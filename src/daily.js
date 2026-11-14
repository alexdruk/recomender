'use strict';
// The daily loop:  npm run daily
//
//   1. fetch-feed.js: read the listing at <mirror>/daily/, download every
//      f.fb2.*.zip above the last processed id (one at a time, with pauses),
//      parse the books, delete the zip. A gap in the listing is logged.
//   2. filter every feed book that has no answer yet (settings: config.json "filters")
//   3. fingerprint the books that passed: stylometry here, embeddings in Python
//   4. score them with the saved model and store score and percentile ("feed_scores")
//   5. cleanup.js: delete parsed zips, feed texts older than 14 days that are in
//      no list, and cached EPUB files older than 30 days
//
// Safe to run again at any time: nothing is downloaded or measured twice.

const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const c = require('./common');
const fetchFeed = require('./fetch-feed');
const { measure } = require('./stylometry');
const cleanup = require('./cleanup');

function ensureTables(db) {
  db.exec(`
    -- one row per feed book: did it pass the filters, its score and its percentile (0 = best)
    CREATE TABLE IF NOT EXISTS feed_scores (
      key TEXT PRIMARY KEY, added_at TEXT, passed INTEGER, reason TEXT, series_note TEXT,
      score REAL, percentile REAL, closest TEXT, scored_at TEXT);
    -- my Like / Dislike presses on the page. kind: 'card' = pressed on a card I never read,
    -- 'read' = pressed on the "Reading" page, after the book was sent to iBooks
    CREATE TABLE IF NOT EXISTS answers (key TEXT PRIMARY KEY, answer TEXT NOT NULL, time TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'card');
    -- where a book is that I picked but did not judge yet: 'to_send' (the waiting list) or 'reading' (sent to iBooks)
    CREATE TABLE IF NOT EXISTS book_state (key TEXT PRIMARY KEY, state TEXT NOT NULL, time TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS stylometry (
      key TEXT NOT NULL, kind TEXT NOT NULL, text_source TEXT, bytes INTEGER, features TEXT,
      PRIMARY KEY (key, kind));
  `);
  // databases made before the "Reading" page have no "kind" column yet
  if (!db.prepare("SELECT 1 FROM pragma_table_info('answers') WHERE name = 'kind'").get()) {
    db.exec("ALTER TABLE answers ADD COLUMN kind TEXT NOT NULL DEFAULT 'card'");
  }
}

// The lowest number I liked in each series: my library plus Likes from the page.
function likedSeries(db) {
  const min = new Map();
  const rows = db.prepare(`
    SELECT series, series_no FROM books WHERE series IS NOT NULL
    UNION ALL SELECT series, series_no FROM parsed WHERE source = 'liked_fb2' AND series IS NOT NULL
    UNION ALL SELECT f.series, f.series_no FROM feed_books f JOIN answers a ON a.key = f.key AND a.answer = 'like' WHERE f.series IS NOT NULL`).all();
  for (const r of rows) {
    const name = c.normaliseText(r.series);
    const no = Number(r.series_no);
    if (!name || !Number.isFinite(no)) continue;
    if (!(min.get(name) <= no)) min.set(name, no);
  }
  return min;
}

// Returns { passed, reason, seriesNote } for one feed book. Genres and series are the ones inside the FB2 file.
function judge(book, f, ctx) {
  const fail = (reason) => ({ passed: 0, reason, seriesNote: null });
  if (book.parse_status === 'failed') return fail('the file could not be read');
  if (f.russianOnly && book.lang !== 'ru') return fail(`language is ${book.lang || 'not given'}`);
  if (f.fictionOnly) {
    const kinds = c.jsonList(book.genres).map((g) => ctx.map.groups[ctx.map.codes[g]]);
    if (kinds.includes('nonfiction') || !kinds.includes('fiction')) return fail('not fiction');
  }
  if (f.skipCollections && book.is_collection) return fail('a collection');
  if (f.minWords && (book.words || 0) < f.minWords) return fail(`shorter than ${f.minWords} words`);
  if (f.skipLibraryBooks && (book.in_archive || ctx.readIds.has(String(book.key).split('-')[0]))) return fail('already in my library');
  let seriesNote = null;
  if (book.series) {
    const no = book.series_no == null || book.series_no === '' ? NaN : Number(book.series_no);
    const likedMin = ctx.liked.get(c.normaliseText(book.series));
    if (!Number.isFinite(no)) seriesNote = 'number in the series not given';
    else if (no <= 1) seriesNote = 'first book of the series';
    else if (likedMin != null && likedMin < no) seriesNote = `I liked book ${likedMin} of this series`;
    else if (f.seriesRule) return fail(`book ${no} of a series I have not started`);
  }
  return { passed: 1, reason: null, seriesNote };
}

