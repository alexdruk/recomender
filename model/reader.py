"""The reading model: a local language model reads the opening of a book and rates it.

Run:  .venv/bin/python model/reader.py time KEY            read one book and say how long it took
      .venv/bin/python model/reader.py stability [N]       read N books (default 20) twice, report the agreement
      .venv/bin/python model/reader.py table               the ratings of 10 well-known library books
      .venv/bin/python model/reader.py run [--hours 12]    the long run: library, scored books, random background
      .venv/bin/python model/reader.py top [--n 30]        the daily step: read the best unread books of the day
      .venv/bin/python model/reader.py status              one line: how far the long run is

Everything runs on this computer, through Ollama (http://127.0.0.1:11434).

How a book is read
  The opening (the first 25,000 words, about 100 pages) is cut into parts at
  paragraph ends (config reader.partWords). The model reads the first
  reader.readWords words of each part - with the settings in config.json that
  is three passages of 2,500 words: from the start, the middle and the end of
  the opening. Reading all 25,000 words takes about 3 minutes a book, too slow
  for 600 books in 12 hours. The model reads one passage at a time and answers with strict
  JSON: a short summary of the part, a rating from 1 to 5 for every quality of
  the rubric below, the narrative voice, and up to 5 theme tags from a fixed
  list. The answer is checked; a wrong answer is asked again (3 tries).
  Temperature is 0. The rating of the book is the average over its parts;
  the themes of the book are the tags named most often; the two-sentence
  summary of the book is written by the model from the summaries of the parts.

Resumable: every part is saved as soon as it is read (table reading_parts),
so the program can be stopped and started again at any time.
"""

import argparse
import json
import os
import random
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / CONFIG.get("dataDir", "data")
DB_PATH = DATA / "books.db"
LOCK = DATA / "reader.lock"
LOG = DATA / "logs" / "reader.log"

READER = CONFIG.get("reader") or {}
OLLAMA = READER.get("ollama", "http://127.0.0.1:11434")
MODEL = READER.get("model", "gemma4:12b")
PART_WORDS = int(READER.get("partWords", 5000))
OPENING_WORDS = int(READER.get("openingWords", 25000))
READ_WORDS = int(READER.get("readWords", PART_WORDS))  # how many words of each part the model reads (from its start)


def depth_text():
    """In words: how much of a book the model reads (for the reports)."""
    n = max(1, round(OPENING_WORDS / PART_WORDS))
    if READ_WORDS >= PART_WORDS:
        return f"the first {OPENING_WORDS:,} words of the book, in {n} parts"
    return f"{n} passages of about {READ_WORDS:,} words each, spread evenly over the first {OPENING_WORDS:,} words (about 100 pages) of the book"
NUM_CTX = int(READER.get("contextTokens", 20480))
DAILY_BOOKS = int(READER.get("dailyBooks", 30))
VERSION = 1  # the version of the rubric; a book read with another version is read again
SEED = 20261115
TRIES = 3
WORD = re.compile(r"[^\W_]+(?:[-'’][^\W_]+)*")  # the same as in model/embed.py

TEXT_DIRS = {"liked": DATA / "text-fb2", "feed": DATA / "feed" / "text", "matched": DATA / "matched" / "text",
             "rated": DATA / "rated" / "text"}

# ---------------------------------------------------------------- the rubric

