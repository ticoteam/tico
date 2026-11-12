// Finance › Budgets and Sources' connect (ui/app/finance-budgets.ts, finance-admin.js) against a fake hub: Acme Lights,
// with marketing budgets per ad bot per platform under team budgets under the Marketing line. The Budgets tab sits
// after P&L for the owner and anyone holding finance.budgets, and stays hidden (even by address) from a teammate
// without it. It draws the budgets as a tree, with platform, pace (amber past the amount) and the used meter when the
// read has them and blank cells when it does not; a double-click on an amount sends budget_update from the month
// shown; + Budget sends budget_create; the month picker reads that month. "+ Source" › Mercury offers the Mercury keys
// on Tools and connects with the one picked; Stripe with none on Tools stores the pasted key on a new Stripe tool
// first; Google Ads goes to its tool page. No sideways scroll on a phone. TICO_UI_SHOTS=<dir> saves screenshots.
import type {Browser, Page, Route} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RouteSupport from './support/routes.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RouteSupport.Api;
const uiDir = path.join(__dirname, '..');
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};
// Shapes the tests read from the page and build for the fake hub.
declare const S: {me: {id: string} | null; emps?: unknown[]};
type Body = Record<string, unknown>;
type Write = [string, string, Body];
interface Hub {connFail: number; indexFail: boolean; hold: {at: string; wait: Promise<unknown>} | null}
interface MateExtra {default_team?: string; rights?: string[]; budgets?: number}
interface AdInfo {platform?: string; state?: Record<string, unknown>}
interface Viewport {width: number; height: number}

const people = [{id: 'ana', name: 'Ana Silva', role: 'owner'}, {id: 'cara', name: 'Cara Lee', role: 'member', team: 'mkt'},
  {id: 'mia', name: 'Mia Park', role: 'member', team: 'mkt'}, {id: 'ned', name: 'Ned Cole', role: 'member', team: 'mkt'}];
const emps = ([['rental-mgr', 'Rental Marketing Manager', 'rental'], ['rental-google', 'Rental Google Ad Owner', 'rental'],
  ['rental-social', 'Rental Social Ads Owner', 'rental'], ['consumer-mgr', 'Consumer Marketing Manager', 'consumer'],
  ['consumer-google', 'Consumer Google Ad Owner', 'consumer']] as [string, string, string][]).map(([name, display_name, team]) =>
  ({name, display_name, role: display_name, team, status: 'active', host: 'keeper', can_chat: true}));
const teams = [{id: 'mkt', name: 'Marketing', parent: '', lead: 'human:ana'}, {id: 'rental', name: 'Rental', parent: 'mkt', lead: 'bot:rental-mgr'},
  {id: 'consumer', name: 'Consumer', parent: 'mkt', lead: 'bot:consumer-mgr'}];
const ALL_RIGHTS = ['finance.book', 'finance.ap', 'finance.ar', 'finance.pay', 'finance.cards', 'finance.budgets', 'finance.rules', 'finance.admin'];
const mate = (teammate: string, kind: string, team: string, def: string, extra: MateExtra = {}) => ({teammate, kind, team, reports_to: '', default: def, grants: [], rights: [], ...extra});
const MATES = [mate('human:ana', 'person', '', 'everything', {rights: ALL_RIGHTS}),
  mate('human:cara', 'person', 'mkt', 'team_expenses', {default_team: 'mkt'}),
  mate('human:mia', 'person', 'mkt', 'own_expenses', {rights: ['finance.budgets']}),
  mate('human:ned', 'person', 'mkt', 'none', {budgets: 1}),
  ...emps.map(e => mate('bot:' + e.name, 'bot', e.team, 'own_expenses'))];
const access = (viewer: string) => ({teams, private_lines: [], defaults: {manager: 'team_expenses', member: 'own_expenses', manager_payroll: 'totals'}, view_version: 'v1',
  teammates: viewer === 'ana' ? MATES : MATES.filter(m => m.teammate === 'human:' + viewer), can_view_as: viewer === 'ana', can_edit_defaults: viewer === 'ana'});

