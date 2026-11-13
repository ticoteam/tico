// The Growth page's cards (ui/app/growth-page.ts on ui/app/dashboard.ts's grid, backend/cards.py place `growth`) against
// a fake server shaped as GET /api/v2/places/growth[?month=], /api/v2/cards/choices?place=growth, POST /api/v2/cards and
// /api/v2/growth/*: the head has the North Star line, the month picker, the Growth board link and Settings; the five
// cards draw (a Number's value, change and trend; the Funnel's five stages, its North Star and the loop; Channels with
// its tasks and "+N more"; Retention's shaded cells with dark text on light shades; the Growth board's lanes); a month
// picked reads the cards again for it and the address keeps it; a Number added from its form sends its metric and
// channel; Settings lists the channels with their months; a member reads and arranges nothing; a phone has one
// column and no sideways scroll, in both themes. TICO_UI_SHOTS=<dir> saves screenshots.
import type {Browser, Locator, Page, Route} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RouteSupport from './support/routes.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RouteSupport.Api;
// Shapes the tests read from the page and build for the fake server.
declare const S: {me: Person | null; emps?: unknown[]};
interface Person {id: string; name: string; email: string; role: string; company_role: string; cloud: boolean; bot_admin?: boolean}
interface CardSettings {metric?: string; channel?: string; count?: number; months?: number}
interface PinCard {id: string | null; template: string; settings: CardSettings; title?: string}
interface Pin {id: string | null; item: string | null; position: number; size: string; made_by: string | null; card: PinCard}
interface PostedCard {size: string; template: string; settings: CardSettings; title: string}
type Write = [string, string, unknown];
const uiDir = path.join(__dirname, '..');
const roster = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'redesign-roster.json'), 'utf8'));
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};
const OWNER: Person = {id: 'ana', name: 'Ana Silva', email: 'ana@acme.example', role: 'owner', company_role: 'owner', cloud: true, bot_admin: true};
const MEMBER: Person = {id: 'cara', name: 'Cara Diaz', email: 'cara@acme.example', role: 'human', company_role: 'member', cloud: true};
const MONTHS = ['2026-07', '2026-08', '2026-09', '2026-10'];
const METRICS: [string, string, string, string | null][] = [['new_customers', 'New customers', 'count', 'up'], ['cac', 'CAC', 'usd', 'down'], ['kept_d30', 'Kept 30 days', 'pct', 'up'],
  ['spend', 'Spend', 'usd', null]];
const SERIES: Record<string, (number | null)[]> = {new_customers: [30, 36, 41, 48], cac: [52, 48, 45, 41], kept_d30: [62, 66, 71, null], spend: [1560, 1730, 1845, 1970]};
const CHANNELS = [{slug: 'search', name: 'Search'}, {slug: 'referrals', name: 'Referrals'}];
// GET /api/v2/pipelines: General, then the Growth factory's Lead and the Growth Experiments a growth pack files ideas into.
const pipe = (id: string, name: string, factory_id: string | null, preset: string | null, retired_at: string | null = null) => ({id, name, goal: '', prefix: null,
  manager_id: null, owner_id: null, private: false, factory_id, preset, settings_version: 1, retired_at, stages: [], done: null, closed: null});
