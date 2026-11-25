import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useRouter } from '@tanstack/react-router';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { run } from '@/actions';
import { ApiError } from '@/api/client';
import { agentWords, Conversation, MessageBones, SessionLine, type ChatMessage, type Session } from '@/conversation';
import { meQuery, useActor } from '@/data';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { useCards } from '@/kit/Popover';
import { Link } from '@/kit/Link';
import { Viewer } from '@/kit/Viewer';
import { toast } from '@/kit/Toast';
import { useFollow } from '@/live/follow';
import { Document, type PageState, type PageTab, ReadFault } from '@/pagekinds';
import { FilePreview } from '@/features/library/exports';
import { flowQuery, flowSnoozedAsksQuery } from '@/features/tasks/routing';
import { whoHref } from '@/features/team/routing';
import { changeWords, closeTask, NEEDS_YOU, nextInList, openOf, type TaskWords } from '@/lib';
import { onAnswered } from './answered';
import { Decision } from './Answer';
import { AddMenu, Details, type Adding } from './Details';
import { EmailSource, Email as EmailView } from './Email';
import { BlockedOn } from './Facts';
import { ArchiveMenu } from './Files';
import { FileTab, FileView, PageBones } from './FileView';
import { TaskMedia } from './Media';
import { MoreMenu, QuietLine, WhereLine } from './Head';
import { askOf, CHAT, changesAskedOf, decisionOf, defaultTab, DETAILS, emailOf, failedToast, requirementOf, sendTitle, tabAddress, tabsOf, targetOf, type TaskTab } from './model';
import { FlowMark } from '@/kit/FlowMark';
import { Face } from './parts';
import { taskDetailQuery, taskRefQuery, type TaskData } from './queries';
import { useTaskWords } from './routing';
import { useTabs, type Tab } from './tabs';
import { TaskStatus } from './StatusLine';
import { approvalCard, useApprovalCards } from './ThreadCards';
import { QuestionCard } from './QuestionCard';
import { PageView, useVersions, VersionBar } from './Versions';
import { Brief, EmailPointers, MatterBrief, MatterDates, MatterDatesOf, WaitingOnYou } from './Waiting';
import { answersHere, questionsFor, waitsNow } from './waits';
import { ChatNotRead, COMMENTS, SessionRun, useTaskLive, useTaskReadMark, useThreadRead } from './sessions';
import './task.css';

/** How often an open task reads itself again, as a backstop for the stream. */
const STATUS_BACKSTOP_MS = 5 * 60_000;
/** How soon a task that did not load (the network, a server restarting) is read again. */
const RETRY_MS = 5_000;
const refused = (e: unknown) => e instanceof ApiError && (e.status === 403 || e.status === 404);

export interface TaskPageProps {
  /** The address's task: its number, or its id. */
  task: string;
  /** The open tab's slug; none opens the page that waits on the person, and the address then names it. */
  tab?: string;
  /** A file from Details, open in the page sheet. */
  file?: string;
}

/** The address's task, read once and kept current by the stream while it is open. `opens`: the task's page, which
    reads it afresh as it opens: what changed while it was closed (a ❓ cleared, say) reaches no copy off screen, and a
    later row merged into that copy would make it look fresh. The chat beside it shares that read. */
function useTask(param: string, opens = false) {
  const ref = useQuery({ ...taskRefQuery(param), refetchInterval: (q) => (q.state.status === 'error' && !refused(q.state.error) ? RETRY_MS : false) });
  // On this screen only, the task reads itself again every few minutes: the stream sends its status line as it changes,
  // and this heals one it missed (a backstop, not the way it updates).
  // a read that failed on the way (not a refusal) tries again by itself until the server answers
  const detail = useQuery({ ...taskDetailQuery(ref.data), refetchInterval: (q) => (q.state.status === 'error' && !refused(q.state.error) ? RETRY_MS : STATUS_BACKSTOP_MS), ...(opens && { refetchOnMount: 'always' as const }) });
  useFollow(ref.data ? { task: ref.data } : null);
  const error = ref.error ?? detail.error;
  const retry = () => void (ref.isError ? ref.refetch() : detail.refetch());
  return { t: detail.data, error, retry };
}

