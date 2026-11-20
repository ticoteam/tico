import { useRef, useState, type MouseEvent } from 'react';
import { toast } from '@/kit/Toast';

/** A press handler. One that returns a promise (a write) keeps its button busy until the promise settles. */
export type PressHandler<E extends Element> = (e: MouseEvent<E>) => unknown;

const isThenable = (v: unknown): v is PromiseLike<unknown> =>
  typeof v === 'object' && v !== null && typeof (v as { then?: unknown }).then === 'function';

/** What a refused action answers (actions/run's Result): `ok` false, and the server's reason in `why` when it gave one.
    Null when it is not one, or when the action has said it already (`told`). */
function refusal(v: unknown): { why?: string; denied: boolean } | null {
  if (typeof v !== 'object' || v === null || (v as { ok?: unknown }).ok !== false) return null;
  const { why, error, told } = v as { why?: unknown; error?: unknown; told?: unknown };
  if (told === true) return null;
  return { why: typeof why === 'string' && why ? why : undefined, denied: error === 'not_allowed' };
}

/**
 * A button's press, held while its work runs: from the press until the promise its handler returns settles (through the
 * client's own retries while the server restarts), the button is busy and a second press does nothing. A failure the
 * handler hands back, a thrown error or a refused action's answer, is said in a toast, so a press is never silently
 * lost; a handler that says it in place returns nothing once it has.
 */
export function usePress<E extends Element>(onClick: PressHandler<E> | undefined, busy: boolean) {
  const [running, setRunning] = useState(false);
  const held = useRef(false);
  const press =
    onClick &&
    ((e: MouseEvent<E>) => {
      if (busy || held.current) return;
      const out = onClick(e);
      if (!isThenable(out)) return;
      held.current = true;
      setRunning(true);
      Promise.resolve(out)
        .then(
          (answer) => {
            const no = refusal(answer);
            if (no) toast({ text: no.denied && !no.why ? 'You cannot do that here' : 'That did not work', ...(no.why && { name: no.why }), kind: 'failed' });
          },
          (err: unknown) => {
            const offline = err instanceof Error && err.name === 'NeedsConnection';
            toast({ text: offline ? 'Needs a connection' : 'That did not work', kind: 'failed' });
          },
        )
        .finally(() => {
          held.current = false;
          setRunning(false);
        });
    });
  return { busy: busy || running, onClick: press };
}
