/* ui/app/task-board.js — The board's hands: dragging cards between and within a type's steps, the folded backlog and
   finished strips, the blocked strip and its focus, and the owner faces over the columns.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ---- folds: a type's leading backlog steps and its trailing finished steps, each one strip, remembered in this browser
const BOARD_FOLD_KEY = 'tico.board.fold';
let BOARD_FOLDS = null;
function taskBoardFoldsSaved() {
  if (BOARD_FOLDS) return BOARD_FOLDS;
  try { BOARD_FOLDS = JSON.parse(localStorage.getItem(BOARD_FOLD_KEY) || '{}') || {}; } catch { BOARD_FOLDS = {}; }
  return BOARD_FOLDS;
}
// Two or more backlog steps start folded and a single one open; the finished steps start folded.
function taskBoardFolds(state, type, backlogSteps = 0) {
  const saved = taskBoardFoldsSaved()[type.id] || {};
  return {backlog: saved.backlog ?? backlogSteps > 1, finished: saved.finished ?? true};
}
function taskBoardFoldSet(state, fold, folded) {
  const type = pipelineSelectedType(state); if (!type) return;
  const all = taskBoardFoldsSaved();
  all[type.id] = {...all[type.id], [fold]: folded};
  try { localStorage.setItem(BOARD_FOLD_KEY, JSON.stringify(all)); } catch {}
  // Finished tasks come from the Done list's newest page; the first unfold reads it.
  tasksDoneEnsure(state);
  tasksRender(state);
}
// The board's finished steps show: the selected type's board, with its finished strip unfolded (also when restored).
function taskBoardFinishedShown(state) {
  const type = state.view === 'board' && pipelineSelectedType(state);
  return !!type && !taskBoardFolds(state, type).finished;
}
// A type's board holds its open work, and its finished and declined tasks while its finished steps show.
function taskBoardShows(it, state) {
  if (it.col !== 'done' && it.task.status !== 'declined') return true;
  const type = pipelineSelectedType(state);
  return !!type && !taskBoardFolds(state, type).finished;
}

// ---- blocked: a card with open blockers says so at its top; the strip focuses the board on it and them
const taskOpenBlockers = t => taskBlockers(t).filter(r => !['done', 'closed', 'declined'].includes(String(r.status || '')));
function taskBlockedStrip(it) {
  const t = it.task;
  // A Blocked hold that is a bot's prerequisite is the bot's wait line, not a second line above it.
  const bot = taskBotWait(t);
  const hold = taskBlockedHold(t) && !bot ? `<div class="bcard-blocked">${esc(taskBlockedLine(t))}</div>` : '';
  const open = taskOpenBlockers(t); if (!open.length) return hold;
  const first = open[0], name = first.title ? clipLine(first.title, 48) : '#' + (first.number ?? first.id);
  const all = open.map(r => r.title || '#' + (r.number ?? r.id)).join(', ');
  // A bot's prerequisite work names the bot; the reason, its age and what blocks it are the tooltip.
  const label = bot ? `Waiting on ${clipLine(bot.name, 48)}` : `Blocked by ${name}`;
  const tip = bot ? `${taskWaitLine(t)}\nBlocked by ${all}` : `Blocked by ${all}`;
  return hold + `<button type="button" class="bcard-blocked" data-board-blocked="${esc(it.key)}"${bot ? ' data-board-waits-bot' : ''} title="${esc(tip)}"><span>${esc(label)}</span>${open.length > 1 ? `<span class="tnum">+${open.length - 1}</span>` : ''}</button>`;
}
function taskBoardFocused(items, state) {
  const focus = state.boardFocus;
  return focus ? items.filter(it => focus.keys.has(it.key)) : items;
}
function taskBoardFocus(state, key) {
  const t = tasksById(state).get(key.slice(1)); if (!t) return;
  state.boardFocus = {key, keys: new Set([key, ...taskOpenBlockers(t).map(r => 't' + r.id)])};
  tasksRender(state);
}

// ---- the faces over the board: whoever has the most cards here (you first), each a toggle on the Owner filter
const taskOwnerHit = actor => v => resolveMe(v) === actor || (!v.includes(':') && actor === 'bot:' + v);
function taskBoardHead(items, state) {
  const me = myActor(), chosen = state.filters?.owner_id || [];
  // Counted without the Owner filter, so a face stays to be turned off again.
  const all = chosen.length ? taskItems({...state, filters: {...state.filters, owner: []}, memo: null}, 'board') : items;
  const counts = new Map();
  for (const it of all) if (it.actor) counts.set(it.actor, (counts.get(it.actor) || 0) + 1);
  const on = actor => chosen.some(taskOwnerHit(actor));
  const ranked = [...counts.entries()].sort((a, b) => (b[0] === me) - (a[0] === me) || b[1] - a[1]
    || actorLabel(a[0]).localeCompare(actorLabel(b[0])));
  const shown = ranked.filter(([actor], i) => i < 10 || on(actor));
  const faces = shown.length > 1 || chosen.length ? shown.map(([actor, n]) => {
    const name = actor === me ? 'You' : actorLabel(actor);
    return `<button type="button" class="bface" data-board-owner="${esc(actor)}" aria-pressed="${on(actor)}" aria-label="${esc(`${name}, ${n} task${n === 1 ? '' : 's'}`)}" title="${esc(`${name} · ${n}`)}">${actorFace(actor, 22)}</button>`;
  }).join('') : '';
  const focus = state.boardFocus ? `<button type="button" class="tl-chip tl-chip-main bfocus" data-board-unfocus aria-label="Clear blocked focus" title="Show every card (X)">${TL_ICON.x}<span>Blocked focus</span></button>` : '';
  return `<div class="board-head">${faces ? `<span class="bfaces" role="group" aria-label="Owners">${faces}</span>` : ''}${focus}</div>`;
}
function taskBoardOwnerToggle(state, actor) {
  const chosen = state.filters.owner_id || [], hit = taskOwnerHit(actor);
  state.filters.owner_id = chosen.some(hit) ? chosen.filter(v => !hit(v)) : [...chosen, actor === myActor() ? 'me' : actor];
  tasksFiltersChanged(state);
}
// Q shows only your cards (again, everyone's); X clears the faces and the blocked focus. Never while typing.
function taskBoardKeys(state, ev) {
  if (TASKS_ST !== state || state.view !== 'board' || state.boardDrag) return;
  if (ev.defaultPrevented || ev.metaKey || ev.ctrlKey || ev.altKey || ev.isComposing) return;
  if (document.querySelector('dialog:modal, :popover-open')) return;
  const el = document.activeElement;
  if (el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName))) return;
  const key = ev.key.toLowerCase(), chosen = state.filters.owner_id || [];
  if (key === 'q') {
    ev.preventDefault();
    const mine = chosen.length === 1 && resolveMe(chosen[0]) === myActor();
    state.filters.owner_id = mine ? [] : ['me'];
    tasksFiltersChanged(state);
  } else if (key === 'x' && (chosen.length || state.boardFocus)) {
    ev.preventDefault();          // with nothing to clear, x selects the card under the cursor as before
    state.filters.owner_id = []; state.boardFocus = null;
    tasksFiltersChanged(state);
  }
}
function taskBoardClick(state, ev) {
  if (TASKS_ST !== state || state.view !== 'board') return;
  const hit = ev.target.closest('[data-board-owner],[data-board-blocked],[data-board-unfocus],[data-board-unfold],[data-board-fold],[data-card-owner],[data-board-select]');
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
function taskBoardSelectColumn(state, button) {
  const column = button.closest('.bcol'); if (!column) return;
  const keys = [...column.querySelectorAll('.bcard[data-task-key]')].map(card => card.dataset.taskKey);
  const name = column.querySelector('h2')?.textContent || '';
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
const taskBoardHolding = state => !!state.boardDrag?.on;
function taskBoardBind(state, body) {
  const signal = state.layoutAbort.signal;
  body.addEventListener('click', ev => taskBoardClick(state, ev), {capture: true, signal});
  body.addEventListener('pointerdown', ev => taskBoardPress(state, ev), {signal});
  document.addEventListener('keydown', ev => taskBoardKeys(state, ev), {signal});
}
function taskBoardPress(state, ev) {
  if (TASKS_ST !== state || state.view !== 'board' || state.boardDrag) return;
  if (ev.button !== 0 || ev.pointerType === 'touch' || ev.shiftKey || ev.metaKey || ev.ctrlKey || ev.altKey || matchMedia(PHONE).matches) return;
  const card = ev.target.closest('.board[data-board-drag] .bcard');
  if (!card || ev.target.closest('a, input, button:not(.bcard-open)')) return;
  const id = card.dataset.taskKey.slice(1);
  if (state.boardMoves?.get(id)?.pending) return;          // one move at a time per card
  const d = state.boardDrag = {card, key: card.dataset.taskKey, x0: ev.clientX, y0: ev.clientY, x: ev.clientX, y: ev.clientY, on: false};
  const move = e => taskBoardMove(state, e), up = e => taskBoardUp(state, e), cancel = () => taskBoardEnd(state, false);
  const keys = e => { if (e.key === 'Escape' && state.boardDrag === d && d.on) { e.preventDefault(); e.stopPropagation(); taskBoardEnd(state, false); } };
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
function taskBoardMove(state, e) {
  const d = state.boardDrag; if (!d) return;
  if (!e.buttons) { taskBoardEnd(state, false); return; }            // let go outside the window
  d.x = e.clientX; d.y = e.clientY;
  if (!d.on) {
    if (Math.abs(d.x - d.x0) < BOARD_SLOP && Math.abs(d.y - d.y0) < BOARD_SLOP) return;
    taskBoardLift(d);
  }
  e.preventDefault();
  d.ghost.style.transform = `translate(${d.x - d.dx}px,${d.y - d.dy}px)`;
  taskBoardOver(state);
  taskBoardAutoScroll(state);
}
function taskBoardLift(d) {
  d.on = true;
  const r = d.card.getBoundingClientRect();
  d.dx = d.x0 - r.left; d.dy = d.y0 - r.top;
  d.from = d.card.closest('.bcol');
  d.fromIndex = [...d.card.parentNode.querySelectorAll('.bcard')].indexOf(d.card);
  const ghost = d.card.cloneNode(true);
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
function taskBoardOver(state) {
  const d = state.boardDrag; if (!d?.on) return;
  const under = document.elementFromPoint(d.x, d.y);
  const col = under?.closest?.('#task-body .board .bcol[data-drop-step]') || null;
  document.querySelectorAll('#task-body .bcol.over').forEach(el => { if (el !== col) el.classList.remove('over'); });
  d.target = col; d.prev = d.next = null; d.index = -1;
  let line = document.querySelector('#task-body .bdrop');
  const body = col && !col.classList.contains('bfold') ? col.querySelector('.bcol-body') : null;
  col?.classList.add('over');
  if (!body) { line?.remove(); return; }
  const cards = [...body.querySelectorAll('.bcard')].filter(c => c !== d.card);
  let at = cards.findIndex(c => { const r = c.getBoundingClientRect(); return d.y < r.top + r.height / 2; });
  if (at < 0) at = cards.length;
  d.index = at; d.prev = cards[at - 1]?.dataset.taskKey || null; d.next = cards[at]?.dataset.taskKey || null;
  if (!line) { line = document.createElement('div'); line.className = 'bdrop'; line.setAttribute('aria-hidden', 'true'); }
  const ref = cards[at] || null;
  if (ref ? line.nextElementSibling !== ref : body.lastElementChild !== line) body.insertBefore(line, ref);
}
// Held near a side of the board it scrolls that way, and near the top or the bottom of a column, that column; faster
// the deeper in, for as long as the card is held there.
function taskBoardAutoScroll(state) {
  const d = state.boardDrag;
  if (!d?.on || d.tick) return;
  const by = (depth, edge) => Math.ceil(Math.min(depth, edge * 1.5) / 4);
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
function taskBoardUp(state, e) {
  const d = state.boardDrag; if (!d) return;
  if (d.on) { d.x = e.clientX; d.y = e.clientY; taskBoardOver(state); }
  taskBoardEnd(state, d.on);
}
function taskBoardEnd(state, drop) {
  const d = state.boardDrag; if (!d) return;
  state.boardDrag = null;
  d.off();
  if (d.tick) cancelAnimationFrame(d.tick);
  if (!d.on) return;                     // a press that never moved is a click: the peek opens as always
  d.ghost?.remove();
  d.card.classList.remove('dragging');
  document.body.classList.remove('board-holding');
  document.querySelectorAll('#task-body .bdrop').forEach(n => n.remove());
  document.querySelectorAll('#task-body .bcol.over').forEach(n => n.classList.remove('over'));
  // The click the carry ends in opens nothing; after Esc, the button is still down, so that is the next release.
  const swallow = ev => { ev.stopPropagation(); ev.preventDefault(); };
  const arm = () => {
    window.addEventListener('click', swallow, {capture: true, once: true});
    setTimeout(() => window.removeEventListener('click', swallow, true), 0);
  };
  if (drop) arm(); else document.addEventListener('pointerup', arm, {capture: true, once: true});
  if (!(drop && d.target && taskBoardDrop(state, d))) tasksRender(state);   // a saved drop draws itself
}

// ---- the move: the step and the place (step_rank) at once, drawn before the server answers. A refusal puts the card
// back and says why; a load that set out before the save cannot put it back meanwhile (taskBoardLive).
function taskBoardRank(prev, next) {
  if (prev != null && next != null) return (prev + next) / 2;
  if (prev != null) return prev + 1;
  if (next != null) return next - 1;
  return null;
}
function taskBoardDrop(state, d) {
  const type = pipelineSelectedType(state), stepId = d.target.dataset.dropStep;
  const step = type?.steps.find(s => s.id === stepId);
  const byId = tasksById(state), id = d.key.slice(1), t = byId.get(id);
  if (!step || !t) return false;
  if (d.target === d.from && d.index === d.fromIndex) return false;           // put back where it was
  const rankOf = key => { const x = key && byId.get(key.slice(1)); return x ? taskBoardLive(state, {task: x}).task.step_rank ?? null : null; };
  let rank;
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
    void taskBlockedReason(t, d.card.querySelector('[data-open-task]')).then(reason => {
      if (reason !== null && TASKS_ST === state) taskBoardSave(state, t, {...body, note: reason}, fields, 'Not moved');
    });
  } else {
    taskBoardSave(state, t, body, fields, 'Not moved');
    if (body.status === 'done') taskDoneMotion(d.key);   // the card, now in its done column, draws its check
  }
  return true;
}
// A card's change (a move, a new owner) drawn at once, before the server answers; a refusal puts it back and says why.
function taskBoardSave(state, t, body, fields, refused) {
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
      const move = state.boardMoves.get(id); if (move) move.pending = false;
      if (data?.task) state.tasks = mergeTaskRows(state.tasks, [data.task]);
      tasksRender(state);
    } catch (e) {
      if (TASKS_ST !== state) return;
      state.boardMoves.delete(id);
      state.tasks = state.tasks.map(x => String(x.id) === id && Number(x.version) <= Number(t.version) ? t : x);
      if (e.status === 409) toast('Changed elsewhere. Showing the latest.', true);
      else taskRefusedSet(state, t, `${refused}: ${e.message || 'refused'}`);
      tasksRender(state);
    }
    void tasksLoad(state);
  })();
}
// The face on a card is its owner: pressing it picks another person or bot, saved from the card (the peek's Owner menu).
function taskBoardOwnerMenu(state, button) {
  const t = tasksById(state).get(button.dataset.cardOwner.slice(1));
  if (!t || !taskPropRights(t).edit) return;
  const opts = new DOMParser().parseFromString(`<select>${taskOwnerOptions('')}</select>`, 'text/html').querySelectorAll('option[value]:not([value=""])');
  propMenu($('#task-card-pops'), button, {label: 'Owner', find: 'Find a person or bot', items: [...opts].map(o => {
    const actor = o.value.includes(':') ? o.value : 'bot:' + o.value;
    return {value: o.value, text: o.textContent, html: `<span class="tl-opt-face">${actorFace(actor, 16)}</span><span>${esc(o.textContent)}</span>`, checked: t.owner_id === actor, actor};
  }), onPick: it => {
    const cur = tasksById(state).get(String(t.id));
    if (!cur || it.actor === cur.owner_id) return;
    taskBoardSave(state, cur, {version: cur.version, owner: it.actor}, {owner: it.actor}, 'Not changed');
  }});
}
// A card as the board draws it: with its move while the server has not said otherwise (a copy no newer than the one
// it was moved from), else as loaded.
function taskBoardLive(state, item) {
  const id = String(item.task?.id ?? ''), move = state.boardMoves?.get(id);
  if (!move) return item;
  if (Number(item.task.version) > move.version) {
    if (!move.pending) state.boardMoves.delete(id);
    return item;
  }
  return {...item, task: {...item.task, ...move.fields}, ...(move.fields.owner_id ? {actor: move.fields.owner_id} : {})};
}
