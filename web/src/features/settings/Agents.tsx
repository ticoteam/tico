import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { reasonIn, refusal, run } from '@/actions';
import { configQuery, meQuery } from '@/data';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { poll } from '@/lib';
import { Section } from '@/pagekinds';
import { modelName } from '@/features/team/routing';
import { AgentList } from './AgentList';
import { editSubscriptionName } from './actions';
import { GatewayLines } from './Gateway';
import { installCommands, type Command, type ComputerKind, type RunnerConfig } from './install';
import { Foot, Line, Meter, SwitchLine, Verb, Word, act, usd, useVisible } from './parts';
import {
  LIVE_SIGN_IN,
  SIGN_IN_KINDS,
  accessQuery,
  computersQuery,
  hostedQuery,
  modelsQuery,
  providersQuery,
  readSignIn,
  sleepQuery,
  subscriptionsQuery,
  type SignIn,
  type SignInKind,
  type Subscription,
} from './queries';

/* Agents (what pays for them, then the agents), Computers and Models. */

const RETRY_MS = 3000;
const GIVE_UP_MS = 5 * 60_000;

// ---------------------------------------------------------------- a sign-in to Claude or Codex, one tap

function SignInDialog({ kind, subscription, onClose }: { kind: SignInKind; subscription?: string; onClose: () => void }) {
  const qc = useQueryClient();
  const [login, setLogin] = useState<SignIn | null>(null);
  const [error, setError] = useState<{ message: string; code?: string } | null>(null);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [restart, setRestart] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [pasting, setPasting] = useState(false);
  const [secret, setSecret] = useState('');
  const [code, setCode] = useState('');
  const [tries, setTries] = useState(0);
  // Both timers here (connect again every 3 s while the computer wakes, then the 2 s follow) stop when the dialog
  // closes (unmounted), wait while the tab is hidden, and end after 5 minutes.
  const visible = useVisible();
  const [late, setLate] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setLate(true), GIVE_UP_MS);
    return () => clearTimeout(t);
  }, []);
  useEffect(() => {
    if (late || !visible || starting || login || error || restart || pasting) return;
    const timer = setTimeout(
      () => {
        const input = subscription ? { subscription, ...(confirmed && { confirm: true }) } : { kind };
        // A request already sent can queue a sign-in after the dialog's deadline.
        setStarting(true);
        void run('settings.connect', input, { via: 'click' }).then((r) => {
          setStarting(false);
          if (!r.ok && r.error === 'confirm_restart' && !confirmed) {
            return setRestart(reasonIn(r) ?? 'Running work will restart');
          }
          if (!r.ok) return setError({ message: reasonIn(r) ?? "Couldn't start", code: r.error });
          const got = (r.out as SignIn | null | undefined) ?? null;
          if (!got) return setError({ message: 'No computer can sign in yet', code: 'offline' });
          if (got.id) setLogin(got);
          else if (got.waking) setTries((n) => n + 1);
          else setError({ message: got.message ?? 'No computer can sign in yet', code: 'offline' });
        });
      },
      tries === 0 ? 0 : RETRY_MS,
    );
    return () => clearTimeout(timer);
  }, [kind, subscription, confirmed, restart, pasting, tries, late, visible, starting, login, error]);
  // The follow: every 2 s while it runs, paused while the tab is hidden. Its answers (the provider's one-time code and
  // page) stay in this state, never the query cache.
  const [follow, setFollow] = useState<SignIn | null>(null);
  const runner = login?.runner_id;
  const lid = login?.id;
  const over = !!follow?.state && !LIVE_SIGN_IN.includes(follow.state);
  useEffect(() => {
    if (!runner || !lid || late || !visible || over || pasting) return;
    return poll((signal) => readSignIn(runner, lid, signal), setFollow, (a) => LIVE_SIGN_IN.includes(a.state ?? 'requested'), 2000);
  }, [runner, lid, late, visible, over, pasting]);
  const now = { ...login, ...follow } as SignIn;
  const state = now.state ?? (login ? 'starting' : 'requested');
  const finished = useRef(false);
  useEffect(() => {
    if (state !== 'done' || finished.current) return;
    finished.current = true;
    void qc.invalidateQueries({ queryKey: subscriptionsQuery.queryKey });
    toast({ text: `${now.name ?? 'Subscription'} connected`, kind: 'done' });
    onClose();
  }, [state, now.name, qc, onClose]);
  const cancelLogin = runner && lid && LIVE_SIGN_IN.includes(state)
    ? () => run('settings.cancelSignIn', { computer: runner, login: lid }, { via: 'click' })
    : null;
  const stop = () => {
    if (!pasting) void cancelLogin?.();
    onClose();
  };
  const timedOut = late && !starting && state !== 'done';
  const ended =
    error?.message ??
    (state === 'failed'
      ? (now.message || 'The sign-in did not finish')
      : state === 'expired' || timedOut
        ? 'The sign-in ran out of time'
        : state === 'canceled'
          ? 'Sign-in canceled'
          : null);
  const hostWarning = now.not_yours ? `Credential passes through someone else's computer${now.computer ? ` (${now.computer})` : ''}` : null;
  const signingIn = state === 'waiting' ? undefined : `Signing in${now.computer ? ` on ${now.computer}` : ''}`;
  const offerPaste = error ? error.code === 'offline' : state !== 'done' && (!!hostWarning || !!ended);
  const switchToPaste = async () => {
    if (cancelLogin) {
      const r = await cancelLogin();
      const answer = r.out as SignIn | undefined;
      if (!r.ok || !answer?.state || LIVE_SIGN_IN.includes(answer.state)) {
        setCancelError(reasonIn(r) ?? "Couldn't cancel the sign-in");
        return;
      }
      setFollow(answer);
      if (answer.state === 'done') return;
    }
    setCancelError(null);
    setError(null);
    setPasting(true);
  };
  const send = () => {
    if (!login?.id || !login.runner_id) return;
    void act('settings.signInCode', { computer: login.runner_id, login: login.id, code: code.trim() });
  };
  const codex = kind === 'codex_login';
  const label = codex ? 'ChatGPT plan' : 'Claude plan';
  const paste = async (confirm = confirmed) => {
    const value = secret.trim();
    const input = subscription ? { subscription, secret: value, ...(confirm && { confirm: true }) } : { kind: codex ? 'codex_login' : 'claude_token', secret: value };
    const r = await run('settings.pastePlan', input, { via: 'click' });
    if (r.ok) {
      const saved = r.out as { name?: string } | undefined;
      toast({ text: `${saved?.name || now.name || label} connected`, kind: 'done' });
      onClose();
    } else if (r.error === 'confirm_restart' && !confirm) {
      setRestart(reasonIn(r) ?? 'Running work will restart');
    } else {
      setRestart(null);
      setError({ message: reasonIn(r) ?? "Couldn't save" });
    }
  };
  const confirmRestart = () => {
    if (pasting) return paste(true);
    setRestart(null);
    setConfirmed(true);
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && stop()}
      alert={!!restart}
      title={restart ? 'Restart runs?' : `${subscription ? 'Reconnect' : 'Connect'} ${label}`}
      description={restart ?? (pasting ? error?.message : cancelError ?? ended ?? hostWarning ?? signingIn)}
      actions={
        <>
          <Button variant="quiet" onClick={stop}>
            {ended && !pasting && !restart ? 'Close' : 'Cancel'}
          </Button>
          {restart ? (
            <Button variant="primary" onClick={confirmRestart}>Reconnect</Button>
          ) : pasting ? (
            <Button variant="primary" disabled={!secret.trim()} onClick={() => paste()}>{subscription ? 'Reconnect' : 'Connect'}</Button>
          ) : (
            <>
              {offerPaste && <Button onClick={switchToPaste}>Paste instead</Button>}
              {!ended && !cancelError && now.url && (
                <a className="btn btn-fill" href={now.url} target="_blank" rel="noreferrer">
                  Open {codex ? 'ChatGPT' : 'Claude'}
                </a>
              )}
            </>
          )}
        </>
      }
    >
      {pasting && !restart && (
        <Field label={codex ? 'auth.json' : 'Setup token'} type="password" value={secret} onValueChange={setSecret} autoComplete="off" autoFocus />
      )}
      {!pasting && !restart && !ended && now.code && <LabelValue label="Code">{now.code}</LabelValue>}
      {!pasting && !restart && !ended && now.accepts_code && (
        <div className="set-add">
          <Field label="Code from the page" value={code} onValueChange={setCode} autoComplete="off" />
          <Button disabled={!code.trim()} onClick={send}>
            Send code
          </Button>
        </div>
      )}
    </Dialog>
  );
}

