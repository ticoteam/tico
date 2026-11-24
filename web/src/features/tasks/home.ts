import { readPref } from '@/prefs';
import type { Pipeline } from './queries';
import { LAST_PREF } from './search';

/** The pipeline opened last, while it still exists, else the first one (New task from search). Null when there is none. */
export function firstPipeline(pipelines: readonly Pipeline[]): string | null {
  const last = readPref<string | null>(LAST_PREF, null);
  return pipelines.find((p) => p.id === last)?.id ?? pipelines[0]?.id ?? null;
}
