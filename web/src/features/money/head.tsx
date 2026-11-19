import type { PageBaseProps } from '@/pagekinds';
import { SearchTabs } from './parts';
import { FINANCE_TABS, type FinanceTab } from './search';
import './money.css';

export const FIN = '/company/finance';
export const GROWTH = '/company/growth';

/** What every Finance page shares: its name, its address, and the class the page's own CSS hangs on. */
export const financeHead = { title: 'Finance', path: FIN, className: 'money' } satisfies Partial<PageBaseProps>;

const TABS = FINANCE_TABS.map(([id, label]) => ({
  id,
  label,
  search: { tab: id === 'overview' ? undefined : id, panel: undefined },
  action: 'finance.setTab',
  input: { tab: id },
}));

export const TAB_NAME = Object.fromEntries(FINANCE_TABS) as Record<FinanceTab, string>;

/** Finance's tab row: the twelve pages, each a link to `?tab=`. */
export function FinanceTabs({ on }: { on: FinanceTab }) {
  return <SearchTabs to={FIN} tabs={TABS} on={on} label="Finance pages" />;
}

export const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;
