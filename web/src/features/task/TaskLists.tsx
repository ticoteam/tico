import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from '@tanstack/react-router';
import { Suspense, lazy, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { ApiError } from '@/api/client';
import { directoryQuery, useActor } from '@/data';
import { filtersOf, flowQuery, inOrder, isFiltered, loadListTools, loadMore, needsYouRows, pipelinesQuery, pipelineTasksQuery, sortOf, stageCount, stagePriorityQuery, taskHitsQuery, useNeedsYou, useNeedsYouDrawn, useStageSort, validateTasksSearch, waitsOnMe, type FlowItem, type TaskItem } from '@/features/tasks/routing';
import { ownerListQuery, type OwnTask } from '@/features/team/routing';
import { Ago } from '@/kit/Ago';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { FlowMark } from '@/kit/FlowMark';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu as DropMenu, MenuItem, MenuRule, MenuSub } from '@/kit/Menu';
import { LatePill } from '@/kit/Pill';
import { VirtualList, type VirtualListHandle } from '@/kit/VirtualList';
import { useFollow } from '@/live/follow';
import { HITS } from '@/search/helpers';
import { boardSearch, hearPlaces, holdRow, keptRows, NEEDS_YOU, noteList, type ListRow } from '@/lib';

/* The list beside a task open over a page (plan: the task modal): the list it was opened from. A board's stage (its
   pipeline's id), Needs you (none), an agent's or a person's open tasks (their id), search hits (`search:<words>`), or
   a list a page drew as it drew it (`rows:…`: an agent's own list, its done tasks). One it cannot draw (a pipeline gone,
   a teammate gone, a page's list not drawn since the app loaded) is Needs you.
   Each notes the rows it draws (lib noteList) for the row below once the open task is answered, and its title is a
   switcher to another list. Rows are the tasks' own links: a press on one opens it in place (router.tsx). */

const ListToolItems = lazy(() => loadListTools().then((m) => ({ default: m.ListToolItems })));
const ListFind = lazy(() => loadListTools().then((m) => ({ default: m.ListFind })));

const SEARCH = 'search:';
const ROWS = 'rows:';
/** What kind of list `list` names (lib openOf keeps only one of these). */
export const listKind = (list: string | undefined): 'needs' | 'search' | 'rows' | 'who' | 'pipeline' =>
  !list ? 'needs' : list.startsWith(SEARCH) ? 'search' : list.startsWith(ROWS) ? 'rows' : list.includes(':') ? 'who' : 'pipeline';

const numberOf = (t: { number?: number | null; id: string }) => String(t.number ?? t.id);
const isOpen = (t: { number?: number | null; id: string }, open: string) => numberOf(t) === open || t.id === open;

export interface TaskListProps {
  /** The list (the address's `list`); none is Needs you. */
  list: string | undefined;
  /** The open task, as the address names it. */
  open: string;
  /** The open task, once read: a pipeline's list shows its stage first; a teammate's or a search's has it, the list it
      was found in as the person found it (a done task, one of an agent's own lists). */
  current?: Shown;
  /** Between the title and the end of the head (the modal's fold). */
  end?: ReactNode;
}

/** A task row as a list draws it. */
type Shown = Pick<OwnTask, 'id' | 'number' | 'title' | 'status' | 'pipeline_id' | 'owner_id' | 'late' | 'updated_at'> & { stage_id?: string | null };

export function TaskList({ list, open, current, end }: TaskListProps) {
  const kind = listKind(list);
  if (kind === 'needs') return <NeedsYouList open={open} end={end} />;
  if (kind === 'search') return <SearchList words={list!.slice(SEARCH.length)} list={list!} open={open} current={current} end={end} />;
  if (kind === 'rows') return <DrawnList list={list!} open={open} current={current} end={end} />;
  if (kind === 'who') return <WhoList who={list!} open={open} current={current} end={end} />;
  return <KnownPipeline key={list} pipeline={list!} open={open} stage={current?.stage_id ?? undefined} end={end} />;
}

