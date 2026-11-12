'use strict';
// Step 8. Downloads one daily update file (f.fb2.<from>-<to>.zip) from Flibusta,
// parses every book in it with the same FB2 parser, fills the "feed_books"
// table (label "unlabeled") and writes reports/feed-probe.md.
//
// Network use is kept small: one request for the list of update files, then
// one request for one zip. Mirrors are tried in the order given in config.json.
// If the newest zip is already in data/feed/ it is not downloaded again.

const fs = require('node:fs');
const path = require('node:path');
const { Readable } = require('node:stream');
const { pipeline } = require('node:stream/promises');
const c = require('./common');
const { parseFb2 } = require('./parse-fb2');
const { rebuildFeed } = require('./dedupe');

const UA = 'book-feed-probe/1.0';
const FEED_NAME = /f\.fb2\.(\d+)-(\d+)\.zip/g;

// ---------------------------------------------------------------- download

async function listUpdates(mirror) {
  const res = await fetch(mirror + '/daily/', { headers: { 'user-agent': UA }, signal: AbortSignal.timeout(30000) });
  if (!res.ok) throw new Error(`listing returned HTTP ${res.status}`);
  const html = await res.text();
  const seen = new Map();
  for (const m of html.matchAll(FEED_NAME)) seen.set(m[0], { name: m[0], from: Number(m[1]), to: Number(m[2]) });
  const list = [...seen.values()].sort((a, b) => a.to - b.to);
  if (!list.length) throw new Error('listing has no f.fb2.*.zip files');
  return list;
}

async function downloadOne(mirror, name, target) {
  const res = await fetch(`${mirror}/daily/${name}`, { headers: { 'user-agent': UA }, signal: AbortSignal.timeout(600000) });
  if (!res.ok || !res.body) throw new Error(`download returned HTTP ${res.status}`);
  const tmp = target + '.part';
  try {
    await pipeline(Readable.fromWeb(res.body), fs.createWriteStream(tmp));
    const head = Buffer.alloc(4);
    const fd = fs.openSync(tmp, 'r');
    fs.readSync(fd, head, 0, 4, 0);
    fs.closeSync(fd);
    if (head.toString('latin1', 0, 2) !== 'PK') throw new Error('downloaded file is not a zip');
    fs.renameSync(tmp, target);
  } catch (e) {
    fs.rmSync(tmp, { force: true });
    throw e;
  }
}

function localZips(feedDir) {
  return fs.readdirSync(feedDir)
    .filter((n) => /\.zip$/i.test(n))
    .sort((a, b) => {
      const na = Number((a.match(/-(\d+)\.zip$/) || [])[1] || 0);
      const nb = Number((b.match(/-(\d+)\.zip$/) || [])[1] || 0);
      return nb - na || (a < b ? 1 : -1);
    });
}

// Returns { file, name, how, mirror, listing, errors }
async function getFeedZip(cfg) {
  fs.mkdirSync(cfg.feedDir, { recursive: true });
  const errors = [];
  for (const mirror of cfg.mirrors) {
    try {
      const listing = await listUpdates(mirror);
      const newest = listing[listing.length - 1];
      const target = path.join(cfg.feedDir, newest.name);
      if (fs.existsSync(target) && fs.statSync(target).size > 0) {
        return { file: target, name: newest.name, how: 'already downloaded', mirror, listing, errors };
      }
      await downloadOne(mirror, newest.name, target);
      return { file: target, name: newest.name, how: 'downloaded', mirror, listing, errors };
    } catch (e) {
      const cause = e.cause && e.cause.code ? ` (${e.cause.code})` : '';
      errors.push(`${mirror}: ${e.message}${cause}`);
      console.log(`  mirror failed - ${mirror}: ${e.message}${cause}`);
    }
  }
  const local = localZips(cfg.feedDir);
  if (local.length) {
    console.log(`  DOWNLOAD FAILED ON ALL MIRRORS. Using a zip that is already in data/feed/: ${local[0]}`);
    return { file: path.join(cfg.feedDir, local[0]), name: local[0], how: 'local fallback (download failed on all mirrors)', mirror: null, listing: null, errors };
  }
  throw new Error('Feed download failed on all mirrors and there is no zip in data/feed/.\n' + errors.join('\n'));
}

// ---------------------------------------------------------------- parse the zip

