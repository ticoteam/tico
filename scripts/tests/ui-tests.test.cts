import type {Api} from '../ui-tests.cts';
const {test}: typeof import('node:test') = require('node:test');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const vm: typeof import('node:vm') = require('node:vm');
const {spawnSync}: typeof import('node:child_process') = require('node:child_process');
const {EventEmitter}: typeof import('node:events') = require('node:events');
// browser.cts is TypeScript: the vm below runs its source with the types stripped (Node's own stripper, as --require does).
const {stripTypeScriptTypes}: typeof import('node:module') = require('node:module');
type Emitter = import('node:events').EventEmitter;
const {resources, runScripts, sampleCpu, stopTree, stoppedReport} = require('../ui-tests.cts') as Api;

test('frame timing scripts run alone after the parallel pool, within the selected files', async () => {
  const active = new Set<string>(), started: string[] = [], finished: string[] = [];
  let peak = 0;
  const run = async (file: string): Promise<void> => {
    if (file === 'dot-perf.cjs') assert.equal(active.size, 0, 'frame timing has no competing script');
    else assert.equal(active.has('dot-perf.cjs'), false, 'ordinary scripts never overlap frame timing');
    active.add(file); started.push(file); peak = Math.max(peak, active.size);
    await new Promise(resolve => setImmediate(resolve));
    active.delete(file); finished.push(file);
  };
  const files = ['dot-perf.cjs', 'a.cjs', 'b.cjs', 'c.cjs'];
  await runScripts(files, 2, run);
  assert.equal(peak, 2, 'ordinary scripts still use the parallel pool');
  assert.deepEqual(started, ['a.cjs', 'b.cjs', 'c.cjs', 'dot-perf.cjs']);
  assert.equal(finished.at(-1), 'dot-perf.cjs');
  assert.deepEqual([...started].sort(), [...files].sort(), 'the selection is unchanged');
  started.length = 0;
  await runScripts(['a.cjs'], 4, run);
  assert.deepEqual(started, ['a.cjs'], 'a shard without frame timing never adds it');
});

