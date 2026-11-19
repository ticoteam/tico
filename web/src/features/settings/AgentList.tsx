import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from '@tanstack/react-router';
import { useState, type HTMLAttributes } from 'react';
import { run } from '@/actions/run';
import { directoryQuery, meQuery, type Actor } from '@/data';
import { ModelPicker, agentsQuery, computersQuery, type AgentRow } from '@/features/team/exports';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Checkbox } from '@/kit/Checkbox';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field, Search } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Facts } from '@/kit/LabelValue';
import { Link as To } from '@/kit/Link';
import { Menu, MenuItem } from '@/kit/Menu';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { ValueButton } from '@/kit/ValueButton';
import { Section } from '@/pagekinds';
import { ComputerPanel } from './ComputerPanel';
import { useRowDrag } from './drag';
import { Foot, Line, SwitchLine, Verb, Word, act, usd } from './parts';
import {
  SAFE_RETRY,
  accessQuery,
  botAccessQuery,
  botMailQuery,
  botsQuery,
  limitsQuery,
  modelsQuery,
  stoppedRunsQuery,
  subscriptionsQuery,
  type Audience,
  type BotAccess,
  type BotListed,
  type StoppedRun,
  type Subscription,
  type TaskModel,
} from './queries';

/* Settings › Agents, the fleet (pages/8-settings/settings.js fleet): search, group by team, computer or subscription,
   change a row where it stands, check several to change their model or move them; open one in place for its model,
   owners, access, mail and stopped runs; bring back a retired one. */

const bare = (id: string) => id.replace(/^(human|bot):/, '');

interface Person {
  id: string;
  name: string;
}

function usePeople(): Person[] {
  const dir = useQuery(directoryQuery);
  return (dir.data?.actors ?? []).filter((a) => a.kind === 'person').map((a) => ({ id: bare(a.id), name: a.name }));
}

/** Owners and admins, and an agent's own owners, may bring a retired agent back. */
function useMayRestore() {
  const me = useQuery(meQuery);
  const access = useQuery({ ...accessQuery, retry: false });
  const mine = me.data ? bare(me.data.id) : '';
  const admin = me.data?.role === 'owner' || access.data?.you === 'owner' || access.data?.you === 'admin';
  return (b: BotListed) =>
    admin || (!!mine && (bare(b.owner_id ?? '') === mine || (b.bot_owners ?? []).some((o) => bare(o.id) === mine)));
}

const agentsWord = (n: number) => `${n} ${n === 1 ? 'agent' : 'agents'}`;

const toggled = (s: Set<string>, id: string, on: boolean) => {
  const next = new Set(s);
  if (on) next.add(id);
  else next.delete(id);
  return next;
};

// ---------------------------------------------------------------- change the model of several agents at once (one agent: team's ModelPicker)

function ModelsDialog({ agents, onClose, onDone }: { agents: string[]; onClose: () => void; onDone?: () => void }) {
  const models = useQuery(modelsQuery);
  const [harness, setHarness] = useState('');
  const [model, setModel] = useState('');
  const [effort, setEffort] = useState('');
  const [busy, setBusy] = useState(false);
  const v = models.data;
  const on = v?.enabled_providers;
  const usable = (v?.models ?? []).filter((m) => !m.deprecated && (!on || on.includes(m.provider)));
  const harnesses = (v?.harnesses ?? []).filter((h) => usable.some((m) => !m.harnesses || m.harnesses.includes(h.id)));
  const shown = usable.filter((m) => !harness || !m.harnesses || m.harnesses.includes(harness));
  const efforts = shown.find((m) => m.id === model)?.efforts ?? [];
  const apply = async () => {
    setBusy(true);
    const r = await run(
      'settings.changeModels',
      { agents, model, ...(effort ? { effort } : {}), ...(harness ? { harness } : {}) },
      { via: 'click' },
    );
    setBusy(false);
    if (!r.ok) return toast({ text: "Couldn't change the model", kind: 'failed' });
    toast({ text: r.did ?? 'Changed', kind: 'done' });
    onDone?.();
    onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Change model"
      description={agentsWord(agents.length)}
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} disabled={!model} onClick={() => void apply()}>
            Change
          </Button>
        </>
      }
    >
      <div className="set-add">
        {harnesses.length > 1 && (
          <Select
            label="Harness"
            value={harness}
            options={[{ value: '', label: 'Each its own' }, ...harnesses.map((h) => ({ value: h.id, label: h.name }))]}
            onValueChange={(h) => {
              setHarness(h);
              if (h && !usable.some((m) => m.id === model && (!m.harnesses || m.harnesses.includes(h)))) setModel('');
            }}
          />
        )}
        <Select
          label="Model"
          value={model || null}
          placeholder="Pick a model"
          options={shown.map((m) => ({ value: m.id, label: m.label, ...(m.id === v?.default?.model ? { meta: 'Default' } : {}) }))}
          onValueChange={(m) => {
            setModel(m);
            if (!usable.find((x) => x.id === m)?.efforts?.includes(effort)) setEffort('');
          }}
        />
        {efforts.length > 0 && (
          <Select
            label="Effort"
            value={effort}
            options={[{ value: '', label: 'Default' }, ...efforts.map((e) => ({ value: e, label: e.charAt(0).toUpperCase() + e.slice(1) }))]}
            onValueChange={setEffort}
          />
        )}
      </div>
    </Dialog>
  );
}

