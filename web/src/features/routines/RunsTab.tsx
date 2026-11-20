import { useQuery, useSuspenseInfiniteQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useActors } from '@/data';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { BarChart } from '@/kit/Chart';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Link } from '@/kit/Link';
import { Menu, MenuItem, MenuSub } from '@/kit/Menu';
import { useCards } from '@/kit/Popover';
import { EmptyRow, Table, Td, Th, Tr } from '@/kit/Table';
import { fmtWhen } from '@/lib';
import { FilterRow, ItemHead } from '@/pagekinds';
import { automationHref, LISTENING, ROUTINES } from './actions';
import { RangePick, rangeOf, routinesHref, type RoutinesSearch } from './AutomationsPage';
import { KIND_ICONS, KIND_WORDS, KINDS, money, RANGE_BUCKET, RANGE_SECONDS, RANGE_WORDS, RANGES, took, type Range } from './flow';
import { useWidth } from './overview';
import { actorKey, Face, press, Word } from './parts';
import { allRunsQuery, routinesQuery, RUN_STATUSES, statsQuery, type AnyRun, type RunStatus } from './queries';
import { runWords } from './words';

/* Automations › Runs, the content beside the sidebar (automations-overview.ts auAllHTML): the runs per bucket over the
   range as a chart, then every run across them newest first, fifty at a time. Status and range live in the address;
   trigger and owner narrow it from the filter menu. A run opens its automation's Runs; Tico's own job opens in the panel. */

const STATUS: Record<RunStatus, string> = { failed: 'Failed', waiting: 'Waiting', running: 'Running', done: 'Done' };
const STATUS_ICON: Record<RunStatus, 'alert' | 'pause' | 'play' | 'check'> = { failed: 'alert', waiting: 'pause', running: 'play', done: 'check' };
// Only these say something the row's words do not (a run that went through has no mark).
const MARKED = new Set(['failed', 'waiting', 'held']);
const SHORT: Record<string, string> = { failed: 'Failed', waiting: 'Waiting', held: 'Held' };
const noRuns = (r: Range, status?: RunStatus) => `No ${status ? `${STATUS[status].toLowerCase()} ` : ''}runs in the ${RANGE_WORDS[r].toLowerCase()}`;

/** Where a run opens: its automation's runs, Tico's own job in the panel, a watch's run on Listening. */
function runHref(x: AnyRun, range: Range | undefined): string {
  if (x.automation_id === 'maintenance' || x.automation_id === 'heartbeat') return routinesHref({ tab: 'runs', range, panel: `builtin:${x.automation_id}` });
  if (x.trigger === 'system') return routinesHref({ tab: 'runs', range, panel: 'builtin:system' });
  if (x.trigger === 'listening') return `${LISTENING}?watch=${encodeURIComponent(x.automation_id)}`;
  return `${automationHref(x.automation_id)}?view=runs`;
}

