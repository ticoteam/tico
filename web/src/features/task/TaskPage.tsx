import { useQuery } from '@tanstack/react-query';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { run } from '@/actions';
import { ApiError } from '@/api/client';
import { Conversation, MessageBones } from '@/conversation';
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
import { whoHref } from '@/features/team/routing';
import { Decision } from './Answer';
import { AddMenu, Details, type Adding } from './Details';
import type { TaskSearch } from './exports';
import { EmailSource, Email as EmailView } from './Email';
import { FileTab, FileView, PageBones } from './FileView';
import { FlowTop, MoreMenu, QuietLine, WhereLine } from './Head';
import { changesAskedOf, decisionOf, defaultTab, DETAILS, emailOf, failedToast, personOf, sendTitle, tabAddress, tabsOf, type TaskTab } from './model';
import { FlowMark } from '@/kit/FlowMark';
import { Face } from './parts';
import { taskDetailQuery, taskFilesQuery, taskRefQuery, type TaskData } from './queries';
import { approvalCard, useApprovalCards } from './ThreadCards';
import { PageView, useVersions, VersionBar } from './Versions';
import { Brief, MatterBrief, useWaiting, WaitingOnYou, type Waiting } from './Waiting';
import { waitSlugs } from './waits';
import './task.css';

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
  const detail = useQuery(taskDetailQuery(ref.data));
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
        title="Task"
        path={path}
        crumb={flow ? [{ label: 'Flow', to: '/flow' }] : pipeline ? [{ label: 'Tasks', to: `/tasks/${pipeline}` }] : undefined}
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

/** A tab of the open task, or an email's OK hung on it (`wait`, its OK task). */
type Tab = TaskTab & { wait?: TaskData };

/** One tab per email's OK that waits on the person, first and marked (one the person asked changes on is not marked): "Email", or "Email to <who>" when there are two. */
function waitTabs(items: Waiting[], own: TaskTab[]): Tab[] {
  const mails = items.flatMap((x) => (x.send ? [{ ...x, send: x.send }] : []));
  const slugs = waitSlugs(mails.length, own.map((x) => x.slug));
  return mails.map(({ sub, send }, i) => {
    const to = sendTitle(sub.title)?.to;
    // Changes asked: the tab stays, not marked as waiting on the person.
    const mark = decisionOf(sub) ? ('ask' as const) : ('none' as const);
    return { slug: slugs[i]!, label: mails.length > 1 && to ? `Email to ${personOf(to)}` : 'Email', mark, email: send, wait: sub };
  });
}

/** A tab's mark (parts-kit.js tab): approved, waits on you, not yet. */
function markOf(x: TaskTab) {
  if (x.mark === 'ok') return <Icon name="check" label="Approved" />;
  if (x.mark === 'ask') return <FlowMark size={10} />;
  if (x.mark === 'none') return <i className="hollow" />;
  return undefined;
}

