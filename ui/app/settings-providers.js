/* ui/app/settings-providers.js — Settings > Models: Tico Gateway and Defaults; the first-run models step
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
    </div>`;
}
function providersFillModels(root, view, models, keep) {
  // The first-run step ticks providers; Settings > Models offers what the company has on (its subscriptions tick them).
  const gateway = providersGatewayIds(view).filter(id => (view.enabled || []).includes(id));
  const boxes = root.querySelectorAll('[data-provider]');
  const enabled = boxes.length ? [...[...boxes].filter(input => input.checked).map(input => input.value), ...gateway] : view.enabled || [];
  const select = root.querySelector('[data-provider-model]');
  const options = providersModels(view, models, enabled);
  const meetingOptions = providersModels(view, models, enabled, true);
  const wanted = options.some(row => row.id === keep) ? keep
    : (options.find(row => row.recommended) || options[0] || {}).id || '';
  select.innerHTML = options.length ? options.map(row =>
    `<option value="${esc(row.id)}" ${row.id === wanted ? 'selected' : ''}${row.description ? ` title="${esc(row.description)}"` : ''}>${esc(row.label)}</option>`).join('')
    : `<option value="">${boxes.length ? 'Tick a provider first' : 'No models yet'}</option>`;
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
// ----------------------------------------------------------------- Settings > Models: Tico Gateway and Defaults
let SETTINGS_PROVIDERS_VIEW = null;
// The Gateway toggle is the models preset (backend/models_preset.py): on runs a computer with no subscription for a
// tool, or one whose plan ran out, on Tico Gateway; off waits. "Gateway only" and "own keys" keep their place.
const providersGatewayOn = view => ['subscriptions-gateway', 'gateway'].includes(view?.preset || 'subscriptions-gateway');
const providersPresetFor = (view, on) => on ? (view.preset === 'gateway' ? 'gateway' : 'subscriptions-gateway')
  : (view.preset === 'own-keys' ? 'own-keys' : 'subscriptions');
// Read the providers view once for the Computers tab too: its pickers say "Gateway" or "None" by the toggle.
async function providersViewLoad() {
  const view = await get('/v2/providers', {fresh: true});
  SETTINGS_PROVIDERS_VIEW = view;
  return view;
}
// A label with at most one (?) bubble, then its control.
const providersRow = (id, label, control, help = '') =>
  `<div class="models-row"><span class="k"><label for="${id}">${esc(label)}</label>${help ? helpBubble(help) : ''}</span>${control}</div>`;
function providersDefaultsHTML(view, owner) {
  const dis = owner ? '' : ' disabled';
  const levels = view.decide_levels || [];
  return `<form id="set-prov" class="models-form" data-providers-form>
    ${providersRow('set-prov-model', 'Default model', `<select id="set-prov-model" class="settings-inline-select" data-provider-model${dis}></select>`)}
    ${levels.length ? providersRow('set-prov-decide', 'Decisions', `<select id="set-prov-decide" class="settings-inline-select" data-provider-decide${dis}>${levels.map(level =>
      `<option value="${esc(level)}"${level === (view.decide_level || levels[0]) ? ' selected' : ''}>${esc(level.charAt(0).toUpperCase() + level.slice(1))}</option>`).join('')}</select>`,
      'How carefully Tico judges approvals and checks. Higher is slower and costs more.') : ''}
    ${providersRow('set-prov-meeting', 'Meeting model', `<select id="set-prov-meeting" class="settings-inline-select" data-provider-meeting${dis}></select>`)}
    ${providersRow('set-prov-fallback', 'Fall back when a plan runs out', `<input id="set-prov-fallback" type="checkbox" class="people-switch" data-provider-fallback${view.auto_fallback !== false ? ' checked' : ''}${dis}>`,
      'A bot with no fallback of its own runs on another AI tool signed in on its computer.')}
    ${providersRow('set-prov-check', 'Check model', `<input id="set-prov-check" type="text" data-provider-check value="${esc((view.check_models || []).join(', '))}"
      placeholder="${esc((view.check_order || []).map(row => row.runtime).join(', ') || 'grok, codex')}"${dis}>`,
      "Reviews and checks prefer the first of these from a provider other than the author's.")}
    ${owner ? '<div class="onb-actions"><button class="primary" type="submit" id="set-prov-save">Save</button><span class="muted" id="set-prov-status" role="status"></span></div>' : settingsWhoCanHTML('models')}
  </form>`;
}
function providersGatewayCardHTML(view, gateway, owner) {
  const [tone, line] = gateway ? gatewayStatusLine(gateway) : ['', ''];
  return `<div class="models-gw">
      <label class="models-gw-toggle"><input type="checkbox" class="people-switch" data-gateway-toggle${providersGatewayOn(view) ? ' checked' : ''}${owner ? '' : ' disabled'}>
        <span>Use when a computer has no subscription, or a plan runs out</span></label></div>
    ${gateway ? `<details class="models-adv" data-gateway-advanced><summary>Advanced</summary>
      <p class="models-gw-line"><span class="dot ${tone === 'ok' ? 'ok' : 'failed'}" aria-hidden="true"></span><span data-gateway-line>${esc(line)}</span>${settingsIsAdmin() ? ' · <a href="#/settings/gateway">Picks and services</a>' : ''}</p>
      ${gatewayHTML(gateway, owner)}</details>` : ''}`;
}
async function renderSettingsProviders() {
  const el = $('#set-providers'), card = $('#set-gateway-card');
  if (!el || !card) return;
  if (formBusy(el) || formBusy(card)) return;
  const owner = S.me?.role === 'owner';
  try {
    const [view, gateway] = await Promise.all([providersViewLoad(), get('/v2/providers/gateway').catch(() => null)]);
    if (!el.isConnected) return;
    const open = !!card.querySelector('[data-gateway-advanced]')?.open;
    card.innerHTML = providersGatewayCardHTML(view, gateway, owner);
    if (open) card.querySelector('[data-gateway-advanced]').open = true;
    el.innerHTML = providersDefaultsHTML(view, owner);
    const form = $('#set-prov');
    providersWire(form, view, SETTINGS_DATA.models);
    if (gateway) gatewayWire(gateway);
    settingsReveal('#settings-models');
    const toggle = card.querySelector('[data-gateway-toggle]');
    toggle.onchange = async () => {
      toggle.disabled = true;
      try {
        const saved = await put('/v2/providers', {enabled: view.enabled || [], model: view.default?.model || '', expected_revision: view.revision || 0,
          preset: providersPresetFor(view, toggle.checked)});
        SETTINGS_PROVIDERS_VIEW = saved;
        toast(toggle.checked ? 'Gateway on' : 'Gateway off');
      } catch (error) { toast(error.message, true); toggle.checked = !toggle.checked; }
      await renderSettingsProviders();
    };
    if (owner) form.onsubmit = async event => {
      event.preventDefault();
      const save = $('#set-prov-save'), status = $('#set-prov-status');
      // No preset here (the Gateway toggle is its own save), and the providers stay as the company has them.
      const chosen = {...providersCollect(form), enabled: view.enabled || []};
      delete chosen.preset;
      save.disabled = true; status.textContent = 'Saving…';
      try {
        const saved = await providersSave(view, chosen);
        applyConfig({...S.config, providers_configured: !!saved.enabled?.length, default_runtime: saved.default?.runtime || ''});
        SETTINGS_DATA.enabledProviders = saved.enabled; SETTINGS_DATA.defaultModel = saved.default?.model || '';
        form.querySelectorAll('input').forEach(input => { input.defaultValue = input.value; });
        toast('Saved');
        await renderSettingsProviders();
      } catch (error) { status.innerHTML = `<span class="err">${esc(error.message)}</span>`; save.disabled = false; }
    };
  } catch (error) { el.innerHTML = `<div class="err">${esc(error.message)}</div>`; }
}

// ----------------------------------------------------------------- Tico AI Gateway: key and address
// One key (and the Gateway's URL) for everything the app asks Tico's Gateway: Decide and the Tico models. The key is
// typed once and never shown again: the server keeps it sealed and answers with its first and last three characters.
function gatewayHTML(gateway, owner) {
  if (!owner) return '';
  const state = gateway.state || (gateway.connected ? 'connected' : 'none');
  const message = gateway.message ? `<p class="${gateway.working === false || state === 'cap_reached' ? 'err' : 'muted'}" data-gateway-message>${esc(gateway.message)}</p>` : '';
  // An install the install command invited gets its own key from Tico: nothing to paste, and a pasted key still wins.
  const automatic = gateway.automatic ? '<p class="muted" data-gateway-automatic>Connected automatically</p>'
    : gateway.auto_off ? '<div class="onb-actions"><button class="ghost" type="button" data-gateway-automatic-on>Connect automatically</button></div>' : '';
  if (!gateway.editable) {
    return `<div class="prov-gateway" id="set-gateway">
      <p class="muted">Set by the server's environment (${esc(gateway.env || 'TICO_GATEWAY_KEY')}).</p>${message}
      <div class="onb-actions"><button class="ghost" type="button" data-gateway-check>Check now</button></div></div>`;
  }
  return `<div class="prov-gateway" id="set-gateway">${automatic}
    ${providersRow('set-gateway-key', gateway.automatic ? 'Own gateway key' : 'Gateway key', `<input id="set-gateway-key" type="password" autocomplete="off" spellcheck="false"
      data-gateway-key placeholder="${gateway.connected ? esc(`Saved (${gateway.preview || '••••••'})`) : 'tgw_…'}">`)}
    ${providersRow('set-gateway-url', 'Gateway URL', `<input id="set-gateway-url" type="url" data-gateway-url
      value="${esc(gateway.url && gateway.url !== gateway.default_url ? gateway.url : '')}" placeholder="${esc(gateway.default_url || '')}">`)}
    ${message}
    <div class="onb-actions"><button class="primary" type="button" data-gateway-save>${gateway.connected ? 'Save' : 'Connect'}</button>
      ${gateway.connected ? '<button class="ghost" type="button" data-gateway-check>Check now</button><button class="ghost danger" type="button" data-gateway-clear>Clear key</button>' : ''}
      <span class="spacer"></span><span class="muted" data-gateway-status role="status"></span></div></div>`;
}
function gatewayWire(gateway) {
  const root = $('#set-gateway'); if (!root) return;
  const status = root.querySelector('[data-gateway-status]');
  const run = async (button, busy, work, done) => {
    button.disabled = true; if (status) status.textContent = busy;
    try { const shown = await work(); toast(done(shown), shown?.working === false); await loadSettings(); await renderSettingsProviders(); }
    catch (error) { if (status) status.innerHTML = `<span class="err">${esc(error.message)}</span>`; else toast(error.message, true); button.disabled = false; }
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
  if (clear) clear.onclick = async () => {
    if (!await settingsConfirm({title: 'Clear the gateway key?', text: 'Bots on Tico models stop until a key is saved again.', ok: 'Clear key'})) return;
    run(clear, 'Clearing…', () => writeRequest('DELETE', '/v2/providers/gateway'), () => 'Gateway key cleared');
  };
}
