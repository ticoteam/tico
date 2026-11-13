/* ui/app/tasks-page.ts — the Tasks page (docs/pipelines-v3.md S2, S7): the header, You, a pipeline, the archive, search
   and filter, and New task (S6).
   The header, left to right: "Tasks", the view button ("You 7 ▾" or a pipeline's name), the pencil on a pipeline,
   then search, filter, list or columns on a pipeline, archive, play (Catch up) and +.
   You is Needs you in three groups, Answer, Review and Do; a row is the sentence asked, and opens the ask screen
   (decide.ts). A pipeline is drawn by task-pipelines.ts through TASK_PAGE_HOOKS (task-row.ts), else as a list grouped
   by stage; its manager's rail sits beside it. The archive is Snoozed, Done and Closed, of the pipeline in view or of every pipeline from You.
   Reads: GET /api/v2/needs-you, GET /api/v2/pipelines, GET /api/v2/tasks (through task-api.ts).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

type TasksLayout = 'list' | 'columns';
type TasksArchive = '' | 'snoozed' | 'done' | 'closed';
interface TasksPageState {
  /** The pipeline in view, by id; '' is You. */
  pipeline: string;
  layout: TasksLayout;
  archive: TasksArchive;
  q: string;
  filters: TaskFilters;
  needs: NeedsEntry[];
  /** The archive's tasks, or the pipeline's open tasks. */
  tasks: TaskBriefV3[];
  pipelines: PipelineRec[];
  loading: boolean;
  /** What is drawn while the view's read is out is its last read (S.lastRead.tasks), so no skeleton. */
  kept: boolean;
  failed: string;
  /** The view's reads against its live events (task-api.ts TaskFeed): a read that lands late undoes no newer event. */
  feed: TaskFeed;
  /** Where the list's catch-up resumes (GET /v2/tasks?changed_after); '' until a full read gave one. */
  cursor: string;
  /** A live change came while a read was on its way: catch up once it lands. */
  behind: boolean;
  /** The server holds more of the view than `tasks` (a pipeline over 500 open tasks, an archive over 200, a search's
   * or a filter's matches over 100). */
  more: boolean;
  /** The last row the server gave. */
  edge: TaskBriefV3 | null;
  /** Where the next page starts: the server's sort key of that last row (a keyset page). */
  after: string;
  /** The search and filters the rows were read with (tasksNarrow), and its key: when the page's own differ, the view
   * is read again with them. */
  narrow: TaskBriefQuery;
  readKey: string;
  /** The view's rows read with no search or filter: a filter's values come from them too, so a filter set does not
   * take away the values beside it. */
  base: TaskBriefV3[];
  /** The next page's read, while it is out. */
  paging: Promise<void> | null;
  /** A page read failed or was overtaken. */
  stalled: boolean;
  abort: AbortController;
  /** The popover menu's anchor and the filter whose values it shows (task-list-menus.ts). */
  menuAnchor?: HTMLElement | null | undefined;
  menuField?: string | undefined;
  searchTimer?: ReturnType<typeof setTimeout>;
}
let TASKS_ST: TasksPageState | null = null;
const TASKS_KEPT = 'tico.tasks.view';
const TASK_ARCHIVES: [TasksArchive, string][] = [['snoozed', 'Snoozed'], ['done', 'Done'], ['closed', 'Closed']];
let TASK_PIPELINES: PipelineRec[] | null = null;
const tasksPipelinesRead = async (): Promise<PipelineRec[]> => (TASK_PIPELINES = (await pipelinesAll()).filter(p => !p.retired_at));

// The view comes from the address (#/tasks?pipeline=<id>&layout=columns&archive=done), else as it was last left.
// A `view` (NEEDS_YOU, Home's My tasks) names You: no pipeline unless the address names one.
function tasksViewFromURL(): Pick<TasksPageState, 'pipeline' | 'layout' | 'archive'> {
  const q = new URLSearchParams(S.route.split('?')[1] || '');
  let kept: {pipeline?: string; layout?: string} = {};
  try { kept = JSON.parse(localStorage.getItem(TASKS_KEPT) || '{}') as typeof kept; } catch { kept = {}; }
  const named = q.has('pipeline') || q.has('archive') || q.has('view');
  const layout = q.get('layout') || kept.layout;
  const archive = q.get('archive') || '';
  return {
    pipeline: named ? q.get('pipeline') || '' : kept.pipeline || '',
    layout: layout === 'columns' ? 'columns' : 'list',
    archive: TASK_ARCHIVES.some(([k]) => k === archive) ? archive as TasksArchive : '',
  };
}
function tasksURLWrite(state: TasksPageState) {
  const q = new URLSearchParams();
  if (state.pipeline) q.set('pipeline', state.pipeline);
  if (state.pipeline && state.layout === 'columns') q.set('layout', 'columns');
  if (state.archive) q.set('archive', state.archive);
  const want = TASKS + (q.toString() ? '?' + q.toString() : '');
  if (location.hash !== want) { history.replaceState(null, '', want); S.route = LAST_ROUTE = want; }
  try { localStorage.setItem(TASKS_KEPT, JSON.stringify({pipeline: state.pipeline, layout: state.layout})); } catch { /* the view is still in the address */ }
}

