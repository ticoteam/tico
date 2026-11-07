// Settings > General > Assistant > Approvals while the Settings page is rebuilt mid-load. The page is redrawn (a role
// change does it) while the first approvals render still waits on its two reads; the first render then finishes while
// the newer render's host is still empty. It must not throw "Cannot set properties of null (setting 'onchange')", and the
// newer render must still wire the switch so it saves. Every request is intercepted; no server is needed.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    let holding = false;                       // while true, the approvals reads wait until the test releases them
    const held = [], posts = [];
    let decides = false;
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
      if (p === '/api/me') return json({id: 'ana', role: 'owner', bot_admin: true, name: 'Ana', email: 'ana@acme.example', registered: true, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/v2/assistant/settings') {
        if (req.method() === 'POST') {
          const body = req.postDataJSON();
          posts.push(body);
          decides = body.decides_approvals;
          return json({decides_approvals: decides, team_allows: true});
        }
        const answer = () => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({decides_approvals: decides, team_allows: true})});
        if (holding) return held.push(answer);
        return answer();
      }
      if (p === '/api/v2/assistant/decisions') {
        const answer = () => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({decisions: []})});
        if (holding) return held.push(answer);
        return answer();
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

    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('[data-settings-tab=general]').click();
    await page.locator('#assistant-decides').waitFor();
    assert.equal(await page.locator('#assistant-decides').isChecked(), false, 'starts off');

    // The first render starts and waits on its reads. Its host is then replaced by a redraw (a role change), and a
    // second render starts on the new, still empty host and waits too.
    holding = true;
    await page.evaluate(() => { void renderSettingsAssistantApprovals(); });
    await page.evaluate(() => { S.me.role = 'member'; S.me.bot_admin = false; pageSettings(); });
    for (let i = 0; i < 100 && held.length < 2; i++) await page.waitForTimeout(20);
    assert.ok(held.length >= 2, 'both renders are waiting on the reads');
    assert.equal(await page.locator('#assistant-decides').count(), 0, 'the newer host is still empty when the older render resumes');

    // The older render resumes first, into a host that is no longer in the page.
    holding = false;
    held.splice(0).forEach(answer => answer());
    await page.waitForTimeout(500);
    assert.deepEqual(errors, [], 'no page error when the older render resumes');
    assert.equal(await page.locator('#assistant-decides').count(), 1, 'the newer render draws the switch');

    // The newer render wired the switch, so it saves.
    await page.locator('#assistant-decides').click();
    await page.waitForFunction(() => document.querySelector('#assistant-decides')?.checked === true);
    await page.waitForTimeout(300);
    assert.deepEqual(posts, [{decides_approvals: true}], 'the switch saves the person\'s choice');
    assert.deepEqual(errors, [], 'no page error after saving');
    console.log('PASS: Settings > General > Assistant approvals: an overlapping redraw does not throw, and the switch still saves');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
