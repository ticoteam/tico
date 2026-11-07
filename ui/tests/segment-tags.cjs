// Segments on the surfaces that carry them (ui/app/segment-tags.ts) against a fake hub: Acme Lights, with Ana as the
// owner and Cara as a teammate. The org chart: an owner's card Segments button (on hover) and its menu, the All label on
// the card, a teammate's tags with no menu. A teammate's page (About card) and a bot's Info card: the owner's Edit writes
// the tags. Goals: the Segment filter (its name on a direct entry, a phone's icon only, the context goals muted), and the
// goal dialog's Segment. Tasks: the Segment filter chip, a task's Segment property saved with its version. Growth: its
// Segment filter, with a pinned card. Finance: the budgets' Segment filter, a budget's Segment in the rail (Edit saves
// budget_update from this month, an inherited one reads muted), + Budget with a segment; the P&L's By segment view (year
// to date, no Period), the probe (a 403 hides it), a column's drill into the company P&L (the address names the segment),
// Unallocated's label, a segment lead's one column (their own P&L read carries segment=), and a line split by segment,
// with and without the server's echo. Every surface is checked for no sideways scroll on a phone, and a company with
// no segments shows no segment UI. TICO_UI_SHOTS=<dir> saves <surface>-<light|dark>-<desktop|phone>.png for each surface
// at both sizes and both themes.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const uiDir = path.join(__dirname, '..');
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};
const org = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'redesign-org.json'), 'utf8'));
// The fixture's bots are keyed by name; the employee list the page reads carries an id per bot.
const EMPS = org.employees.map(e => ({...e, id: e.name, slug: e.name}));

const ALL_RIGHTS = ['finance.book', 'finance.ap', 'finance.ar', 'finance.pay', 'finance.cards', 'finance.budgets', 'finance.rules', 'finance.admin'];
// GET /api/v2/segments (backend/segments.py _segment_view): the list order is the rank order.
const SEGMENTS = [{id: 'seg-rental', name: 'Rental', who: 'Rental managers and ad owners', color: 'blue', rank: 1, faces: [], teammates: 2},
  {id: 'seg-consumer', name: 'Consumer', who: 'Consumer marketing', color: 'green', rank: 2, faces: [], teammates: 1},
  {id: 'seg-trade', name: 'Trade shows', who: 'Trade show budget', color: 'amber', rank: 3, faces: [], teammates: 0}];
// GET /api/v2/segments/members: who is tagged, with which segments.
const MEMBERS = [{member: 'bot:builder', segments: ['seg-rental']}, {member: 'bot:sales-lead', segments: ['seg-rental']},
  {member: 'person:cara', segments: ['seg-consumer']}];
const GOALS = [{id: 'g-co', title: 'Reach 100 paying customers', owner_id: 'company', parent_id: null, status: 'green', status_source: 'auto', rank: 1, kpis: []},
  {id: 'g-rental', title: 'Book 40 rental demos a month', owner_id: 'bot:sales-lead', parent_id: 'g-co', status: 'yellow', status_source: 'auto', rank: 1, kpis: [], segment_id: 'seg-rental'}];
const mkTask = (id, title, segment_id) => ({id, title, body: 'Details.', owner_id: 'bot:builder', requester_id: 'human:ana', status: 'doing', lane: 'company',
  rank: null, labels: [], tags: [], links: [], parts: {total: 0, done: 0}, version: 3, segment_id, relations: {},
  created_at: new Date(Date.now() - 7200e3).toISOString(), updated_at: new Date(Date.now() - 3600e3).toISOString()});
const TASKS = [mkTask('t-rent-1', 'Draft the rental offer page', 'seg-rental'), mkTask('t-rent-2', 'Pull rental lease counts', 'seg-rental'),
  mkTask('t-cons-1', 'Write the consumer FAQ', 'seg-consumer'), mkTask('t-plain', 'Renew the domain', null)];

// GET /api/v2/places/growth: one pinned Number, so the page has content (not "Nothing pinned").
const GROWTH_MONTH = new Date().toISOString().slice(0, 7);
const GROWTH = {place: 'growth', kind: 'growth', saved: true, can_edit: false, month: GROWTH_MONTH, months: [GROWTH_MONTH],
  pins: [{id: 'p-new', item: 'card:c-new', position: 0, size: 'small', made_by: null, card: {id: 'c-new', template: 'growth_number',
    settings: {metric: 'new_customers'}, name: 'Number', heading: 'New customers', title: '', team_name: '', hidden: false,
    data: {label: 'New customers', metric: 'new_customers', format: 'count', value: 42, month: GROWTH_MONTH, series: [{month: GROWTH_MONTH, value: 42}]}}}]};

