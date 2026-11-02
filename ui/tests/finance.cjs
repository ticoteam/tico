// The Finance tab (ui/app/finance*.js) against a fake hub: Acme (acme.example) with an owner (Ana), a manager (Cara,
// who leads Engineering) and a few bots. The owner sees every tab; the manager only P&L (expenses), Cards, Transactions
// and Access, and Balance sheet / Sources stay hidden even by address. A budget cell edits on a double-click in a
// future month (or, to raise it, this one), dry run first, and sends budget_update with effective_from; the card rail's Freeze sends card_freeze. Payables and
// Receivables list bills and invoices with no totals row; their decisions run a dry run first and send the right action,
// and both tabs hide from a viewer without payables or receivables (a payables grant shows only Payables, read only).
// The Balance sheet's "+ Add" enters a journal entry and offers Loan, Asset and Import history. Cards: Unfreeze only
// for a person's freeze, a bot card's Saved on chips (card_unsave). Transactions › Transfers cancels a queued one
// (payment_cancel). Tax (owner only): Sales tax with Exempt, and 1099. The P&L marks imported months. Each tab draws
// without errors and without sideways scroll on a phone. TICO_UI_SHOTS=<dir> saves screenshots (desktop and phone).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const uiDir = path.join(__dirname, '..');
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};

// ----------------------------------------------------------------- Acme
const people = [{id: 'ana', name: 'Ana Silva', role: 'owner'}, {id: 'cara', name: 'Cara Lee', role: 'member', team: 'eng'},
  {id: 'erin', name: 'Erin Tester', role: 'member'}];
const emps = [['eng-lead', 'Engineering Lead', 'eng'], ['builder', 'Builder', 'platform'], ['mkt-lead', 'Marketing Lead', 'mkt'],
  ['writer', 'Writer', 'content'], ['bookkeeper', 'Bookkeeper', '']].map(([name, display_name, team]) =>
  ({name, display_name, role: display_name, team, status: 'active', host: 'keeper', can_chat: true}));
const teams = [{id: 'eng', name: 'Engineering', parent: '', lead: 'human:cara'}, {id: 'platform', name: 'Platform', parent: 'eng', lead: 'bot:eng-lead'},
  {id: 'web', name: 'Web', parent: 'eng', lead: 'bot:eng-lead'},
  {id: 'mkt', name: 'Marketing', parent: '', lead: 'bot:mkt-lead'}, {id: 'content', name: 'Content', parent: 'mkt', lead: 'bot:mkt-lead'}];
const ALL_RIGHTS = ['finance.book', 'finance.ap', 'finance.ar', 'finance.pay', 'finance.cards', 'finance.budgets', 'finance.rules', 'finance.admin'];
const mate = (teammate, kind, team, def, extra = {}) => ({teammate, kind, team, reports_to: '', default: def, grants: [], rights: [], ...extra});
const MATES = [
  mate('human:ana', 'person', '', 'everything', {rights: ALL_RIGHTS}),
  // default_team (backend/finance/tab.py): Cara's default is all Engineering; Engineering Lead's spans Platform and Web.
  mate('human:cara', 'person', 'eng', 'team_expenses', {reports_to: 'human:ana', default_team: 'eng'}),
  mate('bot:eng-lead', 'bot', 'platform', 'team_expenses', {reports_to: 'human:cara', default_team: null}),
  mate('bot:builder', 'bot', 'platform', 'own_expenses', {reports_to: 'bot:eng-lead'}),
  mate('bot:mkt-lead', 'bot', 'mkt', 'team_expenses', {reports_to: 'human:ana', default_team: 'mkt', grants: [{id: 'g1', subject_kind: 'teammate', subject: 'bot:mkt-lead',
    what: 'pnl_lines', whose: 'company', whose_team: null, account_ids: ['a-6100'], new_members: false, granted_by: 'human:ana', expires_at: null, status: 'active', live: true}]}),
  mate('bot:writer', 'bot', 'content', 'own_expenses', {reports_to: 'bot:mkt-lead', grants: [{id: 'g2', subject_kind: 'teammate', subject: 'bot:writer',
    what: 'expenses', whose: 'team', whose_team: 'content', account_ids: [], new_members: false, granted_by: 'bot:mkt-lead', expires_at: null, status: 'active', live: true}]}),
  mate('bot:bookkeeper', 'bot', '', 'own_expenses', {rights: ['finance.book', 'finance.ap', 'finance.ar', 'finance.pay', 'finance.cards'],
    grants: [{id: 'g3', subject_kind: 'teammate', subject: 'bot:bookkeeper', what: 'everything', whose: 'company', whose_team: null, account_ids: [],
      new_members: false, granted_by: 'human:ana', expires_at: null, status: 'active', live: true}]}),
  mate('human:erin', 'person', null, 'own_expenses'),
];
function access(viewer, apGrant = false, apRights = []) {
  const base = {teams, private_lines: [{account_id: 'a-6200', name: 'Payroll', others_see: 'totals', full_detail: ['bot:bookkeeper']}],
    defaults: {manager: 'team_expenses', member: 'own_expenses', manager_payroll: 'totals'}, view_version: 'v1'};
  if (viewer === 'ana') return {...base, teammates: MATES.map(m => ({...m, may_grant: m.teammate !== 'human:ana'})), can_view_as: true, can_edit_defaults: true};
  const mine = ['human:cara', 'bot:eng-lead', 'bot:builder'];
  const payables = {id: 'g4', subject_kind: 'teammate', subject: 'human:cara', what: 'payables', whose: 'company', whose_team: null, account_ids: [],
    new_members: false, granted_by: 'human:ana', expires_at: null, status: 'active', live: true};
  return {...base, teams: teams.filter(t => ['eng', 'platform'].includes(t.id)), private_lines: [],
    // may_grant is the server's check_grant verdict; eng-lead's is off here to show the page follows it.
    teammates: MATES.filter(m => mine.includes(m.teammate)).map(m => ({...m, may_grant: m.teammate === 'bot:builder'}))
      .map(m => apGrant && m.teammate === 'human:cara' ? {...m, grants: [payables], rights: apRights} : m),
    can_view_as: false, can_edit_defaults: false};
}

// The chart: [id, code, name, type, parent, monthly actual (cents), budget, private]
const LINES = [
  ['a-4000', '4000', 'Revenue', 'income', null, 0, null],
  ['a-4100', '4100', 'Subscriptions', 'income', 'a-4000', 4200000, 4000000],
  ['a-4200', '4200', 'Services', 'income', 'a-4000', 800000, 1000000],
  ['a-5000', '5000', 'Cost of revenue', 'expense', null, 0, null],
  ['a-5100', '5100', 'Hosting', 'expense', 'a-5000', 410000, 400050],
  ['a-5200', '5200', 'AI services', 'expense', 'a-5000', 520000, null],
  ['a-6000', '6000', 'Operating expenses', 'expense', null, 0, null],
  ['a-6100', '6100', 'Software', 'expense', 'a-6000', 294000, 300000],
  ['a-6200', '6200', 'Payroll', 'expense', 'a-6000', 1800000, 1800000, true],
  ['a-6300', '6300', 'Contractors', 'expense', 'a-6000', 610000, 500000],
];
const pad = n => String(n).padStart(2, '0');
const iso = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const day = s => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
function columns(start, end) {
  const out = [], today = iso(new Date());
  for (let d = day(start); iso(d) <= end; d = new Date(d.getFullYear(), d.getMonth() + 1, 1)) {
    const a = new Date(d.getFullYear(), d.getMonth(), 1), z = new Date(d.getFullYear(), d.getMonth() + 1, 0);
    const [s, e] = [iso(a), iso(z)];
    out.push({start: s, end: e, label: s.slice(0, 7), state: e < today ? 'past' : s <= today ? 'current' : 'future'});
  }
  return out;
}
function pnl(url, me, noRevenue = false, noCogsProjection = false) {
  const q = url.searchParams, cols = columns(q.get('start'), q.get('end')), view = q.get('view') || 'company';
  const lines = LINES.filter(l => (view === 'company' && !noRevenue) || l[3] === 'expense');
  const wave = (v, i) => v ? Math.round(v * (1 + ((i * 7) % 5 - 2) / 40) / 100) * 100 : 0;
  // A private line is hidden from all but the owner (backend/finance/reads.py pnl): renamed, no actuals or projections.
  const viewer = url.searchParams.get('view_as') || 'human:' + me;
  const section = code => code.startsWith('4') ? 'revenue' : code.startsWith('5') ? 'cost_of_revenue' : 'operating';
  const rows = lines.map(([account_id, code, base0, type, parent_id, base, budget, priv]) => priv && viewer !== 'human:ana'
    ? {account_id, code, name: 'Payroll (private)', type, section: section(code), parent_id, private: true, actual: null, total: null, budget: budget == null ? null : cols.map(() => budget), projection: null}
    : ({account_id, code, name: base0, type, section: section(code), parent_id, private: false,
    actual: cols.map((c, i) => c.state === 'future' ? null : c.state === 'current' ? Math.round(wave(base, i) * 0.9) : wave(base, i)),
    total: 0, budget: budget == null ? null : cols.map(() => budget),
    projection: cols.map((c, i) => c.state === 'future' && !(noCogsProjection && code.startsWith('5')) ? wave(base, i + 1) : null), projection_method: 'trend'}));
  const sum = type => cols.map((c, i) => rows.filter(r => r.type === type).reduce((a, r) => a + (r.actual?.[i] || 0), 0));
  const income = sum('income'), expense = sum('expense');
  return {columns: cols, rows, income, expense, net: income.map((v, i) => v - expense[i]), excluded: rows.some(r => r.private), start: q.get('start'), end: q.get('end'), view_version: 'v1',
    imported: viewer === 'human:ana' ? [{month: cols[0].label, basis: 'cash'}] : [], snapshot: null,
    filter: {teams: q.getAll('teams'), teammates: q.getAll('teammates'), who: q.get('who') || 'both', unattributed: q.get('unattributed') === 'true'},
    ...(view === 'expenses' ? {teammates: [{teammate: 'bot:eng-lead', kind: 'bot', spent: 182000, budget: 200000}, {teammate: 'bot:builder', kind: 'bot', spent: 96000, budget: 120000},
      {teammate: 'human:cara', kind: 'person', spent: 41000, budget: null}]} : {})};
}
const budget = (id, name, account_id, amount, extra = {}) => ({id, name, period: 'month', account_id, catch_all: false, team: null, teammate: null, vendor: null,
  parent_id: null, owner: 'human:ana', warn_pct: 80, at_limit: 'ask', rollover: false, active: true,
  state: {amount, actual: Math.round(amount * 0.8), held: 0, remaining: Math.round(amount * 0.2), pct: 80, crossed_warn: true, at_limit: false}, ...extra});
const BUDGETS = [budget('b-software', 'Software', 'a-6100', 300000), budget('b-hosting', 'Hosting', 'a-5100', 400050),
  budget('b-figma', 'Figma', 'a-6100', 135000, {vendor: 'Figma', parent_id: 'b-software', at_limit: 'decline', period: 'quarter'}),
  budget('b-github', 'GitHub', 'a-6100', 90000, {vendor: 'GitHub', parent_id: 'b-software'}),
  // Team budgets: one under Software (Engineering), two under Contractors (Platform and Web, both inside Engineering).
  budget('b-software-eng', 'Software · Engineering', 'a-6100', 120000, {team: 'eng', parent_id: 'b-software'}),
  budget('b-contr', 'Contractors', 'a-6300', 500000),
  budget('b-contr-platform', 'Contractors · Platform', 'a-6300', 200000, {team: 'platform', parent_id: 'b-contr'}),
  budget('b-contr-web', 'Contractors · Web', 'a-6300', 150000, {team: 'web', parent_id: 'b-contr'})];
function line(id, url) {
  const l = LINES.find(x => x[0] === id) || LINES[7], vendor = url.searchParams.get('vendor');
  const nm = new Date(); nm.setDate(1); nm.setMonth(nm.getMonth() + 1);
  if (vendor != null) return {account: {id: l[0], code: l[1], name: l[2], type: l[3]}, vendor, total: 84000, excluded: 0, hidden: 0,
    by_vendor: [{key: vendor, label: vendor, amount: 84000}], by_team: [{key: 'eng', label: 'Engineering', amount: 60000}, {key: 'platform', label: 'Platform', amount: 24000}],
    by_teammate: [], paid_by: [{kind: 'card', name: 'Engineering', amount: 63000, count: 3}, {kind: 'bank', name: 'Checking ••0001', amount: 21000, count: 1}],
    next_month: {month: iso(nm).slice(0, 7), projection: 310000}, ...(vendor === 'GitHub' ? {seats: 12, renewal_on: '2027-01-15'} : {}),
    entries: [{line_id: 'l1', entry_id: 'e1', date: '2026-10-28', account_id: id, amount: 21000, vendor, description: vendor + ' Team', memo: '', source: 'feed', tags: {}}], more: false};
  return {account: {id: l[0], code: l[1], name: l[2], type: l[3]}, vendor: null, total: l[5], excluded: 0, hidden: 0,
    paid_by: [{kind: 'card', name: 'Engineering', amount: 84000, count: 4}], next_month: {month: iso(nm).slice(0, 7), projection: 310000},
    // Each column sums to next_month; Slack and Content have a projection but no spend (Slack falls in the capped Other);
    // vendors named Other and Unattributed are ordinary keys, apart from the reserved ' other' and ' unattributed'.
    next_by: {vendor: {GitHub: 120000, Figma: 60000, Notion: 50000, ' unattributed': 10000, Slack: 50000, unattributed: 10000, other: 10000},
      team: {eng: 200000, mkt: 60000, content: 40000, ' unattributed': 10000},
      teammate: {'bot:eng-lead': 150000, 'bot:writer': 60000, 'human:cara': 40000, ' unattributed': 60000}},
    next_labels: {vendor: {GitHub: 'GitHub', Figma: 'Figma', Notion: 'Notion', ' unattributed': 'No vendor', Slack: 'Slack', unattributed: 'Unattributed', other: 'Other'},
      team: {eng: 'Engineering', mkt: 'Marketing', content: 'Content', ' unattributed': 'Unattributed'}, teammate: {' unattributed': 'Unattributed'}},
    by_vendor: [{key: 'GitHub', label: 'GitHub', amount: 84000}, {key: 'Figma', label: 'Figma', amount: 42000}, {key: 'Notion', label: 'Notion', amount: 32000}, {key: ' unattributed', label: 'No vendor', amount: 5000}, {key: 'unattributed', label: 'Unattributed', amount: 2000}, {key: 'other', label: 'Other', amount: 3000}, {key: ' other', label: 'Other · 4', amount: 21000}],
    by_team: [{key: 'eng', label: 'Engineering', amount: 120000}, {key: 'mkt', label: 'Marketing', amount: 50000}, {key: ' unattributed', label: 'Unattributed', amount: 9000}],
    by_teammate: [{key: 'bot:eng-lead', label: 'Engineering Lead', amount: 64000}, {key: 'bot:writer', label: 'Writer', amount: 30000}, {key: 'human:cara', label: 'Cara Lee', amount: 22000}],
    entries: [{line_id: 'l1', entry_id: 'e1', date: '2026-10-28', account_id: id, amount: 21000, vendor: 'GitHub', description: 'GitHub Team', memo: '', source: 'feed', tags: {teammate: 'bot:eng-lead'}},
      {line_id: 'l2', entry_id: 'e2', date: '2026-10-12', account_id: id, amount: 42000, vendor: 'Figma', description: 'Figma seats', memo: '', source: 'feed', tags: {team: 'mkt'}}], more: false};
}
const BS = {as_of: '2026-10-30', scoped: false, currency: 'USD', closed_through: '2026-09-30',
  assets: [{account_id: 'a-1000', code: '1000', name: 'Cash', subtype: 'cash', parent_id: null, balance: 0, entry_sources: [], sources: []},
    {account_id: 'a-1010', code: '1010', name: 'Checking', subtype: 'cash', parent_id: 'a-1000', balance: 17105000, entry_sources: ['feed'],
      sources: [{money_account_id: 'm1', name: 'Checking', provider: 'Mercury', last4: '0001', provider_balance: 17105000, balance_as_of: '2026-10-30', due_out: {count: 2, amount: 430000}}]},
    {account_id: 'a-1020', code: '1020', name: 'Savings', subtype: 'cash', parent_id: 'a-1000', balance: 1315000, entry_sources: ['feed'],
      sources: [{money_account_id: 'm2', name: 'Savings', provider: 'Mercury', last4: '0002', provider_balance: 1315000, balance_as_of: '2026-10-30'}]},
    {account_id: 'a-1300', code: '1300', name: 'Prepaid expenses', subtype: 'prepaid', parent_id: null, balance: 480000, entry_sources: ['manual'], sources: []}],
  liabilities: [{account_id: 'a-2100', code: '2100', name: 'Card balance', subtype: 'card', parent_id: null, balance: 1240000, entry_sources: ['feed'], sources: []}],
  equity: [{account_id: 'a-3000', code: '3000', name: 'Owner investment', subtype: 'equity', parent_id: null, balance: 15000000, entry_sources: ['manual'], sources: []},
    {account_id: null, code: '3999', name: 'Net income to date', subtype: 'equity', parent_id: null, balance: 2660000, entry_sources: [], sources: []}],
  totals: {asset: 18900000, liability: 1240000, equity: 17660000}};
