'use strict';
// Milestone 3c, parts A and B. The catalog index and my selection rules.
//
//   node src/build-index.js            (npm run index)
//   node src/build-index.js --rebuild  build the catalog part again from scratch
//
// 1. data/index.db, table "cat": one row per Russian-language, not deleted book
//    of the catalog, with authors, genre groups, series name, number in the
//    series, the number of books in that series, votes, average and the number
//    of reader comments. This part is slow (about a minute) and is only built
//    again when the catalog dumps change.
//    Votes and comments left on an older copy of a book are counted for the
//    copy that replaced it, the same way the book page on the site shows them.
// 2. The series rule, for every catalog book ("cat.status") and for every book
//    of mine in data/books.db (table "book_facts"):
//      standalone    not in a series
//      first         number 1 in its series
//      continuation  a later book of a series where I already have a liked
//                    book with a lower number
//      unknown       in a series, but the number is not known (eligible, flagged)
//      later         a later book of a series I have not started: NOT eligible
// 3. My old answers on books that are not eligible get status = 'rule' in the
//    table "labels", so no taste metric uses them.

const fs = require('node:fs');
const path = require('node:path');
const Database = require('better-sqlite3');
const c = require('./common');
const { isCollection } = require('./enrich');

const VERSION = 3; // raise this to force a rebuild of the catalog part
const CHARS_PER_WORD = 5.5; // catalog "Chars" divided by our word count, measured on the feed books
const ELIGIBLE = ['standalone', 'first', 'continuation', 'unknown'];

function indexPath(cfg) {
  return path.join(cfg.dataDir, 'index.db');
}

function openIndex(cfg, readonly) {
  const file = indexPath(cfg);
  if (readonly && !fs.existsSync(file)) return null;
  const ix = new Database(file, readonly ? { readonly: true } : {});
  if (!readonly) ix.pragma('journal_mode = WAL');
  return ix;
}

function idOf(key) {
  const n = Number(String(key).split('-')[0]);
  return Number.isInteger(n) ? n : null;
}

// How many books a series has. Normally the count of different numbers; if
// the numbers have gaps, the highest number (unless it is clearly not a
// position, like a year). Without any numbers: the count of different titles.
function seriesTotal(numbers, titles) {
  if (!numbers.size) return titles.size;
  const max = Math.max(...numbers);
  const total = max <= numbers.size * 2 + 2 ? Math.max(numbers.size, max) : numbers.size;
  return total;
}

// ---------------------------------------------------------------- the catalog part

