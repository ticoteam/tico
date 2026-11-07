// Segments on the surfaces that carry them (ui/app/segment-tags.ts) against a fake hub: Acme Lights, with Ana as the
// owner and Cara as a teammate. The org chart's teammate sheet and the teammate's page, and a bot's Info pane: the
// owner's Segments menu writes the tags, and a teammate sees the chips with no menu. Goals: the Segment filter and the
// goal dialog's Segment. Tasks: the Segment filter chip, and a task's Segment property saved with its version. Growth:
// its Segment filter. Finance: the budgets' Segment filter, a budget's Segment in the rail (Edit saves budget_update
// from this month), + Budget with a segment; the P&L's By segment view, which the probe shows or hides (a 403 hides
// it), a column's drill into the company P&L with no budget cell to edit, and a line split by segment, with and
// without the server's echo. The last checks are a Goals segment pick and the Finance pages' segment lists on a direct
// entry. Every surface is checked for no sideways scroll on a phone. TICO_UI_SHOTS=<dir>
// saves <surface>-<light|dark>-<desktop|phone>.png for each surface at both sizes and both themes.
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
// GET /api/v2/finance/reports/pnl_by?by=segment: a column per segment, Unallocated and Total; cents.
const PNL_BY = {excluded: false, columns: [{key: 'seg-rental', label: 'Rental', color: 'blue'}, {key: 'seg-consumer', label: 'Consumer', color: 'green'},
  {key: 'unallocated', label: 'Unallocated'}, {key: 'total', label: 'Total'}],
rows: [{key: 'revenue', label: 'Revenue', values: {'seg-rental': 0, 'seg-consumer': 0, unallocated: 0, total: 0}},
  {key: 'costs', label: 'Costs', values: {'seg-rental': 5200000, 'seg-consumer': 2500000, unallocated: 900000, total: 8600000}},
  {key: 'contribution', label: 'Contribution', values: {'seg-rental': -5200000, 'seg-consumer': -2500000, unallocated: -900000, total: -8600000}}]};
const budget = (id, name, amount, actual, extra = {}) => ({id, name, period: 'month', account_id: 'a-6400', catch_all: false, team: null, teammate: null,
  vendor: null, parent_id: null, owner: 'human:ana', warn_pct: 80, at_limit: 'warn', rollover: 'none', active: true, card_limits: false, segment_id: null, ...extra,
  state: {amount, actual, held: 0, remaining: amount - actual, pct: String(actual / amount * 100), crossed_warn: false, at_limit: false,
    period_start: thisMonth + '-01', period_end: thisMonth + '-28', excluded: false}});
const BUDGETS = [budget('b-mkt', 'Marketing', 900000, 159000),
  budget('b-rental', 'Rental Marketing Manager', 600000, 128000, {team: 'rental', parent_id: 'b-mkt', segment_id: 'seg-rental'}),
  budget('b-rental-g', 'Rental Google Ad Owner', 300000, 52000, {team: 'rental', vendor: 'Google Ads', parent_id: 'b-rental', segment_id: 'seg-rental'}),
  budget('b-consumer', 'Consumer Marketing Manager', 300000, 250000, {team: 'consumer', parent_id: 'b-mkt', segment_id: 'seg-consumer'})];
