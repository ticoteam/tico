// No browser: a task that fails to load says so (ui/app/task-modal.ts). taskModalOpen reads the task by its id; a read
// that fails opens no window and shows a toast: "Task not found" for a 404 or 403, otherwise the error with Retry, and
// Retry opens the task once the read works. taskModalShow, opened from list data, closes the window when the full read
// fails, and says why the same way. The classic scripts are compiled with esbuild's transform and run in one vm context,
// with the shared globals they read stubbed (the pipeline-view-unit.cts pattern).
// Run: node ui/tests/task-modal-open-unit.cts
'use strict';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const vm: typeof import('node:vm') = require('node:vm');
const {transformSync} = require('esbuild') as typeof import('esbuild');

const APP = path.join(__dirname, '..', 'app');
type Toast = {msg: string; isErr: boolean | undefined; action: {label: string; run: () => unknown} | null | undefined; opts: {host?: unknown} | undefined};
const toasts: Toast[] = [];
const reads: string[] = [];
let answer: () => Promise<unknown> = () => Promise.resolve(null);
const noop = () => {};
const ctx = vm.createContext({
  window: {addEventListener: noop},
  document: {addEventListener: noop, activeElement: null, body: {classList: {add: noop, remove: noop}}, createElement: () => ({})},
  localStorage: {getItem: () => null, setItem: noop, removeItem: noop},
  location: {hash: '#/tasks'},
  history: {state: null, pushState: noop, back: noop},
  SEND_WAY_PAINT: new Set(),
  ENTRY_JUMP: null,
  TASKS_ST: {},
  S: {route: '#/tasks'},
  LIVE: {after: null},              // live.ts: the change number a task's own stream resumes from
  get: (p: string) => { reads.push(p); return answer(); },
  v2Get: (p: string) => { reads.push(p); return answer().catch(() => null); },
  toast: (msg: string, isErr?: boolean, action?: Toast['action'], opts?: Toast['opts']) => { toasts.push({msg, isErr, action, opts}); },
});
for (const file of ['errors.ts', 'task-modal.ts']) {
  vm.runInContext(transformSync(fs.readFileSync(path.join(APP, file), 'utf8'), {loader: 'ts', target: 'es2022'}).code, ctx, {filename: file});
}
// The drawing around the read, stubbed after the load since some of it is defined in task-modal.ts itself.
Object.assign(ctx, {
  $: () => null,
  esc: (s: unknown) => String(s ?? ''),
  loadHarnessRows: () => Promise.resolve(),
  hubModalHTML: () => '',
  liveTasksSync: noop, taskLiveStop: noop, taskLiveFocusKeep: noop, taskLiveFocusRestore: noop, taskFormAdopt: noop,
  taskFormRestore: noop, taskFormCapture: () => ({fields: [], folds: []}), taskModalBind: noop, taskPropFocus: noop,
  taskMenuSettled: () => Promise.resolve(),
  taskLiveEdited: noop, taskLiveStart: noop, taskRailLoad: () => Promise.resolve(), taskChatLoad: () => Promise.resolve(),
  tfLoad: () => Promise.resolve(), taskRunsLoad: () => Promise.resolve(), githubNudgeTask: () => Promise.resolve(),
  decideTaskOpen: () => Promise.resolve(false),     // decide.ts: these tasks do not wait on the person, so the page opens
});
const VmError = vm.runInContext('Error', ctx) as ErrorConstructor;
const failure = (status: number, message: string) => Object.assign(new VmError(message), {status, body: {error: message}});
const TASK = {id: 't1', title: 'Saved carts', version: 1, status: 'open'};

// A dialog the test can watch: showModal opens it, close closes it.
const dialog = () => {
  const d = {open: false, opened: 0, closed: 0, dataset: {} as Record<string, string>, innerHTML: '', scrollTop: 0,
    classList: {toggle: noop, contains: () => false, add: noop, remove: noop},
    contains: () => false, matches: () => false,
    showModal() { d.open = true; d.opened++; }, close() { d.open = false; d.closed++; }};
  return d;
};
const open = vm.runInContext('taskModalOpen', ctx) as (id: string, d?: unknown) => Promise<void>;
const show = vm.runInContext('taskModalShow', ctx) as (task: unknown, d?: unknown, opts?: unknown) => Promise<void>;
const shown: unknown[] = [];
ctx.taskModalShow = (task: unknown) => { shown.push(task); return Promise.resolve(); };   // what taskModalOpen calls on a read that works

