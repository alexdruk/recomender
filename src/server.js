'use strict';
// The recommendations page:  npm start   ->  http://localhost:3000
//
// Shows the feed books ranked by the model, takes Like / Dislike, retrains the
// model after every 10 presses, and runs the daily loop at start and then
// every 6 hours (config.json "checkEveryHours"). Everything is local.

const path = require('node:path');
const { spawn } = require('node:child_process');
const express = require('express');
const c = require('./common');
const { ensureTables } = require('./daily');

const cfg = c.loadConfig();
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

function state() {
  const top = topPercent();
  const map = JSON.parse(require('node:fs').readFileSync(cfg.genreMapPath, 'utf8'));
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
      link: `${cfg.mirrors[0]}/b/${String(r.key).split('-')[0]}`,
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

// ---------------------------------------------------------------- the web server

const app = express();
app.use(express.json());

app.get('/', (req, res) => res.sendFile(path.join(__dirname, 'page.html')));
app.get('/api/state', (req, res) => res.json(state()));

app.post('/api/answer', (req, res) => {
  const { key, answer } = req.body || {};
  if (!['like', 'dislike'].includes(answer) || !db.prepare('SELECT 1 FROM feed_books WHERE key = ?').get(String(key))) {
    return res.status(400).json({ error: 'unknown book or answer' });
  }
  db.prepare('INSERT INTO answers (key, answer, time) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET answer = excluded.answer, time = excluded.time')
    .run(String(key), answer, new Date().toISOString());
  let since = (c.getMeta(db, 'answers_since_train') || 0) + 1;
  let retrain = false;
  if (since >= cfg.retrainEveryAnswers) {
    since = 0;
    retrain = true;
    enqueue('train');
  }
  c.setMeta(db, 'answers_since_train', since);
  log(`${answer} for book ${key}${retrain ? ' - retraining the model' : ''}`);
  res.json({ ok: true, retrain, sinceTraining: since });
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

app.listen(cfg.port, '127.0.0.1', () => {
  log(`Recommendations page: http://localhost:${cfg.port}`);
  enqueue('daily');
  setInterval(() => enqueue('daily'), cfg.checkEveryHours * 3600000);
});
