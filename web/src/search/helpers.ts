import type { FetchQueryOptions, QueryKey } from '@tanstack/react-query';
import { queryClient, type Actor } from '@/data';
import type { Lead } from './define';

/* What the providers share. Features import it as `@/search/helpers` (with `@/search/define` and `@/search/results`),
   not `@/search`: the index loads providers.ts, whose glob loads every feature's search.ts, so a search.ts that
   imported the index would load itself half-made whenever a route reached it first. */

/** Hits a group shows at most. */
export const HITS = 8;

/** A server search through the one cache. When the person types on, the abort cancels the read. */
export async function fetchFor<T, K extends QueryKey>(options: FetchQueryOptions<T, Error, T, K>, signal: AbortSignal): Promise<T> {
  signal.throwIfAborted();
  const stop = () => void queryClient.cancelQueries({ queryKey: options.queryKey, exact: true });
  signal.addEventListener('abort', stop, { once: true });
  try {
    return await queryClient.fetchQuery(options);
  } finally {
    signal.removeEventListener('abort', stop);
  }
}

const initials = (name: string) => {
  const w = name.trim().split(/\s+/).filter(Boolean);
  return (w.length > 1 ? `${w[0]![0]}${w[1]![0]}` : (w[0] ?? '').slice(0, 2)).toUpperCase();
};

/** A teammate's face for a row's lead. */
export const faceOf = (a: Actor): Lead => ({ face: { initials: initials(a.name), hue: a.hue ?? 0, bot: a.kind === 'agent', name: a.name } });
