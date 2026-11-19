import { useQuery } from '@tanstack/react-query';
import { useRouter } from '@tanstack/react-router';
import { useId, useState, type ReactNode } from 'react';
import { registry } from '@/actions';
import { runIn } from '@/actions/run';
import type { Result } from '@/actions/types';
import { meQuery, useActor, useActors } from '@/data';
import { Button } from '@/kit/Button';
import { DatePick } from '@/kit/DatePick';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { newId } from '@/lib';
import { budgetDay, cash, centsOf, dayOf } from './model';
import { Prow, useViewAs } from './parts';
import {
  accessQuery,
  BOOKS,
  budgetsQuery,
  KEY_SOURCES,
  mercuryUsersQuery,
  payFromQuery,
  serviceKeysQuery,
  tryDecision,
  type AccessDefaults,
  type Impact,
  type KeyProvider,
  type ToolKey,
} from './queries';

/* The old app's Finance dialogs, each sending what the old one sent: a transfer (tried first, then sent for approval
   when the rules ask), a card, a source connected with a key or a sign-in, the books, Stripe's webhook, the access
   defaults. A secret goes through an action Tico cannot run, and what is shown once comes back in the action's `out`. */

type Open = (open: boolean) => void;

/** What the person can do in Finance, from their row on the access list (finance.ts finCapsOf). Reading as a teammate
    is read only. */
export function useMay() {
  const me = useQuery(meQuery).data?.id;
  const acc = useQuery(accessQuery).data;
  const viewing = !!useViewAs();
  const row = acc?.teammates.find((t) => t.teammate === me);
  const rights = new Set(row?.rights ?? []);
  return (right: string) => !viewing && !!row && (row.default === 'everything' || rights.has(right) || rights.has('finance.admin'));
}

/** Runs an action from a press and says how it went; a refusal's words stay in the dialog. */
export function useRun() {
  const router = useRouter();
  return async (id: string, input: unknown): Promise<Result> => {
    const r = await runIn(registry, id, input, 'click', router);
    if (r.ok && r.did) toast({ text: r.did });
    return r;
  };
}

export const why = (e: string | undefined) => (e?.startsWith('failed: ') ? e.slice(8) : e === 'not_allowed' ? 'Not allowed' : "Couldn't save");

/** A dialog that asks for something: its fields, a line for what went wrong, Cancel and the act. */
export function Ask({
  title,
  submit,
  onOpenChange,
  onSubmit,
  busy,
  error,
  description,
  children,
}: {
  title: string;
  submit: string;
  onOpenChange: Open;
  onSubmit: () => void;
  busy: boolean;
  error: string;
  description?: string;
  children?: ReactNode;
}) {
  const form = useId();
  return (
    <Dialog
      open
      onOpenChange={onOpenChange}
      title={title}
      description={description}
      className="money"
      actions={
        <>
          <Button variant="quiet" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form={form} busy={busy}>
            {submit}
          </Button>
        </>
      }
    >
      <form
        id={form}
        className="dlg-form"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          if (!busy) onSubmit();
        }}
      >
        {children}
        {error && (
          <p className="dlg-error" role="alert">
            {error}
          </p>
        )}
      </form>
    </Dialog>
  );
}

/** Busy and error for one dialog; `go` runs the action and closes on success. */
export function useAsk(onOpenChange: Open) {
  const run = useRun();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const go = async (id: string, input: unknown, after?: (r: Result) => void) => {
    setBusy(true);
    setError('');
    const r = await run(id, input);
    setBusy(false);
    if (!r.ok) return setError(why(r.error));
    onOpenChange(false);
    after?.(r);
  };
  return { busy, error, setError, setBusy, go };
}

/**
 * The Idempotency-Key for a dialog that moves money: one per opening and field values. Pressing again on an unchanged
 * dialog after an answer that never came back sends the same key, so the server does it once; a changed field is a new
 * request with its own key.
 */
