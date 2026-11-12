'use strict';
// Milestone 2, steps 3, 6 and 7. Adds what the book files do not carry.
// Works on both final tables, "books" (liked) and "feed_books" (unlabeled):
//
//   from the catalog (data/catalog.db), looked up by the Flibusta book id:
//     cat_genres, cat_year, cat_src_lang, cat_series, cat_translated, cat_author_ids
//   for liked books, empty genres / year / src_lang / series are also filled in
//   is_collection  - more than 400,000 words, or the title says it is a bundle
//   genre_groups   - the plain groups from data/genre-map.json
//   is_fiction     - at least one fiction group and no non-fiction group
//   in_archive     - (feed only) the same book is already among the liked books
//
// The catalog is the same source for liked and feed books, so the two can be
// compared fairly. A book the catalog does not know keeps the values from its file.
// Everything is computed again on each run, so running twice changes nothing.

const fs = require('node:fs');
const Database = require('better-sqlite3');
const c = require('./common');
const genreMap = require('./build-genre-map');

const EXTRA = {
  is_collection: 'INTEGER',
  cat_genres: 'TEXT',
  cat_year: 'INTEGER',
  cat_src_lang: 'TEXT',
  cat_series: 'TEXT',
  cat_translated: 'INTEGER',
  cat_author_ids: 'TEXT',
  genre_groups: 'TEXT',
  is_fiction: 'INTEGER',
  in_archive: 'INTEGER',
};

// Which rows of "parsed" each final table is built from, and its "source" value.
const PARSED_SOURCE = { books: 'archive', feed_books: 'feed', matched_books: 'matched' };

function addColumns(db, table) {
  const have = new Set(db.prepare(`PRAGMA table_info(${table})`).all().map((r) => r.name));
  for (const [name, type] of Object.entries(EXTRA)) if (!have.has(name)) db.exec(`ALTER TABLE ${table} ADD COLUMN ${name} ${type}`);
  if (table !== 'books' && !have.has('source')) db.exec(`ALTER TABLE ${table} ADD COLUMN source TEXT`);
}

// The view "unlabeled_books": every book that may be shown or scored as
// unlabeled - feed books and matched background books that are Russian-language
// fiction, parsed, not already liked and not a collection.
function createUnlabeledView(db) {
  const cols = ['key', 'source_file', 'format', 'authors', 'title', 'genres', 'lang', 'src_lang', 'translators', 'series', 'series_no',
    'year', 'annotation', 'chars', 'words', 'is_translation', 'label', 'parse_status', ...Object.keys(EXTRA), 'source'].join(', ');
  const where = "parse_status != 'failed' AND lang = 'ru' AND is_fiction = 1 AND COALESCE(in_archive, 0) = 0 AND COALESCE(is_collection, 0) = 0";
  db.exec(`DROP VIEW IF EXISTS unlabeled_books;
    CREATE VIEW unlabeled_books AS
      SELECT ${cols} FROM feed_books WHERE ${where}
      UNION ALL
      SELECT ${cols} FROM matched_books WHERE ${where} AND key NOT IN (SELECT key FROM feed_books)`);
}

// ---------------------------------------------------------------- collections

const BUNDLE_WORD = /книги|книг|компиляция|цикл|сборник|тома|томах|романы|трилогия|тетралогия|дилогия/i;
const NUMBER_RANGE = /\d+\s*[-–—]\s*\d+/;
const BOOK_COUNT = /\d+\s*(книг|книги|книга|томов|тома|романов|романа)/i;

function isCollection(title, words) {
  if (words != null && words > 400000) return true;
  const t = String(title || '');
  if (/компиляция/i.test(t)) return true;
  return BUNDLE_WORD.test(t) && (NUMBER_RANGE.test(t) || BOOK_COUNT.test(t));
}

// ---------------------------------------------------------------- catalog lookup