// openId opens one task in full over the page: the #/task/<id> address.
function pageTasks(openId = '') {
  tasksLeave();
  const state: TasksPageState = {...tasksViewFromURL(), q: '', filters: {}, needs: S.v2.needs ? [...S.v2.needs] as NeedsEntry[] : [],
    tasks: [], pipelines: TASK_PIPELINES || [], loading: true, kept: false, failed: '', feed: taskFeedNew(), cursor: '', behind: false, more: false, edge: null, after: '', narrow: {}, readKey: '', base: [], paging: null, stalled: false, abort: new AbortController()};
  tasksKept(state);
  TASKS_ST = state;
  const main = $('#main');
  main.classList.add('tasks-layout');
  main.innerHTML = `<div class="tasks-page"><div class="tasks-pane" id="tasks-pane">
      ${pageHead({title: 'Tasks', hook: 'tl-head',
        after: '<button type="button" class="tl-scope" id="task-scope" aria-haspopup="menu" aria-expanded="false"></button>',
        // One row (spec "Pipeline board: one-row header", part A): search, Filter, ▶ Review, the manager's face and ⋯. Nothing
        // on it is accent: New task, Archived, Edit pipeline and List/Columns are in ⋯ (C still makes a task).
        tools: `<div class="tl-search">${TL_ICON.search}<input type="search" id="task-q" spellcheck="false" aria-label="Search tasks" placeholder="Search" autocomplete="off"><kbd aria-hidden="true">/</kbd>`
          + `<button type="button" class="tl-search-x" aria-label="Close search" title="Close search">${TL_ICON.x}</button></div>`
          + `<button type="button" class="tl-ib" id="task-filter" aria-haspopup="true" aria-label="Filter" title="Filter">${TL_ICON.filter}<span class="tl-fbadge tnum" aria-hidden="true" hidden></span></button>`
          + '<button type="button" class="tl-review none" id="task-play" aria-label="Review: nothing waits on you" title="Review"><svg viewBox="0 0 14 14" width="13" height="13" aria-hidden="true" focusable="false"><path d="M4 2.5v9l7-4.5z" fill="currentColor"/></svg><span class="tl-review-n tnum"></span></button>'
          + `<button type="button" class="tl-ib" id="task-manager" aria-pressed="false" aria-label="Manager" title="Manager" hidden>${TL_ICON.chat}</button>`,
        moreId: 'task-menu', menuId: 'task-menu-pop',
        more: '<button type="button" id="task-mi-new" class="tl-mi-k" data-key="C" aria-keyshortcuts="c">New task</button>'
          + '<button type="button" id="task-mi-archive">Archived</button>'
          + '<button type="button" id="task-mi-edit" hidden>Edit pipeline</button>'
          + '<div class="tl-mi-layout" id="task-mi-layout" role="group" aria-label="View" hidden><hr>'
          + '<button type="button" role="menuitemradio" data-layout="list" aria-checked="false">List</button>'
          + '<button type="button" role="menuitemradio" data-layout="columns" aria-checked="false">Columns</button></div>'})}
      <div class="tl-bar" id="task-bar" hidden>
        <button type="button" class="tl-ib" id="task-archive-x" aria-label="Leave Archived" title="Leave Archived" hidden>${TL_ICON.x}</button>
        <div class="tl-pair ph-seg" id="task-archives" role="group" aria-label="Archive" hidden>${TASK_ARCHIVES.map(([k, label]) =>
          `<button type="button" data-archive="${k}">${label}</button>`).join('')}</div>
        <div class="tl-filters" role="group" aria-label="Filters"><span class="tl-chipset" id="task-chips"></span></div>
      </div>
      <div id="task-body" role="region" aria-label="Tasks"></div>
      <div class="tl-more" id="task-more" hidden><button type="button" class="ghost">More</button></div>
      <div class="pa-host" id="task-ask" hidden></div>
    </div><aside class="tl-rail" id="task-rail" hidden></aside></div>
    <div id="task-filter-pop" class="tl-pop" popover></div>`;
  const signal = state.abort.signal;
  const anchorOf = (ev: Event) => ev.currentTarget as HTMLElement;
  $('#task-scope').onclick = (ev: MouseEvent) => tasksScopeMenu(state, anchorOf(ev));
  $('#task-manager').onclick = () => pipelineRailToggle();
  // ⋯: each item does what its button on the row did (the shared header closes the menu on a pick).
  $('#task-mi-new').onclick = () => openTaskCreate('', {pipeline: state.pipeline || null});
  $('#task-mi-archive').onclick = () => tasksShow(state, {archive: state.archive ? '' : 'snoozed'});
  $('#task-mi-edit').onclick = () => { const p = tasksPipeline(state); if (p) TASK_PAGE_HOOKS.pipeline?.edit?.(p); };
  $('#task-mi-layout').onclick = (ev: MouseEvent) => {
    const b = eventElement(ev)?.closest<HTMLElement>('[data-layout]');
    if (b) tasksShow(state, {layout: b.dataset.layout === 'columns' ? 'columns' : 'list'});
  };
  $('#task-archive-x').onclick = () => tasksShow(state, {archive: ''});
  $('#task-archives').onclick = (ev: MouseEvent) => {
    const b = eventElement(ev)?.closest<HTMLElement>('[data-archive]');
    const want = TASK_ARCHIVES.find(([k]) => k === b?.dataset.archive)?.[0];
    if (want) tasksShow(state, {archive: want});
  };
  $('#task-play').onclick = () => void tasksReview(state);
  $('#task-more button').onclick = () => { state.stalled = false; void tasksMore(state); };
  const search = $('#task-q');
  // The phone's search opens over the row; its one ✕ empties it and closes it (the input keeps the focus until the click).
  const searchX = $('.tl-head .tl-search-x');
  for (const type of ['pointerdown', 'mousedown']) searchX.addEventListener(type, (ev: Event) => ev.preventDefault());
  searchX.onclick = () => {
    search.value = '';
    search.dispatchEvent(new Event('input'));
    search.blur();
  };
  search.oninput = () => {
    state.q = search.value;
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => tasksRender(state), 150);
  };
  search.onkeydown = (ev: KeyboardEvent) => {
    if (ev.key !== 'Escape') return;
    if (search.value) ev.stopPropagation(); else search.blur();
    search.value = state.q = '';
    tasksRender(state);
  };
  $('#task-filter').onclick = (ev: MouseEvent) => tasksFieldMenu(state, anchorOf(ev));
  $('#task-chips').onclick = (ev: MouseEvent) => {
    const drop = eventElement(ev)?.closest<HTMLElement>('[data-chip-drop]');
    const field = TASK_FILTER_NAMES.find(([k]) => k === (drop || eventElement(ev)?.closest<HTMLElement>('[data-chip-edit]'))?.dataset[drop ? 'chipDrop' : 'chipEdit'])?.[0];
    if (!field) return;
    if (drop) { delete state.filters[field]; tasksRender(state); $('#task-filter')?.focus(); }
    else tasksFilterMenu(state, field, eventElement(ev)?.closest<HTMLElement>('[data-chip-edit]'));
  };
  const pop = $('#task-filter-pop');
  pop.addEventListener('keydown', tasksMenuKeys);
  pop.addEventListener('toggle', (ev: Event & {newState?: string}) => {
    if (ev.newState !== 'closed') return;
    $('#task-scope')?.setAttribute('aria-expanded', 'false');
    const anchor = state.menuAnchor; state.menuAnchor = null;
    if (anchor?.isConnected && (pop.contains(document.activeElement) || document.activeElement === document.body)) anchor.focus();
  });
  const body = $('#task-body');
  body.addEventListener('click', (ev: MouseEvent) => void tasksBodyClick(state, ev), {signal});
  body.addEventListener('keydown', (ev: KeyboardEvent) => {
    const row = eventElement(ev)?.closest<HTMLElement>('[data-task], [data-need]');
    if ((ev.key === 'Enter' || ev.key === ' ') && row && row === eventElement(ev)) { ev.preventDefault(); row.click(); }
  }, {signal});
  document.addEventListener('keydown', (ev: KeyboardEvent) => {
    if (TASKS_ST !== state || ev.metaKey || ev.ctrlKey || ev.altKey || document.querySelector('dialog[open]')) return;
    if (eventElement(ev)?.closest('input, textarea, select, [contenteditable]')) return;
    if (ev.key === '/') { ev.preventDefault(); search.focus(); }
    else if (ev.key === 'c' && !tasksViewOnly()) { ev.preventDefault(); openTaskCreate('', {pipeline: state.pipeline || null}); }
  }, {signal});
  pullToRefresh($('#tasks-pane'), () => tasksLoad(state), {signal});
  tasksRender(state);
  void tasksLoad(state).then(async () => {
    // An old Decide link to one item (#/decide?item=<id>), or #/task/<id>: that item over the page.
    const want = DECIDE_WANT, stage = DECIDE_STAGE; DECIDE_WANT = DECIDE_STAGE = '';
    if (want && TASKS_ST === state) await decideWanted(want);
    else if (stage && TASKS_ST === state) await decideStageWanted(stage);
  });
  if (openId) taskModalOpen(openId);
}
// The page goes: its listeners, its pipeline view and its timers with it.
function tasksLeave() {
  const state = TASKS_ST;
  if (!state) return;
  state.abort.abort();
  clearTimeout(state.searchTimer);
  tasksRailOff();
  tasksAskOff();
  TASKS_ST = null;
}
/** A change of view: You or a pipeline, list or columns, the archive. Search and filters stay. */
function tasksShow(state: TasksPageState, change: Partial<Pick<TasksPageState, 'pipeline' | 'layout' | 'archive'>>) {
  Object.assign(state, change);
  if ('pipeline' in change) state.filters = {};
  tasksMenuClose();
  tasksURLWrite(state);
  state.tasks = []; state.base = [];
  state.more = false; state.edge = null; state.after = ''; state.stalled = false;
  state.loading = true;
  tasksKept(state);
  void tasksLoad(state);
  tasksRender(state);
}
// A view seen before draws its last read at once while the new one is out; the app's own Needs you list wins for You.
const tasksViewKey = (state: Pick<TasksPageState, 'pipeline' | 'archive'>) => state.pipeline + '|' + state.archive;
function tasksKept(state: TasksPageState) {
  const kept = S.lastRead.tasks;
  state.kept = kept?.view === tasksViewKey(state);
  if (!kept || !state.kept) return;
  state.tasks = kept.tasks.slice();
  if (!S.v2.needs) state.needs = kept.needs.slice();
}

