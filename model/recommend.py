"""The final product: the model behind the recommendations page.

Run:  .venv/bin/python model/recommend.py train    train, save the model, rescore every feed book, rebuild reports/model.html
      .venv/bin/python model/recommend.py score    score new feed books and rescore the unanswered ones of the last 60 days

The model predicts MY SCORE for a book, from 1 (bad) to 5 (excellent).

  what it learns from (the target)
    my kept library books        count as 5, weight 1
    books I scored on the page   count as their score; weight 5 when the score was given
                                 after reading, weight 2 when it was given from the card only
    "would never read" answers   (the old labelling page) count as 1, weight 2
    background books             count as 2, with the low weight 0.3: random catalog books,
                                 catalog books with ratings like my library books, and feed
                                 books that I did not answer
    left out                     books that wait in "To send" or "Reading" without a score

  the factors (six groups)
    text     embeddings of the whole text and of the opening (the first 25,000 words)
    style    stylometry numbers
    genre    genre groups from the codes inside the FB2 file, length, translated or not
    ratings  the book's own reader rating, shrunk towards the catalog average, and the
             number of votes (log)
    record   the track record of the author and of the series: the shrunk rating over
             all EARLIER books, and how many of them have marks
    reading  what the reading model (model/reader.py, a local language model) says about
             the opening: 13 qualities rated from 1 to 5 and the narrative voice

Two models are kept. The CHEAP model uses the first five groups and scores every
book. The FULL model also uses the reading group; it gives the final score of a
book that the reading model has read. A book it has not read keeps the cheap
score. Most background books were never read by the reading model; in training
each of them borrows the ratings of a random background book that was read, so
"was read" can never be a hint by itself. The full model is compared with the
cheap one on the books that were read, in the same author-grouped test, next to
a control where the ratings are shuffled between books (pure noise).

A new book often has no votes yet, and that must not count against it. So a
book without votes sits exactly at the catalog average, and during training
the book's own rating is hidden (set to the average, votes to 0) for a random
half of the books: the model learns to score both rated and unrated books.

Two kinds of model are tried, ridge regression and gradient boosting. The one
with the better ranking in an author-grouped test is kept: all books of an
author are either in the training part or in the test part, never in both.
A feed book that is part of the training data is always scored by the test
models that have not seen it.
"""

import json
import math
import os
import sqlite3
import sys
import time
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")  # the embedding model is already on this computer
os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(os.cpu_count() or 4))  # no "sysctl" lookup when started by launchd

import joblib  # noqa: E402
import numpy as np  # noqa: E402
from joblib import Parallel, delayed  # noqa: E402
from scipy.stats import spearmanr  # noqa: E402
from sklearn.decomposition import PCA  # noqa: E402
from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: E402
from sklearn.linear_model import Ridge  # noqa: E402
from sklearn.model_selection import StratifiedGroupKFold  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from baseline import author_groups, jlist, norm_author  # noqa: E402
from fingerprints import PCA_DIMS, rank_metrics  # noqa: E402
import embed as emb  # noqa: E402
import reader  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / CONFIG.get("dataDir", "data")
DB_PATH = DATA / "books.db"
MAP_PATH = DATA / "genre-map.json"
MODEL_PATH = DATA / "model" / "recommender.joblib"
REPORT_DATA = DATA / "model" / "report.json"

FOLDS = 5
REPEATS = 4
SEED = 20261115
CLOSEST = 3
VEC_SET = {"liked": "liked_fb2", "matched": "matched", "feed": "feed", "rated": "rated"}

# the target and the weights
T_LIBRARY, T_BACKGROUND, T_NEVER = 5.0, 2.0, 1.0
W_LIBRARY, W_BACKGROUND, W_READ, W_CARD, W_NEVER = 1.0, 0.3, 5.0, 2.0, 2.0
PRIOR_VOTES = 5.0  # shrunk rating = (sum of marks + average x 5) / (votes + 5)
FALLBACK_AVERAGE = 3.4
MIN_SCORES = 30  # the agreement with my scores is reported from this many scores on
RESCORE_DAYS = int(CONFIG.get("rescoreDays", 60))

MIN_READ = 60  # the full model is trained from this many books read by the reading model
READ_NAMES = ([f"reading: {reader.Q_NAMES[q]}" for q in reader.Q_IDS] + ["reading: first-person narration"]
              + [f"reading: {reader.TONE_NAMES[t]} tone" for t in reader.TONES])
N_QUALITIES = len(reader.Q_IDS)

KINDS = {"ridge": "ridge regression", "gbm": "gradient boosting"}
ALPHAS = (3.0, 30.0, 300.0, 3000.0)  # strength of the ridge "keep it simple" penalty, chosen inside each training part
GROUPS = {  # the five groups of factors and the blocks of columns each one is made of
    "text": ("E", "O"),
    "style": ("S",),
    "genre": ("G", "lw", "tr"),
    "ratings": ("R",),
    "record": ("A",),
    "reading": ("L",),
}
GROUP_NAMES = {"text": "text", "style": "style", "genre": "genre and length", "ratings": "reader ratings", "record": "author and series record",
               "reading": "reading model"}
ALL_BLOCKS = tuple(b for g in GROUPS.values() for b in g)
CHEAP_BLOCKS = tuple(b for b in ALL_BLOCKS if b != "L")
# "cheap": everything but the reading model; "all": with it; "noise": the cheap factors plus
# the ratings of the reading model shuffled between books (block "N"), the control for chance
BLOCK_SETS = {**GROUPS, "file": GROUPS["text"] + GROUPS["style"] + GROUPS["genre"], "cheap": CHEAP_BLOCKS, "all": ALL_BLOCKS,
              "noise": CHEAP_BLOCKS + ("N",)}