function KeyDialog({ kind, label, onClose }: { kind: string; label: string; onClose: () => void }) {
  const [secret, setSecret] = useState('');
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    const ok = await act('settings.addKey', { kind, secret: secret.trim() });
    setBusy(false);
    if (ok) onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Add ${label}`}
      description="Tico keeps it and never shows it again."
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} disabled={secret.trim().length < 8} onClick={() => save()}>
            Add
          </Button>
        </>
      }
    >
      <Field label={label} type="password" value={secret} onValueChange={setSecret} autoComplete="off" autoFocus />
    </Dialog>
  );
}

// ---------------------------------------------------------------- Agents

const usedWords = (s: Subscription) => {
  const u = s.usage;
  if (s.status === 'needs_reconnect' || s.problem) return null;
  if (u?.used_pct != null) return `${Math.round(u.used_pct)}% of the ${u.window === 'week' ? 'week' : '5 hours'}`;
  if (u?.cost_usd_month != null) return `${usd(u.cost_usd_month)} this month`;
  return null;
};

function EditSubDialog({ s, owned, onClose }: { s: Subscription; owned: boolean; onClose: () => void }) {
  const access = useQuery({ ...accessQuery, enabled: owned });
  const was = s.owner_id.replace(/^human:/, '');
  const [name, setName] = useState(s.name);
  const [owner, setOwner] = useState(was);
  const [busy, setBusy] = useState(false);
  const people = (access.data?.people ?? []).filter((p) => !p.left && p.can_sign_in !== false);
  const save = async () => {
    setBusy(true);
    const ok = await act('settings.editSubscription', { subscription: s.id, name, ...(owned && owner !== was ? { owner } : {}) });
    setBusy(false);
    if (ok) onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Edit ${s.name}`}
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} disabled={!editSubscriptionName.safeParse(name).success} onClick={() => save()}>
            Save
          </Button>
        </>
      }
    >
      <div className="set-add">
        <Field label="Name" value={name} onValueChange={setName} autoFocus />
        {owned && people.length > 0 && (
          <Select label="Held by" value={owner} options={people.map((p) => ({ value: p.id, label: p.name }))} onValueChange={setOwner} />
        )}
      </div>
    </Dialog>
  );
}

