import * as z from 'zod/mini';
import { defineAction } from '@/actions';
import { meQuery, queryClient } from '@/data';
import { BILLING_URL, EXPORT_PATH, checkPlan, currentLock } from './queries';

/* What a person can do from the notices that stand over every page: a locked plan, and the app's files failing to
   load. Paying, downloading the company's files and reloading are the person's own presses, never Tico's. */

const LOCK = { kind: 'dialog', id: 'plan-lock' } as const;
const isOwner = () => queryClient.getQueryData(meQuery.queryKey)?.role === 'owner';

export const openBilling = defineAction({
  id: 'notices.openBilling',
  title: 'Open billing',
  kind: 'view',
  tico: false,
  input: z.strictObject({}),
  // HQ's page, beside the app: paying there and coming back to Check again opens the app.
  run: () => void window.open(currentLock()?.billingUrl ?? BILLING_URL, '_blank', 'noopener,noreferrer'),
  target: () => LOCK,
});

export const checkAgain = defineAction({
  id: 'notices.checkPlan',
  title: 'Check the plan again',
  kind: 'view',
  input: z.strictObject({}),
  run: async () => {
    if (!(await checkPlan())) throw Object.assign(new Error('the plan still needs attention'), { code: 'still_locked' });
    // What was open behind the lock was refused while it lasted (ui/app/cloud-access.ts): start clean.
    location.reload();
  },
  did: () => 'Checked the plan',
  target: () => LOCK,
});

export const downloadFiles = defineAction({
  id: 'notices.downloadFiles',
  title: 'Download all files',
  kind: 'view',
  tico: false,
  input: z.strictObject({}),
  // Only the owner may download every file (backend/tico_storage.py storage_export).
  can: () => isOwner(),
  // The server answers with an attachment: the browser saves it and the page stays.
  run: () => location.assign(EXPORT_PATH),
  target: () => LOCK,
});

export const reload = defineAction({
  id: 'notices.reload',
  title: 'Reload Tico',
  kind: 'view',
  tico: false,
  input: z.strictObject({}),
  run: () => location.reload(),
});
