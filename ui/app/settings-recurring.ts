/* ui/app/settings-recurring.ts — Automations > System jobs (#/automations/system-jobs): what Tico runs on its own
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// One job as GET /api/v2/system-routines lists it (backend/system_routines.py).
interface SysRoutineJob {
  key: string;
  name: string;
  what?: string;
  note?: string | null;
  search?: string;
  department?: { id: string; name: string } | null;
  when?: string[];
  then?: string[];
  schedule?: string;
  presets?: { key: string; label: string }[];
  preset?: string | null;
  at?: string | null;
  can_change?: boolean;
  owner_only?: boolean;
  switch?: boolean;
  needed?: boolean;
  on?: boolean;
  controllable?: boolean;
  run_unavailable?: string | null;
  ai?: boolean;
  route?: string | null;
  fallback?: string | null;
  spent_this_month_usd?: number | null;
  records?: boolean;
  last?: { started_at?: string | null; finished_at?: string | null; ok?: boolean; result?: string } | null;
  next?: string | null;
  triggers?: { key?: string; hours?: number }[];
  actions?: string[];
}

// What GET /api/v2/system-routines answers: the jobs, and the departments the webhook editor reads.
interface SysRoutinesData {
  jobs: SysRoutineJob[];
  task_rule_departments?: { id: string; name: string }[];
}

// What a save or a run answers: the job as it now reads, and whether a run is still going.
interface SysRoutineWrite {
  job: SysRoutineJob;
  running?: boolean;
}

const SYSTEM_ROUTINE_BUSY = new Set<string>();
// Jobs a happening starts, not a clock: they keep no run log, so the row says so in place of a last run.
const SYSTEM_ROUTINE_ON_EVENT = new Set<string>(['topic_fresh']);
// The jobs once they have loaded. Every handler below runs after the list has drawn, so missing jobs is a bug, not a state.
function loadedRoutines(): SysRoutinesData {
  if (!SYSTEM_ROUTINES) throw new Error('The system jobs are not loaded');
  return SYSTEM_ROUTINES;
}
function systemRoutineLastHTML(j: SysRoutineJob) {
  if (!j.records) return SYSTEM_ROUTINE_ON_EVENT.has(j.key) ? '<span class="muted">Runs when it happens</span>' : '<span class="muted">Each connection shows its own</span>';
  if (!j.last) return '<span class="muted">Not run yet</span>';
  const when = `<span class="tnum muted" title="${esc(fmt(j.last.finished_at || j.last.started_at))}">${esc(ago(j.last.finished_at || j.last.started_at))}</span>`;
  return `<span class="${j.last.ok ? '' : 'sysr-failed'}" data-sysr-result>${esc(j.last.result)}</span> ${when}`;
}
function systemRoutineAIHTML(j: SysRoutineJob) {
  if (!j.ai) return '';
  const spent = j.spent_this_month_usd != null ? `<span class="tnum" data-sysr-spend>$${Number(j.spent_this_month_usd).toFixed(2)} this month</span>` : '';
  return `<div class="sysr-ai"><span class="pill" data-sysr-route>${esc(j.route ? 'AI: ' + j.route : 'Uses AI')}</span>${spent}
    ${j.fallback ? `<div class="sysr-fallback" data-sysr-fallback>${esc(j.fallback)}</div>` : ''}</div>`;
}
function systemRoutineRuleHTML(j: SysRoutineJob) {
  const part = (list: string[]) => list.map(esc).join(' <b class="sysr-and">AND</b> ');
  return `<div class="sysr-rule" data-sysr-rule><b>WHEN</b> ${part(j.when || [])} <b>THEN</b> ${part(j.then || [])}</div>`;
}
// An hour range reads 08:00–18:00, the way the rest of Settings writes one.
const systemRoutineHours = (text: string | null | undefined) => String(text || '').replace(/(\d\d:\d\d) to (\d\d:\d\d)/g, '$1–$2');
function systemRoutineScheduleHTML(j: SysRoutineJob) {
  const busy = SYSTEM_ROUTINE_BUSY.has(j.key);
  const presets = j.presets || [];
  if (!presets.length || !j.can_change) return `<span data-sysr-schedule>${esc(systemRoutineHours(j.schedule))}</span>`;
  const options = presets.map(p => `<option value="${esc(p.key)}"${p.key === j.preset ? ' selected' : ''}>${esc(p.key === 'daily' ? (j.key === 'learnings' ? 'Nightly at' : 'Daily at') : systemRoutineHours(p.label))}</option>`).join('');
  // One choice is no choice: its words alone (Nightly at with the time box, or Weekly).
  const one = presets[0], pick = presets.length === 1 && one
    ? `<span>${esc(one.key !== 'daily' ? systemRoutineHours(one.label) : j.key === 'learnings' ? 'Nightly at' : 'Daily at')}</span>`
    : `<select class="settings-inline-select" data-sysr-preset aria-label="${esc(j.name)} schedule"${busy ? ' disabled' : ''}>${options}</select>`;
  return `${pick}
    ${j.preset === 'daily' ? `<input type="time" step="60" data-sysr-at value="${esc(j.at || '')}" aria-label="${esc(j.name)} time (Pacific)" title="Pacific time"${busy ? ' disabled' : ''}>` : ''}`;
}
function systemRoutineControlHTML(j: SysRoutineJob) {
  if (!j.switch) return j.needed ? '<span class="muted sysr-needed">Tico needs this to run</span>' : '';
  const busy = SYSTEM_ROUTINE_BUSY.has(j.key);
  if (!j.can_change) {
    // A member reads on or off; an admin looking at a job only the owner changes sees why they cannot change it.
    return `<span class="sysr-state" data-sysr-state>${j.on ? 'On' : 'Off'}</span>${j.owner_only && settingsIsAdmin() ? '<span class="muted sysr-owner" data-sysr-owner>Only the owner changes this</span>' : ''}`;
  }
  return `<label class="sysr-switch">${peopleSwitch('data-sysr-on', j.on, busy, j.name)}<span>${j.on ? 'On' : 'Off'}</span></label>
    ${j.controllable && !SYSTEM_ROUTINE_ON_EVENT.has(j.key) ? `<button type="button" class="ghost" data-sysr-run${busy || !j.on || j.run_unavailable ? ' disabled' : ''}${j.run_unavailable ? ` title="${esc(j.run_unavailable)}"` : j.on ? '' : ' title="Switch it on to run it"'}>${busy ? 'Running…' : 'Run now'}</button>${j.run_unavailable ? `<span class="muted">${esc(j.run_unavailable)}</span>` : ''}` : ''}`;
}
function systemRoutineRowHTML(j: SysRoutineJob) {
  return `<div class="sysr-row" data-system-routine="${esc(j.key)}">
    <div class="sysr-name"><div class="sysr-title"><strong>${esc(j.name)}</strong><span class="pill sysr-dept" data-sysr-dept>${esc(j.department?.name || '')}</span></div>
      ${systemRoutineRuleHTML(j)}<span class="muted">${esc(j.what)}</span>${j.note ? `<span class="sysr-note" data-sysr-note>${esc(j.note)}</span>` : ''}${systemRoutineAIHTML(j)}</div>
    <div class="sysr-schedule"><span class="sysr-label">Schedule</span>${systemRoutineScheduleHTML(j)}</div>
    <div class="sysr-last"><span class="sysr-label">Last run</span>${systemRoutineLastHTML(j)}</div>
    <div class="sysr-next"><span class="sysr-label">Next run</span>${j.next ? `<span class="tnum" title="${esc(fmt(j.next))}" data-sysr-next>${esc(until(j.next))}</span>` : `<span class="muted">${j.switch && !j.on ? 'Off' : '—'}</span>`}</div>
    <div class="sysr-ctl">${systemRoutineControlHTML(j)}<p class="settings-cell-note" role="status" data-sysr-status></p></div>
  </div>`;
}
function systemRoutineRowsHTML(data: SysRoutinesData) {
  const jobs = (data.jobs || []).filter(j => !SYSTEM_ROUTINES_Q || searchIncludes([j.search, j.name], SYSTEM_ROUTINES_Q));
  return jobs.length ? jobs.map(systemRoutineRowHTML).join('') : '<div class="empty" data-sysr-none>No job matches.</div>';
}
function systemRoutinesHTML(data: SysRoutinesData) {
  return `<div class="settings-bots-filters"><input class="settings-filter-search sysr-search" type="search" autocomplete="off" data-sysr-search
      value="${esc(SYSTEM_ROUTINES_Q)}" placeholder="Search jobs" aria-label="Search jobs"></div>
    <div class="sysr-list" data-sysr-list>${systemRoutineRowsHTML(data)}</div>`;
}
function systemRoutinesPaint() {
  const el: Element | null = $('#set-system-routines'); if (!el || !SYSTEM_ROUTINES) return;
  const list = el.querySelector<HTMLElement>('[data-sysr-list]');
  // A redraw (another row's save, a run being followed) would wipe a half-typed time, so it waits for the box to close.
  if (list?.querySelector('[data-sysr-at]:focus')) { SYSTEM_ROUTINES_HELD = true; return; }
  SYSTEM_ROUTINES_HELD = false;
  if (list) list.innerHTML = systemRoutineRowsHTML(SYSTEM_ROUTINES);
  else el.innerHTML = systemRoutinesHTML(SYSTEM_ROUTINES);
  systemRoutinesBind(el);
}
function systemRoutineReplace(job: SysRoutineJob | null | undefined) {
  if (!SYSTEM_ROUTINES || !job) return;
  SYSTEM_ROUTINES.jobs = SYSTEM_ROUTINES.jobs.map(j => j.key === job.key ? job : j);
}
async function systemRoutineWrite(key: string, send: () => Promise<SysRoutineWrite>, status?: string) {
  SYSTEM_ROUTINE_BUSY.add(key); systemRoutinesPaint();
  let note = '', failed = false, again = false;
  try {
    const out = await send();
    systemRoutineReplace(out.job);
    note = out.running ? 'Running…' : '';
    if (!out.running) toast('Saved');
    again = !!out.running;
  } catch (e) { note = errorMessage(e, 'Try again'); failed = true; }
  finally { SYSTEM_ROUTINE_BUSY.delete(key); }
  systemRoutinesPaint();
  const box: Element | null = $(`[data-system-routine="${key}"] [data-sysr-status]`);
  if (box) { box.textContent = note; box.classList.toggle('err', failed); }
  if (again) systemRoutineFollow(key, 0);
}
// A Run now that asks Decide works on its own thread: read its row again until its result lands (about two minutes).
function systemRoutineFollow(key: string, n: number) {
  const before = (SYSTEM_ROUTINES?.jobs || []).find(j => j.key === key)?.last?.started_at || '';
  setTimeout(async () => {
    try { SYSTEM_ROUTINES = await get<SysRoutinesData>('/v2/system-routines'); SYSTEM_ROUTINES_AT = Date.now(); } catch (e) { return; }
    systemRoutinesPaint();
    const now = (SYSTEM_ROUTINES.jobs || []).find(j => j.key === key)?.last?.started_at || '';
    if (now === before && n < 24) systemRoutineFollow(key, n + 1);
  }, 5000);
}
function systemRoutinesBind(el: Element) {
  const search = el.querySelector<HTMLInputElement>('[data-sysr-search]');
  if (search) search.oninput = () => { SYSTEM_ROUTINES_Q = search.value; const list = el.querySelector<HTMLElement>('[data-sysr-list]'); if (list) list.innerHTML = systemRoutineRowsHTML(loadedRoutines()); };
  const list = el.querySelector<HTMLElement>('[data-sysr-list]'); if (!list) return;
  list.onchange = ev => {
    const target = eventElement(ev), row = target?.closest<HTMLElement>('[data-system-routine]'); if (!row) return;
    const key = row.dataset.systemRoutine ?? '';
    const path = '/v2/system-routines/' + encodeURIComponent(key);
    if (target instanceof HTMLInputElement && target.matches('[data-sysr-on]')) void systemRoutineWrite(key, () => put(path, {on: target.checked}));
    else if (target instanceof HTMLSelectElement && target.matches('[data-sysr-preset]')) {
      const body: {preset: string; at?: string} = {preset: target.value};
      const job = loadedRoutines().jobs.find(j => j.key === key);
      if (body.preset === 'daily' && !job?.at) body.at = '09:00';
      void systemRoutineWrite(key, () => put(path, body));
    }
  };
  // The daily time saves when the box is left (or Enter), not on each keystroke: a save redraws the row, and saving
  // after the hour alone would take the focus before the minutes are typed.
  const saveAt = (input: HTMLInputElement) => {
    const row = input.closest<HTMLElement>('[data-system-routine]')!, key = row.dataset.systemRoutine ?? '';
    const job = loadedRoutines().jobs.find(j => j.key === key);
    if (!input.value || input.value === job?.at) { if (SYSTEM_ROUTINES_HELD) systemRoutinesPaint(); return; }
    void systemRoutineWrite(key, () => put('/v2/system-routines/' + encodeURIComponent(key), {at: input.value}));
  };
  // There is no onfocusout property, so it is a listener, added once: the list element outlives its redraws.
  if (!list.dataset.sysrBound) {
    list.dataset.sysrBound = '1';
    list.addEventListener('focusout', ev => { const target = eventElement(ev); if (target instanceof HTMLInputElement && target.matches('[data-sysr-at]')) saveAt(target); });
  }
  list.onkeydown = ev => { const target = eventElement(ev); if (ev.key === 'Enter' && target instanceof HTMLInputElement && target.matches('[data-sysr-at]')) target.blur(); };
  list.onclick = ev => {
    const target = eventElement(ev);
    const run = target?.closest<HTMLButtonElement>('[data-sysr-run]'); if (!run || run.disabled) return;
    const key = run.closest<HTMLElement>('[data-system-routine]')!.dataset.systemRoutine ?? '';
    void systemRoutineWrite(key, () => post('/v2/system-routines/' + encodeURIComponent(key) + '/run', {}));
  };
}
async function renderSystemRoutines(force?: boolean | undefined) {
  const el: Element | null = $('#set-system-routines'); if (!el) return;
  if (SYSTEM_ROUTINES && !el.querySelector('[data-sysr-list]')) { el.innerHTML = systemRoutinesHTML(SYSTEM_ROUTINES); systemRoutinesBind(el); }
  if (!force && SYSTEM_ROUTINES && Date.now() - SYSTEM_ROUTINES_AT < 60000) return;
  SYSTEM_ROUTINES_AT = Date.now();
  try { SYSTEM_ROUTINES = await get<SysRoutinesData>('/v2/system-routines'); }
  catch (e) { if (!SYSTEM_ROUTINES) el.innerHTML = `<div class="empty">Couldn't load the jobs: ${esc(errorMessage(e, 'try again'))}</div>`; return; }
  if (SYSTEM_ROUTINE_BUSY.size || formBusy(el.querySelector('[data-sysr-list]'))) return;
  systemRoutinesPaint();
}