function stateOf(error: unknown): PageState {
  if (!error) return 'loading';
  if (error instanceof ApiError && error.status === 404) return 'empty';
  if (error instanceof ApiError && error.status === 403) return 'no-access';
  return 'fault';
}

/** One open task: a Document whose pages are the stage's pages and Details (page-types: "A task open"). Another task
    is another page, opened afresh (its read with it), never this one's reused: a route that keeps its component (the
    tab route, moving from one task to the next) would otherwise show a copy changed while it was away. */
export function TaskPage(props: TaskPageProps) {
  return <OneTask key={props.task} {...props} />;
}

function OneTask({ task, tab, file }: TaskPageProps) {
  const { t, error, retry } = useTask(task, true);
  if (!t) {
    const path = `/task/${task}`;
    return (
      <Document
        className="tv"
        // no name until the task's own arrives: a placeholder would reach the tab title and what a message sends as its page
        title={error ? 'Task' : ''}
        path={path}
        body={null}
        state={stateOf(error)}
        empty={{ title: 'This task is gone' }}
        noAccess={{ text: "You don't have access" }}
        reads={error ? [{ error, refetch: retry }] : undefined}
      />
    );
  }
  return <TaskView key={t.id} t={t} tab={tab} file={file} />;
}

/** A tab's mark (parts-kit.js tab): approved, waits on you, not yet. */
function markOf(x: TaskTab) {
  if (x.mark === 'ok') return <Icon name="check" label="Approved" />;
  if (x.mark === 'ask') return <FlowMark size={10} />;
  if (x.mark === 'none') return <i className="hollow" />;
  return undefined;
}

/** The tab the page shows for the address's `tab`: that tab when the task has it, else the one that waits on the person
    (defaultTab), as the page opens it before the address names it. The page and the chat card beside it read this. */
const resolveTab = (tabs: Tab[], tab: string | undefined): string => (tab && tabs.some((x) => x.slug === tab) ? tab : defaultTab(tabs));

/** What the decision answers on tab `x`: an email tab its email's own OK (`email`), none on an email filed apart (its
    own card shows on the page), else the task's ask that is not an email's (null: an email's OK is approved only
    where the email is on screen). */
const decisionFor = (x: Tab | undefined): { email: string | null } | null => (x?.wait ? null : { email: x?.email ?? null });

/** On the person's own task the box is theirs to write on: never "Message <their own name>". */
const ownTask = (t: Pick<TaskData, 'owner_id'>, me: string | undefined): string | undefined => (me && t.owner_id === me ? 'Add a note' : undefined);

/** Back from an agent's session to the task's comments: the address without `session`, its other words kept. */
function useToComments() {
  const navigate = useNavigate();
  return () => void navigate({ to: '.', search: changeWords(({ session: _, ...rest }) => rest) as never, replace: true });
}

interface TaskConversationProps {
  t: TaskData;
  /** Its own thread (`useOwnThread`). */
  own: boolean;
  /** The agent whose session it shows; none: the comments. */
  session?: string;
  /** Over the comments' box. An agent's session has its run there instead, and `decision`. */
  foot?: ReactNode;
  /** The decision the task waits on and the ❓s for the person: over the box in a session too, so they stay in reach. */
  decision?: ReactNode;
  canPost?: boolean;
  extra?: ChatMessage[];
  card?: (m: ChatMessage) => ReactNode;
}

/** The task's chat: its comments, each a log entry that reaches only whom it names, with Reply in place on each; or one
    agent's session (`?session=`), the normal chat bar to that agent, its reply being written, its run while it waits.
    A task filed in its owner's main chat: that chat, as it is. */