function buildCatalogPart(ix, cfg, catalogPath) {
  const t0 = Date.now();
  const map = JSON.parse(fs.readFileSync(cfg.genreMapPath, 'utf8'));
  ix.exec(`
    DROP TABLE IF EXISTS cat; DROP TABLE IF EXISTS cat_author; DROP TABLE IF EXISTS alias; DROP TABLE IF EXISTS seq_total;
    DROP TABLE IF EXISTS rate_agg; DROP TABLE IF EXISTS review_agg;
    CREATE TABLE alias (id INTEGER PRIMARY KEY, root INTEGER NOT NULL);
    CREATE TABLE seq_total (seq_id INTEGER PRIMARY KEY, name TEXT, total INTEGER);
    CREATE TABLE cat (
      id INTEGER PRIMARY KEY, title TEXT, norm_title TEXT, search TEXT, year INTEGER, words INTEGER, file_type TEXT, uploaded TEXT,
      authors TEXT, author_ids TEXT, genres TEXT, groups TEXT, fiction INTEGER, collection INTEGER, translated INTEGER,
      seq_id INTEGER, series TEXT, series_no INTEGER, series_total INTEGER,
      votes INTEGER NOT NULL DEFAULT 0, avg REAL, comments INTEGER NOT NULL DEFAULT 0,
      in_library INTEGER NOT NULL DEFAULT 0, status TEXT);
    CREATE TABLE cat_author (author_id INTEGER NOT NULL, id INTEGER NOT NULL, PRIMARY KEY (author_id, id)) WITHOUT ROWID;
  `);
  ix.exec(`ATTACH DATABASE '${catalogPath.replace(/'/g, "''")}' AS c`);
  const has = (t) => !!ix.prepare("SELECT 1 FROM c.sqlite_master WHERE type = 'table' AND name = ?").get(t);

  // replaced copies: every old id points at the copy that is current today
  const good = new Map();
  if (has('libjoinedbooks')) for (const r of ix.prepare('SELECT BadId AS bad, GoodId AS good FROM c.libjoinedbooks ORDER BY Id').all()) good.set(r.bad, r.good);
  const putAlias = ix.prepare('INSERT OR REPLACE INTO alias (id, root) VALUES (?, ?)');
  ix.transaction(() => {
    for (const bad of good.keys()) {
      let root = bad;
      for (let hops = 0; good.has(root) && hops < 50; hops++) root = good.get(root);
      if (root !== bad) putAlias.run(bad, root);
    }
  })();
  ix.exec('CREATE INDEX alias_root ON alias (root)');

  // votes and comments, counted for the current copy
  ix.exec(`
    CREATE TABLE rate_agg AS SELECT root AS id, COUNT(*) AS n, AVG(r) AS avg FROM (
      SELECT COALESCE(a.root, l.BookId) AS root, l.UserId, MAX(CAST(l.Rate AS INTEGER)) AS r
      FROM c.librate l LEFT JOIN alias a ON a.id = l.BookId GROUP BY 1, 2) GROUP BY root;
    CREATE UNIQUE INDEX rate_agg_id ON rate_agg (id);
  `);
  if (has('reviews')) {
    ix.exec(`CREATE TABLE review_agg AS SELECT COALESCE(a.root, r.BookId) AS id, COUNT(*) AS n
      FROM c.reviews r LEFT JOIN alias a ON a.id = r.BookId WHERE LENGTH(TRIM(r.Text)) > 0 GROUP BY 1`);
  } else {
    ix.exec('CREATE TABLE review_agg (id INTEGER, n INTEGER)');
  }
  ix.exec('CREATE UNIQUE INDEX review_agg_id ON review_agg (id)');

  // authors (an author with several spellings counts as the main one)
  const authorName = new Map();
  const master = new Map();
  for (const r of ix.prepare('SELECT AvtorId AS id, FirstName AS f, MiddleName AS m, LastName AS l, NickName AS n, MasterId AS master FROM c.libavtorname').all()) {
    authorName.set(r.id, c.cleanSpaces([r.f, r.m, r.l].filter(Boolean).join(' ')) || c.cleanSpaces(r.n) || `author ${r.id}`);
    if (r.master > 0) master.set(r.id, r.master);
  }
  // the books of the index, loaded once; the other tables are then read without joins (much faster)
  const liveBooks = new Map();
  for (const b of ix.prepare(`SELECT BookId AS id, Title AS title, Year AS year, Chars AS chars, FileType AS type, Time AS time, SrcLang AS src
      FROM c.libbook WHERE Lang = 'ru' AND Deleted = '0'`).iterate()) liveBooks.set(b.id, b);
  const authorsOf = new Map();
  for (const r of ix.prepare('SELECT BookId AS id, AvtorId AS author FROM c.libavtor ORDER BY BookId, Pos').iterate()) {
    if (!liveBooks.has(r.id)) continue;
    const author = master.get(r.author) || r.author;
    if (!authorsOf.has(r.id)) authorsOf.set(r.id, []);
    if (!authorsOf.get(r.id).includes(author)) authorsOf.get(r.id).push(author);
  }
  const genreCode = new Map(ix.prepare('SELECT GenreId AS id, GenreCode AS code FROM c.libgenrelist').all().map((r) => [r.id, r.code]));
  const genresOf = new Map();
  for (const r of ix.prepare('SELECT BookId AS id, GenreId AS genre FROM c.libgenre ORDER BY BookId, Id').iterate()) {
    const code = genreCode.get(r.genre);
    if (!code || !liveBooks.has(r.id)) continue;
    if (!genresOf.has(r.id)) genresOf.set(r.id, []);
    if (!genresOf.get(r.id).includes(code)) genresOf.get(r.id).push(code);
  }
  const translated = new Set();
  if (has('libtranslator')) for (const r of ix.prepare('SELECT DISTINCT BookId AS id FROM c.libtranslator').iterate()) translated.add(r.id);

  // series: type 0 is a story series by the author; the first row (lowest level) is the book's own series
  const seriesOf = new Map();
  const seqNumbers = new Map();
  const seqTitles = new Map();
  for (const r of ix.prepare('SELECT BookId AS id, SeqId AS seq, SeqNumb AS no FROM c.libseq WHERE Type = 0 ORDER BY BookId, Level, SeqId').iterate()) {
    const book = liveBooks.get(r.id);
    if (!book) continue;
    r.title = book.title;
    const no = r.no > 0 ? r.no : null;
    if (!seriesOf.has(r.id)) seriesOf.set(r.id, { seq: r.seq, no });
    if (!seqNumbers.has(r.seq)) {
      seqNumbers.set(r.seq, new Set());
      seqTitles.set(r.seq, new Set());
    }
    if (no) seqNumbers.get(r.seq).add(no);
    else seqTitles.get(r.seq).add(c.normaliseText(r.title));
  }
  const seqName = new Map(ix.prepare('SELECT SeqId AS id, SeqName AS name FROM c.libseqname').all().map((r) => [r.id, c.cleanSpaces(r.name)]));
  const totals = new Map();
  const putSeq = ix.prepare('INSERT OR REPLACE INTO seq_total (seq_id, name, total) VALUES (?, ?, ?)');
  ix.transaction(() => {
    for (const [seq, numbers] of seqNumbers) {
      const total = seriesTotal(numbers, seqTitles.get(seq));
      totals.set(seq, total);
      putSeq.run(seq, seqName.get(seq) || '', total);
    }
  })();

  const put = ix.prepare(`INSERT INTO cat (id, title, norm_title, search, year, words, file_type, uploaded, authors, author_ids, genres, groups, fiction, collection, translated,
      seq_id, series, series_no, series_total) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`);
  const putAuthor = ix.prepare('INSERT OR IGNORE INTO cat_author (author_id, id) VALUES (?, ?)');
  let n = 0;
  ix.transaction(() => {
    for (const b of liveBooks.values()) {
      const authors = authorsOf.get(b.id) || [];
      const names = authors.map((a) => authorName.get(a) || `author ${a}`);
      const codes = genresOf.get(b.id) || [];
      const groups = [];
      for (const code of codes) {
        const g = map.codes[code];
        if (g && !groups.includes(g)) groups.push(g);
      }
      const kinds = groups.map((g) => map.groups[g]);
      const fiction = kinds.includes('fiction') && !kinds.includes('nonfiction') ? 1 : 0;
      const words = b.chars > 0 ? Math.round(b.chars / CHARS_PER_WORD) : null;
      const s = seriesOf.get(b.id);
      const title = c.cleanSpaces(b.title);
      const norm = c.normaliseText(title);
      const src = c.normLang(b.src);
      put.run(b.id, title, norm, c.normaliseText(names.join(' ')) + ' | ' + norm, b.year > 0 ? b.year : null, words, b.type, b.time,
        names.join(', '), JSON.stringify(authors), JSON.stringify(codes), JSON.stringify(groups), fiction, isCollection(title, words) ? 1 : 0,
        translated.has(b.id) || (src && src !== 'ru') ? 1 : 0,
        s ? s.seq : null, s ? seqName.get(s.seq) || '' : null, s ? s.no : null, s ? totals.get(s.seq) || null : null);
      for (const a of authors) putAuthor.run(a, b.id);
      n++;
    }
  })();
  ix.exec(`
    UPDATE cat SET votes = r.n, avg = r.avg FROM rate_agg r WHERE r.id = cat.id;
    UPDATE cat SET comments = r.n FROM review_agg r WHERE r.id = cat.id;
    CREATE INDEX cat_seq ON cat (seq_id);
    CREATE INDEX cat_author_id ON cat_author (id);
  `);
  ix.exec('DETACH DATABASE c');
  console.log(`  catalog index built: ${c.num(n)} Russian-language books, ${c.num(totals.size)} series, in ${((Date.now() - t0) / 1000).toFixed(0)} s`);
}