async function parseFeedZip(db, cfg, zipFile, zipName) {
  const store = c.makeIngester(db, 'feed', cfg.feedTextDir);
  const entries = [];
  const results = [];
  const zip = await c.openZip(zipFile);
  const t0 = performance.now();
  try {
    await c.eachZipEntry(zip, async (name, entry, io) => {
      if (name.endsWith('/') || c.isJunk(name)) return;
      const ext = c.extOf(name);
      entries.push({ name, ext, size: entry.uncompressedSize, packed: entry.compressedSize });
      const sourceFile = `${zipName}/${name}`;
      if (ext !== '.fb2') {
        results.push({ sourceFile, format: ext.replace(/^\./, ''), result: { ok: false, reason: `unsupported format ${ext}` }, ms: null });
        return;
      }
      const buf = await io.read();
      const t = performance.now();
      let result;
      try {
        result = parseFb2(buf);
      } catch (e) {
        result = { ok: false, reason: 'parser crashed: ' + e.message };
      }
      results.push({ sourceFile, format: 'fb2', result, ms: performance.now() - t });
    });
  } finally {
    zip.close();
  }
  const counts = { ok: 0, ok_lenient: 0, failed: 0 };
  db.transaction(() => {
    // the table describes one update file: forget rows from any other zip first,
    // so a book id never clashes with an older copy
    c.pruneParsed(db, 'feed', cfg.feedTextDir, results.map((r) => r.sourceFile));
    for (const r of results) {
      const s = store(r.sourceFile, r.format, r.result, r.ms);
      counts[s.status]++;
      if (s.status === 'failed') console.log(`  FAILED ${r.sourceFile}: ${s.reason}`);
    }
  })();
  return { entries, counts, wallMs: performance.now() - t0 };
}

// ---------------------------------------------------------------- what counts as fiction

// Genre codes in the feed are a mix of the classic FB2 list (sf_fantasy,
// det_classic, love_sf ...) and newer free-form ones (boyar-anime, fentezi,
// popadantsy-v-magicheskie-miry ...). So the test works on word pieces.
const NONFICTION = /^(sci|nonf|nonfiction|ref|comp|religion|home|science|military_history|geo|economics|business|psychology|health|design|music|cine|theatre|art|painting|architecture|sport|auto|travel_notes|cooking|pedagogy|foreign_(business|psychology|edu|publicism|language|religion|desc|comp|home|other))(_|$)/;
const FICTION = new RegExp(
  '^(sf|det|prose|love|adv|humor|thriller|poetry|dramaturgy|child|children|fantasy|foreign)(_|$)' +
  '|fantas|fentezi|fantastik|fiction|popadan|detectiv|detektiv|adventur|priklyuch|romance|roman(_|$)|romany|lyubov|liubov' +
  '|proza|horror|uzhas|mystic|mistik|litrpg|rpg|anime|wuxia|back_to_ussr|rusreal|story|steampunk|cyberpunk|erotic|action' +
  '|voennaia_literatura|magician_book|vampire_book|gothic_novel|network_literature|fanfic|comedy|tragedy|drama(_|$)|lyrics|tale|sagas|epic|mystery'
);

function genreKind(g) {
  const code = String(g).toLowerCase().replace(/[-\s]+/g, '_');
  if (NONFICTION.test(code)) return 'nonfiction';
  if (FICTION.test(code)) return 'fiction';
  return 'other';
}

function isFictionGenre(g) {
  return genreKind(g) === 'fiction';
}

// A book is fiction when it has at least one fiction genre and no non-fiction genre.
function isFiction(genres) {
  const kinds = genres.map(genreKind);
  return kinds.includes('fiction') && !kinds.includes('nonfiction');
}

// ---------------------------------------------------------------- report

