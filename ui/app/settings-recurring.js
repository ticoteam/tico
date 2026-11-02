/* ui/app/settings-recurring.js — Settings > System: what Tico runs on its own, and task rules
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// "What Tico runs on its own": every job Tico runs on its own (backend/system_routines.py) in one flat list,
// no group headings, with a search box that matches name, trigger, action, department tag and group word. Each row reads
// WHEN <trigger> THEN <action>, with its department tag, schedule, last and next run and, for a job that asks Decide,
// which route answered and this month's spend. An owner or admin switches the six AI/people jobs (and Topic changed),
// picks their schedule preset and runs them now; Health scan only the owner (`can_change`, enforced by the server too).
// Fetched again at most once a minute while the tab is open.
let SYSTEM_ROUTINES = null, SYSTEM_ROUTINES_AT = 0, SYSTEM_ROUTINES_Q = '', SYSTEM_ROUTINES_HELD = false;
const SYSTEM_ROUTINE_BUSY = new Set();
// Jobs a happening starts, not a clock: they keep no run log, so the row says so in place of a last run.
const SYSTEM_ROUTINE_ON_EVENT = new Set(['topic_fresh', 'stall_triage']);
function systemRoutineLastHTML(j) {
  if (!j.records) return SYSTEM_ROUTINE_ON_EVENT.has(j.key) ? '<span class="muted">Runs when it happens</span>' : '<span class="muted">Each connection shows its own</span>';
  if (!j.last) return '<span class="muted">Not run yet</span>';
  const when = `<span class="tnum muted" title="${esc(fmt(j.last.finished_at || j.last.started_at))}">${esc(ago(j.last.finished_at || j.last.started_at))}</span>`;
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
  return `<div class="sysr-rule" data-sysr-rule><b>WHEN</b> ${part(j.when || [])} <b>THEN</b> ${part(j.then || [])}</div>`;
}
// An hour range reads 08:00–18:00, the way the rest of Settings writes one.
const systemRoutineHours = text => String(text || '').replace(/(\d\d:\d\d) to (\d\d:\d\d)/g, '$1–$2');
function systemRoutineScheduleHTML(j) {
  const busy = SYSTEM_ROUTINE_BUSY.has(j.key);
  if (!(j.presets || []).length || !j.can_change) return `<span data-sysr-schedule>${esc(systemRoutineHours(j.schedule))}</span>`;
  const options = j.presets.map(p => `<option value="${esc(p.key)}"${p.key === j.preset ? ' selected' : ''}>${esc(p.key === 'daily' ? (['learnings', 'memory_tidy'].includes(j.key) ? 'Nightly at' : 'Daily at') : systemRoutineHours(p.label))}</option>`).join('');
  // One choice is no choice: its words alone (Nightly at with the time box, or Weekly).
  const one = j.presets[0], pick = j.presets.length === 1
    ? `<span>${esc(one.key !== 'daily' ? systemRoutineHours(one.label) : ['learnings', 'memory_tidy'].includes(j.key) ? 'Nightly at' : 'Daily at')}</span>`
    : `<select class="settings-inline-select" data-sysr-preset aria-label="${esc(j.name)} schedule"${busy ? ' disabled' : ''}>${options}</select>`;
  return `${pick}
    ${j.preset === 'daily' ? `<input type="time" step="60" data-sysr-at value="${esc(j.at || '')}" aria-label="${esc(j.name)} time (Pacific)" title="Pacific time"${busy ? ' disabled' : ''}>` : ''}`;
}
function systemRoutineControlHTML(j) {
  if (!j.switch) return j.needed ? '<span class="muted sysr-needed">Tico needs this to run</span>' : '';
  const busy = SYSTEM_ROUTINE_BUSY.has(j.key);
  if (!j.can_change) {
    // A member reads on or off; an admin looking at Health scan sees why they cannot change it.
    return `<span class="sysr-state" data-sysr-state>${j.on ? 'On' : 'Off'}</span>${j.owner_only && settingsIsAdmin() ? '<span class="muted sysr-owner" data-sysr-owner>Only the owner changes this</span>' : ''}`;
  }
  return `<label class="sysr-switch">${peopleSwitch('data-sysr-on', j.on, busy, j.name)}<span>${j.on ? 'On' : 'Off'}</span></label>
    ${j.kind === 'task_rule' ? `<button type="button" class="ghost" data-task-rule-edit${busy ? ' disabled' : ''}>Edit</button>` : ''}
    ${j.controllable && !SYSTEM_ROUTINE_ON_EVENT.has(j.key) ? `<button type="button" class="ghost" data-sysr-run${busy || !j.on || j.run_unavailable ? ' disabled' : ''}${j.run_unavailable ? ` title="${esc(j.run_unavailable)}"` : j.on ? '' : ' title="Switch it on to run it"'}>${busy ? 'Running…' : 'Run now'}</button>${j.run_unavailable ? `<span class="muted">${esc(j.run_unavailable)}</span>` : ''}` : ''}`;
}
function systemRoutineRowHTML(j) {
  return `<div class="sysr-row" data-system-routine="${esc(j.key)}">
    <div class="sysr-name"><div class="sysr-title"><strong>${esc(j.name)}</strong><span class="pill sysr-dept" data-sysr-dept>${esc(j.department?.name || '')}</span></div>
      ${systemRoutineRuleHTML(j)}<span class="muted">${esc(j.what)}</span>${j.note ? `<span class="sysr-note" data-sysr-note>${esc(j.note)}</span>` : ''}${systemRoutineAIHTML(j)}</div>
    <div class="sysr-schedule"><span class="sysr-label">Schedule</span>${systemRoutineScheduleHTML(j)}</div>
    <div class="sysr-last"><span class="sysr-label">Last run</span>${systemRoutineLastHTML(j)}</div>
    <div class="sysr-next"><span class="sysr-label">Next run</span>${j.next ? `<span class="tnum" title="${esc(fmt(j.next))}" data-sysr-next>${esc(until(j.next))}</span>` : `<span class="muted">${j.switch && !j.on ? 'Off' : '—'}</span>`}</div>
    <div class="sysr-ctl">${systemRoutineControlHTML(j)}${j.key === 'memory_tidy' && j.can_change ? '<button type="button" class="ghost" data-memory-tidy-edit>Pilot list</button>' : ''}<p class="settings-cell-note" role="status" data-sysr-status></p></div>
  </div>`;
}
function systemRoutineRowsHTML(data) {
  const jobs = (data.jobs || []).filter(j => !SYSTEM_ROUTINES_Q || searchIncludes([j.search, j.name], SYSTEM_ROUTINES_Q));
  return jobs.length ? jobs.map(systemRoutineRowHTML).join('') : '<div class="empty" data-sysr-none>No job matches.</div>';
}
function systemRoutinesHTML(data) {
  return `<div class="settings-bots-filters">${data.can_create_task_rule ? '<button type="button" class="primary" data-task-rule-new>New task rule</button>' : ''}<input class="settings-filter-search sysr-search" type="search" autocomplete="off" data-sysr-search
      value="${esc(SYSTEM_ROUTINES_Q)}" placeholder="Search jobs" aria-label="Search jobs"></div>
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
    note = out.running ? 'Running…' : '';
    if (!out.running) toast('Saved');
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
  const before = (SYSTEM_ROUTINES?.jobs || []).find(j => j.key === key)?.last?.started_at || '';
  setTimeout(async () => {
    try { SYSTEM_ROUTINES = await get('/v2/system-routines'); SYSTEM_ROUTINES_AT = Date.now(); } catch (e) { return; }
    systemRoutinesPaint();
    const now = (SYSTEM_ROUTINES.jobs || []).find(j => j.key === key)?.last?.started_at || '';
    if (now === before && n < 24) systemRoutineFollow(key, n + 1);
  }, 5000);
}
function systemRoutinesBind(el) {
  const add = el.querySelector('[data-task-rule-new]');
  if (add) add.onclick = () => taskRuleEditor();
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
    const pilot = ev.target.closest('[data-memory-tidy-edit]');
    if (pilot) { memoryTidyEditor(SYSTEM_ROUTINES.jobs.find(j => j.key === 'memory_tidy')); return; }
    const edit = ev.target.closest('[data-task-rule-edit]');
    if (edit && !edit.disabled) { taskRuleEditor(SYSTEM_ROUTINES.jobs.find(j => j.key === edit.closest('[data-system-routine]').dataset.systemRoutine)); return; }
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
  catch (e) { if (!SYSTEM_ROUTINES) el.innerHTML = `<div class="empty">Couldn't load the jobs: ${esc(e.message || 'try again')}</div>`; return; }
  if (SYSTEM_ROUTINE_BUSY.size || formBusy(el.querySelector('[data-sysr-list]'))) return;
  systemRoutinesPaint();
}


function taskRuleEditor(job) {
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
      dialog.close(); systemRoutinesPaint(); toast('Saved');
    } catch (e) { dialog.querySelector('[data-task-rule-error]').textContent = e.message || 'Could not save the rule'; }
    finally { button.disabled = false; }
  };
  document.body.appendChild(dialog); dialog.showModal(); form.elements.ruleName.focus();
}


function memoryTidyEditor(job) {
  const dialog = document.createElement('dialog');
  dialog.className = 'tmodal task-rule-dialog';
  const choices = job.pilot_models || [];
  dialog.innerHTML = `<form class="task-rule-form"><header><h2>Nightly memory tidy pilot</h2><button type="button" class="ghost" data-close>Cancel</button></header>
    <p>Up to three bots. Use each bot's slug. These models apply only to the tidy pass. The pilot stops at $50.</p>
    <p class="muted">Only models enabled in Settings > Computers > Models are offered. Haiku-tier models cannot be used.</p>
    <div data-pilot-rows></div><button type="button" class="ghost" data-add>Add bot</button>
    <p class="err" role="status" data-error></p><footer><button type="submit" class="primary">Save pilot list</button></footer></form>`;
  const rows = dialog.querySelector('[data-pilot-rows]'), add = dialog.querySelector('[data-add]');
  const addRow = (entry = {}) => {
    const row = document.createElement('div'); row.dataset.pilotRow = '';
    row.innerHTML = `<label>Bot slug<input data-bot required maxlength="100" value="${esc(entry.bot || '')}"></label>
      <label>Pass model<select data-model required><option value="">Choose a model</option>${choices.map(c => `<option value="${esc(c.model)}"${c.model === entry.model ? ' selected' : ''}>${esc(c.label)}</option>`).join('')}
      ${entry.model && !choices.some(c => c.model === entry.model) ? `<option value="${esc(entry.model)}" selected disabled>${esc(entry.model)} (unavailable)</option>` : ''}</select></label>
      <label>Reasoning effort<select data-effort><option value="low">low</option></select></label><button type="button" class="ghost" data-remove>Remove bot</button>`;
    row.querySelector('[data-remove]').onclick = () => { row.remove(); add.disabled = false; };
    rows.appendChild(row); add.disabled = rows.children.length >= 3;
  };
  (job.pilots || []).forEach(addRow);
  add.onclick = () => { if (rows.children.length < 3) addRow(); };
  dialog.querySelector('[data-close]').onclick = () => dialog.close();
  dialog.addEventListener('close', () => dialog.remove());
  dialog.querySelector('form').onsubmit = async ev => {
    ev.preventDefault(); const button = dialog.querySelector('[type="submit"]'); button.disabled = true;
    const pilots = Array.from(rows.children).map(row => ({bot: row.querySelector('[data-bot]').value.trim(),
      model: row.querySelector('[data-model]').value, effort: row.querySelector('[data-effort]').value}));
    try {
      const out = await put('/v2/system-routines/memory_tidy', {pilots});
      systemRoutineReplace(out.job); dialog.close(); systemRoutinesPaint(); toast('Saved');
    } catch (e) { dialog.querySelector('[data-error]').textContent = e.message || 'Could not save the pilot list'; }
    finally { button.disabled = false; }
  };
  document.body.appendChild(dialog); dialog.showModal(); add.focus();
}
