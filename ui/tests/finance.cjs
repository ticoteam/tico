// The Finance tab (ui/app/finance*.js) against a fake hub: Acme (acme.example) with an owner (Ana), a manager (Cara,
// who leads Engineering) and a few bots. The owner sees every tab; the manager only P&L (expenses), Cards, Transactions
// and Access, and Balance sheet / Sources stay hidden even by address. A budget cell edits on a double-click only in a
// future month and sends budget_update with effective_from; the card rail's Freeze sends card_freeze. Payables and
// Receivables list bills and invoices with no totals row; their decisions run a dry run first and send the right action,
// and both tabs hide from a viewer without payables or receivables (a payables grant shows only Payables, read only).
// The Balance sheet's "+ Add" enters a journal entry. Each tab draws without errors and without sideways scroll on a
// phone. TICO_UI_SHOTS=<dir> saves screenshots (desktop and phone).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const uiDir = path.join(__dirname, '..');
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};

// ----------------------------------------------------------------- Acme
const people = [{id: 'ana', name: 'Ana Silva', role: 'owner'}, {id: 'cara', name: 'Cara Lee', role: 'member', team: 'eng'}];
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
  mate('human:cara', 'person', 'eng', 'team_expenses', {reports_to: 'human:ana'}),
  mate('bot:eng-lead', 'bot', 'platform', 'team_expenses', {reports_to: 'human:cara'}),
  mate('bot:builder', 'bot', 'platform', 'own_expenses', {reports_to: 'bot:eng-lead'}),
  mate('bot:mkt-lead', 'bot', 'mkt', 'team_expenses', {reports_to: 'human:ana', grants: [{id: 'g1', subject_kind: 'teammate', subject: 'bot:mkt-lead',
    what: 'pnl_lines', whose: 'company', whose_team: null, account_ids: ['a-6100'], new_members: false, granted_by: 'human:ana', expires_at: null, status: 'active', live: true}]}),
  mate('bot:writer', 'bot', 'content', 'own_expenses', {reports_to: 'bot:mkt-lead', grants: [{id: 'g2', subject_kind: 'teammate', subject: 'bot:writer',
    what: 'expenses', whose: 'team', whose_team: 'content', account_ids: [], new_members: false, granted_by: 'bot:mkt-lead', expires_at: null, status: 'active', live: true}]}),
  mate('bot:bookkeeper', 'bot', '', 'own_expenses', {rights: ['finance.book', 'finance.ap', 'finance.ar', 'finance.pay', 'finance.cards'],
    grants: [{id: 'g3', subject_kind: 'teammate', subject: 'bot:bookkeeper', what: 'everything', whose: 'company', whose_team: null, account_ids: [],
      new_members: false, granted_by: 'human:ana', expires_at: null, status: 'active', live: true}]}),
];
function access(viewer, apGrant = false) {
  const base = {teams, private_lines: [{account_id: 'a-6200', name: 'Payroll', others_see: 'totals', full_detail: ['bot:bookkeeper']}],
    defaults: {manager: 'team_expenses', member: 'own_expenses', manager_payroll: 'totals'}, view_version: 'v1'};
  if (viewer === 'ana') return {...base, teammates: MATES, can_view_as: true, can_edit_defaults: true};
  const mine = ['human:cara', 'bot:eng-lead', 'bot:builder'];
  const payables = {id: 'g4', subject_kind: 'teammate', subject: 'human:cara', what: 'payables', whose: 'company', whose_team: null, account_ids: [],
    new_members: false, granted_by: 'human:ana', expires_at: null, status: 'active', live: true};
  return {...base, teams: teams.filter(t => ['eng', 'platform'].includes(t.id)), private_lines: [],
    teammates: MATES.filter(m => mine.includes(m.teammate)).map(m => apGrant && m.teammate === 'human:cara' ? {...m, grants: [payables]} : m),
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
    projection: cols.map((c, i) => c.state === 'future' && !(noCogsProjection && code.startsWith('5')) ? wave(base, i + 1) : null)}));
  const sum = type => cols.map((c, i) => rows.filter(r => r.type === type).reduce((a, r) => a + (r.actual?.[i] || 0), 0));
  const income = sum('income'), expense = sum('expense');
  return {columns: cols, rows, income, expense, net: income.map((v, i) => v - expense[i]), excluded: rows.some(r => r.private), start: q.get('start'), end: q.get('end'), view_version: 'v1',
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
    next_month: {month: iso(nm).slice(0, 7), projection: 310000},
    entries: [{line_id: 'l1', entry_id: 'e1', date: '2026-10-28', account_id: id, amount: 21000, vendor, description: vendor + ' Team', memo: '', source: 'feed', tags: {}}], more: false};
  return {account: {id: l[0], code: l[1], name: l[2], type: l[3]}, vendor: null, total: l[5], excluded: 0, hidden: 0,
    paid_by: [{kind: 'card', name: 'Engineering', amount: 84000, count: 4}], next_month: {month: iso(nm).slice(0, 7), projection: 310000},
    by_vendor: [{key: 'GitHub', label: 'GitHub', amount: 84000}, {key: 'Figma', label: 'Figma', amount: 42000}, {key: 'Notion', label: 'Notion', amount: 32000}, {key: 'unattributed', label: 'No vendor', amount: 5000}, {key: 'other', label: 'Other · 4', amount: 21000}],
    by_team: [{key: 'eng', label: 'Engineering', amount: 120000}, {key: 'mkt', label: 'Marketing', amount: 50000}, {key: 'unattributed', label: 'Unattributed', amount: 9000}],
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
    currency: 'USD', budget: null, spent: 12000, sync_status: 'ok'},
  {id: 'card-3', provider: 'mercury', holder: 'human:cara', nickname: 'Travel', last4: '3007', kind: 'physical', status: 'active', limit: 300000, limit_interval: 'monthly',
    currency: 'USD', budget: null, spent: 41000, sync_status: 'ok'}];