function SubRow({ s, open, owned, admin }: { s: Subscription; open: boolean; owned: boolean; admin: boolean }) {
  const [confirm, setConfirm] = useState(false);
  const [editing, setEditing] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const words = usedWords(s);
  const agents = s.agents ?? [];
  const out = s.status === 'needs_reconnect';
  const fix = out ? 'Sign in again' : s.problem;
  const row = (
    <Link
      to="/settings/$section"
      params={{ section: 'agents' }}
      search={open ? {} : { open: s.id }}
      className={fix ? 'row g-fsub is-bad' : 'row g-fsub'}
      data-target={`subscription:${s.id}`}
      aria-expanded={open}
      title={s.name}
    >
      <span className="row-title fs-name">
        <span>{s.name}</span>
        <em>{s.label}</em>
      </span>
      <span className={s.in_use_on.length ? 'fs-on' : 'fs-on is-none'}>
        {s.in_use_on.length ? `${s.in_use_on.length} ${s.in_use_on.length === 1 ? 'computer' : 'computers'}` : 'Not in use'}
      </span>
      <span className="fs-n">{agents.length > 0 ? `${agents.length} ${agents.length === 1 ? 'agent' : 'agents'}` : ''}</span>
      {fix ? (
        <span className="fs-use is-out">
          <Word>{fix}</Word>
        </span>
      ) : (
        <span className={words ? 'fs-use' : 'fs-use is-none'}>
          {s.usage?.used_pct != null && <Meter pct={s.usage.used_pct} over={s.usage.used_pct >= 100} />}
          {words ?? 'No use yet'}
        </span>
      )}
      <span />
    </Link>
  );
  if (!open) return row;
  return (
    <div className="open">
      {row}
      <Facts label={s.name}>
        <LabelValue label="Kind">{s.label}</LabelValue>
        <LabelValue label="Held by">{s.owner_name || s.added_by_name}</LabelValue>
        {s.status_detail && <LabelValue label="State">{s.status_detail}</LabelValue>}
        {agents.length > 0 && (
          <Line label="Agents on it">
            {agents.map((a, i) => (
              <span key={a.id}>
                {i > 0 && ', '}
                {a.name}
                {a.fallback && <Pill>Fallback</Pill>}
              </span>
            ))}
          </Line>
        )}
      </Facts>
      <Foot open>
        {admin && isPlanKind(s.kind) && <Verb onClick={() => setReconnecting(true)}>Reconnect</Verb>}
        <Verb onClick={() => setEditing(true)}>Edit</Verb>
        <Verb onClick={() => void act('settings.refreshUsage', { subscription: s.id })}>Check use</Verb>
        {s.can_remove !== false && <Verb onClick={() => setConfirm(true)}>Remove</Verb>}
      </Foot>
      {reconnecting && admin && isPlanKind(s.kind) && <SignInDialog kind={s.kind} subscription={s.id} onClose={() => setReconnecting(false)} />}
      {editing && <EditSubDialog s={s} owned={owned} onClose={() => setEditing(false)} />}
      <Dialog
        open={confirm}
        onOpenChange={setConfirm}
        alert
        title={`Remove ${s.name}?`}
        description="Agents on it stop, and their sessions start fresh on what is left."
        actions={
          <>
            <Button variant="quiet" onClick={() => setConfirm(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                setConfirm(false);
                void act('settings.removeSubscription', { subscription: s.id });
              }}
            >
              Remove
            </Button>
          </>
        }
      />
    </div>
  );
}

