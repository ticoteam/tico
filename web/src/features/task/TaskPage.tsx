import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useRouter, useRouterState, useSearch } from '@tanstack/react-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { run } from '@/actions';
import { ApiError } from '@/api/client';
import { Conversation, MessageBones, SessionLine, type Session } from '@/conversation';
import { meQuery, useActor } from '@/data';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { useCards } from '@/kit/Popover';
import { Link } from '@/kit/Link';
import { toast } from '@/kit/Toast';
import { useFollow } from '@/live/follow';
import { Document, type PageState, type PageTab } from '@/pagekinds';
import { FilePreview } from '@/features/library/exports';
import { threadQuery } from '@/features/threads/exports';
import { flowQuery, flowSnoozedAsksQuery } from '@/features/tasks/routing';
import { whoHref } from '@/features/team/routing';
import { leaveTask } from '@/lib';
import { onAnswered } from './answered';
import { Decision } from './Answer';
import { AskCard } from './AskCard';
import { AddMenu, Details, type Adding } from './Details';
import type { TaskSearch } from './exports';
import { EmailSource, Email as EmailView } from './Email';
import { BlockedOn } from './Facts';
import { ArchiveMenu } from './Files';
import { FileTab, FileView, PageBones } from './FileView';
import { MoreMenu, QuietLine, WhereLine } from './Head';
import { askOf, CHAT, changesAskedOf, decisionOf, defaultTab, DETAILS, emailOf, failedToast, requirementOf, sendTitle, tabAddress, tabsOf, targetOf, type TaskTab } from './model';
import { FlowMark } from '@/kit/FlowMark';
import { Face } from './parts';
import { taskDetailQuery, taskRefQuery, type TaskData } from './queries';
import { boardOf, ownAddress } from './routing';
import { useTabs, type Tab } from './tabs';
import { TaskStatus } from './StatusLine';
import { approvalCard, useApprovalCards } from './ThreadCards';
import { PageView, useVersions, VersionBar } from './Versions';
import { Brief, EmailPointers, MatterBrief, MatterDates, MatterDatesOf, WaitingOnYou } from './Waiting';
import { questionLine, questionsFor, waitsNow } from './waits';
import './task.css';

/** How often an open task reads itself again, as a backstop for the stream. */
const STATUS_BACKSTOP_MS = 5 * 60_000;

export interface TaskPageProps {
  /** The address's task: its number, or its id. */
  task: string;
  /** The address's pipeline (`/tasks/<pipeline>/<task>`). */
  pipeline?: string;
  /** The open tab's slug; none opens the page that waits on the person, and the address then names it. */
  tab?: string;
  /** A file from Details, open in the page sheet. */
  file?: string;
}

/** The address's task, read once and kept current by the stream while it is open. `opens`: the task's page, which
    reads it afresh as it opens: what changed while it was closed (a ❓ cleared, say) reaches no copy off screen, and a
    later row merged into that copy would make it look fresh. The chat beside it shares that read. */
