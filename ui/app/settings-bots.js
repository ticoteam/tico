/* ui/app/settings-bots.js — Settings > Bots: model and computer pickers, filters, bulk change, catalog picker
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const settingsBotName = slug => S.emps.find(e => e.name === slug)?.display_name || slug;
const settingsPerson = id => SETTINGS_DATA.people.find(person => person.id === id);
const settingsPersonName = id => settingsPerson(id)?.name || id || 'Unassigned';
let SETTINGS_KEEP_MODEL = '';
const settingsModel = id => SETTINGS_DATA.models.find(model => model.id === id);
const settingsHarness = id => (SETTINGS_DATA.harnesses || []).find(row => row.id === id);
const settingsModelName = id => settingsModel(id)?.label || id || 'Not set';
const settingsHarnessName = id => settingsHarness(id)?.label || id || '';
const settingsEffortName = effort => ({low:'low', medium:'medium', high:'high', xhigh:'xhigh', max:'max', ultra:'ultra'})[effort] || effort || '';
const settingsChoiceValue = (harness, model, effort) => [harness || '', model || '', effort || ''].join('::');
function settingsChoiceFromValue(value) {
  const parts = String(value || '').split('::');
  return {harness: parts[0] || '', model: parts[1] || '', effort: parts[2] || ''};
}
// "Claude Opus 5 · high" (modelChoiceWords, ui/app/format.js); the tool that runs it only when it is not the model's own.
const settingsChoiceLabel = (harness, model, effort) => {
  if (!model) return 'not set';
  const row = settingsModel(model), own = row?.harnesses?.[0] || row?.runtime || harness;
  return modelChoiceWords(model, settingsEffortName(effort), harness, own) || settingsModelName(model);
};
// A bot with no model of its own follows the team default; say which one it resolves to.
function settingsDefaultLabel(e) {
  if (e.model || !e.resolved_model) return 'not set';
  const row = settingsModel(e.resolved_model);
  return `team default (${settingsChoiceLabel(e.resolved_runtime, e.resolved_model, row?.default_effort || '')})`;
}
function settingsAllChoices() {
  // Once the owner has chosen providers, only their models are offered (a bot already on another
  // one keeps its own choice in the editor).
  const enabled = SETTINGS_DATA.enabledProviders || [];
  return (SETTINGS_DATA.models || []).filter(model => !model.deprecated &&
    (!enabled.length || !model.provider || enabled.includes(model.provider) || model.id === SETTINGS_KEEP_MODEL)).flatMap(model =>
    (model.harnesses || [model.runtime]).flatMap(harness =>
      (model.efforts?.length ? model.efforts : [model.default_effort || '']).map(effort => {
        const label = settingsChoiceLabel(harness, model.id, effort);
        const search = [harness, settingsHarnessName(harness), model.id, model.label, effort, label].join(' ').toLowerCase();
        return {harness, model: model.id, effort, label, search, value: settingsChoiceValue(harness, model.id, effort)};
      })));
}
// What the bot's computer cannot run, as the server judges it (backend/readiness.py `can_run`, sent with
// /v2/operations as `runnable`): {can_run: false, problem, short, computer, sign_in, move}. Not listed: not refused.
function settingsRuntimeBlock(e, harness) {
  const answer = e && !e.agent && harness ? SETTINGS_DATA.runnable?.[e.name]?.[harness] : null;
  if (!answer || answer.can_run !== false) return null;
  return {runtime: settingsHarness(harness)?.runtime || harness, short: answer.short || "can't run", reason: answer.problem || "Can't run here",
    computer: answer.computer, move: answer.move,
    signIn: answer.sign_in && (settingsIsAdmin() || settingsMachineOperator(answer.computer?.id) === S.me?.id) ? answer.sign_in : null};
}
const settingsMachineOperator = id => (SETTINGS_DATA.machines || []).find(row => row.id === id)?.operator;
// One line per harness this computer cannot run that has a way out: sign in here, or move the bot.
function settingsBlockedNote(e, harnesses) {
  return harnesses.map(id => [id, settingsRuntimeBlock(e, id)]).filter(([, block]) => block && (block.move || block.signIn))
    .filter(([, block], i, rows) => rows.findIndex(([, other]) => other.runtime === block.runtime) === i)
    .map(([id, block]) => `<p class="settings-choice-block" data-choice-block="${esc(block.runtime)}"><span>${block.reason.toLowerCase().includes(block.runtime) ? '' : esc(harnessWords(block.runtime) || settingsHarnessName(id)) + ': '}${esc(block.reason)}</span>
      ${block.signIn ? `<button class="ghost" type="button" data-model-login data-runner="${esc(block.signIn.runner_id)}" data-runtime="${esc(block.runtime)}" data-machine="${esc(block.signIn.computer)}">Sign in</button>` : ''}
      ${block.move ? `<button class="ghost" type="button" data-choice-move="${esc(block.move.id)}" data-machine="${esc(block.move.label)}">Move to ${esc(block.move.label)}</button>` : ''}</p>`).join('');
}
async function settingsMoveForModel(button) {
  const box = button.closest('[data-bot-choice]'), e = S.emps.find(row => row.name === box?.dataset.botChoice);
  if (!e || !await settingsConfirmTransition(e, 'machine', button.dataset.machine)) return;
  await settingsBeginTransition(button, e, {kind: 'machine', runner_id: button.dataset.choiceMove,
    expected_generation: e.machine?.generation || 0, expected_revision: e.revision});
}
document.addEventListener('click', event => {
  const move = event.target.closest('[data-choice-move]');
  if (move) void settingsMoveForModel(move);
});
// Keep the three choices local until Apply, so selecting a harness cannot start a transition.
function settingsChoiceFields(current = '', disabled = false, none = false, label = 'Model settings') {
  return `<div class="settings-model-choice" role="group" aria-label="${esc(label)}" data-choice-fields data-current="${esc(current)}" ${disabled ? 'data-readonly' : ''} ${none ? 'data-allow-none' : ''}>
    <label>Harness / provider<select data-choice-harness aria-label="Harness / provider" ${disabled ? 'disabled' : ''}></select></label>
    <label>Model<select data-choice-model aria-label="Model" ${disabled ? 'disabled' : ''}></select></label>
    <label>Effort<select data-choice-effort aria-label="Effort" ${disabled ? 'disabled' : ''}></select></label>
    <div class="settings-choice-blocks" data-choice-blocks></div>
  </div>`;
}
function settingsWireChoiceFields(fields, onChange = () => {}) {
  const harness = fields.querySelector('[data-choice-harness]');
  const model = fields.querySelector('[data-choice-model]');
  const effort = fields.querySelector('[data-choice-effort]');
  const choices = settingsAllChoices(), readonly = fields.hasAttribute('data-readonly');
  // A bot's own Model control greys out what its computer cannot run; bulk change checks each bot on Apply.
  const box = fields.closest('[data-bot-choice]');
  const bot = box?.dataset.kind === 'model' ? S.emps.find(row => row.name === box.dataset.botChoice) : null;
  const block = id => bot ? settingsRuntimeBlock(bot, id) : null;
  const option = (value, label, selected, disabled = false) => `<option value="${esc(value)}" ${selected ? 'selected' : ''} ${disabled ? 'disabled' : ''}>${esc(label)}</option>`;
  let picked = settingsChoiceFromValue(fields.dataset.current);
  const paint = () => {
    const harnesses = [...new Set(choices.map(row => row.harness))];
    harness.innerHTML = option('', fields.hasAttribute('data-allow-none') ? 'None' : 'Choose harness', !picked.harness)
      + (picked.harness && !harnesses.includes(picked.harness) ? option(picked.harness, settingsHarnessName(picked.harness) || picked.harness, true, true) : '')
      + harnesses.map(id => { const why = block(id);
        // Short, so a narrow select does not cut it off: "Claude Code (sign in)".
        return option(id, why ? `${harnessWords(why.runtime)} (${why.short})` : settingsHarnessName(id) || id, id === picked.harness, !!why && id !== picked.harness); }).join('');
    harness.title = block(picked.harness)?.reason || '';
    // The reason and its Sign in / Move line only in the open bot editor: the table already marks the bot's row,
    // and every bot on that computer would repeat it.
    const notes = fields.querySelector('[data-choice-blocks]');
    if (notes) notes.innerHTML = bot && !readonly && fields.closest('#bot-editor') ? settingsBlockedNote(bot, harnesses) : '';
    const models = [...new Set(choices.filter(row => row.harness === picked.harness).map(row => row.model))];
    model.innerHTML = option('', 'Choose model', !picked.model, true)
      + (picked.model && !models.includes(picked.model) ? option(picked.model, `${settingsModelName(picked.model)} (current)`, true, true) : '')
      + models.map(id => option(id, settingsModelName(id), id === picked.model)).join('');
    const efforts = choices.filter(row => row.harness === picked.harness && row.model === picked.model).map(row => row.effort);
    effort.innerHTML = (!picked.effort && efforts.length ? option('', 'Choose effort', true, true) : '')
      + (!efforts.length ? option(picked.effort, settingsEffortName(picked.effort) || 'Choose effort', true, true) : '')
      + (picked.effort && efforts.length && !efforts.includes(picked.effort) ? option(picked.effort, `${settingsEffortName(picked.effort)} (current)`, true, true) : '')
      + efforts.map(id => option(id, id === 'as-configured' ? 'Managed by harness' : settingsEffortName(id), id === picked.effort)).join('');
    harness.disabled = readonly;
    model.disabled = readonly || !picked.harness || !models.length;
    effort.disabled = readonly || efforts.length <= 1;
  };
  const value = () => picked.model ? settingsChoiceValue(picked.harness, picked.model, picked.effort) : '';
  const valid = () => (choices.some(row => row.value === value()) && !block(picked.harness)) || (!picked.harness && fields.hasAttribute('data-allow-none'));
  harness.onchange = () => {
    picked = {harness: harness.value, model: '', effort: ''};
    paint(); onChange(value(), valid());
  };
  model.onchange = () => {
    const row = settingsModel(model.value);
    const efforts = choices.filter(choice => choice.harness === picked.harness && choice.model === model.value).map(choice => choice.effort);
    picked = {harness: picked.harness, model: model.value,
      effort: efforts.includes(picked.effort) ? picked.effort : (efforts.includes(row?.default_effort) ? row.default_effort : efforts[0] || '')};
    paint(); onChange(value(), valid());
  };
  effort.onchange = () => { picked.effort = effort.value; onChange(value(), valid()); };
  paint();
  return {value, valid};
}
function settingsChoiceCombo(e, kind) {
  const fallback = kind === 'fallback', selected = fallback ? e.fallback : e;
  const current = selected?.model ? settingsChoiceValue(selected.harness || selected.runtime, selected.model,
    selected.reasoning_effort || selected.effort) : '';
  const manage = settingsCanManageBot(e);
  return `<div class="settings-choice" data-bot-choice="${esc(e.name)}" data-kind="${kind}" data-current="${esc(current)}">
    ${!current && !fallback ? `<small class="muted">${esc(settingsDefaultLabel(e))}</small>` : ''}
    ${settingsChoiceFields(current, !manage, fallback, `${fallback ? 'Fallback' : 'Model'} for ${e.display_name || e.name}`)}
    ${manage ? '<button type="button" class="ghost" data-choice-apply disabled>Apply</button>' : ''}
  </div>`;
}
// In the bots table a model or fallback is one short line; its three pickers open in a popover under it.
function settingsChoiceCell(e, kind) {
  const fallback = kind === 'fallback', selected = fallback ? e.fallback : e;
  const label = selected?.model ? settingsChoiceLabel(selected.harness || selected.runtime, selected.model, selected.reasoning_effort || selected.effort)
    : fallback ? 'None' : settingsDefaultLabel(e);
  const text = `<span class="sb-pick-text${selected?.model ? '' : ' muted'}">${esc(label)}</span>`;
  if (!settingsCanManageBot(e)) return `<span class="sb-pick ro" title="${esc(label)}">${text}</span>`;
  return `<button type="button" class="sb-pick" data-choice-open="${esc(e.name)}" data-kind="${kind}" aria-haspopup="dialog" aria-expanded="false"
    aria-label="${fallback ? 'Fallback' : 'Model'} for ${esc(e.display_name || e.name)}: ${esc(label)}" title="${esc(label)}">${text}<span class="sb-pick-caret" aria-hidden="true"></span></button>`;
}
function settingsChoicePopover(anchor) {
  let pop = $('#sb-choice-pop');
  if (pop?.matches(':popover-open')) { const same = pop.anchor === anchor; pop.hidePopover(); if (same) return; }
  const e = S.emps.find(row => row.name === anchor.dataset.choiceOpen);
  if (!e) return;
  if (!pop) {
    pop = document.createElement('div');
    pop.id = 'sb-choice-pop'; pop.className = 'tl-pop sb-choice-pop'; pop.popover = 'auto';
    pop.setAttribute('role', 'dialog');
    pop.addEventListener('toggle', ev => {
      if (ev.newState !== 'closed') return;
      pop.anchor?.setAttribute('aria-expanded', 'false');
      if (pop.anchor?.isConnected && (pop.contains(document.activeElement) || document.activeElement === document.body)) pop.anchor.focus();
    });
    document.body.append(pop);
  }
  pop.anchor = anchor;
  const kind = anchor.dataset.kind;
  pop.setAttribute('aria-label', `${kind === 'fallback' ? 'Fallback' : 'Model'} for ${e.display_name || e.name}`);
  pop.innerHTML = `<div class="sb-choice-head">${kind === 'fallback' ? 'Fallback' : 'Model'} · ${esc(e.display_name || e.name)}</div>${settingsChoiceCombo(e, kind)}`;
  const box = pop.querySelector('[data-bot-choice]');
  box.querySelector('[data-choice-apply]')?.classList.replace('ghost', 'primary');
  box.insertAdjacentHTML('beforeend', '<button type="button" class="ghost" data-choice-cancel>Cancel</button>');
  box.querySelector('[data-choice-cancel]').onclick = () => pop.hidePopover();
  // Apply closes the popover first: a model change continues in its own dialog.
  settingsWireCombos(pop, async (target, value) => { pop.hidePopover(); await settingsPickChoice(target, value); });
  pop.showPopover();
  anchor.setAttribute('aria-expanded', 'true');
  tasksMenuPlace(pop, anchor);
  pop.querySelector('select:not(:disabled)')?.focus();
}
function settingsWireCombos(root, onPick = settingsPickChoice) {
  root.querySelectorAll('[data-bot-choice]').forEach(box => {
    const button = box.querySelector('[data-choice-apply]');
    const mount = () => {
      const fields = box.querySelector('[data-choice-fields]');
      fields.dataset.current = box.dataset.current || '';
      const picker = settingsWireChoiceFields(fields, (value, valid) => {
        if (button) button.disabled = !valid || value === (box.dataset.current || '');
      });
      if (!button) return;
      button.disabled = true;
      button.onclick = async () => {
        if (!picker.valid()) return;
        const value = picker.value();
        box.querySelectorAll('select,button').forEach(control => control.disabled = true);
        try { await onPick(box, value); }
        finally { if (box.isConnected) mount(); }
      };
    };
    mount();
  });
}
async function settingsPickChoice(box, value) {
  const slug = box.dataset.botChoice, e = S.emps.find(row => row.name === slug);
  const current = box.dataset.current || '';
  if (!e || value === current) return;
  const input = box.querySelector('input[type=search]');
  if (box.dataset.kind === 'fallback') {
    try {
      const choice = value ? settingsChoiceFromValue(value) : null;
      await post(`/v2/bots/${encodeURIComponent(slug)}/fallback`, {
        fallback: choice ? {harness: choice.harness, model: choice.model, effort: choice.effort} : null,
        expected_revision: e.revision});
      await loadSettings();
      toast(choice ? `${e.display_name} fallback is ${settingsChoiceLabel(choice.harness, choice.model, choice.effort)}`
        : `${e.display_name} fallback is none`);
    } catch (error) {
      if (input) input.value = input.defaultValue;
      toast(error.message, true);
    }
    return;
  }
  const choice = settingsChoiceFromValue(value);
  if (!choice.model || !choice.effort || !choice.harness) { if (input) input.value = input.defaultValue; return; }
  if (!await settingsConfirmTransition(e, 'model', settingsChoiceLabel(choice.harness, choice.model, choice.effort))) {
    if (input) input.value = input.defaultValue;
    return;
  }
  await settingsBeginTransition(box, e, {kind:'model', model:choice.model, effort:choice.effort,
    harness:choice.harness, expected_generation:e.machine?.generation || 0, expected_revision:e.revision});
}
function settingsMachineSelect(e) {
  if (e.agent) return settingsAgentCell(e);
  const current = e.machine?.runner_id || '';
  const machines = SETTINGS_DATA.machines.filter(machine => !machine.revoked_at &&
    (S.me?.role === 'owner' || machine.operator === S.me?.id));
  const unassigned = `Not assigned · ${settingsPersonName(e.operator)}`;
  const currentLabel = e.machine ? `${e.machine.label} · ${settingsPersonName(e.machine.operator)}` : unassigned;
  const seen = e.machine?.last_seen ? `last seen ${ago(e.machine.last_seen)}` : e.machine ? 'waiting for its first heartbeat' : 'register a computer first';
  return `<select class="settings-inline-select" data-bot-machine="${esc(e.name)}" data-current="${esc(current)}"
    aria-label="Computer for ${esc(e.display_name)}" title="${esc(`${currentLabel} · ${seen}`)}" ${settingsCanManageBot(e) && machines.length ? '' : 'disabled'}>
      ${current ? '' : `<option value="" selected disabled>${esc(unassigned)}</option>`}
      ${machines.map(machine => `<option value="${esc(machine.id)}" ${machine.id === current ? 'selected' : ''}>${esc(machine.label)} · ${esc(settingsPersonName(machine.operator))}</option>`).join('')}
    </select>`;
}
// A problem worth a badge on the bot's row; a bot that is fine shows nothing.
function settingsBotProblem(e) {
  if (e.status !== 'active') return '';
  if (e.agent?.synced) return e.online ? '' : 'history not synced';
  if (e.agent) return !e.agent.credential ? 'no credential' : e.online ? '' : 'not reporting';
  return !e.machine ? 'no computer' : e.online && e.ready ? '' : e.online ? (e.readiness?.problems?.[0] || 'Computer not ready') : 'offline';
}
// Settings → Bots search, filters and bulk model change ("filter by computer, filter
// by model, select in bulk, and change model in bulk"). Filters are remembered per browser; the
// search, like Routines' search, lasts while the page is open; the selection is not remembered, and
// it is trimmed to what the search and filters show so a bulk change never reaches a bot the person
// cannot see. Bulk apply is the single-row path run once per bot, not a new route.
// On a phone the selects fold behind a Filters button and the checkboxes behind Select (`filtersOpen`, `picking`).
const SETTINGS_BOTS_FILTER_KEY = 'hub.settings.bots.filters';
const SETTINGS_BOTS_VIEW = {q: '', computer: '', model: '', effort: '', selected: new Set(), filtersOpen: false, picking: false};
try {
  const {computer = '', model = '', effort = ''} = JSON.parse(localStorage.getItem(SETTINGS_BOTS_FILTER_KEY) || '{}');
  Object.assign(SETTINGS_BOTS_VIEW, {computer, model, effort});
} catch {}
function settingsBotsRemember() {
  const {computer, model, effort} = SETTINGS_BOTS_VIEW;
  try { localStorage.setItem(SETTINGS_BOTS_FILTER_KEY, JSON.stringify({computer, model, effort})); } catch {}
}
const settingsBotHarness = e => e.harness || e.runtime || '';
const settingsBotEffort = e => e.reasoning_effort || e.effort || '';
const settingsBotComputerKey = e => e.agent ? 'agent' : e.machine?.runner_id || 'none';
const settingsBotModelKey = e => e.agent ? 'agent' : `${settingsBotHarness(e)}::${e.model || ''}`;
const settingsBotSelectable = e => !e.agent && settingsCanManageBot(e);
function settingsBotsFilterOptions(rows) {
  const count = (map, key, label) => { const row = map.get(key) || {key, label, n: 0}; row.n++; map.set(key, row); };
  const computers = new Map(), models = new Map(), efforts = new Map();
  rows.forEach(e => {
    count(computers, settingsBotComputerKey(e), e.agent ? 'External agents'
      : e.machine ? `${e.machine.label} · ${settingsPersonName(e.machine.operator)}` : 'Unassigned');
    count(models, settingsBotModelKey(e), e.agent ? "External agent's own model"
      : settingsChoiceLabel(settingsBotHarness(e), e.model, ''));
    if (!e.agent && settingsBotEffort(e)) count(efforts, settingsBotEffort(e), settingsEffortName(settingsBotEffort(e)));
  });
  const sorted = map => [...map.values()].sort((a, b) => a.label.localeCompare(b.label));
  return {computers: sorted(computers), models: sorted(models), efforts: sorted(efforts)};
}
// The search box: every word of the query appears in the bot's name, slug, role, description or team.
const settingsBotMatches = (e, q) => !q.trim() || searchIncludes([e.display_name, botDisplayName(e.name), e.name, e.role,
  e.description, e.team && teamLabel(e.team)], q);
function settingsBotsVisible(rows) {
  const {q, computer, model, effort} = SETTINGS_BOTS_VIEW;
  return rows.filter(e => (!computer || settingsBotComputerKey(e) === computer)
    && (!model || settingsBotModelKey(e) === model)
    && (!effort || (!e.agent && settingsBotEffort(e) === effort))
    && settingsBotMatches(e, q));
}
function settingsBotsFilterHTML(options, shown, total) {
  const view = SETTINGS_BOTS_VIEW, active = ['computer', 'model', 'effort'].filter(name => view[name]).length;
  const picking = view.picking || view.selected.size > 0;
  const select = (name, label, all, list) => `<select class="settings-inline-select" data-bots-filter="${name}" aria-label="Filter bots by ${label.toLowerCase()}">
      <option value="">${all}</option>${list.map(row => `<option value="${esc(row.key)}" ${row.key === view[name] ? 'selected' : ''}>${esc(row.label)} (${row.n})</option>`).join('')}</select>`;
  return `<div class="settings-bots-filters${view.filtersOpen ? ' open' : ''}">
    <span class="sb-search"><input class="settings-filter-search" type="search" autocomplete="off" spellcheck="false" enterkeyhint="search" data-bots-search
      value="${esc(view.q)}" placeholder="Search bots" aria-label="Search bots" aria-controls="settings-bots-list">
      <button type="button" class="sb-search-clear" data-bots-search-clear aria-label="Clear search" ${view.q ? '' : 'hidden'}>×</button></span>
    <button type="button" class="ghost sb-filters-toggle" data-bots-filters-toggle aria-expanded="${view.filtersOpen}" aria-controls="settings-bots-selects">Filters${active ? ` <span class="cnt">${active}</span>` : ''}</button>
    <span class="sb-selects" id="settings-bots-selects">${select('computer', 'Computer', 'All computers', options.computers)}
    ${select('model', 'Model', 'All models', options.models)}${select('effort', 'Effort', 'Any effort', options.efforts)}</span>
    <span class="settings-bots-count" data-bots-count role="status">${shown === total ? '' : `${shown} of `}${total} bot${total === 1 ? '' : 's'}</span>
    <span class="sb-pick-tools"><button type="button" class="linkish" data-bots-pick-all ${picking ? '' : 'hidden'}>Select all</button>
      <button type="button" class="linkish" data-bots-picking aria-pressed="${picking}">${picking ? 'Done' : 'Select'}</button></span></div>
    <div class="settings-bulk" data-bots-bulk hidden><strong data-bulk-count></strong>
      <button class="primary" type="button" data-bulk-model>Change model…</button><button class="ghost" type="button" data-bulk-clear>Clear</button></div>`;
}
function settingsBotsSyncSelection(el, visible) {
  const selectable = visible.filter(settingsBotSelectable).map(e => e.name);
  const picked = selectable.filter(slug => SETTINGS_BOTS_VIEW.selected.has(slug)).length;
  const all = el.querySelector('[data-bots-select-all]');
  if (all) { all.checked = !!selectable.length && picked === selectable.length; all.indeterminate = picked > 0 && picked < selectable.length; all.disabled = !selectable.length; }
  const bar = el.querySelector('[data-bots-bulk]');
  if (bar) { bar.hidden = !SETTINGS_BOTS_VIEW.selected.size; bar.querySelector('[data-bulk-count]').textContent = `${SETTINGS_BOTS_VIEW.selected.size} selected`; }
}
function settingsBulkModelDialog() {
  const bots = [...SETTINGS_BOTS_VIEW.selected].map(slug => S.emps.find(e => e.name === slug)).filter(Boolean);
  if (!bots.length) return;
  const dialog = document.createElement('dialog'); dialog.className = 'tmodal settings-bulk-dialog';
  dialog.setAttribute('aria-labelledby', 'bulk-model-title');
  let choice = null, running = false, ran = false;
  const paint = () => {
    const label = choice ? settingsChoiceLabel(choice.harness, choice.model, choice.effort) : '';
    dialog.innerHTML = `<div class="tmodal-head"><h2 id="bulk-model-title">Change model · ${bots.length} bot${bots.length === 1 ? '' : 's'}</h2><span class="spacer"></span><button class="ghost" type="button" data-bulk-close aria-label="Close">✕</button></div>
      <div class="transition-body">
        <div class="settings-choice" data-bot-choice="" data-kind="model" data-current="${esc(choice ? settingsChoiceValue(choice.harness, choice.model, choice.effort) : '')}">
          ${settingsChoiceFields(choice ? settingsChoiceValue(choice.harness, choice.model, choice.effort) : '')}</div>
        <p data-bulk-destination>${choice ? `Destination: ${esc(label)}` : ''}</p>
        <div class="transition-progress"><strong>Each bot starts a fresh provider session.</strong>
          <p>History is kept. A bot mid-turn is skipped; retry it after.</p></div>
        <ul class="settings-bulk-list" data-bulk-list>${bots.map(e => `<li data-bulk-bot="${esc(e.name)}" data-state="pending"><span>${esc(e.display_name || e.name)}</span><span class="settings-cell-note">${esc(settingsChoiceLabel(settingsBotHarness(e), e.model, settingsBotEffort(e)))}</span></li>`).join('')}</ul>
        <p role="status" data-bulk-summary></p>
        <div class="transition-actions"><button class="primary" type="button" data-bulk-apply ${choice ? '' : 'disabled'}>Change ${bots.length} bot${bots.length === 1 ? '' : 's'}</button><button class="ghost" type="button" data-bulk-close>Cancel</button></div></div>`;
    dialog.querySelectorAll('[data-bulk-close]').forEach(button => button.onclick = () => dialog.close());
    settingsWireChoiceFields(dialog.querySelector('[data-choice-fields]'), (value, valid) => {
      choice = valid ? settingsChoiceFromValue(value) : null;
      dialog.querySelector('[data-bulk-apply]').disabled = !choice;
      dialog.querySelector('[data-bulk-destination]').textContent = choice
        ? `Destination: ${settingsChoiceLabel(choice.harness, choice.model, choice.effort)}` : '';
      // Before Apply: say which bots' computers cannot run this choice.
      bots.forEach(e => { const why = choice && settingsRuntimeBlock(e, choice.harness);
        mark(e.name, why ? 'blocked' : 'pending', why ? `Can't run: ${why.reason}` : settingsChoiceLabel(settingsBotHarness(e), e.model, settingsBotEffort(e))); });
    });
    dialog.querySelector('[data-bulk-apply]').onclick = () => void run(bots);
  };
  const mark = (slug, state, note) => {
    const row = dialog.querySelector(`[data-bulk-bot="${CSS.escape(slug)}"]`); if (!row) return;
    row.dataset.state = state; row.querySelector('.settings-cell-note').textContent = note;
  };
  const run = async targets => {
    if (running || !choice) return;
    running = ran = true;
    const apply = dialog.querySelector('[data-bulk-apply]');
    apply.disabled = true;
    dialog.querySelectorAll('[data-bot-choice] select,[data-choice-apply]').forEach(control => control.disabled = true);
    const results = {changed: 0, skipped: 0, blocked: 0, failed: []};
    for (const e of targets) {
      // A bot whose computer cannot run the choice is reported, not sent: the server would refuse it anyway.
      const why = settingsRuntimeBlock(e, choice.harness);
      if (why) { mark(e.name, 'blocked', `Can't run: ${why.reason}${why.move ? ` · move to ${why.move.label}` : ''}`); results.blocked++; continue; }
      mark(e.name, 'working', 'Changing…');
      const result = await settingsBulkApplyOne(e.name, choice);
      mark(e.name, result.state, result.note);
      if (result.state === 'failed') results.failed.push(e); else results[result.state]++;
    }
    running = false;
    const summary = [`${results.changed} changed`, results.skipped ? `${results.skipped} already on it` : '',
      results.blocked ? `${results.blocked} can't run it` : '',
      results.failed.length ? `${results.failed.length} failed` : ''].filter(Boolean).join(' · ');
    dialog.querySelector('[data-bulk-summary]').textContent = summary;
    const actions = dialog.querySelector('.transition-actions');
    actions.innerHTML = `${results.failed.length ? '<button class="ghost" type="button" data-bulk-retry>Retry failed</button>' : ''}<button class="primary" type="button" data-bulk-close>Done</button>`;
    actions.querySelector('[data-bulk-close]').onclick = () => dialog.close();
    const retry = actions.querySelector('[data-bulk-retry]');
    if (retry) retry.onclick = () => void run(results.failed);
    await loadSettings();
  };
  // Cancel keeps the selection; once a change has run, the next bulk action starts fresh.
  dialog.onclose = () => { dialog.remove(); if (ran) { SETTINGS_BOTS_VIEW.selected.clear(); void loadSettings(); } };
  document.body.appendChild(dialog); paint(); dialog.showModal();
}
// One bot, the same request the row's Model control sends. A stale revision (someone changed
// the bot a moment ago) refreshes the bot list and tries once more; anything else is reported.
async function settingsBulkApplyOne(slug, choice) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const e = S.emps.find(row => row.name === slug);
    if (!e) return {state: 'failed', note: 'This bot is no longer listed'};
    if (settingsBotHarness(e) === choice.harness && e.model === choice.model && settingsBotEffort(e) === choice.effort)
      return {state: 'skipped', note: 'Already on this model'};
    try {
      const t = await post(`/v2/bots/${encodeURIComponent(slug)}/transitions`, {kind: 'model', model: choice.model,
        effort: choice.effort, harness: choice.harness, expected_generation: e.machine?.generation || 0,
        expected_revision: e.revision});
      return t.state === 'applied' ? {state: 'changed', note: `Now ${settingsChoiceLabel(choice.harness, choice.model, choice.effort)}`}
        : {state: 'failed', note: `Change ${t.state}; open the bot's Model control to finish it`};
    } catch (error) {
      const code = error.body?.error?.code;
      if (code === 'unchanged') return {state: 'skipped', note: 'Already on this model'};
      if (code === 'version_conflict' && attempt === 0) {
        try { S.emps = namedRoster(await get('/employees')); } catch {}
        continue;
      }
      return {state: 'failed', note: error.message || 'The change failed'};
    }
  }
  return {state: 'failed', note: 'Bot settings kept changing; refresh and try again'};
}
// Archived bots the person may bring back (Restore), listed under the bots table. One read is kept for a while.
let SETTINGS_ARCHIVED = null;
const settingsMayRestore = b => settingsIsAdmin() || b.operator === S.me?.id || (b.bot_owners || []).some(o => o.id === S.me?.id);
async function settingsArchivedBots(el) {
  if (!SETTINGS_ARCHIVED || Date.now() - SETTINGS_ARCHIVED.at > 30000) {
    try { SETTINGS_ARCHIVED = {at: Date.now(), bots: (await get('/v2/bots?include_archived=1')).filter(b => b.state === 'archived' && settingsMayRestore(b))}; }
    catch { return; }
  }
  if (!el.isConnected || !SETTINGS_ARCHIVED.bots.length) return;
  el.querySelector('[data-archived-bots]')?.remove();
  const box = document.createElement('div'); box.dataset.archivedBots = '';
  box.innerHTML = `<h3>Archived</h3>${SETTINGS_ARCHIVED.bots.map(b => `<div class="row" data-archived-bot="${esc(b.slug)}"><strong>${esc(b.display_name || b.slug)}</strong>
    <span class="spacer"></span><button class="ghost" type="button" data-bot-restore="${esc(b.slug)}">Restore</button></div>`).join('')}`;
  el.appendChild(box);
}
async function settingsRestoreBot(slug, button) {
  button.disabled = true;
  try {
    const done = await post(`/v2/bots/${encodeURIComponent(slug)}/restore`, {});
    SETTINGS_ARCHIVED = null;
    toast(`Restored ${slug}${done.agent && !done.agent.credential ? '; pair or create its credential' : ''}`);
    await loadSettings();
  } catch (error) { toast(error.message, true); button.disabled = false; }
}
function renderSettingsBots() {
  const el = $('#set-bots'); if (!el) return;
  const all = S.emps.slice().sort((a,b) => (a.team || '').localeCompare(b.team || '') || byBotOrder(a, b) || settingsBotName(a.name).localeCompare(settingsBotName(b.name)));
  const options = settingsBotsFilterOptions(all);
  // A remembered filter whose computer or model no longer exists would hide every bot silently.
  ['computer', 'model', 'effort'].forEach(name => {
    const list = options[{computer: 'computers', model: 'models', effort: 'efforts'}[name]];
    if (SETTINGS_BOTS_VIEW[name] && !list.some(row => row.key === SETTINGS_BOTS_VIEW[name])) SETTINGS_BOTS_VIEW[name] = '';
  });
  const rows = settingsBotsVisible(all);
  const shownSelectable = new Set(rows.filter(settingsBotSelectable).map(e => e.name));
  [...SETTINGS_BOTS_VIEW.selected].forEach(slug => { if (!shownSelectable.has(slug)) SETTINGS_BOTS_VIEW.selected.delete(slug); });
  const pick = e => settingsBotSelectable(e)
    ? `<input type="checkbox" data-bot-pick="${esc(e.name)}" aria-label="Select ${esc(e.display_name)}" ${SETTINGS_BOTS_VIEW.selected.has(e.name) ? 'checked' : ''}>`
    : `<input type="checkbox" disabled aria-label="${esc(e.display_name)} cannot be changed here" title="${e.agent ? 'An external agent uses its own model' : 'You do not own this bot'}">`;
  const stack = e => {
    const owners = (e.bot_owners || []).length ? e.bot_owners : (e.users || []);
    const names = owners.map(o => o.name || o.id);
    return `<span class="sb-stack" title="${esc(names.join(', ') || 'No owner')}">${owners.slice(0, 3).map(o => personCircle(o.name || o.id, 22)).join('')}${owners.length > 3 ? `<span class="more">+${owners.length - 3}</span>` : ''}${owners.length ? '' : '<span class="muted">None</span>'}</span>`;
  };
  const row = e => {
    const problem = settingsBotProblem(e);
    const badges = `${isBuiltInBot(e.name) ? '<span class="pill" data-built-in>Built-in</span>' : ''}${e.status && e.status !== 'active' ? `<span class="pill ${e.status === 'paused' ? 'waiting' : ''}">${esc(statusWord(e.status))}</span>` : ''}`;
    const model = e.agent ? `<span class="muted" title="${esc(e.agent.model ? `profile's model · ${e.agent.model}` : "the profile's own model")}">${esc(agentKind(e.agent))}</span>` : settingsChoiceCell(e, 'model');
    return `<tr data-settings-bot="${esc(e.name)}"><td class="settings-pick">${pick(e)}</td>
      <td class="sb-cell-name"><div class="sb-bot">${avatar(e.name, 27, stateOf(e.name))}<div class="sb-text"><div class="sb-line"><a class="sb-name" href="#/bot/${esc(e.name)}">${shownName(e)}</a>${botDisplayName(e.name) !== (e.display_name || e.name) ? `<span class="mono muted">${esc(e.name)}</span>` : ''}${badges}</div>${e.team || problem ? `<small>${e.team ? esc(teamLabel(e.team)) : ''}${e.team && problem ? ' · ' : ''}${problem ? `<span class="sb-problem">${esc(problem)}</span>` : ''}</small>` : ''}</div></div></td>
      <td class="sb-cell-access" aria-label="Access: ${esc(settingsAccessWord(e))}"><span class="sb-access-label" aria-hidden="true">Access:</span>${settingsAccessCell(e)}</td><td class="sb-cell-model">${model}</td>
      <td class="sb-cell-fallback">${e.agent ? '<span class="muted">-</span>' : settingsChoiceCell(e, 'fallback')}</td>
      <td class="sb-cell-owners">${stack(e)}</td><td class="sb-cell-computer">${settingsMachineSelect(e)}</td>
      <td class="sb-cell-limit">${useLimitButton(e.name, e.display_name, SETTINGS_DATA.limits?.bots?.[e.name])}</td>
      <td class="settings-row-actions">${settingsCanManageBot(e) ? `<button class="ghost" type="button" data-edit-bot="${esc(e.name)}" aria-label="Edit ${esc(e.display_name)}">Edit</button>` : ''}</td></tr>`;
  };
  const q = SETTINGS_BOTS_VIEW.q.trim();
  const none = q ? `<div class="empty">No bots match “${esc(q)}”${['computer', 'model', 'effort'].some(name => SETTINGS_BOTS_VIEW[name]) ? ' with these filters' : ''}.</div>`
    : '<div class="empty">No bots match these filters.</div>';
  const html = !all.length ? '<div class="empty">No bots are registered.</div>' : `${settingsBotsFilterHTML(options, rows.length, all.length)}${rows.length ? `<div class="scroll" id="settings-bots-list"><table class="settings-bots-table${SETTINGS_BOTS_VIEW.picking || SETTINGS_BOTS_VIEW.selected.size ? ' picking' : ''}"><thead><tr><th class="settings-pick"><input type="checkbox" data-bots-select-all aria-label="Select all shown bots"></th><th>Bot</th><th>Access</th><th>Model</th><th>Fallback</th><th>Owners</th><th>Computer</th><th>Limit</th><th aria-label="Actions"></th></tr></thead><tbody>
    ${rows.map(row).join('')}
    </tbody></table></div>` : `<div id="settings-bots-list">${none}</div>`}`;
  // While the search box has the cursor (typing, or the refresh loop meanwhile) it stays the same element:
  // replacing a focused field drops the cursor and closes a phone's keyboard. Everything around it is redrawn.
  const tools = el.querySelector('.settings-bots-filters');
  const search = tools?.querySelector('[data-bots-search]');
  if (all.length && search && search === document.activeElement) {
    const next = document.createElement('template'); next.innerHTML = html;
    const fresh = next.content.querySelector('.settings-bots-filters');
    tools.className = fresh.className;
    for (const part of ['[data-bots-filters-toggle]', '.sb-selects', '[data-bots-count]', '.sb-pick-tools']) tools.querySelector(part).replaceWith(fresh.querySelector(part));
    tools.querySelector('[data-bots-search-clear]').hidden = !SETTINGS_BOTS_VIEW.q;
    fresh.remove();
    [...el.children].forEach(child => { if (child !== tools) child.remove(); });
    tools.after(next.content);
  } else el.innerHTML = html;
  settingsBotsSyncSelection(el, rows);
  void settingsArchivedBots(el);
  // A control that redraws the list takes the keyboard back to itself, or to the search box when it is gone
  // (hidden on a computer, or no longer drawn): a redraw must not drop focus to the page.
  const redraw = selector => {
    renderSettingsBots();
    const target = [...el.querySelectorAll(selector)].find(node => node.getClientRects().length);
    (target || el.querySelector('[data-bots-search]'))?.focus();
  };
  el.onclick = event => {
    const restore = event.target.closest('[data-bot-restore]');
    if (restore) { void settingsRestoreBot(restore.dataset.botRestore, restore); return; }
    const choice = event.target.closest('[data-choice-open]');
    if (choice) { settingsChoicePopover(choice); return; }
    if (event.target.closest('[data-bulk-model]')) { settingsBulkModelDialog(); return; }
    if (event.target.closest('[data-bulk-clear]')) { SETTINGS_BOTS_VIEW.selected.clear(); redraw('[data-bots-picking]'); return; }
    if (event.target.closest('[data-bots-search-clear]')) {
      SETTINGS_BOTS_VIEW.q = ''; renderSettingsBots(); el.querySelector('[data-bots-search]')?.focus(); return;
    }
    // On a phone the checkbox cell is the tap target, not just the 16px box.
    const pickCell = event.target.closest('td.settings-pick');
    if (pickCell && event.target === pickCell) { pickCell.querySelector('input:not(:disabled)')?.click(); return; }
    if (event.target.closest('[data-bots-filters-toggle]')) { SETTINGS_BOTS_VIEW.filtersOpen = !SETTINGS_BOTS_VIEW.filtersOpen; redraw('[data-bots-filters-toggle]'); return; }
    // Done also drops the selection: nothing stays picked behind checkboxes that are no longer shown.
    if (event.target.closest('[data-bots-picking]')) {
      SETTINGS_BOTS_VIEW.picking = !(SETTINGS_BOTS_VIEW.picking || SETTINGS_BOTS_VIEW.selected.size);
      if (!SETTINGS_BOTS_VIEW.picking) SETTINGS_BOTS_VIEW.selected.clear();
      redraw('[data-bots-picking]'); return;
    }
    if (event.target.closest('[data-bots-pick-all]')) {
      const pickable = rows.filter(settingsBotSelectable), on = !pickable.every(e => SETTINGS_BOTS_VIEW.selected.has(e.name));
      pickable.forEach(e => on ? SETTINGS_BOTS_VIEW.selected.add(e.name) : SETTINGS_BOTS_VIEW.selected.delete(e.name));
      el.querySelectorAll('[data-bot-pick]').forEach(box => { box.checked = on; });
      settingsBotsSyncSelection(el, rows); return;
    }
    const cap = event.target.closest('[data-use-limit]');
    if (cap) {
      const slug = cap.dataset.useLimit, name = settingsBotName(slug);
      useLimitDialog(slug, name, SETTINGS_DATA.limits?.bots?.[slug], SETTINGS_DATA.limits?.default, async () => {
        try { SETTINGS_DATA.limits = await get('/v2/usage/limits'); } catch {}
        renderSettingsBots();
      });
      return;
    }
    const bot = event.target.closest('[data-edit-bot]');
    const credential = event.target.closest('[data-agent-credential]');
    const revoke = event.target.closest('[data-agent-revoke]');
    const pairing = event.target.closest('[data-agent-pair]');
    if (pairing) void settingsAgentPair(pairing.dataset.agentPair);
    if (bot) settingsEditBot(bot.dataset.editBot);
    if (credential) void settingsAgentCredential(credential.dataset.agentCredential);
    if (revoke) void settingsAgentRevoke(revoke.dataset.agentRevoke);
  };
  el.oninput = event => {
    const search = event.target.closest('[data-bots-search]');
    if (search) { SETTINGS_BOTS_VIEW.q = search.value; renderSettingsBots(); }
  };
  // Escape empties the search box (as a search field does in Chrome and Safari); an empty box lets it through.
  el.onkeydown = event => {
    const search = event.key === 'Escape' && event.target.closest('[data-bots-search]');
    if (!search || !search.value) return;
    event.preventDefault(); event.stopPropagation();
    search.value = ''; SETTINGS_BOTS_VIEW.q = ''; renderSettingsBots();
  };
  el.onchange = event => {
    if (event.target.closest('[data-bots-search]')) return;
    const filter = event.target.closest('[data-bots-filter]');
    if (filter) { SETTINGS_BOTS_VIEW[filter.dataset.botsFilter] = filter.value; settingsBotsRemember(); redraw(`[data-bots-filter="${filter.dataset.botsFilter}"]`); return; }
    const one = event.target.closest('[data-bot-pick]');
    if (one) { one.checked ? SETTINGS_BOTS_VIEW.selected.add(one.dataset.botPick) : SETTINGS_BOTS_VIEW.selected.delete(one.dataset.botPick); settingsBotsSyncSelection(el, rows); return; }
    if (event.target.closest('[data-bots-select-all]')) {
      const on = event.target.checked;
      rows.filter(settingsBotSelectable).forEach(e => on ? SETTINGS_BOTS_VIEW.selected.add(e.name) : SETTINGS_BOTS_VIEW.selected.delete(e.name));
      el.querySelectorAll('[data-bot-pick]').forEach(box => { box.checked = on; });
      settingsBotsSyncSelection(el, rows); return;
    }
    const machine = event.target.closest('[data-bot-machine]');
    if (machine) void settingsMoveBot(machine);
  };
  settingsWireCombos(el);
  const copyAll = $('#copy-setup-prompt');
  if (copyAll) { copyAll.onclick = () => settingsCopyPrompt(); copyAll.disabled = false; }
  const add = $('#settings-add-bot');
  if (add) { add.onclick = () => settingsEditBot(); add.disabled = false; }
  const fromCatalog = $('#settings-add-catalog');
  if (fromCatalog) { fromCatalog.onclick = () => void settingsCatalogPicker(); fromCatalog.disabled = false; }
}
// The same catalog cards the first run shows, for a team that is already set up. Nothing is
// locked on here: the bots that were required at first run already exist and are filtered out.
async function settingsCatalogPicker() {
  const dialog = $('#catalog-picker'); if (!dialog) return;
  dialog.innerHTML = `<form><div class="tmodal-head"><h2 id="catalog-picker-title">Add from template</h2><span class="spacer"></span>
      <button class="ghost" type="button" data-catalog-close aria-label="Close">✕</button></div>
    <div class="bot-editor-body" id="catalog-picker-body"><div class="empty">Loading templates…</div></div></form>`;
  dialog.onclose = () => { dialog.innerHTML = ''; };
  dialog.querySelectorAll('[data-catalog-close]').forEach(button => button.onclick = () => dialog.close());
  dialog.showModal();
  let cards;
  try { cards = catalogCards(await get('/v2/templates')); }
  catch (error) {
    const body = $('#catalog-picker-body'); if (body) body.innerHTML = `<div class="err">${esc(error.message)}</div>`;
    return;
  }
  if (!dialog.open) return;
  const have = new Set(S.emps.filter(row => row.status !== 'archived').map(row => row.name));
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
    grid.querySelectorAll('[data-cat-card]').forEach(node => {
      const card = catalogCard(state, node.dataset.catCard);
      const text = [card.name, card.slug, card.summary, card.when, ...(card.owns || []), catalogName(state, card)].join(' ').toLowerCase();
      node.hidden = !words.every(word => text.includes(word));
      if (!node.hidden) shown++;
    });
    $('#catalog-picker-count').textContent = `${shown} of ${state.cards.length} templates · ${state.picked.size} selected`;
    $('#catalog-picker-empty').hidden = shown > 0;
  };
  // Keep cards mounted: filtering must preserve edited instructions, choices and selections.
  catalogWire(grid, state, filter);
  search.oninput = filter;
  search.onkeydown = event => { if (event.key === 'Enter') event.preventDefault(); };
  filter();
  search.focus();
  dialog.querySelectorAll('[data-catalog-close]').forEach(button => button.onclick = () => dialog.close());
  const form = dialog.querySelector('form'), status = $('#catalog-picker-status');
  form.onsubmit = async event => {
    event.preventDefault();
    const submit = form.querySelector('[type=submit]');
    const chosen = state.cards.filter(card => state.picked.has(card.slug));
    if (!chosen.length) { status.innerHTML = '<span class="err">Choose at least one bot.</span>'; return; }
    if (!S.me?.id) { status.innerHTML = '<span class="err">Sign in before adding a bot.</span>'; return; }
    submit.disabled = true; status.textContent = 'Adding…';
    try {
      const hints = [];
      for (const card of chosen) {
        if (card.template === 'inbox' && !catalogPerson(state, card)) {
          status.innerHTML = '<span class="err">Choose whose mailbox the message bot reads.</span>';
          submit.disabled = false; return;
        }
        const person = catalogPerson(state, card);
        const slug = card.template === 'inbox' && person ? `${person.id}-inbox`
          : card.template === 'assistant' ? assistantBot() : card.slug;
        const named = catalogName(state, card);
        const display = card.template === 'inbox' && person && named === (card.name || card.slug)
          ? `${person.name || person.id} message bot` : named;
        const added = await post('/v2/bots', {
          slug, display_name: display, description: card.summary || '',
          template: card.template, instructions: catalogInstructions(state, card),
          reports_to: null, status: 'planned', repo: `bot-${slug}`, thread_mode: 'personal',
          model: card.model || '', effort: card.reasoning_effort || '', operator: S.me.id, owners: [S.me.id], runner_id: null});
        if (added.name_hint) hints.push(added.name_hint);
      }
      dialog.close(); await loadSettings(); settingsShow('bots');
      toast(hints.length ? hints.join('; ') : chosen.length === 1 ? `Added ${catalogName(state, chosen[0])}` : `Added ${chosen.length} bots`);
    } catch (error) {
      status.innerHTML = `<span class="err">${esc(error.message)}</span>`;
      if (String(error.message).includes('Team default')) {
        const link = document.createElement('button'); link.type = 'button'; link.className = 'linkish'; link.textContent = 'AI providers';
        link.onclick = () => { dialog.close(); settingsShow('providers'); };
        status.append(' ', link);
      }
      submit.disabled = false;
    }
  };
}
