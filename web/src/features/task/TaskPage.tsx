import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useRouter, useRouterState, useSearch } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
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
import { whoHref } from '@/features/team/routing';
import { leaveTask } from '@/lib';
import { onAnswered } from './answered';
import { Decision } from './Answer';
import { AddMenu, Details, type Adding } from './Details';
import type { TaskSearch } from './exports';
import { EmailSource, Email as EmailView } from './Email';
import { ArchiveMenu } from './Files';
import { FileTab, FileView, PageBones } from './FileView';
import { FlowTop, MoreMenu, QuietLine, WhereLine } from './Head';
import { askOf, changesAskedOf, decisionOf, defaultTab, DETAILS, emailOf, failedToast, requirementOf, sendTitle, tabAddress, tabsOf, targetOf, type TaskTab } from './model';
import { FlowMark } from '@/kit/FlowMark';
import { Face } from './parts';
import { taskDetailQuery, taskRefQuery, type TaskData } from './queries';
import { useTabs, type Tab } from './tabs';
import { TaskStatus } from './StatusLine';
import { approvalCard, useApprovalCards } from './ThreadCards';
import { PageView, useVersions, VersionBar } from './Versions';
import { Brief, EmailPointers, MatterBrief, MatterDatesOf, WaitingOnYou } from './Waiting';
import { waitsNow } from './waits';
import './task.css';

/** How often an open task reads itself again, as a backstop for the stream. */
const STATUS_BACKSTOP_MS = 5 * 60_000;

export interface TaskPageProps {
  /** The address's task: its number, or its id. */
  task: string;
  /** The address's pipeline (`/tasks/<pipeline>/<task>`); Flow has none. */
  pipeline?: string;
  /** The open tab's slug; none opens the page that waits on the person, and the address then names it. */
  tab?: string;
  /** A file from Details, open in the page sheet. */
  file?: string;
  /** Inside Flow: Flow's previous and next, and the next task opens once this one is answered. */
  flow?: boolean;
}