type Adding = { sign: SignInKind } | { key: string; label: string } | null;

// A plan's kind, as its chip names it; anything else is a key.
const PLAN: Partial<Record<Subscription['kind'], string>> = { claude_login: 'Claude', claude_token: 'Claude', codex_login: 'ChatGPT' };
const isPlanKind = (kind: string): kind is SignInKind => (SIGN_IN_KINDS as readonly string[]).includes(kind);

/** Folded, the section is one line: the fix for an expired sign-in or a plan past due, a quiet chip for each kind, then
    Add. */
function Chips({ list }: { list: Subscription[] }) {
  const bad = list.find((s) => s.status === 'needs_reconnect') ?? list.find((s) => s.problem);
  const kinds = [...new Set(list.flatMap((s) => (PLAN[s.kind] && s !== bad ? [PLAN[s.kind] as string] : [])))];
  const keys = list.filter((s) => !PLAN[s.kind]).length;
  const chip = (label: string, n = 1) => (
    <span key={label} className="pill fs-chip">
      {label}
      {n > 1 && <b>{n}</b>}
    </span>
  );
  return (
    <>
      {bad && (
        <span className="fs-bad">
          <span className="pill pill-flag fs-chip" title={bad.label}>
            <Icon name="alert" />
            {bad.status === 'needs_reconnect' ? `${bad.name} expired` : `${bad.name}: ${bad.problem}`}
          </span>
        </span>
      )}
      <span className="fs-kinds">
        {kinds.map((k) => chip(k, list.filter((s) => PLAN[s.kind] === k && s !== bad).length))}
        {keys > 0 && chip(keys === 1 ? '1 key' : `${keys} keys`)}
      </span>
    </>
  );
}

function Subs({ list, open, owned, admin, add }: { list: Subscription[]; open?: string; owned: Set<string>; admin: boolean; add: ReactNode }) {
  const [shown, setShown] = useState(() => list.some((s) => s.id === open));
  return (
    <Section
      name="Subscriptions and keys"
      count={list.length}
      open={shown}
      onOpenChange={setShown}
      actions={
        <>
          {!shown && <Chips list={list} />}
          {add}
        </>
      }
      target="setting:subscriptions"
    >
      {list.length === 0 ? (
        <Empty title="Nothing connected" />
      ) : (
        <>
          <div className="tbl-head g-fsub" aria-hidden="true">
            <span>Name</span>
            <span>On</span>
            <span>Agents</span>
            <span>Used</span>
            <span />
          </div>
          {list.map((s) => (
            <SubRow key={s.id} s={s} open={open === s.id} owned={owned.has(s.kind)} admin={admin} />
          ))}
        </>
      )}
    </Section>
  );
}

