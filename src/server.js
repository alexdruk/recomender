'use strict';
// The recommendations page:  npm start   ->  http://localhost:3000
//
// Shows the feed books ranked by the model, takes my scores from 1 to 5,
// retrains the model after every 10 scores, and runs the daily loop at start
// and then every 6 hours (config.json "checkEveryHours"). Everything is local.
//
// The page is also reachable from the home network (an old iPad), and
// /epub/<id> hands a book to the iPad as an EPUB file for iBooks.
//
// Sending a book is not a verdict. A book goes:
//   recommendations -> "To send" (the waiting list) -> "Reading" (sent to iBooks)
//   -> "My scores" (my score from 1 to 5, which can be changed or cleared at any time).

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, execFileSync } = require('node:child_process');
const express = require('express');
const c = require('./common');
const net = require('./net');
const { ensureTables } = require('./daily');
const { FALLBACK_AVERAGE } = require('./ratings');

const cfg = c.loadConfig();
net.setPause(cfg.pauseSeconds);
const db = c.openDb(cfg);
db.pragma('busy_timeout = 60000');
ensureTables(db);
fs.mkdirSync(cfg.epubDir, { recursive: true });

const log = (msg) => console.log(`[${new Date().toISOString()}] ${msg}`);

// ---------------------------------------------------------------- background jobs, one at a time

const JOBS = {
  daily: { what: 'Checking for new books', cmd: process.execPath, args: [path.join(__dirname, 'daily.js')] },
  train: { what: 'Retraining the model', cmd: cfg.python, args: [path.join(cfg.root, 'model', 'recommend.py'), 'train'] },
};
const queue = [];
let running = null;

function enqueue(name) {
  if (running === name && name === 'daily') return false;
  if (queue.includes(name)) return false;
  queue.push(name);
  next();
  return true;
}

function next() {
  if (running || !queue.length) return;
  const name = queue.shift();
  const job = JOBS[name];
  running = name;
  log(`${job.what} ...`);
  const child = spawn(job.cmd, job.args, { cwd: cfg.root, stdio: 'inherit' });
  const done = (note) => {
    if (running !== name) return;
    running = null;
    log(`${job.what}: ${note}`);
    next();
  };
  child.on('error', (e) => done('could not start: ' + e.message));
  child.on('exit', (code) => done(code === 0 ? 'finished' : `stopped with an error (code ${code})`));
}

// ---------------------------------------------------------------- what the page shows

// A book that readers rated badly is hidden: at least 5 votes and an average below 3.
// A book without votes is never hidden for that.
const LOW_VOTES = 5;
const LOW_AVERAGE = 3;
// "s" is feed_scores: the books that can be shown as recommendations
const OPEN = `s.passed = 1 AND s.score IS NOT NULL
  AND s.key NOT IN (SELECT key FROM answers) AND s.key NOT IN (SELECT key FROM book_state)
  AND NOT EXISTS (SELECT 1 FROM rating_facts q WHERE q.key = s.key AND q.votes >= ${LOW_VOTES} AND q.sum < ${LOW_AVERAGE} * q.votes)`;

function topPercent() {
  const v = Number(c.getMeta(db, 'top_percent'));
  return v > 0 && v <= 100 ? v : cfg.topPercent;
}

// the number Flibusta uses for the book on its site
const bookId = (key) => String(key).split('-')[0];

