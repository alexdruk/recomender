"""The final product: the model behind the recommendations page.

Run:  .venv/bin/python model/recommend.py train    train, save the model, rescore every waiting feed book
      .venv/bin/python model/recommend.py score    fingerprint and score feed books that have no score yet

The model is the model "all" of model/fingerprints.py (logistic regression on
genres + length + translated + stylometry + embeddings), with one more block:
embeddings of the opening (the first 25,000 words). Everything comes from the
book file. Ratings, votes, comments and the year are not used anywhere.

  positives        my liked books, plus every Like from the page (weight 1)
  background       the old matched set, plus every feed book that has a fingerprint
  extra negatives  my earlier "would never read" answers and every Dislike on a card I never read (weight 3),
                   plus every Dislike given after reading the book (weight 5)
  left out         books that wait in "To send" or "Reading" without an answer: I picked them,
                   so they are not background, and I have not judged them yet

Genres are the codes written inside the FB2 file, for every book, mapped to
plain groups with data/genre-map.json.

The quality numbers come from author-grouped cross-validation: all books of an
author are either in the training part or in the test part, never in both.
Feed books that are part of the training data get the score they had while
they sat on the test side, so a book is never scored by a model that has seen it.
"""

import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")  # the embedding model is already on this computer

import joblib  # noqa: E402
import numpy as np  # noqa: E402
from joblib import Parallel, delayed  # noqa: E402
from sklearn.decomposition import PCA  # noqa: E402
from sklearn.model_selection import StratifiedGroupKFold  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from baseline import author_groups, jlist, norm_author  # noqa: E402
from fingerprints import C_GRID, PCA_DIMS, fit_best, rank_metrics  # noqa: E402,F401
import embed as emb  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / CONFIG.get("dataDir", "data")
DB_PATH = DATA / "books.db"
MAP_PATH = DATA / "genre-map.json"
MODEL_PATH = DATA / "model" / "recommender.joblib"

FOLDS = 5
REPEATS = 4
SEED = 20261113
NEGATIVE_WEIGHT = 3.0  # "would never read" and a Dislike on a card I never read
READ_NEGATIVE_WEIGHT = 5.0  # a Dislike given after reading the book
CLOSEST = 3
VEC_SET = {"liked": "liked_fb2", "matched": "matched", "feed": "feed", "rated": "rated"}


# ---------------------------------------------------------------- loading


