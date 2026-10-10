/* ui/app/settings.js — Settings shell: pageSettings, tabs, Privacy, loadSettings, Health services
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- settings
// People stay deliberately compact. Bot ownership, model, and physical placement are separate
// controls because changing who can use a bot must never silently move its runtime (or vice versa).
// Teams come from the team's data; a name reads as its words (`customer-success` is Customer Success).
const teamLabel = t => (S.orgGroups || []).find(g => g.id === t)?.name || String(t || '').replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
let SETTINGS_DATA = {people: [], machines: [], services: [], models: [], issues: [], history: {changes: [], transitions: []}};
const SETTINGS_TAB_KEY = 'tico.settings.tab';
// Remembered per browser tab so a reload lands where the owner was, not back on Computers.
let SETTINGS_TAB = (() => { try { return sessionStorage.getItem(SETTINGS_TAB_KEY) || 'devices'; } catch { return 'devices'; } })();
let SETTINGS_TRANSITION_TIMER = null;
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
  if (built && built.dataset.role === (S.me?.role || '')) { settingsShow(SETTINGS_TAB); loadSettings(); return; }
  $('#main').innerHTML = `<div class="meeting-head"><div><h1>Settings</h1></div>${settingsThemeHTML()}</div>
    <div id="settings-issues"></div>
    <div class="tabs settings-tabs" id="settings-tabs" data-role="${esc(S.me?.role || '')}" role="tablist" aria-label="Settings">
      ${S.me?.cloud && S.me?.role === 'owner' ? '<button type="button" data-settings-tab="team" role="tab">Team</button>' : ''}
      <button type="button" data-settings-tab="assistant" role="tab">${esc(assistantName())}</button>
      ${settingsIsAdmin() ? '<button type="button" data-settings-tab="people" role="tab">Humans</button>' : ''}
      <button type="button" data-settings-tab="bots" role="tab">Bots</button>
      <button type="button" data-settings-tab="devices" role="tab">Computers</button>
      <button type="button" data-settings-tab="providers" role="tab">AI providers</button>
      <button type="button" data-settings-tab="repos" role="tab">Repositories</button>
      <button type="button" data-settings-tab="chat" role="tab">Chat</button>
      <button type="button" data-settings-tab="tasks" role="tab">Tasks</button>
      <details class="settings-more" id="settings-more"><summary>More</summary><div>
        <button type="button" data-settings-tab="recurring" role="tab">Routines</button>
        <button type="button" data-settings-tab="health" role="tab">Health<span class="hl-alert" data-hl-alert hidden></span></button>
        ${S.me?.role === 'owner' ? '<button type="button" data-settings-tab="history" role="tab">History</button><button type="button" data-settings-tab="privacy" role="tab">Privacy</button>' : ''}
      </div></details>
    </div>
    ${S.me?.cloud && S.me?.role === 'owner' ? `<div class="settings-pane" id="settings-team" role="tabpanel" hidden>
      <section class="card"><header><h2>Team</h2></header><div class="settings-team-icon">
        <img id="settings-team-icon" alt="Team icon" hidden><span id="settings-team-icon-empty" class="muted">No icon</span>
        <div><strong>Team icon</strong><p class="settings-cell-note">Public team logo. PNG, JPEG or WebP, up to 1 MB.</p>
          <input id="settings-team-icon-file" type="file" accept="image/png,image/jpeg,image/webp" aria-label="Choose team icon" hidden>
          <button type="button" class="ghost" id="settings-team-icon-change">Choose icon</button>
          <p id="settings-team-icon-status" class="settings-cell-note" role="status"></p>
        </div>
      </div></section></div>` : ''}
    <div class="settings-pane" id="settings-devices" role="tabpanel">
      <section class="card"><div id="set-machines">${skelRows(3, {card: true, dot: false})}</div></section>
      ${settingsCanMakeTokens() || S.me?.has_tokens ? `<section class="card" id="settings-tokens"><header><h2>API tokens</h2></header><div id="set-tokens">${skelRows(3, {card: true, dot: false})}</div></section>` : ''}
    </div>
    <div class="settings-pane" id="settings-bots" role="tabpanel" hidden>
      <section class="card settings-bots-card"><header><h2>Bots</h2>${settingsCanCreateBots() ? '<div class="row"><button class="primary" type="button" id="settings-add-bot" disabled>Add bot</button><button class="ghost" type="button" id="settings-add-catalog" disabled><span class="sb-wide">Add from template</span><span class="sb-narrow">From template</span></button></div>' : ''}</header><div id="set-bots">${skelRows(3, {card: true, dot: false})}</div></section>
    </div>
    <div class="settings-pane" id="settings-repos" role="tabpanel" hidden><section class="card repos-card" id="set-repos">${skelRows(3, {card: true, dot: false})}</section></div>
    <div class="settings-pane" id="settings-health" role="tabpanel" hidden><div class="hl-page" id="hl-page"></div>
      <section class="card"><header><h2>Services</h2></header><div id="set-services">${skelRows(3, {card: true, dot: false})}</div></section></div>
    ${settingsIsAdmin() ? `<div class="settings-pane" id="settings-people" role="tabpanel" hidden><div id="set-people">${skelRows(3, {card: true, dot: false})}</div></div>` : ''}
    <div class="settings-pane" id="settings-providers" role="tabpanel" hidden>
      <section class="card subs-card" id="settings-subs" aria-labelledby="settings-subs-h" hidden><header><h2 id="settings-subs-h">Subscriptions</h2></header><div id="set-subs"></div></section>
      <section class="card"><header><h2>AI providers</h2></header><div id="set-providers">${skelRows(3, {card: true, dot: false})}</div></section>
    </div>
    <div class="settings-pane" id="settings-recurring" role="tabpanel" hidden>
      <section class="card"><header><h2>Routines</h2></header><div id="set-recurring">${skelRows(3, {card: true, dot: false})}</div></section>
    </div>
    <div class="settings-pane" id="settings-assistant-pane" role="tabpanel" hidden>
      <section class="card"><header><h2>${esc(assistantName())}</h2></header>
        <p>Your personal assistant helps find information and coordinate work.</p>
        <a class="ghost" href="${ASSISTANT}">Open chat</a>
        ${settingsCanManageBot((S.emps || []).find(e => e.name === assistantBot())) ? `<a class="ghost" href="#/bot/${encodeURIComponent(assistantBot())}/more">Bot settings</a>` : ''}
      </section><div id="settings-assistant"></div>
    </div>
    <div class="settings-pane" id="settings-chat" role="tabpanel" hidden><section class="card"><header><h2>Chat</h2></header>
      <p>Each bot has its own chat. Open a bot to choose a conversation or start a new one.</p>
      <button type="button" class="ghost" id="settings-chat-bots">Choose a bot</button>
      <a class="ghost" href="${ASSISTANT}">Chat with ${esc(assistantName())}</a>
    </section></div>
    <div class="settings-pane" id="settings-tasks" role="tabpanel" hidden>
      <div id="settings-types"></div>
      <section class="card"><header><h2>Task tags</h2></header><div id="settings-tags"></div></section>
    </div>
    <div class="settings-pane" id="settings-history" role="tabpanel" hidden><section class="card"><header><h2>Settings history</h2></header><div id="set-history">${skelRows(3, {card: true, dot: false})}</div></section></div>
    ${S.me?.role === 'owner' ? `<div class="settings-pane" id="settings-privacy" role="tabpanel" hidden><section class="card"><header><h2>Privacy</h2></header><div id="set-privacy">${skelRows(3, {card: true, dot: false})}</div></section></div>` : ''}
    <dialog class="tmodal" id="people-dialog" aria-label="Humans"></dialog>
    <dialog class="owner-picker" id="owner-picker" aria-labelledby="owner-picker-title"></dialog>
    <dialog class="bot-editor" id="bot-editor" aria-labelledby="bot-editor-title"></dialog>
    <dialog class="bot-editor catalog-picker" id="catalog-picker" aria-labelledby="catalog-picker-title"></dialog>`;
  taskTypesSettingsMount();
  pageSettingsThemeBind();
  settingsTeamIconBind();
  $('#settings-tabs').onclick = event => {
    const button = event.target.closest('[data-settings-tab]');
    if (button) settingsShow(button.dataset.settingsTab);
  };
  $('#settings-chat-bots').onclick = () => settingsShow('bots');
  // Opening More on a phone shows its tabs at the end of the scrolling row.
  $('#settings-more').addEventListener('toggle', event => { if (event.target.open) settingsTabIntoView(event.target.lastElementChild); });
  settingsShow(SETTINGS_TAB);
  loadSettings();
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
  const tabs = [...document.querySelectorAll('#settings-tabs [data-settings-tab]')];
  // Tab names come from the navigation; recheck access in case this person's role just changed.
  const owner = S.me?.role === 'owner';
  const access = {team: !!S.me?.cloud && owner, people: settingsIsAdmin(), history: owner, privacy: owner};
  SETTINGS_TAB = access[tab] !== false && tabs.some(button => button.dataset.settingsTab === tab) ? tab : 'devices';
  try { sessionStorage.setItem(SETTINGS_TAB_KEY, SETTINGS_TAB); } catch { /* private window: the tab is just not remembered */ }
  if (['health', 'history', 'privacy', 'recurring'].includes(SETTINGS_TAB)) $('#settings-more').open = true;
  for (const name of ['assistant', 'chat', 'tasks']) {
    const pane = $(name === 'assistant' ? '#settings-assistant-pane' : '#settings-' + name);
    if (pane) pane.hidden = SETTINGS_TAB !== name;
  }
  tabs.forEach(button => {
    const selected = button.dataset.settingsTab === SETTINGS_TAB;
    button.classList.toggle('cur', selected); button.setAttribute('aria-selected', String(selected));
  });
  settingsTabIntoView();
  settingsTags();
  taskTypesSettingsShow(SETTINGS_TAB);
  const devices = $('#settings-devices'), history = $('#settings-history'), bots = $('#settings-bots');
  if (bots) bots.hidden = SETTINGS_TAB !== 'bots';
  // The owner's one click when the team has no Assistant (ui/assistant.js): restore it or add it.
  if (SETTINGS_TAB === 'assistant' && S.me?.role === 'owner') window.assistantChat?.settingsStrip($('#settings-assistant'), {get, post, esc, toast, after: async () => { await refresh(true); renderSettingsBots(); }});
  if (devices) devices.hidden = SETTINGS_TAB !== 'devices';
  if (history) history.hidden = SETTINGS_TAB !== 'history';
  const team = $('#settings-team');
  if (team) team.hidden = SETTINGS_TAB !== 'team';
  const peoplePane = $('#settings-people');
  if (peoplePane) { peoplePane.hidden = SETTINGS_TAB !== 'people'; if (SETTINGS_TAB === 'people' && !formBusy(peoplePane)) void renderSettingsPeople(); }
  const privacyPane = $('#settings-privacy');
  if (privacyPane) { privacyPane.hidden = SETTINGS_TAB !== 'privacy'; if (SETTINGS_TAB === 'privacy') void renderSettingsPrivacy(); }
  const healthPane = $('#settings-health');
  if (healthPane) { healthPane.hidden = SETTINGS_TAB !== 'health'; if (SETTINGS_TAB === 'health') window.hlMount?.(); }
  const providersPane = $('#settings-providers');
  if (providersPane) { providersPane.hidden = SETTINGS_TAB !== 'providers'; if (SETTINGS_TAB === 'providers') void renderSettingsProviders(); }
  const reposPane = $('#settings-repos');
  if (reposPane) { reposPane.hidden = SETTINGS_TAB !== 'repos'; if (SETTINGS_TAB === 'repos' && !formBusy(reposPane)) void renderSettingsRepos(); }
  const recurring = $('#settings-recurring');
  if (recurring) { recurring.hidden = SETTINGS_TAB !== 'recurring'; if (SETTINGS_TAB === 'recurring' && !formBusy(recurring)) renderSettingsRecurring(); }
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
      status.textContent = 'Team icon saved';
    } catch (error) { status.textContent = error.message; status.classList.add('err'); }
    finally { button.disabled = input.disabled = false; input.value = ''; }
  };
}
// Settings > Privacy: the one switch for the anonymous usage count, and a new random install ID (PRIVACY.md).
async function renderSettingsPrivacy() {
  const host = $('#set-privacy');
  if (!host) return;
  let v;
  try { v = await get('/v2/system/usage-count'); } catch (e) { host.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const forced = v.off_by && v.off_by !== 'setting';
  host.innerHTML = `<label class="privacy-row"><input type="checkbox" id="privacy-count"${v.enabled ? ' checked' : ''}${forced ? ' disabled' : ''}> Help count active installs</label>
    <p class="settings-cell-note">${forced ? (v.off_by === 'demo' ? 'Always off in the demo. ' : `Turned off by ${esc(v.off_by)}. `) : ''}A random ID, the version and two yes/no activity flags. <a href="${esc(v.doc)}" target="_blank" rel="noopener noreferrer">What is sent</a></p>
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
      S.me?.cloud ? get('/v2/usage/limits').catch(() => null) : Promise.resolve(null)
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
      schedulerEnabled: operations.scheduler_enabled};
    document.dispatchEvent(new Event('tico:settings-loaded'));
    renderTree();
    if (S.route !== SETTINGS) return;
    renderSettingsIssues(); renderSettingsBots(); renderSettingsMachines(); renderSettingsServices(); renderSettingsHistory();
    void renderSettingsSubs();
    if (!formBusy($('#set-recurring'))) renderSettingsRecurring();   // not while its search box has the cursor
    void renderSettingsTokens();
    const add = $('#settings-add-bot');
    if (add) { add.onclick = () => settingsEditBot(); add.disabled = false; }
    const fromCatalog = $('#settings-add-catalog');
    if (fromCatalog) { fromCatalog.onclick = () => void settingsCatalogPicker(); fromCatalog.disabled = false; }
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
async function executionReview(bot) {
  const dialog = document.createElement('dialog'); dialog.className = 'tmodal';
  dialog.innerHTML = `<header><h2>Stopped runs</h2><button class="ghost" data-close>Close</button></header><div data-review>Loading…</div>`;
  dialog.querySelector('[data-close]').onclick = () => dialog.close();
  dialog.addEventListener('close', () => dialog.remove());
  document.body.appendChild(dialog); dialog.showModal();
  const host = dialog.querySelector('[data-review]');
  try {
    const {jobs} = await get(`/v2/bots/${encodeURIComponent(bot)}/execution-review`);
    host.innerHTML = jobs.length ? jobs.map(job => `<form data-job="${esc(job.id)}">
      <h3>${esc(job.task?.title || 'Interrupted conversation')}</h3>
      <p class="muted">Stopped ${esc(fmt(job.attempt?.started || job.attempt?.created))}${job.task ? ` · task ${esc(job.task.status)}` : ''}</p>
      <p><strong>Why Tico paused this request</strong><br>${esc(job.reason)}</p>
      <details><summary>Original request</summary><div style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(job.request)}</div></details>
      <details${job.attempt?.final_text || job.output.some(event=>event.text) ? ' open' : ''}><summary>${job.attempt?.final_text || job.output.some(event=>event.text) ? 'What the run saved' : 'No answer was saved'}</summary><div style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:240px;overflow:auto">${esc(job.attempt?.final_text || job.output.map(event=>event.text).filter(Boolean).join('\n') || 'The run ended without saving an answer.')}</div></details>
      <p><strong>Did an outside action finish?</strong><br>If not, Tico can retry. If it did or you are unsure, it stays stopped.</p>
      <label>Anything Tico should know? <span class="muted">Optional</span><textarea name="note" aria-label="Details for Tico" maxlength="4000" placeholder="For example: the draft exists, but nothing was sent."></textarea></label>
      <div class="row"><button type="submit" name="decision" value="resume">Nothing happened — try again</button><button type="submit" name="decision" value="dismiss" class="ghost">Done or unsure — don’t retry</button></div>
      <p data-result role="status"></p>
    </form>`).join('') : '<p>No stopped runs remain.</p>';
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
        form.innerHTML = `<p role="status">${decision === 'resume' ? 'Got it. Tico will try this request again.' : 'Got it. Tico will not retry this delivery.'}</p>`;
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