# id, English name (for the page and the reports), Russian name, one-line definition, what 1, 3 and 5 mean
QUALITIES = [
    ("pace", "plot pace", "Темп сюжета",
     "как быстро сменяются события и продвигается действие",
     "почти ничего не происходит: описания, размышления, быт",
     "действие идёт ровно, события чередуются со спокойными сценами",
     "события следуют одно за другим без передышки"),
    ("originality", "plot originality", "Оригинальность сюжета",
     "насколько завязка, ходы и ситуации непохожи на привычные штампы жанра",
     "сплошные штампы, всё это много раз встречалось",
     "знакомая схема с отдельными своими находками",
     "необычный замысел, неожиданные ходы, так почти никто не пишет"),
    ("characters", "depth of characters", "Глубина персонажей",
     "насколько герои похожи на живых людей со своим характером, прошлым и манерой речи",
     "картонные фигуры и функции, отличаются только именами",
     "у главных героев есть характер, остальные схематичны",
     "объёмные, противоречивые, запоминающиеся люди, включая второстепенных"),
    ("psychology", "psychological complexity", "Психологическая сложность",
     "сколько внимания внутренней жизни: мотивам, сомнениям, внутренним конфликтам",
     "внутренней жизни нет, герои просто действуют",
     "мотивы объясняются, но просто и прямо",
     "тонкие мотивы, внутренние конфликты, самообман, неоднозначный выбор"),
    ("dialogue", "quality of dialogue", "Качество диалогов",
     "насколько реплики естественны, различаются по героям и работают на сюжет",
     "деревянные, все говорят одинаково, диалог только пересказывает сведения (если диалогов почти нет, ставь 3)",
     "диалоги рабочие, но обычные",
     "живая речь, у каждого свой голос, подтекст, реплики хочется цитировать"),
    ("humour", "humour and irony", "Юмор и ирония",
     "сколько в тексте смешного: шуток, иронии, комических положений",
     "совсем нет, текст полностью серьёзен",
     "встречается время от времени",
     "юмор или ирония в основе текста, смешно почти на каждой странице"),
    ("emotion", "emotional intensity", "Эмоциональная напряжённость",
     "насколько сильные чувства испытывают герои и насколько высоки ставки",
     "ровно и спокойно, без сильных чувств",
     "умеренные переживания, есть напряжённые моменты",
     "очень сильные чувства, высокое напряжение почти всё время"),
    ("darkness", "darkness and violence", "Мрачность и насилие",
     "сколько жестокости, смертей, страданий и безысходности",
     "светлый, безопасный текст без жестокости",
     "есть опасность и отдельные жёсткие сцены, без подробностей",
     "много подробного насилия, жестокости, мрака"),
    ("romance", "weight of the romance line", "Вес любовной линии",
     "какое место занимают любовные отношения героев",
     "любовной линии нет",
     "любовная линия заметна, но она побочная",
     "любовные отношения - главное содержание текста"),
    ("explicit", "weight of explicit content", "Вес откровенных сцен",
     "сколько места занимают эротические, сексуальные сцены и насколько они подробны",
     "таких сцен нет вовсе",
     "есть намёки или короткие неподробные сцены",
     "частые подробные сексуальные сцены"),
    ("world", "world-building", "Проработка мира",
     "насколько подробно и убедительно показан мир: место, эпоха, быт, устройство общества, магия или техника",
     "мира почти не видно, условные декорации",
     "мир обрисован достаточно для сюжета, без особых подробностей",
     "подробный, продуманный, осязаемый мир с собственными правилами и деталями"),
    ("language", "richness of language", "Богатство языка и метафор",
     "насколько разнообразна лексика, есть ли образность, метафоры, свой стиль",
     "бедный, однообразный язык, только простые фразы",
     "грамотный нейтральный язык, образы редки",
     "богатый, образный язык, яркие метафоры, узнаваемый стиль"),
    ("polish", "literary polish", "Литературная отделка",
     "вычитанная редактором проза или сырой сетевой текст: ошибки, повторы, канцелярит, небрежные фразы",
     "сырой сетевой текст: ошибки, повторы слов, корявые фразы",
     "читается нормально, но встречаются шероховатости",
     "чистая, выверенная, профессионально отредактированная проза"),
]
Q_IDS = [q[0] for q in QUALITIES]
Q_NAMES = {q[0]: q[1] for q in QUALITIES}

# narrative voice: who tells the story, and in what tone
PERSONS = {"first": "от первого лица (я)", "third": "от третьего лица (он, она)", "mixed": "вперемешку или иначе"}
TONES = {"serious": "серьёзный", "ironic": "ироничный, насмешливый", "light": "лёгкий, шутливый", "lyrical": "лиричный, задумчивый",
         "dark": "мрачный, тяжёлый", "tense": "напряжённый, тревожный", "dry": "сухой, деловой"}
TONE_NAMES = {"serious": "serious", "ironic": "ironic", "light": "light", "lyrical": "lyrical", "dark": "dark", "tense": "tense", "dry": "dry"}

THEMES = [
    "любовь", "семья", "дружба", "взросление", "поиск себя", "одиночество", "смерть и утрата", "предательство", "месть",
    "моральный выбор", "долг и честь", "свобода", "власть и интриги", "политика", "война", "армия и служба", "выживание",
    "путешествие и приключения", "детектив и расследование", "преступление и криминал", "тайны прошлого", "магия",
    "магическая академия", "попаданцы", "мифы и фольклор", "мистика и потустороннее", "чудовища и нечисть", "религия и вера",
    "спасение мира", "космос", "наука и технологии", "искусственный интеллект", "игра и виртуальные миры", "постапокалипсис",
    "альтернативная история", "история и эпоха", "придворная жизнь", "социальное неравенство", "деньги и бизнес",
    "работа и карьера", "школа и учёба", "медицина", "искусство и творчество", "природа и животные", "быт и повседневность",
    "еда и кулинария", "брак по расчёту", "соперничество", "юмор и абсурд", "спорт",
]

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "ratings": {"type": "object", "properties": {q: {"type": "integer", "minimum": 1, "maximum": 5} for q in Q_IDS}, "required": Q_IDS},
        "person": {"type": "string", "enum": list(PERSONS)},
        "tone": {"type": "string", "enum": list(TONES)},
        "themes": {"type": "array", "items": {"type": "string", "enum": THEMES}, "minItems": 1, "maxItems": 5},
    },
    "required": ["summary", "ratings", "person", "tone", "themes"],
}