const TX = [
  {kind: 'feed', id: 'f1', date: '2026-10-29', amount: -21000, currency: 'USD', counterparty: 'GitHub', description: 'GITHUB.COM', status: 'posted', pending: false, card_id: 'card-1',
    money_account: {id: 'm1', name: 'Checking', last4: '0001'}, lines: [{line_id: 'l1', account_id: 'a-6100', account: 'Software', amount: 21000, vendor: 'GitHub', tags: {teammate: 'bot:eng-lead', budget: 'b-software'}}]},
  {kind: 'feed', id: 'f2', date: '2026-10-28', amount: -9900, currency: 'USD', counterparty: 'Figma', description: 'FIGMA', status: 'new', pending: false, card_id: null,
    money_account: {id: 'm1', name: 'Checking', last4: '0001'}, lines: []},
  {kind: 'feed', id: 'f4', date: '2026-10-27', amount: -50000, currency: 'USD', counterparty: 'Linear', description: 'LINEAR', status: 'posted', pending: false, card_id: null,
    money_account: {id: 'm1', name: 'Checking', last4: '0001'}, lines: [
      {line_id: 'l5', account_id: 'a-6100', account: 'Software', amount: 30000, vendor: 'Linear', tags: {team: 'eng', teammate: 'bot:eng-lead'}},
      {line_id: 'l6', account_id: 'a-6100', account: 'Software', amount: 20000, vendor: 'Linear', tags: {team: 'mkt'}}]},
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
  doc('bill', 'bill-3', 'Oct-2026', {id: 'v-vercel', name: 'Vercel'}, 'scheduled', 124000, '2026-11-12', {pay_on: '2026-11-11'}),
  doc('bill', 'bill-4', 'DD-8812', {id: 'v-datadog', name: 'Datadog'}, 'open', 139000, '2026-10-15', {aging: '1_30', days_late: 16})];
