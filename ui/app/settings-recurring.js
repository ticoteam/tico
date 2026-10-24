/* ui/app/settings-recurring.js — Settings > Routines
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// One place to see every routine on every bot, searchable and filtered by bot,
// by computer (including "my computer"), kind and state. Rows are the same blocks as the Tasks
// view, so Edit, Pause and Delete work here too.
const SETTINGS_RECURRING = {q: '', bot: '', computer: '', kind: 'all', armed: 'all'};
function settingsRoutineRows() {
  const byBot = new Map((S.emps || []).map(e => [e.name, e]));
  return (S.status?.schedules || []).map(s => ({...s, _bot: byBot.get(s.employee || s.bot)}));
}
function settingsRoutineComputer(r) {
  const m = r._bot?.machine;
  return m ? m.runner_id : 'none';
}
function renderSettingsRecurring() {
  const el = $('#set-recurring'); if (!el) return;
  const f = SETTINGS_RECURRING, all = settingsRoutineRows();
  const computers = new Map();
  all.forEach(r => { const m = r._bot?.machine; const k = settingsRoutineComputer(r);
    if (!computers.has(k)) computers.set(k, m ? `${m.label}${m.operator ? ' · ' + settingsPersonName(m.operator) : ''}` : 'No computer'); });
  const bots = [...new Set(all.map(r => r.employee || r.bot))].sort((a, b) => empName(a).localeCompare(empName(b)));
  const rows = all.filter(r => routineMatches(r, f)
    && (!f.computer || (f.computer === 'mine' ? r._bot?.machine?.operator === S.me?.id : settingsRoutineComputer(r) === f.computer))
    && (!f.q || searchIncludes([r.title, cadenceWords(r), empName(r.employee || r.bot), r.employee || r.bot], f.q)))
    .sort((a, b) => (a.active ? 0 : 1) - (b.active ? 0 : 1) || String(a.next || '~').localeCompare(String(b.next || '~')) || a.title.localeCompare(b.title));
  const opt = (v, label, cur) => `<option value="${esc(v)}" ${v === cur ? 'selected' : ''}>${esc(label)}</option>`;
  const sel = (name, label, options) => `<select class="settings-inline-select" data-recurring-filter="${name}" aria-label="${label}">${options}</select>`;
  const add = (S.emps || []).some(e => routineMayEdit({bot: e.name}))
    ? '<button class="primary" type="button" data-new-routine="">New routine</button>' : '';
  el.innerHTML = `${add}<div class="settings-bots-filters">
      <input class="settings-filter-search" type="search" autocomplete="off" data-recurring-filter="q" value="${esc(f.q)}" placeholder="Search routines" aria-label="Search routines">
      ${sel('bot', 'Bot', opt('', 'All bots', f.bot) + bots.map(b => opt(b, empName(b), f.bot)).join(''))}
      ${sel('computer', 'Computer', opt('', 'All computers', f.computer) + opt('mine', 'My computer', f.computer) + [...computers].sort((a, b) => a[1].localeCompare(b[1])).map(([k, v]) => opt(k, v, f.computer)).join(''))}
      ${sel('kind', 'Kind', [['all', 'Any kind'], ['cron', 'On a schedule'], ['event', 'On an event'], ['inbox', 'On a message']].map(([k, v]) => opt(k, v, f.kind)).join(''))}
      ${sel('armed', 'State', [['all', 'Any state'], ['armed', 'Armed'], ['idle', 'Not armed']].map(([k, v]) => opt(k, v, f.armed)).join(''))}
      <span class="settings-bots-count" data-recurring-count>${rows.length === all.length ? `${all.length} routines` : `${rows.length} of ${all.length} routines`}</span></div>
    ${rows.length ? `<div class="rlist">${rows.map(r => routineBlockHTML(r)).join('')}</div>` : `<div class="empty">${all.length ? 'No routines match these filters.' : 'No routines yet.'}</div>`}`;
  bindRoutineExpand(el);
  el.oninput = el.onchange = ev => {
    const input = ev.target.closest('[data-recurring-filter]'); if (!input) return;
    if (ev.type === 'input' && input.tagName !== 'INPUT') return;
    SETTINGS_RECURRING[input.dataset.recurringFilter] = input.value;
    const focus = input.tagName === 'INPUT' ? input.selectionStart : null;
    renderSettingsRecurring();
    if (focus != null) { const box = el.querySelector('input[data-recurring-filter="q"]'); box.focus(); box.setSelectionRange(focus, focus); }
  };
}

// The Routines list above the bot routines: every job Tico runs on its own (backend/system_routines.py) in one flat list,
// no group headings, with a search box that matches name, trigger, action, department tag and group word. Each row reads
// WHEN <trigger> THEN <action>, with its department tag, schedule, last and next run and, for a job that asks Decide,
// which route answered and this month's spend. An owner or admin switches the six AI/people jobs (and Topic changed),
// picks their schedule preset and runs them now; Health scan only the owner (`can_change`, enforced by the server too).
// Fetched again at most once a minute while the tab is open.
let SYSTEM_ROUTINES = null, SYSTEM_ROUTINES_AT = 0, SYSTEM_ROUTINES_Q = '', SYSTEM_ROUTINES_HELD = false;
const SYSTEM_ROUTINE_BUSY = new Set();
function systemRoutineLastHTML(j) {
  // A task check that runs when it happens shows what it caught, overrode and skipped instead of a last run.
  if (j.check && !j.last) return `<span data-sysr-stats>${esc(j.stats || '')}</span>`;
  if (!j.records) return j.key === 'topic_fresh' ? '<span class="muted">Runs when it happens</span>' : '<span class="muted">Each connection shows its own</span>';
  if (!j.last) return '<span class="muted">Not run yet</span>';
  const when = `<span class="tnum muted" title="${esc(fmt(j.last.finished || j.last.started))}">${esc(ago(j.last.finished || j.last.started))}</span>`;
  return `<span class="${j.last.ok ? '' : 'sysr-failed'}" data-sysr-result>${esc(j.last.result)}</span> ${when}`;
}
function systemRoutineAIHTML(j) {
  if (!j.ai) return '';
  const spent = j.spent_this_month_usd != null ? `<span class="tnum" data-sysr-spend>$${Number(j.spent_this_month_usd).toFixed(2)} this month</span>` : '';
  return `<div class="sysr-ai"><span class="pill" data-sysr-route>${esc(j.route ? 'AI: ' + j.route : 'Uses AI')}</span>${spent}
    ${j.fallback ? `<div class="sysr-fallback" data-sysr-fallback>${esc(j.fallback)}</div>` : ''}</div>`;
}
function systemRoutineRuleHTML(j) {
  const part = list => list.map(esc).join(' <b class="sysr-and">AND</b> ');
  const check = j.check ? ` <b>FOR</b> ${esc(j.for || 'all types')} <b>CHECK</b> ${esc(j.check_words || '')}` : '';
  return `<div class="sysr-rule" data-sysr-rule><b>WHEN</b> ${part(j.when || [])}${check} <b>THEN</b> ${part(j.then || [])}</div>`;
}
function systemRoutineScheduleHTML(j) {
  const busy = SYSTEM_ROUTINE_BUSY.has(j.key);
  if (!(j.presets || []).length || !j.can_change) return `<span data-sysr-schedule>${esc(j.schedule)}</span>`;
  const options = j.presets.map(p => `<option value="${esc(p.key)}"${p.key === j.preset ? ' selected' : ''}>${esc(p.key === 'daily' ? (j.key === 'learnings' ? 'Nightly at' : 'Daily at') : p.label)}</option>`).join('');
  const pick = j.presets.length === 1 ? `<span>${esc(j.key === 'learnings' ? 'Nightly at' : 'Daily at')}</span>`
    : `<select class="settings-inline-select" data-sysr-preset aria-label="${esc(j.name)} schedule"${busy ? ' disabled' : ''}>${options}</select>`;
  return `${pick}
    ${j.preset === 'daily' ? `<input type="time" step="60" data-sysr-at value="${esc(j.at || '')}" aria-label="${esc(j.name)} time (Pacific)" title="Pacific time"${busy ? ' disabled' : ''}>` : ''}`;
}
function systemRoutineControlHTML(j) {
  if (!j.switch) return j.needed ? '<span class="muted sysr-needed">Tico needs this to run</span>' : '';
  const busy = SYSTEM_ROUTINE_BUSY.has(j.key);
  if (!j.can_change && j.checkable) return `<span class="sysr-state" data-sysr-state>${j.on ? 'On' : 'Off'}</span><button type="button" class="ghost" data-task-check-now>Check now</button>`;
  if (!j.can_change) {
    // A member reads on or off; an admin looking at Health scan sees why they cannot change it.
    return `<span class="sysr-state" data-sysr-state>${j.on ? 'On' : 'Off'}</span>${j.owner_only && settingsIsAdmin() ? '<span class="muted sysr-owner" data-sysr-owner>Only the owner changes this</span>' : ''}`;
  }
  return `<label class="sysr-switch">${peopleSwitch('data-sysr-on', j.on, busy, j.name)}<span>${j.on ? 'On' : 'Off'}</span></label>
    ${j.kind === 'task_rule' ? `<button type="button" class="ghost" data-task-rule-edit${busy ? ' disabled' : ''}>Edit</button>` : ''}
    ${j.checkable ? `<button type="button" class="ghost" data-task-check-now${busy ? ' disabled' : ''}>Check now</button>` : ''}
    ${j.controllable ? `<button type="button" class="ghost" data-sysr-run${busy || !j.on ? ' disabled' : ''}${j.on ? '' : ' title="Switch it on to run it"'}>${busy ? 'Running…' : 'Run now'}</button>` : ''}`;
}
function systemRoutineRowHTML(j) {
  return `<div class="sysr-row" data-system-routine="${esc(j.key)}">
    <div class="sysr-name"><div class="sysr-title"><strong>${esc(j.name)}</strong><span class="pill sysr-dept" data-sysr-dept>${esc(j.department?.name || '')}</span></div>
      ${systemRoutineRuleHTML(j)}<span class="muted">${esc(j.what)}</span>${j.note ? `<span class="sysr-note" data-sysr-note>${esc(j.note)}</span>` : ''}${systemRoutineAIHTML(j)}</div>
    <div class="sysr-schedule"><span class="sysr-label">Schedule</span>${systemRoutineScheduleHTML(j)}</div>
    <div class="sysr-last"><span class="sysr-label">Last run</span>${systemRoutineLastHTML(j)}</div>
    <div class="sysr-next"><span class="sysr-label">Next run</span>${j.next ? `<span class="tnum" title="${esc(fmt(j.next))}" data-sysr-next>${esc(until(j.next))}</span>` : `<span class="muted">${j.switch && !j.on ? 'Off' : '—'}</span>`}</div>
    <div class="sysr-ctl">${systemRoutineControlHTML(j)}<p class="settings-cell-note" role="status" data-sysr-status></p></div>
  </div>`;
}
function systemRoutineRowsHTML(data) {
  const jobs = (data.jobs || []).filter(j => !SYSTEM_ROUTINES_Q || searchIncludes([j.search, j.name], SYSTEM_ROUTINES_Q));
  return jobs.length ? jobs.map(systemRoutineRowHTML).join('') : '<div class="empty" data-sysr-none>No routine matches.</div>';
}
function systemRoutinesHTML(data) {
  const checks = (data.jobs || []).some(j => j.checkable) ? '<button type="button" class="ghost" data-task-check-all>Check all</button>' : '';
  return `<div class="settings-bots-filters">${data.can_create_task_rule ? '<button type="button" class="primary" data-task-rule-new>New task rule</button>' : ''}${checks}<input class="settings-filter-search sysr-search" type="search" autocomplete="off" data-sysr-search
      value="${esc(SYSTEM_ROUTINES_Q)}" placeholder="Search by name, when, then or department" aria-label="Search routines"></div>
    <div class="sysr-list" data-sysr-list>${systemRoutineRowsHTML(data)}</div>`;
}
function systemRoutinesPaint() {
  const el = $('#set-system-routines'); if (!el || !SYSTEM_ROUTINES) return;
  const list = el.querySelector('[data-sysr-list]');
  // A redraw (another row's save, a run being followed) would wipe a half-typed time, so it waits for the box to close.
  if (list?.querySelector('[data-sysr-at]:focus')) { SYSTEM_ROUTINES_HELD = true; return; }
  SYSTEM_ROUTINES_HELD = false;
  if (list) list.innerHTML = systemRoutineRowsHTML(SYSTEM_ROUTINES);
  else el.innerHTML = systemRoutinesHTML(SYSTEM_ROUTINES);
  systemRoutinesBind(el);
}
function systemRoutineReplace(job) {
  if (!SYSTEM_ROUTINES || !job) return;
  SYSTEM_ROUTINES.jobs = SYSTEM_ROUTINES.jobs.map(j => j.key === job.key ? job : j);
}
async function systemRoutineWrite(key, send, status) {
  SYSTEM_ROUTINE_BUSY.add(key); systemRoutinesPaint();
  let note = '', failed = false, again = false;
  try {
    const out = await send();
    systemRoutineReplace(out.job);
    note = out.running ? 'Running…' : 'Saved';
    again = !!out.running;
  } catch (e) { note = e.message || 'Try again'; failed = true; }
  finally { SYSTEM_ROUTINE_BUSY.delete(key); }
  systemRoutinesPaint();
  const box = $(`[data-system-routine="${key}"] [data-sysr-status]`);
  if (box) { box.textContent = note; box.classList.toggle('err', failed); }
  if (again) systemRoutineFollow(key, 0);
}
// A Run now that asks Decide works on its own thread: read its row again until its result lands (about two minutes).
function systemRoutineFollow(key, n) {
  const before = (SYSTEM_ROUTINES?.jobs || []).find(j => j.key === key)?.last?.started || '';
  setTimeout(async () => {
    try { SYSTEM_ROUTINES = await get('/v2/system-routines'); SYSTEM_ROUTINES_AT = Date.now(); } catch (e) { return; }
    systemRoutinesPaint();
    const now = (SYSTEM_ROUTINES.jobs || []).find(j => j.key === key)?.last?.started || '';
    if (now === before && n < 24) systemRoutineFollow(key, n + 1);
  }, 5000);
}
function systemRoutinesBind(el) {
  const add = el.querySelector('[data-task-rule-new]');
  if (add) add.onclick = () => taskRuleEditor();
  const all = el.querySelector('[data-task-check-all]');
  if (all) all.onclick = () => taskCheckRun(null);
  const search = el.querySelector('[data-sysr-search]');
  if (search) search.oninput = () => { SYSTEM_ROUTINES_Q = search.value; const list = el.querySelector('[data-sysr-list]'); if (list) list.innerHTML = systemRoutineRowsHTML(SYSTEM_ROUTINES); };
  const list = el.querySelector('[data-sysr-list]'); if (!list) return;
  list.onchange = ev => {
    const row = ev.target.closest('[data-system-routine]'); if (!row) return;
    const key = row.dataset.systemRoutine, job = SYSTEM_ROUTINES.jobs.find(j => j.key === key);
    const path = (job?.kind === 'task_rule' ? '/v2/task-rules/' : '/v2/system-routines/') + encodeURIComponent(key);
    if (ev.target.matches('[data-sysr-on]')) void systemRoutineWrite(key, () => put(path, {on: ev.target.checked}));
    else if (ev.target.matches('[data-sysr-preset]')) {
      const body = {preset: ev.target.value};
      const job = SYSTEM_ROUTINES.jobs.find(j => j.key === key);
      if (body.preset === 'daily' && !job?.at) body.at = '09:00';
      void systemRoutineWrite(key, () => put(path, body));
    }
  };
  // The daily time saves when the box is left (or Enter), not on each keystroke: a save redraws the row, and saving
  // after the hour alone would take the focus before the minutes are typed.
  const saveAt = input => {
    const row = input.closest('[data-system-routine]'), key = row.dataset.systemRoutine;
    const job = SYSTEM_ROUTINES.jobs.find(j => j.key === key);
    if (!input.value || input.value === job?.at) { if (SYSTEM_ROUTINES_HELD) systemRoutinesPaint(); return; }
    const path = (job?.kind === 'task_rule' ? '/v2/task-rules/' : '/v2/system-routines/') + encodeURIComponent(key);
    void systemRoutineWrite(key, () => put(path, {at: input.value}));
  };
  // There is no onfocusout property, so it is a listener, added once: the list element outlives its redraws.
  if (!list.dataset.sysrBound) {
    list.dataset.sysrBound = '1';
    list.addEventListener('focusout', ev => { if (ev.target.matches('[data-sysr-at]')) saveAt(ev.target); });
  }
  list.onkeydown = ev => { if (ev.key === 'Enter' && ev.target.matches('[data-sysr-at]')) ev.target.blur(); };
  list.onclick = ev => {
    const edit = ev.target.closest('[data-task-rule-edit]');
    if (edit && !edit.disabled) { taskRuleEditor(SYSTEM_ROUTINES.jobs.find(j => j.key === edit.closest('[data-system-routine]').dataset.systemRoutine)); return; }
    const now = ev.target.closest('[data-task-check-now]');
    if (now && !now.disabled) { taskCheckRun(now.closest('[data-system-routine]').dataset.systemRoutine); return; }
    const run = ev.target.closest('[data-sysr-run]'); if (!run || run.disabled) return;
    const key = run.closest('[data-system-routine]').dataset.systemRoutine;
    void systemRoutineWrite(key, () => post('/v2/system-routines/' + encodeURIComponent(key) + '/run', {}));
  };
}
async function renderSystemRoutines(force) {
  const el = $('#set-system-routines'); if (!el) return;
  if (SYSTEM_ROUTINES && !el.querySelector('[data-sysr-list]')) { el.innerHTML = systemRoutinesHTML(SYSTEM_ROUTINES); systemRoutinesBind(el); }
  if (!force && SYSTEM_ROUTINES && Date.now() - SYSTEM_ROUTINES_AT < 60000) return;
  SYSTEM_ROUTINES_AT = Date.now();
  try { SYSTEM_ROUTINES = await get('/v2/system-routines'); }
  catch (e) { if (!SYSTEM_ROUTINES) el.innerHTML = `<div class="empty">Couldn't load the routines: ${esc(e.message || 'try again')}</div>`; return; }
  if (SYSTEM_ROUTINE_BUSY.size || formBusy(el.querySelector('[data-sysr-list]'))) return;
  systemRoutinesPaint();
}


function taskRuleEditor(job) {
  if (job?.check) return taskCheckEditor(job);
  const data = SYSTEM_ROUTINES, dialog = document.createElement('dialog');
  dialog.className = 'tmodal task-rule-dialog';
  const department = job?.department?.id || data.task_rule_department || '';
  const options = (data.task_rule_departments || []).map(d => `<option value="${esc(d.id)}"${d.id === department ? ' selected' : ''}>${esc(d.name)}</option>`).join('');
  dialog.innerHTML = `<form class="task-rule-form"><header><h2>${job ? 'Edit task rule' : 'New task rule'}</h2><button type="button" class="ghost" data-close>Cancel</button></header>
    <label>Name<input name="ruleName" maxlength="120" required value="${esc(job?.name || 'Close after done')}"></label>
    <label>Department<select name="department" required><option value="">Choose a department</option>${options}</select></label>
    <label>WHEN a task has been done and untouched for <input name="hours" type="number" min="1" max="8760" required value="${job?.triggers?.[0]?.hours || 24}"> hours</label>
    <p><b>THEN</b> close it. Reopen the task to undo.</p>
    <p class="muted">Skips tasks waiting on a person, snoozed, pinned, or with an open question or pending approval. Closes at most 50 tasks per pass across all rules.</p>
    <label><input type="checkbox" name="enabled"${job?.on === false ? '' : ' checked'}> On</label>
    <p class="err" role="status" data-task-rule-error></p><footer><button type="submit" class="primary">Save rule</button></footer></form>`;
  const form = dialog.querySelector('form');
  dialog.querySelector('[data-close]').onclick = () => dialog.close();
  dialog.addEventListener('close', () => dialog.remove());
  form.onsubmit = async ev => {
    ev.preventDefault(); const button = form.querySelector('[type="submit"]'); button.disabled = true;
    const values = new FormData(form), hours = Number(values.get('hours'));
    const body = {name: values.get('ruleName'), department: values.get('department'), on: values.has('enabled'),
      triggers: [{key: 'done_untouched', hours}, ...(job?.triggers || []).slice(1)], actions: job?.actions || ['close']};
    try {
      const out = job ? await put('/v2/task-rules/' + encodeURIComponent(job.key), body) : await post('/v2/task-rules', body);
      if (job) systemRoutineReplace(out.job); else SYSTEM_ROUTINES.jobs.push(out.job);
      dialog.close(); systemRoutinesPaint();
    } catch (e) { dialog.querySelector('[data-task-rule-error]').textContent = e.message || 'Could not save the rule'; }
    finally { button.disabled = false; }
  };
  document.body.appendChild(dialog); dialog.showModal(); form.elements.ruleName.focus();
}


// A task check's editor: its name, department, FOR task types, THEN action and, for quiet waits, the quiet time. The
// trigger and the check stay the row's; the server checks the department's edit rights.
function taskCheckEditor(job) {
  const data = SYSTEM_ROUTINES, dialog = document.createElement('dialog');
  const spec = (data.task_checks || []).find(c => c.key === job.check.key) || {actions: [], hours: false};
  dialog.className = 'tmodal task-rule-dialog';
  const department = job.department?.id || '';
  const options = (data.task_rule_departments || []).map(d => `<option value="${esc(d.id)}"${d.id === department ? ' selected' : ''}>${esc(d.name)}</option>`).join('');
  const types = (data.task_types || []).map(t => `<label><input type="checkbox" name="types" value="${esc(t.id)}"${(job.types || []).includes(t.id) ? ' checked' : ''}> ${esc(t.name)}</label>`).join('');
  const single = spec.actions.some(a => a.key === 'block');
  const then = single
    ? `<select name="action">${spec.actions.map(a => `<option value="${esc(a.key)}"${job.actions?.[0] === a.key ? ' selected' : ''}>${esc(a.words)}</option>`).join('')}</select>`
    : spec.actions.map(a => `<label><input type="checkbox" name="actions" value="${esc(a.key)}"${(job.actions || []).includes(a.key) ? ' checked' : ''}> ${esc(a.words)}</label>`).join(' then ');
  dialog.innerHTML = `<form class="task-rule-form"><header><h2>Edit task check</h2><button type="button" class="ghost" data-close>Cancel</button></header>
    <label>Name<input name="ruleName" maxlength="120" required value="${esc(job.name)}"></label>
    <label>Department<select name="department" required>${options}</select></label>
    <p><b>WHEN</b> ${esc((job.when || [])[0] || '')}</p>
    <fieldset class="task-check-types"><legend><b>FOR</b> task types (none ticked: all types)</legend>${types}</fieldset>
    <p><b>CHECK</b> ${esc(job.check_words || '')}</p>
    ${spec.hours ? `<label>Quiet for <input name="hours" type="number" min="1" max="8760" value="${esc(job.check.hours || '')}"> hours</label>` : ''}
    <div><b>THEN</b> ${then}</div>
    <label><input type="checkbox" name="enabled"${job.on ? ' checked' : ''}> On</label>
    <p class="err" role="status" data-task-rule-error></p><footer><button type="submit" class="primary">Save check</button></footer></form>`;
  const form = dialog.querySelector('form');
  dialog.querySelector('[data-close]').onclick = () => dialog.close();
  dialog.addEventListener('close', () => dialog.remove());
  form.onsubmit = async ev => {
    ev.preventDefault(); const button = form.querySelector('[type="submit"]'); button.disabled = true;
    const values = new FormData(form);
    const body = {name: values.get('ruleName'), department: values.get('department'), on: values.has('enabled'),
      types: values.getAll('types'), actions: single ? [values.get('action')] : values.getAll('actions')};
    if (spec.hours) body.check = {key: job.check.key, hours: values.get('hours') ? Number(values.get('hours')) : null};
    try {
      const out = await put('/v2/task-rules/' + encodeURIComponent(job.key), body);
      systemRoutineReplace(out.job); dialog.close(); systemRoutinesPaint();
    } catch (e) { dialog.querySelector('[data-task-rule-error]').textContent = e.message || 'Could not save the check'; }
    finally { button.disabled = false; }
  };
  document.body.appendChild(dialog); dialog.showModal(); form.elements.ruleName.focus();
}

// Check now (one check) or Check all: a dry run that lists what breaks each check and changes nothing. A person may
// then confirm the listed fix, on at most 50 tasks, which runs with their own rights on each task.
async function taskCheckRun(key) {
  const dialog = document.createElement('dialog');
  dialog.className = 'tmodal task-rule-dialog';
  dialog.innerHTML = `<div class="task-rule-form"><header><h2>${key ? 'Check now' : 'Check all'}</h2><button type="button" class="ghost" data-close>Close</button></header><div data-task-check-out><p class="muted">Checking…</p></div></div>`;
  dialog.querySelector('[data-close]').onclick = () => dialog.close();
  dialog.addEventListener('close', () => dialog.remove());
  document.body.appendChild(dialog); dialog.showModal();
  const out = dialog.querySelector('[data-task-check-out]');
  let results;
  try { results = key ? [await post('/v2/task-rules/' + encodeURIComponent(key) + '/check', {})] : (await post('/v2/task-rules/check-all', {})).checks; }
  catch (e) { out.innerHTML = `<p class="err">${esc(e.message || 'Could not check')}</p>`; return; }
  const person = ['owner', 'human'].includes(S.me?.role);
  out.innerHTML = results.map((r, i) => `<section data-task-check-result="${esc(r.key)}"><h3>${esc(r.name)}</h3>
    ${r.found.length ? `<ul>${r.found.map(f => `<li>${esc(f.task.title)} <span class="muted">(${esc(f.task.status)}, ${esc(f.task.owner)}, ${esc(f.task.age)})</span>: ${esc(f.why)}</li>`).join('')}</ul>
      ${person ? `<button type="button" class="primary" data-task-check-fix="${i}">${esc(r.fix[0].toUpperCase() + r.fix.slice(1))}: ${r.found.length} task${r.found.length === 1 ? '' : 's'}</button>` : ''}
      <p class="settings-cell-note" role="status" data-task-check-status></p>`
      : '<p class="muted">Nothing breaks this check.</p>'}</section>`).join('') || '<p class="muted">No check is on.</p>';
  out.onclick = async ev => {
    const button = ev.target.closest('[data-task-check-fix]'); if (!button) return;
    const r = results[Number(button.dataset.taskCheckFix)], status = button.parentElement.querySelector('[data-task-check-status]');
    button.disabled = true;
    try {
      const done = await post('/v2/task-rules/' + encodeURIComponent(r.key) + '/fix',
        {tasks: r.found.map(f => ({task: f.task.id, ...(f.duplicate_of ? {duplicate_of: f.duplicate_of.id} : {})}))});
      const refused = done.results.filter(x => !x.ok);
      status.textContent = `Fixed ${done.fixed}` + (refused.length ? `; ${refused.length} refused: ${refused[0].detail}` : '');
    } catch (e) { status.textContent = e.message || 'Could not fix'; status.classList.add('err'); button.disabled = false; }
  };
}
