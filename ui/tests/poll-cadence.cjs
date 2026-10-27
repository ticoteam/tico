// The app's polls while the live stream is down (ui/app/boot.js, ui/app/notices.js, ui/health.js): status, config and
// Health each read every 2 minutes, not every 30 s or minute, so many open tabs do not multiply the load on a hub that
// is already busy. A Health tab shown again reads once at once, and that read restarts its 2-minute timer, so the
// next tick does not read again. Every request is intercepted; the page's clock is faked.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();

(async () => {
  const t0 = Date.now();
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const reads = {status: 0, config: 0, health: 0};
    const health = {audience: 'owner', attention: 0, checks: [{id: 'version', label: 'Version', status: 'ok', summary: 'v0.3.0', fixes: []}],
      computers: [], waiting: [], slow: [], failures: [], checked: new Date().toISOString()};
    await page.route('**/*', route => {
      const p = new URL(route.request().url()).pathname;
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.startsWith('/vendor/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({body: fs.readFileSync(uiFile(p.slice(1))),
        contentType: p.endsWith('.woff2') ? 'font/woff2' : 'application/javascript'});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const config = {version: '0.3.0', app_name: 'Tico'};
      if (p === '/api/v2/events') return route.fulfill({status: 503, body: ''});   // the live stream is down
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
      if (p === '/api/v2/config') { reads.config++; return json(config); }
      if (p === '/api/v2/health') { reads.health++; return json(health); }
      if (p === '/api/v2/health-monitor') return json({state: 'active', on: true, findings: []});
      if (p === '/api/v2/system/metrics') return json({});
      if (p === '/api/employees' || p === '/api/issues') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/status') { reads.status++; return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []}); }
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/operations') return json({machines: [], services: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.clock.install({time: new Date('2026-10-27T12:00:00Z')});
    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('[data-settings-tab=health]').click();
    await page.locator('#hl-page').waitFor({timeout: t(5000)});
    assert.equal(await page.evaluate(() => liveConnected()), false, 'the live stream should be down in this test');
    await page.clock.pauseAt(await page.evaluate(() => Date.now()) + 1000);
    const counted = async ms => {
      for (const key of Object.keys(reads)) reads[key] = 0;
      await page.clock.runFor(ms);
      await page.waitForTimeout(200);   // let the last fulfilled reads land
      return {...reads};
    };
    const visible = shown => page.evaluate(shown => {
      Object.defineProperty(document, 'hidden', {configurable: true, get: () => !shown});
      document.dispatchEvent(new Event('visibilitychange'));
    }, shown);

    // Four minutes with the stream down: about two reads each (it was eight of status and config, four of Health).
    const shown = await counted(240000);
    for (const key of Object.keys(reads))
      assert.ok(shown[key] >= 1 && shown[key] <= 2, `${key} read ${shown[key]} times in 4 minutes with live down: ${JSON.stringify(shown)}`);

    // A tab hidden for five minutes reads no status or Health; shown again, Health reads once and not at the next tick.
    await visible(false);
    const hidden = await counted(300000);
    assert.equal(hidden.status, 0, `a hidden tab read status ${hidden.status} times`);
    assert.equal(hidden.health, 0, `a hidden tab read Health ${hidden.health} times`);
    for (const key of Object.keys(reads)) reads[key] = 0;
    await visible(true);
    await page.clock.runFor(115000);
    await page.waitForTimeout(200);
    assert.equal(reads.health, 1, `a Health tab shown again read ${reads.health} times in the next 115 s`);
    assert.equal(reads.status, 1, `a tab shown again read status ${reads.status} times in the next 115 s`);
    assert.deepEqual(errors, []);
    console.log(`PASS: status, config and Health poll every 2 minutes with live down; a shown tab reads once (${((Date.now() - t0) / 1000).toFixed(1)}s)`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
