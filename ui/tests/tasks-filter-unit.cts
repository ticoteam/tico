// No browser: the Tasks page's Filter (ui/app/tasks-page.ts, docs/pipelines-v3.md S7) offers every field whose values the
// listed rows carry, Tag and Pushed past included:
// - Tag lists the distinct labels on the view's rows (unfiltered and read), as they are, and none when no row has a label;
// - Pushed past is one choice when any shown row was pushed past, and absent otherwise;
// - both appear in the Filter menu's fields;
// - picking Pushed past or a tag in its menu sets the filter, and the filter narrows the rows (task-row.ts).
// Run: node ui/tests/tasks-filter-unit.cts
'use strict';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const vm: typeof import('node:vm') = require('node:vm');
const {transformSync} = require('esbuild') as typeof import('esbuild');

const APP = path.join(__dirname, '..', 'app');
const ctx = vm.createContext({
  esc: (s: unknown) => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'} as Record<string, string>)[c]!),
  TASKS: '#/tasks',
  document: {addEventListener() {}, getElementById: () => null, querySelector: () => null},
  HTMLElement: class {}, HTMLInputElement: class {}, HTMLSelectElement: class {},
  S: {people: [], emps: []},
  localStorage: {getItem: () => null, setItem() {}},
  actorFace: (who: string) => `<i data-face="${who}"></i>`,
  actorLabel: (who: string) => who.replace(/^(bot|human):/, ''),
});
for (const file of ['task-api.ts', 'task-row.ts', 'tasks-page.ts']) {
  vm.runInContext(transformSync(fs.readFileSync(path.join(APP, file), 'utf8'), {loader: 'ts', target: 'es2022'}).code, ctx, {filename: file});
}
const plain = <T,>(x: T): T => JSON.parse(JSON.stringify(x));   // out of the vm's realm, for deepEqual

const task = (id: string, extra: Record<string, unknown> = {}) => ({id, title: id, stage_id: 'build', pipeline_id: 'eng',
  status: 'open', number: 1, owner_id: 'bot:builder', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null,
  snoozed: false, pushed_past: false, turn: null, version: 1, labels: [], ...extra});
const ROWS = [task('a', {labels: ['ui-fail', 'stress-run']}), task('b', {labels: ['ui-fail'], pushed_past: true}), task('c')];
// `base` is the view's unfiltered rows, `tasks` the rows a filter's server read returned (tasks-page.ts tasksNarrow).
const state = (tasks: unknown[], base: unknown[] = []) => ({pipeline: 'eng', archive: '', q: '', filters: {} as Record<string, unknown>, needs: [], tasks, base, pipelines: [],
  layout: 'list'});
type Value = {value: string; label: string};
const values = (st: unknown, k: string): Value[] => plain(ctx.tasksFilterValues(st, k)).map(({value, label}: Value) => ({value, label}));

// ---- Tag: the distinct labels, as they are
const st = state(ROWS);
assert.deepEqual(values(st, 'tag'), [{value: 'stress-run', label: 'stress-run'}, {value: 'ui-fail', label: 'ui-fail'}], 'Tag lists the labels');
assert.deepEqual(values(state([task('c')]), 'tag'), [], 'no labels, no Tag values');

// Once a pick narrows the rows, the unfiltered rows still offer their tags, so another tag can be added.
assert.deepEqual(values(state([ROWS[0]], ROWS), 'tag').map(v => v.value), ['stress-run', 'ui-fail'], 'Tag keeps the unfiltered rows\' labels');

// ---- Pushed past: one choice when a row was pushed past
assert.deepEqual(values(st, 'pushed'), [{value: '1', label: 'Pushed past'}], 'Pushed past is one choice');
assert.deepEqual(values(state([task('a'), task('c')]), 'pushed'), [], 'nothing pushed past, no choice');
assert.deepEqual(values(state([task('a')], ROWS), 'pushed'), [{value: '1', label: 'Pushed past'}], 'a pushed row in the unfiltered rows offers it');

// ---- the Filter menu's fields include both
let menuHTML = '';
let pop: {onchange?: () => void; onclick?: () => void; querySelector: () => null; querySelectorAll: (sel: string) => {value: string}[]} | null = null;
let checked: string[] = [];
ctx.tasksMenuStub = (_st: unknown, _anchor: unknown, html: string, wire: (p: unknown) => void) => {
  menuHTML = html;
  pop = {querySelector: () => null, querySelectorAll: () => checked.map(value => ({value}))};
  wire(pop);
};
vm.runInContext('tasksMenu = tasksMenuStub; tasksRender = () => {}; tasksMenuPlace = () => {}; $ = () => null;', ctx);
ctx.tasksFieldMenu(st, {});
const fields = [...menuHTML.matchAll(/data-pick-field="([^"]+)"/g)].map(m => m[1]);
assert.ok(fields.includes('tag'), `Tag is a field: ${fields.join(', ')}`);
assert.ok(fields.includes('pushed'), `Pushed past is a field: ${fields.join(', ')}`);

// ---- picking narrows the list
const shown = (f: unknown) => ROWS.filter(t => ctx.taskFilterMatch(t, '', f)).map(t => t.id);
ctx.tasksFilterMenu(st, 'pushed', null);
assert.match(menuHTML, /type="radio" name="tl-pushed" value="1"/, 'Pushed past is a single choice');
checked = ['1'];
pop!.onchange!();
assert.equal(st.filters.pushed, true, 'picking Pushed past sets the filter');
assert.deepEqual(shown(st.filters), ['b'], 'Pushed past narrows to the pushed task');
checked = [];
pop!.onchange!();
assert.equal(st.filters.pushed, false, 'unpicking clears it');

ctx.tasksFilterMenu(st, 'tag', null);
checked = ['stress-run'];
pop!.onchange!();
assert.deepEqual(plain(st.filters).tag, ['stress-run'], 'picking a tag sets the filter');
assert.deepEqual(shown(st.filters), ['a'], 'Tag narrows to the tagged task');
assert.deepEqual(plain(ctx.tasksFilterWords(st, 'tag')), ['stress-run'], 'the chip names the tag');

console.log('tasks-filter-unit: ok');
