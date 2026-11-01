/* ui/app/tasks-page.js — pageTasks: loading, tools, needs you, Done, render
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// openId opens one task in full as soon as the list is loaded: the #/task/<id> address a bot or
// the setup progress screen links to, which is the Tasks page with that item already open.
const TASK_PREF = 'tasks.view';
function pageTasks(forced, openId = '') {
  TASKS_ST?.layoutAbort?.abort();
  PROP_TASKS = null;                       // the pickers' copy of the open tasks starts fresh with the page
  const state = TASKS_ST = {view: 'foryou', tasks: [], labels: [], open: openId, q: '', who: 'me', side: 'plate',
    loading: true, doneLoaded: false, doneLoading: false, doneNext: null, loadSeq: 0,
    filters: tasksEmptyFilters(), group: {...TASK_GROUP_DEFAULT}, collapsed: new Set(), folded: new Set(),
    selected: new Set(), anchor: '', cursor: '', since: null, tabKey: '', peek: '', bulkFail: new Map(), bulkMsg: '', memo: new Map(),
    fullAt: 0, stale: false, fullRead: null};
  const query = tasksFiltersFromURL(state);
  const hadQuery = [...query.keys()].length > 0;
  if (state.urlView && TASK_VIEWS.some(([k]) => k === state.urlView)) forced = state.urlView;
  try { state.view = forced || localStorage.getItem('hub.tasks.view2') || 'foryou'; } catch { state.view = forced || 'foryou'; }
  taskPipelineState(state);
  if (hadQuery) state.type = state.urlType || 'general';
  tasksPrefsFromLocal(state);
  // Filters an older page saved (Mine, Asked by me, an owner, a tag) come over once as chips, unless the address has its own.
  state.migrate = !hadQuery;
  if (state.migrate && tasksMigrateOldFilters(state, null)) state.migrated = true;
  tasksNormalise(state, forced);
  // A return visit: the remembered view as the server last answered it, and the list as it was left, painted with the
  // shell. The reads below still go out and patch what changed.
  const keptPref = S.lastRead.tasksPref || null, kept = S.lastRead.tasks?.key === activeTasksPath() ? S.lastRead.tasks : null;
  const prefApply = value => {
    if (!hadQuery && !state.typeChosen && value.type != null) state.type = String(value.type) || 'general';
    // Before `views: 2`, List and Board both showed Needs you, so an old choice means Needs you.
    if (!forced && value.view) state.view = value.views === 2 ? String(value.view) : 'foryou';
    tasksPrefsApply(state, value);
    tasksNormalise(state, forced);
    tasksTypeCheck(state);
  };
  if (keptPref) { prefApply(keptPref); state.migrate = false; }
  if (kept) Object.assign(state, {tasks: kept.tasks, labels: kept.labels, tags: kept.tags, filtered: kept.filtered, loading: false, kept: true});
  const main = $('#main');
  main.classList.add('tasks-layout');
  main.innerHTML = `<div class="tasks-page"><div class="tasks-pane" id="tasks-pane">
      <header class="tl-head">
        <h1>Tasks</h1>
        <div class="tl-tabs" id="task-view" role="tablist" aria-label="Task views"></div>
        <span class="spacer"></span>
        <div class="tl-type-search"><div class="tl-types" id="task-type" role="group" aria-label="Task type"></div>
        <div class="tl-search">${TL_ICON.search}<input type="search" id="task-q" spellcheck="false" aria-label="Search tasks" placeholder="Search" autocomplete="off"><kbd aria-hidden="true">/</kbd></div></div>
        <button type="button" class="tl-decide tl-decide-head" data-go-through hidden></button>
        <button class="primary tl-new" type="button" id="task-new" aria-label="New task" title="New task (C)">${TL_ICON.plus}<span>New task</span></button>
      </header>
      <div class="tl-bar" id="task-bar">
        <span class="tl-who-wrap" id="task-who-wrap" hidden></span>
        <div class="tl-filters" role="group" aria-label="Filters"><span class="tl-chipset" id="task-chips"></span>
          <button type="button" class="tl-addf" id="task-filter" aria-haspopup="true">${TL_ICON.filter}<span>Filter</span></button>
          <button type="button" class="linkish tl-clear" id="task-filter-clear" hidden>Clear</button></div>
        <span class="spacer"></span>
        <button type="button" class="tl-decide tl-decide-bar" data-go-through hidden></button>
        ${S.me?.role === 'bot' ? '' : `<button type="button" class="tl-addf tl-ic" id="task-stats" aria-label="Task stats" title="Task stats">${TL_ICON.chart}</button>`}
        <button type="button" class="tl-addf tl-ic tl-pin" id="task-pin" aria-pressed="false" aria-label="Pin to the sidebar" title="Pin to the sidebar">${TL_ICON.pin}</button>
        <button type="button" class="tl-addf tl-selmode" id="task-select-mode" aria-pressed="false" hidden>Select</button>
        <span class="tl-groupby" id="task-group-wrap"></span>
      </div>
      <span id="tl-sel-word" hidden>Selected</span>
      <div id="task-body"></div>
      <div class="tl-bulk" id="task-bulk" role="toolbar" aria-label="Selected tasks" hidden></div>
    </div></div>
    <div id="task-filter-pop" class="tl-pop" popover></div><div id="task-card-pops"></div>`;
  const bar = $('#task-bar');
  bar.onscroll = () => tasksBarEdge(bar);
  if (window.ResizeObserver) new ResizeObserver(() => tasksBarEdge(bar)).observe(bar);
  $('#task-type').onclick = ev => {
    const button = ev.target.closest('[data-task-type]');
    if (button) taskPipelineSelect(state, button.dataset.taskType);
    else if (ev.target.closest('#task-type-more')) taskPipelineMenu(state, $('#task-type-more'));
  };
  $('#task-pin').onclick = () => tasksPinToggle(state);
  // Go through: the Needs you cards from the first, one at a time (decide.js).
  for (const b of document.querySelectorAll('#tasks-pane [data-go-through]')) b.onclick = () => decideGoThrough();
  $('#task-stats')?.addEventListener('click', () => taskStatsOpen(state));
  $('#task-new').onclick = () => { const p = tasksCreatePrefill(state); openTaskCreate(p.owner, {labels: p.labels}); };
  const search = $('#task-q');
  search.oninput = () => {
    state.q = search.value;
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => {
      tasksRender(state);
      if (tasksFinishedView(state.view) && state.q && state.doneNext != null) void tasksLoadAllDone(state);
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
  $('#task-who-wrap').onclick = ev => { const b = ev.target.closest('#task-who'); if (b) tasksWhoMenu(state, b); };
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
  body.addEventListener('click', ev => {
    if (TASKS_ST !== state) return;
    if (ev.target.closest('#board-more')) { void tasksLoadDone(state); return; }
    tasksBodyClick(state, ev);
  });
  state.layoutAbort = new AbortController();
  taskBoardBind(state, body);
  pullToRefresh($('#tasks-pane'), () => tasksPulled(state), {signal: state.layoutAbort.signal, skip: () => taskBoardHolding(state)});
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
    const value = pref?.value && typeof pref.value === 'object' ? pref.value : null;
    // The same answer as the one already applied draws nothing again: the header is painted once.
    if (value && !sameData(value, keptPref)) {
      S.lastRead.tasksPref = value;
      prefApply(value);
      const migrated = state.migrate && tasksMigrateOldFilters(state, value);
      if (!openId && (hadQuery || state.type !== 'general' || migrated)) tasksURLWrite(state);
      if (migrated) tasksRemember(state);       // the saved value is written again without the old keys
      tasksTools(state);
      tasksRender(state);
    } else if (!value && !keptPref) { tasksTools(state); tasksRender(state); }
    tasksDoneEnsure(state);
    await loading;
    // An old Decide link to one item (#/decide?item=<id>): its card over Needs you.
    const want = DECIDE_WANT; DECIDE_WANT = '';
    if (want && TASKS_ST === state) await decideWanted(want);
  })();
}
function tasksSetView(state, view) {
  state.view = view;
  tasksMenuClose();
  state.selectMode = false;
  if (state.selected.size || state.bulkMsg) tasksSelectionClear(state);
  tasksRemember(state); tasksURLWrite(state); tasksTools(state); tasksRender(state);
  if (tasksFinishedShown(state) && !state.doneLoaded && !state.doneLoading) void tasksLoadDone(state, true);
  else if (tasksFinishedShown(state)) void tasksDoneMerge(state);
}
// Finished tasks are on screen: the Done or Closed tab, or a board with its finished steps unfolded.
const tasksFinishedShown = state => tasksFinishedView(state.view) || taskBoardFinishedShown(state);
// The Done list's newest page is read once finished tasks are on screen, through the one loading guard.
function tasksDoneEnsure(state) {
  if (tasksFinishedShown(state) && !state.doneLoaded && !state.doneLoading) void tasksLoadDone(state, true);
}
function tasksNormalise(state, forced) {
  if (!TASK_VIEWS.some(([k]) => k === state.view)) state.view = forced || 'foryou';
}
function tasksRemember(state) {
  taskPipelineRemember(state);
  S.lastRead.tasksPref = {type: state.type, view: state.view, views: 2, ...tasksPrefsValue(state)};   // the next visit opens as left
  try {
    localStorage.setItem('hub.tasks.view2', state.view);
    localStorage.setItem('hub.tasks.layout', JSON.stringify(tasksPrefsValue(state)));
  } catch {}
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(() => { void post('/v2/preferences/' + TASK_PREF,
    {value: {type: state.type, view: state.view, views: 2, ...tasksPrefsValue(state)}}).catch(() => {}); }, 400);
}
// Leaving the page keeps its list for the next visit (S.lastRead), unless the list was never read (a read again under
// way when leaving still keeps the last one).
function tasksKeep(state) {
  if (!state?.read && !state?.kept) return;
  S.lastRead.tasks = {key: activeTasksPath(), tasks: state.tasks, labels: state.labels, tags: state.tags, filtered: state.filtered};
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
      `<option value="${esc(e.name)}" title="${esc([(e.resolved_model_label || e.resolved_model) ? `Configured: ${e.resolved_model_label || e.resolved_model}${e.resolved_provider ? ` (${e.resolved_provider})` : ''}` : 'Configured model unavailable',
        e.latest_runtime_model ? `Latest run: ${e.latest_runtime_model} (${e.latest_runtime_provider}); next run may differ` : ''].filter(Boolean).join(' · '))}"${pick(e.name) || pick('bot:' + e.name)}>${esc(e.display_name || e.name)}${e.resolved_model ? ` · ${esc(e.resolved_model_label || e.resolved_model)}${e.resolved_provider ? ` (${esc(e.resolved_provider)})` : ''}` : ''}${e.latest_runtime_model ? ` · latest: ${esc(e.latest_runtime_model)} (${esc(e.latest_runtime_provider)})` : ''}</option>`).join('')}</optgroup>`;
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
// The new-task form's file input also takes files dropped on the form and images pasted into it, and lists what it
// holds, so the submit reads one FileList.
function taskFormFiles(form) {
  const input = form.querySelector('input[type=file]');
  const list = document.createElement('div');
  list.className = 'files';
  input.closest('label').after(list);
  const show = () => { list.innerHTML = [...input.files].map(f => `<span>${esc(f.name)}</span>`).join(''); };
  const add = files => {
    if (!files.length) return;
    const merged = new DataTransfer();
    [...input.files, ...files].forEach(f => merged.items.add(f));
    input.files = merged.files; show();
  };
  input.addEventListener('change', show);
  fileDropTarget(form, add);
  pasteImages(form, add);
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
        <label class="attach">Attach files <input type="file" name="files" multiple aria-label="Task attachments"></label><span class="muted"> or drop them on this form, or paste a screenshot</span>
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
  taskFormFiles(creation);
  void taskPipelineCreate($('#task-create-form'), opts.type || TASKS_ST?.type);
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
    if (parent) payload.relations = [{task: parent.id, kind: 'parent'}];
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
// Snoozed tasks too: the Snoozed view shows them and every other view leaves them out (taskItems).
const activeTasksPath = (offset = 0) => `/v2/tasks?lane=company&status=${ACTIVE_TASK_STATUSES}&snoozed=all&limit=100&offset=${offset}`;
// Every status: a task that finished comes back as finished (as a live event brings it), not as gone.
const tasksChangedPath = cursor => `/v2/tasks?lane=company&snoozed=all&changed_after=${encodeURIComponent(cursor)}`;
async function tasksLoad(state, opts = {}) {
  const seq = ++state.loadSeq;
  // A list read in full keeps the cursor it was read at (`since`; `cursor` is the keyboard's row); from then on a load
  // asks only for what changed since (backend/task_reads.py), unless the server says to read in full again (`reset`).
  const cursor = state.since;
  if (!cursor) state.activeComplete = false;
  // A list kept from the last visit is shown, never a skeleton, while it is read again.
  state.loading = !state.kept && !(state.tasks || []).some(t => !['done', 'closed'].includes(String(t.status)));
  if (!cursor) tasksRender(state);
  const others = () => [v2Get('/v2/tasks/labels'),
    taskTypesLoad().then(t => { state.typesLoaded = true; return t; }).catch(() => TASK_TYPES)];
  let changed = null, active = null, lab, held = null;
  const seen = new Set(), isFinished = t => ['done', 'closed'].includes(String(t.status));
  if (cursor) {
    [changed, lab] = await Promise.all([v2Get(tasksChangedPath(cursor)), ...others()]);
    if (TASKS_ST !== state || state.loadSeq !== seq) return;
    if (!changed || changed.reset) { changed = null; state.since = null; state.activeComplete = false; }
  }
  if (!changed) {
    if (cursor) tasksRender(state);
    [active, lab] = await Promise.all([v2Get(activeTasksPath()), ...others()]);
    // A first read that lands after the page was left is kept for the next visit, as leaving would have kept it.
    if (TASKS_ST !== state && state.loadSeq === seq && active && !state.read && !state.kept && !S.lastRead.tasks)
      S.lastRead.tasks = {key: activeTasksPath(), tasks: active.tasks || [], labels: lab?.labels || [], tags: lab?.tags || []};
    if (TASKS_ST !== state || state.loadSeq !== seq) return;
  }
  if (changed) {
    const gone = new Set((changed.gone || []).map(String));
    state.tasks = mergeTaskRows((state.tasks || []).filter(t => !gone.has(String(t.id))), changed.tasks || []);
    state.since = changed.cursor;
  } else if (active) {
    // Read in full. The open tasks already held stay until every page has come, so a later page that fails leaves the
    // list as long as it was; only a list read to its end drops the ones the server no longer lists as open (below).
    // A finished copy held is replaced when the task is open again.
    held = new Set((state.tasks || []).filter(t => !isFinished(t)).map(t => String(t.id)));
    (active.tasks || []).forEach(t => seen.add(String(t.id)));
    state.tasks = mergeTaskRows(state.tasks || [], active.tasks || []);
  }
  // A read that failed (offline, the server away) leaves the list as it was rather than empty; the next poll reads again.
  state.labels = lab?.labels || [];
  state.tags = lab?.tags || [];
  state.loading = false; state.read = true;
  tasksTypeCheck(state);
  tasksTools(state); tasksRender(state);
  if (tasksNeedsQueue(state)) void askFilteredLoad(state);
  tasksFilterMenuLoaded(state);
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
      if (!page || TASKS_ST !== state || state.loadSeq !== seq) return false;
      state.tasks = mergeTaskRows(state.tasks, page.tasks || []);
      (page.tasks || []).forEach(t => seen.add(String(t.id)));
      next = page.next_offset;
      tasksTools(state); tasksRender(state);
    }
    return next == null;
  };
  let complete = true;
  if (active) {
    complete = await drain(active.next_offset);
    if (TASKS_ST !== state || state.loadSeq !== seq) return;      // a newer read took over
    // Only a list read to its end can be caught up from its cursor, drops what left it, and starts the clock for the
    // next full read again (refresh.js tasksFreshen).
    state.activeComplete = complete;
    state.since = complete ? active.cursor || null : null;
    if (complete) {
      state.tasks = state.tasks.filter(t => !held.has(String(t.id)) || seen.has(String(t.id)) || isFinished(t));
      state.fullAt = Date.now(); state.stale = false;
      tasksTools(state); tasksRender(state);
    }
  }
  // Done and Closed keep their own paging. A reload after a change folds in the newest page; the poll does that too, but
  // only while finished tasks are on screen (Done, Closed, or an unfolded board), and never more than that one page.
  // Asked only now: the board's type is known once the task types have loaded.
  const doneShown = tasksFinishedShown(state);
  const reloadDone = opts.poll ? doneShown && state.doneLoaded : (state.doneLoaded || doneShown);
  if (reloadDone && TASKS_ST === state && state.loadSeq === seq) await (state.doneLoaded ? tasksDoneMerge(state) : tasksLoadDone(state, true));
  return changed ? true : active ? complete : false;
}
// The whole list read again, as on opening the page: the cursor is dropped and the kept copies of the task paths are
// forgotten, so the server reads every row afresh rather than answering 304. A delta read and the live stream carry
// only what the change log names; a fact a row shows that changes without a task change (a run of the task's own
// starting, a question growing stale) reaches an open list only this way. One full read at a time: a second call
// while one is on its way shares it.
function tasksReadFull(state, opts = {}) {
  if (state.fullRead) return state.fullRead;
  state.since = null;
  getForget();
  getForgetTagged(p => p.startsWith('/v2/tasks'));
  const read = state.fullRead = tasksLoad(state, opts).finally(() => { if (state.fullRead === read) state.fullRead = null; });
  return read;
}
// Pulled down at the top on a touch screen (pull-refresh.js): the list, Needs you and the counts read again.
async function tasksPulled(state) {
  const [ok] = await Promise.all([tasksReadFull(state), v2Refresh().catch(() => {})]);
  if (TASKS_ST !== state) return;
  tasksTools(state); tasksRender(state);
  if (ok === false) toast('Could not refresh the tasks. Check the connection and pull again.', true);
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
  if (page && tasksFinishedView(state.view) && state.q && state.doneNext != null && !state.doneDrain) {
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
  const candidates = taskWaitingOn(task) === me || taskWaitingOn(need) === me ? [task.owner]
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
    if (!groups.has(actor)) groups.set(actor, {actor, items: [], firstNeed: Infinity, firstRank: Infinity, updated_at: ''});
    const group = groups.get(actor);
    group.items.push(it);
    group.firstNeed = Math.min(group.firstNeed, needOrder.get(String(it.id)) ?? Infinity);
    group.firstRank = Math.min(group.firstRank, it.rank == null ? Infinity : Number(it.rank));
    if (String(it.updated_at || '') > group.updated_at) group.updated_at = String(it.updated_at || '');
  }
  return [...groups.values()].sort((a, b) => a.firstNeed - b.firstNeed || a.firstRank - b.firstRank
    || String(b.updated_at).localeCompare(String(a.updated_at)) || actorLabel(a.actor).localeCompare(actorLabel(b.actor)));
}
function tasksTabsPaint(state) { tasksPaint($('#task-view'), tasksTabsHTML(state)); }
// The body redraws on every load (the 30-second poll reloads the list): only rows that changed are replaced, so the
// focus, the scroll, the cursor, the selection and the open peek all stay. A hidden row leaves the selection.
// A `data-patch-key` node keeps what the person did in it (opened, typed) through redraws. With a `data-patch-sig` its
// content can still change (the Stale tickets card): a new sig patches it in place, and a <details> stays open or
// shut as the person left it.
function tasksPatchNode(live, next) {
  if (live.nodeType !== next.nodeType || live.nodeName !== next.nodeName
      || (live.nodeType === 1 && (live.dataset.taskKey || '') !== (next.dataset.taskKey || ''))) { live.replaceWith(next); return; }
  if (live.nodeType === 3) { if (live.data !== next.data) live.data = next.data; return; }
  if (live.nodeType !== 1) return;
  const keep = !!live.dataset.patchKey && live.nodeName === 'DETAILS';
  if (live.dataset.patchKey || next.dataset.patchKey) {
    if (live.dataset.patchKey !== next.dataset.patchKey) { live.replaceWith(next); return; }
    if ((live.dataset.patchSig || '') === (next.dataset.patchSig || '')) return;
  }
  if (live.dataset.taskKey) { if (!next.dataset.sig || live.dataset.sig !== next.dataset.sig) live.replaceWith(next); return; }   // a row: whole, or not at all
  for (const {name} of [...live.attributes]) if (!next.hasAttribute(name) && !(keep && name === 'open')) live.removeAttribute(name);
  for (const {name, value} of [...next.attributes]) if (live.getAttribute(name) !== value && !(keep && name === 'open')) live.setAttribute(name, value);
  const a = [...live.childNodes], b = [...next.childNodes];
  if (tasksPatchKeyed(live, a, b)) return;
  if (a.length !== b.length) { live.replaceChildren(...b); return; }
  a.forEach((node, i) => tasksPatchNode(node, b[i]));
}
// Children that all carry a key (a `data-patch-key`, as the Stale tickets card's rows do, or a task's `data-task-key`,
// as a board column's cards do) are matched by it: a row that went is removed, a new one inserted, and one that stayed
// is patched where it is, so focus on it is not lost, also when one row goes and another comes in the same redraw.
// A card moved to another step rebuilds that one card, not both columns.
function tasksPatchKeyed(live, a, b) {
  const key = n => n.nodeType === 1 ? n.dataset.patchKey || (n.dataset.taskKey ? 't:' + n.dataset.taskKey : '') : '';
  if (!a.length || !b.length || !a.every(key) || !b.every(key)) return false;
  const old = new Map(a.map(n => [key(n), n]));
  if (old.size !== a.length || new Set(b.map(key)).size !== b.length) return false;
  const want = new Set(b.map(key));
  for (const n of a) if (!want.has(key(n))) n.remove();
  b.forEach((n, i) => {
    const o = old.get(key(n)), at = live.childNodes[i] || null;
    if (!o) { live.insertBefore(n, at); return; }
    if (o !== at) live.insertBefore(o, at);
    tasksPatchNode(o, n);
  });
  return true;
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
  if (taskBoardHolding(state)) return;      // a card is being dragged: the board redraws once it is let go
  state.memo = new Map();
  const active = document.activeElement, inBody = el.contains(active);
  const focusKey = inBody ? active.closest('[data-task-key]')?.dataset.taskKey || '' : '';
  const focusWhat = !inBody ? '' : active.matches('[data-select]') ? '[data-select]' : active.matches('[data-expand]') ? '[data-expand]'
    : active.matches('[data-open-task]') ? '[data-open-task]' : active.dataset.groupToggle ? `[data-group-toggle="${CSS.escape(active.dataset.groupToggle)}"]`
    : active.dataset.groupAdd ? `[data-group-add="${CSS.escape(active.dataset.groupAdd)}"]` : active.matches('.tl-gchat') ? `.tl-gchat[href="${CSS.escape(active.getAttribute('href'))}"]`
    : active.id === 'board-more' ? '#board-more' : active.matches('[data-clear-filters]') ? '[data-clear-filters]' : '';
  decidePaintButton();
  el.dataset.view = state.view;
  el.setAttribute('role', 'tabpanel'); el.setAttribute('aria-labelledby', 'task-tab-' + state.view);
  const items = taskItems(state);
  let html = state.loading && !tasksFinishedView(state.view) && !items.length
    ? skelRows(6)
    : tasksFinishedView(state.view) ? tasksDoneHTML(items, state)
    : state.view === 'board' ? tasksBoardHTML(items, state)
    : tasksListHTML(items, state);
  // Needs you holds the server's list a page at a time: its order, groups, approvals and questions come with each page.
  const paged = tasksNeedsQueue(state) && !state.loading;
  if (paged) html += needsMoreButton();
  tasksPatch(el, html);
  if (state.view === 'board') boardReleaseSlots();
  needsMoreWatch(paged ? el : null);
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
