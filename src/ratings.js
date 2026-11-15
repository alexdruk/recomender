'use strict';
// Reader ratings:  npm run ratings      (node src/ratings.js --force downloads again now)
//
//   1. refresh(): at most once a day, downloads the ratings dump of the library
//      (lib.librate.sql.gz) and the two small dumps that say which book belongs
//      to which author and series (lib.libavtor, lib.libseq), through the polite
//      queue, and loads them into data/catalog.db. The files are kept in data/sql/.
//   2. buildFacts(): for every book of mine (library, background, feed) writes
//      the table "rating_facts" in data/books.db:
//        votes, sum              the book's own marks (1 to 5)
//        a_votes, a_sum, a_books the marks of all EARLIER books of the same author(s)
//                                (a lower book id, a different title), and how many
//                                of those books have marks
//        s_votes, s_sum, s_books the same for the earlier books of the same series
//      The book itself is never part of its author or series record.
//
// A book without votes is not a bad book: the model and the page turn these
// sums into a "shrunk" rating that sits at the catalog average when there are
// no votes (see PRIOR_VOTES).

const fs = require('node:fs');
const path = require('node:path');
const c = require('./common');
const net = require('./net');
const { loadDump, openCatalog } = require('./fetch-catalog');

const DUMPS = ['librate', 'libavtor', 'libseq'];
const PRIOR_VOTES = 5; // shrunk rating = (sum of marks + average x 5) / (votes + 5)
const FALLBACK_AVERAGE = 3.4;
const HOUR = 3600000;

const hasTable = (db, name) => !!db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?").get(name);

// ---------------------------------------------------------------- download, at most once a day

// Helper tables in catalog.db: votes per book, names for matching new books.
function buildHelpers(cat, cfg) {
  cat.exec(`
    DROP TABLE IF EXISTS rate_sum;
    CREATE TABLE rate_sum AS SELECT BookId, COUNT(*) AS n, SUM(CAST(Rate AS INTEGER)) AS s
      FROM librate WHERE CAST(Rate AS INTEGER) BETWEEN 1 AND 5 GROUP BY BookId;
    CREATE UNIQUE INDEX rate_sum_id ON rate_sum (BookId);
    CREATE INDEX IF NOT EXISTS libseq_SeqId ON libseq (SeqId);
    CREATE INDEX IF NOT EXISTS libavtorname_MasterId ON libavtorname (MasterId);
  `);
  // names in the same shape as the names inside book files: first and last word, lower case
  const filled = (t) => hasTable(cat, t) && cat.prepare(`SELECT 1 FROM ${t} LIMIT 1`).get();
  if (!filled('avtor_norm')) {
    cat.exec('DROP TABLE IF EXISTS avtor_norm; CREATE TABLE avtor_norm (norm TEXT, AvtorId INTEGER)');
    const put = cat.prepare('INSERT INTO avtor_norm VALUES (?, ?)');
    cat.transaction(() => {
      for (const r of cat.prepare('SELECT AvtorId, FirstName, MiddleName, LastName FROM libavtorname').all()) {
        const norm = c.normaliseAuthor([r.FirstName, r.MiddleName, r.LastName].filter(Boolean).join(' '));
        if (norm) put.run(norm, r.AvtorId);
      }
    })();
    cat.exec('CREATE INDEX IF NOT EXISTS avtor_norm_norm ON avtor_norm (norm)');
  }
  if (!filled('seq_norm')) {
    cat.exec('DROP TABLE IF EXISTS seq_norm; CREATE TABLE seq_norm (norm TEXT, SeqId INTEGER)');
    const put = cat.prepare('INSERT INTO seq_norm VALUES (?, ?)');
    cat.transaction(() => {
      for (const r of cat.prepare('SELECT SeqId, SeqName FROM libseqname').all()) {
        const norm = c.normaliseText(r.SeqName);
        if (norm) put.run(norm, r.SeqId);
      }
    })();
    cat.exec('CREATE INDEX IF NOT EXISTS seq_norm_norm ON seq_norm (norm)');
  }
  // the average mark of fiction books in the whole catalog: the neutral value for a book without votes
  const map = JSON.parse(fs.readFileSync(cfg.genreMapPath, 'utf8'));
  const ids = { fiction: [], nonfiction: [] };
  for (const g of cat.prepare('SELECT GenreId, GenreCode FROM libgenrelist').all()) {
    const kind = map.groups[map.codes[g.GenreCode]];
    if (ids[kind]) ids[kind].push(g.GenreId);
  }
  const avg = cat.prepare(`SELECT SUM(r.s) AS s, SUM(r.n) AS n, COUNT(*) AS books FROM rate_sum r
    WHERE EXISTS (SELECT 1 FROM libgenre g WHERE g.BookId = r.BookId AND g.GenreId IN (${ids.fiction.join(',') || 0}))
      AND NOT EXISTS (SELECT 1 FROM libgenre g WHERE g.BookId = r.BookId AND g.GenreId IN (${ids.nonfiction.join(',') || 0}))`).get();
  const all = cat.prepare('SELECT SUM(s) AS s, SUM(n) AS n, COUNT(*) AS books FROM rate_sum').get();
  return {
    average: avg.n ? avg.s / avg.n : FALLBACK_AVERAGE,
    fictionVotes: avg.n || 0,
    fictionBooks: avg.books || 0,
    allVotes: all.n || 0,
    allBooks: all.books || 0,
  };
}

