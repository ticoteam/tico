import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useEffect, useEffectEvent, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { refusalFor } from '@/actions';
import { toast } from '@/kit/Toast';
import { meQuery, useActor, type Actor } from '@/data';
import { DidLine } from '@/kit/DidLine';
import { Link } from '@/kit/Link';
import { useFollow, type TimelineView } from '@/live';
import { capCount } from '@/kit/Count';
import { IconButton } from '@/kit/IconButton';
import { motionLevel } from '@/motion';
import { VirtualList, type VirtualListHandle } from '@/kit/VirtualList';
import { SheetHead } from '@/shell';
import { fromPage, pageAt, type FromPage } from '@/shell/pageOwner';
import { dot, type DotState } from '@/tico/dot';
import { usePageName } from '@/tico/pageName';
import { ReadFault } from '@/pagekinds';
import { MessageBones } from './Bones';
import { Composer, readDraft, writeDraft } from './Composer';
import { dayKey, dayLabel } from './dates';
import { Face, LiveRow, Message, whoOf } from './Message';
import { onJump } from './Outline';
import { ChatPlace } from './Rich';
import { quoteFrom, quoteOf, Replying } from './Quote';
import { SessionLine, type Session } from './Session';
import { installConversationLive, isDmChanged, liveQuery, liveText, markRead, rowsOf, sendMessage, timelineQuery, type TaskSend } from './queries';
import { ReplyBox } from './ReplyBox';
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
  /** The box's own words in place of "Message …": a person's own task, which they write on, not to themself. */
  compose?: string;
  /** Not the thread's own messages, drawn among them by time (a team's updates, through `card`): no reply, no rating. */
  extra?: ChatMessage[];
  /** This thread as the shell's `?chat=` (a second chat's own address): a page a message links to opens beside it.
      Defaults to `thread:<id>`; Tico's sheet has none (it is always there). */
  chat?: string;
  /** Actor ids the box's @ list names first (a task's people and agents). */
  mentionFirst?: readonly string[];
  /** The only actor ids the box's @ list may name (a direct chat's members). */
  mentionOnly?: readonly string[];
  /** A task's chat: each message is a task comment, to `to`'s session when one is named, or the answer to the ❓ `ask`
      (queries.ts Send.task). */
  task?: TaskSend;
  /** One view of a task's thread: its comments, each reply one level under its comment and Reply in place under it; or
      one agent's session, with that agent's reply being written. Neither marks the thread read by itself: the task's
      page does (one read mark per task). */
  view?: TimelineView;
  /** false: the box shows, but nothing can be sent from it (who may not message the agent). */
  canSend?: boolean;
  /** Over the messages of a chat drawn as the page: which chat this is, and the way out of it (an agent's session). */
  head?: ReactNode;
}

type Item =
  | { k: 'day'; key: string; label: string; isNew: boolean }
  | { k: 'did'; key: string; text: string; fault: boolean }
  | { k: 'msg'; key: string; m: ChatMessage; cont: boolean; under?: string }
  | { k: 'live'; key: string; r: LiveRun; text: string }
  | { k: 'reply'; key: string; m: ChatMessage };

/** A message that is someone's (a person's or an agent's), not a Tico line: what Reply answers in a task's comments. */
const someones = (m: ChatMessage) => /^(human|bot):./.test(m.sender_id ?? '');

/** A task's comments as they read: each comment, then the replies under it, one level deep (a reply to a reply sits
    under the same comment). A reply whose comment is not loaded stays where it is, with its quote. `under` is the
    comment a reply sits under. */
