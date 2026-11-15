"""Portrait of my taste: what the reading model says about my library, against ordinary new books.

Run:  .venv/bin/python model/portrait.py      writes reports/portrait.md

It is also rebuilt at the end of every training (model/recommend.py train), and the
same comparison is drawn as a chart in reports/model.html.

  my library    the kept library books that the reading model has read
  background    feed books picked at random and read in the long run (not the books that
                were read because the model already liked them)
  my scores     from 30 scored books on: the qualities of the books I scored 4-5 against 1-2
"""

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reader  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "portrait.md"
MIN_SCORES = 30
MIN_BOOKS = 20  # no portrait from fewer books on either side


def rows_of(db, keys):
    out = []
    for key in keys:
        r = db.execute("SELECT ratings, person_first, tones, themes FROM readings WHERE key = ? AND model = ? AND version = ?",
                       (key, reader.MODEL, reader.VERSION)).fetchone()
        if r:
            out.append({"ratings": json.loads(r["ratings"]), "first": r["person_first"], "tones": json.loads(r["tones"]), "themes": json.loads(r["themes"])})
    return out


def compare(a, b, get):
    """Two groups of books and a number per book -> averages, the difference and the difference in spreads."""
    x, y = np.array([get(r) for r in a], dtype=float), np.array([get(r) for r in b], dtype=float)
    sd = float(np.sqrt((x.var() + y.var()) / 2)) or 1.0
    return {"a": float(x.mean()), "b": float(y.mean()), "diff": float(x.mean() - y.mean()), "d": float((x.mean() - y.mean()) / sd)}


def build(db):
    db.row_factory = sqlite3.Row
    reader.ensure_tables(db)
    lib = rows_of(db, [k for _, k in reader.library_books(db)])
    daily = {r["key"] for r in db.execute("SELECT key FROM readings WHERE kind = 'daily'")}
    bg = rows_of(db, [k for _, k in reader.background_books(db) if k not in daily])
    scored = {r["key"]: r["score"] for r in db.execute("SELECT key, score FROM answers WHERE score BETWEEN 1 AND 5")}
    high, low = rows_of(db, [k for k, s in scored.items() if s >= 4]), rows_of(db, [k for k, s in scored.items() if s <= 2])
    n_scored_read = len(rows_of(db, list(scored)))
    data = {"library": len(lib), "background": len(bg), "scored": len(scored), "scored_read": n_scored_read, "model": reader.MODEL,
            "built_at": datetime.now().astimezone().isoformat(timespec="seconds"), "ready": len(lib) >= MIN_BOOKS and len(bg) >= MIN_BOOKS}
    if data["ready"]:
        quals = [{"id": q, "name": reader.Q_NAMES[q], **compare(lib, bg, lambda r, q=q: r["ratings"][q])} for q in reader.Q_IDS]
        voice = [{"id": "first", "name": "first-person narration", **compare(lib, bg, lambda r: r["first"])}]
        voice += [{"id": t, "name": f"{reader.TONE_NAMES[t]} tone", **compare(lib, bg, lambda r, t=t: r["tones"].get(t, 0.0))} for t in reader.TONES]
        themes = []
        for t in reader.THEMES:
            na, nb = sum(t in r["themes"] for r in lib), sum(t in r["themes"] for r in bg)
            # shares with one "half book" added on each side, so a theme that never appears on one side gives no division by zero
            sa, sb = (na + 0.5) / (len(lib) + 1), (nb + 0.5) / (len(bg) + 1)
            themes.append({"name": t, "lib": na, "bg": nb, "lib_share": na / len(lib), "bg_share": nb / len(bg), "ratio": sa / sb})
        data.update(qualities=quals, voice=voice, themes=themes)
    data["scores_ready"] = n_scored_read >= MIN_SCORES and len(high) >= 5 and len(low) >= 5
    if data["scores_ready"]:
        data["by_score"] = [{"id": q, "name": reader.Q_NAMES[q], **compare(high, low, lambda r, q=q: r["ratings"][q])} for q in reader.Q_IDS]
        data["high"], data["low"] = len(high), len(low)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(markdown(data), encoding="utf-8")
    return data


def size(d):
    d = abs(d)
    return "very large" if d >= 1.2 else "large" if d >= 0.8 else "clear" if d >= 0.5 else "small" if d >= 0.2 else "none"


