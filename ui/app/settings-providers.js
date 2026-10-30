/* ui/app/settings-providers.js — Settings > Computers > Models
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- Models
// Which model vendors this team uses and the default model (backend/providers.py). One form
// serves the first-run step and Settings; the server derives the runtime from the model.
function providersModels(view, models, enabled, meeting = false) {
  const labels = Object.fromEntries((view.providers || []).map(row => [row.id, row.label]));
  const gateway = providersGatewayIds(view);
  // The saved default stays offered after its model retires, so a save about something else never moves it.
  const saved = view.default?.model || '';
  // The Gateway's tiers are 60 rows; the company default offers General at each level, and a bot's own Model picker the rest.
  // The meeting models (Settings' Meeting model select) are only the meeting ones; a default or a bot's pick never is.
  const general = meeting ? model => !!model.meeting
    : model => !model.tier || model.id === saved || (model.tier.department === 'general' && !model.tier.fast);
  return (models || []).filter(model => (!model.deprecated || model.id === saved) && enabled.includes(model.provider) && general(model))
    .map(model => ({id: model.id,
                    // A Tico model says what runs underneath it ("Claude Sonnet 5.5 underneath").
                    description: gateway.includes(model.provider) ? model.description || '' : '',
                    label: (modelWords(model.id) || `${labels[model.provider] || model.provider} · ${model.label}`)
                      + (model.deprecated ? (model.lineup === 'old' ? '' : ' (retired)') : ''),
                    recommended: (view.providers || []).some(row => row.recommended === model.id)}));
}
// The Tico AI Gateway's provider is on while its connection has a key, never ticked (backend/gateway_connection.py).
const providersGatewayIds = view => (view.providers || []).filter(row => row.gateway).map(row => row.id);
// One line per provider: box, name, a few muted words. The vendors that run through OpenRouter share one line.
const PROVIDER_LINE = {openai: 'Codex, ChatGPT or API key', anthropic: 'Claude Code, Claude or API key', google: 'Gemini CLI, API key',
  xai: 'Grok Build, Grok or API key', cursor: 'Cursor agent, Cursor or API key', meta: 'Muse Code, Meta account or API key'};
// The company's models choice (backend/models_preset.py), first in the form. Plain words only: no provider logos, and
// nothing that says Tico partners with, or is approved by, a model provider.
const PROVIDER_PRESETS = [
  {id: 'subscriptions-gateway', label: 'Your Claude and ChatGPT subscriptions, plus Tico Gateway', recommended: true,
   line: "Sign in with your own Claude and ChatGPT accounts. Don't share one login across your team. Until you sign in, and when a plan runs out, bots run on Tico Gateway."},
  {id: 'subscriptions', label: 'Your Claude and ChatGPT subscriptions only',
   line: 'Bots wait until a Claude or ChatGPT account is signed in on their computer.'},
  {id: 'gateway', label: 'Tico Gateway only', line: 'Bots run on Tico models with the company\'s gateway key below.'},
  {id: 'own-keys', label: "Your company's plans or API keys", line: 'Bring your own: tick the providers below and sign in, or add API keys in Credentials.'},
];
// The two choices that run main work on Claude and ChatGPT tick them when nothing is ticked yet.
const PRESET_PROVIDERS = {'subscriptions-gateway': ['anthropic', 'openai'], subscriptions: ['anthropic', 'openai']};
function providersPresetHTML(view, dis) {
  const known = view.presets || PROVIDER_PRESETS.map(row => row.id);
  return `<div class="prov-list" role="radiogroup" aria-label="Models" data-provider-presets>${PROVIDER_PRESETS.filter(row => known.includes(row.id)).map(row =>
    `<label class="prov prov-preset"><input type="radio" name="provider-preset" data-provider-preset value="${esc(row.id)}" ${view.preset === row.id ? 'checked' : ''}${dis}>
      <span class="prov-preset-title"><strong>${esc(row.label)}</strong>${row.recommended ? ' <span class="pill">Recommended</span>' : ''}</span><span class="muted">${esc(row.line)}</span></label>`).join('')}</div>`;
}
function providersFormHTML(view, models, id, editable = true) {
  const enabled = view.enabled || [], dis = editable ? '' : ' disabled';
  const box = row => `<input type="checkbox" data-provider value="${esc(row.id)}" ${enabled.includes(row.id) ? 'checked' : ''}${dis}>`;
  const rows = (view.providers || []).filter(row => !row.gateway);
  const direct = rows.filter(row => PROVIDER_LINE[row.id]), routed = rows.filter(row => !PROVIDER_LINE[row.id]);
  return `<div id="${id}" data-providers-form>
    ${providersPresetHTML(view, dis)}
    <div data-provider-details${id === 'onb-prov' && view.preset !== 'own-keys' ? ' hidden' : ''}>
    <div class="prov-list" role="group" aria-label="Providers">${direct.map(row =>
      `<label class="prov">${box(row)}<strong>${esc(row.label)}</strong><span class="muted">${esc(PROVIDER_LINE[row.id])}</span></label>`).join('')}
      ${routed.length ? `<div class="prov prov-routed"><span class="muted">Via OpenRouter</span>${routed.map(row =>
        `<label class="prov-chip">${box(row)}${esc(row.label)}</label>`).join('')}</div>` : ''}</div>
    <label class="onb-field"><span class="k">Default model</span><select data-provider-model${dis}></select></label></div>
    ${id === 'set-prov' && view.decide_levels?.length ? `<label class="onb-field"><span class="k">Decisions</span><select data-provider-decide${dis}>${view.decide_levels.map(level =>
      `<option value="${esc(level)}"${level === (view.decide_level || view.decide_levels[0]) ? ' selected' : ''}>${esc(level.charAt(0).toUpperCase() + level.slice(1))}</option>`).join('')}</select>
      <span class="muted">How carefully Tico judges approvals and checks. Higher is slower and costs more.</span></label>` : ''}
    ${id === 'set-prov' ? `<label class="onb-field"><span class="k">Meeting model</span><select data-provider-meeting${dis}></select></label>` : ''}
    ${id === 'set-prov' ? `<label class="prov"><input type="checkbox" data-provider-fallback ${view.auto_fallback !== false ? 'checked' : ''}${dis}>
      <strong>Fall back when a plan runs out</strong><span class="muted">A bot with no fallback of its own runs on another provider signed in on its computer</span></label>
    <label class="onb-field"><span class="k">Check model</span><input type="text" data-provider-check
      value="${esc((view.check_models || []).join(', '))}"
      placeholder="${esc((view.check_order || []).map(row => row.runtime).join(', ') || 'grok, codex')}"${dis}>
      <span class="muted">Review and check subtasks prefer the first of these from a provider other than the author's, then the other providers above. With none that can run, they use the author's own rather than wait.</span></label>` : ''}</div>`;
}
function providersFillModels(root, view, models, keep) {
  const gateway = providersGatewayIds(view).filter(id => (view.enabled || []).includes(id));
  const enabled = [...[...root.querySelectorAll('[data-provider]:checked')].map(input => input.value), ...gateway];
  const select = root.querySelector('[data-provider-model]');
  const options = providersModels(view, models, enabled);
  const meetingOptions = providersModels(view, models, enabled, true);
  const wanted = options.some(row => row.id === keep) ? keep
    : (options.find(row => row.recommended) || options[0] || {}).id || '';
  select.innerHTML = options.length ? options.map(row =>
    `<option value="${esc(row.id)}" ${row.id === wanted ? 'selected' : ''}${row.description ? ` title="${esc(row.description)}"` : ''}>${esc(row.label)}</option>`).join('')
    : '<option value="">Tick a provider first</option>';
  const meeting = root.querySelector('[data-provider-meeting]');
  if (meeting) {
    // Live-meeting replies: the bot's own model, or one of the models above; the saved one stays selected.
    const keepMeeting = meeting.options.length ? meeting.value : (view.meeting_model || '');
    meeting.dataset.saved = view.meeting_model || '';
    meeting.innerHTML = `<option value="">Bot's own model</option>` + meetingOptions.map(row =>
      `<option value="${esc(row.id)}" ${row.id === keepMeeting ? 'selected' : ''}>${esc(row.label)}</option>`).join('');
  }
}
function providersWire(root, view, models) {
  if (!root) return;
  providersFillModels(root, view, models, view.default?.model || '');
  root.querySelectorAll('[data-provider]').forEach(input => input.onchange = () =>
    providersFillModels(root, view, models, root.querySelector('[data-provider-model]').value));
  root.querySelectorAll('[data-provider-preset]').forEach(input => input.onchange = () => {
    if (root.id === 'onb-prov') root.querySelector('[data-provider-details]').hidden = input.value !== 'own-keys';
    const boxes = [...root.querySelectorAll('[data-provider]')];
    if (boxes.some(box => box.checked)) return;
    boxes.filter(box => (PRESET_PROVIDERS[input.value] || []).includes(box.value)).forEach(box => { box.checked = true; });
    providersFillModels(root, view, models, root.querySelector('[data-provider-model]').value);
  });
}
const providersCollect = root => {
  const fallback = root.querySelector('[data-provider-fallback]'), check = root.querySelector('[data-provider-check]');
  const preset = root.querySelector('[data-provider-preset]:checked'), decide = root.querySelector('[data-provider-decide]');
  const meeting = root.querySelector('[data-provider-meeting]');
  return {enabled: [...root.querySelectorAll('[data-provider]:checked')].map(input => input.value),
    ...(preset ? {preset: preset.value} : {}),
    model: root.querySelector('[data-provider-model]').value, ...(decide ? {decide_level: decide.value} : {}), ...(fallback ? {auto_fallback: fallback.checked} : {}),
    // Sent only when changed, so a save about something else keeps an unset meeting model unset.
    ...(meeting && meeting.value !== meeting.dataset.saved ? {meeting_model: meeting.value} : {}),
    // Sent only when edited, so a save about something else never re-checks a saved entry.
    ...(check && check.value !== check.defaultValue ? {check_models: check.value.split(',').map(item => item.trim()).filter(Boolean)} : {})};
};
async function providersSave(view, chosen) {
  return put('/v2/providers', {enabled: chosen.enabled, model: chosen.model, expected_revision: view.revision || 0,
    ...('auto_fallback' in chosen ? {auto_fallback: chosen.auto_fallback} : {}),
    ...('check_models' in chosen ? {check_models: chosen.check_models} : {}),
    ...('preset' in chosen ? {preset: chosen.preset} : {}),
    ...('decide_level' in chosen ? {decide_level: chosen.decide_level} : {}),
    ...('meeting_model' in chosen ? {meeting_model: chosen.meeting_model} : {})});
}
let SETTINGS_PROVIDERS_VIEW = null;
function renderSettingsProviderNext() {
  const root = $('#set-provider-next');
  if (!root || !SETTINGS_PROVIDERS_VIEW) return;
  const restoreFocus = computerFocusRestore(root);
  patchHTML(root, providersNextHTML(SETTINGS_PROVIDERS_VIEW, S.me?.role === 'owner'));
  restoreFocus();
}
async function renderSettingsProviders() {
  const el = $('#set-providers'); if (!el) return;
  const owner = S.me?.role === 'owner';
  try {
    const view = await get('/v2/providers');
    if (!$('#set-providers')) return;
    const gateway = await get('/v2/providers/gateway').catch(() => null);
    if (!$('#set-providers')) return;
    SETTINGS_PROVIDERS_VIEW = view;
    el.innerHTML = `${providersFormHTML(view, SETTINGS_DATA.models, 'set-prov', owner)}
      <div id="set-provider-next">${providersNextHTML(view, owner)}</div>
      ${owner ? '<div class="onb-actions"><button class="primary" type="button" id="set-prov-save">Save</button><span class="spacer"></span><span class="muted" id="set-prov-status"></span></div>'
        : settingsWhoCanHTML('models')}
      ${gateway ? gatewayHTML(gateway, owner) : ''}`;
    providersWire($('#set-prov'), view, SETTINGS_DATA.models);
    if (gateway) gatewayWire(gateway);
    settingsReveal('#settings-models');
    const save = $('#set-prov-save');
    if (save) save.onclick = async () => {
      const chosen = providersCollect($('#set-prov')), status = $('#set-prov-status');
      save.disabled = true; status.textContent = 'Saving…';
      try {
        const saved = await providersSave(view, chosen);
        applyConfig({...S.config, providers_configured: !!saved.enabled?.length, default_runtime: saved.default?.runtime || ''});
        SETTINGS_DATA.enabledProviders = saved.enabled; SETTINGS_DATA.defaultModel = saved.default?.model || '';
        toast('Saved');
        await renderSettingsProviders();
      } catch (error) { status.innerHTML = `<span class="err">${esc(error.message)}</span>`; save.disabled = false; }
    };
  } catch (error) { el.innerHTML = `<div class="err">${esc(error.message)}</div>`; }
}

// ----------------------------------------------------------------- Tico AI Gateway
// One key (and the Gateway's URL) for everything the app asks Tico's Gateway: Decide and the Tico models. The key is
// typed once and never shown again: the server keeps it sealed and answers with its first and last three characters.
const GATEWAY_STATE = {connected: 'ok', unchecked: 'ok', warning: 'needs', not_ready: 'needs', cap_reached: 'fail', revoked: 'fail', unknown: 'fail'};
function gatewayHTML(gateway, owner) {
  const state = gateway.state || (gateway.connected ? 'connected' : 'none');
  const badge = gateway.connected
    ? `<span class="pill ${GATEWAY_STATE[state] || ''}" data-gateway-state="${esc(state)}">${esc(
      gateway.working === false ? 'Not working' : state === 'cap_reached' ? 'Gateway cap reached'
        : state === 'not_ready' ? 'Not ready' : 'Connected')}</span>`
    : '<span class="pill" data-gateway-state="none">Not connected</span>';
  const head = `<h3 class="prov-gateway-title">Tico AI Gateway ${badge}</h3>
    <p class="muted">Tico's own models and Decide, with one gateway key from Tico.${settingsIsAdmin() ? ' <a href="#/settings/gateway">Picks and services</a>' : ''}</p>`;
  if (!owner) return `<div class="prov-gateway" id="set-gateway">${head}</div>`;
  const message = gateway.message ? `<p class="${gateway.working === false || state === 'cap_reached' ? 'err' : 'muted'}" data-gateway-message>${esc(gateway.message)}</p>` : '';
  // An install the install command invited gets its own key from Tico: nothing to paste, and a pasted key still wins.
  const automatic = gateway.automatic ? '<p data-gateway-automatic>Tico AI Gateway: connected automatically</p>'
    : gateway.auto_off ? `<p class="muted">This install can connect to the Tico AI Gateway by itself.</p>
      <div class="onb-actions"><button class="ghost" type="button" data-gateway-automatic-on>Connect automatically</button></div>` : '';
  if (!gateway.editable) {
    return `<div class="prov-gateway" id="set-gateway">${head}
      <p class="muted">Set by the server's environment (${esc(gateway.env || 'TICO_GATEWAY_KEY')}); change it there.</p>${message}
      <div class="onb-actions"><button class="ghost" type="button" data-gateway-check>Check now</button></div></div>`;
  }
  return `<div class="prov-gateway" id="set-gateway">${head}${automatic}
    <label class="onb-field"><span class="k">${gateway.automatic ? 'Use your own gateway key instead' : 'Gateway key'}</span><input type="password" autocomplete="off" spellcheck="false"
      data-gateway-key placeholder="${gateway.connected ? esc(`Saved (${gateway.preview || '••••••'}). Leave empty to keep it`) : 'Paste the gateway key Tico gave you'}"></label>
    <label class="onb-field"><span class="k">Gateway URL</span><input type="url" data-gateway-url
      value="${esc(gateway.url && gateway.url !== gateway.default_url ? gateway.url : '')}" placeholder="${esc(gateway.default_url || '')}"></label>
    ${message}
    <div class="onb-actions"><button class="primary" type="button" data-gateway-save>${gateway.connected ? 'Save' : 'Connect'}</button>
      ${gateway.connected ? '<button class="ghost" type="button" data-gateway-check>Check now</button><button class="ghost danger" type="button" data-gateway-clear>Clear key</button>' : ''}
      <span class="spacer"></span><span class="muted" data-gateway-status></span></div></div>`;
}
function gatewayWire(gateway) {
  const root = $('#set-gateway'); if (!root) return;
  const status = root.querySelector('[data-gateway-status]');
  const run = async (button, busy, work, done) => {
    button.disabled = true; if (status) status.textContent = busy;
    try { const shown = await work(); toast(done(shown), shown?.working === false); await loadSettings(); await renderSettingsProviders(); }
    catch (error) { if (status) status.innerHTML = `<span class="err">${esc(error.message)}</span>`; else toast(error.message); button.disabled = false; }
  };
  const save = root.querySelector('[data-gateway-save]');
  if (save) save.onclick = () => {
    const key = root.querySelector('[data-gateway-key]'), url = root.querySelector('[data-gateway-url]');
    // The key leaves the field as soon as it is sent: nothing on the page holds it after a save.
    const body = {url: url.value.trim(), expected_revision: gateway.revision || 0, ...(key.value.trim() ? {key: key.value.trim()} : {})};
    key.value = '';
    // A new address for another service while a key is saved asks first: keep the key, or clear it and paste one again.
    run(save, 'Saving…', () => settingsDestinationSave(extra => put('/v2/providers/gateway', {...body, ...extra})),
      saved => saved.credentials_choice === 'clear' ? 'Gateway address saved and its key cleared: paste a key for the new address'
        : saved.working === false ? saved.message : 'Tico AI Gateway saved');
  };
  const check = root.querySelector('[data-gateway-check]');
  if (check) check.onclick = () => run(check, 'Checking…', () => post('/v2/providers/gateway/check', {}), shown => shown.message || 'Checked');
  const automaticOn = root.querySelector('[data-gateway-automatic-on]');
  if (automaticOn) automaticOn.onclick = () => run(automaticOn, 'Connecting…', () => post('/v2/providers/gateway/automatic', {}), () => 'Connecting automatically');
  const clear = root.querySelector('[data-gateway-clear]');
  if (clear) clear.onclick = () => {
    if (!confirm('Clear the gateway key? Bots on Tico models stop until a key is saved again.')) return;
    run(clear, 'Clearing…', () => writeRequest('DELETE', '/v2/providers/gateway'), () => 'Gateway key cleared');
  };
}

function providersNextHTML(view, owner) {
  if (!view.enabled?.length) return '<p class="muted">Bots wait until you add an AI provider.</p>';
  const wanted = new Set(SETTINGS_DATA.models.filter(model => view.enabled.includes(model.provider)).map(model => model.runtime));
  const machines = SETTINGS_DATA.machines.filter(machine => !machine.revoked_at);
  const rows = machines.flatMap(machine => [...wanted].filter(runtime => machine.readiness?.runtimes?.[runtime]?.authenticated !== 'ready').map(runtime => {
    const online = machineOnline(machine);
    const ready = machine.readiness?.runtimes?.[runtime]?.installed;
    return `<li>${esc(machine.label)} · ${esc(runtime)}${owner && online && ready && harnessCaps(runtime).browser_sign_in
      ? ` <button class="ghost" type="button" data-model-login data-runner="${esc(machine.id)}" data-runtime="${esc(runtime)}" data-machine="${esc(machine.label)}">Sign in</button>`
      : ' · <button class="linkish" type="button" data-provider-computer="${esc(machine.id)}">Open computer</button>'}</li>`;
  }));
  return `<div class="provider-next"><p>Sign in on each computer, or <a href="${CREDENTIALS}">use an API key</a> in Credentials and share it with every computer.</p>
    ${machines.length ? (rows.length ? `<ul>${rows.join('')}</ul>` : '<p class="muted">Computers are signed in.</p>') : ''}</div>`;
}
// The computers list is just above: open that computer's pop-up.
document.addEventListener('click', event => {
  const link = event.target.closest('[data-provider-computer]');
  if (link) $(`#set-machines [data-computer-open="${CSS.escape(link.dataset.providerComputer)}"]`)?.click();
});