// Finance: the same month and cents as ui/tests/finance-budgets.cjs.
const pad = n => String(n).padStart(2, '0');
const iso = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const day = s => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
const thisMonth = iso(new Date()).slice(0, 7);
function columns(start, end) {
  const out = [], today = iso(new Date());
  for (let d = day(start); iso(d) <= end; d = new Date(d.getFullYear(), d.getMonth() + 1, 1)) {
    const [s, e] = [iso(new Date(d.getFullYear(), d.getMonth(), 1)), iso(new Date(d.getFullYear(), d.getMonth() + 1, 0))];
    out.push({start: s, end: e, label: s.slice(0, 7), state: e < today ? 'past' : s <= today ? 'current' : 'future'});
  }
  return out;
}
const LINES = [['a-6400', '6400', 'Marketing', 900000], ['a-6100', '6100', 'Software', 300000]];
function pnl(url) {
  const q = url.searchParams, cols = columns(q.get('start'), q.get('end'));
  const rows = LINES.map(([account_id, code, name, base]) => ({account_id, code, name, type: 'expense', section: 'operating', parent_id: null, private: false,
    actual: cols.map(c => c.state === 'future' ? null : base), total: 0, budget: null, budget_to_date: null,
    projection: cols.map(c => c.state === 'future' ? base : null), projection_method: 'trend'}));
  const expense = cols.map(() => rows.reduce((a, r) => a + r.actual[0], 0));
  return {columns: cols, rows, income: cols.map(() => 0), expense, net: expense.map(v => -v), excluded: false, start: q.get('start'), end: q.get('end'),
    view_version: 'v1', imported: [], snapshot: null, filter: {teams: [], teammates: [], who: 'both', unattributed: false}};
}
// GET /api/v2/finance/reports/pnl_by?by=segment: a column per segment, Unallocated and Total, in cents. The Total is the
// company P&L's expense over the same window: one month's lines (LINES) for each month that has happened. A lead gets
// only the column they lead, with no Unallocated and no Total (backend/finance/reads.py).
const MONTH_COST = 900000 + 300000;
function pnlBy(url, lead) {
  const q = url.searchParams, cols = columns(q.get('start'), q.get('end'));
  const total = cols.filter(c => c.state !== 'future').length * MONTH_COST;
  const rental = Math.round(total * 0.6), consumer = Math.round(total * 0.25), unallocated = total - rental - consumer;
  const row = (key, label, values) => ({key, label, values});
  if (lead) return {excluded: false, columns: [{key: 'seg-consumer', label: 'Consumer', color: 'green'}],
    rows: [row('revenue', 'Revenue', {'seg-consumer': 0}), row('costs', 'Costs', {'seg-consumer': consumer}), row('contribution', 'Contribution', {'seg-consumer': -consumer})]};
  return {excluded: false, columns: [{key: 'seg-rental', label: 'Rental', color: 'blue'}, {key: 'seg-consumer', label: 'Consumer', color: 'green'},
    {key: 'unallocated', label: 'Unallocated'}, {key: 'total', label: 'Total'}],
  rows: [row('revenue', 'Revenue', {'seg-rental': 0, 'seg-consumer': 0, unallocated: 0, total: 0}),
    row('costs', 'Costs', {'seg-rental': rental, 'seg-consumer': consumer, unallocated, total}),
    row('contribution', 'Contribution', {'seg-rental': -rental, 'seg-consumer': -consumer, unallocated: -unallocated, total: -total})]};
}
const budget = (id, name, amount, actual, extra = {}) => ({id, name, period: 'month', account_id: 'a-6400', catch_all: false, team: null, teammate: null,
  vendor: null, parent_id: null, owner: 'human:ana', warn_pct: 80, at_limit: 'warn', rollover: 'none', active: true, card_limits: false, segment_id: null, ...extra,
  // What the server sends (backend/finance/reads.py): the segment the spend reports under, the parent's when a budget has none.
  effective_segment_id: 'effective_segment_id' in extra ? extra.effective_segment_id : (extra.segment_id ?? null),
  state: {amount, actual, held: 0, remaining: amount - actual, pct: String(actual / amount * 100), crossed_warn: false, at_limit: false,
    period_start: thisMonth + '-01', period_end: thisMonth + '-28', excluded: false}});
const BUDGETS = [budget('b-mkt', 'Marketing', 900000, 159000),
  budget('b-rental', 'Rental Marketing Manager', 600000, 128000, {team: 'rental', parent_id: 'b-mkt', segment_id: 'seg-rental'}),
  budget('b-rental-g', 'Rental Google Ad Owner', 300000, 52000, {team: 'rental', vendor: 'Google Ads', parent_id: 'b-rental', segment_id: 'seg-rental'}),
  budget('b-consumer', 'Consumer Marketing Manager', 300000, 250000, {team: 'consumer', parent_id: 'b-mkt', segment_id: 'seg-consumer'}),
  budget('b-rental-inh', 'Rental Sales Child', 100000, 0, {team: 'rental', parent_id: 'b-rental', segment_id: null, effective_segment_id: 'seg-rental'})];
const ACCESS = {teams: [], private_lines: [], defaults: {manager: 'team_expenses', member: 'own_expenses', manager_payroll: 'totals'}, view_version: 'v1',
  teammates: [{teammate: 'human:ana', kind: 'person', team: '', reports_to: '', default: 'everything', grants: [], rights: ALL_RIGHTS, may_grant: true, budgets: 0, segments_led: []}],
  can_view_as: true, can_edit_defaults: true};
// Cara leads Consumer and has no other Finance access (backend/finance/tab.py: segments_led on her teammate row).
const LEAD_ACCESS = {...ACCESS, can_view_as: false, teammates: [{teammate: 'human:cara', kind: 'person', team: '', reports_to: '', default: 'none', grants: [],
  rights: [], may_grant: false, budgets: 0, segments_led: ['seg-consumer']}]};
// GET /api/v2/finance/lines/<id>: the vendors of the line; the segment is echoed only when the server splits it.
const lineRead = (url, echo) => {
  const seg = url.searchParams.get('segment') || '';
  return {account: {id: 'a-6400', name: 'Marketing', code: '6400', type: 'expense'}, ...(echo && seg ? {segment: seg} : {}),
    by_vendor: [{key: 'Google Ads', label: 'Google Ads', amount: 5200000}, {key: 'Meta Ads', label: 'Meta Ads', amount: 7600000}],
    by_team: [], by_teammate: [], entries: [], paid_by: [], next_month: null};
};

// The last page open() made, for a failing scenario's report.
let LAST = null;
// What a failing scenario left on screen, for the run's log: the address, the open dialog, the toasts, the last writes
// and reads, the page's errors and console errors, and a screenshot in TICO_UI_SHOTS.
async function report(what) {
  if (!LAST) return '';
  const {page, errors, hub, consoleLines} = LAST;
  const seen = await page.evaluate(() => ({
    hash: location.hash,
    dialog: document.querySelector('dialog[open]')?.innerText.slice(0, 200) ?? null,
    toasts: [...document.querySelectorAll('.toast')].map(t => t.textContent).slice(-3),
    main: (document.querySelector('#main')?.innerText || '').slice(0, 200),
  })).catch(e => ({unreadable: String(e.message).split('\n')[0]}));
  const dir = process.env.TICO_UI_SHOTS;
  if (dir) {
    fs.mkdirSync(dir, {recursive: true});
    await page.screenshot({path: path.join(dir, 'failed-' + what.replace(/[^a-z0-9]+/gi, '-').slice(0, 50) + '.png'), fullPage: true}).catch(() => {});
  }
  return ' | ' + JSON.stringify({seen, writes: hub.writes.slice(-3).map(w => `${w.method} ${w.path} ${JSON.stringify(w.body)}`),
    reads: hub.reads.slice(-4), errors: errors.slice(0, 2), console: consoleLines.slice(-3)});
}