def system_prompt():
    lines = [
        "Ты опытный литературный редактор. Тебе дают отрывок из начала художественной книги на русском языке.",
        "Оцени именно этот отрывок по списку качеств. Каждое качество - целое число от 1 до 5.",
        "Оценивай только то, что есть в тексте отрывка, а не то, что ты знаешь об авторе или книге.",
        "Используй всю шкалу: 3 - это обычный средний уровень, 1 и 5 ставь, когда текст им действительно соответствует; 2 и 4 - промежуточные.",
        "",
        "КАЧЕСТВА",
    ]
    for qid, _, ru, what, one, three, five in QUALITIES:
        lines.append(f"{qid} - {ru}: {what}.")
        lines.append(f"  1 = {one}; 3 = {three}; 5 = {five}.")
    lines += [
        "",
        "ГОЛОС ПОВЕСТВОВАНИЯ",
        "person - от чьего лица ведётся рассказ: " + "; ".join(f"{k} = {v}" for k, v in PERSONS.items()) + ".",
        "tone - преобладающий тон: " + "; ".join(f"{k} = {v}" for k, v in TONES.items()) + ".",
        "",
        "ТЕМЫ",
        "themes - от 1 до 5 главных тем отрывка, только из этого списка, слово в слово:",
        ", ".join(THEMES) + ".",
        "",
        "ОТВЕТ",
        "Ответь одним объектом JSON и больше ничем, с полями в таком порядке:",
        '{"summary": "одно-два предложения по-русски: что происходит в отрывке и как он написан", '
        '"ratings": {' + ", ".join(f'"{q}": 1-5' for q in Q_IDS) + "}, "
        '"person": "first|third|mixed", "tone": "' + "|".join(TONES) + '", "themes": ["тема", "..."]}',
    ]
    return "\n".join(lines)


SYSTEM = system_prompt()


def user_prompt(text, part, total):
    return (f"Отрывок {part} из {total} (начало книги).\n\n<отрывок>\n{text.strip()}\n</отрывок>\n\n"
            "Оцени этот отрывок по списку качеств и ответь одним объектом JSON.")


# ---------------------------------------------------------------- talking to Ollama


class ReaderError(Exception):
    pass


def ollama(path, body=None, timeout=900):
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ReaderError(f"Ollama answered {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise ReaderError(f"Ollama does not answer at {OLLAMA} ({e}). Start it with: brew services start ollama")


def ready(model=MODEL):
    """Raises ReaderError when Ollama is not running or the model is not downloaded."""
    names = {m["name"] for m in ollama("/api/tags", timeout=10).get("models", [])}
    if model not in names and model + ":latest" not in names:
        raise ReaderError(f'The model "{model}" is not downloaded. Run: ollama pull {model}')


NO_SCHEMA = set()  # models whose engine cannot force a JSON shape; their answers are only checked afterwards


def chat(model, messages, schema=None, seed=SEED, max_tokens=700, ctx=NUM_CTX):
    body = {"model": model, "messages": messages, "stream": False, "think": False, "keep_alive": "15m",
            "options": {"temperature": 0, "seed": seed, "num_ctx": ctx, "num_predict": max_tokens}}
    if schema is not None and model not in NO_SCHEMA:
        body["format"] = schema
    for _ in range(3):
        try:
            r = ollama("/api/chat", body)
            break
        except ReaderError as e:
            if "structured output" in str(e) and "format" in body:
                NO_SCHEMA.add(model)
                body.pop("format")
            elif ("think" in str(e)) and "think" in body:
                body.pop("think")  # a model without a thinking mode refuses the switch
            else:
                raise
    else:
        raise ReaderError("Ollama refused the request")
    return (r.get("message") or {}).get("content", ""), r


def check(raw):
    """The answer of the model -> a clean dict, or ValueError that says what is wrong."""
    raw = raw.strip()
    if raw.startswith("```"):  # an answer wrapped in a code block
        raw = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", raw)
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"не JSON: {e.msg}")
    if not isinstance(d, dict):
        raise ValueError("ответ должен быть объектом JSON")
    ratings = d.get("ratings")
    if not isinstance(ratings, dict):
        raise ValueError('нет поля "ratings"')
    clean = {}
    for q in Q_IDS:
        v = ratings.get(q)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v) or not 1 <= v <= 5:
            raise ValueError(f'"ratings.{q}" должно быть целым числом от 1 до 5')
        clean[q] = int(v)
    if d.get("person") not in PERSONS:
        raise ValueError('"person" должно быть одним из: ' + ", ".join(PERSONS))
    if d.get("tone") not in TONES:
        raise ValueError('"tone" должно быть одним из: ' + ", ".join(TONES))
    themes = d.get("themes")
    if not isinstance(themes, list) or not themes:
        raise ValueError('"themes" должно быть списком из 1-5 тем')
    themes = list(dict.fromkeys(t for t in themes if t in THEMES))[:5]
    if not themes:
        raise ValueError('в "themes" нет ни одной темы из списка')
    summary = d.get("summary")
    if not isinstance(summary, str) or len(summary.strip()) < 20:
        raise ValueError('"summary" должно быть одним-двумя предложениями')
    return {"summary": " ".join(summary.split())[:600], "ratings": clean, "person": d["person"], "tone": d["tone"], "themes": themes}


def rate_part(model, text, part, total):
    """One part -> (clean answer, facts about the call). Asks again when the answer is not valid."""
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_prompt(text, part, total)}]
    problem = None
    for attempt in range(TRIES):
        msgs = messages if problem is None else messages + [
            {"role": "user", "content": f"Предыдущий ответ не подошёл: {problem}. Ответь заново, строго одним объектом JSON по образцу."}]
        raw, r = chat(model, msgs, SCHEMA, seed=SEED + attempt)
        tokens_in = r.get("prompt_eval_count") or 0
        if tokens_in >= NUM_CTX - 8:
            raise ReaderError(f"the part does not fit the context ({tokens_in} tokens): make reader.contextTokens bigger or reader.partWords smaller")
        try:
            return check(raw), {"tokens_in": tokens_in, "tokens_out": r.get("eval_count") or 0, "retries": attempt}
        except ValueError as e:
            problem = str(e)
    raise ReaderError(f"no valid JSON after {TRIES} tries: {problem}")


