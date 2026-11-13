// No browser: which Tasks view an address opens. A `?view=` address (Home's "waiting on you", My tasks, old #/decide
// links) opens You, even when a pipeline was the last one looked at; a bare #/tasks still opens that pipeline.
// Run: node ui/tests/tasks-view-url-unit.cts (after npm run build:ui)
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const vm: typeof import('node:vm') = require('node:vm');
const path: typeof import('node:path') = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../../build/ui/app/tasks-page.js'), 'utf8');
// Only the address reader and what it reads: the rest of the file draws the page.
const start = source.indexOf('const TASKS_KEPT'), end = source.indexOf('function tasksURLWrite');
assert.ok(start >= 0 && end > start, 'tasksViewFromURL is where this test reads it');

interface View {pipeline: string; layout: string; archive: string}
function viewFor(route: string, kept: unknown): View {
  const store: Record<string, string> = kept === undefined ? {} : {'tico.tasks.view': JSON.stringify(kept)};
  const context = vm.createContext({
    S: {route}, URLSearchParams, JSON,
    localStorage: {getItem: (k: string) => store[k] ?? null},
    pipelinesAll: async () => [],
  });
  vm.runInContext(`${source.slice(start, end)};globalThis.view = tasksViewFromURL();`, context);
  return {...(context as unknown as {view: View}).view};
}

const kept = {pipeline: 'engineering', layout: 'columns'};
// Home's "waiting on you" (NEEDS_YOU) and the old #/decide redirect.
assert.equal(viewFor('#/tasks?view=foryou', kept).pipeline, '', '?view=foryou opens You, not the remembered pipeline');
// Home's My tasks count and its task card link.
assert.equal(viewFor('#/tasks?view=list', kept).pipeline, '');
assert.equal(viewFor('#/tasks?view=list&who=all', kept).pipeline, '');
// A bare #/tasks still restores the last view.
assert.deepEqual(viewFor('#/tasks', kept), {pipeline: 'engineering', layout: 'columns', archive: ''});
// Named addresses are unchanged.
assert.equal(viewFor('#/tasks?pipeline=sales', kept).pipeline, 'sales');
assert.equal(viewFor('#/tasks?view=foryou&pipeline=sales', kept).pipeline, 'sales');
assert.deepEqual(viewFor('#/tasks?archive=done', kept), {pipeline: '', layout: 'columns', archive: 'done'});
assert.equal(viewFor('#/tasks', undefined).pipeline, '');

// The outbox's Review link on a letter waiting for an OK opens Needs you (there is no #/foryou route).
{
  const outbox = fs.readFileSync(path.join(__dirname, '../../build/ui/app/inboxes-outbox.js'), 'utf8');
  const from = outbox.indexOf('const lettersOutMoney'), to = outbox.indexOf('const lettersOutSettings');
  const row = outbox.indexOf('function lettersOutProof'), rowEnd = outbox.indexOf('\n}\n', outbox.indexOf('function lettersOutActivity')) + 3;
  assert.ok(from >= 0 && to > from && row > to && rowEnd > row, 'the letter row is where this test reads it');
  const context = vm.createContext({
    NEEDS_YOU: '#/tasks?view=foryou', esc: (v: unknown) => String(v ?? ''), actorLabel: (v: string) => v, inboxesPill: (_tone: string, label: string) => label,
  });
  vm.runInContext(`${outbox.slice(from, to)};${outbox.slice(row, rowEnd)};globalThis.draw = lettersOutActivity;`, context);
  const html = (context as unknown as {draw(row: unknown): string}).draw({id: 'l1', status: 'waiting_for_ok', created_at: '2026-11-12T08:00:00Z',
    page_count: 1, mail_class: 'first_class', total_micros: 1000000, billing: 'reserved', recipient: {name: 'A'}, requested_by: 'human:p'});
  assert.match(html, /<a href="#\/tasks\?view=foryou">Review<\/a>/, 'Review opens Needs you');
}
console.log('tasks-view-url-unit: ok');