export function RunsTab({ search }: { search: RoutinesSearch }) {
  const { status } = search;
  const range = rangeOf(search);
  const phone = useCards();
  const [kinds, setKinds] = useState<string[]>([]);
  const [owner, setOwner] = useState('');
  const by = { trigger: kinds.length ? kinds.join(',') : undefined, owner: owner || undefined };
  const q = useSuspenseInfiniteQuery(allRunsQuery(range, status, by));
  const stats = useQuery(statsQuery(range));
  const autos = useQuery(routinesQuery).data ?? [];
  const actors = useActors();
  const nameOf = (id: string) => actors.find((a) => a.id === actorKey(id) || (a.slug && `bot:${a.slug}` === id))?.name ?? id.replace(/^\w+:/, '');
  const runs = q.data.pages.flatMap((p) => p.runs);
  const show = (next: { status?: RunStatus; range?: Range }) => void press('automations.showRuns', { status: next.status, range: next.range });
  const pickKind = (v: string) => setKinds((k) => (k.includes(v) ? k.filter((x) => x !== v) : [...k, v]));
  const owners = [...new Set([...autos.map((a) => a.owner_id ?? ''), ...runs.map((r) => r.owner_id ?? '')].filter(Boolean))].sort((a, b) => nameOf(a).localeCompare(nameOf(b)));
  // the automations the filters leave, for the chart's bars
  const shown = new Set(autos.filter((a) => (!kinds.length || kinds.includes(a.trigger.type)) && (!owner || a.owner_id === owner)).map((a) => a.id));
  const narrowed = kinds.length > 0 || !!owner;
  const on = kinds.length + +!!owner + +!!status;
  const filters = [
    ...(status ? [{ label: STATUS[status], onClear: () => show({ range: search.range }) }] : []),
    ...kinds.map((k) => ({ label: KIND_WORDS[k] ?? k, onClear: () => pickKind(k) })),
    ...(owner ? [{ label: nameOf(owner), onClear: () => setOwner('') }] : []),
  ];

  return (
    <>
      <ItemHead
        title="Runs"
        target="tab:runs"
        back={{ to: ROUTINES, label: 'Automations' }}
        actions={
          <>
            <RangePick range={range} onPick={(r) => show({ status, range: r })} />
            <Menu trigger={<IconButton icon="filter" label="Filter runs" on={on > 0} />}>
              <MenuSub label="Run">
                <MenuItem label="All" on={!status} onSelect={() => show({ range: search.range })} />
                {RUN_STATUSES.map((x) => (
                  <MenuItem key={x} label={STATUS[x]} icon={STATUS_ICON[x]} on={x === status} onSelect={() => show({ status: x === status ? undefined : x, range: search.range })} />
                ))}
              </MenuSub>
              <MenuSub label="Trigger">
                {KINDS.map((k) => (
                  <MenuItem key={k} label={KIND_WORDS[k]!} icon={KIND_ICONS[k]} on={kinds.includes(k)} onSelect={() => pickKind(k)} />
                ))}
              </MenuSub>
              {owners.length > 0 && (
                <MenuSub label="Owner">
                  {owners.map((o) => (
                    <MenuItem key={o} label={nameOf(o)} lead={<Face id={o} size={16} />} on={o === owner} onSelect={() => setOwner(owner === o ? '' : o)} />
                  ))}
                </MenuSub>
              )}
            </Menu>
            {phone && (
              <Menu trigger={<IconButton icon="clock" label="Time range" />}>
                {RANGES.map((r) => (
                  <MenuItem key={r} label={RANGE_WORDS[r]} on={r === range} onSelect={() => show({ status, range: r })} />
                ))}
              </Menu>
            )}
          </>
        }
      />
      <div className="conv-body au-body" data-scroll>
        <FilterRow filters={filters} />
        <RunsChart range={range} stats={stats.data} only={narrowed ? shown : null} failed={stats.isError} />
        {phone ? (
          <PhoneRuns runs={runs} range={search.range} empty={noRuns(range, status)} />
        ) : (
          <Table label="Runs" stick>
            <thead>
              <tr>
                <Th>When</Th>
                <Th>Status</Th>
                <th scope="col" className="au-c-name">
                  Automation
                </th>
                <th scope="col" className="au-c-by">
                  Started by
                </th>
                <th scope="col" className="au-c-out">
                  Outcome
                </th>
                <th scope="col" className="num au-c-steps">
                  Steps
                </th>
                <th scope="col" className="num au-c-time">
                  Time
                </th>
                <Th num>Cost</Th>
              </tr>
            </thead>
            <tbody>
              {runs.length ? (
                runs.map((x) => (
                  <Tr key={x.id} linked className="au-row">
                    <td className="au-c-when" title={fmtWhen(x.started_at)}>
                      <Link href={runHref(x, search.range)} data-target={`run:${x.id}`}>
                        <Ago at={x.started_at} />
                      </Link>
                    </td>
                    <td className="au-c-st">{MARKED.has(x.status) && <Word icon={x.status === 'failed' ? 'alert' : 'pause'}>{SHORT[x.status] ?? runWords(x.status)}</Word>}</td>
                    <td className="au-c-name">
                      <span className="au-name">
                        <Icon name={KIND_ICONS[x.trigger] ?? 'spark'} />
                        <span className="au-name-t">{x.automation_name}</span>
                      </span>
                    </td>
                    <td className="au-c-by" title={x.started_by ?? ''}>
                      {x.started_by}
                    </td>
                    <td className={x.status === 'failed' ? 'au-c-out is-failed' : 'au-c-out'} title={x.outcome ?? ''}>
                      {x.outcome}
                      {(x.count ?? 0) > 1 && <span className="au-times">{` ×${x.count}`}</span>}
                    </td>
                    <td className={x.step_count == null ? 'num zero au-c-steps' : 'num au-c-steps'}>{x.step_count ?? '—'}</td>
                    <td className={took(x.started_at, x.finished_at) ? 'num au-c-time' : 'num zero au-c-time'}>{took(x.started_at, x.finished_at) || '—'}</td>
                    <Td num zero={!x.cost}>
                      {x.cost ? money(x.cost) : '—'}
                    </Td>
                  </Tr>
                ))
              ) : (
                <EmptyRow span={8}>{noRuns(range, status)}</EmptyRow>
              )}
            </tbody>
          </Table>
        )}
        {q.hasNextPage && (
          <div className="au-more">
            <Button variant="quiet" busy={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>
              More
            </Button>
          </div>
        )}
      </div>
    </>
  );
}

/** Done and failed runs per bucket over the range, for the automations the filters leave. */
function RunsChart({ range, stats, only, failed }: { range: Range; stats: { until: string; totals: { buckets: { ok: number; failed: number }[] }; automations: { id: string; buckets: { ok: number; failed: number }[] }[] } | undefined; only: Set<string> | null; failed: boolean }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  if (failed) return <p className="au-note">{"The chart didn't load"}</p>;
  const n = stats?.totals.buckets.length ?? 0;
  const b = Array.from({ length: n }, () => ({ ok: 0, failed: 0 }));
  for (const a of stats?.automations ?? [])
    if (!only || only.has(a.id))
      a.buckets.forEach((x, i) => {
        if (b[i]) {
          b[i].ok += x.ok;
          b[i].failed += x.failed;
        }
      });
  const ok = b.reduce((t, x) => t + x.ok, 0);
  const bad = b.reduce((t, x) => t + x.failed, 0);
  const max = Math.max(1, ...b.map((x) => x.ok + x.failed));
  const span = RANGE_SECONDS[range] * 1000;
  // the bars end at the server's "until": the ticks count back from it
  const until = stats ? Date.parse(stats.until) : 0;
  const at = (q: number) => {
    const d = new Date(until - span * (1 - q));
    return range === '7d' ? d.toLocaleDateString('en-US', { weekday: 'short' }) : d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  };
  const ticks = n ? ([0, Math.round(n / 3), Math.round((2 * n) / 3)] as const).map((i, k) => [i, at(k / 3)] as const) : [];
  return (
    <div className="au-chart" ref={ref}>
      <div className="au-chart-h">
        <span className="au-chart-t">{`Runs per ${RANGE_BUCKET[range]}`}</span>
        <span className="au-key">
          <i className="c1" />
          {`${ok.toLocaleString()} done`}
        </span>
        <span className="au-key">
          <i className="c2" />
          {`${bad.toLocaleString()} failed`}
        </span>
      </div>
      {n > 0 && width > 0 && (
        <BarChart
          points={b.map((x) => [x.ok, x.failed] as const)}
          width={width}
          height={112}
          max={max}
          ticks={[0, max]}
          fmt={(v) => String(Math.round(v))}
          xLabels={[...ticks, [n - 1, 'now']]}
          label={`${ok} done, ${bad} failed in the ${RANGE_WORDS[range].toLowerCase()}`}
        />
      )}
    </div>
  );
}

/** A phone's runs: each the automation and its mark over when, the outcome and its numbers. */
function PhoneRuns({ runs, range, empty }: { runs: AnyRun[]; range: Range | undefined; empty: string }) {
  if (!runs.length) return <p className="au-note">{empty}</p>;
  return (
    <div className="au-prows">
      {runs.map((x) => (
        <div key={x.id} className="au-prow">
          <span className="au-prow-1">
            <Icon name={KIND_ICONS[x.trigger] ?? 'spark'} />
            <Link href={runHref(x, range)} className="au-prow-a" data-target={`run:${x.id}`}>
              {x.automation_name}
            </Link>
            {MARKED.has(x.status) && <Word icon={x.status === 'failed' ? 'alert' : 'pause'}>{SHORT[x.status] ?? runWords(x.status)}</Word>}
            <span className="au-prow-end">
              <Ago at={x.started_at} className="au-last" />
            </span>
          </span>
          <span className="au-prow-2">
            {[x.outcome && ((x.count ?? 0) > 1 ? `${x.outcome} ×${x.count}` : x.outcome), x.step_count != null ? `${x.step_count} ${x.step_count === 1 ? 'step' : 'steps'}` : '', took(x.started_at, x.finished_at), x.cost ? money(x.cost) : '', x.started_by]
              .filter(Boolean)
              .join(' · ')}
          </span>
        </div>
      ))}
    </div>
  );
}