function state() {
  const top = topPercent();
  const map = JSON.parse(fs.readFileSync(cfg.genreMapPath, 'utf8'));
  const since = new Date(Date.now() - cfg.restDays * 86400000).toISOString();
  const rows = db.prepare(`
    SELECT s.key, s.score, s.percentile, s.closest, s.series_note, s.added_at, s.note,
           f.authors, f.title, f.series, f.series_no, f.genres, f.words, f.annotation, f.is_translation,
           r.votes, r.sum, r.a_known, r.a_books, r.a_votes, r.a_sum, r.s_known, r.s_books, r.s_votes, r.s_sum
    FROM feed_scores s JOIN feed_books f ON f.key = s.key LEFT JOIN rating_facts r ON r.key = s.key
    WHERE ${OPEN}
    ORDER BY s.score DESC`).all();
  // "rating 4.3 from 12 votes": the plain average; null when nobody voted
  const rating = (votes, sum, books) => (votes > 0 ? { votes, avg: sum / votes, books: books || 0 } : null);
  const card = (r) => {
    const groups = [];
    for (const g of c.jsonList(r.genres)) if (map.codes[g] && !groups.includes(map.codes[g])) groups.push(map.codes[g]);
    return {
      key: r.key,
      id: bookId(r.key),
      authors: c.jsonList(r.authors).join(', '),
      title: r.title,
      series: r.series,
      seriesNo: r.series_no,
      seriesNote: r.series_note,
      genres: groups,
      words: r.words,
      translated: !!r.is_translation,
      annotation: r.annotation,
      score: r.score,
      percentile: r.percentile,
      note: r.note,
      rating: rating(r.votes, r.sum),
      authorRecord: rating(r.a_votes, r.a_sum, r.a_books),
      seriesRecord: r.s_known ? rating(r.s_votes, r.s_sum, r.s_books) : null,
      inSeries: !!r.s_known,
      closest: c.jsonList(r.closest),
      link: `${cfg.mirrors[0]}/b/${bookId(r.key)}`,
    };
  };
  const counts = db.prepare('SELECT COUNT(*) AS analysed, SUM(passed) AS passed FROM feed_scores').get();
  const answers = db.prepare("SELECT COUNT(*) AS n, SUM(kind = 'read') AS read, SUM(rescored = 0) AS old FROM answers").get();
  const lowRated = db.prepare(`SELECT COUNT(*) AS n FROM feed_scores s JOIN rating_facts r ON r.key = s.key
    WHERE s.passed = 1 AND s.score IS NOT NULL AND r.votes >= ${LOW_VOTES} AND r.sum < ${LOW_AVERAGE} * r.votes
      AND s.key NOT IN (SELECT key FROM answers) AND s.key NOT IN (SELECT key FROM book_state)`).get().n;
  const ratingsInfo = c.getMeta(db, 'ratings_info') || {};
  const hasFiles = db.prepare("SELECT 1 FROM sqlite_master WHERE name = 'feed_files'").get();
  const lastFile = hasFiles ? db.prepare('SELECT MAX(processed_at) AS t FROM feed_files').get().t : null;
  const recommended = rows.filter((r) => r.percentile <= top).map(card);
  return {
    top,
    restDays: cfg.restDays,
    retrainEvery: cfg.retrainEveryAnswers,
    counts: counts_(recommended.length),
    recommended,
    rest: rows.filter((r) => r.percentile > top && r.added_at >= since).map(card),
    status: {
      lastDownload: lastFile ? lastFile.replace(' ', 'T') + 'Z' : null,
      lastCheck: c.getMeta(db, 'daily_last_run') || null,
      analysed: counts.analysed,
      passed: counts.passed || 0,
      scores: answers.n,
      scoresRead: answers.read || 0,
      scoresOld: answers.old || 0,
      lowRated,
      ratingsAt: ratingsInfo.refreshed_at || null,
      ratingsAverage: ratingsInfo.average || FALLBACK_AVERAGE,
      sinceTraining: c.getMeta(db, 'answers_since_train') || 0,
      model: c.getMeta(db, 'model_info') || null,
    },
    gaps: c.getMeta(db, 'feed_gaps') || [],
    busy: running ? JOBS[running].what : null,
    waiting: queue.map((n) => JOBS[n].what),
  };
}

