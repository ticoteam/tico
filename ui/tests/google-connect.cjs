// Offline browser regression: Settings > Inbox, Connect Google (ui/google-connect.js). A person connects their own
// mailbox read only; a Workspace policy denial is shown as the admin's; only Google's own sign-in page is opened.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
    const errors = [], writes = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('dialog', dialog => dialog.accept());
    let google = {configured: true, mailbox: 'cara@acme.example', syncing: true, status: 'failed', kind: 'admin_policy',
      message: 'The Google Workspace for acme.example has a policy that blocks Tico, so Google refused the sign-in.',
      hint: 'Ask the administrator for acme.example to allow Tico in the Admin console.', can: []};
    let authUrl = 'https://accounts.google.com/o/oauth2/v2/auth?client_id=x&login_hint=cara%40acme.example';
    await page.route('**/*', route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.hostname === 'accounts.google.com') return route.fulfill({contentType: 'text/html', body: '<h1>Google sign-in</h1>'});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const config = {version: '0.1.0', app_name: 'Tico', local: true};
      if (p === '/api/me') return json({id: 'cara', role: 'human', name: 'Cara', email: 'cara@acme.example', cloud: true, registered: true, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json([]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/humans') return json({people: [{id: 'cara', name: 'Cara'}], teams: {}});
      if (p === '/api/people') return json({people: [], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/operations') return json({machines: [], services: []});
      if (p === '/api/v2/models') return json({models: [], harnesses: []});
      if (p === '/api/v2/me/tokens') return json({tokens: []});
      if (p === '/api/v2/mail/google') return json(google);
      if (p === '/api/v2/mail/google/connect') { writes.push(p); return json({auth_url: authUrl, mailbox: google.mailbox}); }
      if (p === '/api/v2/mail/google/disconnect') { writes.push(p); google = {...google, status: 'disconnecting'}; return json(google); }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('[data-settings-tab=inbox]').click();
    const pane = page.locator('#set-google');
    await pane.locator('[role=alert]').waitFor();
    assert.match(await pane.innerText(), /Google Workspace for acme\.example has a policy that blocks Tico/);
    assert.match(await pane.innerText(), /only to read your mail and calendar/);

    // A sign-in address that is not Google's own is never opened.
    authUrl = 'https://evil.example/phish';
    await pane.locator('[data-google-connect]').click();
    await page.waitForFunction(() => /Unexpected sign-in address/.test(document.querySelector('[data-google-status]')?.textContent || ''));
    assert.equal(new URL(page.url()).hostname, 'tico-ui.test');

    // Google's sign-in opens in this tab, and Settings comes back on Inbox.
    authUrl = 'https://accounts.google.com/o/oauth2/v2/auth?client_id=x&login_hint=cara%40acme.example';
    await pane.locator('[data-google-connect]').click();
    await page.waitForURL(u => new URL(u).hostname === 'accounts.google.com');

    // Connected: back on Settings > Inbox, read only, with Disconnect, which revokes.
    google = {...google, status: 'connected', kind: null, message: null, hint: null, can: ['read']};
    await page.goto('https://tico-ui.test/#/settings/inbox');
    await page.locator('[data-settings-tab=inbox][aria-selected=true]').waitFor();
    await page.locator('#set-google [data-google-disconnect]').waitFor();
    assert.match(await page.locator('#set-google').innerText(), /Connected: Tico reads cara@acme\.example's mail and calendar, read only/);
    await page.locator('#set-google [data-google-disconnect]').click();
    await page.waitForFunction(() => /Disconnecting/.test(document.querySelector('#set-google')?.textContent || ''));
    assert.deepEqual(writes, ['/api/v2/mail/google/connect', '/api/v2/mail/google/connect', '/api/v2/mail/google/disconnect']);

    // A disconnect the holding computer never takes says so, and points to Google's own page to revoke now.
    google = {...google, status: 'disconnecting', stalled: true,
      message: "The computer that holds your Google access hasn't come online, so Tico hasn't revoked it yet."};
    await page.evaluate(() => window.mountGoogleConnect(document.querySelector('#set-google')));
    await page.locator('#set-google [role=alert]').waitFor();
    assert.match(await page.locator('#set-google').innerText(), /hasn't come online/);
    assert.doesNotMatch(await page.locator('#set-google').innerText(), /within two minutes/);
    assert.equal(await page.locator('#set-google a[href="https://myaccount.google.com/permissions"]').count(), 1);

    // A server without Tico's Google app says so, with no button.
    google = {configured: false, mailbox: 'cara@acme.example', status: 'none'};
    await page.evaluate(() => window.mountGoogleConnect(document.querySelector('#set-google')));
    await page.waitForFunction(() => /isn't available on this Tico server/.test(document.querySelector('#set-google')?.textContent || ''));
    assert.equal(await page.locator('#set-google [data-google-connect]').count(), 0);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