function useSendKey(fields: string): string {
  const [opened] = useState(newId);
  // FNV-1a: a short, stable stand-in for the fields (the key holds at most 200 characters).
  let h = 0x811c9dc5;
  for (let i = 0; i < fields.length; i++) h = Math.imul(h ^ fields.charCodeAt(i), 0x01000193);
  return `${opened}.${(h >>> 0).toString(36)}`;
}

// ---------------------------------------------------------------- the decision, tried first (s449)

function Approver({ who }: { who: string }) {
  const a = useActor(who.includes(':') ? who : null);
  const name = a?.name ?? (who.includes(':') ? who.slice(who.indexOf(':') + 1) : who);
  return <p className="dlg-ask">{name ? `Approval from ${name}` : 'Needs approval'}</p>;
}

type Decision = { kind: string; payload: object; action: string; input: object };

/**
 * A dialog whose act is a decision (finance-docs.ts finDecideDialog). The first press tries it: one the rules allow is
 * sent at once; one that needs approval names the approver and what each budget would have left, and the act becomes
 * Send. Changing a field asks again.
 */
function Decide({ title, submit, decision, onOpenChange, children }: { title: string; submit: string; decision: () => Decision | string; onOpenChange: Open; children: ReactNode }) {
  const ask = useAsk(onOpenChange);
  const [asked, setAsked] = useState<{ sig: string; who: string; impact?: Impact } | null>(null);
  const now = decision();
  const sig = typeof now === 'string' ? '' : JSON.stringify([now.kind, now.payload]);
  const key = useSendKey(sig);
  const waiting = asked && asked.sig === sig ? asked : null;
  const press = async () => {
    if (typeof now === 'string') return ask.setError(now);
    if (!waiting) {
      ask.setBusy(true);
      ask.setError('');
      try {
        const dry = await tryDecision(now.kind, now.payload);
        if (dry.status === 'needs_approval') return setAsked({ sig, who: dry.approver ?? '', impact: dry.impact });
        if (dry.status !== 'committed') return ask.setError(dry.reason || 'Not done');
      } catch {
        return ask.setError("Couldn't check");
      } finally {
        ask.setBusy(false);
      }
    }
    await ask.go(now.action, { ...now.input, key });
  };
  const left = (waiting?.impact?.budgets ?? []).filter((b) => b.remaining != null);
  return (
    <Ask title={title} submit={waiting ? 'Send' : submit} onOpenChange={onOpenChange} onSubmit={() => void press()} busy={ask.busy} error={ask.error}>
      {children}
      {waiting && <Approver who={waiting.who} />}
      {left.map((b) => (
        <Prow key={b.name} name={`${b.name} left`} fig={`${cash(b.remaining)} → ${cash(b.remaining_after)}`} />
      ))}
    </Ask>
  );
}

// ---------------------------------------------------------------- a transfer, a card (s461, s460)

export function TransferDialog({ onOpenChange }: { onOpenChange: Open }) {
  const q = useQuery(payFromQuery);
  const options = (q.data?.accounts ?? []).map((a) => ({ value: a.id, label: a.last4 ? `${a.name} ••${a.last4}` : a.name }));
  const [from, setFrom] = useState<string | null>(null);
  const [to, setTo] = useState<string | null>(null);
  const [amount, setAmount] = useState('');
  const [memo, setMemo] = useState('');
  const f = from ?? options[0]?.value ?? null;
  const t = to ?? options[1]?.value ?? null;
  const decision = (): Decision | string => {
    const c = centsOf(amount);
    if (!c) return 'A dollar amount, like 500';
    if (!f || !t || f === t) return 'Pick two different accounts';
    const m = memo.trim();
    return {
      kind: 'payment_create',
      payload: { method: 'internal', from_account: f, to_account: t, amount: c, ...(m ? { memo: m } : {}) },
      action: 'finance.transfer',
      input: { from: f, to: t, amount: c / 100, ...(m ? { memo: m } : {}) },
    };
  };
  return (
    <Decide title="Transfer" submit="Transfer" decision={decision} onOpenChange={onOpenChange}>
      <Select label="From" options={options} value={f} onValueChange={setFrom} />
      <Select label="To" options={options} value={t} onValueChange={setTo} />
      <Field label="Amount" value={amount} onValueChange={setAmount} placeholder="$0" autoComplete="off" />
      <Field label="Memo" value={memo} onValueChange={(v) => setMemo(v.slice(0, 140))} autoComplete="off" />
    </Decide>
  );
}