// ---------------------------------------------------------------- who owns an agent

function OwnersDialog({ bot, name, listed, onClose }: { bot: BotListed; name: string; listed: string[]; onClose: () => void }) {
  const people = usePeople();
  const creator = bare(bot.owner_id ?? '');
  const [chosen, setChosen] = useState(() => new Set(listed));
  const [busy, setBusy] = useState(false);
  const add = [...chosen].filter((id) => !listed.includes(id) && id !== creator);
  const remove = listed.filter((id) => !chosen.has(id) && id !== creator);
  const save = async () => {
    setBusy(true);
    const ok = await act('settings.agentOwners', { agent: bot.id ?? bot.slug ?? '', add, remove });
    setBusy(false);
    if (ok) onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Who owns ${name}`}
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} disabled={!add.length && !remove.length} onClick={() => void save()}>
            Save
          </Button>
        </>
      }
    >
      <div className="set-picks">
        {people.map((p) => (
          <Checkbox
            key={p.id}
            label={p.name}
            checked={p.id === creator || chosen.has(p.id)}
            disabled={p.id === creator}
            onCheckedChange={(on) => setChosen((s) => toggled(s, p.id, on))}
          />
        ))}
      </div>
    </Dialog>
  );
}

// ---------------------------------------------------------------- who sees, reads and writes to an agent

type Preset = 'open' | 'requests' | 'private' | 'custom';
type Levels = Pick<BotAccess, 'see' | 'read' | 'write'>;
const LEVELS = [
  ['see', 'See'],
  ['read', 'Read'],
  ['write', 'Write'],
] as const;
const PRESETS = [
  { value: 'open', label: 'Open' },
  { value: 'requests', label: 'Requests only' },
  { value: 'private', label: 'Private' },
  { value: 'custom', label: 'Custom' },
] as const;

const EVERYONE: Audience = { everyone: true, people: [], teams: [], bots: [] };
const same = (a: Audience, b: Audience) => JSON.stringify(a) === JSON.stringify(b);

/** The old app's four presets (ui/app/settings-access.ts accessPresetOf). */
export function presetOf(l: Levels): Preset {
  if (l.see.everyone && l.read.everyone && l.write.everyone) return 'open';
  if (l.see.everyone && l.write.everyone && !l.read.everyone) return 'requests';
  if (!l.see.everyone && same(l.see, l.read) && same(l.read, l.write)) return 'private';
  return 'custom';
}

const ACCESS_WORD: Record<Preset, string> = { open: 'Open', requests: 'Requests only', private: 'Private', custom: 'Custom' };

function PeoplePicks({ who, onChange }: { who: Audience; onChange: (a: Audience) => void }) {
  const people = usePeople();
  return (
    <div className="set-picks">
      {people.map((p) => (
        <Checkbox
          key={p.id}
          label={p.name}
          checked={who.people.includes(p.id)}
          onCheckedChange={(on) =>
            onChange({ ...who, everyone: false, people: on ? [...who.people, p.id] : who.people.filter((x) => x !== p.id) })
          }
        />
      ))}
    </div>
  );
}

function AccessDialog({ bot, name, now, onClose }: { bot: string; name: string; now: BotAccess; onClose: () => void }) {
  const [draft, setDraft] = useState<Levels>({ see: now.see, read: now.read, write: now.write });
  const [preset, setPreset] = useState<Preset>(presetOf(now));
  const [busy, setBusy] = useState(false);
  // The chosen people of Requests only and Private: whoever reads its work now.
  const chosen: Audience = { ...draft.read, everyone: false };
  const pick = (p: Preset) => {
    setPreset(p);
    if (p === 'open') setDraft({ see: EVERYONE, read: EVERYONE, write: EVERYONE });
    if (p === 'requests') setDraft({ see: EVERYONE, read: chosen, write: EVERYONE });
    if (p === 'private') setDraft({ see: chosen, read: chosen, write: chosen });
  };
  const choose = (a: Audience) => setDraft(preset === 'private' ? { see: a, read: a, write: a } : { see: EVERYONE, read: a, write: EVERYONE });
  const save = async () => {
    setBusy(true);
    const ok = await act('settings.agentAccess', { agent: bot, ...draft });
    setBusy(false);
    if (ok) onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Who can use ${name}`}
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} onClick={() => void save()}>
            Save
          </Button>
        </>
      }
    >
      <div className="set-add">
        <Segmented label="Access" options={PRESETS} value={preset} onValueChange={pick} />
        {(preset === 'requests' || preset === 'private') && <PeoplePicks who={chosen} onChange={choose} />}
        {preset === 'custom' &&
          LEVELS.map(([key, label]) => (
            <div key={key} className="set-level">
              <Checkbox
                label={`${label}: everyone`}
                checked={draft[key].everyone}
                onCheckedChange={(on) => setDraft((d) => ({ ...d, [key]: { ...d[key], everyone: on } }))}
              />
              {!draft[key].everyone && <PeoplePicks who={draft[key]} onChange={(a) => setDraft((d) => ({ ...d, [key]: a }))} />}
            </div>
          ))}
      </div>
    </Dialog>
  );
}

