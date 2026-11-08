// Settings > Health, the owner's Health Monitor: each finding says how often the hourly scan has seen it and when it
// last did ("seen N times, last <time>"), from GET /api/v2/health-monitor `occurrences` and `last_seen`
// (backend/health_monitor.py: seeing a finding again counts and wakes nobody). Every request is intercepted.
// TICO_SHOTS=<dir> saves screenshots (dark and light).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();
const shots = process.env.TICO_SHOTS || '';

(async () => {
  const t0 = Date.now();
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const iso = minutes => new Date(Date.now() - minutes * 60000).toISOString();
    const health = {audience: 'owner', attention: 0, checks: [{id: 'version', label: 'Version', status: 'ok', summary: 'v0.3.0', fixes: []}],
      computers: [], waiting: [], slow: [], failures: [], checked: new Date().toISOString()};
    const monitor = {state: 'active', on: true, reports_without_asking: true, findings: [
      {key: 'computer_silent:r1', signature: 'hm-aaaaaaaaaaaa', check_id: 'computer_silent', kind: 'setup', severity: 'warn',
       title: 'Studio Mac has not checked in for 40 minutes', first_seen_at: iso(5 * 60), last_seen_at: iso(3), occurrences: 6},
      {key: 'server_cpu', signature: 'hm-bbbbbbbbbbbb', check_id: 'server_cpu', kind: 'tico-bug', severity: 'warn',
       title: 'The server used 55% CPU for an hour while idle', first_seen_at: iso(120), last_seen_at: iso(120), occurrences: 1,
       ticket_id: 't1', approval_id: null}]};
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
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/v2/health') return json(health);
      if (p === '/api/v2/health-monitor') return json(monitor);
      if (p === '/api/v2/system/metrics') return json({});
      if (p === '/api/employees' || p === '/api/issues') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/operations') return json({machines: [], services: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const theme = mode => page.evaluate(m => document.documentElement.setAttribute('data-theme', m), mode);
    const shot = async name => { if (shots) await page.screenshot({path: path.join(shots, name + '.png')}); };

    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('#settings-more > summary').click();
    await page.locator('[data-settings-tab=health]').click();
    const row = key => page.locator(`#hl-page [data-hl-finding="${key}"]`);
    await row('computer_silent:r1').waitFor({timeout: t(5000)});
    const text = async key => (await row(key).innerText()).replace(/\s+/g, ' ').trim();
    assert.match(await text('computer_silent:r1'), /^Studio Mac has not checked in for 40 minutes being fixed · since 5h ago · seen 6 times, last 3m ago$/);
    assert.match(await text('server_cpu'), /· seen 1 time, last 2h ago$/);
    assert.match(await page.locator('#hl-page [data-hl-monitor]').innerText(), /seeing a problem again only adds to its count/);
    for (const mode of ['dark', 'light']) { await theme(mode); await shot(`health-monitor-seen-${mode}`); }
    assert.deepEqual(errors, []);
    console.log(`PASS: Health Monitor findings say how often and when they were last seen (${((Date.now() - t0) / 1000).toFixed(1)}s)`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