const CARDS = [
  {id: 'card-1', provider: 'mercury', holder: 'bot:eng-lead', nickname: 'Engineering', last4: '4242', kind: 'virtual', status: 'active', limit: 200000, limit_interval: 'monthly',
    currency: 'USD', budget: {id: 'b-software', name: 'Software'}, spent: 182000, sync_status: 'ok', expires_at: '2028-10-31'},
  {id: 'card-2', provider: 'mercury', holder: 'bot:writer', nickname: 'Content tools', last4: '1881', kind: 'virtual', status: 'frozen', limit: 50000, limit_interval: 'monthly',
    currency: 'USD', budget: null, spent: 12000, sync_status: 'ok', frozen_by: 'person',
    saved_on: [{vendor: 'v-figma', label: 'Figma', saved_by: 'human:ana', saved_at: '2026-10-20T09:00:00Z'}]},
  {id: 'card-3', provider: 'mercury', holder: 'human:cara', nickname: 'Travel', last4: '3007', kind: 'physical', status: 'active', limit: 300000, limit_interval: 'monthly',
    currency: 'USD', budget: null, spent: 41000, sync_status: 'ok'},
  {id: 'card-4', provider: 'mercury', holder: 'bot:builder', nickname: 'Build tools', last4: '5150', kind: 'virtual', status: 'frozen', limit: 80000, limit_interval: 'monthly',
    currency: 'USD', budget: {id: 'b-software', name: 'Software'}, spent: 80000, sync_status: 'ok', frozen_by: 'budget', saved_on: []}];
const TRANSFERS = [{id: 'xf-1', status: 'queued', amount: 500000, currency: 'USD', memo: 'to savings', from: {id: 'm1', name: 'Checking', last4: '0001'},
  to: {id: 'm2', name: 'Savings', last4: '0002'}, created_by: 'human:ana', created_at: '2026-10-29T10:00:00Z', settled_at: null, last_error: null}];
const SALES_TAX = {year: 2026, currency: 'USD', rows: [{period: '2026-Q3', code: 'CA', jurisdiction: 'California', taxable_sales: 1200000, exempt_sales: 300000,
  tax_collected: 87000, tax_remitted: 60000, due: 27000, due_on: '2026-10-31'}]};
const F1099 = {year: 2026, currency: 'USD', threshold: 60000, due_on: '2027-01-31', rows: [{vendor_id: 'v-northwind', vendor: 'Northwind Design', tin_on_file: false,
  cash_paid: 610000, card_paid_excluded: 0, threshold: 60000, reportable: true}]};
const TX = [
  {kind: 'feed', id: 'f1', date: '2026-10-29', amount: -21000, currency: 'USD', counterparty: 'GitHub', description: 'GITHUB.COM', status: 'posted', pending: false, card_id: 'card-1',
    money_account: {id: 'm1', name: 'Checking', last4: '0001'}, lines: [{line_id: 'l1', account_id: 'a-6100', account: 'Software', amount: 21000, vendor: 'GitHub', tags: {teammate: 'bot:eng-lead', budget: 'b-software'}}]},
  {kind: 'feed', id: 'f2', date: '2026-10-28', amount: -9900, currency: 'USD', counterparty: 'Figma', description: 'FIGMA', status: 'new', pending: false, card_id: null,
    money_account: {id: 'm1', name: 'Checking', last4: '0001'}, lines: []},
  {kind: 'feed', id: 'f4', date: '2026-10-27', amount: -50000, currency: 'USD', counterparty: 'Linear', description: 'LINEAR', status: 'posted', pending: false, card_id: null,
    money_account: {id: 'm1', name: 'Checking', last4: '0001'}, lines: [
      {line_id: 'l5', account_id: 'a-6100', account: 'Software', amount: 30000, vendor: 'Linear', tags: {team: 'eng', teammate: 'bot:eng-lead'}},
      {line_id: 'l6', account_id: 'a-6100', account: 'Software', amount: 20000, vendor: 'Linear', tags: {team: 'mkt'}}]},
  {kind: 'feed', id: 'f5', date: '2026-10-26', amount: -41000, currency: 'USD', counterparty: 'AWS', description: 'AWS', status: 'posted', pending: false, card_id: null, reconciled: true,
    money_account: {id: 'm1', name: 'Checking', last4: '0001'}, lines: [{line_id: 'l7', account_id: 'a-5100', account: 'Hosting', amount: 41000, vendor: 'AWS', tags: {}}]},
  {kind: 'feed', id: 'f7', date: '2026-10-25', amount: -500000, currency: 'USD', counterparty: 'Savings', description: 'Transfer to Savings', status: 'posted', pending: false, card_id: null,
    provider_kind: 'internalTransfer', entry_id: 'e7', money_account: {id: 'm1', name: 'Checking', last4: '0001'}, lines: []},
  {kind: 'entry', id: 'l9', entry_id: 'e9', date: '2026-10-27', amount: 610000, account_id: 'a-6300', account: 'Contractors', vendor: 'Northwind Design', description: 'October design work',
    memo: '', source: 'manual', reversed: false, tags: {team: 'mkt'}}];
const SOURCES = {groups: [
  {group: 'banks', name: 'Banks', count: 2, rows: [
    {id: 'm1', kind: 'bank', name: 'Checking', provider: 'mercury', last4: '0001', balance: 17105000, balance_as_of: '2026-10-30T10:00:00Z', status: 'active', account: {id: 'a-1010', code: '1010', name: 'Checking'},
      sync: {connection_id: 'c1', provider: 'mercury', status: 'active', last_ok_at: new Date(Date.now() - 120000).toISOString(), last_sync_at: null, last_error: null}, feed: {posted: 211, new: 3}, access: [{who: 'human:ana', via: 'default'}]},
    {id: 'm2', kind: 'bank', name: 'Savings', provider: 'mercury', last4: '0002', balance: 1315000, balance_as_of: '2026-10-30T10:00:00Z', status: 'active', account: {id: 'a-1020', code: '1020', name: 'Savings'},
      sync: {connection_id: 'c1', provider: 'mercury', status: 'active', last_ok_at: new Date(Date.now() - 120000).toISOString(), last_sync_at: null, last_error: null}, feed: {posted: 12}, access: []}]},
  {group: 'cards', name: 'Cards', count: 1, rows: [{id: 'm3', kind: 'card_program', name: 'Tico cards', provider: 'mercury', last4: null, balance: null, account: {id: 'a-2100', code: '2100', name: 'Card balance'},
    sync: {connection_id: 'c1', provider: 'mercury', status: 'active', last_ok_at: new Date().toISOString(), last_error: null}, feed: {}, cards: {active: 2, frozen: 1}, access: []}]},
  {group: 'payments', name: 'Payments', count: 1, rows: [{id: 'c1', kind: 'connection', name: 'Mercury', provider: 'mercury',
    sync: {connection_id: 'c1', provider: 'mercury', status: 'active', last_ok_at: new Date().toISOString(), last_error: null},
    payments: {mode: 'approval_queue', eligible: true, enabled: false, direct_send: false, why_not: null}}]},
  {group: 'payroll', name: 'Payroll', count: 0, rows: []},
  {group: 'manual', name: 'Manual', count: 1, rows: [{id: 'manual', kind: 'entries', name: 'Journal entries and imports', manual: 4, import: 0}]}]};
// Bills and invoices (GET /v2/finance/bills, /invoices and one of each): FinanceDocumentRow and FinanceDocument.
const doc = (kind, id, number, contact, status, total, due, extra = {}) => ({id, kind, number, [kind === 'bill' ? 'vendor' : 'customer']: contact,
  date: '2026-10-20', due_date: due, status, currency: 'USD', total, paid: 0, remaining: ['draft', 'paid', 'void'].includes(status) ? 0 : total,
  aging: ['draft', 'paid', 'void'].includes(status) ? null : 'current', days_late: 0, memo: '', entry_id: null, attachments: 0,
  created_by: 'bot:bookkeeper', created_at: '2026-10-20T09:00:00Z',
  ...(kind === 'bill' ? {origin: 'email', approved_by: null, approved_at: null, pay_on: null} : {sent_at: '2026-10-15', written_off: 0, payment_link: null}), ...extra});
const BILLS = [
  doc('bill', 'bill-1', 'INV-2291', {id: 'v-figma', name: 'Figma'}, 'draft', 180000, '2026-11-03', {attachments: 1}),
  doc('bill', 'bill-2', '#118', {id: 'v-northwind', name: 'Northwind Design'}, 'open', 250000, '2026-11-05', {approved_by: 'human:ana'}),
  doc('bill', 'bill-3', 'Oct-2026', {id: 'v-vercel', name: 'Vercel'}, 'scheduled', 124000, '2026-11-12', {pay_on: '2026-11-11',
    payment: {id: 'pay-3', status: 'awaiting_bank_approval', amount: 124000, on: '2026-11-11', error: null}}),
  // Its last payment was rejected in Mercury: the bill is open again and says so.
  doc('bill', 'bill-4', 'DD-8812', {id: 'v-datadog', name: 'Datadog'}, 'open', 139000, '2026-10-15', {aging: '1_30', days_late: 16,
    payment: {id: 'pay-4', status: 'cancelled', amount: 139000, on: '2026-10-14', error: 'rejected in Mercury'}}),
  // Its scheduled payment's approval was declined before any payment existed: open, and says so.
  doc('bill', 'bill-5', 'GH-77', {id: 'v-github', name: 'GitHub'}, 'open', 21000, '2026-10-20', {
    payment: {id: null, status: 'denied', amount: 21000, on: '2026-10-15', error: 'declined by human:ana'}}),
  // Scheduled for a later day, no payment yet: it can move to another day.
  doc('bill', 'bill-6', 'NW-9', {id: 'v-northwind', name: 'Northwind Design'}, 'scheduled', 40000, '2026-12-20', {pay_on: '2099-12-18'}),
  // Paid: listed under Recently paid with its line, team, day and who paid it.
  doc('bill', 'bill-7', 'AWS-10', {id: 'v-aws', name: 'AWS'}, 'paid', 391208, '2026-10-27', {account: {id: 'a-5100', code: '5100', name: 'Hosting'},
    split: false, team: 'eng', paid_on: '2026-10-27', paid_by: 'human:ana'})];
const INVOICES = [
  doc('invoice', 'inv-1', '#1047', {id: 'c-bright', name: 'Brightline Studio'}, 'sent', 400000, '2026-11-14'),
  doc('invoice', 'inv-2', '#1033', {id: 'c-pine', name: 'Pine & Co'}, 'sent', 150000, '2026-09-20', {aging: '31_60', days_late: 41}),
  doc('invoice', 'inv-3', '#1051', {id: 'c-north', name: 'Northstar Labs'}, 'partial', 320000, '2026-11-27', {paid: 100000, remaining: 220000})];
const docDetail = r => ({...r, terms_days: 30, view_version: 'v1',
  lines: [{id: 'dl-' + r.id, account: r.kind === 'bill' ? {id: 'a-6100', code: '6100', name: 'Software'} : {id: 'a-4200', code: '4200', name: 'Services'},
    amount: r.total, description: 'Seats', tags: {}}],
  attachments: r.attachments ? [{id: 'att-1', file_id: 'file-1', created_by: 'bot:bookkeeper', created_at: '2026-10-20T09:00:00Z'}] : [],
  // Part came in on a bank row; the rest was marked paid and its bank row has not arrived (from_kind expected).
  settlements: r.paid ? [{id: 's-1', from_kind: 'feed', from_id: 'f9', amount: 60000, settled_on: '2026-10-25', entry_id: 'e9', voided_at: null, created_by: 'bot:bookkeeper'},
    {id: 's-2', from_kind: 'expected', from_id: 'm1', amount: r.paid - 60000, settled_on: '2026-10-29', entry_id: 'e10', voided_at: null, created_by: 'human:ana'}] : [],
  actions: r.id === 'bill-1' ? [{action_id: 'act-b1', kind: 'bill_approve', status: 'needs_approval', actor: 'bot:bookkeeper', approval_id: 'ap-1',
    approval_to: 'human:ana', rule_id: 'r-1', created_at: '2026-10-20T09:01:00Z'}] : [],
  ...(r.kind === 'bill' ? {schedule: r.pay_on && !r.payment ? {pay_on: r.pay_on, money_account_id: 'm2', action_id: null} : null} : {})});
