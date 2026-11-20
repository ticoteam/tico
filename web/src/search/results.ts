import * as z from 'zod/mini';
import type { AnyAction, Ctx } from '@/actions';
import type { SearchHit, SearchProvider } from './providers';

export interface Group {
  name: string;
  hits: SearchHit[];
  /** The provider's boost (define.ts). */
  boost?: number;
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

const esc = (w: string) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
/** Leading marks a name may carry ("#concierge", "#412"), not part of what is typed. */
const bare = (t: string) => t.replace(/^[^\p{L}\p{N}]+/u, '');

/**
 * How well a text answers the words, for ranking: 0 when a word is missing. The whole text, then its start, then the
 * start of a word for every word, then anywhere; and among those a short text the words mostly fill beats a long one,
 * so a teammate, a page or a setting by its name comes before a task that mentions it.
 */
export function score(text: string, q: string): number {
  const t = text.toLowerCase().trim();
  const ws = words(q);
  if (!ws.length || !ws.every((w) => t.includes(w))) return 0;
  const phrase = bare(ws.join(' '));
  const name = bare(t);
  const atWord = (w: string) => new RegExp(`(^|[^\\p{L}\\p{N}])${esc(bare(w) || w)}`, 'u').test(t);
  const base = name === phrase ? 1000 : name.startsWith(phrase) ? 600 : ws.every(atWord) ? 400 : 200;
  return base + Math.round(400 * Math.min(1, phrase.length / Math.max(1, name.length)));
}

/** A hit's score: by its title or its own name, whichever answers better. */
export const hitScore = (h: SearchHit, q: string): number => Math.max(score(h.title, q), h.name ? score(h.name, q) : 0);

const runKey = (h: SearchHit) => `${h.run.id}:${JSON.stringify(h.run.input)}`;

/**
 * The groups, best answer first: each by its best hit, ties in provider order (Actions last). Nothing shows twice: a
 * thing two groups find stays in the better one, and an action that only opens a thing already found ("Open
 * Engineering" beside the Engineering pipeline) is left out.
 */
export function rank(groups: Group[], q: string): Group[] {
  const found = groups.filter((g) => g.name !== ACTIONS);
  const runs = new Set(found.flatMap((g) => g.hits.map(runKey)));
  const opens = new Set(found.flatMap((g) => g.hits.map((h) => `open ${h.title.toLowerCase()}`)));
  const out = groups
    .map((g) => (g.name === ACTIONS ? { ...g, hits: g.hits.filter((h) => !runs.has(runKey(h)) && !opens.has(h.title.toLowerCase())) } : g))
    .filter((g) => g.hits.length);
  const best = new Map(out.map((g) => [g, Math.max(...g.hits.map((h) => hitScore(h, q))) + (g.boost ?? 0)]));
  const seen = new Set<string>();
  return out
    .map((g, i) => ({ g, i }))
    .sort((a, b) => best.get(b.g)! - best.get(a.g)! || a.i - b.i)
    .map(({ g }) => ({ ...g, hits: g.hits.filter((h) => !seen.has(runKey(h)) && !!seen.add(runKey(h))) }))
    .filter((g) => g.hits.length);
}

/** The text cut into plain and matched runs, for `<mark>`. */
export function highlight(text: string, q: string): { text: string; hit: boolean }[] {
  const ws = words(q).map(esc);
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
    const rows = a.offer ? a.offer(ctx) : z.safeParse(a.input, {}).success ? [{ input: {} as unknown }] : [];
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

/** Providers that share a group's name show as one group, in provider order: Threads by name from the list at once,
    then by what was said in them when the server answers. */
function joined(groups: Group[]): Group[] {
  const byName = new Map<string, Group>();
  for (const g of groups) {
    const had = byName.get(g.name);
    if (!had) byName.set(g.name, g);
    else {
      const boost = Math.max(had.boost ?? 0, g.boost ?? 0);
      byName.set(g.name, { name: g.name, hits: [...had.hits, ...g.hits], ...(boost ? { boost } : {}) });
    }
  }
  return [...byName.values()];
}

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
  const groups = () => rank([...joined(from.flatMap((p) => found.get(p) ?? [])), ...(acts.hits.length ? [acts] : [])], q);
  if (acts.hits.length && !signal.aborted) early?.(groups());
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
      // a list searched here, best first; a server's own order is its ranking and stays
      if (!p.remote) hits = hits.map((h, i) => ({ h, i, s: hitScore(h, q) })).sort((a, b) => b.s - a.s || a.i - b.i).map(({ h }) => h);
      found.set(p, { name: p.group, hits, ...(p.boost ? { boost: p.boost } : {}) });
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