export function Agents({ open }: { open?: string }) {
  const subs = useQuery(subscriptionsQuery);
  const admin = useAdmin();
  const [adding, setAdding] = useState<Adding>(null);
  const v = subs.data;
  if (!v) return null;
  const list = v.subscriptions as Subscription[];
  const signIns = v.kinds.filter((k): k is typeof k & { kind: SignInKind } => k.connect && isPlanKind(k.kind));
  const keys = v.kinds.filter((k) => !k.connect);
  const owned = new Set<string>(v.kinds.filter((k) => k.owned).map((k) => k.kind));
  const add = admin && (
    <Menu trigger={<Button icon="plus">Add</Button>}>
      {signIns.map((k) => (
        <MenuItem key={k.kind} label={k.label} meta="Sign in" onSelect={() => setAdding({ sign: k.kind })} />
      ))}
      {signIns.length > 0 && keys.length > 0 && <MenuRule />}
      {keys.map((k) => (
        <MenuItem key={k.kind} label={k.label.replace(/ API key$/, ' key')} meta="Paste" onSelect={() => setAdding({ key: k.kind, label: k.secret_label || k.label })} />
      ))}
    </Menu>
  );
  return (
    <>
      <Subs list={list} open={open} owned={owned} admin={admin} add={add} />
      <AgentList open={open} />
      {admin && adding && 'sign' in adding && <SignInDialog kind={adding.sign} onClose={() => setAdding(null)} />}
      {admin && adding && 'key' in adding && <KeyDialog kind={adding.key} label={adding.label} onClose={() => setAdding(null)} />}
    </>
  );
}

// ---------------------------------------------------------------- Computers

const KINDS = [
  { value: 'server', label: 'Server' },
  { value: 'mac', label: 'Mac' },
  { value: 'windows', label: 'Windows' },
] as const;

function CommandLine({ c }: { c: Command }) {
  return (
    <div className="set-cmd">
      <span className="set-cmd-note">{c.note}</span>
      <div className="set-cmd-row">
        <code className="fs-code">{c.line}</code>
        <IconButton
          icon="copy"
          label="Copy"
          size="sm"
          quiet
          onClick={() => navigator.clipboard?.writeText(c.line).then(() => toast({ text: 'Copied', kind: 'done' }))}
        />
      </div>
    </div>
  );
}

export function AddComputer() {
  const config = useQuery(configQuery);
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<ComputerKind>('server');
  const [label, setLabel] = useState('');
  const [code, setCode] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const close = () => {
    setOpen(false);
    setCode(null);
    setLabel('');
  };
  const start = async () => {
    setBusy(true);
    const r = await run('settings.addComputer', {}, { via: 'click' });
    setBusy(false);
    if (!r.ok) return toast(refusal(r, "Couldn't make a code"));
    setCode((r.out as { code?: string } | undefined)?.code ?? null);
  };
  const [main, ...more] = code
    ? installCommands((config.data ?? {}) as RunnerConfig, location.origin, kind, code, label.trim())
    : [];
  return (
    <>
      <Button variant="primary" onClick={() => setOpen(true)}>
        Add computer
      </Button>
      <Dialog
        open={open}
        onOpenChange={(o) => !o && close()}
        title="Add a computer"
        description={code ? 'The code works once, for 15 minutes, and is not shown again.' : undefined}
        actions={
          code ? (
            <Button variant="primary" onClick={close}>
              Done
            </Button>
          ) : (
            <>
              <Button variant="quiet" onClick={close}>
                Cancel
              </Button>
              <Button variant="primary" busy={busy} disabled={!label.trim()} onClick={() => start()}>
                Get code
              </Button>
            </>
          )
        }
      >
        {main ? (
          <div className="set-add">
            <CommandLine c={main} />
            {more.length > 0 && (
              <details className="set-more">
                <summary>Other ways</summary>
                {more.map((c) => (
                  <CommandLine key={c.line} c={c} />
                ))}
              </details>
            )}
          </div>
        ) : (
          <div className="set-add">
            <Segmented label="Kind" options={KINDS} value={kind} onValueChange={setKind} />
            <Field label="Name" value={label} onValueChange={setLabel} autoComplete="off" />
          </div>
        )}
      </Dialog>
    </>
  );
}

