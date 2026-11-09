import type {Route} from 'playwright';
// Offline browser regression: a place that names a pipeline shows its current name. The product pipeline is renamed here
// ("Bets", not the preset's "Product roadmap"), so a hard-coded label would fail: the scope menu lists it under its name,
// and the Ranked page (#/roadmap) heads itself with it.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs') as typeof import('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs') as typeof import('./support/routes.cjs');

const statuses = ['open', 'doing', 'review', 'ready', 'done', 'closed'];
const types = [
  {id: 'general', name: 'General', steps: statuses.map((status, position) => ({id: 'general-' + status, type_id: 'general', name: status, status, position}))},
  {id: 'bets', name: 'Bets', role: 'product-roadmap', manager: 'product-manager', steps: []},
  {id: 'engineering', name: 'Engineering', role: 'engineering', manager: 'engineering-lead', steps: []},
  {id: 'marketing', name: 'Marketing', steps: []},
];
const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const bots = [{name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', can_chat: true}];

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [];
  try {
    const page = await browser.newPage({viewport: {width: 1200, height: 900}, serviceWorkers: 'block'});
    page.on('pageerror', (error: Error) => errors.push(error.message));
    await page.route('**/*', (route: Route) => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      if (p === '/api/me') return json({...me, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json(bots);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/v2/task-types' && req.method() === 'GET') return json({types});
      if (p === '/api/v2/roadmap-tasks') return json({tasks: [], next_offset: null});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('http://tico-ui.test/#/tasks');
    await page.locator('#task-scope').waitFor();
    await page.locator('#task-scope').click();
    await page.locator('#task-filter-pop [data-task-type]').first().waitFor();
    const scope = await page.locator('#task-filter-pop [data-task-type]').evaluateAll((buttons: HTMLElement[]) => buttons.map(b => [b.dataset.taskType, b.textContent!.replace('✓', '').trim()]));
    assert.deepEqual(scope, [['general', 'General'], ['bets', 'Bets'], ['engineering', 'Engineering'], ['marketing', 'Marketing']],
      'the scope menu lists the product pipeline under its current name, not a fixed Roadmap label');
    await page.keyboard.press('Escape');
    await page.goto('http://tico-ui.test/#/roadmap');
    await page.waitForFunction(() => document.querySelector('.roadmap-page h1')?.textContent === 'Bets');
    assert.equal(await page.locator('.roadmap-page h1').innerText(), 'Bets', 'the Ranked page heads itself with the pipeline name');
    assert.deepEqual(errors, []);
    console.log('PASS: the scope menu and the Ranked page name the product pipeline as it is named now.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
