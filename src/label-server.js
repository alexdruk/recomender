'use strict';
// Milestone 3c, part C. The labelling page.
//
//   node src/label-server.js      (npm run label)   then open http://localhost:3000
//
// Four tabs:
//   My authors           every author of my liked books, with a switch "I almost always
//                        read this author" (table "followed_authors").
//   New books            one book at a time. Only books my rules allow: eligible under
//                        the series rule, at least 5 votes, average 3 or more. The order
//                        comes from the table "label_queue" (written by "npm run funnel"):
//                        half are the books the best model scores highest, half are picked
//                        at random, mixed, and the page does not show which is which.
//                        Answers go to "labels" (never / maybe / want / skip) together
//                        with the half the book came from.
//   Books by my authors  other fiction books of my authors that are not in my library.
//                        Answers go to "read_marks" (disliked / liked / unread / forgot).
//   Add a book           find any catalog book and mark it "read it, did not like it".
//
// Books marked "read it" are downloaded by the next "npm run funnel".

const path = require('node:path');
const express = require('express');
const Database = require('better-sqlite3');
const c = require('./common');
const index = require('./build-index');

const PORT = Number(process.env.PORT) || 3000;
const LABELS = new Set(['never', 'maybe', 'want', 'skip']);
const MARKS = new Set(['disliked', 'liked', 'unread', 'forgot']);
const MIN_VOTES = 5;
const MIN_AVG = 3;
const MAX_COMMENTS = 6;
const COMMENT_CHARS = 600;
const MIN_LIKED_BEFORE_LIST = 2; // until I fill my list: authors with at least this many liked books
const ELIGIBLE = index.ELIGIBLE.map((s) => `'${s}'`).join(', ');

const cfg = c.loadConfig();
const db = c.openDb(cfg);
db.pragma('busy_timeout = 10000'); // the downloader may be writing at the same time
index.build(cfg, db, { quiet: true }); // series, eligibility, votes and comments are fresh on every start
const ix = index.openIndex(cfg, true);
const cat = new Database(cfg.catalogPath, { readonly: true });
const hasReviews = !!cat.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'reviews'").get();

const now = () => new Date().toISOString();
const round2 = (x) => Math.round(x * 100) / 100;

// ---------------------------------------------------------------- shared pieces

function followedIds() {
  return new Set(db.prepare('SELECT author_id FROM followed_authors').all().map((r) => r.author_id));
}

function seriesText(name, no, total) {
  if (!name) return '';
  if (no && total) return `${name} - book ${no} of ${total}`;
  if (no) return `${name} - book ${no}`;
  return total ? `${name} - number not known, ${total} books in the series` : `${name} - number not known`;
}

// The newest comments of a book. Comments left on an older copy count too, as on the site.
function commentsOf(catId) {
  if (!hasReviews || catId == null) return [];
  const ids = [catId, ...ix.prepare('SELECT id FROM alias WHERE root = ?').all(catId).map((r) => r.id)];
  const rows = cat.prepare(`SELECT Name AS name, Time AS time, Text AS text FROM reviews WHERE BookId IN (${ids.map(() => '?').join(',')})
    AND LENGTH(TRIM(Text)) > 0 ORDER BY Time DESC LIMIT ${MAX_COMMENTS}`).all(...ids);
  return rows.map((r) => {
    const text = String(r.text).replace(/\r/g, '').replace(/\n{3,}/g, '\n\n').trim();
    const chars = [...text];
    return { name: r.name || '', date: String(r.time || '').slice(0, 10), text: chars.length > COMMENT_CHARS ? chars.slice(0, COMMENT_CHARS).join('') + ' ...' : text };
  });
}

// ---------------------------------------------------------------- tab "New books"

const NEW_FROM = `FROM unlabeled_books u JOIN book_facts f ON f.key = u.key LEFT JOIN label_queue q ON q.key = u.key`;
const NEW_COLS = `u.key, u.authors, u.title, u.genre_groups, u.words, u.annotation, f.cat_id, f.series, f.series_no, f.series_total, f.status,
  f.votes, f.avg, f.comments, f.author_ids, q.grp, q.score, q.pos`;
const NEW_WHERE = `f.status IN (${ELIGIBLE}) AND f.votes >= ${MIN_VOTES} AND f.avg >= ${MIN_AVG}`;

function card(row) {
  const followed = followedIds();
  return {
    key: row.key,
    authors: c.jsonList(row.authors).join(', '),
    title: row.title || '(no title)',
    groups: c.jsonList(row.genre_groups),
    series: seriesText(row.series, row.series_no, row.series_total),
    flagged: row.status === 'unknown',
    continuation: row.status === 'continuation',
    words: row.words || 0,
    annotation: row.annotation || '',
    votes: row.votes || 0,
    rating: row.votes ? round2(row.avg) : null,
    followed: c.jsonList(row.author_ids).some((a) => followed.has(a)),
    commentCount: row.comments || 0,
    comments: commentsOf(row.cat_id),
  };
}

