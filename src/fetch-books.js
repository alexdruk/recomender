'use strict';
// Milestone 3, part A. Makes the liked pile and the unlabeled pile comparable.
//
//   node src/fetch-books.js              everything below, in order
//   node src/fetch-books.js --liked      only step 1
//   node src/fetch-books.js --matched    only steps 2 and 3
//   node src/fetch-books.js --retry      also try again the books that failed before
//   node src/fetch-books.js --target 1150   how many matched books to pick
//
// 1. Liked books: download <mirror>/b/<id>/fb2 for every liked book and parse
//    it with the same FB2 parser as the feed. Text goes to data/text-fb2/.
//    The EPUB text in data/text/ is kept and used only when the FB2 is missing.
// 2. Matched background: pick random catalog books (Russian, fiction, FB2, not
//    deleted, not liked, not in the feed, not a collection, at least 20,000
//    words when the catalog knows the size) so that their years are spread
//    like the years of the liked books. The pick is saved, so it never changes.
// 3. Download and parse the picked books into "matched_books" (source = matched).
// 4. Ratings: count and average of the catalog's user ratings for every book.
//
// Polite: every request goes through net.js (one at a time, 3 s pause).
// Cached: each download is kept in data/fb2/<id>.fb2.zip and never asked for
// twice. Resumable: stop it at any time and run it again.

const fs = require('node:fs');
const path = require('node:path');
const Database = require('better-sqlite3');
const c = require('./common');
const net = require('./net');
const { parseFb2 } = require('./parse-fb2');
const { rebuildMatched } = require('./dedupe');
const enrich = require('./enrich');

const SEED = 20261112;
const DEFAULT_TARGET = 1150; // a little over 1,000, because some downloads fail
const CHARS_PER_WORD = 5.5; // catalog "Chars" divided by our word count, measured on the feed books
const MIN_WORDS = 20000;
const MAX_WORDS = 400000; // above this a book counts as a collection (same rule as enrich.js)
const STOP_AFTER_FAILS = 15; // this many failures in a row means the mirror is down or unhappy with us

function ensureTables(db) {
  db.exec(`
    -- one row per download attempt target; "id" is the Flibusta book id asked for
    CREATE TABLE IF NOT EXISTS fb2_fetch (
      id INTEGER NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, status TEXT NOT NULL,
      reason TEXT, bytes INTEGER, tried_at TEXT,
      PRIMARY KEY (kind, key)
    );
    CREATE TABLE IF NOT EXISTS matched_pick (id INTEGER PRIMARY KEY, year INTEGER, pos INTEGER);
    CREATE TABLE IF NOT EXISTS book_ratings (key TEXT PRIMARY KEY, n INTEGER, avg REAL);
  `);
}

// Small seeded random generator, so the pick is the same on every machine.
function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function shuffle(list, rnd) {
  for (let i = list.length - 1; i > 0; i--) {
    const j = Math.floor(rnd() * (i + 1));
    [list[i], list[j]] = [list[j], list[i]];
  }
  return list;
}

// ---------------------------------------------------------------- one download

async function firstFb2InZip(zipFile) {
  const zip = await c.openZip(zipFile);
  let buf = null;
  try {
    await c.eachZipEntry(zip, async (name, entry, io) => {
      if (!buf && c.extOf(name) === '.fb2' && !c.isJunk(name)) buf = await io.read();
    });
  } finally {
    zip.close();
  }
  return buf;
}

// Returns { buf, bytes, cached } or throws. The zip is kept in data/fb2/.
async function getFb2(cfg, id) {
  const target = path.join(cfg.fb2Dir, `${id}.fb2.zip`);
  let cached = true;
  if (!fs.existsSync(target) || fs.statSync(target).size === 0) {
    cached = false;
    await net.onMirrors(cfg.mirrors, (m) => net.download(`${m}/b/${id}/fb2`, target, { magic: 'PK', timeoutMs: 180000 }), () => {});
  }
  const buf = await firstFb2InZip(target);
  if (!buf) throw new Error('the downloaded zip has no .fb2 file inside');
  return { buf, bytes: fs.statSync(target).size, cached };
}

function shortReason(e) {
  return String(e.message || e).split('\n').filter((l) => l && l !== 'All mirrors failed.').map((l) => l.replace(/ for https?:\S+/, '').replace(/: https?:\S+$/, '')).join('; ').slice(0, 300);
}

