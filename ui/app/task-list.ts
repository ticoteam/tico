/* ui/app/task-list.ts — The Tasks list: one line per task, groups, filter chips (kept in the URL) and pins. Its toolbar
   and menus are in task-list-menus.ts, multi-select with bulk changes in task-list-bulk.ts, and the side peek, the
   cursor and the keyboard in task-list-peek.ts.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// What the list adds to the Tasks page's state (TaskState, tasks.ts): the address it opened with, the ⋯ menu's Select
// mode, the open menu, the tasks by id, the bulk changes (task-list-bulk.ts), the board's focus and how the peek opened.
interface TaskState {
  urlType?: string; urlView?: string; whoFixed?: boolean;
  layout?: string; selectMode?: boolean; tags?: import('../types/api').Tag[];
  byId?: Map<string, TaskRow>; byIdFor?: TaskRow[]; byIdNeeds?: NeedsItem[];
  menuAnchor?: HTMLElement | null | undefined; menuField?: string;
  bulkScope?: TaskBulkScope | null; bulkUndo?: TaskBulkUndo | null; bulkBusy?: boolean; bulkTimer?: number;
  activeComplete?: boolean; boardFocus?: {key: string; keys: Set<string>} | null; peekVia?: string;
}
// A row's parent as an older row carried it (taskParentId reads the relation; this is the fallback for its title).
interface TaskRow { parent?: {id: string; title: string; owner_id?: string} | null; }
// The chips' values: TaskState.filters holds them as unknown[]; each value is a string (an actor, a tag, a word).
type TaskChips = Record<string, string[] | undefined>;
// One value a chip can take (tasksFilterOptions): its value, its words, and a face for an actor.
interface TaskFilterOption { value: string; label: string; face?: string; }
// A pinned view: its address and its name (TASK_PINS).
interface TaskPin { hash: string; name: string; }
// What a group's + button opens a new task with: an owner, a tag, or a parent.
interface TaskGroupCreate { owner_id?: string; labels?: string[]; parent?: {id: string; title: string; owner_id?: string | undefined}; }
// One group of the list (tasksGroupsFor): its heading, its rows, and what its heading offers.
interface TaskGroup {
  key: string; label: string; items: TaskItem[]; icon?: string; actor?: string; chat?: string; chatName?: string;
  last?: boolean; folded?: boolean; create?: TaskGroupCreate | null;
}
// What every row of one group is drawn with (taskListRowHTML).
// An approval or stale check that leads Needs you (needsExtras), drawn among the tasks when grouped by kind.
type TaskNeedExtra = ReturnType<typeof needsExtras>[number];
interface TaskRowCtx { state: TaskState; kids: Map<string, TaskItem[]>; done: boolean; by: string; select: boolean; }

// ---- filters: chips over the loaded tasks, kept in the address (#/tasks?owner=bot:eng&tag=bug) so a view can be shared
const TASK_FILTER_FIELDS: [string, string][] = [['owner', 'Owner'], ['team', 'Team'], ['tag', 'Tag'], ['repo', 'Repo'], ['pr', 'Has PR'], ['due', 'Due'], ['asked', 'Asked by']];
const TASK_FILTER_WORDS: Record<string, Record<string, string>> = {pr: {yes: 'Yes', no: 'No'}, due: {overdue: 'Overdue', week: 'Next 7 days', any: 'Has a date', none: 'No date'}};
const TASK_GROUPS: [string, string][] = [['need', 'Kind'], ['status', 'Status'], ['owner', 'Owner'], ['team', 'Team'], ['parent', 'Parent task'], ['tag', 'Tag'], ['requester', 'Asked by']];
const TASK_GROUP_DEFAULT: Record<string, string> & {list: string; foryou: string} = {list: 'status', foryou: 'need'};
// Kind (what an item takes: Answer, Pay, Connect, Review, Do) groups your own You list only: it follows the server's
// queue, which is yours alone.
const taskGroupsFor = (view: string, state?: TaskState | null) => TASK_GROUPS.filter(([k]) => k !== 'need' || (view === 'foryou' && (!state || tasksNeedsQueue(state, view))));
const tasksEmptyFilters = () => Object.fromEntries(TASK_FILTER_FIELDS.map(([k]) => [k, []]));
const isTasksRoute = (r: string | null | undefined) => [TASKS, BOARD, ISSUES].includes(String(r || '').split('?')[0]!);
const PHONE = '(max-width:760px)';
// Small inline icons (the icon font is a fixed subset; these never need it).
const TL_ICON = {
  chev: '<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false"><path d="M4.5 3l3 3-3 3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  plus: '<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false"><path d="M6 2v8M2 6h8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  x: '<svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true" focusable="false"><path d="M3 3l6 6M9 3l-6 6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  filter: filterIcon(13),
  search: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><circle cx="6.2" cy="6.2" r="4.2" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M9.3 9.3L12.3 12.3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  chat: '<svg viewBox="0 0 14 14" width="13" height="13" aria-hidden="true" focusable="false"><path d="M2.5 3.5h9v6h-5l-2.5 2v-2h-1.5z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>',
  full: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><path d="M8.5 2.5h3v3M5.5 11.5h-3v-3M11.5 2.5L8 6M2.5 11.5L6 8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  up: '<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false"><path d="M3 7.5l3-3 3 3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  down: '<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false"><path d="M3 4.5l3 3 3-3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  team: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><circle cx="5" cy="5" r="2" fill="none" stroke="currentColor" stroke-width="1.3"/><circle cx="9.6" cy="5.6" r="1.6" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M1.8 11.2c.5-2 1.7-3 3.2-3s2.7 1 3.2 3M8.4 8.6c1.6-.4 3 .5 3.6 2.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
  note: '<svg viewBox="0 0 14 14" width="13" height="13" aria-hidden="true" focusable="false"><path d="M3 2.5h8v6.5l-2.5 2.5H3z M8.5 11.5V9h2.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
  group: '<svg viewBox="0 0 14 14" width="13" height="13" aria-hidden="true" focusable="false"><path d="M2.5 3.5h9M4.5 7h7M4.5 10.5h7M2.5 7h.01M2.5 10.5h.01" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  chart: '<svg viewBox="0 0 14 14" width="13" height="13" aria-hidden="true" focusable="false"><path d="M2 12h10M4 10V7M7 10V3.5M10 10V5.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  dots: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><circle cx="3" cy="7" r="1.25" fill="currentColor"/><circle cx="7" cy="7" r="1.25" fill="currentColor"/><circle cx="11" cy="7" r="1.25" fill="currentColor"/></svg>',
  pin: '<svg viewBox="0 0 14 14" width="13" height="13" aria-hidden="true" focusable="false"><path d="M5 1.8h4M5.6 1.8v3.6L3.6 8h6.8L8.4 5.4V1.8M7 8v4.2" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  caret: '<svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true" focusable="false"><path d="M3 4.5l3 3 3-3" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  list: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><path d="M2.5 3.5h9M2.5 7h9M2.5 10.5h9" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  board: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><rect x="1.8" y="2.2" width="2.9" height="9.6" rx="1" fill="none" stroke="currentColor" stroke-width="1.3"/><rect x="5.55" y="2.2" width="2.9" height="6.6" rx="1" fill="none" stroke="currentColor" stroke-width="1.3"/><rect x="9.3" y="2.2" width="2.9" height="8.1" rx="1" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>',
  screen: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><rect x="1.8" y="2.2" width="10.4" height="7.6" rx="1.4" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M5.2 12.2h3.6M7 9.8v2.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
  archive: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><rect x="1.8" y="2.3" width="10.4" height="3" rx="1" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M2.8 5.3v5.4a1 1 0 0 0 1 1h6.4a1 1 0 0 0 1-1V5.3M5.6 7.8h2.8" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  all: '<svg viewBox="0 0 14 14" width="13" height="13" aria-hidden="true" focusable="false"><rect x="2" y="2" width="10" height="10" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M4.6 7.2l1.7 1.6 3.2-3.4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};
// A bot in the address is `bot:<id>`; an old link's slug (or a bare name) is moved to its id.
const tasksBotActor = (v: string) => {
  if (v === 'me' || v === 'all' || (v.includes(':') && !v.startsWith('bot:'))) return v;
  const ref = v.replace(/^bot:/, '');
  return 'bot:' + (botByRef(ref)?.id || ref);
};
function tasksFiltersFromURL(state: TaskState) {
  const query = new URLSearchParams(String(location.hash).split('?')[1] || '');
  state.filters = tasksEmptyFilters();
  for (const k of Object.keys(state.filters)) {
    const raw = query.get(k);
    if (raw) state.filters[k] = [...new Set(raw.split(',').map(s => s.trim()).filter(Boolean).map(v => k === 'owner' || k === 'asked' ? tasksBotActor(v) : v))];
  }
  state.urlType = query.get('type') || '';
  state.urlView = query.get('view') || '';
  // An address with any of these says whose tasks the first tab shows (Me when it names nobody).
  if ([...query.keys()].length) {
    state.who = tasksBotActor(query.get('who') || 'me');
    state.side = query.get('side') === 'asked' ? 'asked' : 'plate';
    state.whoFixed = true;
  }
  return query;
}
function tasksActiveFilterCount(state: TaskState) {
  return Object.values(state.filters || {}).filter(v => v.length).length;
}
// The address follows the filters without reloading the page (no hashchange, no history entry). With any filter in
// it, it names the view too, so a shared address opens the same tab whatever route it started from.
function tasksQuery(state: TaskState) {
  const query = new URLSearchParams();
  for (const [k, values] of Object.entries(state.filters)) if (values.length) query.set(k, values.join(','));
  if (state.type) query.set('type', state.type);
  if (state.who && state.who !== 'me') query.set('who', state.who);
  if (state.side === 'asked') query.set('side', 'asked');
  // Any view but the default is named too, so #/tasks?view=board&type=video is a link that stays put.
  if ([...query.keys()].length || state.view !== 'foryou') query.set('view', state.view);
  return query;
}
const tasksQueryText = (query: URLSearchParams) => [...query.keys()].length ? '?' + query.toString().replace(/%2C/gi, ',').replace(/%3A/gi, ':') : '';
function tasksURLWrite(state: TaskState) {
  if (TASKS_ST !== state) return;
  const query = tasksQuery(state);
  const filtered = [...query.keys()].some(k => k !== 'view');
  let base = String(location.hash || TASKS).split('?')[0];
  if (!isTasksRoute(base)) {
    if (!filtered) return;                        // #/task/<id> stays as it is until a filter is chosen
    base = TASKS;
  }
  if (query.has('view')) base = TASKS;            // one address per view: #/board?view=list would say two things
  const hash = base + tasksQueryText(query);
  if (hash !== location.hash) {
    history.replaceState(history.state, '', location.pathname + location.search + hash);
    S.route = LAST_ROUTE = hash;
    tasksPinsRender();
  }
}
// ---- pinned views: Pin keeps the view and its filters (its address) under Pipelines in the left rail, per person
// (preference tasks.pins, cached on this device). A pin is only a link: nothing moves a task's step on its own.
const TASK_PINS_PREF = 'tasks.pins', TASK_PINS_KEY = 'tico.tasks.pins';
const tasksPinValid = (p: Partial<TaskPin> | null | undefined) => p && typeof p.hash === 'string' && p.hash.startsWith(TASKS) && typeof p.name === 'string';
let TASK_PINS: TaskPin[] = (() => { try { return (JSON.parse(localStorage.getItem(TASK_PINS_KEY) || '[]') || []).filter(tasksPinValid); } catch { return []; } })();
const tasksPinHash = (state: TaskState) => TASKS + tasksQueryText(tasksQuery(state));
function tasksPinName(state: TaskState) {
  const type = state.type ? (TASK_TYPES || []).find(t => t.id === state.type)?.name || (state.type === 'general' ? 'General' : state.type) : '';
  const view = tasksViewLabel(state, state.view) || 'Tasks';
  const words = TASK_FILTER_FIELDS.filter(([k]) => state.filters[k]?.length).map(([k]) => {
    const opts = new Map(tasksFilterOptions(state, k).map(o => [o.value, o.label]));
    return (state.filters[k] as string[]).map(v => opts.get(v) || (k === 'owner' || k === 'asked' ? actorLabel(v) : v)).join(', ');
  });
  return [type || view, ...words].join(' · ').slice(0, 60);
}
function tasksPinsKeep(items: TaskPin[]) {
  TASK_PINS = items;
  try { localStorage.setItem(TASK_PINS_KEY, JSON.stringify(items)); } catch {}
  void post('/v2/preferences/' + TASK_PINS_PREF, {value: {items, at: Date.now()}}).catch(() => {});
  tasksPinsRender();
  if (TASKS_ST) tasksTools(TASKS_ST);
}
function tasksPinToggle(state: TaskState) {
  const hash = tasksPinHash(state);
  tasksPinsKeep(TASK_PINS.some(p => p.hash === hash) ? TASK_PINS.filter(p => p.hash !== hash) : [...TASK_PINS, {hash, name: tasksPinName(state)}]);
}
function tasksPinsRender() {
  const box = $('#nav-pins'), list = $('#pin-list');
  if (!box || !list) return;
  box.hidden = !TASK_PINS.length;
  const here = String(location.hash || '');
  const html = TASK_PINS.map((p, i) => `<li class="pin-row"><a class="nav-link" href="${esc(p.hash)}" title="${esc(p.name)}"${p.hash === here ? ' aria-current="page"' : ''}><span class="nav-icon" aria-hidden="true">route</span><span class="pin-name">${esc(p.name)}</span></a>
    <button type="button" class="pin-x" data-unpin="${i}" aria-label="Unpin ${esc(p.name)}" title="Unpin">✕</button></li>`).join('');
  if (list.dataset.html !== html) list.innerHTML = list.dataset.html = html;
}
async function tasksPinsSync() {
  const v = (await v2Get('/v2/preferences/' + TASK_PINS_PREF))?.value;
  if (!Array.isArray(v?.items)) return;
  TASK_PINS = v.items.filter(tasksPinValid);
  try { localStorage.setItem(TASK_PINS_KEY, JSON.stringify(TASK_PINS)); } catch {}
  tasksPinsRender();
  if (TASKS_ST) tasksTools(TASKS_ST);
}
document.addEventListener('click', ev => {
  const x = eventElement(ev)?.closest<HTMLElement>('[data-unpin]');
  if (!x) return;
  ev.preventDefault();
  tasksPinsKeep(TASK_PINS.filter((_, i) => i !== Number(x.dataset.unpin)));
});
window.addEventListener('hashchange', () => tasksPinsRender());
tasksPinsRender();
// The filters an older page saved (Mine, Asked by me, an owner, a tag) become chips once, then are forgotten.
function tasksMigrateOldFilters(state: TaskState, saved: Record<string, string | null> | null) {
  let local: Record<string, string | null> = {};
  try {
    if (!localStorage.getItem('hub.tasks.migrated')) {
      local = {filter: localStorage.getItem('hub.tasks.filter'), bot: localStorage.getItem('hub.board.bot'), label: localStorage.getItem('hub.tasks.label')};
      localStorage.setItem('hub.tasks.migrated', '1');
      for (const k of ['hub.tasks.filter', 'hub.board.bot', 'hub.tasks.label']) localStorage.removeItem(k);
    }
  } catch {}
  const old = {...local, ...Object.fromEntries(Object.entries(saved || {}).filter(([k, v]) => ['filter', 'bot', 'label'].includes(k) && v))};
  const add = (field: string, value: string | null | undefined) => { if (value && !state.filters[field]?.includes(value)) { state.filters[field]!.push(value); return true; } return false; };
  let changed = false;
  if (old.filter === 'mine') changed = add('owner', 'me') || changed;
  if (old.filter === 'asked') changed = add('asked', 'me') || changed;
  if (old.bot) changed = add('owner', tasksBotActor(old.bot)) || changed;
  if (old.label) changed = add('tag', old.label) || changed;
  return changed || Object.keys(old).some(k => old[k] != null && old[k] !== '' && old[k] !== 'all');
}
// the team a task's owner sits in (a group: backend/groups.py)
function taskTeam(t: TaskRow | null | undefined) {
  const slug = actorSlug(t?.owner_id), pid = actorPerson(t?.owner_id);
  if (slug) return teamOf(slug) || '';
  if (pid) return (S.people || []).find(p => p.id === pid)?.team || '';
  return '';
}
const taskTeamLabel = (id: string) => !id ? 'No team' : typeof teamLabel === 'function' ? teamLabel(id) : titleCase(id);
// The repos a task's code is in: a link's repo, or the repo a worktree's folder names (the same as its Code line).
function taskRepos(t: TaskRow | null | undefined) {
  return [...new Set((t?.links || []).filter(l => l.kind === 'pr' || l.kind === 'worktree')
    .map(l => taskLinkRepo(l) || (l.kind === 'worktree' ? taskWorktreeRepo(l) : '')).filter(Boolean))] as string[];
}
const resolveMe = (v: string) => v === 'me' ? myActor() : v;
function taskPassesFilters(t: TaskRow, f: TaskChips) {
  if (f.owner?.length && !f.owner.some(v => resolveMe(v) === t.owner_id || (!v.includes(':') && t.owner_id === 'bot:' + v))) return false;
  if (f.asked?.length && !f.asked.some(v => resolveMe(v) === taskRequester(t))) return false;
  if (f.team?.length && !f.team.includes(taskTeam(t) || 'none')) return false;
  if (f.tag?.length && !f.tag.some(v => (t.labels || []).includes(v))) return false;
  if (f.repo?.length && !f.repo.some(v => taskRepos(t).includes(v))) return false;
  if (f.pr?.length && !f.pr.includes(taskHasPR(t) ? 'yes' : 'no')) return false;
  if (f.due?.length) {
    const due = taskDueInfo(t), now = Date.now();
    const hit: Record<string, boolean> = {overdue: !!due?.late, week: !!due && due.at >= now && due.at < now + 7 * 86400000, any: !!due, none: !due};
    if (!f.due.some(v => hit[v])) return false;
  }
  return true;
}
// The tasks a view shows, before grouping: the team lane, the chips, the type, the search, then the view's own cut.
// One render asks for these a few times; state.memo keeps the answer until the next render or load.
function taskItems(state: TaskState, view = state.view): TaskItem[] {
  if (state.memo?.has(view)) return state.memo.get(view) as TaskItem[];
  // Needs you holds the person's whole plate across boards. A remembered board, chip or search must not hide a
  // counted item. Use the full task when held (relations and properties), with the queue's current ask and status.
  if (tasksNeedsQueue(state, view)) {
    const held = new Map((state.tasks || []).map(t => [String(t.id), t]));
    const out = needsQueueItems().filter(it => NEEDS_TASK_KINDS.includes(it.kind))
      .map(it => taskItem({...held.get(String(it.id)), ...it} as TaskRow));
    state.memo?.set(view, out);
    return out;
  }
  const out = (state.tasks || []).map(taskItem).filter(it => {
    if (it.lane !== 'company') return false;
    if (!taskPassesFilters(it.task, (state.filters || {}) as TaskChips)) return false;
    if (view !== 'foryou' && !taskPipelineMatches(it.task, state)) return false;
    if (state.q && !taskMatches(it, state.q)) return false;
    if (taskSnoozed(it.task) !== (view === 'snoozed')) return false;
    // A type's board has its working steps only, unless its finished steps are unfolded (taskBoardShows).
    if (view === 'board' && pipelineSelectedType(state)) return taskBoardShows(it, state);
    if (tasksFinishedView(view)) return it.col === 'done' && it.task.status === view;
    if (it.col === 'done') return false;
    return view === 'foryou' ? tasksFirstTabHas(state, it) : true;
  });
  state.memo?.set(view, out);
  return out;
}
// Your Needs you count covers the whole plate, independent of the selected board and task filters.
const tasksMyCount = () => decideCount();
function tasksCounts(state: TaskState): {foryou?: number; list?: number; snoozed?: number} {
  if (state.loading) return {};
  const active = taskItems(state, 'list');
  return {foryou: tasksNeedsQueue(state, 'foryou') ? tasksMyCount() : taskItems(state, 'foryou').length, list: active.length,
    snoozed: taskItems(state, 'snoozed').length};
}

// ---- whose tasks the first tab shows: Me (Needs you, in the server's queue with its asks and approvals), Everyone
// (no person), or one teammate's: what is on their plate now, or what they asked of someone else. Another person's
// plate is worked out here from the loaded tasks. The Owner and Asked by chips narrow those other plates.
function tasksWhoKnown(who: string) {
  if (who === 'me' || who === 'all') return true;
  const pid = actorPerson(who), slug = actorSlug(who);
  if (pid) return !(S.people || []).length || S.people.some(p => p.id === pid);
  if (slug) return !(S.emps || []).length || !!botRow(slug);
  return false;
}
// 'me', 'all' or an actor id; anyone this install does not know is Me
function tasksWhoKey(state: TaskState) {
  const who = state.who || 'me';
  return !tasksWhoKnown(who) || who === myActor() ? 'me' : who;
}
const tasksWhoActor = (state: TaskState) => { const key = tasksWhoKey(state); return key === 'me' ? myActor() : key === 'all' ? '' : key; };
// Your own Needs you: the queue the server orders, with asks and approvals
const tasksNeedsQueue = (state: TaskState, view = state.view) => view === 'foryou' && tasksWhoKey(state) === 'me' && state.side !== 'asked';
// A person's plate (not Everyone, not their asks): blocked tasks fold away at its foot
const tasksPlateView = (state: TaskState) => state.view === 'foryou' && state.side !== 'asked' && tasksWhoKey(state) !== 'all';
// On someone's plate: it waits on them now. Yours is exactly Needs you; anyone else's is the person a waiting task
// names, else its owner, and a question asked of them, and a declined task handed back to whoever asked for it.
function taskOnPlate(t: TaskRow | null | undefined, actor: string) {
  if (!t || !actor || taskFinished(t)) return false;
  if (actor === myActor()) return taskNeedsViewer(t);
  if (t.status === 'declined') return taskDeclinedToWho(t, actor);
  const on = t.status === 'waiting' && t.waiting_on ? t.waiting_on : '';
  return (on ? on === actor : t.owner_id === actor) || (t.ask?.recipient_id === actor && !t.ask?.older);
}
const taskAskedBy = (t: TaskRow, actor: string) => !!actor && taskRequester(t) === actor && t.owner_id !== actor;
function tasksFirstTabHas(state: TaskState, it: TaskItem) {
  const key = tasksWhoKey(state), t = it.task;
  if (state.side === 'asked') return taskAskedBy(t, tasksWhoActor(state));
  if (key === 'me') return (taskNeedsViewer(t) || taskOlderAsk(t)) && !needsFolded(it.id);
  return key === 'all' || taskOnPlate(t, key);
}
// Blocked by a task that is still open (as the loaded list has it, else as the relation says)
function taskOpenlyBlocked(t: TaskRow, state: TaskState) {
  const byId = tasksById(state);
  return taskBlockers(t).some(b => !['done', 'closed', 'declined'].includes(String(byId.get(String(b.id))?.status || b.status || 'open')));
}
const tasksActorShort = (a: string) => actorPerson(a) ? personShortName(actorPerson(a)) : actorLabel(a);
// The first tab's name says whose it is: Needs you, Needs Sam, Asked by Sam, Everyone.
function tasksViewLabel(state: TaskState, view: string) {
  const label = TASK_VIEWS.find(([k]) => k === view)?.[1] || '';
  if (view !== 'foryou') return label;
  const key = tasksWhoKey(state), asked = state.side === 'asked';
  if (key === 'all') return 'Everyone';
  if (key === 'me') return asked ? 'Asked by you' : label;
  const name = clipLine(tasksActorShort(key), 18);
  return asked ? `Asked by ${name}` : `Needs ${name}`;
}
// Whose list: the face (or Everyone's glyph) and a caret; the words are its label.
function tasksWhoButtonHTML(state: TaskState) {
  const key = tasksWhoKey(state), asked = state.side === 'asked';
  const words = key === 'me' ? 'Me' : key === 'all' ? 'Everyone' : tasksActorShort(key);
  const label = `Showing: ${asked ? 'Asked by ' : ''}${words}`;
  const face = key === 'all' ? `<span class="tl-gglyph">${TL_ICON.team}</span>` : actorFace(key === 'me' ? myActor() : key, 18);
  return `<button type="button" class="tl-who${asked ? ' asked' : ''}" id="task-who" aria-haspopup="true" aria-label="${esc(label)}" title="${esc(label)}">${face}${TL_ICON.caret}</button>`;
}
function tasksWhoSet(state: TaskState, who: string, side = state.side) {
  side = side === 'asked' ? 'asked' : 'plate';
  if (who === myActor()) who = 'me';
  if (side === 'asked' && who === 'all') who = 'me';       // Everyone asks nothing of its own
  state.who = who; state.side = side;
  state.view = 'foryou';
  state.selectMode = false;
  if (state.selected.size || state.bulkMsg) tasksSelectionClear(state);
  state.memo = new Map();
  tasksRemember(state); tasksURLWrite(state); tasksTools(state); tasksRender(state);
}

// ---- options a chip can take, from what is loaded (and whatever the address already asks for)
function tasksFilterOptions(state: TaskState, field: string): TaskFilterOption[] {
  const tasks = (state.tasks || []).filter(t => (t.lane || 'company') === 'company');
  const selected = (state.filters[field] || []) as string[];
  const me = myActor();
  const actorOpts = (actors: string[], withMe: boolean) => {
    const list = [...new Set([...actors, ...selected.filter(v => v !== 'me')])].filter(a => a && a !== me);
    list.sort((a, b) => (actorPerson(a) ? 0 : 1) - (actorPerson(b) ? 0 : 1) || actorLabel(a).localeCompare(actorLabel(b)));
    return [...(withMe && me ? [{value: 'me', label: 'You', face: actorFace(me, 16)}] : []),
      ...list.map(a => ({value: a, label: actorLabel(a), face: actorFace(a, 16)}))];
  };
  if (field === 'owner') return actorOpts(tasks.map(t => t.owner_id), true);
  if (field === 'asked') return actorOpts(tasks.map(taskRequester), true);
  if (field === 'team') {
    const ids = [...new Set([...tasks.map(t => taskTeam(t) || 'none'), ...selected])];
    return ids.sort((a, b) => Number(a === 'none') - Number(b === 'none') || taskTeamLabel(a === 'none' ? '' : a).localeCompare(taskTeamLabel(b === 'none' ? '' : b)))
      .map(id => ({value: id, label: taskTeamLabel(id === 'none' ? '' : id)}));
  }
  if (field === 'tag') {
    const rich = new Map([...tasks.flatMap(t => t.tags || []), ...(state.tags || [])].map(tag => [tag.key, tag]));
    const keys = [...new Set([...(state.labels || []), ...tasks.flatMap(t => t.labels || []), ...selected])];
    return keys.map(key => ({value: key, label: tagSummary(rich.get(key) || {key, label: key})})).sort((a, b) => a.label.localeCompare(b.label));
  }
  if (field === 'repo') return [...new Set([...tasks.flatMap(taskRepos), ...selected])].sort().map(r => ({value: r, label: r}));
  return Object.entries(TASK_FILTER_WORDS[field] || {}).map(([value, label]) => ({value, label}));
}

// ---- grouping
function tasksGroupBy(state: TaskState) {
  if (tasksFinishedView(state.view)) return 'none';
  const by = (state.group as Record<string, string | undefined>)?.[state.view] || TASK_GROUP_DEFAULT[state.view] || 'status';
  return taskGroupsFor(state.view, state).some(([k]) => k === by) ? by : 'status';
}
const tagHue = (key: string) => [...String(key)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
const tasksById = (state: TaskState) => {
  if (state.byIdFor !== state.tasks || state.byIdNeeds !== S.v2?.needs) {
    // A counted card can arrive before its task's page. Its own task data keeps its properties and selection usable.
    const needs = (S.v2?.needs || []).filter((it): it is NeedsItem & TaskRow => NEEDS_TASK_KINDS.includes(it.kind));
    state.byId = new Map([...needs, ...(state.tasks || [])].map(t => [String(t.id), t]));
    state.byIdFor = state.tasks; state.byIdNeeds = S.v2?.needs;
  }
  return state.byId!;      // set above on the first call: byIdFor starts unset
};
// A group heading for whoever asked: a bot's says "Chat" and opens that bot's chat (where its requests are worked through).
const chatOf = (actor: string) => actorSlug(actor) ? `#/bot/${encodeURIComponent(actorSlug(actor))}` : '';
function tasksGroupsFor(items: TaskItem[], by: string, state: TaskState): TaskGroup[] {
  const groups = new Map<string, TaskGroup>();
  const add = (key: string, make: () => Omit<TaskGroup, 'key' | 'items'>, it: TaskItem) => { if (!groups.has(key)) groups.set(key, {key, items: [], ...make()}); groups.get(key)!.items.push(it); };
  const me = myActor();
  if (by === 'need') {
    // In the server's order (what is due first), each card under its kind; approvals and questions asked
    // outside any task join their group (tasksListHTML).
    const at = (it: TaskItem) => needsIndex().byId.get(String(it.id))?.i ?? Infinity;
    const buckets = new Map<string, TaskGroup>(NEED_GROUPS.map(([k, label]) => [k, {key: 'need:' + k, label, items: []}]));
    for (const it of items.slice().sort((a, b) => at(a) - at(b))) buckets.get(needsItem(it.id)?.need || 'do')!.items.push(it);
    return [...buckets.values()];
  }
  if (by === 'requester' && tasksNeedsQueue(state)) {
    return companyNeedGroups(items).map(g => ({key: 'a:' + g.actor, label: actorLabel(g.actor), icon: actorFace(g.actor, 16), items: g.items,
      chat: chatOf(g.actor), chatName: actorLabel(g.actor)}));
  }
  if (by === 'status') {
    return taskStatusGroups(items).map(g => ({key: g.key, label: g.label, items: g.items, icon: '',
      create: g.owner ? {owner_id: g.owner} : g.key === 'doing' ? {} : null}));
  }
  const byId = tasksById(state);
  for (const it of items) {
    const t = it.task;
    if (by === 'owner') {
      add('o:' + it.actor, () => ({label: actorLabel(it.actor), icon: actorFace(it.actor, 16), actor: it.actor,
        create: {owner_id: actorSlug(it.actor) || it.actor}}), it);
    } else if (by === 'requester') {
      const who = taskRequester(t) || '';
      add('a:' + who, () => ({label: actorLabel(who) || 'Unknown', icon: actorFace(who, 16), actor: who, chat: chatOf(who), chatName: actorLabel(who)}), it);
    } else if (by === 'team') {
      const team = taskTeam(t);
      add('m:' + (team || 'none'), () => ({label: taskTeamLabel(team), icon: `<span class="tl-gglyph">${TL_ICON.team}</span>`, last: !team}), it);
    } else if (by === 'parent') {
      const pid = taskParentId(t);
      const parent = pid && (byId.get(pid) || (t.parent && String(t.parent.id) === pid ? t.parent : null)) || null;
      add('p:' + (pid || 'none'), () => pid ? {label: parent?.title || 'Parent task', icon: '',
        create: parent ? {parent: {id: pid, title: parent.title, owner_id: parent.owner_id}} : null}
        : {label: 'No parent', icon: '', last: true}, it);
    } else if (by === 'tag') {
      const labels = t.labels || [];
      const key = labels.find(l => (state.filters?.tag || []).includes(l)) || labels[0] || '';
      add('g:' + (key || 'none'), () => key ? {label: tagSummary((t.tags || []).find(tag => tag.key === key) || (state.tags || []).find(tag => tag.key === key) || {key, label: key}),
        icon: `<span class="tl-dot" style="--hue:${tagHue(key)}"></span>`, create: {labels: [key]}}
        : {label: 'No tag', icon: '', last: true}, it);
    } else add('all', () => ({label: '', icon: ''}), it);
  }
  const list = [...groups.values()];
  if (by === 'owner' || by === 'requester') {
    return list.sort((a, b) => Number(b.actor === me) - Number(a.actor === me) || (actorPerson(a.actor) ? 0 : 1) - (actorPerson(b.actor) ? 0 : 1) || a.label.localeCompare(b.label));
  }
  return list.sort((a, b) => (a.last ? 1 : 0) - (b.last ? 1 : 0) || a.label.localeCompare(b.label));
}
// Within one group, a subtask nests under its parent when the parent is in that same group (grouping by Status or
// Owner). Elsewhere it stays in its own group, with a small "↳ parent" hint. A task never shows twice.
function tasksNest(items: TaskItem[], nest: boolean) {
  const kids = new Map<string, TaskItem[]>();
  if (!nest) return {tops: items, kids};
  const here = new Map(items.map(it => [String(it.id), it]));
  const tops = [];
  for (const it of items) {
    const pid = taskParentId(it.task);
    if (pid && pid !== String(it.id) && here.has(pid)) {
      if (!kids.has('t' + pid)) kids.set('t' + pid, []);
      kids.get('t' + pid)!.push(it);
    } else tops.push(it);
  }
  // a loop of parents (a → b → a) would hide both: whatever is not reachable from the top joins it
  const seen = new Set<string>();
  const walk = (it: TaskItem) => { if (seen.has(it.key)) return; seen.add(it.key); (kids.get(it.key) || []).forEach(walk); };
  tops.forEach(walk);
  for (const it of items) if (!seen.has(it.key)) {
    for (const list of kids.values()) { const i = list.indexOf(it); if (i >= 0) list.splice(i, 1); }
    tops.push(it); walk(it);
  }
  return {tops, kids};
}
const tasksDescendants = (key: string, kids: Map<string, TaskItem[]>): number => (kids.get(key) || []).reduce((n, k) => n + 1 + tasksDescendants(k.key, kids), 0);
const tasksCanSelect = () => canMove();
// A row's signature: a short hash of its HTML, written on it as data-sig. The poll's patch compares signatures.
function tasksSigned(html: string) {
  let h = 5381;
  for (let i = 0; i < html.length; i++) h = ((h << 5) + h + html.charCodeAt(i)) | 0;
  return html.replace(/^(<div [^>]*?)>/, `$1 data-sig="${(h >>> 0).toString(36)}">`);
}

// ---- one line per task
function taskListRowHTML(it: TaskItem, ctx: TaskRowCtx, depth = 0): string {
  const t = it.task, key = it.key, state = ctx.state;
  const kids = (ctx.kids.get(key) || []).slice().sort(byRank);
  const open = kids.length && !state.folded.has(key);
  const selected = state.selected.has(key), failed = state.bulkFail?.get(key);
  const note = taskRowNote(t), refused = taskRefused(state, t);
  const me = myActor(), asker = taskRequester(t);
  // The amber "Waiting" chip already says the status, so the status word stays out (an empty pad keeps the columns).
  const waitsOnMe = !!taskWaitingOn(t) && taskWaitingOn(t) === me;
  // who asked: a tiny face beside the owner's when it is not you (and not already the group's heading)
  const showAsker = ctx.by !== 'requester' && asker && asker !== me && asker !== t.owner_id && (t.owner_id === me || !!actorPerson(asker));
  // a subtask shown away from its parent says whose it is
  const pid = taskParentId(t);
  const parent = pid && depth === 0 ? (tasksById(state).get(pid) || t.parent) : null;
  const when = ctx.done ? (it.closed || it.updated_at) : it.updated_at;
  const ageText = ctx.done ? ageShort(when) : taskAge(t);
  const ageTitle = ctx.done ? `${t.status === 'closed' ? 'Closed' : 'Done'} ${fmt(when)}` : taskAgeTitle(t);
  // Your You list: everything in it is open, so no status; one face, whoever asks; the ask a muted line under the title.
  const queue = tasksNeedsQueue(state);
  const need = queue ? needsItem(t.id) : null;
  const ask = queue && note && !refused ? note.text : '';
  const asking = queue ? companyNeedActor(t, need) : '';
  // A task answered on a card (a question with options, a yes/no, a recommendation) opens its card, not the peek.
  const card = need ? decideCardKey(need) : '';
  // What a row shows is drawn here; its selection, cursor and peek are painted on afterwards (tasksSelectionPaint,
  // tasksCursorPaint), so an unchanged row keeps the same signature and the poll leaves it alone.
  void selected;
  const fb = fbTaskKey(t);             // a bot asked for it: 👍 / 👎 on hover or a long press (ui/app/feedback.ts)
  const row = tasksSigned(`<div class="tl-row${failed ? ' failed' : ''}${note?.mine && !queue ? ' mine' : ''}${ask ? ' tl-two' : ''}" data-task-key="${esc(key)}"${card ? ` data-need-card="${esc(card)}"` : ''}${fb ? ` data-fb-press="${esc(fb)}"` : ''} role="listitem" aria-level="${depth + 1}" style="--depth:${depth}" title="${esc(taskRowTip(t))}">
    ${ctx.select ? `<input type="checkbox" class="tl-check" data-select="${esc(key)}" tabindex="-1" aria-label="Select ${esc(it.title)}">` : '<span class="tl-check-pad"></span>'}
    ${kids.length ? `<button type="button" class="tl-chev${open ? ' open' : ''}" data-expand="${esc(key)}" tabindex="-1" aria-expanded="${open ? 'true' : 'false'}" aria-label="${open ? 'Hide' : 'Show'} subtasks of ${esc(it.title)}">${TL_ICON.chev}</button>` : '<span class="tl-chev-pad"></span>'}
    <button type="button" class="tl-open" data-open-task="${esc(key)}" tabindex="-1"${kids.length ? ` aria-expanded="${open ? 'true' : 'false'}"` : ''}>${ask ? `<span class="tl-lines"><span class="tl-title">${esc(it.title)}</span><span class="tl-ask" title="${esc(ask)}">${esc(ask)}</span></span>` : `<span class="tl-title">${esc(it.title)}</span>`}${fbMarkHTML(fb)}</button>
    ${parent?.title ? `<span class="tl-parent" title="Part of ${esc(parent.title)}"><span aria-hidden="true">↳ </span><span class="tl-parent-t">${esc(parent.title)}</span></span>` : ''}
    ${refused ? `<span class="tl-note tl-refused" role="alert" data-task-refused title="${esc(refused)}"><span class="tl-note-text">${esc(refused)}</span></span>`
      : ask ? '' : note ? `<span class="tl-note" title="${esc(note.text)}"><span class="tl-note-ic">${TL_ICON.note}</span><span class="tl-note-text">${esc(note.text)}</span></span>` : '<span class="tl-fill"></span>'}
    <span class="tl-chips">${need?.folded?.length ? `<span class="tl-folded" title="${esc(`Also waiting on this: ${need.folded.map(f => f.title).join('; ')}`)}">+${need.folded.length}</span>` : ''}${queue ? '' : waitsOnMe ? '<span class="tl-waiting" title="Its bot is waiting on you">Waiting</span>' : taskStatusLabel(t) === 'Waiting' ? '' : taskWaitTag(t)}${taskChipsHTML(t)}</span>
    ${need?.recommend ? recommendButton(need.recommend, 'tl-rec', t.version) : ''}
    ${queue || ctx.by === 'status' ? '' : waitsOnMe && taskStatusLabel(t) === 'Waiting' ? '<span class="tl-status-pad"></span>' : taskRowStatus(t, key)}
    <span class="tl-people">${queue ? asking && asking !== me ? `<span class="tl-face" title="${esc(actorLabel(asking))}">${actorFace(asking, 18)}</span>` : '' : `${showAsker ? `<span class="tl-asker" title="Asked by ${esc(actorLabel(asker))}">${actorFace(asker, 14)}</span>` : ''}<span class="tl-face" title="${esc(actorLabel(t.owner_id))}">${actorFace(t.owner_id, 18)}</span>`}</span>
    <time class="tl-age tnum" datetime="${esc(when || '')}" title="${esc(ageTitle)}">${esc(ageText)}</time>
    ${failed ? `<span class="tl-failed" role="img" aria-label="Not changed: ${esc(failed)}" title="Not changed: ${esc(failed)}">!</span>` : ''}${fbButtonsHTML(fb, {row: true})}
  </div>`);
  return row + (open ? kids.map(k => taskListRowHTML(k, ctx, depth + 1)).join('') : '');
}
// Whether a plate shows its blocked tasks: this browser's choice (folded until opened)
function tasksShowBlocked() { try { return localStorage.getItem('hub.tasks.blocked') === '1'; } catch { return false; } }
function tasksShowBlockedSet(on: boolean) { try { localStorage.setItem('hub.tasks.blocked', on ? '1' : '0'); } catch {} }
// The row's status word, as a chip that moves the task when the viewer may: its steps for a typed task, else the
// statuses, saved the way the peek's Status row saves them.
function taskRowStatus(t: TaskRow, key: string) {
  if (!taskPropRights(t).edit) return taskStatusText(t);
  const label = taskStatusLabel(t);
  return `<button type="button" class="task-status tl-status" data-status="${esc(t.status || 'open')}" data-row-status="${esc(key)}" tabindex="-1" aria-haspopup="menu" aria-label="${esc(`Status: ${label}. Change`)}" title="${esc(label)}">${esc(label)}</button>`;
}
function tasksRowStatusMenu(state: TaskState, anchor: HTMLElement) {
  const id = anchor.dataset.rowStatus!.slice(1), t = tasksById(state).get(id);
  const host = $('#tasks-pane');
  if (!t || !host) return;
  const type = pipelineType(t), typed = !!type && pipelineTypeId(t) !== 'general';
  const items = typed ? stepItems(t, type).slice(1)
    : TASK_SET_STATUSES.map(s => ({value: s, text: STATUS_WORD[s] || s, html: `<span>${esc(STATUS_WORD[s] || s)}</span>`, checked: t.status === s}));
  propMenu(host, anchor, {label: typed ? 'Step' : 'Status', items, onPick: it => void tasksRowStatusSave(state, id, cur => typed
    ? (it.value === cur.step_id ? null : {step: it.value, ...(it.step?.status === 'done' ? {status: 'done'} : {})})
    : (it.value === cur.status ? null : {status: it.value}))});
}
// One update with the task's version; a 409 reads it again and tries once more; a refusal is named.
async function tasksRowStatusSave(state: TaskState, id: string, bodyFor: (t: TaskRow) => Record<string, unknown> | null) {
  let t = tasksById(state).get(id);
  for (let attempt = 0; t && attempt < 2; attempt++) {
    const body = bodyFor(t);
    if (!body) return;
    if (attempt === 0 && body.status === 'done') taskDoneMotion('t' + id);   // its check draws while the list catches up
    state.refused?.delete(id);
    try { await post(`/v2/tasks/${encodeURIComponent(t.id)}`, {version: t.version, ...body}); break; }
    catch (e) {
      if (errorStatus(e) === 409 && attempt === 0) { t = (await v2Get(`/v2/tasks/${encodeURIComponent(id)}`))?.task; continue; }
      taskRefusedSet(state, t, `Not changed: ${errorMessage(e, 'not saved')}`);
      break;
    }
  }
  if (TASKS_ST === state) await tasksLoad(state);
}
function tasksEmptyHTML(state: TaskState, what: string) {
  const filtered = !tasksNeedsQueue(state) && (tasksActiveFilterCount(state) || state.q);
  return `<div class="tl-empty">${filtered ? 'No tasks match.' : esc(what)}${filtered ? ' <button type="button" class="linkish" data-clear-filters>Clear filters</button>' : ''}</div>`;
}
function tasksListHTML(items: TaskItem[], state: TaskState) {
  const by = tasksGroupBy(state), done = tasksFinishedView(state.view);
  // Approvals and stale checks lead the personal queue, regardless of remembered task filters.
  const queue = tasksNeedsQueue(state);
  // A card just answered leaves Needs you at once (decide.js), and comes back on Undo.
  if (queue) items = items.filter(it => !decideGone('task:' + it.id));
  const cards = queue;
  // Grouped by kind, approvals and stale checks go in their groups, in the server's order with the tasks.
  const byNeed = by === 'need' && queue;
  const lead = cards ? needsExtras() : [];
  const asks = lead.length && !byNeed ? `<section class="tl-group" data-group="asks">
    <header class="tl-ghead"><span class="tl-gname">Approvals and stale checks</span><span class="tl-gcount tnum">${lead.length}</span></header>
    <div class="tl-rows" role="list" aria-label="Approvals and stale checks">${lead.map(it => needRowHTML(it, {by, select: tasksCanSelect()})).join('')}</div></section>` : '';
  const extra = new Map<string, TaskNeedExtra[]>();
  if (byNeed) for (const it of lead) {
    const k = 'need:' + (it.need || 'do');
    extra.set(k, [...(extra.get(k) || []), it]);
  }
  if (!items.length && asks) return `<div class="tl tl-queue">${asks}</div>`;
  if (!items.length && !extra.size) {
    const who = tasksWhoKey(state);
    const emptyWhat = queue ? 'Nothing needs you.' : state.view === 'foryou' ? who === 'all' ? 'Nothing open.' : state.side === 'asked' ? 'Nothing asked.'
      : `Nothing needs ${tasksActorShort(who)}.` : state.view === 'snoozed' ? 'Nothing snoozed.'
      : done ? state.doneNext != null ? 'No matching finished tasks on this page.' : state.view === 'closed' ? 'Nothing closed.' : 'Nothing finished yet.' : 'Nothing open.';
    const empty = tasksEmptyHTML(state, emptyWhat);
    // Done is paged before type and chip filters are applied. An empty page can still have older rows to scan.
    return done && state.doneNext != null
      ? `<div class="tl">${empty}<button class="ghost tl-more" type="button" id="board-more">Show more</button></div>`
      : empty;
  }
  // Needs you ends with the questions nobody answered in three days, folded until opened.
  const older = queue ? items.filter(it => taskOlderAsk(it.task)) : [];
  // A plate's tasks blocked by another open task fold under Blocked at its foot (remembered on this browser).
  const blocked = tasksPlateView(state) ? items.filter(it => !older.includes(it) && taskOpenlyBlocked(it.task, state)) : [];
  const live = older.length || blocked.length ? items.filter(it => !older.includes(it) && !blocked.includes(it)) : items;
  const groups: TaskGroup[] = [...(by === 'none' ? [{key: 'all', label: older.length ? 'Needs you' : '', items: live}] : tasksGroupsFor(live, by, state)),
    ...(older.length ? [{key: 'older', label: 'Older questions', items: older, folded: true}] : []),
    ...(blocked.length ? [{key: 'blocked', label: 'Blocked', items: blocked}] : [])];
  const nest = by === 'status' || by === 'owner';
  const select = tasksCanSelect();
  return `<div class="tl${select ? ' can-select' : ''}${queue ? ' tl-queue' : ''}" data-group-by="${esc(by)}">${asks}${groups.map((g, i) => {
    const {tops: raw, kids} = tasksNest(g.items, nest);
    const tops = byNeed ? raw : raw.slice().sort(done ? byNewest : g.key === 'scheduled' ? (a, b) => String(a.updated_at).localeCompare(String(b.updated_at)) : byRank);
    const more = extra.get(g.key) || [];
    if (!tops.length && !more.length) return '';
    const ctx = {state, kids, done, by, select};
    const count = more.length + tops.reduce((n, it) => n + 1 + tasksDescendants(it.key, kids), 0);
    const ck = g.key === 'blocked' ? 'blocked' : `${state.view}:${by}:${g.key}`;
    const collapsed = g.key === 'blocked' ? !tasksShowBlocked() : state.collapsed.has(ck) !== !!g.folded;
    const id = `tl-g-${i}`;
    const head = g.label ? `<header class="tl-ghead">
        <button type="button" class="tl-gtoggle" data-group-toggle="${esc(ck)}" aria-expanded="${collapsed ? 'false' : 'true'}" aria-controls="${id}" aria-label="${esc(g.label)}, ${count}">
          <span class="tl-gchev">${TL_ICON.chev}</span>${g.icon || ''}<span class="tl-gname">${esc(g.label)}</span><span class="tl-gcount tnum">${count}</span></button>
        ${g.chat ? `<a class="tl-gchat" href="${esc(g.chat)}" aria-label="Chat with ${esc(g.chatName)}">${TL_ICON.chat}<span>Chat</span></a>` : ''}
        <span class="spacer"></span>
        ${g.create ? `<button type="button" class="tl-gact" data-group-add="${esc(g.key)}" aria-label="New task in ${esc(g.label)}" title="New task">${TL_ICON.plus}</button>` : ''}
      </header>` : '';
    return `<section class="tl-group${collapsed ? ' collapsed' : ''}" data-group="${esc(g.key)}">${head}
      <div class="tl-rows" id="${id}" role="list"${g.label ? ` aria-label="${esc(g.label)}"` : ' aria-label="Tasks"'}${collapsed ? ' hidden' : ''}>${byNeed ? tasksNeedRowsHTML(more, tops, ctx) : tops.map(it => taskListRowHTML(it, ctx)).join('')}</div></section>`;
  }).join('')}${done && state.doneNext != null ? '<button class="ghost tl-more" type="button" id="board-more">Show more</button>' : ''}</div>`;
}
// A need group: its items outside any task among its tasks, all in the server's order.
function tasksNeedRowsHTML(more: TaskNeedExtra[], tops: TaskItem[], ctx: TaskRowCtx) {
  const at = (id: unknown) => needsIndex().byId.get(String(id))?.i ?? Infinity;
  return [...more.map((it): [number, string] => [at(it.id), needRowHTML(it, ctx)]), ...tops.map((it): [number, string] => [at(it.id), taskListRowHTML(it, ctx)])]
    .sort((a, b) => a[0] - b[0]).map(r => r[1]).join('');
}
// Done: everything finished, newest first, a page at a time (a schedule's runs are under Automations › Runs).
function tasksDoneHTML(items: TaskItem[], state: TaskState) {
  const list = items.filter(it => !isRecurringTask(it.task));
  if (state.doneLoading && !list.length) return '<div class="tl-empty">Loading finished tasks…</div>';
  return tasksListHTML(list, state);
}

// ---- the toolbar: tabs with counts, search, new; then the filter chips and Group by. Drawn only when it changes,
// so a load never closes a menu or takes the focus off a chip.
// The scope button: You with its count (whose list it is: You, Needs Sam, Everyone), else the type in view.
const tasksScopeLabel = (state: TaskState) => state.view === 'foryou' ? tasksViewLabel(state, 'foryou') : taskPipelineLabel(state.type);
// The ⋯ menu: Select tasks (where tasks can be picked), Stats (not for a bot), the ranked roadmap on the Roadmap type,
// and Pin to sidebar, or Unpin when this view is pinned.
const tasksSelectable = (state: TaskState) => tasksCanSelect() && ['list', 'foryou', 'done', 'closed'].includes(state.view);
// A phone's chips row scrolls sideways; while more of it is off to the right, its edge fades (task-list.css .more).
const tasksBarEdge = (bar: HTMLElement | null | undefined) => bar?.classList.toggle('more', bar.scrollWidth - bar.clientWidth - bar.scrollLeft > 1);

// ---- remembered per viewer: the group, the folded groups and the folded parents (the person's preference, else this browser)
function tasksPrefsFromLocal(state: TaskState) {
  try {
    const raw = JSON.parse(localStorage.getItem('hub.tasks.layout') || '{}');
    tasksPrefsApply(state, raw);
  } catch {}
}
function tasksPrefsApply(state: TaskState, value: Record<string, unknown> | null | undefined) {
  if (!state.whoFixed && typeof value?.who === 'string' && value.who) state.who = value.who;
  if (!state.whoFixed && typeof value?.side === 'string') state.side = value.side === 'asked' ? 'asked' : 'plate';
  if ((value?.layout === 'list' || value?.layout === 'board') && !tasksLayoutView(state.view)) state.layout = value.layout;
  if (value?.group && typeof value.group === 'object') state.group = {...state.group, ...value.group};
  if (Array.isArray(value?.collapsed)) state.collapsed = new Set(value.collapsed.map(String));
  if (Array.isArray(value?.folded)) state.folded = new Set(value.folded.map(String));
}
const tasksPrefsValue = (state: TaskState) => ({layout: state.layout || 'list', who: state.who || 'me', side: state.side === 'asked' ? 'asked' : 'plate', group: state.group, collapsed: [...state.collapsed].slice(-200), folded: [...state.folded].slice(-200)});

const BULK_STATUSES = ['open', 'doing', 'waiting', 'review', 'ready', 'done', 'declined'];
// ---- all of a column or a list at once. The selection is every task shown, and with it the filter that shows them,
// so the change is one call (POST /v2/task-bulk) and the server makes it to each task as that task's own update.
// A filter the server cannot run (a team, a repo, a search, two owners, a fold that hides some) goes as the ids shown.
const BULK_IDS = 100;
const BULK_SERVER_CHIPS: Record<string, string> = {owner: 'owner', asked: 'requester', tag: 'label'};
const tasksSelectedTitle = (state: TaskState, key: string) => tasksById(state).get(key.slice(1))?.title || key.slice(1);

let PEEK_BACK = false;
window.addEventListener('popstate', () => {
  PEEK_BACK = false;
  const d = $('#task-peek');
  if (d?.open && !history.state?.taskPeek && TASKS_ST) taskPeekClose(TASKS_ST);
});

