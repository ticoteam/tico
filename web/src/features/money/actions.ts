import type { AnyRouter } from '@tanstack/react-router';
import * as z from 'zod/mini';
import { defineAction } from '@/actions';
import { ApiError } from '@/api/client';
import { directoryQuery, queryClient } from '@/data';
import { chatAddress } from '@/lib';
import { budgetDay, lastFullMonth, monthName } from './model';
import {
  addBudget,
  addStripeHook,
  balanceQuery,
  BOOKS,
  booksSignIn,
  cachedCard,
  cachedTx,
  connectSource,
  decide,
  disconnectBooks,
  findGrowthManager,
  askGrowthManager,
  growthReadQuery,
  segmentsQuery,
  KEY_SOURCES,
  removeBudget,
  cachedBudget,
  tmpBudgetId,
  setBillsAddress,
  setBudgetAmount,
  accessQuery,
  type Decided,
} from './queries';
import { FINANCE_TAB_IDS } from './search';
import { capsOf, tabShown, type AccessList } from './caps';
import { asOfDay, balanceCsv, balanced, billPayload, compareDay, download, entryPayload, invoicePayload, paidPayload, toCents } from './ledger';
import { TX_STATUSES, validateFinanceSearch } from './search';

/* Every view and change on Finance and Growth. Tabs, ranges and the open panel live in the address, so Back undoes a
   view; a budget change is shown first and taken back if the server refuses it. Growth is read here: its changes go
   through the Growth Manager's chat, as in the old app. */

const FIN = '/company/finance';
const GROWTH = '/company/growth';
const id = z.string().check(z.minLength(1), z.maxLength(200));
const ym = z.string().check(z.regex(/^\d{4}-(0[1-9]|1[0-2])$/), z.describe('a month, 2026-11'));
const dollars = z.number().check(z.minimum(0), z.maximum(1_000_000_000));

/** Moves within a page, keeping what its address already holds; from elsewhere it starts clean. */
function go(router: AnyRouter, to: string, patch: Record<string, unknown>) {
  const loc = router.state.location;
  const prev = loc.pathname === to ? (loc.search as Record<string, unknown>) : {};
  return router.navigate({ to, search: { ...prev, ...patch } });
}
const closeHere = (router: AnyRouter) =>
  router.navigate({ to: '.', search: (prev: Record<string, unknown>) => ({ ...prev, panel: undefined }) });

// ---------------------------------------------------------------- Finance views

export const setTab = defineAction({
  id: 'finance.setTab',
  title: 'Open a Finance page',
  kind: 'view',
  input: z.strictObject({ tab: z.enum(FINANCE_TAB_IDS) }),
  run: ({ router }, { tab }) => go(router, FIN, { tab: tab === 'overview' ? undefined : tab, panel: undefined }),
  did: ({ tab }) => `Opened ${tab === 'pnl' ? 'P&L' : tab} in Finance`,
  target: ({ tab }) => ({ kind: 'tab', id: tab }),
});

export const setRange = defineAction({
  id: 'finance.setRange',
  title: 'Show the P&L from a month',
  kind: 'view',
  input: z.strictObject({ from: ym }),
  run: ({ router }, { from }) => go(router, FIN, { tab: 'pnl', from }),
  did: () => 'Moved the range',
  target: () => ({ kind: 'range', id: 'pnl' }),
});

export const setPeriod = defineAction({
  id: 'finance.setPeriod',
  title: 'Show the P&L by month, quarter or year',
  kind: 'view',
  input: z.strictObject({ period: z.enum(['month', 'quarter', 'year'] as const) }),
  run: ({ router }, { period }) => go(router, FIN, { tab: 'pnl', period: period === 'month' ? undefined : period, from: undefined }),
  did: ({ period }) => `Showed the P&L by ${period}`,
  target: () => ({ kind: 'range', id: 'pnl' }),
});

export const setBasis = defineAction({
  id: 'finance.setBasis',
  title: 'Show the P&L on a cash or accrual basis',
  kind: 'view',
  input: z.strictObject({ basis: z.enum(['cash', 'accrual'] as const) }),
  run: ({ router }, { basis }) => go(router, FIN, { basis: basis === 'cash' ? 'cash' : undefined }),
  did: ({ basis }) => `Showed the ${basis} basis`,
  target: () => ({ kind: 'range', id: 'pnl' }),
});

export const openLine = defineAction({
  id: 'finance.openLine',
  title: 'Open a P&L line',
  kind: 'view',
  input: z.strictObject({ account: id }),
  run: ({ router }, { account }) => go(router, FIN, { tab: 'pnl', panel: `line:${account}` }),
  did: () => 'Opened the line',
  target: ({ account }) => ({ kind: 'line', id: account }),
});

export const closePanel = defineAction({
  id: 'finance.closePanel',
  title: 'Close the Finance panel',
  kind: 'view',
  input: z.strictObject({}),
  run: ({ router }) => closeHere(router),
  did: () => 'Closed the panel',
});

/** Connecting a bank, a card or payroll signs in to it, in Tools, where the person does it; opening Tools is Tico's too. */
export const addSource = defineAction({
  id: 'finance.addSource',
  title: 'Connect a Finance source',
  kind: 'view',
  input: z.strictObject({}),
  run: ({ router }) => router.navigate({ to: '/library/tools' }),
  did: () => 'Opened Tools',
});

