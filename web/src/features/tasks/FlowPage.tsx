import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useFollow } from '@/live/follow';
import { FlowMark } from '@/kit/FlowMark';
import { Icon } from '@/kit/Icon';
import { TaskRow } from '@/kit/Row';
import { List, Section, type PageState } from '@/pagekinds';
import { Dot } from '@/tico/dot';
import { Capped, LIST_CAP, LIST_SHOW, RowMore, ROW_H } from './capped';
import { Face, FlowButton } from './parts';
import { flowQuery, pipelinesQuery, type FlowItem, type Pipeline } from './queries';
import './tasks.css';

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
  return (
    <Section
      name={
        <>
          {g.pipe}
          <Icon name="chevron" className="sec-sep" />
          {g.stage}
        </>
      }
      label={`${g.pipe}, ${g.stage}`}
      count={g.items.length}
      target={`stage:${g.key}`}
    >
      <Capped
        list={g.items}
        cap={LIST_CAP}
        show={LIST_SHOW}
        height={ROW_H}
        more={undefined}
        draw={(it) => <FlowRow key={it.id} it={it} />}
        moreRow={(n, open) => <RowMore key="more" n={n} onClick={open} />}
      />
    </Section>
  );
}

/** Nothing waits on you: the Dot at rest, then a way back to Tasks (list.js flowBody). */
const Clear = () => (
  <div className="empty">
    <Dot size={44} state="rest" />
    <h2>Nothing waits on you</h2>
    <Link to="/tasks" className="btn">
      Open Tasks
    </Link>
  </div>
);

/** Flow: every task that waits on you, in every pipeline, one after another. A row opens it in Flow. */
export function FlowPage() {
  useFollow({ allTasks: true }, [['tasks', 'list'], ['flow']]);
  const flow = useQuery(flowQuery);
  const pipes = useQuery(pipelinesQuery);
  let state: PageState = 'ready';
  if (flow.isError || pipes.isError) state = 'fault';
  else if (!flow.data || !pipes.data) state = 'loading';
  let sections = null;
  if (flow.data && pipes.data)
    sections = flow.data.items.length ? groups(flow.data.items, pipes.data).map((g) => <FlowSection key={g.key} g={g} />) : <Clear />;
  return (
    <List
      title="Tasks"
      path="/flow"
      actions={<FlowButton on count={flow.data?.count ?? 0} />}
      state={state}
      onRetry={() => void (flow.isError ? flow.refetch() : pipes.refetch())}
      className="tasks-page"
      sections={sections}
    />
  );
}
