import { useQuery } from '@tanstack/react-query';
import { useState, type FocusEvent, type FormEvent } from 'react';
import { meQuery } from '@/data';
import { Button } from '@/kit/Button';
import { Field } from '@/kit/Field';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Pill } from '@/kit/Pill';
import { Segmented } from '@/kit/Segmented';
import { EmptyRow, GroupRow, Table } from '@/kit/Table';
import { List, Section, SidePanel } from '@/pagekinds';
import { Ask, useAsk, useMay } from './dialogs';
import { FIN, FinanceOwner, useFinanceHead } from './head';
import { addMonths, budgetDay, budgetGroups, cash, centsOf, monShort, monthName, monthSpan, paceOf, type BudgetLine, type Period } from './model';
import { ActLink, Meter, Pfig, PhoneRows, Prow, readState, usePhone, usePress, useViewAs } from './parts';
import { budgetHistoryQuery, budgetsQuery, pnlQuery, type Budget, type BudgetHistory } from './queries';
import { panelOf, type FinanceSearch } from './search';

/** A line's own budget: on the line, for no team, teammate or vendor, under no other (finance-pnl.ts finLineBudget). */
export const lineBudget = (budgets: readonly Budget[], line: string) => {
  const live = budgets.filter((b) => b.active !== false && !b.catch_all);
  const ids = new Set(live.map((b) => b.id));
  return live.find((b) => b.account_id === line && !b.team && !b.teammate && !b.vendor && (!b.parent_id || !ids.has(b.parent_id)));
};

const PERIODS = [
  { value: 'month', label: 'Month' },
  { value: 'quarter', label: 'Quarter' },
  { value: 'year', label: 'Year' },
] as const;

const DAY = 86_400_000;
const days = (a: string, b: string) => Math.round((new Date(`${b}T12:00:00`).getTime() - new Date(`${a}T12:00:00`).getTime()) / DAY) + 1;

/** The quarter or year that holds today, from the budget report: its last bucket is the current one, whatever month the
    fiscal year starts in, because the window opens before that bucket does. */
function spanOf(per: Exclude<Period, 'month'>) {
  const ym = budgetDay().slice(0, 7);
  return { start: `${addMonths(ym, per === 'quarter' ? -2 : -11)}-01`, end: budgetDay() };
}

/** Each budget with its amount and spend for the current quarter or year instead of this month. */
function inSpan(budgets: readonly Budget[], h: BudgetHistory | undefined): Budget[] {
  const i = (h?.columns.length ?? 0) - 1;
  const by = new Map((h?.budgets ?? []).map((r) => [r.id, r]));
  return budgets.map((b) => {
    const r = by.get(b.id);
    return { ...b, state: { amount: r?.budget[i] ?? null, actual: r?.actual?.[i] ?? null } };
  });
}

/** Where today is in the shown period: its share gone, and "Day 50 of 92". */
function paceIn(h: BudgetHistory | undefined) {
  const col = h?.columns.at(-1);
  if (!col?.end) return { pace: 0, words: '' };
  const all = days(col.start, col.end);
  const gone = Math.min(all, days(col.start, budgetDay()));
  return { pace: (gone / all) * 100, words: `Day ${gone} of ${all}` };
}

/**
 * Budgets: each line against its budget, by section, for this month, quarter or year, with where today is in it. A row
 * opens beside. `mine`: My budget, a teammate's own budgets, flat and read only (finance-budgets.ts:122).
 */