// One page on the fake hub. viewer: who is signed in (ana is the owner; cara a member). segmentsOk: false answers the
// segment P&L probe with a 403. echo: false leaves the segment out of a line's answer.
async function open(browser, {viewport = DESKTOP, viewer = 'ana', theme = 'dark', hash = '#/strategy/org', segmentsOk = true, echo = true,
  lead = false, noSegments = false} = {}) {
  const me = viewer === org.me.id ? org.me
    : {id: viewer, name: org.people.find(p => p.id === viewer).name, email: viewer + '@acme.example', role: 'member', company_role: 'member', cloud: true};
  const config = {company_name: 'Acme Lights'};
  const hub = {writes: [], reads: [], members: noSegments ? [] : MEMBERS.map(m => ({...m, segments: [...m.segments]})), tasks: TASKS.map(t => ({...t})),
    goals: GOALS.map(g => ({...g}))};
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE});
  await page.addInitScript(t => { try { localStorage.setItem('tico.theme', t); } catch {} }, theme);
  const errors = [], consoleLines = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') consoleLines.push(m.text().slice(0, 160)); });
  LAST = {page, errors, hub, consoleLines};
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (answer, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(answer)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events') return;
    const body = method === 'GET' ? null : req.postDataJSON?.() ?? null;
    if (method === 'GET') { if (p.startsWith('/api/v2/')) hub.reads.push(p + url.search); }
    else hub.writes.push({method, path: p, body});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/employees') return json(EMPS);
    if (p === '/api/humans') return json({people: org.people, org_groups: org.org_groups});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/needs-you') return json({items: [], version: 'v0', count: 0, next: null});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p.startsWith('/api/v2/preferences/')) return json({key: p.split('/').pop(), value: null});
    // Segments: the list, and the teammates' tags (a POST replaces one teammate's list and answers what was saved).
    if (p === '/api/v2/segments') return json({segments: noSegments ? [] : SEGMENTS});
    if (p === '/api/v2/segments/members' && method === 'POST') {
      const row = hub.members.find(m => m.member === body.member);
      if (row) row.segments = body.segments; else hub.members.push({member: body.member, segments: body.segments});
      return json({member: body.member, segments: body.segments});
    }
    if (p === '/api/v2/segments/members') return json({members: noSegments ? [] : hub.members});
    // The org chart and Strategy's Goals.
    if (p === '/api/v2/groups') return json(org.groups);
    if (p === '/api/v2/strategy') return json({current: null, bets: [], may_edit: true});
    if (p === '/api/v2/goals/tree') {
      // A segment's tree keeps its goals and the parents they sit under, which the server marks as context.
      const seg = url.searchParams.get('segment') || '';
      const kept = seg ? hub.goals.filter(g => g.segment_id === seg) : hub.goals;
      const parents = seg ? hub.goals.filter(g => !kept.includes(g) && kept.some(k => k.parent_id === g.id)).map(g => ({...g, context: true})) : [];
      return json({goals: [...kept, ...parents], owners: {}, other_kpis: [], proposals: []});
    }
    if (p === '/api/v2/goals/needs-you') return json({items: []});
    if (p === '/api/v2/goals' && method === 'POST') {
      const g = {id: 'g-new', title: body.title, owner_id: body.owner, parent_id: null, status: 'gray', rank: 9, kpis: [], segment_id: body.segment_id || null};
      hub.goals.push(g);
      return json({goal: g});
    }
    // Tasks: the list, the labels and types, and one task (read for its peek, saved with its version).
    if (p === '/api/v2/tasks' && method === 'GET') return json({tasks: hub.tasks, next_offset: null});
    if (p === '/api/v2/tasks/labels') return json({labels: [], tags: []});
    if (p === '/api/v2/task-types') return json({types: []});
    const tm = p.match(/^\/api\/v2\/tasks\/([^/]+)(\/tree)?$/), row = tm && hub.tasks.find(t => t.id === decodeURIComponent(tm[1]));
    if (tm?.[2]) return json([]);
    if (row && method === 'GET') return json({task: row});
    if (row && method === 'POST') {
      if ('segment_id' in body) row.segment_id = body.segment_id || null;
      row.version += 1;
      return json({task: row});
    }
    // Finance.
    if (p === '/api/v2/finance/access') return json(lead ? LEAD_ACCESS : ACCESS);
    if (p === '/api/v2/places/growth') return json(GROWTH);
    if (p === '/api/v2/finance/pnl') return json(pnl(url));
    if (p === '/api/v2/finance/budgets') {
      const seg = url.searchParams.get('segment') || '';
      return json({budgets: BUDGETS.filter(b => !seg || b.segment_id === seg || b.id === 'b-mkt'), more: false, on: url.searchParams.get('at'), excluded: false});
    }
    if (p === '/api/v2/finance/reports/budget_vs_actual') {
      const s = url.searchParams.get('start'), z = url.searchParams.get('end'), cols = s && z ? columns(s, z) : [];
      return json({kind: 'budget_vs_actual', columns: cols, budgets: BUDGETS.map(b => ({id: b.id, budget: cols.map(() => b.state.amount)}))});
    }
    if (p === '/api/v2/finance/reports/pnl_by') return segmentsOk ? json(pnlBy(url, lead)) : json({error: {code: 'denied', detail: 'Not in your view'}}, 403);
    if (p.startsWith('/api/v2/finance/lines/')) return json(lineRead(url, echo));
    if (p === '/api/v2/finance/actions' && method === 'POST' && body.dry_run) return json({status: 'committed', kind: body.kind, action_id: '', approver: null, reason: '', dry_run: true, result: {}, impact: {}});
    if (p === '/api/v2/finance/actions' && method === 'POST') return json({action_id: 'act-' + hub.writes.length, kind: body.kind, status: 'committed', code: null, reason: null, approver: null, result: {}});
    if (p === '/api/v2/finance/sources') return json({groups: []});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  return {page, errors, hub};
}

