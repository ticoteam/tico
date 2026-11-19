import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useEffect, useState, type ReactNode } from 'react';
import { useAction } from '@/actions';
import { directoryQuery, meQuery, useActors } from '@/data';
import { useFollow } from '@/live/follow';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Search } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem } from '@/kit/Menu';
import { LatePill } from '@/kit/Pill';
import { TaskRow } from '@/kit/Row';
import { Segmented } from '@/kit/Segmented';
import { VirtualList } from '@/kit/VirtualList';
import { Board, BoardColumn, FilterRow, List, Section, type FilterChip, type PageState } from '@/pagekinds';
import { usePref, writePref } from '@/prefs';
import { BoardFit } from './BoardFit';
import { useCardDrag } from './drag';
import { FilterMenu } from './FilterMenu';
import { NewTask } from './NewTask';
import { Face, FlowButton, isPending, markOf, Movable, OwnerButton, taskHref, waitsOnMe, FlowMark } from './parts';
import { flowQuery, loadMore, pipelinesQuery, pipelineTasksQuery, tagsQuery, type Pipeline, type Stage, type TaskItem } from './queries';
import { filtersOf, LAST_PREF, VIEW_PREF, type TasksSearch, type View } from './search';
import './tasks.css';

/* The mock's caps (list.js): a list stage past twelve rows shows ten, a column past ten cards shows eight, then
   "N more", which opens the rest. A stage opened past this many rows draws only what is on screen. */
const LIST_CAP = 12;
const LIST_SHOW = 10;
const COL_CAP = 10;
const COL_SHOW = 8;
const LONG = 100;
const ROW_H = 34;
const CARD_H = 96;

const FOLD_PREF = 'tasks.fold.v1';

/** One pipeline's open tasks: a board (stages are columns) or a list (stages are sections), with filters in the address. */
export function TasksPage({ pipeline, search }: { pipeline: string; search: TasksSearch }) {
  useFollow({ allTasks: true });
  const pipes = useQuery(pipelinesQuery);
  const p = pipes.data?.find((x) => x.id === pipeline);
  const [saved] = usePref<View>(`${VIEW_PREF}:${pipeline}`, 'board');
  const view = search.view ?? saved;
  const filters = filtersOf(search);
  const tasks = useQuery(pipelineTasksQuery(pipeline, filters));
  const me = useQuery(meQuery).data?.id;
  const flow = useQuery(flowQuery).data?.count ?? 0;
  const [folds, setFolds] = usePref<string[]>(`${FOLD_PREF}:${pipeline}`, []);
  const [newOpen, setNewOpen] = useState(false);

  useEffect(() => {
    if (p) writePref(LAST_PREF, p.id);
  }, [p]);

  // Another pipeline's rows, kept while this one's first read is out, are not this board.
  const other = tasks.isPlaceholderData && tasks.data?.tasks.some((t) => t.pipeline_id !== pipeline);
  const rows = other ? undefined : tasks.data?.tasks;
  const filtered = !!(filters.owner || filters.tag || filters.late || filters.q);

  let state: PageState = 'ready';
  if (pipes.isError || tasks.isError) state = 'fault';
  else if (!pipes.data || !rows) state = 'loading';
  else if (!p || (!rows.length && !filtered)) state = 'empty';

  const newButton = (
    <Button variant="primary" icon="plus" onClick={() => setNewOpen(true)}>
      New task
    </Button>
  );
  const head = {
    title: 'Tasks',
    path: `/tasks/${pipeline}`,
    after: pipes.data && p && <PipelinePick pipelines={pipes.data} current={p} />,
    actions: <HeadActions pipeline={pipeline} view={view} search={search} flow={flow} owner={p?.owner ?? null} onNew={() => setNewOpen(true)} />,
    state,
    empty: p ? { title: `Nothing in ${p.name}`, action: newButton } : { title: 'No such pipeline', action: <Link to="/tasks">Open Tasks</Link> },
    onRetry: () => void (pipes.isError ? pipes.refetch() : tasks.refetch()),
    filter: <Chips search={search} />,
    className: 'tasks-page',
  };
  const fold = (id: string, open: boolean) => setFolds(open ? folds.filter((x) => x !== id) : [...folds, id]);
  const more = () => void loadMore(pipeline, filters);
  const hasMore = !!tasks.data?.next_after;
  const body =
    p && rows ? { p, rows, me, folds, fold, more: hasMore ? more : undefined } : null;

  return (
    <>
      {view === 'board' ? (
        <Board {...head} columns={body ? <Columns {...body} /> : null} />
      ) : (
        <List {...head} sections={body ? <Sections {...body} /> : null} />
      )}
      {pipes.data && newOpen && (
        <NewTask open={newOpen} onOpenChange={setNewOpen} pipelines={pipes.data} pipeline={pipeline} owner={filters.owner ?? null} />
      )}
    </>
  );
}

