import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useEffect, useEffectEvent, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { toast } from '@/kit/Toast';
import { meQuery, useActor, type Actor } from '@/data';
import { Button } from '@/kit/Button';
import { DidLine } from '@/kit/DidLine';
import { Empty } from '@/kit/Empty';
import { Link } from '@/kit/Link';
import { useFollow } from '@/live';
import { Icon, type IconName } from '@/kit/Icon';
import { VirtualList, type VirtualListHandle } from '@/kit/VirtualList';
import { SheetHead } from '@/shell';
import { dot, type DotState } from '@/tico/dot';
import { MessageBones } from './Bones';
import { Composer } from './Composer';
import { dayKey, dayLabel } from './dates';
import { canRate } from './Feedback';
import { Face, LiveRow, Message, whoOf } from './Message';
import { onJump } from './Outline';
import { quoteFrom, quoteOf, Replying } from './Reactions';
import { feedbackQuery, installConversationLive, liveQuery, liveText, markRead, rowsOf, sendMessage, timelineQuery } from './queries';
import type { ChatMessage, LiveRun, PageRef, Variant } from './types';

/** Within this many pixels of the end counts as "at the bottom": what comes in is read. */
const READ_PX = 24;
/** A row's guess before it is measured. */
const ROW_PX = 60;
/** Pages a jump from the Outline loads before it gives up (the old app's 40). */
const JUMP_PAGES = 40;
const frame = () => new Promise((r) => requestAnimationFrame(() => r(null)));

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
  /** What a message draws in place of its text, when it is more than words (a thread's call card). */
  card?: (m: ChatMessage) => ReactNode;
  /** false: the person may read but not write here (an archived, closed or meeting thread): no box. */
  canPost?: boolean;
  /** Not the thread's own messages, drawn among them by time (a team's updates, through `card`): no reply, no rating. */
  extra?: ChatMessage[];
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
function itemsOf(rows: ChatMessage[], live: LiveRun[], me: string | undefined, id: string, marks: Map<string, number> | null): Item[] {
  const out: Item[] = [];
  let day = '';
  let newShown = false;
  let last: ChatMessage | null = null;
  for (const m of rows) {
    if (m.hidden) continue;
    const d = dayKey(m.created_at);
    if (d && d !== day) {
      day = d;
      out.push({ k: 'day', key: `day:${d}`, label: dayLabel(m.created_at), isNew: false });
      last = null;
    }
    const mark = marks?.get(m.thread_id || id) ?? marks?.get(id);
    if (!newShown && mark != null && typeof m.seq === 'number' && m.seq > mark && m.sender_id !== me && !m.pending) {
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

/** A teammate's page, `/team/<agent slug or id>` as features/team's whoHref writes it; none for Tico or no one. */
function teammateHref(actor: string | undefined, a: Actor | undefined): string | null {
  if (!actor || !/^(bot|human):./.test(actor)) return null;
  return `/team/${encodeURIComponent(a?.kind === 'agent' && a.slug ? a.slug : actor.slice(actor.indexOf(':') + 1))}`;
}

const herePage = (): PageRef => ({
  path: typeof location === 'undefined' ? '/' : location.pathname,
  title: typeof document === 'undefined' ? '' : document.title,
});


installConversationLive();

/** The thread's rows with `extra` among them by time; while older pages are left, none from before the oldest loaded. */
function withExtra(rows: ChatMessage[], extra: ChatMessage[] | undefined, more: boolean): ChatMessage[] {
  if (!extra?.length) return rows;
  const at = (m: ChatMessage) => m.created_at ?? '';
  const from = more ? (rows[0]?.created_at ?? '') : '';
  const left = extra.filter((m) => at(m) >= from).sort((a, b) => (at(a) < at(b) ? -1 : 1));
  const out: ChatMessage[] = [];
  for (const m of rows) {
    while (left.length && at(left[0]!) < at(m)) out.push(left.shift()!);
    out.push(m);
  }
  return [...out, ...left];
}

export function Conversation({ id, variant, title, actor, page, foot, card, canPost = true, extra }: ConversationProps) {
  useFollow(variant === 'tico' ? { conversation: id } : { thread: id });

  const { data: me } = useQuery(meQuery);
  const myId = me?.id;
  const timeline = useInfiniteQuery(timelineQuery(id));
  const { data: live = [] } = useQuery(liveQuery(id));
  const own = rowsOf(timeline.data);
  const rows = withExtra(own, extra, !!timeline.hasNextPage);
  const extraIds = new Set(extra?.map((m) => m.id));

  // Flat pages can hold replies from several threads, each with its own read mark.
  const [opened, setOpened] = useState<{ id: string; pages: typeof timeline.data; marks: Map<string, number> } | null>(null);
  if (timeline.data && (opened?.id !== id || opened.pages !== timeline.data)) {
    const marks = new Map(opened?.id === id ? opened.marks : undefined);
    for (const page of timeline.data.pages)
      for (const [thread, seq] of Object.entries(page.read_marks ?? {}))
        if (!marks.has(thread)) marks.set(thread, seq);
    setOpened({ id, pages: timeline.data, marks });
  }
  const marks = opened?.id === id ? opened.marks : null;

  const items = itemsOf(rows, live, myId, id, marks);
  const name = variant === 'tico' ? 'Tico' : (title ?? 'Teammate');
  const mate = useActor(actor);
  const mateHref = teammateHref(actor, mate);
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

  const byId = new Map(rows.map((m) => [m.id, m]));

  /* ---- a reply being written: the message it answers sits above the box until it is sent or dropped. Tico's own
     chat takes no reply (POST /api/v2/assistant/messages has no in_reply_to). */
  const [replying, setReplying] = useState<{ id: string; m: ChatMessage } | null>(null);
  const replyTo = replying?.id === id ? replying.m : null;
  const footRef = useRef<HTMLElement>(null);
  const canReply = canPost && variant !== 'tico';
  const startReply = (m: ChatMessage) => {
    setReplying({ id, m });
    footRef.current?.querySelector('textarea')?.focus();
  };
  // A quote goes to the message it names when that is loaded.
  const toQuoted = (message: string) => {
    if (!scroller.current?.scrollToKey(message)) toast({ text: 'That message is not loaded', kind: 'failed' });
  };

  /* ---- a jump from the Outline: page back until the message is loaded, then bring it to the middle */
  const { fetchNextPage } = timeline;
  useEffect(
    () =>
      onJump(id, (message) => {
        void (async () => {
          for (let n = 0; n <= JUMP_PAGES; n++) {
            if (scroller.current?.scrollToKey(message)) return;
            if (n === JUMP_PAGES) break;
            const r = await fetchNextPage();
            await frame();
            if (!r.hasNextPage && !scroller.current?.scrollToKey(message)) break;
          }
          if (!scroller.current?.scrollToKey(message)) toast({ text: 'That message is not in this chat', kind: 'failed' });
        })();
      }),
    [id, fetchNextPage],
  );

  /* ---- the person's Good and Not good on what others said */
  const rateable = own.filter((m) => canRate(m, myId, variant === 'tico')).map((m) => m.id);
  const { data: ratings = [] } = useQuery(feedbackQuery(id, rateable));

  /* ---- read up to the last message shown, while it is in view */
  const readAtEnd = () => {
    if ((scroller.current?.fromEnd() ?? 0) > READ_PX || document.visibilityState !== 'visible') return;
    const newest = new Map<string, number>();
    for (const m of rows) {
      if (m.hidden || typeof m.seq !== 'number') continue;
      const thread = m.thread_id || id;
      if (m.seq > (newest.get(thread) ?? marks?.get(thread) ?? 0)) newest.set(thread, m.seq);
    }
    for (const [thread, seq] of newest) markRead(thread, seq);
  };
  const readOnMessage = useEffectEvent(readAtEnd);
  useEffect(() => readOnMessage(), [id, timeline.data]);

  /* ---- the Dot, for Tico's own chat only */
  const doing = variant === 'tico' ? dotFor(rows, live) : null;
  useEffect(() => {
    if (!doing) return;
    return dot.set(doing);
  }, [doing]);

  const send = (text: string, files: File[], retry?: ChatMessage) => {
    if (!myId) return;
    jump.current = true;
    const answers = retry ? retry.in_reply_to : replyTo?.id;
    const quote = retry ? retry.reply_to : replyTo && quoteFrom(replyTo);
    if (!retry) setReplying(null);
    void sendMessage({
      id,
      variant,
      text,
      files,
      page: at && { path: at.path, title: at.title },
      me: myId,
      ...(retry && { retry: retry.id }),
      ...(answers && { replyTo: answers, quote }),
    });
  };

  const list = (
    <VirtualList
      key={id}
      ref={scroller}
      items={items}
      getKey={(it) => it.key}
      estimateSize={ROW_PX}
      measure
      anchor="end"
      role="log"
      label={`Chat with ${name}`}
      className="chat-body"
      onScroll={readAtEnd}
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
        const rate = rateable.includes(it.m.id);
        return (
          <Message
            m={it.m}
            variant={variant}
            me={myId}
            cont={it.cont}
            onRetry={(m) => send(m.body ?? '', m.files ?? [], m)}
            rateIn={rate ? id : undefined}
            feedback={rate ? (ratings.find((f) => f.subject_id === it.m.id) ?? null) : null}
            card={card}
            reactIn={variant === 'tico' || extraIds.has(it.m.id) ? undefined : id}
            onReply={canReply && !extraIds.has(it.m.id) ? startReply : undefined}
            quote={quoteOf(it.m, byId)}
            onQuote={toQuoted}
          />
        );
      }}
    />
  );

  // Bones while the first page loads; a read that failed says so with Try again (the box stays).
  const body = timeline.isPending ? (
    <MessageBones />
  ) : timeline.isError && !timeline.data ? (
    <Empty icon="alert" title="This chat did not load">
      <Button onClick={() => void timeline.refetch()}>Try again</Button>
    </Empty>
  ) : (
    list
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
    <footer className="chat-foot" data-dock ref={footRef}>
      {foot}
      {look}
      {canReply && replyTo && myId && <Replying q={quoteFrom(replyTo)} me={myId} onDrop={() => setReplying(null)} />}
      {myId && canPost && <Composer id={id} me={myId} to={name} canSend={!!timeline.data} onSend={(text, files) => send(text, files)} />}
    </footer>
  );

  if (variant === 'page')
    return (
      <div className="tv-col chat-page">
        {body}
        {footer}
      </div>
    );
  if (variant === 'side') {
    const who = whoOf({ sender_id: actor ?? '' }, variant, myId);
    const face = <Face who={{ ...who, name }} size={28} />;
    // The face and the name go to the teammate's page; one link is read out, the face's is for the pointer.
    return (
      <>
        <SheetHead
          kind="side"
          title={mateHref ? <Link className="head-name" href={mateHref}>{name}</Link> : name}
          lead={mateHref ? <Link className="head-face" href={mateHref} tabIndex={-1} aria-hidden>{face}</Link> : face}
        />
        {body}
        {footer}
      </>
    );
  }
  return (
    <>
      {body}
      {footer}
    </>
  );
}
