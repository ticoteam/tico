import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { fmtDate, fmtMoney } from '@/lib';
import { Button } from '@/kit/Button';
import { BarChart } from '@/kit/Chart';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { Segmented } from '@/kit/Segmented';
import { GroupRow, Table } from '@/kit/Table';
import { List, Section, SidePanel } from '@/pagekinds';
import { lineBudget, LineBudgetDialog } from './Budgets';
import { useCaps } from './caps';
import { FIN, FinanceOwner, useFinanceHead } from './head';
import { budgetDay, cash, colLabel, monShort, nowCol, paceOf, pnlRange, rangeLabel, statement, stepRange, valueAt, type Period, type StatementRow } from './model';
import { ActLink, Meter, Pfig, Prow, readState, usePhone, usePress, useViewAs } from './parts';
import { budgetsQuery, lineQuery, pnlQuery, type LineDetail, type Pnl } from './queries';
import { panelOf, type FinanceSearch } from './search';

const PERIODS = [
  { value: 'month', label: 'Month' },
  { value: 'quarter', label: 'Quarter' },
  { value: 'year', label: 'Year' },
] as const;
const BASES = [
  { value: 'cash', label: 'Cash' },
  { value: 'accrual', label: 'Accrual' },
] as const;

/** The P&L: one line per account, by month, quarter or year, with the plan for the periods ahead. A line opens beside. */
export function PnlPage({ search }: { search: FinanceSearch }) {
  const press = usePress();
  const phone = usePhone();
  const period: Period = search.period ?? 'month';
  const basis = search.basis ?? 'accrual';
  const range = pnlRange(period, search.from);
  const head = useFinanceHead();
  const q = useQuery(pnlQuery({ start: range.start, end: range.end, period, basis }, search.as));
  const open = panelOf(search.panel);
  const lineId = open?.[0] === 'line' ? open[1] : null;
  const step = (dir: -1 | 1) => void press('finance.setRange', { from: stepRange(period, search.from, dir) });

  return (
    <List
      {...head}
      state={readState(q)}
      onRetry={() => void q.refetch()}
      after={
        <span className="range" data-target="range:pnl">
          <IconButton icon="chevron" className="flip" label="Earlier" size="sm" quiet onClick={() => step(-1)} />
          <span>{rangeLabel(range.start, range.end)}</span>
          <IconButton icon="chevron" label="Later" size="sm" quiet onClick={() => step(1)} />
        </span>
      }
      actions={
        <>
          <Menu trigger={<IconButton className="pnl-head-menu" icon="more" label="More" data-phone />}>
            {PERIODS.map((o) => (
              <MenuItem key={o.value} label={o.label} on={o.value === period} onSelect={() => void press('finance.setPeriod', { period: o.value })} />
            ))}
            <MenuRule />
            {BASES.map((o) => (
              <MenuItem key={o.value} label={o.label} on={o.value === basis} onSelect={() => void press('finance.setBasis', { basis: o.value })} />
            ))}
          </Menu>
          {!phone && (
            <>
              <Segmented className="pnl-head-option" label="Period" options={PERIODS} value={period} onValueChange={(v) => void press('finance.setPeriod', { period: v })} />
              <Segmented className="pnl-head-option" label="Basis" options={BASES} value={basis} onValueChange={(v) => void press('finance.setBasis', { basis: v })} />
            </>
          )}
          <FinanceOwner />
        </>
      }
      sections={q.data && (phone ? <PhoneStatement pnl={q.data} open={lineId} /> : <Statement pnl={q.data} open={lineId} />)}
      panel={q.data && lineId && <LinePanel pnl={q.data} account={lineId} />}
    />
  );
}