// The finance actions the page sent (not the dry runs).
const sent = hub => hub.writes.filter(w => w.path === '/api/v2/finance/actions' && !w.body?.dry_run).map(w => w.body);
const taskKeys = page => page.locator('#task-body [data-task-key]').evaluateAll(els => els.map(el => el.dataset.taskKey).sort());
const sideways = page => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
async function until(cond, ms = 3000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (await cond()) return true; await new Promise(r => setTimeout(r, 25)); }
  return cond();
}
async function shot(page, name) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  await page.evaluate(() => Promise.all(document.getAnimations().filter(a => a.effect?.getComputedTiming().endTime !== Infinity).map(a => a.finished.catch(() => {}))));
  await page.screenshot({path: path.join(dir, name + '.png'), fullPage: true});
}
// A surface in the sizes it is checked at: dark on a phone always (the no-sideways-scroll check), and with
// TICO_UI_SHOTS every size in both themes. The theme is the one the page was asked for.
async function sizes(browser, name, hash, ready, opts = {}) {
  const combos = process.env.TICO_UI_SHOTS ? [['light', DESKTOP, 'desktop'], ['dark', DESKTOP, 'desktop'], ['light', PHONE, 'phone'], ['dark', PHONE, 'phone']]
    : [['dark', PHONE, 'phone']];
  for (const [theme, viewport, size] of combos) {
    const {page, errors} = await open(browser, {...opts, hash, theme, viewport});
    await ready(page);
    assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), theme, `${name}: the ${theme} theme`);
    if (size === 'phone') assert.equal(await sideways(page), false, `${name}: no sideways scroll on a phone`);
    await shot(page, `${name}-${theme}-${size}`);
    assert.deepEqual(errors, [], `${name} ${theme} ${size}: no page errors`);
    await page.close();
  }
}

// The org chart's Segments menu for a card: the card's button (it shows on hover), or on a touch screen the sheet's row.
async function openOrgMenu(page, ref) {
  // A menu left open by the last pick can cover the card, so Escape closes it first.
  if (await page.locator('.seg-menu').count()) await page.keyboard.press('Escape');
  if (await page.evaluate(() => matchMedia('(hover: none)').matches)) {
    await page.locator(`.rdo-card[data-rdo-ref="${ref}"]`).click();
    await page.locator('dialog.rdo-dialog[open] [data-seg-tags]').click();
  } else {
    await page.locator(`.rdo-card[data-rdo-ref="${ref}"]`).hover();
    await page.locator(`[data-rdo-seg="${ref}"]`).click();
  }
  await page.locator('.seg-menu').waitFor();
}

