"""Milestone 3, parts C to F. Text fingerprints: checks, models, taste map, report.

Run:  .venv/bin/python model/fingerprints.py        writes reports/fingerprints.md

Needs, in data/books.db and data/emb/ (made by the earlier steps):
  node src/fetch-books.js      liked FB2 texts, the matched background, ratings
  node src/stylometry.js       the "stylometry" table
  .venv/bin/python model/embed.py   chunk vectors for every book
"npm run fingerprints" runs these (they skip what is already done) and then this file.

Positive class  = liked books (no collections, no non-fiction).
Unlabeled class = the view "unlabeled_books": matched background + feed books.
"Year" is not used by any model here.
"""

import json
import math
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import numpy as np
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
MAP_PATH = DATA / "genre-map.json"
EMB = DATA / "emb"
REPORT = ROOT / "reports" / "fingerprints.md"

FOLDS = 5
REPEATS = 10
SEED = 20261112
PCA_DIMS = 50
C_GRID = (0.01, 0.1, 1.0)  # strength of the model's "keep it simple" penalty, chosen inside each training part
TOP_SHARE = 0.05
OLD_AUC = 0.79  # milestone 2, model b, author-grouped
MIN_LABELS = 100
NEVER_WEIGHT = 3.0  # "would never read" books count this many times as a negative
STAR_WEIGHT = 2.0
PILE_LIMIT = 0.75
MIN_MATCHED = 800


# ---------------------------------------------------------------- loading


def book_vector(set_name, key):
    path = EMB / set_name / f"{key}.npy"
    if not path.exists():
        return None
    v = np.load(path).astype(np.float32).mean(axis=0)
    return v / (np.linalg.norm(v) or 1.0)


def load():
    if not DB_PATH.exists():
        sys.exit("data/books.db not found. Run the Node steps first.")
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row

    def rows(sql, args=()):
        return [dict(r) for r in db.execute(sql, args)]

    def has(name):
        return db.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone() is not None

    if not has("unlabeled_books"):
        sys.exit('No "unlabeled_books" view yet. Run "node src/fetch-books.js" first.')
    meta = {r["name"]: json.loads(r["value"]) for r in db.execute("SELECT name, value FROM meta")}
    liked_all = rows("SELECT * FROM books WHERE parse_status != 'failed'")
    unl_all = rows("SELECT * FROM unlabeled_books")
    fb2 = {r["key"]: r for r in rows("SELECT key, words, annotation FROM parsed WHERE source = 'liked_fb2' AND parse_status != 'failed'")}
    fetch = rows("SELECT * FROM fb2_fetch") if has("fb2_fetch") else []
    styl = {(r["kind"], r["key"]): json.loads(r["features"]) for r in rows("SELECT * FROM stylometry WHERE features IS NOT NULL")} if has("stylometry") else {}
    ratings = {r["key"]: r for r in rows("SELECT * FROM book_ratings")} if has("book_ratings") else {}
    labels = {r["key"]: r["label"] for r in rows("SELECT key, label FROM labels")} if has("labels") else {}
    stars = {r["key"] for r in rows("SELECT key FROM stars")} if has("stars") else set()
    picked = db.execute("SELECT COUNT(*) FROM matched_pick").fetchone()[0] if has("matched_pick") else 0
    matched_table = db.execute("SELECT COUNT(*) FROM matched_books").fetchone()[0]
    matched_parsed = db.execute("SELECT COUNT(*) FROM parsed WHERE source = 'matched' AND parse_status != 'failed'").fetchone()[0]
    db.close()

    text_dir = DATA / "text-fb2"
    counts = {
        "liked_all": len(liked_all),
        "liked_collections": sum(1 for b in liked_all if b["is_collection"]),
        "liked_nonfiction": sum(1 for b in liked_all if not b["is_collection"] and not b["is_fiction"]),
        "picked": picked, "matched_table": matched_table, "matched_parsed": matched_parsed,
    }
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
                source = "liked"
                use_fb2 = key in has_fb2
                vec = book_vector("liked_fb2", key) if use_fb2 else book_vector("liked_epub", key)
                words = fb2[key]["words"] if use_fb2 else b["words"]
                text = "fb2" if use_fb2 else "epub"
            else:
                source = b["source"]
                vec = book_vector(source, key)
                words, text = b["words"], "fb2"
            if vec is None:
                no_vector[source] += 1
                continue
            ids, names = jlist(b["cat_author_ids"]), jlist(b["authors"])
            authors = [f"id{i}" for i in ids] if ids else [f"n:{norm_author(n)}" for n in names if n.strip()]
            in_catalog = b["cat_author_ids"] is not None or b["cat_genres"] is not None
            year = b["cat_year"] if b["cat_year"] else b["year"]
            books.append({
                "key": key, "y": label, "source": source, "text": text,
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
                "rating": ratings.get(key),
                "label": labels.get(key) if label == 0 else None,
                "star": key in stars,
            })
    return {"books": books, "counts": counts, "meta": meta, "fetch": fetch, "no_vector": no_vector, "labels": labels,
            "stars": stars, "liked_all": liked_all, "has_fb2": has_fb2, "has_ratings": bool(ratings), "liked_keys_all": liked_keys_all}


