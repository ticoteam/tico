/* ui/app/settings-bots.js — Settings > Bots: model and computer pickers, search, bulk change, catalog picker
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const settingsBotName = id => botRow(id)?.display_name || botSlug(id);
const settingsPerson = id => SETTINGS_DATA.people.find(person => person.id === id);
const settingsPersonName = id => settingsPerson(id)?.name || id || 'Unassigned';
let SETTINGS_KEEP_MODEL = '';
const settingsModel = id => SETTINGS_DATA.models.find(model => model.id === id);
const settingsHarness = id => (SETTINGS_DATA.harnesses || []).find(row => row.id === id);
const settingsModelName = id => settingsModel(id)?.label || settingsTierName(id) || id || 'Not set';
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
// ----------------------------------------------------------------- Tico AI Gateway picks
// A Gateway bot's model is a pick, not a name: Department · Level · Fast · Version (GET /v2/models `gateway`, backend/gateway_tiers.py).
// The stored model is the id they make: tico-{department}-{level}[-fast][-{version}], no version meaning always the latest.
const settingsIsTico = id => /^tico-/.test(id || '');
const settingsGateway = () => SETTINGS_DATA.gateway?.tiers && Object.keys(SETTINGS_DATA.gateway.tiers).length ? SETTINGS_DATA.gateway : null;
// {tier, department, level, fast, version ('auto' when none)} for a tier id, or null for an old-lineup id or any other.
function settingsTierParse(id) {
  const gw = settingsGateway(), m = /^(tico-[a-z0-9-]+?)(?:-(\d+\.\d+))?$/.exec(String(id || ''));
  const tier = m && gw?.tiers[m[1]];
  return tier ? {tier: m[1], department: tier.department, level: tier.level, fast: tier.fast, version: m[2] || 'auto'} : null;
}
const settingsTierId = t => `${t.tier}${t.version === 'auto' ? '' : '-' + t.version}`;
const settingsTierKey = (department, level, fast) => `tico-${department}-${level}${fast ? '-fast' : ''}`;
function settingsTierName(id) {
  const t = settingsTierParse(id);
  return t ? `${SETTINGS_DATA.gateway.tiers[t.tier].name}${t.version === 'auto' ? ' (auto)' : ' ' + t.version}` : '';
}
// "Claude Sonnet 5.5 · $2.50 / $12.50 per 1M" for a pick, from the catalog's version rows (an old id from the model row).
function settingsTierLine(id) {
  const t = settingsTierParse(id), tier = t && SETTINGS_DATA.gateway.tiers[t.tier];
  const now = tier && tier.versions.find(v => v.version === (t.version === 'auto' ? tier.current : t.version));
  if (now) return `${now.model} · $${now.price.input.toFixed(2)} / $${now.price.output.toFixed(2)} per 1M`;
  return settingsModel(id)?.description?.replace(/ underneath$/, '') || '';
}
// In the model list a Tico model says what runs underneath it: "Tico Mid 1.0 (Claude Sonnet 5.5 underneath)".
const settingsModelPickName = id => {
  const row = settingsModel(id);
  return /^tico-/.test(id) && row?.description ? `${settingsModelName(id)} (${row.description})` : settingsModelName(id);
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
// Harness the team has no provider for: shown greyed out to whoever can turn it on, never silently
// missing, so a shipped vendor arrives with its way in (Muse Code stayed invisible while Meta was off).
// Only admins see them: anyone else gets no way in, just clutter.
function settingsOffHarnesses(choices) {
  const enabled = SETTINGS_DATA.enabledProviders || [];
  if (!enabled.length || !settingsIsAdmin()) return [];
  const have = new Set(choices.map(row => row.harness));
  return (SETTINGS_DATA.harnesses || []).filter(row =>
    !row.external && !have.has(row.id) && (SETTINGS_DATA.models || []).some(model =>
      !model.deprecated && model.provider && !enabled.includes(model.provider) &&
      (model.harnesses || [model.runtime]).includes(row.id)));
}
// One line naming what is off and where to turn it on; the Models section itself says only the owner can.
function settingsOffNote(off) {
  if (!off.length) return '';
  const names = off.map(row => harnessWords(row.id)).join(', ');
  return `<p class="settings-choice-block" data-choice-off><span>${esc(names)} ${off.length === 1 ? 'is' : 'are'} off — turn on ${off.length === 1 ? 'its' : 'their'} provider in Models</span> <a href="#/settings" data-off-providers>Open Models</a></p>`;
}
// What the bot's computer cannot run, as the server judges it (backend/readiness.py `can_run`, sent with
// /v2/operations as `runnable`): {can_run: false, problem, short, computer, sign_in, move}. Not listed: not refused.
function settingsRuntimeBlock(e, harness) {
  const answer = e && !e.agent && harness ? SETTINGS_DATA.runnable?.[e.id]?.[harness] : null;
  if (!answer || answer.can_run !== false) return null;
  return {runtime: settingsHarness(harness)?.runtime || harness, short: answer.short || "can't run", reason: answer.problem || "Can't run here",
    computer: answer.computer, move: answer.move,
    signIn: answer.sign_in && (settingsIsAdmin() || settingsMachineOperator(answer.computer?.id) === S.me?.id) ? answer.sign_in : null};
}
const settingsMachineOperator = id => (SETTINGS_DATA.machines || []).find(row => row.id === id)?.owner_id;
// One line per harness this computer cannot run that has a way out: sign in here, or move the bot.
function settingsBlockedNote(e, harnesses) {
  return harnesses.map(id => [id, settingsRuntimeBlock(e, id)]).filter(([, block]) => block && (block.move || block.signIn))
    .filter(([, block], i, rows) => rows.findIndex(([, other]) => other.runtime === block.runtime) === i)
    .map(([id, block]) => `<p class="settings-choice-block" data-choice-block="${esc(block.runtime)}"><span>${block.reason.toLowerCase().includes(block.runtime) ? '' : esc(harnessWords(block.runtime) || settingsHarnessName(id)) + ': '}${esc(block.reason)}</span>
      ${block.signIn ? `<button class="ghost" type="button" data-model-login data-runner="${esc(block.signIn.runner_id)}" data-runtime="${esc(block.runtime)}" data-machine="${esc(block.signIn.computer)}">Sign in</button>` : ''}
      ${block.move ? `<button class="ghost" type="button" data-choice-move="${esc(block.move.id)}" data-machine="${esc(block.move.label)}">Move to ${esc(block.move.label)}</button>` : ''}</p>`).join('');
}
async function settingsMoveForModel(button) {
  const box = button.closest('[data-bot-choice]'), e = S.emps.find(row => row.id === box?.dataset.botChoice);
  if (!e || !await settingsConfirmTransition(e, 'machine', button.dataset.machine)) return;
  await settingsBeginTransition(button, e, {kind: 'machine', runner_id: button.dataset.choiceMove,
    expected_generation: e.machine?.generation || 0, expected_revision: e.revision});
}
document.addEventListener('click', event => {
  const move = event.target.closest('[data-choice-move]');
  if (move) void settingsMoveForModel(move);
  if (event.target.closest('[data-off-providers]')) settingsShow('models');
});
// Keep the three choices local until Apply, so selecting a harness cannot start a transition. `none` allows no
// choice at all, named by it when it is a string ("Same as bot" for a task model), else "None". `noEffort` hides the
// Effort picker where another one stands beside it (a task model's is Task effort, settingsTaskParts).
function settingsChoiceFields(current = '', disabled = false, none = false, label = 'Model settings', noEffort = false) {
  return `<div class="settings-model-choice" role="group" aria-label="${esc(label)}" data-choice-fields data-current="${esc(current)}" ${disabled ? 'data-readonly' : ''} ${none ? `data-allow-none="${esc(none === true ? 'None' : none)}"` : ''}>
    <label>Harness / provider<select data-choice-harness aria-label="Harness / provider" ${disabled ? 'disabled' : ''}></select></label>
    <label data-choice-model-label>Model<select data-choice-model aria-label="Model" ${disabled ? 'disabled' : ''}></select></label>
    <div class="settings-tier" data-choice-tier hidden></div>
    <label ${noEffort ? 'hidden' : ''}>Effort<select data-choice-effort aria-label="Effort" ${disabled ? 'disabled' : ''}></select></label>
    <div class="settings-choice-blocks" data-choice-blocks></div>
  </div>`;
}
function settingsWireChoiceFields(fields, onChange = () => {}) {
  const harness = fields.querySelector('[data-choice-harness]');
  const model = fields.querySelector('[data-choice-model]');
  const effort = fields.querySelector('[data-choice-effort]');
  const tierBox = fields.querySelector('[data-choice-tier]');
  const modelLabel = fields.querySelector('[data-choice-model-label]');
  const all = settingsAllChoices(), readonly = fields.hasAttribute('data-readonly');
  // The Gateway's picks are made in the tier controls; its 60 rows stay out of the Model list (and one virtual
  // harness, "Tico AI Gateway", stands for them). `real` maps it back to the pi harness the bot runs them on.
  const gw = settingsGateway(), viaTier = !!gw && all.some(row => settingsIsTico(row.model));
  const choices = viaTier ? all.filter(row => !settingsIsTico(row.model)) : all;
  const tierRows = viaTier ? all.filter(row => settingsIsTico(row.model)) : [];
  // A bot's own Model control greys out what its computer cannot run; bulk change checks each bot on Apply.
  const box = fields.closest('[data-bot-choice]');
  const bot = box?.dataset.botChoice ? S.emps.find(row => row.id === box.dataset.botChoice) : null;
  const guard = box?.dataset.kind === 'model' ? bot : null;
  const block = id => guard ? settingsRuntimeBlock(guard, id) : null;
  const option = (value, label, selected, disabled = false) => `<option value="${esc(value)}" ${selected ? 'selected' : ''} ${disabled ? 'disabled' : ''}>${esc(label)}</option>`;
  let picked = settingsChoiceFromValue(fields.dataset.current);
  if (viaTier && settingsIsTico(picked.model)) picked.harness = 'tico';
  let legacy = viaTier && picked.harness === 'tico' && !settingsTierParse(picked.model);   // an old-lineup id, until a control changes it
  const inTier = () => viaTier && picked.harness === 'tico';
  const real = () => inTier() ? {...picked, harness: 'pi'} : picked;
  const own = bot?.gateway_department || 'general';
  // The tier the controls show: the model's own, or (old lineup, nothing chosen yet) the bot's department at Medium.
  const shown = () => settingsTierParse(picked.model) || {tier: settingsTierKey(own, 'medium', false), department: own, level: 'medium', fast: false,
    version: gw.tiers[settingsTierKey(own, 'medium', false)]?.current || '1.0'};
  const tierEfforts = t => { const row = settingsModel(`${t.tier}-${gw.tiers[t.tier]?.current}`) || settingsModel(picked.model);
    return {list: row?.efforts?.length ? [...row.efforts] : ['low', 'high', 'max'], def: row?.default_effort || 'high'}; };
  const setTier = t => {
    const key = settingsTierKey(t.department, t.level, t.fast);
    if (!gw.tiers[key]) return;
    legacy = false;
    // Auto stays Auto; a pinned version stays only where the destination tier has it, else that tier's current one.
    const has = version => (gw.tiers[key].versions || []).some(v => v.version === version);
    const next = {...t, tier: key, version: t.version === 'auto' || has(t.version) ? t.version : gw.tiers[key].current};
    const efforts = tierEfforts(next);
    picked = {harness: 'tico', model: settingsTierId(next), effort: efforts.list.includes(picked.effort) ? picked.effort : efforts.def};
  };
  const paintTier = () => {
    const t = shown(), tier = gw.tiers[t.tier];
    const now = tier?.current;
    const versions = [...(tier?.versions || [])];   // newest first
    tierBox.innerHTML = `<div class="settings-tier-row">
        <label>Department<select data-tier-department aria-label="Department" ${readonly ? 'disabled' : ''}>${gw.departments.map(d =>
          option(d.id, d.name + (bot?.gateway_department === d.id ? ' (from group)' : ''), d.id === t.department)).join('')}</select></label>
        <div class="settings-tier-seg" role="radiogroup" aria-label="Level">${gw.levels.map(l =>
          `<button type="button" role="radio" aria-checked="${l.id === t.level && !legacy}" data-tier-level="${esc(l.id)}" ${readonly ? 'disabled' : ''}>${esc(l.name)}</button>`).join('')}</div>
        <label class="settings-tier-fast"><input type="checkbox" data-tier-fast ${t.fast ? 'checked' : ''} ${readonly ? 'disabled' : ''}>Fast</label>
        <label>Version<select data-tier-version aria-label="Version" ${readonly ? 'disabled' : ''}>
          ${legacy ? option('', `${settingsModelName(picked.model)}`, true, true) : ''}
          ${versions.map(v => option(v.version, v.version + (v.version === now ? ' (current)' : ''), !legacy && t.version === v.version)).join('')}
          ${option('auto', 'Auto (always latest)', !legacy && t.version === 'auto')}</select></label></div>
      <p class="settings-tier-line" data-tier-line>${esc(settingsTierLine(picked.model))}</p>`;
    tierBox.querySelector('[data-tier-department]').onchange = ev => { setTier({...t, department: ev.target.value}); paint(); onChange(value(), valid()); };
    tierBox.querySelectorAll('[data-tier-level]').forEach(b => b.onclick = () => { setTier({...t, level: b.dataset.tierLevel}); paint(); onChange(value(), valid()); });
    tierBox.querySelector('[data-tier-fast]').onchange = ev => { setTier({...t, fast: ev.target.checked}); paint(); onChange(value(), valid()); };
    tierBox.querySelector('[data-tier-version]').onchange = ev => { setTier({...t, version: ev.target.value}); paint(); onChange(value(), valid()); };
  };
  const paint = () => {
    const harnesses = [...new Set(choices.map(row => row.harness))];
    const off = settingsOffHarnesses(choices);
    harness.innerHTML = option('', fields.hasAttribute('data-allow-none') ? fields.getAttribute('data-allow-none') || 'None' : 'Choose harness', !picked.harness)
      + (picked.harness && picked.harness !== 'tico' && !harnesses.includes(picked.harness) ? option(picked.harness, settingsHarnessName(picked.harness) || picked.harness, true, true) : '')
      + harnesses.map(id => { const why = block(id);
        // Short, so a narrow select does not cut it off: "Claude Code (sign in)".
        return option(id, why ? `${harnessWords(why.runtime)} (${why.short})` : settingsHarnessName(id) || id, id === picked.harness, !!why && id !== picked.harness); }).join('')
      + (viaTier ? (() => { const why = block('tico');
        return option('tico', why ? `Tico AI Gateway (${why.short})` : 'Tico AI Gateway', picked.harness === 'tico', !!why && picked.harness !== 'tico'); })() : '')
      + off.filter(row => row.id !== picked.harness)
        .map(row => option(row.id, `${harnessWords(row.id)} (off)`, false, true)).join('');
    harness.title = block(picked.harness === 'tico' ? 'tico' : picked.harness)?.reason || '';
    // The reason and its Sign in / Move line only in the open bot editor: the table already marks the bot's row,
    // and every bot on that computer would repeat it. The provider-off line shows wherever the picker is, since
    // it is about the team, not the bot.
    const notes = fields.querySelector('[data-choice-blocks]');
    if (notes) notes.innerHTML = (guard && !readonly && fields.closest('#bot-editor') ? settingsBlockedNote(guard, harnesses) : '')
      + (!readonly ? settingsOffNote(off) : '');
    const models = [...new Set(choices.filter(row => row.harness === picked.harness).map(row => row.model))];
    modelLabel.hidden = inTier(); tierBox.hidden = !inTier();
    model.innerHTML = option('', 'Choose model', !picked.model, true)
      + (picked.model && !inTier() && !models.includes(picked.model) ? option(picked.model, `${settingsModelName(picked.model)} (current)`, true, true) : '')
      + models.map(id => option(id, settingsModelPickName(id), id === picked.model)).join('');
    let efforts, current = picked.effort;
    if (inTier()) { efforts = tierEfforts(shown()).list; paintTier(); }
    else efforts = choices.filter(row => row.harness === picked.harness && row.model === picked.model).map(row => row.effort);
    effort.innerHTML = (!current && efforts.length ? option('', 'Choose effort', true, true) : '')
      + (!efforts.length ? option(current, settingsEffortName(current) || 'Choose effort', true, true) : '')
      + (current && efforts.length && !efforts.includes(current) ? option(current, `${settingsEffortName(current)} (current)`, true, true) : '')
      + efforts.map(id => option(id, id === 'as-configured' ? 'Managed by harness' : settingsEffortName(id), id === current)).join('');
    harness.disabled = readonly;
    model.disabled = readonly || !picked.harness || !models.length;
    effort.disabled = readonly || efforts.length <= 1;
  };
  const value = () => picked.model ? settingsChoiceValue(real().harness, picked.model, picked.effort) : '';
  const tierValid = () => { const t = settingsTierParse(picked.model);
    return !!t && (t.version === 'auto' || gw.tiers[t.tier].versions.some(v => v.version === t.version)) && tierEfforts(t).list.includes(picked.effort) && !block('tico'); };
  const valid = () => (inTier() ? tierValid() : (choices.some(row => row.value === value()) && !block(picked.harness)))
    || (!picked.harness && fields.hasAttribute('data-allow-none'));
  // Whether the three fields show something other than what the bot has, finished or not.
  const changed = () => { const was = settingsChoiceFromValue(fields.dataset.current), now = real();
    return now.harness !== was.harness || now.model !== was.model || now.effort !== was.effort; };
  harness.onchange = () => {
    picked = {harness: harness.value, model: '', effort: ''};
    if (picked.harness === 'tico') {   // the bot's department at Medium, pinned to the current version
      legacy = false;
      const t = {tier: settingsTierKey(own, 'medium', false), department: own, level: 'medium', fast: false};
      setTier({...t, version: gw.tiers[t.tier]?.current || '1.0'});
    }
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
  return {value, valid, changed};
}
// What each picker kind sets on a bot: its own model, its fallback, or the model its tasks run on (task_model,
// backend/task_models.py; none is the same as the bot).
const SETTINGS_CHOICE_KINDS = {model: {name: 'Model', of: e => e}, fallback: {name: 'Fallback', of: e => e.fallback, none: true},
  task: {name: 'Task model', of: e => e.task_model, none: 'Same as bot'}};
function settingsChoiceCombo(e, kind) {
  const spec = SETTINGS_CHOICE_KINDS[kind], selected = spec.of(e);
  const current = selected?.model ? settingsChoiceValue(selected.harness || selected.runtime, selected.model,
    selected.reasoning_effort || selected.effort) : '';
  const manage = settingsCanManageBot(e);
  return `<div class="settings-choice" data-bot-choice="${esc(e.id)}" data-kind="${kind}" data-current="${esc(current)}">
    ${!current && !spec.none ? `<small class="muted">${esc(settingsDefaultLabel(e))}</small>` : ''}
    ${settingsChoiceFields(current, !manage, spec.none || false, `${spec.name} for ${e.display_name || e.slug}`, kind === 'task')}
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
  return `<button type="button" class="sb-pick" data-choice-open="${esc(e.id)}" data-kind="${kind}" aria-haspopup="dialog" aria-expanded="false"
    aria-label="${fallback ? 'Fallback' : 'Model'} for ${esc(e.display_name || e.slug)}: ${esc(label)}" title="${esc(label)}">${text}<span class="sb-pick-caret" aria-hidden="true"></span></button>`;
}
// A bot's task settings (backend/task_models.py `stored`): a task model, a task effort and a task context length, each
// "Same as bot" until set. The record's model may be '' (its tasks run on the bot's own model).
const settingsTaskSet = task => !!(task?.model || task?.reasoning_effort || task?.compact_at);
const settingsContextName = tokens => !tokens ? '' : SETTINGS_COMPACT_AT.find(([size]) => size === tokens)?.[1]
  || (tokens >= 1e6 && tokens % 1e6 === 0 ? `${tokens / 1e6}M` : `${Math.round(tokens / 1000)}k`);
// The model a bot's tasks run on: its task model, else its own (or the team default it follows).
const settingsTaskRunsOn = (e, model = e.task_model?.model) => model || e.model || e.resolved_model || '';
// The efforts a model takes, as its Effort picker lists them (a Gateway pick by its tier's current version).
function settingsModelEfforts(id) {
  const t = settingsTierParse(id), row = (t && settingsModel(`${t.tier}-${SETTINGS_DATA.gateway.tiers[t.tier]?.current}`)) || settingsModel(id);
  return row?.efforts?.length ? [...row.efforts] : t ? ['low', 'high', 'max'] : [];
}
// What the bot's tasks run on, in a few words: "Claude Haiku 5.5 · high · 600k", "the bot's model · 600k".
function settingsTaskWords(task) {
  const context = settingsContextName(task?.compact_at);
  const model = task?.model ? settingsChoiceLabel(task.harness || task.runtime, task.model, task.reasoning_effort || task.effort)
    : ["the bot's model", settingsEffortName(task?.reasoning_effort)].filter(Boolean).join(' · ');
  return [model, context].filter(Boolean).join(' · ');
}
// The /task-model body for one change, carrying the other parts as they are: `choice` a task model ({harness, model,
// effort}, null for Same as bot), `effort` a task effort ('' for Same as bot), `compact_at` a task context length (null
// for Same as bot). The effort goes only where the model that runs tasks takes it; a new task model otherwise takes
// its own picker's. Null when all three are the same as the bot.
function settingsTaskBody(e, change = {}) {
  const was = e.task_model || {};
  const choice = 'choice' in change ? change.choice : was.model ? {harness: was.harness || was.runtime, model: was.model, effort: was.reasoning_effort} : null;
  let effort = 'effort' in change ? change.effort || '' : was.reasoning_effort || '';
  const compact_at = 'compact_at' in change ? change.compact_at || null : was.compact_at || null;
  if (effort && !settingsModelEfforts(settingsTaskRunsOn(e, choice?.model)).includes(effort)) effort = '';
  if (choice?.model && !effort) effort = choice.effort || '';
  if (!choice?.model && !effort && !compact_at) return null;
  return choice?.model ? {harness: choice.harness, model: choice.model, effort: effort || null, compact_at}
    : {model: null, effort: effort || null, compact_at};
}
// The Task effort choices for the model that runs tasks; "Same as bot" only while that is the bot's own model.
function settingsTaskEffortPaint(select, efforts, current, own) {
  const option = (value, label, selected, disabled = false) => `<option value="${esc(value)}" ${selected ? 'selected' : ''} ${disabled ? 'disabled' : ''}>${esc(label)}</option>`;
  select.innerHTML = settingsTaskEffortOptions(efforts, current, own, option);
  select.disabled = !select.hasAttribute('data-manage') || efforts.length <= (own ? 0 : 1);
}
function settingsTaskEffortOptions(efforts, current, own, option) {
  return (own ? option('', 'Same as bot', !current) : '')
    + (current && !efforts.includes(current) ? option(current, `${settingsEffortName(current)} (current)`, true, true) : '')
    + efforts.map(id => option(id, id === 'as-configured' ? 'Managed by harness' : settingsEffortName(id), id === current)).join('');
}
// Task effort and Task context length beside the Task model: labelled like its pickers in the bots table's task row
// (`row`), rows like Compact at in the bot editor.
function settingsTaskParts(e, row = false) {
  const task = e.task_model, own = !task?.model, manage = settingsCanManageBot(e), name = e.display_name || e.slug;
  const effort = task?.reasoning_effort || '', context = task?.compact_at || null;
  const efforts = settingsModelEfforts(settingsTaskRunsOn(e));
  const option = (value, label, selected, disabled = false) => `<option value="${esc(value)}" ${selected ? 'selected' : ''} ${disabled ? 'disabled' : ''}>${esc(label)}</option>`;
  const sizes = SETTINGS_COMPACT_AT.some(([size]) => size === context) || !context ? SETTINGS_COMPACT_AT : [...SETTINGS_COMPACT_AT, [context, settingsContextName(context)]];
  const cls = row ? '' : 'class="settings-inline-select"';
  const effortSelect = `<select ${cls} data-task-effort="${esc(e.id)}" data-current="${esc(effort)}" ${manage ? 'data-manage' : ''}
    aria-label="Task effort for ${esc(name)}" ${manage && efforts.length > (own ? 0 : 1) ? '' : 'disabled'}>${settingsTaskEffortOptions(efforts, effort, own, option)}</select>`;
  const contextSelect = `<select ${cls} data-task-context="${esc(e.id)}" data-current="${context || ''}" aria-label="Task context length for ${esc(name)}" ${manage ? '' : 'disabled'}>
    ${option('', 'Same as bot', !context)}${sizes.map(([size, label]) => option(size, label, size === context)).join('')}</select>`;
  return row ? `<label class="sb-task-part">Task effort${effortSelect}</label><label class="sb-task-part">Task context length${contextSelect}</label>`
    : `<div class="sb-row"><span>Task effort</span>${effortSelect}<span></span></div>
    <div class="sb-row"><span>Task context length</span>${contextSelect}<span></span></div>`;
}
// The bots table's task indicator, beside the model: faint while tasks run as the bot does, marked when any task
// setting differs ("Tasks · 600k" with a task context length). It opens the bot's task row under it
// (SETTINGS_BOTS_VIEW.taskOpen).
function settingsTaskIndicator(e) {
  const task = e.task_model, set = settingsTaskSet(task), open = SETTINGS_BOTS_VIEW.taskOpen.has(e.id);
  const label = set ? `Tasks run on ${settingsTaskWords(task)}` : 'Tasks run on the same model as the bot';
  const context = settingsContextName(task?.compact_at);
  if (!settingsCanManageBot(e) && !set) return '';
  return `<button type="button" class="sb-task-mark${set ? ' on' : ''}" data-task-open="${esc(e.id)}" aria-expanded="${open}"
    aria-label="${esc(`${label}. Task settings for ${e.display_name || e.slug}`)}" title="${esc(label)}" ${settingsCanManageBot(e) ? '' : 'disabled'}>Tasks${context ? ` · ${esc(context)}` : ''}</button>`;
}
// The row a task indicator opens under its bot: the task model's pickers and Apply, Task effort and Task context
// length (each saves on its own when changed), and Close. The bot's own model never changes here.
function settingsTaskRow(e, columns) {
  return `<tr class="sb-task-row" data-task-row="${esc(e.id)}"><td colspan="${columns}"><div class="sb-task-under">
    <span class="sb-task-head">Tasks · ${esc(e.display_name || e.slug)}</span>${settingsChoiceCombo(e, 'task')}${settingsTaskParts(e, true)}
    <button type="button" class="ghost" data-task-close="${esc(e.id)}">Close</button></div>
    ${settingsTaskWarning(e)}</td></tr>`;
}
// A Task effort or Task context length changed in the bots table's task row: saved at once, the rest kept.
async function settingsSaveTaskPart(select) {
  const effort = 'taskEffort' in select.dataset, slug = select.dataset.taskEffort || select.dataset.taskContext;
  const e = S.emps.find(row => row.id === slug);
  if (!e || select.value === (select.dataset.current || '')) return;
  select.disabled = true;
  try { await settingsSaveTaskModel(e, effort ? {effort: select.value} : {compact_at: Number(select.value) || null}, e.revision); }
  catch (error) { select.value = select.dataset.current || ''; toast(error.message, true); }
  finally {
    if (select.isConnected) select.disabled = false;
    else $(`[data-task-row] [${effort ? 'data-task-effort' : 'data-task-context'}="${CSS.escape(slug)}"]`)?.focus();
  }
}
// The plain-words warning for a cheaper task model on a high-stakes bot, as the server judges it against the bot's
// model now (`task_model_warning`, backend/task_models.py `warning`), under the task model in the row and the editor.
function settingsTaskWarning(e) {
  return e.task_model_warning ? `<p class="sb-task-warning" role="status" data-task-warning>${esc(e.task_model_warning)}</p>` : '';
}
function settingsChoicePopover(anchor) {
  let pop = $('#sb-choice-pop');
  if (pop?.matches(':popover-open')) { const same = pop.anchor === anchor; pop.hidePopover(); if (same) return; }
  const e = S.emps.find(row => row.id === anchor.dataset.choiceOpen);
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
  const name = SETTINGS_CHOICE_KINDS[kind].name;
  pop.setAttribute('aria-label', `${name} for ${e.display_name || e.slug}`);
  pop.innerHTML = `<div class="sb-choice-head">${name} · ${esc(e.display_name || e.slug)}</div>${settingsChoiceCombo(e, kind)}`;
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
        box.dispatchEvent(new CustomEvent('tico:choice', {bubbles: true}));   // the bot editor repaints Task effort
      });
      box.picker = picker;   // the bot editor's Save reads a pick that was never Applied
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
// The /fallback body for a picker value ('' is None); Apply and the bot editor's Save send the same.
function settingsFallbackBody(value) {
  const choice = value ? settingsChoiceFromValue(value) : null;
  return choice ? {harness: choice.harness, model: choice.model, effort: choice.effort} : null;
}
async function settingsPickChoice(box, value) {
  const slug = box.dataset.botChoice, e = S.emps.find(row => row.id === slug);
  const current = box.dataset.current || '';
  if (!e || value === current) return;
  const input = box.querySelector('input[type=search]');
  if (box.dataset.kind === 'task') {
    try { await settingsSaveTaskModel(e, {choice: value ? settingsChoiceFromValue(value) : null}, e.revision); }
    catch (error) { toast(error.message, true); }
    return;
  }
  if (box.dataset.kind === 'fallback') {
    try {
      const choice = value ? settingsChoiceFromValue(value) : null;
      await post(`/v2/bots/${encodeURIComponent(slug)}/fallback`, {fallback: settingsFallbackBody(value), expected_revision: e.revision});
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
// POST one change to the bot's task settings (settingsTaskBody: the other parts are carried as they are) and say what
// its tasks now run on. A pick its computer cannot run yet saves all the same, as does a cheaper one on a high-stakes
// bot: the toast and the row say so. The bot editor's Save passes `inEditor`: it reloads and toasts once at the end,
// and takes the revision this made.
async function settingsSaveTaskModel(e, change, revision, inEditor = false) {
  const body = settingsTaskBody(e, change);
  const saved = await post(`/v2/bots/${encodeURIComponent(e.id)}/task-model`, {task_model: body, expected_revision: revision});
  const task = saved.task_model !== undefined ? saved.task_model : body && {...body, reasoning_effort: body.effort};
  const cannot = saved.readiness?.can_run === false ? ` For now they run on its own model: ${saved.readiness.problem}.` : '';
  const said = `${settingsTaskSet(task) ? `${e.display_name}'s tasks run on ${settingsTaskWords(task)}.`
    : `${e.display_name}'s tasks run on the same model as ${e.display_name}.`}${cannot}${saved.warning ? ` ${saved.warning}` : ''}`;
  if (inEditor) { if (cannot || saved.warning) toast(said); return saved.revision; }
  await loadSettings();
  toast(said);
  return saved.revision;
}
function settingsMachineSelect(e) {
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
function settingsBotProblem(e) {
  if (e.status !== 'active') return '';
  if (e.agent?.synced) return e.online ? '' : 'history not synced';
  if (e.agent) return !e.agent.credential ? 'no credential' : e.online ? '' : 'not reporting';
  if (cloudMachineOn(e)) return e.cloud_machine.problem || '';
  return !e.machine ? 'no computer' : e.online && e.ready ? '' : e.online ? (e.readiness?.problems?.[0] || 'Computer not ready') : 'offline';
}
// Search lasts while the page is open; selection is trimmed to matching manageable bots.
// Remove the retired filters so an older browser preference can never hide search results.
try { localStorage.removeItem('hub.settings.bots.filters'); } catch {}
const SETTINGS_BOTS_VIEW = {q: '', selected: new Set(), picking: false, taskOpen: new Set()};
const settingsBotHarness = e => e.harness || e.runtime || '';
const settingsBotEffort = e => e.reasoning_effort || e.effort || '';
const settingsBotSelectable = e => !e.agent && settingsCanManageBot(e);
const settingsBotSearchModels = e => {
  const bot = e.agent ? {model: e.agent.model} : e.model ? e : {model: e.resolved_model, harness: e.resolved_runtime};
  return {bot, task: e.agent ? null : e.task_model?.model ? e.task_model : bot};
};
const settingsSearchModelWords = model => model ? [model.model, settingsModelName(model.model)] : [];
// Every word can occur in the name, slug, role, description, team, bot model or effective task model.
const settingsBotMatches = (e, q) => {
  const {bot, task} = settingsBotSearchModels(e);
  return !q.trim() || searchIncludes([e.display_name, botDisplayName(e.id), e.slug, e.role,
    e.description, e.team && teamLabel(e.team), ...settingsSearchModelWords(bot), ...settingsSearchModelWords(task)], q);
};
function settingsBotSearchMatch(e) {
  const words = SETTINGS_BOTS_VIEW.q.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return '';
  const {bot, task} = settingsBotSearchModels(e);
  const matches = model => model?.model && words.some(word => searchIncludes(settingsSearchModelWords(model), word));
  const labels = [[bot, 'Bot model'], [task, 'Task model']].filter(([model]) => matches(model));
  return labels.length ? `<small class="sb-model-match" data-model-match>${labels.map(([model, label]) => `${label}: ${esc(settingsModelName(model.model))}`).join(' · ')}</small>` : '';
}
function settingsBotsVisible(rows) {
  return rows.filter(e => settingsBotMatches(e, SETTINGS_BOTS_VIEW.q));
}
function settingsBotsSearchHTML(shown, total) {
  const view = SETTINGS_BOTS_VIEW, picking = view.picking || view.selected.size > 0;
  return `<div class="settings-bots-filters">
    <span class="sb-search"><input class="settings-filter-search" type="search" autocomplete="off" spellcheck="false" enterkeyhint="search" data-bots-search
      value="${esc(view.q)}" placeholder="Search bots or models" aria-label="Search bots" aria-controls="settings-bots-list">
      <button type="button" class="sb-search-clear" data-bots-search-clear aria-label="Clear search" ${view.q ? '' : 'hidden'}>×</button></span>
    <span class="settings-bots-count" data-bots-count role="status">${shown === total ? '' : `${shown} of `}${total} bot${total === 1 ? '' : 's'}</span>
    <span class="sb-pick-tools"><button type="button" class="linkish" data-bots-pick-all ${picking ? '' : 'hidden'}>Select all</button>
      <button type="button" class="linkish" data-bots-picking aria-pressed="${picking}">${picking ? 'Done' : 'Select'}</button></span></div>
    <div class="settings-bulk" data-bots-bulk hidden><strong data-bulk-count></strong>
      <button class="primary" type="button" data-bulk-model>Change model…</button><button class="ghost" type="button" data-bulk-clear>Clear</button></div>`;
}
function settingsBotsSyncSelection(el, visible) {
  const selectable = visible.filter(settingsBotSelectable).map(e => e.id);
  const picked = selectable.filter(slug => SETTINGS_BOTS_VIEW.selected.has(slug)).length;
  const all = el.querySelector('[data-bots-select-all]');
  if (all) { all.checked = !!selectable.length && picked === selectable.length; all.indeterminate = picked > 0 && picked < selectable.length; all.disabled = !selectable.length; }
  const bar = el.querySelector('[data-bots-bulk]');
  if (bar) { bar.hidden = !SETTINGS_BOTS_VIEW.selected.size; bar.querySelector('[data-bulk-count]').textContent = `${SETTINGS_BOTS_VIEW.selected.size} selected`; }
}
function settingsBulkModelDialog() {
  const bots = [...SETTINGS_BOTS_VIEW.selected].map(slug => S.emps.find(e => e.id === slug)).filter(Boolean);
  if (!bots.length) return;
  const dialog = document.createElement('dialog'); dialog.className = 'tmodal settings-bulk-dialog';
  dialog.setAttribute('aria-labelledby', 'bulk-model-title');
  let choice = null, target = 'bot', running = false, ran = false;
  const paint = () => {
    const label = choice ? settingsChoiceLabel(choice.harness, choice.model, choice.effort) : '';
    dialog.innerHTML = `<div class="tmodal-head"><h2 id="bulk-model-title">Change model · ${bots.length} bot${bots.length === 1 ? '' : 's'}</h2><span class="spacer"></span><button class="ghost" type="button" data-bulk-close aria-label="Close">✕</button></div>
      <div class="transition-body">
        <label>Apply to<select data-bulk-target aria-label="Apply to"><option value="bot">Bot model</option><option value="task">Task model</option><option value="both">Both models</option></select></label>
        <div class="settings-choice" data-bot-choice="" data-kind="model" data-current="${esc(choice ? settingsChoiceValue(choice.harness, choice.model, choice.effort) : '')}">
          ${settingsChoiceFields(choice ? settingsChoiceValue(choice.harness, choice.model, choice.effort) : '')}</div>
        <p data-bulk-destination>${choice ? `Destination: ${esc(label)}` : ''}</p>
        <div class="transition-progress" data-bulk-explanation></div>
        <ul class="settings-bulk-list" data-bulk-list>${bots.map(e => `<li data-bulk-bot="${esc(e.id)}" data-state="pending"><span>${esc(e.display_name || e.slug)}</span><span class="settings-cell-note">${esc(settingsChoiceLabel(settingsBotHarness(e), e.model, settingsBotEffort(e)))}</span></li>`).join('')}</ul>
        <p role="status" data-bulk-summary></p>
        <div class="transition-actions"><button class="primary" type="button" data-bulk-apply ${choice ? '' : 'disabled'}>Change ${bots.length} bot${bots.length === 1 ? '' : 's'}</button><button class="ghost" type="button" data-bulk-close>Cancel</button></div></div>`;
    dialog.querySelectorAll('[data-bulk-close]').forEach(button => button.onclick = () => dialog.close());
    const preview = () => {
      dialog.querySelector('[data-bulk-explanation]').innerHTML = target === 'task'
        ? '<strong>Only task models change.</strong><p>New task sessions use this choice. If unavailable, tasks use the bot model and say so.</p>'
        : `<strong>${target === 'both' ? 'Bot and task models change independently.' : 'Only bot models change; explicit task models stay as set.'}</strong><p>Each changed bot starts a fresh provider session. History is kept. A bot mid-turn is skipped; retry it after.${target === 'both' ? ' Task settings still save if the bot model is blocked or fails.' : ''}</p>`;
      bots.forEach(e => { const why = target !== 'task' && choice && settingsRuntimeBlock(e, choice.harness);
        mark(e.id, why ? 'blocked' : 'pending', why ? `Can't run bot model: ${why.reason}${target === 'both' ? ' · Task model will still be saved' : ''}` : 'Ready to apply'); });
    };
    dialog.querySelector('[data-bulk-target]').onchange = event => { target = event.target.value; preview(); };
    settingsWireChoiceFields(dialog.querySelector('[data-choice-fields]'), (value, valid) => {
      choice = valid ? settingsChoiceFromValue(value) : null;
      dialog.querySelector('[data-bulk-apply]').disabled = !choice;
      dialog.querySelector('[data-bulk-destination]').textContent = choice
        ? `Destination: ${settingsChoiceLabel(choice.harness, choice.model, choice.effort)}` : '';
      preview();
    });
    preview();
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
    if (apply) apply.disabled = true;
    const retry = dialog.querySelector('[data-bulk-retry]');
    if (retry) retry.disabled = true;
    dialog.querySelectorAll('[data-bot-choice] select,[data-choice-apply],[data-bulk-target]').forEach(control => control.disabled = true);
    const results = {changed: 0, partial: 0, skipped: 0, blocked: 0, failed: []};
    for (const e of targets) {
      mark(e.id, 'working', 'Changing…');
      const result = await settingsBulkApplyOne(e.id, choice, target);
      mark(e.id, result.state, result.note);
      if (result.partial) results.partial++;
      if (result.state === 'failed') results.failed.push(e); else results[result.state]++;
    }
    running = false;
    const summary = [results.changed || !results.partial ? `${results.changed} changed` : '',
      results.partial ? `${results.partial} partly changed` : '', results.skipped ? `${results.skipped} already on it` : '',
      results.blocked ? `${results.blocked} can't run it` : '',
      results.failed.length ? `${results.failed.length} failed` : ''].filter(Boolean).join(' · ');
    dialog.querySelector('[data-bulk-summary]').textContent = summary;
    const actions = dialog.querySelector('.transition-actions');
    actions.innerHTML = `${results.failed.length ? '<button class="ghost" type="button" data-bulk-retry>Retry failed</button>' : ''}<button class="primary" type="button" data-bulk-close>Done</button>`;
    actions.querySelector('[data-bulk-close]').onclick = () => dialog.close();
    const retryFailed = actions.querySelector('[data-bulk-retry]');
    if (retryFailed) retryFailed.onclick = () => void run(results.failed);
    await loadSettings();
  };
  // Cancel keeps the selection; once a change has run, the next bulk action starts fresh.
  dialog.onclose = () => { dialog.remove(); if (ran) { SETTINGS_BOTS_VIEW.selected.clear(); void loadSettings(); } };
  document.body.appendChild(dialog); paint(); dialog.showModal();
}
// Each destination uses its existing single-bot route. In Both mode, reload after the bot transition
// so the task-model request uses its new revision. Retry failed preserves already-applied destinations.
async function settingsBulkApplyOne(slug, choice, target = 'bot') {
  const notes = [], kinds = target === 'both' ? ['bot', 'task'] : [target];
  let changed = false, failed = false, blocked = false;
  for (const kind of kinds) {
    const result = await settingsBulkApplyKind(slug, choice, kind);
    notes.push(`${kind === 'task' ? 'Task model' : 'Bot model'}: ${result.note}`);
    failed ||= result.state === 'failed';
    blocked ||= result.state === 'blocked';
    changed ||= result.state === 'changed';
    if (kind === 'bot' && target === 'both' && result.state === 'changed') {
      try { S.emps = namedRoster(await get('/employees')); }
      // The task route can refresh a stale revision itself if this read fails.
      catch {}
    }
  }
  return {state: failed ? 'failed' : blocked ? 'blocked' : changed ? 'changed' : 'skipped',
    partial: changed && (failed || blocked), note: notes.join(' · ')};
}
async function settingsBulkApplyKind(slug, choice, kind) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const e = S.emps.find(row => row.id === slug);
    if (!e || !settingsBotSelectable(e)) return {state: 'failed', note: 'This bot can no longer be changed here'};
    // Task settings save even when unavailable; the server returns its fallback note.
    const why = kind === 'bot' && settingsRuntimeBlock(e, choice.harness);
    if (why) return {state: 'blocked', note: `Can't run: ${why.reason}${why.move ? ` · move to ${why.move.label}` : ''}`};
    const selected = kind === 'task' ? e.task_model : e;
    if (selected && settingsBotHarness(selected) === choice.harness && selected.model === choice.model && settingsBotEffort(selected) === choice.effort)
      return {state: 'skipped', note: 'Already on this model'};
    try {
      if (kind === 'task') {
        // The task model and its effort change; the bot's own task context length stays.
        const saved = await post(`/v2/bots/${encodeURIComponent(slug)}/task-model`, {
          task_model: settingsTaskBody(e, {choice, effort: choice.effort}), expected_revision: e.revision});
        return {state: 'changed', note: [`Now ${settingsChoiceLabel(choice.harness, choice.model, choice.effort)}`,
          saved.readiness?.can_run === false ? `For now tasks use the bot model: ${saved.readiness.problem}` : '', saved.warning || ''].filter(Boolean).join('. ')};
      }
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
const settingsMayRestore = b => settingsIsAdmin() || b.owner_id === S.me?.id || (b.bot_owners || []).some(o => o.id === S.me?.id);
async function settingsArchivedBots(el) {
  if (!SETTINGS_ARCHIVED || Date.now() - SETTINGS_ARCHIVED.at > 30000) {
    try { SETTINGS_ARCHIVED = {at: Date.now(), bots: (await get('/v2/bots?include_archived=1')).filter(b => b.state === 'archived' && settingsMayRestore(b))}; }
    catch { return; }
  }
  if (!el.isConnected || !SETTINGS_ARCHIVED.bots.length) return;
  el.querySelector('[data-archived-bots]')?.remove();
  const box = document.createElement('div'); box.dataset.archivedBots = '';
  box.innerHTML = `<h3>Archived</h3>${SETTINGS_ARCHIVED.bots.map(b => `<div class="row" data-archived-bot="${esc(b.id ?? b.slug)}"><strong>${esc(b.display_name || b.slug)}</strong>
    <span class="spacer"></span><button class="ghost" type="button" data-bot-restore="${esc(b.id ?? b.slug)}" data-label="${esc(b.display_name || b.slug)}">Restore</button></div>`).join('')}`;
  el.appendChild(box);
}
async function settingsRestoreBot(slug, button, label = slug) {
  button.disabled = true;
  try {
    const done = await post(`/v2/bots/${encodeURIComponent(slug)}/restore`, {});
    SETTINGS_ARCHIVED = null;
    toast(`Restored ${label}${done.agent && !done.agent.credential ? '; pair or create its credential' : ''}`);
    await loadSettings();
  } catch (error) { toast(error.message, true); button.disabled = false; }
}
function renderSettingsBots() {
  const el = $('#set-bots'); if (!el) return;
  const all = S.emps.slice().sort((a,b) => (a.team || '').localeCompare(b.team || '') || byBotOrder(a, b) || settingsBotName(a.id).localeCompare(settingsBotName(b.id)));
  const rows = settingsBotsVisible(all);
  const shownSelectable = new Set(rows.filter(settingsBotSelectable).map(e => e.id));
  [...SETTINGS_BOTS_VIEW.selected].forEach(slug => { if (!shownSelectable.has(slug)) SETTINGS_BOTS_VIEW.selected.delete(slug); });
  const pick = e => settingsBotSelectable(e)
    ? `<input type="checkbox" data-bot-pick="${esc(e.id)}" aria-label="Select ${esc(e.display_name)}" ${SETTINGS_BOTS_VIEW.selected.has(e.id) ? 'checked' : ''}>`
    : `<input type="checkbox" disabled aria-label="${esc(e.display_name)} cannot be changed here" title="${e.agent ? 'An external agent uses its own model' : 'You do not own this bot'}">`;
  const stack = e => {
    const owners = (e.bot_owners || []).length ? e.bot_owners : (e.users || []);
    const names = owners.map(o => o.name || o.id);
    return `<span class="sb-stack" title="${esc(names.join(', ') || 'No owner')}">${owners.slice(0, 3).map(o => personCircle(o.name || o.id, 22)).join('')}${owners.length > 3 ? `<span class="more">+${owners.length - 3}</span>` : ''}${owners.length ? '' : '<span class="muted">None</span>'}</span>`;
  };
  const row = e => {
    const problem = settingsBotProblem(e);
    const badges = `${isBuiltInBot(e.id) ? '<span class="pill" data-built-in>Built-in</span>' : ''}${botPausedBadge(e.id, true) || (e.status && e.status !== 'active' ? `<span class="pill">${esc(statusWord(e.status))}</span>` : '')}`;
    const model = e.agent ? `<span class="muted" title="${esc(e.agent.model ? `profile's model · ${e.agent.model}` : "the profile's own model")}">${esc(agentKind(e.agent))}</span>` : settingsChoiceCell(e, 'model');
    return `<tr data-settings-bot="${esc(e.id)}"><td class="settings-pick">${pick(e)}</td>
      <td class="sb-cell-name"><div class="sb-bot">${avatar(e.id, 27, stateOf(e.id))}<div class="sb-text"><div class="sb-line"><a class="sb-name" href="#/bot/${encodeURIComponent(e.id)}">${shownName(e)}</a>${botDisplayName(e.id) !== (e.display_name || e.slug) ? `<span class="mono muted">${esc(e.slug)}</span>` : ''}${badges}</div>${e.task_flow ? `<small data-task-flow>${e.task_flow.going} of ${e.keep_tasks_going == null ? 'unlimited' : e.keep_tasks_going} tasks going, ${e.task_flow.waiting} waiting to start</small>` : ''}${e.team || problem ? `<small>${e.team ? esc(teamLabel(e.team)) : ''}${e.team && problem ? ' · ' : ''}${problem ? `<span class="sb-problem">${esc(problem)}</span>` : ''}</small>` : ''}</div></div></td>
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
    const fresh = next.content.querySelector('.settings-bots-filters');
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
  const redraw = selector => {
    renderSettingsBots();
    const target = [...el.querySelectorAll(selector)].find(node => node.getClientRects().length);
    (target || el.querySelector('[data-bots-search]'))?.focus();
  };
  el.onclick = event => {
    const restore = event.target.closest('[data-bot-restore]');
    if (restore) { void settingsRestoreBot(restore.dataset.botRestore, restore, restore.dataset.label); return; }
    const choice = event.target.closest('[data-choice-open]');
    if (choice) { settingsChoicePopover(choice); return; }
    const task = event.target.closest('[data-task-open],[data-task-close]');
    if (task) {
      const slug = task.dataset.taskOpen || task.dataset.taskClose, open = SETTINGS_BOTS_VIEW.taskOpen;
      open.has(slug) ? open.delete(slug) : open.add(slug);
      const back = `[data-task-open="${CSS.escape(slug)}"]`;
      redraw(open.has(slug) ? `[data-task-row="${CSS.escape(slug)}"] select:not(:disabled)` : back); return;
    }
    if (event.target.closest('[data-bulk-model]')) { settingsBulkModelDialog(); return; }
    if (event.target.closest('[data-bulk-clear]')) { SETTINGS_BOTS_VIEW.selected.clear(); redraw('[data-bots-picking]'); return; }
    if (event.target.closest('[data-bots-search-clear]')) {
      SETTINGS_BOTS_VIEW.q = ''; renderSettingsBots(); el.querySelector('[data-bots-search]')?.focus(); return;
    }
    // On a phone the checkbox cell is the tap target, not just the 16px box.
    const pickCell = event.target.closest('td.settings-pick');
    if (pickCell && event.target === pickCell) { pickCell.querySelector('input:not(:disabled)')?.click(); return; }
    // Done also drops the selection: nothing stays picked behind checkboxes that are no longer shown.
    if (event.target.closest('[data-bots-picking]')) {
      SETTINGS_BOTS_VIEW.picking = !(SETTINGS_BOTS_VIEW.picking || SETTINGS_BOTS_VIEW.selected.size);
      if (!SETTINGS_BOTS_VIEW.picking) SETTINGS_BOTS_VIEW.selected.clear();
      redraw('[data-bots-picking]'); return;
    }
    if (event.target.closest('[data-bots-pick-all]')) {
      const pickable = rows.filter(settingsBotSelectable), on = !pickable.every(e => SETTINGS_BOTS_VIEW.selected.has(e.id));
      pickable.forEach(e => on ? SETTINGS_BOTS_VIEW.selected.add(e.id) : SETTINGS_BOTS_VIEW.selected.delete(e.id));
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
    const one = event.target.closest('[data-bot-pick]');
    if (one) { one.checked ? SETTINGS_BOTS_VIEW.selected.add(one.dataset.botPick) : SETTINGS_BOTS_VIEW.selected.delete(one.dataset.botPick); settingsBotsSyncSelection(el, rows); return; }
    if (event.target.closest('[data-bots-select-all]')) {
      const on = event.target.checked;
      rows.filter(settingsBotSelectable).forEach(e => on ? SETTINGS_BOTS_VIEW.selected.add(e.id) : SETTINGS_BOTS_VIEW.selected.delete(e.id));
      el.querySelectorAll('[data-bot-pick]').forEach(box => { box.checked = on; });
      settingsBotsSyncSelection(el, rows); return;
    }
    const machine = event.target.closest('[data-bot-machine]');
    if (machine) void settingsMoveBot(machine);
    const part = event.target.closest('[data-task-row] [data-task-effort],[data-task-row] [data-task-context]');
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
  dialog.querySelectorAll('[data-catalog-close]').forEach(button => button.onclick = () => dialog.close());
  dialog.showModal();
  let cards;
  try { cards = catalogCards(await get('/v2/templates')); }
  catch (error) {
    const body = $('#catalog-picker-body'); if (body) body.innerHTML = `<div class="err">${esc(error.message)}</div>`;
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
    if (!settingsCanCreateBots()) { status.innerHTML = '<span class="err">You no longer have permission to add bots.</span>'; return; }
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
      dialog.close(); await refresh(true);
      if (S.route === SETTINGS) { await loadSettings(); settingsShow('bots'); }
      toast(hints.length ? hints.join('; ') : chosen.length === 1 ? `Added ${catalogName(state, chosen[0])}` : `Added ${chosen.length} bots`);
    } catch (error) {
      status.innerHTML = `<span class="err">${esc(error.message)}</span>`;
      if (String(error.message).includes('Team default')) {
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
