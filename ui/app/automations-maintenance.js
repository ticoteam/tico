/* ui/app/automations-maintenance.js — Maintenance: Tico's one scheduled upkeep run, as a built-in row on the Automations
   list and its own screen (#/automations/maintenance[/runs]): the run's schedule, pause and monthly cap, each step's
   switch, "How often" and number of bots, Run now, and the run history with each step's line. Beside it, System jobs
   (#/automations/system-jobs): every job Tico runs on its own and the task rules, drawn by ui/app/settings-recurring.js.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   Payloads: backend/maintenance.py (GET/PATCH /api/v2/maintenance, GET /maintenance/runs, POST /maintenance/run).
   Anyone signed in reads it; `can_change` (an owner or an admin) decides whether the controls work. */
'use strict';

const AU_MAINT = 'maintenance';
const AU_MAINT_OWNER = 'tico';   // the list's Owner: Tico itself runs it
const AU_MAINT_SCHEDULES = [['hourly', 'Every hour'], ['2h', 'Every 2 hours'], ['3h', 'Every 3 hours'], ['6h', 'Every 6 hours'],
  ['12h', 'Every 12 hours'], ['daily', 'Once a day']];
const AU_MAINT_OFTEN = [['run', 'Every run'], ['day', 'At most once a day'], ['week', 'At most once a week'], ['custom', 'Custom']];
const AU_MAINT_CAPS = [0, 10, 25, 50, 100, 250, 500];
const AU_MAINT_WHAT = {collect: 'What changed since the last run', clean_up: 'Old data past retention',
  check_health: 'Setup problems to BotOps', find_stuck: 'Wakes bots on work that stalled', learn: 'Passes on what people said',
  suggest: 'Ideas from Tico Playbooks', update: 'Each bot that worked writes its update', dream: 'Bots tidy their memory'};
const AU_MAINT_BOTS = {find_stuck: 'Most bots woken per run', dream: 'Most bots per run'};
const AU_MAINT_BETA = new Set(['learn']);
const AU_MAINT_PRIVACY = '#/settings/general/settings-privacy';
const AU_MAINT_RUNS_PAGE = 25;

const AU_SYSJOBS = 'system-jobs';
const AU_BUILTIN_PILL = '<span class="pill">Built-in</span>';
const auMaintOk = m => !!m && Array.isArray(m.steps) && !!m.settings;
const auMaintStep = key => (AU?.maint?.steps || []).find(s => s.key === key);
const auMaintSchedule = s => (AU_MAINT_SCHEDULES.find(([k]) => k === s) || [, 'Every hour'])[1];
function auMaintOften(s) {
  if (s.frequency === 'custom' && s.custom_every) {
    const unit = s.custom_unit === 'days' ? 'day' : 'hour';
    return `Every ${s.custom_every} ${unit}${s.custom_every === 1 ? '' : 's'}`;
  }
  return {run: 'Every run', day: 'Once a day', week: 'Once a week'}[s.frequency] || 'Every run';
}
const auMaintRunStatus = r => !r ? '' : !r.finished_at ? 'running' : r.ok ? 'ok' : 'failed';

