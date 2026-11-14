'use strict';
// Steps 3 and 4 for EPUB. parseEpub(path) reads an EPUB (a .epub file, or a
// folder holding an unpacked EPUB) and returns { ok, lenient, reason, meta, text }.
// Run as a script, it parses every EPUB found in data/library/.

const fs = require('node:fs');
const path = require('node:path');
const { Parser } = require('htmlparser2');
const c = require('./common');

// ---------------------------------------------------------------- reading the container

function dirReader(dir) {
  const names = [];
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const full = path.join(d, e.name);
      if (e.isDirectory()) walk(full);
      else names.push(path.relative(dir, full).split(path.sep).join('/'));
    }
  };
  walk(dir);
  return { names, read: (name) => fs.readFileSync(path.join(dir, name)) };
}

async function zipReader(file) {
  const wanted = /\.(xml|opf|x?html?|ncx)$/i;
  const files = new Map();
  const zip = await c.openZip(file);
  try {
    await c.eachZipEntry(zip, async (name, entry, io) => {
      if (name.endsWith('/')) return;
      files.set(name, wanted.test(name) ? await io.read() : null);
    });
  } finally {
    zip.close();
  }
  return {
    names: [...files.keys()],
    read: (name) => {
      const b = files.get(name);
      if (!b) throw new Error('file missing in EPUB: ' + name);
      return b;
    },
  };
}

function decodeXmlBytes(buf) {
  if (buf.length >= 2 && ((buf[0] === 0xff && buf[1] === 0xfe) || (buf[0] === 0xfe && buf[1] === 0xff))) {
    return c.decodeBytes(buf.subarray(2), buf[0] === 0xff ? 'utf-16le' : 'utf-16be');
  }
  const head = buf.subarray(0, 300).toString('latin1');
  const m = head.match(/encoding\s*=\s*["']\s*([^"'\s]+)\s*["']/i);
  return c.decodeBytes(buf, m ? m[1].toLowerCase() : 'utf-8') || buf.toString('utf8');
}

function resolveHref(baseDir, href) {
  let h = String(href).split('#')[0];
  try {
    h = decodeURIComponent(h);
  } catch {
    // keep as is
  }
  return path.posix.normalize(baseDir ? baseDir + '/' + h : h);
}

// ---------------------------------------------------------------- metadata from the OPF

// Some converters escape twice ("&amp;quot;"); undo the second layer.
function unescapeAgain(s) {
  return String(s || '')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&nbsp;/g, ' ')
    .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))
    .replace(/&amp;/g, '&');
}

const clean = (s) => c.cleanSpaces(unescapeAgain(s));

