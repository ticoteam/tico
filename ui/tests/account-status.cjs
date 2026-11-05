// The account button's one status dot and its menu (Amendment v3, _plan-maintenance.md, Chris Nov 4): two states only.
// "Can't reach Tico" is amber, clears by itself and offers Retry. "Bots aren't running" is red: the cloud scheduler or
// Tico on this computer stopped, or no computer is online at all. Health's checks, the person's operation issues,
// computer restarts and a bot's stopped-runs Review never reach the menu, the dot or its hover card. Settings > Health
// lists the checks, and a bot's own page holds its Review. Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

// Each of these lit the dot or filled the menu before Amendment v3. None may do so now.
const NOISE_HEALTH = {audience: 'owner', stuck: [], attention: 3, computers: [{id: 'r1', label: 'Studio Mac', online: true, runtimes: []}], failures: [], checks: [
  {id: 'version', label: 'Version', status: 'warn', summary: 'Tico 0.3.80 is available. You are on 0.3.79.', fixes: [{label: 'Update', href: '', tab: '', click: '#new-version'}]},
  {id: 'models', label: 'Models', status: 'bad', summary: "Builder's Claude login was rejected. Sign in again.", fixes: [{label: 'Open Credentials', href: '#/credentials'}]},
  {id: 'computers', label: 'Computers', status: 'ok', summary: '1 computer online.', fixes: []}]};