// Downloads and loads the dumps when the last good refresh is older than a day.
// Returns { refreshed, why }.
async function refresh(db, cfg, { force = false, log = console.log } = {}) {
  const last = c.getMeta(db, 'ratings_info');
  const cat0 = openCatalog(cfg);
  const ready = ['rate_sum', 'avtor_norm', 'seq_norm'].every((t) => hasTable(cat0, t) && cat0.prepare(`SELECT 1 FROM ${t} LIMIT 1`).get());
  cat0.close();
  if (!force && ready && last && last.refreshed_at && Date.now() - Date.parse(last.refreshed_at) < cfg.ratingsEveryHours * HOUR) {
    return { refreshed: false, why: `the ratings were downloaded ${last.refreshed_at}, less than ${cfg.ratingsEveryHours} hours ago` };
  }
  net.setPause(cfg.pauseSeconds);
  fs.mkdirSync(cfg.sqlDir, { recursive: true });
  let downloadError = null;
  const got = [];
  // a first run without any download still builds the helper tables from the catalog that is there
  const mayDownload = force || !last || !last.tried_at || Date.now() - Date.parse(last.tried_at) >= Math.min(cfg.ratingsEveryHours, 6) * HOUR - 60000;
  if (mayDownload) {
    try {
      for (const t of DUMPS) {
        const name = `lib.${t}.sql.gz`;
        const file = path.join(cfg.sqlDir, name);
        // a file that is already here and less than a day old is used as it is
        if (!force && fs.existsSync(file) && Date.now() - fs.statSync(file).mtimeMs < cfg.ratingsEveryHours * HOUR) {
          log(`  ${name}: the copy in data/sql/ is less than ${cfg.ratingsEveryHours} hours old, not downloaded again`);
        } else {
          const r = await net.onMirrors(cfg.mirrors, (m) => net.download(`${m}/sql/${name}`, file, { magic: Buffer.from([0x1f, 0x8b]), timeoutMs: 600000 }), log);
          log(`  downloaded ${name} (${(r.value / 1048576).toFixed(1)} MB) from ${r.mirror}`);
        }
        got.push(t);
      }
    } catch (e) {
      downloadError = String(e.message || e).split('\n').join(' | ');
      log('Ratings: the download failed. ' + downloadError);
    }
  }
  const cat = openCatalog(cfg);
  let info;
  try {
    const mark = cat.prepare("INSERT OR REPLACE INTO _loaded VALUES (?, ?, ?, ?, ?, datetime('now'))");
    for (const t of got) {
      const file = path.join(cfg.sqlDir, `lib.${t}.sql.gz`);
      const st = fs.statSync(file);
      const old = cat.prepare('SELECT bytes, mtime FROM _loaded WHERE name = ?').get(t);
      if (old && old.bytes === st.size && old.mtime === Math.round(st.mtimeMs)) continue; // this very file is loaded already
      const rows = await loadDump(cat, t, file);
      mark.run(t, path.basename(file), st.size, Math.round(st.mtimeMs), rows);
      log(`  ${t}: loaded ${c.num(rows)} rows`);
    }
    if (got.includes('librate') || !ready) info = buildHelpers(cat, cfg);
  } finally {
    cat.close();
  }
  const now = new Date().toISOString();
  const complete = got.length === DUMPS.length;
  const out = Object.assign({}, last || {}, info || {}, {
    prior_votes: PRIOR_VOTES,
    tried_at: mayDownload ? now : (last || {}).tried_at || null,
    refreshed_at: complete ? new Date(Math.min(...DUMPS.map((t) => fs.statSync(path.join(cfg.sqlDir, `lib.${t}.sql.gz`)).mtimeMs))).toISOString() : (last || {}).refreshed_at || null,
    error: downloadError,
  });
  if (out.average == null) out.average = FALLBACK_AVERAGE;
  c.setMeta(db, 'ratings_info', out);
  return { refreshed: complete, why: complete ? 'downloaded' : downloadError || 'the lookup tables were rebuilt from the dump that is already here' };
}

// ---------------------------------------------------------------- facts for my books

