// Settings > Computers, part 3: Add connection (the tool, Subscription or API key, a name, then the provider's sign-in in
// the same pop-up; the chip appears once the computer reports, with no restart), Rename a subscription, and Remove one:
// refused while bots run on it, naming them and offering the Bots page (by the page's own list or the server's), and
// otherwise taken off the computer. Every request is intercepted.
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
    const box = {id: 'box', label: 'Team box', operator: 'ana', bots: ['writer'], platform: 'linux', version: '0.3.53',
      last_seen: at(-3000), readiness_at: at(-60000), needed_runtimes: ['codex'], bot_runtimes: ['codex'],
      readiness: {runtimes: {codex: {installed: true, authenticated: 'missing'}, claude: {installed: true, authenticated: 'missing'},
        grok: {installed: false}},
        harnesses: {codex: tool('Codex', 'codex'), claude: tool('Claude Code', 'claude')}},
      profiles: [
        {name: 'acme', id: 'p-acme', display_name: 'Acme Pro', runtimes: {codex: {signed_in: true}}},
        {name: 'beta', id: 'p-beta', display_name: 'beta', runtimes: {codex: {signed_in: true}}},
        {name: 'spare', id: 'p-spare', display_name: 'Spare', runtimes: {codex: {signed_in: true}}}],
      bot_connections: [{bot: 'writer', profile: 'beta', display_name: 'beta', source: 'bot', runtime: 'codex', harness: 'codex', signed_in: true, problem: ''}]};
    const me = {id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.com', cloud: true, registered: true};
    const writes = [];
    let login = null, removal = null, refuseRemoval = true;
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
            created: new Date().toISOString(), updated: new Date().toISOString()};
          return json(login);
        }
        if (p === '/api/v2/computers/box/removals') {
          if (refuseRemoval) return json({error: {code: 'in_use', detail: 'Sleeper run on it. Move them to another connection on the Bots page first',
            bots: [{bot: 'sleeper', name: 'Sleeper'}]}}, 409);
          removal = {id: 'r1', kind: 'remove', runner_id: 'box', runtime: body.runtime, profile: body.profile, state: 'requested',
            created: new Date().toISOString(), updated: new Date().toISOString(), lines: []};
          return json(removal);
        }
        return json({});
      }
      if (p === '/api/v2/computers/box/logins/l1') return json(login);
      if (p === '/api/v2/computers/box/logins/r1') return json(removal);
      const config = {version: '0.1.0', app_name: 'Tico'};
      if (p === '/api/me') return json({...me, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json([{name: 'writer', display_name: 'Writer', status: 'active', operator: 'ana', can_manage: true,
        users: [], bot_owners: [], machine: {runner_id: 'box', label: 'Team box', operator: 'ana'}}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/operations') return json({machines: [box], services: []});
      if (p === '/api/v2/models') return json({models: [], harnesses: [
        {id: 'claude', runtime: 'claude', name: 'Claude Code', capabilities: {account: 'status-json', weekly_usage: 'turns', browser_sign_in: true, subscription_profiles: true, sign_out: true}},
        {id: 'codex', runtime: 'codex', name: 'Codex', capabilities: {account: 'app-server', weekly_usage: 'refresh', browser_sign_in: true, subscription_profiles: true, sign_out: true}},
        {id: 'grok', runtime: 'grok', name: 'Grok Build', capabilities: {account: '', weekly_usage: 'manual'}}]});
      if (p === '/api/v2/subscriptions') return json({profiles_by_computer: [], assignments: []});
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

    // 15. Add connection → Codex → Subscription → name → sign in, all in one pop-up.
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
    assert.equal(await add.locator('[data-add-kind=subscription]').isEnabled(), true);
    // API key, for a tool whose key is not the computer's own: the way there, and Back (ui/tests/model-keys.cjs: Add key).
    await add.locator('[data-add-kind=api_key]').click();
    assert.match(await add.innerText(), /Codex takes its key from Credentials/);
    assert.equal(await add.locator('a[href="#/credentials"]').count(), 1);
    await add.locator('[data-add-back=kind]').click();
    await add.locator('[data-add-kind=subscription]').click();
    const name = add.locator('[name=profile]');
    await name.fill('Team ChatGPT');
    assert.equal(await add.locator('[data-add-msg]').innerText(), 'Saved as team-chatgpt');
    await shot(add, 'add-3-name');
    await add.locator('button[type=submit]').click();
    const signIn = page.locator('dialog[data-marker=same][open]');
    await signIn.locator('[data-code]').waitFor();
    assert.equal(await page.locator('dialog[open]:not(.computer-detail)').count(), 1, 'the sign-in carries on in the same pop-up');
    assert.match(await signIn.locator('h2').innerText(), /Sign in to Codex \(ChatGPT\) · team-chatgpt on Team box/);
    assert.equal(await signIn.locator('[data-code]').innerText(), 'ABCD-1234');
    await shot(signIn, 'add-4-sign-in');
    assert.deepEqual(writes.find(w => w.p === '/api/v2/computers/box/logins').body, {runtime: 'codex', profile: 'team-chatgpt'});
    // Signed in, and the computer reports it: the chip appears on the row with no restart.
    assert.equal(await row.locator('[data-chip="profile:team-chatgpt:codex"]').count(), 0);
    login = {...login, state: 'signed_in', url: '', code: '', updated: new Date().toISOString()};
    box.profiles.push({name: 'team-chatgpt', id: 'p-team', display_name: 'Team ChatGPT', runtimes: {codex: {signed_in: true}}});
    box.readiness_at = at(60000);
    await signIn.locator('[data-status][data-state=signed_in]').waitFor({timeout: 10000});
    assert.match(await signIn.locator('[data-status]').innerText(), /Signed in\. Codex \(ChatGPT\) · team-chatgpt is ready on Team box/);
    await row.locator('[data-chip="profile:team-chatgpt:codex"]').waitFor({timeout: 15000});
    await signIn.locator('[data-close]').last().click();
    // The pop-up is taken out of the page once it has closed, not in the same tick as the click.
    await page.locator('dialog[data-marker=same]').waitFor({state: 'detached', timeout: 5000});

    // Rename a subscription: its name only; login and bots stay.
    const acme = detail.locator('[data-connection="profile:acme:codex"]');
    await acme.locator('.computer-conn-rename summary').click();
    await acme.locator('[data-subs-rename] [name=display_name]').fill('Acme Team');
    await shot(detail, 'detail-rename');
    await acme.locator('[data-subs-rename] button[type=submit]').click();
    for (let i = 0; i < 100 && !writes.some(w => w.p === '/api/v2/subscriptions/name'); i++) await page.waitForTimeout(20);
    assert.deepEqual(writes.find(w => w.p === '/api/v2/subscriptions/name').body, {runner_id: 'box', profile: 'acme', display_name: 'Acme Team'});
    // The computer's own login has no Rename or Remove (Sign out is its way off).
    assert.equal(await detail.locator('[data-connection^="own:"] .computer-conn-rename, [data-connection^="own:"] [data-connection-remove]').count(), 0);

    // 18. Remove with bots on it: it names them and sends the person to the Bots page first; no Remove button.
    await detail.locator('[data-connection="profile:beta:codex"] [data-connection-remove]').click();
    const remove = page.locator('dialog.model-remove[open]');
    await remove.waitFor();
    assert.match(await remove.locator('[data-remove-bots]').innerText(), /^Writer runs on it\. Move it to another connection on the Bots page first/);
    assert.equal(await remove.locator('[data-remove]').count(), 0);
    await shot(remove, 'remove-bots');
    await remove.locator('[data-remove-bots-page]').click();
    assert.equal(await page.locator('dialog[open]').count(), 0);
    assert.equal(await page.evaluate(() => SETTINGS_BOTS_VIEW.computer), 'box');
    assert.equal(writes.filter(w => w.p === '/api/v2/computers/box/removals').length, 0);

    // A bot the page did not list (paused, or moved here since): the server's refusal names it the same way.
    await page.locator('[data-settings-tab=devices]').click();
    await openDetail();
    await detail.locator('[data-connection="profile:spare:codex"] [data-connection-remove]').click();
    await remove.waitFor();
    assert.match(await remove.innerText(), /No bot runs on it/);
    await remove.locator('[data-remove]').click();
    await remove.locator('[data-remove-bots]').waitFor();
    assert.match(await remove.locator('[data-remove-bots]').innerText(), /^Sleeper runs on it\. Move it to another connection on the Bots page first/);
    await remove.locator('[data-close]').first().click();

    // A bot that came onto it after the ask stops it at the computer, before anything goes: the Bots page again.
    refuseRemoval = false;
    await detail.locator('[data-connection="profile:spare:codex"] [data-connection-remove]').click();
    await remove.waitFor();
    await remove.locator('[data-remove]').click();
    removal = {...removal, state: 'failed', message: 'Writer run on it. Move them to another connection on the Bots page first',
      updated: new Date().toISOString()};
    await remove.locator('[data-status][data-state=failed]').waitFor({timeout: 10000});
    assert.match(await remove.locator('[data-status]').innerText(), /^Writer run on it\. Move them/);
    assert.equal(await remove.locator('[data-remove-bots-page]').count(), 1);
    await remove.locator('[data-close]').last().click();

    // With no bots on it, it comes off the computer.
    await detail.locator('[data-connection="profile:spare:codex"] [data-connection-remove]').click();
    await remove.waitFor();
    await remove.locator('[data-remove]').click();
    assert.deepEqual(writes.filter(w => w.p === '/api/v2/computers/box/removals').at(-1).body, {runtime: 'codex', profile: 'spare'});
    removal = {...removal, state: 'done', updated: new Date().toISOString()};
    box.profiles = box.profiles.filter(p => p.name !== 'spare');
    box.readiness_at = at(120000);
    await remove.locator('[data-status][data-state=done]').waitFor({timeout: 10000});
    assert.match(await remove.locator('[data-status]').innerText(), /Removed\. Codex \(ChatGPT\) · Spare is no longer on Team box/);
    await remove.locator('[data-close]').last().click();
    for (let i = 0; i < 150 && await row.locator('[data-chip="profile:spare:codex"]').count(); i++) await page.waitForTimeout(100);
    assert.equal(await row.locator('[data-chip="profile:spare:codex"]').count(), 0);

    // Offline: Add connection, Rename and Remove wait for the computer.
    box.last_seen = at(-3 * 3600000);
    await page.evaluate(() => loadSettings());
    await detail.locator('[data-computer-offline]').waitFor();
    assert.equal(await detail.locator('[data-add-connection]:enabled, [data-connection-remove]:enabled, .computer-conn-rename').count(), 0);
    assert.equal(await detail.locator('[data-connection="profile:acme:codex"] [data-connection-remove]').getAttribute('title'), 'Computer is offline');

    assert.deepEqual(errors, []);
    console.log('PASS: Add connection (tool, subscription, name, sign-in in one pop-up, chip without restart), Rename, Remove (bots first, server refusal, removed), offline');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
