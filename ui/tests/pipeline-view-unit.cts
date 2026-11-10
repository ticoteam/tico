// No browser: the pipeline screens' drawing and decisions (docs/pipelines-v3.md S7, S8, S11, S12, S17.3). The classic
// scripts are compiled with esbuild's transform and run in one vm context, with the few shared globals they read stubbed:
// - the pipeline view's hook draws columns, the list and the phone's tabs from the tasks the page gives it, with each
//   stage's count out of its limit, and a stage header that opens settings only for whoever may change the pipeline;
// - a refused move reads as the engine said it: a push past asks why, anything else is the engine's line;
// - Company › Factories orders a factory's pipelines by where they file, joins them with an arrow, and puts pipelines in
//   no factory under Other with no count;
// - the editor names what someone else changed when its save is refused as out of date.
// Run: node ui/tests/pipeline-view-unit.cts
'use strict';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const vm: typeof import('node:vm') = require('node:vm');
const {transformSync} = require('esbuild') as typeof import('esbuild');

const APP = path.join(__dirname, '..', 'app');
const hooks: Record<string, {html: (rec: unknown, tasks: unknown[], opts: unknown) => string}> = {};
const ctx = vm.createContext({
  esc: (s: unknown) => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'} as Record<string, string>)[c]!),
  TASKS: '#/tasks',
  TASK_PAGE_HOOKS: hooks,
  document: {addEventListener() {}},
  localStorage: {getItem: () => null, setItem() {}},
  actorFace: (who: string) => `<i data-face="${who}"></i>`,
  actorLabel: (who: string) => who.replace(/^(bot|human):/, ''),
  ageShort: () => '2h',
  skelRows: () => '<i class="skel"></i>',
  errorMessage: (_e: unknown, fallback: string) => fallback,
});
for (const file of ['task-api.ts', 'task-row.ts', 'pipeline-api.ts', 'task-pipelines.ts', 'overview-factories.ts', 'pipeline-editor.ts']) {
  vm.runInContext(transformSync(fs.readFileSync(path.join(APP, file), 'utf8'), {loader: 'ts', target: 'es2022'}).code, ctx, {filename: file});
}
const plain = <T,>(x: T): T => JSON.parse(JSON.stringify(x));   // out of the vm's realm, for deepEqual

const req = (kind: string) => ({kind, settings: {}, can_push_past: true, who_oks: 'none', person_id: null, line: ''});
const stage = (id: string, name: string, extra: Record<string, unknown> = {}) => ({id, name, purpose: '', owner: null, by_hand: false,
  task_limit: null, expected_minutes: null, expected_bots_only: false, on_enter: {}, requirements: [req('owner_done')], ...extra});
const rec = (extra: Record<string, unknown> = {}) => ({id: 'product', name: 'Product', goal: 'Specs that ship', prefix: 'P',
  manager_id: 'bot:pm', owner_id: 'human:ana', private: false, factory_id: 'f-product', preset: null, settings_version: 3,
  retired_at: null, stages: [stage('ideas', 'Ideas', {by_hand: true}), stage('spec', 'Spec', {task_limit: 2}), stage('dev', 'In development')],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true, ...extra});
const task = (id: string, title: string, stageId: string, extra: Record<string, unknown> = {}) => ({id, title, stage_id: stageId,
  pipeline_id: 'product', status: 'open', number: 1, owner_id: 'bot:pm', requester_id: 'human:ana', assignee_id: null, due_at: null,
  late: null, snoozed: false, turn: null, version: 1, ...extra});
