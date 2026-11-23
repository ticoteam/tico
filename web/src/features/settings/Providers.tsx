import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useId, useState } from 'react';
import { reasonIn, run } from '@/actions';
import type { PlanExit } from '@/api/schema';
import { meQuery } from '@/data';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Facts } from '@/kit/LabelValue';
import { Menu, MenuItem, MenuRule, MenuSub } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { SectionHead } from '@/kit/SectionHead';
import { Select } from '@/kit/Select';
import { fmtDate } from '@/lib';
import { Section } from '@/pagekinds';
import { editSubscriptionName, maxLiveTurns } from './actions';
import { ConnectPlan } from './ConnectPlan';
import { GatewayLines } from './Gateway';
import { Line, Meter, act, agentsWord, usd, useAdmin } from './parts';
import { limitReset, needsReconnect, providerBadge } from './providerStatus';
import { accessQuery, planExitQuery, SIGN_IN_KINDS, subscriptionsQuery, type SignInKind, type Subscription } from './queries';

const isPlan = (kind: string): kind is SignInKind => (SIGN_IN_KINDS as readonly string[]).includes(kind);

type Owner = { id: string; name: string };
type Adding = ({ sign: SignInKind } | { key: string; label: string }) & { owner?: Owner };
type Change = 'reconnect' | 'replace' | 'rename' | 'remove';

function KeyDialog({ kind, label, subscription, owner, onClose }: { kind: string; label: string; subscription?: Subscription; owner?: Owner; onClose: () => void }) {
  const [secret, setSecret] = useState('');
  const [restart, setRestart] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async (confirm = false) => {
    setBusy(true);
    try {
      if (!subscription) {
        if (await act('settings.addKey', { kind, secret: secret.trim(), ...(owner && { owner: owner.id }) })) onClose();
        return;
      }
      const result = await run('settings.pastePlan', { subscription: subscription.id, secret: secret.trim(), ...(confirm && { confirm: true }) }, { via: 'click' });
      if (result.ok) return onClose();
      if (result.error === 'confirm_restart' && !confirm) {
        setRestart(reasonIn(result) ?? 'Running work will restart');
      } else {
        setRestart(null);
        setError(reasonIn(result) ?? "Couldn't replace the key");
      }
    } finally {
      setBusy(false);
    }
  };
  const verb = subscription ? 'Replace key' : 'Add';
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !busy && onClose()}
      title={restart ? 'Restart runs?' : subscription ? `Replace key for ${subscription.name}` : `Add ${label}${owner ? ` for ${owner.name}` : ''}`}
      alert={!!restart}
      description={restart ?? error ?? undefined}
      actions={
        <>
          <Button variant="quiet" disabled={busy} onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} disabled={secret.trim().length < 8} onClick={() => save(!!restart)}>{verb}</Button>
        </>
      }
    >
      {!restart && <Field label={label} type="password" value={secret} onValueChange={setSecret} disabled={busy} autoComplete="off" autoFocus />}
    </Dialog>
  );
}

function RenameDialog({ s, onClose }: { s: Subscription; onClose: () => void }) {
  const [name, setName] = useState(s.name);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      if (await act('settings.editSubscription', { subscription: s.id, name })) onClose();
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !busy && onClose()}
      title={`Rename ${s.name}`}
      actions={
        <>
          <Button variant="quiet" disabled={busy} onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} disabled={!editSubscriptionName.safeParse(name).success} onClick={save}>Save</Button>
        </>
      }
    >
      <Field label="Name" value={name} onValueChange={setName} disabled={busy} autoFocus />
    </Dialog>
  );
}

function LiveTurnLimit({ id, limit, manage }: { id: string; limit: number; manage: boolean }) {
  const [draft, setDraft] = useState<string>();
  const [saving, setSaving] = useState(false);
  const value = draft ?? String(limit);
  const parsed = maxLiveTurns.safeParse(Number(value));
  const save = async () => {
    if (saving || !parsed.success || parsed.data === limit) return;
    setSaving(true);
    try {
      await act('settings.editSubscription', { subscription: id, max_live_turns: parsed.data });
    } finally {
      setDraft(undefined);
      setSaving(false);
    }
  };
  return manage ? (
    <div className="set-num provider-limit">
      <Field
        label="Max live turns"
        type="number"
        wide={false}
        value={value}
        onValueChange={setDraft}
        onCommit={save}
        disabled={saving}
        error={parsed.success ? undefined : '1 to 200'}
      />
    </div>
  ) : <small className="provider-limit">Max live turns {limit}</small>;
}

