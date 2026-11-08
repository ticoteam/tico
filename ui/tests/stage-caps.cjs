// Stage caps (D68, D69) on a phone at 390x844: a full stage's chip reads "3/3", the ✎ editor's "At most" field sets a
// stage's cap (PATCH /v2/pipeline-stages/{id} with card_cap), "No cap" clears it, and On ice reads "At most: 0".
// Screenshots go to $TICO_UI_SHOTS when it names a folder.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const stamp = '2026-11-08T09:00:00Z';
const SHOTS = process.env.TICO_UI_SHOTS;
const shot = (page, name, locator) => {
  if (!SHOTS) return Promise.resolve();
  fs.mkdirSync(SHOTS, {recursive: true});
  return (locator ? locator.screenshot({path: path.join(SHOTS, name)}) : page.screenshot({path: path.join(SHOTS, name)}));
};

// The Dev tickets pipeline: On ice (cap 0) first, Specifying capped at 3 with three cards in it, In development uncapped.
function initialSteps() {
  return [
    {id: 'ice', name: 'On ice', status: 'open', card_cap: 0, limit_minutes: null},
    {id: 'spec', name: 'Specifying', status: 'doing', card_cap: 3, limit_minutes: null},
    {id: 'dev', name: 'In development', status: 'doing', card_cap: null, limit_minutes: null},
    {id: 'done', name: 'Done', status: 'done', card_cap: null, limit_minutes: null},
  ].map((s, position) => ({...s, type_id: 'dev', position}));
}

function typeOf(state) {
  return {id: 'dev', name: 'Dev tickets', prefix: 'D', pipeline_owner_id: 'human:ana', manager: null, role: 'engineering', has_requirements: true, steps: state.steps};
}

// Where each card sits: the three specs in Specifying, and Build the export in In development until a person moves it.
const HOME = {T41: 'spec', T42: 'spec', T43: 'spec', T44: 'dev'};
function taskOf(state, id) {
  const where = state.where[id] ?? HOME[id];
  const step = state.steps.find(s => s.id === where);
  const title = {T41: 'Spec the export', T42: 'Spec the import', T43: 'Spec the sync', T44: 'Build the export'}[id];
  return {id, number: Number(id.slice(1)), title, body: '', owner_id: 'bot:builder', requester_id: 'human:ana', status: step.status, lane: 'company',
    version: 1, labels: [], links: [], parts: {total: 0, done: 0}, rank: Number(id.slice(1)), created_at: stamp, updated_at: stamp, step_since: stamp,
    type_id: 'dev', type: {id: 'dev', name: 'Dev tickets'}, step_id: where, step: {...step, type_id: 'dev'}};
}
function tasksOf(state) {
  return Object.keys(HOME).map(id => taskOf(state, id));
}