function TaskConversation({ t, own, session, foot, decision, canPost, extra, card }: TaskConversationProps) {
  const me = useQuery(meQuery).data?.id;
  const owner = useActor(t.owner_id);
  const agent = useActor(session);
  if (!t.conversation_id) return null;
  if (own && session)
    return (
      <Conversation
        key={session}
        id={t.conversation_id}
        variant="task"
        title={agent?.name ?? agentWords(session)}
        actor={session}
        view={{ session }}
        mentionFirst={onTask(t)}
        task={{ id: t.id, to: session }}
        canSend={t.can_direct}
        canPost={canPost}
        foot={
          <>
            <SessionRun t={t} agent={session} />
            {decision}
          </>
        }
        extra={extra}
        card={card}
      />
    );
  return (
    <Conversation
      id={t.conversation_id}
      variant="task"
      title={owner?.name}
      actor={t.owner_id ?? undefined}
      compose={own ? 'Add a comment' : ownTask(t, me)}
      {...(own && { view: COMMENTS })}
      mentionFirst={onTask(t)}
      task={{ id: t.id }}
      foot={foot}
      canPost={canPost}
      extra={extra}
      card={card}
    />
  );
}

/** Whom a task's @ list names first: who asked, who it is for, who has it, then its roles' people. */
const onTask = (t: Pick<TaskData, 'requester_id' | 'assignee_id' | 'owner_id' | 'roles'>): string[] => [
  ...new Set([t.requester_id, t.assignee_id, t.owner_id, ...Object.values(t.roles ?? {}).flat()].filter((x): x is string => !!x)),
];