export function BudgetsPage({ search, mine = false }: { search: FinanceSearch; mine?: boolean }) {
  const press = usePress();
  const phone = usePhone();
  const per: Period = search.period ?? 'month';
  const now = budgetDay().slice(0, 7);
  const head = useFinanceHead();
  const me = useQuery(meQuery).data?.id;
  const list = useQuery(budgetsQuery(budgetDay(), search.as));
  const span = per === 'month' ? null : spanOf(per);
  const report = useQuery({ ...budgetHistoryQuery(span?.start ?? '', span?.end ?? '', search.as, per), enabled: !!span });
  const current = span && list.isSuccess ? report : list;
  const pnl = useQuery(pnlQuery({ ...monthSpan(now), period: 'month', basis: 'accrual' }, search.as));
  const pace = span ? paceIn(report.data) : paceOf();
  const lines = pnl.data?.rows ?? [];
  const held = list.data?.budgets ?? [];
  const budgets = span ? inSpan(held, report.data) : held;
  const who = search.as ?? me;

  const made = budgetGroups(mine ? budgets.filter((b) => b.teammate === who) : budgets, lines, pace.pace);
  const { spent, amount } = made;
  let { groups } = made;
  if (mine) groups = [{ name: '', rows: groups.flatMap((g) => g.rows).sort((a, b) => a.budget.name.localeCompare(b.budget.name)) }];
  const all = groups.flatMap((g) => g.rows);
  const open = panelOf(search.panel);
  const id = !mine && open?.[0] === 'budget' ? open[1] : null;
  const row = id ? all.find((r) => r.budget.id === id) : undefined;
  const [folded, setFolded] = useState<ReadonlySet<string>>(new Set());
  const toggle = (name: string) => setFolded((was) => (was.has(name) ? new Set([...was].filter((x) => x !== name)) : new Set([...was, name])));
  const none = mine ? 'No budget yet' : 'No budgets yet';
  const meter = (r: { spent: number | null; amount: number | null }) => (r.amount ? <Meter pct={((r.spent ?? 0) / r.amount) * 100} pace={pace.pace} /> : null);
  // What is left of a budget; nothing where there is no budget.
  const left = (r: { spent: number | null; amount: number | null }) => {
    if (!r.amount) return '';
    const v = r.amount - (r.spent ?? 0);
    return v < 0 ? <span className="word-late">{cash(v)}</span> : cash(v);
  };
  const name = (r: BudgetLine) =>
    mine ? (
      r.budget.name
    ) : (
      <ActLink to={FIN} search={{ tab: 'budgets', panel: `budget:${r.budget.id}` }} action="budget.openRow" input={{ budget: r.budget.id }} className="t-link">
        {r.budget.name}
      </ActLink>
    );
  const titled = (r: BudgetLine) => (
    <>
      {name(r)}
      {r.flag && <Pill flag>{r.flag}</Pill>}
    </>
  );

  return (
    <List
      {...head}
      state={readState(current)}
      onRetry={() => void current.refetch()}
      actions={
        <>
          <span data-phone data-target="range:budgets">
            <Segmented label="Period" options={PERIODS} value={per} onValueChange={(v) => void press('budget.setPeriod', { period: v })} />
          </span>
          <FinanceOwner />
        </>
      }
      sections={
        phone ? (
          <PhoneRows
            label={mine ? 'My budget' : 'Budgets'}
            none={none}
            groups={[
              ...groups.map((g) => ({
                name: g.name,
                count: g.rows.filter((r) => r.depth === 0).length,
                rows: g.rows.map((r) => ({
                  key: r.budget.id,
                  title: titled(r),
                  sub: [r.amount != null && `of ${cash(r.amount)}`],
                  fig: cash(r.spent),
                  cls: [r.depth > 0 && 'is-sub', r.budget.id === id && 'is-selected'].filter(Boolean).join(' '),
                  bar: meter(r),
                })),
              })),
              { rows: all.length ? [{ key: 'total', title: 'Total', sub: [`of ${cash(amount)}`], fig: cash(spent), cls: 'is-total' }] : [] },
            ]}
          />
        ) : (
          <div className="t-scroll">
            <Table label={mine ? 'My budget' : 'Budgets'} stick>
              <colgroup>
                <col />
                <col className="t-col" />
                <col className="t-col" />
                {!id && <col className="t-col" />}
                <col className="t-meter" />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">Line</th>
                  <th scope="col" className="num">
                    Spent
                  </th>
                  <th scope="col" className="num">
                    Budget
                  </th>
                  {/* a row open beside it leaves no room for Left: the panel says it */}
                  {!id && (
                    <th scope="col" className="num">
                      Left
                    </th>
                  )}
                  <th scope="col">{pace.words}</th>
                </tr>
              </thead>
              <tbody>
                {all.length === 0 && <EmptyRow span={id ? 4 : 5}>{none}</EmptyRow>}
                {groups.map((g) => [
                  g.name && (
                    <GroupRow
                      key={`g:${g.name}`}
                      name={g.name}
                      open={!folded.has(g.name)}
                      onToggle={() => toggle(g.name)}
                      span={id ? 4 : 5}
                    />
                  ),
                  ...(folded.has(g.name) ? [] : g.rows).map((r) => (
                    <tr
                      key={r.budget.id}
                      className={[r.depth > 0 && 'is-sub', r.budget.id === id && 'is-selected'].filter(Boolean).join(' ') || undefined}
                      data-target={`budget:${r.budget.id}`}
                    >
                      <td className="t-name">{titled(r)}</td>
                      <td className="num t-fig">{cash(r.spent)}</td>
                      <td className="num">{cash(r.amount)}</td>
                      {!id && <td className="num">{left(r)}</td>}
                      <td className="t-bar">{meter(r)}</td>
                    </tr>
                  )),
                ])}
                {all.length > 0 && (
                  <tr className="is-total">
                    <td className="t-name">Total</td>
                    <td className="num">{cash(spent)}</td>
                    <td className="num">{cash(amount)}</td>
                    {!id && <td className="num">{left({ spent, amount })}</td>}
                    <td className="t-bar">{meter({ spent, amount })}</td>
                  </tr>
                )}
              </tbody>
            </Table>
          </div>
        )
      }
      panel={row && <BudgetPanel line={row} per={per} pace={pace} kids={all.filter((r) => r.budget.parent_id === row.budget.id)} lines={lines} budgets={held} />}
    />
  );
}

