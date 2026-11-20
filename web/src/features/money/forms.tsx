import { useQuery } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { fmtDate } from '@/lib';
import { meQuery, useActor, useActors } from '@/data';
import { Button } from '@/kit/Button';
import { Checkbox } from '@/kit/Checkbox';
import { DatePick } from '@/kit/DatePick';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Select } from '@/kit/Select';
import { Table } from '@/kit/Table';
import { toast } from '@/kit/Toast';
import { readSheet, type Sheet } from './history';
import { cash, dayOf } from './model';
import { Prow } from './parts';
import { Ask, useAsk, useRun, why } from './dialogs';
import {
  accessQuery,
  base64Of,
  journalChartQuery,
  lineShareQuery,
  payFromQuery,
  previewStatement,
  STATEMENT_MAX,
  tryDecision,
  vendorsQuery,
  type Decided,
  type Signs,
  type SourceRow,
  type StatementReview,
} from './queries';

/* The old app's other Finance dialogs: whose view to read, the bills address, a statement file, a line's attribution,
   a rule, and a spreadsheet's history. Each act is an action (actions.ts), so Tico can do the same. */

type Open = (open: boolean) => void;

// ---------------------------------------------------------------- view as (s456)

/** The owner picks a teammate and reads Finance as they see it. */
export function ViewAsDialog({ onOpenChange }: { onOpenChange: Open }) {
  const ask = useAsk(onOpenChange);
  const me = useQuery(meQuery).data?.id;
  const access = useQuery(accessQuery);
  const actors = useActors();
  const nameOf = (id: string) => actors.find((a) => a.id === id)?.name ?? id.slice(id.indexOf(':') + 1);
  const options = (access.data?.teammates ?? [])
    .filter((t) => t.teammate !== me)
    .map((t) => ({ value: t.teammate, label: nameOf(t.teammate) }))
    .sort((a, b) => a.label.localeCompare(b.label));
  const [pick, setPick] = useState<string | null>(null);
  const who = pick ?? options[0]?.value ?? null;
  return (
    <Ask
      title="View as"
      submit="View"
      onOpenChange={onOpenChange}
      onSubmit={() => (who ? void ask.go('finance.viewAs', { teammate: who }) : ask.setError('Pick a teammate'))}
      busy={ask.busy}
      error={ask.error}
    >
      <Select label="Teammate" options={options} value={who} onValueChange={setPick} />
    </Ask>
  );
}

// ---------------------------------------------------------------- the bills address (s464)

/** One address: bills emailed to it land in Payables. Empty clears it. */
export function BillsAddressDialog({ address, onOpenChange }: { address: string; onOpenChange: Open }) {
  const ask = useAsk(onOpenChange);
  const [text, setText] = useState(address);
  const save = () => {
    const a = text.trim();
    if (a && !/^[^@\s]+@[^@\s]+$/.test(a)) return ask.setError('An email, like bills@company.com');
    void ask.go('finance.setBillsAddress', { address: a || null });
  };
  return (
    <Ask title="Bills address" submit="Save" onOpenChange={onOpenChange} onSubmit={save} busy={ask.busy} error={ask.error}>
      <Field label="Address" type="email" value={text} onValueChange={(v) => setText(v.slice(0, 254))} autoComplete="off" />
    </Ask>
  );
}

// ---------------------------------------------------------------- a statement (s455)

