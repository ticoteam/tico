import type { ModelSubscriptionList } from '@/api/schema';
import type { SetupRecord } from './queries';

/* The first run's steps, held in the address (`?setup=<step>`) so Back walks them and a reload resumes where it was.
   `path` chooses who does the setup; `paste` is the key behind Connect and counts as the model step. `github` stands
   alone: Connect GitHub, opened from Tools or by Tico, outside the three counted steps. */
export const STEPS = ['path', 'name', 'model', 'paste', 'team', 'github'] as const;
export type SetupStep = (typeof STEPS)[number];

export const setupStep = (v: unknown): SetupStep | null => (STEPS.includes(v as SetupStep) ? (v as SetupStep) : null);

/** Where "Set it up myself" picks up: the first step not yet done. */
export function resumeStep(record: SetupRecord | undefined, subs: ModelSubscriptionList | undefined): SetupStep {
  if (!record?.names.company_name.trim()) return 'name';
  if (!subs?.subscriptions.length) return 'model';
  return 'team';
}

/** The step before this one, for Back. */
export const stepBefore = (step: SetupStep): SetupStep => (step === 'team' || step === 'paste' ? 'model' : step === 'model' ? 'name' : 'path');

/** The three steps the bar counts; the path choice stands before them. */
export const stepNumber = (step: SetupStep): number | null => (step === 'name' ? 1 : step === 'model' || step === 'paste' ? 2 : step === 'team' ? 3 : null);
