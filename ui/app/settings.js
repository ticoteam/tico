/* ui/app/settings.js — Settings shell: pageSettings, tabs, Privacy, loadSettings, Health services
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// How visuals show (Visuals always on): decided by the setup, so a line to read, never a switch. "full" with a
// separate visual address, "hosted" from Tico file hosting's address; otherwise pages and SVG are Open cards, and the
// line says what fixes it (backend/visuals.py shown).
const SETTINGS_VISUALS = {full: 'Visuals: full', hosted: 'Visuals: full, from Tico file hosting',
  media: "Visuals: pages and SVG can't show inline. Add a visual address or Tico file hosting."};
function settingsVisualsLine() {
  return `${SETTINGS_VISUALS[S.config?.visuals] || SETTINGS_VISUALS.media}
    <a href="${GH}/blob/main/docs/visuals.md#self-hosting" target="_blank" rel="noopener noreferrer">How visuals show</a>`;
}

// ----------------------------------------------------------------- settings
// People stay deliberately compact. Bot ownership, model, and physical placement are separate
// controls because changing who can use a bot must never silently move its runtime (or vice versa).
// Teams come from the team's data; a name reads as its words (`customer-success` is Customer Success).
const teamLabel = t => (S.orgGroups || []).find(g => g.id === t)?.name || String(t || '').replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
let SETTINGS_DATA = {people: [], machines: [], services: [], models: [], issues: [], history: {changes: [], transitions: []}};
// Presence is judged when a response arrives, never by aging a cached heartbeat on a later paint.
const SETTINGS_PRESENCE = {interval: 25000, timeout: 10000, at: 0, checking: false, failed: false, timer: null, pending: null, request: 0, applied: 0};
function settingsMachinesSnapshot(machines, request = ++SETTINGS_PRESENCE.request) {
  if (request < SETTINGS_PRESENCE.applied) return;
  SETTINGS_PRESENCE.applied = request;
  SETTINGS_PRESENCE.at = Date.now();
  SETTINGS_PRESENCE.failed = false;
  SETTINGS_DATA.machines = machines;
}
async function settingsMachinesRead() {
  const request = ++SETTINGS_PRESENCE.request;
  let timer;
  try {
    const operations = await Promise.race([
      get('/v2/operations', {fresh: true}),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Presence check timed out')), SETTINGS_PRESENCE.timeout); })
    ]);
    if (!Array.isArray(operations.machines)) throw new Error('Presence unavailable');
    settingsMachinesSnapshot(operations.machines, request);
    return operations;
  } finally { clearTimeout(timer); }
}
const settingsMachinesVisible = () => S.route === SETTINGS && SETTINGS_TAB === 'devices' && !!$('#set-machines');
function settingsMachinesWatch() {
  clearTimeout(SETTINGS_PRESENCE.timer);
  SETTINGS_PRESENCE.timer = null;
  if (!settingsMachinesVisible() || !S.me?.cloud) return;
  SETTINGS_PRESENCE.timer = setTimeout(() => {
    if (!settingsMachinesVisible()) return;
    void settingsMachinesRefresh().finally(settingsMachinesWatch);
  }, SETTINGS_PRESENCE.interval);
}
function settingsMachinesRefresh() {
  if (!S.me?.cloud) return Promise.resolve();
  if (SETTINGS_PRESENCE.pending) return SETTINGS_PRESENCE.pending;
  SETTINGS_PRESENCE.checking = true;
  if (settingsMachinesVisible()) renderSettingsMachines();
  SETTINGS_PRESENCE.pending = settingsMachinesRead().catch(() => {
    SETTINGS_PRESENCE.failed = true;
  }).finally(() => {
    SETTINGS_PRESENCE.pending = null;
    SETTINGS_PRESENCE.checking = false;
    if (settingsMachinesVisible()) renderSettingsMachines();
  });
  return SETTINGS_PRESENCE.pending;
}
// Each model CLI's capabilities, from its manifest (GET /v2/models `harnesses`, runner/harnesses/*.toml): what the UI
// asks instead of naming CLIs. Fetched once; until then, or if the fetch fails, every capability reads as unknown.
let HARNESS_ROWS = [];
let HARNESS_ROWS_LOADING = null;
function loadHarnessRows() {
  HARNESS_ROWS_LOADING ||= get('/v2/models').then(d => { HARNESS_ROWS = d?.harnesses || []; }, () => { HARNESS_ROWS_LOADING = null; });
  return HARNESS_ROWS_LOADING;
}
const harnessRow = id => HARNESS_ROWS.find(row => row.id === id) || null;
const harnessCaps = id => harnessRow(id)?.capabilities || {};
const harnessesWith = test => HARNESS_ROWS.filter(row => row.capabilities && test(row.capabilities));
const SETTINGS_TAB_KEY = 'tico.settings.tab';
// Remembered per browser tab so a reload lands where the person was; a first visit opens General.
let SETTINGS_TAB = (() => { try { return sessionStorage.getItem(SETTINGS_TAB_KEY) || 'general'; } catch { return 'general'; } })();
// The address the shown tab writes (settingsAddress) so it can be copied, bookmarked or shared; route() reads it as Settings.
let SETTINGS_ADDRESS = '';
// Every tab's address (`#/settings/<tab>`), and the other names people use for one. A known tab this person
// can't see opens General with a line saying so; an unknown name just opens General.
const SETTINGS_TABS = ['general', 'people', 'bots', 'devices', 'models', 'repos', 'tasks', 'inbox',
  'inbound', 'mail', 'system',
  'webhooks', 'health', 'history', 'gateway', 'billing'];
// System was Rules (wake rules, now automations) and before that Routines; chat: the old Chat tab; providers and
// subscriptions: what is the Models tab now; limits: its turn levels.
const SETTINGS_ALIAS = {humans: 'people', computers: 'devices', repositories: 'repos', google: 'inbox', 'email-phone': 'inbound',
  rules: 'system', routines: 'system', recurring: 'system', chat: 'models', providers: 'models', subscriptions: 'models',
  limits: 'models', 'turn-levels': 'models'};
// Cards that moved from Computers to Models: an old link to one (the settings catalogue's) opens Models.
const SETTINGS_MOVED_TO_MODELS = ['#settings-models', '#set-providers', '#set-gateway', '#set-turn-levels'];
let SETTINGS_DENIED = false;
let SETTINGS_TRANSITION_TIMER = null;
// A section a link points into (an old tab's name, settingsShow); it scrolls there once drawn.
let SETTINGS_REVEAL = '';
// The old tabs that are now sections of General.
const SETTINGS_GENERAL = {team: '#settings-team', company: '#settings-company', assistant: '#settings-assistant-pane', privacy: '#settings-privacy', storage: '#settings-storage'};
const settingsReveal = selector => {
  if (!selector || SETTINGS_REVEAL !== selector) return;
  SETTINGS_REVEAL = ''; $(selector)?.scrollIntoView({block: 'start'});
};
// A card a link names (`#/settings/<tab>/<card>`, the settings catalogue's) on a tab that draws it later: scroll once it is there.
const settingsRevealSoon = (selector, tries = 20) => {
  if (!selector || SETTINGS_REVEAL !== selector) return;
  if ($(selector)) { settingsReveal(selector); return; }
  if (tries > 0) setTimeout(() => settingsRevealSoon(selector, tries - 1), 150);
};
// Owners and admins always; a member unless an admin switched create_bots off (the server decides, and counts the limit).
const settingsCanCreateBots = () => S.me?.role === 'owner' || !!S.me?.bot_admin || S.me?.can_create_bots !== false;
// The owner, an admin, one of the bot's owners, or someone it reports up to: what the server says (`can_manage`).
const settingsCanManageBot = e => S.me?.role === 'owner' || !!S.me?.bot_admin || !!e?.can_manage;
const settingsIsAdmin = () => S.me?.role === 'owner' || !!S.me?.bot_admin;
// The team owner's name, for "who can change this" lines; '' when the roster has not loaded.
function settingsOwnerName() {
  const people = [...(SETTINGS_DATA.people || []), ...(S.people || [])], id = S.me?.owner_id;
  const owner = people.find(p => (id && p.id === id) || p.owner || p.role === 'owner');
  return owner?.name || S.config?.owner_name || '';
}
// "Only Ana, the owner, can change models." / "Only Ana (the owner) or an admin can change this."
function settingsWhoCanHTML(what = 'this', {admins = false} = {}) {
  const name = settingsOwnerName();
  const who = admins ? (name ? `${esc(name)} (the owner) or an admin` : 'the owner or an admin') : (name ? `${esc(name)}, the owner,` : 'the owner');
  return `<p class="settings-cell-note" data-who-can>Only ${who} can change ${esc(what)}.</p>`;
}
// A save that would send saved credentials to another service is held (backend/destinations.py: 409
// destination_change, naming the old and new destination by scheme, host and port and the credentials by label, never a
// value). The editor keeps them (they go to the new address), clears them to type again, or cancels (nothing is saved).
// Resolves 'keep', 'clear' or '' for cancel.
const settingsDestinationHeld = error => error?.status === 409 && error?.body?.error?.code === 'destination_change';
function settingsDestinationChoice(error) {
  const held = error?.body?.error || {}, labels = (held.credentials || []).join(', ') || 'The saved credentials';
  return new Promise(resolve => {
    const dialog = document.createElement('dialog');
    dialog.className = 'tmodal destination-dialog';
    dialog.setAttribute('aria-label', 'Change where this sends');
    dialog.innerHTML = `<form method="dialog"><header><h2>Change where ${esc(held.name || 'this integration')} sends?</h2></header>
      <p><b>${esc(labels)}</b> ${held.credentials?.length > 1 ? 'were' : 'was'} saved for <b data-destination-old>${esc(held.old?.address || '')}</b>.
        This change sends to <b data-destination-new>${esc(held.new?.address || '')}</b>${held.path ? ` (${esc(held.path)})` : ''}.</p>
      <p class="muted">Keep sends the saved ${esc(labels)} to the new address from now on. Clear removes ${held.credentials?.length > 1 ? 'them' : 'it'}, so you type ${held.credentials?.length > 1 ? 'them' : 'it'} again for the new address. Either way the change is kept in the history.</p>
      <footer><button value="keep" class="primary" data-destination-keep>Keep and send to the new address</button>
        <button value="clear" class="ghost danger" data-destination-clear>Clear and re-enter</button>
        <button value="" class="ghost" data-destination-cancel>Cancel</button></footer></form>`;
    dialog.addEventListener('close', () => { dialog.remove(); resolve(dialog.returnValue || ''); });
    document.body.appendChild(dialog); dialog.showModal();
  });
}
// `send(extra)` once, and again with the editor's answer when it was held; a cancel throws "Nothing was saved".
async function settingsDestinationSave(send) {
  try { return await send({}); } catch (error) {
    if (!settingsDestinationHeld(error)) throw error;
    const credentials = await settingsDestinationChoice(error);
    if (!credentials) throw new Error('Nothing was saved');
    return Object.assign(await send({credentials}), {credentials_choice: credentials});
  }
}
// The one way a failure shows: a red line under the control that failed, saying what to do. A row-like
// parent holds it so it sits under the whole row; a list item holds it inside.
function settingsFieldAnchor(control) {
  return control?.closest?.('li, label, .row, .sb-row, .people-line, .privacy-row, .rrow-wrap, tr') || control;
}
function settingsFieldClear(control) {
  const anchor = settingsFieldAnchor(control);
  if (!anchor) return;
  const inside = anchor.querySelector?.(':scope > [data-field-err]'), next = anchor.nextElementSibling;
  inside?.remove();
  if (next?.matches?.('[data-field-err]')) next.remove();
}
function settingsFieldError(control, message) {
  const anchor = settingsFieldAnchor(control);
  if (!anchor?.isConnected) { toast(message, true); return; }
  settingsFieldClear(control);
  const line = document.createElement('p');
  line.className = 'field-err err'; line.dataset.fieldErr = ''; line.setAttribute('role', 'alert');
  line.textContent = message;
  if (anchor.tagName === 'LI' || anchor.tagName === 'TR') (anchor.tagName === 'TR' ? anchor.lastElementChild : anchor).appendChild(line);
  else anchor.after(line);
}
// Ask before anything hard to undo. Resolves true only on the confirm button.
function settingsConfirm({title, text, ok = 'Confirm', danger = true}) {
  return new Promise(resolve => {
    const dialog = document.createElement('dialog');
    dialog.className = 'tmodal settings-confirm'; dialog.setAttribute('aria-label', title);
    dialog.innerHTML = `<form method="dialog"><div class="tmodal-title">${esc(title)}</div><div class="tmodal-body"><p>${esc(text)}</p>
      <div class="onb-actions"><button type="submit" class="ghost" value="cancel" data-confirm-cancel>Cancel</button><span class="spacer"></span>
      <button type="submit" class="primary${danger ? ' people-danger' : ''}" value="ok" data-confirm-ok>${esc(ok)}</button></div></div></form>`;
    dialog.addEventListener('close', () => { resolve(dialog.returnValue === 'ok'); dialog.remove(); });
    document.body.appendChild(dialog); dialog.showModal();
    dialog.querySelector('[data-confirm-cancel]').focus();
  });
}
// Whoever the server lets make a personal token (`can_create_tokens`); an admin also sees everyone's. Someone
// who still holds a token keeps the card to revoke it.
const settingsCanMakeTokens = () => S.me?.can_create_tokens ?? settingsIsAdmin();
function pageSettings() {
  clearInterval(SETTINGS_TRANSITION_TIMER);
  if (SETTINGS_TAB === 'credentials' || SETTINGS_TAB === 'cloud') { SETTINGS_TAB = 'general'; location.hash = INTEGRATIONS; return; }
  // A repeat visit to the page already on screen (a refresh, a same-route redraw) keeps it: rebuilding
  // it would snap back to the default tab and drop whatever is half typed in a form.
  const built = $('#settings-tabs');
  if (built && built.dataset.role === (S.me?.role || '') && built.dataset.admin === String(settingsIsAdmin())) { settingsShow(SETTINGS_TAB); loadSettings(); return; }
  $('#main').innerHTML = `<div class="meeting-head"><div><h1>Settings</h1>${settingsVersionHTML()}</div>${settingsThemeHTML()}</div>
    <div id="settings-issues"></div>
    <div class="tabs settings-tabs" id="settings-tabs" data-role="${esc(S.me?.role || '')}" data-admin="${settingsIsAdmin()}" role="tablist" aria-label="Settings">
      <button type="button" data-settings-tab="general" role="tab">General</button>
      ${settingsIsAdmin() ? '<button type="button" data-settings-tab="people" role="tab">People</button>' : ''}
      <button type="button" data-settings-tab="bots" role="tab">Bots</button>
      <button type="button" data-settings-tab="devices" role="tab">Computers</button>
      <button type="button" data-settings-tab="models" role="tab">Models</button>
      <button type="button" data-settings-tab="repos" role="tab">Repositories</button>
      <button type="button" data-settings-tab="tasks" role="tab">Tasks</button>
      <button type="button" data-settings-tab="inbox" role="tab">My Google account</button>
      <details class="settings-more" id="settings-more"><summary>More</summary><div>
        <button type="button" data-settings-tab="inbound" role="tab">Email &amp; Phone</button>
        <button type="button" data-settings-tab="mail" role="tab">Mail</button>
        <button type="button" data-settings-tab="system" role="tab">System</button>
        <button type="button" data-settings-tab="webhooks" role="tab">Webhooks</button>
        <button type="button" data-settings-tab="health" role="tab">Health<span class="hl-alert" data-hl-alert hidden></span></button>
        <button type="button" data-settings-tab="history" role="tab">History</button>
        ${settingsIsAdmin() ? '<button type="button" data-settings-tab="gateway" role="tab">Gateway</button>' : ''}
        ${settingsIsAdmin() ? '<button type="button" data-settings-tab="billing" role="tab">Billing</button>' : ''}
      </div></details>
    </div>
    <div class="settings-pane" id="settings-general" role="tabpanel" hidden>
      <p class="settings-denied" id="settings-denied" role="status" hidden>That page is for admins.</p>
      ${settingsIsAdmin() ? `<section class="card" id="settings-team"><header><h2>Team</h2></header>
        ${S.me?.cloud && S.me?.role === 'owner' ? `<div class="settings-team-icon">
          <img id="settings-team-icon" alt="Team icon" hidden><span id="settings-team-icon-empty" class="muted">No icon</span>
          <div><strong>Team icon</strong>
            <input id="settings-team-icon-file" type="file" accept="image/png,image/jpeg,image/webp" aria-label="Choose team icon" hidden>
            <button type="button" class="ghost" id="settings-team-icon-change">Choose icon</button>
            <p id="settings-team-icon-status" class="settings-cell-note" role="status"></p>
          </div>
        </div>` : ''}
        <p class="settings-cell-note" id="settings-visuals">${settingsVisualsLine()}</p></section>
      <section class="card" id="settings-company"><header><h2>Tico company</h2></header><div id="set-company"></div></section>` : ''}
      <section class="card" id="settings-assistant-pane"><header><h2>${esc(assistantName())}</h2><div class="row">
        <a class="ghost" href="${ASSISTANT}">Open chat</a>
        ${settingsCanManageBot((S.emps || []).find(e => e.name === assistantBot())) ? `<a class="ghost" href="#/bot/${encodeURIComponent(assistantBot())}/more">Bot settings</a>` : ''}
      </div></header><div id="settings-assistant-approvals"></div></section>
      <div id="settings-assistant"></div>
      ${S.me?.role === 'owner' ? `<section class="card" id="settings-privacy"><header><h2>Privacy</h2></header><div id="set-privacy">${skelRows(3, {card: true, dot: false})}</div></section>
      <section class="card" id="settings-storage"><header><h2>Storage</h2></header><div id="set-storage">${skelRows(3, {card: true, dot: false})}</div></section>` : ''}
    </div>
    <div class="settings-pane" id="settings-devices" role="tabpanel">
      <section class="card" id="settings-computers"><header><h2>Computers</h2><button class="primary settings-head-act" type="button" id="machine-add-open">Add computer</button></header><div id="set-machines">${skelRows(3, {card: true, dot: false})}</div></section>
      ${settingsIsAdmin() ? '<section class="card" id="settings-hosted" hidden><header><h2>Hosted servers</h2></header><div id="set-hosted"></div></section>' : ''}
      <section class="card" id="settings-sleep" hidden><header><h2>Sleep when idle</h2></header><div id="set-sleep"></div></section>
      ${settingsCanMakeTokens() || S.me?.has_tokens ? `<section class="card" id="settings-tokens"><header><h2>API tokens</h2></header><div id="set-tokens">${skelRows(3, {card: true, dot: false})}</div></section>` : ''}
    </div>
    <div class="settings-pane" id="settings-models" role="tabpanel" hidden>
      <section class="card" id="settings-subs"><header><h2>Subscriptions</h2><button class="primary settings-head-act" type="button" id="subs-add" hidden>Add subscription</button></header><div id="set-subs">${skelRows(2, {card: true, dot: false})}</div></section>
      <section class="card" id="settings-gateway-card"><header><h2>Tico Gateway</h2></header><div id="set-gateway-card">${skelRows(1, {card: true, dot: false})}</div></section>
      <section class="card" id="settings-defaults"><header><h2>Defaults</h2></header><div id="set-providers">${skelRows(3, {card: true, dot: false})}</div></section>
      ${settingsIsAdmin() ? '<section class="card" id="settings-limits"><header><h2>Limits</h2></header><div id="set-turn-levels"></div></section>' : ''}
    </div>
    <div class="settings-pane" id="settings-bots" role="tabpanel" hidden>
      <section class="card settings-bots-card"><header><h2>Bots</h2>${settingsCanCreateBots() ? '<div class="row"><button class="primary" type="button" id="settings-add-bot" disabled>Add bot</button><button class="ghost" type="button" id="settings-add-catalog" disabled><span class="sb-wide">Add from template</span><span class="sb-narrow">From template</span></button></div>' : ''}</header><div id="set-bots">${skelRows(3, {card: true, dot: false})}</div></section>
      ${settingsIsAdmin() ? '<section class="card" id="settings-turn-budgets" hidden><header><h2>Run budget</h2></header><div id="set-turn-budgets"></div></section>' : ''}
    </div>
    <div class="settings-pane" id="settings-repos" role="tabpanel" hidden><section class="card repos-card" id="set-repos">${skelRows(3, {card: true, dot: false})}</section></div>
    <div class="settings-pane" id="settings-health" role="tabpanel" hidden><div class="hl-page" id="hl-page"></div>
      ${hlSetupCheckHTML()}
      <section class="card"><header><h2>Services</h2></header><div id="set-services">${skelRows(3, {card: true, dot: false})}</div></section></div>
    ${settingsIsAdmin() ? `<div class="settings-pane" id="settings-gateway" role="tabpanel" hidden><div id="set-gateway-page">${skelRows(3, {card: true, dot: false})}</div></div>` : ''}
    ${settingsIsAdmin() ? `<div class="settings-pane" id="settings-billing" role="tabpanel" hidden><section class="card"><header><h2>Billing</h2></header><div id="set-billing">${skelRows(3, {card: true, dot: false})}</div></section></div>` : ''}
    ${settingsIsAdmin() ? `<div class="settings-pane" id="settings-people" role="tabpanel" hidden><div id="set-people">${skelRows(3, {card: true, dot: false})}</div></div>` : ''}
    <div class="settings-pane" id="settings-system" role="tabpanel" hidden>
      <section class="card" id="set-system-card"><header><h2>What Tico runs on its own</h2></header><div id="set-system-routines">${skelRows(3, {card: true, dot: false})}</div></section>
      <section class="card" id="set-release-card" hidden><header><h2>Releases</h2></header><div id="set-release"></div></section>
      <section class="card" id="set-task-manager-card" hidden><header><h2>Task Manager</h2><span class="pill">Built-in</span></header><div id="set-task-manager"></div></section>
      ${settingsStrategyBotShown() ? `<section class="card" id="set-strategy-bot-card"><header><h2>${esc(strategyBotName())}</h2><span class="pill">Built-in</span>
        <div class="row"><a class="ghost" href="#/bot/goal-manager/more">Bot settings</a></div></header><div id="set-strategy-bot">${skelRows(3, {card: true, dot: false})}</div></section>` : ''}
      ${settingsIsAdmin() ? '<section class="card" id="set-screening-card" hidden><header><h2>Screening</h2></header><div id="set-screening"></div></section>' : ''}
    </div>
    <div class="settings-pane" id="settings-webhooks" role="tabpanel" hidden>
      <section class="card"><header><h2>Webhooks</h2><div class="theme-seg" id="settings-webhooks-view" role="radiogroup" aria-label="Webhooks">${WH_TABS.map(([k, wide, narrow]) =>
        `<button type="button" role="radio" data-wh-tab="${k}" aria-checked="${k === WH.tab}">${narrow ? `<span class="sb-wide">${wide}</span><span class="sb-narrow">${narrow}</span>` : wide}</button>`).join('')}</div></header>
        <div id="set-webhooks">${skelRows(2, {card: true, dot: false})}</div></section>
    </div>
    <div class="settings-pane" id="settings-tasks" role="tabpanel" hidden>
      <section class="card" id="set-ask-filter-card"><header><h2>What bots may put on you</h2></header><div id="set-ask-filter"></div></section>
      <div id="settings-types"></div>
      <section class="card" id="settings-roadmaps"></section>
      <section class="card"><header><h2>Task tags</h2></header><div id="settings-tags"></div></section>
    </div>
    <div class="settings-pane" id="settings-inbound" role="tabpanel" hidden><section class="card" id="set-inbound"></section></div>
    <div class="settings-pane" id="settings-mail" role="tabpanel" hidden><div id="set-mail">${skelRows(3, {card: true, dot: false})}</div></div>
    <div class="settings-pane" id="settings-inbox" role="tabpanel" hidden><section class="card"><header><h2>Your Google account</h2></header><div id="set-google"></div></section></div>
    <div class="settings-pane" id="settings-history" role="tabpanel" hidden><section class="card"><header><h2>History</h2>
      ${S.me?.role === 'owner' ? '<div class="theme-seg" id="settings-history-view" role="radiogroup" aria-label="History"><button type="button" role="radio" data-history-view="runs">Runs</button><button type="button" role="radio" data-history-view="changes">Changes</button></div>' : ''}</header>
      <div id="set-runs">${skelRows(3, {card: true, dot: false})}</div>
      ${S.me?.role === 'owner' ? `<div id="set-history" hidden>${skelRows(3, {card: true, dot: false})}</div>` : ''}</section></div>
    <dialog class="tmodal" id="people-dialog" aria-label="People"></dialog>
    <dialog class="owner-picker" id="owner-picker" aria-labelledby="owner-picker-title"></dialog>
    <dialog class="bot-editor" id="bot-editor" aria-labelledby="bot-editor-title"></dialog>
    <dialog class="bot-editor catalog-picker" id="catalog-picker" aria-labelledby="catalog-picker-title"></dialog>`;
  taskTypesSettingsMount();
  $('#settings-app-log')?.addEventListener('click', () => nativeHandler('showLog')?.postMessage(null));
  pageSettingsThemeBind();
  settingsTeamIconBind();
  $('#settings-history-view')?.addEventListener('click', event => {
    const button = event.target.closest('[data-history-view]');
    if (button) settingsHistoryShow(button.dataset.historyView);
  });
  $('#settings-tabs').onclick = event => {
    const button = event.target.closest('[data-settings-tab]');
    if (button) settingsShow(button.dataset.settingsTab);
  };
  // Opening More on a phone shows its tabs at the end of the scrolling row, or the current one when it is in More.
  $('#settings-more').addEventListener('toggle', event => {
    if (event.target.open) settingsTabIntoView(event.target.querySelector('[data-settings-tab].cur') || event.target.lastElementChild);
  });
  settingsShow(SETTINGS_TAB);
  loadSettings();
}
// The server's version (and the release Tico support will update a hosted one to), and the desktop app's own when the page runs inside it (app/src/main.rs bridge_script), with
// a way to the app's log file (app/src/applog.rs) when the shell keeps one (it says so in ticoNativeFeatures).
function settingsVersionHTML() {
  const parts = [S.config.version && `Version ${esc(nvVersion(S.config.version))}`,
    // Tico support's scheduled update of a hosted server (backend/hosted_updates.py).
    S.config.update?.scheduled && `Tico will update this server to ${esc(nvVersion(S.config.update.scheduled))} when idle`,
    window.ticoNativeVersion && `App ${esc(nvVersion(window.ticoNativeVersion))}`,
    (window.ticoNativeFeatures || []).includes('showLog') && '<button class="settings-log" type="button" id="settings-app-log">Show the app\'s log</button>'].filter(Boolean);
  return parts.length ? `<p class="settings-version" id="settings-version">${parts.join(' · ')}</p>` : '';
}
// Theme: this browser's choice (ui/theme.js); dark when none is saved.
const SETTINGS_THEMES = [['dark', 'Dark'], ['light', 'Light'], ['system', 'System']];
function settingsThemeHTML() {
  const cur = window.ticoTheme?.get() || 'dark';
  return `<div class="theme-seg" id="settings-theme" role="radiogroup" aria-label="Theme">${SETTINGS_THEMES.map(([v, label]) =>
    `<button type="button" role="radio" data-theme-choice="${v}" aria-checked="${v === cur}">${label}</button>`).join('')}</div>`;
}
function pageSettingsThemeBind() {
  const seg = $('#settings-theme');
  if (seg) seg.onclick = event => {
    const button = event.target.closest('[data-theme-choice]');
    if (!button) return;
    window.ticoTheme?.set(button.dataset.themeChoice);
    seg.querySelectorAll('[data-theme-choice]').forEach(b => b.setAttribute('aria-checked', String(b === button)));
  };
}
function settingsShow(tab) {
  if (tab === 'credentials' || tab === 'cloud') { location.hash = INTEGRATIONS; return; }
  tab = SETTINGS_ALIAS[tab] || tab;
  if (tab === 'tags' || tab === 'types') tab = 'tasks'; // Saved links keep working.
  if (tab === 'triggers') tab = 'webhooks';             // Settings > Triggers became Webhooks
  if (tab === 'devices' && SETTINGS_MOVED_TO_MODELS.includes(SETTINGS_REVEAL)) tab = 'models';
  // The Strategy Bot's settings were a tab of their own; they are a helper's card in System now.
  if (tab === 'strategy-bot') { tab = 'system'; SETTINGS_REVEAL = '#set-strategy-bot-card'; }
  if (SETTINGS_GENERAL[tab]) { SETTINGS_REVEAL = SETTINGS_GENERAL[tab]; tab = 'general'; }
  if (tab === 'runs') { tab = 'history'; SETTINGS_HISTORY_VIEW = 'runs'; }   // the Runs page moved here
  const tabs = [...document.querySelectorAll('#settings-tabs [data-settings-tab]')];
  // Tab names come from the navigation; recheck access in case this person's role just changed.
  const owner = S.me?.role === 'owner';
  const access = {people: settingsIsAdmin(), billing: settingsIsAdmin(), gateway: settingsIsAdmin()};
  const shown = access[tab] !== false && tabs.some(button => button.dataset.settingsTab === tab);
  // A tab this person can't see (an admin's link, a role just lost) opens General and says why.
  SETTINGS_DENIED = !shown && SETTINGS_TABS.includes(tab) && tab !== 'general';
  SETTINGS_TAB = shown ? tab : 'general';
  const denied = $('#settings-denied');
  if (denied) denied.hidden = !SETTINGS_DENIED;
  try { sessionStorage.setItem(SETTINGS_TAB_KEY, SETTINGS_TAB); } catch { /* private window: the tab is just not remembered */ }
  settingsAddress();
  if (['health', 'history', 'system', 'webhooks', 'inbound', 'mail', 'gateway', 'billing'].includes(SETTINGS_TAB)) $('#settings-more').open = true;
  const tasks = $('#settings-tasks');
  if (tasks) tasks.hidden = SETTINGS_TAB !== 'tasks';
  tabs.forEach(button => {
    const selected = button.dataset.settingsTab === SETTINGS_TAB;
    button.classList.toggle('cur', selected); button.setAttribute('aria-selected', String(selected));
  });
  settingsTabIntoView();
  settingsTags();
  taskTypesSettingsShow(SETTINGS_TAB);
  if (SETTINGS_TAB === 'tasks') { void roadmapSettingsMount(); void askFilterSettingsLoad(); }
  const devices = $('#settings-devices'), models = $('#settings-models'), history = $('#settings-history'), bots = $('#settings-bots');
  if (bots) { bots.hidden = SETTINGS_TAB !== 'bots'; if (SETTINGS_TAB === 'bots') void renderTeamBudgets(); }
  if (devices) devices.hidden = SETTINGS_TAB !== 'devices';
  if (models) models.hidden = SETTINGS_TAB !== 'models';
  settingsMachinesWatch();
  if (settingsMachinesVisible() && SETTINGS_DATA.loaded) void settingsMachinesRefresh();
  // Before the first load the forms have no models or computers to show; loadSettings draws them then.
  if (SETTINGS_DATA.loaded) settingsModelsRender();
  if (history) { history.hidden = SETTINGS_TAB !== 'history'; if (SETTINGS_TAB === 'history') settingsHistoryShow(); }
  const general = $('#settings-general');
  if (general) {
    general.hidden = SETTINGS_TAB !== 'general';
    // Drawn for the role this person had; one lost since keeps its sections hidden.
    const team = $('#settings-team'), privacy = $('#settings-privacy'), storage = $('#settings-storage');
    const company = $('#settings-company');
    if (team) team.hidden = !settingsIsAdmin();
    if (company) company.hidden = !settingsIsAdmin();
    if (privacy) privacy.hidden = storage.hidden = !owner;
    if (SETTINGS_TAB === 'general') {
      void Promise.all([renderSettingsAssistantApprovals(),
        // The owner's one click when the team has no Assistant (ui/assistant.js): restore it or add it.
        owner && window.assistantChat?.settingsStrip($('#settings-assistant'), {get, post, esc, toast, after: async () => { await refresh(true); renderSettingsBots(); }}),
        privacy && owner && renderSettingsPrivacy(), storage && owner && renderSettingsStorage(),
        company && settingsIsAdmin() && renderSettingsCompany()]).then(() => settingsReveal(SETTINGS_REVEAL));
    }
  }
  const peoplePane = $('#settings-people');
  if (peoplePane) { peoplePane.hidden = SETTINGS_TAB !== 'people'; if (SETTINGS_TAB === 'people' && !formBusy(peoplePane)) void renderSettingsPeople(); }
  const billingPane = $('#settings-billing');
  if (billingPane) { billingPane.hidden = SETTINGS_TAB !== 'billing'; if (SETTINGS_TAB === 'billing') void renderSettingsBilling(); }
  const gatewayPane = $('#settings-gateway');
  if (gatewayPane) { gatewayPane.hidden = SETTINGS_TAB !== 'gateway'; if (SETTINGS_TAB === 'gateway') void renderSettingsGateway(); }
  const healthPane = $('#settings-health');
  if (healthPane) { healthPane.hidden = SETTINGS_TAB !== 'health'; if (SETTINGS_TAB === 'health') window.hlMount?.(); }
  const inboundPane = $('#settings-inbound');
  if (inboundPane) { inboundPane.hidden = SETTINGS_TAB !== 'inbound'; if (SETTINGS_TAB === 'inbound') void inboundMount(); }
  const mailPane = $('#settings-mail');
  if (mailPane) { mailPane.hidden = SETTINGS_TAB !== 'mail'; if (SETTINGS_TAB === 'mail' && !formBusy(mailPane)) void renderSettingsMail(); }
  const inboxPane = $('#settings-inbox');
  if (inboxPane) { inboxPane.hidden = SETTINGS_TAB !== 'inbox'; if (SETTINGS_TAB === 'inbox') void window.mountGoogleConnect?.($('#set-google')); }
  const reposPane = $('#settings-repos');
  if (reposPane) { reposPane.hidden = SETTINGS_TAB !== 'repos'; if (SETTINGS_TAB === 'repos' && !formBusy(reposPane)) void renderSettingsRepos(); }
  const system = $('#settings-system');
  if (system) { system.hidden = SETTINGS_TAB !== 'system'; if (SETTINGS_TAB === 'system') { void renderSystemRoutines(); void renderReleaseCard(); void renderTaskManagerCard(); void renderScreeningCard(); void renderSettingsStrategyBot(); } }
  const webhooksPane = $('#settings-webhooks');
  if (webhooksPane) { webhooksPane.hidden = SETTINGS_TAB !== 'webhooks'; if (SETTINGS_TAB === 'webhooks' && !formBusy(webhooksPane)) void renderSettingsWebhooks(); }
  settingsRevealSoon(SETTINGS_REVEAL);
}
// While Settings is the page, the address bar names the tab shown: #/settings/<tab>. It replaces the entry: no
// hashchange, no new history step.
function settingsAddress() {
  if (S.route !== SETTINGS) return;
  SETTINGS_ADDRESS = `${SETTINGS}/${SETTINGS_TAB}`;
  if (location.hash !== SETTINGS_ADDRESS) history.replaceState(history.state, '', SETTINGS_ADDRESS);
}
// On a phone the tabs are one row that scrolls sideways (styles/settings.css); bring the current one into view.
// Only the row scrolls: scrollIntoView would also move the page, which a refresh must not do.
function settingsTabIntoView(target) {
  const bar = $('#settings-tabs'), cur = target || bar?.querySelector('[data-settings-tab].cur');
  if (!cur || bar.scrollWidth <= bar.clientWidth) return;
  const box = bar.getBoundingClientRect(), tab = cur.getBoundingClientRect(), pad = 24;
  if (tab.left < box.left) bar.scrollLeft -= box.left - tab.left + pad;
  else if (tab.right > box.right) bar.scrollLeft += tab.right - box.right + pad;
}
function settingsTeamIconBind() {
  const input = $('#settings-team-icon-file'), button = $('#settings-team-icon-change');
  if (!input || !button) return;
  const image = $('#settings-team-icon'), empty = $('#settings-team-icon-empty'), status = $('#settings-team-icon-status');
  image.onload = () => { image.hidden = false; empty.hidden = true; button.textContent = 'Change icon'; };
  image.onerror = () => { image.hidden = true; empty.hidden = false; };
  image.src = API + '/v2/team/icon';
  button.onclick = () => input.click();
  input.onchange = async () => {
    const file = input.files?.[0];
    if (!file) return;
    status.classList.remove('err');
    if (file.size > 1048576 || !['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
      status.textContent = file.size > 1048576 ? 'Choose an image up to 1 MB.' : 'Choose a PNG, JPEG or WebP image.';
      status.classList.add('err'); input.value = ''; return;
    }
    button.disabled = input.disabled = true; status.textContent = 'Saving…';
    try {
      const response = await formFetch(API + '/v2/team/icon', file);
      const result = await response.json().catch(() => ({}));
      if (!response.ok) { signInRedirect(response, result); throw apiFailure(result, response); }
      if (result.url !== '/api/v2/team/icon') throw new Error('The server did not confirm this icon. Please retry.');
      image.src = API + '/v2/team/icon?v=' + Date.now();
      status.textContent = ''; toast('Saved');
    } catch (error) { status.textContent = error.message; status.classList.add('err'); }
    finally { button.disabled = input.disabled = false; input.value = ''; }
  };
}
// Settings > General > Assistant: the person's own switch for their Assistant deciding approvals addressed to them, and what it
// decided, each undoable until it has run (backend/assistant.py decides, approval_undo). Only they see or change it.
async function renderSettingsAssistantApprovals() {
  const host = $('#settings-assistant-approvals');
  if (!host || formBusy(host)) return;
  let v, list;
  try { [v, list] = await Promise.all([get('/v2/assistant/settings'), get('/v2/assistant/decisions')]); }
  catch (e) { host.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const name = esc(assistantName());
  const rows = (list.decisions || []).map(d => {
    const words = d.decided_for?.words ? `“${esc(d.decided_for.words)}”` : '';
    return `<li class="people-line"><span><strong>${d.decision === 'approved' ? 'Approved' : 'Declined'}</strong> ${esc(d.what)}
      <span class="settings-cell-note">${esc(ago(d.decided_at))}${words ? ` · you said ${words}` : ''}${d.consumed_at ? ' · already ran' : ''}</span></span>
      ${d.can_undo ? `<button type="button" class="ghost" data-undo-approval="${esc(d.id)}">Undo</button>` : ''}</li>`;
  }).join('');
  host.innerHTML = `<h3 id="settings-assistant-approvals-h">Approvals</h3>
    <label class="people-line"><input type="checkbox" role="switch" class="people-switch" id="assistant-decides"${v.decides_approvals ? ' checked' : ''}${v.team_allows ? '' : ' disabled'} aria-label="My assistant can decide approvals for me">
      <span>My assistant can decide approvals for me</span></label>
    <p class="settings-cell-note">${v.team_allows
      ? `When you ask in its chat, your ${name} approves or declines your approvals. It reads the list back first, and only your own words count.`
      : 'Your team’s owner has turned this off for everyone (Settings > People).'}</p>
    ${rows ? `<h3>Decided by ${name} for you</h3><ul class="assistant-decisions">${rows}</ul>` : ''}`;
  $('#assistant-decides').onchange = async ev => {
    const box = ev.target;
    try { await post('/v2/assistant/settings', {decides_approvals: box.checked}); toast('Saved'); void renderSettingsAssistantApprovals(); }
    catch (e) { box.checked = !box.checked; settingsFieldError(box, e.message); }
  };
  host.querySelectorAll('[data-undo-approval]').forEach(button => button.onclick = async () => {
    button.disabled = true;
    try { await post(`/v2/approvals/${encodeURIComponent(button.dataset.undoApproval)}/undo`); toast('Undone: it is waiting for you again'); }
    catch (e) { toast(e.message, true); }
    void renderSettingsAssistantApprovals();
  });
}
// Settings > General > Privacy: the one switch for the anonymous usage count, and a new random install ID (PRIVACY.md).
async function renderSettingsPrivacy() {
  const host = $('#set-privacy');
  if (!host) return;
  let v;
  try { v = await get('/v2/system/usage-count'); } catch (e) { host.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const forced = v.off_by && v.off_by !== 'setting';
  host.innerHTML = `<label class="privacy-row"><input type="checkbox" id="privacy-count"${v.enabled ? ' checked' : ''}${forced ? ' disabled' : ''}> Help count active installs</label>
    <p class="settings-cell-note">${forced ? (v.off_by === 'demo' ? 'Always off in the demo. ' : `Turned off by ${esc(v.off_by)}. `) : ''}A random ID, the version and three yes/no activity flags. <a href="${esc(v.doc)}" target="_blank" rel="noopener noreferrer">What is sent</a></p>
    <p class="settings-cell-note">${v.install_id ? `Install ID: <code>${esc(v.install_id)}</code> <button class="ghost" type="button" id="privacy-reset">Reset install ID</button>` : 'No install ID yet: one is made the first time the count is sent.'}</p>`;
  $('#privacy-count').onchange = async ev => {
    try { await put('/v2/system/usage-count', {enabled: ev.target.checked}); toast('Saved'); void renderSettingsPrivacy(); }
    catch (e) { ev.target.checked = !ev.target.checked; settingsFieldError(ev.target, e.message); }
  };
  if ($('#privacy-reset')) $('#privacy-reset').onclick = async ev => {
    const button = ev.currentTarget;
    if (!await settingsConfirm({title: 'Reset install ID?', ok: 'Reset install ID',
      text: 'Tico makes a new random ID. The old one is gone for good, so earlier counts can no longer be linked to this install.'})) return;
    try { await post('/v2/system/usage-count/reset'); toast('Saved'); void renderSettingsPrivacy(); } catch (e) { settingsFieldError(button, e.message); }
  };
}
// Models draws its cards when shown; Computers reads the subscriptions and the Gateway toggle for its pickers.
function settingsModelsRender() {
  if (SETTINGS_TAB === 'models') { void renderSettingsSubs(); void renderSettingsProviders(); void renderSettingsTurnLevels(); }
  else if (SETTINGS_TAB === 'devices' && S.me?.cloud) {
    void Promise.all([subsLoad(), providersViewLoad().catch(() => null)]).then(() => { if (settingsMachinesVisible()) renderSettingsMachines(); });
  }
}
async function renderSettingsTurnLevels() {
  const host = $('#set-turn-levels');
  if (!host || !settingsIsAdmin() || formBusy(host) || host.dataset.loading) return;
  host.dataset.loading = 'true';
  try {
    const value = await get('/v2/settings/turn-levels', {fresh: true});
    if (!host.isConnected || !settingsIsAdmin() || formBusy(host)) return;
    host.innerHTML = `<form id="turn-levels-form" class="models-form">
      <div class="models-row"><span class="k"><label for="turn-levels-warn">Warn at</label>${helpBubble('Per person who can sign in, across all company computers. At warn, Tico opens a Health incident; at stop, new runs wait.')}</span>
        <span class="models-num"><input id="turn-levels-warn" name="warning_per_person" type="number" required min="1" max="10000" value="${value.warning_per_person}"><span class="muted">runs per person</span></span></div>
      <div class="models-row"><span class="k"><label for="turn-levels-stop">Stop at</label></span>
        <span class="models-num"><input id="turn-levels-stop" name="stop_per_person" type="number" required min="1" max="10000" value="${value.stop_per_person}"><span class="muted">runs per person</span></span></div>
      <div class="onb-actions"><button class="primary" type="submit">Save</button><span class="muted" data-turn-levels-status role="status">${value.people} ${value.people === 1 ? 'person' : 'people'} · ${value.in_use} of ${value.stop} in use</span></div>
    </form>`;
    const form = host.querySelector('form');
    form.onsubmit = async event => {
      event.preventDefault();
      const status = form.querySelector('[data-turn-levels-status]'), button = form.querySelector('button');
      const warning = Number(form.elements.warning_per_person.value), stop = Number(form.elements.stop_per_person.value);
      if (warning > stop) { status.textContent = 'Warn at must not be above Stop at.'; status.classList.add('err'); return; }
      button.disabled = true; status.textContent = ''; status.classList.remove('err');
      try {
        const saved = await post('/v2/settings/turn-levels', {warning_per_person: warning, stop_per_person: stop});
        toast('Saved');
        status.textContent = `Warn at ${saved.warning} · stop at ${saved.stop} company runs`;
      } catch (error) { status.textContent = error.message; status.classList.add('err'); }
      finally { button.disabled = false; }
    };
  } catch (error) { host.textContent = error.message; }
  finally { delete host.dataset.loading; }
}
async function loadSettings() {
  try {
    const [people, operations, catalog, employees, history, limits] = await Promise.all([
      get('/humans'),
      S.me?.cloud ? settingsMachinesRead().catch(() => {
        SETTINGS_PRESENCE.failed = true;
        return {...SETTINGS_DATA, scheduler_enabled: SETTINGS_DATA.schedulerEnabled};
      }) : Promise.resolve({machines: [], services: []}),
      S.me?.cloud ? get('/v2/models') : Promise.resolve({models: []}),
      get('/employees'),
      S.me?.cloud && S.me?.role === 'owner' ? get('/v2/settings/history') : Promise.resolve({changes: [], transitions: []}),
      S.me?.cloud ? get('/v2/usage/limits').catch(() => null) : Promise.resolve(null),
      loadHarnessRows(),
      // The Computers table's pickers: the company's subscriptions, and whether the Gateway runs a tool without one.
      S.me?.cloud ? Promise.all([subsLoad(), providersViewLoad().catch(() => null)]) : null
    ]);
    S.emps = namedRoster(employees);
    setPeople(people);
    SETTINGS_DATA = {people: (people.people || []).filter(p => !p.hidden), machines: S.me?.cloud ? SETTINGS_DATA.machines : [],
      runnable: operations.runnable || {},
      agents: operations.agents || [],
      services: operations.services || [], models: catalog.models || [],
      harnesses: catalog.harnesses || [], gateway: catalog.gateway || null,
      enabledProviders: catalog.enabled_providers || [], defaultModel: catalog.default?.model || '',
      issues: operations.issues || [], history, limits,
      schedulerEnabled: operations.scheduler_enabled, loaded: true};
    document.dispatchEvent(new Event('tico:settings-loaded'));
    renderTree();
    if (S.route !== SETTINGS) return;
    renderSettingsIssues(); renderSettingsBots(); renderSettingsMachines(); renderSettingsServices(); renderSettingsHistory();
    void renderSettingsTokens();
    void renderSettingsHosted();
    void renderSettingsSleep();
    if (SETTINGS_TAB === 'models') settingsModelsRender();
    const add = $('#settings-add-bot');
    if (add) { add.onclick = () => void settingsRecruitFlow(); add.disabled = false; }
    const fromCatalog = $('#settings-add-catalog');
    if (fromCatalog) { fromCatalog.onclick = () => void settingsCatalogPicker(); fromCatalog.disabled = false; }
    settingsRecruitHandoff();
  } catch (error) {
    for (const id of ['#set-bots','#set-machines','#set-services','#set-history']) {
      const el = $(id); if (el) el.innerHTML = `<div class="err">${esc(error.message || 'Settings could not be loaded')}</div>`;
    }
  }
}
function renderSettingsIssues() {
  const el = $('#settings-issues'); if (!el) return;
  // Only what needs a person heads Settings; the rest is listed under Health, so the two never disagree.
  const issues = (SETTINGS_DATA.issues || []).filter(needsPerson);
  const urgent = issues.length > 0;
  el.innerHTML = issues.length ? `<details class="card settings-issues"><summary><span class="dot ${urgent ? 'failed' : ''}" aria-hidden="true"></span><strong>System checks <span class="tnum">${issues.length}</span></strong></summary>
    <div>${issues.map(issue => `<div class="settings-issue"><div><strong>${esc(issue.title)}</strong>${needsPerson(issue) ? '' : ` <span class="tag">${issue.kind === 'uncertain_work' ? 'review later' : 'not urgent'}</span>`}<p>${esc(issue.detail)}</p>${issue.action && issue.action !== 'review' ? `<p>${esc(issue.action)}</p>` : ''}</div>${issue.kind === 'uncertain_work' ? `<button class="ghost" data-execution-review="${esc(issue.bot)}">Review stopped runs</button>` : ''}${runnerRestartButton(issue)}${issue.bot ? `<a class="ghost" href="#/bot/${esc(issue.bot)}/more">Open bot</a>` : ''}</div>`).join('')}</div></details>` : '';
  el.querySelectorAll('[data-execution-review]').forEach(button => {
    button.onclick = () => executionReview(button.dataset.executionReview);
  });
  bindRunnerRestart(el);
}
// The stopped-run card is the decision only (Chris, Oct 22: "remove 80%, give me clear action items"): one line
// on what stopped, one recommendation with its button first, the rest under Details. `stopped` comes from
// backend/execution.py review_jobs, by the rule its sweep retries by (retry_facts): a run that only read, on a host
// that reports every call, is safe to try again; one that may have acted, left no full record or keeps stopping is
// not retried unless the person knows better.
const STOPPED_PICK = {
  read: {retry: true, what: () => 'before changing anything', say: () => 'Try again: it only read, so nothing repeats.'},
  restarted: {retry: true, what: () => 'when its computer restarted',
    say: () => 'Try again: check what already happened first, then continue.'},
  timeout: {retry: true, what: () => 'at its run time limit',
    say: () => 'Try again: check what already happened first, then continue.'},
  changed: {retry: false, what: tools => `after using ${tools || 'an approval or a tool that can change things'}`,
    say: tools => `Don’t retry unless you know ${tools || 'that action'} didn’t finish. It could run twice.`},
  twice: {retry: false, what: () => 'the same way more than once',
    say: () => 'Don’t retry: it keeps stopping. Check the task first.'},
  unknown: {retry: false, what: () => 'before answering',
    say: () => 'Don’t retry unless you know it did nothing. Tico has no record of what it did.'},
};
// A decision on one stopped request, from the review card or a Needs you row (heartbeat.js): the same note to the bot
// either way, and the same receipt line.
const STOPPED_NOTE = {
  resume: 'The human chose to retry this request. Check existing work and external effects before repeating any step.',
  dismiss: 'The human chose not to retry this delivery. Inspect the task separately.',
};
const reconcileStopped = (job, decision, detail = '') => post(`/v2/jobs/${encodeURIComponent(job.id)}/reconcile`, {
  attempt_id: job.attempt_id, decision, note: detail ? `${STOPPED_NOTE[decision]} Details: ${detail}` : STOPPED_NOTE[decision],
  acknowledge_uncertain_effects: true
});
const stoppedOutcome = (decision, deferred) => decision === 'mixed'
  ? 'Choices saved. The bot is busy; Tico will apply them when its turn ends.'
  : deferred ? `Choice saved. The bot is busy; Tico will ${decision === 'resume' ? 'try again' : 'dismiss this run'} when its turn ends.`
  : decision === 'resume' ? 'Queued to try again.' : 'Dismissed. Tico won’t retry this run.';
// `after` redraws the page it was opened from once a run is decided: Settings, or the bot's own header.
async function executionReview(bot, after = () => $('#settings-tabs') && loadSettings()) {
  const dialog = document.createElement('dialog'); dialog.className = 'tmodal tmodal-shell stopped-runs-dialog';
  dialog.setAttribute('aria-label', 'Stopped runs');
  // Open bot lives here, not on the Needs you card that opens this.
  dialog.innerHTML = `<header class="tmodal-head"><h2>Stopped runs</h2><span class="spacer"></span><a class="ghost" href="#/bot/${esc(bot)}/more" data-open-bot>Open bot</a><button class="ghost" type="button" data-close>Close</button></header><div class="tmodal-body" data-review>Loading…</div>`;
  dialog.querySelector('[data-close]').onclick = () => dialog.close();
  dialog.querySelector('[data-open-bot]').onclick = () => dialog.close();
  dialog.addEventListener('close', () => dialog.remove());
  document.body.appendChild(dialog); dialog.showModal();
  const host = dialog.querySelector('[data-review]');
  try {
    const {jobs} = await get(`/v2/bots/${encodeURIComponent(bot)}/execution-review`);
    host.innerHTML = jobs.length ? jobs.map(job => {
      const saved = job.attempt?.final_text || job.output.map(event=>event.text).filter(Boolean).join('');
      const pick = STOPPED_PICK[job.stopped] || STOPPED_PICK.unknown, retry = pick.retry;
      const tools = (job.acted || []).join(', ');
      const button = (decision, label) => `<button type="submit" name="decision" value="${decision}"${(decision === 'resume') === retry ? '' : ' class="ghost"'}>${label}</button>`;
      return `<form data-job="${esc(job.id)}" class="stopped-run">
      <h3>${esc(job.task?.title || 'Interrupted conversation')}</h3>
      <p data-stopped>Stopped ${esc(fmt(job.attempt?.started_at || job.attempt?.created_at))} ${esc(pick.what(tools))}.</p>
      <p data-pick><strong>${esc(pick.say(tools))}</strong></p>
      <details data-details><summary>Details</summary>
        ${job.task ? `<p class="muted">Task ${esc(job.task.status)}${job.task.owner_name ? `, owner ${esc(job.task.owner_name)}` : ''}</p>` : ''}
        <p class="muted">${esc(job.reason)}</p>
        <details><summary>Original request</summary><div style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(job.request)}</div></details>
        <details><summary>${saved ? 'What the run saved' : 'No answer was saved'}</summary><div data-saved style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:240px;overflow:auto">${esc(saved || 'The run ended without saving an answer.')}</div></details>
        ${job.tools?.length ? `<details><summary>Tool calls (${job.tools.length})</summary><ul class="review-tools">${job.tools.map(call => `<li${call.nested ? ' class="nested"' : ''}><code>${esc(call.tool)}</code>${call.summary ? ` ${esc(call.summary)}` : ''}${call.status ? ` <span class="muted">· ${esc(call.status)}</span>` : ''}</li>`).join('')}</ul></details>` : ''}
        <label>Note for the bot <span class="muted">Optional</span><textarea name="note" aria-label="Details for Tico" maxlength="4000" placeholder="For example: the draft exists, but nothing was sent."></textarea></label>
      </details>
      <div class="stopped-run-actions">
        ${retry ? '' : '<p data-retry-warning>Trying again could repeat work that already finished.</p>'}
        <div class="row">${retry ? button('resume', 'Try again') + button('dismiss', 'Don’t retry') : button('dismiss', 'Don’t retry') + button('resume', 'Try again')}</div>
        <p data-result role="status" aria-live="polite"></p>
      </div>
    </form>`; }).join('') : '<p>No stopped runs remain.</p>';
    host.querySelectorAll('form').forEach(form => {
      const job = jobs.find(row => row.id === form.dataset.job);
      const result = form.querySelector('[data-result]'), buttons = form.querySelectorAll('button');
      if (job.pending_decision) {
        result.textContent = stoppedOutcome(job.pending_decision.decision, true);
        buttons.forEach(button => button.disabled = true);
      }
      form.onsubmit = async event => {
        event.preventDefault();
        const decision = event.submitter?.value;
        if (!decision || form.dataset.resolved || job.pending_decision) return;
        buttons.forEach(button => button.disabled = true);
        result.classList.remove('err'); result.textContent = 'Saving choice…';
        result.scrollIntoView({block: 'nearest'});
        try {
          const done = await reconcileStopped(job, decision, form.elements.note.value.trim());
          form.dataset.resolved = 'true';
          form.querySelector('[data-details]').open = false;
          result.textContent = stoppedOutcome(decision, done.deferred);
        } catch(error) {
          result.classList.add('err'); result.textContent = error.message;
          buttons.forEach(button => button.disabled = false);
          result.scrollIntoView({block: 'nearest'});
          return;
        }
        // Refreshing another page is independent of recording the decision. Keep the receipt if it fails.
        try { await after(); }
        catch { toast('Choice saved. Refresh the page to update the list.', true); }
      };
    });
  } catch(error) { host.textContent=error.message; }
}
function renderSettingsServices() {
  const el = $('#set-services'); if (!el) return;
  const rows = SETTINGS_DATA.services;
  el.innerHTML = `<div class="settings-services-list">${rows.map(service => `<div><strong>${esc(service.name || service.service)}</strong> · ${service.last_error ? `<span class="err">${esc(service.last_error)}</span>` : '<span class="ok">healthy</span>'}${service.last_succeeded_at ? `<span class="settings-cell-note">${esc(ago(service.last_succeeded_at))}</span>` : ''}</div>`).join('') || '<span class="muted">No health reports yet.</span>'}</div>`;
}