// ---------------------------------------------------------------- facts for one book of mine

// Votes and comments are looked up for the book id, its current copy and every older copy.
function makeFacts(ix, cat) {
  const q = {
    root: ix.prepare('SELECT root FROM alias WHERE id = ?'),
    row: ix.prepare('SELECT * FROM cat WHERE id = ?'),
    seq: cat.prepare('SELECT s.SeqId AS seq, s.SeqNumb AS no, n.SeqName AS name FROM libseq s LEFT JOIN libseqname n ON n.SeqId = s.SeqId WHERE s.BookId = ? AND s.Type = 0 ORDER BY s.Level, s.SeqId LIMIT 1'),
    total: ix.prepare('SELECT total FROM seq_total WHERE seq_id = ?'),
    rate: ix.prepare('SELECT n, avg FROM rate_agg WHERE id = ?'),
    reviews: ix.prepare('SELECT n FROM review_agg WHERE id = ?'),
    authors: cat.prepare('SELECT CASE WHEN n.MasterId > 0 THEN n.MasterId ELSE a.AvtorId END AS id FROM libavtor a LEFT JOIN libavtorname n ON n.AvtorId = a.AvtorId WHERE a.BookId = ? ORDER BY a.Pos'),
  };
  return (id) => {
    if (id == null) return null;
    const a = q.root.get(id);
    const root = a ? a.root : id;
    const row = q.row.get(root);
    const rate = q.rate.get(root);
    const rev = q.reviews.get(root);
    const out = { cat_id: root, in_catalog: row ? 1 : 0, seq_id: null, series: null, series_no: null, series_total: null,
      votes: rate ? rate.n : 0, avg: rate ? rate.avg : null, comments: rev ? rev.n : 0, author_ids: row ? row.author_ids : null };
    if (row) {
      Object.assign(out, { seq_id: row.seq_id, series: row.series, series_no: row.series_no, series_total: row.series_total });
    } else {
      // the book is not in the index (deleted, or not Russian): ask the catalog tables directly
      const s = q.seq.get(root) || q.seq.get(id);
      if (s) {
        const t = q.total.get(s.seq);
        Object.assign(out, { seq_id: s.seq, series: c.cleanSpaces(s.name), series_no: s.no > 0 ? s.no : null, series_total: t ? t.total : null });
      }
      const ids = [...new Set(q.authors.all(root).concat(q.authors.all(id)).map((r) => r.id).filter((x) => x != null))];
      out.author_ids = ids.length ? JSON.stringify(ids) : null;
    }
    return out;
  };
}