const CONTACTS = [{id: 'v-figma', name: 'Figma', kind: 'vendor'}, {id: 'v-northwind', name: 'Northwind Design', kind: 'vendor'},
  {id: 'c-bright', name: 'Brightline Studio', kind: 'customer'}, {id: 'c-pine', name: 'Pine & Co', kind: 'customer'}]
  .map(c => ({...c, emails: [], default_account: null, is_1099: false, has_tax_id: false, tax_id_last4: null, recipient_id: null, active: true, owed: {}}));
const listDocs = (rows, url) => {
  const status = url.searchParams.get('status') || '', q = (url.searchParams.get('q') || '').toLowerCase();
  const open = ['draft', 'open', 'scheduled', 'sent', 'partial'], late = url.searchParams.get('late') === 'true';
  return rows.filter(r => (!status || (status === 'unpaid' ? open.includes(r.status) : r.status === status)) && (!late || (r.aging && r.aging !== 'current'))
    && (!q || (r.vendor || r.customer).name.toLowerCase().includes(q) || r.number.toLowerCase().includes(q)));
};

// ----------------------------------------------------------------- the fake hub
// GET /v2/finance/chart: bills post to expense and asset lines, invoices to income lines, journals to any line. The
// balance-sheet lines here and the P&L lines (not private) of LINES.
const CHART = [...[['a-1010', '1010', 'Checking', 'asset', 'bank'], ['a-1020', '1020', 'Savings', 'asset', 'bank'], ['a-1200', '1200', 'Accounts receivable', 'asset', 'receivable'],
  ['a-1300', '1300', 'Prepaid expenses', 'asset', 'prepaid'], ['a-2000', '2000', 'Accounts payable', 'liability', 'payable'], ['a-2100', '2100', 'Card balance', 'liability', 'card']]
  .map(([id, number, name, type, subtype]) => ({id, number, name, type, subtype, parent_id: null, company_wide: true})),
  ...LINES.filter(l => !l[7]).map(([id, number, name, type, parent_id]) => ({id, number, name, type, subtype: '', parent_id, company_wide: true}))];
const chartFor = use => CHART.filter(a => use === 'bill' ? ['expense', 'asset'].includes(a.type) && !['payable', 'receivable'].includes(a.subtype)
  : use === 'invoice' ? a.type === 'income' : true);

async function open(browser, {viewport = DESKTOP, viewer = 'ana', hash = '#/finance/pnl', noRevenue = false, noCogsProjection = false, apGrant = false, apRights = [], budgetsExcl = false} = {}) {
  const person = people.find(p => p.id === viewer);
  const me = {id: viewer, name: person.name, email: viewer + '@acme.example', role: person.role, company_role: person.role, cloud: true};
  const config = {company_name: 'Acme'};
  // hub.ask: dry runs need this approver; hub.hold: dry runs wait for it; hub.apply: a categorize that moves a bank row
  // to a line shows in the next transactions read (this page's own copy of TX); hub.noChart: the chart read fails.
  const writes = [], reads = [], hub = {ask: '', hold: null, apply: false, noChart: false, impact: null}, tx = structuredClone(TX);
  let billsEmail = 'bills@acme.example';
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events') return;
    const body = method === 'GET' ? null : req.postDataJSON?.() ?? null;
    if (method !== 'GET') writes.push([method, p + url.search, body, req.headers()['idempotency-key'] || '']);
    else if (p.startsWith('/api/v2/finance/')) reads.push(p + url.search);
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/employees') return json(emps);
    if (p === '/api/humans') return json({people, org_groups: []});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/needs-you') return json({items: [], version: 'v0', count: 0, next: null});
    if (p === '/api/v2/conversations') return json({conversations: []});
    const denied = () => json({error: {code: 'denied', detail: 'Not in your view'}}, 403);
    if (p === '/api/v2/finance/access') return json(access(viewer, apGrant, apRights));
    // Payables for the owner and a payables grant; receivables for the owner only (backend/finance/tab_ap.py).
    const seesAp = viewer === 'ana' || apGrant, seesAr = viewer === 'ana';
    if (p === '/api/v2/finance/bills') return seesAp ? json({bills: listDocs(BILLS, url), next_cursor: null, currency: 'USD', view_version: 'v1', bills_email: billsEmail,
      open: {count: 3, remaining: 513000, aging: {current: 374000, '1_30': 139000, '31_60': 0, '61_90': 0, over_90: 0}, more: false}}) : denied();
    if (p === '/api/v2/finance/invoices') return seesAr ? json({invoices: listDocs(INVOICES, url), next_cursor: null, currency: 'USD', view_version: 'v1',
      open: {count: 3, remaining: 770000, aging: {current: 620000, '1_30': 0, '31_60': 150000, '61_90': 0, over_90: 0}, more: false}}) : denied();
    if (p.startsWith('/api/v2/finance/bills/') || p.startsWith('/api/v2/finance/invoices/')) {
      const r = [...(seesAp ? BILLS : []), ...(seesAr ? INVOICES : [])].find(x => x.id === decodeURIComponent(p.split('/').pop()));
      return r ? json(docDetail(r)) : json({error: {code: 'not_found', detail: 'no such document in your view'}}, 404);
    }
    if (p === '/api/v2/finance/contacts') return json({contacts: CONTACTS.filter(c => !url.searchParams.get('kind') || c.kind === url.searchParams.get('kind')), next_cursor: null, view_version: 'v1'});
    // The form pickers: the chart for teammates who write to the books, the bank accounts for finance.pay.
    const rights = viewer === 'ana' ? ALL_RIGHTS : apRights;
    // For the owner, Contractors stands in for a line that is not company-wide, so its amounts need a For (the viewer
    // first); for anyone else no P&L line is.
    if (p === '/api/v2/finance/chart' && hub.noChart) return json({error: {code: 'down', detail: 'down'}}, 500);
    if (p === '/api/v2/finance/chart') return ['finance.book', 'finance.ap', 'finance.ar'].some(r => rights.includes(r))
      ? json({accounts: chartFor(url.searchParams.get('for')).map(a => viewer === 'ana'
        ? (a.id === 'a-6300' ? {...a, company_wide: false, attributable: [{teammate: 'human:ana'}, {team: 'eng'}, {team: 'platform'}, {teammate: 'bot:builder'}]} : a)
        : !['expense', 'income'].includes(a.type) ? a
        : {...a, company_wide: false, attributable: [{team: 'eng'}, {team: 'platform'}, {teammate: 'bot:builder'}, {teammate: 'bot:eng-lead'}, {teammate: 'human:cara'}]}),
        more: false, view_version: 'v1'}) : denied();
    if (p === '/api/v2/finance/pay-from') return rights.includes('finance.pay')
      ? json({accounts: [{id: 'm1', name: 'Checking', provider: 'mercury', last4: '0001', balance: 17105000}, {id: 'm2', name: 'Savings', provider: 'mercury', last4: '0002', balance: 1315000}],
        currency: 'USD', view_version: 'v1'}) : denied();
    // The bills address: only an Email & Phone inbox's address has an inbox; any other is kept with inbox_id null.
    if (p === '/api/v2/finance/bills-address' && method === 'PUT') {
      billsEmail = body.address;
      return json({bills_email: billsEmail, inbox_id: billsEmail === 'bills@acme.example' ? 'ib-1' : null});
    }
    if (p === '/api/v2/finance/pnl') return json(pnl(url, viewer, noRevenue, noCogsProjection));
    // budgetsExcl: a viewer whose budget figures leave payroll out (the read and each row say excluded).
    if (p === '/api/v2/finance/budgets') return json(budgetsExcl ? {budgets: BUDGETS.map(b => ({...b, state: {...b.state, excluded: true}})), more: false, excluded: true} : {budgets: BUDGETS, more: false});
    if (p.startsWith('/api/v2/finance/lines/')) return json(line(decodeURIComponent(p.split('/').pop()), url));
    if (p === '/api/v2/finance/mercury-users') return json({users: [{id: 'mu-1', name: 'Ana Silva', connection_id: 'c1', email: 'a•••@acme.example', updated_at: '2026-10-30T10:00:00Z'},
      {id: 'mu-2', name: 'Ops Card User', connection_id: 'c1', email: 'o•••@acme.example', updated_at: '2026-10-30T10:00:00Z'}]});
    if (p === '/api/v2/finance/balance-sheet') return viewer === 'ana' ? json(BS) : denied();
    if (p === '/api/v2/finance/cards') return json({cards: viewer === 'ana' ? CARDS : CARDS.filter(c => c.holder !== 'bot:writer'), next: null});
    if (p === '/api/v2/finance/transactions') return json({rows: tx, next: null});
    // The new Figma row ($99): two Figma payments marked paid add up to it; one Northwind payment is the same amount.
    if (p === '/api/v2/finance/feed/f2/expected') return json({feed_id: 'f2', kind: 'bill', amount: 9900, date: '2026-10-28', more: false, view_version: 'v1', expected: [
      {settlement_id: 's-a', kind: 'bill', document_id: 'bill-5', number: 'FG-1', contact_id: 'v-figma', contact_name: 'Figma', amount: 6000, marked_on: '2026-10-26'},
      {settlement_id: 's-b', kind: 'bill', document_id: 'bill-6', number: 'FG-2', contact_id: 'v-figma', contact_name: 'Figma', amount: 3900, marked_on: '2026-10-27'},
      {settlement_id: 's-c', kind: 'bill', document_id: 'bill-2', number: '#118', contact_id: 'v-northwind', contact_name: 'Northwind Design', amount: 9900, marked_on: '2026-10-27'}]});
    if (p.startsWith('/api/v2/finance/feed/')) return json({feed_id: p.split('/')[5], kind: 'bill', amount: 0, date: '', more: false, view_version: 'v1', expected: []});
    if (p === '/api/v2/finance/sources') return viewer === 'ana' ? json(SOURCES) : denied();
    if (p === '/api/v2/finance/transfers') return viewer === 'ana' ? json({transfers: TRANSFERS, next_cursor: null}) : denied();
    if (p === '/api/v2/finance/tax/sales-tax') return viewer === 'ana' ? json(SALES_TAX) : denied();
    if (p === '/api/v2/finance/tax/1099') return viewer === 'ana' ? json(F1099) : denied();
    if (p === '/api/v2/triggers' && method === 'GET') return json({triggers: []});
    if (p === '/api/v2/finance/actions' && method === 'POST' && body.dry_run && hub.hold) await hub.hold;
    if (p === '/api/v2/finance/actions' && method === 'POST' && body.dry_run && hub.ask) return json({kind: body.kind, status: 'needs_approval', approver: hub.ask, dry_run: true, impact: hub.impact || {}});
    const moved = body?.kind === 'categorize' && !body.dry_run && hub.apply && tx.find(r => r.id === body.payload?.feed_id);
    if (moved) moved.lines = [{line_id: 'l-' + moved.id, account_id: body.payload.account_id, account: CHART.find(a => a.id === body.payload.account_id)?.name,
      amount: -moved.amount, vendor: moved.counterparty, tags: body.payload.tags}];
    // The rules send a bill's schedule to the owner for approval: its dry run says so, then the request is sent.
    if (p === '/api/v2/finance/actions' && method === 'POST' && body.dry_run) return json(body.kind === 'bill_schedule'
      ? {status: 'needs_approval', kind: body.kind, action_id: '', approver: 'human:ana', reason: '', dry_run: true, result: {}, impact: {}}
      : {status: 'committed', kind: body.kind, action_id: '', approver: null, reason: '', dry_run: true, result: {}, impact: {}});
    if (p === '/api/v2/finance/actions' && method === 'POST') return json({action_id: 'act-' + writes.length, kind: body.kind,
      status: body.kind === 'bill_schedule' ? 'needs_approval' : 'committed', code: null, reason: null, approver: null,
      result: {contact_create: {contact_id: 'v-new'}, bill_create: {bill_id: 'bill-2'}, invoice_create: {invoice_id: 'inv-9'}}[body.kind] || {}});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  await page.locator('#fin-head [data-fin-tab]').first().waitFor();
  return {page, errors, writes, reads, hub};
}

// ----------------------------------------------------------------- helpers
const tabs = page => page.locator('#fin-head [data-fin-tab]').allInnerTexts();
const sideways = page => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
const actions = writes => writes.filter(w => w[1] === '/api/v2/finance/actions').map(w => w[2]);
// The real requests only (a decision's dry run comes first).
const sent = writes => actions(writes).filter(a => !a.dry_run);
// Waits (up to 3s) for something the fake hub records, then a beat for the page to redraw.
async function settled(done) {
  for (let t = Date.now(); !done() && Date.now() - t < 3000;) await new Promise(r => setTimeout(r, 20));
  assert.ok(done(), 'timed out waiting');
  await new Promise(r => setTimeout(r, 100));
}
async function shot(page, name) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  // Finite animations only (a running skeleton shimmer never finishes).
  await page.evaluate(() => Promise.all(document.getAnimations().filter(a => a.effect?.getComputedTiming().endTime !== Infinity).map(a => a.finished.catch(() => {}))));
  await page.screenshot({path: path.join(dir, name + '.png'), fullPage: true});
}
async function go(page, hash, ready) {
  await page.evaluate(h => { location.hash = h; }, hash);
  await page.locator(ready).first().waitFor();
}
const READY = {pnl: '.fin-pl', balance: '.fin-bs', payables: 'tr[data-fin-doc="bill-1"]', receivables: 'tr[data-fin-doc="inv-1"]', cards: '[data-fin-card]', transactions: '[data-fin-tx]',
  tax: 'tr[data-fin-tax]', sources: '[data-fin-group]', access: '[data-fin-mate]'};

