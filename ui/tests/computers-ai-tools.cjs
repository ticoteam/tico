// Settings > Computers: one compact row per computer (name, status, operator and Tico version; AI tool chips and Add) and the
// pop-up a row opens (Connections, Screen, Bots on this computer, Versions and updates, Settings). A chip only for a
// connection that was added, usage as "% left" with the reset on hover, a red dot only when bots depend on a
// broken one; one login per AI tool, named subscriptions a runner still reports not shown; each bot's actual connection,
// read-only; the Tool and Version headers apart at 1024 px; no connection, offline, waiting, revoked, and a
// member who may change nothing; a desktop app's state and a sign-in several bots take turns on. Every request is
// intercepted.
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
    const now = Date.now(), at = ms => new Date(now + ms).toISOString();
    const tool = (name, runtime, version) => ({name, runtime, installed: true, version, managed: true, state: 'idle',
      wanted: true, pinned: false, update_available: false, detail: ''});
    const box = {id: 'box', label: 'Team box', operator: 'ana', bots: ['helper', 'ops', 'writer'], platform: 'linux', version: '0.3.52',
      last_seen: at(-3000), needed_runtimes: ['claude', 'codex'], bot_runtimes: ['claude', 'codex'], harness_choices: {grok: false},
      update: {state: 'needs_update', label: 'Needs update', target: '0.3.53'}, inbox_only: false,
      readiness: {
        runtimes: {
          claude: {installed: true, authenticated: 'ready', account: {email: 'chris@acme.example', plan: 'max', checked_at: at(-60000)}},
          codex: {installed: true, authenticated: 'ready', account: {email: 'Dev@acme.example', plan: 'pro', checked_at: at(-60000)},
                  weekly: {used_percent: 70, resets_at: at(3 * 86400000), reported_at: at(-3600000), status: null}},
          musecode: {installed: true, authenticated: 'rejected', rejected_at: at(-600000), rejected_reason: 'The provider refused the login', credential_source: 'computer'},
          cursor: {installed: true, authenticated: 'missing'},
          grok: {installed: true, authenticated: 'unknown'},
        },
        harnesses: {claude: tool('Claude Code', 'claude', '2.1.300'), codex: tool('Codex', 'codex', '0.162.1'),
          musecode: tool('Muse Code', 'musecode', '1.4.9'), cursor: tool('Cursor Agent', 'cursor', '2026.10.14-941492e-long-build-suffix'),
          grok: tool('Grok Build', 'grok', '1.0.52')}},
      // Named subscriptions from before one login per computer (#1259): still reported, never shown.
      profiles: [
        {name: 'acme', display_name: 'Acme Pro', runtimes: {
          codex: {signed_in: true, account: {email: 'dev@acme.example', plan: 'pro'},
                  weekly: {used_percent: 67, resets_at: at(2 * 86400000), reported_at: at(-120000), status: null, source: 'provider'}},
          claude: {signed_in: false}, musecode: {signed_in: null}}},
        {name: 'beta', display_name: 'beta', runtimes: {codex: {signed_in: false}}},
      ],
      bot_connections: [
        {bot: 'helper', profile: null, source: 'computer', runtime: 'claude', harness: 'claude', signed_in: true, problem: ''},
        {bot: 'ops', profile: null, source: 'computer', runtime: 'codex', harness: 'codex', signed_in: true, problem: ''},
        {bot: 'writer', profile: null, source: 'computer', runtime: 'codex', harness: 'codex', signed_in: true, problem: ''}]};
    const empty = {id: 'empty', label: 'Spare server', operator: 'ana', bots: [], platform: 'linux', version: '0.3.53', last_seen: at(-2000),
      readiness: {runtimes: {codex: {installed: true, authenticated: 'missing'}}, harnesses: {}}, profiles: [], bot_connections: []};
    // Before its first report the server sends readiness_document(None) (backend/store.py).
    const fresh = {id: 'fresh', label: 'Fresh Mac', operator: 'ana', bots: [], version: '', last_seen: at(-2000), readiness: {schema_version: 0, runtimes: {}, bots: {}}};
    const revoked = {id: 'old', label: 'Old laptop', operator: 'ana', bots: [], platform: 'darwin', revoked_at: at(-86400000), last_seen: at(-86400000),
      readiness: {runtimes: {claude: {installed: true, authenticated: 'ready'}}}};
    const me = {id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.com', cloud: true, registered: true};
    const writes = [];
    await page.route('**/*', route => {
      const req = route.request(), p = new URL(req.url()).pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (req.method() !== 'GET') { writes.push({p, body: req.postDataJSON?.()}); return json({}); }
      const config = {version: '0.1.0', app_name: 'Tico'};
      if (p === '/api/me') return json({...me, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json(['helper', 'ops', 'writer'].map(name => ({name, display_name: name[0].toUpperCase() + name.slice(1),
        status: 'active', operator: 'ana', can_manage: true, users: [], bot_owners: [], machine: {runner_id: 'box', label: 'Team box', operator: 'ana'}})));
      if (p === '/api/issues') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'cara', name: 'Cara'}], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/operations') return json({machines: [box, empty, fresh, revoked], services: []});
      // The manifests' capabilities this page reads (runner/harnesses/*.toml).
      if (p === '/api/v2/models') return json({models: [], harnesses: [
        {id: 'claude', runtime: 'claude', name: 'Claude Code', capabilities: {account: 'status-json', weekly_usage: 'turns', browser_sign_in: true}},
        {id: 'codex', runtime: 'codex', name: 'Codex', capabilities: {account: 'app-server', weekly_usage: 'refresh', browser_sign_in: true, sign_out: ['logout']}},
        // terminal_login as the server sends it: the executable and its arguments (clients/harness_manifests.py).
        {id: 'grok', runtime: 'grok', name: 'Grok Build', capabilities: {account: '', weekly_usage: 'manual', terminal_login: 'grok login'}},
        {id: 'cursor', runtime: 'cursor', name: 'Cursor Agent', capabilities: {account: '', weekly_usage: '', terminal_login: 'cursor-agent login', api_keys: 'computer'}},
        {id: 'musecode', runtime: 'musecode', name: 'Muse Code', capabilities: {account: '', weekly_usage: ''}}]});
      if (p === '/api/v2/subscriptions') return json({profiles_by_computer: [], assignments: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('[data-settings-tab=devices]').click();
    const row = id => page.locator(`#set-machines tr[data-computer-row="${id}"]`);
    await row('box').waitFor();
    const cells = async locator => (await locator.locator('td').allInnerTexts()).map(t => t.replace(/\s+/g, ' ').trim());

    // 1. Two cells per row, and nothing else inline: no bots, tool table, sign-in rows or menu.
    assert.deepEqual(await page.locator('#set-machines thead th').allInnerTexts().then(a => a.map(t => t.toLowerCase())),
      ['computer', 'ai tools']);
    assert.equal((await cells(row('box')))[0], 'Team box online Ana · Tico 0.3.52 Update');
    assert.equal((await cells(row('empty')))[0].endsWith('Tico 0.3.53'), true, 'Update only when one waits');
    assert.equal(await page.locator('#set-machines table [role=table], #set-machines table .people-more, #set-machines table [data-model-login]').count(), 0);

    // 4, 5, 8, 9. A chip per connection: tool, thin bar and % left; hover says the rest; a red dot only where bots are stuck.
    const chips = row('box').locator('.machine-chip:not(.machine-chip-add)');
    assert.deepEqual(await chips.allInnerTexts().then(a => a.map(t => t.replace(/\s+/g, ' ').trim())), ['Claude Code', 'Codex 30%', 'Muse Code']);
    assert.deepEqual(await chips.evaluateAll(list => list.map(c => [c.querySelector('meter')?.value ?? null, !!c.querySelector('.machine-chip-dot')])),
      [[null, false], [30, false], [null, true]]);
    assert.match(await chips.nth(1).getAttribute('title'), /^Codex · Computer's login · 30% left · resets \w{3} \d{1,2}:\d{2}\s?[AP]M$/);
    assert.match(await chips.nth(2).getAttribute('title'), /Login expired or rejected/);
    // Nothing for a named subscription, nor for an unused tool.
    assert.doesNotMatch(await row('box').innerText(), /Acme|beta|Cursor|Grok|signed out/i);

    // 24. No connection; never reported yet; revoked hidden.
    assert.equal((await cells(row('empty')))[1], 'No AI connection + Add');
    assert.equal(await row('empty').locator('.machine-chip.attention .machine-chip-dot').count(), 1);
    assert.equal((await cells(row('fresh')))[1], 'Waiting for Fresh Mac');
    assert.equal(await row('old').count(), 0);

    // 10. A click on the row opens the pop-up, sections in order.
    await row('box').locator('.machine-facts').click();
    const dialog = page.locator('#set-machines dialog.computer-detail[open]');
    await dialog.waitFor();
    assert.deepEqual(await dialog.locator('section h3').allInnerTexts().then(a => a.map(t => t.toLowerCase())),
      ['connections', 'screen', 'bots on this computer', 'versions and updates', 'settings']);
    // 7. One line per AI tool, the computer's login, used by all its bots on that tool.
    const conns = dialog.locator('[data-connection]');
    assert.deepEqual(await conns.evaluateAll(list => list.map(c => c.dataset.connection)), ['own:claude', 'own:codex', 'own:musecode']);
    const codex = dialog.locator('[data-connection="own:codex"]');
    assert.match(await codex.innerText(), /Codex\s+Computer's login/);
    assert.match(await codex.innerText(), /Weekly 30% left/);
    assert.match(await codex.innerText(), /Bots: Ops, Writer/);
    assert.equal(await codex.locator('meter').evaluateAll(m => m.map(x => x.value)).then(v => v.join()), '30');
    assert.match(await dialog.locator('[data-connection="own:musecode"]').innerText(), /Login expired or rejected[\s\S]*The provider refused the login/);
    assert.equal(await dialog.locator('[data-detail-section=connections] > :last-child [data-add-connection=box]').isEnabled(), true, 'Add connection at the end');
    // Installed tools no bot uses, signed in only from a terminal: their command is in the Add connection pop-up, with no
    // chip and no line.
    const add = page.locator('dialog.computer-add-dialog[open]');
    for (const [tool, command] of [['grok', 'grok login'], ['cursor', 'cursor-agent login']]) {
      await dialog.locator('[data-add-connection=box]').click();
      await add.locator(`[data-add-tool=${tool}]`).click();
      const line = add.locator(`[data-add-terminal=${tool}]`);
      assert.deepEqual([await line.locator('code').textContent(), await line.locator('[data-machine-copy-login]').getAttribute('data-machine-copy-login')], [command, command]);
      assert.equal(await add.locator('[data-add-own]').isDisabled(), true);
      await add.locator('[data-add-close]').first().click();
      await add.waitFor({state: 'detached'});
    }
    assert.equal(await dialog.locator('[data-connection][data-machine-tool=grok], [data-connection][data-machine-tool=cursor]').count(), 0);
    assert.doesNotMatch(await row('box').locator('.machine-chips').innerText(), /Grok|Cursor/);
    // Usage reads as what is left, everywhere in the pop-up and the list.
    assert.doesNotMatch(await page.locator('#set-machines').innerText(), /% used/);

    // 13. Each bot's actual connection, read-only, with the way to change it.
    const bots = dialog.locator('[data-detail-section=bots]');
    assert.deepEqual(await bots.locator('li').allInnerTexts().then(a => a.map(t => t.replace(/\s+/g, ' ').trim())),
      ["Helper Runs on Claude Code · Computer's login", "Ops Runs on Codex · Computer's login", "Writer Runs on Codex · Computer's login"]);
    assert.equal(await bots.locator('select, input').count(), 0);
    assert.equal(await bots.locator('a[data-computer-bots=box]').innerText(), 'Assign on the Bots page');

    // 12. Tool and Version have their own room at 1024 px and wide.
    for (const width of [1440, 1024]) {
      await page.setViewportSize({width, height: 900});
      const [first, version] = await dialog.locator('.machine-tools-2 [role=columnheader]').evaluateAll(list => list.map(h => h.getBoundingClientRect()).map(r => [r.left, r.right]));
      assert.ok(first[1] <= version[0], `headers apart at ${width}px: ${JSON.stringify([first, version])}`);
    }
    const version = dialog.locator('[data-machine-tool=cursor] .machine-tool-version');
    assert.ok(await version.evaluate(el => el.scrollWidth > el.clientWidth), 'a long version is cut off, the full one in its title');

    // Settings: inbox-only saves; Revoke is here and not on the row.
    await dialog.locator('[data-inbox-only=box]').check();
    for (let i = 0; i < 100 && !writes.some(w => w.p === '/api/v2/runners/box/inbox-only'); i++) await page.waitForTimeout(20);
    assert.deepEqual(writes.find(w => w.p === '/api/v2/runners/box/inbox-only').body, {enabled: true});
    assert.equal(await dialog.locator('[data-detail-section=settings] [data-computer-remove=box]').innerText(), 'Revoke computer');
    await dialog.locator('[data-detail-close]').click();
    assert.equal(await page.locator('#set-machines dialog.computer-detail[open]').count(), 0);

    // API-key billing is visible before an upgrade moves bots to the computer login; account switching stays available.
    const account = box.readiness.runtimes.codex.account;
    box.readiness.runtimes.codex.account = {kind: 'api_key'};
    box.bot_connections[1].warning = 'API credit is not checked here. Check billing or switch to the intended subscription.';
    await page.evaluate(() => loadSettings('devices'));
    await row('box').locator('.machine-facts').click();
    await dialog.locator('[data-login-warning=codex]').waitFor();
    assert.match(await dialog.locator('[data-login-warning=codex]').innerText(), /API credit is not checked/);
    assert.equal(await codex.getByRole('button', {name: 'Switch to subscription', exact: true}).isEnabled(), true);
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await dialog.locator('[data-login-warning=codex]').isVisible(), true);
    assert.equal(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth), true);
    await dialog.locator('[data-detail-close]').click();
    box.readiness.runtimes.codex.account = account;
    delete box.bot_connections[1].warning;
    await page.setViewportSize({width: 1440, height: 900});
    await page.evaluate(() => loadSettings('devices'));

    // 24. The pop-up of a computer with no connection.
    await row('empty').locator('[data-no-connection]').click();
    assert.match(await dialog.locator('[data-detail-section=connections]').innerText(), /Bots here can't run until a connection is added/);
    assert.equal(await dialog.locator('[data-add-connection=empty]').count(), 1);
    await page.keyboard.press('Escape');

    // 23. Offline: last seen on the row, chips keep their reading, actions wait for the computer.
    box.last_seen = at(-3 * 3600000);
    await page.evaluate(() => loadSettings());
    await row('box').locator('[data-machine-status]', {hasText: 'offline'}).waitFor();
    assert.equal(await row('box').locator('[data-machine-status]').innerText(), 'offline 3h ago');
    assert.equal(await row('box').locator('.machine-chip meter').first().evaluate(m => m.value), 30);
    await row('box').locator('.machine-name').click();
    await dialog.locator('[data-computer-offline]').waitFor();
    assert.match(await dialog.locator('[data-computer-offline]').innerText(), /Computer is offline/);
    assert.equal(await dialog.locator('[data-model-login]:enabled, [data-add-connection]:enabled').count(), 0);
    assert.equal(await dialog.locator('[data-add-connection]').getAttribute('title'), 'Computer is offline');
    await page.keyboard.press('Escape');

    // Desktop apps (a Mac's app driver), under Versions and updates: only when the computer reports `appdriver`, one row
    // per app with its state and the driver's detail; still titled on a phone. Bots sharing one ChatGPT sign-in take
    // turns (backend/views.py shared_logins): each login a line stands for says so, naming the bots this reader may see.
    box.last_seen = at(-2000);
    await page.evaluate(() => loadSettings());
    await row('box').locator('.machine-name').click();
    await dialog.locator('[data-connection]').first().waitFor();
    assert.equal(await dialog.locator('.machine-apps').count(), 0, 'no driver, no Desktop apps');
    assert.equal(await dialog.locator('[data-shared-login]').count(), 0, 'no shared sign-in, no note');
    box.readiness.appdriver = {trusted: false, macos: '15.1', detail: '', apps: {grok: {installed: true, version: '1.2.0', running: true,
      signed_in: true, background_ok: false, ready: false, state: 'no_permission', detail: 'Turn on tico-appdriver in System Settings > Privacy & Security > Accessibility'}}};
    box.shared_logins = [{runtime: 'codex', profile: '', display_name: '', count: 3, bots: ['helper']}];
    await page.evaluate(() => loadSettings());
    const apps = dialog.locator('[data-detail-section=versions] [role=table][aria-label="Desktop apps on Team box"]');
    await apps.waitFor();
    assert.deepEqual(await apps.locator('[data-machine-app="grok"] [role=cell]').allInnerTexts().then(a => a.map(t => t.replace(/\s+/g, ' ').trim())),
      ['Grok', '1.2.0', 'Needs Accessibility permission Turn on tico-appdriver in System Settings > Privacy & Security > Accessibility']);
    assert.equal(await dialog.locator('[data-connection="own:codex"] [data-shared-login="codex:"]').innerText(), '3 bots share this sign-in (Helper and 2 more). They take turns: one Codex run at a time.');
    assert.equal(await dialog.locator('[data-connection="own:claude"] [data-shared-login]').count(), 0);
    await page.setViewportSize({width: 390, height: 844});
    assert.ok(await apps.locator('[role=columnheader]', {hasText: 'Desktop apps'}).isVisible(), 'titled on a phone');
    assert.ok(await apps.evaluate(el => el.scrollWidth <= el.clientWidth + 1), 'fits a phone');
    await page.setViewportSize({width: 1440, height: 900});
    await page.keyboard.press('Escape');

    // 25. Revoked computers on request; Revoke lives in the pop-up only.
    await page.locator('#set-machines [data-show-revoked-computers]').check();
    await row('old').waitFor();
    assert.equal(await row('old').locator('[data-machine-status]').innerText(), 'revoked');
    assert.equal(await page.locator('#set-machines table [data-computer-remove]').count(), 0);

    // 22. A member who is neither admin nor operator sees everything and can change nothing. The server sends them no
    // accounts and no usage of the computer's own login.
    box.last_seen = at(-2000);
    for (const state of Object.values(box.readiness.runtimes)) { delete state.account; delete state.weekly; }
    Object.assign(box, {may_manage: false});
    await page.evaluate(() => { S.me = {...S.me, id: 'cara', role: 'member', bot_admin: false}; return loadSettings(); });
    await row('box').locator('.machine-name').click();
    await dialog.locator('[data-connection]').first().waitFor();
    assert.deepEqual(await dialog.locator('[data-connection]').evaluateAll(list => list.map(c => c.dataset.connection)),
      ['own:claude', 'own:codex', 'own:musecode']);
    assert.doesNotMatch(await dialog.innerText(), /@/, 'no account emails');
    assert.equal(await dialog.locator('[data-model-login], [data-add-connection], [data-machine-copy-login], [data-computer-remove], [data-inbox-only], [data-member-bots], [data-computer-screen], [data-harness-choice], [data-performance-mode]').count(), 0);
    await page.keyboard.press('Escape');

    assert.deepEqual(errors, []);
    console.log('PASS: compact computer rows, connection chips, detail pop-up, one login per tool, bots, header fix, empty/offline/waiting/revoked/member, desktop apps, shared sign-ins');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