// ---------------------------------------------------------------- money, sources and access (the old app's dialogs)

const money = z.number().check(z.minimum(0.01), z.maximum(1_000_000_000));
const cents = (dollars: number) => Math.round(dollars * 100);
const day = z.string().check(z.regex(/^\d{4}-\d{2}-\d{2}$/), z.describe('a day, 2026-01-01'));

/** A decision waiting for its approver says whom; the server names a teammate, an email or a role. */
function waits(out: Decided | undefined, done: string) {
  if (out?.status !== 'needs_approval') return done;
  const who = out.approver ?? '';
  const name = queryClient.getQueryData(directoryQuery.queryKey)?.byId[who]?.name ?? (who.includes(':') ? who.slice(who.indexOf(':') + 1) : who);
  return name ? `Sent to ${name} for approval` : 'Sent for approval';
}

export const transfer = defineAction({
  id: 'finance.transfer',
  title: 'Move money between two bank accounts',
  kind: 'change',
  // Money leaves an account: the person's own press. An agent asks through Finance's own tools, where the rules see it as the agent.
  tico: false,
  input: z.strictObject({ from: id, to: id, amount: money, memo: z.optional(z.string().check(z.maxLength(140))) }),
  can: (_ctx, { from, to }) => from !== to,
  run: (_ctx, { from, to, amount, memo }) =>
    decide('payment_create', { method: 'internal', from_account: from, to_account: to, amount: cents(amount), ...(memo?.trim() ? { memo: memo.trim() } : {}) }),
  did: (_i, out) => waits(out, 'Queued'),
});

export const newCard = defineAction({
  id: 'finance.newCard',
  title: 'Issue a card with a monthly limit from a budget',
  kind: 'change',
  tico: false,
  input: z.strictObject({ holder: id, budget: id, amount: money, user: id, name: z.optional(z.string().check(z.maxLength(50))) }),
  run: (_ctx, { holder, budget, amount, user, name }) =>
    decide('card_create', { holder, budget_id: budget, amount: cents(amount), provider_user_id: user, ...(name?.trim() ? { nickname: name.trim() } : {}) }),
  did: (_i, out) => waits(out, 'Card created'),
});

/** A key is a secret: the person's own press only. */
export const connect = defineAction({
  id: 'finance.connect',
  title: 'Connect a Mercury, Brex or Stripe account',
  kind: 'change',
  tico: false,
  input: z.strictObject({
    provider: z.enum(['mercury', 'brex', 'stripe'] as const),
    key: z.optional(id),
    token: z.optional(z.string().check(z.minLength(1), z.maxLength(4000))),
    level: z.optional(z.enum(['read', 'read_write'] as const)),
    from: z.optional(day),
    approver: z.optional(z.string().check(z.maxLength(254), z.regex(/^[^@\s]+@[^@\s]+$/), z.describe('an email'))),
  }),
  can: (_ctx, { key, token }) => !!key || !!token,
  run: (_ctx, { provider, key, token, level, from, approver }) =>
    connectSource(provider, KEY_SOURCES[provider], key ? { id: key } : { token: token ?? '', level: level ?? 'read' }, {
      ...(approver ? { approver_email: approver } : {}),
      ...(from ? { history_from: from } : {}),
    }),
  did: ({ provider }, out) => waits(out as Decided | undefined, `${KEY_SOURCES[provider]} connected`),
});

/** QuickBooks and Xero are signed in to on their own page; the server brings the browser back. */
export const connectBooks = defineAction({
  id: 'finance.connectBooks',
  title: 'Connect QuickBooks or Xero',
  kind: 'change',
  tico: false,
  input: z.strictObject({ provider: z.enum(['quickbooks', 'xero'] as const) }),
  run: async (_ctx, { provider }) => {
    let url: string;
    try {
      url = await booksSignIn(provider);
    } catch {
      throw new Error(`${BOOKS[provider]} did not answer`);
    }
    window.location.assign(url);
  },
  did: ({ provider }) => `Opened ${BOOKS[provider]}`,
});

export const startMirror = defineAction({
  id: 'finance.startMirror',
  title: 'Use QuickBooks or Xero as the books',
  kind: 'change',
  // The books are the person's own press: what posts to them, and whether they are connected.
  tico: false,
  input: z.strictObject({ connection: id }),
  run: (_ctx, { connection }) => decide('mirror_start', { connection_id: connection }),
  did: (_i, out) => waits(out, 'Mirroring'),
  target: ({ connection }) => ({ kind: 'source', id: connection }),
});

export const stopMirror = defineAction({
  id: 'finance.stopMirror',
  title: 'Stop using QuickBooks or Xero as the books',
  kind: 'change',
  tico: false,
  input: z.strictObject({}),
  run: () => decide('mirror_stop', {}),
  did: (_i, out) => waits(out, 'Stopped'),
});

/** QuickBooks and Xero go through HQ's broker, which drops its grant first; the rest are a Finance decision. */
export const disconnect = defineAction({
  id: 'finance.disconnect',
  title: 'Disconnect a Finance source',
  kind: 'change',
  tico: false,
  input: z.strictObject({ connection: id, books: z.optional(z.boolean()) }),
  run: async (_ctx, { connection, books }): Promise<Decided | undefined> => {
    if (!books) return decide('connection_disconnect', { connection_id: connection });
    await disconnectBooks(connection);
    return undefined;
  },
  did: (_i, out) => waits(out, 'Disconnected'),
});