function statusOf(f, likedMin, ownNo) {
  if (!f || f.seq_id == null) return 'standalone';
  if (f.series_no == null) return 'unknown';
  if (f.series_no === 1) return 'first';
  const min = likedMin.get(f.seq_id);
  if (min != null && min < f.series_no) return 'continuation';
  return 'later';
}

// ---------------------------------------------------------------- the rules part

function ensureTables(db) {
  db.exec(`
    -- 3c: series, eligibility, votes and comments for every book of mine. kind = liked / pool / extra
    CREATE TABLE IF NOT EXISTS book_facts (
      key TEXT PRIMARY KEY, kind TEXT NOT NULL, cat_id INTEGER, in_catalog INTEGER, seq_id INTEGER, series TEXT, series_no INTEGER,
      series_total INTEGER, status TEXT NOT NULL, votes INTEGER, avg REAL, comments INTEGER, author_ids TEXT);
    -- 3c: authors I almost always read (tab "My authors")
    CREATE TABLE IF NOT EXISTS followed_authors (author_id INTEGER PRIMARY KEY, name TEXT, time TEXT NOT NULL);
    -- 3c: answers from the tabs "Books by my authors" and "Add a book".
    --     mark = disliked (read it, did not like it) / liked (read it, liked it) / unread / forgot
    CREATE TABLE IF NOT EXISTS read_marks (id INTEGER PRIMARY KEY, mark TEXT NOT NULL, tab TEXT, authors TEXT, title TEXT, time TEXT NOT NULL);
    -- 3c: books marked "read it", downloaded and parsed like the other sets
    CREATE TABLE IF NOT EXISTS extra_books (${c.BOOK_COLUMNS});
    -- 3c: the queue of the tab "New books", written by model/funnel.py. grp = top / random
    CREATE TABLE IF NOT EXISTS label_queue (key TEXT PRIMARY KEY, grp TEXT NOT NULL, pos INTEGER NOT NULL, score REAL, model TEXT);
  `);
  const cols = new Set(db.prepare('PRAGMA table_info(labels)').all().map((r) => r.name));
  // status: 'rule' = the book is not eligible under my series rule. grp, score: which half of the queue the book came from.
  for (const [name, type] of [['status', 'TEXT'], ['grp', 'TEXT'], ['score', 'REAL'], ['followed', 'INTEGER']]) {
    if (!cols.has(name)) db.exec(`ALTER TABLE labels ADD COLUMN ${name} ${type}`);
  }
}

