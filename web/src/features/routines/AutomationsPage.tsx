import { useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { createContext, use, useState, type ReactNode } from 'react';
import { useActors } from '@/data';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Icon, type IconName } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Link } from '@/kit/Link';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { Row } from '@/kit/Row';
import { useCards } from '@/kit/Popover';
import { Segmented } from '@/kit/Segmented';
import { EmptyRow, GroupRow, Table, Td, Th, Tr } from '@/kit/Table';
import { toast } from '@/kit/Toast';
import { FilterRow, ItemHead, ListAndItem, PageMore, PanelSlot, Section, usePagePanelOpen } from '@/pagekinds';
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
import { Bars, ListChoices, type Bucket } from './overview';
import { actorKey, Face, Find, PageOwner, press, Word } from './parts';
import { heartbeatQuery, hooksQuery, maintenanceQuery, routinesQuery, RUN_STATUSES, statsQuery, systemJobsQuery, type RunStatus } from './queries';
import { RoutinePanel } from './RoutinePanel';
import { RunsTab } from './RunsTab';
import type { Hook, Routine, SystemJob } from './types';
import { HookDialog, WebhookPanel } from './WebhookPanel';
import { maintenanceWords, scheduleWords, startsWords } from './words';
import './routines.css';
import './automations.css';

/* Automations (ui/app/automations*.ts), as the old page was: a sidebar, then a lot of content, on one sheet (the kit's
   list-and-item page). The sidebar leads with the page's views, All automations, Runs and Webhooks, then is the old
   filter rail: Run, Trigger, Status, Team and Owner, each choice with its mark and its count. Picking one narrows the
   content's list. The content is All (every automation the filters leave in a dense table grouped by what starts it,
   with its last and next run, its bars, runs, failures and cost over the time range), Runs (the chart and every run),
   Webhooks, or the automation opened in full: its flow, its runs, its editors in the panel. Tico's own jobs and a
   webhook open in `?panel=`. */

export interface RoutinesSearch {
  /** The content when no automation is open: All (also on a phone, where the sidebar shows first), Runs, Webhooks. */
  tab?: 'all' | 'webhooks' | 'runs';
  panel?: string;
  /** Runs: which runs. */
  status?: RunStatus;
  /** The counts' and the chart's time range. */
  range?: Range;
  /** The words searched for, and which state shows. */
  q?: string;
  state?: RoutineState;
}

/** `/company/automations?…`: the address a row or a filter opens. */
export const routinesHref = (search: RoutinesSearch) => {
  const q = new URLSearchParams(Object.entries(search).filter((e): e is [string, string] => !!e[1])).toString();
  return q ? `${ROUTINES}?${q}` : ROUTINES;
};

/** The range the address names, else the last hour (as the old page opened). */
export const rangeOf = (s: { range?: Range }): Range => s.range ?? '1h';

const close = () => void press('automations.closePanel', {});

/** The time range, in the content's head on a computer (a phone picks it in the sidebar's "⋯"). */
export function RangePick({ range, onPick }: { range: Range; onPick: (r: Range) => void }) {
  if (useCards()) return null;
  return <Segmented label="Time range" options={RANGES.map((r) => ({ value: r, label: r }))} value={range} onValueChange={onPick} />;
}

// ---------------------------------------------------------------- the sidebar's choices, shared with the content

/** The sidebar's filters the address does not hold (status, run status and the search are in it): what starts it,
    one team, one owner. Any of the triggers picked, and all of the rest, as the old rail combined them. */
interface Picks {
  kinds: string[];
  team: string;
  owner: string;
}
interface Shell {
  picks: Picks;
  setPicks: (p: Picks) => void;
  group: GroupBy;
  sort: SortBy;
  closed: string[];
  fold: (k: string) => void;
}
const ShellCtx = createContext<Shell | null>(null);
const NO_PICKS: Picks = { kinds: [], team: '', owner: '' };
const useShell = (): Shell => use(ShellCtx) ?? { picks: NO_PICKS, setPicks: () => {}, group: 'trigger', sort: 'last', closed: [], fold: () => {} };
/** The sidebar's picks for the Runs view: what starts it and the owner go to the server; status, team and the search
    leave a list of automations, sent as ids while it is short (automations-overview.ts auAllQuery). */
