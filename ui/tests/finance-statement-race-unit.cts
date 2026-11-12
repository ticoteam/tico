// No browser: Upload statement (ui/app/finance-statement-upload.ts finStatementUpload) posts only the preview the person
// is looking at, whatever order the previews answer in (state-machines audit SM-01 A). Previews and posts are deferred
// fakes, so each case settles them in the order it names:
// - a sign flip whose preview is still on its way, then Post at once: Post is off and nothing is posted; once the new
//   preview lands, Post sends the flipped choice and its token;
// - "as shown" then "flipped" asked, answered in reverse: the flipped preview stays and is what Post sends;
// - the flipped preview fails: the old preview is gone, and the next press previews again instead of posting;
// - a new file, or closing the dialog, while a preview is on its way: its late answer draws nothing;
// - a post whose answer is lost: the file and signs boxes are locked while it runs, and Post again sends the same
//   action with the same key; a post the server refuses drops the preview, as before.
// Run: node ui/tests/finance-statement-race-unit.cts
'use strict';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const vm: typeof import('node:vm') = require('node:vm');
const {transformSync} = require('esbuild') as typeof import('esbuild');

interface Call { body: {signs: string; file_name: string}; resolve: (v: unknown) => void; reject: (e: unknown) => void }
interface Act { payload: {preview_token: string; signs?: string; file_name: string}; op: string; resolve: (v: unknown) => void; reject: (e: unknown) => void }
type Listener = (ev: {currentTarget: {checked: boolean}}) => void;

const previews: Call[] = [], acts: Act[] = [];
let opCount = 0;
let onSave: ((form: unknown) => Promise<unknown>) | null = null;
let closeDialog: (() => void) | null = null;

// The dialog's pieces, as finDialog and finStmtShow leave them. The signs box exists while a review is drawn; each draw
// makes a new one (innerHTML), so only the newest change listener is live.
const save = {disabled: false, textContent: 'Preview'};
const err = {hidden: true, textContent: ''};
const flip = {disabled: false, checked: false, listener: null as Listener | null,
  addEventListener(_: string, fn: Listener) { this.listener = fn; }};
const box = {hidden: true, html: '',
  get innerHTML() { return this.html; },
  set innerHTML(v: string) { this.html = v; flip.listener = null; flip.checked = / checked>/.test(v); flip.disabled = false; }};
let fileChange: (() => void) | null = null;
const fileInput = {disabled: false, files: [] as unknown[], addEventListener(_: string, fn: () => void) { fileChange = fn; }};
const form = {
  querySelector(sel: string): unknown {
    if (sel.includes('type=file')) return fileInput;
    if (sel.includes('data-fin-stmt-review')) return box;
    if (sel.includes('type=submit')) return save;
    if (sel.includes('data-fin-stmt-flip')) return box.html ? flip : null;
    if (sel.includes('data-error')) return err;
    return null;
  },
  querySelectorAll: (sel: string) => sel === 'input' ? [fileInput, ...(box.html ? [flip] : [])] : [],
};

const ctx = vm.createContext({
  TextEncoder, Uint8Array, Date, console,
  btoa: (s: string) => Buffer.from(s, 'binary').toString('base64'),
  esc: (s: unknown) => String(s ?? ''),
  finNeed: <T,>(x: T) => x,
  finDialog: (_title: string, _body: string, _label: string, fn: (form: unknown) => Promise<unknown>) => {
    onSave = fn;
    let onClose: (() => void) | null = null;
    closeDialog = () => onClose?.();
    return {querySelector: () => form, addEventListener: (_: string, f: () => void) => { onClose = f; }};
  },
  post: (_path: string, body: Call['body']) => new Promise((resolve, reject) => { previews.push({body, resolve, reject}); }),
  finAct: (_kind: string, payload: Act['payload'], op: string) => new Promise((resolve, reject) => { acts.push({payload, op, resolve, reject}); }),
  newRequestId: () => `op-${++opCount}`,
  toast: () => {},
  finSrcLoad: async () => {},
});
const file = path.join(__dirname, '..', 'app', 'finance-statement-upload.ts');
vm.runInContext(transformSync(fs.readFileSync(file, 'utf8'), {loader: 'ts', target: 'es2022'}).code, ctx, {filename: 'finance-statement-upload.ts'});

