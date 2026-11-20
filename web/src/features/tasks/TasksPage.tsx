import { useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useEffect, useState, type MouseEvent, type ReactNode } from 'react';
import { useAction } from '@/actions';
import { meQuery, useActor, useActors } from '@/data';
import { useFollow } from '@/live/follow';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { announce, useDrag } from '@/kit/Drag';
import { Empty } from '@/kit/Empty';
import { SettledSearch } from '@/kit/Field';
import { FlowMark } from '@/kit/FlowMark';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule, MenuSub } from '@/kit/Menu';
import { useCards } from '@/kit/Popover';
import { LatePill } from '@/kit/Pill';
import { TaskRow } from '@/kit/Row';
import { Segmented } from '@/kit/Segmented';
import { ValueButton } from '@/kit/ValueButton';
import { Board, BoardColumn, FilterRow, List, PageMore, Section, type FilterChip } from '@/pagekinds';
import { usePref, writePref } from '@/prefs';
import { ArchivedSections, useShelves } from './Archived';
import { BoardFit } from './BoardFit';
import { Capped, CARD_H, COL_CAP, COL_SHOW, LIST_CAP, LIST_SHOW, moreText, PHONE_ROW_H, RowMore, ROW_H } from './capped';
import { DropDialog } from './DropDialog';
import { DUE_NAMES, FilterItems, FilterMenu } from './FilterMenu';
import { SelectBar, StageMenu } from './many';
import { NewTask } from './NewTask';
import { Blocks, Face, FlowButton, isPending, markOf, Movable, OwnerButton, taskHref, waitsOnMe } from './parts';
import {
  flowQuery,
  isFiltered,
  loadMore,
  pipelinesQuery,
  pipelineTasksQuery,
  stageCount,
  tagsQuery,
  type Filters,
  type Pipeline,
  type Stage,
  type TaskItem,
} from './queries';
import { filtersOf, LAST_PREF, VIEW_PREF, type TasksSearch, type View } from './search';
import './tasks.css';

const FOLD_PREF = 'tasks.fold.v1';

/** One pipeline's tasks: open ones as a board (stages are columns) or a list (stages are sections), or the archived ones. */
export function TasksPage({ pipeline, search }: { pipeline: string; search: TasksSearch }) {
  useFollow({ allTasks: true });
  const pipes = useSuspenseQuery(pipelinesQuery).data;
  const p = pipes.find((x) => x.id === pipeline);
  const [saved] = usePref<'board' | 'list'>(`${VIEW_PREF}:${pipeline}`, 'board');
  const view: View = search.view ?? saved;

  useEffect(() => {
    if (p) writePref(LAST_PREF, p.id);
  }, [p]);

  const head = {
    path: `/tasks/${pipeline}`,
    after: p && <PipelinePick pipelines={pipes} current={p} />,
    className: 'tasks-page',
  };
  if (view === 'archived') return <ArchivedPage pipeline={pipeline} p={p} search={search} head={head} />;
  return <OpenPage pipeline={pipeline} p={p} pipes={pipes} search={search} view={view} head={head} />;
}

interface PageProps {
  pipeline: string;
  p: Pipeline | undefined;
  pipes: readonly Pipeline[];
  search: TasksSearch;
  head: { path: string; after: ReactNode; className: string };
}

