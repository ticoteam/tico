import type {Route} from 'playwright';
// Pipeline spend caps (spec v2, pipeline budgets, editor half): the Spend caps block in the pipeline editor and the Spend line
// on the pipeline's page. Reads: GET /api/v2/pipelines/{id}/spend. Saves: PATCH /api/v2/pipelines/{id}/settings with
// daily_cap_usd / monthly_cap_usd (a number above zero, or null for no cap).
//  - an owner opens the editor from the pencil: both caps blank, each field shows its period's spend as a bar once a cap is set;
//  - a daily cap saves, the editor redraws it, and the pipeline's page shows it beside today's spend (A2);
//  - zero, a negative number and text are refused with the one message and nothing is sent (the stored cap stays);
//  - a reader who may not edit sees the caps and the Spend line read-only, with no edit control (A15);
//  - a pipeline whose spend route does not answer shows no Spend line.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {html, isUiPath, serveUi} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

type Write = {method: string; p: string; body: Record<string, unknown>};
type Spend = {daily_cap_usd: number | null; monthly_cap_usd: number | null; today_usd: number; month_usd: number};

const REFUSED = {error: {code: 'validation', detail: 'Enter an amount above zero, or leave it blank.'}};
const MESSAGE = 'Enter an amount above zero, or leave it blank.';
const settled = async (page: import('playwright').Page) => { await page.waitForTimeout(100); await page.waitForLoadState('networkidle'); };
const lastBody = (writes: Write[], p: string): Record<string, unknown> | undefined => writes.filter(w => w.p === p).at(-1)?.body;

const LEAD = {name: 'lead', display_name: 'Lead Manager', status: 'active', state: 'active', users: [], bot_owners: [], my_access: {see: true, read: true, write: true}};