const LINES: [string, string, string, number][] = [['a-6100', '6100', 'Software', 300000], ['a-6400', '6400', 'Marketing', 900000]];
const pad = (n: number) => String(n).padStart(2, '0');
const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const day = (s: string) => { const [y, m, d] = s.split('-').map(Number) as [number, number, number]; return new Date(y, m - 1, d); };
const thisMonth = iso(new Date()).slice(0, 7);
const lastMonth = (() => { const d = new Date(); return iso(new Date(d.getFullYear(), d.getMonth() - 1, 1)).slice(0, 7); })();
function columns(start: string, end: string) {
  const out = [], today = iso(new Date());
  for (let d = day(start); iso(d) <= end; d = new Date(d.getFullYear(), d.getMonth() + 1, 1)) {
    const [s, e] = [iso(new Date(d.getFullYear(), d.getMonth(), 1)), iso(new Date(d.getFullYear(), d.getMonth() + 1, 0))];
    out.push({start: s, end: e, label: s.slice(0, 7), state: e < today ? 'past' : s <= today ? 'current' : 'future'});
  }
  return out;
}
function pnl(url: URL) {
  const q = url.searchParams, cols = columns(q.get('start')!, q.get('end')!);
  const rows = LINES.map(([account_id, code, name, base]) => ({account_id, code, name, type: 'expense', section: 'operating', parent_id: null, private: false,
    actual: cols.map(c => c.state === 'future' ? null : base), total: 0, budget: null, budget_to_date: null, projection: cols.map(c => c.state === 'future' ? base : null), projection_method: 'trend'}));
  const expense = cols.map(() => rows.reduce((a, r) => a + r.actual[0]!, 0));
  return {columns: cols, rows, income: cols.map(() => 0), expense, net: expense.map(v => -v), excluded: false, start: q.get('start'), end: q.get('end'), view_version: 'v1',
    imported: [], snapshot: null, filter: {teams: [], teammates: [], who: 'both', unattributed: false}};
}
// A budget row as GET /v2/finance/budgets sends it (backend/finance/reads.py _budget_row, _state_row), with the ad
// platform fields (platform, pace_end, spent_from, as_of, reported) unless `plain`.
const budget = (id: string, name: string, amount: number | null, actual: number, extra: Body = {}, ads: AdInfo = {}) => ({id, name, period: 'month', account_id: 'a-6400', catch_all: false, team: null, teammate: null,
  vendor: null, parent_id: null, owner: 'human:ana', warn_pct: 80, at_limit: 'warn', rollover: 'none', active: true, card_limits: false, ...extra,
  state: {amount, actual, held: 0, remaining: amount == null ? null : amount - actual, pct: amount ? String(actual / amount * 100) : null, crossed_warn: false,
    at_limit: false, period_start: thisMonth + '-01', period_end: thisMonth + '-28', excluded: false, ...ads.state}, ...(ads.platform ? {platform: ads.platform} : {})});
const asOf = new Date(Date.now() - 3600e3).toISOString();
const ad = (platform: string, pace: number) => ({platform, state: {pace_end: pace, spent_from: 'mixed', as_of: asOf}});
const BUDGETS = [
  budget('b-mkt', 'Marketing', 900000, 159000, {}, {state: {pace_end: 880000}}),
  budget('b-rental', 'Rental Marketing Manager', 600000, 128000, {team: 'rental', parent_id: 'b-mkt'}, {state: {pace_end: 640000}}),
  budget('b-rental-g', 'Rental Google Ad Owner', 300000, 52000, {team: 'rental', teammate: 'bot:rental-google', vendor: 'Google Ads', parent_id: 'b-rental'}, ad('google_ads', 260000)),
  budget('b-rental-m', 'Rental Social Ads Owner', 300000, 76000, {team: 'rental', teammate: 'bot:rental-social', vendor: 'Meta Ads', parent_id: 'b-rental'}, ad('meta_ads', 380000)),
  budget('b-consumer', 'Consumer Marketing Manager', 300000, 250000, {team: 'consumer', parent_id: 'b-mkt'}),
  // Paid through an agency: Spent is the books', Google's own figure shows beside it (reported).
  budget('b-consumer-g', 'Consumer Google Ad Owner', 300000, 250000, {team: 'consumer', teammate: 'bot:consumer-google', vendor: 'Acme Agency', parent_id: 'b-consumer'},
    {platform: 'google_ads', state: {pace_end: 290000, spent_from: 'books', as_of: null, reported: 295000}}),
  budget('b-video', 'Video production', null, 0, {account_id: null, catch_all: true})];
