// The Growth page's cards (ui/app/growth-page.js on ui/app/dashboard.js's grid, backend/cards.py place `growth`) against
// a fake server shaped as GET /api/v2/places/growth[?month=], /api/v2/cards/choices?place=growth, POST /api/v2/cards and
// /api/v2/growth/*: the head has the North Star line, the month picker, the Growth board link and Settings; the five
// cards draw (a Number's value, change and trend; the Funnel's five stages, its North Star and the loop; Channels with
// its tasks and "+N more"; Retention's shaded cells with dark text on light shades; the Growth board's lanes); a month
// picked reads the cards again for it and the address keeps it; a Number added from its form sends its metric and
// channel; Settings lists the channels with their months; a member reads and arranges nothing; a phone has one
// column and no sideways scroll, in both themes. TICO_UI_SHOTS=<dir> saves screenshots.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const uiDir = path.join(__dirname, '..');
const roster = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'redesign-roster.json'), 'utf8'));
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};
const OWNER = {id: 'ana', name: 'Ana Silva', email: 'ana@acme.example', role: 'owner', company_role: 'owner', cloud: true, bot_admin: true};
const MEMBER = {id: 'cara', name: 'Cara Diaz', email: 'cara@acme.example', role: 'human', company_role: 'member', cloud: true};
const MONTHS = ['2026-07', '2026-08', '2026-09', '2026-10'];
const METRICS = [['new_customers', 'New customers', 'count', 'up'], ['cac', 'CAC', 'usd', 'down'], ['kept_d30', 'Kept 30 days', 'pct', 'up'],
  ['spend', 'Spend', 'usd', null]];
const SERIES = {new_customers: [30, 36, 41, 48], cac: [52, 48, 45, 41], kept_d30: [62, 66, 71, null], spend: [1560, 1730, 1845, 1970]};
const CHANNELS = [{slug: 'search', name: 'Search'}, {slug: 'referrals', name: 'Referrals'}];
const upTo = month => MONTHS.slice(0, MONTHS.indexOf(month) + 1);
const DATA = {
  growth_number: (s, month) => {
    const [metric, label, format, good] = METRICS.find(m => m[0] === s.metric), cal = upTo(month);
    const values = cal.map((_, i) => s.channel ? Math.round(SERIES[metric][i] / 2) : SERIES[metric][i]);
    let at = values.length - 1;
    while (at > 0 && values[at] == null && metric.startsWith('kept')) at--;
    return {metric, label, format, good, month, value: values[at], prev: values[at - 1] ?? null, from: at === values.length - 1 ? '' : cal[at],
      series: cal.map((m, i) => ({month: m, value: values[i]}))};
  },
  growth_funnel: (_, month) => {
    const k = MONTHS.indexOf(month), v = [12000, 480, 210, 160, 22].map(x => Math.round(x * (0.85 + 0.05 * k)));
    const stages = ['Reach', 'Acquire', 'Activate', 'Retain', 'Refer'].map((label, i) => ({stage: label.toLowerCase(), label,
      kpi: i === 3 ? null : 'k' + i, name: i === 3 ? '' : ['Site visits', 'Signups', 'Activated', '', 'Referrals'][i], unit: '', value: i === 3 ? null : v[i],
      prev: i === 3 ? null : Math.round(v[i] * 0.9), conv: i === 0 || i === 3 || i === 4 ? null : Math.round(v[i] / v[i - 1] * 1000) / 10,
      north_star: i === 2, series: i === 3 ? [] : [v[i] * 0.8, v[i] * 0.9, v[i]]}));
    return {stages, north_star: {id: 'k2', name: 'Activated', unit: '', value: stages[2].value, prev: stages[2].prev, target: 300}};
  },
  growth_channels: (s, month) => ({month, rows: [
    {slug: 'search', name: 'Search', new_customers: 31, cac: 38, ltv_cac: 3.2, ltv_from: '', series: [18, 22, 25, 31],
      tasks: [{id: 't1', title: 'Search: long-tail landing pages', stage: 'scaling'}, {id: 't2', title: 'Search: competitor keywords', stage: 'testing'},
        {id: 't3', title: 'Search: brand terms', stage: 'idea'}], more: 2},
    {slug: 'referrals', name: 'Referrals', new_customers: 17, cac: null, ltv_cac: 4.1, ltv_from: '2026-09', series: [12, 14, 16, 17], tasks: [], more: 0},
  ].slice(0, s.count || 10)}),
  growth_retention: (s, month) => ({columns: ['30 days', '90 days', '180 days'], rows: upTo(month).slice(-(s.months || 6)).map((m, i) => ({month: m,
    new_customers: SERIES.new_customers[i], kept: [[62, 48, 35], [66, 51, null], [71, null, null], [null, null, null]][i]}))}),
  growth_board: s => ({lanes: [
    {stage: 'testing', label: 'Testing', count: 4, cards: [{id: 't2', title: 'Search: competitor keywords', channel: 'Search', owner_id: 'human:ana'},
      {id: 't4', title: 'Referral reward email', channel: 'Referrals', owner_id: ''}].slice(0, s.count || 3)},
    {stage: 'scaling', label: 'Scaling', count: 1, cards: [{id: 't1', title: 'Search: long-tail landing pages', channel: 'Search', owner_id: 'human:ana'}]},
    {stage: 'idea', label: 'Idea', count: 0, cards: []}]}),
};
const NAMES = {growth_number: 'Number', growth_funnel: 'Funnel', growth_channels: 'Channels', growth_retention: 'Retention', growth_board: 'Growth board', kpi: 'KPI'};
const FIELDS = {growth_number: ['metric', 'channel'], growth_funnel: [], growth_channels: ['count'], growth_retention: ['months'], growth_board: ['count'], kpi: ['kpi']};
const CHOICES = {templates: Object.entries(NAMES).map(([id, name]) => ({id, name, fields: FIELDS[id]})), teams: [], kpis: [{id: 'k2', name: 'Activated'}], pages: [],
  metrics: METRICS.map(([id, label]) => ({id, label})), channels: CHANNELS};
