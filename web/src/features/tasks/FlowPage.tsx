import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useId, useState } from 'react';
import { useFollow } from '@/live/follow';
import { Icon } from '@/kit/Icon';
import { TaskRow } from '@/kit/Row';
import { VirtualList } from '@/kit/VirtualList';
import { List, type PageState } from '@/pagekinds';
import { Face, FlowButton, FlowMark } from './parts';
import { flowQuery, pipelinesQuery, type FlowItem, type Pipeline } from './queries';
import './tasks.css';

const CAP = 12;
const SHOW = 10;
/** Opened past this many rows, a section draws only what is on screen. */
const LONG = 100;
const ROW_H = 34;

interface Group {
  key: string;
  pipe: string;
  stage: string;
  items: FlowItem[];
}

/** Sections in pipeline order, then stage order: "Engineering › QA". */
function groups(items: readonly FlowItem[], pipelines: readonly Pipeline[]): Group[] {
  const out = new Map<string, Group>();
  const rank = (it: FlowItem) => {
    const pi = pipelines.findIndex((p) => p.id === it.pipeline_id);
    const si = pi < 0 ? -1 : (pipelines[pi]?.stages.findIndex((s) => s.id === it.stage_id) ?? -1);
    return (pi < 0 ? 999 : pi) * 1000 + (si < 0 ? 999 : si);
  };
  for (const it of [...items].sort((a, b) => rank(a) - rank(b))) {
    const key = `${it.pipeline_id}:${it.stage_id}`;
    let g = out.get(key);
    if (!g) {
      const p = pipelines.find((x) => x.id === it.pipeline_id);
      const s = p?.stages.find((x) => x.id === it.stage_id) ?? (p?.done?.id === it.stage_id ? p?.done : null);
      g = { key, pipe: p?.name ?? 'Tasks', stage: s?.name ?? 'Open', items: [] };
      out.set(key, g);
    }
    g.items.push(it);
  }
  return [...out.values()];
}

function FlowRow({ it }: { it: FlowItem }) {
  return (
    <TaskRow
      title={it.title}
      href={`/flow/${it.number ?? encodeURIComponent(it.id)}`}
      target={`task:${it.number ?? it.id}`}
      mark={<FlowMark />}
      late={it.late}
      avatar={<Face actor={it.owner_id} />}
      at={it.since}
    />
  );
}

/** A section named by its pipeline, then its stage (list.js path()); its name folds it like every section. */
function FlowSection({ g }: { g: Group }) {
  const [open, setOpen] = useState(true);
  const [all, setAll] = useState(false);
  const id = useId();
  const shown = all || g.items.length <= CAP ? g.items : g.items.slice(0, SHOW);
  return (
    <section className={open ? 'stage pk-sec' : 'stage pk-sec is-folded'} aria-label={`${g.pipe}, ${g.stage}`} data-target={`stage:${g.key}`}>
      <header className="stage-head">
        <h2 className="sec-name">
          <button type="button" className="sec-fold" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
            <span>
              {g.pipe}
              <Icon name="chevron" className="sec-sep" />
              {g.stage}
            </span>
            <span className="stage-n">{g.items.length}</span>
            <Icon name="chevdown" />
          </button>
        </h2>
      </header>
      <div id={id} hidden={!open}>
        {open && shown.length > LONG ? (
          <VirtualList className="tk-long" items={shown} getKey={(it) => it.id} estimateSize={ROW_H} renderItem={(it) => <FlowRow it={it} />} />
        ) : (
          open && shown.map((it) => <FlowRow key={it.id} it={it} />)
        )}
        {open && shown.length < g.items.length && (
          <button className="row task row-more" type="button" onClick={() => setAll(true)}>
            <span className="row-mark" />
            <span className="row-title">{g.items.length - shown.length} more</span>
          </button>
        )}
      </div>
    </section>
  );
}

/** Flow: every task that waits on you, in every pipeline, one after another. A row opens it in Flow. */
export function FlowPage() {
  useFollow({ allTasks: true }, [['tasks', 'list'], ['flow']]);
  const flow = useQuery(flowQuery);
  const pipes = useQuery(pipelinesQuery);
  let state: PageState = 'ready';
  if (flow.isError || pipes.isError) state = 'fault';
  else if (!flow.data || !pipes.data) state = 'loading';
  else if (!flow.data.items.length) state = 'empty';
  return (
    <List
      title="Tasks"
      path="/flow"
      actions={<FlowButton on count={flow.data?.count ?? 0} />}
      state={state}
      empty={{ title: 'Nothing waits on you', action: <Link to="/tasks" className="btn">Open Tasks</Link> }}
      onRetry={() => void (flow.isError ? flow.refetch() : pipes.refetch())}
      className="tasks-page"
      sections={flow.data && pipes.data ? groups(flow.data.items, pipes.data).map((g) => <FlowSection key={g.key} g={g} />) : null}
    />
  );
}
