import { useInfiniteQuery, useMutation, useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { Link, useLocation } from '@tanstack/react-router';
import { useEffect, useRef, useSyncExternalStore, type SyntheticEvent } from 'react';
import { hearPlaces, heldRow, holdRow } from '@/lib';
import { QuestionCard, RequestCard } from '@/features/task/exports';
import { Button, FillContext } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { FlowMark } from '@/kit/FlowMark';
import { Popover, useCards } from '@/kit/Popover';
import { TaskRow } from '@/kit/Row';
import { useFollow } from '@/live/follow';
import { List, ReadFault, Section } from '@/pagekinds';
import { AllRows, PHONE_ROW_H, ReadOn, ROW_H } from './capped';
import { useNeedsYouDrawn } from './needs';
import { Blocks, Face } from './parts';
import { flowListParams, flowListQuery, flowOriginQuery, flowQuery, flowSnoozedAsksQuery, flowTask, unsnoozeFlowAsk, useContactNames, useNeedsYouSort, type Flow, type FlowItem, type FlowRequest } from './queries';
import './tasks.css';

/** How many rows stay live while Tico sorts the list (backend/needs_order.py); the rest wait for the new order. */
const LIVE = 5;

/** A row of Mine: a request's card or a task's row. */
type MineRow = { id: string; request: FlowRequest; task?: undefined } | { id: string; task: FlowItem; request?: undefined };

/** Mine as one list in Tico's order, the rows down to the one the person is on kept in place: the last one they
    clicked or focused, until they pick another, click or focus anything outside the rows (a row's own popover is
    inside it, as React passes its events up), or it leaves the list; the task open from it, beside it (the shell).
    The order is useNeedsYouDrawn's, the same the list beside a task draws and an answer goes on down. `on(id)` goes on
    each row. */
function useTicoOrder(flow: Flow) {
  const held = useSyncExternalStore(hearPlaces, heldRow);
  const inside = useRef<Event | null>(null);
  useEffect(() => {
    const away = (e: Event) => { if (e !== inside.current) holdRow(null); };
    document.addEventListener('pointerdown', away);
    document.addEventListener('focusin', away);
    return () => {
      document.removeEventListener('pointerdown', away);
      document.removeEventListener('focusin', away);
    };
  }, []);
  const on = (id: string) => (e: SyntheticEvent) => {
    inside.current = e.nativeEvent;
    holdRow(id);
  };
  const order = useNeedsYouDrawn(flow);
  const byId = new Map<string, MineRow>([
    ...(flow.requests ?? []).map((it) => [it.id, { id: it.id, request: it }] as const),
    ...flow.items.map((it) => [it.id, { id: it.id, task: it }] as const),
  ]);
  return { rows: order.flatMap((id) => byId.get(id) ?? []), on, held };
}

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

/** Whether a task's row is answered in place, by a card under it: an open ❓ on it. An approval is not: what it
    approves (a page, a file, an email, a payment) is shown on the task, where Approve opens the next one. */
const answersHere = (it: FlowItem): boolean => !!it.questions?.length;

/** The card under a task's row (answersHere): its first ❓, whole, with the answers the agent offers and a reply box.
    Answered, it leaves the row, and the next ❓ there shows; the last takes the row out of Needs you. */
function RowCard({ it }: { it: FlowItem }) {
  const q = it.questions?.[0];
  return q ? <QuestionCard key={q.id} id={q.id} task={it.id} line={q.line} detail={q.detail} choices={q.choices} who={q.by} /> : null;
}

function FlowRow({ it, mine, contact }: { it: FlowItem; mine: boolean; contact?: string }) {
  const title = it.about || it.title;
  // a ❓'s card under the row asks it: the row does not say it again
  const reason = it.questions?.some((q) => q.line === it.reason) ? undefined : it.reason;
  return (
    <div className="flow-row">
      <TaskRow
        title={title}
        href={`/task/${it.number ?? encodeURIComponent(it.id)}`}
        target={`task:${it.number ?? it.id}`}
        mark={mine && <FlowMark />}
        late={it.late}
        pills={<>{reason && reason !== title && <span className="flow-reason" title={reason}>{reason}</span>}{contact && <span className="pill flow-contact" title={contact}>{contact}</span>}<Blocks t={it} /></>}
        avatar={<Face actor={it.owner_id} />}
        at={it.since}
      />
      <Popover trigger={<Button variant="quiet" aria-label={`Why here for ${title}`}>Why here</Button>} className="flow-why" label="Why here" align="end">
        <OriginDetails it={it} />
      </Popover>
    </div>
  );
}

/** The tasks of a view (All, Snoozed, Done) as one list, each row with its relationship's name as a chip. */
function TaskRows({ items, pages }: { items: readonly FlowItem[]; pages: readonly (readonly Pick<FlowItem, 'entity_id'>[])[] }) {
  const cards = useCards();
  const names = useContactNames(pages);
  return (
    <Section name="Tasks" count={items.length}>
      <AllRows
        list={items}
        height={cards ? PHONE_ROW_H : ROW_H}
        measure={cards}
        draw={(it) => <FlowRow key={it.id} it={it} mine={false} contact={it.entity_id ? names.get(it.entity_id) : undefined} />}
      />
    </Section>
  );
}

function TaskSections({ read, of }: { read: ReturnType<typeof useFlowList>; of: string }) {
  const pages = read.data?.pages.map((page) => page.tasks);
  const items = new Map(pages?.flatMap((tasks) => tasks.map((task) => [task.id, flowTask(task)] as const)));
  return (
    <>
      <TaskRows items={[...items.values()]} pages={pages ?? []} />
      <ReadOn of={of} more={read.hasNextPage ? () => read.fetchNextPage().then((r) => !r.isFetchNextPageError) : undefined} />
    </>
  );
}

function useFlowList(owner: string | undefined, shelf: 'open' | 'snoozed' | 'done', enabled = true) {
  return useInfiniteQuery({ ...flowListQuery(flowListParams(owner, shelf)), enabled });
}

function FlowShelf({ owner, shelf }: { owner?: string; shelf: 'snoozed' | 'done' }) {
  const read = useFlowList(owner, shelf);
  if (read.isPending) return <p className="flow-shelf-state" role="status">Loading</p>;
  if (read.isLoadingError)
    return (
      <p className="flow-shelf-state">
        <ReadFault what={shelf === 'done' ? 'Done tasks' : 'Snoozed tasks'} reads={[read]} inline />
      </p>
    );
  if (!read.data?.pages[0]?.tasks.length) return <p className="flow-shelf-state">{shelf === 'done' ? 'No done tasks' : 'No snoozed tasks'}</p>;
  return <TaskSections read={read} of={JSON.stringify(flowListParams(owner, shelf))} />;
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
  if (read.isError)
    return (
      <p className="flow-shelf-state">
        <ReadFault what="Asks" reads={[read]} inline />
      </p>
    );
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

/** What waits on you, as one list in Tico's order: requests' cards and tasks' rows, each task with its relationship's
    name, and under a task's row the card for its ❓ when it has one (answersHere). The first card has the filled
    button. While Tico sorts it, rows past the top LIVE wait, dimmed. */
function Mine({ sorting, bare = false }: { sorting: boolean; bare?: boolean }) {
  const flow = useSuspenseQuery(flowQuery).data;
  const { rows, on } = useTicoOrder(flow);
  const names = useContactNames([flow.items]);
  const cards = useCards();
  const at = new Map(rows.map((row, i) => [row.id, i] as const));
  const firstCard = rows.find((row) => row.request || answersHere(row.task))?.id;
  const draw = (row: MineRow) => (
    <div key={row.id} className={sorting && rows.length > LIVE && (at.get(row.id) ?? 0) >= LIVE ? 'flow-item flow-wait' : 'flow-item'}
      onPointerDownCapture={on(row.id)} onFocusCapture={on(row.id)}>
      <FillContext value={row.id === firstCard}>
        {row.request
          ? <div className="flow-request"><RequestCard it={row.request} /></div>
          : <>
            <FlowRow it={row.task} mine contact={row.task.entity_id ? names.get(row.task.entity_id) : undefined} />
            {answersHere(row.task) && <div className="flow-card"><RowCard it={row.task} /></div>}
          </>}
      </FillContext>
    </div>
  );
  // `bare`: inside a section of its own (Tasks' Needs you), with its head
  const list = (
    <AllRows list={rows} height={cards ? PHONE_ROW_H : ROW_H} measure draw={draw} />
  );
  return (
    <>
      {rows.length > 0 && (bare ? list : (
        // a task opened from here opens with Needs you as its list (router.tsx)
        <section className="stage pk-sec flow-mine" aria-label="Needs you" data-list="">
          {list}
        </section>
      ))}
      {!flow.count && !bare && <Empty title="Nothing waits on you"><Link to="/tasks" className="btn">Open Tasks</Link></Empty>}
    </>
  );
}

/** Mine's ask for Tico to sort it, sent where Mine shows (`on`): this page's Mine, and Needs you on top of Tasks, never
    the list beside a task. Whether its rows past the top LIVE wait for the new order. */
function useMineSort(on: boolean): boolean {
  const flow = useQuery({ ...flowQuery, enabled: on });
  const sorting = useNeedsYouSort(on ? flow.data : undefined);
  return sorting && (flow.data?.count ?? 0) > LIVE;
}

/** Needs you as a page of its own: what waits on you (Mine), or, from the All link in its head on Tasks (`?view=all`),
    every task anyone could claim. The link picks the view, so no tabs; Snoozed and Done are the head's other links. A
    row opens the task, and closing it comes back here. */
export function NeedsYouPage() {
  useFollow({ allTasks: true });
  const all = useLocation({ select: (l) => (l.search as { view?: string }).view === 'all' });
  const read = useFlowList(undefined, 'open', all);
  const waiting = useMineSort(!all);

  return (
    <List
      title={all ? 'All' : 'Needs you'}
      path="/needs-you"
      // Always here, the same height either way: the rows never shift when it clears.
      after={all ? undefined : <span className="flow-sorting" role="status">{waiting ? 'Tico is sorting…' : ''}</span>}
      state={all && read.isPending ? 'loading' : all && read.isLoadingError ? 'fault' : 'ready'}
      reads={[read]}
      className="tasks-page"
      sections={all
        ? read.data?.pages[0]?.tasks.length ? <TaskSections read={read} of={JSON.stringify(flowListParams(undefined, 'open'))} /> : <Empty title="No work to claim" />
        : <Mine sorting={waiting} />}
    />
  );
}

/** What waits on you as a list (Mine): Tasks draws it on top, and beside a task opened from it (NeedsYouList). */
export { Mine as NeedsYouRows, FlowShelf as NeedsYouShelf, SnoozedAsks as NeedsYouSnoozedAsks, useMineSort as useNeedsYouSorting };