/** Its address and signing secret come back once, to the person's own screen. */
export const addWebhook = defineAction({
  id: 'finance.addWebhook',
  title: "Add Stripe's webhook to Finance",
  kind: 'change',
  tico: false,
  input: z.strictObject({ secret: z.optional(z.string().check(z.maxLength(300))) }),
  run: (_ctx, { secret }) => addStripeHook(secret?.trim() || undefined),
  did: () => 'Added the webhook',
});

export const setDefaults = defineAction({
  id: 'finance.setDefaults',
  title: 'Set what managers and everyone else see in Finance',
  kind: 'change',
  // Who sees financial data is the person's own press.
  tico: false,
  input: z.strictObject({
    manager: z.optional(z.enum(['none', 'team_expenses', 'own_expenses'] as const)),
    manager_payroll: z.optional(z.enum(['totals', 'hidden'] as const)),
    member: z.optional(z.enum(['none', 'own_expenses'] as const)),
  }),
  can: (_ctx, pick) => Object.values(pick).some(Boolean),
  run: (_ctx, pick) => decide('view_defaults_set', Object.fromEntries(Object.entries(pick).filter(([, v]) => v))),
  did: (_i, out) => waits(out, 'Saved'),
  target: () => ({ kind: 'tab', id: 'access' }),
});

/** The owner reads Finance as one teammate sees it, read only; no teammate is their own view again. */
export const viewAs = defineAction({
  id: 'finance.viewAs',
  title: 'Read Finance as a teammate sees it, or go back to your own view',
  kind: 'view',
  input: z.strictObject({ teammate: z.optional(z.string().check(z.maxLength(200), z.regex(/^(human|bot):\S+$/), z.describe('a teammate, human:… or bot:…'))) }),
  // Reading as someone lands on their P&L, as the old app did (finance-shared.ts finViewAs); a teammate without one gets
  // the plain address, their first page. Never the page it was pressed on (Access is closed while viewing).
  run: ({ router, queryClient: qc }, { teammate }) => {
    if (!teammate) return go(router, FIN, { as: undefined, panel: undefined });
    const acc = qc.getQueryData(accessQuery.queryKey) as AccessList | null | undefined;
    const caps = capsOf(acc, teammate);
    return router.navigate({ to: FIN, search: { as: teammate, ...(caps && tabShown('pnl', caps, acc, true) ? { tab: 'pnl' } : {}) } });
  },
  did: ({ teammate }) => (teammate ? 'Viewing as a teammate' : 'Back to your own view'),
});

const email = z.string().check(z.maxLength(254), z.regex(/^[^@\s]+@[^@\s]+$/), z.describe('an email'));

/** Bills emailed to this address land in Payables; none clears it. An address that is no Tico inbox is kept, and said. */
export const setBillsEmail = defineAction({
  id: 'finance.setBillsAddress',
  title: 'Set or clear the address bills are emailed to',
  kind: 'change',
  input: z.strictObject({ address: z.nullable(email) }),
  run: async (_ctx, { address }) => {
    let out;
    try {
      out = await setBillsAddress(address);
    } catch (e) {
      throw e instanceof ApiError && e.status === 403 ? new Error('Only a finance admin sets the bills address') : e;
    }
    if (out.bills_email && !out.inbox_id) throw new Error('Not an inbox address');
    return out;
  },
  did: ({ address }) => (address ? 'Saved' : 'Cleared'),
  target: () => ({ kind: 'tab', id: 'payables' }),
});

/** The rows of a statement file the preview showed as new; the token says it posts what was shown. */
export const postStatement = defineAction({
  id: 'finance.postStatement',
  title: 'Post the new rows of a bank or card statement',
  kind: 'change',
  // No press takes a posted statement back (the server's undo is refused once reconciled): the person's own press.
  tico: false,
  input: z.strictObject({
    account: id,
    file: z.string().check(z.minLength(1), z.maxLength(1_400_000)),
    name: z.string().check(z.minLength(1), z.maxLength(255)),
    token: id,
    signs: z.optional(z.enum(['as_shown', 'flipped'] as const)),
  }),
  run: (_ctx, { account, file, name, token, signs }) =>
    decide('statement_post', { money_account_id: account, file, file_name: name, preview_token: token, ...(signs ? { signs } : {}) }),
  did: (_i, out) => waits(out, 'Posted'),
  target: ({ account }) => ({ kind: 'source', id: account }),
});

/** One part of a split: a teammate or a team and its share in basis points (10,000 is the whole line). */
const share = z.strictObject({ teammate: z.optional(id), team: z.optional(id), share_bps: z.int().check(z.minimum(1), z.maximum(10_000)) });
const splits = z.array(share).check(z.maxLength(10));
type Split = { teammate?: string; team?: string; share_bps: number };
const whole = (parts: readonly Split[]) => parts.every((p) => !!p.teammate !== !!p.team) && parts.reduce((a, p) => a + p.share_bps, 0) <= 10_000;

/** Who a line is attributed to, set by hand over its tags and rules; no parts clears that. */
export const attribute = defineAction({
  id: 'finance.attribute',
  title: "Set who a line's amount is attributed to",
  kind: 'change',
  input: z.strictObject({ line: id, splits }),
  can: (_ctx, { splits }) => whole(splits),
  run: (_ctx, { line, splits }) => decide('attribute', { line_id: line, splits }),
  did: (i, out) => waits(out, i.splits.length ? 'Saved' : 'Cleared'),
});

