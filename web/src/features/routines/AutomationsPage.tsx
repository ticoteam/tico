import { useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { useActors } from '@/data';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Link } from '@/kit/Link';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { Row } from '@/kit/Row';
import { useCards } from '@/kit/Popover';
import { Segmented } from '@/kit/Segmented';
import { EmptyRow, GroupRow, Table, Td, Th, Tr } from '@/kit/Table';
import { toast } from '@/kit/Toast';
import { List, PageMore, Section, type PageTab } from '@/pagekinds';
import { automationHref, BUILTINS, ROUTINES, type Builtin, type RoutineState } from './actions';
import { BuiltinPanel } from './BuiltinPanel';
import { NewInbox } from './Templates';
import {
  KIND_ICONS,
  KIND_WORDS,
  KINDS,
  kindOf,
  money,
  nextWords,
  RANGE_BUCKET,
  RANGES,
  sourceWords,
  stateOf,
  STATE_WORDS,
  WHEN,
  type AutoState,
  type GroupBy,
  type Range,
  type SortBy,
} from './flow';
import { Bars, Facet, ListChoices, Rail, type Bucket, type Choice } from './overview';
import { actorKey, Face, Find, PageOwner, press, Word } from './parts';
import { heartbeatQuery, hooksQuery, maintenanceQuery, routinesQuery, statsQuery, systemJobsQuery, type RunStatus } from './queries';
import { RoutinePanel } from './RoutinePanel';
import { RunsTab } from './RunsTab';
import type { Hook, Routine, SystemJob } from './types';
import { HookDialog, WebhookPanel } from './WebhookPanel';
import { maintenanceWords, scheduleWords, startsWords } from './words';
import './routines.css';
import './automations.css';

/* Automations (ui/app/automations-overview.ts): the filters down the left with a count per choice, then every
   automation in a dense table with its last run, its next, and its runs, failures and cost over the time range, grouped
   by what starts it. Runs is every run across them with a chart; Webhooks the incoming ones. One automation opens as its
   own page; Tico's own jobs and a webhook open in `?panel=`. */

export interface RoutinesSearch {
  tab?: 'webhooks' | 'runs';
  panel?: string;
  /** Runs: which runs. */
  status?: RunStatus;
  /** The counts' and the chart's time range. */
  range?: Range;
  /** Automations: the words searched for, and which state shows. */
  q?: string;
  state?: RoutineState;
}

/** `/company/automations?…`: the address a row or a filter opens. */
export const routinesHref = (search: RoutinesSearch) => {
  const q = new URLSearchParams(Object.entries(search).filter((e): e is [string, string] => !!e[1])).toString();
  return q ? `${ROUTINES}?${q}` : ROUTINES;
};

export const routinesTabs = (range?: Range): readonly PageTab[] => [
  { label: 'Automations', to: ROUTINES, search: { tab: '', ...(range ? { range } : {}) }, target: 'tab:routines' },
  { label: 'Runs', to: ROUTINES, search: { tab: 'runs', ...(range ? { range } : {}) }, target: 'tab:runs' },
  { label: 'Webhooks', to: ROUTINES, search: { tab: 'webhooks' }, target: 'tab:webhooks' },
];

export const automationsHead = (range?: Range) => ({ title: 'Automations', path: ROUTINES, tabs: routinesTabs(range), className: 'rr au' });
/** The range the address names, else the last hour (as the old page opened). */
export const rangeOf = (s: { range?: Range }): Range => s.range ?? '1h';

export function RoutinesPage({ search }: { search: RoutinesSearch }) {
  if (search.tab === 'runs') return <RunsTab search={search} />;
  return search.tab === 'webhooks' ? <WebhooksTab panel={search.panel} /> : <AutomationsTab search={search} />;
}
export { RoutinesPage as AutomationsPage };

const close = () => void press('automations.closePanel', {});

// ---------------------------------------------------------------- the rows

/** One row of the list: an automation, or one of Tico's own jobs (which open in the panel). */
interface Item {
  id: string;
  name: string;
  kind: string;
  owner: string | null;
  team: string;
  state: AutoState;
  trigger: string;
  failed: boolean;
  at?: string | null;
  next?: string;
  href: string;
  builtin?: Builtin;
  hold: boolean;
  pending: number;
  problems: string[];
}

