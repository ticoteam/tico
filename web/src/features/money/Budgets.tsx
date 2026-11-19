import { useQuery } from '@tanstack/react-query';
import { useState, type FocusEvent, type FormEvent } from 'react';
import { directoryQuery, meQuery } from '@/data';
import { Button } from '@/kit/Button';
import { Field, Search } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Pill } from '@/kit/Pill';
import { Select } from '@/kit/Select';
import { EmptyRow, GroupRow, Table } from '@/kit/Table';
import { List, Section, SidePanel } from '@/pagekinds';
import type { AccessList } from './caps';
import { Ask, useAsk, useMay } from './dialogs';
import { FIN, useFinanceHead } from './head';
import { addMonths, budgetDay, budgetGroups, cash, centsOf, monShort, monthName, monthSpan, paceOf, type BudgetGroup, type BudgetLine } from './model';
import { ActLink, Meter, Pfig, Prow, readState, usePhone, usePress, useViewAs } from './parts';
import { accessQuery, budgetHistoryQuery, budgetsQuery, pnlQuery, type Budget, type PnlRow } from './queries';
import { panelOf, type FinanceSearch } from './search';

/** The day a month's budgets are read at: today in this month, else its first day (finance-budgets.ts:76). */
const atOf = (ym: string) => (ym === budgetDay().slice(0, 7) ? budgetDay() : `${ym}-01`);
/** The month an action names: left out for this month. */
const monthArg = (ym: string) => (ym === budgetDay().slice(0, 7) ? {} : { month: ym });

/** A line's own budget: on the line, for no team, teammate or vendor, under no other (finance-pnl.ts finLineBudget). */
export const lineBudget = (budgets: readonly Budget[], line: string) => {
  const live = budgets.filter((b) => b.active !== false && !b.catch_all);
  const ids = new Set(live.map((b) => b.id));
  return live.find((b) => b.account_id === line && !b.team && !b.teammate && !b.vendor && (!b.parent_id || !ids.has(b.parent_id)));
};

/**
 * Budgets: each line's month against its budget, by section, with where today is in the month. Any month can be read
 * (Earlier, Later) and searched. `mine`: My budget, a teammate's own budgets, flat and read only (finance-budgets.ts:122).
 */