async function open(browser: import('playwright').Browser, {may = true, spendRoute = true, daily = null}: {may?: boolean; spendRoute?: boolean; daily?: number | null} = {}) {
  const page = await browser.newPage({viewport: {width: 1280, height: 820}, serviceWorkers: 'block'});
  const errors: string[] = [], writes: Write[] = [];
  const spend: Spend = {daily_cap_usd: daily, monthly_cap_usd: null, today_usd: 1.25, month_usd: 42.1};
  const settings = {id: 'dev', name: 'Dev tickets', prefix: 'D', goal: null, visibility: 'company', factory_id: null,
    owner_id: 'human:ana', manager_id: null, members: [], stuck_after_hours: null, ask_person_after_hours: null, question_expiry_days: null,
    stuck_rule_defaults: {stuck_after_hours: 4, ask_person_after_hours: 24, question_expiry_days: 7}};
  const type = {id: 'dev', name: 'Dev tickets', prefix: 'D', pipeline_owner_id: 'human:ana', manager: null, steps: [
    {id: 's1', name: 'Todo', status: 'open', limit_minutes: null, card_cap: null}, {id: 's2', name: 'Doing', status: 'doing', limit_minutes: null, card_cap: null},
    {id: 's3', name: 'Done', status: 'done', limit_minutes: null, card_cap: null}]};
  page.on('pageerror', (e: Error) => errors.push(e.message));
  await page.addInitScript(() => { try { localStorage.setItem('tico.theme', 'dark'); } catch {} });
  const me = {id: 'ana', role: 'owner', name: 'Ana Ruiz', email: 'ana@example.com', cloud: true, registered: true, owner_id: 'ana', mover: true, config: {company_name: 'Acme Bikes'}};
  await page.route('**/*', async (route: Route) => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (isUiPath(p)) return serveUi(route, p);
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    const body = (method === 'GET' ? {} : req.postDataJSON()) as Record<string, unknown>;
    if (method !== 'GET') writes.push({method, p, body});
    if (p === '/api/me') return json(me);
    if (p === '/api/employees') return json([LEAD]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Ruiz', role: 'owner'}, {id: 'sam', name: 'Sam Okafor', role: 'member'}], teams: {}, org_groups: []});
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/issues') return json([]);
    if (p === '/api/v2/needs-you') return json({items: [], count: 0, plate_count: 0});
    if (p === '/api/v2/tasks' && method === 'GET') return json({tasks: [], next: null, labels: [], tags: []});
    if (p === '/api/v2/tasks/labels') return json({labels: [], tags: []});
    if (p === '/api/v2/tags') return json({tags: []});
    if (p === '/api/v2/pipelines/dev/rights') return json({may_edit_rules: may});
    if (p === '/api/v2/task-types' && method === 'GET') return json({types: [type]});
    if (p === '/api/v2/task-types/dev' && method === 'GET') return json({type});
    if (p === '/api/v2/pipelines/dev/settings' && method === 'GET') return json(settings);
    if (p === '/api/v2/pipelines/dev/settings') {
      for (const key of ['daily_cap_usd', 'monthly_cap_usd'] as const) {
        if (!(key in body)) continue;
        const value = body[key];
        // The server's check (backend/pipeline_requirements.py): a cap is null or a number above zero; nothing is stored on a refusal.
        if (value !== null && !(typeof value === 'number' && value > 0)) return json(REFUSED, 422);
        spend[key] = value as number | null;
      }
      return json(settings);
    }
    if (p === '/api/v2/pipelines/dev/spend' && method === 'GET') return spendRoute ? json(spend) : json({error: {code: 'not_found'}}, 404);
    if (p === '/api/v2/factories') return json({factories: []});
    if (p === '/api/v2/pipeline-links' && method === 'GET') return json({links: []});
    if (p.startsWith('/api/v2/preferences/')) return json({key: 'tasks.view', value: null});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/tasks?type=dev&view=list');
  await page.locator('#task-scope').waitFor();
  return {page, errors, writes, spend};
}

// An owner sets a daily cap, and the page shows it beside today's spend; zero, a negative and text are refused; blank clears.
async function ownerSetsCaps(browser: import('playwright').Browser) {
  const {page, errors, writes, spend} = await open(browser);
  await page.waitForFunction(() => !document.getElementById('task-pipeline-edit')!.hidden);
  await page.locator('#task-pipeline-edit').click();
  await page.locator('[data-pe-caps]').waitFor();
  const daily = page.locator('[data-pe-cap-amount=daily_cap_usd]'), monthly = page.locator('[data-pe-cap-amount=monthly_cap_usd]');
  assert.equal(await daily.inputValue(), '', 'A1: the daily cap is blank');
  assert.equal(await monthly.inputValue(), '', 'A1: the monthly cap is blank');
  assert.equal(await daily.getAttribute('placeholder'), 'No cap', 'A1: a blank cap reads No cap');
  const spent = await page.locator('.pe-cap-spent').allTextContents();
  assert.ok(spent[0]?.includes('Today: $1.25') && spent[1]?.includes('This month: $42.10'), `each field shows its period's spend: ${JSON.stringify(spent)}`);
  assert.equal(await page.locator('[data-pe-caps] .use-meter').count(), 0, 'no bar until a cap is set');

  await daily.fill('5');
  await daily.press('Enter');
  await settled(page);
  assert.deepEqual(lastBody(writes, '/api/v2/pipelines/dev/settings'), {daily_cap_usd: 5}, 'a daily cap saves through the settings route, that field only');
  assert.equal(spend.daily_cap_usd, 5, 'the cap is stored');
  assert.equal(await daily.inputValue(), '5', 'the editor redraws the saved cap');
  assert.equal(await page.locator('[data-pe-caps] .use-meter').count(), 1, 'a set cap shows its bar');

  const before = writes.length;
  for (const raw of ['0', '-3']) {
    await daily.fill(raw);
    await daily.press('Enter');
    await settled(page);
    assert.ok(await page.getByText(MESSAGE).count() >= 1, `${raw} is refused with the one message`);
    assert.equal(writes.length, before, `${raw} sends nothing`);
    assert.equal(spend.daily_cap_usd, 5, `${raw} leaves the stored cap as it was`);
  }

  // A2: the saved cap is on the pipeline's page, beside today's spend.
  await page.locator('[data-pe-close]').click();
  await page.locator('#task-spend').waitFor();
  const line = await page.locator('#task-spend').innerText();
  assert.ok(line.includes('today $1.25') && line.includes('of $5.00 cap'), `the Spend line shows the cap beside today's spend: ${line}`);

  // Blank clears the cap.
  await page.locator('#task-pipeline-edit').click();
  await page.locator('[data-pe-caps]').waitFor();
  await page.locator('[data-pe-cap-amount=daily_cap_usd]').fill('');
  await page.locator('[data-pe-cap-amount=daily_cap_usd]').press('Enter');
  await settled(page);
  assert.deepEqual(lastBody(writes, '/api/v2/pipelines/dev/settings'), {daily_cap_usd: null}, 'a blank field clears the cap');
  assert.equal(spend.daily_cap_usd, null, 'blank means no cap');
  assert.deepEqual(errors, [], 'no page errors');
  await page.close();
}

// A reader who may not edit sees the caps and the Spend line read-only, with no edit control (A15).
async function readerSeesReadOnly(browser: import('playwright').Browser) {
  const {page, errors} = await open(browser, {may: false, daily: 5});
  await page.locator('#task-spend').waitFor();
  assert.equal(await page.locator('#task-pipeline-edit').isHidden(), true, 'A15: no pencil');
  assert.equal(await page.locator('#task-spend input, #task-spend button').count(), 0, 'A15: the Spend line has no control');
  const line = await page.locator('#task-spend').innerText();
  assert.ok(line.includes('today $1.25') && line.includes('of $5.00 cap'), `A15: the Spend line reads the cap: ${line}`);
  assert.equal(await page.locator('[data-pe-cap-amount]').count(), 0, 'A15: no cap field on the page');
  assert.deepEqual(errors, [], 'reader: no page errors');
  await page.close();
}

// A pipeline whose spend route does not answer shows no Spend line.
async function noSpendRoute(browser: import('playwright').Browser) {
  const {page, errors} = await open(browser, {spendRoute: false});
  await settled(page);
  assert.equal(await page.locator('#task-spend').isVisible(), false, 'no Spend line without the route');
  assert.deepEqual(errors, [], 'no spend route: no page errors');
  await page.close();
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined) as string});
  try {
    await ownerSetsCaps(browser);
    await readerSeesReadOnly(browser);
    await noSpendRoute(browser);
    console.log('pipeline-spend-caps: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
