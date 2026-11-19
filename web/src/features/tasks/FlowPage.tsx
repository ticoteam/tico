import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useFollow } from '@/live/follow';
import { FlowMark } from '@/kit/FlowMark';
import { Icon } from '@/kit/Icon';
import { TaskRow } from '@/kit/Row';
import { List, Section, type PageState } from '@/pagekinds';
import { Capped, LIST_CAP, LIST_SHOW, RowMore, ROW_H } from './capped';
import { Blocks, Face, FlowButton } from './parts';
import { flowGroups, flowQuery, pipelinesQuery, type FlowGroup, type FlowItem } from './queries';
import './tasks.css';

function FlowRow({ it }: { it: FlowItem }) {
  return (
    <TaskRow
      title={it.title}
      href={`/flow/${it.number ?? encodeURIComponent(it.id)}`}
      target={`task:${it.number ?? it.id}`}
      mark={<FlowMark />}
      late={it.late}
      pills={<Blocks t={it} />}
      avatar={<Face actor={it.owner_id} />}
      at={it.since}
    />
  );
}

/** A section named by its pipeline, then its stage (list.js path()); its name folds it like every section. */
function FlowSection({ g }: { g: FlowGroup }) {
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

/** Flow: every task that waits on you, in every pipeline, one after another. A row opens it in Flow. It is Tasks with
    the Flow button pressed (list.js flow). */
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
      empty={{
        title: 'Nothing waits on you',
        action: (
          <Link to="/tasks" className="btn">
            Open Tasks
          </Link>
        ),
      }}
      onRetry={() => void (flow.isError ? flow.refetch() : pipes.refetch())}
      className="tasks-page"
      sections={flow.data && pipes.data ? flowGroups(flow.data.items, pipes.data).map((g) => <FlowSection key={g.key} g={g} />) : null}
    />
  );
}
