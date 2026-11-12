"""Milestone 2, part D. A simple baseline score for "would I like this book?".

Reads data/books.db (filled by the Node scripts in src/), trains three
logistic regression models, tests them with 5-fold cross-validation in two
ways, and writes reports/baseline.md.

Run:  .venv/bin/python model/baseline.py

Positive class  = liked books (table "books"), collections left out.
Unlabeled class = feed books (table "feed_books") that are Russian-language
                  fiction, not in the archive, and not collections.
"""

import json
import math
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold, StratifiedKFold
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / CONFIG.get("dataDir", "data")
DB_PATH = DATA / "books.db"
MAP_PATH = DATA / "genre-map.json"
REPORT = ROOT / "reports" / "baseline.md"

FOLDS = 5
REPEATS = 10  # the 5-fold test is repeated with different shuffles, to steady the numbers
TOP_SHARE = 0.10
MIN_UNLABELED = 300
SEED = 20261112


# ---------------------------------------------------------------- loading


def jlist(s):
    if not s:
        return []
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else []
    except ValueError:
        return []


def norm_author(name):
    words = "".join(ch if ch.isalnum() else " " for ch in name.lower().replace("ё", "е")).split()
    return " ".join(words) if len(words) <= 2 else words[0] + " " + words[-1]


def load_books():
    if not DB_PATH.exists():
        sys.exit("data/books.db not found. Run the Node steps first (node src/run-all.js).")
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    cols = {r["name"] for r in db.execute("PRAGMA table_info(books)")}
    if "genre_groups" not in cols:
        sys.exit('The books table has no genre groups yet. Run "node src/enrich.js" first.')

    def rows(sql):
        return [dict(r) for r in db.execute(sql)]

    liked_all = rows("SELECT * FROM books WHERE parse_status != 'failed'")
    feed_all = rows("SELECT * FROM feed_books WHERE parse_status != 'failed'")
    meta = {r["name"]: json.loads(r["value"]) for r in db.execute("SELECT name, value FROM meta")}
    db.close()

    liked = [b for b in liked_all if not b["is_collection"]]
    unlabeled = [b for b in feed_all if b["lang"] == "ru" and b["is_fiction"] and not b["in_archive"] and not b["is_collection"]]
    counts = {
        "liked_all": len(liked_all),
        "liked_collections": sum(1 for b in liked_all if b["is_collection"]),
        "liked_no_genre": sum(1 for b in liked_all if not jlist(b["genre_groups"])),
        "feed_all": len(feed_all),
        "feed_not_ru": sum(1 for b in feed_all if b["lang"] != "ru"),
        "feed_not_fiction": sum(1 for b in feed_all if b["lang"] == "ru" and not b["is_fiction"]),
        "feed_in_archive": sum(1 for b in feed_all if b["lang"] == "ru" and b["is_fiction"] and b["in_archive"]),
        "feed_collections": sum(1 for b in feed_all if b["lang"] == "ru" and b["is_fiction"] and not b["in_archive"] and b["is_collection"]),
    }
    books = []
    for label, group in ((1, liked), (0, unlabeled)):
        for b in group:
            ids = jlist(b["cat_author_ids"])
            names = jlist(b["authors"])
            # catalog author ids when we have them, otherwise the names from the file
            authors = [f"id{i}" for i in ids] if ids else [f"n:{norm_author(n)}" for n in names if n.strip()]
            cat_series = jlist(b["cat_series"])
            in_catalog = b["cat_author_ids"] is not None or b["cat_genres"] is not None
            year = b["cat_year"] if b["cat_year"] else b["year"]
            books.append({
                "key": b["key"],
                "y": label,
                "title": b["title"] or "",
                "author_names": ", ".join(names),
                "authors": authors or [f"solo:{label}:{b['key']}"],
                "genres": jlist(b["cat_genres"]) or jlist(b["genres"]),
                "groups": jlist(b["genre_groups"]),
                "words": b["words"] or 0,
                "translated": int(b["cat_translated"] if b["cat_translated"] is not None else (b["is_translation"] or 0)),
                "in_series": int(bool(cat_series) if in_catalog else bool(b["series"])),
                "year": int(year) if year else None,
            })
    return books, counts, meta


