/* ui/app/task-list.js — The Tasks list: one line per task, groups, filter chips (kept in the URL), the side peek,
   multi-select with bulk changes, and the keyboard.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ---- filters: chips over the loaded tasks, kept in the address (#/tasks?owner=bot:eng&tag=bug) so a view can be shared
const TASK_FILTER_FIELDS = [['owner', 'Owner'], ['team', 'Team'], ['tag', 'Tag'], ['repo', 'Repo'], ['pr', 'Has PR'], ['due', 'Due'], ['asked', 'Asked by']];
const TASK_FILTER_WORDS = {pr: {yes: 'Yes', no: 'No'}, due: {overdue: 'Overdue', week: 'Next 7 days', any: 'Has a date', none: 'No date'}};
const TASK_GROUPS = [['status', 'Status'], ['owner', 'Owner'], ['team', 'Team'], ['parent', 'Parent task'], ['tag', 'Tag'], ['requester', 'Asked by']];
const TASK_GROUP_DEFAULT = {list: 'status', foryou: 'requester'};
const tasksEmptyFilters = () => Object.fromEntries(TASK_FILTER_FIELDS.map(([k]) => [k, []]));
const isTasksRoute = r => [TASKS, BOARD, ISSUES, RECURRING].includes(String(r || '').split('?')[0]);
const PHONE = '(max-width:760px)';
// Small inline icons (the icon font is a fixed subset; these never need it).
const TL_ICON = {
  chev: '<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false"><path d="M4.5 3l3 3-3 3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  plus: '<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false"><path d="M6 2v8M2 6h8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  x: '<svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true" focusable="false"><path d="M3 3l6 6M9 3l-6 6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  filter: '<svg viewBox="0 0 14 14" width="13" height="13" aria-hidden="true" focusable="false"><path d="M2 3.5h10M4 7h6M5.8 10.5h2.4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  search: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><circle cx="6.2" cy="6.2" r="4.2" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M9.3 9.3L12.3 12.3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  chat: '<svg viewBox="0 0 14 14" width="13" height="13" aria-hidden="true" focusable="false"><path d="M2.5 3.5h9v6h-5l-2.5 2v-2h-1.5z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>',
  full: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><path d="M8.5 2.5h3v3M5.5 11.5h-3v-3M11.5 2.5L8 6M2.5 11.5L6 8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  up: '<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false"><path d="M3 7.5l3-3 3 3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  down: '<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false"><path d="M3 4.5l3 3 3-3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  team: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><circle cx="5" cy="5" r="2" fill="none" stroke="currentColor" stroke-width="1.3"/><circle cx="9.6" cy="5.6" r="1.6" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M1.8 11.2c.5-2 1.7-3 3.2-3s2.7 1 3.2 3M8.4 8.6c1.6-.4 3 .5 3.6 2.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
  note: '<svg viewBox="0 0 14 14" width="13" height="13" aria-hidden="true" focusable="false"><path d="M3 2.5h8v6.5l-2.5 2.5H3z M8.5 11.5V9h2.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
  group: '<svg viewBox="0 0 14 14" width="13" height="13" aria-hidden="true" focusable="false"><path d="M2.5 3.5h9M4.5 7h7M4.5 10.5h7M2.5 7h.01M2.5 10.5h.01" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  caret: '<svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true" focusable="false"><path d="M3 4.5l3 3 3-3" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};
function tasksFiltersFromURL(state) {
  const query = new URLSearchParams(String(location.hash).split('?')[1] || '');
  state.filters = tasksEmptyFilters();
  for (const k of Object.keys(state.filters)) {
    const raw = query.get(k);
    if (raw) state.filters[k] = [...new Set(raw.split(',').map(s => s.trim()).filter(Boolean))];
  }
  state.urlType = query.get('type') || '';
  state.urlView = query.get('view') || '';
  return query;
}
function tasksActiveFilterCount(state) {
  return Object.values(state.filters || {}).filter(v => v.length).length;
}
// The address follows the filters without reloading the page (no hashchange, no history entry). With any filter in
// it, it names the view too, so a shared address opens the same tab whatever route it started from.
function tasksQuery(state) {
  const query = new URLSearchParams();
  for (const [k, values] of Object.entries(state.filters)) if (values.length) query.set(k, values.join(','));
  if (state.type) query.set('type', state.type);
  // Any view but the default is named too, so #/tasks?view=board&type=video is a link that stays put.
  if ([...query.keys()].length || state.view !== 'foryou') query.set('view', state.view);
  return query;
}
const tasksQueryText = query => [...query.keys()].length ? '?' + query.toString().replace(/%2C/gi, ',').replace(/%3A/gi, ':') : '';
function tasksURLWrite(state) {
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
const tasksPinValid = p => p && typeof p.hash === 'string' && p.hash.startsWith(TASKS) && typeof p.name === 'string';
let TASK_PINS = (() => { try { return (JSON.parse(localStorage.getItem(TASK_PINS_KEY) || '[]') || []).filter(tasksPinValid); } catch { return []; } })();
const tasksPinHash = state => TASKS + tasksQueryText(tasksQuery(state));
function tasksPinName(state) {
  const type = state.type ? (TASK_TYPES || []).find(t => t.id === state.type)?.name || (state.type === 'general' ? 'General' : state.type) : '';
  const view = TASK_VIEWS.find(([k]) => k === state.view)?.[1] || 'Tasks';
  const words = TASK_FILTER_FIELDS.filter(([k]) => state.filters[k]?.length).map(([k]) => {
    const opts = new Map(tasksFilterOptions(state, k).map(o => [o.value, o.label]));
    return state.filters[k].map(v => opts.get(v) || (k === 'owner' || k === 'asked' ? actorLabel(v) : v)).join(', ');
  });
  return [type || view, ...words].join(' · ').slice(0, 60);
}
function tasksPinsKeep(items) {
  TASK_PINS = items;
  try { localStorage.setItem(TASK_PINS_KEY, JSON.stringify(items)); } catch {}
  void post('/v2/preferences/' + TASK_PINS_PREF, {value: {items, at: Date.now()}}).catch(() => {});
  tasksPinsRender();
  if (TASKS_ST) tasksTools(TASKS_ST);
}
function tasksPinToggle(state) {
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
  const x = ev.target.closest('[data-unpin]');
  if (!x) return;
  ev.preventDefault();
  tasksPinsKeep(TASK_PINS.filter((_, i) => i !== Number(x.dataset.unpin)));
});
window.addEventListener('hashchange', () => tasksPinsRender());
tasksPinsRender();
// The filters an older page saved (Mine, Asked by me, an owner, a tag) become chips once, then are forgotten.
function tasksMigrateOldFilters(state, saved) {
  let local = {};
  try {
    if (!localStorage.getItem('hub.tasks.migrated')) {
      local = {filter: localStorage.getItem('hub.tasks.filter'), bot: localStorage.getItem('hub.board.bot'), label: localStorage.getItem('hub.tasks.label')};
      localStorage.setItem('hub.tasks.migrated', '1');
      for (const k of ['hub.tasks.filter', 'hub.board.bot', 'hub.tasks.label']) localStorage.removeItem(k);
    }
  } catch {}
  const old = {...local, ...Object.fromEntries(Object.entries(saved || {}).filter(([k, v]) => ['filter', 'bot', 'label'].includes(k) && v))};
  const add = (field, value) => { if (value && !state.filters[field].includes(value)) { state.filters[field].push(value); return true; } return false; };
  let changed = false;
  if (old.filter === 'mine') changed = add('owner', 'me') || changed;
  if (old.filter === 'asked') changed = add('asked', 'me') || changed;
  if (old.bot) changed = add('owner', old.bot.includes(':') ? old.bot : 'bot:' + old.bot) || changed;
  if (old.label) changed = add('tag', old.label) || changed;
  return changed || Object.keys(old).some(k => old[k] != null && old[k] !== '' && old[k] !== 'all');
}
// the team a task's owner sits in (a group: backend/groups.py)
function taskTeam(t) {
  const slug = actorSlug(t?.owner), pid = actorPerson(t?.owner);
  if (slug) return teamOf(slug) || '';
  if (pid) return (S.people || []).find(p => p.id === pid)?.team || '';
  return '';
}
const taskTeamLabel = id => !id ? 'No team' : typeof teamLabel === 'function' ? teamLabel(id) : titleCase(id);
// The repos a task's code is in: a link's repo, or the repo a worktree's folder names (the same as its Code line).
function taskRepos(t) {
  return [...new Set((t?.links || []).filter(l => l.kind === 'pr' || l.kind === 'worktree')
    .map(l => taskLinkRepo(l) || (l.kind === 'worktree' ? taskWorktreeRepo(l) : '')).filter(Boolean))];
}
const resolveMe = v => v === 'me' ? myActor() : v;
function taskPassesFilters(t, f) {
  if (f.owner?.length && !f.owner.some(v => resolveMe(v) === t.owner || (!v.includes(':') && t.owner === 'bot:' + v))) return false;
  if (f.asked?.length && !f.asked.some(v => resolveMe(v) === taskRequester(t))) return false;
  if (f.team?.length && !f.team.includes(taskTeam(t) || 'none')) return false;
  if (f.tag?.length && !f.tag.some(v => (t.labels || []).includes(v))) return false;
  if (f.repo?.length && !f.repo.some(v => taskRepos(t).includes(v))) return false;
  if (f.pr?.length && !f.pr.includes(taskHasPR(t) ? 'yes' : 'no')) return false;
  if (f.due?.length) {
    const due = taskDueInfo(t), now = Date.now();
    const hit = {overdue: !!due?.late, week: !!due && due.at >= now && due.at < now + 7 * 86400000, any: !!due, none: !due};
    if (!f.due.some(v => hit[v])) return false;
  }
  return true;
}
// The tasks a view shows, before grouping: the team lane, the chips, the type, the search, then the view's own cut.
// One render asks for these a few times; state.memo keeps the answer until the next render or load.
function taskItems(state, view = state.view) {
  if (state.memo?.has(view)) return state.memo.get(view);
  const out = (state.tasks || []).map(taskItem).filter(it => {
    if (it.lane !== 'company') return false;
    if (!taskPassesFilters(it.task, state.filters || {})) return false;
    if (!taskPipelineMatches(it.task, state)) return false;
    if (state.q && !taskMatches(it, state.q)) return false;
    if (view === 'board' && pipelineSelectedType(state)) return true;
    if (view === 'done') return it.col === 'done';
    if (it.col === 'done') return false;
    return view === 'foryou' ? taskNeedsViewer(it.task) : true;
  });
  state.memo?.set(view, out);
  return out;
}
function tasksCounts(state) {
  if (state.loading) return {};
  const active = taskItems(state, 'list');
  return {foryou: active.filter(it => taskNeedsViewer(it.task)).length, list: active.length,
    recurring: typeof routineItems === 'function' ? routineItems(state).length : 0};
}

// ---- options a chip can take, from what is loaded (and whatever the address already asks for)
function tasksFilterOptions(state, field) {
  const tasks = (state.tasks || []).filter(t => (t.lane || 'company') === 'company');
  const selected = state.filters[field] || [];
  const me = myActor();
  const actorOpts = (actors, withMe) => {
    const list = [...new Set([...actors, ...selected.filter(v => v !== 'me')])].filter(a => a && a !== me);
    list.sort((a, b) => (actorPerson(a) ? 0 : 1) - (actorPerson(b) ? 0 : 1) || actorLabel(a).localeCompare(actorLabel(b)));
    return [...(withMe && me ? [{value: 'me', label: 'You', face: actorFace(me, 16)}] : []),
      ...list.map(a => ({value: a, label: actorLabel(a), face: actorFace(a, 16)}))];
  };
  if (field === 'owner') return actorOpts(tasks.map(t => t.owner), true);
  if (field === 'asked') return actorOpts(tasks.map(taskRequester), true);
  if (field === 'team') {
    const ids = [...new Set([...tasks.map(t => taskTeam(t) || 'none'), ...selected])];
    return ids.sort((a, b) => (a === 'none') - (b === 'none') || taskTeamLabel(a === 'none' ? '' : a).localeCompare(taskTeamLabel(b === 'none' ? '' : b)))
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
function tasksGroupBy(state) {
  if (state.view === 'done') return 'none';
  const by = state.group?.[state.view] || TASK_GROUP_DEFAULT[state.view] || 'status';
  return TASK_GROUPS.some(([k]) => k === by) ? by : 'status';
}
const tagHue = key => [...String(key)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
const tasksById = state => {
  if (state.byIdFor !== state.tasks) { state.byId = new Map((state.tasks || []).map(t => [String(t.id), t])); state.byIdFor = state.tasks; }
  return state.byId;
};
// A group heading for whoever asked: a bot's says "Chat" and opens that bot's chat (where its requests are worked through).
const chatOf = actor => actorSlug(actor) ? `#/bot/${encodeURIComponent(actorSlug(actor))}` : '';
function tasksGroupsFor(items, by, state) {
  const groups = new Map();
  const add = (key, make, it) => { if (!groups.has(key)) groups.set(key, {key, items: [], ...make()}); groups.get(key).items.push(it); };
  const me = myActor();
  if (by === 'requester' && state.view === 'foryou') {
    return companyNeedGroups(items).map(g => ({key: 'a:' + g.actor, label: actorLabel(g.actor), icon: actorFace(g.actor, 16), items: g.items,
      chat: chatOf(g.actor), chatName: actorLabel(g.actor)}));
  }
  if (by === 'status') {
    return taskStatusGroups(items).map(g => ({key: g.key, label: g.label, items: g.items, icon: '',
      create: g.owner ? {owner: g.owner} : g.key === 'doing' ? {} : null}));
  }
  const byId = tasksById(state);
  for (const it of items) {
    const t = it.task;
    if (by === 'owner') {
      add('o:' + it.actor, () => ({label: actorLabel(it.actor), icon: actorFace(it.actor, 16), actor: it.actor,
        create: {owner: actorSlug(it.actor) || it.actor}}), it);
    } else if (by === 'requester') {
      const who = taskRequester(t) || '';
      add('a:' + who, () => ({label: actorLabel(who) || 'Unknown', icon: actorFace(who, 16), actor: who, chat: chatOf(who), chatName: actorLabel(who)}), it);
    } else if (by === 'team') {
      const team = taskTeam(t);
      add('m:' + (team || 'none'), () => ({label: taskTeamLabel(team), icon: `<span class="tl-gglyph">${TL_ICON.team}</span>`, last: !team}), it);
    } else if (by === 'parent') {
      const pid = t.parent_id ? String(t.parent_id) : '';
      const parent = pid && (byId.get(pid) || (t.parent && String(t.parent.id) === pid ? t.parent : null));
      add('p:' + (pid || 'none'), () => pid ? {label: parent?.title || 'Parent task', icon: '',
        create: parent ? {parent: {id: pid, title: parent.title, owner: parent.owner}} : null}
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
    return list.sort((a, b) => (b.actor === me) - (a.actor === me) || (actorPerson(a.actor) ? 0 : 1) - (actorPerson(b.actor) ? 0 : 1) || a.label.localeCompare(b.label));
  }
  return list.sort((a, b) => (a.last ? 1 : 0) - (b.last ? 1 : 0) || a.label.localeCompare(b.label));
}
// Within one group, a subtask nests under its parent when the parent is in that same group (grouping by Status or
// Owner). Elsewhere it stays in its own group, with a small "↳ parent" hint. A task never shows twice.
function tasksNest(items, nest) {
  const kids = new Map();
  if (!nest) return {tops: items, kids};
  const here = new Map(items.map(it => [String(it.id), it]));
  const tops = [];
  for (const it of items) {
    const pid = it.task?.parent_id ? String(it.task.parent_id) : '';
    if (pid && pid !== String(it.id) && here.has(pid)) {
      if (!kids.has('t' + pid)) kids.set('t' + pid, []);
      kids.get('t' + pid).push(it);
    } else tops.push(it);
  }
  // a loop of parents (a → b → a) would hide both: whatever is not reachable from the top joins it
  const seen = new Set();
  const walk = it => { if (seen.has(it.key)) return; seen.add(it.key); (kids.get(it.key) || []).forEach(walk); };
  tops.forEach(walk);
  for (const it of items) if (!seen.has(it.key)) {
    for (const list of kids.values()) { const i = list.indexOf(it); if (i >= 0) list.splice(i, 1); }
    tops.push(it); walk(it);
  }
  return {tops, kids};
}
const tasksDescendants = (key, kids) => (kids.get(key) || []).reduce((n, k) => n + 1 + tasksDescendants(k.key, kids), 0);
const tasksCanSelect = () => canMove();
// A row's signature: a short hash of its HTML, written on it as data-sig. The poll's patch compares signatures.
function tasksSigned(html) {
  let h = 5381;
  for (let i = 0; i < html.length; i++) h = ((h << 5) + h + html.charCodeAt(i)) | 0;
  return html.replace(/^(<div [^>]*?)>/, `$1 data-sig="${(h >>> 0).toString(36)}">`);
}

// ---- one line per task
function taskListRowHTML(it, ctx, depth = 0) {
  const t = it.task, key = it.key, state = ctx.state;
  const kids = (ctx.kids.get(key) || []).slice().sort(byRank);
  const open = kids.length && !state.folded.has(key);
  const selected = state.selected.has(key), failed = state.bulkFail?.get(key);
  const note = taskRowNote(t);
  const me = myActor(), asker = taskRequester(t);
  // who asked: a tiny face beside the owner's when it is not you (and not already the group's heading)
  const showAsker = ctx.by !== 'requester' && asker && asker !== me && asker !== t.owner && (t.owner === me || !!actorPerson(asker));
  // a subtask shown away from its parent says whose it is
  const pid = t.parent_id ? String(t.parent_id) : '';
  const parent = pid && depth === 0 ? (tasksById(state).get(pid) || t.parent) : null;
  const when = ctx.done ? (it.closed || it.updated) : it.updated;
  // What a row shows is drawn here; its selection, cursor and peek are painted on afterwards (tasksSelectionPaint,
  // tasksCursorPaint), so an unchanged row keeps the same signature and the poll leaves it alone.
  void selected;
  const row = tasksSigned(`<div class="tl-row${failed ? ' failed' : ''}${note?.mine ? ' mine' : ''}" data-task-key="${esc(key)}" role="listitem" aria-level="${depth + 1}" style="--depth:${depth}" title="${esc(taskRowTip(t))}">
    ${ctx.select ? `<input type="checkbox" class="tl-check" data-select="${esc(key)}" tabindex="-1" aria-label="Select ${esc(it.title)}">` : '<span class="tl-check-pad"></span>'}
    ${kids.length ? `<button type="button" class="tl-chev${open ? ' open' : ''}" data-expand="${esc(key)}" tabindex="-1" aria-expanded="${open ? 'true' : 'false'}" aria-label="${open ? 'Hide' : 'Show'} subtasks of ${esc(it.title)}">${TL_ICON.chev}</button>` : '<span class="tl-chev-pad"></span>'}
    <button type="button" class="tl-open" data-open-task="${esc(key)}" tabindex="-1"${kids.length ? ` aria-expanded="${open ? 'true' : 'false'}"` : ''}><span class="tl-title">${esc(it.title)}</span></button>
    ${parent?.title ? `<span class="tl-parent" title="Part of ${esc(parent.title)}"><span aria-hidden="true">↳ </span><span class="tl-parent-t">${esc(parent.title)}</span></span>` : ''}
    ${note ? `<span class="tl-note" title="${esc(note.text)}"><span class="tl-note-ic">${TL_ICON.note}</span><span class="tl-note-text">${esc(note.text)}</span></span>` : '<span class="tl-fill"></span>'}
    <span class="tl-chips">${taskChipsHTML(t)}</span>
    ${ctx.by === 'status' ? '' : taskStatusText(t)}
    <span class="tl-people">${showAsker ? `<span class="tl-asker" title="Asked by ${esc(actorLabel(asker))}">${actorFace(asker, 14)}</span>` : ''}<span class="tl-face" title="${esc(actorLabel(t.owner))}">${actorFace(t.owner, 18)}</span></span>
    <time class="tl-age tnum" datetime="${esc(when || '')}" title="${ctx.done ? 'Done' : 'Updated'} ${esc(fmt(when))}">${esc(ageShort(when))}</time>
    ${failed ? `<span class="tl-failed" role="img" aria-label="Not changed: ${esc(failed)}" title="Not changed: ${esc(failed)}">!</span>` : ''}
  </div>`);
  return row + (open ? kids.map(k => taskListRowHTML(k, ctx, depth + 1)).join('') : '');
}
function tasksEmptyHTML(state, what) {
  const filtered = tasksActiveFilterCount(state) || state.q;
  return `<div class="tl-empty">${filtered ? 'No tasks match.' : esc(what)}${filtered ? ' <button type="button" class="linkish" data-clear-filters>Clear filters</button>' : ''}</div>`;
}
function tasksListHTML(items, state) {
  const by = tasksGroupBy(state), done = state.view === 'done';
  if (!items.length) {
    const emptyWhat = state.view === 'foryou' ? 'Nothing needs you.'
      : done ? state.doneNext != null ? 'No matching finished tasks on this page.' : 'Nothing finished yet.' : 'Nothing open.';
    const empty = tasksEmptyHTML(state, emptyWhat);
    // Done is paged before type and chip filters are applied. An empty page can still have older rows to scan.
    return done && state.doneNext != null
      ? `<div class="tl">${empty}<button class="ghost tl-more" type="button" id="board-more">Show more</button></div>`
      : empty;
  }
  const groups = by === 'none' ? [{key: 'all', label: '', items}] : tasksGroupsFor(items, by, state);
  const nest = by === 'status' || by === 'owner';
  const select = tasksCanSelect();
  return `<div class="tl${select ? ' can-select' : ''}" data-group-by="${esc(by)}">${groups.map((g, i) => {
    const {tops: raw, kids} = tasksNest(g.items, nest);
    const tops = raw.slice().sort(done ? byNewest : g.key === 'scheduled' ? (a, b) => String(a.updated).localeCompare(String(b.updated)) : byRank);
    if (!tops.length) return '';
    const ctx = {state, kids, done, by, select};
    const count = tops.reduce((n, it) => n + 1 + tasksDescendants(it.key, kids), 0);
    const ck = `${state.view}:${by}:${g.key}`, collapsed = state.collapsed.has(ck);
    const id = `tl-g-${i}`;
    const head = g.label ? `<header class="tl-ghead">
        <button type="button" class="tl-gtoggle" data-group-toggle="${esc(ck)}" aria-expanded="${collapsed ? 'false' : 'true'}" aria-controls="${id}" aria-label="${esc(g.label)}, ${count}">
          <span class="tl-gchev">${TL_ICON.chev}</span>${g.icon || ''}<span class="tl-gname">${esc(g.label)}</span><span class="tl-gcount tnum">${count}</span></button>
        ${g.chat ? `<a class="tl-gchat" href="${esc(g.chat)}" aria-label="Chat with ${esc(g.chatName)}">${TL_ICON.chat}<span>Chat</span></a>` : ''}
        <span class="spacer"></span>
        ${g.create ? `<button type="button" class="tl-gact" data-group-add="${esc(g.key)}" aria-label="New task in ${esc(g.label)}" title="New task">${TL_ICON.plus}</button>` : ''}
      </header>` : '';
    return `<section class="tl-group${collapsed ? ' collapsed' : ''}" data-group="${esc(g.key)}">${head}
      <div class="tl-rows" id="${id}" role="list"${g.label ? ` aria-label="${esc(g.label)}"` : ' aria-label="Tasks"'}${collapsed ? ' hidden' : ''}>${tops.map(it => taskListRowHTML(it, ctx)).join('')}</div></section>`;
  }).join('')}${done && state.doneNext != null ? '<button class="ghost tl-more" type="button" id="board-more">Show more</button>' : ''}</div>`;
}
// Done: everything finished, newest first, a page at a time (routine runs live under Recurring).
function tasksDoneHTML(items, state) {
  const list = items.filter(it => !isRecurringTask(it.task));
  if (state.doneLoading && !list.length) return '<div class="tl-empty">Loading finished tasks…</div>';
  return tasksListHTML(list, state);
}

// ---- the toolbar: tabs with counts, search, new; then the filter chips and Group by. Drawn only when it changes,
// so a load never closes a menu or takes the focus off a chip.
function tasksTabsHTML(state) {
  const counts = tasksCounts(state);
  return TASK_VIEWS.map(([k, label]) => {
    const on = k === state.view, n = counts[k];
    return `<button type="button" role="tab" id="task-tab-${k}" data-view="${k}" aria-selected="${on}" aria-controls="task-body" tabindex="${on ? 0 : -1}"${n != null ? ` aria-label="${esc(label)}, ${n}"` : ''}>${esc(label)}${n != null ? `<span class="cnt tnum${k === 'foryou' && n ? ' hot' : ''}" aria-hidden="true">${n}</span>` : ''}</button>`;
  }).join('');
}
function tasksPaint(el, html) {
  if (!el || el.dataset.html === html) return false;
  const focused = el.contains(document.activeElement) ? document.activeElement : null;
  const mark = focused && (focused.dataset.view ? `[data-view="${focused.dataset.view}"]` : focused.dataset.chipEdit ? `[data-chip-edit="${focused.dataset.chipEdit}"]`
    : focused.dataset.chipDrop ? `[data-chip-drop="${focused.dataset.chipDrop}"]` : focused.id ? '#' + CSS.escape(focused.id) : '');
  el.innerHTML = el.dataset.html = html;
  if (mark) el.querySelector(mark)?.focus();
  return true;
}
function tasksTools(state) {
  const tabs = $('#task-view');
  if (!tabs || TASKS_ST !== state) return;
  taskPipelineTools(state);
  tasksPaint(tabs, tasksTabsHTML(state));
  $('#task-bar').hidden = state.view === 'recurring';
  const chips = [];
  for (const [field, name] of TASK_FILTER_FIELDS) {
    const values = state.filters[field];
    if (!values?.length) continue;
    const opts = new Map(tasksFilterOptions(state, field).map(o => [o.value, o.label]));
    const words = values.map(v => opts.get(v) || (field === 'owner' || field === 'asked' ? actorLabel(v) : v));
    const shown = words.length > 2 ? `${words.slice(0, 2).join(', ')} +${words.length - 2}` : words.join(', ');
    chips.push(`<span class="tl-chip" data-chip="${field}"><button type="button" class="tl-chip-main" data-chip-edit="${field}" aria-haspopup="true" aria-label="${esc(`${name}: ${words.join(', ')}. Change`)}" title="${esc(`${name}: ${words.join(', ')}`)}"><span class="k">${esc(name)}</span><span class="v">${esc(shown)}</span></button><button type="button" class="tl-chip-x" data-chip-drop="${field}" aria-label="Remove ${esc(name)} filter">${TL_ICON.x}</button></span>`);
  }
  tasksPaint($('#task-chips'), chips.join(''));
  const count = tasksActiveFilterCount(state);
  const addBtn = $('#task-filter');
  addBtn.classList.toggle('active', !!count);
  addBtn.setAttribute('aria-label', count ? `Add filter, ${count} active` : 'Add filter');
  $('#task-filter-clear').hidden = count === 0;
  const groupable = state.view === 'list' || state.view === 'foryou';
  $('#task-group-wrap').hidden = !groupable;
  if (groupable) {
    const by = tasksGroupBy(state), word = TASK_GROUPS.find(([k]) => k === by)?.[1] || by;
    tasksPaint($('#task-group-wrap'), `<button type="button" class="tl-chip tl-chip-main tl-groupchip" id="task-group" aria-haspopup="true" aria-label="Group by: ${esc(word)}. Change">${TL_ICON.group}<span class="k">Group</span><span class="v">${esc(word)}</span>${TL_ICON.caret}</button>`);
  }
  const pin = $('#task-pin');
  if (pin) {
    const on = TASK_PINS.some(p => p.hash === tasksPinHash(state));
    pin.setAttribute('aria-pressed', String(on));
    pin.textContent = on ? 'Pinned' : 'Pin';
    pin.title = on ? 'Unpin from the sidebar' : 'Pin to the sidebar';
  }
  const mode = $('#task-select-mode');
  if (mode) {
    mode.hidden = !tasksCanSelect() || !['list', 'foryou', 'done'].includes(state.view);
    mode.setAttribute('aria-pressed', String(!!state.selectMode));
    mode.textContent = state.selectMode ? 'Done' : 'Select';
  }
}
// One popover for every menu on the page: a filter's values, the fields, Group by, and the bulk actions.
// ↑/↓ move between its items (and in and out of its find box), Home/End jump, Enter picks, Esc closes.
function tasksMenu(state, anchor, html, bind, toggle = true) {
  const pop = $('#task-filter-pop');
  if (!pop) return;
  if (toggle && pop.matches(':popover-open') && state.menuAnchor === anchor) { pop.hidePopover(); return; }
  state.menuAnchor = anchor;
  pop.innerHTML = html;
  pop.onclick = pop.onchange = null;
  bind?.(pop);
  if (!pop.matches(':popover-open')) pop.showPopover();
  tasksMenuPlace(pop, anchor);
  (pop.querySelector('input[type=search], input[type=text]') || pop.querySelector('[aria-checked="true"], input:checked') || pop.querySelector('.tl-mi, .tl-opt input') || pop).focus();
}
function tasksMenuKeys(ev) {
  const pop = ev.currentTarget;
  const items = [...pop.querySelectorAll('.tl-mi, .tl-opt input, .tl-menu-q, .tl-menu-new input')].filter(el => !el.closest('[hidden]'));
  const at = items.indexOf(document.activeElement);
  const go = i => { ev.preventDefault(); items[Math.max(0, Math.min(items.length - 1, i))]?.focus(); };
  if (ev.key === 'ArrowDown') go(at + 1);
  else if (ev.key === 'ArrowUp') go(at - 1);
  else if (ev.key === 'Home' && !/^(search|text)$/.test(document.activeElement?.type || '')) go(0);
  else if (ev.key === 'End' && !/^(search|text)$/.test(document.activeElement?.type || '')) go(items.length - 1);
  else if (ev.key === 'Enter' && document.activeElement?.matches('.tl-opt input')) { ev.preventDefault(); document.activeElement.click(); }
}
function tasksMenuPlace(pop, anchor) {
  if (matchMedia(PHONE).matches || !anchor?.isConnected) { pop.style.top = pop.style.left = ''; return; }
  const r = anchor.getBoundingClientRect();
  const up = anchor.closest('#task-bulk');
  pop.style.top = `${Math.max(8, up ? r.top - pop.offsetHeight - 6 : Math.min(r.bottom + 6, innerHeight - pop.offsetHeight - 8))}px`;
  pop.style.left = `${Math.max(8, Math.min(r.left, innerWidth - pop.offsetWidth - 8))}px`;
}
function tasksMenuClose() { const pop = $('#task-filter-pop'); if (pop?.matches(':popover-open')) pop.hidePopover(); }
// The value list for one filter: a search when it is long, a check per value; changes apply as they are ticked.
function tasksFilterMenu(state, field, anchor, toggle = true) {
  const name = TASK_FILTER_FIELDS.find(([k]) => k === field)?.[1] || field;
  const opts = tasksFilterOptions(state, field);
  const chosen = state.filters[field];
  const html = `<div class="tl-menu" role="group" aria-label="${esc(name)}">
    <div class="tl-menu-h" aria-hidden="true">${esc(name)}</div>
    ${opts.length > 7 ? `<input type="search" class="tl-menu-q" placeholder="Find ${esc(name.toLowerCase())}…" aria-label="Find ${esc(name.toLowerCase())}" autocomplete="off" spellcheck="false">` : ''}
    <div class="tl-menu-list">${opts.map(o => `<label class="tl-opt" data-label="${esc(o.label.toLowerCase())}"><input type="checkbox" name="tl-${field}" value="${esc(o.value)}"${chosen.includes(o.value) ? ' checked' : ''}>${o.face ? `<span class="tl-opt-face">${o.face}</span>` : ''}<span>${esc(o.label)}</span></label>`).join('') || '<div class="tl-menu-none">None loaded</div>'}</div>
  </div>`;
  tasksMenu(state, anchor, html, pop => {
    const q = pop.querySelector('.tl-menu-q');
    if (q) q.oninput = () => { for (const o of pop.querySelectorAll('.tl-opt')) o.hidden = !o.dataset.label.includes(q.value.trim().toLowerCase()); };
    pop.onchange = ev => {
      const input = ev.target.closest('input[name]'); if (!input) return;
      state.filters[field] = [...pop.querySelectorAll(`input[name="tl-${field}"]:checked`)].map(i => i.value);
      tasksFiltersChanged(state);
      state.menuAnchor = $(`[data-chip-edit="${field}"]`) || $('#task-filter');
      tasksMenuPlace(pop, state.menuAnchor);
    };
  }, toggle);
}
function tasksFieldMenu(state, anchor) {
  const fields = TASK_FILTER_FIELDS;
  tasksMenu(state, anchor, `<div class="tl-menu" role="menu" aria-label="Add filter"><div class="tl-menu-h" aria-hidden="true">Filter</div>
    ${fields.map(([k, name]) => `<button type="button" role="menuitem" class="tl-mi" data-pick-field="${k}">${esc(name)}</button>`).join('')}</div>`, pop => {
    pop.onclick = ev => {
      const b = ev.target.closest('[data-pick-field]'); if (!b) return;
      tasksFilterMenu(state, b.dataset.pickField, anchor, false);
    };
  });
}
function tasksGroupMenu(state, anchor) {
  const by = tasksGroupBy(state);
  tasksMenu(state, anchor, `<div class="tl-menu" role="menu" aria-label="Group by"><div class="tl-menu-h" aria-hidden="true">Group by</div>
    ${TASK_GROUPS.map(([k, label]) => `<button type="button" role="menuitemradio" aria-checked="${k === by}" class="tl-mi" data-pick-group="${k}"><span>${esc(label)}</span>${k === by ? '<span class="tl-mi-on" aria-hidden="true">✓</span>' : ''}</button>`).join('')}</div>`, pop => {
    pop.onclick = ev => {
      const b = ev.target.closest('[data-pick-group]'); if (!b) return;
      pop.hidePopover();
      state.group = {...state.group, [state.view]: b.dataset.pickGroup};
      tasksRemember(state); tasksTools(state); tasksRender(state);
      $('#task-group')?.focus();
    };
  });
}
function tasksFiltersChanged(state) {
  state.memo = new Map();
  tasksURLWrite(state); tasksTools(state); tasksRender(state);
}

// ---- remembered per viewer: the group, the folded groups and the folded parents (the person's preference, else this browser)
function tasksPrefsFromLocal(state) {
  try {
    const raw = JSON.parse(localStorage.getItem('hub.tasks.layout') || '{}');
    tasksPrefsApply(state, raw);
  } catch {}
}
function tasksPrefsApply(state, value) {
  if (value?.group && typeof value.group === 'object') state.group = {...state.group, ...value.group};
  if (Array.isArray(value?.collapsed)) state.collapsed = new Set(value.collapsed.map(String));
  if (Array.isArray(value?.folded)) state.folded = new Set(value.folded.map(String));
}
const tasksPrefsValue = state => ({group: state.group, collapsed: [...state.collapsed].slice(-200), folded: [...state.folded].slice(-200)});

// ---- selection and bulk changes. What is selected is always what can be seen selected: a filter, a search or a fold
// that hides a row drops it from the selection on the next draw.
function tasksVisibleKeys() {
  return [...document.querySelectorAll('#task-body [data-task-key]')].filter(el => el.getClientRects().length).map(el => el.dataset.taskKey);
}
function tasksSelectionPrune(state) {
  if (!state.selected.size) return;
  const seen = new Set(tasksVisibleKeys());
  for (const k of [...state.selected]) if (!seen.has(k)) state.selected.delete(k);
  for (const k of [...(state.bulkFail?.keys() || [])]) if (!seen.has(k)) state.bulkFail.delete(k);
}
function tasksSelectionPaint(state) {
  const body = $('#task-body'); if (!body) return;
  body.classList.toggle('has-sel', state.selected.size > 0 || !!state.selectMode);
  for (const row of body.querySelectorAll('[data-task-key]')) {
    const on = state.selected.has(row.dataset.taskKey);
    row.classList.toggle('sel', on);
    const box = row.querySelector('.tl-check'); if (box) box.checked = on;
    const open = row.querySelector('[data-open-task]');
    if (on) open?.setAttribute('aria-describedby', 'tl-sel-word'); else open?.removeAttribute('aria-describedby');
  }
  tasksBulkBar(state);
}
function tasksSelect(state, key, how = 'toggle') {
  if (!tasksCanSelect()) return;
  if (how === 'range' && state.anchor) {
    const keys = tasksVisibleKeys(), a = keys.indexOf(state.anchor), b = keys.indexOf(key);
    if (a >= 0 && b >= 0) { for (const k of keys.slice(Math.min(a, b), Math.max(a, b) + 1)) state.selected.add(k); tasksSelectionPaint(state); return; }
  }
  if (state.selected.has(key)) state.selected.delete(key); else state.selected.add(key);
  state.anchor = key;
  if (state.bulkMsg && !state.bulkUndo) state.bulkMsg = '';
  tasksSelectionPaint(state);
}
function tasksSelectionClear(state) {
  state.selected.clear(); state.bulkFail = new Map(); state.bulkMsg = ''; state.bulkUndo = null;
  tasksSelectionPaint(state);
  for (const row of document.querySelectorAll('#task-body .tl-row.failed')) row.classList.remove('failed');
  document.querySelectorAll('#task-body .tl-failed').forEach(el => el.remove());
}
const BULK_STATUSES = ['open', 'doing', 'waiting', 'review', 'ready', 'done', 'declined'];
function tasksBulkBar(state) {
  const bar = $('#task-bulk'); if (!bar) return;
  const n = state.selected.size;
  const undo = state.bulkUndo && state.bulkUndo.until > Date.now();
  const show = tasksCanSelect() && (n || state.bulkMsg);
  bar.hidden = !show;
  if (!show) { bar.innerHTML = ''; bar.dataset.html = ''; return; }
  const busy = !!state.bulkBusy;
  const html = `${n ? `<span class="tl-bulk-n tnum">${n} selected</span>
    <button type="button" data-bulk="status" aria-haspopup="true"${busy ? ' disabled' : ''}>Status</button>
    <button type="button" data-bulk="owner" aria-haspopup="true"${busy ? ' disabled' : ''}>Owner</button>
    <button type="button" data-bulk="tags" aria-haspopup="true"${busy ? ' disabled' : ''}>Tags</button>
    <button type="button" data-bulk="close" class="danger"${busy ? ' disabled' : ''}>Close</button>` : ''}
    <span class="tl-bulk-msg${state.bulkFail?.size ? ' err' : ''}" role="status" aria-live="polite">${esc(state.bulkMsg || '')}</span>
    ${undo ? '<button type="button" data-bulk="undo" class="tl-bulk-undo">Undo</button>' : ''}
    <button type="button" data-bulk="clear" class="tl-bulk-x" aria-label="${n ? 'Clear selection' : 'Dismiss'}" title="Clear (Esc)">${TL_ICON.x}<span>${n ? 'Clear' : ''}</span></button>`;
  if (bar.dataset.html === html) return;          // the poll redraws the list; a focused button here keeps its focus
  const focused = bar.contains(document.activeElement) ? document.activeElement.dataset.bulk : '';
  bar.innerHTML = bar.dataset.html = html;
  if (focused) (bar.querySelector(`[data-bulk="${focused}"]`) || bar.querySelector('[data-bulk]'))?.focus();
}
// the selected tasks, in the order they show (the selection only ever holds visible rows)
function tasksSelectedTasks(state) {
  const byId = tasksById(state);
  return tasksVisibleKeys().filter(k => state.selected.has(k)).map(k => byId.get(k.slice(1))).filter(Boolean);
}
const tasksSelectedTitle = (state, key) => tasksById(state).get(key.slice(1))?.title || key.slice(1);
const asksForResult = t => String(t.owner || '').startsWith('human:') && String(t.requester || '').startsWith('bot:') && !['done', 'declined'].includes(String(t.status));
// Each task is its own update (POST /v2/tasks/{id}) with its latest version; a 409 refetches it and tries once more.
// A failure is named, kept selected and marked; nothing is dropped quietly. A bot's request gets its own result note.
async function tasksBulkApply(state, describe, bodyFor) {
  const tasks = tasksSelectedTasks(state);
  if (!tasks.length || state.bulkBusy || !tasksCanSelect()) return;
  tasksMenuClose();
  const finishing = describe.status === 'done' || describe.close;
  state.bulkBusy = true; state.bulkFail = new Map(); state.bulkUndo = null; state.bulkMsg = `Updating ${tasks.length}…`;
  tasksBulkBar(state);
  const failed = new Map(), closed = [];
  let changed = 0, already = 0, skipped = 0;
  for (let t of tasks) {
    let body = bodyFor(t);
    if (body === null) { already++; continue; }
    if (finishing && asksForResult(t)) {
      const note = await taskOutcomeNote(t, describe.close ? {close: true} : {status: 'done'});
      if (note === null) { skipped++; continue; }
      if (note) body = {...body, note};
    }
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        await post(`/v2/tasks/${encodeURIComponent(t.id)}`, {version: t.version, ...body});
        changed++;
        if (describe.close) closed.push({id: t.id, title: t.title, status: t.status, step_id: t.step_id || '', owner: t.owner, typed: pipelineTypeId(t) !== 'general',
          told: !!body.note || !!actorSlug(taskRequester(t))});
        break;
      } catch (e) {
        if (e.status === 409 && attempt === 0) {
          const fresh = (await v2Get(`/v2/tasks/${encodeURIComponent(t.id)}`))?.task;
          if (fresh) {
            const again = bodyFor(fresh);
            if (again === null) { already++; break; }
            body = {...again, ...(body.note ? {note: body.note} : {})}; t = fresh; continue;
          }
        }
        failed.set('t' + t.id, e.message || 'Not saved');
        break;
      }
    }
  }
  if (TASKS_ST !== state) return;
  state.bulkBusy = false;
  state.bulkFail = failed;
  state.selected = new Set(failed.keys());
  const names = [...failed.keys()].map(k => tasksSelectedTitle(state, k));
  state.bulkMsg = [`${changed} ${describe.done}`, already ? `${already} already ${describe.already || 'so'}` : '', skipped ? `${skipped} skipped` : '',
    failed.size ? `${failed.size} not changed: ${names.slice(0, 2).join(', ')}${names.length > 2 ? ` +${names.length - 2}` : ''}` : ''].filter(Boolean).join(', ');
  if (closed.length) state.bulkUndo = {items: closed, until: Date.now() + 10000};
  if (failed.size) toast(`${failed.size} not changed: ${[...failed.entries()].map(([k, m]) => `${tasksSelectedTitle(state, k)} (${m})`).join('; ')}`, true);
  await tasksLoad(state);
  if (TASKS_ST !== state) return;
  tasksSelectionPaint(state);
  tasksBulkSettle(state, closed.length ? 10000 : 4000);
}
// The result line stays a moment (Undo for ten seconds after a Close), then goes.
function tasksBulkSettle(state, ms) {
  clearTimeout(state.bulkTimer);
  state.bulkTimer = setTimeout(() => {
    if (TASKS_ST !== state) return;
    state.bulkUndo = null;
    if (!state.selected.size && !state.bulkFail.size) state.bulkMsg = '';
    tasksBulkBar(state);
  }, ms);
}
// Undo puts each closed task back as it was: its status (or step), and its owner if that moved. A bot that asked for
// the task was already told it closed; the message says so rather than pretend. Nothing else can start meanwhile.
async function tasksBulkUndo(state) {
  if (state.bulkBusy) return;
  const items = state.bulkUndo?.items || [];
  state.bulkUndo = null; state.bulkBusy = true; state.bulkMsg = `Reopening ${items.length}…`; tasksBulkBar(state);
  let ok = 0;
  const failed = [];
  for (const prev of items) {
    try {
      const fresh = (await get(`/v2/tasks/${encodeURIComponent(prev.id)}`)).task;
      const body = prev.typed && prev.step_id ? {step: prev.step_id} : {status: prev.status};
      if (fresh.owner !== prev.owner) body.owner = actorSlug(prev.owner) || prev.owner;
      await post(`/v2/tasks/${encodeURIComponent(prev.id)}`, {version: fresh.version, ...body});
      ok++;
    } catch (e) { failed.push({id: prev.id, title: prev.title, why: e.message}); }
  }
  if (TASKS_ST !== state) return;
  state.bulkBusy = false;
  const told = items.some(p => p.told);
  const names = failed.map(f => f.title || tasksSelectedTitle(state, 't' + f.id));
  state.bulkMsg = [failed.length ? `${ok} of ${items.length} reopened` : told ? 'Reopened' : `${ok} reopened`, told ? 'bots were already told' : '',
    failed.length ? `${failed.length} not reopened: ${names.join(', ')}` : ''].filter(Boolean).join('; ');
  state.bulkFail = new Map(failed.map(f => ['t' + f.id, f.why || 'Not reopened']));
  if (told || failed.length) toast(state.bulkMsg, !!failed.length);
  await tasksLoad(state);
  tasksBulkBar(state);
  tasksBulkSettle(state, 4000);
}
function tasksBulkMenu(state, kind, anchor) {
  if (kind === 'clear') { tasksSelectionClear(state); return; }
  if (kind === 'undo') { void tasksBulkUndo(state); return; }
  if (kind === 'close') { void tasksBulkApply(state, {close: true, done: 'closed', already: 'closed'}, t => t.status === 'closed' ? null : {close: true}); return; }
  if (kind === 'status') {
    tasksMenu(state, anchor, `<div class="tl-menu" role="menu" aria-label="Set status"><div class="tl-menu-h" aria-hidden="true">Status</div>
      ${BULK_STATUSES.map(s => `<button type="button" role="menuitem" class="tl-mi" data-bulk-status="${s}"><span>${esc(STATUS_WORD[s] || s)}</span></button>`).join('')}</div>`, pop => {
      pop.onclick = ev => {
        const b = ev.target.closest('[data-bulk-status]'); if (!b) return;
        const status = b.dataset.bulkStatus, word = STATUS_WORD[status] || status;
        void tasksBulkApply(state, {status, done: `moved to ${word}`, already: word}, t => t.status === status ? null : {status});
      };
    });
  } else if (kind === 'owner') {
    const opts = new DOMParser().parseFromString(`<select>${taskOwnerOptions('')}</select>`, 'text/html').querySelectorAll('option[value]:not([value=""])');
    tasksMenu(state, anchor, `<div class="tl-menu" role="group" aria-label="Set owner"><div class="tl-menu-h" aria-hidden="true">Owner</div>
      <input type="search" class="tl-menu-q" placeholder="Find…" aria-label="Find an owner" autocomplete="off" spellcheck="false">
      <div class="tl-menu-list">${[...opts].map(o => {
        const actor = o.value.includes(':') ? o.value : 'bot:' + o.value;
        return `<button type="button" class="tl-mi tl-opt" data-label="${esc(o.textContent.toLowerCase())}" data-bulk-owner="${esc(o.value)}"><span class="tl-opt-face">${actorFace(actor, 16)}</span><span>${esc(o.textContent)}</span></button>`;
      }).join('')}</div></div>`, pop => {
      const q = pop.querySelector('.tl-menu-q');
      q.oninput = () => { for (const o of pop.querySelectorAll('.tl-opt')) o.hidden = !o.dataset.label.includes(q.value.trim().toLowerCase()); };
      pop.onclick = ev => {
        const b = ev.target.closest('[data-bulk-owner]'); if (!b) return;
        const owner = b.dataset.bulkOwner, actor = owner.includes(':') ? owner : 'bot:' + owner;
        void tasksBulkApply(state, {done: `handed to ${actorLabel(actor)}`, already: `with ${actorLabel(actor)}`}, t => t.owner === actor ? null : {owner});
      };
    });
  } else if (kind === 'tags') {
    const tasks = tasksSelectedTasks(state);
    const keys = tasksFilterOptions(state, 'tag');
    tasksMenu(state, anchor, `<div class="tl-menu" role="group" aria-label="Tags"><div class="tl-menu-h" aria-hidden="true">Tags</div>
      <form class="tl-menu-new"><input type="text" placeholder="New tag" aria-label="New tag" autocomplete="off" spellcheck="false"></form>
      <div class="tl-menu-list">${keys.map(o => {
        const all = tasks.every(t => (t.labels || []).includes(o.value));
        return `<button type="button" aria-pressed="${all}" class="tl-mi" data-bulk-tag="${esc(o.value)}"><span class="tl-dot" style="--hue:${tagHue(o.value)}"></span><span>${esc(o.label)}</span>${all ? '<span class="tl-mi-on" aria-hidden="true">✓</span>' : ''}</button>`;
      }).join('')}</div></div>`, pop => {
      const apply = (tag, remove) => void tasksBulkApply(state, {done: remove ? `untagged ${tag}` : `tagged ${tag}`, already: remove ? 'untagged' : 'tagged'}, t => {
        const has = (t.labels || []).includes(tag);
        if (remove ? !has : has) return null;
        return {labels: remove ? t.labels.filter(l => l !== tag) : [...(t.labels || []), tag]};
      });
      pop.querySelector('.tl-menu-new').onsubmit = ev => {
        ev.preventDefault();
        const tag = ev.target.querySelector('input').value.trim().toLowerCase();
        if (tag) apply(tag, false);
      };
      pop.onclick = ev => {
        const b = ev.target.closest('[data-bulk-tag]'); if (!b) return;
        apply(b.dataset.bulkTag, b.getAttribute('aria-pressed') === 'true');
      };
    });
  }
}

// ---- the side peek: the task beside the list (the modal's content, narrower); Esc closes, j/k moves it.
// On a phone it is a full-screen sheet: modal (the list behind it is inert), and Back closes it.
function taskPeek() {
  let d = $('#task-peek');
  if (d) return d;
  d = document.createElement('dialog');
  d.id = 'task-peek'; d.className = 'tmodal task-peek'; d.dataset.peek = '1';
  d.setAttribute('aria-label', 'Task');
  taskDialogWire(d);
  d.addEventListener('close', () => {           // the event lands a moment later: the peek may be open again
    if (d.open) return;
    const lost = !document.activeElement || document.activeElement === document.body || d.contains(document.activeElement);
    taskPeekClosed();
    if (lost && TASKS_ST) tasksFocusCursor(TASKS_ST);
  });
  d.addEventListener('cancel', ev => { ev.preventDefault(); if (TASKS_ST) taskPeekClose(TASKS_ST); else d.close(); });
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
let PEEK_BACK = false;
function taskPeekOpen(state, key, opts = {}) {
  const task = tasksById(state).get(String(key).slice(1));
  if (!task) return;
  const d = taskPeek();
  state.peek = key;
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
    taskModalBind(d, task);
    state.peekTimer = setTimeout(() => { if (state.peek === key && d.open) void taskModalShow(task, d); }, 160);
  } else void taskModalShow(task, d);
  if (opts.focus || matchMedia(PHONE).matches) setTimeout(() => d.querySelector('.tmodal-title')?.focus({preventScroll: true}), 0);
}
function taskPeekClose(state) {
  const d = $('#task-peek');
  const lost = d?.contains(document.activeElement);
  if (d?.open) d.close();
  taskPeekClosed();
  if (lost || !document.activeElement || document.activeElement === document.body) tasksFocusCursor(state);
}
// After a reload: a peek showing a task that changed elsewhere redraws, unless you are typing or picking in it.
function tasksPeekSync(state) {
  const d = $('#task-peek');
  if (!d?.open || !state.peek) return;
  const fresh = tasksById(state).get(state.peek.slice(1));
  if (!fresh || Number(fresh.version) <= Number(d.dataset.version) || d.saving) return;
  const busy = d.contains(document.activeElement) && document.activeElement.matches('input, textarea, select, [contenteditable]');
  if (busy || $('.prop-pop:popover-open', d)) return;
  void taskModalShow(fresh, d);
}
window.addEventListener('popstate', () => {
  PEEK_BACK = false;
  const d = $('#task-peek');
  if (d?.open && !history.state?.taskPeek && TASKS_ST) taskPeekClose(TASKS_ST);
});

// ---- the cursor and the keys: j/k or ↑/↓ move, Enter opens, ←/→ fold, c creates, / searches, x selects, Esc closes or clears.
// One row is in the Tab order at a time (the cursor's), so the list is one Tab stop, not three per row.
function tasksCursorPaint(state) {
  for (const el of document.querySelectorAll('#task-body .is-cursor')) el.classList.remove('is-cursor');
  const el = state.cursor ? document.querySelector(`#task-body [data-task-key="${CSS.escape(state.cursor)}"]`) : null;
  el?.classList.add('is-cursor');
  const tabKey = el ? state.cursor : tasksVisibleKeys()[0] || '';
  state.tabKey = tabKey;
  for (const b of document.querySelectorAll('#task-body [data-open-task]')) b.tabIndex = b.closest('[data-task-key]')?.dataset.taskKey === tabKey ? 0 : -1;
  return el;
}
function tasksFocusCursor(state) {
  const el = tasksCursorPaint(state) || document.querySelector(`#task-body [data-task-key="${CSS.escape(state.tabKey || '')}"]`);
  el?.querySelector('[data-open-task]')?.focus({preventScroll: true});
}
function tasksMove(state, step) {
  const keys = tasksVisibleKeys();
  if (!keys.length) return;
  const at = keys.indexOf(state.cursor);
  const next = keys[at < 0 ? (step > 0 ? 0 : keys.length - 1) : Math.max(0, Math.min(keys.length - 1, at + step))];
  state.cursor = next;
  const el = tasksCursorPaint(state);
  el?.querySelector('[data-open-task]')?.focus({preventScroll: true});
  el?.scrollIntoView({block: 'nearest'});
  if (state.peek && next !== state.peek) taskPeekOpen(state, next, {quick: true});
}
function tasksCreatePrefill(state) {
  const owners = state.filters.owner || [];
  const owner = owners.length === 1 ? resolveMe(owners[0]) : '';
  return {owner: actorSlug(owner) || owner, labels: (state.filters.tag || []).slice(0, 1)};
}
function tasksFold(state, key, open) {
  const has = !!document.querySelector(`#task-body [data-expand="${CSS.escape(key)}"]`);
  if (!has) return false;
  if (open === !state.folded.has(key)) return false;
  open ? state.folded.delete(key) : state.folded.add(key);
  tasksRemember(state); tasksRender(state);
  return true;
}
function tasksKeys(state, ev) {
  if (TASKS_ST !== state) return;
  if (ev.defaultPrevented || ev.metaKey || ev.ctrlKey || ev.altKey || ev.isComposing) return;
  if (document.querySelector('dialog:modal')) return;                    // the full task, a new task, a result prompt, a phone's sheet
  if (document.querySelector(':popover-open')) return;                   // a menu has the keys (Esc closes it)
  const el = document.activeElement;
  const typing = el && (el.isContentEditable || ['TEXTAREA', 'SELECT'].includes(el.tagName)
    || (el.tagName === 'INPUT' && !['checkbox', 'radio', 'button', 'submit', 'reset'].includes(el.type)));
  if (typing) return;
  const inPeek = !!el?.closest?.('#task-peek');
  const listy = ['list', 'foryou', 'done', 'board'].includes(state.view);
  const key = ev.key;
  const inTabs = !!el?.closest?.('[role=tablist]');
  if ((key === 'j' || (key === 'ArrowDown' && !inPeek && !inTabs)) && listy) { ev.preventDefault(); tasksMove(state, 1); return; }
  if ((key === 'k' || (key === 'ArrowUp' && !inPeek && !inTabs)) && listy) { ev.preventDefault(); tasksMove(state, -1); return; }
  if ((key === 'ArrowRight' || key === 'ArrowLeft') && !inPeek && !inTabs && state.cursor && el?.closest?.('.tl-row')) {
    const open = key === 'ArrowRight';
    if (tasksFold(state, state.cursor, open)) { ev.preventDefault(); tasksFocusCursor(state); return; }
    if (!open) {   // on a subtask, ← goes to its parent
      const pid = tasksById(state).get(state.cursor.slice(1))?.parent_id;
      if (pid && document.querySelector(`#task-body [data-task-key="t${CSS.escape(String(pid))}"]`)) { ev.preventDefault(); state.cursor = 't' + pid; tasksFocusCursor(state); }
    }
    return;
  }
  if (key === '/') { ev.preventDefault(); $('#task-q')?.focus(); $('#task-q')?.select(); return; }
  if (key === 'c' && !ev.shiftKey) { ev.preventDefault(); const p = tasksCreatePrefill(state); openTaskCreate(p.owner, {labels: p.labels}); return; }
  if (key === 'x' && listy && state.cursor && tasksCanSelect()) { ev.preventDefault(); tasksSelect(state, state.cursor); return; }
  if (key === 'Enter' && listy && state.cursor && (!el || el === document.body || el.closest?.('.tl-row,.bcard') && !el.matches('button,a,input'))) {
    ev.preventDefault(); taskPeekOpen(state, state.cursor); return;
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
function tasksBodyClick(state, ev) {
  const box = ev.target.closest('[data-select]');
  if (box) {
    ev.stopPropagation();
    const key = box.dataset.select;
    if (ev.shiftKey && state.anchor && state.anchor !== key) { box.checked = true; tasksSelect(state, key, 'range'); }
    else { if (box.checked !== state.selected.has(key)) tasksSelect(state, key); }
    state.cursor = key; tasksCursorPaint(state);
    return true;
  }
  const exp = ev.target.closest('[data-expand]');
  if (exp) {
    const key = exp.dataset.expand;
    state.folded.has(key) ? state.folded.delete(key) : state.folded.add(key);
    tasksRemember(state); tasksRender(state);
    $(`#task-body [data-expand="${CSS.escape(key)}"]`)?.focus();
    return true;
  }
  const toggle = ev.target.closest('[data-group-toggle]');
  if (toggle) {
    const k = toggle.dataset.groupToggle;
    state.collapsed.has(k) ? state.collapsed.delete(k) : state.collapsed.add(k);
    tasksRemember(state); tasksRender(state);
    $(`#task-body [data-group-toggle="${CSS.escape(k)}"]`)?.focus();
    return true;
  }
  const addIn = ev.target.closest('[data-group-add]');
  if (addIn) {
    const g = tasksGroupsFor(taskItems(state), tasksGroupBy(state), state).find(x => x.key === addIn.dataset.groupAdd);
    const c = g?.create || {};
    if (c.parent) openTaskCreate(c.parent.owner ? (actorSlug(c.parent.owner) || c.parent.owner) : '', {parent: c.parent});
    else openTaskCreate(c.owner ? (actorSlug(c.owner) || c.owner) : '', {labels: c.labels || []});
    return true;
  }
  if (ev.target.closest('[data-clear-filters]')) {
    state.filters = tasksEmptyFilters();
    state.q = ''; if ($('#task-q')) $('#task-q').value = '';
    tasksFiltersChanged(state);
    $('#task-filter')?.focus();
    return true;
  }
  const row = ev.target.closest('.tl-row,.bcard');
  if (!row || ev.target.closest('a[href]')) return false;
  const key = row.dataset.taskKey;
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
  row.querySelector('[data-open-task]')?.focus({preventScroll: true});
  taskPeekOpen(state, key);
  return true;
}
// The peek finished its task (Done, Close): the list moves on to the next row and the peek follows it.
function tasksAfterFinish(state, oldKey, at) {
  if (TASKS_ST !== state) return false;
  if (document.querySelector(`#task-body [data-task-key="${CSS.escape(oldKey)}"]`)) return false;
  const keys = tasksVisibleKeys();
  if (!keys.length) { taskPeekClose(state); $('#task-q')?.focus(); return true; }
  state.cursor = keys[Math.max(0, Math.min(keys.length - 1, at))];
  tasksFocusCursor(state);
  taskPeekOpen(state, state.cursor);
  return true;
}
