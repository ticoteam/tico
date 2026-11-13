// No browser: the pipeline screens' drawing and decisions (docs/pipelines-v3.md S7, S8, S11, S12, S17.3). The classic
// scripts are compiled with esbuild's transform and run in one vm context, with the few shared globals they read stubbed:
// - the pipeline view's hook draws columns, the list and the phone's tabs from the tasks the page gives it, with each
//   stage's count out of its limit, and a stage header that opens settings only for whoever may change the pipeline;
// - against a limit the count is the tasks that hold a slot; one count per stage: those waiting only on a person
//   (`on_person`) are said in the count's tooltip and name ("5 of 20 slots used. 15 more wait on a person and hold no
//   slot."), and on a phone in one muted line above the open stage's cards; no person pill;
// - a refused move reads as the engine said it: a push past asks why, anything else is the engine's line;
// - Company › Factories orders a factory's pipelines by where they file, joins them with an arrow, and puts pipelines in
//   no factory under Other with no count;
// - the editor names what someone else changed when its save is refused as out of date;
// - the editor offers every kind, a page's and In a release's settings, and a stage's On enter, and saves them as the
//   record stores them; an answer's name follows its title until edited, and its type keeps only its own fields;
// - a task's row for a stage's answer is its title, and once met its value.
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
  document: {addEventListener() {}, getElementById: () => null},
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
  taskOwnerOptions: () => '',
  stageRulesButtonHTML: () => '',   // stage-rules.cts draws a stage's Rules button
  needsStageCount: () => 0,   // nothing waits on the person here: no stage's ▶ (stage-review.cts draws them)
});
for (const file of ['task-api.ts', 'task-row.ts', 'pipeline-api.ts', 'task-pipelines.ts', 'overview-factories.ts', 'pipeline-editor.ts', 'task-requirements.ts']) {
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
  assert.match(html, /data-pv-settings="spec"[^>]*><span class="pv-stage-name">Spec<\/span><span class="pv-stage-n tnum" title="2 of 2 slots used." aria-label="2 of 2 slots used.">2\/2<\/span>/, 'a stage with a limit counts out of it');
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
  assert.match(html, new RegExp(`<span class="pv-stage-name">Spec</span><span class="pv-stage-n tnum" title="${words}" aria-label="${words}">5/20</span></button>`),
    'Spec 5/20, the rest said in its tooltip and name, and nothing after it');
  assert.doesNotMatch(html, /pv-stage-person/, 'no person pill');
  assert.equal((html.match(/class="tk-card"/g) || []).length, 20, 'every task is still drawn');
  const list = view.html(twenty, [...waiting, ...working], opts({layout: 'list'}));
  assert.match(list, new RegExp(`title="${words}"`));
  assert.doesNotMatch(list, /pv-stage-person/);
  const phone = view.html(twenty, [...waiting, ...working], opts({phone: true}));
  assert.match(phone, /data-pv-tab="spec"[^>]*>Spec <span class="tnum">5\/20<\/span>/, 'the phone tab counts bot work too');
  assert.doesNotMatch(phone, /pv-stage-person/);
  assert.match(phone, new RegExp(`</div><p class="pv-slots">${words}</p><div class="pv-phone">`), 'a phone says it in one line above the cards');
  const one = view.html(twenty, [waiting[0], ...working], opts());
  assert.match(one, /title="5 of 20 slots used\. 1 more waits on a person and holds no slot\."/);
  const none = view.html(twenty, working, opts({phone: true}));
  assert.doesNotMatch(none, /pv-slots/, 'none waits on a person: no line on the phone');
  assert.match(view.html(twenty, working, opts()), /title="5 of 20 slots used\." aria-label="5 of 20 slots used\.">5\/20</);
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

// ---- Company › Factories (read through factoriesRead, so this part waits)
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
  // The server's answer, read as factoriesRead reads it: its marks become the task mark's names.
  ctx.get = async () => map;
  const read = await ctx.factoriesRead('7d');
  assert.deepEqual(plain(read.pipelines.map((p: {mark: string | null}) => p.mark)), ['person', null, 'cant']);
  const groups = plain(ctx.ofGroups(read, recs));
  assert.deepEqual(groups.map((g: {name: string; done: number | null; pipelines: {id: string}[]}) => [g.name, g.done, g.pipelines.map(p => p.id)]),
    [['Product', 4, ['product', 'eng']], ['Other', null, ['general']]], 'a pipeline comes before the one it files into; retired ones go');
  const html = ctx.ofGroupHTML(groups[0]);
  assert.match(html, /<span class="of-done tnum">4 done<\/span>/);
  assert.match(html, /Product<\/span><span class="of-box-n tnum">17<\/span><span class="tk-mark tk-mark-person"[^>]*>!<\/span><\/a><span class="of-arrow"[^>]*>→<\/span><a class="of-box" href="#\/tasks\?pipeline=eng"/);
  const other = ctx.ofGroupHTML(groups[1]);
  assert.doesNotMatch(other, /done/, 'Other has no count');
  assert.match(other, /tk-mark-cant/);
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
// ---- the editor: the new kinds, their settings and On enter
{
  const state = {id: 'product', pipelines: [{id: 'product', name: 'Product'}, {id: 'engineering', name: 'Engineering'}],
    draft: rec({stages: [stage('spec', 'Spec', {requirements: [{...req('page'), who_oks: 'people_with_exceptions'}]}),
      stage('release', 'Release', {requirements: [{...req('item'), source: 'github', condition: 'in_release'}]})]})};
  const kinds = ctx.peReqHTML(state, state.draft.stages[0]!.requirements[0]!, 0, 0);
  assert.doesNotMatch(kinds, /later|disabled>/, 'every kind may be picked');
  assert.match(kinds, /<option value="in_release">In a release<\/option>/);
  assert.match(kinds, /<span>Jev checks<\/span>/);
  assert.match(kinds, /<span>Then OK from<\/span><select[^>]*><option value="">Nobody<\/option>/, 'a page needs no person');
  assert.match(kinds, /data-pe-rs="at_head"[^>]*> Newest commit only/);
  assert.match(kinds, /data-pe-rs="with_release"[^>]*> With its release/);
  const release = ctx.peReqHTML(state, state.draft.stages[1]!.requirements[0]!, 1, 0);
  assert.match(release, /data-pe-rf="release_repo"/);
  assert.match(release, /data-pe-rf="release_with"[^>]*placeholder="workflow:release.yml or tag"/);
  assert.match(release, /data-pe-rs="prereleases"[^>]* checked> Pre-releases count/, 'omission renders the enabled default');
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
  assert.doesNotMatch(ctx.peReqHTML({...state, draft: saved}, saved.stages[1]!.requirements[0]!, 1, 0),
    /data-pe-rs="prereleases"[^>]* checked/, 'the saved opt-out renders off');

  const onEnter = ctx.peOnEnterOptions(state, state.draft.stages[0]!);
  assert.match(onEnter, /<option value="" selected>Nothing<\/option><option value="file_into:engineering">File into Engineering<\/option><option value="act:github.queue">Queue PRs<\/option>/,
    'a stage files into another pipeline, not its own');
  ctx.peInput(state, field('HTMLSelectElement', {peSi: '0', peSf: 'on_enter'}, 'file_into:engineering'));
  assert.deepEqual(plain(state.draft.stages[0]!.on_enter), {file_into: 'engineering'});
  assert.match(ctx.peOnEnterOptions(state, state.draft.stages[0]!), /value="file_into:engineering" selected/);
  ctx.peInput(state, field('HTMLSelectElement', {peSi: '1', peSf: 'on_enter'}, 'act:github.queue'));
  assert.deepEqual(plain(state.draft.stages[1]!.on_enter), {act: 'github.queue'});
  ctx.peInput(state, field('HTMLSelectElement', {peSi: '1', peSf: 'on_enter'}, ''));
  assert.deepEqual(plain(state.draft.stages[1]!.on_enter), {});

  ctx.peInput(state, field('HTMLSelectElement', {peSi: '0', peRi: '0', peRf: 'kind'}, 'pr_merged'));
  const merged = plain(state.draft.stages[0]!.requirements[0]!) as Record<string, unknown>;
  assert.deepEqual([merged['kind'], merged['source'], merged['condition']], ['item', 'github', 'pr_merged'],
    'a GitHub condition is saved as an item of source github');
  assert.match(ctx.peReqHTML(state, state.draft.stages[0]!.requirements[0]!, 0, 0), /<option value="pr_merged" selected>/);
  ctx.peInput(state, field('HTMLSelectElement', {peSi: '0', peRi: '0', peRf: 'kind'}, 'owner_done'));
  const done = plain(state.draft.stages[0]!.requirements[0]!) as Record<string, unknown>;
  assert.deepEqual([done['kind'], done['source'], done['condition']], ['owner_done', null, null]);

  // A by-hand stage alone offers whether a person's request moves on by itself; leaving by hand off clears it.
  const ideas = stage('ideas', 'Ideas', {by_hand: true});
  state.draft.stages.unshift(ideas);
  (state as Record<string, unknown>)['open'] = new WeakSet();
  assert.match(ctx.peStageHTML(state, ideas, 0, 3), /data-pe-sf="requests_move_on"[^>]*> A person's request moves on/);
  assert.doesNotMatch(ctx.peStageHTML(state, state.draft.stages[1]!, 1, 3), /requests_move_on/);
  ctx.peInput(state, Object.assign(field('HTMLInputElement', {peSi: '0', peSf: 'requests_move_on'}, 'on', true), {type: 'checkbox'}));
  assert.equal(state.draft.stages[0]!['requests_move_on'], true);
  ctx.peClick(state, {closest: () => ({dataset: {peSi: '0', peSeg: 'by_hand', v: '0'}, hasAttribute: (at: string) => at === 'data-pe-seg'})});
  assert.equal(state.draft.stages[0]!['requests_move_on'], false, 'only a by-hand stage keeps it');
}
// ---- the editor: a stage's answer
{
  const state = {id: 'product', pipelines: [], reqOpen: new WeakSet(), draft: rec({stages: [stage('spec', 'Spec', {requirements: [req('owner_done')]})]})};
  const field = (kind: 'HTMLInputElement' | 'HTMLSelectElement', data: Record<string, string>, value = '') =>
    Object.assign(Object.create(ctx[kind].prototype), {dataset: {peSi: '0', peRi: '0', ...data}, value, type: 'text', closest: () => null});
  const r = () => plain(state.draft.stages[0]!.requirements[0]!) as Record<string, unknown>;
  const html = () => ctx.peReqHTML(state, state.draft.stages[0]!.requirements[0]!, 0, 0) as string;
  assert.match(html(), /<optgroup label="Responses"><option value="owner_done" selected>Owner says done<\/option><option value="ok">OK from someone<\/option><option value="answer">Answer<\/option><\/optgroup><optgroup label="Links"><option value="linked_done">/);
  ctx.peInput(state, field('HTMLInputElement', {peRf: 'line'}, 'Confidence'));
  ctx.peInput(state, field('HTMLSelectElement', {peRf: 'kind'}, 'answer'));
  assert.deepEqual([r()['kind'], r()['settings'], r()['who_oks'], r()['choices']], ['answer', {type: 'text', name: 'confidence'}, 'none', null]);
  assert.match(html(), /<span>Title<\/span>/);
  assert.match(html(), /<span>From<\/span><select data-pe-rf="person_id"[^>]*><option value="">Task owner<\/option>/);
  assert.match(html(), /data-pe-rf="answer_name"[^>]*value="confidence"/);
  ctx.peInput(state, field('HTMLInputElement', {peRf: 'line'}, 'Confidence (0-100)'));
  assert.equal((r()['settings'] as Record<string, unknown>)['name'], 'confidence_0_100', 'the name follows the title');
  ctx.peInput(state, field('HTMLInputElement', {peRf: 'answer_name'}, 'conf'));
  ctx.peInput(state, field('HTMLInputElement', {peRf: 'line'}, 'Confidence'));
  assert.equal((r()['settings'] as Record<string, unknown>)['name'], 'conf', 'a name edited by hand stays');
  ctx.peInput(state, field('HTMLSelectElement', {peRf: 'answer_type'}, 'number'));
  ctx.peInput(state, field('HTMLInputElement', {peRf: 'min'}, '0'));
  ctx.peInput(state, field('HTMLInputElement', {peRf: 'max'}, '100'));
  ctx.peInput(state, field('HTMLInputElement', {peRf: 'question'}, 'How sure are you?'));
  assert.deepEqual(r()['settings'], {type: 'number', name: 'conf', min: 0, max: 100, question: 'How sure are you?'});
  assert.match(html(), /<span>Min<\/span><input type="number"[^>]*value="0">/);
  ctx.peInput(state, field('HTMLSelectElement', {peRf: 'answer_type'}, 'choice'));
  assert.deepEqual([r()['settings'], r()['choices']], [{type: 'choice', name: 'conf', question: 'How sure are you?'}, []], 'a choice has no bounds');
  assert.match(html(), /data-pe-yes-no/);
  ctx.peInput(state, field('HTMLSelectElement', {peRf: 'answer_type'}, 'text'));
  assert.equal(r()['choices'], null, 'text has no choices');
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
  const state = {id: 'product', pipelines: [], open: new WeakSet(), reqOpen: new WeakSet(), base, draft: JSON.parse(JSON.stringify(base))};
  const html = (si: number) => ctx.peStageHTML(state, state.draft.stages[si]!, si, 3) as string;

  // Three groups in the order a task moves, with every field the window had before (AC-11).
  const body = html(1);
  const groups = [...body.matchAll(/<h4 class="pe-h">([^<]+)<\/h4>/g)].map(m => m[1]);
  assert.deepEqual(groups, ['When a task arrives', 'Who works on it', 'To leave']);
  const at = (needle: string) => { const i = body.indexOf(needle); assert.ok(i >= 0, needle); return i; };
  const [arrives, works, leave] = groups.map(g => at(`>${g}<`)) as [number, number, number];
  for (const f of ['data-pe-sf="on_enter"', 'data-pe-sf="task_limit"']) assert.ok(at(f) > arrives && at(f) < works, `${f} is under When a task arrives`);
  for (const f of ['data-pe-sf="owner"', 'data-pe-sf="expected_n"']) assert.ok(at(f) > works && at(f) < leave, `${f} is under Who works on it`);
  for (const f of ['data-pe-seg="by_hand"', 'data-pe-req-add']) assert.ok(at(f) > leave, `${f} is under To leave`);
  for (const f of ['data-pe-sf="name"', 'data-pe-sf="purpose"', 'data-pe-sf="expected_unit"']) at(f);
  // Tasks leave is a two-way switch now, not a list.
  assert.match(body, /role="radiogroup" aria-label="Tasks leave"[^>]*><button type="button" role="radio" data-v="0" aria-checked="true"[^>]*>When requirements are met<\/button><button type="button" role="radio" data-v="1" aria-checked="false"[^>]*>By hand<\/button>/);
  assert.doesNotMatch(body, /<select data-pe-sf="by_hand"/);

  // One of several: the bots with their model family, Pick, Limit each, Prefer, the owner switch and the sentence.
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
  assert.match(body, /<span class="pe-stage-owner pe-stage-pool" title="b1, b2"><span>One of 2<\/span><\/span>/);

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
  ctx.peClick(state, button({peSeg: 'by_hand', v: '1'}));
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
// ---- a task's row for a stage's answer
{
  const typed = {name: 'confidence', type: 'number', min: 0, max: 100, question: 'How sure are you?'};
  const row = (extra: Record<string, unknown>) => ctx.taskReqRowHTML({requirement_id: 'r1', kind: 'answer', line: 'Confidence', met: false,
    standing: 'How sure are you?', who: 'bot:builder', asked_by: null, choices: null, typed, shows: [], answer: null, may: [], ...extra}) as string;
  const open = row({});
  assert.match(open, /<span class="task-req-name">Confidence<\/span>/, 'the title only');
  assert.doesNotMatch(open, /How sure|task-req-val/);
  const met = row({met: true, who: null, standing: '80', answer: {id: 'a1', at: '', by: 'bot:builder', note: null, value: '80'}});
  assert.match(met, /<span class="task-req-name">Confidence<\/span>/);
  assert.match(met, /<span class="task-req-val">80<\/span>/, 'once met, its value');
}
factories.then(() => console.log('pipeline-view-unit: ok'), error => { console.error(error); process.exitCode = 1; });
