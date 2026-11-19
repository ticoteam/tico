import type { IconName } from '@/kit/Icon';

/* What a feature adds to search: `features/<area>/search.ts` exports `defineSearch({...})`. Every hit is an action
   with its input, so choosing one goes through the same dispatch as a click (via 'search'). */

/** Before the title: a face (the mock's `.av av-22`) or a kind icon (the mock's `.tl-kind`). Plain data, so a hit can
    be remembered in Recent. */
export type Lead = { icon: IconName } | { face: { initials: string; hue: number; bot?: boolean; name?: string } };

export interface SearchHit {
  /** Stable for the thing it names ('task:412'); Recent keeps one of each. */
  key: string;
  title: string;
  /** The right column: a stage, a time, a team. */
  meta?: string;
  lead?: Lead;
  /** The action choosing it runs. */
  run: { id: string; input: unknown };
}

export interface SearchProvider {
  /** The group's name: "Tasks", "Threads", "Files". */
  group: string;
  /** Lower comes first. */
  order: number;
  /** It asks the server: the dialog waits for a pause in the typing before calling it. */
  remote?: boolean;
  /** From the cache where it can; a server search should go through the one cache too. */
  find(q: string, signal: AbortSignal): SearchHit[] | Promise<SearchHit[]>;
}

/* Its own module, with nothing heavy behind it: every search.ts imports it, and providers.ts loads those. */
export const defined = new WeakSet<object>();

export function defineSearch(p: SearchProvider): SearchProvider {
  defined.add(p);
  return p;
}

