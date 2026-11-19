import { useSuspenseInfiniteQuery } from '@tanstack/react-query';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { Row } from '@/kit/Row';
import { fmtMoney } from '@/lib';
import { FilterRow, List, Section, type PageTab } from '@/pagekinds';
import { LISTENING, RUNS_RANGE } from './actions';
import { Face, press, Word } from './parts';
import { allRunsQuery, RUN_RANGES, RUN_STATUSES, type AnyRun, type RunRange, type RunStatus } from './queries';
import { RoutinePanel } from './RoutinePanel';
import { runWords } from './words';

/* Routines › Runs (automations-overview.ts Runs): every run across routines, newest first, fifty at a time. Status and
   time range live in the address (`?tab=runs&status=&range=`); the filter button stays on a phone. A run opens its
   routine in the panel; a listening watch's run opens the watch. */

const STATUS: Record<RunStatus, string> = { failed: 'Failed', waiting: 'Waiting', running: 'Running', done: 'Done' };
const RANGE: Record<RunRange, string> = { '5m': 'Last 5 minutes', '1h': 'Last hour', '24h': 'Last 24 hours', '7d': 'Last 7 days' };
/** "No runs in the last hour": an empty list names the range it looked at (the default is narrow). */
const noRuns = (r: RunRange, status?: RunStatus) => `No ${status ? `${STATUS[status].toLowerCase()} ` : ''}runs in the ${RANGE[r].toLowerCase()}`;
// Only these say something a row's words do not.
const MARKED = new Set(['failed', 'waiting', 'held']);

interface Head {
  title: string;
  path: string;
  tabs: readonly PageTab[];
  className: string;
}

type RunPick = { status?: RunStatus; range?: RunRange };

export function RunsTab({ head, status, range, panel, href }: { head: Head; panel?: string; href: (panel: string) => string } & RunPick) {
  const r = range ?? RUNS_RANGE;
  const q = useSuspenseInfiniteQuery(allRunsQuery(r, status));
  const runs = q.data.pages.flatMap((p) => p.runs);
  const open = panel?.startsWith('routine:') ? panel.slice(8) : null;
  const now = { status, range: r };
  const show = (next: RunPick) => void press('routines.showRuns', next);
  const filters = [
    ...(status ? [{ label: STATUS[status], onClear: () => show({ ...now, status: undefined }) }] : []),
    ...(r !== RUNS_RANGE ? [{ label: RANGE[r], onClear: () => show({ ...now, range: undefined }) }] : []),
  ];
  return (
    <List
      {...head}
      state={runs.length ? 'ready' : 'empty'}
      // the filter row shows only with rows, so an empty filtered list clears from here
      empty={{ title: noRuns(r, status), action: filters.length ? <Button onClick={() => show({})}>Clear filters</Button> : undefined }}
      actions={
        <Menu trigger={<IconButton icon="filter" label="Filter runs" on={filters.length > 0} data-phone />}>
          {RUN_STATUSES.map((x) => (
            <MenuItem key={x} label={STATUS[x]} on={x === status} onSelect={() => show({ ...now, status: x === status ? undefined : x })} />
          ))}
          <MenuRule />
          {RUN_RANGES.map((x) => (
            <MenuItem key={x} label={RANGE[x]} on={x === r} onSelect={() => show({ ...now, range: x })} />
          ))}
        </Menu>
      }
      filter={<FilterRow filters={filters} onClearAll={() => show({})} />}
      sections={
        <Section name={RANGE[r]} count={runs.length} target="section:runs">
          {runs.map((run) => (
            <RunRow key={run.id} run={run} href={href} selected={run.automation_id === open} />
          ))}
          {q.hasNextPage && (
            <div className="rr-more">
              <Button busy={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>
                More
              </Button>
            </div>
          )}
        </Section>
      }
      panel={open ? <RoutinePanel key={open} id={open} onClose={() => void press('routines.closePanel', {})} /> : null}
    />
  );
}

function RunRow({ run, href, selected }: { run: AnyRun; href: (panel: string) => string; selected: boolean }) {
  const name = run.automation_name || 'Routine';
  const what = [run.outcome || runWords(run.status), run.cost ? fmtMoney(run.cost, { cents: true }) : ''].filter(Boolean).join(' · ');
  // Tico's own jobs have no page; a watch opens on Listening.
  const to = run.trigger === 'system' ? undefined : run.trigger === 'listening' ? `${LISTENING}?watch=${encodeURIComponent(run.automation_id)}` : href(`routine:${run.automation_id}`);
  return (
    <Row className="auto" title={name} href={to} selected={selected} target={`run:${run.id}`}>
      <span className="row-title">{name}</span>
      <span className="row-sub">{what}</span>
      <span className="row-state">{MARKED.has(run.status) && <Word>{runWords(run.status)}</Word>}</span>
      <Face id={run.owner_id} />
      <Ago at={run.started_at} className="row-age" />
    </Row>
  );
}
