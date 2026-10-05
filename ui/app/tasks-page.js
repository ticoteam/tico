/* ui/app/tasks-page.js — pageTasks: loading, tools, needs you, Done, render
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// openId opens one task in full as soon as the list is loaded: the #/task/<id> address a bot or
// the setup progress screen links to, which is the Tasks page with that item already open.
const TASK_PREF = 'tasks.view';
function pageTasks(forced, openId = '') {
  TASKS_ST?.layoutAbort?.abort();
  PROP_TASKS = null;                       // the pickers' copy of the open tasks starts fresh with the page
  const state = TASKS_ST = {view: 'foryou', tasks: [], routines: null, labels: [], kind: 'all', armed: 'all', open: openId, q: '',
    loading: true, doneLoaded: false, doneLoading: false, doneNext: null, loadSeq: 0,
    filters: tasksEmptyFilters(), group: {...TASK_GROUP_DEFAULT}, collapsed: new Set(), folded: new Set(),
    selected: new Set(), anchor: '', cursor: '', tabKey: '', peek: '', bulkFail: new Map(), bulkMsg: '', memo: new Map()};
  const query = tasksFiltersFromURL(state);
  const hadQuery = [...query.keys()].length > 0;
  if (state.urlView && TASK_VIEWS.some(([k]) => k === state.urlView)) forced = state.urlView;
  try {
    state.view = forced || localStorage.getItem('hub.tasks.view2') || 'foryou';
    state.kind = localStorage.getItem('hub.recurring.kind') || 'all';
    state.armed = localStorage.getItem('hub.recurring.armed') || 'all';
  } catch { state.view = forced || 'foryou'; }
  taskPipelineState(state);
  if (hadQuery) state.type = state.urlType || 'general';
  tasksPrefsFromLocal(state);
  // Filters an older page saved (Mine, Asked by me, an owner, a tag) come over once as chips, unless the address has its own.
  state.migrate = !hadQuery;
  if (state.migrate && tasksMigrateOldFilters(state, null)) state.migrated = true;
  tasksNormalise(state, forced);
  const main = $('#main');
  main.classList.add('tasks-layout');
  main.innerHTML = `<div class="tasks-page"><div class="tasks-pane" id="tasks-pane">
      <header class="tl-head">
        <h1>Tasks</h1>
        <div class="tl-tabs" id="task-view" role="tablist" aria-label="Task views"></div>
        <span class="spacer"></span>
        <div class="tl-type-search"><div class="tl-types" id="task-type" role="group" aria-label="Task type"></div>
        <div class="tl-search">${TL_ICON.search}<input type="search" id="task-q" spellcheck="false" aria-label="Search tasks" placeholder="Search" autocomplete="off"><kbd aria-hidden="true">/</kbd></div></div>
        <button class="primary tl-new" type="button" id="task-new" aria-label="New task" title="New task (C)">${TL_ICON.plus}<span>New task</span></button>
      </header>
      <div class="tl-bar" id="task-bar">
        <div class="tl-filters" role="group" aria-label="Filters"><span class="tl-chipset" id="task-chips"></span>
          <button type="button" class="tl-addf" id="task-filter" aria-haspopup="true">${TL_ICON.filter}<span>Filter</span></button>
          <button type="button" class="linkish tl-clear" id="task-filter-clear" hidden>Clear</button></div>
        <span class="spacer"></span>
        <button type="button" class="tl-addf tl-pin" id="task-pin" aria-pressed="false">Pin</button>
        <button type="button" class="tl-addf tl-selmode" id="task-select-mode" aria-pressed="false" hidden>Select</button>
        <span class="tl-groupby" id="task-group-wrap"></span>
      </div>
      <span id="tl-sel-word" hidden>Selected</span>
      <div id="task-body"></div>
      <div class="tl-bulk" id="task-bulk" role="toolbar" aria-label="Selected tasks" hidden></div>
    </div></div>
    <div id="task-filter-pop" class="tl-pop" popover></div>`;
  $('#task-type').onclick = ev => {
    const button = ev.target.closest('[data-task-type]');
    if (button) taskPipelineSelect(state, button.dataset.taskType);
    else if (ev.target.closest('#task-type-more')) taskPipelineMenu(state, $('#task-type-more'));
  };
  $('#task-pin').onclick = () => tasksPinToggle(state);
  $('#task-new').onclick = () => { const p = tasksCreatePrefill(state); openTaskCreate(p.owner, {labels: p.labels}); };
  const search = $('#task-q');
  search.oninput = () => {
    state.q = search.value;
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => {
      tasksRender(state);
      if (state.view === 'done' && state.q && state.doneNext != null) void tasksLoadAllDone(state);
    }, 150);
  };
  search.onkeydown = ev => {
    if (ev.key === 'ArrowDown' && !search.value.includes('\n')) { ev.preventDefault(); search.blur(); tasksMove(state, 1); return; }
    if (ev.key !== 'Escape') return;
    if (search.value) ev.stopPropagation();
    else search.blur();
    search.value = state.q = '';
    clearTimeout(state.searchTimer);
    tasksRender(state);
  };
  const tabs = $('#task-view');
  tabs.onclick = ev => {
    const b = ev.target.closest('[data-view]'); if (!b || b.dataset.view === state.view) return;
    tasksSetView(state, b.dataset.view);
  };
  tabs.onkeydown = ev => {
    const list = [...tabs.querySelectorAll('[role=tab]')], at = list.indexOf(document.activeElement);
    if (at < 0) return;
    const to = {ArrowRight: at + 1, ArrowLeft: at - 1, Home: 0, End: list.length - 1}[ev.key];
    if (to == null) return;
    ev.preventDefault();
    const next = list[(to + list.length) % list.length];
    tasksSetView(state, next.dataset.view);
    $(`#task-view [data-view="${next.dataset.view}"]`)?.focus();
  };
  $('#task-filter').onclick = ev => tasksFieldMenu(state, ev.currentTarget);
  $('#task-chips').onclick = ev => {
    const drop = ev.target.closest('[data-chip-drop]');
    if (drop) {
      const field = drop.dataset.chipDrop;
      state.filters[field] = [];
      tasksFiltersChanged(state);
      $('#task-filter')?.focus();
      return;
    }
    const edit = ev.target.closest('[data-chip-edit]');
    if (edit) tasksFilterMenu(state, edit.dataset.chipEdit, edit);
  };
  $('#task-filter-clear').onclick = () => {
    state.filters = tasksEmptyFilters();
    tasksFiltersChanged(state);
    $('#task-filter')?.focus();
  };
  $('#task-group-wrap').onclick = ev => { const b = ev.target.closest('#task-group'); if (b) tasksGroupMenu(state, b); };
  $('#task-select-mode').onclick = () => {
    state.selectMode = !state.selectMode;
    if (!state.selectMode) tasksSelectionClear(state);
    tasksTools(state); tasksSelectionPaint(state);
  };
  $('#task-bulk').onclick = ev => {
    const b = ev.target.closest('[data-bulk]'); if (!b) return;
    tasksBulkMenu(state, b.dataset.bulk, b);
  };
  const pop = $('#task-filter-pop');
  pop.addEventListener('keydown', tasksMenuKeys);
  pop.addEventListener('toggle', ev => {
    if (ev.newState !== 'closed') return;
    $('#task-type-more')?.setAttribute('aria-expanded', 'false');
    const anchor = state.menuAnchor; state.menuAnchor = null;
    if (anchor?.isConnected && (pop.contains(document.activeElement) || document.activeElement === document.body)) anchor.focus();
  });
  const body = $('#task-body');
  body.addEventListener('focusin', ev => {
    const row = ev.target.closest('[data-task-key]');
    if (row && row.dataset.taskKey !== state.cursor) { state.cursor = row.dataset.taskKey; tasksCursorPaint(state); }
  });
  bindRoutineActions(body);
  body.addEventListener('click', ev => {
    if (TASKS_ST !== state) return;
    if (ev.target.closest('[data-new-routine],[data-edit-routine],[data-toggle-routine],[data-delete-routine]')) return;
    const chip = ev.target.closest('[data-rfilter]');
    if (chip) {
      state[chip.dataset.rfilter] = chip.dataset.val;
      try { localStorage.setItem('hub.recurring.' + chip.dataset.rfilter, state[chip.dataset.rfilter]); } catch {}
      tasksRender(state);
      return;
    }
    const exp = ev.target.closest('[data-expand-routine]');
    if (exp && !ev.target.closest('a[href^="#/task/"]')) { ev.preventDefault(); toggleRoutineOccurrences(exp); return; }
    if (ev.target.closest('#board-more')) { void tasksLoadDone(state); return; }
    if (state.view === 'recurring') {
      const b = ev.target.closest('[data-open-task]'); if (!b) return;
      ev.preventDefault(); taskModalOpen(b.dataset.openTask);
      return;
    }
    tasksBodyClick(state, ev);
  });
  state.layoutAbort = new AbortController();
  document.addEventListener('keydown', ev => tasksKeys(state, ev), {signal: state.layoutAbort.signal});
  tasksTools(state); tasksRender(state);
  if (!openId && (hadQuery || state.type !== 'general' || state.migrated)) tasksURLWrite(state);
  if (state.migrated) tasksRemember(state);
  // the remembered view lives with the person, not the browser; the local copy is the fallback
  void (async () => {
    // Start the task request immediately. A slow preference read must never hold the board blank.
    const loading = tasksLoad(state);
    const pref = await v2Get('/v2/preferences/' + TASK_PREF);
    if (TASKS_ST !== state) return;
    if (pref?.value && typeof pref.value === 'object') {
      if (!hadQuery && !state.typeChosen && pref.value.type != null) state.type = String(pref.value.type) || 'general';
      // Before `views: 2`, List and Board both showed Needs you, so an old choice means Needs you.
      if (!forced && pref.value.view) state.view = pref.value.views === 2 ? String(pref.value.view) : 'foryou';
      tasksPrefsApply(state, pref.value);
      tasksNormalise(state, forced);
      tasksTypeCheck(state);
      const migrated = state.migrate && tasksMigrateOldFilters(state, pref.value);
      if (!openId && (hadQuery || state.type !== 'general' || migrated)) tasksURLWrite(state);
      if (migrated) tasksRemember(state);       // the saved value is written again without the old keys
    }
    tasksTools(state);
    tasksRender(state);
    if ((state.view === 'done' || state.view === 'board') && !state.doneLoaded && !state.doneLoading) void tasksLoadDone(state, true);
    await loading;
  })();
}
function tasksSetView(state, view) {
  state.view = view;
  tasksMenuClose();
  state.selectMode = false;
  if (state.selected.size || state.bulkMsg) tasksSelectionClear(state);
  tasksRemember(state); tasksURLWrite(state); tasksTools(state); tasksRender(state);
  if ((state.view === 'done' || state.view === 'board') && !state.doneLoaded && !state.doneLoading) void tasksLoadDone(state, true);
  else if (state.view === 'done') void tasksDoneMerge(state);
}
function tasksNormalise(state, forced) {
  if (!TASK_VIEWS.some(([k]) => k === state.view)) state.view = forced || 'foryou';
  if (!['all', 'cron', 'event', 'inbox'].includes(state.kind)) state.kind = 'all';
  if (!['all', 'armed', 'idle'].includes(state.armed)) state.armed = 'all';
}
function tasksRemember(state) {
  taskPipelineRemember(state);
  try {
    localStorage.setItem('hub.tasks.view2', state.view);
    localStorage.setItem('hub.tasks.layout', JSON.stringify(tasksPrefsValue(state)));
  } catch {}
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(() => { void post('/v2/preferences/' + TASK_PREF,
    {value: {type: state.type, view: state.view, views: 2, ...tasksPrefsValue(state)}}).catch(() => {}); }, 400);
}
function taskOwnerOptions(selected = '') {
  const people = (S.people || []).filter(p => !p.hidden)
    .slice().sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)));
  const bots = (S.emps || []).filter(e => e.status !== 'archived' && (!isHiddenBot(e.name) || [e.name, 'bot:' + e.name].includes(selected)))
    .slice().sort((a, b) => byBotOrder(a, b) || String(a.display_name || a.name).localeCompare(String(b.display_name || b.name)));
  const pick = value => value === selected ? ' selected' : '';
  return `<option value="">Who is this for?</option>
    <optgroup label="Humans">${people.map(p =>
      `<option value="human:${esc(p.id)}"${pick('human:' + p.id) || pick(p.id)}>${esc(p.name || p.id)}</option>`).join('')}</optgroup>
    <optgroup label="Bots">${bots.map(e =>
      `<option value="${esc(e.name)}"${pick(e.name) || pick('bot:' + e.name)}>${esc(e.display_name || e.name)}</option>`).join('')}</optgroup>`;
}
function taskCreateModal() {
  let d = $('#task-create');
  if (d) return d;
  d = document.createElement('dialog');
  d.id = 'task-create'; d.className = 'tmodal';
  d.addEventListener('click', ev => { if (ev.target === d) d.close(); });
  document.body.appendChild(d);
  return d;
}
function openTaskCreate(owner = '', opts = {}) {
  const d = taskCreateModal();
  const parent = opts.parent || null;
  d.innerHTML = `<div class="tmodal-head"><h2 class="tmodal-title" style="margin:0">${parent ? 'New subtask' : 'New task'}</h2>
      <span class="spacer"></span>
      <button class="ghost tmodal-x" type="button" data-modal-close aria-label="Close">✕</button></div>
    <div class="tmodal-body">
      <form class="task" id="task-create-form">
        ${parent ? `<p class="muted">Part of <b>${esc(parent.title)}</b></p>` : ''}
        <div class="r1" style="grid-template-columns:1fr"><input type="text" name="title" required aria-label="Title" placeholder="Email Dana the renewal brief"></div>
        <label>For <select name="owner" required aria-label="Who this task is for">${taskOwnerOptions(owner)}</select></label>
        <label class="task-private"><span class="privacy-row"><input type="checkbox" name="private" ${parent?.private ? 'checked' : ''}> Private</span><small>Only the requester and assignee can see this task.</small></label>
        <div class="r3">
          <label>Tags <input type="text" name="labels" list="task-label-list-new" placeholder="bug, pricing-page" aria-label="Tag keys, comma separated" size="18" value="${esc((opts.labels || []).join(', '))}"><datalist id="task-label-list-new">${(TASKS_ST?.labels || []).map(l => `<option value="${esc(l)}">`).join('')}</datalist></label>
          <label><input type="checkbox" name="top"> Top of their queue</label>
        </div>
        <label>Serves <select name="goal" aria-label="The goal this task serves"><option value="">No goal</option></select></label>
        <label>Details (required)<textarea name="body" required aria-label="Details" placeholder="Details"></textarea></label>
        <input type="url" name="link" inputmode="url" autocomplete="off" spellcheck="false" placeholder="Link (pull request, doc)" aria-label="Link">
        <label class="attach">Attach files <input type="file" name="files" multiple aria-label="Task attachments"></label>
        <div class="r3"><button class="primary" type="submit">Create task</button><span class="muted" id="task-create-msg"></span></div>
        <p class="muted hint">You close it. For a human, start the title with a verb.</p>
      </form>
    </div>`;
  $('[data-modal-close]', d).onclick = () => d.close();
  const creation = $('#task-create-form', d);
  let privateTouched = false;
  creation.elements.private.addEventListener('change', () => { privateTouched = true; });
  const privateDefault = () => {
    const slug = creation.elements.owner.value.replace(/^bot:/, '');
    const bot = S.emps.find(e => e.name === slug);
    if (parent?.private || !privateTouched)
      creation.elements.private.checked = !!parent?.private || !!bot?.private_tasks_default;
    creation.elements.private.disabled = !!parent?.private;
  };
  creation.elements.owner.addEventListener('change', privateDefault); privateDefault();
  goalOptions().then(goals => {
    const sel = $('#task-create-form select[name=goal]'); if (!sel || !goals.length) return;
    sel.innerHTML = '<option value="">No goal</option>' + goals.map(g =>
      `<option value="${esc(g.id)}">${esc(g.title)} · ${esc(goalOwnerInfo(g.owner).name)}</option>`).join('');
  });
  void taskPipelineCreate($('#task-create-form'), TASKS_ST?.type);
  $('#task-create-form').onsubmit = async ev => {
    ev.preventDefault();
    const form = ev.target, btn = form.querySelector('[type=submit]'), msg = $('#task-create-msg');
    const title = form.title.value.trim(), owner = form.owner.value, body = form.body.value.trim();
    if (!title || !owner) { msg.textContent = 'Choose who this is for.'; return; }
    if (!body) { msg.textContent = 'Add details.'; form.body.focus(); return; }
    btn.disabled = true; msg.textContent = 'Creating…';
    const payload = {title, body, owner};
    payload.private = form.elements.private.checked;
    taskPipelineCreatePayload(form, payload);
    const labels = form.labels.value.split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
    if (labels.length) payload.labels = labels;
    if (form.top.checked) payload.top = true;
    if (form.link.value.trim()) payload.links = [form.link.value.trim()];
    if (parent) payload.parent_id = parent.id;
    try {
      const goal = form.goal?.value || '';
      if (goal) payload.goal_id = goal;
      await cloudCompose('/v2/tasks', payload, Array.from(form.elements.files?.files || []));
      d.close();
      toast('Task created');
      if (TASKS_ST) await tasksLoad(TASKS_ST);
      await v2Refresh();
    } catch (e) { msg.innerHTML = `<span class="err">${esc(e.message)}</span>`; btn.disabled = false; }
  };
  if (!d.open) d.showModal();
  formFocus(d);
}
function formFocus(root) {
  (root.querySelector('input[name=title], textarea, select') || {}).focus?.();
}
const mergeTaskRows = (...groups) => [...new Map(groups.flat().map(task => [task.id, task])).values()];
const activeTasksPath = (offset = 0) => `/v2/tasks?lane=company&status=${ACTIVE_TASK_STATUSES}&limit=100&offset=${offset}`;
async function tasksLoad(state, opts = {}) {
  const seq = ++state.loadSeq;
  // Done keeps its own paging. A reload after a change folds in its newest page; the poll does that too, but only while
  // Done (or a type's board, which shows finished steps) is on screen, and never more than that one page.
  const doneShown = state.view === 'done' || state.view === 'board';
  const reloadDone = opts.poll ? doneShown && state.doneLoaded : (state.doneLoaded || doneShown);
  state.loading = !(state.tasks || []).some(t => !['done', 'closed'].includes(String(t.status)));
  tasksRender(state);
  const [active, rec, lab] = await Promise.all([
    v2Get(activeTasksPath()),
    v2Get('/v2/routines'), v2Get('/v2/tasks/labels'), taskTypesLoad().then(t => { state.typesLoaded = true; return t; }).catch(() => TASK_TYPES)]);
  if (TASKS_ST !== state || state.loadSeq !== seq) return;
  const activeIds = new Set((active?.tasks || []).map(t => String(t.id)));
  const finished = (state.tasks || []).filter(t => ['done', 'closed'].includes(String(t.status)) && !activeIds.has(String(t.id)));   // reopened ones are active again
  state.tasks = mergeTaskRows(finished, active?.tasks || []);
  state.routines = rec?.routines || null;
  state.labels = lab?.labels || [];
  state.tags = lab?.tags || [];
  state.loading = false;
  tasksTypeCheck(state);
  tasksTools(state); tasksRender(state);
  tasksPeekSync(state);
  tasksFinishPending(state, seq);

  if (state.open) {
    const id = state.open; state.open = '';
    if (!state.tasks.some(t => t.id === id)) {
      const detail = await v2Get(`/v2/tasks/${encodeURIComponent(id)}`);
      if (TASKS_ST !== state || state.loadSeq !== seq) return;
      if (detail?.task) state.tasks = mergeTaskRows(state.tasks, [detail.task]);
    }
    taskModalOpen('t' + id);
  }

  const drain = async next => {
    while (next != null && TASKS_ST === state && state.loadSeq === seq) {
      const page = await v2Get(activeTasksPath(next));
      if (!page || TASKS_ST !== state || state.loadSeq !== seq) return;
      state.tasks = mergeTaskRows(state.tasks, page.tasks || []);
      next = page.next_offset;
      tasksTools(state); tasksRender(state);
    }
  };
  await drain(active?.next_offset);
  if (reloadDone && TASKS_ST === state && state.loadSeq === seq) await (state.doneLoaded ? tasksDoneMerge(state) : tasksLoadDone(state, true));
}
// A save took its task out of the view? Once a load that started after the save has drawn, the list and the peek
// move on (tasksAfterFinish). Any load may land it: an older one overtaken by the poll returns before it gets here.
function tasksFinishPending(state, seq) {
  const p = state.pendingFinish;
  if (!p || seq <= p.after) return;
  state.pendingFinish = null;
  if (state.peek === p.key) tasksAfterFinish(state, p.key, p.at);
}
// Old combined views and deleted types fall back to General, never a mixture of types.
function tasksTypeCheck(state) {
  if (state.type === 'general' || TASK_TYPES.some(type => type.id === state.type)) return;
  if (!state.type || !S.me?.cloud || state.typesLoaded) {
    state.type = 'general';
    taskPipelineRemember(state); tasksRemember(state);
    if (isTasksRoute(location.hash)) tasksURLWrite(state);
  }
}
async function tasksLoadDone(state, reset = false) {
  if (TASKS_ST !== state || state.doneLoading || (!reset && state.doneNext == null)) return false;
  state.doneLoading = true;
  tasksRender(state);
  const offset = reset ? 0 : state.doneNext;
  const page = await v2Get(`/v2/tasks?lane=company&status=done,closed&sort=finished&limit=${DONE_CAP}&offset=${offset}`);
  if (TASKS_ST !== state) return false;
  if (page) {
    const keep = (state.tasks || []).filter(t => !['done', 'closed'].includes(String(t.status)));
    const previous = reset ? [] : (state.tasks || []).filter(t => ['done', 'closed'].includes(String(t.status)));
    state.tasks = mergeTaskRows(keep, previous, page.tasks || []);
    state.doneNext = page.next_offset;
    state.doneLoaded = true;
  }
  state.doneLoading = false;
  tasksTools(state); tasksRender(state);
  if (page && state.view === 'done' && state.q && state.doneNext != null && !state.doneDrain) {
    void tasksLoadAllDone(state);
  }
  return !!page;
}
// The newest finished tasks folded into what Done already shows: nothing older is dropped, the paging is untouched.
async function tasksDoneMerge(state) {
  if (TASKS_ST !== state || state.doneLoading) return;
  const page = await v2Get(`/v2/tasks?lane=company&status=done,closed&sort=finished&limit=${DONE_CAP}&offset=0`);
  if (!page || TASKS_ST !== state) return;
  const fresh = new Map((page.tasks || []).map(t => [String(t.id), t]));
  state.tasks = mergeTaskRows((state.tasks || []).filter(t => !fresh.has(String(t.id)) || !['done', 'closed'].includes(String(t.status))), page.tasks || []);
  tasksTools(state); tasksRender(state);
}
async function tasksLoadAllDone(state) {
  if (state.doneDrain) return;
  state.doneDrain = true;
  try {
    while (TASKS_ST === state && state.doneNext != null) {
      if (!await tasksLoadDone(state)) break;
    }
  } finally { state.doneDrain = false; }
}
function companyNeedActor(task, need) {
  const me = myActor();
  // A task waiting on me waits with the bot that owns it, whoever asked for it.
  const candidates = taskWaitingOn(task) === me ? [task.owner]
    : [need?.ask?.from_actor, task?.ask?.from_actor, taskRequester(task), task?.owner];
  return candidates.find(actor => actorSlug(actor) && actor !== me)
    || candidates.find(actor => actor && actor !== me)
    || me;
}
function companyNeedGroups(items) {
  const needOrder = new Map((S.v2.needs || []).map((item, index) => [String(item.id), index]));
  const needById = new Map((S.v2.needs || []).map(item => [String(item.id), item]));
  const groups = new Map();
  for (const it of items) {
    if (!it.task || it.col === 'done' || !taskNeedsViewer(it.task)) continue;
    const need = needById.get(String(it.id)), actor = companyNeedActor(it.task, need);
    if (!groups.has(actor)) groups.set(actor, {actor, items: [], firstNeed: Infinity, firstRank: Infinity, updated: ''});
    const group = groups.get(actor);
    group.items.push(it);
    group.firstNeed = Math.min(group.firstNeed, needOrder.get(String(it.id)) ?? Infinity);
    group.firstRank = Math.min(group.firstRank, it.rank == null ? Infinity : Number(it.rank));
    if (String(it.updated || '') > group.updated) group.updated = String(it.updated || '');
  }
  return [...groups.values()].sort((a, b) => a.firstNeed - b.firstNeed || a.firstRank - b.firstRank
    || String(b.updated).localeCompare(String(a.updated)) || actorLabel(a.actor).localeCompare(actorLabel(b.actor)));
}
function tasksTabsPaint(state) { tasksPaint($('#task-view'), tasksTabsHTML(state)); }
// The body redraws on every load (the 30-second poll reloads the list): only rows that changed are replaced, so the
// focus, the scroll, the cursor, the selection and the open peek all stay. A hidden row leaves the selection.
function tasksPatchNode(live, next) {
  if (live.nodeType !== next.nodeType || live.nodeName !== next.nodeName
      || (live.nodeType === 1 && (live.dataset.taskKey || '') !== (next.dataset.taskKey || ''))) { live.replaceWith(next); return; }
  if (live.nodeType === 3) { if (live.data !== next.data) live.data = next.data; return; }
  if (live.nodeType !== 1) return;
  if (live.dataset.taskKey) { if (!next.dataset.sig || live.dataset.sig !== next.dataset.sig) live.replaceWith(next); return; }   // a row: whole, or not at all
  for (const {name} of [...live.attributes]) if (!next.hasAttribute(name)) live.removeAttribute(name);
  for (const {name, value} of [...next.attributes]) if (live.getAttribute(name) !== value) live.setAttribute(name, value);
  const a = [...live.childNodes], b = [...next.childNodes];
  if (a.length !== b.length) { live.replaceChildren(...b); return; }
  a.forEach((node, i) => tasksPatchNode(node, b[i]));
}
function tasksPatch(el, html) {
  const next = document.createElement('div');
  next.innerHTML = html;
  const a = [...el.childNodes], b = [...next.childNodes];
  if (!a.length || a.length !== b.length) { el.replaceChildren(...b); return; }
  a.forEach((node, i) => tasksPatchNode(node, b[i]));
}
function tasksRender(state) {
  const el = $('#task-body');
  if (!el || TASKS_ST !== state) return;
  state.memo = new Map();
  const active = document.activeElement, inBody = el.contains(active);
  const focusKey = inBody ? active.closest('[data-task-key]')?.dataset.taskKey || '' : '';
  const focusWhat = !inBody ? '' : active.matches('[data-select]') ? '[data-select]' : active.matches('[data-expand]') ? '[data-expand]'
    : active.matches('[data-open-task]') ? '[data-open-task]' : active.dataset.groupToggle ? `[data-group-toggle="${CSS.escape(active.dataset.groupToggle)}"]`
    : active.dataset.groupAdd ? `[data-group-add="${CSS.escape(active.dataset.groupAdd)}"]` : active.matches('.tl-gchat') ? `.tl-gchat[href="${CSS.escape(active.getAttribute('href'))}"]`
    : active.id === 'board-more' ? '#board-more' : active.matches('[data-clear-filters]') ? '[data-clear-filters]' : '';
  el.dataset.view = state.view;
  el.setAttribute('role', 'tabpanel'); el.setAttribute('aria-labelledby', 'task-tab-' + state.view);
  let html;
  if (state.view === 'recurring') html = tasksRecurringHTML(state);
  else {
    const items = taskItems(state);
    html = state.loading && state.view !== 'done' && !items.length
      ? '<div class="tl-empty">Loading tasks…</div>'
      : state.view === 'done' ? tasksDoneHTML(items, state)
      : state.view === 'board' ? tasksBoardHTML(items, state)
      : tasksListHTML(items, state);
  }
  if (state.view === 'recurring') el.innerHTML = html; else tasksPatch(el, html);
  if (focusWhat && !el.contains(document.activeElement)) {
    const host = focusKey ? el.querySelector(`[data-task-key="${CSS.escape(focusKey)}"]`) : el;
    host?.querySelector(focusWhat)?.focus({preventScroll: true});
  }
  tasksSelectionPrune(state);
  tasksCursorPaint(state);
  if (state.peek) {
    const row = el.querySelector(`[data-task-key="${CSS.escape(state.peek)}"]`);
    row?.classList.add('peeked'); row?.querySelector('[data-open-task]')?.setAttribute('aria-current', 'true');
  }
  tasksSelectionPaint(state);
  tasksTabsPaint(state);
}
