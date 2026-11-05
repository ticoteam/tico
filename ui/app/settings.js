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
const settingsMachinesVisible = () => S.route === SETTINGS && SETTINGS_TAB === 'computers' && !!$('#set-machines');
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
// Each model CLI's capabilities, from its manifest (GET /v2/models `harnesses`, runner-rs/harnesses/*.toml): what the UI
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
// The group shown (SETTINGS_GROUPS key), remembered per browser tab so a reload lands where the person was; a first
// visit opens You. Anything else set here (an old tab's name, from a link or the router) is mapped by settingsShow.
let SETTINGS_TAB = (() => { try { return sessionStorage.getItem(SETTINGS_TAB_KEY) || 'you'; } catch { return 'you'; } })();
// The address the shown group writes (settingsAddress) so it can be copied, bookmarked or shared; route() reads it as Settings.
let SETTINGS_ADDRESS = '';
// Inboxes keeps its own list, settings sections and detail addresses within the Connections group (ui/app/inboxes.js).
// The groups, in the list's order (settings stage 2, spec Appendix C): You is personal and sits above a divider, then
// the company's ten. `shown` is who sees the group; its address is #/settings/<key>.
const SETTINGS_GROUPS = [
  {key: 'you', label: 'You'},
  {key: 'people', label: 'People', shown: () => settingsIsAdmin()},
  {key: 'bots', label: 'Bots'},
  {key: 'models', label: 'AI models'},
  {key: 'computers', label: 'Computers'},
  {key: 'limits', label: 'Limits & billing', shown: () => settingsIsAdmin()},
  {key: 'automation', label: 'Automation'},
  {key: 'tasks', label: 'Tasks'},
  {key: 'connections', label: 'Connections'},
  {key: 'policies', label: 'Company & policies', shown: () => settingsIsAdmin()},
  {key: 'health', label: 'Health & history'},
];
const settingsGroupShown = key => { const g = SETTINGS_GROUPS.find(x => x.key === key); return !!g && (!g.shown || g.shown()); };
// Every address Settings had before the groups (`#/settings/<tab>`, a tab's other names, the old sections of General):
// the group that holds it now, and the card to scroll to when it is not the group's first. Saved links, bookmarks,
// the settings catalogue's links and the manual keep opening what they opened.
const SETTINGS_PLACES = {
  general: ['you'], assistant: ['you', '#settings-assistant-pane'],
  inbox: ['connections'], google: ['connections'], mailboxes: ['connections'], inboxes: ['connections'],
  humans: ['people'],
  ceo: ['bots', '#set-strategy-bot-card'], 'strategy-bot': ['bots', '#set-strategy-bot-card'],
  devices: ['computers'], subscriptions: ['models', '#settings-subs'],
  providers: ['models'], chat: ['models'],   // chat: the old Chat tab, then the Models card; Models is AI models' first card
  gateway: ['models', '#settings-gateway'], 'turn-levels': ['limits', '#settings-limits'],
  billing: ['limits', '#settings-billing'],
  system: ['automation'], recurring: ['automation'], rules: ['automation'], routines: ['automation'],
  tags: ['tasks', '#settings-tags'], types: ['tasks', '#settings-types'],
  mail: ['connections'], repos: ['connections', '#settings-repos'], repositories: ['connections', '#settings-repos'],
  inbound: ['connections'], 'email-phone': ['connections'],
  'company-inbox': ['connections'],
  webhooks: ['connections', '#settings-webhooks'], triggers: ['connections', '#settings-webhooks'],
  team: ['policies', '#settings-team'], company: ['policies', '#settings-company'],
  privacy: ['policies', '#settings-privacy'], storage: ['policies', '#settings-storage'],
  history: ['health', '#settings-history'], runs: ['health', '#settings-history'],
};
// The names an address may carry: a group, or an old tab or section. A known one this person can't see opens You
// with a line saying so; an unknown name just opens You.
const SETTINGS_TABS = [...SETTINGS_GROUPS.map(g => g.key), ...Object.keys(SETTINGS_PLACES)];
let SETTINGS_DENIED = false;
let SETTINGS_TRANSITION_TIMER = null;
// A card a link points into (an old tab's place, or the card a link names); it scrolls there once drawn.
let SETTINGS_REVEAL = '';
let SETTINGS_SEARCH_HIGHLIGHT = false;
const SETTINGS_MOVED_TO_MODELS = ['#settings-models', '#set-providers', '#set-gateway'];
const settingsReveal = selector => {
  if (!selector || SETTINGS_REVEAL !== selector) return;
  SETTINGS_REVEAL = '';
  const card = $(selector);
  for (let parent = card?.parentElement; parent; parent = parent.parentElement) {
    if (parent instanceof HTMLDetailsElement) parent.open = true;
  }
  card?.scrollIntoView({block: 'start'});
  if (card && SETTINGS_SEARCH_HIGHLIGHT) {
    SETTINGS_SEARCH_HIGHLIGHT = false;
    card.classList.add('settings-search-highlight');
    setTimeout(() => card.classList.remove('settings-search-highlight'), 1800);
  }
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
  if (SETTINGS_TAB === 'credentials' || SETTINGS_TAB === 'cloud') { SETTINGS_TAB = 'you'; location.hash = INTEGRATIONS; return; }
  // A repeat visit to the page already on screen (a refresh, a same-route redraw) keeps it: rebuilding
  // it would snap back to the default group and drop whatever is half typed in a form.
  const built = $('#settings-tabs');
  if (built && built.dataset.role === (S.me?.role || '') && built.dataset.admin === String(settingsIsAdmin())) { settingsShow(SETTINGS_TAB); loadSettings(); return; }
  const groups = SETTINGS_GROUPS.filter(g => !g.shown || g.shown());
  const pane = (key, body) => `<div class="settings-pane" id="settings-group-${key}" data-settings-group="${key}" role="tabpanel" aria-label="${esc(SETTINGS_GROUPS.find(g => g.key === key).label)}" hidden>${body}</div>`;
  $('#main').innerHTML = `<div class="meeting-head"><div><h1>Settings</h1>${settingsVersionHTML()}</div></div>
    <div id="settings-issues"></div>
    <div class="settings-layout">
    <nav class="settings-groups" id="settings-tabs" data-role="${esc(S.me?.role || '')}" data-admin="${settingsIsAdmin()}" aria-label="Settings">
      <div class="settings-search" id="settings-search"></div>
      <select class="settings-group-pick" id="settings-group-pick" aria-label="Settings group">${groups.map(g =>
        `<option value="${g.key}">${esc(g.label)}</option>`).join('')}</select>
      <div class="settings-group-list" role="tablist" aria-orientation="vertical" aria-label="Settings groups">${groups.map(g =>
        `<button type="button" data-settings-tab="${g.key}" role="tab">${esc(g.label)}${g.key === 'health' ? '<span class="hl-alert" data-hl-alert hidden></span>' : ''}</button>${g.key === 'you' ? '<hr class="settings-group-divider">' : ''}`).join('')}</div>
    </nav>
    <div class="settings-panes">
    <p class="settings-denied" id="settings-denied" role="status" hidden>That page is for admins.</p>
    ${pane('you', `<section class="card" id="settings-theme-card"><header><h2>Theme</h2></header>${settingsThemeHTML()}</section>
      <section class="card" id="settings-assistant-pane"><header><h2>${esc(assistantName())}</h2><div class="row">
        <a class="ghost" href="${ASSISTANT}">Open chat</a>
        ${settingsCanManageBot(botRow(assistantBot())) ? `<a class="ghost" href="#/bot/${encodeURIComponent(assistantBot())}/more">Bot settings</a>` : ''}
      </div></header><div id="settings-assistant-approvals"></div></section>
      <div id="settings-assistant"></div>
      `)}
    ${settingsIsAdmin() ? pane('people', '<div id="settings-people"><div id="set-people">' + skelRows(3, {card: true, dot: false}) + '</div></div>') : ''}
    ${pane('bots', `${settingsStrategyBotShown() ? `<section class="card" id="set-strategy-bot-card"><header><h2>${esc(strategyBotName())}</h2><span class="pill">Built-in</span>
        <div class="row"><a class="ghost" href="#/bot/goal-manager/more">Bot settings</a></div></header><div id="set-strategy-bot">${skelRows(3, {card: true, dot: false})}</div></section>` : ''}
      <div id="settings-bots"><section class="card settings-bots-card" id="settings-bots-card"><header><h2>Bots</h2>${settingsCanCreateBots() ? '<div class="row"><button class="primary" type="button" id="settings-add-bot" disabled>Add bot</button><button class="ghost" type="button" id="settings-add-catalog" disabled><span class="sb-wide">Add from template</span><span class="sb-narrow">From template</span></button></div>' : ''}</header><div id="set-bots">${skelRows(3, {card: true, dot: false})}</div></section></div>`)}
    ${pane('models', `<div id="settings-models"><section class="card" id="settings-subs"><header><h2>Subscriptions</h2><button class="primary settings-head-act" type="button" id="subs-add" hidden>Add subscription</button></header><div id="set-subs">${skelRows(2, {card: true, dot: false})}</div></section>
      <section class="card" id="settings-gateway-card"><header><h2>Tico Gateway</h2></header><div id="set-gateway-card">${skelRows(1, {card: true, dot: false})}</div></section>
      ${settingsIsAdmin() ? '<div id="settings-gateway"><div id="set-gateway-page"></div></div>' : ''}
      <section class="card" id="settings-defaults"><header><h2>Defaults</h2></header><div id="set-providers">${skelRows(3, {card: true, dot: false})}</div></section></div>
`)}
    ${pane('computers', `<div id="settings-devices">
      <section class="card" id="settings-computers"><header><h2>Computers</h2><button class="primary settings-head-act" type="button" id="machine-add-open">Add computer</button></header><div id="set-machines">${skelRows(3, {card: true, dot: false})}</div></section>
      ${settingsIsAdmin() ? '<section class="card" id="settings-hosted" hidden><header><h2>Hosted servers</h2></header><div id="set-hosted"></div></section>' : ''}
      <section class="card" id="settings-sleep" hidden><header><h2>Sleep when idle</h2></header><div id="set-sleep"></div></section></div>
`)}
    ${settingsIsAdmin() ? pane('limits', `<section class="card" id="settings-spend-limits" hidden><header><h2>Bot spend limits</h2></header><div id="set-spend-limits"></div></section>
      <section class="card" id="settings-turn-budgets" hidden><header><h2>Run budget</h2></header><div id="set-turn-budgets"></div></section>
      <section class="card" id="settings-limits"><header><h2>Company turn levels</h2></header><div id="set-turn-levels"></div></section>
      <div id="settings-billing"><section class="card"><header><h2>Billing</h2></header><div id="set-billing">${skelRows(3, {card: true, dot: false})}</div></section></div>`) : ''}
    ${pane('automation', `
      <section class="card" id="set-automations-card"><header><h2>Automations</h2></header>
        <p class="settings-card-line"><a href="#/automations">Bot routines, and the rules that wake a bot when something happens.</a></p></section>
      <section class="card" id="set-system-card"><header><h2>What Tico runs on its own</h2></header><div id="set-system-routines">${skelRows(3, {card: true, dot: false})}</div></section>
      <section class="card" id="set-release-card" hidden><header><h2>Releases</h2></header><div id="set-release"></div></section>
      <section class="card" id="set-task-manager-card" hidden><header><h2>Task Manager</h2><span class="pill">Built-in</span></header><div id="set-task-manager"></div></section>
    `)}
    ${pane('tasks', `<div id="settings-tasks">
      <section class="card" id="set-ask-filter-card"><header><h2>What bots may put on you</h2></header><div id="set-ask-filter"></div></section>
      <div id="settings-types"></div>
      <section class="card" id="settings-roadmaps"></section>
      <section class="card" id="settings-tags-card"><header><h2>Task tags</h2></header><div id="settings-tags"></div></section>
    </div>`)}
    ${pane('connections', `<div id="settings-inboxes"><div id="set-inboxes">${skelRows(4, {card: true, dot: false})}</div></div>
      <div id="settings-repos"><section class="card repos-card" id="set-repos">${skelRows(3, {card: true, dot: false})}</section></div>
      <div id="settings-webhooks"><section class="card"><header><h2>Webhooks</h2><div class="theme-seg" id="settings-webhooks-view" role="radiogroup" aria-label="Webhooks">${WH_TABS.map(([k, wide, narrow]) =>
        `<button type="button" role="radio" data-wh-tab="${k}" aria-checked="${k === WH.tab}">${narrow ? `<span class="sb-wide">${wide}</span><span class="sb-narrow">${narrow}</span>` : wide}</button>`).join('')}</div></header>
        <div id="set-webhooks">${skelRows(2, {card: true, dot: false})}</div></section></div>
      ${settingsCanMakeTokens() || S.me?.has_tokens ? `<section class="card" id="settings-tokens"><header><h2>API tokens</h2></header><div id="set-tokens">${skelRows(3, {card: true, dot: false})}</div></section>` : ''}
      <section class="card" id="settings-tools-link"><header><h2>Tools &amp; Credentials</h2></header>
        <p class="settings-card-line">The tools bots use and the credentials they sign in with have their own page. <a href="${INTEGRATIONS}">Open Tools &amp; Credentials</a></p></section>`)}
    ${settingsIsAdmin() ? pane('policies', `<section class="card" id="settings-team"><header><h2>Team</h2></header>
        ${S.me?.cloud && S.me?.role === 'owner' ? `<div class="settings-team-icon">
          <img id="settings-team-icon" alt="Team icon" hidden><span id="settings-team-icon-empty" class="muted">No icon</span>
          <div><strong>Team icon</strong>
            <input id="settings-team-icon-file" type="file" accept="image/png,image/jpeg,image/webp" aria-label="Choose team icon" hidden>
            <button type="button" class="ghost" id="settings-team-icon-change">Choose icon</button>
            <p id="settings-team-icon-status" class="settings-cell-note" role="status"></p>
          </div>
        </div>` : ''}
        <p class="settings-cell-note" id="settings-visuals">${settingsVisualsLine()}</p></section>
      <section class="card" id="settings-policies"><header><h2>Team policies</h2></header><div id="set-policies">${skelRows(3, {card: true, dot: false})}</div></section>
      <section class="card" id="set-screening-card" hidden><header><h2>Screening</h2></header><div id="set-screening"></div></section>
      <section class="card" id="settings-company"><header><h2>Tico company</h2></header><div id="set-company"></div></section>
      ${S.me?.role === 'owner' ? `<section class="card" id="settings-storage"><header><h2>Storage</h2></header><div id="set-storage">${skelRows(3, {card: true, dot: false})}</div></section>
      <section class="card" id="settings-privacy"><header><h2>Privacy</h2></header><div id="set-privacy">${skelRows(3, {card: true, dot: false})}</div></section>` : ''}`) : ''}
    ${pane('health', `<div id="settings-health"><div class="hl-page" id="hl-page"></div>
      ${hlSetupCheckHTML()}
      <section class="card" id="settings-services-card"><header><h2>Services</h2></header><div id="set-services">${skelRows(3, {card: true, dot: false})}</div></section></div>
      <div id="settings-history"><section class="card"><header><h2>History</h2>
      <div class="theme-seg" id="settings-history-view" role="radiogroup" aria-label="History"><button type="button" role="radio" data-history-view="log">Log</button><button type="button" role="radio" data-history-view="runs">Runs</button>${S.me?.role === 'owner' ? '<button type="button" role="radio" data-history-view="changes">Changes</button>' : ''}</div></header>
      <div id="set-log"></div>
      <div id="set-runs" hidden>${skelRows(3, {card: true, dot: false})}</div>
      ${S.me?.role === 'owner' ? `<div id="set-history" hidden>${skelRows(3, {card: true, dot: false})}</div>` : ''}</section></div>`)}
    </div></div>
    <dialog class="tmodal" id="people-dialog" aria-label="People"></dialog>
    <dialog class="owner-picker" id="owner-picker" aria-labelledby="owner-picker-title"></dialog>
    <dialog class="bot-editor" id="bot-editor" aria-labelledby="bot-editor-title"></dialog>
    <dialog class="bot-editor catalog-picker" id="catalog-picker" aria-labelledby="catalog-picker-title"></dialog>`;
  taskTypesSettingsMount();
  $('#settings-app-log')?.addEventListener('click', () => nativeHandler('showLog')?.postMessage(null));
  pageSettingsThemeBind();
  settingsTeamIconBind();
  settingsSearchMount();
  settingsCardsWatch();   // ui/app/settings-cards.js: each card's first line and Ask BotOps
  $('#settings-history-view')?.addEventListener('click', event => {
    const button = event.target.closest('[data-history-view]');
    if (button) settingsHistoryShow(button.dataset.historyView);
  });
  // A group picked in the list (desktop) or the picker (phone) opens at its top: no old card to scroll to.
  $('#settings-tabs').onclick = event => {
    const button = event.target.closest('[data-settings-tab]');
    if (button) { SETTINGS_REVEAL = ''; INBOXES_VIEW = ''; settingsShow(button.dataset.settingsTab, {group: true}); }
  };
  $('#settings-group-pick').onchange = event => { SETTINGS_REVEAL = ''; INBOXES_VIEW = ''; settingsShow(event.target.value, {group: true}); };
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
// Where a name opens: {group, reveal}. A group's own key opens it; an old tab or section opens the group that holds it
// now, at its card. A card a link names (`#/settings/<tab>/<card>`, the settings catalogue's) wins: the group is the
// one the card sits in, wherever it moved.
function settingsPlace(name) {
  const [group, reveal = ''] = SETTINGS_GROUPS.some(g => g.key === name) ? [name] : SETTINGS_PLACES[name] || [name];
  return {group, reveal};
}
function settingsShow(tab, {group: picked = false} = {}) {
  if (SETTINGS_REVEAL === '#settings-models') SETTINGS_REVEAL = '#settings-defaults';
  if (SETTINGS_REVEAL === '#set-turn-levels') SETTINGS_REVEAL = '#settings-limits';
  if (tab === 'credentials' || tab === 'cloud') { location.hash = INTEGRATIONS; return; }
  const place = settingsPlace(tab);
  if (tab === 'runs') SETTINGS_HISTORY_VIEW = 'runs';   // the Runs page moved here
  if (!SETTINGS_REVEAL && place.reveal) SETTINGS_REVEAL = place.reveal;
  const home = !picked && SETTINGS_REVEAL ? $(SETTINGS_REVEAL)?.closest('[data-settings-group]') : null;
  const group = home?.dataset.settingsGroup || place.group;
  // Group names come from the navigation; recheck access in case this person's role just changed. A card drawn
  // only for some roles (Billing, Gateway, Listening, Storage) is not there for the others.
  const shown = settingsGroupShown(group) && !!$(`#settings-tabs [data-settings-tab="${group}"]`)
    && (!place.reveal || picked || !!$(place.reveal));
  // A group or card this person can't see (an admin's link, a role just lost) opens You and says why.
  SETTINGS_DENIED = !shown && SETTINGS_TABS.includes(tab) && !['you', 'general'].includes(tab);
  SETTINGS_TAB = shown ? group : 'you';
  if (!shown) SETTINGS_REVEAL = '';
  const denied = $('#settings-denied');
  if (denied) denied.hidden = !SETTINGS_DENIED;
  try { sessionStorage.setItem(SETTINGS_TAB_KEY, SETTINGS_TAB); } catch { /* private window: the group is just not remembered */ }
  settingsAddress();
  document.querySelectorAll('#settings-tabs [data-settings-tab]').forEach(button => {
    const selected = button.dataset.settingsTab === SETTINGS_TAB;
    button.classList.toggle('cur', selected); button.setAttribute('aria-selected', String(selected));
  });
  const pick = $('#settings-group-pick');
  if (pick) pick.value = SETTINGS_TAB;
  document.querySelectorAll('.settings-pane[data-settings-group]').forEach(pane => { pane.hidden = pane.dataset.settingsGroup !== SETTINGS_TAB; });
  const on = key => SETTINGS_TAB === key;
  const owner = S.me?.role === 'owner';
  settingsTags();
  taskTypesSettingsShow(SETTINGS_TAB);
  // Each group draws its cards when it opens; a card an address points into is scrolled to once they are drawn (and
  // after route() has put the page back at its top).
  const loads = [];
  const load = (...calls) => loads.push(...calls.filter(Boolean));
  if (on('you')) {
    load(renderSettingsAssistantApprovals(),
      // The owner's one click when the team has no Assistant (ui/assistant.js): restore it or add it.
      owner && window.assistantChat?.settingsStrip($('#settings-assistant'), {get, post, esc, toast, after: async () => { await refresh(true); renderSettingsBots(); }}),
      );
  }
  if (on('people') && $('#settings-people') && !formBusy($('#settings-people'))) load(renderSettingsPeople());
  // Before the first load the form has no models or computers to show; loadSettings draws it then.
  if (on('models')) {
    if (SETTINGS_DATA.loaded) settingsModelsRender();
    if ($('#settings-gateway')) load(renderSettingsGateway());
  }
  settingsMachinesWatch();
  if (settingsMachinesVisible() && SETTINGS_DATA.loaded) void settingsMachinesRefresh();
  if (on('bots')) load(renderSettingsStrategyBot());
  if (on('limits')) load(SETTINGS_DATA.loaded && renderSettingsSpendLimits(), renderTeamBudgets(), SETTINGS_DATA.loaded && renderSettingsTurnLevels(), $('#settings-billing') && renderSettingsBilling());
  if (on('automation')) {
    load(renderSystemRoutines(), renderReleaseCard(), renderTaskManagerCard());
  }
  if (on('tasks')) load(roadmapSettingsMount(), askFilterSettingsLoad());
  if (on('connections')) load(renderSettingsInboxes(), !formBusy($('#settings-repos')) && renderSettingsRepos(), !formBusy($('#settings-webhooks')) && renderSettingsWebhooks());
  if (on('policies')) {
    const privacy = $('#settings-privacy'), storage = $('#settings-storage'), company = $('#settings-company');
    load(privacy && owner && renderSettingsPrivacy(), storage && owner && renderSettingsStorage(), company && settingsIsAdmin() && renderSettingsCompany(),
      $('#settings-policies') && renderSettingsPolicies(), $('#set-screening') && renderScreeningCard());
  }
  if (on('health')) load(window.hlMount?.(), settingsHistoryShow());
  const reveal = SETTINGS_REVEAL;
  if (reveal) void Promise.allSettled(loads).then(() => settingsRevealSoon(reveal));
}
// While Settings is the page, the address bar names the tab shown: #/settings/<tab>, with History's view and filters.
// It replaces the entry: no hashchange, no new history step.
function settingsAddress() {
  if (S.route !== SETTINGS) return;
  // Inboxes keeps its page address while Connections remains the selected Settings group.
  const inbox = SETTINGS_TAB === 'connections' && INBOXES_VIEW;
  SETTINGS_ADDRESS = inbox ? `${SETTINGS}/inboxes${inboxesAddress()}`
    : `${SETTINGS}/${SETTINGS_TAB}${SETTINGS_TAB === 'history' ? settingsHistoryQuery() : ''}`;
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
  if (!host.isConnected) return;   // Settings redrew while this loaded; the new page draws its own
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
  host.querySelector('#assistant-decides').onchange = async ev => {
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
// Settings > General > Team: whether a bot's report to the Tico team goes without a card (team rule bot_reports_direct,
// backend/support.py send_by_rule). Owner only. Off, every bot report waits for a person's yes in Needs you.
async function renderSettingsBotReports() {
  const host = $('#settings-bot-reports');
  if (!host || formBusy(host)) return;
  let rules;
  try { rules = (await get('/v2/access')).rules || {}; }
  catch (e) { if (host.isConnected) host.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (!host.isConnected) return;
  host.innerHTML = `<label class="people-line"><input type="checkbox" role="switch" class="people-switch" id="bot-reports-direct"${rules.bot_reports_direct !== false ? ' checked' : ''} aria-label="Bots send reports to the Tico team without asking">
    <span>Bots send reports to the Tico team without asking</span></label>`;
  host.querySelector('#bot-reports-direct').onchange = async ev => {
    const box = ev.target;
    try { await put('/v2/access/rules', {bot_reports_direct: box.checked}); toast('Saved'); void renderSettingsBotReports(); }
    catch (e) { box.checked = !box.checked; settingsFieldError(box, e.message); }
  };
}
// Settings > General > Privacy: usage counting and explicit consent for Gateway suggestions (PRIVACY.md).
async function renderSettingsPrivacy() {
  const host = $('#set-privacy');
  if (!host) return;
  let v, suggestions;
  try { [v, suggestions] = await Promise.all([get('/v2/system/usage-count'), get('/v2/system/suggestions')]); }
  catch (e) { if (host.isConnected) host.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (!host.isConnected) return; // Settings redrew while this was loading; the new card draws itself
  const forced = v.off_by && v.off_by !== 'setting';
  host.innerHTML = `<label class="privacy-row"><input type="checkbox" id="privacy-count"${v.enabled ? ' checked' : ''}${forced ? ' disabled' : ''}> Help count active installs</label>
    <p class="settings-cell-note">${forced ? (v.off_by === 'demo' ? 'Always off in the demo. ' : `Turned off by ${esc(v.off_by)}. `) : ''}A random ID, the version and three yes/no activity flags. <a href="${esc(v.doc)}" target="_blank" rel="noopener noreferrer">What is sent</a></p>
    <p class="settings-cell-note">${v.install_id ? `Install ID: <code>${esc(v.install_id)}</code> <button class="ghost" type="button" id="privacy-reset">Reset install ID</button>` : 'No install ID yet: one is made the first time the count is sent.'}</p>
    <label class="privacy-row"><input type="checkbox" id="privacy-suggestions"${suggestions.share_stats_for_suggestions ? ' checked' : ''}${suggestions.available ? '' : ' disabled'}> Share stats for suggestions</label>
    <label class="privacy-row"><input type="checkbox" id="privacy-outcomes"${suggestions.report_suggestion_outcomes ? ' checked' : ''}${suggestions.available ? '' : ' disabled'}> Report suggestion outcomes</label>`;
  host.querySelector('#privacy-suggestions').onchange = async ev => {
    const box = ev.target, enabled = box.checked;
    if (enabled && !await settingsConfirm({title: 'Share stats for suggestions?', ok: 'Share stats',
      text: 'Sends counts only, never message text, to Tico.'})) { box.checked = false; return; }
    box.disabled = true;
    try { await put('/v2/system/suggestions', {share_stats_for_suggestions: enabled}); toast('Saved'); void renderSettingsPrivacy(); }
    catch (e) { box.disabled = false; box.checked = !enabled; settingsFieldError(box, e.message); }
  };
  host.querySelector('#privacy-outcomes').onchange = async ev => {
    const box = ev.target, enabled = box.checked;
    box.disabled = true;
    try { await put('/v2/system/suggestions', {report_suggestion_outcomes: enabled}); toast('Saved'); void renderSettingsPrivacy(); }
    catch (e) { box.disabled = false; box.checked = !enabled; settingsFieldError(box, e.message); }
  };
  host.querySelector('#privacy-count').onchange = async ev => {
    try { await put('/v2/system/usage-count', {enabled: ev.target.checked}); toast('Saved'); void renderSettingsPrivacy(); }
    catch (e) { ev.target.checked = !ev.target.checked; settingsFieldError(ev.target, e.message); }
  };
  const reset = host.querySelector('#privacy-reset');
  if (reset) reset.onclick = async ev => {
    const button = ev.currentTarget;
    if (!await settingsConfirm({title: 'Reset install ID?', ok: 'Reset install ID',
      text: 'Tico makes a new random ID. The old one is gone for good, so earlier counts can no longer be linked to this install.'})) return;
    try { await post('/v2/system/usage-count/reset'); toast('Saved'); void renderSettingsPrivacy(); } catch (e) { settingsFieldError(button, e.message); }
  };
}
// Models draws its cards when shown; Computers reads the subscriptions and the Gateway toggle for its pickers.
function settingsModelsRender() {
  if (SETTINGS_TAB === 'models') { void renderSettingsSubs(); void renderSettingsProviders(); }
  if (SETTINGS_TAB === 'limits') { void renderSettingsSpendLimits(); void renderSettingsTurnLevels(); }
  if (SETTINGS_TAB === 'computers' && S.me?.cloud) {
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
    if (['models', 'limits'].includes(SETTINGS_TAB)) settingsModelsRender();
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
  run_id: job.run_id, decision, note: detail ? `${STOPPED_NOTE[decision]} Details: ${detail}` : STOPPED_NOTE[decision],
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
  dialog.innerHTML = `<header class="tmodal-head"><h2>Stopped runs</h2><span class="spacer"></span><a class="ghost" href="#/bot/${esc(encodeURIComponent(bot))}/more" data-open-bot>Open bot</a><button class="ghost" type="button" data-close>Close</button></header><div class="tmodal-body" data-review>Loading…</div>`;
  dialog.querySelector('[data-close]').onclick = () => dialog.close();
  dialog.querySelector('[data-open-bot]').onclick = () => dialog.close();
  dialog.addEventListener('close', () => dialog.remove());
  document.body.appendChild(dialog); dialog.showModal();
  const host = dialog.querySelector('[data-review]');
  try {
    const {jobs} = await get(`/v2/bots/${encodeURIComponent(bot)}/execution-review`);
    host.innerHTML = jobs.length ? jobs.map(job => {
      const saved = job.run?.final_text || job.output.map(event=>event.text).filter(Boolean).join('');
      const pick = STOPPED_PICK[job.stopped] || STOPPED_PICK.unknown, retry = pick.retry;
      const tools = (job.acted || []).join(', ');
      const button = (decision, label) => `<button type="submit" name="decision" value="${decision}"${(decision === 'resume') === retry ? '' : ' class="ghost"'}>${label}</button>`;
      return `<form data-job="${esc(job.id)}" class="stopped-run">
      <h3>${esc(job.task?.title || 'Interrupted conversation')}</h3>
      <p data-stopped>Stopped ${esc(fmt(job.run?.started_at || job.run?.created_at))} ${esc(pick.what(tools))}.</p>
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