const INVOICES = [
  doc('invoice', 'inv-1', '#1047', {id: 'c-bright', name: 'Brightline Studio'}, 'sent', 400000, '2026-11-14'),
  doc('invoice', 'inv-2', '#1033', {id: 'c-pine', name: 'Pine & Co'}, 'sent', 150000, '2026-09-20', {aging: '31_60', days_late: 41}),
  doc('invoice', 'inv-3', '#1051', {id: 'c-north', name: 'Northstar Labs'}, 'partial', 320000, '2026-11-27', {paid: 100000, remaining: 220000})];
const docDetail = r => ({...r, terms_days: 30, view_version: 'v1',
  lines: [{id: 'dl-' + r.id, account: r.kind === 'bill' ? {id: 'a-6100', code: '6100', name: 'Software'} : {id: 'a-4200', code: '4200', name: 'Services'},
    amount: r.total, description: 'Seats', tags: {}}],
  attachments: r.attachments ? [{id: 'att-1', file_id: 'file-1', created_by: 'bot:bookkeeper', created_at: '2026-10-20T09:00:00Z'}] : [],
  settlements: r.paid ? [{id: 's-1', from_kind: 'feed', from_id: 'f9', amount: r.paid, settled_on: '2026-10-25', entry_id: 'e9', voided_at: null, created_by: 'bot:bookkeeper'}] : [],
  actions: r.id === 'bill-1' ? [{action_id: 'act-b1', kind: 'bill_approve', status: 'needs_approval', actor: 'bot:bookkeeper', approval_id: 'ap-1',
    approval_to: 'human:ana', rule_id: 'r-1', created_at: '2026-10-20T09:01:00Z'}] : [],
  ...(r.kind === 'bill' ? {schedule: null} : {})});
const CONTACTS = [{id: 'v-figma', name: 'Figma', kind: 'vendor'}, {id: 'v-northwind', name: 'Northwind Design', kind: 'vendor'},
  {id: 'c-bright', name: 'Brightline Studio', kind: 'customer'}, {id: 'c-pine', name: 'Pine & Co', kind: 'customer'}]
  .map(c => ({...c, emails: [], default_account: null, is_1099: false, has_tax_id: false, tax_id_last4: null, recipient_id: null, active: true, owed: {}}));
const listDocs = (rows, url) => {
  const status = url.searchParams.get('status') || '', q = (url.searchParams.get('q') || '').toLowerCase();
  const open = ['draft', 'open', 'scheduled', 'sent', 'partial'];
  return rows.filter(r => (!status || (status === 'unpaid' ? open.includes(r.status) : r.status === status))
    && (!q || (r.vendor || r.customer).name.toLowerCase().includes(q) || r.number.toLowerCase().includes(q)));
};

