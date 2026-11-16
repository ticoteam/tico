// No browser: the pipeline screens' drawing and decisions (docs/dev/pipelines-v3.md S7, S8, S11, S12, S17.3). The classic
// scripts are compiled with esbuild's transform and run in one vm context, with the few shared globals they read stubbed:
// - the pipeline view's hook draws columns, the list and the phone's tabs from the tasks the page gives it, with each
//   stage's number the tasks it lists (what a person sees below it), and a stage header that opens settings only for
//   whoever may change the pipeline;
// - a limited stage shows its limit only when every slot is used (`full`, on the number and the phone tab); the slots
//   and the tasks waiting only on a person (`on_person`) are said in the number's tooltip ("5 of 20 slots used. 15 more
//   wait on a person and hold no slot."), and on a phone in one muted line above the open stage's cards; no person pill;
// - a refused move reads as the engine said it: a push past asks why, anything else is the engine's line;
// - Strategy › Org's Factories section orders a factory's pipelines by where they file, and puts pipelines in no factory
//   in one group after them;
// - the editor names what someone else changed when its save is refused as out of date;
// - the editor offers every kind, a page's and In a release's settings, and a stage's On entering list (its Limit and
//   actions, the owner change among them, as rows; + Rule and + Action), and saves them as the record stores them;
// - a task's row for a stage's OK is its title alone, met or not; what it waits on is its tooltip.
// - in the list, each run of empty stages (even one) is one line of stage chips in its place, and a pipeline with nothing
//   open is one empty state with New task; neither while searching, nor while more tasks are still to be read (#500).
// Run: node ui/tests/pipeline-view-unit.cts
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
  HTMLInputElement: class {},
  HTMLSelectElement: class {},
  S: {people: [{id: 'ana', name: 'Ana'}], emps: []},
  botDisplayName: (id: string) => id,
  localStorage: {getItem: () => null, setItem() {}},
  actorFace: (who: string) => `<i data-face="${who}"></i>`,
  actorAvatar: (who: string) => `<i data-avatar="${who}"></i>`,
  actorLabel: (who: string) => who.replace(/^(bot|human):/, ''),
  // hub-v2.ts's asker, as the task page names it (the browser check pipeline-cards.cts runs the real one).
  taskAsker: (t: {filer?: string | null; requester_id?: string | null}) => t.filer || t.requester_id,
  taskFiledByBuiltin: (t: {filer?: string | null}) => t.filer === 'check-health' ? 'Health check' : '',
  taskAskerLabel: (t: {filer?: string | null; requester_id?: string | null}) => t.filer === 'check-health' ? 'Health check' : String(t.filer || t.requester_id || '').replace(/^(bot|human):/, ''),
  ageShort: () => '2h',
  skelRows: () => '<i class="skel"></i>',
  errorMessage: (_e: unknown, fallback: string) => fallback,
  TL_ICON: {plus: '+'},
  giveToFieldHTML: () => '',
  stageRulesButtonHTML: () => '',   // stage-rules.cts draws a stage's Rules button
  // and the rules in each of a stage editor's phases: here only where they go
  stageRulesPhaseHTML: (_p: string, s: {id?: string | null}, ph: string) => s.id ? `<div class="sr-phase" data-sr-section="${s.id}" data-sr-phase="${ph}"></div>` : '',
  // On entering's Limit and action rows are rule rows (stage-rules.ts; stage-rules.cts draws the real one): here, what
  // the row is given
  stageRuleRowHTML: (o: {attrs: string; icon?: string; text: string; open?: string; below?: string}) =>
    `<li class="sr-r" ${o.attrs}><button type="button" class="sr-open" ${o.open ?? ''} data-icon="${o.icon ?? ''}">${o.text}</button>${o.below ?? ''}</li>`,
  SR_ED: null,   // the rules not read: + Rule offers no Rule
  SR_ROW_ICONS: {rule: 'gavel', act: 'bolt', req: 'task_alt', lock: 'lock'},
  srMarkHTML: (cls: string, icon: string) => `<span class="sr-m ${cls}">${icon}</span>`,   // stage-rules.ts: one mark for a rule, one for an action
  needsStageCount: () => 0,   // nothing waits on the person here: no stage's ▶ (stage-review.cts draws them)
  EMPTY_ART: {pipeline: '<svg class="ea-art" aria-hidden="true"></svg>'},   // empty-art.ts: the empty pipeline's picture
  tasksViewOnly: () => false,   // tasks-page.ts: a bot's read-only view of the app
});
for (const file of ['task-api.ts', 'task-row.ts', 'pipeline-api.ts', 'task-pipelines.ts', 'org-factories.ts', 'pipeline-editor.ts', 'task-requirements.ts']) {
  vm.runInContext(transformSync(fs.readFileSync(path.join(APP, file), 'utf8'), {loader: 'ts', target: 'es2022'}).code, ctx, {filename: file});
}
const plain = <T,>(x: T): T => JSON.parse(JSON.stringify(x));   // out of the vm's realm, for deepEqual

const req = (kind: string) => ({kind, settings: {}, can_push_past: true, who_oks: 'none', person_id: null, line: ''});
const stage = (id: string, name: string, extra: Record<string, unknown> = {}) => ({id, name, purpose: '', owner: null, by_hand: false,
  requests_move_on: false, task_limit: null, expected_minutes: null, expected_bots_only: false, on_enter: {}, requirements: [req('owner_done')], ...extra});
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
// task-row.ts declares the page's hooks; task-pipelines.ts sets its own as it loads.
const view = vm.runInContext('TASK_PAGE_HOOKS.pipeline', ctx) as {html: (rec: unknown, tasks: unknown[], opts: unknown) => string} | undefined;
if (!view) throw new Error('the view sets its hook on the page');

