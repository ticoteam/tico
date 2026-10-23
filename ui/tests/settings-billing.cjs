// Settings > Billing against a fake server: the owner's balance and one status line, credits with past ones (and what
// expired) behind "Show past credits", a day opening into hours with "<$0.01" under a cent, the month's statement with
// its CSV link, and "Talk to us to add credit" opening the booking link with no card form; the empty state and the
// record-only line; a member has no Billing tab and never asks for it; no sideways scroll on a phone. Every request is
// intercepted. TICO_SHOTS=<dir> saves screenshots (desktop and phone, dark and light).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const shots = process.env.TICO_SHOTS || '';

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const statement = {month: '2026-11', opening_micros: 0, credits_micros: 25_000_000, credits_by_source: {grant: 25_000_000},
      debits_micros: 1_250_003, debits_by_product: {gateway: 1_250_003, hosting: 0, support: 0}, expired_micros: 4_000_000,
      closing_micros: 19_749_997};
    const full = {state: 'low', balance_micros: 19_749_997, min_balance_micros: 0, last_day_micros: 20_000_000, record_only: false,
      grace_until: null, month: '2026-11', months: ['2026-11', '2026-10'], booking_url: 'https://cal.acme.example/tico',
      credits: [
        {label: 'Grant', source: 'grant', amount_micros: 20_000_000, left_micros: 19_749_997, expired_micros: 0, expires: null,
          expired: false, used_up: false, current: true},
        {label: 'Grant', source: 'grant', amount_micros: 5_000_000, left_micros: 4_000_000, expired_micros: 4_000_000,
          expires: '2026-11-04T00:00:00.000000Z', expired: true, used_up: false, current: false}],
      usage: [{period: '2026-11-02', total_micros: 1_250_003, lines: [
        {product: 'gateway', sku: 'tico-mid-1.0:input', amount_micros: 1_250_000},
        {product: 'gateway', sku: 'tico-cheap-1.1:output', amount_micros: 3}]}],
      statement};
    let billing = full, role = 'owner', hoursDown = true;
    const slow = {};                                       // month -> ms its answer waits
    const asked = [];
    await page.route('**/*', route => {
      const url = new URL(route.request().url()), p = url.pathname;
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.startsWith('/vendor/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({body: fs.readFileSync(uiFile(p.slice(1))),
        contentType: p.endsWith('.woff2') ? 'font/woff2' : 'application/javascript'});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const config = {version: '0.3.0', app_name: 'Tico'};
      if (p === '/api/me') return json({id: 'ana', role, name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
      if (p === '/api/v2/config') return json(config);
      if (p.startsWith('/api/v2/billing')) {
        asked.push(p + url.search);
        if (p === '/api/v2/billing/usage') {
          if (hoursDown) { hoursDown = false; return json({state: 'unreachable'}); }   // the first read fails
          return json({state: 'ok', day: '2026-11-02', hours: [
            {period: '2026-11-02T09', total_micros: 1_250_003, lines: billing.usage[0].lines}]});
        }
        const month = url.searchParams.get('month') || billing.month;
        const answer = billing.statement ? {...billing, month, statement: {...billing.statement, month}} : billing;
        return new Promise(done => setTimeout(done, slow[month] || 0)).then(() => json(answer));
      }
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
    const shot = async name => { if (shots) await page.screenshot({path: path.join(shots, name + '.png'), fullPage: true}); };
    const host = page.locator('#set-billing');

    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('#settings-more > summary').click();
    await page.locator('[data-settings-tab=billing]').click();
    await host.locator('.billing-balance').waitFor();
    assert.equal(await host.locator('.billing-balance').innerText(), '$19.75');
    assert.equal(await host.locator('.billing-status').innerText(), 'Low balance: Tico models stop when your balance reaches $0.00');
    // credits: the current one in the table; the expired one behind "Show past credits", with what expired
    const past = host.locator('.billing-past');
    assert.match(await past.locator('summary').innerText(), /^Show past credits \(1\)$/);
    assert.equal(await past.evaluate(el => el.open), false);
    await past.locator('summary').click();
    assert.match((await past.innerText()).replace(/\s+/g, ' '), /Grant \$5\.00 \$4\.00 Nov 4, 2026 Expired · \$4\.00 expired/);
    assert.match((await host.locator('.billing-table').first().innerText()).replace(/\s+/g, ' '), /Grant \$20\.00 \$19\.75 Never/);
    // a day opens into hours; under a cent says so, the day's total is to the cent
    const day = host.locator('[data-billing-day="2026-11-02"]');
    assert.match((await day.locator('summary').innerText()).replace(/\s+/g, ' '), /2026-11-02 \$1\.25/);
    // HQ not answering says so, and the day asks again when it is opened again
    await day.locator('summary').click();
    await day.getByText('Tico HQ did not answer. Try again in a minute.').waitFor();
    await day.locator('summary').click();
    await day.locator('summary').click();
    await day.locator('.billing-hour').waitFor();
    assert.match((await day.innerText()).replace(/\s+/g, ' '), /Tico models tico-cheap-1\.1 <\$0\.01/);
    assert.match((await day.locator('.billing-hour-head').innerText()).replace(/\s+/g, ' '), /^09:00 UTC \$1\.25$/);
    assert(asked.includes('/api/v2/billing/usage?day=2026-11-02'), asked.join());
    // the statement: opening + credits - debits - expired = closing, and its CSV
    assert.match((await host.locator('.billing-statement').innerText()).replace(/\s+/g, ' '),
      /^Opening balance \$0\.00 Credits: Grant \$25\.00 Debits: Tico models -\$1\.25 Debits: Hosting \$0\.00 Debits: Support \$0\.00 Expired credit -\$4\.00 Closing balance \$19\.75$/);
    assert.equal(await host.locator('[data-billing-csv]').getAttribute('href'), '/api/v2/billing/statements/2026-11.csv');
    // "Talk to us to add credit" opens the booking link in a new tab; no card form anywhere
    const talk = host.locator('[data-billing-talk]');
    assert.equal(await talk.innerText(), 'Talk to us to add credit');
    assert.equal(await talk.getAttribute('href'), 'https://cal.acme.example/tico');
    assert.equal(await talk.getAttribute('target'), '_blank');
    assert.equal(await host.locator('input, form').count(), 0);
    for (const mode of ['dark', 'light']) { await theme(mode); await shot(`billing-desktop-${mode}`); }
    // months picked quickly: the older answer arriving last does not replace the newer month
    slow['2026-10'] = 800;
    await host.locator('[data-billing-month]').selectOption('2026-10');
    await page.evaluate(() => { BILLING_MONTH = '2026-11'; void renderSettingsBilling(); });
    for (let i = 0; i < 100 && !asked.includes('/api/v2/billing?month=2026-11'); i++) await page.waitForTimeout(50);
    await page.waitForTimeout(1200);
    assert(asked.includes('/api/v2/billing?month=2026-10'), asked.join());
    assert.equal(await host.locator('[data-billing-csv]').getAttribute('href'), '/api/v2/billing/statements/2026-11.csv');
    assert.equal(await host.locator('[data-billing-month]').inputValue(), '2026-11');
    // the status lines
    const status = async (state, extra = {}) => {
      billing = {...full, state, ...extra};
      await page.evaluate(() => renderSettingsBilling());
      return host.locator('.billing-status').innerText();
    };
    assert.equal(await status('active'), 'Active');
    assert.equal(await status('paused', {min_balance_micros: 1_000_000}), 'Tico models paused: balance reached $1.00. Your bots are using their other models.');
    assert.equal(await status('grace', {grace_until: '2026-11-04T09:00:00.000000Z'}),
      'Hosting grace period: servers stop on November 4, 2026 at 09:00 UTC unless credit is added.');
    // a phone: nothing scrolls sideways
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForTimeout(300);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways scroll on a phone');
    for (const mode of ['dark', 'light']) { await theme(mode); await shot(`billing-phone-${mode}`); }
    await page.setViewportSize({width: 1280, height: 800});
    // no credit yet on a record-only account: its balance, the record-only line and the empty-state text
    billing = {...full, state: 'active', record_only: true, balance_micros: 0, credits: [], usage: []};
    await page.evaluate(() => renderSettingsBilling());
    assert.match((await host.innerText()).replace(/\s+/g, ' '),
      /^\$0\.00 Active Usage is recorded but not charged yet\. Talk to us to add credit Credits No credit yet\. Tico models and Hosting start once credit is added\./);
    // no credit, but an enforced account below a negative minimum in Hosting's grace: balance and status still show
    billing = {...full, state: 'grace', grace_until: '2026-11-04T09:00:00.000000Z', balance_micros: -5_000_000,
      min_balance_micros: -5_000_000, credits: []};
    await page.evaluate(() => renderSettingsBilling());
    assert.equal(await host.locator('.billing-balance').innerText(), '-$5.00');
    assert.match(await host.locator('.billing-status').innerText(), /^Hosting grace period: servers stop on November 4, 2026/);
    // no account at HQ yet: the empty state, with HQ's booking link
    billing = {state: 'no_account', booking_url: 'https://cal.acme.example/tico'};
    await page.evaluate(() => renderSettingsBilling());
    assert.match(await host.innerText(), /No credit yet/);
    assert.equal(await host.locator('[data-billing-talk]').getAttribute('href'), 'https://cal.acme.example/tico');
    // a member: no Billing tab, and the page never asks for billing
    role = 'human';
    const before = asked.length;
    await page.goto('about:blank');
    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('#settings-tabs').waitFor();
    assert.equal(await page.locator('[data-settings-tab=billing]').count(), 0);
    await page.evaluate(() => settingsShow('billing'));          // a saved link to the tab lands on another one
    assert.notEqual(await page.evaluate(() => SETTINGS_TAB), 'billing');
    assert.equal(await page.locator('#settings-billing').count(), 0);
    assert.equal(asked.length, before);
    assert.deepEqual(errors, []);
    console.log('PASS: Settings > Billing, owner and admins only, status lines, past credits, hours, statement and CSV, empty state, phone');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