export function nest(rows: ChatMessage[]): { m: ChatMessage; under?: string }[] {
  const byId = new Map(rows.map((m) => [m.id, m]));
  const topOf = (m: ChatMessage) => {
    let at = m;
    const seen = new Set([m.id]);
    for (let up = at.in_reply_to && byId.get(at.in_reply_to); up && !seen.has(up.id); up = at.in_reply_to && byId.get(at.in_reply_to)) {
      seen.add(up.id);
      at = up;
    }
    return at;
  };
  const tops: ChatMessage[] = [];
  const replies = new Map<string, ChatMessage[]>();
  for (const m of rows) {
    const top = topOf(m);
    if (top === m) tops.push(m);
    else replies.set(top.id, [...(replies.get(top.id) ?? []), m]);
  }
  return tops.flatMap((m) => [{ m }, ...(replies.get(m.id) ?? []).map((r) => ({ m: r, under: m.id }))]);
}

const didsOf = (m: ChatMessage): string[] => {
  const d = m.refs?.did;
  return (Array.isArray(d) ? d : d ? [d] : []).filter((x): x is string => typeof x === 'string' && !!x.trim());
};

/** The list as it reads, oldest first: day breaks, the "New" line, what Tico did, messages, and replies being written.
    `nested`: a task's comments, each reply under its comment (`nest`), and the Reply being written there (`replying`). */
