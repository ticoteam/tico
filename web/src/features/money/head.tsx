import { useQuery } from '@tanstack/react-query';
import { useLocation } from '@tanstack/react-router';
import { useActor } from '@/data';
import { chatAddress } from '@/lib';
import { OwnerButton } from '@/kit/OwnerButton';
import type { PageBaseProps, PageTab } from '@/pagekinds';
import { useCaps } from './caps';
import { ActLink, useViewAs } from './parts';
import { pageOwnersQuery } from './queries';
import { FINANCE_TABS } from './search';
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

/** Finance Owner's face in the head, before the filled button: it opens its chat beside the page (`?chat=`, the shell's).
    Kept on a phone. Nothing when the page has no owner. */
export function FinanceOwner() {
  const bot = useQuery(pageOwnersQuery).data?.finance?.bot ?? null;
  const actor = bot && !bot.includes(':') ? `bot:${bot}` : bot;
  const who = useActor(actor);
  const addr = actor ? chatAddress(actor, who?.kind) : '';
  const [path, search] = useLocation({ select: (l) => `${l.pathname}?${l.searchStr.replace(/^\?/, '')}` }).split('?');
  const q = new URLSearchParams(search);
  const on = !!addr && q.get('chat') === addr;
  if (!actor) return null;
  q.set('chat', addr);
  const href = `${path}?${q.toString()}`;
  return (
    <span className="gm-ask" data-phone>
      <OwnerButton name={who?.name ?? 'Finance Owner'} kind={who?.kind ?? 'agent'} hue={who?.hue} live={who?.working} href={href} on={on} target={`chat:${addr}`} />
    </span>
  );
}

export const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;
