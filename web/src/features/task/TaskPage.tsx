import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { ApiError } from '@/api/client';
import { Conversation } from '@/conversation';
import { useActor } from '@/data';
import { Icon } from '@/kit/Icon';
import { LatePill } from '@/kit/Pill';
import { useFollow } from '@/live/follow';
import { Document, type PageState } from '@/pagekinds';
import { Decision } from './Answer';
import { Details } from './Details';
import { DocFrame, FileView } from './FileView';
import { FlowSteps, MoreMenu, OwnerPick, StagePick } from './Head';
import { defaultTab, DETAILS, tabAddress, tabsOf, type TaskTab } from './model';
import { Face, FlowMark, Go } from './parts';
import { taskDetailQuery, taskRefQuery, type TaskData } from './queries';
import './task.css';

export interface TaskPageProps {
  /** The address's task: its number, or its id. */
  task: string;
  /** The address's pipeline (`/tasks/<pipeline>/<task>`); Flow has none. */
  pipeline?: string;
  /** The open tab's slug; none opens the page that waits on the person. */
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

function TaskView({ t, tab, file, flow }: { t: TaskData; tab?: string; file?: string; flow: boolean }) {
  const tabs = tabsOf(t);
  // With no tab in the address, the page it opened on stays open once it is answered.
  const [opened] = useState(() => defaultTab(tabs));
  const on = tab && tabs.some((x) => x.slug === tab) ? tab : tabs.some((x) => x.slug === opened) ? opened : defaultTab(tabs);
  const current = tabs.find((x) => x.slug === on);
  const address = tabAddress(t, tab ? on : undefined, flow);
  const details = on === DETAILS;

  const body =
    file && details ? (
      <FileView t={t} file={file} flow={flow} />
    ) : details ? (
      <Details t={t} flow={flow} />
    ) : current?.doc ? (
      <DocFrame task={t.id} doc={current.doc} />
    ) : null;

  return (
    <Document
      className={details ? 'tv is-details' : 'tv'}
      title={t.title}
      path={address.to}
      crumb={flow ? [{ label: 'Flow', to: '/flow' }] : [{ label: t.pipeline?.name ?? 'Tasks', to: `/tasks/${encodeURIComponent(t.pipeline_id ?? '')}` }]}
      after={t.late ? <LatePill /> : undefined}
      actions={
        <>
          <StagePick t={t} />
          <OwnerPick t={t} />
          {flow && <FlowSteps t={t} />}
        </>
      }
      more={<MoreMenu t={t} />}
      framed
      body={
        <div className="tv-view">
          <TaskTabs t={t} tabs={tabs} on={on} flow={flow} />
          {body}
          <Decision t={t} flow={flow} dock />
        </div>
      }
    />
  );
}

/** The tab row (parts-kit.js tab): each of the stage's pages with its mark, then Details. Links: the address decides. */
function TaskTabs({ t, tabs, on, flow }: { t: TaskData; tabs: TaskTab[]; on: string; flow: boolean }) {
  return (
    <div className="tv-tabs">
      <nav className="docs" aria-label={`${t.title} pages`}>
        {tabs.map((x) => {
          const a = tabAddress(t, x.slug, flow);
          return (
            <Go
              key={x.slug}
              to={a.to}
              search={a.search}
              className={['doc-tab', x.mark === 'none' && 'is-none', x.slug === on && 'is-on'].filter(Boolean).join(' ')}
              aria-current={x.slug === on ? 'page' : undefined}
            >
              {x.mark === 'ok' ? <Icon name="check" label="Approved" /> : x.mark === 'ask' ? <FlowMark size={10} /> : x.mark === 'none' ? <i className="hollow" /> : null}
              <span>{x.label}</span>
            </Go>
          );
        })}
      </nav>
    </div>
  );
}

export interface TaskChatProps {
  /** The address's task: its number, or its id. */
  task: string;
  flow?: boolean;
}

/**
 * The task's chat, for the second chat sheet of the 'task' arrangement: its owner's face and name, the conversation,
 * and the answer card over the message box. The root wraps it in the chat sheet.
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
      {t.conversation_id && <Conversation id={t.conversation_id} variant="task" title={name} actor={t.owner_id ?? undefined} />}
      <Decision t={t} flow={flow} />
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