function nextBook(skipKey) {
  const row = db.prepare(`SELECT ${NEW_COLS} ${NEW_FROM} WHERE ${NEW_WHERE} AND u.key NOT IN (SELECT key FROM labels) AND u.key != ?
    ORDER BY (q.pos IS NULL), q.pos, random() LIMIT 1`).get(skipKey || '');
  return row ? card(row) : null;
}

function counts() {
  const by = { never: 0, maybe: 0, want: 0, skip: 0, rule: 0 };
  for (const r of db.prepare("SELECT CASE WHEN status = 'rule' THEN 'rule' ELSE label END AS l, COUNT(*) AS n FROM labels GROUP BY 1").all()) by[r.l] = r.n;
  const left = db.prepare(`SELECT COUNT(*) AS n ${NEW_FROM} WHERE ${NEW_WHERE} AND u.key NOT IN (SELECT key FROM labels)`).get().n;
  return { done: by.never + by.maybe + by.want, ...by, left };
}

const app = express();
app.use(express.json());

app.get('/', (req, res) => res.sendFile(path.join(__dirname, 'label.html')));

app.get('/api/next', (req, res) => res.json({ book: nextBook(), counts: counts(), hasComments: hasReviews }));

app.post('/api/label', (req, res) => {
  const { key, label } = req.body || {};
  if (!key || !LABELS.has(label)) return res.status(400).json({ error: 'need a key and a label: never, maybe, want or skip' });
  const row = db.prepare(`SELECT ${NEW_COLS} ${NEW_FROM} WHERE u.key = ?`).get(String(key));
  if (!row) return res.status(400).json({ error: 'unknown book' });
  // which half of the queue the book came from. No queue yet = every book is a random pick.
  const hasQueue = !!db.prepare('SELECT 1 FROM label_queue LIMIT 1').get();
  const grp = row.grp || (hasQueue ? 'rest' : 'random');
  const followed = followedIds();
  const isFollowed = c.jsonList(row.author_ids).some((a) => followed.has(a)) ? 1 : 0;
  db.prepare(`INSERT INTO labels (key, label, time, status, grp, score, followed) VALUES (?, ?, ?, NULL, ?, ?, ?)
    ON CONFLICT(key) DO UPDATE SET label = excluded.label, time = excluded.time, status = NULL, grp = excluded.grp, score = excluded.score, followed = excluded.followed`)
    .run(String(key), label, now(), grp, row.score, isFollowed);
  res.json({ book: nextBook(), counts: counts() });
});

// Takes back the most recent answer and shows that book again.
app.post('/api/undo', (req, res) => {
  const last = db.prepare("SELECT key, label FROM labels WHERE status IS NULL ORDER BY time DESC, rowid DESC LIMIT 1").get();
  if (!last) return res.json({ book: null, undone: null, counts: counts() });
  db.prepare('DELETE FROM labels WHERE key = ?').run(last.key);
  const row = db.prepare(`SELECT ${NEW_COLS} ${NEW_FROM} WHERE u.key = ?`).get(last.key);
  res.json({ book: row ? card(row) : nextBook(), undone: last, counts: counts() });
});

// ---------------------------------------------------------------- tab "My authors"

// Every author of my liked books, with the number of liked books (co-authors each count).
function likedAuthors() {
  const n = new Map();
  for (const r of db.prepare("SELECT author_ids FROM book_facts WHERE kind = 'liked'").all()) {
    for (const a of c.jsonList(r.author_ids)) n.set(a, (n.get(a) || 0) + 1);
  }
  const followed = followedIds();
  const name = ix.prepare('SELECT name, aka FROM author WHERE id = ?');
  const list = [...n].map(([id, liked]) => {
    const a = name.get(id) || {};
    return { id, name: a.name || `author ${id}`, aka: a.aka || '', liked, followed: followed.has(id) };
  });
  list.sort((a, b) => b.liked - a.liked || a.name.localeCompare(b.name, 'ru'));
  return list;
}

app.get('/api/authors', (req, res) => {
  const authors = likedAuthors();
  res.json({ authors, followed: authors.filter((a) => a.followed).length });
});