function readOpfMeta(opf) {
  const md = c.findFirst(opf, 'metadata') || opf;
  const all = (name) => c.findAll(md, name);
  const text = (name) => clean(c.textOf(all(name)[0]));
  const metas = all('meta').map((m) => ({
    name: String(c.attr(m, 'name') || c.attr(m, 'property') || '').toLowerCase(),
    content: c.attr(m, 'content') != null ? c.attr(m, 'content') : c.textOf(m),
  }));
  const metaAll = (name) => metas.filter((m) => m.name === name).map((m) => clean(m.content)).filter(Boolean);
  const meta1 = (name) => metaAll(name)[0] || '';
  const uniq = (a) => [...new Set(a.filter(Boolean))];

  const authors = [];
  const translators = [...metaAll('fb2.book-info.translator')];
  for (const cr of [...all('creator'), ...all('contributor')]) {
    const role = String(c.attr(cr, 'role') || '').toLowerCase();
    const name = clean(c.textOf(cr));
    if (role === 'trl') translators.push(name);
    else if (cr.name === 'creator' && (!role || role === 'aut')) authors.push(name);
  }

  // series: FB2EPUB writes "Series name; number=3". Calibre and EPUB 3 have their own ways.
  let series = '';
  let seriesNo = null;
  const seq = meta1('fb2.book-info.sequence');
  if (seq) {
    const m = seq.match(/^(.*?)(?:;\s*number=(.*))?$/);
    series = c.cleanSpaces(m[1]);
    seriesNo = m[2] != null ? c.cleanSpaces(m[2]) : null;
  } else if (meta1('calibre:series')) {
    series = meta1('calibre:series');
    seriesNo = meta1('calibre:series_index') || null;
  } else if (meta1('belongs-to-collection')) {
    series = meta1('belongs-to-collection');
    seriesNo = meta1('group-position') || null;
  }
  if (seriesNo != null && /^\d+\.0+$/.test(seriesNo)) seriesNo = seriesNo.replace(/\.0+$/, '');

  const year = c.yearFrom(meta1('fb2.book-info.date')) || c.yearFrom(meta1('fb2.publish-info.year')) || c.yearFrom(text('date'));
  const ids = all('identifier').map((n) => clean(c.textOf(n))).filter(Boolean);
  const docId = meta1('fb2.document-info.id') || (ids[0] || '').replace(/^urn:uuid:/i, '');
  const annotation = clean(text('description').replace(/<[^>]+>/g, ' '));

  return {
    authors: uniq(authors),
    title: text('title'),
    genres: uniq([...metaAll('fb2.book-info.genre'), ...all('subject').map((n) => clean(c.textOf(n)).toLowerCase())]),
    lang: text('language'),
    src_lang: meta1('fb2.book-info.src-lang'),
    translators: uniq(translators),
    series,
    series_no: seriesNo,
    year,
    annotation,
    doc_id: docId,
  };
}

// ---------------------------------------------------------------- text from one spine document