/** Owners and admins change company-wide settings; a member's read of the roster is refused (403). */
function useAdmin(): boolean {
  const me = useQuery(meQuery);
  const access = useQuery({ ...accessQuery, retry: false });
  return me.data?.role === 'owner' || access.data?.you === 'owner' || access.data?.you === 'admin';
}

const HOSTED_KINDS: Record<string, string> = { tico: 'Tico server', bot_computer: 'Agent computer' };

function Hosted() {
  const hosted = useQuery({ ...hostedQuery, retry: false });
  const admin = useAdmin();
  const [said, setSaid] = useState<Record<string, string>>({});
  // A start refused for want of credit: where to ask Tico support for more (old settings-hosted.ts).
  const [credit, setCredit] = useState<Record<string, string>>({});
  const v = hosted.data;
  if (v?.state === 'deactivated')
    return (
      <Section name="Hosted servers" target="setting:hosted">
        <Empty title="Hosting has ended" />
      </Section>
    );
  if (v?.state !== 'ok' || !v.servers?.length) return null;
  const start = async (id: string) => {
    const r = await run('settings.startServer', { server: id }, { via: 'click' });
    const out = r.out as { state?: string; message?: string; credit_url?: string } | undefined;
    if (!r.ok) return toast(refusal(r, "Couldn't start"));
    if (out?.state !== 'started') setSaid((s) => ({ ...s, [id]: out?.message ?? 'Not started' }));
    const url = out?.state === 'paused' && /^(https:\/\/|mailto:)\S+$/.test(out.credit_url ?? '') ? (out.credit_url ?? '') : '';
    setCredit((c) => ({ ...c, [id]: url }));
  };
  return (
    <Section name="Hosted servers" count={v.servers.length} target="setting:hosted">
      {v.servers.map((s) => (
        <div key={s.server_id} className="row g-host" data-target={`hosted:${s.server_id}`} title={said[s.server_id]}>
          <span className="row-title">{HOSTED_KINDS[s.kind] ?? s.kind}</span>
          <span className="row-state">
            <Word fine={s.power === 'on'}>{s.power === 'on' ? 'On' : (said[s.server_id] ?? 'Stopped')}</Word>
          </span>
          <span>
            {credit[s.server_id] ? (
              <a className="verb" href={credit[s.server_id]} target="_blank" rel="noreferrer" title="Contact Tico support for more credit">
                Tico support
              </a>
            ) : (
              admin && s.power !== 'on' && <Verb onClick={() => void start(s.server_id)}>Start</Verb>
            )}
          </span>
        </div>
      ))}
    </Section>
  );
}

const SLEEP_HOURS = [0, 1, 2, 3, 4, 6, 8, 12, 24];
const AWAKE: Record<string, string> = {
  run_queued: 'A run is waiting',
  run_in_progress: 'A run is going',
  task_session: 'A task is waiting',
  runner_job: 'A computer job is running',
  send_pending: 'A send is waiting',
  routine_due: 'A routine is due soon',
};

function Sleep() {
  const sleep = useQuery({ ...sleepQuery, retry: false });
  const admin = useAdmin();
  const v = sleep.data;
  if (!v?.hosted || v.managed) return null;
  const now = v.hours ?? 0;
  const hours = SLEEP_HOURS.includes(now) ? SLEEP_HOURS : [...SLEEP_HOURS, now].sort((a, b) => a - b);
  return (
    <Section name="When idle" target="setting:sleep">
      <Facts label="When idle">
        <Line label="Sleep when idle">
          <Select
            label="Sleep when idle"
            labelHidden
            look="quiet"
            disabled={!admin}
            value={String(now)}
            options={hours.map((h) => ({ value: String(h), label: h ? `After ${h} ${h === 1 ? 'hour' : 'hours'}` : 'Off' }))}
            onValueChange={(h) => void act('settings.setSleep', { hours: Number(h) })}
          />
        </Line>
        {now > 0 && (
          <SwitchLine
            label="Agents' computers sleep too"
            checked={v.runners}
            disabled={!admin}
            onChange={(on) => void act('settings.sleepComputers', { on })}
          />
        )}
        {now > 0 && v.blocker && <Line label="Awake">{AWAKE[v.blocker] ?? v.blocker}</Line>}
      </Facts>
    </Section>
  );
}

