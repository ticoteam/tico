/* ui/app/settings-bots-choice.ts — Settings > Bots: a bot's model, fallback and task model pickers (harness, model, effort, Gateway tier), their popover and Apply
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// What settingsWireChoiceFields hands back: the pick as a value, whether it can be applied, and whether it differs.
interface SettingsChoicePicker { value: () => string; valid: () => boolean; changed: () => boolean; }
// A [data-bot-choice] box keeps its wired picker, so the bot editor's Save can read a pick that was never Applied.
interface SettingsChoiceBox extends HTMLElement { picker?: SettingsChoicePicker; }

// Keep the three choices local until Apply, so selecting a harness cannot start a transition. `none` allows no
// choice at all, named by it when it is a string ("Same as bot" for a task model), else "None". `noEffort` hides the
// Effort picker where another one stands beside it (a task model's is Task effort, settingsTaskParts). `compact` adds
// a Compact select with "Same as bot" first (the Fallback's window, spec scope 5): its size rides in the value as a
// fourth part, "harness::model::effort::300000", so Apply and the bot editor's Save see a window change like any other.
function settingsChoiceFields(current = '', disabled = false, none: boolean | string = false, label = 'Model settings', noEffort = false, compact = false) {
  return `<div class="settings-model-choice" role="group" aria-label="${esc(label)}" data-choice-fields data-current="${esc(current)}" ${disabled ? 'data-readonly' : ''} ${none ? `data-allow-none="${esc(none === true ? 'None' : none)}"` : ''}>
    <label>Harness / provider<select data-choice-harness aria-label="Harness / provider" ${disabled ? 'disabled' : ''}></select></label>
    <label data-choice-model-label>Model<select data-choice-model aria-label="Model" ${disabled ? 'disabled' : ''}></select></label>
    <div class="settings-tier" data-choice-tier hidden></div>
    <label ${noEffort ? 'hidden' : ''}>Effort<select data-choice-effort aria-label="Effort" ${disabled ? 'disabled' : ''}></select></label>
    ${compact ? `<label data-choice-compact-label>Compact<select data-choice-compact aria-label="Compact" ${disabled ? 'disabled' : ''}></select></label>` : ''}
    <div class="settings-choice-blocks" data-choice-blocks></div>
  </div>`;
}
function settingsWireChoiceFields(fields: HTMLElement, onChange: (value: string, valid: boolean) => void = () => {}): SettingsChoicePicker {
  const harness = fields.querySelector<HTMLSelectElement>('[data-choice-harness]')!;
  const model = fields.querySelector<HTMLSelectElement>('[data-choice-model]')!;
  const effort = fields.querySelector<HTMLSelectElement>('[data-choice-effort]')!;
  const tierBox = fields.querySelector<HTMLElement>('[data-choice-tier]')!;
  const modelLabel = fields.querySelector<HTMLElement>('[data-choice-model-label]')!;
  const compactPick = fields.querySelector<HTMLSelectElement>('[data-choice-compact]');
  const compactLabel = fields.querySelector<HTMLElement>('[data-choice-compact-label]');
  const compactOf = (v: string | undefined) => Number(String(v || '').split('::')[3]) || null;
  let compact = compactOf(fields.dataset.current);
  const all = settingsAllChoices(), readonly = fields.hasAttribute('data-readonly');
  // The Gateway's picks are made in the tier controls; its rows stay out of the Model list (and one virtual
  // harness, "Tico AI Gateway", stands for them). `real` maps it back to the pi harness the bot runs them on.
  const gw = settingsGateway(), viaTier = !!gw && all.some(row => settingsIsTico(row.model));
  const choices = viaTier ? all.filter(row => !settingsIsTico(row.model)) : all;
  // A bot's own Model control greys out what its computer cannot run; bulk change checks each bot on Apply.
  const box = fields.closest<HTMLElement>('[data-bot-choice]');
  const bot = box?.dataset.botChoice ? S.emps.find(row => row.id === box.dataset.botChoice) : null;
  const guard = box?.dataset.kind === 'model' ? bot : null;
  const block = (id: string | undefined) => guard ? settingsRuntimeBlock(guard, id) : null;
  const option = (value: string | undefined, label: string | undefined, selected: boolean, disabled = false) => `<option value="${esc(value)}" ${selected ? 'selected' : ''} ${disabled ? 'disabled' : ''}>${esc(label)}</option>`;
  let picked = settingsChoiceFromValue(fields.dataset.current);
  if (viaTier && settingsIsTico(picked.model)) picked.harness = 'tico';
  let legacy = viaTier && picked.harness === 'tico' && !settingsTierParse(picked.model);   // an id the catalog lacks, until a control changes it
  const inTier = () => viaTier && picked.harness === 'tico';
  const real = () => inTier() ? {...picked, harness: 'pi'} : picked;
  const start = settingsTierKey('medium', false);
  // The tier the controls show: the model's own, or (an id the catalog lacks, nothing chosen yet) Medium.
  const shown = (): SettingsTierPick => settingsTierParse(picked.model) || {tier: start, level: 'medium', fast: false, version: gw?.tiers[start]?.current || '1.0'};
  const tierEfforts = (t: SettingsTierPick) => { const row = settingsModel(`${t.tier}-${gw?.tiers[t.tier]?.current}`) || settingsModel(picked.model);
    return {list: row?.efforts?.length ? [...row.efforts] : ['low', 'high', 'max'], def: row?.default_effort || 'high'}; };
  const setTier = (t: SettingsTierPick) => {
    const key = settingsTierKey(t.level, t.fast), to = gw?.tiers[key];
    if (!to) return;
    legacy = false;
    // Auto stays Auto; a pinned version stays only where the destination tier has it, else that tier's current one.
    const has = (version: string) => (to.versions || []).some(v => v.version === version);
    const next = {...t, tier: key, version: t.version === 'auto' || has(t.version) ? t.version : to.current};
    const efforts = tierEfforts(next);
    picked = {harness: 'tico', model: settingsTierId(next), effort: efforts.list.includes(picked.effort) ? picked.effort : efforts.def};
  };
  const paintTier = () => {
    if (!gw) return;
    const t = shown(), tier = gw.tiers[t.tier];
    const now = tier?.current;
    const versions = [...(tier?.versions || [])];   // newest first
    tierBox.innerHTML = `<div class="settings-tier-row">
        <label>Level<select data-tier-level aria-label="Level" ${readonly ? 'disabled' : ''}>
          ${legacy ? option('', 'Choose', true, true) : ''}
          ${gw.levels.map(l => option(l.id, l.name, !legacy && l.id === t.level)).join('')}</select></label>
        <label class="settings-tier-fast"><input type="checkbox" data-tier-fast ${t.fast ? 'checked' : ''} ${readonly ? 'disabled' : ''}>Fast</label>
        <label>Version<select data-tier-version aria-label="Version" ${readonly ? 'disabled' : ''}>
          ${legacy ? option('', `${settingsModelName(picked.model)}`, true, true) : ''}
          ${versions.map(v => option(v.version, v.version + (v.version === now ? ' (current)' : ''), !legacy && t.version === v.version)).join('')}
          ${option('auto', 'Auto (always latest)', !legacy && t.version === 'auto')}</select></label></div>
      <p class="settings-tier-line" data-tier-line>${esc(settingsTierLine(picked.model))}</p>`;
    const levelPick = tierBox.querySelector<HTMLSelectElement>('[data-tier-level]')!, fastPick = tierBox.querySelector<HTMLInputElement>('[data-tier-fast]')!;
    const versionPick = tierBox.querySelector<HTMLSelectElement>('[data-tier-version]')!;
    levelPick.onchange = () => { setTier({...t, level: levelPick.value}); paint(); onChange(value(), valid()); };
    fastPick.onchange = () => { setTier({...t, fast: fastPick.checked}); paint(); onChange(value(), valid()); };
    versionPick.onchange = () => { setTier({...t, version: versionPick.value}); paint(); onChange(value(), valid()); };
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
    // and every bot on that computer would repeat it.
    const notes = fields.querySelector('[data-choice-blocks]');
    if (notes) notes.innerHTML = guard && !readonly && fields.closest('#bot-editor') ? settingsBlockedNote(guard, harnesses) : '';
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
    if (compactPick && compactLabel) {
      // Only for a harness that compacts, and only the sizes the chosen model holds (spec scope 6 and 8).
      compactLabel.hidden = !picked.model || !compactsOn(real().harness);
      compactPick.innerHTML = compactOptions(compact, settingsModel(picked.model)?.context_tokens);
      compactPick.disabled = readonly;
    }
  };
  const base = () => picked.model ? settingsChoiceValue(real().harness, picked.model, picked.effort) : '';
  const value = () => { const v = base(); return v && compact && compactPick && compactsOn(real().harness) ? `${v}::${compact}` : v; };
  const tierValid = () => { const t = settingsTierParse(picked.model);
    return !!t && (t.version === 'auto' || !!gw?.tiers[t.tier]?.versions.some(v => v.version === t.version)) && tierEfforts(t).list.includes(picked.effort) && !block('tico'); };
  const valid = () => (inTier() ? tierValid() : (choices.some(row => row.value === base()) && !block(picked.harness)))
    || (!picked.harness && fields.hasAttribute('data-allow-none'));
  // Whether the three fields show something other than what the bot has, finished or not.
  const changed = () => { const was = settingsChoiceFromValue(fields.dataset.current), now = real();
    return now.harness !== was.harness || now.model !== was.model || now.effort !== was.effort
      || compactOf(value()) !== compactOf(fields.dataset.current); };
  harness.onchange = () => {
    picked = {harness: harness.value, model: '', effort: ''};
    if (picked.harness === 'tico') {   // Medium, pinned to the current version
      legacy = false;
      setTier({tier: start, level: 'medium', fast: false, version: gw?.tiers[start]?.current || '1.0'});
    }
    paint(); onChange(value(), valid());
  };
  model.onchange = () => {
    const row = settingsModel(model.value);
    const efforts = choices.filter(choice => choice.harness === picked.harness && choice.model === model.value).map(choice => choice.effort);
    const own = row?.default_effort;
    picked = {harness: picked.harness, model: model.value,
      effort: efforts.includes(picked.effort) ? picked.effort : (own !== undefined && efforts.includes(own) ? own : efforts[0] || '')};
    // The window picked stays, lowered to the largest size the new model holds (spec scope 8).
    const window = row?.context_tokens || 0;
    if (compact && window && compact > window) compact = COMPACT_SIZES.filter(([size]) => size <= window).pop()?.[0] || null;
    paint(); onChange(value(), valid());
  };
  effort.onchange = () => { picked.effort = effort.value; onChange(value(), valid()); };
  if (compactPick) compactPick.onchange = () => { compact = Number(compactPick.value) || null; onChange(value(), valid()); };
  paint();
  return {value, valid, changed};
}
function settingsChoiceCombo(e: SettingsBot, kind: SettingsChoiceKind) {
  const spec = SETTINGS_CHOICE_KINDS[kind], selected = spec.of(e);
  const own = kind === 'fallback' && selected?.compact_at ? `::${selected.compact_at}` : '';
  const current = selected?.model ? settingsChoiceValue(selected.harness || selected.runtime, selected.model,
    selected.reasoning_effort || selected.effort) + own : '';
  const manage = settingsCanManageBot(e);
  return `<div class="settings-choice" data-bot-choice="${esc(e.id)}" data-kind="${kind}" data-current="${esc(current)}">
    ${!current && !spec.none ? `<small class="muted">${esc(settingsDefaultLabel(e))}</small>` : ''}
    ${settingsChoiceFields(current, !manage, spec.none || false, `${spec.name} for ${e.display_name || e.slug}`, kind === 'task', kind === 'fallback')}
    ${manage ? '<button type="button" class="ghost" data-choice-apply disabled>Apply</button>' : ''}
  </div>`;
}
// In the bots table a model or fallback is one short line; its three pickers open in a popover under it.
function settingsChoiceCell(e: SettingsBot, kind: SettingsChoiceKind) {
  const fallback = kind === 'fallback', selected = fallback ? e.fallback : e;
  const own = fallback ? e.fallback?.compact_at : null, window = own ? ` · ${compactWords(own)}` : '';
  const label = selected?.model ? settingsChoiceLabel(selected.harness || selected.runtime, selected.model, selected.reasoning_effort || selected.effort) + window
    : fallback ? 'None' : settingsDefaultLabel(e);
  const text = `<span class="sb-pick-text${selected?.model ? '' : ' muted'}">${esc(label)}</span>`;
  if (!settingsCanManageBot(e)) return `<span class="sb-pick ro" title="${esc(label)}">${text}</span>`;
  return `<button type="button" class="sb-pick" data-choice-open="${esc(e.id)}" data-kind="${kind}" aria-haspopup="dialog" aria-expanded="false"
    aria-label="${fallback ? 'Fallback' : 'Model'} for ${esc(e.display_name || e.slug)}: ${esc(label)}" title="${esc(label)}">${text}<span class="sb-pick-caret" aria-hidden="true"></span></button>`;
}
function settingsChoicePopover(anchor: HTMLElement) {
  let pop = $('#sb-choice-pop');
  if (pop?.matches(':popover-open')) { const same = pop.anchor === anchor; pop.hidePopover(); if (same) return; }
  const e = S.emps.find(row => row.id === anchor.dataset.choiceOpen);
  if (!e) return;
  if (!pop) {
    pop = document.createElement('div');
    pop.id = 'sb-choice-pop'; pop.className = 'tl-pop sb-choice-pop'; pop.popover = 'auto';
    pop.setAttribute('role', 'dialog');
    pop.addEventListener('toggle', (ev: ToggleEvent) => {
      if (ev.newState !== 'closed') return;
      pop.anchor?.setAttribute('aria-expanded', 'false');
      if (pop.anchor?.isConnected && (pop.contains(document.activeElement) || document.activeElement === document.body)) pop.anchor.focus();
    });
    document.body.append(pop);
  }
  pop.anchor = anchor;
  const kind = anchor.dataset.kind as SettingsChoiceKind;   // settingsChoiceCell draws it
  const name = SETTINGS_CHOICE_KINDS[kind].name;
  pop.setAttribute('aria-label', `${name} for ${e.display_name || e.slug}`);
  pop.innerHTML = `<div class="sb-choice-head">${name} · ${esc(e.display_name || e.slug)}</div>${settingsChoiceCombo(e, kind)}`;
  const box = pop.querySelector('[data-bot-choice]');
  box.querySelector('[data-choice-apply]')?.classList.replace('ghost', 'primary');
  box.insertAdjacentHTML('beforeend', '<button type="button" class="ghost" data-choice-cancel>Cancel</button>');
  box.querySelector('[data-choice-cancel]').onclick = () => pop.hidePopover();
  // Apply closes the popover first: a model change continues in its own dialog.
  settingsWireCombos(pop, async (target: SettingsChoiceBox, value: string) => { pop.hidePopover(); await settingsPickChoice(target, value); });
  pop.showPopover();
  anchor.setAttribute('aria-expanded', 'true');
  tasksMenuPlace(pop, anchor);
  pop.querySelector('select:not(:disabled)')?.focus();
}
function settingsWireCombos(root: ParentNode, onPick: (box: SettingsChoiceBox, value: string) => Promise<void> = settingsPickChoice) {
  root.querySelectorAll<SettingsChoiceBox>('[data-bot-choice]').forEach(box => {
    const button = box.querySelector<HTMLButtonElement>('[data-choice-apply]');
    const mount = () => {
      const fields = box.querySelector<HTMLElement>('[data-choice-fields]')!;
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
        box.querySelectorAll<HTMLSelectElement | HTMLButtonElement>('select,button').forEach(control => { control.disabled = true; });
        try { await onPick(box, value); }
        finally { if (box.isConnected) mount(); }
      };
    };
    mount();
  });
}
// The /fallback body for a picker value ('' is None); Apply and the bot editor's Save send the same. Its fourth part is
// the fallback's own window; none is "Same as bot".
function settingsFallbackBody(value: string) {
  const choice = value ? settingsChoiceFromValue(value) : null, compact_at = Number(value.split('::')[3]) || null;
  return choice ? {harness: choice.harness, model: choice.model, effort: choice.effort, ...(compact_at ? {compact_at} : {})} : null;
}
async function settingsPickChoice(box: HTMLElement, value: string) {
  const slug = box.dataset.botChoice, e: SettingsBot | undefined = S.emps.find(row => row.id === slug);
  const current = box.dataset.current || '';
  if (!e || value === current) return;
  const input = box.querySelector<HTMLInputElement>('input[type=search]');
  if (box.dataset.kind === 'task') {
    try { await settingsSaveTaskModel(e, {choice: value ? settingsChoiceFromValue(value) : null}, e.revision); }
    catch (error) { toast(errorMessage(error, ''), true); }
    return;
  }
  if (box.dataset.kind === 'fallback') {
    try {
      const choice = value ? settingsChoiceFromValue(value) : null;
      await post(`/v2/bots/${encodeURIComponent(String(slug))}/fallback`, {fallback: settingsFallbackBody(value), expected_revision: e.revision});
      await loadSettings();
      toast(choice ? `${e.display_name} fallback is ${settingsChoiceLabel(choice.harness, choice.model, choice.effort)}`
        : `${e.display_name} fallback is none`);
    } catch (error) {
      if (input) input.value = input.defaultValue;
      toast(errorMessage(error, ''), true);
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