const leftOf = (r: { spent: number | null; amount: number | null }) => (r.amount == null ? null : r.amount - (r.spent ?? 0));

/** What is left; over budget it is a negative figure in strong ink. */
function Left({ v }: { v: number | null }) {
  if (v == null) return null;
  return v < 0 ? <span className="word-late">{cash(v)}</span> : <>{cash(v)}</>;
}

/** One budget, opened: the period so far, its amount to change in place (this month's), its teams, and its months: three
    before, this one to date, and two planned ahead. */
function BudgetPanel({
  line,
  per,
  pace,
  kids,
  lines,
  budgets,
}: {
  line: BudgetLine;
  per: Period;
  pace: { pace: number; words: string };
  kids: BudgetLine[];
  lines: readonly { account_id: string; name: string }[];
  budgets: readonly Budget[];
}) {
  const press = usePress();
  const { budget: b, spent, amount } = line;
  const now = budgetDay().slice(0, 7);
  const [adding, setAdding] = useState(false);
  // Without finance.budgets, or reading as a teammate, it is read only: no amount to change, no Remove. Only a monthly
  // budget's month is its amount (finance-budgets.ts finBudgetEditable).
  const as = useViewAs();
  const edits = useMay()('finance.budgets');
  const editable = edits && per === 'month' && b.period === 'month' && !b.catch_all;
  const history = useQuery(budgetHistoryQuery(`${addMonths(now, -3)}-01`, monthSpan(addMonths(now, 2)).end, as));
  const months = history.data?.budgets.find((h) => h.id === b.id);
  const own = b.account_id ? lineBudget(budgets, b.account_id) : undefined;
  const pnlLine = lines.find((r) => r.account_id === b.account_id);
  const save = (text: string) => {
    const cents = centsOf(text);
    if (cents == null || cents === amount) return;
    void press('budget.updateRow', { budget: b.id, amount: cents / 100 });
  };
  const typed = (form: HTMLFormElement) => String(new FormData(form).get('amount') ?? '');
  const onSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    save(typed(e.currentTarget));
  };
  return (
    <SidePanel
      title={b.name}
      target={`budget:${b.id}`}
      onClose={() => void press('finance.closePanel', {})}
      foot={
        edits && (
          <>
            {/* A vendor's budget sits under its line's own monthly one (finance-pnl.ts:780). */}
            {own?.id === b.id && b.period === 'month' && pnlLine && (
              <Button variant="quiet" onClick={() => setAdding(true)}>
                Vendor budget
              </Button>
            )}
            <Button variant="quiet" onClick={() => void press('budget.removeRow', { budget: b.id })}>
              Remove
            </Button>
            {adding && pnlLine && <LineBudgetDialog line={pnlLine} parent={b} onOpenChange={setAdding} />}
          </>
        )
      }
    >
      <div className="money pbody">
        <Pfig big={cash(spent)} sub={amount != null ? `of ${cash(amount)} this ${per}` : monthName(now)}>
          {amount ? <Meter pct={((spent ?? 0) / amount) * 100} pace={pace.pace} /> : null}
        </Pfig>
        <Facts label="Budget">
          <LabelValue label="Budget">
            {/* Saved on Enter or when the field is left; `key` resets it when a refusal takes the amount back. */}
            <form key={amount ?? 'none'} onSubmit={onSubmit} onBlur={(e: FocusEvent<HTMLFormElement>) => save(typed(e.currentTarget))}>
              <Field
                label={`Budget for this ${per}`}
                labelHidden
                name="amount"
                defaultValue={amount != null ? cash(amount).replace('$', '') : ''}
                wide={false}
                disabled={!editable}
              />
            </form>
          </LabelValue>
          <LabelValue label="Left" num>
            <Left v={leftOf(line)} />
          </LabelValue>
          {pace.words && <LabelValue label="Today">{pace.words}</LabelValue>}
          {per === 'month' && b.state.pace_end != null && <LabelValue label="Month end at this pace">{`About ${cash(b.state.pace_end)}`}</LabelValue>}
        </Facts>
        {kids.length > 0 && (
          <Section name="By team" count={kids.length}>
            {kids.map((k) => (
              <Prow key={k.budget.id} name={k.budget.name} fig={cash(k.spent)} />
            ))}
          </Section>
        )}
        {months && history.data && (
          <Section name="By month">
            {history.data.columns.map((c, i) => {
              const m = c.start.slice(0, 7);
              const ahead = m > now;
              return (
                <Prow
                  key={c.start}
                  name={`${monShort(m)}${m === now ? ' to date' : ahead ? ' plan' : ''}`}
                  fig={cash(ahead ? (months.budget[i] ?? null) : (months.actual?.[i] ?? null))}
                  plan={ahead}
                />
              );
            })}
          </Section>
        )}
      </div>
    </SidePanel>
  );
}

