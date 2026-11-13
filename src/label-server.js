'use strict';
// Milestone 3, part G. The labelling page.
//
//   node src/label-server.js      then open http://localhost:3000
//
// Tab "New books": one unlabeled book at a time from the view "unlabeled_books"
// (feed, matched and rated-matched books together). Each card shows the average
// reader rating and the number of votes. Queue order: books with an average of
// 3 or more and at least 5 votes come first, and among those the rated-matched
// books come before the others; inside each group the order is random.
// Answers go into the table "labels" (key, label, time). A book that has an
// answer is never shown again. Books downloaded while the page is open join
// the queue by themselves, because every "next book" is a fresh database query.
//
// Tab "My books": the liked books, with a star for favourites (table "stars").

const path = require('node:path');
const express = require('express');
const c = require('./common');

const PORT = Number(process.env.PORT) || 3000;
const LABELS = new Set(['never', 'maybe', 'want']);

const cfg = c.loadConfig();
const db = c.openDb(cfg);
db.pragma('busy_timeout = 10000'); // the downloader may be writing at the same time

db.exec('CREATE TABLE IF NOT EXISTS book_ratings (key TEXT PRIMARY KEY, n INTEGER, avg REAL)'); // filled by fetch-books.js

const WITH_RATING = 'SELECT u.*, COALESCE(r.n, 0) AS votes, r.avg AS rating FROM unlabeled_books u LEFT JOIN book_ratings r ON r.key = u.key';

function hasPool() {
  return !!db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = 'unlabeled_books'").get();
}

function seriesOf(row) {
  const cat = c.jsonList(row.cat_series)[0];
  if (cat && cat.name) return cat.no ? `${cat.name} #${cat.no}` : cat.name;
  if (row.series) return row.series_no ? `${row.series} #${row.series_no}` : row.series;
  return '';
}

function card(row) {
  return {
    key: row.key,
    authors: c.jsonList(row.authors).join(', '),
    title: row.title || '(no title)',
    groups: c.jsonList(row.genre_groups),
    series: seriesOf(row),
    words: row.words || 0,
    annotation: row.annotation || '',
    source: row.source,
    votes: row.votes || 0,
    rating: row.votes ? Math.round(row.rating * 100) / 100 : null,
  };
}

function counts() {
  const done = db.prepare('SELECT label, COUNT(*) AS n FROM labels GROUP BY label').all();
  const by = { never: 0, maybe: 0, want: 0 };
  for (const r of done) by[r.label] = r.n;
  const left = hasPool() ? db.prepare('SELECT COUNT(*) AS n FROM unlabeled_books WHERE key NOT IN (SELECT key FROM labels)').get().n : 0;
  return { done: by.never + by.maybe + by.want, ...by, left };
}

function nextBook(skipKey) {
  if (!hasPool()) return null;
  const row = db.prepare(`${WITH_RATING} WHERE u.key NOT IN (SELECT key FROM labels) AND u.key != ?
    ORDER BY (COALESCE(r.n, 0) >= 5 AND r.avg >= 3) DESC, (u.source = 'rated-matched') DESC, random() LIMIT 1`).get(skipKey || '');
  return row ? card(row) : null;
}

const app = express();
app.use(express.json());

app.get('/', (req, res) => res.sendFile(path.join(__dirname, 'label.html')));

app.get('/api/next', (req, res) => res.json({ book: nextBook(), counts: counts() }));

app.post('/api/label', (req, res) => {
  const { key, label } = req.body || {};
  if (!key || !LABELS.has(label)) return res.status(400).json({ error: 'need a key and a label: never, maybe or want' });
  db.prepare('INSERT INTO labels (key, label, time) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET label = excluded.label, time = excluded.time')
    .run(String(key), label, new Date().toISOString());
  res.json({ book: nextBook(), counts: counts() });
});

// Takes back the most recent answer and shows that book again.
app.post('/api/undo', (req, res) => {
  const last = db.prepare('SELECT key, label FROM labels ORDER BY time DESC, rowid DESC LIMIT 1').get();
  if (!last) return res.json({ book: null, undone: null, counts: counts() });
  db.prepare('DELETE FROM labels WHERE key = ?').run(last.key);
  const row = hasPool() ? db.prepare(`${WITH_RATING} WHERE u.key = ?`).get(last.key) : null;
  res.json({ book: row ? card(row) : nextBook(), undone: last, counts: counts() });
});

app.get('/api/liked', (req, res) => {
  const rows = db.prepare('SELECT b.key, b.authors, b.title, b.series, b.series_no, b.cat_series, s.key IS NOT NULL AS starred FROM books b LEFT JOIN stars s ON s.key = b.key').all();
  const books = rows.map((r) => ({ key: r.key, authors: c.jsonList(r.authors).join(', '), title: r.title || '(no title)', series: seriesOf(r), starred: !!r.starred }));
  books.sort((a, b) => a.authors.localeCompare(b.authors, 'ru') || a.series.localeCompare(b.series, 'ru', { numeric: true }) || a.title.localeCompare(b.title, 'ru', { numeric: true }));
  res.json({ books, starred: books.filter((b) => b.starred).length });
});

app.post('/api/star', (req, res) => {
  const { key, starred } = req.body || {};
  if (!key || !db.prepare('SELECT 1 FROM books WHERE key = ?').get(String(key))) return res.status(400).json({ error: 'unknown book' });
  if (starred) db.prepare('INSERT OR IGNORE INTO stars (key, time) VALUES (?, ?)').run(String(key), new Date().toISOString());
  else db.prepare('DELETE FROM stars WHERE key = ?').run(String(key));
  res.json({ starred: db.prepare('SELECT COUNT(*) AS n FROM stars').get().n });
});

app.listen(PORT, '127.0.0.1', () => {
  const n = counts();
  console.log(`Labelling page: http://localhost:${PORT}`);
  console.log(`  ${n.done} books answered so far, ${n.left} waiting. Stop with Ctrl+C.`);
});