const GENERAL = pipe('general', 'General', null, 'general');
const PIPELINES = [GENERAL, pipe('lead', 'Lead', 'growth', 'lead'), pipe('growth-experiments', 'Growth Experiments', 'growth', 'growth-experiments')];
const BOARD = '#/tasks?pipeline=growth-experiments&who=all';
const upTo = (month: string) => MONTHS.slice(0, MONTHS.indexOf(month) + 1);
const DATA: Record<string, (s: CardSettings, month: string) => unknown> = {
  growth_number: (s, month) => {
    const [metric, label, format, good] = METRICS.find(m => m[0] === s.metric)!, cal = upTo(month);
    const values = cal.map((_, i) => s.channel ? Math.round(SERIES[metric]![i]! / 2) : SERIES[metric]![i]);
    let at = values.length - 1;
    while (at > 0 && values[at] == null && metric.startsWith('kept')) at--;
    return {metric, label, format, good, month, value: values[at], prev: values[at - 1] ?? null, from: at === values.length - 1 ? '' : cal[at],
      series: cal.map((m, i) => ({month: m, value: values[i]}))};
  },
  growth_funnel: (_, month) => {
    const k = MONTHS.indexOf(month), v = [12000, 480, 210, 160, 22].map(x => Math.round(x * (0.85 + 0.05 * k)));
    const stages = ['Reach', 'Acquire', 'Activate', 'Retain', 'Refer'].map((label, i) => ({stage: label.toLowerCase(), label,
      kpi: i === 3 ? null : 'k' + i, name: i === 3 ? '' : ['Site visits', 'Signups', 'Activated', '', 'Referrals'][i], unit: '', value: i === 3 ? null : v[i],
      prev: i === 3 ? null : Math.round(v[i]! * 0.9), conv: i === 0 || i === 3 || i === 4 ? null : Math.round(v[i]! / v[i - 1]! * 1000) / 10,
      north_star: i === 2, series: i === 3 ? [] : [v[i]! * 0.8, v[i]! * 0.9, v[i]]}));
    return {stages, north_star: {id: 'k2', name: 'Activated', unit: '', value: stages[2]!.value, prev: stages[2]!.prev, target: 300}};
  },
  growth_channels: (s, month) => ({month, rows: [
    {slug: 'search', name: 'Search', new_customers: 31, cac: 38, ltv_cac: 3.2, ltv_from: '', series: [18, 22, 25, 31],
      tasks: [{id: 't1', title: 'Search: long-tail landing pages', stage: 'scaling'}, {id: 't2', title: 'Search: competitor keywords', stage: 'testing'},
        {id: 't3', title: 'Search: brand terms', stage: 'idea'}], more: 2},
    {slug: 'referrals', name: 'Referrals', new_customers: 17, cac: null, ltv_cac: 4.1, ltv_from: '2026-09', series: [12, 14, 16, 17], tasks: [], more: 0},
  ].slice(0, s.count || 10)}),
  growth_retention: (s, month) => ({columns: ['30 days', '90 days', '180 days'], rows: upTo(month).slice(-(s.months || 6)).map((m, i) => ({month: m,
    new_customers: SERIES.new_customers![i], kept: [[62, 48, 35], [66, 51, null], [71, null, null], [null, null, null]][i]}))}),
  growth_board: s => ({lanes: [
    {stage: 'testing', label: 'Testing', count: 4, cards: [{id: 't2', title: 'Search: competitor keywords', channel: 'Search', owner_id: 'human:ana'},
      {id: 't4', title: 'Referral reward email', channel: 'Referrals', owner_id: ''}].slice(0, s.count || 3)},
    {stage: 'scaling', label: 'Scaling', count: 1, cards: [{id: 't1', title: 'Search: long-tail landing pages', channel: 'Search', owner_id: 'human:ana'}]},
    {stage: 'idea', label: 'Idea', count: 0, cards: []}]}),
};
// Computed retention (backend/growth_computed.py): September 2026 cut by channel, segment or plan, monthly or weekly.
// A cohort is [value, new, day 30 [count] or null for not yet, LTV, CAC status].
const fig = (value: number | null, status = 'ok', more: Record<string, unknown> = {}) => ({value: status === 'ok' ? value : null, status, origin: 'computed', ...more});
const KEYS = {still: ['cus_1042', 'cus_1047'], churned: [{key: 'cus_1044', churned: '2026-09-19'}]};
function cohortOf(by: string, grain: string, value: string | null, n: number, kept: number, period = '2026-09', start = '2026-09-01', end = '2026-09-30') {
  const few = n < 30;
  return {cut: by, value, grain, period, start, end, computed_from: n, too_few: few, new_customers: fig(n),
    kept_d30: fig(Math.round(kept * 1000 / n) / 10, few ? 'too_few' : 'ok', {count: kept, denominator: n, fills_on: '2026-10-30'}),
    kept_d90: fig(null, 'not_yet', {count: null, denominator: n, fills_on: '2026-12-29'}), kept_d180: fig(null, 'not_yet', {count: null, denominator: n, fills_on: '2027-03-29'}),
    ltv: few ? fig(null, 'too_few') : fig(by === 'segment' ? 612 : 540), cac: by !== 'channel' ? fig(null, 'not_split') : grain === 'week' ? fig(null, 'spend_monthly') : value === 'unknown' ? fig(null, 'no_spend') : fig(45)};
}
function computedView(by: string, grain: string, keys: boolean, customers: boolean) {
  const cohorts = grain === 'week' ? [cohortOf(by, grain, 'search', 31, 25, '2026-09-28', '2026-09-28', '2026-10-04'), cohortOf(by, grain, 'search', 33, 27, '2026-09-21', '2026-09-21', '2026-09-27')]
    : by === 'segment' ? [cohortOf(by, grain, 'agency', 64, 52), cohortOf(by, grain, 'studio', 41, 27), cohortOf(by, grain, 'solo', 19, 12)]
    : by === 'plan' ? [cohortOf(by, grain, 'pro', 71, 56), cohortOf(by, grain, null, 53, 37)]
    : [cohortOf(by, grain, 'search', 64, 52), cohortOf(by, grain, 'unknown', 19, 12)];
  if (customers) for (const c of cohorts) Object.assign(c.kept_d30, {rule: `became customer ${c.start} to ${c.end}, ${by} ${c.value}, still paying on day 30`}, keys ? {customers: KEYS} : {});
  return {by, grain, month: '2026-09', as_of: '2026-11-13', min_cohort: 30, computed_from: 124, cohorts, keys_visible: keys && customers,
    cac_note: by === 'segment' ? 'spend is not split by segment' : by === 'plan' ? 'spend is not split by plan' : grain === 'week' ? 'spend is monthly' : null,
    loads: [{source: 'upload', customers: 124, removed: 0, loaded_by: 'human:ana', loaded_at: '2026-11-13T06:00:00'}]};
}
const NAMES: Record<string, string> = {growth_number: 'Number', growth_funnel: 'Funnel', growth_channels: 'Channels', growth_retention: 'Retention', growth_board: 'Growth board', kpi: 'KPI'};
const FIELDS: Record<string, string[]> = {growth_number: ['metric', 'channel'], growth_funnel: [], growth_channels: ['count'], growth_retention: ['months'], growth_board: ['count'], kpi: ['kpi']};
const CHOICES = {templates: Object.entries(NAMES).map(([id, name]) => ({id, name, fields: FIELDS[id]})), teams: [], kpis: [{id: 'k2', name: 'Activated'}], pages: [],
  metrics: METRICS.map(([id, label]) => ({id, label})), channels: CHANNELS};
const DEFAULTS: [string, string, CardSettings][] = [['growth_funnel', 'large', {}], ['growth_number', 'small', {metric: 'new_customers'}], ['growth_number', 'small', {metric: 'cac'}],
  ['growth_number', 'small', {metric: 'kept_d30'}], ['growth_channels', 'large', {count: 10}], ['growth_retention', 'medium', {months: 6}],
  ['growth_board', 'medium', {count: 3}]];

// GET /api/v2/growth/setup (backend/growth_setup.py show) with the first `done` steps done, as the panel reads it.
const STEPS = ['settings', 'channel', 'source', 'month'];
const SEARCH = {slug: 'search', name: 'Search'};
interface Setup {done: boolean; celebrate: boolean; steps: {step: string; done: boolean}[]; next: Record<string, unknown> | null; channel: typeof SEARCH | null;
  set: Record<string, unknown>; you_may: Record<string, boolean>}
