// Sign out and Switch account from the app (ui/model-login.js, backend/model_login.py). In a computer's pop-up on
// Settings > Computers each signed-in Codex, Claude Code or Muse Code connection, the computer's one login for that tool,
// offers Switch account and Sign out to the computer's operator or an admin, only while the computer is online. Sign out first names the bots that
// stop, then asks the computer and follows it to Signed out; a computer login someone signed out reads Signed out and
// offers Sign in and Use the shared key. Switch account is the sign-in dialog, titled as a switch, posting switch: true,
// and a cancel says the old account still works. A named subscription a runner still reports from before one login
// per computer (#1259) has no line. A tool without a logout in its manifest, and a plain member, see none of it. Sign out stays off until the bots are
// named, and a failed lookup offers Try again. After a change the page looks again until a heartbeat newer than the
// change shows it (a stale one still reads the old state). A switch past its commit point reads as finishing with no cancel, and a Mac offers no
// Switch account for Claude Code (switch_unavailable_on). Every request is intercepted; the harness capabilities come
// from runner/harnesses/*.toml.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile, harnesses} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const now = Date.now(), at = ms => new Date(now + ms).toISOString();
    const chip = (name, runtime) => ({name, runtime, installed: true, version: '1.0.0', managed: true, state: 'idle', wanted: true,
      pinned: false, update_available: false, detail: ''});
    const fixture = () => ({id: 'box', label: 'Team box', owner_id: 'sam', bots: ['writer'], platform: 'linux', last_seen_at: at(-3000),
      readiness_at: at(-3000),
      needed_runtimes: ['claude', 'codex'], bot_runtimes: ['codex'], signed_out: {},
      readiness: {
        runtimes: {
          codex: {installed: true, authenticated: 'ready', account: {email: 'old@acme.example', plan: 'pro', kind: 'subscription', checked_at: at(-60000)}},
          claude: {installed: true, authenticated: 'ready', account: {email: 'chris@acme.example', plan: 'max', checked_at: at(-60000)}},
          gemini: {installed: true, authenticated: 'ready'},
          musecode: {installed: true, authenticated: 'ready'},
        },
        harnesses: {codex: chip('Codex', 'codex'), claude: chip('Claude Code', 'claude'), gemini: chip('Gemini CLI', 'gemini'), musecode: chip('Muse Code', 'musecode')}},
      profiles: [{name: 'acme', display_name: 'Acme', runtimes: {codex: {signed_in: true, account: {email: 'dev@acme.example', plan: 'pro'}}, musecode: {signed_in: true}}}]});

    async function open(as = 'sam') {
      const box = fixture(), writes = [], rows = {};
      let seq = 0, gate = null;
      const heartbeats = [];
      // The computer's next heartbeat, a few seconds after the change finished: until then the page reads the old one.
      // `early` sends one sooner that still reads the old state (it began before the change landed).
      const beat = (apply, early) => {
        if (early) heartbeats.push({at: Date.now() + 1000, apply: () => {}});
        heartbeats.push({at: Date.now() + 4000, apply});
      };
      const finish = (id, state, apply, early) => { Object.assign(rows[id], {state, updated_at: new Date().toISOString()}); beat(apply, early); };
      // Hold the next bots lookup until the test lets it go: `fail` answers it with an error.
      const hold = () => { let let_go; const held = new Promise(resolve => { let_go = resolve; }); gate = {held}; return fail => let_go(fail); };
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
        if (p === '/api/v2/operations') {
          while (heartbeats.length && Date.now() >= heartbeats[0].at) { heartbeats.shift().apply(); box.readiness_at = new Date().toISOString(); }
          return json({machines: [box], services: []});
        }
        if (p === '/api/v2/models') return json({models: [], harnesses});
        if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
        if (p === '/api/v2/subscriptions') return json({profiles_by_computer: [{runner_id: 'box', label: 'Team box', profiles: box.profiles}], assignments: []});
        // backend/model_login.py affected_bots
        if (p === '/api/v2/computers/box/logouts/bots') {
          if (gate) {
            const {held} = gate; gate = null;
            if (await held) return json({detail: 'The server is busy'}, 503);
          }
          const runtime = url.searchParams.get('runtime'), profile = url.searchParams.get('profile') || '';
          const named = names => names.map(name => ({bot: name.toLowerCase().replace(/ /g, '-'), name}));
          return json({runtime, profile, bots: profile ? [] : runtime === 'codex' ? named(['Writer', 'Scout'])
            : runtime === 'claude' ? named(['Reviewer 1', 'QA', 'Scout', 'Writer']) : []});
        }
        if (p === '/api/v2/computers/box/logouts' && method === 'POST') {
          const body = req.postDataJSON(), id = `out${++seq}`; writes.push({p, body});
          rows[id] = {id, runtime: body.runtime, profile: body.profile || '', kind: 'sign_out', state: 'requested', polls: 0, lines: []};
          return json(rows[id]);
        }
        const one = p.match(/^\/api\/v2\/computers\/box\/logins\/(\w+)(\/cancel)?$/);
        if (one && one[2]) {
          writes.push({p});
          rows[one[1]].state = 'cancelled';
          return json(rows[one[1]]);
        }
        if (one) {
          const row = rows[one[1]];
          // The computer signs out on its second check: the server marks it at once, the readiness follows on the
          // next heartbeat.
          if (row.kind === 'sign_out' && ++row.polls === 2) {
            // backend/model_login.py records every sign-out from Tico, the computer's own login's ('') and a subscription's.
            box.signed_out = {...box.signed_out, [row.runtime]: [...(box.signed_out[row.runtime] || []), row.profile]};
            finish(row.id, 'signed_out', () => {
              if (row.profile) box.profiles.find(x => x.name === row.profile).runtimes[row.runtime] = {signed_in: false};
              else box.readiness.runtimes[row.runtime] = {installed: true, authenticated: 'missing'};
            }, true);
          }
          return json(row);
        }
        if (p === '/api/v2/computers/box/shared-key' && method === 'POST') {
          const body = req.postDataJSON(); writes.push({p, body});
          // The computer takes the shared key on its next check.
          box.signed_out = {};
          beat(() => { box.readiness.runtimes[body.runtime] = {installed: true, authenticated: 'ready', account: {kind: 'api_key', checked_at: new Date().toISOString()}}; });
          return json({id: 'key1', kind: 'use_shared_key', state: 'done', created_at: new Date().toISOString(), lines: []});
        }
        if (p === '/api/v2/computers/box/logins' && method === 'POST') {
          const body = req.postDataJSON(), id = `in${++seq}`; writes.push({p, body});
          rows[id] = {id, runtime: body.runtime, profile: body.profile || '', kind: body.switch ? 'switch' : body.to ? 'to_subscription' : 'sign_in', state: 'waiting',
            url: 'https://auth.openai.com/codex/device', code: 'ABCD-1234', created_at: new Date().toISOString(), lines: []};
          return json(rows[id]);
        }
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json(unrouted(p));
      });
      await page.goto('https://tico-ui.test/#/settings');
      await page.locator('[data-settings-tab=devices]').click();
      await page.locator('#set-machines .machine-name[data-computer-open=box]').click();
      await page.locator('#set-machines dialog.computer-detail[open] [data-connection]').first().waitFor();
      return {page, box, writes, errors, rows, finish, hold, last: () => `${['in', 'out'].find(k => rows[`${k}${seq}`])}${seq}`};
    }

    // ---- The operator, on Settings > Computers
    const {page, box, writes, errors, rows, finish, hold, last} = await open();
    const table = page.locator('#set-machines dialog.computer-detail[open]');
    const own = name => table.locator(`[data-connection="own:${name}"]`);
    // Switch account and Sign out are icons: their name is the aria-label.
    const buttons = row => row.locator('.computer-conn-signin button').evaluateAll(all => all.map(b => b.getAttribute('aria-label') || b.innerText));
    assert.deepEqual(await buttons(own('codex')), ['Switch account', 'Switch to API key', 'Sign out']);
    assert.deepEqual(await buttons(own('claude')), ['Switch account', 'Switch to API key', 'Sign out']);
    assert.equal(await table.locator('[data-connection^="profile:"]').count(), 0, 'a leftover named subscription has no line');
    assert.deepEqual(await buttons(own('gemini')), [], 'no logout in its manifest: nothing to offer');

    // Muse has no account metadata; its signed-in file still enables both controls at both widths.
    for (const width of [1440, 390]) {
      await page.setViewportSize({width, height: 900});
      assert.deepEqual(await buttons(own('musecode')), ['Switch account', 'Sign out']);
      for (const row of [own('musecode')]) {
        assert.ok(await row.getByRole('button', {name: 'Switch account', exact: true}).isVisible());
        assert.ok(await row.getByRole('button', {name: 'Sign out', exact: true}).isVisible());
      }
    }
    await page.setViewportSize({width: 1440, height: 900});
    // The same Muse capability enables key recovery and subscription switching for the computer's own login.
    const museActions = await page.evaluate(() => {
      const actions = options => window.TicoModelLogin.actionsHTML({runnerId: 'box', runtime: 'musecode', ...options});
      const labels = html => {
        const row = document.createElement('div');
        row.innerHTML = html;
        return Array.from(row.querySelectorAll('button'), b => b.getAttribute('aria-label') || b.textContent);
      };
      return [labels(actions({signedOut: true})), labels(actions({signedOut: true, profile: 'acme'})),
        labels(actions({signedIn: true, onKey: true}))];
    });
    assert.deepEqual(museActions, [['Use the shared key'], [], ['Switch to subscription', 'Sign out']]);

    // Sign out: off while the bots that stop are looked up and when the lookup fails, which offers Try again; then
    // the confirmation names them. Cancel sends nothing.
    let letGo = hold();
    await own('codex').getByRole('button', {name: 'Sign out'}).click();
    const confirm = page.locator('dialog.model-logout');
    await confirm.locator('[data-bots]', {hasText: 'Checking which bots'}).waitFor();
    assert.equal(await confirm.locator('[data-sign-out]').isDisabled(), true, 'off while the bots load');
    letGo(true);
    await confirm.locator('[data-bots]', {hasText: "Couldn't check which bots use it"}).waitFor();
    assert.equal(await confirm.locator('[data-sign-out]').isDisabled(), true, 'off when the lookup failed');
    await confirm.locator('[data-sign-out]').dispatchEvent('click');
    assert.deepEqual(writes, [], 'a click on the disabled button sends nothing');
    letGo = hold();
    await confirm.getByRole('button', {name: 'Try again'}).click();
    await confirm.locator('[data-bots]', {hasText: 'Checking which bots'}).waitFor();
    assert.equal(await confirm.locator('[data-sign-out]').isDisabled(), true);
    letGo(false);
    await confirm.locator('[data-bots]', {hasText: 'Writer'}).waitFor();
    assert.equal(await confirm.locator('[data-sign-out]').isDisabled(), false, 'on once the bots are named');
    assert.equal(await confirm.locator('h2').innerText(), 'Sign out of Codex (ChatGPT) on Team box?');
    // The spec's words: "Reviewer 1, QA and 2 more will stop running until this is signed in again".
    assert.equal(await confirm.locator('[data-bots]').innerText(), 'Writer and Scout will stop running until this is signed in again.');
    assert.match(await confirm.innerText(), /shared key won't sign it back in by itself/);
    await confirm.getByRole('button', {name: 'Cancel'}).click();
    await page.locator('dialog.model-logout').waitFor({state: 'detached'});   // removed on the dialog's close event, a task after the click
    assert.deepEqual(writes, []);
    // Four bots: the first two by name and how many more, every name on hover.
    await own('claude').getByRole('button', {name: 'Sign out'}).click();
    await confirm.locator('[data-bots]', {hasText: 'Reviewer 1'}).waitFor();
    assert.equal(await confirm.locator('[data-bots]').innerText(), 'Reviewer 1, QA and 2 more will stop running until this is signed in again.');
    assert.equal(await confirm.locator('[data-bots]').getAttribute('title'), 'Reviewer 1, QA, Scout, Writer');
    await confirm.getByRole('button', {name: 'Cancel'}).click();
    await page.locator('dialog.model-logout').waitFor({state: 'detached'});
    assert.deepEqual(writes, []);

    // Confirmed: it asks the computer and follows it to Signed out; the row then reads Signed out, with Sign in and
    // Use the shared key, once a heartbeat newer than the sign-out says so (the first look still reads it signed in).
    await own('codex').getByRole('button', {name: 'Sign out'}).click();
    await confirm.locator('[data-bots]', {hasText: 'Writer'}).waitFor();
    await confirm.locator('[data-sign-out]').click();
    await confirm.locator('[data-status][data-state=signed_out]').waitFor();
    assert.equal(await confirm.locator('[data-status]').innerText(), 'Signed out. Codex (ChatGPT) on Team box stays signed out until someone signs in.');
    assert.deepEqual(writes, [{p: '/api/v2/computers/box/logouts', body: {runtime: 'codex'}}]);
    await confirm.locator('[data-actions] [data-close]').click();
    await own('codex').getByRole('button', {name: 'Use the shared key'}).waitFor({timeout: 15000});
    // Add key: a key of its own (Dev 97, ui/tests/model-keys.cjs).
    assert.deepEqual(await buttons(own('codex')), ['Sign in', 'Use the shared key', 'Add key']);
    // "Signed out · Sign in", and the chip on the computer's row has the red dot.
    assert.match((await own('codex').locator('.computer-conn-signin').textContent()).replace(/\s+/g, ' ').trim(), /^Signed out · Sign in/);
    assert.equal(await own('codex').locator('.computer-conn-head .machine-chip-dot').count(), 1);
    assert.equal(await page.locator('#set-machines [data-chip="own:codex"].attention .machine-chip-dot').count(), 1);
    assert.match(await own('codex').locator('.computer-conn-signin .pill').getAttribute('title'), /shared key does not sign it back in/);
    await own('codex').getByRole('button', {name: 'Use the shared key'}).click();
    await page.locator('.toast', {hasText: 'shared key signs it in'}).waitFor();
    assert.deepEqual(writes.at(-1), {p: '/api/v2/computers/box/shared-key', body: {runtime: 'codex'}});
    await own('codex').getByRole('button', {name: 'Sign out'}).waitFor({timeout: 15000});

    // On the shared key it runs on an API key, so it offers Switch to subscription, not Switch account (Computers part
    // 2). It is the sign-in dialog as a switch of type: a cancel says the key is still in use; done, the line shows
    // the subscription and offers Switch account again.
    assert.deepEqual(await buttons(own('codex')), ['Switch to subscription', 'Sign out']);
    const toSub = page.locator('dialog.model-login:not(.model-logout)');
    await own('codex').getByRole('button', {name: 'Switch to subscription'}).click();
    await toSub.locator('h2', {hasText: 'Switch Codex (ChatGPT) on Team box to a subscription'}).waitFor();
    await toSub.locator('code', {hasText: 'ABCD-1234'}).waitFor();
    assert.deepEqual(writes.at(-1), {p: '/api/v2/computers/box/logins', body: {runtime: 'codex', to: 'subscription'}});
    await toSub.locator('[data-cancel]').click();
    await toSub.locator('[data-status]', {hasText: 'cancelled'}).waitFor();
    assert.match(await toSub.locator('[data-status]').innerText(), /The API key is still in use\./);
    await page.keyboard.press('Escape');
    await own('codex').getByRole('button', {name: 'Switch to subscription'}).click();
    await toSub.locator('code', {hasText: 'ABCD-1234'}).waitFor();
    finish(last(), 'signed_in', () => { box.readiness.runtimes.codex.account = {email: 'old@acme.example', plan: 'pro', kind: 'subscription', checked_at: new Date().toISOString()}; });
    await toSub.locator('[data-status][data-state=signed_in]').waitFor();
    assert.equal(await toSub.locator('[data-status]').innerText(), 'Switched. Codex (ChatGPT) on Team box now runs on the subscription; its bots keep it, and the API key is off.');
    await toSub.getByRole('button', {name: 'Done'}).click();
    await own('codex').getByRole('button', {name: 'Switch account'}).waitFor({timeout: 15000});

    // Switch account: the sign-in dialog, as a switch; a cancel keeps the old account.
    const login = page.locator('dialog.model-login:not(.model-logout)');
    writes.length = 0;
    await own('claude').getByRole('button', {name: 'Switch account'}).click();
    await login.locator('h2', {hasText: 'Switch account for Claude Code on Team box'}).waitFor();
    await login.locator('code', {hasText: 'ABCD-1234'}).waitFor();
    assert.deepEqual(writes, [{p: '/api/v2/computers/box/logins', body: {runtime: 'claude', switch: true}}]);
    await login.locator('[data-cancel]').click();
    await login.locator('[data-status]', {hasText: 'cancelled'}).waitFor();
    assert.match(await login.locator('[data-status]').innerText(), /The account signed in before still works\./);
    await page.keyboard.press('Escape');

    // Past the commit point a switch reads as finishing, with nothing to cancel. Done, the row shows the new account
    // once a heartbeat newer than the switch carries it, not on the first look, which still reads the old one ready.
    await own('claude').getByRole('button', {name: 'Switch account'}).click();
    await login.locator('code', {hasText: 'ABCD-1234'}).waitFor();
    rows[last()].state = 'finishing';
    await login.locator('[data-status][data-state=finishing]').waitFor();
    assert.equal(await login.locator('[data-status]').innerText(), 'Putting the new account in place on Team box…');
    assert.equal(await login.locator('[data-actions] button').count(), 0, 'no cancel once it is finishing');
    finish(last(), 'signed_in', () => { box.readiness.runtimes.claude.account = {email: 'new@acme.example', plan: 'max', checked_at: new Date().toISOString()}; });
    await login.locator('[data-status][data-state=signed_in]').waitFor();
    await login.getByRole('button', {name: 'Done'}).click();
    await own('claude').locator('.computer-conn-signin', {hasText: 'new@acme.example'}).waitFor({timeout: 15000});

    // Offline: Switch account and Sign out stay, disabled, saying why; nothing can be started.
    const online = await buttons(own('claude'));
    box.last_seen_at = at(-3 * 3600000);
    await page.evaluate(() => loadSettings());
    await own('claude').locator('.computer-conn-signin', {hasText: 'last checked'}).waitFor();
    assert.deepEqual(await buttons(own('claude')), online);
    assert.ok(online.includes('Sign out') && online.includes('Switch account'), online.join());
    assert.deepEqual(await own('claude').locator('.computer-conn-signin button').evaluateAll(all => all.map(b => b.disabled && b.title)),
      online.map(() => 'Computer is offline'));
    box.last_seen_at = at(-3000);
    await page.evaluate(() => loadSettings());
    await own('claude').getByRole('button', {name: 'Sign out'}).waitFor();

    // A Mac: Claude Code keeps its login in the keychain there, so it offers Sign out but no Switch account, nor Switch
    // to API key (a logout there could not be undone).
    box.platform = 'darwin';
    await page.evaluate(() => loadSettings());
    await own('claude').getByRole('button', {name: 'Sign out'}).waitFor();
    assert.deepEqual(await buttons(own('claude')), ['Sign out']);
    assert.ok((await buttons(own('codex'))).includes('Switch account'), 'Codex still switches on a Mac');
    box.platform = 'linux';
    await page.evaluate(() => loadSettings());
    await own('claude').getByRole('button', {name: 'Switch account'}).waitFor();

    assert.deepEqual(errors, []);
    await page.close();

    // ---- A member who does not run the computer: nothing to sign out or switch.
    const other = await open('cara');
    assert.equal(await other.page.locator('#set-machines [data-model-sign-out], #set-machines [data-model-switch], #set-machines [data-model-to-subscription]').count(), 0);
    assert.deepEqual(other.errors, []);
    console.log('PASS: sign out with the bots named, switch account, use the shared key, rights, offline, refresh on a newer heartbeat, and Mac');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
