/* A part of the frame's trail, and a phone's Back in a page head, go up the trail (`/tasks/launch` from a task). When
   the step before this one in the app's history is that very page, they go back to it instead: the list returns as it was left, its view (list or
   board), filters and place kept, and the step is not pushed a second time. The pathname of each history step is kept
   as the router resolves it, by the step's index (TanStack's `__TSR_index`; a replace keeps its index). */

interface Located {
  pathname: string;
  state: unknown;
}
interface Watchable {
  state: { location: Located };
  subscribe: (event: 'onResolved', fn: (e: { toLocation: Located }) => void) => () => void;
}

const steps = new Map<number, string>();
let watched: Watchable | null = null;

const indexOf = (l: Located): number | undefined => {
  const i = (l.state as { __TSR_index?: unknown } | null)?.__TSR_index;
  return typeof i === 'number' ? i : undefined;
};

function keep(l: Located) {
  const i = indexOf(l);
  if (i !== undefined) steps.set(i, l.pathname);
}

/** Start keeping the router's steps (once per router). */
export function watchSteps(router: Watchable): void {
  if (watched === router) return;
  watched = router;
  steps.clear();
  keep(router.state.location);
  router.subscribe('onResolved', (e) => keep(e.toLocation));
}

/** Whether the history step before the current one is the page at `to` (a path, a query ignored). */
export function stepBackIs(router: Watchable, to: string): boolean {
  const i = indexOf(router.state.location);
  return i !== undefined && i > 0 && steps.get(i - 1) === new URL(to, 'http://x').pathname;
}
