// Offline browser regression: what moved off the Help page has its own home. Settings shows the version lines and,
// inside the desktop app, Show the app's log; Settings > Health runs the Setup check with each fix, keeps the result
// through Health's redraw and sends it to support attached; What's new opens with the Beta features and the known
// issues in this version. (Take the tour in the account menu is in getting-started.cjs.)
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile, beta} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const ISSUES = [{id: 5, title: 'Board stays empty after a restart', since: '0.2.15', fixed_in: '0.2.18', url: 'https://github.com/ticoteam/tico/issues/700'},
  {id: 6, title: '<img src=x onerror=alert(1)>', since: '0.2.16', fixed_in: '', url: 'javascript:alert(1)'}];
const CHECKS = [{name: 'HTTPS certificate', status: 'bad', detail: 'could not connect to tico.acme.example:443', fix: 'Open port 443 to the server.', source: 'doctor'},
  {name: '/healthz', status: 'ok', detail: 'ok', fix: 'Restart the server.', source: 'doctor'}];

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const visit = async ({hash, native = false, enabled = true, update = null} = {}) => {
      const ctx = await browser.newContext({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
      // The desktop app's bridge (app/src/main.rs BRIDGE_SCRIPT), recording what the page posts to it.
      if (native) await ctx.addInitScript(() => {
        window.__posted = [];
        window.ticoNative = name => ({postMessage: () => window.__posted.push(name)});
        window.ticoNativeFeatures = ['openWindow', 'checkForUpdates', 'showLog', 'appLog'];
        window.ticoNativeVersion = '0.3.48';
        window.ticoNativeLog = async () => ({version: '0.3.48', lines: []});
      });
      const page = await ctx.newPage();
      const errors = [], calls = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/*', route => {
        const req = route.request(), p = new URL(req.url()).pathname;
        const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
        if (ui && fs.existsSync(uiFile(ui[1])))
          return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
        if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        const config = {version: '0.2.17', update, app_name: 'Tico', usage_count_notice: false, beta};
        if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
        if (p === '/api/v2/config') return json(config);
        if (p.startsWith('/api/v2/support/')) {
          const rest = p.slice('/api/v2/support/'.length), body = req.postData() ? JSON.parse(req.postData()) : null;
          calls.push(req.method() + ' ' + rest + (body ? ' ' + JSON.stringify(body) : ''));
          if (rest === 'setup-check') return json({checks: CHECKS, failing: 1, doctor_only: 'Run tico-setup doctor on the server for the rest.'});
          if (rest === 'compose') return json({enabled: true, email: 'ana@acme.example', version: '0.2.17', install_id: 'i', to: 'updates.tico.team', max: 4000});
          if (rest === 'diagnostics/capture') return json({id: 'c'.repeat(64), bytes: 2048, text: '{}'});
          if (rest === 'access') return json({available: false, off_reason: 'TICO_SUPPORT_ACCESS', active: null, last: null});
          if (rest === 'tickets' || rest === 'tickets/refresh') return json({enabled, tickets: [], unread: 0});
          return json({});
        }
        if (p === '/api/v2/changelog') return json({entries: [], unread_count: 0, next_offset: null, can_add: false});
        if (p === '/api/employees' || p === '/api/issues') return json([]);
        if (p === '/api/humans') return json({people: [], teams: {}});
        if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
        if (p === '/api/v2/status') return json({bots: []});
        if (p === '/api/v2/needs-you') return json({items: []});
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json(unrouted(p));
      });
      await page.goto('https://tico-ui.test/' + hash);
      await page.waitForFunction(() => !document.querySelector('#account .account-email')?.textContent.includes('Signing in'));
      return {page, ctx, errors, calls};
    };
    const base = {current: '0.2.17', latest: '0.2.17', available: false, url: '', published_at: '', name: ''};
    const health = async page => {
      await page.locator('#settings-more summary').click();
      await page.locator('[data-settings-tab="health"]').click();
      await page.locator('#hl-setup-check').waitFor({state: 'visible'});
    };

    // Settings, inside the desktop app: both versions and Show the app's log, next to each other at the top.
    let v = await visit({hash: '#/settings', native: true});
    await v.page.locator('#settings-version').waitFor();
    assert.equal(await v.page.locator('#settings-version').innerText(), "Version v0.2.17 · App v0.3.48 · Show the app's log");
    await v.page.locator('#settings-app-log').click();
    assert.deepEqual(await v.page.evaluate(() => window.__posted), ['showLog']);

    // Settings > Health: Setup check runs the same checks and shows each result, with the fix only where one is needed.
    await health(v.page);
    assert.equal(await v.page.locator('#hl-setup-check [data-hl-setup-result]').isVisible(), false, 'no empty result box before a run');
    await v.page.locator('[data-hl-setup-check]').click();
    await v.page.locator('[data-hl-setup-row="bad"]').waitFor();
    assert.equal(await v.page.locator('.hl-setup-head').innerText(), '1 of 2 checks fail.');
    assert.match(await v.page.locator('[data-hl-setup-row="bad"]').innerText(), /HTTPS certificate[\s\S]*could not connect[\s\S]*Fix: Open port 443 to the server\./);
    assert.equal(await v.page.locator('[data-hl-setup-row="ok"] .hl-setup-fix').count(), 0, 'a passing check shows no fix, even when it has one');
    assert.equal(await v.page.locator('[data-hl-setup-check]').innerText(), 'Setup check');
    assert.equal(v.calls.filter(c => c === 'POST setup-check {}').length, 1, 'the same endpoint and payload as Help');
    // Health redraws its list every minute; the result is not part of it and stays.
    await v.page.evaluate(() => hlMount());
    assert.equal(await v.page.locator('[data-hl-setup-row]').count(), 2);
    // Send to support attaches the result, as from Help today.
    await v.page.locator('[data-hl-setup-send]').click();
    await v.page.locator('[data-report]').waitFor();
    assert.match(await v.page.locator('[data-report]').innerText(), /Setup check/);
    assert.match(await v.page.locator('[data-sent]').innerText(), /message marked setup/, 'a Setup check goes as a setup request');
    await v.page.locator('[data-compose] [data-preview]').click();
    await v.page.locator('dialog.support-modal [data-use]:not([disabled])').waitFor();
    const previewed = JSON.parse(v.calls.filter(c => c.startsWith('POST diagnostics/capture')).at(-1).slice('POST diagnostics/capture '.length)).report;
    assert.equal(previewed.kind, 'setup_check');
    assert.equal(previewed.title, '1 of 2 checks fail.');
    assert.deepEqual(previewed.checks.map(c => [c.name, c.status, c.fix]), CHECKS.map(c => [c.name, c.status, c.fix]));
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // In a browser: the version, no log button. Support turned off: the check still runs, with nothing to send it to.
    v = await visit({hash: '#/settings', enabled: false});
    await v.page.locator('#settings-version').waitFor();
    assert.equal(await v.page.locator('#settings-version').innerText(), 'Version v0.2.17');
    assert.equal(await v.page.locator('#settings-app-log').count(), 0);
    await v.page.waitForFunction(() => window.supportOn?.(true) === false && window.supportOn?.() === false);
    await health(v.page);
    await v.page.locator('[data-hl-setup-check]').click();
    await v.page.locator('[data-hl-setup-row="bad"]').waitFor();
    assert.equal(await v.page.locator('[data-hl-setup-send]').count(), 0);
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // What's new: the Beta features, then the known issues in this version, above the list of changes.
    v = await visit({hash: '#/changelog', update: {...base, known_issues: ISSUES, known_issues_checked: true}});
    const top = v.page.locator('.release-page');
    await top.locator('#changelog-known-issues').waitFor();
    assert.deepEqual(await top.evaluate(page => [...page.children].slice(0, 4).map(el => el.id || el.className)),
      ['release-header', 'changelog-beta', 'changelog-known-issues', 'release-filters']);
    assert.equal(await v.page.locator('#changelog-beta summary').innerText(), 'Beta features');
    assert.deepEqual(await v.page.locator('#changelog-beta li').evaluateAll(rows => rows.map(li => li.firstChild.textContent)), beta.map(f => f.name));
    assert.equal(await v.page.locator('#changelog-beta li .pill.beta').count(), beta.length);
    const ki = v.page.locator('#changelog-known-issues');
    assert.equal(await ki.locator('h2').textContent(), 'Known issues in your version');
    assert.equal(await ki.locator('li').count(), 2);
    assert.equal(await ki.locator('li').first().locator('a').getAttribute('href'), ISSUES[0].url);
    assert.match(await ki.locator('li').first().textContent(), /Fixed in v0\.2\.18/);
    assert.equal(await ki.locator('li').nth(1).locator('a').count(), 0, 'only an https link is a link');
    assert.equal(await ki.locator('li').nth(1).textContent(), ISSUES[1].title, 'a title is text, never HTML');
    assert.deepEqual(v.errors, []);
    await v.ctx.close();
    v = await visit({hash: '#/changelog', update: {...base, known_issues: [], known_issues_checked: true}});
    await v.page.locator('#changelog-known-issues').waitFor();
    assert.equal(await v.page.locator('#changelog-known-issues .changelog-ki-none').textContent(), 'None known in v0.2.17.');
    await v.ctx.close();
    // HQ not reached: it says it could not check rather than "none".
    v = await visit({hash: '#/changelog', update: {...base, known_issues: [], known_issues_checked: false}});
    await v.page.locator('#changelog-known-issues').waitFor();
    assert.match(await v.page.locator('#changelog-known-issues').textContent(), /Could not check/);
    assert.deepEqual(v.errors, []);
    await v.ctx.close();
    console.log('PASS: Settings shows the versions and the app log; Health runs the Setup check and sends it attached; What\'s new opens with Beta features and known issues.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
