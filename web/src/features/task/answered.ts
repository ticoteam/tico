import { needsYouLeft } from '@/features/tasks/routing';
import { holdList } from '@/lib';

/* An answer the person gave (Approve, Request changes, a hold decided, a Release): the open task's page hears it and
   decides whether to stay, on what still waits on them there, or to close, back to where the task was opened from. One
   page is open at a time, so one listener. */

/** What the page hears: the task answered, and whether Needs you, as read when the person acted, still names it for
    something besides this answer (a second ask, paid shots to reconcile). Read then, so a read of Needs you that
    lands while the answer is sent never decides it. */
export type Answered = { task: string; more: boolean };
type Hand = (a: Answered | null) => void;
let hand: Hand | null = null;
let given: Answered | null = null;

/** The open task's page listens while it is drawn; the returned function stops it. */
export function onAnswered(h: Hand): () => void {
  hand = h;
  return () => {
    if (hand === h) hand = null;
  };
}

/** Said once the server took the answer. */
export const answered = (): void => hand?.(given);

/** Said as the person gives an answer to `wait` on `task` (null: all that waits there, as a review's last hold or a
    Release), before it is sent: what else Needs you has them do there is read now, and the list beside the task keeps
    the order it has now, so the row below the task stays the one below it whatever the answer moves. */
export const answering = (task: string, wait: string | null): void => {
  given = { task, more: needsYouLeft(task, wait).length > 0 };
  holdList();
};