const ACCESS = {teams: [], private_lines: [], defaults: {manager: 'team_expenses', member: 'own_expenses', manager_payroll: 'totals'}, view_version: 'v1',
  teammates: [{teammate: 'human:ana', kind: 'person', team: '', reports_to: '', default: 'everything', grants: [], rights: ALL_RIGHTS}],
  can_view_as: true, can_edit_defaults: true};
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
async function open(browser, {viewport = DESKTOP, viewer = 'ana', theme = 'dark', hash = '#/strategy/org', segmentsOk = true, echo = true} = {}) {
  const me = viewer === org.me.id ? org.me
    : {id: viewer, name: org.people.find(p => p.id === viewer).name, email: viewer + '@acme.example', role: 'member', company_role: 'member', cloud: true};
  const config = {company_name: 'Acme Lights'};
  const hub = {writes: [], reads: [], members: MEMBERS.map(m => ({...m, segments: [...m.segments]})), tasks: TASKS.map(t => ({...t})),
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
    if (p === '/api/v2/segments') return json({segments: SEGMENTS});
    if (p === '/api/v2/segments/members' && method === 'POST') {
      const row = hub.members.find(m => m.member === body.member);
      if (row) row.segments = body.segments; else hub.members.push({member: body.member, segments: body.segments});
      return json({member: body.member, segments: body.segments});
    }
    if (p === '/api/v2/segments/members') return json({members: hub.members});
    // The org chart and Strategy's Goals.
    if (p === '/api/v2/groups') return json(org.groups);
    if (p === '/api/v2/strategy') return json({current: null, bets: [], may_edit: true});
    if (p === '/api/v2/goals/tree') return json({goals: hub.goals, owners: {}, other_kpis: [], proposals: []});
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
    if (p === '/api/v2/places/growth') return json({kind: 'growth', pins: [], can_edit: false, saved: true});
    // Finance.
    if (p === '/api/v2/finance/access') return json(ACCESS);
    if (p === '/api/v2/finance/pnl') return json(pnl(url));
    if (p === '/api/v2/finance/budgets') {
      const seg = url.searchParams.get('segment') || '';
      return json({budgets: BUDGETS.filter(b => !seg || b.segment_id === seg || b.id === 'b-mkt'), more: false, on: url.searchParams.get('at'), excluded: false});
    }
    if (p === '/api/v2/finance/reports/budget_vs_actual') {
      const s = url.searchParams.get('start'), z = url.searchParams.get('end'), cols = s && z ? columns(s, z) : [];
      return json({kind: 'budget_vs_actual', columns: cols, budgets: BUDGETS.map(b => ({id: b.id, budget: cols.map(() => b.state.amount)}))});
    }
    if (p === '/api/v2/finance/reports/pnl_by') return segmentsOk ? json(PNL_BY) : json({error: {code: 'denied', detail: 'Not in your view'}}, 403);
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

(async () => {
  const browser = await chromium.launch();
  const failed = [];
  // A failing scenario is named in the list the run ends with; the others still run.
  const run = async (what, fn) => {
    LAST = null;
    try { await fn(); } catch (e) { failed.push(`${what}: ${String(e.message).split('\n')[0]}${await report(what)}`); }
  };
  try {
    // 1. The org chart: the owner tags a teammate from the sheet; the chips show on the card, the sheet and the teammate's page.
    await run('org: the owner tags a teammate', async () => {
      const tag = 'org';
      const {page, errors, hub} = await open(browser, {hash: '#/strategy/org'});
      await page.locator('.rdo-card').first().waitFor();
      assert.equal(await page.locator('.rdo-card[data-rdo-ref="person:cara"] .seg-card').getAttribute('title'), 'Consumer', tag + ': a tagged card shows its segment');
      assert.equal(await page.locator('.rdo-card[data-rdo-ref="bot:builder"] .seg-card').getAttribute('title'), 'Rental', tag + ': a bot card too');
      await page.locator('.rdo-card[data-rdo-ref="person:ben"]').click();
      const dlg = page.locator('dialog.rdo-dialog[open]');
      assert.match(await dlg.locator('[data-seg-chips]').innerText(), /None/, tag + ': an untagged teammate says None');
      await dlg.locator('[data-seg-tags]').click();
      await page.locator('.seg-menu [data-pick="seg-consumer"]').click();
      await page.waitForFunction(() => document.querySelector('dialog[open] [data-seg-chips]')?.textContent.includes('Consumer'));
      assert.deepEqual(hub.writes.filter(w => w.path === '/api/v2/segments/members').map(w => w.body), [{member: 'person:ben', segments: ['seg-consumer']}],
        tag + ': a pick saves the teammate\'s segments');
      await page.locator('.seg-menu [data-pick=""]').click();
      await page.waitForFunction(() => document.querySelector('dialog[open] [data-seg-chips]')?.textContent.includes('None'));
      assert.deepEqual(hub.writes.at(-1).body, {member: 'person:ben', segments: []}, tag + ': Clear saves none');
      await page.keyboard.press('Escape');
      if (await dlg.count()) await page.keyboard.press('Escape');
      await dlg.waitFor({state: 'detached'});
      // The teammate's page: the same tags, with the owner's menu.
      await page.evaluate(() => { location.hash = '#/person/cara'; });
      await page.locator('#person-segments [data-seg-chips]').waitFor();
      assert.match(await page.locator('#person-segments').innerText(), /Segments[\s\S]*Consumer/, tag + ': the teammate page shows the tags');
      assert.equal(await page.locator('#person-segments [data-seg-tags]').count(), 1, tag + ': and the owner\'s menu');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // A teammate sees the chips on the card, the sheet and a bot's Info pane, and gets no menu.
    await run('teammate: sees the tags, no menu', async () => {
      const tag = 'teammate';
      const {page, errors} = await open(browser, {viewer: 'cara', hash: '#/strategy/org'});
      await page.locator('.rdo-card').first().waitFor();
      await page.locator('.rdo-card[data-rdo-ref="bot:sales-lead"]').click();
      const dlg = page.locator('dialog.rdo-dialog[open]');
      await dlg.locator('[data-seg-chips]').waitFor();
      assert.match(await dlg.locator('[data-seg-chips]').innerText(), /Rental/, tag + ': the tags show on the sheet');
      assert.equal(await dlg.locator('[data-seg-tags]').count(), 0, tag + ': no Segments menu on the sheet');
      await page.evaluate(() => { location.hash = '#/bot/sales-lead/more'; });
      await page.locator('#bot-segments:not([hidden])').waitFor();
      assert.match(await page.locator('#bot-segments').innerText(), /Rental/, tag + ': the tags show on the Info pane');
      assert.equal(await page.locator('#bot-segments [data-seg-tags]').count(), 0, tag + ': and no menu there');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // 2. A bot's Info pane: the owner adds a second segment; it keeps the first and comes out in list order.
    await run('bot Info pane: a second segment keeps the first', async () => {
      const tag = 'bot';
      const {page, errors, hub} = await open(browser, {hash: '#/bot/sales-lead/more'});
      await page.locator('#bot-segments:not([hidden])').waitFor();
      await page.locator('#bot-segments [data-seg-tags]').click();
      await page.locator('.seg-menu [data-pick="seg-consumer"]').click();
      await page.waitForFunction(() => document.querySelector('#bot-segments [data-seg-chips]')?.textContent.includes('Consumer'));
      assert.deepEqual(hub.writes.at(-1).body, {member: 'bot:sales-lead', segments: ['seg-rental', 'seg-consumer']}, tag + ': the list keeps its order');
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
      await page.locator('[data-gr-seg-slot] [data-seg-filter]').click();
      await page.locator('.seg-menu.seg-filter [data-value="seg-consumer"]').click();
      await page.waitForFunction(() => location.hash.includes('segment=seg-consumer'));
      await page.locator('[data-gr-seg-slot] .seg-filter-btn.active', {hasText: 'Consumer'}).waitFor();
      assert.ok(hub.reads.some(r => r.startsWith('/api/v2/places/growth') && r.includes('segment=seg-consumer')), tag + ': the cards are read for the segment');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // 6. Budgets: a budget's Segment in the rail, and Edit (budget_update from this month).
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
      assert.deepEqual(sent(hub).at(-1), {kind: 'budget_update', payload: {budget_id: 'b-rental-g', segment_id: 'seg-consumer', effective_from: thisMonth + '-01'}},
        tag + ': the segment is saved from this month');
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

    // 9. The P&L: the probe shows By segment; a column drills into the company P&L for that segment, with no budget
    // cell to edit there.
    await run('pnl: By segment, and a column drills into the company P&L', async () => {
      const tag = 'pnl';
      const {page, errors, hub} = await open(browser, {hash: '#/finance/pnl'});
      await page.locator('#fin-pl table.fin-pl').waitFor();
      assert.equal(await page.locator('[data-fin-seg="view"] [data-v="segments"]').count(), 1, tag + ': the By segment view');
      assert.equal(await page.locator('#fin-body [data-seg-filter]').count(), 1, tag + ': the Segment filter on the company P&L');
      await page.locator('[data-fin-seg="view"] [data-v="segments"]').click();
      await page.locator('[data-fin-segcol="seg-rental"]').waitFor();
      assert.deepEqual(await page.locator('[data-fin-segcol]').evaluateAll(els => els.map(el => el.dataset.finSegcol)), ['seg-rental', 'seg-consumer', 'unallocated'],
        tag + ': a column per segment, none on Total');
      await page.locator('[data-fin-segcol="seg-rental"]').click();
      await page.locator('#fin-body .seg-filter-btn.active', {hasText: 'Rental'}).waitFor();
      await page.locator('#fin-pl table.fin-pl').waitFor();
      assert.ok(hub.reads.some(r => r.startsWith('/api/v2/finance/pnl?') && r.includes('segment=seg-rental')), tag + ': the company P&L for the segment');
      assert.equal(await page.locator('td[data-fin-budget]').count(), 0, tag + ': no budget cell to edit under a segment');
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
      assert.ok(hub.reads.some(r => r.startsWith('/api/v2/finance/reports/pnl_by')), tag + ': the probe asked');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // 10. A line split by segment: the chip, and the vendors the segment has.
    await run('line: split by segment', async () => {
      const tag = 'line';
      const {page, errors, hub} = await open(browser, {hash: '#/finance/line/a-6400?segment=seg-rental'});
      await page.locator('#fin-line tr[data-fin-key]').first().waitFor();
      assert.equal(await page.locator('#fin-body .fin-chip', {hasText: 'Rental'}).count(), 1, tag + ': the segment is a chip');
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
      assert.equal(await page.locator('#fin-line .fin-note').innerText(), 'This line can\'t be split by segment yet.', tag + ': says so');
      assert.equal(await page.locator('#fin-line tr[data-fin-key]').count(), 0, tag + ': and shows no rows');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    });

    // Every surface at a phone's width (and each size and theme with TICO_UI_SHOTS): no sideways scroll.
    await run('sizes: org', () => sizes(browser, 'org', '#/strategy/org', p => p.locator('.rdo-card').first().waitFor()));
    await run('sizes: person', () => sizes(browser, 'person', '#/person/cara', p => p.locator('#person-segments [data-seg-chips]').waitFor()));
    await run('sizes: bot', () => sizes(browser, 'bot', '#/bot/sales-lead/more', p => p.locator('#bot-segments:not([hidden]) [data-seg-chips]').waitFor()));
    await run('sizes: goals', () => sizes(browser, 'goals', '#/strategy/goals', p => p.locator('#strat-actions [data-seg-filter]').waitFor()));
    await run('sizes: tasks', () => sizes(browser, 'tasks', '#/tasks?view=list&who=all',p => p.locator('#task-body [data-task-key]').first().waitFor()));
    await run('sizes: growth', () => sizes(browser, 'growth', '#/growth', p => p.locator('[data-gr-seg-slot] [data-seg-filter]').waitFor()));
    await run('sizes: budgets', () => sizes(browser, 'budgets', '#/finance/budgets', p => p.locator('tr[data-fin-bud="b-rental-g"]').waitFor()));
    await run('sizes: pnl', () => sizes(browser, 'pnl', '#/finance/pnl', p => p.locator('#fin-pl table.fin-pl').waitFor()));
    await run('sizes: pnl-segments', () => sizes(browser, 'pnl-segments', '#/finance/pnl?view=segments', p => p.locator('[data-fin-segcol]').first().waitFor()));
    await run('sizes: line', () => sizes(browser, 'line', '#/finance/line/a-6400?segment=seg-rental', p => p.locator('#fin-line tr[data-fin-key]').first().waitFor()));

    // Direct entries: each page read on its own, with nothing above it loaded.
    await run('goals: a segment pick re-reads the tree', async () => {
      const {page, errors, hub} = await open(browser, {hash: '#/strategy/goals'});
      await page.locator('#strat-actions [data-seg-filter]').click();
      await page.locator('.seg-menu.seg-filter [data-value="seg-consumer"]').click();
      await page.waitForFunction(() => location.hash.includes('segment=seg-consumer'));
      assert.ok(await until(() => hub.reads.some(r => r.startsWith('/api/v2/goals/tree?segment=seg-consumer'))), 'the tree is read for the segment');
      assert.deepEqual(errors, [], 'no page errors');
      await page.close();
    });
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
    assert.deepEqual(failed, [], 'scenarios that failed (each line names the scenario and its first failing check)');
    console.log('segment-tags: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