function TaskView({ t, tab, file }: { t: TaskData; tab?: string; file?: string }) {
  const navigate = useNavigate();
  const search = useTaskWords();
  // The ask a card opened it for (`?ask=`): its tab, at the version it pinned, and its Approve.
  const ask = search.ask;
  // Its tab and pin hold while its answer is being sent (that clears `may`) or was refused: whether it can be answered
  // decides only the action.
  const named = ask ? requirementOf(t, ask) : null;
  const { tabs, videos, files, waiting } = useTabs(t, named ?? undefined);
  const own = tabs.filter((x) => !x.wait);
  // The chat's address (`…/chat`, an old link to the chat as the page) is the chat beside it: on a phone it raises the
  // chat (TaskModal) and stays the address, so a reload comes back to it; on a computer the page shows a tab of its own.
  const cards = useCards();
  const chatCard = cards && tab === CHAT;
  const known = tab !== undefined && tab !== CHAT && tabs.some((x) => x.slug === tab);
  const unresolved = !!tab && !known && !chatCard && (!files.isSuccess || !waiting.settled);
  // With no tab in the address, the page that waits on the person opens, and the address names it: the tab row reads
  // the address, and an answer that changes which page waits does not move the person off the page they read. A
  // file's tab is known once the files are read, so nothing is picked before.
  const [first, setFirst] = useState<string | null>(null);
  const me = useQuery(meQuery).data?.id;
  if (first === null && waiting.settled && (files.isSuccess || (!tab && files.isError))) setFirst(defaultTab(tabs));
  // A tab that went (an email's OK answered, its Email tab with it): the page that waits now, never a blank page.
  const opened = resolveTab(tabs, first ?? undefined);
  const on = known ? tab : opened;
  // Closing: the history steps back, and this page names no tab in the meantime; nor once the address no longer opens
  // it (closed while its files were read, the layer still fading): naming its tab then would open it again.
  const router = useRouter();
  const leaving = useRef(false);
  // whether the address still opens this task: not once it closed (its layer still fading) or another took its place
  const stillOpen = useCallback(() => {
    const open = openOf(router.latestLocation.search as Record<string, unknown>)?.task;
    return !leaving.current && (open === String(t.number) || open === t.id);
  }, [router, t.id, t.number]);
  // A video had a tab of its own before the pictures and videos led the task: an old link to it (`…/beach-cut-mp4?v=2`)
  // opens that video over the page that opens, at the version it named. Its slug is the one it had then (layoutOf),
  // never a picture's, never another file's that has its name.
  const video = tab ? videos.get(tab) : undefined;
  useEffect(() => {
    if (known || chatCard || unresolved || first === null || leaving.current) return;
    const open = openOf(router.latestLocation.search as Record<string, unknown>)?.task;
    if (open !== String(t.number) && open !== t.id) return;
    // The address's other words (a version, `v`) stay while the tab is named, in place: never read again as an old
    // address, whose `v` with a file open was the file's. Only an old video link's `v` becomes the video's (`fv`).
    const words = video ? ({ v, vs: _vs, ...rest }: TaskWords) => ({ ...rest, tab: opened, file: video.id, ...(v ? { fv: v } : {}) }) : (prev: TaskWords) => ({ ...prev, tab: opened });
    void navigate({ to: router.latestLocation.pathname, search: changeWords(words) as never, replace: true });
  }, [known, chatCard, unresolved, first, opened, t, navigate, router, video]);
  /* Answered: while another ask on this task waits on the person, its page opens; while only a question to them does,
     the task stays as it is; once nothing does, the task below it in the list it was opened from opens (in its place:
     one Back still closes it); at the list's end it closes, back to the page under it. An answer that lands once the
     task has closed or another is open does none of this. */
  const client = useQueryClient();
  const list = search.list ?? NEEDS_YOU;
  const close = useCallback(() => {
    leaving.current = true;
    closeTask(router);
  }, [router]);
  const [seek, setSeek] = useState(0);
  const sought = useRef(0);
  useEffect(
    () =>
      onAnswered((given) => {
        if (!given || !answersHere(t, given.task) || !stillOpen()) return;
        const waits = waitsNow(client, t.id, me);
        if (waits === 'ask') return setSeek((n) => n + 1);
        if (waits) return;
        // Needs you, as read when the person answered, has them do more here (paid shots to reconcile, say): it stays
        if (given?.task === t.id && given.more) return;
        const next = nextInList(t.id, list);
        if (!next) return close();
        leaving.current = true;
        void navigate({ to: '/task/$task', params: { task: next } });
      }),
    [t, me, client, close, navigate, list, stillOpen],
  );
  const asks = tabs.find((x) => x.mark === 'ask')?.slug;
  useEffect(() => {
    if (seek === sought.current || !stillOpen()) return;
    // Opened for one ask (`?ask=`): once it is answered, the next one still waiting opens, its tab (the same one too):
    // a page or a file for its own ask, at its own pinned version; an email on its Email tab. The thread beside it
    // stays in the address; the answered ask and the version the last page was opened at go.
    if (ask && named && askOf(t, ask)) return; // the answer to it is not read back yet
    const next = ask && named ? decisionOf(t) : null;
    if (next) {
      sought.current = seek;
      const send = emailOf(next);
      const target = targetOf(next);
      const slug = send
        ? tabs.find((x) => x.email === send && !x.wait)?.slug
        : tabsOf(t, files.data, next).find((x) => (x.doc?.file_id ?? x.file?.id ?? x.other) === target?.id)?.slug;
      const search = (s: Record<string, unknown>) => {
        const { ask: _a, v: _v, vs: _vs, ...kept } = s;
        return send ? kept : { ...kept, ask: next.requirement_id };
      };
      void navigate({ to: tabAddress(t, slug) as never, search: search as never });
      return;
    }
    if (!asks) return;
    sought.current = seek;
    if (asks !== on) void navigate({ to: tabAddress(t, asks) as never });
  }, [seek, asks, on, t, navigate, ask, named, files.data, tabs, stillOpen]);
  const [adding, setAdding] = useState<Adding | null>(null);

  const current = unresolved ? undefined : tabs.find((x) => x.slug === on);
  const versions = useVersions(current?.doc, search.v, search.vs, current?.version);
  // The change stepped to, from Changes' two arrows; a new page, version or comparison starts before the first.
  const [step, setStep] = useState({ key: '', at: -1 });
  const key = `${on}:${versions?.shown ?? ''}:${versions?.on ? versions.before : ''}`;
  const at = step.key === key ? step.at : -1;
  const onAt = (n: number) => setStep({ key, at: n });
  const address = tabAddress(t, on);
  const details = !unresolved && on === DETAILS;
  // An approval's view (plan/approvals.html): the title, one quiet line and the thing's tabs; its facts are under ⋯.
  // The email whose tab is open (not one filed apart): its own OK is the decision, never another ask; off email tabs,
  // never an email's OK (reading its preview counts as showing it).
  const pick = decisionFor(current);
  const asked = pick && (decisionOf(t, pick.email) ?? changesAskedOf(t, me, pick.email));
  const quiet = !details && !!asked;
  const send = emailOf(asked);
  const rename = async (title: string) => {
    const r = await run('task.rename', { task: t.id, title }, { via: 'click' });
    if (!r.ok) toast(failedToast(r));
  };

  const ownThread = useThreadRead(t).own;
  // one read mark per task: kept as the page opens, then the task is marked read; its sessions and runs kept current
  useTaskReadMark(t, ownThread);
  useTaskLive(t, ownThread);
  const moreMenu = <MoreMenu t={t} details={quiet} />;

  const askTarget = targetOf(named);
  const pageTabs: PageTab[] = tabs
    .filter((x) => !x.chat && !(quiet && x.slug === DETAILS))
    .map((x) => {
      // The tab of the ask the address names keeps it (`?ask=`), so going back to it is still that ask.
      const mine = !!askTarget && (x.doc?.file_id ?? x.file?.id ?? x.other) === askTarget.id;
      // an agent's session open in the chat stays open from tab to tab
      const kept = { ...(mine && ask && { ask }), ...(search.session && { session: search.session }) };
      return { label: x.label, to: tabAddress(t, x.slug), ...(Object.keys(kept).length && { search: kept }), on: x.slug === on, mark: markOf(x), target: `task-tab:${x.slug}` };
    });

  const body =
    unresolved ? (
      files.isError ? (
        <ReadFault what="This file" reads={[files]} />
      ) : (
        <PageBones />
      )
    ) : details ? (
      <Details t={t} adding={adding} onAdding={setAdding} />
    ) : versions ? (
      <PageView task={t.id} vs={versions} at={at} />
    ) : current?.other ? (
      <div className="frame fv">
        <FilePreview file={current.other} version={search.v ?? current.version} />
      </div>
    ) : current?.file ? (
      <FileTab task={t.id} f={current.file} version={search.v ?? current.version} />
    ) : current?.wait && current.email ? (
      <EmailView
        task={current.wait.id}
        send={current.email}
        part={search.part}
        at={address}
        lead={
          <div className="tv-lead">
            <Brief note={t.note} />
            <MatterDates matter={t.matter} />
            {!cards && <Decision t={current.wait} />}
          </div>
        }
      />
    ) : current?.email ? (
      <EmailView task={t.id} send={current.email} part={search.part} at={address} lead={quiet ? <MatterBrief t={t} send={current.email} /> : <MatterDatesOf t={t} send={current.email} />} />
    ) : null;

  // a file from Details or the pictures at the top opens one layer up; Esc or its Close takes it away, back to the tab
  const fileName = file ? files.data?.find((x) => x.id === file) : undefined;
  const closeFile = () =>
    void navigate({ to: '.', search: changeWords(({ file: _f, full: _p, fv: _v, fvs: _vs, ...rest }) => rest) as never, replace: true });
  const layer = file && !unresolved && (
    <Viewer open onClose={closeFile} title={fileName?.title || fileName?.name || 'File'}>
      <div className="tv tv-layer">
        <FileView t={t} file={file} full={search.full} back={current?.label ?? 'Details'} onClose={closeFile} />
      </div>
    </Viewer>
  );

  return (
    <>
    <Document
      className={['tv', details && 'is-details', quiet && 'is-quiet'].filter(Boolean).join(' ')}
      title={quiet ? (sendTitle(t.title)?.subject ?? t.title) : t.title}
      path={address}
      sub={
        <>
          {quiet ? <QuietLine t={t}>{send && <EmailSource t={t} send={send} />}</QuietLine> : <WhereLine t={t} />}
          <BlockedOn t={t} />
        </>
      }
      // An approval's title is read, not renamed (the mock draws it plain): Details, under ⋯, renames it.
      onRename={quiet ? undefined : (title) => void rename(title)}
      more={moreMenu}
      onBack={close}
      tabs={pageTabs}
      tabsAfter={
        <>
          {details && !file ? <AddMenu onAdding={setAdding} /> : versions && <VersionBar task={t.id} vs={versions} at={at} onAt={onAt} />}
          <ArchiveMenu t={t} />
        </>
      }
      framed
      body={
        <div className="tv-view">
          {/* What the task made comes first: its pictures and videos, over whichever tab is open. */}
          {!unresolved && <TaskMedia files={files.data} />}
          {/* An email's OK filed apart from the matter it answers waits on the person here too, at the top. */}
          {!quiet && !current?.wait && <WaitingOnYou items={tabs.flatMap((x) => (x.wait && x.email ? [{ sub: x.wait, send: x.email, to: tabAddress(t, x.slug) }] : []))} />}
          {/* An email's OK on the task itself waits on the person too: off its tab, a pointer to it, never Approve. */}
          {!current?.email && <EmailPointers t={t} items={own.flatMap((x) => (x.email && x.mark === 'ask' ? [{ send: x.email, to: tabAddress(t, x.slug) }] : []))} />}
          {body}
          {/* Docked: a phone's dock at the bottom of the task, on either tab (its chat then has none); on a computer the
              chat beside it holds the decision. An email's OK filed apart docks on a phone too, in place of its lead's. */}
          {pick && <Decision key={pick.email ?? ''} t={t} dock email={pick.email} requirement={ask} />}
          {cards && current?.wait && <Decision t={current.wait} dock />}
        </div>
      }
    />
    {layer}
    </>
  );
}