/** The address's task, read once and kept current by the stream while it is open. */
function useTask(param: string) {
  const ref = useQuery(taskRefQuery(param));
  // On this screen only, the task reads itself again every few minutes: the stream sends its status line as it changes,
  // and this heals one it missed (a backstop, not the way it updates).
  const detail = useQuery({ ...taskDetailQuery(ref.data), refetchInterval: STATUS_BACKSTOP_MS });
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

/** One open task: a Document whose pages are the stage's pages and Details (page-types: "A task open"). */
export function TaskPage({ task, pipeline, tab, file, flow = false }: TaskPageProps) {
  const { t, error, retry } = useTask(task);
  if (!t) {
    const path = flow ? `/flow/${task}` : `/tasks/${pipeline ?? ''}/${task}`;
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
  return <TaskView key={t.id} t={t} tab={tab} file={file} flow={flow} />;
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

function TaskView({ t, tab, file, flow }: { t: TaskData; tab?: string; file?: string; flow: boolean }) {
  const navigate = useNavigate();
  // The ask a card opened it for (`?ask=`): its tab, at the version it pinned, and its Approve.
  const ask = (useSearch({ strict: false }) as TaskSearch).ask;
  // Its tab and pin hold while its answer is being sent (that clears `may`) or was refused: whether it can be answered
  // decides only the action.
  const named = ask ? requirementOf(t, ask) : null;
  const { tabs, files, waiting } = useTabs(t, named ?? undefined);
  const own = tabs.filter((x) => !x.wait);
  const known = tab !== undefined && tabs.some((x) => x.slug === tab);
  const unresolved = !!tab && !known && (!files.isSuccess || !waiting.settled);
  // With no tab in the address, the page that waits on the person opens, and the address names it: the tab row reads
  // the address, and an answer that changes which page waits does not move the person off the page they read. A
  // file's tab is known once the files are read, so nothing is picked before.
  const [first, setFirst] = useState<string | null>(null);
  if (first === null && waiting.settled && (files.isSuccess || (!tab && files.isError))) setFirst(defaultTab(tabs));
  // A tab that went (an email's OK answered, its Email tab with it): the page that waits now, never a blank page.
  const opened = resolveTab(tabs, first ?? undefined);
  const on = known ? tab : opened;
  // Closing: the history steps back, and this page names no tab in the meantime.
  const leaving = useRef(false);
  useEffect(() => {
    if (known || unresolved || first === null || leaving.current) return;
    const a = tabAddress(t, opened, flow);
    // The address's other words (a version, `v`) stay while the tab is named.
    void navigate({ to: a.to as never, search: ((prev: TaskSearch) => ({ ...prev, ...a.search })) as never, replace: true });
  }, [known, unresolved, first, opened, flow, t, navigate]);
  /* Answered outside Flow (Flow opens its next item itself): while something else on this task waits on the person,
     its page opens; once nothing does, the task closes, back to the board, list or page it was opened from. */
  const router = useRouter();
  const client = useQueryClient();
  const [seek, setSeek] = useState(0);
  const sought = useRef(0);
  useEffect(() => {
    if (flow) return;
    return onAnswered(() => {
      if (waitsNow(client, t.id)) return setSeek((n) => n + 1);
      leaving.current = true;
      leaveTask(router);
    });
  }, [flow, t.id, client, router]);
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
      void navigate({ to: tabAddress(t, slug, false).to as never, search: search as never });
      return;
    }
    if (!asks) return;
    sought.current = seek;
    if (asks !== on) void navigate({ to: tabAddress(t, asks, false).to as never });
  }, [seek, asks, on, t, navigate, ask, named, files.data, tabs]);
  const [adding, setAdding] = useState<Adding | null>(null);
  const [asking, setAsking] = useState(false);
  const search = useSearch({ strict: false }) as TaskSearch;
  const me = useQuery(meQuery).data?.id;
  const owner = useActor(t.owner_id);

  const current = unresolved ? undefined : tabs.find((x) => x.slug === on);
  const versions = useVersions(current?.doc, search.v, search.vs, current?.version);
  // The change stepped to, from Changes' two arrows; a new page, version or comparison starts before the first.
  const [step, setStep] = useState({ key: '', at: -1 });
  const key = `${on}:${versions?.shown ?? ''}:${versions?.on ? versions.before : ''}`;
  const at = step.key === key ? step.at : -1;
  const onAt = (n: number) => setStep({ key, at: n });
  const address = tabAddress(t, on, flow);
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

  const cards = useCards();
  const beside = !!new URLSearchParams(useRouterState({ select: (s) => s.location.searchStr })).get('chat');
  // A phone's Chat tab: an email's OK hung on this task, as a card in the chat.
  const approvals = useApprovalCards({ task: current?.chat ? t : undefined });
  const moreMenu = <MoreMenu t={t} flow={flow} details={quiet} />;

  const askTarget = targetOf(named);
  const pageTabs: PageTab[] = tabs
    .filter((x) => !(quiet && x.slug === DETAILS))
    .map((x) => {
      const a = tabAddress(t, x.slug, flow);
      // The tab of the ask the address names keeps it (`?ask=`), so going back to it is still that ask.
      const mine = !!askTarget && (x.doc?.file_id ?? x.file?.id ?? x.other) === askTarget.id;
      const search = { ...(flow && { tab: x.slug }), ...(mine && ask && { ask }) };
      return { label: x.label, to: a.to, ...(Object.keys(search).length > 0 && { search }), mark: markOf(x), target: `task-tab:${x.slug}`, ...(x.chat && { className: 'tab-chat' }) };
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
      <FileView t={t} file={file} flow={flow} full={search.full} />
    ) : details ? (
      <Details t={t} flow={flow} adding={adding} onAdding={setAdding} />
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
            <MatterDatesOf t={t} send={current.email} />
            <Decision t={current.wait} flow={false} />
          </div>
        }
      />
    ) : current?.email ? (
      <EmailView task={t.id} send={current.email} part={search.part} at={address} lead={quiet ? <MatterBrief t={t} send={current.email} /> : <MatterDatesOf t={t} send={current.email} />} />
    ) : current?.chat && t.conversation_id ? (
      // A phone's Chat tab: the decision sits over its message box, and stands in for it while changes are asked.
      <Conversation
        id={t.conversation_id}
        variant="page"
        title={owner?.name}
        actor={t.owner_id ?? undefined}
        foot={
          <>
            <TaskStatus t={t} />
            <Decision t={t} flow={flow} onAsking={setAsking} email={null} />
          </>
        }
        canPost={!asking}
        extra={approvals}
        card={approvalCard}
      />
    ) : null;

  return (
    <Document
      className={['tv', details && 'is-details', flow && 'is-flow', quiet && 'is-quiet'].filter(Boolean).join(' ')}
      title={quiet ? (sendTitle(t.title)?.subject ?? t.title) : t.title}
      path={address.to}
      sub={quiet ? <QuietLine t={t}>{send && <EmailSource t={t} send={send} />}</QuietLine> : <WhereLine t={t} />}
      // An approval's title is read, not renamed (the mock draws it plain): Details, under ⋯, renames it.
      onRename={quiet ? undefined : (title) => void rename(title)}
      // A phone in Flow keeps "⋯" in the row over the head, beside the steps (more.js taskflow).
      top={flow ? <FlowTop t={t} more={cards ? moreMenu : undefined} /> : undefined}
      more={flow && cards ? undefined : moreMenu}
      tabs={pageTabs}
      tabsAfter={
        <>
          {details && !file ? <AddMenu onAdding={setAdding} /> : versions && <VersionBar task={t.id} vs={versions} at={at} onAt={onAt} />}
          <ArchiveMenu t={t} flow={flow} />
          <IconButton icon="expand" label="Expand" size="sm" className="tv-expand" onClick={expand} />
        </>
      }
      framed
      body={
        <div className="tv-view">
          {/* An email's OK filed apart from the matter it answers waits on the person here too, at the top. */}
          {!quiet && !current?.wait && <WaitingOnYou items={tabs.flatMap((x) => (x.wait && x.email ? [{ sub: x.wait, send: x.email, ...tabAddress(t, x.slug, flow) }] : []))} />}
          {/* An email's OK on the task itself waits on the person too: off its tab, a pointer to it, never Approve. */}
          {!current?.email && <EmailPointers t={t} items={own.flatMap((x) => (x.email && x.mark === 'ask' ? [{ send: x.email, ...tabAddress(t, x.slug, flow) }] : []))} />}
          {body}
          {/* Docked (a phone's bottom, beside the chat sheet's own card on a computer), or in the page when the thread a
              card was opened from holds the chat sheet (`?chat=`): Approve is always somewhere. */}
          {!current?.chat && pick && <Decision key={pick.email ?? ''} t={t} flow={flow} dock={cards || !beside} email={pick.email} requirement={ask} />}
        </div>
      }
    />
  );
}