export function BudgetsPage({ search, mine = false }: { search: FinanceSearch; mine?: boolean }) {
  const press = usePress();
  const phone = usePhone();
  // Budgets are made and changed with finance.budgets (finance-budgets.ts:74); reading as a teammate is read only.
  const edits = useMay()('finance.budgets') && !mine;
  const now = budgetDay().slice(0, 7);
  const ym = search.month ?? now;
  const at = atOf(ym);
  const head = useFinanceHead();
  const me = useQuery(meQuery).data?.id;
  const dir = useQuery(directoryQuery).data;
  const acc = useQuery(accessQuery).data as AccessList | null | undefined;
  const list = useQuery(budgetsQuery(at, search.as));
  const pnl = useQuery(pnlQuery({ ...monthSpan(ym), period: 'month', basis: 'accrual' }, search.as));
  const pace = ym === now ? paceOf() : { pace: ym < now ? 100 : 0, words: 'Used' };
  const lines = pnl.data?.rows ?? [];
  const budgets = list.data?.budgets ?? [];
  const who = search.as ?? me;
  const [find, setFind] = useState('');
  const q = find.trim().toLowerCase();

  const made = budgetGroups(mine ? budgets.filter((b) => b.teammate === who) : budgets, lines, pace.pace);
  const { spent, amount } = made;
  let { groups } = made;
  if (mine) groups = [{ name: '', rows: groups.flatMap((g) => g.rows).sort((a, b) => a.budget.name.localeCompare(b.budget.name)) }];
  else if (q) groups = matching(groups, q, (b) => [b.name, b.vendor, lines.find((r) => r.account_id === b.account_id)?.name, b.teammate && dir?.byId[b.teammate]?.name, b.team && acc?.teams?.find((t) => t.id === b.team)?.name]);
  const all = groups.flatMap((g) => g.rows);
  const open = panelOf(search.panel);
  const id = !mine && open?.[0] === 'budget' ? open[1] : null;
  const row = id && id !== 'new' ? all.find((r) => r.budget.id === id) : undefined;
  const [folded, setFolded] = useState<ReadonlySet<string>>(new Set());
  const toggle = (name: string) => setFolded((was) => (was.has(name) ? new Set([...was].filter((x) => x !== name)) : new Set([...was, name])));
  const none = mine ? 'No budget yet' : q ? 'No budget matches' : 'No budgets yet';
  const name = (r: BudgetLine) =>
    mine ? (
      r.budget.name
    ) : (
      <ActLink to={FIN} search={{ tab: 'budgets', panel: `budget:${r.budget.id}` }} action="budget.openRow" input={{ budget: r.budget.id }} className="t-link">
        {r.budget.name}
      </ActLink>
    );

  return (
    <List
      {...head}
      state={readState(list)}
      onRetry={() => void list.refetch()}
      after={
        <span className="range" data-target="range:budgets">
          <IconButton icon="chevron" className="flip" label="Earlier" size="sm" quiet onClick={() => void press('budget.setMonth', { month: addMonths(ym, -1) })} />
          <span>{monthName(ym)}</span>
          <IconButton icon="chevron" label="Later" size="sm" quiet onClick={() => void press('budget.setMonth', { month: addMonths(ym, 1) })} />
        </span>
      }
      search={!mine && <Search label="Search budgets" placeholder="Search" value={find} onValueChange={setFind} />}
      actions={edits && <IconButton icon="plus" label="New budget" onClick={() => void press('budget.newRow', {})} />}
      sections={
        phone ? (
          <div className="frs">
            {all.length === 0 && <div className="fold">{none}</div>}
            {groups.map((g) => [
              g.name && (
                <div key={`g:${g.name}`} className="fr-group">
                  <span>{g.name}</span>
                  <span className="count">{g.rows.filter((r) => r.depth === 0).length}</span>
                </div>
              ),
              ...g.rows.map((r) => {
                const cls = ['row fr', r.depth > 0 && 'is-sub', r.budget.id === id && 'is-selected'].filter(Boolean).join(' ');
                const body = (
                  <>
                    <span className="fr-main">
                      <span className="row-title">
                        {r.budget.name}
                        {r.flag && <Pill flag>{r.flag}</Pill>}
                      </span>
                      <span className="fr-sub">
                        {r.amount != null && <span>{`of ${cash(r.amount)}`}</span>}
                        {r.amount != null && (
                          <span>
                            Left <Left v={leftOf(r)} />
                          </span>
                        )}
                      </span>
                    </span>
                    <span className="row-fig">{cash(r.spent)}</span>
                    {r.amount ? (
                      <span className="fr-bar">
                        <Meter pct={((r.spent ?? 0) / r.amount) * 100} pace={ym === now ? pace.pace : undefined} />
                      </span>
                    ) : null}
                  </>
                );
                return mine ? (
                  <div key={r.budget.id} className={cls} data-target={`budget:${r.budget.id}`}>
                    {body}
                  </div>
                ) : (
                  <ActLink
                    key={r.budget.id}
                    to={FIN}
                    search={{ tab: 'budgets', panel: `budget:${r.budget.id}` }}
                    action="budget.openRow"
                    input={{ budget: r.budget.id }}
                    className={cls}
                    data-target={`budget:${r.budget.id}`}
                  >
                    {body}
                  </ActLink>
                );
              }),
            ])}
            {all.length > 0 && (
              <div className="row fr is-total">
                <span className="fr-main">
                  <span className="row-title">Total</span>
                  <span className="fr-sub">
                    <span>{`of ${cash(amount)}`}</span>
                    <span>
                      Left <Left v={leftOf({ spent, amount })} />
                    </span>
                  </span>
                </span>
                <span className="row-fig">{cash(spent)}</span>
              </div>
            )}
          </div>
        ) : (
          <div className="t-scroll">
            <Table label={mine ? 'My budget' : 'Budgets'} stick>
              <colgroup>
                <col />
                <col className="t-col" />
                <col className="t-col" />
                <col className="t-col" />
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
                  <th scope="col" className="num">
                    Left
                  </th>
                  <th scope="col">{pace.words}</th>
                </tr>
              </thead>
              <tbody>
                {all.length === 0 && <EmptyRow span={5}>{none}</EmptyRow>}
                {groups.map((g) => [
                  g.name && (
                    <GroupRow
                      key={`g:${g.name}`}
                      name={g.name}
                      count={g.rows.filter((r) => r.depth === 0).length}
                      open={!folded.has(g.name)}
                      onToggle={() => toggle(g.name)}
                      span={5}
                    />
                  ),
                  ...(folded.has(g.name) ? [] : g.rows).map((r) => (
                    <tr
                      key={r.budget.id}
                      className={[r.depth > 0 && 'is-sub', r.budget.id === id && 'is-selected'].filter(Boolean).join(' ') || undefined}
                      data-target={`budget:${r.budget.id}`}
                    >
                      <td className="t-name">
                        {name(r)}
                        {r.flag && <Pill flag>{r.flag}</Pill>}
                      </td>
                      <td className="num t-fig">{cash(r.spent)}</td>
                      <td className="num">{cash(r.amount)}</td>
                      <td className="num">
                        <Left v={leftOf(r)} />
                      </td>
                      <td className="t-bar">{r.amount ? <Meter pct={((r.spent ?? 0) / r.amount) * 100} pace={ym === now ? pace.pace : undefined} /> : null}</td>
                    </tr>
                  )),
                ])}
                {all.length > 0 && (
                  <tr className="is-total">
                    <td className="t-name">Total</td>
                    <td className="num">{cash(spent)}</td>
                    <td className="num">{cash(amount)}</td>
                    <td className="num">
                      <Left v={leftOf({ spent, amount })} />
                    </td>
                    <td />
                  </tr>
                )}
              </tbody>
            </Table>
          </div>
        )
      }
      panel={
        id === 'new' && edits ? (
          <NewBudget lines={lines} budgets={budgets} acc={acc} />
        ) : row ? (
          <BudgetPanel line={row} ym={ym} kids={all.filter((r) => r.budget.parent_id === row.budget.id)} lines={lines} budgets={budgets} />
        ) : undefined
      }
    />
  );
}

