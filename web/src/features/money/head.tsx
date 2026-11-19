import type { PageBaseProps, PageTab } from '@/pagekinds';
import { FINANCE_TABS, type FinanceTab } from './search';
import './money.css';

export const FIN = '/company/finance';
export const GROWTH = '/company/growth';

/** Finance's tab row: the twelve pages, each `?tab=` (Overview has none). A tab link starts its page clean. */
const FINANCE_PAGE_TABS: readonly PageTab[] = FINANCE_TABS.map(([id, label]) => ({
  label,
  to: FIN,
  target: `tab:${id}`,
  ...(id === 'overview' ? {} : { search: { tab: id } }),
}));

/** What every Finance page shares: its name, its address, its tabs and the class the page's own CSS hangs on. */
export const financeHead = { title: 'Finance', path: FIN, className: 'money', tabs: FINANCE_PAGE_TABS } satisfies Partial<PageBaseProps> & {
  tabs: readonly PageTab[];
};

export const TAB_NAME = Object.fromEntries(FINANCE_TABS) as Record<FinanceTab, string>;

export const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;
