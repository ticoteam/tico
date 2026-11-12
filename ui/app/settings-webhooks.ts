/* ui/app/settings-webhooks.ts — webhooks, in three homes: incoming on Automations (#/automations/webhooks[/<id>], a
   built-in row of the list), outgoing on Tools (#/tools/webhooks[/<id>]), through Tico Gateway as a fold of the Tico
   Gateway card on Settings > Models. Settings > Webhooks was all three; its addresses redirect (whOldAddress).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Incoming (backend/triggers.py): an address a sender calls; automations with a webhook trigger listen to it. Outgoing (backend/webhooks.py): endpoints Tico posts its events to. Through Tico Gateway
// (backend/hook_relay.py): a second address for an incoming webhook, held by Tico Gateway, for a server the internet
// cannot reach. A secret is shown once, right after it is made, and is gone from the next screen.
type WhTab = 'incoming' | 'outgoing' | 'gateway';
type WhEventType = import('../types/api').EventType;
type WhWake = import('../types/api').WebhookWakeUrl;
type WhFilterKey = 'departments' | 'bots' | 'pipelines';
type WhFilter = Partial<Record<WhFilterKey, string[]>>;
type WhChoices = Record<WhFilterKey, [string, string][]>;
/** A custom header as an endpoint holds it (backend/webhooks.py): a secret's value never comes back, `set` says it has one. */
interface WhHeader { name?: string; value?: string | null; secret?: boolean; set?: boolean; needs_value?: boolean }
/** A header as the form sends it (backend/webhooks.py HeaderIn): a secret left empty keeps its value. */
interface WhHeaderIn { name: string; value?: string; secret: boolean }
/** An outgoing endpoint (GET /v2/webhooks), with its filter and headers as the page reads them. */
type WhHook = Omit<import('../types/api').Webhook, 'filter' | 'headers'> & { filter?: WhFilter | null; headers?: WhHeader[] };
/** A made or rotated endpoint: its signing secret is in this answer only, unless the write was a replay. */
interface WhHookMade { id: string; secret?: string; replayed?: boolean }
/** A made, rotated or signed incoming webhook: its address or secret is in this answer only, unless it was a replay. */
interface WhTriggerMade { id: string; url?: string; signing_secret?: string; replayed?: boolean }
/** One call to an incoming webhook (GET /v2/triggers/<id>/deliveries). */
interface WhCall {
  delivery: string; outcome: string; reason?: string | null; stored?: boolean; received_at: string; type?: string | null;
  tasks: number; bytes?: number; via?: string | null;
}
/** One kept call with its payload (GET /v2/triggers/<id>/deliveries/<delivery>). */
interface WhPayload {
  received_at: string; type?: string | null; bytes?: number; expired?: boolean; body?: string | null; headers?: Record<string, string> | null;
}
/** One send to an endpoint (GET /v2/webhooks/<id>/sends). */
interface WhSend {
  id: string; created_at: string; type: string; state: string; subject?: string | null; attempts: number;
  last_status?: number | string | null; last_error?: string | null;
}
/** What Retry failed or Send missed answers: a replay round that stopped early says where. */
interface WhCatchup { recovered?: number; queued?: number; more?: boolean; next_since?: string | null; next_after?: string | null }
/** What Recover refused calls answers. */
interface WhRecover { recovered: number; found: number; duplicate?: number; still_refused?: number; more?: boolean }
/** The secrets on screen for one incoming webhook, by the key its Copy button names. */
interface WhShown { id: string; url?: string | undefined; signing?: string | undefined; relay?: string | undefined; [key: string]: string | undefined }
interface WhState {
  tab: WhTab; trigger: string; hook: string; shown: WhShown | null; hookShown: { id: string; secret: string | undefined } | null; wakeSecret: string;
}
interface WhUrlControls extends HTMLFormControlsCollection { readonly url: HTMLInputElement }
interface WhHookControls extends WhUrlControls { readonly name: HTMLInputElement }
interface WhTriggerControls extends HTMLFormControlsCollection {
  readonly [index: number]: HTMLElement;
  /** Only the New webhook form has these two. */
  readonly preset?: HTMLSelectElement;
  readonly type_from?: HTMLInputElement;
}
interface WhUrlForm extends HTMLFormElement { readonly elements: WhUrlControls }
interface WhHookForm extends HTMLFormElement { readonly elements: WhHookControls }
interface WhTriggerForm extends HTMLFormElement { readonly elements: WhTriggerControls }
/** POST /v2/webhooks: an endpoint's fields, with a filter and headers only when set. */
interface WhHookBody { name: string; url: string; events: string[]; allow_http: boolean; filter?: Record<string, string[]>; headers?: WhHeaderIn[] }