const BLOCK = new Set(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr', 'td', 'th', 'br', 'blockquote', 'section', 'article', 'pre', 'dd', 'dt', 'hr', 'body', 'table', 'ul', 'ol']);
const SKIP = new Set(['head', 'script', 'style', 'svg', 'img', 'image', 'nav', 'aside', 'math', 'audio', 'video', 'figure']);
const NOTE_MARK = /^\s*[\[{(]?\s*(?:note\s*)?\d+\s*[\]})]?\s*$/i;
const NOTE_TYPE = /footnote|endnote|rearnote/i;
const NOTES_TITLE = /^(примечани|сноск|комментари|notes|footnotes)/i;

// Returns { lines, noteTargets, firstTitle }.
//   lines        the visible text, one entry per paragraph
//   noteTargets  files that footnote markers such as [1] link to
//   firstTitle   the first heading in the file
function readSpineDoc(html, baseDir) {
  const lines = [];
  const noteTargets = new Set();
  let cur = '';
  let skip = 0; // depth inside a skipped element
  let link = null; // { text, href, depth, noteref } while inside <a>
  let titleDepth = 0;
  let titleText = '';
  let firstTitle = null;
  const flush = () => {
    if (cur.trim()) lines.push(cur);
    cur = '';
  };
  const isTitle = (name, attrs) => /^h[1-6]$/.test(name) || /^title\d*$/.test(String(attrs.class || ''));

  const parser = new Parser(
    {
      onopentag(name, attrs) {
        if (skip) {
          skip++;
          return;
        }
        const type = String(attrs['epub:type'] || attrs.role || '');
        if (SKIP.has(name) || (NOTE_TYPE.test(type) && name !== 'a')) {
          skip = 1;
          return;
        }
        if (link) {
          link.depth++;
          return;
        }
        if (titleDepth) titleDepth++;
        if (name === 'a') {
          link = { text: '', href: attrs.href || '', depth: 1, noteref: /noteref/i.test(type) };
          return;
        }
        if (BLOCK.has(name)) {
          flush();
          if (String(attrs.class || '') === 'empty-line') lines.push('');
          if (!titleDepth && isTitle(name, attrs)) {
            titleDepth = 1;
            titleText = '';
            lines.push('');
          }
        }
      },
      ontext(t) {
        if (skip) return;
        if (link) link.text += t;
        else {
          cur += t;
          if (titleDepth) titleText += t;
        }
      },
      onclosetag(name) {
        if (skip) {
          skip--;
          return;
        }
        if (link) {
          link.depth--;
          if (link.depth > 0) return;
          const isNote = link.noteref || (link.href && NOTE_MARK.test(link.text));
          if (isNote) {
            const file = link.href.split('#')[0];
            if (file) noteTargets.add(resolveHref(baseDir, file));
          } else {
            cur += link.text;
            if (titleDepth) titleText += link.text;
          }
          link = null;
          return;
        }
        if (BLOCK.has(name)) flush();
        if (titleDepth) {
          titleDepth--;
          if (titleDepth === 0) {
            if (firstTitle == null) firstTitle = c.cleanSpaces(titleText);
            lines.push('');
          }
        }
      },
    },
    { xmlMode: false, decodeEntities: true, recognizeSelfClosing: true }
  );
  parser.write(html);
  parser.end();
  flush();
  return { lines, noteTargets, firstTitle };
}

// FB2EPUB names chapter files ch1.xhtml, ch1-1.xhtml, ch1-2.xhtml, ch2.xhtml ...
// The first number is the FB2 "body": body 1 is the book, later bodies are
// usually footnotes. Files with the same first number form one group.
function groupOf(file) {
  const m = path.posix.basename(file).match(/^ch(\d+)(?:-\d+)?\.x?html?$/i);
  return m ? path.posix.dirname(file) + '/ch' + m[1] : file;
}

// ---------------------------------------------------------------- one book

async function parseEpub(fullPath) {
  let reader;
  try {
    reader = fs.statSync(fullPath).isDirectory() ? dirReader(fullPath) : await zipReader(fullPath);
  } catch (e) {
    return { ok: false, reason: 'cannot open EPUB: ' + e.message };
  }
  const has = new Set(reader.names);
  let lenient = false;
  const notes = [];
  const xml = (name) => {
    const r = c.parseXml(decodeXmlBytes(reader.read(name)).replace(/^[^<]*/, ''));
    if (r.lenient) {
      lenient = true;
      notes.push(`${name}: ${r.strictError}`);
    }
    return r.root;
  };

  // 1. find the OPF file
  let opfPath = null;
  if (has.has('META-INF/container.xml')) {
    try {
      const rf = c.findFirst(xml('META-INF/container.xml'), 'rootfile');
      if (rf && c.attr(rf, 'full-path')) opfPath = resolveHref('', c.attr(rf, 'full-path'));
    } catch (e) {
      notes.push('container.xml unreadable: ' + e.message);
    }
  }
  if (!opfPath || !has.has(opfPath)) opfPath = reader.names.filter((n) => /\.opf$/i.test(n)).sort()[0];
  if (!opfPath) return { ok: false, reason: 'no OPF file in EPUB' };

  // 2. metadata, manifest, spine
  let opf;
  try {
    opf = xml(opfPath);
  } catch (e) {
    return { ok: false, reason: 'cannot read OPF: ' + e.message };
  }
  const meta = readOpfMeta(opf);
  const baseDir = path.posix.dirname(opfPath) === '.' ? '' : path.posix.dirname(opfPath);
  const manifest = new Map();
  for (const it of c.findAll(opf, 'item')) {
    if (c.attr(it, 'id') && c.attr(it, 'href')) manifest.set(c.attr(it, 'id'), { href: resolveHref(baseDir, c.attr(it, 'href')), type: String(c.attr(it, 'media-type') || '') });
  }
  let spine = c.findAll(opf, 'itemref')
    .map((r) => manifest.get(c.attr(r, 'idref')))
    .filter((it) => it && (/html/i.test(it.type) || /\.x?html?$/i.test(it.href)))
    .map((it) => it.href);
  if (!spine.length) spine = reader.names.filter((n) => /\.x?html?$/i.test(n)).sort(); // no usable spine: take every page
  spine = [...new Set(spine)];

  // 3. text of every spine document
  const docs = [];
  const missing = [];
  for (const file of spine) {
    if (!has.has(file)) {
      missing.push(file);
      continue;
    }
    const html = decodeXmlBytes(reader.read(file));
    docs.push({ file, group: groupOf(file), ...readSpineDoc(html, path.posix.dirname(file) === '.' ? '' : path.posix.dirname(file)) });
  }
  if (!docs.length) return { ok: false, meta, reason: missing.length ? `spine files missing: ${missing.slice(0, 3).join(', ')}` : 'no spine documents' };

  // 4. drop footnote bodies: groups that footnote markers point to, or that are titled "Примечания"
  const firstGroup = docs[0].group;
  const noteGroups = new Set();
  for (const d of docs) {
    for (const target of d.noteTargets) {
      const g = groupOf(target);
      if (g !== d.group && g !== firstGroup) noteGroups.add(g);
    }
  }
  const seenGroup = new Set();
  for (const d of docs) {
    if (seenGroup.has(d.group)) continue;
    seenGroup.add(d.group);
    if (d.group !== firstGroup && d.firstTitle && NOTES_TITLE.test(d.firstTitle)) noteGroups.add(d.group);
  }
  const lines = [];
  for (const d of docs) {
    if (noteGroups.has(d.group)) continue;
    lines.push(...d.lines, '');
  }
  const text = c.tidyText(lines);
  if (!text.trim()) return { ok: false, meta, reason: 'no body text found' };
  if (!meta.title) return { ok: false, meta, reason: 'no title in OPF metadata' };
  if (missing.length) notes.push(`spine files missing: ${missing.slice(0, 3).join(', ')}`);
  return { ok: true, lenient, reason: notes.length ? notes.join('; ') : null, meta, text };
}

// ---------------------------------------------------------------- script

async function main() {
  const cfg = c.loadConfig();
  // without the unpacked library every liked book would look "gone" and its text would be deleted
  if (!fs.existsSync(cfg.libraryDir)) throw new Error('data/library is not there (it was cleaned up). Nothing was changed. Put Archive.zip back in data/ and run "npm run unzip" first.');
  const db = c.openDb(cfg);
  const units = c.listBookUnits(cfg.libraryDir).filter((u) => u.format === 'epub');
  if (!units.length) {
    c.pruneParsed(db, 'archive', cfg.textDir, [], (r) => r.format === 'epub');
    console.log('EPUB: no EPUB files in the archive, nothing to parse.');
    db.close();
    return;
  }
  const store = c.makeIngester(db, 'archive', cfg.textDir);
  const counts = { ok: 0, ok_lenient: 0, failed: 0 };
  const results = [];
  for (const u of units) {
    const t0 = performance.now();
    let result;
    try {
      result = await parseEpub(u.full);
    } catch (e) {
      result = { ok: false, reason: 'parser crashed: ' + e.message };
    }
    results.push({ u, result, ms: performance.now() - t0 });
  }
  db.transaction(() => {
    for (const { u, result, ms } of results) {
      const r = store(u.rel, 'epub', result, ms);
      counts[r.status]++;
      if (r.status === 'failed') console.log(`  FAILED ${u.rel}: ${r.reason}`);
    }
    c.pruneParsed(db, 'archive', cfg.textDir, units.map((u) => u.rel), (r) => r.format === 'epub');
  })();
  console.log(`EPUB: ${units.length} books, ${counts.ok} ok, ${counts.ok_lenient} ok with lenient parser, ${counts.failed} failed`);
  db.close();
}

if (require.main === module) {
  main().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}

module.exports = { parseEpub, main };
