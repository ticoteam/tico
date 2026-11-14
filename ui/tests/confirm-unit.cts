// No browser: an "are you sure?" question stops the change it guards until the person says OK, in the desktop app as in
// a browser. The desktop app's dialog answers later, so the question is a promise (askConfirm, ui/app/confirm.ts) that
// every caller awaits; before, the shell replaced `window.confirm` with a function returning a promise, which is truthy,
// so `if (!confirm(...)) return;` sent the delete or revoke at once and Cancel did nothing.
// This loads the shell's real dialog script (DIALOG_SCRIPT in app/src/bridge.rs) and three real callers (Computers'
// Delete history, a page's Delete, Settings' Revoke agent credential), holds the native answer, and counts the writes.
// Run: node ui/tests/confirm-unit.cts
'use strict';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const vm: typeof import('node:vm') = require('node:vm');
const {transformSync} = require('esbuild') as typeof import('esbuild');

const root = path.join(__dirname, '..', '..');
const read = (file: string) => fs.readFileSync(path.join(root, file), 'utf8');
const js = (file: string) => transformSync(read(file), {loader: 'ts', target: 'es2022'}).code;

// The shell's dialog script, as bridge.rs gives it to every org page.
const bridge = read('app/src/bridge.rs');
const marker = 'const DIALOG_SCRIPT: &str = r#"';
const begin = bridge.indexOf(marker) + marker.length;
assert.ok(begin > marker.length, 'bridge.rs still has DIALOG_SCRIPT');
const dialogScript = bridge.slice(begin, bridge.indexOf('"#;', begin));

// A top-level function of a compiled file, from its `async function name(` to its closing brace at column 0.
function fn(file: string, name: string): string {
  const code = js(file), start = code.indexOf(`async function ${name}(`);
  assert.ok(start >= 0, `${file} defines ${name}`);
  return code.slice(start, code.indexOf('\n}', start) + 2);
}
const callers = [
  fn('ui/app/computer-performance.ts', 'perfErase'),
  fn('ui/app/pages.ts', 'pageFullAct'),
  fn('ui/app/settings-devices.ts', 'settingsAgentRevoke'),
];

interface Write { method: string; path: string }
interface Dialog { name: string; body: {confirm: boolean; message: string}; resolve(v: unknown): void; reject(e: unknown): void }
let writes: Write[] = [];
let dialogs: Dialog[] = [];
let browserAnswer: boolean | null = null;

// One page: `desktop` runs the shell's script first, as the app does; otherwise `confirm` is a browser's.
function page(desktop: boolean) {
  const button = {disabled: false, closest() { return button; }};
  const context: Record<string, unknown> = {
    S: {emps: [{id: 'scout', display_name: 'Scout'}], me: {id: 'human:ana'}},
    location: {hash: '#/settings'}, history: {length: 2, back() {}}, RD_FILES: '#/files', encodeURIComponent,
    document: {querySelector: () => null},
    writeRequest: async (method: string, p: string) => { writes.push({method, path: p}); return {}; },
    post: async (p: string) => { writes.push({method: 'POST', path: p}); return {}; },
    patch: async (p: string) => { writes.push({method: 'PATCH', path: p}); return {}; },
    toast() {}, perfLoad: async () => {}, loadSettings: async () => {}, errorMessage: String,
    pageMenuClose: () => false, // pageFullAct closes the page's ⋯ menu first; no menu is open here
    confirm: () => { assert.notEqual(browserAnswer, null, 'the browser was asked'); return browserAnswer; },
  };
  if (desktop) context.__TAURI__ = {core: {invoke: (name: string, body: Dialog['body']) =>
    new Promise((resolve, reject) => dialogs.push({name, body, resolve, reject}))}};
  context.window = context;
  vm.createContext(context);
  if (desktop) vm.runInContext(dialogScript, context, {filename: 'bridge.rs DIALOG_SCRIPT'});
  vm.runInContext(js('ui/app/confirm.ts'), context, {filename: 'confirm.ts'});
  for (const code of callers) vm.runInContext(code, context);
  const run = context as unknown as {
    perfErase(state: unknown, button: unknown): Promise<void>;
    pageFullAct(state: unknown, act: string): Promise<void>;
    settingsAgentRevoke(slug: string): Promise<void>;
    askConfirm(message: string, from?: unknown): Promise<boolean>;
    confirm(message: string): unknown;
    location: {hash: string};
    S: {me: {id: string} | null};
  };
  return {run, button};
}

const actions: [string, (p: ReturnType<typeof page>) => Promise<void>, Write][] = [
  ['erase performance', p => p.run.perfErase({id: 'c1'}, p.button), {method: 'DELETE', path: '/v2/computers/c1/performance'}],
  ['delete page', p => p.run.pageFullAct({page: {title: 'Plan', file_id: 'f1'}}, 'delete'), {method: 'DELETE', path: '/v2/files/f1'}],
  ['revoke agent', p => p.run.settingsAgentRevoke('scout'), {method: 'POST', path: '/v2/bots/scout/agent-credential/revoke'}],
];
const settle = () => new Promise(resolve => setImmediate(resolve));

