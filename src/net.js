'use strict';
// Polite network access to the Flibusta mirrors. Every request in the project
// goes through here, so the rules hold everywhere:
//   - one request at a time, also between programs: the server and the daily
//     loop are separate programs, so they take turns through a small lock
//     file in the data folder (net.lock)
//   - a pause (config.json "pauseSeconds", never less than 3) between requests
//   - mirrors are tried in the order given in config.json

const fs = require('node:fs');
const path = require('node:path');
const { Readable } = require('node:stream');
const { pipeline } = require('node:stream/promises');

const UA = 'book-feed-probe/1.0';

let pauseMs = 3000;
let lastEnd = 0; // when the previous request finished
let chain = Promise.resolve(); // makes requests wait for each other

function setPause(seconds) {
  pauseMs = Math.max(3, Number(seconds) || 3) * 1000;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// The lock file holds the id of the program that is talking to the mirrors
// right now. The stamp file's time is when the last request of any program ended.
let lockFile = null;
let stampFile = null;
function lockPaths() {
  if (!lockFile) {
    const dataDir = require('./common').loadConfig().dataDir;
    fs.mkdirSync(dataDir, { recursive: true });
    lockFile = path.join(dataDir, 'net.lock');
    stampFile = path.join(dataDir, 'net.last');
  }
}

function tryLock() {
  try {
    fs.writeFileSync(lockFile, String(process.pid), { flag: 'wx' });
    return true;
  } catch (e) {
    if (e.code !== 'EEXIST') throw e;
  }
  // somebody holds the lock; if that program is gone, the lock is left over
  let pid = 0;
  try {
    pid = Number(fs.readFileSync(lockFile, 'utf8'));
  } catch (e) {
    return false;
  }
  let alive = true;
  try {
    process.kill(pid, 0);
  } catch (e) {
    alive = e.code === 'EPERM';
  }
  if (!alive || pid === process.pid) fs.rmSync(lockFile, { force: true });
  return false;
}

function busyError() {
  const e = new Error('another download from the mirrors is still running');
  e.code = 'BUSY';
  return e;
}

// Runs fn() after the previous request has finished and the pause has passed.
// "maxWaitMs" (optional): give up with the error code BUSY when the turn does
// not come within this time, for example while a big update file is downloading.
function inTurn(fn, { maxWaitMs = 0 } = {}) {
  const asked = Date.now();
  const run = async () => {
    lockPaths();
    while (!tryLock()) {
      if (maxWaitMs && Date.now() - asked > maxWaitMs) throw busyError();
      await sleep(500);
    }
    try {
      let last = lastEnd;
      try {
        last = Math.max(last, fs.statSync(stampFile).mtimeMs);
      } catch (e) { /* no request was made yet */ }
      const wait = last + pauseMs - Date.now();
      if (last && wait > 0) await sleep(wait);
      return await fn();
    } finally {
      lastEnd = Date.now();
      try {
        fs.writeFileSync(stampFile, '');
      } catch (e) { /* the pause inside this program still holds */ }
      fs.rmSync(lockFile, { force: true });
    }
  };
  const p = chain.then(run, run);
  chain = p.catch(() => {});
  return p;
}

function describe(e) {
  const cause = e.cause && e.cause.code ? ` (${e.cause.code})` : '';
  return `${e.message}${cause}`;
}

function getText(url, timeoutMs = 60000) {
  return inTurn(async () => {
    const res = await fetch(url, { headers: { 'user-agent': UA }, signal: AbortSignal.timeout(timeoutMs) });
    if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
    return res.text();
  });
}

// Downloads url into the file "target". Writes to target + ".part" first, so a
// broken download never looks like a finished one. "magic" is the first bytes
// the file must start with (for example "PK" for a zip).
function download(url, target, { timeoutMs = 1800000, magic = null, maxWaitMs = 0 } = {}) {
  return inTurn(async () => {
    const res = await fetch(url, { headers: { 'user-agent': UA }, signal: AbortSignal.timeout(timeoutMs) });
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status} for ${url}`);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    const tmp = target + '.part';
    try {
      await pipeline(Readable.fromWeb(res.body), fs.createWriteStream(tmp));
      if (magic) {
        const want = Buffer.from(magic);
        const head = Buffer.alloc(want.length);
        const fd = fs.openSync(tmp, 'r');
        fs.readSync(fd, head, 0, want.length, 0);
        fs.closeSync(fd);
        if (!head.equals(want)) throw new Error(`downloaded file is not the expected type: ${url}`);
      }
      fs.renameSync(tmp, target);
      return fs.statSync(target).size;
    } catch (e) {
      fs.rmSync(tmp, { force: true });
      throw e;
    }
  }, { maxWaitMs });
}

// Tries job(mirror) on each mirror in turn; returns { mirror, value }.
async function onMirrors(mirrors, job, log = console.log) {
  const errors = [];
  for (const mirror of mirrors) {
    try {
      return { mirror, value: await job(mirror), errors };
    } catch (e) {
      if (e.code === 'BUSY') throw e; // waiting for our turn; another mirror would not help
      errors.push(`${mirror}: ${describe(e)}`);
      log(`  mirror failed - ${mirror}: ${describe(e)}`);
    }
  }
  throw new Error('All mirrors failed.\n' + errors.join('\n'));
}

module.exports = { setPause, getText, download, onMirrors, describe, UA };
