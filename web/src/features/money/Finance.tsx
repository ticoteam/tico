import { useQuery } from '@tanstack/react-query';
import { fmtDate } from '@/lib';
import { Button } from '@/kit/Button';
import { Lines, Sparkline } from '@/kit/Chart';
import { FlowMark } from '@/kit/FlowMark';
import { Table } from '@/kit/Table';
import { Dashboard, FiguresStrip, List, Section } from '@/pagekinds';
import { AccessPage } from './Access';
import { BooksPage } from './Books';
import { useBooksReturn } from './booksReturn';
import { BudgetsPage } from './Budgets';
import { useCaps } from './caps';
import { axis, cash, connected, dayOf, monShort, monthSpan, nowCol, ymOf } from './model';
import { FIN, FinanceOwner, plural, TAB_NAME, useFinanceHead } from './head';
import { ActLink, readState, usePress, useViewAs } from './parts';
import { PnlPage } from './Pnl';
import { SourcesPage } from './Sources';
import {
  billsQuery,
  briefQuery,
  inboxQuery,
  invoicesQuery,
  metricQuery,
  pnlQuery,
  sourcesQuery,
} from './queries';
import { FINANCE_TAB_IDS, type FinanceSearch, type FinanceTab } from './search';

/** Finance: its pages under one title, the tab in `?tab=`, each only for a viewer who may open it (finTabShown). The
    overview is a dashboard; the rest are lists. A page outside the viewer's rights says so; the plain address opens
    the first page they have, as the old app's redirect did (finance.ts finShow). */
export function FinancePage({ search }: { search: FinanceSearch }) {
  const [failure, dismiss] = useBooksReturn(search);
  const { shown, pending, caps } = useCaps();
  // The plain address waits for the access list, so a teammate who has only My budget lands there, not on the overview.
  if (!search.tab && pending) return <Waiting />;
  const tab: FinanceTab = search.tab ?? (shown('overview') ? 'overview' : (FINANCE_TAB_IDS.find(shown) ?? 'overview'));
  // My budget is a tab only for a teammate who sees nothing else, but whoever reads every budget (the owner) may open it
  if (!shown(tab) && !(tab === 'mine' && caps?.budgets)) return <Closed />;
  if (tab === 'overview') return <Overview mayReadSources={shown('sources')} />;
  if (tab === 'pnl') return <PnlPage search={search} />;
  if (tab === 'budgets') return <BudgetsPage search={search} />;
  if (tab === 'mine') return <BudgetsPage search={search} mine />;
  if (tab === 'sources') return <SourcesPage failure={failure} onDismiss={dismiss} search={search} />;
  if (tab === 'access') return <AccessPage search={search} />;
  return <BooksPage tab={tab} />;
}

/** A page this viewer may not open: no access, without Try again. */
function Closed() {
  const head = useFinanceHead();
  return <List {...head} state="no-access" sections={null} />;
}

function Waiting() {
  const head = useFinanceHead();
  return <List {...head} state="loading" sections={null} />;
}

/** Where each kind of waiting item is dealt with. */
const KIND_TAB: Record<string, FinanceTab> = {
  uncategorized: 'transactions',
  unmatched_receipt: 'transactions',
  missing_receipt: 'transactions',
  unreconciled: 'transactions',
  bill_to_enter: 'payables',
  bill_due: 'payables',
  bill_email: 'payables',
  invoice_overdue: 'receivables',
  budget_near_limit: 'budgets',
  sync_failed: 'sources',
  period_ready: 'balance',
};

/** The overview: four figures, then one column of sections as the mock stacks them (finance.js `overview`): revenue and
    spend by week, what waits on you, what changed. Each Section puts its name on the page edge itself, so none sits in
    the kind's padded chart slot or its two-column widget grid. */