# ---------------------------------------------------------------- features and models


class Data:
    def __init__(self, books, group_names, styl_names):
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
        self.log_words = np.array([math.log10(max(b["words"], 100)) for b in books])
        self.flags = np.array([[b["translated"], b["in_series"]] for b in books], dtype=float)
        self.S = np.array([[(b["styl"] or {}).get(nm) if (b["styl"] or {}).get(nm) is not None else np.nan for nm in styl_names] for b in books], dtype=float)
        self.E = np.vstack([b["vec"] for b in books])
        self.groups = author_groups(books)
        self.never = np.array([b["label"] == "never" for b in books])
        self.want = np.array([b["label"] == "want" for b in books])
        self.star = np.array([b["star"] and b["y"] == 1 for b in books])

    def blocks(self, train):
        """The three feature blocks for all books, with every scale, median and
        PCA direction learned from the training rows only."""
        words = StandardScaler().fit(self.log_words[train, None]).transform(self.log_words[:, None])
        b0 = np.hstack([self.G, words, self.flags])
        med = np.nanmedian(self.S[train], axis=0)
        med = np.where(np.isnan(med), 0.0, med)
        S = np.where(np.isnan(self.S), med, self.S)
        S = np.clip(StandardScaler().fit(S[train]).transform(S), -5, 5)
        dims = min(PCA_DIMS, len(train) - 1)
        pca = PCA(n_components=dims, random_state=SEED).fit(self.E[train])
        E = pca.transform(self.E)
        E = E / E[train].std(axis=0).mean()  # one common scale, so the big directions stay the big ones
        return {"b0": b0, "s": S, "e": E}


MODELS = {
    "b0": ("b0: genres + length, translated, series", ["b0"]),
    "s": ("s: b0 + stylometry", ["b0", "s"]),
    "e": ("e: embeddings only", ["e"]),
    "be": ("be: b0 + embeddings", ["b0", "e"]),
    "all": ("all: b0 + stylometry + embeddings", ["b0", "s", "e"]),
}


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


