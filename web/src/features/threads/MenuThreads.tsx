import { useInfiniteQuery, useQueries, useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useRef, useState, type ReactNode } from 'react';
import { Ago } from '@/kit/Ago';
import { Avatar } from '@/kit/Avatar';
import { FlowMark } from '@/kit/FlowMark';
import { Icon, type IconName } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { SkeletonRows } from '@/kit/Skeleton';
import { contactsOf, conversationsOf, isDm, letterAt, letterRows, letterUnread, othersOf, rowsOf, senderName, teamOf, unreadOf, type LetterRow, type Text, type Thread } from './model';
import { Face, press, useMe, useTitle } from './people';
import { archivedQuery, inboxQuery, mailboxesQuery, textListQuery, threadListQuery } from './queries';

const ACTIVE = { className: 'is-active', 'aria-current': 'page' as const };
/* the faces beside a folded Archived */
const FACES = 3;

export interface MenuThreadsProps {
  /** The threads that ask you something: the Dot on their rows. */
  asks: ReadonlySet<string>;
}

type Item =
  | { kind: 'thread'; key: string; at: number; t: Thread }
  | { kind: 'mail'; key: string; at: number; r: LetterRow; box: string; organize: boolean }
  | { kind: 'text'; key: string; at: number; t: Text };

const when = (s: string | null | undefined) => (s ? Date.parse(s) : 0);

/**
 * The menu's Threads tab (plan/threads-team.html, pass 7): every conversation but your 1:1 DMs, newest first. Team
 * threads, group DMs, and what came from outside (mail and texts), each a row: its face, its title, one quiet mark
 * for where it came from, and its age, or the Dot when it asks you. Bold is new. Archive is the one verb, where it is
 * the viewer's own (mail): on hover, or a swipe on a phone. Archived threads fold at the foot.
 */
