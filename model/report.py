"""Builds reports/model.html: what the model learned from, what it uses, how well it ranks.

One file, charts as inline SVG, no libraries and no script, so it also opens in
Safari 9. It is rebuilt at the end of every training (model/recommend.py train).

Run alone:  .venv/bin/python model/report.py     (uses the numbers of the last training)
"""

import html
import json
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / CONFIG.get("dataDir", "data")
REPORT_DATA = DATA / "model" / "report.json"
OUT = ROOT / "reports" / "model.html"

W = 720  # width of every chart, in SVG units
esc = html.escape

STYLE = """
* { -webkit-box-sizing: border-box; box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: #f6f4ef; color: #22201c; font: 17px/1.5 -apple-system, "Helvetica Neue", Arial, sans-serif; }
main { display: block; max-width: 860px; margin: 0 auto; padding: 20px 16px 80px; }
h1 { font-size: 26px; margin: 0 0 4px; }
h2 { font-size: 20px; margin: 36px 0 6px; }
p { margin: 8px 0; }
a { color: #2f6f4f; }
.muted { color: #6d675d; font-size: 15px; }
.back { display: inline-block; margin: 0 0 14px; padding: 11px 14px; border: 1px solid #e2dccf; border-radius: 10px; background: #fffdf8; color: #22201c; text-decoration: none; }
.card { background: #fffdf8; border: 1px solid #e2dccf; border-radius: 12px; padding: 14px 16px; margin: 12px 0; }
.tiles { display: -webkit-flex; display: flex; -webkit-flex-wrap: wrap; flex-wrap: wrap; margin: 0 -6px; }
.tile { -webkit-flex: 1 1 150px; flex: 1 1 150px; margin: 6px; padding: 12px 14px; background: #fffdf8; border: 1px solid #e2dccf; border-radius: 12px; }
.tile b { display: block; font-size: 26px; line-height: 1.2; }
.tile span { color: #6d675d; font-size: 14px; }
table { border-collapse: collapse; width: 100%; font-size: 15px; }
th, td { text-align: left; padding: 6px 10px 6px 0; border-bottom: 1px solid #e2dccf; vertical-align: top; }
th { color: #6d675d; font-weight: 400; }
td.n, th.n { text-align: right; white-space: nowrap; }
ul { margin: 6px 0; padding-left: 20px; }
.legend { font-size: 14px; color: #6d675d; margin: 4px 0 8px; }
.legend i { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin: 0 6px -1px 0; }
.legend span { margin-right: 18px; white-space: nowrap; }
svg { display: block; width: 100%; height: auto; }
svg text { font: 13px -apple-system, "Helvetica Neue", Arial, sans-serif; fill: #22201c; }
svg text.m { fill: #6d675d; }
svg .grid { stroke: #e2dccf; stroke-width: 1; }
svg .axis { stroke: #a39c8e; stroke-width: 1; }
.c1 { fill: #2a78d6; } .c2 { fill: #eb6834; } .c0 { fill: #a39c8e; }
i.c1 { background: #2a78d6; } i.c2 { background: #eb6834; } i.c0 { background: #a39c8e; }
.ring { fill: #fffdf8; stroke: #2a78d6; stroke-width: 2; }
.dot { stroke: #fffdf8; stroke-width: 2; }
.say li { margin: 6px 0; }
@media (prefers-color-scheme: dark) {
  body { background: #1b1a17; color: #ece8df; }
  a { color: #7fc8a0; }
  .muted, .tile span, th, .legend { color: #a39c8e; }
  .card, .tile, .back { background: #25231f; border-color: #3a372f; color: #ece8df; }
  th, td { border-color: #3a372f; }
  svg text { fill: #ece8df; }
  svg text.m { fill: #a39c8e; }
  svg .grid { stroke: #3a372f; }
  svg .axis { stroke: #6d675d; }
  .c1 { fill: #3987e5; } .c2 { fill: #d95926; } .c0 { fill: #6d675d; }
  i.c1 { background: #3987e5; } i.c2 { background: #d95926; } i.c0 { background: #6d675d; }
  .ring { fill: #25231f; stroke: #3987e5; }
  .dot { stroke: #25231f; }
}
"""