export function CardDialog({ onOpenChange }: { onOpenChange: Open }) {
  const ask = useAsk(onOpenChange);
  const actors = useActors();
  const access = useQuery(accessQuery);
  const budgets = useQuery(budgetsQuery(budgetDay()));
  const users = useQuery(mercuryUsersQuery);
  const nameOf = (id: string) => actors.find((a) => a.id === id)?.name ?? id.slice(id.indexOf(':') + 1);
  const holders = (access.data?.teammates ?? []).map((t) => ({ value: t.teammate, label: nameOf(t.teammate) }));
  const lines = (budgets.data?.budgets ?? []).filter((b) => b.active !== false && !b.catch_all).map((b) => ({ value: b.id, label: b.name }));
  const people = (users.data?.users ?? []).map((u) => ({ value: u.id, label: u.email ? `${u.name} · ${u.email}` : u.name }));
  const [holder, setHolder] = useState<string | null>(null);
  const [budget, setBudget] = useState<string | null>(null);
  const [user, setUser] = useState<string | null>(null);
  const [amount, setAmount] = useState('');
  const [name, setName] = useState('');
  const h = holder ?? holders[0]?.value ?? null;
  const b = budget ?? lines[0]?.value ?? null;
  const u = user ?? people[0]?.value ?? null;
  const key = useSendKey(JSON.stringify([h, b, centsOf(amount), u, name.trim()]));
  const create = () => {
    const c = centsOf(amount);
    if (!c) return ask.setError('A dollar amount, like 500');
    if (!b) return ask.setError('Pick a budget');
    if (!u) return ask.setError('No Mercury users yet');
    if (!h) return ask.setError('Pick a holder');
    void ask.go('finance.newCard', { holder: h, budget: b, amount: c / 100, user: u, ...(name.trim() ? { name: name.trim() } : {}), key });
  };
  return (
    <Ask title="New card" submit="Create" onOpenChange={onOpenChange} onSubmit={create} busy={ask.busy} error={ask.error}>
      <Select label="Holder" options={holders} value={h} onValueChange={setHolder} />
      <Select label="Budget" options={lines} value={b} onValueChange={setBudget} />
      <Field label="Limit / mo" value={amount} onValueChange={setAmount} placeholder="$0" autoComplete="off" />
      <Field label="Name" value={name} onValueChange={(v) => setName(v.slice(0, 50))} autoComplete="off" />
      <Select label="Mercury user" options={people} value={u} onValueChange={setUser} />
    </Ask>
  );
}

// ---------------------------------------------------------------- connecting a source (s450, s451)

const SECRET: Record<KeyProvider, { secret: string; paste: string; hint?: string; level: 'read' | 'read_write' }> = {
  mercury: { secret: 'API token', paste: 'Paste the token', level: 'read_write' },
  brex: { secret: 'API token', paste: 'Paste the token', level: 'read' },
  stripe: { secret: 'Restricted key', paste: 'Paste the key', hint: 'rk_live_…', level: 'read' },
};
const LEVELS = [
  { value: 'read', label: 'Read' },
  { value: 'read_write', label: 'Read and write' },
] as const;
const PASTE = 'new';

/** "Mercury · Read and write · ••••a1f": the key's tool, level and the end of its preview. */
const keyLabel = (tool: string, k: ToolKey) =>
  [tool, k.access_level === 'read' ? 'Read' : 'Read and write', String(k.preview ?? '').includes('…') ? `••••${String(k.preview).split('…').pop()}` : '']
    .filter(Boolean)
    .join(' · ');

