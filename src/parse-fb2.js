'use strict';
// Steps 2 and 4 for FB2 files. parseFb2(buffer) turns one FB2 file into
// { ok, lenient, reason, meta, text }. Run as a script, it parses every .fb2
// file found in data/library/ and stores the result.

const fs = require('node:fs');
const c = require('./common');

const SKIP_TEXT = new Set(['binary']);

// ---------------------------------------------------------------- encoding

// The encoding is named in the first line: <?xml version="1.0" encoding="windows-1251"?>
function decodeFb2(buf) {
  if (buf.length >= 3 && buf[0] === 0xef && buf[1] === 0xbb && buf[2] === 0xbf) return { xml: buf.subarray(3).toString('utf8'), encoding: 'utf-8' };
  if (buf.length >= 2 && buf[0] === 0xff && buf[1] === 0xfe) return { xml: c.decodeBytes(buf.subarray(2), 'utf-16le'), encoding: 'utf-16le' };
  if (buf.length >= 2 && buf[0] === 0xfe && buf[1] === 0xff) return { xml: c.decodeBytes(buf.subarray(2), 'utf-16be'), encoding: 'utf-16be' };

  const head = buf.subarray(0, 400).toString('latin1');
  const m = head.match(/encoding\s*=\s*["']\s*([^"'\s]+)\s*["']/i);
  const declared = m ? m[1].toLowerCase() : 'utf-8';
  let xml = c.decodeBytes(buf, declared);
  let encoding = declared;
  if (xml == null) {
    // unknown encoding name: guess
    xml = null;
    try {
      xml = new TextDecoder('utf-8', { fatal: true }).decode(buf);
      encoding = 'utf-8 (guessed)';
    } catch {
      xml = c.decodeBytes(buf, 'windows-1251');
      encoding = 'windows-1251 (guessed)';
    }
  } else if (/^utf-?8$/.test(declared)) {
    // says UTF-8 but is not: most likely windows-1251 with a wrong header
    let bad = 0;
    for (let i = xml.indexOf('�'); i !== -1 && bad <= xml.length / 50; i = xml.indexOf('�', i + 1)) bad++;
    if (bad > xml.length / 50) {
      xml = c.decodeBytes(buf, 'windows-1251');
      encoding = 'windows-1251 (header said utf-8)';
    }
  }
  return { xml, encoding };
}

// ---------------------------------------------------------------- metadata

function personName(node) {
  const part = (n) => c.cleanSpaces(c.textOf(c.childrenNamed(node, n)[0]));
  const full = [part('first-name'), part('middle-name'), part('last-name')].filter(Boolean).join(' ');
  return full || part('nickname') || c.cleanSpaces(c.textOf(node));
}

function blockText(node) {
  // text of an element with paragraphs kept apart by spaces
  const parts = [];
  const walk = (n) => {
    for (const ch of n.children) {
      if (typeof ch === 'string') parts.push(ch);
      else {
        walk(ch);
        parts.push(' ');
      }
    }
  };
  if (node) walk(node);
  return c.cleanSpaces(parts.join(''));
}

function readMeta(root) {
  const desc = c.findFirst(root, 'description');
  const ti = desc && c.findFirst(desc, 'title-info');
  if (!ti) return null;
  const di = c.findFirst(desc, 'document-info');
  const first = (name) => c.childrenNamed(ti, name)[0];
  const seq = first('sequence');
  const dateNode = first('date');
  const uniq = (a) => [...new Set(a.filter(Boolean))];
  return {
    authors: uniq(c.childrenNamed(ti, 'author').map(personName)),
    title: c.cleanSpaces(c.textOf(first('book-title'))),
    genres: uniq(c.childrenNamed(ti, 'genre').map((g) => c.cleanSpaces(c.textOf(g)).toLowerCase())),
    lang: c.cleanSpaces(c.textOf(first('lang'))),
    src_lang: c.cleanSpaces(c.textOf(first('src-lang'))),
    translators: uniq(c.childrenNamed(ti, 'translator').map(personName)),
    series: seq ? c.cleanSpaces(c.attr(seq, 'name')) : '',
    series_no: seq ? c.attr(seq, 'number') : null,
    year: dateNode ? c.yearFrom(c.attr(dateNode, 'value')) || c.yearFrom(c.textOf(dateNode)) : null,
    annotation: blockText(first('annotation')),
    doc_id: di ? c.cleanSpaces(c.textOf(c.childrenNamed(di, 'id')[0])) : '',
  };
}

// ---------------------------------------------------------------- body text

const PARAGRAPH = new Set(['p', 'v', 'subtitle', 'text-author', 'td', 'th']);
const DROP = new Set(['image', 'binary']);
const SPACED = new Set(['title', 'section', 'epigraph', 'poem', 'stanza', 'cite', 'annotation', 'table']);
const NOTES_NAME = /note|comment|footnote/i;
const NOTES_TITLE = /^(примечани|сноск|комментари|notes|footnotes)/i;