def svg(height, body, label):
    return f'<svg viewBox="0 0 {W} {height}" role="img" aria-label="{esc(label)}">{"".join(body)}</svg>'


def bar(x, y, w, h, cls, tip):
    """A horizontal bar from x to the right, the far end rounded."""
    w = max(w, 1.0)
    r = min(4.0, w / 2, h / 2)
    d = f"M{x:.1f},{y:.1f}h{w - r:.1f}a{r},{r} 0 0 1 {r},{r}v{h - 2 * r:.1f}a{r},{r} 0 0 1 -{r},{r}h-{w - r:.1f}z"
    return f'<path class="{cls}" d="{d}"><title>{esc(tip)}</title></path>'


def bar_left(x, y, w, h, cls, tip):
    """A horizontal bar from x to the left, the far end rounded."""
    w = max(w, 1.0)
    r = min(4.0, w / 2, h / 2)
    d = f"M{x:.1f},{y:.1f}h-{w - r:.1f}a{r},{r} 0 0 0 -{r},{r}v{h - 2 * r:.1f}a{r},{r} 0 0 0 {r},{r}h{w - r:.1f}z"
    return f'<path class="{cls}" d="{d}"><title>{esc(tip)}</title></path>'


# ---------------------------------------------------------------- the charts


def quality_chart(d):
    """Ranking quality of each group of factors alone and together, with and without the book's own rating."""
    kind = d["info"]["kind"]
    rows = [(d["group_names"][g], g) for g in ("text", "style", "genre", "ratings", "record")]
    rows += [("the book file alone (text + style + genre)", "file"), ("all five groups together", "all")]
    left, right, top, bh, gap, rowh = 250, 60, 26, 13, 2, 46
    x0, x1 = left, W - right
    sx = lambda v: x0 + (max(v, 0.5) - 0.5) / 0.5 * (x1 - x0)  # noqa: E731
    out = []
    for v in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
        out.append(f'<line class="{"axis" if v == 0.5 else "grid"}" x1="{sx(v):.1f}" y1="{top - 6}" x2="{sx(v):.1f}" y2="{top + rowh * len(rows)}"/>')
        out.append(f'<text class="m" x="{sx(v):.1f}" y="14" text-anchor="middle">{int(v * 100)}%{" (chance)" if v == 0.5 else ""}</text>')
    table = []
    for i, (name, key) in enumerate(rows):
        q = d["quality"][f"{kind}:{key}"]
        y = top + i * rowh + 6
        out.append(f'<text x="{left - 10}" y="{y + bh + 5}" text-anchor="end"{" font-weight=\"600\"" if key == "all" else ""}>{esc(name)}</text>')
        for j, (cond, cls, what) in enumerate((("seen", "c1", "rated books"), ("hidden", "c2", "books without votes"))):
            v = q[cond]["auc"]
            yy = y + j * (bh + gap)
            out.append(bar(x0, yy, sx(v) - x0, bh, cls, f"{name}, {what}: {100 * v:.1f}% (± {100 * q[cond]['auc_sd']:.1f})"))
            out.append(f'<text x="{sx(v) + 6:.1f}" y="{yy + bh - 2}">{100 * v:.0f}%</text>')
        table.append(f'<tr><td>{esc(name)}</td><td class="n">{100 * q["seen"]["auc"]:.1f}% ± {100 * q["seen"]["auc_sd"]:.1f}</td>'
                     f'<td class="n">{100 * q["hidden"]["auc"]:.1f}% ± {100 * q["hidden"]["auc_sd"]:.1f}</td></tr>')
    legend = ('<div class="legend"><span><i class="c1"></i>rated books: the model sees the book\'s own rating</span>'
              '<span><i class="c2"></i>books without votes: the book\'s own rating is hidden, as for a brand-new book</span></div>')
    tbl = ('<table><tr><th>Factors</th><th class="n">Rated books</th><th class="n">Books without votes</th></tr>' + "".join(table) + "</table>")
    return legend + svg(top + rowh * len(rows) + 6, out, "Ranking quality by group of factors") + tbl


