import type { AnyAction, Ctx } from '@/actions';
import type { SearchHit, SearchProvider } from './providers';

export interface Group {
  name: string;
  hits: SearchHit[];
}

const RECENT_KEY = 'tico-next:search-recent';
const RECENT_MAX = 6;
export const RECENT = 'Recent';
export const ACTIONS = 'Actions';

const words = (q: string) => q.toLowerCase().split(/\s+/).filter(Boolean);

/** Every word of the query is somewhere in the text. */
export function matches(text: string, q: string): boolean {
  const t = text.toLowerCase();
  return words(q).every((w) => t.includes(w));
}

/** The text cut into plain and matched runs, for `<mark>`. */
export function highlight(text: string, q: string): { text: string; hit: boolean }[] {
  const ws = words(q).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  if (!ws.length) return [{ text, hit: false }];
  return text
    .split(new RegExp(`(${ws.join('|')})`, 'gi'))
    .filter(Boolean)
    .map((part) => ({ text: part, hit: ws.some((w) => new RegExp(`^${w}$`, 'i').test(part)) }));
}

/** Rows the actions themselves offer: each `offer()` row, or the action alone when it needs no input. */
export function actionHits(q: string, actions: Iterable<AnyAction>, ctx: Ctx | null): SearchHit[] {
  if (!ctx) return [];
  const out: SearchHit[] = [];
  for (const a of actions) {
    if (a.id === 'search.open') continue;
    const rows = a.offer ? a.offer(ctx) : a.input.parse({}).ok ? [{ input: {} as unknown }] : [];
    for (const row of rows) {
      const title = row.title ?? a.title;
      if (!matches(title, q)) continue;
      try {
        if (a.can && !a.can(ctx, row.input)) continue;
      } catch {
        continue;
      }
      out.push({ key: `${a.id}:${JSON.stringify(row.input)}`, title, run: { id: a.id, input: row.input } });
    }
  }
  return out;
}

/** How long the typing must pause before the server is asked. */
export const REMOTE_WAIT_MS = 150;

/** Resolves after `ms`, or false at once when the person types on. */
const pause = (ms: number, signal: AbortSignal) =>
  new Promise<boolean>((done) => {
    if (signal.aborted) return done(false);
    const stop = () => {
      clearTimeout(t);
      done(false);
    };
    const t = setTimeout(() => {
      signal.removeEventListener('abort', stop);
      done(true);
    }, ms);
    signal.addEventListener('abort', stop, { once: true });
  });

/**
 * The groups for a query, in provider order, with actions last. Publish each group as it arrives: a provider
 * searching a list can have a cold cache, and its read should hold up neither the local results nor the search
 * debounce. A provider that fails drops out quietly.
 */
export async function gather(
  q: string,
  from: SearchProvider[],
  actions: Iterable<AnyAction>,
  ctx: Ctx | null,
  signal: AbortSignal,
  early?: (groups: Group[]) => void,
): Promise<Group[]> {
  if (!q.trim()) {
    const recent = readRecent();
    return recent.length ? [{ name: RECENT, hits: recent }] : [];
  }
  const wait = from.some((p) => p.remote) ? pause(REMOTE_WAIT_MS, signal) : undefined;
  const found = new Map<SearchProvider, Group>();
  const acts = { name: ACTIONS, hits: actionHits(q, actions, ctx) };
  const groups = () => [...from.flatMap((p) => found.get(p) ?? []), ...(acts.hits.length ? [acts] : [])];
  if (acts.hits.length && !signal.aborted) early?.([acts]);
  await Promise.all(
    from.map(async (p) => {
      if (p.remote && !(await wait)) return;
      if (signal.aborted) return;
      let hits: SearchHit[];
      try {
        hits = await p.find(q, signal);
      } catch {
        return;
      }
      if (!hits.length || signal.aborted) return;
      found.set(p, { name: p.group, hits });
      early?.(groups());
    }),
  );
  return groups();
}

export function readRecent(): SearchHit[] {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]');
    return Array.isArray(v) ? (v as SearchHit[]).filter((h) => typeof h?.key === 'string' && typeof h.run?.id === 'string') : [];
  } catch {
    return [];
  }
}

export function remember(hit: SearchHit): void {
  try {
    const next = [hit, ...readRecent().filter((h) => h.key !== hit.key)].slice(0, RECENT_MAX);
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    // private window or blocked storage: Recent just stays empty
  }
}