export function useRunPicks(): { trigger?: string; owner?: string; picked: boolean } {
  const { picks } = useShell();
  return { trigger: picks.kinds.join(',') || undefined, owner: picks.owner || undefined, picked: !!(picks.kinds.length || picks.team || picks.owner) };
}

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

// ---------------------------------------------------------------- what the filters leave, in order and in groups

/** Tico's own jobs have no owner in the list: they are Tico's. */
const TICO = 'tico';
const ownerKey = (it: Item) => it.owner ?? TICO;

function useNameOf() {
  const actors = useActors();
  return (id: string | null) => (id && id !== TICO ? (actors.find((a) => a.id === actorKey(id) || (a.slug && `bot:${a.slug}` === id))?.name ?? id.replace(/^\w+:/, '')) : 'Tico');
}

/** The automations the search and the sidebar's filters leave, sorted and grouped as the sidebar's "⋯" says. The old
    rail's rule: any of the triggers picked, and the one status, team and owner picked; the search on top. */
function useShown(search: RoutinesSearch, statOf: (it: Item) => Stat = () => NO_STAT) {
  const { items, none } = useItems(search);
  const { picks, group, sort } = useShell();
  const nameOf = useNameOf();
  const { q, state } = search;
  const words = (it: Item) => `${it.name} ${it.trigger} ${nameOf(it.owner)} ${KIND_WORDS[it.kind] ?? ''}`.toLowerCase();
  const matches = (it: Item) =>
    (!picks.kinds.length || picks.kinds.includes(it.kind)) &&
    (!state || it.state === state) &&
    (!picks.team || it.team === picks.team) &&
    (!picks.owner || ownerKey(it) === picks.owner) &&
    (!q || words(it).includes(q.toLowerCase()));
  const lastAt = (it: Item) => it.at ?? '';
  const order: Record<SortBy, (a: Item, b: Item) => number> = {
    last: (a, b) => lastAt(b).localeCompare(lastAt(a)),
    fail: (a, b) => statOf(b).failed - statOf(a).failed || statOf(b).runs - statOf(a).runs,
    runs: (a, b) => statOf(b).runs - statOf(a).runs,
    cost: (a, b) => statOf(b).cost - statOf(a).cost,
    name: (a, b) => a.name.localeCompare(b.name),
  };
  const rows = items.filter(matches).sort(order[sort]);
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
  return { items, none, rows, groups, nameOf, grouped: group !== 'none', narrowed: !!(picks.kinds.length || picks.team || picks.owner || state || q) };
}

// ---------------------------------------------------------------- the sidebar: the old filter rail

/** The page: the sidebar (All automations, Runs, Webhooks, then the filters), and `children`, the content, beside it. */
export function AutomationsShell({ search, id, children }: { search: RoutinesSearch & { view?: string }; id?: string; children: ReactNode }) {
  const [picks, setPicks] = useState<Picks>(NO_PICKS);
  const [group, setGroup] = useState<GroupBy>('trigger');
  const [sort, setSort] = useState<SortBy>('last');
  const [closed, setClosed] = useState<string[]>([]);
  const fold = (k: string) => setClosed((c) => (c.includes(k) ? c.filter((x) => x !== k) : [...c, k]));
  const shell: Shell = { picks, setPicks, group, sort, closed, fold };
  return (
    <ShellCtx value={shell}>
      <Sidebar search={search} id={id} setGroup={(g) => (setGroup(g), setClosed([]))} setSort={setSort}>
        {children}
      </Sidebar>
    </ShellCtx>
  );
}

const STATE_MARK: Record<AutoState, IconName> = { on: 'check', failed: 'alert', hold: 'pause', off: 'minus' };
const RUN_WORDS: Record<RunStatus, string> = { failed: 'Failed', waiting: 'Waiting', running: 'Running', done: 'Done' };
const RUN_MARK: Record<RunStatus, IconName> = { failed: 'alert', waiting: 'pause', running: 'play', done: 'check' };

