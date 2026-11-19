import type { QueryKey } from '@tanstack/react-query';
import { newId } from '@/lib/ids';
import { hold, isNetworkError, isOffline } from './held';
import { queryClient } from './query-client';

export { isNetworkError };

/** Tapping a control that cannot wait for the connection: the control says "Needs a connection". */
export class NeedsConnection extends Error {
  constructor() {
    super('Needs a connection');
    this.name = 'NeedsConnection';
  }
}

/* eslint-disable @typescript-eslint/no-explicit-any -- each key holds its own shape; the caller's functions know it */
export interface Write<T> {
  /** The query, or queries, the change shows in. */
  key: QueryKey | QueryKey[];
  /** What each key shows at once. */
  optimistic: (current: any) => any;
  /** Send it. Pass `idempotencyKey` to the API so a resend after a drop lands once. */
  send: (idempotencyKey: string) => Promise<T>;
  /**
   * Put the server's answer in place of what was shown (swap a pending `tmp:` row for the real one). The stream's
   * echo of the same row may already be there: replace by id, so a row never shows twice.
   */
  settle?: (current: any, answer: T) => any;
  /** Take just this change back out. Without it, the key is put back if nothing else changed it, else read again. */
  revert?: (current: any, error: unknown) => any;
  /** While offline: hold it and send on return, or refuse it (the default; platforms.md 7 says which is which). */
  offline?: 'hold' | 'refuse';
}
/* eslint-enable @typescript-eslint/no-explicit-any */

const keysOf = (key: QueryKey | QueryKey[]): QueryKey[] =>
  Array.isArray(key[0]) ? (key as QueryKey[]) : [key as QueryKey];

// ---------------------------------------------------------------- areas with no live topic

/** How old a quiet area's read may be when a page opens (the feature's queries.ts sets it as staleTime). */
export const QUIET_STALE_MS = 60_000;
const quiet = new Set<string>();

/**
 * Mark a key root as having no live topic (goals, KPIs, budgets, finance, records: README). Its keys are read
 * again after the person's own writes (below), when the stream is back after being down (live/stream.ts), and on
 * page open when older than QUIET_STALE_MS. Nothing polls.
 */
export function quietRoot(root: string): void {
  quiet.add(root);
}

export const isQuiet = (key: QueryKey): boolean => typeof key[0] === 'string' && quiet.has(key[0]);

/** Read the quiet areas on screen again (the stream is back). */
export function refetchQuiet(): void {
  for (const root of quiet) void queryClient.invalidateQueries({ queryKey: [root], refetchType: 'active' });
}

/**
 * Show a change first, send it after, and take it back if the server refuses it. Offline, a held change stays on
 * screen and is sent when the stream is back; a refused one throws NeedsConnection and shows nothing.
 * Resolves with the server's answer; rejects with its refusal, after reverting.
 */
export async function write<T>(w: Write<T>): Promise<T> {
  const holdable = w.offline === 'hold';
  if (isOffline() && !holdable) throw new NeedsConnection();
  const keys = keysOf(w.key);
  const idem = newId();
  const shown = keys.map((queryKey) => {
    // A read in flight would land over the change: cancel it (the stream or a later read brings it back).
    void queryClient.cancelQueries({ queryKey });
    const before = queryClient.getQueryData(queryKey);
    const after = queryClient.setQueryData(queryKey, w.optimistic);
    return { queryKey, before, after };
  });
  const send = () => w.send(idem);
  let answer: T;
  try {
    answer = await (isOffline() ? hold(send) : send().catch((err: unknown) => (holdable && isNetworkError(err) ? hold(send) : Promise.reject(err))));
  } catch (err) {
    for (const { queryKey, before, after } of shown) {
      if (w.revert) queryClient.setQueryData(queryKey, (current: unknown) => (w.revert as NonNullable<Write<T>['revert']>)(current, err));
      else if (queryClient.getQueryData(queryKey) === after) queryClient.setQueryData(queryKey, before);
      else void queryClient.invalidateQueries({ queryKey });
      // Changed meanwhile by someone else (409): read the truth.
      if (typeof err === 'object' && err !== null && (err as { status?: unknown }).status === 409) void queryClient.invalidateQueries({ queryKey });
    }
    throw err;
  }
  for (const { queryKey } of shown) {
    if (w.settle) queryClient.setQueryData(queryKey, (current: unknown) => (w.settle as NonNullable<Write<T>['settle']>)(current, answer));
    // No topic brings this area's echo: read it again (README, areas with no live topic).
    if (isQuiet(queryKey)) void queryClient.invalidateQueries({ queryKey });
  }
  return answer;
}