function OpenPage({ pipeline, p, pipes, search, view, head }: PageProps & { view: 'board' | 'list' }) {
  const filters = filtersOf(search);
  const tasks = useSuspenseQuery(pipelineTasksQuery(pipeline, filters)).data;
  const me = useQuery(meQuery).data?.id;
  const [folds, setFolds] = usePref<string[]>(`${FOLD_PREF}:${pipeline}`, []);
  const edit = useAction('tasks.editPipeline');
  const [newIn, setNewIn] = useState<{ stage: string | null } | null>(null);
  // A phone's head has no room for the search: "⋯" › Search brings its line up under the head.
  const cards = useCards();
  const [finding, setFinding] = useState(false);
  // Select: null while off; the picked task ids while on. Another pipeline starts with it off.
  const [picked, setPicked] = useState<{ pipeline: string; ids: ReadonlySet<string> } | null>(null);
  const picking = picked?.pipeline === pipeline ? picked.ids : null;

  const rows = tasks.tasks;
  const filtered = isFiltered(filters);
  const cut = !!tasks.next_after;
  // A stage's count is the server's (under the filters, past what was read); a read without counts has only its rows.
  const countOf = (stage: string, loaded: number) => stageCount(tasks, stage, loaded) ?? (cut ? undefined : loaded);
  const onNew = () => setNewIn({ stage: null });

  const page = {
    ...head,
    title: 'Tasks',
    search: !cards || finding || search.q ? <TaskFind search={search} /> : undefined,
    actions: <HeadActions pipeline={pipeline} view={view} search={search} p={p} onNew={onNew} />,
    more: p && (
      <MoreMenu
        p={p}
        pipes={pipes}
        onSelect={() => setPicked({ pipeline, ids: new Set() })}
        phone={cards ? { view, search, onNew, onFind: () => setFinding(true) } : undefined}
      />
    ),
    state: p ? ('ready' as const) : ('empty' as const),
    empty: { title: 'No such pipeline', action: <Link to="/tasks">Open Tasks</Link> },
    filter:
      picking && p ? (
        <SelectBar p={p} ids={[...picking]} onDone={() => setPicked(null)} />
      ) : (
        <Chips search={search} />
      ),
  };
  const toggle = picking
    ? (id: string) => {
        const next = new Set(picking);
        if (!next.delete(id)) next.add(id);
        setPicked({ pipeline, ids: next });
      }
    : undefined;
  const fold = (id: string, open: boolean) => setFolds(open ? folds.filter((x) => x !== id) : [...folds, id]);
  const more = cut ? () => void loadMore(pipeline, filters) : undefined;
  const body: BodyProps | null = p
    ? { p, rows, me, folds, fold, more, cut, countOf, picking, toggle, onNew: (stage) => setNewIn({ stage }), onEdit: () => void edit.run({ pipeline }) }
    : null;
  const bare = body && !body.rows.length && !filtered && <Bare p={body.p} board={view === 'board'} onNew={onNew} />;

  return (
    <>
      {view === 'board' ? (
        <Board {...page} columns={bare || (body ? <Columns {...body} /> : null)} />
      ) : (
        <List {...page} sections={bare || (body ? <Sections {...body} edit={p?.mayChange} /> : null)} />
      )}
      {newIn && (
        <NewTask
          open
          onOpenChange={(o) => !o && setNewIn(null)}
          pipelines={pipes}
          pipeline={pipeline}
          stage={newIn.stage}
          owner={filters.owner ?? null}
        />
      )}
    </>
  );
}

function ArchivedPage({ pipeline, p, search, head }: Omit<PageProps, 'pipes'>) {
  const filters: Filters = filtersOf(search);
  const shelves = useShelves(pipeline, filters);
  const edit = useAction('tasks.editPipeline');
  const empty = !p || (Object.values(shelves).every((x) => !x.tasks.length) && !isFiltered(filters));
  return (
    <List
      {...head}
      title="Archived"
      crumb={[{ label: 'Tasks', to: `/tasks/${encodeURIComponent(pipeline)}` }]}
      search={<TaskFind search={search} />}
      actions={<HeadActions pipeline={pipeline} view="archived" search={search} p={p} />}
      // "⋯" stays last in the head (list.js archived): the pipeline's own verb, for whoever may change it
      more={
        <PageMore target="tasks:more">
          {p?.mayChange && <MenuItem icon="edit" label="Edit pipeline" onSelect={() => void edit.run({ pipeline: p.id })} />}
        </PageMore>
      }
      state={empty ? 'empty' : 'ready'}
      empty={{ title: p ? `Nothing archived in ${p.name}` : 'No such pipeline' }}
      filter={<Chips search={search} />}
      sections={p ? <ArchivedSections p={p} filters={filters} shelves={shelves} /> : null}
    />
  );
}