/** A pipeline's list, while the pipelines read have it; one they do not (gone, retired, mistyped): Needs you. */
function KnownPipeline(props: { pipeline: string; open: string; stage?: string; end?: ReactNode }) {
  const { data: pipes } = useQuery(pipelinesQuery);
  if (pipes && !pipes.some((x) => x.id === props.pipeline)) return <NeedsYouList open={props.open} end={props.end} noteAs={props.pipeline} />;
  return <PipelineList {...props} />;
}

/** The rows read, with the open task first when they lack it: the list it was opened from always has it. */
function withCurrent<T extends Shown>(tasks: readonly T[] | undefined, open: string, current: Shown | undefined): readonly (T | Shown)[] | undefined {
  if (!tasks || !current || tasks.some((t) => isOpen(t, open))) return tasks;
  return [current, ...tasks];
}

/** A list's rows as an answer goes on down them. */
function useNoteRows(list: string, open: string, rows: ListRow[] | undefined) {
  useEffect(() => {
    if (rows) noteList({ pipeline: list, open, rows });
  }, [list, open, rows]);
  useEffect(() => () => noteList(null), []);
}

/** Another list beside the open task: the address's `list` changes, the task stays. */
function useToList() {
  const navigate = useNavigate();
  return (list: string | null) =>
    void navigate({
      to: '.',
      replace: true,
      search: ((s: Record<string, unknown>) => {
        const { list: _, ...rest } = s;
        return list ? { ...rest, list } : rest;
      }) as never,
    });
}

/** The switcher's other lists: Needs you, and each pipeline's. */
function OtherLists({ list }: { list: string | undefined }) {
  const { data: pipes } = useQuery(pipelinesQuery);
  const to = useToList();
  return (
    <>
      <MenuItem label="Needs you" on={!list} onSelect={() => to(null)} />
      <MenuSub label="Pipelines">
        {(pipes ?? []).map((x) => (
          <MenuItem key={x.id} label={x.name} on={x.id === list} onSelect={() => to(x.id)} />
        ))}
      </MenuSub>
    </>
  );
}

function Head({ title, list, end, children, tools }: { title: ReactNode; list: string | undefined; end?: ReactNode; children?: ReactNode; tools?: ReactNode }) {
  return (
    <header className="li-head">
      <DropMenu
        align="start"
        trigger={
          <button className="li-pick" type="button" data-target="list:pick">
            {title}
            <Icon name="chevdown" />
          </button>
        }
      >
        {children}
        {children ? <MenuRule /> : null}
        <OtherLists list={list} />
      </DropMenu>
      <span className="grow" />
      {tools}
      {end}
    </header>
  );
}

function ListFace({ id }: { id: string | null | undefined }) {
  const who = useActor(id ?? undefined);
  return id ? <Avatar decorative kind={who?.kind ?? (id.startsWith('bot:') ? 'agent' : 'person')} name={who?.name ?? (id.startsWith('bot:') ? 'Agent' : 'Person')} hue={who?.hue} live={who?.working} size={22} /> : null;
}

function Row({ to, n, on, title, mark, late, who, at }: { to: string; n: string; on: boolean; title: string; mark?: ReactNode; late?: boolean; who?: string | null; at?: string | null }) {
  return (
    <Link to={to as never} className={on ? 'li is-on' : 'li'} title={title} data-target={`task:${n}`} aria-current={on ? 'true' : undefined}>
      <span className="li-mark">{mark}</span>
      <span className="li-main">
        <span className="li-title">{title}</span>
        {late ? <LatePill /> : null}
      </span>
      <span className="li-who">
        <ListFace id={who} />
        {at ? <Ago at={at} className="row-age" /> : null}
      </span>
    </Link>
  );
}

const taskTo = (pipeline: string | null | undefined, n: string) => (pipeline ? `/tasks/${encodeURIComponent(pipeline)}/${encodeURIComponent(n)}` : `/task/${encodeURIComponent(n)}`);

/* ---------------------------------------------------------------- a board's stage */