export interface TaskChatProps {
  /** The address's task: its number, or its id. */
  task: string;
}

/**
 * The task's chat, the task modal's middle column: its owner's face and name (or the agent whose session is open), then
 * the conversation with the answer card over its message box.
 */
export function TaskChat({ task }: TaskChatProps) {
  const { t } = useTask(task);
  // An agent's session open (`?session=`): its face and name head the chat, and ✕ goes back to the comments.
  const open = useTaskWords().session;
  const toComments = useToComments();
  const room = useThreadRead(t);
  const own = room.own;
  const session = own ? open : undefined;
  const who = session ?? t?.owner_id;
  const owner = useActor(who);
  const me = useQuery(meQuery).data?.id;
  const [asking, setAsking] = useState(false);
  // On a phone the task page docks the one decision above the bar, in reach with the chat up, so its chat has none.
  const cards = useCards();
  const decision = t && !cards && <ChatDecision key={t.id} t={t} onAsking={setAsking} />;
  // An email's OK hung on this task waits on the person: a card in its chat, pointing to the email.
  const approvals = useApprovalCards({ task: t });
  // Its conversation is the task's own, or (a task filed in its owner's room) the owner's main chat: the head says which.
  if (!t) return <MessageBones />;
  const name = owner?.name ?? (who?.startsWith('bot:') ? agentWords(who) : 'Person');
  const place: Session | null = own === undefined ? null : own ? { task: t.id, title: t.title } : 'main';
  const sub = place && <SessionLine session={place} />;
  const asked = questionsFor(t, me);
  return (
    <>
      <header className="side-head">
        {/* The face and the name go to the owner's page; one link is read out, the face's is for the pointer. */}
        {owner ? (
          <>
            <Link className="head-face" href={whoHref(owner)} tabIndex={-1} aria-hidden>
              <Face id={who} size={28} decorative />
            </Link>
            <span className="head-who">
              <b>
                <Link className="head-name" href={whoHref(owner)}>
                  {name}
                </Link>
              </b>
              {sub}
            </span>
          </>
        ) : (
          <>
            <Face id={who} size={28} decorative />
            <span className="head-who">
              <b>{name}</b>
              {sub}
            </span>
          </>
        )}
        <span className="grow" />
        {session && <IconButton icon="close" label="Back to comments" size="sm" onClick={toComments} />}
      </header>
      {t.conversation_id && room.failed ? (
        <ChatNotRead read={room.room} />
      ) : t.conversation_id && own === undefined ? (
        <MessageBones />
      ) : t.conversation_id ? (
        <TaskConversation
          t={t}
          own={!!own}
          session={session}
          foot={
            <>
              {/* a pinned ❓ already says it needs them: the status line would say it again */}
              {!asked.length && <TaskStatus t={t} />}
              {decision}
              <Questions t={t} ids={asked} />
            </>
          }
          decision={
            <>
              {decision}
              <Questions t={t} ids={asked} />
            </>
          }
          canPost={!asking}
          extra={approvals}
          card={approvalCard}
        />
      ) : (
        <>
          <TaskStatus t={t} />
          {decision}
        </>
      )}
    </>
  );
}

