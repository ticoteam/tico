import type {Route} from 'playwright';
// The pipeline editor (ui/app/pipeline-editor.ts), opened from the pencil by the pipeline's name on the Tasks page.
//  - the pencil shows only where GET /api/v2/pipelines/{id}/rights says the reader may edit;
//  - settings: goal, judge, factory, private (with who may see it), prefix, owner and manager save through
//    PATCH /v2/pipelines/{id}/settings, read back from GET /v2/pipelines/{id}/settings;
//  - a stage: rename, move, add and remove save the whole list through the task-type route; Warn after saves minutes
//    (hours and days converted) or "Don't warn" (null);
//  - a requirement: + Require saves the kind's own shape, a person reviewer is needed, an edit sends PUT, Retire sends DELETE;
//  - Then adds and removes a link; a phone opens the editor as a bottom sheet with no sideways scroll.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {html, isUiPath, serveUi} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

type Write = {method: string; p: string; body: unknown};
type Req = {id: string; name: string; kind: string; needs: unknown; reviewed_by: string; reviewer_id: string | null; backup_reviewer_id?: string | null};
type State = {type: {id: string; name: string; prefix: string; pipeline_owner_id: string; manager: string | null;
    steps: {id: string; name: string; status: string; limit_minutes: number | null}[]};
  reqs: Record<string, Req[]>; links: {id: string; [k: string]: unknown}[]; stageReads: number;
  settings: {id: string; name: string; prefix: string; goal: string | null; judge_model: string | null; visibility: string; factory_id: string | null;
    owner_id: string | null; manager_id: string | null; members: string[]; judge_models: string[]; default_judge_model: string};
  factories: {id: string; name: string; goal: null; owner_id: null; manager_id: null; position: number}[]};

// The server refuses Nobody with a reviewer or a backup (backend/pipeline_requirements.py): the fixture answers the same 422.
const refused = (b: {reviewed_by?: string; reviewer_id?: string | null; backup_reviewer_id?: string | null}) => b.reviewed_by === 'nobody' && !!(b.reviewer_id || b.backup_reviewer_id);
const REFUSED = {error: {code: 'validation', detail: 'Nobody reviews this requirement, so it names no reviewer'}};

const steps = () => [
  {id: 's1', name: 'Todo', status: 'open', limit_minutes: null},
  {id: 's2', name: 'Doing', status: 'doing', limit_minutes: 120},
  {id: 's3', name: 'Done', status: 'done', limit_minutes: null},
];