/* ---------------------------------------------------------------- the head */

function PipelinePick({ pipelines, current }: { pipelines: readonly Pipeline[]; current: Pipeline }) {
  const open = useAction('tasks.open');
  return (
    <Menu
      align="start"
      trigger={
        <ValueButton look="scope" data-target="tasks:pipeline">
          {current.name}
        </ValueButton>
      }
    >
      {pipelines.map((x) => (
        <MenuItem key={x.id} label={x.name} on={x.id === current.id} onSelect={() => void open.run({ pipeline: x.id })} />
      ))}
    </Menu>
  );
}

/** What a phone's "⋯" adds first, since its head keeps only Flow and "⋯" (list.js phoneMore). */
interface PhoneMore {
  view: 'board' | 'list';
  search: TasksSearch;
  onNew: () => void;
  onFind: () => void;
}

/** The page's "⋯" (list.js moreItems): Archived, Select, then Edit pipeline, and New pipeline (not drawn in the mock;
    quiet here) for whoever may make one: the company's owner, or who may change every pipeline (an Admin). On a phone,
    New task, Search, Filter and the other view come first. */
function MoreMenu({ p, pipes, onSelect, phone }: { p: Pipeline; pipes: readonly Pipeline[]; onSelect: () => void; phone?: PhoneMore }) {
  const setView = useAction('tasks.setView');
  const edit = useAction('tasks.editPipeline');
  const make = useAction('tasks.newPipeline');
  const owner = useQuery(meQuery).data?.role === 'owner';
  const actors = useActors();
  const tags = useQuery(tagsQuery).data ?? [];
  const mayMake = owner || (pipes.length > 0 && pipes.every((x) => x.mayChange));
  return (
    <PageMore target="tasks:more">
      {phone && (
        <>
          <MenuItem icon="plus" label="New task" onSelect={phone.onNew} />
          <MenuItem icon="search" label="Search" onSelect={phone.onFind} />
          <MenuSub icon="filter" label="Filter">
            <FilterItems filters={phone.search} actors={actors} tags={tags.map((t) => t.key)} />
          </MenuSub>
          {phone.view === 'list' ? (
            <MenuItem icon="columns" label="Columns" onSelect={() => void setView.run({ pipeline: p.id, view: 'board' })} />
          ) : (
            <MenuItem icon="list" label="List" onSelect={() => void setView.run({ pipeline: p.id, view: 'list' })} />
          )}
          <MenuRule />
        </>
      )}
      <MenuItem icon="archive" label="Archived" onSelect={() => void setView.run({ pipeline: p.id, view: 'archived' })} />
      <MenuItem icon="square" label="Select" onSelect={onSelect} />
      {(p.mayChange || mayMake) && <MenuRule />}
      {p.mayChange && <MenuItem icon="edit" label="Edit pipeline" onSelect={() => void edit.run({ pipeline: p.id })} />}
      {mayMake && <MenuItem icon="plus" label="New pipeline" onSelect={() => void make.run({ pipeline: p.id })} />}
    </PageMore>
  );
}

/** The head's search (the page kind's `search` slot): the field, as the kit draws it; on a phone it has its own line
    under the head, opened from "⋯". Typing narrows the board after a short pause, not on every key. */
function TaskFind({ search }: { search: TasksSearch }) {
  const filter = useAction('tasks.filter');
  const cards = useCards();
  return (
    <SettledSearch
      label="Search tasks"
      placeholder="Search"
      value={search.q ?? ''}
      onSettle={(q) => void filter.run({ q: q || null })}
      autoFocus={cards && !search.q}
    />
  );
}

/** Filter, the view switch, New task, Flow, the owner (list.js head). A phone keeps only Flow; the rest is under "⋯".
    Archived keeps the filter. */