def distribution_chart(d):
    """Model scores (from the test side) for four kinds of books, as small histograms on one shared axis."""
    books = d["books"]
    sets = [
        ("My library books (target 5)", [b["seen"] for b in books if b["role"] == "library"], "c1"),
        ("Background books (target 2)", [b["seen"] for b in books if b["role"] == "background"], "c0"),
        ("Books I scored 4 or 5", [b["seen"] for b in books if b["role"] == "scored" and b["score"] >= 4], "c1"),
        ("Books I scored 1 or 2", [b["seen"] for b in books if b["role"] == "scored" and b["score"] <= 2], "c2"),
    ]
    everything = [v for _, vals, _ in sets for v in vals] or [1.0, 5.0]
    lo, hi = min(1.0, np.floor(min(everything) * 2) / 2), max(5.0, np.ceil(max(everything) * 2) / 2)
    step = 0.1
    edges = np.arange(lo, hi + step / 2, step)
    left, right, panel, head = 16, 16, 86, 22
    sx = lambda v: left + (v - lo) / (hi - lo) * (W - left - right)  # noqa: E731
    out = []
    for i, (name, vals, cls) in enumerate(sets):
        y0 = i * (panel + head) + head
        base = y0 + panel - 16
        med = f", middle value {np.median(vals):.2f}" if vals else ""
        out.append(f'<text x="{left}" y="{y0 - 6}" font-weight="600">{esc(name)}</text>')
        out.append(f'<text class="m" x="{W - right}" y="{y0 - 6}" text-anchor="end">{len(vals)} {"book" if len(vals) == 1 else "books"}{med}</text>')
        out.append(f'<line class="axis" x1="{left}" y1="{base}" x2="{W - right}" y2="{base}"/>')
        if not vals:
            out.append(f'<text class="m" x="{W / 2}" y="{base - 22}" text-anchor="middle">no books yet</text>')
        elif len(vals) < 25:  # a few books: one dot per book
            for v in sorted(vals):
                out.append(f'<circle class="{cls} dot" cx="{sx(v):.1f}" cy="{base - 12}" r="6"><title>{v:.2f}</title></circle>')
        else:
            counts, _ = np.histogram(vals, bins=edges)
            top = counts.max()
            bw = sx(lo + step) - sx(lo)
            for c, e in zip(counts, edges[:-1]):
                if c:
                    h = max(1.5, c / top * (panel - 24))
                    out.append(f'<rect class="{cls}" x="{sx(e) + 1:.1f}" y="{base - h:.1f}" width="{bw - 2:.1f}" height="{h:.1f}" rx="1.5">'
                               f'<title>{e:.1f} to {e + step:.1f}: {c} books</title></rect>')
        if vals:
            m = float(np.median(vals))
            out.append(f'<line class="axis" x1="{sx(m):.1f}" y1="{y0 + 2}" x2="{sx(m):.1f}" y2="{base}" stroke-dasharray="3 3"/>')
        for t in np.arange(np.ceil(lo), hi + 0.01, 1.0):
            out.append(f'<text class="m" x="{sx(t):.1f}" y="{base + 14}" text-anchor="middle">{t:.0f}</text>')
    return svg(len(sets) * (panel + head) + 4, out, "Distribution of model scores") + \
        '<p class="muted">Across: the score the model predicted (1 to 5). The dashed line is the middle value of the group. Each book was scored by a model that had not seen it.</p>'


def effects_chart(d):
    """The 15 factors that push the predicted score up most and down most."""
    eff = sorted(d["effects"], key=lambda e: -e["effect"])
    up = [e for e in eff if e["effect"] > 0][:15]
    down = [e for e in reversed(eff) if e["effect"] < 0][:15]
    biggest = max([abs(e["effect"]) for e in up + down] or [1.0])
    rowh, bh, labw = 24, 14, 440
    out_html = []
    for title, rows, cls, sign in (("Push the score up", up, "c1", "+"), ("Push the score down", down, "c2", "−")):
        out = []
        for i, e in enumerate(rows):
            y = i * rowh + 4
            name = e["name"] + (f" ({e['hint']})" if e["hint"] else "")
            how = "the book has it, against a book that has not" if e["yes_no"] else "a high value against a low value"
            out.append(f'<text x="{labw - 10}" y="{y + bh - 2}" text-anchor="end">{esc(name if len(name) <= 62 else name[:60] + "…")}</text>')
            w = abs(e["effect"]) / biggest * (W - labw - 60)
            out.append(bar(labw, y, w, bh, cls, f"{name}: {sign}{abs(e['effect']):.2f} points ({how})"))
            out.append(f'<text x="{labw + w + 6:.1f}" y="{y + bh - 2}">{sign}{abs(e["effect"]):.2f}</text>')
        out_html.append(f"<h3 style=\"font-size:17px;margin:14px 0 4px\">{title}</h3>" + svg(len(rows) * rowh + 6, out, title))
    return "".join(out_html) + ('<p class="muted">The number is the change of the predicted score, in points, when this one factor goes from a low value '
                                "(lower than in 9 books of 10) to a high value (higher than in 9 books of 10) and everything else stays as it is. "
                                'For a yes-or-no factor such as a genre: a book that has it against a book that has not. A "direction" of the text is a '
                                "mix of meanings found by the computer; it has no name, so the genre it goes with most is given when there is one.</p>")


