import { useQuery } from '@tanstack/react-query';
import { fmtDate } from '@/lib';
import { Button } from '@/kit/Button';
import { Sparkline } from '@/kit/Chart';
import { Table } from '@/kit/Table';
import { Dashboard, FiguresStrip, Section } from '@/pagekinds';
import { BooksPage } from './Books';
import { BudgetsPage } from './Budgets';
import { axis, cash, connected, dayOf, monShort, monthSpan, nowCol, ymOf } from './model';
import { FIN, FinanceTabs, financeHead, plural, TAB_NAME } from './head';
import { ActLink, LineChart, usePress } from './parts';
import { PnlPage } from './Pnl';
import {
  billsQuery,
  briefQuery,
  inboxQuery,
  invoicesQuery,
  metricQuery,
  pnlQuery,
  sourcesQuery,
} from './queries';
import type { FinanceSearch, FinanceTab } from './search';

/** Finance: twelve pages under one title, the tab in `?tab=`. The overview is a dashboard; the rest are lists. */
export function FinancePage({ search }: { search: FinanceSearch }) {
  const tab = search.tab ?? 'overview';
  if (tab === 'overview') return <Overview />;
  if (tab === 'pnl') return <PnlPage search={search} />;
  if (tab === 'budgets') return <BudgetsPage search={search} />;
  return <BooksPage tab={tab} />;
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

/** The overview: four figures, revenue and spend by week, what waits on you and what changed. */
function Overview() {
  const press = usePress();
  const now = new Date();
  const ym = ymOf(now);
  const weeks = new Date(now);
  weeks.setDate(weeks.getDate() - ((weeks.getDay() + 6) % 7) - 77);
  const sources = useQuery(sourcesQuery);
  const month = useQuery(pnlQuery({ ...monthSpan(ym), period: 'month', basis: 'accrual' }));
  const weekly = useQuery(pnlQuery({ start: dayOf(weeks), end: dayOf(now), period: 'week', basis: 'accrual' }));
  const cashNow = useQuery(metricQuery('cash'));
  const invoices = useQuery(invoicesQuery);
  const bills = useQuery(billsQuery);
  const inbox = useQuery(inboxQuery);
  const brief = useQuery(briefQuery);

  const live = connected(sources.data?.groups);
  const state = sources.isPending ? 'loading' : sources.isError ? 'fault' : live ? 'ready' : 'empty';
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
      {...financeHead}
      state={state}
      onRetry={() => void sources.refetch()}
      empty={{
        title: 'No source connected',
        action: (
          <Button variant="primary" onClick={() => void press('finance.addSource', {})}>
            Add source
          </Button>
        ),
      }}
      figures={
        <>
          <FinanceTabs on="overview" />
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
        </>
      }
      chart={
        n > 1 && (
          <Section name="Revenue and spend, by week">
            <div className="chart-sec">
              <LineChart
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
        )
      }
      widgets={
        <>
          <Section name="Waits on you" count={waits.length}>
            <Table label="Waits on you">
              <tbody>
                {waits.map((it) => {
                  const tab = KIND_TAB[it.kind] ?? 'overview';
                  return (
                    <tr key={it.id}>
                      <td className="t-title">
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
                    <td className="t-title">{line}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Section>
        </>
      }
    />
  );
}