function openCatalog(cfg) {
  if (!fs.existsSync(cfg.catalogPath)) return null;
  const cat = new Database(cfg.catalogPath, { readonly: true });
  const has = (t) => !!cat.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?").get(t);
  if (!has('libbook') || !has('libgenre') || !has('libgenrelist')) {
    cat.close();
    return null;
  }
  const q = {
    book: cat.prepare('SELECT BookId, Lang, SrcLang, Year FROM libbook WHERE BookId = ?'),
    joined: has('libjoinedbooks') ? cat.prepare('SELECT GoodId FROM libjoinedbooks WHERE BadId = ? ORDER BY Id DESC LIMIT 1') : null,
    genres: cat.prepare('SELECT l.GenreCode AS code FROM libgenre g JOIN libgenrelist l ON l.GenreId = g.GenreId WHERE g.BookId = ? ORDER BY g.Id'),
    series: has('libseq') && has('libseqname') ? cat.prepare('SELECT n.SeqName AS name, s.SeqNumb AS no, s.Type AS type FROM libseq s JOIN libseqname n ON n.SeqId = s.SeqId WHERE s.BookId = ? ORDER BY s.Type, s.Level') : null,
    translators: has('libtranslator') ? cat.prepare('SELECT COUNT(*) AS n FROM libtranslator WHERE BookId = ?') : null,
    authors: has('libavtor') && has('libavtorname') ? cat.prepare('SELECT CASE WHEN n.MasterId > 0 THEN n.MasterId ELSE a.AvtorId END AS id FROM libavtor a LEFT JOIN libavtorname n ON n.AvtorId = a.AvtorId WHERE a.BookId = ? ORDER BY a.Pos') : null,
  };
  // Looks up one book id. A book that was merged into a newer copy has its
  // genres on the newer copy, so that one is tried as well.
  const lookup = (id) => {
    let book = q.book.get(id);
    if (!book) return null;
    let genres = q.genres.all(book.BookId).map((r) => r.code);
    for (let hops = 0; !genres.length && q.joined && hops < 5; hops++) {
      const j = q.joined.get(book.BookId);
      const next = j && q.book.get(j.GoodId);
      if (!next || next.BookId === book.BookId) break;
      book = next;
      genres = q.genres.all(book.BookId).map((r) => r.code);
    }
    const series = q.series ? q.series.all(book.BookId) : [];
    // Type 0 is a story series by the author; other types are publisher series.
    const story = series.filter((s) => s.type === 0);
    const lang = c.normLang(book.Lang);
    const srcLang = c.normLang(book.SrcLang);
    return {
      genres: [...new Set(genres)],
      year: book.Year > 0 ? book.Year : null,
      srcLang,
      series: story.map((s) => ({ name: s.name, no: s.no || null })),
      translated: (q.translators && q.translators.get(book.BookId).n > 0) || (srcLang && lang && srcLang !== lang) ? 1 : 0,
      authorIds: q.authors ? [...new Set(q.authors.all(book.BookId).map((r) => r.id).filter((x) => x != null))] : [],
    };
  };
  return { lookup, close: () => cat.close() };
}

// ---------------------------------------------------------------- the main routine