/* One stage of a pipeline's open tasks at a time, with the filters its board was last shown with and the search,
   filters and sort the list was given since (lib changeBoardSearch), the open task chosen. The head is the pipeline ›
   the stage and its count; its menu picks another stage or another list. The stage shown is the one the open task was
   in as it opened, or the one picked since; it changes only when another task opens or the person picks. */
function PipelineList({ pipeline, open, stage, end }: { pipeline: string; open: string; stage?: string; end?: ReactNode }) {
  const needs = useNeedsYou();
  const { data: pipes } = useQuery(pipelinesQuery);
  const p = pipes?.find((x) => x.id === pipeline);
  // every task's change, as its board follows them: a move into the stage shown lands here too
  useFollow({ allTasks: true });
  const search = useSyncExternalStore(hearPlaces, () => boardSearch(pipeline));
  const filters = useMemo(() => ({ ...filtersOf(validateTasksSearch(search)), sort: sortOf(search) }), [search]);
  const list = useQuery({ ...pipelineTasksQuery(pipeline, filters), enabled: !!p });
  const page = list.data;
  const rows = useMemo(() => inOrder(page?.tasks ?? [], filters.sort), [page, filters.sort]);
  const read = useMemo(
    () =>
      (p?.stages ?? []).map((s) => {
        const items = rows.filter((t) => t.stage_id === s.id);
        return { ...s, items, n: page && stageCount(page, s.id, items.length) };
      }),
    [p, rows, page],
  );
  const own = stage ?? rows.find((t) => isOpen(t, open))?.stage_id ?? undefined;
  // the open task's stage as it opened (`at`), and the stage shown: that one, or the one picked since
  const [head, setHead] = useState<{ open: string; at: string; stage: string } | null>(null);
  if (own && head?.open !== open) setHead({ open, at: own, stage: own });
  const at = head?.open === open ? head.at : undefined;
  const want = head?.open === open ? head.stage : undefined;
  const showing = read.find((g) => g.id === want)?.id ?? (page ? (read.find((g) => g.items.length) ?? read[0])?.id : undefined);
  // Priority: the stage shown is read on its own, in Tico's order for the person, whole (no More)
  const ranks = !filters.sort && !!showing;
  const byPriority = useQuery({ ...stagePriorityQuery(pipeline, showing ?? '', filters), enabled: !!p && ranks });
  const ranked = byPriority.data;
  // failed: unless the stage shown is read on its own by priority, the pipeline's read is the list
  const listFailed = list.isError && !page && (!showing || !ranks);
  const failed = listFailed || (ranks && byPriority.isError && !ranked);
  useStageSort((ranks && showing) || null, ranked);
  const stages = useMemo(
    () => (ranks ? read.map((g) => (g.id === showing ? { ...g, items: (ranked?.tasks ?? []).filter((t) => t.stage_id === g.id) } : g)) : read),
    [ranks, read, showing, ranked],
  );
  const shown = stages.find((g) => g.id === showing);
  const countOf = (g: (typeof stages)[number]) => g.n ?? g.items.length;
  const cut = !!page?.next_after;
  const [finding, setFinding] = useState(false);
  // the rows an answer goes on to: from the stage it opened in on, past each stage's last row to the next stage's
  // first, ending with the first stage not read whole
  const drawn = useMemo(() => {
    const out: ListRow[] = [];
    for (const g of stages.slice(Math.max(0, stages.findIndex((x) => x.id === at)))) {
      out.push(...g.items.map((t) => ({ id: t.id, n: numberOf(t) })));
      if (cut && g.items.length < (g.n ?? 0)) break;
    }
    return out;
  }, [stages, at, cut]);
  useNoteRows(pipeline, open, drawn);
  const name = p?.name ?? 'Tasks';
  const more = !ranks && cut && !!shown && shown.items.length < countOf(shown);
  const using = finding || !!filters.q || isFiltered(filters) || !!filters.sort;
  return (
    <>
      <Head
        list={pipeline}
        end={end}
        title={
          <>
            <span className="li-pipe">{name}</span>
            {shown ? (
              <>
                <Icon name="chevron" className="li-sep" />
                <span className="li-stage-name">{shown.name}</span>
                <span className="li-n">{countOf(shown)}</span>
              </>
            ) : null}
          </>
        }
        tools={
          <DropMenu trigger={<IconButton icon="sliders" label="Search, filter, sort" quiet on={using} data-target="list:tools" />}>
            <Suspense fallback={null}>
              <ListToolItems search={filters} finding={finding} onFind={setFinding} />
            </Suspense>
          </DropMenu>
        }
      >
        {stages.length
          ? stages.map((g) => <MenuItem key={g.id} label={g.name} meta={String(countOf(g))} on={g.id === shown?.id} onSelect={() => setHead({ open, at: at ?? g.id, stage: g.id })} />)
          : null}
      </Head>
      {finding || filters.q || isFiltered(filters) ? (
        <Suspense fallback={null}>
          <ListFind search={filters} finding={finding} />
        </Suspense>
      ) : null}
      <div className="nav-scroll li-body">
        {failed ? (
          <Empty icon="alert" title="These tasks did not load">
            <Button onClick={() => void (listFailed ? list.refetch() : byPriority.refetch())}>Try again</Button>
          </Empty>
        ) : page && (!ranks || ranked) && !more && !shown?.items.length ? (
          <Empty title="No open tasks" />
        ) : null}
        {shown?.items.map((t: TaskItem) => (
          <Row key={t.id} to={taskTo(pipeline, numberOf(t))} n={numberOf(t)} on={isOpen(t, open)} title={t.title} mark={waitsOnMe(t, needs) ? <FlowMark size={10} /> : null} late={!!t.late} who={t.owner_id} at={t.stage_since ?? t.updated_at} />
        ))}
        {more ? (
          <button className="li li-more" type="button" onClick={() => void loadMore(pipeline, filters)}>
            <span className="li-mark" />
            <span className="li-title">More tasks</span>
          </button>
        ) : null}
      </div>
    </>
  );
}