// ---------------------------------------------------------------- runs that stopped part way

function StoppedDialog({ bot, jobs, onClose }: { bot: string; jobs: StoppedRun[]; onClose: () => void }) {
  const [note, setNote] = useState('');
  const decide = async (j: StoppedRun, decision: 'resume' | 'dismiss') => {
    const r = await run(
      'settings.stoppedRun',
      { agent: bot, job: j.id, run: j.run_id, decision, ...(note.trim() ? { detail: note.trim() } : {}) },
      { via: 'click' },
    );
    toast(r.ok ? { text: r.did ?? 'Saved', kind: 'done' } : { text: "Couldn't save", kind: 'failed' });
    if (r.ok && jobs.length === 1) onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Stopped runs"
      actions={
        <Button variant="quiet" onClick={onClose}>
          Close
        </Button>
      }
    >
      <div className="set-add">
        {jobs.map((j) => (
          <div key={j.id} className="set-stop">
            <b>{j.task?.title || j.request || 'A run'}</b>
            {j.reason && <span className="set-cmd-note">{j.reason}</span>}
            {!SAFE_RETRY.includes(j.stopped) && <span className="set-cmd-note">Trying again could repeat work that already finished.</span>}
            <div className="open-foot">
              <Verb onClick={() => void decide(j, 'resume')}>Try again</Verb>
              <Verb onClick={() => void decide(j, 'dismiss')}>Dismiss</Verb>
            </div>
          </div>
        ))}
        <Field label="Note for the agent" value={note} onValueChange={setNote} area />
      </div>
    </Dialog>
  );
}

// ---------------------------------------------------------------- one agent, opened in place

type Editing = 'model' | 'owners' | 'access' | 'stopped' | null;