function TaskView({ t, tab, file, flow }: { t: TaskData; tab?: string; file?: string; flow: boolean }) {
  const navigate = useNavigate();
  const files = useQuery(taskFilesQuery(t.id));
  // An email's OK filed as its own task (a "Send:" sub-task) that waits on the person: the matter opens on it.
  const waiting = useWaiting(t);
  const own = tabsOf(t, files.data);
  const tabs: Tab[] = [...waitTabs(waiting.items, own), ...own];
  const known = tab !== undefined && tabs.some((x) => x.slug === tab);
  const unresolved = !!tab && !known && (!files.isSuccess || !waiting.settled);
  // With no tab in the address, the page that waits on the person opens, and the address names it: the tab row reads
  // the address, and an answer that changes which page waits does not move the person off the page they read. A
  // file's tab is known once the files are read, so nothing is picked before.
  const [first, setFirst] = useState<string | null>(null);
  if (first === null && waiting.settled && (files.isSuccess || (!tab && files.isError))) setFirst(defaultTab(tabs));
  const opened = first ?? defaultTab(tabs);
  const on = known ? tab : opened;
  useEffect(() => {
    if (known || unresolved || first === null) return;
    const a = tabAddress(t, opened, flow);
    void navigate({ to: a.to as never, search: a.search as never, replace: true });
  }, [known, unresolved, first, opened, flow, t, navigate]);
  const [adding, setAdding] = useState<Adding | null>(null);
  const [asking, setAsking] = useState(false);
  const search = useSearch({ strict: false }) as TaskSearch;
  const me = useQuery(meQuery).data?.id;
  const owner = useActor(t.owner_id);

  const current = unresolved ? undefined : tabs.find((x) => x.slug === on);
  const versions = useVersions(current?.doc, search.v, search.vs);
  // The change stepped to, from Changes' two arrows; a new page, version or comparison starts before the first.
  const [step, setStep] = useState({ key: '', at: -1 });
  const key = `${on}:${versions?.shown ?? ''}:${versions?.on ? versions.before : ''}`;
  const at = step.key === key ? step.at : -1;
  const onAt = (n: number) => setStep({ key, at: n });
  const address = tabAddress(t, on, flow);
  const details = !unresolved && on === DETAILS;
  // An approval's view (plan/approvals.html): the title, one quiet line and the thing's tabs; its facts are under ⋯.
  const asked = decisionOf(t) ?? changesAskedOf(t, me);
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
  // A phone's Chat tab: an email's OK hung on this task, as a card in the chat.
  const approvals = useApprovalCards({ task: current?.chat ? t : undefined });
  const moreMenu = <MoreMenu t={t} flow={flow} details={quiet} />;

  const pageTabs: PageTab[] = tabs
    .filter((x) => !(quiet && x.slug === DETAILS))
    .map((x) => {
      const a = tabAddress(t, x.slug, flow);
      return { label: x.label, to: a.to, ...(flow && { search: { tab: x.slug } }), mark: markOf(x), target: `task-tab:${x.slug}`, ...(x.chat && { className: 'tab-chat' }) };
    });

  const body =
    unresolved ? (
      files.isError ? (
        <Empty icon="alert" title="This file did not load">
          <Button onClick={() => void files.refetch()}>Try again</Button>
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
    ) : current?.file ? (
      <FileTab task={t.id} f={current.file} />
    ) : current?.wait && current.email ? (
      <EmailView
        task={current.wait.id}
        send={current.email}
        part={search.part}
        at={address}
        lead={
          <div className="tv-lead">
            <Brief note={t.note} />
            <Decision t={current.wait} flow={false} />
          </div>
        }
      />
    ) : current?.email ? (
      <EmailView task={t.id} send={current.email} part={search.part} at={address} lead={quiet && <MatterBrief t={t} send={current.email} />} />
    ) : current?.chat && t.conversation_id ? (
      // A phone's Chat tab: the decision sits over its message box, and stands in for it while changes are asked.
      <Conversation
        id={t.conversation_id}
        variant="page"
        title={owner?.name ?? 'Teammate'}
        actor={t.owner_id ?? undefined}
        foot={<Decision t={t} flow={flow} onAsking={setAsking} />}
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
      // Inside Flow too the trail is the task's pipeline (more.js where): Flow's own way back is its list, and a phone's row
      // over the head.
      crumb={[{ label: t.pipeline?.name ?? 'Tasks', to: `/tasks/${encodeURIComponent(t.pipeline_id ?? '')}` }]}
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
          <IconButton icon="expand" label="Expand" size="sm" className="tv-expand" onClick={expand} />
        </>
      }
      framed
      body={
        <div className="tv-view">
          {/* An email's OK filed apart from the matter it answers waits on the person here too, at the top. */}
          {!quiet && !current?.wait && <WaitingOnYou items={tabs.flatMap((x) => (x.wait && x.email ? [{ sub: x.wait, send: x.email, to: tabAddress(t, x.slug, flow).to }] : []))} />}
          {body}
          {!current?.chat && <Decision t={t} flow={flow} dock />}
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
  // An email's OK hung on this task waits on the person: a card in its chat, pointing to the email.
  const approvals = useApprovalCards({ task: t });
  if (!t) return <MessageBones />;
  const name = owner?.name ?? 'Teammate';
  return (
    <>
      <header className="side-head">
        {/* The face and the name go to the owner's page; one link is read out, the face's is for the pointer. */}
        {owner ? (
          <>
            <Link className="head-face" href={whoHref(owner)} tabIndex={-1} aria-hidden>
              <Face id={t.owner_id} size={28} decorative />
            </Link>
            <b>
              <Link className="head-name" href={whoHref(owner)}>
                {name}
              </Link>
            </b>
          </>
        ) : (
          <>
            <Face id={t.owner_id} size={28} decorative />
            <b>{name}</b>
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
          foot={<Decision key={t.id} t={t} flow={flow} onAsking={setAsking} />}
          canPost={!asking}
          extra={approvals}
          card={approvalCard}
        />
      ) : (
        <Decision key={t.id} t={t} flow={flow} />
      )}
    </>
  );
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
