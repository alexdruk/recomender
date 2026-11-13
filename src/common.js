'use strict';
// Shared helpers: config, database, text utilities, and the "ingest" routine
// that stores one parsed book. Used by every other script in src/.

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const Database = require('better-sqlite3');
const yauzl = require('yauzl');
const saxes = require('saxes');
const { Parser: LenientParser } = require('htmlparser2');

const ROOT = path.resolve(__dirname, '..');

function loadConfig() {
  const cfg = JSON.parse(fs.readFileSync(path.join(ROOT, 'config.json'), 'utf8'));
  const dataDir = path.resolve(ROOT, cfg.dataDir || 'data');
  return {
    root: ROOT,
    mirrors: cfg.mirrors || [],
    dataDir,
    archiveZip: path.join(dataDir, 'Archive.zip'),
    libraryDir: path.join(dataDir, 'library'),
    textDir: path.join(dataDir, 'text'),
    feedDir: path.join(dataDir, 'feed'),
    feedTextDir: path.join(dataDir, 'feed', 'text'),
    fb2Dir: path.join(dataDir, 'fb2'), // cache of downloaded .fb2.zip files
    likedFb2TextDir: path.join(dataDir, 'text-fb2'), // liked books, text from the FB2 file
    matchedTextDir: path.join(dataDir, 'matched', 'text'),
    ratedTextDir: path.join(dataDir, 'rated', 'text'), // new downloads for the rated-matched set
    dbPath: path.join(dataDir, 'books.db'),
    catalogPath: path.join(dataDir, 'catalog.db'),
    sqlDir: path.join(dataDir, 'sql'),
    pagesDir: path.join(dataDir, 'pages'),
    genreMapPath: path.join(dataDir, 'genre-map.json'),
    pauseSeconds: Math.max(3, Number(cfg.pauseSeconds) || 3),
    deleteZipAfterParse: cfg.deleteZipAfterParse !== false,
    catalogTables: cfg.catalogTables || [],
    reportsDir: path.join(ROOT, 'reports'),
  };
}

// ---------------------------------------------------------------- database

const BOOK_COLUMNS = `
  key TEXT PRIMARY KEY,
  source_file TEXT NOT NULL,
  format TEXT,
  authors TEXT,
  title TEXT,
  genres TEXT,
  lang TEXT,
  src_lang TEXT,
  translators TEXT,
  series TEXT,
  series_no TEXT,
  year INTEGER,
  annotation TEXT,
  chars INTEGER,
  words INTEGER,
  is_translation INTEGER,
  label TEXT,
  parse_status TEXT`;

function openDb(cfg) {
  fs.mkdirSync(cfg.dataDir, { recursive: true });
  const db = new Database(cfg.dbPath);
  db.pragma('journal_mode = WAL');
  db.exec(`
    -- every file found on disk after unpacking
    CREATE TABLE IF NOT EXISTS files (
      path TEXT PRIMARY KEY,
      ext TEXT,
      size INTEGER,
      kind TEXT
    );
    -- one row per book file we tried to parse (archive and feed), before dedupe
    CREATE TABLE IF NOT EXISTS parsed (
      source TEXT NOT NULL,
      source_file TEXT NOT NULL,
      key TEXT NOT NULL,
      format TEXT,
      authors TEXT,
      title TEXT,
      genres TEXT,
      lang TEXT,
      src_lang TEXT,
      translators TEXT,
      series TEXT,
      series_no TEXT,
      year INTEGER,
      annotation TEXT,
      chars INTEGER,
      words INTEGER,
      is_translation INTEGER,
      parse_status TEXT,
      fail_reason TEXT,
      doc_id TEXT,
      norm TEXT,
      parse_ms REAL,
      PRIMARY KEY (source, source_file)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS parsed_key ON parsed (source, key);
    CREATE TABLE IF NOT EXISTS books (${BOOK_COLUMNS});
    CREATE TABLE IF NOT EXISTS feed_books (${BOOK_COLUMNS});
    -- milestone 3: random catalog books with the same year spread as the liked books
    CREATE TABLE IF NOT EXISTS matched_books (${BOOK_COLUMNS});
    -- milestone 3b: the rated-matched comparison set. "rated_pick" lists every book of the set;
    -- reused = 1 means the book is already in matched_books, the others are in rated_books
    CREATE TABLE IF NOT EXISTS rated_books (${BOOK_COLUMNS});
    CREATE TABLE IF NOT EXISTS rated_pick (id INTEGER PRIMARY KEY, year INTEGER, bin REAL, pos INTEGER, reused INTEGER);
    -- milestone 3: my answers from the labelling page (label: never / maybe / want)
    CREATE TABLE IF NOT EXISTS labels (
      key TEXT PRIMARY KEY,
      label TEXT NOT NULL,
      time TEXT NOT NULL
    );
    -- milestone 3: favourite liked books
    CREATE TABLE IF NOT EXISTS stars (
      key TEXT PRIMARY KEY,
      time TEXT NOT NULL
    );
    -- books dropped by dedupe, and which book was kept instead
    CREATE TABLE IF NOT EXISTS duplicates (
      source TEXT NOT NULL,
      source_file TEXT NOT NULL,
      key TEXT NOT NULL,
      title TEXT,
      chars INTEGER,
      kept_key TEXT NOT NULL,
      kept_chars INTEGER,
      norm TEXT,
      PRIMARY KEY (source, source_file)
    );
    -- small key/value notes used by the reports (timings, sizes, ...)
    CREATE TABLE IF NOT EXISTS meta (
      name TEXT PRIMARY KEY,
      value TEXT
    );
  `);
  return db;
}

