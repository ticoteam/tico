/* The places the shell keeps as the person moves: each board's search (the list beside a task draws its pipeline's
   tasks as the board did), and the list beside the open task, for the row below it once it is answered. */

import { pageSearch } from './taskAddress';

/** What closing a task needs of the router: the history (to step back to the page it opened over) and a plain move. */
export interface Steps {
  history: { location: { state: unknown }; back: () => void };
  state: { location: { pathname: string; search: unknown } };
  navigate: (to: { to: string; search: Record<string, unknown>; replace?: boolean; resetScroll?: boolean }) => unknown;
}

/** The mark on the history step that opened a task over a page (router.tsx): Back from it is that page. */
export const OVER_PAGE = 'overPage';

const BOARD = /^\/tasks\/([^/]+)\/?$/;
const boards = new Map<string, Record<string, unknown>>();

/** The shell, at each address it shows: the path without the app's base, the search as written and as read. */
export function notePlace(path: string, _searchStr: string, search: Record<string, unknown>, _state?: unknown): void {
  const board = BOARD.exec(path);
  // the board's own words: never a task open over it
  if (board?.[1]) boards.set(decodeURIComponent(board[1]), pageSearch(search));
  heard.forEach((on) => on());
}

const NONE: Record<string, unknown> = Object.freeze({});

/** The search a pipeline's board was last shown with (its view, filters and sort), or none; the same object until it
    changes, so a reader can hear it (`hearPlaces`). */
export const boardSearch = (pipeline: string): Record<string, unknown> => boards.get(pipeline) ?? NONE;

/** The list beside an open task changes its search, filters or sort (`tasks.filter`, `tasks.sort` there): kept as the
    board's, which that list draws, until the board is shown again with its own. A null, '' or false clears one. */
export function changeBoardSearch(pipeline: string, change: Record<string, unknown>): void {
  const next: Record<string, unknown> = { ...boardSearch(pipeline) };
  for (const [k, v] of Object.entries(change)) {
    if (v === undefined) continue;
    if (v === null || v === '' || v === false) delete next[k];
    else next[k] = v;
  }
  boards.set(pipeline, next);
  heard.forEach((on) => on());
}

/** Closing the task open over a page: the page as it was. Opened over it in the app, the history steps back (Forward
    opens it again); from a link or a reload, the same page with the task taken out of its address. */
export function closeTask(r: Steps): void {
  const state = r.history.location.state as Record<string, unknown> | null | undefined;
  if (state?.[OVER_PAGE]) return r.history.back();
  const { pathname, search } = r.state.location;
  void r.navigate({ to: pathname, search: pageSearch((search ?? {}) as Record<string, unknown>), replace: true, resetScroll: false });
}

/** Needs you's name among the lists a task opens from: one list across every pipeline, and the list of a task opened
    with none named (a link, a notice). No pipeline's id has a colon, so none is named this. */
export const NEEDS_YOU = ':needs-you';

// who draws by the places noted (the board's search, the row the person is on): told after each change
const heard = new Set<() => void>();

/** Hear each address noted (what `boardSearch` says may have changed); the returned function stops. */
export function hearPlaces(on: () => void): () => void {
  heard.add(on);
  return () => void heard.delete(on);
}

/** A row of the list beside an open task: its task's id, and its number as the address names it. */
export interface ListRow {
  id: string;
  n: string;
}

/* The list beside an open task while it shows: its rows in the order drawn, `held` as they were when an answer was
   given on the open task. */
let listed: { pipeline: string; open: string; rows: ListRow[]; held?: ListRow[] } | null = null;

/** The list beside an open task (its pipeline, or NEEDS_YOU; the open task as the address names it), as it draws its
    rows in order (a folded stage's are not drawn); null while it does not show. */
export function noteList(list: { pipeline: string; open: string; rows: ListRow[] } | null): void {
  const same = !!list && listed?.pipeline === list.pipeline && listed.open === list.open;
  listed = list && { ...list, ...(same && listed?.held && { held: listed.held }) };
  heard.forEach((on) => on());
}

/** How many rows the list beside the open task draws besides it (a phone's "N more"). */
export const othersListed = (): number => (listed ? listed.rows.filter((r) => r.n !== listed!.open && r.id !== listed!.open).length : 0);

/** An answer is being given on the open task: the row below it is the one below it now, wherever the answer moves it. */
export const holdList = (): void => void (listed && (listed.held = listed.rows));

/** The pipeline whose list shows beside the open task, or null (none, or Needs you's). */
export const listShown = (): string | null => (listed && listed.pipeline !== NEEDS_YOU ? listed.pipeline : null);

/** The task below `id` in the list beside it as an answer found it (past rows that have left the list since): its
    number. Null at the list's last row, or while `list` (the one the task was opened from) is not the one drawn. */
export function nextInList(id: string, list: string): string | null {
  if (!listed || listed.pipeline !== list) return null;
  const order = listed.held ?? listed.rows;
  const now = new Set(listed.rows.map((r) => r.id));
  const at = order.findIndex((r) => r.id === id);
  const next = at < 0 ? undefined : order.slice(at + 1).find((r) => r.id !== id && now.has(r.id));
  return next?.n ?? null;
}

/* A list Tico orders (Needs you), as it was last drawn: the row the person is on (`onRow`: the one they last pressed or
   focused, or the task open from it), and the order drawn. A new order moves only the rows below the one they are on,
   wherever the list is drawn (Tasks, beside the task) or read for the row below (NeedsYouOrder). */
let onRow: string | null = null;
let drawn: { from?: unknown; order: string[]; latest: string[]; held: string | null } = { order: [], latest: [], held: null };

/** The row the person is on in Needs you, or null. */
export const heldRow = (): string | null => onRow;

/** The person is on this row of Needs you (null: on none). */
export function holdRow(id: string | null): void {
  if (onRow === id) return;
  onRow = id;
  heard.forEach((on) => on());
}

/** `next`, with the rows from the top of `shown` down to `held` kept where they are: a new order moves only the rows
    below it. Rows that left the list are gone from the top part too. */
export function keepTop(shown: readonly string[], next: readonly string[], held: string | null): string[] {
  const at = held ? shown.indexOf(held) : -1;
  if (!held || at < 0 || !next.includes(held)) return [...next];
  const still = new Set(next);
  const top = shown.slice(0, at + 1).filter((id) => still.has(id));
  const kept = new Set(top);
  return [...top, ...next.filter((id) => !kept.has(id))];
}

/** The order to draw: `latest` (read from `from`, a new read each time the list changes) with the rows down to the one
    the person is on kept from the order drawn last; the same for every reader of the same read and row. */
export function keptOrder(from: unknown, latest: () => string[], held: string | null): string[] {
  if (drawn.from !== from) {
    const next = latest();
    drawn = { from, order: held ? keepTop(drawn.order, next, held) : next, latest: next, held };
  } else if (held !== drawn.held) {
    // The person moved: the rows down to the new one stay, the rest take the latest order (all of it, let go).
    drawn = { ...drawn, order: held ? keepTop(drawn.order, drawn.latest, held) : drawn.latest, held };
  }
  return drawn.order;
}

/** Forgets every place (tests). */
export function forgetPlaces(): void {
  boards.clear();
  listed = null;
  onRow = null;
  drawn = { order: [], latest: [], held: null };
}
