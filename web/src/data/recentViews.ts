import { useSyncExternalStore } from 'react';
import { readPref, removePref, usePref, writePref } from '@/prefs';

/* Recent views: the last views a person opened, newest first, for the top bar's Recent views button (shell/Recent.tsx)
   and for Search with nothing typed. Kept on this device, for this person in this company, under one versioned key:
   bad or newer data reads as empty, and newer data is never written over. Each tab writes the same key, so a view
   opened in one tab is listed in the next. Sign-out clears it.

   A view is a page whatever its filters (its address as last seen is kept, so the row opens it that way), or a task
   whatever page it is open over. A view the app learns is gone (deleted here, not found, no access) is dropped and is
   not listed again in this load. */

export interface RecentView {
  /** `page:<path>` or `task:<number>` (viewKey; a task's id where it has no number). */
  key: string;
  /** The address as last seen there, filters and all. */
  to: string;
  /** The trail as it read, without the company. */
  trail: string[];
}

const KEY = 'recent-views.v1';
const V = 1;
/** How many rows the list shows. */
export const RECENT_VIEWS = 12;
// the open view is kept too, never shown: one more than the list shows
const KEEP = RECENT_VIEWS + 1;
// addresses that only pass on to another page
const HOPS = new Set(['page:/', 'page:/library']);

interface Stored {
  v: number;
  who: string;
  rows: RecentView[];
}

const isRow = (r: unknown): r is RecentView => {
  const o = r as RecentView | null;
  return (
    !!o &&
    typeof o.key === 'string' &&
    typeof o.to === 'string' &&
    o.to.startsWith('/') &&
    Array.isArray(o.trail) &&
    o.trail.length > 0 &&
    o.trail.every((s) => typeof s === 'string')
  );
};

function rowsOf(raw: unknown, who: string): RecentView[] {
  const s = raw as Partial<Stored> | null;
  if (!s || typeof s !== 'object' || s.v !== V || s.who !== who || !Array.isArray(s.rows) || !s.rows.every(isRow)) return [];
  return s.rows.slice(0, KEEP);
}
const newer = (raw: unknown) => typeof (raw as Partial<Stored> | null)?.v === 'number' && (raw as Stored).v > V;

/* Who the list is for (company and person) and the view open now: the root sets them. */
let here = { who: '', open: '' };
const heard = new Set<() => void>();
const listen = (on: () => void) => {
  heard.add(on);
  return () => heard.delete(on);
};

/** The person (`<company>\0<person id>`) and the view open now (viewKey), as the root knows them. */
export function setRecentHere(who: string, open: string): void {
  if (who === here.who && open === here.open) return;
  const known = who && who !== here.who;
  here = { who, open };
  // views found gone (or failing) before it was known whose list this is leave it now
  if (known && (gone.size || fault)) write(who, (rows) => (rows.some(out) ? rows.filter((r) => !out(r)) : rows));
  for (const on of heard) on();
}

const out = (r: RecentView) => gone.has(r.key) || r.key === fault || (r.key.startsWith('task:') && gone.has(`task:${taskIn(r.to)}`));

/** The address's path as one key: without the app's base and a closing slash. */
export const pageKey = (path: string): string => `page:${path.replace(/^\/next(?=\/|$)/, '').replace(/(.)\/+$/, '$1') || '/'}`;

/** Which view an address shows: the task open over the page, else the page, whatever its filters. */
export function viewKey(path: string, search: Record<string, unknown>): string {
  const task = search.task;
  return typeof task === 'string' || typeof task === 'number' ? `task:${task}` : pageKey(path);
}

const gone = new Set<string>();

/** Everything stored for this person, newest first, the open view included. */
export function readRecentViews(who: string): RecentView[] {
  return rowsOf(readPref<unknown>(KEY, null), who);
}