export function MenuThreads({ asks }: MenuThreadsProps) {
  const me = useMe();
  const list = useInfiniteQuery(threadListQuery);
  const boxes = (useQuery(mailboxesQuery).data ?? []).filter((b) => b.connection !== 'none');
  const inboxes = useQueries({ queries: boxes.map((b) => inboxQuery(b.address)) });
  const texts = useInfiniteQuery(textListQuery);
  const archived = useInfiniteQuery(archivedQuery);
  const [swiped, setSwiped] = useState<string | null>(null);
  const [archOpen, setArchOpen] = useState(false);

  const items: Item[] = [
    ...conversationsOf(rowsOf(list.data), me).map((t): Item => ({ kind: 'thread', key: `thread:${t.id}`, at: when(t.last_message_at), t })),
    ...boxes.flatMap((b, i) =>
      letterRows(inboxes[i]?.data?.messages ?? []).map(
        (r): Item => ({ kind: 'mail', key: `mail:${r.letter.thread_id}`, at: letterAt(r.letter)?.getTime() ?? 0, r, box: b.address, organize: b.may?.organize !== false }),
      ),
    ),
    ...contactsOf(texts.data?.pages.flatMap((p) => p.messages ?? []) ?? []).map((t): Item => ({ kind: 'text', key: `text:${t.from ?? t.message_id}`, at: when(t.received_at), t })),
  ].sort((a, b) => b.at - a.at);
  const gone = (archived.data?.pages.flatMap((p) => p.threads ?? []) ?? []).filter((t) => !t.task_id);
  const swipe = (key: string) => (on: boolean) => setSwiped(on ? key : null);

  return (
    <>
      <div className="ca-list">
        {list.isPending ? <SkeletonRows rows={3} label="Loading threads" /> : null}
        {list.isError && !list.data ? (
          <button type="button" className="tree-row ca-more" disabled={list.isFetching} onClick={() => void list.refetch()}>
            Try again
          </button>
        ) : null}
        {items.map((it) =>
          it.kind === 'thread' ? (
            <ThreadLine key={it.key} t={it.t} ask={asks.has(it.t.id)} />
          ) : it.kind === 'mail' ? (
            <MailLine key={it.key} r={it.r} box={it.box} organize={it.organize} swiped={swiped === it.key} onSwipe={swipe(it.key)} />
          ) : (
            <TextLine key={it.key} t={it.t} />
          ),
        )}
        {list.hasNextPage ? (
          <button type="button" className="tree-row ca-more" disabled={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
            <span className="tree-ic" />
            <span className="tree-name">Older</span>
          </button>
        ) : null}
        {archOpen ? (
          <>
            <button type="button" className="grp ca-arch" aria-expanded onClick={() => setArchOpen(false)}>
              <span className="tree-name">Archived</span>
              <Icon name="chevdown" />
            </button>
            {gone.map((t) => (
              <ThreadLine key={t.id} t={t} ask={false} gone />
            ))}
          </>
        ) : null}
      </div>
      {gone.length && !archOpen ? (
        <div className="ca-src">
          <button type="button" className="grp" aria-expanded={false} onClick={() => setArchOpen(true)}>
            <span className="tree-name">Archived</span>
            <span className="faces">
              {gone.slice(0, FACES).map((t) => (
                <ThreadFace key={t.id} t={t} size={16} />
              ))}
            </span>
            <Icon name="chevron" />
          </button>
        </div>
      ) : null}
    </>
  );
}

/** A thread's face: who it is with (the other person in a DM, the first others in a group DM), a team's mark, or the
    thread's mark. */
function ThreadFace({ t, size = 22 }: { t: Thread; size?: 16 | 22 }) {
  const me = useMe();
  if (teamOf(t))
    return (
      <span className="tree-ic">
        <Icon name="people" />
      </span>
    );
  const who = isDm(t) ? othersOf(t, me)[0] : undefined;
  if (who) return <Face actor={who} size={size} />;
  return (
    <span className="tree-ic">
      <Icon name="threads" />
    </span>
  );
}

/* No Archive on a Tico thread yet: the server's archive (PATCH archived) archives the thread for everyone in it.
   Server need: a per-person archive for threads, a member-level archived flag that clears when someone writes. Until
   then only mail, whose archive is the viewer's own mailbox's, has the verb. */
function ThreadLine({ t, ask, gone }: { t: Thread; ask: boolean; gone?: boolean }) {
  const title = useTitle(t);
  const isNew = !gone && (unreadOf(t) > 0 || !!t.me?.activity);
  const at = gone ? t.archived_at : t.last_message_at;
  return (
    <Line>
      <Link
        className={isNew ? 'tree-row is-new' : 'tree-row'}
        to="/threads/$thread"
        params={{ thread: t.id }}
        activeProps={ACTIVE}
        title={title}
        data-target={`thread:${t.id}`}
      >
        <ThreadFace t={t} />
        <Tail title={title} ask={ask} at={at} />
      </Link>
    </Line>
  );
}

function MailLine({ r, box, organize, swiped, onSwipe }: { r: LetterRow; box: string; organize: boolean; swiped: boolean; onSwipe: (on: boolean) => void }) {
  const l = r.letter;
  const from = senderName(l);
  const title = l.subject || from;
  return (
    <Line archive={organize ? () => void press('mail.archive', { box, letter: l.thread_id, ids: r.ids }) : undefined} swiped={swiped} onSwipe={onSwipe}>
      <Link
        className={letterUnread(l) ? 'tree-row is-new' : 'tree-row'}
        to="/threads/mail"
        search={{ box, open: l.thread_id }}
        activeProps={ACTIVE}
        activeOptions={{ includeSearch: true }}
        title={`${from}: ${title}`}
        data-target={`letter:${l.thread_id}`}
      >
        <Avatar kind="person" name={from} className="is-out" decorative />
        <Tail title={title} mark="mail" markLabel="Mail" at={letterAt(l)?.toISOString()} />
      </Link>
    </Line>
  );
}

/** A contact who texted the company's numbers: no archive yet (texts have none). */
function TextLine({ t }: { t: Text }) {
  const from = t.from || 'Unknown';
  return (
    <Line>
      <Link className="tree-row" to="/threads/messaging" search={{ open: from }} activeProps={ACTIVE} activeOptions={{ includeSearch: true }} title={from} data-target={`contact:${from}`}>
        <Avatar kind="person" name={from} className="is-out" decorative />
        <Tail title={from} mark="sms" markLabel="Text" at={t.received_at} />
      </Link>
    </Line>
  );
}

/** Title, the one quiet mark, then the Dot when it asks you, else its age. */
function Tail({ title, mark, markLabel, ask, at }: { title: string; mark?: IconName; markLabel?: string; ask?: boolean; at?: string | null }) {
  return (
    <>
      <span className="tree-name">{title}</span>
      {mark ? <Icon name={mark} label={markLabel} className="cr-mark" /> : null}
      {ask ? <FlowMark label="Asks you" /> : at ? <Ago at={at} className="cr-age" /> : null}
    </>
  );
}

/** A row with Archive: its icon on hover, or a swipe to the left on a touch screen. */
function Line({ archive, swiped = false, onSwipe, children }: { archive?: () => void; swiped?: boolean; onSwipe?: (on: boolean) => void; children: ReactNode }) {
  const start = useRef<{ x: number; y: number } | null>(null);
  const moved = useRef(false);
  return (
    <div
      className={swiped ? 'cr is-swiped' : 'cr'}
      onPointerDown={(e) => {
        moved.current = false;
        start.current = archive && e.pointerType === 'touch' ? { x: e.clientX, y: e.clientY } : null;
      }}
      onPointerMove={(e) => {
        const s = start.current;
        if (!s) return;
        const dx = e.clientX - s.x;
        if (Math.abs(dx) < 24 || Math.abs(dx) < Math.abs(e.clientY - s.y)) return;
        moved.current = true;
        start.current = null;
        onSwipe?.(dx < 0);
      }}
      onPointerUp={() => (start.current = null)}
      onClickCapture={(e) => {
        if (!moved.current && !swiped) return;
        // a swipe is not a tap; a tap on a swiped row puts it back
        if (!(e.target as Element).closest('.cr-swipe')) {
          e.preventDefault();
          e.stopPropagation();
          if (!moved.current) onSwipe?.(false);
        }
        moved.current = false;
      }}
    >
      {children}
      {archive && !swiped ? <IconButton className="cr-arch" icon="archive" label="Archive" size="sm" quiet onClick={archive} /> : null}
      {archive && swiped ? (
        <button type="button" className="cr-swipe" onClick={archive}>
          <Icon name="archive" />
          Archive
        </button>
      ) : null}
    </div>
  );
}
