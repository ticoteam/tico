// Paused badge acceptance: both entry points use the existing lifecycle state and navigate to the bot.
// Synthetic offline fixtures only; no live bot is paused or resumed.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}, serviceWorkers: 'block', reducedMotion: 'reduce'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    
    const opus = 'claude-opus-5-5', sonnet = 'claude-sonnet-5-5', haiku = 'claude-haiku-5-5';
    const model = id => ({id, label: id === opus ? 'Opus 5.5' : id === sonnet ? 'Sonnet 5.5' : 'Haiku 5.5', runtime: 'claude', provider: 'anthropic',
      efforts: ['low', 'high'], default_effort: 'high', harnesses: ['claude']});
    const choice = id => ({model: id, harness: 'claude', reasoning_effort: 'high'});
    const bot = (name, own, task) => ({name, display_name: name[0].toUpperCase() + name.slice(1), role: 'planner', team: 'research', description: 'Drafts forecasts',
      status: 'active', owner_id: 'ana', can_manage: true, users: [], bot_owners: [], revision: 4, ...choice(own), task_model: task ? choice(task) : null,
      machine: {runner_id: 'r1', label: 'Box', owner_id: 'ana', generation: 7}});
    const bots = ['paused', 'active', 'planned', 'quarantined', 'archived'].map(status => ({...bot(status, opus, null), status, org_parent: 'p:ana', my_access: {see: true, read: true, write: true}}));
    await page.route('**/*', route => {
      const p = new URL(route.request().url()).pathname;
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p === '/vendor/fonts/material-symbols-outlined.woff2') return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile('vendor/fonts/material-symbols-outlined.woff2'))});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', cloud: true, registered: true});
      if (p === '/api/v2/config') return json({version: 'dev', local: true});
      if (p === '/api/employees') return json(bots);
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], teams: {research: {label: 'Research'}}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: []});
      // A turn can still be finishing after the bot is paused: lifecycle state wins.
      if (p === '/api/v2/status') return json({bots: [{bot: 'paused', state: 'running'}, {bot: 'quarantined', state: 'quarantined'}]});
      if (p === '/api/v2/models') return json({models: [model(opus)], harnesses: [{id: 'claude', label: 'Claude Code', runtime: 'claude'}]});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/settings');
    await page.waitForFunction(() => typeof S !== 'undefined' && S.emps?.length === 5);
    await page.evaluate(() => settingsShow('bots'));
    const badge = page.locator('[data-settings-bot=paused] .bot-paused-badge');
    const node = page.locator('#tree a[data-org="b:paused"]');
    await badge.waitFor(); await node.waitFor();
    assert.equal(await badge.innerText(), 'Paused');
    assert.equal(await badge.locator('svg[aria-hidden=true] path').count(), 1);
    assert.equal(await node.locator('.bot-paused-badge').innerText(), 'Paused');
    assert.equal(await node.locator('.bot-paused-badge svg').count(), 1);
    assert.ok(await node.evaluate(e => Number(getComputedStyle(e).opacity) < 1), 'whole node is dimmed');
    for (const status of ['active', 'planned', 'quarantined', 'archived']) {
      assert.equal(await page.locator(`[data-settings-bot=${status}] .bot-paused-badge`).count(), 0, `${status} is not Paused`);
    }
    assert.match(await page.locator('[data-settings-bot=planned]').innerText(), /Setting up/);
    assert.match(await page.locator('[data-settings-bot=quarantined]').innerText(), /quarantined/);
    assert.match(await page.locator('[data-settings-bot=archived]').innerText(), /archived/);
    if (process.env.TICO_UI_RECEIPT_DIR) {
      fs.mkdirSync(process.env.TICO_UI_RECEIPT_DIR, {recursive: true});
      await page.screenshot({path: path.join(process.env.TICO_UI_RECEIPT_DIR, 'paused-badges-desktop.png'), fullPage: true});
    }
    await badge.click();
    await page.waitForURL('**/#/bot/paused');
    await page.locator('.bot-ident').waitFor();
    await page.evaluate(() => { S.route = '#/settings'; location.hash = '/settings'; });
    await page.locator('#settings-tabs').waitFor();
    await node.locator('.bot-paused-badge').click();
    await page.waitForURL('**/#/bot/paused');
    await page.locator('.bot-ident').waitFor();
    await page.setViewportSize({width: 390, height: 844});
    await page.evaluate(() => { location.hash = '/settings'; });
    await page.locator('#settings-tabs').waitFor();
    await page.evaluate(() => settingsShow('bots'));
    await badge.scrollIntoViewIfNeeded();
    assert.equal(await badge.isVisible(), true);
    await badge.focus();
    await page.keyboard.press('Enter');
    await page.waitForURL('**/#/bot/paused');
    await page.locator('.bot-ident').waitFor();
    // A roster refresh removes both marks and restores full node opacity on unpause.
    bots[0].status = 'active';
    await page.evaluate(async () => { S.emps = namedRoster(await get('/employees')); renderTree(); });
    assert.equal(await node.locator('.bot-paused-badge').count(), 0);
    assert.equal(await node.evaluate(e => getComputedStyle(e).opacity), '1');
    await page.evaluate(() => { location.hash = '/settings'; });
    await page.locator('#settings-tabs').waitFor();
    await page.evaluate(() => settingsShow('bots'));
    assert.equal(await badge.count(), 0);
    assert.deepEqual(errors, []);
    console.log('PASS paused badges: list, chart, icon, dimming, navigation, other states and unpause');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