function useTask(param: string, opens = false) {
  const ref = useQuery(taskRefQuery(param));
  // On this screen only, the task reads itself again every few minutes: the stream sends its status line as it changes,
  // and this heals one it missed (a backstop, not the way it updates).
  const detail = useQuery({ ...taskDetailQuery(ref.data), refetchInterval: STATUS_BACKSTOP_MS, ...(opens && { refetchOnMount: 'always' as const }) });
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

function OneTask({ task, pipeline, tab, file }: TaskPageProps) {
  const { t, error, retry } = useTask(task, true);
  if (!t) {
    const path = `/tasks/${pipeline ?? ''}/${task}`;
    return (
      <Document
        className="tv"
        // no name until the task's own arrives: a placeholder would reach the tab title and what a message sends as its page
        title={error ? 'Task' : ''}
        path={path}
        body={null}
        state={stateOf(error)}
        empty={{ title: 'No such task' }}
        noAccess={{ text: 'This task is private.' }}
        onRetry={retry}
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

/** A path part decoded, or as it is when it is not valid percent-encoding (a bad `%` never throws). */
const decoded = (part: string): string => {
  try {
    return decodeURIComponent(part);
  } catch {
    return part;
  }
};

/** What the decision answers on tab `x`: an email tab its email's own OK (`email`), none on an email filed apart (its
    own card shows on the page), else the task's ask that is not an email's (null: an email's OK is approved only
    where the email is on screen). */
const decisionFor = (x: Tab | undefined): { email: string | null } | null => (x?.wait ? null : { email: x?.email ?? null });

/** On the person's own task the box is theirs to write on: never "Message <their own name>". */
const ownTask = (t: Pick<TaskData, 'owner_id'>, me: string | undefined): string | undefined => (me && t.owner_id === me ? 'Add a note' : undefined);

function TaskView({ t, tab, file }: { t: TaskData; tab?: string; file?: string }) {
  const navigate = useNavigate();
  // The ask a card opened it for (`?ask=`): its tab, at the version it pinned, and its Approve.
  const ask = (useSearch({ strict: false }) as TaskSearch).ask;
  // Its tab and pin hold while its answer is being sent (that clears `may`) or was refused: whether it can be answered
  // decides only the action.
  const named = ask ? requirementOf(t, ask) : null;
  const { tabs, files, waiting } = useTabs(t, named ?? undefined);
  const own = tabs.filter((x) => !x.wait);
  // On a phone the chat's address (`…/chat`) raises its card over the task's first page, and stays the address, so a
  // reload comes back to the chat; on a computer it is the chat as the page.
  const cards = useCards();
  const chatCard = cards && tab === CHAT;
  const known = !chatCard && tab !== undefined && tabs.some((x) => x.slug === tab);
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
  // Closing: the history steps back, and this page names no tab in the meantime.
  const leaving = useRef(false);
  useEffect(() => {
    if (known || chatCard || unresolved || first === null || leaving.current) return;
    // The address's other words (a version, `v`) stay while the tab is named.
    void navigate({ to: tabAddress(t, opened) as never, search: ((prev: TaskSearch) => prev) as never, replace: true });
  }, [known, chatCard, unresolved, first, opened, t, navigate]);
  /* Answered: while another ask on this task waits on the person, its page opens; while only a question to them does,
     the task stays as it is; once nothing does, it closes, back to the board, list or page it was opened from (its
     board, while there is one, when nothing is behind it). */
  const router = useRouter();
  const client = useQueryClient();
  const pipeline = t.pipeline_id;
  const close = useCallback(() => {
    leaving.current = true;
    leaveTask(router, boardOf(client, pipeline));
  }, [router, client, pipeline]);
  const [seek, setSeek] = useState(0);
  const sought = useRef(0);
  useEffect(
    () =>
      onAnswered(() => {
        const waits = waitsNow(client, t.id, me);
        if (waits === 'ask') return setSeek((n) => n + 1);
        if (!waits) close();
      }),
    [t.id, me, client, close],
  );
  const asks = tabs.find((x) => x.mark === 'ask')?.slug;
  useEffect(() => {
    if (seek === sought.current) return;
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
  }, [seek, asks, on, t, navigate, ask, named, files.data, tabs]);
  const [adding, setAdding] = useState<Adding | null>(null);
  const [asking, setAsking] = useState(false);
  const search = useSearch({ strict: false }) as TaskSearch;
  const owner = useActor(t.owner_id);

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
  // The page alone: the sheets beside it fold (the shell's own keys unfold them).
  const expand = () => {
    for (const sheet of ['tico', 'menu', 'list'] as const) void run('shell.fold', { sheet, folded: true }, { via: 'click' });
  };
  const rename = async (title: string) => {
    const r = await run('task.rename', { task: t.id, title }, { via: 'click' });
    if (!r.ok) toast(failedToast(r));
  };

  const beside = !!new URLSearchParams(useRouterState({ select: (s) => s.location.searchStr })).get('chat');
  // The chat as the page (a computer's `…/chat`): an email's OK hung on this task, as a card in the chat.
  const approvals = useApprovalCards({ task: current?.chat ? t : undefined });
  const moreMenu = <MoreMenu t={t} details={quiet} />;

  const askTarget = targetOf(named);
  const pageTabs: PageTab[] = tabs
    .filter((x) => !x.chat && !(quiet && x.slug === DETAILS))
    .map((x) => {
      // The tab of the ask the address names keeps it (`?ask=`), so going back to it is still that ask.
      const mine = !!askTarget && (x.doc?.file_id ?? x.file?.id ?? x.other) === askTarget.id;
      return { label: x.label, to: tabAddress(t, x.slug), ...(mine && ask && { search: { ask } }), mark: markOf(x), target: `task-tab:${x.slug}` };
    });

  const body =
    unresolved ? (
      files.isError ? (
        <Empty icon="alert" title="This file did not load">
          <Button onClick={() => files.refetch()}>Try again</Button>
        </Empty>
      ) : (
        <PageBones />
      )
    ) : file && details ? (
      <FileView t={t} file={file} full={search.full} />
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
    ) : current?.chat && t.conversation_id ? (
      // The chat as the page: the decision sits over its message box, and stands in for it while changes are asked.
      <Conversation
        id={t.conversation_id}
        variant="page"
        title={owner?.name}
        actor={t.owner_id ?? undefined}
        compose={ownTask(t, me)}
        foot={
          <>
            <TaskStatus t={t} />
            <Decision t={t} onAsking={setAsking} email={null} />
          </>
        }
        canPost={!asking}
        extra={approvals}
        card={approvalCard}
      />
    ) : null;

  return (
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
          <IconButton icon="expand" label="Expand" size="sm" className="tv-expand" onClick={expand} />
        </>
      }
      framed
      body={
        <div className="tv-view">
          {/* An email's OK filed apart from the matter it answers waits on the person here too, at the top. */}
          {!quiet && !current?.wait && <WaitingOnYou items={tabs.flatMap((x) => (x.wait && x.email ? [{ sub: x.wait, send: x.email, to: tabAddress(t, x.slug) }] : []))} />}
          {/* An email's OK on the task itself waits on the person too: off its tab, a pointer to it, never Approve. */}
          {!current?.email && <EmailPointers t={t} items={own.flatMap((x) => (x.email && x.mark === 'ask' ? [{ send: x.email, to: tabAddress(t, x.slug) }] : []))} />}
          {body}
          {/* Docked (a phone's dock above its bar, on every tab and over the chat card, whose chat then has none; beside
              the chat sheet's own card on a computer), or in the page when the thread a card was opened from holds the
              chat sheet (`?chat=`): Approve is always somewhere. An email's OK filed apart docks on a phone too, in
              place of its lead's. */}
          {!current?.chat && pick && <Decision key={pick.email ?? ''} t={t} dock={cards || !beside} email={pick.email} requirement={ask} />}
          {cards && current?.wait && <Decision t={current.wait} dock />}
        </div>
      }
    />
  );
}

export interface TaskChatProps {
  /** The address's task: its number, or its id. */
  task: string;
}

/**
 * The task's chat, for the second chat sheet of the 'task' arrangement: its owner's face and name, then the
 * conversation with the answer card over its message box. The root wraps it in the chat sheet.
 */
export function TaskChat({ task }: TaskChatProps) {
  const { t } = useTask(task);
  const owner = useActor(t?.owner_id);
  const me = useQuery(meQuery).data?.id;
  const [asking, setAsking] = useState(false);
  // On a phone the task page docks the one decision above the bar, in reach with the chat up, so its chat has none.
  const cards = useCards();
  const decision = t && !cards && <ChatDecision key={t.id} t={t} onAsking={setAsking} />;
  // An email's OK hung on this task waits on the person: a card in its chat, pointing to the email.
  const approvals = useApprovalCards({ task: t });
  // Its conversation is the task's own, or (a task filed in its owner's room) the owner's main chat: the head says which.
  const room = useQuery({ ...threadQuery(t?.conversation_id ?? ''), enabled: !!t?.conversation_id });
  if (!t) return <MessageBones />;
  const name = owner?.name ?? (t.owner_id?.startsWith('bot:') ? 'Agent' : 'Person');
  const session: Session | null = !room.data ? null : room.data.task_id === t.id ? { task: t.id, title: t.title } : 'main';
  const sub = session && <SessionLine session={session} />;
  const asked = questionsFor(t, me);
  return (
    <>
      <header className="side-head">
        {/* The face and the name go to the owner's page; one link is read out, the face's is for the pointer. */}
        {owner ? (
          <>
            <Link className="head-face" href={whoHref(owner)} tabIndex={-1} aria-hidden>
              <Face id={t.owner_id} size={28} decorative />
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
            <Face id={t.owner_id} size={28} decorative />
            <span className="head-who">
              <b>{name}</b>
              {sub}
            </span>
          </>
        )}
        <span className="grow" />
      </header>
      {t.conversation_id ? (
        <Conversation
          id={t.conversation_id}
          variant="task"
          title={name}
          actor={t.owner_id ?? undefined}
          compose={ownTask(t, me)}
          foot={
            <>
              {/* a pinned ❓ already says it needs them: the status line would say it again */}
              {!asked.length && <TaskStatus t={t} />}
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

/** Each ❓ on the task waiting on the person, pinned over the message box they answer it in: the question, and who
    asked, as their Needs you has it (the agent the server holds for the ❓; a snoozed one's from the snoozed list),
    else the task's owner. Their reply clears it on the server, whose `question` change reads the task again
    (live/patch.ts), and the pin goes. Several scroll in a bounded stack, so the message box stays in reach. */
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
        <AskCard key={w.requirement_id} name="Needs you" label="Needs you" who={by(w.requirement_id ?? '')}>
          <p className="askcard-what">{questionLine(w.line)}</p>
        </AskCard>
      ))}
    </div>
  );
}

/** The chat card beside the page: the decision for the tab the page has open, read from the same tab list
    (`useTabs`, `decisionFor`). Which tab a slug is needs the files and the emails filed apart read: no card until then,
    never the wrong one. */
function ChatDecision({ t, onAsking }: { t: TaskData; onAsking: (asking: boolean) => void }) {
  const location = useRouterState({ select: (s) => s.location });
  const ask = (location.search as TaskSearch).ask;
  const { tabs, settled } = useTabs(t, (ask && requirementOf(t, ask)) || undefined);
  const tab = location.pathname.split('/')[4];
  if (!settled) return null;
  const pick = decisionFor(tabs.find((x) => x.slug === resolveTab(tabs, tab && decoded(tab))));
  return pick && <Decision key={pick.email ?? ''} t={t} onAsking={onAsking} email={pick.email} requirement={ask} />;
}

/** `/task/<n>` when its route could not read the task (Try again read it here): once it is read, the address becomes
    its own, replaced, as the route would have made it (ownAddress). */
export function TaskById({ task }: { task: string }) {
  const { t } = useTask(task);
  const router = useRouter();
  const client = useQueryClient();
  // the address as it was opened: once the task is read it is replaced, once
  const [search] = useState(useRouterState({ select: (s) => s.location.search as Record<string, unknown> }));
  const read = !!t;
  useEffect(() => {
    if (!read) return;
    let on = true;
    const from = router.state.location.href;
    void ownAddress(client, task, search).then((to) => {
      // gone, or the person went elsewhere meanwhile: theirs wins
      if (on && to && router.state.location.href === from) void router.navigate({ ...to, search: to.search as never, replace: true });
    });
    return () => {
      on = false;
    };
  }, [read, client, task, search, router]);
  return t ? null : <TaskPage task={task} />;
}