// ---- reads
// A list fed by the live stream applies each event's task in place and never reads the whole list because an event came
// (Chris, Nov 3: whole-list reads per event cost 1.5 GB/h). Full reads: the page's first, a change of view, pull to
// refresh, and the stream's `reset`. Otherwise the 2-minute refresh, a tab back from sleep, a bulk change and this
// tab's own writes catch up with a changed_after delta from the cursor the last read gave.
/** The view's server filters: the archive's (of the pipeline in view, if any), or the pipeline's open tasks, snoozed
 * ones too (task-pipelines.ts draws them last in their stage); You is Needs you's. */
function tasksQuery(state: TasksPageState): TaskBriefQuery | null {
  const of = state.pipeline ? {pipeline: state.pipeline} : {};
  if (state.archive) return {status: state.archive === 'snoozed' ? 'open' : state.archive, snoozed: state.archive === 'snoozed' ? 'only' : 'hide', ...of, ...state.narrow};
  if (state.pipeline) return {status: 'open', snoozed: 'all', ...of, ...state.narrow};
  // A bot's read-only view has no Needs you (a person's list): its You is the bot's own open tasks.
  if (tasksViewOnly()) return {status: 'open', snoozed: 'all', owner: 'bot:' + (S.me?.id || ''), ...state.narrow};
  return null;
}
/** A bot's read-only view of the app (backend/app_view.py). */
const tasksViewOnly = (): boolean => !!S.me?.read_only_view;
/** A view's You: the bot's own open tasks (tasksQuery), kept live and paged like a pipeline's rows. */
const tasksOwnView = (state: TasksPageState): boolean => tasksViewOnly() && !state.pipeline && !state.archive;
/** The search and the filters as the server takes them (backend/task_match.py): the same meaning as taskFilterMatch, so
 * the server answers only the matches, a page at a time. */
function tasksNarrow(state: Pick<TasksPageState, 'q' | 'filters'>): TaskBriefQuery {
  const f = state.filters, out: TaskBriefQuery = {};
  const list = (v: string[] | undefined) => v?.length ? [...v] : undefined;
  if (state.q.trim()) out.search = state.q.trim();
  const owners = list(f.owner), turn = list(f.waiting_on), stages = list(f.stage), requesters = list(f.asked_by), labels = list(f.tag);
  if (owners) out.owners = owners;
  if (turn) out.turn = turn;
  if (stages) out.stages = stages;
  if (requesters) out.requesters = requesters;
  if (labels) out.labels = labels;
  if (f.due) out.due = f.due;
  if (f.late) out.late = true;
  if (f.pushed) out.pushed_past = true;
  return out;
}
/** '' when nothing narrows the view. */
const tasksNarrowKey = (narrow: TaskBriefQuery) => Object.keys(narrow).length ? taskBriefParams(narrow).toString() : '';
/** A page of the view: Done and Closed most recent first, the rest in queue order; the archive 200 at a time, a
 * pipeline 500 (the server's most), a search's or a filter's matches 100. */
const tasksPageQuery = (state: TasksPageState, query: TaskBriefQuery): TaskBriefQuery =>
  ({...query, sort: state.archive && state.archive !== 'snoozed' ? 'finished' : 'queue',
    limit: Object.keys(state.narrow).length ? 100 : state.archive ? 200 : 500});
/** The view's order, as its read asks for it: Done and Closed most recent first, the rest in queue order. */
const tasksOrder = (state: TasksPageState): TaskRowOrder => state.archive && state.archive !== 'snoozed' ? taskFinishedOrder : taskQueueOrder;
/** Whether a task, as the viewer reads it now, belongs in the view: tasksQuery's filters, client-side, the search and
 * filters it was read with too. */
function tasksViewHas(state: TasksPageState, t: TaskBriefV3): boolean {
  if (state.pipeline && t.pipeline_id !== state.pipeline) return false;
  if (state.readKey && !taskFilterMatch(t, state.q, state.filters)) return false;
  if (state.archive === 'snoozed') return t.status === 'open' && !!t.snoozed;
  if (state.archive) return t.status === state.archive;
  if (tasksOwnView(state)) return t.status === 'open' && t.owner_id === 'bot:' + (S.me?.id || '');
  return !!state.pipeline && t.status === 'open';
}
/** What the view needs: the pipelines (for the view button and the stage names), then You's Needs you, the archive, or
 * the pipeline's open tasks. */