test('one CPU-idle policy, explicit overrides and shared context/page timeouts', async () => {
  const counters = (user: number, idle: number) => Array.from({length: 12}, () => ({times: {user, nice: 0, sys: 0, idle, irq: 0}}));
  const snapshots = [counters(10000, 50000), counters(10070, 50030)];
  const sampled = await sampleCpu(() => snapshots.shift()!, async (ms: number) => assert.equal(ms, 1000));
  assert.deepEqual(sampled, {cores: 12, idleFraction: 0.3});
  assert.equal(snapshots.length, 0);
  const spare = resources({}, sampled.cores, sampled.idleFraction);
  assert.equal(spare.jobs, 3);
  assert.ok(Math.abs(spare.slowdown - 5 / 3) < 1e-12);
  assert.equal(spare.timeout, 200);
  assert.deepEqual(resources({}, 12, 1), {jobs: 4, slowdown: 1, timeout: 120});
  assert.deepEqual(resources({}, 12, 0.1), {jobs: 1, slowdown: 4, timeout: 480});
  assert.deepEqual(resources({}, 12, 0), {jobs: 1, slowdown: 4, timeout: 480});
  assert.deepEqual(resources({TICO_UI_JOBS: '3', TICO_UI_SLOWDOWN: '2.5'}, 12, 0.1), {jobs: 3, slowdown: 2.5, timeout: 300});
  assert.equal(resources({TICO_UI_SCRIPT_TIMEOUT: '30', TICO_UI_SLOWDOWN: '2'}, 12, 1).timeout, 60);
  assert.throws(() => resources({TICO_UI_SCRIPT_TIMEOUT: '0'}, 12, 1), /TICO_UI_SCRIPT_TIMEOUT/);

  const support = path.resolve(__dirname, '../../ui/tests/support');
  for (const [value, expected] of [[undefined, 1], ['2.5', 2.5]] as [string | undefined, number][]) {
    const env = {...process.env};
    delete env.TICO_UI_SLOWDOWN;
    if (value !== undefined) env.TICO_UI_SLOWDOWN = value;
    const result = spawnSync(process.execPath, ['-e', `
      const {SLOWDOWN,t,applyTimeouts}=require(${JSON.stringify(path.join(support, 'load.cts'))});
      const calls=[]; applyTimeouts({setDefaultTimeout:n=>calls.push(n),setDefaultNavigationTimeout:n=>calls.push(n)});
      console.log(JSON.stringify({SLOWDOWN,rounded:t(101),calls}));`], {env, encoding: 'utf8'});
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout), {SLOWDOWN: expected, rounded: Math.round(101 * expected),
                                               calls: [30000 * expected, 30000 * expected]});
  }

  const calls: [string, number][] = [];
  const page = () => ({setDefaultTimeout: (n: number) => calls.push(['page', n]),
                      setDefaultNavigationTimeout: (n: number) => calls.push(['navigation', n])});
  const context = () => {
    let onPage: (p: unknown) => void;
    return {setDefaultTimeout: (n: number) => calls.push(['context', n]),
            setDefaultNavigationTimeout: (n: number) => calls.push(['context-navigation', n]),
            on: (event: string, callback: (p: unknown) => void) => { assert.equal(event, 'page'); onPage = callback; },
            newPage: async () => { const p = page(); onPage(p); return p; }};
  };
  const engine = () => ({launch: async () => ({newContext: async () => context(),
                                            newPage: async function (this: {newContext: () => Promise<{newPage: () => Promise<unknown>}>}) { return (await this.newContext()).newPage(); }}),
                        launchPersistentContext: async () => context()});
  const playwright = {chromium: engine(), firefox: engine(), webkit: engine()};
  vm.runInNewContext(stripTypeScriptTypes(fs.readFileSync(path.join(support, 'browser.cts'), 'utf8'), {mode: 'strip'}), {
    require: (name: string) => name === 'playwright' ? playwright : {
      applyTimeouts: (p: {setDefaultTimeout: (n: number) => void; setDefaultNavigationTimeout: (n: number) => void}) => { p.setDefaultTimeout(60000); p.setDefaultNavigationTimeout(60000); }, t: (ms: number) => ms},
    setTimeout, clearTimeout, console
  });
  const browser = await playwright.chromium.launch();
  await browser.newPage();
  assert.deepEqual(calls, [['context', 60000], ['context-navigation', 60000], ['page', 60000], ['navigation', 60000]]);
  calls.length = 0;
  const ctx = await browser.newContext();
  await ctx.newPage();
  await ctx.newPage(); // includes popups/other pages: each gets its defaults before the script uses it
  assert.equal(calls.length, 6);
  calls.length = 0;
  // @ts-expect-error the fake engine ignores its profile argument
  await playwright.chromium.launchPersistentContext('synthetic-profile');
  assert.deepEqual(calls, [['context', 60000], ['context-navigation', 60000]]);
});


test('newPage and page.close fail with their name instead of waiting forever', {timeout: 30000}, async () => {
  const never = (): Promise<never> => new Promise(() => {});
  const page = {setDefaultTimeout() {}, setDefaultNavigationTimeout() {}, close: never};
  const context: FakeContext = {setDefaultTimeout() {}, setDefaultNavigationTimeout() {}, on: (event: string, callback: unknown) => { context.onPage = callback as NonNullable<FakeContext['onPage']>; },
                   newPage: never};
  const engine = () => ({launch: async () => ({newContext: async () => context}), launchPersistentContext: async () => context});
  const playwright = {chromium: engine(), firefox: engine(), webkit: engine()};
  const errors: string[] = [];
  vm.runInNewContext(stripTypeScriptTypes(fs.readFileSync(path.resolve(__dirname, '../../ui/tests/support/browser.cts'), 'utf8'), {mode: 'strip'}), {
    require: (name: string) => name === 'playwright' ? playwright : {applyTimeouts() {}, t: () => 50},
    setTimeout, clearTimeout, console: {error: (message: string) => errors.push(message)}
  });
  const ctx = await (await playwright.chromium.launch()).newContext();
  await assert.rejects(ctx.newPage(), /browserContext\.newPage did not finish in 0\.05s/);
  ctx.onPage!(page);
  await assert.rejects(page.close(), /page\.close did not finish in 0\.05s/);
  assert.deepEqual(errors, ['browserContext.newPage did not finish in 0.05s', 'page.close did not finish in 0.05s']);
  const quick = {setDefaultTimeout() {}, setDefaultNavigationTimeout() {}, close: async () => 'closed'};
  ctx.onPage!(quick);
  assert.equal(await quick.close(), 'closed');
});