const usd = (c: number) => `${c < 0 ? '−' : ''}$${(Math.abs(c) / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const monthOf = (day: string) => new Date(`${day}T12:00:00Z`).toLocaleDateString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });

/** A file picked from disk, behind a plain button (the kit has no file field). */
function FilePick({ label, accept, file, onFile }: { label: string; accept: string; file: File | null; onFile: (f: File | null) => void }) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <div className="file-pick">
      <input ref={input} type="file" hidden accept={accept} aria-label={label} onChange={(e) => onFile(e.target.files?.[0] ?? null)} />
      <Button icon="attach" onClick={() => input.current?.click()}>
        {file ? 'Change file' : 'Choose file'}
      </Button>
      <span className="row-sub">{file?.name ?? ''}</span>
    </div>
  );
}

type Shown = { review: StatementReview; file: File; data: string; signs: Signs };

/**
 * A CSV, OFX or QFX statement for one bank or card account (finance-statement-upload.ts): Preview shows the tie-out and
 * each row, new or already in Tico; Post sends that preview's token, so it posts what was shown. Turning the signs over
 * previews again. Only the newest preview may show.
 */
export function StatementDialog({ account, onOpenChange }: { account: SourceRow; onOpenChange: Open }) {
  const ask = useAsk(onOpenChange);
  const [file, setFile] = useState<File | null>(null);
  const [shown, setShown] = useState<Shown | null>(null);
  const turn = useRef(0);
  const review = async (f: File, signs: Signs, data?: string) => {
    const mine = ++turn.current;
    setShown(null);
    ask.setError('');
    ask.setBusy(true);
    try {
      const bytes = data ?? (await base64Of(f));
      const r = await previewStatement(account.id, bytes, f.name, signs);
      if (mine === turn.current) setShown({ review: r, file: f, data: bytes, signs });
    } catch (e) {
      if (mine === turn.current) ask.setError(e instanceof Error && e.message ? e.message : 'Not read');
    } finally {
      if (mine === turn.current) ask.setBusy(false);
    }
  };
  const press = () => {
    if (!file) return ask.setError('Pick a CSV, OFX or QFX file');
    if (file.size > STATEMENT_MAX) return ask.setError('A statement file is 1 MB at most');
    if (!shown || shown.file !== file) return void review(file, 'auto');
    if (!shown.review.new) return ask.setError('No new rows to post');
    void ask.go('finance.postStatement', {
      account: account.id,
      file: shown.data,
      name: file.name,
      token: shown.review.token,
      ...(shown.signs === 'auto' ? {} : { signs: shown.signs }),
    });
  };
  const r = shown?.review;
  const card = !!r?.card;
  const t = r?.tie;
  const posts = r ? `Post ${r.new ?? 0} ${r.new === 1 ? 'row' : 'rows'}` : 'Preview';
  return (
    <Ask title="Upload statement" submit={posts} onOpenChange={onOpenChange} onSubmit={press} busy={ask.busy} error={ask.error}>
      <FilePick
        label="Statement file"
        accept=".csv,.ofx,.qfx,text/csv"
        file={file}
        onFile={(f) => {
          turn.current++;
          setShown(null);
          setFile(f);
        }}
      />
      {r && t && shown && (
        <div className="stmt">
          <p className="row-sub">{[account.last4 ? `${account.name} ••${account.last4}` : account.name, r.end ? monthOf(r.end) : '', shown.file.name].filter(Boolean).join(' · ')}</p>
          <p className="stmt-tie">
            {`Opening ${usd(t.opening)} + ${card ? 'Charges' : 'In'} ${usd(t.added)} − ${card ? 'Payments' : 'Out'} ${usd(t.removed)} = Ends at ${usd(t.ends)}`}
            {t.statement_says != null && ` · Statement says ${usd(t.statement_says)}`}
            {t.off_by ? ` · ${usd(Math.abs(t.off_by))} off` : ''}
          </p>
          <Checkbox
            label={card ? 'Charges show positive' : 'Money out shows positive'}
            checked={!!r.signs?.flipped}
            onCheckedChange={(on) => void review(shown.file, on ? 'flipped' : 'as_shown', shown.data)}
          />
          <Table label="Statement rows">
            <tbody>
              {r.rows.map((x) => (
                <tr key={x.id} className={x.status === 'new' ? undefined : 'is-quiet'}>
                  <td className="t-words">{fmtDate(x.date)}</td>
                  <td className="t-name">{x.vendor?.name || x.description}</td>
                  <td className="num t-fig">{usd(x.amount)}</td>
                  <td className="t-words">{x.status === 'new' ? 'New' : 'Already in Tico'}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
    </Ask>
  );
}

// ---------------------------------------------------------------- shares: a line's, a rule's (s458, s459)

export type Split = { teammate?: string; team?: string; share_bps: number };
type ShareRow = { who: string; pct: string };

const ALL = 10_000;
const pctOf = (bps: number) => String(Number((bps / 100).toFixed(2)));
const bpsOf = (t: string) => (/^\d{1,3}(\.\d{1,2})?$/.test(t.trim()) ? Math.round(Number(t) * 100) : NaN);
const refOf = (p: { teammate?: string; team?: string }) => (p.teammate ? `teammate:${p.teammate}` : p.team ? `team:${p.team}` : '');
const rowsOf = (parts: readonly { teammate?: string; team?: string; share_bps: number }[]): ShareRow[] =>
  parts.length ? parts.map((p) => ({ who: refOf(p), pct: pctOf(p.share_bps) })) : [{ who: '', pct: '' }];

/** The split the rows hold: each a teammate or team once, a share from 0.01 to 100%, no more than 100% in all. */
export function readShares(rows: readonly ShareRow[]): Split[] | string {
  const out: Split[] = [];
  const seen = new Set<string>();
  let total = 0;
  for (const r of rows) {
    if (!r.who) return 'Pick a teammate or team';
    const bps = bpsOf(r.pct);
    if (!(bps >= 1 && bps <= ALL)) return 'A share from 0.01 to 100%, like 50';
    if (seen.has(r.who)) return 'Each teammate or team once';
    seen.add(r.who);
    total += bps;
    const at = r.who.indexOf(':');
    const [kind, id] = [r.who.slice(0, at), r.who.slice(at + 1)];
    out.push(kind === 'team' ? { team: id, share_bps: bps } : { teammate: id, share_bps: bps });
  }
  if (!out.length) return 'Add a share';
  if (total > ALL) return `The shares add to ${pctOf(total)}%, over 100%`;
  return out;
}

/** Rows of a teammate or team and its share, with + and what is left unattributed. */
function Shares({ rows, onChange }: { rows: readonly ShareRow[]; onChange: (rows: ShareRow[]) => void }) {
  const access = useQuery(accessQuery);
  const actors = useActors();
  const nameOf = (id: string) => actors.find((a) => a.id === id)?.name ?? id.slice(id.indexOf(':') + 1);
  const options = [
    ...(access.data?.teammates ?? []).map((t) => ({ value: `teammate:${t.teammate}`, label: nameOf(t.teammate) })).sort((a, b) => a.label.localeCompare(b.label)),
    ...(access.data?.teams ?? []).map((t) => ({ value: `team:${t.id}`, label: t.name })).sort((a, b) => a.label.localeCompare(b.label)),
  ];
  // One no longer on the list (a teammate who left) still shows, so it can be changed.
  const gone = rows.filter((r) => r.who && !options.some((o) => o.value === r.who)).map((r) => ({ value: r.who, label: nameOf(r.who.slice(r.who.indexOf(':') + 1)) }));
  const set = (i: number, patch: Partial<ShareRow>) => onChange(rows.map((r, k) => (k === i ? { ...r, ...patch } : r)));
  const total = rows.reduce((a, r) => a + (bpsOf(r.pct) || 0), 0);
  return (
    <div className="shares" role="group" aria-label="Split">
      {rows.map((r, i) => (
        <div key={i} className="share-row">
          <Select label="Teammate or team" labelHidden options={[...gone, ...options]} value={r.who || null} placeholder="Teammate or team" onValueChange={(v) => set(i, { who: v })} />
          <Field label="Share, %" labelHidden value={r.pct} onValueChange={(v) => set(i, { pct: v })} placeholder="%" wide={false} autoComplete="off" />
          <IconButton icon="close" label="Remove this share" size="sm" quiet onClick={() => onChange(rows.filter((_, k) => k !== i))} />
        </div>
      ))}
      <div className="share-foot">
        <Button variant="quiet" icon="plus" disabled={rows.length >= 10} onClick={() => onChange([...rows, { who: '', pct: '' }])}>
          Share
        </Button>
        <span className="count" aria-live="polite">
          {total > ALL ? `${pctOf(total)}%, over 100%` : `Unattributed ${pctOf(ALL - total)}%`}
        </span>
      </div>
    </div>
  );
}

function SetBy({ id }: { id: string }) {
  const a = useActor(id || null);
  return <>{`Set by ${a?.name ?? id.slice(id.indexOf(':') + 1)}`}</>;
}

/** A line's split (the old rail's "Attributed to"): Save sets it by hand, Clear drops that, Undo goes back one. */
export function ShareDialog({ line, onOpenChange }: { line: string; onOpenChange: Open }) {
  const ask = useAsk(onOpenChange);
  const run = useRun();
  const q = useQuery(lineShareQuery(line));
  const [rows, setRows] = useState<ShareRow[] | null>(null);
  const a = q.data;
  const now = rows ?? rowsOf(a?.splits ?? []);
  const save = () => {
    const splits = readShares(now);
    if (typeof splits === 'string') return ask.setError(splits);
    void ask.go('finance.attribute', { line, splits });
  };
  const other = async (id: string, input: object) => {
    ask.setBusy(true);
    const r = await run(id, input);
    ask.setBusy(false);
    if (!r.ok) return ask.setError(why(r));
    onOpenChange(false);
  };
  const from = !a ? '' : a.source === 'rule' ? (a.rule_name ? `Rule: ${a.rule_name}` : 'Rule') : a.source === 'tags' ? 'Tags' : '';
  return (
    <Ask
      title="Attributed to"
      submit="Save"
      onOpenChange={onOpenChange}
      onSubmit={save}
      busy={ask.busy || q.isPending}
      error={ask.error || (q.isError ? "Couldn't read the line" : a && !a.can_change ? 'Not allowed' : '')}
    >
      {a && (
        <p className="row-sub">
          {from || <SetBy id={a.set_by ?? ''} />}
          {a.set_at && a.source !== 'tags' ? ` · ${fmtDate(a.set_at)}` : ''}
        </p>
      )}
      {a && <Shares rows={now} onChange={setRows} />}
      {a?.can_change && (
        <div className="share-foot">
          <Button variant="quiet" onClick={() => other('finance.attribute', { line, splits: [] })}>
            Clear
          </Button>
          {a.history.length > 1 && (
            <Button variant="quiet" onClick={() => other('finance.undoAttribution', { line })}>
              Undo
            </Button>
          )}
        </div>
      )}
    </Ask>
  );
}

export interface RuleRow {
  id: string;
  name: string;
  match: { kind: string; id: string; label?: string };
  splits: { teammate?: string; team?: string; share_bps: number }[];
  enabled: boolean;
  apply_from?: string | null;
}

const MATCH = [
  { value: 'vendor', label: 'Vendor' },
  { value: 'account', label: 'P&L account' },
  { value: 'money_account', label: 'Card or bank' },
] as const;
type MatchKind = (typeof MATCH)[number]['value'];

/** What a rule of this kind can match: vendors, the chart's lines, or the bank and card accounts. */
function useMatches(kind: MatchKind) {
  const vendors = useQuery({ ...vendorsQuery, enabled: kind === 'vendor' });
  const chart = useQuery({ ...journalChartQuery, enabled: kind === 'account' });
  const money = useQuery({ ...payFromQuery, enabled: kind === 'money_account' });
  if (kind === 'vendor') return (vendors.data?.contacts ?? []).map((c) => ({ value: c.id, label: c.name }));
  if (kind === 'account') return (chart.data?.accounts ?? []).map((c) => ({ value: c.id, label: c.number ? `${c.number} ${c.name}` : c.name }));
  return (money.data?.accounts ?? []).map((c) => ({ value: c.id, label: c.last4 ? `${c.name} ••${c.last4}` : c.name }));
}

/** New rule, or one to change: its name, what it matches, the split, and from when (the old rule dialog). */
export function RuleDialog({ rule, onOpenChange }: { rule?: RuleRow; onOpenChange: Open }) {
  const ask = useAsk(onOpenChange);
  const [name, setName] = useState(rule?.name ?? '');
  const [kind, setKind] = useState<MatchKind>((MATCH.find((m) => m.value === rule?.match.kind)?.value ?? 'vendor') as MatchKind);
  const [to, setTo] = useState<string | null>(rule?.match.id ?? null);
  const [rows, setRows] = useState<ShareRow[]>(rowsOf(rule?.splits ?? []));
  const [on, setOn] = useState(rule?.enabled ?? true);
  const [from, setFrom] = useState<Date | null>(rule?.apply_from ? new Date(`${rule.apply_from}T00:00:00`) : null);
  const matches = useMatches(kind);
  const kept = to && !matches.some((m) => m.value === to) && rule?.match.id === to ? [{ value: to, label: rule.match.label || to }] : [];
  const save = () => {
    const n = name.trim();
    if (!n) return ask.setError('Name the rule');
    if (!to) return ask.setError('Pick what it applies to');
    const splits = readShares(rows);
    if (typeof splits === 'string') return ask.setError(splits);
    void ask.go('finance.saveRule', {
      ...(rule ? { rule: rule.id } : {}),
      name: n,
      match: kind,
      to,
      splits,
      on,
      ...(from ? { from: dayOf(from) } : {}),
    });
  };
  return (
    <Ask title={rule ? 'Rule' : 'New rule'} submit={rule ? 'Save' : 'Add'} onOpenChange={onOpenChange} onSubmit={save} busy={ask.busy} error={ask.error}>
      <Field label="Name" value={name} onValueChange={(v) => setName(v.slice(0, 80))} autoComplete="off" />
      <Select
        label="Applies to"
        options={MATCH}
        value={kind}
        onValueChange={(v) => {
          setKind(v);
          setTo(null);
        }}
      />
      <Select label="Match" options={[...kept, ...matches]} value={to} placeholder="Pick" onValueChange={setTo} />
      <Shares rows={rows} onChange={setRows} />
      <DatePick label="From" value={from} onValueChange={setFrom} />
      {rule && <Checkbox label="On" checked={on} onCheckedChange={setOn} />}
    </Ask>
  );
}

// ---------------------------------------------------------------- import history (s465)

type ImportImpact = { already_imported?: string; months?: number; rows?: number; zero_rows?: number; accounts?: { code: string; name: string; total: number }[] };
type Dry = Decided & { impact?: Decided['impact'] & ImportImpact };

const UNITS = [
  { value: 'dollars', label: 'Dollars' },
  { value: 'cents', label: 'Cents' },
] as const;
const BASIS = [
  { value: 'accrual', label: 'Accrual' },
  { value: 'cash', label: 'Cash' },
] as const;

/**
 * A spreadsheet of month, line, amount (and team) posted as raw totals (finance-tax.ts finImportOpen): each sheet line
 * mapped to a line of the chart, then Preview says what would post, then Import.
 */
export function ImportDialog({ onOpenChange }: { onOpenChange: Open }) {
  const ask = useAsk(onOpenChange);
  const chart = useQuery(journalChartQuery);
  const lines = (chart.data?.accounts ?? []).filter((a) => !(a.type === 'equity' && a.subtype === 'opening'));
  const options = lines.map((a) => ({ value: a.id, label: a.number ? `${a.number} ${a.name}` : a.name }));
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [units, setUnits] = useState<'dollars' | 'cents'>('dollars');
  const [basis, setBasis] = useState<'accrual' | 'cash'>('accrual');
  const [source, setSource] = useState('');
  const [map, setMap] = useState<Record<string, string>>({});
  const [dry, setDry] = useState<{ sig: string; out: Dry } | null>(null);
  let sheet: Sheet | null = null;
  let bad = '';
  if (text != null) {
    try {
      sheet = readSheet(text, units === 'dollars');
    } catch (e) {
      bad = e instanceof Error ? e.message : 'Not read';
    }
  }
  const lineOf = (n: string) => map[n] ?? lines.find((a) => a.name.toLowerCase() === n.toLowerCase())?.id ?? null;
  const take = async (f: File | null) => {
    setFile(f);
    setDry(null);
    setText(null);
    if (!f) return;
    if (f.size > 512 * 1024) return ask.setError('The file is over 512 KB');
    ask.setError('');
    setText(await f.text());
  };
  const payload = () => {
    if (!sheet) return null;
    const mapping: Record<string, string> = {};
    for (const n of sheet.names) {
      const id = lineOf(n);
      if (!id) return `Map ${n}`;
      mapping[n] = id;
    }
    return { csv: sheet.csv, basis, mapping, ...(source.trim() ? { source: source.trim() } : {}) };
  };
  const p = payload();
  const sig = JSON.stringify(p);
  const shown = dry && dry.sig === sig ? dry.out : null;
  const press = async () => {
    if (bad) return ask.setError(bad);
    if (!p) return ask.setError('Pick a CSV file');
    if (typeof p === 'string') return ask.setError(p);
    if (shown?.impact?.already_imported) return onOpenChange(false);
    if (shown) return void ask.go('finance.importHistory', p);
    ask.setBusy(true);
    ask.setError('');
    try {
      const out = (await tryDecision('import_raw_totals', { csv: p.csv, basis: p.basis, mapping: p.mapping, ...(p.source ? { source_name: p.source } : {}) })) as Dry;
      if (out.status !== 'committed' && out.status !== 'needs_approval') return ask.setError(out.reason || 'Not done');
      setDry({ sig, out });
    } catch {
      ask.setError("Couldn't check");
    } finally {
      ask.setBusy(false);
    }
  };
  const x = shown?.impact;
  const submit = !shown ? 'Preview' : x?.already_imported ? 'Done' : shown.status === 'needs_approval' ? 'Send' : 'Import';
  return (
    <Ask title="Import history" submit={submit} onOpenChange={onOpenChange} onSubmit={() => void press()} busy={ask.busy} error={ask.error || bad}>
      <FilePick label="Sheet file" accept=".csv,text/csv" file={file} onFile={(f) => void take(f)} />
      <Select label="Amounts" options={UNITS} value={units} onValueChange={setUnits} />
      <Select label="Basis" options={BASIS} value={basis} onValueChange={setBasis} />
      <Field label="Source" value={source} onValueChange={(v) => setSource(v.slice(0, 120))} placeholder="Sheet name" autoComplete="off" />
      {sheet?.names.map((n) => (
        <Select key={n} label={n} options={options} value={lineOf(n)} placeholder="Pick a line" onValueChange={(v) => setMap((m) => ({ ...m, [n]: v }))} />
      ))}
      {x?.already_imported && <Prow name="Already imported" fig={x.already_imported} />}
      {x && !x.already_imported && (
        <div>
          <Prow name="Months" fig={String(x.months ?? '')} />
          <Prow name="Rows" fig={String(x.rows ?? '')} />
          {(x.accounts ?? []).map((a) => (
            <Prow key={a.code + a.name} name={`${a.code} ${a.name}`} fig={cash(a.total)} />
          ))}
        </div>
      )}
    </Ask>
  );
}

/** Says a copied address landed. */
export const copied = (v: string) =>
  void navigator.clipboard?.writeText(v).then(
    () => toast({ text: 'Copied' }),
    () => toast({ text: "Couldn't copy", kind: 'failed' }),
  );