function applyRules(db, ix, cfg) {
  const cat = new Database(cfg.catalogPath, { readonly: true });
  const facts = makeFacts(ix, cat);
  try {
    const hasView = !!db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = 'unlabeled_books'").get();
    const liked = db.prepare("SELECT key, title, authors FROM books WHERE parse_status != 'failed'").all().map((b) => ({ ...b, kind: 'liked', f: facts(idOf(b.key)) }));
    const pool = hasView ? db.prepare('SELECT key FROM unlabeled_books').all().map((b) => ({ ...b, kind: 'pool', f: facts(idOf(b.key)) })) : [];
    const marks = db.prepare('SELECT id, mark FROM read_marks').all();
    const extraKeys = new Set(db.prepare("SELECT key FROM extra_books WHERE parse_status != 'failed'").all().map((r) => r.key));
    const likedKeys = new Set(liked.map((b) => b.key));
    const poolKeys = new Set(pool.map((b) => b.key));
    const extra = marks.filter((m) => extraKeys.has(String(m.id)) && !likedKeys.has(String(m.id)) && !poolKeys.has(String(m.id)))
      .map((m) => ({ key: String(m.id), kind: 'extra', f: facts(m.id) }));

    // the lowest number I liked in each series: my liked books, plus books I marked "read it, liked it"
    const likedMin = new Map();
    const note = (f) => {
      if (f && f.seq_id != null && f.series_no != null && !(likedMin.get(f.seq_id) <= f.series_no)) likedMin.set(f.seq_id, f.series_no);
    };
    for (const b of liked) note(b.f);
    for (const m of marks) if (m.mark === 'liked') note(facts(m.id));

    const put = db.prepare(`INSERT INTO book_facts (key, kind, cat_id, in_catalog, seq_id, series, series_no, series_total, status, votes, avg, comments, author_ids)
      VALUES (@key, @kind, @cat_id, @in_catalog, @seq_id, @series, @series_no, @series_total, @status, @votes, @avg, @comments, @author_ids)`);
    db.transaction(() => {
      db.prepare('DELETE FROM book_facts').run();
      for (const b of [...liked, ...pool, ...extra]) {
        const f = b.f || { cat_id: null, in_catalog: 0, seq_id: null, series: null, series_no: null, series_total: null, votes: 0, avg: null, comments: 0, author_ids: null };
        put.run({ key: b.key, kind: b.kind, ...f, status: statusOf(b.f, likedMin) });
      }
      // old answers on books that the series rule would never have shown me
      db.prepare("UPDATE labels SET status = NULL WHERE status = 'rule'").run();
      db.prepare("UPDATE labels SET status = 'rule' WHERE key IN (SELECT key FROM book_facts WHERE status = 'later')").run();
    })();

    // the same rule for the whole catalog, and "is this book already in my library?"
    const likedRoots = new Set(liked.map((b) => b.f && b.f.cat_id).filter((x) => x != null));
    const likedTitles = new Map(); // author id -> set of normalised titles
    for (const b of liked) {
      const title = c.normaliseText(b.title);
      for (const a of c.jsonList(b.f && b.f.author_ids)) {
        if (!likedTitles.has(a)) likedTitles.set(a, new Set());
        likedTitles.get(a).add(title);
      }
    }
    ix.exec('CREATE TEMP TABLE IF NOT EXISTS liked_seq (seq_id INTEGER PRIMARY KEY, min_no INTEGER); DELETE FROM liked_seq');
    const putSeq = ix.prepare('INSERT INTO liked_seq (seq_id, min_no) VALUES (?, ?)');
    const setLib = ix.prepare('UPDATE cat SET in_library = 1 WHERE id = ?');
    const byAuthor = ix.prepare('SELECT k.id, k.norm_title FROM cat_author a JOIN cat k ON k.id = a.id WHERE a.author_id = ?');
    ix.transaction(() => {
      for (const [seq, no] of likedMin) putSeq.run(seq, no);
      ix.exec(`UPDATE cat SET in_library = 0, status = CASE
          WHEN seq_id IS NULL THEN 'standalone'
          WHEN series_no IS NULL THEN 'unknown'
          WHEN series_no = 1 THEN 'first'
          WHEN series_no > (SELECT min_no FROM liked_seq l WHERE l.seq_id = cat.seq_id) THEN 'continuation'
          ELSE 'later' END`);
      for (const id of likedRoots) setLib.run(id);
      for (const [author, titles] of likedTitles) for (const r of byAuthor.all(author)) if (titles.has(r.norm_title)) setLib.run(r.id);
    })();

    const count = (kind) => {
      const by = {};
      for (const r of db.prepare('SELECT status, COUNT(*) AS n FROM book_facts WHERE kind = ? GROUP BY status').all(kind)) by[r.status] = r.n;
      return by;
    };
    const ruled = db.prepare("SELECT COUNT(*) AS n FROM labels WHERE status = 'rule'").get().n;
    c.setMeta(db, 'has_reviews', !!ix.prepare('SELECT 1 FROM review_agg LIMIT 1').get());
    return { liked: count('liked'), pool: count('pool'), extra: extra.length, ruled };
  } finally {
    cat.close();
  }
}

