// The pipeline editor (ui/app/pipeline-editor.ts), opened from the pencil by the pipeline's name on the Tasks page.
//  - the pencil shows only where GET /api/v2/pipelines/{id}/rights says the reader may edit;
//  - settings: prefix, owner and manager save through PATCH /v2/pipelines/{id}/settings;
//  - a stage: rename, move, add and remove save the whole list through the task-type route; Warn after saves minutes
//    (hours and days converted) or "Don't warn" (null);
//  - a requirement: + Require saves the kind's own shape, a person reviewer is needed, an edit sends PUT, Retire sends DELETE;
//  - Then adds and removes a link; a phone opens the editor as a bottom sheet with no sideways scroll.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const steps = () => [
  {id: 's1', name: 'Todo', status: 'open', limit_minutes: null},
  {id: 's2', name: 'Doing', status: 'doing', limit_minutes: 120},
  {id: 's3', name: 'Done', status: 'done', limit_minutes: null},
];

async function open(browser, {viewport = {width: 1280, height: 820}, may = true, mover = true} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 600, isMobile: viewport.width < 600});
  const errors = [], writes = [];
  const state = {type: {id: 'dev', name: 'Dev tickets', prefix: 'D', pipeline_owner_id: 'human:ana', manager: null, steps: steps()},
    reqs: {s2: [{id: 'r1', name: 'Screenshot', kind: 'attachment', needs: {formats: ['image'], newer_than_change: true, is_work: false}, reviewed_by: 'nobody', reviewer_id: null}]},
    links: []};
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(() => { try { localStorage.setItem('tico.theme', 'dark'); } catch {} });
  const me = {id: 'ana', role: 'owner', name: 'Ana Ruiz', email: 'ana@example.com', cloud: true, registered: true, owner_id: 'ana', mover, config: {company_name: 'Acme Bikes'}};
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p.startsWith('/tico/ui/')) {
      const name = p.slice('/tico/ui/'.length), file = uiFile(name);
      if (fs.existsSync(file)) return route.fulfill({body: fs.readFileSync(file), contentType: name.endsWith('.css') ? 'text/css' : name.endsWith('.js') ? 'application/javascript' : 'application/octet-stream'});
    }
    if (p.startsWith('/vendor/fonts/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile(p.slice(1)))});
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
      state.type.steps = body.steps.map((s, i) => ({limit_minutes: null, ...state.type.steps.find(x => x.id === s.id), ...s, id: s.id || 'n' + i}));
      return json({type: state.type});
    }
    if (p === '/api/v2/pipeline-links' && method === 'GET') return json({links: state.links});
    if (p === '/api/v2/pipeline-links') { state.links = [{id: 'l1', from_pipeline_id: 'dev', from_pipeline_stage_id: body.from_stage_id, to_pipeline_id: body.to_pipeline_id, mode: body.mode}]; return json({link: state.links[0]}); }
    if (p === '/api/v2/pipeline-links/l1/retire') { state.links = []; return json({link: {}}); }
    const stageReqs = p.match(/^\/api\/v2\/pipeline-stages\/(\w+)\/requirements$/);
    if (stageReqs && method === 'GET') return json({requirements: state.reqs[stageReqs[1]] || []});
    if (stageReqs) { (state.reqs[stageReqs[1]] ||= []).push({id: 'r2', ...body}); return json({requirement: {id: 'r2'}}); }
    if (p === '/api/v2/requirements/r1' && method === 'PUT') { state.reqs.s2[0] = {id: 'r1', ...body}; return json({requirement: {}}); }
    if (p === '/api/v2/requirements/r1' && method === 'DELETE') { state.reqs.s2 = []; return json({requirement: {}}); }
    if (p === '/api/v2/pipelines/dev/settings') { Object.assign(state.type, {prefix: body.prefix ?? state.type.prefix, pipeline_owner_id: body.owner_id ?? state.type.pipeline_owner_id, manager: body.manager_id ?? state.type.manager}); return json({}); }
    const limit = p.match(/^\/api\/v2\/pipeline-stages\/(\w+)$/);
    if (limit) { state.type.steps.find(s => s.id === limit[1]).limit_minutes = body.limit_minutes; return json({}); }
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
const settled = async page => { await page.waitForTimeout(100); await page.waitForLoadState('networkidle'); };
const last = (writes, p) => writes.filter(w => w.p === p).at(-1)?.body;

async function desktop(browser) {
  const {page, errors, writes} = await open(browser);
  const pencil = page.locator('#task-pipeline-edit');
  await page.waitForFunction(() => !document.getElementById('task-pipeline-edit').hidden);
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
  await page.waitForFunction(() => document.querySelector('[data-pe-setting=manager_id]').value === 'bot:lead');
  assert.deepEqual(last(writes, '/api/v2/pipelines/dev/settings'), {manager_id: 'bot:lead'});
  // Warn after: days convert to minutes; Don't warn sends null.
  await page.locator('[data-pe-warn=s1]').click();
  await page.locator('[data-pe-warn-n]').fill('2');
  await page.locator('[data-pe-warn-u]').selectOption({label: 'days'});
  await page.locator('[data-pe-warn-save=s1]').click();
  await page.waitForFunction(() => /2 days/.test(document.querySelector('.pe-stage').innerText));
  assert.deepEqual(last(writes, '/api/v2/pipeline-stages/s1'), {limit_minutes: 2880});
  await page.locator('[data-pe-warn=s1]').click();
  await page.locator('[data-pe-warn-off=s1]').click();
  await page.waitForFunction(() => /Warn after: Off/.test(document.querySelector('.pe-stage').innerText));
  assert.deepEqual(last(writes, '/api/v2/pipeline-stages/s1'), {limit_minutes: null});
  // A requirement: an approval needs a person; the kind's own shape is sent.
  await page.locator('[data-pe-new=s2]').click();
  await page.locator('.pe-form [name=name]').fill('Lead sign-off');
  await page.locator('.pe-form [name=kind]').selectOption('approval');
  assert.equal(await page.locator('.pe-form [name=reviewed_by]').inputValue(), 'person');
  await page.locator('.pe-form [name=reviewer_id]').selectOption('human:sam');
  await page.locator('.pe-form button[type=submit]').click();
  await page.waitForFunction(() => document.body.innerText.includes('Lead sign-off'));
  const added = last(writes, '/api/v2/pipeline-stages/s2/requirements');
  assert.equal(added.kind, 'approval'); assert.equal(added.reviewed_by, 'person'); assert.equal(added.reviewer_id, 'human:sam'); assert.deepEqual(added.needs, {});
  await page.locator('[data-pe-req=r1]').click();
  await page.locator('.pe-form [name=name]').fill('Screenshot of the fix');
  await page.locator('.pe-form button[type=submit]').click();
  await page.waitForFunction(() => document.body.innerText.includes('Screenshot of the fix'));
  assert.equal(writes.filter(w => w.method === 'PUT').at(-1).body.name, 'Screenshot of the fix');
  await page.locator('[data-pe-req=r1]').click();
  await page.locator('[data-pe-retire=r1]').click();
  await page.waitForFunction(() => !document.body.innerText.includes('Screenshot of the fix'));
  assert.equal(writes.filter(w => w.method === 'DELETE').length, 1);
  // Stages: rename, add (before Done), move, remove.
  await page.locator('[data-pe-rename=s1]').fill('Backlog');
  await page.locator('[data-pe-rename=s1]').blur();
  await page.waitForFunction(() => document.querySelector('[data-pe-rename=s1]').value === 'Backlog');
  assert.equal(last(writes, '/api/v2/task-types/dev').steps[0].name, 'Backlog');
  await settled(page);
  await page.locator('[data-pe-add-stage] input').fill('Review');
  await page.locator('[data-pe-add-stage] button').click();
  await page.waitForFunction(() => document.querySelectorAll('.pe-stage').length === 4);
  assert.deepEqual(last(writes, '/api/v2/task-types/dev').steps.map(s => s.name), ['Backlog', 'Doing', 'Review', 'Done']);
  await page.locator('[data-pe-up=s3]').click();
  await page.waitForFunction(() => document.querySelectorAll('.pe-stage-name')[2].value === 'Done');
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

async function hiddenAndRestricted(browser) {
  const a = await open(browser, {may: false});
  await a.page.waitForTimeout(400);
  assert.equal(await a.page.locator('#task-pipeline-edit').isHidden(), true);
  await a.page.close();
  // A reader who is not a mover edits requirements and settings, but not the stages themselves.
  const b = await open(browser, {mover: false});
  await b.page.waitForFunction(() => !document.getElementById('task-pipeline-edit').hidden);
  await b.page.locator('#task-pipeline-edit').click();
  await b.page.locator('.pe-stage').first().waitFor();
  assert.equal(await b.page.locator('[data-pe-add-stage], [data-pe-up], [data-pe-rename]').count(), 0);
  assert.equal(await b.page.locator('[data-pe-new]').count(), 3);
  await b.page.close();
}

async function phone(browser) {
  const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}});
  await page.waitForFunction(() => !document.getElementById('task-pipeline-edit').hidden);
  await page.locator('#task-pipeline-edit').click();
  await page.locator('.pe-stage').first().waitFor();
  const box = await page.locator('#pe-dialog').boundingBox();
  assert.ok(box.width >= 389 && box.y + box.height <= 845, `sheet ${JSON.stringify(box)}`);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways scroll');
  assert.deepEqual(errors, []);
  await page.close();
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try { await desktop(browser); await hiddenAndRestricted(browser); await phone(browser); console.log('pipeline-editor: ok'); } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