app.post('/api/follow', (req, res) => {
  const id = Number((req.body || {}).id);
  const author = likedAuthors().find((a) => a.id === id);
  if (!author) return res.status(400).json({ error: 'unknown author' });
  if ((req.body || {}).on) db.prepare('INSERT OR IGNORE INTO followed_authors (author_id, name, time) VALUES (?, ?, ?)').run(id, author.name, now());
  else db.prepare('DELETE FROM followed_authors WHERE author_id = ?').run(id);
  res.json({ followed: db.prepare('SELECT COUNT(*) AS n FROM followed_authors').get().n });
});

// ---------------------------------------------------------------- tab "Books by my authors"

function catBook(r, marks) {
  return {
    id: r.id, title: r.title, authors: r.authors, year: r.year, series: r.series || '', no: r.series_no, total: r.series_total,
    votes: r.votes || 0, rating: r.votes ? round2(r.avg) : null, status: r.status, mark: marks.get(r.id) || null,
  };
}

app.get('/api/author-books', (req, res) => {
  const all = likedAuthors();
  const listFilled = all.some((a) => a.followed);
  const authors = listFilled ? all.filter((a) => a.followed) : all.filter((a) => a.liked >= MIN_LIKED_BEFORE_LIST);
  const marks = new Map(db.prepare('SELECT id, mark FROM read_marks').all().map((r) => [r.id, r.mark]));
  const q = ix.prepare(`SELECT k.* FROM cat_author a JOIN cat k ON k.id = a.id
    WHERE a.author_id = ? AND k.fiction = 1 AND k.in_library = 0 AND k.collection = 0`);
  const groups = [];
  let total = 0, marked = 0;
  for (const a of authors) {
    // several editions of one book: keep the one with the most votes
    const best = new Map();
    for (const r of q.all(a.id)) {
      const old = best.get(r.norm_title);
      if (!old || r.votes > old.votes || (r.votes === old.votes && r.id > old.id)) best.set(r.norm_title, r);
    }
    // an edition that is already marked wins, so an answer never disappears
    for (const r of q.all(a.id)) if (marks.has(r.id)) best.set(r.norm_title, r);
    const books = [...best.values()].map((r) => catBook(r, marks));
    books.sort((x, y) => (x.series === '') - (y.series === '') || x.series.localeCompare(y.series, 'ru') || (x.no || 9999) - (y.no || 9999)
      || (x.year || 0) - (y.year || 0) || x.title.localeCompare(y.title, 'ru'));
    if (!books.length) continue;
    total += books.length;
    marked += books.filter((b) => b.mark).length;
    groups.push({ author: a, books });
  }
  res.json({ groups, total, marked, listFilled });
});

app.post('/api/mark', (req, res) => {
  const { mark, tab } = req.body || {};
  const id = Number((req.body || {}).id);
  const row = ix.prepare('SELECT id, title, authors FROM cat WHERE id = ?').get(id);
  if (!row) return res.status(400).json({ error: 'unknown book' });
  if (mark == null) {
    db.prepare('DELETE FROM read_marks WHERE id = ?').run(id);
  } else {
    if (!MARKS.has(mark)) return res.status(400).json({ error: 'mark must be disliked, liked, unread or forgot' });
    db.prepare(`INSERT INTO read_marks (id, mark, tab, authors, title, time) VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET mark = excluded.mark, tab = excluded.tab, time = excluded.time`).run(id, mark, tab === 'add' ? 'add' : 'authors', row.authors, row.title, now());
  }
  res.json({ ok: true, added: addedBooks() });
});

// ---------------------------------------------------------------- tab "Add a book"

function addedBooks() {
  return db.prepare("SELECT id, authors, title, mark FROM read_marks WHERE tab = 'add' ORDER BY time DESC").all();
}

app.get('/api/search', (req, res) => {
  const words = c.normaliseText(req.query.q || '').split(' ').filter((w) => w.length >= 2).slice(0, 6);
  if (!words.length) return res.json({ books: [], added: addedBooks() });
  const marks = new Map(db.prepare('SELECT id, mark FROM read_marks').all().map((r) => [r.id, r.mark]));
  const rows = ix.prepare(`SELECT * FROM cat WHERE ${words.map(() => 'search LIKE ?').join(' AND ')} ORDER BY votes DESC, id DESC LIMIT 40`)
    .all(...words.map((w) => `%${w}%`));
  res.json({ books: rows.map((r) => ({ ...catBook(r, marks), inLibrary: !!r.in_library })), added: addedBooks() });
});

app.listen(PORT, '127.0.0.1', () => {
  const n = counts();
  console.log(`Labelling page: http://localhost:${PORT}`);
  console.log(`  ${n.done} books answered so far (${n.rule} more are set aside by the series rule), ${n.left} eligible books waiting.`);
  console.log(`  Reader comments: ${hasReviews ? 'shown on the cards' : 'not in the catalog'}. Stop with Ctrl+C.`);
});
