import { useInfiniteQuery, useMutation, useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { Link, useLocation } from '@tanstack/react-router';
import { useState } from 'react';
import { meQuery } from '@/data';
import { RequestCard } from '@/features/task/exports';
import { Button, FillContext } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { FlowMark } from '@/kit/FlowMark';
import { Popover, useCards } from '@/kit/Popover';
import { TaskRow } from '@/kit/Row';
import { useFollow } from '@/live/follow';
import { List, Section } from '@/pagekinds';
import { Capped, LIST_CAP, LIST_SHOW, PHONE_ROW_H, RowMore, ROW_H } from './capped';
import { Blocks, Face, FlowButton } from './parts';
import { flowCountQuery, flowListParams, flowListQuery, flowOriginQuery, flowQuery, flowSnoozedAsksQuery, flowTask, unsnoozeFlowAsk, useFlowGroups, type FlowItem, type FlowRequest } from './queries';
import './tasks.css';

const TABS = [{ label: 'Mine', to: '/flow' }, { label: 'All', to: '/flow', search: { view: 'all' } }] as const;

function OriginDetails({ it }: { it: FlowItem }) {
  const origin = useQuery(flowOriginQuery(it.id));
  const cause = origin.data?.cause;
  const madeBy = origin.data?.made_by?.name;
  const detail = cause?.type === 'automation' ? cause.name
    : cause?.type === 'rule' ? cause.text
    : cause?.type === 'parent_task' ? cause.title
    : null;
  return (
    <>
      {it.reason && <p>{it.reason}</p>}
      {typeof detail === 'string' && detail && <p>{detail}</p>}
      {typeof madeBy === 'string' && madeBy && <p>Filed by {madeBy}</p>}
      {origin.isPending && <p role="status">Loading</p>}
      {origin.isError && <p>Origin unavailable</p>}
    </>
  );
}

function FlowRow({ it, mine }: { it: FlowItem; mine: boolean }) {
  const title = it.about || it.title;
  const reason = it.reason;
  return (
    <div className="flow-row">
      <TaskRow
        title={title}
        href={`/${mine ? 'flow' : 'task'}/${it.number ?? encodeURIComponent(it.id)}`}
        target={`task:${it.number ?? it.id}`}
        mark={mine && <FlowMark />}
        late={it.late}
        pills={<>{reason && reason !== title && <span className="flow-reason" title={reason}>{reason}</span>}<Blocks t={it} /></>}
        avatar={<Face actor={it.owner_id} />}
        at={it.since}
      />
      <Popover trigger={<Button variant="quiet" aria-label={`Why here for ${title}`}>Why here</Button>} className="flow-why" label="Why here" align="end">
        <OriginDetails it={it} />
      </Popover>
    </div>
  );
}

function RelationshipSections({ items, pages, mine = false }: {
  items: readonly FlowItem[];
  pages?: Parameters<typeof useFlowGroups>[1];
  mine?: boolean;
}) {
  const cards = useCards();
  return useFlowGroups(items, pages).map((g) => (
    <Section key={g.key} name={g.name} count={g.items.length}>
      <Capped
        list={g.items}
        cap={LIST_CAP}
        show={LIST_SHOW}
        height={cards ? PHONE_ROW_H : ROW_H}
        measure={cards}
        more={undefined}
        draw={(it) => <FlowRow key={it.id} it={it} mine={mine} />}
        moreRow={(n, open) => <RowMore key="more" n={n} onClick={open} />}
      />
    </Section>
  ));
}

function TaskSections({ read }: { read: ReturnType<typeof useFlowList> }) {
  const pages = read.data?.pages.map((page) => page.tasks);
  const items = new Map(pages?.flatMap((tasks) => tasks.map((task) => [task.id, flowTask(task)] as const)));
  return (
    <>
      <RelationshipSections items={[...items.values()]} pages={pages} />
      {read.hasNextPage && <Button variant="quiet" disabled={read.isFetching} onClick={() => void read.fetchNextPage()}>
        {read.isFetchingNextPage ? 'Loading' : read.isFetchNextPageError ? 'Try again' : 'Show more'}
      </Button>}
    </>
  );
}

function useFlowList(owner: string | undefined, shelf: 'open' | 'snoozed' | 'done', enabled = true) {
  return useInfiniteQuery({ ...flowListQuery(flowListParams(owner, shelf)), enabled });
}

function FlowShelf({ owner, shelf }: { owner?: string; shelf: 'snoozed' | 'done' }) {
  const read = useFlowList(owner, shelf);
  if (read.isPending) return <p className="flow-shelf-state" role="status">Loading</p>;
  if (read.isLoadingError) return <Button variant="quiet" onClick={() => void read.refetch()}>Try again</Button>;
  if (!read.data?.pages[0]?.tasks.length) return <p className="flow-shelf-state">{shelf === 'done' ? 'No done tasks' : 'No snoozed tasks'}</p>;
  return <TaskSections read={read} />;
}

function SnoozedAsk({ id, title, href, until }: { id: string; title: string; href?: string; until: string }) {
  const wake = useMutation({ mutationFn: () => unsnoozeFlowAsk(id) });
  return (
    <div className="flow-row">
      <TaskRow title={title} href={href} at={until} />
      <Button variant="quiet" disabled={wake.isPending} onClick={() => wake.mutate()}>Unsnooze</Button>
      {wake.isError && <p role="alert">Couldn’t unsnooze the ask.</p>}
    </div>
  );
}

function SnoozedAsks() {
  const read = useQuery(flowSnoozedAsksQuery);
  if (read.isPending) return <p className="flow-shelf-state" role="status">Loading asks</p>;
  if (read.isError) return <Button variant="quiet" onClick={() => void read.refetch()}>Retry loading asks</Button>;
  if (!read.data.items.length) return null;
  return (
    <Section name="Asks" count={read.data.items.length}>
      {read.data.items.map((it) => (
        <SnoozedAsk key={it.id} id={it.id} title={it.about || it.line || it.task?.title || 'Ask'}
          href={it.href ?? (it.task ? `/task/${it.task.number ?? encodeURIComponent(it.task.id)}` : undefined)}
          until={it.snoozed_until} />
      ))}
    </Section>
  );
}

/** Requests with no task keep their own cards in Mine. */
function Requests({ items }: { items: readonly FlowRequest[] }) {
  return (
    <Section name="Requests" count={items.length} target="stage:requests">
      <div className="flow-requests">
        {items.map((it, i) => (
          <FillContext key={it.id} value={i === 0}>
            <RequestCard it={it} />
          </FillContext>
        ))}
      </div>
    </Section>
  );
}

function Mine() {
  const flow = useSuspenseQuery(flowQuery).data;
  const requests = flow.requests ?? [];
  return (
    <>
      {requests.length > 0 && <Requests items={requests} />}
      <RelationshipSections items={flow.items} mine />
      {!flow.count && <Empty title="Nothing waits on you"><Link to="/tasks" className="btn">Open Tasks</Link></Empty>}
    </>
  );
}

export function FlowPage() {
  useFollow({ allTasks: true });
  const all = useLocation({ select: (l) => (l.search as { view?: string }).view === 'all' });
  const { data: count = 0 } = useQuery(flowCountQuery);
  const me = useQuery(meQuery).data?.id;
  const read = useFlowList(undefined, 'open', all);
  const [hideArchive, setHideArchive] = useState(false);

  return (
    <List
      title="Tasks"
      path="/flow"
      actions={<FlowButton on count={count} />}
      tabs={TABS}
      tabsAfter={<Button variant="quiet" onClick={() => setHideArchive(!hideArchive)}>{hideArchive ? 'Show archive' : 'Hide archive'}</Button>}
      state={all && read.isPending ? 'loading' : all && read.isLoadingError ? 'fault' : 'ready'}
      onRetry={() => void read.refetch()}
      className="tasks-page"
      sections={
        <>
          {all
            ? read.data?.pages[0]?.tasks.length ? <TaskSections read={read} /> : <Empty title="No work to claim" />
            : <Mine />}
          {!hideArchive && (all || me) && (
            <div key={all ? 'all' : 'mine'} className="flow-archive">
              <Section name="Snoozed" defaultOpen={false}>
                {!all && <SnoozedAsks />}
                <FlowShelf owner={all ? undefined : me} shelf="snoozed" />
              </Section>
              <Section name="Done" defaultOpen={false}><FlowShelf owner={all ? undefined : me} shelf="done" /></Section>
            </div>
          )}
        </>
      }
    />
  );
}