async function open(browser) {
  const page = await browser.newPage({viewport: {width: 390, height: 844}, serviceWorkers: 'block', hasTouch: true, isMobile: true});
  const errors = [], writes = [];
  const state = {steps: initialSteps(), where: {}, moves: []};
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(() => { try { localStorage.setItem('tico.theme', 'dark'); } catch {} });
  const me = {id: 'ana', role: 'owner', name: 'Ana Ruiz', email: 'ana@example.com', cloud: true, registered: true, owner_id: 'ana', mover: true, config: {company_name: 'Acme Bikes'}};
  const settings = {id: 'dev', name: 'Dev tickets', prefix: 'D', goal: null, judge_model: null, visibility: 'company', factory_id: null,
    owner_id: 'human:ana', manager_id: null, members: [], judge_models: ['haiku-5.5'], default_judge_model: 'haiku-5.5'};
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
    if (p === '/api/employees') return json([]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Ruiz', role: 'owner'}], teams: {}, org_groups: []});
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/issues') return json([]);
    if (p === '/api/v2/needs-you') return json({items: [], count: 0, plate_count: 0});
    if (p === '/api/v2/tasks' && method === 'GET') return json({tasks: tasksOf(state), next: null, labels: [], tags: []});
    const one = p.match(/^\/api\/v2\/tasks\/(T\d+)$/);
    if (one && method === 'GET') return json({task: taskOf(state, one[1]), children: [], parent: null, comments: [], events: [], messages: [], mover: true});
    if (one && method === 'POST') {
      // Like the server: a person's move into a full stage stands and answers over_cap (D68).
      const target = body.step;
      const cap = state.steps.find(s => s.id === target)?.card_cap;
      const held = tasksOf(state).filter(t => t.step_id === target && t.id !== one[1]).length;
      const over = !!target && cap != null && held >= cap;
      state.where[one[1]] = target || HOME[one[1]];
      state.moves.push({id: one[1], body, over});
      return json({task: taskOf(state, one[1]), ...(over ? {over_cap: true} : {})});
    }
    if (p === '/api/v2/tasks/labels') return json({labels: [], tags: []});
    if (p === '/api/v2/tags') return json({tags: []});
    if (p === '/api/v2/task-types' && method === 'GET') return json({types: [typeOf(state)]});
    if (p === '/api/v2/task-types/dev' && method === 'GET') return json({type: typeOf(state)});
    if (p === '/api/v2/pipelines/dev/rights') return json({may_edit_rules: true});
    if (p === '/api/v2/pipelines/dev/settings' && method === 'GET') return json(settings);
    if (p === '/api/v2/factories') return json({factories: []});
    if (p === '/api/v2/pipeline-links' && method === 'GET') return json({links: []});
    const stageReqs = p.match(/^\/api\/v2\/pipeline-stages\/(\w+)\/requirements$/);
    if (stageReqs && method === 'GET') return json({requirements: []});
    const stage = p.match(/^\/api\/v2\/pipeline-stages\/(\w+)$/);
    if (stage && method === 'PATCH') {
      const s = state.steps.find(x => x.id === stage[1]);
      if ('card_cap' in body) s.card_cap = body.card_cap;
      if ('limit_minutes' in body) s.limit_minutes = body.limit_minutes;
      return json({});
    }
    if (p.startsWith('/api/v2/preferences/')) return json({key: 'tasks.view', value: null});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/tasks?view=board&type=dev&who=all');
  await page.locator('.pchips').waitFor();
  return {page, errors, writes};
}

const settled = async page => { await page.waitForTimeout(100); await page.waitForLoadState('networkidle'); };
const lastPatch = (writes, id) => writes.filter(w => w.method === 'PATCH' && w.p === `/api/v2/pipeline-stages/${id}`).at(-1)?.body;

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const {page, errors, writes} = await open(browser);

    // The phone's chips: Specifying holds three cards against its cap of three.
    const chip = page.locator('.pchip', {hasText: 'Specifying'});
    await chip.waitFor();
    assert.match(await chip.innerText(), /3\/3/, 'the Specifying chip reads 3/3');
    assert.match(await page.locator('.pchip', {hasText: 'In development'}).innerText(), /^In development\s*1$/, 'an uncapped stage shows its count');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways scroll on the board');
    await shot(page, 'phone-chip-3of3.png');

    // A person moves Build the export into Specifying, which is full: the move stands, the answer says over_cap, and the
    // one-line warning shows. The chip then reads 4/3.
    await page.locator('.pchip', {hasText: 'In development'}).click();
    await page.locator('.bcard-open', {hasText: 'Build the export'}).first().click();
    const stepButton = page.locator('.prop-v[data-prop="step"]');
    await stepButton.waitFor();
    await stepButton.click();
    await page.locator('.tl-mi', {hasText: 'Specifying'}).first().click();
    await page.locator('.toast', {hasText: 'Specifying is over its cap of 3.'}).waitFor();
    assert.equal(writes.filter(w => w.method === 'POST' && w.p === '/api/v2/tasks/T44').at(-1)?.body.step, 'spec', 'the move is sent');
    await page.keyboard.press('Escape');
    await page.locator('.pchip', {hasText: 'Specifying'}).filter({hasText: '4/3'}).waitFor();

    // The ✎ editor: On ice reads 0, Specifying has the "At most" field beside "Warn after".
    await page.waitForFunction(() => !document.getElementById('task-pipeline-edit').hidden);
    await page.locator('#task-pipeline-edit').click();
    // The stage names are inputs in the editor (for a mover), so the stages are found by their order: On ice, then Specifying.
    const stages = page.locator('#pe-dialog .pe-stage');
    await stages.nth(1).waitFor();
    assert.equal(await stages.nth(1).locator('.pe-stage-name').inputValue(), 'Specifying');
    assert.match(await stages.nth(0).innerText(), /At most: 0/, 'On ice is at most 0');
    const specifying = stages.nth(1);
    const atMost = specifying.locator('[data-pe-cap]');
    assert.match(await atMost.innerText(), /At most: 3/);
    await atMost.click();
    const field = specifying.locator('[data-pe-cap-n]');
    await field.waitFor();
    assert.equal(await field.inputValue(), '3');
    await shot(page, 'at-most-field.png', page.locator('#pe-dialog'));

    // A cap is saved as a whole number; a fraction is refused here and sends nothing.
    await field.fill('2.5');
    await specifying.locator('[data-pe-cap-save]').click();
    await settled(page);
    assert.equal(lastPatch(writes, 'spec'), undefined, 'a fraction is not sent');
    await field.fill('5');
    await specifying.locator('[data-pe-cap-save]').click();
    await settled(page);
    assert.deepEqual(lastPatch(writes, 'spec'), {card_cap: 5});
    await page.waitForFunction(() => /At most: 5/.test(document.querySelectorAll('#pe-dialog .pe-stage [data-pe-cap]')[1]?.textContent ?? ''));

    // "No cap" clears it.
    await specifying.locator('[data-pe-cap]').click();
    await specifying.locator('[data-pe-cap-off]').click();
    await settled(page);
    assert.deepEqual(lastPatch(writes, 'spec'), {card_cap: null});
    assert.deepEqual(writes.filter(w => w.method === 'PATCH').map(w => w.p).filter(p => p.endsWith('/spec')).length, 2);

    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways scroll in the editor');
    assert.deepEqual(errors, [], 'no page errors');
    await page.close();
    console.log('stage-caps: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
