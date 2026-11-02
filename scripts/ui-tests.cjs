#!/usr/bin/env node
// Runs the browser scripts in ui/tests/*.cjs a few at a time (npm run test:ui).
// Each script's output is held and printed together, so a failure reads as one block.
// Usage: node scripts/ui-tests.cjs [-j N] [--all] [--list] [name-or-path ...]   (free-core jobs, or $TICO_UI_JOBS)
// --list prints the scripts the same arguments would run, one name per line, and runs nothing (CI splits them
// across VMs). With TICO_UI_TIMINGS set, each passing script's seconds are written there as JSON {name: seconds}.
// With no names it runs CORE, the main pages and the privacy boundaries; --all runs every script (the release
// check does). A name runs that script whether or not it is in CORE. Run locally, it waits for one of the box's
// heavy-job slots first (scripts/heavy.sh). A script still running after TICO_UI_SCRIPT_TIMEOUT seconds (120, times the
// slowdown) is stopped with every process it started and fails with its name and the browser calls it was waiting on
// (or "cleanup incomplete" and why, if a process could not be stopped), so one hang does not hold the job until CI
// cancels it.
const {execFileSync, spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

async function sampleCpu(read = os.cpus, pause = ms => new Promise(resolve => setTimeout(resolve, ms))) {
  // CPU counters measure spare capacity even when macOS load average stays high.
  const before = read();
  await pause(1000);
  const after = read();
  const idle = cpus => cpus.reduce((sum, cpu) => sum + cpu.times.idle, 0);
  const total = cpus => cpus.reduce((sum, cpu) => sum + Object.values(cpu.times).reduce((a, b) => a + b, 0), 0);
  const elapsed = total(after) - total(before);
  const idleFraction = before.length === after.length && elapsed > 0
    ? Math.max(0, Math.min(1, (idle(after) - idle(before)) / elapsed)) : 0;
  return {cores: Math.max(1, after.length), idleFraction};
}

function resources(env, cores, idleFraction) {
  const clamp = (n, min, max) => Math.max(min, Math.min(max, n));
  const jobs = Number(env.TICO_UI_JOBS ?? clamp(Math.floor(cores * idleFraction), 1, 4));
  const slowdown = Number(env.TICO_UI_SLOWDOWN ?? clamp(1 / Math.max(idleFraction * 2, 0.25), 1, 4));
  const timeout = Number(env.TICO_UI_SCRIPT_TIMEOUT ?? 120) * slowdown;
  if (!Number.isInteger(jobs) || jobs < 1) throw new Error('TICO_UI_JOBS must be a positive integer');
  if (!Number.isFinite(slowdown) || slowdown <= 0) throw new Error('TICO_UI_SLOWDOWN must be a positive finite number');
  if (!Number.isFinite(timeout) || timeout <= 0) throw new Error('TICO_UI_SCRIPT_TIMEOUT must be a positive number of seconds');
  return {jobs, slowdown, timeout};
}

// Lists every process as {pid, ppid, start, command}; a stopped script's tree is found from it by parent, never by
// name. `start` (when the process started) goes with the pid as its identity, so a pid the system has handed to a new
// process is not mistaken for the one seen before.
function listProcesses() {
  return execFileSync('ps', ['-A', '-o', 'pid=,ppid=,lstart=,command='],
                      {encoding: 'utf8', timeout: 5000, env: {...process.env, LC_ALL: 'C'}}).split('\n')
    .map(line => /^\s*(\d+)\s+(\d+)\s+(\S+\s+\S+\s+\S+\s+\S+\s+\S+)\s*(.*)$/.exec(line)).filter(Boolean)
    .map(([, pid, ppid, start, command]) => ({pid: Number(pid), ppid: Number(ppid), start, command: command.trim()}));
}

// Shortens text that goes into a stopped script's report: a URL keeps its origin and path (no query, fragment or
// user:password), and anything shaped like a key, token, password or long secret is replaced.
function redact(text, max = 240) {
  const cut = String(text).slice(0, 4096)
    .replace(/\b([a-z][a-z0-9+.-]*:\/\/)(?:[^\s/?#@"'<>]*@)?([^\s/?#"'<>]*)([^\s?#"'<>]*)(?:[?#][^\s"'<>]*)?/gi, '$1$2$3')
    .replace(/((?:--?)?[\w.-]*(?:token|key|secret|passw(?:or)?d|auth\w*|cookie|session|credential|signature)[\w.-]*\s*[=:]\s*|(?<![\w-])--?[\w.-]*(?:token|key|secret|passw(?:or)?d|auth\w*|cookie|session|credential|signature)[\w.-]*\s+|\b(?:bearer|basic)\s+)((?:bearer\s+|basic\s+)?(?:"[^"]*"|'[^']*'|[^\s"',;&]+))/gi,
             '$1[redacted]')
    .replace(/[A-Za-z0-9_+=-]{32,}/g, '[redacted]');
  return cut.length > max ? `${cut.slice(0, max)}… (cut)` : cut;
}

// Stops a script and every process it started: its browser (which Playwright puts in a process group of its own),
// the browser's helpers and any server the script spawned. Each pass freezes what it finds, so nothing in the tree
// can start another process or be orphaned while the tree is read; then all of it is killed. A process counts as part
// of the tree only while it is seen with the same pid and start time as when it was found under the script, and only
// once it has actually been frozen; one that has gone is dropped, so a pid reused by another process gets nothing.
// Returns the processes it stopped, with `failed` (what could not be stopped) and `errors` (why the cleanup is
// incomplete, if it is) on the list.
function stopTree(root, {list = listProcesses, signal = (pid, name) => process.kill(pid, name), platform = process.platform,
                         taskkill = pid => execFileSync('taskkill', ['/PID', String(pid), '/T', '/F'], {stdio: 'ignore', timeout: 10000})} = {}) {
  const result = Object.assign([], {failed: [], errors: []});
  const why = error => (error && (error.code || error.message)) || String(error);
  if (platform === 'win32') {
    try { taskkill(root); result.push({pid: root, command: 'and every process under it (taskkill /T)'}); }
    catch (error) { result.failed.push({pid: root, command: '', error: why(error)}); result.errors.push(`taskkill failed: ${why(error)}`); }
    return result;
  }
  const snapshot = () => {
    try { return list(); }
    catch (error) { result.errors.push(`could not list processes: ${why(error)}`); return null; }
  };
  const frozen = new Map(); // pid -> {start, command}, each one seen under the script and frozen
  const same = (row, known) => row && row.start === known.start;
  let rows = null, rootSeen = true, complete = false;
  for (let pass = 0; pass < 5; pass++) {
    rows = snapshot();
    if (!rows) break;
    const byPid = new Map(rows.map(row => [row.pid, row]));
    if (!byPid.has(root) && !frozen.has(root)) { rootSeen = false; complete = true; break; } // it has already exited
    for (const [pid, known] of frozen) if (!same(byPid.get(pid), known)) frozen.delete(pid); // gone, maybe reused
    const tree = new Set([root, ...frozen.keys()]);
    for (let grew = true; grew;) {
      grew = false;
      for (const row of rows) if (tree.has(row.ppid) && !tree.has(row.pid)) { tree.add(row.pid); grew = true; }
    }
    let added = 0;
    for (const pid of tree) {
      if (frozen.has(pid) || result.failed.some(p => p.pid === pid)) continue;
      const row = byPid.get(pid);
      try { signal(pid, 'SIGSTOP'); }
      catch (error) {
        if (error && error.code === 'ESRCH') continue; // exited before it could be frozen: nothing to stop
        if (pid === root) continue; // the script itself is still killed below
        result.failed.push({pid, command: redact(row?.command ?? '', 120), error: why(error)});
        continue;
      }
      frozen.set(pid, {start: row?.start, command: row?.command ?? ''});
      added++;
    }
    if (!added) { complete = true; break; }
  }
  if (rows && !complete) result.errors.push('the process tree was still growing after 5 passes');
  // Kill only what a fresh list still shows as the process that was frozen: something else may have killed a frozen
  // process and its pid been reused. Without a fresh list nothing is confirmed, so only the script itself is killed
  // and the rest is left stopped and reported.
  const last = snapshot();
  const current = last && new Map(last.map(row => [row.pid, row]));
  const targets = new Map(frozen);
  if (rootSeen && !targets.has(root)) targets.set(root, {start: undefined, command: ''});
  const unconfirmed = [...targets.keys()].filter(pid => pid !== root && !current);
  if (unconfirmed.length) result.errors.push(`${unconfirmed.length} frozen process(es) left stopped: could not confirm they are still ours`);
  for (const [pid, known] of targets) {
    if (pid !== root && !(current && same(current.get(pid), known))) continue;
    const entry = {pid, command: redact(known.command, 120)};
    try { signal(pid, 'SIGKILL'); result.push(entry); }
    catch (error) {
      if (error && error.code === 'ESRCH') result.push(entry);
      else result.failed.push({...entry, error: why(error)});
    }
  }
  if (result.failed.length) result.errors.push(`${result.failed.length} process(es) could not be stopped`);
  return result;
}

// Bounds on what is held of Playwright's call log, and on the report made from it.
const TRACE = {line: 8192, call: 240, calls: 10, pending: 20, report: 8192};

// Runs one browser script with the support preload and holds its output. A script still running after `timeout`
// seconds is stopped with everything it started; if it has still not exited `grace` seconds later, the run ends
// anyway and says so. Playwright's call log (DEBUG=pw:api) is held apart from the output, bounded: it names the
// browser calls a stopped script was still waiting on, which is the hang point.
function runScript(script, {env = process.env, timeout, preload = path.join(__dirname, '..', 'ui', 'tests', 'support', 'browser.cjs'),
                            stop = stopTree, grace = 5}) {
  return new Promise(resolve => {
    const t0 = Date.now();
    const trace = env.DEBUG === undefined;
    const child = spawn(process.execPath, ['--require', preload, script], {env: trace ? {...env, DEBUG: 'pw:api'} : env});
    let out = '', err = '', timedOut = false, settled = false, graceTimer;
    let cleanup = Object.assign([], {failed: [], errors: []});
    const calls = [], pending = [];
    const take = line => {
      const api = / pw:api (.*)$/.exec(line);
      if (!api) { out += line + '\n'; return; }
      calls.push(redact(api[1], TRACE.call));
      if (calls.length > TRACE.calls) calls.shift();
      let call;
      if ((call = /^=> (\S+) started/.exec(api[1]))) {
        pending.push(redact(call[1], 80));
        if (pending.length > TRACE.pending) pending.shift();
      } else if ((call = /^<= (\S+) (succeeded|failed)/.exec(api[1])) && pending.includes(redact(call[1], 80))) {
        pending.splice(pending.lastIndexOf(redact(call[1], 80)), 1);
      }
    };
    child.stdout.on('data', d => { out += d; });
    child.stderr.on('data', d => {
      if (!trace) { out += d; return; }
      const lines = (err + d).split('\n');
      err = lines.pop();
      if (err.length > TRACE.line) err = err.slice(0, TRACE.line); // an endless line is held only up to its bound
      lines.forEach(line => take(line.length > TRACE.line ? line.slice(0, TRACE.line) : line));
    });
    const finish = code => {
      if (settled) return;
      settled = true;
      clearTimeout(timer); clearTimeout(graceTimer);
      child.stdout.destroy(); child.stderr.destroy();
      if (err) take(err);
      resolve({code, out, timedOut, secs: (Date.now() - t0) / 1000, stopped: cleanup,
               pending: [...pending], calls: [...calls]});
    };
    const timer = setTimeout(() => {
      timedOut = true;
      try {
        const got = stop(child.pid);
        cleanup = Object.assign([...got], {failed: [...(got.failed || [])], errors: [...(got.errors || [])]});
      } catch (error) { cleanup.errors.push(`stopping failed: ${error.message}`); }
      // A script that could not be stopped is not waited for; one that was is given `grace` seconds to exit.
      const stuck = cleanup.failed.some(p => p.pid === child.pid);
      graceTimer = setTimeout(() => {
        cleanup.errors.push(stuck ? `the script (pid ${child.pid}) could not be stopped; the run ended without waiting for it`
                                  : `the script (pid ${child.pid}) was still running ${grace}s after it was stopped`);
        finish(null);
      }, stuck ? 0 : grace * 1000);
    }, timeout * 1000);
    // Wait for the process, not for its pipes: a browser's helper processes can hold them open for a minute.
    child.on('exit', code => {
      clearTimeout(timer); clearTimeout(graceTimer);
      setTimeout(() => finish(code), 100);
    });
  });
}

// What a stopped script was doing: the browser calls it was waiting on, the last ones logged and what was stopped,
// what could not be and why the cleanup is incomplete. Every piece is shortened and redacted, and the whole is bounded.
function stoppedReport({pending, calls, stopped}) {
  const failed = stopped.failed || [], errors = stopped.errors || [];
  const lines = [pending.length ? `waiting on: ${pending.map(name => redact(name, 80)).join(', ')}`
                                : 'waiting on: no browser call (the script itself)',
                 ...(calls.length ? ['last browser calls:', ...calls.map(call => `  ${redact(call, TRACE.call)}`)] : []),
                 `stopped ${stopped.length} process(es) it started:`,
                 ...stopped.map(p => `  ${p.pid} ${redact(p.command, 120)}`),
                 ...(failed.length ? [`could not stop ${failed.length} process(es):`,
                                      ...failed.map(p => `  ${p.pid} ${redact(p.command, 120)}: ${redact(p.error, 120)}`)] : []),
                 ...(errors.length ? [`cleanup incomplete: ${errors.map(e => redact(e, 200)).join('; ')}`] : [])];
  const report = lines.join('\n');
  return report.length > TRACE.report ? `${report.slice(0, TRACE.report)}\n… (report cut at ${TRACE.report} characters)` : report;
}

async function main() {
  const dir = path.join(__dirname, '..', 'ui', 'tests');
  const CORE = ['account-status', 'bot-permissions', 'chat', 'docs', 'goals', 'inbound', 'meetings', 'org-rules', 'org-rules-counsel', 'people-access', 'recruit-unit',
                'settings-forms', 'settings-no-dead-ends', 'sign-in-redirect', 'strategy', 'task-privacy', 'tasks-page', 'team-groups', 'threads', 'dashboard', 'visual-cards', 'visual-fallback', 'visual-hosted', 'visual-media', 'visual-media-app'];
  const args = process.argv.slice(2);
  let jobs;
  let all = false;
  let list = false;
  const wanted = [];
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '-j') {
      jobs = Number(args[++i]);
      if (!Number.isInteger(jobs) || jobs < 1) throw new Error('-j must be a positive integer');
    }
    else if (args[i] === '--all') all = true;
    else if (args[i] === '--list') list = true;
    else wanted.push(path.basename(args[i]).replace(/\.cjs$/, ''));
  }
  // A tour that takes its own screenshots runs by name, on its own time: --all would stop it at the script timeout.
  const WALKTHROUGHS = ['threads-walkthrough'];
  const chosen = wanted.length ? wanted : all ? null : CORE;
  const files = fs.readdirSync(dir).filter(f => f.endsWith('.cjs')).sort()
    .filter(f => chosen ? chosen.includes(f.replace(/\.cjs$/, '')) : !WALKTHROUGHS.includes(f.replace(/\.cjs$/, '')));
  if (!files.length) { console.error('no UI test scripts matched'); process.exit(1); }
  if (list) { console.log(files.map(f => f.replace(/\.cjs$/, '')).join('\n')); return; }
  const {cores, idleFraction} = await sampleCpu();
  const selected = resources(process.env, cores, idleFraction);
  jobs ??= selected.jobs;
  console.log(`UI tests: jobs=${jobs}, slowdown=${selected.slowdown}, timeout=${selected.timeout}s, cores=${cores}, idle=${(idleFraction * 100).toFixed(1)}%`);

  const failed = [];
  const timings = {};
  const started = Date.now();
  const env = {TICO_BROWSER: 'chromium', ...process.env, TICO_UI_SLOWDOWN: String(selected.slowdown)};
  const run = async file => {
    const result = await runScript(path.join(dir, file), {env, timeout: selected.timeout});
    const {code, out, timedOut, secs} = result;
    if (code === 0 && !timedOut) {
      timings[file.replace(/\.cjs$/, '')] = secs;
      console.log(`ok    ${file} (${secs.toFixed(1)}s)`);
    }
    else {
      failed.push(file);
      const why = timedOut ? `, stopped: still running after ${selected.timeout}s` : '';
      const text = timedOut ? `${out.trimEnd()}\n${stoppedReport(result)}` : out.trimEnd();
      console.log(`FAIL  ${file} (${secs.toFixed(1)}s${why})\n${text.replace(/^/gm, '      ')}\n`);
    }
  };

  const queue = [...files];
  await Promise.all(Array.from({length: Math.min(jobs, queue.length)}, async () => {
    while (queue.length) await run(queue.shift());
  }));
  const secs = ((Date.now() - started) / 1000).toFixed(0);
  if (process.env.TICO_UI_TIMINGS) fs.writeFileSync(process.env.TICO_UI_TIMINGS, JSON.stringify(timings, null, 2) + '\n');
  console.log(failed.length ? `\n${failed.length} of ${files.length} UI scripts failed: ${failed.join(', ')} (${secs}s)`
                            : `\n${files.length} UI scripts passed in ${secs}s`);
  process.exit(failed.length ? 1 : 0);
}

module.exports = {resources, runScript, sampleCpu, inSlot, stopTree, stoppedReport, redact};
// A local run first takes one of the box's heavy-job slots, at low priority (scripts/heavy.sh), before it samples
// the CPU; hosted CI, a run already in a slot, or a computer without bash goes straight on.
function inSlot(env = process.env) { return Boolean(env.CI || env.TICO_HEAVY_HELD) || process.platform === 'win32'; }

if (require.main === module) {
  const go = () => main().catch(error => { console.error(error.message); process.exitCode = 1; });
  // Listing runs nothing, so it never waits for a slot.
  if (inSlot() || process.argv.includes('--list')) go();
  else {
    // The wrapper's own stop ends the browser scripts, so a stop here is passed on and the exit waits for it.
    const slotted = spawn(path.join(__dirname, 'heavy.sh'), [process.execPath, __filename, ...process.argv.slice(2)],
                          {stdio: 'inherit'});
    for (const signal of ['SIGTERM', 'SIGINT', 'SIGHUP']) process.on(signal, () => slotted.kill('SIGTERM'));
    slotted.on('error', go);
    slotted.on('exit', code => process.exit(code ?? 1));
  }
}
