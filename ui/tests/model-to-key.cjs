// Switch to API key from the app (ui/model-login.js, backend/model_login.py `to_key`, runner-rs/src/login.rs). In a
// computer's pop-up on Settings > Computers, the computer's own login on a subscription offers Switch to API key next to
// Switch account and Sign out, for a tool Tico keeps a key for, to the computer's operator while the computer is online.
// The key is taken in a password field, posted once with `switch` and emptied from the page; the dialog says the
// subscription stays in use while the computer checks it, offers Cancel switch meanwhile, and ends saying the
// subscription was signed out, or that it is still in use. Once switched, the same connection (same bots) runs on the
// key. Every request is intercepted; the harness capabilities come from runner/harnesses/*.toml.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile, harnesses} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const now = Date.now(), at = ms => new Date(now + ms).toISOString();
    const SECRET = 'sk-test-not-a-real-key-123456';
    const chip = (name, runtime) => ({name, runtime, installed: true, version: '1.0.0', managed: true, state: 'idle', wanted: true,
      pinned: false, update_available: false, detail: ''});
    const subscription = () => ({installed: true, authenticated: 'ready', account: {email: 'dev@acme.example', plan: 'pro', checked_at: at(-60000)}});
    const fixture = () => ({id: 'box', label: 'Team box', owner_id: 'sam', bots: ['writer'], platform: 'linux', last_seen_at: at(-3000),
      readiness_at: at(-3000), needed_runtimes: ['codex', 'claude'], bot_runtimes: ['codex', 'claude'], signed_out: {}, keys: {}, shared_keys: {},
      bot_connections: [{bot: 'writer', runtime: 'codex', profile: ''}],
      readiness: {runtimes: {codex: subscription(), claude: subscription()},
        harnesses: {codex: chip('Codex', 'codex'), claude: chip('Claude Code', 'claude')}},
      profiles: []});
    // How each job ends on the computer: on its second check, or (`hold`) not until it is cancelled.
    const ending = {codex: {state: 'done', message: 'Switched to the API key; the subscription is signed out'}, claude: 'hold'};
    // `held`: the next Switch to API key request waits until it is released, as on a slow connection.
    let held = null;

    async function open(as = 'sam') {
      const box = fixture(), writes = [], rows = {};
      const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/*', async route => {
        const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
        const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
        if (ui && fs.existsSync(uiFile(ui[1])))
          return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
        if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        const config = {version: '0.1.0', app_name: 'Tico'};
        // Sam is a member who operates the computer; Cara is a member who does not.
        if (p === '/api/me') return json({id: as, role: 'member', name: as, email: `${as}@example.com`, cloud: true, registered: true, config});
        if (p === '/api/v2/config') return json(config);
        if (p === '/api/employees') return json([]);
        if (p === '/api/issues') return json([]);
        if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'sam', name: 'Sam'}], teams: {}});
        if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
        if (p === '/api/v2/status') return json({bots: []});
        if (p === '/api/v2/needs-you') return json({items: []});
        if (p === '/api/v2/operations') return json({machines: [box], services: []});
        if (p === '/api/v2/models') return json({models: [], harnesses});
        if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
        if (p === '/api/v2/subscriptions') return json({profiles_by_computer: [], assignments: []});
        const keys = p.match(/^\/api\/v2\/computers\/(\w+)\/keys$/);
        if (keys && method === 'GET') {
          const runtime = url.searchParams.get('runtime'), name = runtime === 'codex' ? 'OPENAI_API_KEY' : 'ANTHROPIC_API_KEY';
          return json({runtime, source: null, name, preview: '', cleared: false, names: [name], computers: [], can_replace_all: false});
        }
        if (p === '/api/v2/computers/box/keys' && method === 'POST') {
          const body = req.postDataJSON(); writes.push({p, body});
          if (held) await held.promise;
          const id = 'key' + Object.keys(rows).length;
          rows[id] = {id, kind: body.switch ? 'to_key' : 'use_key', runtime: body.runtime, state: 'requested', polls: 0, created_at: new Date().toISOString(), lines: []};
          return json({...rows[id], key: {id: 'cred9', env: 'OPENAI_API_KEY', preview: 'sk-…456'}});
        }
        const cancel = p.match(/^\/api\/v2\/computers\/box\/logins\/(\w+)\/cancel$/);
        if (cancel) {
          writes.push({p, body: req.postDataJSON()});
          return json(Object.assign(rows[cancel[1]], {state: 'cancelled', updated_at: new Date().toISOString()}));
        }
        const one = p.match(/^\/api\/v2\/computers\/box\/logins\/(\w+)$/);
        if (one) {
          const row = rows[one[1]], end = ending[row.runtime];
          if (++row.polls === 2 && end !== 'hold') Object.assign(row, end, {updated_at: new Date().toISOString()});
          return json(row);
        }
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json(unrouted(p));
      });
      await page.goto('https://tico-ui.test/#/settings');
      await page.locator('[data-settings-tab=devices]').click();
      await page.locator('#set-machines .machine-name[data-computer-open=box]').click();
      await page.locator('#set-machines dialog.computer-detail[open] [data-connection]').first().waitFor();
      return {page, box, writes, errors};
    }

    // ---- The operator, on Settings > Computers
    const {page, box, writes, errors} = await open();
    const detail = page.locator('#set-machines dialog.computer-detail[open]');
    const own = name => detail.locator(`[data-connection="own:${name}"]`);
    // Switch account and Sign out are icons: their name is the aria-label.
    const buttons = row => row.locator('.computer-conn-signin button').evaluateAll(all => all.map(b => b.getAttribute('aria-label') || b.innerText));
    assert.deepEqual(await buttons(own('codex')), ['Switch account', 'Switch to API key', 'Sign out']);
    assert.deepEqual(await buttons(own('claude')), ['Switch account', 'Switch to API key', 'Sign out']);
    const before = await own('codex').innerText();
    assert.match(before, /Bots: writer/i);

    // Switch: the key in a password field, posted once with `switch`, and the subscription in use until it is checked.
    await own('codex').getByRole('button', {name: 'Switch to API key'}).click();
    const dialog = page.locator('dialog.model-key');
    await dialog.locator('input[name=secret]').waitFor();
    assert.equal(await dialog.locator('h2').innerText(), 'Switch Codex (ChatGPT) on Team box to an API key');
    assert.match(await dialog.locator('[data-key-form]').innerText(), /The subscription stays signed in and in use while Team box checks the key; once it works there the subscription is signed out/);
    assert.match(await dialog.locator('[data-key-form]').innerText(), /Bots keep this login, so nothing needs reassigning/);
    assert.equal(await dialog.locator('[data-key-scope]').count(), 0);
    assert.equal(await dialog.locator('input[name=secret]').getAttribute('type'), 'password');
    assert.ok(await dialog.locator('[data-key-go]').isDisabled(), 'nothing to switch to yet');
    await dialog.locator('input[name=secret]').fill(SECRET);
    assert.equal(await dialog.locator('[data-key-go]').innerText(), 'Switch to API key');
    await dialog.locator('[data-key-go]').click();
    await dialog.locator('[data-status][data-state=done]').waitFor();
    assert.deepEqual(writes, [{p: '/api/v2/computers/box/keys', body: {runtime: 'codex', secret: SECRET, scope: 'this', switch: true}}]);
    assert.equal(await dialog.locator('[data-status]').innerText(), 'Switched. Codex (ChatGPT) on Team box now uses sk-…456; the subscription is signed out.');
    assert.equal(await dialog.locator('input[name=secret]').inputValue(), '');
    assert.ok(!(await page.content()).includes(SECRET), 'the key is not kept in the page');
    await dialog.locator('[data-actions] [data-close]').click();
    assert.equal(writes.length, 1, 'closing a finished switch cancels nothing');

    // Once the computer reports it: the same connection, its bots kept, runs on this computer's key.
    box.readiness.runtimes.codex = {installed: true, authenticated: 'ready', key: {name: 'OPENAI_API_KEY', source: 'computer'}, account: {kind: 'api_key', checked_at: at(0)}};
    box.keys = {codex: {env: 'OPENAI_API_KEY', preview: 'sk-…456', since: at(0)}};
    box.readiness_at = new Date().toISOString();
    await page.evaluate(() => loadSettings());
    await own('codex').locator('.machine-key', {hasText: "this computer's key sk-…456"}).waitFor({timeout: 15000});
    assert.deepEqual(await buttons(own('codex')), ['Replace key', 'Clear key', 'Switch to subscription']);
    assert.match(await own('codex').innerText(), /Bots: writer/i, 'its bots kept, nothing reassigned');

    // Cancel switch while the key is checked: the subscription is still in use.
    await own('claude').getByRole('button', {name: 'Switch to API key'}).click();
    await dialog.locator('input[name=secret]').fill(SECRET);
    await dialog.locator('[data-key-go]').click();
    await dialog.getByRole('button', {name: 'Cancel switch'}).click();
    await dialog.locator('[data-status][data-state=cancelled]').waitFor();
    assert.deepEqual(writes.slice(-1).map(w => w.p), ['/api/v2/computers/box/logins/key1/cancel']);
    assert.equal(await dialog.locator('[data-status]').innerText(), 'Cancelled. The subscription is still in use.');
    await dialog.locator('[data-actions] [data-close]').click();

    // Closing the dialog while it is checked cancels it too.
    await own('claude').getByRole('button', {name: 'Switch to API key'}).click();
    await dialog.locator('input[name=secret]').fill(SECRET);
    await dialog.locator('[data-key-go]').click();
    await dialog.getByRole('button', {name: 'Cancel switch'}).waitFor();
    await dialog.locator('[data-close]').first().click();
    for (let i = 0; i < 50 && writes.at(-1).p !== '/api/v2/computers/box/logins/key2/cancel'; i++) await page.waitForTimeout(100);
    assert.equal(writes.at(-1).p, '/api/v2/computers/box/logins/key2/cancel');

    // Closed while the request is still on its way: the switch it started is cancelled once it is back.
    held = {}; held.promise = new Promise(resolve => { held.release = resolve; });
    await own('claude').getByRole('button', {name: 'Switch to API key'}).click();
    await dialog.locator('input[name=secret]').fill(SECRET);
    await dialog.locator('[data-key-go]').click();
    for (let i = 0; i < 50 && writes.at(-1).p !== '/api/v2/computers/box/keys'; i++) await page.waitForTimeout(100);
    await dialog.locator('[data-close]').first().click();
    await dialog.waitFor({state: 'detached'});
    const release = held.release; held = null; release();
    for (let i = 0; i < 50 && writes.at(-1).p !== '/api/v2/computers/box/logins/key3/cancel'; i++) await page.waitForTimeout(100);
    assert.equal(writes.at(-1).p, '/api/v2/computers/box/logins/key3/cancel');

    // A failure says what the computer reported: the subscription still in use when it put everything back, and what
    // could not be put back when it could not, with no promise that the subscription is in use.
    const fails = async message => {
      ending.claude = {state: 'failed', message};
      await own('claude').getByRole('button', {name: 'Switch to API key'}).click();
      await dialog.locator('input[name=secret]').fill(SECRET);
      await dialog.locator('[data-key-go]').click();
      await dialog.locator('[data-status][data-state=failed]').waitFor();
      const said = await dialog.locator('[data-status]').innerText();
      await dialog.locator('[data-actions] [data-close]').click();
      return said;
    };
    assert.equal(await fails('The new key did not work for claude; the subscription is still in use'),
      'The new key did not work for claude; the subscription is still in use.');
    const lost = "The subscription could not be signed out on claude, and the subscription's login could not be put back (mv failed); its files are in /home/sam/.tico-switch-1";
    assert.equal(await fails(lost), lost + '.');
    const keys = "Could not check that claude signed its subscription out; the subscription is back, but this computer's keys could not all be put back (PermissionDenied)";
    assert.equal(await fails(keys), keys + '.');
    for (const said of [lost, keys]) assert.doesNotMatch(said, /still in use/);

    // Offline: Switch to API key stays, disabled, saying why.
    const offered = await detail.locator('[data-model-to-key]').count();
    box.last_seen_at = at(-3 * 3600000);
    await page.evaluate(() => loadSettings());
    await own('claude').locator('.computer-conn-signin', {hasText: 'last checked'}).waitFor();
    assert.ok(offered > 0);
    assert.deepEqual(await detail.locator('[data-model-to-key]').evaluateAll(all => all.map(b => b.disabled && b.title)),
      Array(offered).fill('Computer is offline'));
    assert.deepEqual(errors, []);
    await page.close();

    // ---- A member who does not run the computer: no switch.
    const other = await open('cara');
    assert.equal(await other.page.locator('#set-machines [data-model-to-key]').count(), 0);
    assert.deepEqual(other.errors, []);
    console.log('PASS: Switch to API key on a subscription line, the key posted once with switch, done / cancel / close (also while it is being asked for) / failed with what the computer put back, same connection after, offline and rights');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
