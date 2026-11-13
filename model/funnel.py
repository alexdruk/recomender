"""Milestone 3c. The selection funnel: copy how I really choose books.

Run:  .venv/bin/python model/funnel.py        writes reports/funnel.md
"npm run funnel" runs the steps before it (they skip what is already done) and then this file.

Decision 1, before reading ("should I try this book?"): reader rating, reader
comments, the series rule, authors I follow.
Decision 2, while reading ("do I like it?"): style and plot of the first 100 pages.

What this file does:
  Part A  checks: reader comments in the catalog, series of my liked books
  Part D  signals for Decision 1, compared on the eligible rated-matched set:
          rb, + series length, + like-minded readers, + item-to-item, + comments
  Part E  text test for Decision 2: liked books against "read it, did not like it" books
  Part F  the funnel counts, the short list, the test of Decision 1 on my new answers,
          and the queue of the labelling page (table "label_queue")

Needs: data/index.db and the table "book_facts" (node src/build-index.js), the
"stylometry_opening" table (node src/stylometry.js), and the vectors in data/emb,
data/emb-open and data/emb-comments (model/embed.py).
"""

import hashlib
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from joblib import Parallel, delayed
from scipy import sparse
from sklearn.decomposition import PCA
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fingerprints as fp  # noqa: E402
from baseline import jlist, norm_author, pct, table  # noqa: E402

ROOT = fp.ROOT
DATA = fp.DATA
DB_PATH = fp.DB_PATH
CAT_PATH = fp.CAT_PATH
INDEX_PATH = DATA / "index.db"
EMB_OPEN = DATA / "emb-open"
EMB_COMMENTS = DATA / "emb-comments"
REPORT = ROOT / "reports" / "funnel.md"

FOLDS, REPEATS, SEED = fp.FOLDS, fp.REPEATS, fp.SEED
MIN_VOTES = fp.MIN_VOTES
MIN_AVG = 3.0
ELIGIBLE = ("standalone", "first", "continuation", "unknown")
MAIN = ("standalone", "first")  # the main test uses only these, on both sides
I2I_NEAREST = 5  # item-to-item: average over this many nearest liked books
COMMENT_DIMS = 20  # PCA directions of the comment embeddings
TEXT_DIMS = 10  # PCA directions in the small Decision 2 test
MIN_DISLIKED = 30  # Part E runs when at least this many books are marked "read it, did not like it"
MIN_NEW_ANSWERS = 60  # the test of Decision 1 runs when the "New books" tab has this many answers
QUEUE_HALF = 100  # books in each half of the labelling queue (top-scored and random)
MIN_LIKED_BEFORE_LIST = 2  # until I fill my list of authors: authors with at least this many liked books
LIST_AUTHORS = 40
LIST_NEW = 20

MODELS = {
    "rb": ("rb: ratings + genres, length, translated, in a series", ["r", "b0"]),
    "rb+s": ("rb+s: rb + series length", ["r", "b0", "series"]),
    "rb+s+lm": ("rb+s+lm: + like-minded readers", ["r", "b0", "series", "lm"]),
    "rb+s+lm+i2i": ("rb+s+lm+i2i: + item-to-item", ["r", "b0", "series", "lm", "i2i"]),
    "rb+s+lm+i2i+c": ("rb+s+lm+i2i+c: + reader comments", ["r", "b0", "series", "lm", "i2i", "c"]),
    "rb+s+i2i": ("rb+s+i2i: item-to-item without like-minded readers", ["r", "b0", "series", "i2i"]),
    "rb+s+lm+c": ("rb+s+lm+c: comments without item-to-item", ["r", "b0", "series", "lm", "c"]),
}
CHAIN = ["rb", "rb+s", "rb+s+lm", "rb+s+lm+i2i", "rb+s+lm+i2i+c"]
PAIRS = [
    ("rb+s", "rb", "series length on top of rb"),
    ("rb+s+lm", "rb+s", "like-minded readers on top of that"),
    ("rb+s+lm+i2i", "rb+s+lm", "item-to-item on top of that"),
    ("rb+s+lm+i2i+c", "rb+s+lm+i2i", "reader comments on top of that"),
    ("rb+s+i2i", "rb+s", "item-to-item instead of like-minded readers"),
    ("rb+s+lm+i2i", "rb+s+i2i", "like-minded readers when item-to-item is already there"),
    ("rb+s+lm+c", "rb+s+lm", "reader comments without item-to-item"),
]
NO_COMMENT_MODELS = [m for m, (_, cols) in MODELS.items() if "c" not in cols]


# ---------------------------------------------------------------- loading


def unit(v):
    return v / (np.linalg.norm(v) or 1.0)


def mean_vector(path):
    if not path.exists():
        return None
    return unit(np.load(path).astype(np.float32).mean(axis=0))


def connect(path, readonly=True):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True) if readonly else sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def load():
    if not INDEX_PATH.exists():
        sys.exit('data/index.db not found. Run "npm run index" first.')
    d = fp.load()
    db = connect(DB_PATH)

    def rows(sql, args=()):
        return [dict(r) for r in db.execute(sql, args)]

    def has(name):
        return db.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone() is not None

    if not has("book_facts"):
        sys.exit('No "book_facts" table yet. Run "npm run index" first.')
    facts = {r["key"]: r for r in rows("SELECT * FROM book_facts")}
    styl_open = {(r["kind"], r["key"]): json.loads(r["features"]) for r in rows("SELECT * FROM stylometry_opening WHERE features IS NOT NULL")} if has("stylometry_opening") else {}
    styl = {(r["kind"], r["key"]): json.loads(r["features"]) for r in rows("SELECT * FROM stylometry WHERE features IS NOT NULL")}
    marks = {r["id"]: r for r in rows("SELECT * FROM read_marks")} if has("read_marks") else {}
    followed = {r["author_id"]: r["name"] for r in rows("SELECT * FROM followed_authors")} if has("followed_authors") else {}
    all_labels = rows("SELECT * FROM labels") if has("labels") else []
    liked_facts = [r for r in facts.values() if r["kind"] == "liked"]

    # books I marked "read it" that were downloaded for it (kind = extra)
    books = d["books"]
    no_vector = 0
    for b in rows("SELECT * FROM extra_books WHERE parse_status != 'failed'") if has("extra_books") else []:
        key = b["key"]
        if key not in facts or facts[key]["kind"] != "extra":
            continue
        vec = fp.book_vector("extra", key)
        if vec is None:
            no_vector += 1
            continue
        ids, names = jlist(b["cat_author_ids"]), jlist(b["authors"])
        authors = [f"id{i}" for i in ids] if ids else [f"n:{norm_author(n)}" for n in names if n.strip()]
        in_catalog = b["cat_author_ids"] is not None or b["cat_genres"] is not None
        year = b["cat_year"] if b["cat_year"] else b["year"]
        books.append({
            "key": key, "y": 0, "source": "extra", "store": "extra", "title": b["title"] or "", "author_names": ", ".join(names),
            "authors": authors or [f"solo:0:{key}"], "genres": jlist(b["cat_genres"]) or jlist(b["genres"]), "groups": jlist(b["genre_groups"]),
            "words": b["words"] or 0, "translated": int(b["cat_translated"] if b["cat_translated"] is not None else (b["is_translation"] or 0)),
            "in_series": int(bool(jlist(b["cat_series"])) if in_catalog else bool(b["series"])),
            "year": int(year) if year and 1800 <= int(year) <= 2100 else None, "vec": vec, "styl": styl.get(("extra", key)), "label": None, "star": False,
        })
    db.close()

    missing_open = 0
    for b in books:
        key = b["key"]
        f = facts.get(key) or {"cat_id": fp.book_id(key), "seq_id": None, "series": None, "series_no": None, "series_total": None,
                               "status": "standalone", "votes": 0, "avg": None, "comments": 0, "author_ids": None, "kind": "?"}
        b["facts"] = f
        b["status"] = f["status"]
        b["author_ids"] = [int(a) for a in jlist(f["author_ids"])]
        if b["y"] == 1:
            b["vec_open"] = mean_vector(EMB_OPEN / "liked_fb2" / f"{key}.npy")
            if b["vec_open"] is None:
                b["vec_open"] = mean_vector(EMB_OPEN / "liked_epub" / f"{key}.npy")
            b["styl_open"] = styl_open.get(("liked", key))
        else:
            b["vec_open"] = mean_vector(EMB_OPEN / b["store"] / f"{key}.npy")
            b["styl_open"] = styl_open.get(("extra" if b["store"] == "extra" else "unlabeled", key))
        if b["vec_open"] is None:
            missing_open += 1
        b["vec_comments"] = mean_vector(EMB_COMMENTS / f"{key}.npy")
        mark = marks.get(f["cat_id"]) or marks.get(fp.book_id(key))
        b["mark"] = mark["mark"] if mark else None
    d.update({"facts": facts, "marks": marks, "followed": followed, "all_labels": all_labels, "liked_facts": liked_facts,
              "missing_open": missing_open, "extra_no_vector": no_vector})
    return d


