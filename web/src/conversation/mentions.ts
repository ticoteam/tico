import type { Actor } from '@/data';

/** The most names the @ list shows at once. */
export const MENTION_MAX = 8;

/** The @word the caret is in: where its @ sits and what follows it. Null when the caret is in none (an email's @). */
export function mentionAt(text: string, caret: number): { start: number; query: string } | null {
  const m = /(?:^|\s)@([^\s@]*)$/.exec(text.slice(0, caret));
  return m ? { start: caret - m[1]!.length - 1, query: m[1]! } : null;
}

/** A pick as written in the message: the name `mentions` in backend/threads.py reads, an agent's slug or a person's id
    (an agent with no slug, or a person whose id would not read as one word, goes by name). */
export function mentionText(a: Actor): string {
  if (a.kind === 'agent') return `@${a.slug || a.name}`;
  const id = a.id.slice(a.id.indexOf(':') + 1);
  return /^[\w.-]+$/.test(id) ? `@${id}` : `@${a.name}`;
}

const words = (s: string) => s.toLowerCase().split(/[\s._-]+/).filter(Boolean);

/** How well `query` names `a`: 0 a name or handle starting with it, 1 a word in one starting with it, 2 inside one;
    null not at all. */
function rank(a: Actor, query: string): number | null {
  if (!query) return 0;
  const names = [a.name.toLowerCase(), mentionText(a).slice(1).toLowerCase()];
  if (names.some((n) => n.startsWith(query))) return 0;
  if (names.some((n) => words(n).some((w) => w.startsWith(query)))) return 1;
  if (names.some((n) => n.includes(query))) return 2;
  return null;
}

/** Whom "@query" may mean, best first: the `first` ids (a task's people and agents) ahead of everyone else, then the
    closer match, then by name. Never the writer, never a retired agent. */
export function mentionMatches(actors: readonly Actor[], query: string, me: string, first: readonly string[] = [], only?: readonly string[]): Actor[] {
  const q = query.toLowerCase();
  const self = new Set([me, `human:${me}`]);
  const order = new Map(first.map((id, i) => [id, i]));
  const allowed = only && new Set(only);
  return actors
    .filter((a) => !self.has(a.id) && a.lifecycle !== 'archived' && (!allowed || allowed.has(a.id)))
    .map((a) => ({ a, r: rank(a, q), f: order.get(a.id) ?? (a.slug !== undefined ? order.get(`bot:${a.slug}`) : undefined) }))
    .filter((m): m is { a: Actor; r: number; f: number | undefined } => m.r !== null)
    .sort((x, y) => (x.f ?? Infinity) - (y.f ?? Infinity) || x.r - y.r || x.a.name.localeCompare(y.a.name))
    .slice(0, MENTION_MAX)
    .map((m) => m.a);
}
