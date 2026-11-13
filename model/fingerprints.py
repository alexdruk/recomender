"""Milestones 3 and 3b. Text fingerprints and ratings: checks, models, taste map, report.

Run:  .venv/bin/python model/fingerprints.py        writes reports/fingerprints.md

Needs, in data/books.db, data/catalog.db and data/emb/ (made by the earlier steps):
  node src/fetch-books.js      liked FB2 texts, the matched and rated-matched sets
  node src/stylometry.js       the "stylometry" table
  .venv/bin/python model/embed.py   chunk vectors for every book
"npm run fingerprints" runs these (they skip what is already done) and then this file.

Positive class = liked books (no collections, no non-fiction).
Two comparison sets, each used on its own as the unlabeled class:
  rated-matched  about 1,000 catalog books with at least 5 votes, whose years and
                 average ratings are spread like those of the liked books. The one that counts.
  matched        the old, easier set: random catalog books matched on year only.
Feed books are never trained on; they are only scored.
"Year" is not used by any model here.
"""

import json
import math
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from joblib import Parallel, delayed
from scipy import sparse
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, silhouette_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
from baseline import author_groups, jlist, norm_author, pct, table  # noqa: E402
from embed import epub_check_keys  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / CONFIG.get("dataDir", "data")
DB_PATH = DATA / "books.db"
CAT_PATH = DATA / "catalog.db"
MAP_PATH = DATA / "genre-map.json"
EMB = DATA / "emb"
REPORT = ROOT / "reports" / "fingerprints.md"

FOLDS = 5
REPEATS = 10
SEED = 20261112
PCA_DIMS = 50
C_GRID = (0.01, 0.1, 1.0)  # strength of the model's "keep it simple" penalty, chosen inside each training part
TOP_SHARE = 0.05
MIN_LABELS = 100
NEVER_WEIGHT = 3.0  # "would never read" books count this many times as a negative
STAR_WEIGHT = 2.0
PILE_LIMIT = 0.75
MIN_SET = 800  # a comparison set should have at least this many books
MIN_VOTES = 5  # an average with at least this many votes is trusted
LM_MIN_BOOKS = 3  # a like-minded reader gave 4 or 5 to at least this many of my liked books
LM_SHRINK = 3.0  # a few like-minded votes are pulled towards the book's overall average
LIST_SIZE = 30
SETS = {"rated": "rated-matched", "matched": "old matched"}


# ---------------------------------------------------------------- loading


def book_vector(set_name, key):
    path = EMB / set_name / f"{key}.npy"
    if not path.exists():
        return None
    v = np.load(path).astype(np.float32).mean(axis=0)
    return v / (np.linalg.norm(v) or 1.0)


def book_id(key):
    head = str(key).split("-")[0]
    return int(head) if head.isdigit() else None


def load():
    if not DB_PATH.exists():
        sys.exit("data/books.db not found. Run the Node steps first.")
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row

    def rows(sql, args=()):
        return [dict(r) for r in db.execute(sql, args)]

    def has(name):
        return db.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone() is not None

    if not has("unlabeled_books") or not has("rated_pick"):
        sys.exit('No "unlabeled_books" view or "rated_pick" table yet. Run "node src/fetch-books.js" first.')
    meta = {r["name"]: json.loads(r["value"]) for r in db.execute("SELECT name, value FROM meta")}
    liked_all = rows("SELECT * FROM books WHERE parse_status != 'failed'")
    unl_all = rows("SELECT * FROM unlabeled_books")
    fb2 = {r["key"]: r for r in rows("SELECT key, words, annotation FROM parsed WHERE source = 'liked_fb2' AND parse_status != 'failed'")}
    fetch = rows("SELECT * FROM fb2_fetch") if has("fb2_fetch") else []
    styl = {(r["kind"], r["key"]): json.loads(r["features"]) for r in rows("SELECT * FROM stylometry WHERE features IS NOT NULL")} if has("stylometry") else {}
    labels = {r["key"]: r["label"] for r in rows("SELECT key, label FROM labels")} if has("labels") else {}
    stars = {r["key"] for r in rows("SELECT key FROM stars")} if has("stars") else set()
    counts = {
        "liked_all": len(liked_all),
        "liked_collections": sum(1 for b in liked_all if b["is_collection"]),
        "liked_nonfiction": sum(1 for b in liked_all if not b["is_collection"] and not b["is_fiction"]),
        "picked": db.execute("SELECT COUNT(*) FROM matched_pick").fetchone()[0] if has("matched_pick") else 0,
        "matched_parsed": db.execute("SELECT COUNT(*) FROM parsed WHERE source = 'matched' AND parse_status != 'failed'").fetchone()[0],
        "rated_picked": db.execute("SELECT COUNT(*) FROM rated_pick").fetchone()[0],
        "rated_reused": db.execute("SELECT COUNT(*) FROM rated_pick WHERE reused = 1").fetchone()[0],
        "rated_parsed": db.execute("SELECT COUNT(*) FROM parsed WHERE source = 'rated' AND parse_status != 'failed'").fetchone()[0],
    }
    db.close()

    text_dir = DATA / "text-fb2"
    liked_keys_all = [b["key"] for b in liked_all]
    has_fb2 = {k for k in liked_keys_all if k in fb2 and (text_dir / f"{k}.txt").exists()}
    counts["liked_fb2"] = len(has_fb2)

    books, no_vector = [], Counter()
    for label, group in ((1, liked_all), (0, unl_all)):
        for b in group:
            if label == 1 and (b["is_collection"] or not b["is_fiction"]):
                continue
            key = b["key"]
            if label == 1:
                source, store = "liked", "liked"
                use_fb2 = key in has_fb2
                vec = book_vector("liked_fb2", key) if use_fb2 else book_vector("liked_epub", key)
                words = fb2[key]["words"] if use_fb2 else b["words"]
            else:
                source, store = b["source"], b["store"]
                vec = book_vector(store, key)
                words = b["words"]
            if vec is None:
                no_vector[source] += 1
                continue
            ids, names = jlist(b["cat_author_ids"]), jlist(b["authors"])
            authors = [f"id{i}" for i in ids] if ids else [f"n:{norm_author(n)}" for n in names if n.strip()]
            in_catalog = b["cat_author_ids"] is not None or b["cat_genres"] is not None
            year = b["cat_year"] if b["cat_year"] else b["year"]
            books.append({
                "key": key, "y": label, "source": source, "store": store,
                "title": b["title"] or "", "author_names": ", ".join(names),
                "authors": authors or [f"solo:{label}:{key}"],
                "genres": jlist(b["cat_genres"]) or jlist(b["genres"]),
                "groups": jlist(b["genre_groups"]),
                "words": words or 0,
                "translated": int(b["cat_translated"] if b["cat_translated"] is not None else (b["is_translation"] or 0)),
                "in_series": int(bool(jlist(b["cat_series"])) if in_catalog else bool(b["series"])),
                "year": int(year) if year and 1800 <= int(year) <= 2100 else None,
                "vec": vec,
                "styl": styl.get(("liked" if label == 1 else "unlabeled", key)),
                "label": labels.get(key) if label == 0 else None,
                "star": key in stars,
            })
    return {"books": books, "counts": counts, "meta": meta, "fetch": fetch, "no_vector": no_vector, "labels": labels,
            "stars": stars, "liked_all": liked_all, "has_fb2": has_fb2, "liked_keys_all": liked_keys_all}


# ---------------------------------------------------------------- ratings from the catalog