// How many books each list has: the numbers beside the names in the menu.
function counts_(recommended) {
  if (recommended === undefined) {
    recommended = db.prepare(`SELECT COUNT(*) AS n FROM feed_scores s WHERE ${OPEN} AND s.percentile <= ?`).get(topPercent()).n;
  }
  const st = db.prepare(`SELECT SUM(state = 'to_send') AS toSend, SUM(state = 'reading') AS reading
    FROM book_state WHERE key NOT IN (SELECT key FROM answers)`).get();
  const an = db.prepare('SELECT COUNT(*) AS n FROM answers').get();
  return { home: recommended, tosend: st.toSend || 0, reading: st.reading || 0, scores: an.n };
}

// The books of one list: "tosend" (in the order I added them), "reading"
// (newest first), "scores" (every book I scored or was sent, newest first).
// There is no list of books I have not seen: a book gets here only after I
// answered it or it was sent to me.
const LISTS = {
  tosend: `SELECT f.key, f.authors, f.title, f.series, f.series_no, b.time, NULL AS kind, NULL AS score, NULL AS rescored, NULL AS sent
    FROM book_state b JOIN feed_books f ON f.key = b.key
    WHERE b.state = 'to_send' AND b.key NOT IN (SELECT key FROM answers) ORDER BY b.time, b.rowid`,
  reading: `SELECT f.key, f.authors, f.title, f.series, f.series_no, b.time, NULL AS kind, NULL AS score, NULL AS rescored, b.time AS sent
    FROM book_state b JOIN feed_books f ON f.key = b.key
    WHERE b.state = 'reading' AND b.key NOT IN (SELECT key FROM answers) ORDER BY b.time DESC, b.rowid DESC`,
  scores: `SELECT f.key, f.authors, f.title, f.series, f.series_no, a.time, a.kind, a.score, a.rescored, b.time AS sent
      FROM answers a JOIN feed_books f ON f.key = a.key LEFT JOIN book_state b ON b.key = a.key AND b.state = 'reading'
    UNION ALL
    SELECT f.key, f.authors, f.title, f.series, f.series_no, b.time, NULL, NULL, NULL, b.time
      FROM book_state b JOIN feed_books f ON f.key = b.key WHERE b.state = 'reading' AND b.key NOT IN (SELECT key FROM answers)
    ORDER BY 6 DESC`,
};

function listBooks(name) {
  return db.prepare(LISTS[name]).all().map((r) => {
    const id = bookId(r.key);
    return {
      key: r.key,
      id,
      time: r.time,
      kind: r.kind,
      score: r.score,
      rescored: r.rescored == null ? null : !!r.rescored,
      sent: r.sent,
      authors: c.jsonList(r.authors).join(', '),
      title: r.title,
      series: r.series,
      seriesNo: r.series_no,
      link: `${cfg.mirrors[0]}/b/${id}`,
      epub: fs.existsSync(epubFile(id)) ? 'ready' : name !== 'tosend' ? 'none' : prepFailed.has(id) ? 'failed' : 'preparing',
    };
  });
}

const knownBook = (key) => db.prepare('SELECT 1 FROM feed_books WHERE key = ?').get(String(key));
const stateOf = (key) => (db.prepare('SELECT state FROM book_state WHERE key = ?').get(String(key)) || {}).state || null;

// Puts books on the waiting list, in the order given. No verdict is recorded.
// Books that are already picked or answered stay where they are.
function addToSend(keys) {
  const put = db.prepare("INSERT INTO book_state (key, state, time) VALUES (?, 'to_send', ?)");
  const now = new Date().toISOString();
  let added = 0;
  db.transaction(() => {
    for (const key of keys) {
      if (!knownBook(key) || stateOf(key) || db.prepare('SELECT 1 FROM answers WHERE key = ?').get(String(key))) continue;
      put.run(String(key), now);
      added++;
    }
  })();
  if (added) log(`${added} ${added === 1 ? 'book' : 'books'} added to the send list`);
  return added;
}

