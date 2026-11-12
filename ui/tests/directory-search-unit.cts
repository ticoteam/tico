// No browser: Contacts' list search (ui/app/directory.ts dirLoadRows) draws a read's outcome, rows or error, only while
// that read is still the newest for the list. Typing "A" then "B": B's rows stay when A fails or succeeds late; the
// newest search's own failure, and a failed Load more for it, still show their error; retyping "A" after "B" is a new
// read, so the first "A" failing late does not replace it; a relationship change while a read is out keeps its rows.
// Reads are deferred fakes resolved in the order each case needs; the page is a minimal markup host.
// Run: node ui/tests/directory-search-unit.cts
'use strict';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const vm: typeof import('node:vm') = require('node:vm');
const {transformSync} = require('esbuild') as typeof import('esbuild');

interface Read { url: string; resolve: (v: unknown) => void; reject: (e: unknown) => void }
const reads: Read[] = [];
const ctx = vm.createContext({
  esc: (s: unknown) => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'} as Record<string, string>)[c]!),
  get: (url: string) => new Promise((resolve, reject) => { reads.push({url, resolve, reject}); }),
  actorLabel: (who: string) => who,
  URLSearchParams,
});
const file = 'directory.ts';
vm.runInContext(transformSync(fs.readFileSync(path.join(__dirname, '..', 'app', file), 'utf8'), {loader: 'ts', target: 'es2022'}).code, ctx, {filename: file});

// The list's body: what dirLoadRows last drew.
const body = {innerHTML: '', querySelector: () => null};
const root = {querySelector: (sel: string) => sel === '[data-dir-body]' ? body : null};
const types = [{key: 'person', name: 'Person', plural_name: 'People'}];
const page = (name: string, next: string | null = null) => ({rows: [{id: `person/${name.toLowerCase()}`, type: 'person', name, status: 'active',
  primary_email: null, primary_domain: null, links_to_us: [], last_activity_at: null}], next, count: 1, approximate: false});
ctx.__root = root; ctx.__types = types;
const run = (code: string): unknown => vm.runInContext(code, ctx);
// A fresh list on screen (what pageDirectory sets up), with no read out.
const open = (link = '') => {
  reads.length = 0; body.innerHTML = '';
  run(`DIRP = {run: (DIRP?.run ?? 0) + 1}; DIRL = {run: DIRP.run, type: 'person', q: '', link: ${JSON.stringify(link)}, next: null, rows: []};`);
};
// The search box's handler after its typing delay: the new text, then a fresh read.
const type = (q: string): Promise<void> => run(`DIRL.q = ${JSON.stringify(q)}; dirLoadRows(__root, __types, true)`) as Promise<void>;
const more = (): Promise<void> => run('dirLoadRows(__root, __types, false)') as Promise<void>;
const settle = () => new Promise(r => setImmediate(r));
const shows = (name: string) => body.innerHTML.includes(`data-dir-row="person/${name.toLowerCase()}"`);
const error = () => body.innerHTML.includes('data-dir-error');

async function main(): Promise<void> {
  // "A" is out, "B" is typed and draws, then "A" fails: B's rows and count stay, no error.
  open();
  const a = type('A'), b = type('B');
  assert.match(reads[0]!.url, /name=A/); assert.match(reads[1]!.url, /name=B/);
  reads[1]!.resolve(page('Bea')); await b;
  assert.ok(shows('Bea'));
  reads[0]!.reject(new Error('Could not load. Try again.')); await a;
  assert.ok(shows('Bea') && !error(), 'an older failed search leaves the newer rows: ' + body.innerHTML);
  assert.match(body.innerHTML, /data-dir-count/);

  // The same order, "A" succeeding late: B's rows stay (as before).
  open();
  const a2 = type('A'), b2 = type('B');
  reads[1]!.resolve(page('Bea')); await b2;
  reads[0]!.resolve(page('Ann')); await a2;
  assert.ok(shows('Bea') && !shows('Ann'), 'an older successful search leaves the newer rows');

  // The newest search fails: its error shows.
  open();
  const a3 = type('A'), b3 = type('B');
  reads[0]!.resolve(page('Ann')); await a3;
  reads[1]!.reject(new Error('Could not load. Try again.')); await b3;
  assert.ok(error() && !shows('Ann'), 'the newest search\'s failure shows its error');

  // "A", "B", "A" again: the second "A" draws, then the first "A" fails late and is dropped.
  open();
  const a4 = type('A'), b4 = type('B'), a5 = type('A');
  reads[1]!.resolve(page('Bea')); await b4;
  reads[2]!.resolve(page('Ann')); await a5;
  reads[0]!.reject(new Error('Could not load. Try again.')); await a4;
  assert.ok(shows('Ann') && !error(), 'an older failure of the same text leaves the newer rows');

  // A relationship change while a read is out: the page opens again with the filter; the older read failing draws nothing.
  open();
  const before = type('A');
  run(`DIRP = {run: DIRP.run + 1}; DIRL = {run: DIRP.run, type: 'person', q: 'A', link: 'customer_of', next: null, rows: []};`);
  const after = run('dirLoadRows(__root, __types, true)') as Promise<void>;
  assert.match(reads[1]!.url, /link=customer_of/);
  reads[1]!.resolve(page('Cal')); await after;
  reads[0]!.reject(new Error('Could not load. Try again.')); await before;
  assert.ok(shows('Cal') && !error(), 'an older read failing after a relationship change leaves the newer rows');

  // Load more for the current search: its page adds to the rows; when it fails, its error shows as today.
  open();
  const first = type('A');
  reads[0]!.resolve(page('Ann', 'c1')); await first;
  const second = more();
  assert.match(reads[1]!.url, /after=c1/);
  reads[1]!.resolve(page('Ava', 'c2')); await second;
  assert.ok(shows('Ann') && shows('Ava'), 'Load more adds its page');
  const third = more();
  reads[2]!.reject(new Error('Could not load. Try again.')); await third;
  assert.ok(error(), 'a failed Load more for the current search shows its error');
  await settle();
}
main().then(() => console.log('directory-search-unit: ok'), error => { console.error(error); process.exitCode = 1; });