interface FakeStream extends Emitter {
  destroy?: () => void;
}
interface FakeChild extends Emitter {
  pid: number;
  stdout: FakeStream;
  stderr: FakeStream;
}
interface FakeContext {
  onPage?: (p: unknown) => void;
  setDefaultTimeout(n?: number): void;
  setDefaultNavigationTimeout(n?: number): void;
  on(event: string, callback: unknown): void;
  newPage(): Promise<unknown>;
}
interface ProcRow {
  pid: number;
  ppid: number;
  start: string;
  command: string;
}

// The runner loaded with a fake child_process and platform: no real process is started, frozen or killed.
function fakeRunner({platform = 'darwin', execFileSync = () => { throw new Error('ps must not run'); }}: {platform?: string; execFileSync?: (cmd: string, args: string[]) => unknown} = {}) {
  const child = new EventEmitter() as unknown as FakeChild;
  child.pid = 123;
  for (const pipe of ['stdout', 'stderr'] as const) { child[pipe] = new EventEmitter() as unknown as FakeStream; child[pipe].destroy = () => {}; }
  const sandbox: {exports: Api} = {exports: {} as Api};
  // The runner is TypeScript: the vm runs its source with the types stripped, as Node does when it runs the file.
  vm.runInNewContext(stripTypeScriptTypes(fs.readFileSync(path.resolve(__dirname, '../ui-tests.cts'), 'utf8'), {mode: 'strip'}), {
    module: sandbox, __dirname: path.resolve(__dirname, '..'), setTimeout, clearTimeout, console, Buffer,
    require: (name: string) => name === 'node:child_process' ? {spawn: () => child, execFileSync} : require(name),
    process: {platform, env: {}, execPath: 'node', kill: () => { throw new Error('no real signals'); }}
  });
  return {child, runner: sandbox.exports};
}
const row = (pid: number, ppid: number, start: string, command = 'fixture'): ProcRow => ({pid, ppid, start, command});
const settles = <T,>(promise: Promise<T>, ms: number): Promise<T> => Promise.race([promise, new Promise<T>(r => setTimeout(() => r('unsettled' as T), ms))]);

test('a pid that left the tree and was reused by another process is never stopped', () => {
  // Freezing 101 fails (it exited); the next list shows 101 reused by an unrelated process with a child of its own.
  let scans = 0;
  const sent: [number, string][] = [];
  const reaped = stopTree(100, {
    list: () => ++scans === 1 ? [row(100, 1, 'T0'), row(101, 100, 'T1', 'owned worker')]
                              : [row(100, 1, 'T0'), row(101, 1, 'T9', 'unrelated'), row(102, 101, 'T9', 'unrelated child')],
    signal: (pid: number, name: string) => {
      sent.push([pid, name]);
      if (pid === 101 && name === 'SIGSTOP') throw Object.assign(new Error('gone'), {code: 'ESRCH'});
    }});
  assert.deepEqual(sent.filter(([pid]) => pid !== 100), [[101, 'SIGSTOP']]);
  assert.deepEqual(reaped.map(p => p.pid), [100]);
  // Frozen 201 is killed by someone else and its pid reused (same pid, new start time) before the final kill.
  scans = 0;
  sent.length = 0;
  const reused = stopTree(200, {
    list: () => ++scans <= 2 ? [row(200, 1, 'T0'), row(201, 200, 'T1', 'owned worker')]
                             : [row(200, 1, 'T0'), row(201, 1, 'T9', 'unrelated'), row(202, 201, 'T9', 'unrelated child')],
    signal: (pid: number, name: string) => sent.push([pid, name])});
  assert.deepEqual(sent, [[200, 'SIGSTOP'], [201, 'SIGSTOP'], [200, 'SIGKILL']]);
  assert.deepEqual(reused.map(p => p.pid), [200]);
  // A stable tree is all stopped, and a sibling is untouched.
  sent.length = 0;
  const stable = stopTree(300, {list: () => [row(300, 1, 'T0'), row(301, 300, 'T1'), row(302, 301, 'T2'), row(900, 1, 'T3')],
                                signal: (pid: number, name: string) => sent.push([pid, name])});
  assert.deepEqual(stable.map(p => p.pid).sort(), [300, 301, 302]);
  assert.deepEqual(stable.errors, []);
  assert.equal(sent.some(([pid]) => pid === 900), false);
});