// Counts one new or changed answer towards the next retraining.
function countAnswer() {
  let since = (c.getMeta(db, 'answers_since_train') || 0) + 1;
  let retrain = false;
  if (since >= cfg.retrainEveryAnswers) {
    since = 0;
    retrain = true;
    enqueue('train');
  }
  c.setMeta(db, 'answers_since_train', since);
  return { retrain, sinceTraining: since };
}

// Stores my score, 1 (bad) to 5 (excellent). The score remembers how it was
// given: 'read' when the book was sent to iBooks (it is or was in "Reading"),
// otherwise 'card' (from the card only). A score can be changed at any time.
// Pressing the same score again changes nothing, so a book is never counted
// twice towards the next retraining; on an old answer ("not rescored") it
// confirms the score.
const WORD = { 1: 'dislike', 2: 'dislike', 3: 'average', 4: 'like', 5: 'like' };
function recordScore(key, score) {
  key = String(key);
  const old = db.prepare('SELECT score, kind, rescored FROM answers WHERE key = ?').get(key);
  const state = stateOf(key);
  const kind = state === 'reading' ? 'read' : old ? old.kind : 'card';
  if (old && old.score === score) {
    if (!old.rescored) db.prepare('UPDATE answers SET rescored = 1, kind = ? WHERE key = ?').run(kind, key);
    return { retrain: false, sinceTraining: c.getMeta(db, 'answers_since_train') || 0 };
  }
  db.prepare(`INSERT INTO answers (key, answer, time, kind, score, rescored) VALUES (?, ?, ?, ?, ?, 1)
    ON CONFLICT (key) DO UPDATE SET answer = excluded.answer, time = excluded.time, kind = excluded.kind, score = excluded.score, rescored = 1`)
    .run(key, WORD[score], new Date().toISOString(), kind, score);
  // the "reading" row stays (hidden by the score), so a cleared score puts the book back in "Reading"
  if (state === 'to_send') db.prepare('DELETE FROM book_state WHERE key = ?').run(key);
  const r = countAnswer();
  log(`score ${score} for book ${key} (${kind === 'read' ? 'after reading' : 'from the card'}${old ? ', was ' + old.score : ''})${r.retrain ? ' - retraining the model' : ''}`);
  return r;
}

// Clears a score. The book goes back to where it was: to "Reading" when it
// was scored after reading, otherwise to the recommendations.
function clearAnswer(key) {
  key = String(key);
  const old = db.prepare('SELECT kind FROM answers WHERE key = ?').get(key);
  if (!old) return false;
  db.prepare('DELETE FROM answers WHERE key = ?').run(key);
  if (old.kind === 'read' && !stateOf(key)) {
    db.prepare("INSERT INTO book_state (key, state, time) VALUES (?, 'reading', ?)").run(key, new Date().toISOString());
  }
  log(`score cleared for book ${key} - back to ${old.kind === 'read' ? '"Reading"' : 'the recommendations'}`);
  return true;
}

// A book was sent to iBooks: it moves from "To send" (or straight from the
// recommendations) to "Reading". A book that has an answer stays where it is.
function markReading(key) {
  key = String(key);
  if (db.prepare('SELECT 1 FROM answers WHERE key = ?').get(key) || stateOf(key) === 'reading') return;
  db.prepare('INSERT OR REPLACE INTO book_state (key, state, time) VALUES (?, ?, ?)').run(key, 'reading', new Date().toISOString());
  log(`book ${key} sent to iBooks - now in "Reading"`);
}

