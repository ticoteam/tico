const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {spawnSync} = require('node:child_process');
const os = require('node:os');
const {EventEmitter} = require('node:events');
const {resources, runScript, sampleCpu, stopTree, stoppedReport} = require('../ui-tests.cjs');

test('one CPU-idle policy, explicit overrides and shared context/page timeouts', async () => {
  const counters = (user, idle) => Array.from({length: 12}, () => ({times: {user, nice: 0, sys: 0, idle, irq: 0}}));
  const snapshots = [counters(10000, 50000), counters(10070, 50030)];
  const sampled = await sampleCpu(() => snapshots.shift(), async ms => assert.equal(ms, 1000));
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
  for (const [value, expected] of [[undefined, 1], ['2.5', 2.5]]) {
    const env = {...process.env};
    delete env.TICO_UI_SLOWDOWN;
    if (value !== undefined) env.TICO_UI_SLOWDOWN = value;
    const result = spawnSync(process.execPath, ['-e', `
      const {SLOWDOWN,t,applyTimeouts}=require(${JSON.stringify(path.join(support, 'load.cjs'))});
      const calls=[]; applyTimeouts({setDefaultTimeout:n=>calls.push(n),setDefaultNavigationTimeout:n=>calls.push(n)});
      console.log(JSON.stringify({SLOWDOWN,rounded:t(101),calls}));`], {env, encoding: 'utf8'});
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout), {SLOWDOWN: expected, rounded: Math.round(101 * expected),
                                               calls: [30000 * expected, 30000 * expected]});
  }

  const calls = [];
  const page = () => ({setDefaultTimeout: n => calls.push(['page', n]),
                      setDefaultNavigationTimeout: n => calls.push(['navigation', n])});
  const context = () => {
    let onPage;
    return {setDefaultTimeout: n => calls.push(['context', n]),
            setDefaultNavigationTimeout: n => calls.push(['context-navigation', n]),
            on: (event, callback) => { assert.equal(event, 'page'); onPage = callback; },
            newPage: async () => { const p = page(); onPage(p); return p; }};
  };
  const engine = () => ({launch: async () => ({newContext: async () => context(),
                                            newPage: async function () { return (await this.newContext()).newPage(); }}),
                        launchPersistentContext: async () => context()});
  const playwright = {chromium: engine(), firefox: engine(), webkit: engine()};
  vm.runInNewContext(fs.readFileSync(path.join(support, 'browser.cjs'), 'utf8'), {
    require: name => name === 'playwright' ? playwright : {
      applyTimeouts: p => { p.setDefaultTimeout(60000); p.setDefaultNavigationTimeout(60000); }, t: ms => ms},
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
  await playwright.chromium.launchPersistentContext('synthetic-profile');
  assert.deepEqual(calls, [['context', 60000], ['context-navigation', 60000]]);
});

test('a hung browser script is named with its pending call; it and everything it started stop, one that ends does not',
     {timeout: 30000}, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ui-tests-'));
  const alive = pid => { try { process.kill(pid, 0); return true; } catch { return false; } };
  // A helper's identity (pid and start time) is taken while the hung script is still its parent, so cleanup below can
  // tell it from a later process that reuses the pid.
  const ps = pid => (spawnSync('ps', ['-o', 'ppid=,lstart=', '-p', String(pid)], {encoding: 'utf8'}).stdout || '').trim();
  const started = pid => ps(pid).replace(/^\d+\s+/, '');
  const owned = new Map();
  let helper;
  try {
    const preload = path.join(dir, 'preload.cjs');
    fs.writeFileSync(preload, '');
    // Like a stalled Playwright script: a detached "browser" (its own process group, as Playwright launches one),
    // a "server" child, Playwright's call log on stderr, then a call that never returns.
    fs.writeFileSync(path.join(dir, 'hangs.cjs'), `
      const {spawn} = require('node:child_process');
      const sleep = ['-e', 'setInterval(() => {}, 1000)'];
      const browser = spawn(process.execPath, sleep, {detached: true, stdio: 'ignore'});
      const server = spawn(process.execPath, sleep, {stdio: 'ignore'});
      console.log('started', browser.pid, server.pid);
      require('node:fs').writeFileSync(${JSON.stringify(path.join(dir, 'started'))}, [process.pid, browser.pid, server.pid].join(' '));
      console.error('2026-10-22T09:31:00.000Z pw:api => page.goto started');
      console.error('2026-10-22T09:31:00.100Z pw:api <= page.goto succeeded');
      console.error('2026-10-22T09:31:00.200Z pw:api => page.click started');
      setInterval(() => {}, 1000);`);
    fs.writeFileSync(path.join(dir, 'ends.cjs'), "console.log('PASS');");
    const running = runScript(path.join(dir, 'hangs.cjs'), {env: {...process.env, DEBUG: undefined}, timeout: 1, preload});
    const marker = path.join(dir, 'started');
    for (let i = 0; i < 100 && !fs.existsSync(marker); i++) await new Promise(r => setTimeout(r, 10));
    if (fs.existsSync(marker)) {
      const [parent, ...pids] = fs.readFileSync(marker, 'utf8').split(' ').map(Number);
      for (const pid of pids) { const [ppid, ...start] = ps(pid).split(/\s+/); if (Number(ppid) === parent) owned.set(pid, start.join(' ')); }
    }
    const hung = await running;
    helper = hung.out.match(/started (\d+) (\d+)/).slice(1).map(Number);
    assert.deepEqual([...owned.keys()].sort(), [...helper].sort(), 'each helper identity was taken while it was ours');
    assert.equal(hung.timedOut, true);
    assert.notEqual(hung.code, 0);
    assert.ok(hung.secs < 10, `stopped after ${hung.secs}s`);
    assert.doesNotMatch(hung.out, /pw:api/);
    assert.deepEqual(hung.pending, ['page.click']);
    assert.match(stoppedReport(hung), /waiting on: page\.click/);
    const stoppedPids = hung.stopped.map(p => p.pid);
    for (const pid of helper) assert.ok(stoppedPids.includes(pid), `${pid} in ${stoppedPids}`);
    for (let i = 0; i < 50 && helper.some(alive); i++) await new Promise(r => setTimeout(r, 100));
    assert.deepEqual(helper.filter(alive), [], 'no orphan browser or server is left');
    const ended = await runScript(path.join(dir, 'ends.cjs'), {timeout: 30, preload});
    assert.deepEqual([ended.code, ended.timedOut, ended.out.trim(), [...ended.stopped]], [0, false, 'PASS', []]);
  } finally {
    // Only a helper this test started: same pid and start time as when the hung script was its parent.
    for (const [pid, start] of owned) if (started(pid).replace(/\s+/g, ' ') === start) process.kill(pid, 'SIGKILL');
    fs.rmSync(dir, {recursive: true, force: true});
  }
});