export interface TaskChatProps {
  /** The address's task: its number, or its id. */
  task: string;
  flow?: boolean;
}

/**
 * The task's chat, for the second chat sheet of the 'task' arrangement: its owner's face and name, then the
 * conversation with the answer card over its message box. The root wraps it in the chat sheet.
 */
export function TaskChat({ task, flow = false }: TaskChatProps) {
  const { t } = useTask(task);
  const owner = useActor(t?.owner_id);
  const [asking, setAsking] = useState(false);
  const decision = t && <ChatDecision key={t.id} t={t} flow={flow} onAsking={setAsking} />;
  // An email's OK hung on this task waits on the person: a card in its chat, pointing to the email.
  const approvals = useApprovalCards({ task: t });
  // Its conversation is the task's own, or (a task filed in its owner's room) the owner's main chat: the head says which.
  const room = useQuery({ ...threadQuery(t?.conversation_id ?? ''), enabled: !!t?.conversation_id });
  if (!t) return <MessageBones />;
  const name = owner?.name ?? (t.owner_id?.startsWith('bot:') ? 'Agent' : 'Person');
  const session: Session | null = !room.data ? null : room.data.task_id === t.id ? { task: t.id, title: t.title } : 'main';
  const sub = session && <SessionLine session={session} />;
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
          foot={
            <>
              <TaskStatus t={t} />
              {decision}
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

/** The chat card beside the page: the decision for the tab the page has open, read from the same tab list
    (`useTabs`, `decisionFor`). Which tab a slug is needs the files and the emails filed apart read: no card until then,
    never the wrong one. */
function ChatDecision({ t, flow, onAsking }: { t: TaskData; flow: boolean; onAsking: (asking: boolean) => void }) {
  const location = useRouterState({ select: (s) => s.location });
  const ask = (location.search as TaskSearch).ask;
  const { tabs, settled } = useTabs(t, (ask && requirementOf(t, ask)) || undefined);
  const tab = flow ? (location.search as TaskSearch).tab : location.pathname.split('/')[4];
  if (!settled) return null;
  const pick = decisionFor(tabs.find((x) => x.slug === resolveTab(tabs, tab && decoded(tab))));
  return pick && <Decision key={pick.email ?? ''} t={t} flow={flow} onAsking={onAsking} email={pick.email} requirement={ask} />;
}

/** `/task/<n>`: the task by its number alone. Once its pipeline is known the address becomes its own, replaced. */
export function TaskById({ task }: { task: string }) {
  const { t } = useTask(task);
  const navigate = useNavigate();
  useEffect(() => {
    if (!t) return;
    const a = tabAddress(t, undefined, false);
    void navigate({ to: a.to as never, replace: true });
  }, [t, navigate]);
  return t ? null : <TaskPage task={task} />;
}
