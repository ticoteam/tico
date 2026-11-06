// Health has no tab: General opens with one Health line (ui/health.js hlSummaryDraw). It counts Health's checks by
// severity with Settings' own issues (one a person must act on is urgent, the rest FYI), says "All good" when nothing
// needs work, carries the alert dot, and shows a newer release with the owner's one Update (none for a member or when
// Tico runs updates). Details opens #/settings/health: no tab highlighted, a way back to General, and the issues a
// person must act on (with Restart) at its top instead of above the tabs. Fixtures only. With TICO_SHOTS set, it saves
// General's top and the Health page at desktop and phone width, dark theme.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const shotsDir = process.env.TICO_SHOTS || '';

const check = (id, status) => ({id, label: id, status, summary: `${id} is ${status}`, fixes: []});
const RESTART = {kind: 'runner_checkout', title: 'Studio Mac has a Tico update', detail: 'Nothing is running.',
  machine: 'r1', severity: 'warning', needs_person: true, restart: true};
const NOTE = {kind: 'uncertain_work', title: 'Writer saved 1 stopped run for later', bot: 'writer', detail: 'Review it whenever useful.',
  severity: 'warning', needs_person: false, action: 'review'};

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    let role = 'owner', update = {current: '0.5.10', latest: '0.5.12', available: true};
    let health = {audience: 'owner', attention: 3, computers: [], waiting: [], stuck: [], failures: [],
      checks: [check('models', 'bad'), check('backups', 'warn'), check('slack', 'warn'), check('github', 'info'), check('computers', 'ok')]};
    let issues = [RESTART, NOTE];
    const context = await browser.newContext({viewport: {width: 1440, height: 900}, serviceWorkers: 'block', colorScheme: 'dark'});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await context.route('**/*', route => {
      const url = new URL(route.request().url()), p = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'https://tico-ui.test') return route.abort();
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.startsWith('/vendor/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({body: fs.readFileSync(uiFile(p.slice(1))),
        contentType: p.endsWith('.woff2') ? 'font/woff2' : 'application/javascript'});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const config = {version: '0.5.10', app_name: 'Tico', update};
      if (p === '/api/me') return json({id: 'ana', role, name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/v2/health') return json(health);
      if (p === '/api/v2/health/findings') return json({findings: [], botops: true, reports_without_asking: false});
      if (p === '/api/v2/system/metrics') return json({});
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/employees' || p === '/api/issues') return json([]);
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/operations') return json({machines: [{id: 'r1', label: 'Studio Mac', online: true}], services: [], issues});
      if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    // A route change cross-fades (router.js): the shot waits for it to end.
    const shot = async name => {
      if (!shotsDir) return;
      fs.mkdirSync(shotsDir, {recursive: true});
      await page.waitForFunction(() => !document.querySelector(':root:active-view-transition'));
      await page.screenshot({path: path.join(shotsDir, name)});
    };
    const line = page.locator('#settings-health-line');
    const counts = () => line.locator('[data-hl-counts]').innerText();
    const redraw = () => page.evaluate(async () => { await loadSettings(); await hlRefresh(); });

    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('#settings-tabs').waitFor();
    // The tab row: Health, Gateway and Repositories are not tabs, Security is an admin's; More keeps Billing.
    const tabs = await page.locator('#settings-tabs > [data-settings-tab]').evaluateAll(b => b.map(x => x.dataset.settingsTab));
    assert.deepEqual(tabs.filter(t => t !== 'security'), ['general', 'people', 'bots', 'devices', 'models', 'tasks', 'history']);
    if (tabs.includes('security')) assert.equal(tabs.indexOf('security'), tabs.indexOf('history') - 1, 'Security sits before History');
    assert.deepEqual(await page.locator('#settings-more [data-settings-tab]').evaluateAll(b => b.map(x => x.dataset.settingsTab)),
      ['billing']);
    // Bad checks and a person's issue are urgent, warn checks to fix, info checks and the rest of the issues FYI.
    await page.waitForFunction(() => document.querySelector('#settings-health-line [data-hl-count=urgent]'));
    await redraw();
    assert.equal(await counts(), '2 urgent · 2 to fix · 2 FYI');
    assert.equal(await line.locator('[data-hl-alert]').isVisible(), true, 'the alert dot sits on the line');
    assert.equal(await line.locator('[data-hl-version]').innerText(), 'v0.5.12 available');
    assert.equal(await line.locator('[data-hl-update]').innerText(), 'Update');
    assert.equal(await page.locator('.settings-issue:visible').count(), 0, 'no issues card above the tabs');
    await shot('desktop-general-health-line.png');

    // Details opens the full page: no tab highlighted, the person's issue with its Restart at the top, a way back.
    await line.locator('[data-hl-details]').click();
    await page.waitForFunction(() => location.hash === '#/settings/health' && SETTINGS_TAB === 'health');
    await page.locator('#hl-page .hl-list').waitFor();
    assert.equal(await page.locator('#settings-tabs [aria-selected=true]').count(), 0, 'no tab is highlighted');
    assert.equal(await page.locator('#settings-health #settings-issues .settings-issue').count(), 1);
    assert.equal(await page.locator('#settings-health #settings-issues [data-runner-restart=r1]').isVisible(), true);
    assert.equal(await page.locator('#hl-page .hl-notes li').count(), 1, 'the note that needs nobody stays under Not urgent');
    await shot('desktop-health-details.png');
    await page.locator('#settings-health [data-settings-back]').click();
    await page.waitForFunction(() => location.hash === '#/settings/general' && SETTINGS_TAB === 'general');
    assert.equal(await line.isVisible(), true);

    // Nothing to fix: All good, the FYI count kept, no dot.
    health = {...health, attention: 0, checks: [check('github', 'info'), check('computers', 'ok')]};
    issues = [];
    await redraw();
    assert.equal(await counts(), 'All good · 1 FYI');
    assert.equal(await line.locator('[data-hl-alert]').isHidden(), true);

    // Tico runs this server's updates: the release shows, no Update. A member sees the line, without Update either.
    update = {...update, managed: true};
    await page.evaluate(u => { S.config.update = u; hlSummaryDraw(); }, update);
    assert.equal(await line.locator('[data-hl-version]').isVisible(), true);
    assert.equal(await line.locator('[data-hl-update]').count(), 0, 'managed: no Update');
    update = {current: '0.5.10', latest: '0.5.12', available: true};
    role = 'human';
    await page.reload();
    await page.locator('#settings-health-line [data-hl-counts] .ok').waitFor();
    assert.equal(await line.locator('[data-hl-update]').count(), 0, 'a member has no Update');
    assert.equal(await line.locator('[data-hl-details]').getAttribute('href'), '#/settings/health');

    // A phone: the line and the Health page fit.
    role = 'owner';
    health = {...health, attention: 3, checks: [check('models', 'bad'), check('backups', 'warn'), check('slack', 'warn'), check('github', 'info')]};
    issues = [RESTART, NOTE];
    await page.setViewportSize({width: 390, height: 844});
    await page.reload();
    await page.locator('#settings-health-line [data-hl-count=urgent]').waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'no sideways scroll on General');
    await shot('phone-general-health-line.png');
    await line.locator('[data-hl-details]').click();
    await page.locator('#hl-page .hl-list').waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'no sideways scroll on Health');
    await shot('phone-health-details.png');
    assert.deepEqual(errors, []);
    await context.close();
  } finally { await browser.close(); }
  console.log('settings-health-line ok');
})().catch(error => { console.error(error); process.exit(1); });
