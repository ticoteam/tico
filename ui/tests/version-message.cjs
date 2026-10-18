// Offline browser regression: Tico HQ's message for this version appears once per person (a reload does not bring it
// back), and What's new lists the known issues in the running version, or says it could not check.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const MESSAGE = {id: 2, versions: '0.3', body: 'You are on 0.3.52: update for faster boards.', url: 'https://github.com/ticoteam/tico/blob/main/docs/updates.md'};
const ISSUES = [{id: 5, title: 'Board stays empty after a restart', since: '0.3.50', fixed_in: '0.3.53', url: 'https://github.com/ticoteam/tico/issues/700'},
  {id: 6, title: '<img src=x onerror=alert(1)>', since: '0.3.51', fixed_in: '', url: 'javascript:alert(1)'}];
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const visit = async ({ctx, update, hash = '#/changelog', me = 'ana'} = {}) => {
      ctx = ctx || await browser.newContext({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
      const page = await ctx.newPage();
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/*', route => {
        const req = route.request(), p = new URL(req.url()).pathname;
        const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
        if (ui && fs.existsSync(uiFile(ui[1])))
          return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
        if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        const config = {version: '0.3.52', update, app_name: 'Tico', usage_count_notice: false};
        if (p === '/api/me') return json({id: me, role: 'owner', name: 'Ana', email: me + '@acme.example', cloud: true, registered: true, config});
        if (p === '/api/v2/config') return json(config);
        if (p === '/api/employees' || p === '/api/issues') return json([]);
        if (p === '/api/humans') return json({people: [], teams: {}});
        if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
        if (p === '/api/v2/status') return json({bots: []});
        if (p === '/api/v2/needs-you') return json({items: []});
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json(unrouted(p));
      });
      await page.goto('https://tico-ui.test/' + hash);
      await page.waitForFunction(() => !document.querySelector('#account .account-email')?.textContent.includes('Signing in'));
      return {page, ctx, errors};
    };
    const base = {current: '0.3.52', latest: '0.3.52', available: false, url: '', published_at: '', name: ''};

    // Shown once: on the first visit, not after a reload, and again for another person in the same browser.
    let v = await visit({update: {...base, message: MESSAGE, known_issues: ISSUES, known_issues_checked: true}});
    await v.page.locator('#version-message').waitFor({state: 'visible'});
    assert.equal(await v.page.locator('#version-message-text').textContent(), MESSAGE.body);
    assert.equal(await v.page.locator('#version-message-link').getAttribute('href'), MESSAGE.url);
    // What's new: the known issues, the link only when it is https, the title as text.
    const ki = v.page.locator('#changelog-known-issues');
    await ki.waitFor({state: 'visible'});
    assert.equal(await ki.locator('h2').textContent(), 'Known issues in your version');
    assert.equal(await ki.locator('li').count(), 2);
    assert.equal(await ki.locator('li').first().locator('a').getAttribute('href'), ISSUES[0].url);
    assert.match(await ki.locator('li').first().textContent(), /Fixed in v0\.3\.53/);
    assert.equal(await ki.locator('li').nth(1).locator('a').count(), 0);
    assert.equal(await ki.locator('li').nth(1).textContent(), ISSUES[1].title);
    await v.page.locator('#version-message-x').click();
    assert.equal(await v.page.locator('#version-message').isVisible(), false);
    assert.deepEqual(v.errors, []);
    await v.page.close();
    let again = await visit({ctx: v.ctx, update: {...base, message: MESSAGE, known_issues: [], known_issues_checked: true}});
    await again.page.locator('#changelog-known-issues').waitFor({state: 'visible'});
    assert.equal(await again.page.locator('#version-message').isVisible(), false, 'a reload does not show it again');
    assert.equal(await again.page.locator('#changelog-known-issues .changelog-ki-none').textContent(), 'None known in v0.3.52.');
    await again.page.close();
    again = await visit({ctx: v.ctx, me: 'riley', update: {...base, message: MESSAGE, known_issues_checked: true}});
    await again.page.locator('#version-message').waitFor({state: 'visible'});
    await v.ctx.close();

    // HQ not reached: no message, and What's new says it could not check rather than "none".
    v = await visit({update: {...base, message: null, known_issues: [], known_issues_checked: false}});
    await v.page.locator('#changelog-known-issues').waitFor({state: 'visible'});
    assert.match(await v.page.locator('#changelog-known-issues').textContent(), /Could not check/);
    assert.equal(await v.page.locator('#version-message').isVisible(), false);
    assert.deepEqual(v.errors, []);
    await v.ctx.close();
    console.log('version-message: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