def open_catalog():
    if not CAT_PATH.exists():
        return None
    cat = sqlite3.connect(f"file:{CAT_PATH}?mode=ro", uri=True)
    if not cat.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'librate'").fetchone():
        cat.close()
        return None
    return cat


def load_votes(cat, books):
    """Every vote on our books, as three book-by-reader tables:
    V = the rating (1 to 5), P = 1 where the reader voted, H = 1 where the reader gave 4 or 5."""
    n = len(books)
    row_of = {}
    for i, b in enumerate(books):
        bid = book_id(b["key"])
        if bid is not None:
            row_of.setdefault(bid, []).append(i)
    ids = sorted(row_of)
    r_idx, users, rates = [], [], []
    if cat is not None:
        for start in range(0, len(ids), 900):
            part = ids[start:start + 900]
            sql = f"SELECT BookId, UserId, MAX(CAST(Rate AS INTEGER)) FROM librate WHERE BookId IN ({','.join('?' * len(part))}) GROUP BY BookId, UserId"
            for bid, user, rate in cat.execute(sql, part):
                for i in row_of[bid]:
                    r_idx.append(i)
                    users.append(user)
                    rates.append(rate)
    uniq, cols = np.unique(np.array(users, dtype=np.int64), return_inverse=True) if users else (np.array([]), np.array([], dtype=int))
    shape = (n, max(1, len(uniq)))
    rates = np.array(rates, dtype=float)
    V = sparse.csr_matrix((rates, (r_idx, cols)), shape=shape)
    P = sparse.csr_matrix((np.ones(len(rates)), (r_idx, cols)), shape=shape)
    H = sparse.csr_matrix(((rates >= 4).astype(float), (r_idx, cols)), shape=shape)
    return V, P, H


def catalog_facts(cat, genre_map):
    """What the ratings table holds, and votes and average for every
    Russian-language fiction book of the catalog that is not deleted."""
    cols = [r[1] for r in cat.execute("PRAGMA table_info(librate)")]
    rows_, readers, rated_books = cat.execute("SELECT COUNT(*), COUNT(DISTINCT UserId), COUNT(DISTINCT BookId) FROM librate").fetchone()
    pairs = cat.execute("SELECT COUNT(*) FROM (SELECT 1 FROM librate GROUP BY BookId, UserId)").fetchone()[0]
    values = dict(cat.execute("SELECT CAST(Rate AS INTEGER), COUNT(*) FROM librate GROUP BY 1"))
    kind_of = {code: genre_map["groups"].get(group) for code, group in genre_map.get("codes", {}).items()}
    kinds = {}
    sql = ("SELECT b.BookId, l.GenreCode FROM libbook b JOIN libgenre g ON g.BookId = b.BookId "
           "JOIN libgenrelist l ON l.GenreId = g.GenreId WHERE b.Lang = 'ru' AND b.Deleted = '0'")
    for bid, code in cat.execute(sql):
        kinds.setdefault(bid, set()).add(kind_of.get(code))
    fiction = [bid for bid, k in kinds.items() if "fiction" in k and "nonfiction" not in k]
    agg = {bid: (n, avg) for bid, n, avg in cat.execute("SELECT BookId, COUNT(*), AVG(CAST(Rate AS REAL)) FROM librate GROUP BY BookId")}
    n = np.array([agg.get(bid, (0, 0.0))[0] for bid in fiction], dtype=float)
    avg = np.array([agg.get(bid, (0, np.nan))[1] for bid in fiction], dtype=float)
    date_cols = [c for c in cols if any(w in c.lower() for w in ("time", "date", "created", "stamp"))]
    return {"cols": cols, "rows": rows_, "readers": readers, "rated_books": rated_books, "pairs": pairs, "values": values,
            "date_cols": date_cols, "fiction_n": n, "fiction_avg": avg}


# ---------------------------------------------------------------- features and models


class Data:
    def __init__(self, books, group_names, styl_names, votes):
        self.books = books
        n = len(books)
        col = {g: i for i, g in enumerate(group_names)}
        self.G = np.zeros((n, len(group_names)))
        for i, b in enumerate(books):
            for g in b["groups"]:
                if g in col:
                    self.G[i, col[g]] = 1.0
        self.y = np.array([b["y"] for b in books])
        self.source = np.array([b["source"] for b in books])
        self.store = np.array([b["store"] for b in books])
        self.is_feed = self.source == "feed"
        self.sets = {"rated": (self.y == 1) | (self.source == "rated-matched"), "matched": (self.y == 1) | (self.store == "matched")}
        self.log_words = np.array([math.log10(max(b["words"], 100)) for b in books])
        self.flags = np.array([[b["translated"], b["in_series"]] for b in books], dtype=float)
        self.S = np.array([[(b["styl"] or {}).get(nm) if (b["styl"] or {}).get(nm) is not None else np.nan for nm in styl_names] for b in books], dtype=float)
        self.E = np.vstack([b["vec"] for b in books])
        self.groups = author_groups(books)
        self.never = np.array([b["label"] == "never" for b in books])
        self.want = np.array([b["label"] == "want" for b in books])
        self.star = np.array([b["star"] and b["y"] == 1 for b in books])
        self.weights = np.ones(n)
        self.weights[self.never] = NEVER_WEIGHT
        self.weights[self.star] = STAR_WEIGHT
        # ratings
        self.V, self.P, self.H = votes
        self.votes = np.asarray(self.P.sum(axis=1)).ravel()
        total = np.asarray(self.V.sum(axis=1)).ravel()
        self.avg = np.where(self.votes > 0, total / np.maximum(self.votes, 1), np.nan)
        self.rated_ok = (self.votes >= MIN_VOTES) & (np.nan_to_num(self.avg) >= 3)
        self.group_rows = {}
        for i, g in enumerate(self.groups):
            self.group_rows.setdefault(g, []).append(i)

    def like_minded(self, liked_train):
        """The like-minded readers score, from the liked books of the training part only.

        A like-minded reader gave 4 or 5 to at least LM_MIN_BOOKS of those books.
        For every book: how many like-minded readers voted for it, their share
        among all its voters, and how much higher they rated it than all readers did.
        A training book never counts towards its own like-minded readers, and neither
        do other books by its author: test books always come from unseen authors,
        so training books must be looked at in the same way."""
        counts = np.asarray(self.H[liked_train].sum(axis=0)).ravel()
        readers = (counts >= LM_MIN_BOOKS).astype(float)
        n_lm = self.P @ readers
        sum_lm = self.V @ readers
        g_liked = self.groups[liked_train]
        for g in np.unique(g_liked):
            own = np.asarray(self.H[liked_train[g_liked == g]].sum(axis=0)).ravel()
            without = ((counts - own) >= LM_MIN_BOOKS).astype(float)
            rows = self.group_rows[g]
            n_lm[rows] = self.P[rows] @ without
            sum_lm[rows] = self.V[rows] @ without
        avg = np.nan_to_num(self.avg)
        diff = (sum_lm - n_lm * avg) / (n_lm + LM_SHRINK)
        share = n_lm / (self.votes + MIN_VOTES)
        return np.column_stack([np.log1p(n_lm), share, diff]), int(readers.sum())

    def blocks(self, train, lm):
        """The feature blocks for all books, with every scale, median and
        PCA direction learned from the training rows only."""
        fit = lambda X: np.clip(StandardScaler().fit(X[train]).transform(X), -5, 5)  # noqa: E731
        words = StandardScaler().fit(self.log_words[train, None]).transform(self.log_words[:, None])
        b0 = np.hstack([self.G, words, self.flags])
        med = np.nanmedian(self.S[train], axis=0)
        med = np.where(np.isnan(med), 0.0, med)
        S = fit(np.where(np.isnan(self.S), med, self.S))
        dims = min(PCA_DIMS, len(train) - 1)
        pca = PCA(n_components=dims, random_state=SEED).fit(self.E[train])
        E = pca.transform(self.E)
        E = E / E[train].std(axis=0).mean()  # one common scale, so the big directions stay the big ones
        # ratings: average (a book without votes gets the typical average and a "no votes" mark), number of votes
        seen = self.avg[train][~np.isnan(self.avg[train])]
        typical = float(np.median(seen)) if len(seen) else 3.0
        R = np.column_stack([fit(np.where(np.isnan(self.avg), typical, self.avg)[:, None]), fit(np.log1p(self.votes)[:, None]), (self.votes == 0).astype(float)])
        return {"b0": b0, "s": S, "e": E, "r": R, "lm": fit(lm)}


