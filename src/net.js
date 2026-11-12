'use strict';
// Polite network access to the Flibusta mirrors. Every request in the project
// goes through here, so the rules hold everywhere:
//   - one request at a time
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

// Runs fn() after the previous request has finished and the pause has passed.
function inTurn(fn) {
  const run = async () => {
    const wait = lastEnd + pauseMs - Date.now();
    if (lastEnd && wait > 0) await new Promise((r) => setTimeout(r, wait));
    try {
      return await fn();
    } finally {
      lastEnd = Date.now();
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
function download(url, target, { timeoutMs = 1800000, magic = null } = {}) {
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
  });
}

// Tries job(mirror) on each mirror in turn; returns { mirror, value }.
async function onMirrors(mirrors, job, log = console.log) {
  const errors = [];
  for (const mirror of mirrors) {
    try {
      return { mirror, value: await job(mirror), errors };
    } catch (e) {
      errors.push(`${mirror}: ${describe(e)}`);
      log(`  mirror failed - ${mirror}: ${describe(e)}`);
    }
  }
  throw new Error('All mirrors failed.\n' + errors.join('\n'));
}

module.exports = { setPause, getText, download, onMirrors, describe, UA };