async function tasksLoad(state: TasksPageState, pipelinesToo = true): Promise<void> {
  const ticket = taskFeedRead(state.feed);
  state.behind = false;
  state.narrow = tasksNarrow(state); state.readKey = tasksNarrowKey(state.narrow);
  const narrowed = !!state.readKey;
  let held: LiveTaskEvent[] | null = [];
  try {
    const query = tasksQuery(state);
    const [pipelines, rows] = await Promise.all([
      pipelinesToo ? tasksPipelinesRead().catch(() => state.pipelines) : state.pipelines,
      query ? taskBriefsPage(tasksPageQuery(state, query)) : needsYou(),
    ]);
    // Kept for the next visit even when the page was left before it landed; a search's matches are not the view.
    if (!narrowed || !query) S.lastRead.tasks = {view: tasksViewKey(state), needs: Array.isArray(rows) ? rows : [],
      tasks: Array.isArray(rows) ? [] : rows.tasks.filter(t => !state.pipeline || t.pipeline_id === state.pipeline)};
    if (TASKS_ST !== state) return;
    held = taskFeedLanded(state.feed, ticket, Array.isArray(rows) ? '' : rows.cursor);
    if (!held) return;
    state.pipelines = pipelines;
    if (state.pipeline && !pipelines.some(p => p.id === state.pipeline)) { state.pipeline = ''; tasksURLWrite(state); }
    state.stalled = false;
    if (Array.isArray(rows)) { state.needs = rows; state.cursor = ''; state.more = false; state.edge = null; state.after = ''; }
    else {
      state.tasks = rows.tasks.filter(t => !state.pipeline || t.pipeline_id === state.pipeline); state.cursor = rows.cursor;
      state.more = rows.more; state.edge = rows.tasks.at(-1) ?? null; state.after = rows.next;
      if (!narrowed) state.base = state.tasks.slice();
    }
    state.failed = '';
  } catch (e) {
    if (TASKS_ST !== state || ticket !== state.feed.ticket) return;
    taskFeedFailed(state.feed, ticket);
    state.failed = errorMessage(e, 'Tasks did not load.');
  }
  state.loading = false;
  // What came while the read was out goes over it: a task removed meanwhile stays removed.
  if (held?.length) tasksEventsPut(state, held);
  tasksRender(state);
  if (state.behind) void tasksCatchUp(state);
}
/** The view's next page (More), of the search's and filters' matches when they are set. It starts after the server's
 * sort key of the last row it gave (a keyset page), so a task put in or taken out live since skips or repeats nothing;
 * a row read twice goes in once. Nothing is re-read: the rows held stay, and the events that came meanwhile go over the
 * page as over any read. */
function tasksMore(state: TasksPageState): Promise<void> {
  if (state.paging) return state.paging;
  const run = async () => {
    if (!state.more || !state.edge || TASKS_ST !== state || state.loading) return;
    const query = tasksQuery(state), view = tasksViewKey(state), key = state.readKey;
    if (!query) return;
    let held: Awaited<ReturnType<typeof taskPageNext>>;
    try {
      held = await taskPageNext(state, state.tasks, tasksPageQuery(state, query), state.feed, row => tasksViewHas(state, row), tasksOrder(state),
        () => TASKS_ST === state && !state.loading && tasksViewKey(state) === view && state.readKey === key);
    } catch (e) { state.stalled = true; if (TASKS_ST === state) toast(errorMessage(e, 'More did not load.'), true); return; }
    if (held === 'left') return;
    if (!held) { state.stalled = true; return; }   // a newer read is out; More stays
    if (held.length) tasksEventsPut(state, held);
  };
  state.paging = run().finally(() => { state.paging = null; tasksRender(state); });
  tasksRender(state);
  return state.paging;
}
/** Catch up from the cursor: the changed rows that still match go in, the rest come out. A full read only when the
 * server says the cursor is too old (`reset`). You follows Needs you's own version (hub-v2.ts), already mended. */
async function tasksCatchUp(state: TasksPageState | null): Promise<void> {
  if (!state || TASKS_ST !== state) return;
  if (state.loading) { state.behind = true; return; }
  const query = tasksQuery(state);
  if (!query) { tasksNeedsLive(); return; }
  if (!state.cursor) { await tasksLoad(state); return; }
  try {
    const out = await taskRowsCatchUp(state.tasks, state.cursor, query, t => tasksViewHas(state, t), () => TASKS_ST === state && !state.loading, state.feed, tasksOrder(state));
    if (!out) return;
    if (out === 'reset') { await tasksLoad(state); return; }
    state.cursor = out.cursor;
    tasksRender(state);
    if (out.again) void tasksCatchUp(state);
  } catch { /* the next refresh tries again */ }
}
/** One task as the viewer reads it now (an event's, a delta's, a write's reply): in place, added, or out of the view. */
function tasksTaskPut(state: TasksPageState, given: TaskAny | null, id: string | null = null, draw = true): boolean {
  const t = given && taskListRow(given), key = String(t?.id ?? id ?? '');
  let moved = false;
  // You: Needs you says what is on it; a task's change only redraws the item that shows it.
  for (const it of state.needs) if (t && it.task && String(it.task.id) === key) { it.task = {...it.task, ...t} as typeof it.task; moved = true; }
  // A full task carries every list field.
  if (state.pipeline || state.archive || tasksOwnView(state)) moved = taskRowsPut(state.tasks, t as TaskBriefV3 | null, key, row => tasksViewHas(state, row), tasksOrder(state)) || moved;
  if (moved && draw) tasksRender(state);
  return moved;
}
/** Live task events (refresh.ts liveTasksApply): each carries the task as this viewer reads it, or says it is gone. While
 * a read is out they are held for it too; before the first read lands there is nothing to put them on. */
function tasksLiveApply(events: LiveTaskEvent[]): void {
  const state = TASKS_ST;
  if (!state || !isTasksRoute(S.route)) return;
  events = taskFeedEvents(state.feed, events);
  if (state.loading) return;
  tasksEventsPut(state, events);
}
function tasksEventsPut(state: TasksPageState, events: LiveTaskEvent[]): void {
  if (events.some(d => d.bulk)) { void tasksCatchUp(state); return; }
  let moved = false;
  for (const d of events) moved = tasksTaskPut(state, d.gone ? null : (d.task as TaskAny | null | undefined) ?? null, String(d.id ?? ''), false) || moved;
  if (moved) tasksRender(state);
}
/** Needs you changed (mended from the ids the event named, hub-v2.ts): You draws it; nothing is read. */
function tasksNeedsLive(): void {
  const state = TASKS_ST;
  if (!state || !isTasksRoute(S.route) || state.archive || state.loading) return;
  if (state.pipeline) {   // a stage's ▶ and its count; the phone's stage tabs keep where they were scrolled to
    const left = document.querySelector('.pv-tabs')?.scrollLeft || 0;
    tasksRender(state);
    const tabs = document.querySelector('.pv-tabs');
    if (tabs) tabs.scrollLeft = left;
    return;
  }
  state.needs = [...(S.v2.needs || [])] as NeedsEntry[];
  tasksRender(state);
}