MODELS = {
    "r": ("r: average rating + number of votes", ["r"]),
    "rb": ("rb: r + genres, length, translated, series", ["r", "b0"]),
    "rb+text": ("rb+text: rb + stylometry + embeddings", ["r", "b0", "s", "e"]),
    "lm": ("lm: like-minded readers score alone", ["lm"]),
    "lm+rb": ("lm+rb: like-minded readers + rb", ["lm", "r", "b0"]),
    "lm+rb+text": ("lm+rb+text: like-minded readers + rb+text", ["lm", "r", "b0", "s", "e"]),
    "b0": ("b0: genres + length, translated, series (no ratings)", ["b0"]),
    "s": ("s: b0 + stylometry", ["b0", "s"]),
    "e": ("e: embeddings only", ["e"]),
    "be": ("be: b0 + embeddings", ["b0", "e"]),
    "all": ("all: b0 + stylometry + embeddings", ["b0", "s", "e"]),
}
NO_RATINGS = ("b0", "s", "e", "be", "all")
LM_MODELS = ("lm", "lm+rb", "lm+rb+text")
LABEL_MODEL = "all+labels"


def model_name(m):
    return MODELS[m][0] if m in MODELS else "all+labels: all, with my answers"


def fit_lr(X, y, C, w=None):
    return LogisticRegression(C=C, class_weight="balanced", max_iter=3000).fit(X, y, sample_weight=w)


def fit_best(X, y, groups, seed, w=None):
    """Chooses C on an inner author-grouped split of the training part, then fits on all of it."""
    inner = list(StratifiedGroupKFold(3, shuffle=True, random_state=seed).split(X, y, groups))
    best, best_auc = C_GRID[0], -1.0
    for C in C_GRID:
        aucs = []
        for tr, te in inner:
            if len(set(y[te])) < 2:
                continue
            m = fit_lr(X[tr], y[tr], C, None if w is None else w[tr])
            aucs.append(roc_auc_score(y[te], m.decision_function(X[te])))
        auc = float(np.mean(aucs)) if aucs else 0.0
        if auc > best_auc:
            best, best_auc = C, auc
    return fit_lr(X, y, best, w), best


def rank_metrics(pos, unl):
    """pos, unl: scores of liked and unlabeled test books.
    Returns AUC, the median percentile of the liked books in the unlabeled
    ranking (0 = top), and the share of liked books inside the top 5%."""
    if not len(pos) or not len(unl):
        return None
    srt = np.sort(unl)
    higher = len(unl) - np.searchsorted(srt, pos, side="right")
    ties = np.searchsorted(srt, pos, side="right") - np.searchsorted(srt, pos, side="left")
    perc = (higher + 0.5 * ties) / len(unl)
    return {"auc": float(1.0 - perc.mean()), "median": float(np.median(perc)), "top": float((perc <= TOP_SHARE).mean())}


def run_fold(data, rep, f, train_all, test_all, names):
    """One test fold. The split is made over all books; each comparison set then
    trains on its own books of the training side and is tested on its own books
    of the test side. Every book of the test side gets a score, also books that
    are in neither set (feed books), so the lists can use them."""
    y, groups = data.y, data.groups
    assert not set(groups[train_all]) & set(groups[test_all]), "an author is on both sides of the split"
    seed = SEED + 100 * rep + f
    with_lm = any(m in LM_MODELS for m in names)
    lm, n_readers = data.like_minded(train_all[y[train_all] == 1]) if with_lm else (np.zeros((len(y), 3)), 0)
    feed = test_all[data.is_feed[test_all]]
    out = {}
    for s, mask in data.sets.items():
        train, test = train_all[mask[train_all]], test_all[mask[test_all]]
        B = data.blocks(train, lm)
        pos, unl = test[y[test] == 1], test[y[test] == 0]
        for m in names:
            X = np.hstack([B[c] for c in MODELS["all" if m == LABEL_MODEL else m][1]])
            if m == LABEL_MODEL:
                tr = train[~data.want[train]]  # "want to read" books are not used as negatives
                model, C = fit_best(X[tr], y[tr], groups[tr], seed, data.weights[tr])
            else:
                model, C = fit_best(X[train], y[train], groups[train], seed)
            dec = model.decision_function(X)
            out[(s, m)] = {"metrics": rank_metrics(dec[pos], dec[unl]), "feed": rank_metrics(dec[pos], dec[feed]),
                           "scores": model.predict_proba(X[test_all])[:, 1], "C": C}
    return rep, test_all, out, n_readers


def cross_validate(data, use_labels):
    y, n = data.y, len(data.y)
    names = list(MODELS) + ([LABEL_MODEL] if use_labels else [])
    jobs = []
    for rep in range(REPEATS):
        splits = StratifiedGroupKFold(FOLDS, shuffle=True, random_state=SEED + rep).split(np.zeros(n), y, data.groups)
        jobs += [(rep, f, train, test) for f, (train, test) in enumerate(splits)]
    done = Parallel(n_jobs=-1)(delayed(run_fold)(data, rep, f, train, test, names) for rep, f, train, test in jobs)

    def summary(rs):
        out = {}
        for k in ("auc", "median", "top"):
            v = np.array([r[k] for r in rs])
            out[k], out[k + "_sd"] = float(v.mean()), float(v.std())
        out["aucs"] = np.array([r["auc"] for r in rs])
        return out

    res, feed, oof, chosen = {}, {}, {}, {}
    for s in data.sets:
        for m in names:
            parts = [d[2][(s, m)] for d in done]
            rep_scores = np.zeros((REPEATS, n))
            for (rep, test, out, _) in done:
                rep_scores[rep, test] = out[(s, m)]["scores"]
            r = summary([p["metrics"] for p in parts])
            if data.never.sum() >= 5:
                nv = [rank_metrics(rep_scores[k][y == 1], rep_scores[k][data.never])["auc"] for k in range(REPEATS)]
                r["never"], r["never_sd"] = float(np.mean(nv)), float(np.std(nv))
            res[(s, m)] = r
            fm = [p["feed"] for p in parts if p["feed"]]
            if fm:
                feed[(s, m)] = summary(fm)
            oof[(s, m)] = rep_scores.mean(axis=0)
            chosen[(s, m)] = Counter(p["C"] for p in parts)
    readers = np.array([d[3] for d in done])
    return names, res, feed, oof, chosen, readers


