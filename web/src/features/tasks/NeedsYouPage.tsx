import { useInfiniteQuery, useMutation, useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { Link, useLocation } from '@tanstack/react-router';
import { useEffect, useRef, useState, type SyntheticEvent } from 'react';
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
import { Blocks, Face } from './parts';
import { flowListParams, flowListQuery, flowOriginQuery, flowQuery, flowSnoozedAsksQuery, flowTask, unsnoozeFlowAsk, useContactNames, useFlowGroups, useNeedsYouSort, type Flow, type FlowItem, type FlowRequest } from './queries';
import './tasks.css';

const TABS = [{ label: 'Mine', to: '/needs-you' }, { label: 'All', to: '/needs-you', search: { view: 'all' } }] as const;

/** How many rows stay live while Tico sorts the list (backend/needs_order.py); the rest wait for the new order. */
const LIVE = 5;

/** `next`, with the rows from the top of `shown` down to the row the person is on (`held`) kept where they are: a new
    order moves only the rows below it. Rows that left the list are gone from the top part too. */
function keepTop(shown: readonly string[], next: readonly string[], held: string | null): string[] {
  const at = held ? shown.indexOf(held) : -1;
  if (!held || at < 0 || !next.includes(held)) return [...next];
  const still = new Set(next);
  const top = shown.slice(0, at + 1).filter((id) => still.has(id));
  const kept = new Set(top);
  return [...top, ...next.filter((id) => !kept.has(id))];
}

/** A row of Mine: a request's card or a task's row. */
type MineRow = { id: string; request: FlowRequest; task?: undefined } | { id: string; task: FlowItem; request?: undefined };

/** Mine as one list in Tico's order, the rows down to the one the person is on kept in place: the last one they
    clicked or focused, until they pick another, click or focus anything outside the rows (a row's own popover is
    inside it, as React passes its events up), or it leaves the list. `on(id)` goes on each row. */
function useTicoOrder(flow: Flow) {
  const [held, hold] = useState<string | null>(null);
  const inside = useRef<Event | null>(null);
  useEffect(() => {
    const away = (e: Event) => { if (e !== inside.current) hold(null); };
    document.addEventListener('pointerdown', away);
    document.addEventListener('focusin', away);
    return () => {
      document.removeEventListener('pointerdown', away);
      document.removeEventListener('focusin', away);
    };
  }, []);
  const on = (id: string) => (e: SyntheticEvent) => {
    inside.current = e.nativeEvent;
    hold(id);
  };
  const [kept, keep] = useState<{ from?: Flow; order: string[]; latest: string[]; held?: string | null }>({ order: [], latest: [] });
  let order = kept.order;
  if (kept.from !== flow) {
    const latest = flow.order ?? [...(flow.requests ?? []), ...flow.items].map((it) => it.id);
    order = held ? keepTop(kept.order, latest, held) : latest;
    keep({ from: flow, order, latest, held });
    if (held && !latest.includes(held)) hold(null);
  } else if (held !== kept.held) {
    // The person moved: the rows down to the new one stay, the rest take the latest order (all of it, let go).
    order = held ? keepTop(order, kept.latest, held) : kept.latest;
    keep({ ...kept, order, held });
  }
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

function FlowRow({ it, mine, contact }: { it: FlowItem; mine: boolean; contact?: string }) {
  const title = it.about || it.title;
  const reason = it.reason;
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
  if (read.isError) return <Button variant="quiet" onClick={() => void read.refetch()}>Try again</Button>;
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
    name. While Tico sorts it, rows past the top LIVE wait, dimmed. */
function Mine({ sorting }: { sorting: boolean }) {
  const flow = useSuspenseQuery(flowQuery).data;
  const { rows, on, held } = useTicoOrder(flow);
  const names = useContactNames([flow.items]);
  const cards = useCards();
  const at = new Map(rows.map((row, i) => [row.id, i] as const));
  const firstRequest = rows.find((row) => row.request)?.id;
  const shownTo = held ? (at.get(held) ?? -1) + 1 : 0; // the rows down to the one the person is on stay drawn
  const draw = (row: MineRow) => (
    <div key={row.id} className={sorting && rows.length > LIVE && (at.get(row.id) ?? 0) >= LIVE ? 'flow-item flow-wait' : 'flow-item'}
      onPointerDownCapture={on(row.id)} onFocusCapture={on(row.id)}>
      {row.request
        ? <FillContext value={row.id === firstRequest}><div className="flow-request"><RequestCard it={row.request} /></div></FillContext>
        : <FlowRow it={row.task} mine contact={row.task.entity_id ? names.get(row.task.entity_id) : undefined} />}
    </div>
  );
  return (
    <>
      {rows.length > 0 && (
        <section className="stage pk-sec flow-mine" aria-label="Needs you">
          <Capped list={rows} cap={Math.max(LIST_CAP, shownTo)} show={Math.max(LIST_SHOW, shownTo)} height={cards ? PHONE_ROW_H : ROW_H} measure more={undefined} draw={draw}
            moreRow={(n, open) => <RowMore key="more" n={n} onClick={open} />} />
        </section>
      )}
      {!flow.count && <Empty title="Nothing waits on you"><Link to="/tasks" className="btn">Open Tasks</Link></Empty>}
    </>
  );
}

/** Needs you: what waits on you (Mine), or every task anyone could claim (All). A row opens the task, and closing it
    comes back here. */
export function NeedsYouPage() {
  useFollow({ allTasks: true });
  const all = useLocation({ select: (l) => (l.search as { view?: string }).view === 'all' });
  const me = useQuery(meQuery).data?.id;
  const read = useFlowList(undefined, 'open', all);
  const [hideArchive, setHideArchive] = useState(false);
  const flow = useQuery({ ...flowQuery, enabled: !all });
  const sorting = useNeedsYouSort(all ? undefined : flow.data);
  const waiting = sorting && (flow.data?.count ?? 0) > LIVE;

  return (
    <List
      title="Needs you"
      path="/needs-you"
      tabs={TABS}
      tabsAfter={<>
        {/* Always here, the same height either way: the rows never shift when it clears. */}
        {!all && <span className="flow-sorting" role="status">{waiting ? 'Tico is sorting…' : ''}</span>}
        <Button variant="quiet" onClick={() => setHideArchive(!hideArchive)}>{hideArchive ? 'Show archive' : 'Hide archive'}</Button>
      </>}
      state={all && read.isPending ? 'loading' : all && read.isLoadingError ? 'fault' : 'ready'}
      onRetry={() => void read.refetch()}
      className="tasks-page"
      sections={
        <>
          {all
            ? read.data?.pages[0]?.tasks.length ? <TaskSections read={read} /> : <Empty title="No work to claim" />
            : <Mine sorting={waiting} />}
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