function Usage({ s }: { s: Subscription }) {
  const u = s.usage;
  if (u?.used_pct != null) {
    const reset = limitReset(s);
    return (
      <span className="provider-usage">
        <Meter pct={u.used_pct} over={u.used_pct >= 100} />
        <small>
          {Number(u.used_pct.toFixed(1))}%{u.window ? ` of ${u.window === 'week' ? 'week' : '5 hours'}` : ''}{reset && `, resets ${reset}`}
        </small>
      </span>
    );
  }
  return <span className="provider-usage">{u?.cost_usd_month != null ? `${usd(u.cost_usd_month)} this month` : u?.reason || '—'}</span>;
}

function ProviderRow({ s, admin, own, open }: { s: Subscription; admin: boolean; own: boolean; open: boolean }) {
  const [changing, setChanging] = useState<Change | null>(null);
  const plan = isPlan(s.kind);
  const agents = s.agents ?? [];
  const badge = providerBadge(s);
  const expandable = !!badge.detail || agents.length > 0;
  const by = plan ? (s.owner_id ? s.owner_name || 'A teammate' : `Added by ${s.added_by_name || 'A teammate'}`) : '';
  const preview = s.preview;
  const byWhen = s.reconnect_by && !needsReconnect(s) && fmtDate(s.reconnect_by);
  const manage = admin || own;
  const mayRemove = s.can_remove ?? manage;
  const close = () => setChanging(null);
  return (
    <>
      <div className="row g-provider" data-target={`subscription:${s.id}`} title={s.name}>
        <span className="provider-order">{s.order ?? '–'}</span>
        <div className="provider-name">
          {expandable ? <Link
            className="row-title"
            to="/settings/$section"
            params={{ section: 'providers' }}
            search={open ? {} : { open: s.id }}
            aria-expanded={open}
          >
            {s.name}
          </Link> : <span className="row-title">{s.name}</span>}
          {by && <small title={by}>{by}</small>}
          {preview && <small title={preview}>{preview}</small>}
          {byWhen && <small>Reconnect by {byWhen}</small>}
        </div>
        <div className="provider-kind">
          {plan ? (s.kind === 'codex_login' ? 'ChatGPT plan' : 'Claude plan') : 'API key'}
          {plan && s.max_live_turns !== undefined && <LiveTurnLimit id={s.id} limit={s.max_live_turns} manage={manage} />}
        </div>
        {badge.label !== 'Reconnect needed' && badge.label !== 'Problem' ? <Usage s={s} /> : <span className="provider-usage" />}
        <span className="provider-badge"><Pill flag={badge.label === 'Problem' || needsReconnect(s)} title={badge.detail}>{badge.label}</Pill></span>
        <span className="provider-agents">Picked by {agentsWord(agents.length)}</span>
        <span className="provider-more">
          {(manage || mayRemove) && (
            <Menu trigger={<IconButton icon="more" label={`More for ${s.name}`} size="sm" quiet />}>
              {manage && <MenuItem label={plan ? 'Reconnect' : 'Replace key'} onSelect={() => setChanging(plan ? 'reconnect' : 'replace')} />}
              {manage && s.runtime === 'claude' && plan && !needsReconnect(s) && (
                <MenuItem label="Check use" onSelect={() => void act('settings.refreshUsage', { subscription: s.id })} />
              )}
              {manage && <MenuItem label="Rename" onSelect={() => setChanging('rename')} />}
              {mayRemove && <MenuItem label="Remove" onSelect={() => setChanging('remove')} />}
            </Menu>
          )}
        </span>
      </div>
      {open && expandable && (
        <Facts label={s.name}>
          {badge.detail && <Line label="State">{badge.detail}</Line>}
          {agents.length > 0 && (
            <Line label="Picked by">
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
      )}
      {changing === 'reconnect' && manage && isPlan(s.kind) && <ConnectPlan kind={s.kind} subscription={s.id} onClose={close} />}
      {changing === 'replace' && manage && <KeyDialog kind={s.kind} label="API key" subscription={s} onClose={close} />}
      {changing === 'rename' && manage && <RenameDialog s={s} onClose={close} />}
      {changing === 'remove' && mayRemove && (
        <Dialog
          open
          onOpenChange={(o) => !o && close()}
          alert
          title={`Remove ${s.name}?`}
          description={s.removal_agents === undefined ? undefined : `${agentsWord(s.removal_agents)} will lose Paid by.`}
          actions={
            <>
              <Button variant="quiet" onClick={close}>Cancel</Button>
              <Button variant="primary" onClick={async () => { if (await act('settings.removeSubscription', { subscription: s.id })) close(); }}>Remove</Button>
            </>
          }
        />
      )}
    </>
  );
}

function ExitPicker({ exit, close }: { exit: PlanExit; close: () => void }) {
  const { data, isError, isPending, hasNextPage, isFetchingNextPage, fetchNextPage } = useInfiniteQuery(planExitQuery(exit.person_id));
  const [computer, choose] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const options = data?.pages.flatMap((page) => page.computers.map(({ id, label }) => ({ value: id, label }))) ?? [];
  const save = async () => {
    if (!computer) return;
    setBusy(true);
    try {
      if (await act('settings.changePlanExit', { person: exit.person_id, computer })) close();
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open
      onOpenChange={(open) => !open && !busy && close()}
      title="Change exit computer"
      actions={
        <>
          <Button variant="quiet" disabled={busy} onClick={close}>Cancel</Button>
          <Button variant="primary" busy={busy} disabled={!computer || computer === exit.runner_id} onClick={save}>Save</Button>
        </>
      }
    >
      {isError && !data ? <p role="alert">Couldn't load computers</p> : (
        <Select label="Computer" value={computer ?? exit.runner_id} options={options} onValueChange={choose}
          placeholder={isPending ? 'Loading…' : options.length ? 'Choose a computer' : 'No computers'} disabled={busy || options.length === 0} />
      )}
      {hasNextPage && <Button variant="quiet" busy={isFetchingNextPage} onClick={() => void fetchNextPage()}>More computers</Button>}
    </Dialog>
  );
}

function Pool({ name, list, admin, own = false, open, exit, defaultExpanded = true }: { name: string; list: Subscription[]; admin: boolean; own?: boolean; open?: string; exit?: PlanExit; defaultExpanded?: boolean }) {
  const content = useId();
  const [changingExit, setChangingExit] = useState(false);
  const opened = open?.replace(/^subscription:/, '');
  const addressed = list.some((s) => s.id === opened) ? opened : undefined;
  const [toggle, setToggle] = useState<{ addressed?: string; expanded: boolean }>();
  const expanded = toggle && toggle.addressed === addressed ? toggle.expanded : defaultExpanded || !!addressed;
  const plans = list.filter((s) => isPlan(s.kind));
  const keys = list.filter((s) => !isPlan(s.kind));
  const agents = new Set(list.flatMap((s) => (s.agents ?? []).map((a) => a.id)));
  const summary = [
    plans.length && `${plans.length} ${plans.length === 1 ? 'plan' : 'plans'}`,
    keys.length && `${keys.length} ${keys.length === 1 ? 'key' : 'keys'}`,
  ].filter(Boolean).join(', ') || 'empty';
  const sorted = [...plans].sort((a, b) => a.runtime.localeCompare(b.runtime) || (a.order ?? Infinity) - (b.order ?? Infinity));
  return (
    <section className="provider-pool" aria-label={name}>
      <header className="provider-pool-head">
        <SectionHead level={2} name={name} open={expanded} controls={content} onToggle={() => setToggle({ addressed, expanded: !expanded })} />
        <span>{summary}</span>
        {exit && list.some((s) => isPlan(s.kind)) && (
          <span>
            Exit: {exit.runner_label ?? 'None'}
            {exit.can_change && <> <button type="button" className="verb" onClick={() => setChangingExit(true)}>Change</button></>}
          </span>
        )}
        {list.length > 0 && <span className="provider-pool-count">Picked by {agentsWord(agents.size)}</span>}
      </header>
      {changingExit && exit?.can_change && <ExitPicker exit={exit} close={() => setChangingExit(false)} />}
      <div id={content} hidden={!expanded}>
        {expanded && [...sorted, ...keys].map((s) => (
          <ProviderRow key={s.id} s={s} admin={admin} own={own} open={addressed === s.id} />
        ))}
      </div>
    </section>
  );
}

export function Providers({ open }: { open?: string }) {
  const { data } = useQuery(subscriptionsQuery);
  const { data: me } = useQuery(meQuery);
  const admin = useAdmin();
  const { data: access } = useQuery({ ...accessQuery, retry: false, enabled: admin });
  const [adding, setAdding] = useState<Adding | null>(null);
  if (!data) return null;
  const exits = new Map(data.plan_exits?.map((exit) => [`human:${exit.person_id}`, exit]));
  const mine: Subscription[] = [];
  const ownerless: Subscription[] = [];
  const company: Subscription[] = [];
  const other = new Map<string, { name: string; list: Subscription[] }>();
  for (const s of data.subscriptions) {
    if (!s.owner_id) (isPlan(s.kind) ? ownerless : company).push(s);
    else if (s.owner_id === me?.id) mine.push(s);
    else {
      const pool = other.get(s.owner_id);
      if (pool) pool.list.push(s);
      else other.set(s.owner_id, { name: s.owner_name || 'A teammate', list: [s] });
    }
  }
  const signIns = data.kinds.filter((k): k is typeof k & { kind: SignInKind } => k.connect && isPlan(k.kind));
  const keys = data.kinds.filter((k) => !k.connect);
  const addKinds = (owner?: Owner) => (
    <>
      {signIns.map((k) => <MenuItem key={k.kind} label={k.label} meta="Sign in" onSelect={() => setAdding({ sign: k.kind, owner })} />)}
      {signIns.length > 0 && keys.length > 0 && <MenuRule />}
      {keys.map((k) => <MenuItem key={k.kind} label={k.label.replace(/ API key$/, ' key')} meta="Paste" onSelect={() => setAdding({ key: k.kind, label: k.secret_label || k.label, owner })} />)}
    </>
  );
  const people = admin ? (access?.people ?? []).filter((p) => !p.left && p.sign_in && `human:${p.id}` !== me?.id) : [];
  const add = (
    <Menu trigger={<Button icon="plus" variant="primary">Add</Button>}>
      {addKinds()}
      {people.length > 0 && (
        <>
          <MenuRule />
          <MenuSub label="Add for a teammate">
            {people.map((p) => <MenuSub key={p.id} label={p.name}>{addKinds(p)}</MenuSub>)}
          </MenuSub>
        </>
      )}
    </Menu>
  );
  return (
    <>
      <div className="provider-pools" data-target="setting:subscriptions">
        <SectionHead name="Providers" actions={add} />
        <Pool name="Your pool" list={mine} admin={admin} own open={open} exit={exits.get(me?.id ?? '')} />
        {[...other].sort(([, a], [, b]) => a.name.localeCompare(b.name)).map(([id, pool]) => (
          <Pool key={id} name={`${pool.name}'s pool`} list={pool.list} admin={admin} open={open} exit={exits.get(id)} defaultExpanded={false} />
        ))}
        {ownerless.length > 0 && <Pool name="No owner" list={ownerless} admin={admin} open={open} />}
        {company.length > 0 && <Pool name="Company keys" list={company} admin={admin} open={open} />}
      </div>
      <Section name="Tico Gateway" target="setting:fallback">
        <Facts label="Tico Gateway">
          <GatewayLines />
          <Line label="Gateway limit"><Link className="verb" to="/company/usage" search={{ view: 'limits' }}>Limits</Link></Line>
        </Facts>
      </Section>
      {adding && 'sign' in adding && <ConnectPlan kind={adding.sign} owner={adding.owner} onClose={() => setAdding(null)} />}
      {adding && 'key' in adding && <KeyDialog kind={adding.key} label={adding.label} owner={adding.owner} onClose={() => setAdding(null)} />}
    </>
  );
}
