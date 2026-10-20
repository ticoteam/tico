// Offline browser regression: the one-time usage-count notice reaches the owner alone and is dismissible, and
// Settings > General has the one toggle and "Reset install ID" (PRIVACY.md).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const visit = async ({role = 'owner', notice = true, hash = '#/tasks'} = {}) => {
      const ctx = await browser.newContext({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
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
        const config = {version: '0.2.15', update: null, app_name: 'Tico', usage_count_notice: role === 'owner' && notice};
        if (p === '/api/me') return json({id: 'ana', role, name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
        if (p === '/api/v2/config') return json(config);
        if (p.startsWith('/api/v2/system/usage-count')) {
          calls.push(req.method() + ' ' + p + (req.method() === 'GET' ? '' : ' ' + (req.postData() || '')));
          return json({enabled: true, off_by: '', install_id: '6f1c2a9e-3b7d-4c58-9a10-2d4e8b7f5a63', notice: 'shown', doc: 'https://example.test/PRIVACY.md'});
        }
        if (p === '/api/v2/system/storage') return json({active: null, earlier: [], server: {mode: 'local', location: ''}, copy: {done: 0, total: 0, failed: 0, running: false}, doc: 'https://example.test/docs/files.md'});   // Storage shares General with Privacy
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

    let v = await visit();
    await v.page.locator('#usage-notice').waitFor({state: 'visible'});
    assert.ok(v.calls.some(c => c.includes('POST') && c.includes('"shown"')), 'the app reports that the notice was shown');
    await v.page.locator('#usage-notice-x').click();
    assert.equal(await v.page.locator('#usage-notice').isVisible(), false);
    assert.ok(v.calls.some(c => c.includes('POST') && c.includes('"dismissed"')), 'dismissing is recorded');
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    v = await visit({role: 'human'});
    assert.equal(await v.page.locator('#usage-notice').isVisible(), false, 'only the owner sees the notice');
    assert.equal(v.calls.length, 0);
    await v.ctx.close();

    v = await visit({notice: false, hash: '#/settings'});
    await v.page.locator('[data-settings-tab="general"]').click();
    await v.page.locator('#privacy-count').waitFor();
    assert.equal(await v.page.locator('#privacy-count').isChecked(), true);
    await v.page.locator('#privacy-count').uncheck();
    await v.page.waitForFunction(() => document.querySelector('.toast'));
    assert.ok(v.calls.some(c => c.startsWith('PUT') && c.includes('"enabled":false')), 'the toggle is saved');
    await v.page.locator('#privacy-reset').click();
    await v.page.waitForFunction(() => [...document.querySelectorAll('.toast')].some(t => /New install ID/.test(t.textContent)));
    assert.ok(v.calls.some(c => c.startsWith('POST /api/v2/system/usage-count/reset')), 'the ID is reset');
    assert.deepEqual(v.errors, []);
    await v.ctx.close();
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
