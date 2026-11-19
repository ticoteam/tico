import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import { run } from '@/actions/run';
import { configQuery, meQuery } from '@/data';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { Section } from '@/pagekinds';
import { AgentList } from './AgentList';
import { installCommands, type Command, type ComputerKind, type RunnerConfig } from './install';
import { Foot, Line, Meter, SwitchLine, Verb, Word, act, usd, useVisible } from './parts';
import {
  LIVE_SIGN_IN,
  accessQuery,
  computersQuery,
  hostedQuery,
  modelsQuery,
  providersQuery,
  sleepQuery,
  signInQuery,
  subscriptionsQuery,
  type Computer,
  type SignIn,
  type Subscription,
} from './queries';

/* Agents (what pays for them, then the agents), Computers and Models. */

const RETRY_MS = 3000;
const GIVE_UP_MS = 5 * 60_000;

// ---------------------------------------------------------------- a sign-in to Claude or Codex, one tap

function SignInDialog({ kind, onClose }: { kind: 'claude_login' | 'codex_login'; onClose: () => void }) {
  const qc = useQueryClient();
  const [login, setLogin] = useState<SignIn | null>(null);
  const [error, setError] = useState<string | null>(null);
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
    if (late || !visible || login || error) return;
    let gone = false;
    const timer = setTimeout(
      () => {
        void run('settings.connect', { kind }, { via: 'click' }).then((r) => {
          if (gone) return;
          if (!r.ok) return setError(r.error ?? "Couldn't start");
          const got = (r.out as SignIn | null | undefined) ?? null;
          if (!got) return setError('No computer can sign in yet');
          if (got.id) setLogin(got);
          else if (got.waking) setTries((n) => n + 1);
          else setError(got.message ?? 'No computer can sign in yet');
        });
      },
      tries === 0 ? 0 : RETRY_MS,
    );
    return () => {
      gone = true;
      clearTimeout(timer);
    };
  }, [kind, tries, late, visible, login, error]);
  // A hidden tab pauses the follow too: Query does not poll in the background.
  const follow = useQuery({ ...signInQuery(login?.runner_id ?? '', login?.id ?? ''), enabled: !!login?.id && !late });
  const now = { ...login, ...follow.data } as SignIn;
  const state = now.state ?? (login ? 'starting' : 'requested');
  const finished = useRef(false);
  useEffect(() => {
    if (state !== 'done' || finished.current) return;
    finished.current = true;
    void qc.invalidateQueries({ queryKey: subscriptionsQuery.queryKey });
    toast({ text: `${now.name ?? 'Subscription'} connected`, kind: 'done' });
    onClose();
  }, [state, now.name, qc, onClose]);
  const stop = () => {
    if (login?.id && login.runner_id && LIVE_SIGN_IN.includes(state)) void run('settings.cancelSignIn', { computer: login.runner_id, login: login.id }, { via: 'click' });
    onClose();
  };
  const ended =
    error ??
    (state === 'failed'
      ? (now.message ?? 'The sign-in did not finish')
      : state === 'expired' || (late && state !== 'done')
        ? 'The sign-in ran out of time'
        : null);
  const send = () => {
    if (!login?.id || !login.runner_id) return;
    void act('settings.signInCode', { computer: login.runner_id, login: login.id, code: code.trim() });
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && stop()}
      title={kind === 'codex_login' ? 'Connect Codex' : 'Connect Claude'}
      description={
        ended ??
        (state === 'waiting' && now.url
          ? 'Approve it on the sign-in page. It lands here when you do.'
          : `Starting the sign-in${now.computer ? ` on ${now.computer}` : ''}`)
      }
      actions={
        <>
          <Button variant="quiet" onClick={stop}>
            {ended ? 'Close' : 'Cancel'}
          </Button>
          {!ended && now.url && (
            <a className="btn btn-fill" href={now.url} target="_blank" rel="noreferrer">
              Open page
            </a>
          )}
        </>
      }
    >
      {!ended && now.accepts_code && (
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
          <Button variant="primary" busy={busy} disabled={secret.trim().length < 8} onClick={() => void save()}>
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
  if (s.status === 'needs_reconnect') return null;
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
    const ok = await act('settings.editSubscription', { subscription: s.id, name: name.trim(), ...(owned && owner !== was ? { owner } : {}) });
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
          <Button variant="primary" busy={busy} disabled={!name.trim() || name.trim().length > 80} onClick={() => void save()}>
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

function SubRow({ s, open, owned }: { s: Subscription; open: boolean; owned: boolean }) {
  const [confirm, setConfirm] = useState(false);
  const [editing, setEditing] = useState(false);
  const words = usedWords(s);
  const agents = s.agents ?? [];
  const out = s.status === 'needs_reconnect';
  const row = (
    <Link
      to="/settings/$section"
      params={{ section: 'agents' }}
      search={open ? {} : { open: s.id }}
      className={out ? 'row g-fsub is-bad' : 'row g-fsub'}
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
      {out ? (
        <span className="fs-use is-out">
          <Word>Sign in again</Word>
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
        <Verb onClick={() => setEditing(true)}>Edit</Verb>
        <Verb onClick={() => void act('settings.refreshUsage', { subscription: s.id })}>Check use</Verb>
        {s.can_remove !== false && <Verb onClick={() => setConfirm(true)}>Remove</Verb>}
      </Foot>
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

type Adding = { sign: 'claude_login' | 'codex_login' } | { key: string; label: string } | null;

export function Agents({ open }: { open?: string }) {
  const subs = useQuery(subscriptionsQuery);
  const computers = useQuery(computersQuery);
  const [adding, setAdding] = useState<Adding>(null);
  const v = subs.data;
  if (!v) return null;
  const list = v.subscriptions as Subscription[];
  const signIns = v.kinds.filter((k) => k.kind === 'claude_login' || k.kind === 'codex_login');
  const keys = v.kinds.filter((k) => !k.connect && k.kind !== 'claude_login' && k.kind !== 'codex_login');
  const owned = new Set<string>(v.kinds.filter((k) => k.owned).map((k) => k.kind));
  const live = (computers.data ?? []).filter((c) => !c.revoked_at);
  const add = (
    <Menu trigger={<Button icon="plus">Add</Button>}>
      {signIns.map((k) => (
        <MenuItem key={k.kind} label={k.kind === 'codex_login' ? 'Codex' : 'Claude'} meta="Sign in" onSelect={() => setAdding({ sign: k.kind as 'claude_login' | 'codex_login' })} />
      ))}
      {signIns.length > 0 && keys.length > 0 && <MenuRule />}
      {keys.map((k) => (
        <MenuItem key={k.kind} label={k.label} meta="Key" onSelect={() => setAdding({ key: k.kind, label: k.secret_label || k.label })} />
      ))}
    </Menu>
  );
  return (
    <>
      <Section name="Subscriptions and keys" count={list.length} actions={add} target="setting:subscriptions">
        {list.length === 0 ? (
          <Empty title="Nothing connected" text="Agents run on a Claude or Codex plan, or on a key." />
        ) : (
          list.map((s) => <SubRow key={s.id} s={s} open={open === s.id} owned={owned.has(s.kind)} />)
        )}
      </Section>
      {live.length > 0 && (
        <Section name="When a plan runs out" target="setting:fallback">
          <Facts label="When a plan runs out">
            {live.map((c) => (
              <SwitchLine
                key={c.id}
                label={`${c.label || c.id}: fall back to Tico Gateway`}
                target={`computer:${c.id}`}
                checked={c.gateway_fallback === true}
                onChange={(on) => void act('settings.gatewayFallback', { computer: c.id, on })}
              />
            ))}
            <Line label="Limits">
              <Link className="verb" to="/company/usage" search={{ view: 'limits' }}>
                Open limits
              </Link>
            </Line>
          </Facts>
        </Section>
      )}
      <AgentList open={open} />
      {adding && 'sign' in adding && <SignInDialog kind={adding.sign} onClose={() => setAdding(null)} />}
      {adding && 'key' in adding && <KeyDialog kind={adding.key} label={adding.label} onClose={() => setAdding(null)} />}
    </>
  );
}

// ---------------------------------------------------------------- Computers

function ComputerOpen({ c }: { c: Computer }) {
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(c.label ?? '');
  const [confirm, setConfirm] = useState(false);
  const bots = Object.keys(c.readiness?.bots ?? {});
  return (
    <>
      <Facts label={c.label || c.id}>
        <LabelValue label="State">{c.online ? 'Online' : 'Offline'}</LabelValue>
        <LabelValue label="Kind">{c.cloud_machine ? (c.cloud_machine.shared ? 'Shared, in the cloud' : 'In the cloud') : 'Your own'}</LabelValue>
        <LabelValue label="Agents">{bots.length}</LabelValue>
        <SwitchLine
          label="Fall back to Tico Gateway"
          checked={c.gateway_fallback === true}
          onChange={(on) => void act('settings.gatewayFallback', { computer: c.id, on })}
        />
      </Facts>
      <Foot open>
        <Verb onClick={() => setRenaming(true)}>Rename</Verb>
        <Verb onClick={() => setConfirm(true)}>Remove</Verb>
      </Foot>
      <Dialog
        open={renaming}
        onOpenChange={setRenaming}
        title="Rename computer"
        actions={
          <>
            <Button variant="quiet" onClick={() => setRenaming(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={!name.trim()}
              onClick={() => {
                setRenaming(false);
                void act('settings.nameComputer', { computer: c.id, name: name.trim() });
              }}
            >
              Rename
            </Button>
          </>
        }
      >
        <Field label="Name" value={name} onValueChange={setName} autoFocus />
      </Dialog>
      <Dialog
        open={confirm}
        onOpenChange={setConfirm}
        alert
        title={`Remove ${c.label || 'this computer'}?`}
        description="It stops taking work at once. Its agents wait until they have another computer."
        actions={
          <>
            <Button variant="quiet" onClick={() => setConfirm(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                setConfirm(false);
                void act('settings.removeComputer', { computer: c.id });
              }}
            >
              Remove
            </Button>
          </>
        }
      />
    </>
  );
}

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
          onClick={() => void navigator.clipboard?.writeText(c.line).then(() => toast({ text: 'Copied', kind: 'done' }))}
        />
      </div>
    </div>
  );
}

function AddComputer() {
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
    if (!r.ok) return toast({ text: "Couldn't make a code", kind: 'failed' });
    setCode((r.out as { code?: string } | undefined)?.code ?? null);
  };
  const [main, ...more] = code
    ? installCommands((config.data ?? {}) as RunnerConfig, location.origin, kind, code, label.trim())
    : [];
  return (
    <>
      <Button icon="plus" onClick={() => setOpen(true)}>
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
              <Button variant="primary" busy={busy} disabled={!label.trim()} onClick={() => void start()}>
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
  const v = hosted.data;
  if (v?.state === 'deactivated')
    return (
      <Section name="Hosted servers" target="setting:hosted">
        <Empty title="Hosting has ended" text="Ask Tico support to start it again." />
      </Section>
    );
  if (v?.state !== 'ok' || !v.servers?.length) return null;
  const start = async (id: string) => {
    const r = await run('settings.startServer', { server: id }, { via: 'click' });
    const out = r.out as { state?: string; message?: string } | undefined;
    if (!r.ok) return toast({ text: "Couldn't start", kind: 'failed' });
    if (out?.state !== 'started') setSaid((s) => ({ ...s, [id]: out?.message ?? 'Not started' }));
  };
  return (
    <Section name="Hosted servers" count={v.servers.length} target="setting:hosted">
      {v.servers.map((s) => (
        <div key={s.server_id} className="row g-host" data-target={`hosted:${s.server_id}`} title={said[s.server_id]}>
          <span className="row-title">{HOSTED_KINDS[s.kind] ?? s.kind}</span>
          <span className="row-state">
            <Word fine={s.power === 'on'}>{s.power === 'on' ? 'On' : (said[s.server_id] ?? 'Stopped')}</Word>
          </span>
          <span>{admin && s.power !== 'on' && <Verb onClick={() => void start(s.server_id)}>Start</Verb>}</span>
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

export function Computers({ open }: { open?: string }) {
  const computers = useQuery(computersQuery);
  const list = (computers.data ?? []).filter((c) => !c.revoked_at);
  if (!computers.data) return null;
  return (
    <>
      <Section name="Computers" count={list.length} actions={<AddComputer />} target="setting:computers">
        {list.length === 0 ? (
          <Empty icon="computer" title="No computers yet" text="Agents run on a computer: yours, or one in the cloud." />
        ) : (
          <>
            <div className="tbl-head g-comp" aria-hidden="true">
              <span>Computer</span>
              <span>Kind</span>
              <span>Agents</span>
              <span>State</span>
            </div>
            {list.map((c) => {
              const row = (
                <Link
                  key={c.id}
                  to="/settings/$section"
                  params={{ section: 'computers' }}
                  search={open === c.id ? {} : { open: c.id }}
                  className={c.online ? 'row g-comp' : 'row g-comp is-bad'}
                  data-target={`computer:${c.id}`}
                  aria-expanded={open === c.id}
                  title={c.label || c.id}
                >
                  <span className="row-title">{c.label || c.id}</span>
                  <span className="row-cell">{c.cloud_machine?.shared ? 'Shared' : c.cloud_machine ? 'Cloud' : 'Own'}</span>
                  <span className="row-cell">{Object.keys(c.readiness?.bots ?? {}).length}</span>
                  <span className="row-state">
                    <Word fine={c.online}>{c.online ? 'Online' : 'Offline'}</Word>
                  </span>
                </Link>
              );
              return open === c.id ? (
                <div key={c.id} className="open">
                  {row}
                  <ComputerOpen c={c} />
                </div>
              ) : (
                row
              );
            })}
          </>
        )}
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

export function Models() {
  const models = useQuery(modelsQuery);
  const v = models.data;
  if (!v) return null;
  const on = v.enabled_providers;
  const shown = v.models.filter((m) => !m.deprecated && (!on || on.includes(m.provider)));
  const providers = [...new Set(shown.map((m) => m.provider))];
  if (shown.length === 0) return <Empty title="No models yet" text="Connect a subscription or a key under Agents." />;
  return (
    <>
      <Defaults names={shown.map((m) => ({ value: m.id, label: m.label }))} />
      {providers.map((p) => {
        const rows = shown.filter((m) => m.provider === p);
        return (
          <Section key={p} name={p.charAt(0).toUpperCase() + p.slice(1)} count={rows.length}>
            {rows.map((m) => (
              <div key={m.id} className="row g-model" title={m.label}>
                <span className="row-title">{m.label}</span>
                <span className="row-cell">{m.runtime ?? ''}</span>
                <span className="row-fig">{m.context_tokens ? `${Math.round(m.context_tokens / 1000)}K` : ''}</span>
              </div>
            ))}
          </Section>
        );
      })}
    </>
  );
}
