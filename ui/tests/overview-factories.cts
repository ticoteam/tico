import type {Route} from 'playwright';
// Overview › Factories (ui/app/overview-factories.ts): the flow map.
//  - the Factories tab at #/overview/factories; factory boxes, pipelines in flow order, counts, Late, running;
//  - lines labelled "38 · 2d" (count, median time in the source pipeline), no noun, no loop chips;
//  - input circles about 28px; the period control (Day | Week | Month) remembered after a reload and sent to the read;
//  - tap a pipeline opens its Tasks page; chat icons on a factory and a pipeline open the rail, its chips switch managers,
//    the thread is the Tasks rail's embedded DM (threadMount), Hide closes the rail and unmounts it;
//    a pipeline with no manager has no chat icon;
//  - lines hold still under prefers-reduced-motion; a phone stacks the factories, shows counts between the boxes and
//    opens chat as a full-screen sheet, with no sideways scroll.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {html, isUiPath, serveUi} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const map = {
  factories: [{id: 'f1', name: 'Lead factory', goal: null, manager_id: 'bot:lead', done: 12, overdue: 2, live: 1,
    scoreboard: {merges_per_hour: 0.13, queued_to_merged_median_minutes: 180,
      merged_to_production_median_minutes: 60, queue_size: 4}}],
  pipelines: [
    {id: 'camp', name: 'Campaigns', goal: null, manager_id: 'bot:lead', factory_id: 'f1', cards: 5, overdue: 2, for_you: 1, live: 1},
    {id: 'sales', name: 'Sales', goal: null, manager_id: 'sales', factory_id: 'f1', cards: 9, overdue: 0, for_you: 0, live: 0},
    {id: 'help', name: 'Help desk', goal: null, manager_id: null, factory_id: null, cards: 3, overdue: 0, for_you: 0, live: 0},
  ],
  edges: [{from: 'camp', to: 'sales', count: 38, median_age_minutes: 2880}],
  inputs: [{label: 'Ads', count: 14, to: 'camp'}],
};