interface Choice {
  value: string;
  label: string;
  /** How many automations have it; a choice none has is left out unless picked. None for runs (the server counts none). */
  count?: number;
  icon?: IconName;
  lead?: ReactNode;
}

/** One section of the rail: its name, then a row per choice with its mark and its count. A pressed row is picked. */
function Facet({ name, choices, picked, onPick }: { name: string; choices: readonly Choice[]; picked: readonly string[]; onPick: (v: string) => void }) {
  const shown = choices.filter((c) => c.count === undefined || c.count > 0 || picked.includes(c.value));
  if (!shown.length) return null;
  return (
    <Section name={name} target={`facet:${name.toLowerCase()}`}>
      <div className="au-facet" role="group" aria-label={name}>
        {shown.map((c) => {
          const on = picked.includes(c.value);
          return (
            <button key={c.value} type="button" className={on ? 'row tl-row au-fb is-selected' : 'row tl-row au-fb'} aria-pressed={on} onClick={() => onPick(c.value)}>
              <span className="au-fb-lead">{c.lead ?? (c.icon ? <Icon name={c.icon} /> : null)}</span>
              <span className="row-title">{c.label}</span>
              <span className="row-age">{c.count ?? ''}</span>
            </button>
          );
        })}
      </div>
    </Section>
  );
}

function Sidebar({ search, id, setGroup, setSort, children }: { search: RoutinesSearch; id?: string; setGroup: (g: GroupBy) => void; setSort: (s: SortBy) => void; children: ReactNode }) {
  const { q, state, panel, tab, status } = search;
  const range = rangeOf(search);
  const phone = useCards();
  const { picks, setPicks, group, sort } = useShell();
  const { items, none, nameOf } = useShown(search);
  const [inbox, setInbox] = useState(false);
  // an editor open in the panel takes the sidebar's room: the flow and its editor need it more
  const editing = usePagePanelOpen();
  const runs = !id && tab === 'runs';
  const keep = { q, state, range: search.range };
  /** A filter shows its list (as the old rail did): the runs while Runs is showing, else All, an open one closing. */
  const filter = (to: { q?: string; state?: RoutineState; status?: RunStatus; runs?: boolean }) =>
    void press('automations.list', { q: to.q, state: to.state, range: search.range, ...(to.runs ? { tab: 'runs', ...(to.status ? { status: to.status } : {}) } : {}) });
  const here = { q, state, status, runs };
  const pick = (next: Picks) => {
    setPicks(next);
    if (id || panel || tab === 'webhooks' || (phone && !tab)) filter({ ...here, runs: runs });
  };
  const teams = [...new Set(items.map((it) => it.team).filter(Boolean))].sort();
  const owners = [...new Set(items.map(ownerKey))].map((o) => ({ o, name: nameOf(o === TICO ? null : o) })).sort((a, b) => a.name.localeCompare(b.name));
  const count = (f: (it: Item) => boolean) => items.filter(f).length;
  const built = panel?.startsWith('builtin:') ? panel.slice(8) : '';
  const view = id ? '' : tab === 'runs' ? 'runs' : tab === 'webhooks' ? 'webhooks' : 'all';
  // on a phone the sidebar shows first; anything picked shows alone, with a way back
  const open = !!id || (phone ? !!tab : false) || !!panel;
  const nav = (key: 'all' | 'runs' | 'webhooks', label: string, icon: IconName, n?: number) => (
    <Row key={key} className="tl-row au-nav" title={label} href={routinesHref({ ...keep, tab: key })} selected={view === key && !built} target={`tab:${key}`}>
      <Icon name={icon} />
      <span className="row-title">{label}</span>
      <span className="row-age">{n ?? ''}</span>
    </Row>
  );
  return (
    <ListAndItem
      title="Automations"
      path={ROUTINES}
      className={editing ? 'rr au has-panel' : 'rr au'}
      listLabel="Automations"
      itemLabel="Automation"
      open={open}
      actions={
        <>
          <Menu
            trigger={
              phone ? (
                <Button variant="primary" aria-label="Add automation" data-phone>
                  Add
                </Button>
              ) : (
                <IconButton icon="plus" label="Add automation" />
              )
            }
          >
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
          <ListChoices group={group} sort={sort} range={range} phone={phone} onGroup={setGroup} onSort={setSort} onRange={(r) => void press('automations.list', { ...keep, range: r, ...(runs ? { tab: 'runs', ...(status ? { status } : {}) } : {}) })} />
        </PageMore>
      }
      search={!none && <Find label="Search automations" value={q} onFind={(words) => filter({ ...here, q: words })} wide />}
      list={
        <div className="au-side">
          <div className="au-navs" role="group" aria-label="Views">
            {nav('all', 'All automations', 'list', items.length)}
            {nav('runs', 'Runs', 'clock')}
            {nav('webhooks', 'Webhooks', 'link')}
          </div>
          <Facet
            name="Run"
            picked={runs && status ? [status] : []}
            onPick={(v) => filter({ ...here, runs: true, status: runs && status === v ? undefined : (v as RunStatus) })}
            choices={RUN_STATUSES.map((s) => ({ value: s, label: RUN_WORDS[s], icon: RUN_MARK[s] }))}
          />
          <Facet
            name="Trigger"
            picked={picks.kinds}
            onPick={(v) => pick({ ...picks, kinds: picks.kinds.includes(v) ? picks.kinds.filter((k) => k !== v) : [...picks.kinds, v] })}
            choices={KINDS.map((k) => ({ value: k, label: KIND_WORDS[k]!, icon: KIND_ICONS[k], count: count((it) => it.kind === k) }))}
          />
          <Facet
            name="Status"
            picked={state ? [state] : []}
            onPick={(v) => filter({ ...here, state: state === v ? undefined : (v as RoutineState) })}
            choices={(Object.keys(STATE_WORDS) as AutoState[]).map((k) => ({ value: k, label: STATE_WORDS[k], icon: STATE_MARK[k], count: count((it) => it.state === k) }))}
          />
          {teams.length > 0 && (
            <Facet name="Team" picked={picks.team ? [picks.team] : []} onPick={(v) => pick({ ...picks, team: picks.team === v ? '' : v })} choices={teams.map((t) => ({ value: t, label: t, icon: 'people', count: count((it) => it.team === t) }))} />
          )}
          <Facet
            name="Owner"
            picked={picks.owner ? [picks.owner] : []}
            onPick={(v) => pick({ ...picks, owner: picks.owner === v ? '' : v })}
            choices={owners.map(({ o, name }): Choice => ({ value: o, label: name, lead: o === TICO ? <Icon name="gear" /> : <Face id={o} size={16} />, count: count((it) => ownerKey(it) === o) }))}
          />
        </div>
      }
      conversation={children}
    />
  );
}

