import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { Conversation, Message } from '@/conversation';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Count } from '@/kit/Count';
import { Empty } from '@/kit/Empty';
import { Search } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem } from '@/kit/Menu';
import { Row, ThreadRow } from '@/kit/Row';
import { VirtualList } from '@/kit/VirtualList';
import { ItemHead, ListAndItem, type PageTab } from '@/pagekinds';
import { Browse } from './Browse';
import { isDm, othersOf, rowsOf, unreadOf, type Thread } from './model';
import { NewMessage, NewMessageFoot, useNewDraft } from './NewMessage';
import { Face, press, useMe, useTitle } from './people';
import { catchUpQuery, threadListQuery, threadQuery, threadSearchQuery, type CatchUpItem, type SearchHit } from './queries';
import { ThreadHead } from './ThreadHead';
import './threads.css';

/** The list's tabs: Threads, Mail and Messaging are one place. `here` keeps Threads on while a thread is open. */
export function threadTabs(here: string): PageTab[] {
  return [
    { label: 'Threads', to: here, target: 'tab:threads' },
    { label: 'Mail', to: '/threads/mail', target: 'tab:mail' },
    { label: 'Messaging', to: '/threads/messaging', target: 'tab:messaging' },
  ];
}

export const NewButton = () => (
  <Button variant="primary" icon="edit" onClick={() => void press('threads.compose', {})}>
    New message
  </Button>
);

export interface ThreadsPageProps {
  /** What the reading column holds: Catch up, New message, or the thread `thread`. */
  pane: 'catch-up' | 'new' | 'thread';
  thread?: string;
  /** The search words in the address (`?q=`). */
  q?: string;
  /** Something is open in the reading column (a phone shows it instead of the list). */
  open?: boolean;
}

const SEARCH_WAIT_MS = 200;

/** Threads: the list of threads and DMs, newest first, with Catch up on top; the open one beside it. */
export function ThreadsPage({ pane, thread, q = '', open = false }: ThreadsPageProps) {
  const list = useInfiniteQuery(threadListQuery);
  const draft = useNewDraft();
  const [words, setWords] = useState(q);
  const [browsing, setBrowsing] = useState(false);
  const navigate = useNavigate();
  useEffect(() => {
    const next = words.trim();
    if (next === q) return;
    const t = setTimeout(
      () => void navigate({ to: '.', search: (prev: Record<string, unknown>) => ({ ...prev, q: next || undefined }) as never, replace: true }),
      SEARCH_WAIT_MS,
    );
    return () => clearTimeout(t);
  }, [words, q, navigate]);

  const all = rowsOf(list.data);
  const here = pane === 'thread' && thread ? `/threads/${encodeURIComponent(thread)}` : pane === 'new' ? '/threads/new' : '/threads';
  const keep = q ? `?q=${encodeURIComponent(q)}` : '';
  const state = list.isPending ? 'loading' : list.isError ? 'fault' : all.length === 0 && !open && !q ? 'empty' : 'ready';
  const more = () => {
    if (list.hasNextPage && !list.isFetchingNextPage) void list.fetchNextPage();
  };

  const listNode = q ? (
    <Found q={q} open={thread} keep={keep} />
  ) : (
    <>
      <CatchUpRow selected={pane === 'catch-up'} />
      <VirtualList
        className="tl-vl"
        label="Threads"
        items={all}
        getKey={(t) => t.id}
        estimateSize={35}
        onReachEnd={more}
        renderItem={(t) => <ListRow t={t} selected={t.id === thread} />}
      />
    </>
  );

  const item =
    pane === 'thread' && thread ? (
      { itemHead: <ThreadHead id={thread} />, conversation: <ThreadConversation id={thread} />, itemLabel: 'Thread' }
    ) : pane === 'new' ? (
      { itemHead: <NewHead />, item: <NewMessage draft={draft} />, foot: <NewMessageFoot draft={draft} />, itemLabel: 'New message' }
    ) : (
      { itemHead: <CatchUpHead />, item: <CatchUpBody />, itemLabel: 'Catch up' }
    );

  return (
    <>
    <ListAndItem
      title="Threads"
      path="/threads"
      actions={<NewButton />}
      more={
        <Menu trigger={<IconButton icon="more" label="More" />}>
          <MenuItem label="Browse threads" icon="search" onSelect={() => setBrowsing(true)} />
          <MenuItem label="Learned" icon="spark" onSelect={() => void press('learned.open', {})} />
        </Menu>
      }
      search={<Search label="Find a thread" placeholder="Find a thread" value={words} onValueChange={setWords} wide />}
      tabs={threadTabs(here)}
      list={listNode}
      listLabel="Threads"
      open={open}
      state={state}
      empty={{ title: 'No threads yet' }}
      onRetry={() => void list.refetch()}
      {...item}
    />
    {browsing && <Browse onClose={() => setBrowsing(false)} />}
    </>
  );
}