export const undoAttribution = defineAction({
  id: 'finance.undoAttribution',
  title: "Put a line's attribution back to what it was before the last change",
  kind: 'change',
  input: z.strictObject({ line: id }),
  run: (_ctx, { line }) => decide('attribute_undo', { line_id: line }),
  did: (_i, out) => waits(out, 'Undone'),
});

/** A rule attributes every line it matches (a vendor, a P&L account, a card or bank account) from a day on. */
export const saveRule = defineAction({
  id: 'finance.saveRule',
  title: 'Add or change an attribution rule',
  kind: 'change',
  input: z.strictObject({
    rule: z.optional(id),
    name: z.string().check(z.minLength(1), z.maxLength(80)),
    match: z.enum(['vendor', 'account', 'money_account'] as const),
    to: id,
    splits,
    on: z.optional(z.boolean()),
    from: z.optional(day),
  }),
  can: (_ctx, { splits }) => splits.length > 0 && whole(splits),
  run: (_ctx, { rule, name, match, to, splits, on, from }) =>
    decide('attribution_rule_set', { ...(rule ? { id: rule } : {}), name, match: { kind: match, id: to }, splits, enabled: on ?? true, ...(from ? { apply_from: from } : {}) }),
  did: (i, out) => waits(out, i.rule ? 'Saved' : 'Rule added'),
  target: ({ rule }) => (rule ? { kind: 'rule', id: rule } : { kind: 'tab', id: 'attribution' }),
});

export const deleteRule = defineAction({
  id: 'finance.deleteRule',
  title: 'Delete an attribution rule; its lines go back to their tags',
  kind: 'change',
  input: z.strictObject({ rule: id }),
  run: (_ctx, { rule }) => decide('attribution_rule_delete', { id: rule }),
  did: (_i, out) => waits(out, 'Deleted'),
});

/** A spreadsheet's monthly totals (month, line, amount in cents, team), each sheet line mapped to a line of the chart. */
export const importHistory = defineAction({
  id: 'finance.importHistory',
  title: "Import a spreadsheet's monthly totals as history",
  kind: 'change',
  // Open to Tico: a posted month can be undone, and a non-owner's import waits for the owner's OK (decide).
  input: z.strictObject({
    csv: z.string().check(z.minLength(1), z.maxLength(1_000_000)),
    basis: z.enum(['accrual', 'cash'] as const),
    mapping: z.record(z.string(), id),
    source: z.optional(z.string().check(z.maxLength(120))),
  }),
  run: (_ctx, { csv, basis, mapping, source }) => decide('import_raw_totals', { csv, basis, mapping, ...(source?.trim() ? { source_name: source.trim() } : {}) }),
  did: (_i, out) => waits(out, 'Imported'),
  target: () => ({ kind: 'tab', id: 'balance' }),
});

// ---------------------------------------------------------------- budgets

export const openBudget = defineAction({
  id: 'budget.openRow',
  title: 'Open a budget',
  kind: 'view',
  input: z.strictObject({ budget: id }),
  run: ({ router }, { budget }) => go(router, FIN, { tab: 'budgets', panel: `budget:${budget}` }),
  did: () => 'Opened the budget',
  target: ({ budget }) => ({ kind: 'budget', id: budget }),
});

/** The day a month's budgets are read at: today in this month, else the month's first day (finance-budgets.ts:76). */
const budgetAt = (month?: string) => (!month || month === budgetDay().slice(0, 7) ? budgetDay() : `${month}-01`);

export const addRow = defineAction({
  id: 'budget.addRow',
  title: 'Add a budget: for a P&L line, or under one for a teammate or a vendor',
  kind: 'change',
  // A budget is money: the person's own press. Opening one (above) stays Tico's.
  tico: false,
  input: z.strictObject({
    name: z.string().check(z.minLength(1), z.maxLength(120)),
    line: id,
    amount: dollars,
    under: z.optional(id),
    teammate: z.optional(z.string().check(z.maxLength(200), z.regex(/^(human|bot):\S+$/), z.describe('a teammate, human:… or bot:…'))),
    vendor: z.optional(z.string().check(z.minLength(1), z.maxLength(120))),
    warn: z.optional(z.int().check(z.minimum(1), z.maximum(100))),
    period: z.optional(z.enum(['month', 'quarter', 'year'] as const)),
    from: z.optional(ym),
  }),
  run: (_ctx, { name, line, amount, under, teammate, vendor, warn, period, from }) =>
    addBudget(budgetDay(), { name, line, cents: Math.round(amount * 100), parent: under, teammate, vendor, warn, period, from }),
  did: ({ name }) => `Added “${name}”`,
  target: (i) => ({ kind: 'budget', id: tmpBudgetId(i) }),
});

/** Whether a new amount is at most what the budget holds now, as last read. */
const lowers = (at: string, budget: string, amount: number) => {
  const now = cachedBudget(queryClient, at, budget)?.state.amount;
  return now != null && Math.round(amount * 100) <= now;
};