const WH_PRESETS: [string, string][] = [['none', 'None'], ['hmac', 'HMAC'], ['github', 'GitHub'], ['standard', 'Standard Webhooks'], ['stripe', 'Stripe'], ['slack', 'Slack']];
// Where a preset reads the event type (backend/trigger_presets.py TYPE_FROM): the API has no list of presets to read.
const WH_TYPE_FROM: Record<string, string> = {github: 'X-GitHub-Event', stripe: '/type', standard: '/type', slack: '/event/type|/type'};
const WH_UNHEARD: [string, string][] = [['log', 'Log it'], ['note', 'Tell the maker']];
const WH_OUTCOMES: Record<string, [string, string]> = {accepted: ['ok', 'Accepted'], refused: ['fail', 'Refused'], rate_limited: ['fail', 'Over limit'], replayed: ['', 'Rerun']};
const whRefused = (r: { outcome: string }) => r.outcome === 'refused' || r.outcome === 'rate_limited';
const WH_SEND_STATES: Record<string, string> = {delivered: 'ok', failed: 'fail', ready: 'waiting', sending: 'waiting', canceled: '', withheld: ''};
// Each part's host element, and the page address an open item rides on (none for the Gateway fold).
const WH_HOSTS: Record<WhTab, string> = {incoming: '#wh-incoming', outgoing: '#wh-outgoing', gateway: '#wh-gateway'};
const WH_HOOKS_ROUTE = '#/tools/webhooks', WH_INCOMING_ROUTE = '#/automations/webhooks';
const WH_ROUTES: Partial<Record<WhTab, string>> = {incoming: WH_INCOMING_ROUTE, outgoing: WH_HOOKS_ROUTE};
// A link to the Gateway part (#/settings/models/wh-gateway) opens the fold, or does once the card is drawn
// (settingsShow, renderSettingsProviders).
let WH_OPEN_GATEWAY = false;
// The open part and item, and the one set of secrets on screen: {id, url?, signing?, relay?} for an incoming webhook,
// {id, secret} for an endpoint. The wake address's signing secret stays until the person dismisses it.
const WH: WhState = {tab: 'incoming', trigger: '', hook: '', shown: null, hookShown: null, wakeSecret: ''};
let WH_RUN = 0, WH_HOOKS: WhHook[] | null = null;
// The incoming webhooks (GET /v2/triggers), loaded when a part opens; other files read it through webhookTriggers().
let WH_TRIGGERS: WebhookTrigger[] | null = null;
// The event catalog (backend/event_catalog.py): the endpoint picker reads it.
let EVENT_TYPES: WhEventType[] = [], EVENT_TYPES_P: Promise<WhEventType[]> | null = null;
function eventTypesLoad(): Promise<WhEventType[]> {
  EVENT_TYPES_P ||= get<import('../types/api').EventTypeList>('/v2/event-types').then(d => (EVENT_TYPES = d.items || [])).catch(e => { EVENT_TYPES_P = null; throw e; });
  return EVENT_TYPES_P;
}
const catalogWords = (name: string) => (EVENT_TYPES.find(t => t.name === name || (t.aliases || []).includes(name))?.description || '').replace(/\.$/, '');
async function whHooksLoad(): Promise<WhHook[]> {
  try { WH_HOOKS = (await get<{ items?: WhHook[] }>('/v2/webhooks?limit=200')).items || []; } catch { WH_HOOKS = WH_HOOKS || []; }
  return WH_HOOKS;
}
const whPresetName = (p: string) => (WH_PRESETS.find(([k]) => k === p) || [p, p])[1];
// A local day `offset` days from today, as an <input type=date> value.
const whDay = (offset: number) => { const d = new Date(Date.now() + offset * 864e5); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
const whPlural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;
// Who manages an incoming webhook (backend/triggers.py `manages`): the owner, an admin, or whoever made it.
const whMine = (t: WebhookTrigger) => !t.builtin && (settingsIsAdmin() || t.created_by === 'human:' + (S.me?.id || ''));
const whOpt = (v: string, label: string, cur: string) => `<option value="${esc(v)}"${String(v) === String(cur) ? ' selected' : ''}>${esc(label)}</option>`;

// Open a part in its host, on an item (a webhook's id) or its list; the same part and item keep what is shown.
function whMount(tab: WhTab, item = '') {
  const cur = tab === 'incoming' ? WH.trigger : tab === 'outgoing' ? WH.hook : '';
  if (WH.tab !== tab || cur !== item) Object.assign(WH, {tab, trigger: tab === 'incoming' ? item : '', hook: tab === 'outgoing' ? item : '', shown: null, hookShown: null});
  return whRender();
}
// An old Settings > Webhooks address (#/triggers, #/settings/webhooks[/<part>]) at the part's new home.
function whOldAddress(route: string) {
  const part = String(route).split('?')[0]!.split('/')[3];
  if (part === 'outgoing') return WH_HOOKS_ROUTE;
  if (part === 'gateway') return SETTINGS + '/models/wh-gateway';
  return WH_INCOMING_ROUTE;
}
async function whRender() {
  const el = $(WH_HOSTS[WH.tab]); if (!el) return;
  // The page's address names the open item, so it can be shared; it replaces the entry (no new history step).
  const base = WH_ROUTES[WH.tab];
  if (base && (S.route === base || S.route.startsWith(base + '/'))) {
    const item = WH.tab === 'incoming' ? WH.trigger : WH.hook, to = base + (item ? '/' + encodeURIComponent(item) : '');
    if (location.hash !== to) { history.replaceState(history.state, '', to); S.route = LAST_ROUTE = to; }
  }
  el.onclick = whClick;
  const run = ++WH_RUN;
  try {
    if (WH.tab === 'outgoing') await (WH.hook ? whHookDetail(el, run) : whHooks(el, run));
    else if (WH.tab === 'gateway') await whGateway(el, run);
    else await (WH.trigger ? whTriggerDetail(el, run) : whTriggers(el, run));
  } catch (e) { if (run === WH_RUN) el.innerHTML = `<div class="err">${esc(errorMessage(e, ''))}</div>`; }
}
function whSecretHTML(label: string, value: string | undefined, key: string) {
  return `<div class="trigger-secret" role="status" data-wh-secret="${key}"><p><strong>${esc(label)}</strong> <span class="muted">Shown once</span></p>
    <div class="row"><code class="trigger-url">${esc(value)}</code><button class="ghost" type="button" data-wh-copy="${key}">Copy</button></div></div>`;
}
function whShownHTML(id: string) {
  const s = WH.shown;
  if (!s || s.id !== id) return '';
  return (s.url ? whSecretHTML('Address', s.url, 'url') : '') + (s.signing ? whSecretHTML('Signing secret', s.signing, 'signing') : '')
    + (s.relay ? whSecretHTML('Gateway address', s.relay, 'relay') : '');
}
function whHeadHTML(title: string, pills: string, actions: string) {
  return `<div class="wh-head"><button class="ghost" type="button" data-wh-back aria-label="Back">‹ Back</button>
    <h3>${esc(title)}</h3>${pills}<span class="spacer"></span><div class="wh-actions">${actions}</div></div>`;
}
async function whClick(ev: MouseEvent) {
  const target = eventElement(ev), copy = target?.closest<HTMLElement>('[data-wh-copy]');
  if (copy) {
    const key = copy.dataset.whCopy!, value = key === 'secret' ? WH.hookShown?.secret : key === 'wake' ? WH.wakeSecret : WH.shown?.[key];
    if (value) { await copyText(value); toast('Copied'); }
    return;
  }
  if (target?.closest('[data-wh-back]')) {
    Object.assign(WH, {trigger: '', hook: '', shown: null, hookShown: null});
    void whRender();
  }
}
// A dialog in the house form style; Cancel and Escape close it, and closing removes it.
function whDialog(title: string, body: string, footer = ''): HTMLDialogElement {
  const dialog = document.createElement('dialog');
  dialog.className = 'tmodal task-rule-dialog wh-dialog';
  dialog.setAttribute('aria-label', title);
  dialog.innerHTML = `<form class="task-rule-form" novalidate><header><h2>${esc(title)}</h2><button type="button" class="ghost" data-close>Cancel</button></header>
    ${body}<p class="err" role="status" data-wh-error></p>${footer ? `<footer>${footer}</footer>` : ''}</form>`;
  dialog.addEventListener('close', () => dialog.remove());
  dialog.querySelectorAll<HTMLElement>('[data-close]').forEach(b => { b.onclick = () => dialog.close(); });
  document.body.appendChild(dialog); dialog.showModal();
  return dialog;
}
// A form's submit with its button held and any refusal shown in the dialog.
function whSubmit(dialog: HTMLDialogElement, fn: (data: FormData, form: HTMLFormElement) => unknown) {
  const form = dialog.querySelector('form')!, error = dialog.querySelector('[data-wh-error]')!;
  form.onsubmit = async ev => {
    ev.preventDefault();
    if (!form.reportValidity()) return;
    const button = form.querySelector<HTMLButtonElement>('[type="submit"]'); if (button) button.disabled = true; error.textContent = '';
    try { await fn(new FormData(form), form); } catch (e) { error.textContent = errorMessage(e, 'Try again'); } finally { if (button) button.disabled = false; }
  };
}

// ---------------------------------------------------------------------------------------------------------- Incoming
// Finance's own triggers (consumer finance: Mercury, Stripe, QuickBooks, Xero) are managed in Finance › Sources, not here.
const WH_FINANCE_PRESETS = ['mercury', 'stripe_finance', 'intuit', 'xero'];
const whIncoming = (list: WebhookTrigger[]) => list.filter(t => t.consumer ? t.consumer !== 'finance' : !WH_FINANCE_PRESETS.includes(t.preset));
async function whTriggersFresh(): Promise<WebhookTrigger[]> {
  WH_TRIGGERS = whIncoming((await get<{ triggers?: WebhookTrigger[] }>('/v2/triggers', {fresh: true})).triggers || []);
  return WH_TRIGGERS;
}
function whCountsHTML(t: WebhookTrigger) {
  return `<span class="tnum">${t.received_24h} in 24h</span>${t.refused_24h ? ` · <span class="wh-bad tnum">${t.refused_24h} refused</span>` : ''}`;
}
function whTriggerPills(t: WebhookTrigger) {
  return (t.builtin ? '<span class="pill">Built-in</span>' : '') + (t.preset && t.preset !== 'none' ? `<span class="pill">${esc(whPresetName(t.preset))}</span>` : '')
    + (t.relay ? '<span class="pill">Gateway</span>' : '');
}
async function whTriggers(el: HTMLElement, run: number) {
  const list = await whTriggersFresh();
  if (run !== WH_RUN) return;
  el.innerHTML = `<div class="settings-bots-filters"><button type="button" class="primary" data-wh-new>New webhook</button></div>
    ${list.length ? `<div class="wh-list" data-wh-triggers>${list.map(t => `<button type="button" class="wh-row" data-wh-open="${esc(t.id)}">
        <span class="wh-name"><strong>${esc(t.name)}</strong>${whTriggerPills(t)}</span>
        <span class="wh-meta">${whCountsHTML(t)}</span>
        <span class="wh-meta muted">${t.last_received ? esc(ago(t.last_received)) : 'Never called'}</span></button>`).join('')}</div>`
      : '<div class="empty">No webhooks yet.</div>'}`;
  el.querySelector<HTMLElement>('[data-wh-new]')!.onclick = () => whTriggerDialog(null);
  el.querySelectorAll<HTMLElement>('[data-wh-open]').forEach(b => { b.onclick = () => { WH.trigger = b.dataset.whOpen!; WH.shown = null; void whRender(); }; });
}
async function whTriggerDetail(el: HTMLElement, run: number) {
  const id = WH.trigger, path = `/v2/triggers/${encodeURIComponent(id)}`;
  const [list, log] = await Promise.all([whTriggersFresh(), get<{ deliveries?: WhCall[] }>(path + '/deliveries', {fresh: true})]);
  if (run !== WH_RUN) return;
  const t = list.find(x => x.id === id);
  if (!t) { WH.trigger = ''; return whTriggers(el, run); }
  const mine = whMine(t), may = mine || (t.builtin && S.me?.role === 'owner');
  const rows = log.deliveries || [];
  const actions = mine ? `<button class="ghost" type="button" data-wh-edit>Edit</button><button class="ghost" type="button" data-wh-rotate>New secret</button>
      <button class="ghost" type="button" data-wh-sign>Signing</button><button class="ghost" type="button" data-wh-delete>Delete</button>`
    : may ? '<button class="ghost" type="button" data-wh-edit>Edit</button>' : '';
  el.innerHTML = `${whHeadHTML(t.name, whTriggerPills(t), actions)}
    ${whShownHTML(id)}
    <dl class="wh-facts">
      ${t.builtin ? '' : `<dt>Address</dt><dd><code>${esc(t.url)}/…</code></dd>
      <dt>Gateway</dt><dd>${t.relay ? `<code>${esc(t.relay.address_prefix)}/…</code>` : '<span class="muted">None</span>'}</dd>
      <dt>Signing</dt><dd>${esc(whPresetName(t.preset || 'none'))}</dd>
      <dt>Type from</dt><dd>${t.type_from ? `<code>${esc(t.type_from)}</code>` : '<span class="muted">None</span>'}</dd>
      <dt>Per hour</dt><dd class="tnum">${t.hourly}</dd>`}
      <dt>Keep</dt><dd>${whPlural(t.keep_days, 'day')}</dd>
      ${t.builtin ? '' : `<dt>If nothing listens</dt><dd>${esc((WH_UNHEARD.find(([k]) => k === t.unheard) || ['', 'Log it'])[1])}</dd>`}
      <dt>24h</dt><dd>${whCountsHTML(t)}</dd>
    </dl>
    <h4 class="wr-sub">Listeners</h4>
    <div class="wh-listeners" data-wh-listeners><a href="${AUTOMATIONS}?q=${encodeURIComponent(t.name)}" data-wh-automations>Automations</a></div>
    <h4 class="wr-sub">Recent calls</h4>
    ${mine && rows.some(r => whRefused(r) && r.stored && !/\(recovered\)$/.test(r.reason || '')) ? `<div class="row wh-li-add"><button class="ghost" type="button" data-wh-recover
      title="Check the kept refused calls again with the current secret and preset">Recover refused calls</button></div>` : ''}
    ${rows.length ? `<div class="wh-calls" data-wh-calls>${rows.map(r => {
      const [tone, word] = WH_OUTCOMES[r.outcome] || ['', r.outcome];
      const what = whRefused(r) ? esc(r.reason) : whPlural(r.tasks, 'task');
      return `<div class="wh-call" data-wh-call="${esc(r.delivery)}">
        <span class="wh-time" title="${esc(r.received_at)}">${esc(ago(r.received_at))}</span>
        <span class="wh-type">${r.type ? `<code>${esc(r.type)}</code>` : '<span class="muted">No type</span>'}</span>
        <span class="wh-outcome"><span class="pill ${tone}">${esc(word)}</span> <span class="muted">${what}</span></span>
        <span class="tnum muted">${esc(bytes(r.bytes || 0))}</span>
        <span class="muted">${esc(r.via === 'gateway' ? 'Gateway' : r.via === 'direct' ? 'Direct' : r.via || '')}</span>
        <span class="wh-call-ctl">${r.stored && may ? `<button class="ghost" type="button" data-wh-payload>View payload</button>` : ''}
          ${r.stored && mine && !whRefused(r) ? '<button class="ghost" type="button" data-wh-replay>Run again</button>' : ''}</span></div>`;
    }).join('')}</div>` : '<div class="empty">No calls yet.</div>'}`;
  const on = (sel: string, fn: (ev: MouseEvent) => unknown) => { const b = el.querySelector<HTMLElement>(sel); if (b) b.onclick = fn; };
  on('[data-wh-edit]', () => whTriggerDialog(t));
  on('[data-wh-sign]', () => whSigningDialog(t));
  on('[data-wh-rotate]', () => void whRotate(t));
  on('[data-wh-delete]', () => void whTriggerDelete(t));
  on('[data-wh-recover]', async ev => {
    const b = ev.currentTarget; if (!(b instanceof HTMLButtonElement)) return;
    b.disabled = true;
    try {
      const r: WhRecover = (await post(path + '/recover', {since_hours: Math.min(720, Math.max(1, (t.keep_days || 7) * 24))})).recover;
      toast([`Recovered ${r.recovered} of ${r.found}`, r.duplicate && `${r.duplicate} already in`, r.still_refused && `${r.still_refused} still refused`,
        r.more && 'more wait'].filter(Boolean).join(' · '), !r.recovered && r.found > 0);
      void whRender();
    } catch (e) { toast(errorMessage(e, ''), true); b.disabled = false; }
  });
  el.querySelectorAll<HTMLElement>('[data-wh-call]').forEach(row => {
    const delivery = row.dataset.whCall!;
    const view = row.querySelector<HTMLElement>('[data-wh-payload]'), again = row.querySelector<HTMLButtonElement>('[data-wh-replay]');
    if (view) view.onclick = () => void whPayload(id, delivery);
    if (again) again.onclick = async () => {
      again.disabled = true;
      try {
        const r: { tasks: number } = (await post(`${path}/deliveries/${encodeURIComponent(delivery)}/replay`, {})).replay;
        toast(`Ran again: ${whPlural(r.tasks, 'task')}`);
        void whRender();
      } catch (e) { toast(errorMessage(e, ''), true); again.disabled = false; }
    };
  });
}
async function whPayload(id: string, delivery: string) {
  let d: WhPayload;
  try { d = (await get<{ delivery: WhPayload }>(`/v2/triggers/${encodeURIComponent(id)}/deliveries/${encodeURIComponent(delivery)}`, {fresh: true})).delivery; }
  catch (e) { toast(errorMessage(e, ''), true); return; }
  let body = d.body ?? '';
  try { body = JSON.stringify(JSON.parse(body), null, 2); } catch { /* not JSON: shown as sent */ }
  const headers = Object.entries(d.headers || {}).map(([k, v]) => `${k}: ${v}`).join('\n');
  whDialog('Payload', `<p class="muted wh-payload-meta">${esc(ago(d.received_at))}${d.type ? ` · <code>${esc(d.type)}</code>` : ''} · ${esc(bytes(d.bytes || 0))}</p>
    ${d.expired ? '<p class="muted">No longer kept.</p>' : `<h4 class="wr-sub">Headers</h4><pre class="wh-pre" data-wh-headers>${esc(headers)}</pre>
    <h4 class="wr-sub">Body</h4><pre class="wh-pre" data-wh-body>${esc(body)}</pre>`}`);
}
function whTriggerDialog(t: WebhookTrigger | null) {
  const builtin = !!t?.builtin;
  const fields = builtin ? `<label>Keep days<input name="keep_days" type="number" min="1" max="30" required value="${esc(t?.keep_days)}"></label>`
    : `<label>Name<input name="name" maxlength="100" required autocomplete="off" value="${esc(t?.name || '')}"></label>
    ${t ? '' : `<label>Signing<select name="preset" data-wh-preset>${WH_PRESETS.map(([k, v]) => whOpt(k, v, 'none')).join('')}</select></label>
    <label data-wh-secret-field hidden>Signing secret<input name="secret" maxlength="300" autocomplete="off" spellcheck="false" placeholder="Leave empty to make one"></label>`}
    <label>Type from<input name="type_from" maxlength="120" autocomplete="off" spellcheck="false" value="${esc(t?.type_from || '')}" placeholder="Header or /json/path"></label>
    <label>Per hour<input name="hourly" type="number" min="1" max="600" required value="${esc(t?.hourly || 60)}"></label>
    <label>Keep days<input name="keep_days" type="number" min="1" max="30" required value="${esc(t?.keep_days || 7)}"></label>
    <label>If nothing listens<select name="unheard">${WH_UNHEARD.map(([k, v]) => whOpt(k, v, t?.unheard || 'log')).join('')}</select></label>`;
  const dialog = whDialog(t ? (builtin ? t.name : 'Edit webhook') : 'New webhook', fields, `<button type="submit" class="primary">${t ? 'Save' : 'Create'}</button>`);
  const form = dialog.querySelector<WhTriggerForm>('form')!, preset = form.elements.preset;
  if (preset) preset.onchange = () => {
    dialog.querySelector<HTMLElement>('[data-wh-secret-field]')!.hidden = preset.value === 'none';
    const typeFrom = form.elements.type_from!, was = typeFrom.value;
    if (!was || Object.values(WH_TYPE_FROM).includes(was)) typeFrom.value = WH_TYPE_FROM[preset.value] || '';
  };
  whSubmit(dialog, async v => {
    const num = (k: string) => Number(v.get(k));
    if (builtin) {
      await post(`/v2/triggers/${encodeURIComponent(t!.id)}`, {keep_days: num('keep_days')});
      dialog.close(); void whRender(); return;
    }
    const settings = {type_from: String(v.get('type_from') || '').trim(), hourly: num('hourly'), keep_days: num('keep_days'), unheard: String(v.get('unheard'))};
    if (t) {
      await post(`/v2/triggers/${encodeURIComponent(t.id)}`, {name: String(v.get('name')).trim(), ...settings});
      dialog.close(); void whRender(); return;
    }
    // Making one is three calls (create, signing, settings): the create answer carries only the address.
    const made: WhTriggerMade = (await post('/v2/triggers', {name: String(v.get('name')).trim()})).trigger;
    Object.assign(WH, {tab: 'incoming', trigger: made.id, shown: {id: made.id, url: made.replayed ? '' : made.url}});
    if (made.replayed) toast('Made, but the address was lost on the way. Use New secret to get one.', true);
    const kind = String(v.get('preset') || 'none');
    try {
      if (kind !== 'none') {
        const secret = String(v.get('secret') || '').trim();
        const signed: WhTriggerMade = (await post(`/v2/triggers/${encodeURIComponent(made.id)}/signing`, {preset: kind, ...(secret ? {secret} : {})})).trigger;
        if (!secret && !signed.replayed) WH.shown!.signing = signed.signing_secret;
      }
      const changed: { type_from?: string; hourly?: number; keep_days?: number; unheard?: string } = {};
      if (settings.type_from && settings.type_from !== (WH_TYPE_FROM[kind] || '')) changed.type_from = settings.type_from;
      if (settings.hourly !== 60) changed.hourly = settings.hourly;
      if (settings.keep_days !== 7) changed.keep_days = settings.keep_days;
      if (settings.unheard !== 'log') changed.unheard = settings.unheard;
      if (Object.keys(changed).length) await post(`/v2/triggers/${encodeURIComponent(made.id)}`, changed);
    } catch (e) { toast(errorMessage(e, ''), true); }
    dialog.close(); void whRender();
  });
  form.elements[0]!.focus();
}
function whSigningDialog(t: WebhookTrigger) {
  const dialog = whDialog('Signing', `<label>Preset<select name="preset">${WH_PRESETS.filter(([k]) => k !== 'none').map(([k, v]) => whOpt(k, v, t.signed ? t.preset : 'hmac')).join('')}</select></label>
    <label>Signing secret<input name="secret" maxlength="300" autocomplete="off" spellcheck="false" placeholder="Leave empty to make one"></label>`,
  `${t.signed ? '<button type="button" class="ghost danger" data-wh-sign-off>Turn off</button>' : ''}<button type="submit" class="primary">Save</button>`);
  const path = `/v2/triggers/${encodeURIComponent(t.id)}/signing`;
  whSubmit(dialog, async v => {
    if (t.signed && !(await askConfirm('Replace the signing secret? Calls signed with the current one are refused at once.'))) return;
    const secret = String(v.get('secret') || '').trim();
    const made: WhTriggerMade = (await post(path, {preset: String(v.get('preset')), ...(secret ? {secret} : {})})).trigger;
    WH.shown = {...(WH.shown?.id === t.id ? WH.shown : {}), id: t.id};
    if (!secret) { if (made.replayed) toast('Saved, but the secret was lost on the way. Save again for a new one.', true); else WH.shown.signing = made.signing_secret; }
    dialog.close(); void whRender();
  });
  const off = dialog.querySelector<HTMLElement>('[data-wh-sign-off]');
  if (off) off.onclick = async () => {
    if (!(await askConfirm('Turn signing off? Anyone with the address can call it.', off))) return;
    try { await post(path, {off: true}); if (WH.shown?.id === t.id) delete WH.shown.signing; dialog.close(); void whRender(); }
    catch (e) { const error = dialog.querySelector('[data-wh-error]'); if (error) error.textContent = errorMessage(e, ''); }
  };
}
async function whRotate(t: WebhookTrigger) {
  if (!(await askConfirm('Make a new address? The current one stops working at once.'))) return;
  try {
    const made: WhTriggerMade = (await post(`/v2/triggers/${encodeURIComponent(t.id)}/rotate`, {})).trigger;
    WH.shown = {...(WH.shown?.id === t.id ? WH.shown : {}), id: t.id, url: made.replayed ? '' : made.url};
    if (made.replayed) toast('A new address was made, but it was lost on the way. Try New secret again.', true);
    void whRender();
  } catch (e) { toast(errorMessage(e, ''), true); }
}
async function whTriggerDelete(t: WebhookTrigger) {
  if (!(await askConfirm(`Delete “${t.name}”?`))) return;
  try {
    await post(`/v2/triggers/${encodeURIComponent(t.id)}/delete`, {});
    Object.assign(WH, {trigger: '', shown: null});
    void whRender();
  } catch (e) { toast(errorMessage(e, ''), true); }
}

// ---------------------------------------------------------------------------------------------------------- Outgoing
function whEventsSummary(events: string[] | null | undefined) {
  if (!events?.length || events.includes('*')) return 'All events';
  return events.length <= 2 ? events.join(', ') : `${events.slice(0, 2).join(', ')} +${events.length - 2}`;
}
function whHookPills(h: WhHook) { return h.enabled ? '' : `<span class="pill waiting" title="${esc(h.paused_reason || '')}">Paused</span>`; }
async function whHooks(el: HTMLElement, run: number) {
  const list = (await get<{ items?: WhHook[] }>('/v2/webhooks?limit=200', {fresh: true})).items || [];
  WH_HOOKS = list;
  if (run !== WH_RUN) return;
  el.innerHTML = `<div class="settings-bots-filters"><button type="button" class="primary" data-wh-hook-new>New endpoint</button></div>
    ${list.length ? `<div class="wh-list" data-wh-hooks>${list.map(h => `<button type="button" class="wh-row" data-wh-hook="${esc(h.id)}">
        <span class="wh-name"><strong>${esc(h.name)}</strong>${whHookPills(h)}</span>
        <span class="wh-meta muted">${esc(h.host)} · ${esc(whEventsSummary(h.events))}</span>
        <span class="wh-meta"><span class="tnum">${h.last_7_days.delivered} sent</span>${h.last_7_days.failed ? ` · <span class="wh-bad tnum">${h.last_7_days.failed} failed</span>` : ''} <span class="muted">7d</span></span></button>`).join('')}</div>`
      : '<div class="empty">No endpoints yet.</div>'}`;
  el.querySelector<HTMLElement>('[data-wh-hook-new]')!.onclick = () => void whHookDialog(null);
  el.querySelectorAll<HTMLElement>('[data-wh-hook]').forEach(b => { b.onclick = () => { WH.hook = b.dataset.whHook!; WH.hookShown = null; void whRender(); }; });
}
// The event picker: All, then each catalog group (its box means `group.*`) and its events. A pattern the catalog
// does not know is kept, checked, at the end.
function whEventGroups(types: WhEventType[]) {
  const groups = new Map<string, WhEventType[]>();
  for (const t of types) { if (!groups.has(t.group)) groups.set(t.group, []); groups.get(t.group)!.push(t); }
  return [...groups];
}
function whEventsHTML(types: WhEventType[], events: string[]) {
  const set = new Set(events), all = set.has('*'), groups = whEventGroups(types);
  const known = new Set(types.map(t => t.name).concat(groups.map(([g]) => g + '.*')));
  const extra = events.filter(e => e !== '*' && !known.has(e));
  const on = (g: string, n: string) => all || set.has(n) || set.has(g + '.*');
  const box = (attrs: string, checked: boolean, label: string, title = '') => `<label${title ? ` title="${esc(title)}"` : ''}><input type="checkbox" ${attrs}${checked ? ' checked' : ''}> ${label}</label>`;
  return `<fieldset class="wh-events" data-wh-events><legend>Events</legend>
    ${box('data-ev-all', all, 'All events')}
    <div class="wh-ev-groups">${groups.map(([g, items]) => `<div class="wh-ev-group" data-ev-group="${esc(g)}">
      ${items.length > 1 ? box('data-ev-head', items.every(i => on(g, i.name)), `<code>${esc(g)}.*</code>`) : ''}
      ${items.map(i => box(`data-ev-name="${esc(i.name)}"`, on(g, i.name), items.length > 1 ? esc(i.name.slice(g.length + 1)) : `<code>${esc(i.name).replace('.', '.<wbr>')}</code>`, i.description)).join('')}</div>`).join('')}
      ${extra.length ? `<div class="wh-ev-group">${extra.map(n => box(`data-ev-name="${esc(n)}"`, true, `<code>${esc(n)}</code>`)).join('')}</div>` : ''}</div></fieldset>`;
}
function whEventsBind(box: HTMLElement) {
  const sync = () => {
    const all = box.querySelector<HTMLInputElement>('[data-ev-all]')!.checked;
    box.querySelectorAll<HTMLInputElement>('.wh-ev-groups input').forEach(i => { i.disabled = all; if (all) i.checked = true; });
    if (!all) box.querySelectorAll('[data-ev-group]').forEach(g => {
      const head = g.querySelector<HTMLInputElement>('[data-ev-head]');
      if (head) head.checked = [...g.querySelectorAll<HTMLInputElement>('[data-ev-name]')].every(i => i.checked);
    });
  };
  // Only the picker's checkboxes fire change in it.
  box.addEventListener('change', ev => {
    const target = ev.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.matches('[data-ev-all]') && !target.checked) box.querySelectorAll<HTMLInputElement>('.wh-ev-groups input').forEach(i => { i.checked = false; });
    const head = target.closest<HTMLInputElement>('[data-ev-head]');
    if (head) head.closest('[data-ev-group]')!.querySelectorAll<HTMLInputElement>('[data-ev-name]').forEach(i => { i.checked = head.checked; });
    sync();
  });
  sync();
}
function whEventsValue(box: HTMLElement): string[] {
  if (box.querySelector<HTMLInputElement>('[data-ev-all]')?.checked) return ['*'];
  const out: string[] = [];
  box.querySelectorAll<HTMLElement>('.wh-ev-group').forEach(g => {
    const head = g.querySelector<HTMLInputElement>('[data-ev-head]');
    if (head?.checked) out.push(g.dataset.evGroup + '.*');
    else g.querySelectorAll<HTMLElement>('[data-ev-name]:checked').forEach(i => out.push(i.dataset.evName!));
  });
  return out;
}
// The events an endpoint may subscribe to: the catalog less the ones only rules see (`outbound: false`).
const whOutbound = (types: WhEventType[]) => types.filter(t => t.outbound !== false);
// Filters (backend/webhooks.py FilterIn): departments, bots, pipelines. A list left empty lets every event through.
// A name the lists no longer have (a removed bot) stays, checked, so saving keeps it.
const WH_FILTERS: [WhFilterKey, string][] = [['departments', 'Departments'], ['bots', 'Bots'], ['pipelines', 'Pipelines']];
let WH_DEPTS: { task_rule_departments: { id: string; name: string }[] } | null = null;
async function whFilterChoices(): Promise<WhChoices> {
  const [routines, pipelines] = await Promise.all([SYSTEM_ROUTINES || WH_DEPTS || get<{ task_rule_departments?: { id: string; name: string }[] }>('/v2/system-routines').catch(() => null), pipelinesAll().catch((): PipelineRec[] => [])]);
  if (routines?.task_rule_departments) WH_DEPTS = {task_rule_departments: routines.task_rule_departments};
  return {
    departments: (routines?.task_rule_departments || []).map((d): [string, string] => [d.id, d.name]),
    bots: (S.emps || []).filter(e => e.status !== 'archived').map((e): [string, string] => [e.id, empName(e.id)]).sort((a, b) => a[1].localeCompare(b[1])),
    pipelines: pipelines.filter(p => !p.retired_at).map((p): [string, string] => [p.id, p.name]),
  };
}
const whFilterIds = (key: WhFilterKey, list: string[] | undefined) => (list || []).map(v => key === 'bots' ? String(v).replace(/^bot:/, '') : String(v));
function whFiltersHTML(filter: WhFilter | null | undefined, choices: WhChoices) {
  const cols = WH_FILTERS.map(([key, label]) => {
    const chosen = new Set(whFilterIds(key, filter?.[key])), known = new Set(choices[key].map(([v]) => v));
    const items = [...choices[key], ...[...chosen].filter(v => !known.has(v)).map((v): [string, string] => [v, v])];
    if (!items.length) return '';
    return `<div class="wh-ev-group wh-filter" data-filter="${key}"><span class="wh-filter-head">${label} <span class="muted" data-filter-all>All</span></span>
      <div class="wh-filter-items">${items.map(([v, w]) => `<label><input type="checkbox" value="${esc(v)}"${chosen.has(v) ? ' checked' : ''}> ${esc(w)}</label>`).join('')}</div></div>`;
  }).join('');
  return cols ? `<fieldset class="wh-events" data-wh-filters><legend>Filters</legend><div class="wh-ev-groups">${cols}</div></fieldset>` : '';
}
function whFiltersBind(box: HTMLElement) {
  const sync = () => box.querySelectorAll('[data-filter]').forEach(g => { g.querySelector<HTMLElement>('[data-filter-all]')!.hidden = !!g.querySelector('input:checked'); });
  box.addEventListener('change', sync); sync();
}
function whFiltersValue(box: HTMLElement) {
  const out: Record<string, string[]> = {departments: [], bots: [], pipelines: []};
  box.querySelectorAll<HTMLElement>('[data-filter]').forEach(g => { out[g.dataset.filter!] = [...g.querySelectorAll<HTMLInputElement>('input:checked')].map(i => i.value); });
  return out;
}
// Custom headers (backend/webhooks.py HeaderIn), at most ten. A secret's value never comes back: its row says "set",
// and left empty it keeps the value it has.
const WH_MAX_HEADERS = 10;
// A secret cleared when the address changed says "needs value": the endpoint is paused until it is typed or removed.
function whHeaderRowHTML(x: WhHeader = {}) {
  const kept = x.secret && (x.set || x.needs_value);
  return `<div class="wh-hdr${x.needs_value ? ' wh-hdr-needs' : ''}" data-hdr${x.needs_value ? ' data-hdr-needs' : ''}>
    <input data-hdr-name maxlength="64" autocomplete="off" spellcheck="false" placeholder="Name" aria-label="Header name" value="${esc(x.name || '')}">
    <input data-hdr-value type="${x.secret ? 'password' : 'text'}" maxlength="1000" autocomplete="new-password" spellcheck="false" aria-label="Value" placeholder="${x.needs_value ? 'needs value' : kept ? 'set' : 'Value'}" value="${kept ? '' : esc(x.value || '')}">
    <label class="wh-hdr-secret"><input type="checkbox" data-hdr-secret${x.secret ? ' checked' : ''}> Secret</label>
    <button type="button" class="ghost" data-hdr-remove aria-label="Remove header">✕</button></div>`;
}
function whHeadersHTML(list: WhHeader[] | undefined) {
  return `<fieldset class="wh-events" data-wh-headers><legend>Headers</legend><div class="wh-hdrs">${(list || []).map(whHeaderRowHTML).join('')}</div>
    <div><button type="button" class="ghost" data-hdr-add>Add header</button></div></fieldset>`;
}
function whHeadersBind(box: HTMLElement) {
  const rows = box.querySelector('.wh-hdrs')!, add = box.querySelector<HTMLElement>('[data-hdr-add]')!;
  const sync = () => { add.hidden = rows.children.length >= WH_MAX_HEADERS; };
  add.onclick = () => { rows.insertAdjacentHTML('beforeend', whHeaderRowHTML()); sync(); rows.lastElementChild!.querySelector<HTMLElement>('[data-hdr-name]')!.focus(); };
  box.addEventListener('click', ev => { const x = eventElement(ev)?.closest('[data-hdr-remove]'); if (x) { x.closest('[data-hdr]')!.remove(); sync(); } });
  // A secret is typed hidden. Only the rows' inputs fire change here.
  box.addEventListener('change', ev => {
    const target = ev.target;
    if (target instanceof HTMLInputElement && target.matches('[data-hdr-secret]')) target.closest('[data-hdr]')!.querySelector<HTMLInputElement>('[data-hdr-value]')!.type = target.checked ? 'password' : 'text';
  });
  sync();
}
function whHeadersValue(box: HTMLElement) {
  const out: WhHeaderIn[] = [];
  box.querySelectorAll('[data-hdr]').forEach(r => {
    const name = r.querySelector<HTMLInputElement>('[data-hdr-name]')!.value.trim(), value = r.querySelector<HTMLInputElement>('[data-hdr-value]')!.value, secret = r.querySelector<HTMLInputElement>('[data-hdr-secret]')!.checked;
    if (!name && !value) return;
    if (!name) throw new Error('Name each header');
    out.push(secret && !value ? {name, secret} : {name, value, secret});
  });
  return out;
}
async function whHookDialog(h: WhHook | null) {
  const [types, choices] = await Promise.all([eventTypesLoad().catch(() => []), whFilterChoices()]);
  const dialog = whDialog(h ? 'Edit endpoint' : 'New endpoint', `<label>Name<input name="name" maxlength="100" required autocomplete="off" value="${esc(h?.name || '')}"></label>
    <label>URL<input name="url" type="url" maxlength="2000" required autocomplete="off" spellcheck="false" placeholder="https://" value="${esc(h?.url || '')}"></label>
    <label data-wh-http hidden><input type="checkbox" name="allow_http"${h?.allow_http ? ' checked' : ''}> Allow plain http to this computer</label>
    ${whEventsHTML(whOutbound(types), h?.events || ['*'])}${whFiltersHTML(h?.filter, choices)}${whHeadersHTML(h?.headers)}`,
  `<button type="submit" class="primary">${h ? 'Save' : 'Create'}</button>`);
  const form = dialog.querySelector<WhHookForm>('form')!, events = dialog.querySelector<HTMLElement>('[data-wh-events]')!;
  const filters = dialog.querySelector<HTMLElement>('[data-wh-filters]'), headers = dialog.querySelector<HTMLElement>('[data-wh-headers]')!;
  whEventsBind(events);
  if (filters) whFiltersBind(filters);
  whHeadersBind(headers);
  const http = () => { dialog.querySelector<HTMLElement>('[data-wh-http]')!.hidden = !/^http:/i.test(form.elements.url.value.trim()); };
  form.elements.url.oninput = http; http();
  whSubmit(dialog, async v => {
    const chosen = whEventsValue(events);
    if (!chosen.length) throw new Error('Pick at least one event');
    const url = String(v.get('url')).trim();
    const body: WhHookBody = {name: String(v.get('name')).trim(), url, events: chosen, allow_http: /^http:/i.test(url) && v.has('allow_http')};
    const filter = filters ? whFiltersValue(filters) : null, sent = whHeadersValue(headers);
    if (h) {
      // A new destination while secret headers keep their values asks first: keep them, or clear them to type again.
      const saved = await settingsDestinationSave(extra => post(`/v2/webhooks/${encodeURIComponent(h.id)}/update`,
        {...body, ...(filter ? {filter} : {}), headers: sent, expected_revision: h.revision, ...extra}));
      if (saved.credentials_choice === 'clear') toast('Saved. Type the secret header values again: the endpoint is paused until then.');
      dialog.close(); void whRender(); return;
    }
    if (filter && Object.values(filter).some(x => x.length)) body.filter = filter;
    if (sent.length) body.headers = sent;
    const made: { webhook?: WhHookMade } = await post('/v2/webhooks', body);
    dialog.close();
    if (!made.webhook) { toast('Sent for approval'); void whRender(); return; }
    Object.assign(WH, {tab: 'outgoing', hook: made.webhook.id, hookShown: made.webhook.replayed ? null : {id: made.webhook.id, secret: made.webhook.secret}});
    if (made.webhook.replayed) toast('Made, but the secret was lost on the way. Use New secret to get one.', true);
    void whRender();
  });
  form.elements.name.focus();
}
async function whHookDetail(el: HTMLElement, run: number) {
  const id = WH.hook, path = `/v2/webhooks/${encodeURIComponent(id)}`;
  const [d, sends, types, choices] = await Promise.all([get<{ webhook: WhHook }>(path, {fresh: true}), get<{ items?: WhSend[] }>(path + '/sends?limit=50', {fresh: true}), eventTypesLoad().catch(() => []),
    whFilterChoices()]);
  if (run !== WH_RUN) return;
  const h = d.webhook, items = sends.items || [], may = h.can_change;
  const actions = may ? `<button class="ghost" type="button" data-wh-hook-edit>Edit</button>
    <button class="ghost" type="button" data-wh-hook-pause>${h.enabled ? 'Pause' : 'Resume'}</button>
    <button class="ghost" type="button" data-wh-hook-rotate>New secret</button><button class="ghost" type="button" data-wh-hook-delete>Delete</button>` : '';
  const names = whOutbound(types).filter(t => !t.name.includes('*')).map(t => t.name);
  const filterWords = WH_FILTERS.flatMap(([key]) => whFilterIds(key, h.filter?.[key]).map(v => (choices[key].find(([id]) => id === v) || [v, v])[1]));
  el.innerHTML = `${whHeadHTML(h.name, whHookPills(h), actions)}
    ${WH.hookShown?.id === id ? whSecretHTML('Signing secret', WH.hookShown.secret, 'secret') : ''}
    <dl class="wh-facts">
      <dt>URL</dt><dd><code>${esc(h.url)}</code></dd>
      <dt>Events</dt><dd>${h.events.includes('*') ? 'All events' : h.events.map(e => `<code>${esc(e)}</code>`).join(' ')}</dd>
      ${filterWords.length ? `<dt>Filters</dt><dd data-wh-filter-facts>${esc(filterWords.join(' · '))}</dd>` : ''}
      ${h.headers?.length ? `<dt>Headers</dt><dd data-wh-header-facts>${h.headers.map(x => `<code>${esc(x.name)}</code>${x.secret ? ' <span class="muted">secret</span>' : ''}`).join(', ')}</dd>` : ''}
      <dt>7 days</dt><dd><span class="tnum">${h.last_7_days.delivered} sent</span> · <span class="tnum${h.last_7_days.failed ? ' wh-bad' : ''}">${h.last_7_days.failed} failed</span>${h.last_7_days.pending ? ` · <span class="tnum">${h.last_7_days.pending} waiting</span>` : ''}</dd>
      ${h.enabled ? '' : `<dt>Paused</dt><dd>${esc(h.paused_reason || 'Yes')}</dd>`}
      ${h.old_secret_until ? `<dt>Old secret</dt><dd>Works until ${esc(fmt(h.old_secret_until))}</dd>` : ''}
    </dl>
    <h4 class="wr-sub">Sends</h4>
    ${may && names.length ? `<div class="settings-bots-filters"><select class="settings-inline-select" data-wh-example-type aria-label="Event type">${names.map(n => whOpt(n, n, '')).join('')}</select>
      <button class="ghost" type="button" data-wh-example>Send example</button></div>` : ''}
    ${may && h.enabled ? `<div class="settings-bots-filters" data-wh-catchup><label class="wh-since">Since<input type="date" class="settings-inline-select" data-wh-since
        value="${whDay(-7)}" min="${whDay(-30)}" max="${whDay(0)}"></label>
      <button class="ghost" type="button" data-wh-catchup-do="recover" title="Send again what gave up since then">Retry failed</button>
      <button class="ghost" type="button" data-wh-catchup-do="replay" title="Send the events since then that this endpoint never got">Send missed</button></div>` : ''}
    ${items.length ? `<div class="wh-calls wh-sends" data-wh-sends>${items.map(s => `<div class="wh-call" data-wh-send="${esc(s.id)}">
        <span class="wh-time" title="${esc(s.created_at)}">${esc(ago(s.created_at))}</span>
        <span class="wh-type"><code>${esc(s.type)}</code></span>
        <span class="wh-outcome"><span class="pill ${WH_SEND_STATES[s.state] ?? ''}">${esc(s.state[0]!.toUpperCase() + s.state.slice(1))}</span>${s.subject ? ` <span class="muted">${esc(s.subject)}</span>` : ''}</span>
        <span class="tnum muted">${(s.attempts === 1 ? '1 try' : `${s.attempts} tries`)}</span>
        <span class="tnum muted" title="${esc(s.last_error || '')}">${s.last_status ? esc(s.last_status) : s.last_error ? 'Error' : ''}</span>
        <span class="wh-call-ctl">${may && ['failed', 'canceled'].includes(s.state) ? '<button class="ghost" type="button" data-wh-retry>Retry</button>' : ''}</span></div>`).join('')}</div>`
      : '<div class="empty">Nothing sent yet.</div>'}`;
  const on = (sel: string, fn: () => unknown) => { const b = el.querySelector<HTMLElement>(sel); if (b) b.onclick = fn; };
  const act = async (fn: () => Promise<unknown>, after?: () => void) => { try { await fn(); after?.(); void whRender(); } catch (e) { toast(errorMessage(e, ''), true); } };
  on('[data-wh-hook-edit]', () => void whHookDialog(h));
  on('[data-wh-hook-pause]', () => act(() => post(`${path}/${h.enabled ? 'pause' : 'resume'}`, {})));
  on('[data-wh-hook-rotate]', async () => {
    if (!(await askConfirm('Make a new signing secret? The old one keeps working for a while.'))) return;
    void act(async () => {
      const made: WhHookMade = (await post(path + '/rotate', {})).webhook;
      WH.hookShown = made.replayed ? null : {id, secret: made.secret};
      if (made.replayed) toast('A new secret was made, but it was lost on the way. Try again.', true);
    });
  });
  on('[data-wh-hook-delete]', async () => {
    if (!(await askConfirm(`Delete “${h.name}”? Nothing more is sent to it.`))) return;
    void act(() => post(path + '/delete', {}), () => { WH.hook = ''; WH.hookShown = null; });
  });
  on('[data-wh-example]', () => act(async () => {
    await post(path + '/example', {type: el.querySelector<HTMLSelectElement>('[data-wh-example-type]')!.value});
    toast('Example queued');
  }));
  el.querySelectorAll<HTMLElement>('[data-wh-catchup-do]').forEach(b => { b.onclick = () => act(async () => {
    const since = el.querySelector<HTMLInputElement>('[data-wh-since]')!.value;
    if (!since) throw new Error('Pick a day');
    let r: WhCatchup = await post(`${path}/${b.dataset.whCatchupDo}`, {since}), n = r.recovered ?? r.queued ?? 0;
    // A replay round that left more waiting says where it stopped: carry on from there, a few rounds at most.
    for (let round = 1; r.more && r.next_since && round < 10; round++) {
      r = await post(`${path}/${b.dataset.whCatchupDo}`, {since: r.next_since, after: r.next_after});
      n += r.queued ?? 0;
    }
    toast(`${b.dataset.whCatchupDo === 'recover' ? 'Retrying' : 'Sending'} ${n}${r.more ? ' (more wait: run it again)' : ''}`);
  }); });
  el.querySelectorAll<HTMLElement>('[data-wh-retry]').forEach(b => { b.onclick = () => act(() => post(`${path}/sends/${encodeURIComponent(b.closest<HTMLElement>('[data-wh-send]')!.dataset.whSend!)}/retry`, {})); });
}