/** The server's search: one row per thread, newest hit first; each row keeps the words in its address. */
function Found({ q, open, keep }: { q: string; open?: string; keep: string }) {
  const found = useInfiniteQuery(threadSearchQuery(q));
  const seen = new Set<string>();
  const rows: SearchHit[] = [];
  for (const h of found.data?.pages.flatMap((p) => p.hits) ?? [])
    if (!seen.has(h.thread_id)) {
      seen.add(h.thread_id);
      rows.push(h);
    }
  if (found.isPending) return null;
  if (found.isError)
    return (
      <Empty icon="alert" title="Search did not load">
        <Button onClick={() => void found.refetch()}>Try again</Button>
      </Empty>
    );
  if (!rows.length) return <Empty icon="search" title="No threads match" />;
  return (
    <VirtualList
      className="tl-vl"
      label="Threads found"
      items={rows}
      getKey={(h) => h.thread_id}
      estimateSize={35}
      onReachEnd={() => {
        if (found.hasNextPage && !found.isFetchingNextPage) void found.fetchNextPage();
      }}
      renderItem={(h) => (
        <ThreadRow
          title={h.title || 'Thread'}
          href={`/threads/${encodeURIComponent(h.thread_id)}${keep}`}
          selected={h.thread_id === open}
          at={h.created_at ?? new Date(0)}
          kind="threads"
          target={`thread:${h.thread_id}`}
        />
      )}
    />
  );
}

function ListRow({ t, selected }: { t: Thread; selected: boolean }) {
  const me = useMe();
  const title = useTitle(t);
  const other = isDm(t) ? othersOf(t, me)[0] : undefined;
  const home = t.audience === 'home' && typeof t.home?.actor === 'string' ? t.home.actor : undefined;
  const face = other ?? home;
  const n = unreadOf(t);
  return (
    <ThreadRow
      title={title}
      href={`/threads/${encodeURIComponent(t.id)}`}
      selected={selected}
      unread={n > 0 || !!t.me?.activity}
      at={t.last_message_at ?? t.created_at ?? new Date(0)}
      count={n}
      kind={t.task_id ? 'tasks' : 'threads'}
      avatar={face ? <Face actor={face} /> : undefined}
      target={`thread:${t.id}`}
    />
  );
}

/** The first row: what is new across every thread. */
function CatchUpRow({ selected }: { selected: boolean }) {
  const { data } = useQuery(catchUpQuery);
  const n = data?.count ?? 0;
  return (
    <Row className="tl-row tl-catch" title="Catch up" href="/threads/catch-up" selected={selected} unread={n > 0} target="page:/threads/catch-up">
      <span className="tl-kind">
        <Icon name="inbox" />
      </span>
      <span className="row-title">Catch up</span>
      <span className="row-age" />
      <span className="tl-slot">{n > 0 && <Count n={n} label={`${n} new`} />}</span>
    </Row>
  );
}

/* ---------------------------------------------------------------- one thread */

function ThreadConversation({ id }: { id: string }) {
  const { data } = useQuery(threadQuery(id));
  const title = useTitle(data);
  return <Conversation id={id} variant="page" title={title || undefined} />;
}

/* ---------------------------------------------------------------- Catch up */

const BACK = { to: '/threads', label: 'Threads' };

function CatchUpHead() {
  const { data } = useQuery(catchUpQuery);
  const ids = data?.items.map((i) => i.thread.id) ?? [];
  return (
    <ItemHead
      title="Catch up"
      target="page:/threads/catch-up"
      back={BACK}
      actions={
        <>
          {!!data?.count && <Count n={data.count} className="conv-n" label={`${data.count} new`} />}
          {ids.length > 0 && <Button onClick={() => void press('threads.markRead', { threads: ids })}>Mark read</Button>}
        </>
      }
    />
  );
}

function CatchUpBody() {
  const { data, isPending, isError, refetch } = useQuery(catchUpQuery);
  if (isPending) return null;
  if (isError)
    return (
      <Empty icon="alert" title="Catch up did not load">
        <Button onClick={() => void refetch()}>
          Try again
        </Button>
      </Empty>
    );
  if (data.items.length === 0) return <Empty title="Nothing new" />;
  return (
    <div className="cu-list">
      {data.items.map((it) => (
        <CatchUpGroup key={it.thread.id} it={it} />
      ))}
    </div>
  );
}

function CatchUpGroup({ it }: { it: CatchUpItem }) {
  const me = useMe();
  const title = useTitle(it.thread);
  const last = it.messages.at(-1)?.created_at ?? it.thread.last_message_at ?? new Date(0);
  return (
    <section className="cu" aria-label={title}>
      <Link className="row tl-row cu-head" to="/threads/$thread" params={{ thread: it.thread.id }} data-target={`thread:${it.thread.id}`}>
        <span className="tl-kind">
          <Icon name={it.thread.task_id ? 'tasks' : 'threads'} />
        </span>
        <span className="row-title">{title}</span>
        <Ago at={last} className="row-age" />
        <span className="tl-slot">{it.new_count > 0 && <Count n={it.new_count} label={`${it.new_count} new`} />}</span>
      </Link>
      <div className="cu-msgs">
        {it.messages.map((m, i) => (
          <Message key={m.id} m={m} variant="page" me={me} cont={i > 0 && it.messages[i - 1]?.sender_id === m.sender_id} />
        ))}
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------- New message */

function NewHead() {
  return (
    <ItemHead
      title="New message"
      target="page:/threads/new"
      back={BACK}
      actions={
        <Link className="ib" to="/threads" aria-label="Close" title="Close">
          <Icon name="close" />
        </Link>
      }
    />
  );
}
