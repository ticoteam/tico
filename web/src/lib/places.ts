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
  noteFromList(path, search, entryOf(state), board?.[1] ? decodeURIComponent(board[1]) : null);
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

/* A task opened from its pipeline: from its board (not a thread's card over it), or by a press on a row of the list
   beside an open task. Each entry in the history that is one (its own key: Back and Forward come to the same entry, a
   new address is a new one), with its task (`<pipeline>/<task>`); `seen`, every entry shown, so a step back or forward
   onto one keeps what it had; `here`, the place shown now; `last`, the board shown just before it, if one was. */
const OPEN_TASK = /^\/tasks\/([^/]+)\/(?!edit(?:\/|$))([^/]+)/;
const fromList = new Map<string, string>();
const seen = new Set<string>();
let pressed: string | null = null;
let last: string | null = null;
let here: { entry: string; task: string } | null = null;

const entryOf = (state: unknown): string => {
  const s = state as { __TSR_key?: unknown; key?: unknown } | null | undefined;
  return String(s?.__TSR_key ?? s?.key ?? stepOf(state) ?? '');
};

function noteFromList(path: string, search: Record<string, unknown>, entry: string, board: string | null): void {
  const t = OPEN_TASK.exec(path);
  const task = t ? `${decodeURIComponent(t[1]!)}/${decodeURIComponent(t[2]!)}` : null;
  // a new entry only (an address opened, or one changed in place): Back and Forward come to one as it was
  if (task && !seen.has(entry)) {
    const plain = search.chat === undefined && search.via === undefined; // not a thread's card
    const fromBoard = plain && last !== null && task.startsWith(`${last}/`);
    // the same task's address changing (its tab named, another tab) keeps how it was opened
    const kept = plain && here?.task === task && fromList.get(here.entry) === task;
    if (pressed === task || fromBoard || kept) fromList.set(entry, task);
  }
  seen.add(entry);
  pressed = null;
  here = task ? { entry, task } : null;
  last = board;
}

/** A row of the list beside an open task pressed (its pipeline and task, as its address names them): the task it opens
    was opened from the list. */
export const pressListRow = (pipeline: string, task: string): void => void (pressed = `${pipeline}/${task}`);

/** A row of the list beside an open task: its task's id, its number as the address names it. */
export interface ListRow {
  id: string;
  n: string;
}

/* The list beside an open task while it shows: its rows in the order drawn, `held` as they were when an answer was
   given on the open task. */
let listed: { pipeline: string; open: string; rows: ListRow[]; held?: ListRow[] } | null = null;

/** The list beside an open task (its pipeline, the open task as the address names it), as it draws its rows in order (a
    folded stage's are not drawn); null while it does not show. */
export function noteList(list: { pipeline: string; open: string; rows: ListRow[] } | null): void {
  const same = !!list && listed?.pipeline === list.pipeline && listed.open === list.open;
  listed = list && { ...list, ...(same && listed?.held && { held: listed.held }) };
}

/** An answer is being given on the open task: the row below it is the one below it now, wherever the answer moves it. */
export const holdList = (): void => void (listed && (listed.held = listed.rows));

/** The task below `id` in the list beside it as an answer found it (past rows that have left the list since), opened as
    a press on its row would: its pipeline and number. Null at the list's last row, with no list showing, or when the open
    task was not opened from its pipeline's board or list (a link, Needs you, a thread: its answer goes back there). */
export function nextInList(id: string): { pipeline: string; task: string } | null {
  if (!listed || !here || fromList.get(here.entry) !== here.task || !here.task.startsWith(`${listed.pipeline}/`)) return null;
  const { pipeline, rows } = listed;
  const order = listed.held ?? rows;
  const now = new Set(rows.map((r) => r.id));
  const at = order.findIndex((r) => r.id === id);
  const next = at < 0 ? undefined : order.slice(at + 1).find((r) => r.id !== id && now.has(r.id));
  return next ? { pipeline, task: next.n } : null;
}

/** Forgets every place (tests). */
export function forgetPlaces(): void {
  beforeTask = null;
  boards.clear();
  listed = null;
  fromList.clear();
  seen.clear();
  pressed = null;
  last = null;
  here = null;
}
