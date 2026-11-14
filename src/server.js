'use strict';
// The recommendations page:  npm start   ->  http://localhost:3000
//
// Shows the feed books ranked by the model, takes Like / Dislike, retrains the
// model after every 10 presses, and runs the daily loop at start and then
// every 6 hours (config.json "checkEveryHours"). Everything is local.
//
// The page is also reachable from the home network (an old iPad), and
// /epub/<id> hands a book to the iPad as an EPUB file for iBooks.

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, execFileSync } = require('node:child_process');
const express = require('express');
const c = require('./common');
const net = require('./net');
const { ensureTables } = require('./daily');

const cfg = c.loadConfig();
net.setPause(cfg.pauseSeconds);
const db = c.openDb(cfg);
db.pragma('busy_timeout = 60000');
ensureTables(db);

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
    SELECT s.key, s.score, s.percentile, s.closest, s.series_note, s.added_at,
           f.authors, f.title, f.series, f.series_no, f.genres, f.words, f.annotation, f.is_translation
    FROM feed_scores s JOIN feed_books f ON f.key = s.key
    WHERE s.passed = 1 AND s.score IS NOT NULL AND s.key NOT IN (SELECT key FROM answers)
    ORDER BY s.score DESC`).all();
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
      closest: c.jsonList(r.closest),
      link: `${cfg.mirrors[0]}/b/${bookId(r.key)}`,
    };
  };
  const counts = db.prepare('SELECT COUNT(*) AS analysed, SUM(passed) AS passed FROM feed_scores').get();
  const answers = db.prepare("SELECT COUNT(*) AS n, SUM(answer = 'like') AS likes FROM answers").get();
  const hasFiles = db.prepare("SELECT 1 FROM sqlite_master WHERE name = 'feed_files'").get();
  const lastFile = hasFiles ? db.prepare('SELECT MAX(processed_at) AS t FROM feed_files').get().t : null;
  return {
    top,
    restDays: cfg.restDays,
    retrainEvery: cfg.retrainEveryAnswers,
    recommended: rows.filter((r) => r.percentile <= top).map(card),
    rest: rows.filter((r) => r.percentile > top && r.added_at >= since).map(card),
    status: {
      lastDownload: lastFile ? lastFile.replace(' ', 'T') + 'Z' : null,
      lastCheck: c.getMeta(db, 'daily_last_run') || null,
      analysed: counts.analysed,
      passed: counts.passed || 0,
      answers: answers.n,
      likes: answers.likes || 0,
      sinceTraining: c.getMeta(db, 'answers_since_train') || 0,
      model: c.getMeta(db, 'model_info') || null,
    },
    gaps: c.getMeta(db, 'feed_gaps') || [],
    busy: running ? JOBS[running].what : null,
    waiting: queue.map((n) => JOBS[n].what),
  };
}

// The books I liked on the page, newest first.
function liked() {
  const rows = db.prepare(`
    SELECT a.key, a.time, f.authors, f.title, f.series, f.series_no
    FROM answers a JOIN feed_books f ON f.key = a.key
    WHERE a.answer = 'like' ORDER BY a.time DESC`).all();
  return rows.map((r) => ({
    key: r.key,
    id: bookId(r.key),
    time: r.time,
    authors: c.jsonList(r.authors).join(', '),
    title: r.title,
    series: r.series,
    seriesNo: r.series_no,
    link: `${cfg.mirrors[0]}/b/${bookId(r.key)}`,
    cached: fs.existsSync(epubFile(bookId(r.key))),
  }));
}

// Stores a Like or Dislike. Pressing the same answer again changes nothing,
// so a book is never counted twice towards the next retraining.
function recordAnswer(key, answer) {
  const old = db.prepare('SELECT answer FROM answers WHERE key = ?').get(String(key));
  let since = c.getMeta(db, 'answers_since_train') || 0;
  if (old && old.answer === answer) return { retrain: false, sinceTraining: since };
  db.prepare('INSERT INTO answers (key, answer, time) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET answer = excluded.answer, time = excluded.time')
    .run(String(key), answer, new Date().toISOString());
  since += 1;
  let retrain = false;
  if (since >= cfg.retrainEveryAnswers) {
    since = 0;
    retrain = true;
    enqueue('train');
  }
  c.setMeta(db, 'answers_since_train', since);
  log(`${answer} for book ${key}${retrain ? ' - retraining the model' : ''}`);
  return { retrain, sinceTraining: since };
}

// ---------------------------------------------------------------- EPUB files for iBooks

const epubFile = (id) => path.join(cfg.epubDir, id + '.epub');
const fetching = new Map(); // id -> the download that is running for it

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
${retryUrl ? `<a href="${escHtml(retryUrl)}">Try again</a>` : ''}<a href="/liked">Liked books</a><a href="/">Recommendations</a>
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
app.get('/liked', page);
app.get('/api/state', (req, res) => res.json(state()));
app.get('/api/liked', (req, res) => res.json({ books: liked() }));

app.post('/api/answer', (req, res) => {
  const { key, answer } = req.body || {};
  if (!['like', 'dislike'].includes(answer) || !db.prepare('SELECT 1 FROM feed_books WHERE key = ?').get(String(key))) {
    return res.status(400).json({ error: 'unknown book or answer' });
  }
  res.json({ ok: true, ...recordAnswer(key, answer) });
});

// /epub/<id> records a Like, gets the EPUB (from the cache or the mirror) and
// then moves the browser to /epub/<id>/<Author - Title>.epub, which sends the
// file. The readable name is in the address because old Safari names the
// file after the address.
app.get('/epub/:id{/:name}', async (req, res) => {
  res.set('Cache-Control', 'no-store');
  const id = req.params.id;
  const book = /^\d+$/.test(id) ? db.prepare('SELECT key, authors, title FROM feed_books WHERE key = ?').get(id) : null;
  if (!book) {
    return res.status(404).send(errorPage('Unknown book', [`There is no book with the number ${id} among the books of the daily updates.`]));
  }
  recordAnswer(book.key, 'like'); // a book sent to iBooks is a liked book
  const what = `${c.jsonList(book.authors).join(', ') || 'Author not given'} — ${book.title}`;
  let file;
  try {
    file = await ensureEpub(id);
  } catch (e) {
    log(`EPUB of book ${id} failed: ${e.message.replace(/\n/g, ' | ')}`);
    const lines = e.code === 'BUSY'
      ? [what, 'The computer is downloading new update files from the library right now, and it only makes one request at a time. Please try again in a few minutes.']
      : [what, 'Both library mirrors were asked for the EPUB file and neither gave it:', ...e.message.split('\n').slice(1), 'The library may be down, or it may have no EPUB for this book. Please try again later.'];
    lines.push('The Like is saved. The book is on the "Liked" page, so you can send it to iBooks later.');
    return res.status(e.code === 'BUSY' ? 503 : 502).send(errorPage('Could not get the book', lines, `/epub/${id}`));
  }
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