function HeadActions({
  pipeline,
  view,
  search,
  p,
  onNew,
}: {
  pipeline: string;
  view: View;
  search: TasksSearch;
  p: Pipeline | undefined;
  onNew?: () => void;
}) {
  const setView = useAction('tasks.setView');
  const actors = useActors();
  const tags = useQuery(tagsQuery).data ?? [];
  return (
    <>
      <FilterMenu filters={search} actors={actors} tags={tags.map((t) => t.key)} phone={view === 'archived'} />
      {view !== 'archived' && (
        <span data-target="tasks:view">
          <Segmented
            label="View"
            value={view}
            onValueChange={(v) => void setView.run({ pipeline, view: v })}
            options={[
              { value: 'board', label: 'Board', icon: 'columns' },
              { value: 'list', label: 'List', icon: 'list' },
            ]}
          />
        </span>
      )}
      {onNew && <IconButton icon="plus" label="New task" onClick={onNew} data-target="tasks:new" />}
      {view !== 'archived' && <OpenFlowButton />}
      {p?.owner && <OwnerButton actor={p.owner} />}
    </>
  );
}

function OpenFlowButton() {
  const { data = 0 } = useQuery({ ...flowQuery, select: (flow) => flow.count });
  return <FlowButton on={false} count={data} />;
}

/** The filters that are set, one chip each, then Clear. */
export function Chips({ search }: { search: Filters }) {
  const filter = useAction('tasks.filter');
  const owner = useActor(search.owner)?.name;
  const waiting = useActor(search.waiting)?.name;
  const asked = useActor(search.asked)?.name;
  const chips: FilterChip[] = [];
  if (search.owner) chips.push({ label: owner ?? 'Owner', onClear: () => void filter.run({ owner: null }) });
  if (search.waiting) chips.push({ label: `Waiting on ${waiting ?? 'someone'}`, onClear: () => void filter.run({ waiting: null }) });
  if (search.asked) chips.push({ label: `Asked by ${asked ?? 'someone'}`, onClear: () => void filter.run({ asked: null }) });
  if (search.tag) chips.push({ label: search.tag, onClear: () => void filter.run({ tag: null }) });
  if (search.due) chips.push({ label: DUE_NAMES[search.due], onClear: () => void filter.run({ due: null }) });
  if (search.late) chips.push({ label: 'Late', onClear: () => void filter.run({ late: null }) });
  return (
    <FilterRow
      filters={chips}
      onClearAll={() => void filter.run({ owner: null, waiting: null, asked: null, tag: null, due: null, late: null, q: null })}
    />
  );
}

/* ---------------------------------------------------------------- the body */

interface BodyProps {
  p: Pipeline;
  rows: readonly TaskItem[];
  me: string | undefined;
  folds: readonly string[];
  fold: (stage: string, open: boolean) => void;
  /** Read the next page; only while the server has one. */
  more: (() => void) | undefined;
  /** The server holds more than was read. */
  cut: boolean;
  /** A stage's count: what was read when that is all, else the Floor's, else none. */
  countOf: (stage: string, loaded: number) => number | undefined;
  /** Select is on: the picked ids. A click picks instead of opening. */
  picking: ReadonlySet<string> | null;
  toggle: ((id: string) => void) | undefined;
  onNew: (stage: string) => void;
  onEdit: () => void;
}

function byStage(stages: readonly Stage[], rows: readonly TaskItem[]) {
  const out = new Map<string, TaskItem[]>(stages.map((s) => [s.id, []]));
  for (const t of rows) if (t.stage_id) out.get(t.stage_id)?.push(t);
  return out;
}

/** In Select, the box in the mark's place. */
const Pick = ({ on }: { on: boolean }) => (
  <span className={on ? 'check is-on' : 'check'} aria-hidden="true">
    <span className="check-box">
      <Icon name="check" />
    </span>
  </span>
);

const picker = (toggle: BodyProps['toggle'], id: string) =>
  toggle &&
  ((e: MouseEvent) => {
    e.preventDefault();
    toggle(id);
  });

