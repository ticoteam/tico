import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useActors } from '@/data';
import { Button } from '@/kit/Button';
import { Checkbox } from '@/kit/Checkbox';
import { DatePick } from '@/kit/DatePick';
import { Field } from '@/kit/Field';
import { Select } from '@/kit/Select';
import { billPayload, entryPayload, invoicePayload, paidPayload, type BillIn, type EntryLineIn, type InvoiceIn, type Tags } from './ledger';
import { Decide, useMay, useRun, why, type Decision } from './dialogs';
import { cash, centsOf, dayOf } from './model';
import {
  customersQuery,
  docChartQuery,
  journalChartQuery,
  payFromQuery,
  vendorsQuery,
  type Card,
  type ChartLine,
  type DocKind,
  type DocRow,
} from './queries';

/* The books tabs' dialogs, each a decision tried first and then sent (dialogs.tsx Decide, s449): a new bill, a new
   invoice, a journal entry, a bill's approval, schedule, payment and void, an invoice's sending, payment and write-off,
   a card's limit. Each sends what the old app sent (finance-docs.ts finDocAdd, finDocDo, finJournalAdd). */

type Open = (open: boolean) => void;
const NEW = '+new';
const dollars = (c: number | null | undefined) => (c ? String(c / 100) : '');
const today = () => new Date(new Date().setHours(0, 0, 0, 0));
const dateOf = (day: string | null | undefined) => (day ? new Date(`${day}T00:00:00`) : null);

/** Whom a line not company-wide may be put on: the teams and teammates every chosen line allows (finance-docs.ts finForSync). */
function useForOptions(chosen: readonly (ChartLine | undefined)[]) {
  const actors = useActors();
  const scoped = chosen.filter((l): l is ChartLine => l?.company_wide === false);
  if (!scoped.length) return null;
  const key = (t: { team?: string; teammate?: string }) => (t.teammate ? `teammate:${t.teammate}` : `team:${t.team ?? ''}`);
  const sets = scoped.map((l) => new Set((l.attributable ?? []).map(key)));
  const first = scoped[0]!.attributable ?? [];
  return first
    .filter((t) => sets.every((s) => s.has(key(t))))
    .map((t) => ({
      value: key(t),
      label: t.teammate ? (actors.find((a) => a.id === t.teammate)?.name ?? t.teammate.slice(t.teammate.indexOf(':') + 1)) : (t.team ?? ''),
    }));
}
const tagsOf = (v: string | null): Tags | undefined => {
  const i = v ? v.indexOf(':') : -1;
  return v && i > 0 ? { [v.slice(0, i)]: v.slice(i + 1) } : undefined;
};
const lineOptions = (lines: readonly ChartLine[]) => lines.map((l) => ({ value: l.id, label: l.number ? `${l.number} ${l.name}` : l.name }));