// The list row: a read-only built-in of kind system, Tico's own, on while not paused.
function auMaintRow(m) {
  const s = m.settings, last = m.last_run;
  return {id: AU_MAINT, name: 'Maintenance', kind: 'system', builtin: true, readonly: true, owner_id: AU_MAINT_OWNER,
    enabled: !s.paused, paused: !!s.paused, schedule: auMaintSchedule(s.schedule), trigger: {type: 'schedule'}, steps: [],
    description: (m.steps || []).map(x => x.name).join(' '), can_edit: false, can_pause: false,
    ...(last ? {last_run: {id: last.id, started_at: last.started_at, status: auMaintRunStatus(last)}} : {}),
    next_run: m.next_run_at || null};
}
// The list with Maintenance and System jobs beside it first; an older hub (no steps) leaves it as it is.
function auMaintList(list, m) {
  if (!auMaintOk(m)) return list;
  return [auMaintRow(m), auSysJobsRow(), ...list.filter(a => a.id !== AU_MAINT && a.id !== AU_SYSJOBS)];
}
// A fresh listing (a save, Run now's follow): kept, the list row matched, and what is shown drawn again. A follow's
// refresh leaves Settings as it is while a field there is being edited, so a value not yet saved is not lost.
function auMaintAdopt(state, m, {keepEdits = false} = {}) {
  if (!auMaintOk(m) || AU !== state) return;
  state.maint = m;
  if (state.list) state.list = auMaintList(state.list, m);
  if (m.live_run_id && !state.mtFollow) auMaintFollow(state);
  if (state.id !== AU_MAINT) { auDrawList(); return; }
  auDrawHead();
  const f = document.activeElement;
  if (!(keepEdits && f?.matches?.('input:not([role="switch"]),select') && state.root?.querySelector('.au-pane')?.contains(f))) auDrawPane();
}
async function auMaintLoad(state, opts) {
  const gen = state.mtGen;
  try {
    const m = await get('/v2/maintenance');
    if (AU !== state || state.mtGen !== gen) return;
    state.maintError = auMaintOk(m) ? '' : 'Maintenance needs a newer hub';
    if (auMaintOk(m)) auMaintAdopt(state, m, opts);
  } catch (e) { if (AU === state && state.mtGen === gen) state.maintError = [403, 404].includes(e.status) ? 'Maintenance needs a newer hub' : e.message; }
}

// -------------------------------------------------------------------------------------------- drawing
const auMaintSw = (on, label, attrs, off) =>
  `<input type="checkbox" role="switch" class="people-switch" aria-label="${esc(label)}"${on ? ' checked' : ''}${off ? ' disabled' : ''} ${attrs}>`;

function auMaintHead(hd) {
  const m = AU.maint;
  if (!m) { hd.innerHTML = '<h2 class="au-name">Maintenance</h2>'; return; }
  const s = m.settings, can = !!m.can_change, live = !!m.live_run_id, last = m.last_run;
  const meta = [live ? 'Running…' : last ? 'Last run ' + auRunTime(last.started_at) : 'Not run yet',
    s.paused ? 'paused' : m.next_run_at ? 'next ' + auRunTime(m.next_run_at) : ''].filter(Boolean).join(' · ');
  const seg = ['flow', 'runs'].map(v => `<a role="tab" href="${auHref(AU_MAINT, v)}" aria-selected="${AU.view === v}">${v === 'flow' ? 'Settings' : 'Runs'}</a>`).join('');
  hd.innerHTML = `<h2 class="au-name">Maintenance</h2>
    ${can ? auMaintSw(!s.paused, 'Maintenance on', 'data-mt-pause', AU.mtBusy) : s.paused ? '<span class="pill waiting">Paused</span>' : ''}
    <span class="mt-meta muted" data-mt-meta>${esc(meta)}</span>
    <span class="au-sp"></span>
    <div class="au-hd2">
      <span class="au-sp au-sp-phone"></span>
      <div class="au-seg" role="tablist" aria-label="View">${seg}</div>
      ${can ? `<button class="primary mt-now" type="button" data-mt-now${live || AU.mtStarting ? ' disabled' : ''}>${live || AU.mtStarting ? 'Running…' : 'Run now'}</button>` : ''}
    </div>`;
}

function auMaintPane(pane) {
  const m = AU.maint;
  if (!m) { pane.innerHTML = AU.maintError ? `<p class="err au-pad">${esc(AU.maintError)}</p>` : '<p class="muted au-pad">Loading…</p>'; return; }
  // A redraw keeps the place; Settings and Runs each open at the top.
  const top = AU.mtPaneView === AU.view ? pane.scrollTop : 0;
  AU.mtPaneView = AU.view;
  pane.innerHTML = AU.view === 'runs' ? auMaintRunsHTML() : auMaintSettingsHTML(m);
  pane.scrollTop = top;
}