/** The rows that match, each with the budget it sits under (finance-budgets.ts finBudgetMatch). */
function matching(groups: BudgetGroup[], q: string, words: (b: Budget) => (string | null | undefined | false)[]): BudgetGroup[] {
  const hit = (b: Budget) => words(b).some((w) => typeof w === 'string' && w.toLowerCase().includes(q));
  const rows = groups.flatMap((g) => g.rows);
  const keep = new Set<string>();
  for (const r of rows) {
    if (!hit(r.budget)) continue;
    keep.add(r.budget.id);
    if (r.budget.parent_id) keep.add(r.budget.parent_id);
  }
  return groups.map((g) => ({ ...g, rows: g.rows.filter((r) => keep.has(r.budget.id)) })).filter((g) => g.rows.length > 0);
}

const leftOf = (r: { spent: number | null; amount: number | null }) => (r.amount == null ? null : r.amount - (r.spent ?? 0));

/** What is left; over budget it is a negative figure in strong ink. */
function Left({ v }: { v: number | null }) {
  if (v == null) return null;
  return v < 0 ? <span className="word-late">{cash(v)}</span> : <>{cash(v)}</>;
}

/** One budget, opened: the month so far, its amount to change in place, its teams and its months. */
function BudgetPanel({ line, ym, kids, lines, budgets }: { line: BudgetLine; ym: string; kids: BudgetLine[]; lines: readonly PnlRow[]; budgets: readonly Budget[] }) {
  const press = usePress();
  const { budget: b, spent, amount } = line;
  const now = budgetDay().slice(0, 7);
  const pace = paceOf();
  const first = addMonths(ym, -5);
  const [adding, setAdding] = useState(false);
  // Without finance.budgets, or reading as a teammate, it is read only: no amount to change, no Remove. A month gone
  // by is kept as it was, and only a monthly budget's month is its amount (finance-budgets.ts finBudgetEditable).
  const as = useViewAs();
  const edits = useMay()('finance.budgets');
  const editable = edits && ym >= now && b.period === 'month' && !b.catch_all;
  const history = useQuery(budgetHistoryQuery(`${first}-01`, monthSpan(ym).end, as));
  const months = history.data?.budgets.find((h) => h.id === b.id);
  const own = b.account_id ? lineBudget(budgets, b.account_id) : undefined;
  const pnlLine = lines.find((r) => r.account_id === b.account_id);
  const save = (text: string) => {
    const cents = centsOf(text);
    if (cents == null || cents === amount) return;
    void press('budget.updateRow', { budget: b.id, amount: cents / 100, ...monthArg(ym) });
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
            <Button variant="quiet" onClick={() => void press('budget.removeRow', { budget: b.id, ...monthArg(ym) })}>
              Remove
            </Button>
            {adding && pnlLine && <LineBudgetDialog line={pnlLine} parent={b} onOpenChange={setAdding} />}
          </>
        )
      }
    >
      <div className="money">
        <Pfig big={cash(spent)} sub={amount != null ? `of ${cash(amount)} in ${monShort(ym)}` : monthName(ym)}>
          {amount ? <Meter pct={((spent ?? 0) / amount) * 100} pace={ym === now ? pace.pace : undefined} /> : null}
        </Pfig>
        <Facts label="Budget">
          <LabelValue label="Budget">
            {/* Saved on Enter or when the field is left; `key` resets it when a refusal takes the amount back. */}
            <form key={amount ?? 'none'} onSubmit={onSubmit} onBlur={(e: FocusEvent<HTMLFormElement>) => save(typed(e.currentTarget))}>
              <Field
                label={`Budget for ${monthName(ym)}`}
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
          {ym === now && <LabelValue label="Today">{pace.words}</LabelValue>}
          {ym === now && b.state.pace_end != null && <LabelValue label="Month end at this pace">{`About ${cash(b.state.pace_end)}`}</LabelValue>}
        </Facts>
        {kids.length > 0 && (
          <Section name="By team" count={kids.length}>
            {kids.map((k) => (
              <Prow key={k.budget.id} name={k.budget.name} sub={k.amount != null ? `of ${cash(k.amount)}` : ''} fig={cash(k.spent)} />
            ))}
          </Section>
        )}
        {months && history.data && (
          <Section name="By month">
            {history.data.columns.map((c, i) => (
              <Prow key={c.start} name={monShort(c.start.slice(0, 7))} sub={months.budget[i] != null ? `of ${cash(months.budget[i])}` : ''} fig={cash(months.actual?.[i] ?? null)} />
            ))}
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

const PERIODS = [
  { value: 'month', label: 'Month' },
  { value: 'quarter', label: 'Quarter' },
  { value: 'year', label: 'Year' },
] as const;
type Per = (typeof PERIODS)[number]['value'];
const NONE = 'none';

/**
 * A budget for one P&L line, from a month on: under another budget, for a teammate or a vendor, by month, quarter or
 * year, warning at a share used (finance-budgets.ts finBudgetAdd). Its name follows the teammate, the vendor or the
 * line until one is typed.
 */
function NewBudget({ lines, budgets, acc }: { lines: readonly PnlRow[]; budgets: readonly Budget[]; acc: AccessList | null | undefined }) {
  const press = usePress();
  const dir = useQuery(directoryQuery).data;
  const now = budgetDay().slice(0, 7);
  const options = lines.filter((r) => r.type === 'expense' && !r.private).map((r) => ({ value: r.account_id, label: r.name }));
  const [line, setLine] = useState<string | null>(null);
  const [under, setUnder] = useState<string | null>(null);
  const [mate, setMate] = useState(NONE);
  const [vendor, setVendor] = useState('');
  const [typedName, setName] = useState<string | null>(null);
  const [amount, setAmount] = useState('');
  const [warn, setWarn] = useState('80');
  const [period, setPeriod] = useState<Per>('month');
  const [from, setFrom] = useState(now);

  // The budgets it may sit under: those on its line or a line above it.
  const above = new Set<string>();
  for (let at = lines.find((r) => r.account_id === line); at && !above.has(at.account_id); at = lines.find((r) => r.account_id === at!.parent_id)) above.add(at.account_id);
  const parents = budgets.filter((b) => b.active !== false && !b.catch_all && !!b.account_id && above.has(b.account_id));
  const parentId = under ?? (line ? (lineBudget(budgets, line)?.id ?? NONE) : NONE);
  const parent = parents.find((b) => b.id === parentId);
  const per: Per = parent ? (parent.period as Per) : period;
  const mates = (acc?.teammates ?? []).map((t) => ({ value: t.teammate, label: dir?.byId[t.teammate]?.name ?? t.teammate.slice(t.teammate.indexOf(':') + 1) }));
  const auto = (mate !== NONE ? mates.find((m) => m.value === mate)?.label : '') || vendor.trim() || options.find((o) => o.value === line)?.label || '';
  const name = typedName ?? auto;
  const cents = centsOf(amount);
  const pct = /^\d{1,3}$/.test(warn.trim()) ? Number(warn.trim()) : NaN;
  const ready = !!line && !!name.trim() && cents != null && pct >= 1 && pct <= 100;
  const months = Array.from({ length: 12 }, (_, k) => addMonths(now, k)).map((m) => ({ value: m, label: monthName(m) }));
  const add = (e: FormEvent) => {
    e.preventDefault();
    if (!ready) return;
    void press('budget.addRow', {
      name: name.trim(),
      line,
      amount: cents / 100,
      ...(parent ? { under: parent.id } : {}),
      ...(mate !== NONE ? { teammate: mate } : {}),
      ...(vendor.trim() ? { vendor: vendor.trim() } : {}),
      ...(pct !== 80 ? { warn: pct } : {}),
      period: per,
      from,
    });
  };
  return (
    <SidePanel
      title="New budget"
      target="budget:new"
      onClose={() => void press('finance.closePanel', {})}
      foot={
        <Button variant="primary" type="submit" form="new-budget" disabled={!ready}>
          Add budget
        </Button>
      }
    >
      <form id="new-budget" className="money new-budget" onSubmit={add}>
        <Select
          label="Line"
          options={options}
          value={line}
          placeholder="Choose"
          onValueChange={(v) => {
            setLine(v);
            setUnder(null);
          }}
        />
        <Select label="Under" options={[{ value: NONE, label: 'None' }, ...parents.map((b) => ({ value: b.id, label: b.name }))]} value={parentId} onValueChange={setUnder} disabled={!line} />
        <Select label="Teammate" options={[{ value: NONE, label: 'None' }, ...mates]} value={mate} onValueChange={setMate} />
        <Field label="Vendor" value={vendor} onValueChange={setVendor} />
        <Field label="Name" value={name} onValueChange={setName} />
        <Field label={`Amount a ${per}`} value={amount} onValueChange={setAmount} placeholder="$0" />
        <Field label="Warn at" value={warn} onValueChange={setWarn} placeholder="80" />
        {/* A budget under another keeps its period. */}
        <Select label="Period" options={PERIODS} value={per} onValueChange={setPeriod} disabled={!!parent} />
        <Select label="From" options={months} value={from} onValueChange={setFrom} />
      </form>
    </SidePanel>
  );
}