/**
 * A budget for a P&L line from its panel: the line's own when it has none ("Line budget"), else a vendor's under it
 * ("Vendor budget"), by the month from this one on (finance-pnl.ts:780).
 */
export function LineBudgetDialog({ line, parent, onOpenChange }: { line: { account_id: string; name: string }; parent?: Budget; onOpenChange: (open: boolean) => void }) {
  const { busy, error, setError, go } = useAsk(onOpenChange);
  const [vendor, setVendor] = useState('');
  const [amount, setAmount] = useState('');
  const submit = () => {
    const cents = centsOf(amount);
    if (parent && !vendor.trim()) return setError('Name the vendor');
    if (cents == null) return setError('Enter an amount');
    const v = vendor.trim();
    void go(
      'budget.addRow',
      parent ? { name: v, line: line.account_id, amount: cents / 100, under: parent.id, vendor: v } : { name: line.name, line: line.account_id, amount: cents / 100 },
    );
  };
  return (
    <Ask title={parent ? 'Vendor budget' : 'Line budget'} description={line.name} submit="Add budget" onOpenChange={onOpenChange} onSubmit={submit} busy={busy} error={error}>
      {parent && <Field label="Vendor" value={vendor} onValueChange={setVendor} autoFocus />}
      <Field label="Amount a month" value={amount} onValueChange={setAmount} placeholder="$0" autoFocus={!parent} />
    </Ask>
  );
}