function auMaintSettingsHTML(m) {
  const s = m.settings, off = !m.can_change || AU.mtBusy, open = AU.mtOpen || new Set();
  const opts = (pairs, cur) => pairs.map(([v, l]) => `<option value="${esc(v)}"${String(v) === String(cur) ? ' selected' : ''}>${esc(l)}</option>`).join('');
  const cap = s.monthly_cap_usd ?? s.monthly_cap_usd_in_effect ?? 0;
  const caps = [...new Set([...AU_MAINT_CAPS, Number(cap) || 0])].sort((x, y) => x - y)
    .map(v => [v, v ? '$' + (Number.isInteger(v) ? v : v.toFixed(2)) + ' a month' : 'No cap']);
  const rows = m.steps.map((st, i) => {
    const on = st.enabled, isOpen = open.has(st.key), always = st.key === 'collect';
    const pills = (AU_MAINT_BETA.has(st.key) ? '<span class="pill beta">Beta</span>' : '')
      + (st.key === 'suggest' && st.consent === false ? `<a class="pill waiting" href="${AU_MAINT_PRIVACY}" data-mt-consent>Needs Privacy consent</a>` : '');
    return `<li class="mt-step${on ? '' : ' off'}${isOpen ? ' open' : ''}" data-mt-step="${esc(st.key)}">
      <div class="mt-row">
        <span class="mt-n">${i + 1}</span>
        <div class="mt-main" role="button" tabindex="0" aria-expanded="${isOpen}" data-mt-open="${esc(st.key)}">
          <div class="mt-name"><b>${esc(st.name)}</b>${pills}${auIcon('expand_more', 'mt-chev')}</div>
          <div class="mt-what muted">${esc(AU_MAINT_WHAT[st.key] || '')}<span class="mt-when-p"> · ${esc(on ? auMaintOften(st) : 'Off')}</span></div>
        </div>
        <span class="mt-when muted">${esc(on ? auMaintOften(st) : 'Off')}</span>
        ${always ? '<span class="mt-always muted">Always</span>' : auMaintSw(on, st.name + ' on', `data-mt-on="${esc(st.key)}"`, off)}
      </div>
      ${isOpen ? auMaintDetailHTML(st, off, opts) : ''}
    </li>`;
  }).join('');
  return `<div class="mt">
    <div class="mt-set"><label class="mt-lb" for="mt-schedule">Run</label>
      <select id="mt-schedule" data-mt-schedule${off ? ' disabled' : ''}>${opts(AU_MAINT_SCHEDULES, s.schedule)}</select></div>
    <ol class="mt-steps">${rows}</ol>
    <div class="mt-set"><label class="mt-lb" for="mt-cap">Monthly cap</label>
      <select id="mt-cap" data-mt-cap${off ? ' disabled' : ''}>${opts(caps, Number(cap) || 0)}</select>
      <span class="mt-hint muted">Learn and people’s update drafts</span></div>
  </div>`;
}

function auMaintDetailHTML(st, off, opts) {
  const k = esc(st.key), custom = st.frequency === 'custom';
  const often = st.fixed ? '<span>Every run</span>'
    : `<select data-mt-often="${k}" aria-label="${esc(st.name)} how often"${off ? ' disabled' : ''}>${opts(AU_MAINT_OFTEN, st.frequency)}</select>
      ${custom ? `<span class="mt-custom">Every <input type="number" min="1" max="999" inputmode="numeric" data-mt-every="${k}" aria-label="${esc(st.name)} every" value="${esc(st.custom_every ?? 2)}"${off ? ' disabled' : ''}>
        <select data-mt-unit="${k}" aria-label="${esc(st.name)} unit"${off ? ' disabled' : ''}>${opts([['hours', 'hours'], ['days', 'days']], st.custom_unit || 'hours')}</select></span>` : ''}`;
  const bots = st.max_bots != null || AU_MAINT_BOTS[st.key]
    ? `<div class="mt-kv"><span class="mt-lb">${esc(AU_MAINT_BOTS[st.key] || 'Most bots per run')}</span>
      <input type="number" min="1" max="20" inputmode="numeric" data-mt-bots="${k}" aria-label="${esc(AU_MAINT_BOTS[st.key] || 'Most bots per run')}" value="${esc(st.max_bots ?? '')}"${off ? ' disabled' : ''}></div>` : '';
  const last = st.last_ran_at ? `<div class="mt-kv"><span class="mt-lb">Last ran</span><span title="${esc(fmt(st.last_ran_at))}">${esc(auRunTime(st.last_ran_at))}</span></div>` : '';
  return `<div class="mt-detail">
    <div class="mt-kv"><span class="mt-lb">How often</span><span class="mt-often">${often}</span></div>
    ${bots}${last}${st.stats ? auMaintStatsHTML(st.stats) : ''}
  </div>`;
}