const flush = () => new Promise(r => setImmediate(r));
const pageError = (message: string, extra: object = {}) => Object.assign(new (vm.runInContext('Error', ctx) as ErrorConstructor)(message), extra);
const statement = (name: string) => ({name, size: 30, arrayBuffer: async () => new TextEncoder().encode('date,amount\n2026-10-02,-59.99').buffer});
// What a preview answers for the signs it was asked for.
const answer = (signs: string, rows = 2) => ({token: `token-${signs}`, expires_at: 'later', money_account_id: 'acct', file_name: 'oct.csv',
  file_format: 'csv', start: '2026-10-01', end: '2026-10-31', card: false, last4: null, signs: {flipped: signs === 'flipped', by: signs},
  new: rows, duplicate: 0, tie: {opening: 0, added: 0, removed: 5999, ends: -5999, statement_says: null, off_by: 0}, rows: []});

// A fresh dialog on a picked file.
function open(name = 'oct.csv'): unknown {
  previews.length = 0; acts.length = 0; onSave = null;
  Object.assign(save, {disabled: false, textContent: 'Preview'});
  Object.assign(err, {hidden: true, textContent: ''});
  box.hidden = true; box.innerHTML = '';
  ctx.finStatementUpload({id: 'acct', name: 'Synthetic checking', last4: null, kind: 'bank'});
  const picked = statement(name);
  fileInput.files = [picked]; fileInput.disabled = false;
  return picked;
}
// The person presses the dialog's button, as finDialog's submit runs it (off while it runs; a refusal shows its error).
async function press(): Promise<'blocked' | 'open' | 'closed' | 'error'> {
  if (save.disabled) return 'blocked';
  save.disabled = true;
  try {
    const kept = await onSave!(form);
    save.disabled = false;
    return kept === false ? 'open' : 'closed';
  } catch (e) {
    err.textContent = (e as Error).message; err.hidden = false; save.disabled = false;
    return 'error';
  }
}
// The person ticks or unticks the signs box.
function tick(checked: boolean): void {
  flip.checked = checked;
  assert.ok(flip.listener, 'the signs box is drawn');
  flip.listener({currentTarget: {checked}});
}
// Previews the picked file under `auto` and leaves the review on screen.
async function reviewed(): Promise<void> {
  const pressing = press();
  await flush();
  previews[0]!.resolve(answer('auto'));
  assert.equal(await pressing, 'open');
  assert.match(save.textContent, /^Post 2 rows/);
}

