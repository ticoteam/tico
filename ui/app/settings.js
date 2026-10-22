/* ui/app/settings.js — Settings shell: pageSettings, tabs, Privacy, loadSettings, Health services
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// How visuals show (Visuals always on): decided by the setup, so a line to read, never a switch. "full" with a
// separate visual address; otherwise pictures, video and audio only (backend/visuals.py mode).
function settingsVisualsLine() {
  const full = S.config?.visuals === 'full';
  return `${full ? 'Visuals: full' : 'Visuals: pictures, video and audio only (no separate visual address)'}
    <a href="${GH}/blob/main/docs/visuals.md#self-hosting" target="_blank" rel="noopener noreferrer">How visuals show</a>`;
}

// ----------------------------------------------------------------- settings
// People stay deliberately compact. Bot ownership, model, and physical placement are separate
// controls because changing who can use a bot must never silently move its runtime (or vice versa).
// Teams come from the team's data; a name reads as its words (`customer-success` is Customer Success).
const teamLabel = t => (S.orgGroups || []).find(g => g.id === t)?.name || String(t || '').replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
let SETTINGS_DATA = {people: [], machines: [], services: [], models: [], issues: [], history: {changes: [], transitions: []}};
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
// Remembered per browser tab so a reload lands where the owner was, not back on Computers.
let SETTINGS_TAB = (() => { try { return sessionStorage.getItem(SETTINGS_TAB_KEY) || 'devices'; } catch { return 'devices'; } })();
let SETTINGS_TRANSITION_TIMER = null;
// A section a link points into (an old tab's name, settingsShow); it scrolls there once drawn.
let SETTINGS_REVEAL = '';
// The old tabs that are now sections of General.
const SETTINGS_GENERAL = {team: '#settings-team', assistant: '#settings-assistant-pane', privacy: '#settings-privacy', storage: '#settings-storage'};
const settingsReveal = selector => {
  if (!selector || SETTINGS_REVEAL !== selector) return;
  SETTINGS_REVEAL = ''; $(selector)?.scrollIntoView({block: 'start'});
};
// Owners and admins always; a member unless an admin switched create_bots off (the server decides, and counts the limit).
const settingsCanCreateBots = () => S.me?.role === 'owner' || !!S.me?.bot_admin || S.me?.can_create_bots !== false;
// The owner, an admin, one of the bot's owners, or someone it reports up to: what the server says (`can_manage`).
const settingsCanManageBot = e => S.me?.role === 'owner' || !!S.me?.bot_admin || !!e?.can_manage;
const settingsIsAdmin = () => S.me?.role === 'owner' || !!S.me?.bot_admin;
// Whoever the server lets make a personal token (`can_create_tokens`); an admin also sees everyone's. Someone
// who still holds a token keeps the card to revoke it.
const settingsCanMakeTokens = () => S.me?.can_create_tokens ?? settingsIsAdmin();
function pageSettings() {
  clearInterval(SETTINGS_TRANSITION_TIMER);
  if (SETTINGS_TAB === 'credentials' || SETTINGS_TAB === 'cloud') { SETTINGS_TAB = 'devices'; location.hash = INTEGRATIONS; return; }
  // A repeat visit to the page already on screen (a refresh, a same-route redraw) keeps it: rebuilding
  // it would snap back to the default tab and drop whatever is half typed in a form.
  const built = $('#settings-tabs');
  if (built && built.dataset.role === (S.me?.role || '') && built.dataset.ceo === String(settingsCeoBotShown())) { settingsShow(SETTINGS_TAB); loadSettings(); return; }
  $('#main').innerHTML = `<div class="meeting-head"><div><h1>Settings</h1>${settingsVersionHTML()}</div>${settingsThemeHTML()}</div>
    <div id="settings-issues"></div>
    <div class="tabs settings-tabs" id="settings-tabs" data-role="${esc(S.me?.role || '')}" data-ceo="${settingsCeoBotShown()}" role="tablist" aria-label="Settings">
      <button type="button" data-settings-tab="general" role="tab">General</button>
      ${settingsIsAdmin() ? '<button type="button" data-settings-tab="people" role="tab">Humans</button>' : ''}
      <button type="button" data-settings-tab="bots" role="tab">Bots</button>
      ${settingsCeoBotShown() ? `<button type="button" data-settings-tab="ceo" role="tab">${esc(ceoBotName())}</button>` : ''}
      <button type="button" data-settings-tab="devices" role="tab">Computers</button>
      <button type="button" data-settings-tab="repos" role="tab">Repositories</button>
      <button type="button" data-settings-tab="tasks" role="tab">Tasks</button>
      <button type="button" data-settings-tab="inbox" role="tab">Inbox</button>
      <details class="settings-more" id="settings-more"><summary>More</summary><div>
        <button type="button" data-settings-tab="inbound" role="tab">Email &amp; Phone</button>
        <button type="button" data-settings-tab="recurring" role="tab">Routines</button>
        <button type="button" data-settings-tab="triggers" role="tab">Triggers</button>
        <button type="button" data-settings-tab="health" role="tab">Health<span class="hl-alert" data-hl-alert hidden></span></button>
        <button type="button" data-settings-tab="history" role="tab">History</button>
      </div></details>
    </div>
    <div class="settings-pane" id="settings-general" role="tabpanel" hidden>
      ${settingsIsAdmin() ? `<section class="card" id="settings-team"><header><h2>Team</h2></header>
        <label class="settings-team-rule">${peopleSwitch('id="settings-stale-review"', false, true, 'Stale ticket review')}
          <span><strong>Stale ticket review</strong><span class="settings-cell-note">Hourly, suggests what to do with tasks that look finished or forgotten. Nothing changes until the owner accepts.</span></span></label>
        <p id="settings-stale-review-status" class="settings-cell-note" role="status"></p>
        <label class="settings-team-rule">${peopleSwitch('id="settings-topic-fresh"', false, true, 'Start fresh when the topic changes')}
          <span><strong>Start fresh when the topic changes</strong><span class="settings-cell-note">In a direct chat, a message on a new topic starts a fresh session, like Clear.</span></span></label>
        <p id="settings-topic-fresh-status" class="settings-cell-note" role="status"></p>
        ${S.me?.cloud && S.me?.role === 'owner' ? `<div class="settings-team-icon">
          <img id="settings-team-icon" alt="Team icon" hidden><span id="settings-team-icon-empty" class="muted">No icon</span>
          <div><strong>Team icon</strong><p class="settings-cell-note">PNG, JPEG or WebP, up to 1 MB.</p>
            <input id="settings-team-icon-file" type="file" accept="image/png,image/jpeg,image/webp" aria-label="Choose team icon" hidden>
            <button type="button" class="ghost" id="settings-team-icon-change">Choose icon</button>
            <p id="settings-team-icon-status" class="settings-cell-note" role="status"></p>
          </div>
        </div>` : ''}
        <p class="settings-cell-note" id="settings-visuals">${settingsVisualsLine()}</p></section>` : ''}
      <section class="card" id="settings-assistant-pane"><header><h2>${esc(assistantName())}</h2><div class="row">
        <a class="ghost" href="${ASSISTANT}">Open chat</a>
        ${settingsCanManageBot((S.emps || []).find(e => e.name === assistantBot())) ? `<a class="ghost" href="#/bot/${encodeURIComponent(assistantBot())}/more">Bot settings</a>` : ''}
      </div></header><div id="settings-assistant-approvals"></div></section>
      <div id="settings-assistant"></div>
      ${S.me?.role === 'owner' ? `<section class="card" id="settings-privacy"><header><h2>Privacy</h2></header><div id="set-privacy">${skelRows(3, {card: true, dot: false})}</div></section>
      <section class="card" id="settings-storage"><header><h2>Storage</h2></header><div id="set-storage">${skelRows(3, {card: true, dot: false})}</div></section>` : ''}
    </div>
    <div class="settings-pane" id="settings-devices" role="tabpanel">
      <section class="card"><div id="set-machines">${skelRows(3, {card: true, dot: false})}</div></section>
      <section class="card" id="settings-models"><header><h2>Models</h2></header><div id="set-providers">${skelRows(3, {card: true, dot: false})}</div></section>
      ${settingsCanMakeTokens() || S.me?.has_tokens ? `<section class="card" id="settings-tokens"><header><h2>API tokens</h2></header><div id="set-tokens">${skelRows(3, {card: true, dot: false})}</div></section>` : ''}
    </div>
    <div class="settings-pane" id="settings-bots" role="tabpanel" hidden>
      <section class="card settings-bots-card"><header><h2>Bots</h2>${settingsCanCreateBots() ? '<div class="row"><button class="primary" type="button" id="settings-add-bot" disabled>Add bot</button><button class="ghost" type="button" id="settings-add-catalog" disabled><span class="sb-wide">Add from template</span><span class="sb-narrow">From template</span></button></div>' : ''}</header><div id="set-bots">${skelRows(3, {card: true, dot: false})}</div></section>
    </div>
    <div class="settings-pane" id="settings-repos" role="tabpanel" hidden><section class="card repos-card" id="set-repos">${skelRows(3, {card: true, dot: false})}</section></div>
    <div class="settings-pane" id="settings-health" role="tabpanel" hidden><div class="hl-page" id="hl-page"></div>
      ${hlSetupCheckHTML()}
      <section class="card"><header><h2>Services</h2></header><div id="set-services">${skelRows(3, {card: true, dot: false})}</div></section></div>
    ${settingsIsAdmin() ? `<div class="settings-pane" id="settings-people" role="tabpanel" hidden><div id="set-people">${skelRows(3, {card: true, dot: false})}</div></div>` : ''}
    <div class="settings-pane" id="settings-recurring" role="tabpanel" hidden>
      <section class="card"><header><h2>Routines</h2></header><div id="set-recurring">${skelRows(3, {card: true, dot: false})}</div></section>
    </div>
    <div class="settings-pane" id="settings-triggers" role="tabpanel" hidden>
      <section class="card"><header><h2>Triggers</h2></header><div id="set-triggers">${skelRows(2, {card: true, dot: false})}</div></section>
    </div>
    <div class="settings-pane" id="settings-tasks" role="tabpanel" hidden>
      <div id="settings-types"></div>
      <section class="card" id="settings-roadmaps"></section>
      <section class="card"><header><h2>Task tags</h2></header><div id="settings-tags"></div></section>
    </div>
    <div class="settings-pane" id="settings-inbound" role="tabpanel" hidden><section class="card" id="set-inbound"></section></div>
    <div class="settings-pane" id="settings-inbox" role="tabpanel" hidden><section class="card"><header><h2>Your Google account</h2></header><div id="set-google"></div></section></div>
    <div class="settings-pane" id="settings-history" role="tabpanel" hidden><section class="card"><header><h2>History</h2>
      ${S.me?.role === 'owner' ? '<div class="theme-seg" id="settings-history-view" role="radiogroup" aria-label="History"><button type="button" role="radio" data-history-view="runs">Runs</button><button type="button" role="radio" data-history-view="changes">Changes</button></div>' : ''}</header>
      <div id="set-runs">${skelRows(3, {card: true, dot: false})}</div>
      ${S.me?.role === 'owner' ? `<div id="set-history" hidden>${skelRows(3, {card: true, dot: false})}</div>` : ''}</section></div>
    ${settingsCeoBotShown() ? `<div class="settings-pane" id="settings-ceo" role="tabpanel" hidden><section class="card"><header><h2>${esc(ceoBotName())}</h2></header><div id="set-ceo-bot">${skelRows(3, {card: true, dot: false})}</div></section></div>` : ''}
    <dialog class="tmodal" id="people-dialog" aria-label="Humans"></dialog>
    <dialog class="owner-picker" id="owner-picker" aria-labelledby="owner-picker-title"></dialog>
    <dialog class="bot-editor" id="bot-editor" aria-labelledby="bot-editor-title"></dialog>
    <dialog class="bot-editor catalog-picker" id="catalog-picker" aria-labelledby="catalog-picker-title"></dialog>`;
  taskTypesSettingsMount();
  $('#settings-app-log')?.addEventListener('click', () => nativeHandler('showLog')?.postMessage(null));
  pageSettingsThemeBind();
  settingsTeamIconBind();
  void settingsTeamRulesBind();
  $('#settings-history-view')?.addEventListener('click', event => {
    const button = event.target.closest('[data-history-view]');
    if (button) settingsHistoryShow(button.dataset.historyView);
  });
  $('#settings-tabs').onclick = event => {
    const button = event.target.closest('[data-settings-tab]');
    if (button) settingsShow(button.dataset.settingsTab);
  };
  // Opening More on a phone shows its tabs at the end of the scrolling row.
  $('#settings-more').addEventListener('toggle', event => { if (event.target.open) settingsTabIntoView(event.target.lastElementChild); });
  settingsShow(SETTINGS_TAB);
  loadSettings();
}
// The server's version, and the desktop app's own when the page runs inside it (app/src/main.rs bridge_script), with
// a way to the app's log file (app/src/applog.rs) when the shell keeps one (it says so in ticoNativeFeatures).
function settingsVersionHTML() {
  const parts = [S.config.version && `Version ${esc(nvVersion(S.config.version))}`,
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
  if (tab === 'tags' || tab === 'types') tab = 'tasks'; // Saved links keep working.
  // The company's models choice is a section of Computers; a link to it (or to the old AI providers tab) scrolls there.
  if (tab === 'models' || tab === 'providers') { tab = 'devices'; SETTINGS_REVEAL = '#settings-models'; }
  if (SETTINGS_GENERAL[tab]) { SETTINGS_REVEAL = SETTINGS_GENERAL[tab]; tab = 'general'; }
  if (tab === 'runs') { tab = 'history'; SETTINGS_HISTORY_VIEW = 'runs'; }   // the Runs page moved here
  const tabs = [...document.querySelectorAll('#settings-tabs [data-settings-tab]')];
  // Tab names come from the navigation; recheck access in case this person's role just changed.
  const owner = S.me?.role === 'owner';
  const access = {people: settingsIsAdmin(), ceo: settingsCeoBotShown()};
  SETTINGS_TAB = access[tab] !== false && tabs.some(button => button.dataset.settingsTab === tab) ? tab : 'devices';
  try { sessionStorage.setItem(SETTINGS_TAB_KEY, SETTINGS_TAB); } catch { /* private window: the tab is just not remembered */ }
  if (['health', 'history', 'recurring', 'triggers', 'inbound'].includes(SETTINGS_TAB)) $('#settings-more').open = true;
  const tasks = $('#settings-tasks');
  if (tasks) tasks.hidden = SETTINGS_TAB !== 'tasks';
  tabs.forEach(button => {
    const selected = button.dataset.settingsTab === SETTINGS_TAB;
    button.classList.toggle('cur', selected); button.setAttribute('aria-selected', String(selected));
  });
  settingsTabIntoView();
  settingsTags();
  taskTypesSettingsShow(SETTINGS_TAB);
  if (SETTINGS_TAB === 'tasks') void roadmapSettingsMount();
  const devices = $('#settings-devices'), history = $('#settings-history'), bots = $('#settings-bots');
  if (bots) bots.hidden = SETTINGS_TAB !== 'bots';
  if (devices) devices.hidden = SETTINGS_TAB !== 'devices';
  // Before the first load the form has no models or computers to show; loadSettings draws it then.
  if (SETTINGS_TAB === 'devices' && SETTINGS_DATA.loaded) void renderSettingsProviders();
  if (history) { history.hidden = SETTINGS_TAB !== 'history'; if (SETTINGS_TAB === 'history') settingsHistoryShow(); }
  const general = $('#settings-general');
  if (general) {
    general.hidden = SETTINGS_TAB !== 'general';
    // Drawn for the role this person had; one lost since keeps its sections hidden.
    const team = $('#settings-team'), privacy = $('#settings-privacy'), storage = $('#settings-storage');
    if (team) team.hidden = !settingsIsAdmin();
    if (privacy) privacy.hidden = storage.hidden = !owner;
    if (SETTINGS_TAB === 'general') {
      void Promise.all([renderSettingsAssistantApprovals(),
        // The owner's one click when the team has no Assistant (ui/assistant.js): restore it or add it.
        owner && window.assistantChat?.settingsStrip($('#settings-assistant'), {get, post, esc, toast, after: async () => { await refresh(true); renderSettingsBots(); }}),
        privacy && owner && renderSettingsPrivacy(), storage && owner && renderSettingsStorage()]).then(() => settingsReveal(SETTINGS_REVEAL));
    }
  }
  const peoplePane = $('#settings-people');
  if (peoplePane) { peoplePane.hidden = SETTINGS_TAB !== 'people'; if (SETTINGS_TAB === 'people' && !formBusy(peoplePane)) void renderSettingsPeople(); }
  const ceoPane = $('#settings-ceo');
  if (ceoPane) { ceoPane.hidden = SETTINGS_TAB !== 'ceo'; if (SETTINGS_TAB === 'ceo') void renderSettingsCeoBot(); }
  const healthPane = $('#settings-health');
  if (healthPane) { healthPane.hidden = SETTINGS_TAB !== 'health'; if (SETTINGS_TAB === 'health') window.hlMount?.(); }
  const inboundPane = $('#settings-inbound');
  if (inboundPane) { inboundPane.hidden = SETTINGS_TAB !== 'inbound'; if (SETTINGS_TAB === 'inbound') void inboundMount(); }
  const inboxPane = $('#settings-inbox');
  if (inboxPane) { inboxPane.hidden = SETTINGS_TAB !== 'inbox'; if (SETTINGS_TAB === 'inbox') void window.mountGoogleConnect?.($('#set-google')); }
  const reposPane = $('#settings-repos');
  if (reposPane) { reposPane.hidden = SETTINGS_TAB !== 'repos'; if (SETTINGS_TAB === 'repos' && !formBusy(reposPane)) void renderSettingsRepos(); }
  const recurring = $('#settings-recurring');
  if (recurring) { recurring.hidden = SETTINGS_TAB !== 'recurring'; if (SETTINGS_TAB === 'recurring' && !formBusy(recurring)) renderSettingsRecurring(); }
  const triggersPane = $('#settings-triggers');
  if (triggersPane) { triggersPane.hidden = SETTINGS_TAB !== 'triggers'; if (SETTINGS_TAB === 'triggers' && !formBusy(triggersPane)) void renderSettingsTriggers(); }
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
// Settings > General: the team rules an owner or an admin turns (backend/team_rules.py ADMIN_RULES): Stale ticket review
// (backend/stale_review.py) and Start fresh when the topic changes (backend/topic_change.py).
const SETTINGS_TEAM_RULES = {stale_review: 'settings-stale-review', topic_fresh: 'settings-topic-fresh'};
async function settingsTeamRulesBind() {
  const boxes = Object.entries(SETTINGS_TEAM_RULES).map(([rule, id]) => [rule, $('#' + id), $(`#${id}-status`)]).filter(([, box]) => box);
  if (!boxes.length) return;
  let rules;
  try { rules = (await get('/v2/access')).rules || {}; }
  catch (e) { for (const [, , status] of boxes) { status.textContent = e.message; status.classList.add('err'); } return; }
  for (const [rule, box, status] of boxes) {
    box.checked = !!rules[rule]; box.disabled = false;
    box.onchange = async () => {
      box.disabled = true; status.classList.remove('err'); status.textContent = 'Saving…';
      try { box.checked = !!(await put('/v2/access/rules', {[rule]: box.checked}))[rule]; status.textContent = 'Saved'; }
      catch (e) { box.checked = !box.checked; status.textContent = e.message; status.classList.add('err'); }
      finally { box.disabled = false; }
    };
  }
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
      status.textContent = 'Team icon saved';
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
      : 'Your team’s owner has turned this off for everyone (Settings > Humans).'}</p>
    ${rows ? `<h3>Decided by ${name} for you</h3><ul class="assistant-decisions">${rows}</ul>` : ''}`;
  $('#assistant-decides').onchange = async ev => {
    try { await post('/v2/assistant/settings', {decides_approvals: ev.target.checked}); toast(ev.target.checked ? `${assistantName()} can decide your approvals` : 'Approvals need your click again'); }
    catch (e) { toast(e.message, true); }
    void renderSettingsAssistantApprovals();
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
    try { await put('/v2/system/usage-count', {enabled: ev.target.checked}); toast(ev.target.checked ? 'Counting is on' : 'Counting is off'); }
    catch (e) { toast(e.message, true); }
    void renderSettingsPrivacy();
  };
  if ($('#privacy-reset')) $('#privacy-reset').onclick = async () => {
    try { await post('/v2/system/usage-count/reset'); toast('New install ID made'); } catch (e) { toast(e.message, true); }
    void renderSettingsPrivacy();
  };
}
async function loadSettings() {
  try {
    const [people, operations, catalog, employees, history, limits] = await Promise.all([
      get('/humans'),
      S.me?.cloud ? get('/v2/operations') : Promise.resolve({machines: [], services: []}),
      S.me?.cloud ? get('/v2/models') : Promise.resolve({models: []}),
      get('/employees'),
      S.me?.cloud && S.me?.role === 'owner' ? get('/v2/settings/history') : Promise.resolve({changes: [], transitions: []}),
      S.me?.cloud ? get('/v2/usage/limits').catch(() => null) : Promise.resolve(null),
      loadHarnessRows()
    ]);
    S.emps = namedRoster(employees);
    setPeople(people);
    SETTINGS_DATA = {people: (people.people || []).filter(p => !p.hidden), machines: operations.machines || [],
      runnable: operations.runnable || {},
      agents: operations.agents || [],
      services: operations.services || [], models: catalog.models || [],
      harnesses: catalog.harnesses || [],
      enabledProviders: catalog.enabled_providers || [], defaultModel: catalog.default?.model || '',
      issues: operations.issues || [], history, limits,
      schedulerEnabled: operations.scheduler_enabled, loaded: true};
    document.dispatchEvent(new Event('tico:settings-loaded'));
    renderTree();
    if (S.route !== SETTINGS) return;
    renderSettingsIssues(); renderSettingsBots(); renderSettingsMachines(); renderSettingsServices(); renderSettingsHistory();
    if (!formBusy($('#set-recurring'))) renderSettingsRecurring();   // not while its search box has the cursor
    void renderSettingsTokens();
    if (SETTINGS_TAB === 'devices' && !$('#set-prov')) void renderSettingsProviders();
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
  changed: {retry: false, what: tools => `after using ${tools || 'an approval or a tool that can change things'}`,
    say: tools => `Don’t retry unless you know ${tools || 'that action'} didn’t finish. It could run twice.`},
  twice: {retry: false, what: () => 'the same way more than once',
    say: () => 'Don’t retry: it keeps stopping. Check the task first.'},
  unknown: {retry: false, what: () => 'before answering',
    say: () => 'Don’t retry unless you know it did nothing. Tico has no record of what it did.'},
};
async function executionReview(bot) {
  const dialog = document.createElement('dialog'); dialog.className = 'tmodal';
  dialog.innerHTML = `<header><h2>Stopped runs</h2><button class="ghost" data-close>Close</button></header><div data-review>Loading…</div>`;
  dialog.querySelector('[data-close]').onclick = () => dialog.close();
  dialog.addEventListener('close', () => dialog.remove());
  document.body.appendChild(dialog); dialog.showModal();
  const host = dialog.querySelector('[data-review]');
  try {
    const {jobs} = await get(`/v2/bots/${encodeURIComponent(bot)}/execution-review`);
    host.innerHTML = jobs.length ? jobs.map(job => {
      const saved = job.attempt?.final_text || job.output.map(event=>event.text).filter(Boolean).join('\n');
      const pick = STOPPED_PICK[job.stopped] || STOPPED_PICK.unknown, retry = pick.retry;
      const tools = (job.acted || []).join(', ');
      const button = (decision, label) => `<button type="submit" name="decision" value="${decision}"${(decision === 'resume') === retry ? '' : ' class="ghost"'}>${label}</button>`;
      return `<form data-job="${esc(job.id)}" class="stopped-run">
      <h3>${esc(job.task?.title || 'Interrupted conversation')}</h3>
      <p data-stopped>Stopped ${esc(fmt(job.attempt?.started || job.attempt?.created))} ${esc(pick.what(tools))}.</p>
      <p data-pick><strong>${esc(pick.say(tools))}</strong></p>
      <div class="row">${retry ? button('resume', 'Try again') + button('dismiss', 'Don’t retry') : button('dismiss', 'Don’t retry') + button('resume', 'Try again')}</div>
      <p data-result role="status"></p>
      <details data-details><summary>Details</summary>
        ${job.task ? `<p class="muted">Task ${esc(job.task.status)}${job.task.owner_name ? `, owner ${esc(job.task.owner_name)}` : ''}</p>` : ''}
        <p class="muted">${esc(job.reason)}</p>
        <details><summary>Original request</summary><div style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(job.request)}</div></details>
        <details><summary>${saved ? 'What the run saved' : 'No answer was saved'}</summary><div style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:240px;overflow:auto">${esc(saved || 'The run ended without saving an answer.')}</div></details>
        ${job.tools?.length ? `<details><summary>Tool calls (${job.tools.length})</summary><ul class="review-tools">${job.tools.map(call => `<li${call.nested ? ' class="nested"' : ''}><code>${esc(call.tool)}</code>${call.summary ? ` ${esc(call.summary)}` : ''}${call.status ? ` <span class="muted">· ${esc(call.status)}</span>` : ''}</li>`).join('')}</ul></details>` : ''}
        <label>Note for the bot <span class="muted">Optional</span><textarea name="note" aria-label="Details for Tico" maxlength="4000" placeholder="For example: the draft exists, but nothing was sent."></textarea></label>
      </details>
    </form>`; }).join('') : '<p>No stopped runs remain.</p>';
    host.querySelectorAll('form').forEach(form => form.onsubmit = async event => {
      event.preventDefault();
      const job = jobs.find(row=>row.id === form.dataset.job), decision = event.submitter?.value;
      if (!decision) return;
      const buttons = form.querySelectorAll('button'); buttons.forEach(button=>button.disabled=true);
      try {
        const detail = form.elements.note.value.trim();
        const note = decision === 'resume'
          ? 'The human said no outside action completed. Retry this request and check existing work before repeating any step.'
          : 'The human said an outside action may have completed. Do not retry this delivery; inspect the task separately.';
        await post(`/v2/jobs/${encodeURIComponent(job.id)}/reconcile`, {
          attempt_id:job.attempt_id, decision, note:detail ? `${note} Details: ${detail}` : note,
          acknowledge_uncertain_effects:true
        });
        form.innerHTML = `<p role="status">${decision === 'resume' ? 'Got it. Tico will try this request again.' : 'Got it. Tico won’t retry it; the task stays as it is.'}</p>`;
        await loadSettings();
      } catch(error) {
        form.querySelector('[data-result]').textContent=error.message;
        buttons.forEach(button=>button.disabled=false);
      }
    });
  } catch(error) { host.textContent=error.message; }
}
function renderSettingsServices() {
  const el = $('#set-services'); if (!el) return;
  const rows = SETTINGS_DATA.services;
  el.innerHTML = `<div class="settings-services-list">${rows.map(service => `<div><strong>${esc(service.name || service.service)}</strong> · ${service.last_error ? `<span class="err">${esc(service.last_error)}</span>` : '<span class="ok">healthy</span>'}${service.last_success ? `<span class="settings-cell-note">${esc(ago(service.last_success))}</span>` : ''}</div>`).join('') || '<span class="muted">No health reports yet.</span>'}</div>`;
}
