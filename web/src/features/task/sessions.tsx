import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouterState } from '@tanstack/react-router';
import { useEffect, useRef, useSyncExternalStore } from 'react';
import { activityQuery, agentWords, dropRun, liveQuery, markRead, rowsOf, seedRun, timelineQuery, type LiveRun, type TimelineView } from '@/conversation';
import { keys, useActor } from '@/data';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { useCards } from '@/kit/Popover';
import { Row } from '@/kit/Row';
import { Section } from '@/pagekinds';
import { threadQuery } from '@/features/threads/exports';
import { CHAT, tabAddress } from './model';
import { Face } from './parts';
import { sessionRunQuery, type SessionRun as RunSnapshot, type TaskData, type TaskSession } from './queries';

/* Each agent on a task has its own session view on the task's one thread (its stamped messages and those that
   @mention it); the comments are the rest, a log that reaches only whom they name. One read mark per task: the page
   keeps the mark it had when it opened, then marks the task read, and the Sessions list marks each session with newer
   messages than the kept mark. */

/** The comments of a task's thread: what the task's chat shows when no agent's session is open. */
export const COMMENTS: TimelineView = { comments: true };

/** Whether the task's conversation is its own thread (`task_id` is the task), or undefined until that is read. A task
    filed in its owner's main chat has no comments or sessions of its own: its chat is that chat. */
export function useOwnThread(t: Pick<TaskData, 'id' | 'conversation_id'> | undefined): boolean | undefined {
  return useThreadRead(t).own;
}

/** `useOwnThread`, and whether the thread's read failed (with nothing held), with a way to read it again. */
export function useThreadRead(t: Pick<TaskData, 'id' | 'conversation_id'> | undefined) {
  const room = useQuery({ ...threadQuery(t?.conversation_id ?? ''), enabled: !!t?.conversation_id });
  return { own: room.data ? room.data.task_id === t?.id : undefined, failed: room.isError && !room.data, retry: () => void room.refetch() };
}

/** In place of a task's chat whose thread did not read: says so, with Try again. */
export function ChatNotRead({ retry }: { retry: () => void }) {
  return (
    <Empty icon="alert" title="This chat did not load">
      <Button onClick={retry}>Try again</Button>
    </Empty>
  );
}

/* ---------------------------------------------------------------- one read mark per task */

// The task's read mark as its page found it on entry, per thread, while the page is open.
const opened = new Map<string, number>();
const watchers = new Set<() => void>();
const told = () => watchers.forEach((w) => w());
const watch = (w: () => void) => {
  watchers.add(w);
  return () => void watchers.delete(w);
};

/** The task's read mark as it was when its page opened; null until the page has read it. */
export function useOpenedMark(thread: string | null | undefined): number | null {
  return useSyncExternalStore(watch, () => (thread ? (opened.get(thread) ?? null) : null));
}

/** The task's page, once, on entry: keeps the read mark the comments' first read brings (before anything marks it), then
    marks the task read up to its newest message (a comment's, or any session's `last_seq`). A view of the thread never
    marks it by itself. */
export function useTaskReadMark(t: Pick<TaskData, 'conversation_id' | 'sessions'>, own: boolean | undefined) {
  const thread = own ? t.conversation_id : null;
  const { data } = useInfiniteQuery({ ...timelineQuery(thread ?? '', COMMENTS), enabled: !!thread });
  const marks = data?.pages[0]?.read_marks;
  useEffect(() => {
    if (!thread || !marks || opened.has(thread)) return;
    opened.set(thread, marks[thread] ?? 0);
    told();
  }, [thread, marks]);
  useEffect(
    () => () => {
      if (thread && opened.delete(thread)) told();
    },
    [thread],
  );
  const old = useOpenedMark(thread);
  const newest = Math.max(0, ...rowsOf(data).map((m) => m.seq ?? 0), ...t.sessions.map((s) => s.last_seq ?? 0));
  useEffect(() => {
    if (thread && old !== null && newest > old && document.visibilityState === 'visible') markRead(thread, newest);
  }, [thread, old, newest]);
}

/* ---------------------------------------------------------------- the Sessions list */

/** How long a burst of the thread's messages and runs waits before the task's sessions are read again. */
const SESSIONS_WAIT_MS = 1000;

/** The task page, while it is open on a task with its own thread: its sessions read again (its detail, which carries
    them) as the thread's messages and runs come, once per burst (an agent an @mention woke, a session's newest
    message); and an agent's run read again on each of its job changes, open in its session or not (a closed one is
    dropped, so opening it again reads it, never an ended run kept from before). */
export function useTaskLive(t: Pick<TaskData, 'id' | 'conversation_id'>, own: boolean | undefined) {
  const thread = own ? t.conversation_id : null;
  const task = t.id;
  const { data } = useQuery({ ...activityQuery(thread ?? ''), enabled: !!thread });
  const client = useQueryClient();
  const moved = data ? data.messages + Object.values(data.runs).reduce((a, b) => a + b, 0) : 0;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!moved || timer.current) return;
    timer.current = setTimeout(() => {
      timer.current = null;
      void client.invalidateQueries({ queryKey: keys.tasks.detail(task), exact: true });
    }, SESSIONS_WAIT_MS);
  }, [moved, client, task]);
  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), []);
  const seen = useRef<Record<string, number>>({});
  const runs = data?.runs;
  useEffect(() => {
    if (!runs) return;
    for (const [agent, n] of Object.entries(runs)) {
      if (seen.current[agent] === n) continue;
      seen.current[agent] = n;
      const queryKey = sessionRunQuery(task, agent).queryKey;
      client.removeQueries({ queryKey, exact: true, type: 'inactive' });
      void client.invalidateQueries({ queryKey, exact: true, type: 'active' });
    }
  }, [runs, client, task]);
}

