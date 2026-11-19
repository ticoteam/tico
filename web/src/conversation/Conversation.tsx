import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { meQuery } from '@/data';
import { useFollow } from '@/live';
import { Icon, type IconName } from '@/kit/Icon';
import { VirtualList, type VirtualListHandle } from '@/kit/VirtualList';
import { SheetHead } from '@/shell';
import { dot, type DotState } from '@/tico/dot';
import { Composer } from './Composer';
import { dayKey, dayLabel } from './dates';
import { DidLine, Face, LiveRow, Message, whoOf } from './Message';
import { installConversationLive, liveQuery, liveText, markRead, rowsOf, sendMessage, timelineQuery } from './queries';
import type { ChatMessage, LiveRun, PageRef, Variant } from './types';

/** Within this many pixels of the end counts as "at the bottom": what comes in is read. */
const READ_PX = 24;
/** A row's guess before it is measured. */
const ROW_PX = 60;

export interface ConversationProps {
  id: string;
  variant: Variant;
  /** Who it is with: the head of a second chat and the box's "Message …". Tico's sheet says "Tico". */
  title?: string;
  /** The face in a second chat's head (bot:<id> or human:<id>). */
  actor?: string;
  /** The page it sits beside: the look chip, and `refs.page` on what is sent. Defaults to the address and title. */
  page?: (PageRef & { icon?: IconName }) | null;
  /** Above the composer, in the chat's foot: a card the chat is waiting on (a task's answer card). */
  foot?: ReactNode;
}

type Item =
  | { k: 'day'; key: string; label: string; isNew: boolean }
  | { k: 'did'; key: string; text: string; fault: boolean }
  | { k: 'msg'; key: string; m: ChatMessage; cont: boolean }
  | { k: 'live'; key: string; r: LiveRun; text: string };

const didsOf = (m: ChatMessage): string[] => {
  const d = m.refs?.did;
  return (Array.isArray(d) ? d : d ? [d] : []).filter((x): x is string => typeof x === 'string' && !!x.trim());
};

/** The list as it reads, oldest first: day breaks, the "New" line, what Tico did, messages, and replies being written. */
export function itemsOf(rows: ChatMessage[], live: LiveRun[], me: string | undefined, newAfter: number | null): Item[] {
  const out: Item[] = [];
  let day = '';
  let newShown = newAfter == null;
  let last: ChatMessage | null = null;
  for (const m of rows) {
    if (m.hidden) continue;
    const d = dayKey(m.created_at);
    if (d && d !== day) {
      day = d;
      out.push({ k: 'day', key: `day:${d}`, label: dayLabel(m.created_at), isNew: false });
      last = null;
    }
    if (!newShown && typeof m.seq === 'number' && m.seq > (newAfter as number) && m.sender_id !== me && !m.pending) {
      newShown = true;
      out.push({ k: 'day', key: 'new', label: 'New', isNew: true });
      last = null;
    }
    const dids = didsOf(m);
    for (const [i, text] of dids.entries()) out.push({ k: 'did', key: `did:${m.id}:${i}`, text, fault: false });
    if (typeof m.refs?.fault === 'string') out.push({ k: 'did', key: `fault:${m.id}`, text: m.refs.fault, fault: true });
    if (dids.length || m.refs?.fault) last = null;
    if (!m.deleted && !(m.body ?? '').trim() && !m.refs?.attachments?.length && !m.refs?.action) continue;
    const cont = !!last && last.sender_id === m.sender_id;
    out.push({ k: 'msg', key: m.id, m, cont });
    last = m;
  }
  for (const r of live) out.push({ k: 'live', key: `live:${r.run}`, r, text: liveText(r) });
  return out;
}

/** What the Dot shows while Tico answers in its own chat. */
function dotFor(rows: ChatMessage[], live: LiveRun[]): DotState | null {
  const r = live.find((x) => !x.ended);
  if (r) return r.doing;
  return rows.some((m) => m.pending === 'sending') ? 'think' : null;
}

const herePage = (): PageRef => ({
  path: typeof location === 'undefined' ? '/' : location.pathname,
  title: typeof document === 'undefined' ? '' : document.title,
});


installConversationLive();

