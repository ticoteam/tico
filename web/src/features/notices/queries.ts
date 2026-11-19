import type { Query } from '@tanstack/react-query';
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

const refused = (q: Query): boolean => q.state.status === 'error' && lockOf(q.state.error) !== null;
const same = (a: PlanLockState | null, b: PlanLockState | null) => a === b || (!!a && !!b && a.reason === b.reason && a.billingUrl === b.billingUrl);

let held: PlanLockState | null = null;

/** The newest lock among the reads the cache holds, or null: the company is open (or not a cloud one). */
export function currentLock(): PlanLockState | null {
  let latest: Query | undefined;
  for (const q of queryClient.getQueryCache().getAll()) {
    if (refused(q) && (!latest || q.state.errorUpdatedAt > latest.state.errorUpdatedAt)) latest = q;
  }
  const next = latest ? lockOf(latest.state.error) : null;
  if (!same(next, held)) held = next;
  return held;
}

const subscribe = (fn: () => void) => queryClient.getQueryCache().subscribe(fn);

/** For the lock: re-reads as the cache changes. */
export const usePlanLock = (): PlanLockState | null => useSyncExternalStore(subscribe, currentLock, () => null);

/** Read again everything the lock refused. True when it all came back: the plan is paid and the app is open again. */
export async function checkPlan(): Promise<boolean> {
  await queryClient.refetchQueries({ predicate: refused });
  return currentLock() === null;
}