function inlineText(node) {
  let s = '';
  for (const ch of node.children) {
    if (typeof ch === 'string') s += ch;
    else if (DROP.has(ch.name)) continue;
    else if (ch.name === 'a' && String(c.attr(ch, 'type') || '').toLowerCase() === 'note') continue; // the [1] marker
    else s += inlineText(ch);
  }
  return s;
}

function isNotesBody(body, index) {
  if (NOTES_NAME.test(String(c.attr(body, 'name') || ''))) return true;
  if (index === 0) return false;
  const title = c.childrenNamed(body, 'title')[0];
  return title ? NOTES_TITLE.test(c.cleanSpaces(c.textOf(title))) : false;
}

function readBodyText(root) {
  const fb = c.findFirst(root, 'fictionbook') || root;
  let bodies = c.childrenNamed(fb, 'body');
  if (!bodies.length) bodies = c.findAll(root, 'body'); // broken nesting in lenient mode
  const lines = [];
  const walk = (node) => {
    for (const ch of node.children) {
      if (typeof ch === 'string') {
        if (ch.trim()) lines.push(ch); // stray text outside a paragraph (broken files)
      } else if (DROP.has(ch.name)) {
        continue;
      } else if (PARAGRAPH.has(ch.name)) {
        lines.push(inlineText(ch));
      } else if (ch.name === 'empty-line') {
        lines.push('');
      } else {
        const spaced = SPACED.has(ch.name);
        if (spaced) lines.push('');
        walk(ch);
        if (spaced) lines.push('');
      }
    }
  };
  bodies.forEach((b, i) => {
    if (isNotesBody(b, i)) return;
    walk(b);
    lines.push('');
  });
  return c.tidyText(lines);
}

// ---------------------------------------------------------------- one file

function parseFb2(buf) {
  let decoded;
  try {
    decoded = decodeFb2(buf);
  } catch (e) {
    return { ok: false, reason: 'cannot decode text: ' + e.message };
  }
  let xml = decoded.xml || '';
  const start = xml.indexOf('<');
  if (start === -1) return { ok: false, reason: 'not XML: no tags in file' };
  if (start > 0) xml = xml.slice(start);

  let parsed;
  try {
    parsed = c.parseXml(xml, SKIP_TEXT);
  } catch (e) {
    return { ok: false, reason: 'XML parser crashed: ' + e.message };
  }
  const { root, lenient, strictError } = parsed;
  const why = lenient ? ` (strict XML error: ${strictError})` : '';
  const top = root.children.find((n) => typeof n !== 'string');
  if (!c.findFirst(root, 'fictionbook')) {
    return { ok: false, reason: `not FB2: top element is <${top ? top.name : 'nothing'}>` + why };
  }
  const meta = readMeta(root);
  if (!meta) return { ok: false, reason: 'no title-info block found' + why };
  const text = readBodyText(root);
  if (!text.trim()) return { ok: false, meta, reason: 'no body text found' + why };
  return { ok: true, lenient, reason: lenient ? 'strict XML error: ' + strictError : null, meta, text, encoding: decoded.encoding };
}

// ---------------------------------------------------------------- script

async function main() {
  const cfg = c.loadConfig();
  const db = c.openDb(cfg);
  const units = c.listBookUnits(cfg.libraryDir);
  const fb2 = units.filter((u) => u.format === 'fb2');
  const other = units.filter((u) => u.unsupported);
  const store = c.makeIngester(db, 'archive', cfg.textDir);
  const counts = { ok: 0, ok_lenient: 0, failed: 0 };

  db.transaction(() => {
    for (const u of fb2) {
      const t0 = performance.now();
      let result;
      try {
        result = parseFb2(fs.readFileSync(u.full));
      } catch (e) {
        result = { ok: false, reason: 'parser crashed: ' + e.message };
      }
      const r = store(u.rel, 'fb2', result, performance.now() - t0);
      counts[r.status]++;
      if (r.status === 'failed') console.log(`  FAILED ${u.rel}: ${r.reason}`);
    }
    // files that are neither FB2 nor EPUB are listed as failures with a reason
    for (const u of other) {
      store(u.rel, u.format, { ok: false, reason: `unsupported format .${u.format}` }, null);
      counts.failed++;
      console.log(`  FAILED ${u.rel}: unsupported format .${u.format}`);
    }
    c.pruneParsed(db, 'archive', cfg.textDir, [...fb2, ...other].map((u) => u.rel), (r) => r.format !== 'epub');
  })();

  if (!fb2.length) console.log('FB2: no .fb2 files in the archive, nothing to parse.');
  else console.log(`FB2: ${fb2.length} files, ${counts.ok} ok, ${counts.ok_lenient} ok with lenient parser, ${counts.failed} failed`);
  db.close();
}

if (require.main === module) {
  main().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}

module.exports = { parseFb2, decodeFb2, main };