function setupAt(done: number, {may = true, sources = 1, celebrate = false}: {may?: boolean; sources?: number; celebrate?: boolean} = {}): Setup {
  const step = STEPS[done];
  const next = step ? {step, number: done + 1, says: '', you_may: may, ...(may ? {} : {who_may: [{ref: 'human:ana', name: 'Ana Silva'}, {ref: 'human:ben', name: 'Ben Ode'}]}),
    ...(step === 'source' ? {sources} : {}), ...(step === 'month' ? {month: '2026-10'} : {})} : null;
  return {done: done >= 4, celebrate, steps: STEPS.map((s, i) => ({step: s, done: i < done})), next, channel: done >= 1 ? SEARCH : null,
    set: {settings: done >= 1 ? {unit: 'customers', revenue_month: 80, margin_pct: 70} : null, channel: done >= 2 ? SEARCH : null,
      source: done >= 3 ? {system: 'Google Ads', truth_for: 'ad spend'} : null, month: done >= 4 ? {month: '2026-10', spend: 1800, new_customers: 6} : null},
    you_may: {settings: may, channels: may, sources: may, months: may}};
}

function server(me: Person, pipelines: unknown[] = PIPELINES, setup: Setup = setupAt(4), emptyCards: string[] = [], computed = false) {
  let n = 0, saved = false;
  const keys = me.company_role !== 'member';
  const empty = !setup.steps[3]!.done;   // a Growth with no month yet: every card shows nothing (backend/cards.py card_view)
  const none = (template: string) => empty || emptyCards.includes(template);
  let pins: Pin[] = DEFAULTS.map(([template, size, settings], i) => ({id: null, item: null, position: i, size, made_by: null, card: {id: null, template, settings}}));
  const reads: unknown[] = [], writes: Write[] = [];
  const draw = (c: PinCard, month: string) => ({...c, title: c.title || '', name: NAMES[c.template], heading: c.title || NAMES[c.template], team_name: '', hidden: none(c.template),
    data: none(c.template) ? null : computed && c.template === 'growth_retention' ? {...(DATA[c.template]!(c.settings, month) as object), computed: {...computedView('channel', 'month', keys, false), typed: [{value: 'referrals', new_customers: 25, kept: [70, null, null], origin: 'typed'}]}}
      : DATA[c.template]!(c.settings, month)});
  const block = () => setup.done ? {done: true} : {done: false, steps: setup.steps, next: setup.next};
  const view = (month: string) => ({place: 'growth', kind: 'growth', saved, can_edit: me.company_role !== 'member', month, months: MONTHS, setup: block(),
    pins: pins.map(p => ({...p, card: draw(p.card, month)}))});
  const save = () => {
    if (saved) return;
    saved = true;
    pins = pins.map(p => { const cid = 'c' + (++n); return {...p, id: 'p' + n, item: 'card:' + cid, card: {...p.card, id: cid}}; });
  };
  return {reads, writes, setup, handle(method: string, p: string, body: PostedCard, query: URLSearchParams): [number, unknown] | null {
    if (method !== 'GET' && /^\/api\/v2\/(places|pins|cards|growth)\b/.test(p)) writes.push([method, p, body]);
    if (p === '/api/v2/places/dashboard' && method === 'GET') return [200, {
      place: 'dashboard', kind: 'dashboard', saved: true, can_edit: me.company_role !== 'member',
      pins: [{id: 'overview-pin', item: 'card:overview-card', position: 0, size: 'small', made_by: 'human:ana',
        card: {id: 'overview-card', template: 'kpi', title: 'Company activation', heading: 'Company activation', name: 'KPI',
          settings: {kpi: 'k2'}, team_name: '', hidden: false, data: {name: 'Activated', unit: '', value: 210, spark: []}}}],
    }];
    if (p === '/api/v2/places/growth') {
      const month = (query.get('month') || MONTHS.at(-1))!;
      reads.push(month);
      if (!MONTHS.includes(month) && month !== '2026-11') return [422, {error: {code: 'invalid', detail: 'A month as YYYY-MM'}}];
      return [200, view(month)];
    }
    if (p === '/api/v2/pipelines' && method === 'GET') return [200, {pipelines}];
    if (p === '/api/v2/cards/choices') return query.get('place') === 'growth' ? [200, CHOICES] : [200, {...CHOICES, templates: []}];
    if (p === '/api/v2/cards' && method === 'POST') {
      if (me.company_role === 'member') return [403, {error: {code: 'forbidden', detail: 'The owner, admins and bots arrange the Growth page'}}];
      save();
      const cid = 'c' + (++n);
      pins.push({id: 'p' + (++n), item: 'card:' + cid, position: pins.length, size: body.size, made_by: 'human:ana',
        card: {id: cid, template: body.template, settings: body.settings, title: body.title}});
      return [200, {card: draw(pins.at(-1)!.card, MONTHS.at(-1)!), pin: 'p' + n}];
    }
    if (p === '/api/v2/growth/computed') {
      reads.push(query.toString());
      return [200, computedView(query.get('by') || 'channel', query.get('week') ? 'week' : query.get('grain') || 'month', keys, query.get('customers') === 'true')];
    }
    if (p === '/api/v2/growth/customers' && method === 'POST') {
      const header = String((body as unknown as {csv?: string}).csv || '').split('\n')[0]!;
      if (/email/.test(header)) return [422, {error: {code: 'customer_facts', detail: 'Nothing loaded. Growth keeps only: customer key, signed up, became customer, channel, segment, plan, churned, revenue per month. Remove these columns and load again: email, name.', rule: 'columns'}}];
      return [200, {customers: 2, removed: 0, unknown_channel: 1}];
    }
    if (p === '/api/v2/growth/setup') return [200, setup];
    if (p === '/api/v2/growth/setup/shown') { if (setup.done) setup.celebrate = false; return [200, setup]; }
    // A channel added finishes step 2: the panel reads its steps again after the save.
    if (p === '/api/v2/growth/channels' && method === 'POST') { Object.assign(setup, setupAt(2)); return [200, {slug: 'search', name: 'Search'}]; }
    if (p === '/api/v2/growth/settings') return [200, {unit: 'customers', revenue_month: 49, margin_pct: 80}];
    if (p === '/api/v2/growth/channels') return [200, {channels: CHANNELS.map(c => ({...c, stage: 'testing', owner_ref: 'human:ana', track: 'performance'})), may_edit: true}];
    if (p === '/api/v2/growth/cohorts') return [200, {cohorts: MONTHS.map((month, i) => ({channel: query.get('channel'), month, new_customers: SERIES.new_customers![i],
      kept_d30: SERIES.kept_d30![i], kept_d90: null, kept_d180: null, quality: 'actual'}))}];
    return null;
  }};
}

