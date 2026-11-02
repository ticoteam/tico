// Tico Cloud access (ui/app/cloud-access.js, backend/cloud_access.py) against a fake server. Off cloud nothing shows.
// Frozen: a slim strip over every page, "Out of credit · Add credit" to the billing page, the page below shortened to
// fit and still usable, on a desktop and a phone with no sideways scroll. Locked: the whole page is Pay to continue,
// with Open billing and the owner's Download your files, shown at once by any 402 `locked`; open again reloads the page
// by itself. Every request is intercepted. TICO_SHOTS=<dir> saves screenshots of both states.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const shots = process.env.TICO_SHOTS || '';
const BILLING = 'https://app.tico.dev/billing/boatyard';

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    let cloud = null, role = 'owner', lockTasks = false;   // lockTasks: one read answers 402 `locked`
    const serve = async route => {
      const url = new URL(route.request().url()), p = url.pathname;
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      const json = (body, code = 200) => route.fulfill({status: code, contentType: 'application/json', body: JSON.stringify(body)});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p.startsWith('/tico/ui/')) {
        const name = p.slice('/tico/ui/'.length), file = uiFile(name);
        if (fs.existsSync(file)) return route.fulfill({body: fs.readFileSync(file), contentType: name.endsWith('.css') ? 'text/css'
          : name.endsWith('.js') ? 'application/javascript' : 'application/octet-stream'});
      }
      if (p === '/api/me') return json({id: 'ana', email: 'ana@boatyard.example', role, cloud: true, config: {company_name: 'Boatyard'}});
      if (p === '/api/status') return json({cloud: true, keeper_alive: true, health_issues: [], active: [], queued: [], cloud_access: cloud});
      if (p === '/api/v2/cloud-access') return json({cloud});
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], org_groups: []});
      if (p === '/api/employees') return json([]);
      if (lockTasks && p === '/api/v2/goals')
        return json({error: {code: 'locked', detail: 'Pay to continue', retryable: false, cloud: {access: 'locked', reason: 'trial_over', billing_url: BILLING}}}, 402);
      return json(unrouted(p));
    };
    await page.route('**/*', serve);
    await page.addInitScript(() => localStorage.setItem('tico.needsYou', JSON.stringify({snoozedUntil: Date.now() + 3600e3})));
    let opened = 0;
    const open = async () => {
      await page.goto(`http://tico-ui.test/?open=${++opened}#/tasks`);   // a new query: a fresh load every time
      await page.waitForFunction(() => !document.querySelector('#account .account-email')?.textContent.includes('Signing in'));
      await page.evaluate(async () => { await refresh(false); });
    };
    const shown = sel => page.locator(sel).isVisible();

    // Off cloud: nothing.
    await open();
    assert.equal(await shown('#cloud-banner'), false);
    assert.equal(await shown('#cloud-lock'), false);
    assert.equal(await page.evaluate(() => document.documentElement.classList.contains('strip')), false);

    // Frozen: the strip with its one link; the sidebar sits below it and the page still works.
    cloud = {access: 'frozen', reason: 'out_of_credit', plan: 'trial', trial_days_left: 9, billing_url: BILLING};
    await page.evaluate(async () => { await refresh(false); });
    await page.locator('#cloud-banner').waitFor();
    assert.equal((await page.locator('#cloud-banner').innerText()).replace(/\s+/g, ' ').trim(), 'Out of credit Add credit');
    assert.equal(await page.locator('#cloud-banner a').getAttribute('href'), BILLING);
    const bar = await page.locator('#cloud-banner').boundingBox(), side = await page.locator('#side').boundingBox();
    assert.ok(bar.height <= 32 && bar.y === 0, 'a slim strip at the top');
    assert.ok(side.y >= bar.height - 1, 'the sidebar starts below the strip');
    assert.equal(await shown('#cloud-lock'), false);
    await page.locator('#side a[data-nav="docs"]').click();
    assert.equal(await shown('#cloud-banner'), true, 'on every page');
    if (shots) await page.screenshot({path: path.join(shots, 'cloud-frozen-desktop.png')});
    await page.setViewportSize({width: 390, height: 780});
    await page.waitForTimeout(100);
    assert.equal(await shown('#cloud-banner'), true);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways scroll on a phone');
    if (shots) await page.screenshot({path: path.join(shots, 'cloud-frozen-phone.png')});
    await page.setViewportSize({width: 1280, height: 800});

    // Locked by the poll: Pay to continue over everything, with billing and the owner's download.
    cloud = {access: 'locked', reason: 'trial_over', plan: 'trial', trial_days_left: 0, billing_url: BILLING};
    await page.evaluate(async () => { await cloudAccessPoll(); });
    await page.locator('#cloud-lock').waitFor();
    assert.equal(await shown('#cloud-banner'), false);
    assert.equal(await page.locator('#cloud-lock h1').innerText(), 'Pay to continue');
    assert.equal(await page.locator('#cloud-lock [data-cloud-why]').innerText(), 'Your trial has ended.');
    assert.equal(await page.locator('#cloud-lock [data-cloud-pay]').getAttribute('href'), BILLING);
    const download = page.locator('#cloud-lock [data-cloud-download]');
    assert.equal(await download.isVisible(), true);
    assert.equal(await download.getAttribute('href'), '/api/v2/system/storage/export');
    assert.equal(await page.locator('#side').isVisible(), false, 'nothing else shows');
    if (shots) await page.screenshot({path: path.join(shots, 'cloud-locked-desktop.png')});
    await page.setViewportSize({width: 390, height: 780});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    if (shots) await page.screenshot({path: path.join(shots, 'cloud-locked-phone.png')});
    await page.setViewportSize({width: 1280, height: 800});

    // Paid: open again reloads the page by itself, and nothing shows.
    cloud = {access: 'open', reason: null, plan: 'starter', trial_days_left: null, billing_url: BILLING};
    await Promise.all([page.waitForEvent('load'), page.evaluate(() => { void cloudAccessPoll(); })]);
    await page.waitForFunction(() => !document.querySelector('#account .account-email')?.textContent.includes('Signing in'));
    assert.equal(await shown('#cloud-lock'), false);
    assert.equal(await shown('#cloud-banner'), false);

    // Any 402 `locked` shows it at once, before a poll says so; a member gets no download.
    role = 'viewer';
    lockTasks = true;
    await open();
    await page.evaluate(() => get('/v2/goals').catch(() => null));   // any read the server refuses 402 `locked`
    await page.locator('#cloud-lock').waitFor();
    assert.equal(await page.locator('#cloud-lock [data-cloud-pay]').getAttribute('href'), BILLING);
    await page.waitForTimeout(100);
    assert.equal(await page.locator('#cloud-lock [data-cloud-download]').isVisible(), false);
    assert.deepEqual(errors, [], errors.join('; '));
    console.log('cloud access: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
