'use strict';
// Automatic cleaning, run at the end of every daily loop:  npm run cleanup
//
//   1. update zips in data/feed/ that were already parsed
//   2. texts of feed books older than 14 days that are in no list
//   3. cached EPUB files older than 30 days
//
// Never touched: texts of liked books, vectors, the model, answers, config.
//
//   node src/cleanup.js --dry-run     only say what would be deleted
//
// A feed text is kept when the book
//   - arrived less than 14 days ago ("cleanup.feedTextDays" in config.json), or
//   - has an answer (Liked, Disliked) or waits in "To send" or "Reading", or
//   - is on the recommendations page now, or
//   - passed the filters but has no vectors yet (the text is still needed), or
//   - was not looked at by the daily loop yet.
// Once the vectors and the style numbers are saved, nothing reads the text again.

const fs = require('node:fs');
const path = require('node:path');
const c = require('./common');

const DAY = 86400000;
const sizeOf = (file) => {
  try {
    return fs.statSync(file).size;
  } catch (e) {
    return 0;
  }
};

function hasTable(db, name) {
  return !!db.prepare("SELECT 1 FROM sqlite_master WHERE name = ?").get(name);
}

// 1. update zips that are recorded as processed
function oldZips(db, cfg) {
  if (!cfg.deleteZipAfterParse || !fs.existsSync(cfg.feedDir) || !hasTable(db, 'feed_files')) return [];
  const done = new Set(db.prepare('SELECT name FROM feed_files').all().map((r) => r.name));
  return fs.readdirSync(cfg.feedDir).filter((f) => /\.zip$/i.test(f) && done.has(f)).map((f) => path.join(cfg.feedDir, f));
}

// 2. feed texts that nothing needs any more
function oldFeedTexts(db, cfg, now) {
  if (!fs.existsSync(cfg.feedTextDir) || !hasTable(db, 'feed_scores')) return [];
  const before = new Date(now - cfg.cleanup.feedTextDays * DAY).toISOString();
  const top = Number(c.getMeta(db, 'top_percent'));
  const topPercent = top > 0 && top <= 100 ? top : cfg.topPercent;
  const rows = db.prepare(`SELECT s.key, s.passed FROM feed_scores s
    WHERE s.added_at < ?
      AND s.key NOT IN (SELECT key FROM answers)
      AND s.key NOT IN (SELECT key FROM book_state)
      AND NOT (s.passed = 1 AND s.score IS NOT NULL AND s.percentile <= ?)`).all(before, topPercent);
  const out = [];
  for (const r of rows) {
    const file = path.join(cfg.feedTextDir, r.key + '.txt');
    if (!fs.existsSync(file)) continue;
    if (r.passed) {
      const vectors = ['emb', 'emb-open'].every((d) => fs.existsSync(path.join(cfg.dataDir, d, 'feed', r.key + '.npy')));
      if (!vectors) continue;
    }
    out.push(file);
  }
  return out;
}

// 3. cached EPUB files, except the ones that still wait in "To send"
function oldEpubs(db, cfg, now) {
  if (!fs.existsSync(cfg.epubDir)) return [];
  const waiting = new Set(hasTable(db, 'book_state')
    ? db.prepare("SELECT key FROM book_state WHERE state = 'to_send'").all().map((r) => String(r.key).split('-')[0])
    : []);
  return fs.readdirSync(cfg.epubDir)
    .filter((f) => /\.epub$/i.test(f) && !waiting.has(f.replace(/\.epub$/i, '')))
    .map((f) => path.join(cfg.epubDir, f))
    .filter((file) => now - fs.statSync(file).mtimeMs > cfg.cleanup.epubDays * DAY);
}

// Returns { zips, texts, epubs, bytes }. dryRun = true deletes nothing.
function clean(db, cfg, dryRun = false) {
  const now = Date.now();
  const lists = { zips: oldZips(db, cfg), texts: oldFeedTexts(db, cfg, now), epubs: oldEpubs(db, cfg, now) };
  const stats = { zips: lists.zips.length, texts: lists.texts.length, epubs: lists.epubs.length, bytes: 0, dryRun };
  for (const file of [...lists.zips, ...lists.texts, ...lists.epubs]) {
    stats.bytes += sizeOf(file);
    if (!dryRun) fs.rmSync(file, { force: true });
  }
  return stats;
}

function report(stats) {
  const verb = stats.dryRun ? 'would delete' : 'deleted';
  return `Cleaning: ${verb} ${stats.zips} parsed update zips, ${stats.texts} old feed texts, ${stats.epubs} old EPUB files (${(stats.bytes / 1048576).toFixed(1)} MB).`;
}

async function main() {
  const cfg = c.loadConfig();
  const db = c.openDb(cfg);
  db.pragma('busy_timeout = 60000');
  const stats = clean(db, cfg, process.argv.includes('--dry-run'));
  db.close();
  console.log(report(stats));
}

if (require.main === module) {
  main().catch((e) => {
    console.error(e.message || e);
    process.exit(1);
  });
}

module.exports = { main, clean, report };