def connect():
    if not DB_PATH.exists():
        sys.exit("data/books.db not found. Run the Node steps first.")
    db = sqlite3.connect(DB_PATH, timeout=60)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS answers (
        key TEXT PRIMARY KEY, answer TEXT NOT NULL, time TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'card')""")
    if not any(r["name"] == "kind" for r in db.execute("PRAGMA table_info(answers)")):
        db.execute("ALTER TABLE answers ADD COLUMN kind TEXT NOT NULL DEFAULT 'card'")
    db.execute("""CREATE TABLE IF NOT EXISTS book_state (key TEXT PRIMARY KEY, state TEXT NOT NULL, time TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS feed_scores (
        key TEXT PRIMARY KEY, added_at TEXT, passed INTEGER, reason TEXT, series_note TEXT,
        score REAL, percentile REAL, closest TEXT, scored_at TEXT)""")
    return db


def mean_vector(folder, set_name, key):
    path = DATA / folder / set_name / f"{key}.npy"
    if not path.exists():
        return None
    v = np.load(path).astype(np.float32).mean(axis=0)
    return v / (np.linalg.norm(v) or 1.0)


class Loader:
    """Turns database rows into books with features. Genres come from the FB2 file only."""

    def __init__(self, db):
        gmap = json.loads(MAP_PATH.read_text(encoding="utf-8"))
        self.codes = gmap["codes"]
        self.group_names = list(gmap["groups"])
        self.styl = {(r["kind"], r["key"]): json.loads(r["features"])
                     for r in db.execute("SELECT kind, key, features FROM stylometry WHERE features IS NOT NULL")}
        meta = db.execute("SELECT value FROM meta WHERE name = 'stylometry_features'").fetchone()
        self.styl_names = [f["name"] for f in json.loads(meta[0])]

    def book(self, kind, r):
        """kind: liked / matched / feed / rated. r: key, authors, title, genres, words, is_translation."""
        key = r["key"]
        vec = mean_vector("emb", VEC_SET[kind], key)
        opening = mean_vector("emb-open", VEC_SET[kind], key)
        if vec is None or opening is None:
            return None
        names = [n for n in jlist(r["authors"]) if n.strip()]
        groups = []
        for code in jlist(r["genres"]):
            g = self.codes.get(code)
            if g and g not in groups:
                groups.append(g)
        return {
            "key": key, "kind": kind, "title": r["title"] or "", "author_names": ", ".join(names),
            "authors": [f"n:{norm_author(n)}" for n in names] or [f"solo:{kind}:{key}"],
            "groups": groups, "words": r["words"] or 0, "translated": int(r["is_translation"] or 0),
            "vec": vec, "open": opening, "styl": self.styl.get(("liked" if kind == "liked" else "unlabeled", key)),
            "y": 0, "w": 1.0,
        }

    def raw(self, books):
        """The plain feature blocks, before any scaling."""
        col = {g: i for i, g in enumerate(self.group_names)}
        G = np.zeros((len(books), len(col)))
        for i, b in enumerate(books):
            for g in b["groups"]:
                G[i, col[g]] = 1.0
        S = np.array([[(b["styl"] or {}).get(n) if (b["styl"] or {}).get(n) is not None else np.nan for n in self.styl_names]
                      for b in books], dtype=float).reshape(len(books), len(self.styl_names))
        return {
            "G": G,
            "lw": np.array([math.log10(max(b["words"], 100)) for b in books]),
            "tr": np.array([float(b["translated"]) for b in books]),
            "S": S,
            "E": np.vstack([b["vec"] for b in books]),
            "O": np.vstack([b["open"] for b in books]),
        }


COLS = "key, authors, title, genres, words, is_translation"


def training_books(db, loader):
    """Every book the model learns from, with its class (y) and weight (w)."""
    never = {r["key"] for r in db.execute("SELECT key FROM labels WHERE label = 'never' AND status IS NULL")}
    want = {r["key"] for r in db.execute("SELECT key FROM labels WHERE label = 'want' AND status IS NULL")}
    answers = {r["key"]: r["answer"] for r in db.execute("SELECT key, answer FROM answers")}
    after_reading = {r["key"] for r in db.execute("SELECT key FROM answers WHERE kind = 'read'")}
    picked = {r["key"] for r in db.execute("SELECT key FROM book_state")}  # waiting in "To send" or "Reading"
    books, seen = [], set()

    def add(kind, rows, only=None):
        for r in rows:
            key = r["key"]
            if key in seen or (only is not None and key not in only):
                continue
            if key in want and key not in answers:
                continue  # I said I want to read it: not a negative, not yet a positive
            if key in picked and key not in answers:
                continue  # picked for reading on the page, no verdict yet
            b = loader.book(kind, r)
            if b is None:
                continue
            seen.add(key)
            if kind == "liked" or answers.get(key) == "like":
                b["y"] = 1
            elif answers.get(key) == "dislike" and key in after_reading:
                b["w"] = READ_NEGATIVE_WEIGHT
            elif key in never or answers.get(key) == "dislike":
                b["w"] = NEGATIVE_WEIGHT
            b["answer"] = answers.get(key)
            b["read"] = key in after_reading and key in answers
            books.append(b)

    # liked books: genres, length and "translated" from the downloaded FB2 file, like every other book
    add("liked", db.execute("""SELECT b.key, b.authors, b.title, p.genres, p.words, p.is_translation
        FROM books b JOIN parsed p ON p.source = 'liked_fb2' AND p.key = b.key
        WHERE b.parse_status != 'failed' AND p.parse_status != 'failed'
          AND COALESCE(b.is_collection, 0) = 0 AND b.is_fiction = 1"""))
    add("feed", db.execute(f"SELECT {COLS} FROM feed_books WHERE parse_status != 'failed'"))
    add("matched", db.execute(f"SELECT {COLS} FROM unlabeled_books WHERE store = 'matched'"))
    # from the rated-matched set only the books I answered "would never read" are used
    add("rated", db.execute(f"SELECT {COLS} FROM unlabeled_books WHERE store = 'rated'"), only=never)
    return books


# ---------------------------------------------------------------- the model


class Prep:
    """Scales and PCA directions, learned from the training rows only."""

    def fit(self, raw, train):
        self.lw_mean, self.lw_sd = float(raw["lw"][train].mean()), float(raw["lw"][train].std() or 1.0)
        med = np.nanmedian(raw["S"][train], axis=0)
        self.s_med = np.where(np.isnan(med), 0.0, med)
        S = self._fill(raw["S"])[train]
        self.s_mean, self.s_sd = S.mean(axis=0), np.where(S.std(axis=0) > 0, S.std(axis=0), 1.0)
        dims = min(PCA_DIMS, len(train) - 1)
        self.pca_e = PCA(n_components=dims, random_state=SEED).fit(raw["E"][train])
        self.scale_e = float(self.pca_e.transform(raw["E"][train]).std(axis=0).mean())
        self.pca_o = PCA(n_components=dims, random_state=SEED).fit(raw["O"][train])
        self.scale_o = float(self.pca_o.transform(raw["O"][train]).std(axis=0).mean())
        return self

    def _fill(self, S):
        return np.where(np.isnan(S), self.s_med, S)

    def transform(self, raw):
        words = (raw["lw"] - self.lw_mean) / self.lw_sd
        S = np.clip((self._fill(raw["S"]) - self.s_mean) / self.s_sd, -5, 5)
        E = self.pca_e.transform(raw["E"]) / self.scale_e
        Op = self.pca_o.transform(raw["O"]) / self.scale_o
        return np.hstack([raw["G"], words[:, None], raw["tr"][:, None], S, E, Op])


def take(raw, idx):
    return {k: v[idx] for k, v in raw.items()}


def run_fold(raw, y, w, groups, train, test, seed):
    prep = Prep().fit(raw, train)
    X = prep.transform(raw)
    model, _ = fit_best(X[train], y[train], groups[train], seed, w[train])
    dec = model.decision_function(X[test])
    return test, rank_metrics(dec[y[test] == 1], dec[y[test] == 0]), model.predict_proba(X[test])[:, 1]


def train(db):
    t0 = time.time()
    loader = Loader(db)
    books = training_books(db, loader)
    y = np.array([b["y"] for b in books])
    w = np.array([b["w"] for b in books])
    groups = author_groups(books)
    raw = loader.raw(books)
    n = len(books)

    jobs = []
    for rep in range(REPEATS):
        for f, (tr, te) in enumerate(StratifiedGroupKFold(FOLDS, shuffle=True, random_state=SEED + rep).split(np.zeros(n), y, groups)):
            jobs.append((rep, tr, te, SEED + 100 * rep + f))
    done = Parallel(n_jobs=-1)(delayed(run_fold)(raw, y, w, groups, tr, te, seed) for _, tr, te, seed in jobs)
    oof = np.zeros(n)
    for test, _, proba in done:
        oof[test] += proba / REPEATS
    metrics = [m for _, m, _ in done if m]
    auc = np.array([m["auc"] for m in metrics])
    top = np.array([m["top"] for m in metrics])

    everything = np.arange(n)
    prep = Prep().fit(raw, everything)
    model, C = fit_best(prep.transform(raw), y, groups, SEED, w)

    kinds = [b["kind"] for b in books]
    info = {
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "auc": float(auc.mean()), "auc_sd": float(auc.std()),
        "top5": float(top.mean()), "top5_sd": float(top.std()),
        "positives": int(y.sum()), "liked_library": kinds.count("liked"),
        "page_likes": sum(1 for b in books if b.get("answer") == "like"),
        "page_dislikes": sum(1 for b in books if b.get("answer") == "dislike"),
        "read_likes": sum(1 for b in books if b.get("answer") == "like" and b.get("read")),
        "read_dislikes": sum(1 for b in books if b.get("answer") == "dislike" and b.get("read")),
        "never_answers": int(((w > 1) & (y == 0)).sum()) - sum(1 for b in books if b.get("answer") == "dislike"),
        "background": int((y == 0).sum()), "background_matched": kinds.count("matched"),
        "background_feed": sum(1 for b in books if b["kind"] == "feed" and b["y"] == 0),
        "answers_used": db.execute("SELECT COUNT(*) FROM answers").fetchone()[0],
        "C": C, "folds": FOLDS, "repeats": REPEATS, "seconds": round(time.time() - t0, 1),
    }
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MODEL_PATH.with_suffix(".tmp")
    # feed books that were part of the training data keep the score they got on the test side
    held_out = {b["key"]: float(oof[i]) for i, b in enumerate(books) if b["kind"] == "feed"}
    joblib.dump({"prep": prep, "model": model, "info": info, "held_out": held_out}, tmp)
    tmp.replace(MODEL_PATH)
    db.execute("INSERT INTO meta (name, value) VALUES ('model_info', ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value", (json.dumps(info),))
    db.commit()

    scored = score(db, loader=loader, everything=True)

    print(f"Trained on {info['positives']} liked books ({info['liked_library']} from my library, {info['page_likes']} Likes from the page) "
          f"against {info['background']} background books ({info['background_matched']} matched, {info['background_feed']} feed; "
          f"{info['never_answers']} \"would never read\" and {info['page_dislikes'] - info['read_dislikes']} Dislikes on cards count 3 times, "
          f"{info['read_dislikes']} Dislikes after reading count 5 times).")
    print(f"Author-grouped ROC AUC: {info['auc']:.3f} (± {info['auc_sd']:.3f} over {len(metrics)} test folds).")
    print(f"Share of liked books in the top 5%: {100 * info['top5']:.1f}% (± {100 * info['top5_sd']:.1f}).")
    print(f"Model saved to {MODEL_PATH.relative_to(ROOT)}. {scored} waiting feed books rescored. Took {time.time() - t0:.0f} s.")


# ---------------------------------------------------------------- scoring feed books


def make_vectors(db, rows):
    """Embeds (whole text and opening) the feed books that have no vectors yet."""
    text_dir = DATA / "feed" / "text"
    todo = [r["key"] for r in rows
            if not ((emb.EMB / "feed" / f"{r['key']}.npy").exists() and (emb.EMB_OPEN / "feed" / f"{r['key']}.npy").exists())
            and (text_dir / f"{r['key']}.txt").exists()]
    if not todo:
        return 0
    settings = json.loads(emb.SETTINGS.read_text()) if emb.SETTINGS.exists() else {}
    n = settings.get("chunks", emb.MAX_CHUNKS)
    model, device = emb.load_model()
    print(f"Embedding {len(todo)} new books with {emb.MODEL} on {device} ...", flush=True)
    t0 = time.time()
    for key in todo:
        text = (text_dir / f"{key}.txt").read_text(encoding="utf-8")
        emb.save(emb.EMB / "feed" / f"{key}.npy", emb.embed(model, emb.cut_chunks(text, n)))
        emb.save(emb.EMB_OPEN / "feed" / f"{key}.npy", emb.embed(model, emb.cut_chunks(emb.opening_of(text), n)))
    print(f"  done in {(time.time() - t0) / 60:.1f} minutes.", flush=True)
    return len(todo)


def score(db, loader=None, everything=False):
    """Scores feed books that passed the filters. everything = False: only books without a score."""
    if not MODEL_PATH.exists():
        sys.exit('No trained model yet. Run "npm run train" first.')
    saved = joblib.load(MODEL_PATH)
    where = "" if everything else "AND s.score IS NULL"
    sql = f"SELECT f.key, f.authors, f.title, f.genres, f.words, f.is_translation FROM feed_scores s JOIN feed_books f ON f.key = s.key WHERE s.passed = 1 {where}"
    rows = db.execute(sql).fetchall()
    made = make_vectors(db, rows)
    if made or loader is None:
        loader = Loader(db)  # reads the stylometry of the new books
    books = [b for b in (loader.book("feed", r) for r in rows) if b is not None]
    if books:
        scores = saved["model"].predict_proba(saved["prep"].transform(loader.raw(books)))[:, 1]
        held_out = saved.get("held_out") or {}
        if held_out:
            scores = [held_out.get(b["key"], s) for b, s in zip(books, scores)]
        # the liked books closest by text: my library plus Likes from the page
        liked = [b for b in (loader.book("liked", r) for r in db.execute(
            "SELECT b.key, b.authors, b.title, p.genres, p.words, p.is_translation FROM books b "
            "JOIN parsed p ON p.source = 'liked_fb2' AND p.key = b.key WHERE b.parse_status != 'failed' "
            "AND COALESCE(b.is_collection, 0) = 0 AND b.is_fiction = 1")) if b]
        liked += [b for b in (loader.book("feed", r) for r in db.execute(
            f"SELECT {COLS} FROM feed_books WHERE key IN (SELECT key FROM answers WHERE answer = 'like')")) if b]
        L = np.vstack([b["vec"] for b in liked])
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for b, s in zip(books, scores):
            sims = L @ b["vec"]
            near = [i for i in np.argsort(-sims) if liked[i]["key"] != b["key"]][:CLOSEST]
            closest = [{"authors": liked[i]["author_names"], "title": liked[i]["title"], "sim": round(float(sims[i]), 3)} for i in near]
            db.execute("UPDATE feed_scores SET score = ?, closest = ?, scored_at = ? WHERE key = ?",
                       (float(s), json.dumps(closest, ensure_ascii=False), now, b["key"]))
    # the percentile of every book among all feed books scored so far: 0 = the best, 100 = the worst
    allrows = db.execute("SELECT key, score FROM feed_scores WHERE score IS NOT NULL ORDER BY score DESC").fetchall()
    for rank, r in enumerate(allrows, 1):
        db.execute("UPDATE feed_scores SET percentile = ? WHERE key = ?", (100.0 * rank / len(allrows), r["key"]))
    db.commit()
    return len(books)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in ("train", "score"):
        sys.exit("Use: recommend.py train   or   recommend.py score")
    db = connect()
    if cmd == "train":
        train(db)
    else:
        n = score(db)
        total = db.execute("SELECT COUNT(*) FROM feed_scores WHERE score IS NOT NULL").fetchone()[0]
        print(f"Scored {n} new feed books. {total} feed books have a score now.")
    db.close()


if __name__ == "__main__":
    main()