function AgentOpen({ slug, name, listed }: { slug: string; name: string; listed?: BotListed }) {
  const agents = useQuery(agentsQuery);
  const row = agents.data?.find((r) => r.slug === slug);
  const bot = row?.id ?? slug;
  // Each read is refused (403) to someone who does not manage the agent: its line is then left out.
  const access = useQuery({ ...botAccessQuery(bot), retry: false });
  const mail = useQuery({ ...botMailQuery(bot), retry: false });
  const stopped = useQuery({ ...stoppedRunsQuery(bot), retry: false });
  const people = usePeople();
  const [editing, setEditing] = useState<Editing>(null);
  const manage = row?.can_manage !== false;
  const owners = [
    ...new Set([listed?.owner_id, ...(listed?.bot_owners ?? []).map((o) => o.id)].filter((id): id is string => !!id).map(bare)),
  ];
  const ownerName = (id: string) => people.find((p) => p.id === id)?.name ?? listed?.bot_owners?.find((o) => bare(o.id) === id)?.name ?? id;
  const jobs = stopped.data ?? [];
  return (
    <>
      <Facts label={name}>
        <Line label="Model">
          {row?.resolved_model_label ?? row?.resolved_model ?? row?.model ?? 'None'}
          {manage && row && <Verb onClick={() => setEditing('model')}>Change</Verb>}
        </Line>
        {listed && (
          <Line label="Owners">
            {owners.map(ownerName).join(', ') || 'None'}
            {manage && <Verb onClick={() => setEditing('owners')}>Edit</Verb>}
          </Line>
        )}
        {access.data && (
          <Line label="Access">
            {ACCESS_WORD[presetOf(access.data)]}
            {manage && <Verb onClick={() => setEditing('access')}>Edit</Verb>}
          </Line>
        )}
        {mail.data && (
          <SwitchLine
            label="Send mail without asking"
            checked={mail.data.outbound_send}
            disabled={!manage || mail.data.company_send === false}
            onChange={(on) => void act('settings.agentMail', { agent: bot, on })}
          />
        )}
        {jobs.length > 0 && (
          <Line label="Stopped runs">
            {jobs.length}
            <Verb onClick={() => setEditing('stopped')}>Review</Verb>
          </Line>
        )}
      </Facts>
      <Foot open>
        <To href={`/team/${encodeURIComponent(slug)}?tab=settings`} className="verb">
          Open page
        </To>
      </Foot>
      {editing === 'model' && row && <ModelPicker agent={row} open onOpenChange={(o) => !o && setEditing(null)} />}
      {editing === 'owners' && listed && <OwnersDialog bot={listed} name={name} listed={owners} onClose={() => setEditing(null)} />}
      {editing === 'access' && access.data && <AccessDialog bot={bot} name={name} now={access.data} onClose={() => setEditing(null)} />}
      {editing === 'stopped' && <StoppedDialog bot={bot} jobs={jobs} onClose={() => setEditing(null)} />}
    </>
  );
}

// ---------------------------------------------------------------- the fleet (settings.js fleet, a trial)

/* One table of agents, grouped by team, computer or subscription. A row is changed where it stands: its model, what
   its tasks run on, its computer and its limit. Check several to change their model or move them at once. */

type By = 'team' | 'computer' | 'subscription';
const BY = [
  { value: 'team', label: 'Team' },
  { value: 'computer', label: 'Computer' },
  { value: 'subscription', label: 'Subscription' },
] as const;
const THIRD: Record<By, string> = { team: 'Computer', computer: 'Team', subscription: 'Next' };

const useRows = () => useQuery(agentsQuery).data;
const useMachines = () => useQuery(computersQuery).data;
/** The server also sends where an agent compacts and what its tasks run on. */
type Row = AgentRow & { compact_at?: number | null; task_model?: TaskModel | null };
type Machine = NonNullable<ReturnType<typeof useMachines>>[number];

const brief = (m?: string | null) => (m ?? '').replace(/^Claude /, '');
const cap = (w?: string | null) => (w ? w.charAt(0).toUpperCase() + w.slice(1) : '');
const tokensK = (n?: number | null) => (!n ? '' : n >= 1_000_000 ? `${+(n / 1_000_000).toFixed(1)}M` : `${Math.round(n / 1000)}K`);

