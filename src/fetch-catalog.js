'use strict';
// Milestone 2, part A, step 1. Downloads the Flibusta catalog dump files
// (<mirror>/sql/lib.<table>.sql.gz) and loads them into data/catalog.db.
//
// The dump files are MySQL text dumps. Each file is downloaded once and kept in
// data/sql/; run with --refresh to download fresh copies. A table is loaded
// again only when its dump file has changed.

const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const Database = require('better-sqlite3');
const c = require('./common');
const net = require('./net');

// Indexes that make the lookups in fill-genres.js fast.
const INDEXES = {
  libbook: ['BookId'],
  libavtor: ['BookId', 'AvtorId'],
  libavtorname: ['AvtorId'],
  libtranslator: ['BookId'],
  libgenre: ['BookId'],
  libgenrelist: ['GenreId'],
  libseq: ['BookId'],
  libseqname: ['SeqId'],
  librate: ['BookId'],
  libjoinedbooks: ['BadId'],
};

// ---------------------------------------------------------------- MySQL dump reading

// Reads the rows out of one line "INSERT INTO `t` VALUES (1,'a',NULL),(2,'b',3);"
function parseInsertLine(line, onRow) {
  let i = line.indexOf('VALUES');
  if (i === -1) return;
  i += 6;
  const n = line.length;
  const ESC = { n: '\n', r: '\r', t: '\t', 0: '\0', b: '\b', Z: '\x1a' };
  while (i < n) {
    while (i < n && line[i] !== '(') {
      if (line[i] === ';') return;
      i++;
    }
    if (i >= n) return;
    i++; // skip "("
    const row = [];
    for (;;) {
      if (line[i] === "'") {
        i++;
        let s = '';
        let start = i;
        for (;;) {
          const ch = line[i];
          if (ch === '\\') {
            s += line.slice(start, i);
            const e = line[i + 1];
            s += ESC[e] !== undefined ? ESC[e] : e;
            i += 2;
            start = i;
          } else if (ch === "'") {
            if (line[i + 1] === "'") {
              s += line.slice(start, i + 1);
              i += 2;
              start = i;
            } else {
              s += line.slice(start, i);
              i++;
              break;
            }
          } else if (ch === undefined) {
            throw new Error('unfinished string in dump line');
          } else {
            i++;
          }
        }
        row.push(s);
      } else {
        const start = i;
        while (i < n && line[i] !== ',' && line[i] !== ')') i++;
        const raw = line.slice(start, i);
        if (raw === 'NULL') row.push(null);
        else if (/^-?\d+$/.test(raw) && raw.length < 16) row.push(Number(raw));
        else if (/^-?\d*\.\d+$/.test(raw)) row.push(Number(raw));
        else row.push(raw);
      }
      if (line[i] === ',') {
        i++;
        continue;
      }
      if (line[i] === ')') {
        i++;
        break;
      }
      throw new Error('unexpected character in dump line');
    }
    onRow(row);
  }
}

// Yields the lines of a gzipped dump. Lines are split on "\n" only: book
// titles can hold other characters that some tools treat as line ends.
async function* dumpLines(file) {
  const decoder = new TextDecoder('utf-8');
  let rest = '';
  for await (const chunk of fs.createReadStream(file).pipe(zlib.createGunzip())) {
    rest += decoder.decode(chunk, { stream: true });
    let at;
    let from = 0;
    while ((at = rest.indexOf('\n', from)) !== -1) {
      yield rest.slice(from, at);
      from = at + 1;
    }
    rest = rest.slice(from);
  }
  rest += decoder.decode();
  if (rest) yield rest;
}

