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
// An open task: under its pipeline (not the pipeline editor), or by its number alone.
const TASK = /^\/(tasks\/[^/]+\/(?!edit(\/|$))[^/]+|task\/[^/]+)(\/|$)/;
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
    is behind. Opened from a link or reloaded, nothing is behind: its pipeline's board, else Tasks. */
export function leaveTask(r: Steps, pipeline?: string | null): void {
  const now = stepOf(r.history.location.state);
  const to = beforeTask;
  if (to?.at !== undefined && now !== undefined && to.at < now) r.history.go(to.at - now);
  else void r.navigate({ href: to?.href ?? (pipeline ? `/tasks/${encodeURIComponent(pipeline)}` : HOME) });
}

/** Forgets every place (tests). */
export function forgetPlaces(): void {
  beforeTask = null;
  boards.clear();
}