test('newPage and page.close fail with their name instead of waiting forever', {timeout: 30000}, async () => {
  const never = () => new Promise(() => {});
  const page = {setDefaultTimeout() {}, setDefaultNavigationTimeout() {}, close: never};
  const context = {setDefaultTimeout() {}, setDefaultNavigationTimeout() {}, on: (event, callback) => { context.onPage = callback; },
                   newPage: never};
  const engine = () => ({launch: async () => ({newContext: async () => context}), launchPersistentContext: async () => context});
  const playwright = {chromium: engine(), firefox: engine(), webkit: engine()};
  const errors = [];
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../../ui/tests/support/browser.cjs'), 'utf8'), {
    require: name => name === 'playwright' ? playwright : {applyTimeouts() {}, t: () => 50},
    setTimeout, clearTimeout, console: {error: message => errors.push(message)}
  });
  const ctx = await (await playwright.chromium.launch()).newContext();
  await assert.rejects(ctx.newPage(), /browserContext\.newPage did not finish in 0\.05s/);
  ctx.onPage(page);
  await assert.rejects(page.close(), /page\.close did not finish in 0\.05s/);
  assert.deepEqual(errors, ['browserContext.newPage did not finish in 0.05s', 'page.close did not finish in 0.05s']);
  const quick = {setDefaultTimeout() {}, setDefaultNavigationTimeout() {}, close: async () => 'closed'};
  ctx.onPage(quick);
  assert.equal(await quick.close(), 'closed');
});

// The runner loaded with a fake child_process and platform: no real process is started, frozen or killed.
function fakeRunner({platform = 'darwin', execFileSync = () => { throw new Error('ps must not run'); }} = {}) {
  const child = new EventEmitter();
  child.pid = 123;
  for (const pipe of ['stdout', 'stderr']) { child[pipe] = new EventEmitter(); child[pipe].destroy = () => {}; }
  const sandbox = {exports: {}};
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../ui-tests.cjs'), 'utf8'), {
    module: sandbox, __dirname: path.resolve(__dirname, '..'), setTimeout, clearTimeout, console, Buffer,
    require: name => name === 'node:child_process' ? {spawn: () => child, execFileSync} : require(name),
    process: {platform, env: {}, execPath: 'node', kill: () => { throw new Error('no real signals'); }}
  });
  return {child, runner: sandbox.exports};
}
const row = (pid, ppid, start, command = 'fixture') => ({pid, ppid, start, command});
const settles = (promise, ms) => Promise.race([promise, new Promise(r => setTimeout(() => r('unsettled'), ms))]);

