/* ui/app/settings-bot-editor.js — Inline bot settings, bot creation, owners, and model/computer transitions
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// What the bot editor shows for an existing bot beyond its definition: who may use it, who it works for and who
// owns it (each opens its own dialog); its computer, where it compacts, its profile (what wakes it and which tasks it
// takes) and, for a synced bot, the app it is delivered through; and its model, fallback and task model. Every one of
// them is held until Save bot, which saves them together (settingsEditBot `pending`, settingsEditorPendingChoice). A
// model change or a move goes through its transition dialog first; a task model changes only what its tasks run on.
const settingsOwnerChips = list => (list || []).map(o => `<span class="pchip">${personCircle(o.name || o.id, 16)}<span>${esc(firstName(o.name) || o.id)}</span></span>`).join('');
function settingsBotEditorRows(e) {
  const chips = settingsOwnerChips;
  const manage = settingsCanManageBot(e);
  const change = (attr, what) => manage ? `<button class="ghost" type="button" ${attr}="${esc(e.id)}" aria-label="Change ${what} for ${esc(e.display_name)}">Change</button>` : '<span></span>';
  return `<div class="sb-row"><span>Access</span><span data-access-summary>${esc(accessSummary(e.access_policy))}</span>${change('data-edit-access', 'access')}</div>
    <div class="sb-row"><span>Works for</span><div class="settings-owner-list" data-bot-users>${chips(e.users) || '<span class="muted">Nobody</span>'}</div>${change('data-edit-owners', 'who it works for')}</div>
    <div class="sb-row"><span>Owners</span><div class="settings-owner-list" data-bot-owners>${chips(e.bot_owners) || '<span class="muted">Its owner</span>'}</div>${change('data-edit-bot-owners', 'owners')}</div>
    ${manage ? `<div class="sb-row"><span>Mail sending</span><span class="muted" data-mail-summary="${esc(e.id)}">…</span>${change('data-edit-mail', 'mail sending')}</div>` : ''}
    ${profileRowHTML(e)}
    ${settingsKeepTasksRow(e)}
    ${settingsTurnsAtOnceRow(e)}
    ${settingsManagesReportsRow(e)}
    ${budgetRowHTML(e)}
    ${e.agent ? `<div class="sb-row"><span>Computer</span>${settingsAgentCell(e)}</div>${settingsAppDeliveryRow(e)}`
      : `<div class="sb-row"><span>Model</span>${settingsChoiceCombo(e, 'model')}<span></span></div>
    ${settingsCompactRow(e)}
    <div class="sb-row"><span>Fallback</span>${settingsChoiceCombo(e, 'fallback')}<span></span></div>
    <div class="sb-row"><span>Task model</span><div>${settingsChoiceCombo(e, 'task')}${settingsTaskWarning(e)}</div><span></span></div>
    ${settingsTaskParts(e)}
    <div class="sb-row"><span>Computer</span>${settingsMachineSelect(e)}<span></span></div>
    ${settingsBrowserExitRow(e)}
    ${cloudMachineRowHTML(e)}`}`;
}
// Where the bot's conversation compacts (backend/settings_admin.py COMPACT_AT). Only a CLI whose manifest says
// `compaction` takes it, and it saves on its own without a fresh session, like the computer.
const SETTINGS_COMPACT_AT = [[150000, '150k'], [250000, '250k'], [400000, '400k'], [600000, '600k'], [1000000, '1M']];
const SETTINGS_COMPACT_DEFAULT = 250000;
function settingsCompactRow(e) {
  const harness = e.harness || e.runtime || e.resolved_runtime;
  if (harness && !(settingsHarness(harness)?.capabilities || harnessCaps(harness)).compaction) return '';
  const current = Number(e.compact_at) || SETTINGS_COMPACT_DEFAULT;
  const options = SETTINGS_COMPACT_AT.some(([value]) => value === current) ? SETTINGS_COMPACT_AT : [...SETTINGS_COMPACT_AT, [current, `${Math.round(current / 1000)}k`]];
  return `<div class="sb-row sb-compact"><span>Compact at</span><select class="settings-inline-select" data-bot-compact="${esc(e.id)}" data-current="${current}" aria-label="Compact at for ${esc(e.display_name)}" ${settingsCanManageBot(e) ? '' : 'disabled'}>
      ${options.map(([value, label]) => `<option value="${value}" ${value === current ? 'selected' : ''}>${label}</option>`).join('')}
    </select><span></span></div>`;
}
// Which computer's internet the bot's browser goes out through (backend/browser_exit.py): only computers whose owner
// shares with this bot, and the one it has now even if that stopped (it then fails closed, so it stays visible).
function settingsBrowserExitRow(e) {
  if (e.agent) return '';
  const current = e.browser_exit || '';
  const choices = (SETTINGS_DATA.machines || []).filter(m => m.id === current
    || (!m.revoked_at && m.browser_exit?.sharing && m.browser_exit.bots.includes(e.id)));
  if (!choices.length) return '';
  return `<div class="sb-row"><span>Browse through</span><select class="settings-inline-select" data-bot-browser-exit="${esc(e.id)}" data-current="${esc(current)}"
      aria-label="Browse through for ${esc(e.display_name)}" ${settingsCanManageBot(e) ? '' : 'disabled'}>
      <option value=""${current ? '' : ' selected'}>None</option>
      ${choices.map(m => `<option value="${esc(m.id)}"${m.id === current ? ' selected' : ''}>${esc(m.label)} · ${esc(settingsPersonName(m.owner_id))}</option>`).join('')}
    </select><span></span></div>`;
}
function settingsBrowserExitRun(select) {
  const slug = select.dataset.botBrowserExit, runner_id = select.value || null;
  return async rev => { await post(`/v2/bots/${encodeURIComponent(slug)}/browser-exit`, {runner_id}); return rev; };
}
// The editor's held changes: each returns the save Save bot runs, given the bot's revision, answering the next one.
function settingsCompactRun(select) {
  const slug = select.dataset.botCompact, compact_at = Number(select.value);
  return async rev => (await post(`/v2/bots/${encodeURIComponent(slug)}/compact-at`, {compact_at, expected_revision: rev}))?.revision ?? rev + 1;
}
function settingsKeepTasksRow(e) {
  if (e.agent) return '';
  const current = e.keep_tasks_going == null ? '' : String(e.keep_tasks_going);
  return `<div class="sb-row"><span>Keep N tasks going</span><input type="number" min="0" max="1000" step="1" class="settings-inline-select"
    data-bot-keep-tasks="${esc(e.id)}" data-current="${esc(current)}" value="${esc(current)}" placeholder="No limit"
    aria-label="Keep N tasks going for ${esc(e.display_name)}" ${settingsCanManageBot(e) ? '' : 'disabled'}><span></span></div>
    <div class="sb-sub muted">Blank: start assigned work whenever a turn is free. 0: off. Waiting work frees a place; reviews have no cap.</div>`;
}
function settingsKeepTasksRun(input) {
  const slug = input.dataset.botKeepTasks, keep_tasks_going = input.value === '' ? null : Number(input.value);
  return async rev => (await post(`/v2/bots/${encodeURIComponent(slug)}/keep-tasks-going`, {keep_tasks_going, expected_revision: rev}))?.revision ?? rev + 1;
}
// The most turns a bot runs at once across all its computers. The server takes 1 to 256, and empty is no limit.
function settingsTurnsAtOnceRow(e) {
  if (e.agent) return '';
  const current = e.turns_at_once == null ? '' : String(e.turns_at_once);
  return `<div class="sb-row"><span>Turns at once</span><input type="number" min="1" max="256" step="1"
    class="settings-inline-select" data-bot-turns-at-once="${esc(e.id)}" data-current="${esc(current)}"
    value="${esc(current)}" placeholder="No limit" aria-label="Turns at once for ${esc(e.display_name)}"
    ${settingsCanManageBot(e) ? '' : 'disabled'}><span></span></div>
    <div class="sb-sub muted">Most turns it runs at once across all computers. Empty for no limit.</div>`;
}
function settingsTurnsAtOnceRun(input) {
  const slug = input.dataset.botTurnsAtOnce, turns_at_once = input.value === '' ? null : Number(input.value);
  return async rev => (await post(`/v2/bots/${encodeURIComponent(slug)}/turns-at-once`,
    {turns_at_once, expected_revision: rev}))?.revision ?? rev + 1;
}
// Whether this bot changes its direct reports (backend/report_managers.py). On by default; the owner's team rule turns
// every manager off whatever this says. The bot cannot set its own: whoever manages it does.
function settingsManagesReportsRow(e) {
  if (e.agent) return '';
  const on = e.manages_reports !== false;
  return `<div class="sb-row"><span>Manages its reports</span><label class="settings-inline-check"><input type="checkbox" data-bot-manages-reports="${esc(e.id)}"
    data-current="${on}" ${on ? 'checked' : ''} aria-label="Manages its reports for ${esc(e.display_name)}" ${settingsCanManageBot(e) ? '' : 'disabled'}>
    <span>${on ? 'On' : 'Off'}</span></label><span></span></div>
    <div class="sb-sub muted">This bot changes its direct reports' model, task model, name and description, as itself. Off: it changes none of them.</div>`;
}
function settingsManagesReportsRun(box) {
  const slug = box.dataset.botManagesReports, manages_reports = box.checked;
  return async rev => (await post(`/v2/bots/${encodeURIComponent(slug)}/manages-reports`,
    {manages_reports, expected_revision: rev}))?.revision ?? rev + 1;
}
// The old wake presets, for history rows written before profiles (backend/profiles.py replaced them).
const SETTINGS_WAKE_PRESETS = [['token_saver', 'Token saver'], ['performance', 'Performance']];
// A synced bot whose platform has a desktop app a Mac's runner drives (backend/app_delivery.py APPS) can be delivered
// through that app on one of its person's computers instead of waiting for the daily sync. Only that person chooses a
// computer, one of their own where the app is ready (the rest are listed, disabled, with why); they or an admin turn it
// off. Everyone else reads the current value. It saves on its own, like the computer.
function settingsAppDeliveryRow(e) {
  // The server names the app a synced bot can be delivered through; none means the bot has no app.
  const app = e.agent?.synced && e.app_delivery_app;
  if (!app) return '';
  const current = e.app_delivery?.computer || '', name = machineAppName(app);
  const label = id => SETTINGS_DATA.machines.find(machine => machine.id === id)?.label || e.app_delivery?.computer_label || 'a computer';
  const mine = !!S.me?.id && e.owner_id === S.me.id;
  const off = 'Off (daily sync)';
  if (!mine && !(current && settingsIsAdmin())) {
    return `<div class="sb-row"><span>Deliver through the app on</span><span data-app-delivery-value="${esc(e.id)}">${current ? `${esc(label(current))} <span class="muted">· ${esc(name)} app</span>` : `<span class="muted">${off}</span>`}</span><span></span></div>`;
  }
  const own = mine ? SETTINGS_DATA.machines.filter(machine => !machine.revoked_at && machine.owner_id === S.me.id) : [];
  const options = own.map(machine => {
    const report = machine.readiness?.appdriver?.apps?.[app];
    const why = report?.ready === true ? '' : report ? [machineAppState(report), report.detail].filter(Boolean).join(': ') : `${name} not reported`;
    // The chosen computer stays selectable even when its app stops being ready, so the select shows it.
    return `<option value="${esc(machine.id)}" ${machine.id === current ? 'selected' : why ? 'disabled' : ''}>${esc(machine.label)}${why ? ` · ${esc(why)}` : ''}</option>`;
  });
  if (current && !own.some(machine => machine.id === current)) options.push(`<option value="${esc(current)}" selected>${esc(label(current))}</option>`);
  return `<div class="sb-row"><span>Deliver through the app on</span><select class="settings-inline-select" data-bot-app-delivery="${esc(e.id)}" data-app="${esc(app)}" data-current="${esc(current)}" aria-label="Deliver ${esc(e.display_name)} through the ${esc(name)} app on">
      <option value="" ${current ? '' : 'selected'}>${off}</option>${options.join('')}
    </select><span></span></div>`;
}
function settingsAppDeliveryRun(select) {
  const slug = select.dataset.botAppDelivery, chosen = select.value, app = select.dataset.app;
  // The server refuses (422, with why) a computer that isn't the person's own or where the app isn't ready.
  return async rev => (await post(`/v2/bots/${encodeURIComponent(slug)}/definition`, {
    app_delivery: chosen ? {computer: chosen, app} : null, expected_revision: rev}))?.revision ?? rev + 1;
}
// The slug the server would pick for this name (clients/catalog.py slug_from_name), shown under Advanced.
function slugFromName(name) {
  const words = String(name || '').normalize('NFKD').replace(/[^\x00-\x7f]/g, '').toLowerCase().match(/[a-z0-9]+/g) || [];
  let slug = '';
  for (const word of words) {
    const joined = slug ? `${slug}-${word}` : word;
    if (joined.length > 40) break;
    slug = joined;
  }
  return slug || (words[0] || '').slice(0, 40);
}

// Subscribe to updates (backend/update_feeds.py): what the bot hears about as quiet notes. Information only, so it
// changes nothing about the bot; the feeds' state comes from the server's config.
function settingsUpdateSubscriptions(e) {
  const feeds = S.config.update_feeds || {}, chosen = new Set(e?.update_subscriptions || []);
  const unreleased = feeds.unreleased ? ' <span class="pill unreleased" title="Not in a published release yet">Unreleased</span>' : '';
  const templateNote = !feeds.templates?.feed ? feeds.templates?.status || 'No feed yet.'
    : !e?.template ? feeds.templates?.no_template || '' : `Changes to its template, ${e.template}: public source changes, which may not be released yet.`;
  return `<h3 class="bot-editor-sec bot-editor-wide">Subscribe to updates${unreleased}</h3>
        <p class="bot-editor-hint bot-editor-wide">Each update waits as a note for the bot's next run and wakes nothing. It never changes the bot's Instructions, settings or access.</p>
        <label class="bot-editor-check"><input type="checkbox" name="update_templates" ${chosen.has('templates') ? 'checked' : ''}><span>Default employee settings<small>${esc(templateNote)}</small></span></label>
        <label class="bot-editor-check"><input type="checkbox" name="update_tico" ${chosen.has('tico') ? 'checked' : ''}><span>Tico updates<small>Published Tico releases, including MCP and tool changes.</small></span></label>`;
}
function settingsUpdateSubscriptionsChange(form, e) {
  const picked = ['templates', 'tico'].filter(name => form.elements['update_' + name]?.checked);
  const before = ['templates', 'tico'].filter(name => (e?.update_subscriptions || []).includes(name));
  return picked.join() === before.join() ? {} : {update_subscriptions: picked};
}
function settingsEditBot(slug = '', inline = false) {
  if (slug && !inline) { location.hash = `#/bot/${encodeURIComponent(slug)}/settings`; return; }
  const editing = !!slug, e = editing ? S.emps.find(row => row.id === slug) : null;
  const dialog = $('#bot-editor');
  if (!dialog || editing && (!e || !settingsCanManageBot(e))) return;
  if (e?.shared_from) return settingsEditBranch(e, dialog);
  const closeEditor = () => inline ? settingsEditBot(slug, true) : dialog.close();
  const active = () => inline ? dialog.isConnected : dialog.open;
  SETTINGS_KEEP_MODEL = e?.model || '';
  // A new bot starts on the team default; nothing here names a vendor.
  const enabledModels = SETTINGS_DATA.models.filter(model => !model.deprecated && model.provider &&
    (SETTINGS_DATA.enabledProviders || []).includes(model.provider));
  const fallbackModel = SETTINGS_DATA.models.find(model => model.id === SETTINGS_DATA.defaultModel && !model.deprecated)
    || enabledModels[0] || SETTINGS_DATA.models[0] || {};
  const effort = e?.reasoning_effort || e?.effort || fallbackModel.default_effort || 'high';
  const defaultHarness = e?.harness || fallbackModel.harnesses?.[0] || fallbackModel.runtime || '';
  const currentModel = settingsChoiceValue(defaultHarness, e?.model || fallbackModel.id, effort);
  const parentOptions = S.emps.filter(row => row.id !== slug &&
    (S.me?.role === 'owner' || settingsCanManageBot(row))).map(row =>
    `<option value="${esc(row.id)}" ${e?.reports_to === row.id ? 'selected' : ''}>${esc(row.display_name || row.slug)}</option>`).join('');
  const operator = e?.owner_id || S.me?.id || SETTINGS_DATA.people[0]?.id || '';
  // A member's bot goes on a computer an admin has opened to members' bots (the server refuses the rest).
  const machineOptions = SETTINGS_DATA.machines.filter(machine => !machine.revoked_at &&
    (settingsIsAdmin() || machine.accepts_member_bots)).map(machine =>
    `<option value="${esc(machine.id)}" data-operator="${esc(machine.owner_id)}">${esc(machine.label)} · ${esc(settingsPersonName(machine.owner_id))}</option>`).join('');
  // The Strategy Bot's name is the owner's alone, as the whole built-in bot is (backend/auth.py `bot_manager`).
  const ownerNamesIt = editing && e.id === 'goal-manager' && S.me?.role !== 'owner';
  // A built-in bot's slug is fixed (the server refuses its rename); any other bot's slug is renamed with its definition.
  const fixedSlug = editing && isBuiltInBot(e.id);
  dialog.innerHTML = `<form><div class="tmodal-head"><h2 id="bot-editor-title" tabindex="-1">${editing ? 'Settings' : 'Add bot'}</h2><span class="spacer"></span>${inline ? '' : '<button class="ghost" type="button" data-bot-close aria-label="Close" title="Close (Esc)">✕</button>'}</div>
    <div class="bot-editor-body">
      <div class="bot-editor-grid">
        <h3 class="bot-editor-sec bot-editor-wide">Profile</h3>
        <label class="bot-editor-wide">Name<input name="display_name" type="text" autocomplete="off" value="${esc(e?.display_name || '')}" placeholder="Release Captain" maxlength="100" required${ownerNamesIt ? ' readonly' : ''}>${ownerNamesIt ? `<small>Only the owner renames the ${esc(strategyBotName())}.</small>` : ''}</label>
        <label class="bot-editor-wide">Description<textarea name="description" maxlength="2000" placeholder="What this bot owns and does">${esc(e?.description || '')}</textarea></label>
        <label>Status<select name="status">${['planned','active','paused'].map(value => `<option value="${value}" ${(e?.status || 'planned') === value ? 'selected' : ''}>${value === 'planned' ? 'Setting up' : value[0].toUpperCase() + value.slice(1)}</option>`).join('')}</select></label>
        <label>Reports to<select name="reports_to"><option value="">Top level</option>${parentOptions}</select></label>
        <h3 class="bot-editor-sec bot-editor-wide">Working with others</h3>
        <label>Other bots<select name="bot_contact"><option value="open" ${(e?.bot_contact || 'open') === 'open' ? 'selected' : ''}>May chat and assign</option><option value="replies" ${e?.bot_contact === 'replies' ? 'selected' : ''}>Replies only</option><option value="tasks" ${e?.bot_contact === 'tasks' ? 'selected' : ''}>Tasks only</option></select><small>Applies to bots only. Humans are never affected.</small></label>
        <label>Conversation<select name="thread_mode"><option value="personal" ${(e?.thread_mode || 'personal') === 'personal' ? 'selected' : ''}>Private per human</option><option value="shared" ${e?.thread_mode === 'shared' ? 'selected' : ''}>Shared room</option></select></label>
        <label class="bot-editor-check bot-editor-wide"><input type="checkbox" name="private_tasks_default" ${e?.private_tasks_default ? 'checked' : ''}><span>Create private tasks by default<small>Tasks created by or assigned to this bot start private. A human requester can choose company visibility.</small></span></label>
        <label class="bot-editor-check"><input type="checkbox" name="shared" ${e?.shared ? 'checked' : ''}><span>Allow branches<small>People can make their own branch of it, on their computer and subscription.</small></span></label>
        <label class="bot-editor-check"><input type="checkbox" name="temp" ${e?.temp ? 'checked' : ''}><span>Temp bot<small>For work expected to end.</small></span></label>
        ${editing ? settingsUpdateSubscriptions(e) : ''}
        ${editing ? `<h3 class="bot-editor-sec bot-editor-wide">Access, model and computer</h3><p class="bot-editor-hint bot-editor-wide">Nothing here saves until you press Save bot. Then every change saves together.</p><div class="bot-editor-wide sb-rows" data-bot-people></div>` : ''}
        ${editing ? '' : `<h3 class="bot-editor-sec bot-editor-wide">Model and computer</h3><div class="bot-editor-wide" data-add-choice>
          ${settingsChoiceFields(currentModel)}
          <input type="hidden" name="model_effort" value="${esc(currentModel)}">
        </div>
        <label>Computer owner<select name="operator" ${S.me?.role === 'owner' ? '' : 'disabled'}>${SETTINGS_DATA.people.map(person => `<option value="${esc(person.id)}" ${person.id === operator ? 'selected' : ''}>${esc(person.name || person.id)}</option>`).join('')}</select></label>
        <label class="bot-editor-wide">Registered computer<select name="runner_id"><option value="">Assign later</option>${machineOptions}</select><small data-computer-note></small></label>
        <p class="bot-editor-wide muted">Everyone can use it. Change that under Access once it is added.</p>`}
        ${editing && !e.agent ? '<fieldset class="bot-editor-wide brepo" data-bot-repos hidden></fieldset>' : ''}
        <details class="bot-editor-wide" data-bot-advanced><summary>Advanced</summary><div class="bot-editor-grid">
          <label>Slug<input name="slug" type="text" autocomplete="off" spellcheck="false" value="${esc(e?.slug || '')}" placeholder="release-captain" pattern="[a-z0-9]+(?:-[a-z0-9]+)*" maxlength="80" ${fixedSlug ? 'readonly' : ''}>${editing ? '' : '<small>Picked from the name. Tico adds -2 when it is taken.</small>'}</label>
          <label>Repository<input name="repo" type="text" autocomplete="off" spellcheck="false" value="${esc(e?.repo || (e?.slug ? `bot-${e.slug}` : ''))}" placeholder="bot-release-captain" maxlength="200" ${editing ? 'required' : ''}></label>
        </div></details>
      </div>
    </div>
    ${editing ? '<p class="bot-editor-pending" data-bot-pending role="status" hidden></p>' : ''}
    <div class="bot-editor-foot"><button class="primary" type="submit">${editing ? 'Save bot' : 'Add bot'}</button><button class="ghost" type="button" data-bot-close>Cancel</button><span class="muted" data-bot-status></span>${editing && !isBuiltInBot(e.id) ? `<span class="spacer"></span><select name="successor" aria-label="Hand its work to"><option value="">Hand its work to ${esc(settingsPersonName(e.owner_id))}</option>${S.emps.filter(row => row.id !== slug).map(row => `<option value="${esc(row.id)}">Hand its work to ${esc(row.display_name || row.slug)}</option>`).join('')}</select>${['hermes', 'openclaw'].includes(e.agent?.harness) ? `<label class="bot-editor-check" title="Its agent stops either way; revoking makes it stop reporting in"><input type="checkbox" name="revoke_agent" checked> Revoke its credential</label>` : ''}<button class="ghost danger" type="button" data-bot-remove>Archive bot</button>` : ''}</div></form>`;
  const form = dialog.querySelector('form'), status = dialog.querySelector('[data-bot-status]');
  dialog.querySelectorAll('[data-bot-close]').forEach(button => button.onclick = closeEditor);
  // Every row is held until Save bot (`pending`, and the model pickers' own pick), so a reload elsewhere redraws the
  // rows only while nothing is waiting. An agent's credential buttons act at once and move the revision, so after one
  // Save uses the newer revision.
  let rev = e?.revision, touched = false, machinePick = null;
  const rows = dialog.querySelector('[data-bot-people]'), pendingLine = dialog.querySelector('[data-bot-pending]');
  const pending = new Map();   // key → {label, run: async revision => next revision}
  const choiceKinds = ['model', 'fallback', 'task'];
  const pendingNames = () => [...[...pending.values()].map(item => item.label), ...(machinePick ? ['computer'] : []),
    ...choiceKinds.filter(kind => settingsEditorPendingChoice(rows, kind)).map(kind => SETTINGS_CHOICE_KINDS[kind].name.toLowerCase()),
    ...settingsEditorTaskParts(rows).map(([, name]) => name)];
  const paintPending = () => {
    if (!pendingLine) return;
    const names = pendingNames();
    pendingLine.hidden = !names.length;
    pendingLine.textContent = names.length ? `Not saved yet: ${names.join(', ')}. Save bot saves them together.` : '';
  };
  const stage = (key, label, run) => { if (run) pending.set(key, {label, run}); else pending.delete(key); paintPending(); };
  if (rows) {
    const paint = () => { rows.innerHTML = settingsBotEditorRows(S.emps.find(row => row.id === slug) || e); rows.querySelectorAll('[data-choice-apply]').forEach(button => button.remove()); settingsWireCombos(rows); void settingsMailSummary(rows, slug); void budgetMount(rows, slug, stage); void profileMount(rows, slug); void cloudMachineMount(rows, slug); paintPending(); };
    const refresh = () => { if (!active() || !form.isConnected) { cleanup(); return; } if (pendingNames().length) return; if (touched) rev = (S.emps.find(row => row.id === slug) || e).revision; paint(); };
    paint();
    document.addEventListener('tico:settings-loaded', refresh);
    const cleanup = () => {
      if (active() && form.isConnected) return; // A previous opening's close event can arrive after this form opens.
      document.removeEventListener('tico:settings-loaded', refresh);
      dialog.removeEventListener('close', cleanup);
    };
    if (inline && BOT?.slug === slug) { BOT.settingsCleanup?.(); BOT.settingsCleanup = () => document.removeEventListener('tico:settings-loaded', refresh); }
    else dialog.addEventListener('close', cleanup);
    rows.addEventListener('click', event => {
      const access = event.target.closest('[data-edit-access]'), owners = event.target.closest('[data-edit-owners]'), botOwners = event.target.closest('[data-edit-bot-owners]');
      if (access) void settingsEditAccess(access.dataset.editAccess, {stage: (run, policy) => {
        const cell = rows.querySelector('[data-access-summary]'); if (cell) cell.textContent = accessSummary(policy);
        stage('access', 'access', run);
      }});
      if (owners) settingsEditOwners(owners.dataset.editOwners, {stage: (run, people) => {
        const cell = rows.querySelector('[data-bot-users]'); if (cell) cell.innerHTML = settingsOwnerChips(people) || '<span class="muted">Nobody</span>';
        stage('owners', 'who it works for', run);
      }});
      if (botOwners) void settingsEditBotOwners(botOwners.dataset.editBotOwners, {stage: (run, people) => {
        const cell = rows.querySelector('[data-bot-owners]'); if (cell) cell.innerHTML = settingsOwnerChips(people) || '<span class="muted">Its owner</span>';
        stage('co-owners', 'owners', run);
      }});
      const mail = event.target.closest('[data-edit-mail]');
      if (mail) void settingsEditMail(mail.dataset.editMail, null, {stage: (run, m) => {
        const cell = rows.querySelector('[data-mail-summary]'); if (cell) cell.textContent = mailSettingText(m);
        stage('mail', 'mail sending', run);
      }});
      const credential = event.target.closest('[data-agent-credential]'), revoke = event.target.closest('[data-agent-revoke]'), pairing = event.target.closest('[data-agent-pair]');
      if (credential || revoke || pairing) touched = true;
      if (pairing) void settingsAgentPair(pairing.dataset.agentPair);
      if (credential) void settingsAgentCredential(credential.dataset.agentCredential);
      if (revoke) void settingsAgentRevoke(revoke.dataset.agentRevoke);
    });
    rows.addEventListener('change', event => {
      const changed = control => control.value !== (control.dataset.current || '');
      const machine = event.target.closest('[data-bot-machine]');
      if (machine) {
        const target = SETTINGS_DATA.machines.find(row => row.id === machine.value);
        machinePick = changed(machine) && target ? {id: target.id, label: target.label} : null;
        // A cloud machine is not a move: the bot goes there when it is up (made or started when the bot next has work).
        const pick = cloudMachinePick(machine.value);
        stage('cloud', 'computer', changed(machine) && pick && cloudMachineRun(slug, {mode: 'cloud', placement: pick}));
      }
      const cloudSize = event.target.closest('[data-bot-cloud-size]');
      if (cloudSize) stage('cloud-size', 'size', changed(cloudSize) && cloudMachineRun(slug, {size: cloudSize.value}, cloudSize));
      const cloudAlways = event.target.closest('[data-bot-cloud-always]');
      if (cloudAlways) stage('cloud-always', 'always on', cloudAlways.checked !== (cloudAlways.dataset.current === 'true')
        && cloudMachineRun(slug, {always_on: cloudAlways.checked}, cloudAlways));
      const compact = event.target.closest('[data-bot-compact]');
      if (compact) stage('compact', 'compact at', changed(compact) && settingsCompactRun(compact));
      const exit = event.target.closest('[data-bot-browser-exit]');
      if (exit) stage('browser-exit', 'browse through', changed(exit) && settingsBrowserExitRun(exit));
      const profile = event.target.closest('[data-bot-profile], [data-profile-key]');
      if (profile) stage('profile', 'profile', profileStaged(profile));
      const keep = event.target.closest('[data-bot-keep-tasks]');
      if (keep) {
        settingsFieldClear(keep);
        if (!keep.checkValidity()) settingsFieldError(keep, keep.validationMessage || 'Enter a whole number from 0 to 1000, or leave it blank.');
        stage('keep', 'keep tasks going', changed(keep) && keep.checkValidity() && settingsKeepTasksRun(keep));
      }
      const turns = event.target.closest('[data-bot-turns-at-once]');
      if (turns) {
        settingsFieldClear(turns);
        if (!turns.checkValidity()) settingsFieldError(turns,
          turns.validationMessage || 'Enter a whole number from 1 to 256, or leave it blank.');
        stage('turns', 'turns at once', changed(turns) && turns.checkValidity() && settingsTurnsAtOnceRun(turns));
      }
      const managesReports = event.target.closest('[data-bot-manages-reports]');
      if (managesReports) stage('manages', 'manages its reports', managesReports.checked !== (managesReports.dataset.current === 'true') && settingsManagesReportsRun(managesReports));
      if (event.target.matches('[data-task-effort]')) event.target.dataset.touched = '1';
      const delivery = event.target.closest('[data-bot-app-delivery]');
      if (delivery) stage('delivery', 'app delivery', changed(delivery) && settingsAppDeliveryRun(delivery));
      paintPending();
    });
    // A task model picked here changes the Task effort choices: the efforts of the model that would run tasks.
    rows.addEventListener('tico:choice', event => {
      const box = event.target.closest('[data-bot-choice][data-kind=task]'), select = rows.querySelector('[data-task-effort]');
      if (!box?.picker?.valid() || !select) return;
      const bot = S.emps.find(row => row.id === slug) || e, value = box.picker.value();
      const choice = value ? settingsChoiceFromValue(value) : null, efforts = settingsModelEfforts(settingsTaskRunsOn(bot, choice?.model || ''));
      // Picking the task model back as it was shows its effort as it was, unless Task effort itself was changed.
      const held = box.picker.changed() || select.dataset.touched ? select.value : select.dataset.current || '';
      const effort = efforts.includes(held) ? held : choice?.effort || '';
      settingsTaskEffortPaint(select, efforts, effort, !choice);
      paintPending();
    });
    rows.addEventListener('input', () => paintPending());
  }
  void botReposMount(dialog.querySelector('[data-bot-repos]'), slug);   // ui/app/settings-repositories.js
  const remove = dialog.querySelector('[data-bot-remove]');
  if (remove) remove.onclick = async () => {
    // Archive (#535): off the chart, no automations or new work; open tasks go to the picked heir. It asks first.
    const successor = form.elements.successor.value;
    const hermes = ['hermes', 'openclaw'].includes(e.agent?.harness);
    const heir = successor ? settingsBotName(successor) : settingsPersonName(e.owner_id);
    if (!await settingsConfirm({title: `Archive ${e.display_name}?`, ok: 'Archive bot',
      text: `Its open tasks go to ${heir}. It leaves the team chart and its automations stop. You can restore it later from Archived bots.`})) return;
    remove.disabled = true; status.textContent = 'Archiving…';
    try {
      const done = await post(`/v2/bots/${encodeURIComponent(slug)}/archive`, {successor: successor || null, expected_revision: e.revision,
        ...(hermes ? {revoke_agent: !!form.elements.revoke_agent?.checked} : {})});
      SETTINGS_ARCHIVED = null;
      if (!inline) dialog.close();
      await loadSettings();
      if (inline) location.hash = '#/settings'; else settingsShow('bots');
      toast(done.agent?.detail || `Archived ${e.display_name}`);
    } catch (error) { status.innerHTML = `<span class="err">${esc(error.message)}</span>`; remove.disabled = false; }
  };
  if (!editing) {
    // Nobody types a slug: it follows the name, and the server picks a free one (adding -2, -3) unless edited here.
    const slugInput = form.elements.slug, repo = form.elements.repo, name = form.elements.display_name;
    let repoEdited = false, operatorEdited = false;
    repo.addEventListener('input', () => { repoEdited = true; repo.dataset.edited = '1'; });
    slugInput.addEventListener('input', () => {
      slugInput.dataset.edited = '1';
      if (!repoEdited) repo.value = slugInput.value ? `bot-${slugInput.value}` : '';
    });
    name.addEventListener('input', () => {
      if (slugInput.dataset.edited) return;
      slugInput.value = slugFromName(name.value);
      if (!repoEdited) repo.value = slugInput.value ? `bot-${slugInput.value}` : '';
    });
    form.elements.operator.addEventListener('change', () => { operatorEdited = true; });
    form.elements.reports_to.addEventListener('change', event => {
      const parent = S.emps.find(row => row.id === event.target.value);
      if (!operatorEdited && !form.elements.runner_id.value && parent?.owner_id) form.elements.operator.value = parent.owner_id;
    });
    form.elements.runner_id.addEventListener('change', event => {
      const selected = event.target.selectedOptions[0]?.dataset.operator;
      if (selected) form.elements.operator.value = selected;
    });
    const combo = dialog.querySelector('[data-add-choice]');
    if (combo) {
      const hidden = combo.querySelector('input[name=model_effort]');
      const computer = form.elements.runner_id, computerNote = dialog.querySelector('[data-computer-note]');
      const syncComputer = () => {
        const external = !!settingsHarness(settingsChoiceFromValue(hidden.value).harness)?.external;
        computer.disabled = external; if (external) computer.value = '';
        if (computerNote) computerNote.textContent = external ? 'Run by an external agent: no computer. After saving, create its credential in the Computer column.' : '';
      };
      const picker = settingsWireChoiceFields(combo.querySelector('[data-choice-fields]'), (value, valid) => {
        hidden.value = valid ? value : '';
        syncComputer();
      });
      if (!picker.valid()) hidden.value = '';
      syncComputer();
    }
  }

  form.onsubmit = async event => {
    event.preventDefault();
    const submit = form.querySelector('[type=submit]'); submit.disabled = true; status.textContent = 'Saving…';
    let added = null, saved = false;
    try {
      if (editing) {
        const model = settingsEditorPendingChoice(rows, 'model'), fallback = settingsEditorPendingChoice(rows, 'fallback');
        const task = settingsEditorPendingChoice(rows, 'task'), taskParts = settingsEditorTaskParts(rows);
        const unfinished = [model, fallback, task].find(pick => pick && !pick.ready);
        if (unfinished) throw new Error(`Nothing saved: finish choosing the ${unfinished === model ? 'model' : unfinished === fallback ? 'fallback' : 'task model'}, or reset it.`);
        // A model change or a move starts a fresh session, so Save asks first; saying no saves nothing.
        const bot = S.emps.find(row => row.id === slug) || e, target = model && settingsChoiceFromValue(model.value);
        if (model && !await settingsConfirmTransition(bot, 'model', settingsChoiceLabel(target.harness, target.model, target.effort)))
          throw new Error('Nothing saved: the model change was not confirmed. Reset the model, or press Save bot again.');
        const move = machinePick;
        if (move && !await settingsConfirmTransition(bot, 'machine', move.label))
          throw new Error('Nothing saved: the move was not confirmed. Pick the current computer again, or press Save bot again.');
        if (fallback) {
          rev = (await post(`/v2/bots/${encodeURIComponent(slug)}/fallback`, {fallback: settingsFallbackBody(fallback.value), expected_revision: rev})).revision;
          saved = true;
          // Saved now: if the definition fails below, pressing Save again does not send it twice.
          fallback.box.dataset.current = fallback.box.querySelector('[data-choice-fields]').dataset.current = fallback.value;
        }
        if (task || taskParts.length) {
          // The task model, Task effort and Task context length in one save, each part not changed kept as it is.
          // The bot's own model, and so its sessions, stay as they are.
          const change = Object.fromEntries(taskParts.map(([select]) => 'taskEffort' in select.dataset
            ? ['effort', select.value] : ['compact_at', Number(select.value) || null]));
          if (task) change.choice = task.value ? settingsChoiceFromValue(task.value) : null;
          rev = await settingsSaveTaskModel(bot, change, rev, true);
          saved = true;
          if (task) task.box.dataset.current = task.box.querySelector('[data-choice-fields]').dataset.current = task.value;
          taskParts.forEach(([select]) => { select.dataset.current = select.value; });
        }
        rev = (await post(`/v2/bots/${encodeURIComponent(slug)}/definition`, {
          display_name: form.elements.display_name.value, description: form.elements.description.value,
          reports_to: form.elements.reports_to.value || null, status: form.elements.status.value,
          bot_contact: form.elements.bot_contact.value,
          private_tasks_default: form.elements.private_tasks_default.checked,
          repo: form.elements.repo.value, thread_mode: form.elements.thread_mode.value,
          temp: form.elements.temp.checked, shared: form.elements.shared.checked, expected_revision: rev,
          ...(!fixedSlug && form.elements.slug.value.trim() && form.elements.slug.value.trim() !== e.slug ? {slug: form.elements.slug.value.trim()} : {}),
          ...settingsUpdateSubscriptionsChange(form, e)}))?.revision ?? rev;
        saved = true;
        // The held rows, in the order they were changed; each one done leaves the list, so Save again never repeats it.
        for (const [key, item] of pending) { rev = await item.run(rev); pending.delete(key); }
        // Last, since a transition holds the revision it started from; its own dialog shows the progress. A model
        // change applies at once, then the move starts from the bot as it is now (one change at a time per bot).
        let transition = null;
        if (model) {
          transition = await post(`/v2/bots/${encodeURIComponent(slug)}/transitions`, {kind: 'model',
            model: target.model, effort: target.effort, harness: target.harness,
            expected_generation: bot.machine?.generation || 0, expected_revision: rev});
          model.box.dataset.current = model.box.querySelector('[data-choice-fields]').dataset.current = model.value;
        }
        if (move) {
          if (model) await loadSettings();
          const now = S.emps.find(row => row.id === slug) || bot;
          transition = await post(`/v2/bots/${encodeURIComponent(slug)}/transitions`, {kind: 'machine', runner_id: move.id,
            expected_generation: now.machine?.generation || 0, expected_revision: model ? now.revision : rev});
          machinePick = null;
        }
        if (transition) settingsWatchTransition(transition.id);
      } else {
        const choice = settingsChoiceFromValue(form.elements.model_effort.value);
        if (!choice.harness || !choice.model || !choice.effort) throw new Error('Choose a harness, model and effort.');
        const slugChosen = form.elements.slug.dataset.edited && form.elements.slug.value;
        added = await post('/v2/bots', {...(slugChosen ? {slug: slugChosen} : {}), display_name: form.elements.display_name.value,
          description: form.elements.description.value, reports_to: form.elements.reports_to.value || null,
          status: form.elements.status.value, ...(slugChosen || form.elements.repo.dataset.edited ? {repo: form.elements.repo.value} : {}),
          private_tasks_default: form.elements.private_tasks_default.checked,
          thread_mode: form.elements.thread_mode.value, shared: form.elements.shared.checked, model: choice.model, effort: choice.effort,
          harness: choice.harness, operator: form.elements.operator.value || S.me?.id,
          runner_id: form.elements.runner_id.value || null});
      }
      if (!inline) dialog.close();
      await loadSettings();
      if (inline && BOT?.slug === slug && dialog.isConnected) {
        settingsEditBot(slug, true);
        dialog.querySelector('[data-bot-status]').textContent = 'Saved';
        botInfoRefresh(slug);
      }
      else if (!inline) settingsShow('bots');
      if (editing && BOT?.slug === slug) void botBranchesLoad(slug);   // Allow branches shows the picker without a reload
      toast(editing ? 'Saved' : added?.note || `Added ${form.elements.display_name.value}`);
    } catch (error) {
      status.innerHTML = `<span class="err">${esc(error.message)}</span>`; submit.disabled = false;
      // Part of it saved: the next Save starts from the bot as it is now, and sends only what is still waiting.
      if (saved) { await loadSettings().catch(() => {}); rev = (S.emps.find(row => row.id === slug) || e).revision; paintPending(); }
    }
  };
  if (!inline) {
    dialog.onclose = () => { if (!dialog.open) dialog.innerHTML = ''; };
    dialog.showModal();
  }
}
// A Model or Fallback picked in the editor but not Applied (TK-BLOG3EDS): Save applies it, or says why it can't,
// rather than closing over it. Null when the picker shows what the bot has; `ready` when Apply would take it.
function settingsEditorPendingChoice(rows, kind) {
  const box = rows?.querySelector(`[data-bot-choice][data-kind="${kind}"]`);
  if (!box?.picker?.changed()) return null;
  const value = box.picker.value();
  return {box, value, ready: box.picker.valid() && value !== (box.dataset.current || '')};
}
// Task effort and Task context length changed in the editor and not saved yet: [[select, name], ...].
function settingsEditorTaskParts(rows) {
  return [['[data-task-effort]', 'task effort'], ['[data-task-context]', 'task context length']]
    .map(([selector, name]) => [rows?.querySelector(selector), name]).filter(([select]) => select && select.value !== (select.dataset.current || ''));
}
function settingsEditOwners(slug, {stage} = {}) {
  const e = S.emps.find(row => row.id === slug);
  const dialog = botSettingsPanel('owner-picker', 'owner-picker', 'owner-picker-title') || $('#owner-picker');
  if (!e || !dialog) return;
  const selected = new Set((e.users || []).map(person => person.id));
  dialog.innerHTML = `<form><div class="tmodal-head"><h2 id="owner-picker-title">Who ${esc(e.display_name)} works for</h2><span class="spacer"></span><button class="ghost" type="button" data-owner-close aria-label="Close">✕</button></div>
    <div class="owner-picker-body">
      <div class="owner-options">${SETTINGS_DATA.people.map(person => `<label class="owner-option"><input type="checkbox" name="owner" value="${esc(person.id)}" ${selected.has(person.id) ? 'checked' : ''}>${personAvatar(person, 22)}<span>${esc(person.name || person.id)}</span></label>`).join('')}</div>
      <div class="row"><button class="primary" type="submit">${stage ? 'Done' : 'Save'}</button><button class="ghost" type="button" data-owner-close>Cancel</button><span id="owner-picker-status" class="muted"></span></div></div></form>`;
  dialog.querySelectorAll('[data-owner-close]').forEach(button => button.onclick = () => settingsPanelClose(dialog));
  dialog.querySelector('form').onsubmit = async event => {
    event.preventDefault();
    const owners = [...dialog.querySelectorAll('input[name=owner]:checked')].map(input => input.value);
    const status = $('#owner-picker-status', dialog);
    if (!owners.length) { status.innerHTML = '<span class="err">Choose at least one person.</span>'; return; }
    if (stage) {
      stage(async rev => (await post(`/v2/bots/${encodeURIComponent(slug)}/owners`, {owners, expected_revision: rev}))?.revision ?? rev + 1,
        owners.map(id => SETTINGS_DATA.people.find(person => person.id === id) || {id}));
      settingsPanelClose(dialog); return;
    }
    dialog.querySelectorAll('button,input').forEach(control => control.disabled = true); status.textContent = 'Saving…';
    try {
      await post(`/v2/bots/${encodeURIComponent(slug)}/owners`, {owners, expected_revision: e.revision});
      settingsPanelClose(dialog); await loadSettings(); toast('Saved');
    } catch (error) {
      status.innerHTML = `<span class="err">${esc(error.message)}</span>`;
      dialog.querySelectorAll('button,input').forEach(control => control.disabled = false);
    }
  };
  dialog.onclose = () => { dialog.innerHTML = ''; };
  settingsPanelShow(dialog);
}
async function settingsMoveBot(select) {
  const slug = select.dataset.botMachine, e = S.emps.find(row => row.id === slug);
  const current = select.dataset.current || '', machine = SETTINGS_DATA.machines.find(row => row.id === select.value);
  const pick = cloudMachinePick(select.value);
  if (e && pick && select.value !== current) {
    try { await post(`/v2/bots/${encodeURIComponent(slug)}/cloud-machine`, {mode: 'cloud', placement: pick}); toast('Saved'); }
    catch (error) { select.value = current; toast(error.message); return; }
    await loadSettings();
    return;
  }
  if (!e || !machine || machine.id === current) return;
  if (!await settingsConfirmTransition(e, 'machine', machine.label)) { select.value = current; return; }
  await settingsBeginTransition(select, e, {kind:'machine', runner_id:machine.id,
    expected_generation:e.machine?.generation || 0, expected_revision:e.revision});
}
function settingsControlInput(control) {
  if (control?.matches?.('.settings-choice')) return null; // The three-field picker restores its own state.
  return control?.matches?.('input,select') ? control : control?.querySelector?.('input,select');
}
function settingsTransitionDialog() {
  let dialog = $('#transition-dialog');
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.className = 'transition-dialog'; dialog.id = 'transition-dialog';
    dialog.setAttribute('aria-labelledby', 'transition-title');
    document.body.append(dialog);
  }
  return dialog;
}
function settingsConfirmTransition(e, kind, target) {
  const dialog = settingsTransitionDialog();
  clearInterval(SETTINGS_TRANSITION_TIMER);
  const label = kind === 'model' ? 'Change model settings' : 'Move bot';
  return new Promise(resolve => {
    let settled = false;
    const finish = accepted => {
      if (settled) return;
      settled = true;
      dialog.onclose = null;
      if (dialog.open) dialog.close();
      dialog.innerHTML = '';
      resolve(accepted);
    };
    dialog.innerHTML = `<div class="tmodal-head"><h2 id="transition-title">${label} · ${esc(e.display_name)}</h2><span class="spacer"></span><button class="ghost" type="button" data-confirm-cancel aria-label="Close">✕</button></div>
      <div class="transition-body"><p>Destination: <strong>${esc(target)}</strong></p>
        <div class="transition-progress"><strong>This starts a fresh provider session.</strong>
          <p>The bot saves a checkpoint first. History is kept; session-only context is lost.</p></div>
        <div class="transition-actions"><button class="primary" type="button" data-confirm-prepare>Prepare change</button><button class="ghost" type="button" data-confirm-cancel>Cancel</button></div></div>`;
    dialog.querySelector('[data-confirm-prepare]').onclick = () => finish(true);
    dialog.querySelectorAll('[data-confirm-cancel]').forEach(button => button.onclick = () => finish(false));
    dialog.oncancel = event => { event.preventDefault(); finish(false); };
    // A dialog close event can arrive after an immediately-following picker has
    // already reused this element. Do not let that stale event cancel the new modal.
    dialog.onclose = () => { if (!dialog.open) finish(false); };
    dialog.showModal();
  });
}
async function settingsBeginTransition(control, e, body) {
  const input = settingsControlInput(control);
  const current = control.dataset.current || '';
  if (input) input.disabled = true;
  try {
    const transition = await post(`/v2/bots/${encodeURIComponent(e.id)}/transitions`, body);
    settingsWatchTransition(transition.id, {control, current});
  } catch (error) {
    if (input) { input.value = input.defaultValue || current; input.disabled = false; }
    toast(error.message, true);
  }
}
function settingsTransitionTarget(t) {
  return t.kind === 'model' ? settingsChoiceLabel(t.target?.harness || t.target?.runtime, t.target?.model, t.target?.effort)
    : t.target?.label || 'another computer';
}
function settingsTransitionHTML(t) {
  const name = settingsBotName(t.bot), progress = t.progress || {prepared:0,total:0};
  const status = t.state === 'preparing' ? `Checkpointing ${progress.prepared} of ${progress.total} current conversation${progress.total === 1 ? '' : 's'}…`
    : t.state === 'blocked' ? `Current-session checkpoint blocked: ${t.error || 'The current computer is unavailable.'}`
    : t.state === 'failed' ? t.error || 'The checkpoint could not be completed.'
    : t.state === 'applied' ? `${name} now uses ${settingsTransitionTarget(t)}.${t.without_checkpoint ? ' No prepared checkpoint was created.' : t.progress.total ? ' Its conversation checkpoint is saved.' : ' It had no active session to checkpoint.'}`
    : t.state === 'cancelled' ? 'This change was cancelled.' : 'Preparing the change…';
  const pending = ['preparing','blocked','failed','prepared'].includes(t.state);
  const force = ['blocked','failed'].includes(t.state);
  return `<div class="tmodal-head"><h2 id="transition-title">${t.kind === 'model' ? 'Change model settings' : 'Move bot'} · ${esc(name)}</h2><span class="spacer"></span><button class="ghost" type="button" data-transition-close aria-label="Close">✕</button></div>
    <div class="transition-body"><p>Destination: <strong>${esc(settingsTransitionTarget(t))}</strong></p>
      <div class="transition-progress"><strong>${esc(status)}</strong>${t.state === 'preparing' ? '<p class="muted">It applies when checkpoints are saved. You can close this.</p>' : ''}</div>
      ${force ? `<p class="err">Without a checkpoint the new session starts with no handoff. History is kept.</p>` : ''}
      <div class="transition-actions">${force ? '<button class="fail" type="button" data-transition-force>Change without checkpoint</button>' : ''}
        ${pending ? '<button class="ghost" type="button" data-transition-cancel>Cancel change</button>' : ''}
        ${t.state === 'applied' ? '<button class="primary" type="button" data-transition-done>Done</button>' : ''}</div></div>`;
}
function settingsWatchTransition(id, origin = {}) {
  const dialog = settingsTransitionDialog();
  clearInterval(SETTINGS_TRANSITION_TIMER);
  const paint = async () => {
    let t;
    try { t = await get(`/v2/settings/transitions/${encodeURIComponent(id)}`); }
    catch (error) { if (dialog.open) dialog.innerHTML = `<div class="transition-body"><p class="err">${esc(error.message)}</p></div>`; return; }
    if (!dialog.open) dialog.showModal();
    dialog.innerHTML = settingsTransitionHTML(t);
    dialog.querySelector('[data-transition-close]')?.addEventListener('click', () => dialog.close());
    dialog.querySelector('[data-transition-done]')?.addEventListener('click', () => dialog.close());
    dialog.querySelector('[data-transition-force]')?.addEventListener('click', async button => {
      const control = button.currentTarget;
      if (!await settingsConfirm({title: 'Change without a checkpoint?', ok: 'Change without checkpoint',
        text: `Raw conversation history stays in ${appName()}, but persistent session context will be lost.`})) return;
      control.disabled = true;
      try { await post(`/v2/settings/transitions/${encodeURIComponent(id)}/apply-without-checkpoint`, {change_without_checkpoint:true}); await paint(); }
      catch (error) {toast(error.message, true); control.disabled = false;}
    });
    dialog.querySelector('[data-transition-cancel]')?.addEventListener('click', async button => {
      button.currentTarget.disabled = true;
      try { await post(`/v2/settings/transitions/${encodeURIComponent(id)}/cancel`, {}); await paint(); }
      catch (error) {toast(error.message, true); button.currentTarget.disabled = false;}
    });
    if (['applied','cancelled'].includes(t.state)) {
      clearInterval(SETTINGS_TRANSITION_TIMER);
      await loadSettings();
      if (t.state === 'applied') {
        const suffix = t.without_checkpoint ? ' · changed without checkpoint' : t.progress.total ? ' · checkpoint saved' : '';
        toast(`${settingsBotName(t.bot)} ${t.kind === 'model' ? 'now uses' : 'now runs on'} ${settingsTransitionTarget(t)}${suffix}`);
      }
    }
  };
  dialog.onclose = () => {
    if (dialog.open) return;
    clearInterval(SETTINGS_TRANSITION_TIMER);
    const input = settingsControlInput(origin.control);
    if (input && !input.disabled) input.value = input.defaultValue || origin.current;
  };
  void paint(); SETTINGS_TRANSITION_TIMER = setInterval(() => { if (!document.hidden) paint(); }, 2000);
}

function settingsEditInstructions(slug) {
  location.hash = `#/bot/${encodeURIComponent(slug)}/instructions`;
}
document.addEventListener('click', event => {
  const button = event.target.closest('[data-edit-instructions]');
  if (button) settingsEditInstructions(button.dataset.editInstructions);
});