/** What the sidebar has picked, as chips over the content, each clearing its own (the content says why it is narrow). */
function Picked({ search }: { search: RoutinesSearch }) {
  const { picks, setPicks } = useShell();
  const nameOf = useNameOf();
  const { q, state, status, tab } = search;
  const runs = tab === 'runs';
  const go = (to: { q?: string; state?: RoutineState; status?: RunStatus }) =>
    void press('automations.list', { ...to, range: search.range, ...(runs ? { tab: 'runs', ...(to.status ? { status: to.status } : {}) } : {}) });
  const chips = [
    ...(runs && status ? [{ label: RUN_WORDS[status], onClear: () => go({ q, state }) }] : []),
    ...picks.kinds.map((k) => ({ label: KIND_WORDS[k] ?? k, onClear: () => setPicks({ ...picks, kinds: picks.kinds.filter((x) => x !== k) }) })),
    ...(state ? [{ label: STATE_WORDS[state as AutoState], onClear: () => go({ q, status }) }] : []),
    ...(picks.team ? [{ label: picks.team, onClear: () => setPicks({ ...picks, team: '' }) }] : []),
    ...(picks.owner ? [{ label: nameOf(picks.owner === TICO ? null : picks.owner), onClear: () => setPicks({ ...picks, owner: '' }) }] : []),
    ...(q ? [{ label: `“${q}”`, onClear: () => go({ state, status }) }] : []),
  ];
  return <FilterRow filters={chips} />;
}

