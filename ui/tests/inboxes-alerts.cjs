// The Alerts registry card: explicit consent and price, stable paid retries, opt-out, and phone-width settings.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}, serviceWorkers: 'block'});
    const errors = [], writes = [], receipts = new Set();
    page.on('pageerror', e => errors.push(e.message));
    const state = {phone: null, status: 'none', verified_at: null, call_consent: false,
      preferences: {urgent_needs: false, legal_mail: false, bot_stuck: false, urgent_calls: false, timezone: 'America/Los_Angeles',
        quiet_start: '22:00', quiet_end: '07:00', emergency_exception: false, monthly_cap: 10},
      monthly_spend: 0, company_monthly_cap: 50, company_monthly_spend: 0, price_usd: 1, paid_by: 'company', recent: []};
    let callConsent = false, sendAttempts = 0;
    await page.route('**/*', route => {
      const req = route.request(), url = new URL(req.url()), path = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const file = path.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (file && fs.existsSync(uiFile(file[1]))) return route.fulfill({contentType: file[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(file[1]), 'utf8')});
      if (path.startsWith('/vendor/') && fs.existsSync(uiFile(path.slice(1)))) return route.fulfill({body: fs.readFileSync(uiFile(path.slice(1)))});
      if (path.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (path === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (path.startsWith('/api/v2/alerts/')) {
        if (req.method() === 'GET') return json(state);
        const body = req.postDataJSON(), operation = req.headers()['idempotency-key'];
        writes.push({path, body, operation});
        if (path.endsWith('/phone/confirm')) { state.status = 'verified'; state.verified_at = '2026-11-04T18:00:00Z'; state.call_consent = callConsent; return json(state); }
        if (path.endsWith('/phone') && req.method() === 'POST') { state.phone = body.phone; state.status = 'code_sent'; callConsent = body.call_consent; return json({status: 'code_sent', expires_in: 600}); }
        if (path.endsWith('/phone') && req.method() === 'DELETE') { Object.assign(state, {phone: null, status: 'none', verified_at: null, call_consent: false}); return json(state); }
        if (path.endsWith('/test')) {
          if (!receipts.has(operation)) { receipts.add(operation); state.monthly_spend++; state.company_monthly_spend++; }
          if (++sendAttempts < 3) return json({error: {code: 'gateway_unavailable', detail: 'Receipt lost'}}, 502);
          return json({id: 'pa_test', status: 'accepted', price_usd: 1, paid_by: 'company'});
        }
        if (path.endsWith('/company')) { state.company_monthly_cap = body.monthly_cap; return json({monthly_cap: body.monthly_cap, monthly_spend: state.company_monthly_spend, price_usd: 1}); }
        Object.assign(state.preferences, body); return json(state);
      }
      const config = {version: '0.3.0', app_name: 'Tico'};
      if (path === '/api/me' || path === '/api/v2/me') return json({id: 'ana', actor: 'human:ana', role: 'owner', name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
      if (path === '/api/v2/config') return json(config);
      if (path === '/api/employees' || path === '/api/issues') return json([]);
      if (path === '/api/humans' || path === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (path === '/api/status') return json({active: [], queued: [], recent_runs: [], health_issues: []});
      if (path === '/api/v2/status') return json({bots: []});
      if (path === '/api/v2/needs-you') return json({items: []});
      if (path === '/api/v2/operations') return json({machines: [], services: []});
      if (path.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(path));
    });
    await page.goto('https://tico-ui.test/#/settings/company-inbox');
    await page.waitForFunction(() => typeof inboxesAlerts === 'function' && S.me?.id);
    const draw = admin => page.evaluate(async admin => {
      let host = document.querySelector('#alerts-test-host');
      if (!host) { document.querySelector('#main').innerHTML = '<div id="alerts-test-host"></div>'; host = document.querySelector('#alerts-test-host'); }
      window.ALERT_CTX = {admin, me: S.me, live: () => true};
      await INBOX_SETTINGS.find(s => s.key === 'texts-alerts').render(host, window.ALERT_CTX);
    }, admin);
    await draw(true);
    const card = page.locator('#inboxes-alerts');
    assert(await card.locator('[data-alert-send-code]').isDisabled(), 'SMS consent is an explicit choice');
    await card.locator('[name=phone]').fill('+14155550123');
    await card.locator('[name=sms_consent]').check();
    await card.locator('[name=call_consent]').check();
    await card.locator('[data-alert-send-code]').click();
    await card.locator('[name=code]').waitFor();
    assert.deepEqual(writes[0].body, {phone: '+14155550123', sms_consent: true, call_consent: true});
    await card.locator('[name=code]').fill('123456');
    await card.locator('[data-alert-confirm] button').click();
    await card.locator('[data-alert-test]:enabled').waitFor();
    await card.locator('[name=legal_mail]').check();
    await card.locator('[name=quiet]').uncheck();
    await card.locator('[name=monthly_cap]').first().fill('2');
    await card.locator('[data-alert-preferences] button[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#ib-alert-cap')?.value === '2' && !document.querySelector('[name=quiet]')?.checked);
    const saved = writes.find(w => w.path === '/api/v2/alerts/me');
    assert.equal(saved.body.legal_mail, true); assert.equal(saved.body.quiet_start, ''); assert.equal(saved.body.quiet_end, '');
    assert.equal(saved.body.timezone, 'America/Los_Angeles');
    await card.locator('[data-alert-test]').click();
    await page.locator('dialog.settings-confirm').waitFor();
    assert.equal(writes.filter(w => w.path.endsWith('/test')).length, 0, 'no paid send before confirming the disclosed price');
    await page.locator('[data-confirm-ok]').click();
    await page.waitForFunction(() => [...document.querySelectorAll('.toast')].some(el => el.textContent.includes('Test accepted')));
    const tests = writes.filter(w => w.path.endsWith('/test'));
    assert.equal(tests.length, 3); assert.equal(new Set(tests.map(w => w.operation)).size, 1); assert.equal(receipts.size, 1);
    state.status = 'opted_out'; state.phone = '<img src=x onerror="window.ALERT_PWNED=1">';
    await draw(true);
    assert(await card.locator('[data-alert-test]').isDisabled());
    assert.match(await card.innerText(), /Opted out/);
    assert.equal(await card.locator('img').count(), 0); assert.equal(await page.evaluate(() => window.ALERT_PWNED), undefined);
    for (const width of [320, 390, 1440]) {
      await page.setViewportSize({width, height: 900});
      for (const dark of [false, true]) {
        await page.evaluate(dark => document.documentElement.dataset.theme = dark ? 'dark' : 'light', dark);
        assert(await card.evaluate(el => el.scrollWidth <= el.clientWidth + 1), 'Alerts fits ' + width);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'page fits ' + width);
        if (process.env.TICO_SHOTS) await page.screenshot({path: process.env.TICO_SHOTS + '/alerts-' + width + (dark ? '-dark' : '-light') + '.png'});
      }
    }
    await draw(false);
    assert.equal(await card.locator('[data-alert-company]').count(), 0, 'members do not edit the company cap');
    state.phone = '+14155550123'; state.status = 'verified';
    await draw(true);
    await card.locator('[data-alert-remove]').click();
    await page.locator('[data-confirm-ok]').click();
    await card.locator('[data-alert-add]').waitFor();
    assert(writes.some(w => w.path.endsWith('/phone') && w.body && w.body.phone === undefined));
    assert.deepEqual(errors, []);
    await page.close();
    console.log('Personal alerts: consent, price, retry identity, preferences, opt-out, revoke, and responsive themes passed');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