/* ---------------------------------------------------------------- Needs you */

/* Needs you's task rows in its own order (useNeedsYouDrawn: Tico's, the rows down to the open task kept in place if a
   sort lands meanwhile), the open task the row the person is on. Past SIDE_LONG it draws only what is on screen. */
const SIDE_LONG = 50;
const SIDE_ROW_H = 34;
function NeedsYouList({ open, end, noteAs = NEEDS_YOU }: { open: string; end?: ReactNode; noteAs?: string }) {
  const { data: flow } = useQuery(flowQuery);
  const order = useNeedsYouDrawn(flow);
  const rows = useMemo(() => (flow ? needsYouRows(flow, order) : undefined), [flow, order]);
  const items = useMemo(() => {
    const byId = new Map((flow?.items ?? []).map((it) => [it.id, it] as const));
    return flow && order.flatMap((id) => byId.get(id) ?? []);
  }, [flow, order]);
  const at = items?.find((it) => isOpen(it, open))?.id;
  useEffect(() => {
    if (at) holdRow(at);
  }, [at]);
  useEffect(() => () => holdRow(null), []);
  // noted as the list the address names (one in its place, a teammate gone), for the row below an answer
  useNoteRows(noteAs, open, rows);
  const long = useRef<VirtualListHandle>(null);
  const many = !!items && items.length > SIDE_LONG;
  useEffect(() => {
    if (many && at) long.current?.scrollToKey(at);
  }, [many, at]);
  const row = (it: FlowItem) => <Row key={it.id} to={taskTo(it.pipeline_id, numberOf(it))} n={numberOf(it)} on={isOpen(it, open)} title={it.about || it.title} mark={<FlowMark size={10} />} late={!!it.late} who={it.owner_id} at={it.since} />;
  return (
    <>
      <Head list={undefined} end={end} title={<span className="li-stage-name">Needs you</span>} />
      {/* its rows open with Needs you, though it stands in for another list */}
      <div className="nav-scroll li-body" data-list="">
        {items && !items.length ? <Empty title="Nothing waits on you" /> : null}
        {!items ? null : many ? (
          <VirtualList ref={long} items={items} getKey={(it) => it.id} estimateSize={SIDE_ROW_H} measure scrollParent=".li-body" renderItem={row} />
        ) : (
          items.map(row)
        )}
      </div>
    </>
  );
}