const BUILTIN_STATS: Record<Builtin, string> = { maintenance: 'maintenance', heartbeat: 'heartbeat', system: 'system-jobs' };
const lastJob = (jobs: SystemJob[] | undefined) =>
  jobs?.reduce<string | null>((at, j) => (j.last?.started_at && (!at || j.last.started_at > at) ? j.last.started_at : at), null);

function useItems(search: RoutinesSearch): { items: Item[]; none: boolean } {
  const all = useSuspenseQuery(routinesQuery).data;
  const hooks = useQuery({ ...hooksQuery, enabled: all.some((r) => r.trigger.type === 'webhook' && !!r.trigger.id) });
  const actors = useActors();
  const m = useQuery(maintenanceQuery);
  const h = useQuery(heartbeatQuery);
  const j = useQuery(systemJobsQuery);
  const by = new Map<string, { name: string; team?: string }>();
  for (const a of actors) {
    by.set(a.id, a);
    if (a.slug) by.set(`bot:${a.slug}`, a);
  }
  const who = (id: string) => by.get(actorKey(id) ?? '')?.name ?? '';
  const hookName = (id: string) => hooks.data?.find((x: Hook) => x.id === id)?.name;
  const keep = { range: search.range, q: search.q, state: search.state };
  const items: Item[] = all.map((r: Routine) => ({
    id: r.id,
    name: r.name || 'Untitled',
    kind: kindOf(r),
    owner: r.owner_id ?? null,
    team: by.get(actorKey(r.owner_id) ?? '')?.team ?? '',
    state: stateOf(r),
    trigger: sourceWords(r.trigger, { who, hook: hookName, schedule: (c) => cap(scheduleWords(c)) }),
    failed: r.last_run?.status === 'failed',
    at: r.last_run?.started_at ?? r.last_run?.finished_at,
    next:
      r.enabled === false || !r.next_run
        ? ''
        : r.trigger.type === 'due' && r.next_due_at
          ? nextWords(r.next_run)
          : nextWords(r.next_run),
    href: automationHref(r.id),
    hold: !!r.hold,
    pending: r.pending ?? 0,
    problems: [...new Set((r.problems ?? []).map((p) => p.step))],
  }));
  const tico = { owner: null, team: '', kind: 'system', failed: false, hold: false, pending: 0, problems: [] as string[] };
  const builtins: (Item | null)[] = [
    m.data
      ? { ...tico, id: 'maintenance', name: 'Maintenance', state: m.data.settings.paused ? 'off' : 'on', trigger: maintenanceWords(m.data.settings.schedule), at: m.data.last_run?.started_at, href: routinesHref({ ...keep, panel: 'builtin:maintenance' }), builtin: 'maintenance' }
      : null,
    h.data ? { ...tico, id: 'heartbeat', name: 'Heartbeat', state: 'on', trigger: `Every ${h.data.interval_s} s`, at: h.data.last_beat_at, href: routinesHref({ ...keep, panel: 'builtin:heartbeat' }), builtin: 'heartbeat' } : null,
    j.data ? { ...tico, id: 'system-jobs', name: 'System jobs', state: 'on', trigger: `${j.data.length} ${j.data.length === 1 ? 'job' : 'jobs'}`, at: lastJob(j.data), href: routinesHref({ ...keep, panel: 'builtin:system' }), builtin: 'system' } : null,
  ];
  return { items: [...items, ...builtins.filter((x): x is Item => !!x)], none: !all.length };
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

interface Stat {
  runs: number;
  failed: number;
  cost: number;
  buckets: Bucket[];
}
const NO_STAT: Stat = { runs: 0, failed: 0, cost: 0, buckets: [] };

// ---------------------------------------------------------------- the list

interface Picks {
  kinds: string[];
  team: string;
  owner: string;
}

function AutomationsTab({ search }: { search: RoutinesSearch }) {
  const { q, state, panel } = search;
  const range = rangeOf(search);
  const phone = useCards();
  const { items, none } = useItems(search);
  const stats = useQuery(statsQuery(range));
  const actors = useActors();
  const [picks, setPicks] = useState<Picks>({ kinds: [], team: '', owner: '' });
  const [group, setGroup] = useState<GroupBy>('trigger');
  const [sort, setSort] = useState<SortBy>('last');
  const [closed, setClosed] = useState<string[]>([]);
  // the filters show beside the list on a computer, and under the head on a phone once asked for
  const [filters, setFilters] = useState(!phone);
  const [inbox, setInbox] = useState(false);
  const built = panel?.startsWith('builtin:') ? (BUILTINS.find((b) => `builtin:${b}` === panel) ?? null) : null;
  const listed = (to: RoutinesSearch) => void press('automations.list', { q: to.q, state: to.state });
  const setRange = (r: Range) => void press('automations.list', { q, state, range: r });

  const per = new Map<string, Stat>((stats.data?.automations ?? []).map((s) => [s.id, s]));
  const statOf = (it: Item): Stat => per.get(it.builtin ? BUILTIN_STATS[it.builtin] : it.id) ?? NO_STAT;
  const nameOf = (id: string | null) => (id ? (actors.find((a) => a.id === actorKey(id) || (a.slug && `bot:${a.slug}` === id))?.name ?? id.replace(/^\w+:/, '')) : 'Tico');
  const words = (it: Item) => `${it.name} ${it.trigger} ${nameOf(it.owner)} ${KIND_WORDS[it.kind] ?? ''}`.toLowerCase();
  const matches = (it: Item, skip?: keyof Picks | 'state') =>
    (skip === 'kinds' || !picks.kinds.length || picks.kinds.includes(it.kind)) &&
    (skip === 'state' || !state || it.state === state) &&
    (skip === 'team' || !picks.team || it.team === picks.team) &&
    (skip === 'owner' || !picks.owner || ownerKey(it) === picks.owner) &&
    (!q || words(it).includes(q.toLowerCase()));
  const shown = items.filter((it) => matches(it));
  const count = (skip: keyof Picks | 'state', f: (it: Item) => boolean) => items.filter((it) => matches(it, skip) && f(it)).length;

  // sorted, then grouped in the old order: what starts it, by owner, or not at all
  const lastAt = (it: Item) => it.at ?? '';
  const order: Record<SortBy, (a: Item, b: Item) => number> = {
    last: (a, b) => lastAt(b).localeCompare(lastAt(a)),
    fail: (a, b) => statOf(b).failed - statOf(a).failed || statOf(b).runs - statOf(a).runs,
    runs: (a, b) => statOf(b).runs - statOf(a).runs,
    cost: (a, b) => statOf(b).cost - statOf(a).cost,
    name: (a, b) => a.name.localeCompare(b.name),
  };
  const rows = [...shown].sort(order[sort]);
  const keyOf = (it: Item) => (group === 'trigger' ? it.kind : group === 'owner' ? ownerKey(it) : '');
  const groups: { key: string; label: string; rows: Item[] }[] = [];
  for (const it of rows) {
    const k = keyOf(it);
    let g = groups.find((x) => x.key === k);
    if (!g) groups.push((g = { key: k, label: group === 'trigger' ? (KIND_WORDS[k] ?? cap(k)) : nameOf(k === TICO ? null : k), rows: [] }));
    g.rows.push(it);
  }
  const rank = (k: string) => {
    const i = (KINDS as readonly string[]).indexOf(k);
    return i < 0 ? KINDS.length : i;
  };
  if (group === 'trigger') groups.sort((a, b) => rank(a.key) - rank(b.key));
  if (group === 'owner') groups.sort((a, b) => a.label.localeCompare(b.label));

  // a column empty for every automation over the range leaves (decided from the whole list, so typing never moves one)
  const has = {
    next: items.some((it) => it.next),
    trend: items.some((it) => statOf(it).runs > 0),
    runs: items.some((it) => statOf(it).runs > 0),
    failed: items.some((it) => statOf(it).failed > 0),
    cost: items.some((it) => statOf(it).cost >= 0.005),
  };

  const pick = (key: 'team' | 'owner', v: string) => setPicks((p) => ({ ...p, [key]: p[key] === v ? '' : v }));
  const pickKind = (v: string) => setPicks((p) => ({ ...p, kinds: p.kinds.includes(v) ? p.kinds.filter((k) => k !== v) : [...p.kinds, v] }));
  const teams = [...new Set(items.map((it) => it.team).filter(Boolean))].sort();
  const owners = [...new Set(items.map(ownerKey))].map((o) => ({ o, name: nameOf(o === TICO ? null : o) })).sort((a, b) => a.name.localeCompare(b.name));
  const on = picks.kinds.length + +!!picks.team + +!!picks.owner + +!!state;

  const rail = (
    <Rail open={filters && !panel}>
      <Facet
        label="Trigger"
        picked={picks.kinds}
        onPick={pickKind}
        choices={KINDS.map((k) => ({ value: k, label: KIND_WORDS[k]!, icon: KIND_ICONS[k], count: count('kinds', (it) => it.kind === k) }))}
      />
      <Facet
        label="Status"
        picked={state ? [state] : []}
        onPick={(v) => listed({ q, state: state === v ? undefined : (v as RoutineState) })}
        choices={(Object.keys(STATE_WORDS) as AutoState[]).map((k) => ({ value: k, label: STATE_WORDS[k], icon: STATE_ICON[k], count: count('state', (it) => it.state === k) }))}
      />
      {teams.length > 0 && (
        <Facet label="Team" picked={picks.team ? [picks.team] : []} onPick={(v) => pick('team', v)} choices={teams.map((t) => ({ value: t, label: t, icon: 'people', count: count('team', (it) => it.team === t) }))} />
      )}
      <Facet
        label="Owner"
        picked={picks.owner ? [picks.owner] : []}
        onPick={(v) => pick('owner', v)}
        choices={owners.map(({ o, name }): Choice => ({ value: o, label: name, lead: o === TICO ? <Icon name="gear" /> : <Face id={o} size={16} />, count: count('owner', (it) => ownerKey(it) === o) }))}
      />
    </Rail>
  );

  const span = 4 + +has.next + +has.trend + +has.runs + +has.failed + +has.cost;
  const fold = (k: string) => setClosed((c) => (c.includes(k) ? c.filter((x) => x !== k) : [...c, k]));
  const emptyWords = items.length ? 'No automations match these filters' : 'No automations';
  const table = phone ? (
    <PhoneRows groups={groups} grouped={group !== 'none'} closed={closed} onFold={fold} statOf={statOf} range={range} empty={emptyWords} />
  ) : (
    <Table label="Automations" stick>
      <thead>
        <tr>
          <th scope="col" className="au-c-name">
            Name
          </th>
          <th scope="col" className="au-c-trig">
            Trigger
          </th>
          <Th>Owner</Th>
          <Th>Last run</Th>
          {has.next && (
            <th scope="col" className="au-c-next">
              Next
            </th>
          )}
          {has.trend && <th scope="col" className="au-c-trend">{`Last ${range}`}</th>}
          {has.runs && <Th num>Runs</Th>}
          {has.failed && <Th num>Failed</Th>}
          {has.cost && (
            <th scope="col" className="num au-c-cost">
              Cost
            </th>
          )}
        </tr>
      </thead>
      <tbody>
        {rows.length ? (
          groups.map((g) => (
            <GroupRows key={g.key} grouped={group !== 'none'} label={g.label} open={!closed.includes(g.key)} onFold={() => fold(g.key)} span={span} count={g.rows.length}>
              {g.rows.map((it) => (
                <ItemRow key={it.id} it={it} s={statOf(it)} has={has} range={range} selected={built !== null && it.builtin === built} />
              ))}
            </GroupRows>
          ))
        ) : (
          <EmptyRow span={span}>{emptyWords}</EmptyRow>
        )}
      </tbody>
    </Table>
  );

  return (
    <List
      {...automationsHead(search.range)}
      search={!none && <Find label="Search automations" value={q} onFind={(words) => listed({ q: words, state })} />}
      actions={
        <>
          {!phone && <Segmented label="Time range" options={RANGES.map((r) => ({ value: r, label: r }))} value={range} onValueChange={setRange} />}
          <IconButton icon="filter" label="Filters" on={(filters && !panel) || on > 0} onClick={() => setFilters(!filters || !!panel)} data-phone />
          <PageOwner page="automations" />
          <Menu trigger={<Button variant="primary">{phone ? 'Add' : 'Add automation'}</Button>}>
            {WHEN.map(([k, l]) => (
              <MenuItem key={k} label={l} icon={KIND_ICONS[k]} onSelect={() => void press('automations.start', { when: k })} />
            ))}
            <MenuRule />
            <MenuItem label="New inbox" icon="mail" onSelect={() => setInbox(true)} />
          </Menu>
          {inbox && <NewInbox onClose={() => setInbox(false)} />}
        </>
      }
      more={
        <PageMore>
          <ListChoices group={group} sort={sort} range={range} phone={phone} onGroup={(g) => (setGroup(g), setClosed([]))} onSort={setSort} onRange={setRange} />
        </PageMore>
      }
      sections={
        <div className="au-split">
          {rail}
          <div className="au-main">
            {table}
            {stats.isError && <p className="au-note">{"Counts didn't load"}</p>}
          </div>
        </div>
      }
      panel={panel === 'routine:new' ? <RoutinePanel id="new" onClose={close} /> : built ? <BuiltinPanel which={built} onClose={close} /> : null}
    />
  );
}

/** Tico's own jobs have no owner in the list: they are Tico's. */
const TICO = 'tico';
const ownerKey = (it: Item) => it.owner ?? TICO;

const STATE_ICON: Record<AutoState, 'check' | 'alert' | 'pause' | 'minus'> = { on: 'check', failed: 'alert', hold: 'pause', off: 'minus' };

function GroupRows({ grouped, label, open, onFold, span, count, children }: {
  grouped: boolean;
  label: string;
  open: boolean;
  onFold: () => void;
  span: number;
  count: number;
  children: ReactNode;
}) {
  if (!grouped) return children;
  return (
    <>
      <GroupRow name={label} count={count} open={open} onToggle={onFold} span={span} />
      {open && children}
    </>
  );
}

/** Its marks beside the name: off, on hold, a proposal waiting, a step its rule names wrongly. */
function Marks({ it }: { it: Item }) {
  return (
    <>
      {it.builtin && (
        <span className="au-mark" title="Built in">
          <Icon name="lock" />
        </span>
      )}
      {it.state === 'off' && (
        <span className="au-mark" title="Off">
          <Icon name="pause" />
        </span>
      )}
      {it.hold && <Word icon="pause">On hold</Word>}
      {it.pending > 0 && <Word icon="edit">Proposed</Word>}
      {it.problems.length > 0 && (
        <span className="au-mark" title={`Names a step its type does not have: ${it.problems.join(', ')}`}>
          <Icon name="alert" />
        </span>
      )}
    </>
  );
}

function LastRun({ it }: { it: Item }) {
  if (!it.at) return null;
  return it.failed ? (
    <span className="au-last is-failed" title="Last run failed">
      <Icon name="alert" />
      <Ago at={it.at} />
    </span>
  ) : (
    <Ago at={it.at} className="au-last" />
  );
}

function ItemRow({ it, s, has, range, selected }: { it: Item; s: Stat; has: Record<'next' | 'trend' | 'runs' | 'failed' | 'cost', boolean>; range: Range; selected: boolean }) {
  return (
    <Tr linked selected={selected} className={it.state === 'off' ? 'au-row is-off' : 'au-row'}>
      <td className="au-c-name">
        <span className="au-name">
          <Icon name={KIND_ICONS[it.kind] ?? 'spark'} />
          <Link href={it.href} data-target={`routine:${it.id}`}>
            {it.name}
          </Link>
          <Marks it={it} />
        </span>
      </td>
      <td className="au-c-trig" title={it.trigger}>
        {it.trigger}
      </td>
      <td className="au-c-own">{it.owner ? <Face id={it.owner} /> : <span className="av-gap" />}</td>
      <td className="au-c-last">
        <LastRun it={it} />
      </td>
      {has.next && <td className="au-c-next">{it.next}</td>}
      {has.trend && <td className="au-c-trend">{s.runs > 0 && <Bars buckets={s.buckets} label={`${s.runs} runs, ${s.failed} failed, each bar ${RANGE_BUCKET[range]}`} />}</td>}
      {has.runs && <Td num>{s.runs ? s.runs.toLocaleString() : ''}</Td>}
      {has.failed && <Td num>{s.failed ? <strong>{s.failed}</strong> : ''}</Td>}
      {has.cost && <td className="num au-c-cost">{s.cost >= 0.005 ? money(s.cost) : ''}</td>}
    </Tr>
  );
}

/** A phone's list: a table does not fit, so each automation is two lines, its name and marks over its facts. */
function PhoneRows({ groups, grouped, closed, onFold, statOf, range, empty }: {
  groups: { key: string; label: string; rows: Item[] }[];
  grouped: boolean;
  closed: string[];
  onFold: (k: string) => void;
  statOf: (it: Item) => Stat;
  range: Range;
  empty: string;
}) {
  if (!groups.some((g) => g.rows.length)) return <p className="au-note">{empty}</p>;
  const row = (it: Item) => {
    const s = statOf(it);
    const facts = [it.trigger, it.next, s.runs ? `${s.runs.toLocaleString()} ${s.runs === 1 ? 'run' : 'runs'}` : '', s.failed ? `${s.failed} failed` : '', s.cost >= 0.005 ? money(s.cost) : ''].filter(Boolean);
    return (
      <div key={it.id} className={it.state === 'off' ? 'au-prow is-off' : 'au-prow'}>
        <span className="au-prow-1">
          <Icon name={KIND_ICONS[it.kind] ?? 'spark'} />
          <Link href={it.href} className="au-prow-a" data-target={`routine:${it.id}`}>
            {it.name}
          </Link>
          <Marks it={it} />
          <span className="au-prow-end">
            <LastRun it={it} />
            {it.owner && <Face id={it.owner} />}
          </span>
        </span>
        {facts.length > 0 && <span className="au-prow-2">{facts.join(' · ')}</span>}
      </div>
    );
  };
  return (
    <div className="au-prows" aria-label={`Automations, ${range}`}>
      {groups.map((g) =>
        grouped ? (
          <Section key={g.key} name={g.label} count={g.rows.length} open={!closed.includes(g.key)} onOpenChange={() => onFold(g.key)}>
            {g.rows.map(row)}
          </Section>
        ) : (
          g.rows.map(row)
        ),
      )}
    </div>
  );
}

// ---------------------------------------------------------------- webhooks

function WebhooksTab({ panel }: { panel?: string }) {
  const list = useSuspenseQuery(hooksQuery).data;
  const [adding, setAdding] = useState(false);
  // a new webhook's full address, secret included, shown once: this state only, never the cache or the address
  const [made, setMade] = useState<{ hook: string; url: string; signing?: string } | null>(null);
  const open = panel?.startsWith('hook:') ? panel.slice(5) : null;
  const add = (
    <Button variant="primary" onClick={() => setAdding(true)}>
      Add webhook
    </Button>
  );
  return (
    <List
      {...automationsHead()}
      state={list.length ? 'ready' : 'empty'}
      empty={{ title: 'No webhooks yet', action: add }}
      actions={
        <>
          {list.length > 0 && add}
          {adding && <HookDialog onMade={setMade} onClose={() => setAdding(false)} />}
        </>
      }
      sections={
        <Section name="Incoming" count={list.length} target="section:incoming">
          {list.map((h) => (
            <HookRow key={h.id} h={h} selected={h.id === open} />
          ))}
        </Section>
      }
      panel={
        open && (
          <WebhookPanel
            key={open}
            id={open}
            shown={made?.hook === open ? made.url : null}
            signing={made?.hook === open ? made.signing : null}
            onShown={(url) => setMade({ hook: open, url })}
            onClose={() => {
              setMade(null);
              close();
            }}
          />
        )
      }
    />
  );
}

function HookRow({ h, selected }: { h: Hook; selected: boolean }) {
  const copy = () =>
    void navigator.clipboard.writeText(h.url).then(
      () => toast({ text: 'Address copied', kind: 'done' }),
      () => toast({ text: "Couldn't copy", kind: 'failed' }),
    );
  return (
    <Row className="hook" title={h.name} selected={selected} target={`hook:${h.id}`}>
      <Link className="row-title" href={routinesHref({ tab: 'webhooks', panel: `hook:${h.id}` })} aria-current={selected ? 'true' : undefined}>
        {h.name}
      </Link>
      <span className="row-sub">{startsWords(h)}</span>
      <span className="row-state">{h.signed === false && !h.builtin && <Word>Unsigned</Word>}</span>
      <span className="row-fig">{`${h.received_24h ?? 0} in 24 h`}</span>
      {h.last_received ? <Ago at={h.last_received} className="row-age" /> : <span className="row-age">Never</span>}
      {h.url ? <IconButton icon="link" label="Copy address" size="sm" quiet onClick={copy} /> : <span />}
    </Row>
  );
}
