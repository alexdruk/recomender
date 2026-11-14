'use strict';
// Checks that src/page.html can run in Safari 9 (old iPad, iOS 9.3):
//
//   npm run check-page
//
// 1. Every <script> must parse as ES5. The parser (acorn, in ES5 mode) stops
//    at arrow functions, let, const, template strings, classes, async, await,
//    default parameters, destructuring, spread and for-of.
// 2. The script must not use things that are fine in ES5 grammar but that
//    Safari 9 does not have or has half-working (fetch, Promise, ...).
// 3. The style must not use CSS that Safari 9 does not know (grid, var, gap ...).

const fs = require('node:fs');
const path = require('node:path');
const acorn = require('acorn');

const file = path.join(__dirname, 'page.html');
const html = fs.readFileSync(file, 'utf8');
const problems = [];
const lineOf = (pos) => html.slice(0, pos).split('\n').length;

// names that must not appear anywhere in the script
const BANNED_NAMES = ['fetch', 'Promise', 'Symbol', 'Map', 'Set', 'WeakMap', 'WeakSet', 'Proxy', 'Reflect', 'URLSearchParams', 'URL',
  'IntersectionObserver', 'ResizeObserver', 'AbortController', 'FormData', 'requestIdleCallback', 'Intl', 'globalThis'];
// methods that must not be called:  something.name(
const BANNED_METHODS = ['includes', 'startsWith', 'endsWith', 'find', 'findIndex', 'findLast', 'repeat', 'padStart', 'padEnd', 'replaceAll',
  'flat', 'flatMap', 'fill', 'assign', 'entries', 'values', 'fromEntries', 'append', 'prepend', 'replaceChildren', 'replaceWith', 'remove',
  'closest', 'matches', 'before', 'after', 'toggleAttribute', 'toLocaleString', 'toLocaleDateString', 'toLocaleTimeString',
  'trimStart', 'trimEnd', 'isInteger', 'isFinite', 'isNaN', 'querySelectorAll'];
// properties that must not be used:  something.name
const BANNED_PROPS = ['classList', 'dataset', 'hidden', 'scrollY', 'scrollX'];
const BANNED_PAIRS = ['Array.from', 'Array.of', 'Object.is'];

let scripts = 0;
for (const m of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)) {
  scripts++;
  const start = m.index + m[0].indexOf('>') + 1;
  const code = m[1];
  const tokens = [];
  try {
    acorn.parse(code, { ecmaVersion: 5, sourceType: 'script', onToken: tokens });
  } catch (e) {
    problems.push(`line ${lineOf(start + (e.pos || 0))}: not ES5 - ${e.message.replace(/ \(\d+:\d+\)$/, '')}`);
    continue;
  }
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t.type.label !== 'name') continue;
    const afterDot = i > 0 && tokens[i - 1].type.label === '.';
    const isKey = !afterDot && tokens[i + 1] && tokens[i + 1].type.label === ':' && i > 0 && ['{', ','].includes(tokens[i - 1].type.label);
    const at = `line ${lineOf(start + t.start)}`;
    const called = tokens[i + 1] && tokens[i + 1].type.label === '(';
    if (afterDot && (BANNED_PROPS.includes(t.value) || (called && BANNED_METHODS.includes(t.value)))) problems.push(`${at}: ".${t.value}" is not safe in Safari 9`);
    if (afterDot && i > 1 && BANNED_PAIRS.includes(`${tokens[i - 2].value}.${t.value}`)) problems.push(`${at}: "${tokens[i - 2].value}.${t.value}" is not in Safari 9`);
    if (!afterDot && !isKey && BANNED_NAMES.includes(t.value)) problems.push(`${at}: "${t.value}" is not in Safari 9 (or not in ES5)`);
  }
}
if (!scripts) problems.push('no <script> found in the page');

const BANNED_CSS = [
  [/display\s*:\s*(inline-)?grid/i, 'CSS grid'],
  [/\bgrid-[a-z-]+\s*:/i, 'CSS grid'],
  [/var\(\s*--/i, 'CSS variables'],
  [/(^|[;{\s])--[a-z0-9-]+\s*:/i, 'CSS variables'],
  [/(^|[;{\s])(row-|column-)?gap\s*:/i, '"gap"'],
  [/\b(clamp|min|max)\(/i, 'clamp / min / max'],
  [/(^|[;{\s])inset\s*:/i, '"inset"'],
  [/aspect-ratio/i, '"aspect-ratio"'],
  [/:(is|where|has|focus-visible)\b/i, 'a new selector'],
  [/overflow-wrap/i, '"overflow-wrap" (use word-wrap)'],
  [/position\s*:\s*sticky/i, '"position: sticky"'],
];
for (const m of html.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)) {
  const start = m.index + m[0].indexOf('>') + 1;
  const css = m[1].replace(/\/\*[\s\S]*?\*\//g, (x) => x.replace(/[^\n]/g, ' '));
  let pos = 0;
  for (const line of css.split('\n')) {
    for (const [re, what] of BANNED_CSS) if (re.test(line)) problems.push(`line ${lineOf(start + pos)}: ${what} is not in Safari 9`);
    pos += line.length + 1;
  }
}

if (problems.length) {
  console.error(`src/page.html breaks the Safari 9 rules in ${problems.length} place(s):`);
  for (const p of problems) console.error('  ' + p);
  process.exit(1);
}
console.log(`src/page.html is fine for Safari 9: ${scripts} script parsed as ES5, no banned script or style features.`);
