// No browser: the Contacts list (ui/app/directory.ts dirLoadRows) draws a read's rows or its error only while that read is
// still the newest search for the list. Reads are deferred fakes, so each case settles them in the order it names:
// - "A" is typed, then "B"; B's rows draw, then A fails: B's rows and count stay and no error shows;
// - the same, but A succeeds late: B's rows stay;
// - the newest search fails: its error shows;
// - A, B, then A again: the first A failing after the second A drew leaves the second A's rows;
// - the relationship filter changes (a new page run) while a read is in flight: the older read's failure draws nothing;
// - Load more for the current search adds its page, and a failed Load more still shows its error.
// Run: node ui/tests/directory-search-race-unit.cts
'use strict';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const vm: typeof import('node:vm') = require('node:vm');
const {transformSync} = require('esbuild') as typeof import('esbuild');

interface Read { url: string; resolve: (v: unknown) => void; reject: (e: unknown) => void }
const reads: Read[] = [];
const ctx = vm.createContext({
  window: {addEventListener() {}, matchMedia: () => ({matches: false, addEventListener() {}})},
  esc: (s: unknown) => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'} as Record<string, string>)[c]!),
  get: (url: string) => new Promise((resolve, reject) => { reads.push({url, resolve, reject}); }),
  actorLabel: (who: string) => who,
  agoSpan: () => '',
  skelRows: () => '',
  filterIcon: () => '',
  eventElement: () => null,
  rdUnder: () => false,
  S: {},
  document: {addEventListener() {}, getElementById: () => null, querySelector: () => null},
  location: {hash: ''},
  history: {replaceState() {}},
  HTMLElement: class {}, HTMLButtonElement: class {}, HTMLDetailsElement: class {},
  URLSearchParams,
});
// The names the shell keeps for every page (ui/app/pages-shell.ts, the page parts' ui/parts.json) load first, as in the app.
vm.runInContext(transformSync(fs.readFileSync(path.join(__dirname, '..', 'app', 'pages-shell.ts'), 'utf8'), {loader: 'ts', target: 'es2022'}).code, ctx, {filename: 'pages-shell.ts'});
const file = path.join(__dirname, '..', 'app', 'directory.ts');
vm.runInContext(transformSync(fs.readFileSync(file, 'utf8'), {loader: 'ts', target: 'es2022'}).code, ctx, {filename: 'directory.ts'});

// The page's own Error, so its message is what it draws (an Error from this realm fails its instanceof).
const failure = (): Error => new (vm.runInContext('Error', ctx) as ErrorConstructor)('Could not load. Try again.');
// A minimal host: the list's body is the one element dirLoadRows draws into.
const body = {innerHTML: '', querySelector: () => null};
const root = {querySelector: (sel: string) => sel === '[data-dir-body]' ? body : null};
const types = [{key: 'person', name: 'Person', plural_name: 'People'}];
const load = (reset = true): Promise<void> => ctx.dirLoadRows(root, types, reset);
const flush = () => new Promise(r => setImmediate(r));
const person = (id: string, name: string) => ({id, type: 'person', name, status: 'active', primary_email: null, primary_domain: null,
  links_to_us: [], last_activity_at: null, organization: null, owner: null, sources: []});
const found = (rows: unknown[], next: string | null = null) => ({rows, next, count: rows.length, approximate: false});
const RAJ = person('person/raj-patel', 'Raj Patel'), ANA = person('person/ana-ruiz', 'Ana Ruiz'), DEV = person('person/dev-shah', 'Dev Shah');

// A fresh list on page run `run`, as pageDirectory leaves it.
function open(run = 1, link = ''): void {
  reads.length = 0;
  body.innerHTML = '';
  vm.runInContext(`DIRP = {run: ${run}}; DIRL = {run: ${run}, type: 'person', q: '', link: ${JSON.stringify(link)}, next: null, rows: [], search: 0};`, ctx);
}
// What the search box does after its typing delay.
function type(q: string): Promise<void> {
  vm.runInContext(`DIRL.q = ${JSON.stringify(q)};`, ctx);
  return load();
}
const rows = () => [...body.innerHTML.matchAll(/data-dir-row="([^"]+)"/g)].map(m => m[1]);
const shows = (ids: string[], tag: string) => {
  assert.deepEqual(rows(), ids, tag + ': rows');
  assert.ok(!body.innerHTML.includes('data-dir-error'), tag + ': no error');
  assert.match(body.innerHTML, new RegExp(`data-dir-count>${ids.length} people<`), tag + ': count');
};

async function main(): Promise<void> {
  // 1. A, then B; B draws, then A fails: B's rows stay.
  open();
  const a1 = type('A'), b1 = type('B');
  const [readA, readB] = reads;
  assert.match(readA!.url, /name=A/);
  assert.match(readB!.url, /name=B/);
  readB!.resolve(found([RAJ]));
  await b1;
  shows([RAJ.id], 'new success');
  readA!.reject(failure());
  await a1;
  shows([RAJ.id], 'old failure after new success');

  // 2. The same order, A succeeds late: B's rows stay (as before).
  open();
  const a2 = type('A'), b2 = type('B');
  reads[1]!.resolve(found([RAJ]));
  await b2;
  reads[0]!.resolve(found([ANA, DEV]));
  await a2;
  shows([RAJ.id], 'old success after new success');

  // 3. The newest search fails: its error shows.
  open();
  const a3 = type('A'), b3 = type('B');
  reads[0]!.resolve(found([ANA]));
  await a3;
  reads[1]!.reject(failure());
  await b3;
  assert.match(body.innerHTML, /data-dir-error>Could not load\. Try again\.</, 'the newest failure shows');

  // 4. A, B, A again: the first A fails after the second A drew.
  open();
  const a4 = type('A'), b4 = type('B'), again = type('A');
  reads[2]!.resolve(found([ANA]));
  await again;
  reads[1]!.resolve(found([RAJ]));
  await b4;
  reads[0]!.reject(failure());
  await a4;
  shows([ANA.id], 'retyped search');

  // 5. The relationship filter changes while a read is in flight: a new page run with its own list.
  open(1);
  const old = type('A');
  const stale = reads[0]!;
  open(2, 'customer_of');
  const fresh = load();
  reads[0]!.resolve(found([DEV]));
  await fresh;
  stale.reject(failure());
  await old;
  shows([DEV.id], 'older filter fails');

  // 6. Load more for the current search adds its page; a failed Load more shows its error as before.
  open();
  const first = type('A');
  reads[0]!.resolve(found([ANA], 'cursor-1'));
  await first;
  const more = load(false);
  assert.match(reads[1]!.url, /after=cursor-1/);
  reads[1]!.resolve(found([DEV], 'cursor-2'));
  await more;
  assert.deepEqual(rows(), [ANA.id, DEV.id], 'Load more appends');
  const failed = load(false);
  reads[2]!.reject(failure());
  await failed;
  assert.match(body.innerHTML, /data-dir-error/, 'a failed Load more shows its error');
  await flush();
  console.log('directory-search-race-unit: ok');
}
main().catch(e => { console.error(e); process.exit(1); });
