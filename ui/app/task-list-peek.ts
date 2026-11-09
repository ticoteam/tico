/* ui/app/task-list-peek.ts — the Tasks list's side peek, its cursor and keys, and clicks on the list.
   Split out of task-list.ts. Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ---- the side peek: the task beside the list (the modal's content, narrower); Esc closes, j/k moves it.
// On a phone it is a full-screen sheet: modal (the list behind it is inert), and Back closes it.
function taskPeek() {
  let d = $('#task-peek');
  if (d) return d;
  d = document.createElement('dialog');
  d.id = 'task-peek'; d.className = 'tmodal task-peek'; d.dataset.peek = '1';
  d.setAttribute('aria-label', 'Task');
  taskDialogWire(d);
  d.addEventListener('close', () => {           // the event lands a moment later: the peek may be reopening
    if (d.open || d.taskOpening?.pending) return;
    const lost = !document.activeElement || document.activeElement === document.body || d.contains(document.activeElement);
    taskPeekClosed();
    if (lost && TASKS_ST) tasksFocusCursor(TASKS_ST);
  });
  d.addEventListener('cancel', (ev: Event) => { ev.preventDefault(); if (TASKS_ST) taskPeekClose(TASKS_ST); else d.close(); });
  $('.tasks-page')?.appendChild(d);
  return d;
}
function taskPeekClosed() {
  const state = TASKS_ST;
  $('.tasks-page')?.classList.remove('peek-open');
  document.body.classList.remove('task-peek-open');
  if (state) { state.peek = ''; clearTimeout(state.peekTimer); }
  for (const el of document.querySelectorAll('#task-body .peeked')) { el.classList.remove('peeked'); el.querySelector('[aria-current]')?.removeAttribute('aria-current'); }
  // The sheet's own history entry (a phone's Back) goes with it, once: back() lands later than the dialog's close
  // event, so a second close before it lands must not step back again (off the Tasks page).
  if (history.state?.taskPeek && !PEEK_BACK) { PEEK_BACK = true; history.back(); }
}
// How a peek opens: from the list or a link inside it, drawn at once while j/k moves (quick), with the focus in it.
interface TaskPeekOpts { via?: string; quick?: boolean; focus?: boolean; }
function taskPeekOpen(state: TaskState, key: string, opts: TaskPeekOpts = {}) {
  const task = tasksById(state).get(String(key).slice(1));
  if (!task) return;
  const d = taskPeek();
  state.peek = key;
  state.peekVia = opts.via || 'list';      // 'link': a parent, subtask or related task opened from inside the peek
  $('.tasks-page')?.classList.add('peek-open');
  document.body.classList.add('task-peek-open');
  for (const el of document.querySelectorAll('#task-body .peeked')) { el.classList.remove('peeked'); el.querySelector('[aria-current]')?.removeAttribute('aria-current'); }
  const row = document.querySelector(`#task-body [data-task-key="${CSS.escape(key)}"]`);
  row?.classList.add('peeked'); row?.querySelector('[data-open-task]')?.setAttribute('aria-current', 'true');
  if (row && state.cursor !== key) { state.cursor = key; tasksCursorPaint(state); }    // the list's place follows the peek
  if (matchMedia(PHONE).matches && !d.open && !history.state?.taskPeek) history.pushState({...(history.state || {}), taskPeek: 1}, '');
  clearTimeout(state.peekTimer);
  // j/k through a list: draw what the list knows at once, fetch the detail once the keys settle
  if (opts.quick && d.open) {
    d.dataset.task = task.id;
    d.innerHTML = hubModalHTML(task, taskItem(task), {peek: true});
    void taskRequirementsLoad(d);
    taskModalBind(d, task);
    state.peekTimer = setTimeout(() => { if (state.peek === key && d.open) void taskModalShow(task, d); }, 160);
  } else void taskModalShow(task, d);
  if (opts.focus || matchMedia(PHONE).matches) setTimeout(() => d.querySelector('.tmodal-title')?.focus({preventScroll: true}), 0);
}
function taskPeekClose(state: TaskState) {
  const d = $('#task-peek');
  const lost = d?.contains(document.activeElement);
  if (d?.open) d.close();
  taskPeekClosed();
  if (lost || !document.activeElement || document.activeElement === document.body) tasksFocusCursor(state);
}
// After a reload: a peek showing a task that changed elsewhere redraws, unless you are typing or picking in it.
function tasksPeekSync(state: TaskState) {
  const d = $('#task-peek');
  if (!d?.open || !state.peek) return;
  const fresh = tasksById(state).get(state.peek.slice(1));
  if (!fresh || Number(fresh.version) <= Number(d.dataset.version) || d.saving) return;
  const busy = d.contains(document.activeElement) && document.activeElement?.matches('input, textarea, select, [contenteditable]');
  if (busy || $('.prop-pop:popover-open', d)) return;
  void taskModalShow(fresh, d);
}
// ---- the cursor and the keys: j/k or ↑/↓ move, Enter opens, ←/→ fold, c creates, / searches, x selects, Esc closes or clears.
// One row is in the Tab order at a time (the cursor's), so the list is one Tab stop, not three per row.
function tasksCursorPaint(state: TaskState) {
  for (const el of document.querySelectorAll('#task-body .is-cursor')) el.classList.remove('is-cursor');
  const el = state.cursor ? document.querySelector(`#task-body [data-task-key="${CSS.escape(state.cursor)}"]`) : null;
  el?.classList.add('is-cursor');
  const tabKey = el ? state.cursor : tasksVisibleKeys()[0] || '';
  state.tabKey = tabKey;
  for (const b of document.querySelectorAll<HTMLElement>('#task-body [data-open-task]')) b.tabIndex = b.closest<HTMLElement>('[data-task-key]')?.dataset.taskKey === tabKey ? 0 : -1;
  return el;
}
function tasksFocusCursor(state: TaskState) {
  const el = tasksCursorPaint(state) || document.querySelector(`#task-body [data-task-key="${CSS.escape(state.tabKey || '')}"]`);
  el?.querySelector<HTMLElement>('[data-open-task]')?.focus({preventScroll: true});
}
function tasksMove(state: TaskState, step: number) {
  const keys = tasksVisibleKeys();
  if (!keys.length) return;
  const at = keys.indexOf(state.cursor);
  const next = keys[at < 0 ? (step > 0 ? 0 : keys.length - 1) : Math.max(0, Math.min(keys.length - 1, at + step))]!;     // keys is not empty
  state.cursor = next;
  const el = tasksCursorPaint(state);
  el?.querySelector<HTMLElement>('[data-open-task]')?.focus({preventScroll: true});
  el?.scrollIntoView({block: 'nearest'});
  if (state.peek && next !== state.peek) taskPeekOpen(state, next, {quick: true});
}
// A new task's owner and tag from the chips. It has no owner_id: c opens the new task with no owner set.
interface TaskCreatePrefill { owner: string; labels: unknown[]; owner_id?: undefined; }
function tasksCreatePrefill(state: TaskState): TaskCreatePrefill {
  const owners = (state.filters.owner || []) as string[];
  const owner = owners.length === 1 ? resolveMe(owners[0]!) : '';
  return {owner: actorSlug(owner) || owner, labels: (state.filters.tag || []).slice(0, 1)};
}
function tasksFold(state: TaskState, key: string, open: boolean) {
  const has = !!document.querySelector(`#task-body [data-expand="${CSS.escape(key)}"]`);
  if (!has) return false;
  if (open === !state.folded.has(key)) return false;
  open ? state.folded.delete(key) : state.folded.add(key);
  tasksRemember(state); tasksRender(state);
  return true;
}
function tasksKeys(state: TaskState, ev: KeyboardEvent) {
  if (TASKS_ST !== state) return;
  if (ev.defaultPrevented || ev.metaKey || ev.ctrlKey || ev.altKey || ev.isComposing) return;
  if (document.querySelector('dialog:modal')) return;                    // the full task, a new task, a result prompt, a phone's sheet
  if (document.querySelector(':popover-open')) return;                   // a menu has the keys (Esc closes it)
  const el = document.activeElement as HTMLInputElement | null;     // an input's type is read only when it is one
  const typing = el && (el.isContentEditable || ['TEXTAREA', 'SELECT'].includes(el.tagName)
    || (el.tagName === 'INPUT' && !['checkbox', 'radio', 'button', 'submit', 'reset'].includes(el.type)));
  if (typing) return;
  const inPeek = !!el?.closest?.('#task-peek');
  const listy = ['list', 'foryou', 'done', 'closed', 'board'].includes(state.view);
  const key = ev.key;
  const inTabs = !!el?.closest?.('[role=tablist]');
  if ((key === 'j' || (key === 'ArrowDown' && !inPeek && !inTabs)) && listy) { ev.preventDefault(); tasksMove(state, 1); return; }
  if ((key === 'k' || (key === 'ArrowUp' && !inPeek && !inTabs)) && listy) { ev.preventDefault(); tasksMove(state, -1); return; }
  if ((key === 'ArrowRight' || key === 'ArrowLeft') && !inPeek && !inTabs && state.cursor && el?.closest?.('.tl-row')) {
    const open = key === 'ArrowRight';
    if (tasksFold(state, state.cursor, open)) { ev.preventDefault(); tasksFocusCursor(state); return; }
    if (!open) {   // on a subtask, ← goes to its parent
      const pid = taskParentId(tasksById(state).get(state.cursor.slice(1)));
      if (pid && document.querySelector(`#task-body [data-task-key="t${CSS.escape(String(pid))}"]`)) { ev.preventDefault(); state.cursor = 't' + pid; tasksFocusCursor(state); }
    }
    return;
  }
  if (key === '/' && !tasksNeedsQueue(state)) { ev.preventDefault(); $('#task-q')?.focus(); $('#task-q')?.select(); return; }
  if (key === 'c' && !ev.shiftKey) { ev.preventDefault(); const p = tasksCreatePrefill(state); openTaskCreate(p.owner_id, {labels: p.labels}); return; }
  if (key === 'x' && listy && state.cursor?.startsWith('t') && tasksCanSelect()) { ev.preventDefault(); tasksSelect(state, state.cursor); return; }
  if (key === 'Enter' && listy && state.cursor && (!el || el === document.body || el.closest?.('.tl-row,.bcard') && !el.matches('button,a,input'))) {
    ev.preventDefault();
    const card = document.querySelector<HTMLElement>(`#task-body [data-task-key="${CSS.escape(state.cursor)}"]`)?.dataset.needCard;
    if (card) decideOpen(card); else taskPeekOpen(state, state.cursor);
    return;
  }
  if (key === 'Escape') {
    if ($('#task-peek')?.open) { ev.preventDefault(); taskPeekClose(state); return; }
    if (state.selected.size || state.bulkMsg || state.selectMode) {
      ev.preventDefault(); state.selectMode = false; tasksSelectionClear(state); tasksTools(state);
    }
  }
}
// Clicks on the list: a row opens the peek (shift selects a range, ⌘/Ctrl one more), its box selects, ▸ folds.
// In a phone's Select mode a tap selects.
function tasksBodyClick(state: TaskState, ev: MouseEvent) {
  const box = eventElement(ev)?.closest<HTMLInputElement>('[data-select]');
  if (box) {
    ev.stopPropagation();
    const key = box.dataset.select!;          // matched by [data-select]
    if (ev.shiftKey && state.anchor && state.anchor !== key) { box.checked = true; tasksSelect(state, key, 'range'); }
    else { if (box.checked !== state.selected.has(key)) tasksSelect(state, key); }
    state.cursor = key; tasksCursorPaint(state);
    return true;
  }
  const exp = eventElement(ev)?.closest<HTMLElement>('[data-expand]');
  if (exp) {
    const key = exp.dataset.expand!;
    state.folded.has(key) ? state.folded.delete(key) : state.folded.add(key);
    tasksRemember(state); tasksRender(state);
    $(`#task-body [data-expand="${CSS.escape(key)}"]`)?.focus();
    return true;
  }
  const status = eventElement(ev)?.closest<HTMLElement>('[data-row-status]');
  if (status) { ev.stopPropagation(); tasksRowStatusMenu(state, status); return true; }
  const toggle = eventElement(ev)?.closest<HTMLElement>('[data-group-toggle]');
  if (toggle) {
    const k = toggle.dataset.groupToggle!;
    if (k === 'blocked') tasksShowBlockedSet(!tasksShowBlocked());
    else { state.collapsed.has(k) ? state.collapsed.delete(k) : state.collapsed.add(k); tasksRemember(state); }
    tasksRender(state);
    $(`#task-body [data-group-toggle="${CSS.escape(k)}"]`)?.focus();
    return true;
  }
  const addIn = eventElement(ev)?.closest<HTMLElement>('[data-group-add]');
  if (addIn) {
    const g = tasksGroupsFor(taskItems(state), tasksGroupBy(state), state).find(x => x.key === addIn.dataset.groupAdd);
    const c = g?.create || {};
    if (c.parent) openTaskCreate(c.parent.owner_id ? (actorSlug(c.parent.owner_id) || c.parent.owner_id) : '', {parent: c.parent});
    else openTaskCreate(c.owner_id ? (actorSlug(c.owner_id) || c.owner_id) : '', {labels: c.labels || []});
    return true;
  }
  if (eventElement(ev)?.closest('[data-clear-filters]')) {
    state.filters = tasksEmptyFilters();
    state.q = ''; if ($('#task-q')) $('#task-q').value = '';
    tasksFiltersChanged(state);
    $('#task-filter')?.focus();
    return true;
  }
  const row = eventElement(ev)?.closest<HTMLElement>('.tl-row,.bcard');
  if (!row || eventElement(ev)?.closest('a[href]')) return false;
  const key = row.dataset.taskKey!;   // every row and card in the list carries its key (a board drag's ghost is outside it)
  // A row answered on a card opens its card (decide.js); a row outside any task has nothing to select.
  if (row.dataset.needCard && (key?.startsWith('n') || !(tasksCanSelect() && (ev.shiftKey || ev.metaKey || ev.ctrlKey || state.selectMode)))) {
    ev.preventDefault();
    state.cursor = key; tasksCursorPaint(state);
    if (!decideOpen(row.dataset.needCard) && key?.startsWith('t')) taskPeekOpen(state, key);
    return true;
  }
  if (tasksCanSelect() && (ev.shiftKey || ev.metaKey || ev.ctrlKey || state.selectMode)) {
    ev.preventDefault();
    tasksSelect(state, key, ev.shiftKey ? 'range' : 'toggle');
    state.cursor = key; tasksCursorPaint(state);
    return true;
  }
  ev.preventDefault();
  state.cursor = key; tasksCursorPaint(state);
  // Enter on the row already open in the peek takes the keyboard into it; otherwise the list keeps the focus.
  if (state.peek === key && $('#task-peek')?.open && ev.detail === 0) { $('#task-peek .tmodal-title')?.focus(); return true; }
  row.querySelector<HTMLElement>('[data-open-task]')?.focus({preventScroll: true});
  taskPeekOpen(state, key);
  return true;
}
// The peek finished its task (Done, Close): the list moves on to the next row and the peek follows it.
function tasksAfterFinish(state: TaskState, oldKey: string, at: number) {
  if (TASKS_ST !== state) return false;
  if (document.querySelector(`#task-body [data-task-key="${CSS.escape(oldKey)}"]`)) return false;
  const keys = tasksVisibleKeys();
  if (!keys.length) { taskPeekClose(state); $('#task-q')?.focus(); return true; }
  state.cursor = keys[Math.max(0, Math.min(keys.length - 1, at))]!;     // keys is not empty
  tasksFocusCursor(state);
  taskPeekOpen(state, state.cursor);
  return true;
}