const DEFAULTS = [['growth_funnel', 'large', {}], ['growth_number', 'small', {metric: 'new_customers'}], ['growth_number', 'small', {metric: 'cac'}],
  ['growth_number', 'small', {metric: 'kept_d30'}], ['growth_channels', 'large', {count: 10}], ['growth_retention', 'medium', {months: 6}],
  ['growth_board', 'medium', {count: 3}]];

function server(me) {
  let n = 0, saved = false;
  let pins = DEFAULTS.map(([template, size, settings], i) => ({id: null, item: null, position: i, size, made_by: null, card: {id: null, template, settings}}));
  const reads = [], writes = [];
  const draw = (c, month) => ({...c, title: c.title || '', name: NAMES[c.template], heading: c.title || NAMES[c.template], team_name: '', hidden: false,
    data: DATA[c.template](c.settings, month)});
  const view = month => ({place: 'growth', kind: 'growth', saved, can_edit: me.company_role !== 'member', month, months: MONTHS,
    pins: pins.map(p => ({...p, card: draw(p.card, month)}))});
  const save = () => {
    if (saved) return;
    saved = true;
    pins = pins.map(p => { const cid = 'c' + (++n); return {...p, id: 'p' + n, item: 'card:' + cid, card: {...p.card, id: cid}}; });
  };
  return {reads, writes, handle(method, p, body, query) {
    if (method !== 'GET' && /^\/api\/v2\/(places|pins|cards|growth)\b/.test(p)) writes.push([method, p, body]);
    if (p === '/api/v2/places/growth') {
      const month = query.get('month') || MONTHS.at(-1);
      reads.push(month);
      if (!MONTHS.includes(month) && month !== '2026-11') return [422, {error: {code: 'invalid', detail: 'A month as YYYY-MM'}}];
      return [200, view(month)];
    }
    if (p === '/api/v2/cards/choices') return query.get('place') === 'growth' ? [200, CHOICES] : [200, {...CHOICES, templates: []}];
    if (p === '/api/v2/cards' && method === 'POST') {
      if (me.company_role === 'member') return [403, {error: {code: 'forbidden', detail: 'The owner, admins and bots arrange the Growth page'}}];
      save();
      const cid = 'c' + (++n);
      pins.push({id: 'p' + (++n), item: 'card:' + cid, position: pins.length, size: body.size, made_by: 'human:ana',
        card: {id: cid, template: body.template, settings: body.settings, title: body.title}});
      return [200, {card: draw(pins.at(-1).card, MONTHS.at(-1)), pin: 'p' + n}];
    }
    if (p === '/api/v2/growth/settings') return [200, {unit: 'customers', revenue_month: 49, margin_pct: 80}];
    if (p === '/api/v2/growth/channels') return [200, {channels: CHANNELS.map(c => ({...c, stage: 'testing', owner_ref: 'human:ana', track: 'performance'})), may_edit: true}];
    if (p === '/api/v2/growth/cohorts') return [200, {cohorts: MONTHS.map((month, i) => ({channel: query.get('channel'), month, new_customers: SERIES.new_customers[i],
      kept_d30: SERIES.kept_d30[i], kept_d90: null, kept_d180: null, quality: 'actual'}))}];
    return null;
  }};
}

