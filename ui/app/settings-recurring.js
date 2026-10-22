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

// The System section above the bot routines: every job Tico runs on its own timers (backend/system_routines.py), by
// group, with its schedule, last run, next run and, for a job that asks Decide, which route answered and this month's
// spend. Read-only; fetched again at most once a minute while the tab is open.
let SYSTEM_ROUTINES = null, SYSTEM_ROUTINES_AT = 0;
function systemRoutineLastHTML(j) {
  if (!j.records) return '<span class="muted">Each connection shows its own</span>';
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
function systemRoutineRowHTML(j) {
  return `<div class="sysr-row" data-system-routine="${esc(j.key)}">
    <div class="sysr-name"><strong>${esc(j.name)}</strong><span class="muted">${esc(j.what)}</span>
      ${j.needed ? '<span class="muted sysr-needed">Tico needs this to run</span>' : ''}${systemRoutineAIHTML(j)}</div>
    <div class="sysr-schedule" data-sysr-schedule>${esc(j.schedule)}</div>
    <div class="sysr-last"><span class="sysr-label">Last run</span>${systemRoutineLastHTML(j)}</div>
    <div class="sysr-next"><span class="sysr-label">Next run</span>${j.next ? `<span class="tnum" title="${esc(fmt(j.next))}" data-sysr-next>${esc(until(j.next))}</span>` : '<span class="muted">—</span>'}</div>
  </div>`;
}
function systemRoutinesHTML(data) {
  return (data.groups || []).map(g => `<div class="sysr-group" data-system-group="${esc(g.key)}">
    <h3>${esc(g.name)}</h3>${g.jobs.map(systemRoutineRowHTML).join('')}</div>`).join('');
}
async function renderSystemRoutines(force) {
  const el = $('#set-system-routines'); if (!el) return;
  if (SYSTEM_ROUTINES) el.innerHTML = systemRoutinesHTML(SYSTEM_ROUTINES);
  if (!force && SYSTEM_ROUTINES && Date.now() - SYSTEM_ROUTINES_AT < 60000) return;
  SYSTEM_ROUTINES_AT = Date.now();
  try { SYSTEM_ROUTINES = await get('/v2/system-routines'); }
  catch (e) { if (!SYSTEM_ROUTINES) el.innerHTML = `<div class="empty">Couldn't load the system routines: ${esc(e.message || 'try again')}</div>`; return; }
  const now = $('#set-system-routines'); if (now) now.innerHTML = systemRoutinesHTML(SYSTEM_ROUTINES);
}
