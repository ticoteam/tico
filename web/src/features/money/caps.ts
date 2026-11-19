import { useQuery } from '@tanstack/react-query';
import { meQuery } from '@/data';
import { useViewAs } from './parts';
import { accessQuery, type Access, type AccessTeammate } from './queries';
import type { FinanceTab } from './search';

/* Which Finance pages a viewer has, from their row on GET /api/v2/finance/access (ui/app/finance.ts finCapsOf and
   finTabShown). The server still refuses each read it does not allow; this only keeps the tab row to what opens. */

/** A view grant as the access list carries it (backend/finance/tab.py access). */
export interface Grant {
  id: string;
  what: string;
  whose?: string | null;
  whose_team?: string | null;
  account_ids?: string[] | null;
  granted_by?: string | null;
  expires_at?: string | null;
  status?: string;
  live?: boolean;
}
export interface Team {
  id: string;
  name: string;
  parent?: string | null;
  lead?: string | null;
}
/** A teammate's row with the fields the route adds past the narrow shared type. */
export type Mate = Omit<AccessTeammate, 'grants'> & {
  grants?: Grant[];
  /** Their own active budgets: with some and no other view, Finance is My budget alone. */
  budgets?: number;
  attribute?: boolean | null;
  /** Whether the viewer may give this teammate more (check_grant). */
  may_grant?: boolean;
  reports_to?: string | null;
  default_team?: string | null;
};
export type AccessList = Omit<Access, 'teammates' | 'teams'> & {
  teammates: Mate[];
  teams?: Team[];
  /** Who approves finance-admin actions; empty is the owner (owners only). */
  finance_approver?: string | null;
};

export type Caps = NonNullable<ReturnType<typeof capsOf>>;

/** What a teammate's Finance holds; null when they have no row. */
export function capsOf(acc: AccessList | null | undefined, who: string) {
  const row = acc?.teammates?.find((t) => t.teammate === who);
  if (!row) return null;
  const live = (row.grants ?? []).filter((g) => g.live && g.status === 'active');
  const what = new Set(live.map((g) => g.what));
  const owner = row.default === 'everything';
  const all = owner || what.has('everything');
  const rights = new Set(row.rights ?? []);
  const may = (right: string) => owner || rights.has(right) || rights.has('finance.admin');
  // Finance past one's own budgets: a default view, a grant, or finance.admin (backend/finance/handlers.py reads_budget).
  const sees = all || row.default !== 'none' || live.length > 0 || rights.has('finance.admin');
  const mine = !owner && (row.budgets ?? 0) > 0;
  const attribute = typeof row.attribute === 'boolean' ? row.attribute : owner || rights.has('finance.attribution') || rights.has('finance.admin');
  return {
    owner,
    all,
    rights,
    sees,
    mine,
    attribute,
    any: sees || mine || attribute,
    balance: all || what.has('balance_sheet') || what.has('bs_lines'),
    sources: all || what.has('sources'),
    budgets: owner || rights.has('finance.budgets') || rights.has('finance.admin'),
    payables: all || what.has('payables') || may('finance.ap'),
    receivables: all || what.has('receivables') || may('finance.ar'),
  };
}

/** Whether a tab opens for these caps. Tax is filed whole, so only a viewer who sees everything reads it. */
export function tabShown(tab: FinanceTab, caps: Caps, acc: AccessList | null | undefined, viewing: boolean): boolean {
  if (!caps.any) return false;
  if (tab === 'mine') return caps.mine;
  if (tab === 'attribution') return caps.attribute;
  if (!caps.sees) return false;
  switch (tab) {
    case 'balance':
      return caps.balance;
    case 'budgets':
      return caps.budgets;
    case 'sources':
      return caps.sources;
    case 'tax':
      return caps.all;
    case 'payables':
      return caps.payables;
    case 'receivables':
      return caps.receivables;
    case 'access':
      return !viewing && (!!acc?.can_view_as || caps.rights.has('finance.admin') || (acc?.teammates?.length ?? 0) > 1);
    default:
      return true;
  }
}

/** The viewer's caps (the teammate's, while the owner reads as one). Until the list says, every tab shows and each
    read's own answer decides (a refusal is the page's no-access state). */
export function useCaps() {
  const me = useQuery(meQuery).data?.id;
  const q = useQuery(accessQuery);
  const acc = q.data as AccessList | null | undefined;
  const as = useViewAs();
  const who = as && acc?.can_view_as ? as : me;
  const caps = who ? capsOf(acc, who) : null;
  const viewing = !!as && who !== me;
  return { acc, me, caps, pending: q.isPending, shown: (tab: FinanceTab) => !caps || tabShown(tab, caps, acc, viewing) };
}