/** On a phone: the open period's figure for each line, the one before it under the name (finance.js rowsPhone). */
function PhoneStatement({ pnl, open }: { pnl: Pnl; open: string | null }) {
  const rows = statement(pnl);
  const cols = pnl.columns;
  const i = nowCol(cols);
  const prev = i > 0 ? cols[i - 1] : undefined;
  const was = prev ? (pnl.period === 'month' ? monShort(prev.start.slice(0, 7)) : colLabel(prev, pnl.period)) : '';
  const fig = (v: number | null | undefined, pct: boolean) => (v == null ? '' : pct ? `${v}%` : cash(v));
  return (
    <div className="frs">
      {rows.map((r: StatementRow, k) => {
        if (r.kind === 'group')
          return (
            <div key={`g${k}`} className="fr-group">
              <span>{r.name}</span>
            </div>
          );
        const pct = r.kind === 'pct';
        const then = prev ? r.values[i - 1] : null;
        const body = (
          <>
            <span className="fr-main">
              <span className="row-title">{r.name}</span>
              <span className="fr-sub">{then != null && <span>{`${was} ${fig(then, pct)}`}</span>}</span>
            </span>
            <span className="row-fig">{fig(r.values[i], pct)}</span>
          </>
        );
        if (r.kind === 'line')
          return (
            <ActLink
              key={r.id}
              to={FIN}
              search={{ tab: 'pnl', panel: `line:${r.id}` }}
              action="finance.openLine"
              input={{ account: r.id }}
              className={['row fr', r.depth > 0 && 'is-sub', r.id === open && 'is-selected'].filter(Boolean).join(' ')}
              data-target={`line:${r.id}`}
            >
              {body}
            </ActLink>
          );
        return (
          <div key={`${r.kind}${k}`} className={`row fr is-${r.kind}`}>
            {body}
          </div>
        );
      })}
    </div>
  );
}