export const updateRow = defineAction({
  id: 'budget.updateRow',
  title: 'Change a budget from a month on (this one when left out)',
  kind: 'change',
  input: z.strictObject({ budget: id, amount: dollars, month: z.optional(ym) }),
  // Raising a budget is money: the person's own press. Lowering one stays Tico's.
  can: (ctx, { budget, amount, month }) => ctx.via !== 'tico' || lowers(budgetAt(month), budget, amount),
  run: (_ctx, { budget, amount, month }) => setBudgetAmount(budgetAt(month), budget, Math.round(amount * 100)),
  did: () => 'Changed the budget',
  target: ({ budget }) => ({ kind: 'budget', id: budget }),
});

/** Stopping a budget keeps its history but lifts its cap (a card it froze can spend again): the person's own press. */
export const removeRow = defineAction({
  id: 'budget.removeRow',
  title: 'Stop a budget from a month on (this one when left out)',
  kind: 'change',
  tico: false,
  input: z.strictObject({ budget: id, month: z.optional(ym) }),
  run: async ({ router }, { budget, month }) => {
    const done = removeBudget(budgetAt(month), budget);
    await closeHere(router);
    return done;
  },
  did: () => 'Removed the budget',
});

export const setBudgetPeriod = defineAction({
  id: 'budget.setPeriod',
  title: 'Show budgets for this month, quarter or year',
  kind: 'view',
  input: z.strictObject({ period: z.enum(['month', 'quarter', 'year'] as const) }),
  run: ({ router }, { period }) =>
    go(router, FIN, {
      // My budget keeps its tab; anywhere else it is Budgets.
      tab: validateFinanceSearch(router.state.location.search as Record<string, unknown>).tab === 'mine' ? 'mine' : 'budgets',
      period: period === 'month' ? undefined : period,
      panel: undefined,
    }),
  did: ({ period }) => `Showed budgets by ${period}`,
  target: () => ({ kind: 'range', id: 'budgets' }),
});

// ---------------------------------------------------------------- Growth views

export const setSegment = defineAction({
  id: 'growth.setSegment',
  title: 'Show Growth for all customers or one segment',
  kind: 'view',
  input: z.strictObject({ segment: z.optional(z.string().check(z.minLength(1), z.maxLength(80), z.regex(/^[\w-]+$/), z.describe('a segment slug'))) }),
  run: ({ router }, { segment }) => go(router, GROWTH, { tab: undefined, segment, panel: undefined }),
  did: () => 'Showed the segment',
  target: ({ segment }) => ({ kind: 'tab', id: segment ?? 'all' }),
});

export const setMonth = defineAction({
  id: 'growth.setMonth',
  title: 'Show Growth for a month',
  kind: 'view',
  input: z.strictObject({ month: ym }),
  run: ({ router }, { month }) => go(router, GROWTH, { tab: undefined, month }),
  did: () => 'Showed the month',
  target: () => ({ kind: 'range', id: 'growth' }),
});

export const openChannel = defineAction({
  id: 'growth.openChannel',
  title: 'Open a channel: where its number comes from',
  kind: 'view',
  input: z.strictObject({ channel: id }),
  run: ({ router }, { channel }) => go(router, GROWTH, { tab: undefined, panel: `channel:${channel}` }),
  did: () => 'Opened the channel',
  target: ({ channel }) => ({ kind: 'channel', id: channel }),
});

/** What the page shows, in words, ahead of a question: "Rental, October 2026". */
function growthScopeWords(router: AnyRouter, qc: typeof queryClient) {
  const loc = router.state.location;
  if (loc.pathname !== GROWTH) return '';
  const search = loc.search as { segment?: string; month?: string };
  const seg = search.segment && qc.getQueryData(segmentsQuery.queryKey)?.segments.find((x) => x.slug === search.segment);
  const read = qc.getQueryData(growthReadQuery(search.segment, search.month).queryKey);
  return `${seg ? seg.name : 'All'}, ${monthName(read?.period ?? search.month ?? lastFullMonth())}`;
}

/** Growth changes (channels, customers, sources) are asked of the Growth Manager: its chat opens beside the page. With
    words, they are sent first, led by the scope and month on screen. Ordinary messaging, so Tico may ask too. */
export const askManager = defineAction({
  id: 'growth.askManager',
  title: 'Ask the Growth Manager',
  kind: 'change',
  input: z.strictObject({ text: z.optional(z.string().check(z.minLength(1), z.maxLength(4000))) }),
  run: async ({ router, queryClient: qc }, { text }): Promise<string> => {
    const who = await findGrowthManager(qc);
    if (!who) throw new Error('No Growth Manager');
    const words = text?.trim();
    let chat = chatAddress(who, 'agent');
    if (words) {
      const lead = growthScopeWords(router, qc);
      const sent = await askGrowthManager(who, lead ? `${lead}: ${words}` : words);
      // the thread it went to opens beside the page
      const thread = sent?.conversation_id || sent?.thread_id;
      if (thread) chat = `thread:${thread}`;
    }
    await router.navigate({ to: '.', search: (prev: Record<string, unknown>) => ({ ...prev, chat }) });
    return chat;
  },
  did: ({ text }) => (text?.trim() ? 'Asked the Growth Manager' : 'Opened the chat'),
  target: (_i, chat) => (chat ? { kind: 'chat', id: chat } : null),
});

export const closeGrowthPanel = defineAction({
  id: 'growth.closePanel',
  title: 'Close the Growth panel',
  kind: 'view',
  input: z.strictObject({}),
  run: ({ router }) => closeHere(router),
  did: () => 'Closed the panel',
});

