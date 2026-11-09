/* ui/app/task-board.ts — The board's hands: dragging cards between and within a type's steps, the folded backlog and
   finished strips, the blocked strip and its focus, and the owner faces over the columns.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The board's shapes. A card's row and item are the tasks page's (tasks.ts: TaskRow, TaskItem); a drag and a move are the
// board's own, and the board sets its fields on the page's state.
// A relation of a task (taskBlockers) with the number a blocker may carry when the server sends one.
type BoardBlocker = TaskRelRow & {number?: number | null | undefined};
// A card's change: the fields it sets on the row, drawn before the server answers (the owner, the step, the rank).
type TbFields = {owner_id?: string; [key: string]: unknown};
// What a card draws: its move while the server has not said otherwise, else the loaded task.
type TbLive = {task: TaskRow; actor?: string | null | undefined};
type TbFocus = {key: string; keys: Set<string>};
type TbFold = {backlog?: boolean | undefined; finished?: boolean | undefined};
type TbMove = {fields: TbFields; version: number; pending: boolean};
// A held card: the press sets the first fields; the lift (the card drawn under the pointer) sets the rest.
type TbDrag = {
  card: HTMLElement; key: string; x0: number; y0: number; x: number; y: number; on: boolean;
  dx?: number | undefined; dy?: number | undefined; ghost?: HTMLElement | undefined;
  from?: HTMLElement | null | undefined; fromIndex?: number | undefined;
  target?: HTMLElement | null | undefined; prev?: string | null | undefined; next?: string | null | undefined; index?: number | undefined;
  tick?: number | undefined; off?: (() => void) | undefined;
};
// The tasks page's state (TASKS_ST) with the fields the board keeps on it.
type TbState = TaskState & {
  boardFocus?: TbFocus | null | undefined; boardDrag?: TbDrag | null | undefined; boardMoves?: Map<string, TbMove> | undefined;
};
// The Owner filter's values: the people, bots or 'me' the filter was set to (tasks.ts keeps them in its filters).
const taskOwnerFilter = (state: TbState): string[] => (state.filters.owner || []) as string[];

// ---- folds: a type's leading backlog steps and its trailing finished steps, each one strip, remembered in this browser
const BOARD_FOLD_KEY = 'tico.board.fold';
let BOARD_FOLDS: Record<string, TbFold> | null = null;
function taskBoardFoldsSaved() {
  if (BOARD_FOLDS) return BOARD_FOLDS;
  try { BOARD_FOLDS = (JSON.parse(localStorage.getItem(BOARD_FOLD_KEY) || '{}') || {}) as Record<string, TbFold>; } catch { BOARD_FOLDS = {}; }
  return BOARD_FOLDS;
}
// Two or more backlog steps start folded and a single one open; the finished steps start folded.
function taskBoardFolds(state: TbState, type: {id: string}, backlogSteps = 0) {
  // A phone shows one stage at a time, each a chip, finished stages included: nothing folds there.
  if (pipelinePhone()) return {backlog: false, finished: false};
  const saved: TbFold = taskBoardFoldsSaved()[type.id] || {};
  return {backlog: saved.backlog ?? backlogSteps > 1, finished: saved.finished ?? true};
}
function taskBoardFoldSet(state: TbState, fold: string, folded: boolean) {
  const type = pipelineSelectedType(state); if (!type) return;
  const all = taskBoardFoldsSaved();
  all[type.id] = {...all[type.id], [fold]: folded};
  try { localStorage.setItem(BOARD_FOLD_KEY, JSON.stringify(all)); } catch {}
  // Finished tasks come from the Done list's newest page; the first unfold reads it.
  tasksDoneEnsure(state);
  tasksRender(state);
}
// The board's finished steps show: the selected type's board, with its finished strip unfolded (also when restored).
function taskBoardFinishedShown(state: TbState) {
  const type = state.view === 'board' && pipelineSelectedType(state);
  return !!type && !taskBoardFolds(state, type).finished;
}
// A type's board holds its open work, and its finished and declined tasks while its finished steps show.
function taskBoardShows(it: TaskItem, state: TbState) {
  if (it.col !== 'done' && it.task.status !== 'declined') return true;
  const type = pipelineSelectedType(state);
  return !!type && !taskBoardFolds(state, type).finished;
}

// ---- blocked: one line at a card's top, "Blocked 7h: <first open blocker>", clipped to the card; the line focuses
// the board on the card and its blockers. The hold's reason and date and every blocker are the tooltip.
const taskOpenBlockers = (t: TaskRow): BoardBlocker[] => taskBlockers(t).filter((r: BoardBlocker) => !['done', 'closed', 'declined'].includes(String(r.status || '')));
// How long a hold has held: 7m, 7h, 9d (no date fallback, unlike ageShort).
function taskHeldFor(iso: string | null | undefined) {
  const at = Date.parse(iso || ''); if (!at) return '';
  const s = Math.max(0, (Date.now() - at) / 1000);
  return s < 3600 ? `${Math.max(1, Math.round(s / 60))}m` : s < 86400 ? `${Math.round(s / 3600)}h` : `${Math.round(s / 86400)}d`;
}
// Reasons the server writes when a hold has no note of its own: the line says only how long.
const TASK_PLAIN_HOLDS = new Set(['Waiting on a hold', 'Waiting on a dependency']);
function taskBlockedStrip(it: TaskItem) {
  const t = it.task, held = taskBlockedHold(t), open = taskOpenBlockers(t);
  if (!held && !open.length) return '';
  const age = held ? taskHeldFor(held.since) : '', head = `Blocked${age ? ' ' + age : ''}`;
  const line = (what: string) => `<span>${esc(what ? `${head}: ${what}` : head)}</span>`;
  if (!open.length) {
    if (!held) return '';          // the early return above leaves a hold here
    const why = String(held.reason || '').trim();
    return `<div class="bcard-blocked" title="${esc(taskBlockedLine(t))}">${line(TASK_PLAIN_HOLDS.has(why) ? '' : why)}</div>`;
  }
  // A bot's prerequisite work names the bot; the reason, its age and what blocks it are the tooltip.
  const bot = taskBotWait(t);
  const all = open.map(r => r.title || '#' + (r.number ?? r.id)).join(', ');
  const first = open[0];
  if (!first) return '';           // open has a blocker here
  const name = first.title || '#' + (first.number ?? first.id);
  const label = bot ? `<span>${esc(`Waiting on ${bot.name}`)}</span>` : line(name);
  const tip = `${bot ? taskWaitLine(t) : held ? taskBlockedLine(t) : ''}${bot || held ? '\n' : ''}Blocked by ${all}`;
  return `<button type="button" class="bcard-blocked" data-board-blocked="${esc(it.key)}"${bot ? ' data-board-waits-bot' : ''} title="${esc(tip)}">${label}${open.length > 1 ? `<span class="tnum">+${open.length - 1}</span>` : ''}</button>`;
}
function taskBoardFocused(items: TaskItem[], state: TbState) {
  const focus = state.boardFocus;
  return focus ? items.filter(it => focus.keys.has(it.key)) : items;
}
function taskBoardFocus(state: TbState, key: string) {
  const t = tasksById(state).get(key.slice(1)); if (!t) return;
  state.boardFocus = {key, keys: new Set([key, ...taskOpenBlockers(t).map(r => 't' + r.id)])};
  tasksRender(state);
}

// ---- the faces over the board: whoever has the most cards here (you first), each a toggle on the Owner filter
const taskOwnerHit = (actor: string) => (v: string) => resolveMe(v) === actor || (!v.includes(':') && actor === 'bot:' + v);
function taskBoardHead(items: TaskItem[], state: TbState) {
  const me = myActor(), chosen = taskOwnerFilter(state);
  // Counted without the Owner filter, so a face stays to be turned off again.
  // @ts-expect-error memo null skips the cache (taskItems reads it with ?.); tasks.ts (open PR) types it Map, not Map | null
  const all = chosen.length ? taskItems({...state, filters: {...state.filters, owner: []}, memo: null}, 'board') : items;
  const counts = new Map<string, number>();
  for (const it of all) if (it.actor) counts.set(it.actor, (counts.get(it.actor) || 0) + 1);
  const on = (actor: string) => chosen.some(taskOwnerHit(actor));
  const ranked = [...counts.entries()].sort((a, b) => Number(b[0] === me) - Number(a[0] === me) || b[1] - a[1]
    || actorLabel(a[0]).localeCompare(actorLabel(b[0])));
  const shown = ranked.filter(([actor], i) => i < 10 || on(actor));
  const faces = shown.length > 1 || chosen.length ? shown.map(([actor, n]) => {
    const name = actor === me ? 'You' : actorLabel(actor);
    return `<button type="button" class="bface" data-board-owner="${esc(actor)}" aria-pressed="${on(actor)}" aria-label="${esc(`${name}, ${n} task${n === 1 ? '' : 's'}`)}" title="${esc(`${name} · ${n}`)}">${actorFace(actor, 22)}</button>`;
  }).join('') : '';
  const focus = state.boardFocus ? `<button type="button" class="tl-chip tl-chip-main bfocus" data-board-unfocus aria-label="Clear blocked focus" title="Show every card (X)">${TL_ICON.x}<span>Blocked focus</span></button>` : '';
  return `<div class="board-head">${faces ? `<span class="bfaces" role="group" aria-label="Owners">${faces}</span>` : ''}${focus}</div>`;
}
function taskBoardOwnerToggle(state: TbState, actor: string) {
  const chosen = taskOwnerFilter(state), hit = taskOwnerHit(actor);
  state.filters.owner = chosen.some(hit) ? chosen.filter(v => !hit(v)) : [...chosen, actor === myActor() ? 'me' : actor];
  tasksFiltersChanged(state);
}
// Q shows only your cards (again, everyone's); X clears the faces and the blocked focus. Never while typing.
function taskBoardKeys(state: TbState, ev: KeyboardEvent) {
  if (TASKS_ST !== state || state.view !== 'board' || state.boardDrag) return;
  if (ev.defaultPrevented || ev.metaKey || ev.ctrlKey || ev.altKey || ev.isComposing) return;
  if (document.querySelector('dialog:modal, :popover-open')) return;
  const el = document.activeElement;
  if (el && (el instanceof HTMLElement && el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName))) return;
  const key = ev.key.toLowerCase(), chosen = taskOwnerFilter(state);
  if (key === 'q') {
    ev.preventDefault();
    const mine = chosen.length === 1 && resolveMe(chosen[0]!) === myActor();
    state.filters.owner = mine ? [] : ['me'];
    tasksFiltersChanged(state);
  } else if (key === 'x' && (chosen.length || state.boardFocus)) {
    ev.preventDefault();          // with nothing to clear, x selects the card under the cursor as before
    state.filters.owner = []; state.boardFocus = null;
    tasksFiltersChanged(state);
  }
}
function taskBoardClick(state: TbState, ev: MouseEvent) {
  if (TASKS_ST !== state || state.view !== 'board') return;
  const target = eventElement(ev);
  if (!target) return;
  const hit = target.closest<HTMLElement>('[data-board-owner],[data-board-blocked],[data-board-unfocus],[data-board-unfold],[data-board-fold],[data-card-owner],[data-board-select]');
  if (!hit) return;
  ev.stopPropagation(); ev.preventDefault();
  if (hit.dataset.boardSelect) taskBoardSelectColumn(state, hit);
  else if (hit.dataset.cardOwner) taskBoardOwnerMenu(state, hit);
  else if (hit.dataset.boardOwner) taskBoardOwnerToggle(state, hit.dataset.boardOwner);
  else if (hit.dataset.boardBlocked) taskBoardFocus(state, hit.dataset.boardBlocked);
  else if (hit.hasAttribute('data-board-unfocus')) { state.boardFocus = null; tasksRender(state); }
  else if (hit.dataset.boardUnfold) taskBoardFoldSet(state, hit.dataset.boardUnfold, false);
  else if (hit.dataset.boardFold) taskBoardFoldSet(state, hit.dataset.boardFold, true);
}
// Select all in this column: every card it shows. A type's step column is also a filter the server can run (that type,
// that step, the open statuses a board shows), so the bulk change reaches the step's tasks as the board has them.
function taskBoardSelectColumn(state: TbState, button: HTMLElement) {
  // A board column is a .bcol; a phone's one stage (task-pipelines.js pipePhoneStageHTML) is a .pipe-stage.
  const column = button.closest('.bcol, .pipe-stage'); if (!column) return;
  // The selector names the attribute, so every card here has its key.
  const keys = [...column.querySelectorAll<HTMLElement>('.bcard[data-task-key]')].map(card => String(card.dataset.taskKey));
  const name = column.querySelector('h2')?.textContent || column.getAttribute('aria-label') || '';
  const byId = tasksById(state);
  // A finished or declined card in the column is shown, but the open statuses would miss it: its tasks go by id.
  const open = keys.every(k => ['open', 'doing', 'waiting', 'review', 'ready'].includes(byId.get(k.slice(1))?.status || 'open'));
  const base = button.dataset.boardStep && open
    ? {type: button.dataset.boardType, step: button.dataset.boardStep, status: 'open,doing,waiting,review,ready'} : null;
  tasksBulkSelectAll(state, keys, base, name ? `in ${name}` : 'in this column');
}

// ---- dragging a card, on a desktop pointer. The page carries it rather than the browser's drag and drop, so the
// wheel and the trackpad still scroll the columns and the board while it is held. A phone moves a card with the
// peek's Step instead.
const BOARD_SLOP = 5;
const taskBoardHolding = (state: TbState) => !!state.boardDrag?.on;
function taskBoardBind(state: TbState, body: HTMLElement) {
  const abort = state.layoutAbort; if (!abort) return;   // the page makes it before any board draws
  const signal = abort.signal;
  body.addEventListener('click', ev => taskBoardClick(state, ev), {capture: true, signal});
  body.addEventListener('pointerdown', ev => taskBoardPress(state, ev), {signal});
  document.addEventListener('keydown', ev => taskBoardKeys(state, ev), {signal});
}
function taskBoardPress(state: TbState, ev: PointerEvent) {
  if (TASKS_ST !== state || state.view !== 'board' || state.boardDrag) return;
  if (ev.button !== 0 || ev.pointerType === 'touch' || ev.shiftKey || ev.metaKey || ev.ctrlKey || ev.altKey || matchMedia(PHONE).matches) return;
  const target = eventElement(ev);
  if (!target) return;
  const card = target.closest<HTMLElement>('.board[data-board-drag] .bcard');
  if (!card || target.closest('a, input, button:not(.bcard-open)')) return;
  const taskKey = card.dataset.taskKey;
  if (!taskKey) return;          // a card on the board always has its key
  const id = taskKey.slice(1);
  if (state.boardMoves?.get(id)?.pending) return;          // one move at a time per card
  const d: TbDrag = state.boardDrag = {card, key: taskKey, x0: ev.clientX, y0: ev.clientY, x: ev.clientX, y: ev.clientY, on: false};
  const move = (e: PointerEvent) => taskBoardMove(state, e), up = (e: PointerEvent) => taskBoardUp(state, e), cancel = () => taskBoardEnd(state, false);
  const keys = (e: KeyboardEvent) => { if (e.key === 'Escape' && state.boardDrag === d && d.on) { e.preventDefault(); e.stopPropagation(); taskBoardEnd(state, false); } };
  const scrolled = () => taskBoardOver(state);
  document.addEventListener('pointermove', move, true);
  document.addEventListener('pointerup', up, true);
  document.addEventListener('pointercancel', cancel, true);
  document.addEventListener('keydown', keys, true);
  document.addEventListener('scroll', scrolled, true);
  d.off = () => {
    document.removeEventListener('pointermove', move, true);
    document.removeEventListener('pointerup', up, true);
    document.removeEventListener('pointercancel', cancel, true);
    document.removeEventListener('keydown', keys, true);
    document.removeEventListener('scroll', scrolled, true);
  };
}
function taskBoardMove(state: TbState, e: PointerEvent) {
  const d = state.boardDrag; if (!d) return;
  if (!e.buttons) { taskBoardEnd(state, false); return; }            // let go outside the window
  d.x = e.clientX; d.y = e.clientY;
  if (!d.on) {
    if (Math.abs(d.x - d.x0) < BOARD_SLOP && Math.abs(d.y - d.y0) < BOARD_SLOP) return;
    taskBoardLift(d);
  }
  if (d.dx === undefined || d.dy === undefined || !d.ghost) return;  // the lift above sets all three
  e.preventDefault();
  d.ghost.style.transform = `translate(${d.x - d.dx}px,${d.y - d.dy}px)`;
  taskBoardOver(state);
  taskBoardAutoScroll(state);
}
function taskBoardLift(d: TbDrag) {
  d.on = true;
  const r = d.card.getBoundingClientRect();
  d.dx = d.x0 - r.left; d.dy = d.y0 - r.top;
  d.from = d.card.closest<HTMLElement>('.bcol');
  const parent = d.card.parentNode;
  d.fromIndex = parent ? [...parent.querySelectorAll<HTMLElement>('.bcard')].indexOf(d.card) : -1;
  const ghost = d.card.cloneNode(true) as HTMLElement;
  ghost.removeAttribute('data-task-key');
  ghost.classList.remove('peeked', 'is-cursor', 'sel');
  ghost.classList.add('bghost');
  ghost.setAttribute('aria-hidden', 'true');
  ghost.style.width = r.width + 'px';
  ghost.style.transform = `translate(${r.left}px,${r.top}px)`;
  document.body.append(ghost);
  d.ghost = ghost;
  d.card.classList.add('dragging');
  document.body.classList.add('board-holding');
  getSelection()?.removeAllRanges();
}
// What is under the held card: a step's column (or a folded strip), and the place in its cards, shown by a line.
function taskBoardOver(state: TbState) {
  const d = state.boardDrag; if (!d?.on) return;
  const under = document.elementFromPoint(d.x, d.y);
  const col = under?.closest?.<HTMLElement>('#task-body .board .bcol[data-drop-step]') || null;
  document.querySelectorAll('#task-body .bcol.over').forEach(el => { if (el !== col) el.classList.remove('over'); });
  d.target = col; d.prev = d.next = null; d.index = -1;
  let line = document.querySelector('#task-body .bdrop');
  const body = col && !col.classList.contains('bfold') ? col.querySelector<HTMLElement>('.bcol-body') : null;
  col?.classList.add('over');
  if (!body) { line?.remove(); return; }
  const cards = [...body.querySelectorAll<HTMLElement>('.bcard')].filter(c => c !== d.card);
  let at = cards.findIndex(c => { const r = c.getBoundingClientRect(); return d.y < r.top + r.height / 2; });
  if (at < 0) at = cards.length;
  d.index = at; d.prev = cards[at - 1]?.dataset.taskKey || null; d.next = cards[at]?.dataset.taskKey || null;
  if (!line) { line = document.createElement('div'); line.className = 'bdrop'; line.setAttribute('aria-hidden', 'true'); }
  const ref = cards[at] || null;
  if (ref ? line.nextElementSibling !== ref : body.lastElementChild !== line) body.insertBefore(line, ref);
}
// Held near a side of the board it scrolls that way, and near the top or the bottom of a column, that column; faster
// the deeper in, for as long as the card is held there.
function taskBoardAutoScroll(state: TbState) {
  const d = state.boardDrag;
  if (!d?.on || d.tick) return;
  const by = (depth: number, edge: number) => Math.ceil(Math.min(depth, edge * 1.5) / 4);
  const step = () => {
    d.tick = 0;
    if (state.boardDrag !== d) return;
    let moved = false;
    const board = document.querySelector('#task-body .board');
    if (board) {
      const r = board.getBoundingClientRect(), edge = 60, left = board.scrollLeft;
      if (d.y >= r.top && d.y <= r.bottom) {
        if (d.x < r.left + edge) board.scrollLeft = left - by(r.left + edge - d.x, edge);
        else if (d.x > r.right - edge) board.scrollLeft = left + by(d.x - (r.right - edge), edge);
      }
      moved = board.scrollLeft !== left;
    }
    const body = document.elementFromPoint(d.x, d.y)?.closest?.('#task-body .bcol')?.querySelector('.bcol-body');
    if (body) {
      const r = body.getBoundingClientRect(), edge = Math.max(24, Math.min(60, r.height / 4)), top = body.scrollTop;
      if (d.y < r.top + edge) body.scrollTop = top - by(r.top + edge - d.y, edge);
      else if (d.y > r.bottom - edge) body.scrollTop = top + by(d.y - (r.bottom - edge), edge);
      moved = moved || body.scrollTop !== top;
    }
    if (moved) { taskBoardOver(state); d.tick = requestAnimationFrame(step); }
  };
  d.tick = requestAnimationFrame(step);
}
function taskBoardUp(state: TbState, e: PointerEvent) {
  const d = state.boardDrag; if (!d) return;
  if (d.on) { d.x = e.clientX; d.y = e.clientY; taskBoardOver(state); }
  taskBoardEnd(state, d.on);
}
function taskBoardEnd(state: TbState, drop: boolean) {
  const d = state.boardDrag; if (!d) return;
  state.boardDrag = null;
  if (d.off) d.off();                    // the press sets it
  if (d.tick) cancelAnimationFrame(d.tick);
  if (!d.on) return;                     // a press that never moved is a click: the peek opens as always
  d.ghost?.remove();
  d.card.classList.remove('dragging');
  document.body.classList.remove('board-holding');
  document.querySelectorAll('#task-body .bdrop').forEach(n => n.remove());
  document.querySelectorAll('#task-body .bcol.over').forEach(n => n.classList.remove('over'));
  // The click the carry ends in opens nothing; after Esc, the button is still down, so that is the next release.
  const swallow = (ev: Event) => { ev.stopPropagation(); ev.preventDefault(); };
  const arm = () => {
    window.addEventListener('click', swallow, {capture: true, once: true});
    setTimeout(() => window.removeEventListener('click', swallow, true), 0);
  };
  if (drop) arm(); else document.addEventListener('pointerup', arm, {capture: true, once: true});
  if (!(drop && d.target && taskBoardDrop(state, d))) tasksRender(state);   // a saved drop draws itself
}

// ---- the move: the step and the place (step_rank) at once, drawn before the server answers. A refusal puts the card
// back and says why; a load that set out before the save cannot put it back meanwhile (taskBoardLive).
function taskBoardRank(prev: number | null | undefined, next: number | null | undefined) {
  if (prev != null && next != null) return (prev + next) / 2;
  if (prev != null) return prev + 1;
  if (next != null) return next - 1;
  return null;
}
function taskBoardDrop(state: TbState, d: TbDrag) {
  const target = d.target;
  if (!target) return false;     // the caller drops on a column only
  const type = pipelineSelectedType(state), stepId = target.dataset.dropStep;
  const step = type?.steps.find((s: {id: string}) => s.id === stepId);
  const byId = tasksById(state), id = d.key.slice(1), t = byId.get(id);
  if (!step || !t) return false;
  if (d.target === d.from && d.index === d.fromIndex) return false;           // put back where it was
  const rankOf = (key: string | null | undefined): number | null => { const x = key && byId.get(key.slice(1)); return x ? taskBoardLive(state, {task: x}).task.step_rank ?? null : null; };
  let rank: number | null;
  if (d.prev || d.next) rank = taskBoardRank(rankOf(d.prev), rankOf(d.next));
  else {
    // A folded strip, an empty column, or one whose cards are filtered away: the end of the step.
    const ranks = (state.tasks || []).filter(x => x.step_id === stepId && String(x.id) !== id && x.step_rank != null).map(x => Number(x.step_rank));
    rank = ranks.length ? Math.max(...ranks) + 1 : 1;
  }
  const moving = stepId !== t.step_id;
  if (!moving && rank == null) return false;
  const body = {version: t.version, ...(moving ? {step: stepId} : {}), ...(rank != null ? {step_rank: rank} : {}),
    ...(moving && step.status === 'done' && t.status !== 'done' ? {status: 'done'} : {})};
  const fields = {...(moving ? {step_id: stepId, step, status: t.status === 'waiting' && !BOARD_HIDDEN.has(step.status) ? 'waiting' : step.status} : {}),
    ...(rank != null ? {step_rank: rank} : {})};
  if (moving && step.status === 'waiting') {
    void taskBlockedReason(t, d.card.querySelector<HTMLElement>('[data-open-task]')).then((reason: string | null) => {
      if (reason !== null && TASKS_ST === state) taskBoardSave(state, t, {...body, note: reason}, fields, 'Not moved');
    });
  } else {
    taskBoardSave(state, t, body, fields, 'Not moved');
    if (body.status === 'done') taskDoneMotion(d.key);   // the card, now in its done column, draws its check
  }
  return true;
}
// A card's change (a move, a new owner) drawn at once, before the server answers; a refusal puts it back and says why.
// task-requirements.ts calls this with the page state and a row it types narrower, so both arrive as unknown here.
function taskBoardSave(stateIn: unknown, taskIn: unknown, body: Record<string, unknown>, fields: TbFields, refused: string) {
  const state = stateIn as TbState, t = taskIn as TaskRow;
  const id = String(t.id);
  state.refused?.delete(id);
  state.boardMoves ||= new Map();
  state.boardMoves.set(id, {fields, version: Number(t.version), pending: true});
  state.tasks = state.tasks.map(x => x === t ? {...t, ...fields} : x);
  tasksRender(state);
  void (async () => {
    try {
      const data = await post(`/v2/tasks/${encodeURIComponent(id)}`, body);
      if (TASKS_ST !== state) return;
      const move = state.boardMoves?.get(id); if (move) move.pending = false;
      if (data?.task) state.tasks = mergeTaskRows(state.tasks, [data.task]);
      tasksRender(state);
    } catch (e) {
      if (TASKS_ST !== state) return;
      state.boardMoves?.delete(id);
      state.tasks = state.tasks.map(x => String(x.id) === id && Number(x.version) <= Number(t.version) ? t : x);
      if (errorStatus(e) === 409) toast('Changed elsewhere. Showing the latest.', true);
      else if (pipelineOverrideOffer(state, {id: t.id, ...(t.type_id ? {type_id: t.type_id} : {})}, body, fields, e)) { /* a pipeline editor may move it anyway (task-requirements.ts) */ }
      else taskRefusedSet(state, t, `${refused}: ${errorMessage(e, 'refused')}`);
      tasksRender(state);
    }
    void tasksLoad(state);
  })();
}
// The face on a card is its owner: pressing it picks another person or bot, saved from the card (the peek's Owner menu).
function taskBoardOwnerMenu(state: TbState, button: HTMLElement) {
  const t = tasksById(state).get(String(button.dataset.cardOwner).slice(1));
  if (!t || !taskPropRights(t).edit) return;
  const opts = new DOMParser().parseFromString(`<select>${taskOwnerOptions('')}</select>`, 'text/html').querySelectorAll<HTMLOptionElement>('option[value]:not([value=""])');
  propMenu($('#task-card-pops'), button, {label: 'Owner', find: 'Find a person or bot', items: [...opts].map(o => {
    const actor = o.value.includes(':') ? o.value : 'bot:' + o.value;
    return {value: o.value, text: o.textContent, html: `<span class="tl-opt-face">${actorFace(actor, 16)}</span><span>${esc(o.textContent)}</span>`, checked: t.owner_id === actor, actor};
  }), onPick: (it: {actor: string}) => {
    const cur = tasksById(state).get(String(t.id));
    if (!cur || it.actor === cur.owner_id) return;
    taskBoardSave(state, cur, {version: cur.version, owner: it.actor}, {owner_id: it.actor}, 'Not changed');
  }});
}
// A card as the board draws it: with its move while the server has not said otherwise (a copy no newer than the one
// it was moved from), else as loaded.
function taskBoardLive(state: TbState, item: TbLive): TbLive {
  const id = String(item.task?.id ?? ''), moves = state.boardMoves, move = moves?.get(id);
  if (!moves || !move) return item;
  if (Number(item.task.version) > move.version) {
    if (!move.pending) moves.delete(id);
    return item;
  }
  return {...item, task: {...item.task, ...move.fields}, ...(move.fields.owner_id ? {actor: move.fields.owner_id} : {})};
}
