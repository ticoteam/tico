// A bot's Sites card: rows of host and exit, saved as one list. A computer's Exit for bots: the bots ticked, none is off.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block'});
    page.setDefaultTimeout(t(5000));
    const errors = [], writes = [];
    page.on('pageerror', error => errors.push(error.message));
    const bot = {host: 'keeper', status: 'active', state: 'active', can_chat: true, can_manage: true, operator: 'ana',
      my_access: {see: true, read: true, write: true}, schedules: [], revision: 1, thread_mode: 'personal', users: [],
      name: 'ops', slug: 'ops', display_name: 'ops', online: true, machine: null};
    let view = {sites: [{host: 'shop.acme.example', exit: 'computer:mac', exit_name: 'Home Mac', exit_takes_bot: true}],
                exits: [{exit: 'computer:mac', name: 'Home Mac'}]};
    const machines = [{id: 'mac', label: 'Home Mac', operator: 'ana', bots: [], platform: 'darwin', last_seen: new Date().toISOString(),
      readiness: {}, screen: 'off', screen_available: false, exit: {on: false, bots: []}, exit_available: true}];
    await page.route('**/*', route => {
      const request = route.request(), p = new URL(request.url()).pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true, registered: true, config: {version: '0.5.6', app_name: 'Tico'}});
      if (p === '/api/employees') return json([bot]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/issues') return json([]);
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], schedules: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/tasks') return json({tasks: []});
      if (p === '/api/v2/conversations') return json({conversations: []});
      if (p === '/api/v2/operations') return json({machines, services: []});
      if (p === '/api/v2/runners/mac/exit') {
        const body = JSON.parse(request.postData()); writes.push(body);
        machines[0].exit = {on: body.on, bots: body.on ? body.bots : []};
        return json({exit: machines[0].exit, exit_available: true});
      }
      if (p === '/api/v2/bots/ops/sites') {
        if (request.method() === 'PUT') {
          const body = JSON.parse(request.postData()); writes.push(body);
          view = {...view, sites: body.sites.map(s => ({...s, exit_name: s.exit === 'direct' ? '' : 'Home Mac', exit_takes_bot: true}))};
        }
        return json(view);
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p.endsWith('/files')) return json({files: []});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/bot/ops/more');
    await page.locator('[data-bot-detail-tab=settings]').click();
    await page.locator('#bot-sites [data-bot-site]').waitFor();
    assert.equal(await page.locator('#bot-sites-card').isVisible(), true);
    assert.equal(await page.locator('[data-site-host]').inputValue(), 'shop.acme.example');
    assert.deepEqual(await page.locator('[data-site-exit] option').allTextContents(), ['Direct', 'Home Mac']);
    assert.equal(await page.locator('[data-site-exit]').inputValue(), 'computer:mac');
    await page.locator('[data-site-add]').click();
    await page.locator('[data-bot-site]').nth(1).locator('[data-site-host]').fill('*.cdn.example');
    await page.locator('[data-bot-site]').nth(1).locator('[data-site-exit]').selectOption('direct');
    await page.locator('[data-site-add]').click();                                // a blank row is left out
    await page.locator('[data-sites-save]').click();
    await page.waitForFunction(() => document.querySelectorAll('#bot-sites [data-bot-site]').length === 2);
    assert.deepEqual(writes, [{sites: [{host: 'shop.acme.example', exit: 'computer:mac'}, {host: '*.cdn.example', exit: 'direct'}]}]);
    await page.locator('[data-bot-site]').first().locator('[data-site-remove]').click();
    await page.locator('[data-sites-save]').click();
    await page.waitForFunction(() => document.querySelectorAll('#bot-sites [data-bot-site]').length === 1);
    assert.deepEqual(writes.at(-1), {sites: [{host: '*.cdn.example', exit: 'direct'}]});

    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('[data-settings-tab=devices]').click();
    await page.locator('#set-machines .machine-name[data-computer-open=mac]').click();
    await page.locator('[data-machine-details="mac:exit"] summary').click();
    await page.locator('[data-exit-bot=mac][value=ops]').check();
    await page.waitForFunction(() => SETTINGS_DATA.machines[0].exit?.on === true);
    assert.deepEqual(writes.at(-1), {on: true, bots: ['ops']});
    await page.locator('[data-exit-bot=mac][value=ops]:enabled').uncheck();
    await page.waitForFunction(() => SETTINGS_DATA.machines[0].exit?.on === false);
    assert.deepEqual(writes.at(-1), {on: false, bots: []});
    assert.deepEqual(errors, []);
    console.log('PASS: a bot\'s site list saves host and exit rows; blank rows are left out; Exit for bots saves the bots ticked');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
