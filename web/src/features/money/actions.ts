import type { AnyRouter } from '@tanstack/react-router';
import { defineAction, s } from '@/actions';
import { budgetDay } from './model';
import { addBudget, removeBudget, setBudgetAmount } from './queries';
import { FINANCE_TAB_IDS } from './search';

/* Every view and change on Finance and Growth. Tabs, ranges and the open panel live in the address, so Back undoes a
   view; a budget change is shown first and taken back if the server refuses it. Growth is read here: its changes go
   through the Growth Manager's chat, as in the old app. */

const FIN = '/company/finance';
const GROWTH = '/company/growth';
const id = s.string({ min: 1, max: 200 });
const ym = s.string({ pattern: /^\d{4}-(0[1-9]|1[0-2])$/, what: 'a month, 2026-11' });
const dollars = s.number({ min: 0, max: 1_000_000_000 });

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
  input: s.object({ tab: s.oneOf(FINANCE_TAB_IDS) }),
  run: ({ router }, { tab }) => go(router, FIN, { tab: tab === 'overview' ? undefined : tab, panel: undefined }),
  did: ({ tab }) => `Opened ${tab === 'pnl' ? 'P&L' : tab} in Finance`,
  target: ({ tab }) => ({ kind: 'tab', id: tab }),
});

export const setRange = defineAction({
  id: 'finance.setRange',
  title: 'Show the P&L from a month',
  kind: 'view',
  input: s.object({ from: ym }),
  run: ({ router }, { from }) => go(router, FIN, { tab: 'pnl', from }),
  did: () => 'Moved the range',
  target: () => ({ kind: 'range', id: 'pnl' }),
});

export const setPeriod = defineAction({
  id: 'finance.setPeriod',
  title: 'Show the P&L by month, quarter or year',
  kind: 'view',
  input: s.object({ period: s.oneOf(['month', 'quarter', 'year'] as const) }),
  run: ({ router }, { period }) => go(router, FIN, { tab: 'pnl', period: period === 'month' ? undefined : period, from: undefined }),
  did: ({ period }) => `Showed the P&L by ${period}`,
  target: () => ({ kind: 'range', id: 'pnl' }),
});

export const setBasis = defineAction({
  id: 'finance.setBasis',
  title: 'Show the P&L on a cash or accrual basis',
  kind: 'view',
  input: s.object({ basis: s.oneOf(['cash', 'accrual'] as const) }),
  run: ({ router }, { basis }) => go(router, FIN, { basis: basis === 'cash' ? 'cash' : undefined }),
  did: ({ basis }) => `Showed the ${basis} basis`,
  target: () => ({ kind: 'range', id: 'pnl' }),
});

export const openLine = defineAction({
  id: 'finance.openLine',
  title: 'Open a P&L line',
  kind: 'view',
  input: s.object({ account: id }),
  run: ({ router }, { account }) => go(router, FIN, { tab: 'pnl', panel: `line:${account}` }),
  did: () => 'Opened the line',
  target: ({ account }) => ({ kind: 'line', id: account }),
});

export const closePanel = defineAction({
  id: 'finance.closePanel',
  title: 'Close the Finance panel',
  kind: 'view',
  input: s.object({}),
  run: ({ router }) => closeHere(router),
  did: () => 'Closed the panel',
});

/** Connecting a bank, a card or payroll signs in to it: the person does it. */
export const addSource = defineAction({
  id: 'finance.addSource',
  title: 'Connect a Finance source',
  kind: 'view',
  tico: false,
  input: s.object({}),
  run: ({ router }) => router.navigate({ to: '/library/tools' }),
  did: () => 'Opened Tools',
});

// ---------------------------------------------------------------- budgets

export const openBudget = defineAction({
  id: 'budget.openRow',
  title: 'Open a budget',
  kind: 'view',
  input: s.object({ budget: id }),
  run: ({ router }, { budget }) => go(router, FIN, { tab: 'budgets', panel: `budget:${budget}` }),
  did: () => 'Opened the budget',
  target: ({ budget }) => ({ kind: 'budget', id: budget }),
});