def cross_validate(data, use_labels):
    y, groups, n = data.y, data.groups, len(data.y)
    names = list(MODELS) + (["all+labels"] if use_labels else [])
    folds_out = {m: [] for m in names}
    extra = {"be_on_feed": [], "be_matched_only_on_feed": []}
    oof = {m: np.zeros(n) for m in names}
    never_auc = {m: [] for m in names}
    chosen = {m: Counter() for m in names}
    is_feed = data.source == "feed"
    weights = np.ones(n)
    weights[data.never] = NEVER_WEIGHT
    weights[data.star] = STAR_WEIGHT

    for rep in range(REPEATS):
        rep_scores = {m: np.zeros(n) for m in names}
        splits = StratifiedGroupKFold(FOLDS, shuffle=True, random_state=SEED + rep).split(np.zeros(n), y, groups)
        for f, (train, test) in enumerate(splits):
            assert not set(groups[train]) & set(groups[test]), "an author is on both sides of the split"
            seed = SEED + 100 * rep + f
            B = data.blocks(train)
            pos, unl = test[y[test] == 1], test[y[test] == 0]
            for m in names:
                cols = MODELS["all" if m == "all+labels" else m][1]
                X = np.hstack([B[c] for c in cols])
                if m == "all+labels":
                    tr = train[~data.want[train]]  # "want to read" books are not used as negatives
                    model, C = fit_best(X[tr], y[tr], groups[tr], seed, weights[tr])
                else:
                    model, C = fit_best(X[train], y[train], groups[train], seed)
                chosen[m][C] += 1
                score = model.predict_proba(X[test])[:, 1]
                rep_scores[m][test] = score
                r = rank_metrics(model.decision_function(X[pos]), model.decision_function(X[unl]))
                folds_out[m].append(r)
                if m == "be":
                    feed_test = unl[is_feed[unl]]
                    r2 = rank_metrics(model.decision_function(X[pos]), model.decision_function(X[feed_test]))
                    if r2:
                        extra["be_on_feed"].append(r2)
            # "be" trained without any feed book, tested on liked vs feed
            tr = train[~is_feed[train]]
            if (y[tr] == 0).sum() >= 30:  # needs a matched background to train against
                Bm = data.blocks(tr)
                X = np.hstack([Bm["b0"], Bm["e"]])
                model, _ = fit_best(X[tr], y[tr], groups[tr], seed)
                feed_test = unl[is_feed[unl]]
                r3 = rank_metrics(model.decision_function(X[pos]), model.decision_function(X[feed_test]))
                if r3:
                    extra["be_matched_only_on_feed"].append(r3)
        for m in names:
            oof[m] += rep_scores[m] / REPEATS
            if data.never.sum() >= 5:
                never_auc[m].append(rank_metrics(rep_scores[m][y == 1], rep_scores[m][data.never])["auc"])

    def summary(rs):
        out = {}
        for k in ("auc", "median", "top"):
            v = np.array([r[k] for r in rs])
            out[k], out[k + "_sd"] = float(v.mean()), float(v.std())
        out["aucs"] = np.array([r["auc"] for r in rs])
        return out

    res = {m: summary(folds_out[m]) for m in names}
    for m in names:
        if never_auc[m]:
            res[m]["never"], res[m]["never_sd"] = float(np.mean(never_auc[m])), float(np.std(never_auc[m]))
    ext = {k: summary(v) for k, v in extra.items() if v}
    return res, ext, oof, chosen


def pile_check_feed(data):
    """Can a model tell matched books from feed books by their embeddings alone?"""
    idx = np.where(data.y == 0)[0]
    t = (data.source[idx] == "feed").astype(int)
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


