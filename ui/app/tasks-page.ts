/* ui/app/tasks-page.ts — pageTasks: loading, tools, needs you, Done, render
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The page's state: the shared TaskState (tasks.ts) plus the fields this file sets and reads on it.
interface TasksPageState extends TaskState {
  typeChosen?: boolean; migrate?: boolean; migrated?: boolean;
  filtered?: unknown; kept?: boolean; read?: boolean; typesLoaded?: boolean;
  searchTimer?: ReturnType<typeof setTimeout>; saveTimer?: ReturnType<typeof setTimeout>; doneDrain?: boolean;
  back?: string; loadAfter?: Promise<unknown> | null;
  pendingFinish?: {key: string; at: number; after: number} | null;
}
// The kept list of the last visit (S.lastRead.tasks) and the saved view preference (S.lastRead.tasksPref).
interface KeptTasksRead { key: string; tasks: TaskRow[]; labels: string[]; tags: import('../types/api').Tag[]; filtered?: unknown; since?: string | null; fullAt?: number; }
type TasksPref = { type?: unknown; view?: unknown; views?: unknown; };
// The new-task form's named controls. The form's own title is an HTMLElement attribute, so the form type intersects it.
interface TaskCreateForm {
  title: HTMLInputElement; owner: HTMLSelectElement; body: HTMLTextAreaElement; labels: HTMLInputElement;
  top: HTMLInputElement; link: HTMLInputElement; goal?: HTMLSelectElement; elements: TaskCreateControls;
}
interface TaskCreateControls extends PipelineCreateElements { private: HTMLInputElement; follow: HTMLInputElement; files?: HTMLInputElement; }
type TaskCreatePayload = {title: string; body: string; owner: string; private?: boolean; labels?: string[]; top?: boolean;
  follow?: boolean; links?: string[]; relations?: {task: string; kind: string}[]; goal_id?: string};
// The ask of a task or of a need item (companyNeedActor reads the sender, which the need item carries too).
type NeedAsk = {body?: string | null; recipient_id?: string | null; older?: boolean; sender_id?: string | null} | null;
// The company queue grouped by the actor who holds it (companyNeedGroups).
interface CompanyNeedGroup { actor: string; items: TaskItem[]; firstNeed: number; firstRank: number; updated_at: string; }
// Typed reads of a patched node: only element nodes reach the element reads (the callers check nodeType first).
const patchEl = (n: ChildNode): HTMLElement => n as HTMLElement;
const patchText = (n: ChildNode): Text => n as Text;

// openId opens one task in full as soon as the list is loaded: the #/task/<id> address a bot or
// the setup progress screen links to, which is the Tasks page with that item already open.
const TASK_PREF = 'tasks.view';
function pageTasks(forced?: string, openId = '') {
  TASKS_ST?.layoutAbort?.abort();
  PROP_TASKS = null;                       // the pickers' copy of the open tasks starts fresh with the page
  const state: TasksPageState = TASKS_ST = {view: 'foryou', tasks: [], labels: [], open: openId, q: '', who: 'me', side: 'plate',
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
  const keptPref = (S.lastRead.tasksPref || null) as TasksPref | null, keptRead = S.lastRead.tasks as KeptTasksRead | undefined,
    kept = keptRead?.key === activeTasksPath() ? keptRead : null;
  const prefApply = (value: TasksPref) => {
    if (!hadQuery && !state.typeChosen && value.type != null) state.type = String(value.type) || 'general';
    // Before `views: 2`, List and Board both showed Needs you, so an old choice means Needs you.
    if (!forced && value.view) state.view = value.views === 2 ? String(value.view) : 'foryou';
    tasksPrefsApply(state, value);
    tasksNormalise(state, forced);
    tasksTypeCheck(state);
  };
  if (keptPref) { prefApply(keptPref); state.migrate = false; }
  // A kept list read to its end keeps its cursor too, so coming back asks only for what changed since (a delta read),
  // and the 15-minute clock (refresh.js tasksFreshen) runs on from its last full read.
  if (kept) Object.assign(state, {tasks: kept.tasks, labels: kept.labels, tags: kept.tags, filtered: kept.filtered, loading: false, kept: true,
    since: kept.since || null, fullAt: kept.since ? kept.fullAt || 0 : 0, activeComplete: !!kept.since});
  const main = $('#main');
  main.classList.add('tasks-layout');
  main.innerHTML = `<div class="tasks-page"><div class="tasks-pane" id="tasks-pane">
      <header class="tl-head">
        <h1>Tasks</h1>
        <button type="button" class="tl-scope" id="task-scope" aria-haspopup="menu" aria-expanded="false"></button>
        <button type="button" class="tl-ib tl-pipeline-edit" id="task-pipeline-edit" aria-label="Edit pipeline" title="Edit pipeline" hidden><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M4 20h4L19 9l-4-4L4 16v4z"/><path d="M13.5 6.5l4 4"/></svg></button>
        <span class="tl-play" id="task-play" hidden></span>
        <span class="spacer"></span>
        <span class="tl-who-wrap" id="task-who-wrap" hidden></span>
        <div class="tl-search">${TL_ICON.search}<input type="search" id="task-q" spellcheck="false" aria-label="Search tasks" placeholder="Search" autocomplete="off"><kbd aria-hidden="true">/</kbd></div>
        <button type="button" class="tl-ib tl-filter" id="task-filter" aria-haspopup="true" aria-label="Filter" title="Filter">${TL_ICON.filter}<span class="tl-fbadge tnum" aria-hidden="true" hidden></span></button>
        <div class="tl-pair" id="task-layout" role="group" aria-label="Layout">
          <button type="button" data-layout="list" aria-pressed="false" aria-label="List" title="List">${TL_ICON.list}</button>
          <button type="button" data-layout="board" aria-pressed="false" aria-label="Board" title="Board">${TL_ICON.board}</button></div>
        <span class="tl-finished-slot"><span class="tl-pair tl-finished" id="task-finished" role="group" aria-label="Finished" hidden></span></span>
        <button type="button" class="tl-ib" id="task-archive" aria-pressed="false" aria-label="Finished" title="Finished tasks">${TL_ICON.archive}</button>
        <button type="button" class="tl-decide" data-go-through hidden></button>
        <button type="button" class="tl-ib" id="task-more" aria-haspopup="menu" aria-expanded="false" aria-label="More" title="More">${TL_ICON.dots}</button>
        <button class="primary add-ctl" type="button" id="task-new" aria-label="New task" title="New task (C)">${TL_ICON.plus}</button>
      </header>
      <div class="tl-bar" id="task-bar" hidden>
        <span class="tl-finished-bar"></span>
        <div class="tl-filters" role="group" aria-label="Filters"><span class="tl-chipset" id="task-chips"></span>
          <button type="button" class="linkish tl-clear" id="task-filter-clear" hidden>Clear</button></div>
      </div>
      <span id="tl-sel-word" hidden>Selected</span>
      <p class="tl-spend" id="task-spend" hidden></p>
      <div id="task-body"></div>
      <div class="tl-bulk" id="task-bulk" role="toolbar" aria-label="Selected tasks" hidden></div>
    </div></div>
    <div id="task-filter-pop" class="tl-pop" popover></div><div id="task-card-pops"></div>`;
  const bar = $('#task-bar');
  bar.onscroll = () => tasksBarEdge(bar);
  if (window.ResizeObserver) {
    new ResizeObserver(() => tasksBarEdge(bar)).observe(bar);
    // A narrower row folds the search to its icon; measured on the next frame, outside the observer's own delivery.
    new ResizeObserver(() => requestAnimationFrame(() => tasksHeadFit(state))).observe($('#tasks-pane .tl-head'));
  }
  // The button a menu opens from: the handler's own element (each onclick below sits on one).
  const tasksAnchor = (ev: Event) => ev.currentTarget as HTMLElement;
  $('#task-scope').onclick = (ev: MouseEvent) => tasksScopeMenu(state, tasksAnchor(ev));
  $('#task-layout').onclick = (ev: MouseEvent) => { const b = eventElement(ev)?.closest<HTMLElement>('[data-layout]'); if (b && b.dataset.layout !== state.view) tasksSetView(state, b.dataset.layout ?? ''); };
  $('#task-archive').onclick = () => tasksArchiveToggle(state);
  $('#tasks-pane').addEventListener('click', (ev: MouseEvent) => {
    const b = eventElement(ev)?.closest<HTMLElement>('#task-finished [data-view]');
    if (b && b.dataset.view !== state.view) { tasksSetView(state, b.dataset.view ?? ''); $(`#task-finished [data-view="${b.dataset.view}"]`)?.focus(); }
  });
  $('#task-more').onclick = (ev: MouseEvent) => tasksMoreMenu(state, tasksAnchor(ev));
  // Catch up: the Needs you cards from the first, one at a time (decide.js).
  for (const b of document.querySelectorAll<HTMLElement>('#tasks-pane [data-go-through]')) b.onclick = () => decideGoThrough();
  $('#task-new').onclick = () => { const p = tasksCreatePrefill(state); openTaskCreate(p.owner_id, {labels: p.labels}); };
  const search = $('#task-q');
  search.oninput = () => {
    state.q = search.value;
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => {
      tasksRender(state);
      if (tasksFinishedView(state.view) && state.q && state.doneNext != null) void tasksLoadAllDone(state);
    }, 150);
  };
  search.onkeydown = (ev: KeyboardEvent) => {
    if (ev.key === 'ArrowDown' && !search.value.includes('\n')) { ev.preventDefault(); search.blur(); tasksMove(state, 1); return; }
    if (ev.key !== 'Escape') return;
    if (search.value) ev.stopPropagation();
    else search.blur();
    search.value = state.q = '';
    clearTimeout(state.searchTimer);
    tasksRender(state);
  };
  $('#task-filter').onclick = (ev: MouseEvent) => tasksFieldMenu(state, tasksAnchor(ev));
  $('#task-chips').onclick = (ev: MouseEvent) => {
    const drop = eventElement(ev)?.closest<HTMLElement>('[data-chip-drop]');
    if (drop) {
      const field = drop.dataset.chipDrop ?? '';
      state.filters[field] = [];
      tasksFiltersChanged(state);
      $('#task-filter')?.focus();
      return;
    }
    const edit = eventElement(ev)?.closest<HTMLElement>('[data-chip-edit]');
    if (edit) tasksFilterMenu(state, edit.dataset.chipEdit!, edit);   // the closest [data-chip-edit] has it
  };
  $('#task-filter-clear').onclick = () => {
    state.filters = tasksEmptyFilters();
    tasksFiltersChanged(state);
    $('#task-filter')?.focus();
  };
  $('#task-who-wrap').onclick = (ev: MouseEvent) => { const b = eventElement(ev)?.closest<HTMLElement>('#task-who'); if (b) tasksWhoMenu(state, b); };
  $('#task-bulk').onclick = (ev: MouseEvent) => {
    const b = eventElement(ev)?.closest<HTMLElement>('[data-bulk]'); if (!b) return;
    tasksBulkMenu(state, b.dataset.bulk!, b);   // the closest [data-bulk] has it
  };
  const pop = $('#task-filter-pop');
  pop.addEventListener('keydown', tasksMenuKeys);
  pop.addEventListener('toggle', (ev: Event & {newState?: string}) => {
    if (ev.newState !== 'closed') return;
    for (const id of ['#task-scope', '#task-more']) $(id)?.setAttribute('aria-expanded', 'false');
    const anchor = state.menuAnchor; state.menuAnchor = null;
    if (anchor?.isConnected && (pop.contains(document.activeElement) || document.activeElement === document.body)) anchor.focus();
  });
  const body = $('#task-body');
  body.addEventListener('focusin', (ev: FocusEvent) => {
    const row = eventElement(ev)?.closest<HTMLElement>('[data-task-key]');
    if (row && row.dataset.taskKey !== state.cursor) { state.cursor = row.dataset.taskKey ?? ''; tasksCursorPaint(state); }
  });
  body.addEventListener('click', (ev: MouseEvent) => {
    if (TASKS_ST !== state) return;
    if (eventElement(ev)?.closest('#board-more')) { void tasksLoadDone(state); return; }
    tasksBodyClick(state, ev);
  });
  const pageAbort = state.layoutAbort = new AbortController();
  taskBoardBind(state, body);
  pullToRefresh($('#tasks-pane'), () => tasksPulled(state), {signal: pageAbort.signal, skip: () => taskBoardHolding(state)});
  document.addEventListener('keydown', ev => tasksKeys(state, ev), {signal: pageAbort.signal});
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
// The views are the scope (You, or a type's open tasks), the layout (list or board) and the finished feed (Done,
// Closed, Snoozed): the layout is kept for the next type picked, and leaving the finished feed goes back to where it was.
const tasksLayoutView = (view: string | undefined) => view === 'list' || view === 'board';
const tasksArchived = (view: string) => tasksFinishedView(view) || view === 'snoozed';
function tasksSetView(state: TasksPageState, view: string) {
  if (tasksArchived(view) && !tasksArchived(state.view)) state.back = state.view;
  if (tasksLayoutView(view)) state.layout = view;
  state.view = view;
  tasksMenuClose();
  state.selectMode = false;
  if (state.selected.size || state.bulkMsg) tasksSelectionClear(state);
  tasksRemember(state); tasksURLWrite(state); tasksTools(state); tasksRender(state);
  if (tasksFinishedShown(state) && !state.doneLoaded && !state.doneLoading) void tasksLoadDone(state, true);
  else if (tasksFinishedShown(state)) void tasksDoneMerge(state);
}
// Finished tasks are on screen: the Done or Closed tab, or a board with its finished steps unfolded.
const tasksFinishedShown = (state: TasksPageState) => tasksFinishedView(state.view) || taskBoardFinishedShown(state);
// The Done list's newest page is read once finished tasks are on screen, through the one loading guard.
function tasksDoneEnsure(state: TasksPageState) {
  if (tasksFinishedShown(state) && !state.doneLoaded && !state.doneLoading) void tasksLoadDone(state, true);
}
function tasksNormalise(state: TasksPageState, forced?: string) {
  if (!TASK_VIEWS.some(([k]) => k === state.view)) state.view = forced || 'foryou';
  if (tasksLayoutView(state.view)) state.layout = state.view;
  if (!tasksLayoutView(state.layout)) state.layout = 'list';
}
// The archive button: the finished feed of the type in view, and pressed again, back to the list it left.
function tasksArchiveToggle(state: TasksPageState) {
  if (!tasksArchived(state.view)) { tasksSetView(state, 'done'); return; }
  const back = state.back ?? '', layout = state.layout ?? 'list';
  tasksSetView(state, back === 'foryou' || tasksLayoutView(back) ? back : layout);
}
// The scope menu's type: that type's open tasks in the layout last used.
function tasksScopeType(state: TasksPageState, id: string) {
  taskPipelineSelect(state, id);
  if (!tasksLayoutView(state.view)) tasksSetView(state, state.layout || 'list');
}
function tasksRemember(state: TasksPageState) {
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
function tasksKeep(state: TasksPageState | null) {
  if (!state?.read && !state?.kept) return;
  S.lastRead.tasks = {key: activeTasksPath(), tasks: state.tasks, labels: state.labels, tags: state.tags, filtered: state.filtered,
    since: state.activeComplete ? state.since : null, fullAt: state.fullAt};
}
function taskOwnerOptions(selected = '', blank = 'Who is this for?') {
  const people = (S.people || []).filter(p => !p.hidden)
    .slice().sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)));
  const bots = (S.emps || []).filter(e => e.status !== 'archived' && (!isHiddenBot(e.id) || [e.id, 'bot:' + e.id].includes(selected)))
    .slice().sort((a, b) => byBotOrder(a, b) || String(a.display_name || a.slug).localeCompare(String(b.display_name || b.slug)));
  const pick = (value: string) => value === selected ? ' selected' : '';
  return `<option value="">${esc(blank)}</option>
    <optgroup label="Humans">${people.map(p =>
      `<option value="human:${esc(p.id)}"${pick('human:' + p.id) || pick(p.id)}>${esc(p.name || p.id)}</option>`).join('')}</optgroup>
    <optgroup label="Bots">${bots.map(e =>
      `<option value="${esc(e.id)}" title="${esc([(e.resolved_model_label || e.resolved_model) ? `Configured: ${e.resolved_model_label || e.resolved_model}${e.resolved_provider ? ` (${e.resolved_provider})` : ''}` : 'Configured model unavailable',
        e.latest_runtime_model ? `Latest run: ${e.latest_runtime_model} (${e.latest_runtime_provider}); next run may differ` : ''].filter(Boolean).join(' · '))}"${pick(e.id) || pick('bot:' + e.id)}>${esc(e.display_name || e.slug)}${e.resolved_model ? ` · ${esc(e.resolved_model_label || e.resolved_model)}${e.resolved_provider ? ` (${esc(e.resolved_provider)})` : ''}` : ''}${e.latest_runtime_model ? ` · latest: ${esc(e.latest_runtime_model)} (${esc(e.latest_runtime_provider)})` : ''}</option>`).join('')}</optgroup>`;
}
function taskCreateModal() {
  let d = $('#task-create');
  if (d) return d;
  d = document.createElement('dialog');
  d.id = 'task-create'; d.className = 'tmodal';
  d.addEventListener('click', (ev: MouseEvent) => { if (ev.target === d) d.close(); });
  document.body.appendChild(d);
  return d;
}
// The new-task form's file input also takes files dropped on the form and images pasted into it, and lists what it
// holds, so the submit reads one FileList.
function taskFormFiles(form: HTMLFormElement) {
  const input = form.querySelector<HTMLInputElement>('input[type=file]'), label = input?.closest<HTMLLabelElement>('label');
  if (!input || !label) return;
  const list = document.createElement('div');
  list.className = 'files';
  label.after(list);
  const show = () => { list.innerHTML = [...(input.files ?? [])].map(f => `<span>${esc(f.name)}</span>`).join(''); };
  const add = (files: File[]) => {
    if (!files.length) return;
    const merged = new DataTransfer();
    [...(input.files ?? []), ...files].forEach(f => merged.items.add(f));
    input.files = merged.files; show();
  };
  input.addEventListener('change', show);
  fileDropTarget(form, add);
  pasteImages(form, add);
}
function openTaskCreate(owner = '', opts: {parent?: (Pick<TaskRow, 'id' | 'title'> & Partial<Pick<TaskRow, 'private'>>) | null | undefined; labels?: unknown[] | undefined; type?: string | null | undefined} = {}) {
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
          <label><input type="checkbox" name="follow"> Follow</label>
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
    const bot = botRow(creation.elements.owner.value);
    if (parent?.private || !privateTouched)
      creation.elements.private.checked = !!parent?.private || !!bot?.private_tasks_default;
    creation.elements.private.disabled = !!parent?.private;
  };
  creation.elements.owner.addEventListener('change', privateDefault); privateDefault();
  goalOptions().then(goals => {
    const sel = $('#task-create-form select[name=goal]'); if (!sel || !goals.length) return;
    sel.innerHTML = '<option value="">No goal</option>' + goals.map(g =>
      `<option value="${esc(g.id)}">${esc(g.title)} · ${esc(goalOwnerInfo(g.owner_id).name)}</option>`).join('');
  });
  taskFormFiles(creation);
  void taskPipelineCreate($('#task-create-form'), opts.type || TASKS_ST?.type);
  $('#task-create-form').onsubmit = async (ev: SubmitEvent) => {
    ev.preventDefault();
    const form = ev.target as HTMLFormElement & TaskCreateForm, btn = form.querySelector<HTMLButtonElement>('[type=submit]'), msg = $('#task-create-msg');
    const title = form.title.value.trim(), owner = form.owner.value, body = form.body.value.trim();
    if (!title || !owner) { msg.textContent = 'Choose who this is for.'; return; }
    if (!body) { msg.textContent = 'Add details.'; form.body.focus(); return; }
    if (!btn) return;
    btn.disabled = true; msg.textContent = 'Creating…';
    const payload: TaskCreatePayload = {title, body, owner: owner.includes(':') ? owner : 'bot:' + owner};
    payload.private = form.elements.private.checked;
    taskPipelineCreatePayload(form, payload);
    const labels = form.labels.value.split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
    if (labels.length) payload.labels = labels;
    if (form.top.checked) payload.top = true;
    if (form.elements.follow.checked) payload.follow = true;
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
    } catch (e) { msg.innerHTML = `<span class="err">${esc(errorMessage(e, ''))}</span>`; btn.disabled = false; }
  };
  if (!d.open) d.showModal();
  formFocus(d);
}
function formFocus(root: ParentNode) {
  root.querySelector<HTMLElement>('input[name=title], textarea, select')?.focus();
}
const mergeTaskRows = (...groups: TaskRow[][]) => [...new Map(groups.flat().map(task => [task.id, task])).values()];
// Snoozed tasks too: the Snoozed view shows them and every other view leaves them out (taskItems).
// The first page is short so the list paints soon; the rest come 500 at a time (the server's most), so a full read of a
// thousand open tasks is three requests, not eleven.
const activeTasksPath = (offset = 0) => `/v2/tasks?lane=company&status=${ACTIVE_TASK_STATUSES}&snoozed=all&limit=${offset ? 500 : 100}&offset=${offset}`;
// Every status: a task that finished comes back as finished (as a live event brings it), not as gone.
const tasksChangedPath = (cursor: string) => `/v2/tasks?lane=company&snoozed=all&changed_after=${encodeURIComponent(cursor)}`;
// One full read at a time (state.fullRead), the first one on opening the page included. A load asked for while one is
// on its way never starts the list over: the 2-minute poll shares it, and a load after a save waits for it and then
// reads what changed since its cursor, which takes in the save. A list with a cursor reads only what changed.
function tasksLoad(state: TasksPageState, opts: {poll?: boolean} = {}): Promise<unknown> {
  if (state.fullRead) {
    if (opts.poll) return state.fullRead;
    return state.loadAfter ||= state.fullRead.then(() => {
      state.loadAfter = null;
      return TASKS_ST === state ? tasksLoad(state, opts) : false;
    });
  }
  return state.since ? tasksRead(state, opts) : tasksFullStart(state, opts);
}
function tasksFullStart(state: TasksPageState, opts: {poll?: boolean}): Promise<unknown> {
  const read: Promise<unknown> = state.fullRead = tasksRead(state, opts).finally(() => { if (state.fullRead === read) state.fullRead = null; });
  return read;
}
async function tasksRead(state: TasksPageState, opts: {poll?: boolean} = {}): Promise<unknown> {
  const seq = ++state.loadSeq;
  // A list read in full keeps the cursor it was read at (`since`; `cursor` is the keyboard's row); from then on a load
  // asks only for what changed since (backend/task_reads.py), unless the server says to read in full again (`reset`).
  const cursor = state.since;
  if (!cursor) state.activeComplete = false;
  // A list kept from the last visit is shown, never a skeleton, while it is read again.
  state.loading = !state.kept && !(state.tasks || []).some(t => !['done', 'closed'].includes(String(t.status)));
  if (!cursor) tasksRender(state);
  const others = () => [v2Get('/v2/tasks/labels'),
    taskTypesLoad().then(t => { state.typesLoaded = true; return t; }).catch(() => TASK_TYPES),
    // A load after a save also reads the personal queue: its membership comes from the feed, including other boards.
    // The first load and polls already read it in refresh(), alongside the task request.
    ...(state.read && !opts.poll ? [v2NeedsRead()] : [])];
  let changed: {gone?: unknown[]; tasks?: TaskRow[]; cursor: string | null; reset?: boolean} | null = null,
    active: {tasks?: TaskRow[]; cursor?: string | null; next_offset?: number | null} | null = null,
    lab: {labels?: string[]; tags?: import('../types/api').Tag[]} | null | undefined, held: Set<string> | null = null;
  const seen = new Set<string>(), isFinished = (t: TaskRow) => ['done', 'closed'].includes(String(t.status));
  if (cursor) {
    [changed, lab] = await Promise.all([v2Get(tasksChangedPath(cursor)), ...others()]);
    if (TASKS_ST !== state || state.loadSeq !== seq) return;
    // The server says to read in full again: that read is the one full read (tasksLoad).
    if (!changed || changed.reset) { state.since = null; state.activeComplete = false; return tasksLoad(state, opts); }
  }
  if (!changed) {
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

  const drain = async (next: number | null | undefined): Promise<boolean> => {
    while (next != null && TASKS_ST === state && state.loadSeq === seq) {
      const page = await v2Get(activeTasksPath(next));
      if (!page || TASKS_ST !== state || state.loadSeq !== seq) return false;
      state.tasks = mergeTaskRows(state.tasks, page.tasks || []);
      (page.tasks || []).forEach((t: TaskRow) => seen.add(String(t.id)));
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
      state.tasks = state.tasks.filter(t => !held?.has(String(t.id)) || seen.has(String(t.id)) || isFinished(t));
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
function tasksReadFull(state: TasksPageState, opts: {poll?: boolean} = {}): Promise<unknown> {
  if (state.fullRead) return state.fullRead;
  state.since = null;
  getForget();
  getForgetTagged(p => p.startsWith('/v2/tasks'));
  return tasksFullStart(state, opts);
}
// Pulled down at the top on a touch screen (pull-refresh.ts): the list, Needs you and the counts read again.
async function tasksPulled(state: TasksPageState) {
  const [ok] = await Promise.all([tasksReadFull(state), v2Refresh().catch(() => {})]);
  if (TASKS_ST !== state) return;
  tasksTools(state); tasksRender(state);
  if (ok === false) toast('Could not refresh the tasks. Check the connection and pull again.', true);
}
// A save took its task out of the view? Once a load that started after the save has drawn, the list and the peek
// move on (tasksAfterFinish). Any load may land it: an older one overtaken by the poll returns before it gets here.
function tasksFinishPending(state: TasksPageState, seq: number) {
  const p = state.pendingFinish;
  if (!p || seq <= p.after) return;
  state.pendingFinish = null;
  if (state.peek === p.key) tasksAfterFinish(state, p.key, p.at);
}
// Old combined views and deleted types fall back to General, never a mixture of types.
function tasksTypeCheck(state: TasksPageState) {
  if (state.type === 'general' || TASK_TYPES.some(type => type.id === state.type)) return;
  if (!state.type || !S.me?.cloud || state.typesLoaded) {
    state.type = 'general';
    taskPipelineRemember(state); tasksRemember(state);
    if (isTasksRoute(location.hash)) tasksURLWrite(state);
  }
}
async function tasksLoadDone(state: TasksPageState, reset = false) {
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
async function tasksDoneMerge(state: TasksPageState) {
  if (TASKS_ST !== state || state.doneLoading) return;
  const page = await v2Get(`/v2/tasks?lane=company&status=done,closed&sort=finished&limit=${DONE_CAP}&offset=0`);
  if (!page || TASKS_ST !== state) return;
  const fresh = new Map((page.tasks || []).map((t: TaskRow): [string, TaskRow] => [String(t.id), t]));
  state.tasks = mergeTaskRows((state.tasks || []).filter(t => !fresh.has(String(t.id)) || !['done', 'closed'].includes(String(t.status))), page.tasks || []);
  tasksTools(state); tasksRender(state);
}
async function tasksLoadAllDone(state: TasksPageState) {
  if (state.doneDrain) return;
  state.doneDrain = true;
  try {
    while (TASKS_ST === state && state.doneNext != null) {
      if (!await tasksLoadDone(state)) break;
    }
  } finally { state.doneDrain = false; }
}
function companyNeedActor(task: PartialTaskRow & {ask?: NeedAsk}, need: (Pick<PartialTaskRow, 'status' | 'waiting_on' | 'reconnect'> & {ask?: NeedAsk}) | null | undefined) {
  const me = myActor();
  // A task waiting on me waits with the bot that owns it, whoever asked for it.
  const candidates = taskWaitingOn(task) === me || taskWaitingOn(need) === me ? [task.owner_id]
    : [need?.ask?.sender_id, task?.ask?.sender_id, taskRequester(task), task?.owner_id];
  return candidates.find(actor => actorSlug(actor) && actor !== me)
    || candidates.find(actor => actor && actor !== me)
    || me;
}
function companyNeedGroups(items: TaskItem[]) {
  const needOrder = new Map((S.v2.needs || []).map((item, index) => [String(item.id), index]));
  const needById = new Map((S.v2.needs || []).map(item => [String(item.id), item]));
  const groups = new Map<string, CompanyNeedGroup>();
  for (const it of items) {
    // The server's queue already includes every counted task, including bot-owned escalations to their manager.
    if (!it.task) continue;
    const need = needById.get(String(it.id)), actor = companyNeedActor(it.task, need);
    let group = groups.get(actor);
    if (!group) { group = {actor, items: [], firstNeed: Infinity, firstRank: Infinity, updated_at: ''}; groups.set(actor, group); }
    group.items.push(it);
    group.firstNeed = Math.min(group.firstNeed, needOrder.get(String(it.id)) ?? Infinity);
    group.firstRank = Math.min(group.firstRank, it.rank == null ? Infinity : Number(it.rank));
    if (String(it.updated_at || '') > group.updated_at) group.updated_at = String(it.updated_at || '');
  }
  return [...groups.values()].sort((a, b) => a.firstNeed - b.firstNeed || a.firstRank - b.firstRank
    || String(b.updated_at).localeCompare(String(a.updated_at)) || actorLabel(a.actor).localeCompare(actorLabel(b.actor)));
}
// The body redraws on every load (the 30-second poll reloads the list): only rows that changed are replaced, so the
// focus, the scroll, the cursor, the selection and the open peek all stay. A hidden row leaves the selection.
// A `data-patch-key` node keeps what the person did in it (opened, typed) through redraws. With a `data-patch-sig` its
// content can still change (the Stale tickets card): a new sig patches it in place, and a <details> stays open or
// shut as the person left it.
function tasksPatchNode(live: ChildNode, next: ChildNode): void {
  if (live.nodeType !== next.nodeType || live.nodeName !== next.nodeName
      || (live.nodeType === 1 && (patchEl(live).dataset.taskKey || '') !== (patchEl(next).dataset.taskKey || ''))) { live.replaceWith(next); return; }
  if (live.nodeType === 3) { const old = patchText(live), data = patchText(next).data; if (old.data !== data) old.data = data; return; }
  if (live.nodeType !== 1) return;
  const keep = !!patchEl(live).dataset.patchKey && live.nodeName === 'DETAILS';
  if (patchEl(live).dataset.patchKey || patchEl(next).dataset.patchKey) {
    if (patchEl(live).dataset.patchKey !== patchEl(next).dataset.patchKey) { live.replaceWith(next); return; }
    if ((patchEl(live).dataset.patchSig || '') === (patchEl(next).dataset.patchSig || '')) return;
  }
  if (patchEl(live).dataset.taskKey) { if (!patchEl(next).dataset.sig || patchEl(live).dataset.sig !== patchEl(next).dataset.sig) live.replaceWith(next); return; }   // a row: whole, or not at all
  for (const {name} of [...patchEl(live).attributes]) if (!patchEl(next).hasAttribute(name) && !(keep && name === 'open')) patchEl(live).removeAttribute(name);
  for (const {name, value} of [...patchEl(next).attributes]) if (patchEl(live).getAttribute(name) !== value && !(keep && name === 'open')) patchEl(live).setAttribute(name, value);
  const a = [...live.childNodes], b = [...next.childNodes];
  if (tasksPatchKeyed(live, a, b)) return;
  if (a.length !== b.length) { patchEl(live).replaceChildren(...b); return; }
  a.forEach((node, i) => { const other = b[i]; if (other) tasksPatchNode(node, other); });
}
// Children that all carry a key (a `data-patch-key`, as the Stale tickets card's rows do, or a task's `data-task-key`,
// as a board column's cards do) are matched by it: a row that went is removed, a new one inserted, and one that stayed
// is patched where it is, so focus on it is not lost, also when one row goes and another comes in the same redraw.
// A card moved to another step rebuilds that one card, not both columns.
function tasksPatchKeyed(live: Node, a: ChildNode[], b: ChildNode[]): boolean {
  const key = (n: ChildNode): string => n.nodeType === 1 ? patchEl(n).dataset.patchKey || (patchEl(n).dataset.taskKey ? 't:' + patchEl(n).dataset.taskKey : '') : '';
  if (!a.length || !b.length || !a.every(key) || !b.every(key)) return false;
  const old = new Map<string, ChildNode>(a.map((n): [string, ChildNode] => [key(n), n]));
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
function tasksPatch(el: Element, html: string) {
  const next = document.createElement('div');
  next.innerHTML = html;
  const a = [...el.childNodes], b = [...next.childNodes];
  if (!a.length || a.length !== b.length) { el.replaceChildren(...b); return; }
  a.forEach((node, i) => { const other = b[i]; if (other) tasksPatchNode(node, other); });
}
function tasksRender(state: TasksPageState | null) {
  const el = $('#task-body');
  if (!el || !state || TASKS_ST !== state) return;
  if (taskBoardHolding(state)) return;      // a card is being dragged: the board redraws once it is let go
  state.memo = new Map();
  const active = document.activeElement, inBody = active instanceof HTMLElement && el.contains(active);
  const focusKey = inBody ? active.closest<HTMLElement>('[data-task-key]')?.dataset.taskKey || '' : '';
  const focusWhat = !inBody ? '' : active.matches('[data-select]') ? '[data-select]' : active.matches('[data-expand]') ? '[data-expand]'
    : active.matches('[data-open-task]') ? '[data-open-task]' : active.dataset.groupToggle ? `[data-group-toggle="${CSS.escape(active.dataset.groupToggle)}"]`
    : active.dataset.groupAdd ? `[data-group-add="${CSS.escape(active.dataset.groupAdd)}"]` : active.matches('.tl-gchat') ? `.tl-gchat[href="${CSS.escape(String(active.getAttribute('href')))}"]`
    : active.id === 'board-more' ? '#board-more' : active.matches('[data-clear-filters]') ? '[data-clear-filters]' : '';
  decidePaintButton();
  el.dataset.view = state.view;
  el.setAttribute('role', 'region');
  const items = taskItems(state);
  let html = state.loading && !tasksFinishedView(state.view) && !items.length
    ? skelRows(6)
    : tasksFinishedView(state.view) ? tasksDoneHTML(items, state)
    : state.view === 'board' ? tasksBoardHTML(items, state)
    : tasksListHTML(items, state);
  // The whole-list count includes unread pages, which load as the list scrolls or through Show more.
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
  const scopeChanged = tasksScopePaint(state), playChanged = pipelinePlayPaint(state);
  if (scopeChanged || playChanged) tasksHeadFit(state);
  pipelineRailSync(state);
  fbSync();
}