/* ---------------------------------------------------------------- a teammate's tasks, search hits */

function WhoList({ who, open, current, end }: { who: string; open: string; current?: Shown; end?: ReactNode }) {
  const actor = useActor(who);
  // one the roster does not know (gone, or never one): Needs you, never every task
  const known = useQuery({ ...directoryQuery, select: (d) => !!d.byId[who] }).data;
  const read = useQuery({ ...ownerListQuery(who), enabled: known !== false });
  const needs = useNeedsYou();
  const tasks = useMemo(() => withCurrent(read.data?.tasks, open, current), [read.data, open, current]);
  const rows = useMemo(() => tasks?.map((t) => ({ id: t.id, n: numberOf(t) })), [tasks]);
  const refused = known === false || (read.error instanceof ApiError && read.error.status === 422);
  useNoteRows(who, open, refused ? undefined : rows);
  if (refused) return <NeedsYouList open={open} end={end} noteAs={who} />;
  return (
    <>
      <Head list={who} end={end} title={<span className="li-stage-name">{actor?.name ?? 'Tasks'}</span>} />
      <Rows read={read} tasks={tasks} open={open} needs={needs} empty="No open tasks" />
    </>
  );
}

/** A list a page drew (lib keepRows), its rows in its order; none drawn since the app loaded: Needs you. */
const READ = { isError: false, refetch: () => undefined };
function DrawnList({ list, open, current, end }: { list: string; open: string; current?: Shown; end?: ReactNode }) {
  const kept = useSyncExternalStore(hearPlaces, () => keptRows(list));
  const needs = useNeedsYou();
  const tasks = useMemo(() => kept && withCurrent(kept.rows as readonly Shown[], open, current), [kept, open, current]);
  const rows = useMemo(() => tasks?.map((t) => ({ id: t.id, n: numberOf(t) })), [tasks]);
  useNoteRows(list, open, rows ?? undefined);
  if (!kept) return <NeedsYouList open={open} end={end} noteAs={list} />;
  return (
    <>
      <Head list={list} end={end} title={<span className="li-stage-name">{kept.title}</span>} />
      <Rows read={READ} tasks={tasks ?? undefined} open={open} needs={needs} empty="No tasks" />
    </>
  );
}

/** Search hits as Cmd+K found them (the same read, the same few), the task picked among them. */
function SearchList({ words, list, open, current, end }: { words: string; list: string; open: string; current?: Shown; end?: ReactNode }) {
  const read = useQuery(taskHitsQuery(words, HITS));
  const needs = useNeedsYou();
  const tasks = useMemo(() => withCurrent(read.data?.tasks, open, current), [read.data, open, current]);
  const rows = useMemo(() => tasks?.map((t) => ({ id: t.id, n: numberOf(t) })), [tasks]);
  useNoteRows(list, open, rows);
  return (
    <>
      <Head list={list} end={end} title={<span className="li-stage-name">“{words}”</span>} />
      <Rows read={read} tasks={tasks} open={open} needs={needs} empty="No tasks found" />
    </>
  );
}

function Rows({ read, tasks, open, needs, empty }: { read: { isError: boolean; refetch: () => unknown }; tasks: readonly Shown[] | undefined; open: string; needs: ReadonlySet<string> | undefined; empty: string }) {
  return (
    <div className="nav-scroll li-body">
      {read.isError && !tasks ? (
        <Empty icon="alert" title="These tasks did not load">
          <Button onClick={() => void read.refetch()}>Try again</Button>
        </Empty>
      ) : tasks && !tasks.length ? (
        <Empty title={empty} />
      ) : null}
      {tasks?.map((t) => (
        <Row key={t.id} to={taskTo(t.pipeline_id, numberOf(t))} n={numberOf(t)} on={isOpen(t, open)} title={t.title ?? ''} mark={needs?.has(t.id) ? <FlowMark size={10} /> : null} late={t.status === 'open' && !!t.late} who={t.owner_id} at={t.updated_at} />
      ))}
    </div>
  );
}