function filterBooks(db, cfg) {
  ensureTables(db);
  const map = JSON.parse(fs.readFileSync(cfg.genreMapPath, 'utf8'));
  const hasMarks = db.prepare("SELECT 1 FROM sqlite_master WHERE name = 'read_marks'").get();
  const ctx = {
    map,
    liked: likedSeries(db),
    readIds: new Set(hasMarks ? db.prepare('SELECT id FROM read_marks').all().map((r) => String(r.id)) : []),
  };
  // when the book arrived: the time its update file was processed
  const fileTime = new Map(db.prepare('SELECT name, processed_at FROM feed_files').all().map((r) => [r.name, r.processed_at.replace(' ', 'T') + 'Z']));
  const have = new Map(db.prepare('SELECT key, passed FROM feed_scores').all().map((r) => [r.key, r]));
  const books = db.prepare('SELECT * FROM feed_books WHERE key NOT IN (SELECT key FROM answers)').all();
  const put = db.prepare(`INSERT INTO feed_scores (key, added_at, passed, reason, series_note) VALUES (?, ?, ?, ?, ?)
    ON CONFLICT (key) DO UPDATE SET passed = excluded.passed, reason = excluded.reason, series_note = excluded.series_note`);
  const stats = { fresh: 0, freshPassed: 0, total: 0, passed: 0 };
  db.transaction(() => {
    for (const b of books) {
      const r = judge(b, cfg.filters, ctx);
      const added = fileTime.get(String(b.source_file).split('/')[0]) || new Date().toISOString();
      put.run(b.key, added, r.passed, r.reason, r.seriesNote);
      if (!have.has(b.key)) {
        stats.fresh++;
        stats.freshPassed += r.passed;
      }
    }
  })();
  const t = db.prepare('SELECT COUNT(*) AS n, SUM(passed) AS p FROM feed_scores').get();
  stats.total = t.n;
  stats.passed = t.p || 0;
  return stats;
}

// Stylometry of the whole text for every book that passed and has none yet.
function measureBooks(db, cfg) {
  const rows = db.prepare(`SELECT s.key FROM feed_scores s WHERE s.passed = 1
    AND NOT EXISTS (SELECT 1 FROM stylometry t WHERE t.key = s.key AND t.kind = 'unlabeled')`).all();
  const put = db.prepare(`INSERT INTO stylometry (key, kind, text_source, bytes, features) VALUES (?, 'unlabeled', 'feed', ?, ?)
    ON CONFLICT (key, kind) DO UPDATE SET text_source = excluded.text_source, bytes = excluded.bytes, features = excluded.features`);
  let n = 0;
  for (const r of rows) {
    const file = path.join(cfg.feedTextDir, r.key + '.txt');
    if (!fs.existsSync(file)) continue;
    const f = measure(fs.readFileSync(file, 'utf8'));
    put.run(r.key, fs.statSync(file).size, f ? JSON.stringify(f) : null);
    n++;
  }
  return n;
}

async function main() {
  const cfg = c.loadConfig();
  const started = new Date().toISOString();
  let downloadError = null;
  try {
    await fetchFeed.main();
  } catch (e) {
    downloadError = String(e.message || e);
    console.log('Daily loop: the download step failed. ' + downloadError);
  }
  const db = c.openDb(cfg);
  db.pragma('busy_timeout = 60000');
  const stats = filterBooks(db, cfg);
  const measured = measureBooks(db, cfg);
  c.setMeta(db, 'daily_last_run', { time: started, error: downloadError, fresh: stats.fresh, freshPassed: stats.freshPassed });
  db.close();
  console.log(`Daily loop: ${stats.fresh} new books looked at, ${stats.freshPassed} of them passed the filters, ${measured} measured for style.`);

  if (!fs.existsSync(path.join(cfg.dataDir, 'model', 'recommender.joblib'))) {
    console.log('Daily loop: there is no trained model yet, so nothing was scored. Run "npm run train" first.');
    process.exitCode = 1;
    return;
  }
  const r = spawnSync(cfg.python, [path.join(cfg.root, 'model', 'recommend.py'), 'score'], { cwd: cfg.root, stdio: 'inherit' });
  if (r.status !== 0) {
    console.log('Daily loop: the scoring step failed.');
    process.exitCode = 1;
    return; // no cleaning: the texts may still be needed
  }
  // 5. cleaning: parsed zips, old feed texts that are in no list, old EPUB files
  const db2 = c.openDb(cfg);
  db2.pragma('busy_timeout = 60000');
  try {
    console.log('Daily loop: ' + cleanup.report(cleanup.clean(db2, cfg)));
  } finally {
    db2.close();
  }
}

if (require.main === module) {
  main().catch((e) => {
    console.error(e.message || e);
    process.exit(1);
  });
}

module.exports = { main, filterBooks, ensureTables };
