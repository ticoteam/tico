#!/usr/bin/env node
// Runs the browser scripts in ui/tests/*.cjs a few at a time (npm run test:ui).
// Each script's output is held and printed together, so a failure reads as one block.
// Usage: node scripts/ui-tests.cjs [-j N] [--all] [name-or-path ...]   (free-core jobs, or $TICO_UI_JOBS)
// With no names it runs CORE, the main pages and the privacy boundaries; --all runs every script (the release
// check does). A name runs that script whether or not it is in CORE. Run locally, it waits for one of the box's
// heavy-job slots first (scripts/heavy.sh). A script still running after TICO_UI_SCRIPT_TIMEOUT seconds (120, times the
// slowdown) is stopped and fails with its name, so one hang does not hold the job until CI cancels it.
const {spawn} = require('node:child_process');
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

// Runs one browser script with the support preload and holds its output. A script still running after `timeout`
// seconds is killed: Playwright's browser exits with it (its pipe closes).
function runScript(script, {env = process.env, timeout, preload = path.join(__dirname, '..', 'ui', 'tests', 'support', 'browser.cjs')}) {
  return new Promise(resolve => {
    const t0 = Date.now();
    const child = spawn(process.execPath, ['--require', preload, script], {env});
    let out = '', timedOut = false;
    child.stdout.on('data', d => { out += d; });
    child.stderr.on('data', d => { out += d; });
    const timer = setTimeout(() => { timedOut = true; child.kill('SIGKILL'); }, timeout * 1000);
    // Wait for the process, not for its pipes: a browser's helper processes can hold them open for a minute.
    child.on('exit', code => setTimeout(() => {
      clearTimeout(timer);
      child.stdout.destroy(); child.stderr.destroy();
      resolve({code, out, timedOut, secs: (Date.now() - t0) / 1000});
    }, 100));
  });
}

async function main() {
  const {cores, idleFraction} = await sampleCpu();
  const selected = resources(process.env, cores, idleFraction);

  const dir = path.join(__dirname, '..', 'ui', 'tests');
  const CORE = ['bot-permissions', 'chat', 'docs', 'goals', 'inbound', 'meetings', 'people-access', 'recruit-unit',
                'settings-forms', 'sign-in-redirect', 'task-privacy', 'tasks-page', 'team-groups', 'visual-cards', 'visual-fallback', 'visual-media', 'visual-media-app'];
  const args = process.argv.slice(2);
  let jobs = selected.jobs;
  let all = false;
  const wanted = [];
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '-j') {
      jobs = Number(args[++i]);
      if (!Number.isInteger(jobs) || jobs < 1) throw new Error('-j must be a positive integer');
    }
    else if (args[i] === '--all') all = true;
    else wanted.push(path.basename(args[i]).replace(/\.cjs$/, ''));
  }
  const chosen = wanted.length ? wanted : all ? null : CORE;
  const files = fs.readdirSync(dir).filter(f => f.endsWith('.cjs')).sort()
    .filter(f => !chosen || chosen.includes(f.replace(/\.cjs$/, '')));
  if (!files.length) { console.error('no UI test scripts matched'); process.exit(1); }
  console.log(`UI tests: jobs=${jobs}, slowdown=${selected.slowdown}, timeout=${selected.timeout}s, cores=${cores}, idle=${(idleFraction * 100).toFixed(1)}%`);

  const failed = [];
  const started = Date.now();
  const env = {TICO_BROWSER: 'chromium', ...process.env, TICO_UI_SLOWDOWN: String(selected.slowdown)};
  const run = async file => {
    const {code, out, timedOut, secs} = await runScript(path.join(dir, file), {env, timeout: selected.timeout});
    if (code === 0) console.log(`ok    ${file} (${secs.toFixed(1)}s)`);
    else {
      failed.push(file);
      const why = timedOut ? `, stopped: still running after ${selected.timeout}s` : '';
      console.log(`FAIL  ${file} (${secs.toFixed(1)}s${why})\n${out.trimEnd().replace(/^/gm, '      ')}\n`);
    }
  };

  const queue = [...files];
  await Promise.all(Array.from({length: Math.min(jobs, queue.length)}, async () => {
    while (queue.length) await run(queue.shift());
  }));
  const secs = ((Date.now() - started) / 1000).toFixed(0);
  console.log(failed.length ? `\n${failed.length} of ${files.length} UI scripts failed: ${failed.join(', ')} (${secs}s)`
                            : `\n${files.length} UI scripts passed in ${secs}s`);
  process.exit(failed.length ? 1 : 0);
}

module.exports = {resources, runScript, sampleCpu, inSlot};
// A local run first takes one of the box's heavy-job slots, at low priority (scripts/heavy.sh), before it samples
// the CPU; hosted CI, a run already in a slot, or a computer without bash goes straight on.
function inSlot(env = process.env) { return Boolean(env.CI || env.TICO_HEAVY_HELD) || process.platform === 'win32'; }

if (require.main === module) {
  const go = () => main().catch(error => { console.error(error.message); process.exitCode = 1; });
  if (inSlot()) go();
  else {
    // The wrapper's own stop ends the browser scripts, so a stop here is passed on and the exit waits for it.
    const slotted = spawn(path.join(__dirname, 'heavy.sh'), [process.execPath, __filename, ...process.argv.slice(2)],
                          {stdio: 'inherit'});
    for (const signal of ['SIGTERM', 'SIGINT', 'SIGHUP']) process.on(signal, () => slotted.kill('SIGTERM'));
    slotted.on('error', go);
    slotted.on('exit', code => process.exit(code ?? 1));
  }
}
