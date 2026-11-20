import { useEffect } from 'react';
import type { QueryKey } from '@tanstack/react-query';
import { queryClient } from '@/data/query-client';
import { restartLive, setScopeSource, type StreamScope, type Topic } from './stream';

// What is on screen tells the stream what to send (backend/events.py narrowing): every task's change on a board,
// one task's in its view, a person's or an agent's own tasks on their page, a thread's messages and run output while
// it is open. Follows are counted, so two views of one thread follow it once. When a scope stops, the queries it fed
// become stale: the stream no longer keeps them fresh, so the next screen that shows them reads them again.

export type FollowScope =
  | { allTasks: true }
  | { task: string }
  | { party: string } // an actor: human:<id> or bot:<id>
  | { conversation: string }
  | { thread: string } // the thread and its sub-threads
  | { meeting: string }
  | { place: string }
  | { topic: Topic }; // a topic sent whole (letters on a records page)

/** Always followed: the menu's counts, agents' status lines, the rail, refetch signals for calendar and setup, and
    Tico's moves of this screen. */
const BASE: Topic[] = ['bots', 'needs', 'threads', 'calendar', 'setup', 'screen'];

const counts = new Map<string, number>();
// A quick trip away from a board can reuse its large list while the stream still covers it.
const TASK_LIST_GRACE_MS = 5000;
let tasksExpiry: ReturnType<typeof setTimeout> | undefined;

const idOf = (scope: FollowScope): string => {
  const [kind, value] = Object.entries(scope)[0] as [string, unknown];
  return `${kind}\u0000${String(value)}`;
};

function scope(): StreamScope {
  const out: StreamScope = { topics: [...BASE], conversation: [], thread: [], task: [], party: [], meeting: [], place: [] };
  let allTasks = tasksExpiry !== undefined;
  const topics = new Set<Topic>(BASE);
  for (const id of counts.keys()) {
    const [kind, value] = id.split('\u0000') as [string, string];
    if (kind === 'allTasks') allTasks = true;
    else if (kind === 'topic') topics.add(value as Topic);
    else out[kind as 'task' | 'party' | 'conversation' | 'thread' | 'meeting' | 'place'].push(value);
  }
  if (allTasks || out.task.length || out.party.length) topics.add('tasks');
  if (allTasks) out.task = out.party = [];
  if (out.conversation.length || out.thread.length) topics.add('messages').add('runs');
  if (out.meeting.length) topics.add('meeting_items');
  if (out.place.length) topics.add('presence');
  out.topics = [...topics];
  return out;
}
setScopeSource(scope);

function fed(scope: FollowScope): QueryKey[] {
  if ('allTasks' in scope || 'party' in scope) return [['tasks', 'list']];
  if ('task' in scope) return [['tasks', 'detail', scope.task]];
  if ('conversation' in scope) return [['thread', scope.conversation]];
  if ('thread' in scope) return [['thread', scope.thread]];
  if ('meeting' in scope) return [['meetings', scope.meeting]];
  if ('place' in scope) return [['presence', scope.place]];
  return [[scope.topic]];
}

/** Follow `scope` until the returned release is called. `feeds`: the keys it keeps fresh, when not the usual ones. */
export function follow(scope: FollowScope, feeds?: QueryKey[]): () => void {
  const id = idOf(scope);
  const n = counts.get(id) ?? 0;
  counts.set(id, n + 1);
  if ('allTasks' in scope && tasksExpiry !== undefined) {
    clearTimeout(tasksExpiry);
    tasksExpiry = undefined;
  } else if (!n) restartLive();
  const stop = () => {
    restartLive();
    for (const queryKey of feeds ?? fed(scope)) void queryClient.invalidateQueries({ queryKey, refetchType: 'none' });
  };
  let released = false;
  return () => {
    if (released) return;
    released = true;
    const left = (counts.get(id) ?? 1) - 1;
    if (left > 0) return void counts.set(id, left);
    counts.delete(id);
    if ('allTasks' in scope) {
      tasksExpiry = setTimeout(() => {
        tasksExpiry = undefined;
        stop();
      }, TASK_LIST_GRACE_MS);
    } else stop();
  };
}

/** Follow while the component is mounted; null follows nothing. */
export function useFollow(scope: FollowScope | null | undefined, feeds?: QueryKey[]): void {
  const id = scope ? JSON.stringify([scope, feeds ?? null]) : '';
  useEffect(() => {
    if (!id) return;
    const [s, f] = JSON.parse(id) as [FollowScope, QueryKey[] | null];
    return follow(s, f ?? undefined);
  }, [id]);
}

/** Tests: forget every follow. */
export function resetFollows(): void {
  clearTimeout(tasksExpiry);
  tasksExpiry = undefined;
  counts.clear();
}
