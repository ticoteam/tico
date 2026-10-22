// Settings > CEO Bot (ui/app/settings-ceo-bot.js): the owner sees the regular checks, on, and the three levels with their
// sentences, Passive current; picking another shows its sentence again and changes nothing until Confirm, and Cancel
// leaves it as it was. Turning the regular checks off saves at once. Only the owner has the tab: an admin or a member
// has none. The tab and headings use the bot's name. Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile, harnesses} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const LEVELS = [
  {level: 1, name: 'Passive', sentence: 'It keeps the regular checks running and otherwise acts only when you talk to it.', ready: true},
  {level: 2, name: 'Suggests actions', sentence: 'It also asks goal owners and suggests how your bots could recover a goal.', ready: true},
  {level: 3, name: 'Acts on its own', sentence: 'It also makes bot tasks itself. Not yet: it does what level 2 does.', ready: false},
];

async function open(browser, {role = 'owner', name = 'CEO Bot'} = {}) {
  const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
  const errors = [], posted = [];
  page.on('pageerror', e => errors.push(e.message));
  const state = {regular_checks: true, level: 1, level_changed_by: '', level_changed_by_name: '', level_changed_at: '', level_history: [], levels: LEVELS,
    name, daily_cap: 5, asked_today: {}};
  await page.route('**/*', route => {
    const req = route.request(), p = new URL(req.url()).pathname;
    const json = body => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(body)});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const config = {version: '0.1.0', app_name: 'Tico'};
    if (p === '/api/me') return json({id: role === 'owner' ? 'ana' : 'cara', role, name: 'Ana', email: 'ana@example.com', cloud: true, registered: true, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json([{name: 'goal-manager', display_name: name, status: 'active'}]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/models') return json({models: [], harnesses});
    if (p === '/api/v2/ceo-bot' && req.method() === 'GET') return json(state);
    if (p === '/api/v2/ceo-bot/level') {
      const body = req.postDataJSON();
      posted.push(body);
      const at = new Date().toISOString();
      state.level_history.unshift({level: body.level, was: state.level, by: 'human:ana', by_name: 'Ana', at});
      Object.assign(state, {level: body.level, level_changed_by: 'human:ana', level_changed_by_name: 'Ana', level_changed_at: at});
      return json(state);
    }
    if (p === '/api/v2/ceo-bot/checks') {
      const body = req.postDataJSON();
      posted.push({checks: body.on});
      state.regular_checks = body.on;
      return json(state);
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/settings');
  await page.locator('#settings-tabs').waitFor();
  return {page, errors, posted};
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    {
      const {page, errors, posted} = await open(browser);
      await page.locator('[data-settings-tab="ceo"]').click();
      const levels = page.locator('#set-ceo-bot [data-ceo-level]');
      await levels.first().waitFor();
      assert.equal(await levels.count(), 3);
      assert.match(await levels.nth(0).innerText(), /Passive[\s\S]*Now[\s\S]*acts only when you talk to it/);
      assert.equal(await levels.nth(0).getAttribute('aria-checked'), 'true');
      assert.match(await levels.nth(2).innerText(), /Not yet/);
      const panel = await page.locator('#set-ceo-bot').innerText();
      assert.match(panel, /Regular checks[\s\S]*How much your CEO Bot does[\s\S]*Passive, where every company starts/);
      assert.doesNotMatch(panel, /Goal Manager|level 1|switch/i);
      assert.equal(await page.locator('#set-ceo-bot [data-ceo-checks]').isChecked(), true);
      // Picking level 2 shows its sentence with a Confirm; Cancel changes nothing.
      await levels.nth(1).click();
      const confirm = page.locator('#set-ceo-bot .ceo-confirm');
      await confirm.waitFor();
      assert.match(await confirm.innerText(), /Change to Suggests actions\?[\s\S]*asks goal owners/);
      await confirm.locator('[data-ceo-cancel]').click();
      await confirm.waitFor({state: 'detached'});
      assert.deepEqual(posted, []);
      // Confirm saves it, and the change is listed with who.
      await levels.nth(1).click();
      await page.locator('#set-ceo-bot [data-ceo-confirm="2"]').click();
      await page.waitForFunction(() => document.querySelector('#set-ceo-bot [data-ceo-level="2"]')?.classList.contains('cur'));
      assert.deepEqual(posted, [{level: 2}]);
      assert.match(await page.locator('#set-ceo-bot').innerText(), /chosen by Ana/);
      // The regular checks go off at once, on the owner's click.
      await page.locator('#set-ceo-bot [data-ceo-checks]').uncheck();
      await page.waitForFunction(() => document.querySelector('#set-ceo-bot [data-ceo-checks]') && !document.querySelector('#set-ceo-bot [data-ceo-checks]').disabled
        && !document.querySelector('#set-ceo-bot [data-ceo-checks]').checked);
      assert.deepEqual(posted, [{level: 2}, {checks: false}]);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Named by the owner: the tab and the heading use that name.
      const {page, errors} = await open(browser, {name: 'Atlas'});
      assert.equal((await page.locator('[data-settings-tab="ceo"]').innerText()).trim(), 'Atlas');
      await page.locator('[data-settings-tab="ceo"]').click();
      await page.locator('#set-ceo-bot [data-ceo-level]').first().waitFor();
      assert.match(await page.locator('#set-ceo-bot').innerText(), /How much your Atlas does/);
      assert.deepEqual(errors, []);
      await page.close();
    }
    for (const opts of [{role: 'admin'}, {role: 'viewer'}]) {
      const {page, errors} = await open(browser, opts);
      assert.equal(await page.locator('[data-settings-tab="ceo"]').count(), 0, JSON.stringify(opts));
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('settings-ceo-bot: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
