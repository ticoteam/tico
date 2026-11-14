/* ui/app/settings-providers.ts — Settings > Models: Tico Gateway and Defaults; the first-run models step
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// What the providers view (GET /v2/providers, backend/providers.py) carries, as the page reads it.
interface SettingsProviderRow { id: string; label: string; gateway?: boolean; recommended?: string | null; runtime?: string; }
interface SettingsProvidersView {
  providers?: SettingsProviderRow[];
  /** Whether the team has chosen its AI providers (the first run starts with them until it has). */
  configured?: boolean;
  presets?: string[];
  preset?: string;
  enabled?: string[];
  default?: { model?: string; runtime?: string } | null;
  revision?: number;
  meeting_model?: string;
  auto_fallback?: boolean;
  check_models?: string[];
  check_order?: { runtime: string }[];
}
// One model the company can run (backend/providers.py), as the model lists carry it.
interface SettingsProviderModel {
  id: string;
  provider: string;
  label: string;
  description?: string;
  deprecated?: boolean;
  meeting?: boolean;
  tier?: { fast?: boolean } | null;
}
// One option of the Default model and Meeting model selects.
interface SettingsModelOption { id: string; description: string; label: string; recommended: boolean; }
// One of the company's models choices (backend/models_preset.py), first in the form.
interface SettingsProviderPreset { id: string; label: string; recommended?: boolean; line: string; }
// What saving the Models form sends (providersCollect): each part only when it applies.
interface SettingsProvidersChoice {
  enabled: string[];
  preset?: string;
  model: string;
  auto_fallback?: boolean;
  meeting_model?: string;
  check_models?: string[];
}
// The Gateway connection as GET /v2/providers/gateway answers it (backend/gateway_connection.py).
interface SettingsGatewayView {
  state?: string;
  connected?: boolean;
  editable?: boolean;
  env?: string;
  message?: string;
  working?: boolean;
  automatic?: boolean;
  auto_off?: boolean;
  preview?: string;
  url?: string | null;
  default_url?: string | null;
  revision?: number;
}
// What a Gateway key, address, check or connect answers.
interface SettingsGatewayAnswer { working?: boolean; message?: string; credentials_choice?: string; }

// ----------------------------------------------------------------- Models
// Which model vendors this team uses and the default model (backend/providers.py). One form
// serves the first-run step and Settings; the server derives the runtime from the model.
function providersModels(view: SettingsProvidersView, models: SettingsProviderModel[] | null | undefined, enabled: string[], meeting = false): SettingsModelOption[] {
  const labels: Record<string, string> = Object.fromEntries((view.providers || []).map(row => [row.id, row.label]));
  const gateway = providersGatewayIds(view);
  // The saved default stays offered after its model retires, so a save about something else never moves it.
  const saved = view.default?.model || '';
  // The company default offers each level's regular tier; a bot's own Model picker has Fast too.
  // The meeting models (Settings' Meeting model select) are only the meeting ones; a default or a bot's pick never is.
  const general: (model: SettingsProviderModel) => boolean = meeting ? model => !!model.meeting
    : model => !model.tier || model.id === saved || !model.tier.fast;
  return (models || []).filter(model => (!model.deprecated || model.id === saved) && enabled.includes(model.provider) && general(model))
    .map(model => ({id: model.id,
                    // A Tico model says what runs underneath it ("Claude Sonnet 5.5 underneath").
                    description: gateway.includes(model.provider) ? model.description || '' : '',
                    label: (modelWords(model.id) || `${labels[model.provider] || model.provider} · ${model.label}`)
                      + (model.deprecated ? ' (retired)' : ''),
                    recommended: (view.providers || []).some(row => row.recommended === model.id)}));
}
// The Tico AI Gateway's provider is on while its connection has a key, never ticked (backend/gateway_connection.py).
const providersGatewayIds = (view: SettingsProvidersView): string[] => (view.providers || []).filter(row => row.gateway).map(row => row.id);
// One line per provider: box, name, a few muted words. The vendors that run through OpenRouter share one line.
const PROVIDER_LINE: Record<string, string> = {openai: 'Codex, ChatGPT or API key', anthropic: 'Claude Code, Claude or API key', google: 'Gemini CLI, API key',
  xai: 'Grok Build, Grok or API key', cursor: 'Cursor agent, Cursor or API key', meta: 'Muse Code, Meta account or API key'};
