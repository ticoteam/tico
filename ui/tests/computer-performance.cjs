// A computer's Performance page: the Computers ⋯ menu links it for everyone and offers the off switch to its operator
// or an admin; the page draws CPU and memory on one percent axis with overloads shaded and active turns beneath, a
// tooltip under the pointer, an overloads table that links Replay, the busiest processes, and a table view. Turning
// it off and deleting the history go to the server. Every request is intercepted.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('dialog', d => d.accept());
    const machines = [{id: 'c1', label: 'Build server', operator: 'ana', bots: [], platform: 'linux', last_seen_at: new Date().toISOString(),
      readiness: {}, screen: 'off', screen_available: false, performance: 'on'}];
    const now = Math.floor(Date.now() / 1000 / 60) * 60, since = now - 86400;
    const points = [];
    for (let ts = now - 3 * 3600; ts <= now - 60; ts += 60) {
      const busy = ts >= now - 7200 && ts < now - 6600;
      points.push([ts, busy ? 97 : 20, busy ? 99 : 30, 55, 60, 1.2, 0, 5e10, busy ? 3 : 1]);
    }
    const iso = ts => new Date(ts * 1000).toISOString();
    const view = () => ({computer: {id: 'c1', label: 'Build server'}, performance: machines[0].performance, may_switch: true,
      hours: 24, span: 60, since, until: now, columns: ['ts', 'cpu', 'cpu_max', 'mem', 'mem_max', 'load1', 'swap', 'disk_free', 'active'],
      points: erased ? [] : points, mem_total: 16e9, swap_total: 0, disk_total: 1e11, scope: 'container_cpu',
      runs: [{attempt: 'a1', bot: 'ops', model: 'claude-opus-5-5', started_at: iso(now - 7300), finished_at: iso(now - 6500), state: 'done'}],
      busiest: {bots: [{bot: 'ops', model: 'claude-opus-5-5', runs: 1}], processes: [{name: 'cargo', cpu_minutes: 40.5, cpu_max: 400, mem_max: 2e9}]},
      overloads: [{started_at: now - 7200, ended: now - 6600, last: now - 6660, minutes: 10, reasons: ['cpu'], cpu_max: 99, mem_max: 60, runs_max: 3,
        bots: [{bot: 'ops', model: 'claude-opus-5-5', runs: 1}], procs: [{name: 'cargo'}]}],
      summary: 'Overloaded once in the last 1 day (CPU over 90%): CPU over 90% for 10 minutes; running: ops; busiest processes: cargo.'});
    const writes = [];
    let erased = false;
    await page.route('**/*', route => {
      const req = route.request(), p = new URL(req.url()).pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
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
      if (p === '/api/v2/computers/c1/performance') {
        if (req.method() === 'GET') return json(view());
        if (req.method() === 'DELETE') { writes.push('delete'); erased = true; return json({erased: true}); }
        const body = req.postDataJSON(); writes.push(body);
        machines[0].performance = body.mode;
        return json({performance: body.mode});
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('[data-settings-tab=devices]').click();
    // In the computer's pop-up, under Settings; it stays open while the choice saves.
    await page.locator('#set-machines .machine-name[data-computer-open=c1]').click();
    await page.locator('[data-performance-mode=c1]').uncheck();
    await page.waitForFunction(() => SETTINGS_DATA.machines[0].performance === 'off');
    await page.locator('[data-performance-mode=c1]:enabled').check();
    await page.waitForFunction(() => SETTINGS_DATA.machines[0].performance === 'on');
    assert.deepEqual(writes, [{mode: 'off'}, {mode: 'on'}]);
    await page.locator('[data-computer-performance=c1]').click();
    await page.waitForSelector('#perf-chart svg');
    assert.equal(await page.evaluate(() => location.hash), '#/computers/c1/performance');
    // One axis, two lines, the overload shaded, active turns beneath, and both lines labelled by name.
    assert.equal(await page.locator('#perf-chart .perf-line').count(), 2);
    assert.equal(await page.locator('#perf-chart rect.perf-band').count(), 1);
    assert.equal(await page.locator('#perf-chart .perf-strip').count(), 1);
    assert.deepEqual(await page.locator('#perf-chart .perf-direct').allTextContents(), ['Memory 55%', 'CPU 20%']);   // top to bottom
    assert.match(await page.locator('.perf-summary').textContent(), /Overloaded once/);
    // A container without a memory limit: CPU is its own, memory the host's.
    assert.match(await page.locator('.card .sub').first().textContent(), /memory is the whole computer's/);
    // The tooltip under the pointer names the turn that was running.
    const band = await page.locator('#perf-chart rect.perf-band').boundingBox();
    await page.mouse.move(band.x + band.width / 2, band.y + 40);
    await page.waitForSelector('.perf-tip:not([hidden])');
    const tip = await page.locator('.perf-tip').textContent();
    assert.match(tip, /CPU 97%/); assert.match(tip, /3 active turns: ops/);
    // The overloads table links its window in Replay; the busiest processes and the table view are there.
    assert.match(await page.locator('table a[href^="#/replay?from="]').getAttribute('href'), /&to=/);
    assert.match(await page.locator('.perf-split').textContent(), /cargo/);
    await page.locator('#perf-table summary').click();
    await page.waitForFunction(n => document.querySelectorAll('#perf-table tbody tr').length === n, points.length);
    // Turning it off says so; deleting empties the chart.
    await page.locator('[data-perf-mode=off]').click();
    await page.waitForSelector('.perf-off');
    await page.locator('[data-perf-erase]').click();
    await page.waitForFunction(() => !document.querySelector('#perf-chart') && /Nothing recorded/.test(document.querySelector('#perf-body').textContent));
    assert.deepEqual(writes.slice(2), [{mode: 'off'}, 'delete']);
    assert.deepEqual(errors, []);
    console.log('PASS: menu link and switch, chart, tooltip, overloads with Replay, table view, off, delete');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
