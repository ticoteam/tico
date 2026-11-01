// Settings > Computers, a computer's pop-up > Versions and updates > Installation and updates: the owner's On box per model CLI. What the computer's bots run on, their fallback
// included (`bot_runtimes`), is checked and locked; any other one turns on and off with a POST and the page reloads; a
// refused change puts the box back. An older server sends no bot_runtimes: its bots' own runtimes stay locked.
// Every request is intercepted.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const chip = (name, runtime, installed) => ({name, runtime, installed, version: installed ? '1.0.0' : '', managed: true, state: 'idle',
      wanted: installed, pinned: false, update_available: false, detail: ''});
    const computer = {id: 'computer-1', label: 'Build server', operator: 'ana', bots: ['helper'], platform: 'linux',
      last_seen_at: new Date().toISOString(), needed_runtimes: ['codex'], bot_runtimes: ['codex', 'gemini'], harness_choices: {},
      readiness: {runtimes: {codex: {installed: true, authenticated: 'ready'}},
        harnesses: {codex: chip('Codex', 'codex', true), antigravity: chip('Antigravity', 'gemini', true), grok: chip('Grok Build', 'grok', false)}}};
    const writes = [];
    let refuse = false;
    await page.route('**/*', route => {
      const req = route.request(), p = new URL(req.url()).pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const config = {version: '0.1.0', app_name: 'Tico'};
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.com', cloud: true, registered: true, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json([{name: 'helper', display_name: 'Helper', status: 'active', operator: 'ana', can_manage: true, users: [], bot_owners: []}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/operations') return json({machines: [computer], services: []});
      if (p === '/api/v2/computers/computer-1/harness-choices') {
        const body = req.postDataJSON(); writes.push(body);
        if (refuse) return json({error: {code: 'needed', detail: 'A bot on this computer runs on this tool, so it stays on'}}, 409);
        computer.harness_choices = {...computer.harness_choices, [body.harness]: body.enabled};
        return json({runner_id: 'computer-1', ...body});
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('[data-settings-tab=devices]').click();
    await page.locator('#set-machines .machine-name[data-computer-open=computer-1]').click();
    await page.locator('#set-machines details[data-machine-details] > summary').click();
    const box = id => page.locator(`#set-machines [data-harness-choice="${id}"]`);
    const state = async id => [await box(id).isChecked(), await box(id).isDisabled()];
    // The bot's harness and its fallback: on and locked. Grok: off, the owner's to turn on.
    assert.deepEqual(await state('codex'), [true, true]);
    assert.deepEqual(await state('antigravity'), [true, true]);
    assert.deepEqual(await state('grok'), [false, false]);
    await box('grok').check();
    await page.waitForFunction(() => SETTINGS_DATA.machines[0].harness_choices.grok === true);
    await page.waitForFunction(() => !document.querySelector('#set-machines [data-harness-choice="grok"]').disabled);
    assert.deepEqual(await state('grok'), [true, false]);
    await box('grok').uncheck();
    await page.waitForFunction(() => SETTINGS_DATA.machines[0].harness_choices.grok === false);
    await page.waitForFunction(() => !document.querySelector('#set-machines [data-harness-choice="grok"]').disabled);
    assert.deepEqual(await state('grok'), [false, false]);
    assert.deepEqual(writes, [{harness: 'grok', enabled: true}, {harness: 'grok', enabled: false}]);
    // A refused change puts the box back and leaves it usable.
    refuse = true;
    // click, not check: the refusal can put the box back before check() reads it, which check() calls a failure.
    const refused = page.waitForResponse(r => r.url().endsWith('/harness-choices') && r.status() === 409);
    await box('grok').click();
    await refused;
    await page.waitForFunction(() => { const el = document.querySelector('#set-machines [data-harness-choice="grok"]'); return !el.checked && !el.disabled; });
    assert.equal(writes.length, 3);
    assert.deepEqual(writes[2], {harness: 'grok', enabled: true});
    // An older server: no bot_runtimes, so only what its bots run on (needed_runtimes) is locked.
    delete computer.bot_runtimes;
    await page.evaluate(() => loadSettings());
    await page.locator('#set-machines details[data-machine-details]').evaluate(el => { el.open = true; });
    assert.deepEqual(await state('codex'), [true, true]);
    assert.deepEqual(await state('antigravity'), [true, false]);
    assert.deepEqual(errors, []);
    console.log('PASS: Model CLIs toggle, bot and fallback locked, on/off, refused, older server');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
