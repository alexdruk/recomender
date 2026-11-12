'use strict';
// Step 1. Unpack data/Archive.zip into data/library/, unpack any archives found
// inside it (.zip, .fb2.zip), and record every file with its extension in the
// "files" table. Safe to run again: files already unpacked are skipped and the
// table is rebuilt from what is on disk.

const fs = require('node:fs');
const path = require('node:path');
const { pipeline } = require('node:stream/promises');
const c = require('./common');

async function extractZip(zipFile, destDir) {
  const stats = { written: 0, skipped: 0, unsafe: 0 };
  const zip = await c.openZip(zipFile);
  try {
    await c.eachZipEntry(zip, async (name, entry, io) => {
      const rel = c.safeRelPath(name);
      if (!rel) {
        if (rel === null) stats.unsafe++;
        return;
      }
      const target = path.join(destDir, rel);
      if (name.endsWith('/')) {
        fs.mkdirSync(target, { recursive: true });
        return;
      }
      const st = fs.statSync(target, { throwIfNoEntry: false });
      if (st && st.isFile() && st.size === entry.uncompressedSize) {
        stats.skipped++;
        return;
      }
      fs.mkdirSync(path.dirname(target), { recursive: true });
      const tmp = target + '.part';
      await pipeline(await io.stream(), fs.createWriteStream(tmp));
      fs.renameSync(tmp, target);
      stats.written++;
    });
  } finally {
    zip.close();
  }
  return stats;
}

function walkFiles(rootDir) {
  const out = [];
  const walk = (dir) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, e.name);
      if (e.isDirectory()) walk(full);
      else if (e.isFile()) out.push(full);
    }
  };
  walk(rootDir);
  return out;
}

// Archives inside the archive (book.fb2.zip, bundle.zip) are unpacked into a
// folder next to them named "<archive name>.unpacked". EPUB files are zips too,
// but they are books, so they are left alone.
async function extractNested(libraryDir) {
  let found = 0;
  const done = new Set();
  for (let round = 0; round < 5; round++) {
    const zips = walkFiles(libraryDir).filter((f) => /\.zip$/i.test(f) && !done.has(f) && !c.isJunk(path.relative(libraryDir, f)));
    if (!zips.length) break;
    for (const z of zips) {
      done.add(z);
      found++;
      try {
        await extractZip(z, z + '.unpacked');
      } catch (e) {
        console.log(`  could not unpack nested archive ${path.relative(libraryDir, z)}: ${e.message}`);
      }
    }
  }
  return found;
}

function recordFiles(db, libraryDir) {
  const rows = walkFiles(libraryDir).map((full) => {
    const rel = path.relative(libraryDir, full).split(path.sep).join('/');
    const ext = c.extOf(rel);
    const kind = c.isJunk(rel) ? 'junk' : /\.zip$/i.test(rel) ? 'nested-archive' : 'file';
    return { path: rel, ext, size: fs.statSync(full).size, kind };
  });
  const insert = db.prepare('INSERT INTO files (path, ext, size, kind) VALUES (@path, @ext, @size, @kind)');
  db.transaction(() => {
    db.prepare('DELETE FROM files').run();
    for (const r of rows) insert.run(r);
  })();
  return rows;
}

async function main() {
  const cfg = c.loadConfig();
  if (!fs.existsSync(cfg.archiveZip)) throw new Error(`Archive not found: ${cfg.archiveZip}`);
  fs.mkdirSync(cfg.libraryDir, { recursive: true });
  const db = c.openDb(cfg);

  console.log(`Unpacking ${path.relative(cfg.root, cfg.archiveZip)} ...`);
  const stats = await extractZip(cfg.archiveZip, cfg.libraryDir);
  console.log(`  ${stats.written} files written, ${stats.skipped} already there` + (stats.unsafe ? `, ${stats.unsafe} unsafe names skipped` : ''));

  const nested = await extractNested(cfg.libraryDir);
  console.log(`  ${nested} nested archives unpacked`);

  const rows = recordFiles(db, cfg.libraryDir);
  const units = c.listBookUnits(cfg.libraryDir);
  c.setMeta(db, 'archive_zip_bytes', fs.statSync(cfg.archiveZip).size);
  c.setMeta(db, 'archive_nested_archives', nested);

  const byExt = new Map();
  for (const r of rows) if (r.kind !== 'junk') byExt.set(r.ext, (byExt.get(r.ext) || 0) + 1);
  console.log(`  ${rows.length} files recorded (${rows.filter((r) => r.kind === 'junk').length} are macOS junk)`);
  console.log('  by extension: ' + [...byExt].sort((a, b) => b[1] - a[1]).map(([e, n]) => `${e} ${n}`).join(', '));
  const byFormat = new Map();
  for (const u of units) byFormat.set(u.format + (u.isDir ? ' (unpacked folder)' : ''), (byFormat.get(u.format + (u.isDir ? ' (unpacked folder)' : '')) || 0) + 1);
  console.log(`  ${units.length} books found: ` + [...byFormat].map(([f, n]) => `${f} ${n}`).join(', '));
  db.close();
}

if (require.main === module) {
  main().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}

module.exports = { main, extractZip };