// ----------------------------------------------------------------- the fake hub
async function open(browser, {viewport = DESKTOP, viewer = 'ana', hash = '#/finance/pnl', noRevenue = false, noCogsProjection = false, apGrant = false} = {}) {
  const person = people.find(p => p.id === viewer);
  const me = {id: viewer, name: person.name, email: viewer + '@acme.example', role: person.role, company_role: person.role, cloud: true};
  const config = {company_name: 'Acme'};
  const writes = [], reads = [];
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
    if (p === '/api/v2/finance/access') return json(access(viewer, apGrant));
    // Payables for the owner and a payables grant; receivables for the owner only (backend/finance/tab_ap.py).
    const seesAp = viewer === 'ana' || apGrant, seesAr = viewer === 'ana';
    if (p === '/api/v2/finance/bills') return seesAp ? json({bills: listDocs(BILLS, url), next_cursor: null, currency: 'USD', view_version: 'v1', bills_email: 'bills@acme.example',
      open: {count: 3, remaining: 513000, aging: {current: 374000, '1_30': 139000, '31_60': 0, '61_90': 0, over_90: 0}, more: false}}) : denied();
    if (p === '/api/v2/finance/invoices') return seesAr ? json({invoices: listDocs(INVOICES, url), next_cursor: null, currency: 'USD', view_version: 'v1',
      open: {count: 3, remaining: 770000, aging: {current: 620000, '1_30': 0, '31_60': 150000, '61_90': 0, over_90: 0}, more: false}}) : denied();
    if (p.startsWith('/api/v2/finance/bills/') || p.startsWith('/api/v2/finance/invoices/')) {
      const r = [...(seesAp ? BILLS : []), ...(seesAr ? INVOICES : [])].find(x => x.id === decodeURIComponent(p.split('/').pop()));
      return r ? json(docDetail(r)) : json({error: {code: 'not_found', detail: 'no such document in your view'}}, 404);
    }
    if (p === '/api/v2/finance/contacts') return json({contacts: CONTACTS.filter(c => !url.searchParams.get('kind') || c.kind === url.searchParams.get('kind')), next_cursor: null, view_version: 'v1'});
    if (p === '/api/v2/finance/pnl') return json(pnl(url, viewer, noRevenue, noCogsProjection));
    if (p === '/api/v2/finance/budgets') return json({budgets: BUDGETS, more: false});
    if (p.startsWith('/api/v2/finance/lines/')) return json(line(decodeURIComponent(p.split('/').pop()), url));
    if (p === '/api/v2/finance/mercury-users') return json({users: [{id: 'mu-1', name: 'Ana Silva', connection_id: 'c1', email: 'a•••@acme.example', updated_at: '2026-10-30T10:00:00Z'},
      {id: 'mu-2', name: 'Ops Card User', connection_id: 'c1', email: 'o•••@acme.example', updated_at: '2026-10-30T10:00:00Z'}]});
    if (p === '/api/v2/finance/balance-sheet') return viewer === 'ana' ? json(BS) : denied();
    if (p === '/api/v2/finance/cards') return json({cards: viewer === 'ana' ? CARDS : CARDS.filter(c => c.holder !== 'bot:writer'), next: null});
    if (p === '/api/v2/finance/transactions') return json({rows: TX, next: null});
    if (p === '/api/v2/finance/sources') return viewer === 'ana' ? json(SOURCES) : denied();
    // The rules send a bill's schedule to the owner for approval: its dry run says so, then the request is sent.
    if (p === '/api/v2/finance/actions' && method === 'POST' && body.dry_run) return json(body.kind === 'bill_schedule'
      ? {status: 'needs_approval', kind: body.kind, action_id: '', approver: 'human:ana', reason: '', dry_run: true, result: {}, impact: {}}
      : {status: 'committed', kind: body.kind, action_id: '', approver: null, reason: '', dry_run: true, result: {}, impact: {}});
    if (p === '/api/v2/finance/actions' && method === 'POST') return json({action_id: 'act-' + writes.length, kind: body.kind,
      status: body.kind === 'bill_schedule' ? 'needs_approval' : 'committed', code: null, reason: null, approver: null,
      result: body.kind === 'contact_create' ? {contact_id: 'v-new'} : {}});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  await page.locator('#fin-head [data-fin-tab]').first().waitFor();
  return {page, errors, writes, reads};
}

// ----------------------------------------------------------------- helpers
const tabs = page => page.locator('#fin-head [data-fin-tab]').allInnerTexts();
const sideways = page => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
const actions = writes => writes.filter(w => w[1] === '/api/v2/finance/actions').map(w => w[2]);
// The real requests only (a decision's dry run comes first).
const sent = writes => actions(writes).filter(a => !a.dry_run);
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
  sources: '[data-fin-group]', access: '[data-fin-mate]'};