test('a pid that left the tree and was reused by another process is never stopped', () => {
  // Freezing 101 fails (it exited); the next list shows 101 reused by an unrelated process with a child of its own.
  let scans = 0;
  const sent = [];
  const reaped = stopTree(100, {
    list: () => ++scans === 1 ? [row(100, 1, 'T0'), row(101, 100, 'T1', 'owned worker')]
                              : [row(100, 1, 'T0'), row(101, 1, 'T9', 'unrelated'), row(102, 101, 'T9', 'unrelated child')],
    signal: (pid, name) => {
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
    signal: (pid, name) => sent.push([pid, name])});
  assert.deepEqual(sent, [[200, 'SIGSTOP'], [201, 'SIGSTOP'], [200, 'SIGKILL']]);
  assert.deepEqual(reused.map(p => p.pid), [200]);
  // A stable tree is all stopped, and a sibling is untouched.
  sent.length = 0;
  const stable = stopTree(300, {list: () => [row(300, 1, 'T0'), row(301, 300, 'T1'), row(302, 301, 'T2'), row(900, 1, 'T3')],
                                signal: (pid, name) => sent.push([pid, name])});
  assert.deepEqual(stable.map(p => p.pid).sort(), [300, 301, 302]);
  assert.deepEqual(stable.errors, []);
  assert.equal(sent.some(([pid]) => pid === 900), false);
});

test('a frozen pid is never killed when its identity cannot be read again', () => {
  // 101 is frozen, then killed by someone else and its pid reused; every later process list fails.
  for (const failAfter of [1, 2]) {
    let scans = 0;
    const sent = [];
    const cleanup = stopTree(100, {
      list: () => { if (++scans > failAfter) throw new Error('synthetic ps timeout'); return [row(100, 1, 'T0'), row(101, 100, 'T1', 'owned worker')]; },
      signal: (pid, name) => sent.push([pid, name])});
    assert.deepEqual(sent.filter(([pid, name]) => pid === 101 && name === 'SIGKILL'), [], `list fails after scan ${failAfter}`);
    assert.ok(cleanup.errors.some(e => /could not list processes/.test(e)));
    assert.ok(cleanup.errors.some(e => /left stopped/.test(e)), cleanup.errors.join('; '));
  }
});

test('a process list that fails is reported as incomplete cleanup, with the error', () => {
  const sent = [];
  const cleanup = stopTree(100, {list: () => { throw new Error('synthetic ps timeout'); }, signal: (pid, name) => sent.push([pid, name])});
  const report = stoppedReport({pending: [], calls: [], stopped: cleanup});
  assert.match(report, /cleanup incomplete: could not list processes: synthetic ps timeout/);
  assert.deepEqual([...new Set(sent.map(([pid]) => pid))], [100], 'only the script itself, which the runner owns');
});

test('a stop that cannot kill the script (taskkill denied) still ends the run in bounded time', {timeout: 10000}, async () => {
  const attempts = [];
  const {runner} = fakeRunner({platform: 'win32', execFileSync: (cmd, args) => {
    attempts.push([cmd, ...args]);
    throw Object.assign(new Error('Access is denied.'), {code: 'EPERM'});
  }});
  const result = await settles(runner.runScript('synthetic.cjs', {preload: 'synthetic', timeout: 0.01, grace: 30}), 3000);
  assert.notEqual(result, 'unsettled', 'the run ended although the script never exited');
  assert.deepEqual(attempts, [['taskkill', '/PID', '123', '/T', '/F']]);
  assert.equal(result.timedOut, true);
  assert.equal(result.code, null);
  const report = runner.stoppedReport(result);
  assert.match(report, /could not stop 1 process\(es\):\n {2}123 : EPERM/);
  assert.match(report, /cleanup incomplete: taskkill failed: EPERM; .*could not be stopped; the run ended without waiting for it/);
  // A script that was signalled but never exits (it did not die) is waited for only `grace` seconds.
  const quiet = fakeRunner();
  const started = Date.now();
  const unexited = await settles(quiet.runner.runScript('synthetic.cjs', {preload: 'synthetic', timeout: 0.01, grace: 0.2,
                                                                          stop: pid => [{pid, command: ''}]}), 3000);
  assert.notEqual(unexited, 'unsettled');
  assert.ok(Date.now() - started >= 200);
  assert.match(quiet.runner.stoppedReport(unexited), /cleanup incomplete: the script \(pid 123\) was still running 0\.2s after it was stopped/);
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
  const script = path.join(__dirname, '..', 'ui-tests.cjs');
  const list = spawnSync(process.execPath, [script, '--group', 'e2e', '--list'], {encoding: 'utf8'});
  assert.equal(list.status, 0, list.stderr);
  const names = list.stdout.trim().split('\n');
  assert.ok(names.includes('e2e-tasks') && names.includes('e2e-roles'), list.stdout);
  assert.ok(names.every(name => name.startsWith('e2e-')), list.stdout);
  const bad = spawnSync(process.execPath, [script, '--group', 'nope', '--list'], {encoding: 'utf8'});
  assert.equal(bad.status, 1);
  assert.match(bad.stderr, /--group must be one of: e2e/);
});
