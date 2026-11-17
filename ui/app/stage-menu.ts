/* ui/app/stage-menu.ts — A stage's ⋯ on a pipeline board (docs/tasks.md, "Moving a task to another pipeline"): at the
   right of its column's header (the list's group header, a phone's stage row), what can be done to every task the
   column shows, the search and filter applied, each in one write: Move all to… (this pipeline's stages, Done, then the
   other pipelines'), Close all (Undo reopens them), Assign all to…, Snooze all…, and Edit stage for whoever may change
   the pipeline. Move all and Close all ask first, with the count. Each task changes as its single change would; one that
   can't stays, and the bar above the stages says how many and why. A page part (ui/parts.json stage-menu): the first ⋯
   loads it with the menus it uses (task, give-to) and task-pick's words for what stayed.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** What a write to many tasks did (POST /v2/tasks/owner, /v2/tasks/snooze): the ones changed, and each one that stayed
    with its code; one the caller may not read is counted (no id). */
interface TasksChanged { batch_id: string; changed: string[]; changed_unseen?: number; unchanged?: number; skipped: TasksMoved['skipped'] }

/** The column's ⋯ as drawn now: a live draw may have replaced the one that was pressed. */
const pvColAnchor = (stageId: string, was: HTMLElement): HTMLElement =>
  was.isConnected ? was : document.querySelector<HTMLElement>(`[data-pv-col-menu="${CSS.escape(stageId)}"]`) ?? was;

/** Whoever may work a task closes and reassigns it: a person who moves tasks, its owner or whoever asked for it (the
    server checks each, and says which stayed). */
function pvMayWork(state: PvState, tasks: TaskAny[]): boolean {
  if (state.rec.may_change || canMove()) return true;
  const me = myActor();
  return !!me && tasks.some(t => t.owner_id === me || t.requester_id === me);
}

/** The ⋯ of a stage: the shell's stand-in calls it (task-pipelines.ts pvClick). */
function pvColMenu(state: PvState, stageId: string, anchor: HTMLElement): void {
  const stage = state.rec.stages.find(s => s.id === stageId);
  if (!stage || PV !== state) return;
  const tasks = pvInStage(state, stageId), ids = tasks.map(t => t.id), work = pvMayWork(state, tasks);
  const later = (run: () => void) => () => setTimeout(run);
  const items: PropMenuAction[] = [];
  if (ids.length) {
    items.push({text: 'Move all to…', icon: 'swap_horiz', run: later(() => pvColMove(state, stage, ids, pvColAnchor(stageId, anchor)))});
    if (work) {
      items.push({text: 'Close all', icon: 'archive', run: later(() => pvColClose(state, ids, pvColAnchor(stageId, anchor)))});
      items.push({text: 'Assign all to…', icon: 'person', run: later(() => pvColAssign(state, ids, pvColAnchor(stageId, anchor)))});
      items.push({text: 'Snooze all…', icon: 'schedule', run: later(() => pvColSnooze(state, ids, pvColAnchor(stageId, anchor)))});
    }
  }
  if (state.rec.may_change) items.push({text: 'Edit stage', icon: 'edit', sep: items.length > 0, run: () => void openPipelineEditor(state.rec.id, stageId)});
  if (!items.length) return;
  propMenu(pvMenuHost(), anchor, {label: stage.name, onPick: it => it.run(),
    items: items.map(it => ({...it, html: `<span class="nav-icon" aria-hidden="true">${it.icon ?? ''}</span><span>${esc(it.text)}</span>`}))});
}

/** Move all to…: Move to…'s picker with this pipeline first and Done, then "Move 22 tasks to Build?" with an optional
    why, then one write. */
