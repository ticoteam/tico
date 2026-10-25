// The weekly market briefing line on the Market page (ui/market-briefing.js) against a stand-in for
// GET /api/v2/market/briefings/latest (fixtures only, the Acme example): the line shows the week, the title, the item
// count and a chip for each bet the briefing touches; Read opens the body in a panel; with no briefing, or a failing
// call, the Market page is unchanged; and at phone width nothing scrolls sideways.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const uiDir = path.join(__dirname, '..');

const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};
const briefing = {
  id: 'br-1', week: '2026-W44', title: 'Northwind raised its price', created_by: 'bot:librarian',
  body: '# Week 44\n\nNorthwind moved Pro from 29 to 39 a seat.\n\n- Source: northwind.example',
  items: [{text: 'Northwind moved Pro from 29 to 39 a seat.', kind: 'change', entity_ids: ['company/northwind']},
    {text: 'Landlords ask about flat pricing.', kind: 'theme', entity_ids: []}],
  bets: ['b-price', 'b-offices'],
  touched: [{id: 'b-price', n: 1, statement: 'Price at half the market and still make money.'},
    {id: 'b-offices', n: 2, statement: 'Sell to small offices through property managers.'}],
};
const self = {id: 'company/self', name: 'Acme', type: 'company', status: 'active', summary: 'Acme.'};

async function open(browser, {latest = briefing, fail = false, viewport = DESKTOP} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events') return;
    if (p === '/api/v2/config') return json({});
    if (p === '/api/employees') return json([]);
    if (p === '/api/me') return json({id: 'ana', name: 'Ana Silva', email: 'x@example.test', role: 'owner', company_role: 'owner', cloud: true, config: {}});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Silva', org_parent: ''}], org_groups: []});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/market/briefings/latest') return fail ? json({error: {code: 'boom', detail: 'no'}}, 500) : json({briefing: latest});
    if (p === '/api/v2/market/graph') return json({entities: [self], edges: []});
    if (p === '/api/v2/market/entities') return json({entities: [self], total: 1});
    if (p === '/api/company-docs') return json({documents: []});
    if (p === '/api/v2/librarian/setup') return json({market: null});
    if (p === '/api/v2/strategy') return json({draft: null, current: null, bets: [], may_edit: true, goals_by_bet: {}, health_by_bet: {}});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/#/strategy/market');
  await page.waitForSelector('#market-shell .market-list');
  return {page, errors};
}

(async () => {
  const browser = await chromium.launch();
  try {
    // The line: week, title, count, one chip per touched bet; it sits between the Strategy tabs and the market.
    {
      const {page, errors} = await open(browser);
      await page.waitForSelector('#market-briefing');
      const card = page.locator('#market-briefing');
      assert.match(await card.innerText(), /2026-W44/);
      assert.match(await card.innerText(), /Northwind raised its price/);
      assert.match(await card.innerText(), /2 items/);
      assert.deepEqual(await card.locator('.brief-bets a').allInnerTexts(), ['Bet 1', 'Bet 2']);
      assert.equal(await page.evaluate(() => document.querySelector('#market-briefing').nextElementSibling.id), 'market-shell');
      // Read opens the body, with the bets it touches, and closes again.
      await card.getByRole('button', {name: 'Read'}).click();
      const panel = page.locator('dialog.brief-panel');
      await panel.waitFor();
      assert.match(await panel.innerText(), /Northwind moved Pro from 29 to 39 a seat/);
      assert.match(await panel.innerText(), /Sell to small offices/);
      await panel.getByRole('button', {name: 'Close'}).click();
      await panel.waitFor({state: 'detached'});
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A briefing that touches no bet shows no chips; none at all, or a failing call, shows no line and the market opens.
    {
      const {page} = await open(browser, {latest: {...briefing, bets: [], touched: [], items: []}});
      await page.waitForSelector('#market-briefing');
      assert.equal(await page.locator('#market-briefing .brief-bets').count(), 0);
      await page.close();
    }
    for (const options of [{latest: null}, {fail: true}]) {
      const {page, errors} = await open(browser, options);
      await page.waitForTimeout(300);
      assert.equal(await page.locator('#market-briefing').count(), 0);
      assert.equal(await page.locator('#market-shell .market-list').count(), 1);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // Phone: the line wraps and nothing scrolls sideways.
    {
      const {page} = await open(browser, {viewport: PHONE});
      await page.waitForSelector('#market-briefing');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth
        && document.querySelector('#market-briefing').scrollWidth <= document.querySelector('#market-briefing').clientWidth), true);
      await page.close();
    }
    console.log('market-briefing: ok');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
