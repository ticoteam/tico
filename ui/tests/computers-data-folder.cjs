// Settings > Computers: a Muse Code login whose turns can't save a session (readiness `data_problem`, Dev 329). The chip
// has a red dot and names the folder on hover; the pop-up names the folder on screen, offers no Sign in or Add key for
// it, and the bot's line says the folder problem, not "Signed out". Every request is intercepted.
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
    const at = ms => new Date(Date.now() + ms).toISOString();
    const folder = '/home/runner/.tico-host/state-box/musecode/data/muse/sessions';
    const problem = `Muse Code can't save sessions: the bot user can't write ${folder} (another user owns it or it is read-only)`;
    const box = {id: 'box', label: 'Team box', operator: 'ana', bots: ['ops'], platform: 'linux', version: '0.3.74', last_seen_at: at(-3000),
      readiness: {
        runtimes: {musecode: {installed: true, authenticated: 'failed', detail: problem, data_problem: problem}},
        bots: {ops: {ready: false, runtime: 'musecode', sign_in: 'failed', data_problem: problem, problems: [problem]}},
        harnesses: {}},
      profiles: [],
      // The server's line for the bot (backend/subscriptions.py): the folder, not a sign-in.
      bot_connections: [{bot: 'ops', profile: null, source: 'computer', runtime: 'musecode', harness: 'musecode', signed_in: null, problem}]};
    const me = {id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.com', cloud: true, registered: true};
    await page.route('**/*', route => {
      const req = route.request(), p = new URL(req.url()).pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (req.method() !== 'GET') return json({});
      const config = {version: '0.1.0', app_name: 'Tico'};
      if (p === '/api/me') return json({...me, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', status: 'active', operator: 'ana', can_manage: true,
        users: [], bot_owners: [], machine: {runner_id: 'box', label: 'Team box', operator: 'ana'}}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/operations') return json({machines: [box], services: []});
      if (p === '/api/v2/models') return json({models: [], harnesses: [
        {id: 'musecode', runtime: 'musecode', name: 'Muse Code', capabilities: {account: '', weekly_usage: '', browser_sign_in: true, api_keys: 'computer'}}]});
      if (p === '/api/v2/subscriptions') return json({profiles_by_computer: [], assignments: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('[data-settings-tab=devices]').click();
    const row = page.locator('#set-machines tr[data-computer-row="box"]');
    await row.waitFor();

    // The chip needs attention and its hover names the folder.
    const chip = row.locator('.machine-chip');
    assert.equal(await chip.count(), 1);
    assert.equal(await chip.locator('.machine-chip-dot').count(), 1, 'a red dot');
    assert.ok((await chip.getAttribute('title')).includes(folder), await chip.getAttribute('title'));

    // The pop-up names the folder on screen, with no sign-in or key offered for it.
    await row.locator('.machine-name').click();
    const dialog = page.locator('#set-machines dialog.computer-detail[open]');
    await dialog.waitFor();
    const line = dialog.locator('[data-connection="own:musecode"]');
    assert.equal(await line.locator('[data-data-problem=musecode]').innerText(), problem);
    assert.equal(await line.locator('.machine-chip-dot').count(), 1);
    assert.doesNotMatch(await line.innerText(), /Couldn't check|Signed out|Sign in|Add key/);
    assert.equal(await line.locator('button.machine-signin, [data-model-login]:not([data-model-logout])').count(), 0);
    // The bot's own line says the folder problem, not "Signed out".
    assert.equal(await dialog.locator('[data-detail-bot=ops] .pill').innerText(), problem);
    assert.doesNotMatch(await dialog.innerText(), /Signed out/);

    // The same computer login in a retained named home: accepted profile-rich reports select that home too.
    box.readiness.computer_profile = 'work';
    box.readiness.runtimes.musecode = {installed: true, authenticated: 'failed',
      profiles: {work: {installed: true, authenticated: 'failed', data_problem: problem}}};
    await page.evaluate(() => loadSettings());
    assert.equal(await line.locator('[data-data-problem=musecode]').innerText(), problem);
    assert.equal(await line.locator('.machine-chip-dot').count(), 1);
    assert.equal(await line.locator('[data-model-login], [data-model-key-add]').count(), 0);

    assert.deepEqual(errors, []);
    console.log('PASS: a Muse Code data folder its turns cannot write shows on the Computers page, naming the folder');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