async function open(browser: import('playwright').Browser, {viewport = {width: 1280, height: 820}, hash = '#/overview/factories', reduce = false, data = map as unknown, role = 'owner'} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 600, isMobile: viewport.width < 600, reducedMotion: reduce ? 'reduce' : 'no-preference'});
  const errors: unknown[] = [], periods: unknown[] = [], dms: unknown[] = [], factories: unknown[] = [];
  page.on('pageerror', (e: Error) => errors.push(e.message));
  await page.addInitScript(() => { try { localStorage.setItem('tico.theme', 'dark'); } catch {} });
  const me = {id: 'ana', role, name: 'Ana Ruiz', email: 'ana@example.com', cloud: true, registered: true, owner_id: 'ana', config: {company_name: 'Acme Bikes'}};
  await page.route('**/*', async (route: Route) => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (isUiPath(p)) return serveUi(route, p);
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/api/me') return json(me);
    if (p === '/api/employees') return json([{name: 'lead', display_name: 'Lead Manager', status: 'active', state: 'active', users: [], bot_owners: [], my_access: {see: true, read: true, write: true}}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Ruiz', role: 'owner'}], teams: {}, org_groups: []});
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/pipelines/overview') { periods.push(url.searchParams.get('period')); return json(data); }
    if (p === '/api/v2/factories') {
      if (req.method() !== 'POST') return json({factories: []});
      factories.push(JSON.parse(req.postData() || '{}'));
      return json({id: 'fac-new'});
    }
    if (p === '/api/v2/threads/dm') { dms.push(url.searchParams.get('with')); return json({thread: {id: 'th' + dms.length}}); }
    if (p.startsWith('/api/v2/threads/th') && p.endsWith('/timeline')) return json({thread: {id: p.split('/')[4]}, messages: [], next_cursor: null, read_marks: {}});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/' + hash);
  await page.locator('.dash-tabs').first().waitFor();
  return {page, errors, periods, dms, factories};
}

async function desktop(browser: import('playwright').Browser) {
  const {page, errors, periods, dms} = await open(browser);
  await page.locator('.of-node').first().waitFor();
  assert.equal(await page.locator('.dash-tabs a.cur').innerText(), 'Factories');
  assert.deepEqual(await page.locator('.of-factory h2').allInnerTexts(), ['Lead factory', 'Other pipelines']);
  assert.deepEqual(await page.locator('.of-scoreboard dt').allInnerTexts(),
    ['Merges / hour', 'Queued to merged median', 'Merged to production median', 'Queue size (now)']);
  assert.deepEqual(await page.locator('.of-scoreboard dd').allInnerTexts(), ['0.13', '3h', '1h', '4']);
  assert.deepEqual(await page.locator('.of-name').allInnerTexts(), ['Campaigns', 'Sales', 'Help desk']);
  assert.match(await page.locator('[data-of-pl=camp]').innerText(), /5 open.*2 Late.*1 running.*1 for you/s);
  assert.equal(await page.locator('.of-loop, .of-loops').count(), 0);
  // The line is labelled with the count and the median time, and nothing else.
  await page.waitForSelector('.of-edge-l');
  assert.equal(await page.locator('.of-edge-l').textContent(), '38 · 2d');
  assert.equal(await page.locator('.of-edge:not(.of-edge-in)').count(), 1);
  const dot = (await page.locator('.of-dot').boundingBox())!;
  assert.ok(dot.width >= 27 && dot.width <= 34 && dot.height === 28, `input circle ${dot.width}x${dot.height}`);
  assert.equal(await page.locator('.of-dot').innerText(), '14');
  assert.equal(await page.locator('.of-in-l').innerText(), 'Ads');
  assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('.of-edge')!).animationName), 'of-flow');
  // The period: the week at first, and the one picked is remembered.
  assert.equal(await page.locator('[data-of-period="7d"]').getAttribute('aria-pressed'), 'true');
  await page.locator('[data-of-period="30d"]').click();
  await page.waitForFunction(() => document.querySelector('[data-of-period="30d"]')!.getAttribute('aria-pressed') === 'true');
  await page.reload();
  await page.locator('.of-node').first().waitFor();
  assert.equal(await page.locator('[data-of-period="30d"]').getAttribute('aria-pressed'), 'true');
  assert.deepEqual(periods.slice(0, 3), ['7d', '30d', '30d']);
  // Chat: a factory or pipeline with a manager has the icon; one without has none. The rail embeds the manager's thread,
  // its chips (every node with a manager, in map order) switch managers, and Hide unmounts the thread.
  const until = async (test: () => boolean) => { for (let i = 0; i < 100 && !test(); i++) await new Promise(r => setTimeout(r, 50)); assert.ok(test(), 'timed out'); };
  assert.equal(await page.locator('.of-chat').count(), 3);
  assert.equal(await page.locator('[data-of-pl=help] .of-chat').count(), 0);
  assert.equal(await page.locator('#of-rail').isHidden(), true);
  await page.locator('.of-factory header .of-chat').click();
  await page.locator('#of-rail-chat .thv').waitFor();
  assert.deepEqual(await page.locator('.of-chip').allInnerTexts(), ['Lead factory', 'Campaigns', 'Sales']);
  assert.equal(await page.locator('.of-chip[aria-pressed=true]').innerText(), 'Lead factory');
  assert.deepEqual(dms, ['bot:lead']);
  // Campaigns has the same manager, so its chip shows the same thread: no second read of it.
  await page.locator('[data-of-pl=camp] .of-chat').click();
  await page.waitForFunction(() => document.querySelector('.of-chip[aria-pressed=true]')?.textContent === 'Campaigns');
  assert.equal(await page.locator('#of-rail-chat .thv').count(), 1);
  assert.equal(await page.locator('[data-of-pl=camp] .of-chat[aria-pressed=true]').count(), 1);
  // Sales has another manager: its chip swaps the thread.
  await page.locator('[data-of-chip="p:sales"]').click();
  await page.waitForFunction(() => document.querySelector('.of-chip[aria-pressed=true]')?.textContent === 'Sales');
  await until(() => dms.length === 2);
  assert.deepEqual(dms, ['bot:lead', 'bot:sales']);
  assert.equal(await page.locator('#of-rail-chat .thv').count(), 1);
  // Hide closes the rail and takes its thread away.
  await page.locator('[data-of-rail-hide]').click();
  assert.equal(await page.locator('#of-rail').isHidden(), true);
  assert.equal(await page.locator('#of-rail-chat .thv').count(), 0);
  assert.equal(await page.locator('.of-chat[aria-pressed=true]').count(), 0);
  // Tap a pipeline: its page.
  await page.locator('[data-of-pl=sales] .of-link').click();
  await page.waitForFunction(() => location.hash.startsWith('#/tasks') && location.hash.includes('type=sales'));
  assert.equal(await page.evaluate(() => new URL('https://x/' + location.hash.slice(1)).searchParams.get('view')), 'board');
  assert.deepEqual(errors, []);
  await page.close();
}