function setMeta(db, name, value) {
  db.prepare('INSERT INTO meta (name, value) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value')
    .run(name, JSON.stringify(value));
}

function getMeta(db, name) {
  const row = db.prepare('SELECT value FROM meta WHERE name = ?').get(name);
  return row ? JSON.parse(row.value) : undefined;
}

// ---------------------------------------------------------------- text helpers

function cleanSpaces(s) {
  return String(s == null ? '' : s).replace(/[\s ­]+/g, ' ').trim();
}

// Join text lines into a clean body: trimmed lines, at most one blank line in a row.
function tidyText(lines) {
  const out = [];
  for (const raw of lines) {
    const line = cleanSpaces(raw);
    if (line === '' && (out.length === 0 || out[out.length - 1] === '')) continue;
    out.push(line);
  }
  while (out.length && out[out.length - 1] === '') out.pop();
  return out.join('\n') + (out.length ? '\n' : '');
}

function countWords(text) {
  const m = text.match(/[\p{L}\p{N}]+(?:[-'’][\p{L}\p{N}]+)*/gu);
  return m ? m.length : 0;
}

function countChars(text) {
  let n = 0;
  for (const _ of text) n++; // counts real characters, not UTF-16 halves
  return n;
}

// lowercase, ё -> е, punctuation removed, spaces collapsed
function normaliseText(s) {
  return cleanSpaces(
    String(s || '')
      .toLowerCase()
      .replace(/ё/g, 'е')
      .replace(/[^\p{L}\p{N}]+/gu, ' ')
  );
}

// Author names come as "First Middle Last". Middle names are often missing in
// one edition and present in another, so we only keep the first and last word.
function normaliseAuthor(name) {
  const words = normaliseText(name).split(' ').filter(Boolean);
  if (words.length <= 2) return words.join(' ');
  return words[0] + ' ' + words[words.length - 1];
}

function normKey(authors, title) {
  const a = (authors || []).map(normaliseAuthor).filter(Boolean).sort().join(', ');
  const t = normaliseText(title);
  if (!t) return null;
  return a + ' | ' + t;
}

function yearFrom(s) {
  const m = String(s || '').match(/(?<!\d)(1[0-9]{3}|20[0-9]{2})(?!\d)/);
  return m ? Number(m[1]) : null;
}

function normLang(s) {
  const v = cleanSpaces(s).toLowerCase();
  if (!v) return null;
  const map = { rus: 'ru', russian: 'ru', 'ru-ru': 'ru', eng: 'en', english: 'en', 'en-us': 'en', 'en-gb': 'en', ukr: 'uk', ua: 'uk', bel: 'be', deu: 'de', ger: 'de', fra: 'fr', fre: 'fr' };
  return map[v] || v;
}

// Flibusta names look like "Author_Title.155848.fb2.epub" or "895576.fb2".
// The number is the Flibusta book id, which makes a stable, readable key.
function flibustaId(fileName) {
  const base = path.basename(fileName);
  const m = base.match(/(?:^|[._])(\d{3,})\.fb2/i);
  return m ? m[1] : null;
}

function makeKey(sourceFile, taken) {
  const id = flibustaId(sourceFile);
  const hash = crypto.createHash('sha1').update(sourceFile).digest('hex').slice(0, 10);
  let key = id ? id : 'x' + hash;
  if (taken && taken.has(key)) key = key + '-' + hash.slice(0, 6);
  return key;
}

// ---------------------------------------------------------------- bytes and names

function decodeBytes(buf, label) {
  try {
    return new TextDecoder(label).decode(buf);
  } catch {
    return null;
  }
}

// Zip entry names: use UTF-8 when the zip says so or when the bytes are valid
// UTF-8; otherwise assume the old DOS Cyrillic code page (cp866).
function decodeZipName(raw, utf8Flag) {
  if (utf8Flag) return raw.toString('utf8');
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(raw);
  } catch {
    return new TextDecoder('ibm866').decode(raw);
  }
}

// Turn a name from inside a zip into a safe relative path (no "..", no leading "/").
function safeRelPath(name) {
  const parts = name.replace(/\\/g, '/').split('/').filter((p) => p && p !== '.');
  if (parts.some((p) => p === '..')) return null;
  return parts.join('/').normalize('NFC');
}

function isJunk(relPath) {
  const parts = relPath.split('/');
  return parts.includes('__MACOSX') || parts.some((p) => p.startsWith('._') || p === '.DS_Store' || p === 'Thumbs.db');
}

function extOf(relPath) {
  const base = path.basename(relPath).toLowerCase();
  if (base.endsWith('.fb2.zip')) return '.fb2.zip';
  const e = path.extname(base);
  return e || '(none)';
}

// ---------------------------------------------------------------- zip reading

function openZip(file) {
  return new Promise((resolve, reject) => {
    yauzl.open(file, { lazyEntries: true, decodeStrings: false, autoClose: false }, (err, zip) => (err ? reject(err) : resolve(zip)));
  });
}

function openZipBuffer(buf) {
  return new Promise((resolve, reject) => {
    yauzl.fromBuffer(buf, { lazyEntries: true, decodeStrings: false }, (err, zip) => (err ? reject(err) : resolve(zip)));
  });
}

// Calls onEntry(name, entry, read) for each entry, one at a time.
// read() returns a Promise<Buffer>; stream() returns a Promise<ReadableStream>.
function eachZipEntry(zip, onEntry) {
  return new Promise((resolve, reject) => {
    zip.on('error', reject);
    zip.on('end', resolve);
    zip.on('entry', (entry) => {
      const name = decodeZipName(entry.fileName, (entry.generalPurposeBitFlag & 0x800) !== 0);
      const stream = () => new Promise((res, rej) => zip.openReadStream(entry, (e, s) => (e ? rej(e) : res(s))));
      const read = async () => {
        const s = await stream();
        const chunks = [];
        for await (const c of s) chunks.push(c);
        return Buffer.concat(chunks);
      };
      Promise.resolve(onEntry(name, entry, { read, stream })).then(() => zip.readEntry(), reject);
    });
    zip.readEntry();
  });
}

// ---------------------------------------------------------------- XML to a small tree

// A node is { name, attrs, children } where children are nodes or strings.
// Names are lowercased and stripped of namespace prefixes ("dc:title" -> "title");
// the full name is kept in .qname. Text inside any tag listed in skipText is not kept
// (used for <binary>, which holds base64 images).
function makeTreeBuilder(skipText) {
  const root = { name: '#root', qname: '#root', attrs: {}, children: [] };
  const stack = [root];
  return {
    root,
    open(qname, attrs) {
      const q = String(qname).toLowerCase();
      const node = { name: q.includes(':') ? q.slice(q.indexOf(':') + 1) : q, qname: q, attrs: attrs || {}, children: [] };
      stack[stack.length - 1].children.push(node);
      stack.push(node);
    },
    text(t) {
      const top = stack[stack.length - 1];
      if (skipText && skipText.has(top.name)) return;
      const kids = top.children;
      if (kids.length && typeof kids[kids.length - 1] === 'string') kids[kids.length - 1] += t;
      else kids.push(t);
    },
    close() {
      if (stack.length > 1) stack.pop();
    },
  };
}

function parseXmlStrict(xml, skipText) {
  const b = makeTreeBuilder(skipText);
  const p = new saxes.SaxesParser({ xmlns: false });
  let firstError = null;
  p.on('error', (e) => {
    if (!firstError) firstError = e;
  });
  p.on('opentag', (n) => b.open(n.name, n.attributes));
  p.on('text', (t) => b.text(t));
  p.on('cdata', (t) => b.text(t));
  p.on('closetag', () => b.close());
  try {
    p.write(xml).close();
  } catch (e) {
    if (!firstError) firstError = e;
  }
  if (firstError) throw new Error(String(firstError.message || firstError).split('\n')[0]);
  return b.root;
}

function parseXmlLenient(xml, skipText) {
  const b = makeTreeBuilder(skipText);
  const p = new LenientParser(
    {
      onopentag: (name, attrs) => b.open(name, attrs),
      ontext: (t) => b.text(t),
      onclosetag: () => b.close(),
    },
    { xmlMode: true, decodeEntities: true }
  );
  p.write(xml);
  p.end();
  return b.root;
}

// Strict first; if the XML is broken, lenient. Returns { root, lenient, strictError }.
function parseXml(xml, skipText) {
  try {
    return { root: parseXmlStrict(xml, skipText), lenient: false, strictError: null };
  } catch (e) {
    return { root: parseXmlLenient(xml, skipText), lenient: true, strictError: e.message };
  }
}

function findAll(node, name, out = []) {
  for (const c of node.children) {
    if (typeof c === 'string') continue;
    if (c.name === name) out.push(c);
    findAll(c, name, out);
  }
  return out;
}

function findFirst(node, name) {
  for (const c of node.children) {
    if (typeof c === 'string') continue;
    if (c.name === name) return c;
    const deep = findFirst(c, name);
    if (deep) return deep;
  }
  return null;
}

function childrenNamed(node, name) {
  return node ? node.children.filter((c) => typeof c !== 'string' && c.name === name) : [];
}

function textOf(node) {
  if (!node) return '';
  if (typeof node === 'string') return node;
  let s = '';
  for (const c of node.children) s += textOf(c);
  return s;
}

function attr(node, name) {
  if (!node) return undefined;
  for (const k of Object.keys(node.attrs)) {
    const lk = k.toLowerCase();
    if (lk === name || lk.endsWith(':' + name)) return node.attrs[k];
  }
  return undefined;
}

// ---------------------------------------------------------------- book units on disk

// Walk a folder and list the things that count as "one book":
//   - a folder whose name ends in .epub (an EPUB that was stored unpacked)
//   - an .epub file
//   - an .fb2 file
//   - any other non-junk file (reported as unsupported)
function listBookUnits(rootDir) {
  const units = [];
  const walk = (dir) => {
    const entries = fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => (a.name < b.name ? -1 : 1));
    for (const e of entries) {
      const full = path.join(dir, e.name);
      const rel = path.relative(rootDir, full).split(path.sep).join('/');
      if (isJunk(rel)) continue;
      if (e.isDirectory()) {
        const looksLikeEpub = /\.epub$/i.test(e.name) || fs.existsSync(path.join(full, 'META-INF', 'container.xml'));
        if (looksLikeEpub) units.push({ rel, full, format: 'epub', isDir: true });
        else walk(full);
      } else if (e.isFile()) {
        const ext = extOf(rel);
        if (ext === '.epub') units.push({ rel, full, format: 'epub', isDir: false });
        else if (ext === '.fb2') units.push({ rel, full, format: 'fb2', isDir: false });
        else if (ext === '.zip' || ext === '.fb2.zip') continue; // already unpacked next to it by unzip.js
        else units.push({ rel, full, format: ext.replace(/^\./, ''), isDir: false, unsupported: true });
      }
    }
  };
  if (fs.existsSync(rootDir)) walk(rootDir);
  return units;
}

// ---------------------------------------------------------------- storing one parsed book

const UPSERT_PARSED = `
  INSERT INTO parsed (source, source_file, key, format, authors, title, genres, lang, src_lang, translators,
                      series, series_no, year, annotation, chars, words, is_translation, parse_status,
                      fail_reason, doc_id, norm, parse_ms)
  VALUES (@source, @source_file, @key, @format, @authors, @title, @genres, @lang, @src_lang, @translators,
          @series, @series_no, @year, @annotation, @chars, @words, @is_translation, @parse_status,
          @fail_reason, @doc_id, @norm, @parse_ms)
  ON CONFLICT (source, source_file) DO UPDATE SET
    key = excluded.key, format = excluded.format, authors = excluded.authors, title = excluded.title,
    genres = excluded.genres, lang = excluded.lang, src_lang = excluded.src_lang,
    translators = excluded.translators, series = excluded.series, series_no = excluded.series_no,
    year = excluded.year, annotation = excluded.annotation, chars = excluded.chars, words = excluded.words,
    is_translation = excluded.is_translation, parse_status = excluded.parse_status,
    fail_reason = excluded.fail_reason, doc_id = excluded.doc_id, norm = excluded.norm,
    parse_ms = excluded.parse_ms`;

// Creates a function store(sourceFile, format, result, parseMs).
// "result" is what parseFb2 / parseEpub return: { ok, lenient, reason, meta, text }.
// It writes the text file and upserts the row, so running twice never duplicates.
function makeIngester(db, source, textDir) {
  fs.mkdirSync(textDir, { recursive: true });
  const upsert = db.prepare(UPSERT_PARSED);
  const taken = new Map(); // key -> source_file, for keys used by other files of this source
  for (const r of db.prepare('SELECT key, source_file FROM parsed WHERE source = ?').all(source)) taken.set(r.key, r.source_file);

  return function store(sourceFile, format, result, parseMs) {
    let key = makeKey(sourceFile);
    if (taken.has(key) && taken.get(key) !== sourceFile) key = makeKey(sourceFile, taken);
    taken.set(key, sourceFile);

    const meta = (result && result.meta) || {};
    const text = result && result.ok ? result.text : '';
    const textFile = path.join(textDir, key + '.txt');
    let status = 'failed';
    let reason = result ? result.reason || null : 'no result';
    if (result && result.ok) {
      status = result.lenient ? 'ok_lenient' : 'ok';
      fs.writeFileSync(textFile, text, 'utf8');
    } else {
      fs.rmSync(textFile, { force: true });
    }
    const list = (a) => (a && a.length ? JSON.stringify(a) : null);
    const translators = meta.translators || [];
    const lang = normLang(meta.lang);
    const srcLang = normLang(meta.src_lang);
    const isTranslation = status === 'failed' ? null : translators.length > 0 || (srcLang && lang && srcLang !== lang) ? 1 : 0;
    upsert.run({
      source,
      source_file: sourceFile,
      key,
      format,
      authors: list(meta.authors),
      title: cleanSpaces(meta.title) || null,
      genres: list(meta.genres),
      lang,
      src_lang: srcLang,
      translators: list(translators),
      series: cleanSpaces(meta.series) || null,
      series_no: meta.series_no != null && cleanSpaces(meta.series_no) !== '' ? cleanSpaces(meta.series_no) : null,
      year: meta.year || null,
      annotation: cleanSpaces(meta.annotation) || null,
      chars: status === 'failed' ? null : countChars(text),
      words: status === 'failed' ? null : countWords(text),
      is_translation: isTranslation,
      parse_status: status,
      fail_reason: reason,
      doc_id: cleanSpaces(meta.doc_id) || null,
      norm: status === 'failed' ? null : normKey(meta.authors, meta.title),
      parse_ms: parseMs == null ? null : Math.round(parseMs * 100) / 100,
    });
    return { key, status, reason };
  };
}

// Remove rows (and text files) for source files that no longer exist on disk.
function pruneParsed(db, source, textDir, keepFiles, formatFilter) {
  const keep = new Set(keepFiles);
  const rows = db.prepare('SELECT source_file, key, format FROM parsed WHERE source = ?').all(source);
  const del = db.prepare('DELETE FROM parsed WHERE source = ? AND source_file = ?');
  let n = 0;
  for (const r of rows) {
    if (formatFilter && !formatFilter(r)) continue;
    if (keep.has(r.source_file)) continue;
    del.run(source, r.source_file);
    fs.rmSync(path.join(textDir, r.key + '.txt'), { force: true });
    n++;
  }
  return n;
}

// ---------------------------------------------------------------- markdown helpers for the reports

function mdCell(v) {
  return String(v == null ? '' : v).replace(/\|/g, '\\|').replace(/\s+/g, ' ');
}

function mdTable(headers, rows) {
  if (!rows.length) return '_none_\n';
  const out = ['| ' + headers.map(mdCell).join(' | ') + ' |', '|' + headers.map(() => ' --- ').join('|') + '|'];
  for (const r of rows) out.push('| ' + r.map(mdCell).join(' | ') + ' |');
  return out.join('\n') + '\n';
}

function pct(part, whole) {
  return whole ? (Math.round((part / whole) * 1000) / 10).toFixed(1) + '%' : 'n/a';
}

function num(n) {
  return n == null ? '' : Number(n).toLocaleString('en-US');
}

function jsonList(s) {
  if (!s) return [];
  try {
    const v = JSON.parse(s);
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

module.exports = {
  mdTable, pct, num, jsonList,
  loadConfig, openDb, setMeta, getMeta, BOOK_COLUMNS,
  cleanSpaces, tidyText, countWords, countChars, normaliseText, normaliseAuthor, normKey, yearFrom, normLang,
  flibustaId, makeKey, decodeBytes, decodeZipName, safeRelPath, isJunk, extOf,
  openZip, openZipBuffer, eachZipEntry,
  parseXml, parseXmlStrict, parseXmlLenient, findAll, findFirst, childrenNamed, textOf, attr,
  listBookUnits, makeIngester, pruneParsed,
};