function Statement({ pnl, open }: { pnl: Pnl; open: string | null }) {
  const rows = statement(pnl);
  const cols = pnl.columns;
  // With a line open beside it the statement keeps four columns, two before this one and one after, so the names keep
  // their room (finance.js: "The statement keeps four months").
  const now = nowCol(cols);
  const shown = cols.map((_, i) => i).filter((i) => !open || cols.length <= 4 || (i >= Math.min(now - 2, cols.length - 4) && i < Math.max(now + 2, 4)));
  const [folded, setFolded] = useState<ReadonlySet<string>>(new Set());
  const toggle = (name: string) => setFolded((was) => (was.has(name) ? new Set([...was].filter((x) => x !== name)) : new Set([...was, name])));
  const cell = (v: number | null, i: number, pct = false) => {
    const c = cols[i];
    const cls = ['num', c?.state === 'future' && 'proj', c?.state === 'current' && 'is-now', v === 0 && 'zero'].filter(Boolean).join(' ');
    return (
      <td key={i} className={cls}>
        {v == null ? '' : pct ? `${v}%` : cash(v)}
      </td>
    );
  };
  // Each row with the group it is under: a folded group hides its lines and keeps its totals.
  const under: string[] = [];
  for (const r of rows) under.push(r.kind === 'group' ? r.name : (under.at(-1) ?? ''));
  return (
    <div className="t-scroll">
      <Table label="P&L" stick>
        <colgroup>
          <col />
          {shown.map((i) => (
            <col key={cols[i]!.start} className="t-col" />
          ))}
        </colgroup>
        <thead>
          <tr>
            <th scope="col">Line</th>
            {shown.map((i) => {
              const c = cols[i]!;
              return (
                <th key={c.start} scope="col" className={c.state === 'current' ? 'num is-now' : c.state === 'future' ? 'num proj' : 'num'}>
                  {colLabel(c, pnl.period)}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((r: StatementRow, k) => {
            if (r.kind === 'group')
              return <GroupRow key={`g${k}`} name={r.name} open={!folded.has(r.name)} onToggle={() => toggle(r.name)} span={shown.length + 1} />;
            if (r.kind === 'line' && folded.has(under[k] ?? '')) return null;
            if (r.kind === 'line')
              return (
                <tr key={r.id} className={[r.depth > 0 && 'is-sub', r.id === open && 'is-selected'].filter(Boolean).join(' ') || undefined} data-target={`line:${r.id}`}>
                  <td className="t-name">
                    <ActLink to={FIN} search={{ tab: 'pnl', panel: `line:${r.id}` }} action="finance.openLine" input={{ account: r.id }} className="t-link">
                      {r.name}
                    </ActLink>
                    {r.locked && <Icon name="lock" />}
                  </td>
                  {shown.map((i) => cell(r.values[i] ?? null, i))}
                </tr>
              );
            return (
              <tr key={`${r.kind}${k}`} className={`is-${r.kind}`}>
                <td className="t-name">{r.name}</td>
                {shown.map((i) => cell(r.values[i] ?? null, i, r.kind === 'pct'))}
              </tr>
            );
          })}
        </tbody>
      </Table>
    </div>
  );
}

type By = 'vendor' | 'team' | 'teammate';
const BY = [
  { value: 'vendor', label: 'Vendor' },
  { value: 'team', label: 'Team' },
  { value: 'teammate', label: 'Teammate' },
] as const;
const groupsOf = (d: LineDetail | undefined, by: By) => (by === 'vendor' ? d?.by_vendor : by === 'team' ? d?.by_team : d?.by_teammate) ?? [];
const nameOf = (label: string, key: string) => (key === ' other' ? 'Other' : key === ' unattributed' ? 'Not attributed' : label);

/** One line of the statement, opened: this period against its budget, its periods, who spent it, how it was paid. */
function LinePanel({ pnl, account }: { pnl: Pnl; account: string }) {
  const press = usePress();
  const [by, setBy] = useState<By>('vendor');
  const row = pnl.rows.find((r) => r.account_id === account);
  const cols = pnl.columns;
  const i = nowCol(cols);
  const col = cols[i];
  const prev = i > 0 ? cols[i - 1] : undefined;
  const as = useViewAs();
  const now = useQuery(lineQuery(account, col?.state === 'future' ? undefined : col, as));
  const was = useQuery(lineQuery(account, prev, as));
  // A budget is added here rarely: the line's own when it has none, else a vendor's under it (finance-pnl.ts:780).
  // finance.budgets, as useMay reads it (owner, the right, or finance.admin); reading as a teammate is read only.
  const { caps } = useCaps();
  const may = !as && !!caps?.budgets;
  const budgets = useQuery({ ...budgetsQuery(budgetDay(), as), enabled: may });
  const [adding, setAdding] = useState(false);
  if (!row || !col) return null;
  const list = budgets.data?.budgets;
  const own = list ? lineBudget(list, account) : undefined;
  const add = may && !!list && row.type === 'expense' && !row.private && (!own || own.period === 'month');
  const v = valueAt(row, cols, i);
  const budget = row.budget?.[i] ?? null;
  const span = pnl.period === 'month' ? 'this month' : `this ${pnl.period}`;
  const past = cols.map((c, k) => ({ c, v: row.actual?.[k] ?? 0 })).filter(({ c }) => c.state !== 'future');
  const max = Math.max(1, ...past.map((p) => p.v / 100)) * 1.15;
  const prevBy = new Map(groupsOf(was.data, by).map((g) => [g.key, g.amount]));
  const prevName = prev ? (pnl.period === 'month' ? monShort(prev.start.slice(0, 7)) : prev.label) : '';
  const groups = groupsOf(now.data, by);
  return (
    <SidePanel
      title={row.name}
      target={`line:${account}`}
      onClose={() => void press('finance.closePanel', {})}
      foot={
        add && (
          <>
            <Button variant="quiet" onClick={() => setAdding(true)}>
              {own ? 'Vendor budget' : 'Line budget'}
            </Button>
            {adding && <LineBudgetDialog line={row} parent={own} onOpenChange={setAdding} />}
          </>
        )
      }
    >
      <div className="money pbody">
        <Pfig big={cash(v)} sub={budget && col.state !== 'future' ? `of ${cash(budget)} ${span}` : colLabel(col, pnl.period)}>
          {budget ? <Meter pct={((v ?? 0) / budget) * 100} pace={pnl.period === 'month' && col.state === 'current' ? paceOf().pace : undefined} /> : null}
        </Pfig>
        <Section name="By month">
          <div className="chart-sec">
            <BarChart
              points={past.map((p) => p.v / 100)}
              width={304}
              height={148}
              max={max}
              ticks={[]}
              fmt={(x) => fmtMoney(x)}
              xLabels={past.map((p, k) => [k, pnl.period === 'month' ? monShort(p.c.start.slice(0, 7)) : p.c.label] as const)}
              each
              label={`${row.name} by ${pnl.period}`}
            />
          </div>
        </Section>
        <Section name="Spent by" actions={<Segmented label="Spent by" options={BY} value={by} onValueChange={setBy} />}>
          {groups.map((g) => (
            <Prow key={g.key} name={nameOf(g.label, g.key)} sub={prevBy.has(g.key) ? `${prevName} ${cash(prevBy.get(g.key))}` : ''} fig={cash(g.amount)} />
          ))}
        </Section>
        <Section name="Latest">
          {(now.data?.entries ?? []).map((e) => (
            <Prow key={`${e.entry_id}:${e.line_id}`} name={e.vendor_name || e.description || e.memo || 'Entry'} sub={fmtDate(e.date)} fig={cash(e.amount, true)} />
          ))}
        </Section>
      </div>
    </SidePanel>
  );
}
