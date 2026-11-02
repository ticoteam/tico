// A bot's cloud machine in Settings (ui/app/cloud-machines.js, backend/cloud_machines.py):
//  - on Tico Cloud the Computer select offers Shared and Own cloud machine, and never a cloud machine's computer;
//  - a bot on its own machine has Size and Always on, held until Save bot, which posts each to /cloud-machine after
//    the definition; HQ's sizes replace the plain names once read;
//  - a bot on the shared machine shows the company's Size and Always on, posted to /cloud-machines/shared;
//  - picking a cloud machine for a bot on a computer is no move: Save posts mode cloud with the placement and starts
//    no transition;
//  - off cloud (`cloud_machine` null) there is no such option and no Size row.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const catalog = {models: [{id: 'opus-5-5', label: 'opus 5 5', runtime: 'claude', provider: 'anthropic', efforts: ['high'],
      default_effort: 'high', harnesses: ['claude']}], harnesses: [{id: 'claude', label: 'Claude Code (Anthropic)', runtime: 'claude'}],
      default: {runtime: 'claude', model: 'opus-5-5'}};
    const own = {mode: 'cloud', placement: 'own', size: 'default', always_on: false, state: 'stopped', machine_size: 'default',
      cost_month_usd: 0.42, starts_month: 3, runner_id: 'r9', problem: ''};
    const bot = (name, extra) => ({name, display_name: name[0].toUpperCase() + name.slice(1), description: '', status: 'active',
      owner_id: 'ana', repo: 'bot-' + name, can_manage: true, users: [], bot_owners: [], revision: 4, model: 'opus-5-5',
      harness: 'claude', reasoning_effort: 'high', thread_mode: 'personal', bot_contact: 'open', ...extra});
    const helper = bot('helper', {machine: {runner_id: 'r9', label: 'Helper (cloud)', owner_id: 'ana', generation: 2}, cloud_machine: own});
    const writer = bot('writer', {machine: {runner_id: 'r1', label: 'Box', owner_id: 'ana', generation: 7},
      cloud_machine: {...own, mode: 'computer', state: 'none', runner_id: null}});
    const scout = bot('scout', {machine: {runner_id: 'r8', label: 'Shared cloud machine', owner_id: 'ana', generation: 1},
      cloud_machine: {...own, placement: 'shared', runner_id: 'r8', starts_month: 1}});
    let bots = [helper, writer, scout], calls = [];
    await page.route('**/*', route => {
      const req = route.request(), p = new URL(req.url()).pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const write = p.match(/^\/api\/v2\/bots\/([^/]+)\/([^/]+)$/);
      if (req.method() === 'POST' && write) {
        const body = req.postDataJSON(), b = bots.find(row => row.name === write[1]);
        calls.push({bot: write[1], what: write[2], body});
        if (write[2] === 'definition') return json({name: b.name, revision: ++b.revision});
        if (write[2] === 'cloud-machine') { Object.assign(b.cloud_machine, body); return json({cloud_machine: b.cloud_machine}); }
        if (write[2] === 'transitions') return json({id: 'tr-1', bot: b.name, kind: 'machine', state: 'preparing'});
      }
      if (req.method() === 'POST' && p === '/api/v2/cloud-machines/shared') {
        calls.push({bot: null, what: 'shared', body: req.postDataJSON()});
        return json({size: 'default', always_on: false, bots: ['scout']});
      }
      if (p === '/api/v2/cloud-machines') return json({offered: true, caps: {}, sizes: [['small', 1], ['default', 2], ['large', 4], ['xlarge', 8]]
        .map(([name, cpu]) => ({name, cpu, memory_gb: cpu * 2, usd_per_hour: cpu * 0.05, start_usd: 0.04, idle_stop_s: 300}))});
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.com', cloud: true, registered: true});
      if (p === '/api/v2/config') return json({version: '0.1.0', app_name: 'Tico', local: true});
      if (p === '/api/employees') return json(bots);
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/operations') return json({services: [], machines: [
        {id: 'r1', label: 'Box', owner_id: 'ana', bots: ['writer']},
        {id: 'r9', label: 'Helper (cloud)', owner_id: 'ana', bots: ['helper'], cloud_machine: {bot: 'helper', shared: false, size: 'default', state: 'stopped'}},
        {id: 'r8', label: 'Shared cloud machine', owner_id: 'ana', bots: ['scout'], cloud_machine: {bot: null, shared: true, size: 'default', state: 'stopped'}}]});
      if (p === '/api/v2/models') return json({...catalog, enabled_providers: ['anthropic']});
      if (p === '/api/v2/providers') return json({enabled: ['anthropic'], default: catalog.default, revision: 1, providers: [{id: 'anthropic', label: 'Anthropic'}]});
      if (p === '/api/v2/usage/limits') return json({bots: {}});
      if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
      if (p === '/api/v2/settings/transitions/tr-1') return json({id: 'tr-1', bot: 'writer', kind: 'machine', state: 'preparing', progress: {prepared: 0, total: 1}});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const editor = page.locator('#bot-editor');
    // The bot page draws its editor once its settings load (bot-details.js botSettingsMount); a choice made before
    // that would be drawn over, so each opening waits for that drawing.
    const open = async slug => {
      const drawn = await page.evaluate(() => {
        if (!window.editorDrawn) {
          const draw = window.settingsEditBot;
          window.editorDrawn = 0;
          window.settingsEditBot = (...args) => { const out = draw(...args); if (args[1]) window.editorDrawn++; return out; };
        }
        return window.editorDrawn;
      });
      // Already on its page, nothing draws it again: draw it here.
      await page.evaluate(name => location.hash === `#/bot/${name}/settings` ? settingsEditBot(name, true) : settingsEditBot(name), slug);
      await page.waitForFunction(count => window.editorDrawn > count && BOT?.slug && !!document.querySelector('#bot-editor [data-bot-status]'), drawn);
      await editor.locator('[data-bot-machine]').waitFor({state: 'attached'});
      calls = [];
    };
    const save = async () => {
      await editor.locator('button[type=submit]').click();
      await page.waitForFunction(() => document.querySelector('#bot-editor [data-bot-status]')?.textContent === 'Saved');
    };
    await page.goto('https://tico-ui.test/#/settings');
    await page.waitForFunction(() => typeof S !== 'undefined' && S.emps?.some(e => e.name === 'helper') && SETTINGS_DATA.models?.length);

    // Shared and Own cloud machine are choices; a cloud machine's computer never is.
    await open('helper');
    const select = editor.locator('[data-bot-machine]');
    assert.equal(await select.inputValue(), 'cloud-own');
    assert.deepEqual(await select.locator('option').evaluateAll(options => options.map(o => o.value)), ['cloud-shared', 'cloud-own', 'r1']);
    // HQ's sizes come in with their prices; Size and Always on wait for Save bot.
    await page.waitForFunction(() => document.querySelector('#bot-editor [data-bot-cloud-size] option')?.textContent.includes('CPU'));
    await editor.locator('[data-bot-cloud-size]').selectOption('large');
    await editor.locator('[data-bot-cloud-always]').check();
    assert.deepEqual(calls, [], 'nothing is saved before Save bot');
    await save();
    assert.deepEqual(calls.map(c => [c.what, c.body]), [['definition', calls[0].body], ['cloud-machine', {size: 'large'}],
      ['cloud-machine', {always_on: true}]]);

    // A bot on the shared machine: its Size and Always on are the company's, posted to the shared machine.
    await open('scout');
    assert.equal(await editor.locator('[data-bot-machine]').inputValue(), 'cloud-shared');
    await page.waitForFunction(() => document.querySelector('#bot-editor [data-bot-cloud-size] option')?.textContent.includes('CPU'));
    await editor.locator('[data-bot-cloud-size]').selectOption('small');
    await save();
    assert.deepEqual(calls.map(c => [c.what, c.body]), [['definition', calls[0].body], ['shared', {size: 'small'}]]);

    // A bot on a computer joins the shared machine: mode cloud, placement shared, no transition.
    await open('writer');
    assert.equal(await editor.locator('[data-bot-cloud-size]').count(), 0, 'no Size row while it runs on a computer');
    await editor.locator('[data-bot-machine]').selectOption('cloud-shared');
    await save();
    assert.deepEqual(calls.map(c => c.what), ['definition', 'cloud-machine']);
    assert.deepEqual(calls[1].body, {mode: 'cloud', placement: 'shared'});

    // Off cloud: no cloud machine choice, no Size row.
    bots = bots.map(row => ({...row, cloud_machine: null}));
    await page.evaluate(() => loadSettings());
    await page.waitForFunction(() => S.emps.every(e => e.cloud_machine === null));
    await open('writer');
    assert.equal(await editor.locator('[data-bot-machine] option[value^=cloud]').count(), 0);
    assert.equal(await editor.locator('[data-bot-cloud-size]').count(), 0);
    assert.deepEqual(errors, []);
    console.log('cloud machines ok');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
