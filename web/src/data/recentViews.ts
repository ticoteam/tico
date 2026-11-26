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
  /** `page:<path>` or `task:<task>` (viewKey). */
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
  here = { who, open };
  for (const on of heard) on();
}

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
  write(who, (rows) => (rows.some((r) => r.key === key) ? rows.filter((r) => r.key !== key) : rows));
}

/** A task that is gone, by any of the ways its address names it (its number, its id). */
export function forgetTask(...names: readonly (string | number | null | undefined)[]): void {
  for (const n of names) if (n != null && n !== '') forgetView(`task:${n}`);
}

/** Sign-out: nothing is left on this device. */
export function forgetRecentViews(): void {
  removePref(KEY);
  gone.clear();
}

const shown = (rows: RecentView[], open: string) => rows.filter((r) => r.key !== open).slice(0, RECENT_VIEWS);

/** The rows the list shows: the views before the open one, newest first, at most RECENT_VIEWS. */
export function useRecentViews(): RecentView[] {
  const now = useSyncExternalStore(listen, () => here);
  const [raw] = usePref<unknown>(KEY, null);
  return shown(rowsOf(raw, now.who), now.open);
}

/** The same rows, read once (Search with nothing typed). */
export const recentViewsNow = (): RecentView[] => shown(readRecentViews(here.who), here.open);

/* The open task's name, which only the task knows: its trail ends with it. */
let task = { task: '', name: '' };
const named = new Set<() => void>();

/** The task open over the page has its name (TaskModal). */
export function nameOpenTask(open: string, name: string): void {
  if (open === task.task && name === task.name) return;
  task = { task: open, name };
  for (const on of named) on();
}

/** The open task's name, once the task has said it. */
export function useOpenTaskName(open: string): string {
  return useSyncExternalStore(
    (on) => {
      named.add(on);
      return () => named.delete(on);
    },
    () => (open && task.task === open ? task.name : ''),
  );
}
