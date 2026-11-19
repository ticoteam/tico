import { useQuery } from '@tanstack/react-query';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { run } from '@/actions';
import { ApiError } from '@/api/client';
import { Conversation } from '@/conversation';
import { useActor } from '@/data';
import { Icon } from '@/kit/Icon';
import { toast } from '@/kit/Toast';
import { useFollow } from '@/live/follow';
import { Document, type PageState, type PageTab } from '@/pagekinds';
import { Decision } from './Answer';
import { AddMenu, Details, type Adding } from './Details';
import type { TaskSearch } from './exports';
import { FileView } from './FileView';
import { FlowCover } from './FlowCover';
import { FlowSteps, FlowTop, MoreMenu, WhereLine } from './Head';
import { defaultTab, DETAILS, failedToast, tabAddress, tabsOf, type TaskTab } from './model';
import { Face, FlowMark } from './parts';
import { taskDetailQuery, taskRefQuery, type TaskData } from './queries';
import { PageView, useVersions, VersionBar } from './Versions';
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

/** A tab's mark (parts-kit.js tab): approved, waits on you, not yet. */
function markOf(x: TaskTab) {
  if (x.mark === 'ok') return <Icon name="check" label="Approved" />;
  if (x.mark === 'ask') return <FlowMark size={10} />;
  if (x.mark === 'none') return <i className="hollow" />;
  return undefined;
}

function TaskView({ t, tab, file, flow }: { t: TaskData; tab?: string; file?: string; flow: boolean }) {
  const navigate = useNavigate();
  const tabs = tabsOf(t);
  const known = !!tab && tabs.some((x) => x.slug === tab);
  // With no tab in the address, the page that waits on the person opens, and the address names it: the tab row reads
  // the address, and an answer that changes which page waits does not move the person off the page they read.
  const [opened] = useState(() => defaultTab(tabs));
  const on = known ? (tab as string) : opened;
  useEffect(() => {
    if (known) return;
    const a = tabAddress(t, opened, flow);
    void navigate({ to: a.to as never, search: a.search as never, replace: true });
  }, [known, opened, flow, t, navigate]);
  const [adding, setAdding] = useState<Adding | null>(null);
  const search = useSearch({ strict: false }) as TaskSearch;

  const current = tabs.find((x) => x.slug === on);
  const versions = useVersions(current?.doc, search.v, search.vs);
  // The change stepped to, from Changes' two arrows; a new page, version or comparison starts before the first.
  const [step, setStep] = useState({ key: '', at: -1 });
  const key = `${on}:${versions?.shown ?? ''}:${versions?.on ? versions.before : ''}`;
  const at = step.key === key ? step.at : -1;
  const onAt = (n: number) => setStep({ key, at: n });
  const address = tabAddress(t, on, flow);
  const details = on === DETAILS;
  const rename = async (title: string) => {
    const r = await run('task.rename', { task: t.id, title }, { via: 'click' });
    if (!r.ok) toast(failedToast(r));
  };

  const pageTabs: PageTab[] = tabs.map((x) => {
    const a = tabAddress(t, x.slug, flow);
    return { label: x.label, to: a.to, ...(flow && { search: { tab: x.slug } }), mark: markOf(x), target: `task-tab:${x.slug}` };
  });

  const body =
    file && details ? (
      <FileView t={t} file={file} flow={flow} full={search.full} />
    ) : details ? (
      <Details t={t} flow={flow} adding={adding} onAdding={setAdding} />
    ) : versions ? (
      <PageView task={t.id} vs={versions} at={at} />
    ) : null;

  return (
    <Document
      className={['tv', details && 'is-details', flow && 'is-flow'].filter(Boolean).join(' ')}
      title={t.title}
      path={address.to}
      crumb={flow ? [{ label: 'Flow', to: '/flow' }] : [{ label: t.pipeline?.name ?? 'Tasks', to: `/tasks/${encodeURIComponent(t.pipeline_id ?? '')}` }]}
      sub={<WhereLine t={t} />}
      onRename={(title) => void rename(title)}
      actions={flow ? <FlowSteps t={t} /> : undefined}
      top={flow ? <FlowTop t={t} /> : undefined}
      more={<MoreMenu t={t} flow={flow} />}
      tabs={pageTabs}
      tabsAfter={
        details && !file ? <AddMenu onAdding={setAdding} /> : versions ? <VersionBar task={t.id} vs={versions} at={at} onAt={onAt} /> : undefined
      }
      framed
      body={
        <div className="tv-view">
          {body}
          <Decision t={t} flow={flow} dock />
          {flow && <FlowCover />}
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
  if (!t) return null;
  const name = owner?.name ?? 'Teammate';
  return (
    <>
      <header className="side-head">
        <Face id={t.owner_id} size={28} decorative />
        <b>{name}</b>
        <span className="grow" />
      </header>
      {t.conversation_id ? (
        <Conversation id={t.conversation_id} variant="task" title={name} actor={t.owner_id ?? undefined} foot={<Decision t={t} flow={flow} />} />
      ) : (
        <Decision t={t} flow={flow} />
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