function Card({ t, p, me, picked, onPick }: { t: TaskItem; p: Pipeline; me: string | undefined; picked?: boolean; onPick?: (e: MouseEvent) => void }) {
  const mark = onPick ? <Pick on={!!picked} /> : markOf(t, me);
  const inner = (
    <>
      <span className="card-title">{t.title}</span>
      <span className="card-foot">
        {(mark || t.late || t.blocks?.length) && (
          <span className="card-marks">
            {mark}
            {t.late && <LatePill />}
            <Blocks t={t} />
          </span>
        )}
        <span className="card-end">
          <Face actor={t.owner_id} />
          <Ago at={t.stage_since ?? t.updated_at} className="row-age" />
        </span>
      </span>
    </>
  );
  if (isPending(t))
    return (
      <div className="card" title={t.title}>
        {inner}
      </div>
    );
  return (
    <Link
      className={picked ? 'card is-selected' : 'card'}
      to="/tasks/$pipeline/$task"
      params={{ pipeline: p.id, task: String(t.number ?? t.id) }}
      title={t.title}
      data-target={`task:${t.number ?? t.id}`}
      aria-pressed={onPick ? !!picked : undefined}
      onClick={onPick}
      // the card's wrapper drags it, not the link (while picking nothing drags)
      draggable={picked === undefined ? false : undefined}
    >
      {inner}
    </Link>
  );
}

/** Where a board card lands: a stage's column, or another card (its subtask or a merge). */
const COLUMN = '.col[data-target^="stage:"]';
const CARD = '.tk-item[data-task]';

function Columns({ p, rows, me, folds, fold, more, cut, countOf, picking, toggle }: BodyProps) {
  const move = useAction('tasks.move');
  const [dropped, setDropped] = useState<{ task: string; onto: string } | null>(null);
  // A card let go over a stage moves there; over another card it asks: a subtask or a merge (DropDialog).
  const drag = useDrag({
    zones: `${COLUMN}, ${CARD}`,
    controls: '.tk-more',
    takes: (zone, card) => zone !== card,
    drop: (zone, card) => {
      const task = card.getAttribute('data-task') ?? '';
      const onto = zone.getAttribute('data-task');
      if (onto) return setDropped({ task, onto });
      if (zone.contains(card)) return;
      const to = zone.getAttribute('data-target')?.slice('stage:'.length) ?? '';
      const name = p.stages.find((s) => s.id === to)?.name;
      void move.run({ task, to }).then((r) => r.ok && announce(`${r.did} to ${name}`));
    },
  });
  const groups = byStage(p.stages, rows);
  const shape = p.stages.map((s) => `${s.id}:${groups.get(s.id)?.length ?? 0}:${folds.includes(s.id) ? 1 : 0}`).join(',');
  return (
    <>
      {p.stages.map((s) => {
        const list = groups.get(s.id) ?? [];
        const waits = list.filter((t) => waitsOnMe(t, me)).length;
        const folded = !list.length || folds.includes(s.id);
        return (
          <BoardColumn
            key={s.id}
            name={s.name}
            count={countOf(s.id, list.length)}
            folded={folded}
            onToggle={list.length ? () => fold(s.id, folded) : undefined}
            target={`stage:${s.id}`}
            waits={
              waits > 0 && (
                <span className="col-flow" title={`${waits} wait${waits === 1 ? 's' : ''} on you`}>
                  <FlowMark />
                  <b>{waits}</b>
                </span>
              )
            }
          >
            <Capped
              list={list}
              cap={COL_CAP}
              show={COL_SHOW}
              more={more}
              height={CARD_H}
              measure
              draw={(t) => (
                <Movable key={t.id} task={t} stages={p.stages} wrap={picking ? undefined : { 'data-task': t.id, ref: drag }}>
                  <Card t={t} p={p} me={me} picked={picking?.has(t.id)} onPick={picker(toggle, t.id)} />
                </Movable>
              )}
              moreRow={(n, open) => (
                <button key="more" className="col-more" type="button" onClick={open}>
                  {moreText(n, cut)}
                </button>
              )}
            />
          </BoardColumn>
        );
      })}
      <BoardFit shape={shape} />
      {dropped && <DropDialog task={dropped.task} onto={dropped.onto} onClose={() => setDropped(null)} />}
    </>
  );
}

