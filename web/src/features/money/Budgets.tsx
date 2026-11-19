import { useQuery } from '@tanstack/react-query';
import { useState, type FocusEvent, type FormEvent } from 'react';
import { Button } from '@/kit/Button';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Pill } from '@/kit/Pill';
import { Select } from '@/kit/Select';
import { EmptyRow, GroupRow, Table } from '@/kit/Table';
import { List, Section, SidePanel } from '@/pagekinds';
import { FIN, useFinanceHead } from './head';
import { addMonths, budgetDay, budgetGroups, cash, centsOf, monShort, monthName, monthSpan, paceOf, type BudgetLine } from './model';
import { ActLink, Meter, Pfig, Prow, readState, usePhone, usePress, useViewAs } from './parts';
import { budgetHistoryQuery, budgetsQuery, pnlQuery, type PnlRow } from './queries';
import { panelOf, type FinanceSearch } from './search';
import { useMay } from './dialogs';

/** Budgets: each line's month so far against its budget, by section, with where today is in the month. */
export function BudgetsPage({ search }: { search: FinanceSearch }) {
  const press = usePress();
  const phone = usePhone();
  // Budgets are made and changed with finance.budgets (finance-budgets.ts:74); reading as a teammate is read only.
  const edits = useMay()('finance.budgets');
  const at = budgetDay();
  const ym = at.slice(0, 7);
  const head = useFinanceHead();
  const list = useQuery(budgetsQuery(at, search.as));
  const pnl = useQuery(pnlQuery({ ...monthSpan(ym), period: 'month', basis: 'accrual' }, search.as));
  const pace = paceOf();
  const lines = pnl.data?.rows ?? [];
  const { groups, spent, amount } = budgetGroups(list.data?.budgets ?? [], lines, pace.pace);
  const all = groups.flatMap((g) => g.rows);
  const open = panelOf(search.panel);
  const id = open?.[0] === 'budget' ? open[1] : null;
  const row = id && id !== 'new' ? all.find((r) => r.budget.id === id) : undefined;
  const [folded, setFolded] = useState<ReadonlySet<string>>(new Set());
  const toggle = (name: string) => setFolded((was) => (was.has(name) ? new Set([...was].filter((x) => x !== name)) : new Set([...was, name])));

  return (
    <List
      {...head}
      state={readState(list)}
      onRetry={() => void list.refetch()}
      actions={edits && <IconButton icon="plus" label="New budget" onClick={() => void press('budget.newRow', {})} />}
      sections={
        phone ? (
          <div className="frs">
            {all.length === 0 && <div className="fold">No budgets yet</div>}
            {groups.map((g) => [
              <div key={`g:${g.name}`} className="fr-group">
                <span>{g.name}</span>
                <span className="count">{g.rows.filter((r) => r.depth === 0).length}</span>
              </div>,
              ...g.rows.map((r) => (
                <ActLink
                  key={r.budget.id}
                  to={FIN}
                  search={{ tab: 'budgets', panel: `budget:${r.budget.id}` }}
                  action="budget.openRow"
                  input={{ budget: r.budget.id }}
                  className={['row fr', r.depth > 0 && 'is-sub', r.budget.id === id && 'is-selected'].filter(Boolean).join(' ')}
                  data-target={`budget:${r.budget.id}`}
                >
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
                      <Meter pct={((r.spent ?? 0) / r.amount) * 100} pace={pace.pace} />
                    </span>
                  ) : null}
                </ActLink>
              )),
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
          <Table label="Budgets" stick>
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
              {all.length === 0 && <EmptyRow span={5}>No budgets yet</EmptyRow>}
              {groups.map((g) => [
                <GroupRow key={`g:${g.name}`} name={g.name} count={g.rows.filter((r) => r.depth === 0).length} open={!folded.has(g.name)} onToggle={() => toggle(g.name)} span={5} />,
                ...(folded.has(g.name) ? [] : g.rows).map((r) => (
                  <tr
                    key={r.budget.id}
                    className={[r.depth > 0 && 'is-sub', r.budget.id === id && 'is-selected'].filter(Boolean).join(' ') || undefined}
                    data-target={`budget:${r.budget.id}`}
                  >
                    <td className="t-name">
                      <ActLink to={FIN} search={{ tab: 'budgets', panel: `budget:${r.budget.id}` }} action="budget.openRow" input={{ budget: r.budget.id }} className="t-link">
                        {r.budget.name}
                      </ActLink>
                      {r.flag && <Pill flag>{r.flag}</Pill>}
                    </td>
                    <td className="num t-fig">{cash(r.spent)}</td>
                    <td className="num">{cash(r.amount)}</td>
                    <td className="num">
                      <Left v={leftOf(r)} />
                    </td>
                    <td className="t-bar">{r.amount ? <Meter pct={((r.spent ?? 0) / r.amount) * 100} pace={pace.pace} /> : null}</td>
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
      panel={id === 'new' && edits ? <NewBudget lines={lines} /> : row ? <BudgetPanel line={row} kids={all.filter((r) => r.budget.parent_id === row.budget.id)} /> : undefined}
    />
  );
}

const leftOf = (r: { spent: number | null; amount: number | null }) => (r.amount == null ? null : r.amount - (r.spent ?? 0));

/** What is left; over budget it is a negative figure in strong ink. */
function Left({ v }: { v: number | null }) {
  if (v == null) return null;
  return v < 0 ? <span className="word-late">{cash(v)}</span> : <>{cash(v)}</>;
}

/** One budget, opened: the month so far, its amount to change in place, its teams and its months. */
function BudgetPanel({ line, kids }: { line: BudgetLine; kids: BudgetLine[] }) {
  const press = usePress();
  const { budget: b, spent, amount } = line;
  const at = budgetDay();
  const ym = at.slice(0, 7);
  const pace = paceOf();
  const first = addMonths(ym, -5);
  // Without finance.budgets, or reading as a teammate, it is read only: no amount to change, no Remove.
  const as = useViewAs();
  const edits = useMay()('finance.budgets');
  const history = useQuery(budgetHistoryQuery(`${first}-01`, monthSpan(ym).end, as));
  const months = history.data?.budgets.find((h) => h.id === b.id);
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
          <Button variant="quiet" onClick={() => void press('budget.removeRow', { budget: b.id })}>
            Remove
          </Button>
        )
      }
    >
      <div className="money">
        <Pfig big={cash(spent)} sub={amount != null ? `of ${cash(amount)} this month` : 'this month'}>
          {amount ? <Meter pct={((spent ?? 0) / amount) * 100} pace={pace.pace} /> : null}
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
                disabled={!edits}
              />
            </form>
          </LabelValue>
          <LabelValue label="Left" num>
            <Left v={leftOf(line)} />
          </LabelValue>
          <LabelValue label="Today">{pace.words}</LabelValue>
          {b.state.pace_end != null && <LabelValue label="Month end at this pace">{`About ${cash(b.state.pace_end)}`}</LabelValue>}
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

/** A monthly budget for one P&L line, from this month on. */
function NewBudget({ lines }: { lines: readonly PnlRow[] }) {
  const press = usePress();
  const options = lines.filter((r) => r.type === 'expense' && !r.private).map((r) => ({ value: r.account_id, label: r.name }));
  const [line, setLine] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [amount, setAmount] = useState('');
  const cents = centsOf(amount);
  const ready = !!line && !!name.trim() && cents != null;
  const add = (e: FormEvent) => {
    e.preventDefault();
    if (ready) void press('budget.addRow', { name: name.trim(), line, amount: cents / 100 });
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
            if (!name.trim()) setName(options.find((o) => o.value === v)?.label ?? '');
          }}
        />
        <Field label="Name" value={name} onValueChange={setName} />
        <Field label="Amount a month" value={amount} onValueChange={setAmount} placeholder="$0" />
      </form>
    </SidePanel>
  );
}