// ---- columns, list and phone
{
  const html = view.html(rec(), TASKS, opts());
  assert.equal((html.match(/class="pv-col"/g) || []).length, 3, 'a column per work stage; Done and Closed are not columns');
  assert.match(html, /data-pv-settings="spec"[^>]*><span class="pv-stage-name">Spec<\/span><span class="pv-stage-n tnum full" title="2 of 2 slots used." aria-label="2 of 2 slots used.">2<\/span>/, 'a full stage with a limit shows its limit on its number');
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
  assert.equal((list.match(/class="pv-group"/g) || []).length, 2, 'an empty stage has no header in the list');
  assert.match(list, /<div class="pv-fold" role="group" aria-label="Empty stages: In development"><button type="button" class="pv-chip" data-pv-settings="dev"/,
    'an empty stage after the last task is a chip');
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
  // An empty stage says so in one line: nothing found under a search, else nothing here.
  const searched = view.html(rec(), TASKS, opts({phone: true, q: 'nothing like this'}));
  assert.match(searched, /<p class="pv-empty muted">Nothing found<\/p>/);
  const pv = vm.runInContext('PV', ctx) as {stage: string};
  const was = pv.stage;
  pv.stage = 'dev';   // In development has no task
  const empty = view.html(rec(), TASKS, opts({phone: true}));
  pv.stage = was;
  assert.match(empty, /class="pv-tab on" data-pv-tab="dev"/);
  assert.match(empty, /<p class="pv-empty muted">Nothing here<\/p>/);
}

// ---- the list folds empty stages (#500)
{
  const five = rec({stages: [stage('a', 'Pitch'), stage('b', 'Spec'), stage('c', 'Build'), stage('d', 'Review'), stage('e', 'Fixes'),
    stage('f', 'QA'), stage('g', 'Merge'), stage('h', 'Validate')]});
  const at = (...ids: string[]) => ids.map((id, i) => task(`r${i}`, `Row ${i}`, id));
  const folds = (html: string) => [...html.matchAll(/<div class="pv-fold" role="group" aria-label="Empty stages: ([^"]*)">(.*?)<\/div>/g)].map(m => m[1]);
  const heads = (html: string) => [...html.matchAll(/<span class="pv-stage-name">([^<]*)</g)].map(m => m[1]);
  // Runs before the first task, between tasks and after the last: each one line, in its place; one empty stage alone too.
  const html = view.html(five, at('c', 'e'), opts({layout: 'list'}));
  assert.deepEqual(folds(html), ['Pitch, Spec', 'Review', 'QA, Merge, Validate']);
  assert.deepEqual(heads(html), ['Build', 'Fixes'], 'only stages with tasks keep a header');
  assert.ok(html.indexOf('Empty stages: Pitch') < html.indexOf('>Build<') && html.indexOf('>Build<') < html.indexOf('Empty stages: Review')
    && html.indexOf('Empty stages: Review') < html.indexOf('>Fixes<') && html.indexOf('>Fixes<') < html.indexOf('Empty stages: QA'), 'each run sits in its place');
  assert.match(html, /<button type="button" class="pv-chip" data-pv-settings="a" title="Edit Pitch">Pitch<\/button>/, 'a chip opens its stage, names only');
  assert.doesNotMatch(folds(html).join(''), /\d/, 'no number on a chip');
  const plainChips = view.html(rec({...five, may_change: false}), at('c'), opts({layout: 'list'}));
  assert.match(plainChips, /<span class="pv-chip">Pitch<\/span>/, 'plain text for whoever may not change the pipeline');
  assert.doesNotMatch(plainChips, /<button[^>]*pv-chip/);
  // A stage whose only task is snoozed or blocked is not empty.
  const asleep = view.html(five, [task('s1', 'Asleep', 'b', {snoozed: true}), task('s2', 'Blocked', 'd', {blocked_by: [{id: 'x', number: 9}]})], opts({layout: 'list'}));
  assert.deepEqual(heads(asleep), ['Spec', 'Review']);
  // Nothing open: one picture, one line, New task; no stage names.
  const none = view.html(five, [], opts({layout: 'list'}));
  assert.match(none, /<div class="empty-state pv-none"><svg class="ea-art" aria-hidden="true"><\/svg><p>No open tasks in Product<\/p><button type="button" class="ghost" data-pv-new aria-keyshortcuts="c">New task<\/button><\/div>/);
  assert.doesNotMatch(none, /pv-chip|pv-stage-name/);
  ctx.tasksViewOnly = () => true;
  const readOnly = view.html(five, [], opts({layout: 'list'}));
  ctx.tasksViewOnly = () => false;
  assert.match(readOnly, /No open tasks in Product/);
  assert.doesNotMatch(readOnly, /data-pv-new/, 'no New task in the read-only view');
  // A search or filter that finds nothing: Nothing found, no chips, no empty state.
  for (const o of [{q: 'zzz'}, {filters: {owner: ['bot:nobody']}}]) {
    const found = view.html(five, at('c'), opts({layout: 'list', ...o}));
    assert.match(found, /Nothing found/);
    assert.doesNotMatch(found, /pv-fold|pv-none/);
  }
  // More tasks still to read: every stage keeps its header, nothing is called empty.
  const paged = view.html(five, at('c'), opts({layout: 'list', more: true}));
  assert.equal(heads(paged).length, 8);
  assert.doesNotMatch(paged, /pv-fold|pv-none/);
  assert.doesNotMatch(view.html(five, [], opts({layout: 'list', more: true})), /pv-none/);
  // The columns and the phone do not fold.
  for (const o of [{layout: 'columns'}, {phone: true}, {layout: 'list', phone: true}]) {
    for (const tasks of [at('c'), []]) assert.doesNotMatch(view.html(five, tasks, opts(o)), /pv-fold|pv-none/, JSON.stringify(o));
  }
  assert.equal((view.html(five, at('c'), opts({layout: 'columns'})).match(/class="pv-col"/g) || []).length, 8, 'empty columns keep their place');
}

