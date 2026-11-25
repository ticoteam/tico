/* A task opens over the page you are on (plan: the task modal): that page's address, plus `task=<n>`, the list it was
   opened from (`list`: a pipeline's id, a teammate's id, `search:<words>`; none is Needs you), and the task's own words
   (its tab, an ask, a version, a session) as `task.<word>`, so they never meet the page's own (an agent page's `tab`, a
   doc's `v`) and closing it takes exactly those away. The task's old addresses (`/task/<n>`, `/tasks/<p>/<n>/<tab>`,
   `/flow/<n>`) stay its links: a link, a bookmark or a new tab still names them, and opening one in the app opens the
   modal instead. Plain functions: the router, the old addresses' routes and the modal read and write through them. */

/** The task's own words, as its old address named them (its tab in the path, the rest in the search). */
export interface TaskWords {
  tab?: string;
  ask?: string;
  v?: number;
  vs?: number;
  part?: number;
  session?: string;
  file?: string;
  full?: string;
}

/** A task to open: its number (or id), the list it opens from, its own words. */
export interface TaskOpen {
  task: string;
  list?: string;
  words?: TaskWords;
}

const PREFIX = 'task.';
const NUMBERS = ['v', 'vs', 'part'] as const;
const TEXTS = ['tab', 'ask', 'session', 'file', 'full'] as const;

/** Whether a key of the address is the open task's (its number, its list, its words). */
export const isTaskKey = (key: string): boolean => key === 'task' || key === 'list' || key.startsWith(PREFIX);

const text = (v: unknown): string | undefined => (typeof v === 'number' && Number.isFinite(v) ? String(v) : typeof v === 'string' && v ? v : undefined);
const whole = (v: unknown, min: number): number | undefined => {
  const n = typeof v === 'number' ? v : typeof v === 'string' && /^\d+$/.test(v) ? Number(v) : NaN;
  return Number.isInteger(n) && n >= min ? n : undefined;
};

/** The words a search names, read from `key(word)`: the ones the task knows, each its own shape, nothing else. */
function wordsOf(search: Record<string, unknown>, key: (w: string) => string): TaskWords {
  const out: TaskWords = {};
  for (const w of TEXTS) {
    const v = text(search[key(w)]);
    if (v && (w !== 'session' || v.startsWith('bot:'))) out[w] = v;
  }
  for (const w of NUMBERS) {
    // an email's files count from 0; versions from 1
    const v = whole(search[key(w)], w === 'part' ? 0 : 1);
    if (v !== undefined) out[w] = v;
  }
  return out;
}

/** The page's own search: the address without the open task. */
export function pageSearch(search: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(search).filter(([k]) => !isTaskKey(k)));
}

/** A list a task opens from: a pipeline's id (no colon), a teammate's id, or a search's words; anything else is none
    (Needs you). */
const LIST = /^(?:[^:]+|(?:human|bot):.+|search:.+)$/;

/** The task open over the page, or null. */
export function openOf(search: Record<string, unknown>): Required<Pick<TaskOpen, 'task' | 'words'>> & { list?: string } | null {
  const task = text(search.task);
  if (!task) return null;
  const list = [text(search.list)].find((l) => l && LIST.test(l));
  return { task, ...(list && { list }), words: wordsOf(search, (w) => PREFIX + w) };
}

/** A number as the router writes one (a plain `12`, not a quoted "12"); anything else as it is. */
const asWritten = (v: string): string | number => (/^[1-9]\d{0,15}$/.test(v) ? Number(v) : v);

/** The page's search with `open` over it: any task open before is gone, the page's own words stay. */
export function withOpen(search: Record<string, unknown>, open: TaskOpen): Record<string, unknown> {
  const out: Record<string, unknown> = { ...pageSearch(search), task: asWritten(open.task) };
  if (open.list) out.list = open.list;
  for (const [w, v] of Object.entries(open.words ?? {})) if (v !== undefined && v !== '') out[PREFIX + w] = v;
  return out;
}

/** One of the task's old addresses (`/task/<n>`, `/tasks/<pipeline>/<n>[/<tab>]`, `/flow/<n>`, under /next too), with
    its search: the task, its pipeline as the path named it, and its words. Null for any other address (a board, the
    pipeline editor). */
export function coldOf(path: string, search: Record<string, unknown> = {}): { task: string; pipeline?: string; words: TaskWords } | null {
  const m = /^(?:\/next)?\/(?:task\/([^/]+)|flow\/([^/]+)|tasks\/([^/]+)\/(?!edit(?:\/|$))([^/]+)(?:\/([^/]+))?)\/?$/.exec(path);
  if (!m) return null;
  const dec = (s: string) => {
    try {
      return decodeURIComponent(s);
    } catch {
      return s;
    }
  };
  const task = dec((m[1] ?? m[2] ?? m[4])!);
  const words = wordsOf(search, (w) => w);
  // the path names the tab; an old `/task/<n>?tab=` named it in the search
  if (m[5]) words.tab = dec(m[5]);
  return { task, ...(m[3] && { pipeline: dec(m[3]) }), words };
}

/** Where an old address opens when nothing is on screen yet (a cold link, a bookmark, a reload): a pipeline's address
    over its board, with that board's list; any other over Tasks, with Needs you's. The address's other words (a thread
    beside it, `chat`) stay the page's. */
export function coldPage(cold: { task: string; pipeline?: string; words: TaskWords }, search: Record<string, unknown> = {}): { to: string; search: Record<string, unknown> } {
  const p = cold.pipeline && cold.pipeline !== 'all' ? cold.pipeline : undefined;
  const words = new Set<string>([...TEXTS, ...NUMBERS]);
  const page = Object.fromEntries(Object.entries(search).filter(([k]) => !words.has(k)));
  return { to: p ? `/tasks/${encodeURIComponent(p)}` : '/tasks', search: withOpen(page, { task: cold.task, ...(p && { list: p }), words: cold.words }) };
}

/** A search update (`navigate({ to: '.', search })`) that changes the open task's words, the page's kept. */
export const changeWords =
  (change: (words: TaskWords) => TaskWords) =>
  (search: Record<string, unknown>): Record<string, unknown> => {
    const open = openOf(search);
    return open ? withOpen(search, { ...open, words: change(open.words) }) : search;
  };
