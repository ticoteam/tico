import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import { run } from '@/actions/run';
import type { UsageLimitView, UsageLimits } from '@/api/schema';
import { directoryQuery } from '@/data';
import { agentsQuery } from '@/features/team/exports';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Pill } from '@/kit/Pill';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { refusal } from '@/actions';
import { Section, SidePanel } from '@/pagekinds';
import { Foot, Meter, Verb, Word, act, usd } from './parts';
import { agentUsageQuery, limitsQuery, poolsOf, taskCapsQuery, type UsagePool } from './queries';
import type { UsageSearch } from './Usage';

/* Usage › Limits (pages/8-settings/more.js; the old Strategy › Budget, ui/app/strategy-budget.ts). Monthly limits only:
   the default for a new agent, the whole company's, each team's (a team's spend counts every agent under it, so teams
   are drawn as their tree), and each agent's. A row opens beside the list (?open=<slug> | pool:company | pool:<team>):
   what it spent, its limit, and for an agent where the money went (tasks, models) and its caps on tasks a day. */

export const POOL = 'pool:';
const COMPANY = 'company';

type Editing = { who: string; now: number | null; agent?: string; scope?: string } | null;

/** Where a limit row opens: an agent's slug, or `pool:company` / `pool:<group id>`. */
const openOf = (search: UsageSearch, id: string) => ({ ...search, open: search.open === id ? undefined : id });

