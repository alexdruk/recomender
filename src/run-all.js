'use strict';
// Runs the library steps in order:
//   unpack -> parse FB2 -> parse EPUB -> dedupe and store -> inventory report
//   -> feed probe (only until the feed collector has run) -> genres and cleaning

const steps = [
  ['1. Unpack the archive', './unzip'],
  ['2. Parse FB2 books', './parse-fb2'],
  ['3. Parse EPUB books', './parse-epub'],
  ['4. Dedupe and fill the books table', './dedupe'],
  ['5. Inventory report', './inventory'],
  ['6. Feed probe', './probe-feed'],
  ['7. Genres from the catalog, collections, genre groups', './enrich'],
];

async function main() {
  const t0 = Date.now();
  for (const [title, mod] of steps) {
    console.log(`\n=== ${title} ===`);
    const t = Date.now();
    await require(mod).main();
    console.log(`(${((Date.now() - t) / 1000).toFixed(1)} s)`);
  }
  console.log(`\nAll steps finished in ${((Date.now() - t0) / 1000).toFixed(1)} s.`);
  console.log('Reports: reports/inventory.md and reports/feed-probe.md');
}

main().catch((e) => {
  console.error('\nSTOPPED: ' + (e.message || e));
  process.exit(1);
});
