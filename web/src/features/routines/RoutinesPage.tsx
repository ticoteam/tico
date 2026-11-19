import { useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Link } from '@/kit/Link';
import { Menu, MenuItem } from '@/kit/Menu';
import { Row } from '@/kit/Row';
import { toast } from '@/kit/Toast';
import { FilterRow, List, PageMore, Section, type PageTab } from '@/pagekinds';
import { BUILTINS, ROUTINES, type Builtin, type RoutineState } from './actions';
import { BuiltinPanel } from './BuiltinPanel';
import { NewInbox } from './Templates';
import { Face, Find, press, Rows, Word } from './parts';
import { heartbeatQuery, hooksQuery, maintenanceQuery, routinesQuery, systemJobsQuery, type RunRange, type RunStatus } from './queries';
import { RoutinePanel } from './RoutinePanel';
import { RunsTab } from './RunsTab';
import type { Hook, Routine, SystemJob } from './types';
import { HookDialog, WebhookPanel } from './WebhookPanel';
import { maintenanceWords, startsWords, triggerWords } from './words';
import './routines.css';

/* Routines (pages/3-company/rules.js routinesScreen, webhooksScreen): a List. Routines in sections by what starts
   them, then the built-in three; Runs lists every run across them; Webhooks lists incoming webhooks (outgoing ones
   live on Tools). One routine, a built-in one or a webhook opens in `?panel=`. */

export interface RoutinesSearch {
  tab?: 'webhooks' | 'runs';
  panel?: string;
  /** Runs: which runs, and over what time. */
  status?: RunStatus;
  range?: RunRange;
  /** Routines: the words searched for, and which state shows. */
  q?: string;
  state?: RoutineState;
}

/** `/company/routines?…`: the address a row opens. */
export const routinesHref = (search: RoutinesSearch) => {
  const q = new URLSearchParams(Object.entries(search).filter((e): e is [string, string] => !!e[1])).toString();
  return q ? `${ROUTINES}?${q}` : ROUTINES;
};

export const routinesTabs: readonly PageTab[] = [
  { label: 'Routines', to: ROUTINES, search: { tab: '' }, target: 'tab:routines' },
  { label: 'Runs', to: ROUTINES, search: { tab: 'runs' }, target: 'tab:runs' },
  { label: 'Webhooks', to: ROUTINES, search: { tab: 'webhooks' }, target: 'tab:webhooks' },
];

const head = { title: 'Routines', path: ROUTINES, tabs: routinesTabs, className: 'rr' };

export function RoutinesPage({ search }: { search: RoutinesSearch }) {
  if (search.tab === 'runs') {
    const { status, range } = search;
    return <RunsTab head={head} status={status} range={range} panel={search.panel} href={(panel) => routinesHref({ tab: 'runs', status, range, panel })} />;
  }
  return search.tab === 'webhooks' ? <WebhooksTab panel={search.panel} /> : <RoutinesTab panel={search.panel} q={search.q} state={search.state} />;
}

const close = () => void press('routines.closePanel', {});

// ---------------------------------------------------------------- routines

const SECTIONS: [string, string][] = [
  ['schedule', 'Scheduled'],
  ['event', 'Events'],
  ['webhook', 'Webhooks'],
  ['due', 'Due dates'],
  ['message', 'Messages'],
  ['task_received', 'Tasks received'],
];

/** Who runs it: the agent that takes the turn, or the owner. */
export const runnerOf = (r: Routine) => r.steps?.find((s) => s.type === 'bot_turn')?.bot ?? r.owner_id ?? null;

/** The filter's states (old automations-overview.ts AU_STATES): on, last run failed, on hold, off or paused. */
const STATE_WORDS: Record<RoutineState, string> = { on: 'On', failed: 'Failed', hold: 'On hold', off: 'Off' };
const stateOf = (r: Routine): RoutineState => (r.last_run?.status === 'failed' ? 'failed' : r.hold ? 'hold' : r.enabled === false || r.paused ? 'off' : 'on');
const has = (words: string, q: string | undefined) => !q || words.toLowerCase().includes(q.toLowerCase());

