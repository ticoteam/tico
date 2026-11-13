/* ui/app/settings-bots-choice.ts — Settings > Bots: the one model picker (a bot's model, fallback and task model, a new
   bot's model, bulk change). Closed it is a short button ("Opus 5.5 MED 400K"); open it is one panel: Harness, Model
   (each with its window), Effort and Compact, then Apply.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Which window a picker's Compact row sets: the bot's own (`bot`: a bot's model, a new bot), its tasks' (`task`,
// "Same as bot" first), the fallback's own (`fallback`, "Same as bot" first: the turn's window lowered to fit), every
// selected bot's (`bulk`, "Keep each bot's" first, and settable with no model picked), or none.
type SettingsCompactMode = '' | 'bot' | 'task' | 'fallback' | 'bulk';
// A box's pick: its value ('harness::model::effort', '' for Same as bot / None) and its window (null: the model's
// default, the bot's own or each bot's own, as the mode says).
interface SettingsChoicePicker { value: () => string; compact: () => number | null; valid: () => boolean; changed: () => boolean; }
// A [data-bot-choice] box keeps its pick, so the bot editor's Save reads a pick that was Applied but not saved, and
// what Apply does with it (`onPick`: save it, or hold it).
interface SettingsChoiceBox extends HTMLElement { picker?: SettingsChoicePicker; onPick?: (box: SettingsChoiceBox, value: string, compact: number | null) => Promise<void>; }
// How a box is drawn: what it chooses, for which bot, and what it has now.
interface SettingsChoiceSpec {
  bot?: SettingsBot | null | undefined; kind: string; name: string; current: string; compact?: number | null; readonly?: boolean;
  none?: string; noEffort?: boolean; mode: SettingsCompactMode;
}

// Where a conversation compacts (backend/providers.py COMPACT_SIZES).
const SETTINGS_COMPACT_AT: [number, string][] = [[100000, '100k'], [150000, '150k'], [200000, '200k'], [250000, '250k'], [300000, '300k'],
  [400000, '400k'], [500000, '500k'], [600000, '600k'], [700000, '700k'], [800000, '800k'], [900000, '900k'], [1000000, '1M']];
const SETTINGS_COMPACT_SIZES = SETTINGS_COMPACT_AT.map(([size]) => size);
let SETTINGS_CHOICE_UID = 0;
// The button whose panel is open (focus goes back to it when the panel closes).
let SETTINGS_CHOICE_ANCHOR: HTMLElement | null = null;
// One choice in a panel row: a segment or a model line.
type SettingsPanelRow = {value: string; label: string; on: boolean; off?: boolean; title?: string | undefined; extra?: string};

// The harness a pick runs on: the Gateway's picks run on pi.
const settingsRealHarness = (harness: string | null | undefined) => harness === 'tico' ? 'pi' : harness || '';
// Whether a harness compacts at a size we set (its manifest says `compaction`).
// One that compacts on its own default (`own_compaction`: Claude Code, as for a person) takes no size either.
function settingsCompacts(harness: string | null | undefined) {
  const id = settingsRealHarness(harness), caps = id ? settingsHarness(id)?.capabilities || harnessCaps(id) : null;
  return !!caps && !!caps.compaction && !caps.own_compaction;
}
// A model's context window in tokens (a Gateway pick: its tier's version), or 0 when unknown.
function settingsModelWindow(id: string | null | undefined) {
  const t = settingsTierParse(id), tier = t && SETTINGS_DATA.gateway?.tiers[t.tier];
  const version = tier && tier.versions.find(v => v.version === (t?.version === 'auto' ? tier.current : t?.version));
  return Number(version?.context || settingsModel(id)?.context_tokens || 0);
}
// `size` lowered to the largest size that fits a window (backend/providers.py fit_compact_at).
function settingsFitCompact(size: number, window: number) {
  if (!window || size <= window) return size;
  return [...SETTINGS_COMPACT_SIZES].reverse().find(s => s <= window) || SETTINGS_COMPACT_SIZES[0]!;
}
// Where a bot on `model` compacts when it has none saved (backend/providers.py compact_default, sent per model).
function settingsCompactDefault(id: string | null | undefined) {
  const own = settingsModel(id)?.compact_default;
  if (own) return own;
  const name = String(id || '');
  return settingsFitCompact(name.includes('opus') ? 400000 : name.includes('haiku') ? 150000 : 250000, settingsModelWindow(id));
}
const settingsSizeWords = (size: number | null | undefined) => modelContextWords(size);
const settingsEffortShort = (effort: string | null | undefined) => effort === 'as-configured' ? ''
  : ({low: 'LOW', medium: 'MED', high: 'HIGH', xhigh: 'XHIGH', max: 'MAX'} as Record<string, string>)[String(effort)] || String(effort || '').toUpperCase();
// A model as the closed button reads it: "Opus 5.5", "GPT 6 Luna" (format.ts modelWords, without the Claude family name the
// model already says); a Gateway tier or an id modelWords returns as it is keeps the catalog's name, first letter raised.
const settingsModelShort = (id: string) => {
  const read = settingsTierParse(id) ? '' : modelWords(id), words = read && read !== id ? read.replace(/^Claude /, '') : '';
  const name = words || settingsModelName(id);
  return name.charAt(0).toUpperCase() + name.slice(1);
};
// "Claude Code", "Antigravity", "Pi": the catalog's name without its provider, first letter raised (some are lower case).
const settingsHarnessShort = (id: string) => { const name = (settingsHarnessName(id) || id).replace(/\s*\(.*\)$/, ''); return name.charAt(0).toUpperCase() + name.slice(1); };

// What a box's pick reads as: `short` on the closed button ("Opus 5.5 MED 400K", "Same as bot 150K") and `long` for
// its tooltip and accessible name ("Claude Code, opus 5.5, medium effort, compacts at 400k").
function settingsPickWords(spec: SettingsChoiceSpec, value: string, compact: number | null | undefined) {
  const c = settingsChoiceFromValue(value), bot = spec.bot;
  if (!c.model) {
    if (!spec.none) return bot ? {short: settingsDefaultLabel(bot), long: settingsDefaultLabel(bot)} : {short: 'Choose', long: 'Not chosen'};
    // Same as bot with its tasks' own window, or bulk change's window alone; a fallback of None has none.
    const size = compact && (spec.mode === 'bulk' || (spec.mode === 'task' && settingsCompacts(bot ? settingsBotHarness(bot) : ''))) ? settingsSizeWords(compact) : '';
    return {short: [spec.none, size].filter(Boolean).join(' '), long: [spec.none, size ? `compacts at ${size.toLowerCase()}` : ''].filter(Boolean).join(', ')};
  }
  const compacts = !!spec.mode && settingsCompacts(c.harness);
  const size = compacts ? compact || (spec.mode === 'bot' ? settingsCompactDefault(c.model) : null) : null;
  // The model's default effort is not named: "Opus 5.5 400K" is Opus at its default.
  const ownEffort = !!c.effort && c.effort === settingsModel(c.model)?.default_effort;
  return {short: [settingsModelShort(c.model), ownEffort ? '' : settingsEffortShort(c.effort), settingsSizeWords(size)].filter(Boolean).join(' '),
    long: [settingsHarnessShort(c.harness), settingsModelShort(c.model), c.effort ? (ownEffort ? 'default effort' : `${settingsEffortName(c.effort)} effort`) : '',
      size ? `compacts at ${settingsSizeWords(size).toLowerCase()}` : compacts && spec.mode !== 'bot' ? 'compacts as the bot does' : ''].filter(Boolean).join(', ')};
}
// The spec a box was drawn from, read back from its attributes.
function settingsBoxSpec(box: HTMLElement): SettingsChoiceSpec {
  return {bot: box.dataset.botChoice ? S.emps.find(row => row.id === box.dataset.botChoice) : null, kind: box.dataset.kind || 'model',
    name: box.dataset.name || 'Model', current: box.dataset.current || '', compact: Number(box.dataset.compactCurrent) || null,
    readonly: box.hasAttribute('data-readonly'), none: box.dataset.none || '', noEffort: box.hasAttribute('data-no-effort'),
    mode: (box.dataset.compactMode || '') as SettingsCompactMode};
}
// A picker: a box with its closed button. The panel is drawn when it opens (settingsChoicePopover).
function settingsChoiceBox(spec: SettingsChoiceSpec) {
  const words = settingsPickWords(spec, spec.current, spec.compact), who = spec.bot ? ` for ${spec.bot.display_name || spec.bot.slug}` : '';
  return `<div class="settings-choice" data-bot-choice="${esc(spec.bot?.id || '')}" data-kind="${esc(spec.kind)}" data-name="${esc(spec.name)}"
    data-current="${esc(spec.current)}" data-compact-current="${spec.compact || ''}" data-compact-mode="${spec.mode}"
    ${spec.none ? `data-none="${esc(spec.none)}"` : ''} ${spec.noEffort ? 'data-no-effort' : ''} ${spec.readonly ? 'data-readonly' : ''}>
    <button type="button" class="sb-pick${spec.readonly ? ' ro' : ''}" data-choice-open aria-haspopup="dialog" aria-expanded="false"
      aria-label="${esc(`${spec.name}${who}: ${words.long}`)}" title="${esc(words.long)}"><span class="sb-pick-text${settingsChoiceFromValue(spec.current).model ? '' : ' muted'}">${esc(words.short)}</span><span class="sb-pick-caret" aria-hidden="true"></span></button>
  </div>`;
}
// What each bot picker kind is drawn with (SETTINGS_CHOICE_KINDS names it).
function settingsChoiceCombo(e: SettingsBot, kind: SettingsChoiceKind) {
  const spec = SETTINGS_CHOICE_KINDS[kind], selected = spec.of(e);
  const current = selected?.model ? settingsChoiceValue(selected.harness || selected.runtime, selected.model,
    selected.reasoning_effort || selected.effort) : '';
  const compact = kind === 'model' ? Number(e.compact_at) || null : kind === 'task' ? e.task_model?.compact_at || null : e.fallback?.compact_at || null;
  return settingsChoiceBox({bot: e, kind, name: spec.name, current, compact, readonly: !settingsCanManageBot(e),
    none: spec.none === true ? 'None' : spec.none || '', noEffort: kind === 'task', mode: kind === 'model' ? 'bot' : kind});
}
// In the bots table a model or fallback is its picker's closed button.
const settingsChoiceCell = (e: SettingsBot, kind: SettingsChoiceKind) => settingsChoiceCombo(e, kind);

// Wire the panel `form` for `box`: it starts from the box's pick and ends in `apply(value, compact)`.
function settingsWireChoicePanel(form: HTMLFormElement, box: SettingsChoiceBox, apply: (value: string, compact: number | null) => void) {
  const spec = settingsBoxSpec(box), readonly = !!spec.readonly, mode = spec.mode, bot = spec.bot;
  const uid = ++SETTINGS_CHOICE_UID;
  const all = settingsAllChoices();
  // The Gateway's picks are made in the tier controls; its rows stay out of the Model list (and one virtual
  // harness, "Tico AI Gateway", stands for them). `real` maps it back to the pi harness the bot runs them on.
  const gw = settingsGateway(), viaTier = !!gw && all.some(row => settingsIsTico(row.model));
  const choices = viaTier ? all.filter(row => !settingsIsTico(row.model)) : all;
  // A bot's own Model control greys out what its computer cannot run; bulk change checks each bot on Apply.
  const guard = spec.kind === 'model' ? bot : null;
  const block = (id: string | undefined) => guard ? settingsRuntimeBlock(guard, id) : null;
  const held = box.picker ? box.picker.value() : spec.current, savedCompact = spec.compact || null;
  let picked = settingsChoiceFromValue(held);
  let compact: number | null = box.picker ? box.picker.compact() : savedCompact;
  let note = '';
  if (viaTier && settingsIsTico(picked.model)) picked.harness = 'tico';
  let legacy = viaTier && picked.harness === 'tico' && !settingsTierParse(picked.model);   // an id the catalog lacks, until a control changes it
  const inTier = () => viaTier && picked.harness === 'tico';
  const real = () => inTier() ? {...picked, harness: 'pi'} : picked;
  const start = settingsTierKey('medium', false);
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
  // What runs, for the Compact row: a task pick on Same as bot runs on the bot's own harness and model.
  const runHarness = () => picked.harness ? real().harness : mode === 'task' && bot ? settingsBotHarness(bot) : '';
  const runModel = () => picked.model || (mode === 'task' && bot ? settingsTaskRunsOn(bot, '') : '');
  // Bulk change with no model picked sets a window alone, each bot keeping its model: every size is offered.
  // The Compact row shows for every pick, so the panel keeps one shape; only a harness that compacts at a size we set
  // offers sizes (one that compacts on its own, such as Claude Code, offers Default alone and says so).
  const compactShown = () => !!mode && (mode === 'bulk' || !!runModel());
  const compactable = () => !!mode && (mode === 'bulk' && !picked.model ? true : !!runModel() && settingsCompacts(runHarness()));
  // A fallback on a harness that does not compact keeps no window of its own.
  const compactOut = () => compactable() ? compact : mode === 'fallback' ? null : savedCompact;
  // A model change: a saved window past the new model's lowers to fit it; the default follows the model.
  const followModel = (before: string) => {
    note = '';
    const now = runModel();
    if (!compactable() || !now || now === before) return;
    const window = settingsModelWindow(now), name = settingsModelShort(now);
    if (compact && window && compact > window) {
      const to = settingsFitCompact(compact, window);
      note = `Compact was ${settingsSizeWords(compact)}; lowered to ${settingsSizeWords(to)} to fit ${name}.`;
      compact = to;
    } else if (!compact && mode === 'bot' && before && settingsCompactDefault(before) !== settingsCompactDefault(now)) {
      note = `The default follows the model: ${settingsSizeWords(settingsCompactDefault(now))} for ${name}.`;
    }
  };
  const radios = (group: string, rows: SettingsPanelRow[]) =>
    rows.map(row => `<label class="mp-opt${row.off ? ' off' : ''}" ${row.title ? `title="${esc(row.title)}"` : ''}><input type="radio" name="mp-${group}-${uid}" value="${esc(row.value)}"
      ${row.on ? 'checked' : ''} ${row.off || readonly ? 'disabled' : ''}><span class="mp-name">${esc(row.label)}</span>${row.extra || ''}</label>`).join('');
  const option = (value: string | number, label: string, selected: boolean, disabled = false) => `<option value="${esc(value)}" ${selected ? 'selected' : ''} ${disabled ? 'disabled' : ''}>${esc(label)}</option>`;
  const part = (selector: string) => form.querySelector<HTMLElement>(selector)!;
  const paintHarness = () => {
    // The harnesses the team offers, always in the catalog's order: one whose provider is off shows greyed with its
    // way in (Settings > Models); an external agent (Hermes, OpenClaw) or one the catalog marks `offered: false` is
    // not picked here unless the bot is on it.
    const have = new Set(choices.map(row => row.harness)), catalog = SETTINGS_DATA.harnesses || [];
    const offered = (h: HarnessRow | undefined) => !!h && !h.external && h.offered !== false;
    const harnesses = [...new Set([...catalog.filter(offered).map(h => h.id), ...[...have].filter(id => offered(settingsHarness(id)))])]
      .filter((id): id is string => !!id);
    const rows: SettingsPanelRow[] = [];
    if (spec.none) rows.push({value: '', label: spec.none, on: !picked.harness});
    if (picked.harness && picked.harness !== 'tico' && !harnesses.includes(picked.harness)) rows.push({value: picked.harness, label: settingsHarnessShort(picked.harness), on: true, off: true});
    harnesses.forEach(id => { const why = block(id), name = settingsHarnessShort(id), on = have.has(id);
      if (!on) { rows.push({value: id, label: `${name} (off)`, on: id === picked.harness, off: true, title: 'Turn its provider on in Settings > Models'}); return; }
      rows.push({value: id, label: why ? `${name} (${why.short})` : name, on: id === picked.harness, off: !!why && id !== picked.harness, title: why?.reason}); });
    if (viaTier) { const why = block('tico');
      rows.push({value: 'tico', label: why ? `Tico AI Gateway (${why.short})` : 'Tico AI Gateway', on: picked.harness === 'tico', off: !!why && picked.harness !== 'tico', title: why?.reason}); }
    part('[data-choice-harness] .mp-seg').innerHTML = radios('h', rows);
    // The reason and its Sign in / Move line only in the open bot editor's picker: the table already marks the bot's
    // row, and every bot on that computer would repeat it.
    part('[data-choice-blocks]').innerHTML = guard && !readonly && box.closest('#bot-editor, [data-bot-people]') ? settingsBlockedNote(guard, harnesses) : '';
  };
  const paintModel = () => {
    const models = [...new Set(choices.filter(row => row.harness === picked.harness).map(row => row.model))];
    const list = part('[data-choice-model]');
    list.hidden = !picked.harness || inTier();
    const row = (id: string, current = false): SettingsPanelRow => ({value: id, label: current ? `${settingsModelShort(id)} (current)` : settingsModelShort(id), on: id === picked.model, off: current,
      extra: id === SETTINGS_DATA.defaultModel ? '<span class="mp-mark">(default)</span>' : ''});
    list.querySelector<HTMLElement>('.mp-list')!.innerHTML = radios('m', [
      ...(picked.model && !inTier() && !models.includes(picked.model) ? [row(picked.model, true)] : []), ...models.map(id => row(id))]);
    part('[data-choice-tier]').hidden = !inTier();
    if (inTier()) paintTier();
  };
  const paintTier = () => {
    if (!gw) return;
    const t = shown(), tier = gw.tiers[t.tier];
    const now = tier?.current;
    const versions = [...(tier?.versions || [])];   // newest first
    part('[data-choice-tier]').innerHTML = `<div class="settings-tier-row">
        <label>Level<select data-tier-level aria-label="Level" ${readonly ? 'disabled' : ''}>
          ${legacy ? option('', 'Choose', true, true) : ''}
          ${gw.levels.map(l => option(l.id, l.name, !legacy && l.id === t.level)).join('')}</select></label>
        <label class="settings-tier-fast"><input type="checkbox" data-tier-fast ${t.fast ? 'checked' : ''} ${readonly ? 'disabled' : ''}>Fast</label>
        <label>Version<select data-tier-version aria-label="Version" ${readonly ? 'disabled' : ''}>
          ${legacy ? option('', `${settingsModelName(picked.model)}`, true, true) : ''}
          ${versions.map(v => option(v.version, v.version + (v.version === now ? ' (current)' : ''), !legacy && t.version === v.version)).join('')}
          ${option('auto', 'Auto (always latest)', !legacy && t.version === 'auto')}</select></label></div>
      <p class="settings-tier-line" data-tier-line>${esc(settingsTierLine(picked.model))}</p>`;
  };
  const defaultEffort = () => inTier() ? tierEfforts(shown()).def : settingsModel(picked.model)?.default_effort || '';
  const efforts = () => inTier() ? tierEfforts(shown()).list
    : choices.filter(row => row.harness === picked.harness && row.model === picked.model).map(row => row.effort);
  const paintEffort = () => {
    const list = efforts(), current = picked.effort, row = part('[data-choice-effort]');
    row.hidden = !!spec.noEffort || !picked.model;
    const rows: SettingsPanelRow[] = current && !list.includes(current) ? [{value: current, label: `${settingsEffortName(current)} (current)`, on: true, off: true}] : [];
    // The model's own default comes first as Default; the closed button then names no effort.
    const def = defaultEffort(), order = list.includes(def) ? [def, ...list.filter(id => id !== def)] : list;
    order.forEach(id => { rows.push({value: id, label: id === def ? 'Default' : id === 'as-configured' ? 'Managed by harness' : settingsEffortName(id), on: id === current, off: list.length <= 1 && id !== current}); });
    row.querySelector<HTMLElement>('.mp-seg')!.innerHTML = radios('e', rows);
  };
  const paintCompact = () => {
    const row = part('[data-choice-compact-row]'), select = part('[data-choice-compact]') as HTMLSelectElement;
    row.hidden = !compactShown();
    const sized = compactable();
    part('[data-choice-note]').textContent = sized || row.hidden ? note : `${settingsHarnessShort(runHarness())} compacts on its own.`;
    if (row.hidden) return;
    const model = runModel(), window = settingsModelWindow(model), def = mode === 'bot' && sized ? settingsCompactDefault(model) : null;
    if (!sized) {
      select.innerHTML = option('', mode === 'bot' ? 'Default' : mode === 'bulk' ? "Keep each bot's" : 'Same as bot', true);
      select.disabled = true;
      return;
    }
    // Only the sizes the model can hold; a size saved before, off the list, stays where it fits.
    const sizes = SETTINGS_COMPACT_SIZES.filter(size => !window || size <= window);
    if (compact && !sizes.includes(compact) && (!window || compact <= window)) sizes.push(compact);
    sizes.sort((a, b) => a - b);
    // First the choice that saves no size: a bot's Default follows its model, Same as bot follows the bot.
    const first = mode === 'bot' ? `Default (${settingsSizeWords(def)})` : mode === 'bulk' ? "Keep each bot's" : 'Same as bot';
    select.innerHTML = option('', first, !compact) + sizes.map(size => option(size, settingsSizeWords(size), size === compact)).join('');
    select.disabled = readonly;
  };
  const value = () => picked.model ? settingsChoiceValue(real().harness, picked.model, picked.effort) : '';
  const tierValid = () => { const t = settingsTierParse(picked.model);
    return !!t && (t.version === 'auto' || !!gw?.tiers[t.tier]?.versions.some(v => v.version === t.version)) && tierEfforts(t).list.includes(picked.effort) && !block('tico'); };
  const valid = () => (inTier() ? tierValid() : (choices.some(row => row.value === value()) && !block(picked.harness)))
    || (!picked.harness && !!spec.none);
  const changed = () => value() !== spec.current || compactOut() !== savedCompact;
  const applyButton = form.querySelector<HTMLButtonElement>('[data-choice-apply]');
  // Repaint from one row down, keeping the keyboard where it was.
  const paint = (from: 'all' | 'harness' | 'model' | 'effort') => {
    const active = document.activeElement, inside = !!active && form.contains(active);
    const again = active instanceof HTMLInputElement && active.type === 'radio' ? `input[name="${active.name}"]:checked`
      : active instanceof HTMLElement ? ['data-tier-level', 'data-tier-fast', 'data-tier-version', 'data-choice-compact'].filter(name => active.hasAttribute(name)).map(name => `[${name}]`)[0] : '';
    if (from === 'all') paintHarness();
    if (from === 'all' || from === 'harness') paintModel();
    else if (from === 'model' && inTier()) paintTier();
    if (from !== 'effort') paintEffort();
    paintCompact();
    if (applyButton) applyButton.disabled = readonly || !valid() || !changed();
    form.dataset.value = value();   // the pick Apply would make, for a reader of the open panel
    if (inside && !form.contains(document.activeElement) && again) form.querySelector<HTMLElement>(again)?.focus();
  };
  form.addEventListener('change', event => {
    // A radio, a tier control or the Compact select; each reads its value (and Fast its check).
    const target = event.target as HTMLInputElement;
    const before = runModel();
    if (target.closest('[data-choice-harness]')) {
      picked = {harness: target.value, model: '', effort: ''};
      if (picked.harness === 'tico') {   // Medium, pinned to the current version
        legacy = false;
        setTier({tier: start, level: 'medium', fast: false, version: gw?.tiers[start]?.current || '1.0'});
      } else {
        // One model on that harness: it is the pick.
        const models = [...new Set(choices.filter(row => row.harness === picked.harness).map(row => row.model))];
        if (models.length === 1) pickModel(models[0]!);
      }
      followModel(before); paint('harness');
    } else if (target.closest('[data-choice-model]')) {
      pickModel(target.value);
      followModel(before); paint('model');
    } else if (target.closest('[data-choice-effort]')) {
      picked.effort = target.value; paint('effort');
    } else if (target.matches('[data-tier-level], [data-tier-fast], [data-tier-version]')) {
      const t = shown();
      if (target.matches('[data-tier-level]')) setTier({...t, level: target.value});
      if (target.matches('[data-tier-fast]')) setTier({...t, fast: target.checked});
      if (target.matches('[data-tier-version]')) setTier({...t, version: target.value});
      followModel(before); paint('model');
    } else if (target.matches('[data-choice-compact]')) {
      const size = Number(target.value) || null;
      compact = size;
      note = ''; paint('effort');
    }
  });
  function pickModel(id: string) {
    const row = settingsModel(id);
    const list = choices.filter(choice => choice.harness === picked.harness && choice.model === id).map(choice => choice.effort);
    const own = row?.default_effort;
    // An effort left on the old model's Default follows to the new model's.
    const wasDefault = !picked.effort || picked.effort === settingsModel(picked.model)?.default_effort;
    picked = {harness: picked.harness, model: id,
      effort: list.includes(picked.effort) && !wasDefault ? picked.effort : (own !== undefined && list.includes(own) ? own : list[0] || '')};
  }
  // Enter applies from any row; a button keeps its own Enter.
  form.addEventListener('keydown', event => {
    if (event.key !== 'Enter' || (event.target as HTMLElement).matches('button, textarea')) return;
    event.preventDefault();
    form.requestSubmit();
  });
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (readonly || !valid() || !changed()) return;
    apply(value(), compactOut());
  });
  paint('all');
}
// The panel's rows, filled by settingsWireChoicePanel.
function settingsChoicePanelHTML(spec: SettingsChoiceSpec) {
  return `<form class="mp-panel" data-choice-fields novalidate>
    <fieldset class="mp-row" data-choice-harness><legend>Harness</legend><div class="mp-seg"></div></fieldset>
    <fieldset class="mp-row" data-choice-model><legend>Model</legend><div class="mp-list"></div></fieldset>
    <div class="settings-tier mp-row" data-choice-tier hidden></div>
    <fieldset class="mp-row" data-choice-effort><legend>Effort</legend><div class="mp-seg"></div></fieldset>
    <label class="mp-row mp-compact" data-choice-compact-row hidden><span>Compact</span><select data-choice-compact aria-label="Compact"></select></label>
    <p class="mp-note" data-choice-note role="status"></p>
    <div class="settings-choice-blocks" data-choice-blocks></div>
    <div class="mp-foot">${spec.readonly ? '<button type="button" class="ghost" data-choice-cancel>Close</button>'
      : '<button type="submit" class="primary" data-choice-apply disabled>Apply</button><button type="button" class="ghost" data-choice-cancel>Cancel</button>'}</div>
  </form>`;
}
// Open a box's panel in a popover under its button (a sheet on a phone). In a modal dialog it opens inside it, so
// the dialog does not make it inert.
function settingsChoicePopover(anchor: HTMLElement) {
  const box = anchor.closest<SettingsChoiceBox>('[data-bot-choice]');
  if (!box) return;
  let pop = document.getElementById('sb-choice-pop');
  if (pop?.matches(':popover-open')) { const same = SETTINGS_CHOICE_ANCHOR === anchor; pop.hidePopover(); if (same) return; }
  if (!pop) {
    const made = document.createElement('div');
    pop = made;
    made.id = 'sb-choice-pop'; made.className = 'tl-pop sb-choice-pop'; made.popover = 'auto';
    made.setAttribute('role', 'dialog');
    made.addEventListener('toggle', (ev: ToggleEvent) => {
      const opener = SETTINGS_CHOICE_ANCHOR;
      if (ev.newState !== 'closed' || !opener) return;
      opener.setAttribute('aria-expanded', 'false');
      if (opener.isConnected && (made.contains(document.activeElement) || document.activeElement === document.body)) opener.focus();
    });
    // A row that grows the panel (the Gateway's tier controls, the Compact row) keeps it on screen.
    new ResizeObserver(() => { if (made.matches(':popover-open')) tasksMenuPlace(made, SETTINGS_CHOICE_ANCHOR); }).observe(made);
  }
  const shown = pop;
  (anchor.closest('dialog[open]') || document.body).append(shown);
  SETTINGS_CHOICE_ANCHOR = anchor;
  const spec = settingsBoxSpec(box), who = spec.bot ? ` · ${spec.bot.display_name || spec.bot.slug}` : '';
  shown.setAttribute('aria-label', `${spec.name}${who}`);
  shown.innerHTML = `<div class="sb-choice-head">${esc(spec.name)}${esc(who)}</div>${settingsChoicePanelHTML(spec)}`;
  const form = shown.querySelector<HTMLFormElement>('form')!;
  shown.querySelector<HTMLButtonElement>('[data-choice-cancel]')!.onclick = () => shown.hidePopover();
  // Apply closes the panel first: a model change continues in its own dialog.
  settingsWireChoicePanel(form, box, (value, compact) => { shown.hidePopover(); void (box.onPick || settingsPickChoice)(box, value, compact); });
  shown.showPopover();
  anchor.setAttribute('aria-expanded', 'true');
  tasksMenuPlace(shown, anchor);
  shown.querySelector<HTMLElement>('input:checked:not(:disabled), input:not(:disabled), select:not(:disabled), button')?.focus();
}
document.addEventListener('click', event => {
  const open = eventElement(event)?.closest<HTMLElement>('[data-choice-open]');
  if (open) settingsChoicePopover(open);
});
// Give each box under `root` its pick and what Apply does: `onPick` saves at once (the bots table), or `hold` keeps
// the pick on the box until a Save elsewhere (the bot editor, new bot, bulk change).
function settingsWireCombos(root: ParentNode, onPick: ((box: SettingsChoiceBox, value: string, compact: number | null) => Promise<void>) | 'hold' = settingsPickChoice) {
  root.querySelectorAll<SettingsChoiceBox>('[data-bot-choice]').forEach(box => {
    const saved = () => ({value: box.dataset.current || '', compact: Number(box.dataset.compactCurrent) || null});
    let pick = saved();
    box.picker = {value: () => pick.value, compact: () => pick.compact, valid: () => true,
      changed: () => pick.value !== saved().value || pick.compact !== saved().compact};
    box.onPick = onPick === 'hold' ? async (_box, value, compact) => { pick = {value, compact}; settingsChoiceRepaint(box); } : onPick;
  });
}
// A box's closed button, after its pick changed: it says the pick, and the box tells its form (tico:choice).
function settingsChoiceRepaint(box: SettingsChoiceBox) {
  const spec = settingsBoxSpec(box), value = box.picker ? box.picker.value() : spec.current;
  const words = settingsPickWords(spec, value, box.picker ? box.picker.compact() : spec.compact);
  const button = box.querySelector<HTMLElement>('[data-choice-open]'), text = box.querySelector<HTMLElement>('.sb-pick-text');
  if (text) { text.textContent = words.short; text.classList.toggle('muted', !settingsChoiceFromValue(value).model); }
  if (button) { button.title = words.long; button.setAttribute('aria-label', `${spec.name}${spec.bot ? ` for ${spec.bot.display_name || spec.bot.slug}` : ''}: ${words.long}`); }
  box.dispatchEvent(new CustomEvent('tico:choice', {bubbles: true}));
}
// The /fallback body for a picker value ('' is None) and its own window (none: Same as bot); Apply and the bot
// editor's Save send the same.
function settingsFallbackBody(value: string, compact: number | null = null) {
  const choice = value ? settingsChoiceFromValue(value) : null;
  return choice ? {harness: choice.harness, model: choice.model, effort: choice.effort, ...(compact ? {compact_at: compact} : {})} : null;
}
// Apply in the bots table: save the pick. A window alone saves at once and keeps the session; a model change asks
// first and continues in its own dialog, after the window.
async function settingsPickChoice(box: SettingsChoiceBox, value: string, compact: number | null) {
  const slug = box.dataset.botChoice, e: SettingsBot | undefined = S.emps.find(row => row.id === slug);
  const current = box.dataset.current || '', savedCompact = Number(box.dataset.compactCurrent) || null;
  if (!e || (value === current && compact === savedCompact)) return;
  if (box.dataset.kind === 'task') {
    const change: SettingsTaskChange = {};
    if (value !== current) change.choice = value ? settingsChoiceFromValue(value) : null;
    if (compact !== savedCompact) change.compact_at = compact;
    try { await settingsSaveTaskModel(e, change, e.revision); }
    catch (error) { toast(errorMessage(error, ''), true); }
    return;
  }
  if (box.dataset.kind === 'fallback') {
    try {
      const choice = value ? settingsChoiceFromValue(value) : null;
      await post(`/v2/bots/${encodeURIComponent(String(slug))}/fallback`, {fallback: settingsFallbackBody(value, compact), expected_revision: e.revision});
      await loadSettings();
      toast(choice ? `${e.display_name} fallback is ${settingsChoiceLabel(choice.harness, choice.model, choice.effort)}${compact ? `, compacting at ${settingsSizeWords(compact)}` : ''}`
        : `${e.display_name} fallback is none`);
    } catch (error) { toast(errorMessage(error, ''), true); }
    return;
  }
  const choice = settingsChoiceFromValue(value), move = value !== current;
  if (move && (!choice.model || !choice.effort || !choice.harness)) return;
  if (move && !await settingsConfirmTransition(e, 'model', settingsChoiceLabel(choice.harness, choice.model, choice.effort))) return;
  let revision = e.revision || 0;
  // A size saves it; Default (null) clears a saved one, back to the model's own.
  if (compact !== savedCompact) {
    try { revision = (await post(`/v2/bots/${encodeURIComponent(e.id)}/compact-at`, {compact_at: compact, expected_revision: revision}))?.revision ?? revision + 1; }
    catch (error) { toast(errorMessage(error, ''), true); return; }
    if (!move) { await loadSettings(); toast(compact ? `${e.display_name} compacts at ${settingsSizeWords(compact)}` : `${e.display_name} compacts at its model's default`); return; }
  }
  await settingsBeginTransition(box, e, {kind: 'model', model: choice.model, effort: choice.effort,
    harness: choice.harness, expected_generation: e.machine?.generation || 0, expected_revision: revision});
}
