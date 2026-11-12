'use strict';
// Step 7. Writes reports/inventory.md. Every number comes from data/books.db.

const fs = require('node:fs');
const path = require('node:path');
const c = require('./common');

function median(sorted) {
  if (!sorted.length) return null;
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : Math.round((sorted[mid - 1] + sorted[mid]) / 2);
}

function buildReport(db) {
  const one = (sql, ...args) => db.prepare(sql).get(...args);
  const all = (sql, ...args) => db.prepare(sql).all(...args);
  const out = [];
  const add = (s = '') => out.push(s);

  const units = one("SELECT COUNT(*) n FROM parsed WHERE source = 'archive'").n;
  const parsedOk = one("SELECT COUNT(*) n FROM parsed WHERE source = 'archive' AND parse_status != 'failed'").n;
  const lenient = one("SELECT COUNT(*) n FROM parsed WHERE source = 'archive' AND parse_status = 'ok_lenient'").n;
  const failed = units - parsedOk;
  const dups = one("SELECT COUNT(*) n FROM duplicates WHERE source = 'archive'").n;
  const books = one('SELECT COUNT(*) n FROM books').n;
  const good = one("SELECT COUNT(*) n, SUM(chars) chars, SUM(words) words FROM books WHERE parse_status != 'failed'");
  const files = one("SELECT COUNT(*) n, SUM(size) bytes FROM files WHERE kind != 'junk'");
  const junk = one("SELECT COUNT(*) n FROM files WHERE kind = 'junk'").n;
  const zipBytes = c.getMeta(db, 'archive_zip_bytes');

  add('# Library inventory');
  add();
  add('Built from `data/books.db` by `src/inventory.js`. All archive books carry the label `liked`.');
  add();

  add('## Totals');
  add();
  add(c.mdTable(['What', 'Count'], [
    ['Archive.zip size', zipBytes ? `${(zipBytes / 1048576).toFixed(1)} MB` : 'unknown'],
    ['Files unpacked (without macOS junk)', c.num(files.n)],
    ['macOS junk files ignored (`__MACOSX`, `._*`)', c.num(junk)],
    ['Nested archives unpacked', c.num(c.getMeta(db, 'archive_nested_archives') || 0)],
    ['Book files found', c.num(units)],
    ['Parsed successfully', `${c.num(parsedOk)} (${c.pct(parsedOk, units)})`],
    ['... of which needed the lenient parser', c.num(lenient)],
    ['Parse failures', c.num(failed)],
    ['Duplicates removed', c.num(dups)],
    ['Rows in `books`', c.num(books)],
    ['Unique parsed books', c.num(good.n)],
    ['Total text', `${c.num(good.chars || 0)} characters, ${c.num(good.words || 0)} words`],
  ]));

  add('## Files in the archive by extension');
  add();
  add('Most files are the insides of EPUB books (chapters, pictures, fonts).');
  add();
  add(c.mdTable(['Extension', 'Files', 'Size MB'], all("SELECT ext, COUNT(*) n, SUM(size) b FROM files WHERE kind != 'junk' GROUP BY ext ORDER BY n DESC").map((r) => [r.ext, c.num(r.n), (r.b / 1048576).toFixed(1)])));

  add('## Parse failures');
  add();
  const fails = all("SELECT source_file, format, fail_reason FROM parsed WHERE source = 'archive' AND parse_status = 'failed' ORDER BY source_file");
  if (!fails.length) add('None. Every book file in the archive was parsed.\n');
  else add(c.mdTable(['File', 'Format', 'Reason'], fails.map((r) => [r.source_file, r.format, r.fail_reason])));
  const soft = all("SELECT source_file, fail_reason FROM parsed WHERE source = 'archive' AND parse_status != 'failed' AND fail_reason IS NOT NULL ORDER BY source_file");
  if (soft.length) {
    add('Parsed, but with a warning:');
    add();
    add(c.mdTable(['File', 'Warning'], soft.map((r) => [r.source_file, r.fail_reason])));
  }

  add('## Duplicates');
  add();
  add('Two books are duplicates when author + title are the same after lowercasing, replacing ё with е and removing punctuation (middle names are ignored). The longest text is kept.');
  add();
  add(c.mdTable(['Dropped file', 'Title', 'Chars', 'Kept key', 'Kept chars'],
    all("SELECT * FROM duplicates WHERE source = 'archive' ORDER BY key").map((d) => [d.source_file, d.title, c.num(d.chars), d.kept_key, c.num(d.kept_chars)])));

  add('## By format');
  add();
  add(c.mdTable(['Format', 'Books'], all('SELECT format, COUNT(*) n FROM books GROUP BY format ORDER BY n DESC').map((r) => [r.format, c.num(r.n)])));

  add('## By language');
  add();
  add(c.mdTable(['Language', 'Books', 'Share'], all("SELECT COALESCE(lang, '(not stated)') lang, COUNT(*) n FROM books GROUP BY 1 ORDER BY n DESC").map((r) => [r.lang, c.num(r.n), c.pct(r.n, books)])));

  add('## Original vs translated');
  add();
  add('A book counts as translated when it names a translator, or when its source language differs from its language.');
  add();
  const tr = all("SELECT CASE is_translation WHEN 1 THEN 'Translated' WHEN 0 THEN 'Original' ELSE 'Unknown (not parsed)' END kind, COUNT(*) n FROM books GROUP BY 1 ORDER BY n DESC");
  add(c.mdTable(['Kind', 'Books', 'Share'], tr.map((r) => [r.kind, c.num(r.n), c.pct(r.n, books)])));
  const srcLangs = all('SELECT src_lang, COUNT(*) n FROM books WHERE src_lang IS NOT NULL GROUP BY 1 ORDER BY n DESC');
  if (srcLangs.length) add(c.mdTable(['Source language', 'Books'], srcLangs.map((r) => [r.src_lang, c.num(r.n)])));
  else add('No book in the archive states its source language (the EPUB files do not carry that field).\n');

  add('## By genre');
  add();
  const withGenre = one('SELECT COUNT(*) n FROM books WHERE genres IS NOT NULL').n;
  const genres = all('SELECT j.value g, COUNT(*) n FROM books b, json_each(b.genres) j GROUP BY 1 ORDER BY n DESC, g');
  add(`Books with at least one genre: ${c.num(withGenre)} of ${c.num(books)} (${c.pct(withGenre, books)}).`);
  add();
  if (!genres.length) {
    add('**No genre data.** The archive books are EPUB files converted from FB2, and the converter did not copy the genre into the EPUB metadata. Genres will have to come from somewhere else (for example the Flibusta book id, which is in every file name).');
    add();
  } else {
    add(c.mdTable(['Genre', 'Books'], genres.map((r) => [r.g, c.num(r.n)])));
  }

  add('## Top 30 authors');
  add();
  // the same person can appear with and without a middle name, so group by first + last name
  const byAuthor = new Map();
  for (const r of all("SELECT authors, words FROM books WHERE authors IS NOT NULL")) {
    for (const name of c.jsonList(r.authors)) {
      const k = c.normaliseAuthor(name);
      if (!byAuthor.has(k)) byAuthor.set(k, { names: new Map(), n: 0, w: 0 });
      const a = byAuthor.get(k);
      a.names.set(name, (a.names.get(name) || 0) + 1);
      a.n++;
      a.w += r.words || 0;
    }
  }
  const authors = [...byAuthor.values()]
    .map((a) => ({ a: [...a.names].sort((x, y) => y[1] - x[1] || y[0].length - x[0].length)[0][0], n: a.n, w: a.w }))
    .sort((x, y) => y.n - x.n || y.w - x.w || (x.a < y.a ? -1 : 1));
  add(`${c.num(authors.length)} different authors in total.`);
  add();
  add(c.mdTable(['#', 'Author', 'Books', 'Words'], authors.slice(0, 30).map((r, i) => [i + 1, r.a, c.num(r.n), c.num(r.w)])));

  add('## Series');
  add();
  const inSeries = one('SELECT COUNT(*) n FROM books WHERE series IS NOT NULL').n;
  const seriesCount = one('SELECT COUNT(DISTINCT series) n FROM books WHERE series IS NOT NULL').n;
  add(`${c.num(inSeries)} books (${c.pct(inSeries, books)}) belong to a series; ${c.num(books - inSeries)} are standalone. There are ${c.num(seriesCount)} different series. Some of them are publisher series rather than story series.`);
  add();
  const series = all(`SELECT series, COUNT(*) n, GROUP_CONCAT(series_no, ', ') nos FROM (SELECT series, series_no FROM books WHERE series IS NOT NULL ORDER BY series, CAST(series_no AS REAL)) GROUP BY series ORDER BY n DESC, series`);
  const multi = series.filter((s) => s.n > 1);
  add(`Series with more than one book (${multi.length}):`);
  add();
  add(c.mdTable(['Series', 'Books', 'Numbers present'], multi.map((r) => [r.series, c.num(r.n), r.nos || ''])));
  add(`Series with a single book: ${series.length - multi.length}.`);
  add();

  add('## Length distribution');
  add();
  const words = all("SELECT words FROM books WHERE parse_status != 'failed' ORDER BY words").map((r) => r.words);
  if (words.length) {
    const q = (p) => words[Math.min(words.length - 1, Math.floor(p * words.length))];
    add(`Words per book: shortest ${c.num(words[0])}, lower quarter ${c.num(q(0.25))}, median ${c.num(median(words))}, upper quarter ${c.num(q(0.75))}, longest ${c.num(words[words.length - 1])}.`);
    add();
    const buckets = [
      ['under 10,000 (story)', 0, 10000],
      ['10,000 - 39,999 (novella)', 10000, 40000],
      ['40,000 - 79,999 (short novel)', 40000, 80000],
      ['80,000 - 119,999 (novel)', 80000, 120000],
      ['120,000 - 199,999 (long novel)', 120000, 200000],
      ['200,000 - 499,999 (very long or omnibus)', 200000, 500000],
      ['500,000 and more (multi-book collection)', 500000, Infinity],
    ];
    add(c.mdTable(['Words', 'Books', 'Share'], buckets.map(([name, lo, hi]) => {
      const n = words.filter((w) => w >= lo && w < hi).length;
      return [name, c.num(n), c.pct(n, words.length)];
    })));
    add('Longest books:');
    add();
    add(c.mdTable(['Key', 'Title', 'Words'], all("SELECT key, title, words FROM books WHERE parse_status != 'failed' ORDER BY words DESC LIMIT 5").map((r) => [r.key, r.title, c.num(r.words)])));
  }

  add('## Decade distribution');
  add();
  add('The year is the date the book was written, as stated in the file. When that is missing, the publication year is used instead.');
  add();
  const decades = all("SELECT CASE WHEN year IS NULL THEN '(no year)' ELSE (year / 10 * 10) || 's' END d, COUNT(*) n FROM books GROUP BY 1 ORDER BY year IS NULL, year / 10");
  add(c.mdTable(['Decade', 'Books', 'Share'], decades.map((r) => [r.d, c.num(r.n), c.pct(r.n, books)])));

  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim() + '\n';
}

async function main() {
  const cfg = c.loadConfig();
  const db = c.openDb(cfg);
  fs.mkdirSync(cfg.reportsDir, { recursive: true });
  const file = path.join(cfg.reportsDir, 'inventory.md');
  fs.writeFileSync(file, buildReport(db), 'utf8');
  console.log(`Inventory report written to ${path.relative(cfg.root, file)}`);
  db.close();
}

if (require.main === module) {
  main().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}

module.exports = { main };
