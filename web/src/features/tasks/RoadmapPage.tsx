import { useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import { useAction } from '@/actions';
import { meQuery } from '@/data';
import { useFollow } from '@/live/follow';
import { Button } from '@/kit/Button';
import { FlowMark } from '@/kit/FlowMark';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { ContextMenu, Menu, MenuItem, MenuRule, MenuSub } from '@/kit/Menu';
import { LatePill } from '@/kit/Pill';
import { useCards } from '@/kit/Popover';
import { Row } from '@/kit/Row';
import { List, PageMore, Section } from '@/pagekinds';
import { usePref } from '@/prefs';
import { fmtDate } from '@/lib';
import { Capped, LIST_CAP, LIST_SHOW, RowMore, ROW_H } from './capped';
import { FilterItems, FilterMenu } from './FilterMenu';
import { NewTask } from './NewTask';
import { Face, isPending, taskHref, waitsOnMe } from './parts';
import { isFiltered, loadMore, loadMoreOf, pipelinesQuery, pipelineTasksQuery, shelfParams, shelfQuery, shelfTotal, type Filters, type Pipeline, type TaskItem } from './queries';
import { RequestersDialog } from './Requesters';
import { byHorizon, HORIZONS, roadmapPipeline, stageName, trackOf, type Horizon } from './roadmap';
import { filtersOf, type RoadmapSearch } from './search';
import { Chips } from './TasksPage';
import './tasks.css';

const FOLD_PREF = 'roadmap.fold.v1';

/**
 * Roadmap (more.js roadmapPage): the Features pipeline's tasks as three horizons, Now, Next and Later, then Shipped
 * drawn shut. A row is a task: its track through the pipeline, its owner, its date. A List page.
 */
export function RoadmapPage({ search }: { search: RoadmapSearch }) {
  useFollow({ allTasks: true });
  const pipes = useSuspenseQuery(pipelinesQuery).data;
  const p = roadmapPipeline(pipes);
  if (!p)
    return (
      <List
        title="Roadmap"
        path="/roadmap"
        className="tasks-page"
        state="empty"
        empty={{ title: 'No Features pipeline', action: <Link to="/tasks">Open Tasks</Link> }}
        sections={null}
      />
    );
  return <Roadmap p={p} pipes={pipes} search={search} />;
}

function Roadmap({ p, pipes, search }: { p: Pipeline; pipes: readonly Pipeline[]; search: RoadmapSearch }) {
  const filters = filtersOf(search);
  const open = useSuspenseQuery(pipelineTasksQuery(p.id, filters)).data;
  const shipped = useSuspenseQuery(shelfQuery(p.id, 'done', filters)).data;
  const me = useQuery(meQuery).data?.id;
  const [folds, setFolds] = usePref<Horizon[]>(FOLD_PREF, ['shipped']);
  const [adding, setAdding] = useState(false);
  const requesters = useAction('tasks.openRequesters');

  const rows = open.tasks;
  const done = shipped.tasks;
  const none = !rows.length && !done.length && !isFiltered(filters);

  const fold = (h: Horizon, isOpen: boolean) => setFolds(isOpen ? folds.filter((x) => x !== h) : [...folds, h]);
  const openTask = search.requesters ? [...rows, ...done].find((t) => t.id === search.requesters) : undefined;
  const newFeature = () => setAdding(true);

  return (
    <>
      <List
        title="Roadmap"
        path="/roadmap"
        className="tasks-page"
        actions={<HeadActions search={search} onNew={newFeature} none={none} />}
        more={<MoreMenu p={p} search={search} none={none} />}
        state={none ? 'empty' : 'ready'}
        empty={{
          title: 'No features yet',
          action: (
            <Button icon="plus" onClick={newFeature}>
              New feature
            </Button>
          ),
        }}
        filter={<Chips search={search} />}
        sections={
          <Horizons p={p} rows={rows} done={done} me={me} folds={folds} fold={fold} cut={!!open.next_after} shippedCut={!!shipped.next_after} shippedCount={shelfTotal(shipped)} filters={filters} />
        }
      />
      {adding && <NewTask open onOpenChange={(o) => !o && setAdding(false)} pipelines={pipes} pipeline={p.id} owner={filters.owner ?? null} />}
      {search.requesters && (
        <RequestersDialog
          task={search.requesters}
          title={openTask?.title ?? ''}
          onClose={() => void requesters.run({ task: search.requesters, open: false })}
        />
      )}
    </>
  );
}

/** Filter and New feature (the pipeline's agent is in the Agent bar). Share is left out: the public roadmap pages are
    off on the server. A phone's head keeps New feature; Filter moves into the "⋯". */
function HeadActions({ search, onNew, none }: { search: RoadmapSearch; onNew: () => void; none: boolean }) {
  return (
    <>
      {!none && <FilterMenu filters={search} />}
      {!none && <IconButton icon="plus" label="New feature" onClick={onNew} data-target="tasks:new" data-phone />}
    </>
  );
}

function MoreMenu({ p, search, none }: { p: Pipeline; search: RoadmapSearch; none: boolean }) {
  const openTasks = useAction('tasks.open');
  const edit = useAction('tasks.editPipeline');
  const cards = useCards();
  return (
    <PageMore target="roadmap:more">
      {cards && !none && (
        <MenuSub label="Filter" icon="filter">
          <FilterItems filters={search} />
        </MenuSub>
      )}
      <MenuItem icon="tasks" label={`Open ${p.name}`} onSelect={() => void openTasks.run({ pipeline: p.id })} />
      {p.mayChange && <MenuItem icon="edit" label="Edit pipeline" onSelect={() => void edit.run({ pipeline: p.id })} />}
    </PageMore>
  );
}

interface HorizonsProps {
  p: Pipeline;
  rows: readonly TaskItem[];
  done: readonly TaskItem[];
  me: string | undefined;
  folds: readonly Horizon[];
  fold: (h: Horizon, open: boolean) => void;
  /** The server holds more open features than were read. */
  cut: boolean;
  shippedCut: boolean;
  /** How many shipped, past what was read too; none when unknown. */
  shippedCount: number | undefined;
  filters: Filters;
}

function Horizons({ p, rows, done, me, folds, fold, cut, shippedCut, shippedCount, filters }: HorizonsProps) {
  const cards = useCards();
  const groups = { ...byHorizon(p, rows), shipped: [...done] };
  const more = cut ? () => void loadMore(p.id, filters) : undefined;
  const moreShipped = shippedCut ? () => void loadMoreOf(shelfParams(p.id, 'done', filters)) : undefined;
  return (
    <>
      {HORIZONS.map(([h, name]) => {
        const list = groups[h];
        if (!list.length && h !== 'shipped') return null;
        // A phone folds Shipped to one line that opens the pipeline's Archived (more.js: "14 since Oct 1").
        if (h === 'shipped' && cards && list.length) {
          const n = shippedCount ?? list.length;
          return (
            <div key={h} className="stage-fold">
              <span className="fold-label">Shipped</span>
              <Link to="/tasks/$pipeline" params={{ pipeline: p.id }} search={{ view: 'archived' }}>
                {`${n}${shippedCount === undefined ? '+' : ''} ${n === 1 ? 'feature' : 'features'}`}
              </Link>
            </div>
          );
        }
        const count = h === 'shipped' ? shippedCount : cut ? undefined : list.length;
        return (
          <Section key={h} name={name} count={count} open={!folds.includes(h)} onOpenChange={(o) => fold(h, o)} target={`horizon:${h}`}>
            <Capped
              list={list}
              cap={LIST_CAP}
              show={LIST_SHOW}
              height={ROW_H}
              more={h === 'shipped' ? moreShipped : more}
              draw={(t) => <RoadmapRow key={t.id} t={t} p={p} me={me} />}
              moreRow={(n, open) => <RowMore key="more" n={n} cut={h === 'shipped' ? shippedCut : cut} onClick={open} />}
            />
          </Section>
        );
      })}
    </>
  );
}

/** The track: how far the task is through the pipeline's stages; read out as the stage's name. */
function Track({ p, t }: { p: Pipeline; t: TaskItem }) {
  const { steps, at } = trackOf(p, t);
  const name = stageName(p, t);
  return (
    <span className="track" role="img" aria-label={name} title={name}>
      {Array.from({ length: steps }, (_, i) => (
        <i key={i} className={i < at ? 'is-done' : i === at ? 'is-on' : undefined} />
      ))}
    </span>
  );
}

/** A roadmap row (more.js roadmapRow): mark, title, Late and the lock, the track, owner, date; "⋯" holds Requesters. */
function RoadmapRow({ t, p, me }: { t: TaskItem; p: Pipeline; me: string | undefined }) {
  const requesters = useAction('tasks.openRequesters');
  const move = useAction('tasks.move');
  const locked = !!t.visibility && t.visibility !== 'public';
  const row = (
    <Row className="task rm" href={isPending(t) ? undefined : taskHref(p.id, t)} title={t.title} target={`task:${t.number ?? t.id}`}>
      <span className="row-mark">{waitsOnMe(t, me) && <FlowMark />}</span>
      <span className="row-title">{t.title}</span>
      <span className="row-sub">
        <span className="row-pills">
          {t.late && <LatePill />}
          {locked && (
            <span className="rm-lock" title="Not public">
              <Icon name="lock" />
            </span>
          )}
        </span>
        <Track p={p} t={t} />
      </span>
      {t.owner_id ? <Face actor={t.owner_id} /> : <span className="av-gap" />}
      <span className="row-age">{t.due_at ? fmtDate(t.due_at) : ''}</span>
    </Row>
  );
  if (isPending(t)) return <div className="tk-item">{row}</div>;
  const items = [
    <MenuItem key="rq" icon="people" label="Requesters" onSelect={() => void requesters.run({ task: t.id })} />,
    ...(t.status === 'open'
      ? [
          <MenuRule key="rule" />,
          ...p.stages.map((s) => (
            <MenuItem key={s.id} label={s.name} on={s.id === t.stage_id} onSelect={() => s.id !== t.stage_id && void move.run({ task: t.id, to: s.id })} />
          )),
        ]
      : []),
  ];
  return (
    <div className="tk-item">
      <ContextMenu items={items}>{row}</ContextMenu>
      <Menu trigger={<IconButton icon="more" label="More" size="sm" className="tk-more" />}>{items}</Menu>
    </div>
  );
}