// ---- drawing
const tasksPipeline = (state: TasksPageState) => state.pipelines.find(p => p.id === state.pipeline) || null;
const tasksStageName = (state: TasksPageState, id: string | null | undefined) => {
  for (const p of state.pipelines) {
    const s = p.stages.find(x => x.id === id) || (p.done?.id === id ? p.done : p.closed?.id === id ? p.closed : null);
    if (s) return s.name;
  }
  return '';
};
// ▶ Review's count: on a pipeline, what waits on the person in its stages; elsewhere all of Needs you.
function tasksReviewCount(state: TasksPageState): number {
  const p = tasksPipeline(state);
  if (!p) return decideCount();
  const counts = needsStageCounts();
  return p.stages.reduce((sum, s) => sum + (counts.get(s.id || '') || 0), 0);
}
// ▶ Review: on You, all of Needs you as before; on a pipeline, the phone's open stage first, else the first stage with
// items, never another pipeline's. Nothing to walk says so.
async function tasksReview(state: TasksPageState): Promise<void> {
  const p = tasksPipeline(state);
  if (!p) { if (!decideGoThrough()) toast('Nothing waits on you'); return; }
  const counts = needsStageCounts(), open = matchMedia(PHONE).matches ? pvStateFor(p).stage : '';
  const stage = open && counts.get(open) ? open : p.stages.find(s => counts.get(s.id || ''))?.id || '';
  if (!stage || !await decideStageOpen(stage)) toast(`Nothing waits on you in ${p.name}`);
}
function tasksHeadPaint(state: TasksPageState) {
  // A bot's read-only view has no Needs you: its scope is the bot's own open tasks, with their count.
  const p = tasksPipeline(state), view = tasksViewOnly(), own = view ? state.tasks.length : 0;
  const you = view ? S.me?.name || 'Bot' : 'You';
  const scope = $('#task-scope');
  const label = p ? `<span class="tl-scope-l">${esc(p.name)}</span>`
    : `<span class="tl-scope-l">${esc(you)}</span>${own ? `<span class="cnt tnum">${own}${state.more ? '+' : ''}</span>` : ''}`;
  if (scope.dataset.label !== label) { scope.dataset.label = label; scope.innerHTML = label + TL_ICON.caret; }
  scope.setAttribute('aria-label', p ? `Showing ${p.name}` : `Showing ${you}${own ? `, ${own}` : ''}`);
  // ▶ is always on the row and keeps one width: its count is the one count of what waits on the person.
  const n = tasksReviewCount(state), play = $('#task-play');
  play.hidden = view;                       // a view reviews nothing: no Needs you, no answers
  play.classList.toggle('none', !n);
  play.querySelector('.tl-review-n')!.textContent = n ? (n > 99 ? '99+' : String(n)) : '';
  play.setAttribute('aria-label', n ? `Review: ${n} wait${n === 1 ? 's' : ''} on you` : 'Review: nothing waits on you');
  // The manager's own face opens its panel, where a generic chat icon did.
  const manager = $('#task-manager');
  manager.hidden = !p || !!state.archive || !TASK_PAGE_HOOKS.pipeline?.rail || !pipelineRailFor(p);
  const who = p?.manager_id ? prActor(p.manager_id) : '', face = who ? actorFace(who, 20) : TL_ICON.chat;
  if (manager.dataset.face !== face) {      // drawn again only when the face changes (its name or picture arrives)
    manager.dataset.face = face;
    manager.innerHTML = face;
    const words = who ? `Chat with ${actorLabel(who)}` : 'Manager';
    manager.setAttribute('aria-label', words); manager.title = words;
  }
  // ⋯
  const archive = $('#task-mi-archive');
  archive.textContent = state.archive ? 'Leave Archived' : 'Archived';
  $('#task-mi-edit').hidden = !p || p.may_change === false || !TASK_PAGE_HOOKS.pipeline?.edit || !!state.archive;
  $('#task-mi-layout').hidden = !p || !!state.archive || matchMedia(PHONE).matches;
  for (const b of document.querySelectorAll<HTMLElement>('#task-mi-layout [data-layout]')) b.setAttribute('aria-checked', String(b.dataset.layout === state.layout));
  $('#task-archive-x').hidden = !state.archive;
  const archives = $('#task-archives');
  archives.hidden = !state.archive;
  for (const b of document.querySelectorAll<HTMLElement>('#task-archives [data-archive]')) b.setAttribute('aria-pressed', String(b.dataset.archive === state.archive));
  const on = TASK_FILTER_NAMES.filter(([k]) => tasksFilterOn(state.filters, k));
  $('#task-chips').innerHTML = on.map(([k, name]) => {
    const words = tasksFilterWords(state, k), shown = words.length > 2 ? `${words[0]} +${words.length - 1}` : words.join(', ');
    return `<span class="tl-chip" data-chip="${k}"><button type="button" class="tl-chip-main" data-chip-edit="${k}" aria-haspopup="true" title="${esc(`${name}: ${words.join(', ')}`)}"><span class="k">${esc(name)}</span>${shown ? `<span class="v">${esc(shown)}</span>` : ''}</button><button type="button" class="tl-chip-x" data-chip-drop="${k}" aria-label="Remove ${esc(name)}">${TL_ICON.x}</button></span>`;
  }).join('');
  const badge = $('#task-filter .tl-fbadge');
  badge.hidden = !on.length; badge.textContent = String(on.length);
  $('#task-bar').hidden = !on.length && !state.archive;
}
function tasksRender(state: TasksPageState | null) {
  const body = $('#task-body');
  if (!body || !state || TASKS_ST !== state) return;
  tasksHeadPaint(state);
  needsBadgePaint();
  // A search or a filter is the server's: a change of either reads its first page of matches (held rows, matched here,
  // show meanwhile); More reads the next page.
  if (tasksQuery(state) && tasksNarrowKey(tasksNarrow(state)) !== state.readKey) {
    state.loading = true; state.kept = true;
    void tasksLoad(state, false);
  }
  const more = $('#task-more');
  if (more) {
    more.hidden = !state.more || !!state.failed || state.loading;
    ($('button', more) as HTMLButtonElement).disabled = !!state.paging;
  }
  body.dataset.view = state.archive ? 'archive' : state.pipeline ? 'pipeline' : 'you';
  const p = state.archive ? null : tasksPipeline(state), hooks = TASK_PAGE_HOOKS.pipeline;
  if (p && hooks?.rail) hooks.rail($('#task-rail'), p); else tasksRailOff();
  if (p && hooks && !state.failed && !(state.loading && !state.kept && !state.tasks.length)) {
    // The view keeps what is being done in it (a drag, a phone's stage) and draws itself again into the root it is bound to.
    const html = hooks.html(p, state.tasks, {layout: state.layout, q: state.q, filters: state.filters, phone: matchMedia(PHONE).matches});
    if (body.dataset.html !== html) { body.dataset.html = html; body.innerHTML = html; }
    hooks.bind?.(body, p);
    hooks.ask?.($('#task-ask'), p, matchMedia(PHONE).matches);
    return;
  }
  tasksAskOff();
  const html = state.failed ? `<p class="err" role="alert">${esc(state.failed)} <button type="button" class="linkish" data-reload>Try again</button></p>`
    : state.loading && !state.kept && !state.needs.length && !state.tasks.length ? skelRows(6)
    : state.archive ? tasksArchiveHTML(state)
    : state.pipeline ? tasksPipelineHTML(state)
    : tasksViewOnly() ? tasksOwnHTML(state)
    : tasksYouHTML(state);
  if (body.dataset.html !== html) { body.dataset.html = html; body.innerHTML = html; }
}
/** No Ask bar away from a pipeline's board (pipeline-ask.ts). */
function tasksAskOff() {
  if (typeof pipelineAskLeave === 'function') pipelineAskLeave();
}
/** The rail's host is emptied and hidden away from a pipeline (and its chat goes, pipeline-rail.ts). */
function tasksRailOff() {
  const rail = document.getElementById('task-rail');
  if (rail && !rail.hidden) { rail.hidden = true; rail.replaceChildren(); }
  rail?.closest('.tasks-page')?.classList.remove('has-rail');
  if (typeof pipelineRailLeave === 'function') pipelineRailLeave();
}
const tasksGroupHTML = (name: string, rows: string[]) => !rows.length ? ''
  : `<section class="tk-section"><h2 class="tk-group">${esc(name)} <span class="n">${rows.length}</span></h2>${rows.join('')}</section>`;