function write(who: string, change: (rows: RecentView[]) => RecentView[]): void {
  const raw = readPref<unknown>(KEY, null);
  if (!who || newer(raw)) return;
  const rows = rowsOf(raw, who);
  const next = change(rows);
  if (next !== rows) writePref<Stored>(KEY, { v: V, who, rows: next.slice(0, KEEP) });
}

/** The person opened a view: it goes to the top, once. */
export function noteView(view: RecentView, who = here.who): void {
  if (gone.has(view.key) || HOPS.has(view.key) || !isRow(view)) return;
  write(who, (rows) => {
    const top = rows[0];
    if (top && top.key === view.key && top.to === view.to && top.trail.join('\0') === view.trail.join('\0')) return rows;
    return [view, ...rows.filter((r) => r.key !== view.key)];
  });
}

/** A view that is gone (deleted, not found, no access): dropped, and not listed again in this load. */
export function forgetView(key: string, who = here.who): void {
  gone.add(key);
  drop(key, who);
}

function drop(key: string, who: string): void {
  write(who, (rows) => (rows.some((r) => r.key === key) ? rows.filter((r) => r.key !== key) : rows));
}

/** A task that is gone, by any of the ways its address names it (its number, its id). */
export function forgetTask(...names: readonly (string | number | null | undefined)[]): void {
  const said = names.filter((n) => n != null && n !== '').map(String);
  // a row is keyed by the task's number, but its address may name the task by its id
  for (const r of readRecentViews(here.who)) if (said.includes(taskIn(r.to))) forgetView(r.key);
  for (const n of said) forgetView(`task:${n}`);
}

const taskIn = (to: string): string => new URL(to, 'http://x').searchParams.get('task') ?? '';

/* The page whose route failed (PageFault): no visit to it is a view while it shows its fault. Read again and drawn, it
   is one again. */
let fault = '';
const faults = new Set<() => void>();

/** The page (pageKey) shows a fault: it is no recent view, and leaves the list, until it draws (`on` false). */
export function pageFaulted(key: string, on: boolean, who = here.who): void {
  if (on) drop(key, who);
  else if (fault !== key) return;
  fault = on ? key : '';
  for (const f of faults) f();
}

/** The page that shows a fault now, if any: read where a visit is noted, after the page's own effects. */
export const faultedPage = (): string => fault;

/** The same, as state, so a page drawn again after its fault is noted then. */
export function useFaultedPage(): string {
  return useSyncExternalStore(
    (on) => {
      faults.add(on);
      return () => faults.delete(on);
    },
    () => fault,
  );
}

/** Sign-out: nothing is left on this device. */
export function forgetRecentViews(): void {
  removePref(KEY);
  gone.clear();
}

const shown = (rows: RecentView[], open: string) => rows.filter((r) => r.key !== open && !gone.has(r.key)).slice(0, RECENT_VIEWS);

/** The rows the list shows: the views before the open one, newest first, at most RECENT_VIEWS. */
export function useRecentViews(): RecentView[] {
  const now = useSyncExternalStore(listen, () => here);
  const [raw] = usePref<unknown>(KEY, null);
  return shown(rowsOf(raw, now.who), now.open);
}

/** The same rows, read once (Search with nothing typed). */
export const recentViewsNow = (): RecentView[] => shown(readRecentViews(here.who), here.open);

/* The open task's name and the number it is known by (its id where it has none), which only the task knows: its trail
   ends with its name, and its row is keyed by that number whichever way its address named it. */
let task = { task: '', name: '', as: '' };
const named = new Set<() => void>();

/** The task open over the page (as its address names it) has its name and number (TaskModal). */
export function nameOpenTask(open: string, name: string, as: string = open): void {
  if (open === task.task && name === task.name && as === task.as) return;
  task = { task: open, name, as };
  for (const on of named) on();
}

const NONE = { name: '', as: '' };

/** The open task's name and number, once the task has said them. */
export function useOpenTask(open: string): { name: string; as: string } {
  return useSyncExternalStore(
    (on) => {
      named.add(on);
      return () => named.delete(on);
    },
    () => (open && task.task === open ? task : NONE),
  );
}
