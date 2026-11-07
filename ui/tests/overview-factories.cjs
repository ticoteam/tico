// Overview › Factories (ui/app/overview-factories.ts): the flow map.
//  - the Factories tab at #/overview/factories; factory boxes, pipelines in flow order, counts, Late, running;
//  - lines labelled "38 · 2d" (count, median time in the source pipeline), no noun, no loop chips;
//  - input circles about 28px; the period control (Day | Week | Month) remembered after a reload and sent to the read;
//  - tap a pipeline opens its Tasks page; chat icons on a factory and a pipeline open the rail, its chips switch managers,
//    the thread is the Tasks rail's embedded DM (threadMount), Hide closes the rail and unmounts it;
//    a pipeline with no manager has no chat icon;
//  - lines hold still under prefers-reduced-motion; a phone stacks the factories, shows counts between the boxes and
//    opens chat as a full-screen sheet, with no sideways scroll.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const map = {
  factories: [{id: 'f1', name: 'Lead factory', goal: null, manager_id: 'bot:lead', done: 12, overdue: 2, live: 1}],
  pipelines: [
    {id: 'camp', name: 'Campaigns', goal: null, manager_id: 'bot:lead', factory_id: 'f1', cards: 5, overdue: 2, for_you: 1, live: 1},
    {id: 'sales', name: 'Sales', goal: null, manager_id: 'sales', factory_id: 'f1', cards: 9, overdue: 0, for_you: 0, live: 0},
    {id: 'help', name: 'Help desk', goal: null, manager_id: null, factory_id: null, cards: 3, overdue: 0, for_you: 0, live: 0},
  ],
  edges: [{from: 'camp', to: 'sales', count: 38, median_age_minutes: 2880}],
  inputs: [{label: 'Ads', count: 14, to: 'camp'}],
};

async function open(browser, {viewport = {width: 1280, height: 820}, hash = '#/overview/factories', reduce = false} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 600, isMobile: viewport.width < 600, reducedMotion: reduce ? 'reduce' : 'no-preference'});
  const errors = [], periods = [], dms = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(() => { try { localStorage.setItem('tico.theme', 'dark'); } catch {} });
  const me = {id: 'ana', role: 'owner', name: 'Ana Ruiz', email: 'ana@example.com', cloud: true, registered: true, owner_id: 'ana', config: {company_name: 'Acme Bikes'}};
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p.startsWith('/tico/ui/')) {
      const name = p.slice('/tico/ui/'.length), file = uiFile(name);
      if (fs.existsSync(file)) return route.fulfill({body: fs.readFileSync(file), contentType: name.endsWith('.css') ? 'text/css' : name.endsWith('.js') ? 'application/javascript' : 'application/octet-stream'});
    }
    if (p.startsWith('/vendor/fonts/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile(p.slice(1)))});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/api/me') return json(me);
    if (p === '/api/employees') return json([{name: 'lead', display_name: 'Lead Manager', status: 'active', state: 'active', users: [], bot_owners: [], my_access: {see: true, read: true, write: true}}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Ruiz', role: 'owner'}], teams: {}, org_groups: []});
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/pipelines/overview') { periods.push(url.searchParams.get('period')); return json(map); }
    if (p === '/api/v2/threads/dm') { dms.push(url.searchParams.get('with')); return json({thread: {id: 'th' + dms.length}}); }
    if (p.startsWith('/api/v2/threads/th') && p.endsWith('/timeline')) return json({thread: {id: p.split('/')[4]}, messages: [], next_cursor: null, read_marks: {}});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/' + hash);
  await page.locator('.dash-tabs').first().waitFor();
  return {page, errors, periods, dms};
}

async function desktop(browser) {
  const {page, errors, periods, dms} = await open(browser);
  await page.locator('.of-node').first().waitFor();
  assert.equal(await page.locator('.dash-tabs a.cur').innerText(), 'Factories');
  assert.deepEqual(await page.locator('.of-factory h2').allInnerTexts(), ['Lead factory', 'Other pipelines']);
  assert.deepEqual(await page.locator('.of-name').allInnerTexts(), ['Campaigns', 'Sales', 'Help desk']);
  assert.match(await page.locator('[data-of-pl=camp]').innerText(), /5 open.*2 Late.*1 running.*1 for you/s);
  assert.equal(await page.locator('.of-loop, .of-loops').count(), 0);
  // The line is labelled with the count and the median time, and nothing else.
  await page.waitForSelector('.of-edge-l');
  assert.equal(await page.locator('.of-edge-l').textContent(), '38 · 2d');
  assert.equal(await page.locator('.of-edge:not(.of-edge-in)').count(), 1);
  const dot = await page.locator('.of-dot').boundingBox();
  assert.ok(dot.width >= 27 && dot.width <= 34 && dot.height === 28, `input circle ${dot.width}x${dot.height}`);
  assert.equal(await page.locator('.of-dot').innerText(), '14');
  assert.equal(await page.locator('.of-in-l').innerText(), 'Ads');
  assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('.of-edge')).animationName), 'of-flow');
  // The period: the week at first, and the one picked is remembered.
  assert.equal(await page.locator('[data-of-period="7d"]').getAttribute('aria-pressed'), 'true');
  await page.locator('[data-of-period="30d"]').click();
  await page.waitForFunction(() => document.querySelector('[data-of-period="30d"]').getAttribute('aria-pressed') === 'true');
  await page.reload();
  await page.locator('.of-node').first().waitFor();
  assert.equal(await page.locator('[data-of-period="30d"]').getAttribute('aria-pressed'), 'true');
  assert.deepEqual(periods.slice(0, 3), ['7d', '30d', '30d']);
  // Chat: a factory or pipeline with a manager has the icon; one without has none. The rail embeds the manager's thread,
  // its chips (every node with a manager, in map order) switch managers, and Hide unmounts the thread.
  const until = async (test) => { for (let i = 0; i < 100 && !test(); i++) await new Promise(r => setTimeout(r, 50)); assert.ok(test(), 'timed out'); };
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
  assert.deepEqual(errors, []);
  await page.close();
}

async function reducedMotion(browser) {
  const {page} = await open(browser, {reduce: true});
  await page.waitForSelector('.of-edge', {state: 'attached'});
  assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('.of-edge')).animationName), 'none');
  await page.close();
}

async function phone(browser) {
  const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}});
  await page.locator('.of-node').first().waitFor();
  const boxes = await page.locator('.of-node').evaluateAll(n => n.map(e => e.getBoundingClientRect().top));
  assert.ok(boxes[0] < boxes[1] && boxes[1] < boxes[2], 'pipelines stack');
  assert.equal(await page.locator('#of-lines').isVisible(), false);
  assert.match(await page.locator('[data-of-pl=camp] .of-onward').innerText(), /38 · 2d to Sales/);
  assert.match(await page.locator('[data-of-pl=camp] .of-in-chip').innerText(), /Ads 14/);
  await page.locator('[data-of-pl=camp] .of-chat').click();
  await page.locator('#of-rail-chat .thv').waitFor();
  const sheet = await page.locator('#of-rail').boundingBox();
  assert.ok(sheet.width >= 389 && sheet.height >= 843, `sheet ${sheet.width}x${sheet.height}`);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways scroll');
  await page.locator('[data-of-rail-hide]').click();
  assert.equal(await page.locator('#of-rail').isHidden(), true);
  assert.deepEqual(errors, []);
  await page.close();
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try { await desktop(browser); await reducedMotion(browser); await phone(browser); console.log('overview-factories: ok'); } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
