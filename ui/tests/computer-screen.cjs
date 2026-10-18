// A computer's screen: the Computers ⋯ menu offers Screen only once the computer has one (`screen_available`) or a
// mode is set; choosing a mode POSTs it. The viewer opens a view session first, draws what a pretend VNC server sends,
// offers Control only when the mode is control (a new session with control: true), and says when the computer did not
// answer (close code 4404). Every request and socket is intercepted.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {fakeVnc, exposeRoutedSockets} = require('./support/fake-vnc.cjs');
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const computer = (id, label, more) => ({id, label, operator: 'ana', bots: [], platform: 'linux', last_seen: new Date().toISOString(),
      readiness: {}, screen: 'off', screen_available: false, ...more});
    const machines = [computer('c1', 'Build server', {screen_available: true}), computer('c2', 'Old Mac')];
    const writes = [], sessions = [];
    let answer = true;
    await page.routeWebSocket(/\/screen\/ws\?ticket=/, ws => {
      if (answer) fakeVnc(ws); else ws.close({code: 4404, reason: 'computer did not answer'});
    });
    await exposeRoutedSockets(page);
    await page.route('**/*', route => {
      const req = route.request(), p = new URL(req.url()).pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.startsWith('/vendor/novnc/') && fs.existsSync(uiFile(p.slice(1))))
        return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile(p.slice(1)), 'utf8')});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const config = {version: '0.1.0', app_name: 'Tico'};
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.com', cloud: true, registered: true, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees' || p === '/api/issues') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/operations') return json({machines, services: []});
      if (p === '/api/v2/computers/c1/screen') {
        const body = req.postDataJSON(); writes.push(body);
        machines[0].screen = body.mode;
        return json({id: 'c1', screen: body.mode, screen_available: true});
      }
      if (p === '/api/v2/computers/c1/screen/sessions') {
        const body = req.postDataJSON(); sessions.push(body);
        return json({session_id: 's' + sessions.length, ticket: 't' + sessions.length, control: body.control, width: 1440, height: 900,
                     ws_path: `/api/v2/runners/c1/screen/ws?ticket=t${sessions.length}`});
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('[data-settings-tab=devices]').click();
    // Only the computer that has a screen offers the choice.
    const more = label => page.locator(`#set-machines [aria-label="More for ${label}"]`);
    assert.equal(await page.locator('[data-computer-screen=c2]').count(), 0);
    await more('Build server').click();
    // All three stay choices here; the bot page's Turn on picks Control only.
    assert.deepEqual(await page.locator('[data-computer-screen=c1] input').evaluateAll(els => els.map(el => el.value)), ['off', 'view', 'control']);
    await page.locator('[data-computer-screen=c1] label:has(input[value=control])').click();
    await page.waitForFunction(() => SETTINGS_DATA.machines[0].screen === 'control');
    assert.deepEqual(writes, [{mode: 'control'}]);
    await page.locator('#set-machines a.machine-screen').click();
    // The viewer: a view session first, the picture drawn, Control offered.
    await page.waitForFunction(() => document.querySelector('#scr-state')?.textContent === 'View');
    assert.deepEqual(sessions, [{control: false}]);
    assert.equal(await page.locator('#scr-target canvas').count(), 1);
    assert.equal(await page.locator('#scr-keys-btn').isVisible(), false);
    await page.locator('#scr-mode [data-scr-control="1"]').click();
    await page.waitForFunction(() => document.querySelector('#scr-state')?.textContent === 'Control');
    assert.deepEqual(sessions, [{control: false}, {control: true}]);
    assert.equal(await page.locator('#scr-keys-btn').isVisible(), true);
    // A computer that does not answer says so, and Reconnect tries again.
    answer = false;
    await page.locator('#scr-mode [data-scr-control="0"]').click();
    await page.waitForFunction(() => document.querySelector('#scr-msg-text')?.textContent === 'Computer did not answer');
    assert.equal(await page.locator('#scr-retry').isVisible(), true);
    // Back leaves the viewer and closes the session.
    await page.locator('#scr-back').click();
    await page.waitForFunction(() => location.hash === '#/settings' && !document.querySelector('#scr'));
    assert.deepEqual(errors, []);
    console.log('PASS: Screen choice gated, mode saved, viewer view/control sessions, no answer, back');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