test('a frozen pid is never killed when its identity cannot be read again', () => {
  // 101 is frozen, then killed by someone else and its pid reused; every later process list fails.
  for (const failAfter of [1, 2]) {
    let scans = 0;
    const sent: [number, string][] = [];
    const cleanup = stopTree(100, {
      list: () => { if (++scans > failAfter) throw new Error('synthetic ps timeout'); return [row(100, 1, 'T0'), row(101, 100, 'T1', 'owned worker')]; },
      signal: (pid: number, name: string) => sent.push([pid, name])});
    assert.deepEqual(sent.filter(([pid, name]) => pid === 101 && name === 'SIGKILL'), [], `list fails after scan ${failAfter}`);
    assert.ok(cleanup.errors.some(e => /could not list processes/.test(e)));
    assert.ok(cleanup.errors.some(e => /left stopped/.test(e)), cleanup.errors.join('; '));
  }
});

test('a process list that fails is reported as incomplete cleanup, with the error', () => {
  const sent: [number, string][] = [];
  const cleanup = stopTree(100, {list: () => { throw new Error('synthetic ps timeout'); }, signal: (pid: number, name: string) => sent.push([pid, name])});
  const report = stoppedReport({pending: [], calls: [], stopped: cleanup});
  assert.match(report, /cleanup incomplete: could not list processes: synthetic ps timeout/);
  assert.deepEqual([...new Set(sent.map(([pid]) => pid))], [100], 'only the script itself, which the runner owns');
});


test('the browser call log is bounded while it streams and in the report, and URLs and secrets are redacted',
     {timeout: 10000}, async () => {
  const {child, runner} = fakeRunner();
  const run = runner.runScript('synthetic.cjs', {preload: 'synthetic', timeout: 0.01,
    stop: () => { child.emit('exit', null); return [{pid: 123, command: `fixture --token=SYNTH_ONLY_SECRET ${'a'.repeat(5000)}`}]; }});
  const starts = Array.from({length: 1000}, (_, i) => `2026-10-23T00:00:00Z pw:api => page.click${i} started`);
  const url = `https://acme.example/app/page?token=SYNTH_ONLY_SECRET&x=${'x'.repeat(200000)}`;
  child.stderr.emit('data', Buffer.from(`${starts.join('\n')}\n2026-10-23T00:00:00Z pw:api navigating to "${url}"\n`));
  // A line that never ends: held only up to its bound however much arrives.
  child.stderr.emit('data', Buffer.from('2026-10-23T00:00:00Z pw:api => page.fill started api_key=SYNTH_ONLY_SECRET '));
  for (let i = 0; i < 20; i++) child.stderr.emit('data', Buffer.from('y'.repeat(100000)));
  const result = await settles(run, 3000);
  assert.notEqual(result, 'unsettled');
  const report = runner.stoppedReport(result);
  assert.ok(result.pending.length <= 20, `${result.pending.length} pending calls held`);
  assert.ok(result.calls.length <= 10 && result.calls.every(call => call.length <= 260), 'each held call is short');
  assert.ok(report.length <= 8300, `report is ${report.length} characters`);
  assert.equal(report.includes('SYNTH_ONLY_SECRET'), false);
  assert.match(report, /navigating to "https:\/\/acme\.example\/app\/page\s/);
  assert.match(report, /waiting on: .*page\.click999/);
  assert.match(report, /--token=\[redacted\]/);
});

test('--group e2e lists the end-to-end journeys, and an unknown group is refused', () => {
  const script = path.join(__dirname, '..', 'ui-tests.cts');
  const list = spawnSync(process.execPath, [script, '--group', 'e2e', '--list'], {encoding: 'utf8'});
  assert.equal(list.status, 0, list.stderr);
  const names = list.stdout.trim().split('\n');
  assert.ok(names.includes('e2e-core') && names.includes('e2e-roles'), list.stdout);
  assert.ok(names.every(name => name.startsWith('e2e-')), list.stdout);
  const bad = spawnSync(process.execPath, [script, '--group', 'nope', '--list'], {encoding: 'utf8'});
  assert.equal(bad.status, 1);
  assert.match(bad.stderr, /--group must be one of: e2e/);
});