void (async () => {
  // The shell's `confirm` cannot wait, so it answers false at once rather than a (truthy) promise.
  const bare = page(true);
  assert.equal(bare.run.confirm('Delete?'), false, 'the desktop confirm answers false, never a promise');
  assert.equal(dialogs.length, 0, 'and shows nothing');

  for (const [name, act, write] of actions) {
    // Pending, then Cancel: nothing is sent while the question is up, nor after Cancel.
    let p = page(true); writes = []; dialogs = [];
    let doing = act(p);
    await settle();
    assert.equal(dialogs.length, 1, `${name}: one dialog`);
    assert.deepEqual({name: dialogs[0]!.name, confirm: dialogs[0]!.body.confirm}, {name: 'page_dialog', confirm: true});
    assert.equal(writes.length, 0, `${name}: nothing is sent while the question is up`);
    dialogs[0]!.resolve(false); await doing;
    assert.equal(writes.length, 0, `${name}: Cancel sends nothing`);

    // The shell refuses the dialog (the page is not on screen) or it fails: nothing is sent.
    p = page(true); writes = []; dialogs = [];
    doing = act(p); await settle();
    dialogs[0]!.reject('Only the page on screen may show a dialog.'); await doing;
    assert.equal(writes.length, 0, `${name}: a refused dialog sends nothing`);

    // Anything but a plain true is not OK.
    p = page(true); writes = []; dialogs = [];
    doing = act(p); await settle();
    dialogs[0]!.resolve('true'); await doing;
    assert.equal(writes.length, 0, `${name}: a non-boolean answer sends nothing`);

    // The page moved on (another org's page or route) while the question was up: OK sends nothing.
    p = page(true); writes = []; dialogs = [];
    doing = act(p); await settle();
    p.run.location.hash = '#/home';
    dialogs[0]!.resolve(true); await doing;
    assert.equal(writes.length, 0, `${name}: OK on a page that moved on sends nothing`);

    // Someone else signed in while the question was up: the answer was not theirs, so OK sends nothing.
    p = page(true); writes = []; dialogs = [];
    doing = act(p); await settle();
    p.run.S.me = {id: 'human:ben'};
    dialogs[0]!.resolve(true); await doing;
    assert.equal(writes.length, 0, `${name}: OK after another person signed in sends nothing`);

    // OK, with a second click while the question is up: one dialog, exactly one write.
    p = page(true); writes = []; dialogs = [];
    doing = act(p); await settle();
    const again = act(p); await settle();
    assert.equal(dialogs.length, 1, `${name}: a second click while the question is up asks nothing more`);
    await again;
    assert.equal(writes.length, 0, `${name}: and sends nothing`);
    dialogs[0]!.resolve(true); await doing;
    assert.deepEqual(writes, [write], `${name}: OK sends exactly one write`);

    // A browser's confirm, the same way: Cancel nothing, OK one write.
    for (const answer of [false, true]) {
      p = page(false); writes = []; dialogs = []; browserAnswer = answer;
      await act(p);
      assert.deepEqual(writes, answer ? [write] : [], `${name}: browser ${answer ? 'OK' : 'Cancel'}`);
    }
    browserAnswer = null;
  }

  // The button that asked is disabled while the question is up, and given back after Cancel.
  const p = page(true); dialogs = [];
  const button = {disabled: false, closest() { return button; }};
  const asked = p.run.askConfirm('Delete?', button); await settle();
  assert.equal(button.disabled, true, 'the asking button is disabled while the question is up');
  dialogs[0]!.resolve(false);
  assert.equal(await asked, false);
  assert.equal(button.disabled, false, 'and enabled again after the answer');

  // askConfirm itself takes only a plain true, and a failure as false, whatever answers it (not only this bridge).
  for (const [answer, expected] of [[Promise.resolve('true'), false], [Promise.resolve(1), false],
    [Promise.reject(new Error('gone')), false], [Promise.resolve(true), true]] as const) {
    const q = page(false);
    (q.run as unknown as {ticoConfirm: () => Promise<unknown>}).ticoConfirm = () => answer;
    assert.equal(await q.run.askConfirm('Delete?'), expected);
  }

  // Every question in the UI goes through askConfirm and is awaited (TypeScript flags `if (askConfirm(...))`, but not
  // `!askConfirm(...)`, which is the usual guard, so this reads the sources).
  // Every UI script in every folder, so a new folder cannot slip past; tests, vendored and generated code aside.
  const skip = new Set(['ui/tests', 'ui/vendor', 'ui/types', 'ui/assets', 'ui/node_modules', 'ui/app/confirm.ts', 'ui/sw.ts']);
  const walk = (dir: string): string[] => fs.readdirSync(path.join(root, dir), {withFileTypes: true}).flatMap(entry => {
    const rel = `${dir}/${entry.name}`;
    if (skip.has(rel)) return [];
    if (entry.isDirectory()) return walk(rel);
    return /\.(ts|js)$/.test(entry.name) ? [rel] : [];
  });
  const sources = walk('ui');
  const bad: string[] = [];
  for (const file of sources) {
    read(file).split('\n').forEach((line, i) => {
      const code = line.replace(/\/\/.*$/, '');
      if (/(^|[^\w.$])(window\.)?confirm\s*\(/.test(code)) bad.push(`${file}:${i + 1}: confirm( instead of askConfirm(`);
      for (const m of code.matchAll(/askConfirm\s*\(/g)) {
        if (!/await\s*$/.test(code.slice(0, m.index))) bad.push(`${file}:${i + 1}: askConfirm( without await`);
      }
    });
  }
  assert.deepEqual(bad, [], 'every confirmation is an awaited askConfirm');
  assert.ok(sources.length > 100, 'the scan read the UI');
  console.log('confirm-unit: ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
