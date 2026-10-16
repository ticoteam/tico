/* ui/app/settings-providers.js — Settings > AI providers
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- AI providers
// Which model vendors this team uses and the default model (backend/providers.py). One form
// serves the first-run step and Settings; the server derives the runtime from the model.
function providersModels(view, models, enabled) {
  const labels = Object.fromEntries((view.providers || []).map(row => [row.id, row.label]));
  return (models || []).filter(model => !model.deprecated && enabled.includes(model.provider))
    .map(model => ({id: model.id, label: modelWords(model.id) || `${labels[model.provider] || model.provider} · ${model.label}`,
                    recommended: (view.providers || []).some(row => row.recommended === model.id)}));
}
// One line per provider: box, name, a few muted words. The vendors that run through OpenRouter share one line.
const PROVIDER_LINE = {openai: 'Codex, ChatGPT or API key', anthropic: 'Claude Code, Claude or API key', google: 'Gemini CLI, API key',
  xai: 'Grok Build, Grok or API key', cursor: 'Cursor agent, Cursor or API key', meta: 'Muse Code, Meta account or API key'};
function providersFormHTML(view, models, id, editable = true) {
  const enabled = view.enabled || [], dis = editable ? '' : ' disabled';
  const box = row => `<input type="checkbox" data-provider value="${esc(row.id)}" ${enabled.includes(row.id) ? 'checked' : ''}${dis}>`;
  const rows = view.providers || [];
  const direct = rows.filter(row => PROVIDER_LINE[row.id]), routed = rows.filter(row => !PROVIDER_LINE[row.id]);
  return `<div id="${id}" data-providers-form>
    <div class="prov-list" role="group" aria-label="Providers">${direct.map(row =>
      `<label class="prov">${box(row)}<strong>${esc(row.label)}</strong><span class="muted">${esc(PROVIDER_LINE[row.id])}</span></label>`).join('')}
      ${routed.length ? `<div class="prov prov-routed"><span class="muted">Via OpenRouter</span>${routed.map(row =>
        `<label class="prov-chip">${box(row)}${esc(row.label)}</label>`).join('')}</div>` : ''}</div>
    <label class="onb-field"><span class="k">Default model</span><select data-provider-model${dis}></select></label>
    ${id === 'set-prov' ? `<label class="prov"><input type="checkbox" data-provider-fallback ${view.auto_fallback !== false ? 'checked' : ''}${dis}>
      <strong>Fall back when a plan runs out</strong><span class="muted">A bot with no fallback of its own runs on another provider signed in on its computer</span></label>
    <label class="onb-field"><span class="k">Check model</span><input type="text" data-provider-check
      value="${esc((view.check_models || []).join(', '))}"
      placeholder="${esc((view.check_order || []).map(row => row.runtime).join(', ') || 'grok, codex')}"${dis}>
      <span class="muted">Review and check subtasks run on the first of these from a provider other than the author's, then on the other providers above.</span></label>` : ''}</div>`;
}
function providersFillModels(root, view, models, keep) {
  const enabled = [...root.querySelectorAll('[data-provider]:checked')].map(input => input.value);
  const select = root.querySelector('[data-provider-model]');
  const options = providersModels(view, models, enabled);
  const wanted = options.some(row => row.id === keep) ? keep
    : (options.find(row => row.recommended) || options[0] || {}).id || '';
  select.innerHTML = options.length ? options.map(row =>
    `<option value="${esc(row.id)}" ${row.id === wanted ? 'selected' : ''}>${esc(row.label)}</option>`).join('')
    : '<option value="">Tick a provider first</option>';
}
function providersWire(root, view, models) {
  if (!root) return;
  providersFillModels(root, view, models, view.default?.model || '');
  root.querySelectorAll('[data-provider]').forEach(input => input.onchange = () =>
    providersFillModels(root, view, models, root.querySelector('[data-provider-model]').value));
}
const providersCollect = root => {
  const fallback = root.querySelector('[data-provider-fallback]'), check = root.querySelector('[data-provider-check]');
  return {enabled: [...root.querySelectorAll('[data-provider]:checked')].map(input => input.value),
    model: root.querySelector('[data-provider-model]').value, ...(fallback ? {auto_fallback: fallback.checked} : {}),
    // Sent only when edited, so a save about something else never re-checks a saved entry.
    ...(check && check.value !== check.defaultValue ? {check_models: check.value.split(',').map(item => item.trim()).filter(Boolean)} : {})};
};
async function providersSave(view, chosen) {
  return put('/v2/providers', {enabled: chosen.enabled, model: chosen.model, expected_revision: view.revision || 0,
    ...('auto_fallback' in chosen ? {auto_fallback: chosen.auto_fallback} : {}),
    ...('check_models' in chosen ? {check_models: chosen.check_models} : {})});
}
async function renderSettingsProviders() {
  const el = $('#set-providers'); if (!el) return;
  const owner = S.me?.role === 'owner';
  try {
    const view = await get('/v2/providers');
    if (!$('#set-providers')) return;
    el.innerHTML = `${providersFormHTML(view, SETTINGS_DATA.models, 'set-prov', owner)}
      ${providersNextHTML(view, owner)}
      ${owner ? '<div class="onb-actions"><button class="primary" type="button" id="set-prov-save">Save</button><span class="spacer"></span><span class="muted" id="set-prov-status"></span></div>'
        : '<p class="muted">Only the owner can change this.</p>'}`;
    providersWire($('#set-prov'), view, SETTINGS_DATA.models);
    const save = $('#set-prov-save');
    if (save) save.onclick = async () => {
      const chosen = providersCollect($('#set-prov')), status = $('#set-prov-status');
      save.disabled = true; status.textContent = 'Saving…';
      try {
        const saved = await providersSave(view, chosen);
        applyConfig({...S.config, providers_configured: !!saved.enabled?.length, default_runtime: saved.default?.runtime || ''});
        SETTINGS_DATA.enabledProviders = saved.enabled; SETTINGS_DATA.defaultModel = saved.default?.model || '';
        toast('AI providers saved');
        await renderSettingsProviders();
      } catch (error) { status.innerHTML = `<span class="err">${esc(error.message)}</span>`; save.disabled = false; }
    };
  } catch (error) { el.innerHTML = `<div class="err">${esc(error.message)}</div>`; }
}

function providersNextHTML(view, owner) {
  if (!view.enabled?.length) return '<p class="muted">Bots wait until you add an AI provider.</p>';
  const wanted = new Set(SETTINGS_DATA.models.filter(model => view.enabled.includes(model.provider)).map(model => model.runtime));
  const machines = SETTINGS_DATA.machines.filter(machine => !machine.revoked_at);
  const rows = machines.flatMap(machine => [...wanted].filter(runtime => machine.readiness?.runtimes?.[runtime]?.authenticated !== 'ready').map(runtime => {
    const online = machine.last_seen && Date.now() - new Date(machine.last_seen) < 60000;
    const ready = machine.readiness?.runtimes?.[runtime]?.installed;
    return `<li>${esc(machine.label)} · ${esc(runtime)}${owner && online && ready && harnessCaps(runtime).browser_sign_in
      ? ` <button class="ghost" type="button" data-model-login data-runner="${esc(machine.id)}" data-runtime="${esc(runtime)}" data-machine="${esc(machine.label)}">Sign in</button>`
      : ' · <a href="#/settings" data-provider-computers>Open computer</a>'}</li>`;
  }));
  return `<div class="provider-next"><p>Sign in on each computer, or <a href="${CREDENTIALS}">use an API key</a> in Credentials and share it with every computer.</p>
    ${machines.length ? (rows.length ? `<ul>${rows.join('')}</ul>` : '<p class="muted">Computers are signed in.</p>') : '<p><a href="#/settings" data-provider-computers>Add computer</a></p>'}</div>`;
}
document.addEventListener('click', event => {
  if (event.target.closest('[data-provider-computers]')) settingsShow('devices');
});
