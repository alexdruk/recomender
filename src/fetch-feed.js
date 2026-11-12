'use strict';
// Milestone 2, part B. The feed collector.
//
// Reads the listing at <mirror>/daily/, finds every f.fb2.<from>-<to>.zip whose
// first id is above the last processed id, and for each one, oldest first:
// downloads it, parses every book into the feed tables, saves the texts, and
// stores the last processed id in the "meta" table. f.n.* files are ignored.
//
// Safe to run again at any time: a zip that was processed is never downloaded
// again, and a zip that is still in data/feed/ is used instead of a download.

const fs = require('node:fs');
const path = require('node:path');
const c = require('./common');
const net = require('./net');
const { parseFeedZip } = require('./probe-feed');
const { rebuildFeed } = require('./dedupe');

const FEED_NAME = /f\.fb2\.(\d+)-(\d+)\.zip/g;

function parseListing(html) {
  const seen = new Map();
  for (const m of html.matchAll(FEED_NAME)) seen.set(m[0], { name: m[0], from: Number(m[1]), to: Number(m[2]) });
  return [...seen.values()].sort((a, b) => a.from - b.from);
}

function noteGap(db, gap) {
  const gaps = c.getMeta(db, 'feed_gaps') || [];
  if (!gaps.some((g) => g.from === gap.from && g.to === gap.to)) gaps.push(gap);
  c.setMeta(db, 'feed_gaps', gaps);
  console.log(`  GAP: ids ${gap.from} to ${gap.to} (${gap.to - gap.from + 1} ids) are not covered by any file on the listing. ${gap.why}`);
}

async function main() {
  const cfg = c.loadConfig();
  net.setPause(cfg.pauseSeconds);
  const db = c.openDb(cfg);
  db.exec(`CREATE TABLE IF NOT EXISTS feed_files (
    name TEXT PRIMARY KEY, first_id INTEGER, last_id INTEGER, bytes INTEGER,
    files INTEGER, ok INTEGER, failed INTEGER, how TEXT, processed_at TEXT)`);
  fs.mkdirSync(cfg.feedDir, { recursive: true });

  console.log('Feed collector: reading the listing at <mirror>/daily/ ...');
  const { mirror, value: listing } = await net.onMirrors(cfg.mirrors, async (m) => {
    const list = parseListing(await net.getText(m + '/daily/'));
    if (!list.length) throw new Error('listing has no f.fb2.*.zip files');
    return list;
  });
  const lastId = c.getMeta(db, 'feed_last_id');
  console.log(`  ${mirror} lists ${listing.length} update files, ids ${listing[0].from} to ${listing[listing.length - 1].to}`);
  console.log(`  last processed id: ${lastId == null ? 'none yet' : lastId}`);

  if (lastId != null && listing[0].from > lastId + 1) {
    noteGap(db, { from: lastId + 1, to: listing[0].from - 1, why: 'These update files have already left the listing; the collector was not run often enough.', seen: new Date().toISOString() });
  }
  for (let i = 1; i < listing.length; i++) {
    if (listing[i].from > listing[i - 1].to + 1 && (lastId == null || listing[i].from > lastId)) {
      noteGap(db, { from: listing[i - 1].to + 1, to: listing[i].from - 1, why: 'There is a hole between two files on the listing.', seen: new Date().toISOString() });
    }
  }

  const todo = listing.filter((f) => lastId == null || f.from > lastId);
  if (!todo.length) console.log('  nothing new to download.');

  const record = db.prepare(`INSERT OR REPLACE INTO feed_files VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))`);
  let done = 0;
  for (const f of todo) {
    const target = path.join(cfg.feedDir, f.name);
    let how = 'downloaded';
    if (fs.existsSync(target) && fs.statSync(target).size > 0) {
      how = 'already in data/feed/';
    } else {
      console.log(`  downloading ${f.name} ...`);
      await net.download(`${mirror}/daily/${f.name}`, target, { magic: 'PK' });
    }
    const bytes = fs.statSync(target).size;
    const { entries, counts } = await parseFeedZip(db, cfg, target, f.name, false);
    db.transaction(() => {
      record.run(f.name, f.from, f.to, bytes, entries.length, counts.ok + counts.ok_lenient, counts.failed, how);
      c.setMeta(db, 'feed_last_id', Math.max(f.to, c.getMeta(db, 'feed_last_id') || 0));
    })();
    if (cfg.deleteZipAfterParse) fs.rmSync(target, { force: true });
    done++;
    console.log(`  ${f.name}: ${how}, ${(bytes / 1048576).toFixed(1)} MB, ${entries.length} files, ${counts.ok + counts.ok_lenient} parsed, ${counts.failed} failed${cfg.deleteZipAfterParse ? ', zip deleted' : ''}`);
  }

  const feed = rebuildFeed(db, cfg);
  let extra = '';
  try {
    const enriched = require('./enrich').enrichAll(db, cfg);
    extra = `, ${enriched.feed.candidates} of them are candidates (Russian fiction, not in the archive, not a collection)`;
  } catch (e) {
    if (e.code !== 'MODULE_NOT_FOUND') throw e;
  }
  console.log(`Feed collector: ${done} update file${done === 1 ? '' : 's'} processed. "feed_books" now has ${feed.kept} books${extra}. Last processed id: ${c.getMeta(db, 'feed_last_id')}.`);
  db.close();
}

if (require.main === module) {
  main().catch((e) => {
    console.error(e.message || e);
    process.exit(1);
  });
}

module.exports = { main, parseListing };