const tasksNone = (state: TasksPageState) => `<p class="empty muted">${state.q || taskFiltersOn(state.filters) ? 'No matches' : 'Nothing here'}</p>`;
/** Which group of You an item is in: Answer (questions, mentions, stuck tasks), Review (OKs of a page or a draft) or Do. */
function needsGroup(it: NeedsEntry): 'answer' | 'review' | 'do' {
  if (it.kind === 'ask') return it.ask?.kind === 'answer' ? 'answer' : 'review';
  if (it.kind === 'mention') return 'answer';
  if (it.kind === 'task' && it.task?.turn?.state === 'paused') return 'answer';
  return 'do';
}
function needsMatch(state: TasksPageState, it: NeedsEntry): boolean {
  if (it.task) return taskFilterMatch({...it.task, title: `${it.line} ${it.task.title}`}, state.q, state.filters);
  const words = state.q.toLowerCase().split(/\s+/).filter(Boolean), line = it.line.toLowerCase();
  return !taskFiltersOn(state.filters) && words.every(w => line.includes(w));
}
/** A Needs you row's first line: its task's title; a Paused cause or a mention outside a task by its own line. */
function needsTitle(it: NeedsEntry): string {
  return (it.kind !== 'paused' && it.task?.title) || it.line || 'Untitled';
}
/** Its second line: the question, under an Answer row's title (its buttons answer it); other asks keep it as the tooltip. */
function needsAsk(it: NeedsEntry): string {
  return needsGroup(it) === 'answer' && needsTitle(it) !== it.line ? it.line : '';
}
/** One row of You: its task's title (needsTitle), an Answer row's question under it; a question with two choices answers in place (S1.8). */
function needsRowHTML(it: NeedsEntry): string {
  const ask = it.ask, two = it.kind === 'ask' && ask?.kind === 'answer' && ask.choices?.length === 2 && ask.may.includes('answer');
  const after = two ? ask!.choices!.map((c, i) => `<button type="button" class="${i ? 'ghost' : 'primary'} sm" data-need-answer="${esc(c)}">${esc(c)}</button>`).join('') : '';
  const who = it.kind === 'mention' ? it.from_id || '' : ask?.asked_by || it.task?.owner_id || (it.bots?.[0] ? 'bot:' + it.bots[0] : '');
  const tip = it.kind !== 'task' && needsTitle(it) !== it.line && !needsAsk(it) ? ` title="${esc(it.line)}"` : '';
  return `<div class="tk-row" data-need="${esc(it.id)}" role="button" tabindex="0"${tip}>`
    + `<span class="tk-two"><span class="tk-title">${esc(needsTitle(it))}</span>${needsAsk(it) ? `<span class="tk-ask">${esc(needsAsk(it))}</span>` : ''}</span>${it.task ? lateTagHTML(it.task) : ''}`
    + (after ? `<span class="tk-after">${after}</span>` : '')
    + `<span class="tk-who">${who ? actorFace(who, 18) : ''}</span><span class="tk-age">${esc(ageShort(it.since))}</span></div>`;
}
/** Needs you by stage: a chip per stage with items for the person, in the pipelines' order, then Other; each opens that
    stage's review (decide.ts). None until a stage of a pipeline in view has items. */
function tasksStageChipsHTML(state: TasksPageState): string {
  const counts = needsStageCounts(), total = decideCount();
  const chips: [string, string, number][] = [];
  for (const p of state.pipelines) for (const st of p.stages) if (st.id && counts.get(st.id)) chips.push([st.id, st.name, counts.get(st.id)!]);
  const known = new Set(chips.map(c => c[0]));
  // A stage of no pipeline in view (retired, or not readable here) is counted under Other.
  const other = total - chips.reduce((n, c) => n + c[2], 0);
  if (other > 0) chips.push([DEC_OTHER, 'Other', other]);
  if (!known.size) return '';
  return `<div class="tk-stages" role="group" aria-label="Needs you by stage">${chips.map(([id, name, n]) =>
    `<button type="button" class="tk-stage" data-need-stage="${esc(id)}" aria-label="${esc(`Review ${name}: ${n}`)}"><span>${esc(name)}</span><span class="n tnum">${n}</span></button>`).join('')}</div>`;
}
function tasksYouHTML(state: TasksPageState): string {
  const shown = state.needs.filter(it => needsMatch(state, it));
  if (!shown.length) return tasksNone(state);
  const of = (g: string) => shown.filter(it => needsGroup(it) === g).map(needsRowHTML);
  const chips = state.q.trim() || taskFiltersOn(state.filters) ? '' : tasksStageChipsHTML(state);
  return chips + tasksGroupHTML('Answer', of('answer')) + tasksGroupHTML('Review', of('review')) + tasksGroupHTML('Do', of('do'));
}
/** A bot's read-only view in place of You: a line that Needs you is a person's list, then the bot's open tasks by stage,
    in the pipelines' order, then Other. */
function tasksOwnHTML(state: TasksPageState): string {
  const name = S.me?.name || 'This bot';
  const note = `<p class="muted tk-view-note">Needs you is a person's list. These are ${esc(name)}'s open tasks.</p>`;
  const shown = state.tasks.filter(t => taskFilterMatch(t, state.q, state.filters));
  if (!shown.length) return note + tasksNone(state);
  const known = new Set<string>(), groups: string[] = [];
  for (const p of state.pipelines) for (const st of p.stages) {
    if (st.id) known.add(st.id);
    groups.push(tasksGroupHTML(st.name, shown.filter(t => t.stage_id === st.id).map(t => taskRowHTML(t))));
  }
  return note + groups.join('') + tasksGroupHTML('Other', shown.filter(t => !known.has(t.stage_id || '')).map(t => taskRowHTML(t)));
}
/** A pipeline's open tasks by stage, in the pipeline's order, until task-pipelines.ts draws it. */
function tasksPipelineHTML(state: TasksPageState): string {
  const p = tasksPipeline(state), shown = state.tasks.filter(t => taskFilterMatch(t, state.q, state.filters));
  if (!p || !shown.length) return tasksNone(state);
  const known = new Set(p.stages.map(s => s.id));
  return p.stages.map(s => tasksGroupHTML(s.name, shown.filter(t => t.stage_id === s.id).map(t => taskRowHTML(t)))).join('')
    + tasksGroupHTML('Other', shown.filter(t => !known.has(t.stage_id || undefined)).map(t => taskRowHTML(t)));
}
function tasksArchiveHTML(state: TasksPageState): string {
  const shown = state.tasks.filter(t => taskFilterMatch(t, state.q, state.filters));
  return shown.length ? shown.map(t => taskRowHTML(t, {look: 'archive'})).join('') : tasksNone(state);
}