function RoutinesTab({ panel, q, state }: { panel?: string; q?: string; state?: RoutineState }) {
  const all = useSuspenseQuery(routinesQuery).data;
  const hooks = useQuery(hooksQuery);
  const open = panel?.startsWith('routine:') ? panel.slice(8) : null;
  const built = panel?.startsWith('builtin:') ? (BUILTINS.find((b) => `builtin:${b}` === panel) ?? null) : null;
  const hookName = (id: string) => hooks.data?.find((h) => h.id === id)?.name;
  const routines = all.filter((r) => (!state || stateOf(r) === state) && has(`${r.name ?? ''} ${triggerWords(r.trigger, { hook: hookName })}`, q));
  const listed = (to: { q?: string; state?: RoutineState }) => void press('routines.list', to);
  const known = new Set(SECTIONS.map(([t]) => t));
  const groups: [string, Routine[]][] = [
    ...SECTIONS.map(([t, name]): [string, Routine[]] => [name, routines.filter((r) => r.trigger.type === t)]),
    ['Other', routines.filter((r) => !known.has(r.trigger.type))],
  ];
  const add = (
    <Button variant="primary" onClick={() => void press('routines.new', {})}>
      Add routine
    </Button>
  );
  // The built-in section keeps the page ready while there are no routines, so the page kind cannot see it is empty:
  // with none, the head keeps only "⋯" and the empty state holds Add.
  const none = !all.length;
  const [inbox, setInbox] = useState(false);
  return (
    <List
      {...head}
      search={!none && <Find label="Search routines" value={q} onFind={(words) => listed({ q: words, state })} />}
      filter={state && <FilterRow filters={[{ label: STATE_WORDS[state], onClear: () => listed({ q }) }]} />}
      actions={
        <>
          {!none && (
            <>
              <Menu trigger={<IconButton icon="filter" label="Filter" on={!!state} data-phone />}>
                <MenuItem label="All" on={!state} onSelect={() => listed({ q })} />
                {(Object.keys(STATE_WORDS) as RoutineState[]).map((k) => (
                  <MenuItem key={k} label={STATE_WORDS[k]} on={k === state} onSelect={() => listed({ q, state: k })} />
                ))}
              </Menu>
              {add}
            </>
          )}
          {inbox && <NewInbox onClose={() => setInbox(false)} />}
        </>
      }
      more={
        <PageMore>
          <MenuItem label="New inbox" icon="mail" onSelect={() => setInbox(true)} />
        </PageMore>
      }
      sections={
        <>
          {routines.length ? (
            groups
              .filter(([, rs]) => rs.length)
              .map(([name, rs]) => (
                <Section key={name} name={name} count={rs.length} target={`section:${name}`}>
                  <Rows
                    label={name}
                    items={rs}
                    getKey={(r) => r.id}
                    draw={(r) => (
                      <RoutineRow
                        key={r.id}
                        r={r}
                        href={routinesHref({ q, state, panel: `routine:${r.id}` })}
                        selected={r.id === open}
                        when={triggerWords(r.trigger, { hook: hookName })}
                      />
                    )}
                  />
                </Section>
              ))
          ) : all.length ? (
            state && <Empty title="No matches" />
          ) : (
            <Empty title="No routines yet">{add}</Empty>
          )}
          {!state && <BuiltInSection selected={built} q={q} showEmpty={!none && !routines.length} />}
        </>
      }
      panel={open ? <RoutinePanel key={open} id={open} onClose={close} /> : built ? <BuiltinPanel which={built} onClose={close} /> : null}
    />
  );
}

function RoutineRow({ r, href, selected, when }: { r: Routine; href: string; selected: boolean; when: string }) {
  const off = r.enabled === false || !!r.paused;
  const failed = r.last_run?.status === 'failed';
  const at = r.last_run?.started_at ?? r.last_run?.finished_at;
  const name = r.name || 'Routine';
  return (
    <Row className={off ? 'auto is-off' : 'auto'} title={name} href={href} selected={selected} target={`routine:${r.id}`}>
      <span className="row-title">{name}</span>
      <span className="row-sub">{when}</span>
      <span className="row-state">
        {failed ? (
          <Word>Failed</Word>
        ) : r.hold ? (
          <Word icon="pause">On hold</Word>
        ) : off ? (
          <span title="Paused">
            <Icon name="pause" />
          </span>
        ) : null}
      </span>
      <Face id={runnerOf(r)} />
      {at ? <Ago at={at} className="row-age" /> : <span className="row-age" />}
    </Row>
  );
}

/** When the most recent of Tico's own jobs last started. */
const lastJob = (jobs: SystemJob[] | undefined) =>
  jobs?.reduce<string | null>((at, j) => (j.last?.started_at && (!at || j.last.started_at > at) ? j.last.started_at : at), null);

/** Maintenance, Heartbeat and System jobs: Tico's own, each with its switch and timing in its panel. One the person
    may not read is left out. */
function BuiltInSection({ selected, q, showEmpty }: { selected: Builtin | null; q?: string; showEmpty: boolean }) {
  const m = useQuery(maintenanceQuery);
  const h = useQuery(heartbeatQuery);
  const j = useQuery(systemJobsQuery);
  const rows: { id: Builtin; name: string; when: string; at?: string | null; off?: boolean; gone: boolean }[] = [
    { id: 'maintenance', name: 'Maintenance', when: m.data ? maintenanceWords(m.data.settings.schedule) : '', at: m.data?.last_run?.started_at, off: m.data?.settings.paused, gone: m.isError },
    { id: 'heartbeat', name: 'Heartbeat', when: h.data ? `Every ${h.data.interval_s} seconds` : '', at: h.data?.last_beat_at, gone: h.isError },
    { id: 'system', name: 'System jobs', when: j.data ? `${j.data.length} ${j.data.length === 1 ? 'job' : 'jobs'}` : '', at: lastJob(j.data), gone: j.isError },
  ];
  const shown = rows.filter((r) => !r.gone && has(r.name, q));
  if (!shown.length) return showEmpty ? <Empty title="No matches" /> : null;
  return (
    <Section name="Built in" count={shown.length} target="section:built-in">
      {shown.map((b) => (
        <Row
          key={b.id}
          className={b.off ? 'auto is-off' : 'auto'}
          title={b.name}
          href={routinesHref({ q, panel: `builtin:${b.id}` })}
          selected={selected === b.id}
          target={`routine:${b.id}`}
        >
          <span className="row-title">{b.name}</span>
          <span className="row-sub">{b.when}</span>
          <span className="row-state">
            {b.off && (
              <span title="Paused">
                <Icon name="pause" />
              </span>
            )}
          </span>
          <span className="row-state auto-lock" title="Built in">
            <Icon name="lock" />
          </span>
          {b.at ? <Ago at={b.at} className="row-age" /> : <span className="row-age" />}
        </Row>
      ))}
    </Section>
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
      {...head}
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