function ensureFactsTable(db) {
  db.exec(`CREATE TABLE IF NOT EXISTS rating_facts (
    key TEXT PRIMARY KEY, votes INTEGER, sum INTEGER,
    a_known INTEGER, a_books INTEGER, a_votes INTEGER, a_sum INTEGER,
    s_known INTEGER, s_books INTEGER, s_votes INTEGER, s_sum INTEGER,
    how TEXT, updated_at TEXT)`);
}

const BOOK_TABLES = ['books', 'feed_books', 'matched_books', 'rated_books'];

function buildFacts(db, cfg) {
  ensureFactsTable(db);
  if (!fs.existsSync(cfg.catalogPath)) return { books: 0, withVotes: 0, authorRecord: 0, seriesRecord: 0, byName: 0 };
  const cat = openCatalog(cfg);
  if (!hasTable(cat, 'rate_sum')) {
    cat.close();
    return { books: 0, withVotes: 0, authorRecord: 0, seriesRecord: 0, byName: 0 };
  }
  const q = {
    own: cat.prepare('SELECT n, s FROM rate_sum WHERE BookId = ?'),
    authorsOf: cat.prepare('SELECT AvtorId FROM libavtor WHERE BookId = ?'),
    byName: cat.prepare('SELECT AvtorId FROM avtor_norm WHERE norm = ?'),
    master: cat.prepare('SELECT MasterId FROM libavtorname WHERE AvtorId = ?'),
    aliases: cat.prepare('SELECT AvtorId FROM libavtorname WHERE MasterId = ?'),
    booksOf: cat.prepare('SELECT a.BookId AS id, r.n, r.s FROM libavtor a LEFT JOIN rate_sum r ON r.BookId = a.BookId WHERE a.AvtorId = ?'),
    nBooks: cat.prepare('SELECT COUNT(*) AS n FROM libavtor WHERE AvtorId = ?'),
    title: cat.prepare('SELECT Title FROM libbook WHERE BookId = ?'),
    seqOf: cat.prepare('SELECT SeqId, SeqNumb FROM libseq WHERE BookId = ? AND Type = 0 ORDER BY Level'),
    seqByName: cat.prepare('SELECT SeqId FROM seq_norm WHERE norm = ?'),
    seqBooks: cat.prepare('SELECT q.BookId AS id, q.SeqNumb AS no, r.n, r.s FROM libseq q LEFT JOIN rate_sum r ON r.BookId = q.BookId WHERE q.SeqId = ?'),
    seqHasAuthor: cat.prepare('SELECT 1 FROM libseq q JOIN libavtor a ON a.BookId = q.BookId WHERE q.SeqId = ? AND a.AvtorId = ? LIMIT 1'),
  };
  const titles = new Map();
  const titleOf = (id) => {
    if (!titles.has(id)) titles.set(id, c.normaliseText((q.title.get(id) || {}).Title || '') || '#' + id);
    return titles.get(id);
  };
  // an author and all the other names the catalog keeps for the same person
  const families = new Map();
  const family = (id) => {
    if (!families.has(id)) {
      const m = q.master.get(id);
      const head = m && m.MasterId > 0 ? m.MasterId : id;
      families.set(id, [...new Set([head, id, ...q.aliases.all(head).map((r) => r.AvtorId)])]);
    }
    return families.get(id);
  };
  const authorBooks = new Map();
  const booksOf = (id) => {
    if (!authorBooks.has(id)) authorBooks.set(id, q.booksOf.all(id));
    return authorBooks.get(id);
  };
  const seqBooks = new Map();
  const booksOfSeq = (id) => {
    if (!seqBooks.has(id)) seqBooks.set(id, q.seqBooks.all(id));
    return seqBooks.get(id);
  };
  // adds up the marks of a list of books; one title counts as one book
  const record = (list) => {
    const seen = new Set();
    const rated = new Set();
    let votes = 0;
    let sum = 0;
    for (const b of list) {
      if (seen.has(b.id)) continue;
      seen.add(b.id);
      if (b.n) {
        votes += b.n;
        sum += b.s;
        rated.add(titleOf(b.id));
      }
    }
    return { books: rated.size, votes, sum };
  };

  const put = db.prepare(`INSERT INTO rating_facts (key, votes, sum, a_known, a_books, a_votes, a_sum, s_known, s_books, s_votes, s_sum, how, updated_at)
    VALUES (@key, @votes, @sum, @a_known, @a_books, @a_votes, @a_sum, @s_known, @s_books, @s_votes, @s_sum, @how, @now)
    ON CONFLICT (key) DO UPDATE SET votes = excluded.votes, sum = excluded.sum, a_known = excluded.a_known, a_books = excluded.a_books,
      a_votes = excluded.a_votes, a_sum = excluded.a_sum, s_known = excluded.s_known, s_books = excluded.s_books, s_votes = excluded.s_votes,
      s_sum = excluded.s_sum, how = excluded.how, updated_at = excluded.updated_at`);
  const now = new Date().toISOString();
  const stats = { books: 0, withVotes: 0, authorRecord: 0, seriesRecord: 0, byName: 0 };
  const done = new Set();
  db.transaction(() => {
    for (const table of BOOK_TABLES) {
      if (!hasTable(db, table)) continue;
      for (const b of db.prepare(`SELECT key, authors, title, series, series_no FROM ${table}`).all()) {
        if (done.has(b.key)) continue;
        done.add(b.key);
        const id = Number(String(b.key).split('-')[0]);
        const hasId = Number.isInteger(id) && id > 0;
        const myTitle = c.normaliseText(b.title);
        const earlier = (x) => (!hasId || x.id < id) && x.id !== id && titleOf(x.id) !== myTitle;
        const own = hasId ? q.own.get(id) : null;

        // the authors: by the book id when the catalog knows the book, otherwise by name
        let authorIds = hasId ? q.authorsOf.all(id).map((r) => r.AvtorId) : [];
        let how = authorIds.length ? 'id' : 'none';
        if (!authorIds.length) {
          for (const name of c.jsonList(b.authors)) {
            const found = q.byName.all(c.normaliseAuthor(name)).map((r) => r.AvtorId);
            if (!found.length) continue;
            // several people with one name: the one with the most books
            const heads = [...new Set(found.map((f) => family(f)[0]))];
            heads.sort((x, y) => q.nBooks.get(y).n - q.nBooks.get(x).n);
            authorIds.push(heads[0]);
            how = 'name';
          }
        }
        const people = [...new Set(authorIds.flatMap((a) => family(a)))];
        const a = record(people.flatMap((p) => booksOf(p)).filter(earlier));

        // the series: by the book id, otherwise by the series name among the series of the same authors
        let seq = hasId ? q.seqOf.get(id) : null;
        if (!seq && b.series && people.length) {
          const cand = q.seqByName.all(c.normaliseText(b.series)).map((r) => r.SeqId).find((sid) => people.some((p) => q.seqHasAuthor.get(sid, p)));
          if (cand) seq = { SeqId: cand, SeqNumb: Number(b.series_no) || 0 };
        }
        let s = { books: 0, votes: 0, sum: 0 };
        if (seq) {
          const myNo = Number(seq.SeqNumb) || 0;
          s = record(booksOfSeq(seq.SeqId).filter((x) => x.id !== id && titleOf(x.id) !== myTitle && (myNo > 0 && x.no > 0 ? x.no < myNo : !hasId || x.id < id)));
        }
        put.run({
          key: b.key, votes: own ? own.n : 0, sum: own ? own.s : 0,
          a_known: people.length ? 1 : 0, a_books: a.books, a_votes: a.votes, a_sum: a.sum,
          s_known: seq ? 1 : 0, s_books: s.books, s_votes: s.votes, s_sum: s.sum, how, now,
        });
        stats.books++;
        if (own && own.n) stats.withVotes++;
        if (a.votes) stats.authorRecord++;
        if (s.votes) stats.seriesRecord++;
        if (how === 'name') stats.byName++;
      }
    }
  })();
  cat.close();
  return stats;
}

