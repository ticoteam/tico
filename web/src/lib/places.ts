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
  history: { location: { state: unknown }; go: (n: number) => void };
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

/** The history entry an address is (its own key, kept while it stays). */
const entryOf = (state: unknown): unknown => (state as { __TSR_key?: unknown } | null | undefined)?.__TSR_key;

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
  // the board may take a moment to read: a page opened meanwhile stands
  const from = entryOf(r.history.location.state);
  void Promise.resolve(board?.() ?? null).then((p) => {
    if (entryOf(r.history.location.state) === from) r.navigate({ href: p ? `/tasks/${encodeURIComponent(p)}` : HOME });
  });
}

/** Forgets every place (tests). */
export function forgetPlaces(): void {
  beforeTask = null;
  boards.clear();
}