// Learn's beta numbers (backend/learnings.py beta_stats): what it read, sent, kept and dropped.
function auMaintStatsHTML(x) {
  const sent = Object.values(x.items_sent || {}).reduce((t, n) => t + (Number(n) || 0), 0);
  const cells = [['Runs', x.runs], ['Read', x.items_read], ['Sent', sent], ['Saved', x.saved], ['Dropped', x.dropped]]
    .map(([l, v]) => `<div class="mt-stat"><b>${esc(Number(v || 0).toLocaleString())}</b><span class="muted">${l}</span></div>`).join('');
  return `<div class="mt-kv mt-beta" data-mt-stats><span class="mt-lb">Last ${esc(x.days || 7)} days</span>
    <div class="mt-stats">${cells}<div class="mt-stat"><b>${esc(auMoney(x.cost_usd))}</b><span class="muted">Cost</span></div></div></div>`;
}

const AU_MAINT_STEP_WORDS = {ran: 'ran', skipped: 'skipped', failed: 'failed', pending: 'pending', off: 'off'};
function auMaintRunsHTML() {
  const r = AU.mtRuns;
  if (!r || (r.loading && !r.rows.length)) return '<p class="muted au-pad">Loading…</p>';
  if (r.error && !r.rows.length) return `<p class="err au-pad">${esc(r.error)}</p>`;
  if (!r.rows.length) return '<p class="muted au-pad">No runs yet</p>';
  const name = k => auMaintStep(k)?.name || auCap(String(k || '').replace(/_/g, ' '));
  const counts = x => [[x.checked, 'checked'], [x.found, 'found'], [x.woke, 'woken']].filter(([n]) => n != null).map(([n, l]) => `${n} ${l}`).join(' · ');
  return `<table class="au-runs mt-runs"><thead><tr><th>Time</th><th><span class="au-vh">Result</span></th><th>Outcome</th><th class="mt-by">Started by</th><th class="au-n">Cost</th></tr></thead><tbody>
    ${r.rows.map(run => {
      const open = r.open.has(run.id), st = auMaintRunStatus(run), dot = st === 'ok' ? 'ok' : st === 'failed' ? 'fail' : 'wait';
      const by = run.trigger === 'run_now' ? 'Run now' + (run.step ? ' · ' + name(run.step) : '') : 'Schedule';
      return `<tr class="au-rr${open ? ' open' : ''}" role="button" tabindex="0" aria-expanded="${open}" data-mt-run="${esc(run.id)}">
        <td class="au-rt"><span class="nav-icon" aria-hidden="true">${open ? 'expand_more' : 'chevron_right'}</span>${esc(auRunTime(run.started_at))}</td>
        <td><span class="au-dot ${dot}" role="img" aria-label="${esc(AU_RUN_STATUS[st] || st)}"></span></td>
        <td class="au-ro">${esc(st === 'running' ? 'Running' : run.result || AU_RUN_STATUS[st] || '')}</td>
        <td class="mt-by muted">${esc(by)}</td><td class="au-n">${esc(auMoney(run.cost_usd))}</td></tr>`
        + (open ? `<tr class="au-rx"><td colspan="5">${(run.steps || []).map(x => `<div class="au-sl" data-mt-line="${esc(x.step)}">
            <span class="au-tag${x.status === 'failed' ? ' mt-bad' : x.status === 'ran' ? ' acc' : ''}">${esc(AU_MAINT_STEP_WORDS[x.status] || x.status)}</span>
            <span><b>${esc(name(x.step))}</b>${x.reason ? ' · ' + esc(x.reason) : ''}${counts(x) ? ` <span class="muted">· ${esc(counts(x))}</span>` : ''}</span>
            ${Number(x.cost_usd) ? `<span class="au-n muted">${esc(auMoney(x.cost_usd))}</span>` : ''}</div>`).join('') || '<span class="muted">No steps</span>'}</td></tr>` : '');
    }).join('')}</tbody></table>
    ${r.next ? `<div class="au-more"><button class="ghost" type="button" data-mt-more${r.loading ? ' disabled' : ''}>${r.loading ? 'Loading…' : 'More'}</button></div>` : ''}
    ${r.error ? `<p class="err au-pad">${esc(r.error)}</p>` : ''}`;
}