// ---------------------------------------------------------------- bills, invoices, cards, entries, the balance sheet (s405-s411, s449, s475)

/* A bill or an invoice is entered as a draft and decided from its panel, each decision tried first by its dialog (s449).
   What moves or commits money (approve, schedule, mark paid, send, write off, void, unfreezing a card, raising its
   limit) is the person's own press; a draft, a vendor, a journal entry, a freeze and a lower limit are Tico's too. */

const RECORD_TAB = { bill: 'payables', invoice: 'receivables', card: 'cards', tx: 'transactions' } as const;
const tags = z.optional(z.record(z.string(), z.string().check(z.minLength(1), z.maxLength(200))));
const words = (max: number) => z.optional(z.string().check(z.maxLength(max)));

export const openRecord = defineAction({
  id: 'finance.openRecord',
  title: 'Open a bill, an invoice or a card beside its list, or a transaction in place',
  kind: 'view',
  input: z.strictObject({ kind: z.enum(['bill', 'invoice', 'card', 'tx'] as const), record: id }),
  run: ({ router }, { kind, record }) => go(router, FIN, { tab: RECORD_TAB[kind], panel: `${kind}:${record}` }),
  did: () => 'Opened it',
  target: ({ kind, record }) => ({ kind, id: record }),
});

export const setAsOf = defineAction({
  id: 'finance.setAsOf',
  title: 'Show the balance sheet as of today, the last month end or the last year end',
  kind: 'view',
  input: z.strictObject({ asof: z.enum(['today', 'month', 'year'] as const) }),
  run: ({ router }, { asof }) => go(router, FIN, { tab: 'balance', asof: asof === 'today' ? undefined : asof }),
  did: () => 'Moved the day',
  target: () => ({ kind: 'tab', id: 'balance' }),
});

/** Each filter given is set; one given as null is cleared; one left out stays. */
export const filterTransactions = defineAction({
  id: 'finance.filterTransactions',
  title: 'Filter transactions by source (bank or entries), status or line',
  kind: 'view',
  input: z.strictObject({
    src: z.optional(z.nullable(z.enum(['feed', 'entry'] as const))),
    status: z.optional(z.nullable(z.enum(TX_STATUSES))),
    line: z.optional(z.nullable(id)),
  }),
  run: ({ router }, f) => go(router, FIN, { tab: 'transactions', panel: undefined, ...Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v ?? undefined])) }),
  did: () => 'Filtered transactions',
  target: () => ({ kind: 'tab', id: 'transactions' }),
});

/** The balance sheet shown, beside the day before it, as a file (finance-books.ts Export CSV). */
export const exportBalance = defineAction({
  id: 'finance.exportBalance',
  title: 'Download the balance sheet as a CSV',
  kind: 'view',
  input: z.strictObject({}),
  run: async ({ router, queryClient: qc }) => {
    const at = validateFinanceSearch(router.state.location.search as Record<string, unknown>);
    const asof = at.asof ?? 'today';
    const [now, was] = await Promise.all([
      qc.fetchQuery(balanceQuery(asOfDay(asof), at.as)),
      qc.fetchQuery(balanceQuery(compareDay(asof), at.as)).catch(() => undefined),
    ]);
    const name = `balance-sheet-${now.as_of}.csv`;
    download(name, balanceCsv(now, was));
    return name;
  },
  did: () => 'Exported',
});

/** A new vendor or customer; its id comes back for the bill or invoice it was named on. */
export const addContact = defineAction({
  id: 'finance.addContact',
  title: 'Add a vendor or a customer',
  kind: 'change',
  input: z.strictObject({ name: z.string().check(z.minLength(1), z.maxLength(120)), kind: z.enum(['vendor', 'customer'] as const) }),
  run: async (_ctx, { name, kind }) => {
    const out = (await decide('contact_create', { name: name.trim(), kind })) as Decided & { result?: { contact_id?: unknown } };
    const made = out.result?.contact_id;
    if (out.status !== 'committed' || typeof made !== 'string') throw new Error(`The new ${kind} waits for approval`);
    return made;
  },
  did: (i) => `Added ${i.name.trim()}`,
});

/** A bill entered by hand, as a draft: it holds budget, and Approve enters it. */
export const addBill = defineAction({
  id: 'finance.addBill',
  title: 'Enter a bill from a vendor, as a draft',
  kind: 'change',
  input: z.strictObject({ vendor: id, date: day, due: z.optional(day), number: words(60), memo: words(200), line: id, amount: money, tags }),
  run: (_ctx, b) => decide('bill_create', billPayload(b)),
  did: (_i, out) => waits(out, 'Bill added'),
  target: () => ({ kind: 'tab', id: 'payables' }),
});

export const approveBill = defineAction({
  id: 'finance.approveBill',
  title: 'Approve a draft bill, entering it to be paid',
  kind: 'change',
  // Approving commits the company to paying it: the person's own press.
  tico: false,
  input: z.strictObject({ bill: id }),
  run: (_ctx, { bill }) => decide('bill_approve', { bill_id: bill }),
  did: (_i, out) => waits(out, 'Approved'),
});

export const scheduleBill = defineAction({
  id: 'finance.scheduleBill',
  title: 'Pay a bill from a bank account on a day',
  kind: 'change',
  // Money leaves the bank on that day: the person's own press.
  tico: false,
  input: z.strictObject({ bill: id, on: day, from: id }),
  run: (_ctx, { bill, on, from }) => decide('bill_schedule', { bill_id: bill, pay_on: on, money_account_id: from }),
  did: (_i, out) => waits(out, 'Scheduled'),
});