def load_votes(cat, ix, books):
    """Every vote on our books, as the three book-by-reader tables of fingerprints.py.
    Votes left on an older copy of a book count for the book, as on the site."""
    n = len(books)
    row_of = defaultdict(set)
    for i, b in enumerate(books):
        ids = {fp.book_id(b["key"]), b["facts"]["cat_id"]}
        ids.discard(None)
        if b["facts"]["cat_id"] is not None:
            ids.update(r[0] for r in ix.execute("SELECT id FROM alias WHERE root = ?", (b["facts"]["cat_id"],)))
        for bid in ids:
            row_of[bid].add(i)
    ids = sorted(row_of)
    best = {}
    for start in range(0, len(ids), 900):
        part = ids[start:start + 900]
        sql = f"SELECT BookId, UserId, CAST(Rate AS INTEGER) FROM librate WHERE BookId IN ({','.join('?' * len(part))})"
        for bid, user, rate in cat.execute(sql, part):
            for i in row_of[bid]:
                if rate > best.get((i, user), 0):
                    best[(i, user)] = rate
    r_idx = np.array([k[0] for k in best], dtype=int)
    users = np.array([k[1] for k in best], dtype=np.int64)
    rates = np.array(list(best.values()), dtype=float)
    uniq, cols = np.unique(users, return_inverse=True) if len(users) else (np.array([], dtype=np.int64), np.array([], dtype=int))
    shape = (n, max(1, len(uniq)))
    V = sparse.csr_matrix((rates, (r_idx, cols)), shape=shape)
    P = sparse.csr_matrix((np.ones(len(rates)), (r_idx, cols)), shape=shape)
    H = sparse.csr_matrix(((rates >= 4).astype(float), (r_idx, cols)), shape=shape)
    # each reader's own average mark over the whole catalog, for the item-to-item score
    mean_of = dict(cat.execute("SELECT UserId, AVG(CAST(Rate AS REAL)) FROM librate GROUP BY UserId"))
    centred = rates - np.array([mean_of.get(int(u), 3.0) for u in users]) if len(users) else rates
    Vc = sparse.csr_matrix((centred, (r_idx, cols)), shape=shape)
    return (V, P, H), Vc


# ---------------------------------------------------------------- features


class Data(fp.Data):
    def __init__(self, books, group_names, styl_names, votes, centred):
        super().__init__(books, group_names, styl_names, votes)
        n = len(books)
        self.status = np.array([b["status"] for b in books])
        self.eligible = np.isin(self.status, ELIGIBLE)
        self.is_main = np.isin(self.status, MAIN)
        self.rated = self.source == "rated-matched"
        # the main test: standalone books and first books of a series, on both sides
        self.main_set = self.is_main & ((self.y == 1) | self.rated)
        total = np.array([b["facts"]["series_total"] or 0 for b in books], dtype=float)
        in_series = np.array([b["facts"]["seq_id"] is not None for b in books])
        self.series_len = np.where(in_series, np.log1p(np.maximum(total, 1)), 0.0)
        self.series_total = np.where(in_series, np.maximum(total, 1), 0)
        self.n_comments = np.array([b["facts"]["comments"] or 0 for b in books], dtype=float)
        self.has_comments = np.array([b["vec_comments"] is not None for b in books])
        dim = self.E.shape[1]
        self.CE = np.vstack([b["vec_comments"] if b["vec_comments"] is not None else np.zeros(dim, dtype=np.float32) for b in books])
        self.has_open = np.array([b["vec_open"] is not None for b in books])
        self.EO = np.vstack([b["vec_open"] if b["vec_open"] is not None else b["vec"] for b in books])
        self.SO = np.array([[(b["styl_open"] or {}).get(nm) if (b["styl_open"] or {}).get(nm) is not None else np.nan for nm in styl_names] for b in books], dtype=float)
        # item-to-item: cosine similarity between books on the readers' centred marks
        dots = np.asarray((centred @ centred.T).todense())
        norms = np.sqrt(np.maximum(np.diag(dots), 0))
        self.sim = dots / np.maximum(np.outer(norms, norms), 1e-9)
        self.never = np.array([b["label"] == "never" for b in books]) & self.eligible

    def item_to_item(self, liked_train):
        """How close a book's readers and marks are to those of my liked books.
        Only liked books of the training part are used, and never books by the
        same author (for a training book as well, so it is looked at like a test book)."""
        sub = self.sim[:, liked_train].copy()
        sub[self.groups[:, None] == self.groups[liked_train][None, :]] = -1.0
        k = min(I2I_NEAREST, sub.shape[1])
        top = -np.partition(-sub, k - 1, axis=1)[:, :k]
        return np.column_stack([top.mean(axis=1), top.max(axis=1)])

    def blocks(self, train, lm, i2i):
        B = super().blocks(train, lm)
        fit = lambda X: np.clip(StandardScaler().fit(X[train]).transform(X), -5, 5)  # noqa: E731
        B["series"] = fit(self.series_len[:, None])
        B["i2i"] = fit(i2i)
        # reader comments: how many, and what they say (PCA learned on training books that have comments)
        has = self.has_comments
        tr = train[has[train]]
        if len(tr) > COMMENT_DIMS + 1:
            C = PCA(n_components=COMMENT_DIMS, random_state=SEED).fit(self.CE[tr]).transform(self.CE)
            C[~has] = 0.0
            C = C / (C[tr].std(axis=0).mean() or 1.0)
        else:
            C = np.zeros((len(has), 1))
        B["c"] = np.hstack([fit(np.log1p(self.n_comments)[:, None]), (~has)[:, None].astype(float), C])
        return B