// Downloads and parses one book. ids = the ids to try, in order (a liked book
// that was replaced by a newer copy has two). Returns { status, reason }.
async function fetchOne(db, cfg, store, kind, key, ids, sourceFile) {
  const note = db.prepare(`INSERT INTO fb2_fetch (id, kind, key, status, reason, bytes, tried_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
    ON CONFLICT (kind, key) DO UPDATE SET id = excluded.id, status = excluded.status, reason = excluded.reason, bytes = excluded.bytes, tried_at = excluded.tried_at`);
  const reasons = [];
  let network = false;
  for (const id of ids) {
    let got;
    try {
      got = await getFb2(cfg, id);
    } catch (e) {
      reasons.push(`id ${id}: ${shortReason(e)}`);
      network = true;
      continue;
    }
    const t0 = performance.now();
    let result;
    try {
      result = parseFb2(got.buf);
    } catch (e) {
      result = { ok: false, reason: 'parser crashed: ' + e.message };
    }
    const r = store(sourceFile, 'fb2', result, performance.now() - t0);
    if (r.status !== 'failed') {
      note.run(id, kind, key, 'ok', id === ids[0] ? null : `used the newer copy ${id}`, got.bytes);
      return { status: 'ok', cached: got.cached };
    }
    reasons.push(`id ${id}: parse failed: ${r.reason}`);
  }
  note.run(ids[0], kind, key, 'failed', reasons.join(' | '), null);
  return { status: 'failed', reason: reasons.join(' | '), network };
}

// ---------------------------------------------------------------- step 1: liked books

async function fetchLiked(db, cfg, cat, retry) {
  const liked = db.prepare('SELECT key FROM books ORDER BY key').all();
  const store = c.makeIngester(db, 'liked_fb2', cfg.likedFb2TextDir);
  const done = new Map(db.prepare("SELECT key, status FROM fb2_fetch WHERE kind = 'liked'").all().map((r) => [r.key, r.status]));
  const newer = cat.prepare('SELECT GoodId FROM libjoinedbooks WHERE BadId = ? ORDER BY Id DESC LIMIT 1');
  let ok = 0, failed = 0, fresh = 0, failsInRow = 0;
  console.log(`Step 1: FB2 files for ${liked.length} liked books ...`);
  for (const b of liked) {
    const id = Number(String(b.key).split('-')[0]);
    if (!Number.isInteger(id)) {
      failed++;
      continue;
    }
    const before = done.get(b.key);
    if (before === 'ok' || (before === 'failed' && !retry)) {
      before === 'ok' ? ok++ : failed++;
      continue;
    }
    const good = newer.get(id);
    const ids = good && good.GoodId && good.GoodId !== id ? [id, good.GoodId] : [id];
    const r = await fetchOne(db, cfg, store, 'liked', b.key, ids, `${b.key}.fb2`);
    if (r.status === 'ok') {
      ok++;
      failsInRow = 0;
      if (!r.cached) fresh++;
    } else {
      failed++;
      failsInRow++;
      console.log(`  FAILED liked ${b.key}: ${r.reason}`);
      if (failsInRow >= STOP_AFTER_FAILS) throw new Error(`${STOP_AFTER_FAILS} downloads failed in a row. Stopping to be polite; run the command again later.`);
    }
    if ((ok + failed) % 25 === 0) console.log(`  ${ok + failed} of ${liked.length} liked books done (${ok} ok, ${failed} failed)`);
  }
  console.log(`Step 1 done: ${ok} of ${liked.length} liked books have FB2 text (${c.pct(ok, liked.length)}), ${failed} failed, ${fresh} downloaded in this run.`);
  return { ok, failed };
}

// ---------------------------------------------------------------- step 2: pick the matched background

// How many books to pick from each year: the liked books' share of that year
// times the target, rounded so that the total is exactly the target.
function yearQuota(likedYears, target) {
  const count = new Map();
  for (const y of likedYears) count.set(y, (count.get(y) || 0) + 1);
  const total = likedYears.length;
  const rows = [...count].map(([year, n]) => ({ year, exact: (n / total) * target }));
  for (const r of rows) r.n = Math.floor(r.exact);
  let left = target - rows.reduce((s, r) => s + r.n, 0);
  for (const r of [...rows].sort((a, b) => (b.exact - b.n) - (a.exact - a.n))) {
    if (left-- <= 0) break;
    r.n++;
  }
  return rows.sort((a, b) => a.year - b.year);
}