/** One folding section per computer: its state (it opens the computer's panel), its agents and their models. Add
    computer is the page's filled button. */
export function Computers({ open }: { open?: string }) {
  const computers = useQuery(computersQuery);
  if (!computers.data) return null;
  // An old address names the computer alone (?open=<id>); the list opens it as computer:<id>.
  const at = open && computers.data.some((c) => c.id === open) ? `computer:${open}` : open;
  return (
    <>
      <AgentList open={at} section="computers" />
      {/* Owner, Nov 19: agents made and run outside Tico are not in the first build; one dimmed line. */}
      <Section name="External agents" target="setting:external">
        <div className="row is-off" aria-disabled="true" title="Agents made and run outside Tico">
          <span className="row-title">
            Agents made and run outside Tico
            <Pill>Coming soon</Pill>
          </span>
        </div>
      </Section>
      <Hosted />
      <Sleep />
    </>
  );
}

// ---------------------------------------------------------------- Models

function Defaults({ names }: { names: { value: string; label: string }[] }) {
  const providers = useQuery({ ...providersQuery, retry: false });
  const me = useQuery(meQuery);
  const v = providers.data;
  if (!v) return null;
  // The owner chooses; everyone else sees the choice.
  const owner = me.data?.role === 'owner';
  const model = v.default.model ?? '';
  const meeting = v.meeting_model ?? '';
  const known = (id: string) => (id && !names.some((n) => n.value === id) ? [{ value: id, label: id }] : []);
  return (
    <Section name="Defaults" target="setting:defaults">
      <Facts label="Defaults">
        <Line label="A new agent uses" target="setting:default-model">
          <Select
            label="A new agent uses"
            labelHidden
            look="quiet"
            disabled={!owner}
            value={model || null}
            placeholder="None"
            options={[...known(model), ...names]}
            onValueChange={(m) => void act('settings.defaultModel', { model: m })}
          />
        </Line>
        <Line label="Meetings" target="setting:meeting-model">
          <Select
            label="Meetings"
            labelHidden
            look="quiet"
            disabled={!owner}
            value={meeting}
            options={[{ value: '', label: "Agent's own model" }, ...known(meeting), ...names]}
            onValueChange={(m) => void act('settings.meetingModel', { model: m })}
          />
        </Line>
      </Facts>
    </Section>
  );
}

/** What happens when a plan runs out: each computer may fall back to Tico Gateway, the Gateway's key (owner), and the
    limits that cap it. */
function WhenOut() {
  const computers = useQuery(computersQuery);
  const live = (computers.data ?? []).filter((c) => !c.revoked_at);
  return (
    <Section name="When a plan runs out" target="setting:fallback">
      <Facts label="When a plan runs out">
        {live.map((c) => (
          <SwitchLine
            key={c.id}
            label={`${c.label || c.id}: use Tico Gateway`}
            target={`computer:${c.id}`}
            checked={c.gateway_fallback === true}
            onChange={(on) => void act('settings.gatewayFallback', { computer: c.id, on })}
          />
        ))}
        <GatewayLines />
        <Line label="Gateway limit">
          <Link className="verb" to="/company/usage" search={{ view: 'limits' }}>
            Limits
          </Link>
        </Line>
      </Facts>
    </Section>
  );
}

export function Models() {
  const models = useQuery(modelsQuery);
  const v = models.data;
  if (!v) return null;
  const on = v.enabled_providers;
  const shown = v.models.filter((m) => !m.deprecated && (!on || on.includes(m.provider)));
  return (
    <>
      {shown.length === 0 ? <Empty title="No models yet" /> : <Defaults names={shown.map((m) => ({ value: m.id, label: modelName(m.label) }))} />}
      <WhenOut />
    </>
  );
}