function buildReport(db) {
  const info = c.getMeta(db, 'feed_probe');
  const out = [];
  const add = (s = '') => out.push(s);
  if (!info) return '# Feed probe\n\nNo feed file has been probed yet. Run `node src/probe-feed.js`.\n';

  const parsedRows = db.prepare("SELECT * FROM parsed WHERE source = 'feed' ORDER BY key").all();
  const books = db.prepare("SELECT f.*, p.norm, p.parse_ms FROM feed_books f JOIN parsed p ON p.source = 'feed' AND p.key = f.key ORDER BY f.key").all();
  const ok = books.filter((b) => b.parse_status !== 'failed');
  const failed = parsedRows.filter((b) => b.parse_status === 'failed');
  const feedDups = db.prepare("SELECT * FROM duplicates WHERE source = 'feed' ORDER BY key").all();
  const archiveNorms = new Set(db.prepare("SELECT norm FROM parsed WHERE source = 'archive' AND norm IS NOT NULL").all().map((r) => r.norm));
  const archiveIds = new Set(db.prepare("SELECT source_file FROM parsed WHERE source = 'archive'").all().map((r) => c.flibustaId(r.source_file)).filter(Boolean));
  const inArchive = (b) => archiveIds.has(String(b.key).split('-')[0]) || (b.norm && archiveNorms.has(b.norm));

  add('# Feed probe');
  add();
  add('Built from `data/books.db` by `src/probe-feed.js`. One daily update file from Flibusta was parsed with the same FB2 parser as the library. All feed books carry the label `unlabeled`.');
  add();

  add('## The file');
  add();
  const fb2Entries = info.entries.filter((e) => e.ext === '.fb2');
  add(c.mdTable(['What', 'Value'], [
    ['File', '`' + info.name + '`'],
    ['How it was obtained', info.how + (info.mirror ? ` from ${info.mirror}` : '')],
    ['Zip size', `${c.num(info.bytes)} bytes (${(info.bytes / 1048576).toFixed(1)} MB)`],
    ['Files inside', c.num(info.entries.length)],
    ['Files by extension', Object.entries(info.extCounts).map(([e, n]) => `${e}: ${n}`).join(', ')],
    ['Unpacked size', `${(info.unpackedBytes / 1048576).toFixed(1)} MB`],
    ['Average size of one book', fb2Entries.length ? `${(info.bytes / fb2Entries.length / 1024).toFixed(0)} KB zipped, ${(info.unpackedBytes / fb2Entries.length / 1024).toFixed(0)} KB unpacked` : 'n/a'],
  ]));
  if (info.errors && info.errors.length) {
    add('Problems while downloading:');
    add();
    for (const e of info.errors) add(`- ${e}`);
    add();
  }

  add('## How file names map to book ids');
  add();
  const m = info.mapping;
  add(`- The zip is named \`f.fb2.<first id>-<last id>.zip\`. This one covers ids ${m.from} to ${m.to}, a span of ${c.num(m.span)} ids.`);
  add(`- Inside, each book is one file named \`<book id>.fb2\`. ${m.matching} of ${info.entries.length} files follow this pattern` + (m.examples.length ? ` (for example \`${m.examples.join('`, `')}\`).` : '.'));
  add(`- ${m.inRange} of those ids fall inside the range in the zip name; lowest ${m.min}, highest ${m.max}.`);
  add(`- ${m.span - m.inRange} ids in the range have no file here (${c.pct(m.span - m.inRange, m.span)}). Those are books in other formats (they go into the matching \`f.n.*\` zip) or ids that were never published.`);
  add('- The id is the same number Flibusta uses on its site (`/b/<id>`) and in the names of the library files, so the database key for a feed book is simply that id.');
  if (m.odd.length) add(`- Files that do not follow the pattern: ${m.odd.map((n) => '`' + n + '`').join(', ')}.`);
  add();

  add('## Parsing');
  add();
  const lenient = ok.filter((b) => b.parse_status === 'ok_lenient').length;
  add(c.mdTable(['What', 'Count'], [
    ['Book files', c.num(parsedRows.length)],
    ['Parsed', `${c.num(parsedRows.length - failed.length)} (${c.pct(parsedRows.length - failed.length, parsedRows.length)})`],
    ['... of which needed the lenient parser', c.num(lenient)],
    ['Failed', c.num(failed.length)],
    ['Duplicates inside the file', c.num(feedDups.length)],
    ['Rows in `feed_books`', c.num(books.length)],
  ]));
  if (failed.length) {
    add('Failures:');
    add();
    add(c.mdTable(['File', 'Reason'], failed.map((r) => [r.source_file, r.fail_reason])));
  }
  const soft = parsedRows.filter((r) => r.parse_status === 'ok_lenient');
  if (soft.length) {
    add('Broken XML that the lenient parser rescued:');
    add();
    add(c.mdTable(['File', 'Title', 'Problem'], soft.map((r) => [r.source_file, r.title, r.fail_reason])));
  }

  add('## Parse time per book');
  add();
  const times = parsedRows.filter((r) => r.parse_ms != null).map((r) => r.parse_ms).sort((a, b) => a - b);
  if (times.length) {
    const sum = times.reduce((a, b) => a + b, 0);
    const totalChars = ok.reduce((a, b) => a + (b.chars || 0), 0);
    add(c.mdTable(['What', 'Time'], [
      ['Average per book', `${(sum / times.length).toFixed(1)} ms`],
      ['Median per book', `${times[Math.floor(times.length / 2)].toFixed(1)} ms`],
      ['Slowest book', `${times[times.length - 1].toFixed(1)} ms`],
      ['All books, parsing only', `${(sum / 1000).toFixed(2)} s`],
      ['Whole file, including unzip and saving', `${(info.wallMs / 1000).toFixed(2)} s`],
      ['Speed', totalChars ? `${c.num(Math.round(totalChars / (sum / 1000)))} characters of book text per second` : 'n/a'],
    ]));
    add('Parsing covers decoding the file, reading the XML, and pulling out metadata and body text.');
    add();
  }

  add('## Language distribution');
  add();
  const langs = new Map();
  for (const b of ok) langs.set(b.lang || '(not stated)', (langs.get(b.lang || '(not stated)') || 0) + 1);
  add(c.mdTable(['Language', 'Books', 'Share'], [...langs].sort((a, b) => b[1] - a[1]).map(([l, n]) => [l, c.num(n), c.pct(n, ok.length)])));

  add('## Genre distribution');
  add();
  add('A book can have several genres, so the counts add up to more than the number of books. The last column shows how this project reads each code. The feed mixes the classic FB2 codes (`sf_fantasy`, `det_classic`) with newer free-form ones (`boyar-anime`, `fentezi`).');
  add();
  const genres = new Map();
  let noGenre = 0;
  for (const b of ok) {
    const g = c.jsonList(b.genres);
    if (!g.length) noGenre++;
    for (const x of g) genres.set(x, (genres.get(x) || 0) + 1);
  }
  add(c.mdTable(['Genre code', 'Books', 'Share of books', 'Read as'], [...genres].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1)).map(([g, n]) => [g, c.num(n), c.pct(n, ok.length), genreKind(g)])));
  add(`Books with no genre: ${noGenre}.`);
  add();

  add('## Russian-language fiction');
  add();
  const ru = ok.filter((b) => b.lang === 'ru');
  const fiction = ok.filter((b) => isFiction(c.jsonList(b.genres)));
  const ruFiction = ru.filter((b) => isFiction(c.jsonList(b.genres)));
  const ruFictionTranslated = ruFiction.filter((b) => b.is_translation === 1).length;
  add('A book is fiction when it has at least one fiction genre code (science fiction, fantasy, detective, prose, romance, adventure, humour, poetry, drama, children\'s stories) and no non-fiction code (science, biography, reference, religion, home, arts). Books with no genre at all are not counted as fiction. It is Russian-language when its `lang` field is `ru`.');
  add();
  add(c.mdTable(['Group', 'Books', 'Share of parsed books'], [
    ['All parsed books', c.num(ok.length), '100.0%'],
    ['Russian-language', c.num(ru.length), c.pct(ru.length, ok.length)],
    ['Fiction, any language', c.num(fiction.length), c.pct(fiction.length, ok.length)],
    ['**Russian-language fiction**', `**${c.num(ruFiction.length)}**`, `**${c.pct(ruFiction.length, ok.length)}**`],
    ['... written in Russian', c.num(ruFiction.length - ruFictionTranslated), c.pct(ruFiction.length - ruFictionTranslated, ok.length)],
    ['... translated into Russian', c.num(ruFictionTranslated), c.pct(ruFictionTranslated, ok.length)],
  ]));

  add('## Overlap with the archive');
  add();
  const dupArchive = ok.filter(inArchive);
  const dupRuFiction = ruFiction.filter(inArchive);
  add(`A feed book duplicates an archive book when it has the same Flibusta id, or the same normalised author + title.`);
  add();
  add(`- Feed books that duplicate an archive book: **${dupArchive.length} of ${ok.length} (${c.pct(dupArchive.length, ok.length)})**.`);
  add(`- Among Russian-language fiction: ${dupRuFiction.length} of ${ruFiction.length} (${c.pct(dupRuFiction.length, ruFiction.length)}).`);
  if (dupArchive.length) {
    add();
    add(c.mdTable(['Key', 'Title', 'Authors'], dupArchive.map((b) => [b.key, b.title, c.jsonList(b.authors).join(', ')])));
  }
  add();

  add('## Candidates to expect per update file');
  add();
  const candidates = ruFiction.length - dupRuFiction.length;
  add(`After filtering to Russian-language fiction and removing books already in the archive, **this update file gives ${candidates} candidate books** out of ${parsedRows.length} files (${c.pct(candidates, parsedRows.length)}).`);
  add();
  if (info.listing && info.listing.length > 1) {
    const spans = info.listing.map((l) => l.to - l.from + 1);
    const perId = candidates / m.span;
    const est = spans.map((s) => Math.round(s * perId));
    add(`The mirror listed ${info.listing.length} update files at the time of the probe. Their id ranges span ${Math.min(...spans)} to ${Math.max(...spans)} ids (this file: ${m.span}). If the other files have the same mix as this one, that means roughly **${Math.min(...est)} to ${Math.max(...est)} candidates per update file**, about ${Math.round(est.reduce((a, b) => a + b, 0) / est.length)} on average.`);
    add();
    add(c.mdTable(['Update file', 'Ids in range', 'Estimated candidates'], info.listing.map((l, i) => [l.name + (l.name === info.name ? ' (probed)' : ''), spans[i], l.name === info.name ? `${candidates} (measured)` : `about ${est[i]}`])));
  } else {
    add(`Only this one file was measured, so treat **about ${candidates} candidates per update file** as a first estimate.`);
    add();
  }
  add('This is a measurement of one file. The number will move from day to day; a few more files are needed before trusting it to better than a factor of two.');
  add();
  const sample = ruFiction.filter((b) => !inArchive(b)).slice(0, 15);
  if (sample.length) {
    add(`First ${sample.length} candidates, as a sanity check:`);
    add();
    add(c.mdTable(['Key', 'Author', 'Title', 'Genres', 'Words'], sample.map((b) => [b.key, c.jsonList(b.authors).join(', '), b.title, c.jsonList(b.genres).join(', '), c.num(b.words)])));
  }

  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim() + '\n';
}

