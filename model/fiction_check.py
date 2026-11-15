"""The text check that keeps non-fiction out: a small classifier, fiction against non-fiction.

The genre codes inside a book file can be wrong or vague (a history book that
only carries the code "prose"). So every feed book that passed the genre rule
is also checked by its text: a logistic regression on the embedding of the
whole text (the same vectors the recommender uses).

  fiction examples      books whose genre codes are all known and say fiction: my library,
                        the background catalog books, the feed books that passed
  non-fiction examples  feed books with a non-fiction genre code (they get a whole-text
                        embedding here, only for this check), and the few non-fiction
                        books of my library

The accuracy is measured in an author-grouped test (an author is never on both
sides). It is trained again at every training of the recommender.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import joblib  # noqa: E402
import numpy as np  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.model_selection import StratifiedGroupKFold  # noqa: E402

from baseline import author_groups, jlist, norm_author  # noqa: E402
import embed as emb  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / CONFIG.get("dataDir", "data")
MAP_PATH = DATA / "genre-map.json"
CHECK_PATH = DATA / "model" / "fiction_check.joblib"

MIN_WORDS = 10000
FOLDS, REPEATS, SEED = 5, 4, 20261115
C = 10.0
MAX_FICTION_LOST = 0.01  # the threshold is set so that at most 1% of fiction books are set aside by mistake
MIN_NONFICTION = 20  # with fewer non-fiction examples there is no check
REASON = "not fiction: the text reads like non-fiction"


def get_meta(db, name, default=None):
    r = db.execute("SELECT value FROM meta WHERE name = ?", (name,)).fetchone()
    return json.loads(r[0]) if r and r[0] is not None else default


def set_meta(db, name, value):
    db.execute("INSERT INTO meta (name, value) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value", (name, json.dumps(value)))


def code_kind(genres, gmap):
    """What the genre codes of a book say: 'fiction', 'nonfiction' or 'unclear' (an unknown code, or no fiction code)."""
    kinds = [gmap["groups"].get(gmap["codes"].get(c)) if c in gmap["codes"] else "unknown" for c in jlist(genres)]
    if "nonfiction" in kinds:
        return "nonfiction"
    if "unknown" in kinds or "fiction" not in kinds:
        return "unclear"
    return "fiction"


def vector(set_name, key):
    path = emb.EMB / set_name / f"{key}.npy"
    if not path.exists():
        return None
    v = np.load(path).astype(np.float32).mean(axis=0)
    return v / (np.linalg.norm(v) or 1.0)


def embed_nonfiction(db, gmap):
    """Whole-text embeddings for feed books with a non-fiction code, so the check has non-fiction examples."""
    text_dir = DATA / "feed" / "text"
    todo = [r["key"] for r in db.execute("SELECT key, genres, words FROM feed_books WHERE parse_status != 'failed' AND lang = 'ru'")
            if (r["words"] or 0) >= MIN_WORDS and code_kind(r["genres"], gmap) == "nonfiction"
            and not (emb.EMB / "feed" / f"{r['key']}.npy").exists() and (text_dir / f"{r['key']}.txt").exists()]
    if not todo:
        return 0
    settings = json.loads(emb.SETTINGS.read_text()) if emb.SETTINGS.exists() else {}
    n = settings.get("chunks", emb.MAX_CHUNKS)
    model, device = emb.load_model()
    print(f"Text check: embedding {len(todo)} non-fiction feed books with {emb.MODEL} on {device} ...", flush=True)
    t0 = time.time()
    for key in todo:
        text = (text_dir / f"{key}.txt").read_text(encoding="utf-8")
        emb.save(emb.EMB / "feed" / f"{key}.npy", emb.embed(model, emb.cut_chunks(text, n)))
    print(f"  done in {(time.time() - t0) / 60:.1f} minutes.", flush=True)
    return len(todo)


def examples(db, gmap):
    """Books with a whole-text vector whose genre codes are clear: label 1 = non-fiction, 0 = fiction."""
    out, seen = [], set()

    def add(set_name, rows):
        for r in rows:
            kind = code_kind(r["genres"], gmap)
            if r["key"] in seen or kind == "unclear":
                continue
            v = vector(set_name, r["key"])
            if v is None:
                continue
            seen.add(r["key"])
            names = [n for n in jlist(r["authors"]) if n.strip()]
            out.append({"key": r["key"], "set": set_name, "y": int(kind == "nonfiction"), "vec": v, "title": r["title"] or "",
                        "authors": [f"n:{norm_author(n)}" for n in names] or [f"solo:{set_name}:{r['key']}"]})

    add("liked_fb2", db.execute("""SELECT b.key, b.authors, b.title, p.genres FROM books b
        JOIN parsed p ON p.source = 'liked_fb2' AND p.key = b.key WHERE p.parse_status != 'failed' AND COALESCE(b.is_collection, 0) = 0"""))
    add("feed", db.execute("SELECT key, authors, title, genres FROM feed_books WHERE parse_status != 'failed' AND lang = 'ru'"))
    add("matched", db.execute("SELECT key, authors, title, genres FROM unlabeled_books WHERE store = 'matched'"))
    add("rated", db.execute("SELECT key, authors, title, genres FROM unlabeled_books WHERE store = 'rated'"))
    return out


def fit(X, y):
    return LogisticRegression(C=C, class_weight="balanced", max_iter=3000).fit(X, y)


def train(db):
    """Trains the check, measures it, saves it. Returns the numbers (also stored in meta 'fiction_check')."""
    gmap = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    embed_nonfiction(db, gmap)
    books = examples(db, gmap)
    y = np.array([b["y"] for b in books])
    info = {"trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "fiction": int((y == 0).sum()), "nonfiction": int((y == 1).sum()), "ready": False}
    if info["nonfiction"] < MIN_NONFICTION:
        CHECK_PATH.unlink(missing_ok=True)
        set_meta(db, "fiction_check", info)
        db.commit()
        return info
    X = np.vstack([b["vec"] for b in books])
    groups = author_groups(books)
    oof = np.zeros(len(books))
    for rep in range(REPEATS):
        for tr, te in StratifiedGroupKFold(FOLDS, shuffle=True, random_state=SEED + rep).split(X, y, groups):
            oof[te] += fit(X[tr], y[tr]).predict_proba(X[te])[:, 1] / REPEATS
    # the threshold: the lowest one that sets aside at most 1% of the fiction books (never below 0.5)
    fiction = np.sort(oof[y == 0])
    threshold = max(0.5, float(np.quantile(fiction, 1 - MAX_FICTION_LOST)) + 1e-6)
    said = oof >= threshold
    wrong_fiction = sorted(((float(oof[i]), books[i]) for i in np.where(said & (y == 0))[0]), key=lambda t: -t[0])
    info.update({
        "ready": True, "threshold": threshold,
        "accuracy": float((said == (y == 1)).mean()),
        "nonfiction_caught": float(said[y == 1].mean()), "fiction_kept": float((~said[y == 0]).mean()),
        "caught": int(said[y == 1].sum()), "lost": int(said[y == 0].sum()),
        # fiction-coded books the check calls non-fiction: often the genre code is what is wrong
        "doubtful": [{"key": b["key"], "title": b["title"], "set": b["set"], "p": round(p, 2)} for p, b in wrong_fiction[:15]],
        "held_out": {b["key"]: round(float(oof[i]), 4) for i, b in enumerate(books) if b["set"] == "feed"},
    })
    CHECK_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": fit(X, y), "threshold": threshold, "held_out": info["held_out"]}, CHECK_PATH)
    set_meta(db, "fiction_check", {k: v for k, v in info.items() if k != "held_out"})
    db.commit()
    return info


def describe(info):
    if not info.get("ready"):
        return f"Text check (fiction against non-fiction): not enough non-fiction examples yet ({info['nonfiction']}, needs {MIN_NONFICTION})."
    return (f"Text check (fiction against non-fiction): trained on {info['fiction']} fiction and {info['nonfiction']} non-fiction books. "
            f"Author-grouped test: accuracy {100 * info['accuracy']:.1f}%, it catches {info['caught']} of {info['nonfiction']} non-fiction books "
            f"({100 * info['nonfiction_caught']:.0f}%) and wrongly sets aside {info['lost']} of {info['fiction']} fiction books "
            f"({100 * (1 - info['fiction_kept']):.1f}%).")


def apply(db):
    """Gives every feed book that passed the genre rule its chance of being non-fiction ("nf_prob"), and takes the
    unanswered books above the threshold out of the list. Returns how many were newly set aside."""
    if not CHECK_PATH.exists():
        return 0
    saved = joblib.load(CHECK_PATH)
    rows = db.execute("""SELECT s.key, s.nf_prob FROM feed_scores s WHERE s.passed = 1
        AND s.key NOT IN (SELECT key FROM answers) AND s.key NOT IN (SELECT key FROM book_state)""").fetchall()
    flagged = 0
    for r in rows:
        v = vector("feed", r["key"])
        if v is None:
            continue
        # a book that was a training example gets the value from the test side
        p = saved["held_out"].get(r["key"])
        if p is None:
            p = float(saved["model"].predict_proba(v[None, :])[0, 1])
        if p >= saved["threshold"]:
            db.execute("UPDATE feed_scores SET nf_prob = ?, passed = 0, reason = ? WHERE key = ?", (p, REASON, r["key"]))
            flagged += 1
        else:
            db.execute("UPDATE feed_scores SET nf_prob = ? WHERE key = ?", (p, r["key"]))
    db.commit()
    return flagged