def pile_check_feed(data):
    """Can a model tell old matched books from feed books by their embeddings alone?"""
    idx = np.where((data.y == 0) & (data.is_feed | (data.store == "matched")))[0]
    t = data.is_feed[idx].astype(int)
    g = data.groups[idx]
    if t.sum() < FOLDS or (1 - t).sum() < FOLDS:
        return None
    aucs = []
    for rep in range(REPEATS):
        for tr, te in StratifiedGroupKFold(FOLDS, shuffle=True, random_state=SEED + rep).split(idx, t, g):
            pca = PCA(n_components=min(PCA_DIMS, len(tr) - 1), random_state=SEED).fit(data.E[idx[tr]])
            X = pca.transform(data.E[idx])
            X = X / X[tr].std(axis=0).mean()
            model, _ = fit_best(X[tr], t[tr], g[tr], SEED + rep)
            aucs.append(roc_auc_score(t[te], model.decision_function(X[te])))
    return float(np.mean(aucs)), float(np.std(aucs)), int(t.sum()), int((1 - t).sum())


def pile_check_epub(data, liked_keys_all):
    keys = epub_check_keys(liked_keys_all)
    sims = []
    for k in keys:
        a, b = book_vector("liked_fb2", k), book_vector("liked_epub", k)
        if a is not None and b is not None:
            sims.append(float(a @ b))
    L = data.E[data.y == 1]
    rng = np.random.default_rng(SEED)
    i, j = rng.integers(0, len(L), 4000), rng.integers(0, len(L), 4000)
    keep = i != j
    other = float((L[i[keep]] * L[j[keep]]).sum(axis=1).mean())
    return sims, other


# ---------------------------------------------------------------- taste map


def taste_map(data):
    idx = np.where(data.y == 1)[0]
    X = data.E[idx] - data.E[idx].mean(axis=0)  # take away what all liked books share, then compare directions
    X = X / np.linalg.norm(X, axis=1, keepdims=True)
    tries = []
    for k in range(4, 11):
        km = KMeans(n_clusters=k, n_init=10, random_state=SEED).fit(X)
        sizes = sorted(Counter(km.labels_).values(), reverse=True)
        tries.append({"k": k, "sil": float(silhouette_score(X, km.labels_, metric="cosine")), "sizes": sizes, "km": km})
    best = max(tries, key=lambda t: t["sil"])
    km = best["km"]
    clusters = []
    for c in range(best["k"]):
        members = np.where(km.labels_ == c)[0]
        centre = km.cluster_centers_[c] / np.linalg.norm(km.cluster_centers_[c])
        order = members[np.argsort(-(X[members] @ centre))]
        bs = [data.books[idx[i]] for i in members]
        authors = Counter(a.strip() for b in bs for a in b["author_names"].split(", ") if a.strip())
        groups = Counter(g for b in bs for g in b["groups"])
        clusters.append({
            "size": len(members),
            "authors": authors.most_common(5),
            "groups": [(g, c_ / len(members)) for g, c_ in groups.most_common(5)],
            "closest": [data.books[idx[i]] for i in order[:8]],
        })
    clusters.sort(key=lambda c: -c["size"])
    return tries, best, clusters


# ---------------------------------------------------------------- report


def pm(r, k, as_pct=False):
    if as_pct:
        return f"{100 * r[k]:.1f}% ± {100 * r[k + '_sd']:.1f}"
    return f"{r[k]:.3f} ± {r[k + '_sd']:.3f}"


def paired(res, s, a, b):
    """Model a against model b on comparison set s, fold by fold on the same folds."""
    d = res[(s, a)]["aucs"] - res[(s, b)]["aucs"]
    mean, sd, wins = float(d.mean()), float(d.std()), int((d > 0).sum())
    if mean > sd:
        verdict = "yes, more than the noise"
    elif mean < -sd:
        verdict = "no, it is worse by more than the noise"
    else:
        verdict = "no, inside the noise"
    return {"mean": mean, "sd": sd, "wins": wins, "n": len(d), "verdict": verdict,
            "text": f"{mean:+.3f} ± {sd:.3f} (better in {wins} of {len(d)} folds)"}


def quart(a, fmt="{:.2f}", points=(10, 25, 50, 75, 90)):
    a = np.asarray(a, dtype=float)
    a = a[~np.isnan(a)]
    return " / ".join(fmt.format(v) for v in np.percentile(a, points)) if len(a) else "n/a"


def band_rows(name, votes, avg):
    """Shares for the rule check. Returns the "any number of votes" row and the "trusted votes" row."""
    n = len(votes)
    if not n:
        return [name, 0, "", "", "", ""], [name, 0, "", "", "", "", ""]
    has = votes > 0
    a = np.where(has, avg, np.nan)
    any_row = [name, n, pct(np.mean(a > 4)), pct(np.mean((a >= 3) & (a <= 4))), pct(np.mean(a < 3)), pct(np.mean(~has))]
    ok = votes >= MIN_VOTES
    t = avg[ok]
    trusted = [name, n, pct(ok.mean()), pct(np.mean(has & ~ok)), pct(np.mean(~has))]
    trusted += [f"{pct(np.mean(t > 4))} / {pct(np.mean((t >= 3) & (t <= 4)))} / {pct(np.mean(t < 3))}" if len(t) else "n/a"]
    return any_row, trusted