/** Money paid on a bill, or received on an invoice, from (to) a bank account or elsewhere. */
export const markPaid = defineAction({
  id: 'finance.markPaid',
  title: 'Record money paid on a bill or received on an invoice',
  kind: 'change',
  // It settles money in the books: the person's own press.
  tico: false,
  input: z.strictObject({ kind: z.enum(['bill', 'invoice'] as const), doc: id, amount: money, date: z.optional(day), account: z.optional(id) }),
  run: (_ctx, p) => decide(p.kind === 'bill' ? 'bill_mark_paid' : 'invoice_record_payment', paidPayload(p)),
  did: (_i, out) => waits(out, 'Marked paid'),
});

export const voidBill = defineAction({
  id: 'finance.voidBill',
  title: 'Void a bill',
  kind: 'change',
  // A void cannot be undone: the person's own press.
  tico: false,
  input: z.strictObject({ bill: id, reason: words(200) }),
  run: (_ctx, { bill, reason }) => decide('bill_void', { bill_id: bill, ...(reason?.trim() ? { reason: reason.trim() } : {}) }),
  did: (_i, out) => waits(out, 'Voided'),
});

/** An invoice entered by hand, as a draft: Send sends it. */
export const newInvoice = defineAction({
  id: 'finance.newInvoice',
  title: 'Write an invoice to a customer, as a draft',
  kind: 'change',
  input: z.strictObject({
    customer: id,
    date: day,
    due: z.optional(day),
    number: words(60),
    memo: words(200),
    lines: z.array(z.strictObject({ line: id, amount: money, taxable: z.optional(z.boolean()), tags })).check(z.maxLength(50)),
  }),
  can: (_ctx, { lines }) => lines.length > 0,
  run: (_ctx, v) => decide('invoice_create', invoicePayload(v)),
  did: (_i, out) => waits(out, 'Invoice added'),
  target: () => ({ kind: 'tab', id: 'receivables' }),
});

export const sendInvoice = defineAction({
  id: 'finance.sendInvoice',
  title: 'Send a draft invoice to its customer',
  kind: 'change',
  // It goes to the customer and books what they owe: the person's own press.
  tico: false,
  input: z.strictObject({ invoice: id }),
  run: (_ctx, { invoice }) => decide('invoice_send', { invoice_id: invoice }),
  did: (_i, out) => waits(out, 'Sent'),
});

export const writeOff = defineAction({
  id: 'finance.writeOff',
  title: 'Write off what a customer will not pay on an invoice',
  kind: 'change',
  // It takes income off the books for good: the person's own press.
  tico: false,
  input: z.strictObject({ invoice: id, amount: money, memo: words(200) }),
  run: (_ctx, { invoice, amount, memo }) => decide('invoice_write_off', { invoice_id: invoice, amount: toCents(amount), ...(memo?.trim() ? { memo: memo.trim() } : {}) }),
  did: (_i, out) => waits(out, 'Written off'),
});

/** A journal entry: debits more than 0, credits less, equal in all (finance-docs.ts finJournalAdd). */
export const addEntry = defineAction({
  id: 'finance.addEntry',
  title: 'Post a journal entry of two balanced lines or more',
  kind: 'change',
  input: z.strictObject({
    date: day,
    memo: words(200),
    lines: z.array(z.strictObject({ line: id, amount: z.number().check(z.minimum(-1_000_000_000), z.maximum(1_000_000_000)), tags })).check(z.maxLength(50)),
  }),
  can: (_ctx, { lines }) => balanced(lines),
  run: (_ctx, e) => decide('journal', entryPayload(e)),
  did: (_i, out) => waits(out, 'Posted'),
  target: () => ({ kind: 'tab', id: 'transactions' }),
});

/** Freezing stops spend at once and is undone by hand: Tico may. */
export const freezeCard = defineAction({
  id: 'finance.freezeCard',
  title: 'Freeze a card',
  kind: 'change',
  input: z.strictObject({ card: id }),
  run: (_ctx, { card }) => decide('card_freeze', { card_id: card }),
  did: (_i, out) => waits(out, 'Frozen'),
  target: ({ card }) => ({ kind: 'card', id: card }),
});

export const unfreezeCard = defineAction({
  id: 'finance.unfreezeCard',
  title: 'Unfreeze a card a person froze',
  kind: 'change',
  // The card can spend again: the person's own press.
  tico: false,
  input: z.strictObject({ card: id }),
  run: (_ctx, { card }) => decide('card_unfreeze', { card_id: card }),
  did: (_i, out) => waits(out, 'Unfrozen'),
});

export const setCardLimit = defineAction({
  id: 'finance.setCardLimit',
  title: "Change a card's monthly limit",
  kind: 'change',
  input: z.strictObject({ card: id, amount: money }),
  // A higher limit lets more be spent: the person's own press. Tico may lower one the cards list shows.
  can: ({ via, queryClient: qc }, { card, amount }) => {
    if (via !== 'tico') return true;
    const now = cachedCard(qc, card)?.limit;
    return now != null && toCents(amount) < now;
  },
  run: (_ctx, { card, amount }) => decide('card_update_limit', { card_id: card, amount: toCents(amount) }),
  did: (_i, out) => waits(out, 'Saved'),
  target: ({ card }) => ({ kind: 'card', id: card }),
});

