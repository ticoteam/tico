/* ui/app/settings-history.js — Settings > History: the Log, Runs, and the owner's Changes
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

function settingsValue(field, value) {
  if (field === 'owners') return value === null ? 'registry defaults' : (value || []).map(settingsPersonName).join(', ') || 'nobody';
  if (field === 'access') return accessSummary(value);
  if (field === 'model') return settingsChoiceLabel(value?.harness || value?.runtime, value?.model, value?.effort);
  if (field === 'fallback') return value ? settingsChoiceLabel(value.harness, value.model, value.reasoning_effort || value.effort) : 'None (fail)';
  if (field === 'profile') return profileHistoryWords(value);
  if (field === 'wake_preset') return SETTINGS_WAKE_PRESETS.find(([preset]) => preset === value)?.[1] || String(value);
  if (field === 'keep_tasks_going') return value == null ? 'No limit' : value === 0 ? 'Off' : `${value} tasks`;
  if (field === 'turns_at_once') return value == null ? 'No limit' : `${value} ${value === 1 ? 'turn' : 'turns'}`;
  if (field === 'task_model') return value?.model ? settingsChoiceLabel(value.harness || value.runtime, value.model, value.reasoning_effort) : 'Same as bot';
  if (field === 'identity') return [value?.display_name, value?.description].filter(Boolean).join(' · ') || 'blank';
  if (field === 'manages_reports') return value ? 'On' : 'Off';
  if (field === 'compact_at') return SETTINGS_COMPACT_AT.find(([size]) => size === value)?.[1] || String(value);
  if (field === 'placement') return value?.label ? `${value.label} · ${settingsPersonName(value.operator)}` : 'not assigned';
  if (field === 'definition') return [value?.display_name, value?.status,
    value?.reports_to ? `reports to ${settingsBotName(value.reports_to)}` : 'top level'].filter(Boolean).join(' · ');
  return JSON.stringify(value);
}
function settingsCheckpointDetails(transition) {
  const prepared = (transition?.checkpoints || []).filter(row => row.checkpoint);
  if (!prepared.length) return '';
  return `<details><summary>${prepared.length} saved conversation checkpoint${prepared.length === 1 ? '' : 's'}</summary>${prepared.map(row => {
    const cp = row.checkpoint;
    return `<div class="transition-progress"><strong>${esc(cp.current_objective || 'No active objective')}</strong><p>${esc(cp.next_recommended_action || 'No next action recorded')}</p>
      ${cp.open_decisions?.length ? `<p><strong>Open decisions:</strong> ${esc(cp.open_decisions.join(' · '))}</p>` : ''}</div>`;
  }).join('')}</details>`;
}
// A bot's setup changing, in words: only what changed ("Setting up → active", "Now reports to Ana").
const historyStatus = v => { const w = statusWord(v); return w.charAt(0).toUpperCase() + w.slice(1); };
function settingsDefinitionChange(before, after) {
  const b = before || {}, a = after || {}, out = [];
  if (b.display_name !== a.display_name) out.push(b.display_name ? `Renamed from ${b.display_name} to ${a.display_name}` : `Named ${a.display_name}`);
  if (b.status !== a.status) out.push(`${historyStatus(b.status) || 'New'} → ${historyStatus(a.status)}`);
  if ((b.reports_to || '') !== (a.reports_to || '')) out.push(a.reports_to ? `Now reports to ${settingsBotName(a.reports_to)}` : 'Now at the top level');
  return out.join(' · ') || 'No visible change';
}
// ----------------------------------------------------------------- Log
// The audit log (GET /api/v2/history, backend/history_log.py): what every teammate did, asked and was blocked from,
// newest first. The reader sees only rows their own SQL would show them. The view and the filters ride in the address
// (#/settings/history?kind=decision&bot=ops), so a filtered view can be shared.
const HISTORY_KINDS = [['run', 'Runs'], ['task', 'Tasks'], ['approval.asked', 'Approvals asked'],
  ['approval.granted', 'Approvals granted'], ['approval.denied', 'Approvals denied'], ['outbound.blocked', 'Outbound blocked'],
  ['credential.revealed', 'Credential reveals'], ['permission.changed', 'Permission changes'], ['decision', 'Decisions']];
const HISTORY_KIND_LABEL = {run: 'Run', task: 'Task', 'approval.asked': 'Approval asked', 'approval.granted': 'Approval granted',
  'approval.denied': 'Approval denied', 'outbound.blocked': 'Outbound blocked', 'credential.revealed': 'Credential revealed',
  'permission.changed': 'Permission change', decision: 'Decision'};
let SETTINGS_HISTORY_VIEW = 'log';
let SETTINGS_LOG = {scope: '', kind: ''};
// The router hands over the query of #/settings/history?..; settingsAddress writes it back with settingsHistoryQuery.
function settingsHistoryAddress(query) {
  const q = new URLSearchParams(query || '');
  SETTINGS_HISTORY_VIEW = ['runs', 'changes'].includes(q.get('view')) ? q.get('view') : 'log';
  const scope = ['bot', 'person', 'team'].map(k => q.get(k) ? `${k === 'person' ? 'human' : k}:${q.get(k)}` : '').find(Boolean) || '';
  const kind = HISTORY_KINDS.some(([k]) => k === q.get('kind')) ? q.get('kind') : '';
  SETTINGS_LOG = {scope, kind};
}
// kind, and bot, person or team: the filters, as the API takes them.
function settingsLogFilters() {
  const q = new URLSearchParams();
  if (SETTINGS_LOG.kind) q.set('kind', SETTINGS_LOG.kind);
  if (SETTINGS_LOG.scope) { const [kind, id] = SETTINGS_LOG.scope.split(/:(.*)/); q.set(kind === 'human' ? 'person' : kind, id); }
  return q;
}
function settingsHistoryQuery() {
  const q = SETTINGS_HISTORY_VIEW === 'log' ? settingsLogFilters() : new URLSearchParams();
  if (SETTINGS_HISTORY_VIEW !== 'log') q.set('view', SETTINGS_HISTORY_VIEW);
  return q.toString() ? '?' + q : '';
}
function settingsLogWho(actor, names) {
  const slug = actorSlug(actor);
  return slug ? empChip(slug) : `<span class="chip">${esc(names[actor] || actorLabel(actor) || 'Tico')}</span>`;
}
function settingsLogRowsHTML(items, names) {
  return items.map(item => {
    const about = item.about && item.about !== item.actor ? `<span class="log-about">${esc(names[item.about] || actorLabel(item.about))}</span>` : '';
    const link = item.link || '', away = link.startsWith('/');
    const subject = link ? `<a href="${esc(link)}"${away ? ' target="_blank" rel="noopener"' : ''}>${esc(item.subject || 'Open')}</a>` : esc(item.subject);
    return `<div class="log-row" data-log-id="${esc(item.id)}">
      <span class="log-when tnum">${agoSpan(item.at)}</span>
      <span class="log-who">${settingsLogWho(item.actor, names)}</span>
      <span class="log-kind">${esc(HISTORY_KIND_LABEL[item.kind] || item.kind)}</span>
      <span class="log-subject">${about}<span>${subject}</span></span>
      <span class="log-result" title="${esc(item.result)}">${esc(item.result)}</span></div>`;
  }).join('');
}
async function renderSettingsLog() {
  const el = $('#set-log'); if (!el) return;
  const filters = settingsLogFilters();
  el.innerHTML = `<div class="log-filters">
      <select id="log-who" aria-label="Who">${replayScopeOptions(SETTINGS_LOG.scope)}</select>
      <select id="log-kind" aria-label="Kind"><option value="">Everything</option>${HISTORY_KINDS.map(([k, label]) =>
        `<option value="${k}"${k === SETTINGS_LOG.kind ? ' selected' : ''}>${label}</option>`).join('')}</select>
      ${settingsIsAdmin() ? `<button class="linkish log-csv" id="log-csv" type="button">Export CSV</button>` : ''}
    </div><div id="log-rows">${skelRows(3, {card: true, dot: false})}</div>`;
  const change = () => {
    SETTINGS_LOG = {scope: $('#log-who').value, kind: $('#log-kind').value};
    settingsAddress(); void renderSettingsLog();
  };
  $('#log-who').onchange = change; $('#log-kind').onchange = change;
  const csv = $('#log-csv'); if (csv) csv.onclick = () => settingsLogCsv(csv, filters);
  const rows = $('#log-rows'), drawn = el._logDrawn = {};
  const draw = async (before, shown) => {
    const q = new URLSearchParams(filters); if (before) q.set('before', before);
    const page = await get(`/v2/history${q.toString() ? '?' + q : ''}`);
    if (el._logDrawn !== drawn) return;   // a newer filter drew over this one
    const items = [...shown.items, ...page.items], names = {...shown.names, ...page.names};
    rows.innerHTML = items.length ? `<div class="log-list"><div class="log-row log-head"><span>When</span><span>Who</span><span>Kind</span><span>What</span><span>Result</span></div>${settingsLogRowsHTML(items, names)}</div>`
      + (page.next ? '<button class="ghost" type="button" data-log-older>Show older</button>' : '')
      : '<div class="empty">Nothing here yet.</div>';
    const older = rows.querySelector('[data-log-older]');
    if (older) older.onclick = async () => {
      older.disabled = true;
      try { await draw(page.next, {items, names}); } catch (error) { older.disabled = false; toast(error.message, true); }
    };
  };
  try { await draw('', {items: [], names: {}}); } catch (e) { if (el._logDrawn === drawn) rows.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}
// The Log's filters as one CSV. A page holds 5,000 rows and X-Next says where the next one starts, so every page is
// fetched in turn and the file is saved whole; the header row is kept once.
async function settingsLogCsv(button, filters) {
  button.disabled = true;
  try {
    let text = '', before = '';
    do {
      const q = new URLSearchParams(filters); if (before) q.set('before', before);
      const r = await fetch(`${API}/v2/history.csv?${q}`, {cache: 'no-store'}), body = await r.text();
      if (!r.ok) { let j; try { j = JSON.parse(body); } catch { j = {}; } throw apiFailure(j, r); }
      text = text ? text + body.slice(body.indexOf('\n') + 1) : body;
      before = r.headers.get('X-Next') || '';
    } while (before);
    const url = URL.createObjectURL(new Blob([text], {type: 'text/csv'})), link = document.createElement('a');
    link.href = url; link.download = 'tico-history.csv';
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}
// ----------------------------------------------------------------- Runs
// Every run this person may see (GET /runs), one line each: the bot's own Runs card keeps runsTable (ui/app/issues.js).
// Changes is the owner's alone: anyone else sees the Log or Runs, even on a page drawn before they lost the role.
function settingsHistoryShow(view = SETTINGS_HISTORY_VIEW) {
  const log = $('#set-log'), runs = $('#set-runs'), changes = $('#set-history'), owner = S.me?.role === 'owner'; if (!runs) return;
  SETTINGS_HISTORY_VIEW = view === 'runs' ? 'runs' : changes && owner && view === 'changes' ? 'changes' : 'log';
  const own = $('[data-history-view=changes]'); if (own) own.hidden = !owner;
  if (log) log.hidden = SETTINGS_HISTORY_VIEW !== 'log';
  runs.hidden = SETTINGS_HISTORY_VIEW !== 'runs';
  if (changes) changes.hidden = SETTINGS_HISTORY_VIEW !== 'changes';
  document.querySelectorAll('[data-history-view]').forEach(button => button.setAttribute('aria-checked', String(button.dataset.historyView === SETTINGS_HISTORY_VIEW)));
  settingsAddress();
  if (SETTINGS_HISTORY_VIEW === 'log') void renderSettingsLog();
  if (SETTINGS_HISTORY_VIEW === 'runs') void renderSettingsRuns();
}
// "18K out", so a run with no price fits its column on a phone; the exact count is in the CSV and the log.
const settingsRunTokens = new Intl.NumberFormat(undefined, {notation: 'compact', maximumFractionDigits: 1});
const settingsRunTook = s => s == null ? '' : s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
function settingsRunsHTML(list) {
  if (!list.length) return `<div class="empty">No runs yet.</div>`;
  return `<div class="runs-list"><div class="run-row run-head"><span>Bot</span><span>Result</span><span>Model</span><span>Took</span><span>Cost</span><span>Finished</span><span></span></div>
    ${list.map(r => {
      const attempt = r.record_kind === 'attempt', ok = !attempt && r.exit === 0;
      // An attempt reads its outcome alone; "attempt" and its id are in the pill's tooltip.
      const result = ok ? 'Done' : r.outcome || `exit ${r.exit}`;
      const why = attempt ? r.failure_reason || '' : '';
      const cost = r.cost_usd != null ? '$' + r.cost_usd.toFixed(2) : r.output_tokens != null ? `${settingsRunTokens.format(r.output_tokens)} out` : '';
      return `<div class="run-row">
        <span class="run-bot">${empChip(r.employee)}</span>
        <span class="run-result"><span class="pill ${ok ? 'ok' : 'fail'}"${attempt ? ` title="${esc(`Attempt ${r.attempt_id || ''}`.trim())}"` : ''}>${esc(result)}</span>${ok ? '' : runsReportHTML(r, true)}</span>
        <span class="run-model">${why ? `<span class="run-why" title="${esc(why)}">${esc(why)}</span>` : ''}<span class="mono" title="The model that did the work; a fallback changes it">${esc(ranOn(r.ran_on))}</span>${r.fallback
          ? `<span class="pill waiting" title="Ran on the fallback after the primary was unavailable">${esc(r.fallback)}</span>` : ''}${r.resumed ? '<span class="run-tag" title="Picked up where it left off">resumed</span>' : ''}</span>
        <span class="run-took tnum">${settingsRunTook(r.duration_s)}</span>
        <span class="run-cost tnum">${cost}</span>
        <span class="run-when tnum" title="${esc(fmt(r.finished))}">${esc(ago(r.finished))}</span>
        <a class="run-log" href="${API}/runs/${encodeURIComponent(r.run)}/log" target="_blank" rel="noopener">Log</a></div>`;
    }).join('')}</div>`;
}
async function renderSettingsRuns() {
  const el = $('#set-runs'); if (!el) return;
  try { el.innerHTML = settingsRunsHTML(await get('/runs')); } catch (e) { el.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}

// ----------------------------------------------------------------- Changes
// A page of changes older than `before` (a page's next_before): the owner's list, or one bot's card with `bot`.
function settingsHistoryOlder(before, bot = '') {
  const scope = bot ? `bot=${encodeURIComponent(bot)}&` : '';
  return get(`/v2/settings/history?${scope}before=${encodeURIComponent(before)}`);
}
// "Show older" fetches the page after the last one drawn, with `older`, and draws all the rows together. A page still
// on its way is dropped when a later draw replaces this one, as a fresh first page after an undo does.
function renderSettingsHistory(el = $('#set-history'), history = SETTINGS_DATA.history, older = settingsHistoryOlder) {
  if (!el) return;
  const changes = history?.changes || [], transitions = history?.transitions || [];
  const pending = transitions.filter(t => ['preparing','blocked','failed','prepared'].includes(t.state));
  const transitionById = new Map(transitions.map(t => [t.id, t]));
  const pendingRows = pending.map(t => `<div class="settings-history-row"><span>${avatar(t.bot, 24, stateOf(t.bot))}</span><div><strong>${esc(settingsBotName(t.bot))} · ${t.kind === 'model' ? 'model change' : 'computer move'}</strong><p>${esc(t.state === 'preparing' ? `Checkpointing ${t.progress.prepared} of ${t.progress.total} conversations` : t.error || t.state)}</p></div><div class="history-action"><span class="pill ${t.state === 'failed' ? 'fail' : 'waiting'}">${esc(t.state)}</span><button class="ghost" type="button" data-transition-review="${esc(t.id)}">Review</button></div></div>`).join('');
  const rows = changes.map(change => {
    const e = S.emps.find(row => row.name === change.bot), transition = transitionById.get(change.transition_id);
    const field = {owners:'who it works for',access:'access',model:'model',placement:'computer',fallback:'fallback',
      compact_at:'compact at',wake_preset:'wakes',profile:'profile',keep_tasks_going:'keep tasks going',
      turns_at_once:'Turns at once',definition:'setup',task_model:'task model',identity:'name and description',
      manages_reports:'manages its reports'}[change.field] || change.field;
    // A change too big for a page comes without its values (backend/settings_admin.py `truncated`).
    const what = change.truncated ? 'Too big to show here.'
      : change.field === 'definition' ? settingsDefinitionChange(change.before, change.after)
      : `${settingsValue(change.field, change.before)} → ${settingsValue(change.field, change.after)}`;
    // A manager bot's change says so: it acted for the bot, as its manager (backend/report_managers.py).
    const by = change.via === 'report_manager' ? `${actorLabel(change.actor)}, as its manager` : actorLabel(change.actor);
    return `<div class="settings-history-row"><span>${avatar(change.bot, 24, stateOf(change.bot))}</span><div><strong>${esc(settingsBotName(change.bot))} · ${esc(field)}</strong>
      <p>${esc(what)}</p><p>${esc(by)} · ${esc(fmt(change.created))}${change.undone_at ? ` · undone ${esc(ago(change.undone_at))}` : ''}</p></div>
      <div class="history-action">${change.can_undo && e ? `<button class="ghost" type="button" data-settings-undo="${esc(change.id)}" data-bot="${esc(change.bot)}" data-revision="${e.revision}">Undo</button>` : ''}</div>
      ${settingsCheckpointDetails(transition)}</div>`;
  }).join('');
  const more = history?.next_before
    ? '<button class="ghost" type="button" data-settings-older>Show older</button>' : '';
  // The page leaves out the oldest transitions when they would not fit (backend/settings_admin.py history).
  const late = history?.transitions_truncated
    ? '<p class="empty">Some older in-progress changes are not listed here.</p>' : '';
  el.innerHTML = `${pendingRows || late ? `<h3>In progress</h3>${pendingRows}${late}` : ''}`
    + `${rows || '<div class="empty">No settings changes yet.</div>'}${more}`;
  const drawn = el._settingsDrawn = {};
  el.onclick = async event => {
    const review = event.target.closest('[data-transition-review]');
    if (review) { settingsWatchTransition(review.dataset.transitionReview); return; }
    const showOlder = event.target.closest('[data-settings-older]');
    if (showOlder) {
      showOlder.disabled = true;
      try {
        const next = await older(history.next_before);
        if (el._settingsDrawn === drawn) renderSettingsHistory(el, {changes: [...changes, ...(next.changes || [])],
          transitions, transitions_truncated: history.transitions_truncated, next_before: next.next_before}, older);
      } catch (error) { showOlder.disabled = false; toast(error.message, true); }
      return;
    }
    const undo = event.target.closest('[data-settings-undo]'); if (!undo) return;
    undo.disabled = true;
    try {
      const result = await post(`/v2/settings/history/${encodeURIComponent(undo.dataset.settingsUndo)}/undo`, {expected_revision:Number(undo.dataset.revision)});
      if (result.id) settingsWatchTransition(result.id); else { await loadSettings(); if (el.id === 'bot-settings-history' && BOT) await botSettingsHistoryLoad(BOT.slug); toast('Settings change undone'); }
    } catch (error) {undo.disabled = false; toast(error.message, true);}
  };
}