def author_groups(books):
    """Books that share any author get the same group number (co-authors link groups)."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for b in books:
        first = find(b["authors"][0])
        for a in b["authors"][1:]:
            parent[find(a)] = first
    roots = {}
    return np.array([roots.setdefault(find(b["authors"][0]), len(roots)) for b in books])


# ---------------------------------------------------------------- features


class Features:
    """Builds the feature table for one model: 'a', 'b' or 'c'.

    There is also 'x': model b without the two year columns. It is not one of
    the three models; it is a check on how much "year" alone is doing."""

    def __init__(self, books, group_names):
        self.books = books
        self.group_names = group_names
        col = {g: i for i, g in enumerate(group_names)}
        self.G = np.zeros((len(books), len(group_names)))
        for i, b in enumerate(books):
            for g in b["groups"]:
                self.G[i, col[g]] = 1.0
        self.y = np.array([b["y"] for b in books])
        self.log_words = np.array([math.log10(max(b["words"], 100)) for b in books])
        self.translated = np.array([b["translated"] for b in books], dtype=float)
        self.in_series = np.array([b["in_series"] for b in books], dtype=float)
        self.year = np.array([b["year"] if b["year"] else np.nan for b in books], dtype=float)

    def names(self, model):
        n = list(self.group_names)
        if model == "x":
            n += ["length (log of words)", "translated", "part of a series"]
        if model in ("b", "c"):
            n += ["length (log of words)", "translated", "part of a series", "year", "year unknown"]
        if model == "c":
            n += ["known author"]
        return n

    def known_author(self, train):
        """1 when one of the book's authors wrote a liked book in the training part.

        The book itself is never counted, so a liked training book only gets 1
        when the author has ANOTHER liked book in the training part. Books in
        the test part are scored against the training part only."""
        count = Counter()
        for i in train:
            if self.y[i] == 1:
                for a in set(self.books[i]["authors"]):
                    count[a] += 1
        in_train = np.zeros(len(self.books), dtype=bool)
        in_train[train] = True
        out = np.zeros(len(self.books))
        for i, b in enumerate(self.books):
            own = 1 if (in_train[i] and self.y[i] == 1) else 0
            out[i] = 1.0 if any(count[a] - own > 0 for a in set(b["authors"])) else 0.0
        return out

    def build(self, model, train):
        """Returns the full feature table; numbers are scaled using the training part only."""
        parts = [self.G]
        if model == "x":
            words = StandardScaler().fit(self.log_words[train, None]).transform(self.log_words[:, None])
            parts += [words, self.translated[:, None], self.in_series[:, None]]
        if model in ("b", "c"):
            median_year = np.nanmedian(self.year[train]) if np.any(~np.isnan(self.year[train])) else 2020.0
            missing = np.isnan(self.year).astype(float)
            year = np.where(np.isnan(self.year), median_year, self.year)
            numeric = np.column_stack([self.log_words, year])
            numeric = StandardScaler().fit(numeric[train]).transform(numeric)
            parts += [numeric[:, :1], self.translated[:, None], self.in_series[:, None], numeric[:, 1:], missing[:, None]]
        if model == "c":
            parts.append(self.known_author(train)[:, None])
        return np.hstack(parts)


def fit(X, y):
    return LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000).fit(X, y)


# ---------------------------------------------------------------- testing


def top_share(y, score, rng):
    """Share of the liked books that land in the top 10% of the ranking (ties broken at random)."""
    k = max(1, int(round(len(y) * TOP_SHARE)))
    order = np.lexsort((rng.random(len(y)), -score))
    return y[order[:k]].sum() / max(1, y.sum())


def cross_validate(feats, groups):
    y = feats.y
    n = len(y)
    results = {}
    oof = {}
    for split in ("random", "grouped"):
        for model in ("a", "b", "c", "x"):
            aucs, tops, best, sums = [], [], [], np.zeros(n)
            for rep in range(REPEATS):
                rng = np.random.default_rng(SEED + rep)
                if split == "random":
                    folds = StratifiedKFold(FOLDS, shuffle=True, random_state=SEED + rep).split(np.zeros(n), y)
                else:
                    folds = StratifiedGroupKFold(FOLDS, shuffle=True, random_state=SEED + rep).split(np.zeros(n), y, groups)
                for train, test in folds:
                    if split == "grouped":
                        assert not set(groups[train]) & set(groups[test]), "an author is on both sides of the split"
                    X = feats.build(model, train)
                    score = fit(X[train], y[train]).predict_proba(X[test])[:, 1]
                    sums[test] += score
                    aucs.append(roc_auc_score(y[test], score))
                    tops.append(top_share(y[test], score, rng))
                    k = max(1, int(round(len(test) * TOP_SHARE)))
                    best.append(min(k, y[test].sum()) / y[test].sum())
            results[(split, model)] = {
                "auc": float(np.mean(aucs)), "auc_sd": float(np.std(aucs)),
                "top": float(np.mean(tops)), "top_sd": float(np.std(tops)),
                "best": float(np.mean(best)),
            }
            oof[(split, model)] = sums / REPEATS
    return results, oof


# ---------------------------------------------------------------- report


def cell(v):
    return " ".join(str("" if v is None else v).replace("|", "\\|").split())


def table(headers, rows):
    if not rows:
        return "_none_\n"
    out = ["| " + " | ".join(cell(h) for h in headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    out += ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def pct(x):
    return f"{100 * x:.1f}%"


def main():
    books, counts, meta = load_books()
    genre_map = json.loads(MAP_PATH.read_text(encoding="utf-8")) if MAP_PATH.exists() else {"groups": {}, "unmapped": []}
    group_names = list(genre_map["groups"]) or sorted({g for b in books for g in b["groups"]})
    n_pos = sum(b["y"] for b in books)
    n_unl = len(books) - n_pos
    if n_pos < FOLDS or n_unl < FOLDS:
        sys.exit(f"Not enough books to test: {n_pos} liked, {n_unl} unlabeled.")

    feats = Features(books, group_names)
    y = feats.y
    groups = author_groups(books)
    results, oof = cross_validate(feats, groups)

    everything = np.arange(len(books))
    model_a = fit(feats.build("a", everything), y)
    model_b = fit(feats.build("b", everything), y)
    model_c = fit(feats.build("c", everything), y)
    known = feats.known_author(everything)

    out = []
    add = out.append
    add("# Baseline score\n")
    add("Built by `model/baseline.py` from `data/books.db`. The score answers one question: "
        "*does this book look more like the books I liked than like an average new book in the feed?* "
        "It only looks at simple facts about a book (genre, length, and so on), not at the text.\n")

    if n_unl < MIN_UNLABELED:
        add(f"> **WARNING: the unlabeled set has only {n_unl} books, fewer than {MIN_UNLABELED}.** "
            "The numbers below are shaky. Run the feed collector on more days (`node src/fetch-feed.js`) and build this report again.\n")

    add("## Books used\n")
    add(table(["Group", "Books"], [
        ["Liked books in the archive", counts["liked_all"]],
        ["... left out as collections", counts["liked_collections"]],
        ["**Liked books used (positive class)**", f"**{n_pos}**"],
        ["Feed books collected", counts["feed_all"]],
        ["... left out: not Russian-language", counts["feed_not_ru"]],
        ["... left out: not fiction", counts["feed_not_fiction"]],
        ["... left out: already in the archive", counts["feed_in_archive"]],
        ["... left out as collections", counts["feed_collections"]],
        ["**Feed books used (unlabeled class)**", f"**{n_unl}**"],
        ["Different author groups (co-authors count as one group)", len(set(groups))],
    ]))
    add(f"Liked books that still have no genre: {counts['liked_no_genre']} of {counts['liked_all']}.\n")
    add("\"Unlabeled\" does not mean \"disliked\". These are simply new books nobody has judged yet; some of them would be liked. "
        "So the model can never look perfect, and that is fine.\n")
    if meta.get("has_catalog"):
        add("Genres, year, series and \"translated\" come from the Flibusta catalog for both liked and feed books, "
            "so both groups are described by the same source. The codes written inside a feed file are used only when the catalog has nothing for that book.\n")
    else:
        add("> **Note:** the catalog (`data/catalog.db`) was not available, so genres come only from the book files.\n")

    add("## Genre groups: liked vs unlabeled\n")
    add("A book can be in several groups, so the columns add up to more than 100%.\n")
    G = feats.G
    share_rows = []
    for j, g in enumerate(group_names):
        lp, up = G[y == 1, j].mean(), G[y == 0, j].mean()
        if lp == 0 and up == 0:
            continue
        share_rows.append((lp - up, [g, int(G[y == 1, j].sum()), pct(lp), int(G[y == 0, j].sum()), pct(up), f"{100 * (lp - up):+.1f}"]))
    share_rows.sort(key=lambda r: -r[0])
    add(table(["Genre group", "Liked books", "Liked share", "Unlabeled books", "Unlabeled share", "Difference (points)"], [r[1] for r in share_rows]))
    none_l = int((G[y == 1].sum(axis=1) == 0).sum())
    none_u = int((G[y == 0].sum(axis=1) == 0).sum())
    add(f"Books with no genre group at all: {none_l} liked, {none_u} unlabeled.\n")

    unmapped_used = meta.get("unmapped_genres") or []
    unmapped_all = genre_map.get("unmapped") or []
    add("### Unmapped genre codes\n")
    if not unmapped_all and not unmapped_used:
        add("None. Every genre code is mapped to a group.\n")
    else:
        used = {u["code"]: u["books"] for u in unmapped_used}
        add("These codes are not in `data/genre-map.json` and are ignored by the model. "
            "\"Books\" counts only books whose genres really came from that code.\n")
        add(table(["Code", "Books"], [[f"`{c}`", used.get(c, 0)] for c in sorted(set(unmapped_all) | set(used))]))

    add("## How well the models rank\n")
    add("Three logistic regression models:\n")
    add("- **a** - genre groups only\n- **b** - genre groups + length, translated or not, part of a series or not, year\n- **c** - model b + \"known author\" (the author has another liked book)\n")
    add(f"Each model is tested with {FOLDS}-fold cross-validation, repeated {REPEATS} times with different shuffles. Two kinds of split:\n")
    add("- **random** - books are dealt into folds at random. An author can be on both sides, so this flatters the model.\n"
        "- **author-grouped** - all books of an author (and co-authors) stay together on one side. "
        "This is the honest test for finding *new* authors.\n")
    add("The \"known author\" feature is computed from the training part only, and a book never counts as its own evidence.\n")
    r0 = results[("random", "a")]
    add(table(["Model", "Split", "ROC AUC", f"Liked test books in top {int(TOP_SHARE * 100)}%"], [
        [{"a": "a: genres", "b": "b: + length, translated, series, year", "c": "c: + known author"}[m],
         {"random": "random", "grouped": "author-grouped"}[s],
         f"{results[(s, m)]['auc']:.3f} ± {results[(s, m)]['auc_sd']:.3f}",
         f"{pct(results[(s, m)]['top'])} ± {100 * results[(s, m)]['top_sd']:.1f}"]
        for s in ("random", "grouped") for m in ("a", "b", "c")
    ]))
    add("How to read the numbers:\n")
    add("- **ROC AUC**: 0.5 is coin-flipping, 1.0 is a perfect ranking. The number after ± shows how much it moves from fold to fold.\n"
        f"- **Top {int(TOP_SHARE * 100)}%**: the test books are sorted by score and the top tenth is taken. The figure is the share of all liked test books found there. "
        f"Guessing gives {pct(TOP_SHARE)}. Because about {pct(n_pos / len(books))} of the test books are liked, "
        f"even a perfect ranking can reach only about {pct(r0['best'])}.\n")
    gb, gc = results[("grouped", "b")], results[("grouped", "c")]
    rb, rc = results[("random", "b")], results[("random", "c")]
    add(f"- **Known author**: in the random split it moves AUC from {rb['auc']:.3f} to {rc['auc']:.3f}. "
        f"In the author-grouped split it cannot help (from {gb['auc']:.3f} to {gc['auc']:.3f}), because no test author is ever seen in training. "
        "So this feature is about following authors you already read, not about discovering new ones.\n")

    j_year = feats.names("b").index("year")
    add("### A caution about \"year\"\n")
    years_l = feats.year[y == 1]
    years_u = feats.year[y == 0]
    gx = results[("grouped", "x")]
    add(f"The liked books were collected over many years (median year {np.nanmedian(years_l):.0f}), while the unlabeled books are this week's uploads "
        f"(median year {np.nanmedian(years_u):.0f}). So \"year\" partly tells the model *which pile a book came from*, not *whether it is good* "
        f"(its weight in model b is {model_b.coef_[0][j_year]:+.2f}). As a check, here is model b with the year taken out:\n")
    add(table(["Model", "Split", "ROC AUC", f"Liked test books in top {int(TOP_SHARE * 100)}%"], [
        [name, {"random": "random", "grouped": "author-grouped"}[s],
         f"{results[(s, m)]['auc']:.3f} ± {results[(s, m)]['auc_sd']:.3f}",
         f"{pct(results[(s, m)]['top'])} ± {100 * results[(s, m)]['top_sd']:.1f}"]
        for s in ("random", "grouped") for m, name in (("a", "a: genres"), ("x", "b without year"), ("b", "b: with year"))
    ]))
    add(f"In the author-grouped test the year changes AUC by {gb['auc'] - gx['auc']:+.3f}, which is far less than the fold-to-fold noise. "
        f"The step from model a to model b ({gb['auc'] - results[('grouped', 'a')]['auc']:+.3f}) therefore comes from length, translation and series, not from the year. "
        "Keep an eye on this as the feed collection grows.\n")

    add("## Which genre groups push the score\n")
    add("Weights of model a (genre groups only), trained on all books. A positive weight pushes a book towards \"liked\", a negative one away. "
        "Groups with very few books have unreliable weights. "
        "\"nonfiction\" is high only because non-fiction was filtered out of the unlabeled set but not out of the liked books; ignore it.\n")
    coef = [(model_a.coef_[0][j], g, int(G[y == 1, j].sum()), int(G[y == 0, j].sum())) for j, g in enumerate(group_names) if G[:, j].sum() > 0]
    coef.sort(reverse=True)
    add("Pushing the score up most:\n")
    add(table(["Genre group", "Weight", "Liked books", "Unlabeled books"], [[g, f"{w:+.2f}", lp, up] for w, g, lp, up in coef[:8] if w > 0]))
    add("Pushing the score down most:\n")
    add(table(["Genre group", "Weight", "Liked books", "Unlabeled books"], [[g, f"{w:+.2f}", lp, up] for w, g, lp, up in coef[::-1][:8] if w < 0]))
    add("Other features in model c, trained on all books (numbers are scaled, so sizes can be compared roughly):\n")
    names_c = feats.names("c")
    add(table(["Feature", "Weight"], [[nm, f"{model_c.coef_[0][j]:+.2f}"] for j, nm in enumerate(names_c) if j >= len(group_names)]))

    score = oof[("grouped", "b")]
    add("## The 20 unlabeled books with the highest score\n")
    add("Scores here and in the next table come from model b in the author-grouped test: every book is scored by models "
        "that never saw the book or its author. The score is between 0 and 1; higher means more like the liked books. "
        "\"Known author\" marks authors who already have a liked book.\n")
    unl = [i for i in np.argsort(-score) if y[i] == 0][:20]
    add(table(["Score", "Key", "Author", "Title", "Genres", "Known author"],
              [[f"{score[i]:.2f}", books[i]["key"], books[i]["author_names"], books[i]["title"], ", ".join(books[i]["genres"]), "yes" if known[i] else ""] for i in unl]))
    add("## The 20 liked books with the lowest score\n")
    add("These are liked books the model would have missed. They show what the simple features cannot see.\n")
    low = [i for i in np.argsort(score) if y[i] == 1][:20]
    add(table(["Score", "Key", "Author", "Title", "Genres"],
              [[f"{score[i]:.2f}", books[i]["key"], books[i]["author_names"], books[i]["title"], ", ".join(books[i]["genres"])] for i in low]))

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(out).replace("\n\n\n", "\n\n").rstrip() + "\n", encoding="utf-8")

    print(f"Baseline: {n_pos} liked books, {n_unl} unlabeled books, {len(set(groups))} author groups")
    for s in ("random", "grouped"):
        for m in ("a", "b", "c"):
            r = results[(s, m)]
            print(f"  model {m}, {s:8s} split: AUC {r['auc']:.3f}, liked in top 10%: {pct(r['top'])}")
    if n_unl < MIN_UNLABELED:
        print(f"  WARNING: only {n_unl} unlabeled books (fewer than {MIN_UNLABELED}).")
    print(f"Report written to {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
