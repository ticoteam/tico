/* ui/app/recurring.js — Routines on Tasks and bot pages: rows, editor, actions
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Recurring work as a list of its own, not as cards on the board. One row per
// routine from every bot's employee.yaml (the dispatcher's status carries the cadence and the
// next fire): who, the title with the cadence under it, the last run, and when it fires next.
// One plain list, no headings: active routines first, soonest first, inactive ones at the end.
// The last run is the newest Issue or hub task the schedule created with that title; tapping the
// row opens it in the same modal as any other task. Armed routines first, soonest first.
const isRecurringTask = t => !!t.routine_id || /from schedule/.test(String(t.body || ''));
function routineItems(state) {
  const source = state.routines != null ? state.routines : (S.status?.schedules || []);
  const scheds = source.filter(s => routineMatches(s, state) && (!state.q || searchIncludes(
    [s.title, cadenceWords(s), actorLabel('bot:' + (s.employee || s.bot)), s.employee || s.bot], state.q)));
  const runs = (state.tasks || []).filter(isRecurringTask).map(taskItem).sort(byNewest);
  return scheds.map(s => ({...s, last: runs.find(r => (r.task?.routine_id ? r.task.routine_id === s.id : r.slug === (s.employee || s.bot) && r.title === s.title)) || null}))
    .sort((a, b) => (a.active ? 0 : 1) - (b.active ? 0 : 1) || String(a.next || '~').localeCompare(String(b.next || '~')) || a.title.localeCompare(b.title));
}
function routineNextHTML(r) {
  return r.held_by_bot ? '<span class="pill">paused with the bot</span>'
    : !r.active ? '<span class="pill">off</span>'
    : r.on ? '<span class="pill">on event</span>'
    : `<strong>${esc(until(r.next))}</strong><span class="muted">${esc(fmt(r.next))}</span>`;
}
function routineLastHTML(r) {
  const running = new Set((S.status?.active || []).map(a => a.issue));
  if (r.last) {
    const col = r.last.kind === 'issue' ? boardColumn(r.last.issue, running) : hubColumn(r.last.task);
    const word = {scheduled: 'scheduled', doing: 'doing', waiting: 'waiting', needs: (typeof needsWho === 'function' && r.last.task) ? needsWho(r.last.task) : 'needs you', done: 'done'}[col] || col;
    return `<span class="pill ${col === 'done' ? 'ok' : col === 'needs' ? 'fail' : col === 'doing' ? 'waiting' : ''}">${esc(word)}</span><span class="tnum">${esc(ago(r.last.closed || r.last.updated))}</span>`;
  }
  if (r.last_fired) return `<span class="tnum">${esc(ago(r.last_fired))}</span>`;
  return '<span class="muted">never run</span>';
}
// `opts.actions` (Settings > Rules > Bot routines): the routine's on/off switch and Edit sit on its row, no expanding.
function routineActsHTML(r, id) {
  if (!r.id || !routineMayEdit(r)) return '';
  const on = !(r.enabled === false || r.enabled === 0);
  return `<div class="rrow-acts"><label class="rrow-switch">${peopleSwitch(`data-routine-on="${esc(id)}"`, on, false, `${r.title}: on`)}<span>${on ? 'On' : 'Off'}</span></label>
    <button class="ghost" type="button" data-edit-routine="${esc(id)}" aria-label="Edit ${esc(r.title)}">Edit</button></div>`;
}
function routineBlockHTML(r, opts = {}) {
  const id = r.id || ((r.employee || r.bot) + ':' + (r.routine_key || r.title));
  const acts = opts.actions ? routineActsHTML(r, id) : '';
  return `<div class="rblock${acts ? ' has-acts' : ''}">
    <button class="rrow" type="button" data-expand-routine="${esc(id)}" aria-expanded="false" title="Show recent runs">
      <span class="chev" aria-hidden="true">▸</span>
      <span class="trow-who">${avatar(r.employee || r.bot, 18, stateOf(r.employee || r.bot))}</span>
      <span><span class="ttl">${esc(r.title)}${routinePaused(r)}</span>${routineHowHTML(r, opts.hideBot)}</span>
      <span class="last">${routineLastHTML(r)}</span>
      <span class="next tnum">${routineNextHTML(r)}</span>
    </button>${acts}
    <div class="rocc" hidden></div>
  </div>`;
}
// The bot's routines under More; "New routine" sits in the card's header (bot-page.js).
function botRoutinesHTML(e, slug) {
  if (!e.schedules?.length) return '<div class="empty">No routines yet.</div>';
  return `<div class="rlist">${e.schedules.map(s => {
    const st = (S.status?.schedules || []).find(x => (s.id ? x.id === s.id : x.employee === slug && x.title === s.title)) || {};
    const r = {...s, ...st, employee: slug, active: st.active != null ? st.active : s.active};
    return routineBlockHTML(r, {hideBot: true});
  }).join('')}</div>`;
}
function recurringFiltersHTML(state) {
  const kinds = [['all', 'All'], ['cron', 'Cron'], ['event', 'Event'], ['inbox', 'Message']];
  const armed = [['all', 'All'], ['armed', 'On'], ['idle', 'Off']];
  const chips = (key, items) => items.map(([k, label]) =>
    `<button type="button" class="rchip${state[key] === k ? ' on' : ''}" data-rfilter="${key}" data-val="${k}">${label}</button>`).join('');
  const add = (S.me?.role === 'owner' || (S.emps || []).some(e => e.operator === S.me?.id))
    ? `<span class="spacer"></span><button class="ghost" type="button" data-new-routine="${esc(state.bot && !state.bot.startsWith('human:') ? state.bot : '')}">New routine</button>` : '';
  return `<div class="r-filters">
    <div class="group"><span class="lbl">Kind</span>${chips('kind', kinds)}</div>
    <div class="group"><span class="lbl">State</span>${chips('armed', armed)}</div>${add}
  </div>`;
}
function tasksRecurringHTML(state) {
  const rows = routineItems(state);
  const all = state.routines != null ? state.routines : (S.status?.schedules || []);
  const empty = !all.length
    ? (state.bot ? `${esc(empName(state.bot))} has no routines yet.` : 'No routines yet.')
    : 'No routines match these filters.';
  return `<section class="card">${recurringFiltersHTML(state)}${rows.length ? `<div class="rlist">${rows.map(r => routineBlockHTML(r)).join('')}</div>` : `<div class="empty">${empty}</div>`}</section>`;
}
async function toggleRoutineOccurrences(btn) {
  const id = btn.dataset.expandRoutine;
  const panel = btn.parentElement && btn.parentElement.querySelector('.rocc');
  if (!id || !panel) return;
  const open = btn.getAttribute('aria-expanded') === 'true';
  if (open) { btn.setAttribute('aria-expanded', 'false'); panel.hidden = true; return; }
  btn.setAttribute('aria-expanded', 'true');
  panel.hidden = false;
  if (panel.dataset.loaded) return;
  panel.innerHTML = '<div class="muted">Loading runs…</div>';
  try {
    const d = await get(`/v2/routines/${encodeURIComponent(id)}/occurrences`);
    const r = routineById(id);
    const actions = r && routineMayEdit(r) ? `<div class="row" style="margin:8px 0 4px">
      <button class="ghost" type="button" data-edit-routine="${esc(id)}">Edit</button>
      <button class="ghost" type="button" data-toggle-routine="${esc(id)}">${r.enabled === false || r.enabled === 0 ? 'Resume' : 'Pause'}</button>
      <button class="ghost" type="button" data-delete-routine="${esc(id)}">Delete</button></div>` : '';
    panel.innerHTML = actions + occurrenceTableHTML(d.occurrences || []);
    panel.dataset.loaded = '1';
  } catch (e) { panel.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}
function routineById(id) {
  const rows = (TASKS_ST && TASKS_ST.routines) || S.status?.schedules || [];
  return rows.find(r => r.id === id) || (S.emps || []).flatMap(e => (e.schedules || []).map(s => ({...s, employee: e.name}))).find(r => r.id === id) || null;
}
// The routine editor: one dialog for a new routine on a bot, for editing one and, for someone who may not
// edit it, for reading what it says. What it saves is the hub's row; the bot reads the task the row opens
// when it is due (backend/routines.py). A schedule is picked as words (every day, weekdays, a weekday, a
// day of the month, at a time) and saved as a cron; anything else stays a cron to type.
// "In Tico" events come from the catalog (GET /v2/event-types). Only these groups' producers start routines
// (backend/routines.py emit callers); the catalog has no flag for it, and task, approval and bot events reach
// outgoing webhooks and rules only.
const ROUTINE_EVENT_GROUPS = new Set(['meeting', 'market', 'release', 'strategy']);
function routineEvents(current) {
  const out = EVENT_TYPES.filter(t => ROUTINE_EVENT_GROUPS.has(t.group) && !t.name.includes('*')).map(t => {
    // A routine saved under an older spelling (recording.ready, release_published) keeps it, under the event's words.
    const value = (t.aliases || []).includes(current) ? current : t.name;
    return [value, t.description.replace(/\.$/, '')];
  });
  return out;
}
let ROUTINE_TRIGGERS = null;     // Settings > Webhooks > Incoming (GET /v2/triggers), loaded when the editor or that tab opens
async function routineTriggersLoad() {
  try { ROUTINE_TRIGGERS = (await get('/v2/triggers')).triggers || []; } catch { ROUTINE_TRIGGERS = ROUTINE_TRIGGERS || []; }
  return ROUTINE_TRIGGERS;
}
const WEEKDAYS = [['1', 'Monday'], ['2', 'Tuesday'], ['3', 'Wednesday'], ['4', 'Thursday'], ['5', 'Friday'], ['6', 'Saturday'], ['0', 'Sunday']];
// "30 9 * * 1" → {repeat: 'weekly', day: '1', time: '09:30'}; a cron the words cannot say is 'custom'.
function cronPick(cron) {
  const f = String(cron || '').trim().split(/\s+/);
  const num = (v, max) => /^\d{1,2}$/.test(v) && +v <= max;
  if (f.length !== 5 || !num(f[0], 59) || !num(f[1], 23) || f[3] !== '*') return {repeat: 'custom'};
  const time = `${String(f[1]).padStart(2, '0')}:${String(f[0]).padStart(2, '0')}`;
  if (f[2] === '*' && f[4] === '*') return {repeat: 'daily', time};
  if (f[2] === '*' && f[4] === '1-5') return {repeat: 'weekdays', time};
  if (f[2] === '*' && /^[0-7]$/.test(f[4])) return {repeat: 'weekly', time, day: f[4] === '7' ? '0' : f[4]};
  if (num(f[2], 31) && +f[2] >= 1 && f[4] === '*') return {repeat: 'monthly', time, date: f[2]};
  return {repeat: 'custom'};
}
function cronOf(pick) {
  const [h, m] = String(pick.time || '09:00').split(':').map(n => String(+n || 0));
  return {daily: `${m} ${h} * * *`, weekdays: `${m} ${h} * * 1-5`, weekly: `${m} ${h} * * ${pick.day || '1'}`,
    monthly: `${m} ${h} ${pick.date || '1'} * *`}[pick.repeat] || String(pick.cron || '').trim();
}
// The team's timezone for a new routine: the team's setting when the server sends one, else the zone most of the team's
// routines already use, else the scheduler's own default (backend/routines.py).
function routineTeamZone() {
  if (S.config?.timezone) return S.config.timezone;
  const count = new Map();
  (S.status?.schedules || []).forEach(s => { if (s.timezone && !s.on) count.set(s.timezone, (count.get(s.timezone) || 0) + 1); });
  return [...count].sort((a, b) => b[1] - a[1])[0]?.[0] || 'America/Los_Angeles';
}
function routineZones(current) {
  let zones = [];
  try { zones = Intl.supportedValuesOf('timeZone'); } catch { /* an older browser: the current zone and UTC only */ }
  return [...new Set([...zones, 'UTC', current].filter(Boolean))].sort((a, b) => a.localeCompare(b));
}
function routineEditorOpen(r = null, bot = '') {
  let dialog = $('#routine-editor');
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.className = 'bot-editor'; dialog.id = 'routine-editor'; dialog.setAttribute('aria-labelledby', 'routine-editor-title');
    document.body.appendChild(dialog);
  }
  const editing = !!r, slug = editing ? (r.employee || r.bot) : bot;
  const may = !editing || routineMayEdit(r), off = may ? '' : 'disabled';
  const mine = (S.emps || []).filter(e => e.status !== 'archived' && routineMayEdit({bot: e.name}));
  const kind = editing && r.on ? 'event' : 'cron';
  const pick = kind === 'cron' ? cronPick(editing ? r.cron : '0 9 * * 1-5') : cronPick('0 9 * * 1-5');
  const paused = editing && (r.enabled === false || r.enabled === 0);
  const opt = (v, w, cur) => `<option value="${esc(v)}" ${v === cur ? 'selected' : ''}>${esc(w)}</option>`;
  dialog.innerHTML = `<form><div class="tmodal-head"><h2 id="routine-editor-title">${!editing ? 'New routine' : may ? 'Edit routine' : 'Routine'}</h2><span class="spacer"></span><button class="ghost" type="button" data-routine-close aria-label="Close">✕</button></div>
    <div class="bot-editor-body">
      <div class="bot-editor-grid">
        <label>Bot<select name="bot" ${editing ? 'disabled' : ''} required>${(editing ? [S.emps.find(e => e.name === slug) || {name: slug}] : mine).map(e => opt(e.name, botDisplayName(e.name), slug)).join('')}</select></label>
        <label>Title<input name="title" type="text" value="${esc(r?.title || '')}" maxlength="300" required autocomplete="off" ${off}></label>
        <label>Runs<select name="kind" ${off}>${opt('cron', 'On a schedule', kind)}${opt('event', 'On an event', kind)}</select></label>
        <label data-routine-cron>Repeat<select name="repeat" ${off}>${[['daily', 'Every day'], ['weekdays', 'Weekdays'], ['weekly', 'Every week'], ['monthly', 'Every month'], ['custom', 'Custom (cron)']].map(([v, w]) => opt(v, w, pick.repeat)).join('')}</select></label>
        <label data-routine-pick="weekly">On<select name="day" ${off}>${WEEKDAYS.map(([v, w]) => opt(v, w, pick.day || '1')).join('')}</select></label>
        <label data-routine-pick="monthly">Day of the month<input name="date" type="number" min="1" max="31" value="${esc(pick.date || '1')}" ${off}></label>
        <label data-routine-pick="daily weekdays weekly monthly">At<input name="time" type="time" value="${esc(pick.time || '09:00')}" ${off}></label>
        <label data-routine-pick="custom">Cron<input name="cron" type="text" autocomplete="off" spellcheck="false" value="${esc(r?.cron || '0 9 * * 1-5')}" placeholder="0 9 * * 1-5" maxlength="100" ${off}></label>
        <label data-routine-cron>Timezone<select name="timezone" ${off}>${routineZones(r?.timezone || routineTeamZone()).map(z => opt(z, z.replace(/_/g, ' '), r?.timezone || routineTeamZone())).join('')}</select></label>
        <label data-routine-event>Event<select name="on" ${off}></select></label>
        <p class="settings-cell-note bot-editor-wide" data-routine-event>Webhooks are added in <a href="#/settings/webhooks" data-routine-close>Settings › Webhooks</a>.</p>
        <label class="bot-editor-wide">Instruction<textarea name="text" maxlength="100000" placeholder="Go through the messages and…" ${off}>${esc(r?.text || '')}</textarea></label>
      </div>
      <div class="row" style="margin-top:16px">${may ? `<button class="primary" type="submit">${editing ? 'Save routine' : 'Add routine'}</button>` : ''}<button class="ghost" type="button" data-routine-close>${may ? 'Cancel' : 'Close'}</button>
        <span class="muted" data-routine-status></span>
        ${editing && may ? `<span class="spacer"></span>${kind === 'cron' && !paused ? '<button class="ghost" type="button" data-routine-do="run">Run now</button>' : ''}
          <button class="ghost" type="button" data-routine-do="toggle">${paused ? 'Resume' : 'Pause'}</button>
          <button class="ghost" type="button" data-routine-do="delete">Delete</button>` : ''}</div></div></form>`;
  const form = dialog.querySelector('form'), status = dialog.querySelector('[data-routine-status]');
  dialog.querySelectorAll('[data-routine-close]').forEach(b => b.addEventListener('click', () => dialog.close()));
  const fillTriggers = list => {
    const webhooks = (list || []).map(t => [t.event, t.name]), tico = routineEvents(r?.on);
    const known = [...webhooks, ...tico].some(([v]) => v === r?.on);
    form.elements.on.innerHTML = (webhooks.length ? `<optgroup label="Webhooks">${webhooks.map(([v, w]) => opt(v, w, r?.on)).join('')}</optgroup>` : '')
      + (tico.length ? `<optgroup label="In Tico">${tico.map(([v, w]) => opt(v, w, r?.on)).join('')}</optgroup>` : '')
      + (r?.on && !known ? `<optgroup label="Removed">${opt(r.on, r.on, r.on)}</optgroup>` : '');
  };
  fillTriggers(ROUTINE_TRIGGERS);
  Promise.all([routineTriggersLoad(), eventTypesLoad().catch(() => [])]).then(([list]) => { if (dialog.open) fillTriggers(list); });
  const show = () => {
    const isCron = form.elements.kind.value === 'cron', repeat = form.elements.repeat.value;
    dialog.querySelectorAll('[data-routine-cron]').forEach(el => { el.hidden = !isCron; });
    dialog.querySelectorAll('[data-routine-event]').forEach(el => { el.hidden = isCron; });
    dialog.querySelectorAll('[data-routine-pick]').forEach(el => { el.hidden = !isCron || !el.dataset.routinePick.split(' ').includes(repeat); });
  };
  form.elements.kind.onchange = form.elements.repeat.onchange = () => {
    // Switching to Custom starts from the schedule the words were showing.
    if (form.elements.repeat.value === 'custom' && cronPick(form.elements.cron.value).repeat !== 'custom') {
      form.elements.cron.value = cronOf({repeat: cronPick(form.elements.cron.value).repeat, ...pickOf()});
    }
    show();
  };
  const pickOf = () => ({repeat: form.elements.repeat.value, day: form.elements.day.value, date: form.elements.date.value,
    time: form.elements.time.value, cron: form.elements.cron.value});
  show();
  dialog.querySelectorAll('[data-routine-do]').forEach(b => b.onclick = async () => {
    const what = b.dataset.routineDo;
    if (what === 'delete' && !await settingsConfirm({title: `Delete "${r.title}"?`, ok: 'Delete', text: 'Its history stays; it will not run again.'})) return;
    status.classList.remove('err'); status.textContent = what === 'run' ? 'Starting…' : 'Saving…';
    try {
      if (what === 'run') await post(`/v2/routines/${encodeURIComponent(r.id)}/run`, {});
      if (what === 'toggle') await post(`/v2/routines/${encodeURIComponent(r.id)}`, {enabled: paused});
      if (what === 'delete') await post(`/v2/routines/${encodeURIComponent(r.id)}/delete`, {});
      dialog.close();
      toast(what === 'run' ? 'Routine started' : what === 'delete' ? 'Deleted' : 'Saved');
      routineChanged();
    } catch (e) { status.classList.add('err'); status.textContent = e.message; }
  });
  form.onsubmit = async ev => {
    ev.preventDefault();
    if (!may) return;
    const isCron = form.elements.kind.value === 'cron';
    const body = {title: form.elements.title.value.trim(), text: form.elements.text.value,
                  cron: isCron ? cronOf(pickOf()) : '', on: isCron ? '' : form.elements.on.value,
                  timezone: isCron ? form.elements.timezone.value : ''};
    status.classList.remove('err'); status.textContent = 'Saving…';
    try {
      if (editing) await post(`/v2/routines/${encodeURIComponent(r.id)}`, body);
      else await post(`/v2/bots/${encodeURIComponent(form.elements.bot.value)}/routines`, body);
      dialog.close();
      toast(editing ? 'Saved' : 'Routine added');
      routineChanged();
    } catch (e) { status.classList.add('err'); status.textContent = e.message; }
  };
  if (editing && r.text == null) {
    // The list carries no text; fetch the bot's full rows before the person edits blind.
    get(`/v2/bots/${encodeURIComponent(slug)}/routines`).then(d => {
      const full = (d.routines || []).find(x => x.id === r.id);
      if (full && !form.elements.text.value) form.elements.text.value = full.text || '';
    }).catch(() => {});
  }
  dialog.showModal();
}
async function routineAction(kind, id) {
  const r = routineById(id);
  if (!r) return;
  if (kind === 'edit') return routineEditorOpen(r);
  if (kind === 'toggle') { await post(`/v2/routines/${encodeURIComponent(id)}`, {enabled: r.enabled === false || r.enabled === 0}); toast('Saved'); }
  if (kind === 'delete') {
    if (!await settingsConfirm({title: `Delete "${r.title}"?`, ok: 'Delete', text: 'Its history stays; it will not run again.'})) return;
    await post(`/v2/routines/${encodeURIComponent(id)}/delete`, {});
    toast('Deleted');
  }
  routineChanged();
}
// The row's on/off switch: saves at once, says Saved, and on a refusal goes back with the reason under the row.
async function routineSetEnabled(input) {
  const id = input.dataset.routineOn, block = input.closest('.rblock'), word = input.closest('label')?.querySelector('span');
  settingsFieldClear(block);
  input.disabled = true;
  try {
    await post(`/v2/routines/${encodeURIComponent(id)}`, {enabled: input.checked});
    toast('Saved');
    if (word) word.textContent = input.checked ? 'On' : 'Off';
    input.disabled = false;
    void routineChanged();
  } catch (e) {
    input.checked = !input.checked; input.disabled = false;
    settingsFieldError(block, e.message);
  }
}
async function routineChanged() {
  if (TASKS_ST && TASKS_ST.view === 'recurring' && $('#task-body')) return tasksLoad(TASKS_ST);
  await refresh(true);     // the bot page's Routines card and the overview read the employee rows
  if (SETTINGS_TAB === 'automation' && $('#set-recurring')) renderSettingsRecurring();
  // A deleted routine stayed on the bot page. route() only switches tabs on the
  // same bot, so redraw the two routine cards from the fresh employee rows here.
  const e = BOT && S.emps.find(x => x.name === BOT.slug);
  if (e) {
    if ($('#bot-routines-list')) $('#bot-routines-list').innerHTML = botRoutinesHTML(e, BOT.slug);
    botRecurringPaint(e, BOT.slug);
  }
  route();
}
function bindRoutineActions(root) {
  root.addEventListener('click', ev => {
    const add = ev.target.closest('[data-new-routine]');
    if (add) { ev.preventDefault(); routineEditorOpen(null, add.dataset.newRoutine || ''); return; }
    for (const [attr, kind] of [['editRoutine', 'edit'], ['toggleRoutine', 'toggle'], ['deleteRoutine', 'delete']]) {
      const b = ev.target.closest(`[data-${kind}-routine]`);
      if (b) { ev.preventDefault(); routineAction(kind, b.dataset[attr]).catch(e => settingsFieldError(b.closest('.rblock') || b, e.message)); return; }
    }
  });
  root.addEventListener('change', ev => {
    const on = ev.target.closest('[data-routine-on]');
    if (on) void routineSetEnabled(on);
  });
}
function bindRoutineExpand(root) {
  if (!root || root.dataset.roccBound) return;
  root.dataset.roccBound = '1';
  bindRoutineActions(root);
  root.addEventListener('click', ev => {
    if (ev.target.closest('[data-open-task], a[href^="#/task/"]')) return;
    const btn = ev.target.closest('[data-expand-routine]');
    if (!btn || !root.contains(btn)) return;
    ev.preventDefault();
    toggleRoutineOccurrences(btn);
  });
}
