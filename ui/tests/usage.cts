// Offline browser regression: the Usage page (account menu > Usage). The range switch asks for the right days, the rows
// are sorted by spend whatever order they arrive in, a subscription's figure stays apart from spend, a bot opens to its
// cache rate, token split, top tasks, models, a daily chart on ranges of several days, its routines and a CSV, every limit is changed in one Limits table, and an empty range says so. A bot over its limit is marked in the org tree and its
// page leads with a banner whose link opens Usage.
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs = require('node:fs') as typeof import('node:fs');
// The page as the server sends it (the real bundle, TypeScript compiled), like every other browser script.
const {html, uiFile} = require('./support/page.cts');
const {t} = (() => { try { return require('./support/load.cts') as import('./support/load.cts').Api; } catch { return {t: (ms: number) => ms}; } })();   // load.cjs arrives with #254
const {unrouted} = require('./support/routes.cts');

declare function useLimits(): void;
declare function pageUsage(): void;
const day = (n: number) => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);
const figures = (runs: number, i: number, c: number, o: number, est: number | null, sub = 0, w = 0) => ({runs, input_tokens: i, cached_tokens: c, cache_write_tokens: w, output_tokens: o, est_cost_usd: est, subscription_equiv_usd: sub, unpriced_runs: 0});
const limit = (daily: number | null, monthly: number | null, percent = 0, blocked: string | null = null) => ({daily_usd: daily, monthly_usd: monthly, own_daily_usd: daily, own_monthly_usd: monthly, percent, blocked, may_edit: true, source: {}});
// Arriving in the wrong order on purpose.
const ROWS = [
  {bot: 'sales', name: 'Sales', department: 'Revenue', ...figures(3, 1000, 2000, 500, 0.4), low_estimate_runs: 2, share: 0.05, limit: limit(null, null)},
  {bot: 'support', name: 'Support', department: 'Revenue', ...figures(12, 900000, 2100000, 60000, 6.1, 1.5), share: 0.8, limit: limit(5, 50, 122, 'daily')},
  {bot: 'inbox', name: 'Inbox', department: null, ...figures(5, 400, 0, 50, null, 0, 100), share: 0, limit: limit(null, 20, 85)},
  {bot: 'assistant', name: 'COO', department: 'Ops', ...figures(4, 5000, 5000, 900, 0, 2.2), share: 0.15, limit: limit(null, null)},
];
// The opened bot's top tasks (the server sorts them) and its models, one with no list price.
const TASKS = [
  {value: 'Usage detail', name: "Usage detail explains a bot's spend", ...figures(3, 200000, 14000000, 300000, 2.0, 0, 100000), share: 0.26},
  {value: 'A private task', name: 'A private task', ...figures(2, 100000, 4000000, 100000, 1.5), share: 0.2},
  {value: null, name: 'Chats and other runs', ...figures(4, 0, 0, 0, 0, 1.2), share: 0.16},
  ...Array.from({length: 7}, (_, n) => ({value: `Job ${n}`, name: `Job ${n}`, ...figures(1, 1000, 3000, 100, 0.3), share: 0.04})),
];
const MODELS = [{value: 'claude-opus-5-5', name: 'claude-opus-5-5', ...figures(10, 2.0e6, 43.9e6, 1.0e6, 6.1, 1.5, 1.2e6), share: 1},
                {value: 'mystery-model', name: 'mystery-model', ...figures(2, 100000, 0, 1000, null), share: 0}];
// The team's limits: a default, a team cap with the old hour window, a capped group, one bot with limits of its own.
const LIMITS = {default: {daily_usd: 20, monthly_usd: null, count_subscription: false}, may_edit_default: true,
  bots: {support: {...limit(5, 50, 122, 'daily'), spent: {day: 6.1, month: 7.6}}, sales: {...limit(null, null), daily_usd: 20, spent: {day: 0.4}},
         inbox: limit(null, null), coo: limit(null, null)},
  pools: {company: {hourly_usd: 10, per_30m_usd: 4, spent: {hour: 4, '30m': 3.5}, percent: 87},
          groups: {ops: {name: 'Ops', parent: null, daily_usd: 30, spent: {day: 3}}, revenue: {name: 'Revenue', parent: null}}}};