/** A vendor or customer, or a new one named and added in place before the document is saved. */
function Contact({ kind, value, onChange }: { kind: 'vendor' | 'customer'; value: string | null; onChange: (id: string | null) => void }) {
  const run = useRun();
  const q = useQuery(kind === 'vendor' ? vendorsQuery : customersQuery);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const word = kind === 'vendor' ? 'Vendor' : 'Customer';
  const options = [
    ...(q.data?.contacts ?? []).filter((c) => c.active !== false).map((c) => ({ value: c.id, label: c.name })),
    { value: NEW, label: `New ${kind}` },
  ];
  const add = async () => {
    if (!name.trim()) return setError(`Name the ${kind}`);
    setBusy(true);
    const r = await run('finance.addContact', { name: name.trim(), kind });
    setBusy(false);
    if (!r.ok || typeof r.out !== 'string') return setError(r.ok ? 'Not added' : why(r));
    setError('');
    onChange(r.out);
  };
  return (
    <>
      <Select label={word} options={options} value={value} onValueChange={onChange} placeholder={`Pick a ${kind}`} />
      {value === NEW && (
        <div className="dlg-pair">
          <Field label="Name" value={name} onValueChange={(v) => setName(v.slice(0, 120))} autoComplete="off" error={error || undefined} />
          <Button busy={busy} onClick={() => void add()}>
            Add
          </Button>
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------- a new bill, a new invoice (s406, s407)

export function AddBillDialog({ onOpenChange }: { onOpenChange: Open }) {
  const chart = useQuery(docChartQuery('bill'));
  const lines = chart.data?.accounts ?? [];
  const [vendor, setVendor] = useState<string | null>(null);
  const [number, setNumber] = useState('');
  const [date, setDate] = useState<Date>(today);
  const [due, setDue] = useState<Date | null>(null);
  const [line, setLine] = useState<string | null>(null);
  const [amount, setAmount] = useState('');
  const [memo, setMemo] = useState('');
  const [who, setWho] = useState<string | null>(null);
  const l = line ?? lines[0]?.id ?? null;
  const forOptions = useForOptions([lines.find((x) => x.id === l)]);
  const w = forOptions ? (forOptions.some((o) => o.value === who) ? who : (forOptions[0]?.value ?? null)) : null;
  const decision = (): Decision | string => {
    if (!vendor || vendor === NEW) return vendor === NEW ? 'Add the vendor first' : 'Pick a vendor';
    if (!l) return 'No line in your view';
    const c = centsOf(amount);
    if (!c) return 'A dollar amount, like 500';
    if (forOptions && !w) return 'Pick who it is for';
    const input: BillIn = {
      vendor,
      date: dayOf(date),
      line: l,
      amount: c / 100,
      ...(due ? { due: dayOf(due) } : {}),
      ...(number.trim() ? { number: number.trim() } : {}),
      ...(memo.trim() ? { memo: memo.trim() } : {}),
      ...(forOptions && w ? { tags: tagsOf(w) } : {}),
    };
    return { kind: 'bill_create', payload: billPayload(input), action: 'finance.addBill', input };
  };
  return (
    <Decide title="New bill" submit="Save" decision={decision} onOpenChange={onOpenChange}>
      <Contact kind="vendor" value={vendor} onChange={setVendor} />
      <Field label="Number" value={number} onValueChange={(v) => setNumber(v.slice(0, 60))} autoComplete="off" />
      <div className="dlg-pair">
        <DatePick label="Date" value={date} onValueChange={setDate} withTime={false} />
        <DatePick label="Due" value={due} onValueChange={setDue} withTime={false} />
      </div>
      <Select label="Line" options={lineOptions(lines)} value={l} onValueChange={setLine} />
      {forOptions && <Select label="For" options={forOptions} value={w} onValueChange={setWho} />}
      <Field label="Amount" value={amount} onValueChange={setAmount} placeholder="$0" autoComplete="off" />
      <Field label="Memo" value={memo} onValueChange={(v) => setMemo(v.slice(0, 200))} autoComplete="off" />
    </Decide>
  );
}

type InvLine = { line: string | null; amount: string; taxable: boolean };

export function NewInvoiceDialog({ onOpenChange }: { onOpenChange: Open }) {
  const chart = useQuery(docChartQuery('invoice'));
  const lines = chart.data?.accounts ?? [];
  const [customer, setCustomer] = useState<string | null>(null);
  const [number, setNumber] = useState('');
  const [date, setDate] = useState<Date>(today);
  const [due, setDue] = useState<Date | null>(null);
  const [rows, setRows] = useState<InvLine[]>([{ line: null, amount: '', taxable: false }]);
  const [memo, setMemo] = useState('');
  const [who, setWho] = useState<string | null>(null);
  const lineAt = (r: InvLine) => r.line ?? lines[0]?.id ?? null;
  const filled = rows.filter((r) => r.amount.trim());
  const forOptions = useForOptions(filled.map((r) => lines.find((x) => x.id === lineAt(r))));
  const w = forOptions ? (forOptions.some((o) => o.value === who) ? who : (forOptions[0]?.value ?? null)) : null;
  const set = (i: number, patch: Partial<InvLine>) => setRows((all) => all.map((r, k) => (k === i ? { ...r, ...patch } : r)));
  // Taxable is asked of an income line only.
  const income = (r: InvLine) => lines.find((x) => x.id === lineAt(r))?.type === 'income';
  const decision = (): Decision | string => {
    if (!customer || customer === NEW) return customer === NEW ? 'Add the customer first' : 'Pick a customer';
    if (!filled.length) return 'A dollar amount, like 500';
    const out: InvoiceIn['lines'] = [];
    for (const r of filled) {
      const c = centsOf(r.amount);
      const l = lineAt(r);
      if (!c) return 'A dollar amount on each line, like 500';
      if (!l) return 'No line in your view';
      const scoped = lines.find((x) => x.id === l)?.company_wide === false;
      out.push({ line: l, amount: c / 100, ...(income(r) ? { taxable: r.taxable } : {}), ...(scoped && w ? { tags: tagsOf(w) } : {}) });
    }
    if (forOptions && !w) return 'Pick who it is for';
    const input: InvoiceIn = {
      customer,
      date: dayOf(date),
      lines: out,
      ...(due ? { due: dayOf(due) } : {}),
      ...(number.trim() ? { number: number.trim() } : {}),
      ...(memo.trim() ? { memo: memo.trim() } : {}),
    };
    return { kind: 'invoice_create', payload: invoicePayload(input), action: 'finance.newInvoice', input };
  };
  return (
    <Decide title="New invoice" submit="Save" decision={decision} onOpenChange={onOpenChange}>
      <Contact kind="customer" value={customer} onChange={setCustomer} />
      <Field label="Number" value={number} onValueChange={(v) => setNumber(v.slice(0, 60))} autoComplete="off" />
      <div className="dlg-pair">
        <DatePick label="Date" value={date} onValueChange={setDate} withTime={false} />
        <DatePick label="Due" value={due} onValueChange={setDue} withTime={false} />
      </div>
      {rows.map((r, i) => {
        const n = rows.length > 1 ? ` ${i + 1}` : '';
        return (
          <div key={i} className="dlg-line">
            <Select label={`Line${n}`} options={lineOptions(lines)} value={lineAt(r)} onValueChange={(v) => set(i, { line: v })} />
            <Field label={`Amount${n}`} value={r.amount} onValueChange={(v) => set(i, { amount: v })} placeholder="$0" autoComplete="off" />
            {income(r) && <Checkbox label="Taxable" checked={r.taxable} onCheckedChange={(v) => set(i, { taxable: v })} />}
          </div>
        );
      })}
      <Button variant="quiet" icon="plus" onClick={() => setRows((all) => [...all, { line: null, amount: '', taxable: false }])}>
        Add line
      </Button>
      {forOptions && <Select label="For" options={forOptions} value={w} onValueChange={setWho} />}
      <Field label="Memo" value={memo} onValueChange={(v) => setMemo(v.slice(0, 200))} autoComplete="off" />
    </Decide>
  );
}

// ---------------------------------------------------------------- a journal entry (s405)

type EntryRow = { line: string | null; debit: string; credit: string };

export function EntryDialog({ onOpenChange }: { onOpenChange: Open }) {
  const chart = useQuery(journalChartQuery);
  const lines = chart.data?.accounts ?? [];
  const [date, setDate] = useState<Date>(today);
  const [memo, setMemo] = useState('');
  const [rows, setRows] = useState<EntryRow[]>([
    { line: null, debit: '', credit: '' },
    { line: null, debit: '', credit: '' },
  ]);
  const [who, setWho] = useState<string | null>(null);
  const used = rows.filter((r) => r.line || r.debit.trim() || r.credit.trim());
  const forOptions = useForOptions(used.map((r) => lines.find((x) => x.id === r.line)));
  const w = forOptions ? (forOptions.some((o) => o.value === who) ? who : (forOptions[0]?.value ?? null)) : null;
  const set = (i: number, patch: Partial<EntryRow>) => setRows((all) => all.map((r, k) => (k === i ? { ...r, ...patch } : r)));
  const decision = (): Decision | string => {
    const out: EntryLineIn[] = [];
    for (const r of used) {
      const d = r.debit.trim() ? centsOf(r.debit) : 0;
      const c = r.credit.trim() ? centsOf(r.credit) : 0;
      if (!r.line) return 'Pick a line for each amount';
      if (d == null || c == null || (d && c) || !(d || c)) return 'A debit or a credit on each line, like 500';
      const scoped = lines.find((x) => x.id === r.line)?.company_wide === false;
      out.push({ line: r.line, amount: (d - c) / 100, ...(scoped && w ? { tags: tagsOf(w) } : {}) });
    }
    if (out.length < 2) return 'Two lines or more';
    const off = out.reduce((a, l) => a + Math.round(l.amount * 100), 0);
    if (off) return `Debits and credits differ by ${cash(Math.abs(off), true)}`;
    if (forOptions && !w) return 'Pick who it is for';
    const input = { date: dayOf(date), lines: out, ...(memo.trim() ? { memo: memo.trim() } : {}) };
    return { kind: 'journal', payload: entryPayload(input), action: 'finance.addEntry', input };
  };
  const options = lineOptions(lines);
  return (
    <Decide title="Journal entry" submit="Post" decision={decision} onOpenChange={onOpenChange}>
      <DatePick label="Date" value={date} onValueChange={setDate} withTime={false} />
      <Field label="Memo" value={memo} onValueChange={(v) => setMemo(v.slice(0, 200))} autoComplete="off" />
      {rows.map((r, i) => (
        <div key={i} className="dlg-line">
          <Select label={`Line ${i + 1}`} options={options} value={r.line} onValueChange={(v) => set(i, { line: v })} placeholder="Pick a line" />
          <Field label={`Debit ${i + 1}`} value={r.debit} onValueChange={(v) => set(i, { debit: v })} placeholder="$0" autoComplete="off" />
          <Field label={`Credit ${i + 1}`} value={r.credit} onValueChange={(v) => set(i, { credit: v })} placeholder="$0" autoComplete="off" />
        </div>
      ))}
      <Button variant="quiet" icon="plus" onClick={() => setRows((all) => [...all, { line: null, debit: '', credit: '' }])}>
        Add line
      </Button>
      {forOptions && <Select label="For" options={forOptions} value={w} onValueChange={setWho} />}
    </Decide>
  );
}

// ---------------------------------------------------------------- a bill's and an invoice's decisions (s406, s407, s449)

/** A decision with nothing to fill in: approve a bill, send an invoice. */
export function DocDecide({ title, submit, kind, payload, action, input, doc, onOpenChange }: Decision & { title: string; submit: string; doc: DocRow; onOpenChange: Open }) {
  const name = (doc.kind === 'bill' ? doc.vendor?.name : doc.customer?.name) || doc.number;
  return (
    <Decide title={title} submit={submit} decision={() => ({ kind, payload, action, input })} onOpenChange={onOpenChange}>
      <p className="t-read">{`${name}, ${cash(doc.total)}`}</p>
    </Decide>
  );
}

/** Pays a bill from a bank account on a day, or moves a scheduled one to another (finance-docs.ts finBillSchedule). */
export function ScheduleDialog({ doc, from: was, onOpenChange }: { doc: DocRow; from?: string; onOpenChange: Open }) {
  const banks = useQuery(payFromQuery);
  const options = (banks.data?.accounts ?? []).map((a) => ({ value: a.id, label: a.last4 ? `${a.name} ••${a.last4}` : a.name }));
  const again = doc.status === 'scheduled';
  const want = dateOf(again && doc.pay_on ? doc.pay_on : doc.due_date);
  const [on, setOn] = useState<Date>(() => (want && want > today() ? want : today()));
  const [from, setFrom] = useState<string | null>(null);
  const f = from ?? (options.some((o) => o.value === was) ? was! : (options[0]?.value ?? null));
  const decision = (): Decision | string => {
    if (!f) return 'No bank account to pay from';
    if (on < today()) return 'Pick today or a later day';
    const input = { bill: doc.id, on: dayOf(on), from: f };
    return { kind: 'bill_schedule', payload: { bill_id: doc.id, pay_on: input.on, money_account_id: f }, action: 'finance.scheduleBill', input };
  };
  const word = again ? 'Reschedule' : 'Schedule';
  return (
    <Decide title={word} submit={word} decision={decision} onOpenChange={onOpenChange}>
      <DatePick label="Pay on" value={on} onValueChange={setOn} withTime={false} />
      <Select label="From" options={options} value={f} onValueChange={setFrom} />
    </Decide>
  );
}

const ELSEWHERE = 'elsewhere';

/** Money paid on a bill or received on an invoice: from (to) a bank account, or elsewhere (finance-docs.ts finDocMarkPaid). */
export function PaidDialog({ kind, doc, onOpenChange }: { kind: DocKind; doc: DocRow; onOpenChange: Open }) {
  const may = useMay();
  const pay = may('finance.pay');
  const banks = useQuery({ ...payFromQuery, enabled: pay });
  const options = [
    ...(pay ? (banks.data?.accounts ?? []) : []).map((a) => ({ value: a.id, label: a.last4 ? `${a.name} ••${a.last4}` : a.name })),
    { value: ELSEWHERE, label: 'Elsewhere' },
  ];
  const [amount, setAmount] = useState(() => dollars(doc.remaining));
  const [date, setDate] = useState<Date>(today);
  const [from, setFrom] = useState<string | null>(null);
  const f = from ?? options[0]!.value;
  const decision = (): Decision | string => {
    const c = centsOf(amount);
    if (!c) return 'A dollar amount, like 500';
    if (c > doc.remaining) return `At most ${cash(doc.remaining, true)}`;
    const input = { kind, doc: doc.id, amount: c / 100, date: dayOf(date), ...(f !== ELSEWHERE ? { account: f } : {}) };
    return { kind: kind === 'bill' ? 'bill_mark_paid' : 'invoice_record_payment', payload: paidPayload(input), action: 'finance.markPaid', input };
  };
  return (
    <Decide title="Mark paid" submit="Mark paid" decision={decision} onOpenChange={onOpenChange}>
      <Field label="Amount" value={amount} onValueChange={setAmount} placeholder="$0" autoComplete="off" />
      <DatePick label="Date" value={date} onValueChange={setDate} withTime={false} />
      <Select label={kind === 'bill' ? 'From' : 'To'} options={options} value={f} onValueChange={setFrom} />
    </Decide>
  );
}

export function VoidDialog({ doc, onOpenChange }: { doc: DocRow; onOpenChange: Open }) {
  const [reason, setReason] = useState('');
  const decision = (): Decision => {
    const r = reason.trim();
    return { kind: 'bill_void', payload: { bill_id: doc.id, ...(r ? { reason: r } : {}) }, action: 'finance.voidBill', input: { bill: doc.id, ...(r ? { reason: r } : {}) } };
  };
  return (
    <Decide title="Void bill" submit="Void" decision={decision} onOpenChange={onOpenChange}>
      <Field label="Reason" value={reason} onValueChange={(v) => setReason(v.slice(0, 200))} autoComplete="off" />
    </Decide>
  );
}

export function WriteOffDialog({ doc, onOpenChange }: { doc: DocRow; onOpenChange: Open }) {
  const [amount, setAmount] = useState(() => dollars(doc.remaining));
  const [memo, setMemo] = useState('');
  const decision = (): Decision | string => {
    const c = centsOf(amount);
    if (!c) return 'A dollar amount, like 500';
    if (c > doc.remaining) return `At most ${cash(doc.remaining, true)}`;
    const m = memo.trim();
    return {
      kind: 'invoice_write_off',
      payload: { invoice_id: doc.id, amount: c, ...(m ? { memo: m } : {}) },
      action: 'finance.writeOff',
      input: { invoice: doc.id, amount: c / 100, ...(m ? { memo: m } : {}) },
    };
  };
  return (
    <Decide title="Write off" submit="Write off" decision={decision} onOpenChange={onOpenChange}>
      <Field label="Amount" value={amount} onValueChange={setAmount} placeholder="$0" autoComplete="off" />
      <Field label="Memo" value={memo} onValueChange={(v) => setMemo(v.slice(0, 200))} autoComplete="off" />
    </Decide>
  );
}

// ---------------------------------------------------------------- a card's limit (s411)

export function LimitDialog({ card, onOpenChange }: { card: Card; onOpenChange: Open }) {
  const [amount, setAmount] = useState(() => dollars(card.limit));
  const decision = (): Decision | string => {
    const c = centsOf(amount);
    if (!c) return "A dollar amount, like 500; freeze the card to stop it";
    return { kind: 'card_update_limit', payload: { card_id: card.id, amount: c }, action: 'finance.setCardLimit', input: { card: card.id, amount: c / 100 } };
  };
  return (
    <Decide title={`${card.nickname || 'Card'} limit`} submit="Save" decision={decision} onOpenChange={onOpenChange}>
      <Field label="Limit / mo" value={amount} onValueChange={setAmount} placeholder="$0" autoComplete="off" />
    </Decide>
  );
}
