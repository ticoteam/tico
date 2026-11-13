/* ui/app/settings-bots.ts — Settings > Bots: model and computer pickers, search, bulk change, catalog picker
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A model choice as a bot row stores it (its own model, `fallback`, `task_model`): older rows say runtime and effort.
interface SettingsBotChoice {
  harness?: string | null | undefined; runtime?: string | undefined; model?: string | null | undefined; reasoning_effort?: string | null | undefined; effort?: string | null | undefined;
  // A fallback's own window (backend/harnesses.py normalize_fallback); absent is "Same as bot".
  compact_at?: number | null | undefined;
}
// A bot's task settings (backend/task_models.py `stored`): a task model, a task effort and a task context length.
interface SettingsTaskModel extends SettingsBotChoice { compact_at?: number | null; }
// The three parts a picker value ('harness::model::effort') holds.
interface SettingsChoice { harness: string; model: string; effort: string; }
// A Gateway pick (settingsTierParse): its tier id, level, Fast flag and version ('auto' for always the latest).
interface SettingsTierPick { tier: string; level: string; fast: boolean; version: string; }
// The three picker kinds a bot has: its own model, its fallback, and the model its tasks run on.
type SettingsChoiceKind = 'model' | 'fallback' | 'task';
// One harness the bot's computer cannot run, with its way out (settingsRuntimeBlock).
type SettingsRunBlock = NonNullable<ReturnType<typeof settingsRuntimeBlock>>;
// The Bots view's search, selection and open task rows; they last while the page is open.
interface SettingsBotsView { q: string; selected: Set<string>; picking: boolean; taskOpen: Set<string>; }
// An archived bot as GET /v2/bots?include_archived=1 lists it (Restore).
interface SettingsArchivedBot { id?: string; slug?: string; display_name?: string; state?: string; owner_id?: string | null; bot_owners?: BriefPerson[]; }
// A template card as GET /v2/templates lists it (catalogCards, ui/app/catalog.ts).
interface SettingsCatalogCard { slug: string; name?: string; summary?: string; when?: string; owns?: string[]; template?: string; model?: string; reasoning_effort?: string; }
interface SettingsChoiceKindSpec { name: string; of: (e: SettingsBot) => SettingsBotChoice | null | undefined; none?: boolean | string; }
// A bot row as the Bots settings read it (GET /api/employees): its model choices, revision and readiness.
interface SettingsBot extends CloudMachineBot {
  model?: string; runtime?: string; effort?: string; reasoning_effort?: string; resolved_model?: string; resolved_runtime?: string | null;
  fallback?: SettingsBotChoice | null; task_model?: SettingsTaskModel | null; task_model_warning?: string | null;
  compact_at?: number | null;   // its chosen window; none: its model's default
  revision?: number; role?: string; description?: string; can_manage?: boolean;
  readiness?: { gateway?: string | null; problems?: string[] } | null;
  agent?: (BotAgent & { model?: string; harness?: string }) | null;
}

const settingsBotName = (id: unknown) => botRow(id)?.display_name || botSlug(id);
const settingsPerson = (id: string | null | undefined) => SETTINGS_DATA.people.find(person => person.id === id);
const settingsPersonName = (id: string | null | undefined) => settingsPerson(id)?.name || id || 'Unassigned';
let SETTINGS_KEEP_MODEL = '';
const settingsModel = (id: string | null | undefined) => SETTINGS_DATA.models.find(model => model.id === id);
const settingsHarness = (id: string | null | undefined) => (SETTINGS_DATA.harnesses || []).find(row => row.id === id);
const settingsModelName = (id: string | null | undefined) => settingsModel(id)?.label || settingsTierName(id) || id || 'Not set';
const settingsHarnessName = (id: string | null | undefined) => settingsHarness(id)?.label || id || '';
const settingsEffortName = (effort: string | null | undefined) => ({low:'low', medium:'medium', high:'high', xhigh:'xhigh', max:'max', ultra:'ultra'} as Record<string, string>)[String(effort)] || effort || '';
const settingsChoiceValue = (harness: string | null | undefined, model: string | null | undefined, effort: string | null | undefined) => [harness || '', model || '', effort || ''].join('::');
function settingsChoiceFromValue(value: string | null | undefined): SettingsChoice {
  const parts = String(value || '').split('::');
  return {harness: parts[0] || '', model: parts[1] || '', effort: parts[2] || ''};
}
// "Claude Opus 5 · high · 1M" (modelChoiceWords and modelContextWords, ui/app/format.ts); the tool that runs it only when it is not the model's own.
const settingsChoiceLabel = (harness: string | null | undefined, model: string | null | undefined, effort: string | null | undefined) => {
  if (!model) return 'not set';
  const row = settingsModel(model), own = row?.harnesses?.[0] || row?.runtime || harness;
  const words = modelChoiceWords(model, settingsEffortName(effort), harness, own) || settingsModelName(model);
  const context = modelContextWords(row?.context_tokens);
  return context ? `${words} · ${context}` : words;
};
// ----------------------------------------------------------------- Tico AI Gateway picks
// A Gateway bot's model is a pick, not a name: Level · Fast · Version (GET /v2/models `gateway`, backend/gateway_tiers.py).
// The stored model is the id they make: tico-{level}[-fast][-{version}], no version meaning always the latest.
const settingsIsTico = (id: string | null | undefined) => /^tico-/.test(id || '');
const settingsGateway = () => SETTINGS_DATA.gateway?.tiers && Object.keys(SETTINGS_DATA.gateway.tiers).length ? SETTINGS_DATA.gateway : null;
// {tier, level, fast, version ('auto' when none)} for a tier id, or null for any other id.
function settingsTierParse(id: string | null | undefined): SettingsTierPick | null {
  const gw = settingsGateway(), m = /^(tico-[a-z0-9-]+?)(?:-(\d+\.\d+))?$/.exec(String(id || ''));
  if (!m) return null;
  const tier = gw?.tiers[String(m[1])];
  return tier ? {tier: String(m[1]), level: tier.level, fast: tier.fast, version: m[2] || 'auto'} : null;
}
const settingsTierId = (t: SettingsTierPick) => `${t.tier}${t.version === 'auto' ? '' : '-' + t.version}`;
const settingsTierKey = (level: string, fast: boolean) => `tico-${level}${fast ? '-fast' : ''}`;
function settingsTierName(id: string | null | undefined) {
  const t = settingsTierParse(id);
  return t ? `${SETTINGS_DATA.gateway?.tiers[t.tier]?.name}${t.version === 'auto' ? ' (auto)' : ' ' + t.version}` : '';
}
// "Claude Sonnet 5.5 · 1M · $2.50 / $12.50 per 1M" for a pick, from the catalog's version rows (another id from its model row).
function settingsTierLine(id: string) {
  const t = settingsTierParse(id), tier = t && SETTINGS_DATA.gateway?.tiers[t.tier];
  const now = tier && tier.versions.find(v => v.version === (t?.version === 'auto' ? tier.current : t?.version));
  if (now) return [now.model, modelContextWords(now.context), `$${now.price.input.toFixed(2)} / $${now.price.output.toFixed(2)} per 1M`]
    .filter(Boolean).join(' · ');
  return settingsModel(id)?.description?.replace(/ underneath$/, '') || '';
}
// In the model list a Tico model says what runs underneath it: "Medium 1.0 (Claude Sonnet 5.5 underneath)".
// The same list says the window the model takes: "grok 4.6 · 500K" (modelContextWords, ui/app/format.ts).
const settingsModelPickName = (id: string) => {
  const row = settingsModel(id), context = modelContextWords(row?.context_tokens);
  const name = /^tico-/.test(id) && row?.description ? `${settingsModelName(id)} (${row.description})` : settingsModelName(id);
  return context ? `${name} · ${context}` : name;
};
// A bot with no model of its own follows the team default; say which one it resolves to.
function settingsDefaultLabel(e: SettingsBot) {
  if (e.model || !e.resolved_model) return 'not set';
  const row = settingsModel(e.resolved_model);
  return `team default (${settingsChoiceLabel(e.resolved_runtime, e.resolved_model, row?.default_effort || '')})`;
}
function settingsAllChoices() {
  // Once the owner has chosen providers, only their models are offered (a bot already on another
  // one keeps its own choice in the editor).
  const enabled = SETTINGS_DATA.enabledProviders || [];
  // Meeting models (providers.py `meeting`) are picked in Settings' Meeting model select, not as a bot's own model.
  return (SETTINGS_DATA.models || []).filter(model => !model.deprecated && (!model.meeting || model.id === SETTINGS_KEEP_MODEL) &&
    (!enabled.length || !model.provider || enabled.includes(model.provider) || model.id === SETTINGS_KEEP_MODEL)).flatMap(model =>
    (model.harnesses || [model.runtime]).flatMap(harness =>
      (model.efforts?.length ? model.efforts : [model.default_effort || '']).map(effort => {
        const label = settingsChoiceLabel(harness, model.id, effort);
        const search = [harness, settingsHarnessName(harness), model.id, model.label, model.description || '', effort, label].join(' ').toLowerCase();
        return {harness, model: model.id, effort, label, search, value: settingsChoiceValue(harness, model.id, effort)};
      })));
}
// What the bot's computer cannot run, as the server judges it (backend/readiness.py `can_run`, sent with
// /v2/operations as `runnable`): {can_run: false, problem, short, computer, sign_in, move}. Not listed: not refused.
function settingsRuntimeBlock(e: SettingsBot | null | undefined, harness: string | undefined) {
  const answer = e && !e.agent && harness ? SETTINGS_DATA.runnable?.[e.id]?.[harness] : null;
  if (!answer || answer.can_run !== false) return null;
  return {runtime: settingsHarness(harness)?.runtime || harness, short: answer.short || "can't run", reason: answer.problem || "Can't run here",
    computer: answer.computer, move: answer.move,
    signIn: answer.sign_in && (settingsIsAdmin() || settingsMachineOperator(answer.computer?.id) === S.me?.id) ? answer.sign_in : null};
}
const settingsMachineOperator = (id: string | undefined) => (SETTINGS_DATA.machines || []).find(row => row.id === id)?.owner_id;
// One line per harness this computer cannot run that has a way out: sign in here, or move the bot.
function settingsBlockedNote(e: SettingsBot | null | undefined, harnesses: (string | undefined)[]) {
  return harnesses.map((id): [string | undefined, SettingsRunBlock | null] => [id, settingsRuntimeBlock(e, id)])
    .filter((row): row is [string | undefined, SettingsRunBlock] => !!row[1] && !!(row[1].move || row[1].signIn))
    .filter(([, block], i, rows) => rows.findIndex(([, other]) => other.runtime === block.runtime) === i)
    .map(([id, block]) => `<p class="settings-choice-block" data-choice-block="${esc(block.runtime)}"><span>${block.reason.toLowerCase().includes(String(block.runtime)) ? '' : esc(harnessWords(block.runtime) || settingsHarnessName(id)) + ': '}${esc(block.reason)}</span>
      ${block.signIn ? `<button class="ghost" type="button" data-model-login data-runner="${esc(block.signIn.runner_id)}" data-runtime="${esc(block.runtime)}" data-machine="${esc(block.signIn.computer)}">Sign in</button>` : ''}
      ${block.move ? `<button class="ghost" type="button" data-choice-move="${esc(block.move.id)}" data-machine="${esc(block.move.label)}">Move to ${esc(block.move.label)}</button>` : ''}</p>`).join('');
}
async function settingsMoveForModel(button: HTMLElement) {
  const box = button.closest<HTMLElement>('[data-bot-choice]'), e = S.emps.find(row => row.id === box?.dataset.botChoice);
  if (!e || !await settingsConfirmTransition(e, 'machine', button.dataset.machine)) return;
  await settingsBeginTransition(button, e, {kind: 'machine', runner_id: button.dataset.choiceMove,
    expected_generation: e.machine?.generation || 0, expected_revision: e.revision});
}
document.addEventListener('click', event => {
  const move = eventElement(event)?.closest<HTMLElement>('[data-choice-move]');
  if (move) void settingsMoveForModel(move);
});
// What each picker kind sets on a bot: its own model, its fallback, or the model its tasks run on (task_model,
// backend/task_models.py; none is the same as the bot).
const SETTINGS_CHOICE_KINDS: Record<SettingsChoiceKind, SettingsChoiceKindSpec> = {model: {name: 'Model', of: e => e}, fallback: {name: 'Fallback', of: e => e.fallback, none: true},
  task: {name: 'Task model', of: e => e.task_model, none: 'Same as bot'}};
// A bot's task settings (backend/task_models.py `stored`): a task model, a task effort and a task context length, each
// "Same as bot" until set. The record's model may be '' (its tasks run on the bot's own model).
const settingsTaskSet = (task: SettingsTaskModel | null | undefined) => !!(task?.model || task?.reasoning_effort || task?.compact_at);
const settingsContextName = (tokens: number | null | undefined) => !tokens ? '' : SETTINGS_COMPACT_AT.find(([size]) => size === tokens)?.[1]
  || (tokens >= 1e6 && tokens % 1e6 === 0 ? `${tokens / 1e6}M` : `${Math.round(tokens / 1000)}k`);
// The model a bot's tasks run on: its task model, else its own (or the team default it follows).
const settingsTaskRunsOn = (e: SettingsBot, model: string | null | undefined = e.task_model?.model) => model || e.model || e.resolved_model || '';
function settingsMachineSelect(e: SettingsBot) {
  if (e.agent) return settingsAgentCell(e);
  // On Tico Cloud the shared and the bot's own cloud machine are two choices; no cloud machine is offered as a computer.
  const cloud = e.cloud_machine, current = cloudMachineValue(e) || e.machine?.runner_id || '';
  const machines = SETTINGS_DATA.machines.filter(machine => !machine.revoked_at && !machine.cloud_machine &&
    (S.me?.role === 'owner' || machine.owner_id === S.me?.id));
  const unassigned = `Not assigned · ${settingsPersonName(e.owner_id)}`;
  const currentLabel = e.machine ? `${e.machine.label} · ${settingsPersonName(e.machine.owner_id)}` : unassigned;
  const seen = e.machine?.last_seen_at ? `last seen ${ago(e.machine.last_seen_at)}` : e.machine ? 'waiting for its first heartbeat' : 'register a computer first';
  return `<select class="settings-inline-select" data-bot-machine="${esc(e.id)}" data-current="${esc(current)}"
    aria-label="Computer for ${esc(e.display_name)}" title="${esc(`${currentLabel} · ${seen}`)}" ${settingsCanManageBot(e) && (machines.length || cloud) ? '' : 'disabled'}>
      ${current ? '' : `<option value="" selected disabled>${esc(unassigned)}</option>`}
      ${cloud ? cloudMachineOptionsHTML(current) : ''}
      ${machines.map(machine => `<option value="${esc(machine.id)}" ${machine.id === current ? 'selected' : ''}>${esc(machine.label)} · ${esc(settingsPersonName(machine.owner_id))}</option>`).join('')}
    </select>`;
}
// A problem worth a badge on the bot's row; a bot that is fine shows nothing.
function settingsBotProblem(e: SettingsBot) {
  if (e.status !== 'active') return '';
  if (e.agent?.synced) return e.online ? '' : 'history not synced';
  if (e.agent) return !e.agent.credential ? 'no credential' : e.online ? '' : 'not reporting';
  if (cloudMachineOn(e)) return e.cloud_machine?.problem || '';
  return !e.machine ? 'no computer' : e.online && e.ready ? '' : e.online ? (e.readiness?.problems?.[0] || 'Computer not ready') : 'offline';
}
// Search lasts while the page is open; selection is trimmed to matching manageable bots.
// Remove the retired filters so an older browser preference can never hide search results.
try { localStorage.removeItem('hub.settings.bots.filters'); } catch {}
const SETTINGS_BOTS_VIEW: SettingsBotsView = {q: '', selected: new Set(), picking: false, taskOpen: new Set()};
const settingsBotHarness = (e: SettingsBotChoice) => e.harness || e.runtime || '';
const settingsBotEffort = (e: SettingsBotChoice) => e.reasoning_effort || e.effort || '';
const settingsBotSelectable = (e: SettingsBot) => !e.agent && settingsCanManageBot(e);
const settingsBotSearchModels = (e: SettingsBot) => {
  const bot = e.agent ? {model: e.agent.model} : e.model ? e : {model: e.resolved_model, harness: e.resolved_runtime};
  return {bot, task: e.agent ? null : e.task_model?.model ? e.task_model : bot};
};
const settingsSearchModelWords = (model: SettingsBotChoice | null | undefined) => model ? [model.model, settingsModelName(model.model)] : [];
// A bot's owners as the Owners column shows them.
const settingsBotOwners = (e: SettingsBot) => e.bot_owners?.length ? e.bot_owners : (e.users || []);
// Every word can occur in the name, slug, role, description, team, bot model or effective task model, the label and
// owner's name of the bot's computer (as the Computer column shows them), its cloud machine's name, or an owner's name.
const settingsBotMatches = (e: SettingsBot, q: string) => {
  const {bot, task} = settingsBotSearchModels(e);
  return !q.trim() || searchIncludes([e.display_name, botDisplayName(e.id), e.slug, e.role,
    e.description, e.team && teamLabel(e.team), ...settingsSearchModelWords(bot), ...settingsSearchModelWords(task),
    e.machine?.label, e.machine && settingsPersonName(e.machine.owner_id), cloudMachineLabel(e),
    ...settingsBotOwners(e).map(o => o.name || o.id)], q);
};
function settingsBotSearchMatch(e: SettingsBot) {
  const words = SETTINGS_BOTS_VIEW.q.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return '';
  const {bot, task} = settingsBotSearchModels(e);
  const matches = (model: SettingsBotChoice | null | undefined) => model?.model && words.some(word => searchIncludes(settingsSearchModelWords(model), word));
  const labels: [SettingsBotChoice | null | undefined, string][] = [[bot, 'Bot model'], [task, 'Task model']];
  const shown = labels.filter(([model]) => matches(model));
  return shown.length ? `<small class="sb-model-match" data-model-match>${shown.map(([model, label]) => `${label}: ${esc(settingsModelName(model?.model))}`).join(' · ')}</small>` : '';
}
function settingsBotsVisible(rows: SettingsBot[]) {
  return rows.filter(e => settingsBotMatches(e, SETTINGS_BOTS_VIEW.q));
}
function settingsBotsSearchHTML(shown: number, total: number) {
  const view = SETTINGS_BOTS_VIEW, picking = view.picking || view.selected.size > 0;
  return `<div class="settings-bots-filters">
    <span class="sb-search"><input class="settings-filter-search" type="search" autocomplete="off" spellcheck="false" enterkeyhint="search" data-bots-search
      value="${esc(view.q)}" placeholder="Search bots, models or computers" aria-label="Search bots, models or computers" aria-controls="settings-bots-list">
      <button type="button" class="sb-search-clear" data-bots-search-clear aria-label="Clear search" ${view.q ? '' : 'hidden'}>×</button></span>
    <span class="settings-bots-count" data-bots-count role="status">${shown === total ? '' : `${shown} of `}${total} bot${total === 1 ? '' : 's'}</span>
    <span class="sb-pick-tools"><button type="button" class="linkish" data-bots-pick-all ${picking ? '' : 'hidden'}>Select all</button>
      <button type="button" class="linkish" data-bots-picking aria-pressed="${picking}">${picking ? 'Done' : 'Select'}</button></span></div>
    <div class="settings-bulk" data-bots-bulk hidden><strong data-bulk-count></strong>
      <button class="primary" type="button" data-bulk-model>Change model…</button><button class="ghost" type="button" data-bulk-clear>Clear</button></div>`;
}
function settingsBotsSyncSelection(el: HTMLElement, visible: SettingsBot[]) {
  const selectable = visible.filter(settingsBotSelectable).map(e => e.id);
  const picked = selectable.filter(slug => SETTINGS_BOTS_VIEW.selected.has(slug)).length;
  const all = el.querySelector<HTMLInputElement>('[data-bots-select-all]');
  if (all) { all.checked = !!selectable.length && picked === selectable.length; all.indeterminate = picked > 0 && picked < selectable.length; all.disabled = !selectable.length; }
  const bar = el.querySelector<HTMLElement>('[data-bots-bulk]');
  if (bar) { bar.hidden = !SETTINGS_BOTS_VIEW.selected.size; bar.querySelector('[data-bulk-count]')!.textContent = `${SETTINGS_BOTS_VIEW.selected.size} selected`; }
}
// Archived bots the person may bring back (Restore), listed under the bots table. One read is kept for a while.
let SETTINGS_ARCHIVED: {at: number; bots: SettingsArchivedBot[]} | null = null;
const settingsMayRestore = (b: SettingsArchivedBot) => settingsIsAdmin() || b.owner_id === S.me?.id || (b.bot_owners || []).some(o => o.id === S.me?.id);
async function settingsArchivedBots(el: HTMLElement) {
  if (!SETTINGS_ARCHIVED || Date.now() - SETTINGS_ARCHIVED.at > 30000) {
    try { SETTINGS_ARCHIVED = {at: Date.now(), bots: (await get<SettingsArchivedBot[]>('/v2/bots?include_archived=1')).filter(b => b.state === 'archived' && settingsMayRestore(b))}; }
    catch { return; }
  }
  if (!el.isConnected || !SETTINGS_ARCHIVED.bots.length) return;
  el.querySelector('[data-archived-bots]')?.remove();
  const box = document.createElement('div'); box.dataset.archivedBots = '';
  box.innerHTML = `<h3>Archived</h3>${SETTINGS_ARCHIVED.bots.map(b => `<div class="row" data-archived-bot="${esc(b.id ?? b.slug)}"><strong>${esc(b.display_name || b.slug)}</strong>
    <span class="spacer"></span><button class="ghost" type="button" data-bot-restore="${esc(b.id ?? b.slug)}" data-label="${esc(b.display_name || b.slug)}">Restore</button></div>`).join('')}`;
  el.appendChild(box);
}
async function settingsRestoreBot(slug: string, button: HTMLButtonElement, label = slug) {
  button.disabled = true;
  try {
    const done: {agent?: {credential?: unknown} | null} = await post(`/v2/bots/${encodeURIComponent(slug)}/restore`, {});
    SETTINGS_ARCHIVED = null;
    toast(`Restored ${label}${done.agent && !done.agent.credential ? '; pair or create its credential' : ''}`);
    await loadSettings();
  } catch (error) { toast(errorMessage(error, ''), true); button.disabled = false; }
}
function renderSettingsBots() {
  const el = $('#set-bots'); if (!el) return;
  const all = S.emps.slice().sort((a,b) => (a.team || '').localeCompare(b.team || '') || byBotOrder(a, b) || settingsBotName(a.id).localeCompare(settingsBotName(b.id)));
  const rows = settingsBotsVisible(all);
  const shownSelectable = new Set(rows.filter(settingsBotSelectable).map(e => e.id));
  [...SETTINGS_BOTS_VIEW.selected].forEach(slug => { if (!shownSelectable.has(slug)) SETTINGS_BOTS_VIEW.selected.delete(slug); });
  const pick = (e: SettingsBot) => settingsBotSelectable(e)
    ? `<input type="checkbox" data-bot-pick="${esc(e.id)}" aria-label="Select ${esc(e.display_name)}" ${SETTINGS_BOTS_VIEW.selected.has(e.id) ? 'checked' : ''}>`
    : `<input type="checkbox" disabled aria-label="${esc(e.display_name)} cannot be changed here" title="${e.agent ? 'An external agent uses its own model' : 'You do not own this bot'}">`;
  const stack = (e: SettingsBot) => {
    const owners = settingsBotOwners(e);
    const names = owners.map(o => o.name || o.id);
    return `<span class="sb-stack" title="${esc(names.join(', ') || 'No owner')}">${owners.slice(0, 3).map(o => personCircle(o.name || o.id, 22)).join('')}${owners.length > 3 ? `<span class="more">+${owners.length - 3}</span>` : ''}${owners.length ? '' : '<span class="muted">None</span>'}</span>`;
  };
  const row = (e: SettingsBot) => {
    const problem = settingsBotProblem(e);
    const badges = `${isBuiltInBot(e.id) ? '<span class="pill" data-built-in>Built-in</span>' : ''}${botPausedBadge(e.id, true) || (e.status && e.status !== 'active' ? `<span class="pill">${esc(statusWord(e.status))}</span>` : '')}`;
    const model = e.agent ? `<span class="muted" title="${esc(e.agent.model ? `profile's model · ${e.agent.model}` : "the profile's own model")}">${esc(agentKind(e.agent))}</span>` : settingsChoiceCell(e, 'model');
    return `<tr data-settings-bot="${esc(e.id)}"><td class="settings-pick">${pick(e)}</td>
      <td class="sb-cell-name"><div class="sb-bot">${avatar(e.id, 27, stateOf(e.id))}<div class="sb-text"><div class="sb-line"><a class="sb-name" href="#/bot/${encodeURIComponent(e.id)}">${shownName(e)}</a>${botDisplayName(e.id) !== (e.display_name || e.slug) ? `<span class="mono muted">${esc(e.slug)}</span>` : ''}${badges}</div>${e.team || problem ? `<small>${e.team ? esc(teamLabel(e.team)) : ''}${e.team && problem ? ' · ' : ''}${problem ? `<span class="sb-problem">${esc(problem)}</span>` : ''}</small>` : ''}</div></div></td>
      <td class="sb-cell-access" aria-label="Access: ${esc(settingsAccessWord(e))}"><span class="sb-access-label" aria-hidden="true">Access:</span>${settingsAccessCell(e)}</td><td class="sb-cell-model"><div class="sb-model-wrap">${model}${e.agent ? '' : settingsTaskIndicator(e)}</div>${settingsBotSearchMatch(e)}</td>
      <td class="sb-cell-fallback">${e.agent ? '<span class="muted">-</span>' : settingsChoiceCell(e, 'fallback')}</td>
      <td class="sb-cell-owners">${stack(e)}</td><td class="sb-cell-computer">${settingsMachineSelect(e)}</td>
      <td class="sb-cell-limit">${useLimitButton(e.id, e.display_name, SETTINGS_DATA.limits?.bots?.[e.id])}</td>
      <td class="settings-row-actions">${settingsCanManageBot(e) ? `<button class="ghost" type="button" data-edit-bot="${esc(e.id)}" aria-label="Edit ${esc(e.display_name)}">Edit</button>` : ''}</td></tr>${
      !e.agent && settingsCanManageBot(e) && SETTINGS_BOTS_VIEW.taskOpen.has(e.id) ? settingsTaskRow(e, 9) : ''}`;
  };
  const q = SETTINGS_BOTS_VIEW.q.trim();
  const none = `<div class="empty">No bots match “${esc(q)}”.</div>`;
  const html = !all.length ? '<div class="empty">No bots are registered.</div>' : `${settingsBotsSearchHTML(rows.length, all.length)}${rows.length ? `<div class="scroll" id="settings-bots-list"><table class="settings-bots-table${SETTINGS_BOTS_VIEW.picking || SETTINGS_BOTS_VIEW.selected.size ? ' picking' : ''}"><thead><tr><th class="settings-pick"><input type="checkbox" data-bots-select-all aria-label="Select all shown bots"></th><th>Bot</th><th>Access</th><th>Model</th><th>Fallback</th><th>Owners</th><th>Computer</th><th>Limit</th><th aria-label="Actions"></th></tr></thead><tbody>
    ${rows.map(row).join('')}
    </tbody></table></div>` : `<div id="settings-bots-list">${none}</div>`}`;
  // While the search box has the cursor (typing, or the refresh loop meanwhile) it stays the same element:
  // replacing a focused field drops the cursor and closes a phone's keyboard. Everything around it is redrawn.
  const tools = el.querySelector('.settings-bots-filters');
  const search = tools?.querySelector('[data-bots-search]');
  if (all.length && search && search === document.activeElement) {
    const next = document.createElement('template'); next.innerHTML = html;
    const fresh = next.content.querySelector<HTMLElement>('.settings-bots-filters')!;
    tools.className = fresh.className;
    for (const part of ['[data-bots-count]', '.sb-pick-tools']) tools.querySelector(part).replaceWith(fresh.querySelector(part));
    tools.querySelector('[data-bots-search-clear]').hidden = !SETTINGS_BOTS_VIEW.q;
    fresh.remove();
    [...el.children].forEach(child => { if (child !== tools) child.remove(); });
    tools.after(next.content);
  } else el.innerHTML = html;
  settingsBotsSyncSelection(el, rows);
  void settingsArchivedBots(el);
  // A control that redraws the list takes the keyboard back to itself, or to the search box when it is gone
  // (hidden on a computer, or no longer drawn): a redraw must not drop focus to the page.
  const redraw = (selector: string) => {
    renderSettingsBots();
    const target = [...el.querySelectorAll(selector)].find(node => node.getClientRects().length);
    (target || el.querySelector('[data-bots-search]'))?.focus();
  };
  el.onclick = (event: MouseEvent) => {
    const target = eventElement(event);
    if (!target) return;
    const restore = target.closest<HTMLButtonElement>('[data-bot-restore]');
    if (restore) { void settingsRestoreBot(String(restore.dataset.botRestore), restore, restore.dataset.label); return; }
    if (target.closest('[data-choice-open]')) return;   // the picker opens itself (settings-bots-choice.ts)
    const task = target.closest<HTMLElement>('[data-task-open],[data-task-close]');
    if (task) {
      const slug = String(task.dataset.taskOpen || task.dataset.taskClose), open = SETTINGS_BOTS_VIEW.taskOpen;
      open.has(slug) ? open.delete(slug) : open.add(slug);
      const back = `[data-task-open="${CSS.escape(slug)}"]`;
      redraw(open.has(slug) ? `[data-task-row="${CSS.escape(slug)}"] select:not(:disabled)` : back); return;
    }
    if (target.closest<HTMLElement>('[data-bulk-model]')) { settingsBulkModelDialog(); return; }
    if (target.closest<HTMLElement>('[data-bulk-clear]')) { SETTINGS_BOTS_VIEW.selected.clear(); redraw('[data-bots-picking]'); return; }
    if (target.closest<HTMLElement>('[data-bots-search-clear]')) {
      SETTINGS_BOTS_VIEW.q = ''; renderSettingsBots(); el.querySelector('[data-bots-search]')?.focus(); return;
    }
    // On a phone the checkbox cell is the tap target, not just the 16px box.
    const pickCell = target.closest<HTMLElement>('td.settings-pick');
    if (pickCell && target === pickCell) { pickCell.querySelector<HTMLInputElement>('input:not(:disabled)')?.click(); return; }
    // Done also drops the selection: nothing stays picked behind checkboxes that are no longer shown.
    if (target.closest<HTMLElement>('[data-bots-picking]')) {
      SETTINGS_BOTS_VIEW.picking = !(SETTINGS_BOTS_VIEW.picking || SETTINGS_BOTS_VIEW.selected.size);
      if (!SETTINGS_BOTS_VIEW.picking) SETTINGS_BOTS_VIEW.selected.clear();
      redraw('[data-bots-picking]'); return;
    }
    if (target.closest<HTMLElement>('[data-bots-pick-all]')) {
      const pickable = rows.filter(settingsBotSelectable), on = !pickable.every(e => SETTINGS_BOTS_VIEW.selected.has(e.id));
      pickable.forEach(e => on ? SETTINGS_BOTS_VIEW.selected.add(e.id) : SETTINGS_BOTS_VIEW.selected.delete(e.id));
      el.querySelectorAll('[data-bot-pick]').forEach((box: HTMLInputElement) => { box.checked = on; });
      settingsBotsSyncSelection(el, rows); return;
    }
    const cap = target.closest<HTMLElement>('[data-use-limit]');
    if (cap) {
      const slug = String(cap.dataset.useLimit), name = settingsBotName(slug);
      useLimitDialog(slug, name, SETTINGS_DATA.limits?.bots?.[slug], SETTINGS_DATA.limits?.default, async () => {
        try { SETTINGS_DATA.limits = await get('/v2/usage/limits'); } catch {}
        renderSettingsBots();
      });
      return;
    }
    const bot = target.closest<HTMLElement>('[data-edit-bot]');
    const credential = target.closest<HTMLElement>('[data-agent-credential]');
    const revoke = target.closest<HTMLElement>('[data-agent-revoke]');
    const pairing = target.closest<HTMLElement>('[data-agent-pair]');
    if (pairing) void settingsAgentPair(pairing.dataset.agentPair!);
    if (bot) settingsEditBot(bot.dataset.editBot);
    if (credential) void settingsAgentCredential(credential.dataset.agentCredential!);
    if (revoke) void settingsAgentRevoke(revoke.dataset.agentRevoke!);
  };
  el.oninput = (event: Event) => {
    const target = eventElement(event);
    if (!target) return;
    const search = target.closest<HTMLInputElement>('[data-bots-search]');
    if (search) { SETTINGS_BOTS_VIEW.q = search.value; renderSettingsBots(); }
  };
  // Escape empties the search box (as a search field does in Chrome and Safari); an empty box lets it through.
  el.onkeydown = (event: KeyboardEvent) => {
    const target = eventElement(event);
    if (!target) return;
    const search = event.key === 'Escape' && target.closest<HTMLInputElement>('[data-bots-search]');
    if (!search || !search.value) return;
    event.preventDefault(); event.stopPropagation();
    search.value = ''; SETTINGS_BOTS_VIEW.q = ''; renderSettingsBots();
  };
  el.onchange = (event: Event) => {
    const target = eventElement(event);
    if (!target) return;
    if (target.closest<HTMLElement>('[data-bots-search]')) return;
    const one = target.closest<HTMLInputElement>('[data-bot-pick]');
    if (one) { one.checked ? SETTINGS_BOTS_VIEW.selected.add(String(one.dataset.botPick)) : SETTINGS_BOTS_VIEW.selected.delete(String(one.dataset.botPick)); settingsBotsSyncSelection(el, rows); return; }
    const allBox = target.closest<HTMLInputElement>('[data-bots-select-all]');
    if (allBox) {
      const on = allBox.checked;
      rows.filter(settingsBotSelectable).forEach(e => on ? SETTINGS_BOTS_VIEW.selected.add(e.id) : SETTINGS_BOTS_VIEW.selected.delete(e.id));
      el.querySelectorAll('[data-bot-pick]').forEach((box: HTMLInputElement) => { box.checked = on; });
      settingsBotsSyncSelection(el, rows); return;
    }
    const machine = target.closest<HTMLSelectElement>('[data-bot-machine]');
    if (machine) void settingsMoveBot(machine);
    const part = target.closest<HTMLSelectElement>('[data-task-row] [data-task-effort]');
    if (part) void settingsSaveTaskPart(part);
  };
  settingsWireCombos(el);
  const copyAll = $('#copy-setup-prompt');
  if (copyAll) { copyAll.onclick = () => settingsCopyPrompt(); copyAll.disabled = false; }
  const add = $('#settings-add-bot');
  if (add) { add.onclick = () => void settingsRecruitFlow(); add.disabled = false; }
  const fromCatalog = $('#settings-add-catalog');
  if (fromCatalog) { fromCatalog.onclick = () => void settingsCatalogPicker(); fromCatalog.disabled = false; }
}
// The same catalog cards the first run shows, for a team that is already set up. Nothing is
// locked on here: the bots that were required at first run already exist and are filtered out.
async function settingsCatalogPicker() {
  if (!S.me?.id || !settingsCanCreateBots()) return;
  let dialog = $('#catalog-picker');
  const temporary = !dialog;
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.id = 'catalog-picker'; dialog.className = 'bot-editor catalog-picker';
    dialog.setAttribute('aria-labelledby', 'catalog-picker-title');
    document.body.append(dialog);
  }
  dialog.innerHTML = `<form><div class="tmodal-head"><h2 id="catalog-picker-title">Add from template</h2><span class="spacer"></span>
      <button class="ghost" type="button" data-catalog-close aria-label="Close">✕</button></div>
    <div class="bot-editor-body" id="catalog-picker-body"><div class="empty">Loading templates…</div></div></form>`;
  const leave = () => dialog.close();
  window.addEventListener('hashchange', leave);
  dialog.onclose = () => {
    window.removeEventListener('hashchange', leave); dialog.innerHTML = '';
    if (temporary) { dialog.remove(); teamAddReturnFocus(); }
  };
  dialog.querySelectorAll('[data-catalog-close]').forEach((button: HTMLButtonElement) => button.onclick = () => dialog.close());
  dialog.showModal();
  let cards: SettingsCatalogCard[];
  try { cards = catalogCards(await get('/v2/templates')); }
  catch (error) {
    const body = $('#catalog-picker-body'); if (body) body.innerHTML = `<div class="err">${esc(errorMessage(error, ''))}</div>`;
    return;
  }
  if (!dialog.open) return;
  const have = new Set(S.emps.filter(row => row.status !== 'archived').map(row => row.slug));
  const state = catalogState(cards.filter(card => !have.has(card.slug)), {lock: false});
  const body = $('#catalog-picker-body');
  body.innerHTML = `
    <label>Search templates<input type="search" id="catalog-picker-search" placeholder="Name or description" autocomplete="off" aria-controls="catalog-picker-grid"></label>
    <p class="muted" id="catalog-picker-count" role="status"></p>
    <div class="empty" id="catalog-picker-empty" hidden>No matching templates.</div>
    <div class="cat-grid" id="catalog-picker-grid">${catalogGridHTML(state)}</div>
    <div class="row" style="margin-top:16px"><button class="primary" type="submit">Add selected</button>
      <button class="ghost" type="button" data-catalog-close>Cancel</button><span class="muted" id="catalog-picker-status"></span></div>`;
  const grid = $('#catalog-picker-grid'), search = $('#catalog-picker-search');
  const filter = () => {
    const words = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    let shown = 0;
    grid.querySelectorAll('[data-cat-card]').forEach((node: HTMLElement) => {
      const card = catalogCard(state, String(node.dataset.catCard));
      if (!card) throw new Error('Catalog card missing');
      const text = [card.name, card.slug, card.summary, card.when, ...(card.owns || []), catalogName(state, card)].join(' ').toLowerCase();
      node.hidden = !words.every((word: string) => text.includes(word));
      if (!node.hidden) shown++;
    });
    $('#catalog-picker-count').textContent = `${shown} of ${state.cards.length} templates · ${state.picked.size} selected`;
    $('#catalog-picker-empty').hidden = shown > 0;
  };
  // Keep cards mounted: filtering must preserve edited instructions, choices and selections.
  catalogWire(grid, state, filter);
  search.oninput = filter;
  search.onkeydown = (event: KeyboardEvent) => { if (event.key === 'Enter') event.preventDefault(); };
  filter();
  search.focus();
  dialog.querySelectorAll('[data-catalog-close]').forEach((button: HTMLButtonElement) => button.onclick = () => dialog.close());
  const form = dialog.querySelector('form'), status = $('#catalog-picker-status');
  form.onsubmit = async (event: SubmitEvent) => {
    event.preventDefault();
    const submit = form.querySelector('[type=submit]');
    const chosen = state.cards.filter(card => state.picked.has(card.slug));
    if (!chosen.length) { status.innerHTML = '<span class="err">Choose at least one bot.</span>'; return; }
    if (!S.me?.id) { status.innerHTML = '<span class="err">Sign in before adding a bot.</span>'; return; }
    if (!settingsCanCreateBots()) { status.innerHTML = '<span class="err">You no longer have permission to add bots.</span>'; return; }
    submit.disabled = true; status.textContent = 'Adding…';
    try {
      const hints = [];
      for (const card of chosen) {
        if (card.template === 'inbox-manager' && !catalogPerson(state, card)) {
          status.innerHTML = '<span class="err">Choose whose mailbox the message bot reads.</span>';
          submit.disabled = false; return;
        }
        const person = catalogPerson(state, card);
        const slug = card.template === 'inbox-manager' && person ? `${person.id}-inbox`
          : card.template === 'assistant' ? assistantBot() : card.slug;
        const named = catalogName(state, card);
        const display = card.template === 'inbox-manager' && person && named === (card.name || card.slug)
          ? `${person.name || person.id} message bot` : named;
        const added = await post('/v2/bots', {
          slug, display_name: display, description: card.summary || '',
          template: card.template, instructions: catalogInstructions(state, card),
          reports_to: null, status: 'planned', repo: `bot-${slug}`, thread_mode: 'personal',
          model: card.model || '', effort: card.reasoning_effort || '', operator: S.me.id, owners: [S.me.id], runner_id: null});
        if (added.name_hint) hints.push(added.name_hint);
      }
      dialog.close(); await refresh(true);
      if (S.route === SETTINGS) { await loadSettings(); settingsShow('bots'); }
      toast(hints.length ? hints.join('; ') : chosen.length === 1 ? `Added ${catalogName(state, chosen[0]!)}` : `Added ${chosen.length} bots`);
    } catch (error) {
      status.innerHTML = `<span class="err">${esc(errorMessage(error, ''))}</span>`;
      if (String(errorMessage(error, '')).includes('Team default')) {
        const link = document.createElement('button'); link.type = 'button'; link.className = 'linkish'; link.textContent = 'Models';
        link.onclick = () => {
          dialog.close();
          if (S.route === SETTINGS) settingsShow('models');
          else { SETTINGS_TAB = 'models'; location.hash = SETTINGS; }
        };
        status.append(' ', link);
      }
      submit.disabled = false;
    }
  };
}
