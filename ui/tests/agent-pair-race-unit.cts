// No browser: Pair (ui/app/settings-devices.ts settingsAgentPair) approves only the code whose preview the person is
// looking at, captured when they press Pair (state-machines audit SM-01 B). Previews, approvals and the 250 ms typing
// delay are fakes, so each case settles them in the order it names:
// - code A typed, Pair pressed before A's preview answers, the box edited to B, then A answers: nothing is approved and
//   no preview shows; B's preview then shows, and only a press after that approves B;
// - A then B typed and their previews answered in reverse: B's preview stays and Pair approves B;
// - Pair on a shown preview approves that code; the box is read-only while it runs, so an edit approves nothing else;
// - a failed preview approves nothing;
// - a failed approval leaves the code reviewed, and Pair again sends the same approval.
// Run: node ui/tests/agent-pair-race-unit.cts
'use strict';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const vm: typeof import('node:vm') = require('node:vm');
const {transformSync} = require('esbuild') as typeof import('esbuild');

interface Read { code: string; resolve: (v: unknown) => void; reject: (e: unknown) => void }
interface Approve { body: {code: string; bot: string}; resolve: (v: unknown) => void; reject: (e: unknown) => void }
const reads: Read[] = [], approvals: Approve[] = [];
let timers: (() => void)[] = [];
const A = 'AAAA-BBBB', B = 'CCCC-DDDD';

// The dialog's pieces settingsAgentPair reads.
const preview = {textContent: ''}, status = {textContent: '', innerHTML: ''}, button = {disabled: false}, close = {onclick: null};
const form = {
  code: {value: '', readOnly: false, focus() {}, oninput: null as null | (() => void)},
  onsubmit: null as null | ((ev: {preventDefault(): void}) => Promise<void>),
  querySelector: () => button,
};
let closed = 0;
const dialog = {
  className: '', innerHTML: '', onclose: null as null | (() => void),
  querySelector: (sel: string) => sel === 'form' ? form : sel.includes('pair-status') ? status : sel.includes('data-close') ? close : preview,
  showModal() {}, close() { closed++; this.onclose?.(); }, remove() {},
};
const ctx = vm.createContext({
  S: {emps: [{id: 'ops', display_name: 'Ops', agent: {harness: 'hermes', credential: true}}]},
  document: {createElement: () => dialog, body: {appendChild() {}}},
  esc: String, settingsHarnessName: () => 'Hermes', errorMessage: (e: Error) => e.message,
  get: (url: string) => new Promise((resolve, reject) => { reads.push({code: decodeURIComponent(url.split('code=')[1]!), resolve, reject}); }),
  post: (_path: string, body: Approve['body']) => new Promise((resolve, reject) => { approvals.push({body, resolve, reject}); }),
  toast() {}, loadSettings: async () => {},
  setTimeout: (f: () => void) => { timers.push(f); return timers.length; }, clearTimeout: (n: number) => { if (n) timers[n - 1] = () => {}; },
});
// Only the Pair dialog's function: the rest of the Devices page needs a whole page around it.
const source = fs.readFileSync(path.join(__dirname, '..', 'app', 'settings-devices.ts'), 'utf8');
const begin = source.indexOf('function settingsAgentPair('), end = source.indexOf('async function settingsAgentCredential(', begin);
assert.ok(begin > 0 && end > begin, 'settingsAgentPair is in settings-devices.ts');
vm.runInContext(transformSync(source.slice(begin, end), {loader: 'ts', target: 'es2022'}).code, ctx, {filename: 'settings-devices.ts'});

const flush = () => new Promise(r => setImmediate(r));
// Plain copies, so values made in the page's realm compare by value.
const plain = (v: unknown) => JSON.parse(JSON.stringify(v));
const found = (profile: string) => ({profile, host: `computer-${profile}`, harness: 'hermes'});
// A fresh dialog.
function open(): void {
  reads.length = 0; approvals.length = 0; timers = []; closed = 0;
  Object.assign(form.code, {value: '', readOnly: false});
  preview.textContent = ''; status.textContent = ''; status.innerHTML = ''; button.disabled = false;
  ctx.settingsAgentPair('ops');
}
// The person types into the code box (the box refuses while read-only).
function type(code: string): void {
  if (form.code.readOnly) return;
  form.code.value = code;
  form.code.oninput!();
}
// The typing delay runs out.
function settle(): void { const due = timers; timers = []; due.forEach(f => f()); }
// The person presses Pair (a disabled button does nothing).
const press = (): Promise<void> => button.disabled ? Promise.resolve() : form.onsubmit!({preventDefault() {}});
// Types `code` and lets its preview show.
async function shown(code: string, profile: string): Promise<void> {
  type(code); settle();
  reads.at(-1)!.resolve(found(profile));
  await flush();
  assert.match(preview.textContent, new RegExp(`^${profile} · computer-${profile}`));
}

