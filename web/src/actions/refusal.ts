import { refused, sayable } from './run';
import type { Result } from './types';

/** A failure toast: what did not happen ("Not saved"), then why when there is a reason. Shaped as `@/kit/Toast` takes it. */
export interface RefusalToast {
  text: string;
  name?: string;
  kind: 'failed';
}

/* What the action layer writes about the request itself, never words for a person: input that did not parse, an
   unknown id, a thrown error's own message. */
const OWN = /^(failed|bad_input|unknown_action):/;

/** Why a run was refused, in words: the server's reason, else the words an action gave ("code: reason", first line),
    else nothing. A bare code (`factory_name`) is never turned into words. */
export function reasonIn(r: Pick<Result, 'error' | 'why'>): string | undefined {
  if (r.why) return r.why;
  const error = r.error ?? '';
  if (error === 'network') return 'No connection';
  const i = error.indexOf(': ');
  if (i < 0 || OWN.test(error)) return undefined;
  return sayable(error.slice(i + 2).split('\n')[0] ?? '');
}

/** The toast for a refused run: `failed` is the short words ("Not saved"); `denied` stands in for them when the person
    may not do it and the server said nothing more. */
export function refusal(r: Pick<Result, 'error' | 'why'>, failed: string, denied = 'You cannot do that here'): RefusalToast {
  const why = reasonIn(r);
  const text = r.error === 'not_allowed' && !why ? denied : failed;
  return why ? { text, name: why, kind: 'failed' } : { text, kind: 'failed' };
}

/** The same, for an error caught inside an action (one that says so itself before it passes the refusal on). */
export const refusalFor = (e: unknown, failed: string, denied?: string): RefusalToast => refusal(refused(e), failed, denied);
