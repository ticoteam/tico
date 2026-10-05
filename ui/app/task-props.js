/* ui/app/task-props.js — A task's properties, Linear style: one click-to-edit row each (Status, Owner, Asked by, Due,
   Tags, Part of, Blocked by, and Type and Step for a typed task), their menus and pickers, and the task's "…" menu.
   Used by the full task (#task-modal) and the side peek (#task-peek); each change saves at once with the task's version.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const TASK_SET_STATUSES = ['open', 'doing', 'waiting', 'review', 'ready', 'done', 'declined'];
const PROP_ICON = {
  due: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><rect x="2" y="3" width="10" height="9" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M2 6h10M5 1.8v2.4M9 1.8v2.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
  type: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><path d="M2.5 4.5h9M2.5 7h9M2.5 9.5h5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
  open: '<svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true" focusable="false"><path d="M5 2.5H2.5v7h7V7M7 2.5h2.5V5M9.5 2.5L5.5 6.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  more: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><circle cx="3" cy="7" r="1.2" fill="currentColor"/><circle cx="7" cy="7" r="1.2" fill="currentColor"/><circle cx="11" cy="7" r="1.2" fill="currentColor"/></svg>',
};
// who may change what (the server holds the rules; this only hides controls that could never work)
function taskPropRights(t) {
  const me = myActor(), mover = canMove();
  const party = !!me && [t.owner, t.requester, taskRequester(t)].includes(me);
  const live = !taskFinished(t);
  return {mover, party, live, edit: (mover || party) && live, links: mover && live, reopen: mover || party};
}
const taskPropTask = id => id && ((TASKS_ST?.tasks || []).find(x => String(x.id) === String(id)) || null);
function taskPropsHTML(t, opts = {}) {
  const r = taskPropRights(t);
  const type = pipelineType(t), typed = !!type && pipelineTypeId(t) !== 'general';
  const row = (key, label, value, o = {}) => {
    const words = o.words || label;
    const v = o.edit
      ? `<button type="button" class="prop-v${o.empty ? ' empty' : ''}" data-prop="${key}" aria-haspopup="${o.popup || 'menu'}" aria-label="${esc(label)}: ${esc(words)}">${value}</button>`
      : `<span class="prop-v ro${o.empty ? ' empty' : ''}" aria-label="${esc(label)}: ${esc(words)}">${value}</span>`;
    return `<div class="prop" data-prop-row="${key}"><span class="prop-k" aria-hidden="true">${esc(label)}</span><span class="prop-vwrap">${v}${o.extra || ''}</span></div>`;
  };
  const txt = s => `<span class="prop-txt">${esc(s)}</span>`;
  const rows = [];
  // Status: the shared icon and the status's name (a typed task's status follows its steps)
  const statusName = STATUS_WORD[t.status] || String(t.status || '');
  rows.push(row('status', 'Status', txt(statusName), {edit: r.edit || (!r.live && r.reopen), words: statusName}));
  // Owner and who asked
  rows.push(row('owner', 'Owner', actorFace(t.owner, 16) + txt(actorLabel(t.owner)), {edit: r.edit, words: actorLabel(t.owner),
    extra: t.private ? '<small>Reassigning grants the new assignee access and removes the previous assignee’s access unless they requested the task.</small>' : ''}));
  const asker = taskRequester(t);
  rows.push(row('asker', 'Asked by', asker ? actorFace(asker, 16) + txt(actorLabel(asker)) : txt('Unknown'), {words: actorLabel(asker) || 'Unknown', empty: !asker}));
  rows.push(`<div class="prop" data-prop-row="private"><span class="prop-k">Private</span><span class="prop-vwrap"><label><input type="checkbox" data-task-private ${t.private ? 'checked' : ''} ${r.party && (!t.private || myActor() === t.requester) ? '' : 'disabled'}> ${t.private ? '<span class="nav-icon" aria-hidden="true">lock</span> Private' : 'Company'}</label><small>Only the requester and assignee can see a private task.</small></span></div>`);
  // Due: a person's deadline (red once passed), or when a bot's parked task wakes (muted "Wakes Oct 2")
  const dueAt = parseServerTime(t.due);
  const info = taskDueInfo(t);
  const dueDay = dueAt ? new Date(dueAt).toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'}) : '';
  const dueWords = !dueAt ? '' : info?.kind === 'wake' ? `${info.words.split(' ')[0]} ${dueDay}` : dueDay;
  const dueLate = !!info?.late;
  const dueISO = dueAt ? new Date(dueAt - new Date(dueAt).getTimezoneOffset() * 60000).toISOString().slice(0, 10) : '';
  rows.push(row('due', 'Due', `<span class="prop-ic${dueLate ? ' late' : ''}">${PROP_ICON.due}</span>` + (dueAt ? `<span class="prop-txt${dueLate ? ' late' : ''}${info?.kind === 'wake' ? ' wake' : ''}">${esc(dueWords)}</span>` : txt('Add due date')),
    {edit: r.edit, empty: !dueAt, popup: 'dialog', words: dueWords || 'none',
      extra: (r.edit ? `<input type="date" class="prop-date" data-prop-date tabindex="-1" aria-hidden="true" value="${esc(dueISO)}">` : '')
        + (r.edit && dueAt ? `<button type="button" class="prop-x" data-prop-clear="due" aria-label="Clear the due date" title="Clear">${TL_ICON.x}</button>` : '')}));
  // Tags: chips (each opens its tag) and a small +
  const chips = tagChips(t.labels || [], t.tags || [], r.links, true);
  rows.push(`<div class="prop" data-prop-row="tags"><span class="prop-k" aria-hidden="true">Tags</span><span class="prop-vwrap"><span class="prop-tags tlabels" aria-label="Tags">${chips
    || (r.links ? '' : '<span class="prop-v ro empty"><span class="prop-txt">None</span></span>')}${r.links
    ? (chips ? `<button type="button" class="prop-add" data-prop="tags" aria-haspopup="menu" aria-label="Add a tag" title="Add a tag">${TL_ICON.plus}</button>`
      : `<button type="button" class="prop-v empty" data-prop="tags" aria-haspopup="menu" aria-label="Tags: none, add a tag"><span class="prop-txt">Add tags</span></button>`) : ''}</span></span></div>`);
  // Part of and Blocked by: the other task's title is a link to it, × clears it; empty, a picker finds one.
  for (const [key, label, id, known, verb] of [['parent', 'Part of', t.parent_id, t.parent, 'parent'], ['blocked', 'Blocked by', t.blocked_by, t.blocker, 'blocker']]) {
    const other = id ? (known && String(known.id) === String(id) ? known : null) || taskPropTask(id) : null;
    const title = other?.title || 'A task';
    if (id) {
      rows.push(`<div class="prop" data-prop-row="${key}"><span class="prop-k" aria-hidden="true">${esc(label)}</span><span class="prop-vwrap">`
        + `<button type="button" class="prop-v prop-link" data-open-task="t${esc(id)}" aria-label="${esc(label)}: ${esc(title)}. Open it"><span class="prop-txt">${esc(title)}</span></button>`
        + (r.links ? `<button type="button" class="prop-x" data-prop-clear="${key}" aria-label="Clear ${esc(label)}" title="Clear">${TL_ICON.x}</button>` : '') + '</span></div>');
    } else rows.push(row(key, label, txt(r.links ? `Add ${verb}` : 'None'), {edit: r.links, empty: true, words: 'none'}));
  }
  // Type and Step: only for a task that has a type
  if (typed) {
    rows.push(row('type', 'Type', `<span class="prop-ic">${PROP_ICON.type}</span>` + txt(type.name), {edit: r.edit && TASK_TYPES.length > 1, words: type.name}));
    const step = type.steps.find(s => s.id === t.step_id);
    rows.push(row('step', 'Step', step ? txt(step.name) : txt('Add step'), {edit: r.edit || (!r.live && r.reopen), empty: !step, words: step?.name || 'none'}));
  }
  return `<div class="props">${rows.join('')}</div><div class="props-msg" data-props-msg role="status" aria-live="polite"></div>`;
}
// A refused save's reason stays under the properties until that property saves (several: one line each, named).
const PROP_WORDS = {status: 'Status', owner: 'Owner', due: 'Due', tags: 'Tags', parent: 'Part of', blocked: 'Blocked by', type: 'Type', step: 'Step'};
function taskPropsMsgPaint(d) {
  const box = $('[data-props-msg]', d); if (!box) return;
  const errs = d.propsErrs?.id === String(d.dataset.task) ? [...d.propsErrs.map] : [];
  box.textContent = errs.length === 1 ? errs[0][1] : errs.map(([k, text]) => `${PROP_WORDS[k] || 'Task'}: ${text}`).join('\n');
}

// ---- one small menu for every row (and the "…" menu): arrow keys, Home/End, Enter, Esc; an optional find box
function propMenu(d, anchor, {label, items = [], find = '', entry = '', none = 'Nothing to pick', onPick, onEntry}) {
  let pop = $('.prop-pop', d);
  if (pop?.matches(':popover-open')) { const same = pop.anchor === anchor; pop.hidePopover(); if (same) return; }
  if (pop?.anchor && pop.anchor !== anchor) pop.anchor.setAttribute('aria-expanded', 'false');   // only one menu, only one expanded
  if (!pop) {
    pop = document.createElement('div');
    pop.className = 'tl-pop prop-pop'; pop.popover = 'auto';
    pop.addEventListener('toggle', ev => {
      if (ev.newState !== 'closed') return;
      const a = pop.anchor;
      if (a?.isConnected && (pop.contains(document.activeElement) || document.activeElement === document.body)) a.focus();
      pop.dispatchEvent(new Event('menuclosed'));
    });
    pop.addEventListener('keydown', ev => {
      const list = [...pop.querySelectorAll('.tl-mi')].filter(b => !b.hidden);
      const input = $('.tl-menu-q', pop);
      const at = list.indexOf(document.activeElement);
      const go = i => { ev.preventDefault(); ev.stopPropagation(); list[Math.max(0, Math.min(list.length - 1, i))]?.focus(); };
      if (ev.key === 'ArrowDown') return go(at < 0 ? 0 : at + 1);
      if (ev.key === 'ArrowUp') { if (at <= 0 && input) { ev.preventDefault(); ev.stopPropagation(); input.focus(); return; } return go(at - 1); }
      if (ev.key === 'Home' && at >= 0) return go(0);
      if (ev.key === 'End' && at >= 0) return go(list.length - 1);
      if (ev.key === 'Enter' && document.activeElement === input) {
        ev.preventDefault(); ev.stopPropagation();
        if (pop.onEntry && input.value.trim()) pop.onEntry(input.value.trim());
        else list[0]?.click();
      }
      if (['j', 'k', 'x', 'c', '/'].includes(ev.key)) ev.stopPropagation();   // the list's keys wait until the menu closes
    });
    d.append(pop);
  }
  pop.anchor = anchor;
  pop.onEntry = onEntry ? v => { pop.hidePopover(); onEntry(v); } : null;
  // A menu with a find box is a group of plain buttons (a role=menu holds only menu items); either way ↑/↓ move.
  const menu = !(find || entry);
  const state = it => it.checked == null ? '' : menu ? ` aria-checked="${it.checked ? 'true' : 'false'}"` : it.checked ? ' aria-current="true"' : '';
  pop.innerHTML = `<div class="tl-menu" role="${menu ? 'menu' : 'group'}" aria-label="${esc(label)}"><div class="tl-menu-h" aria-hidden="true">${esc(label)}</div>
    ${find || entry ? `<input type="${entry ? 'text' : 'search'}" class="tl-menu-q" placeholder="${esc(entry || find)}" aria-label="${esc(entry || find)}" autocomplete="off" spellcheck="false">` : ''}
    <div class="tl-menu-list">${items.map((it, i) => `<button type="button"${menu ? ` role="${it.checked != null ? 'menuitemradio' : 'menuitem'}"` : ''}${state(it)} class="tl-mi${it.danger ? ' danger' : ''}" data-pick="${i}"${it.value != null ? ` data-prop-pick="${esc(it.value)}"` : ''} data-label="${esc(String(it.text || '').toLowerCase())}">${it.html || esc(it.text)}${it.checked ? '<span class="tl-mi-on" aria-hidden="true">✓</span>' : ''}</button>`).join('')}</div>
    ${items.length ? '' : `<div class="tl-menu-none">${esc(none)}</div>`}</div>`;
  const input = $('.tl-menu-q', pop);
  if (input) input.oninput = () => {
    const q = input.value.trim().toLowerCase();
    for (const b of pop.querySelectorAll('.tl-mi')) b.hidden = !!q && !b.dataset.label.includes(q);
  };
  pop.onclick = ev => {
    const b = ev.target.closest('[data-pick]'); if (!b) return;
    const it = items[Number(b.dataset.pick)];
    pop.hidePopover();
    onPick?.(it);
  };
  pop.showPopover();
  if (!matchMedia('(max-width:760px)').matches) {
    const a = anchor.getBoundingClientRect();
    const below = a.bottom + 4 + pop.offsetHeight <= innerHeight - 8;
    pop.style.top = `${Math.max(8, below ? a.bottom + 4 : a.top - pop.offsetHeight - 4)}px`;
    pop.style.left = `${Math.max(8, Math.min(a.left, innerWidth - pop.offsetWidth - 8))}px`;
  } else pop.style.top = pop.style.left = '';
  (input || pop.querySelector('.tl-mi[aria-checked="true"], .tl-mi[aria-current="true"]') || pop.querySelector('.tl-mi'))?.focus();
  anchor.setAttribute('aria-expanded', 'true');
  pop.addEventListener('menuclosed', () => anchor.setAttribute('aria-expanded', 'false'), {once: true});
  return pop;
}
// A redraw replaces the dialog's content; one that lands while a menu is open waits for it to close.
function taskMenuSettled(d) {
  const pop = $('.prop-pop', d);
  if (!pop?.matches(':popover-open')) return Promise.resolve();
  return new Promise(resolve => pop.addEventListener('menuclosed', resolve, {once: true}));
}

// ---- the pickers
const stepItems = (t, type) => [{value: '', text: 'No step', html: `<span>No step</span>`, checked: !t.step_id},
  ...type.steps.map(s => ({value: s.id, text: s.name, html: `<span>${esc(s.name)}</span>`, checked: s.id === t.step_id, step: s}))];
// Tasks to pick from: the Tasks page's list, else a short-lived copy (a minute; the Tasks page clears it on open).
let PROP_TASKS = null;
async function taskPropCandidates(t, key) {
  let rows = (TASKS_ST?.tasks || []).filter(x => !taskFinished(x));
  if (!rows.length) {
    if (!PROP_TASKS || Date.now() - PROP_TASKS.at > 60000) PROP_TASKS = {at: Date.now(), rows: (await v2Get(activeTasksPath()))?.tasks || []};
    rows = PROP_TASKS.rows;
  }
  const bar = taskPropLoops(t, key, rows);
  return rows.filter(x => !bar.has(String(x.id))).sort((a, b) => String(a.title).localeCompare(String(b.title)));
}
// What a picker must not offer, because it would make a loop: the task itself, and for Part of everything under it
// (children, grandchildren…), for Blocked by every task that already waits on it, however far down the chain;
// for Related only the task itself.
function taskPropLoops(t, key, rows) {
  if (key === 'related') return new Set([String(t.id)]);   // a relation has no direction, so it cannot loop
  const here = String(t.id), field = key === 'parent' ? 'parent_id' : 'blocked_by';
  const under = new Map();
  for (const x of rows) { const up = String(x[field] || ''); if (up) { if (!under.has(up)) under.set(up, []); under.get(up).push(String(x.id)); } }
  const out = new Set([here]), todo = [here];
  while (todo.length) for (const k of under.get(todo.pop()) || []) if (!out.has(k)) { out.add(k); todo.push(k); }
  return out;
}
// Wires the rows of a drawn task. `change(body)` saves (task-modal.js taskModalBind) and redraws.
function taskPropsBind(d, task, change) {
  const box = $('[data-task-props]', d); if (!box) return;
  taskPropsMsgPaint(d);
  const save = (key, body) => { d.focusProp = key; return change(body); };
  const type = pipelineType(task), typed = !!type && pipelineTypeId(task) !== 'general';
  box.onclick = async ev => {
    if (ev.target.closest('[data-tag-key],[data-drop-label],[data-open-task]')) return;   // a tag opens its page; × and ↗ are bound in taskModalBind
    const clear = ev.target.closest('[data-prop-clear]');
    if (clear) {
      const key = clear.dataset.propClear;
      void save(key, key === 'due' ? {due: ''} : key === 'parent' ? {parent_id: ''} : {blocked_by: ''});
      return;
    }
    const b = ev.target.closest('[data-prop]'); if (!b) return;
    const key = b.dataset.prop;
    if (key === 'status') {
      if (typed) {
        propMenu(d, b, {label: 'Status', items: stepItems(task, type).slice(1), onPick: it => {
          if (it.value === task.step_id) return;
          void save('status', {step: it.value, ...(it.step?.status === 'done' ? {status: 'done'} : {})});
        }});
        return;
      }
      propMenu(d, b, {label: 'Status', items: TASK_SET_STATUSES.map(s => ({value: s, text: STATUS_WORD[s] || s,
        html: `<span>${esc(STATUS_WORD[s] || s)}</span>`, checked: task.status === s})),
        onPick: it => { if (it.value !== task.status) void save('status', {status: it.value}); }});
    } else if (key === 'step' && typed) {
      propMenu(d, b, {label: 'Step', items: stepItems(task, type), onPick: it => {
        if (it.value === (task.step_id || '')) return;
        void save('step', {step: it.value, ...(it.step?.status === 'done' ? {status: 'done'} : {})});
      }});
    } else if (key === 'type') {
      taskTypeMenu(d, b, task, save);
    } else if (key === 'owner') {
      const opts = new DOMParser().parseFromString(`<select>${taskOwnerOptions('')}</select>`, 'text/html').querySelectorAll('option[value]:not([value=""])');
      propMenu(d, b, {label: 'Owner', find: 'Find a person or bot', items: [...opts].map(o => {
        const actor = o.value.includes(':') ? o.value : 'bot:' + o.value;
        return {value: o.value, text: o.textContent, html: `<span class="tl-opt-face">${actorFace(actor, 16)}</span><span>${esc(o.textContent)}</span>`, checked: task.owner === actor, actor};
      }), onPick: it => { if (it.actor !== task.owner) void save('owner', {owner: it.value}); }});
    } else if (key === 'due') {
      const input = $('[data-prop-date]', d);
      d.focusProp = 'due';
      try { input.showPicker(); } catch { input.removeAttribute('tabindex'); input.classList.add('shown'); input.focus(); }
    } else if (key === 'tags') {
      const have = new Set(task.labels || []);
      const keys = [...new Set([...(TASKS_ST?.labels || []), ...(TASKS_ST?.tasks || []).flatMap(x => x.labels || [])])].filter(k => !have.has(k)).sort();
      // built from the newest copy when it saves, so two quick tags both stay
      const add = tag => { tag = String(tag).trim().toLowerCase(); if (tag && !have.has(tag)) void save('tags', cur => (cur.labels || []).includes(tag) ? null : {labels: [...(cur.labels || []), tag]}); };
      propMenu(d, b, {label: 'Add a tag', entry: 'Tag', none: 'Type a tag, then Enter', items: keys.map(k => ({value: k, text: k,
        html: `<span class="tl-dot" style="--hue:${tagHue(k)}"></span><span>${esc(k)}</span>`})), onPick: it => add(it.value), onEntry: add});
    } else if (key === 'parent' || key === 'blocked') {
      const field = key === 'parent' ? 'parent_id' : 'blocked_by';
      const rows = await taskPropCandidates(task, key);
      if (!b.isConnected) return;
      propMenu(d, b, {label: key === 'parent' ? 'Part of' : 'Blocked by', find: 'Find a task', none: 'No open tasks', items: rows.map(x => ({value: x.id, text: `${x.title} ${actorLabel(x.owner)}`,
        html: `${taskStatusText(x)}<span class="tl-mi-t">${esc(clipLine(x.title, 70))}</span><span class="tl-mi-sub">${esc(actorLabel(x.owner))}</span>`,
        checked: String(task[field] || '') === String(x.id)})), onPick: it => {
          if (taskPropLoops(task, key, TASKS_ST?.tasks || PROP_TASKS?.rows || []).has(String(it.value))) { d.propsErrs = {id: String(task.id), map: new Map([[key, 'That would make a loop.']])}; taskPropsMsgPaint(d); return; }
          if (String(it.value) !== String(task[field] || '')) void save(key, {[field]: it.value});
        }});
    }
  };
  // A date saves only when it is whole and sensible. With the native picker that is its change; typed by hand (the
  // fallback field), only on Enter or leaving the field, so a half-typed year like 0002 never saves.
  const date = $('[data-prop-date]', d);
  const saveDate = () => {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date.value || '');
    if (!m) return;
    const [y, mo, day] = m.slice(1).map(Number), when = new Date(y, mo - 1, day, 17, 0);
    if (y < 2000 || y > 2200 || when.getMonth() !== mo - 1 || when.getDate() !== day) {
      d.propsErrs = {id: String(task.id), map: new Map([['due', 'That date is not valid.']])}; taskPropsMsgPaint(d); return;
    }
    void save('due', {due: when.toISOString()});   // the end of that working day, local time
  };
  if (date) {
    date.onchange = () => { if (!date.classList.contains('shown')) saveDate(); };
    date.onkeydown = ev => { if (ev.key === 'Enter') { ev.preventDefault(); saveDate(); } };
    date.onblur = () => { if (date.classList.contains('shown')) saveDate(); };
  }
  // the "…" menu in the header: what used to be links and buttons under the task
  const more = $('[data-task-more]', d);
  if (more) more.onclick = () => {
    const r = taskPropRights(task), items = [];
    if (d.dataset.peek) items.push({text: 'Open full', run: () => { const t = d.liveTask || task; d.close(); void taskModalShow(t); }});
    if (r.links && !$('[data-sub-add]', d)) items.push({text: 'Add subtask', run: () => taskRailAdd(d, task)});
    if (r.edit && TASK_TYPES.length > 1 && !typed) items.push({text: 'Set type…', run: () => taskTypeMenu(d, more, task, save)});
    items.push({text: 'Copy link', run: async () => {
      try { await navigator.clipboard.writeText(`${location.origin}/#/task/${encodeURIComponent(task.id)}`); toast('Link copied'); } catch { toast('Could not copy the link', true); }
    }});
    if (!r.live && r.reopen) items.push({text: 'Reopen', run: () => save('status', {status: 'open'})});
    if (task.status !== 'closed' && (r.mover || r.party)) items.push({text: 'Close task', danger: true, run: () => save('status', {close: true})});
    propMenu(d, more, {label: 'Task', items: items.map(it => ({...it, html: `<span>${esc(it.text)}</span>`})), onPick: it => it.run()});
  };
}
function taskTypeMenu(d, anchor, task, save) {
  propMenu(d, anchor, {label: 'Type', items: TASK_TYPES.map(type => ({value: type.id, text: type.name,
    html: `<span class="prop-ic">${PROP_ICON.type}</span><span>${esc(type.name)}</span>`, checked: type.id === pipelineTypeId(task)})),
    onPick: it => { if (it.value !== pipelineTypeId(task)) void save('type', {type: it.value}); }});
}