async function auMaintRunsLoad(state, more = false) {
  const r = state.mtRuns ||= {rows: [], next: null, open: new Set(), loading: false, error: ''};
  // A run tapped on the overview's Runs tab opens here (automations-overview.js sets mtOpenRun).
  if (state.mtOpenRun) { r.open.add(state.mtOpenRun); state.mtOpenRun = null; }
  if (r.loading || (more && !r.next)) return;
  r.loading = true; r.error = '';
  if (AU === state && state.id === AU_MAINT && state.view === 'runs') auDrawPane();
  try {
    const page = await get(`/v2/maintenance/runs?limit=${AU_MAINT_RUNS_PAGE}${more ? '&before=' + encodeURIComponent(r.next) : ''}`);
    if (state.mtRuns !== r) return;
    const rows = page.runs || [];
    if (!more && r.rows.length) {
      const fresh = new Map(rows.map(x => [x.id, x]));
      r.rows = [...rows.filter(x => !r.rows.some(y => y.id === x.id)), ...r.rows.map(x => fresh.get(x.id) || x)];
    } else {
      r.rows = more ? r.rows.concat(rows) : rows;
      r.next = page.next || null;
    }
  } catch (e) { r.error = e.message; }
  r.loading = false;
  // A run tapped on the overview that is older than the page loaded: the pages before it are read too, a few at most.
  if (!r.error && r.next && [...r.open].some(id => !r.rows.some(x => x.id === id)) && (r.seek = (r.seek || 0) + 1) <= 10) {
    void auMaintRunsLoad(state, true);
    return;
  }
  r.seek = 0;
  if (AU === state && state.id === AU_MAINT && state.view === 'runs') auDrawPane();
}

// -------------------------------------------------------------------------------------------- writes
// One PATCH; the answer is the whole listing. A refusal says why and draws the saved values back.
async function auMaintSave(body) {
  const state = AU;
  if (state.mtBusy) return;
  state.mtBusy = true; state.mtGen++;
  auDrawHead(); auDrawPane();
  try {
    const m = await patch('/v2/maintenance', body);
    state.mtBusy = false; state.mtGen++;   // a read that started during the save holds the old values
    auMaintAdopt(state, m);
  } catch (e) {
    state.mtBusy = false; state.mtGen++;
    toast(e.message, true);
    if (AU === state && state.id === AU_MAINT) { auDrawHead(); auDrawPane(); }
  }
}
const auMaintStepSave = (key, change) => auMaintSave({steps: [{key, ...change}]});

// Run now: the same claim and due rules as the schedule (a live run is returned, not a second one), then the screen
// follows it until it ends.
async function auMaintRunNow() {
  const state = AU;
  if (state.mtStarting || state.maint?.live_run_id) return;
  state.mtStarting = true; state.mtGen++; auDrawHead();
  try {
    const out = await post('/v2/maintenance/run', {});
    toast(out.started === false ? 'Already running' : 'Running');
    if (AU !== state) return;
    state.mtStarting = false;
    if (state.maint && out.run && !out.run.finished_at) state.maint = {...state.maint, live_run_id: out.run.id};
    if (state.id === AU_MAINT) { auDrawHead(); if (state.view === 'runs') void auMaintRunsLoad(state); }
    auMaintFollow(state);
  } catch (e) { toast(e.message, true); if (AU === state) { state.mtStarting = false; auDrawHead(); } }
}
// Every 3 s for the first two minutes, then every 15 s for as long as the run is live.
function auMaintFollow(state, tries = 0) {
  clearTimeout(state.mtFollow);
  state.mtFollow = null;
  if (AU !== state) return;
  state.mtFollow = setTimeout(async () => {
    if (AU !== state) return;
    await auMaintLoad(state, {keepEdits: true});
    state.mtFollow = null;
    if (AU !== state) return;
    if (state.id === AU_MAINT && state.view === 'runs') void auMaintRunsLoad(state);
    if (state.maint?.live_run_id) auMaintFollow(state, tries + 1);
  }, tries < 40 ? 3000 : 15000);
}

