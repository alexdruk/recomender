"""Milestone 3, part B, step 6. Text embeddings for every book.

Run:  .venv/bin/python model/embed.py              embed every book that has no vectors yet
      .venv/bin/python model/embed.py --time 20    only time the model on 20 books, save nothing
      .venv/bin/python model/embed.py --chunks 32  set the number of chunks per book by hand

Model: BAAI/bge-m3 (multilingual, good with Russian), run locally on the
Apple Silicon GPU ("mps") in half precision.

For each book: N chunks of about 500 tokens, spread evenly through the text,
skipping the first 2% (front matter). The chunk vectors are saved to
data/emb/<set>/<key>.npy (N rows of 1024 numbers); the book vector is their mean.
Sets:  liked_fb2   - liked books, text from the downloaded FB2 file
       liked_epub  - liked books without an FB2 file, plus the EPUB text of 30
                     liked books for the FB2-vs-EPUB check
       feed, matched, rated - the unlabeled books (rated = new downloads for the rated-matched set)

       extra       - books I marked "read it" on the labelling page

Milestone 3c adds two more kinds of vectors, made in the same run:
  data/emb-open/<set>/<key>.npy   the opening only: the same number of chunks, spread
                                  evenly through the first 25,000 words (about 100 pages)
  data/emb-comments/<key>.npy     reader comments from the catalog: one vector per comment,
                                  the newest 30 comments of the book (each cut like a chunk)

Resumable: a book whose .npy file exists is skipped, so the script can be
stopped and started again at any time.
"""

import argparse
import json
import random
import re
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / CONFIG.get("dataDir", "data")
DB_PATH = DATA / "books.db"
CAT_PATH = DATA / "catalog.db"
INDEX_PATH = DATA / "index.db"
EMB = DATA / "emb"
EMB_OPEN = DATA / "emb-open"
EMB_COMMENTS = DATA / "emb-comments"
SETTINGS = EMB / "settings.json"

MODEL = "BAAI/bge-m3"
TOKENS = 500  # tokens per chunk
CHUNK_CHARS = 1900  # characters cut for one chunk: about 550 tokens of Russian text, then trimmed to 500
SKIP_SHARE = 0.02  # front matter
MIN_CHUNKS, MAX_CHUNKS = 16, 48
BUDGET_SECONDS = 2.5 * 3600  # aim under 3 hours, with some room to spare
EPUB_CHECK_BOOKS = 30
SEED = 20261112
BATCH = 16
OPENING_WORDS = 25000  # the same number as in src/stylometry.js
MAX_COMMENTS = 30  # the newest comments of a book that are embedded
WORD = re.compile(r"[^\W_]+(?:[-'’][^\W_]+)*")


def text_dirs():
    return {
        "liked_fb2": DATA / "text-fb2",
        "liked_epub": DATA / "text",
        "feed": DATA / "feed" / "text",
        "matched": DATA / "matched" / "text",
        "rated": DATA / "rated" / "text",
        "extra": DATA / "extra" / "text",
    }


def liked_keys(db):
    return [r[0] for r in db.execute("SELECT key FROM books WHERE parse_status != 'failed' ORDER BY key")]


def epub_check_keys(keys):
    """The liked books used for the FB2-vs-EPUB check: the same 30 every time
    (a fixed shuffle of the books that have both texts)."""
    dirs = text_dirs()
    both = [k for k in keys if (dirs["liked_fb2"] / f"{k}.txt").exists() and (dirs["liked_epub"] / f"{k}.txt").exists()]
    random.Random(SEED).shuffle(both)
    return sorted(both[:EPUB_CHECK_BOOKS])


