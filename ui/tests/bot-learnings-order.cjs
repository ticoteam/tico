// No browser: the Learnings panel's memory files (ui/app/bot-learnings.js learnNewestFirst) open on the working
// rules, then the newest entry. Bots append at the bottom, so file order opened on the oldest (Dev 301).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/bot-learnings.js'), 'utf8'), context);
const order = text => context.learnNewestFirst(text);
const headings = text => order(text).split('\n').filter(l => /^#|^- /.test(l));

// Appended at the bottom: dated sections come back newest first, under the title and intro, each section whole.
const appended = [
  '# Learnings', 'Durable facts. Date each entry.', '',
  '## 2026-10-05 — Oldest', '- first point', '  continued', '',
  '## Working rules', '- Ask before you spec.', '- Don\'t hand off until Chris says go.', '',
  '## 2026-10-16 — Middle', '- middle point', '',
  '## 2026-10-21 — Newest', '- newest point',
].join('\n');
assert.deepEqual(headings(appended), ['# Learnings', '## Working rules', '- Ask before you spec.', '- Don\'t hand off until Chris says go.',
  '## 2026-10-21 — Newest', '- newest point', '## 2026-10-16 — Middle', '- middle point', '## 2026-10-05 — Oldest', '- first point']);
assert.match(order(appended), /- first point\n  continued/, 'an entry keeps its continuation lines');
assert.equal(order(appended).split('\n').sort().join('\n'), appended.split('\n').sort().join('\n'), 'no line is lost or added');

// Already newest first (product-lead's stopgap): unchanged.
const already = ['# Learnings', '', '## Working rules', '- one', '- two', '', '## 2026-10-21 — B', '- b', '', '## 2026-10-16 — A', '- a'].join('\n');
assert.equal(order(already), already);

// One log section of dated entries: newest entry first; an undated entry stays under the dated one above it.
const log = ['# Learnings', '## Log',
  '- Old rule [source: chat; added: 2026-10-01]', '- still about the old one',
  '- 2026-10-12: Middle', '- New one [source: task; added: 2026-10-20]'].join('\n');
assert.deepEqual(order(log).split('\n'), ['# Learnings', '## Log',
  '- New one [source: task; added: 2026-10-20]', '- 2026-10-12: Middle', '- Old rule [source: chat; added: 2026-10-01]', '- still about the old one']);

// A dated heading that mentions rules is a log entry, not the rules section; a `## ` inside a code fence is no heading.
const fenced = ['# L', '## 2026-10-02 — Rules for merges', '```', '## 2026-10-30 not a heading', '```', '## 2026-10-09 — Later'].join('\n');
assert.deepEqual(order(fenced).split('\n'), ['# L', '## 2026-10-09 — Later', '## 2026-10-02 — Rules for merges', '```', '## 2026-10-30 not a heading', '```']);

// `Oct 14` dates read in the year of the file's latest ISO date, so they sort among the ISO ones.
const short = ['# L', '## 2026-10-05 — A', '## Granola is per-person (Oct 13)', '## Tasks close on merge (Chris, Oct 14)', '## 2026-10-18 — B'].join('\n');
assert.deepEqual(headings(short), ['# L', '## 2026-10-18 — B', '## Tasks close on merge (Chris, Oct 14)', '## Granola is per-person (Oct 13)', '## 2026-10-05 — A']);

// No dates at all, or nothing: file order.
const plain = '# Decisions\n## Alpha\n- x\n## Beta\n- y';
assert.equal(order(plain), plain);
assert.equal(order(''), '');
assert.equal(order(undefined), '');
console.log('bot-learnings-order: ok');
