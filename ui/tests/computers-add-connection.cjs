// Settings > Computers, part 3: Add connection (the tool, Subscription or API key, then the provider's sign-in of the
// computer's one login for that tool in the same pop-up; the chip appears once the computer reports, with no restart).
// Each computer has one login per AI tool (#1259): named subscriptions a runner still reports are not shown, and a
// tool already signed in has no second sign-in. Every request is intercepted.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const SHOTS = process.env.SHOTS;
const shot = async (locator, name) => { if (SHOTS) await locator.screenshot({path: `${SHOTS}/${name}.png`}); };
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const now = Date.now(), at = ms => new Date(now + ms).toISOString();
    const tool = (name, runtime) => ({name, runtime, installed: true, version: '1.0', managed: true, state: 'idle', wanted: true, pinned: false, update_available: false, detail: ''});
    const box = {id: 'box', label: 'Team box', owner_id: 'ana', bots: ['writer'], platform: 'linux', version: '0.3.53',
      last_seen_at: at(-3000), readiness_at: at(-60000), needed_runtimes: ['codex'], bot_runtimes: ['codex'],
      readiness: {runtimes: {codex: {installed: true, authenticated: 'missing'}, claude: {installed: true, authenticated: 'missing'},
        grok: {installed: false}},
        harnesses: {codex: tool('Codex', 'codex'), claude: tool('Claude Code', 'claude')}},
      // Named subscriptions from before one login per computer: still reported, never shown.
      profiles: [{name: 'acme', id: 'p-acme', display_name: 'Acme Pro', runtimes: {codex: {signed_in: true}}}],
      bot_connections: [{bot: 'writer', profile: null, source: 'computer', runtime: 'codex', harness: 'codex', signed_in: false, problem: ''}]};
    const me = {id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.com', cloud: true, registered: true};
    const writes = [];
    let login = null;
    await page.route('**/*', route => {
      const req = route.request(), p = new URL(req.url()).pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (req.method() !== 'GET') {
        const body = req.postDataJSON?.();
        writes.push({p, body});
        if (p === '/api/v2/computers/box/logins') {
          login = {id: 'l1', kind: 'sign_in', runner_id: 'box', runtime: body.runtime, profile: body.profile || '', state: 'waiting',
            url: 'https://auth.openai.com/codex/device', code: 'ABCD-1234', lines: [], accepts_code: false, code_sent: false,
            created_at: new Date().toISOString(), updated_at: new Date().toISOString()};
          return json(login);
        }
        return json({});
      }
      if (p === '/api/v2/computers/box/logins/l1') return json(login);
      const config = {version: '0.1.0', app_name: 'Tico'};
      if (p === '/api/me') return json({...me, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json([{name: 'writer', display_name: 'Writer', status: 'active', owner_id: 'ana', can_manage: true,
        users: [], bot_owners: [], machine: {runner_id: 'box', label: 'Team box', owner_id: 'ana'}}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/operations') return json({machines: [box], services: []});
      if (p === '/api/v2/models') return json({models: [], harnesses: [
        {id: 'claude', runtime: 'claude', name: 'Claude Code', capabilities: {account: 'status-json', weekly_usage: 'turns', browser_sign_in: true, sign_out: true}},
        {id: 'codex', runtime: 'codex', name: 'Codex', capabilities: {account: 'app-server', weekly_usage: 'refresh', browser_sign_in: true, sign_out: true}},
        {id: 'grok', runtime: 'grok', name: 'Grok Build', capabilities: {account: '', weekly_usage: 'manual'}}]});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('[data-settings-tab=devices]').click();
    const row = page.locator('#set-machines tr[data-computer-row="box"]');
    await row.waitFor();
    const detail = page.locator('#set-machines dialog.computer-detail[open]');
    const openDetail = async () => { await row.locator('.machine-name').click(); await detail.waitFor(); };
    await openDetail();

    // The computer's one Codex login, and no line for the named subscription it still reports.
    assert.deepEqual(await detail.locator('[data-connection]').evaluateAll(list => list.map(c => c.dataset.connection)), ['own:codex']);
    assert.doesNotMatch(await detail.innerText(), /Acme|Rename|Remove/);

    // 15. Add connection → Codex → Subscription → the computer's login signs in, all in one pop-up.
    await detail.locator('[data-add-connection=box]').click();
    const add = page.locator('dialog.computer-add-dialog[open]');
    await add.waitFor();
    await add.evaluate(d => { d.dataset.marker = 'same'; });
    assert.deepEqual(await add.locator('[data-add-tool]').allInnerTexts(), ['Claude Code', 'Codex'], 'only the tools installed here');
    assert.equal(await add.locator('[data-add-install]').innerText(), 'Install another tool');
    await shot(add, 'add-1-tool');
    await add.locator('[data-add-tool=codex]').click();
    await shot(add, 'add-2-kind');
    assert.match(await add.locator('h2').innerText(), /Add Codex/);
    // API key, for a tool whose key is not the computer's own: the way there, and Back (ui/tests/model-keys.cjs: Add key).
    await add.locator('[data-add-kind=api_key]').click();
    assert.match(await add.innerText(), /Codex takes its key from Credentials/);
    assert.equal(await add.locator('a[href="#/credentials"]').count(), 1);
    await add.locator('[data-add-back=kind]').click();
    assert.equal(await add.locator('input[name=profile]').count(), 0, 'no name: the computer has one login per tool');
    await add.locator('[data-add-own]').click();
    const signIn = page.locator('dialog[data-marker=same][open]');
    await signIn.locator('[data-code]').waitFor();
    assert.equal(await page.locator('dialog[open]:not(.computer-detail)').count(), 1, 'the sign-in carries on in the same pop-up');
    assert.match(await signIn.locator('h2').innerText(), /Sign in to Codex \(ChatGPT\) on Team box/);
    assert.equal(await signIn.locator('[data-code]').innerText(), 'ABCD-1234');
    await shot(signIn, 'add-3-sign-in');
    assert.deepEqual(writes.find(w => w.p === '/api/v2/computers/box/logins').body.profile || '', '');
    // Signed in, and the computer reports it: the line reads signed in with no restart.
    login = {...login, state: 'signed_in', url: '', code: '', updated_at: new Date().toISOString()};
    box.readiness.runtimes.codex.authenticated = 'ready';
    box.bot_connections[0].signed_in = true;
    box.readiness_at = at(60000);
    await signIn.locator('[data-status][data-state=signed_in]').waitFor({timeout: 10000});
    await signIn.locator('[data-close]').last().click();
    // Close clears [open] before its close-event handler removes the node.
    await page.locator('dialog[data-marker=same]').waitFor({state: 'detached'});
    // Signed in now: Add connection offers no second Codex sign-in; a different account means another computer.
    await detail.locator('[data-connection="own:codex"]', {hasText: 'Signed in'}).waitFor({timeout: 15000});
    await detail.locator('[data-add-connection=box]').click();
    await add.locator('[data-add-tool=codex]').click();
    assert.equal(await add.locator('[data-add-own]').isDisabled(), true);
    assert.match(await add.locator('[data-add-own]').innerText(), /add another computer/);
    await page.keyboard.press('Escape');
    await add.waitFor({state: 'detached'});

    // Offline: Add connection waits for the computer.
    box.last_seen_at = at(-3 * 3600000);
    await page.evaluate(() => loadSettings());
    await detail.locator('[data-computer-offline]').waitFor();
    assert.equal(await detail.locator('[data-add-connection]:enabled').count(), 0);

    await page.keyboard.press('Escape');
    // Hosted enrollment leaves the browser with no code or installer command.
    assert.equal(await page.locator('#machine-kind option[value="hosted"]').count(), 0);
    await page.evaluate(() => { S.config.hosted = 'invited'; document.querySelector('#machine-add-dialog').remove(); return loadSettings(); });
    await page.evaluate(() => { window.hostedOpened = []; window.open = (...args) => window.hostedOpened.push(args); });
    await page.locator('#machine-add-open').click();
    await page.locator('#machine-kind').selectOption('hosted');
    assert.equal(await page.locator('#machine-label').isVisible(), false);
    const beforeHosted = writes.length;
    await page.locator('#register-machine').click();
    const opened = await page.evaluate(() => window.hostedOpened);
    assert.match(opened[0][0], /^https:\/\/app\.tico\.dev\/hosting\?workspace=/);
    assert.equal(opened[0][2], 'noopener');
    assert.equal(writes.length, beforeHosted);
    assert.deepEqual(errors, []);
    console.log('PASS: Add connection (tool, the computer\'s one login signed in in one pop-up, no second sign-in), offline');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