// Refresh (when a day has passed) and rebuild the facts. Used by the daily loop.
async function update(db, cfg, opts = {}) {
  const log = opts.log || console.log;
  let r = { refreshed: false, why: '' };
  try {
    r = await refresh(db, cfg, opts);
  } catch (e) {
    r.why = String(e.message || e);
    log('Ratings: the refresh failed. ' + r.why);
  }
  const stats = buildFacts(db, cfg);
  const info = c.getMeta(db, 'ratings_info') || {};
  log(`Ratings: ${r.refreshed ? 'fresh dump downloaded' : 'no download (' + r.why + ')'}. ${stats.books} books looked up: ${stats.withVotes} have votes, ` +
    `${stats.authorRecord} have an author record (${stats.byName} authors found by name), ${stats.seriesRecord} a series record. ` +
    `Catalog average for fiction: ${Number(info.average || FALLBACK_AVERAGE).toFixed(2)}.`);
  return { refreshed: r.refreshed, stats };
}

async function main() {
  const cfg = c.loadConfig();
  const db = c.openDb(cfg);
  db.pragma('busy_timeout = 60000');
  await update(db, cfg, { force: process.argv.includes('--force') });
  db.close();
}

if (require.main === module) {
  main().catch((e) => {
    console.error(e.message || e);
    process.exit(1);
  });
}

module.exports = { main, update, refresh, buildFacts, ensureFactsTable, PRIOR_VOTES, FALLBACK_AVERAGE };