// Ned's own budget: all GET /v2/finance/budgets gives a teammate who sees nothing else (handlers.reads_budget).
const NED = budget('b-ned', 'Trade shows', 200000, 45000, {teammate: 'human:ned', account_id: 'a-6400'});
const plainRow = (b: Body & {state: Body}) => { const {platform, ...rest} = b; const {pace_end, spent_from, as_of, reported, ...state} = b.state; return {...rest, state}; };

// Tools: one Mercury tool with a Read key and a Read and write key, one Google Ads tool; no Stripe, Brex or Meta Ads.
const TOOL_INDEX = {rows: [{id: 'api:mercury', kind: 'api', name: 'Mercury', service: 'mercury', href: '#/tools/api/mercury', status: {tone: 'ok', text: 'Working'}, who: [], who_total: 0},
  {id: 'api:gads', kind: 'api', name: 'Google Ads', service: 'google-ads', href: '#/tools/api/gads', status: {tone: 'ok', text: 'Working'}, who: [], who_total: 0}],
counts: {all: 2}, next: null};
const TOOLS: Record<string, unknown> = {mercury: {tool: {id: 'mercury', name: 'Mercury', service: 'mercury'}, keys: [
  {id: 'k-merc-r', name: 'Mercury', preview: 'sec…a1f', access_level: 'read', stored: true},
  {id: 'k-merc', name: 'Mercury', preview: 'sec…9z2', access_level: 'read_write', stored: true}]},
gads: {tool: {id: 'gads', name: 'Google Ads', service: 'google-ads'}, keys: []}};

async function open(browser: Browser, {viewport = DESKTOP as Viewport, viewer = 'ana', hash = '#/finance/budgets', plain = false, more = false} = {}) {
  const person = people.find(p => p.id === viewer)!;
  const me = {id: viewer, name: person.name, email: viewer + '@acme.example', role: person.role, company_role: person.role, cloud: true};
  const config = {company_name: 'Acme Lights'};
  // hub.connFail: connects to fail; hub.indexFail: the tool index read fails; hub.hold: {at, wait} holds that read.
  const writes: Write[] = [], reads: string[] = [];
  const hub: Hub = {connFail: 0, indexFail: false, hold: null};
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE});
  const errors: string[] = [];
  page.on('pageerror', (e: Error) => errors.push(e.message));
  await page.route('**/*', async (route: Route) => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: /\.(?:js|ts)$/.test(file) ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events') return;
    const body = method === 'GET' ? null : req.postDataJSON?.() ?? null;
    if (method !== 'GET') writes.push([method, p + url.search, body]);
    else if (p.startsWith('/api/v2/')) reads.push(p + url.search);
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/employees') return json(emps);
    if (p === '/api/humans') return json({people, org_groups: []});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/needs-you') return json({items: [], version: 'v0', count: 0, next: null});
    if (p === '/api/v2/conversations') return json({conversations: []});
    const denied = () => json({error: {code: 'denied', detail: 'Not in your view'}}, 403);
    if (p === '/api/v2/finance/access') return json(access(viewer));
    if (p === '/api/v2/finance/pnl') return json(pnl(url));
    if (p === '/api/v2/finance/budgets') {
      if (viewer === 'cara') return denied();
      if (viewer === 'ned') return json({budgets: [NED], more: false, on: url.searchParams.get('at'), excluded: false});
      if (hub.hold && url.searchParams.get('at') === hub.hold.at) await hub.hold.wait;
      return json({budgets: plain ? BUDGETS.map(plainRow) : BUDGETS, more, on: url.searchParams.get('at'), excluded: false});
    }
    if (p === '/api/v2/finance/reports/budget_vs_actual') {
      const cols = columns(url.searchParams.get('start')!, url.searchParams.get('end')!);
      return json({kind: 'budget_vs_actual', columns: cols, budgets: BUDGETS.map(b => ({id: b.id, budget: cols.map((c, i) => b.state.amount == null ? null : b.state.amount - (i < 3 ? 50000 : 0))}))});
    }
    if (p === '/api/v2/finance/sources') return viewer === 'ana' ? json({groups: []}) : denied();
    if (p === '/api/v2/finance/balance-sheet') return json({assets: [], liabilities: [], equity: []});
    if (p === '/api/v2/tool-index') return hub.indexFail ? json({error: {code: 'unavailable', detail: 'Tools are down'}}, 503) : json(TOOL_INDEX);
    if (p.startsWith('/api/v2/api-tools/') && method === 'GET') return TOOLS[p.split('/').pop()!] ? json(TOOLS[p.split('/').pop()!]) : json({error: {code: 'not_found'}}, 404);
    if (p === '/api/v2/api-tools' && method === 'POST') return json({id: body.service, name: body.name, service: body.service, base_url: 'https://api.stripe.com', auth_scheme: '', check_path: '', delivery: 'proxy'});
    if (/^\/api\/v2\/api-tools\/[^/]+\/keys$/.test(p) && method === 'POST') return json({id: 'k-' + p.split('/')[4], name: body.name, preview: 'rk_…xyz', access_level: body.access_level, stored: true});
    if (method === 'DELETE' && p.startsWith('/api/v2/api-tools/')) return json({});
    if (p === '/api/v2/finance/connections' && method === 'POST' && hub.connFail > 0) { hub.connFail--; return json({error: {code: 'invalid', detail: 'The key was refused'}}, 422); }
    if (p === '/api/v2/finance/connections' && method === 'POST') return json({action_id: 'act-c', kind: 'connection_create', status: 'committed', code: null, reason: null, approver: null, result: {}});
    if (p === '/api/v2/finance/actions' && method === 'POST' && body.dry_run) return json({status: 'committed', kind: body.kind, action_id: '', approver: null, reason: '', dry_run: true, result: {}, impact: {}});
    if (p === '/api/v2/finance/actions' && method === 'POST') return json({action_id: 'act-' + writes.length, kind: body.kind, status: 'committed', code: null, reason: null, approver: null, result: {}});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  await page.locator('#fin-head [data-fin-tab]').first().waitFor();
  return {page, errors, writes, reads, hub};
}