function Sections({ p, rows, me, folds, fold, more, cut, countOf, picking, toggle, onNew, onEdit, edit }: BodyProps & { edit?: boolean }) {
  const cards = useCards();
  const groups = byStage(p.stages, rows);
  const none = p.stages.filter((s) => !groups.get(s.id)?.length);
  return (
    <>
      {p.stages
        .filter((s) => groups.get(s.id)?.length)
        .map((s) => {
          const list = groups.get(s.id) ?? [];
          return (
            <Section
              key={s.id}
              name={s.name}
              count={countOf(s.id, list.length)}
              open={!folds.includes(s.id)}
              onOpenChange={(o) => fold(s.id, o)}
              target={`stage:${s.id}`}
              actions={
                <>
                  <IconButton icon="plus" label={`New task in ${s.name}`} size="sm" className="ib-ghost" onClick={() => onNew(s.id)} />
                  <StageMenu p={p} stage={s} ids={list.filter((t) => !isPending(t)).map((t) => t.id)} onEdit={edit ? onEdit : undefined} />
                </>
              }
            >
              <Capped
                list={list}
                cap={LIST_CAP}
                show={LIST_SHOW}
                more={more}
                height={cards ? PHONE_ROW_H : ROW_H}
                measure={cards}
                draw={(t) => (
                  <Movable key={t.id} task={t} stages={p.stages}>
                    <TaskRow
                      title={t.title}
                      href={isPending(t) ? undefined : taskHref(p.id, t)}
                      target={`task:${t.number ?? t.id}`}
                      mark={picking ? <Pick on={picking.has(t.id)} /> : markOf(t, me)}
                      selected={picking?.has(t.id)}
                      aria-pressed={picking ? picking.has(t.id) : undefined}
                      onClick={picker(toggle, t.id)}
                      late={!!t.late}
                      pills={<Blocks t={t} />}
                      avatar={<Face actor={t.owner_id} />}
                      at={t.stage_since ?? t.updated_at}
                    />
                  </Movable>
                )}
                moreRow={(n, open) => <RowMore key="more" n={n} cut={cut} onClick={open} />}
              />
            </Section>
          );
        })}
      <StageLine stages={none} countOf={countOf} />
    </>
  );
}

/** Stages with no loaded rows: their names folded on one line under the list. */
function StageLine({ stages, countOf }: { stages: readonly Stage[]; countOf?: BodyProps['countOf'] }) {
  if (!stages.length) return null;
  return (
    <div className="stage-line">
      {stages.map((s) => (
        <h2 key={s.id} className="sec-name" data-target={`stage:${s.id}`}>
          <span className="sec-fold">
            <span>{s.name}</span>
            <span className="stage-n">{countOf ? countOf(s.id, 0) : 0}</span>
          </span>
        </h2>
      ))}
    </div>
  );
}

/** Nothing in the pipeline: its stages stay (the board's heads, the list's folded line), with one line and New task
    (list.js listBody, boardBody). */
function Bare({ p, board, onNew }: { p: Pipeline; board: boolean; onNew: () => void }) {
  const empty = (
    <Empty title={`Nothing in ${p.name}`}>
      <Button icon="plus" onClick={onNew}>
        New task
      </Button>
    </Empty>
  );
  if (!board)
    return (
      <>
        {empty}
        <StageLine stages={p.stages} />
      </>
    );
  return (
    <>
      <div className="tk-heads">
        {p.stages.map((s) => (
          <BoardColumn key={s.id} name={s.name} target={`stage:${s.id}`} />
        ))}
      </div>
      {empty}
    </>
  );
}