const TASKS = [task('t1', 'Saved carts', 'ideas'), task('t2', 'Refund flow', 'spec', {turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana'}}),
  task('t3', 'Carrier contract', 'spec'), task('t4', 'Other pipeline', 'spec', {pipeline_id: 'eng'}), task('t5', 'Shipped', 'dev', {status: 'done'})];
const opts = (extra: Record<string, unknown> = {}) => ({layout: 'columns', phone: false, q: '', filters: {}, ...extra});
const view = hooks['pipeline'];
assert.ok(view, 'the view sets its hook on the page');

// ---- columns, list and phone
{
  const html = view.html(rec(), TASKS, opts());
  assert.equal((html.match(/class="pv-col"/g) || []).length, 3, 'a column per work stage; Done and Closed are not columns');
  assert.match(html, /data-pv-settings="spec"[^>]*><span class="pv-stage-name">Spec<\/span><span class="pv-stage-n tnum">2\/2<\/span>/, 'a stage with a limit counts out of it');
  assert.match(html, /<span class="pv-stage-name">Ideas<\/span><span class="pv-stage-n tnum">1<\/span>/);
  assert.equal((html.match(/class="tk-card"/g) || []).length, 3, 'only this pipeline\'s open tasks');
  assert.doesNotMatch(html, /Other pipeline|Shipped/);
  assert.match(html, /data-task="t2"/, 'a task opens through the page\'s [data-task]');
  assert.match(html, /tk-mark-person/, 'the mark comes from the shared row');
}
{
  const html = view.html(rec({may_change: false}), TASKS, opts());
  assert.doesNotMatch(html, /data-pv-settings/, 'no settings for whoever may not change the pipeline');
  assert.match(html, /<span class="pv-stage-h">/);
}
{
  const list = view.html(rec(), TASKS, opts({layout: 'list'}));
  assert.equal((list.match(/class="pv-group"/g) || []).length, 3, 'the list keeps an empty stage while nothing is searched');
  const found = view.html(rec(), TASKS, opts({layout: 'list', q: 'refund'}));
  assert.equal((found.match(/class="pv-group"/g) || []).length, 1, 'searching hides the stages with nothing found');
  assert.match(found, /Refund flow/);
  const none = view.html(rec(), TASKS, opts({layout: 'list', q: 'nothing like this'}));
  assert.match(none, /Nothing found/);
}
{
  const phone = view.html(rec(), TASKS, opts({phone: true}));
  assert.equal((phone.match(/role="tab"/g) || []).length, 3, 'a tab per stage');
  assert.match(phone, /class="pv-tab on" data-pv-tab="ideas"/, 'the first stage with tasks shows first');
  assert.match(phone, /pv-is-phone/);
}

// ---- a refused move
{
  const refused = (body: Record<string, unknown>) => Object.assign(new Error('refused'), {body});
  const t = task('t2', 'Refund flow', 'spec');
  const push = plain(ctx.pvRefusedBar(t, 'dev', 'In development', refused({error: 'task_not_met', detail: 'Spec page is missing.', missing: ['Spec page'], next: []}), true));
  assert.deepEqual(push, {taskId: 't2', to: 'dev', push: true, text: 'Move to In development without Spec page?'});
  const again = plain(ctx.pvRefusedBar(t, 'dev', 'In development', refused({error: 'task_not_met', detail: 'Spec page is missing.', missing: ['Spec page'], next: []}), false));
  assert.equal(again.push, false, 'a push past that was refused is not offered again');
  assert.equal(again.text, 'Refund flow: Spec page is missing. Missing: Spec page.');
  const full = plain(ctx.pvRefusedBar(t, 'dev', 'In development', refused({error: 'task_stage_full', detail: 'In development is full (3 of 3).', missing: [], next: []}), true));
  assert.deepEqual(full, {taskId: 't2', to: 'dev', push: false, text: 'Refund flow: In development is full (3 of 3).'});
  const other = plain(ctx.pvRefusedBar(t, 'dev', 'In development', new Error('offline'), true));
  assert.equal(other.text, 'Refund flow: Not moved.');
}

// ---- Company › Factories
{
  const recs = [
    rec({id: 'eng', name: 'Engineering', factory_id: 'f-product', stages: [stage('b', 'Build')]}),
    rec({id: 'product', name: 'Product', factory_id: 'f-product', stages: [stage('s', 'Spec', {on_enter: {file_into: 'eng'}})]}),
    rec({id: 'general', name: 'General', factory_id: null, stages: [stage('g', 'Doing')]}),
    rec({id: 'old', name: 'Old', factory_id: null, retired_at: '2026-11-01T00:00:00Z'}),
  ];
  const map = {factories: [{id: 'f-product', name: 'Product', goal: null, manager_id: null, done: 4}],
    pipelines: [{id: 'product', factory_id: 'f-product', open: 17, mark: 'person'}, {id: 'eng', factory_id: 'f-product', open: 9, mark: null},
      {id: 'general', factory_id: null, open: 31, mark: 'cant'}]};
  const groups = plain(ctx.ofGroups(map, recs));
  assert.deepEqual(groups.map((g: {name: string; done: number | null; pipelines: {id: string}[]}) => [g.name, g.done, g.pipelines.map(p => p.id)]),
    [['Product', 4, ['product', 'eng']], ['Other', null, ['general']]], 'a pipeline comes before the one it files into; retired ones go');
  const html = ctx.ofGroupHTML(groups[0]);
  assert.match(html, /<span class="of-done tnum">4 done<\/span>/);
  assert.match(html, /Product<\/span><span class="of-box-n tnum">17<\/span><span class="tk-mark tk-mark-person"[^>]*>!<\/span><\/a><span class="of-arrow"[^>]*>→<\/span><a class="of-box" href="#\/tasks\?pipeline=eng"/);
  const other = ctx.ofGroupHTML(groups[1]);
  assert.doesNotMatch(other, /done/, 'Other has no count');
  assert.match(other, /tk-mark-cant/);
}

// ---- the editor's out-of-date save
{
  const mine = rec();
  const theirs = rec({goal: 'Better specs', settings_version: 4,
    stages: [stage('ideas', 'Ideas', {by_hand: true}), stage('spec', 'Spec', {task_limit: 3}), stage('qa', 'QA')]});
  assert.deepEqual(plain(ctx.peChanged(mine, theirs)), ['Goal', 'Spec', 'QA (added)', 'In development (removed)']);
  const order = rec({stages: [...rec().stages].reverse()});
  assert.deepEqual(plain(ctx.peChanged(mine, order)), ['The order of the stages']);
  assert.deepEqual(plain(ctx.peChanged(mine, rec())), []);
}
console.log('pipeline-view-unit: ok');