def run_fold(data, rep, f, train_all, test_all):
    """One test fold of the main test. The split is made over all books by author;
    the models are trained on the main-test books of the training side. Every book of
    the test side gets a score from a model that never saw the book or its author."""
    y, groups = data.y, data.groups
    assert not set(groups[train_all]) & set(groups[test_all]), "an author is on both sides of the split"
    seed = SEED + 100 * rep + f
    liked_train = train_all[y[train_all] == 1]
    lm, n_readers = data.like_minded(liked_train)
    i2i = data.item_to_item(liked_train)
    train, test = train_all[data.main_set[train_all]], test_all[data.main_set[test_all]]
    B = data.blocks(train, lm, i2i)
    pos, unl = test[y[test] == 1], test[y[test] == 0]
    out = {}
    for m, (_, cols) in MODELS.items():
        X = np.hstack([B[c] for c in cols])
        model, C = fp.fit_best(X[train], y[train], groups[train], seed)
        dec = model.decision_function(X)
        out[m] = {"metrics": fp.rank_metrics(dec[pos], dec[unl]), "scores": model.predict_proba(X[test_all])[:, 1], "C": C}
    return rep, test_all, out, n_readers


def cross_validate(data):
    y, n = data.y, len(data.y)
    jobs = []
    for rep in range(REPEATS):
        splits = StratifiedGroupKFold(FOLDS, shuffle=True, random_state=SEED + rep).split(np.zeros(n), y, data.groups)
        jobs += [(rep, f, train, test) for f, (train, test) in enumerate(splits)]
    done = Parallel(n_jobs=-1)(delayed(run_fold)(data, rep, f, train, test) for rep, f, train, test in jobs)
    res, oof = {}, {}
    main_pos = data.main_set & (y == 1)
    for m in MODELS:
        parts = [d[2][m]["metrics"] for d in done]
        r = {}
        for k in ("auc", "median", "top"):
            v = np.array([p[k] for p in parts])
            r[k], r[k + "_sd"] = float(v.mean()), float(v.std())
        r["aucs"] = np.array([p["auc"] for p in parts])
        rep_scores = np.zeros((REPEATS, n))
        for rep, test, out, _ in done:
            rep_scores[rep, test] = out[m]["scores"]
        if data.never.sum() >= 5:
            nv = [fp.rank_metrics(rep_scores[k][main_pos], rep_scores[k][data.never])["auc"] for k in range(REPEATS)]
            r["never"], r["never_sd"] = float(np.mean(nv)), float(np.std(nv))
        res[("main", m)] = r
        oof[m] = rep_scores.mean(axis=0)
    return res, oof, np.array([d[3] for d in done])


# ---------------------------------------------------------------- Part E: the text test for Decision 2