function pickMatched(db, cfg, cat, lookup, map, target) {
  const have = db.prepare('SELECT COUNT(*) AS n FROM matched_pick').get().n;
  if (have >= target) {
    console.log(`Step 2: the matched pick is already saved (${have} books), keeping it.`);
    return have;
  }
  // the liked books used in the models: not collections, not the non-fiction ones
  const likedYears = db.prepare('SELECT COALESCE(cat_year, year) AS y FROM books WHERE COALESCE(is_collection, 0) = 0 AND is_fiction = 1').all()
    .map((r) => r.y).filter((y) => y >= 1800 && y <= 2100);
  const quota = yearQuota(likedYears, target);

  const minChars = Math.round(MIN_WORDS * CHARS_PER_WORD);
  const maxChars = Math.round(MAX_WORDS * CHARS_PER_WORD);
  const byYear = new Map();
  for (const r of cat.prepare(`SELECT BookId AS id, Year AS year, Title AS title FROM libbook
      WHERE Lang = 'ru' AND Deleted = '0' AND FileType = 'fb2' AND Year > 0
        AND (Chars IS NULL OR Chars = 0 OR (Chars >= ? AND Chars <= ?)) ORDER BY BookId`).all(minChars, maxChars)) {
    if (!byYear.has(r.year)) byYear.set(r.year, []);
    byYear.get(r.year).push(r);
  }
  const rnd = mulberry32(SEED);
  for (const list of byYear.values()) shuffle(list, rnd);

  const taken = new Set(db.prepare('SELECT id FROM matched_pick').all().map((r) => r.id));
  const banned = new Set(); // liked and feed book ids
  for (const r of db.prepare("SELECT key FROM books UNION SELECT key FROM feed_books").all()) banned.add(Number(String(r.key).split('-')[0]));
  const likedNorms = new Set(db.prepare("SELECT norm FROM parsed WHERE source IN ('archive', 'feed') AND norm IS NOT NULL").all().map((r) => r.norm.split(' | ')[1]));
  const seenTitles = new Set(); // author + title, so two editions of one book are not both picked

  const usable = (r) => {
    if (taken.has(r.id) || banned.has(r.id)) return false;
    if (enrich.isCollection(r.title, null)) return false;
    const info = lookup(r.id);
    if (!info) return false;
    const kinds = [...new Set(info.genres.map((g) => map.codes[g]).filter(Boolean))].map((g) => map.groups[g]);
    if (!kinds.includes('fiction') || kinds.includes('nonfiction')) return false;
    const title = c.normaliseText(r.title);
    if (!title || likedNorms.has(title)) return false;
    const sig = `${info.authorIds[0] || ''} | ${title}`;
    if (seenTitles.has(sig)) return false;
    seenTitles.add(sig);
    return true;
  };

  const insert = db.prepare('INSERT OR IGNORE INTO matched_pick (id, year, pos) VALUES (?, ?, ?)');
  let pos = db.prepare('SELECT COALESCE(MAX(pos), 0) AS p FROM matched_pick').get().p;
  const picked = [];
  const short = [];
  const cursor = new Map();
  const takeFrom = (year, n) => {
    const list = byYear.get(year) || [];
    let i = cursor.get(year) || 0;
    let got = 0;
    while (got < n && i < list.length) {
      const r = list[i++];
      if (usable(r)) {
        picked.push({ id: r.id, year });
        taken.add(r.id);
        got++;
      }
    }
    cursor.set(year, i);
    return got;
  };
  for (const q of quota) {
    let need = q.n - takeFrom(q.year, q.n);
    // a year with too few books borrows from the nearest years
    for (let d = 1; need > 0 && d <= 10; d++) {
      need -= takeFrom(q.year - d, need);
      if (need > 0) need -= takeFrom(q.year + d, need);
    }
    if (need > 0) short.push(`${q.year} (short by ${need})`);
  }
  shuffle(picked, rnd); // download order is random, so a half-finished run is still a fair sample
  db.transaction(() => {
    for (const p of picked) insert.run(p.id, p.year, ++pos);
  })();
  console.log(`Step 2: picked ${picked.length} matched background books across ${quota.length} years (liked books with a known year: ${likedYears.length}).`);
  if (short.length) console.log('  years without enough catalog books: ' + short.join(', '));
  return have + picked.length;
}

// ---------------------------------------------------------------- step 3: download the matched books