// -------------------------------------------------------------------------------------------- events
function auMaintClick(t) {
  if (AU.id !== AU_MAINT) return false;
  if (t.closest('a[href]')) return false;   // Needs Privacy consent and the tabs are links
  let m;
  if ((m = t.closest('[data-mt-now]'))) { if (!m.disabled) void auMaintRunNow(); return true; }
  if ((m = t.closest('[data-mt-open]'))) {
    const k = m.dataset.mtOpen, open = AU.mtOpen ||= new Set();
    if (open.has(k)) open.delete(k); else open.add(k);
    auDrawPane();
    return true;
  }
  if ((m = t.closest('tr[data-mt-run]'))) {
    const r = AU.mtRuns, id = m.dataset.mtRun;
    if (r) { if (r.open.has(id)) r.open.delete(id); else r.open.add(id); auDrawPane(); }
    return true;
  }
  if ((m = t.closest('[data-mt-more]'))) { void auMaintRunsLoad(AU, true); return true; }
  return false;
}
function auMaintChange(e) {
  const t = e.target;
  if (!AU || AU.id !== AU_MAINT || !t.closest?.('.au-hd,.au-pane')) return;
  if (AU.mtBusy) { auDrawHead(); auDrawPane(); return; }
  const d = t.dataset, step = k => auMaintStep(k) || {};
  if ('mtPause' in d) {
    if (!t.checked && !confirm('Pause Maintenance? No step runs until it is back on.')) { t.checked = true; return; }
    void auMaintSave({paused: !t.checked});
  } else if ('mtSchedule' in d) void auMaintSave({schedule: t.value});
  else if ('mtCap' in d) void auMaintSave({monthly_cap_usd: Number(t.value)});
  else if (d.mtOn) void auMaintStepSave(d.mtOn, {enabled: t.checked});
  else if (d.mtOften) {
    const s = step(d.mtOften);
    void auMaintStepSave(d.mtOften, t.value === 'custom'
      ? {frequency: 'custom', custom_every: s.custom_every || 2, custom_unit: s.custom_unit || 'hours'} : {frequency: t.value});
  } else if (d.mtEvery || d.mtUnit) {
    const k = d.mtEvery || d.mtUnit, row = t.closest('.mt-detail');
    const every = Math.round(Number(row.querySelector('[data-mt-every]').value)), unit = row.querySelector('[data-mt-unit]').value;
    if (!(every >= 1 && every <= 999)) { toast('Every 1 to 999', true); auDrawPane(); return; }
    void auMaintStepSave(k, {frequency: 'custom', custom_every: every, custom_unit: unit});
  } else if (d.mtBots) {
    const n = Math.round(Number(t.value));
    if (!(n >= 1 && n <= 20)) { toast('1 to 20 bots', true); auDrawPane(); return; }
    void auMaintStepSave(d.mtBots, {max_bots: n});
  }
}

// -------------------------------------------------------------------------------------------- System jobs
// The other built-in row: every job Tico runs on its own and the task rules (GET /api/v2/system-routines), with their
// switches, schedules, Run now and New task rule (ui/app/settings-recurring.js). Anyone signed in reads it; the server's
// `can_change` decides each row's controls.
function auSysJobsRow() {
  return {id: AU_SYSJOBS, name: 'System jobs', kind: 'system', builtin: true, readonly: true, owner_id: AU_MAINT_OWNER,
    enabled: true, trigger: {type: 'system'}, steps: [], description: 'What Tico runs on its own', can_edit: false, can_pause: false};
}
function auSysJobsHead(hd) {
  hd.innerHTML = `<h2 class="au-name">System jobs</h2>${AU_BUILTIN_PILL}`;
}
// A redraw of the screen keeps the list (and a half-typed time in it); renderSystemRoutines reads it again at most once a minute.
function auSysJobsPane(pane) {
  if (!pane.querySelector('#set-system-routines')) pane.innerHTML = `<div class="sysr-au" id="set-system-routines">${skelRows(3, {card: true, dot: false})}</div>`;
  void renderSystemRoutines();
}