/** The agents with a session on the task: the task read's rows, and an agent at work on its thread that the read does
    not have yet (an @mention just woke it). */
function sessionsOf(t: TaskData, runs: { bot: string; ended: boolean }[]): TaskSession[] {
  const rows = [...t.sessions];
  for (const r of runs) if (!r.ended && !rows.some((s) => s.agent === r.bot)) rows.push({ agent: r.bot, last_seq: null });
  return rows;
}

function SessionRow({ agent, href, unread, selected }: { agent: string; href: string; unread: boolean; selected: boolean }) {
  const name = useActor(agent)?.name ?? agentWords(agent);
  return (
    <Row className="who" title={name} href={href} unread={unread} selected={selected} target={`task-session:${agent}`}>
      <Face id={agent} decorative />
      <span className="row-title">{name}</span>
    </Row>
  );
}

/** The address's words as text, `session` set to the agent (the rest kept: an ask, a version, a file). */
const withSession = (search: Record<string, unknown>, agent: string): string => {
  const out = new URLSearchParams();
  for (const [k, v] of Object.entries(search)) if (k !== 'session' && v != null) out.set(k, typeof v === 'string' ? v : JSON.stringify(v));
  out.set('session', agent);
  return out.toString();
};

/** Sessions: one row per agent on the task, each opening its session view in the task's chat (a phone's chat card). New
    when it has messages past the mark the page opened with. A task filed in its owner's main chat has none. */
export function Sessions({ t }: { t: TaskData }) {
  const own = useOwnThread(t);
  const thread = own ? t.conversation_id : null;
  const old = useOpenedMark(thread);
  const { data: runs = [] } = useQuery({ ...liveQuery(thread ?? ''), enabled: !!thread });
  const cards = useCards();
  const location = useRouterState({ select: (s) => s.location });
  const search = location.search as Record<string, unknown>;
  const rows = thread ? sessionsOf(t, runs) : [];
  if (!rows.length) return null;
  const to = cards ? tabAddress(t, CHAT) : location.pathname;
  return (
    <Section name="Sessions" count={rows.length} target={`task-sessions:${t.id}`}>
      {rows.map((s) => (
        <SessionRow
          key={s.agent}
          agent={s.agent}
          href={`${to}?${withSession(search, s.agent)}`}
          unread={old !== null && (s.last_seq ?? 0) > old}
          selected={search.session === s.agent}
        />
      ))}
    </Section>
  );
}

/* ---------------------------------------------------------------- an agent's run in its session */

const UNDER_WAY = new Set(['leased', 'running']);

/** A run under way as its snapshot has it, drawn as the stream would have: its finished pieces, and its last text
    piece still being written (`typing`), so the stream's next words carry on from it and the message that ends it
    takes its place. After a tool call it is at work. */
export function snapshotRun(run: RunSnapshot, agent: string): LiveRun {
  const id = run.attempt_id ?? '';
  const pieces = run.parts ?? (run.text?.trim() ? [{ kind: 'reply', text: run.text }] : []);
  const texts = pieces.filter((p) => p.kind !== 'tool').map((p) => p.text);
  const last = pieces.at(-1);
  if (!last) return { run: id, bot: agent, parts: [], typing: null, doing: 'think', ended: false };
  if (last.kind === 'tool') return { run: id, bot: agent, parts: texts, typing: null, doing: 'work', label: last.text, ended: false };
  return { run: id, bot: agent, parts: texts.slice(0, -1), typing: last.text, doing: 'type', ended: false };
}

/** An agent's run on the task as its session shows it (GET .../execution?agent=), read on opening and again on each
    of that agent's job and run changes on the thread, queued ones too (useTaskLive). A run already under way when the
    view opened is drawn as its reply being written (its snapshot), and the stream carries on from there; one that
    waits or stopped is said over the box ("Saved — queued", "Stopped"). Only a read made since the view opened draws
    anything: a kept one may be of a run that has ended. */
export function SessionRun({ t, agent }: { t: Pick<TaskData, 'id' | 'conversation_id'>; agent: string }) {
  const thread = t.conversation_id ?? '';
  const live = useQuery(liveQuery(thread)).data ?? [];
  const working = live.some((r) => r.bot === agent && !r.ended);
  const read = useQuery({ ...sessionRunQuery(t.id, agent), refetchOnMount: 'always' });
  const run = read.isFetchedAfterMount ? read.data : undefined;
  useEffect(() => {
    if (!thread || !run?.attempt_id) return;
    if (UNDER_WAY.has(run.state)) seedRun(thread, snapshotRun(run, agent));
    else dropRun(thread, run.attempt_id);
  }, [thread, agent, run]);
  if (working || !run || run.state === 'completed' || (UNDER_WAY.has(run.state) && run.attempt_id)) return null;
  return <span className="status-line">{run.label}</span>;
}