def two_sentences(model, summaries):
    """The two-sentence summary of the book, from the summaries of its parts."""
    body = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(summaries))
    messages = [
        {"role": "system", "content": "Ты литературный редактор. Пишешь коротко, точно и по-русски."},
        {"role": "user", "content": "Вот краткие описания нескольких отрывков из начала одной книги, по порядку:\n\n" + body +
            "\n\nНапиши ровно два предложения по-русски. Первое: что это за книга (жанр, герой, завязка). "
            "Второе: как она написана (язык, тон, темп). Не упоминай слова «отрывок» и «часть». Только эти два предложения, без вступления."}]
    for attempt in range(TRIES):
        raw, _ = chat(model, messages, None, seed=SEED + attempt, max_tokens=300)  # the same context size: another size would reload the model
        text = " ".join(raw.replace("*", "").split()).lstrip("> ").strip('"«»')
        if 40 <= len(text) <= 700 and re.search(r"[а-яё]", text):
            return text
    return " ".join(summaries[:1])[:400]


# ---------------------------------------------------------------- cutting the opening


def cut_parts(text, shift=0):
    """The opening (the first 25,000 words) as a list of (text, words), cut at paragraph ends.
    shift = 0: parts of PART_WORDS words. shift > 0: the same opening cut at other places - the first
    part has only "shift" words, so every later cut falls elsewhere (the stability test).
    Of each part only the first READ_WORDS words are kept (see head)."""
    words = list(WORD.finditer(text))
    total = min(len(words), OPENING_WORDS)
    if total == 0:
        return []
    marks = list(range(PART_WORDS, total, PART_WORDS)) if not shift else list(range(shift, total, PART_WORDS))
    cuts, last = [], 0
    for m in marks:
        if total - m < PART_WORDS // 4 and not shift:
            break  # a short tail joins the part before it
        pos = text.find("\n", words[m - 1].end())
        pos = len(text) if pos == -1 else pos + 1
        if pos - words[m - 1].end() > 4000:  # a very long paragraph: cut at the word
            pos = words[m - 1].end()
        if pos > last:
            cuts.append(pos)
            last = pos
    end = text.find("\n", words[total - 1].end())
    end = len(text) if end == -1 or end - words[total - 1].end() > 4000 else end + 1
    edges = [0] + [c for c in cuts if c < end] + [end]
    parts = [text[a:b] for a, b in zip(edges, edges[1:]) if text[a:b].strip()]
    return [(p, len(WORD.findall(p))) for p in (head(p) for p in parts)]


def head(part):
    """The first READ_WORDS words of a part, up to the end of the paragraph. The whole part when it is not longer."""
    if READ_WORDS >= PART_WORDS:
        return part
    n = 0
    for m in WORD.finditer(part):
        n += 1
        if n >= READ_WORDS:
            end = part.find("\n", m.end())
            return part if end == -1 else part[:m.end()] if end - m.end() > 4000 else part[:end + 1]
    return part


# ---------------------------------------------------------------- the database


def connect():
    db = sqlite3.connect(DB_PATH, timeout=120)
    db.row_factory = sqlite3.Row
    ensure_tables(db)
    return db


