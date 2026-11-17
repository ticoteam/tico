/* ui/app/task-pick.ts — Picking many tasks on a pipeline's board (⋯ › Select, or a shift-, ⌘- or Ctrl-click on a card),
   and Move to… for the picked tasks (docs/dev/pipelines-v3.md, "Move to another pipeline"). A page part (ui/parts.json
   task-pick): a cold open does not download it. The first pick or Select loads it, through the shell's stand-in
   (pvPick, task-pipelines.ts). The shell draws the bar and the picked cards through PV_PICK (pvSelHTML, pvSelPaint), since a draw
   cannot wait for a part; the bar's clicks come back through it too.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** What a move of many tasks did: the ones moved, and each one that stayed with its code. One the mover may not read is
    counted (no id); `moved_unseen` counts the moved ones they no longer read. */
interface TasksMoved { batch_id: string; moved: string[]; moved_unseen?: number; skipped: {id?: string; code: string; reason: string}[] }
const tasksMoveTo = (ids: string[], pipeline: string, stage: string, why?: string): Promise<TasksMoved> =>
  post('/v2/tasks/move', {ids, to_pipeline: pipeline, to: stage, ...(why ? {why} : {})}, newRequestId()) as Promise<TasksMoved>;
const pvMany = (n: number): string => `${n} task${n === 1 ? '' : 's'}`;

// ---------------------------------------------------------------- picking many, and Move to…

/** A card picked or let go; picking starts selecting. With no card (⋯ › Select), selecting starts, or stops. The shell's
    one stand-in for this part. */
function pvPick(state: PvState, card?: HTMLElement): void {
  if (!card) { if (state.selecting) pvSelectEnd(state); else { state.selecting = true; pvDraw(state); } return; }
  const id = card.dataset['task'] ?? '', t = state.tasks.find(x => x.id === id);
  if (!t) return;
  state.selecting = true;
  if (state.sel.has(id)) state.sel.delete(id); else { state.sel.add(id); state.selStage = t.stage_id ?? ''; }
  pvDraw(state);
}

/** All in a stage: every task of it the search and filters show is picked; again, they are let go. */
function pvSelectAll(state: PvState, stageId: string): void {
  const ids = pvInStage(state, stageId).map(t => t.id);
  const every = ids.length > 0 && ids.every(id => state.sel.has(id));
  for (const id of ids) if (every) state.sel.delete(id); else state.sel.add(id);
  state.selStage = stageId;
  pvDraw(state);
}

function pvSelectEnd(state: PvState): void {
  state.selecting = false; state.sel.clear();
  pvDraw(state);
}

/** One host for the board's menus: a task dialog has its own. */
function pvMenuHost(): HTMLElement {
  let host = document.getElementById('pv-menu-host');
  if (!host) { host = document.createElement('div'); host.id = 'pv-menu-host'; document.body.append(host); }
  return host;
}

/** Move to… for the picked tasks: the picker, one line that asks first ("Move 12 tasks to Backlog › On ice?"), then one
    write. The result says what moved and what stayed and why; those that stayed stay picked, to try again. */
function pvSelectMove(state: PvState, anchor: HTMLElement): void {
  const ids = [...state.sel].filter(id => state.tasks.some(t => t.id === id));
  if (!ids.length) return;
  const stages = new Set(ids.map(id => state.tasks.find(t => t.id === id)?.stage_id ?? ''));
  void taskMoveToMenu(pvMenuHost(), anchor, {pipeline: state.rec.id, stage: stages.size === 1 ? [...stages][0] ?? null : null,
    confirm: where => `Move ${pvMany(ids.length)} to ${where}?`,
    run: (p, st, why) => void (async () => {
      try {
        const out = await tasksMoveTo(ids, p.id, String(st.id), why || undefined);
        if (PV !== state) return;
        const moved = new Set(out.moved);
        state.tasks = state.tasks.filter(t => !moved.has(t.id) || p.id === state.rec.id);
        state.sel = new Set(out.skipped.map(x => x.id ?? '').filter(Boolean));
        state.selecting = state.sel.size > 0;
        const done = out.moved.length + (out.moved_unseen ?? 0), stayed = ids.length - done;
        state.bar = stayed ? {taskId: '', to: '', push: false, text: `Moved ${done}. ${stayed} didn't move: ${pvSkipWords(out.skipped)}.`} : null;
        if (!stayed) toast(`Moved ${pvMany(done)} to ${p.name} › ${st.name}`);
      } catch (error) {
        if (PV !== state) return;
        state.bar = {taskId: '', to: '', push: false, text: errorMessage(error, 'Not moved.')};
      }
      pvDraw(state);
      if (TASKS_ST) void tasksCatchUp(TASKS_ST);
    })()});
}