def text_test(data, pos_idx, neg_idx):
    """Can text tell liked books from books I read and did not like?
    Author-grouped test: no book (and no author) is in both the training part and the test part."""
    idx = np.concatenate([pos_idx, neg_idx])
    t = np.concatenate([np.ones(len(pos_idx), dtype=int), np.zeros(len(neg_idx), dtype=int)])
    g = data.groups[idx]
    folds = min(FOLDS, len(set(g[t == 0])), len(set(g[t == 1])))
    if folds < 2:
        return None
    names = {"rb": "ratings and genres only", "whole": "whole-book text only", "open": "opening text only (first 25,000 words)"}
    aucs = defaultdict(list)
    pair_hits = defaultdict(list)

    def text_block(S, E, tr):
        med = np.nanmedian(S[idx][tr], axis=0)
        med = np.where(np.isnan(med), 0.0, med)
        Sx = np.where(np.isnan(S[idx]), med, S[idx])
        Sx = np.clip(StandardScaler().fit(Sx[tr]).transform(Sx), -5, 5)
        Ex = PCA(n_components=min(TEXT_DIMS, len(tr) - 1), random_state=SEED).fit(E[idx][tr]).transform(E[idx])
        return np.hstack([Sx, Ex / (Ex[tr].std(axis=0).mean() or 1.0)])

    for rep in range(REPEATS):
        for f, (tr, te) in enumerate(StratifiedGroupKFold(folds, shuffle=True, random_state=SEED + rep).split(idx, t, g)):
            if len(set(t[te])) < 2 or len(set(t[tr])) < 2:
                continue
            sc = lambda X: np.clip(StandardScaler().fit(X[tr]).transform(X), -5, 5)  # noqa: E731
            seen = data.avg[idx][tr]
            typical = float(np.nanmedian(seen)) if np.any(~np.isnan(seen)) else 3.0
            R = np.column_stack([sc(np.where(np.isnan(data.avg[idx]), typical, data.avg[idx])[:, None]), sc(np.log1p(data.votes[idx])[:, None])])
            words = sc(data.log_words[idx][:, None])
            X = {"rb": np.hstack([R, data.G[idx], words, data.flags[idx]]), "whole": text_block(data.S, data.E, tr), "open": text_block(data.SO, data.EO, tr)}
            X["rb+whole"], X["rb+open"] = np.hstack([X["rb"], X["whole"]]), np.hstack([X["rb"], X["open"]])
            for m, Xm in X.items():
                model, _ = fp.fit_best(Xm[tr], t[tr], g[tr], SEED + 100 * rep + f)
                dec = model.decision_function(Xm[te])
                aucs[m].append(fp.rank_metrics(dec[t[te] == 1], dec[t[te] == 0])["auc"])
                # the hardest question: of two books by the same author, is the liked one scored higher?
                for a in set(g[te]):
                    p, q = dec[(g[te] == a) & (t[te] == 1)], dec[(g[te] == a) & (t[te] == 0)]
                    if len(p) and len(q):
                        pair_hits[m].extend(((p[:, None] > q[None, :]) + 0.5 * (p[:, None] == q[None, :])).ravel())
    if not aucs["rb"]:
        return None
    better = "whole" if np.mean(aucs["whole"]) >= np.mean(aucs["open"]) else "open"
    out = {"names": names, "better": better, "folds": folds, "n_pos": len(pos_idx), "n_neg": len(neg_idx), "rows": []}
    for m in ("rb", "whole", "open", "rb+" + better):
        a = np.array(aucs[m])
        label = names.get(m) or f"ratings and genres + {names[better]}"
        out["rows"].append({"m": m, "name": label, "auc": float(a.mean()), "sd": float(a.std()), "aucs": a,
                            "pairs": float(np.mean(pair_hits[m])) if pair_hits[m] else None, "n_pairs": len(pair_hits[m]) // REPEATS})
    return out


# ---------------------------------------------------------------- Part F: the real test of Decision 1


def decision_test(answers):
    """answers: rows of "labels" from the tab "New books" (grp = top / random, label = never / maybe / want)."""
    rows = []
    for who, flag in (("unknown authors", 0), ("authors on my list", 1), ("all", None)):
        for grp, name in (("top", "scored high by the model"), ("random", "picked at random")):
            part = [a for a in answers if a["grp"] == grp and (flag is None or int(a["followed"] or 0) == flag)]
            n = len(part)
            yes = sum(1 for a in part if a["label"] in ("maybe", "want"))
            want = sum(1 for a in part if a["label"] == "want")
            se = math.sqrt(max(yes / n * (1 - yes / n), 1e-9) / n) if n else 0.0
            rows.append({"who": who, "grp": grp, "name": name, "n": n, "yes": yes, "want": want, "never": n - yes, "share": yes / n if n else None, "se": se})
    return rows


# ---------------------------------------------------------------- the queue of the labelling page


def stable_rank(key, salt):
    return hashlib.sha1(f"{salt}:{key}".encode()).hexdigest()


def build_queue(data, score, model, answered):
    """Half of the queue: the books the best model scores highest. Half: picked at random
    from the eligible books (a fixed random order, so the pick does not move between runs).
    The random half is drawn first, from all candidates, so it is a fair sample."""
    books = data.books
    cand = [i for i in range(len(books)) if data.y[i] == 0 and books[i]["source"] != "extra" and data.eligible[i]
            and data.votes[i] >= MIN_VOTES and np.nan_to_num(data.avg[i]) >= MIN_AVG and books[i]["key"] not in answered
            and books[i]["mark"] not in ("disliked", "liked")]
    half = min(QUEUE_HALF, len(cand) // 4)
    rnd = sorted(cand, key=lambda i: stable_rank(books[i]["key"], "random"))[:half]
    taken = set(rnd)
    top = sorted((i for i in cand if i not in taken), key=lambda i: -score[i])[:half]
    queue = [(i, "random") for i in rnd] + [(i, "top") for i in top]
    queue.sort(key=lambda q: stable_rank(books[q[0]]["key"], "mix"))
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS label_queue (key TEXT PRIMARY KEY, grp TEXT NOT NULL, pos INTEGER NOT NULL, score REAL, model TEXT)")
    with db:
        db.execute("DELETE FROM label_queue")
        db.executemany("INSERT INTO label_queue (key, grp, pos, score, model) VALUES (?, ?, ?, ?, ?)",
                       [(books[i]["key"], grp, pos, float(score[i]), model) for pos, (i, grp) in enumerate(queue, 1)])
    db.close()
    return {"candidates": len(cand), "half": half, "top_min": float(min(score[i] for i in top)) if top else None,
            "top_mean": float(np.mean([score[i] for i in top])) if top else None, "rnd_mean": float(np.mean([score[i] for i in rnd])) if rnd else None}


# ---------------------------------------------------------------- catalog numbers


def catalog_numbers(ix, cat, d, liked_authors, list_authors):
    out = {}
    has_reviews = cat.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'reviews'").fetchone() is not None
    out["has_reviews"] = has_reviews
    if has_reviews:
        cols = [r[1] for r in cat.execute("PRAGMA table_info(reviews)")]
        n, books, first, last, empty = cat.execute(
            "SELECT COUNT(*), COUNT(DISTINCT BookId), MIN(Time), MAX(Time), SUM(LENGTH(TRIM(Text)) = 0) FROM reviews").fetchone()
        out["reviews"] = {"cols": cols, "rows": n, "books": books, "first": first, "last": last, "empty": empty or 0,
                          "date_cols": [c for c in cols if any(w in c.lower() for w in ("time", "date"))],
                          "moved": ix.execute("SELECT COUNT(*) FROM alias a WHERE EXISTS (SELECT 1 FROM cat k WHERE k.id = a.root)").fetchone()[0]}
        out["cat_comments"] = [r[0] for r in ix.execute("SELECT comments FROM cat WHERE fiction = 1 AND collection = 0 AND votes >= ?", (MIN_VOTES,))]

    # the funnel: how many catalog books pass each rule in turn
    steps = [
        ("Russian-language books in the catalog, not deleted", "1"),
        ("... that are fiction", "fiction = 1"),
        ("... and not a collection (by title or size)", "collection = 0"),
        ("... and not already in my library", "in_library = 0"),
        (f"... with at least {MIN_VOTES} votes (the rating can be trusted)", f"votes >= {MIN_VOTES}"),
        (f"... with an average of {MIN_AVG:g} or more", f"avg >= {MIN_AVG}"),
        ("... eligible under the series rule", "status IN ('standalone', 'first', 'continuation', 'unknown')"),
    ]
    where, funnel = [], []
    for name, cond in steps:
        where.append(f"({cond})")
        funnel.append((name, ix.execute(f"SELECT COUNT(*) FROM cat WHERE {' AND '.join(where)}").fetchone()[0]))
    out["funnel"] = funnel
    before_series = " AND ".join(where[:-1])
    out["by_status"] = dict(ix.execute(f"SELECT status, COUNT(*) FROM cat WHERE {before_series} GROUP BY status"))
    out["above4"] = ix.execute(f"SELECT COUNT(*) FROM cat WHERE {' AND '.join(where)} AND avg > 4").fetchone()[0]

    marks = d["marks"]
    read_ids = {i for i, m in marks.items() if m["mark"] in ("disliked", "liked")}

    def author_books(author_ids):
        """Eligible fiction books by these authors that I do not have and have not read; one edition per title."""
        best = {}
        for a in author_ids:
            sql = ("SELECT k.* FROM cat_author ca JOIN cat k ON k.id = ca.id WHERE ca.author_id = ? AND k.fiction = 1 AND k.collection = 0 "
                   "AND k.in_library = 0 AND k.status IN ('standalone', 'first', 'continuation', 'unknown')")
            titles = {}
            for r in ix.execute(sql, (a,)):
                r = dict(r)
                old = titles.get(r["norm_title"])
                if r["id"] in read_ids:
                    titles[r["norm_title"]] = None  # any edition marked as read removes the title
                elif r["norm_title"] not in titles or (old is not None and (r["votes"], r["id"]) > (old["votes"], old["id"])):
                    titles[r["norm_title"]] = r
            for r in titles.values():
                if r is not None and not (r["votes"] >= MIN_VOTES and r["avg"] < MIN_AVG):
                    best[r["id"]] = r
        return list(best.values())

    books = author_books(list_authors)
    books.sort(key=lambda r: (r["year"] or int(str(r["uploaded"] or "0")[:4] or 0), str(r["uploaded"] or "")), reverse=True)
    out["author_books"] = books

    # next books of series I have started: number = my highest liked number + 1
    liked_max = {}
    for f in d["liked_facts"]:
        if f["seq_id"] is not None and f["series_no"] is not None:
            liked_max[f["seq_id"]] = max(liked_max.get(f["seq_id"], 0), f["series_no"])
    nxt = {}
    for seq, no in liked_max.items():
        sql = ("SELECT * FROM cat WHERE seq_id = ? AND series_no = ? AND fiction = 1 AND collection = 0 AND in_library = 0 ORDER BY votes DESC LIMIT 1")
        r = ix.execute(sql, (seq, no + 1)).fetchone()
        if r is not None and r["id"] not in read_ids:
            nxt[seq] = dict(r)
    out["next_in_series"] = sorted(nxt.values(), key=lambda r: (-(r["votes"] >= MIN_VOTES), -(r["avg"] or 0)))
    out["continuations_all"] = ix.execute("SELECT COUNT(*) FROM cat WHERE status = 'continuation' AND fiction = 1 AND collection = 0 AND in_library = 0").fetchone()[0]
    names = {a: (ix.execute("SELECT name FROM author WHERE id = ?", (a,)).fetchone() or [f"author {a}"])[0] for a in liked_authors}
    out["author_names"] = names
    return out


# ---------------------------------------------------------------- report helpers


def series_text(f):
    if not f.get("series"):
        return ""
    no, total = f.get("series_no"), f.get("series_total")
    if no and total:
        return f"{f['series']} ({no} of {total})"
    return f"{f['series']} (number not known)" if not no else f"{f['series']} ({no})"


def rating_text(votes, avg):
    return f"{avg:.2f}" if votes and avg is not None else ""


def share(mask):
    return pct(float(np.mean(mask))) if len(mask) else "n/a"


def main():
    d = load()
    books = d["books"]
    genre_map = json.loads(fp.MAP_PATH.read_text(encoding="utf-8")) if fp.MAP_PATH.exists() else {"groups": {}}
    group_names = [g for g in genre_map["groups"] if g != "nonfiction"] or sorted({g for b in books for g in b["groups"]})
    styl_names = [f["name"] for f in d["meta"].get("stylometry_features") or []]
    cat = fp.open_catalog()
    if cat is None:
        sys.exit('data/catalog.db has no ratings table (librate). Run "npm run catalog" first.')
    cat.row_factory = None
    ix = connect(INDEX_PATH)
    votes, centred = load_votes(cat, ix, books)
    data = Data(books, group_names, styl_names, votes, centred)
    y = data.y
    liked = y == 1
    main_pos, main_neg = data.main_set & liked, data.main_set & ~liked
    if main_pos.sum() < FOLDS * 2 or main_neg.sum() < 30:
        sys.exit(f"Not enough books for the main test: {int(main_pos.sum())} liked and {int(main_neg.sum())} rated-matched books are standalone or first in a series.")

    # authors: every author of a liked book; my list (or, until it is filled, authors with 2 or more liked books)
    liked_count = Counter(a for f in d["liked_facts"] for a in jlist(f["author_ids"]))
    followed = set(d["followed"])
    list_filled = bool(followed)
    list_authors = followed if list_filled else {a for a, n in liked_count.items() if n >= MIN_LIKED_BEFORE_LIST}
    by_list_author = np.array([any(a in list_authors for a in b["author_ids"]) for b in books])

    print(f"Funnel: {int(main_pos.sum())} liked and {int(main_neg.sum())} rated-matched books in the main test. Testing the models ...", flush=True)
    res, oof, readers = cross_validate(data)
    best_m = max(MODELS, key=lambda m: res[("main", m)]["auc"])
    pairs = [(a, b, what, fp.paired(res, "main", a, b)) for a, b, what in PAIRS]
    score = oof[best_m]

    nums = catalog_numbers(ix, cat, d, set(liked_count), list_authors)
    has_reviews = nums["has_reviews"]

    valid = [a for a in d["all_labels"] if a["label"] in ("never", "maybe", "want") and a.get("status") is None]
    ruled = [a for a in d["all_labels"] if a.get("status") == "rule"]
    skipped = [a for a in d["all_labels"] if a["label"] == "skip"]
    new_answers = [a for a in valid if a.get("grp") in ("top", "random")]
    answered = {a["key"] for a in d["all_labels"]}
    queue = build_queue(data, score, best_m, answered)

    # Part E
    neg_idx = np.array([i for i, b in enumerate(books) if b["mark"] == "disliked" and y[i] == 0], dtype=int)
    pos_all = np.array([i for i, b in enumerate(books) if y[i] == 1 or b["mark"] == "liked"], dtype=int)
    text_res, pos_used, same_author = None, pos_all, False
    if len(neg_idx) >= MIN_DISLIKED:
        neg_groups = set(data.groups[neg_idx])
        same = np.array([i for i in pos_all if data.groups[i] in neg_groups], dtype=int)
        if len(same) >= MIN_DISLIKED:
            pos_used, same_author = same, True
        text_res = text_test(data, pos_used, neg_idx)

    out = []
    add = out.append
    add("# The selection funnel\n")
    add("Built by `model/funnel.py` (run everything with `npm run funnel`). Milestone 3c copies how I really choose books. "
        "**Decision 1**, before reading: should I try this book? (reader rating, reader comments, the series rule, authors I follow). "
        "**Decision 2**, while reading: do I like it? (style and plot of the first 100 pages). My liked books passed both.\n")

    # ---------------------------------------------------------------- the plain answers
    lf = d["liked_facts"]
    st = Counter(f["status"] for f in lf)
    n_liked_all = len(lf)
    later_all = st["continuation"] + st["later"]
    add("## The plain answers\n")
    if has_reviews:
        rv = nums["reviews"]
        lc = np.array([f["comments"] or 0 for f in lf])
        add(f"1. **Do reader comments exist? Yes.** The catalog has a dump `lib.reviews.sql.gz`: {rv['rows']:,} comments on {rv['books']:,} books, "
            f"each with the reader's name, the full text and a date and time ({str(rv['first'])[:10]} to {str(rv['last'])[:10]}). "
            f"{int((lc > 0).sum())} of my {n_liked_all} liked books have comments (median {np.median(lc):.0f} per book).\n")
    else:
        add("1. **Do reader comments exist? No.** The catalog offers no dump with reader comments, so every step that needs comments was skipped.\n")
    broke = st["later"]
    add(f"2. **Does my series rule hold in my own library? Mostly, not fully.** Of my {n_liked_all} liked books, {st['standalone']} are standalone, {st['first']} are number 1 of a series, "
        f"and {later_all} are a later book of a series. Of those {later_all} later books, {st['continuation']} have an earlier book of the same series among my liked books, "
        f"and **{broke} do not** ({pct(broke / max(1, n_liked_all))} of the library): there the library holds a later book without an earlier one. "
        f"For {st['unknown']} more the catalog gives no number (most are whole-series bundles). "
        "So the rule \"never start a series from a later book\" fits about nine liked books in ten; the exceptions are listed below.\n")
    beat = [(a, b, what, p) for a, b, what, p in pairs[:4] if p["mean"] > p["sd"]]
    lost = [(a, b, what, p) for a, b, what, p in pairs[:4] if not p["mean"] > p["sd"]]
    r_best, r_rb = res[("main", best_m)], res[("main", "rb")]
    line = (f"3. **Which signals beat the noise?** Tested on {int(main_pos.sum())} liked books against {int(main_neg.sum())} eligible rated-matched books "
            f"(standalone or first in a series, on both sides), adding one signal at a time: ")
    line += "; ".join(f"**{what.split(' on top')[0]}**: {'yes' if p['mean'] > p['sd'] else 'no'} ({p['mean']:+.3f} ± {p['sd']:.3f})" for a, b, what, p in pairs[:4]) + ". "
    if beat:
        line += "Beating the noise: " + ", ".join(what.split(" on top")[0] for _, _, what, _ in beat) + ". "
    if lost:
        line += "Not beating the noise: " + ", ".join(what.split(" on top")[0] for _, _, what, _ in lost) + ". "
    line += (f"Best model: **{best_m}**, ROC AUC {r_best['auc']:.3f} ± {r_best['auc_sd']:.3f} (rb alone: {r_rb['auc']:.3f} ± {r_rb['auc_sd']:.3f}). "
             "The \"author on my list\" signal cannot be tested this way, see below.\n")
    add(line)
    add("\"Beats the noise\" means: the average gain over the 50 test folds is bigger than the spread of that gain from fold to fold.\n")
    add("**Votes and comments on replaced copies.** When a book file is replaced by a newer copy, the site keeps showing the votes and comments of the old copy on the new one "
        "(checked on a live book page). From this milestone on, votes and comments are counted the same way, so some ratings here differ a little from `reports/fingerprints.md`.\n")

    # ---------------------------------------------------------------- Part A
    add("## Part A. Data checks\n")
    add("### Reader comments\n")
    if has_reviews:
        rv = nums["reviews"]
        add(f"The table `reviews` has the columns {', '.join('`' + c + '`' for c in rv['cols'])}: reader name, date and time, book number, text. "
            f"{rv['rows']:,} rows on {rv['books']:,} different books" + (f"; {rv['empty']:,} rows have an empty text and are ignored" if rv["empty"] else "") + ". **Comments have dates** (column `Time`), "
            "so the newest comments can be shown first.\n")
        rated_mask = data.rated
        piles = [("Liked books (all in the library)", np.array([f["comments"] or 0 for f in lf])),
                 ("Liked books used in the models", data.n_comments[liked]),
                 ("Rated-matched set (all)", data.n_comments[rated_mask]),
                 ("Rated-matched set, eligible only", data.n_comments[rated_mask & data.eligible]),
                 (f"Catalog fiction with at least {MIN_VOTES} votes", np.array(nums["cat_comments"]))]
        add(table(["Pile", "Books", "With comments", "Share", "Comments per book: lower quarter / median / upper quarter", "Same, only books that have comments"],
                  [[nm, len(v), int((v > 0).sum()), share(v > 0), fp.quart(v, "{:.0f}", (25, 50, 75)), fp.quart(v[v > 0], "{:.0f}", (25, 50, 75))] for nm, v in piles]))
        lv, rm = data.n_comments[liked], data.n_comments[rated_mask]
        add(f"So the typical liked book has {np.median(lv):.0f} comments and the typical rated-matched book has {np.median(rm):.0f}. "
            "My liked books are commented more, the same way they have more votes.\n")
    else:
        add("The mirror offers no dump with reader comments. Every step that needs comments is skipped: no comments on the cards, no comment features in the models.\n")

    add("### Series of my liked books\n")
    add("For every book the catalog index stores the series name, the number in the series, and how many books the series has in the catalog "
        "(story series by the author only; publisher series are ignored). A book is:\n\n"
        "- **standalone**: not in a series\n- **first**: number 1 of its series\n"
        "- **continuation**: a later book of a series where I already have a liked book with a lower number\n"
        "- **unknown**: in a series, but the catalog gives no number (eligible, but flagged)\n"
        "- **later**: a later book of a series where I have no earlier liked book (not eligible)\n")
    used_keys = {b["key"] for b in books if b["y"] == 1}
    st_used = Counter(f["status"] for f in lf if f["key"] in used_keys)
    add(table(["", "Standalone", "First in a series", "Later, with an earlier liked book (continuation)", "Later, without an earlier liked book", "In a series, number unknown", "Total"], [
        ["All liked books"] + [st[k] for k in ("standalone", "first", "continuation", "later", "unknown")] + [n_liked_all],
        ["share"] + [pct(st[k] / max(1, n_liked_all)) for k in ("standalone", "first", "continuation", "later", "unknown")] + [""],
        ["Liked books used in the models (no collections, no non-fiction)"] + [st_used[k] for k in ("standalone", "first", "continuation", "later", "unknown")] + [sum(st_used.values())],
    ]))
    add(f"Of the {later_all} liked books that are a later book of a series, **{st['continuation']} have an earlier book of the same series among my liked books and {st['later']} do not.**\n")
    title_of = {b["key"]: b for b in d["liked_all"]}
    later_rows = sorted((f for f in lf if f["status"] == "later"), key=lambda f: (f["series"] or "", f["series_no"] or 0))
    if later_rows:
        add("The liked books that break the rule (a later book, no earlier book of the series in my library):\n")
        add(table(["Author", "Title", "Series", "Number", "Books in the series"],
                  [[", ".join(jlist(title_of[f["key"]]["authors"])), title_of[f["key"]]["title"], f["series"], f["series_no"], f["series_total"]] for f in later_rows]))
        add("Some of these are loose series where every book is a separate story (detective series, shared worlds), and for some I may have read the earlier books elsewhere. "
            "The library cannot tell these apart.\n")

    add("### Series length: liked books against the rated-matched set\n")
    def series_row(name, mask):
        tot = data.series_total[mask]
        ins = tot > 0
        stt = data.status[mask]
        return [name, int(mask.sum()), share(ins), fp.quart(tot[ins], "{:.0f}", (25, 50, 75)), share(tot[ins] > 5), share(tot[ins] > 10),
                share(np.isin(stt, ("later", "continuation")))]
    add(table(["Pile", "Books", "In a series", "Books in the series: lower quarter / median / upper quarter", "Series longer than 5 books", "Series longer than 10 books", "A later book of its series"], [
        series_row("Liked (used in the models)", liked),
        series_row("Rated-matched set (all)", data.rated),
        series_row("Liked: first in a series only", liked & (data.status == "first")),
        series_row("Rated-matched: first in a series only", data.rated & (data.status == "first")),
    ]))
    lt, rt = data.series_total[liked & (data.status == "first")], data.series_total[data.rated & (data.status == "first")]
    long_l, long_r = float(np.mean(lt > 10)) if len(lt) else 0.0, float(np.mean(rt > 10)) if len(rt) else 0.0
    se_long = math.sqrt(long_l * (1 - long_l) / max(1, len(lt)) + long_r * (1 - long_r) / max(1, len(rt)))
    if long_l < long_r - 2 * se_long:
        verdict = "So my liked books do start long series less often: the data agrees that I am suspicious of long series."
    elif long_l > long_r + 2 * se_long:
        verdict = "So my library does **not** show that I avoid long series: I started long series more often than the comparison set does."
    else:
        verdict = "So my library does **not** show that I avoid long series: the two piles are about the same."
    add("The two \"first in a series only\" rows are the fair comparison for \"am I suspicious of long series?\": both sides are number 1 of their series. "
        f"There the median series has {np.median(lt):.0f} books for my liked books and {np.median(rt):.0f} for the rated-matched set; "
        f"series longer than 5 books: {share(lt > 5)} against {share(rt > 5)}; longer than 10 books: {share(lt > 10)} against {share(rt > 10)}. {verdict} "
        "(The series length is what the catalog holds today; a series may have been shorter when I started it.)\n")
    add(f"The rated-matched set was drawn without looking at series, and {share(np.isin(data.status[data.rated], ('later',)))} of it are later books of series I never started. "
        "Under my rule those were never real candidates, which is why they are left out from now on.\n")

    # ---------------------------------------------------------------- Part B / F19
    add("## The rules, applied to the whole catalog\n")
    add("How many catalog books pass each rule in turn. Different editions of one book are counted separately here.\n")
    add(table(["Rule", "Books left"], [[n, f"{v:,}"] for n, v in nums["funnel"]]))
    bs = nums["by_status"]
    total_before = sum(bs.values())
    add(f"The series rule, on the {total_before:,} books that pass every rule before it: "
        + ", ".join(f"{bs.get(k, 0):,} {k}" for k in ("standalone", "first", "continuation", "unknown", "later"))
        + f". So the rule removes {bs.get('later', 0):,} books ({pct(bs.get('later', 0) / max(1, total_before))}). "
        f"Of the books that pass everything, {nums['above4']:,} have an average above 4 (the ones I prefer).\n")
    add("### The same rules on the downloaded books\n")
    pool = (~liked) & (data.source != "extra")
    ok_votes = data.votes >= MIN_VOTES
    ok_avg = np.nan_to_num(data.avg) >= MIN_AVG
    add(table(["Set", "Books", f"At least {MIN_VOTES} votes and average {MIN_AVG:g} or more", "... and eligible", "... of those: standalone / first / continuation / number unknown"], [
        [nm, int(m.sum()), int((m & ok_votes & ok_avg).sum()), int((m & ok_votes & ok_avg & data.eligible).sum()),
         " / ".join(str(int((m & ok_votes & ok_avg & (data.status == s)).sum())) for s in ELIGIBLE)]
        for nm, m in (("Rated-matched", data.rated), ("Old matched (not in rated-matched)", pool & (data.source == "matched")), ("Feed", pool & data.is_feed), ("All downloaded books", pool))]))
    add("Only eligible books are used in the comparison set, in the lists and in the labelling queue.\n")
    add("### My old answers on books that are not eligible\n")
    rc = Counter(a["label"] for a in ruled)
    vc = Counter(a["label"] for a in valid)
    add(f"Of my {len(valid) + len(ruled)} answers, **{len(ruled)} are on books my series rule would never have shown me** "
        f"({rc.get('never', 0)} would never read, {rc.get('maybe', 0)} maybe, {rc.get('want', 0)} want to read). They are marked \"rule\" and left out of every taste metric. "
        f"{len(valid)} answers remain ({vc.get('never', 0)} would never read, {vc.get('maybe', 0)} maybe, {vc.get('want', 0)} want to read)"
        + (f"; {len(skipped)} \"cannot decide\" answers are not used anywhere" if skipped else "") + ".\n")

    # ---------------------------------------------------------------- Part D
    add("## Part D. Signals for Decision 1\n")
    add(f"**The main test.** Positive = my liked books that are standalone or first in a series ({int(main_pos.sum())} books). "
        f"Comparison = rated-matched books that are standalone or first in a series ({int(main_neg.sum())} books). "
        "Continuations and books with an unknown number are left out on both sides: by my rule a continuation is decided by the earlier book, not by these signals, "
        "and an unknown number would give the model a shortcut. "
        f"Same test as before: author-grouped, {FOLDS} folds, {REPEATS} repeats ({FOLDS * REPEATS} test folds); an author is never on both sides; "
        "every scale, PCA direction, penalty strength, list of like-minded readers and set of nearest liked books comes from the training part only.\n")
    add("The signals:\n")
    add("- **rb**: average rating, number of votes, genre groups, length, translated, in a series (the model from milestone 3b)\n"
        "- **series length**: the number of books in the series (log), 0 for a standalone book. \"First in a series or standalone\" is already the rb flag \"in a series\", "
        "because the main test only has standalone books and first books. \"Continuation\" cannot be a feature here: all continuations are left out, see below.\n"
        "- **like-minded readers**: readers who gave 4 or 5 to at least 3 of my liked books: how many of them voted for the book, their share, and how much higher they rated it\n"
        f"- **item-to-item**: every reader's marks are centred on that reader's own average; two books are similar when the same readers rate both above (or both below) their average "
        f"(cosine similarity). The score of a book is its similarity to the {I2I_NEAREST} nearest liked books (average, and the best one). "
        "Only liked books of the training part are used, and never books by the same author\n"
        + (f"- **reader comments**: the number of comments (log), and the embedding of the comment texts (same model as the books, bge-m3; the newest 30 comments of a book, averaged; "
           f"{COMMENT_DIMS} PCA directions learned inside each training fold)\n" if has_reviews else "- **reader comments**: not available\n"))
    has_never = any("never" in res[("main", m)] for m in MODELS)
    head = ["Model", "**ROC AUC**", "Median percentile of a liked book", "Liked books in the top 5%"] + (["AUC liked vs \"would never read\""] if has_never else [])
    rows_ = []
    for m in CHAIN + [k for k in MODELS if k not in CHAIN]:
        if "c" in MODELS[m][1] and not has_reviews:
            continue
        r = res[("main", m)]
        rows_.append([MODELS[m][0], f"**{fp.pm(r, 'auc')}**", fp.pm(r, "median", True), fp.pm(r, "top", True)]
                     + ([f"{r['never']:.3f} ± {r['never_sd']:.3f}" if "never" in r else ""] if has_never else []))
    add(table(head, rows_))
    add("ROC AUC: 0.5 is coin-flipping, 1.0 is perfect. Median percentile: where the typical liked test book sits in the ranking of the comparison test books (lower is better, 50% is chance). "
        "Top 5%: the share of liked test books that score above 95% of the comparison books (chance gives 5%)."
        + (f" The last column separates the liked books from the {int(data.never.sum())} eligible books I marked \"would never read\"; with so few books it is rough." if has_never else "") + "\n")
    add("### Fold by fold: which additions are real?\n")
    add("Each row compares two models on the same 50 folds, which removes most of the fold-to-fold noise.\n")
    add(table(["Comparison", "What it tests", "AUC gain", "Beats the noise?"],
              [[f"{a} against {b}", what, p["text"], p["verdict"]] for a, b, what, p in pairs if has_reviews or "c" not in MODELS[a][1]]))
    for a, b, what, p in pairs[:4]:
        if "c" in MODELS[a][1] and not has_reviews:
            continue
        word = "**beats the noise**" if p["mean"] > p["sd"] else ("**is worse by more than the noise**" if p["mean"] < -p["sd"] else "**does not beat the noise**")
        add(f"- {what[0].upper() + what[1:]}: {word} ({p['text']}).")
    add("")
    add(f"There are {readers.mean():.0f} like-minded readers on average inside a training part (from {readers.min()} to {readers.max()}).\n")
    add("### Author on my list (step 13)\n")
    n_followed_liked = int(sum(1 for b in books if b["y"] == 1 and any(a in followed for a in b["author_ids"])))
    add((f"My list has **{len(followed)} authors**; {n_followed_liked} of my {int(liked.sum())} liked books are by them. " if list_filled else
         "**My list of followed authors is still empty** (tab \"My authors\" of the labelling page). ")
        + "This signal is stored for every book and used in the short list and in the test of Decision 1 below, but it is **not** in the table above, on purpose: "
        "the list is made from my liked books, so every book by a listed author in the test would be a liked one, and the test is author-grouped, "
        "so a model never sees the same author on both sides. Any number for it there would be meaningless. "
        "Its real test is my answers in the \"New books\" tab, reported separately for listed and unknown authors.\n")
    add("### Continuations, reported separately\n")
    cont = liked & (data.status == "continuation")
    add(f"{int(cont.sum())} of the liked books used in the models are continuations, and {int((data.rated & (data.status == 'continuation')).sum())} rated-matched books are "
        "(unread later books of series I liked). They are in no model. "
        f"For reference, the best model ({best_m}) gives the liked continuations a median score of {np.median(score[cont]) if cont.any() else float('nan'):.2f}, "
        f"against {np.median(score[main_pos]):.2f} for the liked books of the main test and {np.median(score[main_neg]):.2f} for the comparison books; "
        "these scores come from models that never saw the author.\n")
    add(f"In the catalog, {nums['continuations_all']:,} fiction books are continuations of series I liked and are not in my library (editions counted separately). "
        "The next book of each series I have started (my highest liked number + 1), when the catalog has it:\n")
    nx = nums["next_in_series"]
    if nx:
        add(table(["Rating", "Votes", "Id", "Author", "Title", "Series"],
                  [[rating_text(r["votes"], r["avg"]), r["votes"], r["id"], r["authors"], r["title"], series_text(r)] for r in nx[:25]]))
        if len(nx) > 25:
            add(f"({len(nx) - 25} more are not shown.)\n")
    else:
        add("_none_\n")

    # ---------------------------------------------------------------- Part E
    add("## Part E. Text test for Decision 2\n")
    add(f"Every book now has a second fingerprint made from its opening only: the first {d['meta'].get('opening_words', 25000):,} words (about 100 pages), "
        "with the same stylometry numbers and the same embedding method as the whole book (the same number of chunks, spread through the opening). "
        + (f"{d['missing_open']} books have no opening vectors yet (run `npm run funnel` again). " if d["missing_open"] else "Every book has both fingerprints. ")
        + f"Opening and whole-book vectors of the same book have a cosine similarity of {float(np.mean((data.E * data.EO).sum(axis=1)[data.has_open])):.3f} on average.\n")
    n_dis = len(neg_idx)
    waiting = sum(1 for m in d["marks"].values() if m["mark"] == "disliked") - n_dis
    if text_res is None:
        add(f"> **Skipped: {n_dis} books are marked \"read it, did not like it\" and have text; the test needs at least {MIN_DISLIKED}.** "
            + (f"{waiting} more are marked but not downloaded yet (run `npm run funnel`). " if waiting > 0 else "")
            + "Mark books in the tabs \"Books by my authors\" (key 1) and \"Add a book\" of the labelling page, then run `npm run funnel` again: "
            "it downloads them politely, fingerprints them and runs this test.\n")
        add("What the test will do: liked books against books I read and did not like, with liked books by the same authors where there are enough of them; "
            "four models (ratings and genres only; whole-book text only; opening text only; ratings and genres plus the better text version); "
            "author-grouped folds, so no book and no author is in both the training part and the test part.\n")
    else:
        add(f"**{text_res['n_neg']} books I read and did not like against {text_res['n_pos']} liked books** "
            + ("(only liked books by the same authors as the disliked ones). " if same_author else "(all liked books: there are too few liked books by the same authors). ")
            + f"Author-grouped test, {text_res['folds']} folds, {REPEATS} repeats: no book and no author is in both the training part and the test part. "
            f"Text = stylometry + {TEXT_DIMS} PCA directions of the embeddings.\n")
        add(table(["Model", "ROC AUC", "Same author: liked book scored above the disliked one"],
                  [[r["name"], f"{r['auc']:.3f} ± {r['sd']:.3f}", f"{pct(r['pairs'])} of about {r['n_pairs']} pairs" if r["pairs"] is not None else "no such pairs"] for r in text_res["rows"]]))
        by = {r["m"]: r for r in text_res["rows"]}
        bt = by[text_res["better"]]
        combo = by["rb+" + text_res["better"]]
        diff = combo["aucs"] - by["rb"]["aucs"]
        ow = by["open"]["aucs"] - by["whole"]["aucs"]
        se = math.sqrt(0.25 / max(1, min(text_res["n_neg"], text_res["n_pos"])))
        add(f"The better text version is the **{text_res['names'][text_res['better']]}** ({bt['auc']:.3f}). Opening against whole book, fold by fold: {ow.mean():+.3f} ± {ow.std():.3f}. "
            f"Adding it to ratings and genres changes the AUC by {diff.mean():+.3f} ± {diff.std():.3f} "
            f"({'more' if diff.mean() > diff.std() else 'not more'} than the noise).\n")
        add(f"**How reliable is this?** Not very. With {text_res['n_neg']} disliked books an AUC is only known to about ±{se:.2f}, "
            "so two models must differ by roughly twice that before the difference means anything. The better text version was also picked on the same test, which flatters it a little. "
            "Read the table as a first hint; it becomes useful at around 100 disliked books.\n")

    # ---------------------------------------------------------------- Part F: short list
    add("## The short list\n")
    add("### By my authors\n")
    ab = nums["author_books"]
    add(("Authors on my list. " if list_filled else
         f"**My list of authors is still empty, so this uses every author with at least {MIN_LIKED_BEFORE_LIST} liked books** ({len(list_authors)} authors). "
         "Fill the list in the tab \"My authors\" to make it mine. ")
        + f"Eligible fiction books by these authors that are not in my library and not marked as read, newest first. One edition per title; books with at least {MIN_VOTES} votes "
        f"and an average below {MIN_AVG:g} are left out. {len(ab)} books in all, the newest {min(LIST_AUTHORS, len(ab))} are shown.\n")
    if ab:
        add(table(["Year", "Rating", "Votes", "Id", "Author", "Title", "Series"],
                  [[r["year"] or str(r["uploaded"] or "")[:4], rating_text(r["votes"], r["avg"]), r["votes"], r["id"], r["authors"], r["title"], series_text(r)] for r in ab[:LIST_AUTHORS]]))
    else:
        add("_none_\n")
    add("### New for me\n")
    order = [i for i in np.argsort(-score) if not liked[i] and books[i]["source"] != "extra" and data.eligible[i] and ok_votes[i] and ok_avg[i]
             and not by_list_author[i] and books[i]["key"] not in answered and books[i]["mark"] not in ("disliked", "liked")]
    add(f"The {LIST_NEW} best eligible books by other authors that I have not labelled, by the best model (**{best_m}**). "
        f"Candidates: downloaded books with at least {MIN_VOTES} votes and an average of {MIN_AVG:g} or more ({len(order)} books). "
        "Each score comes from models that never saw the book or its author.\n")
    add(table(["Score", "Rating", "Votes", "Comments", "Key", "Author", "Title", "Series", "Genre groups"],
              [[f"{score[i]:.2f}", rating_text(data.votes[i], data.avg[i]), int(data.votes[i]), int(data.n_comments[i]), books[i]["key"], books[i]["author_names"],
                books[i]["title"], series_text(books[i]["facts"]), ", ".join(books[i]["groups"])] for i in order[:LIST_NEW]]))

    # ---------------------------------------------------------------- Part F: the real test
    add("## The real test of Decision 1: my answers in the \"New books\" tab\n")
    add(f"The queue of the tab is rebuilt by every run of this report: {queue['half']} books the best model scores highest and {queue['half']} books picked at random "
        f"from the {queue['candidates']} eligible candidates, mixed together; the page does not show which is which, and every answer stores the half the book came from. "
        + (f"Average score: {queue['top_mean']:.2f} in the top half, {queue['rnd_mean']:.2f} in the random half.\n" if queue["top_mean"] is not None else "\n"))
    if len(new_answers) < MIN_NEW_ANSWERS:
        add(f"> **Skipped: {len(new_answers)} new answers so far; the comparison needs at least {MIN_NEW_ANSWERS}.** Answer books with `npm run label`, then run `npm run funnel` again. "
            "The earlier answers were given before the queue had two halves, so they cannot be used here.\n")
    else:
        dt = decision_test(new_answers)
        add(f"{len(new_answers)} answers. \"Maybe or want\" against \"would never read\", for books the model scored high and for books picked at random:\n")
        add(table(["Authors", "Books", "Answers", "Maybe or want", "... of which want", "Would never read", "Share maybe or want"],
                  [[r["who"], r["name"], r["n"], r["yes"], r["want"], r["never"], f"{pct(r['share'])} ± {100 * r['se']:.0f}" if r["n"] else ""] for r in dt]))
        for who in ("unknown authors", "authors on my list", "all"):
            t_, r_ = [r for r in dt if r["who"] == who and r["grp"] == "top"][0], [r for r in dt if r["who"] == who and r["grp"] == "random"][0]
            if t_["n"] >= 10 and r_["n"] >= 10:
                diff, se = t_["share"] - r_["share"], math.sqrt(t_["se"] ** 2 + r_["se"] ** 2)
                add(f"- **{who[0].upper() + who[1:]}**: {pct(t_['share'])} for high-scored books against {pct(r_['share'])} for random books, a difference of {100 * diff:+.0f} points "
                    f"(± {100 * se:.0f}). {'The model helps.' if diff > 2 * se else 'Not a clear difference yet.' if diff > -2 * se else 'The model is worse than random.'}")
            else:
                add(f"- **{who[0].upper() + who[1:]}**: too few answers to compare ({t_['n']} high-scored, {r_['n']} random).")
        add("")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(out).replace("\n\n\n", "\n\n").rstrip() + "\n", encoding="utf-8")
    cat.close()
    ix.close()

    for m in MODELS:
        r = res[("main", m)]
        print(f"  {m:15s} AUC {r['auc']:.3f} ± {r['auc_sd']:.3f}   median percentile {100 * r['median']:.1f}%   top 5%: {100 * r['top']:.1f}%")
    for a, b, what, p in pairs:
        print(f"  {a} against {b}: {p['text']} - {p['verdict']}")
    print(f"  best model: {best_m}; queue: {queue['half']} top + {queue['half']} random of {queue['candidates']} candidates")
    print(f"Report written to {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
