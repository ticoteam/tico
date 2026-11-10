/* ui/app/task-board.ts — Dragging a task between a pipeline's columns (task-pipelines.ts draws them), on a desktop
   pointer. The page carries the task rather than the browser's drag and drop, so the wheel and the trackpad still
   scroll the columns while it is held. Letting go over another stage asks the engine to move it there (pvMove); a press
   that never moved is a click, which the page reads to open the task. A phone moves a task from the task's own ⋯ menu.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** A held task: the press sets the first fields, the lift (the copy drawn under the pointer) the rest. */
interface TbDrag {
  card: HTMLElement; id: string; x0: number; y0: number; x: number; y: number; on: boolean;
  dx: number; dy: number; ghost: HTMLElement | null; from: string; target: string;
  tick: number; off: () => void;
}

const BOARD_SLOP = 5;
let TB_DRAG: TbDrag | null = null;

function taskBoardBind(root: HTMLElement): void {
  root.addEventListener('pointerdown', ev => { if (PV?.root === root) taskBoardPress(PV, ev); });
}

function taskBoardPress(state: PvState, ev: PointerEvent): void {
  if (TB_DRAG || state.opts.phone || ev.button !== 0 || ev.pointerType === 'touch') return;
  if (ev.shiftKey || ev.metaKey || ev.ctrlKey || ev.altKey) return;
  const target = eventElement(ev);
  const card = target?.closest<HTMLElement>('[data-pv-drag] .tk-card');
  if (!target || !card || target.closest('a, input, select, textarea, button')) return;
  const id = card.dataset['task'] ?? '';
  if (!id || state.moving.has(id)) return;          // one move at a time per task
  const move = (e: PointerEvent): void => taskBoardMove(state, e);
  const up = (e: PointerEvent): void => taskBoardUp(state, e);
  const cancel = (): void => taskBoardEnd(state, false);
  const keys = (e: KeyboardEvent): void => {
    if (e.key === 'Escape' && TB_DRAG?.on) { e.preventDefault(); e.stopPropagation(); taskBoardEnd(state, false); }
  };
  document.addEventListener('pointermove', move, true);
  document.addEventListener('pointerup', up, true);
  document.addEventListener('pointercancel', cancel, true);
  document.addEventListener('keydown', keys, true);
  TB_DRAG = {card, id, x0: ev.clientX, y0: ev.clientY, x: ev.clientX, y: ev.clientY, on: false, dx: 0, dy: 0, ghost: null,
    from: card.closest<HTMLElement>('[data-pv-col]')?.dataset['pvCol'] ?? '', target: '', tick: 0,
    off: () => {
      document.removeEventListener('pointermove', move, true);
      document.removeEventListener('pointerup', up, true);
      document.removeEventListener('pointercancel', cancel, true);
      document.removeEventListener('keydown', keys, true);
    }};
}

function taskBoardMove(state: PvState, e: PointerEvent): void {
  const d = TB_DRAG;
  if (!d) return;
  if (!e.buttons) { taskBoardEnd(state, false); return; }      // let go outside the window
  d.x = e.clientX; d.y = e.clientY;
  if (!d.on) {
    if (Math.abs(d.x - d.x0) < BOARD_SLOP && Math.abs(d.y - d.y0) < BOARD_SLOP) return;
    taskBoardLift(d);
  }
  e.preventDefault();
  if (d.ghost) d.ghost.style.transform = `translate(${d.x - d.dx}px,${d.y - d.dy}px)`;
  taskBoardOver(state);
  taskBoardAutoScroll(state);
}

function taskBoardLift(d: TbDrag): void {
  d.on = true;
  const r = d.card.getBoundingClientRect();
  d.dx = d.x0 - r.left; d.dy = d.y0 - r.top;
  const ghost = d.card.cloneNode(true) as HTMLElement;
  ghost.removeAttribute('data-task');
  ghost.classList.add('pv-ghost');
  ghost.setAttribute('aria-hidden', 'true');
  ghost.style.width = r.width + 'px';
  ghost.style.transform = `translate(${r.left}px,${r.top}px)`;
  document.body.append(ghost);
  d.ghost = ghost;
  d.card.classList.add('dragging');
  document.body.classList.add('board-holding');
  getSelection()?.removeAllRanges();
}

/** The stage under the held task. */
function taskBoardOver(state: PvState): void {
  const d = TB_DRAG;
  if (!d?.on) return;
  const col = document.elementFromPoint(d.x, d.y)?.closest<HTMLElement>('[data-pv-drag] [data-pv-col]') ?? null;
  for (const el of state.root?.querySelectorAll('.pv-col.over') ?? []) if (el !== col) el.classList.remove('over');
  col?.classList.add('over');
  d.target = col?.dataset['pvCol'] ?? '';
}

/** Held near a side of the columns they scroll that way, faster the deeper in. */
function taskBoardAutoScroll(state: PvState): void {
  const d = TB_DRAG;
  if (!d?.on || d.tick) return;
  const step = (): void => {
    d.tick = 0;
    if (TB_DRAG !== d) return;
    const cols = state.root?.querySelector<HTMLElement>('.pv-cols');
    if (!cols) return;
    const r = cols.getBoundingClientRect(), edge = 60, left = cols.scrollLeft;
    if (d.y >= r.top && d.y <= r.bottom) {
      if (d.x < r.left + edge) cols.scrollLeft = left - Math.ceil((r.left + edge - d.x) / 4);
      else if (d.x > r.right - edge) cols.scrollLeft = left + Math.ceil((d.x - (r.right - edge)) / 4);
    }
    if (cols.scrollLeft !== left) { taskBoardOver(state); d.tick = requestAnimationFrame(step); }
  };
  d.tick = requestAnimationFrame(step);
}

function taskBoardUp(state: PvState, e: PointerEvent): void {
  const d = TB_DRAG;
  if (!d) return;
  if (d.on) { d.x = e.clientX; d.y = e.clientY; taskBoardOver(state); }
  taskBoardEnd(state, d.on);
}

function taskBoardEnd(state: PvState, drop: boolean): void {
  const d = TB_DRAG;
  if (!d) return;
  TB_DRAG = null;
  d.off();
  if (d.tick) cancelAnimationFrame(d.tick);
  if (!d.on) return;                      // a click: the task opens as always
  d.ghost?.remove();
  d.card.classList.remove('dragging');
  document.body.classList.remove('board-holding');
  for (const el of state.root?.querySelectorAll('.pv-col.over') ?? []) el.classList.remove('over');
  // The click the carry ends in opens nothing.
  const swallow = (ev: Event): void => { ev.stopPropagation(); ev.preventDefault(); };
  window.addEventListener('click', swallow, {capture: true, once: true});
  setTimeout(() => window.removeEventListener('click', swallow, true), 0);
  const t = state.tasks.find(x => x.id === d.id);
  if (drop && t && d.target && d.target !== d.from) pvMove(state, t, d.target);
}