/* ---------------------------------------------------------------- the head */

function PipelinePick({ pipelines, current }: { pipelines: readonly Pipeline[]; current: Pipeline }) {
  const open = useAction('tasks.open');
  return (
    <Menu
      align="start"
      trigger={
        <button className="scope-pick" type="button" data-target="tasks:pipeline">
          {current.name}
          <Icon name="chevdown" />
        </button>
      }
    >
      {pipelines.map((x) => (
        <MenuItem key={x.id} label={x.name} on={x.id === current.id} onSelect={() => void open.run({ pipeline: x.id })} />
      ))}
    </Menu>
  );
}

/** Search, Filter, the view switch, New task, Flow, the owner (list.js head). */
function HeadActions({
  pipeline,
  view,
  search,
  flow,
  owner,
  onNew,
}: {
  pipeline: string;
  view: View;
  search: TasksSearch;
  flow: number;
  owner: string | null;
  onNew: () => void;
}) {
  const setView = useAction('tasks.setView');
  const filter = useAction('tasks.filter');
  const actors = useActors();
  const tags = useQuery(tagsQuery).data ?? [];
  const [open, setOpen] = useState(!!search.q);
  const [words, setWords] = useState(search.q ?? '');
  // Cleared or set from elsewhere (Clear, Tico): the field follows the address.
  const [seen, setSeen] = useState(search.q);
  if (seen !== search.q) {
    setSeen(search.q);
    setWords(search.q ?? '');
  }
  // Typing narrows the board after a short pause, not on every key.
  useEffect(() => {
    if (words.trim() === (search.q ?? '')) return;
    const t = setTimeout(() => void filter.run({ q: words.trim() || null }), 250);
    return () => clearTimeout(t);
  }, [words, search.q, filter]);
  return (
    <>
      {open ? (
        <Search label="Search tasks" value={words} onValueChange={setWords} placeholder="Search" autoFocus />
      ) : (
        <IconButton icon="search" label="Search" onClick={() => setOpen(true)} />
      )}
      <FilterMenu filters={search} owners={actors} tags={tags.map((t) => t.key)} />
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
      <IconButton icon="plus" label="New task" onClick={onNew} data-target="tasks:new" />
      <FlowButton on={false} count={flow} />
      {owner && <OwnerButton actor={owner} />}
    </>
  );
}

/** The filters that are set, one chip each, then Clear. */
function Chips({ search }: { search: TasksSearch }) {
  const filter = useAction('tasks.filter');
  const dir = useQuery(directoryQuery).data;
  const chips: FilterChip[] = [];
  if (search.owner) chips.push({ label: dir?.byId[search.owner]?.name ?? 'Owner', onClear: () => void filter.run({ owner: null }) });
  if (search.tag) chips.push({ label: search.tag, onClear: () => void filter.run({ tag: null }) });
  if (search.late) chips.push({ label: 'Late', onClear: () => void filter.run({ late: null }) });
  return <FilterRow filters={chips} onClearAll={() => void filter.run({ owner: null, tag: null, late: null, q: null })} />;
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
}

function byStage(stages: readonly Stage[], rows: readonly TaskItem[]) {
  const out = new Map<string, TaskItem[]>(stages.map((s) => [s.id, []]));
  for (const t of rows) if (t.stage_id) out.get(t.stage_id)?.push(t);
  return out;
}

