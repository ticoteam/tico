/* Where the person came from, for the way back that is not the browser's own Back: closing a task (its Back, or once
   nothing on it waits on them). The shell notes every address it shows, with its step in the history, so the way back
   steps the history back to that place (Back and Forward stay as they were) when it is still behind. */

/** An address the app showed, and its step in the history (`__TSR_index`) when known. */
export interface Place {
  href: string;
  at?: number;
}

/** What a way back needs of the router: where it is in the history, a step back, and a plain push. */
export interface Steps {
  history: { location: { state: unknown }; go: (n: number) => void; subscribe: (on: () => void) => () => void };
  navigate: (to: { href: string }) => unknown;
}

const HOME = '/tasks';
// An open task: under its pipeline (not the pipeline editor), by its number alone, or an old Flow link to one (it only
// passes on to the task, and a cold load notes it before it does).
const TASK = /^\/(tasks\/[^/]+\/(?!edit(\/|$))[^/]+|task\/[^/]+|flow\/[^/]+)(\/|$)/;
const BOARD = /^\/tasks\/([^/]+)\/?$/;

let beforeTask: Place | null = null;
const boards = new Map<string, Record<string, unknown>>();

export const isTaskPath = (path: string): boolean => TASK.test(path);

const stepOf = (state: unknown): number | undefined => {
  const at = (state as { __TSR_index?: unknown } | null | undefined)?.__TSR_index;
  return typeof at === 'number' ? at : undefined;
};

/** The shell, at each address it shows: the path without the app's base, the search as written and as read. */
export function notePlace(path: string, searchStr: string, search: Record<string, unknown>, state: unknown): void {
  if (!isTaskPath(path)) beforeTask = { href: path + searchStr, at: stepOf(state) };
  const board = BOARD.exec(path);
  if (board?.[1]) boards.set(decodeURIComponent(board[1]), search);
  noteFromList(path, search, entryOf(state));
}

/** The search a pipeline's board was last shown with (its view and filters), or none. */
export const boardSearch = (pipeline: string): Record<string, unknown> => boards.get(pipeline) ?? {};

/** Closing an open task: back to the board, list or page it was opened from, the history stepping back to it while it
    is behind. Opened from a link or reloaded, nothing is behind: the board `board` names (its pipeline, read only then,
    null when there is none), else Tasks. */
export function leaveTask(r: Steps, board?: () => string | null | Promise<string | null>): void {
  const now = stepOf(r.history.location.state);
  const to = beforeTask;
  if (to?.at !== undefined && now !== undefined && to.at < now) return r.history.go(to.at - now);
  if (to) return void r.navigate({ href: to.href });
  // the board may take a moment to read: any move meanwhile (a page opened, Back) stands
  let moved = false;
  const stop = r.history.subscribe(() => (moved = true));
  void Promise.resolve(board?.() ?? null).then((p) => {
    stop();
    if (!moved) r.navigate({ href: p ? `/tasks/${encodeURIComponent(p)}` : HOME });
  });
}

/* A task opened from a list: by a press on a card of its board, on a row of the list beside an open task, on a row of
   Needs you (on Tasks, or beside an open task), or as Tico went on to the row below after an answer. Only that press
   says so, never the address before it (a link, the tracker over the board). Each entry in the history that is one
   (its own key: Back and Forward come to the same entry, a new address is a new one), with its task (as the address
   names it, its number: the same whatever pipeline it has moved to since) and the list it was opened from (its
   pipeline's, or NEEDS_YOU); `seen`, every entry shown, so a step back or forward onto one keeps what it had; `here`,
   the place shown now. Kept in memory: after a reload, none is. */
const OPEN_TASK = /^\/tasks\/([^/]+)\/(?!edit(?:\/|$))([^/]+)/;

/** The Needs you list's name among the lists a task opens from: one list across every pipeline. No pipeline's id has a
    colon, so none is named this. */
export const NEEDS_YOU = ':needs-you';

interface Opened {
  task: string;
  list: string;
}
const fromList = new Map<string, Opened>();
const seen = new Set<string>();
let pressed: Opened | null = null;
let here: { entry: string; task: string } | null = null;
// who draws by where a task was opened from (the shell's list beside it): told after each address is noted
const heard = new Set<() => void>();

const entryOf = (state: unknown): string => {
  const s = state as { __TSR_key?: unknown; key?: unknown } | null | undefined;
  return String(s?.__TSR_key ?? s?.key ?? stepOf(state) ?? '');
};