const tabs = (page: Page) => page.locator('#fin-head [data-fin-tab]').allInnerTexts();
const sideways = (page: Page) => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
const sent = (writes: Write[]) => writes.filter(w => w[1] === '/api/v2/finance/actions' && !w[2].dry_run).map(w => w[2]);
const row = (page: Page, id: string) => page.locator(`tr[data-fin-bud="${id}"]`);
const cell = (page: Page, id: string, n: number) => row(page, id).locator('td').nth(n);
async function settled(done: () => boolean) {
  for (let t = Date.now(); !done() && Date.now() - t < 3000;) await new Promise(r => setTimeout(r, 20));
  assert.ok(done(), 'timed out waiting');
  await new Promise(r => setTimeout(r, 100));
}
async function shot(page: Page, name: string) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  await page.evaluate(() => Promise.all(document.getAnimations().filter(a => a.effect?.getComputedTiming().endTime !== Infinity).map(a => a.finished.catch(() => {}))));
  await page.screenshot({path: path.join(dir, name + '.png'), fullPage: true});
}

(async () => {
  const browser = await chromium.launch();
  try {
    // The owner: the tree with the ad platform fields, an edit in place, + Budget, another month.
    {
      const tag = 'budgets';
      const {page, errors, writes, reads, hub} = await open(browser);
      await row(page, 'b-rental-g').waitFor();
      assert.deepEqual((await tabs(page)).slice(0, 2), ['P&L', 'Budgets'], tag + ': Budgets right after P&L');
      assert.deepEqual(await page.locator('tr[data-fin-bud] td:first-child').allInnerTexts(), ['Marketing', 'Consumer Marketing Manager', 'Consumer Google Ad Owner',
        'Rental Marketing Manager', 'Rental Google Ad Owner', 'Rental Social Ads Owner', 'Video production'], tag + ': a tree, children under their parent');
      const depth = async (id: string) => (await row(page, id).getAttribute('style'))!.match(/--d:(\d+)/)![1];
      assert.deepEqual([await depth('b-mkt'), await depth('b-rental'), await depth('b-rental-g'), await depth('b-video')], ['0', '1', '2', '1'], tag + ': indented by depth');
      assert.deepEqual(await page.locator('tr.fin-grp').allInnerTexts(), ['Other budgets'], tag + ': a budget with no line goes under Other budgets');
      assert.equal((await cell(page, 'b-rental-g', 1).innerText()).trim(), 'Google Ads', tag + ': the platform');
      assert.equal((await cell(page, 'b-rental-m', 1).innerText()).trim(), 'Meta Ads', tag + ': the platform');
      assert.equal((await cell(page, 'b-rental', 1).innerText()).trim(), 'team', tag + ': a team row says team');
      assert.equal((await cell(page, 'b-mkt', 1).innerText()).trim(), '', tag + ': the line row has no platform');
      assert.equal((await cell(page, 'b-video', 2).innerText()).trim(), 'No amount', tag + ': a budget with no amount says so');
      // Pace by month end: amber past the amount.
      assert.equal(await row(page, 'b-rental-m').locator('.pill.waiting').innerText(), '$3,800', tag + ': pace past the amount is amber');
      assert.equal(await row(page, 'b-rental-g').locator('.pill').count(), 0, tag + ': pace under the amount is plain');
      assert.equal((await cell(page, 'b-rental-g', 5).innerText()).trim(), '$2,600', tag + ': the pace');
      assert.equal(await row(page, 'b-rental').locator('.pill.waiting').innerText(), '$6,400', tag + ': a team row\'s pace, amber past its amount');
      assert.equal((await cell(page, 'b-mkt', 5).innerText()).trim(), '$8,800', tag + ': a line row\'s pace');
      assert.match(await cell(page, 'b-rental-g', 3).getAttribute('title'), /^Platform data as of /, tag + ': Spent says when the platform data is from');
      assert.equal(await row(page, 'b-rental-g').locator('[data-fin-bud-reported]').count(), 0, tag + ': a swapped budget reports nothing beside Spent');
      const reported = row(page, 'b-consumer-g').locator('[data-fin-bud-reported]');
      assert.equal((await reported.innerText()).trim(), 'Platform $2,950', tag + ': the platform\'s figure under Spent');
      assert.equal(await reported.getAttribute('title'), 'Reported by Google Ads, not in Spent', tag + ': and that it is not in Spent');
      assert.equal(await cell(page, 'b-consumer-g', 3).getAttribute('title'), null, tag + ': Spent is the books\', no platform time');
      // Used: green, amber at warn, red at the amount.
      assert.equal(await row(page, 'b-consumer-g').locator('.fin-bar-m.warn').count(), 1, tag + ': used past warn at is amber');
      assert.equal(await row(page, 'b-rental-g').locator('.fin-bar-m:not(.warn):not(.bad)').count(), 1, tag + ': used under warn at is green');
      assert.equal((await cell(page, 'b-rental-g', 6).innerText()).trim(), '80%', tag + ': warn at on a leaf');
      assert.equal((await cell(page, 'b-mkt', 6).innerText()).trim(), '', tag + ': no warn at on a row with children');
      assert.equal((await cell(page, 'b-video', 6).innerText()).trim(), '', tag + ': nor on one with no amount');
      // Search keeps the matches and the budgets above them.
      await page.locator('[data-fin-q]').fill('social');
      assert.deepEqual(await page.locator('tr[data-fin-bud] td:first-child').allInnerTexts(), ['Marketing', 'Rental Marketing Manager', 'Rental Social Ads Owner'], tag + ': search keeps the path');
      assert.equal(await page.locator('tr.fin-grp').count(), 0, tag + ': an empty group is left out');
      await page.locator('[data-fin-q]').fill('');
      // A row opens the rail, with its amount by month.
      await cell(page, 'b-rental-g', 0).click();
      await page.locator('#fin-rail h2', {hasText: 'Rental Google Ad Owner'}).waitFor();
      await page.locator('#fin-rail [data-fin-bud-hist] .fin-kv').first().waitFor();
      const rail = await page.locator('#fin-rail').innerText();
      for (const want of ['Warn at', 'At limit', 'Owner', 'Ana Silva', 'Google Ads']) assert.ok(rail.includes(want), `${tag}: the rail has ${want}`);
      assert.equal(await page.locator('#fin-rail [data-fin-bud-hist] .fin-kv').count(), 6, tag + ': six months of amounts');
      assert.ok(reads.some(r => r.startsWith('/api/v2/finance/reports/budget_vs_actual?period=month&')), tag + ': the history is the budget vs actual report');
      // Double-click the amount, type, Enter: budget_update from this month.
      await cell(page, 'b-rental-g', 2).dblclick();
      await page.locator('.fin-cell-in').fill('3500');
      await page.locator('.fin-cell-in').press('Enter');
      await settled(() => sent(writes).length === 1);
      assert.deepEqual(sent(writes)[0], {kind: 'budget_update', payload: {budget_id: 'b-rental-g', amount: 350000, effective_from: thisMonth + '-01'}}, tag + ': the edit is sent');
      assert.equal(writes.filter(w => w[1] === '/api/v2/finance/actions')[0]![2].dry_run, true, tag + ': a dry run first');
      // Esc puts it back and sends nothing.
      await row(page, 'b-rental-m').waitFor();
      await cell(page, 'b-rental-m', 2).dblclick();
      await page.locator('.fin-cell-in').fill('9');
      await page.locator('.fin-cell-in').press('Escape');
      await page.waitForFunction(() => !document.querySelector('.fin-cell-in'));
      assert.equal(sent(writes).length, 1, tag + ': Esc sends nothing');
      await shot(page, 'budgets-desktop');
      // + Budget: a line, under its budget, for a teammate, a vendor, amount and warn at.
      await page.locator('[data-fin-bud-add]').click();
      const dlg = page.locator('dialog.fin-dlg');
      await dlg.locator('select[name="account_id"]').selectOption('a-6400');
      assert.equal(await dlg.locator('select[name="parent_id"]').inputValue(), 'b-mkt', tag + ': Under defaults to the line\'s budget');
      await dlg.locator('select[name="teammate"]').selectOption('bot:consumer-google');
      await dlg.locator('input[name="vendor"]').fill('Meta Ads');
      await dlg.locator('input[name="amount"]').fill('1,200');
      await dlg.locator('input[name="warn_pct"]').fill('90');
      await shot(page, 'budgets-add-desktop');
      await dlg.locator('[type=submit]').click();
      await dlg.waitFor({state: 'detached'});
      assert.deepEqual(sent(writes).at(-1), {kind: 'budget_create', payload: {name: 'Consumer Google Ad Owner', period: 'month', account_id: 'a-6400', amount: 120000,
        warn_pct: 90, effective_from: thisMonth + '-01', parent_id: 'b-mkt', teammate: 'bot:consumer-google', vendor: 'Meta Ads'}}, tag + ': + Budget sends budget_create');
      // Another month: the table empties at once, read as of its first day; a past month's amounts don't edit.
      const month = async (m: string) => { await page.locator('[data-fin-month]').fill(m); await page.locator('[data-fin-month]').dispatchEvent('change'); };
      let release!: (value?: unknown) => void;
      hub.hold = {at: lastMonth + '-01', wait: new Promise(r => { release = r; })};
      await month(lastMonth);
      await settled(() => reads.includes(`/api/v2/finance/budgets?at=${lastMonth}-01&limit=500`));
      assert.equal(await page.locator('tr[data-fin-bud]').count(), 0, tag + ': the old month\'s rows go while the new one loads');
      assert.equal(await page.locator('#fin-rail').innerText(), '', tag + ': and its rail');
      // Back to this month before last month's read is in: the late answer doesn't replace this month's rows.
      await month(thisMonth);
      await page.locator('td[data-fin-bud-amt]').first().waitFor();
      release();
      await new Promise(r => setTimeout(r, 300));
      assert.ok(await page.locator('td[data-fin-bud-amt]').count() > 0, tag + ': a late answer for an older month is dropped');
      hub.hold = null;
      await month(lastMonth);
      await page.waitForFunction(() => document.querySelector('tr[data-fin-bud]') && !document.querySelector('td[data-fin-bud-amt]'));
      assert.equal(await page.locator('td[data-fin-bud-amt]').count(), 0, tag + ': a past month is read only');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A read without the ad platform fields (main before the ad spend import): those cells are blank.
    {
      const tag = 'without platform fields';
      const {page, errors} = await open(browser, {plain: true, more: true});
      await row(page, 'b-rental-g').waitFor();
      assert.equal(await page.locator('#fin-bud .fin-note').innerText(), 'Showing the first 500', tag + ': a read cut at 500 says so');
      assert.equal(await page.locator('#fin-bud .pill').count(), 0, tag + ': no pace pills');
      assert.equal((await cell(page, 'b-rental-g', 1).innerText()).trim(), '', tag + ': no platform');
      assert.equal((await cell(page, 'b-rental', 1).innerText()).trim(), 'team', tag + ': a team row still says team');
      assert.equal((await cell(page, 'b-rental-g', 5).innerText()).trim(), '', tag + ': no pace');
      assert.equal(await cell(page, 'b-rental-g', 3).getAttribute('title'), null, tag + ': no platform time');
      assert.equal(await page.locator('[data-fin-bud-reported]').count(), 0, tag + ': no platform figure beside Spent');
      assert.equal(await page.locator('td[data-fin-bud-amt]').count() > 0, true, tag + ': amounts still edit');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // Without finance.budgets: no tab, and the address goes to the P&L. With it (not the owner): the tab and + Budget.
    {
      const tag = 'no budgets right';
      const {page, errors, reads} = await open(browser, {viewer: 'cara'});
      await page.waitForFunction(() => location.hash.startsWith('#/finance/pnl'));
      await page.locator('#fin-pl').waitFor();
      assert.ok(!(await tabs(page)).includes('Budgets'), tag + ': no Budgets tab');
      assert.ok(!reads.some(r => r.startsWith('/api/v2/finance/budgets')), tag + ': budgets never read');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A teammate who sees nothing else in Finance but has a budget of their own: My budget alone, read only.
    {
      const tag = 'my budget';
      const {page, errors, reads, writes} = await open(browser, {viewer: 'ned'});
      await page.waitForFunction(() => location.hash.startsWith('#/finance/mine'));
      await row(page, 'b-ned').waitFor();
      assert.deepEqual(await tabs(page), ['My budget'], tag + ': the only tab');
      assert.deepEqual(await page.locator('tr[data-fin-bud] td:first-child').allInnerTexts(), ['Trade shows'], tag + ': their own budget');
      assert.equal(await page.locator('tr.fin-grp').count(), 0, tag + ': no groups');
      assert.equal(await page.locator('[data-fin-bud-add], [data-fin-q], td[data-fin-bud-amt]').count(), 0, tag + ': no + Budget, search or edits');
      assert.ok(!reads.some(r => r.startsWith('/api/v2/finance/pnl')), tag + ': no P&L read');
      await row(page, 'b-ned').click();
      await page.locator('#fin-rail h2').waitFor();
      assert.match(await page.locator('#fin-rail').innerText(), /Trade shows[\s\S]*Left\s+\$1,550/, tag + ': details in the rail');
      await page.evaluate(() => { location.hash = '#/finance/pnl'; });
      await page.waitForFunction(() => location.hash.startsWith('#/finance/mine'));
      assert.deepEqual(writes, [], tag + ': nothing written');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    {
      const tag = 'budgets right';
      const {page, errors} = await open(browser, {viewer: 'mia'});
      await row(page, 'b-mkt').waitFor();
      assert.deepEqual((await tabs(page)).slice(0, 2), ['P&L', 'Budgets'], tag + ': the Budgets tab');
      assert.equal(await page.locator('[data-fin-bud-add]').count(), 1, tag + ': + Budget');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A phone: the table scrolls inside its box, the page does not.
    {
      const tag = 'phone';
      const {page, errors} = await open(browser, {viewport: PHONE});
      await row(page, 'b-rental-g').waitFor();
      assert.equal(await sideways(page), false, tag + ': no sideways scroll');
      await cell(page, 'b-rental-g', 0).click();
      await page.locator('#fin-rail h2').waitFor();
      assert.equal(await sideways(page), false, tag + ': none with the rail open');
      await shot(page, 'budgets-phone');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // Sources: a Tools key for Mercury (or a new one pasted), a pasted Stripe key stored on Tools and taken off again
    // when the connect fails, Brex when Tools can't be read and then with a pasted key, Google Ads to its tool page.
    {
      const tag = 'sources';
      const {page, errors, writes, reads, hub} = await open(browser, {hash: '#/finance/sources'});
      const pick = async (name: string) => {
        await page.locator('[data-fin-connect]').click();
        await page.locator(`#fin-connect [data-fin-connect-item="${name}"]`).click();
      };
      const indexReads = () => reads.filter(r => r === '/api/v2/tool-index').length;
      await page.locator('[data-fin-connect]').click();
      assert.deepEqual(await page.locator('#fin-connect [role=menuitem]').allInnerTexts(), ['Mercury', 'Brex', 'Stripe', 'QuickBooks', 'Xero', 'Google Ads', 'Meta Ads'], tag + ': the menu');
      await page.locator('[data-fin-connect]').click();
      await pick('mercury');
      const dlg = page.locator('dialog.fin-dlg');
      await dlg.locator('input[name=key]').first().waitFor();
      assert.equal(await dlg.locator('legend').innerText(), 'Use the Mercury key from Tools', tag + ': offers the Tools keys');
      assert.deepEqual(await dlg.locator('label.fin-check').allInnerTexts(), ['Mercury · Read · ••••a1f', 'Mercury · Read and write · ••••9z2', 'Paste a new key'], tag + ': one radio per key, then paste');
      assert.equal(await dlg.locator('input[name=token]').isVisible(), false, tag + ': the paste field waits for its radio');
      assert.equal(await dlg.locator('input[name=key]:checked').getAttribute('value'), 'k-merc', tag + ': the Read and write key is picked first');
      await dlg.locator('input[name=key][value=""]').check();
      assert.equal(await dlg.locator('input[name=token]').isVisible(), true, tag + ': Paste a new key shows the field');
      assert.equal(await dlg.locator('select[name=level]').inputValue(), 'read_write', tag + ': Mercury pastes start Read and write');
      await shot(page, 'sources-connect-desktop');
      await dlg.locator('input[name=key][value="k-merc-r"]').check();
      assert.equal(await dlg.locator('input[name=token]').isVisible(), false, tag + ': a key hides it again');
      await dlg.locator('input[name=key][value="k-merc"]').check();
      await dlg.locator('[type=submit]').click();
      await dlg.waitFor({state: 'detached'});
      const posts = writes.filter(w => w[0] === 'POST');
      assert.deepEqual(posts.at(-1)!.slice(1), ['/api/v2/finance/connections', {provider: 'mercury', credential_id: 'k-merc', name: 'Mercury'}], tag + ': connects with the key picked');
      assert.equal(posts.some(w => w[1].startsWith('/api/v2/credentials') || w[1].startsWith('/api/v2/api-tools')), false, tag + ': nothing stored');
      // Stripe has no tool: paste only, Read by default. The first connect fails: the key and its new tool go again.
      const before = writes.length;
      hub.connFail = 1;
      await pick('stripe');
      await dlg.locator('input[name=token]').fill('rk_live_abcdefghijklmnop');
      assert.equal(await dlg.locator('input[name=key]').count(), 0, tag + ': no radios with no key on Tools');
      assert.equal(await dlg.locator('select[name=level]').inputValue(), 'read', tag + ': Stripe pastes start Read');
      const index1 = indexReads();
      await dlg.locator('[type=submit]').click();
      await dlg.locator('[data-error]:not([hidden])').waitFor();
      assert.deepEqual(writes.slice(before).map(w => w[0] + ' ' + w[1]), ['POST /api/v2/api-tools', 'POST /api/v2/api-tools/stripe/keys', 'POST /api/v2/finance/connections',
        'DELETE /api/v2/api-tools/stripe/keys/k-stripe', 'DELETE /api/v2/api-tools/stripe'], tag + ': a failed connect takes the key and its tool off Tools');
      assert.ok(indexReads() > index1, tag + ': Tools read again before storing');
      assert.equal(writes.find(w => w[1] === '/api/v2/api-tools/stripe/keys')![2].access_level, 'read', tag + ': stored at the level picked');
      // The retry reads Tools again and stores it afresh.
      const retry = writes.length, index2 = indexReads();
      await dlg.locator('[type=submit]').click();
      await dlg.waitFor({state: 'detached'});
      assert.ok(indexReads() > index2, tag + ': a retry reads Tools again');
      assert.deepEqual(writes.slice(retry).map(w => w[0] + ' ' + w[1]), ['POST /api/v2/api-tools', 'POST /api/v2/api-tools/stripe/keys', 'POST /api/v2/finance/connections'], tag + ': stored again, then connected');
      assert.deepEqual(writes.find(w => w[1] === '/api/v2/api-tools')![2], {name: 'Stripe', service: 'stripe'}, tag + ': a Stripe tool');
      assert.deepEqual(writes.at(-1)![2], {provider: 'stripe', credential_id: 'k-stripe', name: 'Stripe'}, tag + ': connects with the new key');
      assert.deepEqual(errors, [], tag + ': no page errors');
      // Google Ads: its tool page.
      await pick('google-ads');
      await page.waitForFunction(() => location.hash === '#/tools/api/gads');
      await page.close();
    }
    {
      const {page} = await open(browser, {hash: '#/finance/sources'});
      await page.locator('[data-fin-connect]').click();
      await page.locator('#fin-connect [data-fin-connect-item="meta-ads"]').click();
      await page.waitForFunction(() => location.hash === '#/tools');
      await page.close();
    }
    console.log('finance-budgets: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