def scatter_chart(d):
    """My scores against what the model predicted for the same books while they were on the test side."""
    pts = [b for b in d["books"] if b["role"] == "scored"]
    if not pts:
        return '<p class="muted">No scored books yet.</p>'
    left, right, top, bottom = 56, 20, 16, 44
    H = 360
    vals = [b["seen"] for b in pts]
    lo, hi = min(1.0, np.floor(min(vals))), max(5.0, np.ceil(max(vals)))
    sx = lambda s: left + (s - 0.5) / 5.0 * (W - left - right)  # noqa: E731
    sy = lambda v: top + (hi - v) / (hi - lo) * (H - top - bottom)  # noqa: E731
    out = []
    for t in np.arange(lo, hi + 0.01, 1.0):
        out.append(f'<line class="grid" x1="{left}" y1="{sy(t):.1f}" x2="{W - right}" y2="{sy(t):.1f}"/>')
        out.append(f'<text class="m" x="{left - 8}" y="{sy(t) + 4:.1f}" text-anchor="end">{t:.0f}</text>')
    for s in range(1, 6):
        out.append(f'<text class="m" x="{sx(s):.1f}" y="{H - bottom + 18}" text-anchor="middle">{s}</text>')
    out.append(f'<line class="axis" x1="{left}" y1="{H - bottom}" x2="{W - right}" y2="{H - bottom}"/>')
    out.append(f'<text class="m" x="{(left + W - right) / 2}" y="{H - 6}" text-anchor="middle">my score</text>')
    out.append(f'<text class="m" x="14" y="{(top + H - bottom) / 2}" text-anchor="middle" transform="rotate(-90 14 {(top + H - bottom) / 2})">predicted score</text>')
    seen = {}
    for b in sorted(pts, key=lambda b: (b["score"], b["seen"])):
        k = seen.get(b["score"], 0)
        seen[b["score"]] = k + 1
        x = sx(b["score"]) + ((k % 7) - 3) * 9  # books with the same score sit side by side
        tip = f"{b.get('authors') or ''} — {b.get('title') or ''}: my score {b['score']}, predicted {b['seen']:.2f}, {'after reading' if b['read'] else 'from the card'}"
        cls = "c1 dot" if b["read"] else "ring"
        out.append(f'<circle class="{cls}" cx="{x:.1f}" cy="{sy(b["seen"]):.1f}" r="6"><title>{esc(tip)}</title></circle>')
    legend = ('<div class="legend"><span><svg viewBox="0 0 14 14" style="display:inline-block;width:14px;height:14px;margin:0 6px -2px 0"><circle class="c1" cx="7" cy="7" r="6"/></svg>scored after reading</span>'
              '<span><svg viewBox="0 0 14 14" style="display:inline-block;width:14px;height:14px;margin:0 6px -2px 0"><circle class="ring" cx="7" cy="7" r="5"/></svg>scored from the card only</span></div>')
    rows = "".join(f'<tr><td>{esc(b.get("authors") or "")}</td><td>{esc(b.get("title") or "")}</td><td class="n">{b["score"]}</td><td class="n">{b["seen"]:.2f}</td>'
                   f'<td>{"after reading" if b["read"] else "from the card"}</td></tr>' for b in sorted(pts, key=lambda b: -b["seen"]))
    note = "" if len(pts) >= d["info"]["min_scores"] else \
        f'<p class="muted">Only {len(pts)} scored {"book" if len(pts) == 1 else "books"} so far: too few to judge the model by. The agreement number appears from {d["info"]["min_scores"]} scores on.</p>'
    return legend + svg(H, out, "My scores against the predictions") + note + \
        '<table><tr><th>Author</th><th>Title</th><th class="n">My score</th><th class="n">Predicted</th><th>Given</th></tr>' + rows + "</table>"


