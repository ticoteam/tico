/* Where the person came from, for the two ways back that are not the browser's own Back: leaving Flow, and closing a
   task once nothing on it waits on them. The shell notes every address it shows, with its step in the history, so a
   way back steps the history back to that place (Back and Forward stay as they were) when it is still behind. */

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
const FLOW = /^\/flow(\/|$)/;
// An open task: under its pipeline (not the pipeline editor), by its number alone, or inside Flow.
const TASK = /^\/(tasks\/[^/]+\/(?!edit(\/|$))[^/]+|task\/[^/]+|flow\/[^/]+)(\/|$)/;
const BOARD = /^\/tasks\/([^/]+)\/?$/;

let beforeFlow: Place | null = null;
let beforeTask: Place | null = null;
const boards = new Map<string, Record<string, unknown>>();

export const isFlowPath = (path: string): boolean => FLOW.test(path);
export const isTaskPath = (path: string): boolean => TASK.test(path);

const stepOf = (state: unknown): number | undefined => {
  const at = (state as { __TSR_index?: unknown } | null | undefined)?.__TSR_index;
  return typeof at === 'number' ? at : undefined;
};

/** The shell, at each address it shows: the path without the app's base, the search as written and as read. */
export function notePlace(path: string, searchStr: string, search: Record<string, unknown>, state: unknown): void {
  const here: Place = { href: path + searchStr, at: stepOf(state) };
  if (!isFlowPath(path)) beforeFlow = here;
  if (!isTaskPath(path) && !isFlowPath(path)) beforeTask = here;
  const board = BOARD.exec(path);
  if (board?.[1]) boards.set(decodeURIComponent(board[1]), search);
}

/** The search a pipeline's board was last shown with (its view and filters), or none. */
export const boardSearch = (pipeline: string): Record<string, unknown> => boards.get(pipeline) ?? {};

/** Back to a place: the history steps back to it while it is behind, else it opens anew (Tasks when there is none). */
function goBack(r: Steps, to: Place | null): void {
  const now = stepOf(r.history.location.state);
  if (to?.at !== undefined && now !== undefined && to.at < now) r.history.go(to.at - now);
  else void r.navigate({ href: to?.href ?? HOME });
}

/** Leaving Flow: back to the page it was entered from. */
export const leaveFlow = (r: Steps): void => goBack(r, beforeFlow);

/** Closing an open task: back to the board, list or page it was opened from. */
export const leaveTask = (r: Steps): void => goBack(r, beforeTask);

/** Forgets every place (tests). */
export function forgetPlaces(): void {
  beforeFlow = null;
  beforeTask = null;
  boards.clear();
}
