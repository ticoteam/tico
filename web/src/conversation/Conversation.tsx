import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useEffect, useEffectEvent, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { toast } from '@/kit/Toast';
import { meQuery, useActor, type Actor } from '@/data';
import { Button } from '@/kit/Button';
import { DidLine } from '@/kit/DidLine';
import { Empty } from '@/kit/Empty';
import { Link } from '@/kit/Link';
import { useFollow } from '@/live';
import { capCount } from '@/kit/Count';
import { IconButton } from '@/kit/IconButton';
import { motionLevel } from '@/motion';
import { VirtualList, type VirtualListHandle } from '@/kit/VirtualList';
import { SheetHead } from '@/shell';
import { fromPage, pageAt, type FromPage } from '@/shell/pageOwner';
import { dot, type DotState } from '@/tico/dot';
import { usePageName } from '@/tico/pageName';
import { MessageBones } from './Bones';
import { Composer } from './Composer';
import { dayKey, dayLabel } from './dates';
import { Face, LiveRow, Message, whoOf } from './Message';
import { onJump } from './Outline';
import { ChatPlace } from './Rich';
import { quoteFrom, quoteOf, Replying } from './Quote';
import { SessionLine, type Session } from './Session';
import { installConversationLive, liveQuery, liveText, markRead, rowsOf, sendMessage, timelineQuery, useStranded } from './queries';
import type { ChatMessage, LiveRun, Variant } from './types';

/** Within this many pixels of the end counts as "at the bottom": what comes in is read, and the asks over the box come back. */
const READ_PX = 24;
/** Scrolled up past this, the asks over the box (Tico's chips) fade away. More than their own height, so the room
    they give back cannot bring the chat to its end again. */
const UP_PX = 120;
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
  /** A second chat's head, under the name: which of the agent's chats this is (a task's, or its main chat). */
  session?: Session;
  /** The page it was written on, sent as `from_page` with each message (nothing is drawn). Defaults to the address's
      page (shell/pageOwner.ts) with its own name. */
  from?: FromPage | null;
  /** With no messages yet: words to start with, each a press that puts them in the box (nothing is sent). */
  starters?: readonly string[];
  /** The Agent bar's: each message goes to the sender's DM with exactly this agent, found by the server as it writes;
      `onMoved` when that is another thread than this one (the chat then shows that one). */
  toDm?: string;
  onMoved?: (thread: string) => void;
  /** Above the composer, in the chat's foot: a card the chat is waiting on (a task's answer card). */
  foot?: ReactNode;
  /** What a message draws in place of its text, when it is more than words (a thread's call card). */
  card?: (m: ChatMessage) => ReactNode;
  /** What someone else's message draws under its words (Tico's approval card). */
  below?: (m: ChatMessage) => ReactNode;
  /** false: the person may read but not write here (an archived, closed or meeting thread): no box. */
  canPost?: boolean;
  /** Not the thread's own messages, drawn among them by time (a team's updates, through `card`): no reply, no rating. */
  extra?: ChatMessage[];
  /** This thread as the shell's `?chat=` (a second chat's own address): a page a message links to opens beside it.
      Defaults to `thread:<id>`; Tico's sheet has none (it is always there). */
  chat?: string;
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