def main():
    d = load()
    books, counts = d["books"], d["counts"]
    genre_map = json.loads(MAP_PATH.read_text(encoding="utf-8")) if MAP_PATH.exists() else {"groups": {}}
    group_names = [g for g in genre_map["groups"] if g != "nonfiction"] or sorted({g for b in books for g in b["groups"]})
    styl_meta = d["meta"].get("stylometry_features") or []
    styl_names = [f["name"] for f in styl_meta]
    cat = open_catalog()
    if cat is None:
        sys.exit('data/catalog.db has no ratings table (librate). Run "node src/fetch-catalog.js" first.')
    data = Data(books, group_names, styl_names, load_votes(cat, books))
    facts = catalog_facts(cat, genre_map)
    cat.close()
    y = data.y
    liked = y == 1
    in_rated, in_matched, is_feed = data.source == "rated-matched", data.store == "matched", data.is_feed
    n_pos, n_rated, n_matched, n_feed = int(liked.sum()), int(in_rated.sum()), int(in_matched.sum()), int(is_feed.sum())
    if n_pos < FOLDS or n_rated < 30 or n_matched < 30:
        sys.exit(f"Not enough books with vectors: {n_pos} liked, {n_rated} rated-matched, {n_matched} matched. Run model/embed.py first.")

    answers = Counter(d["labels"].values())
    n_answers = sum(answers.values())
    use_labels = n_answers >= MIN_LABELS
    print(f"Fingerprints: {n_pos} liked, {n_rated} rated-matched, {n_matched} old matched, {n_feed} feed books; {n_answers} label answers. Testing the models ...", flush=True)
    names, res, feed_res, oof, chosen, readers = cross_validate(data, use_labels)
    _, readers_all = data.like_minded(np.where(liked)[0])
    pile1 = pile_check_feed(data)
    sims, other_sim = pile_check_epub(data, d["liked_keys_all"])
    tries, best_k, clusters = taste_map(data)

    R = "rated"
    text_gain = paired(res, R, "rb+text", "rb")
    lm_vs_rb, lm_vs_text = paired(res, R, "lm", "rb"), paired(res, R, "lm", "rb+text")
    lmrb_vs_rb, lmall_vs_text = paired(res, R, "lm+rb", "rb"), paired(res, R, "lm+rb+text", "rb+text")
    best_lm = max(LM_MODELS, key=lambda m: res[(R, m)]["auc"])
    best_lm_vs_rb, best_lm_vs_text = paired(res, R, best_lm, "rb"), paired(res, R, best_lm, "rb+text")

    out = []
    add = out.append
    add("# Text fingerprints and ratings\n")
    add("Built by `model/fingerprints.py` (run everything with `npm run fingerprints`). "
        "Milestone 3 asked whether the *text itself* helps to find my books: how a book is written (stylometry) and what it is about (embeddings). "
        "Milestone 3b adds the reader ratings from Flibusta, which is how I really choose books, and a harder comparison set: "
        "books that are rated as well as my liked books.\n")

    # ---- the plain answers
    add("## The three plain answers\n")
    has_dates = bool(facts["date_cols"])
    add(f"1. **What the ratings table contains.** One row per reader per book: {facts['rows']:,} votes from {facts['readers']:,} readers on {facts['rated_books']:,} books. "
        f"Each row has a reader number, a book number and a mark from 1 to 5. "
        + ("Votes have dates." if has_dates else "**Votes have no dates**, so nothing can be said about how fast a new book collects votes.") + "\n")
    r_rb, r_text = res[(R, "rb")], res[(R, "rb+text")]
    add(f"2. **Does text beat model rb on the rated-matched set?** **{'Yes' if text_gain['mean'] > text_gain['sd'] else 'No'}.** "
        f"rb scores {r_rb['auc']:.3f} ± {r_rb['auc_sd']:.3f}, rb+text scores {r_text['auc']:.3f} ± {r_text['auc_sd']:.3f}. "
        f"Measured fold by fold on the same folds the difference is {text_gain['text']}. "
        + ("That is more than the fold-to-fold noise of the difference, so text does add something beyond ratings and genres."
           if text_gain["mean"] > text_gain["sd"] else
           "That is not more than the fold-to-fold noise of the difference, so text is not shown to add anything beyond ratings and genres.") + "\n")
    r_lm = res[(R, best_lm)]
    both = lmrb_vs_rb["mean"] > lmrb_vs_rb["sd"] and lmall_vs_text["mean"] > lmall_vs_text["sd"]
    both_word = "Yes, when it is added to them" if both else "No"
    alone_both = lm_vs_rb["mean"] > lm_vs_rb["sd"] and lm_vs_text["mean"] > lm_vs_text["sd"]
    add(f"3. **Does the like-minded readers score beat both?** **{both_word}; {'yes also' if alone_both else 'no'} on its own.** "
        f"Adding the score to rb changes the AUC by {lmrb_vs_rb['text']}; adding it to rb+text by {lmall_vs_text['text']}. "
        f"The best model of all is **{best_lm}**: {r_lm['auc']:.3f} ± {r_lm['auc_sd']:.3f} "
        f"({best_lm_vs_rb['mean']:+.3f} ± {best_lm_vs_rb['sd']:.3f} against rb, {best_lm_vs_text['mean']:+.3f} ± {best_lm_vs_text['sd']:.3f} against rb+text). "
        f"The score alone (model lm, three numbers and nothing else) reaches {res[(R, 'lm')]['auc']:.3f}, "
        f"which is level with rb and rb+text, not above them "
        f"(against rb {lm_vs_rb['mean']:+.3f} ± {lm_vs_rb['sd']:.3f}, against rb+text {lm_vs_text['mean']:+.3f} ± {lm_vs_text['sd']:.3f}). "
        f"There are {readers_all} like-minded readers when all liked books are used, and {readers.mean():.0f} on average inside a training part.\n")
    add("\"More than the noise\" means everywhere in this report: the average gain over the 50 test folds is bigger than the spread of that gain from fold to fold.\n")

    # ---- counts
    add("## Books used\n")
    fb2_share = counts["liked_fb2"] / max(1, counts["liked_all"])
    liked_fetch = {r["key"]: r for r in d["fetch"] if r["kind"] == "liked"}
    fetch_of = {k: [r for r in d["fetch"] if r["kind"] == k] for k in ("matched", "rated")}
    failed_of = {k: sum(1 for r in v if r["status"] != "ok") for k, v in fetch_of.items()}
    rated_new = counts["rated_picked"] - counts["rated_reused"]
    add(table(["Group", "Books"], [
        ["Liked books in the archive", counts["liked_all"]],
        ["... with FB2 text downloaded from the mirror", f"{counts['liked_fb2']} ({pct(fb2_share)})"],
        ["... still on EPUB text (FB2 could not be downloaded)", counts["liked_all"] - counts["liked_fb2"]],
        ["... left out as collections", counts["liked_collections"]],
        ["... left out as non-fiction", counts["liked_nonfiction"]],
        ["**Liked books used (positive class)**", f"**{n_pos}**"],
        ["Rated-matched set: books picked from the catalog", counts["rated_picked"]],
        ["... reused from the old matched set", counts["rated_reused"]],
        ["... new: downloaded and parsed", counts["rated_parsed"]],
        ["... new: download or parse failed", failed_of["rated"]],
        ["... new: not tried yet", max(0, rated_new - len(fetch_of["rated"]))],
        ["**Rated-matched books used (source = rated-matched)**", f"**{n_rated}**"],
        ["Old matched set: books picked from the catalog", counts["picked"]],
        ["... downloaded and parsed", counts["matched_parsed"]],
        ["... download or parse failed", failed_of["matched"]],
        ["**Old matched books used**", f"**{n_matched}** ({int((in_rated & in_matched).sum())} of them are also in the rated-matched set)"],
        ["**Feed books (only scored, never trained on)**", f"**{n_feed}**"],
        ["Different author groups (co-authors count as one group)", len(set(data.groups))],
    ]))
    if d["no_vector"]:
        add("Books left out because they have no embedding yet (run `model/embed.py`): " + ", ".join(f"{v} {k}" for k, v in d["no_vector"].items()) + ".\n")
    add("**The two comparison sets.** Both are Russian-language fiction from the catalog: not liked, not deleted, not a collection, "
        "at least 20,000 words when the catalog knows the size.\n\n"
        f"- **Rated-matched** (the one that counts): every book has at least {MIN_VOTES} votes, and the books were drawn so that their years *and* "
        "their average ratings (in steps of a quarter star) follow the liked books that have at least 5 votes. A model cannot win here just by saying \"this book is well rated\".\n"
        "- **Old matched** (easier): a random sample matched on year only. Most of these books have few votes or a low average, "
        "so the rating alone already separates them from my books.\n\n"
        "A parsed book is dropped when it turns out to be a collection, a duplicate, or not Russian-language. Genre was not matched in either set.\n")
    lk5 = liked & (data.votes >= MIN_VOTES)
    yr = lambda mask: quart([b["year"] for b, m in zip(books, mask) if m and b["year"]], "{:.0f}", (25, 50, 75))  # noqa: E731
    add(table(["Pile", "Books", "Year: lower quarter / median / upper quarter", "Average rating: lower quarter / median / upper quarter", "Votes: median"], [
        ["Liked", n_pos, yr(liked), quart(data.avg[liked], points=(25, 50, 75)), f"{np.median(data.votes[liked]):.0f}"],
        [f"Liked with at least {MIN_VOTES} votes", int(lk5.sum()), yr(lk5), quart(data.avg[lk5], points=(25, 50, 75)), f"{np.median(data.votes[lk5]):.0f}"],
        ["Rated-matched", n_rated, yr(in_rated), quart(data.avg[in_rated], points=(25, 50, 75)), f"{np.median(data.votes[in_rated]):.0f}"],
        ["Old matched", n_matched, yr(in_matched), quart(data.avg[in_matched], points=(25, 50, 75)), f"{np.median(data.votes[in_matched]):.0f}"],
        ["Feed", n_feed, yr(is_feed), quart(data.avg[is_feed], points=(25, 50, 75)), f"{np.median(data.votes[is_feed]):.0f}" if n_feed else ""],
    ]))
    add("Average ratings are over books that have at least one vote. Years and average ratings of the rated-matched set follow the liked books; "
        "the number of votes was not matched.\n")
    for name, n_set in (("rated-matched", n_rated), ("old matched", n_matched)):
        if n_set < MIN_SET:
            add(f"> **WARNING: only {n_set} {name} books are usable, fewer than {MIN_SET}.** Run `npm run fingerprints` again to continue the downloads.\n")
    if fb2_share < 0.95:
        add(f"> **WARNING: only {pct(fb2_share)} of the liked books have FB2 text (the goal is 95%).**\n")

    add("### Download failures\n")
    no_fb2 = [b for b in d["liked_all"] if b["key"] not in d["has_fb2"]]
    if no_fb2:
        add("Liked books that keep their EPUB text, because the FB2 file could not be downloaded or parsed:\n")
        add(table(["Key", "Author", "Title", "Reason"], [
            [b["key"], ", ".join(jlist(b["authors"])), b["title"], (liked_fetch.get(b["key"]) or {}).get("reason") or "not tried yet"] for b in no_fb2]))
    else:
        add("Every liked book has FB2 text.\n")
    newer = [r for r in liked_fetch.values() if r["status"] == "ok" and r["reason"]]
    if newer:
        add(f"{len(newer)} liked books were replaced in the library by a newer copy of the same book; for those the newer copy was downloaded.\n")
    for kind, title in (("rated", "Rated-matched books"), ("matched", "Old matched books")):
        tried = fetch_of[kind]
        if failed_of[kind]:
            why = Counter()
            for r in tried:
                if r["status"] != "ok":
                    reason = (r["reason"] or "unknown").split(": ", 1)[-1]
                    why[reason.split(" | ")[0][:90]] += 1
            add(f"{title}: {failed_of[kind]} of {len(tried)} tried could not be used:\n")
            add(table(["Reason", "Books"], [[k, v] for k, v in why.most_common(8)]))
        else:
            add(f"{title}: no failures in {len(tried)} downloads.\n")

    # ---- ratings (part H)
    add("## Ratings in the catalog\n")
    add("### What the ratings table holds\n")
    v = facts["values"]
    add(f"The table `librate` has the columns {', '.join('`' + c + '`' for c in facts['cols'])}. It is **one row per reader per book**, not a total per book: "
        f"{facts['rows']:,} rows, {facts['readers']:,} different readers, {facts['rated_books']:,} different books. "
        + ("No reader has two rows for the same book. " if facts["pairs"] == facts["rows"] else f"{facts['rows'] - facts['pairs']:,} rows repeat a reader and book pair; the highest mark is used. ")
        + "The readers are only numbers, with no names. "
        + (f"Date columns: {', '.join(facts['date_cols'])}." if has_dates else "**There is no date or time column**, so the table does not say when a vote was given.") + "\n")
    add(table(["Mark", "Votes", "Share"], [[k, f"{v.get(k, 0):,}", pct(v.get(k, 0) / max(1, facts["rows"]))] for k in (1, 2, 3, 4, 5)]))
    add("Because every reader's votes can be followed from book to book, the \"like-minded readers\" score further down is possible.\n")

    add("### My rule against the data\n")
    add(f"My rule: ignore books with an average below 3, prefer books above 4, trust an average with at least {MIN_VOTES} votes, and rarely read books with no ratings. "
        "The tables compare my liked books with all Russian-language fiction in the catalog (not deleted).\n")
    piles = [("Liked", data.votes[liked], data.avg[liked]), ("All Russian-language fiction in the catalog", facts["fiction_n"], facts["fiction_avg"]),
             ("Rated-matched set", data.votes[in_rated], data.avg[in_rated]), ("Old matched set", data.votes[in_matched], data.avg[in_matched]),
             ("Feed (a few days old)", data.votes[is_feed], data.avg[is_feed])]
    bands = [band_rows(*p_) for p_ in piles]
    add("Share of books by average rating, counting every book that has at least one vote:\n")
    add(table(["Pile", "Books", "Average above 4", "Between 3 and 4", "Below 3", "No votes"], [b[0] for b in bands]))
    add(f"The same, counting only averages that can be trusted (at least {MIN_VOTES} votes):\n")
    add(table(["Pile", "Books", f"At least {MIN_VOTES} votes", f"1 to {MIN_VOTES - 1} votes", "No votes", f"Among books with at least {MIN_VOTES} votes: above 4 / 3 to 4 / below 3"], [b[1] for b in bands]))
    add("How the numbers are spread (the 10% / 25% / 50% / 75% / 90% points). Example: a 25% point of 3.6 means a quarter of the books are below 3.6.\n")
    add(table(["Pile", "Average rating (books with a vote)", "Number of votes (all books)"],
              [[nm, quart(np.where(vo > 0, av, np.nan)), quart(vo, "{:.0f}")] for nm, vo, av in piles[:2]]))
    lv, la = data.votes[liked], np.nan_to_num(data.avg[liked])
    cv, ca = facts["fiction_n"], np.nan_to_num(facts["fiction_avg"])
    add(f"**What this says about the rule.** {pct(np.mean((lv > 0) & (la >= 3)))} of my liked books have an average of 3 or more, and only {pct(np.mean((lv > 0) & (la < 3)))} are below 3 "
        f"({pct(np.mean(lv == 0))} have no votes). In the whole catalog only {pct(np.mean((cv > 0) & (ca >= 3)))} of fiction books have an average of 3 or more, "
        f"and {pct(np.mean(cv == 0))} have no votes at all. {pct(np.mean(lv >= MIN_VOTES))} of my books have at least {MIN_VOTES} votes, against {pct(np.mean(cv >= MIN_VOTES))} in the catalog. "
        "So the data agrees with the rule: almost everything I read is rated, and rated 3 or higher. "
        f"\"Above 4\" is a preference, not a wall: {pct(np.mean((lv > 0) & (la > 4)))} of my books are above 4 and {pct(np.mean((lv > 0) & (la >= 3) & (la <= 4)))} sit between 3 and 4.\n")
    add("### How fast a new book collects votes\n")
    if has_dates:
        add("The votes have dates, but this report does not compute the days to 5 and 10 votes yet.\n")
    else:
        add("**This cannot be answered from the catalog.** The question was how many days after upload a book usually reaches 5 and 10 votes. "
            "Votes carry no date, so the dump only shows how many votes a book has today. "
            f"The only hint is the feed: {n_feed} books that are a few days old, of which {pct(np.mean(data.votes[is_feed] >= MIN_VOTES)) if n_feed else 'n/a'} already have at least {MIN_VOTES} votes "
            f"and {pct(np.mean(data.votes[is_feed] == 0)) if n_feed else 'n/a'} have none.\n")

    # ---- pile checks
    add("## Honest checks on the piles\n")
    add("### Check 1: can the text tell a feed book from an old matched book?\n")
    if pile1:
        auc, sd, nf, nm = pile1
        add(f"A model was trained to separate the {nf} feed books from the {nm} old matched books, using only the embeddings "
            f"(author-grouped, {FOLDS} folds, {REPEATS} repeats). **ROC AUC {auc:.3f} ± {sd:.3f}.** 0.5 would mean the two piles look the same.\n")
        if auc > PILE_LIMIT:
            add(f"> **This is above {PILE_LIMIT}: the upload week is visible in the text.** This week's uploads read differently from a random "
                "sample of the library with liked-book years.\n")
        else:
            add(f"This is not above {PILE_LIMIT}, so the upload week is not strongly visible in the text.\n")
    else:
        add("Not enough feed or matched books for this check yet.\n")
    add("### Check 2: does the file format change the embedding?\n")
    if sims:
        add(f"For {len(sims)} liked books the EPUB text and the FB2 text of the same book were embedded separately. "
            f"**Average cosine similarity {np.mean(sims):.4f}** (lowest {np.min(sims):.4f}). "
            f"For comparison, two *different* liked books have {other_sim:.3f} on average. 1.0 means identical.\n")
        add("So the format barely moves a book's vector compared with the distance between books." if np.mean(sims) > 0.98 and np.min(sims) > other_sim
            else "So the format does move the vector for some books; treat EPUB-only books with care.")
        add("")
    else:
        add("No liked book has both an FB2 and an EPUB embedding yet.\n")

    # ---- models
    add("## How well the models rank\n")
    add("Positive = liked books. Each model is trained and tested twice: once against the rated-matched set and once against the old matched set. "
        "No model uses the year. All are logistic regressions:\n")
    add("\n".join(f"- **{MODELS[m][0].split(':')[0]}** -{MODELS[m][0].split(':', 1)[1]}" for m in MODELS) + "\n")
    add(f"Embeddings: {PCA_DIMS} PCA directions, learned inside each training fold. "
        f"Test: author-grouped, {FOLDS} folds, {REPEATS} repeats ({FOLDS * REPEATS} test folds); an author is never on both sides. "
        "Every scale, the model's penalty strength and the list of like-minded readers are also found inside the training fold only.\n")
    top = f"top {int(TOP_SHARE * 100)}%"
    has_never = use_labels and any("never" in res[k] for k in res)
    head = ["Model", "**Rated-matched: ROC AUC**", "Rated-matched: median percentile of a liked book", f"Rated-matched: liked books in the {top}",
            "Old matched: ROC AUC", f"Old matched: liked books in the {top}"]
    if has_never:
        head.append("AUC liked vs \"would never read\"")
    rows_ = []
    for m in names:
        a, b = res[(R, m)], res[("matched", m)]
        row = [model_name(m), f"**{pm(a, 'auc')}**", pm(a, "median", True), pm(a, "top", True), pm(b, "auc"), pm(b, "top", True)]
        if has_never:
            row.append(f"{a['never']:.3f} ± {a['never_sd']:.3f}" if "never" in a else "")
        rows_.append(row)
    add(table(head, rows_))
    add("How to read the numbers:\n")
    add("- **ROC AUC**: 0.5 is coin-flipping, 1.0 is a perfect ranking. The number after ± is how much it moves from fold to fold.\n"
        "- **Median percentile**: each liked test book is placed in the ranking of the comparison test books. 10% means the typical liked book "
        "sits in the top tenth of the pile; 50% would be pure chance. Lower is better.\n"
        f"- **Top {int(TOP_SHARE * 100)}%**: the share of liked test books that score higher than 95% of the comparison test books. Chance gives {int(TOP_SHARE * 100)}%.\n"
        "- **The rated-matched columns are the ones that count.** On the old matched set the rating does most of the work, because that set is full of "
        "books with few votes or low averages. On the rated-matched set the average rating was matched away on purpose, so model r can only use the number of votes.\n")
    if has_never:
        add("- **AUC liked vs \"would never read\"**: how well the score (trained against the rated-matched set) separates liked books from the books I marked "
            "\"would never read\" (scores from the author-grouped test; ± is over the repeats). This is the cleanest number, because both sides are real answers.\n")

    add("### Does one model really beat another?\n")
    add("Each row compares two models fold by fold on the same 50 folds, which removes most of the fold-to-fold noise.\n")
    pairs = [("rb", "r", "genres, length, translated, series on top of ratings"),
             ("rb+text", "rb", "**text on top of ratings and genres**"),
             ("lm", "rb", "like-minded readers alone, against rb"),
             ("lm", "rb+text", "like-minded readers alone, against rb+text"),
             ("lm+rb", "rb", "like-minded readers on top of rb"),
             ("lm+rb+text", "rb+text", "like-minded readers on top of rb+text"),
             ("lm+rb+text", "lm+rb", "text on top of like-minded readers + rb"),
             ("all", "b0", "text on top of genres, with no ratings at all")]
    if use_labels:
        pairs.append((LABEL_MODEL, "all", "my answers on top of model all"))
    pr = []
    for a, b, what in pairs:
        x, o = paired(res, R, a, b), paired(res, "matched", a, b)
        pr.append([f"{a} against {b}", what, x["text"], x["verdict"], o["text"]])
    add(table(["Comparison", "What it tests", "Rated-matched: AUC gain", "Real gain on rated-matched?", "Old matched: AUC gain"], pr))

    add("### Does text add anything beyond ratings and genres?\n")
    add(f"**{'Yes' if text_gain['mean'] > text_gain['sd'] else 'No'}.** On the rated-matched set rb scores {r_rb['auc']:.3f} and rb+text scores {r_text['auc']:.3f}. "
        f"The gain is {text_gain['mean']:+.3f} and its fold-to-fold noise is ±{text_gain['sd']:.3f}; rb+text is better in {text_gain['wins']} of {text_gain['n']} folds. "
        + ("The gain is bigger than its noise, so the text carries something about my taste that ratings, genres and length do not."
           if text_gain["mean"] > text_gain["sd"] else
           "The gain is not bigger than its noise, so there is no proof that text adds anything once ratings, genres and length are known.") + "\n")
    add("### The like-minded readers score\n")
    add(f"A like-minded reader is a reader who gave 4 or 5 to at least {LM_MIN_BOOKS} of my liked books. Using all {n_pos} liked books there are **{readers_all}** such readers. "
        f"In the test they are found from the liked books of the training part only: {readers.mean():.0f} on average (from {readers.min()} to {readers.max()}).\n\n"
        "For every book the score is three numbers: how many like-minded readers voted for it, what share of all its voters they are, "
        f"and how much higher (or lower) they rated it than all readers did (a few votes are pulled towards the book's overall average, with the weight of {LM_SHRINK:g} votes). "
        "A training book never helps to choose its own like-minded readers, and neither do other books by the same author; "
        "otherwise the score would look far better in training than it can be on a new author.\n")
    add(f"**Does it beat both rb and rb+text? {both_word}; {'yes also' if alone_both else 'no'} on its own.** Best model with the score: {best_lm}, {r_lm['auc']:.3f} ± {r_lm['auc_sd']:.3f} on the rated-matched set "
        f"({best_lm_vs_rb['mean']:+.3f} ± {best_lm_vs_rb['sd']:.3f} against rb, {best_lm_vs_text['mean']:+.3f} ± {best_lm_vs_text['sd']:.3f} against rb+text). "
        f"Added to rb it changes the AUC by {lmrb_vs_rb['text']}; added to rb+text by {lmall_vs_text['text']}.\n")

    add("### New uploads: liked books against feed books\n")
    fr = [(s, m) for s in data.sets for m in ("all", "rb+text") if (s, m) in feed_res]
    if fr:
        add("Feed books are never trained on. Here each model's score is tested on \"liked test books against feed test books\".\n")
        add(table(["Model", "Trained against", "ROC AUC on liked vs feed", "Median percentile", f"Top {int(TOP_SHARE * 100)}%"],
                  [[m, SETS[s], pm(feed_res[(s, m)], "auc"), pm(feed_res[(s, m)], "median", True), pm(feed_res[(s, m)], "top", True)] for s, m in fr]))
        add("Model all uses no ratings, so its rows show how well taste in text and genre carries over to this week's uploads. "
            "The rb+text rows are a warning rather than a result: feed books have almost no votes yet, so against the old matched set a ratings model "
            "separates them for the wrong reason, and against the rated-matched set it has never seen a book with so few votes.\n")
    else:
        add("Not enough feed books in the test folds for this check.\n")

    # ---- labels
    add("## My answers from the labelling page\n")
    add(f"Answers so far: **{n_answers}** ({answers.get('never', 0)} would never read, {answers.get('maybe', 0)} maybe, {answers.get('want', 0)} want to read). "
        f"Starred favourites: {len(d['stars'])}.\n")
    add("The page now shows the average rating and the number of votes on each card. Books with an average of 3 or more and at least 5 votes come first, "
        "and among those the rated-matched books come before the others.\n")
    if use_labels:
        add(f"There are at least {MIN_LABELS} answers, so they are used: the extra column \"liked vs would never read\" for every model in the table above, and the extra model **all+labels** "
            f"(model all, where \"would never read\" books count {NEVER_WEIGHT:g} times as negatives, starred books count {STAR_WEIGHT:g} times as positives, "
            "and \"want to read\" books are not used as negatives). Books I already answered are left out of the two lists below.\n")
    else:
        add(f"> **Fewer than {MIN_LABELS} answers, so the label step is skipped**: no \"liked vs would never read\" metric, no all+labels model, "
            "and nothing is removed from the lists below. Start the page with `npm run label`, answer some more, and run the report again.\n")
    add("### Genre groups: \"would never read\" against liked\n")
    never_idx = np.where(data.never)[0]
    if len(never_idx):
        if len(never_idx) < 30:
            add(f"Only {len(never_idx)} \"would never read\" books so far, so the shares below are rough.\n")
        G = data.G
        rr = []
        for j, g in enumerate(group_names):
            lp, np_ = G[y == 1, j].mean(), G[never_idx, j].mean()
            if lp == 0 and np_ == 0:
                continue
            rr.append((np_ - lp, [g, int(G[never_idx, j].sum()), pct(np_), int(G[y == 1, j].sum()), pct(lp), f"{100 * (np_ - lp):+.1f}"]))
        rr.sort(key=lambda r: -r[0])
        add(table(["Genre group", "Never: books", "Never: share", "Liked: books", "Liked: share", "Difference (points)"], [r[1] for r in rr]))
    else:
        add("No \"would never read\" answers yet, so this table is empty.\n")

    # ---- taste map
    add("## Taste map: clusters of the liked books\n")
    add("The liked books were grouped by their embeddings (k-means on the book vectors, after taking away what all liked books have in common). "
        "The number of clusters was chosen by the silhouette score (higher = cleaner groups; values near 0 mean the groups overlap a lot).\n")
    add(table(["Clusters", "Silhouette", "Cluster sizes", ""], [
        [t["k"], f"{t['sil']:.3f}", ", ".join(str(s) for s in t["sizes"]), "**chosen**" if t is best_k else ""] for t in tries]))
    for i, c in enumerate(clusters, 1):
        add(f"### Cluster {i}: {c['size']} books\n")
        add("- Most common authors: " + ", ".join(f"{a} ({n})" for a, n in c["authors"]))
        add("- Most common genre groups: " + ", ".join(f"{g} ({pct(s)})" for g, s in c["groups"]))
        add("- Closest to the centre:\n")
        add(table(["Author", "Title"], [[b["author_names"], b["title"]] for b in c["closest"]]))

    # ---- lists
    best_m = max(names, key=lambda m: res[(R, m)]["auc"])
    early_m = max(NO_RATINGS, key=lambda m: res[(R, m)]["auc"])
    answered = np.array([bool(b["label"]) for b in books]) & use_labels

    def book_rows(order, score, with_source=True):
        rows_b = []
        for i in order:
            b = books[i]
            row = [f"{score[i]:.2f}", f"{data.avg[i]:.2f}" if data.votes[i] else "", int(data.votes[i]), b["key"], b["author_names"], b["title"], ", ".join(b["genres"])]
            rows_b.append(row + ([b["source"]] if with_source else []))
        return rows_b

    cols = ["Score", "Rating", "Votes", "Key", "Author", "Title", "Genres", "Source"]
    add(f"## The top {LIST_SIZE}, in two lists\n")
    add("Every score comes from the author-grouped test against the rated-matched set: a book is scored only by models that never saw "
        "the book or its author, averaged over the repeats. The score is between 0 and 1. Scores of the two lists come from different models, so do not compare them across lists.\n")
    add(f"### Rated: at least {MIN_VOTES} votes and an average of 3 or more\n")
    score = oof[(R, best_m)]
    add(f"Scored by the model with the best ROC AUC on the rated-matched set, **{best_m}**. {int((~liked & data.rated_ok).sum())} unlabeled books qualify.\n")
    order = [i for i in np.argsort(-score) if not liked[i] and data.rated_ok[i] and not answered[i]]
    add(table(cols, book_rows(order[:LIST_SIZE], score)))
    add(f"### Early: fewer than {MIN_VOTES} votes\n")
    early = oof[(R, early_m)]
    add(f"These books have too few votes for the rating to mean much, so they are scored by the best model that uses no ratings at all, **{early_m}** "
        f"(text and genres only). {int((~liked & (data.votes < MIN_VOTES)).sum())} unlabeled books qualify. "
        f"Books with at least {MIN_VOTES} votes and an average below 3 are in neither list: my rule says to ignore them.\n")
    order = [i for i in np.argsort(-early) if not liked[i] and data.votes[i] < MIN_VOTES and not answered[i]]
    add(table(cols, book_rows(order[:LIST_SIZE], early)))
    add("## The 20 liked books with the lowest score\n")
    add(f"Liked books that the best model ({best_m}, trained against the rated-matched set) would have missed.\n")
    low = [i for i in np.argsort(score) if liked[i]][:20]
    add(table(cols[:-1], book_rows(low, score, False)))

    # ---- appendix: stylometry
    if styl_meta:
        add("## Appendix: stylometry averages\n")
        add("The numbers measured on each book's full text, averaged per pile.\n")
        S = data.S
        rows_s = []
        for j, f in enumerate(styl_meta):
            cells = [f["what"]]
            for mask in (liked, in_rated, in_matched, is_feed):
                vals = S[mask, j]
                cells.append(f"{np.nanmean(vals):.3g}" if np.any(~np.isnan(vals)) else "")
            rows_s.append(cells)
        add(table(["Measure", "Liked", "Rated-matched", "Old matched", "Feed"], rows_s))

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(out).replace("\n\n\n", "\n\n").rstrip() + "\n", encoding="utf-8")

    for s in data.sets:
        print(f"Against the {SETS[s]} set:")
        for m in names:
            r = res[(s, m)]
            print(f"  {m:11s} AUC {r['auc']:.3f} ± {r['auc_sd']:.3f}   median percentile {100 * r['median']:.1f}%   top 5%: {100 * r['top']:.1f}%   C chosen: {dict(chosen[(s, m)])}")
    print(f"  rb+text against rb on rated-matched: {text_gain['text']}")
    print(f"  {best_lm} against rb: {best_lm_vs_rb['text']}; against rb+text: {best_lm_vs_text['text']}")
    print(f"  like-minded readers: {readers_all} with all liked books, {readers.mean():.0f} on average in a training part")
    if pile1:
        print(f"  pile check 1 (feed vs old matched): AUC {pile1[0]:.3f}")
    if sims:
        print(f"  pile check 2 (EPUB vs FB2): cosine {np.mean(sims):.4f} on {len(sims)} books")
    print(f"Report written to {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
