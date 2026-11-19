import { readPref } from '@/prefs';
import type { Pipeline } from './queries';
import { LAST_PREF } from './search';

/** Where /tasks lands: the pipeline opened last, while it still exists, else the first one. Null when there is none. */
export function firstPipeline(pipelines: readonly Pipeline[]): string | null {
  const last = readPref<string | null>(LAST_PREF, null);
  return pipelines.find((p) => p.id === last)?.id ?? pipelines[0]?.id ?? null;
}
