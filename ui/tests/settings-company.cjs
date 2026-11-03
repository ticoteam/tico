// Settings > General > Tico company against a fake server (backend/company_server.py): an admin previews a server
// code, sees the company's name, connects, then sees the company, its credit and "Open on app.tico.dev", and
// disconnects. A member never sees the section. No sideways scroll on a phone. Every request is intercepted.
// TICO_SHOTS=<dir> saves screenshots of both states.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const shots = process.env.TICO_SHOTS || '';

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const CODE = 'c'.repeat(43), COMPANY = {id: '2615c13e-d164-414f-94a5-782f0ea1b2f8', name: 'Acme', slug: 'acme'};
    let linked = null, admin = true;   // an admin (owners see the same section)
    const asked = [];
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.startsWith('/vendor/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({body: fs.readFileSync(uiFile(p.slice(1))),
        contentType: p.endsWith('.woff2') ? 'font/woff2' : 'application/javascript'});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const config = {version: '0.3.0', app_name: 'Tico'};
      if (p === '/api/me') return json({id: 'ana', role: 'human', bot_admin: admin, name: 'Ana', email: 'ana@acme.example', registered: true, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/v2/company/server-connection') {
        asked.push(req.method());
        if (req.method() === 'GET') return json(linked || {company: null});
        if (req.method() === 'DELETE') { linked = null; return json({company: null}); }
        const body = req.postDataJSON();
        assert.equal(body.code, CODE);
        if (body.preview) return json({company: COMPANY, preview: true});
        linked = {company: COMPANY, workspace_id: 'ws_1', portal_url: 'https://app.tico.dev/servers', credit: {balance_cents: 1250}};
        return json(linked);
      }
      if (p === '/api/employees' || p === '/api/issues') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/status') return json({active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/operations') return json({machines: [], services: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const card = page.locator('#settings-company');
    const shot = async name => { if (shots) await card.screenshot({path: path.join(shots, name + '.png')}); };

    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('[data-settings-tab=policies]').click();
    await card.locator('[data-company-form]').waitFor();
    assert.equal(await card.locator('h2').innerText(), 'Tico company');
    await card.locator('input[name="code"]').fill(CODE);
    await card.getByRole('button', {name: 'Preview'}).click();
    await card.locator('[data-company-preview]:not([hidden])').waitFor();
    assert.equal(await card.locator('[data-company-preview-name]').innerText(), 'Acme');
    await shot('company-preview');
    await card.getByRole('button', {name: 'Connect'}).click();
    await card.locator('[data-company-name]').waitFor();
    assert.equal(await card.locator('[data-company-credit]').innerText(), '$12.50');
    const open = card.locator('[data-company-open]');
    assert.equal(await open.innerText(), 'Open on app.tico.dev');
    assert.equal(await open.getAttribute('href'), 'https://app.tico.dev/servers');
    await shot('company-linked');
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForTimeout(300);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways scroll on a phone');
    await page.setViewportSize({width: 1280, height: 800});

    page.once('dialog', dialog => dialog.accept());
    await card.locator('[data-company-disconnect]').click();
    await card.locator('[data-company-form]').waitFor();
    assert.ok(asked.includes('DELETE'));

    admin = false;
    const before = asked.length;
    await page.goto('about:blank');
    await page.goto('https://tico-ui.test/#/settings');
    assert.equal(await page.locator('[data-settings-tab=policies]').count(), 0);
    await page.locator('[data-settings-tab=you]').click();
    await page.locator('#settings-assistant-pane').waitFor();
    await page.waitForTimeout(400);
    assert.equal(await card.count(), 0);
    assert.equal(asked.length, before);
    assert.deepEqual(errors, []);
    console.log('PASS: Settings > General > Tico company: preview, connect, credit and portal link, disconnect; members never see it');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
