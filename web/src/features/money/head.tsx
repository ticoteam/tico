import { useActor } from '@/data';
import type { PageBaseProps, PageTab } from '@/pagekinds';
import { useCaps } from './caps';
import { ActLink, useViewAs } from './parts';
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

/** The head, and while the owner reads Finance as a teammate, whose view it is (with Stop) and tabs that keep it. The
    tabs are the pages the viewer (or the teammate read as) may open (finTabShown). */
export function useFinanceHead() {
  const as = useViewAs();
  const who = useActor(as ?? null);
  const { shown } = useCaps();
  const tabs = FINANCE_PAGE_TABS.filter((_, i) => shown(FINANCE_TABS[i]![0]));
  if (!as) return { ...financeHead, tabs };
  const name = who?.name ?? as.slice(as.indexOf(':') + 1);
  const stop = (
    <ActLink to={FIN} search={{ as: undefined }} action="finance.viewAs" input={{}}>
      Stop
    </ActLink>
  );
  return {
    ...financeHead,
    tabs: tabs.map((t) => ({ ...t, search: { ...t.search, as } })),
    sub: (
      <span className="view-as" role="status">
        {`Viewing as ${name}`}
        {stop}
      </span>
    ),
    // A page closed to the teammate draws no head: Stop stays reachable from the no-access state itself.
    noAccess: { text: `Viewing as ${name}`, actions: stop },
  };
}

export const TAB_NAME = Object.fromEntries(FINANCE_TABS) as Record<FinanceTab, string>;

export const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;