# ---------------------------------------------------------------- the page


def sentences(d):
    i, q = d["info"], d["quality"]
    best = f"{i['kind']}:all"
    out = [
        f"It can sort new books so that books like the ones in my library come first: put one book I like and one ordinary book side by side, "
        f"and it picks the right one about {100 * q[best]['seen']['auc']:.0f} times in 100 when the book has reader votes and "
        f"{100 * q[best]['hidden']['auc']:.0f} times in 100 when it has none (50 would be guessing).",
        "It can score a brand-new book that nobody has rated: such a book sits at the catalog average and is judged by its text, style, genre and "
        "the record of its author and series, and it is scored again when votes arrive.",
    ]
    if i["spearman"] is None:
        out.append(f"It cannot yet tell a book I would score 5 from one I would score 3: it has only {i['scored']} of my scores, so today it mostly knows "
                   f"\"looks like my library\" from \"looks ordinary\", and its predicted scores stay near the middle; this is measured from {i['min_scores']} scores on.")
    else:
        out.append(f"It cannot read a book for me: on books it had not seen its ranking agrees with my scores only partly (Spearman {i['spearman']:.2f}, "
                   f"where 1 is full agreement and 0 is none), and my average score for its top 10 was {i['top10_avg']:.1f}.")
    return out


def build(d):
    i = d["info"]
    ratings = d.get("ratings") or {}
    check = d.get("fiction_check") or {}
    trained = datetime.fromisoformat(i["trained_at"]).astimezone().strftime("%d %b %Y, %H:%M")
    w = i["weights"]
    tiles = [
        (f"{100 * i['auc']:.0f}%", "ranking quality, rated books"),
        (f"{100 * i['auc_hidden']:.0f}%", "ranking quality, books without votes"),
        ("—" if i["spearman"] is None else f"{i['spearman']:.2f}", f"agreement with my scores (Spearman){'' if i['spearman'] is not None else ', from ' + str(i['min_scores']) + ' scores'}"),
        ("—" if i["top10_avg"] is None else f"{i['top10_avg']:.1f}", "my average score for its top 10"),
    ]
    by_value = ", ".join(f"{n} × score {v}" for v, n in sorted(i["scores_by_value"].items()) if n) or "none yet"
    data_rows = [
        ("My kept library books", i["library"], "5", w["library"]),
        (f"Books I scored after reading ({by_value} in all)", i["scored_read"], "my score", w["read"]),
        ("Books I scored from the card only", i["scored_card"], "my score", w["card"]),
        ("\"Would never read\" answers from the old labelling page", i["never"], "1", w["never"]),
        ("Background: random catalog books", i["background_matched"], "2", w["background"]),
        ("Background: catalog books with ratings like my library books", i["background_rated"], "2", w["background"]),
        ("Background: feed books I did not answer", i["background_feed"], "2", w["background"]),
    ]
    groups = d["groups"]
    used = []
    for g in ("text", "style", "genre", "ratings", "record"):
        names = groups[g]
        if g == "text":
            body = f"{len(names)} numbers: the meaning of the whole text ({len(names) // 2} directions) and of the opening, the first 25,000 words ({len(names) // 2} directions), from the embedding model bge-m3."
        else:
            body = "; ".join(n.split(": ", 1)[-1] if g in ("style", "genre") else n for n in names) + "."
        used.append(f"<tr><td><b>{esc(d['group_names'][g])}</b></td><td class=\"n\">{len(names)}</td><td>{esc(body)}</td></tr>")
    unused = [
        "the year the book was written or added",
        "the text of reader comments and reviews, and how many there are",
        "the annotation and the cover",
        "the name of the author as such (only the marks of the author's earlier books)",
        "the marks of single readers who like the same books as I do",
        "the publisher, the translator's name, the file size",
        "how often a book is downloaded",
    ]
    kinds = "".join(f"<tr><td>{esc(d['kinds'][k])}{' — kept' if k == i['kind'] else ''}</td><td class=\"n\">{100 * a['seen']:.1f}%</td><td class=\"n\">{100 * a['hidden']:.1f}%</td></tr>"
                    for k, a in i["auc_by_kind"].items())
    check_text = ""
    if check.get("ready"):
        check_text = (f"<p class=\"muted\">Before a book is scored, a separate text check (fiction against non-fiction) looks at it: trained on {check['fiction']} fiction and "
                      f"{check['nonfiction']} non-fiction books, accuracy {100 * check['accuracy']:.1f}% in an author-grouped test; it catches {check['caught']} of "
                      f"{check['nonfiction']} non-fiction books and wrongly sets aside {check['lost']} of {check['fiction']} fiction books.</p>")
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Model report</title>
<style>{STYLE}</style></head><body><main>
<a class="back" href="/">← Recommendations</a>
<h1>Model report</h1>
<p class="muted">Trained {esc(trained)} · {esc(i['kind_name'])} · rebuilt after every training.</p>