def list_jobs():
    """Every (set, key, text file) that should have vectors."""
    db = sqlite3.connect(DB_PATH, timeout=30)
    dirs = text_dirs()
    jobs = []
    liked = liked_keys(db)
    for key in liked:
        fb2 = dirs["liked_fb2"] / f"{key}.txt"
        epub = dirs["liked_epub"] / f"{key}.txt"
        if fb2.exists():
            jobs.append(("liked_fb2", key, fb2))
        elif epub.exists():
            jobs.append(("liked_epub", key, epub))
    for key in epub_check_keys(liked):
        jobs.append(("liked_epub", key, dirs["liked_epub"] / f"{key}.txt"))
    has_view = db.execute("SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = 'unlabeled_books'").fetchone()
    if has_view:
        for key, store in db.execute("SELECT key, store FROM unlabeled_books ORDER BY key"):
            path = dirs[store] / f"{key}.txt"
            if path.exists():
                jobs.append((store, key, path))
    if db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'extra_books'").fetchone():
        for (key,) in db.execute("SELECT key FROM extra_books WHERE parse_status != 'failed' ORDER BY key"):
            path = dirs["extra"] / f"{key}.txt"
            if path.exists():
                jobs.append(("extra", key, path))
    expected = db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'matched_pick'").fetchone()[0]
    picked = db.execute("SELECT COUNT(*) FROM matched_pick").fetchone()[0] if expected else 0
    db.close()
    return jobs, picked


