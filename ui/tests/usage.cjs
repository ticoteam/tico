// Offline browser regression: the Usage page (account menu > Usage). The range switch asks for the right days, the rows
// are sorted by spend whatever order they arrive in, a subscription's figure stays apart from spend, a bot opens to its
// daily chart and routines and a CSV, and an empty range says so.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');

const day = n => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);
const figures = (runs, i, c, o, est, sub = 0) => ({runs, input_tokens: i, cached_tokens: c, output_tokens: o, est_cost_usd: est, subscription_equiv_usd: sub, unpriced_runs: 0});
const limit = (daily, monthly, percent = 0, blocked = null) => ({daily_usd: daily, monthly_usd: monthly, own_daily_usd: daily, own_monthly_usd: monthly, percent, blocked, may_edit: true, source: {}});
// Arriving in the wrong order on purpose.
const ROWS = [
  {bot: 'sales', name: 'Sales', department: 'Revenue', ...figures(3, 1000, 2000, 500, 0.4), share: 0.05, limit: limit(null, null)},
  {bot: 'support', name: 'Support', department: 'Revenue', ...figures(12, 900000, 2100000, 60000, 6.1, 1.5), share: 0.8, limit: limit(5, 50, 122, 'daily')},
  {bot: 'inbox', name: 'Inbox', department: null, ...figures(5, 400, 0, 50, null), share: 0, limit: limit(null, 20, 85)},
  {bot: 'coo', name: 'COO', department: 'Ops', ...figures(4, 5000, 5000, 900, 0, 2.2), share: 0.15, limit: limit(null, null)},
];
const total = rows => rows.reduce((t, r) => ({runs: t.runs + r.runs, est_cost_usd: t.est_cost_usd + (r.est_cost_usd || 0), subscription_equiv_usd: t.subscription_equiv_usd + r.subscription_equiv_usd,
                                              input_tokens: 0, cached_tokens: 0, output_tokens: 0, unpriced_runs: 0}), {runs: 0, est_cost_usd: 0, subscription_equiv_usd: 0, input_tokens: 0, cached_tokens: 0, output_tokens: 0, unpriced_runs: 0});

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const ctx = await browser.newContext({viewport: {width: 1440, height: 900}, serviceWorkers: 'block', acceptDownloads: true});
    const page = await ctx.newPage();
    const errors = [], asked = [];
    let empty = false;
    const puts = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(path.join(__dirname, '..', ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(path.join(__dirname, '..', ui[1]), 'utf8')});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const config = {version: '0.2.18', update: null, app_name: 'Tico', usage_count_notice: false};
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/v2/usage/limits' && req.method() === 'GET') return json({default: {daily_usd: 20, monthly_usd: null, count_subscription: false}, may_edit_default: true, bots: {}});
      if (p.startsWith('/api/v2/usage/limits') && req.method() === 'PUT') { puts.push([p, JSON.parse(req.postData())]); return json({ok: true}); }
      if (p === '/api/v2/usage') {
        const q = Object.fromEntries(url.searchParams);
        asked.push(q);
        if (q.bot) {
          const days = [], from = new Date(q.from + 'T00:00:00Z'), to = new Date(q.to + 'T00:00:00Z');
          for (let d = from, n = 0; d <= to; d = new Date(+d + 864e5), n++)
            days.push({day: d.toISOString().slice(0, 10), ...figures(n % 2 ? 2 : 0, 100, 0, 10, n % 3 ? 1 + n : 0, n % 2 ? 0.5 : 0)});
          return json({from: q.from, to: q.to, prices_as_of: '2026-09-29', bot: q.bot, name: 'Support', department: 'Revenue', totals: figures(12, 0, 0, 0, 6.1, 1.5), daily: days,
                       routines: [{routine: 's1', title: 'Morning triage', ...figures(7, 0, 0, 0, 4.2)}, {routine: null, title: 'Other runs', ...figures(5, 0, 0, 0, 1.9, 1.5)}]});
        }
        const rows = empty ? [] : ROWS.filter(r => !q.department || r.department === q.department);
        return json({from: q.from, to: q.to, prices_as_of: '2026-09-29', group: 'bot', department: q.department || null, totals: total(rows), rows, departments: ['Ops', 'Revenue']});
      }
      if (p === '/api/employees' || p === '/api/issues') return json([]);
      if (p === '/api/humans') return json({people: [], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json({});
    });
    await page.goto('https://tico-ui.test/#/updates');
    await page.waitForFunction(() => !document.querySelector('#account .account-email')?.textContent.includes('Signing in'));

    // The account menu lists Usage after Runs (Learnings may sit between them).
    await page.locator('#account').click();
    const items = await page.locator('#account-menu a:not([hidden])').evaluateAll(els => els.map(e => e.dataset.nav));
    assert.ok(items.includes('usage') && items.indexOf('usage') > items.indexOf('runs'), 'Usage follows Runs: ' + items);
    await page.locator('#account-menu a[data-nav=usage]').click();
    await page.locator('.use-row').first().waitFor();

    // Seven days by default, sorted by spend (estimate plus API-equivalent), with the total on top.
    assert.deepEqual([asked[0].from, asked[0].to], [day(6), day(0)]);
    assert.deepEqual(await page.locator('.use-name').allInnerTexts(), ['Support', 'COO', 'Sales', 'Inbox']);
    assert.match(await page.locator('.use-total').innerText(), /^Estimated\s+\$6\.50 · 24 runs/);
    assert.equal(await page.locator('.use-note').count(), 1, 'the word Estimated is said once');
    const support = page.locator('.use-row[data-bot=support]');
    assert.match(await support.locator('.use-runs').innerText(), /12 runs/);
    assert.match(await support.locator('.use-tok').innerText(), /3\.06M tokens/);
    assert.match(await support.locator('.use-cost').innerText(), /\$6\.10\s*≈ \$1\.50 API-equivalent/);
    assert.equal((await page.locator('.use-row[data-bot=coo] .use-cost').innerText()).trim(), '≈ $2.20 API-equivalent', 'a subscription is not spend');
    assert.equal((await page.locator('.use-row[data-bot=inbox] .use-cost').innerText()).trim(), '—', 'a model with no price has no cost');
    assert.match(await page.locator('.use-row[data-bot=inbox] .use-tok').innerText(), /450 tokens/);
    assert.equal(await page.locator('.use-row[data-bot=support] .use-share i').evaluate(e => e.style.width), '100%');

    // The limit cell: the caps, a warning chip near one and a Paused chip at one; a click edits it.
    assert.equal((await page.locator('.use-row[data-bot=support] .use-limit').innerText()).replace(/\s+/g, ' ').trim(), 'Paused $5/day · $50/mo');
    assert.equal((await page.locator('.use-row[data-bot=inbox] .use-limit').innerText()).replace(/\s+/g, ' ').trim(), '85% $20/mo');
    assert.equal((await page.locator('.use-row[data-bot=sales] .use-limit').innerText()).trim(), 'Set limit');
    await page.locator('.use-row[data-bot=sales] .use-limit').click();
    const cap = page.locator('dialog.use-dialog');
    assert.equal(await cap.locator('[name=daily_usd]').getAttribute('placeholder'), 'Default $20', 'the company default shows as the hint');
    await cap.locator('[name=daily_usd]').fill('7.5');
    await cap.locator('button[type=submit]').click();
    await cap.waitFor({state: 'detached'});
    assert.deepEqual(puts.at(-1), ['/api/v2/usage/limits/sales', {daily_usd: 7.5, monthly_usd: null}]);
    await page.locator('#use-default').click();
    await cap.locator('[name=monthly_usd]').fill('300');
    await cap.locator('[name=count_subscription]').check();
    await cap.locator('button[type=submit]').click();
    await cap.waitFor({state: 'detached'});
    assert.deepEqual(puts.at(-1), ['/api/v2/usage/limits', {daily_usd: 20, monthly_usd: 300, count_subscription: true}]);

    // The range switch asks for the days it says.
    for (const [label, expect] of [['Today', [day(0), day(0)]], ['30 days', [day(29), day(0)]], ['This month', [day(0).slice(0, 8) + '01', day(0)]]]) {
      await page.locator('.use-seg button', {hasText: label}).click();
      await page.waitForFunction(() => document.querySelector('#use-body:not([aria-busy])'));
      assert.deepEqual([asked.at(-1).from, asked.at(-1).to], expect, label);
      assert.equal(await page.locator('.use-seg button[aria-selected=true]').innerText(), label);
    }
    await page.locator('.use-seg button', {hasText: 'Custom'}).click();
    await page.locator('#use-from').waitFor();
    await page.locator('#use-from').fill('2026-09-01');
    await page.locator('#use-to').fill('2026-09-10');
    await page.waitForFunction(() => document.querySelector('#use-from').value === '2026-09-01');
    await page.waitForTimeout(150);
    assert.deepEqual([asked.at(-1).from, asked.at(-1).to], ['2026-09-01', '2026-09-10'], 'a custom range');

    // The department filter.
    await page.locator('.use-seg button', {hasText: '7 days'}).click();
    await page.locator('#use-dept').selectOption('Ops');
    await page.waitForFunction(() => document.querySelectorAll('.use-row').length === 1);
    assert.equal(asked.at(-1).department, 'Ops');
    assert.deepEqual(await page.locator('.use-name').allInnerTexts(), ['COO']);
    await page.locator('#use-dept').selectOption('');
    await page.waitForFunction(() => document.querySelectorAll('.use-row').length === 4);

    // A bot opens to a daily chart, its top routines and a CSV; clicking again closes it.
    await support.locator('.use-line').click();
    await support.locator('.use-col').first().waitFor();
    assert.equal(await support.locator('.use-col').count(), 7);
    assert.match(await support.locator('.use-routines tbody tr').first().innerText(), /Morning triage\s+7\s+\$4\.20/);
    assert.match(await support.locator('.use-routines tbody tr').nth(1).innerText(), /Other runs.*\$1\.90.*≈ \$1\.50 API-equivalent/s);
    assert.equal(asked.at(-1).bot, 'support');
    const [download] = await Promise.all([page.waitForEvent('download'), support.locator('[data-use-csv]').click()]);
    assert.match(download.suggestedFilename(), /^usage-support-\d{4}-\d\d-\d\d-\d{4}-\d\d-\d\d\.csv$/);
    const csv = fs.readFileSync(await download.path(), 'utf8').trim().split('\n');
    assert.equal(csv[0], 'day,runs,input_tokens,cached_tokens,output_tokens,est_cost_usd,subscription_equiv_usd');
    assert.equal(csv.length, 8);
    await support.locator('.use-line').click();
    assert.equal(await support.locator('.use-detail').isHidden(), true);

    // Nothing in the range.
    empty = true;
    await page.locator('.use-seg button', {hasText: 'Today'}).click();
    await page.locator('.empty').waitFor();
    assert.equal(await page.locator('.empty').innerText(), 'No usage yet.');
    assert.equal(await page.locator('.use-row').count(), 0);

    assert.deepEqual(errors, []);
    console.log('usage ui ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
