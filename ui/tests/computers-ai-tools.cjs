// Settings > Computers: one compact row per computer (status dot and name, bots, a column per AI tool, what it does now) and the
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
// Read shipped capabilities so the browser flow cannot drift from its manifest.
const manifestCaps = JSON.parse(require('node:child_process').execFileSync('python3', ['-c',
  'import json; from clients.harness_manifests import load; print(json.dumps({k: v["capabilities"] for k, v in load().items()}))'],
  {cwd: require('node:path').resolve(__dirname, '../..'), encoding: 'utf8'}));

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
      turn_summary: '3 running · 1 starting · 37 waiting for shared login (oldest 2 h) · 5 queued',
      turn_counts: {running: 3, starting: 1, waiting: 37, queued: 5, waits: []},
      last_seen_at: at(-3000), needed_runtimes: ['claude', 'codex'], bot_runtimes: ['claude', 'codex'], harness_choices: {grok: false},
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
    const empty = {id: 'empty', label: 'Spare server', operator: 'ana', bots: [], platform: 'linux', version: '0.3.53', last_seen_at: at(-2000),
      readiness: {runtimes: {codex: {installed: true, authenticated: 'missing'}}, harnesses: {}}, profiles: [], bot_connections: []};
    // Before its first report the server sends readiness_document(None) (backend/store.py).
    const fresh = {id: 'fresh', label: 'Fresh Mac', operator: 'ana', bots: [], version: '', last_seen_at: at(-2000), readiness: {schema_version: 0, runtimes: {}, bots: {}}};
    const revoked = {id: 'old', label: 'Old laptop', operator: 'ana', bots: [], platform: 'darwin', revoked_at: at(-86400000), last_seen_at: at(-86400000),
      readiness: {runtimes: {claude: {installed: true, authenticated: 'ready'}}}};
    const me = {id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.com', cloud: true, registered: true};
    const writes = [];
    let presenceMode = 'fixed', presenceReads = 0, presenceOffset = 0;
    const heldPresence = [];
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
      if (p === '/api/v2/operations') {
        presenceReads++;
        if (presenceMode === 'slow') { heldPresence.push(route); return; }
        if (presenceMode === 'failure') return json({error: 'Unavailable'}, 503);
        if (presenceMode === 'live') box.last_seen_at = at(presenceOffset - 3000);
        return json({machines: [box, empty, fresh, revoked], services: []});
      }
      // The manifests' capabilities this page reads (runner/harnesses/*.toml).
      if (p === '/api/v2/providers') return json({enabled: ['openai'], default: {runtime: 'codex', model: 'test-model'},
        providers: [{id: 'openai', label: 'OpenAI'}]});
      if (p === '/api/v2/models') return json({models: [{id: 'test-model', label: 'Test model', provider: 'openai', runtime: 'codex'}], harnesses: [
        {id: 'claude', runtime: 'claude', name: 'Claude Code', capabilities: {account: 'status-json', weekly_usage: 'turns', browser_sign_in: true}},
        {id: 'codex', runtime: 'codex', name: 'Codex', capabilities: {account: 'app-server', weekly_usage: 'refresh', browser_sign_in: true, sign_out: ['logout']}},
        // terminal_login as the server sends it: the executable and its arguments (clients/harness_manifests.py).
        {id: 'grok', runtime: 'grok', name: 'Grok Build', capabilities: manifestCaps.grok},
        {id: 'cursor', runtime: 'cursor', name: 'Cursor Agent', capabilities: manifestCaps.cursor},
        {id: 'musecode', runtime: 'musecode', name: 'Muse Code', capabilities: {account: '', weekly_usage: ''}}]});
      if (p === '/api/v2/subscriptions') return json({profiles_by_computer: [], assignments: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('[data-settings-tab=devices]').click();
    const row = id => page.locator(`#set-machines tr[data-computer-row="${id}"]`);
    await row('box').waitFor();
    // Opening the tab paints the list, then re-reads presence and says "checking…" until that answers.
    await page.waitForFunction(() => SETTINGS_PRESENCE.at && !SETTINGS_PRESENCE.checking);
    const cells = async locator => (await locator.locator('td').allInnerTexts()).map(t => t.replace(/\s+/g, ' ').trim());

    // 1. One row per computer: status and name, bots, a column per AI tool (this server keeps no subscriptions, so each
    // shows the computer's login), and what it does now. No tool table, sign-in rows or menu inline.
    assert.deepEqual(await page.locator('#set-machines thead th').allInnerTexts().then(a => a.map(t => t.toLowerCase())),
      ['computer', 'bots', 'claude code', 'codex', 'muse code', 'now']);
    assert.equal((await cells(row('box')))[0], 'online Team box Update');
    assert.equal(await row('box').locator('.machine-bot-count').innerText(), '3');
    assert.equal(await row('box').locator('[data-machine-now]').innerText(), '3 running · 1 starting · 5 queued · 37 waiting');
    assert.equal((await cells(row('empty')))[0], 'online Spare server', 'Update only when one waits');
    assert.equal(await row('empty').locator('[data-machine-now]').innerText(), 'idle');
    assert.equal(await page.locator('#set-machines table [role=table], #set-machines table .people-more, #set-machines table [data-model-login]').count(), 0);

    // 4, 5, 8, 9. A chip per connection under its tool's column: its state, thin bar and % left; hover says the rest;
    // a red dot only where bots are stuck.
    const chips = row('box').locator('.machine-chip');
    assert.deepEqual(await chips.allInnerTexts().then(a => a.map(t => t.replace(/\s+/g, ' ').trim())), ['Signed in', 'Signed in 30%', 'Expired']);
    assert.deepEqual(await chips.evaluateAll(list => list.map(c => [c.querySelector('meter')?.value ?? null, !!c.querySelector('.machine-chip-dot')])),
      [[null, false], [30, false], [null, true]]);
    assert.match(await chips.nth(1).getAttribute('title'), /^Codex · Computer's login · 30% left · resets \w{3} \d{1,2}:\d{2}\s?[AP]M$/);
    assert.match(await chips.nth(2).getAttribute('title'), /Login expired or rejected/);
    // Nothing for a named subscription, nor for an unused tool.
    assert.doesNotMatch(await row('box').innerText(), /Acme|beta|Cursor|Grok|signed out/i);

    // 24. No connection; never reported yet; revoked hidden.
    assert.deepEqual((await cells(row('empty'))).slice(2, 5), ['—', '—', '—']);
    assert.equal(await row('fresh').locator('[data-machine-waiting]').innerText(), 'Waiting');
    assert.equal(await row('old').count(), 0);

    // 10. A click on the row opens the pop-up, sections in order.
    await row('box').locator('.machine-name').click();
    const dialog = page.locator('#set-machines dialog.computer-detail[open]');
    await dialog.waitFor();
    assert.equal(await dialog.locator('[data-turn-counts]').innerText(), box.turn_summary);
    assert.deepEqual(await dialog.locator('section h3').allInnerTexts().then(a => a.map(t => t.toLowerCase())),
      ['connections', 'screen', 'bots on this computer', 'versions and updates', 'settings']);
    // 7. One line per AI tool, the computer's login, used by all its bots on that tool.
    const conns = dialog.locator('[data-connection]');
    assert.deepEqual(await conns.evaluateAll(list => list.map(c => c.dataset.connection)), ['own:claude', 'own:codex', 'own:musecode']);
    const codex = dialog.locator('[data-connection="own:codex"]');
    assert.deepEqual(await conns.locator('.computer-conn-head').allInnerTexts(), ['Claude Code', 'Codex', 'Muse Code']);
    assert.match(await codex.innerText(), /Weekly 30% left/);
    assert.match(await codex.innerText(), /Bots: Ops, Writer/);
    assert.equal(await codex.locator('meter').evaluateAll(m => m.map(x => x.value)).then(v => v.join()), '30');
    assert.match(await dialog.locator('[data-connection="own:musecode"]').innerText(), /Login expired or rejected[\s\S]*The provider refused the login/);
    assert.equal(await dialog.locator('[data-detail-section=connections] > :last-child [data-add-connection=box]').isEnabled(), true, 'Add connection at the end');
    // Both tools can sign in the computer's one login from Add connection.
    const add = page.locator('dialog.computer-add-dialog[open]');
    for (const tool of ['grok', 'cursor']) {
      await dialog.locator('[data-add-connection=box]').click();
      await add.locator(`[data-add-tool=${tool}]`).click();
      assert.equal(await add.locator(`[data-add-terminal=${tool}]`).count(), 0);
      assert.equal(await add.locator('[data-add-own]').isEnabled(), true);
      assert.equal(await add.locator('input[name=profile]').count(), 0);
      if (tool === 'cursor') {
        for (const viewport of [{width:1440,height:900}, {width:390,height:680}]) {
          await page.setViewportSize(viewport);
          if (process.env.SHOTS) await page.screenshot({path: `${process.env.SHOTS}/cursor-${viewport.width}.png`});
          const rect = await add.evaluate(d => {
            const box = el => el.getBoundingClientRect().toJSON();
            return {dialog:box(d),header:box(d.querySelector('header')),back:box(d.querySelector('[data-add-back]')),
              action:box(d.querySelector('[data-add-own]')),body:d.querySelector('.tmodal-body').getBoundingClientRect().toJSON()};
          });
          assert.ok(rect.back.top >= rect.header.top && rect.back.bottom <= rect.header.bottom, 'Back is in the header');
          assert.ok(rect.action.left >= rect.body.left + 12 && rect.action.right <= rect.body.right - 12, 'sign-in action stays inside the padding');
          assert.ok(rect.dialog.right <= viewport.width && rect.dialog.bottom <= viewport.height);
        }
        await page.setViewportSize({width:1440,height:900});
      }
      await add.locator('[data-add-close]').first().click();
      await add.waitFor({state: 'detached'});
    }
    assert.equal(await dialog.locator('[data-connection][data-machine-tool=grok], [data-connection][data-machine-tool=cursor]').count(), 0);
    assert.doesNotMatch(await row('box').innerText(), /Grok|Cursor/);
    // Usage reads as what is left, everywhere in the pop-up and the list.
    assert.doesNotMatch(await page.locator('#set-machines').innerText(), /% used/);

    // 13. The bots on this computer, read-only, with the way to change them.
    const bots = dialog.locator('[data-detail-section=bots]');
    assert.deepEqual(await bots.locator('li').allInnerTexts().then(a => a.map(t => t.replace(/\s+/g, ' ').trim())),
      ['Helper', 'Ops', 'Writer']);
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
    await row('box').locator('.machine-name').click();
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
    await row('empty').locator('.machine-name').click();
    assert.match(await dialog.locator('[data-detail-section=connections]').innerText(), /Bots here can't run until a connection is added/);
    assert.equal(await dialog.locator('[data-add-connection=empty]').count(), 1);
    await page.keyboard.press('Escape');

    // 23. Offline: last seen on the row, chips keep their reading, actions wait for the computer.
    box.last_seen_at = at(-3 * 3600000);
    await page.evaluate(() => loadSettings());
    await row('box').locator('[data-machine-status]', {hasText: 'offline'}).waitFor();
    assert.equal(await row('box').locator('[data-machine-status]').innerText(), 'offline · last seen 3h ago');
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
    box.last_seen_at = at(-2000);
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
    assert.equal(await dialog.locator('[data-turn-counts]').innerText(), box.turn_summary);
    assert.ok(await dialog.locator('[data-turn-counts]').evaluate(el => el.scrollWidth <= el.clientWidth + 1), 'turn counts wrap on a phone');
    await page.setViewportSize({width: 1440, height: 900});
    await page.keyboard.press('Escape');

    // Presence refresh protects the core recovery flow: cached heartbeats cannot disable a live computer.
    box.readiness.runtimes.codex.authenticated = 'missing';
    await page.clock.install({time: now});
    await page.clock.pauseAt(now + 1000);
    presenceMode = 'live';
    await page.evaluate(() => { settingsMachinesWatch(); return settingsMachinesRefresh(); });
    // Polling keeps keyboard focus on ordinary controls, including when the row's status changes.
    const rowButton = row('box').locator('.machine-name');
    await rowButton.focus();
    await page.evaluate(() => settingsMachinesRefresh());
    assert.equal(await rowButton.evaluate(node => node === document.activeElement), true, 'list control keeps focus');
    const beforeIdle = presenceReads;
    presenceOffset = 65000;
    await page.clock.fastForward(65000);
    await page.evaluate(() => SETTINGS_PRESENCE.pending);
    assert.ok(presenceReads > beforeIdle, 'Computers refreshes while visible');
    await row('box').locator('.machine-name').click();
    await page.evaluate(() => SETTINGS_PRESENCE.pending);
    const online = async () => {
      assert.match(await row('box').locator('[data-machine-status]').innerText(), /^online/);
      assert.match(await dialog.locator('[data-computer-status]').innerText(), /^online/);
      assert.equal(await dialog.locator('[data-add-connection=box]').isEnabled(), true);
      assert.equal(await dialog.locator('[data-model-login][data-runtime=codex]').isEnabled(), true);
    };
    await online();
    const signIn = dialog.locator('[data-model-login][data-runtime=codex]');
    await signIn.focus();
    const openedReads = presenceReads;
    presenceOffset = 130000;
    await page.clock.fastForward(65000);
    await page.evaluate(() => SETTINGS_PRESENCE.pending);
    await online();
    assert.equal(await signIn.evaluate(node => node === document.activeElement), true, 'popup control keeps focus across polling');
    await dialog.evaluate(node => {
      window.computerClosed = new Promise(resolve => node.addEventListener('close', () => resolve(), {once: true}));
    });
    await page.keyboard.press('Escape');
    await page.clock.runFor(100);
    await page.evaluate(() => window.computerClosed);
    assert.equal(await rowButton.evaluate(node => node === document.activeElement), true, 'Escape returns to the refreshed detail opener');
    // A row-area click has no focused button; close still returns to the computer name.
    await row('empty').locator('.machine-name').focus();
    await row('box').locator('[data-machine-status]').click();
    await page.evaluate(() => SETTINGS_PRESENCE.pending);
    await dialog.evaluate(node => {
      window.computerClosed = new Promise(resolve => node.addEventListener('close', () => resolve(), {once: true}));
    });
    await page.keyboard.press('Escape');
    await page.clock.runFor(100);
    await page.evaluate(() => window.computerClosed);
    assert.equal(await rowButton.evaluate(node => node === document.activeElement), true, 'row-area Escape returns to the computer name');
    // Dispatching click without focus also covers browsers that do not focus clicked buttons.
    await row('empty').locator('.machine-name').focus();
    await rowButton.dispatchEvent('click');
    await page.evaluate(() => SETTINGS_PRESENCE.pending);
    await dialog.evaluate(node => {
      window.computerClosed = new Promise(resolve => node.addEventListener('close', () => resolve(), {once: true}));
    });
    await page.keyboard.press('Escape');
    await page.clock.runFor(100);
    await page.evaluate(() => window.computerClosed);
    assert.equal(await rowButton.evaluate(node => node === document.activeElement), true, 'unfocused name click returns to the computer name');
    // Add connection opens from the pop-up; polling replaces its opener and Escape returns to it.
    await rowButton.click();
    await page.evaluate(() => SETTINGS_PRESENCE.pending);
    const detailAdd = dialog.locator('[data-add-connection=box]');
    await signIn.focus();
    await detailAdd.dispatchEvent('click');
    await add.waitFor();
    await page.evaluate(() => settingsMachinesRefresh());
    await page.keyboard.press('Escape');
    await page.clock.runFor(100);
    await add.waitFor({state: 'detached'});
    assert.equal(await detailAdd.evaluate(node => node === document.activeElement), true, 'Escape returns to the refreshed detail Add opener');
    assert.ok(presenceReads > openedReads, 'open and reopen read presence');
    await online();

    // Carry the actual draft node through repaint, including focus and the selected text range.
    await dialog.locator('[data-connection="own:codex"]').evaluate(node => {
      const field = document.createElement('textarea'); field.dataset.presenceDraft = ''; node.appendChild(field);
      field.value = 'unfinished connection'; field.focus(); field.setSelectionRange(3, 8);
      window.presenceDraft = field;
    });
    await page.evaluate(() => settingsMachinesRefresh());
    assert.deepEqual(await page.evaluate(() => {
      const field = document.querySelector('[data-presence-draft]');
      return [field === window.presenceDraft, field.value, document.activeElement === field, field.selectionStart, field.selectionEnd];
    }), [true, 'unfinished connection', true, 3, 8]);

    await dialog.locator('[data-add-connection=box]').click();
    await add.locator('[data-add-tool=codex]').click();
    await add.locator('[data-add-step=kind]').evaluate(node => {
      const field = document.createElement('textarea'); field.dataset.addDraft = ''; node.appendChild(field);
      field.value = 'unfinished add connection'; field.focus(); field.setSelectionRange(2, 7);
      window.addDraft = field;
    });
    await page.evaluate(() => settingsMachinesRefresh());
    assert.deepEqual(await page.evaluate(() => {
      const field = document.querySelector('[data-add-draft]');
      return [field === window.addDraft, field.value, document.activeElement === field, field.selectionStart, field.selectionEnd];
    }), [true, 'unfinished add connection', true, 2, 7]);
    await add.locator('[data-add-close]').first().click();
    await add.waitFor({state: 'detached'});
    await signIn.focus();

    // A slow/failed read preserves the last confirmed state, even when its heartbeat ages past a minute.
    await page.evaluate(() => clearTimeout(SETTINGS_PRESENCE.timer));
    presenceMode = 'slow';
    await page.evaluate(() => { void settingsMachinesRefresh(); });
    assert.equal(await page.evaluate(() => SETTINGS_PRESENCE.checking), true);
    assert.equal(await signIn.evaluate(node => node === document.activeElement), true, 'focus survives check start');
    assert.match(await dialog.locator('[data-computer-status]').innerText(), /checking/);
    presenceOffset = 200000;
    await page.clock.fastForward(70000);
    await page.evaluate(() => SETTINGS_PRESENCE.pending);
    await online();
    assert.match(await row('box').locator('[data-machine-status]').innerText(), /checking/);
    assert.equal(await signIn.evaluate(node => node === document.activeElement), true, 'focus survives deadline repaint');
    presenceMode = 'failure';
    await page.evaluate(() => settingsMachinesRefresh());
    await online();
    assert.match(await dialog.locator('[data-computer-status]').innerText(), /checking/);

    // An outdated online computer remains in its banner while presence is stale.
    await page.evaluate(() => {
      SETTINGS_DATA.machines.find(machine => machine.id === 'box').update = {outdated: true, server_release: '0.3.53'};
      renderSettingsMachines();
    });
    assert.equal(await page.locator('[data-runner-outdated-banner]').count(), 1);

    // Only a successful read can confirm disconnect; both views recover from the next heartbeat.
    presenceMode = 'fixed';
    await page.evaluate(() => settingsMachinesRefresh());
    assert.match(await row('box').locator('[data-machine-status]').innerText(), /^offline/);
    assert.match(await dialog.locator('[data-computer-status]').innerText(), /^offline/);
    assert.equal(await dialog.locator('[data-add-connection=box]').isDisabled(), true);
    presenceMode = 'live';
    await page.evaluate(() => settingsMachinesRefresh());
    await online();
    for (const held of heldPresence.splice(0)) await held.fulfill({status: 200, contentType: 'application/json',
      body: JSON.stringify({machines: [{...box, last_seen_at: at(-3 * 3600000)}], services: []})});
    await online(); // A late answer to the timed-out read cannot replace the newer heartbeat.
    await dialog.locator('[data-presence-draft]').evaluate(node => node.remove());
    await page.keyboard.press('Escape');

    // A first-load presence timeout is local to Computers; the rest of Settings still loads.
    presenceMode = 'slow';
    await page.evaluate(() => {
      SETTINGS_DATA.loaded = false; SETTINGS_DATA.machines = []; SETTINGS_PRESENCE.at = 0;
      window.initialSettingsLoad = loadSettings();
    });
    await page.clock.fastForward(10001);
    await page.evaluate(() => window.initialSettingsLoad);
    assert.equal(await page.evaluate(() => SETTINGS_DATA.loaded && SETTINGS_DATA.people.length > 0), true);
    assert.equal(await page.locator('#machine-operator').count(), 1);
    assert.match(await page.locator('[data-machines-list]').innerText(), /presence unavailable.*checking/);
    assert.equal(await page.locator('#set-bots > .err, #set-machines > .err').count(), 0);
    presenceMode = 'live';
    await page.evaluate(() => settingsMachinesRefresh());
    for (const held of heldPresence.splice(0)) await held.fulfill({status: 200, contentType: 'application/json',
      body: JSON.stringify({machines: [], services: []})});
    // Leaving the tab stops its timer; returning starts it and requests fresh presence.
    await page.locator('[data-settings-tab=bots]').click();
    const afterLeaving = presenceReads;
    await page.clock.fastForward(65000);
    assert.equal(presenceReads, afterLeaving);
    await page.locator('[data-settings-tab=devices]').click();
    await page.evaluate(() => SETTINGS_PRESENCE.pending);
    assert.ok(presenceReads > afterLeaving);
    await page.clock.resume();
    presenceMode = 'fixed';

    // On a phone the table scrolls inside its card, never the page; the queue line stays in the pop-up.
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'no sideways page scroll');
    assert.ok(await page.locator('#set-machines .scroll').evaluate(el => el.scrollWidth > el.clientWidth), 'the table scrolls in its card');
    await page.setViewportSize({width: 1440, height: 900});

    // 25. Revoked computers on request; Revoke lives in the pop-up only.
    await page.locator('#set-machines [data-show-revoked-computers]').check();
    await row('old').waitFor();
    assert.equal(await row('old').locator('[data-machine-status]').innerText(), 'revoked');
    assert.equal(await page.locator('#set-machines table [data-computer-remove]').count(), 0);

    // 22. A member who is neither admin nor operator sees everything and can change nothing. The server sends them no
    // accounts and no usage of the computer's own login.
    box.last_seen_at = at(-2000);
    for (const state of Object.values(box.readiness.runtimes)) { delete state.account; delete state.weekly; }
    Object.assign(box, {may_manage: false});
    await page.evaluate(() => { S.me = {...S.me, id: 'cara', role: 'member', bot_admin: false}; return loadSettings(); });
    await row('box').locator('.machine-name').click();
    await dialog.locator('[data-connection]').first().waitFor();
    assert.deepEqual(await dialog.locator('[data-connection]').evaluateAll(list => list.map(c => c.dataset.connection)),
      ['own:claude', 'own:codex', 'own:musecode']);
    assert.doesNotMatch(await dialog.innerText(), /@/, 'no account emails');
    assert.equal(await dialog.locator('[data-model-login], [data-add-connection], [data-machine-copy-login], '
      + '[data-computer-remove], [data-inbox-only], [data-member-bots], [data-turns-at-once], [data-computer-screen], '
      + '[data-harness-choice], [data-performance-mode]').count(), 0);
    await page.keyboard.press('Escape');

    assert.deepEqual(errors, []);
    console.log('PASS: compact computer rows, connection chips, detail pop-up, one login per tool, bots, header fix, empty/offline/waiting/revoked/member, desktop apps, shared sign-ins, live/stale presence and draft preservation');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