(async () => {
  // ---- 404 and 403: not found, no Retry, nothing opens
  for (const status of [404, 403]) {
    toasts.length = 0; shown.length = 0;
    answer = () => Promise.reject(failure(status, 'not_found'));
    await open('missing-' + status);
    assert.equal(toasts.length, 1, `a ${status} says so`);
    assert.equal(toasts[0]!.msg, 'Task not found');
    assert.equal(toasts[0]!.isErr, true);
    assert.ok(!toasts[0]!.action, 'not found has nothing to retry');
    assert.equal(shown.length, 0, `a ${status} opens no window`);
  }

  // ---- 500: the error, with Retry; Retry opens the task once the read works
  toasts.length = 0; shown.length = 0;
  answer = () => Promise.reject(failure(500, 'The hub had a problem'));
  await open('t1');
  assert.equal(toasts.length, 1, 'a 500 says so');
  assert.equal(toasts[0]!.msg, 'The hub had a problem');
  assert.equal(toasts[0]!.isErr, true);
  assert.equal(toasts[0]!.action?.label, 'Retry');
  assert.equal(shown.length, 0, 'a 500 opens no window');
  answer = () => Promise.resolve({task: TASK});
  await toasts[0]!.action!.run();
  assert.equal(shown.length, 1, 'Retry opens the task');
  assert.equal((shown[0] as {id: string}).id, 't1');

  // ---- a network error (a TypeError from fetch) reads as its message, with Retry
  toasts.length = 0; shown.length = 0;
  answer = () => Promise.reject(new (vm.runInContext('TypeError', ctx) as ErrorConstructor)('Failed to fetch'));
  await open('t1');
  assert.equal(toasts.length, 1);
  assert.equal(toasts[0]!.msg, 'Failed to fetch');
  assert.equal(toasts[0]!.action?.label, 'Retry');
  assert.equal(shown.length, 0);

  // ---- opened from inside an open task window (a related or sub-task row): the toast goes on that window, since the
  // page behind a modal takes no clicks and Retry could not be pressed there
  for (const [status, retry] of [[404, false], [500, true]] as const) {
    toasts.length = 0; shown.length = 0;
    const d = dialog(); d.open = true;
    answer = () => Promise.reject(failure(status, 'The hub had a problem'));
    await open('t2', d);
    assert.equal(toasts.length, 1, `a ${status} from an open window says so`);
    assert.equal(toasts[0]!.opts?.host, d, `a ${status} toast sits on the open window`);
    assert.equal(toasts[0]!.action?.label, retry ? 'Retry' : undefined);
    assert.equal(shown.length, 0);
  }
  // ---- with no window open, the toast goes on the page
  toasts.length = 0;
  answer = () => Promise.reject(failure(500, 'The hub had a problem'));
  await open('t1', dialog());
  assert.equal(toasts[0]!.opts?.host, undefined, 'a closed window is no host');

  // ---- a read that fails after the person moved on (another page, another task) says nothing
  {
    const S = ctx.S as {route: string};
    toasts.length = 0; shown.length = 0;
    let fail: (e: unknown) => void = () => {};
    answer = () => new Promise((_, reject) => { fail = reject; });
    const late = open('t1');
    S.route = '#/goals';
    fail(failure(500, 'The hub had a problem'));
    await late;
    S.route = '#/tasks';
    assert.equal(toasts.length, 0, 'a failure after a change of page says nothing');
    const first = open('t1');
    const failFirst = fail;
    answer = () => Promise.resolve({task: TASK});
    await open('t2');
    failFirst(failure(500, 'The hub had a problem'));
    await first;
    assert.equal(toasts.length, 0, 'a failure after another task opened says nothing');
  }

  // ---- a read that works opens the task and says nothing
  toasts.length = 0; shown.length = 0;
  answer = () => Promise.resolve({task: TASK});
  await open('t1');
  assert.equal(toasts.length, 0);
  assert.equal(shown.length, 1);

  // ---- opened from list data, the full read fails: the window closes and says why
  for (const [status, message, retry] of [[404, 'not_found', false], [500, 'The hub had a problem', true]] as const) {
    toasts.length = 0;
    const d = dialog();
    answer = () => Promise.reject(failure(status, message));
    await show({...TASK}, d);
    assert.equal(d.opened, 1, 'the list row opens the window at once');
    assert.equal(d.open, false, `a ${status} closes it`);
    assert.equal(toasts.length, 1, `a ${status} says so as it closes`);
    assert.equal(toasts[0]!.msg, retry ? message : 'Task not found');
    assert.equal(toasts[0]!.isErr, true);
    assert.equal(toasts[0]!.action?.label, retry ? 'Retry' : undefined);
  }
  // Retry after the window closed opens the task by its id.
  shown.length = 0;
  answer = () => Promise.resolve({task: TASK});
  await toasts[0]!.action!.run();
  assert.equal(shown.length, 1, 'Retry opens the task');

  // ---- opened from list data, the full read works: no toast
  toasts.length = 0;
  {
    const d = dialog();
    await show({...TASK}, d);
    assert.equal(toasts.length, 0);
  }
  console.log('task-modal-open-unit: ok');
})().catch(e => { console.error(e); process.exit(1); });
