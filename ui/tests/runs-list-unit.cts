// No browser: the two Runs lists (Settings > History > Runs, ui/app/settings-history.ts settingsRunsHTML, and a bot's
// Runs card, ui/app/issues.ts runsTable) drawn from rows shaped as GET /api/runs sends them (backend/views.py run_view).
// - each Log link opens /api/runs/<the row's id>/log: the row has `id` and no `run`;
// - a live run (state leased, running or input; exit and finished_at null) reads Running, with no failure pill,
//   no "exit" and no Finished time;
// - a finished run still reads Done, and a failed one still reads as failed.
// Run: node ui/tests/runs-list-unit.cts
'use strict';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const vm: typeof import('node:vm') = require('node:vm');
const {stripTypeScriptTypes} = require('node:module') as {stripTypeScriptTypes: (code: string) => string};

const ctx = vm.createContext({
  API: '/api',
  S: {emps: []},
  // issues.ts's own roster helpers (botDisplayName, empChip) name a bot from S.emps and the assistant.
  assistantBot: () => '',
  assistantName: () => '',
  esc: (s: unknown) => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'} as Record<string, string>)[c]!),
  ago: (at: unknown) => at ? 'AGO(' + String(at) + ')' : '',
  fmt: (at: unknown) => at ? String(at) : '',
  window: {},
  document: {querySelectorAll: () => []},
});
for (const name of ['issues.ts', 'settings-history.ts']) {
  const file = path.join(__dirname, '..', 'app', name);
  vm.runInContext(stripTypeScriptTypes(fs.readFileSync(file, 'utf8')), ctx, {filename: name});
}

// As run_view sends them: `id`, never `run`.
const row = (id: string, state: string, more: Record<string, unknown>) => ({id, state, bot: 'ops', employee: 'ops', issue: null, outcome: null,
  failure_reason: '', duration_s: null, output_tokens: null, cost_usd: null, resumed: false, fallback: null, ran_on: null, ...more});
const done = row('run-done', 'completed', {outcome: 'completed', exit: 0, finished_at: '2026-11-12T12:00:00Z', duration_s: 65});
const failed = row('run-failed', 'failed', {outcome: 'failed', exit: 1, finished_at: '2026-11-12T12:01:00Z', failure_reason: 'boom'});
const live = (state: string) => row('run-' + state, state, {exit: null, finished_at: null});
const lives = ['leased', 'running', 'input'].map(live);

ctx.__rows = [done, failed, ...lives];
const lists: [string, string][] = [
  ['Settings > History > Runs', vm.runInContext('settingsRunsHTML(__rows)', ctx) as string],
  ["a bot's Runs card", vm.runInContext('runsTable(__rows)', ctx) as string],
];
for (const [where, html] of lists) {
  // 1. Every Log link names its row's id.
  const hrefs = [...html.matchAll(/href="([^"]*\/log)"/g)].map(m => m[1]);
  assert.deepEqual(hrefs, ['/api/runs/run-done/log', '/api/runs/run-failed/log', '/api/runs/run-leased/log',
    '/api/runs/run-running/log', '/api/runs/run-input/log'], `${where}: Log links use the row's id`);
  assert.ok(!html.includes('undefined'), `${where}: nothing reads undefined`);
}
// 2. Each row on its own: a live run reads Running, not failed, with no finished time.
for (const r of lives) {
  ctx.__rows = [r];
  for (const [where, fn] of [['Settings > History > Runs', 'settingsRunsHTML'], ["a bot's Runs card", 'runsTable']]) {
    const html = vm.runInContext(`${fn}(__rows)`, ctx) as string;
    assert.match(html, /<span class="pill in-progress"[^>]*>Running<\/span>/, `${where}: a ${r.state} run reads Running`);
    assert.ok(!html.includes('pill fail'), `${where}: a ${r.state} run has no failure pill`);
    assert.ok(!/exit/.test(html), `${where}: a ${r.state} run shows no exit code`);
    assert.ok(!html.includes('AGO('), `${where}: a ${r.state} run shows no Finished time`);
  }
}
// 3. Finished runs read as before.
for (const [where, fn] of [['Settings > History > Runs', 'settingsRunsHTML'], ["a bot's Runs card", 'runsTable']]) {
  ctx.__rows = [done];
  let html = vm.runInContext(`${fn}(__rows)`, ctx) as string;
  assert.match(html, /pill ok[^>]*>Done</, `${where}: a completed run reads Done`);
  assert.ok(html.includes('AGO(2026-11-12T12:00:00Z)'), `${where}: a completed run shows when it finished`);
  ctx.__rows = [failed];
  html = vm.runInContext(`${fn}(__rows)`, ctx) as string;
  assert.ok(html.includes('pill fail'), `${where}: a failed run reads as failed`);
  assert.ok(html.includes('AGO(2026-11-12T12:01:00Z)'), `${where}: a failed run shows when it finished`);
}

console.log('runs-list-unit: ok');