/** The messages from others after `seen` (the newest when the person scrolled away): what Jump to latest counts. */
function newSince(items: Item[], seen: string, me: string | undefined): number {
  const from = items.findLastIndex((it) => it.key === seen);
  if (from < 0) return 0;
  let n = 0;
  for (const it of items.slice(from + 1)) if (it.k === 'msg' && it.m.sender_id !== me && !it.m.hidden) n++;
  return n;
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

/* the page the person is on, by its own name (its h1, as the tab's title says it), not the window's "<name> - Tico". Kept
   current: a page whose data comes after the chat drew (a task opened beside its thread) names itself once it has. */
function useHerePage(): FromPage {
  usePageName(); // read again as the page changes
  return fromPage(typeof location === 'undefined' ? pageAt('/') : pageAt(location.pathname, location.search));
}


installConversationLive();

/** A second chat's head: the face and the name, which go to the teammate's page (one link is read out, the face's is
    for the pointer), and which of their chats this is; the frame adds its fold or close control. Drawn too while the chat
    cannot load, so that control stays. */
export function SideHead({ actor, name, session }: { actor?: string; name: string; session?: Session }) {
  const { data: me } = useQuery(meQuery);
  const mateHref = teammateHref(actor, useActor(actor));
  const face = <Face who={{ ...whoOf({ sender_id: actor ?? '' }, 'side', me?.id), name }} size={28} />;
  return (
    <SheetHead
      kind="side"
      title={mateHref ? <Link className="head-name" href={mateHref}>{name}</Link> : name}
      lead={mateHref ? <Link className="head-face" href={mateHref} tabIndex={-1} aria-hidden>{face}</Link> : face}
      sub={session && <SessionLine session={session} />}
    />
  );
}

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

export function Conversation({ id, variant, title, actor, session, from, starters, toDm, onMoved, foot, card, below, canPost = true, extra, chat }: ConversationProps) {
  useFollow(variant === 'tico' ? { conversation: id } : { thread: id });

  const { data: me } = useQuery(meQuery);
  const myId = me?.id;
  const timeline = useInfiniteQuery(timelineQuery(id));
  const { data: live = [] } = useQuery(liveQuery(id));
  // the Agent bar's rows that failed when its DM changed under them, kept by its agent, for Retry here
  const stranded = useStranded(toDm);
  const ownRows = rowsOf(timeline.data);
  const own = stranded.length ? [...ownRows, ...stranded.filter((m) => !ownRows.some((r) => r.id === m.id))] : ownRows;
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
  const mate = useActor(actor);
  // never a stand-in word for someone: their name, else what they are
  const name = variant === 'tico' ? 'Tico' : (title ?? mate?.name ?? (actor?.startsWith('bot:') ? 'Agent' : 'Person'));
  const here = useHerePage();
  const at = from === undefined ? here : from;
  // a starter puts its words in the box; opening the chat never sends anything, so it never starts a run
  const [fill, setFill] = useState<{ text: string } | null>(null);
  const empty = !!starters?.length && !!timeline.data && !rows.some((m) => !m.hidden) && !live.length;

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

  /* ---- where the person is: scrolled up past UP_PX, the asks over the box fade; up more than a window, Jump to latest
     shows, counting what came in since. Set only when that changes, so scrolling draws nothing. */
  const [place, setPlace] = useState({ id, up: false, far: false, seen: '' });
  const where = place.id === id ? place : { id, up: false, far: false, seen: '' };
  const followPlace = () => {
    const s = scroller.current;
    if (!s) return;
    const off = s.fromEnd();
    const up = off > UP_PX ? true : off <= READ_PX ? false : where.up;
    const far = off > s.height();
    if (up === where.up && far === where.far) return;
    const newest = items.findLast((it) => it.k === 'msg')?.key ?? '';
    setPlace({ id, up, far, seen: far ? (where.far ? where.seen : newest) : '' });
  };
  const fresh = where.far ? newSince(items, where.seen, myId) : 0;
  const toLatest = () => scroller.current?.scrollToEnd(motionLevel() !== 'off');

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
    // a retry in another thread than its own (a stranded row of the Agent bar) answers nothing there: what it replied
    // to is in the thread it was first written in
    const home = !retry || (retry.thread_id || retry.conversation_id) === id;
    const answers = retry ? (home ? retry.in_reply_to : undefined) : replyTo?.id;
    const quote = retry ? (home ? retry.reply_to : undefined) : replyTo && quoteFrom(replyTo);
    if (!retry) setReplying(null);
    void sendMessage({
      id,
      variant,
      text,
      files,
      // a retry is the same message: it says the page it was first written on
      from: retry ? (retry.refs?.from_page ?? null) : at,
      me: myId,
      ...(toDm && { toDm, onMoved }),
      ...(retry && { retry: retry.id }),
      ...(answers && { replyTo: answers, quote }),
    });
  };

  const list = (
    <ChatPlace value={variant === 'tico' ? undefined : (chat ?? `thread:${id}`)}>
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
        onScroll={() => {
          readAtEnd();
          followPlace();
        }}
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
          return (
            <Message
              m={it.m}
              variant={variant}
              me={myId}
              cont={it.cont}
              onRetry={(m) => send(m.body ?? '', m.files ?? [], m)}
              card={card}
              below={below}
              onReply={canReply && !extraIds.has(it.m.id) ? startReply : undefined}
              quote={quoteOf(it.m, byId)}
              onQuote={toQuoted}
            />
          );
        }}
      />
    </ChatPlace>
  );

  // Bones while the first page loads; a read that failed says so with Try again (the box stays).
  const body = timeline.isPending ? (
    <MessageBones />
  ) : timeline.isError && !timeline.data ? (
    <Empty icon="alert" title="This chat did not load">
      <Button onClick={() => timeline.refetch()}>Try again</Button>
    </Empty>
  ) : (
    list
  );

  // Jump to latest floats over the messages, centred above the box; it rides the foot's top edge.
  const footer = (
    <footer className="chat-foot" data-dock data-up={where.up || undefined} ref={footRef}>
      <div className={where.far ? 'jump is-on' : 'jump'}>
        <IconButton icon="chevdown" label={fresh ? `Jump to latest, ${fresh} new` : 'Jump to latest'} onClick={toLatest} />
        {fresh > 0 && (
          <i className="jump-n" aria-hidden>
            {capCount(fresh)}
          </i>
        )}
      </div>
      {foot}
      {canReply && replyTo && myId && <Replying q={quoteFrom(replyTo)} me={myId} onDrop={() => setReplying(null)} />}
      {myId && canPost && empty && (
        <div className="qchips">
          {starters!.map((words) => (
            <button key={words} type="button" className="qchip" onClick={() => setFill({ text: words })}>
              {words}
            </button>
          ))}
        </div>
      )}
      {myId && canPost && <Composer id={id} me={myId} to={name} fill={fill} canSend={!!timeline.data} onSend={(text, files) => send(text, files)} />}
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
    return (
      <>
        <SideHead actor={actor} name={name} session={session} />
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