def ensure_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS reading_parts (
        key TEXT NOT NULL, run TEXT NOT NULL, part INTEGER NOT NULL, parts INTEGER NOT NULL, words INTEGER,
        model TEXT, version INTEGER, result TEXT, seconds REAL, tokens_in INTEGER, tokens_out INTEGER, retries INTEGER, read_at TEXT,
        PRIMARY KEY (key, run, part))""")
    # one row per book that the reading model has read: the averages over its parts
    db.execute("""CREATE TABLE IF NOT EXISTS readings (
        key TEXT PRIMARY KEY, kind TEXT, model TEXT, version INTEGER, parts INTEGER, words INTEGER,
        ratings TEXT, person_first REAL, tones TEXT, themes TEXT, summary TEXT, seconds REAL, read_at TEXT)""")
    db.commit()


def get_meta(db, name, default=None):
    r = db.execute("SELECT value FROM meta WHERE name = ?", (name,)).fetchone()
    return json.loads(r[0]) if r and r[0] is not None else default


def set_meta(db, name, value):
    db.execute("INSERT INTO meta (name, value) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value", (name, json.dumps(value)))
    db.commit()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def text_of(kind, key):
    path = TEXT_DIRS[kind] / f"{key}.txt"
    return path.read_text(encoding="utf-8") if path.exists() else None


def combine(rows):
    """The rows of reading_parts of one book -> the ratings of the book (averages weighted by the words of each part)."""
    results = [json.loads(r["result"]) for r in rows]
    weights = [max(1, r["words"] or 1) for r in rows]
    total = float(sum(weights))
    ratings = {q: sum(w * res["ratings"][q] for w, res in zip(weights, results)) / total for q in Q_IDS}
    first = sum(w * {"first": 1.0, "mixed": 0.5, "third": 0.0}[res["person"]] for w, res in zip(weights, results)) / total
    tones = {t: sum(w for w, res in zip(weights, results) if res["tone"] == t) / total for t in TONES}
    count, order = Counter(), {}
    for res in results:
        for t in res["themes"]:
            count[t] += 1
            order.setdefault(t, len(order))
    themes = sorted(count, key=lambda t: (-count[t], order[t]))[:5]
    return {"ratings": ratings, "person_first": first, "tones": tones, "themes": themes, "summaries": [res["summary"] for res in results],
            "words": int(total), "seconds": sum(r["seconds"] or 0 for r in rows)}


def read_parts(db, key, text, run, model, shift=0, log=None):
    """Reads every part of the book that is not saved yet. Returns the rows of all parts."""
    parts = cut_parts(text, shift)
    if not parts:
        raise ReaderError("the text is empty")
    have = {r["part"]: r for r in db.execute("SELECT * FROM reading_parts WHERE key = ? AND run = ? AND model = ? AND version = ? AND parts = ?",
                                              (key, run, model, VERSION, len(parts)))}
    for i, (part, words) in enumerate(parts, 1):
        if i in have:
            continue
        t0 = time.time()
        res, facts = rate_part(model, part, i, len(parts))
        db.execute("""INSERT OR REPLACE INTO reading_parts (key, run, part, parts, words, model, version, result, seconds, tokens_in, tokens_out, retries, read_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                   (key, run, i, len(parts), words, model, VERSION, json.dumps(res, ensure_ascii=False), time.time() - t0,
                    facts["tokens_in"], facts["tokens_out"], facts["retries"], now()))
        db.commit()
        if log:
            log(f"    part {i}/{len(parts)}: {words} words, {facts['tokens_in']} tokens, {time.time() - t0:.0f} s"
                + (f", {facts['retries']} retries" if facts["retries"] else ""))
    return db.execute("SELECT * FROM reading_parts WHERE key = ? AND run = ? AND model = ? AND version = ? AND parts = ? ORDER BY part",
                      (key, run, model, VERSION, len(parts))).fetchall()