/** The first `show` rows, or all of them once opened; past LONG, only what is on screen is drawn. */
function Capped({
  list,
  cap,
  show,
  more,
  draw,
  moreRow,
  height,
  cards,
}: {
  list: readonly TaskItem[];
  cap: number;
  show: number;
  more: (() => void) | undefined;
  draw: (t: TaskItem) => ReactNode;
  moreRow: (n: number, open: () => void) => ReactNode;
  height: number;
  cards?: boolean;
}) {
  const [all, setAll] = useState(false);
  if (all && list.length > LONG)
    return (
      <VirtualList
        className="tk-long"
        items={list}
        getKey={(t) => t.id}
        estimateSize={height}
        measure={cards}
        renderItem={(t) => draw(t)}
        onReachEnd={more}
      />
    );
  if (all || list.length <= cap) return list.map(draw);
  return (
    <>
      {list.slice(0, show).map(draw)}
      {moreRow(list.length - show, () => setAll(true))}
    </>
  );
}

function Card({ t, p, me }: { t: TaskItem; p: Pipeline; me: string | undefined }) {
  const mark = markOf(t, me);
  const inner = (
    <>
      <span className="card-title">{t.title}</span>
      <span className="card-foot">
        {(mark || t.late) && (
          <span className="card-marks">
            {mark}
            {t.late && <LatePill />}
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
      className="card"
      to="/tasks/$pipeline/$task"
      params={{ pipeline: p.id, task: String(t.number ?? t.id) }}
      title={t.title}
      data-target={`task:${t.number ?? t.id}`}
    >
      {inner}
    </Link>
  );
}

function Columns({ p, rows, me, folds, fold, more }: BodyProps) {
  const move = useAction('tasks.move');
  const drag = useCardDrag((task, to) => void move.run({ task, to }));
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
            count={list.length}
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
              cards
              draw={(t) => (
                <Movable key={t.id} task={t} stages={p.stages} wrap={drag(t)}>
                  <Card t={t} p={p} me={me} />
                </Movable>
              )}
              moreRow={(n, open) => (
                <button key="more" className="col-more" type="button" onClick={open}>
                  {n} more
                </button>
              )}
            />
          </BoardColumn>
        );
      })}
      <BoardFit shape={shape} />
    </>
  );
}

function Sections({ p, rows, me, folds, fold, more }: BodyProps) {
  const groups = byStage(p.stages, rows);
  const none = p.stages.filter((s) => !groups.get(s.id)?.length);
  return (
    <>
      {p.stages
        .filter((s) => groups.get(s.id)?.length)
        .map((s) => {
          const list = groups.get(s.id) ?? [];
          return (
            <Section key={s.id} name={s.name} count={list.length} open={!folds.includes(s.id)} onOpenChange={(o) => fold(s.id, o)} target={`stage:${s.id}`}>
              <Capped
                list={list}
                cap={LIST_CAP}
                show={LIST_SHOW}
                more={more}
                height={ROW_H}
                draw={(t) => (
                  <Movable key={t.id} task={t} stages={p.stages}>
                    <TaskRow
                      title={t.title}
                      href={isPending(t) ? undefined : taskHref(p.id, t)}
                      target={`task:${t.number ?? t.id}`}
                      mark={markOf(t, me)}
                      late={!!t.late}
                      avatar={<Face actor={t.owner_id} />}
                      at={t.stage_since ?? t.updated_at}
                    />
                  </Movable>
                )}
                moreRow={(n, open) => (
                  <button key="more" className="row task row-more" type="button" onClick={open}>
                    <span className="row-mark" />
                    <span className="row-title">{n} more</span>
                  </button>
                )}
              />
            </Section>
          );
        })}
      {none.length > 0 && (
        <div className="stage-line">
          {none.map((s) => (
            <h2 key={s.id} className="sec-name" data-target={`stage:${s.id}`}>
              <span className="sec-fold">
                <span>{s.name}</span>
                <span className="stage-n">0</span>
              </span>
            </h2>
          ))}
        </div>
      )}
    </>
  );
}

