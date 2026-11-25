import { useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { Empty } from '@/kit/Empty';
import { Row } from '@/kit/Row';
import { useFollow } from '@/live/follow';
import { List, ReadFault, Section } from '@/pagekinds';
import { NeedsYouList } from './NeedsYouList';
import { useNeedsYouSorting } from './NeedsYouPage';
import { NEEDS_YOU_VIEWS } from './NeedsYouViews';
import { Face } from './parts';
import { flowQuery, pipelinesQuery } from './queries';
import './tasks.css';

/** Tasks: what waits on you on top (Needs you, the list that is also beside a task opened from it), then the pipelines.
    On a phone the same stack, Needs you first. Needs you's head links its other views: All, Snoozed, Done. The
    pipelines show at once; Needs you's section fills as its read comes, and a read that fails says so there alone. */
export function TasksHome() {
  useFollow({ allTasks: true });
  const pipes = useSuspenseQuery(pipelinesQuery).data;
  const flow = useQuery(flowQuery);
  const count = flow.data?.count;
  // Needs you here is the Needs you page: it asks Tico to sort it, as the page does (never the list beside a task)
  const sorting = useNeedsYouSorting(true);
  return (
    <List
      title="Tasks"
      path="/tasks"
      className="tasks-page tasks-home"
      // always the sections: Needs you's head links Snoozed and Done, which may hold tasks on no pipeline
      state="ready"
      sections={
        <>
          <Section name="Needs you" count={count || undefined} target="tasks:needs-you" actions={<Views sorting={sorting} />}>
            {flow.data ? (
              flow.data.count > 0 ? (
                <NeedsYouList sorting={sorting} />
              ) : (
                <p className="flow-shelf-state">Nothing needs you</p>
              )
            ) : flow.isError ? (
              <p className="flow-shelf-state">
                <ReadFault what="Needs you" reads={[flow]} inline />
              </p>
            ) : (
              <p className="flow-shelf-state" role="status">
                Loading
              </p>
            )}
          </Section>
          {pipes.length > 0 ? (
            <Section name="Pipelines" count={pipes.length} target="tasks:pipelines">
              {pipes.map((p) => (
                <Row key={p.id} className="pipe" title={p.name} href={`/tasks/${encodeURIComponent(p.id)}`} target={`pipeline:${p.id}`}>
                  <span className="row-mark" />
                  <span className="row-title">{p.name}</span>
                  {p.manager || p.owner ? <Face actor={p.manager ?? p.owner} /> : <span className="av-gap" />}
                </Row>
              ))}
            </Section>
          ) : (
            <Empty title="No pipelines yet" />
          )}
        </>
      }
    />
  );
}

/** All · Snoozed · Done, quiet in Needs you's head, after "Tico is sorting…" while it sorts (always there, the same
    height either way, so the rows never shift when it clears). */
function Views({ sorting }: { sorting: boolean }) {
  return (
    <>
      <span className="flow-sorting" role="status">{sorting ? 'Tico is sorting…' : ''}</span>
      <nav className="ny-views" aria-label="Needs you views">
        {NEEDS_YOU_VIEWS.map((v, i) => (
          <span key={v.view}>
            {i > 0 && <span aria-hidden="true"> · </span>}
            <Link to="/needs-you" search={{ view: v.view }}>
              {v.name}
            </Link>
          </span>
        ))}
      </nav>
    </>
  );
}