// The company's models choice (backend/models_preset.py), first in the form. Plain words only: no provider logos, and
// nothing that says Tico partners with, or is approved by, a model provider.
const PROVIDER_PRESETS: SettingsProviderPreset[] = [
  {id: 'subscriptions-gateway', label: 'Your Claude and ChatGPT subscriptions, plus Tico Gateway', recommended: true,
   line: "Sign in with your own Claude and ChatGPT accounts. Don't share one login across your team. Until you sign in, and when a plan runs out, bots run on Tico Gateway."},
  {id: 'subscriptions', label: 'Your Claude and ChatGPT subscriptions only',
   line: 'Bots wait until a Claude or ChatGPT account is signed in on their computer.'},
  {id: 'gateway', label: 'Tico Gateway only', line: 'Bots run on Tico models with the company\'s gateway key below.'},
  {id: 'own-keys', label: "Your company's plans or API keys", line: 'Bring your own: tick the providers below and sign in, or add API keys in Credentials.'},
];
function providersPresetHTML(view: SettingsProvidersView, dis: string): string {
  const known = view.presets || PROVIDER_PRESETS.map(row => row.id);
  return `<div class="prov-list" role="radiogroup" aria-label="Models" data-provider-presets>${PROVIDER_PRESETS.filter(row => known.includes(row.id)).map(row =>
    `<label class="prov prov-preset"><input type="radio" name="provider-preset" data-provider-preset value="${esc(row.id)}" ${view.preset === row.id ? 'checked' : ''}${dis}>
      <span class="prov-preset-title"><strong>${esc(row.label)}</strong>${row.recommended ? ' <span class="pill">Recommended</span>' : ''}</span><span class="muted">${esc(row.line)}</span></label>`).join('')}</div>`;
}
function providersFormHTML(view: SettingsProvidersView, _models: SettingsProviderModel[] | null | undefined, id: string, editable = true): string {
  const enabled = view.enabled || [], dis = editable ? '' : ' disabled';
  const box = (row: SettingsProviderRow) => `<input type="checkbox" data-provider value="${esc(row.id)}" ${enabled.includes(row.id) ? 'checked' : ''}${dis}>`;
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
// The model selects are drawn with the providers form that holds them (`root`).
function providersFillModels(root: ParentNode, view: SettingsProvidersView, models: SettingsProviderModel[] | null | undefined, keep: string): void {
  // The first-run step ticks providers; Settings > Models offers what the company has on (its subscriptions tick them).
  const gateway = providersGatewayIds(view).filter(id => (view.enabled || []).includes(id));
  const boxes = root.querySelectorAll<HTMLInputElement>('[data-provider]');
  const enabled = boxes.length ? [...[...boxes].filter(input => input.checked).map(input => input.value), ...gateway] : view.enabled || [];
  const select = root.querySelector<HTMLSelectElement>('[data-provider-model]');
  if (!select) return;
  const options = providersModels(view, models, enabled);
  const meetingOptions = providersModels(view, models, enabled, true);
  const fallback = options.find(row => row.recommended) ?? options[0];
  const wanted = options.some(row => row.id === keep) ? keep : fallback?.id || '';
  select.innerHTML = options.length ? options.map(row =>
    `<option value="${esc(row.id)}" ${row.id === wanted ? 'selected' : ''}${row.description ? ` title="${esc(row.description)}"` : ''}>${esc(row.label)}</option>`).join('')
    : `<option value="">${boxes.length ? 'Tick a provider first' : 'No models yet'}</option>`;
  const meeting = root.querySelector<HTMLSelectElement>('[data-provider-meeting]');
  if (meeting) {
    // Live-meeting replies: the bot's own model, or one of the models above; the saved one stays selected.
    const keepMeeting = meeting.options.length ? meeting.value : (view.meeting_model || '');
    meeting.dataset.saved = view.meeting_model || '';
    meeting.innerHTML = `<option value="">Bot's own model</option>` + meetingOptions.map(row =>
      `<option value="${esc(row.id)}" ${row.id === keepMeeting ? 'selected' : ''}>${esc(row.label)}</option>`).join('');
  }
}
function providersWire(root: HTMLElement | null, view: SettingsProvidersView, models: SettingsProviderModel[] | null | undefined): void {
  if (!root) return;
  providersFillModels(root, view, models, view.default?.model || '');
  root.querySelectorAll<HTMLInputElement>('[data-provider]').forEach(input => {
    input.onchange = () => {
      const model = root.querySelector<HTMLSelectElement>('[data-provider-model]');
      if (!model) return;
      providersFillModels(root, view, models, model.value);
    };
  });
  root.querySelectorAll<HTMLInputElement>('[data-provider-preset]').forEach(input => {
    input.onchange = () => {
      if (root.id === 'onb-prov') root.querySelector<HTMLElement>('[data-provider-details]')!.hidden = input.value !== 'own-keys';
      const boxes = [...root.querySelectorAll<HTMLInputElement>('[data-provider]')];
      if (boxes.some(box => box.checked)) return;
      boxes.filter(box => (PRESET_PROVIDERS[input.value] || []).includes(box.value)).forEach(box => { box.checked = true; });
      const model = root.querySelector<HTMLSelectElement>('[data-provider-model]');
      if (!model) return;
      providersFillModels(root, view, models, model.value);
    };
  });
}
const providersCollect = (root: HTMLElement): SettingsProvidersChoice => {
  // Each lookup is optional: a form without a field (the first-run step has no meeting model) sends nothing for it.
  const fallback = root.querySelector<HTMLInputElement>('[data-provider-fallback]'), check = root.querySelector<HTMLInputElement>('[data-provider-check]');
  const preset = root.querySelector<HTMLInputElement>('[data-provider-preset]:checked');
  const meeting = root.querySelector<HTMLSelectElement>('[data-provider-meeting]');
  // The default model select is required: the form always draws it, and a save without it has nothing to send.
  const model = root.querySelector<HTMLSelectElement>('[data-provider-model]');
  if (!model) throw new Error('The models form did not draw its default model');
  return {enabled: [...root.querySelectorAll<HTMLInputElement>('[data-provider]:checked')].map(input => input.value),
    ...(preset ? {preset: preset.value} : {}),
    model: model.value, ...(fallback ? {auto_fallback: fallback.checked} : {}),
    // Sent only when changed, so a save about something else keeps an unset meeting model unset.
    ...(meeting && meeting.value !== meeting.dataset.saved ? {meeting_model: meeting.value} : {}),
    // Sent only when edited, so a save about something else never re-checks a saved entry.
    ...(check && check.value !== check.defaultValue ? {check_models: check.value.split(',').map(item => item.trim()).filter(Boolean)} : {})};
};
async function providersSave(view: SettingsProvidersView, chosen: SettingsProvidersChoice): Promise<SettingsProvidersView> {
  return put('/v2/providers', {enabled: chosen.enabled, model: chosen.model, expected_revision: view.revision || 0,
    ...('auto_fallback' in chosen ? {auto_fallback: chosen.auto_fallback} : {}),
    ...('check_models' in chosen ? {check_models: chosen.check_models} : {}),
    ...('preset' in chosen ? {preset: chosen.preset} : {}),
    ...('meeting_model' in chosen ? {meeting_model: chosen.meeting_model} : {})});
}
// ----------------------------------------------------------------- Settings > Models: Tico Gateway and Defaults
let SETTINGS_PROVIDERS_VIEW: SettingsProvidersView | null = null;
// The Gateway toggle is the models preset (backend/models_preset.py): on runs a computer with no subscription for a
// tool, or one whose plan ran out, on Tico Gateway; off waits. "Gateway only" and "own keys" keep their place.
const providersGatewayOn = (view: SettingsProvidersView | null | undefined): boolean => ['subscriptions-gateway', 'gateway'].includes(view?.preset || 'subscriptions-gateway');
const providersPresetFor = (view: SettingsProvidersView, on: boolean): string => on ? (view.preset === 'gateway' ? 'gateway' : 'subscriptions-gateway')
  : (view.preset === 'own-keys' ? 'own-keys' : 'subscriptions');
// Read the providers view once for the Computers tab too: its pickers say "Gateway" or "None" by the toggle.
async function providersViewLoad(): Promise<SettingsProvidersView> {
  const view = await get<SettingsProvidersView>('/v2/providers', {fresh: true});
  SETTINGS_PROVIDERS_VIEW = view;
  return view;
}
// A label with at most one (?) bubble, then its control.
const providersRow = (id: string, label: string, control: string, help = ''): string =>
  `<div class="models-row"><span class="k"><label for="${id}">${esc(label)}</label>${help ? helpBubble(help) : ''}</span>${control}</div>`;
function providersDefaultsHTML(view: SettingsProvidersView, owner: boolean): string {
  const dis = owner ? '' : ' disabled';
  return `<form id="set-prov" class="models-form" data-providers-form>
    ${providersRow('set-prov-model', 'Default model', `<select id="set-prov-model" class="settings-inline-select" data-provider-model${dis}></select>`)}
    ${providersRow('set-prov-meeting', 'Meeting model', `<select id="set-prov-meeting" class="settings-inline-select" data-provider-meeting${dis}></select>`)}
    ${providersRow('set-prov-fallback', 'Fall back when a plan runs out', `<input id="set-prov-fallback" type="checkbox" class="people-switch" data-provider-fallback${view.auto_fallback !== false ? ' checked' : ''}${dis}>`,
      'A bot with no fallback of its own runs on another AI tool signed in on its computer.')}
    ${providersRow('set-prov-check', 'Check model', `<input id="set-prov-check" type="text" data-provider-check value="${esc((view.check_models || []).join(', '))}"
      placeholder="${esc((view.check_order || []).map(row => row.runtime).join(', ') || 'grok, codex')}"${dis}>`,
      "Reviews and checks prefer the first of these from a provider other than the author's.")}
    ${owner ? '<div class="onb-actions"><button class="primary" type="submit" id="set-prov-save">Save</button><span class="muted" id="set-prov-status" role="status"></span></div>' : settingsWhoCanHTML('models')}
  </form>`;
}
// One line: whether the Gateway is connected, this month's spend against its cap, and the switch. Below it what
// GET /v2/gateway adds for owners and admins (settings-gateway.ts), the key and address behind Advanced (read-only for
// anyone but the owner), and Webhooks: incoming webhooks' Gateway addresses, drawn when opened (settings-webhooks.ts).
function providersGatewayCardHTML(view: SettingsProvidersView, gateway: SettingsGatewayView | null, owner: boolean, overview: unknown): string {
  const [tone, line] = gateway ? gatewayStatusLine(gateway) : ['', ''];
  return `<div class="models-gw models-gw-head">
      ${gateway ? `<p class="models-gw-line" data-gateway-state="${esc(gateway.state || (gateway.connected ? 'connected' : 'none'))}"><span class="dot ${tone === 'ok' ? 'ok' : 'failed'}" aria-hidden="true"></span><span data-gateway-line>${esc(line)}</span>${gatewaySpendBar(gateway)}</p>` : ''}
      <label class="models-gw-toggle"><input type="checkbox" class="people-switch" data-gateway-toggle${providersGatewayOn(view) ? ' checked' : ''}${owner ? '' : ' disabled'}
        aria-label="Use Tico Gateway until a computer is signed in"><span>Use until a computer is signed in</span></label>
      ${helpBubble("Used while a computer has no subscription and no login of its own. When a computer's plans run out, its own Fall back to Tico Gateway decides.")}</div>
    ${gatewayOverviewHTML(overview)}
    ${gateway ? `<details class="models-adv" data-gateway-advanced><summary>Advanced</summary>${gatewayHTML(gateway, owner)}</details>` : ''}
    <details class="models-adv" data-gateway-webhooks><summary>Webhooks</summary><div id="wh-gateway"></div></details>`;
}
// The Gateway toggle saves the models preset at once, with the providers and model as they are.
function providersToggleWire(): void {
  const card = document.querySelector<HTMLElement>('#set-gateway-card'), toggle = card?.querySelector<HTMLInputElement>('[data-gateway-toggle]');
  if (!toggle) return;
  toggle.onchange = async () => {
    const view: SettingsProvidersView = SETTINGS_PROVIDERS_VIEW || {};   // the latest record, so a Defaults save in between keeps its revision
    toggle.disabled = true;
    try {
      const saved: SettingsProvidersView = await put('/v2/providers', {enabled: view.enabled || [], model: view.default?.model || '', expected_revision: view.revision || 0,
        preset: providersPresetFor(view, toggle.checked)});
      SETTINGS_PROVIDERS_VIEW = saved;
      toast(toggle.checked ? 'Gateway on' : 'Gateway off');
    } catch (error) { toast(errorMessage(error, ''), true); toggle.checked = !toggle.checked; }
    await renderSettingsProviders(true);
  };
}
// A poll leaves a card alone while someone types in it; `gatewaySaved` redraws the Gateway card after its own save.
async function renderSettingsProviders(gatewaySaved = false): Promise<void> {
  const el = document.querySelector<HTMLElement>('#set-providers'), card = document.querySelector<HTMLElement>('#set-gateway-card');
  if (!el || !card) return;
  const drawCard = gatewaySaved || !formBusy(card), drawDefaults = !formBusy(el);
  if (!drawCard && !drawDefaults) return;
  const owner = S.me?.role === 'owner';
  try {
    // Prices, picks and services were Settings > Gateway's, an admin's page: owners and admins read them here.
    const [view, gateway, overview] = await Promise.all([providersViewLoad(), get<SettingsGatewayView>('/v2/providers/gateway').catch(() => null),
      settingsIsAdmin() ? get<unknown>('/v2/gateway', {fresh: true}).catch(() => null) : null]);
    if (!el.isConnected) return;
    if (drawCard) {
      const folds = ['advanced', 'services-row', 'webhooks'], open = folds.filter(k => card.querySelector<HTMLDetailsElement>(`[data-gateway-${k}]`)?.open);
      card.innerHTML = providersGatewayCardHTML(view, gateway, owner, overview);
      // The webhooks fold is drawn just above.
      const hooks = card.querySelector<HTMLDetailsElement>('[data-gateway-webhooks]')!;
      hooks.addEventListener('toggle', () => { if (hooks.open) void whMount('gateway'); });
      if (WH_OPEN_GATEWAY) { WH_OPEN_GATEWAY = false; open.push('webhooks'); }   // a link to the Webhooks fold (settingsShow)
      for (const k of open) { const fold = card.querySelector<HTMLDetailsElement>(`[data-gateway-${k}]`); if (fold) fold.open = true; }
      // The Gateway card sits in a settings section, so the section is always there.
      card.closest<HTMLElement>('section')!.dataset.drawn = '';
      if (gateway) gatewayWire(gateway);
    }
    settingsReveal('#settings-models'); settingsReveal(SETTINGS_GATEWAY_CARD);
    if (drawCard) providersToggleWire();
    if (!drawDefaults) return;
    el.innerHTML = providersDefaultsHTML(view, owner);
    // The defaults form is drawn just above, and only an owner's form has the Save button and its status line.
    const form = document.querySelector<HTMLFormElement>('#set-prov')!;
    providersWire(form, view, SETTINGS_DATA.models);
    if (owner) form.onsubmit = async event => {
      event.preventDefault();
      const save = document.querySelector<HTMLButtonElement>('#set-prov-save')!, status = document.querySelector<HTMLElement>('#set-prov-status')!;
      // No preset here (the Gateway toggle is its own save), and the providers stay as the company has them.
      const chosen = {...providersCollect(form), enabled: view.enabled || []};
      delete chosen.preset;
      const sent = [...form.querySelectorAll<HTMLInputElement>('input')].map((input): [HTMLInputElement, string] => [input, input.value]);
      save.disabled = true; status.textContent = 'Saving…';
      try {
        const saved = await providersSave(view, chosen);
        applyConfig({...S.config, providers_configured: !!saved.enabled?.length, default_runtime: saved.default?.runtime || ''});
        // SETTINGS_DATA is a plain object literal in settings.js, so its two keys are set through Object.assign.
        Object.assign(SETTINGS_DATA, {enabledProviders: saved.enabled, defaultModel: saved.default?.model || ''});
        // What was sent is now saved; anything typed while it was sending stays unsaved, and the redraw waits for it.
        for (const [input, value] of sent) input.defaultValue = value;
        Object.assign(view, saved); SETTINGS_PROVIDERS_VIEW = view;
        save.disabled = false; status.textContent = '';
        toast('Saved');
        await renderSettingsProviders();
      } catch (error) { status.innerHTML = `<span class="err">${esc(errorMessage(error, ''))}</span>`; save.disabled = false; }
    };
  } catch (error) { el.innerHTML = `<div class="err">${esc(errorMessage(error, ''))}</div>`; }
}

// ----------------------------------------------------------------- Tico AI Gateway: key and address
// One key (and the Gateway's URL) for everything the app asks Tico's Gateway: Decide and the Tico models. The key is
// typed once and never shown again: the server keeps it sealed and answers with its first and last three characters.
function gatewayHTML(gateway: SettingsGatewayView, owner: boolean): string {
  if (!owner) return '';
  const state = gateway.state || (gateway.connected ? 'connected' : 'none');
  const message = gateway.message ? `<p class="${gateway.working === false || state === 'cap_reached' ? 'err' : 'muted'}" data-gateway-message>${esc(gateway.message)}</p>` : '';
  // An install the install command invited gets its own key from Tico: nothing to paste, and a pasted key still wins.
  const automatic = gateway.automatic ? '<p data-gateway-automatic>Tico AI Gateway: connected automatically</p>'
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
function gatewayWire(gateway: SettingsGatewayView): void {
  const root = document.querySelector<HTMLElement>('#set-gateway'); if (!root) return;
  const status = root.querySelector<HTMLElement>('[data-gateway-status]');
  const run = async (button: HTMLButtonElement, busy: string, work: () => Promise<SettingsGatewayAnswer>, done: (shown: SettingsGatewayAnswer) => string | undefined): Promise<void> => {
    button.disabled = true; if (status) status.textContent = busy;
    try { const shown = await work(); toast(done(shown) ?? '', shown?.working === false); await loadSettings(); await renderSettingsProviders(true); }
    catch (error) { if (status) status.innerHTML = `<span class="err">${esc(errorMessage(error, ''))}</span>`; else toast(errorMessage(error, ''), true); button.disabled = false; }
  };
  const save = root.querySelector<HTMLButtonElement>('[data-gateway-save]');
  if (save) save.onclick = () => {
    // The key and address fields are drawn with the save button.
    const key = root.querySelector<HTMLInputElement>('[data-gateway-key]')!, url = root.querySelector<HTMLInputElement>('[data-gateway-url]')!;
    // The key leaves the field as soon as it is sent: nothing on the page holds it after a save.
    const body = {url: url.value.trim(), expected_revision: gateway.revision || 0, ...(key.value.trim() ? {key: key.value.trim()} : {})};
    key.value = '';
    // A new address for another service while a key is saved asks first: keep the key, or clear it and paste one again.
    void run(save, 'Saving…', () => settingsDestinationSave((extra: Record<string, unknown>) => put('/v2/providers/gateway', {...body, ...extra})),
      saved => saved.credentials_choice === 'clear' ? 'Gateway address saved and its key cleared: paste a key for the new address'
        : saved.working === false ? saved.message : 'Tico AI Gateway saved');
  };
  const check = root.querySelector<HTMLButtonElement>('[data-gateway-check]');
  if (check) check.onclick = () => void run(check, 'Checking…', () => post('/v2/providers/gateway/check', {}), shown => shown.message || 'Checked');
  const automaticOn = root.querySelector<HTMLButtonElement>('[data-gateway-automatic-on]');
  if (automaticOn) automaticOn.onclick = () => void run(automaticOn, 'Connecting…', () => post('/v2/providers/gateway/automatic', {}), () => 'Connecting automatically');
  const clear = root.querySelector<HTMLButtonElement>('[data-gateway-clear]');
  if (clear) clear.onclick = async () => {
    if (!await settingsConfirm({title: 'Clear the gateway key?', text: 'Bots on Tico models stop until a key is saved again.', ok: 'Clear key'})) return;
    void run(clear, 'Clearing…', () => writeRequest('DELETE', '/v2/providers/gateway', undefined), () => 'Gateway key cleared');
  };
}