/** The automations the sidebar leaves, for Runs: their ids while the list is narrowed by more than the server reads. */
export function useShownIds(search: RoutinesSearch): string[] | null {
  const { rows } = useShown(search);
  const { picks } = useShell();
  return search.q || search.state || picks.team ? rows.map((r) => r.id) : null;
}
export { Picked };

// ---------------------------------------------------------------- the content when none is open

export function RoutinesPage({ search }: { search: RoutinesSearch }) {
  if (search.tab === 'runs') return <RunsTab search={search} />;
  if (search.tab === 'webhooks') return <WebhooksTab panel={search.panel} />;
  return <AllTab search={search} />;
}
export { RoutinesPage as AutomationsPage };

/** All: every automation the filters leave, in the dense table with its counts over the range. */
function AllTab({ search }: { search: RoutinesSearch }) {
  const { panel } = search;
  const range = rangeOf(search);
  const phone = useCards();
  const stats = useQuery(statsQuery(range));
  const { closed, fold } = useShell();
  const per = new Map<string, Stat>((stats.data?.automations ?? []).map((s) => [s.id, s]));
  const statOf = (it: Item): Stat => per.get(it.builtin ? BUILTIN_STATS[it.builtin] : it.id) ?? NO_STAT;
  const { items, rows, groups, grouped } = useShown(search, statOf);
  const built = panel?.startsWith('builtin:') ? (BUILTINS.find((b) => `builtin:${b}` === panel) ?? null) : null;
  const setRange = (r: Range) => void press('automations.list', { q: search.q, state: search.state, range: r });

  // a column empty for every automation over the range leaves (decided from the whole list, so typing never moves one)
  const has = {
    next: items.some((it) => it.next),
    trend: items.some((it) => statOf(it).runs > 0),
    runs: items.some((it) => statOf(it).runs > 0),
    failed: items.some((it) => statOf(it).failed > 0),
    cost: items.some((it) => statOf(it).cost >= 0.005),
  };
  const span = 4 + +has.next + +has.trend + +has.runs + +has.failed + +has.cost;
  const emptyWords = items.length ? 'No automations match these filters' : 'No automations';
  return (
    <>
      <ItemHead
        title="All automations"
        target="tab:all"
        back={{ to: ROUTINES, label: 'Automations' }}
        actions={
          <>
            <RangePick range={range} onPick={setRange} />
            <PageOwner page="automations" />
          </>
        }
      />
      <div className="conv-body au-body" data-scroll>
        <Picked search={search} />
        {phone ? (
          <PhoneRows groups={groups} grouped={grouped} closed={closed} onFold={fold} statOf={statOf} range={range} empty={emptyWords} />
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
                  <GroupRows key={g.key} grouped={grouped} label={g.label} open={!closed.includes(g.key)} onFold={() => fold(g.key)} span={span} count={g.rows.length}>
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
        )}
        {stats.isError && <p className="au-note">{"Counts didn't load"}</p>}
      </div>
      {panel === 'routine:new' ? (
        <PanelSlot>
          <RoutinePanel id="new" onClose={close} />
        </PanelSlot>
      ) : built ? (
        <PanelSlot>
          <BuiltinPanel which={built} onClose={close} />
        </PanelSlot>
      ) : null}
    </>
  );
}


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
    <>
      <ItemHead title="Webhooks" target="tab:webhooks" back={{ to: ROUTINES, label: 'Automations' }} actions={list.length > 0 && add} />
      <div className="conv-body au-body" data-scroll>
        {list.length ? (
          <Section name="Incoming" count={list.length} target="section:incoming">
            {list.map((h) => (
              <HookRow key={h.id} h={h} selected={h.id === open} />
            ))}
          </Section>
        ) : (
          <Empty title="No webhooks yet">{add}</Empty>
        )}
      </div>
      {adding && <HookDialog onMade={setMade} onClose={() => setAdding(false)} />}
      {open && (
        <PanelSlot>
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
        </PanelSlot>
      )}
    </>
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
