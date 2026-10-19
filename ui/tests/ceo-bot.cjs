// The CEO Bot switch (backend/ceo_bot.py, `ceo_bot` in the config GET /api/me and /v2/config send): on, the Goal Manager is on the org chart
// by its display name, the CEO Bot, with the bots the server hangs under it, and the Goals page calls it by that name;
// off, it stays apart and is the Goal Manager, as ui/tests/goals.cjs checks too. Fixtures only, no network.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const bot = (name, display_name, org_parent, over) => ({name, display_name, org_parent, host: 'keeper', status: 'active', can_chat: true, ...over});

async function open(browser, on) {
  const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  // What the server sends either way: with the switch on it has renamed the bot and hung the Heads under it.
  const bots = [
    bot('coo', 'Assistant', 'p:ana'), bot('botops', 'BotOps', 'b:coo'), bot('librarian', 'Librarian', 'p:ana'),
    bot('goal-manager', on ? 'CEO Bot' : 'Goal Manager', 'p:ana', {reports_to: ''}),
    bot('cmo', 'Head of Marketing', on ? 'b:goal-manager' : 'p:ana', {reports_to: ''}),
    bot('seo', 'SEO', 'b:cmo', {reports_to: 'cmo'}),
    bot('scribe', 'Scribe', on ? 'b:goal-manager' : 'p:ana', {reports_to: ''}),
  ];
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = body => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui) { const file = uiFile(ui[1]); if (fs.existsSync(file)) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')}); }
    if (p === '/api/v2/events') return;
    if (p === '/api/v2/config') return json({ceo_bot: on});
    if (p === '/api/employees') return json(bots);
    if (p === '/api/me') return json({id: 'ana', name: 'Ana Silva', email: 'ana@example.test', role: 'owner', mover: true, cloud: true, config: {ceo_bot: on}});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Silva', org_parent: ''}]});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/goals/tree') return json({goals: [], owners: {}, other_kpis: [], proposals: []});
    if (p === '/api/v2/goals/needs-you') return json({actor: 'human:ana', items: []});
    if (p === '/api/v2/bots/goal-manager/routines') return json({routines: []});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/#/goals');
  await page.locator('#goal-tree .gt-row').first().waitFor();
  await page.locator('#goal-manager-panel h2').waitFor();
  return {page, errors};
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    {
      const {page, errors} = await open(browser, true);
      // On the chart, by its name, under the owner, with the Head and the bot with no manager under it.
      const ceo = page.locator('#tree a.node[data-org="b:goal-manager"]');
      assert.equal(await ceo.count(), 1, 'the CEO Bot is on the chart');
      assert.equal(await ceo.getAttribute('data-helper'), null, 'not set apart with the built-ins');
      assert.equal((await ceo.locator('.nm').innerText()).trim(), 'CEO Bot');
      const under = await page.locator('#tree a.node[data-org="b:goal-manager"]').locator('xpath=ancestor::li[1]')
        .locator('ul a.node').evaluateAll(els => els.map(el => el.dataset.org));
      assert.deepEqual(under.sort(), ['b:cmo', 'b:scribe', 'b:seo']);
      assert.equal(await page.locator('#tree a.node[data-org="b:coo"]').count(), 0, 'the Assistant stays off the chart');
      // The Goals page tree nests it the same way, and its panel and labels say CEO Bot.
      const lines = await page.locator('#goal-tree > li').evaluateAll(els => els.map(el => `${el.dataset.owner}@${el.style.getPropertyValue('--d')}`));
      assert.deepEqual(lines.filter(l => l.startsWith('bot:')), ['bot:goal-manager@1', 'bot:cmo@2', 'bot:seo@3', 'bot:scribe@2']);
      assert.equal(await page.locator('#goal-manager-panel h2').innerText(), 'CEO Bot');
      assert.equal(await page.locator('#goal-manager-panel').getAttribute('aria-label'), 'CEO Bot');
      assert.doesNotMatch(await page.locator('#main').innerText(), /Goal Manager/);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      const {page, errors} = await open(browser, false);
      // Off: the Goal Manager keeps its name and stays apart; its reports hang under the owner as before.
      assert.equal(await page.locator('#tree a.node[data-org="b:goal-manager"]').count(), 0);
      assert.equal(await page.locator('#goal-manager-panel h2').innerText(), 'Goal Manager');
      const lines = await page.locator('#goal-tree > li').evaluateAll(els => els.map(el => `${el.dataset.owner}@${el.style.getPropertyValue('--d')}`));
      assert.deepEqual(lines.filter(l => l.startsWith('bot:')), ['bot:cmo@1', 'bot:seo@2', 'bot:scribe@1']);
      assert.doesNotMatch(await page.locator('#main').innerText(), /CEO Bot/);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('ceo-bot: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