// ---------------------------------------------------------------- script

async function main() {
  const cfg = c.loadConfig();
  const db = c.openDb(cfg);
  console.log('Feed probe: looking for the newest f.fb2.*.zip ...');
  const got = await getFeedZip(cfg);
  console.log(`  using ${got.name} (${got.how})`);

  const { entries, counts, wallMs } = await parseFeedZip(db, cfg, got.file, got.name);
  const feed = rebuildFeed(db, cfg);

  const ids = entries.map((e) => (path.posix.basename(e.name).match(/^(\d+)\.fb2$/i) || [])[1]).filter(Boolean).map(Number);
  const range = got.name.match(/(\d+)-(\d+)\.zip$/);
  const from = range ? Number(range[1]) : null;
  const to = range ? Number(range[2]) : null;
  const extCounts = {};
  for (const e of entries) extCounts[e.ext] = (extCounts[e.ext] || 0) + 1;
  c.setMeta(db, 'feed_probe', {
    name: got.name,
    how: got.how,
    mirror: got.mirror,
    errors: got.errors,
    listing: got.listing,
    bytes: fs.statSync(got.file).size,
    unpackedBytes: entries.reduce((a, e) => a + e.size, 0),
    entries: entries.map((e) => ({ name: e.name, ext: e.ext, size: e.size })),
    extCounts,
    wallMs,
    mapping: {
      from,
      to,
      span: from != null ? to - from + 1 : ids.length,
      matching: ids.length,
      inRange: from != null ? ids.filter((i) => i >= from && i <= to).length : ids.length,
      min: ids.length ? Math.min(...ids) : null,
      max: ids.length ? Math.max(...ids) : null,
      examples: entries.slice(0, 3).map((e) => e.name),
      odd: entries.filter((e) => !/^(\d+)\.fb2$/i.test(path.posix.basename(e.name))).map((e) => e.name).slice(0, 20),
    },
  });

  console.log(`  ${entries.length} files: ${counts.ok} ok, ${counts.ok_lenient} ok with lenient parser, ${counts.failed} failed; ${feed.kept} rows in "feed_books"`);
  fs.mkdirSync(cfg.reportsDir, { recursive: true });
  const file = path.join(cfg.reportsDir, 'feed-probe.md');
  fs.writeFileSync(file, buildReport(db), 'utf8');
  console.log(`Feed report written to ${path.relative(cfg.root, file)}`);
  db.close();
}

if (require.main === module) {
  main().catch((e) => {
    console.error(e.message || e);
    process.exit(1);
  });
}

module.exports = { main, isFiction };