// ---------------------------------------------------------------- sources and access, opened beside (finance-admin.ts)

const mate = z.string().check(z.maxLength(200), z.regex(/^(human|bot):\S+$/), z.describe('a teammate, human:… or bot:…'));

export const openFinanceSource = defineAction({
  id: 'finance.openSource',
  title: 'Open a Finance source: its accounts, its line in the books, its last sync',
  kind: 'view',
  input: z.strictObject({ source: id }),
  run: ({ router }, { source }) => go(router, FIN, { tab: 'sources', panel: `source:${source}` }),
  did: () => 'Opened the source',
  target: ({ source }) => ({ kind: 'source', id: source }),
});

export const openMate = defineAction({
  id: 'finance.openMate',
  title: "Open a teammate's Finance access: what they see and why",
  kind: 'view',
  input: z.strictObject({ teammate: mate }),
  run: ({ router }, { teammate }) => go(router, FIN, { tab: 'access', panel: `mate:${teammate}` }),
  did: () => 'Opened the teammate',
  target: ({ teammate }) => ({ kind: 'mate', id: teammate }),
});

/** Reads a connection's feed again now; it spends nothing and changes no books. */
export const syncSource = defineAction({
  id: 'finance.syncSource',
  title: 'Sync a Finance source now',
  kind: 'change',
  input: z.strictObject({ connection: id }),
  run: (_ctx, { connection }) => decide('connection_sync', { connection_id: connection }),
  did: (_i, out) => waits(out, 'Syncing'),
  target: ({ connection }) => ({ kind: 'source', id: connection }),
});

/** Which line of the books a bank, card or payments account posts to; another choice puts it back. */
export const mapSource = defineAction({
  id: 'finance.mapSource',
  title: 'Choose the balance sheet line a bank, card or payments account books to',
  kind: 'change',
  input: z.strictObject({ account: id, line: id }),
  run: (_ctx, { account, line }) => decide('connection_map', { money_account_id: account, account_id: line }),
  did: (_i, out) => waits(out, 'Saved'),
  target: ({ account }) => ({ kind: 'source', id: account }),
});

const WHAT = ['everything', 'pnl', 'expenses', 'pnl_lines', 'balance_sheet', 'bs_lines', 'payables', 'receivables', 'sources'] as const;

export const giveAccess = defineAction({
  id: 'finance.giveAccess',
  title: 'Let a teammate see more of Finance',
  kind: 'change',
  // Who sees financial data is a right given: the person's own press.
  tico: false,
  input: z.strictObject({
    teammate: mate,
    what: z.enum(WHAT),
    whose: z.optional(z.enum(['own', 'team', 'company'] as const)),
    team: z.optional(id),
    line: z.optional(id),
    until: z.optional(day),
  }),
  can: (_ctx, { what, whose, team, line }) => (whose !== 'team' || !!team) && (!(what === 'pnl_lines' || what === 'bs_lines') || !!line),
  run: (_ctx, { teammate, what, whose, team, line, until }) =>
    decide('view_grant', {
      subject_kind: 'teammate',
      subject: teammate,
      what,
      ...(whose ? { whose } : {}),
      ...(whose === 'team' && team ? { whose_team: team } : {}),
      ...(line ? { account_ids: [line] } : {}),
      ...(until ? { expires_at: `${until}T23:59:59Z` } : {}),
    }),
  did: (_i, out) => waits(out, 'Access given'),
  target: ({ teammate }) => ({ kind: 'mate', id: teammate }),
});

/** Taking a view back narrows what someone sees; it can be given again. */
export const takeAccess = defineAction({
  id: 'finance.takeAccess',
  title: 'Take back a view of Finance given to a teammate',
  kind: 'change',
  input: z.strictObject({ grant: id }),
  run: (_ctx, { grant }) => decide('view_revoke', { grant_id: grant }),
  did: (_i, out) => waits(out, 'Access taken back'),
});

export const setApprover = defineAction({
  id: 'finance.setApprover',
  title: 'Name who approves finance-admin actions; none is the owner',
  kind: 'change',
  // Naming the approver gives a right: the owner's own press.
  tico: false,
  input: z.strictObject({ approver: z.optional(mate) }),
  run: (_ctx, { approver }) => decide('finance_approver_set', { approver: approver ?? '' }),
  did: (_i, out) => waits(out, 'Saved'),
  target: () => ({ kind: 'tab', id: 'access' }),
});

/** A bank or card row put on a line, in place on Transactions: its tags stay but the budget, which the new line sets
    (finance-books.ts finTxLineSave). Ordinary bookkeeping, undone the same way: Tico may. */
export const setLine = defineAction({
  id: 'finance.setLine',
  title: 'Put a bank or card row on a line',
  kind: 'change',
  input: z.strictObject({ tx: id, line: id }),
  run: async ({ router, queryClient: qc }, { tx, line }) => {
    const tags = { ...((cachedTx(qc, tx)?.lines?.[0]?.tags as Record<string, unknown> | undefined) ?? {}) };
    delete tags.budget;
    const done = await decide('categorize', { feed_id: tx, account_id: line, tags });
    await closeHere(router);
    return done;
  },
  did: (_i, out) => waits(out, 'Saved'),
  target: ({ tx }) => ({ kind: 'tx', id: tx }),
});
