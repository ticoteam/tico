/* ui/app/task-list-menus.ts — the Tasks toolbar and its menus: whose list, the scope, the finished views, the header fit, the ⋯ menu,
   the filter chips and their value menus, and the one popover they share.
   Split out of task-list.ts. Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A menu's contents are wired by its caller once they are drawn in the shared popover (tasksMenu).
type TasksMenuBind = (pop: HTMLElement) => void;
// The page whose state keeps the open menu: the Tasks page's, or another page's that borrows the menu (the roadmap's).
// `& object`: any page state will do; only these two fields are read and written.
type TasksMenuHost = Pick<TaskState, 'menuAnchor' | 'menuField'> & object;
type TaskPerson = (typeof S.people)[number];

// The picker: On their plate / Asked by them, a find box (Enter takes the first match), Me and Everyone, then people
// by team (yours first), each with a count; the bots fold behind one line until opened or searched for.
function tasksWhoMenu(state: TaskState, anchor: HTMLElement, toggle = true) {
  const asked = state.side === 'asked', key = tasksWhoKey(state), me = myActor();
  const active = taskItems(state, 'list');
  const count = (actor: string) => active.filter(it => asked ? taskAskedBy(it.task, actor) : taskOnPlate(it.task, actor)).length;
  const opt = (value: string, face: string, label: string, n: number, find: string) => `<button type="button" class="tl-mi" data-who="${esc(value)}" data-label="${esc(find.toLowerCase())}"${value === key ? ' aria-current="true"' : ''}><span class="tl-opt-face">${face}</span><span class="tl-who-name">${esc(label)}</span>${n ? `<span class="tl-mi-n tnum">${n}</span>` : ''}${value === key ? '<span class="tl-mi-on" aria-hidden="true">✓</span>' : ''}</button>`;
  const mine = mePerson();
  const people = (S.people || []).filter(p => !p.hidden && 'human:' + p.id !== me);
  const teams = new Map();
  for (const p of people) { const team = p.team || ''; if (!teams.has(team)) teams.set(team, []); teams.get(team).push(p); }
  const order = [...teams.keys()].sort((a, b) => Number(b === (mine?.team || null)) - Number(a === (mine?.team || null)) || Number(a === '') - Number(b === '') || taskTeamLabel(a).localeCompare(taskTeamLabel(b)));
  const name = (p: TaskPerson) => p.name || titleCase(p.id);
  const bots = (S.emps || []).filter(e => e.status !== 'archived' && !isHiddenBot(e.id))
    .sort((a, b) => String(botDisplayName(a.id)).localeCompare(String(botDisplayName(b.id))));
  const html = `<div class="tl-menu tl-who-menu" role="group" aria-label="Whose tasks">
    <div class="tl-seg" role="group" aria-label="Show">${[['plate', 'On their plate'], ['asked', 'Asked by them']].map(([k, label]) =>
      `<button type="button" data-who-side="${k}" aria-pressed="${(k === 'asked') === asked}">${esc(label)}</button>`).join('')}</div>
    <input type="search" class="tl-menu-q" placeholder="Find a person" aria-label="Find a person" autocomplete="off" spellcheck="false">
    <div class="tl-menu-list">
      ${opt('me', actorFace(me, 16), 'Me', asked ? count(me) : tasksMyCount(), `me you ${S.me?.name || ''}`)}
      ${asked ? '' : opt('all', `<span class="tl-gglyph">${TL_ICON.team}</span>`, 'Everyone', active.length, 'everyone all')}
      ${order.map(team => `<div class="tl-who-team"><div class="tl-menu-h" aria-hidden="true">${esc(taskTeamLabel(team))}</div>
        ${teams.get(team).sort((a: TaskPerson, b: TaskPerson) => name(a).localeCompare(name(b))).map((p: TaskPerson) => opt('human:' + p.id, actorFace('human:' + p.id, 16), name(p), count('human:' + p.id), `${name(p)} ${p.email || ''} ${taskTeamLabel(team)}`)).join('')}</div>`).join('')}
      ${bots.length ? `<button type="button" class="tl-mi tl-who-more" data-who-bots>${bots.length} bot${bots.length === 1 ? '' : 's'}</button>
        <div class="tl-who-team tl-who-bots" hidden><div class="tl-menu-h" aria-hidden="true">Bots</div>
        ${bots.map(e => opt('bot:' + e.id, actorFace('bot:' + e.id, 16), botDisplayName(e.id), count('bot:' + e.id), `${botDisplayName(e.id)} ${e.slug}`)).join('')}</div>` : ''}
      <div class="tl-menu-none" hidden>Nobody by that name</div>
    </div></div>`;
  tasksMenu(state, anchor, html, pop => {
    // The bots' line and its list are drawn together, or not at all.
    const q = pop.querySelector<HTMLInputElement>('.tl-menu-q')!, box = pop.querySelector<HTMLElement>('.tl-who-bots'), more = pop.querySelector<HTMLElement>('[data-who-bots]');
    if (box && more && String(key).startsWith('bot:')) { box.hidden = false; more.hidden = true; }
    const open = box ? !box.hidden : false;
    q.oninput = () => {
      const words = q.value.trim().toLowerCase();
      for (const b of pop.querySelectorAll<HTMLElement>('[data-who]')) b.hidden = !!words && !b.dataset.label!.includes(words);
      if (box && more) { box.hidden = !words && !open; more.hidden = !!words || open; }
      for (const team of pop.querySelectorAll<HTMLElement>('.tl-who-team:not(.tl-who-bots)')) team.hidden = !team.querySelector('[data-who]:not([hidden])');
      if (box && words && !box.querySelector('[data-who]:not([hidden])')) box.hidden = true;
      pop.querySelector<HTMLElement>('.tl-menu-none')!.hidden = !words || !![...pop.querySelectorAll<HTMLElement>('[data-who]')].some(b => !b.hidden && !b.closest('[hidden]'));
    };
    q.onkeydown = ev => {
      if (ev.key !== 'Enter') return;
      ev.preventDefault();
      [...pop.querySelectorAll<HTMLElement>('[data-who]')].find(b => !b.hidden && !b.closest('[hidden]'))?.click();
    };
    pop.onclick = ev => {
      const side = eventElement(ev)?.closest<HTMLElement>('[data-who-side]');
      if (side) {
        if ((side.dataset.whoSide === 'asked') === asked) return;
        tasksWhoSet(state, state.who || 'me', side.dataset.whoSide);
        tasksWhoMenu(state, $('#task-who') || anchor, false);
        return;
      }
      if (eventElement(ev)?.closest('[data-who-bots]') && box && more) { box.hidden = false; more.hidden = true; box.querySelector<HTMLElement>('[data-who]')?.focus(); return; }
      const b = eventElement(ev)?.closest<HTMLElement>('[data-who]'); if (!b) return;
      pop.hidePopover();
      tasksWhoSet(state, b.dataset.who!);
      $('#task-who')?.focus();
    };
  }, toggle);
}
function tasksScopePaint(state: TaskState) {
  const b = $('#task-scope');
  if (!b) return false;
  const label = tasksScopeLabel(state), n = state.view === 'foryou' ? tasksCounts(state).foryou : null;
  pipelineEditSync(state.view === 'foryou' ? null : state.type);
  b.setAttribute('aria-label', n != null ? `${label}, ${n}` : label);
  $('#task-body')?.setAttribute('aria-label', label);
  return tasksPaint(b, `<span class="tl-scope-l">${esc(label)}</span>${n != null ? `<span class="cnt tnum${n && tasksNeedsQueue(state) ? ' hot' : ''}" aria-hidden="true">${n}</span>` : ''}${TL_ICON.caret}`);
}
// Its menu: You, then the types (task-pipelines.js). A type opens its open tasks in the layout last used.
function tasksScopeMenu(state: TaskState, anchor: HTMLElement) {
  const you = state.view === 'foryou', n = tasksCounts(state).foryou;
  const item = (attr: string, label: string, on: boolean, count = 0) => `<button type="button" role="menuitemradio" aria-checked="${on}" class="tl-mi" ${attr}><span>${esc(label)}</span>${count ? `<span class="tl-mi-n tnum">${count}</span>` : ''}${on ? '<span class="tl-mi-on" aria-hidden="true">✓</span>' : ''}</button>`;
  const sep = '<div class="tl-menu-sep" role="separator"></div>';
  const types: {id: string, label: string, other?: boolean}[] = /** @type {{id: string, label: string, other?: boolean}[]} */ (taskPipelineScopeTypes());
  tasksMenu(state, anchor, `<div class="tl-menu" role="menu" aria-label="Show">
    ${item('data-scope="you"', tasksViewLabel(state, 'foryou'), you, n)}${sep}
    ${types.map((t, i) => (t.other && !types[i - 1]?.other ? sep : '') + item(`data-task-type="${esc(t.id)}"`, t.label, !you && state.type === t.id)).join('')}</div>`, pop => {
    pop.onclick = ev => {
      const b = eventElement(ev)?.closest<HTMLElement>('[data-scope], [data-task-type]'); if (!b) return;
      pop.hidePopover();
      if (b.dataset.scope) { if (state.view !== 'foryou') tasksSetView(state, 'foryou'); }
      else tasksScopeType(state, b.dataset.taskType);
      $('#task-scope')?.focus();
    };
  });
  anchor.setAttribute('aria-expanded', 'true');
}
// The finished feed's own views, in the archive button's place: Done, Closed, and Snoozed while something is snoozed.
function tasksFinishedPaint(state: TaskState) {
  const seg = $('#task-finished');
  if (!seg) return;
  seg.hidden = !tasksArchived(state.view);
  if (seg.hidden) return;
  const snoozed = tasksCounts(state).snoozed || state.view === 'snoozed';
  tasksPaint(seg, [['done', 'Done'], ['closed', 'Closed'], ...(snoozed ? [['snoozed', 'Snoozed']] : [])].map(([k, label]) =>
    `<button type="button" data-view="${k}" aria-pressed="${state.view === k}">${label}</button>`).join(''));
}
// The header is one row. When it has no room, the search folds to its icon first, then the finished views move to the
// head of the chips row; only then (a phone with Catch up) does the row wrap. Measured again when the row is resized
// (tasks-page.js) and whenever its contents change.
function tasksHeadFit(state: TaskState) {
  const head = $('#tasks-pane .tl-head'), seg = $('#task-finished'), bar = $('#task-bar');
  if (!head || !seg || TASKS_ST !== state) return;
  const scope = head.querySelector('.tl-scope-l');
  head.classList.remove('s-fold', 'h-fold', 'wrap');
  if (seg.parentElement !== head.querySelector('.tl-finished-slot')) head.querySelector('.tl-finished-slot').append(seg);
  // A phone's scope may shorten to "Gene…"; a wider header folds the search before it cuts the scope's name.
  // Order of giving way: search to its icon, the finished segment to the chips row, a phone's title, then a second row.
  const phone = matchMedia(PHONE).matches;
  const over = () => head.scrollWidth - head.clientWidth > 1 || (!phone && scope && scope.scrollWidth - scope.clientWidth > 1);
  if (over()) head.classList.add('s-fold');
  if (!seg.hidden && over()) bar.querySelector('.tl-finished-bar').append(seg);
  if (phone && over()) head.classList.add('h-fold');    // a phone drops the title before it wraps
  if (head.scrollWidth - head.clientWidth > 1) head.classList.add('wrap');
  bar.hidden = !bar.querySelector('#task-finished:not([hidden]), .tl-chip');
  tasksBarEdge(bar);
}
function tasksMoreMenu(state: TaskState, anchor: HTMLElement) {
  const pinned = TASK_PINS.some(p => p.hash === tasksPinHash(state));
  const ranked = state.view !== 'foryou' && pipelineSelectedType(state)?.role === 'product-roadmap';
  tasksMenu(state, anchor, `<div class="tl-menu" role="menu" aria-label="More">
    ${tasksSelectable(state) ? `<button type="button" role="menuitemcheckbox" aria-checked="${!!state.selectMode}" class="tl-mi" id="task-select-mode">${TL_ICON.all}<span>Select tasks</span>${state.selectMode ? '<span class="tl-mi-on" aria-hidden="true">✓</span>' : ''}</button>` : ''}
    ${S.me?.role === 'bot' ? '' : `<button type="button" role="menuitem" class="tl-mi" id="task-stats">${TL_ICON.chart}<span>Stats</span></button>`}
    ${ranked ? `<a role="menuitem" class="tl-mi" id="task-ranked" href="#/roadmap">${TL_ICON.up}<span>Ranked</span></a>` : ''}
    <button type="button" role="menuitem" class="tl-mi" id="task-pin" data-pinned="${pinned}">${TL_ICON.pin}<span>${pinned ? 'Unpin' : 'Pin to sidebar'}</span></button></div>`, pop => {
    pop.onclick = ev => {
      const b = eventElement(ev)?.closest('#task-stats, #task-pin, #task-select-mode, #task-ranked'); if (!b) return;
      pop.hidePopover();
      if (b.id === 'task-ranked') return;
      if (b.id === 'task-select-mode') {
        state.selectMode = !state.selectMode;
        if (!state.selectMode) tasksSelectionClear(state);
        tasksTools(state); tasksSelectionPaint(state);
      } else if (b.id === 'task-stats') taskStatsOpen(state);
      else tasksPinToggle(state);
    };
  });
  anchor.setAttribute('aria-expanded', 'true');
}
function tasksPaint(el: HTMLElement | null, html: string) {
  if (!el || el.dataset.html === html) return false;
  const focused = el.contains(document.activeElement) ? document.activeElement as HTMLElement : null;
  const mark = focused && (focused.dataset.view ? `[data-view="${focused.dataset.view}"]` : focused.dataset.chipEdit ? `[data-chip-edit="${focused.dataset.chipEdit}"]`
    : focused.dataset.chipDrop ? `[data-chip-drop="${focused.dataset.chipDrop}"]` : focused.id ? '#' + CSS.escape(focused.id) : '');
  el.innerHTML = el.dataset.html = html;
  if (mark) el.querySelector<HTMLElement>(mark)?.focus();
  return true;
}
function tasksTools(state: TaskState) {
  const head = $('#tasks-pane .tl-head');
  if (!head || TASKS_ST !== state) return;
  taskPipelineTools(state);
  // Someone this install does not know (an old link, a person who left) falls back to Me; Everyone asks nothing itself.
  if (state.who && state.who !== 'me' && (!tasksWhoKnown(state.who) || (state.who === 'all' && state.side === 'asked'))) {
    state.who = 'me'; state.memo = new Map(); tasksURLWrite(state);
  }
  tasksScopePaint(state);
  // The personal queue always shows the whole plate: no search, no filters, only its grouping.
  const queue = tasksNeedsQueue(state), you = state.view === 'foryou', archived = tasksArchived(state.view);
  head.querySelector('.tl-search').hidden = queue;
  const who = $('#task-who-wrap');
  who.hidden = !you;
  if (!who.hidden) tasksPaint(who, tasksWhoButtonHTML(state));
  const chips = [];
  for (const [field, name] of queue ? [] : TASK_FILTER_FIELDS) {
    const values = state.filters[field] as string[] | undefined;
    if (!values?.length) continue;
    const opts = new Map(tasksFilterOptions(state, field).map(o => [o.value, o.label]));
    const words = values.map(v => opts.get(v) || (field === 'owner' || field === 'asked' ? actorLabel(v) : v));
    const shown = words.length > 2 ? `${words.slice(0, 2).join(', ')} +${words.length - 2}` : words.join(', ');
    chips.push(`<span class="tl-chip" data-chip="${field}"><button type="button" class="tl-chip-main" data-chip-edit="${field}" aria-haspopup="true" aria-label="${esc(`${name}: ${words.join(', ')}. Change`)}" title="${esc(`${name}: ${words.join(', ')}`)}"><span class="k">${esc(name)}</span><span class="v">${esc(shown)}</span></button><button type="button" class="tl-chip-x" data-chip-drop="${field}" aria-label="Remove ${esc(name)} filter">${TL_ICON.x}</button></span>`);
  }
  tasksPaint($('#task-chips'), chips.join(''));
  const count = queue ? 0 : tasksActiveFilterCount(state);
  const filter = $('#task-filter'), badge = filter.querySelector('.tl-fbadge');
  filter.classList.toggle('active', !!count);
  filter.setAttribute('aria-label', count ? `Filter, ${count} active` : 'Filter');
  badge.hidden = !count; badge.textContent = count ? String(count) : '';
  $('#task-filter-clear').hidden = count === 0;
  const layout = $('#task-layout');
  layout.hidden = you || archived;
  for (const b of layout.querySelectorAll('[data-layout]')) b.setAttribute('aria-pressed', String(b.dataset.layout === state.view));
  tasksFinishedPaint(state);
  $('#task-archive').setAttribute('aria-pressed', String(archived));
  tasksHeadFit(state);
}
// One popover for every menu on the page: a filter's values, the fields, Group by, and the bulk actions.
// ↑/↓ move between its items (and in and out of its find box), Home/End jump, Enter picks, Esc closes.
function tasksMenu(state: TasksMenuHost, anchor: HTMLElement | null | undefined, html: string, bind?: TasksMenuBind, toggle = true) {
  const pop = $('#task-filter-pop');
  if (!pop) return;
  if (toggle && pop.matches(':popover-open') && state.menuAnchor === anchor) { pop.hidePopover(); return; }
  state.menuAnchor = anchor;
  state.menuField = '';
  pop.innerHTML = html;
  pop.onclick = pop.onchange = null;
  bind?.(pop);
  if (!pop.matches(':popover-open')) pop.showPopover();
  tasksMenuPlace(pop, anchor);
  (pop.querySelector('input[type=search], input[type=text]') || pop.querySelector('[aria-checked="true"], input:checked') || pop.querySelector('.tl-mi, .tl-opt input') || pop).focus();
}
function tasksMenuKeys(ev: KeyboardEvent) {
  const pop = ev.currentTarget as HTMLElement;      // the popover the keys are bound on
  const items: (HTMLElement | null)[] = [...pop.querySelectorAll<HTMLElement>('.tl-mi, .tl-opt input, .tl-menu-q, .tl-menu-new input')].filter(el => !el.closest('[hidden]'));
  const active = document.activeElement as HTMLInputElement | null;     // its type is read only to tell a text box
  const at = items.indexOf(active);
  const go = (i: number) => { ev.preventDefault(); items[Math.max(0, Math.min(items.length - 1, i))]?.focus(); };
  if (ev.key === 'ArrowDown') go(at + 1);
  else if (ev.key === 'ArrowUp') go(at - 1);
  else if (ev.key === 'Home' && !/^(search|text)$/.test(active?.type || '')) go(0);
  else if (ev.key === 'End' && !/^(search|text)$/.test(active?.type || '')) go(items.length - 1);
  else if (ev.key === 'Enter' && active?.matches('.tl-opt input')) { ev.preventDefault(); active.click(); }
}
function tasksMenuPlace(pop: HTMLElement, anchor: HTMLElement | null | undefined) {
  if (matchMedia(PHONE).matches || !anchor?.isConnected) { pop.style.top = pop.style.left = ''; return; }
  const r = anchor.getBoundingClientRect();
  const up = anchor.closest('#task-bulk');
  pop.style.top = `${Math.max(8, up ? r.top - pop.offsetHeight - 6 : Math.min(r.bottom + 6, innerHeight - pop.offsetHeight - 8))}px`;
  pop.style.left = `${Math.max(8, Math.min(r.left, innerWidth - pop.offsetWidth - 8))}px`;
}
function tasksMenuClose() { const pop = $('#task-filter-pop'); if (pop?.matches(':popover-open')) pop.hidePopover(); }
// The value list for one filter: a search when it is long, a check per value; changes apply as they are ticked.
function tasksFilterMenu(state: TaskState, field: string, anchor: HTMLElement | null | undefined, toggle = true) {
  const name = TASK_FILTER_FIELDS.find(([k]) => k === field)?.[1] || field;
  const opts = tasksFilterOptions(state, field);
  const chosen = state.filters[field]!;      // every field has its list (tasksEmptyFilters)
  const html = `<div class="tl-menu" role="group" aria-label="${esc(name)}">
    <div class="tl-menu-h" aria-hidden="true">${esc(name)}</div>
    ${opts.length > 7 ? `<input type="search" class="tl-menu-q" placeholder="Find ${esc(name.toLowerCase())}…" aria-label="Find ${esc(name.toLowerCase())}" autocomplete="off" spellcheck="false">` : ''}
    <div class="tl-menu-list">${opts.map(o => `<label class="tl-opt" data-label="${esc(o.label.toLowerCase())}"><input type="checkbox" name="tl-${field}" value="${esc(o.value)}"${chosen.includes(o.value) ? ' checked' : ''}>${o.face ? `<span class="tl-opt-face">${o.face}</span>` : ''}<span>${esc(o.label)}</span></label>`).join('') || '<div class="tl-menu-none">None loaded</div>'}</div>
  </div>`;
  tasksMenu(state, anchor, html, pop => {
    const q = pop.querySelector<HTMLInputElement>('.tl-menu-q');
    if (q) q.oninput = () => { for (const o of pop.querySelectorAll<HTMLElement>('.tl-opt')) o.hidden = !o.dataset.label!.includes(q.value.trim().toLowerCase()); };
    pop.onchange = ev => {
      const input = eventElement(ev)?.closest('input[name]'); if (!input) return;
      state.filters[field] = [...pop.querySelectorAll<HTMLInputElement>(`input[name="tl-${field}"]:checked`)].map(i => i.value);
      tasksFiltersChanged(state);
      state.menuAnchor = $(`[data-chip-edit="${field}"]`) || $('#task-filter');
      tasksMenuPlace(pop, state.menuAnchor);
    };
  }, toggle);
  state.menuField = field;
}
// A value menu opened before its values loaded shows them once they arrive.
function tasksFilterMenuLoaded(state: TaskState) {
  const pop = $('#task-filter-pop');
  if (state.menuField && pop?.matches(':popover-open') && pop.querySelector('.tl-menu-none')) tasksFilterMenu(state, state.menuField, state.menuAnchor, false);
}
// The filter menu: how the list is grouped (on a list), then the fields to filter by (not on your own queue, which
// always shows the whole plate). A field opens its values in the same menu.
function tasksFieldMenu(state: TaskState, anchor: HTMLElement) {
  const groupable = state.view === 'list' || state.view === 'foryou', by = tasksGroupBy(state);
  const group = !groupable ? '' : `<div class="tl-menu-h" aria-hidden="true">Group</div><div class="tl-gpicks" role="group" aria-label="Group by">
    ${taskGroupsFor(state.view, state).map(([k, label]) => `<button type="button" role="menuitemradio" aria-checked="${k === by}" class="tl-mi tl-gpick" data-pick-group="${k}">${esc(label)}</button>`).join('')}</div>`;
  const fields = tasksNeedsQueue(state) ? [] : TASK_FILTER_FIELDS;
  tasksMenu(state, anchor, `<div class="tl-menu" role="menu" aria-label="Filter">${group}
    ${fields.length ? `<div class="tl-menu-h" aria-hidden="true">Filter</div>` : ''}
    ${fields.map(([k, name]) => `<button type="button" role="menuitem" class="tl-mi" data-pick-field="${k}">${esc(name)}</button>`).join('')}</div>`, pop => {
    pop.onclick = ev => {
      const g = eventElement(ev)?.closest<HTMLElement>('[data-pick-group]');
      if (g) {
        pop.hidePopover();
        state.group = {...state.group, [state.view]: g.dataset.pickGroup};
        tasksRemember(state); tasksTools(state); tasksRender(state);
        $('#task-filter')?.focus();
        return;
      }
      const b = eventElement(ev)?.closest<HTMLElement>('[data-pick-field]'); if (!b) return;
      tasksFilterMenu(state, b.dataset.pickField!, anchor, false);
    };
  });
}
function tasksFiltersChanged(state: TaskState) {
  state.memo = new Map();
  tasksURLWrite(state); tasksTools(state); tasksRender(state);
}