// ---- a card (task #220): two lines of title, who asked, time in this stage, the owner's face when someone else owns it
{
  const card = (extra: Record<string, unknown>) => vm.runInContext('taskCardHTML', ctx)(task('c1', 'Product brief: saved carts for guests', 'ideas', extra)) as string;
  const aged: string[] = [];
  const age = ctx.ageShort;
  ctx.ageShort = (iso: string) => { aged.push(iso); return '3d'; };
  const other = card({requester_id: 'human:ana', owner_id: 'bot:pm', stage_since: '2026-11-09T10:00:00Z', created_at: '2026-11-01T10:00:00Z',
    updated_at: '2026-11-12T10:00:00Z', late: {since: '2026-11-11T10:00:00Z', level: 'told'}, turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana'}});
  ctx.ageShort = age;
  assert.match(other, /title="Product brief: saved carts for guests\nWaiting on Ana"/, 'pointing at the card shows the whole title');
  assert.match(other, /<span class="tk-title">Product brief: saved carts for guests<\/span><span class="tk-meta">/, 'the meta line is under the title');
  assert.match(other, /<span class="tk-asker"><i data-face="human:ana"><\/i><span class="tk-name">ana<\/span><\/span>/, "the asker's face and name");
  assert.deepEqual(aged, ['2026-11-09T10:00:00Z'], 'the age is time in this stage, not since filed or last changed');
  assert.match(other, /<span class="tk-age">3d<\/span><span class="tk-owner" role="img" aria-label="Owner: pm" title="Owner: pm"><i data-face="bot:pm"><\/i><\/span><\/span><\/div>$/,
    "the owner's face last, saying whose it is");
  const meta = other.slice(other.indexOf('class="tk-meta"'));
  assert.match(meta, /tk-late/, 'Late is on the meta line');
  assert.match(meta, /tk-mark-person/, 'the mark is on the meta line');
  const own = card({requester_id: 'human:ana', owner_id: 'human:ana'});
  assert.equal((own.match(/data-face=/g) || []).length, 1, 'the asker owns it: one face, the name once');
  assert.doesNotMatch(own, /tk-owner/);
  const filed = card({requester_id: null, owner_id: 'bot:pm', created_at: '2026-11-01T10:00:00Z'});
  assert.doesNotMatch(filed, /tk-asker/, 'no asker, no name');
  assert.match(filed, /title="Owner: pm"/, 'the owner still shows');
  const phone = view.html(rec(), TASKS, opts({phone: true}));
  assert.match(phone, /<div class="pv-phone"><div class="tk-card" data-task="t1"[^]*class="tk-asker"/, "a phone's stage shows the same card");
}

// ---- a limited stage counts bot work; tasks waiting only on a person's OK hold no slot: one count, its tooltip says so
{
  const waiting = Array.from({length: 15}, (_, i) => task(`w${i}`, `Spec ${i}`, 'spec', {pipeline_id: 'spec20', on_person: true,
    turn: {state: 'person', who: 'human:chris', line: 'OK the spec', ask: true}}));
  const working = Array.from({length: 5}, (_, i) => task(`b${i}`, `Draft ${i}`, 'spec', {pipeline_id: 'spec20'}));
  const twenty = rec({id: 'spec20', stages: [stage('ideas', 'Ideas', {by_hand: true}), stage('spec', 'Spec', {task_limit: 20}), stage('dev', 'In development')]});
  const html = view.html(twenty, [...waiting, ...working], opts());
  const words = '5 of 20 slots used. 15 more wait on a person and hold no slot.';
  assert.match(html, new RegExp(`<span class="pv-stage-name">Spec</span><span class="pv-stage-n tnum" title="${words}" aria-label="${words}">20</span></button>`),
    'Spec 20, the tasks it lists; the slots said in its tooltip and name, and no limit on the number while a slot is free');
  assert.doesNotMatch(html, /pv-stage-person/, 'no person pill');
  assert.equal((html.match(/class="tk-card"/g) || []).length, 20, 'every task is still drawn');
  const list = view.html(twenty, [...waiting, ...working], opts({layout: 'list'}));
  assert.match(list, new RegExp(`title="${words}"`));
  assert.doesNotMatch(list, /pv-stage-person/);
  const phone = view.html(twenty, [...waiting, ...working], opts({phone: true}));
  assert.match(phone, /data-pv-tab="spec"[^>]*>Spec <span class="tnum">20<\/span>/, 'the phone tab counts every task it lists, bot work too');
  assert.doesNotMatch(phone, /pv-stage-person/);
  assert.match(phone, new RegExp(`</div><p class="pv-slots">${words}</p><div class="pv-phone">`), 'a phone says it in one line above the cards');
  const one = view.html(twenty, [waiting[0], ...working], opts());
  assert.match(one, /title="5 of 20 slots used\. 1 more waits on a person and holds no slot\."/);
  const none = view.html(twenty, working, opts({phone: true}));
  assert.doesNotMatch(none, /pv-slots/, 'none waits on a person: no line on the phone');
  assert.match(view.html(twenty, working, opts()), /title="5 of 20 slots used\." aria-label="5 of 20 slots used\.">5<\/span>/);
  const packed = Array.from({length: 20}, (_, i) => task(`f${i}`, `Ship ${i}`, 'spec', {pipeline_id: 'spec20'}));
  assert.match(view.html(twenty, packed, opts()), /<span class="pv-stage-n tnum full" title="20 of 20 slots used\." aria-label="20 of 20 slots used\.">20<\/span>/,
    'every slot used: the limit on the number, and the one time it shows');
  assert.match(view.html(twenty, packed, opts({phone: true})), /data-pv-tab="spec"[^>]*>Spec <span class="tnum full">20<\/span>/, 'and on the phone tab');
  const snoozed = {...working[0], id: 'z1', snoozed: true};
  assert.match(view.html(twenty, [...working, snoozed], opts()), /title="5 of 20 slots used\." aria-label="5 of 20 slots used\.">6<\/span>/,
    'a snoozed task is listed, so it is counted, and holds no slot');
  const open = view.html(rec({id: 'spec20', stages: [stage('ideas', 'Ideas', {by_hand: true}), stage('spec', 'Spec')]}), [...waiting, ...working], opts());
  assert.match(open, /<span class="pv-stage-n tnum">20<\/span><\/button>/, 'without a limit every task counts, with no tooltip');
}

// ---- ▶ keeps its slot when nothing waits on the person, so nothing moves when the count arrives
{
  const html = view.html(rec(), TASKS, opts());
  assert.match(html, /<span class="pv-play pv-play-none" aria-hidden="true"><\/span>/, 'an empty ▶ slot');
  assert.doesNotMatch(html, /data-pv-play=/, 'nothing to click');
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

// ---- Strategy › Org's Factories section (read through factoriesRead, so this part waits)
const factories = (async () => {
  const recs = [
    rec({id: 'eng', name: 'Engineering', factory_id: 'f-product', stages: [stage('b', 'Build')]}),
    rec({id: 'product', name: 'Product', factory_id: 'f-product', stages: [stage('s', 'Spec')]}),
    rec({id: 'general', name: 'General', factory_id: null, stages: [stage('g', 'Doing')]}),
    rec({id: 'old', name: 'Old', factory_id: null, retired_at: '2026-11-01T00:00:00Z'}),
  ];
  const map = {factories: [{id: 'f-product', name: 'Product', goal: null, manager_id: null, done: 4}],
    pipelines: [{id: 'product', name: 'Product', factory_id: 'f-product', open: 17, files_into: ['eng'], mark: 'needs_person'},
      {id: 'eng', name: 'Engineering', factory_id: 'f-product', open: 9, files_into: [], mark: null},
      {id: 'general', name: 'General', factory_id: null, open: 31, files_into: [], mark: 'cant_run'}]};
  ctx.get = async () => map;
  const read = await ctx.factoriesRead('7d');
  assert.deepEqual(plain(read.pipelines.map((p: {files_into: string[]}) => p.files_into)), [['eng'], [], []]);
  const groups = plain(ctx.orgFacGroups(read, recs));
  assert.deepEqual(groups.map((g: {id: string; pipelines: {id: string}[]}) => [g.id, g.pipelines.map(p => p.id)]),
    [['f-product', ['product', 'eng']], ['', ['general']]], 'a pipeline comes before the one it files into; retired ones go');
})();

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
// ---- the editor: the new kinds, their settings and On entering › Then
{
  const state = {id: 'product', pipelines: [{id: 'product', name: 'Product'}, {id: 'engineering', name: 'Engineering'}],
    draft: rec({stages: [stage('spec', 'Spec', {requirements: [{...req('page'), who_oks: 'people_with_exceptions'}]}),
      stage('release', 'Release', {requirements: [{...req('item'), source: 'github', condition: 'in_release'}]})]})};
  // A requirement's editor, opened under its row: its fields, its switches and Remove (the kind is picked when it is
  // added, + Requirement's list, and is the row's words).
  const kinds = ctx.peReqEdHTML(state, state.draft.stages[0]!.requirements[0]!, 0, 0, 'ed-0') as string;
  assert.doesNotMatch(kinds, /data-pe-rf="kind"|<details|<summary/, 'no Needs list, nothing to expand');
  assert.match(kinds, /^<div class="pe-row-ed[^"]*" id="ed-0">/);
  assert.match(kinds, /<span>Page<\/span><input data-pe-rf="line"/);
  assert.match(kinds, /data-pe-row-x="req:0"[^>]*>Remove</);
  // + Requirement's list, in plain words: an OK from anyone (by hand) first, In a release among them
  assert.deepEqual(plain((vm.runInContext('PE_REQ_PICKS', ctx) as string[][]).map(k => k[0]).slice(0, 3)), ['by_hand', 'approval', 'ok_bot']);
  assert.ok((vm.runInContext('PE_REQ_PICKS', ctx) as string[][]).some(k => k[0] === 'in_release'));
  assert.match(kinds, /<span>Then OK from<\/span><select[^>]*><option value="">Nobody<\/option>/, 'a page needs no person');
  assert.match(kinds, /data-pe-rs="at_head"[^>]*> Newest commit only/);
  assert.match(kinds, /data-pe-rs="with_release"[^>]*> With its release/);
  const release = ctx.peReqEdHTML(state, state.draft.stages[1]!.requirements[0]!, 1, 0, 'ed-1') as string;
  assert.match(release, /data-pe-rf="release_repo"/);
  assert.match(release, /data-pe-rf="release_with"[^>]*placeholder="workflow:release.yml or tag"/);
  assert.match(release, /data-pe-rs="prereleases"[^>]* checked> Pre-releases count/, 'omission renders the enabled default');
  // An approval names its document and its approver (any person, or one), and has no page switches.
  const approval = {kind: 'page', source: null, condition: null, settings: {document: 'QA report'}, can_push_past: true, who_oks: 'people_only',
    person_id: null, line: 'QA report'};
  const approves = ctx.peReqEdHTML(state, approval, 0, 1, 'ed-a') as string;
  assert.match(approves, /<span>Document<\/span><select data-pe-rf="document"[^>]*>.*<option value="QA report" selected>QA report<\/option>/);
  assert.match(approves, /<span>Approver<\/span><select data-pe-rf="approver"[^>]*><option value="">Any person<\/option>/);
  assert.doesNotMatch(approves, /data-pe-rs="at_head"|data-pe-rf="line"/, 'no page switches, no title: the document is its name');
  assert.equal(ctx.peReqText(approval), 'Approve QA report');
  assert.deepEqual(plain(ctx.peReqNew(state, 'approval')), {kind: 'page', source: null, condition: null, settings: {document: 'Spec'},
    can_push_past: true, who_oks: 'people_only', person_id: null, line: 'Spec'});
  assert.match(release, /data-pe-rf="can_push_past"[^>]*disabled/, 'In a release is never pushed past');

  const field = (kind: 'HTMLInputElement' | 'HTMLSelectElement', data: Record<string, string>, value = '', checked = false) =>
    Object.assign(Object.create(ctx[kind].prototype), {dataset: data, value, checked, type: 'peRs' in data ? 'checkbox' : 'text'});
  ctx.peInput(state, field('HTMLInputElement', {peSi: '0', peRi: '0', peRs: 'at_head'}, 'on', true));
  ctx.peInput(state, field('HTMLInputElement', {peSi: '1', peRi: '0', peRs: 'prereleases'}, 'on', true));
  ctx.peInput(state, field('HTMLInputElement', {peSi: '1', peRi: '0', peRf: 'release_repo'}, ' ticoteam/tico '));
  ctx.peInput(state, field('HTMLInputElement', {peSi: '1', peRi: '0', peRf: 'release_with'}, ' workflow:release.yml '));
  assert.deepEqual(plain(state.draft.stages[0]!.requirements[0]!.settings), {at_head: true});
  assert.deepEqual(plain(state.draft.stages[1]!.requirements[0]!.settings),
    {prereleases: true, release_repo: 'ticoteam/tico', release_with: 'workflow:release.yml'});
  ctx.peInput(state, field('HTMLInputElement', {peSi: '0', peRi: '0', peRs: 'at_head'}, 'on', false));
  assert.deepEqual(plain(state.draft.stages[0]!.requirements[0]!.settings), {}, 'a switch off is not saved');
  ctx.peInput(state, field('HTMLInputElement', {peSi: '1', peRi: '0', peRs: 'prereleases'}, 'on', false));
  const saved = plain(ctx.peOut(state.draft));
  assert.equal(saved.stages[1]!.requirements[0]!.settings.prereleases, false, 'an opt-out is explicit in the save payload');
  assert.doesNotMatch(ctx.peReqEdHTML({...state, draft: saved}, saved.stages[1]!.requirements[0]!, 1, 0, 'ed-1'),
    /data-pe-rs="prereleases"[^>]* checked/, 'the saved opt-out renders off');

  // On entering is one list: the Limit and the actions (the owner it changes to, what it opens or queues) are rows only
  // when set, then the rules, then + Rule (a rule or the Limit) and + Action, whose picks offer only what is not set
  // (the first stage has no Limit).
  // What is stored is as before: task_limit, owner (and pick), and on_enter {}, {file_into: id} or {act: 'github.queue'};
  // "Do nothing" and "Not set" are no choice: they are no row.
  const onEnter = (si: number) => ctx.peEnterHTML(state, state.draft.stages[si]!, si, `p-${si}`) as string;
  const picks = (si: number) => plain(ctx.peEnterPicks(state, state.draft.stages[si]!, si)).map((p: string[]) => p[1]);
  assert.doesNotMatch(onEnter(0), /data-pe-enter-row|Do nothing|data-pe-sf/, 'nothing set: no row, no field');
  // the first stage with the rules unread: nothing for + Rule (no Limit there), so + Action alone
  assert.match(onEnter(0), /<div class="pe-adds"><button type="button" class="ghost pe-add" data-pe-enter-add="act" data-pe-si="0" aria-haspopup="menu" aria-expanded="false">\+ Action<\/button><\/div><\/div>$/);
  // a later stage: + Rule lists Rule and Limit (here the Limit alone), + Action the actions
  assert.match(onEnter(1), /<div class="pe-adds"><button[^>]*data-pe-enter-add="rule"[^>]*aria-haspopup="menu"[^>]*>\+ Rule<\/button><button[^>]*data-pe-enter-add="act"[^>]*>\+ Action<\/button><\/div>/);
  assert.deepEqual(plain(vm.runInContext('peEnterGroup', ctx)(ctx.peEnterPicks(state, state.draft.stages[1]!, 1), 'act')).map((p: string[]) => p[1]),
    ['Change the owner…', 'Open a linked task in…', 'Add its PRs to the merge queue']);
  assert.deepEqual(picks(0), ['Change the owner…', 'Open a linked task in…', 'Add its PRs to the merge queue'], 'the first stage: no Limit; the rules unread: no Rule');
  assert.deepEqual(picks(1), ['Limit', 'Change the owner…', 'Open a linked task in…', 'Add its PRs to the merge queue']);
  // A Limit's row says it; its editor, opened in place, is the old field's number, with Remove; typing stores it as the
  // field did.
  (state.draft.stages[1] as Record<string, unknown>)['task_limit'] = 4;
  assert.match(onEnter(1), /<li class="sr-r" data-pe-enter-row="limit" data-pe-si="1"><button type="button" class="sr-open" data-pe-row="limit" data-pe-si="1" aria-expanded="false" aria-controls="pe-ed-p-1-limit" data-icon="gavel">At most 4 tasks at once<\/button><\/li>/);
  assert.deepEqual(picks(1), ['Change the owner…', 'Open a linked task in…', 'Add its PRs to the merge queue'], 'a Limit set is not offered');
  (state as Record<string, unknown>)['rowOpen'] = '1:limit';
  assert.match(onEnter(1), /aria-expanded="true"[^>]*>At most 4 tasks at once<\/button><div class="pe-row-ed" id="pe-ed-p-1-limit"><label class="pe-f pe-f-short"><span>Tasks at once<\/span><input type="number" min="1" step="1" inputmode="numeric" data-pe-sf="task_limit" data-pe-si="1" value="4"><\/label><button type="button" class="ghost pe-row-x" data-pe-row-x="limit" data-pe-si="1">Remove<\/button><\/div>/);
  ctx.peInput(state, field('HTMLInputElement', {peSi: '1', peSf: 'task_limit'}, '6'));
  assert.equal(state.draft.stages[1]!.task_limit, 6);
  ctx.peInput(state, field('HTMLInputElement', {peSi: '1', peSf: 'task_limit'}, ''));
  assert.equal(state.draft.stages[1]!.task_limit, null, 'emptied: no limit, as the field stored it');
  assert.equal(vm.runInContext('peLimitText(1)', ctx), 'At most 1 task at once');
  // An action's row says it, with the pipeline by name; its editor picks the action and, for a linked task, the
  // pipeline (another one, never its own).
  ctx.peInput(state, field('HTMLSelectElement', {peSi: '0', peSf: 'on_enter_kind'}, 'file_into'));
  assert.deepEqual(plain(state.draft.stages[0]!.on_enter), {file_into: 'engineering'}, 'the first other pipeline, never its own');
  assert.match(onEnter(0), /data-pe-enter-row="act"[^>]*><button[^>]*data-pe-row="act"[^>]*data-icon="bolt">Open a linked task in Engineering<\/button>/);
  assert.deepEqual(picks(0), ['Change the owner…'], 'the stage stores one action of its own kind');
  (state as Record<string, unknown>)['rowOpen'] = '0:act';
  assert.match(onEnter(0), /<span>Then<\/span><select data-pe-sf="on_enter_kind" data-pe-si="0"><option value="file_into" selected>Open a linked task in…<\/option><option value="act:github.queue">Add its PRs to the merge queue<\/option><\/select>/);
  assert.match(onEnter(0), /<span>Pipeline<\/span><select data-pe-sf="on_enter"[^>]*><option value="file_into:engineering" selected>Engineering<\/option><\/select>/,
    'a stage opens a linked task in another pipeline, not its own');
  assert.match(onEnter(0), /data-pe-row-x="act"[^>]*>Remove</);
  ctx.peInput(state, field('HTMLSelectElement', {peSi: '0', peSf: 'on_enter'}, 'file_into:engineering'));
  assert.deepEqual(plain(state.draft.stages[0]!.on_enter), {file_into: 'engineering'});
  ctx.peInput(state, field('HTMLSelectElement', {peSi: '1', peSf: 'on_enter_kind'}, 'act:github.queue'));
  assert.deepEqual(plain(state.draft.stages[1]!.on_enter), {act: 'github.queue'});
  assert.match(onEnter(1), /data-icon="bolt">Add its PRs to the merge queue<\/button>/);
  // read only: the rows, with nothing to open, and no add button
  const roEnter = ctx.peEnterHTML({...state, readOnly: true}, state.draft.stages[0]!, 0, 'p-0') as string;
  assert.match(roEnter, /<button type="button" class="sr-open" disabled data-icon="bolt">Open a linked task in Engineering<\/button><\/li>/);
  assert.doesNotMatch(roEnter, /data-pe-enter-add|data-pe-row|pe-row-ed/);
  // A change of owner: a row like an action's, set by name, the task's assignee or one of several; its editor holds the
  // Owner (no empty choice: Remove is how none is set) and, for one of several, its controls.
  const ownerTo = (si: number, value: string) => ctx.peInput(state, field('HTMLSelectElement', {peSi: String(si), peSf: 'owner'}, value));
  (state as Record<string, unknown>)['base'] = JSON.parse(JSON.stringify(state.draft));
  ownerTo(0, 'assignee');
  assert.equal(state.draft.stages[0]!.owner, 'assignee');
  assert.match(onEnter(0), /data-pe-enter-row="owner"[^>]*><button[^>]*data-pe-row="owner"[^>]*data-icon="bolt">Change the owner to the task's assignee<\/button>/);
  assert.ok(onEnter(0).indexOf('data-pe-enter-row="owner"') < onEnter(0).indexOf('data-pe-enter-row="act"'), 'the owner, then what it opens');
  assert.ok(onEnter(0).indexOf('data-sr-phase="enter"') < onEnter(0).indexOf('data-pe-enter-row="owner"'), 'the rules above the actions');
  assert.deepEqual(picks(0), [], 'nothing left to add');
  assert.doesNotMatch(onEnter(0), /data-pe-enter-add/, 'nothing left to add: no add button');
  (state as Record<string, unknown>)['rowOpen'] = '0:owner';
  assert.match(onEnter(0), /<div class="pe-row-ed" id="pe-ed-p-0-owner"><label class="pe-f"><span>Owner<\/span><select data-pe-sf="owner" data-pe-si="0"><option value="assignee" selected>The task&#39;s assignee<\/option><option value="pick">One of several<\/option>/);
  assert.doesNotMatch(onEnter(0), /<option value="">/, 'no empty choice: Remove sets none');
  ownerTo(0, 'pick');
  assert.match(onEnter(0), /data-icon="bolt">Change the owner to one of several · takes turns<\/button>/);
  assert.match(onEnter(0), /data-pe-row-x="owner"[^>]*>Remove<\/button><div class="pe-pick">/, 'its controls under the Owner and Remove');
  (state.draft.stages[0] as Record<string, unknown>)['pick'] = {bots: ['bot:a', 'bot:b', 'bot:c'], how: 'random', limit_each: null, prefer: 'anyone', owns_task: false};
  assert.match(onEnter(0), />Change the owner to one of several: a, b \+1 · at random<\/button>/);
  ctx.peClick(state, {closest: () => ({dataset: {peSi: '0', peRowX: 'owner'}, hasAttribute: () => false})});
  assert.equal(state.draft.stages[0]!.owner, null, 'Remove: none set');
  assert.equal('pick' in state.draft.stages[0]!, false);
  assert.doesNotMatch(onEnter(0), /data-pe-enter-row="owner"/);
  state.draft.stages[1]!.on_enter = {};
  (state as Record<string, unknown>)['rowOpen'] = '';

  // + Requirement makes a requirement of the kind picked (peReqNew): a GitHub condition is an item of source github.
  const merged = plain(ctx.peReqNew(state, 'pr_merged')) as Record<string, unknown>;
  assert.deepEqual([merged['kind'], merged['source'], merged['condition'], merged['can_push_past']], ['item', 'github', 'pr_merged', false],
    'a GitHub condition is saved as an item of source github, never pushed past');
  state.draft.stages[0]!.requirements[0] = merged as never;
  assert.equal(ctx.peReqText(state.draft.stages[0]!.requirements[0]!), 'PR merged', 'its row says it');
  const done = plain(ctx.peReqNew(state, 'owner_done')) as Record<string, unknown>;
  assert.deepEqual([done['kind'], done['source'], done['condition']], ['owner_done', null, null]);

  // By hand is one more requirement, "OK from anyone" (the stage's by_hand): its row, opened, offers whether a person's
  // request moves on by itself; Remove takes by hand off and clears it.
  const ideas = stage('ideas', 'Ideas', {by_hand: true});
  state.draft.stages.unshift(ideas);
  (state as Record<string, unknown>)['open'] = new WeakSet();
  assert.match(ctx.peStageHTML(state, ideas, 0, 3), /data-pe-leave-row="hand"/);
  assert.match(ctx.peStageHTML(state, ideas, 0, 3), /OK from anyone/);
  assert.doesNotMatch(ctx.peStageHTML(state, ideas, 0, 3), /requests_move_on/, 'shut: the row alone');
  (state as Record<string, unknown>)['rowOpen'] = '0:hand';
  assert.match(ctx.peStageHTML(state, ideas, 0, 3), /data-pe-sf="requests_move_on"[^>]*> A person's request moves on/);
  assert.doesNotMatch(ctx.peStageHTML(state, state.draft.stages[1]!, 1, 3), /requests_move_on|data-pe-leave-row="hand"/);
  ctx.peInput(state, Object.assign(field('HTMLInputElement', {peSi: '0', peSf: 'requests_move_on'}, 'on', true), {type: 'checkbox'}));
  assert.equal(state.draft.stages[0]!['requests_move_on'], true);
  ctx.peClick(state, {closest: () => ({dataset: {peSi: '0', peRowX: 'hand'}, hasAttribute: () => false})});
  assert.equal(state.draft.stages[0]!['by_hand'], false);
  assert.equal(state.draft.stages[0]!['requests_move_on'], false, 'only a by-hand stage keeps it');
}
// ---- the editor: the stage window in task order, and an Owner that is "One of several"
{
  const bots = [{id: 'b1', name: 'Builder'}, {id: 'b2', name: 'Builder 2'}, {id: 'r1', name: 'Reviewer 1'}];
  ctx.S.emps = bots.map(b => ({id: b.id, state: 'active'}));
  ctx.botDisplayName = (id: string) => bots.find(b => b.id === id)?.name ?? id;
  const pick = {bots: ['bot:b1', 'bot:b2'], how: 'turns', limit_each: null, prefer: 'anyone', owns_task: true,
    families: {'bot:b1': 'Claude', 'bot:b2': 'Claude'}};
  const build = stage('build', 'Build', {owner: 'pick', pick, pick_next: 'The next task goes to Builder 2, after Builder had the last one.', task_limit: 3});
  const review = stage('review', 'Review', {owner: 'bot:r1'});
  const base = rec({stages: [stage('deck', 'On deck'), build, review]});
  const state = {id: 'product', pipelines: [], open: new WeakSet(), base, draft: JSON.parse(JSON.stringify(base))};
  const html = (si: number) => ctx.peStageHTML(state, state.draft.stages[si]!, si, 3) as string;

  // Name and Goal, then the phases in the order a task moves, each a heading with a small leading mark, with every
  // field the window had before (AC-11) and, last in each, the rules that act there.
  let body = html(1);
  const groups = [...body.matchAll(/<h4 class="pe-h pe-phase-h"[^>]*><span class="nav-icon" aria-hidden="true">(\w+)<\/span>([^<]+)<\/h4>/g)].map(m => `${m[1]} ${m[2]}`);
  assert.deepEqual(groups, ['login On entering', 'schedule While here', 'logout To leave']);
  assert.doesNotMatch(body, />Rules</, 'no Rules heading');
  const at = (needle: string) => { const i = body.indexOf(needle); assert.ok(i >= 0, needle); return i; };
  const [arrives, works, leave] = ['On entering', 'While here', 'To leave'].map(g => at(`>${g}<`)) as [number, number, number];
  for (const f of ['data-pe-sf="name"', 'data-pe-sf="purpose"']) assert.ok(at(f) < arrives, `${f} is at the top`);
  for (const f of ['data-pe-enter-row="limit"', 'data-pe-enter-row="owner"', 'data-sr-phase="enter"', 'data-pe-enter-add']) assert.ok(at(f) > arrives && at(f) < works, `${f} is under On entering`);
  assert.doesNotMatch(body, /data-pe-sf="(on_enter_kind|task_limit|owner)"/, 'On entering has rows, not fields (its editors closed); While here has no Owner');
  assert.match(body, /data-icon="bolt">Change the owner to one of several: b1, b2 · takes turns<\/button>/);
  for (const f of ['data-pe-sf="expected_n"', 'data-sr-phase="here"']) assert.ok(at(f) > works && at(f) < leave, `${f} is under While here`);
  for (const f of ['data-pe-req-add']) assert.ok(at(f) > leave && at(f) < at('data-pe-more='), `${f} is under To leave`);
  assert.match(body, /<span>Late after<\/span>/);
  assert.doesNotMatch(body, /data-pe-seg="by_hand"|Expected time/, 'no Tasks leave switch');
  at('data-pe-sf="expected_unit"');
  // On entering, one list: the rules first (the Limit, then the rules acting there), then the actions (the owner
  // change), then + Rule and + Action; the other phases' rules come after their fields
  assert.ok(at('data-pe-enter-row="limit"') < at('data-sr-phase="enter"') && at('data-sr-phase="enter"') < at('data-pe-enter-row="owner"')
    && at('data-pe-enter-row="owner"') < at('data-pe-enter-add'), 'Limit, rules, owner, add');
  assert.ok(at('data-sr-phase="here"') > at('data-pe-sf="expected_unit"'), 'a phase\'s rules follow its fields');
  // The stage's purpose is labelled Goal (its stored key stays `purpose`).
  assert.match(body, /<label class="pe-f"><span>Goal<\/span><input data-pe-sf="purpose"/);
  assert.doesNotMatch(body, />Purpose</);
  // More rules ends the stage: one shut line that opens the snooze rules, then the built-in ones.
  assert.match(body, /<button type="button" class="pe-h pe-phase-h pe-more-h"[^>]*data-pe-more="[^"]+" aria-expanded="false"[^>]*><span class="nav-icon" aria-hidden="true">gavel<\/span>More rules/);
  assert.match(body, /<div class="pe-more-body" id="[^"]+" hidden><div class="sr-phase" data-sr-section="build" data-sr-phase="more"><\/div><div class="sr-phase" data-sr-section="build" data-sr-phase="always"><\/div><\/div><\/section>/);
  // Read only (the viewer may not change the pipeline): the same fields, off, with nothing to add or remove; each
  // phase's rules stay outside its switched-off fields, so their rows still open.
  const ro = ctx.peStageHTML({...state, readOnly: true}, state.draft.stages[1]!, 1, 3) as string;
  assert.equal([...ro.matchAll(/<fieldset class="pe-ro" disabled>/g)].length, 2, 'the top row and the fields of While here are off (To leave is rows, with nothing to open)');
  assert.match(ro, /data-pe-enter-row="limit"[^>]*><button type="button" class="sr-open" disabled/, 'On entering\'s rows: nothing to open');
  assert.doesNotMatch(ro, /data-pe-enter-add/);
  for (const ph of ['here']) {
    const sec = ro.slice(ro.indexOf(`data-pe-phase="${ph}"`));
    assert.ok(sec.indexOf(`data-sr-phase="${ph}"`) > sec.indexOf('</fieldset>'), `${ph}: the rules sit after the fieldset`);
  }
  assert.doesNotMatch(ro, /data-pe-req-add|data-pe-req-x|data-pe-up|data-pe-stage-x/);
  // One of several, its row's editor open: the Owner, then the bots with their model family, Pick, Limit each, Prefer,
  // the owner switch and the sentence, as before.
  assert.doesNotMatch(body, /data-pe-pick/, 'closed: the row says it');
  (state as Record<string, unknown>)['rowOpen'] = '1:owner';
  const closed = body;
  body = html(1);
  assert.match(body, /<option value="pick" selected>One of several<\/option>/);
  assert.match(body, /<input type="checkbox" data-pe-pick-bot="bot:b1"[^>]* checked> Builder <span class="pe-family muted">Claude<\/span>/);
  assert.match(body, /<input type="checkbox" data-pe-pick-bot="bot:r1"[^>]*> Reviewer 1 <\/label>/, 'a bot not on the list, its family not read yet');
  state.draft.bot_families = {'bot:b1': 'Claude', 'bot:b2': 'Claude', 'bot:r1': 'GPT'};
  assert.match(html(1), /data-pe-pick-bot="bot:r1"[^>]*> Reviewer 1 <span class="pe-family muted">GPT<\/span>/, 'a bot not ticked yet shows its family when the record has them all');
  assert.equal('bot_families' in plain(ctx.peOut(state.draft)), false, 'the families are the server\'s');
  delete state.draft.bot_families;
  assert.match(body, /aria-label="Pick"[^>]*><button[^>]*data-v="turns" aria-checked="true"[^>]*>Take turns<\/button><button[^>]*data-v="random" aria-checked="false"[^>]*>Random<\/button>/);
  assert.match(body, /aria-label="Prefer"[^>]*><button[^>]*data-v="anyone" aria-checked="true"[^>]*>Anyone<\/button><button[^>]*data-v="new"[^>]*>New to the task<\/button><button[^>]*data-v="family"[^>]*>A different model family<\/button>/);
  assert.match(body, /<span>Limit each<\/span><input type="number"[^>]*data-pe-pick="limit_each"[^>]*value="" placeholder="None">/);
  assert.match(body, /data-pe-pick="owns_task"[^>]* checked> The pick becomes the task's owner/);
  assert.match(body, /<p class="pe-pick-next">The next task goes to Builder 2, after Builder had the last one\.<\/p>/);
  // A single owner shows none of it.
  assert.doesNotMatch(html(2), /data-pe-pick|pe-pick-next/);

  // The closed row: "One of 2" where its owner's name was, the bots named when pointed at.
  assert.match(closed, /<span class="pe-stage-owner pe-stage-pool" title="b1, b2"><span>One of 2<\/span><\/span>/);

  // Editing it: the draft follows, the sentence waits for a save, and the save leaves out what the server writes.
  const field = (kind: 'HTMLInputElement' | 'HTMLSelectElement', data: Record<string, string>, value = '', checked = false, type = 'text') =>
    Object.assign(Object.create(ctx[kind].prototype), {dataset: {peSi: '1', ...data}, value, checked, type});
  const button = (data: Record<string, string>) => ({closest: () => ({dataset: {peSi: '1', ...data}, hasAttribute: (a: string) => a === 'data-pe-seg'})});
  ctx.peInput(state, field('HTMLInputElement', {pePickBot: 'bot:r1'}, 'on', true, 'checkbox'));
  ctx.peInput(state, field('HTMLInputElement', {pePickBot: 'bot:b1'}, 'on', false, 'checkbox'));
  ctx.peInput(state, field('HTMLInputElement', {pePick: 'limit_each'}, '2'));
  ctx.peInput(state, field('HTMLInputElement', {pePick: 'owns_task'}, 'on', false, 'checkbox'));
  ctx.peClick(state, button({peSeg: 'pick_how', v: 'random'}));
  ctx.peClick(state, button({peSeg: 'pick_prefer', v: 'family'}));
  // by hand ("OK from anyone", added from + Requirement) is the stage's own by_hand
  (state.draft.stages[1] as Record<string, unknown>)['by_hand'] = true;
  const sent = plain(ctx.peOut(state.draft)).stages[1] as Record<string, unknown>;
  assert.deepEqual(sent['pick'], {bots: ['bot:b2', 'bot:r1'], how: 'random', limit_each: 2, prefer: 'family', owns_task: false});
  assert.equal('pick_next' in sent, false, 'the sentence is the server\'s');
  assert.equal(sent['by_hand'], true);
  assert.match(html(1), /<p class="pe-pick-next muted">Save to see who gets the next task\.<\/p>/);
  assert.deepEqual(plain(ctx.peChanged(base, {...base, stages: base.stages.map((s: Record<string, unknown>) => s['id'] === 'build' ? {...s, pick_next: 'The next task goes to Builder.'} : s)})), [],
    'a sentence that moved on is not someone else\'s change');
  ctx.peInput(state, field('HTMLInputElement', {pePick: 'limit_each'}, ''));
  assert.equal(state.draft.stages[1]!.pick!.limit_each, null, 'Limit each is optional');

  // Choosing it on a stage starts an empty list that takes turns; leaving it drops the list.
  ctx.peInput(state, field('HTMLSelectElement', {peSi: '2', peSf: 'owner'}, 'pick'));
  assert.deepEqual(plain(state.draft.stages[2]!.pick), {bots: [], how: 'turns', limit_each: null, prefer: 'anyone', owns_task: false});
  ctx.peInput(state, field('HTMLSelectElement', {peSi: '1', peSf: 'owner'}, 'bot:b2'));
  const one = plain(ctx.peOut(state.draft)).stages[1] as Record<string, unknown>;
  assert.equal(one['owner'], 'bot:b2');
  assert.equal('pick' in one, false, 'a single owner sends no list');
}
// ---- a task waiting for a free bot: its card's mark, and its line in the task window
{
  const waiting = task('t9', 'Refund flow', 'spec', {owner_id: null, turn: {state: 'pick', who: 'review', line: 'Waiting for a free Reviewer', since: null}});
  const card = ctx.taskCardHTML(waiting) as string;
  assert.match(card, /<span class="tk-mark tk-mark-pick" role="img" aria-label="Waiting for a free Reviewer"><\/span>/);
  assert.match(card, /Waiting for a free Reviewer"/, 'pointing at the card shows the line');
  assert.equal(ctx.taskLine(waiting), 'Waiting for a free Reviewer');
}
// ---- a task's row for a stage's OK
{
  const row = (extra: Record<string, unknown>) => ctx.taskReqRowHTML({requirement_id: 'r1', kind: 'ok', line: 'Confidence', met: false,
    standing: 'How sure are you?', who: 'bot:builder', asked_by: null, shows: [], answer: null, may: [], ...extra}) as string;
  const open = row({});
  assert.match(open, /<span class="task-req-name">Confidence<\/span>/, 'the title only');
  // What it is waiting on is in the row's tooltip; the row itself draws the title alone.
  assert.match(open, / title="Not yet · Checks: builder · How sure are you\?"/);
  assert.doesNotMatch(open.replace(/ title="[^"]*"/, ''), /How sure/);
  const met = row({met: true, who: null, standing: 'OK', answer: {id: 'a1', at: '', by: 'bot:builder', note: null, value: 'ok'}});
  assert.match(met, /<span class="task-req-name">Confidence<\/span>/);
  assert.doesNotMatch(met.replace(/ title="[^"]*"/, ''), /task-req-val/, 'once met, still the title alone');
}
factories.then(() => console.log('pipeline-view-unit: ok'), error => { console.error(error); process.exitCode = 1; });