/** Mercury, Brex or Stripe: a key already on Tools (Read and write first) or one pasted, stored on Tools at its level. */
export function ConnectKeyDialog({ provider, onOpenChange }: { provider: KeyProvider; onOpenChange: Open }) {
  const src = SECRET[provider];
  const ask = useAsk(onOpenChange);
  const tools = useQuery(serviceKeysQuery(provider));
  const keys = (tools.data?.tools ?? []).flatMap((t) => t.keys.filter((k) => k.stored).map((k) => ({ value: k.id, label: keyLabel(t.name, k), rw: k.access_level === 'read_write' })));
  const first = keys.find((k) => k.rw)?.value ?? keys[0]?.value ?? PASTE;
  const [pick, setPick] = useState<string | null>(null);
  const [token, setToken] = useState('');
  const [level, setLevel] = useState<'read' | 'read_write'>(src.level);
  const [since, setSince] = useState<Date | null>(() => new Date(new Date().getFullYear(), 0, 1));
  const [approver, setApprover] = useState('');
  const key = pick ?? first;
  const send = () => {
    if (key === PASTE && !token.trim()) return ask.setError(src.paste);
    const a = approver.trim();
    if (a && !/^[^@\s]+@[^@\s]+$/.test(a)) return ask.setError('An email, like name@company.com');
    void ask.go('finance.connect', {
      provider,
      ...(key === PASTE ? { token: token.trim(), level } : { key }),
      ...(since ? { from: dayOf(since) } : {}),
      ...(a ? { approver: a } : {}),
    });
  };
  return (
    <Ask title={`Connect ${KEY_SOURCES[provider]}`} submit="Connect" onOpenChange={onOpenChange} onSubmit={send} busy={ask.busy || tools.isPending} error={ask.error || (tools.isError ? 'Tools could not be read' : '')}>
      {keys.length > 0 && (
        <Select label={`Use the ${KEY_SOURCES[provider]} key from Tools`} options={[...keys, { value: PASTE, label: 'Paste a new key' }]} value={key} onValueChange={setPick} />
      )}
      {key === PASTE && (
        <>
          <Field label={src.secret} type="password" value={token} onValueChange={setToken} placeholder={src.hint} autoComplete="off" />
          <Select label="Level" options={LEVELS} value={level} onValueChange={setLevel} />
        </>
      )}
      <DatePick label="History from" value={since} onValueChange={setSince} />
      {provider === 'mercury' && <Field label="Approver email" type="email" value={approver} onValueChange={setApprover} autoComplete="off" />}
    </Ask>
  );
}

/** QuickBooks or Xero: on to its own sign-in page. */
export function ConnectBooksDialog({ provider, onOpenChange }: { provider: keyof typeof BOOKS; onOpenChange: Open }) {
  const ask = useAsk(onOpenChange);
  return (
    <Ask
      title={`Connect ${BOOKS[provider]}`}
      submit="Continue"
      onOpenChange={onOpenChange}
      onSubmit={() => void ask.go('finance.connectBooks', { provider })}
      busy={ask.busy}
      error={ask.error}
    />
  );
}

// ---------------------------------------------------------------- the books, a source, a webhook (s452, s453, s454)

/** One act to confirm: Use as books, Stop, Disconnect. */
export function ConfirmDialog({ title, submit, action, input, onOpenChange }: { title: string; submit: string; action: string; input: object; onOpenChange: Open }) {
  const ask = useAsk(onOpenChange);
  return <Ask title={title} submit={submit} onOpenChange={onOpenChange} onSubmit={() => void ask.go(action, input)} busy={ask.busy} error={ask.error} />;
}