/** "Haiku 5.5, High, 400K": the model without its maker's name, its effort, where it compacts. */
export function modelWords(r?: Row): string {
  return [brief(r?.resolved_model_label ?? r?.resolved_model ?? r?.model), cap(r?.resolved_effort ?? r?.reasoning_effort), tokensK(r?.compact_at)]
    .filter(Boolean)
    .join(', ');
}

function MoveItems({ machines, rows, onPick }: { machines: Machine[]; rows: Row[]; onPick: (m: Machine) => void }) {
  return (
    <>
      {machines.map((m) => {
        const n = rows.filter((r) => r.machine?.runner_id === m.id).length;
        return (
          <MenuItem
            key={m.id}
            label={m.label || m.id}
            meta={`${n ? agentsWord(n) : 'No agents'}${m.online ? '' : ', off'}`}
            onSelect={() => onPick(m)}
          />
        );
      })}
    </>
  );
}

type Change = { what: 'model' | 'tasks' | 'limit'; slug: string; name: string };

function TasksDialog({ slug, name, now, onClose }: { slug: string; name: string; now?: TaskModel | null; onClose: () => void }) {
  const models = useQuery(modelsQuery);
  const [model, setModel] = useState(now?.model ?? '');
  const [effort, setEffort] = useState(now?.reasoning_effort ?? '');
  const [busy, setBusy] = useState(false);
  const v = models.data;
  const on = v?.enabled_providers;
  const usable = (v?.models ?? []).filter((m) => !m.deprecated && (!on || on.includes(m.provider)));
  const efforts = usable.find((m) => m.id === model)?.efforts ?? [];
  const save = async () => {
    setBusy(true);
    const r = await run('settings.taskModel', { agent: slug, model, ...(model && effort ? { effort } : {}) }, { via: 'click' });
    setBusy(false);
    if (!r.ok) return toast({ text: "Couldn't change it", kind: 'failed' });
    toast({ text: r.did ?? 'Changed', kind: 'done' });
    onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`${name}'s tasks`}
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} onClick={() => void save()}>
            Save
          </Button>
        </>
      }
    >
      <div className="set-add">
        <Select
          label="Model"
          value={model}
          options={[{ value: '', label: 'Same as the agent' }, ...usable.map((m) => ({ value: m.id, label: m.label }))]}
          onValueChange={(m) => {
            setModel(m);
            if (!usable.find((x) => x.id === m)?.efforts?.includes(effort)) setEffort('');
          }}
        />
        {efforts.length > 0 && (
          <Select
            label="Effort"
            value={effort}
            options={[{ value: '', label: 'Default' }, ...efforts.map((e) => ({ value: e, label: cap(e) }))]}
            onValueChange={setEffort}
          />
        )}
      </div>
    </Dialog>
  );
}

function LimitDialog({ slug, name, now, onClose }: { slug: string; name: string; now?: number | null; onClose: () => void }) {
  const [value, setValue] = useState(now != null ? String(now) : '');
  const [busy, setBusy] = useState(false);
  const n = value.trim() === '' ? undefined : Number(value);
  const bad = n !== undefined && (!Number.isFinite(n) || n < 0);
  const save = async () => {
    setBusy(true);
    const ok = await act('usage.setLimit', { agent: slug, ...(n !== undefined ? { monthly: n } : {}) });
    setBusy(false);
    if (ok) onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`${name}'s limit`}
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} disabled={bad} onClick={() => void save()}>
            Save
          </Button>
        </>
      }
    >
      <Field label="Dollars a month" type="number" value={value} onValueChange={setValue} error={bad ? 'Not a number' : undefined} autoFocus />
    </Dialog>
  );
}

interface LineProps {
  a: Actor;
  slug: string;
  r?: Row;
  by: By;
  picked: boolean;
  open: boolean;
  owner?: Actor;
  machines: Machine[];
  next: string;
  limit?: { monthly_usd?: number | null; blocked?: string | null; may_edit?: boolean };
  onPick: (on: boolean) => void;
  onEdit: (e: Change) => void;
  /** Grouped by computer: the row drags onto another computer's group. */
  drag?: HTMLAttributes<HTMLDivElement>;
}