const total = (rows: {runs: number; est_cost_usd: number | null; subscription_equiv_usd: number; low_estimate_runs?: number}[]) => rows.reduce<{runs: number; est_cost_usd: number; subscription_equiv_usd: number; input_tokens: number; cached_tokens: number; cache_write_tokens: number; output_tokens: number; unpriced_runs: number; low_estimate_runs: number}>((t, r) => ({runs: t.runs + r.runs, est_cost_usd: t.est_cost_usd + (r.est_cost_usd || 0), subscription_equiv_usd: t.subscription_equiv_usd + r.subscription_equiv_usd,
                                              input_tokens: 0, cached_tokens: 0, cache_write_tokens: 0, output_tokens: 0, unpriced_runs: 0, low_estimate_runs: t.low_estimate_runs + (r.low_estimate_runs || 0)}),
                                     {runs: 0, est_cost_usd: 0, subscription_equiv_usd: 0, input_tokens: 0, cached_tokens: 0, cache_write_tokens: 0, output_tokens: 0, unpriced_runs: 0, low_estimate_runs: 0});

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as import('playwright').LaunchOptions);
  try {
    const ctx = await browser.newContext({viewport: {width: 1440, height: 900}, serviceWorkers: 'block', acceptDownloads: true});
    const page = await ctx.newPage();
    const errors: string[] = [], asked: Record<string, string>[] = [];
    let empty = false;
    const puts: [string, unknown][] = [], purges: Record<string, unknown>[] = [];
    let mayPurge = true, purgeFails = false;
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!)))
        return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const config = {version: '0.2.18', update: null, app_name: 'Tico', usage_count_notice: false};
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/v2/usage/limits' && req.method() === 'GET') return json({...LIMITS, may_edit_default: mayPurge});
      if (p === '/api/v2/usage/queues/purge' && req.method() === 'POST') {
        purges.push(JSON.parse(req.postData()!));
        return purgeFails ? json({error: {code: 'unavailable', detail: 'Try again.'}}, 503)
          : json({scope: purges.at(-1)!.scope, cancelled: 3, kept_people: 2, still_running: 1});
      }
      if (/^\/api\/v2\/usage\/(limits|pools)/.test(p) && req.method() === 'PUT') { puts.push([p, JSON.parse(req.postData()!)]); return json({ok: true}); }
      if (p === '/api/v2/usage') {
        const q = Object.fromEntries(url.searchParams);
        asked.push(q);
        if (q.bot) {
          const days = [], from = new Date(q.from + 'T00:00:00Z'), to = new Date(q.to + 'T00:00:00Z');
          for (let d = from, n = 0; d <= to; d = new Date(+d + 864e5), n++)
            days.push({day: d.toISOString().slice(0, 10), ...(n === 3 ? figures(2, 100, 0, 10, null, 0, 5) : figures(n % 2 ? 2 : 0, 100, 0, 10, n % 3 ? 1 + n : 0, n % 2 ? 0.5 : 0, 5))});   // day 3: unpriced runs only
          if (q.bot === 'inbox')   // runs that reported no tokens
            return json({from: q.from, to: q.to, prices_as_of: '2026-09-29', bot: q.bot, name: 'Inbox', totals: figures(3, 0, 0, 0, null), daily: days, routines: [],
                         tasks: [{value: null, name: 'Chats and other runs', ...figures(3, 0, 0, 0, null), share: 0}], tasks_more: {count: 0, ...figures(0, 0, 0, 0, 0)},
                         models: [{value: null, name: 'Not recorded / not applicable', ...figures(3, 0, 0, 0, null), share: 0}]});
          return json({from: q.from, to: q.to, prices_as_of: '2026-09-29', bot: q.bot, name: 'Support', department: 'Revenue',
                       totals: figures(12, 2.1e6, 43.9e6, 1.0e6, 6.1, 1.5, 1.2e6), daily: days,
                       routines: [{routine: 's1', title: 'Morning triage', ...figures(7, 0, 0, 0, 4.2)}, {routine: null, title: 'Other runs', ...figures(5, 0, 0, 0, 1.9, 1.5)}],
                       tasks: TASKS, tasks_more: {count: 4, ...figures(4, 1000, 9000, 100, 0.8)}, models: MODELS});
        }
        const rows = empty ? [] : ROWS.filter(r => !q.department || r.department === q.department);
        return json({from: q.from, to: q.to, prices_as_of: '2026-09-29', group: 'bot', department: q.department || null, totals: total(rows), rows, departments: ['Ops', 'Revenue'],
                     dimensions: {harness: q.department === 'Ops' ? ['claude'] : ['claude', 'codex'], model: ['claude-opus-5-5', 'claude-sonnet-5'], effort: [], subscription: []}});
      }
      if (p === '/api/employees') return json([{name: 'support', display_name: 'Support', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/humans') return json({people: [], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: [{bot: 'support', state: 'paused', focus: 'Paused: over its daily limit',
                                                      limit: {period: 'daily', spent: 6.1, limit: 5, until: day(-1) + 'T00:00:00Z'}}]});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/bot/support');
    await page.waitForFunction(() => !document.querySelector('#account .account-email')?.textContent.includes('Signing in'));

    // Over its limit: a marker beside the bot in the org tree, and a banner on its page that opens the bot in Budget.
    const mark = page.locator('#tree a.node[href="#/bot/support"] .tree-limit');
    await mark.waitFor();
    assert.equal(await mark.getAttribute('aria-label'), 'Over its daily limit');
    const banner = page.locator('#bot-limit-host .bot-limit');
    await banner.waitFor();
    assert.equal((await banner.innerText()).replace(/\s+/g, ' ').trim(), '⚠ Over its daily limit ($6.10 of $5.00). Raise limit');
    assert.equal(await banner.locator('a', {hasText: 'Raise limit'}).getAttribute('href'), '#/strategy/budget/bot%3Asupport');
    asked.length = 0;
    await page.goto('https://tico-ui.test/#/updates');

    // The account menu lists Usage after Runs (Learnings may sit between them).
    await page.locator('#account').click();
    const items = await page.locator('#account-menu a:not([hidden])').evaluateAll(els => els.map(e => e.dataset.nav));
    assert.ok(items.includes('usage') && items.indexOf('usage') > items.indexOf('runs'), 'Usage follows Runs: ' + items);
    await page.locator('#account-menu a[data-nav=usage]').click();
    await page.locator('.use-row').first().waitFor();

    // Clearing is a confirmed write in the header's ⋯, scoped separately from the spend filter. Cancel sends nothing;
    // failures stay open.
    const purge = page.locator('#use-purge'), dialog = page.getByRole('dialog', {name: 'Clear automatic queue'});
    const more = page.locator('header.use-head > .ph-more');
    await purge.waitFor({state: 'attached'});
    // It cannot be undone: last in ⋯, in red, after a line; Copy link stays first.
    assert.deepEqual(await page.locator('header.use-head > .ph-menu > *').evaluateAll(els => els.map(e => e.className)), ['ph-copy', 'tl-menu-sep', 'use-purge']);
    assert.deepEqual(await page.locator('header.use-head > .ph-menu').evaluate(m => { const red = document.createElement('i'); red.style.color = 'var(--fail)'; m.append(red);
      const out = [getComputedStyle(m.querySelector('#use-purge')!).color, getComputedStyle(red).color]; red.remove(); return out[0] === out[1]; }), true, 'in red');
    await more.click();
    await purge.click();
    assert.equal(await dialog.locator('[name=scope]').inputValue(), 'company');
    await dialog.getByRole('button', {name: 'Cancel', exact: true}).click();
    assert.deepEqual(purges, []);
    await page.waitForFunction(() => document.activeElement?.matches('header.use-head > .ph-more'));   // the focus back on ⋯, not lost in its closed list
    await page.setViewportSize({width: 390, height: 844});
    await more.click();
    await purge.click();
    await dialog.locator('[name=scope]').selectOption('group:ops');
    purgeFails = true;
    await dialog.getByRole('button', {name: 'Clear queue', exact: true}).click();
    await dialog.locator('[data-error]:not([hidden])').waitFor();
    assert.equal(await dialog.getByRole('button', {name: 'Clear queue', exact: true}).isEnabled(), true);
    const failedRequests = purges.length; // The shared request helper retries a temporary server failure.
    assert.ok(failedRequests > 0);
    purgeFails = false;
    await dialog.getByRole('button', {name: 'Clear queue', exact: true}).click();
    await dialog.waitFor({state: 'detached'});
    assert.equal(purges.length, failedRequests + 1);
    assert.ok(purges.every((body: Record<string, unknown>) => Object.keys(body).length === 1 && body.scope === 'group:ops'));
    assert.deepEqual(puts, [], 'clearing the queue never changes spending limits');
    await page.setViewportSize({width: 1440, height: 900});

    // Group leads may edit their limits, but only owners and administrators see the purge control.
    mayPurge = false;
    await page.evaluate(() => useLimits());
    assert.equal(await purge.count(), 0, 'no Clear automatic queue in ⋯');
    assert.equal(await page.locator('header.use-head .ph-menu .ph-copy').count(), 1, '⋯ keeps its own Copy link');
    mayPurge = true;
    await page.evaluate(() => useLimits());
    await purge.waitFor({state: 'attached'});
    // The period is a segment and the filter the shared icon, both in the one header; no Filter word, no bar under it.
    assert.equal(await page.locator('header.use-head .ph-tools .use-seg[role=tablist]').count(), 1, 'the period sits in the header');
    assert.equal(await page.locator('header.use-head .ph-tools button.use-filter.tl-ib svg').count(), 1, 'the filter is the shared icon');
    assert.equal((await page.locator('button.use-filter').innerText()).trim(), '', 'the filter shows no word');
    assert.equal(await page.locator('.use-bar-row:not([hidden])').count(), 0, 'nothing between the header and the total');

    // Seven days by default, sorted by API-equivalent (billed plus subscriptions), one figure each, billed in the hover.
    assert.deepEqual([asked[0]!.from, asked[0]!.to], [day(6), day(0)]);
    assert.deepEqual(await page.locator('.use-name').allInnerTexts(), ['Support', 'COO', 'Sales', 'Inbox']);
    assert.match(await page.locator('.use-total').innerText(), /^\$10\.20 API-equivalent · 24 runs/);
    assert.equal(await page.locator('.use-note').count(), 1, 'API-equivalent is said once');
    assert.deepEqual(await page.locator('#use-total').evaluate(el => [getComputedStyle(el).fontSize, getComputedStyle(el).fontWeight]), ['16px', '600'], 'the total is 16px/600');
    assert.equal(await page.locator('.use-note').innerText(), 'API-equivalent · 24 runs', 'the rest of the line is small and muted');
    assert.equal(await page.locator('.use-note').evaluate(el => getComputedStyle(el).fontSize), '12px');
    assert.equal(await page.locator('#use-total').getAttribute('title'), '$6.50 billed on API keys or Tico Gateway; the rest ran on subscriptions');
    const support = page.locator('.use-row[data-bot=support]');
    assert.match(await support.locator('.use-runs').innerText(), /12 runs/);
    assert.match(await support.locator('.use-tok').innerText(), /3\.06M tokens/);
    assert.match(await support.locator('.use-cost').innerText(), /^\$7\.60$/);
    assert.equal((await page.locator('.use-row[data-bot=assistant] .use-cost').innerText()).trim(), '$2.20');
    assert.equal(await page.locator('.use-row[data-bot=assistant] .use-est').getAttribute('title'), 'All on subscriptions');
    // Runs on a model that charges more for long prompts, without each request's tokens, may read low.
    assert.match(await page.locator('.use-row[data-bot=sales] .use-cost').innerText(), /\$0\.40\s*estimate, may be low/);
    assert.match(await page.locator('.use-total').innerText(), /24 runs · may be low/);
    assert.match(await page.locator('.use-total .use-low').getAttribute('title') || '', /^2 runs on a model that charges more for long prompts/);
    assert.equal((await page.locator('.use-row[data-bot=inbox] .use-cost').innerText()).trim(), '—', 'a model with no price has no cost');
    assert.match(await page.locator('.use-row[data-bot=inbox] .use-tok').innerText(), /550 tokens/, 'cache writes count as tokens');
    assert.equal(await page.locator('.use-row[data-bot=support] .use-share i').evaluate(e => e.style.width), '100%');

    // The answer first: the first bot row starts within 200px of the title, and how the total is counted waits behind
    // the help button beside it.
    const titleTop = (await page.locator('.use-head h1').boundingBox())!.y, rowTop = (await page.locator('.use-row').first().boundingBox())!.y;
    assert.ok(rowTop - titleTop < 200, `the list starts ${rowTop - titleTop}px below the title`);
    // No info icon: the words "API-equivalent" open it.
    assert.equal(await page.locator('.use-total .nav-icon').count(), 0, 'no info icon beside the total');
    const why = page.locator('#use-why-text'), help = page.getByRole('button', {name: 'API-equivalent', exact: true});
    assert.equal(await why.isHidden(), true, 'the caveats wait');
    await help.click();
    assert.match(await why.innerText(), /^API list price, including runs on subscriptions\..*2 runs on a model that charges more/s);
    assert.equal(await help.getAttribute('aria-expanded'), 'true');
    await help.click();
    assert.equal(await why.isHidden(), true, 'a second click hides them');

    // No limit: nothing in the cell at rest; it is still the button that sets one, named for screen readers, its words
    // on the row's hover or its focus. A limit always shows.
    const setLimit = page.locator('.use-row[data-bot=sales] button.use-limit'), setWords = setLimit.locator('> span:first-child');
    const opacity = (l: import('playwright').Locator) => l.evaluate(e => getComputedStyle(e).opacity);
    await page.mouse.move(1, 1);
    assert.equal(await opacity(setWords), '0', 'Set limit is not shown at rest');
    assert.equal(await page.getByRole('button', {name: 'Set limit for Sales', exact: true}).count(), 1, 'named by its own words, then whose');
    assert.equal(await opacity(page.locator('.use-row[data-bot=support] .use-limit-t')), '1', 'a limit shows');
    await page.locator('.use-row[data-bot=sales] .use-line').hover();
    assert.equal(await opacity(setWords), '1', 'on hover');
    await page.mouse.move(1, 1);
    await setLimit.focus();
    assert.equal(await opacity(setWords), '1', 'on focus');
    await setLimit.blur();

    // Filter: the shared filter icon in the header, its menu the shared popover. The menu puts the cursor in its first
    // select and shows only selects with a choice (Group, Harness; not Model with one value); a filter set counts on the
    // icon's badge; Group by and Sort by do not. Escape closes it and gives the icon the focus; Reset clears the filters
    // and keeps the sort.
    const filter = page.locator('button.use-filter'), panel = page.locator('#task-filter-pop'), badge = filter.locator('.tl-fbadge');
    assert.equal(await filter.getAttribute('aria-label'), 'Filter');
    assert.equal(await badge.isHidden(), true);
    assert.equal(await panel.isHidden(), true);
    await filter.click();
    await panel.locator('.use-filter-menu').waitFor();
    assert.equal(await filter.getAttribute('aria-expanded'), 'true');
    assert.equal(await page.evaluate(() => document.activeElement?.id), 'use-dept', 'the first select has the focus');
    assert.deepEqual(await panel.locator('.use-frow > span').allInnerTexts(), ['Group', 'Harness', 'Model', 'Group by', 'Sort by']);
    await panel.locator('[data-use-filter=harness]').selectOption('codex');
    await page.waitForFunction(() => document.querySelector('.use-filter .tl-fbadge')?.textContent === '1');
    assert.equal(asked.at(-1)!.harness, 'codex');
    assert.equal(await panel.isVisible(), true, 'a choice keeps the menu open');
    await panel.locator('#use-dept').selectOption('Revenue');
    await panel.locator('#use-sort').selectOption('runs');
    await page.waitForFunction(() => document.querySelector('.use-filter .tl-fbadge')?.textContent === '2');
    assert.equal(await filter.getAttribute('aria-label'), 'Filter, 2 on', 'Group by and Sort by do not count');
    await page.keyboard.press('Escape');
    await panel.waitFor({state: 'hidden'});
    await page.waitForFunction(() => document.activeElement?.classList.contains('use-filter'));
    await filter.click();
    await panel.getByRole('button', {name: 'Reset'}).click();
    await page.waitForFunction(() => document.querySelectorAll('.use-row').length === 4);
    assert.equal(await badge.isHidden(), true, 'Reset clears the filters');
    assert.equal(await filter.getAttribute('aria-label'), 'Filter');
    assert.equal(await panel.locator('#use-sort').inputValue(), 'runs', 'Reset keeps Sort by');
    assert.deepEqual(await page.locator('.use-name').allInnerTexts(), ['Support', 'Inbox', 'COO', 'Sales'], 'the list stays sorted by runs after Reset');
    await panel.locator('#use-sort').selectOption('cost');
    await page.locator('.use-head h1').click();
    await panel.waitFor({state: 'hidden'});
    // The icon closes its own menu (the popover closes itself on the press; the click must not open it again).
    await filter.click();
    await panel.locator('.use-filter-menu').waitFor();
    await filter.click();
    await panel.waitFor({state: 'hidden'});
    assert.equal(await filter.getAttribute('aria-expanded'), 'false', 'the button closes it');
    // Its drop-downs keep their own arrow keys: the menu's ↑/↓ between items does not take them.
    await filter.click();
    await panel.locator('#use-sort').focus();
    assert.equal(await panel.locator('#use-sort').evaluate(sel => { const e = new KeyboardEvent('keydown', {key: 'ArrowDown', bubbles: true, cancelable: true}); sel.dispatchEvent(e); return e.defaultPrevented; }), false,
      'ArrowDown in a drop-down is left to it');
    await page.keyboard.press('Escape');
    await panel.waitFor({state: 'hidden'});
    // On a phone the header's tools fold into ⋯: the icon is there, and its menu is a sheet that stays in the window.
    // The focus leaves Filter first: a header keeps a tool that has the focus in its row, and would fold it only when the
    // press on ⋯ takes the focus, moving ⋯ away before the release (the click then misses and ⋯ never opens).
    // Escape gives Filter the focus back (as above): wait for that, or a late return would undo the blur.
    await page.waitForFunction(() => document.activeElement?.classList.contains('use-filter'));
    await filter.evaluate(el => (el as HTMLElement).blur());
    await page.setViewportSize({width: 400, height: 844});
    await page.locator('header.use-head.ph-fold > .ph-menu button.use-filter').waitFor({state: 'attached'});
    await more.click();
    await filter.click();
    await panel.locator('.use-filter-menu').waitFor();
    const pb = (await panel.boundingBox())!;
    assert.ok(pb.x >= 0 && pb.x + pb.width <= 400, `the menu fits: ${JSON.stringify(pb)}`);
    await page.keyboard.press('Escape');
    await panel.waitFor({state: 'hidden'});
    await page.waitForFunction(() => document.activeElement?.matches('header.use-head > .ph-more'), undefined, {timeout: t(5000)});   // folded: the focus goes to ⋯
    await page.setViewportSize({width: 1440, height: 900});

    // The limit cell (style guide, Usage: "Near the limit"): the caps as one line, the words "85%" or "Paused" beside
    // them, a meter under them (amber from 80%, red when paused), and no pill; a click opens it in Limits.
    assert.equal((await page.locator('.use-row[data-bot=support] .use-limit-t').innerText()).replace(/\s+/g, ' ').trim(), 'Paused · $5/day · $50/mo');
    assert.equal((await page.locator('.use-row[data-bot=inbox] .use-limit-t').innerText()).replace(/\s+/g, ' ').trim(), '85% · $20/mo');
    // Its name is what it shows, after whose it is; the meter repeats it, so a reader skips the meter.
    assert.equal(await page.getByRole('button', {name: 'Limit for Inbox: 85% · $20/mo', exact: true}).count(), 1);
    assert.equal(await page.locator('#use-body .use-limit .use-meter[aria-hidden=true]').count(), await page.locator('#use-body .use-limit .use-meter').count());
    assert.equal(await page.locator('#use-body .use-limit .pill').count(), 0, 'no pill in a limit cell');
    assert.equal(await page.locator('.use-row[data-bot=support] .use-limit .use-meter.bad b').evaluate(e => (e as HTMLElement).style.width), '100%');
    assert.equal(await page.locator('.use-row[data-bot=inbox] .use-limit .use-meter.warn b').evaluate(e => (e as HTMLElement).style.width), '85%');
    const under = await page.locator('.use-row[data-bot=inbox] .use-limit').evaluate(b => b.querySelector('.use-meter')!.getBoundingClientRect().top >= b.querySelector('.use-limit-t')!.getBoundingClientRect().bottom - 0.5);
    assert.ok(under, 'the meter sits under the limit');
    assert.equal((await page.locator('.use-row[data-bot=sales] .use-limit > span:first-child').innerText()).trim(), 'Set limit');
    await page.locator('.use-row[data-bot=sales] .use-limit').click();

    // Limits: every limit in one table. The default and the team always, then each group and bot with one of its own,
    // and the bot just asked for. The old hour window shows only because the team still has one.
    const lim = page.locator('#use-limits');
    await lim.locator('[data-lim-row="bot:sales"]').waitFor();
    assert.equal(await page.locator('#use-spend').isHidden(), true);
    assert.deepEqual(await lim.locator('[data-lim-row]').evaluateAll(rows => rows.map(r => r.dataset.limRow)),
                     ['default', 'company', 'group:ops', 'bot:sales', 'bot:support']);
    assert.deepEqual(await lim.locator('.use-lim-head [role=columnheader]').allInnerTexts(), ['', '30 min', 'Hour', 'Day', 'Week', 'Month']);
    const cell = (row: string, key: string) => lim.locator(`input[name="${row}|${key}"]`);
    assert.equal(await page.evaluate(() => (document.activeElement as HTMLInputElement).name), 'bot:sales|per_30m_usd', 'the cursor lands in the bot asked for');
    assert.equal(await cell('bot:sales', 'daily_usd').getAttribute('placeholder'), '$20', 'a bot shows the default it follows');
    assert.equal(await cell('company', 'hourly_usd').inputValue(), '10');
    assert.equal(await cell('bot:sales', 'hourly_usd').count(), 0, 'no new hour limits');
    assert.match(await lim.locator('[data-lim-row=company] .use-limit-w.warn').innerText(), /87%/);
    assert.match(await lim.locator('[data-lim-row="bot:support"] .use-limit-w.bad').innerText(), /Paused/);
    assert.equal(await lim.locator('.pill').count(), 0, 'no pills in the Limits table');
    assert.equal(await lim.locator('[data-lim-save]').isDisabled(), true, 'nothing to save yet');

    // Several at once: a bot, the default and its switch, a group added from the picker, a bot's limits removed.
    await cell('bot:sales', 'daily_usd').fill('7.5');
    await cell('default', 'monthly_usd').fill('300');
    await lim.locator('[name=count_subscription]').check();
    await lim.locator('.use-lim-add').selectOption('group:revenue');
    await cell('group:revenue', 'per_30m_usd').fill('2');
    await lim.locator('[data-lim-row="bot:support"] [data-lim-remove]').click();
    assert.equal(await cell('bot:sales', 'daily_usd').inputValue(), '7.5', 'typing survives a row added or removed');
    assert.equal(await lim.locator('.use-money.changed').count(), 3);
    await cell('company', 'weekly_usd').fill('abc');
    await lim.locator('[data-lim-save]').click();
    assert.match(await lim.locator('[data-lim-err]').innerText(), /Whole team, Week: an amount above \$0/);
    assert.equal(puts.length, 0, 'nothing is sent while a field is wrong');
    await cell('company', 'weekly_usd').fill('');
    await lim.locator('[data-lim-save]').click();
    for (const end = Date.now() + t(10000); puts.length < 4 && Date.now() < end;) await new Promise(r => setTimeout(r, 50));
    assert.deepEqual(puts, [
      ['/api/v2/usage/limits', {monthly_usd: 300, count_subscription: true}],
      ['/api/v2/usage/pools/groups/revenue', {per_30m_usd: 2}],
      ['/api/v2/usage/limits/sales', {daily_usd: 7.5}],
      ['/api/v2/usage/limits/support', {daily_usd: null, monthly_usd: null}],
    ], 'only what changed, one write per row');
    // Read back as saved, and the rows' limits with it.
    await page.waitForFunction(() => !document.querySelector('#use-limits .changed, #use-limits .removed') && !document.querySelector('#use-body[aria-busy]'));
    await page.locator('[data-use-view=spend]').click();

    // The range switch asks for the days it says.
    for (const [label, expect] of [['Today', [day(0), day(0)]], ['30 days', [day(29), day(0)]], ['This month', [day(0).slice(0, 8) + '01', day(0)]]] as const) {
      await page.locator('.use-seg button', {hasText: label}).click();
      await page.waitForFunction(() => document.querySelector('#use-body:not([aria-busy])'));
      assert.deepEqual([asked.at(-1)!.from, asked.at(-1)!.to], expect, label);
      assert.equal(await page.locator('.use-seg button[aria-selected=true]').innerText(), label);
    }
    await page.locator('.use-seg button', {hasText: 'Custom'}).click();
    await page.locator('#use-from').waitFor();
    // The date fields (format.ts dateField) take a typed day on Enter.
    await page.locator('#use-from').fill('2026-09-01');
    await page.locator('#use-from').press('Enter');
    await page.locator('#use-to').fill('2026-09-10');
    await page.locator('#use-to').press('Enter');
    await page.waitForFunction(() => (document.querySelector('#use-from') as HTMLInputElement).value === '2026-09-01');
    const custom = () => asked.at(-1)?.from === '2026-09-01' && asked.at(-1)?.to === '2026-09-10';
    for (const end = Date.now() + t(10000); !custom() && Date.now() < end;) await new Promise(r => setTimeout(r, 50));
    assert.deepEqual([asked.at(-1)!.from, asked.at(-1)!.to], ['2026-09-01', '2026-09-10'], 'a custom range');

    // The department filter.
    await page.locator('.use-seg button', {hasText: '7 days'}).click();
    await page.locator('button.use-filter').click();
    await page.locator('#use-dept').selectOption('Ops');
    await page.waitForFunction(() => document.querySelectorAll('.use-row').length === 1);
    assert.equal(asked.at(-1)!.department, 'Ops');
    // The open menu follows the new answer: Ops has one harness, so Harness has no choice left; the focus stays put.
    await page.waitForFunction(() => !document.querySelector('#task-filter-pop [data-use-filter=harness]'));
    assert.equal(await page.evaluate(() => document.activeElement?.id), 'use-dept', 'the focus stays on Group');
    assert.deepEqual(await page.locator('.use-name').allInnerTexts(), ['COO']);
    await page.locator('#use-dept').selectOption('');
    await page.waitForFunction(() => document.querySelectorAll('.use-row').length === 4);
    await page.keyboard.press('Escape');

    // A bot opens to what explains its spend: a summary with its cache rate, where the tokens go, its top tasks and its
    // models, a labelled daily chart, its routines and a CSV; clicking again closes it.
    await page.locator('button.use-filter').click();
    await page.locator('#task-filter-pop [data-use-filter=model]').selectOption('claude-opus-5-5');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.querySelector('#use-body:not([aria-busy]) .use-row'));
    await support.locator('.use-line').click();
    const detail = support.locator('.use-detail');
    await detail.locator('.use-sum').waitFor();
    assert.equal(asked.at(-1)!.bot, 'support');
    assert.equal(asked.at(-1)!.model, 'claude-opus-5-5', 'the detail counts only the runs the page filters to');
    const flat = (s: string) => s.replace(/\s+/g, ' ').trim();
    assert.equal(flat(await detail.locator('.use-sum').innerText()), '$7.60 API-equivalent · 12 runs · 48.2M tokens · 93% from cache');
    assert.equal(await detail.locator('.use-sum .use-est').getAttribute('title'), '$6.10 billed on API keys or Tico Gateway; the rest ran on subscriptions');
    assert.equal(await detail.locator('.use-sum .use-cache').getAttribute('title'),
                 'Share of input tokens read from cache. On most models a cache read costs about a tenth of new input.');
    // Tokens: one bar in four labelled parts, in a fixed order, each with its count and share.
    assert.equal(await detail.locator('.use-sec h3').first().innerText(), 'Tokens');
    assert.deepEqual((await detail.locator('.use-legend li').allInnerTexts()).map(flat),
                     ['Cache reads 43.9M · 91%', 'Cache writes 1.2M · 2%', 'New input 2.1M · 4%', 'Output 1M · 2%']);
    assert.deepEqual(await detail.locator('.use-mix i').evaluateAll(els => els.map(e => e.dataset.part)), ['read', 'write', 'new', 'output']);
    // Tasks: ten by cost as the server sends them, then the rest on one line.
    const tasks = detail.locator('.use-tasks tbody tr');
    assert.equal(await tasks.count(), 11);
    assert.deepEqual((await tasks.nth(0).locator('td').allInnerTexts()).map(flat).slice(0, 5), ["Usage detail explains a bot's spend", '3', '14.6M', '98%', '$2.00']);
    assert.equal(flat(await tasks.nth(1).locator('td').first().innerText()), 'A private task');
    assert.equal(flat(await tasks.nth(2).locator('td').first().innerText()), 'Chats and other runs');
    assert.equal(flat(await tasks.nth(10).innerText()), '4 more tasks · $0.80');
    assert.equal(await tasks.nth(0).locator('.use-share i').evaluate(e => e.style.width), '26%');
    // Models: every one, an unpriced one with a dash for cost.
    const models = detail.locator('.use-models tbody tr');
    assert.equal(await models.count(), 2);
    assert.equal(flat(await models.nth(1).locator('td').nth(4).innerText()), '—');
    assert.deepEqual(await detail.locator('.use-sec h3').allInnerTexts(), ['Tokens', 'Tasks', 'Models', 'Cost per day', 'Automations']);
    // Cost per day, on a range of several days: a column a day, the top value on an axis line, the first and last dates.
    assert.equal(await detail.locator('.use-col').count(), 7);
    assert.equal(await detail.locator('.use-top').innerText(), '$6.50');
    // Days in the guide's words (format.ts dayWords), whatever the browser's locale.
    const shortDay = (d: string) => page.evaluate(d => (window as unknown as {dayWords: (d: string, o: {weekday: boolean}) => string}).dayWords(d, {weekday: true}), d);
    assert.doesNotMatch(await shortDay(day(6)), /^\d/, 'month first');
    assert.deepEqual(await detail.locator('.use-axis span').allInnerTexts(), [await shortDay(day(6)), await shortDay(day(0))]);
    assert.equal(await detail.locator('.use-col').nth(1).getAttribute('title'), `${await shortDay(day(5))} · $2.50 · 2 runs · 0% from cache`);
    assert.equal(await detail.locator('.use-col').nth(3).getAttribute('title'), `${await shortDay(day(3))} · — · 2 runs · 0% from cache`, 'a day of unpriced runs has no cost, not $0.00');
    assert.doesNotMatch(await detail.innerText(), /a day at most/);
    assert.match(await support.locator('.use-routines tbody tr').first().innerText(), /Morning triage\s+7\s+\$4\.20/);
    assert.match(await support.locator('.use-routines tbody tr').nth(1).innerText(), /Other runs.*\$3\.40/s);
    const [download] = await Promise.all([page.waitForEvent('download'), support.locator('[data-use-csv]').click()]);
    assert.match(download.suggestedFilename(), /^usage-support-\d{4}-\d\d-\d\d-\d{4}-\d\d-\d\d\.csv$/);
    const csv = fs.readFileSync(await download.path(), 'utf8').trim().split('\n');
    assert.equal(csv[0], 'day,runs,input_tokens,cached_tokens,cache_write_tokens,output_tokens,est_cost_usd,subscription_equiv_usd,cache_rate');
    assert.match(csv[1]!, /^\d{4}-\d\d-\d\d,\d+,100,0,5,10,.*,0$/, 'each day carries its cache writes and its cache rate');
    assert.equal(csv.length, 8);

    // At phone width every section fits, and a task row keeps its name and cost.
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, 'no sideways scroll');
    const row = tasks.nth(0), box = await row.boundingBox();
    for (const cell of ['td:first-child', '.use-c-cost']) {
      const b = await row.locator(cell).boundingBox();
      assert.ok(b && b.width > 0 && b.x >= 0 && b.x + b.width <= 390, cell + ' on screen');
    }
    assert.ok(box && box.x + box.width <= 390);
    assert.equal(await row.locator('.use-c-runs').isHidden(), true);
    assert.equal(await row.locator('.use-c-tok').isHidden(), true);
    const bar = await detail.locator('.use-mix').boundingBox(), legend = await detail.locator('.use-legend').boundingBox();
    assert.ok(bar && legend && legend.y >= bar.y + bar.height, 'the legend sits under the bar');
    await page.setViewportSize({width: 1440, height: 900});

    // One day: no daily chart at all.
    await page.locator('.use-seg button', {hasText: 'Today'}).click();
    await page.waitForFunction(() => document.querySelector('#use-body:not([aria-busy]) .use-row'));
    await support.locator('.use-line').click();
    await detail.locator('.use-sum').waitFor();
    assert.equal(await detail.locator('.use-chart, .use-col').count(), 0);
    assert.doesNotMatch(await detail.innerText(), /a day at most|Cost per day/);
    await support.locator('.use-line').click();
    assert.equal(await support.locator('.use-detail').isHidden(), true);

    // A bot whose runs reported no tokens: no cache rate and no empty bar.
    const inbox = page.locator('.use-row[data-bot=inbox]');
    await inbox.locator('.use-line').click();
    await inbox.locator('.use-detail .use-sum').waitFor();
    assert.match(flat(await inbox.locator('.use-sum').innerText()), / · 0 tokens · cache rate —$/);
    assert.equal(flat(await inbox.locator('.use-sec').first().innerText()), 'Tokens No token counts reported for 3 runs');
    assert.equal(await inbox.locator('.use-mix').count(), 0);
    await inbox.locator('.use-line').click();
    await page.locator('button.use-filter').click();
    await page.locator('#task-filter-pop [data-use-filter=model]').selectOption('');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.querySelector('#use-body:not([aria-busy]) .use-row'));

    // One day has no daily chart: a single bar would only fill the width. The automations and the CSV stay.
    await page.locator('.use-seg button', {hasText: 'Today'}).click();
    await page.waitForFunction(() => document.querySelector('#use-body:not([aria-busy])'));
    await support.locator('.use-line').click();
    await support.locator('.use-routines').waitFor();
    assert.equal(await support.locator('.use-chart, .use-axis').count(), 0, 'no chart for a single day');
    assert.match(await support.locator('.use-routines tbody tr').first().innerText(), /Morning triage/);
    assert.equal(await support.locator('[data-use-csv]').count(), 1);
    await support.locator('.use-line').click();
    await page.locator('.use-seg button', {hasText: '7 days'}).click();
    await page.waitForFunction(() => document.querySelector('#use-body:not([aria-busy])'));

    // Drawing Usage again adds no click listener to the document; listeners do not pile up per visit.
    const listening = await page.evaluate(() => {
      let n = 0;
      const add = document.addEventListener.bind(document), remove = document.removeEventListener.bind(document);
      const counts = (type: string, o?: boolean | AddEventListenerOptions) => type === 'click' && (o === true || (typeof o === 'object' && !!o.capture));
      document.addEventListener = ((type: string, f: EventListenerOrEventListenerObject, o?: boolean | AddEventListenerOptions) => { if (counts(type, o)) n++; add(type, f, o); }) as typeof document.addEventListener;
      document.removeEventListener = ((type: string, f: EventListenerOrEventListenerObject, o?: boolean | EventListenerOptions) => { if (counts(type, o)) n--; remove(type, f, o); }) as typeof document.removeEventListener;
      pageUsage(); pageUsage(); pageUsage();
      document.addEventListener = add; document.removeEventListener = remove;
      return n;
    });
    assert.equal(listening, 0, 'three redraws add no document click listener');
    await page.locator('.use-row').first().waitFor();

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