def markdown(p):
    when = datetime.fromisoformat(p["built_at"]).strftime("%d %b %Y, %H:%M")
    out = ["# Portrait of my taste", "",
           f"Built {when} from what the reading model ({p['model']}) says about the opening (the first 25,000 words) of each book. "
           f"**{p['library']}** books of my library against **{p['background']}** ordinary new books picked at random from the daily updates. "
           "Each quality is rated from 1 to 5.", ""]
    if not p["ready"]:
        out += [f"Not enough books have been read yet (at least {MIN_BOOKS} on each side are needed). The page fills in while the long run goes on.", ""]
        return "\n".join(out)
    quals = sorted(p["qualities"], key=lambda q: -abs(q["d"]))
    up = [q for q in quals if q["d"] >= 0.2]
    down = [q for q in quals if q["d"] <= -0.2]
    same = [q for q in quals if abs(q["d"]) < 0.2]
    out += ["## In short", ""]
    if up:
        out.append("- My books stand out most by **more** " + ", ".join(f"{q['name']} ({q['a']:.1f} against {q['b']:.1f})" for q in up[:4]) + ".")
    if down:
        out.append("- They have **less** " + ", ".join(f"{q['name']} ({q['a']:.1f} against {q['b']:.1f})" for q in down[:4]) + ".")
    if same:
        out.append("- No real difference in " + ", ".join(q["name"] for q in same) + ".")
    voice = sorted(p["voice"], key=lambda v: -abs(v["d"]))
    v = [x for x in voice if abs(x["d"]) >= 0.2][:3]
    if v:
        out.append("- Narrative voice: " + "; ".join(f"{x['name']} in {100 * x['a']:.0f}% of my books against {100 * x['b']:.0f}%" for x in v) + ".")
    out += ["", "## Every quality: my library against the background", "",
            "| Quality | My library | Background | Difference | How big |", "|---|---:|---:|---:|---|"]
    for q in quals:
        out.append(f"| {q['name']} | {q['a']:.2f} | {q['b']:.2f} | {q['diff']:+.2f} | {size(q['d'])} |")
    out += ["", "\"How big\" compares the difference with how much books differ from each other inside each group: "
            "\"large\" means most of my books are above (or below) the typical background book.", ""]
    out += ["| Narrative voice | My library | Background |", "|---|---:|---:|"]
    for x in p["voice"]:
        out.append(f"| {x['name']} | {100 * x['a']:.0f}% | {100 * x['b']:.0f}% |")
    themes = [t for t in p["themes"] if t["lib"] >= max(5, 0.03 * p["library"])]
    over = sorted([t for t in themes if t["ratio"] > 1.25], key=lambda t: -t["ratio"])[:10]
    under = sorted([t for t in p["themes"] if t["bg"] >= max(5, 0.03 * p["background"]) and t["ratio"] < 0.8], key=lambda t: t["ratio"])[:6]
    out += ["", "## Themes", "", "Themes that my library has more often than the background (share of books that have the theme):", "",
            "| Theme | My library | Background | Times more often |", "|---|---:|---:|---:|"]
    for t in over:
        out.append(f"| {t['name']} | {100 * t['lib_share']:.0f}% | {100 * t['bg_share']:.0f}% | {t['ratio']:.1f} |")
    if not over:
        out.append("| (none stands out) | | | |")
    if under:
        out += ["", "Themes that my library has less often: " + ", ".join(f"{t['name']} ({100 * t['lib_share']:.0f}% against {100 * t['bg_share']:.0f}%)" for t in under) + "."]
    out += ["", "## My high scores against my low scores", ""]
    if p["scores_ready"]:
        rows = sorted(p["by_score"], key=lambda q: -abs(q["d"]))
        out += [f"{p['high']} books I scored 4 or 5 against {p['low']} books I scored 1 or 2.", "",
                "| Quality | Scored 4-5 | Scored 1-2 | Difference | How big |", "|---|---:|---:|---:|---|"]
        for q in rows:
            out.append(f"| {q['name']} | {q['a']:.2f} | {q['b']:.2f} | {q['diff']:+.2f} | {size(q['d'])} |")
    else:
        out.append(f"This part appears when I have {MIN_SCORES} scored books that the reading model has read. So far: {p['scored_read']} "
                   f"(of {p['scored']} scores). Until then the portrait only says how my library differs from ordinary new books, "
                   "not what makes me give a 5 instead of a 3.")
    out += ["", "A caution: my library is mostly published, edited books and many translations, while the background is what arrives "
            "every day, with a lot of raw web fiction. So part of the difference in language and polish is \"published against "
            "self-published\", not only my taste.", ""]
    return "\n".join(out)


if __name__ == "__main__":
    con = sqlite3.connect(reader.DB_PATH, timeout=60)
    d = build(con)
    print(f"Portrait written to {OUT.relative_to(ROOT)}: {d['library']} library books against {d['background']} background books.")