function FleetLine({ a, slug, r, by, picked, open, owner, machines, next, limit, onPick, onEdit, drag }: LineProps) {
  const models = useQuery(modelsQuery);
  const manage = r?.can_manage !== false;
  const tm = r?.task_model;
  const tasks = tm?.model ? brief(models.data?.models.find((m) => m.id === tm.model)?.label ?? tm.model) : '';
  const at = r?.machine?.runner_id;
  const computer = r?.machine?.label ?? machines.find((m) => m.id === at)?.label;
  const edit = (what: Change['what']) => () => onEdit({ what, slug, name: a.name });
  return (
    <div
      className={['row g-bots g-fleet', picked && 'is-selected', limit?.blocked && 'is-bad'].filter(Boolean).join(' ')}
      title={a.name}
      data-target={`agent:${slug}`}
      {...drag}
    >
      <Checkbox label={<span className="set-hide">{`Check ${a.name}`}</span>} checked={picked} disabled={!manage} onCheckedChange={onPick} />
      <Avatar kind="agent" name={a.name} hue={a.hue} decorative />
      <Link to="/settings/$section" params={{ section: 'agents' }} search={open ? {} : { open: slug }} className="row-title" aria-expanded={open}>
        {a.name}
      </Link>
      <ValueButton label={`${a.name}'s model`} disabled={!manage} onClick={edit('model')}>
        {modelWords(r) || 'None'}
      </ValueButton>
      {tm?.model || tm?.reasoning_effort ? (
        <ValueButton label={`${a.name}'s tasks`} more={cap(tm.reasoning_effort)} className="c-tasks" disabled={!manage} onClick={edit('tasks')}>
          {tasks || 'Same'}
        </ValueButton>
      ) : (
        <ValueButton label={`${a.name}'s tasks`} title="The agent's own model" className="c-tasks" muted disabled={!manage} onClick={edit('tasks')}>
          Same
        </ValueButton>
      )}
      <span className="row-own c-own">{owner && <Avatar kind="person" name={owner.name} hue={owner.hue} />}</span>
      {by === 'team' ? (
        <Menu
          align="start"
          trigger={
            <ValueButton label={`${a.name}'s computer`} muted={!computer} disabled={!manage}>
              {computer ?? 'No computer'}
            </ValueButton>
          }
        >
          {machines.map((m) => (
            <MenuItem
              key={m.id}
              label={m.label || m.id}
              meta={m.online ? undefined : 'Off'}
              on={m.id === at}
              onSelect={() => void act('agents.move', { slug, computer: m.id })}
            />
          ))}
        </Menu>
      ) : (
        <span className="row-cell">{by === 'computer' ? (a.team ?? 'No team') : next}</span>
      )}
      <button type="button" className="fl-lim" title="Limit a month" aria-label={`${a.name}'s limit`} disabled={limit?.may_edit === false} onClick={edit('limit')}>
        {limit?.monthly_usd != null ? usd(limit.monthly_usd) : 'None'}
      </button>
      <Menu trigger={<IconButton icon="more" label={`More for ${a.name}`} size="sm" quiet className="fl-more" />}>
        <MenuItem label="Model" meta={modelWords(r) || 'None'} disabled={!manage} onSelect={edit('model')} />
        <MenuItem label="Tasks" meta={tasks || 'Same'} disabled={!manage} onSelect={edit('tasks')} />
        <MenuItem label="Limit" meta={limit?.monthly_usd != null ? usd(limit.monthly_usd) : 'None'} disabled={limit?.may_edit === false} onSelect={edit('limit')} />
      </Menu>
    </div>
  );
}

interface Group {
  key: string;
  name: string;
  slugs: string[];
}