// ---- clicks
async function tasksBodyClick(state: TasksPageState, ev: MouseEvent) {
  const el = eventElement(ev);
  if (el?.closest('[data-reload]')) { state.failed = ''; state.loading = true; tasksRender(state); void tasksLoad(state); return; }
  const chip = el?.closest<HTMLElement>('[data-need-stage]');
  if (chip) { void decideStageOpen(chip.dataset.needStage || ''); return; }
  const answer = el?.closest<HTMLButtonElement>('[data-need-answer]');
  const row = el?.closest<HTMLElement>('[data-need]');
  const it = row ? state.needs.find(n => n.id === row.dataset.need) : null;
  if (answer && it?.task_id && it.requirement_id) {
    ev.stopPropagation();
    answer.disabled = true;
    try {
      await taskAnswer(it.task_id, it.requirement_id, answer.dataset.needAnswer || '', it.task?.version ?? null);
      state.needs = state.needs.filter(n => n !== it);
      tasksRender(state);
      void v2Refresh();
    } catch (e) { answer.disabled = false; toast(errorMessage(e, 'Not answered.')); }
    return;
  }
  if (it) { decideOpen(it.id); return; }
  const task = el?.closest<HTMLElement>('[data-task]')?.dataset.task;
  // On a pipeline's board on a phone a card opens at its conversation, its box asking the task's owner.
  const board = !!state.pipeline && !state.archive && matchMedia(PHONE).matches;
  if (task) void taskModalOpen(task, undefined, board ? {thread: true, ask: true} : {});
}

// ---- the view button, filters
function tasksScopeMenu(state: TasksPageState, anchor: HTMLElement) {
  const item = (attr: string, label: string, on: boolean, count = 0) => `<button type="button" role="menuitemradio" aria-checked="${on}" class="tl-mi" ${attr}><span class="tl-mi-t">${esc(label)}</span>${count ? `<span class="tl-mi-n tnum">${count}</span>` : ''}${on ? '<span class="tl-mi-on" aria-hidden="true">✓</span>' : ''}</button>`;
  tasksMenu(state, anchor, `<div class="tl-menu" role="menu" aria-label="Show">
    ${item('data-scope=""', tasksViewOnly() ? S.me?.name || 'Bot' : 'You', !state.pipeline, tasksViewOnly() ? state.tasks.length : state.needs.length)}<div class="tl-menu-sep" role="separator"></div>
    ${state.pipelines.map(p => item(`data-scope="${esc(p.id)}"`, p.name, state.pipeline === p.id)).join('')}</div>`, pop => {
    pop.onclick = ev => {
      const b = eventElement(ev)?.closest<HTMLElement>('[data-scope]');
      if (!b) return;
      pop.hidePopover();
      const id = b.dataset.scope || '';
      if (id !== state.pipeline) tasksShow(state, {pipeline: id});
      $('#task-scope')?.focus();
    };
  });
  anchor.setAttribute('aria-expanded', 'true');
}
const tasksFilterOn = (f: TaskFilters, k: keyof TaskFilters) => { const v = f[k]; return Array.isArray(v) ? v.length > 0 : !!v; };
/** The rows a filter's values come from: what the view lists, and what it listed with no search or filter. */
const tasksFilterRows = (state: TasksPageState): TaskBriefV3[] =>
  state.archive || state.pipeline || tasksOwnView(state) ? [...state.base, ...state.tasks] : state.needs.map(it => it.task).filter((t): t is TaskBriefV3 => !!t);
type TaskFilterValue = {value: string; label: string; face?: string};
function tasksFilterValues(state: TasksPageState, k: keyof TaskFilters): TaskFilterValue[] {
  if (k === 'due') return [{value: 'overdue', label: 'Overdue'}, {value: 'week', label: 'This week'}, {value: 'any', label: 'Has a date'}];
  if (k === 'late') return [{value: '1', label: 'Late'}];
  const rows = tasksFilterRows(state);
  if (k === 'pushed') return rows.some(t => t.pushed_past) ? [{value: '1', label: 'Pushed past'}] : [];
  if (k === 'tag') {
    // List rows carry their labels (GET /api/v2/tasks?brief=1), though TaskBrief does not name them.
    const tags = new Set(rows.flatMap(t => (t as {labels?: string[]}).labels || []));
    return [...tags].map(v => ({value: v, label: v})).sort((a, b) => a.label.localeCompare(b.label));
  }
  const of = (t: TaskBriefV3): string | null => k === 'owner' ? t.owner_id : k === 'waiting_on' ? t.turn?.who ?? null
    : k === 'stage' ? t.stage_id : k === 'asked_by' ? t.requester_id : null;
  const values = [...new Set(rows.map(of).filter((v): v is string => !!v))];
  return values.map(v => k === 'stage' ? {value: v, label: tasksStageName(state, v) || v}
    : {value: v, label: actorLabel(v), face: /^(bot|human):/.test(v) ? actorFace(v, 16) : ''})
    .sort((a, b) => a.label.localeCompare(b.label));
}
function tasksFilterWords(state: TasksPageState, k: keyof TaskFilters): string[] {
  const v = state.filters[k];
  if (!Array.isArray(v)) return k === 'due' && typeof v === 'string' ? [tasksFilterValues(state, 'due').find(o => o.value === v)?.label || v] : [];
  const known = new Map(tasksFilterValues(state, k).map(o => [o.value, o.label]));
  return v.map(x => known.get(x) || (k === 'stage' ? tasksStageName(state, x) : actorLabel(x)) || x);
}
/** The filter button: the fields that have values in this view; a field opens its values in the same menu. */
function tasksFieldMenu(state: TasksPageState, anchor: HTMLElement) {
  const fields = TASK_FILTER_NAMES.filter(([k]) => tasksFilterValues(state, k).length);
  tasksMenu(state, anchor, `<div class="tl-menu" role="menu" aria-label="Filter">
    ${fields.map(([k, name]) => `<button type="button" role="menuitem" class="tl-mi" data-pick-field="${k}">${esc(name)}</button>`).join('') || '<div class="tl-menu-none">Nothing to choose</div>'}</div>`, pop => {
    pop.onclick = ev => {
      const k = fields.find(([f]) => f === eventElement(ev)?.closest<HTMLElement>('[data-pick-field]')?.dataset.pickField)?.[0];
      if (k) tasksFilterMenu(state, k, anchor, false);
    };
  });
}
function tasksFilterMenu(state: TasksPageState, k: keyof TaskFilters, anchor: HTMLElement | null | undefined, toggle = true) {
  const name = TASK_FILTER_NAMES.find(([f]) => f === k)?.[1] || k;
  const opts = tasksFilterValues(state, k), one = k === 'due' || k === 'late' || k === 'pushed';
  const cur = state.filters[k];
  const chosen = new Set(Array.isArray(cur) ? cur : cur === true ? ['1'] : typeof cur === 'string' ? [cur] : []);
  const html = `<div class="tl-menu" role="group" aria-label="${esc(name)}">
    <div class="tl-menu-h" aria-hidden="true">${esc(name)}</div>
    ${opts.length > 7 ? `<input type="search" class="tl-menu-q" placeholder="Find" aria-label="Find ${esc(name.toLowerCase())}" autocomplete="off" spellcheck="false">` : ''}
    <div class="tl-menu-list">${opts.map(o => `<label class="tl-opt" data-label="${esc(o.label.toLowerCase())}"><input type="${one ? 'radio' : 'checkbox'}" name="tl-${k}" value="${esc(o.value)}"${chosen.has(o.value) ? ' checked' : ''}>${o.face ? `<span class="tl-opt-face">${o.face}</span>` : ''}<span>${esc(o.label)}</span></label>`).join('')}</div>
  </div>`;
  tasksMenu(state, anchor, html, pop => {
    const q = pop.querySelector<HTMLInputElement>('.tl-menu-q');
    if (q) q.oninput = () => { for (const o of pop.querySelectorAll<HTMLElement>('.tl-opt')) o.hidden = !(o.dataset.label || '').includes(q.value.trim().toLowerCase()); };
    pop.onchange = () => {
      const picked = [...pop.querySelectorAll<HTMLInputElement>(`input[name="tl-${k}"]:checked`)].map(i => i.value);
      if (k === 'late' || k === 'pushed') state.filters[k] = picked.length > 0;
      else if (k === 'due') { const v = picked[0]; if (v === 'overdue' || v === 'week' || v === 'any') state.filters.due = v; else delete state.filters.due; }
      else state.filters[k] = picked;
      tasksRender(state);
      state.menuAnchor = $(`[data-chip-edit="${k}"]`) || $('#task-filter');
      tasksMenuPlace(pop, state.menuAnchor);
    };
  }, toggle);
  state.menuField = k;
}