// One time only: the earlier rule made every sent book a liked book. Those
// Likes (a Like whose EPUB file was sent) are removed and the books go to "Reading".
function undoSentLikes() {
  if (c.getMeta(db, 'sent_likes_undone')) return;
  const rows = db.prepare("SELECT key, time FROM answers WHERE answer = 'like'").all().filter((r) => fs.existsSync(epubFile(bookId(r.key))));
  db.transaction(() => {
    for (const r of rows) {
      db.prepare('DELETE FROM answers WHERE key = ?').run(r.key);
      db.prepare('INSERT OR REPLACE INTO book_state (key, state, time) VALUES (?, ?, ?)').run(r.key, 'reading', r.time);
    }
    if (rows.length) c.setMeta(db, 'answers_since_train', Math.max(0, (c.getMeta(db, 'answers_since_train') || 0) - rows.length));
    c.setMeta(db, 'sent_likes_undone', new Date().toISOString());
  })();
  log(`Sending is not a verdict any more: ${rows.length} sent ${rows.length === 1 ? 'book' : 'books'} moved from "Liked" to "Reading"${rows.length ? ' (' + rows.map((r) => r.key).join(', ') + ')' : ''}.`);
}

// ---------------------------------------------------------------- EPUB files for iBooks

const epubFile = (id) => path.join(cfg.epubDir, id + '.epub');
const fetching = new Map(); // id -> the download that is running for it
const prepFailed = new Map(); // id -> when the background preparation failed for it

// Makes sure data/epub/<id>.epub exists: the mirror's own EPUB download
// (<mirror>/b/<id>/epub), first mirror first, through the polite queue.
function ensureEpub(id) {
  const file = epubFile(id);
  if (fs.existsSync(file)) return Promise.resolve(file);
  if (!fetching.has(id)) {
    log(`Getting the EPUB of book ${id} ...`);
    const p = net
      .onMirrors(cfg.mirrors, (m) => net.download(`${m}/b/${id}/epub`, file, { magic: 'PK', timeoutMs: 180000, maxWaitMs: 45000 }), log)
      .then((r) => {
        log(`EPUB of book ${id}: ${Math.round(r.value / 1024)} KB from ${r.mirror}`);
        return file;
      })
      .finally(() => fetching.delete(id));
    fetching.set(id, p);
  }
  return fetching.get(id);
}

const LATIN = {
  а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'yo', ж: 'zh', з: 'z', и: 'i', й: 'y', к: 'k', л: 'l', м: 'm',
  н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't', у: 'u', ф: 'f', х: 'kh', ц: 'ts', ч: 'ch', ш: 'sh', щ: 'shch',
  ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu', я: 'ya', і: 'i', ї: 'yi', є: 'ye', ґ: 'g', ў: 'u',
};

// "Джон Вердон" -> "Dzhon Verdon": Latin letters, digits and spaces only.
function latin(text) {
  let out = '';
  for (const ch of String(text || '')) {
    const low = ch.toLowerCase();
    if (Object.hasOwn(LATIN, low)) {
      const t = LATIN[low];
      out += ch === low ? t : t.charAt(0).toUpperCase() + t.slice(1);
    } else {
      out += ch;
    }
  }
  return out
    .normalize('NFKD')
    .replace(/[^A-Za-z0-9]+/g, ' ')
    .trim();
}

// Gets the EPUB files of all waiting books in the background, one after
// another through the polite queue, so a tap on "Read in iBooks" delivers the
// file at once. A book that failed is tried again a few minutes later.
let preparing = false;
async function prepareWaiting() {
  if (preparing) return;
  preparing = true;
  try {
    for (;;) {
      const now = Date.now();
      const id = db.prepare("SELECT key FROM book_state WHERE state = 'to_send' ORDER BY time, rowid").all().map((r) => bookId(r.key))
        .find((i) => !fs.existsSync(epubFile(i)) && !(prepFailed.get(i) > now));
      if (!id) break;
      try {
        await ensureEpub(id);
        prepFailed.delete(id);
      } catch (e) {
        // busy with an update file: look again in 2 minutes; the mirrors said no: in 15 minutes
        prepFailed.set(id, Date.now() + (e.code === 'BUSY' ? 2 : 15) * 60000);
        log(`Preparing the EPUB of book ${id} failed: ${e.message.replace(/\n/g, ' | ')}`);
      }
    }
  } finally {
    preparing = false;
  }
}