export function Conversation({ id, variant, title, actor, page, foot }: ConversationProps) {
  useFollow(variant === 'tico' ? { conversation: id } : { thread: id });

  const { data: me } = useQuery(meQuery);
  const myId = me?.id;
  const timeline = useInfiniteQuery(timelineQuery(id));
  const { data: live = [] } = useQuery(liveQuery(id));
  const rows = rowsOf(timeline.data);

  // The "New" line sits where the person had read to when the chat opened, and stays there while it is open.
  const [opened, setOpened] = useState<{ id: string; mark: number | null } | null>(null);
  const first = timeline.data?.pages[0];
  if (first && opened?.id !== id) setOpened({ id, mark: first.read_marks?.[id] ?? null });
  const newAfter = opened?.id === id ? opened.mark : null;

  const items = itemsOf(rows, live, myId, newAfter);
  const name = variant === 'tico' ? 'Tico' : (title ?? 'Teammate');
  const at = page === undefined ? herePage() : page;

  /* VirtualList follows the end while the person is at it (a new row, a reply growing) and keeps their place
     otherwise, and when older pages load above. */
  const scroller = useRef<VirtualListHandle>(null);
  // What the person sends takes them to the end, wherever they had scrolled.
  const jump = useRef(false);
  useLayoutEffect(() => {
    if (!jump.current) return;
    jump.current = false;
    scroller.current?.scrollToEnd();
  });

  /* ---- read up to the last message shown, while it is in view */
  const lastSeq = rows.reduce((n, m) => (typeof m.seq === 'number' && m.seq > n ? m.seq : n), 0);
  useEffect(() => {
    if (!lastSeq || (scroller.current?.fromEnd() ?? 0) > READ_PX || document.visibilityState !== 'visible') return;
    markRead(id, lastSeq);
  }, [id, lastSeq]);

  /* ---- the Dot, for Tico's own chat only */
  const doing = variant === 'tico' ? dotFor(rows, live) : null;
  useEffect(() => {
    if (!doing) return;
    return dot.set(doing);
  }, [doing]);

  const send = (text: string, files: File[], retry?: string) => {
    if (!myId) return;
    jump.current = true;
    void sendMessage({ id, variant, text, files, page: at && { path: at.path, title: at.title }, me: myId, ...(retry && { retry }) });
  };

  const list = (
    <VirtualList
      ref={scroller}
      items={items}
      getKey={(it) => it.key}
      estimateSize={ROW_PX}
      measure
      anchor="end"
      role="log"
      label={`Chat with ${name}`}
      className="chat-body"
      onReachStart={() => {
        if (timeline.hasNextPage && !timeline.isFetchingNextPage) void timeline.fetchNextPage();
      }}
      renderItem={(it) => {
        if (it.k === 'day')
          return (
            <div className={it.isNew ? 'day is-new' : 'day'}>
              <span>{it.label}</span>
            </div>
          );
        if (it.k === 'did') return <DidLine text={it.text} fault={it.fault} />;
        if (it.k === 'live') return <LiveRow r={it.r} variant={variant} text={it.text} />;
        return <Message m={it.m} variant={variant} me={myId} cont={it.cont} onRetry={(m) => send(m.body ?? '', m.files ?? [], m.id)} />;
      }}
    />
  );

  const look: ReactNode =
    (variant === 'tico' || variant === 'side') && at?.title ? (
      <span className="look">
        <span className="look-ico">
          <Icon name={page?.icon ?? 'file'} />
        </span>
        <span className="look-text">{at.title}</span>
      </span>
    ) : null;

  const footer = (
    <footer className="chat-foot">
      {foot}
      {look}
      {myId && <Composer id={id} me={myId} to={name} onSend={(text, files) => send(text, files)} />}
    </footer>
  );

  if (variant === 'page')
    return (
      <div className="tv-col chat-page">
        {list}
        {footer}
      </div>
    );
  if (variant === 'side') {
    const who = whoOf({ sender_id: actor ?? '' }, variant, myId);
    return (
      <>
        <SheetHead kind="side" title={name} lead={<Face who={{ ...who, name }} size={28} />} />
        {list}
        {footer}
      </>
    );
  }
  return (
    <>
      {list}
      {footer}
    </>
  );
}