async function open(browser: Browser, {viewport = DESKTOP, hash = '#/growth', me = OWNER, theme = 'dark', pipelines = PIPELINES as unknown[], setup = setupAt(4), emptyCards = [] as string[], computed = false} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE});
  const errors: string[] = [], fake = server(me, pipelines, setup, emptyCards, computed);
  page.on('pageerror', (e: Error) => errors.push(e.message));
  await page.addInitScript(t => { try { localStorage.setItem('tico.theme', t); } catch { /* none */ } }, theme);
  const config = {company_name: 'Acme'};
  await page.route('**/*', async (route: Route) => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: /\.(?:js|ts)$/.test(file) ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.html') ? 'text/html' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events' || p.endsWith('/watch')) return;
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/employees') return json(roster.employees);
    if (p === '/api/humans') return json(roster.humans);
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/groups') return json([]);
    const answer = fake.handle(method, p, req.postData() ? JSON.parse(req.postData()!) : {}, url.searchParams);
    if (answer) return json(answer[1], answer[0]);
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  return {page, errors, fake};
}
const sideways = (page: Page) => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
// The board links once the page knows the company's pipelines: [href, shown] for the head's, then each "+N more".
const boardLinks = async (page: Page) => {
  await page.waitForFunction('typeof GR_BOARD_PIPE !== "undefined" && GR_BOARD_PIPE !== undefined', null, {timeout: 5000}).catch(() => { /* the links as they stand */ });
  return page.locator('.dash-head [data-gr-board], #dash-grid a.grc-more').evaluateAll(els => els.map(el => [el.getAttribute('href') || '', (el as HTMLElement).checkVisibility()]));
};
const headings = (page: Page) => page.locator('#dash-grid .dash-pin-head h2').allInnerTexts();
// WCAG contrast of an element's text on its own background.
const contrast = (loc: Locator) => loc.evaluate((el: Element) => {
  const rgb = (c: string) => { const ctx = document.createElement('canvas').getContext('2d')!; ctx.fillStyle = c; ctx.fillRect(0, 0, 1, 1); return [...ctx.getImageData(0, 0, 1, 1).data].slice(0, 3); };
  const lum = (c: string) => { const [r, g, b] = rgb(c).map((v: number) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }) as [number, number, number]; return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const s = getComputedStyle(el), a = lum(s.color), b = lum(s.backgroundColor);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
});
async function shot(page: Page, name: string) {
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
      assert.deepEqual((await boardLinks(page))[0], [BOARD, true], 'the Growth board opens Growth Experiments');
      assert.equal(await page.locator('[data-gr-settings]').count(), 1);
      assert.equal(await page.locator('#gr-start').isVisible(), false, 'a set-up Growth has no Get started panel');
      assert.equal(await page.locator('#dash-grid .grs-fill').count(), 0);
      assert.equal(await page.locator('.dash-actions').isVisible(), true);
      assert.equal(await page.locator('[data-dash-add="page"]').count(), 1, 'an owner pins pages too');
      // Arranging is in edit mode: Edit shows the grips, menus and + Card / + Page; Done hides them.
      const editControls = '[data-dash-grip], [data-dash-menu], [data-dash-add], [data-dash-mode="done"]';
      const shown = (sel: string) => page.locator(sel).evaluateAll(els => els.filter(el => el.checkVisibility()).length);
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
      assert.equal(await rows.nth(0).locator('a.grc-more').getAttribute('href'), BOARD + '&tag=channel%3Asearch');
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
      assert.equal(await lanes.nth(0).locator('a.grc-more').getAttribute('href'), BOARD);
      await shot(page, 'desktop-growth');

      // A month picked reads every card again for it, and the address keeps it.
      await page.locator('[data-gr-month]').selectOption('2026-08');
      await page.waitForFunction(() => document.querySelector('#dash-grid .grc-num-v')?.textContent === '36');
      assert.equal(fake.reads.at(-1), '2026-08');
      assert.equal(await page.evaluate(() => location.hash), '#/growth?m=2026-08');
      assert.equal(await page.locator('#dash-grid .grc-ret-t tbody tr').count(), 2);
      // Opened at that address, the page reads that month.
      await page.evaluate(() => { location.hash = '#/overview'; });
      // Both pages use #dash-grid: wait for Company before accepting any pins from it.
      await page.getByRole('heading', {name: 'Company', exact: true}).waitFor();
      await page.locator('#dash-grid .dash-pin').first().waitFor();
      assert.deepEqual(await headings(page), ['Company activation'], 'Company draws its own cards');
      assert.equal(await page.locator('#dash-grid .dash-big strong').innerText(), '210');
      await page.evaluate(() => { location.hash = '#/growth?m=2026-09'; });
      await page.waitForFunction(() => (document.querySelector('[data-gr-month]') as HTMLSelectElement | null)?.value === '2026-09');
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
      const sent = fake.writes.find(([m, p]) => m === 'POST' && p === '/api/v2/cards')![2];
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
      assert.ok(widths[0]! >= 340, 'the full width');
      assert.equal(await sideways(page), false, 'no sideways scroll');
      const past = await page.evaluate(() => [...(document.querySelectorAll('#main *') as unknown as Iterable<Element>)].filter(el => {
        const r = el.getBoundingClientRect();
        return r.width && r.right > innerWidth + 1 && !el.closest('.grc-ret');
      }).map(el => el.className || el.tagName).slice(0, 5));
      assert.deepEqual(past, [], 'nothing past the edge');
      assert.equal(await page.locator('#dash-grid .grc-ch thead').evaluate(el => el.getBoundingClientRect().height <= 1), true, 'channels stack as rows');
      const cells = page.locator('#dash-grid .grc-ret-t td.grc-k:not(.grc-k-none)');
      for (let i = 0; i < await cells.count(); i++) assert.ok(await contrast(cells.nth(i)) >= 4.5, `retention cell ${i} reads at 4.5:1 (${theme})`);
      for (const b of await page.locator('.dash-head :is(a,button,select)').all()) {
        if (!(await b.isVisible())) continue;
        const box = (await b.boundingBox())!;
        assert.ok(box.x >= 0 && box.x + box.width <= PHONE.width + 1, 'the head fits');
      }
      await shot(page, `phone-growth-${theme}`);
      assert.deepEqual(errors, []);
      await page.close();
    }
    for (const viewport of [{width: 1280, height: 800}, PHONE]) {
      // The board links go to the company's Growth pipeline at 1280 and on a phone: the head's, each channel's with its
      // tag, the board card's; never General. A retired one is passed over; with none, no link at all.
      const w = viewport.width, cases: [string, unknown[], string][] = [
        ['growth', PIPELINES, BOARD],
        ['a retired one first', [GENERAL, pipe('gx-old', 'Old experiments', 'growth', 'growth-experiments', '2026-01-01T00:00:00Z'), ...PIPELINES.slice(1)], BOARD],
        ['none', [GENERAL, pipe('lead', 'Lead', 'growth', 'lead')], '']];
      for (const [what, pipelines, board] of cases) {
        const {page, errors} = await open(browser, {viewport, pipelines});
        await page.locator('#dash-grid .grc-funnel').waitFor();
        const links = await boardLinks(page);
        if (board) assert.deepEqual(links, [[board, true], [board + '&tag=channel%3Asearch', true], [board, true]], `${w} ${what}: the board links`);
        else assert.deepEqual(links.filter(([, shown]) => shown), [], `${w} ${what}: no board link`);
        assert.ok(links.every(([href]) => !String(href).includes('pipeline=general')), `${w} ${what}: never General`);
        if (board && w === PHONE.width) {
          await page.locator('.dash-head [data-gr-board]').click();
          await page.waitForFunction((b: string) => location.hash === b, board);
        }
        assert.deepEqual(errors, []);
        await page.close();
      }
    }
    {
      // Back on Growth, the pipelines already read draw the board link at once, before the fresh read answers.
      const {page, errors} = await open(browser);
      await page.locator('#dash-grid .grc-funnel').waitFor();
      assert.deepEqual((await boardLinks(page))[0], [BOARD, true]);
      await page.route('**/api/v2/pipelines', () => { /* never answers */ });
      await page.evaluate(() => { location.hash = '#/growth/sources'; });
      await page.locator('[data-gr-back]').waitFor();
      await page.evaluate(() => { location.hash = '#/growth'; });
      await page.locator('#dash-grid .grc-funnel').waitFor();
      const head = page.locator('.dash-head [data-gr-board]');
      assert.deepEqual([await head.getAttribute('href'), await head.isVisible()], [BOARD, true], 'the link from the pipelines already read');
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
    {
      // Computed retention: the card cuts by channel, segment or plan, monthly or weekly; "not yet" says when it fills; a
      // cohort under the minimum shows counts; the foot says how many customers, when and from where; CAC says why not.
      const {page, errors, fake} = await open(browser, {computed: true});
      const card = page.locator('#dash-grid [data-grx]');
      await card.locator('table').waitFor();
      const rowsOf = () => card.locator('tbody tr:not(.grx-wk)').evaluateAll(els => els.map(el => [...el.children].map(c => (c as HTMLElement).innerText.trim())));
      assert.deepEqual(await rowsOf(), [['Search', '64', '81%', 'not yet · 29 Dec', 'not yet · 29 Mar', '$540', '$45'], ['Unknown', '19', '12 of 19', 'not yet · 29 Dec', 'not yet · 29 Mar', 'too few', '—'],
        ['Referrals typed', '25', '70%', '—', '—', '—', '—']], 'a channel with typed retention and no customers stays, marked typed');
      assert.equal(await card.locator('tr[data-origin="typed"] button').count(), 0, 'a typed figure has no customers to open');
      assert.match(await card.locator('.grx-foot').innerText(), /Rows marked typed were typed by the team/);
      assert.match(await card.locator('.grx-foot').innerText(), /Computed from 124 customers · loaded 13 Nov 06:00 from an upload in Growth\./);
      assert.match(await card.locator('.grx-foot').innerText(), /Under 30 customers a cohort shows counts only: too few for a rate\./);
      assert.ok(await contrast(card.locator('td.grc-k').first()) >= 4.5, 'a computed share reads at 4.5:1');
      await card.locator('[data-gr-seg="grx-by"] button[data-v="segment"]').click();
      await page.waitForFunction(() => document.querySelector('#dash-grid [data-grx] thead th')?.textContent === 'Segment');
      assert.deepEqual((await rowsOf()).map(r => [r[0], r[2], r[5], r[6]]), [['agency', '81%', '$612', 'not split'], ['studio', '66%', '$612', 'not split'], ['solo', '12 of 19', 'too few', 'not split']]);
      assert.match(await card.locator('.grx-foot').innerText(), /CAC is per channel: spend is not split by segment\./);
      assert.match(await card.locator('.grx-foot').innerText(), /Typed retention for Referrals shows under Cut by Channel, Monthly\./);
      await card.locator('[data-gr-seg="grx-by"] button[data-v="plan"]').click();
      await page.waitForFunction(() => document.querySelector('#dash-grid [data-grx] thead th')?.textContent === 'Plan');
      assert.deepEqual((await rowsOf()).map(r => r[0]), ['pro', 'No plan']);
      await card.locator('[data-gr-seg="grx-by"] button[data-v="channel"]').click();
      await card.locator('[data-gr-seg="grx-grain"] button[data-v="week"]').click();
      await page.waitForFunction(() => document.querySelector('#dash-grid [data-grx] tr.grx-wk'));
      assert.deepEqual(await card.locator('tr.grx-wk').allInnerTexts(), ['Week of 28 Sep', 'Week of 21 Sep']);
      assert.match(await card.locator('.grx-foot').innerText(), /Weekly CAC needs daily spend: spend is monthly\./);
      assert.ok(fake.reads.includes('by=channel&grain=week&month=2026-09'), 'the weekly read asks for the month');
      await shot(page, 'desktop-growth-computed');
      // A figure opens its customers: the rule, still customers and churned with the date, counts matching the figure.
      await card.locator('[data-gr-seg="grx-grain"] button[data-v="month"]').click();
      await page.waitForFunction(() => !document.querySelector('#dash-grid [data-grx] tr.grx-wk') && document.querySelector('#dash-grid [data-grx] table'));
      await card.locator('button.grx-open').first().click();
      const dlg = page.locator('dialog.grx-dlg');
      await dlg.waitFor();
      assert.equal(await dlg.getAttribute('aria-label'), 'Search · Sep 2026 · day 30: 81% (52 of 64)');
      assert.match(await dlg.innerText(), /Became customer 2026-09-01 to 2026-09-30, channel search, still paying on day 30\./);
      assert.match(await dlg.innerText(), /Still customers \(52\)[\s\S]*cus_1042[\s\S]*Churned by day 30 \(12\)[\s\S]*cus_1044\s+19 Sep/i);
      assert.ok(fake.reads.includes('by=channel&customers=true&month=2026-09'), 'the trace reads one period');
      await shot(page, 'desktop-growth-trace');
      await dlg.locator('[data-close]').first().click();
      // Upload: a column outside the list loads nothing and names the columns; a good file says how many loaded.
      await card.locator('[data-grx-upload]').click();
      const up = page.locator('dialog.grx-dlg');
      assert.match(await up.innerText(), /may tokenise keys/);
      await up.locator('input[type=file]').setInputFiles({name: 'customers.csv', mimeType: 'text/csv', buffer: Buffer.from('customer_key,became_customer,channel,email,name\ncus_1,2026-09-01,search,a@b.example,Ann\n')});
      await up.locator('button[type=submit]').click();
      const refused = up.locator('[data-grx-refused]');
      await refused.waitFor();
      assert.match(await refused.innerText(), /^Nothing loaded\s+Growth keeps only: [\s\S]*Remove these columns and load again: email, name\.$/);
      await shot(page, 'desktop-growth-refused');
      await up.locator('input[type=file]').setInputFiles({name: 'customers.csv', mimeType: 'text/csv', buffer: Buffer.from('customer_key,became_customer,channel\ncus_1,2026-09-01,search\n')});
      await up.locator('button[type=submit]').click();
      await up.waitFor({state: 'detached'});
      assert.equal(fake.writes.filter(w => w[1] === '/api/v2/growth/customers').length, 2);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // ---- Growth's first run (ui/app/growth-start.ts): the Get started panel and the empty cards.
    const panel = (page: Page) => page.locator('#gr-start');
    const stepKinds = (page: Page) => page.locator('#gr-start .grs-step .grs-k').evaluateAll(els => els.map(el => (el.firstChild?.textContent || '').trim()));
    const panelButtons = (page: Page) => page.locator('#gr-start button.grs-go');
    {
      // AC-1: an admin, nothing set: four steps in order, none done, step 1 next with one button that opens Settings.
      // AC-7: every empty card names the step that fills it; the Funnel names Goals; none says only "No ... yet".
      const {page, errors} = await open(browser, {setup: setupAt(0)});
      await panel(page).locator('.grs-step').first().waitFor();
      assert.equal(await panel(page).getAttribute('aria-label'), 'Get started');
      assert.match(await panel(page).locator('.grs-h').innerText(), /Get started/);
      assert.deepEqual(await page.locator('#gr-start .grs-step').evaluateAll(els => els.map(el => el.getAttribute('data-grs-step'))), STEPS);
      assert.deepEqual(await stepKinds(page), ['Next', 'Step 2', 'Step 3', 'Step 4']);
      assert.equal(await page.locator('#gr-start .grs-done').count(), 0);
      assert.equal(await page.locator('#gr-start [aria-current="step"]').getAttribute('data-grs-step'), 'settings');
      assert.match(await page.locator('#gr-start .grs-prog').innerText(), /0 of 4 done/);
      assert.equal(await panelButtons(page).count(), 1, 'one button');
      // The panel sits between the head and the cards.
      const [headBottom, panelTop, panelBottom, gridTop] = await page.evaluate(() => [document.querySelector('.dash-head')!.getBoundingClientRect().bottom,
        ...(() => { const r = document.querySelector('#gr-start')!.getBoundingClientRect(); return [r.top, r.bottom]; })(), document.querySelector('#dash-grid')!.getBoundingClientRect().top]);
      assert.ok(headBottom! <= panelTop! && panelBottom! <= gridTop!, 'under the head, over the cards');
      const fills = page.locator('#dash-grid .grs-fill');
      assert.equal(await fills.count(), 7, 'every card says what fills it');
      assert.deepEqual(await fills.evaluateAll(els => els.map(el => el.getAttribute('data-grs-fill'))), ['goals', 'month', 'month', 'month', 'channel', 'month', 'board']);
      assert.match(await fills.nth(1).innerText(), /step 4/);
      assert.match(await fills.nth(3).innerText(), /kept 30 days/, 'a kept share waits for 30 days');
      assert.match(await fills.nth(4).innerText(), /step 2/);
      assert.equal(await fills.nth(0).locator('a').getAttribute('href'), '#/strategy/goals', 'the Funnel opens Goals');
      assert.equal(await page.locator('#dash-grid .dash-none').evaluateAll(els => els.filter(el => /^No .* yet$/.test((el.textContent || '').trim())).length), 0);
      // A card's step that is not next yet points at the panel's next step instead of a form.
      await fills.nth(1).locator('[data-gr-step]').click();
      await page.waitForFunction(() => document.querySelector('#gr-start .grs-next')?.classList.contains('grs-flash'));
      assert.equal(await page.locator('dialog.gr-dlg[open]').count(), 0);
      await shot(page, 'desktop-growth-start');
      await panelButtons(page).click();
      await page.locator('dialog.gr-dlg[open][aria-label="Settings"] [name="revenue_month"]').waitFor();
      await page.keyboard.press('Escape');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A member opens a figure to its counts and rule, no keys; uploads nothing.
      const {page, errors} = await open(browser, {computed: true, me: MEMBER});
      const card = page.locator('#dash-grid [data-grx]');
      await card.locator('table').waitFor();
      assert.equal(await card.locator('[data-grx-upload]').count(), 0, 'no upload for a member');
      await card.locator('button.grx-open').first().click();
      const dlg = page.locator('dialog.grx-dlg');
      await dlg.waitFor();
      assert.match(await dlg.innerText(), /Still customers: 52\. Churned by day 30: 12\./);
      assert.doesNotMatch(await dlg.innerText(), /cus_/);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // AC-2: step 1 done shows what was set; step 2 next opens the new channel form. Adding it marks step 2 done at once.
      const {page, errors} = await open(browser, {setup: setupAt(1)});
      await panel(page).locator('.grs-next').waitFor();
      assert.deepEqual(await stepKinds(page), ['Done', 'Next', 'Step 3', 'Step 4']);
      assert.equal(await page.locator('#gr-start [data-grs-step="settings"] .grs-l').innerText(), '$80 per customer a month · 70% margin');
      assert.match(await page.locator('#gr-start .grs-prog').innerText(), /1 of 4 done/);
      assert.equal(await panelButtons(page).getAttribute('data-gr-step'), 'channel');
      await page.locator('#dash-grid .grs-fill[data-grs-fill="channel"] [data-gr-step]').click();
      const dlg = page.locator('dialog.gr-dlg[open][aria-label="New channel"]');
      await dlg.locator('[name="name"]').waitFor();
      await page.keyboard.press('Escape');
      await panelButtons(page).click();
      await dlg.locator('[name="name"]').fill('Search');
      await dlg.locator('[type="submit"]').click();
      await page.waitForFunction(() => document.querySelector('#gr-start [data-grs-step="channel"]')?.classList.contains('grs-done'));
      assert.match(await page.locator('#gr-start [data-grs-step="channel"]').getAttribute('class') || '', /grs-just/, 'the step just done pops');
      assert.equal(await page.locator('#gr-start [data-grs-step="channel"] .grs-l').innerText(), 'Search');
      assert.equal(await page.locator('#gr-start [aria-current="step"]').getAttribute('data-grs-step'), 'source');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // AC-3: step 3 next opens Search's form at its source picker; with no source yet, it opens Sources.
      const {page, errors} = await open(browser, {setup: setupAt(2)});
      await panel(page).locator('.grs-next').waitFor();
      assert.equal(await panelButtons(page).innerText(), 'Choose the source');
      await panelButtons(page).click();
      const dlg = page.locator('dialog.gr-dlg[open][aria-label="Search"]');
      await dlg.locator('[name="source_id"]').waitFor();
      await page.waitForFunction(() => document.activeElement?.getAttribute('name') === 'source_id');
      await page.keyboard.press('Escape');
      await page.close();
      const none = await open(browser, {setup: setupAt(2, {sources: 0})});
      await panel(none.page).locator('.grs-next').waitFor();
      assert.equal(await panelButtons(none.page).innerText(), 'Add a source');
      await panelButtons(none.page).click();
      await none.page.waitForFunction(() => location.hash === '#/growth/sources');
      assert.deepEqual([...errors, ...none.errors], []);
      await none.page.close();
    }
    {
      // AC-4: step 4 next opens the Months form on Search and the last full month.
      const {page, errors} = await open(browser, {setup: setupAt(3)});
      await panel(page).locator('.grs-next').waitFor();
      assert.equal(await page.locator('#gr-start [data-grs-step="source"] .grs-l').innerText(), 'Google Ads · ad spend');
      assert.equal(await panelButtons(page).innerText(), 'Add Oct 2026');
      await panelButtons(page).click();
      const dlg = page.locator('dialog.gr-dlg[open][aria-label="Search · New month"]');
      assert.equal(await dlg.locator('[name="month"]').inputValue(), '2026-10');
      assert.equal(await dlg.locator('[name="spend"]').count(), 1);
      assert.equal(await dlg.locator('[name="new_customers"]').count(), 1);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // AC-5: all four done says so once and points at Search's numbers; the next open has no panel.
      const setup = setupAt(4, {celebrate: true});
      const {page, errors, fake} = await open(browser, {setup});
      await panel(page).locator('.grs-yay').waitFor();
      assert.match(await panel(page).innerText(), /You’re set up/);
      assert.match(await panel(page).innerText(), /Search’s first numbers are in/);
      await page.locator('#dash-grid .grc-ch tbody tr').first().waitFor();
      await shot(page, 'desktop-growth-start-done');
      assert.ok(fake.writes.some(([m, p]) => m === 'POST' && p === '/api/v2/growth/setup/shown'), 'said once: recorded');
      await panel(page).locator('[data-grs-see]').click();
      await page.waitForFunction(() => document.querySelector('#dash-grid tr[data-channel="search"]')?.classList.contains('grs-flash'));
      assert.equal(await panel(page).isVisible(), false);
      await page.evaluate(() => { location.hash = '#/overview'; });
      await page.getByRole('heading', {name: 'Company', exact: true}).waitFor();
      await page.evaluate(() => { location.hash = '#/growth'; });
      await page.locator('#dash-grid .grc-funnel').waitFor();
      await page.waitForTimeout(200);
      assert.equal(await panel(page).isVisible(), false, 'it does not come back');
      assert.deepEqual(errors, []);
      await page.close();
    }
    for (const me of [OWNER, MEMBER]) {
      // AC-13: a set-up Growth with a card still empty (Retention waits 30 days for kept customers) reads as before the
      // panel: plain words, no step link, nothing that says an admin may not.
      const {page, errors} = await open(browser, {me, emptyCards: ['growth_retention', 'growth_board']});
      await page.locator('#dash-grid .grc-funnel').waitFor();
      const lines = page.locator('#dash-grid .dash-none');
      await lines.first().waitFor();
      assert.equal(await panel(page).isVisible(), false);
      assert.equal(await page.locator('#dash-grid [data-gr-step]').count(), 0, `${me.id}: no step links once set up`);
      const words = await lines.allInnerTexts();
      assert.ok(words.every(w => !/step \d/i.test(w)), `${me.id}: no step named once set up: ${words}`);
      assert.ok(words.some(w => /kept 30 days/.test(w)), `${me.id}: Retention says what fills it`);
      assert.equal(await page.locator('.toast').count(), 0);
      if (me === OWNER) await shot(page, 'desktop-growth-set-up-empty-card');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // AC-6: a member with step 2 next sees the owner and admins by name, and no button.
      const {page, errors} = await open(browser, {me: MEMBER, setup: setupAt(1, {may: false})});
      await panel(page).locator('.grs-next').waitFor();
      assert.equal(await page.locator('#gr-start [aria-current="step"]').getAttribute('data-grs-step'), 'channel');
      assert.equal(await page.locator('#gr-start [data-grs-who]').innerText(), 'Only Ana Silva and Ben Ode can do this.');
      assert.equal(await panelButtons(page).count(), 0, 'no button');
      // An empty card's link points at the panel rather than a form they may not use.
      await page.locator('#dash-grid .grs-fill[data-grs-fill="channel"] [data-gr-step]').click();
      await page.waitForFunction(() => document.querySelector('#gr-start .grs-next')?.classList.contains('grs-flash'));
      assert.equal(await page.locator('dialog.gr-dlg[open]').count(), 0);
      await shot(page, 'desktop-growth-start-member');
      assert.deepEqual(errors, []);
      await page.close();
    }
    for (const theme of ['dark', 'light']) {
      // Computed retention on a phone: nothing past the screen's edge but the table's own scroll, shares readable.
      const {page, errors} = await open(browser, {viewport: PHONE, theme, computed: true});
      const card = page.locator('#dash-grid [data-grx]');
      await card.locator('table').waitFor();
      assert.equal(await sideways(page), false, 'no sideways scroll');
      for (const b of await card.locator('.grx-bar button').all()) {
        const box = (await b.boundingBox())!;
        assert.ok(box.x >= 0 && box.x + box.width <= PHONE.width + 1 && box.height >= 24, 'the switches fit and can be tapped');
      }
      assert.ok(await contrast(card.locator('td.grc-k').first()) >= 4.5, `a computed share reads at 4.5:1 (${theme})`);
      await card.scrollIntoViewIfNeeded();
      await shot(page, `phone-growth-computed-${theme}`);
      assert.deepEqual(errors, []);
      await page.close();
    }
    for (const theme of ['dark', 'light']) {
      // A phone: the steps stack in one column, nothing past the edge, the button in reach.
      const {page, errors} = await open(browser, {viewport: PHONE, theme, setup: setupAt(1)});
      await panel(page).locator('.grs-next').waitFor();
      const lefts = await page.locator('#gr-start .grs-step').evaluateAll(els => els.map(el => Math.round(el.getBoundingClientRect().left)));
      assert.equal(new Set(lefts).size, 1, 'one column');
      assert.equal(await sideways(page), false, 'no sideways scroll');
      const box = (await panelButtons(page).boundingBox())!;
      assert.ok(box.x >= 0 && box.x + box.width <= PHONE.width, 'the button fits');
      assert.ok(await contrast(page.locator('#gr-start .grs-next .grs-n')) >= 3, `the next step's number reads (${theme})`);
      await shot(page, `phone-growth-start-${theme}`);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('Growth cards: computed retention (cut, grain, not yet, too few, foot, trace with and without keys, upload refused and loaded, phone); head (North Star, month, board, Settings), Number/Funnel/Channels/Retention/Board renderers, month picker and address, add a Number with metric and channel, form fields, Settings and months, member, phone in both themes, contrast, old routes; Get started panel (AC-1 to AC-7) on desktop and phone');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