async function mapExtras(browser: import('playwright').Browser) {
  // A spawn is drawn dashed and a move solid; a loop is one labelled line back, "laps · median lap".
  const data = {...map,
    edges: [...map.edges, {from: 'camp', to: 'help', mode: 'spawn', count: 4, median_age_minutes: null}],
    loops: [{pipelines: ['camp', 'sales'], laps: 3, median_lap_minutes: 1440}]};
  const {page, errors} = await open(browser, {data});
  await page.locator('.of-node').first().waitFor();
  await page.waitForSelector('.of-loop-l');
  assert.equal(await page.locator('.of-edge-spawn').count(), 1);
  assert.notEqual(await page.evaluate(() => getComputedStyle(document.querySelector('.of-edge-spawn')!).strokeDasharray), 'none');
  assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('.of-edge:not(.of-edge-spawn):not(.of-edge-in)')!).strokeDasharray), 'none');
  assert.equal(await page.locator('.of-loop-l').textContent(), '3 · 1d');
  assert.deepEqual(errors, []);
  await page.close();
}

async function noLinks(browser: import('playwright').Browser) {
  // A factory whose pipelines have no links between them (no Marketing to Sales): it renders with no lines or loops.
  const data = {...map, edges: [], loops: [], inputs: []};
  const {page, errors} = await open(browser, {data});
  await page.locator('.of-node').first().waitFor();
  assert.equal(await page.locator('.of-edge').count(), 0);
  assert.equal(await page.locator('.of-loop').count(), 0);
  assert.equal(await page.locator('.of-factory h2').first().innerText(), 'Lead factory');
  assert.deepEqual(errors, []);
  await page.close();
}

async function makeFactory(browser: import('playwright').Browser) {
  // + Factory: the owner and Admins see it; it posts name and an optional goal, then the map reloads.
  const {page, errors, periods, factories} = await open(browser);
  await page.locator('.of-node').first().waitFor();
  await page.locator('#of-new-b').click();
  await page.locator('#of-new').waitFor();
  await page.locator('#of-new input[name=name]').fill('Support');
  await page.locator('#of-new button[type=submit]').click();
  await page.waitForFunction(() => (document.querySelector('#of-new') as HTMLElement | null)?.hidden === true);
  assert.deepEqual(factories, [{name: 'Support', goal: null}]);
  assert.ok(periods.length >= 2, 'the map reloads after the save');
  assert.deepEqual(errors, []);
  await page.close();
  const member = await open(browser, {role: 'member'});
  await member.page.locator('.of-node').first().waitFor();
  assert.equal(await member.page.locator('#of-new-b').count(), 0);
  await member.page.close();
}

async function reducedMotion(browser: import('playwright').Browser) {
  const {page} = await open(browser, {reduce: true});
  await page.waitForSelector('.of-edge', {state: 'attached'});
  assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('.of-edge')!).animationName), 'none');
  await page.close();
}

async function phone(browser: import('playwright').Browser) {
  const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}});
  await page.locator('.of-node').first().waitFor();
  const boxes = await page.locator('.of-node').evaluateAll((n: HTMLElement[]) => n.map((e: HTMLElement) => e.getBoundingClientRect().top));
  assert.ok(boxes[0]! < boxes[1]! && boxes[1]! < boxes[2]!, 'pipelines stack');
  assert.equal(await page.locator('#of-lines').isVisible(), false);
  assert.match(await page.locator('[data-of-pl=camp] .of-onward').innerText(), /38 · 2d to Sales/);
  assert.match(await page.locator('[data-of-pl=camp] .of-in-chip').innerText(), /Ads 14/);
  await page.locator('[data-of-pl=camp] .of-chat').click();
  await page.locator('#of-rail-chat .thv').waitFor();
  const sheet = (await page.locator('#of-rail').boundingBox())!;
  assert.ok(sheet.width >= 389 && sheet.height >= 843, `sheet ${sheet.width}x${sheet.height}`);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways scroll');
  await page.locator('[data-of-rail-hide]').click();
  assert.equal(await page.locator('#of-rail').isHidden(), true);
  assert.deepEqual(errors, []);
  await page.close();
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined) as string});
  try { await desktop(browser); await mapExtras(browser); await noLinks(browser); await makeFactory(browser); await reducedMotion(browser); await phone(browser); console.log('overview-factories: ok'); } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
