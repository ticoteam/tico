// A computer's screen: the Computers ⋯ menu offers Screen only once the computer has one (`screen_available`) or a
// mode is set; choosing a mode POSTs it. The viewer opens at the computer's mode: at Control a control session first,
// so a click reaches the computer at once, with View one tap away; at View a view session and no switch. It draws what
// a pretend VNC server sends and says when the computer did not answer (close code 4404). Every request and socket is
// intercepted.
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
    const computer = (id, label, more) => ({id, label, owner_id: 'ana', bots: [], platform: 'linux', last_seen_at: new Date().toISOString(),
      readiness: {}, screen: 'off', screen_available: false, ...more});
    const machines = [computer('c1', 'Build server', {screen_available: true}), computer('c2', 'Old Mac')];
    const writes = [], sessions = [], heard = [];
    let answer = true;
    await page.routeWebSocket(/\/screen\/ws\?ticket=/, ws => {
      if (answer) fakeVnc(ws, undefined, heard); else ws.close({code: 4404, reason: 'computer did not answer'});
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
    // In each computer's pop-up.
    await page.locator('#set-machines .machine-name[data-computer-open=c2]').click();
    await page.locator('#set-machines dialog[open] [data-detail-section=screen]').waitFor();
    assert.equal(await page.locator('[data-screen-toggle=c2]').count(), 1);
    assert.equal(await page.locator('[data-screen-copy]').count(), 0);
    await page.keyboard.press('Escape');
    await page.locator('#set-machines .machine-name[data-computer-open=c1]').click();
    // All three stay choices here; the bot page's Turn on picks Control only.
    await page.locator('[data-screen-toggle=c1]').check();
    await page.waitForFunction(() => SETTINGS_DATA.machines[0].screen === 'control');
    assert.deepEqual(writes, [{mode: 'control'}]);
    await page.locator('#set-machines .bot-screen-open').click();
    // The viewer at Control: a control session first, the picture drawn, and a click reaches the computer at once.
    await page.waitForFunction(() => document.querySelector('#scr-state')?.textContent === 'Control');
    assert.deepEqual(sessions, [{control: true}]);
    await page.locator('#scr-target canvas').waitFor();
    assert.equal(await page.locator('#scr-keys-btn').isVisible(), true);
    await page.waitForFunction(() => !document.querySelector('#scr-msg-text')?.textContent);
    await page.locator('#scr-target canvas').click({position: {x: 200, y: 150}});
    for (const end = Date.now() + 5000; !heard.includes(5) && Date.now() < end;) await new Promise(r => setTimeout(r, 50));
    assert(heard.includes(5), 'the click reached the computer as a pointer event');
    // View is one tap away: a new view session, and a click sends nothing.
    await page.locator('#scr-mode [data-scr-control="0"]').click();
    await page.waitForFunction(() => document.querySelector('#scr-state')?.textContent === 'View');
    assert.deepEqual(sessions, [{control: true}, {control: false}]);
    assert.equal(await page.locator('#scr-keys-btn').isVisible(), false);
    await page.waitForFunction(() => !document.querySelector('#scr-msg-text')?.textContent);
    heard.length = 0;
    await page.locator('#scr-target canvas').click({position: {x: 200, y: 150}});
    await new Promise(r => setTimeout(r, 300));
    assert(!heard.includes(5), 'a view session sends no pointer events');
    // A computer that does not answer says so, and Reconnect tries again.
    answer = false;
    await page.locator('#scr-mode [data-scr-control="1"]').click();
    await page.waitForFunction(() => document.querySelector('#scr-msg-text')?.textContent === 'Computer did not answer');
    assert.equal(await page.locator('#scr-retry').isVisible(), true);
    // Back leaves the viewer and closes the session.
    await page.locator('#scr-back').click();
    await page.waitForFunction(() => S.route === '#/settings' && location.hash === '#/settings/devices' && !document.querySelector('#scr'));
    // At View the viewer opens a view session and offers no switch.
    answer = true;
    await page.locator('[data-settings-tab=devices]').click();
    await page.locator('#set-machines .machine-name[data-computer-open=c1]').click();
    await page.locator('[data-screen-mode=c1]').selectOption('view');
    await page.waitForFunction(() => SETTINGS_DATA.machines[0].screen === 'view');
    await page.locator('#set-machines .bot-screen-open').click();
    await page.waitForFunction(() => document.querySelector('#scr-state')?.textContent === 'View');
    assert.deepEqual(sessions.at(-1), {control: false});
    assert.equal(await page.locator('#scr-mode').isVisible(), false);
    assert.deepEqual(errors, []);
    console.log('PASS: Screen choice gated, mode saved, viewer opens at the mode (control drives at once, view sends nothing), no answer, back');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
