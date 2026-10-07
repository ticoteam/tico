#!/usr/bin/env node
// Runs the browser scripts in ui/tests/*.cjs a few at a time (npm run test:ui).
// Each script's output is held and printed together, so a failure reads as one block.
// Usage: node scripts/ui-tests.cjs [-j N] [--core|--non-core|--all] [--list] [name-or-path ...]
// (default N=3, or $TICO_UI_JOBS). Core and non-core partition all scripts; --all runs both.
// A name runs that script; --list prints the selected names as JSON without launching a browser.
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const dir = path.join(__dirname, '..', 'ui', 'tests');
const CORE = ['bot-permissions', 'chat', 'docs', 'goals', 'meetings', 'page-layouts', 'people-access',
              'settings-forms', 'sign-in-redirect', 'task-privacy', 'tasks-page'];
const args = process.argv.slice(2);
let jobs = Number(process.env.TICO_UI_JOBS) || 3;
let mode = null;
let listOnly = false;
const wanted = [];
for (let i = 0; i < args.length; i++) {
  if (args[i] === '-j') jobs = Number(args[++i]) || jobs;
  else if (['--core', '--non-core', '--all'].includes(args[i])) {
    if (mode) { console.error('choose only one of --core, --non-core, or --all'); process.exit(2); }
    mode = args[i].slice(2);
  }
  else if (args[i] === '--list') listOnly = true;
  else wanted.push(path.basename(args[i]).replace(/\.cjs$/, ''));
}
if (mode && wanted.length) { console.error('selection flags cannot be combined with script names'); process.exit(2); }
const allNames = fs.readdirSync(dir).filter(f => f.endsWith('.cjs')).map(f => f.replace(/\.cjs$/, '')).sort();
const coreSet = new Set(CORE);
const missingCore = CORE.filter(name => !allNames.includes(name));
if (coreSet.size !== CORE.length || missingCore.length) {
  console.error(`CORE must contain unique existing scripts${missingCore.length ? `; missing: ${missingCore.join(', ')}` : ''}`);
  process.exit(2);
}
const NON_CORE = allNames.filter(name => !coreSet.has(name));
const partition = new Set([...CORE, ...NON_CORE]);
if (partition.size !== allNames.length || allNames.some(name => !partition.has(name))) {
  console.error('CORE and non-core selections do not partition all UI scripts'); process.exit(2);
}
const chosen = wanted.length ? wanted : mode === 'non-core' ? NON_CORE : mode === 'all' ? allNames : CORE;
const files = allNames.filter(name => chosen.includes(name)).map(name => `${name}.cjs`);
if (!files.length) { console.error('no UI test scripts matched'); process.exit(1); }
if (listOnly) { console.log(JSON.stringify(files.map(f => f.replace(/\.cjs$/, '')))); process.exit(0); }

const failed = [];
const started = Date.now();
const run = file => new Promise(resolve => {
  const t0 = Date.now();
  const child = spawn(process.execPath, [path.join(dir, file)], {env: {TICO_BROWSER: 'chromium', ...process.env}});
  let out = '';
  child.stdout.on('data', d => { out += d; });
  child.stderr.on('data', d => { out += d; });
  // Wait for the process, not for its pipes: a browser's helper processes can hold them open for a minute.
  child.on('exit', code => setTimeout(() => {
    child.stdout.destroy(); child.stderr.destroy();
    const secs = ((Date.now() - t0) / 1000).toFixed(1);
    if (code === 0) console.log(`ok    ${file} (${secs}s)`);
    else { failed.push(file); console.log(`FAIL  ${file} (${secs}s)\n${out.trimEnd().replace(/^/gm, '      ')}\n`); }
    resolve();
  }, 100));
});

(async () => {
  const queue = [...files];
  await Promise.all(Array.from({length: Math.min(jobs, queue.length)}, async () => {
    while (queue.length) await run(queue.shift());
  }));
  const secs = ((Date.now() - started) / 1000).toFixed(0);
  console.log(failed.length ? `\n${failed.length} of ${files.length} UI scripts failed: ${failed.join(', ')} (${secs}s)`
                            : `\n${files.length} UI scripts passed in ${secs}s`);
  process.exit(failed.length ? 1 : 0);
})();
