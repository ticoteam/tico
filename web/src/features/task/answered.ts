/* An answer the person gave outside Flow (Approve, or Request changes): the open task's page hears it and decides
   whether to stay, on what still waits on them there, or to close, back to where the task was opened from. One page
   is open at a time, so one listener. */

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
