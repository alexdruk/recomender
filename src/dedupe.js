'use strict';
// Steps 5 and 6. Reads every parsed book, finds books with the same
// normalised author + title, keeps the one with the longest text, and rebuilds
// the final tables:  books (label "liked")  and  feed_books (label "unlabeled").
// Dropped copies are listed in the "duplicates" table.
// The tables are rebuilt from scratch each time, so running twice changes nothing.

const fs = require('node:fs');
const path = require('node:path');
const c = require('./common');

const COLS = 'key, source_file, format, authors, title, genres, lang, src_lang, translators, series, series_no, year, annotation, chars, words, is_translation';

function rebuild(db, source, table, label, textDir) {
  const rows = db.prepare('SELECT * FROM parsed WHERE source = ? ORDER BY key').all(source);
  const groups = new Map();
  for (const r of rows) {
    if (!r.norm || r.parse_status === 'failed') continue;
    if (!groups.has(r.norm)) groups.set(r.norm, []);
    groups.get(r.norm).push(r);
  }
  const losers = new Map(); // source_file -> kept row
  for (const list of groups.values()) {
    if (list.length < 2) continue;
    list.sort((a, b) => b.chars - a.chars || (a.key < b.key ? -1 : 1)); // longest text first
    for (const r of list.slice(1)) losers.set(r.source_file, list[0]);
  }

  const insertBook = db.prepare(`INSERT INTO ${table} (${COLS}, label, parse_status) SELECT ${COLS}, ?, parse_status FROM parsed WHERE source = ? AND source_file = ?`);
  const insertDup = db.prepare('INSERT INTO duplicates (source, source_file, key, title, chars, kept_key, kept_chars, norm) VALUES (?, ?, ?, ?, ?, ?, ?, ?)');
  db.transaction(() => {
    db.prepare(`DELETE FROM ${table}`).run();
    db.prepare('DELETE FROM duplicates WHERE source = ?').run(source);
    for (const r of rows) {
      const kept = losers.get(r.source_file);
      if (kept) insertDup.run(source, r.source_file, r.key, r.title, r.chars, kept.key, kept.chars, r.norm);
      else insertBook.run(label, source, r.source_file);
    }
  })();
  // the dropped copy's text file is not needed any more
  for (const r of rows) if (losers.has(r.source_file)) fs.rmSync(path.join(textDir, r.key + '.txt'), { force: true });
  return { total: rows.length, kept: rows.length - losers.size, duplicates: losers.size };
}

function rebuildArchive(db, cfg) {
  return rebuild(db, 'archive', 'books', 'liked', cfg.textDir);
}

function rebuildFeed(db, cfg) {
  return rebuild(db, 'feed', 'feed_books', 'unlabeled', cfg.feedTextDir);
}

function rebuildMatched(db, cfg) {
  return rebuild(db, 'matched', 'matched_books', 'unlabeled', cfg.matchedTextDir);
}

async function main() {
  const cfg = c.loadConfig();
  const db = c.openDb(cfg);
  const a = rebuildArchive(db, cfg);
  console.log(`Dedupe: ${a.total} parsed archive books -> ${a.kept} in "books", ${a.duplicates} duplicates recorded`);
  for (const d of db.prepare("SELECT * FROM duplicates WHERE source = 'archive' ORDER BY key").all()) {
    console.log(`  dropped ${d.key} "${d.title}" (${d.chars} chars), kept ${d.kept_key} (${d.kept_chars} chars)`);
  }
  db.close();
}

if (require.main === module) {
  main().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}

module.exports = { main, rebuildArchive, rebuildFeed, rebuildMatched };
