/* ui/app/task-board.ts — Dragging a task between a pipeline's columns (task-pipelines.ts draws them), on a desktop
   pointer. The page carries the task rather than the browser's drag and drop, so the wheel and the trackpad still
   scroll the columns while it is held. Letting go over another stage asks the engine to move it there (pvMove); a press
   that never moved is a click, which the page reads to open the task. A phone moves a task from the task's own ⋯ menu.
   Held over another task's card (one the person may change, as the held one), that card is outlined, not its column:
   letting go there opens the drop dialog, Make it a subtask or Merge (docs/tasks.md, "Merge and make a subtask").
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** A held task: the press sets the first fields, the lift (the copy drawn under the pointer) the rest. */
interface TbDrag {
  card: HTMLElement; id: string; x0: number; y0: number; x: number; y: number; on: boolean;
  dx: number; dy: number; ghost: HTMLElement | null; from: string; target: string;
  /** The task whose card it is held over, when it may be dropped there. */
  onto: string;
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
    from: card.closest<HTMLElement>('[data-pv-col]')?.dataset['pvCol'] ?? '', target: '', onto: '', tick: 0,
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

/** Whether the person may change the task, as the board knows: a task mover, whoever may change the pipeline, or a
    party to it. The server checks again (task_calls.can_work); this only keeps an outline off a card they cannot. */
function taskBoardMayChange(state: PvState, t: TaskAny | undefined): boolean {
  if (!t) return false;
  const me = myActor();
  return canMove() || !!state.rec.may_change || (!!me && [t.owner_id, t.requester_id, t.assignee_id].includes(me));
}

/** What is under the held task: another task's card (it wins over its column), or a stage. A card the person may not
    change is neither: the drop there does nothing. */
function taskBoardOver(state: PvState): void {
  const d = TB_DRAG;
  if (!d?.on) return;
  const under = document.elementFromPoint(d.x, d.y);
  const card = under?.closest<HTMLElement>('[data-pv-drag] .tk-card[data-task]') ?? null;
  const onto = card && card !== d.card ? card.dataset['task'] ?? '' : '';
  const held = state.tasks.find(x => x.id === d.id);
  const may = !!onto && taskBoardMayChange(state, held) && taskBoardMayChange(state, state.tasks.find(x => x.id === onto));
  const col = onto ? null : under?.closest<HTMLElement>('[data-pv-drag] [data-pv-col]') ?? null;
  for (const el of state.root?.querySelectorAll('.pv-col.over') ?? []) if (el !== col) el.classList.remove('over');
  for (const el of state.root?.querySelectorAll('.tk-card.drop-on') ?? []) if (el !== card || !may) el.classList.remove('drop-on');
  col?.classList.add('over');
  if (may) card?.classList.add('drop-on');
  d.onto = may ? onto : '';
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
  for (const el of state.root?.querySelectorAll('.tk-card.drop-on') ?? []) el.classList.remove('drop-on');
  // The click the carry ends in opens nothing.
  const swallow = (ev: Event): void => { ev.stopPropagation(); ev.preventDefault(); };
  window.addEventListener('click', swallow, {capture: true, once: true});
  setTimeout(() => window.removeEventListener('click', swallow, true), 0);
  const t = state.tasks.find(x => x.id === d.id);
  const onto = state.tasks.find(x => x.id === d.onto);
  if (drop && t && onto) void taskDropOpen(state, t, onto);
  else if (drop && t && d.target && d.target !== d.from) pvMove(state, t, d.target);
}

// ---------------------------------------------------------------- the drop dialog

/** GET /api/v2/tasks/{id}/merge?into=: both tasks, why each choice cannot be done (null when it can), what moves. */
interface TaskDropEnd { id: string; number: number | null; title: string; owner_id: string | null; owner: string }
interface TaskDropMoves {
  comments: number; asks: string[]; subtasks: number; files: number; followers: number; requesters: number;
  labels: number; links: number; description: boolean; due: string | null;
}
interface TaskDropChecks {
  task: TaskDropEnd; into: TaskDropEnd; same_owner: boolean; subtask: string | null; merge: string | null; moves: TaskDropMoves;
}

const taskDropName = (e: TaskDropEnd): string => e.number != null ? `#${e.number}` : e.title;
const taskDropCount = (n: number, one: string, many = one + 's'): string => `${n} ${n === 1 ? one : many}`;

/** What a merge moves, in a line each, counted from the real tasks before anyone confirms. */
function taskDropMovesHTML(c: TaskDropChecks): string {
  const m = c.moves, a = esc(taskDropName(c.task)), b = esc(taskDropName(c.into));
  const rows: string[] = [];
  if (m.description || m.files) rows.push(`Description${m.files ? `, ${taskDropCount(m.files, 'file or page', 'files and pages')}` : ''}: added to ${b} under “From ${a}”`);
  if (m.subtasks) rows.push(`${taskDropCount(m.subtasks, 'open subtask')}: move under ${b}`);
  if (m.comments) rows.push(`${taskDropCount(m.comments, 'comment')}: stay on ${a}; ${b}’s note links to them`);
  if (m.asks.length) rows.push(`${taskDropCount(m.asks.length, 'open ask')}: end with ${a}, listed in ${b}’s note`);
  if (m.followers || m.requesters) rows.push(`${[m.followers ? taskDropCount(m.followers, 'follower') : '', m.requesters ? taskDropCount(m.requesters, 'requester') : ''].filter(Boolean).join(', ')}: added to ${b}`);
  if (m.labels || m.links) rows.push(`${[m.labels ? taskDropCount(m.labels, 'label') : '', m.links ? taskDropCount(m.links, 'related link') : ''].filter(Boolean).join(', ')}: added to ${b}`);
  rows.push(`${a}: closed as a duplicate of ${b}`);
  return `<ul class="tb-drop-moves" data-tb-moves>${rows.map(r => `<li>${r}</li>`).join('')}</ul>`;
}

function taskDropChoiceHTML(value: 'subtask' | 'merge', title: string, line: string, why: string | null, checked: boolean): string {
  return `<label class="tb-drop-choice${why ? ' off' : ''}"><input type="radio" name="choice" value="${value}"${why ? ' disabled' : checked ? ' checked' : ''}>
    <span><strong>${esc(title)}</strong><span class="muted">${esc(why ?? line)}</span></span></label>`;
}

/** Letting a task go over another's card: the two choices, the owner choice when the owners differ and Merge is picked,
    and what moves. The button says what it does. */
async function taskDropOpen(state: PvState, t: TaskAny, onto: TaskAny): Promise<void> {
  let dlg = document.getElementById('tb-drop') as HTMLDialogElement | null;
  if (!dlg) { dlg = document.createElement('dialog'); dlg.id = 'tb-drop'; dlg.className = 'tmodal tmodal-shell tb-drop'; document.body.append(dlg); }
  const box = dlg;
  box.setAttribute('aria-label', 'Drop a task on another');
  box.innerHTML = `<div class="tmodal-body"><p class="muted">Reading both tasks…</p></div>`;
  if (!box.open) box.showModal();
  let c: TaskDropChecks;
  try {
    c = await get<TaskDropChecks>(`${taskPath(t.id)}/merge?into=${encodeURIComponent(onto.id)}`, {fresh: true});
  } catch (e) {
    box.innerHTML = `<form method="dialog"><div class="tmodal-body"><p class="err" role="alert">${esc(errorMessage(e, 'These tasks cannot be joined.'))}</p></div>
      <footer class="tmodal-foot tb-drop-foot"><span class="spacer"></span><button class="primary" type="submit">OK</button></footer></form>`;
    return;
  }
  const a = taskDropName(c.task), b = taskDropName(c.into);
  const first: 'subtask' | 'merge' | '' = c.subtask == null ? 'subtask' : c.merge == null ? 'merge' : '';
  box.innerHTML = `<form><header class="tmodal-head"><h2>Drop ${esc(a)} on ${esc(b)}</h2><span class="spacer"></span>
      <button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></header>
    <div class="tmodal-body">
      <p class="tb-drop-titles"><span>${esc(a)} ${esc(c.task.title)}</span><span class="muted">onto</span><span>${esc(b)} ${esc(c.into.title)}</span></p>
      <fieldset class="tb-drop-choices"><legend class="sr-only">What to do</legend>
        ${taskDropChoiceHTML('subtask', 'Make it a subtask', `${a} goes under ${b}. Both keep their owners and stages.`, c.subtask, first === 'subtask')}
        ${taskDropChoiceHTML('merge', 'Merge the tasks', `${b} stays and gets ${a}’s details; ${a} is archived as a duplicate.`, c.merge, first === 'merge')}
      </fieldset>
      ${c.same_owner ? '' : `<fieldset class="tb-drop-owner" data-tb-owner hidden><legend>Who keeps ${esc(b)}</legend>
        <label><input type="radio" name="owner" value="${esc(c.into.owner_id ?? '')}" checked> ${esc(c.into.owner)} <span class="muted">owns ${esc(b)}</span></label>
        <label><input type="radio" name="owner" value="${esc(c.task.owner_id ?? '')}"> ${esc(c.task.owner)} <span class="muted">owns ${esc(a)}</span></label></fieldset>`}
      <div data-tb-what hidden>${taskDropMovesHTML(c)}</div>
    </div>
    <footer class="tmodal-foot tb-drop-foot"><span class="err" role="alert" data-tb-err></span><span class="spacer"></span>
      <button type="button" class="ghost" data-close>Cancel</button><button class="primary" type="submit" data-tb-go${first ? '' : ' disabled'}>Make subtask</button></footer></form>`;
  const form = box.querySelector('form')!;
  const go = form.querySelector<HTMLButtonElement>('[data-tb-go]')!;
  const picked = (): string => (form.querySelector<HTMLInputElement>('[name="choice"]:checked')?.value ?? '');
  const show = (): void => {
    const merge = picked() === 'merge';
    const owner = form.querySelector<HTMLElement>('[data-tb-owner]');
    if (owner) owner.hidden = !merge;
    form.querySelector<HTMLElement>('[data-tb-what]')!.hidden = !merge;
    go.textContent = merge ? 'Merge' : 'Make subtask';
    go.disabled = !picked();
  };
  show();
  form.addEventListener('change', show);
  for (const x of form.querySelectorAll<HTMLElement>('[data-close]')) x.onclick = () => box.close();
  form.onsubmit = async (ev: SubmitEvent) => {
    ev.preventDefault();
    const choice = picked(), err = form.querySelector<HTMLElement>('[data-tb-err]')!;
    if (!choice) return;
    go.disabled = true; err.textContent = '';
    try {
      if (choice === 'merge') {
        const owner = form.querySelector<HTMLInputElement>('[name="owner"]:checked')?.value;
        const done = await post(`${taskPath(t.id)}/merge`,
          {into: onto.id, ...(owner && owner !== c.into.owner_id ? {owner} : {})}, newRequestId()) as {task: TaskAny; closed: TaskAny};
        if (PV === state) state.tasks = state.tasks.filter(x => x.id !== t.id).map(x => x.id === done.task.id ? done.task : x);
      } else {
        const done = await post(`${taskPath(t.id)}/parent`, {parent: onto.id}, newRequestId()) as {task: TaskAny};
        if (PV === state) state.tasks = state.tasks.map(x => x.id === done.task.id ? done.task : x);
      }
      box.close();
      if (PV === state) pvDraw(state);
    } catch (e) {
      // Refused: nothing changed; the dialog says why and stays open.
      err.textContent = taskRefusedOf(e)?.detail || errorMessage(e, 'Not changed.');
      go.disabled = false;
    }
  };
}
