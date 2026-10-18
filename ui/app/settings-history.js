/* ui/app/settings-history.js — Settings > History
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

function settingsValue(field, value) {
  if (field === 'owners') return value === null ? 'registry defaults' : (value || []).map(settingsPersonName).join(', ') || 'nobody';
  if (field === 'access') return accessSummary(value);
  if (field === 'model') return settingsChoiceLabel(value?.harness || value?.runtime, value?.model, value?.effort);
  if (field === 'fallback') return value ? settingsChoiceLabel(value.harness, value.model, value.reasoning_effort || value.effort) : 'None (fail)';
  if (field === 'wake_preset') return SETTINGS_WAKE_PRESETS.find(([preset]) => preset === value)?.[1] || String(value);
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
function renderSettingsHistory() {
  const el = $('#set-history'); if (!el) return;
  const changes = SETTINGS_DATA.history?.changes || [], transitions = SETTINGS_DATA.history?.transitions || [];
  const pending = transitions.filter(t => ['preparing','blocked','failed','prepared'].includes(t.state));
  const transitionById = new Map(transitions.map(t => [t.id, t]));
  const pendingRows = pending.map(t => `<div class="settings-history-row"><span>${avatar(t.bot, 24, stateOf(t.bot))}</span><div><strong>${esc(settingsBotName(t.bot))} · ${t.kind === 'model' ? 'model change' : 'computer move'}</strong><p>${esc(t.state === 'preparing' ? `Checkpointing ${t.progress.prepared} of ${t.progress.total} conversations` : t.error || t.state)}</p></div><div class="history-action"><span class="pill ${t.state === 'failed' ? 'fail' : 'waiting'}">${esc(t.state)}</span><button class="ghost" type="button" data-transition-review="${esc(t.id)}">Review</button></div></div>`).join('');
  const rows = changes.map(change => {
    const e = S.emps.find(row => row.name === change.bot), transition = transitionById.get(change.transition_id);
    const field = {owners:'who it works for',access:'access',model:'model',placement:'computer',fallback:'fallback',compact_at:'compact at',wake_preset:'wakes',definition:'setup'}[change.field] || change.field;
    const what = change.field === 'definition' ? settingsDefinitionChange(change.before, change.after)
      : `${settingsValue(change.field, change.before)} → ${settingsValue(change.field, change.after)}`;
    return `<div class="settings-history-row"><span>${avatar(change.bot, 24, stateOf(change.bot))}</span><div><strong>${esc(settingsBotName(change.bot))} · ${esc(field)}</strong>
      <p>${esc(what)}</p><p>${esc(actorLabel(change.actor))} · ${esc(fmt(change.created))}${change.undone_at ? ` · undone ${esc(ago(change.undone_at))}` : ''}</p></div>
      <div class="history-action">${change.can_undo && e ? `<button class="ghost" type="button" data-settings-undo="${esc(change.id)}" data-bot="${esc(change.bot)}" data-revision="${e.revision}">Undo</button>` : ''}</div>
      ${settingsCheckpointDetails(transition)}</div>`;
  }).join('');
  el.innerHTML = `${pendingRows ? `<h3>In progress</h3>${pendingRows}` : ''}${rows || '<div class="empty">No settings changes yet.</div>'}`;
  el.onclick = async event => {
    const review = event.target.closest('[data-transition-review]');
    if (review) { settingsWatchTransition(review.dataset.transitionReview); return; }
    const undo = event.target.closest('[data-settings-undo]'); if (!undo) return;
    undo.disabled = true;
    try {
      const result = await post(`/v2/settings/history/${encodeURIComponent(undo.dataset.settingsUndo)}/undo`, {expected_revision:Number(undo.dataset.revision)});
      if (result.id) settingsWatchTransition(result.id); else { await loadSettings(); toast('Settings change undone'); }
    } catch (error) {undo.disabled = false; toast(error.message, true);}
  };
}