// ---------------------------------------------------------------------------------------------- Through Tico Gateway
async function whGateway(el: HTMLElement, run: number) {
  // Wake this server (backend/hook_relay.py `wake`): only a server Tico hosts sleeps (GET /v2/sleep `hosted`). Null is
  // on: HQ wakes a hosted install for a new relay until told otherwise (contract C8).
  // The wake address is the owner's alone (403 for anyone else) and only for a server people run themselves.
  const [relay, list, hosted, wake] = await Promise.all([get<import('../types/api').WebhookRelayState>('/v2/relay', {fresh: true}).then(d => d.relay), whTriggersFresh(),
    get<import('../types/api').SleepWhenIdle>('/v2/sleep').then(d => !!d.sleep?.hosted).catch(() => false),
    S.me?.role === 'owner' ? get<WhWake>('/v2/hooks/wake-url', {fresh: true}).catch(() => null) : null]);
  if (run !== WH_RUN) return;
  if (relay.key === 'none') { el.innerHTML = `<div class="empty" data-wh-no-key>Connect the Gateway first.</div>`; return; }
  const rows = list.filter(t => !t.builtin);
  const status = [relay.last_pull ? `Last pull ${esc(ago(relay.last_pull))}` : 'Not pulled yet',
    Number.isFinite(relay.pending) ? `<span class="tnum">${relay.pending}</span> waiting` : ''].filter(Boolean).join(' · ');
  el.innerHTML = `${relay.key === 'revoked' ? `<p class="err">The Gateway refused this server's key.</p>` : ''}
    <p class="settings-cell-note" data-wh-relay-status>${status}${relay.last_error ? ` · <span class="wh-bad">${esc(relay.last_error)}</span>` : ''}</p>
    ${wake && !hosted ? '<div class="wh-wake-url" data-wh-wake-url></div>' : ''}
    ${WH.shown?.relay ? whSecretHTML(`Gateway address · ${list.find(t => t.id === WH.shown!.id)?.name || ''}`, WH.shown.relay, 'relay') : ''}
    ${rows.length ? `<div class="wh-list" data-wh-relays>${rows.map(t => `<div class="wh-row wh-gw" data-wh-relay="${esc(t.id)}">
        <span class="wh-name"><strong>${esc(t.name)}</strong></span>
        <span class="wh-meta">${t.relay ? `<code>${esc(t.relay.address_prefix)}/…</code>` : '<span class="muted">No address</span>'}</span>
        <span class="wh-call-ctl">${hosted && t.relay ? `<label class="sysr-switch wh-wake">${peopleSwitch('data-wh-wake', t.relay.wake !== false, !whMine(t), 'Wake this server')}<span>Wake this server</span></label>` : ''}${whMine(t) ? (t.relay ? `<button class="ghost" type="button" data-wh-relay-do="rotate">New secret</button><button class="ghost" type="button" data-wh-relay-do="delete">Remove</button>`
          : `<button class="ghost" type="button" data-wh-relay-do="make">Make address</button>`) : ''}</span></div>`).join('')}</div>`
      : '<div class="empty">No incoming webhooks yet.</div>'}`;
  // The wake row is drawn only when the wake address was read.
  const wakeEl = el.querySelector<HTMLElement>('[data-wh-wake-url]');
  if (wakeEl) whWakeUrl(wakeEl, wake!);
  el.querySelectorAll<HTMLInputElement>('[data-wh-wake]').forEach(box => { box.onchange = async () => {
    const id = box.closest<HTMLElement>('[data-wh-relay]')!.dataset.whRelay!;
    box.disabled = true;
    try { box.checked = !!(await post(`/v2/triggers/${encodeURIComponent(id)}/relay/wake`, {wake: box.checked})).relay.wake; }
    catch (e) { box.checked = !box.checked; toast(errorMessage(e, ''), true); }
    finally { box.disabled = false; }
  }; });
  el.querySelectorAll<HTMLButtonElement>('[data-wh-relay-do]').forEach(b => { b.onclick = async () => {
    const id = b.closest<HTMLElement>('[data-wh-relay]')!.dataset.whRelay!, what = b.dataset.whRelayDo, path = `/v2/triggers/${encodeURIComponent(id)}/relay`;
    if (what === 'delete' && !(await askConfirm('Remove the Gateway address? Calls to it stop at once.', b))) return;
    if (what === 'rotate' && !(await askConfirm('Make a new Gateway address? The current one stops working at once.', b))) return;
    b.disabled = true;
    try {
      const r: import('../types/api').WebhookRelayAddress['relay'] = (await post(what === 'make' ? path : `${path}/${what}`, {})).relay;
      WH.shown = what === 'delete' ? null : r.replayed ? null : {id, relay: r.address};
      if (what !== 'delete' && r.replayed) toast('Made, but the address was lost on the way. Use New secret.', true);
      void whRender();
    } catch (e) { toast(errorMessage(e, ''), true); b.disabled = false; }
  }; });
}
// The wake address (backend/hook_relay.py `set_wake_url`, docs/install.md "Wake on webhook"): HQ posts a signed
// `tico.wake` there when a call has waited at the Gateway. One row: the address or Off, edited in place. Setting it
// answers with the signing secret once; turning it off makes any secret shown useless, so it goes too.
function whWakeUrl(el: HTMLElement, wake: WhWake, {editing = false, error = '', draft = wake.url || ''}: { editing?: boolean; error?: string; draft?: string } = {}) {
  const how = `<a href="${GH}/blob/main/docs/install.md#wake-on-webhook" target="_blank" rel="noopener noreferrer">How</a>`;
  const secret = WH.wakeSecret ? `<div class="trigger-secret" role="status" data-wh-secret="wake"><p><strong>Wake signing secret</strong> <span class="muted">Signs the tico.wake posts. Not shown again.</span></p>
    <div class="row"><code class="trigger-url">${esc(WH.wakeSecret)}</code><button class="ghost" type="button" data-wh-copy="wake">Copy</button><button class="ghost" type="button" data-wh-wake-do="done">Done</button></div></div>` : '';
  const row = editing ? `<form class="wh-row wh-gw" data-wh-wake-form novalidate>
      <span class="wh-name"><strong>Wake address</strong></span>
      <span class="wh-meta"><input type="url" name="url" value="${esc(draft)}" placeholder="https://" aria-label="Wake address" required data-wh-wake-input></span>
      <span class="wh-call-ctl">${how}<button type="submit" class="primary">Save</button><button class="ghost" type="button" data-wh-wake-do="cancel">Cancel</button></span></form>`
    : `<div class="wh-row wh-gw">
      <span class="wh-name"><strong>Wake address</strong></span>
      <span class="wh-meta" data-wh-wake-value>${wake.url ? `<code${wake.set_at ? ` title="Set ${esc(ago(wake.set_at))}"` : ''}>${esc(wake.url)}</code>` : '<span class="muted">Off</span>'}</span>
      <span class="wh-call-ctl">${how}<button class="ghost" type="button" data-wh-wake-do="edit">${wake.url ? 'Change' : 'Set'}</button>${wake.url ? '<button class="ghost danger" type="button" data-wh-wake-do="off">Turn off</button>' : ''}</span></div>`;
  el.innerHTML = secret + row + (error ? `<p class="err" role="status" data-wh-wake-error>${esc(error)}</p>` : '');
  const save = async (url: string | null, button: HTMLButtonElement | null) => {
    if (button) button.disabled = true;
    try {
      const r: import('../types/api').WebhookWakeUrlSet = await put('/v2/hooks/wake-url', {url});
      WH.wakeSecret = r.url ? r.secret || '' : '';
      whWakeUrl(el, {url: r.url, set_at: r.url ? new Date().toISOString() : null, set_by: r.url ? 'human:' + (S.me?.id || '') : null});
    } catch (e) { whWakeUrl(el, wake, {editing: url !== null, error: errorMessage(e, 'Try again'), draft: url || wake.url || ''}); }
  };
  el.querySelectorAll<HTMLButtonElement>('[data-wh-wake-do]').forEach(b => { b.onclick = () => {
    const what = b.dataset.whWakeDo;
    if (what === 'done') { WH.wakeSecret = ''; whWakeUrl(el, wake); }
    else if (what === 'off') void save(null, b);
    else whWakeUrl(el, wake, {editing: what === 'edit'});
    if (what === 'edit') el.querySelector<HTMLElement>('[data-wh-wake-input]')?.focus();
  }; });
  const form = el.querySelector<WhUrlForm>('[data-wh-wake-form]');
  if (form) form.onsubmit = ev => {
    ev.preventDefault();
    const url = form.elements.url.value.trim();
    if (!url) { whWakeUrl(el, wake, {editing: true, error: 'Enter an https:// address', draft: ''}); return; }
    void save(url, form.querySelector<HTMLButtonElement>('[type="submit"]'));
  };
}

// ------------------------------------------------------------------------------------------------- Where each part shows
// Automations: incoming webhooks are a built-in row of the list, kind webhook, and their own screen (pageAutomations).
// The row shows when the hub answers its list of triggers, as Maintenance's does when /v2/maintenance answers.
const AU_HOOKS = 'webhooks';
function auHooksList<T extends Array<Pick<import('../types/api').Automation, 'id'>>>(list: T, found: { triggers?: WebhookTrigger[] | null } | null): T {
  if (!Array.isArray(found?.triggers)) return list;
  WH_TRIGGERS = whIncoming(found.triggers);
  const row = {id: AU_HOOKS, name: 'Incoming webhooks', kind: 'webhook', builtin: true, readonly: true, owner_id: AU_MAINT_OWNER,
    enabled: true, trigger: {type: 'webhook', name: whPlural(WH_TRIGGERS.length, 'webhook')},
    steps: [], description: WH_TRIGGERS.map(t => t.name).join(' '), can_edit: false, can_pause: false};
  return [row, ...list.filter(a => a.id !== AU_HOOKS)] as T;
}
// The screen: its name over the list or one webhook (#/automations/webhooks/<id>), drawn once per visit.
const auHooksHead = (hd: HTMLElement) => { hd.innerHTML = `<h2 class="au-name">Incoming webhooks</h2>${AU_BUILTIN_PILL}`; };
function auHooksPane(pane: HTMLElement) {
  const item = decodeURIComponent(S.route.split('?')[0]!.slice(WH_INCOMING_ROUTE.length + 1).split('/')[0] || '');
  const fresh = !pane.querySelector(WH_HOSTS.incoming);
  if (fresh) pane.innerHTML = `<div class="au-pad" id="${WH_HOSTS.incoming.slice(1)}">${skelRows(3)}</div>`;
  if (fresh || WH.tab !== 'incoming' || WH.trigger !== item) void whMount('incoming', item);
}

// Tools: outgoing webhooks (#/tools/webhooks[/<id>]); their rows in the Tools list open them here.
function pageToolWebhooks(item = '') {
  if (!$(WH_HOSTS.outgoing)) $('#main').innerHTML = `<div class="tt tt-page" id="tool-webhooks">
    <nav class="tt-crumb" aria-label="Breadcrumb"><a href="${TOOLS_ROUTE}">Tools</a><span aria-hidden="true">›</span><span>Webhooks</span></nav>
    <section class="card"><div id="${WH_HOSTS.outgoing.slice(1)}">${skelRows(3, {card: true, dot: false})}</div></section></div>`;
  void whMount('outgoing', item);
}
