import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import { run } from '@/actions/run';
import { directoryQuery, meQuery } from '@/data';
import { agentsQuery } from '@/features/team/exports';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Checkbox } from '@/kit/Checkbox';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field, Search } from '@/kit/Field';
import { Facts } from '@/kit/LabelValue';
import { Link as To } from '@/kit/Link';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { Section } from '@/pagekinds';
import { Foot, Line, Meter, SwitchLine, Verb, act, usd } from './parts';
import {
  SAFE_RETRY,
  accessQuery,
  botAccessQuery,
  botMailQuery,
  botsQuery,
  limitsQuery,
  modelsQuery,
  stoppedRunsQuery,
  type Audience,
  type BotAccess,
  type BotListed,
  type StoppedRun,
} from './queries';

/* Settings › Agents, the list (pages/8-settings/settings.js botsDesk): search, check several and change their model at
   once; open one in place for its model, owners, access, mail and stopped runs; bring back a retired one. */

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

const toggled = (s: Set<string>, id: string, on: boolean) => {
  const next = new Set(s);
  if (on) next.add(id);
  else next.delete(id);
  return next;
};

// ---------------------------------------------------------------- change the model, for one agent or several

export function ModelDialog({ agents, onClose, onDone }: { agents: string[]; onClose: () => void; onDone?: () => void }) {
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
      description={agents.length > 1 ? `${agents.length} agents` : undefined}
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
          {manage && <Verb onClick={() => setEditing('model')}>Change</Verb>}
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
      {editing === 'model' && <ModelDialog agents={[slug]} onClose={() => setEditing(null)} />}
      {editing === 'owners' && listed && <OwnersDialog bot={listed} name={name} listed={owners} onClose={() => setEditing(null)} />}
      {editing === 'access' && access.data && <AccessDialog bot={bot} name={name} now={access.data} onClose={() => setEditing(null)} />}
      {editing === 'stopped' && <StoppedDialog bot={bot} jobs={jobs} onClose={() => setEditing(null)} />}
    </>
  );
}

// ---------------------------------------------------------------- the list

export function AgentList({ open }: { open?: string }) {
  const dir = useQuery(directoryQuery);
  const agents = useQuery(agentsQuery);
  const limits = useQuery(limitsQuery);
  const bots = useQuery({ ...botsQuery, retry: false });
  const mayRestore = useMayRestore();
  const [q, setQ] = useState('');
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  const [changing, setChanging] = useState(false);
  const all = (dir.data?.actors ?? []).filter((a) => a.kind === 'agent');
  const rowOf = (slug: string) => agents.data?.find((r) => r.slug === slug);
  const listedOf = (slug: string) => bots.data?.find((b) => b.slug === slug || b.id === rowOf(slug)?.id);
  const term = q.trim().toLowerCase();
  const shown = term
    ? all.filter((a) => {
        const r = rowOf(a.slug ?? '');
        return [a.name, a.team, r?.resolved_model_label, r?.resolved_model].some((w) => w?.toLowerCase().includes(term));
      })
    : all;
  const checked = [...picked].filter((s) => shown.some((a) => a.slug === s));
  const retired = (bots.data ?? []).filter((b) => b.state === 'archived' && mayRestore(b));
  return (
    <>
      <Section name="Agents" count={all.length} target="setting:agents">
        {all.length === 0 ? (
          <Empty title="No agents yet" />
        ) : (
          <>
            <div className="list-tools">
              <Search label="Search agents or models" placeholder="Search agents or models" value={q} onValueChange={setQ} />
              {checked.length > 0 ? (
                <span className="bulk">
                  <b>{checked.length} checked</b>
                  <Button onClick={() => setChanging(true)}>Change model</Button>
                  <Verb onClick={() => setPicked(new Set())}>Clear</Verb>
                </span>
              ) : (
                <span className="tools-n">{`${shown.length} ${shown.length === 1 ? 'agent' : 'agents'}`}</span>
              )}
            </div>
            <div className="tbl-head g-bots" aria-hidden="true">
              <span />
              <span />
              <span>Agent</span>
              <span>Model</span>
              <span>Team</span>
              <span>This month</span>
              <span className="is-num">Limit</span>
            </div>
            {shown.map((a) => {
              const slug = a.slug ?? bare(a.id);
              const l = limits.data?.bots[slug];
              const isOpen = open === slug;
              const line = (
                <div
                  key={a.id}
                  className={['row g-bots', picked.has(slug) && 'is-selected', l?.blocked && 'is-bad'].filter(Boolean).join(' ')}
                  title={a.name}
                  data-target={`agent:${slug}`}
                >
                  <Checkbox
                    label={<span className="set-hide">{`Check ${a.name}`}</span>}
                    checked={picked.has(slug)}
                    disabled={rowOf(slug)?.can_manage === false}
                    onCheckedChange={(on) => setPicked((s) => toggled(s, slug, on))}
                  />
                  <Avatar kind="agent" name={a.name} hue={a.hue} decorative />
                  <Link
                    to="/settings/$section"
                    params={{ section: 'agents' }}
                    search={isOpen ? {} : { open: slug }}
                    className="row-title"
                    aria-expanded={isOpen}
                  >
                    {a.name}
                  </Link>
                  <span className="row-cell">{rowOf(slug)?.resolved_model_label ?? ''}</span>
                  <span className="row-cell">{a.team ?? ''}</span>
                  <span className="row-cell">{l ? <Meter pct={l.percent} over={!!l.blocked} /> : null}</span>
                  <span className="row-fig">{l?.monthly_usd != null ? usd(l.monthly_usd) : 'None'}</span>
                </div>
              );
              return isOpen ? (
                <div key={a.id} className="open">
                  {line}
                  <AgentOpen slug={slug} name={a.name} listed={listedOf(slug)} />
                </div>
              ) : (
                line
              );
            })}
          </>
        )}
      </Section>
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
      {changing && <ModelDialog agents={checked} onClose={() => setChanging(false)} onDone={() => setPicked(new Set())} />}
    </>
  );
}
