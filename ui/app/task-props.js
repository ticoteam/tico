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
  check: '<svg viewBox="0 0 14 14" width="13" height="13" aria-hidden="true" focusable="false"><path d="M3 7.3l2.6 2.6L11 4.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  more: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><circle cx="3" cy="7" r="1.2" fill="currentColor"/><circle cx="7" cy="7" r="1.2" fill="currentColor"/><circle cx="11" cy="7" r="1.2" fill="currentColor"/></svg>',
};
// who may change what (the server holds the rules; this only hides controls that could never work)
function taskPropRights(t) {
  const me = myActor(), mover = canMove();
  const party = !!me && [t.owner_id, t.requester_id, taskRequester(t)].includes(me);
  const live = !taskFinished(t);
  const onTask = !!me && Object.values(t.roles || {}).some(actors => actors.includes(me));
  return {mover, party, live, edit: (mover || party) && live, links: mover && live, reopen: mover || party,
    snooze: live && (mover || party || onTask)};
}
// Snooze choices: the start of that day, local time (backend/task_snooze.py).
function snoozeItems() {
  const day = n => { const at = new Date(); at.setHours(0, 0, 0, 0); at.setDate(at.getDate() + n); return at; };
  const monday = (8 - new Date().getDay()) % 7 || 7;
  return [['Tomorrow', day(1)], ['Next week', day(monday)], ['In two weeks', day(14)], ['In a month', day(30)]].map(([text, at]) =>
    ({value: at.toISOString(), text, html: `<span>${esc(text)}</span><span class="tl-mi-sub">${esc(at.toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'}))}</span>`}));
}
function snoozeMenu(d, anchor, save) {
  propMenu(d, anchor, {label: 'Snooze until', items: snoozeItems(), entry: 'A date, like 2026-11-02', none: 'Type a date, then Enter',
    onPick: it => void save('snooze', {snooze: it.value}),
    onEntry: v => {
      const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v.trim()), at = m && new Date(+m[1], +m[2] - 1, +m[3]);
      if (!at || at.getDate() !== +m[3] || at <= new Date()) { toast('Pick a date in the future, like 2026-11-02', true); return; }
      void save('snooze', {snooze: at.toISOString()});
    }});
}
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
  rows.push(row('owner', 'Owner', actorFace(t.owner_id, 16) + txt(actorLabel(t.owner_id)), {edit: r.edit, words: actorLabel(t.owner_id),
    extra: t.private ? '<small>Reassigning grants the new assignee access and removes the previous assignee’s access unless they requested the task.</small>' : ''}));
  const asker = taskRequester(t);
  rows.push(row('asker', 'Asked by', asker ? actorFace(asker, 16) + txt(actorLabel(asker)) : txt('Unknown'), {words: actorLabel(asker) || 'Unknown', empty: !asker}));
  if (t.roles && (typed || Object.keys(t.roles).length)) rows.push(taskPeopleRowHTML(t, r));
  // Private: the label never changes; a few words beside the box say who sees it, the tooltip says it in full
  const seenBy = t.private ? (t.guest ? `Requester, owner, ${actorLabel(t.guest)}` : 'Requester and owner') : 'Everyone';
  const seenTip = t.private ? (t.guest ? `Only the requester, the assignee and ${actorLabel(t.guest)}, whom the assignee brought in, can see this task` : 'Only the requester and assignee can see a private task') : 'Visible to everyone in the company';
  rows.push(`<div class="prop" data-prop-row="private" title="${esc(seenTip)}"><span class="prop-k">Private</span><span class="prop-vwrap"><label class="prop-v prop-check"><input type="checkbox" aria-label="Private" data-task-private ${t.private ? 'checked' : ''} ${r.party && (!t.private || myActor() === t.requester_id) ? '' : 'disabled'}><span class="prop-txt">${esc(seenBy)}</span></label></span></div>`);
  if (roadmapTask(t)) {
    rows.push(`<div class="prop" data-prop-row="publication"><span class="prop-k">Public</span><span class="prop-vwrap"><label class="prop-v prop-check"><input type="checkbox" aria-label="Public" data-task-public ${t.visibility === 'public' ? 'checked' : ''} ${r.reopen && !t.private ? '' : 'disabled'}><span class="prop-txt">${t.visibility === 'public' ? 'Shown on the public roadmap' : 'Team only'}</span></label></span></div>`);
    rows.push(`<div class="prop" data-prop-row="size"><span class="prop-k">Size</span><span class="prop-vwrap"><input class="roadmap-size" type="number" aria-label="Size" data-task-size min="0" step="any" value="${esc(t.size ?? '')}" ${r.reopen ? '' : 'disabled'}></span></div>`);
  }
  // Due: a deadline (red once passed; taskDueInfo), or when a bot's parked task wakes (muted "Wakes Oct 2")
  const dueAt = parseServerTime(t.due_at);
  const info = taskDueInfo(t);
  const dueDayAt = occurrenceDay(t) || dueAt;   // an occurrence's calendar day is its routine's, not the viewer's zone
  const dueDay = dueAt ? new Date(dueDayAt).toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'}) : '';
  const dueWords = !dueAt ? '' : info?.kind === 'wake' ? `${info.words.split(' ')[0]} ${dueDay}` : dueDay;
  const dueLate = !!info?.late;
  const dueISO = dueAt ? new Date(dueDayAt - new Date(dueDayAt).getTimezoneOffset() * 60000).toISOString().slice(0, 10) : '';
  rows.push(row('due', 'Due', `<span class="prop-ic${dueLate ? ' late' : ''}">${PROP_ICON.due_at}</span>` + (dueAt ? `<span class="prop-txt${dueLate ? ' late' : ''}${info?.kind === 'wake' ? ' wake' : ''}">${esc(dueWords)}</span>` : txt('Add due date')),
    {edit: r.edit, empty: !dueAt, popup: 'dialog', words: dueWords || 'none',
      extra: (r.edit ? `<input type="date" class="prop-date" data-prop-date tabindex="-1" aria-hidden="true" value="${esc(dueISO)}">` : '')
        + (r.edit && dueAt ? `<button type="button" class="prop-x" data-prop-clear="due" aria-label="Clear the due date" title="Clear">${TL_ICON.x}</button>` : '')}));
  // Tags: chips (each opens its tag) and a small +
  const chips = tagChips(t.labels || [], t.tags || [], r.links, true);
  rows.push(`<div class="prop" data-prop-row="tags"><span class="prop-k" aria-hidden="true">Tags</span><span class="prop-vwrap"><span class="prop-tags tlabels" aria-label="Tags">${chips
    || (r.links ? '' : '<span class="prop-v ro empty"><span class="prop-txt">None</span></span>')}${r.links
    ? (chips ? `<button type="button" class="prop-add" data-prop="tags" aria-haspopup="menu" aria-label="Add a tag" title="Add a tag">${TL_ICON.plus}</button>`
      : `<button type="button" class="prop-v empty" data-prop="tags" aria-haspopup="menu" aria-label="Tags: none, add a tag"><span class="prop-txt">Add tags</span></button>`) : ''}</span></span></div>`);
  // Part of: the parent's title is a link to it, × takes the task out; empty, a picker finds one. Blockers and
  // other relations are in the Related section.
  const parent = taskParent(t);
  if (parent && !parent.id) {
    // A private parent this reader cannot open (a task filed from private work): named only as "a private task".
    rows.push(row('parent', 'Part of', txt(parent.title), {words: parent.title}));
  } else if (parent) {
    rows.push(`<div class="prop" data-prop-row="parent"><span class="prop-k" aria-hidden="true">Part of</span><span class="prop-vwrap">`
      + `<button type="button" class="prop-v prop-link" data-open-task="t${esc(parent.id)}" aria-label="Part of: ${esc(parent.title)}. Open it"><span class="prop-txt">${esc(parent.title)}</span></button>`
      + (r.links ? `<button type="button" class="prop-x" data-prop-clear="parent" aria-label="Clear Part of" title="Clear">${TL_ICON.x}</button>` : '') + '</span></div>');
  } else rows.push(row('parent', 'Part of', txt(r.links ? 'Add parent' : 'None'), {edit: r.links, empty: true, words: 'none'}));
  // Type and Step: only for a task that has a type
  if (typed) {
    rows.push(row('type', 'Type', `<span class="prop-ic">${PROP_ICON.type}</span>` + txt(type.name), {edit: r.edit && TASK_TYPES.length > 1, words: type.name}));
    const step = type.steps.find(s => s.id === t.step_id);
    rows.push(row('step', 'Step', step ? txt(step.name) : txt('Add step'), {edit: r.edit || (!r.live && r.reopen), empty: !step, words: step?.name || 'none'}));
  }
  if (t.verdict) rows.push(row('verdict', 'Verdict', taskVerdictHTML(t), {words: VERDICT_WORDS[t.verdict]?.[0] || t.verdict}));
  const waiver = (t.qa_waivers || []).at(-1);
  if (waiver) rows.push(row('qa-waiver', 'QA', taskQaWaiverHTML(waiver), {words: `waived on ${String(waiver.head).slice(0, 7)}, not a QA pass`,
    extra: `<small>${esc(actorLabel(waiver.actor))}: ${esc(waiver.reason || '')}</small>`}));
  return `<div class="props">${rows.join('')}</div><div class="props-msg" data-props-msg role="status" aria-live="polite"></div>`;
}
// A refused save's reason stays under the properties until that property saves (several: one line each, named).
const PROP_WORDS = {status: 'Status', owner: 'Owner', due: 'Due', tags: 'Tags', parent: 'Part of', type: 'Type', step: 'Step', people: 'People', publication: 'Public', size: 'Size'};