async function fetchMatched(db, cfg, retry) {
  const picks = db.prepare('SELECT id FROM matched_pick ORDER BY pos').all();
  const store = c.makeIngester(db, 'matched', cfg.matchedTextDir);
  const done = new Map(db.prepare("SELECT key, status FROM fb2_fetch WHERE kind = 'matched'").all().map((r) => [r.key, r.status]));
  let ok = 0, failed = 0, failsInRow = 0, sinceRebuild = 0;
  const refresh = () => {
    rebuildMatched(db, cfg);
    enrich.enrichAll(db, cfg);
    sinceRebuild = 0;
  };
  console.log(`Step 3: downloading ${picks.length} matched background books (about ${Math.round((picks.length - done.size) * 5 / 60)} minutes for the ones still missing) ...`);
  for (const p of picks) {
    const key = String(p.id);
    const before = done.get(key);
    if (before === 'ok' || (before === 'failed' && !retry)) {
      before === 'ok' ? ok++ : failed++;
      continue;
    }
    const r = await fetchOne(db, cfg, store, 'matched', key, [p.id], `matched/${p.id}.fb2`);
    if (r.status === 'ok') {
      ok++;
      failsInRow = 0;
      sinceRebuild++;
    } else {
      failed++;
      failsInRow++;
      console.log(`  FAILED matched ${key}: ${r.reason}`);
      if (failsInRow >= STOP_AFTER_FAILS) {
        refresh();
        throw new Error(`${STOP_AFTER_FAILS} downloads failed in a row. Stopping to be polite; run the command again later.`);
      }
    }
    // every 10 new books: put them into "matched_books", so the labelling page can show them
    if (sinceRebuild >= 10) refresh();
    if ((ok + failed) % 50 === 0) console.log(`  ${ok + failed} of ${picks.length} matched books done (${ok} ok, ${failed} failed)`);
  }
  refresh();
  const usable = db.prepare("SELECT COUNT(*) AS n FROM unlabeled_books WHERE source = 'matched'").get().n;
  console.log(`Step 3 done: ${ok} of ${picks.length} matched books parsed, ${failed} failed. ${usable} are usable (fiction, Russian, not a collection, not a duplicate).`);
  return { ok, failed, usable };
}

// ---------------------------------------------------------------- step 4: ratings

function saveRatings(db, cat) {
  const has = cat.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'librate'").get();
  c.setMeta(db, 'has_ratings', !!has);
  if (!has) {
    console.log('Step 4: the catalog has no ratings table (librate), skipped.');
    return;
  }
  const q = cat.prepare('SELECT COUNT(*) AS n, AVG(CAST(Rate AS REAL)) AS avg FROM librate WHERE BookId = ?');
  const keys = db.prepare('SELECT key FROM books UNION SELECT key FROM unlabeled_books').all().map((r) => r.key);
  const put = db.prepare('INSERT INTO book_ratings (key, n, avg) VALUES (?, ?, ?)');
  let rated = 0;
  db.transaction(() => {
    db.prepare('DELETE FROM book_ratings').run();
    for (const key of keys) {
      const r = q.get(Number(String(key).split('-')[0]));
      put.run(key, r.n, r.n ? r.avg : null);
      if (r.n) rated++;
    }
  })();
  console.log(`Step 4: ratings looked up for ${keys.length} books, ${rated} have at least one rating.`);
}

// ---------------------------------------------------------------- main

async function main() {
  const args = process.argv.slice(2);
  const only = args.includes('--liked') ? 'liked' : args.includes('--matched') ? 'matched' : null;
  const retry = args.includes('--retry');
  const t = args.indexOf('--target');
  const target = t !== -1 ? Number(args[t + 1]) || DEFAULT_TARGET : DEFAULT_TARGET;

  const cfg = c.loadConfig();
  net.setPause(cfg.pauseSeconds);
  if (!fs.existsSync(cfg.catalogPath)) throw new Error('data/catalog.db not found. Run "node src/fetch-catalog.js" first.');
  const db = c.openDb(cfg);
  db.pragma('busy_timeout = 10000');
  ensureTables(db);
  fs.mkdirSync(cfg.fb2Dir, { recursive: true });
  const cat = new Database(cfg.catalogPath, { readonly: true });
  const catalog = enrich.openCatalog(cfg);
  try {
    const { map } = enrich.enrichAll(db, cfg);
    if (only !== 'matched') await fetchLiked(db, cfg, cat, retry);
    if (only !== 'liked') {
      pickMatched(db, cfg, cat, catalog.lookup, map, target);
      await fetchMatched(db, cfg, retry);
    }
    saveRatings(db, cat);
  } finally {
    cat.close();
    catalog.close();
    db.close();
  }
}

if (require.main === module) {
  main().catch((e) => {
    console.error('STOPPED: ' + (e.message || e));
    process.exit(1);
  });
}

module.exports = { main, yearQuota };