async function loadDump(cat, table, file) {
  const lines = dumpLines(file);
  let columns = null;
  let inCreate = false;
  let insert = null;
  let rows = 0;
  cat.exec('BEGIN');
  try {
    for await (const line of lines) {
      if (line.startsWith('CREATE TABLE')) {
        inCreate = true;
        columns = [];
        continue;
      }
      if (inCreate) {
        const m = line.match(/^\s*`([^`]+)`\s+(\w+)/);
        if (m) columns.push({ name: m[1], type: /int/i.test(m[2]) ? 'INTEGER' : /char|text|date|time|enum|set/i.test(m[2]) ? 'TEXT' : '' });
        if (line.startsWith(')')) {
          inCreate = false;
          cat.exec(`DROP TABLE IF EXISTS "${table}"`);
          cat.exec(`CREATE TABLE "${table}" (${columns.map((col) => `"${col.name}" ${col.type}`).join(', ')})`);
          insert = cat.prepare(`INSERT INTO "${table}" VALUES (${columns.map(() => '?').join(', ')})`);
        }
        continue;
      }
      if (line.startsWith('INSERT INTO')) {
        if (!insert) throw new Error(`${table}: INSERT before CREATE TABLE`);
        parseInsertLine(line, (row) => {
          if (row.length !== columns.length) throw new Error(`${table}: row has ${row.length} values, table has ${columns.length} columns`);
          insert.run(row);
          rows++;
        });
      }
    }
    if (!columns) throw new Error(`${table}: no CREATE TABLE found in the dump`);
    for (const col of INDEXES[table] || []) {
      if (columns.some((x) => x.name === col)) cat.exec(`CREATE INDEX IF NOT EXISTS "${table}_${col}" ON "${table}" ("${col}")`);
    }
    cat.exec('COMMIT');
  } catch (e) {
    cat.exec('ROLLBACK');
    throw e;
  }
  return rows;
}

// ---------------------------------------------------------------- script

function openCatalog(cfg) {
  const cat = new Database(cfg.catalogPath);
  cat.pragma('journal_mode = WAL');
  cat.exec('CREATE TABLE IF NOT EXISTS _loaded (name TEXT PRIMARY KEY, file TEXT, bytes INTEGER, mtime INTEGER, rows INTEGER, loaded_at TEXT)');
  return cat;
}

async function main() {
  const cfg = c.loadConfig();
  net.setPause(cfg.pauseSeconds);
  const refresh = process.argv.includes('--refresh');
  fs.mkdirSync(cfg.sqlDir, { recursive: true });

  console.log('Catalog: looking for dump files at <mirror>/sql/ ...');
  const missing = cfg.catalogTables.filter((t) => refresh || !fs.existsSync(path.join(cfg.sqlDir, `lib.${t}.sql.gz`)));
  if (missing.length) {
    const { mirror, value: html } = await net.onMirrors(cfg.mirrors, (m) => net.getText(m + '/sql/'));
    const offered = new Set([...html.matchAll(/lib\.(\w+)\.sql\.gz/g)].map((m) => m[1]));
    if (!offered.size) throw new Error(`${mirror}/sql/ lists no dump files. Use the fallback (book pages) instead.`);
    console.log(`  ${mirror}/sql/ offers ${offered.size} dump files`);
    for (const t of missing) {
      if (!offered.has(t)) {
        console.log(`  ${t}: not offered by the mirror, skipped`);
        continue;
      }
      const name = `lib.${t}.sql.gz`;
      const bytes = await net.download(`${mirror}/sql/${name}`, path.join(cfg.sqlDir, name), { magic: Buffer.from([0x1f, 0x8b]) });
      console.log(`  downloaded ${name} (${(bytes / 1048576).toFixed(1)} MB)`);
    }
  } else {
    console.log('  all dump files are already in data/sql/ (use --refresh to download them again)');
  }

  const cat = openCatalog(cfg);
  const seen = cat.prepare('SELECT * FROM _loaded WHERE name = ?');
  const mark = cat.prepare("INSERT OR REPLACE INTO _loaded VALUES (?, ?, ?, ?, ?, datetime('now'))");
  for (const t of cfg.catalogTables) {
    const file = path.join(cfg.sqlDir, `lib.${t}.sql.gz`);
    if (!fs.existsSync(file)) continue;
    const st = fs.statSync(file);
    const old = seen.get(t);
    if (old && old.bytes === st.size && old.mtime === Math.round(st.mtimeMs)) {
      console.log(`  ${t}: already loaded (${c.num(old.rows)} rows)`);
      continue;
    }
    const t0 = Date.now();
    const rows = await loadDump(cat, t, file);
    mark.run(t, path.basename(file), st.size, Math.round(st.mtimeMs), rows);
    console.log(`  ${t}: loaded ${c.num(rows)} rows in ${((Date.now() - t0) / 1000).toFixed(1)} s`);
  }
  cat.close();
  console.log(`Catalog is in ${path.relative(cfg.root, cfg.catalogPath)}`);
}

if (require.main === module) {
  main().catch((e) => {
    console.error(e.message || e);
    process.exit(1);
  });
}

module.exports = { main, parseInsertLine };
