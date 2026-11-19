import type { Query, QueryCacheNotifyEvent } from '@tanstack/react-query';
import { useSyncExternalStore } from 'react';
import { ApiError } from '@/api/client';
import type { Problem } from '@/api/schema';
import { queryClient } from '@/data';

/* A locked Tico Cloud company (backend/cloud_access.py): the trial ended without a card, or the month is unpaid. The
   server then refuses every person's call 402 `locked`, with HQ's word under `cloud` (reason, billing page), except who
   is signed in, billing and the files download. So the lock is read from the refusals already in the cache: the first
   page's reads (the directory, at least) meet it, and no other read is needed. */

/** HQ's billing page when its answer names none (ui/app/cloud-access.ts). */
export const BILLING_URL = 'https://app.tico.dev/billing';
/** Tico support's own page: the chat inside the app is refused while the company is locked, and may not have loaded. */
export const SUPPORT_URL = 'https://app.tico.dev/support';
/** Every file the owner may open, as one zip; the server keeps it open through a lock. */
export const EXPORT_PATH = '/api/v2/system/storage/export';

export interface PlanLockState {
  /** HQ's reason: `trial_over`, `unpaid`, or another word; null when it gave none. */
  reason: string | null;
  /** Where to pay: HQ's https page. */
  billingUrl: string;
}

/** The lock a refusal carries, or null for any other error. */
export function lockOf(error: unknown): PlanLockState | null {
  if (!(error instanceof ApiError) || error.status !== 402 || error.code !== 'locked') return null;
  // Checked field by field all the same: nothing odd from HQ should decide where a person is sent to pay.
  const cloud = error.extra.cloud as Problem['error']['cloud'] | null;
  const url = typeof cloud?.billing_url === 'string' && /^https:\/\/\S+$/.test(cloud.billing_url) ? cloud.billing_url : BILLING_URL;
  return { reason: typeof cloud?.reason === 'string' && cloud.reason ? cloud.reason : null, billingUrl: url };
}

const same = (a: PlanLockState | null, b: PlanLockState | null) => a === b || (!!a && !!b && a.reason === b.reason && a.billingUrl === b.billingUrl);

/* The lock is held, not re-derived from each query's state: a refused read that is read again goes back to pending
   (its error cleared) and a first refusal is only a failed attempt while its retry runs. Either way the company is
   still locked. It lifts only on proof: a read the lock refused answers, or the whole cache is cleared (signed out). */
let held: PlanLockState | null = null;
/** The reads the lock refused, by query hash: what proves it gone when they answer, and what Check again reads. */
const refused = new Set<string>();
const listeners = new Set<() => void>();
let watching = false;

function hold(next: PlanLockState | null): void {
  if (same(next, held)) return;
  held = next;
  for (const fn of listeners) fn();
}

function seen(q: Query, error: unknown): void {
  const lock = lockOf(error);
  if (!lock) return;
  refused.add(q.queryHash);
  hold(lock);
}

/* Only answers and removals: the cache also reports queries made while a component renders, and telling React then
   is the "update while rendering a different component" fault. Answers and removals never come during a render. */
function onEvent(e: QueryCacheNotifyEvent): void {
  if (e.type === 'removed') {
    refused.delete(e.query.queryHash);
    if (queryClient.getQueryCache().getAll().length === 0) hold(null);
    return;
  }
  if (e.type !== 'updated') return;
  const a = e.action;
  // 'failed' is an attempt with a retry still to come: one 402 is enough.
  if (a.type === 'error' || a.type === 'failed') seen(e.query, a.error);
  // From the server only: a write shown ahead of its answer (setQueryData) proves nothing.
  else if (a.type === 'success' && !a.manual && refused.has(e.query.queryHash)) {
    refused.clear();
    hold(null);
  }
}

function watch(): void {
  if (watching) return;
  watching = true;
  const cache = queryClient.getQueryCache();
  // Refusals that came before anything asked for the lock.
  for (const q of cache.getAll()) if (q.state.status === 'error') seen(q, q.state.error);
  cache.subscribe(onEvent);
}

/** The lock, or null: the company is open (or not a cloud one). */
export function currentLock(): PlanLockState | null {
  watch();
  return held;
}

function subscribe(fn: () => void): () => void {
  watch();
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** For the lock: changes only when a lock arrives or lifts. */
export const usePlanLock = (): PlanLockState | null => useSyncExternalStore(subscribe, currentLock, () => null);

/** The person's own Check again: read again everything the lock refused. True when it all came back, which lifts it. */
export async function checkPlan(): Promise<boolean> {
  watch();
  const again = [...refused];
  await queryClient.refetchQueries({ predicate: (q) => again.includes(q.queryHash) });
  // An answer lifts it (onEvent), a refusal keeps it. With nothing left to read, the reload after it reads anew.
  if (again.length === 0) hold(null);
  return held === null;
}