export function AgentList({ open }: { open?: string }) {
  const navigate = useNavigate();
  const dir = useQuery(directoryQuery);
  const subs = useQuery(subscriptionsQuery);
  const limits = useQuery(limitsQuery);
  const bots = useQuery({ ...botsQuery, retry: false });
  const mayRestore = useMayRestore();
  const [q, setQ] = useState('');
  const [by, setBy] = useState<By>(open?.startsWith('computer:') ? 'computer' : 'team');
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  const [changing, setChanging] = useState(false);
  const [editing, setEditing] = useState<Change | null>(null);
  const all = (dir.data?.actors ?? []).filter((a) => a.kind === 'agent');
  const rows = (useRows() ?? []) as Row[];
  const live = useMachines() ?? [];
  const plans = (subs.data?.subscriptions ?? []) as Subscription[];
  const slugOf = (a: Actor) => a.slug ?? bare(a.id);
  const rowOf = (slug: string) => rows.find((r) => r.slug === slug);
  const listedOf = (slug: string) => bots.data?.find((b) => b.slug === slug || b.id === rowOf(slug)?.id);
  const onPlan = (slug: string, fallback: boolean) => {
    const id = rowOf(slug)?.id;
    return plans.find((s) => (s.agents ?? []).some((x) => (x.id === id || x.id === slug) && !!x.fallback === fallback));
  };
  const computerOf = (slug: string) => {
    const m = rowOf(slug)?.machine;
    return m?.label ?? live.find((c) => c.id === m?.runner_id)?.label ?? '';
  };
  const term = q.trim().toLowerCase();
  const shown = term
    ? all.filter((a) => {
        const r = rowOf(slugOf(a));
        return [a.name, a.team, r?.resolved_model_label, r?.resolved_model, computerOf(slugOf(a))].some((w) => w?.toLowerCase().includes(term));
      })
    : all;
  const checked = [...picked].filter((s) => shown.some((a) => slugOf(a) === s));
  const retired = (bots.data ?? []).filter((b) => b.state === 'archived' && mayRestore(b));

  // A computer or a subscription with no agents is still a group; an agent sits under the plan it uses first.
  const groups: Group[] = [];
  const put = (key: string, name: string, slug?: string) => {
    let g = groups.find((x) => x.key === key);
    if (!g) groups.push((g = { key, name, slugs: [] }));
    if (slug) g.slugs.push(slug);
  };
  if (by === 'computer') live.forEach((m) => put(`computer:${m.id}`, m.label || m.id));
  if (by === 'subscription') plans.forEach((s) => put(`subscription:${s.id}`, s.name));
  for (const a of shown) {
    const slug = slugOf(a);
    if (by === 'team') put(`team:${a.team ?? ''}`, a.team ?? 'No team', slug);
    else if (by === 'computer') {
      const m = rowOf(slug)?.machine?.runner_id;
      if (m && live.some((c) => c.id === m)) put(`computer:${m}`, '', slug);
      else put('computer:', 'No computer', slug);
    } else {
      const s = onPlan(slug, false);
      put(s ? `subscription:${s.id}` : 'subscription:', s?.name ?? 'No subscription', slug);
    }
  }

  const moveTo = async (agents: string[], computer: string) => {
    const r = await run('settings.moveAgents', { agents, computer }, { via: 'click' });
    toast(r.ok ? { text: r.did ?? 'Moved', kind: 'done' } : { text: "Couldn't move them", kind: 'failed' });
    if (r.ok) setPicked(new Set());
  };
  const move = (m: Machine) => moveTo(checked, m.id);
  // A checked row carries the other checked rows with it; agents already there stay put.
  const drag = useRowDrag((slug, computer) => {
    const agents = (picked.has(slug) ? checked : [slug]).filter((s) => {
      const r = rowOf(s);
      return r?.can_manage !== false && r?.machine?.runner_id !== computer;
    });
    if (agents.length) void moveTo(agents, computer);
  });
  const opened = open?.startsWith('computer:') ? open.slice('computer:'.length) : '';
  const machineOf = (key: string) => (key.startsWith('computer:') ? live.find((m) => `computer:${m.id}` === key) : undefined);

  const line = (a: Actor) => {
    const slug = slugOf(a);
    const r = rowOf(slug);
    const isOpen = open === slug;
    const owner = r?.owner_id ? dir.data?.byId[`human:${bare(r.owner_id)}`] : undefined;
    const el = (
      <FleetLine
        key={a.id}
        a={a}
        slug={slug}
        r={r}
        by={by}
        picked={picked.has(slug)}
        open={isOpen}
        owner={owner}
        machines={live}
        next={onPlan(slug, true)?.name ?? 'None'}
        limit={limits.data?.bots[slug]}
        onPick={(on) => setPicked((s) => toggled(s, slug, on))}
        onEdit={setEditing}
        drag={by === 'computer' && r?.can_manage !== false ? drag(slug) : undefined}
      />
    );
    return isOpen ? (
      <div key={a.id} className="open">
        {el}
        <AgentOpen slug={slug} name={a.name} listed={listedOf(slug)} />
      </div>
    ) : (
      el
    );
  };

  const editRow = editing ? rowOf(editing.slug) : undefined;
  return (
    <>
      {all.length === 0 ? (
        <Empty title="No agents yet" />
      ) : (
        <div className="fl">
          <div className="list-tools fl-tools" data-target="setting:agents">
            {checked.length > 0 ? (
              <span className="bulk">
                <b>{checked.length} checked</b>
                <Button onClick={() => setChanging(true)}>Change model</Button>
                <Menu align="start" trigger={<Button>Move</Button>}>
                  <MoveItems machines={live} rows={rows} onPick={(m) => void move(m)} />
                </Menu>
                <Verb onClick={() => setPicked(new Set())}>Clear</Verb>
              </span>
            ) : (
              <>
                <Search label="Search agents, models or computers" placeholder="Search agents, models or computers" value={q} onValueChange={setQ} />
                <span className="tools-n">{agentsWord(shown.length)}</span>
              </>
            )}
            <span className="fl-by">
              <span>Group by</span>
              <Segmented label="Group by" options={BY} value={by} onValueChange={setBy} />
            </span>
          </div>
          <div className="tbl-head g-bots g-fleet" aria-hidden="true">
            <span />
            <span />
            <span>Agent</span>
            <span>Model</span>
            <span className="c-tasks">Tasks</span>
            <span className="c-own is-mid">Owner</span>
            <span>{THIRD[by]}</span>
            <span className="is-num">Limit</span>
          </div>
          {groups.map((g) => {
            const m = machineOf(g.key);
            return (
            <Section
              key={g.key}
              name={g.name}
              count={g.slugs.length}
              target={`group:${g.key}`}
              summary={
                m && (
                  <Link
                    to="/settings/$section"
                    params={{ section: 'agents' }}
                    search={opened === m.id ? {} : { open: `computer:${m.id}` }}
                    className={opened === m.id ? 'fl-sum is-on' : 'fl-sum'}
                    aria-label={`Open ${m.label || m.id}`}
                    aria-expanded={opened === m.id}
                  >
                    <Word fine={m.online}>{m.restarting ? 'Restarting' : m.online ? 'Online' : 'Offline'}</Word>
                    <Icon name="panel" />
                  </Link>
                )
              }
            >
              {g.slugs.length === 0 ? (
                <div className="fl-none">
                  <span>No agents yet</span>
                </div>
              ) : (
                g.slugs.map((slug) => {
                  const a = shown.find((x) => slugOf(x) === slug);
                  return a ? line(a) : null;
                })
              )}
            </Section>
            );
          })}
        </div>
      )}
      {opened && <ComputerPanel id={opened} onClose={() => void navigate({ to: '/settings/$section', params: { section: 'agents' }, search: {} })} />}
      {retired.length > 0 && (
        <Section name="Retired" count={retired.length} defaultOpen={false} target="setting:retired">
          {retired.map((b) => {
            const key = b.id ?? b.slug ?? '';
            return (
              <div key={key} className="row g-ret" title={b.display_name || b.slug}>
                <span className="row-title">{b.display_name || b.slug}</span>
                <Verb onClick={() => void act('settings.restoreAgent', { agent: key })}>Restore</Verb>
              </div>
            );
          })}
        </Section>
      )}
      {changing && <ModelsDialog agents={checked} onClose={() => setChanging(false)} onDone={() => setPicked(new Set())} />}
      {editing?.what === 'model' && editRow && <ModelPicker agent={editRow} open onOpenChange={(o) => !o && setEditing(null)} />}
      {editing?.what === 'tasks' && <TasksDialog slug={editing.slug} name={editing.name} now={editRow?.task_model} onClose={() => setEditing(null)} />}
      {editing?.what === 'limit' && (
        <LimitDialog slug={editing.slug} name={editing.name} now={limits.data?.bots[editing.slug]?.own_monthly_usd} onClose={() => setEditing(null)} />
      )}
    </>
  );
}
