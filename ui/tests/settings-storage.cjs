// Settings > Health: the owner's Performance section from GET /api/v2/system/metrics (owners and admins; members never
// ask for it), and the owner's Storage row from GET /api/v2/health `storage` (local disk or S3, files and size,
// copy progress, failures in red, a Set up S3 link on local disk); nobody else sees it; no sideways scroll on a
// phone. Every request is intercepted. TICO_SHOTS=<dir> saves screenshots (desktop and phone, dark and light).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();   // load.cjs arrives with #254
const shots = process.env.TICO_SHOTS || '';

(async () => {
  const t0 = Date.now();
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const checks = [{id: 'version', label: 'Version', status: 'ok', summary: 'v0.3.0', fixes: []}];
    let health = {audience: 'owner', attention: 0, checks, computers: [], waiting: [], slow: [], failures: [], checked: new Date().toISOString(),
      storage: {mode: 's3', bucket: 'acme-tico-files', region: 'us-east-1', files: 1204, bytes: 3435973837, copy: {done: 120, total: 400, failed: 3}}};
    const now = Math.floor(Date.now() / 1000);
    const metrics = {minutes: 60, now, since: now - 3600,
      requests: {n: 48210, errors: 3, p50: 9.5, p95: 152.6, routes: [
        {route: 'GET /api/v2/tasks', n: 21000, errors: 0, bytes: 9e8, total_ms: 1830000, max_ms: 9200, p50: 40, p95: 310, callers: {bot: 20400, human: 600}},
        {route: 'POST /api/v2/runners/{runner_id}/heartbeat', n: 24000, errors: 3, bytes: 1e6, total_ms: 240000, max_ms: 800, p50: 8, p95: 24, callers: {runner: 24000}}]},
      slow: [{ts: now - 300, route: 'GET /api/v2/tasks', caller: 'bot', actor: 'bot:finance', ms: 9200, bytes: 4e6, status: 200}],
      process: {cpu_avg: 61.2, cpu_max: 99.8, rss: 512e6, threads: 70, lag_max: 2400, lag_p95: 180, wait_max: 1200, hold_max: 900, locked: 0, minutes: []},
      sql: {queries: [{sql: "SELECT t.* FROM tasks t WHERE t.status IN (?,…) AND coalesce(t.private,N)=N ORDER BY t.updated DESC LIMIT ?", n: 81000, total_ms: 1210000, max_ms: 950, p95: 61}]},
      db: {at: now - 600, bytes: 1.07e9, wal_bytes: 4.2e7, free_bytes: 1e6, growth_24h: 1.2e7},
      events: {start: {version: '0.3.0'}, recent: [{id: 1, at: now - 900, kind: 'stall', stalled_s: 3.4,
        threads: {busy: [{thread: 'MainThread', loop: true, stack: ['backend/app.py:520 request_timing', 'backend/hubdb.py:900 task_list']}], idle: 60}}]}};
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
      if (p === '/api/v2/system/metrics') return json(metrics);
      if (p === '/api/employees' || p === '/api/issues') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/operations') return json({machines: [], services: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json({});
    });
    const theme = mode => page.evaluate(m => document.documentElement.setAttribute('data-theme', m), mode);
    const shot = async name => { if (shots) await page.screenshot({path: path.join(shots, name + '.png')}); };
    // The fake server's data changes with no write, so a refresh would join a health read already on its way (from
    // before the change): every change waits for the page's own health read to land first.
    const settled = () => page.waitForFunction(() => !GET_INFLIGHT.has('/v2/health'), null, {timeout: t(5000)});
    const redraw = () => page.evaluate(() => hlRefresh());

    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('#settings-more > summary').click();
    await page.locator('[data-settings-tab=health]').click();
    const row = page.locator('#hl-page .hl-storage');
    await row.waitFor();
    // S3 while copying: bucket, files and size, progress, failures in red; no Set up S3
    const text = (await row.locator('.hl-storage-main').innerText()).replace(/\s+/g, ' ').trim();
    assert.match(text, /^Storage S3 · acme-tico-files 1,204 files · 3\.2 GB Copying to S3 · 120 of 400 3 failed$/, text);
    const red = await row.locator('[data-hl-copy-failed]').evaluate(el => {
      const probe = el.parentNode.appendChild(document.createElement('span'));
      probe.style.color = 'var(--fail)';
      const want = getComputedStyle(probe).color; probe.remove();
      return [getComputedStyle(el).color, want];
    });
    assert.equal(red[0], red[1], 'failures in red');
    assert.equal(await row.locator('a').count(), 0);
    // the flight recorder's last hour: numbers, a stall, top routes and SQL, slow requests
    await page.locator('[data-hl-metrics]').waitFor();
    assert.equal(await page.locator('#hl-page .hl-table').count(), 2);
    for (const mode of ['dark', 'light']) { await theme(mode); await shot(`storage-s3-desktop-${mode}`); }
    // local disk: no copy line, a docs link
    await settled();
    health = {...health, storage: {mode: 'local', bucket: null, region: null, files: 1, bytes: 2048, copy: {done: 0, total: 0, failed: 0}}};
    await redraw();
    assert.match((await row.locator('.hl-storage-main').innerText()).replace(/\s+/g, ' '), /^Storage Local disk 1 file · 2 KB Set up S3$/);
    assert.equal(await row.locator('[data-hl-copy], [data-hl-copy-failed]').count(), 0);
    assert.equal(await row.locator('a').getAttribute('href'), 'https://github.com/ticoteam/tico/blob/main/docs/files.md#storage');
    for (const mode of ['dark', 'light']) { await theme(mode); await shot(`storage-local-desktop-${mode}`); }
    // a phone: one row that wraps, no sideways scroll
    await page.setViewportSize({width: 390, height: 844});
    await settled();
    health = {...health, storage: {mode: 's3', bucket: 'acme-tico-files', region: 'us-east-1', files: 1204, bytes: 3435973837, copy: {done: 120, total: 400, failed: 3}}};
    await redraw();
    await row.locator('[data-hl-copy]').waitFor();
    await row.scrollIntoViewIfNeeded();
    // the phone drawer slides shut after the resize: wait for every finite animation and transition to end
    await page.waitForFunction(() => document.getAnimations().every(a => a.playState !== 'running' || a.effect?.getTiming().iterations === Infinity), null, {timeout: t(5000)});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways scroll on a phone');
    for (const mode of ['dark', 'light']) { await theme(mode); await shot(`storage-s3-phone-${mode}`); }
    // a bot admin's Health has no storage field and no row
    await settled();
    health = {...health, audience: 'admin', storage: undefined};
    await redraw();
    assert.equal(await page.locator('#hl-page .hl-storage').count(), 0);
    assert.equal(await page.locator('[data-hl-metrics]').count(), 1);
    assert.deepEqual(errors, []);
    console.log(`PASS: Health storage row, owner only, S3 copy progress, local disk, phone (${((Date.now() - t0) / 1000).toFixed(1)}s)`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
