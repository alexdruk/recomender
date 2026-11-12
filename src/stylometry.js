'use strict';
// Milestone 3, part B, step 5. Stylometry: about 16 numbers that describe HOW a
// book is written (not what it is about), measured on the full text.
//
//   node src/stylometry.js
//
// Reads the text of every liked book (FB2 text when we have it, EPUB text
// otherwise) and of every book in the view "unlabeled_books", and fills the
// table "stylometry" (key, kind, text_source, bytes, features as JSON).
// A book whose text file did not change is not measured again.

const fs = require('node:fs');
const path = require('node:path');
const c = require('./common');

const WORD = /[\p{L}\p{N}]+(?:[-'’][\p{L}\p{N}]+)*/gu;
const SAMPLE = 5000; // words in one vocabulary sample
const SAMPLES = 5; // samples spread evenly through the book

const set = (s) => new Set(s.split(/\s+/).filter(Boolean));
const FIRST_PERSON = set('я меня мне мной мною мой моя мое мои моего моей моему моим моих мою моем моими');
const SECOND_PERSON = set(`ты тебя тебе тобой тобою твой твоя твое твои твоего твоей твоему твоим твоих твою твоем твоими
  вы вас вам вами ваш ваша ваше ваши вашего вашей вашему вашим ваших вашу вашем вашими`);

const HEADING = /^(глава|часть|пролог|эпилог|интерлюдия|книга|том|chapter|part|prologue|epilogue)(?![\p{L}])/iu;
const NUMBER_ONLY = /^(\d{1,3}|[ivxlc]{1,7})[.)]?$/i;
const SENTENCE_END = /[.!?…]+["»”)]*(?:\s+|$)/u;

const FEATURES = [
  ['sent_len', 'average sentence length (words)'],
  ['sent_len_sd', 'spread of sentence length'],
  ['short_sent', 'share of very short sentences (3 words or fewer)'],
  ['para_len', 'average paragraph length (words)'],
  ['dialogue', 'share of paragraphs that are dialogue lines (start with a dash)'],
  ['first_person', 'share of first-person pronouns (я, меня, мой ...)'],
  ['second_person', 'share of second-person pronouns (ты, вы ...)'],
  ['vocab', `vocabulary richness: different words in a ${SAMPLE}-word sample`],
  ['exclaim', 'exclamation marks per 1,000 words'],
  ['question', 'question marks per 1,000 words'],
  ['ellipsis', 'ellipses per 1,000 words'],
  ['comma', 'commas per 1,000 words'],
  ['chapter_len', 'average chapter length (log of words)'],
  ['word_len', 'average word length (letters)'],
  ['long_words', 'share of long words (10 letters or more)'],
  ['digits', 'numbers per 1,000 words'],
  ['latin', 'Latin-alphabet words per 1,000 words'],
];

function countMatches(text, re) {
  const m = text.match(re);
  return m ? m.length : 0;
}

// Chapters are counted from the text: a short block of one or two lines between
// blank lines that starts with "Глава", "Часть", a bare number and so on. If a
// book has no such headings, any short stand-alone line that does not end like
// a sentence counts as a heading.
function countChapters(lines) {
  let named = 0;
  let generic = 0;
  let block = [];
  const flush = () => {
    if (block.length >= 1 && block.length <= 2 && block.every((l) => l.length <= 80)) {
      const first = block[0];
      if (HEADING.test(first) || NUMBER_ONLY.test(first)) named++;
      const all = block.join(' ');
      if (all.length <= 80 && /\p{L}/u.test(all) && !/[.!?…,;:]["»”)]*$/u.test(all) && !/^[—–-]/.test(first)) generic++;
    }
    block = [];
  };
  for (const line of lines) {
    if (line === '') flush();
    else block.push(line);
  }
  flush();
  if (named >= 2) return named;
  if (generic >= 2) return generic;
  return 1;
}

function measure(text) {
  const lines = text.split('\n');
  const paragraphs = lines.filter((l) => l !== '');
  const words = [];
  let sentences = 0, sentWordsSq = 0, shortSent = 0, dialogue = 0;
  for (const p of paragraphs) {
    if (/^[—–-]\s?\S/.test(p)) dialogue++;
    for (const s of p.split(SENTENCE_END)) {
      const n = countMatches(s, WORD);
      if (!n) continue;
      sentences++;
      sentWordsSq += n * n;
      if (n <= 3) shortSent++;
    }
    const m = p.match(WORD);
    if (m) for (const w of m) words.push(w.toLowerCase().replace(/ё/g, 'е'));
  }
  const n = words.length;
  if (n < 200 || !sentences) return null;

  let first = 0, second = 0, letters = 0, long = 0, digits = 0, latin = 0;
  for (const w of words) {
    if (FIRST_PERSON.has(w)) first++;
    else if (SECOND_PERSON.has(w)) second++;
    letters += w.length;
    if (w.length >= 10) long++;
    if (/\d/.test(w)) digits++;
    else if (/^[a-z'’-]+$/.test(w)) latin++;
  }

  // vocabulary richness on a fixed sample size, so long and short books can be compared
  let vocab = null;
  if (n >= SAMPLE) {
    const count = Math.min(SAMPLES, Math.floor(n / SAMPLE));
    let sum = 0;
    for (let i = 0; i < count; i++) {
      const start = count === 1 ? 0 : Math.floor(((n - SAMPLE) * i) / (count - 1));
      sum += new Set(words.slice(start, start + SAMPLE)).size / SAMPLE;
    }
    vocab = sum / count;
  }

  const meanSent = n / sentences;
  const per1000 = (x) => (x / n) * 1000;
  const f = {
    sent_len: meanSent,
    sent_len_sd: Math.sqrt(Math.max(0, sentWordsSq / sentences - meanSent * meanSent)),
    short_sent: shortSent / sentences,
    para_len: n / paragraphs.length,
    dialogue: dialogue / paragraphs.length,
    first_person: first / n,
    second_person: second / n,
    vocab,
    exclaim: per1000(countMatches(text, /!/g)),
    question: per1000(countMatches(text, /\?/g)),
    ellipsis: per1000(countMatches(text, /…|\.{3,}/g)),
    comma: per1000(countMatches(text, /,/g)),
    chapter_len: Math.log10(n / countChapters(lines)),
    word_len: letters / n,
    long_words: long / n,
    digits: per1000(digits),
    latin: per1000(latin),
  };
  for (const k of Object.keys(f)) if (f[k] != null) f[k] = Math.round(f[k] * 1e5) / 1e5;
  return f;
}

// Where the text of each book lives. Liked books: FB2 text first, EPUB text as a fallback.
function listTexts(db, cfg) {
  const out = [];
  const fb2 = new Set(db.prepare("SELECT key FROM parsed WHERE source = 'liked_fb2' AND parse_status != 'failed'").all().map((r) => r.key));
  for (const r of db.prepare("SELECT key FROM books WHERE parse_status != 'failed'").all()) {
    const useFb2 = fb2.has(r.key) && fs.existsSync(path.join(cfg.likedFb2TextDir, r.key + '.txt'));
    out.push({ key: r.key, kind: 'liked', source: useFb2 ? 'fb2' : 'epub', file: path.join(useFb2 ? cfg.likedFb2TextDir : cfg.textDir, r.key + '.txt') });
  }
  const view = db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = 'unlabeled_books'").get();
  if (view) {
    for (const r of db.prepare('SELECT key, source FROM unlabeled_books').all()) {
      out.push({ key: r.key, kind: 'unlabeled', source: r.source, file: path.join(r.source === 'feed' ? cfg.feedTextDir : cfg.matchedTextDir, r.key + '.txt') });
    }
  }
  return out;
}

async function main() {
  const cfg = c.loadConfig();
  const db = c.openDb(cfg);
  db.pragma('busy_timeout = 10000');
  db.exec(`CREATE TABLE IF NOT EXISTS stylometry (
    key TEXT NOT NULL, kind TEXT NOT NULL, text_source TEXT, bytes INTEGER, features TEXT,
    PRIMARY KEY (key, kind))`);
  c.setMeta(db, 'stylometry_features', FEATURES.map(([name, what]) => ({ name, what })));
  const have = new Map(db.prepare('SELECT key, kind, text_source, bytes FROM stylometry').all().map((r) => [`${r.kind}/${r.key}`, r]));
  const put = db.prepare(`INSERT INTO stylometry (key, kind, text_source, bytes, features) VALUES (?, ?, ?, ?, ?)
    ON CONFLICT (key, kind) DO UPDATE SET text_source = excluded.text_source, bytes = excluded.bytes, features = excluded.features`);
  let measured = 0, kept = 0, missing = 0, tooShort = 0;
  for (const t of listTexts(db, cfg)) {
    if (!fs.existsSync(t.file)) {
      missing++;
      continue;
    }
    const bytes = fs.statSync(t.file).size;
    const old = have.get(`${t.kind}/${t.key}`);
    if (old && old.text_source === t.source && old.bytes === bytes) {
      kept++;
      continue;
    }
    const f = measure(fs.readFileSync(t.file, 'utf8'));
    if (!f) tooShort++;
    put.run(t.key, t.kind, t.source, bytes, f ? JSON.stringify(f) : null);
    measured++;
  }
  console.log(`Stylometry: ${measured} books measured, ${kept} unchanged, ${missing} without a text file, ${tooShort} too short to measure. ${FEATURES.length} numbers per book.`);
  db.close();
}

if (require.main === module) {
  main().catch((e) => {
    console.error(e.message || e);
    process.exit(1);
  });
}

module.exports = { main, measure, listTexts, FEATURES };