function itemsOf(rows: ChatMessage[], live: LiveRun[], me: string | undefined, id: string, marks: Map<string, number> | null, nested = false, replying: ChatMessage | null = null): Item[] {
  const out: Item[] = [];
  let day = '';
  let newShown = false;
  let last: ChatMessage | null = null;
  const ordered = nested ? nest(rows) : rows.map((m) => ({ m, under: undefined }));
  // the "New" line, once: before the first message past the read mark as the list reads, a reply under an older
  // comment included
  const isNew = (m: ChatMessage) => {
    const mark = marks?.get(m.thread_id || id) ?? marks?.get(id);
    return !newShown && mark != null && typeof m.seq === 'number' && m.seq > mark && m.sender_id !== me && !m.pending;
  };
  for (const { m, under } of ordered) {
    if (m.hidden) continue;
    if (under) {
      if (m.deleted || (m.body ?? '').trim() || m.refs?.attachments?.length) {
        if (isNew(m)) {
          newShown = true;
          out.push({ k: 'day', key: 'new', label: 'New', isNew: true });
        }
        out.push({ k: 'msg', key: m.id, m, cont: false, under });
      }
      last = null;
      continue;
    }
    const d = dayKey(m.created_at);
    if (d && d !== day) {
      day = d;
      out.push({ k: 'day', key: `day:${d}`, label: dayLabel(m.created_at), isNew: false });
      last = null;
    }
    if (isNew(m)) {
      newShown = true;
      out.push({ k: 'day', key: 'new', label: 'New', isNew: true });
      last = null;
    }
    const dids = didsOf(m);
    for (const [i, text] of dids.entries()) out.push({ k: 'did', key: `did:${m.id}:${i}`, text, fault: false });
    if (typeof m.refs?.fault === 'string') out.push({ k: 'did', key: `fault:${m.id}`, text: m.refs.fault, fault: true });
    if (dids.length || m.refs?.fault) last = null;
    if (!m.deleted && !(m.body ?? '').trim() && !m.refs?.attachments?.length && !m.refs?.action) continue;
    // a task's comments are a log: each has its author row (where a phone's Reply sits), never run on from the one above;
    // one written for the person by someone else (refs.via) starts its own run, so its line says who wrote it
    const cont = !nested && !!last && last.sender_id === m.sender_id && last.refs?.via === m.refs?.via;
    out.push({ k: 'msg', key: m.id, m, cont });
    last = m;
  }
  for (const r of live) out.push({ k: 'live', key: `live:${r.run}`, r, text: liveText(r) });
  if (replying) {
    // under the comment and its replies, or under the reply itself when its comment is not loaded
    const top = ordered.find((x) => x.m.id === replying.id)?.under ?? replying.id;
    const at = out.findLastIndex((it) => it.k === 'msg' && (it.key === top || it.under === top));
    if (at >= 0) out.splice(at + 1, 0, { k: 'reply', key: `reply:${replying.id}`, m: replying });
  }
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

export function Conversation({ id, variant, title, actor, session, from, starters, toDm, onMoved, foot, card, below, canPost = true, compose, extra, chat, mentionFirst, mentionOnly, task, view, canSend = true, head }: ConversationProps) {
  useFollow(variant === 'tico' ? { conversation: id } : { thread: id });

  const { data: me } = useQuery(meQuery);
  const myId = me?.id;
  const timeline = useInfiniteQuery(timelineQuery(id, view));
  const { data: runs = [] } = useQuery(liveQuery(id));
  // A session shows its agent's reply being written; the comments, none (each agent's work is in its session).
  const live = !view ? runs : 'session' in view ? runs.filter((r) => r.bot === view.session) : [];
  const comments = !!view && 'comments' in view;
  // the Agent bar's rows that failed when its DM changed under them, kept by its agent, for Retry here
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

  /* ---- a reply being written: in a task's comments, in place under the comment it answers (`inline`); elsewhere the
     message it answers sits above the box until it is sent or dropped. Tico's own chat takes no reply (POST
     /api/v2/assistant/messages has no in_reply_to). */
  const [replying, setReplying] = useState<{ id: string; m: ChatMessage; inline: boolean } | null>(null);
  const shownReply = replying?.id === id ? replying : null;
  const replyTo = shownReply && !shownReply.inline ? shownReply.m : null;
  const inline = shownReply?.inline ? shownReply.m : null;

  const items = itemsOf(rows, live, myId, id, marks, comments, inline);
  const mate = useActor(actor);
  // never a stand-in word for someone: their name, else what they are
  const name = variant === 'tico' ? 'Tico' : (title ?? mate?.name ?? (actor?.startsWith('bot:') ? 'Agent' : 'Person'));
  const here = useHerePage();
  const at = from === undefined ? here : from;
  // the Agent bar's box keeps its draft by its agent, not by the thread it shows (which may change under it)
  const draftId = toDm ? `dm:${toDm}` : undefined;
  // each view of a task's thread keeps its own draft: words for one agent never wait in another's box, or the comments'
  const boxId = draftId ?? (!view ? id : 'session' in view ? `${id}:session:${view.session}` : `${id}:comments`);
  // a starter puts its words in the box; opening the chat never sends anything, so it never starts a run. Only in the
  // box it was pressed for: another chat shown here (another agent's page) never takes them.
  const [filling, setFill] = useState<{ text: string; blank?: boolean; box: string } | null>(null);
  const fill = filling?.box === boxId ? filling : null;
  // a file goes as a thread message, which carries no session (the task's comment route has no upload yet)
  const attach = !task?.to;
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

  const footRef = useRef<HTMLElement>(null);
  // An agent's session takes no Reply: a reply reaches its author as a comment does, so it lives in the comments.
  const canReply = canPost && variant !== 'tico' && (!view || comments);
  const startReply = (m: ChatMessage) => {
    setReplying({ id, m, inline: comments });
    if (!comments) footRef.current?.querySelector('textarea')?.focus();
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

  /* ---- read up to the last message shown, while it is in view; a view of a task's thread never does (its page does) */
  const readAtEnd = () => {
    if (view || (scroller.current?.fromEnd() ?? 0) > READ_PX || document.visibilityState !== 'visible') return;
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

  const send = (text: string, files: File[], retry?: ChatMessage, answering?: ChatMessage) => {
    if (!myId) return;
    // a reply written in place stays where it is: the list does not jump to its end
    jump.current = !answering;
    const to = answering ?? replyTo;
    const answers = retry ? retry.in_reply_to : to?.id;
    const quote = retry ? retry.reply_to : to && quoteFrom(to);
    const replied = replyTo;
    if (!retry) setReplying(null);
    // The Agent bar's send that fails (any refusal or fault) keeps no row: its words go back into the box, its draft
    // kept by the agent (`draftId`), so they stay through the bar reading its DM again; the reason shows; and the
    // quote stays unless the DM changed under it (what it answered is in the other one). Send again sends it.
    // Newer words are never written over: with something typed since, the box keeps it and the toast holds the unsent
    // words until it is closed, so nothing is lost.
    let failed = false;
    const onFailed = (e: unknown) => {
      failed = true;
      const why = refusalFor(e, 'Not sent').name;
      const empty = !myId || !draftId || !readDraft(myId, draftId).trim();
      if (empty) {
        if (myId && draftId) writeDraft(myId, draftId, text);
        setFill({ text, box: boxId }); // the box it was sent from, which holds its draft
        toast({ text: 'Not sent', ...(why && { name: why }), kind: 'failed' });
      } else toast({ text: why ? `Not sent (${why})` : 'Not sent', name: text, kind: 'failed', timeout: 0 });
      if (replied && !isDmChanged(e)) setReplying({ id, m: replied, inline: false });
      onMoved?.('');
    };
    void sendMessage({
      id,
      variant,
      text,
      files,
      // a retry is the same message: it says the page it was first written on
      from: retry ? (retry.refs?.from_page ?? null) : at,
      me: myId,
      ...(toDm && { toDm, onMoved, onFailed }),
      ...(task && { task }),
      ...(view && { view }),
      ...(retry && { retry: retry.id }),
      ...(answers && { replyTo: answers, quote }),
    }).then((sent) => {
      // the org rules held it: said, as everywhere a message is held
      if (toDm && !sent && !failed) toast({ text: 'Held for a check' });
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
        className={comments ? 'chat-body is-comments' : 'chat-body'}
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
          if (it.k === 'reply')
            return myId ? (
              <ReplyBox thread={it.m.thread_id || id} m={it.m} me={myId} draft={`${id}:reply:${it.m.id}`} canSend={canSend} mentionFirst={mentionFirst}
                onSend={(text, files) => send(text, files, undefined, it.m)} onDrop={() => setReplying(null)} />
            ) : null;
          const message = (
            <Message
              m={it.m}
              variant={variant}
              me={myId}
              cont={it.cont}
              onRetry={(m) => send(m.body ?? '', m.files ?? [], m)}
              card={card}
              below={below}
              onReply={canReply && !extraIds.has(it.m.id) && (!comments || someones(it.m)) ? startReply : undefined}
              // a reply under the comment it answers needs no quote of it
              quote={it.under && it.m.in_reply_to === it.under ? null : quoteOf(it.m, byId)}
              onQuote={toQuoted}
            />
          );
          return it.under ? <div className="msg-under">{message}</div> : message;
        }}
      />
    </ChatPlace>
  );

  // Bones while the first page loads; a read that failed says why (the box stays).
  const body = timeline.isPending ? (
    <MessageBones />
  ) : timeline.isError && !timeline.data ? (
    <ReadFault what="This chat" reads={[timeline]} />
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
            <button key={words} type="button" className="qchip" onClick={() => setFill({ text: words.replace(/…$/, ' '), blank: true, box: boxId })}>
              {words}
            </button>
          ))}
        </div>
      )}
      {/* no files where the box writes to an agent's session (`attach`; sendMessage drops any); Tico's upload route takes
          files only with words (backend/models.py AssistantUpload) */}
      {myId && canPost && <Composer key={boxId} id={boxId} me={myId} to={name} label={compose} fill={fill} canSend={canSend && !!timeline.data} attach={attach} needsText={variant === 'tico'} mentions={!toDm} mentionFirst={mentionFirst} mentionOnly={mentionOnly} onSend={(text, files) => send(text, files)} />}
    </footer>
  );

  if (variant === 'page')
    return (
      <div className="tv-col chat-page">
        {head}
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
