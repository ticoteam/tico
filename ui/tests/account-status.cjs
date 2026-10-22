// The account button's one status dot (Chris, Oct 22): one dot, left of the person's name, never beside it or on the
// Settings row. Its colour is the worst line: red when something is broken and needs the person, amber for a warning or
// something that clears by itself, no dot when all is clear. Opening the menu (a tap on a phone) or hovering the dot
// shows one plain line per problem, each with its fix. Sources: GET /api/status health_issues that need a person, the
// scheduler, the connection, and GET /api/v2/health warn/bad checks (update available among them). Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const ALL_CLEAR = {audience: 'owner', checks: [{id: 'computers', label: 'Computers', status: 'ok', summary: '1 computer online.', fixes: []}],
  attention: 0, stuck: []};

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 860}, serviceWorkers: 'block'});
    const errors = [];
    let status = {cloud: true, keeper_alive: true, health_issues: [
      {kind: 'bot', title: 'Builder needs attention', detail: 'The bot is quarantined. It tried a refused action.', bot: 'builder',
        severity: 'error', needs_person: true, action: 'Review the refused action, then resume the bot on its page.'},
      // Diagnostics that do not need a person never light the dot.
      {kind: 'bot', title: 'Writer needs attention', detail: 'Usage limit; resumes at 14:00 UTC', bot: 'writer',
        severity: 'warning', needs_person: false}]};
    let health = {audience: 'owner', stuck: [], attention: 2, checks: [
      // Warning listed first by the server: the menu still puts what is broken first.
      {id: 'version', label: 'Version', status: 'warn', summary: 'Tico 0.3.80 is available. You are on 0.3.79.',
        fixes: [{label: 'Update', href: '', tab: '', click: '#new-version'}, {label: 'What is new', href: '#/changelog'}]},
      {id: 'models', label: 'Models', status: 'bad', summary: "Builder's Claude login was rejected. Sign in again.",
        fixes: [{label: 'Open Credentials', href: '#/credentials'}]},
      {id: 'computers', label: 'Computers', status: 'ok', summary: '1 computer online.', fixes: []}]};
    const broken = health;
    let failStatus = false;
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url()), p = url.pathname;
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      const json = (body, code = 200) => route.fulfill({status: code, contentType: 'application/json', body: JSON.stringify(body)});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p.startsWith('/tico/ui/')) {
        const name = p.slice('/tico/ui/'.length), file = uiFile(name);
        if (fs.existsSync(file)) return route.fulfill({body: fs.readFileSync(file), contentType: name.endsWith('.html') ? 'text/html'
          : name.endsWith('.css') ? 'text/css' : name.endsWith('.js') ? 'application/javascript' : 'application/octet-stream'});
      }
      if (p === '/api/me') return json({id: 'chris', email: 'chris@tico.team', role: 'owner', cloud: true, config: {company_name: 'Tico'}});
      if (p === '/api/employees') return json([]);
      if (p === '/api/humans') return json({people: [{id: 'chris', name: 'Chris'}], org_groups: []});
      if (p === '/api/status') return failStatus ? json({error: {detail: 'down'}}, 503) : json(status);
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/health') return json(health);
      if (p === '/api/v2/health-monitor') return json({on: false, findings: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      return json(unrouted(p));
    });
    await page.goto('http://tico-ui.test/#/tasks');
    await page.waitForFunction(() => !document.querySelector('#account .account-email')?.textContent.includes('Signing in'));
    // Keep the auto-opening Needs you panel out of the way: this test is about the sidebar.
    await page.evaluate(() => localStorage.setItem('tico.needsYou', JSON.stringify({snoozedUntil: Date.now() + 3600e3})));
    const settle = async () => { await page.evaluate(async () => { await refresh(false); await hlRefresh(); }); };
    await settle();
    // Status dots in the sidebar footer, whatever their class: what a person sees as a small coloured circle.
    const dots = () => page.evaluate(() => [...document.querySelectorAll('.side-utility *')].filter(el => {
      if (el.closest('.account-status')) return false;   // the bullets of the lines themselves
      const box = el.getBoundingClientRect(), css = getComputedStyle(el);
      return box.width > 0 && box.width <= 12 && Math.abs(box.width - box.height) < 1 && parseFloat(css.borderRadius) >= box.width / 2 - 1
        && css.backgroundColor !== 'rgba(0, 0, 0, 0)';
    }).length);
    const dot = page.locator('#account .account-dot');

    // Broken and needs Chris: one red dot, left of the name, and still one with the menu open.
    assert.equal(await dots(), 1, 'one status dot with the menu closed');
    assert.equal(await dot.isVisible(), true);
    assert.equal(await dot.getAttribute('data-level'), 'bad');
    const dotBox = await dot.boundingBox(), nameBox = await page.locator('#account .account-email').boundingBox();
    assert.ok(dotBox.x + dotBox.width <= nameBox.x, 'the dot sits left of the name');
    await page.locator('#account').click();
    await page.locator('#account-menu').waitFor();
    assert.equal(await dots(), 1, 'one status dot with the menu open: none on Settings, none right of the name');
    assert.equal(await page.locator('#account-menu [data-nav="settings"] .hl-alert, #account .hl-alert').count(), 0);

    // The menu opens with one line per problem, worst first, each with its fix.
    const lines = page.locator('#account-menu .account-status-line');
    assert.deepEqual(await lines.locator('.account-status-text').allInnerTexts(), [
      'Builder needs attention: The bot is quarantined',
      "Models: Builder's Claude login was rejected",
      'Version: Tico 0.3.80 is available']);
    assert.deepEqual(await lines.evaluateAll(rows => rows.map(row => row.dataset.level)), ['bad', 'bad', 'warn']);
    assert.equal(await lines.nth(0).locator('a').getAttribute('href'), '#/bot/builder/more');
    assert.equal(await lines.nth(1).locator('a').getAttribute('href'), '#/credentials');
    assert.equal(await lines.nth(2).locator('[data-hl-click]').getAttribute('data-hl-click'), '#new-version', 'Update, as on Health');
    assert.equal(await lines.nth(2).locator('[data-hl-click]').getAttribute('title'), 'Update');
    const text = (await page.locator('#account-menu .account-status').innerText());
    assert.doesNotMatch(text, /Writer|health_issues|uncertain_work|\bbad\b|\bwarn\b/, 'no internal words, nothing that clears itself');
    await lines.nth(1).locator('a').click();
    // Credentials lives on the Tools page: the link lands there.
    await page.waitForFunction(() => ['#/credentials', '#/integrations'].includes(location.hash));
    assert.equal(await page.locator('#account-menu').isHidden(), true, 'a fix closes the menu');

    // Hovering the dot (desktop) shows the same lines without opening the menu.
    await page.mouse.move(0, 0);
    await dot.hover();
    await page.locator('#account-status-pop').waitFor();
    assert.equal(await page.locator('#account-status-pop .account-status-line').count(), 3);
    assert.equal(await page.locator('#account-menu').isHidden(), true);
    await page.mouse.move(700, 400);
    await page.locator('#account-status-pop').waitFor({state: 'hidden'});

    // A computer offline for two minutes (asleep, a restart): Health says bad, but it clears itself, so amber, one line.
    const offlineHealth = {audience: 'owner', stuck: [], attention: 2, computers: [{id: 'r1', label: 'Studio Mac', online: false}], checks: [
      {id: 'computers', label: 'Computers', status: 'bad', summary: 'Every computer is offline.', fixes: [{label: 'Open Computers', href: '#/settings', tab: 'devices'}]},
      {id: 'waiting', label: 'Bots waiting', status: 'bad', summary: '1 bot cannot run because their computer is offline: Builder.', fixes: [{label: 'Open Computers', href: '#/settings', tab: 'devices'}]},
      {id: 'github', label: 'GitHub', status: 'warn', summary: 'GitHub refused the app. Reinstall it.', fixes: [{label: 'Open GitHub', href: 'https://github.com/settings/installations'}]}]};
    const machine = {kind: 'machine', title: 'Studio Mac is offline', detail: 'Builder cannot run.', machine: 'r1', severity: 'warning', needs_person: false};
    status = {cloud: true, keeper_alive: true, health_issues: [machine]};
    health = offlineHealth;
    await settle();
    assert.equal(await dot.getAttribute('data-level'), 'warn', 'a short offline spell is never red');
    assert.deepEqual(await page.locator('#account-status-pop .account-status-text').allInnerTexts(),
      ['Computers: Every computer is offline', 'GitHub: GitHub refused the app'], 'bots waiting on it are the same cause');
    assert.equal(await page.locator('#account-status-pop .account-status-line').nth(1).locator('a').getAttribute('target'), '_blank',
      'a fix outside Tico opens in its own tab');
    // Past thirty minutes the server asks its owner: now it is red.
    status = {...status, health_issues: [{...machine, title: 'Studio Mac stopped checking in', severity: 'error', needs_person: true,
      action: 'Wake Studio Mac and check that Tico runs there.'}]};
    await settle();
    assert.equal(await dot.getAttribute('data-level'), 'bad');
    // No computer set up at all clears nothing: red.
    status = {cloud: true, keeper_alive: true, health_issues: []};
    health = {...offlineHealth, computers: [], checks: [{id: 'computers', label: 'Computers', status: 'bad', summary: 'No computer is set up. Bots need one to run.', fixes: []}]};
    await settle();
    assert.equal(await dot.getAttribute('data-level'), 'bad');

    // Only warnings: amber, never red.
    status = {...status, health_issues: [{kind: 'runner_checkout', title: 'Studio Mac has a Tico update', detail: 'Nothing is running, so nothing will be interrupted.',
      machine: 'r1', severity: 'warning', needs_person: true, restart: true}]};
    health = {...broken, attention: 1, checks: broken.checks.filter(check => check.id !== 'models')};
    await settle();
    assert.equal(await dot.getAttribute('data-level'), 'warn');
    assert.equal(await dots(), 1);

    // Lost connection clears by itself: amber, with Retry.
    failStatus = true;
    health = ALL_CLEAR;
    await settle();
    assert.equal(await dot.getAttribute('data-level'), 'warn');
    assert.equal(await page.locator('#heartbeat').getAttribute('aria-label'), "Can't reach Tico. Trying again");

    // All clear: no dot, no lines.
    failStatus = false;
    status = {cloud: true, keeper_alive: true, health_issues: []};
    await settle();
    assert.equal(await dot.isHidden(), true, 'no line, no dot');
    assert.equal(await dots(), 0);
    await page.locator('#account').click();
    assert.equal(await page.locator('#account-menu .account-status').isHidden(), true);
    assert.equal(await page.locator('#heartbeat').getAttribute('aria-label'), 'Cloud backend connected');

    // The cloud scheduler stopped: red, plain words.
    status = {cloud: true, keeper_alive: false, health_issues: []};
    await settle();
    assert.equal(await dot.getAttribute('data-level'), 'bad');
    assert.equal(await page.locator('#account-menu .account-status-text').innerText(), "Bots aren't running: the cloud scheduler stopped");
    assert.deepEqual(errors, []);
    console.log('account-status: ok');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