def main():
    d = load()
    books, counts = d["books"], d["counts"]
    genre_map = json.loads(MAP_PATH.read_text(encoding="utf-8")) if MAP_PATH.exists() else {"groups": {}}
    group_names = [g for g in genre_map["groups"] if g != "nonfiction"] or sorted({g for b in books for g in b["groups"]})
    styl_meta = d["meta"].get("stylometry_features") or []
    styl_names = [f["name"] for f in styl_meta]
    data = Data(books, group_names, styl_names)
    y = data.y
    n_pos, n_unl = int(y.sum()), int((1 - y).sum())
    n_matched, n_feed = int((data.source == "matched").sum()), int((data.source == "feed").sum())
    if n_pos < FOLDS or n_unl < FOLDS:
        sys.exit(f"Not enough books with vectors: {n_pos} liked, {n_unl} unlabeled. Run model/embed.py first.")

    answers = Counter(d["labels"].values())
    n_answers = sum(answers.values())
    use_labels = n_answers >= MIN_LABELS
    print(f"Fingerprints: {n_pos} liked, {n_matched} matched, {n_feed} feed books; {n_answers} label answers. Testing the models ...", flush=True)
    res, ext, oof, chosen = cross_validate(data, use_labels)
    pile1 = pile_check_feed(data)
    sims, other_sim = pile_check_epub(data, d["liked_keys_all"])
    tries, best_k, clusters = taste_map(data)

    out = []
    add = out.append
    add("# Text fingerprints\n")
    add("Built by `model/fingerprints.py` (run everything with `npm run fingerprints`). "
        "Milestone 2 scored a book by simple facts such as genre and length. This report asks whether the *text itself* helps: "
        "how the book is written (stylometry) and what it is about (embeddings).\n")

    # ---- counts
    add("## Books used\n")
    fb2_share = counts["liked_fb2"] / max(1, counts["liked_all"])
    liked_fetch = {r["key"]: r for r in d["fetch"] if r["kind"] == "liked"}
    matched_fetch = [r for r in d["fetch"] if r["kind"] == "matched"]
    m_ok = sum(1 for r in matched_fetch if r["status"] == "ok")
    m_failed = sum(1 for r in matched_fetch if r["status"] != "ok")
    add(table(["Group", "Books"], [
        ["Liked books in the archive", counts["liked_all"]],
        ["... with FB2 text downloaded from the mirror", f"{counts['liked_fb2']} ({pct(fb2_share)})"],
        ["... still on EPUB text (FB2 could not be downloaded)", counts["liked_all"] - counts["liked_fb2"]],
        ["... left out as collections", counts["liked_collections"]],
        ["... left out as non-fiction", counts["liked_nonfiction"]],
        ["**Liked books used (positive class)**", f"**{n_pos}**"],
        ["Matched background: books picked from the catalog", counts["picked"]],
        ["... downloaded and parsed", counts["matched_parsed"]],
        ["... download or parse failed", m_failed],
        ["... not tried yet", max(0, counts["picked"] - len(matched_fetch))],
        ["**Matched books used (unlabeled, source = matched)**", f"**{n_matched}**"],
        ["**Feed books used (unlabeled, source = feed)**", f"**{n_feed}**"],
        ["Different author groups (co-authors count as one group)", len(set(data.groups))],
    ]))
    if d["no_vector"]:
        add("Books left out because they have no embedding yet (run `model/embed.py`): " + ", ".join(f"{v} {k}" for k, v in d["no_vector"].items()) + ".\n")
    add(f"The matched background is a random sample of Russian-language fiction from the catalog (not liked, not deleted, not a collection, "
        f"at least 20,000 words when the catalog knows the size), drawn so that its years follow the years of the liked books. Genre was not matched. "
        f"A parsed matched book is dropped when it turns out to be a collection, a duplicate, or not Russian-language.\n")
    yl = np.array([b["year"] for b in books if b["y"] == 1 and b["year"]])
    ym = np.array([b["year"] for b in books if b["source"] == "matched" and b["year"]])
    yf = np.array([b["year"] for b in books if b["source"] == "feed" and b["year"]])
    if len(yl) and len(ym):
        q = lambda a: " / ".join(f"{v:.0f}" for v in np.percentile(a, [25, 50, 75])) if len(a) else "n/a"  # noqa: E731
        add(table(["Pile", "Year: lower quarter / median / upper quarter"], [["Liked", q(yl)], ["Matched", q(ym)], ["Feed", q(yf)]]))
    if n_matched < MIN_MATCHED:
        add(f"> **WARNING: only {n_matched} matched books are usable, fewer than {MIN_MATCHED}.** Run `node src/fetch-books.js` again to continue the downloads.\n")
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
    if m_failed:
        why = Counter()
        for r in matched_fetch:
            if r["status"] != "ok":
                reason = (r["reason"] or "unknown").split(": ", 1)[-1]
                why[reason.split(" | ")[0][:90]] += 1
        add(f"Matched books: {m_failed} of {len(matched_fetch)} tried could not be used:\n")
        add(table(["Reason", "Books"], [[k, v] for k, v in why.most_common(8)]))
    else:
        add(f"Matched books: no failures in {len(matched_fetch)} downloads.\n")

    # ---- ratings
    add("## Ratings in the catalog\n")
    if d["has_ratings"]:
        def rating_row(name, bs):
            rated = [b["rating"] for b in bs if b["rating"] and b["rating"]["n"]]
            if not rated:
                return [name, len(bs), 0, "0.0%", "", ""]
            votes = sum(r["n"] for r in rated)
            return [name, len(bs), len(rated), pct(len(rated) / len(bs)), f"{np.mean([r['avg'] for r in rated]):.2f}", f"{np.median([r['n'] for r in rated]):.0f}"]
        add("The catalog dump has reader ratings from 1 to 5. They are **not used in any model yet**; this is only a count.\n")
        add(table(["Pile", "Books", "Books with a rating", "Share", "Average rating", "Median number of votes"], [
            rating_row("Liked", [b for b in books if b["y"] == 1]),
            rating_row("Unlabeled (all)", [b for b in books if b["y"] == 0]),
            rating_row("... matched", [b for b in books if b["source"] == "matched"]),
            rating_row("... feed", [b for b in books if b["source"] == "feed"]),
        ]))
        add("\"Average rating\" is the average over books of each book's own average. Feed books are a few days old, so few readers have rated them yet.\n")
    else:
        add("The catalog dump has no ratings table, or `node src/fetch-books.js` has not looked them up yet.\n")

    # ---- pile checks
    add("## Honest checks on the two piles\n")
    add("### Check 1: can the text tell a feed book from a matched book?\n")
    if pile1:
        auc, sd, nf, nm = pile1
        add(f"A model was trained to separate the {nf} feed books from the {nm} matched books, using only the embeddings "
            f"(author-grouped, {FOLDS} folds, {REPEATS} repeats). **ROC AUC {auc:.3f} ± {sd:.3f}.** 0.5 would mean the two piles look the same.\n")
        if auc > PILE_LIMIT:
            add(f"> **This is above {PILE_LIMIT}: the upload week is visible in the text.** This week's uploads read differently from a random "
                "sample of the library with liked-book years. So a model trained on \"liked vs feed\" partly learns \"not from this week\" instead of \"my taste\". "
                "That is why the matched background matters, and why the transfer test below is the number to trust for new uploads.\n")
        else:
            add(f"This is not above {PILE_LIMIT}, so the upload week is not strongly visible in the text. The feed and the matched background can be mixed.\n")
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
    add("Positive = liked books. Unlabeled = matched + feed. No model uses the year. All are logistic regressions:\n")
    add("\n".join(f"- **{MODELS[m][0].split(':')[0]}** -{MODELS[m][0].split(':', 1)[1]}" for m in MODELS) + "\n")
    add(f"Embeddings: {PCA_DIMS} PCA directions, learned inside each training fold. "
        f"Test: author-grouped, {FOLDS} folds, {REPEATS} repeats ({FOLDS * REPEATS} test folds); an author is never on both sides. "
        "Every scale and the model's penalty strength are also chosen inside the training fold.\n")
    names = list(res)
    has_never = any("never" in res[m] for m in names)
    head = ["Model", "ROC AUC", "Median percentile of a liked book", f"Liked books in the top {int(TOP_SHARE * 100)}%", "AUC gain over b0 (same folds)"]
    if has_never and use_labels:
        head.append("AUC liked vs \"would never read\"")
    rows_ = []
    for m in names:
        r = res[m]
        diff = r["aucs"] - res["b0"]["aucs"]
        gain = "" if m == "b0" else f"{diff.mean():+.3f} ± {diff.std():.3f} (better in {int((diff > 0).sum())} of {len(diff)} folds)"
        label_name = MODELS[m][0] if m in MODELS else "all+labels: all, with my answers"
        row = [label_name, pm(r, "auc"), pm(r, "median", True), pm(r, "top", True), gain]
        if has_never and use_labels:
            row.append(f"{r['never']:.3f} ± {r['never_sd']:.3f}" if "never" in r else "")
        rows_.append(row)
    add(table(head, rows_))
    add("How to read the numbers:\n")
    add("- **ROC AUC**: 0.5 is coin-flipping, 1.0 is a perfect ranking. The number after ± is how much it moves from fold to fold.\n"
        "- **Median percentile**: each liked test book is placed in the ranking of the unlabeled test books. 10% means the typical liked book "
        "sits in the top tenth of the pile; 50% would be pure chance. Lower is better.\n"
        f"- **Top {int(TOP_SHARE * 100)}%**: the share of liked test books that score higher than 95% of the unlabeled test books. Chance gives {int(TOP_SHARE * 100)}%. "
        "This is the realistic one: it is close to \"if I only look at the best 1 in 20 new books, how many of my books would I find\".\n"
        "- **AUC gain over b0**: the difference to model b0 measured fold by fold on the same folds, which removes most of the fold-to-fold noise.\n")
    if has_never and use_labels:
        add("- **AUC liked vs \"would never read\"**: how well the score separates liked books from the books I marked \"would never read\" "
            "(scores from the author-grouped test; ± is over the repeats). This is the cleanest number, because both sides are real answers.\n")

    # the plain verdict
    text_models = [m for m in ("s", "e", "be", "all") if m in res]
    top_m = max(text_models, key=lambda m: res[m]["auc"])
    r_top, r_b0 = res[top_m], res["b0"]
    diff = r_top["aucs"] - r_b0["aucs"]
    add("### Does text beat 0.79?\n")
    over = r_top["auc"] - OLD_AUC
    if over > r_top["auc_sd"]:
        verdict = f"**Yes.** The best text model ({top_m}) reaches {r_top['auc']:.3f}, which is {over:+.3f} against {OLD_AUC}; the fold-to-fold noise is ±{r_top['auc_sd']:.3f}, so the gain is bigger than the noise."
    elif over > 0:
        verdict = f"**Not clearly.** The best text model ({top_m}) reaches {r_top['auc']:.3f}, which is {over:+.3f} against {OLD_AUC}, but the fold-to-fold noise is ±{r_top['auc_sd']:.3f}. The gain is inside the noise."
    else:
        verdict = f"**No.** The best text model ({top_m}) reaches {r_top['auc']:.3f}, which is {over:+.3f} against {OLD_AUC} (fold-to-fold noise ±{r_top['auc_sd']:.3f})."
    add(verdict + "\n")
    add(f"A caution about that comparison: {OLD_AUC} was measured against feed books only and with the year as a feature; today's test uses a different, "
        f"fairer unlabeled pile. The like-for-like comparison is against **b0 on the same books and folds**: b0 scores {r_b0['auc']:.3f} ± {r_b0['auc_sd']:.3f}, "
        f"model {top_m} scores {r_top['auc']:.3f}, a gain of {diff.mean():+.3f} ± {diff.std():.3f} per fold, and {top_m} wins in {int((diff > 0).sum())} of {len(diff)} folds. "
        + ("That gain is larger than its own fold-to-fold spread, so it is real." if diff.mean() > diff.std() else
           "That gain is not larger than its own fold-to-fold spread, so it is not established yet.") + "\n")
    parts = []
    for m in ("s", "e"):
        dm = res[m]["aucs"] - r_b0["aucs"]
        parts.append(f"{'stylometry added to b0' if m == 's' else 'embeddings alone, with no genre at all,'} "
                     f"{'gains' if m == 's' else 'differ from b0 by'} {dm.mean():+.3f} ± {dm.std():.3f}")
    add("Where it comes from: " + "; ".join(parts) + ".\n")

    add("### Does the model transfer to new uploads?\n")
    if "be_on_feed" in ext and "be_matched_only_on_feed" in ext:
        a, b = ext["be_on_feed"], ext["be_matched_only_on_feed"]
        add("Here the test is always \"liked test books against **feed** test books\". The first row is model be as above; "
            "the second was trained without seeing a single feed book (unlabeled = matched only).\n")
        add(table(["Model be, trained with", "ROC AUC on liked vs feed", "Median percentile", f"Top {int(TOP_SHARE * 100)}%"], [
            ["matched + feed as unlabeled", pm(a, "auc"), pm(a, "median", True), pm(a, "top", True)],
            ["matched only as unlabeled", pm(b, "auc"), pm(b, "median", True), pm(b, "top", True)],
        ]))
        gap = a["auc"] - b["auc"]
        add(f"Training without feed books changes the AUC on new uploads by {-gap:+.3f}. "
            + ("The model transfers well: what it learned from the matched background also ranks this week's uploads." if gap < 0.03 else
               "Part of the first row's score comes from recognising the feed pile itself; the second row is the honest estimate for new uploads.") + "\n")
    else:
        add("Not enough feed books in the test folds for this check.\n")

    # ---- labels
    add("## My answers from the labelling page\n")
    add(f"Answers so far: **{n_answers}** ({answers.get('never', 0)} would never read, {answers.get('maybe', 0)} maybe, {answers.get('want', 0)} want to read). "
        f"Starred favourites: {len(d['stars'])}.\n")
    if use_labels:
        add(f"There are at least {MIN_LABELS} answers, so they are used: the extra column in the table above, and the extra model **all+labels** "
            f"(model all, where \"would never read\" books count {NEVER_WEIGHT:g} times as negatives, starred books count {STAR_WEIGHT:g} times as positives, "
            "and \"want to read\" books are not used as negatives). Books I already answered are left out of the top-30 list below.\n")
    else:
        add(f"> **Fewer than {MIN_LABELS} answers, so the label step is skipped**: no \"liked vs would never read\" metric, no all+labels model, "
            "and nothing is removed from the top-30 list. Start the page with `node src/label-server.js`, answer some more, and run the report again.\n")
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
    best_m = max(names, key=lambda m: res[m]["auc"])
    score = oof[best_m]
    add(f"## The 30 unlabeled books with the highest score\n")
    add(f"Scores come from the model with the best ROC AUC, **{best_m}**, in the author-grouped test: every book is scored only by models that never saw "
        "the book or its author, averaged over the repeats. The score is between 0 and 1.\n")
    order = [i for i in np.argsort(-score) if y[i] == 0 and not (use_labels and books[i]["label"])]
    add(table(["Score", "Key", "Author", "Title", "Genres", "Source"],
              [[f"{score[i]:.2f}", books[i]["key"], books[i]["author_names"], books[i]["title"], ", ".join(books[i]["genres"]), books[i]["source"]] for i in order[:30]]))
    add("## The 20 liked books with the lowest score\n")
    add("Liked books that the best model would have missed.\n")
    low = [i for i in np.argsort(score) if y[i] == 1][:20]
    add(table(["Score", "Key", "Author", "Title", "Genres"],
              [[f"{score[i]:.2f}", books[i]["key"], books[i]["author_names"], books[i]["title"], ", ".join(books[i]["genres"])] for i in low]))

    # ---- appendix: stylometry
    if styl_meta:
        add("## Appendix: stylometry averages\n")
        add("The numbers measured on each book's full text, averaged per pile.\n")
        S = data.S
        rows_s = []
        for j, f in enumerate(styl_meta):
            cells = [f["what"]]
            for mask in (y == 1, data.source == "matched", data.source == "feed"):
                v = S[mask, j]
                cells.append(f"{np.nanmean(v):.3g}" if np.any(~np.isnan(v)) else "")
            rows_s.append(cells)
        add(table(["Measure", "Liked", "Matched", "Feed"], rows_s))

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(out).replace("\n\n\n", "\n\n").rstrip() + "\n", encoding="utf-8")

    for m in names:
        r = res[m]
        print(f"  {m:11s} AUC {r['auc']:.3f} ± {r['auc_sd']:.3f}   median percentile {100 * r['median']:.1f}%   top 5%: {100 * r['top']:.1f}%   C chosen: {dict(chosen[m])}")
    for k, r in ext.items():
        print(f"  {k}: AUC {r['auc']:.3f} ± {r['auc_sd']:.3f}")
    if pile1:
        print(f"  pile check 1 (feed vs matched): AUC {pile1[0]:.3f}")
    if sims:
        print(f"  pile check 2 (EPUB vs FB2): cosine {np.mean(sims):.4f} on {len(sims)} books")
    print(f"Report written to {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