export const newBudget = defineAction({
  id: 'budget.newRow',
  title: 'Start a new budget',
  kind: 'view',
  input: s.object({}),
  run: ({ router }) => go(router, FIN, { tab: 'budgets', panel: 'budget:new' }),
  did: () => 'Opened a new budget',
  target: () => ({ kind: 'budget', id: 'new' }),
});

export const addRow = defineAction({
  id: 'budget.addRow',
  title: 'Add a monthly budget for a P&L line',
  kind: 'change',
  input: s.object({ name: s.string({ min: 1, max: 120 }), line: id, amount: dollars }),
  run: async ({ router }, { name, line, amount }) => {
    const done = addBudget(budgetDay(), { name, line, cents: Math.round(amount * 100) });
    await go(router, FIN, { tab: 'budgets', panel: undefined });
    return done;
  },
  did: ({ name }) => `Added “${name}”`,
  target: ({ line }) => ({ kind: 'budget', id: `tmp:${line}` }),
});

export const updateRow = defineAction({
  id: 'budget.updateRow',
  title: 'Change a budget from this month on',
  kind: 'change',
  input: s.object({ budget: id, amount: dollars }),
  run: (_ctx, { budget, amount }) => setBudgetAmount(budgetDay(), budget, Math.round(amount * 100)),
  did: () => 'Changed the budget',
  target: ({ budget }) => ({ kind: 'budget', id: budget }),
});

export const removeRow = defineAction({
  id: 'budget.removeRow',
  title: 'Stop a budget from this month on',
  kind: 'change',
  input: s.object({ budget: id }),
  run: async ({ router }, { budget }) => {
    const done = removeBudget(budgetDay(), budget);
    await closeHere(router);
    return done;
  },
  did: () => 'Removed the budget',
});

// ---------------------------------------------------------------- Growth views

export const setSegment = defineAction({
  id: 'growth.setSegment',
  title: 'Show Growth for all customers or one segment',
  kind: 'view',
  input: s.object({ segment: s.optional(s.string({ min: 1, max: 80, pattern: /^[\w-]+$/, what: 'a segment slug' })) }),
  run: ({ router }, { segment }) => go(router, GROWTH, { tab: undefined, segment, panel: undefined }),
  did: () => 'Showed the segment',
  target: ({ segment }) => ({ kind: 'tab', id: segment ?? 'all' }),
});

export const setMonth = defineAction({
  id: 'growth.setMonth',
  title: 'Show Growth for a month',
  kind: 'view',
  input: s.object({ month: ym }),
  run: ({ router }, { month }) => go(router, GROWTH, { tab: undefined, month }),
  did: () => 'Showed the month',
  target: () => ({ kind: 'range', id: 'growth' }),
});

export const openChannel = defineAction({
  id: 'growth.openChannel',
  title: 'Open a channel: where its number comes from',
  kind: 'view',
  input: s.object({ channel: id }),
  run: ({ router }, { channel }) => go(router, GROWTH, { tab: undefined, panel: `channel:${channel}` }),
  did: () => 'Opened the channel',
  target: ({ channel }) => ({ kind: 'channel', id: channel }),
});

export const openSources = defineAction({
  id: 'growth.openSources',
  title: 'Open Growth sources',
  kind: 'view',
  input: s.object({}),
  run: ({ router }) => router.navigate({ to: GROWTH, search: { tab: 'sources' } }),
  did: () => 'Opened Sources in Growth',
  target: () => ({ kind: 'page', id: `${GROWTH}?tab=sources` }),
});

export const closeGrowthPanel = defineAction({
  id: 'growth.closePanel',
  title: 'Close the Growth panel',
  kind: 'view',
  input: s.object({}),
  run: ({ router }) => closeHere(router),
  did: () => 'Closed the panel',
});
