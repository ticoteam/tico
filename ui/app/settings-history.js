/* ui/app/settings-history.js — Settings > History: Runs, and the owner's Changes
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
  if (field === 'lifecycle') return value?.state ? `${value.state[0].toUpperCase()}${value.state.slice(1)}` : 'Not made';
  if (field === 'compact_at') return SETTINGS_COMPACT_AT.find(([size]) => size === value)?.[1] || String(value);
  if (field === 'placement') return value?.label ? `${value.label} · ${settingsPersonName(value.owner_id)}` : 'not assigned';
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
  if (b.slug && a.slug && b.slug !== a.slug) out.push(`Slug ${b.slug} → ${a.slug}`);
  if (b.status !== a.status) out.push(`${historyStatus(b.status) || 'New'} → ${historyStatus(a.status)}`);
  if ((b.reports_to || '') !== (a.reports_to || '')) out.push(a.reports_to ? `Now reports to ${settingsBotName(a.reports_to)}` : 'Now at the top level');
  return out.join(' · ') || 'No visible change';
}
// ----------------------------------------------------------------- Runs
// Every run this person may see (GET /runs), one line each: the bot's own Runs card keeps runsTable (ui/app/issues.js).
let SETTINGS_HISTORY_VIEW = 'runs';
// Changes is the owner's alone: anyone else always sees Runs, even on a page drawn before they lost the role.
function settingsHistoryShow(view = SETTINGS_HISTORY_VIEW) {
  const runs = $('#set-runs'), changes = $('#set-history'), owner = S.me?.role === 'owner'; if (!runs) return;
  SETTINGS_HISTORY_VIEW = changes && owner && view === 'changes' ? 'changes' : 'runs';
  const switcher = $('#settings-history-view'); if (switcher) switcher.hidden = !owner;
  runs.hidden = SETTINGS_HISTORY_VIEW !== 'runs';
  if (changes) changes.hidden = SETTINGS_HISTORY_VIEW !== 'changes';
  document.querySelectorAll('[data-history-view]').forEach(button => button.setAttribute('aria-checked', String(button.dataset.historyView === SETTINGS_HISTORY_VIEW)));
  if (SETTINGS_HISTORY_VIEW === 'runs') void renderSettingsRuns();
}
// "18K out", so a run with no price fits its column on a phone; the exact count is in the CSV and the log.
const settingsRunTokens = new Intl.NumberFormat(undefined, {notation: 'compact', maximumFractionDigits: 1});
const settingsRunTook = s => s == null ? '' : s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
function settingsRunsHTML(list) {
  if (!list.length) return `<div class="empty">No runs yet.</div>`;
  return `<div class="runs-list"><div class="run-row run-head"><span>Bot</span><span>Result</span><span>Model</span><span>Took</span><span>Cost</span><span>Finished</span><span></span></div>
    ${list.map(r => {
      const unfinished = runUnfinished(r), ok = !unfinished && r.exit === 0;
      // A failed or expired run reads its outcome alone; its id is in the pill's tooltip.
      const result = ok ? 'Done' : r.outcome || `exit ${r.exit}`;
      const why = unfinished ? r.failure_reason || '' : '';
      const cost = r.cost_usd != null ? '$' + r.cost_usd.toFixed(2) : r.output_tokens != null ? `${settingsRunTokens.format(r.output_tokens)} out` : '';
      return `<div class="run-row">
        <span class="run-bot">${empChip(r.employee)}</span>
        <span class="run-result"><span class="pill ${ok ? 'ok' : 'fail'}"${unfinished ? ` title="${esc(`Run ${r.id || ''}`.trim())}"` : ''}>${esc(result)}</span>${ok ? '' : runsReportHTML(r, true)}</span>
        <span class="run-model">${why ? `<span class="run-why" title="${esc(why)}">${esc(why)}</span>` : ''}<span class="mono" title="The model that did the work; a fallback changes it">${esc(ranOn(r.ran_on))}</span>${r.fallback
          ? `<span class="pill waiting" title="Ran on the fallback after the primary was unavailable">${esc(r.fallback)}</span>` : ''}${r.resumed ? '<span class="run-tag" title="Picked up where it left off">resumed</span>' : ''}</span>
        <span class="run-took tnum">${settingsRunTook(r.duration_s)}</span>
        <span class="run-cost tnum">${cost}</span>
        <span class="run-when tnum" title="${esc(fmt(r.finished_at))}">${esc(ago(r.finished_at))}</span>
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
    const e = botRow(change.bot), transition = transitionById.get(change.transition_id);
    const field = {owners:'who it works for',access:'access',model:'model',placement:'computer',fallback:'fallback',
      compact_at:'compact at',wake_preset:'wakes',profile:'profile',keep_tasks_going:'keep tasks going',
      turns_at_once:'Turns at once',definition:'setup',task_model:'task model',identity:'name and description',
      manages_reports:'manages its reports', lifecycle:'archive or restore'}[change.field] || change.field;
    // A change too big for a page comes without its values (backend/settings_admin.py `truncated`).
    const what = change.truncated ? 'Too big to show here.'
      : change.field === 'definition' ? settingsDefinitionChange(change.before, change.after)
      : `${settingsValue(change.field, change.before)} → ${settingsValue(change.field, change.after)}`;
    // A manager bot's change says so: it acted for the bot, as its manager (backend/report_managers.py).
    const by = change.via === 'report_manager' ? `${actorLabel(change.actor)}, as its manager` : actorLabel(change.actor);
    return `<div class="settings-history-row"><span>${avatar(change.bot, 24, stateOf(change.bot))}</span><div><strong>${esc(settingsBotName(change.bot))} · ${esc(field)}</strong>
      <p>${esc(what)}</p><p>${esc(by)} · ${esc(fmt(change.created_at))}${change.undone_at ? ` · undone ${esc(ago(change.undone_at))}` : ''}</p></div>
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
