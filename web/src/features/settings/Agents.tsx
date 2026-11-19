import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import { run } from '@/actions/run';
import { directoryQuery } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { toast } from '@/kit/Toast';
import { Section } from '@/pagekinds';
import { take } from './handover';
import { Foot, Line, Meter, SwitchLine, Verb, Word, act, usd } from './parts';
import {
  LIVE_SIGN_IN,
  computersQuery,
  limitsQuery,
  modelsQuery,
  signInQuery,
  subscriptionsQuery,
  type Computer,
  type SignIn,
  type Subscription,
} from './queries';

/* Agents (what pays for them, then the agents), Computers and Models. */

// ---------------------------------------------------------------- a sign-in to Claude or Codex, one tap

function SignInDialog({ kind, onClose }: { kind: 'claude_login' | 'codex_login'; onClose: () => void }) {
  const qc = useQueryClient();
  const [login, setLogin] = useState<SignIn | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [code, setCode] = useState('');
  const [tries, setTries] = useState(0);
  // Start, and while its computer wakes (no id yet), ask again every 3 s.
  useEffect(() => {
    let gone = false;
    const timer = setTimeout(
      () => {
        void run('settings.connect', { kind }, { via: 'click' }).then((r) => {
          if (gone) return;
          if (!r.ok) return setError(r.error ?? "Couldn't start");
          const got = take<SignIn | null>('sign-in') ?? null;
          if (!got) return setError('No computer can sign in yet');
          if (got.id) setLogin(got);
          else if (got.waking) setTries((n) => n + 1);
          else setError(got.message ?? 'No computer can sign in yet');
        });
      },
      tries === 0 ? 0 : 3000,
    );
    return () => {
      gone = true;
      clearTimeout(timer);
    };
  }, [kind, tries]);
  const follow = useQuery({ ...signInQuery(login?.runner_id ?? '', login?.id ?? ''), enabled: !!login?.id });
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
  const ended = error ?? (state === 'failed' ? (now.message ?? 'The sign-in did not finish') : state === 'expired' ? 'The sign-in ran out of time' : null);
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

function SubRow({ s, open }: { s: Subscription; open: boolean }) {
  const [confirm, setConfirm] = useState(false);
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
        <Verb onClick={() => void act('settings.refreshUsage', { subscription: s.id })}>Check use</Verb>
        {s.can_remove !== false && <Verb onClick={() => setConfirm(true)}>Remove</Verb>}
      </Foot>
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
  const limits = useQuery(limitsQuery);
  const dir = useQuery(directoryQuery);
  const [adding, setAdding] = useState<Adding>(null);
  const v = subs.data;
  if (!v) return null;
  const list = v.subscriptions as Subscription[];
  const signIns = v.kinds.filter((k) => k.kind === 'claude_login' || k.kind === 'codex_login');
  const keys = v.kinds.filter((k) => !k.connect && k.kind !== 'claude_login' && k.kind !== 'codex_login');
  const agents = (dir.data?.actors ?? []).filter((a) => a.kind === 'agent');
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
          list.map((s) => <SubRow key={s.id} s={s} open={open === s.id} />)
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
      <Section name="Agents" count={agents.length} target="setting:agents">
        {agents.length === 0 ? (
          <Empty title="No agents yet" />
        ) : (
          <>
            <div className="tbl-head g-bot" aria-hidden="true">
              <span />
              <span>Agent</span>
              <span>Team</span>
              <span>This month</span>
              <span className="is-num">Limit</span>
            </div>
            {agents.map((a) => {
              const l = a.slug ? limits.data?.bots[a.slug] : undefined;
              return (
                <div key={a.id} className={l?.blocked ? 'row g-bot is-bad' : 'row g-bot'} title={a.name} data-target={`agent:${a.slug ?? a.id}`}>
                  <Avatar kind="agent" name={a.name} hue={a.hue} decorative />
                  <span className="row-title">{a.name}</span>
                  <span className="row-cell">{a.team ?? ''}</span>
                  <span className="row-cell">{l ? <Meter pct={l.percent} over={!!l.blocked} /> : null}</span>
                  <span className="row-fig">{l?.monthly_usd != null ? usd(l.monthly_usd) : 'None'}</span>
                </div>
              );
            })}
          </>
        )}
      </Section>
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

function AddComputer() {
  const [code, setCode] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const start = async () => {
    setBusy(true);
    const ok = await act('settings.addComputer', {});
    setBusy(false);
    if (ok) setCode(take<string>('enroll') ?? null);
  };
  return (
    <>
      <Button icon="plus" busy={busy} onClick={() => void start()}>
        Add computer
      </Button>
      <Dialog
        open={code != null}
        onOpenChange={(o) => !o && setCode(null)}
        title="Add a computer"
        description="Run the Tico installer on the computer and give it this code. It works once, and is not shown again."
        actions={
          <Button
            variant="primary"
            icon="copy"
            onClick={() => {
              if (code) void navigator.clipboard?.writeText(code);
              setCode(null);
            }}
          >
            Copy
          </Button>
        }
      >
        <code className="fs-code">{code}</code>
      </Dialog>
    </>
  );
}

export function Computers({ open }: { open?: string }) {
  const computers = useQuery(computersQuery);
  const list = (computers.data ?? []).filter((c) => !c.revoked_at);
  if (!computers.data) return null;
  return (
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
  );
}

// ---------------------------------------------------------------- Models

export function Models() {
  const models = useQuery(modelsQuery);
  const v = models.data;
  if (!v) return null;
  const on = v.enabled_providers;
  const shown = v.models.filter((m) => !m.deprecated && (!on || on.includes(m.provider)));
  const providers = [...new Set(shown.map((m) => m.provider))];
  const fallbackName = v.models.find((m) => m.id === v.default?.model)?.label;
  if (shown.length === 0) return <Empty title="No models yet" text="Connect a subscription or a key under Agents." />;
  return (
    <>
      {fallbackName && (
        <Facts label="Models">
          <LabelValue label="A new agent uses">{fallbackName}</LabelValue>
        </Facts>
      )}
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