# ---------------------------------------------------------------- loading


def connect():
    if not DB_PATH.exists():
        sys.exit("data/books.db not found. Run the Node steps first.")
    db = sqlite3.connect(DB_PATH, timeout=60)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS answers (
        key TEXT PRIMARY KEY, answer TEXT NOT NULL, time TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'card',
        score INTEGER, rescored INTEGER NOT NULL DEFAULT 1)""")
    cols = {r["name"] for r in db.execute("PRAGMA table_info(answers)")}
    if "score" not in cols:  # the Node side does the same change; whoever runs first makes it
        if "kind" not in cols:
            db.execute("ALTER TABLE answers ADD COLUMN kind TEXT NOT NULL DEFAULT 'card'")
        db.execute("ALTER TABLE answers ADD COLUMN score INTEGER")
        db.execute("ALTER TABLE answers ADD COLUMN rescored INTEGER NOT NULL DEFAULT 1")
        db.execute("UPDATE answers SET score = CASE answer WHEN 'like' THEN 4 ELSE 2 END, rescored = 0 WHERE score IS NULL")
    db.execute("""CREATE TABLE IF NOT EXISTS book_state (key TEXT PRIMARY KEY, state TEXT NOT NULL, time TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS feed_scores (
        key TEXT PRIMARY KEY, added_at TEXT, passed INTEGER, reason TEXT, series_note TEXT,
        score REAL, percentile REAL, closest TEXT, scored_at TEXT)""")
    have = {r["name"] for r in db.execute("PRAGMA table_info(feed_scores)")}
    for name, kind in (("votes", "INTEGER"), ("note", "TEXT"), ("nf_prob", "REAL"), ("cheap_score", "REAL"), ("reasons", "TEXT")):
        if name not in have:
            db.execute(f"ALTER TABLE feed_scores ADD COLUMN {name} {kind}")
    db.execute("""CREATE TABLE IF NOT EXISTS rating_facts (
        key TEXT PRIMARY KEY, votes INTEGER, sum INTEGER,
        a_known INTEGER, a_books INTEGER, a_votes INTEGER, a_sum INTEGER,
        s_known INTEGER, s_books INTEGER, s_votes INTEGER, s_sum INTEGER,
        how TEXT, updated_at TEXT)""")
    reader.ensure_tables(db)
    db.commit()
    return db


def get_meta(db, name, default=None):
    r = db.execute("SELECT value FROM meta WHERE name = ?", (name,)).fetchone()
    return json.loads(r[0]) if r and r[0] is not None else default


def set_meta(db, name, value):
    db.execute("INSERT INTO meta (name, value) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value", (name, json.dumps(value)))


def mean_vector(folder, set_name, key):
    path = DATA / folder / set_name / f"{key}.npy"
    if not path.exists():
        return None
    v = np.load(path).astype(np.float32).mean(axis=0)
    return v / (np.linalg.norm(v) or 1.0)


FACT_COLS = ("votes", "sum", "a_votes", "a_sum", "a_books", "s_votes", "s_sum", "s_books")


class Loader:
    """Turns database rows into books with features. Genres come from the FB2 file only."""

    def __init__(self, db):
        gmap = json.loads(MAP_PATH.read_text(encoding="utf-8"))
        self.codes = gmap["codes"]
        self.group_names = list(gmap["groups"])
        self.styl = {(r["kind"], r["key"]): json.loads(r["features"])
                     for r in db.execute("SELECT kind, key, features FROM stylometry WHERE features IS NOT NULL")}
        meta = get_meta(db, "stylometry_features", [])
        self.styl_names = [f["name"] for f in meta]
        self.styl_what = {f["name"]: f.get("what", f["name"]) for f in meta}
        self.facts = {r["key"]: [float(r[c] or 0) for c in FACT_COLS] for r in db.execute("SELECT * FROM rating_facts")}
        self.prior = float((get_meta(db, "ratings_info") or {}).get("average") or FALLBACK_AVERAGE)
        # what the reading model said: 13 ratings, the share of first-person narration, the share of each tone
        self.read = {}
        for r in db.execute("SELECT key, ratings, person_first, tones FROM readings WHERE model = ? AND version = ?", (reader.MODEL, reader.VERSION)):
            ratings, tones = json.loads(r["ratings"]), json.loads(r["tones"])
            self.read[r["key"]] = [ratings[q] for q in reader.Q_IDS] + [r["person_first"]] + [tones.get(t, 0.0) for t in reader.TONES]
        # read because the cheap model already liked them: not a fair sample of background books
        self.daily = {r["key"] for r in db.execute("SELECT key FROM readings WHERE kind = 'daily'")}

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
            "facts": self.facts.get(key, [0.0] * len(FACT_COLS)),
            "L": self.read.get(key),
            "y": T_BACKGROUND, "w": W_BACKGROUND, "role": "background",
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
        F = np.array([b["facts"] for b in books], dtype=float).reshape(len(books), len(FACT_COLS))
        L = np.array([b["L"] if b["L"] is not None else [np.nan] * len(READ_NAMES) for b in books], dtype=float).reshape(len(books), len(READ_NAMES))
        return {
            "L": L,
            "N": L,  # train() replaces it with the shuffled copy
            "kh": np.array([zlib.crc32(f"{SEED}:read:{b['key']}".encode()) for b in books], dtype=np.int64),
            "bg": np.array([b["role"] == "background" and b["key"] not in self.daily for b in books], dtype=bool),
            "G": G,
            "lw": np.array([math.log10(max(b["words"], 100)) for b in books]),
            "tr": np.array([float(b["translated"]) for b in books]),
            "S": S,
            "E": np.vstack([b["vec"] for b in books]),
            "O": np.vstack([b["open"] for b in books]),
            "F": F,
        }

    def names(self, dims):
        """The name of every column, block by block."""
        return {
            "G": [f"genre: {g}" for g in self.group_names],
            "lw": ["length of the book"],
            "tr": ["translated book"],
            "S": [f"style: {self.styl_what[n]}" for n in self.styl_names],
            "E": [f"text of the whole book, direction {i + 1}" for i in range(dims)],
            "O": [f"text of the opening, direction {i + 1}" for i in range(dims)],
            "R": ["reader rating of the book (shrunk)", "number of votes for the book"],
            "A": ["author record: rating of the earlier books (shrunk)", "author record: number of earlier rated books",
                  "series record: rating of the earlier books (shrunk)", "series record: number of earlier rated books"],
            "L": list(READ_NAMES),
        }


COLS = "key, authors, title, genres, words, is_translation"
LIKED_SQL = """SELECT b.key, b.authors, b.title, p.genres, p.words, p.is_translation
    FROM books b JOIN parsed p ON p.source = 'liked_fb2' AND p.key = b.key
    WHERE b.parse_status != 'failed' AND p.parse_status != 'failed'
      AND COALESCE(b.is_collection, 0) = 0 AND b.is_fiction = 1"""


def training_books(db, loader):
    """Every book the model learns from, with its target (y), weight (w) and role."""
    never = {r["key"] for r in db.execute("SELECT key FROM labels WHERE label = 'never' AND status IS NULL")}
    want = {r["key"] for r in db.execute("SELECT key FROM labels WHERE label = 'want' AND status IS NULL")}
    scores = {r["key"]: (int(r["score"]), r["kind"]) for r in db.execute("SELECT key, score, kind FROM answers WHERE score BETWEEN 1 AND 5")}
    picked = {r["key"] for r in db.execute("SELECT key FROM book_state")}  # waiting in "To send" or "Reading"
    # feed books that the genre codes or the text check call non-fiction are not background
    not_fiction = {r["key"] for r in db.execute("SELECT key FROM feed_scores WHERE passed = 0 AND reason LIKE 'not fiction%'")}
    books, seen = [], set()

    def add(kind, rows, only=None):
        for r in rows:
            key = r["key"]
            if key in seen or (only is not None and key not in only):
                continue
            if key not in scores and (key in want or key in picked or key in not_fiction):
                continue  # no verdict yet (I want to read it, or it waits), or it is not fiction
            b = loader.book(kind, r)
            if b is None:
                continue
            seen.add(key)
            if key in scores:
                s, how = scores[key]
                b.update(y=float(s), w=W_READ if how == "read" else W_CARD, role="scored", score=s, read=how == "read")
            elif kind == "liked":
                b.update(y=T_LIBRARY, w=W_LIBRARY, role="library")
            elif key in never:
                b.update(y=T_NEVER, w=W_NEVER, role="never")
            books.append(b)

    # liked books: genres, length and "translated" from the downloaded FB2 file, like every other book
    add("liked", db.execute(LIKED_SQL))
    add("feed", db.execute(f"SELECT {COLS} FROM feed_books WHERE parse_status != 'failed'"))
    add("matched", db.execute(f"SELECT {COLS} FROM unlabeled_books WHERE store = 'matched'"))
    # catalog books chosen to have ratings like my library books: without them the model
    # would only learn "many good votes = a book from my library"
    add("rated", db.execute(f"SELECT {COLS} FROM unlabeled_books WHERE store = 'rated'"))
    return books


def hide_half(books):
    """True for the random half of the books whose own rating is hidden in training. The same books every time."""
    return np.array([zlib.crc32(f"{SEED}:{b['key']}".encode()) % 2 == 0 for b in books], dtype=bool)


# ---------------------------------------------------------------- the model


def shrunk(total, votes, prior):
    return (total + prior * PRIOR_VOTES) / (votes + PRIOR_VOTES)


def rating_columns(F, prior, hide=None):
    """F: votes, sum, a_votes, a_sum, a_books, s_votes, s_sum, s_books -> the six rating factors."""
    votes, total = F[:, 0].copy(), F[:, 1].copy()
    if hide is not None:
        votes[hide], total[hide] = 0.0, 0.0
    R = np.column_stack([shrunk(total, votes, prior), np.log1p(votes)])
    A = np.column_stack([shrunk(F[:, 3], F[:, 2], prior), np.log1p(F[:, 4]), shrunk(F[:, 6], F[:, 5], prior), np.log1p(F[:, 7])])
    return R, A


class Prep:
    """Scales and PCA directions, learned from the training rows only."""

    def fit(self, raw, train, prior, hide=None):
        self.prior = prior
        self.lw_mean, self.lw_sd = float(raw["lw"][train].mean()), float(raw["lw"][train].std() or 1.0)
        med = np.nanmedian(raw["S"][train], axis=0)
        self.s_med = np.where(np.isnan(med), 0.0, med)
        S = self._fill(raw["S"])[train]
        self.s_mean, self.s_sd = S.mean(axis=0), np.where(S.std(axis=0) > 0, S.std(axis=0), 1.0)
        self.dims = min(PCA_DIMS, len(train) - 1)
        self.pca_e = PCA(n_components=self.dims, random_state=SEED).fit(raw["E"][train])
        self.scale_e = float(self.pca_e.transform(raw["E"][train]).std(axis=0).mean())
        self.pca_o = PCA(n_components=self.dims, random_state=SEED).fit(raw["O"][train])
        self.scale_o = float(self.pca_o.transform(raw["O"][train]).std(axis=0).mean())
        R, A = rating_columns(raw["F"][train], prior, None if hide is None else hide[train])
        self.r_mean, self.r_sd = R.mean(axis=0), np.where(R.std(axis=0) > 0, R.std(axis=0), 1.0)
        self.a_mean, self.a_sd = A.mean(axis=0), np.where(A.std(axis=0) > 0, A.std(axis=0), 1.0)
        # the reading model: scales from the books it has read; a book it has not read borrows
        # the ratings of a random background book that it has read (always the same one)
        L = raw["L"][train]
        have = ~np.isnan(L[:, 0])
        self.l_ok = int(have.sum()) >= 2
        if self.l_ok:
            pool = L[have & raw["bg"][train]]
            self.l_pool = pool if len(pool) else L[have]
            self.l_mean, self.l_sd = L[have].mean(axis=0), np.where(L[have].std(axis=0) > 0, L[have].std(axis=0), 1.0)
        return self

    def _read(self, L, kh):
        if not getattr(self, "l_ok", False):
            return np.zeros(L.shape)
        out = L.copy()
        miss = np.isnan(out[:, 0])
        if miss.any():
            out[miss] = self.l_pool[kh[miss] % len(self.l_pool)]
        return np.clip((out - self.l_mean) / self.l_sd, -5, 5)

    def _fill(self, S):
        return np.where(np.isnan(S), self.s_med, S)

    def transform(self, raw, hide=None, blocks=ALL_BLOCKS):
        """hide: None = every book shows its own rating; a mask = these books hide it."""
        parts = []
        R, A = rating_columns(raw["F"], self.prior, hide)
        for b in ALL_BLOCKS + ("N",):
            if b not in blocks:
                continue
            if b in ("L", "N"):
                parts.append(self._read(raw[b], raw["kh"]))
            elif b == "G":
                parts.append(raw["G"])
            elif b == "lw":
                parts.append(((raw["lw"] - self.lw_mean) / self.lw_sd)[:, None])
            elif b == "tr":
                parts.append(raw["tr"][:, None])
            elif b == "S":
                parts.append(np.clip((self._fill(raw["S"]) - self.s_mean) / self.s_sd, -5, 5))
            elif b == "E":
                parts.append(self.pca_e.transform(raw["E"]) / self.scale_e)
            elif b == "O":
                parts.append(self.pca_o.transform(raw["O"]) / self.scale_o)
            elif b == "R":
                parts.append((R - self.r_mean) / self.r_sd)
            elif b == "A":
                parts.append((A - self.a_mean) / self.a_sd)
        return np.hstack(parts)


def take(raw, idx):
    return {k: v[idx] for k, v in raw.items()}


def ranking(pred, y):
    """How well the prediction puts the books I like (target 4-5) above the others (target 1-2)."""
    return rank_metrics(pred[y >= 4], pred[y <= 2])


def fit_model(kind, X, y, w, groups, seed):
    if kind == "gbm":
        model = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=8, min_samples_leaf=20,
                                              l2_regularization=1.0, random_state=seed)
        return model.fit(X, y, sample_weight=w), None
    # ridge: the penalty is chosen on an inner author-grouped split of the training part
    cls = (y >= 4).astype(int)
    inner = list(StratifiedGroupKFold(3, shuffle=True, random_state=seed).split(X, cls, groups))
    best, best_auc = ALPHAS[0], -1.0
    for alpha in ALPHAS:
        aucs = []
        for tr, te in inner:
            m = ranking(Ridge(alpha=alpha).fit(X[tr], y[tr], sample_weight=w[tr]).predict(X[te]), y[te])
            if m:
                aucs.append(m["auc"])
        auc = float(np.mean(aucs)) if aucs else 0.0
        if auc > best_auc:
            best, best_auc = alpha, auc
    return Ridge(alpha=best).fit(X, y, sample_weight=w), best


def run_fold(raw, y, w, groups, hide, prior, train, test, seed):
    """One test fold: every kind of model on every set of factor groups."""
    prep = Prep().fit(raw, train, prior, hide)
    raw_tr, raw_te = take(raw, train), take(raw, test)
    everyone = np.ones(len(test), dtype=bool)
    was_read = ~np.isnan(raw_te["L"][:, 0])  # the test books that the reading model has read
    out = {}
    for name, blocks in BLOCK_SETS.items():
        Xtr = prep.transform(raw_tr, hide[train], blocks)
        X_seen = prep.transform(raw_te, None, blocks)  # the test books show their own rating
        X_hidden = prep.transform(raw_te, everyone, blocks)  # the test books are "new": no rating of their own
        for kind in KINDS:
            model, _ = fit_model(kind, Xtr, y[train], w[train], groups[train], seed)
            seen, hidden = model.predict(X_seen), model.predict(X_hidden)
            out[(kind, name)] = {"seen": seen, "hidden": hidden, "m_seen": ranking(seen, y[test]), "m_hidden": ranking(hidden, y[test]),
                                 "r_seen": ranking(seen[was_read], y[test][was_read]), "r_hidden": ranking(hidden[was_read], y[test][was_read]),
                                 "model": model if name in ("cheap", "all") else None}
    return test, prep, out


def effects(prep, model, raw, names, blocks):
    """How much the predicted score changes when one factor goes from low to high, all else as it is."""
    X = prep.transform(raw, None, blocks)
    cols = [n for b in ALL_BLOCKS if b in blocks for n in names[b]]
    G = raw["G"]
    was_read = ~np.isnan(raw["L"][:, 0])
    out = []
    for j, name in enumerate(cols):
        col = X[:, j]
        yes_no = len(np.unique(col)) <= 2
        # a factor of the reading model: low and high among the books it has read
        base = col[was_read] if name.startswith("reading:") and was_read.any() else col
        lo, hi = (col.min(), col.max()) if yes_no else np.percentile(base, [10, 90])
        if hi <= lo:
            continue
        low, high = X.copy(), X.copy()
        low[:, j], high[:, j] = lo, hi
        effect = float((model.predict(high) - model.predict(low)).mean())
        hint = ""
        if name.startswith("text of"):  # a text direction has no name: say which genre it goes with
            cors = [np.corrcoef(col, G[:, g])[0, 1] if G[:, g].std() > 0 else 0.0 for g in range(G.shape[1])]
            g = int(np.nanargmax(np.abs(cors)))
            if abs(cors[g]) >= 0.2:
                hint = ("typical for " if cors[g] > 0 else "the opposite of ") + names["G"][g].replace("genre: ", "")
        out.append({"name": name, "effect": effect, "yes_no": bool(yes_no), "hint": hint})
    return out


def compare_reading(done, kind, n_read, n_read_liked):
    """The full model against the cheap one, on the test books that the reading model has read.
    The same folds for both, so the differences are paired. "noise": the cheap factors plus shuffled ratings."""
    out = {"books_read": n_read, "liked_read": n_read_liked}
    for cond in ("seen", "hidden"):
        rows = [(o[(kind, "cheap")]["r_" + cond], o[(kind, "all")]["r_" + cond], o[(kind, "noise")]["r_" + cond], o[(kind, "reading")]["r_" + cond])
                for _, _, o in done]
        rows = [r for r in rows if all(r)]
        if not rows:
            return None
        cheap, full, noise, alone = (np.array([m["auc"] for m in col]) for col in zip(*rows))
        top_cheap, top_full = (np.array([m["top"] for m in col]) for col in list(zip(*rows))[:2])
        gain, fake = full - cheap, noise - cheap
        # the spread between test parts, counted as 5 independent parts (the repeats reuse the same books)
        se = float(gain.std() / math.sqrt(FOLDS))
        out[cond] = {
            "cheap": float(cheap.mean()), "full": float(full.mean()), "noise": float(noise.mean()), "alone": float(alone.mean()),
            "gain": float(gain.mean()), "gain_sd": float(gain.std()), "gain_se": se, "wins": int((gain > 0).sum()), "folds": len(gain),
            "noise_gain": float(fake.mean()), "noise_gain_sd": float(fake.std()),
            "top5_cheap": float(top_cheap.mean()), "top5_full": float(top_full.mean()),
            "beats_noise": bool(gain.mean() - 2 * se > max(0.0, float(fake.mean()))),
        }
    return out


def train(db):
    t0 = time.time()
    loader = Loader(db)
    books = training_books(db, loader)
    y = np.array([b["y"] for b in books])
    w = np.array([b["w"] for b in books])
    cls = (y >= 4).astype(int)
    groups = author_groups(books)
    raw = loader.raw(books)
    hide = hide_half(books)
    prior = loader.prior
    n = len(books)
    was_read = ~np.isnan(raw["L"][:, 0])
    n_read = int(was_read.sum())
    # the full model needs enough read books on both sides (liked and not)
    with_reading = n_read >= MIN_READ and int((was_read & (y >= 4)).sum()) >= 20 and int((was_read & (y <= 2)).sum()) >= 20
    # the control: the same ratings, handed to other books
    shuffled = raw["L"].copy()
    idx = np.flatnonzero(was_read)
    shuffled[idx] = raw["L"][np.random.default_rng(SEED).permutation(idx)]
    raw["N"] = shuffled

    jobs = []
    for rep in range(REPEATS):
        for f, (tr, te) in enumerate(StratifiedGroupKFold(FOLDS, shuffle=True, random_state=SEED + rep).split(np.zeros(n), cls, groups)):
            jobs.append((tr, te, SEED + 100 * rep + f))
    done = Parallel(n_jobs=-1)(delayed(run_fold)(raw, y, w, groups, hide, prior, tr, te, seed) for tr, te, seed in jobs)

    # ranking quality of every kind of model and every set of factor groups
    quality = {}
    for kind in KINDS:
        for name in BLOCK_SETS:
            q = {}
            for cond in ("seen", "hidden"):
                ms = [out[(kind, name)]["m_" + cond] for _, _, out in done if out[(kind, name)]["m_" + cond]]
                q[cond] = {"auc": float(np.mean([m["auc"] for m in ms])), "auc_sd": float(np.std([m["auc"] for m in ms])),
                           "top5": float(np.mean([m["top"] for m in ms])), "top5_sd": float(np.std([m["top"] for m in ms]))}
            quality[f"{kind}:{name}"] = q
    # the kind that ranks better in the author-grouped test, rated and unrated books counted alike.
    # It is chosen on the cheap factors, which every book has.
    both = {k: (quality[f"{k}:cheap"]["seen"]["auc"] + quality[f"{k}:cheap"]["hidden"]["auc"]) / 2 for k in KINDS}
    kind = max(both, key=both.get)
    reading = compare_reading(done, kind, n_read, int((was_read & (y >= 4)).sum())) if with_reading else None

    # what every book got while it sat on the test side: from the full model when the book was read
    oof_seen, oof_hidden, oof_cheap = np.zeros(n), np.zeros(n), np.zeros(n)
    for test, _, out in done:
        use_full = was_read[test] & with_reading
        oof_seen[test] += np.where(use_full, out[(kind, "all")]["seen"], out[(kind, "cheap")]["seen"]) / REPEATS
        oof_hidden[test] += np.where(use_full, out[(kind, "all")]["hidden"], out[(kind, "cheap")]["hidden"]) / REPEATS
        oof_cheap[test] += out[(kind, "cheap")]["seen"] / REPEATS

    everything = np.arange(n)
    prep = Prep().fit(raw, everything, prior, hide)
    model, alpha = fit_model(kind, prep.transform(raw, hide, CHEAP_BLOCKS), y, w, groups, SEED)
    full = fit_model(kind, prep.transform(raw, hide, ALL_BLOCKS), y, w, groups, SEED)[0] if with_reading else None

    # agreement with my scores, on books the model had not seen
    scored = [i for i, b in enumerate(books) if b["role"] == "scored"]
    my = np.array([books[i]["score"] for i in scored], dtype=float)
    spearman = top10 = spearman_cheap = None
    if len(scored) >= MIN_SCORES:
        rho = spearmanr(oof_seen[scored], my).statistic
        spearman = None if np.isnan(rho) else float(rho)
        rho = spearmanr(oof_cheap[scored], my).statistic
        spearman_cheap = None if np.isnan(rho) else float(rho)
        best = np.argsort(-oof_seen[scored])[:10]
        top10 = float(my[best].mean())

    roles = [b["role"] for b in books]
    kinds = [b["kind"] for b in books]
    q = quality[f"{kind}:cheap"]
    info = {
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": kind, "kind_name": KINDS[kind], "alpha": alpha,
        "auc": q["seen"]["auc"], "auc_sd": q["seen"]["auc_sd"], "auc_hidden": q["hidden"]["auc"], "auc_hidden_sd": q["hidden"]["auc_sd"],
        "top5": q["seen"]["top5"], "top5_sd": q["seen"]["top5_sd"],
        "auc_by_kind": {k: {"seen": quality[f"{k}:cheap"]["seen"]["auc"], "hidden": quality[f"{k}:cheap"]["hidden"]["auc"]} for k in KINDS},
        "books": n, "library": roles.count("library"), "scored": len(scored),
        "scored_read": sum(1 for i in scored if books[i]["read"]), "scored_card": sum(1 for i in scored if not books[i]["read"]),
        "scores_by_value": {str(v): int((my == v).sum()) for v in range(1, 6)},
        "never": roles.count("never"), "background": roles.count("background"),
        "background_matched": sum(1 for b in books if b["role"] == "background" and b["kind"] == "matched"),
        "background_rated": sum(1 for b in books if b["role"] == "background" and b["kind"] == "rated"),
        "background_feed": sum(1 for b in books if b["role"] == "background" and b["kind"] == "feed"),
        "feed_books": kinds.count("feed"), "hidden_half": int(hide.sum()),
        "with_votes": int((raw["F"][:, 0] > 0).sum()), "with_author_record": int((raw["F"][:, 2] > 0).sum()),
        "with_series_record": int((raw["F"][:, 5] > 0).sum()),
        "prior": prior, "min_scores": MIN_SCORES, "spearman": spearman, "spearman_cheap": spearman_cheap, "top10_avg": top10,
        "weights": {"library": W_LIBRARY, "background": W_BACKGROUND, "read": W_READ, "card": W_CARD, "never": W_NEVER},
        "folds": FOLDS, "repeats": REPEATS,
        # the reading model: how many training books it has read, and the full model against the cheap one
        "reader_model": reader.MODEL, "read_books": n_read,
        "read_library": int(sum(1 for i in idx if roles[i] == "library")), "read_background": int(sum(1 for i in idx if roles[i] == "background")),
        "read_scored": int(sum(1 for i in idx if roles[i] == "scored")), "reading": reading,
    }
    info["seconds"] = round(time.time() - t0, 1)

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    # the test models, kept so that a feed book from the training data is scored by models that have not seen it
    fold_models = [{"prep": p, "model": out[(kind, "cheap")]["model"], "full": out[(kind, "all")]["model"] if with_reading else None,
                    "keys": {books[i]["key"] for i in test if books[i]["kind"] == "feed"}}
                   for test, p, out in done]
    tmp = MODEL_PATH.with_suffix(".tmp")
    joblib.dump({"version": 3, "prep": prep, "model": model, "full": full, "info": info, "folds": fold_models}, tmp, compress=3)
    tmp.replace(MODEL_PATH)
    set_meta(db, "model_info", info)
    db.commit()

    # everything the report shows
    names = loader.names(prep.dims)
    report = {
        "info": info, "quality": quality, "kinds": KINDS, "group_names": GROUP_NAMES,
        "groups": {g: [n for b in GROUPS[g] for n in names[b]] for g in GROUPS},
        "effects": effects(prep, full, raw, names, ALL_BLOCKS) if with_reading else effects(prep, model, raw, names, CHEAP_BLOCKS),
        "books": [{"role": b["role"], "kind": b["kind"], "seen": round(float(oof_seen[i]), 3), "hidden": round(float(oof_hidden[i]), 3),
                   "score": b.get("score"), "read": b.get("read"), "votes": int(b["facts"][0]),
                   "title": b["title"] if b["role"] == "scored" else None, "authors": b["author_names"] if b["role"] == "scored" else None}
                  for i, b in enumerate(books)],
        "fiction_check": get_meta(db, "fiction_check"),
        "ratings": get_meta(db, "ratings_info"),
        "stability": get_meta(db, f"reader_stability:{reader.MODEL}"),
        "reading_progress": get_meta(db, "reading_progress"),
    }
    REPORT_DATA.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    scored_n = score(db, loader=loader, everything=True)

    print(f"Trained on {n} books: {info['library']} library books (count as 5), {info['scored']} books I scored "
          f"({info['scored_read']} after reading, {info['scored_card']} from the card), {info['never']} \"would never read\" (count as 1), "
          f"{info['background']} background books (count as 2, weight {W_BACKGROUND}: {info['background_matched']} random catalog, "
          f"{info['background_rated']} catalog with ratings like my library, {info['background_feed']} feed).")
    for k in KINDS:
        a = info["auc_by_kind"][k]
        print(f"  {KINDS[k]}: ranking quality {a['seen']:.3f} with the book's own rating, {a['hidden']:.3f} without it{'   <- kept' if k == kind else ''}")
    print(f"Share of liked books in the top 5%: {100 * info['top5']:.1f}% (± {100 * info['top5_sd']:.1f}).")
    print(describe_reading(info))
    if spearman is None:
        print(f"Agreement with my scores: shown when I have {MIN_SCORES} scores ({info['scored']} so far).")
    else:
        print(f"Agreement with my scores on unseen books: Spearman {spearman:.2f} (cheap model alone {spearman_cheap:.2f}); "
              f"my average score for its top 10: {top10:.2f}.")
    print(f"Model saved to {MODEL_PATH.relative_to(ROOT)}. {scored_n} feed books rescored. Took {time.time() - t0:.0f} s.")
    return report


def describe_reading(info):
    """Plain words: did the ratings of the reading model improve the ranking?"""
    r = info.get("reading")
    if not r:
        return (f"Reading model: {info.get('read_books', 0)} training books read so far, too few for the full model "
                f"(it needs {MIN_READ}, with at least 20 liked and 20 other books). Every book keeps the cheap score.")
    lines = [f"Reading model ({info['reader_model']}): {info['read_books']} training books read ({info['read_library']} library, "
             f"{info['read_background']} background, {info['read_scored']} scored). Ranking quality on the read books, same author-grouped test:"]
    for cond, what in (("seen", "with the book's own rating"), ("hidden", "without it (a new book)")):
        c = r[cond]
        lines.append(f"  {what}: cheap model {c['cheap']:.3f}, with the reading model {c['full']:.3f} (change {c['gain']:+.3f} ± {c['gain_se']:.3f}, "
                     f"better in {c['wins']} of {c['folds']} test parts); with shuffled ratings {c['noise']:.3f} ({c['noise_gain']:+.3f}); "
                     f"reading model alone {c['alone']:.3f}. {'Beats the noise.' if c['beats_noise'] else 'Does not beat the noise.'}")
    return "\n".join(lines)


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


def predict(saved, raw, keys, which, blocks):
    """The predicted score of feed books, with the ratings they have today. which: "model" (cheap) or "full"."""
    pred = saved[which].predict(saved["prep"].transform(raw, None, blocks))
    total, count = np.zeros(len(keys)), np.zeros(len(keys))
    for fold in saved.get("folds") or []:
        idx = np.array([i for i, k in enumerate(keys) if k in fold["keys"]], dtype=int)
        if len(idx):
            total[idx] += fold[which].predict(fold["prep"].transform(take(raw, idx), None, blocks))
            count[idx] += 1
    return np.where(count > 0, total / np.maximum(count, 1), pred)


def reasons(saved, raw, was_read):
    """For every read book: the three qualities that raised its score most and the three that lowered it most.
    The change is measured against a book that the reading model finds average in that quality."""
    X = saved["prep"].transform(raw, None, ALL_BLOCKS)
    base = saved["full"].predict(X)
    first = X.shape[1] - len(READ_NAMES)  # the reading columns are the last block
    deltas = np.zeros((len(X), len(READ_NAMES)))
    for j in range(len(READ_NAMES)):
        Z = X.copy()
        Z[:, first + j] = 0.0
        deltas[:, j] = base - saved["full"].predict(Z)
    out = []
    for i in range(len(X)):
        if not was_read[i]:
            out.append(None)
            continue
        items = [{"name": READ_NAMES[j].replace("reading: ", ""), "value": round(float(raw["L"][i, j]), 2), "change": round(float(deltas[i, j]), 3),
                  "scale": "1-5" if j < N_QUALITIES else "share"} for j in range(len(READ_NAMES))]
        up = sorted((x for x in items if x["change"] > 0.004), key=lambda x: -x["change"])[:3]
        down = sorted((x for x in items if x["change"] < -0.004), key=lambda x: x["change"])[:3]
        out.append({"up": up, "down": down})
    return out


def score(db, loader=None, everything=False):
    """Scores feed books that passed the filters. Every book gets the cheap score; a book that the
    reading model has read gets its final score from the full model, and the reasons.
    everything = False: books without a score, the unanswered books of the last 60 days (their
    ratings may have changed), and the books read by the reading model since they were last scored."""
    import fiction_check

    if not MODEL_PATH.exists():
        sys.exit('No trained model yet. Run "npm run train" first.')
    saved = joblib.load(MODEL_PATH)
    if saved.get("version") != 3:
        sys.exit('The saved model is from the earlier version. Run "npm run train" once.')
    waiting = db.execute("SELECT f.key FROM feed_scores s JOIN feed_books f ON f.key = s.key WHERE s.passed = 1").fetchall()
    made = make_vectors(db, waiting)
    flagged = fiction_check.apply(db)  # the text check: books that read like non-fiction leave the list
    since = (datetime.now(timezone.utc) - timedelta(days=RESCORE_DAYS)).isoformat()
    where = "" if everything else ("AND (s.score IS NULL OR (s.added_at >= ? AND s.key NOT IN (SELECT key FROM answers) "
                                   "AND s.key NOT IN (SELECT key FROM book_state)) "
                                   "OR s.key IN (SELECT key FROM readings WHERE read_at > COALESCE(s.scored_at, '')))")
    sql = f"SELECT f.key, f.authors, f.title, f.genres, f.words, f.is_translation FROM feed_scores s JOIN feed_books f ON f.key = s.key WHERE s.passed = 1 {where}"
    rows = db.execute(sql, () if everything else (since,)).fetchall()
    if made or loader is None:
        loader = Loader(db)  # reads the stylometry of the new books
    books = [b for b in (loader.book("feed", r) for r in rows) if b is not None]
    top = get_meta(db, "top_percent")
    top = float(top) if isinstance(top, (int, float)) and 0 < top <= 100 else float(CONFIG.get("topPercent", 5))
    before = {r["key"]: (r["percentile"], r["votes"], r["note"]) for r in db.execute("SELECT key, percentile, votes, note FROM feed_scores")}
    n_full = 0
    if books:
        raw = loader.raw(books)
        keys = [b["key"] for b in books]
        cheap = predict(saved, raw, keys, "model", CHEAP_BLOCKS)
        was_read = ~np.isnan(raw["L"][:, 0]) & (saved.get("full") is not None)
        final, why = cheap.copy(), [None] * len(books)
        if was_read.any():
            final = np.where(was_read, predict(saved, raw, keys, "full", ALL_BLOCKS), cheap)
            why = reasons(saved, raw, was_read)
            n_full = int(was_read.sum())
        # the liked books closest by text: my library plus the books I scored 4 or 5
        liked = [b for b in (loader.book("liked", r) for r in db.execute(LIKED_SQL)) if b]
        liked += [b for b in (loader.book("feed", r) for r in db.execute(
            f"SELECT {COLS} FROM feed_books WHERE key IN (SELECT key FROM answers WHERE score >= 4)")) if b]
        L = np.vstack([b["vec"] for b in liked])
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for b, s, c, y in zip(books, final, cheap, why):
            sims = L @ b["vec"]
            near = [i for i in np.argsort(-sims) if liked[i]["key"] != b["key"]][:CLOSEST]
            closest = [{"authors": liked[i]["author_names"], "title": liked[i]["title"], "sim": round(float(sims[i]), 3)} for i in near]
            db.execute("UPDATE feed_scores SET score = ?, cheap_score = ?, reasons = ?, closest = ?, scored_at = ?, votes = ? WHERE key = ?",
                       (float(s), float(c), json.dumps(y, ensure_ascii=False) if y else None, json.dumps(closest, ensure_ascii=False), now,
                        int(b["facts"][0]), b["key"]))
    # a book that no longer passes the filters (for example set aside by the text check) keeps no score
    db.execute("""UPDATE feed_scores SET score = NULL, cheap_score = NULL, reasons = NULL, percentile = NULL, note = NULL WHERE passed = 0 AND score IS NOT NULL
        AND key NOT IN (SELECT key FROM answers) AND key NOT IN (SELECT key FROM book_state)""")
    # the percentile of every book among all feed books scored so far: 0 = the best, 100 = the worst
    allrows = db.execute("SELECT key, score FROM feed_scores WHERE score IS NOT NULL ORDER BY score DESC").fetchall()
    risen = 0
    for rank, r in enumerate(allrows, 1):
        pct = 100.0 * rank / len(allrows)
        old_pct, old_votes, note = before.get(r["key"], (None, None, None))
        votes = int(loader.facts.get(r["key"], [0])[0])
        # "now rated": new votes arrived and the book moved up into the recommended range
        if pct > top:
            note = None
        elif old_pct is not None and old_pct > top and old_votes is not None and votes > old_votes:
            note = "now rated"
            risen += 1
        db.execute("UPDATE feed_scores SET percentile = ?, note = ? WHERE key = ?", (pct, note, r["key"]))
    db.commit()
    score.last = {"risen": risen, "flagged": flagged, "full": n_full}
    return len(books)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in ("train", "score"):
        sys.exit("Use: recommend.py train   or   recommend.py score [--no-read]")
    db = connect()
    if cmd == "train":
        import fiction_check
        import portrait
        import report

        check = fiction_check.train(db)
        print(fiction_check.describe(check))
        data = train(db)
        data["fiction_check"] = get_meta(db, "fiction_check")
        data["portrait"] = portrait.build(db)
        REPORT_DATA.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        out = report.build(data)
        print(f"Model report: {out.relative_to(ROOT)}. Portrait of my taste: reports/portrait.md")
    else:
        n = score(db)
        last = score.last
        # the reading model reads the best unread books of the day; then the books it read are scored again
        read = 0
        if "--no-read" not in sys.argv:
            import argparse

            read = reader.cmd_top(db, argparse.Namespace(model=reader.MODEL, n=reader.DAILY_BOOKS))
            if read:
                score(db)
                last["full"] = score.last["full"]
        total = db.execute("SELECT COUNT(*) FROM feed_scores WHERE score IS NOT NULL").fetchone()[0]
        by_reader = db.execute("SELECT COUNT(*) FROM feed_scores WHERE score IS NOT NULL AND reasons IS NOT NULL").fetchone()[0]
        print(f"Scored {n} feed books (new ones, and the unanswered ones of the last {RESCORE_DAYS} days with today's ratings). "
              f"{total} feed books have a score now; {by_reader} of them were read by the reading model ({read} in this run), "
              f"the others keep the cheap score. {last['risen']} moved up into the recommended range after new votes. "
              f"{last['flagged']} newly set aside by the text check as non-fiction.")
    db.close()


if __name__ == "__main__":
    main()