// ---- New task (S6): a title, the details, and one row: who it is for, the pipeline, Create. No due date here; it is
// added on the task. A new task starts in its pipeline's first stage. `parent` files it from that task.
function taskCreateModal() {
  let d = $('#task-create');
  if (d) return d;
  d = document.createElement('dialog');
  d.id = 'task-create'; d.className = 'tmodal';
  d.addEventListener('click', (ev: MouseEvent) => { if (ev.target === d) d.close(); });
  document.body.appendChild(d);
  return d;
}
interface TaskCreateOptions {
  parent?: {id: string; title?: string | null; private?: boolean | null} | null | undefined;
  labels?: unknown[] | undefined;
  /** The pipeline it goes in, by id: General when none is named. */
  pipeline?: string | null | undefined;
}
// What was typed into New task and not yet created: closing the modal (the ✕, a tap outside, the phone's Back) keeps
// it for the next open; a created task clears it.
let TASK_CREATE_DRAFT = {title: '', body: ''};
function openTaskCreate(owner = '', opts: TaskCreateOptions = {}) {
  const d = taskCreateModal();
  const parent = opts.parent || null;
  const forWho = owner && !owner.includes(':') && botRow(owner) ? 'bot:' + owner : owner;
  d.innerHTML = `<div class="tmodal-head"><span class="spacer"></span>
      <button class="ghost tmodal-x" type="button" data-modal-close aria-label="Close">✕</button></div>
    <div class="tmodal-body">
      <form class="task tc-form" id="task-create-form">
        <input type="text" name="title" required aria-label="Title" placeholder="Title" maxlength="300">
        <textarea name="body" required aria-label="Details" placeholder="Details" rows="6"></textarea>
        <div class="tc-row">
          <div class="tc-pick"><span aria-hidden="true">For:</span>${giveToFieldHTML({name: 'owner', label: 'For', placeholder: 'Who is this for?', selected: forWho, task: parent})}</div>
          <label class="tc-pick"><select name="pipeline" aria-label="Pipeline"><option value="">General</option></select></label>
          <span class="spacer"></span>
          <button class="primary" type="submit">Create</button>
        </div>
        <p class="err" id="task-create-msg" role="alert" hidden></p>
      </form>
    </div>`;
  $('[data-modal-close]', d).onclick = () => d.close();
  const form: HTMLFormElement = $('#task-create-form', d);
  const field = <T extends Element>(name: string) => form.elements.namedItem(name) as T;
  field<HTMLInputElement>('title').value = TASK_CREATE_DRAFT.title;
  field<HTMLTextAreaElement>('body').value = TASK_CREATE_DRAFT.body;
  form.oninput = () => { TASK_CREATE_DRAFT = {title: field<HTMLInputElement>('title').value, body: field<HTMLTextAreaElement>('body').value}; };
  const pick = field<HTMLSelectElement>('pipeline');
  const fill = (list: PipelineRec[]) => {
    const want = opts.pipeline || '';
    pick.innerHTML = list.map(p => `<option value="${esc(p.id)}"${p.id === want || (!want && p.preset === 'general') ? ' selected' : ''}>${esc(p.name)}</option>`).join('')
      || '<option value="">General</option>';
  };
  if (TASK_PIPELINES) fill(TASK_PIPELINES);
  void tasksPipelinesRead().then(list => { if (d.open && form.isConnected) fill(list); }).catch(() => {});
  form.onsubmit = async (ev: SubmitEvent) => {
    ev.preventDefault();
    const btn = form.querySelector<HTMLButtonElement>('[type=submit]'), msg = $('#task-create-msg', d);
    const title = field<HTMLInputElement>('title').value.trim(), body = field<HTMLTextAreaElement>('body').value.trim();
    const who = field<HTMLInputElement>('owner').value;
    if (title && body && !who) { giveToFieldOpen(form.querySelector<HTMLButtonElement>('button[data-give-to]')!); return; }   // the hidden field is never "required"
    if (!title || !who || !body || !btn) return;
    btn.disabled = true; msg.hidden = true;
    const labels = (opts.labels || []).map(String).filter(Boolean);
    try {
      const made = await taskCreate({title, body, owner: who.includes(':') ? who : 'bot:' + who,
        ...(pick.value ? {pipeline: pick.value} : {}), ...(parent ? {parent: parent.id} : {}),
        ...(labels.length ? {labels} : {}), ...(parent?.private ? {private: true} : {})});
      TASK_CREATE_DRAFT = {title: '', body: ''};
      d.close();
      if (TASKS_ST) void tasksCatchUp(TASKS_ST);
      await v2Refresh();
      if (made?.id) taskModalOpen(made.id);
    } catch (e) { msg.textContent = errorMessage(e, ''); msg.hidden = false; btn.disabled = false; }
  };
  if (!d.open) d.showModal();
  formFocus(d);
}
function formFocus(root: ParentNode) {
  root.querySelector<HTMLElement>('input[name=title], textarea, select')?.focus();
}