/** Why the tasks of a move stayed, counted by reason: "2 are in a bot's running turn, 1 you can't read". `bare`: one
    reason alone, uncounted, after a line that already counts them ("1 didn't close: in a bot's running turn"). */
function pvSkipWords(skipped: TasksMoved['skipped'], bare = false): string {
  const words: Record<string, [string, string, string]> = {
    task_turn_running: ["is in a bot's running turn", "are in a bot's running turn", "in a bot's running turn"],
    task_stage_full: ['had no room', 'had no room', 'no room in the stage'],
    not_found: ["you can't read", "you can't read", "you can't read them"],
    task_not_shared: ['is private to people the stage needs', 'are private to people the stage needs', 'private to people the stage needs'],
    task_not_met: ["doesn't meet the stage's rules", "don't meet the stage's rules", "the stage's rules aren't met"],
    identity: ["isn't yours to change", "aren't yours to change", 'not yours to change'],
  };
  const counts = new Map<string, number>();
  for (const x of skipped) counts.set(x.code, (counts.get(x.code) ?? 0) + 1);
  const only = counts.size === 1 ? [...counts.keys()][0] ?? '' : '';
  if (bare && only) return words[only]?.[2] ?? skipped[0]?.reason ?? only;
  return [...counts].map(([code, n]) => {
    const said = words[code];
    return said ? `${n} ${code === 'not_found' ? said[0] : said[n === 1 ? 0 : 1]}` : `${n}: ${skipped.find(x => x.code === code)?.reason ?? code}`;
  }).join(', ');
}

/** While selecting: how many are picked, All in the stage last picked from (the stage shown on a phone), Move to… and ✕.
    A picked task that left the board (moved, closed) is let go first. */
function pvSelHTML(state: PvState): string {
  if (!state.selecting) return '';
  const here = new Set(state.tasks.map(t => t.id));
  for (const id of state.sel) if (!here.has(id)) state.sel.delete(id);
  const n = state.sel.size, stageId = state.opts.phone ? state.stage : state.selStage || state.rec.stages[0]?.id || '';
  const stage = state.rec.stages.find(s => s.id === stageId);
  return `<div class="pv-bar pv-sel" role="toolbar" aria-label="Selected tasks"><span class="pv-bar-text tnum">${n} selected</span>`
    + (stage ? `<button type="button" class="ghost" data-pv-sel-all="${esc(stage.id ?? '')}">All in ${esc(stage.name)}</button>` : '')
    + `<button type="button" class="primary" data-pv-sel-move aria-haspopup="menu"${n ? '' : ' disabled'}>Move to…</button>`
    + '<button type="button" class="ghost" data-pv-sel-x aria-label="Stop selecting">✕</button></div>';
}

/** The picked cards and rows marked as picked: drawn after the cards, so a card's HTML stays the same picked or not. */
function pvSelPaint(state: PvState, el: Element | null): void {
  if (!el) return;
  el.classList.toggle('pv-selecting', state.selecting);
  const bar = el.querySelector<HTMLElement>(':scope > .pv-sel');
  if (bar) bar.onclick = ev => { if (ev.target instanceof Element) pvSelClick(state, ev.target); };
  for (const card of el.querySelectorAll<HTMLElement>('[data-task]')) {
    const on = state.sel.has(card.dataset['task'] ?? '');
    if (card.classList.contains('pv-picked') !== on) card.classList.toggle('pv-picked', on);
    if (state.selecting) card.setAttribute('aria-pressed', String(on)); else card.removeAttribute('aria-pressed');
  }
}

/** A click on the picking bar (bound as it is painted): ✕, All in a stage, or Move to…. */
function pvSelClick(state: PvState, target: Element): void {
  if (target.closest('[data-pv-sel-x]')) { pvSelectEnd(state); return; }
  const all = target.closest<HTMLElement>('[data-pv-sel-all]');
  if (all) { pvSelectAll(state, all.dataset['pvSelAll'] ?? ''); return; }
  const move = target.closest<HTMLElement>('[data-pv-sel-move]');
  if (move) pvSelectMove(state, move);
}

// The shell draws the picking bar and the picked cards with these, from the moment this script runs (PV_PICK).
PV_PICK.html = pvSelHTML;
PV_PICK.paint = pvSelPaint;