export function WebhookDialog({ onOpenChange, onShown }: { onOpenChange: Open; onShown: (pairs: [string, string][]) => void }) {
  const ask = useAsk(onOpenChange);
  const [secret, setSecret] = useState('');
  const add = () =>
    void ask.go('finance.addWebhook', secret.trim() ? { secret: secret.trim() } : {}, (r) => {
      const pairs = Array.isArray(r.out) ? (r.out as [string, string][]) : [];
      if (pairs.length) onShown(pairs);
    });
  return (
    <Ask title="Stripe webhook" submit="Add" onOpenChange={onOpenChange} onSubmit={add} busy={ask.busy} error={ask.error}>
      <Field label="Signing secret" value={secret} onValueChange={(v) => setSecret(v.slice(0, 300))} placeholder="Leave empty to make one" autoComplete="off" />
    </Ask>
  );
}

/** Values copied now and never seen again: an address, a signing secret. It closes only on Done. */
export function ShownOnce({ title, pairs, onClose }: { title: string; pairs: readonly [string, string][]; onClose: () => void }) {
  const copy = (v: string) =>
    void navigator.clipboard?.writeText(v).then(
      () => toast({ text: 'Copied' }),
      () => toast({ text: "Couldn't copy", kind: 'failed' }),
    );
  return (
    <Dialog
      open
      alert
      onOpenChange={(o) => !o && onClose()}
      title={title}
      className="money"
      actions={
        <Button variant="primary" onClick={onClose}>
          Done
        </Button>
      }
    >
      {pairs.map(([k, v]) => (
        <div key={k} className="once" role="status">
          <p>
            <b>{k}</b> <span>Shown once</span>
          </p>
          <div className="once-row">
            <code>{v}</code>
            <Button variant="quiet" icon="copy" onClick={() => copy(v)}>
              Copy
            </Button>
          </div>
        </div>
      ))}
    </Dialog>
  );
}

// ---------------------------------------------------------------- access defaults (s457)

const MANAGER = [
  { value: 'none', label: 'Same as everyone else' },
  { value: 'team_expenses', label: 'Expenses of what they manage' },
  { value: 'own_expenses', label: 'Own expenses' },
] as const;
const PAYROLL = [
  { value: 'totals', label: 'Totals' },
  { value: 'hidden', label: 'Hidden' },
] as const;
const MEMBER = [
  { value: 'none', label: 'Nothing' },
  { value: 'own_expenses', label: 'Own expenses' },
] as const;

/** What each kind of teammate sees by default. Owners see everything; revenue and the books stay with owners. */
export function DefaultsDialog({ defaults, onOpenChange }: { defaults: Partial<AccessDefaults>; onOpenChange: Open }) {
  const ask = useAsk(onOpenChange);
  const [pick, setPick] = useState<Partial<AccessDefaults>>({});
  const value = (k: keyof AccessDefaults) => pick[k] ?? defaults[k] ?? null;
  const set = (k: keyof AccessDefaults) => (v: string) => setPick((p) => ({ ...p, [k]: v }));
  const save = () => {
    const changed = Object.fromEntries(Object.entries(pick).filter(([k, v]) => v && v !== defaults[k as keyof AccessDefaults]));
    if (!Object.keys(changed).length) return onOpenChange(false);
    void ask.go('finance.setDefaults', changed);
  };
  return (
    <Ask title="Edit defaults" submit="Save" onOpenChange={onOpenChange} onSubmit={save} busy={ask.busy} error={ask.error}>
      <Select label="Owners" options={[{ value: 'all', label: 'Everything' }]} value="all" onValueChange={() => undefined} disabled />
      <Select label="Managers" options={MANAGER} value={value('manager')} onValueChange={set('manager')} />
      <Select label="Managers' payroll" options={PAYROLL} value={value('manager_payroll')} onValueChange={set('manager_payroll')} />
      <Select label="Everyone else" options={MEMBER} value={value('member')} onValueChange={set('member')} />
      <Select label="Revenue, balance sheet, payables, receivables" options={[{ value: 'owners', label: 'Owners only' }]} value="owners" onValueChange={() => undefined} disabled />
    </Ask>
  );
}