// A readable file name: "Author - Title.epub" (the first author only).
function epubName(book) {
  const author = latin(c.jsonList(book.authors)[0]).slice(0, 40).trim();
  const title = latin(book.title).slice(0, 80).trim() || 'Book ' + bookId(book.key);
  return (author ? author + ' - ' : '') + title + '.epub';
}

const escHtml = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

// A plain page without any script, so every browser can show it.
function errorPage(title, lines, retryUrl) {
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escHtml(title)}</title>
<style>
  body { margin: 0; padding: 32px 20px; background: #f6f4ef; color: #22201c; font: 18px/1.5 -apple-system, "Helvetica Neue", Arial, sans-serif; }
  div { max-width: 640px; margin: 0 auto; }
  h1 { font-size: 24px; color: #a2452f; }
  a { display: inline-block; margin: 12px 12px 0 0; padding: 12px 20px; border: 1px solid #2f6f4f; border-radius: 8px; color: #2f6f4f; text-decoration: none; }
</style></head><body><div>
<h1>${escHtml(title)}</h1>
${lines.map((l) => `<p>${escHtml(l)}</p>`).join('\n')}
${retryUrl ? `<a href="${escHtml(retryUrl)}">Try again</a>` : ''}<a href="/tosend">To send</a><a href="/">Recommendations</a>
</div></body></html>`;
}

// ---------------------------------------------------------------- the web server

const app = express();
app.use(express.json());
app.use('/api', (req, res, next) => {
  res.set('Cache-Control', 'no-store'); // old Safari must not show a remembered answer
  next();
});

const page = (req, res) => res.sendFile(path.join(__dirname, 'page.html'));
app.get('/', page);
for (const name of Object.keys(LISTS)) app.get('/' + name, page);
// the old pages "Liked" and "Disliked" are now one page, "My scores"
app.get(['/liked', '/disliked'], (req, res) => res.redirect('/scores'));

// The model report, rebuilt after every training (model/report.py).
app.get('/report', (req, res) => {
  res.set('Cache-Control', 'no-store');
  const file = path.join(cfg.reportsDir, 'model.html');
  if (!fs.existsSync(file)) {
    return res.status(404).send(errorPage('No model report yet', ['The report is built at the end of every training of the model. Run "npm run train" once, or give 10 scores.']));
  }
  res.sendFile(file);
});
app.get('/api/state', (req, res) => res.json(state()));

app.get('/api/list/:name', (req, res) => {
  const name = req.params.name;
  if (!Object.hasOwn(LISTS, name)) return res.status(404).json({ error: 'unknown list' });
  if (name === 'tosend') prepareWaiting(); // in the background; the answer does not wait for it
  res.json({ books: listBooks(name), counts: counts_() });
});

app.post('/api/score', (req, res) => {
  const { key, score } = req.body || {};
  if (![1, 2, 3, 4, 5].includes(score) || !knownBook(key)) {
    return res.status(400).json({ error: 'unknown book, or the score is not a number from 1 to 5' });
  }
  res.json({ ok: true, ...recordScore(key, score) });
});

app.post('/api/clear', (req, res) => {
  const { key } = req.body || {};
  if (!clearAnswer(key)) return res.status(400).json({ error: 'this book has no score' });
  res.json({ ok: true });
});

// { key } adds one book to the waiting list, { keys: [...] } several, in that order
app.post('/api/tosend', (req, res) => {
  const body = req.body || {};
  const keys = Array.isArray(body.keys) ? body.keys : [body.key];
  if (!keys.length || !keys.every((k) => (typeof k === 'string' || typeof k === 'number') && knownBook(k))) {
    return res.status(400).json({ error: 'unknown book' });
  }
  res.json({ ok: true, added: addToSend(keys) });
});

// "Remove" on the waiting list: the book goes back to the recommendations
app.post('/api/unsend', (req, res) => {
  const { key } = req.body || {};
  const r = db.prepare("DELETE FROM book_state WHERE key = ? AND state = 'to_send'").run(String(key));
  if (r.changes) log(`book ${key} removed from the send list`);
  res.json({ ok: true, removed: r.changes });
});

// /epub/<id> gets the EPUB (from the cache or the mirror), moves the book to
// "Reading" and then moves the browser to /epub/<id>/<Author - Title>.epub,
// which sends the file. It records no verdict. The readable name is in the
// address because old Safari names the file after the address.
app.get('/epub/:id{/:name}', async (req, res) => {
  res.set('Cache-Control', 'no-store');
  const id = req.params.id;
  const book = /^\d+$/.test(id) ? db.prepare('SELECT key, authors, title FROM feed_books WHERE key = ?').get(id) : null;
  if (!book) {
    return res.status(404).send(errorPage('Unknown book', [`There is no book with the number ${id} among the books of the daily updates.`]));
  }
  const what = `${c.jsonList(book.authors).join(', ') || 'Author not given'} — ${book.title}`;
  let file;
  try {
    file = await ensureEpub(id);
  } catch (e) {
    log(`EPUB of book ${id} failed: ${e.message.replace(/\n/g, ' | ')}`);
    const lines = e.code === 'BUSY'
      ? [what, 'The computer is downloading new update files from the library right now, and it only makes one request at a time. Please try again in a few minutes.']
      : [what, 'Both library mirrors were asked for the EPUB file and neither gave it:', ...e.message.split('\n').slice(1), 'The library may be down, or it may have no EPUB for this book. Please try again later.'];
    lines.push('Nothing was changed: the book is where it was, so you can send it later.');
    return res.status(e.code === 'BUSY' ? 503 : 502).send(errorPage('Could not get the book', lines, `/epub/${id}`));
  }
  markReading(book.key);
  const name = epubName(book);
  if (req.params.name !== name) return res.redirect(`/epub/${id}/${encodeURIComponent(name)}`);
  res.set('Content-Type', 'application/epub+zip');
  res.set('Content-Disposition', `attachment; filename="${name}"`);
  res.sendFile(file, { headers: { 'Content-Type': 'application/epub+zip' } });
});

app.post('/api/check', (req, res) => {
  const started = enqueue('daily');
  res.json({ ok: true, started });
});

app.post('/api/settings', (req, res) => {
  const v = Number((req.body || {}).top);
  if (!(v > 0 && v <= 100)) return res.status(400).json({ error: 'the share must be a number between 0 and 100' });
  c.setMeta(db, 'top_percent', v);
  res.json({ ok: true, top: v });
});

// The addresses to type on another device in the home network.
function networkAddresses() {
  const out = [];
  let name = '';
  try {
    name = execFileSync('/usr/sbin/scutil', ['--get', 'LocalHostName'], { encoding: 'utf8' }).trim();
  } catch (e) {
    name = os.hostname().split('.')[0];
  }
  if (name) out.push(`http://${name.toLowerCase()}.local:${cfg.port}`);
  for (const list of Object.values(os.networkInterfaces())) {
    for (const a of list || []) if (a.family === 'IPv4' && !a.internal) out.push(`http://${a.address}:${cfg.port}`);
  }
  return out;
}

undoSentLikes();

const server = app.listen(cfg.port, cfg.host, () => {
  log(`Recommendations page on this computer: http://localhost:${cfg.port}`);
  if (cfg.host === '127.0.0.1' || cfg.host === 'localhost') {
    log('The page is open to this computer only ("host" in config.json).');
  } else {
    const list = networkAddresses();
    if (list.length) {
      log('On the iPad (same Wi-Fi) type one of these addresses in Safari:');
      for (const a of list) log('    ' + a);
    } else {
      log('This computer is not on a network now, so the iPad cannot reach the page.');
    }
  }
  enqueue('daily');
  setInterval(() => enqueue('daily'), cfg.checkEveryHours * 3600000);
});