(async () => {
  const browser = await chromium.launch();
  const failed = [];
  // A failing scenario is named in the list the run ends with; the others still run.
  const run = async (what, fn) => {
    LAST = null;
    try { await fn(); } catch (e) { failed.push(`${what}: ${String(e.message).split('\n')[0]}${await report(what)}`); }
  };
  try {
    // 1. The org chart: the owner sets a teammate's segments from the card's button (it shows on hover); All shows on the
    // card; the sheet has no Segments row with a mouse.
    await run('org: the owner tags a teammate from the card', async () => {
      const tag = 'org';
      const {page, errors, hub} = await open(browser, {hash: '#/strategy/org'});
      await page.locator('.rdo-card').first().waitFor();
      assert.equal(await page.locator('.rdo-card[data-rdo-ref="person:cara"] .seg-card').getAttribute('title'), 'Consumer', tag + ': a tagged card shows its segment');
      assert.equal(await page.locator('.rdo-card[data-rdo-ref="bot:builder"] .seg-card').getAttribute('title'), 'Rental', tag + ': a bot card too');
      assert.equal(await page.locator('.rdo-card[data-rdo-ref="person:ben"] .seg-card').count(), 0, tag + ': an untagged card has no mark');
      await openOrgMenu(page, 'person:ben');
      assert.equal(await page.locator('.seg-menu').getAttribute('aria-label'), 'Segments for Ben Ortiz', tag + ': the menu names the teammate');
      await page.locator('.seg-menu [data-pick="all"]').click();
      await page.locator('.rdo-card[data-rdo-ref="person:ben"] .seg-card-all').waitFor();
      assert.equal(await page.locator('.rdo-card[data-rdo-ref="person:ben"] .seg-card').getAttribute('title'), 'All segments', tag + ': the All label shows on the card');
      assert.deepEqual(hub.writes.at(-1).body, {member: 'person:ben', segments: ['all']}, tag + ': All saves alone');
      await openOrgMenu(page, 'person:ben');
      await page.locator('.seg-menu [data-pick="seg-consumer"]').click();
      await page.waitForFunction(() => document.querySelector('.rdo-card[data-rdo-ref="person:ben"] .seg-card')?.getAttribute('title') === 'Consumer');
      assert.deepEqual(hub.writes.at(-1).body, {member: 'person:ben', segments: ['seg-consumer']}, tag + ': a pick saves the teammate\'s segments');
      await openOrgMenu(page, 'person:ben');
      await page.locator('.seg-menu [data-pick=""]').click();
      await page.waitForFunction(() => !document.querySelector('.rdo-card[data-rdo-ref="person:ben"] .seg-card'));
      assert.deepEqual(hub.writes.at(-1).body, {member: 'person:ben', segments: []}, tag + ': Clear saves none');
      await page.locator('.rdo-card[data-rdo-ref="person:ben"]').click();
      const dlg = page.locator('dialog.rdo-dialog[open]');
      await dlg.waitFor();
      assert.equal(await dlg.locator('[data-seg-tags]').count(), 0, tag + ': the sheet has no Segments row with a mouse');
      await page.keyboard.press('Escape');
      await dlg.waitFor({state: 'detached'});
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // A teammate sees the tags on the card and in a bot's Info card, and gets no menu anywhere.
    await run('teammate: sees the tags, no menu', async () => {
      const tag = 'teammate';
      const {page, errors} = await open(browser, {viewer: 'cara', hash: '#/strategy/org'});
      await page.locator('.rdo-card').first().waitFor();
      assert.equal(await page.locator('.rdo-card[data-rdo-ref="bot:sales-lead"] .seg-card').getAttribute('title'), 'Rental', tag + ': the card shows the tag');
      assert.equal(await page.locator('[data-rdo-seg]').count(), 0, tag + ': no Segments button on a card');
      await page.evaluate(() => { location.hash = '#/bot/sales-lead/more'; });
      await page.locator('#bot-setup-card [data-seg-chips]').waitFor();
      assert.match(await page.locator('#bot-setup-card [data-seg-chips]').innerText(), /Rental/, tag + ': the tags show in the Info card');
      assert.equal(await page.locator('#bot-setup-card [data-seg-tags]').count(), 0, tag + ': and no menu there');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // 2. A bot's Info card: the owner adds a second segment; it keeps the first and comes out in list order.
    await run('bot Info card: a second segment keeps the first', async () => {
      const tag = 'bot';
      const {page, errors, hub} = await open(browser, {hash: '#/bot/sales-lead/more'});
      await page.locator('#bot-setup-card [data-seg-tags]').waitFor();
      await page.locator('#bot-setup-card [data-seg-tags]').click();
      await page.locator('.seg-menu [data-pick="seg-consumer"]').click();
      await page.waitForFunction(() => document.querySelector('#bot-setup-card [data-seg-chips]')?.textContent.includes('Consumer'));
      assert.deepEqual(hub.writes.at(-1).body, {member: 'bot:sales-lead', segments: ['seg-rental', 'seg-consumer']}, tag + ': the list keeps its order');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // The teammate's page: the tags in the About card, with the owner's Edit; there is no separate Segments card.
    await run('person: the tags sit in the About card', async () => {
      const tag = 'person';
      const {page, errors, hub} = await open(browser, {hash: '#/person/cara'});
      await page.locator('#person-about [data-seg-chips]').waitFor();
      assert.match(await page.locator('#person-about [data-person-seg]').innerText(), /Segments[\s\S]*Consumer/, tag + ': the row reads Segments, then the tag');
      assert.equal(await page.locator('#person-segments').count(), 0, tag + ': no Segments card');
      await page.locator('#person-about [data-seg-tags]').click();
      await page.locator('.seg-menu [data-pick="seg-rental"]').click();
      await page.waitForFunction(() => document.querySelector('#person-about [data-seg-chips]')?.textContent.includes('Rental'));
      // A pick adds to the teammate's segments (Consumer stays), in the list's rank order.
      assert.deepEqual(hub.writes.at(-1).body, {member: 'person:cara', segments: ['seg-rental', 'seg-consumer']}, tag + ': the owner\'s pick adds a segment');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // 3. Goals: the dialog's Segment lists the segments in order and saves the goal's segment with it.
    await run('goals: the goal dialog saves its segment', async () => {
      const tag = 'goals';
      const {page, errors, hub} = await open(browser, {hash: '#/strategy/goals'});
      await page.locator('#goal-new').waitFor();
      await page.locator('#goal-new').click();
      const sheet = page.locator('#goal-sheet[open]');
      assert.deepEqual(await sheet.locator('select[name="segment"] option').allInnerTexts(), ['None', 'Rental', 'Consumer', 'Trade shows'], tag + ': the segments, in list order');
      await sheet.locator('input[name="title"]').fill('Close two rental leases a month');
      await sheet.locator('select[name="segment"]').selectOption('seg-rental');
      await sheet.locator('[data-goal-form] [type=submit]').click();
      await sheet.waitFor({state: 'detached'});
      const created = hub.writes.find(w => w.path === '/api/v2/goals' && w.method === 'POST');
      assert.deepEqual([created.body.title, created.body.segment_id], ['Close two rental leases a month', 'seg-rental'], tag + ': the goal takes its segment');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // A direct entry names the segment in the Goals filter (exact text, not the id), and the goals kept only as context
    // for a child read muted.
    await run('goals: a direct entry names the filter, and context goals are muted', async () => {
      const tag = 'goals filter';
      const {page, errors} = await open(browser, {hash: '#/strategy/goals?segment=seg-rental'});
      await page.locator('#strat-actions [data-seg-filter].active').waitFor();
      await page.waitForFunction(() => document.querySelector('#strat-actions .seg-filter-on')?.textContent === 'Rental');
      assert.equal(await page.locator('#strat-actions .seg-filter-on').textContent(), 'Rental', tag + ': the name is the segment\'s, not its id');
      await page.locator('.gt-row.gt-ctx', {hasText: 'Reach 100 paying customers'}).waitFor();
      assert.equal(await page.locator('.gt-row.gt-ctx').count(), 1, tag + ': the parent is context');
      assert.equal(await page.locator('.gt-row:not(.gt-ctx)', {hasText: 'Book 40 rental demos a month'}).count(), 1, tag + ': the segment\'s own goal is not muted');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // On a phone the filter keeps its icon, marked active, and drops the name (the page's buttons must fit).
    await run('goals: a phone marks the filter icon and drops its name', async () => {
      const tag = 'goals phone';
      const {page, errors} = await open(browser, {hash: '#/strategy/goals?segment=seg-rental', viewport: PHONE});
      await page.locator('#strat-actions [data-seg-filter].active').waitFor();
      assert.equal(await page.locator('#strat-actions .seg-filter-on').first().isVisible(), false, tag + ': the name is hidden');
      assert.equal(await sideways(page), false, tag + ': no sideways scroll');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // 4. Tasks: the Segment filter keeps that segment's rows and shows a chip; a task's Segment saves with its version.
    await run('tasks: the filter, its chip, and a task\'s segment saved', async () => {
      const tag = 'tasks';
      const {page, errors, hub} = await open(browser, {hash: '#/tasks?view=list&who=all'});
      await page.locator('#task-body [data-task-key]').first().waitFor();
      await page.locator('#task-filter').click();
      await page.locator('[data-pick-field="segment"]').click();
      await page.locator('input[name="tl-segment"][value="seg-rental"]').check();
      await page.locator('[data-chip="segment"]').waitFor();
      await page.keyboard.press('Escape');
      // A row's key is 't' + the task id (the peek opens from [data-open-task="t<id>"]), and the list redraws a moment after
      // its chip shows: wait for the rows to settle, then name the keys in the failure.
      await page.waitForFunction(want => JSON.stringify([...document.querySelectorAll('#task-body [data-task-key]')].map(el => el.dataset.taskKey).sort()) === JSON.stringify(want),
        ['tt-rent-1', 'tt-rent-2'], {timeout: 5000}).catch(() => {});
      const keys = await taskKeys(page);
      assert.equal(JSON.stringify(keys), JSON.stringify(['tt-rent-1', 'tt-rent-2']), `${tag}: the rows of that segment, got ${keys.join(', ') || 'none'}`);
      await page.locator('#task-body [data-open-task="tt-rent-1"]').click();
      const peek = page.locator('#task-peek');
      await peek.locator('.tmodal-title', {hasText: 'Draft the rental offer page'}).waitFor();
      await page.waitForFunction(() => document.querySelector('#task-peek [data-prop-row="segment"] .seg-chip-name')?.textContent === 'Rental');
      assert.equal(await peek.locator('[data-prop-row="segment"] .seg-chip-name').textContent(), 'Rental', tag + ': the dialog\'s chip is the name');
      await peek.locator('[data-prop="segment"]').click();
      await page.locator('.prop-pop [data-prop-pick="seg-consumer"]').click();
      await page.waitForFunction(() => document.querySelector('#task-peek [data-prop-row="segment"]')?.textContent.includes('Consumer'));
      assert.deepEqual(hub.writes.filter(w => w.path === '/api/v2/tasks/t-rent-1').map(w => w.body), [{version: 3, segment_id: 'seg-consumer'}],
        tag + ': the segment saves with the version');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // 5. Growth: the Segment filter sets the address and reads the cards for that segment.
    await run('growth: the Segment filter reads the cards for it', async () => {
      const tag = 'growth';
      const {page, errors, hub} = await open(browser, {hash: '#/growth'});
      await page.locator('.grc-num-v').first().waitFor();
      await page.locator('[data-gr-seg-slot] [data-seg-filter]').click();
      await page.locator('.seg-menu.seg-filter [data-value="seg-consumer"]').click();
      await page.waitForFunction(() => location.hash.includes('segment=seg-consumer'));
      await page.locator('[data-gr-seg-slot] .seg-filter-btn.active', {hasText: 'Consumer'}).waitFor();
      assert.ok(hub.reads.some(r => r.startsWith('/api/v2/places/growth') && r.includes('segment=seg-consumer')), tag + ': the cards are read for the segment');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // 6. Budgets: a budget's Segment in the rail, and Edit (budget_update: the segment only).
    await run('budgets: the rail shows a budget\'s segment, and Edit saves it', async () => {
      const tag = 'budgets';
      const {page, errors, hub} = await open(browser, {hash: '#/finance/budgets'});
      await page.locator('tr[data-fin-bud="b-rental-g"]').waitFor();
      await page.locator('tr[data-fin-bud="b-rental-g"] td').first().click();
      await page.locator('#fin-rail [data-fin-bud-seg]').waitFor();
      assert.match(await page.locator('#fin-rail').innerText(), /Segment[\s\S]*Rental/, tag + ': the rail shows the segment');
      await page.locator('#fin-rail [data-fin-bud-seg]').click();
      const edit = page.locator('dialog.fin-dlg');
      await edit.locator('select[name="segment_id"]').selectOption('seg-consumer');
      await edit.locator('[type=submit]').click();
      await edit.waitFor({state: 'detached'});
      assert.equal(hub.writes.find(w => w.path === '/api/v2/finance/actions').body.dry_run, true, tag + ': a dry run first');
      assert.deepEqual(sent(hub).at(-1), {kind: 'budget_update', payload: {budget_id: 'b-rental-g', segment_id: 'seg-consumer'}},
        tag + ': the segment is saved, with no effective date (a segment is not versioned)');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // A child budget with no segment of its own reads its parent's, muted.
    await run('budgets: an inherited segment reads muted', async () => {
      const tag = 'budgets inherited';
      const {page, errors} = await open(browser, {hash: '#/finance/budgets'});
      await page.locator('tr[data-fin-bud="b-rental-inh"]').waitFor();
      await page.locator('tr[data-fin-bud="b-rental-inh"] td').first().click();
      await page.locator('#fin-rail .fin-inherit .seg-chip').waitFor();
      assert.equal(await page.locator('#fin-rail .fin-inherit .seg-chip-name').textContent(), 'Rental', tag + ': the parent\'s segment, muted');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // 7. Budgets: + Budget sends the segment it is given.
    await run('budgets: + Budget sends its segment', async () => {
      const tag = 'budgets + Budget';
      const {page, errors, hub} = await open(browser, {hash: '#/finance/budgets'});
      await page.locator('[data-fin-bud-add]').waitFor();
      await page.locator('[data-fin-bud-add]').click();
      const add = page.locator('dialog.fin-dlg');
      await add.locator('input[name="amount"]').fill('1,200');
      await add.locator('select[name="segment_id"]').selectOption('seg-trade');
      await add.locator('[type=submit]').click();
      await add.waitFor({state: 'detached'});
      const created = sent(hub).at(-1);
      assert.deepEqual([created.kind, created.payload.segment_id, created.payload.amount], ['budget_create', 'seg-trade', 120000], tag + ': sends its segment');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // 8. Budgets: the Segment filter keeps that segment's budgets.
    await run('budgets: the Segment filter keeps that segment\'s budgets', async () => {
      const tag = 'budgets filter';
      const {page, errors, hub} = await open(browser, {hash: '#/finance/budgets'});
      await page.locator('tr[data-fin-bud="b-rental-g"]').waitFor();
      await page.locator('#fin-body [data-seg-filter]').click();
      await page.locator('.seg-menu.seg-filter [data-value="seg-consumer"]').click();
      await page.locator('tr[data-fin-bud="b-consumer"]').waitFor();
      assert.equal(await page.locator('tr[data-fin-bud="b-rental-g"]').count(), 0, tag + ': the other segment\'s budgets go');
      assert.ok(hub.reads.some(r => r.startsWith('/api/v2/finance/budgets') && r.includes('segment=seg-consumer')), tag + ': read for the segment');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // 9. The P&L: the probe shows By segment (year to date, no Period); a column drills into the company P&L for that
    // segment (the address names it), with no budget cell to edit there.
    await run('pnl: By segment, and a column drills into the company P&L', async () => {
      const tag = 'pnl';
      const {page, errors, hub} = await open(browser, {hash: '#/finance/pnl'});
      await page.locator('#fin-pl table.fin-pl').waitFor();
      assert.equal(await page.locator('[data-fin-seg="view"] [data-v="segments"]').count(), 1, tag + ': the By segment view');
      assert.equal(await page.locator('#fin-body [data-seg-filter]').count(), 1, tag + ': the Segment filter on the company P&L');
      await page.locator('[data-fin-seg="view"] [data-v="segments"]').click();
      await page.locator('[data-fin-segcol="seg-rental"]').waitFor();
      assert.equal(await page.locator('[data-fin-seg="period"]').count(), 0, tag + ': no Period in the By segment view');
      assert.deepEqual(await page.locator('[data-fin-segcol]').evaluateAll(els => els.map(el => el.dataset.finSegcol)), ['seg-rental', 'seg-consumer', 'unallocated'],
        tag + ': a column per segment, none on Total');
      await page.locator('[data-fin-segcol="seg-rental"]').click();
      await page.locator('#fin-body .seg-filter-btn.active', {hasText: 'Rental'}).waitFor();
      await page.locator('#fin-pl table.fin-pl').waitFor();
      assert.ok(hub.reads.some(r => r.startsWith('/api/v2/finance/pnl?') && r.includes('segment=seg-rental')), tag + ': the company P&L for the segment');
      assert.ok(await until(() => page.evaluate(() => location.hash.includes('segment=seg-rental'))), tag + ': the address names the segment');
      assert.equal(await page.locator('td[data-fin-budget]').count(), 0, tag + ': no budget cell to edit under a segment');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // Unallocated drills with its name, in the filter and its menu.
    await run('pnl: Unallocated is named in the filter', async () => {
      const tag = 'pnl unallocated';
      const {page, errors} = await open(browser, {hash: '#/finance/pnl?view=segments'});
      await page.locator('[data-fin-segcol="unallocated"]').click();
      await page.locator('#fin-body .seg-filter-btn.active', {hasText: 'Unallocated'}).waitFor();
      await page.locator('#fin-body [data-seg-filter]').click();
      assert.equal(await page.locator('.seg-menu.seg-filter [aria-checked="true"]').innerText(), 'Unallocated', tag + ': its menu item is ticked');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // The probe's 403 hides By segment and the Segment filter, and the address falls back to the company P&L.
    await run('pnl without the segment view: the probe\'s 403 hides it', async () => {
      const tag = 'pnl 403';
      const {page, errors, hub} = await open(browser, {hash: '#/finance/pnl?view=segments', segmentsOk: false});
      await page.locator('#fin-pl table.fin-pl').waitFor();
      assert.equal(await page.locator('[data-fin-seg="view"] [data-v="segments"]').count(), 0, tag + ': no By segment');
      assert.equal(await page.locator('#fin-body [data-seg-filter]').count(), 0, tag + ': no Segment filter');
      assert.equal(hub.reads.filter(r => r.startsWith('/api/v2/finance/reports/pnl_by')).length, 1, tag + ': the probe is asked once');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // A segment lead with no other Finance access: one column, their own; a click reads their P&L for the segment.
    await run('lead: a segment lead drills into their own column', async () => {
      const tag = 'lead';
      const {page, errors, hub} = await open(browser, {viewer: 'cara', hash: '#/finance/pnl', lead: true});
      await page.locator('[data-fin-segcol="seg-consumer"]').waitFor();
      assert.deepEqual(await page.locator('[data-fin-segcol]').evaluateAll(els => els.map(el => el.dataset.finSegcol)), ['seg-consumer'],
        tag + ': one column, her own');
      assert.equal(await page.locator('[data-fin-seg="view"] [data-v="company"]').count(), 0, tag + ': no whole P&L view');
      await page.locator('[data-fin-segcol="seg-consumer"]').click();
      await page.locator('#fin-body .seg-filter-btn.active', {hasText: 'Consumer'}).waitFor();
      assert.ok(await until(() => hub.reads.some(r => r.startsWith('/api/v2/finance/pnl?') && r.includes('segment=seg-consumer'))),
        tag + ': her P&L is read with segment=');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // 10. A line split by segment: the chip reads the name (exact text, once the list has loaded), and the segment's vendors.
    await run('line: split by segment', async () => {
      const tag = 'line';
      const {page, errors, hub} = await open(browser, {hash: '#/finance/line/a-6400?segment=seg-rental'});
      await page.locator('#fin-line tr[data-fin-key]').first().waitFor();
      await page.waitForFunction(() => document.querySelector('[data-fin-line-seg]')?.textContent === 'Rental');
      assert.equal(await page.locator('[data-fin-line-seg]').textContent(), 'Rental', tag + ': the segment is a chip with its name');
      assert.equal(await page.locator('#fin-line tr[data-fin-key]').count(), 2, tag + ': the vendors of the segment');
      assert.ok(hub.reads.some(r => r.startsWith('/api/v2/finance/lines/a-6400') && r.includes('segment=seg-rental')), tag + ': read for the segment');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // Without the server's echo of the segment, the line says it cannot be split and shows no rows.
    await run('line the server does not split: says so, no rows', async () => {
      const tag = 'line without echo';
      const {page, errors} = await open(browser, {hash: '#/finance/line/a-6400?segment=seg-rental', echo: false});
      await page.locator('#fin-line .fin-note').waitFor();
      assert.equal(await page.locator('#fin-line .fin-note').count(), 1, tag + ': the note shows');
      assert.equal(await page.locator('#fin-line tr[data-fin-key]').count(), 0, tag + ': and shows no rows');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // A company with no segments: no segment UI on Goals, Tasks or Budgets (the mock has no segments and no tags).
    await run('no segments: no segment UI on goals, tasks or budgets', async () => {
      const tag = 'no segments';
      const {page, errors} = await open(browser, {hash: '#/strategy/goals', noSegments: true});
      await page.locator('#goal-new').waitFor();
      await page.locator('#goal-new').click();
      const sheet = page.locator('#goal-sheet[open]');
      await sheet.locator('input[name="title"]').waitFor();
      assert.equal(await sheet.locator('select[name="segment"]').count(), 0, tag + ': no goal Segment');
      await page.keyboard.press('Escape');
      await sheet.waitFor({state: 'detached'});
      await page.evaluate(() => { location.hash = '#/tasks?view=list&who=all'; });
      await page.locator('#task-body [data-task-key]').first().waitFor();
      await page.locator('#task-filter').click();
      assert.ok(await page.locator('[data-pick-field]').count() > 0, tag + ': the filter menu opened');
      assert.equal(await page.locator('[data-pick-field="segment"]').count(), 0, tag + ': no task filter Segment');
      await page.keyboard.press('Escape');
      await page.locator('#task-body [data-open-task="tt-plain"]').click();
      await page.locator('#task-peek .tmodal-title', {hasText: 'Renew the domain'}).waitFor();
      assert.equal(await page.locator('#task-peek [data-prop="segment"]').count(), 0, tag + ': no task Segment row');
      await page.keyboard.press('Escape');
      await page.evaluate(() => { location.hash = '#/finance/budgets'; });
      await page.locator('[data-fin-bud-add]').waitFor();
      await page.locator('[data-fin-bud-add]').click();
      await page.locator('dialog.fin-dlg input[name="amount"]').waitFor();
      assert.equal(await page.locator('dialog.fin-dlg select[name="segment_id"]').count(), 0, tag + ': no budget Segment');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // A direct entry lists the segments in the Segment filter, and + Budget.
    await run('pnl: a direct entry lists the segments in its Segment filter', async () => {
      const {page, errors} = await open(browser, {hash: '#/finance/pnl'});
      await page.locator('#fin-pl table.fin-pl').waitFor();
      await page.locator('#fin-body [data-seg-filter]').click();
      assert.equal(await page.locator('.seg-menu.seg-filter [data-value="seg-rental"]').count(), 1, 'Rental is in the Segment filter');
      assert.deepEqual(errors, [], 'no page errors');
      await page.close();
    });
    await run('budgets: a direct entry lists the segments in + Budget', async () => {
      const {page, errors} = await open(browser, {hash: '#/finance/budgets'});
      await page.locator('[data-fin-bud-add]').waitFor();
      await page.locator('[data-fin-bud-add]').click();
      assert.equal(await page.locator('dialog.fin-dlg select[name="segment_id"] option').count(), 4, 'None and the three segments');
      assert.deepEqual(errors, [], 'no page errors');
      await page.close();
    });

    // Every surface at a phone's width (and each size and theme with TICO_UI_SHOTS): no sideways scroll.
    await run('sizes: org', () => sizes(browser, 'org', '#/strategy/org', p => p.locator('.rdo-card').first().waitFor()));
    await run('sizes: org menu', () => sizes(browser, 'org-menu', '#/strategy/org', p => openOrgMenu(p, 'person:ben')));
    await run('sizes: org All label', () => sizes(browser, 'org-all', '#/strategy/org', async p => {
      await openOrgMenu(p, 'person:ben');
      await p.locator('.seg-menu [data-pick="all"]').click();
      await p.locator('.rdo-card[data-rdo-ref="person:ben"] .seg-card-all').waitFor();
    }));
    await run('sizes: person', () => sizes(browser, 'person', '#/person/cara', p => p.locator('#person-about [data-seg-chips]').waitFor()));
    await run('sizes: bot', () => sizes(browser, 'bot', '#/bot/sales-lead/more', p => p.locator('#bot-setup-card [data-seg-chips]').waitFor()));
    await run('sizes: goals', () => sizes(browser, 'goals', '#/strategy/goals', p => p.locator('#strat-actions [data-seg-filter]').waitFor()));
    await run('sizes: goals filter', () => sizes(browser, 'goals-filter', '#/strategy/goals?segment=seg-rental', p => p.locator('#strat-actions [data-seg-filter].active').waitFor()));
    await run('sizes: goal dialog', () => sizes(browser, 'goal-dialog', '#/strategy/goals', async p => {
      await p.locator('#goal-new').click();
      await p.locator('#goal-sheet[open] select[name="segment"]').waitFor();
    }));
    await run('sizes: tasks', () => sizes(browser, 'tasks', '#/tasks?view=list&who=all', p => p.locator('#task-body [data-task-key]').first().waitFor()));
    await run('sizes: task dialog', () => sizes(browser, 'task-dialog', '#/tasks?view=list&who=all', async p => {
      await p.locator('#task-body [data-open-task="tt-rent-1"]').click();
      await p.waitForFunction(() => document.querySelector('#task-peek [data-prop-row="segment"] .seg-chip')?.textContent.includes('Rental'));
    }));
    await run('sizes: growth', () => sizes(browser, 'growth', '#/growth', p => p.locator('[data-gr-seg-slot] [data-seg-filter]').waitFor()));
    await run('sizes: budgets', () => sizes(browser, 'budgets', '#/finance/budgets', p => p.locator('tr[data-fin-bud="b-rental-g"]').waitFor()));
    await run('sizes: budget dialog', () => sizes(browser, 'budget-dialog', '#/finance/budgets', async p => {
      await p.locator('[data-fin-bud-add]').click();
      await p.locator('dialog.fin-dlg select[name="segment_id"]').waitFor();
    }));
    await run('sizes: pnl', () => sizes(browser, 'pnl', '#/finance/pnl', p => p.locator('#fin-pl table.fin-pl').waitFor()));
    await run('sizes: pnl-segments', () => sizes(browser, 'pnl-segments', '#/finance/pnl?view=segments', p => p.locator('[data-fin-segcol]').first().waitFor()));
    await run('sizes: pnl lead', () => sizes(browser, 'pnl-lead', '#/finance/pnl', p => p.locator('[data-fin-segcol="seg-consumer"]').waitFor(), {viewer: 'cara', lead: true}));
    await run('sizes: line', () => sizes(browser, 'line', '#/finance/line/a-6400?segment=seg-rental', p => p.locator('#fin-line tr[data-fin-key]').first().waitFor()));

    // Direct entry: the goals tree is read for the segment a pick names.
    await run('goals: a segment pick re-reads the tree', async () => {
      const {page, errors, hub} = await open(browser, {hash: '#/strategy/goals'});
      await page.locator('#strat-actions [data-seg-filter]').click();
      await page.locator('.seg-menu.seg-filter [data-value="seg-consumer"]').click();
      await page.waitForFunction(() => location.hash.includes('segment=seg-consumer'));
      assert.ok(await until(() => hub.reads.some(r => r.startsWith('/api/v2/goals/tree?segment=seg-consumer'))), 'the tree is read for the segment');
      assert.deepEqual(errors, [], 'no page errors');
      await page.close();
    });
    assert.deepEqual(failed, [], 'scenarios that failed (each line names the scenario and its first failing check)');
    console.log('segment-tags: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