function enrichTable(db, cfg, table, cat, map) {
  addColumns(db, table);
  const liked = table === 'books';
  const rows = db.prepare(`SELECT t.*, p.norm FROM ${table} t LEFT JOIN parsed p ON p.source = ? AND p.key = t.key`).all(PARSED_SOURCE[table]);
  const archiveNorms = new Set(db.prepare("SELECT norm FROM parsed WHERE source = 'archive' AND norm IS NOT NULL").all().map((r) => r.norm));
  const archiveIds = new Set(db.prepare("SELECT source_file FROM parsed WHERE source = 'archive'").all().map((r) => c.flibustaId(r.source_file)).filter(Boolean));
  const update = db.prepare(`UPDATE ${table} SET genres = @genres, year = @year, src_lang = @src_lang, series = @series, series_no = @series_no,
    is_collection = @is_collection, cat_genres = @cat_genres, cat_year = @cat_year, cat_src_lang = @cat_src_lang, cat_series = @cat_series,
    cat_translated = @cat_translated, cat_author_ids = @cat_author_ids, genre_groups = @genre_groups, is_fiction = @is_fiction,
    in_archive = @in_archive WHERE key = @key`);
  const stats = { total: rows.length, inCatalog: 0, withGenre: 0, noGenre: [], collections: 0, fiction: 0, inArchive: 0, candidates: 0, unmapped: new Map() };
  const list = (a) => (a && a.length ? JSON.stringify(a) : null);

  db.transaction(() => {
    for (const r of rows) {
      const id = Number(String(r.key).split('-')[0]);
      const info = cat && Number.isInteger(id) ? cat.lookup(id) : null;
      if (info) stats.inCatalog++;
      const fileGenres = c.jsonList(r.genres);
      const catGenres = info ? info.genres : [];
      // the genres used for grouping: catalog first, the book file as a fallback
      const used = catGenres.length ? catGenres : fileGenres;
      const groups = [];
      for (const g of used) {
        const group = map.codes[g];
        if (!group) stats.unmapped.set(g, (stats.unmapped.get(g) || 0) + 1);
        else if (!groups.includes(group)) groups.push(group);
      }
      const kinds = groups.map((g) => map.groups[g]);
      const fiction = kinds.includes('fiction') && !kinds.includes('nonfiction') ? 1 : 0;
      const collection = isCollection(r.title, r.words) ? 1 : 0;
      const inArchive = liked ? null : archiveIds.has(String(id)) || (r.norm && archiveNorms.has(r.norm)) ? 1 : 0;
      const first = info && info.series[0];
      update.run({
        key: r.key,
        genres: liked && !fileGenres.length ? list(catGenres) : r.genres,
        year: liked && r.year == null && info ? info.year : r.year,
        src_lang: liked && !r.src_lang && info ? info.srcLang : r.src_lang,
        series: liked && !r.series && first ? first.name : r.series,
        series_no: liked && !r.series && first && first.no ? String(first.no) : r.series_no,
        is_collection: collection,
        cat_genres: list(catGenres),
        cat_year: info ? info.year : null,
        cat_src_lang: info ? info.srcLang : null,
        cat_series: info ? list(info.series) : null,
        cat_translated: info ? info.translated : null,
        cat_author_ids: info ? list(info.authorIds) : null,
        genre_groups: list(groups),
        is_fiction: fiction,
        in_archive: inArchive,
      });
      if (used.length) stats.withGenre++;
      else stats.noGenre.push(r.key);
      if (collection) stats.collections++;
      if (fiction) stats.fiction++;
      if (inArchive) stats.inArchive++;
      if (!liked && fiction && r.lang === 'ru' && !inArchive && !collection && r.parse_status !== 'failed') stats.candidates++;
    }
    if (!liked) db.prepare(`UPDATE ${table} SET source = ?`).run(PARSED_SOURCE[table]);
  })();
  return stats;
}

function enrichAll(db, cfg) {
  const map = genreMap.build(db, cfg);
  const cat = openCatalog(cfg);
  if (!cat) console.log('  NOTE: data/catalog.db is missing or incomplete. Run "node src/fetch-catalog.js" first; until then only the genres inside the book files are used.');
  try {
    const liked = enrichTable(db, cfg, 'books', cat, map);
    const feed = enrichTable(db, cfg, 'feed_books', cat, map);
    const matched = enrichTable(db, cfg, 'matched_books', cat, map);
    createUnlabeledView(db);
    const unmapped = new Map(liked.unmapped);
    for (const [g, n] of feed.unmapped) unmapped.set(g, (unmapped.get(g) || 0) + n);
    c.setMeta(db, 'unmapped_genres', [...unmapped].sort((a, b) => b[1] - a[1]).map(([code, books]) => ({ code, books })));
    c.setMeta(db, 'has_catalog', !!cat);
    return { liked, feed, matched, map, hasCatalog: !!cat };
  } finally {
    if (cat) cat.close();
  }
}

async function main() {
  const cfg = c.loadConfig();
  const db = c.openDb(cfg);
  const { liked, feed, matched, map } = enrichAll(db, cfg);
  console.log(`Genre map: ${Object.keys(map.codes).length} codes in ${Object.keys(map.groups).length} groups, ${map.unmapped.length} codes unmapped`);
  console.log(`Liked books: ${liked.total}; found in the catalog: ${liked.inCatalog}; with a genre: ${liked.withGenre} (${c.pct(liked.withGenre, liked.total)}); STILL NO GENRE: ${liked.noGenre.length}; collections: ${liked.collections}`);
  if (liked.noGenre.length) console.log('  no genre: ' + liked.noGenre.join(', '));
  console.log(`Feed books: ${feed.total}; found in the catalog: ${feed.inCatalog}; with a genre: ${feed.withGenre}; fiction: ${feed.fiction}; already in the archive: ${feed.inArchive}; collections: ${feed.collections}; candidates: ${feed.candidates}`);
  if (matched.total) console.log(`Matched background books: ${matched.total}; fiction: ${matched.fiction}; already in the archive: ${matched.inArchive}; collections: ${matched.collections}; usable: ${matched.candidates}`);
  db.close();
}

if (require.main === module) {
  main().catch((e) => {
    console.error(e.message || e);
    process.exit(1);
  });
}

module.exports = { main, enrichAll, isCollection, openCatalog };
