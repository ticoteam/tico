// Sign out and Switch account from the app (ui/model-login.js, backend/model_login.py). On Settings > Computers a
// signed-in Codex or Claude Code login, the computer's own or a named subscription's, offers Switch account and Sign
// out to the computer's operator or an admin, only while the computer is online. Sign out first names the bots that
// stop, then asks the computer and follows it to Signed out; a computer login someone signed out reads Signed out and
// offers Sign in and Use the shared key. Switch account is the sign-in dialog, titled as a switch, posting switch: true,
// and a cancel says the old account still works. Manage subscription offers the same for each signed-in runtime. A
// tool without a logout in its manifest, and a plain member, see none of it. Every request is intercepted; the harness
// capabilities come from runner/harnesses/*.toml.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile, harnesses} = require('./support/page.cjs');
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const now = Date.now(), at = ms => new Date(now + ms).toISOString();
    const chip = (name, runtime) => ({name, runtime, installed: true, version: '1.0.0', managed: true, state: 'idle', wanted: true,
      pinned: false, update_available: false, detail: ''});
    const fixture = () => ({id: 'box', label: 'Team box', operator: 'sam', bots: ['writer'], platform: 'linux', last_seen: at(-3000),
      needed_runtimes: ['claude', 'codex'], bot_runtimes: ['codex'], signed_out: {},
      readiness: {
        runtimes: {
          codex: {installed: true, authenticated: 'ready', account: {email: 'old@acme.example', plan: 'pro', kind: 'subscription', checked_at: at(-60000)}},
          claude: {installed: true, authenticated: 'ready', account: {email: 'chris@acme.example', plan: 'max', checked_at: at(-60000)}},
          gemini: {installed: true, authenticated: 'ready'},
        },
        harnesses: {codex: chip('Codex', 'codex'), claude: chip('Claude Code', 'claude'), gemini: chip('Gemini CLI', 'gemini')}},
      profiles: [{name: 'acme', display_name: 'Acme', runtimes: {codex: {signed_in: true, account: {email: 'dev@acme.example', plan: 'pro'}}}}]});

    async function open(as = 'sam') {
      const box = fixture(), writes = [], polls = {};
      const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/*', route => {
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
        if (p === '/api/v2/subscriptions') return json({profiles_by_computer: [{runner_id: 'box', label: 'Team box', profiles: box.profiles}], assignments: []});
        // backend/model_login.py affected_bots
        if (p === '/api/v2/computers/box/logouts/bots') {
          const runtime = url.searchParams.get('runtime'), profile = url.searchParams.get('profile') || '';
          return json({runtime, profile, bots: runtime === 'codex' && !profile ? [{bot: 'writer', name: 'Writer'}, {bot: 'scout', name: 'Scout'}] : []});
        }
        if (p === '/api/v2/computers/box/logouts' && method === 'POST') {
          const body = req.postDataJSON(); writes.push({p, body});
          return json({id: 'out1', runtime: body.runtime, profile: body.profile || '', kind: 'sign_out', state: 'requested', lines: []});
        }
        if (p === '/api/v2/computers/box/logins/out1') {
          // The computer signs out on its next check; the operations view then carries the mark.
          polls.out1 = (polls.out1 || 0) + 1;
          if (polls.out1 < 2) return json({id: 'out1', kind: 'sign_out', state: 'starting', lines: []});
          box.signed_out = {codex: ['']};
          box.readiness.runtimes.codex = {installed: true, authenticated: 'missing'};
          return json({id: 'out1', kind: 'sign_out', state: 'signed_out', message: 'Signed out', lines: []});
        }
        if (p === '/api/v2/computers/box/shared-key' && method === 'POST') {
          writes.push({p, body: req.postDataJSON()});
          return json({id: 'key1', kind: 'use_shared_key', state: 'requested', lines: []});
        }
        if (p === '/api/v2/computers/box/logins' && method === 'POST') {
          writes.push({p, body: req.postDataJSON()});
          return json({id: 'sw1', kind: 'switch', state: 'waiting', url: 'https://auth.openai.com/codex/device', code: 'ABCD-1234', created: at(0), lines: []});
        }
        if (p === '/api/v2/computers/box/logins/sw1') return json({id: 'sw1', kind: 'switch', state: 'waiting', url: 'https://auth.openai.com/codex/device', code: 'ABCD-1234', lines: []});
        if (p === '/api/v2/computers/box/logins/sw1/cancel') {
          writes.push({p});
          return json({id: 'sw1', kind: 'switch', state: 'cancelled', lines: []});
        }
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json({});
      });
      await page.goto('https://tico-ui.test/#/settings');
      await page.locator('[data-settings-tab=devices]').click();
      await page.locator('#set-machines [role=table][aria-label="AI tools on Team box"]').waitFor();
      return {page, box, writes, errors};
    }

    // ---- The operator, on Settings > Computers
    const {page, box, writes, errors} = await open();
    const table = page.locator('#set-machines [role=table][aria-label="AI tools on Team box"]');
    const own = name => table.locator(`[data-machine-tool="${name}"]:not([data-machine-profile])`);
    const buttons = row => row.locator('.machine-tool-signin button').allInnerTexts();
    assert.deepEqual(await buttons(own('codex')), ['Switch account', 'Sign out']);
    assert.deepEqual(await buttons(own('claude')), ['Switch account', 'Sign out']);
    assert.deepEqual(await buttons(table.locator('[data-machine-profile="acme"][data-machine-tool="codex"]')), ['Switch account', 'Sign out']);
    assert.deepEqual(await buttons(own('gemini')), [], 'no logout in its manifest: nothing to offer');

    // Sign out: the confirmation names the bots that stop; Cancel sends nothing.
    await own('codex').getByRole('button', {name: 'Sign out'}).click();
    const confirm = page.locator('dialog.model-logout');
    await confirm.locator('[data-bots]', {hasText: 'Writer'}).waitFor();
    assert.equal(await confirm.locator('h2').innerText(), 'Sign out of Codex (ChatGPT) on Team box?');
    assert.equal(await confirm.locator('[data-bots]').innerText(), 'These 2 bots stop until someone signs in again: Writer, Scout.');
    assert.match(await confirm.innerText(), /shared key won't sign it back in by itself/);
    await confirm.getByRole('button', {name: 'Cancel'}).click();
    assert.equal(await page.locator('dialog.model-logout').count(), 0);
    assert.deepEqual(writes, []);

    // Confirmed: it asks the computer and follows it to Signed out; the row then reads Signed out, with Sign in and
    // Use the shared key.
    await own('codex').getByRole('button', {name: 'Sign out'}).click();
    await confirm.locator('[data-bots]', {hasText: 'Writer'}).waitFor();
    await confirm.locator('[data-sign-out]').click();
    await confirm.locator('[data-status][data-state=signed_out]').waitFor();
    assert.equal(await confirm.locator('[data-status]').innerText(), 'Signed out. Codex (ChatGPT) on Team box stays signed out until someone signs in.');
    assert.deepEqual(writes, [{p: '/api/v2/computers/box/logouts', body: {runtime: 'codex'}}]);
    await confirm.locator('[data-actions] [data-close]').click();
    await own('codex').getByRole('button', {name: 'Use the shared key'}).waitFor({timeout: 10000});
    assert.deepEqual(await buttons(own('codex')), ['Sign in', 'Use the shared key']);
    assert.match(await own('codex').locator('.machine-tool-signin .pill').getAttribute('title'), /shared key does not sign it back in/);
    await own('codex').getByRole('button', {name: 'Use the shared key'}).click();
    await page.locator('.toast', {hasText: 'shared key signs it in'}).waitFor();
    assert.deepEqual(writes.at(-1), {p: '/api/v2/computers/box/shared-key', body: {runtime: 'codex'}});

    // A named subscription's sign-out has no bots here and says nothing about the shared key.
    await table.locator('[data-machine-profile="acme"][data-machine-tool="codex"]').getByRole('button', {name: 'Sign out'}).click();
    await confirm.locator('[data-bots]', {hasText: 'No bot'}).waitFor();
    assert.equal(await confirm.locator('h2').innerText(), 'Sign out of Codex (ChatGPT) · acme on Team box?');
    assert.doesNotMatch(await confirm.innerText(), /shared key/);
    await confirm.getByRole('button', {name: 'Cancel'}).click();

    // Switch account: the sign-in dialog, as a switch; a cancel keeps the old account.
    writes.length = 0;
    await own('claude').getByRole('button', {name: 'Switch account'}).click();
    const login = page.locator('dialog.model-login:not(.model-logout)');
    await login.locator('h2', {hasText: 'Switch account for Claude Code on Team box'}).waitFor();
    await login.locator('code', {hasText: 'ABCD-1234'}).waitFor();
    assert.deepEqual(writes, [{p: '/api/v2/computers/box/logins', body: {runtime: 'claude', switch: true}}]);
    await login.locator('[data-cancel]').click();
    await login.locator('[data-status]', {hasText: 'cancelled'}).waitFor();
    assert.match(await login.locator('[data-status]').innerText(), /The account signed in before still works\./);
    await page.keyboard.press('Escape');

    // Offline: no sign-out or switch to offer.
    box.last_seen = at(-3 * 3600000);
    await page.evaluate(() => loadSettings());
    await table.locator('[data-machine-tool="claude"] .machine-tool-signin', {hasText: 'last checked'}).first().waitFor();
    assert.deepEqual(await buttons(own('claude')), []);
    box.last_seen = at(-3000);
    await page.evaluate(() => loadSettings());
    await own('claude').getByRole('button', {name: 'Sign out'}).waitFor();

    // Manage subscription: the same for each signed-in runtime of the subscription.
    await page.locator('[data-settings-tab=providers]').click();
    const manage = page.locator('details.subs-controls[data-runner="box"][data-profile="acme"]');
    await manage.waitFor();
    await manage.locator('summary').click();
    const account = manage.locator('[data-subs-account="codex"]');
    assert.deepEqual(await account.locator('button').allInnerTexts(), ['Switch account', 'Sign out']);
    await account.getByRole('button', {name: 'Switch account'}).click();
    await login.locator('h2', {hasText: 'Switch account for Codex (ChatGPT) · acme on Team box'}).waitFor();
    assert.deepEqual(writes.at(-1), {p: '/api/v2/computers/box/logins', body: {runtime: 'codex', profile: 'acme', switch: true}});
    assert.deepEqual(errors, []);
    await page.close();

    // ---- A member who does not run the computer: nothing to sign out or switch.
    const other = await open('cara');
    assert.equal(await other.page.locator('#set-machines [data-model-sign-out], #set-machines [data-model-switch]').count(), 0);
    assert.deepEqual(other.errors, []);
    console.log('PASS: sign out with the bots named, switch account, use the shared key, rights and offline');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
