// Synthetic Settings click-through. Server permissions are covered by test_org_turns.py;
// spec 15 still requires installed/live acceptance after publication.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    for (const width of [1280, 390]) {
      const page = await browser.newPage({viewport: {width, height: 900}, serviceWorkers: 'block'});
      const errors = [], writes = [];
      page.on('pageerror', error => errors.push(error.message));
      const config = {environment_id: 'fixture', company_name: 'Fixture company', app_name: 'Tico',
        assistant_name: 'Helper', assistant_bot: 'coo', local: false, onboarding_needed: false, providers_configured: true};
      let levels = {warning_per_person: 40, stop_per_person: 100, people: 4, warning: 160, stop: 400, in_use: 12};
      await page.route('**/*', route => {
        const request = route.request(), p = new URL(request.url()).pathname;
        const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        const module = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
        if (module && fs.existsSync(uiFile(module[1]))) return route.fulfill({
          contentType: module[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(module[1]), 'utf8')});
        if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
        if (p === '/api/me') return json({id: 'ana', role: 'owner', cloud: true, email: 'ana@acme.example', config});
        if (p === '/api/v2/config') return json(config);
        if (p === '/api/employees' || p === '/api/issues') return json([]);
        if (p === '/api/humans') return json({people: []});
        if (p === '/api/status') return json({cloud: true, keeper_alive: true, health_issues: [], active: [], recent_runs: []});
        if (p === '/api/v2/operations') return json({machines: [], services: [], issues: []});
        if (p === '/api/v2/models') return json({models: [], harnesses: []});
        if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
        if (p === '/api/v2/settings/turn-levels') {
          if (request.method() === 'POST') {
            const body = request.postDataJSON(); writes.push(body);
            levels = {...levels, ...body, warning: body.warning_per_person * 4, stop: body.stop_per_person * 4};
          }
          return json(levels);
        }
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json(unrouted(p));
      });
      await page.goto('https://tico-ui.test/#/settings');
      const warning = page.locator('#turn-levels-form [name=warning_per_person]');
      const stop = page.locator('#turn-levels-form [name=stop_per_person]');
      await warning.waitFor();
      assert.equal(await warning.inputValue(), '40');
      assert.equal(await stop.inputValue(), '100');
      assert.match(await page.locator('#set-turn-levels').innerText(), /warning at 160, stop at 400/);
      await warning.fill('45'); await stop.fill('110');
      await page.evaluate(async () => { await loadSettings(); await renderSettingsTurnLevels(); });
      assert.equal(await warning.inputValue(), '45', 'polling keeps an edited warning');
      assert.equal(await stop.inputValue(), '110', 'polling keeps an edited stop');
      await page.getByRole('button', {name: 'Save turn levels', exact: true}).click();
      await page.waitForFunction(() => document.querySelector('[data-turn-levels-status]')?.textContent.includes('Saved. Warning at 180; stop at 440'));
      assert.deepEqual(writes, [{warning_per_person: 45, stop_per_person: 110}]);
      await warning.fill('111');
      await page.getByRole('button', {name: 'Save turn levels', exact: true}).click();
      assert.match(await page.locator('[data-turn-levels-status]').innerText(), /must not exceed/);
      assert.equal(writes.length, 1);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      // Admin to member is a same-role change: it must rebuild the Settings shell too.
      await page.evaluate(() => { S.me = {...S.me, role: 'human', bot_admin: true}; pageSettings(); });
      await warning.waitFor();
      await page.evaluate(() => { S.me = {...S.me, bot_admin: false}; pageSettings(); });
      assert.equal(await page.locator('#set-turn-levels').count(), 0);
      assert.deepEqual(errors, []);
      console.log(`PASS company turn settings: ${width}px defaults, save, validation, polling and admin removal`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