async function open(browser: import('playwright').Browser, {viewport = {width: 1280, height: 820}, may = true, mover = true} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 600, isMobile: viewport.width < 600});
  const errors: unknown[] = [], writes: Write[] = [];
  const state: State = {type: {id: 'dev', name: 'Dev tickets', prefix: 'D', pipeline_owner_id: 'human:ana', manager: null, steps: steps()},
    reqs: {s2: [{id: 'r1', name: 'Screenshot', kind: 'attachment', needs: {formats: ['image'], newer_than_change: true, is_work: false}, reviewed_by: 'nobody', reviewer_id: null}]},
    links: [], stageReads: 0,
    settings: {id: 'dev', name: 'Dev tickets', prefix: 'D', goal: 'Ship tickets fast', judge_model: null, visibility: 'company', factory_id: 'f1', owner_id: 'human:ana', manager_id: null, members: [], judge_models: ['haiku-5.5', 'jev', 'sonnet'], default_judge_model: 'haiku-5.5'},
    factories: [{id: 'f1', name: 'Product', goal: null, owner_id: null, manager_id: null, position: 0}, {id: 'f2', name: 'Growth', goal: null, owner_id: null, manager_id: null, position: 1}]};
  page.on('pageerror', (e: Error) => errors.push(e.message));
  await page.addInitScript(() => { try { localStorage.setItem('tico.theme', 'dark'); } catch {} });
  const me = {id: 'ana', role: 'owner', name: 'Ana Ruiz', email: 'ana@example.com', cloud: true, registered: true, owner_id: 'ana', mover, config: {company_name: 'Acme Bikes'}};
  await page.route('**/*', async (route: Route) => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (isUiPath(p)) return serveUi(route, p);
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    const body = method === 'GET' ? null : req.postDataJSON();
    if (method !== 'GET') writes.push({method, p, body});
    if (p === '/api/me') return json(me);
    if (p === '/api/employees') return json([{name: 'lead', display_name: 'Lead Manager', status: 'active', state: 'active', users: [], bot_owners: [], my_access: {see: true, read: true, write: true}}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Ruiz', role: 'owner'}, {id: 'sam', name: 'Sam Okafor', role: 'member'}], teams: {}, org_groups: []});
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/issues') return json([]);
    if (p === '/api/v2/needs-you') return json({items: [], count: 0, plate_count: 0});
    if (p === '/api/v2/tasks' && method === 'GET') return json({tasks: [], next: null, labels: [], tags: []});
    if (p === '/api/v2/tasks/labels') return json({labels: [], tags: []});
    if (p === '/api/v2/tags') return json({tags: []});
    if (p === '/api/v2/pipelines/dev/rights') return json({may_edit_rules: may});
    if (p === '/api/v2/task-types' && method === 'GET') return json({types: [state.type, {id: 'ops', name: 'Ops', steps: [{id: 'o1', name: 'Todo', status: 'open'}]}]});
    if (p === '/api/v2/task-types/dev' && method === 'GET') return json({type: state.type});
    if (p === '/api/v2/task-types/dev') {
      state.type.steps = body.steps.map((s: {id: string}, i: number) => ({limit_minutes: null, ...state.type.steps.find(x => x.id === s.id), ...s, id: s.id || 'n' + i}));
      return json({type: state.type});
    }
    if (p === '/api/v2/pipeline-links' && method === 'GET') return json({links: state.links});
    if (p === '/api/v2/pipeline-links') { state.links = [{id: 'l1', from_pipeline_id: 'dev', from_pipeline_stage_id: body.from_stage_id, to_pipeline_id: body.to_pipeline_id, mode: body.mode} as {id: string}]; return json({link: state.links[0]}); }
    if (p === '/api/v2/pipeline-links/l1/retire') { state.links = []; return json({link: {}}); }
    // The affected count (GET /api/v2/pipeline-stages/{id}/affected): four open cards at Doing, one with the label "urgent".
    const affected = p.match(/^\/api\/v2\/pipeline-stages\/(\w+)\/affected$/);
    if (affected && method === 'GET') {
      const label = url.searchParams.get('label') || '';
      return json({stage: state.type.steps.find(s => s.id === affected[1])?.name ?? '', label: label || null, count: label === 'urgent' ? 1 : 4});
    }
    const stageReqs = p.match(/^\/api\/v2\/pipeline-stages\/(\w+)\/requirements$/);
    if (stageReqs && method === 'GET') { state.stageReads++; return json({requirements: state.reqs[stageReqs[1]!] || []}); }
    if (stageReqs && refused(body)) return json(REFUSED, 422);
    if (stageReqs) { (state.reqs[stageReqs[1]!] ||= []).push({id: 'r2', ...body}); return json({requirement: {id: 'r2'}}); }
    if (p === '/api/v2/requirements/r1' && method === 'PUT') {
      if (refused(body)) return json(REFUSED, 422);
      state.reqs.s2![0] = {id: 'r1', ...body};
      return json({requirement: {}});
    }
    if (p === '/api/v2/requirements/r1' && method === 'DELETE') { state.reqs.s2 = []; return json({requirement: {}}); }
    if (p === '/api/v2/pipelines/dev/settings' && method === 'GET') return json(state.settings);
    if (p === '/api/v2/pipelines/dev/settings') {
      const s = state.settings;
      if ('prefix' in body) { s.prefix = body.prefix; state.type.prefix = body.prefix; }
      if ('owner_id' in body) { s.owner_id = body.owner_id; state.type.pipeline_owner_id = body.owner_id; }
      if ('manager_id' in body) { s.manager_id = body.manager_id; state.type.manager = body.manager_id; }
      if ('goal' in body) s.goal = body.goal || null;
      if ('judge_model' in body) s.judge_model = body.judge_model || null;
      if (body.visibility) { s.visibility = body.visibility; if (body.visibility === 'members') s.factory_id = null; }
      if ('factory_id' in body) s.factory_id = body.factory_id || null;
      if (body.members) s.members = body.members;
      return json(s);
    }
    if (p === '/api/v2/factories') return json({factories: state.factories});
    const limit = p.match(/^\/api\/v2\/pipeline-stages\/(\w+)$/);
    if (limit) { state.type.steps.find(s => s.id === limit[1])!.limit_minutes = body.limit_minutes; return json({}); }
    if (p.startsWith('/api/v2/preferences/')) return json({key: 'tasks.view', value: null});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/tasks?type=dev&view=list');
  await page.locator('#task-scope').waitFor();
  return {page, errors, writes, state};
}
// The editor reads again after a save and redraws; wait for that before typing into it.
const settled = async (page: import('playwright').Page) => { await page.waitForTimeout(100); await page.waitForLoadState('networkidle'); };
const last = (writes: Write[], p: string): unknown => writes.filter(w => w.p === p).at(-1)?.body;

async function desktop(browser: import('playwright').Browser) {
  const {page, errors, writes} = await open(browser);
  const pencil = page.locator('#task-pipeline-edit');
  await page.waitForFunction(() => !document.getElementById('task-pipeline-edit')!.hidden);
  await pencil.click();
  await page.locator('#pe-dialog .pe-stage').first().waitFor();
  assert.equal(await page.locator('#pe-title').innerText(), 'Dev tickets');
  assert.equal(await page.locator('.pe-stage').count(), 3);
  assert.match(await page.locator('.pe-stage').nth(1).innerText(), /Warn after: 2 hours/);
  assert.match(await page.locator('.pe-req').first().innerText(), /Screenshot/);
  // Settings.
  await page.locator('[data-pe-setting=prefix]').fill('dv');
  await page.locator('[data-pe-setting=prefix]').blur();
  await page.waitForFunction(() => document.querySelector('[data-pe-setting=prefix]'));
  assert.deepEqual(last(writes, '/api/v2/pipelines/dev/settings'), {prefix: 'dv'});
  await page.locator('[data-pe-setting=manager_id]').selectOption('bot:lead');
  await page.waitForFunction(() => (document.querySelector('[data-pe-setting=manager_id]')! as HTMLInputElement).value === 'bot:lead');
  assert.deepEqual(last(writes, '/api/v2/pipelines/dev/settings'), {manager_id: 'bot:lead'});
  // Goal saves on blur; Judge shows the default when none is set, then saves its choice; Factory picks a live one;
  // Private sends the visibility, then who may see it, and a private pipeline has no factory.
  const nextSettings = () => page.waitForRequest(r => r.method() === 'PATCH' && new URL(r.url()).pathname === '/api/v2/pipelines/dev/settings');
  assert.equal(await page.locator('[data-pe-setting=judge_model]').inputValue(), 'haiku-5.5');
  let saving = nextSettings();
  await page.locator('[data-pe-setting=goal]').fill('Launch the spring line');
  await page.locator('[data-pe-setting=goal]').blur();
  assert.deepEqual((await saving).postDataJSON(), {goal: 'Launch the spring line'});
  await page.waitForFunction(() => (document.querySelector('[data-pe-setting=goal]')! as HTMLInputElement).value === 'Launch the spring line');
  saving = nextSettings();
  await page.locator('[data-pe-setting=judge_model]').selectOption('jev');
  assert.deepEqual((await saving).postDataJSON(), {judge_model: 'jev'});
  saving = nextSettings();
  await page.locator('[data-pe-setting=factory_id]').selectOption('f2');
  assert.deepEqual((await saving).postDataJSON(), {factory_id: 'f2'});
  saving = nextSettings();
  await page.locator('[data-pe-private]').check();
  assert.deepEqual((await saving).postDataJSON(), {visibility: 'members'});
  await page.waitForFunction(() => (document.querySelector('[data-pe-setting=factory_id]')! as HTMLInputElement).disabled);
  saving = nextSettings();
  await page.locator('[data-pe-member][value="human:sam"]').check();
  assert.deepEqual((await saving).postDataJSON(), {members: ['human:sam']});
  saving = nextSettings();
  await page.locator('[data-pe-private]').uncheck();
  assert.deepEqual((await saving).postDataJSON(), {visibility: 'company'});
  await page.waitForFunction(() => !(document.querySelector('[data-pe-setting=factory_id]')! as HTMLInputElement).disabled);
  // Warn after: days convert to minutes; Don't warn sends null.
  await page.locator('[data-pe-warn=s1]').click();
  await page.locator('[data-pe-warn-n]').fill('2');
  await page.locator('[data-pe-warn-u]').selectOption({label: 'days'});
  await page.locator('[data-pe-warn-save=s1]').click();
  await page.waitForFunction(() => /2 days/.test((document.querySelector('.pe-stage')! as HTMLElement).innerText));
  assert.deepEqual(last(writes, '/api/v2/pipeline-stages/s1'), {limit_minutes: 2880});
  await page.locator('[data-pe-warn=s1]').click();
  await page.locator('[data-pe-warn-off=s1]').click();
  await page.waitForFunction(() => /Warn after: Off/.test((document.querySelector('.pe-stage')! as HTMLElement).innerText));
  assert.deepEqual(last(writes, '/api/v2/pipeline-stages/s1'), {limit_minutes: null});
  // A requirement: an approval needs a person; the kind's own shape is sent.
  await page.locator('[data-pe-new=s2]').click();
  await page.locator('.pe-form [name=name]').fill('Lead sign-off');
  await page.locator('.pe-form [name=kind]').selectOption('approval');
  assert.equal(await page.locator('.pe-form [name=reviewed_by]').inputValue(), 'person');
  await page.locator('.pe-form [name=reviewer_id]').selectOption('human:sam');
  await page.locator('.pe-form button[type=submit]').click();
  await page.waitForFunction(() => document.body.innerText.includes('Lead sign-off'));
  const added = last(writes, '/api/v2/pipeline-stages/s2/requirements') as Req & {kind: string; needs: unknown};
  assert.equal(added.kind, 'approval'); assert.equal(added.reviewed_by, 'person'); assert.equal(added.reviewer_id, 'human:sam'); assert.deepEqual(added.needs, {});
  await page.locator('[data-pe-req=r1]').click();
  await page.locator('.pe-form [name=name]').fill('Screenshot of the fix');
  await page.locator('.pe-form button[type=submit]').click();
  await page.waitForFunction(() => document.body.innerText.includes('Screenshot of the fix'));
  assert.equal((writes.filter(w => w.method === 'PUT').at(-1)!.body as {name: string}).name, 'Screenshot of the fix');
  await page.locator('[data-pe-req=r1]').click();
  await page.locator('[data-pe-retire=r1]').click();
  await page.waitForFunction(() => !document.body.innerText.includes('Screenshot of the fix'));
  assert.equal(writes.filter(w => w.method === 'DELETE').length, 1);
  // Stages: rename, add (before Done), move, remove.
  await page.locator('[data-pe-rename=s1]').fill('Backlog');
  await page.locator('[data-pe-rename=s1]').blur();
  await page.waitForFunction(() => (document.querySelector('[data-pe-rename=s1]')! as HTMLInputElement).value === 'Backlog');
  assert.equal((last(writes, '/api/v2/task-types/dev') as {steps: {name: string}[]}).steps[0]!.name, 'Backlog');
  await settled(page);
  await page.locator('[data-pe-add-stage] input').fill('Review');
  await page.locator('[data-pe-add-stage] button').click();
  await page.waitForFunction(() => document.querySelectorAll('.pe-stage').length === 4);
  assert.deepEqual((last(writes, '/api/v2/task-types/dev') as {steps: {name: string}[]}).steps.map(s => s.name), ['Backlog', 'Doing', 'Review', 'Done']);
  await page.locator('[data-pe-up=s3]').click();
  await page.waitForFunction(() => (document.querySelectorAll('.pe-stage-name')[2]! as HTMLInputElement).value === 'Done');
  await page.locator('[data-pe-drop="n2"]').click();
  await page.waitForFunction(() => document.querySelectorAll('.pe-stage').length === 3);
  // Then: add a link, then remove it.
  await page.locator('[data-pe-link=s2]').click();
  await page.locator('[data-pe-link-to]').selectOption('ops');
  await page.locator('[data-pe-link-save=s2]').click();
  await page.waitForFunction(() => document.body.innerText.includes('Then Ops'));
  assert.deepEqual(last(writes, '/api/v2/pipeline-links'), {from_stage_id: 's2', to_pipeline_id: 'ops', mode: 'move'});
  await page.locator('[data-pe-unlink=l1]').click();
  await page.waitForFunction(() => !document.body.innerText.includes('Then Ops'));
  await page.locator('[data-pe-close]').click();
  await page.waitForFunction(() => !document.getElementById('pe-dialog'));
  assert.deepEqual(errors, []);
  await page.close();
}

async function hiddenAndRestricted(browser: import('playwright').Browser) {
  const a = await open(browser, {may: false});
  await a.page.waitForTimeout(400);
  assert.equal(await a.page.locator('#task-pipeline-edit').isHidden(), true);
  await a.page.close();
  // A reader who is not a mover edits requirements and settings, but not the stages themselves.
  const b = await open(browser, {mover: false});
  await b.page.waitForFunction(() => !document.getElementById('task-pipeline-edit')!.hidden);
  await b.page.locator('#task-pipeline-edit').click();
  await b.page.locator('.pe-stage').first().waitFor();
  assert.equal(await b.page.locator('[data-pe-add-stage], [data-pe-up], [data-pe-rename]').count(), 0);
  assert.equal(await b.page.locator('[data-pe-new]').count(), 3);
  await b.page.close();
}

// FHI10: switching a requirement to Nobody saves. The reviewer it named goes with the switch (the server refuses the pair).
async function nobodySaves(browser: import('playwright').Browser) {
  const {page, errors, writes, state} = await open(browser);
  state.reqs.s2 = [{id: 'r1', name: 'Screenshot', kind: 'attachment', needs: {formats: ['image'], newer_than_change: true, is_work: false},
    reviewed_by: 'person', reviewer_id: 'human:sam', backup_reviewer_id: null}];
  await page.waitForFunction(() => !document.getElementById('task-pipeline-edit')!.hidden);
  await page.locator('#task-pipeline-edit').click();
  await page.locator('[data-pe-req=r1]').click();
  await page.locator('.pe-form [name=reviewed_by]').selectOption('nobody');
  await page.locator('.pe-form button[type=submit]').click();
  await settled(page);
  const put = writes.filter(w => w.p === '/api/v2/requirements/r1' && w.method === 'PUT').at(-1)?.body as Req;
  assert.equal(put.reviewed_by, 'nobody');
  assert.equal(put.reviewer_id, null, 'Nobody sends no reviewer');
  assert.equal(put.backup_reviewer_id, null, 'Nobody sends no backup');
  assert.equal(state.reqs.s2![0]!.reviewed_by, 'nobody', 'the requirement saved as Nobody');
  assert.equal(await page.locator('.pe-form [data-pe-error]:not([hidden])').count(), 0, 'no refusal is shown');
  assert.deepEqual(errors, [], 'nobody: no page errors');
  await page.close();
}

// FHI11: an owner cannot be cleared, so there is no empty choice; an unset manager shows a disabled "Not set".
async function unsetChoices(browser: import('playwright').Browser) {
  const {page, errors} = await open(browser);
  await page.waitForFunction(() => !document.getElementById('task-pipeline-edit')!.hidden);
  await page.locator('#task-pipeline-edit').click();
  await page.locator('.pe-stage').first().waitFor();
  const enabled = (sel: string) => page.locator(`${sel} option:not([disabled])`).evaluateAll((os: HTMLElement[]) => os.map(o => (o as HTMLOptionElement).value));
  assert.deepEqual(await enabled('[data-pe-setting=owner_id]'), ['human:ana', 'human:sam'], 'the owner has no empty choice');
  assert.deepEqual(await enabled('[data-pe-setting=manager_id]'), ['bot:lead'], 'an unset manager has no empty choice');
  assert.equal(await page.locator('[data-pe-setting=manager_id] option').first().innerText(), 'Not set', 'an unset manager says so');
  assert.equal(await page.locator('[data-pe-setting=manager_id] option').first().evaluate(o => (o as HTMLOptionElement).disabled), true, 'the placeholder cannot be chosen');
  assert.deepEqual(errors, [], 'unset choices: no page errors');
  await page.close();
}

// FHI16: a new requirement says how many open cards it would apply to right now, and reads again when the label changes.
async function affectedNote(browser: import('playwright').Browser) {
  const {page, errors} = await open(browser);
  await page.waitForFunction(() => !document.getElementById('task-pipeline-edit')!.hidden);
  await page.locator('#task-pipeline-edit').click();
  await page.locator('.pe-stage').first().waitFor();
  await page.locator('[data-pe-new=s2]').click();
  await page.waitForSelector('.pe-form [data-pe-affected]:not([hidden])', {timeout: 5000});
  assert.equal((await page.locator('.pe-form [data-pe-affected]').innerText()).trim(), 'Applies to the 4 cards in Doing now.');
  await page.locator('.pe-form [name=applies_to_label]').fill('urgent');
  await page.locator('.pe-form [name=applies_to_label]').press('Tab');
  await page.waitForFunction(() => document.querySelector('.pe-form [data-pe-affected]')?.textContent.includes('1 card'), null, {timeout: 5000});
  assert.equal((await page.locator('.pe-form [data-pe-affected]').innerText()).trim(), 'Applies to the 1 card in Doing now.', 'the label narrows it');
  assert.deepEqual(errors, [], 'affected: no page errors');
  await page.close();
}

// J2: saving members keeps a member the roster does not list (its box is drawn checked), and adds the one ticked.
async function keepsMembers(browser: import('playwright').Browser) {
  const {page, errors, writes, state} = await open(browser);
  state.settings.visibility = 'members';
  state.settings.members = ['human:ana', 'bot:ghost'];
  await page.waitForFunction(() => !document.getElementById('task-pipeline-edit')!.hidden);
  await page.locator('#task-pipeline-edit').click();
  await page.locator('.pe-stage').first().waitFor();
  assert.equal(await page.locator('[data-pe-member][value="bot:ghost"]').isChecked(), true, 'an unlisted member keeps a checked box');
  await page.locator('[data-pe-member][value="human:sam"]').check();
  await settled(page);
  const members = [...(last(writes, '/api/v2/pipelines/dev/settings') as {members: string[]}).members].sort();
  assert.deepEqual(members, ['bot:ghost', 'human:ana', 'human:sam'], 'adding Sam keeps the unlisted member');
  assert.deepEqual(errors, [], 'members: no page errors');
  await page.close();
}

// A redraw while a setting is being typed in (another save reloads the editor) keeps the typed words and the focus, and the
// words still save when the field is left.
async function typingSurvivesRedraw(browser: import('playwright').Browser) {
  const {page, errors, writes, state} = await open(browser);
  await page.waitForFunction(() => !document.getElementById('task-pipeline-edit')!.hidden);
  await page.locator('#task-pipeline-edit').click();
  await page.locator('.pe-stage').first().waitFor();
  await page.locator('[data-pe-setting=goal]').fill('Ship faster');
  const reads = state.stageReads;
  // The judge saves on change: its save reloads the editor (every stage's requirements are read again) while the Goal has focus.
  await page.evaluate(() => document.querySelector('[data-pe-setting=judge_model]')!.dispatchEvent(new Event('change', {bubbles: true})));
  const end = Date.now() + 5000;
  while (state.stageReads < reads + 3) { if (Date.now() > end) throw new Error('the editor was not redrawn'); await new Promise(r => setTimeout(r, 25)); }
  await settled(page);
  assert.equal(await page.locator('[data-pe-setting=goal]').inputValue(), 'Ship faster', 'the typed Goal survives the redraw');
  assert.equal(await page.evaluate(() => (document.activeElement as HTMLElement | null)?.dataset.peSetting), 'goal', 'the Goal keeps the focus');
  await page.locator('[data-pe-setting=goal]').blur();
  await settled(page);
  assert.equal((last(writes, '/api/v2/pipelines/dev/settings') as {goal: string}).goal, 'Ship faster', 'leaving the Goal saves the typed words');
  assert.deepEqual(errors, [], 'redraw: no page errors');
  await page.close();
}

// J5: Enter in the Goal saves it (the PATCH is sent), as a change or blur does.
async function goalEnter(browser: import('playwright').Browser) {
  const {page, errors, writes, state} = await open(browser);
  await page.waitForFunction(() => !document.getElementById('task-pipeline-edit')!.hidden);
  await page.locator('#task-pipeline-edit').click();
  await page.locator('.pe-stage').first().waitFor();
  await page.locator('[data-pe-setting=goal]').fill('Ship faster');
  await page.locator('[data-pe-setting=goal]').press('Enter');
  await settled(page);
  assert.equal((last(writes, '/api/v2/pipelines/dev/settings') as {goal: string}).goal, 'Ship faster', 'Enter sends the goal');
  assert.equal(state.settings.goal, 'Ship faster', 'the goal is saved');
  assert.deepEqual(errors, [], 'goal: no page errors');
  await page.close();
}

async function phone(browser: import('playwright').Browser) {
  const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}});
  await page.waitForFunction(() => !document.getElementById('task-pipeline-edit')!.hidden);
  await page.locator('#task-pipeline-edit').click();
  await page.locator('.pe-stage').first().waitFor();
  const box = await page.locator('#pe-dialog').boundingBox();
  assert.ok(box!.width >= 389 && box!.y + box!.height <= 845, `sheet ${JSON.stringify(box)}`);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways scroll');
  assert.deepEqual(errors, []);
  await page.close();
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined) as string});
  try {
    await desktop(browser); await hiddenAndRestricted(browser); await phone(browser);
    await nobodySaves(browser); await unsetChoices(browser); await affectedNote(browser); await keepsMembers(browser); await goalEnter(browser);
    await typingSurvivesRedraw(browser);
    console.log('pipeline-editor: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
