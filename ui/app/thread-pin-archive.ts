/* ui/app/thread-pin-archive.ts — The thread header's Pin and Archive buttons (thread-view.ts thvClick): pinning for the
   viewer, archiving and unarchiving the thread. Classic script: its globals are shared with the other files under
   ui/app/, loaded in the order index.html lists them.
   A page part (ui/parts.json): it loads on the first press, so a cold open of Home does not download it (P05b). */
'use strict';
// Pin or unpin for the viewer; the rail moves the row at once.
async function thvPinToggle(view: ThvView, button: HTMLButtonElement) {
  const t = view.thread; if (!thvPinnable(t)) return;
  const on = !t.me.pinned;
  button.disabled = true;
  try {
    const r: ThvThread | null = on ? await put(thvPath(t.id) + '/pin', {}) : await writeRequest('DELETE', thvPath(t.id) + '/pin');
    view.thread = {...t, ...(r?.id ? r : {}), me: {...t.me, ...(r?.me || {}), pinned: r?.me ? !!r.me.pinned : on}};
    threadsPut(view.thread);
    thvDraw(view);
  } catch (e) { toast(errorMessage(e, ''), true); button.disabled = false; }
}
// The header's Archive takes the thread off the list and goes back to it (Undo is in the toast); on an archived thread
// Unarchive brings it back here.
async function thvArchive(view: ThvView, button: HTMLButtonElement) {
  const t = view.thread; if (!t) return;
  button.disabled = true;
  try {
    if (!t.archived_at) {
      await threadsArchive(t.id);
      if (threadsLastGet() === t.id) threadsLastSet('');
      location.hash = THREADS_ROUTE;
      return;
    }
    const r: ThvThread = await patch(thvPath(t.id), {archived: false});
    view.thread = {...t, ...r};
    threadsUnarchived(view.thread);
    thvDraw(view);
  } catch (e) { toast(errorMessage(e, ''), true); button.disabled = false; }
}