async function open(browser, {viewport = DESKTOP, hash = '#/growth', me = OWNER, theme = 'dark'} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE});
  const errors = [], fake = server(me);
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(t => { try { localStorage.setItem('tico.theme', t); } catch { /* none */ } }, theme);
  const config = {company_name: 'Acme'};
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.html') ? 'text/html' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events' || p.endsWith('/watch')) return;
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/employees') return json(roster.employees);
    if (p === '/api/humans') return json(roster.humans);
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/groups') return json([]);
    const answer = fake.handle(method, p, req.postData() ? JSON.parse(req.postData()) : {}, url.searchParams);
    if (answer) return json(answer[1], answer[0]);
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  return {page, errors, fake};
}
const sideways = page => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
const headings = page => page.locator('#dash-grid .dash-pin-head h2').allInnerTexts();
// WCAG contrast of an element's text on its own background.
const contrast = loc => loc.evaluate(el => {
  const rgb = c => { const ctx = document.createElement('canvas').getContext('2d'); ctx.fillStyle = c; ctx.fillRect(0, 0, 1, 1); return [...ctx.getImageData(0, 0, 1, 1).data].slice(0, 3); };
  const lum = c => { const [r, g, b] = rgb(c).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const s = getComputedStyle(el), a = lum(s.color), b = lum(s.backgroundColor);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
});
async function shot(page, name) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  await page.screenshot({animations: 'disabled', path: path.join(dir, name + '.png'), fullPage: true});
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    {
      const {page, errors, fake} = await open(browser);
      await page.locator('#dash-grid .grc-funnel').waitFor();
      assert.deepEqual(await headings(page), ['Funnel', 'New customers', 'CAC', 'Kept 30 days', 'Channels', 'Retention', 'Growth board'], 'the default cards');
      assert.equal(await page.locator('#side a[data-nav="growth"].cur').count(), 1, 'Growth is current');
      // The head: Growth, the North Star line, the month, the board, Settings; arranging comes from the grid.
      assert.equal(await page.locator('.dash-head h1').innerText(), 'Growth');
      const ns = page.locator('.dash-head [data-gr-ns]');
      assert.match(await ns.innerText(), /North Star\s+Activated\s+210/);
      assert.match(await ns.innerText(), /Target 300/);
      assert.equal(await ns.getAttribute('href'), '#/strategy/goals?kpi=k2');
      assert.equal(await page.locator('[data-gr-month]').inputValue(), '2026-10');
      assert.deepEqual(await page.locator('[data-gr-month] option').allInnerTexts(), ['Oct 2026', 'Sep 2026', 'Aug 2026', 'Jul 2026'], 'newest first');
      assert.equal(await page.locator('[data-gr-board]').getAttribute('href'), '#/tasks?view=board&type=idea&who=all');
      assert.equal(await page.locator('[data-gr-settings]').count(), 1);
      assert.equal(await page.locator('.dash-actions').isVisible(), true);
      assert.equal(await page.locator('[data-dash-add="page"]').count(), 1, 'an owner pins pages too');
      // Arranging is in edit mode: Edit shows the grips, menus and + Card / + Page; Done hides them.
      const editControls = '[data-dash-grip], [data-dash-menu], [data-dash-add], [data-dash-mode="done"]';
      const shown = sel => page.locator(sel).evaluateAll(els => els.filter(el => el.checkVisibility()).length);
      assert.equal(await shown(editControls), 0, 'viewing shows no edit controls');
      await page.locator('[data-dash-mode="edit"]').click();
      assert.ok(await shown('[data-dash-grip]') > 0, 'Edit shows the grips');
      assert.equal(await shown('[data-dash-add]'), 2, '+ Card and + Page');
      assert.equal(await shown('[data-dash-mode="edit"]'), 0);
      await page.locator('[data-dash-mode="done"]').click();
      assert.equal(await shown(editControls), 0, 'Done hides them');

      // A Number: the value, its change coloured by what is good, its trend; a carried value says its month.
      const nums = page.locator('#dash-grid .grc-num-v');
      assert.deepEqual(await nums.allInnerTexts(), ['48', '$41', '71%']);
      const deltas = page.locator('#dash-grid .grc-num-d .gr-d');
      assert.match(await deltas.nth(0).getAttribute('class'), /gr-d-good/, 'more customers is good');
      assert.match(await deltas.nth(1).getAttribute('class'), /gr-d-good/, 'a lower CAC is good');
      assert.match(await deltas.nth(2).innerText(), /5 pts/, 'a share moves in points');
      assert.match(await page.locator('#dash-grid .grc-num-d').nth(2).innerText(), /Sep 26/, 'carried back from September');
      assert.equal(await page.locator('#dash-grid .grc-num .gr-spark').count(), 3);

      // The Funnel: five stages, conversion, the North Star, the loop; a stage without a KPI says so.
      const stages = page.locator('#dash-grid .grc-funnel > li');
      assert.equal(await stages.count(), 5);
      assert.deepEqual(await stages.evaluateAll(els => els.map(el => el.dataset.stage)), ['reach', 'acquire', 'activate', 'retain', 'refer']);
      assert.match(await page.locator('.grc-st-ns').innerText(), /★ North Star/);
      assert.equal(await page.locator('.grc-st-ns').getAttribute('data-stage'), 'activate');
      assert.match(await stages.nth(4).innerText(), /↺ loops back to Reach/);
      assert.match(await stages.nth(3).innerText(), /No KPI/);
      assert.equal(await stages.nth(3).locator('a').count(), 0, 'nothing to open');
      assert.equal(await stages.nth(1).locator('a').getAttribute('href'), '#/strategy/goals?kpi=k1');
      assert.match(await stages.nth(1).locator('.grc-st-c').innerText(), /^4\.0%$|^4%$/);

      // Channels: names as stored, the numbers, the trend, the tasks being worked on with their stage, then +N more.
      const rows = page.locator('#dash-grid .grc-ch tbody tr');
      assert.deepEqual(await rows.locator('th').allInnerTexts(), ['Search', 'Referrals']);
      assert.deepEqual(await rows.nth(0).locator('td.grc-n').allInnerTexts(), ['31', '$38', '3.2×']);
      assert.equal(await rows.nth(1).locator('td.grc-n').nth(1).innerText(), '—', 'no CAC that month');
      assert.match(await rows.nth(1).locator('td.grc-n').nth(2).getAttribute('class'), /grc-old/, 'LTV:CAC carried back');
      const chips = rows.nth(0).locator('a.grc-chip');
      assert.deepEqual(await chips.evaluateAll(els => els.map(el => el.getAttribute('href'))), ['#/task/t1', '#/task/t2', '#/task/t3']);
      assert.deepEqual(await chips.locator('.grc-sq').evaluateAll(els => els.map(el => el.dataset.stage)), ['scaling', 'testing', 'idea']);
      assert.equal(await rows.nth(0).locator('a.grc-more').innerText(), '+2 more');
      assert.equal(await rows.nth(0).locator('a.grc-more').getAttribute('href'), '#/tasks?view=board&type=idea&who=all&tag=channel%3Asearch');
      assert.equal(await rows.locator('img, svg.nav-icon, .grc-kind').count(), 0, 'no icons or kinds');

      // Retention: a cell per share, empty where none yet, the text readable on every shade.
      const cells = page.locator('#dash-grid .grc-ret-t td.grc-k:not(.grc-k-none)');
      assert.deepEqual(await cells.allInnerTexts(), ['62%', '48%', '35%', '66%', '51%', '71%']);
      for (let i = 0; i < 6; i++) assert.ok(await contrast(cells.nth(i)) >= 4.5, `retention cell ${i} reads at 4.5:1 (dark)`);

      // The Growth board: lanes with tasks, their channel and owner; an empty lane left out.
      const lanes = page.locator('#dash-grid .grc-lane');
      assert.deepEqual(await lanes.evaluateAll(els => els.map(el => el.dataset.lane)), ['testing', 'scaling']);
      assert.deepEqual(await lanes.nth(0).locator('a.grc-card').evaluateAll(els => els.map(el => el.getAttribute('href'))), ['#/task/t2', '#/task/t4']);
      assert.match(await lanes.nth(0).locator('a.grc-card').first().innerText(), /Search/);
      assert.equal(await lanes.nth(0).locator('.grc-face').first().getAttribute('title'), 'Ana Silva');
      assert.equal(await lanes.nth(0).locator('a.grc-more').innerText(), '+2 more');
      await shot(page, 'desktop-growth');

      // A month picked reads every card again for it, and the address keeps it.
      await page.locator('[data-gr-month]').selectOption('2026-08');
      await page.waitForFunction(() => document.querySelector('#dash-grid .grc-num-v')?.textContent === '36');
      assert.equal(fake.reads.at(-1), '2026-08');
      assert.equal(await page.evaluate(() => location.hash), '#/growth?m=2026-08');
      assert.equal(await page.locator('#dash-grid .grc-ret-t tbody tr').count(), 2);
      // Opened at that address, the page reads that month.
      await page.evaluate(() => { location.hash = '#/overview'; });
      await page.locator('#dash-grid .dash-pin').first().waitFor();
      await page.evaluate(() => { location.hash = '#/growth?m=2026-09'; });
      await page.waitForFunction(() => document.querySelector('[data-gr-month]')?.value === '2026-09');
      assert.equal(fake.reads.at(-1), '2026-09');
      await page.waitForFunction(() => document.querySelector('#dash-grid .grc-num-v')?.textContent === '41');

      // Add a Number from its form: a metric for one channel, a tile by default; the defaults are saved first.
      await page.locator('[data-dash-mode="edit"]').click();
      await page.locator('[data-dash-add="card"]').click();
      const dlg = page.locator('#dash-dialog[open]');
      assert.equal(await dlg.locator('[name="template"]').inputValue(), 'growth_number', 'a Number first');
      assert.equal(await dlg.locator('[name="size"]').inputValue(), 'small');
      assert.deepEqual(await dlg.locator('[name="channel"] option').allInnerTexts(), ['All channels', 'Search', 'Referrals']);
      await dlg.locator('[name="metric"]').selectOption('spend');
      assert.equal(await dlg.locator('[name="title"]').getAttribute('placeholder'), 'Spend');
      await dlg.locator('[name="channel"]').selectOption('search');
      await dlg.locator('[type="submit"]').click();
      await page.waitForFunction(() => document.querySelectorAll('#dash-grid .dash-pin').length === 8);
      const sent = fake.writes.find(([m, p]) => m === 'POST' && p === '/api/v2/cards')[2];
      assert.deepEqual(sent, {title: '', settings: {metric: 'spend', channel: 'search'}, template: 'growth_number', place: 'growth', size: 'small'});
      assert.equal((await headings(page)).at(-1), 'Spend · Search', 'its heading names the channel');
      // The other templates' fields: Channels counts rows, Retention months.
      await page.locator('[data-dash-add="card"]').click();
      await dlg.locator('[name="template"]').selectOption('growth_channels');
      assert.equal(await dlg.locator('[name="count"]').inputValue(), '10');
      assert.equal(await dlg.locator('[name="count"]').getAttribute('max'), '20');
      assert.equal(await dlg.locator('[name="size"]').inputValue(), 'large');
      await dlg.locator('[name="template"]').selectOption('growth_retention');
      assert.equal(await dlg.locator('[name="months"]').inputValue(), '6');
      await dlg.locator('[name="template"]').selectOption('growth_board');
      assert.equal(await dlg.locator('label:has([name="count"])').innerText(), 'Tasks per lane');
      await dlg.locator('[data-close]').first().click();

      // Settings: the numbers, each channel with its months, + Channel, and where the numbers come from.
      await page.locator('[data-gr-settings]').click();
      const set = page.locator('dialog.gr-dlg[open]');
      await set.locator('[data-gs-channel="search"]').waitFor();
      assert.deepEqual(await set.locator('[data-gs-channel] .gr-row-t').allInnerTexts(), ['Search', 'Referrals', 'Unknown']);
      assert.equal(await set.locator('[name="unit"]').inputValue(), 'customers');
      assert.equal(await set.locator('a[data-gr-sources]').getAttribute('href'), '#/growth/sources');
      assert.equal(await set.locator('[data-ch-add]').count(), 1);
      await set.locator('[data-gs-months="search"]').click();
      const months = page.locator('dialog.gr-dlg[open]', {hasText: 'Search · Months'});
      await months.locator('.gc-coh').first().waitFor();
      assert.equal(await months.locator('.gc-coh').count(), 4);
      assert.match(await months.locator('.gc-coh').first().innerText(), /Oct 2026/, 'newest first');
      assert.equal(await months.locator('[data-gc-month-add]').count(), 1);
      await shot(page, 'desktop-growth-months');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A member reads the cards, opens the board and the sources, and arranges nothing.
      const {page, errors, fake} = await open(browser, {me: MEMBER});
      await page.locator('#dash-grid .grc-funnel').waitFor();
      assert.equal(await page.locator('.dash-actions').isVisible(), false);
      assert.equal(await page.locator('[data-dash-grip], [data-dash-menu], [data-gr-settings]').count(), 0);
      assert.equal(await page.locator('[data-dash-mode="edit"]').isVisible(), false, 'no Edit');
      assert.equal(await page.locator('.dash-head a[data-gr-sources]').getAttribute('href'), '#/growth/sources');
      assert.equal(await page.locator('[data-gr-board]').count(), 1);
      assert.deepEqual(fake.writes, []);
      assert.deepEqual(errors, []);
      await page.close();
    }
    for (const theme of ['dark', 'light']) {
      // A phone: one column, nothing past the screen's edge, the head in reach; retention reads in each theme.
      const {page, errors} = await open(browser, {viewport: PHONE, theme});
      await page.locator('#dash-grid .grc-funnel').waitFor();
      const widths = await page.locator('#dash-grid .dash-pin').evaluateAll(els => els.map(el => Math.round(el.getBoundingClientRect().width)));
      assert.equal(new Set(widths).size, 1, 'one column');
      assert.ok(widths[0] >= 340, 'the full width');
      assert.equal(await sideways(page), false, 'no sideways scroll');
      const past = await page.evaluate(() => [...document.querySelectorAll('#main *')].filter(el => {
        const r = el.getBoundingClientRect();
        return r.width && r.right > innerWidth + 1 && !el.closest('.grc-ret');
      }).map(el => el.className || el.tagName).slice(0, 5));
      assert.deepEqual(past, [], 'nothing past the edge');
      assert.equal(await page.locator('#dash-grid .grc-ch thead').evaluate(el => el.getBoundingClientRect().height <= 1), true, 'channels stack as rows');
      const cells = page.locator('#dash-grid .grc-ret-t td.grc-k:not(.grc-k-none)');
      for (let i = 0; i < await cells.count(); i++) assert.ok(await contrast(cells.nth(i)) >= 4.5, `retention cell ${i} reads at 4.5:1 (${theme})`);
      for (const b of await page.locator('.dash-head :is(a,button,select)').all()) {
        if (!(await b.isVisible())) continue;
        const box = await b.boundingBox();
        assert.ok(box.x >= 0 && box.x + box.width <= PHONE.width + 1, 'the head fits');
      }
      await shot(page, `phone-growth-${theme}`);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A bad month in the address falls back to the newest; old routes still arrive here.
      const {page, errors, fake} = await open(browser, {hash: '#/growth?m=2026-13'});
      await page.locator('#dash-grid .grc-funnel').waitFor();
      assert.equal(fake.reads.at(-1), '2026-10');
      await page.evaluate(() => { location.hash = '#/growth/channels'; });
      await page.waitForFunction(() => location.hash === '#/growth');
      await page.locator('#dash-grid .grc-funnel').waitFor();
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('Growth cards: head (North Star, month, board, Settings), Number/Funnel/Channels/Retention/Board renderers, month picker and address, add a Number with metric and channel, form fields, Settings and months, member, phone in both themes, contrast, old routes');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