/** Each ❓ on the task waiting on the person, pinned over the message box: a Question card (the question, the answers
    the agent offers, a reply box) and who asked, as their Needs you has it (the agent the server holds for the ❓; a
    snoozed one's from the snoozed list), else the task's owner. Their reply clears it on the server, whose `question`
    change reads the task again (live/patch.ts), and the pin goes. Several scroll in a bounded stack, so the message box
    stays in reach. */
function Questions({ t, ids }: { t: TaskData; ids: string[] }) {
  // Needs you, which the chat's approval cards read already (ThreadCards useApprovalCards): one read for both. A ❓ the
  // person snoozed still waits on the task but leaves that list: only then its own short list is read.
  const asks = useQuery({ ...flowQuery, enabled: ids.length > 0 }).data?.asks;
  const snoozed = !!asks && ids.some((id) => !asks.some((a) => a.id === id));
  const later = useQuery({ ...flowSnoozedAsksQuery, enabled: snoozed }).data?.items;
  const by = (id: string) => asks?.find((a) => a.id === id)?.by ?? later?.find((it) => it.id === id)?.from_id ?? t.owner_id;
  const asked = (t.waits ?? []).filter((w) => !!w.requirement_id && ids.includes(w.requirement_id));
  if (!asked.length) return null;
  return (
    <div className="chat-pins">
      {asked.map((w) => (
        <QuestionCard key={w.requirement_id} id={w.requirement_id ?? ''} task={t.id} line={w.line} choices={w.choices} who={by(w.requirement_id ?? '')} />
      ))}
    </div>
  );
}

/** The chat card beside the page: the decision for the tab the page has open, read from the same tab list
    (`useTabs`, `decisionFor`). Which tab a slug is needs the files and the emails filed apart read: no card until then,
    never the wrong one. */
function ChatDecision({ t, onAsking }: { t: TaskData; onAsking: (asking: boolean) => void }) {
  const { ask, tab } = useTaskWords();
  const { tabs, settled } = useTabs(t, (ask && requirementOf(t, ask)) || undefined);
  if (!settled) return null;
  const pick = decisionFor(tabs.find((x) => x.slug === resolveTab(tabs, tab)));
  return pick && <Decision key={pick.email ?? ''} t={t} onAsking={onAsking} email={pick.email} requirement={ask} />;
}
