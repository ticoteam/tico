import type { TrackedTask } from './queries';

/** Back looks this far at most, however long ago the tracker was last opened. */
export const BACK_DAYS = 7;

/** Where Back starts: the last time the tracker was opened, at most BACK_DAYS ago. */
export function backSince(seenAt: string | null | undefined, now: number): string {
  const floor = now - BACK_DAYS * 86_400_000;
  const seen = seenAt ? Date.parse(seenAt) : NaN;
  return new Date(Number.isFinite(seen) && seen > floor ? seen : floor).toISOString();
}

/** When a task finished: done, else closed, else its last change (the server's finished order). */
export const finishedAt = (t: TrackedTask) => t.done_at ?? t.closed_at ?? t.updated_at;

export interface TrackerGroups {
  /** Open, and waiting on the person (their Needs you). */
  needs: TrackedTask[];
  /** Open, with someone else. */
  open: TrackedTask[];
  /** Finished since `since`, newest first. */
  back: TrackedTask[];
}

/**
 * The tracker's three groups: every open task the person (`me`) asked for that someone else holds, split by whether
 * it waits on them (`waiting`: the task ids in their Needs you), and what came back since `since`. A row the stream
 * patched in out of place is sorted out here: an open list's finished task and a finished list's open one are left.
 */
export function trackerGroups(open: TrackedTask[], back: TrackedTask[], waiting: ReadonlySet<string>, me: string, since: string): TrackerGroups {
  // Held by someone else: an owner, and not the person. An unassigned task waits on no one yet.
  const others = (t: TrackedTask) => !!t.owner_id && t.owner_id !== me;
  const live = open.filter((t) => t.status === 'open' && others(t));
  const at = Date.parse(since);
  return {
    needs: live.filter((t) => waiting.has(t.id)),
    open: live.filter((t) => !waiting.has(t.id)),
    back: back
      .filter((t) => t.status !== 'open' && others(t) && Date.parse(finishedAt(t)) > at)
      .sort((a, b) => Date.parse(finishedAt(b)) - Date.parse(finishedAt(a))),
  };
}

/** When the first of these leaves Back by age (BACK_DAYS after it finished); null for none. */
export function nextAgeOut(back: TrackedTask[]): number | null {
  const ends = back.map((t) => Date.parse(finishedAt(t)) + BACK_DAYS * 86_400_000).filter(Number.isFinite);
  return ends.length ? Math.min(...ends) : null;
}