export function LimitDialog({ who, now, agent, scope, onClose }: NonNullable<Editing> & { onClose: () => void }) {
  const [value, setValue] = useState(now != null ? String(now) : '');
  const n = Number(value);
  const ok = value.trim() !== '' && Number.isFinite(n) && n >= 0;
  const save = () => {
    onClose();
    void (agent
      ? act('usage.setLimit', { agent, monthly: n })
      : scope
        ? act('usage.setPoolLimit', { scope, monthly: n })
        : act('usage.setDefaultLimit', { monthly: n }));
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Monthly limit for ${who}`}
      description="When it is reached, new work waits until the month turns over."
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!ok} onClick={save}>
            Save
          </Button>
        </>
      }
    >
      <Field label="Dollars a month" type="number" value={value} onValueChange={setValue} autoFocus />
    </Dialog>
  );
}

/** Cancel the queued automatic wakes and waits, company-wide or for one team; asked first. */
function ClearQueue({ teams }: { teams: [string, UsagePool, number][] }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [scope, setScope] = useState('company');
  const clear = async () => {
    setBusy(true);
    const r = await run('usage.clearQueue', { scope }, { via: 'click' });
    setBusy(false);
    setAsking(false);
    toast(r.ok ? { text: r.did ?? 'Cleared', kind: 'done' } : refusal(r, "Couldn't clear"));
  };
  return (
    <>
      <Foot>
        <Verb onClick={() => setAsking(true)}>Clear queue</Verb>
      </Foot>
      <Dialog
        open={asking}
        onOpenChange={setAsking}
        alert
        title="Clear the automatic queue?"
        description="Cancels queued automatic wakes and waits. Keeps tasks, people's messages and running work."
        actions={
          <>
            <Button variant="quiet" onClick={() => setAsking(false)}>
              Cancel
            </Button>
            <Button variant="primary" busy={busy} onClick={() => void clear()}>
              Clear queue
            </Button>
          </>
        }
      >
        {teams.length > 0 && (
          <Select
            label="Applies to"
            value={scope}
            options={[
              { value: 'company', label: 'Whole company' },
              ...teams.map(([gid, g, depth]) => ({ value: `group:${gid}`, label: `${'\u2003'.repeat(depth)}${g.name ?? gid}` })),
            ]}
            onValueChange={setScope}
          />
        )}
      </Dialog>
    </>
  );
}

/** The teams as their tree: each after its parent, with its depth. */
export function teamTree(groups: Record<string, UsagePool>): [string, UsagePool, number][] {
  const out: [string, UsagePool, number][] = [];
  const kids = (parent: string | null) =>
    Object.entries(groups)
      .filter(([, g]) => (g.parent && groups[g.parent] ? g.parent : null) === parent)
      .sort((a, b) => (a[1].name ?? a[0]).localeCompare(b[1].name ?? b[0]));
  const walk = (parent: string | null, depth: number) => {
    for (const [gid, g] of kids(parent)) {
      out.push([gid, g, depth]);
      if (depth < 12) walk(gid, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

/** A team and every team under it. */
function subtree(groups: Record<string, UsagePool>, gid: string): Set<string> {
  const all = new Set([gid]);
  for (let grew = true; grew; ) {
    grew = false;
    for (const [id, g] of Object.entries(groups)) {
      if (g.parent && all.has(g.parent) && !all.has(id)) {
        all.add(id);
        grew = true;
      }
    }
  }
  return all;
}

function Spent({ spent, limit }: { spent: number | null; limit: number | null }) {
  return (
    <>
      <span className="row-fig u-used">{usd(spent ?? 0)}</span>
      <span className="row-fig l-of">{limit != null ? `of ${usd(limit)}` : 'No limit'}</span>
      <span className="row-cell u-per">month</span>
    </>
  );
}

function State({ blocked, percent }: { blocked: string | null; percent: number }) {
  return blocked ? <Word>Paused</Word> : percent >= 90 ? <Pill flag>Near</Pill> : null;
}

function PoolRow({ id, name, pool, depth, search }: { id: string; name: string; pool: UsagePool; depth?: number; search: UsageSearch }) {
  const isOpen = search.open === id;
  return (
    <Link
      to="/company/usage"
      search={openOf(search, id)}
      className={pool.blocked ? 'row g-lim is-bad' : 'row g-lim'}
      data-target={`limit:${id === POOL + COMPANY ? COMPANY : id.replace(POOL, 'team:')}`}
      aria-expanded={isOpen}
      title={name}
    >
      <Icon name={id === POOL + COMPANY ? 'company' : 'people'} />
      <span className="row-title" style={depth ? { paddingInlineStart: `${depth * 14}px` } : undefined}>
        {name}
        <State blocked={pool.blocked} percent={pool.percent} />
      </span>
      <span className="u-bar">
        <Meter pct={pool.percent} over={!!pool.blocked} />
      </span>
      <Spent spent={pool.month_spent} limit={pool.monthly_usd} />
    </Link>
  );
}

export function Limits({ search }: { search: UsageSearch }) {
  const limits = useQuery(limitsQuery);
  const dir = useQuery(directoryQuery);
  const [editing, setEditing] = useState<Editing>(null);
  const v = limits.data;
  if (!v) return null;
  const bots = Object.entries(v.bots ?? {}).sort((a, b) => (b[1].month_spent ?? 0) - (a[1].month_spent ?? 0));
  const pools = poolsOf(v);
  const teams = teamTree(pools?.groups ?? {});
  return (
    <>
      <Section name="Defaults" target="limit:default">
        <Facts label="Defaults">
          <LabelValue
            label="A new agent"
            onEdit={v.may_edit_default ? () => setEditing({ who: 'a new agent', now: v.default.monthly_usd }) : undefined}
          >
            {v.default.monthly_usd != null ? `${usd(v.default.monthly_usd)} a month` : 'No limit'}
          </LabelValue>
          <LabelValue label="When reached">New work waits</LabelValue>
          <LabelValue label="Count plan runs">{v.default.count_subscription ? 'Yes' : 'No'}</LabelValue>
        </Facts>
        {v.may_edit_default && <ClearQueue teams={teams} />}
      </Section>
      {pools?.company && (
        <Section name="Company" target="limit:company">
          <PoolRow id={POOL + COMPANY} name="Whole company" pool={pools.company} search={search} />
        </Section>
      )}
      {teams.length > 0 && (
        <Section name="Teams" count={teams.length} target="limit:teams">
          {teams.map(([gid, g, depth]) => (
            <PoolRow key={gid} id={POOL + gid} name={g.name ?? gid} pool={g} depth={depth} search={search} />
          ))}
        </Section>
      )}
      <Section name="Agents" count={bots.length} target="setting:limits">
        {bots.length === 0 ? (
          <Empty title="No agents yet" />
        ) : (
          bots.map(([slug, l]) => {
            const who = dir.data?.byId[slug] ?? dir.data?.byId[`bot:${slug}`];
            const name = who?.name ?? slug;
            return (
              <Link
                key={slug}
                to="/company/usage"
                search={openOf(search, slug)}
                className={l.blocked ? 'row g-lim is-bad' : 'row g-lim'}
                data-target={`limit:${slug}`}
                aria-expanded={search.open === slug}
                title={name}
              >
                <Avatar kind="agent" name={name} hue={who?.hue} decorative />
                <span className="row-title">
                  {name}
                  <State blocked={l.blocked} percent={l.percent} />
                </span>
                <span className="u-bar">
                  <Meter pct={l.percent} over={!!l.blocked} />
                </span>
                <Spent spent={l.month_spent} limit={l.monthly_usd} />
              </Link>
            );
          })
        )}
      </Section>
      {editing && <LimitDialog {...editing} onClose={() => setEditing(null)} />}
    </>
  );
}

/** Add limit (the mock's one filled button on Limits): pick the company, a team or an agent, then dollars a month. */
export function AddLimit() {
  const limits = useQuery(limitsQuery);
  const dir = useQuery(directoryQuery);
  const [open, setOpen] = useState(false);
  const [pick, setPick] = useState<string | null>(null);
  const [value, setValue] = useState('');
  const v = limits.data;
  const options = addable(v, (slug) => (dir.data?.byId[slug] ?? dir.data?.byId[`bot:${slug}`])?.name ?? slug);
  if (options.length === 0) return null;
  const n = Number(value);
  const ok = !!pick && value.trim() !== '' && Number.isFinite(n) && n >= 0;
  const close = () => {
    setOpen(false);
    setPick(null);
    setValue('');
  };
  const save = () => {
    if (!pick) return;
    close();
    void (pick.startsWith(POOL) ? act('usage.setPoolLimit', { scope: pick.slice(POOL.length), monthly: n }) : act('usage.setLimit', { agent: pick, monthly: n }));
  };
  return (
    <>
      <Button variant="primary" onClick={() => setOpen(true)} data-phone>
        Add limit
      </Button>
      <Dialog
        open={open}
        onOpenChange={(o) => !o && close()}
        title="Add limit"
        actions={
          <>
            <Button variant="quiet" onClick={close}>
              Cancel
            </Button>
            <Button variant="primary" disabled={!ok} onClick={save}>
              Save
            </Button>
          </>
        }
      >
        <div className="set-add">
          <Select label="For" look="field" value={pick} options={options} placeholder="Choose" onValueChange={setPick} />
          <Field label="Dollars a month" type="number" value={value} onValueChange={setValue} />
        </div>
      </Dialog>
    </>
  );
}

/** What may take a limit here: the company and the teams (administrators; a lead its own teams) and the agents. */
function addable(v: UsageLimits | undefined, name: (slug: string) => string) {
  if (!v) return [];
  const pools = poolsOf(v);
  return [
    ...(pools?.company && v.may_edit_default ? [{ value: POOL + COMPANY, label: 'Whole company' }] : []),
    ...teamTree(pools?.groups ?? {})
      .filter(([, g]) => v.may_edit_default || g.may_edit)
      .map(([gid, g]) => ({ value: POOL + gid, label: g.name ?? gid, meta: 'Team' })),
    ...Object.entries(v.bots ?? {})
      .filter(([, l]) => l.may_edit)
      .map(([slug]) => ({ value: slug, label: name(slug), meta: 'Agent' })),
  ];
}

/* ---------------------------------------------------------------- the panel */

function Figure({ spent, limit, blocked, percent }: { spent: number | null; limit: number | null; blocked: string | null; percent: number }) {
  return (
    <div className="u-fig">
      <p>
        <b>{usd(spent ?? 0)}</b>
        <span>{limit != null ? `of ${usd(limit)} a month` : 'this month, no limit'}</span>
        <State blocked={blocked} percent={percent} />
      </p>
      <Meter pct={percent} over={!!blocked} />
    </div>
  );
}

/** The opened row beside the list: an agent's, the company's or a team's. */
export function LimitPanel({ search }: { search: UsageSearch }) {
  const id = search.open ?? '';
  const close = () => void run('usage.open', { ...search, open: undefined }, { via: 'click' });
  return id.startsWith(POOL) ? <PoolPanel scope={id.slice(POOL.length)} search={search} onClose={close} /> : <AgentPanel slug={id} onClose={close} />;
}

function AgentPanel({ slug, onClose }: { slug: string; onClose: () => void }) {
  const limits = useQuery(limitsQuery);
  const dir = useQuery(directoryQuery);
  const used = useQuery(agentUsageQuery('month', slug));
  const [editing, setEditing] = useState<Editing>(null);
  const l: UsageLimitView | undefined = limits.data?.bots?.[slug];
  if (!l) return null;
  const who = dir.data?.byId[slug] ?? dir.data?.byId[`bot:${slug}`];
  const name = who?.name ?? slug;
  const u = used.data;
  const tasks = (u?.tasks ?? []).filter((t) => t.value);
  const models = u?.models ?? [];
  const spent = (r: { est_cost_usd: number | null; subscription_equiv_usd: number }) => (r.est_cost_usd ?? 0) + (r.subscription_equiv_usd ?? 0);
  return (
    <SidePanel
      title={name}
      target={`limit:${slug}`}
      lead={<Avatar kind="agent" name={name} hue={who?.hue} size={22} decorative />}
      onClose={onClose}
      foot={
        <>
          <Link className="verb" to="/company/usage" search={{ range: 'month', open: slug }}>
            Open in Usage
          </Link>
          {l.may_edit && l.own_monthly_usd != null && (
            <Button variant="quiet" onClick={() => void act('usage.setLimit', { agent: slug })}>
              Use default
            </Button>
          )}
        </>
      }
    >
      <div className="rail-sec">
        <Figure spent={l.month_spent} limit={l.monthly_usd} blocked={l.blocked} percent={l.percent} />
        <Facts label={name}>
          <LabelValue label="Limit" onEdit={l.may_edit ? () => setEditing({ who: name, now: l.own_monthly_usd ?? l.monthly_usd, agent: slug }) : undefined}>
            {l.monthly_usd != null ? `${usd(l.monthly_usd)} a month` : 'No limit'}
            {l.own_monthly_usd == null && l.monthly_usd != null ? ' (the default)' : ''}
          </LabelValue>
          <LabelValue label="When reached">New work waits</LabelValue>
          {u?.totals && (
            <LabelValue label="Runs" num>
              {u.totals.runs.toLocaleString('en-US')}
            </LabelValue>
          )}
        </Facts>
      </div>
      {tasks.length > 0 && (
        <div className="rail-sec">
          <h3>Went to</h3>
          {tasks.slice(0, 6).map((t) => (
            <div key={t.value} className="rail-row u-line" title={t.name ?? ''}>
              <span className="row-title">{t.name ?? t.value}</span>
              <span className="row-fig">{usd(spent(t))}</span>
            </div>
          ))}
          {tasks.length > 6 && <p className="u-more">{tasks.length - 6} more tasks</p>}
        </div>
      )}
      {models.length > 0 && (
        <div className="rail-sec">
          <h3>Models</h3>
          {models.map((m, i) => (
            <div key={m.value ?? i} className="rail-row u-line" title={m.name ?? ''}>
              <span className="row-title">{m.name ?? m.value}</span>
              <span className="row-fig">{usd(spent(m))}</span>
            </div>
          ))}
        </div>
      )}
      <TaskCaps slug={slug} name={name} />
      {editing && <LimitDialog {...editing} onClose={() => setEditing(null)} />}
    </SidePanel>
  );
}

function PoolPanel({ scope, search, onClose }: { scope: string; search: UsageSearch; onClose: () => void }) {
  const limits = useQuery(limitsQuery);
  const roster = useQuery(agentsQuery);
  const dir = useQuery(directoryQuery);
  const [editing, setEditing] = useState<Editing>(null);
  const v = limits.data;
  const pools = poolsOf(v);
  const pool = scope === COMPANY ? pools?.company : pools?.groups[scope];
  if (!v || !pools || !pool) return null;
  const name = scope === COMPANY ? 'Whole company' : (pool.name ?? scope);
  const editable = v.may_edit_default || !!pool.may_edit;
  // A team's spend is every agent under it: its own and its teams'.
  const under = scope === COMPANY ? null : subtree(pools.groups, scope);
  const agents = under ? (Array.isArray(roster.data) ? roster.data : []).filter((a) => a.team && under.has(a.team)) : [];
  const teams = scope === COMPANY ? teamTree(pools.groups).filter(([, , d]) => d === 0) : teamTree(pools.groups).filter(([gid]) => pools.groups[gid]?.parent === scope);
  return (
    <SidePanel title={name} target={`limit:${scope === COMPANY ? COMPANY : `team:${scope}`}`} lead={<Icon name={scope === COMPANY ? 'company' : 'people'} />} onClose={onClose}>
      <div className="rail-sec">
        <Figure spent={pool.month_spent} limit={pool.monthly_usd} blocked={pool.blocked} percent={pool.percent} />
        <Facts label={name}>
          <LabelValue label="Limit" onEdit={editable ? () => setEditing({ who: name, now: pool.monthly_usd, scope }) : undefined}>
            {pool.monthly_usd != null ? `${usd(pool.monthly_usd)} a month` : 'No limit'}
          </LabelValue>
          <LabelValue label="When reached">New work waits</LabelValue>
        </Facts>
        {editable && pool.monthly_usd != null && (
          <Foot open>
            <Verb onClick={() => void act('usage.setPoolLimit', { scope })}>Remove limit</Verb>
          </Foot>
        )}
      </div>
      {teams.length > 0 && (
        <div className="rail-sec">
          <h3>Teams</h3>
          {teams.map(([gid, g]) => (
            <Link key={gid} className="rail-row u-line" to="/company/usage" search={{ ...search, open: POOL + gid }} title={g.name ?? gid}>
              <span className="row-title">{g.name ?? gid}</span>
              <span className="row-fig">{usd(g.month_spent ?? 0)}</span>
            </Link>
          ))}
        </div>
      )}
      {agents.length > 0 && (
        <div className="rail-sec">
          <h3>Agents</h3>
          {agents.map((a) => {
            const l = v.bots?.[a.slug] ?? v.bots?.[a.id];
            const who = dir.data?.byId[`bot:${a.id}`];
            const label = a.display_name || who?.name || a.slug;
            return (
              <Link key={a.id} className="rail-row u-line" to="/company/usage" search={{ ...search, open: v.bots?.[a.slug] ? a.slug : a.id }} title={label}>
                <span className="row-title">{label}</span>
                <span className="row-fig">{l ? usd(l.month_spent ?? 0) : ''}</span>
              </Link>
            );
          })}
        </div>
      )}
      {editing && <LimitDialog {...editing} onClose={() => setEditing(null)} />}
    </SidePanel>
  );
}

/** As task caps name a person: `person:<id>`, where the directory says `human:<id>`. */
const capRef = (actor: string) => actor.replace(/^human:/, 'person:');

/** Caps on the tasks this agent sends a manager above it a day (the old Budget's task caps). */
function TaskCaps({ slug, name }: { slug: string; name: string }) {
  const caps = useQuery(taskCapsQuery);
  const dir = useQuery(directoryQuery);
  const roster = useQuery(agentsQuery);
  const [editing, setEditing] = useState<{ receiver?: string; now?: number } | null>(null);
  if (!caps.data) return null;
  // The server names an agent by its id (`bot:<id>`) and takes its slug too.
  const id = (Array.isArray(roster.data) ? roster.data : []).find((a) => a.slug === slug)?.id ?? slug;
  const sender = `bot:${id}`;
  const mine = (caps.data.caps ?? []).filter((c) => c.sender === sender || c.sender === `bot:${slug}`);
  const nameOf = (ref: string) => dir.data?.byId[ref.replace(/^person:/, 'human:')]?.name ?? ref.replace(/^(bot|person):/, '');
  return (
    <div className="rail-sec">
      <h3>Tasks a day</h3>
      {mine.map((c) => (
        <div key={c.receiver} className="rail-row u-line" title={`To ${nameOf(c.receiver)}`}>
          <span className="row-title">To {nameOf(c.receiver)}</span>
          <span className="row-fig">{c.per_day === 0 ? 'Blocked' : `${c.count} of ${c.per_day}`}</span>
          <Verb onClick={() => setEditing({ receiver: c.receiver, now: c.per_day })}>Change</Verb>
        </div>
      ))}
      <Foot open>
        <Verb onClick={() => setEditing({})}>Add cap</Verb>
      </Foot>
      {editing && (
        <CapDialog
          sender={sender}
          name={name}
          receiver={editing.receiver}
          now={editing.now}
          options={(dir.data?.actors ?? []).filter((a) => capRef(a.id) !== sender).map((a) => ({ value: capRef(a.id), label: a.name, meta: a.kind === 'agent' ? 'Agent' : undefined }))}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}

function CapDialog({
  sender,
  name,
  receiver,
  now,
  options,
  onClose,
}: {
  sender: string;
  name: string;
  receiver?: string;
  now?: number;
  options: { value: string; label: string; meta?: string }[];
  onClose: () => void;
}) {
  const [to, setTo] = useState<string | null>(receiver ?? null);
  const [value, setValue] = useState(now != null ? String(now) : '');
  const n = Number(value);
  const ok = !!to && value.trim() !== '' && Number.isInteger(n) && n >= 0;
  const send = (perDay?: number) => {
    if (!to) return;
    onClose();
    void act('usage.setTaskCap', { sender, receiver: to, ...(perDay != null ? { perDay } : {}) });
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Tasks a day from ${name}`}
      description="0 blocks them. The receiver must be a manager above."
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          {receiver && (
            <Button variant="quiet" onClick={() => send()}>
              Remove
            </Button>
          )}
          <Button variant="primary" disabled={!ok} onClick={() => send(n)}>
            Save
          </Button>
        </>
      }
    >
      <div className="set-add">
        {!receiver && <Select label="To" look="field" value={to} options={options} placeholder="Choose" onValueChange={setTo} />}
        <Field label="Tasks a day" type="number" value={value} onValueChange={setValue} autoFocus={!!receiver} />
      </div>
    </Dialog>
  );
}