<div class="tiles">{"".join(f'<div class="tile"><b>{esc(v)}</b><span>{esc(t)}</span></div>' for v, t in tiles)}</div>
<p class="muted">Ranking quality: take one book I like (my library, or scored 4–5) and one book I do not (background, or scored 1–2); the number says how often the model puts the right one higher. 50% is guessing, 100% is perfect. Measured on authors the model had not seen.</p>

<h2>What the model can and cannot do</h2>
<div class="card"><ul class="say">{"".join(f"<li>{esc(s)}</li>" for s in sentences(d))}</ul></div>

<h2>What it was trained on</h2>
<p>The model predicts my score, from 1 to 5, for {i['books']:,} books. Each book has a target and a weight (how much it counts).</p>
<div class="card"><table><tr><th>Books</th><th class="n">Count</th><th class="n">Target</th><th class="n">Weight</th></tr>
{"".join(f'<tr><td>{esc(a)}</td><td class="n">{b:,}</td><td class="n">{c}</td><td class="n">{e:g}</td></tr>' for a, b, c, e in data_rows)}
<tr><td><b>All</b></td><td class="n"><b>{i['books']:,}</b></td><td></td><td></td></tr></table></div>
<p class="muted">{i['with_votes']:,} of these books have reader votes, {i['with_author_record']:,} have an author record, {i['with_series_record']:,} a series record.
For a random half ({i['hidden_half']:,} books) the book's own rating was hidden in training, so the model also learns to score books without votes.
The catalog average for fiction is {i['prior']:.2f}{f", from {ratings['fictionVotes']:,} votes on {ratings['fictionBooks']:,} books" if ratings.get('fictionVotes') else ""}; a book without votes sits exactly there.
Books that wait in "To send" or "Reading" without a score are left out.</p>
{check_text}

<h2>Factors it uses</h2>
<div class="card"><table><tr><th>Group</th><th class="n">Numbers</th><th>What</th></tr>{"".join(used)}</table></div>
<h2>Factors it does not use</h2>
<div class="card"><ul>{"".join(f"<li>{esc(u)}</li>" for u in unused)}</ul></div>

<h2>Ranking quality of each group of factors</h2>
<p class="muted">Each group alone, then together. Author-grouped test, {i['folds']} parts, repeated {i['repeats']} times; ± is the spread between the test parts. Bars start at 50%, which is guessing.</p>
<div class="card">{quality_chart(d)}</div>
<p class="muted">Two kinds of model were tried on all factors:</p>
<div class="card"><table><tr><th>Model</th><th class="n">Rated books</th><th class="n">Books without votes</th></tr>{kinds}</table></div>

<h2>Model scores for four kinds of books</h2>
<div class="card">{distribution_chart(d)}</div>

<h2>Factors that push the score up and down most</h2>
<div class="card">{effects_chart(d)}</div>

<h2>My scores against the model's predictions</h2>
<div class="card">{scatter_chart(d)}</div>
</main></body></html>
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    return OUT


if __name__ == "__main__":
    if not REPORT_DATA.exists():
        raise SystemExit('No training numbers yet. Run "npm run train" first.')
    print(f"Model report: {build(json.loads(REPORT_DATA.read_text(encoding='utf-8'))).relative_to(ROOT)}")