function pvColMove(state: PvState, stage: PipelineStageRec, ids: string[], anchor: HTMLElement): void {
  void taskMoveToMenu(pvMenuHost(), anchor, {pipeline: state.rec.id, stage: stage.id ?? null, done: true,
    confirm: where => `Move ${pvMany(ids.length)} to ${where}?`,
    run: (p, st, why) => {
      const inside = p.id === state.rec.id;
      const body = {ids, ...(inside ? {to: String(st.id)} : {to_pipeline: p.id, to: String(st.id)}), ...(why ? {why} : {})};
      void pvColWrite(state, ['Moved', 'move'], () => post('/v2/tasks/move', body, newRequestId()) as Promise<TasksMoved>,
        out => ({done: out.moved, unseen: out.moved_unseen ?? 0, skipped: out.skipped}),
        // Gone from the board at once when they left it (another pipeline, or Done); the live read shows the rest.
        moved => { if (!inside || st.id === 'done') state.tasks = state.tasks.filter(t => !moved.has(t.id)); });
    }});
}

/** Close all: "Close 22 tasks?", then one write; Undo reopens the ones it closed, as a single close's Undo does. */
function pvColClose(state: PvState, ids: string[], anchor: HTMLElement): void {
  propMenu(pvMenuHost(), anchor, {label: `Close ${pvMany(ids.length)}?`, items: [{text: 'Close', danger: true}], onPick: () => {
    void pvColWrite(state, ['Closed', 'close'], () => post('/v2/tasks/move', {ids, to: 'closed'}, newRequestId()) as Promise<TasksMoved>,
      out => ({done: out.moved, unseen: out.moved_unseen ?? 0, skipped: out.skipped}),
      closed => {
        state.tasks = state.tasks.filter(t => !closed.has(t.id));
        if (!closed.size) return false;
        const back = [...closed];
        toast(`Closed ${pvMany(back.length)}`, false, {label: 'Undo', run: async () => {
          await post('/v2/tasks/move', {ids: back, to: 'reopen', why: 'Undone'}, newRequestId());
          if (TASKS_ST) void tasksCatchUp(TASKS_ST);
        }});
        return true;
      });
  }});
}

/** Assign all to…: the task window's owner picker (give-to.ts), then one write. */
function pvColAssign(state: PvState, ids: string[], anchor: HTMLElement): void {
  giveToOpen(anchor, {label: 'Owner', onPick: it => {
    void pvColWrite(state, ['Assigned', 'change'], () => post('/v2/tasks/owner', {ids, owner: it.actor}, newRequestId()) as Promise<TasksChanged>,
      out => ({done: out.changed, unseen: out.changed_unseen ?? 0, skipped: out.skipped}));
  }});
}

/** Snooze all…: the task window's snooze choices or a typed date (task-props.ts snoozePick), then one write. */
function pvColSnooze(state: PvState, ids: string[], anchor: HTMLElement): void {
  snoozePick(pvMenuHost(), anchor, until => void pvColWrite(state, ['Snoozed', 'snooze'],
    () => post('/v2/tasks/snooze', {ids, until}, newRequestId()) as Promise<TasksChanged>,
    out => ({done: out.changed, unseen: out.changed_unseen ?? 0, skipped: out.skipped})));
}

/** One write to the column's tasks, and what the board says after it: nothing when every task changed, else above the
    stages what didn't, and why. "Moved 20. 2 didn't move: 2 are in a bot's running turn."; after a toast that already
    says what changed (Close all's, with Undo), only "2 didn't close: in a bot's running turn.". `after(done)` changes
    the board at once, and is true when it showed that toast; the page's read follows. */
async function pvColWrite<T>(state: PvState, [did, verb]: [string, string], send: () => Promise<T>,
  read: (out: T) => {done: string[]; unseen: number; skipped: TasksMoved['skipped']}, after?: (done: Set<string>) => boolean | void): Promise<void> {
  try {
    const out = read(await send());
    if (PV !== state) return;
    const toasted = after?.(new Set(out.done)) === true, stayed = out.skipped.length;
    const why = `${stayed} didn't ${verb}: ${pvSkipWords(out.skipped, toasted)}.`;
    state.bar = stayed ? {taskId: '', to: '', push: false, text: toasted ? why : `${did} ${out.done.length + out.unseen}. ${why}`} : null;
  } catch (error) {
    if (PV !== state) return;
    state.bar = {taskId: '', to: '', push: false, text: errorMessage(error, 'Not changed.')};
  }
  pvDraw(state);
  if (TASKS_ST) void tasksCatchUp(TASKS_ST);
}
