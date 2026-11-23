import { holdList } from '@/lib';

/* An answer the person gave (Approve, Request changes, a hold decided, a Release): the open task's page hears it and
   decides whether to stay, on what still waits on them there, or to close, back to where the task was opened from. One
   page is open at a time, so one listener. */

type Hand = () => void;
let hand: Hand | null = null;

/** The open task's page listens while it is drawn; the returned function stops it. */
export function onAnswered(h: Hand): () => void {
  hand = h;
  return () => {
    if (hand === h) hand = null;
  };
}

/** Said once the server took the answer. */
export const answered = (): void => hand?.();

/** Said as the person gives an answer, before it is sent: the list beside the task keeps the order it has now, so the
    row below the task stays the one below it whatever the answer moves. */
export const answering = (): void => holdList();
