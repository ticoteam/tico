// Settings > Computers' hosted servers against a fake server (paused-start, d8f8efaa): the owner sees the Tico server
// and the bot computer, Start on the stopped one; a start while the company is paused for credit says "Paused: out of
// credit. Add credit to start this computer." with "Ask for more credit" opening the account page, and stays on screen
// through Settings' reloads; once credit is added the start goes through. An install HQ hosts nothing for has no
// section, and a member never asks. No sideways scroll on a phone. Every request is intercepted. TICO_SHOTS=<dir>
// saves screenshots of the refusal (desktop and phone, dark and light).
// Sleep when idle: an owner sets the hours and the bot-computer switch and sees the quiet time, next wake or why the
// server stays awake; hidden where Tico hosts nothing; a member sees it disabled.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const path = require('node:path');
const {unrouted} = require('./support/routes.cjs');
const shots = process.env.TICO_SHOTS || '';

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const TICO = 'pv-' + 'a'.repeat(16), BOTS = 'pv-' + 'b'.repeat(16);
    const PAUSED = 'Paused: out of credit. Add credit to start this computer.';
    let servers = {state: 'ok', servers: [{server_id: TICO, kind: 'tico', power: 'on'}, {server_id: BOTS, kind: 'bot_computer', power: 'stopped'}]};
    let role = 'owner', paused = true;
    const asked = [];
    let sleep = {hosted: true, layout: null, hours: 0, runners: true, active: false, idle_since: null, blocker: '', wake_at: null, last_request: null, last_error: ''};
    const sleepPosts = [];
    await page.route('**/*', route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.startsWith('/vendor/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({body: fs.readFileSync(uiFile(p.slice(1))),
        contentType: p.endsWith('.woff2') ? 'font/woff2' : 'application/javascript'});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const config = {version: '0.3.0', app_name: 'Tico'};
      if (p === '/api/me') return json({id: 'ana', role, name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
      if (p === '/api/v2/config') return json(config);
      if (p.startsWith('/api/v2/hosted-servers')) {
        asked.push(req.method() + ' ' + p);
        if (p === '/api/v2/hosted-servers') return json(servers);
        if (req.method() === 'POST' && p === `/api/v2/hosted-servers/${BOTS}/start`) {
          if (paused) return json({state: 'paused', message: PAUSED, credit_url: 'https://app.tico.dev'});
          servers = {...servers, servers: servers.servers.map(s => ({...s, power: 'on'}))};
          return json({state: 'started'});
        }
      }
      if (p === '/api/v2/sleep' && req.method() === 'POST') {
        const body = req.postDataJSON();
        sleepPosts.push(body);
        sleep = {...sleep, ...body, active: !!(body.hours ?? sleep.hours), idle_since: '2026-10-25T08:00:00Z', blocker: '', wake_at: '2026-10-26T09:00:00Z'};
        return json({sleep});
      }
      if (p === '/api/v2/sleep') return json({sleep});
      if (p === '/api/employees' || p === '/api/issues') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/operations') return json({machines: [], services: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const card = page.locator('#settings-hosted');
    const theme = mode => page.evaluate(m => document.documentElement.setAttribute('data-theme', m), mode);
    const shot = async name => { if (shots) await card.screenshot({path: path.join(shots, name + '.png')}); };

    await page.goto('https://tico-ui.test/#/settings/devices');
    await card.locator(`[data-hosted="${BOTS}"]`).waitFor();
    assert.equal(await card.locator('h2').innerText(), 'Hosted servers');
    assert.match((await card.locator(`[data-hosted="${TICO}"]`).innerText()).replace(/\s+/g, ' ').trim(), /^Tico server On$/);
    assert.equal(await card.locator(`[data-hosted="${TICO}"] [data-hosted-start]`).count(), 0, 'no Start on a running server');
    assert.match((await card.locator(`[data-hosted="${BOTS}"]`).innerText()).replace(/\s+/g, ' ').trim(), /^Bot computer Stopped Start$/);

    // Paused for credit: refused in plain words, with the account page to ask for more credit.
    await card.locator(`[data-hosted-start="${BOTS}"]`).click();
    const note = card.locator(`[data-hosted-note="${BOTS}"]`);
    await note.locator('.hosted-paused').waitFor();
    assert.equal(await note.locator('.hosted-paused').innerText(), PAUSED);
    const ask = note.locator('[data-hosted-credit]');
    assert.equal(await ask.innerText(), 'Ask for more credit');
    assert.equal(await ask.getAttribute('href'), 'https://app.tico.dev');
    assert.equal(await ask.getAttribute('target'), '_blank');
    assert.equal(await card.locator(`[data-hosted-start="${BOTS}"]`).isDisabled(), false, 'Start can be tried again');
    for (const mode of ['dark', 'light']) { await theme(mode); await shot(`hosted-paused-desktop-${mode}`); }
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForTimeout(400);
    if (shots) await page.screenshot({path: path.join(shots, "hosted-paused-phone-full.png"), fullPage: true});
    for (const mode of ['dark', 'light']) { await theme(mode); await shot(`hosted-paused-phone-${mode}`); }
    await page.setViewportSize({width: 1280, height: 800});
    // Settings reloads: the refusal stays, and HQ is not asked again.
    const reads = asked.filter(a => a === 'GET /api/v2/hosted-servers').length;
    await page.evaluate(() => loadSettings());
    await page.waitForTimeout(300);
    assert.equal(await note.isVisible(), true);
    assert.equal(asked.filter(a => a === 'GET /api/v2/hosted-servers').length, reads);

    // Credit added: the start goes through and the row shows it on.
    paused = false;
    await card.locator(`[data-hosted-start="${BOTS}"]`).click();
    await page.waitForFunction(id => document.querySelector(`[data-hosted="${id}"] .hosted-power`)?.textContent === 'On', BOTS);
    assert.equal(await card.locator('[data-hosted-start]').count(), 0);
    assert.equal(await card.locator(`[data-hosted-note="${BOTS}"]`).isVisible(), false);

    // Sleep when idle: off at first; an owner picks hours and the quiet time and next wake show; a busy server says why.
    const nap = page.locator('#settings-sleep');
    await nap.locator('[data-sleep-hours]').waitFor();
    assert.equal(await nap.locator('[data-sleep-hours]').inputValue(), '0');
    assert.equal(await nap.locator('[data-sleep-status]').count(), 0);
    await nap.locator('[data-sleep-hours]').selectOption('3');
    await nap.locator('[data-sleep-status]').waitFor();
    assert.deepEqual(sleepPosts, [{hours: 3}]);
    assert.match(await nap.locator('[data-sleep-status]').innerText(), /^Quiet since .+ · Next wake .+/);
    await nap.locator('[data-sleep-runners]').click();
    await page.waitForFunction(() => document.querySelector('[data-sleep-runners]') && !document.querySelector('[data-sleep-runners]').disabled);
    assert.deepEqual(sleepPosts[1], {runners: false});
    sleep = {...sleep, blocker: 'run_in_progress', idle_since: null};
    await page.evaluate(() => renderSettingsSleep());
    await page.waitForFunction(() => document.querySelector('[data-sleep-status]')?.textContent.startsWith('Awake: a run is going'));

    // A phone: nothing scrolls sideways.
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForTimeout(300);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways scroll on a phone');
    await page.setViewportSize({width: 1280, height: 800});

    // HQ did not answer the first read: hidden, and asked again at the next reload (a fixed key or HQ back shows it).
    const ok = servers;
    servers = {state: 'unreachable'};
    await page.goto('about:blank');
    await page.goto('https://tico-ui.test/#/settings/devices');
    await page.locator('#set-machines').waitFor();
    for (let i = 0; i < 40 && asked.slice(-1)[0] !== 'GET /api/v2/hosted-servers'; i++) await page.waitForTimeout(50);
    await page.waitForTimeout(200);
    assert.equal(await card.isHidden(), true);
    servers = ok;
    await page.evaluate(() => loadSettings());
    await card.locator(`[data-hosted="${BOTS}"]`).waitFor();
    // An answer HQ gave is kept a minute: another reload does not ask again.
    const kept = asked.length;
    await page.evaluate(() => loadSettings());
    await page.waitForTimeout(300);
    assert.equal(asked.length, kept);

    // Nothing hosted (a self-hosted install, or no account at HQ): no section.
    servers = {state: 'none'};
    sleep = {...sleep, hosted: false};
    await page.goto('about:blank');
    await page.goto('https://tico-ui.test/#/settings/devices');
    await page.locator('#set-machines').waitFor();
    for (let i = 0; i < 40 && !asked.slice(-1)[0]?.startsWith('GET'); i++) await page.waitForTimeout(50);
    await page.waitForTimeout(300);
    assert.equal(await card.isHidden(), true);
    assert.equal(await nap.isHidden(), true, 'no Sleep when idle on a server Tico does not host');

    // A member: no section, and the page never asks.
    role = 'human';
    const before = asked.length;
    await page.goto('about:blank');
    await page.goto('https://tico-ui.test/#/settings/devices');
    await page.locator('#set-machines').waitFor();
    await page.waitForTimeout(500);
    assert.equal(await card.count(), 0);
    assert.equal(asked.length, before);
    // A member sees Sleep when idle but cannot change it.
    sleep = {...sleep, hosted: true};
    await page.evaluate(() => renderSettingsSleep());
    await nap.locator('[data-sleep-hours]').waitFor();
    assert.equal(await nap.locator('[data-sleep-hours]').isDisabled(), true);
    assert.equal(await nap.locator('[data-sleep-runners]').isDisabled(), true);
    assert.deepEqual(errors, []);
    console.log('PASS: Settings > Computers hosted servers, Start refused while paused with the account page, then started; hidden when nothing is hosted; members never ask; Sleep when idle');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
