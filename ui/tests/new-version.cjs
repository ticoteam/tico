// Offline browser regression: the "New version" notice in the sidebar appears only when the server
// says a newer release exists, offers "Update now" to the owner alone, and dismissal is remembered.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile, bundled} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();   // load.cjs arrives with #254
const shots = process.env.TICO_SCREENSHOT_DIR;
// Every finite animation and transition has finished (an endless spinner does not count).
const settled = () => document.getAnimations().every(a => a.playState !== 'running' || a.effect?.getTiming().iterations === Infinity);
const UPDATE = {current: '0.1.0', latest: '0.2.0', available: true, url: 'https://github.com/ticoteam/tico/releases/tag/v0.2.0',
                published_at: '2026-10-20T10:00:00Z', name: 'Tico 0.2.0'};
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const visit = async ({role = 'owner', update = UPDATE, updater = null, context} = {}) => {
      const ctx = context || await browser.newContext({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
      const page = await ctx.newPage();
      const errors = [], posts = [];
      let reloads = 0, polls = 0, served = null;
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/*', route => {
        const req = route.request(), p = new URL(req.url()).pathname;
        const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
        if (ui && fs.existsSync(uiFile(ui[1])))
          return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
        if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        const config = {version: update ? update.current : '0.1.0', update, app_name: 'Tico'};
        if (p === '/api/me') return json({id: 'ana', role, name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
        if (p === '/api/v2/config') return json({...config, version: served || (polls > 1 ? '0.2.0' : '0.1.0')});
        if (p === '/api/v2/system/update' && req.method() === 'POST') {
          posts.push(req.postDataJSON());
          return updater === 'manual'
            ? json({error: {code: 'manual_update', detail: 'Run this on the server', command: 'docker compose pull && docker compose up -d'}}, 409)
            : json({state: 'pulling'});
        }
        if (p === '/api/v2/system/update') { polls++; return json({state: polls > 1 ? 'healthy' : 'restarting', from: '0.1.0', to: '0.2.0', message: ''}); }
        if (p === '/api/employees' || p === '/api/issues') return json([]);
        if (p === '/api/humans') return json({people: [], teams: {}});
        if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
        if (p === '/api/v2/status') return json({bots: []});
        if (p === '/api/v2/needs-you') return json({items: []});
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json(unrouted(p));
      });
      await page.goto('https://tico-ui.test/#/settings');
      await page.waitForFunction(() => !document.querySelector('#account .account-email')?.textContent.includes('Signing in'));
      page.on('framenavigated', () => reloads++);
      return {page, ctx, errors, posts, reloads: () => reloads, serve: version => { served = version; }};
    };

    // Nothing available, nothing shown.
    let v = await visit({update: {...UPDATE, available: false}});
    assert.equal(await v.page.locator('#new-version-wrap').isVisible(), false, 'no notice when up to date');
    assert.match(await v.page.locator('#settings-version').innerText(), /^Version v0\.1\.0$/, 'Settings shows the running version');
    await v.ctx.close();
    v = await visit({update: null});
    assert.equal(await v.page.locator('#new-version-wrap').isVisible(), false, 'no notice without update data');
    await v.ctx.close();

    // A non-owner reads the changelog and cannot update.
    v = await visit({role: 'viewer'});
    await v.page.locator('#new-version').click();
    assert.equal(await v.page.locator('#nv-update').count(), 0, 'non-owner has no Update now');
    assert.equal(await v.page.locator('#nv-changelog').getAttribute('href'), UPDATE.url);
    assert.equal(await v.page.locator('#nv-changelog').getAttribute('target'), '_blank');
    await v.ctx.close();

    // The owner sees it, and a dismissal survives a reload but not a newer version.
    v = await visit({});
    const notice = v.page.locator('#new-version');
    assert.equal((await notice.innerText()).trim(), 'New version v0.2.0');
    await notice.click();
    assert.equal(await v.page.locator('#nv-update').isVisible(), true, 'owner sees Update now');
    if (shots) { await v.page.waitForFunction(settled, null, {timeout: t(5000)}); const box = await v.page.locator('aside').first().boundingBox(); await v.page.screenshot({path: path.join(shots, 'new-version-notice.png'), clip: {x: 0, y: Math.max(0, box.y + box.height - 330), width: box.width, height: 330}}); }
    await v.page.locator('#nv-dismiss').click();
    assert.equal(await v.page.locator('#new-version-wrap').isVisible(), false, 'dismissed');
    const again = await visit({context: v.ctx});
    assert.equal(await again.page.locator('#new-version-wrap').isVisible(), false, 'dismissal persists');
    await again.page.evaluate(() => localStorage.setItem('tico.new-version.dismissed:ana', '0.1.9'));
    await again.page.reload();
    await again.page.waitForFunction(() => !document.querySelector('#account .account-email')?.textContent.includes('Signing in'));
    assert.equal(await again.page.locator('#new-version-wrap').isVisible(), true, 'a newer version shows again');
    await v.ctx.close();

    // A tab left open through an update: the next config answer carries a different version, so a banner
    // offers a reload (never reloads by itself); the same version shows nothing.
    v = await visit({update: {...UPDATE, available: false}});
    assert.equal(await v.page.locator('#stale-banner').isVisible(), false, 'no banner on the version the page loaded with');
    await v.page.evaluate(() => get('/v2/config').then(applyConfig));
    assert.equal(await v.page.locator('#stale-banner').isVisible(), false, 'no banner while the version is unchanged');
    v.serve('0.3.0');
    await v.page.evaluate(() => get('/v2/config').then(applyConfig));
    assert.equal((await v.page.locator('#stale-banner').innerText()).replace(/\s+/g, ' ').trim(), 'New version · Reload');
    await v.page.locator('#stale-reload').waitFor();
    await v.page.evaluate(() => get('/v2/config').then(applyConfig));    // the next config poll answers the same
    assert.equal(await v.page.locator('#stale-banner').isVisible(), true, 'it stays until the person reloads');
    assert.equal(v.reloads(), 0, 'the page is never reloaded for the person');
    await v.ctx.close();

    // The same release with another build of the page's script and stylesheet (an image rebuilt without a new
    // version) shows the bar too, in the bundled page; the page's own build matches the config's and shows nothing.
    v = await visit({update: {...UPDATE, available: false}});
    const own = await v.page.evaluate(() => PAGE_BUILD);
    assert.equal(!!own, bundled, 'the page knows its build when it is bundled');
    await v.page.evaluate(b => get('/v2/config').then(c => applyConfig({...c, ui_build: b})), own || 'aaaaaaaaaaaaaaaa.bbbbbbbbbbbbbbbb');
    assert.equal(await v.page.locator('#stale-banner').isVisible(), false, 'the same build shows nothing');
    await v.page.evaluate(() => get('/v2/config').then(c => applyConfig({...c, ui_build: '0123456789abcdef.fedcba9876543210'})));
    assert.equal(await v.page.locator('#stale-banner').isVisible(), bundled, 'another build shows the bar when the page is bundled');
    await v.ctx.close();

    // A tab shown again asks at once, not at the next poll.
    v = await visit({update: {...UPDATE, available: false}});
    assert.equal(await v.page.locator('#stale-banner').isVisible(), false);
    v.serve('0.4.0');
    await v.page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await v.page.locator('#stale-banner').waitFor({state: 'visible', timeout: t(3000)});
    await v.ctx.close();

    // Without an updater the owner is shown the command to run.
    v = await visit({updater: 'manual'});
    await v.page.locator('#new-version').click();
    await v.page.locator('#nv-update').click();
    await v.page.locator('#nv-copy').waitFor();
    assert.deepEqual(v.posts, [{version: '0.2.0'}]);
    assert.match(await v.page.locator('.nv-cmd code').innerText(), /^docker compose pull && docker compose up -d$/);
    await v.ctx.close();

    // With one, progress is polled and the page reloads once the new version is healthy.
    v = await visit({});
    await v.page.locator('#new-version').click();
    await v.page.locator('#nv-update').click();
    await v.page.locator('#nv-out .nv-status').filter({hasText: 'Updating'}).waitFor();
    await v.page.waitForEvent('framenavigated', {timeout: t(15000)});
    for (const x of [v]) assert.deepEqual(x.errors, []);
    await v.ctx.close();
    console.log('PASS: New version notice hidden unless available, changelog link for everyone, Update now for the owner only (progress, reload, manual command), dismissal per version.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
