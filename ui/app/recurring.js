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
    : !r.active ? '<span class="pill">not armed</span>'
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
function routineBlockHTML(r, opts = {}) {
  const id = r.id || ((r.employee || r.bot) + ':' + (r.routine_key || r.title));
  return `<div class="rblock">
    <button class="rrow" type="button" data-expand-routine="${esc(id)}" aria-expanded="false" title="Show recent runs">
      <span class="chev" aria-hidden="true">▸</span>
      <span class="trow-who">${avatar(r.employee || r.bot, 18, stateOf(r.employee || r.bot))}</span>
      <span><span class="ttl">${esc(r.title)}${routinePaused(r)}</span>${routineHowHTML(r, opts.hideBot)}</span>
      <span class="last">${routineLastHTML(r)}</span>
      <span class="next tnum">${routineNextHTML(r)}</span>
    </button>
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
  const armed = [['all', 'All'], ['armed', 'Armed'], ['idle', 'Not armed']];
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
// The routine editor: one dialog for a new routine on a bot and for editing one. What it saves
// is the hub's row; the bot reads the task the row opens when it is due (backend/routines.py).
const ROUTINE_EVENTS = [['meeting.ready', 'When a meeting is imported'],
  ['recording.ready', 'When a meeting is imported (legacy)'], ['market.insight.urgent', 'When a market insight is urgent']];
function routineEditorOpen(r = null, bot = '') {
  let dialog = $('#routine-editor');
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.className = 'bot-editor'; dialog.id = 'routine-editor'; dialog.setAttribute('aria-labelledby', 'routine-editor-title');
    document.body.appendChild(dialog);
  }
  const editing = !!r, slug = editing ? (r.employee || r.bot) : bot;
  const mine = (S.emps || []).filter(e => e.status !== 'archived' && routineMayEdit({bot: e.name}));
  const kind = editing && r.on ? 'event' : 'cron';
  const events = r?.on && !ROUTINE_EVENTS.some(([value]) => value === r.on)
    ? [...ROUTINE_EVENTS, [r.on, r.on]] : ROUTINE_EVENTS;
  dialog.innerHTML = `<form><div class="tmodal-head"><h2 id="routine-editor-title">${editing ? 'Edit routine' : 'New routine'}</h2><span class="spacer"></span><button class="ghost" type="button" data-routine-close aria-label="Close">✕</button></div>
    <div class="bot-editor-body">
      <div class="bot-editor-grid">
        <label>Bot<select name="bot" ${editing ? 'disabled' : ''} required>${(editing ? [S.emps.find(e => e.name === slug) || {name: slug}] : mine).map(e => `<option value="${esc(e.name)}" ${e.name === slug ? 'selected' : ''}>${esc(botDisplayName(e.name))}</option>`).join('')}</select></label>
        <label>Title<input name="title" type="text" value="${esc(r?.title || '')}" maxlength="300" required autocomplete="off"></label>
        <label>Runs<select name="kind"><option value="cron" ${kind === 'cron' ? 'selected' : ''}>On a schedule</option><option value="event" ${kind === 'event' ? 'selected' : ''}>When something happens</option></select></label>
        <label data-routine-cron ${kind === 'cron' ? '' : 'hidden'}>Cron<input name="cron" type="text" autocomplete="off" spellcheck="false" value="${esc(r?.cron || '0 7 * * 1-5')}" placeholder="0 7 * * 1-5" maxlength="100"></label>
        <label data-routine-event ${kind === 'event' ? '' : 'hidden'}>Event<select name="on">${events.map(([v, w]) => `<option value="${esc(v)}" ${r?.on === v ? 'selected' : ''}>${esc(w)}</option>`).join('')}</select></label>
        <label data-routine-tz ${kind === 'cron' ? '' : 'hidden'}>Timezone<input name="timezone" type="text" autocomplete="off" spellcheck="false" value="${esc(r?.timezone || 'America/Los_Angeles')}" maxlength="100"></label>
        <label class="bot-editor-wide">Instruction<textarea name="text" maxlength="100000" placeholder="Go through the messages and…">${esc(r?.text || '')}</textarea></label>
      </div>
      <div class="row" style="margin-top:16px"><button class="primary" type="submit">${editing ? 'Save routine' : 'Add routine'}</button><button class="ghost" type="button" data-routine-close>Cancel</button><span class="muted" data-routine-status></span></div></div></form>`;
  const form = dialog.querySelector('form'), status = dialog.querySelector('[data-routine-status]');
  dialog.querySelectorAll('[data-routine-close]').forEach(b => b.onclick = () => dialog.close());
  const showKind = () => {
    const isCron = form.elements.kind.value === 'cron';
    dialog.querySelector('[data-routine-cron]').hidden = !isCron;
    dialog.querySelector('[data-routine-tz]').hidden = !isCron;
    dialog.querySelector('[data-routine-event]').hidden = isCron;
  };
  form.elements.kind.onchange = showKind;
  form.onsubmit = async ev => {
    ev.preventDefault();
    const isCron = form.elements.kind.value === 'cron';
    const body = {title: form.elements.title.value.trim(), text: form.elements.text.value,
                  cron: isCron ? form.elements.cron.value.trim() : '', on: isCron ? '' : form.elements.on.value,
                  timezone: isCron ? form.elements.timezone.value.trim() : ''};
    status.textContent = 'Saving…';
    try {
      if (editing) await post(`/v2/routines/${encodeURIComponent(r.id)}`, body);
      else await post(`/v2/bots/${encodeURIComponent(form.elements.bot.value)}/routines`, body);
      dialog.close();
      routineChanged();
    } catch (e) { status.textContent = e.message; }
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
  if (kind === 'toggle') await post(`/v2/routines/${encodeURIComponent(id)}`, {enabled: r.enabled === false || r.enabled === 0});
  if (kind === 'delete') {
    if (!window.confirm(`Delete "${r.title}"? Its history stays; it will not run again.`)) return;
    await post(`/v2/routines/${encodeURIComponent(id)}/delete`, {});
  }
  routineChanged();
}
async function routineChanged() {
  if (TASKS_ST && TASKS_ST.view === 'recurring' && $('#task-body')) return tasksLoad(TASKS_ST);
  await refresh(true);     // the bot page's Routines card and the overview read the employee rows
  if (SETTINGS_TAB === 'recurring' && $('#set-recurring')) renderSettingsRecurring();
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
      if (b) { ev.preventDefault(); routineAction(kind, b.dataset[attr]).catch(e => alert(e.message)); return; }
    }
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
