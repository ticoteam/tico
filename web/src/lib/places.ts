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

/** A row of the list beside an open task: its task's id, its number as the address names it, and its stage. */
export interface ListRow {
  id: string;
  n: string;
  stage: string | null;
}

/* The list beside an open task, as drawn: `rows` in order, the last drawn with the open task where it was first drawn
   (`stage`, undefined until it is drawn), and `now`, what it draws now. */
let listed: { pipeline: string; open: string; rows: ListRow[]; stage?: string | null; now: ReadonlySet<string> } | null = null;

/** The list beside an open task (its pipeline, the open task as the address names it), as it draws its rows in order (a
    folded stage's are not drawn); null once it is not drawn. An answer that moves the open task to another stage, or
    off the list, keeps the order it was answered in: the row below it stays the one below it. */
export function noteList(list: { pipeline: string; open: string; rows: ListRow[] } | null): void {
  if (!list) return void (listed = null);
  const { pipeline, open, rows } = list;
  const now = new Set(rows.map((r) => r.id));
  const at = rows.find((r) => r.n === open || r.id === open);
  if (listed?.pipeline === pipeline && listed.open === open && listed.stage !== undefined && at?.stage !== listed.stage) return void (listed = { ...listed, now });
  listed = { pipeline, open, rows, now, ...(at && { stage: at.stage }) };
}

/** The task below `id` in the list beside it, as a press on its row opens it (its pipeline and number), past rows that
    have left the list since; null at the list's last row, or with no list drawn. */
export function nextInList(id: string): { pipeline: string; task: string } | null {
  if (!listed) return null;
  const { pipeline, rows, now } = listed;
  const at = rows.findIndex((r) => r.id === id);
  const next = at < 0 ? undefined : rows.slice(at + 1).find((r) => r.id !== id && now.has(r.id));
  return next ? { pipeline, task: next.n } : null;
}

/** Forgets every place (tests). */
export function forgetPlaces(): void {
  beforeTask = null;
  boards.clear();
  listed = null;
}