function build(cfg, db, { rebuild = false, quiet = false } = {}) {
  if (!fs.existsSync(cfg.catalogPath)) throw new Error('data/catalog.db not found. Run "npm run catalog" first.');
  ensureTables(db);
  const cat = new Database(cfg.catalogPath, { readonly: true });
  const loaded = cat.prepare('SELECT name, bytes, mtime, rows FROM _loaded ORDER BY name').all();
  cat.close();
  const signature = JSON.stringify({ VERSION, loaded, map: fs.existsSync(cfg.genreMapPath) ? fs.statSync(cfg.genreMapPath).size : 0 });
  const ix = openIndex(cfg, false);
  try {
    ix.exec('CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT)');
    const old = ix.prepare("SELECT value FROM meta WHERE name = 'signature'").get();
    if (rebuild || !old || old.value !== signature) {
      if (!quiet) console.log('Index: building the catalog index (about a minute) ...');
      ix.prepare("DELETE FROM meta WHERE name = 'signature'").run();
      buildCatalogPart(ix, cfg, cfg.catalogPath);
      ix.prepare("INSERT OR REPLACE INTO meta (name, value) VALUES ('signature', ?)").run(signature);
    }
    const r = applyRules(db, ix, cfg);
    if (!quiet) {
      const show = (by) => ['standalone', 'first', 'continuation', 'unknown', 'later'].map((s) => `${by[s] || 0} ${s}`).join(', ');
      console.log(`Index: liked books: ${show(r.liked)}`);
      console.log(`       other books: ${show(r.pool)}`);
      console.log(`       ${r.ruled} old answers are on books my series rule would not show; they are marked "rule".`);
    }
    return r;
  } finally {
    ix.close();
  }
}

function main() {
  const cfg = c.loadConfig();
  const db = c.openDb(cfg);
  db.pragma('busy_timeout = 10000');
  try {
    build(cfg, db, { rebuild: process.argv.includes('--rebuild') });
  } finally {
    db.close();
  }
}

if (require.main === module) {
  try {
    main();
  } catch (e) {
    console.error(e.message || e);
    process.exit(1);
  }
}

module.exports = { build, ensureTables, openIndex, indexPath, idOf, ELIGIBLE, CHARS_PER_WORD };