async function main(): Promise<void> {
  // 1. A sign flip whose preview has not answered, then Post at once.
  open();
  await reviewed();
  tick(true);
  assert.equal(previews.length, 2);
  assert.equal(previews[1]!.body.signs, 'flipped');
  assert.equal(save.disabled, true, 'Post is off while the new preview is on its way');
  assert.equal(await press(), 'blocked');
  await assert.rejects(onSave!(form) as Promise<unknown>, /Wait for the preview/, 'a direct submit is refused too');
  assert.equal(acts.length, 0, 'nothing was posted from the replaced preview');
  previews[1]!.resolve(answer('flipped'));
  await flush();
  assert.equal(save.disabled, false);
  const posting = press();
  await flush();
  assert.equal(acts.length, 1);
  assert.equal(acts[0]!.payload.preview_token, 'token-flipped');
  assert.equal(acts[0]!.payload.signs, 'flipped');
  acts[0]!.resolve({status: 'committed', result: {new: 2}});
  assert.equal(await posting, 'closed');

  // 2. "As shown" then "flipped", answered in reverse order: the newest choice stays.
  open();
  await reviewed();
  tick(false);
  const older = previews[1]!;
  tick(true);
  const newer = previews[2]!;
  assert.deepEqual([older.body.signs, newer.body.signs], ['as_shown', 'flipped']);
  newer.resolve(answer('flipped'));
  await flush();
  older.resolve(answer('as_shown'));
  await flush();
  assert.equal(flip.checked, true, 'the flipped review is the one drawn');
  const posted = press();
  await flush();
  assert.equal(acts[0]!.payload.preview_token, 'token-flipped');
  assert.equal(acts[0]!.payload.signs, 'flipped');
  acts[0]!.resolve({status: 'committed', result: {new: 2}});
  await posted;

  // 3. The replacement preview fails: no old preview is left to post.
  open();
  await reviewed();
  tick(true);
  previews[1]!.reject(pageError('Could not read the file'));
  await flush();
  assert.equal(box.hidden, true, 'the old review is gone');
  assert.equal(save.textContent, 'Preview');
  assert.equal(err.textContent, 'Could not read the file');
  const again = press();
  await flush();
  assert.equal(acts.length, 0, 'the next press previews again, it does not post');
  assert.equal(previews.length, 3);
  assert.equal(previews[2]!.body.signs, 'auto');
  previews[2]!.resolve(answer('auto'));
  assert.equal(await again, 'open');

  // 4a. A new file while the first file's preview is on its way: the late answer draws nothing.
  open('sept.csv');
  const first = press();
  await flush();
  fileInput.files = [statement('oct.csv')];
  fileChange!();
  previews[0]!.resolve(answer('auto'));
  assert.equal(await first, 'open');
  assert.equal(box.hidden, true, "the old file's preview is not drawn");
  assert.equal(save.textContent, 'Preview');
  const second = press();
  await flush();
  assert.equal(acts.length, 0);
  assert.equal(previews[1]!.body.file_name, 'oct.csv', 'the next press previews the new file');
  previews[1]!.resolve(answer('auto'));
  await second;
  // 4b. The dialog closes while a sign flip's preview is on its way: its late answer draws nothing.
  tick(true);
  const drawn = box.html;
  closeDialog!();
  previews[2]!.resolve(answer('flipped', 7));
  await flush();
  assert.equal(box.html, drawn, 'a closed dialog draws no late preview');
  // 4c. A new file previewed while the old file's preview is still on its way: the old press ending does not turn the
  // button back on before the new preview lands.
  open('sept.csv');
  const oldPress = press();
  await flush();
  fileInput.files = [statement('oct.csv')];
  fileChange!();
  const newPress = press();
  await flush();
  previews[0]!.resolve(answer('auto'));
  await flush();
  assert.equal(save.disabled, true, "the button stays off while the new file's preview is on its way");
  previews[1]!.resolve(answer('auto', 3));
  assert.equal(await oldPress, 'open');
  assert.equal(await newPress, 'open');
  assert.equal(save.disabled, false);
  assert.equal(save.textContent, 'Post 3 rows', "the new file's preview is the one drawn");

  // 5. A post whose answer is lost keeps its preview and key; Post again is the same action.
  open();
  await reviewed();
  const lost = press();
  await flush();
  assert.equal(fileInput.disabled, true, 'the file picker is locked while posting');
  assert.equal(flip.disabled, true, 'the signs box is locked while posting');
  acts[0]!.reject(pageError('Could not confirm whether this saved.', {unconfirmed: true}));
  assert.equal(await lost, 'error');
  assert.equal(fileInput.disabled, false);
  assert.equal(box.hidden, false, 'the review stays');
  const retry = press();
  await flush();
  assert.equal(previews.length, 1, 'no new preview');
  assert.equal(acts.length, 2);
  assert.equal(acts[1]!.op, acts[0]!.op, 'the retry carries the same action key');
  assert.deepEqual(JSON.stringify(acts[1]!.payload), JSON.stringify(acts[0]!.payload));
  acts[1]!.resolve({status: 'committed', result: {new: 2}});
  assert.equal(await retry, 'closed');
  // A post the server refuses (a lapsed token) drops the preview; the next press previews again under a new key.
  open();
  await reviewed();
  const refused = press();
  await flush();
  acts[0]!.reject(pageError('The preview has expired'));
  assert.equal(await refused, 'error');
  assert.equal(box.hidden, true);
  assert.equal(save.textContent, 'Preview');
  const fresh = press();
  await flush();
  assert.equal(previews.length, 2, 'Preview starts again');
  previews[1]!.resolve(answer('auto'));
  await fresh;
  const next = press();
  await flush();
  assert.notEqual(acts[1]!.op, acts[0]!.op, 'a new preview posts under a new key');
  acts[1]!.resolve({status: 'committed'});
  await next;
  finished = true;
  console.log('finance-statement-race-unit: ok');
}
// A press the code never settles leaves nothing to run, and node would exit quietly: that is a failure too.
let finished = false;
process.on('exit', code => { if (!code && !finished) { console.error('finance-statement-race-unit: stopped before the end'); process.exitCode = 1; } });
main().catch(e => { console.error(e); process.exit(1); });
