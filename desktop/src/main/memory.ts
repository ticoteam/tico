// Memory with many companies (platforms.md 4.4, R8). Every company's page stays loaded, so rings and notifications
// arrive from all of them, up to MAX_LIVE. Past that, the one shown longest ago is unloaded (never the one on screen,
// never one in a call) and loads again where it was when it is shown. Pure.

export const MAX_LIVE = 6;

export type ViewUse = { id: string; loaded: boolean; shownAt: number; inCall: boolean };

/** The loaded views to unload so that at most `max` stay loaded: oldest shown first, never `activeId` or a call. */
export function viewsToUnload(views: readonly ViewUse[], activeId: string | null, max = MAX_LIVE): string[] {
  const loaded = views.filter((v) => v.loaded);
  const extra = loaded.length - max;
  if (extra <= 0) return [];
  return loaded
    .filter((v) => v.id !== activeId && !v.inCall)
    .sort((a, b) => a.shownAt - b.shownAt)
    .slice(0, extra)
    .map((v) => v.id);
}

/** The companies whose pages load at start: the active one first, then the others in list order, up to `max`. */
export function loadAtStart(ids: readonly string[], activeId: string | null, max = MAX_LIVE): Set<string> {
  const order = activeId && ids.includes(activeId) ? [activeId, ...ids.filter((id) => id !== activeId)] : [...ids];
  return new Set(order.slice(0, max));
}