function Overview({ mayReadSources }: { mayReadSources: boolean }) {
  const press = usePress();
  const head = useFinanceHead();
  const as = useViewAs();
  const now = new Date();
  const ym = ymOf(now);
  const weeks = new Date(now);
  weeks.setDate(weeks.getDate() - ((weeks.getDay() + 6) % 7) - 77);
  const sources = useQuery({ ...sourcesQuery(as), enabled: mayReadSources });
  const month = useQuery(pnlQuery({ ...monthSpan(ym), period: 'month', basis: 'accrual' }, as));
  const weekly = useQuery(pnlQuery({ start: dayOf(weeks), end: dayOf(now), period: 'week', basis: 'accrual' }, as));
  const cashNow = useQuery(metricQuery('cash', as));
  const invoices = useQuery(invoicesQuery(as));
  const bills = useQuery(billsQuery(as));
  const inbox = useQuery(inboxQuery(as));
  const brief = useQuery(briefQuery(as));

  const live = connected(sources.data?.groups);
  const state = mayReadSources ? readState(sources, !live) : 'ready';
  const m = month.data;
  const net = m ? (m.net[nowCol(m.columns)] ?? null) : null;
  const cv = cashNow.data?.value;
  const w = weekly.data;
  const income = (w?.income ?? []).map((c) => c / 100);
  const expense = (w?.expense ?? []).map((c) => c / 100);
  const scale = axis([...income, ...expense]);
  const n = income.length;
  const xl = w ? [0, 4, 8, n - 1].filter((i, k, a) => i >= 0 && i < n && a.indexOf(i) === k).map((i) => [i, fmtDate(w.columns[i]?.start ?? '', now)] as const) : [];
  const waits = inbox.data?.items ?? [];
  const changed = brief.data?.lines ?? [];

  return (
    <Dashboard
      {...head}
      state={state}
      onRetry={() => void sources.refetch()}
      actions={<FinanceOwner />}
      empty={{
        title: 'No source connected',
        action: (
          <Button variant="primary" onClick={() => void press('finance.setTab', { tab: 'sources' })}>
            Add source
          </Button>
        ),
      }}
      figures={
        <FiguresStrip
            figures={[
              { label: 'Cash', value: typeof cv === 'number' ? cash(cv) : '', target: 'figure:cash' },
              {
                label: 'Net income',
                value: cash(net),
                sub: `${monShort(ym)} to date`,
                spark: n > 1 ? <Sparkline points={income.map((v, i) => v - (expense[i] ?? 0))} /> : undefined,
                target: 'figure:net',
              },
              {
                label: 'Owed to you',
                value: invoices.data ? cash(invoices.data.open.remaining) : '',
                sub: invoices.data ? plural(invoices.data.open.count, 'invoice') : undefined,
                target: 'figure:owed',
              },
              {
                label: 'You owe',
                value: bills.data ? cash(bills.data.open.remaining) : '',
                sub: bills.data ? plural(bills.data.open.count, 'bill') : undefined,
                target: 'figure:owe',
              },
            ]}
          />
      }
      widgets={
        <div className="stack">
          {n > 1 && (
            <Section name="Revenue and spend, by week">
              <div className="chart-sec">
                <Lines
                  series={[
                    { name: 'Revenue', end: cash(w?.income[n - 1]), pts: income, cls: 'l1' },
                    { name: 'Spend', end: cash(w?.expense[n - 1]), pts: expense, cls: 'l2' },
                  ]}
                  height={184}
                  {...scale}
                  fmt={(v) => cash(v * 100)}
                  xl={xl}
                  label={`Revenue and spend, ${n} weeks`}
                />
              </div>
            </Section>
          )}
          <Section name="Waits on you" count={waits.length}>
            <Table label="Waits on you">
              <tbody>
                {waits.map((it) => {
                  const tab = KIND_TAB[it.kind] ?? 'overview';
                  return (
                    <tr key={it.id}>
                      <td className="t-name">
                        <FlowMark size={12} className="t-mark" />
                        <ActLink to={FIN} search={{ tab, panel: undefined }} action="finance.setTab" input={{ tab }}>
                          {it.label}
                        </ActLink>
                      </td>
                      <td className="t-words">{TAB_NAME[tab]}</td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          </Section>
          <Section name="Changed" count={changed.length}>
            <Table label="Changed">
              <tbody>
                {changed.map((line, i) => (
                  <tr key={i}>
                    <td className="t-name">{line}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Section>
        </div>
      }
    />
  );
}
