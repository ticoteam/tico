// Settings > Computers > AI tools (#610): one aligned row per tool with its installed version, enabled, sign-in and
// account, and usage; a line per named subscription. Covers each sign-in state (signed in as <email> · <plan>, signed
// out, login expired or rejected, checking, couldn't check with the time), real usage against unavailable usage (never
// 0% or a full allowance), an account reading older than its refresh marked with when it was last checked, the 5-hour
// window label, the computer's own login's windows (and Claude's before its first turn here), several profiles, a long version kept to its cell, revoked computers hidden by default, and no account
// where the server sent none. Every request is intercepted.
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
    const chip = (name, runtime, version, extra = {}) => ({name, runtime, installed: !!version, version: version || '', managed: true, state: 'idle',
      wanted: !!version, pinned: false, update_available: false, detail: '', ...extra});
    const box = {id: 'box', label: 'Team box', operator: 'ana', bots: ['helper'], platform: 'linux', last_seen: at(-3000),
      needed_runtimes: ['claude', 'codex'], bot_runtimes: ['claude', 'codex'], harness_choices: {grok: false},
      readiness: {
        runtimes: {
          claude: {installed: true, authenticated: 'ready', account: {email: 'chris@acme.example', plan: 'max', org: 'Acme', checked_at: at(-60000)}},
          // The computer's own Codex login, read while idle (runner-rs/src/accounts.rs): both windows with percentages.
          codex: {installed: true, authenticated: 'ready',
                  weekly: {used_percent: 12, resets_at: at(3 * 86400000), reported_at: at(-120000), status: null},
                  five_hour: {used_percent: 40, resets_at: at(2 * 3600000), reported_at: at(-120000), status: null}},
          cursor: {installed: true, authenticated: 'missing', detail: 'Cursor login required'},
          musecode: {installed: true, authenticated: 'rejected', rejected_at: at(-600000), rejected_reason: 'The provider refused the login', credential_source: 'computer'},
          grok: {installed: true, authenticated: 'unknown'},
          gemini: {installed: true, authenticated: 'failed', detail: 'Gemini sign-in could not be checked'},
        },
        harnesses: {
          'claude': chip('Claude Code', 'claude', '2.1.300'), 'codex': chip('Codex', 'codex', '0.162.1'),
          'cursor': chip('Cursor Agent', 'cursor', '2026.10.14-941492e-long-build-suffix'), 'musecode': chip('Muse Code', 'musecode', '1.4.9'),
          'grok': chip('Grok Build', 'grok', '1.0.52'), 'gemini': chip('Gemini CLI', 'gemini', '0.59.0'),
        }},
      profiles: [
        {name: 'acme', display_name: 'Acme Max', runtimes: {
          claude: {signed_in: true, account: {email: 'ops@acme.example', plan: 'max'},
                   weekly: {used_percent: null, resets_at: at(2 * 86400000), reported_at: at(-3600000), status: 'allowed', source: 'provider'},
                   five_hour: {used_percent: null, resets_at: at(3 * 3600000), reported_at: at(-60000), status: 'allowed', source: 'provider'}},
          codex: {signed_in: true, account: {email: 'dev@acme.example', plan: 'pro', kind: 'subscription', checked_at: at(-14 * 60000)},
                  weekly: {used_percent: 38, resets_at: at(2 * 86400000), reported_at: at(-3600000), status: null, source: 'provider'},
                  five_hour: {used_percent: 90, resets_at: at(3600000), reported_at: at(-60000), status: null, source: 'provider'}}}},
        {name: 'beta', display_name: 'beta', runtimes: {
          claude: {signed_in: false},
          codex: {signed_in: null, weekly: null}}},
      ]};
    const revoked = {id: 'old', label: 'Old laptop', operator: 'ana', bots: [], platform: 'darwin', revoked_at: at(-86400000), last_seen: at(-86400000),
      readiness: {runtimes: {claude: {installed: true, authenticated: 'ready'}}}};
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
      if (p === '/api/v2/operations') return json({machines: [box, revoked], services: []});
      // The manifests' capabilities this page reads (runner/harnesses/*.toml): where an account and usage come from.
      if (p === '/api/v2/models') return json({models: [], harnesses: [
        {id: 'claude', runtime: 'claude', name: 'Claude Code', capabilities: {account: 'status-json', weekly_usage: 'turns', terminal_login: ['auth', 'login']}},
        {id: 'codex', runtime: 'codex', name: 'Codex', capabilities: {account: 'app-server', weekly_usage: 'refresh'}},
        {id: 'gemini', runtime: 'gemini', name: 'Gemini CLI', capabilities: {account: '', weekly_usage: 'manual'}},
        {id: 'grok', runtime: 'grok', name: 'Grok Build', capabilities: {account: '', weekly_usage: 'manual', terminal_login: ['login']}},
        {id: 'cursor', runtime: 'cursor', name: 'Cursor Agent', capabilities: {account: '', weekly_usage: ''}},
        {id: 'musecode', runtime: 'musecode', name: 'Muse Code', capabilities: {account: '', weekly_usage: ''}}]});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('[data-settings-tab=devices]').click();
    const table = page.locator('#set-machines [role=table][aria-label="AI tools on Team box"]');
    await table.waitFor();
    const own = name => table.locator(`[data-machine-tool="${name}"]:not([data-machine-profile])`);
    const cells = async row => (await row.locator('[role=cell]').allInnerTexts()).map(t => t.replace(/\s+/g, ' ').trim());

    // Installed, enabled and sign-in are separate cells; the header names them.
    assert.deepEqual(await table.locator('[role=columnheader]').allInnerTexts().then(a => a.map(t => t.toLowerCase())), ['tool', 'installed', 'enabled', 'sign-in', 'usage']);
    const claude = await cells(own('claude'));
    assert.deepEqual(claude.slice(0, 4), ['Claude Code', '2.1.300', 'Enabled', 'Signed in as chris@acme.example · Max']);

    // Each sign-in state.
    assert.equal((await cells(own('codex')))[3], 'Signed in · account not reported yet', 'a supported account not read yet');
    assert.match((await cells(own('cursor')))[3], /^Signed out/);
    assert.match((await cells(own('musecode')))[3], /^Login expired or rejected/);
    assert.match((await cells(own('musecode')))[3], /10m ago\. The provider refused the login\./);
    assert.match((await cells(own('grok')))[3], /^Checking…/);
    assert.match((await cells(own('gemini')))[3], /^Couldn't check last checked \ds ago/);
    // Grok is off here; its bots' tools are on and say why.
    assert.equal((await cells(own('grok')))[2], 'Off');
    assert.equal(await own('codex').locator('.machine-tool-enabled').getAttribute('title'), 'Its bots use it');

    // A long version stays in its cell, the full value in the title.
    const version = own('cursor').locator('.machine-tool-version');
    assert.equal(await version.getAttribute('title'), '2026.10.14-941492e-long-build-suffix');
    assert.ok(await version.evaluate(el => el.scrollWidth > el.clientWidth), 'the long version is cut off, not widening the row');

    // Usage: tools without provider data read unavailable. Claude's own login has no reading before a turn here, and
    // says where one will come from; never 0% or a full allowance.
    assert.equal((await cells(own('cursor')))[4], 'Usage unavailable');
    assert.equal((await cells(own('claude')))[4], 'Weekly usage unavailable · reported after a Claude Code turn on this computer');
    assert.equal(await own('claude').locator('meter').count(), 0);
    // The computer's own login reports its windows too: percentages and bars on its own row.
    assert.match((await cells(own('codex')))[4], /^Weekly 88% left resets .* · as of 2m ago 5-hour window 60% left resets/);
    assert.deepEqual(await own('codex').locator('meter').evaluateAll(m => m.map(x => [x.value, x.getAttribute('aria-label')])),
      [[88, 'Codex Weekly: 88% left'], [60, 'Codex 5-hour window: 60% left']]);

    // Several profiles: one line each under the tool, with its own account and usage.
    const acmeCodex = table.locator('[data-machine-profile="acme"][data-machine-tool="codex"]');
    const acme = await cells(acmeCodex);
    assert.equal(acme[0], 'Acme Max');
    assert.equal(acme[3], 'Signed in as dev@acme.example · Pro');
    assert.match(acme[4], /Weekly 62% left resets \w+ .* · as of 1h ago/);
    assert.match(acme[4], /5-hour window 10% left resets/);
    assert.deepEqual(await acmeCodex.locator('meter').evaluateAll(m => m.map(x => [x.value, x.getAttribute('aria-label')])),
      [[62, 'Codex Acme Max Weekly: 62% left'], [10, 'Codex Acme Max 5-hour window: 10% left']]);
    // Claude's turns give resets, never a percentage: it reads unavailable, with no bar.
    const acmeClaude = table.locator('[data-machine-profile="acme"][data-machine-tool="claude"]');
    assert.match((await cells(acmeClaude))[4], /^Weekly percentage unavailable resets .*5-hour window percentage unavailable resets/);
    assert.equal(await acmeClaude.locator('meter').count(), 0);
    // An account reading that missed its refresh says when it was last checked; a fresh one says nothing more.
    assert.equal(await table.locator('[data-account-checked]').count(), 0, 'readings within the refresh interval carry no note');
    box.profiles[0].runtimes.codex.account.checked_at = at(-25 * 60000);
    await page.evaluate(() => loadSettings());
    await acmeCodex.locator('[data-account-checked]').waitFor();
    assert.equal((await cells(acmeCodex))[3], 'Signed in as dev@acme.example · Pro account last checked 25m ago');
    assert.equal(await own('claude').locator('[data-account-checked]').count(), 0);
    // A signed-out profile and one that could not be checked: no account, no usage numbers.
    const beta = async tool => cells(table.locator(`[data-machine-profile="beta"][data-machine-tool="${tool}"]`));
    assert.deepEqual((await beta('claude')).slice(3), ['Signed out', '—']);
    assert.deepEqual((await beta('codex')).slice(3), ["Couldn't check", 'Weekly usage unavailable']);
    const text = await table.innerText();
    assert.doesNotMatch(text, /\b0% left\b|\b100% left\b|\b0% used\b/, 'missing usage never reads as none or all left');

    // After a Claude turn on this computer: its own login's windows, as Claude Code reports them (53% of five hours,
    // 17% of the week used).
    box.readiness.runtimes.claude.weekly = {used_percent: 17, resets_at: at(3 * 86400000), reported_at: at(-60000), status: null};
    box.readiness.runtimes.claude.five_hour = {used_percent: 53, resets_at: at(3 * 3600000), reported_at: at(-60000), status: 'allowed'};
    await page.evaluate(() => loadSettings());
    await own('claude').locator('meter').first().waitFor();
    assert.deepEqual(await own('claude').locator('meter').evaluateAll(m => m.map(x => x.value)), [83, 47]);
    assert.doesNotMatch((await cells(own('claude')))[4], /reported after/);

    // Nothing the server did not send: without an account there is no email, only that it is not reported.
    box.readiness.runtimes.claude = {installed: true, authenticated: 'ready'};
    box.profiles = [];
    await page.evaluate(() => loadSettings());
    await page.locator('#set-machines [data-machine-tool="claude"]').first().waitFor();
    assert.equal((await cells(page.locator('#set-machines [data-machine-tool="claude"]').first()))[3], 'Signed in · account not reported yet');
    assert.doesNotMatch(await page.locator('#set-machines').innerText(), /@acme\.example/);

    // Offline: what the computer last said, with when.
    box.last_seen = at(-3 * 3600000);
    await page.evaluate(() => loadSettings());
    await page.locator('#set-machines [data-machine-tool="grok"]', {hasText: "Couldn't check"}).waitFor();
    assert.match((await cells(page.locator('#set-machines [data-machine-tool="grok"]').first()))[3], /^Couldn't check last checked 3h ago/);

    // Revoked computers stay hidden until asked for.
    assert.equal(await page.locator('#set-machines [aria-label="AI tools on Old laptop"]').count(), 0);
    await page.locator('#set-machines [data-show-revoked-computers]').check();
    await page.locator('#set-machines [aria-label="AI tools on Old laptop"]').waitFor();
    assert.equal(await page.locator('#set-machines [aria-label="AI tools on Old laptop"] .machine-signin').count(), 0, 'no sign-in on a revoked computer');

    // The install, pin and update controls stay in their own disclosure.
    assert.equal(await page.locator('#set-machines details[data-machine-details="box:tools"] > summary').innerText(), 'Installation and updates');
    assert.deepEqual(errors, []);
    console.log('PASS: AI tools rows, sign-in states, usage windows, profiles, long versions, revoked hidden');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