def read_book(db, kind, key, model=MODEL, log=None):
    """Reads one book and stores its ratings in "readings". Returns the stored row as a dict, or None when there is no text."""
    done = db.execute("SELECT * FROM readings WHERE key = ? AND model = ? AND version = ?", (key, model, VERSION)).fetchone()
    if done:
        return dict(done)
    text = text_of(kind, key)
    if text is None:
        return None
    rows = read_parts(db, key, text, "main", model, log=log)
    c = combine(rows)
    t0 = time.time()
    summary = two_sentences(model, c["summaries"])
    db.execute("""INSERT OR REPLACE INTO readings (key, kind, model, version, parts, words, ratings, person_first, tones, themes, summary, seconds, read_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
               (key, kind, model, VERSION, len(rows), c["words"], json.dumps(c["ratings"]), c["person_first"], json.dumps(c["tones"]),
                json.dumps(c["themes"], ensure_ascii=False), summary, c["seconds"] + time.time() - t0, now()))
    db.commit()
    return dict(db.execute("SELECT * FROM readings WHERE key = ?", (key,)).fetchone())


# ---------------------------------------------------------------- which books


LIKED_SQL = """SELECT b.key, b.authors, b.title FROM books b JOIN parsed p ON p.source = 'liked_fb2' AND p.key = b.key
    WHERE b.parse_status != 'failed' AND p.parse_status != 'failed' AND COALESCE(b.is_collection, 0) = 0 AND b.is_fiction = 1"""


def library_books(db):
    return [("liked", r["key"]) for r in db.execute(LIKED_SQL + " ORDER BY b.key") if (TEXT_DIRS["liked"] / f"{r['key']}.txt").exists()]


def scored_books(db):
    rows = db.execute("SELECT key FROM answers WHERE score BETWEEN 1 AND 5 ORDER BY key").fetchall()
    rows += db.execute("SELECT key FROM book_state WHERE key NOT IN (SELECT key FROM answers) ORDER BY key").fetchall()  # sent to me, a score will come
    return [("feed", r["key"]) for r in rows if (TEXT_DIRS["feed"] / f"{r['key']}.txt").exists()]


def background_books(db):
    """Feed books that the scoring model uses as background, with a text on disk, in a random order that never changes."""
    busy = {r["key"] for r in db.execute("SELECT key FROM answers UNION SELECT key FROM book_state")}
    liked = {r["key"] for r in db.execute("SELECT key FROM books")}
    keys = [r["key"] for r in db.execute("""SELECT f.key FROM feed_books f JOIN feed_scores s ON s.key = f.key
        WHERE f.parse_status != 'failed' AND NOT (s.passed = 0 AND s.reason LIKE 'not fiction%') ORDER BY f.key""")
            if r["key"] not in busy and r["key"] not in liked
            and (TEXT_DIRS["feed"] / f"{r['key']}.txt").exists() and (DATA / "emb" / "feed" / f"{r['key']}.npy").exists()]
    random.Random(SEED).shuffle(keys)
    return [("feed", k) for k in keys]


def title_of(db, kind, key):
    r = db.execute(f"SELECT authors, title FROM {'books' if kind == 'liked' else 'feed_books'} WHERE key = ?", (key,)).fetchone()
    if not r:
        return key
    try:
        authors = ", ".join(json.loads(r["authors"] or "[]")[:2])
    except json.JSONDecodeError:
        authors = ""
    return f"{authors} — {r['title']}" if authors else str(r["title"])


# ---------------------------------------------------------------- one at a time


def take_lock(what):
    """Only one reading job at a time: two would fight for the same memory. Returns False when another one is running."""
    if LOCK.exists():
        try:
            info = json.loads(LOCK.read_text())
            os.kill(int(info["pid"]), 0)
            return False
        except (ValueError, KeyError, ProcessLookupError, PermissionError, json.JSONDecodeError):
            pass
    LOCK.write_text(json.dumps({"pid": os.getpid(), "what": what, "since": now()}))
    return True


def drop_lock():
    try:
        if json.loads(LOCK.read_text()).get("pid") == os.getpid():
            LOCK.unlink()
    except (OSError, ValueError):
        pass


def make_logger(to_file):
    if to_file:
        LOG.parent.mkdir(parents=True, exist_ok=True)

    def log(msg):
        line = f"[{datetime.now().strftime('%d %b %H:%M:%S')}] {msg}"
        print(line, flush=True)
        if to_file:
            with LOG.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    return log


# ---------------------------------------------------------------- commands


def cmd_time(db, args):
    key = args.key
    kind = "liked" if (TEXT_DIRS["liked"] / f"{key}.txt").exists() else "feed"
    ready(args.model)
    db.execute("DELETE FROM reading_parts WHERE key = ? AND run = 'time'", (key,))
    db.commit()
    t0 = time.time()
    rows = read_parts(db, key, text_of(kind, key), "time", args.model, log=print)
    c = combine(rows)
    summary = two_sentences(args.model, c["summaries"])
    secs = time.time() - t0
    print(f"{title_of(db, kind, key)}: {len(rows)} parts, {c['words']} words, {sum(r['tokens_in'] for r in rows)} tokens in, {secs:.0f} seconds.")
    print("  " + ", ".join(f"{Q_NAMES[q]} {c['ratings'][q]:.1f}" for q in Q_IDS))
    print("  themes: " + ", ".join(c["themes"]))
    print("  " + summary)
    set_meta(db, "reader_timing", {"model": args.model, "seconds": secs, "words": c["words"], "at": now()})


def agreement(pairs):
    """pairs: list of (first reading, second reading), each a dict from combine(). Returns one row per quality."""
    import numpy as np
    from scipy.stats import spearmanr

    out = []
    names = [(q, Q_NAMES[q], lambda c, q=q: c["ratings"][q]) for q in Q_IDS]
    names.append(("person_first", "narrative voice: first person", lambda c: c["person_first"]))
    for t in TONES:
        names.append(("tone_" + t, f"narrative voice: {TONE_NAMES[t]} tone", lambda c, t=t: c["tones"][t]))
    for qid, name, get in names:
        a = np.array([get(p[0]) for p in pairs], dtype=float)
        b = np.array([get(p[1]) for p in pairs], dtype=float)
        if a.std() == 0 or b.std() == 0:
            r = rho = float("nan")
        else:
            r = float(np.corrcoef(a, b)[0, 1])
            rho = float(spearmanr(a, b).statistic)
        out.append({"id": qid, "name": name, "r": r, "rho": rho, "diff": float(np.abs(a - b).mean()), "spread": float(np.concatenate([a, b]).std()),
                    "mean": float(np.concatenate([a, b]).mean())})
    return out


def stability_books(db, n):
    """Half library books, half feed books, always the same ones."""
    lib, bg = library_books(db), background_books(db)
    rnd = random.Random(SEED + 7)
    lib = rnd.sample(lib, len(lib))
    known = [("liked", k) for k in KNOWN if (TEXT_DIRS["liked"] / f"{k}.txt").exists()]
    lib = known + [b for b in lib if b not in known]
    half = n // 2
    return lib[:half] + bg[:n - half]


def cmd_stability(db, args):
    """Every book is read twice: the same opening, the second time cut at other places, so the model
    sees other passages of the same 100 pages.
    (With temperature 0 the same input gives the same answer, so an exact repeat proves nothing;
    --repeat adds it for the first 3 books as a check.)"""
    ready(args.model)
    books = stability_books(db, args.n)
    log = make_logger(False)
    pairs, t0, secs = [], time.time(), []
    for i, (kind, key) in enumerate(books, 1):
        text = text_of(kind, key)
        t1 = time.time()
        a = combine(read_parts(db, key, text, "main", args.model))
        b = combine(read_parts(db, key, text, "shift", args.model, shift=PART_WORDS // 2))
        pairs.append((a, b))
        secs.append(a["seconds"])
        log(f"{i}/{len(books)} {title_of(db, kind, key)[:60]}: read twice in {time.time() - t1:.0f} s")
    rows = agreement(pairs)
    same = None
    if args.repeat:
        exact = []
        for kind, key in books[:3]:
            text = text_of(kind, key)
            a = combine(read_parts(db, key, text, "main", args.model))
            db.execute("DELETE FROM reading_parts WHERE key = ? AND run = 'repeat'", (key,))
            b = combine(read_parts(db, key, text, "repeat", args.model))
            exact.append(max(abs(a["ratings"][q] - b["ratings"][q]) for q in Q_IDS))
        same = max(exact)
    per_book = sum(secs) / len(secs)
    result = {"model": args.model, "version": VERSION, "books": len(books), "rows": rows, "seconds_per_book": per_book,
              "exact_repeat_max_diff": same, "at": now()}
    set_meta(db, f"reader_stability:{args.model}", result)
    print(f"\nStability of {args.model} on {len(books)} books, each read twice (the same 100 pages, other passages). {per_book:.0f} s per reading.")
    print(f"{'quality':42s} {'agreement':>9s} {'avg diff':>9s} {'spread':>7s} {'mean':>6s}")
    for r in rows:
        print(f"{r['name']:42s} {r['r']:9.2f} {r['diff']:9.2f} {r['spread']:7.2f} {r['mean']:6.2f}")
    if same is not None:
        print(f"Exact repeat of 3 books: the biggest difference in any rating was {same:.2f}.")
    print(f"Total {(time.time() - t0) / 60:.0f} minutes.")


# ten library books I will recognise (the sanity table)
KNOWN = ["329732", "155848", "461245", "501819", "475418", "641061", "785058", "508788", "253815", "539960"]


def cmd_table(db, args):
    ready(args.model)
    rows = []
    for key in KNOWN:
        r = read_book(db, "liked", key, args.model)
        if r:
            rows.append((key, r))
    short = {"pace": "pace", "originality": "orig", "characters": "char", "psychology": "psych", "dialogue": "dial", "humour": "hum", "emotion": "emo",
             "darkness": "dark", "romance": "rom", "explicit": "expl", "world": "world", "language": "lang", "polish": "polish"}
    print("| Book | " + " | ".join(short[q] for q in Q_IDS) + " | voice | themes |")
    print("|---|" + "---|" * (len(Q_IDS) + 2))
    for key, r in rows:
        ratings, tones = json.loads(r["ratings"]), json.loads(r["tones"])
        tone = max(tones, key=tones.get)
        person = "first" if r["person_first"] >= 0.6 else "third" if r["person_first"] <= 0.4 else "mixed"
        print(f"| {title_of(db, 'liked', key)[:48]} | " + " | ".join(f"{ratings[q]:.1f}" for q in Q_IDS)
              + f" | {person}, {tone} | {', '.join(json.loads(r['themes'])[:3])} |")
    print()
    for key, r in rows:
        print(f"- {title_of(db, 'liked', key)[:60]}: {r['summary']}")


def progress(db, **fields):
    p = get_meta(db, "reading_progress") or {}
    p.update(fields, updated_at=now())
    set_meta(db, "reading_progress", p)
    return p


def cmd_run(db, args):
    """The long run. Order: the books I scored, then library and background books mixed, so that
    at any moment the books read so far are a fair sample of both."""
    ready(args.model)
    if not take_lock("the long run"):
        sys.exit("Another reading job is running (data/reader.lock).")
    log = make_logger(True)
    try:
        timing = get_meta(db, "reader_timing") or {}
        per_book = float(args.seconds or timing.get("seconds") or 150)
        budget = args.hours * 3600 * 0.95  # 5% kept spare
        scored, lib, bg = scored_books(db), library_books(db), background_books(db)
        fit = int(budget / per_book) - len(scored) - len(lib)
        n_bg = max(min(len(bg), args.min_background), min(len(bg), fit))
        bg = bg[:n_bg]
        # library and background mixed evenly
        mixed, rnd = [], random.Random(SEED)
        lib = rnd.sample(lib, len(lib))
        i = j = 0
        while i < len(lib) or j < len(bg):
            if j >= len(bg) or (i < len(lib) and i * len(bg) <= j * len(lib)):
                mixed.append(lib[i])
                i += 1
            else:
                mixed.append(bg[j])
                j += 1
        books = scored + [b for b in mixed if b not in scored]
        done_before = {r["key"] for r in db.execute("SELECT key FROM readings WHERE model = ? AND version = ?", (args.model, VERSION))}
        todo = [b for b in books if b[1] not in done_before]
        started, deadline = time.time(), time.time() + args.hours * 3600
        log(f"Long run with {args.model}: {len(books)} books ({len(scored)} scored or sent, {len(lib)} library, {len(bg)} background); "
            f"{len(books) - len(todo)} already read, {len(todo)} to read. About {per_book:.0f} s per book: "
            f"{len(todo) * per_book / 3600:.1f} hours expected, {args.hours:g} hours allowed.")
        progress(db, state="running", model=args.model, total=len(books), done=len(books) - len(todo), failed=0, pid=os.getpid(),
                 started_at=now(), finish_by=datetime.fromtimestamp(deadline, timezone.utc).isoformat(timespec="seconds"),
                 library=len(lib), background=len(bg), scored=len(scored), eta=None, seconds_per_book=per_book)
        read, failed, spent = 0, 0, 0.0
        for n, (kind, key) in enumerate(todo, 1):
            if time.time() > deadline:
                log(f"The {args.hours:g} hours are over: stopped with {len(todo) - n + 1} books not read. Start the same command again to go on.")
                break
            t0 = time.time()
            try:
                read_book(db, kind, key, args.model)
                read += 1
                spent += time.time() - t0
            except ReaderError as e:
                failed += 1
                log(f"  {key}: {e}")
                if "does not answer" in str(e):
                    time.sleep(30)
            avg = spent / max(1, read)
            left = len(todo) - n
            eta = datetime.fromtimestamp(time.time() + left * avg, timezone.utc).isoformat(timespec="seconds")
            progress(db, done=len(books) - len(todo) + read, failed=failed, eta=eta, seconds_per_book=avg)
            if n % 10 == 0 or n == len(todo):
                log(f"{len(books) - len(todo) + read}/{len(books)} read, {avg:.0f} s per book, {left * avg / 3600:.1f} hours left.")
        state = "finished" if time.time() <= deadline else "stopped"
        progress(db, state=state, eta=None, finished_at=now())
        log(f"Long run {state}: {read} books read in {(time.time() - started) / 3600:.1f} hours, {failed} failed.")
    finally:
        drop_lock()
    if args.then_train and read:
        log("Training the scoring model with the new ratings ...")
        os.execv(sys.executable, [sys.executable, str(ROOT / "model" / "recommend.py"), "train"])


def cmd_top(db, args):
    """The daily step: the reading model reads the best unread books of the day, best cheap score first.
    At most 30 books a day (config.json reader.dailyBooks)."""
    try:
        ready(args.model)
    except ReaderError as e:
        print(f"Reading model: skipped. {e}")
        return 0
    if not take_lock("the daily reading"):
        print("Reading model: skipped, the long run is still reading. The best books of today are read at the next check.")
        return 0
    try:
        today = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat(timespec="seconds")
        used = db.execute("SELECT COUNT(*) FROM readings WHERE kind = 'daily' AND read_at >= ?", (today,)).fetchone()[0]
        room = max(0, args.n - used)
        col = "cheap_score" if db.execute("SELECT 1 FROM pragma_table_info('feed_scores') WHERE name = 'cheap_score'").fetchone() else "score"
        rows = db.execute(f"""SELECT s.key FROM feed_scores s WHERE s.passed = 1 AND s.{col} IS NOT NULL
            AND s.key NOT IN (SELECT key FROM answers) AND s.key NOT IN (SELECT key FROM book_state)
            AND s.key NOT IN (SELECT key FROM readings WHERE model = ? AND version = ?)
            ORDER BY s.{col} DESC""", (args.model, VERSION)).fetchall()
        keys = [r["key"] for r in rows if (TEXT_DIRS["feed"] / f"{r['key']}.txt").exists()][:room]
        if not keys:
            print(f"Reading model: nothing to read ({used} books read today, the limit is {args.n} a day).")
            return 0
        print(f"Reading model ({args.model}): reading the best {len(keys)} unread books ...", flush=True)
        t0, n = time.time(), 0
        for key in keys:
            try:
                if read_book(db, "feed", key, args.model):
                    db.execute("UPDATE readings SET kind = 'daily' WHERE key = ?", (key,))
                    db.commit()
                    n += 1
            except ReaderError as e:
                print(f"  {key}: {e}", flush=True)
                if "does not answer" in str(e):
                    break
        print(f"Reading model: {n} books read in {(time.time() - t0) / 60:.0f} minutes.")
        return n
    finally:
        drop_lock()


def cmd_status(db, args):
    p = get_meta(db, "reading_progress")
    if not p:
        print("The long run has not been started.")
        return
    print(f"Long run: {p.get('state')}, {p.get('done')} of {p.get('total')} books read, {p.get('failed', 0)} failed, "
          f"{p.get('seconds_per_book', 0):.0f} s per book" + (f", expected to finish {p['eta']}" if p.get("eta") else "") + ".")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("time")
    s.add_argument("key")
    s = sub.add_parser("stability")
    s.add_argument("n", nargs="?", type=int, default=20)
    s.add_argument("--repeat", action="store_true")
    sub.add_parser("table")
    s = sub.add_parser("run")
    s.add_argument("--hours", type=float, default=12)
    s.add_argument("--seconds", type=float, default=0, help="seconds per book, when it was not timed")
    s.add_argument("--min-background", type=int, default=300)
    s.add_argument("--then-train", action="store_true")
    s = sub.add_parser("top")
    s.add_argument("--n", type=int, default=DAILY_BOOKS)
    sub.add_parser("status")
    args = ap.parse_args()
    db = connect()
    try:
        if args.cmd in ("time", "stability", "table"):  # run and top take the lock themselves
            if not take_lock(args.cmd):
                sys.exit("Another reading job is running (data/reader.lock).")
        {"time": cmd_time, "stability": cmd_stability, "table": cmd_table, "run": cmd_run, "top": cmd_top, "status": cmd_status}[args.cmd](db, args)
    except ReaderError as e:
        sys.exit(f"Reading model: {e}")
    finally:
        if args.cmd in ("time", "stability", "table"):
            drop_lock()


if __name__ == "__main__":
    main()