async function main(): Promise<void> {
  // 1. The audit's sequence: Pair pressed before A's preview, the box edited to B, then A answers.
  open();
  type(A);
  const pressing = press();
  type(B);
  reads[0]!.resolve(found('A'));
  await flush();
  assert.deepEqual(reads.map(r => r.code), [A]);
  assert.equal(approvals.length, 0, 'no code is approved from a stale preview');
  await pressing;
  assert.equal(preview.textContent, '', "A's preview is not shown for B");
  settle();
  reads[1]!.resolve(found('B'));
  await flush();
  assert.match(preview.textContent, /^B · computer-B/);
  assert.equal(approvals.length, 0, "B's preview showing approves nothing by itself");
  const pairB = press();
  await flush();
  assert.deepEqual(plain(approvals.map(x => x.body)), [{code: B, bot: 'ops'}]);
  approvals[0]!.resolve({profile: 'B', host: 'computer-B'});
  await pairB;
  assert.equal(closed, 1);

  // 2. A then B typed, previews answered in reverse: B's stays.
  open();
  type(A); settle();
  type(B); settle();
  reads[1]!.resolve(found('B'));
  await flush();
  reads[0]!.resolve(found('A'));
  await flush();
  assert.match(preview.textContent, /^B · /, "A's late answer does not replace B's preview");
  const pair2 = press();
  await flush();
  assert.equal(approvals[0]!.body.code, B);
  approvals[0]!.resolve({});
  await pair2;

  // 3. Pair on a shown preview approves that code; an edit while it runs approves nothing else.
  open();
  await shown(A, 'A');
  const pair3 = press();
  assert.equal(form.code.readOnly, true, 'the code box is read-only while pairing');
  assert.equal(button.disabled, true);
  type(B);
  assert.equal(form.code.value, A);
  await flush();
  assert.deepEqual(plain(approvals.map(x => x.body.code)), [A]);
  approvals[0]!.resolve({});
  await pair3;
  // Pair before any preview shows the preview first and approves nothing.
  open();
  type(A);
  const early = press();
  reads[0]!.resolve(found('A'));
  await early;
  assert.match(preview.textContent, /^A · /);
  assert.equal(approvals.length, 0, 'the first press only shows the preview');
  assert.match(status.textContent, /Pair/);

  // 4. A failed preview approves nothing.
  open();
  type(A);
  const failing = press();
  reads[0]!.reject(new (vm.runInContext('Error', ctx) as ErrorConstructor)('No such pairing code'));
  await failing;
  assert.equal(approvals.length, 0, 'a failed preview mints no credential');
  assert.equal(preview.textContent, 'No such pairing code');
  const retried = press();
  await flush();
  assert.equal(reads.length, 2, 'Pair after a failed preview previews again');
  reads[1]!.resolve(found('A'));
  await retried;
  assert.equal(approvals.length, 0, 'and approves nothing until the preview shows');

  // 5. A failed approval keeps the reviewed code; Pair again sends the same approval.
  open();
  await shown(A, 'A');
  const first = press();
  await flush();
  approvals[0]!.reject(new (vm.runInContext('Error', ctx) as ErrorConstructor)('Could not confirm whether this saved.'));
  await first;
  assert.equal(form.code.readOnly, false);
  assert.equal(button.disabled, false);
  const second = press();
  await flush();
  assert.equal(reads.length, 1, 'no new preview for the same code');
  assert.deepEqual(plain(approvals[1]!.body), plain(approvals[0]!.body), 'the retry is the same approval');
  approvals[1]!.resolve({});
  await second;
  finished = true;
  console.log('agent-pair-race-unit: ok');
}
// A press the code never settles leaves nothing to run, and node would exit quietly: that is a failure too.
let finished = false;
process.on('exit', code => { if (!code && !finished) { console.error('agent-pair-race-unit: stopped before the end'); process.exitCode = 1; } });
main().catch(e => { console.error(e); process.exit(1); });