def cut_chunks(text, n):
    """n pieces of text, evenly spread, each starting at the beginning of a paragraph."""
    start = int(len(text) * SKIP_SHARE)
    usable = len(text) - start
    if usable <= CHUNK_CHARS:
        return [text[start:] or text]
    count = max(1, min(n, usable // CHUNK_CHARS))  # a short book gets fewer chunks, never overlapping ones
    step = (usable - CHUNK_CHARS) / max(1, count - 1) if count > 1 else 0
    chunks = []
    for i in range(count):
        pos = start + int(i * step)
        nl = text.find("\n", pos, pos + 600)  # move on to the next paragraph start
        if nl != -1:
            pos = nl + 1
        chunks.append(text[pos:pos + CHUNK_CHARS])
    return chunks


def opening_of(text, words=OPENING_WORDS):
    """Everything up to the end of the paragraph that holds word number 25,000."""
    n = 0
    for m in WORD.finditer(text):
        n += 1
        if n >= words:
            end = text.find("\n", m.end())
            return text if end == -1 else text[:end + 1]
    return text


def comment_jobs():
    """(key, newest comments) for every book of mine that has reader comments and no comment vectors yet.
    Comments left on an older copy of a book count for the book, as on the site."""
    if not INDEX_PATH.exists() or not CAT_PATH.exists():
        return []
    db = sqlite3.connect(DB_PATH, timeout=30)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'book_facts'").fetchone():
        db.close()
        return []
    todo = [(k, c) for k, c in db.execute("SELECT key, cat_id FROM book_facts WHERE comments > 0 AND cat_id IS NOT NULL ORDER BY key")
            if not (EMB_COMMENTS / f"{k}.npy").exists()]
    db.close()
    cat = sqlite3.connect(f"file:{CAT_PATH}?mode=ro", uri=True)
    if not cat.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'reviews'").fetchone():
        cat.close()
        return []
    ix = sqlite3.connect(f"file:{INDEX_PATH}?mode=ro", uri=True)
    jobs = []
    for key, cat_id in todo:
        ids = [cat_id] + [r[0] for r in ix.execute("SELECT id FROM alias WHERE root = ?", (cat_id,))]
        sql = f"SELECT Text FROM reviews WHERE BookId IN ({','.join('?' * len(ids))}) ORDER BY Time DESC"
        texts = [t.strip()[:CHUNK_CHARS] for (t,) in cat.execute(sql, ids) if t and t.strip()][:MAX_COMMENTS]
        if texts:
            jobs.append((key, texts))
    cat.close()
    ix.close()
    return jobs


def save(out, vectors):
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.npy")
    np.save(tmp, vectors.astype(np.float16))
    tmp.replace(out)  # a half-written file never looks finished


def load_model():
    import torch
    from sentence_transformers import SentenceTransformer

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = SentenceTransformer(MODEL, device=device)
    model.max_seq_length = TOKENS
    if device == "mps":
        model.half()
    return model, device


def embed(model, chunks):
    vectors = model.encode(chunks, batch_size=BATCH, normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(vectors, dtype=np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--time", type=int, default=0, help="time the model on this many books and stop")
    ap.add_argument("--chunks", type=int, default=0, help="chunks per book (16 to 48)")
    args = ap.parse_args()

    if not DB_PATH.exists():
        sys.exit("data/books.db not found. Run the Node steps first.")
    jobs, picked = list_jobs()
    EMB.mkdir(parents=True, exist_ok=True)
    settings = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}

    model, device = load_model()
    print(f"Model {MODEL} on {device}. {len(jobs)} books have text right now.", flush=True)

    if args.time or "chunks" not in settings and not args.chunks:
        n_books = args.time or 20
        sample = random.Random(SEED).sample(jobs, min(n_books, len(jobs)))
        embed(model, cut_chunks(sample[0][2].read_text(encoding="utf-8"), 4))  # warm-up, not timed
        t0 = time.time()
        n_chunks = 0
        for _, _, path in sample:
            chunks = cut_chunks(path.read_text(encoding="utf-8"), MAX_CHUNKS)
            embed(model, chunks)
            n_chunks += len(chunks)
        per_chunk = (time.time() - t0) / n_chunks
        # how many books the full run will have: the ones here now, plus matched books still downloading
        have_matched = sum(1 for j in jobs if j[0] == "matched")
        total_books = len(jobs) + max(0, (picked or 1150) - have_matched)
        best = int(BUDGET_SECONDS / (per_chunk * total_books))
        chosen = max(MIN_CHUNKS, min(MAX_CHUNKS, best))
        print(f"Timing: {len(sample)} books, {n_chunks} chunks, {per_chunk * 1000:.0f} ms per chunk.")
        print(f"Expected books in the full run: about {total_books}. "
              f"At {chosen} chunks per book the run takes about {per_chunk * total_books * chosen / 60:.0f} minutes.")
        if args.time:
            return
        settings = {"model": MODEL, "tokens": TOKENS, "chunks": chosen, "seconds_per_chunk": round(per_chunk, 4),
                    "timed_on_books": len(sample), "expected_books": total_books}
        SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
    if args.chunks:
        settings.update({"model": MODEL, "tokens": TOKENS, "chunks": max(MIN_CHUNKS, min(MAX_CHUNKS, args.chunks))})
        SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
    n = settings["chunks"]

    def run(name, todo, total, make_chunks, out_path):
        print(f"{name}: {len(todo)} books to embed now (already done: {total - len(todo)}).", flush=True)
        t0 = time.time()
        for i, job in enumerate(todo, 1):
            save(out_path(job), embed(model, make_chunks(job)))
            if i % 50 == 0 or i == len(todo):
                spent = time.time() - t0
                print(f"  {i} of {len(todo)} books, {spent / 60:.1f} min spent, about {spent / i * (len(todo) - i) / 60:.0f} min left", flush=True)
        if todo:
            print(f"{name} done: {len(todo)} books embedded in {(time.time() - t0) / 60:.1f} minutes.", flush=True)

    print(f"Chunks per book: {n}.")
    todo = [j for j in jobs if not (EMB / j[0] / f"{j[1]}.npy").exists()]
    run("Whole books", todo, len(jobs), lambda j: cut_chunks(j[2].read_text(encoding="utf-8"), n), lambda j: EMB / j[0] / f"{j[1]}.npy")

    # the opening only; a liked book is embedded once, from its FB2 text when there is one
    has_fb2 = {k for s, k, _ in jobs if s == "liked_fb2"}
    first = [j for j in jobs if not (j[0] == "liked_epub" and j[1] in has_fb2)]
    todo = [j for j in first if not (EMB_OPEN / j[0] / f"{j[1]}.npy").exists()]
    run(f"Openings (first {OPENING_WORDS} words)", todo, len(first),
        lambda j: cut_chunks(opening_of(j[2].read_text(encoding="utf-8")), n), lambda j: EMB_OPEN / j[0] / f"{j[1]}.npy")

    cj = comment_jobs()
    done = len(list(EMB_COMMENTS.glob("*.npy"))) if EMB_COMMENTS.exists() else 0
    run("Reader comments", cj, len(cj) + done, lambda j: j[1], lambda j: EMB_COMMENTS / f"{j[0]}.npy")


if __name__ == "__main__":
    main()