(async () => {
  const browser = await chromium.launch();
  try {
    // The owner, on a computer: every tab, the P&L as a statement, budget edits only in future months.
    {
      const tag = 'owner';
      const {page, errors, writes, reads} = await open(browser);
      await page.locator('.fin-pl').waitFor();
      assert.deepEqual(await tabs(page), ['P&L', 'Balance sheet', 'Payables', 'Receivables', 'Cards', 'Transactions', 'Sources', 'Access'], tag + ': every tab, in order');
      assert.equal(await page.locator('#side a[data-nav="finance"]').count(), 1, tag + ': Finance in the sidebar');
      const firsts = await page.locator('.fin-pl tbody td:first-child').allInnerTexts();
      for (const want of ['Gross profit', 'Gross margin', 'Total operating expenses', 'Net income']) assert.ok(firsts.includes(want), `${tag}: the statement has ${want}`);
      assert.equal(await page.locator('.fin-pl thead th.fin-p').count() > 0, true, tag + ': projected columns are shaded');
      // The budget column is the current month's: not editable, and a double-click does nothing.
      assert.equal(await page.locator('td[data-fin-budget]').count(), 0, tag + ': the current month\'s budgets are not editable');
      const softwareBudget = page.locator('.fin-ln:has(a[href$="/line/a-6100"]) td.fin-b').last();
      await softwareBudget.dblclick();
      assert.equal(await page.locator('.fin-cell-in').count(), 0, tag + ': no editor on a current month');
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
      assert.equal(await page.locator('#fin-line tbody tr').count(), 5, tag + ': one row per vendor, No vendor and Other, no totals row');
      assert.equal(await page.locator('input[data-fin-vb]').count(), 0, tag + ': this month\'s vendor budgets are read only');
      const next = cols[future].start.slice(0, 7);
      await page.locator('[data-fin-month]').fill(next);
      await page.locator('input[data-fin-vb="GitHub"]').waitFor();
      await page.locator('input[data-fin-vb="GitHub"]').fill('950');
      await page.locator('input[data-fin-vb="GitHub"]').press('Enter');
      await page.waitForTimeout(150);
      assert.deepEqual(actions(writes).at(-1), {kind: 'budget_update', payload: {budget_id: 'b-github', amount: 95000, effective_from: next + '-01'}},
        tag + ': a vendor budget changes from the month shown');
      assert.equal(await page.locator('input[data-fin-vb="Figma"]').count(), 0, tag + ': a quarterly vendor budget is not edited as a month');
      assert.equal(await page.locator('input[data-fin-vb="unattributed"], input[data-fin-vb="other"]').count(), 0, tag + ': No vendor and Other take no budget');
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
      await page.locator('[data-fin-filter]').click();
      await page.locator('[data-fin-pick="team:eng"]').check();
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
      await page.locator('[data-fin-filter]').click();
      await page.locator('[data-fin-pick="mate:bot:writer"]').check();
      await page.locator('[data-fin-pick="none:"]').check();
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
      await page.locator('#fin-rail [data-fin-tag]').waitFor();
      await shot(page, 'transactions-desktop');
      await page.locator('#fin-rail select[name="team"]').selectOption('eng');
      await page.locator('#fin-rail [data-fin-tag] [type=submit]').click();
      await page.waitForTimeout(100);
      assert.deepEqual(actions(writes).at(-1), {kind: 'categorize', payload: {lines: [{line_id: 'l1', tags: {teammate: 'bot:eng-lead', budget: 'b-software', team: 'eng'}}], mode: 'replace'}},
        tag + ': categorize keeps the line\'s other tags');
      await page.locator('[data-fin-tx="feed:f4"]').click();
      assert.equal(await page.locator('#fin-rail select[name="team"]').inputValue(), '*', tag + ': a split row\'s differing team shows Mixed');
      await page.locator('#fin-rail input[name="project"]').fill('Launch');
      await page.locator('#fin-rail [data-fin-tag] [type=submit]').click();
      await page.waitForTimeout(100);
      assert.deepEqual(actions(writes).at(-1), {kind: 'categorize', payload: {lines: [{line_id: 'l5', tags: {team: 'eng', teammate: 'bot:eng-lead', project: 'Launch'}},
        {line_id: 'l6', tags: {team: 'mkt', project: 'Launch'}}], mode: 'replace'}}, tag + ': a split row changes only the field set; each line keeps its own team');
      await page.locator('[data-fin-tx="feed:f4"]').click();
      await page.locator('#fin-rail select[name="team"]').selectOption('');
      await page.locator('#fin-rail [data-fin-tag] [type=submit]').click();
      await page.waitForTimeout(100);
      assert.deepEqual(actions(writes).at(-1), {kind: 'categorize', payload: {lines: [{line_id: 'l5', tags: {teammate: 'bot:eng-lead'}}, {line_id: 'l6', tags: {}}], mode: 'replace'}},
        tag + ': on a mixed split, — clears the team on every line');
      await go(page, '#/finance/sources', READY.sources);
      await page.locator('[data-fin-group="banks"]').click();
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
      const {page, errors, writes} = await open(browser, {hash: '#/finance/payables'});
      const today = await page.evaluate(() => finToday());
      const heads = async () => (await page.locator('.fin-doc-t thead th').allInnerTexts()).map(t => t.toUpperCase());
      await page.locator(READY.payables).waitFor();
      assert.deepEqual(await heads(), ['VENDOR', 'BILL', 'DUE', 'AGING', 'AMOUNT', 'STATUS', 'PAYS'], tag + ': the bill columns');
      assert.equal(await page.locator('.fin-doc-t tbody tr').count(), BILLS.length, tag + ': one row per bill, no totals row');
      assert.ok(!(await page.locator('#fin-body').innerText()).includes('5,130'), tag + ': the open total is not drawn');
      assert.equal(await page.locator('[data-fin-copy]').getAttribute('data-fin-copy'), 'bills@acme.example', tag + ': the bills address, copyable');
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
      await page.locator('#fin-rail [data-fin-do="paid"]').click();
      assert.equal(await page.locator('dialog.fin-dlg input[name="amount"]').inputValue(), '1390', tag + ': Mark paid starts at what is left');
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.waitForFunction(() => !document.querySelector('dialog.fin-dlg[open]'));
      assert.deepEqual(sent(writes).at(-1), {kind: 'bill_mark_paid', payload: {bill_id: 'bill-4', amount: 139000, date: today}}, tag + ': bill_mark_paid');
      await page.locator('tr[data-fin-doc="bill-3"]').click();
      await page.locator('#fin-rail [data-fin-do="paid"]').waitFor();
      assert.deepEqual(await page.locator('#fin-rail [data-fin-do]').allInnerTexts(), ['Void', 'Mark paid'], tag + ': a scheduled bill is paid or voided, not scheduled again');
      // + Bill for a new vendor: the vendor first, then the bill under its id.
      await page.locator('[data-fin-doc-add]').click();
      await page.locator('dialog.fin-dlg select[name="contact"]').selectOption('+');
      await page.locator('dialog.fin-dlg input[name="name"]').fill('Acme Paper');
      await page.locator('dialog.fin-dlg input[name="number"]').fill('AP-1');
      await page.locator('dialog.fin-dlg select[name="line"]').selectOption('a-6100');
      await page.locator('dialog.fin-dlg input[name="amount"]').fill('45.50');
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.waitForFunction(() => !document.querySelector('dialog.fin-dlg[open]'));
      assert.deepEqual(sent(writes).slice(-2), [{kind: 'contact_create', payload: {name: 'Acme Paper', kind: 'vendor'}},
        {kind: 'bill_create', payload: {vendor_id: 'v-new', bill_date: today, lines: [{account_id: 'a-6100', amount: 4550}], number: 'AP-1', origin: 'manual'}}],
        tag + ': contact_create, then bill_create');
      // Receivables.
      await go(page, '#/finance/receivables', READY.receivables);
      assert.deepEqual(await heads(), ['CUSTOMER', 'INVOICE', 'DUE', 'AGING', 'AMOUNT', 'STATUS'], tag + ': the invoice columns');
      assert.equal(await page.locator('.fin-doc-t tbody tr').count(), INVOICES.length, tag + ': one row per invoice, no totals row');
      assert.equal(await page.locator('#fin-body button, #fin-body a').filter({hasText: /remind|chase/i}).count(), 0, tag + ': no reminders');
      await page.locator('tr[data-fin-doc="inv-2"]').click();
      await page.locator('#fin-rail [data-fin-do="paid"]').click();
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.waitForFunction(() => !document.querySelector('dialog.fin-dlg[open]'));
      assert.deepEqual(sent(writes).at(-1), {kind: 'invoice_record_payment', payload: {invoice_id: 'inv-2', amount: 150000, date: today}}, tag + ': Mark paid records the payment');
      await page.locator('tr[data-fin-doc="inv-3"]').click();
      await page.locator('#fin-rail [data-fin-do="writeoff"]').waitFor();
      assert.match(await page.locator('#fin-rail').innerText(), /Left\s+\$2,200[\s\S]*Payments\s+Oct 25 · Bank row\s+\$1,000/i, tag + ': what is left and what was paid');
      await shot(page, 'receivables-desktop');
      await page.locator('#fin-rail [data-fin-do="writeoff"]').click();
      await page.locator('dialog.fin-dlg input[name="memo"]').fill('Settled');
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.waitForFunction(() => !document.querySelector('dialog.fin-dlg[open]'));
      assert.deepEqual(sent(writes).at(-1), {kind: 'invoice_write_off', payload: {invoice_id: 'inv-3', amount: 220000, memo: 'Settled'}}, tag + ': invoice_write_off');
      // The Balance sheet's "+ Add".
      await go(page, '#/finance/balance', READY.balance);
      await page.locator('[data-fin-add]').click();
      assert.deepEqual(await page.locator('#fin-add [role=menuitem]').allInnerTexts(), ['Invoice', 'Bill', 'Journal entry', 'Connect'], tag + ': what the owner may add');
      assert.match(await page.locator('#fin-add a').getAttribute('href'), /#\/finance\/sources$/, tag + ': Connect leads to Sources');
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
      assert.equal(await page.locator('[data-fin-do], [data-fin-doc-add]').count(), 0, tag + ': no decisions without finance.ap');
      await page.evaluate(() => { location.hash = '#/finance/receivables'; });
      await page.waitForFunction(() => location.hash.startsWith('#/finance/pnl'));
      await page.locator('.fin-pl').waitFor();
      await go(page, '#/finance/balance', '.fin-pl');
      assert.ok(!reads.some(r => r.startsWith('/api/v2/finance/invoices')), tag + ': invoices never read');
      assert.equal(writes.length, 0, tag + ': nothing written');
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
      await go(page, '#/finance/access', READY.access);
      assert.equal(await page.locator('[data-fin-defaults], [data-fin-viewas]').count(), 0, tag + ': no defaults or View as');
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
      await page.waitForFunction(() => location.hash === '#/finance/access' && !document.querySelector('.fin-as') && document.querySelectorAll('#fin-head [data-fin-tab]').length === 8);
      await page.goForward();
      await page.locator('.fin-as').waitFor();
      assert.deepEqual(await tabs(page), ['P&L', 'Cards', 'Transactions'], tag + ': Forward views as again');
      await page.locator('.fin-as button').click();
      await page.waitForFunction(() => !document.querySelector('.fin-as') && document.querySelectorAll('#fin-head [data-fin-tab]').length === 8);
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    console.log('finance: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