const NOISE_ISSUES = [
  // A Tico update on a computer: its Restart lives in Settings > Health, never in the menu.
  {kind: 'runner_checkout', title: 'Studio Mac has a Tico update', detail: 'Nothing is running, so nothing will be interrupted.',
    machine: 'r1', severity: 'warning', needs_person: true, restart: true},
  // A person's operation issue: listed in Settings > Health only.
  {kind: 'machine', machine: 'r2', title: 'Acme laptop stopped checking in', detail: 'Builder cannot run until it reconnects.',
    severity: 'error', needs_person: true, action: 'Wake it and check that Tico runs there.'},
  // Builder's stopped run holds work: its Review is on Builder's page, not in the menu.
  {kind: 'uncertain_work', title: 'Builder has 1 stopped run holding up work', bot: 'builder', detail: 'Its computer interrupted this run.',
    severity: 'error', needs_person: false, action: 'review', can_review: true, held_back: 1}];

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const errors = [];
    let status = {cloud: true, keeper_alive: true, health_issues: NOISE_ISSUES};
    let health = NOISE_HEALTH;
    let failStatus = false;
    const serve = async route => {
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
      if (p === '/api/employees') return json([{id: 'builder', name: 'Builder', role: 'Builder'}]);
      if (p === '/api/humans') return json({people: [{id: 'chris', name: 'Chris'}], org_groups: []});
      if (p === '/api/status') return failStatus ? json({error: {detail: 'down'}}, 503) : json(status);
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/health') return json(health);
      if (p === '/api/v2/health/findings') return json({findings: [], botops: true, reports_without_asking: false});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/bots/builder/execution-review') return json({jobs: []});
      return json(unrouted(p));
    };
    const page = await browser.newPage({viewport: {width: 1280, height: 860}, serviceWorkers: 'block'});
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', serve);
    await page.goto('http://tico-ui.test/#/tasks');
    await page.waitForFunction(() => !document.querySelector('#account .account-email')?.textContent.includes('Signing in'));
    const settle = async () => { await page.evaluate(async () => { await refresh(false); await hlRefresh(); }); };
    // Status dots in the sidebar footer, whatever their class: what a person sees as a small coloured circle.
    const dots = () => page.evaluate(() => [...document.querySelectorAll('.side-utility *')].filter(el => {
      if (el.closest('.account-status')) return false;   // the bullets of the lines themselves
      const box = el.getBoundingClientRect(), css = getComputedStyle(el);
      return box.width > 0 && box.width <= 12 && Math.abs(box.width - box.height) < 1 && parseFloat(css.borderRadius) >= box.width / 2 - 1
        && css.backgroundColor !== 'rgba(0, 0, 0, 0)';
    }).length);
    const dot = page.locator('#account .account-dot');
    const menuLines = async () => { await page.evaluate(() => setAccountMenu(true)); await page.locator('#account-menu').waitFor(); return page.locator('#account-menu .account-status-line'); };
    const texts = locator => locator.locator('.account-status-text').allInnerTexts();

    // Health's warn and bad checks, the operation issue and the update line light nothing, and the menu lists none of them.
    await settle();
    assert.equal(await dot.isHidden(), true, 'no line, no dot');
    assert.equal(await dots(), 0);
    assert.equal(await page.locator('#heartbeat').isHidden(), true);
    const none = await menuLines();
    assert.equal(await none.count(), 0);
    assert.doesNotMatch(await page.locator('#account-menu').innerText(), /Review|Restart|Stopped runs|checking in/);
    assert.equal(await page.locator('#account-menu [data-runner-restart], #account-menu [data-execution-review], #account-menu .hl-alert').count(), 0);
    await page.evaluate(() => setAccountMenu(false));

    // Lost connection clears by itself: amber, one line, with Retry.
    failStatus = true;
    await settle();
    assert.equal(await dot.getAttribute('data-level'), 'warn');
    assert.equal(await page.locator('#heartbeat').getAttribute('aria-label'), "Can't reach Tico. Trying again");
    const retry = await menuLines();
    assert.deepEqual(await texts(retry), ["Can't reach Tico. Trying again"]);
    failStatus = false;
    await retry.locator('[data-account-retry]').click();
    await page.locator('#heartbeat').waitFor({state: 'hidden'});
    assert.equal(await dots(), 0);

    // The cloud scheduler stopped: red, one line whose Fix opens Health and closes the menu.
    status = {cloud: true, keeper_alive: false, health_issues: NOISE_ISSUES};
    await settle();
    assert.equal(await dot.getAttribute('data-level'), 'bad');
    const stopped = await menuLines();
    assert.deepEqual(await texts(stopped), ["Bots aren't running: the cloud scheduler stopped"]);
    assert.equal(await stopped.first().getAttribute('data-level'), 'bad');
    assert.equal(await stopped.first().locator('a').getAttribute('href'), '#/settings');
    assert.equal(await stopped.first().locator('a').getAttribute('data-gs-tab'), 'health', 'Fix opens Health');
    // Hovering the dot (desktop) shows the same line without opening the menu.
    await page.evaluate(() => setAccountMenu(false));
    await page.mouse.move(0, 0);
    await dot.hover();
    await page.locator('#account-status-pop .account-status-line').first().waitFor();
    assert.deepEqual(await texts(page.locator('#account-status-pop .account-status-line')), ["Bots aren't running: the cloud scheduler stopped"]);
    assert.equal(await page.locator('#account-menu').isHidden(), true);
    await page.mouse.move(700, 400);
    await page.locator('#account-status-pop').waitFor({state: 'hidden'});
    // The Fix link goes to Health and closes the menu.
    await (await menuLines()).locator('a').click();
    await page.waitForFunction(() => location.hash.startsWith('#/settings'));
    assert.equal(await page.locator('#account-menu').isHidden(), true, 'a fix closes the menu');

    // Tico on this computer stopped: the same red, its own words.
    status = {cloud: false, keeper_alive: false, health_issues: NOISE_ISSUES};
    await settle();
    assert.deepEqual(await texts(await menuLines()), ["Tico stopped on this computer, so bots aren't running"]);

    // No computer online: red. One computer back clears it, since one online computer means bots can run.
    status = {cloud: true, keeper_alive: true, health_issues: NOISE_ISSUES};
    health = {...NOISE_HEALTH, computers: [{id: 'r1', label: 'Studio Mac', online: false, runtimes: []}, {id: 'r2', label: 'Acme laptop', online: false, runtimes: []}]};
    await settle();
    assert.equal(await dot.getAttribute('data-level'), 'bad');
    const offline = await menuLines();
    assert.deepEqual(await texts(offline), ["Bots aren't running: no computer is online"]);
    assert.equal(await offline.first().locator('a').getAttribute('href'), '#/settings');
    health = {...NOISE_HEALTH, computers: [{id: 'r1', label: 'Studio Mac', online: false, runtimes: []}, {id: 'r2', label: 'Acme laptop', online: true, runtimes: []}]};
    await settle();
    await page.locator('#heartbeat').waitFor({state: 'hidden'});
    // No computer set up at all is no computer online.
    health = {...NOISE_HEALTH, computers: []};
    await settle();
    assert.equal(await dot.getAttribute('data-level'), 'bad');
    health = NOISE_HEALTH;
    await settle();
    await page.locator('#heartbeat').waitFor({state: 'hidden'});

    // The Review for stopped runs is on Builder's page, moved out of the menu: one control, and the menu has none.
    await page.evaluate(() => setAccountMenu(false));
    await page.evaluate(() => { location.hash = '#/bot/builder/more'; });
    const review = page.locator('#bot-review-host [data-bot-review]');
    await review.waitFor();
    assert.equal(await page.locator('[data-bot-review]').count(), 1, 'moved, not duplicated');
    await page.evaluate(() => setAccountMenu(true));
    assert.doesNotMatch(await page.locator('#account-menu').innerText(), /Review/, 'no Review in the menu');
    await page.evaluate(() => setAccountMenu(false));
    await review.click();
    await page.locator('dialog.stopped-runs-dialog h2', {hasText: 'Stopped runs'}).waitFor();
    await page.locator('dialog.stopped-runs-dialog [data-close]').click();

    // Phone: one line in the menu, fits the screen, no horizontal page scroll, and its Fix closes the menu.
    status = {cloud: true, keeper_alive: true, health_issues: NOISE_ISSUES};
    health = {...NOISE_HEALTH, computers: []};
    const phone = await browser.newPage({viewport: {width: 390, height: 844}, hasTouch: true, isMobile: true, serviceWorkers: 'block'});
    phone.on('pageerror', e => errors.push(e.message));
    await phone.route('**/*', serve);
    await phone.goto('http://tico-ui.test/#/tasks');
    await phone.evaluate(async () => { await refresh(false); await hlRefresh(); setDrawer(true); });
    await phone.locator('#account').tap();
    const phoneLines = phone.locator('#account-menu .account-status-line');
    await phoneLines.first().waitFor();
    assert.deepEqual(await texts(phoneLines), ["Bots aren't running: no computer is online"]);
    const menu = await phone.locator('#account-menu').boundingBox();
    assert.ok(menu.x >= 0 && menu.x + menu.width <= 390, `the menu fits a phone: ${JSON.stringify(menu)}`);
    assert.equal(await phone.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, 'no horizontal page scroll');
    await phone.locator('#account-menu .account-status-line a').tap();
    assert.equal(await phone.locator('#account-menu').isHidden(), true, 'touch navigation closes the menu');
    await phone.close();
    assert.deepEqual(errors, []);
    console.log('account-status: ok');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