function noteFromList(path: string, search: Record<string, unknown>, entry: string): void {
  const t = OPEN_TASK.exec(path);
  const task = t ? decodeURIComponent(t[2]!) : null;
  // a new entry only (an address opened, or one changed in place): Back and Forward come to one as it was
  if (task && !seen.has(entry)) {
    // the same task's address changing (its tab named, another tab) keeps how it was opened; a thread's card does not
    const was = here && fromList.get(here.entry);
    const kept = search.chat === undefined && search.via === undefined && here?.task === task && was?.task === task ? was : null;
    if (pressed?.task === task) fromList.set(entry, pressed);
    else if (kept) fromList.set(entry, kept);
  }
  seen.add(entry);
  pressed = null;
  here = task ? { entry, task } : null;
  heard.forEach((on) => on());
}

/** A press that opens a task from a list (a board's card, a row of the list beside an open task, a Needs you row; none
    for Tico's own move on): its pipeline and task as the address names them, and the list (its pipeline's unless
    named). The task is matched on alone, so a task that moved to another pipeline keeps where it was opened from. Only
    a plain press opens it here: one with a key held, another button, or one the page took for itself (picking) marks
    nothing. */
export function pressListRow(
  pipeline: string,
  task: string,
  e?: { button: number; metaKey: boolean; ctrlKey: boolean; shiftKey: boolean; altKey: boolean; defaultPrevented: boolean },
  list: string = pipeline,
): void {
  if (e && (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.defaultPrevented)) return;
  pressed = { task, list };
}

/** The list the open task (as its address names it) was opened from (a pipeline, or NEEDS_YOU), or null: as the address
    is noted, or, while it is on its way, as the press that opened it said. */
export function openedFrom(task: string): string | null {
  if (here?.task === task) {
    const from = fromList.get(here.entry);
    return from?.task === task ? from.list : null;
  }
  return pressed?.task === task ? pressed.list : null;
}

/** Hear each address noted (what `openedFrom` says may have changed); the returned function stops. */
export function hearPlaces(on: () => void): () => void {
  heard.add(on);
  return () => void heard.delete(on);
}

/** A row of the list beside an open task: its task's id, its number as the address names it, and its pipeline when
    the list crosses pipelines (Needs you). */
export interface ListRow {
  id: string;
  n: string;
  pipeline?: string;
}

/* The list beside an open task while it shows: its rows in the order drawn, `held` as they were when an answer was
   given on the open task. */
let listed: { pipeline: string; open: string; rows: ListRow[]; held?: ListRow[] } | null = null;

/** The list beside an open task (its pipeline, or NEEDS_YOU; the open task as the address names it), as it draws its
    rows in order (a folded stage's are not drawn); null while it does not show. */
export function noteList(list: { pipeline: string; open: string; rows: ListRow[] } | null): void {
  const same = !!list && listed?.pipeline === list.pipeline && listed.open === list.open;
  listed = list && { ...list, ...(same && listed?.held && { held: listed.held }) };
}

/** An answer is being given on the open task: the row below it is the one below it now, wherever the answer moves it. */
export const holdList = (): void => void (listed && (listed.held = listed.rows));

/** The task below `id` in the list beside it as an answer found it (past rows that have left the list since), opened as
    a press on its row would: its pipeline and number, and the list when it is not that pipeline's (Needs you). Null at
    the list's last row, with no list showing, or when the open task was not opened from that list (a link, a thread:
    its answer goes back there). */
export function nextInList(id: string): { pipeline: string; task: string; list?: string } | null {
  const from = here && fromList.get(here.entry);
  if (!listed || !here || from?.task !== here.task || from.list !== listed.pipeline) return null;
  const { pipeline, rows } = listed;
  const order = listed.held ?? rows;
  const now = new Set(rows.map((r) => r.id));
  const at = order.findIndex((r) => r.id === id);
  const next = at < 0 ? undefined : order.slice(at + 1).find((r) => r.id !== id && now.has(r.id));
  if (!next) return null;
  return next.pipeline && next.pipeline !== pipeline ? { pipeline: next.pipeline, task: next.n, list: pipeline } : { pipeline, task: next.n };
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
  beforeTask = null;
  boards.clear();
  listed = null;
  fromList.clear();
  seen.clear();
  pressed = null;
  here = null;
  onRow = null;
  drawn = { order: [], latest: [], held: null };
}