// Who is on a task by role (the server's `roles`): one chip per person and role, × takes one off, + adds one.
// Roles are names the team chooses; what they mean downstream is the client's.
const roleWord = role => role === 'qa' ? 'QA' : String(role || '').replace(/[-_]/g, ' ').replace(/^\w/, c => c.toUpperCase());
function taskPeopleRowHTML(t, r) {
  const chips = Object.entries(t.roles).flatMap(([role, people]) => people.map(a => `<span class="prop-chip">${actorFace(a, 16)}<span class="prop-txt">${esc(actorLabel(a))} · ${esc(roleWord(role))}</span>${r.edit
    ? `<button type="button" class="prop-x" data-prop-clear-role="${esc(role)}" data-actor="${esc(a)}" aria-label="Take ${esc(actorLabel(a))} off as ${esc(roleWord(role))}" title="Take off">${TL_ICON.x}</button>` : ''}</span>`)).join('');
  const add = !r.edit ? (chips ? '' : '<span class="prop-v ro empty"><span class="prop-txt">None</span></span>')
    : chips ? `<button type="button" class="prop-add" data-prop="people" aria-haspopup="menu" aria-label="Add a person" title="Add">${TL_ICON.plus}</button>`
    : `<button type="button" class="prop-v empty" data-prop="people" aria-haspopup="menu" aria-label="People: none, add someone"><span class="prop-txt">Add</span></button>`;
  return `<div class="prop" data-prop-row="people"><span class="prop-k" aria-hidden="true">People</span><span class="prop-vwrap"><span class="prop-tags" aria-label="People">${chips}${add}</span></span></div>`;
}
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
// (children, grandchildren…), for Blocked by every task that already waits on it, for Blocks every task it already
// waits on, however far along the chain; for the other kinds only the task itself.
function taskPropLoops(t, key, rows) {
  const here = String(t.id), under = new Map();
  const ups = key === 'parent' ? x => [taskParentId(x)] : key === 'blocked_by' ? x => taskBlockers(x).map(b => String(b.id))
    : key === 'blocks' ? x => taskRels(x, 'blocks', 'out').map(b => String(b.id)) : null;
  if (!ups) return new Set([here]);
  for (const x of rows) for (const up of ups(x)) if (up) { if (!under.has(up)) under.set(up, []); under.get(up).push(String(x.id)); }
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
    const off = ev.target.closest('[data-prop-clear-role]');
    if (off) {
      const role = off.dataset.propClearRole, gone = off.dataset.actor;
      void save('people', cur => ({roles: {[role]: ((cur.roles || {})[role] || []).filter(a => a !== gone)}}));
      return;
    }
    const clear = ev.target.closest('[data-prop-clear]');
    if (clear) {
      const key = clear.dataset.propClear;
      if (key === 'parent') { d.focusProp = key; void taskRelate(d, task, {task: taskParentId(task), kind: 'parent', remove: true}); }
      else void save(key, {due: ''});
      return;
    }
    const b = ev.target.closest('[data-prop]'); if (!b) return;
    const key = b.dataset.prop;
    if (key === 'status') {
      if (typed) {
        propMenu(d, b, {label: 'Status', items: stepItems(task, type).slice(1), onPick: async it => {
          if (it.value === task.step_id) return;
          const body = {step: it.value, ...(it.step?.status === 'done' ? {status: 'done'} : {})};
          if (it.step?.status === 'waiting') { const reason = await taskBlockedReason(task); if (reason === null) return; body.note = reason; }
          void save('status', body);
        }});
        return;
      }
      propMenu(d, b, {label: 'Status', items: TASK_SET_STATUSES.map(s => ({value: s, text: STATUS_WORD[s] || s,
        html: `<span>${esc(STATUS_WORD[s] || s)}</span>`, checked: task.status === s})),
        onPick: it => { if (it.value !== task.status) void save('status', {status: it.value}); }});
    } else if (key === 'step' && typed) {
      propMenu(d, b, {label: 'Step', items: stepItems(task, type), onPick: async it => {
        if (it.value === (task.step_id || '')) return;
        const body = {step: it.value, ...(it.step?.status === 'done' ? {status: 'done'} : {})};
        if (it.step?.status === 'waiting') { const reason = await taskBlockedReason(task); if (reason === null) return; body.note = reason; }
        void save('step', body);
      }});
    } else if (key === 'type') {
      taskTypeMenu(d, b, task, save);
    } else if (key === 'people') {
      // First the role (one already on the task, or a new name), then the person or bot.
      const pick = role => {
        const have = new Set((task.roles || {})[role] || []);
        const opts = new DOMParser().parseFromString(`<select>${taskOwnerOptions('')}</select>`, 'text/html').querySelectorAll('option[value]:not([value=""])');
        const items = [...opts].map(o => ({value: o.value, text: o.textContent, actor: o.value.includes(':') ? o.value : 'bot:' + o.value,
          html: `<span class="tl-opt-face">${actorFace(o.value.includes(':') ? o.value : 'bot:' + o.value, 16)}</span><span>${esc(o.textContent)}</span>`}))
          .filter(it => !have.has(it.actor));
        propMenu(d, b, {label: 'Add as ' + roleWord(role), find: 'Find a person or bot', items, none: 'Everyone is on it',
          onPick: it => void save('people', cur => ({roles: {[role]: [...((cur.roles || {})[role] || []), it.actor]}}))});
      };
      propMenu(d, b, {label: 'Role', entry: 'Role, like reviewer', none: 'Type a role, then Enter',
        items: Object.keys(task.roles || {}).map(k => ({value: k, text: k, html: `<span>${esc(roleWord(k))}</span>`})),
        onPick: it => setTimeout(() => pick(it.value)), onEntry: v => {
          const role = v.trim().toLowerCase().replace(/\s+/g, '-');
          if (!/^[a-z0-9][a-z0-9_-]{0,39}$/.test(role)) { d.propsErrs = {id: String(task.id), map: new Map([['people', 'A role is a short name: letters, digits, - or _.']])}; taskPropsMsgPaint(d); return; }
          setTimeout(() => pick(role));
        }});
    } else if (key === 'owner') {
      const opts = new DOMParser().parseFromString(`<select>${taskOwnerOptions('')}</select>`, 'text/html').querySelectorAll('option[value]:not([value=""])');
      propMenu(d, b, {label: 'Owner', find: 'Find a person or bot', items: [...opts].map(o => {
        const actor = o.value.includes(':') ? o.value : 'bot:' + o.value;
        return {value: o.value, text: o.textContent, html: `<span class="tl-opt-face">${actorFace(actor, 16)}</span><span>${esc(o.textContent)}</span>`, checked: task.owner_id === actor, actor};
      }), onPick: it => { if (it.actor !== task.owner_id) void save('owner', {owner: it.actor}); }});
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
    } else if (key === 'parent') {
      const rows = await taskPropCandidates(task, key);
      if (!b.isConnected) return;
      propMenu(d, b, {label: 'Part of', find: 'Find a task', none: 'No open tasks', items: rows.map(x => ({value: x.id, text: `${x.title} ${actorLabel(x.owner_id)}`,
        html: `${taskStatusText(x)}<span class="tl-mi-t">${esc(clipLine(x.title, 70))}</span><span class="tl-mi-sub">${esc(actorLabel(x.owner_id))}</span>`,
        checked: taskParentId(task) === String(x.id)})), onPick: it => {
          if (taskPropLoops(task, key, TASKS_ST?.tasks || PROP_TASKS?.rows || []).has(String(it.value))) { d.propsErrs = {id: String(task.id), map: new Map([[key, 'That would make a loop.']])}; taskPropsMsgPaint(d); return; }
          if (String(it.value) !== taskParentId(task)) { d.focusProp = key; void taskRelate(d, task, {task: it.value, kind: 'parent'}); }
        }});
    }
  };
  const publication = $('[data-task-public]', box), size = $('[data-task-size]', box);
  if (publication) publication.onchange = () => save('publication', {visibility: publication.checked ? 'public' : 'private'});
  if (size) size.onchange = () => { if (size.checkValidity()) void save('size', {size: size.value === '' ? null : Number(size.value)}); };
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
    // The owning bot's session id, for resuming or restoring that session by hand.
    const primary = ((d.liveTask || task).links || []).filter(l => l.kind === 'session').map(l => {
      try { return JSON.parse(l.detail || '{}'); } catch { return {}; }
    }).find(s => s.primary && s.thread_id);
    if (primary) items.push({text: 'Copy session ID', run: async () => {
      try { await navigator.clipboard.writeText(primary.thread_id); toast('Session ID copied'); } catch { toast('Could not copy the session ID', true); }
    }});
    if (r.snooze) items.push(taskSnoozed(task) ? {text: 'Unsnooze', run: () => save('snooze', {unsnooze: true})}
      : {text: 'Snooze…', run: () => setTimeout(() => snoozeMenu(d, more, save))});
    const stepsOnly = pipelineStepsOnly(task);
    if (!r.live && r.reopen && !stepsOnly) items.push({text: 'Reopen', run: () => save('status', {status: 'open'})});
    if (task.status !== 'closed' && (r.mover || r.party)) items.push({text: 'Close task', danger: true, run: () => save('status', {close: true})});
    // Closing counts as finished work; a task made by mistake is deleted into the trash instead.
    if (r.mover || task.requester_id === myActor()) items.push({text: 'Delete task', danger: true, run: async () => {
      if (!window.confirm(`Delete "${task.title}"? It goes to the trash, from which it can be restored.`)) return;
      try { await post(`/v2/tasks/${encodeURIComponent(task.id)}/delete`, {}); } catch (e) { toast(e.body?.error?.code === 'has_work' ? "This task has work on it, such as a bot's turn, an approval, a bot's file or a subtask, so it can't be deleted. Close it instead." : e.message, true); return; }
      d.close(); toast('Deleted');
      if (TASKS_ST) void tasksLoad(TASKS_ST);
    }});
    propMenu(d, more, {label: 'Task', items: items.map(it => ({...it, html: `<span>${esc(it.text)}</span>`})), onPick: it => it.run()});
  };
}
function taskTypeMenu(d, anchor, task, save) {
  propMenu(d, anchor, {label: 'Type', items: TASK_TYPES.map(type => ({value: type.id, text: type.name,
    html: `<span class="prop-ic">${PROP_ICON.type}</span><span>${esc(type.name)}</span>`, checked: type.id === pipelineTypeId(task)})),
    onPick: it => { if (it.value !== pipelineTypeId(task)) void save('type', {type: it.value}); }});
}