(async () => {
  const browser = await chromium.launch();
  try {
    // The owner, on a computer: every tab, the P&L as a statement, budget edits only in future months.
    {
      const tag = 'owner';
      const {page, errors, writes, reads, hub} = await open(browser);
      await page.locator('.fin-pl').waitFor();
      assert.deepEqual(await tabs(page), ['P&L', 'Balance sheet', 'Payables', 'Receivables', 'Cards', 'Transactions', 'Tax', 'Sources', 'Access'], tag + ': every tab, in order');
      assert.equal(await page.locator('#side a[data-nav="finance"]').count(), 1, tag + ': Finance in the sidebar');
      const firsts = await page.locator('.fin-pl tbody td:first-child').allInnerTexts();
      for (const want of ['Gross profit', 'Gross margin', 'Total operating expenses', 'Net income']) assert.ok(firsts.includes(want), `${tag}: the statement has ${want}`);
      assert.equal(await page.locator('.fin-pl thead th.fin-p').count() > 0, true, tag + ': projected columns are shaded');
      assert.equal(await page.locator('.fin-pl thead .fin-tag').first().textContent(), 'Imported', tag + ': an imported month says so');
      // The budget column is the current month's: a line with a budget takes a raise, a line without one nothing.
      assert.equal(await page.locator('td[data-fin-budget="a-5200"]').count(), 0, tag + ': this month has no new budgets');
      const softwareBudget = page.locator('td[data-fin-budget="a-6100"]');
      const nowCols = await page.evaluate(() => FIN.pnl.data.columns), now = nowCols.find(c => c.state === 'current');
      let wrote = actions(writes).length;
      await softwareBudget.dblclick();
      await page.locator('.fin-cell-in').fill('1');
      await page.locator('.fin-cell-in').press('Enter');
      await page.waitForFunction(() => !document.querySelector('.fin-cell-in'));
      assert.equal(actions(writes).length, wrote, tag + ': this month only goes up');
      // A raise the rules send for approval says so first, and Cancel sends nothing.
      hub.ask = 'human:ana';
      await softwareBudget.dblclick();
      await page.locator('.fin-cell-in').fill('9000');
      await page.locator('.fin-cell-in').press('Enter');
      await page.locator('dialog.fin-dlg .fin-ask').waitFor();
      assert.match(await page.locator('dialog.fin-dlg .fin-ask').innerText(), /^Approval from /, tag + ': names the approver');
      assert.match(await page.locator('dialog.fin-dlg .fin-kv').first().innerText(), /\$9,000/, tag + ': shows the change');
      assert.equal(sent(writes).length, 0, tag + ': nothing sent yet');
      assert.equal(actions(writes).at(-1).dry_run, true, tag + ': the dry run came first');
      await page.locator('dialog.fin-dlg .fin-foot [data-close]').click();
      assert.equal(sent(writes).length, 0, tag + ': Cancel sends nothing');
      await softwareBudget.dblclick();
      await page.locator('.fin-cell-in').fill('9000');
      await page.locator('.fin-cell-in').press('Enter');
      await page.locator('dialog.fin-dlg .fin-ask').waitFor();
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.locator('dialog.fin-dlg').waitFor({state: 'detached'});
      assert.deepEqual(actions(writes).at(-1), {kind: 'budget_update', payload: {budget_id: 'b-software', amount: 900000, effective_from: now.start}}, tag + ': Send sends the raise');
      hub.ask = '';
      // A past month's cell moves the budget column there; still not editable.
      const cols = await page.evaluate(() => FIN.pnl.data.columns);
      const past = cols.findIndex(c => c.state === 'past'), future = cols.findIndex(c => c.state === 'future');
      await page.locator(`td[data-fin-cell="a-6100|${past}"]`).click();
      assert.equal(await page.locator('td[data-fin-budget]').count(), 0, tag + ': a past month\'s budgets are not editable');
      assert.match(await page.locator('#fin-rail h2').innerText(), /^Software · /, tag + ': the rail shows the line and month');
      // A future month: double-click, type, Enter → budget_update from that month on.
      await page.locator(`td[data-fin-cell="a-6100|${future}"]`).click();
      await page.locator('td[data-fin-budget="a-6100"]').dblclick();
      await page.locator('.fin-cell-in').fill('3,500');
      await page.locator('.fin-cell-in').press('Enter');
      await page.waitForFunction(() => !document.querySelector('.fin-cell-in'));
      assert.deepEqual(actions(writes).at(-1), {kind: 'budget_update', payload: {budget_id: 'b-software', amount: 350000, effective_from: cols[future].start}},
        tag + ': budget_update with effective_from = the month');
      // A line with no budget yet makes one from that month.
      await page.locator(`td[data-fin-cell="a-5200|${future}"]`).click();
      await page.locator('td[data-fin-budget="a-5200"]').dblclick();
      await page.locator('.fin-cell-in').fill('6000');
      await page.locator('.fin-cell-in').press('Enter');
      await page.waitForFunction(() => !document.querySelector('.fin-cell-in'));
      assert.deepEqual(actions(writes).at(-1), {kind: 'budget_create', payload: {name: 'AI services', period: 'month', account_id: 'a-5200', amount: 600000, effective_from: cols[future].start}},
        tag + ': budget_create for a line without one');
      // An untouched $4,000.50 shows its cents and posts nothing on blur.
      await page.locator(`td[data-fin-cell="a-5100|${future}"]`).click();
      await page.locator('td[data-fin-budget="a-5100"]').dblclick();
      assert.equal(await page.locator('.fin-cell-in').inputValue(), '4000.50', tag + ': the editor seeds exact cents');
      const before = actions(writes).length;
      await page.locator('#fin-head h1').click();
      await page.waitForFunction(() => !document.querySelector('.fin-cell-in'));
      await page.waitForTimeout(100);
      assert.equal(actions(writes).length, before, tag + ': blur on an untouched budget posts nothing');
      const asOf = await page.locator('[data-fin-compare] option').evaluateAll(o => o.map(x => x.value).filter(Boolean)[0]);
      await page.locator('[data-fin-compare]').selectOption(asOf);
      await page.waitForFunction(() => FIN.pnl?.compare && !FIN.pnl.busy);
      assert.ok(reads.some(r => r.startsWith('/api/v2/finance/pnl?') && r.includes('budget_as_of=' + asOf)), tag + ': Compare reads the plan as of that day');
      await page.locator(`td[data-fin-cell="a-6100|${future}"]`).click();
      assert.equal(await page.locator('td[data-fin-budget]').count(), 0, tag + ': no budget editing while comparing');
      await page.evaluate(() => { const tr = [...document.querySelectorAll('.fin-ln')].find(r => r.querySelector('a[href*="/line/a-6100"]')), td = tr.children[tr.children.length - 2]; td.dataset.finBudget = 'a-6100'; finPnlEdit(td); });
      assert.equal(await page.locator('.fin-cell-in').count(), 0, tag + ': the editor refuses while comparing');
      await page.locator('[data-fin-compare]').selectOption('');
      await page.waitForFunction(() => FIN.pnl?.data && !FIN.pnl.compare && !FIN.pnl.busy);
      const keys = writes.map(w => w[3]);
      assert.ok(keys.every(Boolean) && new Set(keys).size === keys.length, tag + ': a fresh Idempotency-Key per write');
      await page.locator(`td[data-fin-cell="a-6100|${cols.findIndex(c => c.state === 'current')}"]`).click();
      await page.locator('#fin-by .fin-bars').waitFor();
      await shot(page, 'pnl-desktop');
      // The line page: vendors, their budgets in place.
      await page.locator('a[href$="/line/a-6100"]').click();
      await page.locator('[data-fin-key="GitHub"]').waitFor();
      assert.deepEqual(await page.locator('#fin-line thead th').allInnerTexts().then(t => t.slice(0, 2)), ['VENDOR', 'TEAM'].map(s => s), tag + ': vendor rows');
      assert.equal(await page.locator('#fin-line tbody tr').count(), 7, tag + ': one row per vendor, No vendor and Other, no totals row');
      const projSum = async () => (await page.locator('#fin-line tbody td.fin-p').allInnerTexts()).reduce((n, t) => n + (Number(t.replace(/[^\d.-]/g, '')) || 0), 0);
      assert.equal(await projSum(), 3100, tag + ': the projection column adds up to the line\'s (a projection-only vendor in the capped Other)');
      assert.equal(await page.locator('[data-fin-key="other"] td.fin-p').innerText().then(t => Number(t.replace(/[^\d.-]/g, ''))), 100,
        tag + ': a vendor named Other is an ordinary row with its own projection, not the folded one');
      await page.locator('[data-fin-by]').selectOption('team');
      await page.locator('[data-fin-key="content"]').waitFor();
      assert.match(await page.locator('[data-fin-key="content"]').innerText(), /^Content\s/, tag + ': a team with only a projection gets its own row');
      assert.equal(await projSum(), 3100, tag + ': by team too');
      await page.locator('[data-fin-by]').selectOption('teammate');
      await page.locator('[data-fin-key=" unattributed"]').waitFor();
      assert.match(await page.locator('[data-fin-key=" unattributed"]').innerText(), /^Unattributed\s/, tag + ': Unattributed by name');
      assert.equal(await projSum(), 3100, tag + ': by teammate too');
      await page.locator('[data-fin-by]').selectOption('vendor');
      await page.locator('[data-fin-key="GitHub"]').waitFor();
      assert.equal(await page.locator('input[data-fin-vb="GitHub"]').count(), 1, tag + ': this month\'s vendor budget takes a raise');
      assert.equal(await page.locator('input[data-fin-vb="Notion"]').count(), 0, tag + ': this month takes no new vendor budget');
      const next = cols[future].start.slice(0, 7);
      await page.locator('[data-fin-month]').fill(next);
      await page.locator('input[data-fin-vb="GitHub"]').waitFor();
      await page.locator('input[data-fin-vb="GitHub"]').fill('950');
      await page.locator('input[data-fin-vb="GitHub"]').press('Enter');
      await page.waitForTimeout(150);
      assert.deepEqual(actions(writes).at(-1), {kind: 'budget_update', payload: {budget_id: 'b-github', amount: 95000, effective_from: next + '-01'}},
        tag + ': a vendor budget changes from the month shown');
      assert.equal(await page.locator('input[data-fin-vb="Figma"]').count(), 0, tag + ': a quarterly vendor budget is not edited as a month');
      assert.equal(await page.locator('input[data-fin-vb=" unattributed"], input[data-fin-vb=" other"]').count(), 0, tag + ': No vendor and Other take no budget');
      assert.equal(await page.locator('input[data-fin-vb="unattributed"], input[data-fin-vb="other"]').count(), 2, tag + ': vendors named Unattributed and Other do');
      assert.match(await page.locator('[data-fin-key="Figma"]').innerText(), /\b1,350 \/ qtr/, tag + ': it says its period');
      await page.locator('input[data-fin-vb="Notion"]').fill('400');
      await page.locator('input[data-fin-vb="Notion"]').press('Enter');
      await page.waitForTimeout(150);
      assert.deepEqual(actions(writes).at(-1), {kind: 'budget_create', payload: {name: 'Notion', period: 'month', account_id: 'a-6100', vendor: 'Notion', parent_id: 'b-software', amount: 40000, effective_from: next + '-01'}},
        tag + ': a vendor budget under the line\'s, from the month shown');
      assert.ok(reads.some(r => r === `/api/v2/finance/budgets?at=${next}-01`), tag + ': budgets read as they stand in the month shown');
      assert.match(await page.locator('#fin-rail .fin-more').getAttribute('href'), /#\/finance\/transactions\?account_id=a-6100&q=GitHub/, tag + ': All GitHub transactions');
      await page.locator('#fin-rail .fin-bars').waitFor();
      assert.ok(reads.some(r => r.startsWith('/api/v2/finance/lines/a-6100?') && r.includes('vendor=GitHub')), tag + ': the vendor\'s own read');
      const lineRail = await page.locator('#fin-rail').innerText();
      for (const want of ['Paid by', 'Card · Engineering', 'Checking ••0001', 'By team', 'Platform', 'Transactions']) assert.ok(lineRail.toLowerCase().includes(want.toLowerCase()), `${tag}: the vendor rail has ${want}`);
      assert.match(lineRail, /Software · \w{3} projected\s+\$3,100/, tag + ': next month projected (the line\'s)');
      assert.match(lineRail, /Seats\s+12[\s\S]*Renews\s+Jan 15/, tag + ': seats and renewal when the read has them');
      await shot(page, 'line-desktop');
      // A line with no budget of its own offers a line budget first, not a vendor one.
      await go(page, '#/finance/line/a-5200', '[data-fin-key="GitHub"]');
      await page.locator('[data-fin-line-add]').waitFor();
      assert.equal(await page.locator('[data-fin-vendor-add]').count(), 0, tag + ': no vendor budget without a line budget');
      await page.locator('[data-fin-line-add]').click();
      await page.locator('dialog.fin-dlg[open]').waitFor();
      await page.goBack();
      await page.waitForFunction(() => location.hash.endsWith('/line/a-6100') && FIN?.line?.id === 'a-6100', null, {timeout: 3000});
      assert.equal(await page.locator('dialog.fin-dlg[open]').count(), 0, tag + ': a dialog closes when the page changes');
      // Expenses view.
      await go(page, '#/finance/pnl?view=expenses', '.fin-pl');
      await page.locator('.fin-tm').waitFor();
      await shot(page, 'pnl-expenses-desktop');
      assert.equal(await page.locator('[data-fin-filter]').isVisible(), false, tag + ': on a desktop the picker is a column, not a popover');
      await page.locator('#fin-tree-col [data-fin-pick="team:eng"]').check();
      await page.waitForFunction(() => /team=eng/.test(JSON.stringify(FIN.pnl?.data ? 1 : 0)) || document.querySelector('.fin-chip'));
      assert.match(await page.locator('#fin-filters .fin-chip').innerText(), /Engineering/, tag + ': the filter chip');
      await page.keyboard.press('Escape');
      await page.locator(`td[data-fin-cell="a-6100|${future}"]`).click();
      await page.locator('td[data-fin-budget="a-6100"]').dblclick();
      await page.locator('.fin-cell-in').fill('9000');
      await page.locator('.fin-cell-in').press('Enter');
      await page.waitForFunction(() => !document.querySelector('.fin-cell-in'));
      await page.waitForTimeout(100);
      assert.deepEqual(actions(writes).at(-1), {kind: 'budget_update', payload: {budget_id: 'b-software-eng', amount: 900000, effective_from: cols[future].start}},
        tag + ': filtered to a team, the cell edits that team\'s budget');
      await page.evaluate(() => { FIN.pnl.busy = true; });
      await page.locator('td[data-fin-budget="a-6100"]').dblclick();
      assert.equal(await page.locator('.fin-cell-in').count(), 0, tag + ': no editing while the table reloads');
      await page.evaluate(() => { FIN.pnl.busy = false; });
      assert.match(await page.locator('.fin-pl a[href*="/line/a-6100"]').getAttribute('href'), /\/line\/a-6100\?teams=eng$/, tag + ': the drill-down keeps the filter');
      await page.locator(`td[data-fin-cell="a-6100|${future}"]`).click();
      await page.waitForFunction(() => document.querySelector('#fin-rail h2'));
      await page.waitForTimeout(100);
      assert.ok(reads.some(r => r.startsWith('/api/v2/finance/lines/a-6100?') && r.includes('teams=eng')), tag + ': the rail reads the line filtered');
      assert.equal(await page.locator('td[data-fin-budget="a-6300"]').count(), 0, tag + ': no editor on a cell summing two team budgets');
      assert.equal(await page.locator('td[data-fin-budget="a-5100"]').count(), 0, tag + ': no editor where the team has no budget');
      await page.locator('#fin-tree-col [data-fin-pick="mate:bot:writer"]').check();
      await page.locator('#fin-tree-col [data-fin-pick="none:"]').check();
      await page.keyboard.press('Escape');
      await page.locator('[data-fin-seg="who"] button[data-v="bots"]').click();
      await page.waitForFunction(() => FIN.pnl?.data?.filter?.who === 'bots');
      const lastPnl = new URL('http://x' + reads.filter(r => r.startsWith('/api/v2/finance/pnl?')).at(-1)).searchParams;
      assert.deepEqual([lastPnl.getAll('teams'), lastPnl.getAll('teammates'), lastPnl.get('who'), lastPnl.get('unattributed')], [['eng'], ['bot:writer'], 'bots', 'true'],
        tag + ': teams, teammates, who and Unattributed go to the route');
      assert.deepEqual(await page.locator('#fin-filters .fin-chip').allInnerTexts().then(t => t.map(x => x.replace('✕', '').trim())), ['Engineering', 'Writer', 'Unattributed'], tag + ': a chip per pick');
      assert.match(await page.locator('#fin-head [data-fin-tab="pnl"]').getAttribute('href'), /\/pnl\?view=expenses$/, tag + ': the P&L tab keeps Expenses');
      await page.locator('#fin-filters [data-fin-clear="team:eng"]').click();
      await page.waitForFunction(() => FIN.pnl?.data?.filter?.teams.length === 0);
      await shot(page, 'pnl-expenses-filter-desktop');
      // A filtered line's "All <vendor> transactions" keeps the filter; the transactions read takes it until cleared.
      await go(page, '#/finance/line/a-6100?teams=eng', '[data-fin-key="GitHub"]');
      await page.locator('[data-fin-key="GitHub"]').click();
      await page.locator('#fin-rail .fin-more').waitFor();
      const allTx = await page.locator('#fin-rail .fin-more').getAttribute('href');
      assert.match(allTx, /#\/finance\/transactions\?account_id=a-6100&q=GitHub&teams=eng$/, tag + ': All GitHub transactions keeps the filter');
      await go(page, allTx.slice(allTx.indexOf('#')), READY.transactions);
      const txRead = () => reads.filter(r => r.startsWith('/api/v2/finance/transactions?')).at(-1) || '';
      assert.match(txRead(), /q=GitHub.*teams=eng|teams=eng.*q=GitHub/, tag + ': the transactions read is filtered');
      assert.match(await page.locator('#fin-body .fin-bar .fin-chip').innerText(), /Engineering/, tag + ': the filter shows');
      await page.locator('[data-fin-org-clear]').click();
      await page.waitForFunction(() => !document.querySelector('[data-fin-org-clear]'));
      await page.waitForTimeout(100);
      assert.ok(!txRead().includes('teams='), tag + ': cleared, the read is not filtered');
      assert.match(await page.evaluate(() => location.hash), /^#\/finance\/transactions\?account_id=a-6100&q=GitHub$/, tag + ': cleared, the address drops it');
      // A filtered visit, then the bare Transactions tab: the old filter does not come back.
      await go(page, allTx.slice(allTx.indexOf('#')), READY.transactions);
      await go(page, '#/finance/transactions', READY.transactions);
      await page.waitForTimeout(100);
      assert.ok(!txRead().includes('teams=') && !(await page.locator('[data-fin-org-clear]').count()), tag + ': the bare tab is unfiltered');
      // Balance sheet, cards (freeze), transactions, sources, access.
      await go(page, '#/finance/balance', READY.balance);
      const bsFirst = await page.locator('.fin-bs tbody td:first-child').allInnerTexts();
      for (const want of ['Total assets', 'Total liabilities', 'Total equity', 'Liabilities + equity']) assert.ok(bsFirst.includes(want), `${tag}: balance sheet has ${want}`);
      assert.equal(await page.locator('#fin-balanced .pill').innerText(), 'Balanced', tag + ': balanced');
      await page.locator('[data-fin-line="a-1010"]').click();
      const bsRail = await page.locator('#fin-rail').innerText();
      assert.match(bsRail.toLowerCase(), /due out\s+checking ••0001 · 2\s+\$4,300/, tag + ': due out by money account');
      assert.match(bsRail, /Closed through\s+Sep 30/, tag + ': closed through');
      await shot(page, 'balance-desktop');
      await go(page, '#/finance/cards', READY.cards);
      await page.locator('[data-fin-card="card-1"]').click();
      await page.locator('#fin-rail [data-fin-freeze]').click();
      await page.waitForFunction(n => true, null);
      await page.waitForTimeout(100);
      assert.deepEqual(actions(writes).at(-1), {kind: 'card_freeze', payload: {card_id: 'card-1'}}, tag + ': Freeze sends card_freeze');
      await page.locator('[data-fin-card="card-2"]').click();
      assert.equal(await page.locator('#fin-rail [data-fin-freeze]').innerText(), 'Unfreeze', tag + ': a frozen card unfreezes');
      await page.locator('#fin-rail [data-fin-freeze]').click();
      await page.waitForTimeout(100);
      assert.deepEqual(actions(writes).at(-1), {kind: 'card_unfreeze', payload: {card_id: 'card-2'}}, tag + ': Unfreeze sends card_unfreeze');
      assert.deepEqual(await page.locator('#fin-rail [data-fin-saved] .fin-chip').allInnerTexts().then(t => t.map(x => x.replace('✕', '').trim())), ['Figma'], tag + ': a bot card is saved on Figma');
      await page.locator('#fin-rail [data-fin-unsave="v-figma"]').click();
      await settled(() => actions(writes).at(-1)?.kind === 'card_unsave');
      assert.deepEqual(actions(writes).at(-1), {kind: 'card_unsave', payload: {card_id: 'card-2', vendor: 'v-figma'}}, tag + ': ✕ sends card_unsave');
      await page.locator('[data-fin-card="card-4"]').click();
      await page.locator('#fin-rail .fin-rail-title').waitFor();
      assert.equal(await page.locator('#fin-rail [data-fin-freeze]').count(), 0, tag + ': a budget freeze has no Unfreeze');
      assert.match(await page.locator('[data-fin-card="card-4"]').innerText(), /Frozen · Budget/, tag + ': it says the budget froze it');
      await page.locator('[data-fin-card-add]').click();
      await page.locator('dialog.fin-dlg[open] select[name="user"] option').first().waitFor({state: 'attached'});
      assert.equal(await page.locator('dialog.fin-dlg input[name="user"]').count(), 0, tag + ': no typed Mercury user id');
      await page.locator('dialog.fin-dlg select[name="holder"]').selectOption('bot:builder');
      await page.locator('dialog.fin-dlg select[name="user"]').selectOption('mu-2');
      await page.locator('dialog.fin-dlg input[name="amount"]').fill('250');
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.waitForFunction(() => !document.querySelector('dialog.fin-dlg[open]'));
      assert.deepEqual(actions(writes).at(-1), {kind: 'card_create', payload: {holder: 'bot:builder', budget_id: 'b-software', amount: 25000, provider_user_id: 'mu-2'}},
        tag + ': card_create with the picked Mercury user');
      await page.locator('[data-fin-card="card-1"]').click();
      await shot(page, 'cards-desktop');
      await go(page, '#/finance/transactions', READY.transactions);
      // A posted bank row: one form (line, teammate, team, project), one Save, one categorize, dry run first.
      const cat = '#fin-rail [data-fin-cat]', save = cat + ' [data-fin-cat-save]';
      await page.locator(cat + ' select[name="account"] option[value="a-6100"]').waitFor({state: 'attached'});
      await shot(page, 'transactions-desktop');
      // The row's own line picker: a dry run, then categorize to the new line.
      const n0 = writes.length;
      assert.equal(await page.locator('select[data-fin-tx-line="feed:f4"]').count(), 0, tag + ': a row split by tags on one line has no row picker');
      await page.locator('select[data-fin-tx-line="feed:f1"]').selectOption('a-5100');
      await settled(() => actions(writes.slice(n0)).length >= 2);
      assert.deepEqual(actions(writes.slice(n0)).map(a => [a.kind, a.payload.feed_id, a.payload.account_id, !!a.dry_run]),
        [['categorize', 'f1', 'a-5100', true], ['categorize', 'f1', 'a-5100', false]], tag + ': a line picked in the row categorizes it');
      await page.waitForFunction(() => !FIN.tx.busy && document.querySelector('#fin-rail [data-fin-cat] select[name="account"]'));
      assert.equal(await page.locator('#fin-rail form').count(), 1, tag + ': one form on a posted row');
      assert.equal(await page.locator(cat + ' select[name="account"]').inputValue(), 'a-6100', tag + ': a posted row starts on its line');
      assert.equal(await page.locator(save).isDisabled(), true, tag + ': nothing changed, nothing to save');
      await page.locator(cat + ' select[name="team"]').selectOption('eng');
      let n = writes.length;
      await page.locator(save).click();
      await settled(() => actions(writes.slice(n)).length >= 2);
      const tagWant = {lines: [{line_id: 'l1', tags: {teammate: 'bot:eng-lead', budget: 'b-software', team: 'eng'}}], mode: 'replace'};
      assert.deepEqual(actions(writes.slice(n)), [{kind: 'categorize', payload: tagWant, dry_run: true}, {kind: 'categorize', payload: tagWant}],
        tag + ': the same line only re-tags, keeping the line\'s other tags');
      assert.notEqual(writes[n][3], writes[n + 1][3], tag + ': the dry run and the action have their own keys');
      // A new line reposts the row with its tags (the old line's budget tag stays behind).
      await page.locator(cat + ' select[name="account"]').selectOption('a-5100');
      await page.locator(cat + ' input[name="project"]').fill('Q4');
      n = writes.length;
      hub.apply = true;
      await page.locator(save).click();
      await settled(() => actions(writes.slice(n)).length >= 2);
      hub.apply = false;
      assert.deepEqual(actions(writes).at(-1), {kind: 'categorize', payload: {feed_id: 'f1', account_id: 'a-5100', tags: {teammate: 'bot:eng-lead', project: 'Q4'}}},
        tag + ': a new line sends feed_id, account_id and the tags');
      await page.waitForFunction(() => FIN.tx.rows.find(r => r.id === 'f1')?.lines[0]?.account_id === 'a-5100');
      assert.equal(await page.locator(cat + ' select[name="account"]').inputValue(), 'a-5100', tag + ': after the reload the rail shows the new line');
      assert.equal(await page.locator(save).isDisabled(), true, tag + ': and nothing left to save');
      assert.ok(!reads.some(r => r.startsWith('/api/v2/finance/feed/f1/')), tag + ': a posted row asks for no match');
      assert.equal(await page.locator('#fin-tx-match').innerHTML(), '', tag + ': no Match section on a posted row');
      // A new row: its expected payments, matched once the checked ones are one contact's and add up to the row.
      await page.locator('[data-fin-tx="feed:f2"] td').first().click();
      await page.locator('#fin-rail .fin-match').first().waitFor();
      assert.deepEqual((await page.locator('#fin-rail .fin-match').allInnerTexts()).map(t => t.replace(/\s+/g, ' ').trim()),
        ['Figma FG-1 $60 Oct 26', 'Figma FG-2 $39 Oct 27', 'Northwind Design #118 $99 Oct 27'], tag + ': the expected payments');
      const matchBtn = page.locator('#fin-rail [data-fin-match]'), tick = id => page.locator(`#fin-rail .fin-match input[value="${id}"]`).check();
      assert.equal(await matchBtn.isDisabled(), true, tag + ': Match waits for a pick');
      await tick('s-a');
      assert.equal(await matchBtn.isDisabled(), true, tag + ': $60 of $99 does not match');
      await tick('s-b');
      assert.equal(await matchBtn.isDisabled(), false, tag + ': $60 + $39 matches');
      await tick('s-c');
      assert.equal(await matchBtn.isDisabled(), true, tag + ': two contacts, or more than the row, do not');
      await page.locator('#fin-rail .fin-match input[value="s-c"]').uncheck();
      await shot(page, 'transactions-match-desktop');
      await matchBtn.click();
      for (let i = 0; i < 50 && !sent(writes).some(w => w.kind === 'feed_match'); i++) await page.waitForTimeout(50);
      assert.deepEqual(actions(writes).slice(-2), [{kind: 'feed_match', payload: {feed_id: 'f2', settlement_ids: ['s-a', 's-b']}, dry_run: true},
        {kind: 'feed_match', payload: {feed_id: 'f2', settlement_ids: ['s-a', 's-b']}}], tag + ': a dry run, then feed_match');
      // A row a rule split: Mixed where its lines differ; the same line changes only the field set.
      await page.locator('[data-fin-tx="feed:f4"] td').first().click();
      assert.equal(await page.locator('#fin-rail select[name="team"]').inputValue(), '*', tag + ': a split row\'s differing team shows Mixed');
      await page.locator('#fin-rail input[name="project"]').fill('Launch');
      n = writes.length;
      await page.locator(save).click();
      await settled(() => actions(writes.slice(n)).length >= 2);
      assert.deepEqual(actions(writes).at(-1), {kind: 'categorize', payload: {lines: [{line_id: 'l5', tags: {team: 'eng', teammate: 'bot:eng-lead', project: 'Launch'}},
        {line_id: 'l6', tags: {team: 'mkt', project: 'Launch'}}], mode: 'replace'}}, tag + ': a split row changes only the field set; each line keeps its own team');
      await page.locator('#fin-rail select[name="team"]').selectOption('');
      n = writes.length;
      await page.locator(save).click();
      await settled(() => actions(writes.slice(n)).length >= 2);
      assert.deepEqual(actions(writes).at(-1), {kind: 'categorize', payload: {lines: [{line_id: 'l5', tags: {teammate: 'bot:eng-lead'}}, {line_id: 'l6', tags: {}}], mode: 'replace'}},
        tag + ': on a mixed split, — clears the team on every line');
      // A new bank row: no line yet; Save waits for one.
      await page.locator('[data-fin-tx="feed:f2"] td').first().click();
      assert.equal(await page.locator(cat + ' select[name="account"]').inputValue(), '', tag + ': a new row has no line');
      assert.equal(await page.locator(cat + ' select[name="teammate"]').count(), 0, tag + ': a new row has no tag fields');
      assert.equal(await page.locator(save).isDisabled(), true, tag + ': Save waits for a line');
      await page.locator(cat + ' select[name="account"]').selectOption('a-6100');
      assert.equal(await page.locator(cat + ' select[name="for"]').count(), 0, tag + ': no For on a company-wide line');
      n = writes.length;
      await page.locator(save).click();
      await settled(() => actions(writes.slice(n)).length >= 2);
      const catWant = {feed_id: 'f2', account_id: 'a-6100', tags: {}};
      assert.deepEqual(actions(writes.slice(n)), [{kind: 'categorize', payload: catWant, dry_run: true}, {kind: 'categorize', payload: catWant}],
        tag + ': a new row posts to the picked line, dry run first');
      // A line that is not company-wide for the viewer asks For, the viewer first; the rules ask for approval.
      await page.locator(cat + ' select[name="account"]').selectOption('a-6300');
      assert.deepEqual((await page.locator(cat + ' select[name="for"] option').allInnerTexts()).slice(0, 2), ['Ana Silva', 'Engineering'], tag + ': For lists the viewer first');
      await page.locator(cat + ' select[name="for"]').selectOption('team:eng');
      hub.ask = 'human:ana';
      n = writes.length;
      await page.locator(save).click();
      await page.locator(cat + ' .fin-ask').waitFor();
      assert.equal(await page.locator(cat + ' .fin-ask').innerText(), 'Approval from Ana Silva', tag + ': who approves');
      await page.locator(cat + ' [data-fin-cat-no]').click();
      assert.equal(await page.locator(cat + ' select[name="for"]').inputValue(), 'team:eng', tag + ': Cancel keeps the picks');
      await page.locator(save).click();
      await page.locator(cat + ' [data-fin-cat-yes]').click();
      await settled(() => actions(writes.slice(n)).length >= 3);
      hub.ask = '';
      const forWant = {feed_id: 'f2', account_id: 'a-6300', tags: {team: 'eng'}};
      assert.deepEqual(actions(writes.slice(n)), [{kind: 'categorize', payload: forWant, dry_run: true}, {kind: 'categorize', payload: forWant, dry_run: true},
        {kind: 'categorize', payload: forWant}], tag + ': For is the line\'s tag, sent after Send, once');
      // While a dry run is out: every Save is off, and an approval asked for picks since changed is not shown.
      let release = () => {};
      hub.ask = 'human:ana'; hub.hold = new Promise(r => { release = r; });
      await page.locator(cat + ' select[name="account"]').selectOption('a-6100');
      n = writes.length;
      await page.locator(save).click();
      await settled(() => writes.length > n);
      await page.locator(cat + ' select[name="account"]').selectOption('a-5100');
      assert.equal(await page.locator(save).isDisabled(), true, tag + ': no Save while a categorize is out');
      release(); hub.hold = null;
      await page.waitForFunction(() => !FIN.tx.busy);
      assert.equal(await page.locator(cat + ' .fin-ask').count(), 0, tag + ': no approval prompt for picks changed since the dry run');
      await page.locator(save).click();
      await page.locator(cat + ' [data-fin-cat-yes]').click();
      await settled(() => actions(writes.slice(n)).length >= 3);
      hub.ask = '';
      assert.deepEqual(actions(writes.slice(n)).at(-1), {kind: 'categorize', payload: {feed_id: 'f2', account_id: 'a-5100', tags: {}}}, tag + ': Send sends what was asked about');
      assert.equal(actions(writes.slice(n)).filter(a => !a.dry_run).length, 1, tag + ': one categorize');
      // Another row's unsaved picks outlive this row's save.
      hub.hold = new Promise(r => { release = r; });
      await page.locator(cat + ' select[name="account"]').selectOption('a-6100');
      n = writes.length;
      await page.locator(save).click();
      await settled(() => writes.length > n);
      await page.locator('[data-fin-tx="feed:f1"] td').first().click();
      await page.locator(cat + ' select[name="team"]').selectOption('mkt');
      release(); hub.hold = null;
      await settled(() => actions(writes.slice(n)).length >= 2);
      await page.waitForFunction(() => !FIN.tx.busy && FIN.tx.sel === 'feed:f1');
      assert.equal(await page.locator(cat + ' select[name="team"]').inputValue(), 'mkt', tag + ': row B keeps its picks when row A saves');
      // A transfer between our own accounts has no line to pick.
      await page.locator('[data-fin-tx="feed:f7"] td').first().click();
      await page.locator('#fin-rail .fin-rail-title').waitFor();
      assert.equal(await page.locator('#fin-rail form').count(), 0, tag + ': no line picker on a transfer');
      // No chart read, no form.
      hub.noChart = true;
      await page.evaluate(() => { FIN.tx.chart = undefined; });
      await page.locator('[data-fin-tx="feed:f2"] td').first().click();
      await page.waitForFunction(() => FIN.tx.chart === false && FIN.tx.sel === 'feed:f2');
      assert.equal(await page.locator(cat).isHidden(), true, tag + ': no form when the chart read fails');
      hub.noChart = false;
      await page.evaluate(() => { FIN.tx.chart = undefined; });
      // A reconciled row keeps its line, read only; its tags still change.
      await page.locator('[data-fin-tx="feed:f5"] td').first().click();
      await page.locator('#fin-rail [data-fin-tag]').waitFor();
      assert.equal(await page.locator(cat).count(), 0, tag + ': no line picker on a reconciled row');
      assert.match(await page.locator('#fin-rail').innerText(), /Hosting\s+410\.00/, tag + ': its line, read only');
      await page.evaluate(() => { FIN.tx.busy = true; finTxRail(); });
      assert.equal(await page.locator('#fin-rail [data-fin-tag] [type=submit]').isDisabled(), true, tag + ': its Save waits while a categorize is out');
      await page.evaluate(() => { FIN.tx.busy = false; finTxRail(); });
      await page.evaluate(() => { FIN_TX.q = ''; FIN_TX.account = ''; });
      await go(page, '#/finance/cards', READY.cards);
      await go(page, '#/finance/transactions', READY.transactions);
      await page.locator(cat + ' select[name="account"] option[value="a-6100"]').waitFor({state: 'attached'});
      await page.locator(cat + ' select[name="team"]').selectOption('eng');
      await page.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));
      await shot(page, 'transactions-categorize-desktop');
      await page.locator('[data-fin-seg="source"] button[data-v="transfer"]').click();
      await page.locator('#fin-rail [data-fin-xfer-cancel]').waitFor();
      await page.locator('#fin-rail [data-fin-xfer-cancel]').click();
      await settled(() => actions(writes).at(-1)?.kind === 'payment_cancel');
      assert.deepEqual(actions(writes).at(-1), {kind: 'payment_cancel', payload: {transfer_id: 'xf-1'}}, tag + ': Cancel transfer sends payment_cancel');
      await shot(page, 'transfers-desktop');
      await page.evaluate(() => { FIN_TX.source = 'all'; });
      await go(page, '#/finance/tax', READY.tax);
      assert.deepEqual(await page.locator('#fin-tax thead th').allInnerTexts(), ['QUARTER', 'JURISDICTION', 'TAXABLE', 'EXEMPT', 'COLLECTED', 'REMITTED', 'DUE', 'DUE ON'], tag + ': the sales tax columns');
      await shot(page, 'tax-desktop');
      await page.locator('[data-fin-seg="tax"] button[data-v="1099"]').click();
      await page.locator('tr[data-fin-tax="v-northwind"]').waitFor();
      assert.match(await page.locator('tr[data-fin-tax="v-northwind"]').innerText(), /Northwind Design\s+Missing\s+6,100/, tag + ': a 1099 vendor without a TIN');
      await go(page, '#/finance/sources', READY.sources);
      // Groups start collapsed, so the button expands them all; once any is open it collapses them all.
      const fold = page.locator('[data-fin-collapse]');
      assert.equal((await fold.innerText()).trim(), 'Expand all', tag + ': all collapsed, Expand all');
      await fold.click();
      assert.equal(await page.locator('[data-fin-group][aria-expanded="false"]').count(), 0, tag + ': Expand all opens every group');
      assert.equal((await fold.innerText()).trim(), 'Collapse all', tag + ': open, Collapse all');
      await fold.click();
      assert.equal(await page.locator('[data-fin-group][aria-expanded="true"]').count(), 0, tag + ': Collapse all shuts every group');
      assert.equal((await fold.innerText()).trim(), 'Expand all', tag + ': shut again, Expand all');
      await page.locator('[data-fin-group="banks"]').click();
      assert.equal((await fold.innerText()).trim(), 'Collapse all', tag + ': one group open, Collapse all');
      await page.locator('[data-fin-q]').fill('mercury');
      assert.equal(await fold.isVisible(), false, tag + ': no toggle while searching');
      await page.locator('[data-fin-q]').fill('');
      assert.equal(await fold.isVisible(), true, tag + ': toggle back once the search clears');
      await page.locator('[data-fin-src="bank:m1"]').click();
      await page.locator('#fin-rail select[data-fin-map="m1"]').waitFor();
      const mapTo = await page.locator('#fin-rail select[data-fin-map="m1"] option').evaluateAll(o => o.map(x => x.value));
      assert.deepEqual(mapTo, ['a-1000', 'a-1010', 'a-1300'], tag + ': asset lines only, none another account books to');
      await shot(page, 'sources-desktop');
      await page.locator('#fin-rail select[data-fin-map="m1"]').selectOption('a-1000');
      await page.waitForTimeout(100);
      assert.deepEqual(actions(writes).at(-1), {kind: 'connection_map', payload: {money_account_id: 'm1', account_id: 'a-1000'}}, tag + ': mapping sends connection_map');
      await page.locator('#fin-rail [data-fin-sync]').click();
      await page.waitForTimeout(100);
      assert.deepEqual(actions(writes).at(-1), {kind: 'connection_sync', payload: {connection_id: 'c1'}}, tag + ': Sync now sends connection_sync');
      assert.equal(await page.locator('#fin-rail [data-fin-disconnect]').count(), 0, tag + ': Disconnect is on the connection, not an account');
      await page.locator('[data-fin-group="payments"]').click();
      await page.locator('[data-fin-src="connection:c1"]').click();
      await page.locator('#fin-rail [data-fin-disconnect]').click();
      await page.locator('dialog.fin-dlg[open] [type=submit]').click();
      await page.waitForFunction(() => !document.querySelector('dialog.fin-dlg[open]'));
      assert.deepEqual(actions(writes).at(-1), {kind: 'connection_disconnect', payload: {connection_id: 'c1'}}, tag + ': Disconnect sends connection_disconnect');
      await go(page, '#/finance/access', READY.access);
      // The owner first, then teams in org chart order, those on no team under their own heading, then the helpers;
      // the rail opens on the owner.
      assert.deepEqual(await page.locator('.fin-acc-t tbody tr').evaluateAll(rs => rs.map(r => r.dataset.finMate || '# ' + r.innerText.trim())),
        ['human:ana', '# Engineering', 'human:cara', '# Platform', 'bot:eng-lead', 'bot:builder', '# Web', '# Marketing', 'bot:mkt-lead', '# Content', 'bot:writer',
          '# No team', 'human:erin', '# Helpers', 'bot:bookkeeper'], tag + ': the Access order');
      // A manager's default names the one team it covers; one spanning several teams stays generic.
      const dflt = async who => (await page.locator(`tr[data-fin-mate="${who}"] .fin-s`).innerText()).trim();
      assert.equal(await dflt('human:cara'), 'Engineering expenses', tag + ': a manager of one team');
      assert.equal(await dflt('bot:mkt-lead'), 'Marketing expenses', tag + ': a manager of one team and its reports');
      assert.equal(await dflt('bot:eng-lead'), 'Team expenses', tag + ': a manager of several teams');
      await page.waitForFunction(() => FIN_ACC.sel === 'human:ana' && document.querySelector('#fin-rail h2'));
      assert.equal(await page.locator('#fin-rail [data-fin-grant]').count(), 0, tag + ': nobody grants to themselves');
      assert.equal(await page.locator('tr[data-fin-mate="human:cara"] .fin-s.mgr').count(), 1, tag + ': a manager sees team expenses');
      assert.equal(await page.locator('#fin-private .fin-chip').count(), 2, tag + ': payroll by person is private by default, beside the private lines');
      await page.locator('[data-fin-mate="bot:writer"]').click();
      await page.locator('#fin-rail [data-fin-grant]').waitFor();
      assert.equal(await page.locator('#fin-rail .fin-rail-title .muted').count(), 0, tag + ': no subtitle under the name');
      assert.match(await page.locator('#fin-rail').innerText(), /Team\s+Marketing › Content[\s\S]*Kind\s+Bot[\s\S]*Reports to\s+Marketing Lead/i, tag + ': team, kind and manager as rows');
      assert.match(await page.locator('#fin-private').innerText(), /Payroll\s+· Totals only · Full detail: Bookkeeper/, tag + ': the private line\'s policy');
      await page.locator('#fin-rail select[name="what"]').selectOption('bs_lines');
      await page.locator('#fin-rail select[name="line"] option[value="a-1010"]').waitFor({state: 'attached'});
      assert.ok(reads.filter(r => r === '/api/v2/finance/balance-sheet').length >= 2, tag + ': Access reads the balance sheet lines itself');
      await page.locator('#fin-rail select[name="what"]').selectOption('expenses');
      await shot(page, 'access-desktop');
      await page.locator('[data-fin-defaults]').click();
      await page.locator('dialog.fin-dlg[open]').waitFor();
      await shot(page, 'access-defaults-desktop');
      assert.deepEqual(await page.locator('dialog.fin-dlg select[name="manager"] option').evaluateAll(o => o.map(x => x.value)), ['team_expenses', 'own_expenses'], tag + ': managers see expenses');
      await page.locator('dialog.fin-dlg select[name="manager_payroll"]').selectOption('hidden');
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.waitForFunction(() => !document.querySelector('dialog.fin-dlg[open]'));
      assert.deepEqual(actions(writes).at(-1), {kind: 'view_defaults_set', payload: {manager_payroll: 'hidden'}}, tag + ': view_defaults_set with what changed');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // The owner's Payables and Receivables: plain rows and no totals row; each decision is a dry run, then the action
    // (one the rules send for approval names the approver first); the Balance sheet's "+ Add" posts a journal entry.
    {
      const tag = 'payables';
      const {page, errors, writes, reads} = await open(browser, {hash: '#/finance/payables'});
      const today = await page.evaluate(() => finToday());
      const heads = async () => (await page.locator('.fin-doc-t').first().locator('thead th').allInnerTexts()).map(t => t.toUpperCase());
      await page.locator(READY.payables).waitFor();
      assert.deepEqual(await heads(), ['VENDOR', 'BILL', 'DUE', 'AMOUNT', 'ACCOUNT', 'TEAM', 'STATUS', 'CAME IN'], tag + ': the bill columns');
      const unpaid = BILLS.filter(r => r.status !== 'paid');
      assert.equal(await page.locator('.fin-doc-t').first().locator('tbody tr').count(), unpaid.length, tag + ': one row per open bill, no totals row');
      await page.locator('tr[data-fin-doc="bill-7"]').waitFor();
      assert.deepEqual((await page.locator('tr[data-fin-doc="bill-7"] td').allInnerTexts()).map(t => t.trim()), ['AWS', 'Oct 27', '3,912.08', 'Hosting', 'Engineering', await page.evaluate(() => finWho('human:ana'))],
        tag + ': Recently paid: vendor, day, amount, line, team, who');
      assert.ok(reads.some(r => r.startsWith('/api/v2/finance/bills?') && r.includes('status=paid') && r.includes('order=paid_on') && r.includes('limit=5')),
        tag + ': Recently paid asks for the last five paid, by payment date');
      const statusOf = async id => (await page.locator(`tr[data-fin-doc="${id}"] td:nth-child(7)`).innerText()).replace(/\s+/g, ' ').trim();
      assert.equal(await statusOf('bill-3'), 'Approve in Mercury pays Nov 11', tag + ': a bill paying now shows its payment\'s state and day');
      assert.equal(await statusOf('bill-4'), 'Open Pay cancelled 1–30 late', tag + ': a rejected payment shows beside Open, with its aging');
      assert.equal(await statusOf('bill-5'), 'Open Pay denied', tag + ': a declined approval shows beside Open');
      assert.ok(!(await page.locator('#fin-body').innerText()).includes('5,130'), tag + ': the open total is not drawn');
      assert.match(await page.locator('[data-fin-email-set]').innerText(), /bills@acme\.example$/, tag + ': the owner\'s bills address control');
      // The owner sets the address: an inbox address saves; any other saves but says it is not an inbox's; empty clears it.
      const setEmail = async value => {
        await page.locator('[data-fin-email-set]').click();
        await page.locator('dialog.fin-dlg[open] input[name="address"]').fill(value);
        await page.locator('dialog.fin-dlg[open] [type=submit]').click();
      };
      await setEmail('ap@acme.example');
      await page.locator('dialog.fin-dlg[open] [data-error]:not([hidden])').waitFor();
      assert.equal(await page.locator('dialog.fin-dlg [data-error]').innerText(), 'Not an inbox address', tag + ': an address no inbox has is named');
      assert.deepEqual(writes.at(-1).slice(0, 3), ['PUT', '/api/v2/finance/bills-address', {address: 'ap@acme.example'}], tag + ': PUT the address');
      await page.locator('dialog.fin-dlg[open] input[name="address"]').fill('bills@acme.example');
      await page.locator('dialog.fin-dlg[open] [type=submit]').click();
      await page.waitForFunction(() => !document.querySelector('dialog.fin-dlg[open]'));
      assert.deepEqual(writes.at(-1).slice(0, 3), ['PUT', '/api/v2/finance/bills-address', {address: 'bills@acme.example'}], tag + ': an inbox address saves');
      await setEmail('');
      await page.waitForFunction(() => !document.querySelector('dialog.fin-dlg[open]'));
      assert.deepEqual(writes.at(-1).slice(0, 3), ['PUT', '/api/v2/finance/bills-address', {address: null}], tag + ': empty clears it');
      assert.equal((await page.locator('[data-fin-email-set]').innerText()).trim(), 'Bills address', tag + ': no address: a Bills address button');
      await setEmail('bills@acme.example');
      await page.waitForFunction(() => !document.querySelector('dialog.fin-dlg[open]'));
      await page.locator('#fin-rail [data-fin-do="approve"]').waitFor();
      const rail = await page.locator('#fin-rail').innerText();
      assert.match(rail, /Lines[\s\S]*Software · Seats[\s\S]*Files[\s\S]*Approvals\s+Approve\s+Waiting Ana Silva/i, tag + ': lines, files and approvals');
      assert.equal(await page.locator('#fin-rail a[href="/api/v2/files/file-1"]').count(), 1, tag + ': the bill\'s file links to the Library');
      await shot(page, 'payables-desktop');
      await page.locator('#fin-rail [data-fin-do="approve"]').click();
      await page.waitForFunction(() => !document.querySelector('#fin-rail [data-fin-do="approve"]:disabled'));
      await page.waitForTimeout(100);
      assert.deepEqual(actions(writes).slice(-2), [{kind: 'bill_approve', payload: {bill_id: 'bill-1'}, dry_run: true}, {kind: 'bill_approve', payload: {bill_id: 'bill-1'}}],
        tag + ': Approve is a dry run, then bill_approve');
      // Schedule: the rules ask the owner, so the dialog says so and the second Save sends it.
      await page.locator('tr[data-fin-doc="bill-2"]').click();
      await page.locator('#fin-rail [data-fin-do="schedule"]').click();
      await page.locator('dialog.fin-dlg[open] select[name="from"] option[value="m1"]').waitFor({state: 'attached'});
      assert.deepEqual(await page.locator('dialog.fin-dlg select[name="from"] option').allInnerTexts(), ['Checking ••0001', 'Savings ••0002'], tag + ': the accounts to pay from');
      const payOn = await page.locator('dialog.fin-dlg input[name="on"]').inputValue();
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.locator('dialog.fin-dlg [data-ask]:not([hidden])').waitFor();
      assert.equal(await page.locator('dialog.fin-dlg [data-ask]').innerText(), 'Approval from Ana Silva', tag + ': the approver is named before sending');
      assert.equal(sent(writes).filter(a => a.kind === 'bill_schedule').length, 0, tag + ': nothing sent by the dry run');
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.waitForFunction(() => !document.querySelector('dialog.fin-dlg[open]'));
      assert.deepEqual(sent(writes).at(-1), {kind: 'bill_schedule', payload: {bill_id: 'bill-2', pay_on: payOn, money_account_id: 'm1'}}, tag + ': bill_schedule from the bank picked');
      assert.equal(actions(writes).filter(a => a.kind === 'bill_schedule' && a.dry_run).length, 1, tag + ': one dry run');
      await page.locator('tr[data-fin-doc="bill-4"]').click();
      await page.locator('#fin-rail [data-fin-do="paid"]').waitFor();
      assert.match(await page.locator('#fin-rail').innerText(), /Payment\s+Pay cancelled rejected in Mercury/, tag + ': the rail says why it was not paid');
      await page.locator('#fin-rail [data-fin-do="paid"]').click();
      assert.equal(await page.locator('dialog.fin-dlg input[name="amount"]').inputValue(), '1390', tag + ': Mark paid starts at what is left');
      await page.locator('dialog.fin-dlg[open] select[name="from"] option[value="m1"]').waitFor({state: 'attached'});
      assert.deepEqual(await page.locator('dialog.fin-dlg select[name="from"] option').allInnerTexts(), ['Checking ••0001', 'Savings ••0002', 'Elsewhere'], tag + ': paid from a bank account or elsewhere');
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.waitForFunction(() => !document.querySelector('dialog.fin-dlg[open]'));
      assert.deepEqual(sent(writes).at(-1), {kind: 'bill_mark_paid', payload: {bill_id: 'bill-4', amount: 139000, date: today, money_account_id: 'm1'}}, tag + ': bill_mark_paid');
      await page.locator('tr[data-fin-doc="bill-3"]').click();
      await page.locator('#fin-rail [data-fin-do="paid"]').waitFor();
      assert.deepEqual(await page.locator('#fin-rail [data-fin-do]').allInnerTexts(), ['Void', 'Mark paid'], tag + ': a scheduled bill is paid or voided, not scheduled again');
      assert.match(await page.locator('#fin-rail').innerText(), /Payment\s+Approve in Mercury/, tag + ': the rail shows the payment\'s state');
      await page.locator('tr[data-fin-doc="bill-6"]').click();
      await page.locator('#fin-rail [data-fin-do="schedule"]').waitFor();
      assert.deepEqual(await page.locator('#fin-rail [data-fin-do]').allInnerTexts(), ['Void', 'Mark paid', 'Reschedule'], tag + ': a scheduled bill with no payment yet moves');
      await page.locator('#fin-rail [data-fin-do="schedule"]').click();
      await page.locator('dialog.fin-dlg[open] select[name="from"] option[value="m1"]').waitFor({state: 'attached'});
      assert.equal(await page.locator('dialog.fin-dlg input[name="on"]').inputValue(), '2099-12-18', tag + ': Reschedule starts at its day');
      assert.equal(await page.locator('dialog.fin-dlg select[name="from"]').inputValue(), 'm2', tag + ': Reschedule keeps its bank account');
      await page.locator('dialog.fin-dlg input[name="on"]').fill('2099-12-20');
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.locator('dialog.fin-dlg [data-ask]:not([hidden])').waitFor();
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.waitForFunction(() => !document.querySelector('dialog.fin-dlg[open]'));
      assert.deepEqual(sent(writes).at(-1), {kind: 'bill_schedule', payload: {bill_id: 'bill-6', pay_on: '2099-12-20', money_account_id: 'm2'}}, tag + ': Reschedule is bill_schedule');
      // + Bill naming an existing vendor in another case picks that vendor; no second contact is made.
      await page.locator('[data-fin-doc-add]').click();
      await page.locator('dialog.fin-dlg select[name="contact"]').selectOption('+');
      await page.locator('dialog.fin-dlg input[name="name"]').fill('figma');
      await page.locator('dialog.fin-dlg select[name="line"]').selectOption('a-6100');
      await page.locator('dialog.fin-dlg input[name="amount"]').fill('12');
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.waitForFunction(() => !document.querySelector('dialog.fin-dlg[open]'));
      assert.deepEqual(sent(writes).at(-1), {kind: 'bill_create', payload: {vendor_id: 'v-figma', bill_date: today, lines: [{account_id: 'a-6100', amount: 1200}], origin: 'manual'}},
        tag + ': an existing name picks its contact');
      assert.equal(sent(writes).filter(a => a.kind === 'contact_create').length, 0, tag + ': no contact_create for a name that exists');
      // + Bill for a new vendor: the vendor first, then the bill under its id.
      await page.locator('[data-fin-doc-add]').click();
      await page.locator('dialog.fin-dlg select[name="contact"]').selectOption('+');
      await page.locator('dialog.fin-dlg input[name="name"]').fill('Acme Paper');
      await page.locator('dialog.fin-dlg input[name="number"]').fill('AP-1');
      assert.deepEqual(await page.locator('dialog.fin-dlg select[name="line"] option').allInnerTexts(), ['Checking', 'Savings', 'Prepaid expenses', 'Cost of revenue', 'Hosting', 'AI services', 'Operating expenses', 'Software', 'Contractors'],
        tag + ': a bill\'s lines, never payables or receivables');
      await page.locator('dialog.fin-dlg select[name="line"]').selectOption('a-6100');
      await page.locator('dialog.fin-dlg input[name="amount"]').fill('45.50');
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.waitForFunction(() => !document.querySelector('dialog.fin-dlg[open]'));
      assert.deepEqual(sent(writes).slice(-2), [{kind: 'contact_create', payload: {name: 'Acme Paper', kind: 'vendor'}},
        {kind: 'bill_create', payload: {vendor_id: 'v-new', bill_date: today, lines: [{account_id: 'a-6100', amount: 4550}], number: 'AP-1', origin: 'manual'}}],
        tag + ': contact_create, then bill_create');
      // The new draft shows: the list turns to Draft and asks for it.
      await page.waitForFunction(() => document.querySelector('[data-fin-seg="status"] button[data-v="draft"]').getAttribute('aria-checked') === 'true');
      assert.ok(reads.some(r => r.startsWith('/api/v2/finance/bills?') && r.includes('status=draft')), tag + ': the list reads drafts after a new bill');
      // Receivables.
      await go(page, '#/finance/receivables', READY.receivables);
      assert.deepEqual(await heads(), ['CUSTOMER', 'INVOICE', 'SENT', 'DUE', 'AMOUNT', 'STATUS'], tag + ': the invoice columns');
      assert.equal(await page.locator('.fin-doc-t tbody tr').count(), INVOICES.length, tag + ': one row per invoice, no totals row');
      await page.locator('[data-fin-seg="status"] button[data-v="late"]').click();
      await page.waitForFunction(() => document.querySelectorAll('.fin-doc-t tbody tr').length === 1);
      assert.ok(reads.some(r => r.startsWith('/api/v2/finance/invoices?') && r.includes('status=unpaid') && r.includes('late=true')), tag + ': Late reads open invoices past due');
      await page.locator('[data-fin-seg="status"] button[data-v="unpaid"]').click();
      await page.waitForFunction(n => document.querySelectorAll('.fin-doc-t tbody tr').length === n, INVOICES.length);
      assert.equal(await page.locator('#fin-body button, #fin-body a').filter({hasText: /remind|chase/i}).count(), 0, tag + ': no reminders');
      await page.locator('tr[data-fin-doc="inv-2"]').click();
      await page.locator('#fin-rail [data-fin-do="paid"]').click();
      await page.locator('dialog.fin-dlg[open] select[name="from"] option[value="m1"]').waitFor({state: 'attached'});
      await page.locator('dialog.fin-dlg select[name="from"]').selectOption('');
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.waitForFunction(() => !document.querySelector('dialog.fin-dlg[open]'));
      assert.deepEqual(sent(writes).at(-1), {kind: 'invoice_record_payment', payload: {invoice_id: 'inv-2', amount: 150000, date: today}}, tag + ': Elsewhere sends no account');
      await page.locator('tr[data-fin-doc="inv-3"]').click();
      await page.locator('#fin-rail [data-fin-do="writeoff"]').waitFor();
      assert.match(await page.locator('#fin-rail').innerText(), /Left\s+\$2,200[\s\S]*Payments\s+Oct 25 · Bank row\s+\$600\s+Oct 29 · Expected\s+\$400/i, tag + ': what is left, what was paid, what is expected');
      await shot(page, 'receivables-desktop');
      await page.locator('#fin-rail [data-fin-do="writeoff"]').click();
      await page.locator('dialog.fin-dlg input[name="memo"]').fill('Settled');
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.waitForFunction(() => !document.querySelector('dialog.fin-dlg[open]'));
      assert.deepEqual(sent(writes).at(-1), {kind: 'invoice_write_off', payload: {invoice_id: 'inv-3', amount: 220000, memo: 'Settled'}}, tag + ': invoice_write_off');
      // The Balance sheet's "+ Add".
      await go(page, '#/finance/balance', READY.balance);
      await page.locator('[data-fin-add]').click();
      assert.deepEqual(await page.locator('#fin-add [role=menuitem]').allInnerTexts(), ['Bank account', 'Card account', 'Payments', 'Invoice', 'Bill', 'Loan or credit line', 'Asset', 'Journal entry', 'Import history'],
        tag + ': what the owner may add');
      await shot(page, 'balance-add-desktop');
      await page.locator('[data-fin-add-item="journal"]').click();
      const jl = page.locator('dialog.fin-dlg [data-fin-jl] .fin-jl');
      await jl.nth(0).locator('select').selectOption('a-6100');
      await jl.nth(0).locator('[name=dr]').fill('100');
      await jl.nth(1).locator('select').selectOption('a-1010');
      await jl.nth(1).locator('[name=cr]').fill('90');
      await page.locator('dialog.fin-dlg [type=submit]').click();
      assert.match(await page.locator('dialog.fin-dlg [data-error]').innerText(), /differ by \$10/, tag + ': an unbalanced entry is refused');
      await jl.nth(1).locator('[name=cr]').fill('100');
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.waitForFunction(() => !document.querySelector('dialog.fin-dlg[open]'));
      assert.deepEqual(sent(writes).at(-1), {kind: 'journal', payload: {date: today, lines: [{account_id: 'a-6100', amount: 10000}, {account_id: 'a-1010', amount: -10000}]}},
        tag + ': journal with balanced lines');
      const keys = writes.map(w => w[3]);
      assert.ok(keys.every(Boolean) && new Set(keys).size === keys.length, tag + ': a fresh Idempotency-Key per write');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A payables grant without the right: Payables only, read only; Receivables stays hidden, even by address.
    {
      const tag = 'payables grant';
      const {page, errors, writes, reads} = await open(browser, {viewer: 'cara', apGrant: true, hash: '#/finance/payables'});
      await page.locator(READY.payables).waitFor();
      assert.deepEqual(await tabs(page), ['P&L', 'Payables', 'Cards', 'Transactions', 'Access'], tag + ': Payables, not Receivables');
      await page.locator('#fin-rail .fin-h').first().waitFor();
      assert.equal(await page.locator('[data-fin-do], [data-fin-doc-add], [data-fin-email-set]').count(), 0, tag + ': no decisions without finance.ap');
      assert.equal(await page.locator('[data-fin-copy]').getAttribute('data-fin-copy'), 'bills@acme.example', tag + ': the bills address, copyable');
      await page.evaluate(() => { location.hash = '#/finance/receivables'; });
      await page.waitForFunction(() => location.hash.startsWith('#/finance/pnl'));
      await page.locator('.fin-pl').waitFor();
      await go(page, '#/finance/balance', '.fin-pl');
      assert.ok(!reads.some(r => r.startsWith('/api/v2/finance/invoices')), tag + ': invoices never read');
      assert.equal(writes.length, 0, tag + ': nothing written');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // finance.ap without finance.pay: bills are approved, paid and voided, never scheduled; no bank list is read.
    {
      const tag = 'ap right';
      const {page, errors, reads, writes} = await open(browser, {viewer: 'cara', apGrant: true, apRights: ['finance.ap'], hash: '#/finance/payables'});
      await page.locator('#fin-rail [data-fin-do="approve"]').waitFor();
      await page.locator('tr[data-fin-doc="bill-2"]').click();
      await page.locator('#fin-rail [data-fin-do="paid"]').waitFor();
      assert.deepEqual(await page.locator('#fin-rail [data-fin-do]').allInnerTexts(), ['Void', 'Mark paid'], tag + ': no Schedule without finance.pay');
      await page.locator('#fin-rail [data-fin-do="paid"]').click();
      assert.deepEqual(await page.locator('dialog.fin-dlg[open] select[name="from"] option').allInnerTexts(), ['Elsewhere'], tag + ': Mark paid offers only Elsewhere');
      await page.locator('dialog.fin-dlg[open] [data-close], dialog.fin-dlg[open] button.ghost').first().click();
      await page.waitForFunction(() => !document.querySelector('dialog.fin-dlg[open]'));
      await page.locator('[data-fin-doc-add]').click();
      await page.locator('dialog.fin-dlg[open] select[name="line"] option[value="a-6100"]').waitFor({state: 'attached'});
      assert.ok(reads.includes('/api/v2/finance/chart?for=bill'), tag + ': the bill form reads the chart for bills');
      assert.ok(!reads.some(r => r.startsWith('/api/v2/finance/pay-from')), tag + ': pay-from never read');
      // Software is not company-wide for a manager: the bill names whom it is for from the chart's attributable tags,
      // the manager first, then teams, then teammates. Checking is, so it asks no one.
      const forPick = page.locator('dialog.fin-dlg[open] [data-fin-for]');
      await page.locator('dialog.fin-dlg select[name="line"]').selectOption('a-1010');
      assert.equal(await forPick.isHidden(), true, tag + ': a company-wide line asks no one');
      await page.locator('dialog.fin-dlg select[name="line"]').selectOption('a-6100');
      assert.deepEqual(await forPick.locator('option').allInnerTexts(), ['Cara Lee', 'Engineering', 'Platform', 'Builder', 'Engineering Lead'], tag + ': whom the manager may attribute to');
      assert.equal(await forPick.locator('select').inputValue(), 'teammate:human:cara', tag + ': their own scope first');
      await page.locator('dialog.fin-dlg input[name="amount"]').fill('80');
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.waitForFunction(() => !document.querySelector('dialog.fin-dlg[open]'));
      const billOf = () => writes.map(w => w[2]).filter(b => b?.kind === 'bill_create' && !b.dry_run).at(-1)?.payload.lines;
      assert.deepEqual(billOf(), [{account_id: 'a-6100', amount: 8000, tags: {teammate: 'human:cara'}}], tag + ': the line tagged to the manager');
      await page.locator('[data-fin-doc-add]').click();
      await page.locator('dialog.fin-dlg[open] select[name="line"] option[value="a-6100"]').waitFor({state: 'attached'});
      await page.locator('dialog.fin-dlg select[name="line"]').selectOption('a-6100');
      await forPick.locator('select').selectOption('team:platform');
      await page.locator('dialog.fin-dlg input[name="amount"]').fill('80');
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.waitForFunction(() => !document.querySelector('dialog.fin-dlg[open]'));
      assert.deepEqual(billOf(), [{account_id: 'a-6100', amount: 8000, tags: {team: 'platform'}}], tag + ': or to a team they lead');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A company with no revenue lines: projected net income is minus the expenses, not blank.
    {
      const tag = 'no revenue';
      const {page, errors} = await open(browser, {noRevenue: true});
      await page.locator('.fin-pl').waitFor();
      const net = await page.locator('.fin-pl tr.fin-tot td').allInnerTexts();
      const cols = await page.evaluate(() => FIN.pnl.data.columns);
      const future = cols.map((c, i) => c.state === 'future' ? net[i + 1] : null).filter(v => v != null);
      assert.equal(net[0], 'Net income', tag + ': the bottom line');
      assert.ok(future.length && future.every(v => /^\(?−?[\d,]+\)?$/.test(v) && v !== '0'), tag + ': projected net income has figures: ' + future.join(' '));
      assert.ok(net[cols.length + 1] !== '', tag + ': the net budget has a figure');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // Revenue projected, cost of revenue not: gross profit is blank and so is gross margin (never 0%).
    {
      const tag = 'no cogs projection';
      const {page, errors} = await open(browser, {noCogsProjection: true});
      await page.locator('.fin-pl').waitFor();
      const cols = await page.evaluate(() => FIN.pnl.data.columns);
      const row = async name => page.locator('.fin-pl tbody tr', {has: page.locator('td:first-child', {hasText: new RegExp('^' + name)})}).first().locator('td').allInnerTexts();
      const gp = await row('Gross profit'), gm = await row('Gross margin');
      const futures = cols.map((c, i) => c.state === 'future' ? i + 1 : -1).filter(i => i > 0);
      assert.ok(futures.length, tag + ': there are projected columns');
      for (const i of futures) assert.deepEqual([gp[i], gm[i]], ['', ''], `${tag}: column ${i} has no gross profit and no margin`);
      assert.ok(gm.slice(1, futures[0]).some(v => /%$/.test(v)), tag + ': past months still show a margin');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // The owner on a phone: each tab fits, the rail under the table.
    {
      const tag = 'owner phone';
      const {page, errors} = await open(browser, {viewport: PHONE});
      for (const tab of Object.keys(READY)) {
        await go(page, '#/finance/' + tab, READY[tab]);
        await page.waitForTimeout(50);
        assert.equal(await sideways(page), false, `${tag}: ${tab} has no sideways scroll`);
        await shot(page, tab + '-phone');
      }
      await go(page, '#/finance/line/a-6100', '[data-fin-key]');
      assert.equal(await sideways(page), false, tag + ': the line page fits');
      await shot(page, 'line-phone');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // Back from a books consent that needs a new grant: Sources, one "Reconnect books", and a clean address.
    {
      const tag = 'books return';
      const {page, errors} = await open(browser, {hash: '#/finance?books=error&reason=reconnect_required'});
      await page.waitForFunction(() => location.hash === '#/finance/sources');
      await page.locator(READY.sources).first().waitFor();
      assert.match(await page.locator('.toast').first().innerText(), /Reconnect books/, tag + ': says to reconnect');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // Budget figures without payroll say so wherever they show, as the P&L's totals do: the budget column, the line
    // page's budget column and rail, and what an approval would leave in a budget.
    {
      const tag = 'budgets excl. payroll';
      const {page, errors, hub} = await open(browser, {budgetsExcl: true});
      await page.locator('.fin-pl').waitFor();
      assert.match(await page.locator('.fin-pl thead th.fin-b', {hasText: 'budget'}).first().textContent(), /budget excl\. payroll$/, tag + ': the budget column');
      await page.locator('a[href$="/line/a-6100"]').click();
      await page.locator('[data-fin-key="GitHub"]').waitFor();
      assert.ok((await page.locator('#fin-line thead th').allTextContents()).includes('Budget / mo excl. payroll'), tag + ': the line page\'s budget column');
      await page.locator('[data-fin-key="GitHub"]').click();
      await page.locator('#fin-rail .fin-bars').waitFor();
      assert.match(await page.locator('#fin-rail').innerText(), /Budget excl\. payroll\s+\$900/, tag + ': the line rail');
      await go(page, '#/finance/balance', READY.balance);
      await page.locator('[data-fin-add]').click();
      await page.locator('[data-fin-add-item="journal"]').click();
      const jl = page.locator('dialog.fin-dlg [data-fin-jl] .fin-jl');
      await jl.nth(0).locator('select').selectOption('a-6100');
      await jl.nth(0).locator('[name=dr]').fill('100');
      await jl.nth(1).locator('select').selectOption('a-1010');
      await jl.nth(1).locator('[name=cr]').fill('100');
      hub.ask = 'human:ana';
      hub.impact = {budgets: [{budget_id: 'b-software', name: 'Software', remaining: 60000, remaining_after: 50000, excluded: true}]};
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.locator('dialog.fin-dlg .fin-ask:not([hidden])').waitFor();
      assert.match(await page.locator('dialog.fin-dlg [data-impact]').innerText(), /Software left excl\. payroll\s+\$600\s+→\s+\$500/, tag + ': approval impact');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A manager: their teams' expenses; Balance sheet and Sources hidden, even by address.
    {
      const tag = 'manager';
      const {page, errors, writes} = await open(browser, {viewer: 'cara', hash: '#/finance'});
      await page.waitForFunction(() => location.hash === '#/finance/pnl?view=expenses');
      await page.locator('.fin-pl').waitFor();
      assert.deepEqual(await tabs(page), ['P&L', 'Cards', 'Transactions', 'Access'], tag + ': only the tabs in view');
      assert.deepEqual(await page.locator('[data-fin-seg="view"] button').allInnerTexts(), ['Expenses'], tag + ': no Company view');
      assert.equal(await page.locator('td[data-fin-budget]').count(), 0, tag + ': no budget editing without the right');
      assert.equal(await page.locator('.fin-pl a[href*="/line/"]').count() > 0, true, tag + ': lines drill down');
      const priv = page.locator('.fin-ln:has-text("Payroll (private)")');
      assert.equal(await priv.locator('a').count(), 0, tag + ': a private line does not drill down');
      assert.deepEqual((await priv.locator('td[data-fin-cell]').allInnerTexts()).filter(t => t !== '—'), [], tag + ': a private line shows no figures');
      const firsts = await page.locator('.fin-pl tbody td:first-child').allInnerTexts();
      assert.ok(firsts.includes('Total expenses excl. payroll'), tag + ': totals without payroll say so');
      for (const hidden of ['balance', 'sources', 'payables', 'receivables']) {
        await page.evaluate(h => { location.hash = h; }, '#/finance/' + hidden);
        await page.waitForFunction(() => location.hash.startsWith('#/finance/pnl'));
        await page.locator('.fin-pl').waitFor();
      }
      assert.equal(writes.filter(w => w[1].includes('balance-sheet') || w[1].includes('sources')).length, 0, tag + ': nothing written');
      await go(page, '#/finance/cards', READY.cards);
      assert.equal(await page.locator('[data-fin-card-add]').count(), 0, tag + ': no card create without the right');
      await page.locator('[data-fin-card="card-1"]').click();
      assert.equal(await page.locator('#fin-rail [data-fin-freeze]').count(), 0, tag + ': no freeze without the right');
      await go(page, '#/finance/transactions', READY.transactions);
      await page.locator('[data-fin-tx="feed:f2"] td').first().click();
      await page.locator('#fin-rail .fin-rail-title').waitFor();
      assert.equal(await page.locator('#fin-rail [data-fin-cat], #fin-rail [data-fin-tag]').count(), 0, tag + ': no line picker without finance.book');
      await go(page, '#/finance/access', READY.access);
      assert.equal(await page.locator('[data-fin-defaults], [data-fin-viewas]').count(), 0, tag + ': no defaults or View as');
      await page.locator('[data-fin-mate="bot:eng-lead"]').click();
      await page.waitForFunction(() => FIN_ACC.sel === 'bot:eng-lead' && document.querySelector('#fin-rail h2'));
      assert.equal(await page.locator('#fin-rail [data-fin-grant]').count(), 0, tag + ': no Grant more where the server would refuse');
      await page.locator('[data-fin-mate="bot:builder"]').click();
      await page.locator('#fin-rail [data-fin-grant]').waitFor();
      await page.locator('#fin-rail select[name="what"]').selectOption('pnl_lines');
      await page.locator('#fin-rail select[name="line"] option[value="a-6100"]').waitFor({state: 'attached'});
      const named = await page.locator('#fin-rail select[name="line"] option').evaluateAll(o => o.map(x => x.value));
      assert.ok(!named.includes('a-6200') && !named.some(v => v.startsWith('a-4')), tag + ': expense lines only, no private line');
      await page.locator('#fin-rail [data-fin-grant] [type=submit]').click();
      await page.waitForTimeout(100);
      assert.equal(actions(writes).at(-1)?.kind, 'view_grant', tag + ': a manager grants to a member of a team under theirs');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // The owner viewing as the manager: the manager's tabs, read only, ?view_as on reads.
    {
      const tag = 'view as';
      const {page, errors} = await open(browser, {hash: '#/finance/access'});
      await page.locator('[data-fin-viewas]').click();
      await page.locator('dialog.fin-dlg select[name="who"]').selectOption('human:cara');
      const read = page.waitForRequest(r => r.url().includes('/api/v2/finance/pnl') && r.url().includes('view_as=human%3Acara'));
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await read;
      await page.locator('.fin-as').waitFor();
      await page.locator('.fin-pl').waitFor();
      assert.deepEqual(await tabs(page), ['P&L', 'Cards', 'Transactions'], tag + ': the manager\'s tabs (Access is the owner\'s own)');
      assert.equal(await page.locator('.fin-pl a[href*="/line/"]').count(), 0, tag + ': no drill-down while viewing as');
      assert.match(await page.evaluate(() => location.hash), /view_as=human%3Acara/, tag + ': View as lives in the address');
      await page.goBack();
      await page.waitForFunction(() => location.hash === '#/finance/access' && !document.querySelector('.fin-as') && document.querySelectorAll('#fin-head [data-fin-tab]').length === 9);
      await page.goForward();
      await page.locator('.fin-as').waitFor();
      assert.deepEqual(await tabs(page), ['P&L', 'Cards', 'Transactions'], tag + ': Forward views as again');
      await page.locator('.fin-as button').click();
      await page.waitForFunction(() => !document.querySelector('.fin-as') && document.querySelectorAll('#fin-head [data-fin-tab]').length === 9);
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    console.log('finance: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
