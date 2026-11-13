/* ui/app/settings-access.ts — Who may see, read and write a bot: the access editor
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The access shapes this file reads and writes. An audience is everyone, or the chosen humans (people), groups (teams)
// and bots. A policy holds the three levels a bot has: see, read and write.
type SaKey = 'see' | 'read' | 'write';
type SaAudience = import('../types/api').AccessAudience;
type SaPolicy = Record<SaKey, SaAudience>;
type SaListKey = 'people' | 'teams' | 'bots';
// A row in a picker list: a person, a group or a bot, by id, with the name it reads as.
type SaRow = {id: string; name?: string | undefined};
// What GET .../access answers, or what a page passes as initialData: the three levels, the groups and the revision.
type SaData = SaPolicy & {teams?: SaRow[] | undefined; revision?: number | undefined};
// What a save hands the after callback: a page's save answers the page (pages.ts reads it).
type SaSaved = {page?: unknown};
// A level is [key, label, what it lets]; a preset is [value, label, hint].
type SaLevel = [SaKey, string, string];
type SaPreset = [string, string, string];
// A write held by the bot editor: it runs with the bot's current revision and answers the revision it saved.
type SaRun = (rev: number) => Promise<number>;
type SaStage = (run: SaRun, policy: SaPolicy) => void;
type SaMailSettings = {set?: boolean | undefined; outbound_send?: boolean | undefined};
type SaMailStage = (run: SaRun, m: SaMailSettings) => void;
type SaOwnersStage = (run: SaRun, people: SettingsPerson[]) => void;
type SaOptions = {
  path?: string | undefined; name: string | undefined; levels?: SaLevel[] | undefined; presets?: SaPreset[] | undefined;
  skipBot?: string | undefined; skipPerson?: string | undefined; stage?: SaStage | undefined; panel?: boolean | undefined;
  after?: ((saved: SaSaved) => unknown) | undefined; initialData?: SaData | undefined; singleAudience?: boolean | undefined;
  save?: ((read: SaAudience) => Promise<unknown>) | undefined;
};
// The mail editor's form: its one checkbox is named outbound_send (form.elements.outbound_send below).
type SaMailForm = HTMLFormElement & {elements: HTMLFormControlsCollection & {outbound_send: HTMLInputElement}};

// Existing bot configuration is edited in the Settings tab. Company-level controls retain their own dialogs.
function botSettingsPanel(id: string, className: string, titleId: string): HTMLElement | null {
  const host = $('#bot-settings-panels');
  if (!host || BOT?.tab !== 'settings') return null;
  const panel = document.createElement('div');
  panel.id = id; panel.className = `${className} bot-settings-panel`;
  panel.setAttribute('aria-labelledby', titleId); panel.setAttribute('role', 'region');
  host.replaceChildren(panel);
  return panel;
}
function settingsPanelClose(panel: Element) {
  if (panel instanceof HTMLDialogElement) panel.close(); else panel.remove();
}
function settingsPanelShow(panel: HTMLElement) {
  if (panel instanceof HTMLDialogElement) panel.showModal();
  else { panel.scrollIntoView({block: 'start'}); panel.querySelector<HTMLElement>('input,button')?.focus({preventScroll: true}); }
}

// ----- Access: who may see, read and write to a bot (backend/bot_access.py, docs/permissions.md).
// Each level is everyone, or humans, groups and bots. Open is all three Everyone; the owner, the bot
// itself and the humans it reports up to always have all three.
const ACCESS_LEVELS: SaLevel[] = [
  ['see', 'See', 'the bot in the team chart and lists: its name, role, who owns it and who it reports to'],
  ['read', 'Read', 'its work: tasks, updates, files, status, run log and automations'],
  ['write', 'Write', 'send it messages, tasks, notes and comments'],
];
const ACCESS_PRESETS: SaPreset[] = [
  ['open', 'Open', 'Everyone'],
  ['requests', 'Requests only', 'Everyone sends requests; chosen humans read its work'],
  ['private', 'Private', 'Chosen humans only'],
  ['custom', 'Custom', 'See, read and write separately'],
];
const accessTeamName = (id: string) => (S.orgGroups || []).find(g => g.id === id)?.name || teamLabel(id);
const accessEmpty = (): SaAudience => ({everyone: false, people: [], teams: [], bots: []});
function accessSummary(policy: SaPolicy | null | undefined) {
  if (!policy) return 'Open';
  if (ACCESS_LEVELS.every(([key]) => policy[key]?.everyone)) return 'Open';
  return ACCESS_LEVELS.map(([key, label]) => `${label}: ${accessLevelText(policy[key])}`).join(' · ');
}
// One word for the table row; the editor shows the full sentence (accessSummary).
const ACCESS_WORD: Record<string, string> = {open: 'Open', requests: 'Requests only', private: 'Private', custom: 'Custom'};
function settingsAccessWord(e: Employee) {
  const mine = e.my_access || {see: true, read: true, write: true};
  return e.access_policy ? ACCESS_WORD[accessPresetOf(e.access_policy)] || 'Custom'
    : `You: ${ACCESS_LEVELS.filter(([key]) => mine[key]).map(([, label]) => label).join(' · ') || 'See'}`;
}
function settingsAccessCell(e: Employee) {
  const word = settingsAccessWord(e);
  return `<span class="sb-access" data-access-summary title="${esc(e.access_policy ? accessSummary(e.access_policy) : word)}">${esc(word)}</span>`;
}
// Who owns a bot: its creator and co-owners (and its operator). Whoever it reports up to and the admins own it too,
// without being listed. Any owner adds or removes humans here; the server checks it (docs/permissions.md).
// `stage` (the bot editor): Done hands the save to the editor, which runs it with Save bot.
async function settingsEditBotOwners(slug: string, {stage}: {stage?: SaOwnersStage | undefined} = {}) {
  const e = S.emps.find(row => row.id === slug);
  if (!e) return;
  let dialog = botSettingsPanel('bot-owners-editor', 'owner-picker', 'bot-owners-title') || $('#bot-owners-editor');
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.className = 'owner-picker'; dialog.id = 'bot-owners-editor'; dialog.setAttribute('aria-labelledby', 'bot-owners-title');
    document.body.append(dialog);
  }
  const listed = new Set((e.bot_owners || []).map(o => o.id));
  const people: SettingsPerson[] = SETTINGS_DATA.people?.length ? SETTINGS_DATA.people : (S.people || []).filter(p => !p.hidden);
  dialog.innerHTML = `<form><div class="tmodal-head"><h2 id="bot-owners-title">Who owns ${esc(e.display_name)}</h2><span class="spacer"></span><button class="ghost" type="button" data-owners-close aria-label="Close">✕</button></div>
    <div class="owner-picker-body">${e.owner_id ? `<p class="muted">${esc(settingsPersonName(e.owner_id))} is responsible for this bot and always stays an owner.</p>` : ''}
      <div class="owner-options">${people.map(person => `<label class="owner-option"><input type="checkbox" name="owner" value="${esc(person.id)}" ${listed.has(person.id) || person.id === e.owner_id ? 'checked' : ''} ${person.id === e.owner_id ? 'disabled' : ''}>${personAvatar(person, 22)}<span>${esc(person.name || person.id)}</span></label>`).join('')}</div>
      <div class="row"><button class="primary" type="submit">${stage ? 'Done' : 'Save'}</button><button class="ghost" type="button" data-owners-close>Cancel</button><span class="muted" data-owners-status role="status"></span></div></div></form>`;
  dialog.querySelectorAll('[data-owners-close]').forEach((button: HTMLElement) => button.onclick = () => settingsPanelClose(dialog));
  dialog.querySelector('form').onsubmit = async (event: SubmitEvent) => {
    event.preventDefault();
    const chosen = new Set<string>([...dialog.querySelectorAll('input[name=owner]:checked:not(:disabled)')].map((input: HTMLInputElement) => input.value));
    const add = [...chosen].filter(id => !listed.has(id) && id !== e.owner_id), remove = [...listed].filter(id => !chosen.has(id) && id !== e.owner_id);
    const status = dialog.querySelector('[data-owners-status]');
    if (!add.length && !remove.length) { settingsPanelClose(dialog); return; }
    if (stage) {
      stage(async rev => (await post(`/v2/bots/${encodeURIComponent(slug)}/co-owners`, {add, remove}))?.revision ?? rev + 1,
        people.filter(person => chosen.has(person.id) || person.id === e.owner_id));
      settingsPanelClose(dialog); return;
    }
    dialog.querySelectorAll('button,input').forEach((control: HTMLButtonElement | HTMLInputElement) => { control.disabled = true; }); status.textContent = 'Saving…';
    try {
      await post(`/v2/bots/${encodeURIComponent(slug)}/co-owners`, {add, remove});
      settingsPanelClose(dialog); await loadSettings(); toast('Saved');
    } catch (error) {
      status.innerHTML = `<span class="err">${esc(errorMessage(error, ''))}</span>`;
      dialog.querySelectorAll('button,input').forEach((control: HTMLButtonElement | HTMLInputElement) => { control.disabled = false; });
      dialog.querySelectorAll('input[name=owner][value]').forEach((input: HTMLInputElement) => { if (input.value === e.owner_id) input.disabled = true; });
    }
  };
  dialog.onclose = () => { dialog.innerHTML = ''; };
  settingsPanelShow(dialog);
}
// Mail sending (backend/mail_settings.py, docs/mail.md "Turning sending on"): whether a bot sends without a
// per-message approval (its layer of the one sending policy, backend/email_sending.py). Set by a person who manages the bot.
function mailSettingText(m: SaMailSettings) {
  if (!m.set) return 'Off: no one has turned it on';
  return m.outbound_send ? 'On' : 'Off';
}
async function settingsMailSummary(root: ParentNode, slug: string) {
  const cell = root.querySelector(`[data-mail-summary="${CSS.escape(slug)}"]`);
  if (!cell) return;
  try {
    const m = await get<SaMailSettings>(`/v2/bots/${encodeURIComponent(slug)}/mail-settings`);
    cell.textContent = mailSettingText(m);
  } catch { cell.textContent = ''; }
}
async function settingsEditMail(slug: string, after?: (() => unknown) | null, {stage}: {stage?: SaMailStage | undefined} = {}) {
  const e = S.emps.find(row => row.id === slug);
  if (!e) return;
  let dialog = botSettingsPanel('bot-mail-editor', 'owner-picker', 'bot-mail-title') || $('#bot-mail-editor');
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.className = 'owner-picker'; dialog.id = 'bot-mail-editor'; dialog.setAttribute('aria-labelledby', 'bot-mail-title');
    document.body.append(dialog);
  }
  let m: SaMailSettings;
  try { m = await get<SaMailSettings>(`/v2/bots/${encodeURIComponent(slug)}/mail-settings`); } catch (error) { toast(errorMessage(error, ''), true); return; }
  dialog.innerHTML = `<form><div class="tmodal-head"><h2 id="bot-mail-title">Mail sending for ${esc(e.display_name)}</h2><span class="spacer"></span><button class="ghost" type="button" data-mail-close aria-label="Close">✕</button></div>
    <div class="owner-picker-body">
      <p class="muted">Each mailbox's Limits still apply.</p>
      <label class="owner-option"><input type="checkbox" name="outbound_send" ${m.outbound_send ? 'checked' : ''}><span>Send without a per-message approval</span></label>
      <div class="row"><button class="primary" type="submit">${stage ? 'Done' : 'Save'}</button><button class="ghost" type="button" data-mail-close>Cancel</button><span class="muted" data-mail-status role="status"></span></div></div></form>`;
  dialog.querySelectorAll('[data-mail-close]').forEach((button: HTMLElement) => button.onclick = () => settingsPanelClose(dialog));
  dialog.querySelector('form').onsubmit = async (event: SubmitEvent) => {
    event.preventDefault();
    const form = event.target as SaMailForm, status = dialog.querySelector('[data-mail-status]');
    const outbound_send = form.elements.outbound_send.checked;
    if (stage) {
      // Mail settings carry no bot revision: the next save starts from the same one.
      stage(async rev => { await post(`/v2/bots/${encodeURIComponent(slug)}/mail-settings`, {outbound_send}); return rev; },
        {set: true, outbound_send});
      settingsPanelClose(dialog); return;
    }
    dialog.querySelectorAll('button,input').forEach((control: HTMLButtonElement | HTMLInputElement) => { control.disabled = true; }); status.textContent = 'Saving…';
    try {
      await post(`/v2/bots/${encodeURIComponent(slug)}/mail-settings`, {outbound_send});
      settingsPanelClose(dialog); toast('Saved'); if (after) void after();
    } catch (error) {
      status.innerHTML = `<span class="err">${esc(errorMessage(error, ''))}</span>`;
      dialog.querySelectorAll('button,input').forEach((control: HTMLButtonElement | HTMLInputElement) => { control.disabled = false; });
    }
  };
  dialog.onclose = () => { dialog.innerHTML = ''; };
  settingsPanelShow(dialog);
}
// Which preset a stored policy is: Open, Visible-requests-only (See and Write Everyone, Read chosen),
// Private (all three the same chosen audience), else Custom.
function accessPresetOf(policy: SaPolicy) {
  const same = (a: SaAudience, b: SaAudience) => JSON.stringify([!!a.everyone, a.people, a.teams, a.bots]) === JSON.stringify([!!b.everyone, b.people, b.teams, b.bots]);
  if (ACCESS_LEVELS.every(([key]) => policy[key].everyone)) return 'open';
  if (policy.see.everyone && policy.write.everyone && !policy.read.everyone) return 'requests';
  if (!policy.see.everyone && same(policy.see, policy.read) && same(policy.read, policy.write)) return 'private';
  return 'custom';
}
async function settingsEditAccess(slug: string, {stage}: {stage?: SaStage | undefined} = {}) {
  const e = S.emps.find(row => row.id === slug);
  if (!e) return;
  return accessEditor({path: `/v2/bots/${encodeURIComponent(slug)}`, name: e.display_name, levels: ACCESS_LEVELS,
    presets: ACCESS_PRESETS, skipBot: slug, stage, panel: true, after: () => loadSettings()});
}
// People and groups (backend/teammate_access.py): the same editor. Whoever manages them always has all three.
const TEAMMATE_ACCESS_LEVELS: Record<'human' | 'group', SaLevel[]> = {
  human: [['see', 'See', 'them in the chart, lists and @ picker'], ['read', 'Read', 'their Home'], ['write', 'Write', 'DM them, add them to threads']],
  group: [['see', 'See', 'it in the chart and lists'], ['read', 'Read', 'its Home'], ['write', 'Write', 'post to it, add it to threads']],
};
const teammateAccessPath = (kind: 'human' | 'group', id: string) => `/v2/${kind === 'human' ? 'humans' : 'groups'}/${encodeURIComponent(id)}`;
// The access, or null when the viewer does not manage them: the server decides (docs/permissions.md).
async function teammateAccessRead(kind: 'human' | 'group', id: string): Promise<SaData | null> {
  try { return await get<SaData>(teammateAccessPath(kind, id) + '/access', {fresh: true}); } catch { return null; }
}
async function accessEditor({path, name, levels, presets = [], skipBot = '', skipPerson = '', stage, panel, after, initialData, singleAudience, save}: SaOptions) {
  let data: SaData;
  try { data = initialData || await get<SaData>(`${path}/access`, {fresh: true}); }
  catch (error) { toast(errorMessage(error, ''), true); return; }
  let dialog = (panel && botSettingsPanel('access-editor', 'access-editor', 'access-editor-title')) || $('#access-editor');
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.className = 'access-editor'; dialog.id = 'access-editor'; dialog.setAttribute('aria-labelledby', 'access-editor-title');
    document.body.append(dialog);
  }
  const people: SettingsPerson[] = (SETTINGS_DATA.people?.length ? SETTINGS_DATA.people : (S.people || []).filter(p => !p.hidden)).filter(p => p.id !== skipPerson);
  const bots = S.emps.filter(row => row.id !== skipBot && row.status !== 'archived');
  const teams = data.teams || [];
  const draft: SaPolicy = {see: {...data.see}, read: {...data.read}, write: {...data.write}};
  const offered = presets.map(([value]) => value);
  let preset = offered.includes(accessPresetOf(draft)) ? accessPresetOf(draft) : 'custom';
  // What the two "chosen" presets edit: the Read audience (requests only), or one audience for all three (Private).
  const chosenOf = () => {
    const pick = (['read', 'see', 'write'] as const).map(key => draft[key]).find(level => !level.everyone) || accessEmpty();
    return {everyone: false, people: [...(pick.people || [])], teams: [...(pick.teams || [])], bots: [...(pick.bots || [])]};
  };
  const list = (level: SaAudience, key: SaListKey, rows: SaRow[], label: string) => `<div class="access-group"><span>${label}</span><div class="access-list">${rows.length ? rows.map(row =>
    `<label><input type="checkbox" data-access-pick="${key}" value="${esc(row.id)}" ${(level[key] || []).includes(row.id) ? 'checked' : ''}>${esc(row.name)}</label>`).join('')
    : '<span class="muted">None</span>'}</div></div>`;
  const picker = (levels: [SaKey, ...SaKey[]], title: string, hint: string, withEveryone: boolean) => {
    const level = draft[levels[0]];
    return `<fieldset class="access-level" data-access-levels="${levels.join(',')}"><legend>${esc(title)}</legend>${hint ? `<p class="access-hint">${esc(hint)}</p>` : ''}
      ${withEveryone ? `<label class="access-everyone"><input type="checkbox" data-access-everyone ${level.everyone ? 'checked' : ''}> Everyone</label>` : ''}
      <div data-access-lists ${level.everyone ? 'hidden' : ''}>${list(level, 'people', people.map(p => ({id: p.id, name: p.name || p.id})), 'Humans')}
      ${teams.length ? list(level, 'teams', teams, 'Groups') : ''}${bots.length ? list(level, 'bots', bots.map(b => ({id: b.id, name: b.display_name || b.slug})), 'Bots') : ''}</div></fieldset>`;
  };
  const paintPickers = () => {
    const host = dialog.querySelector('[data-access-pickers]');
    host.innerHTML = singleAudience ? picker(['read'], 'Who can see it', '', true) : preset === 'open' ? ''
      : preset === 'requests' ? picker(['read'], 'Who can read its work', '', false)
      : preset === 'private' ? picker(['see', 'read', 'write'], 'Who can use it', '', false)
      : (levels ?? []).map(([key, label, what]) => picker([key], label, what, true)).join('');
  };
  dialog.innerHTML = `<form><div class="tmodal-head"><h2 id="access-editor-title">${singleAudience ? 'Who can see it' : 'Access to ' + esc(name)}</h2><span class="spacer"></span><button class="ghost" type="button" data-access-close aria-label="Close">✕</button></div>
    <div class="access-body">
      <fieldset class="access-presets" ${singleAudience ? 'hidden' : ''}><legend>Access</legend>${presets.map(([value, label, hint]) =>
        `<label><input type="radio" name="preset" value="${value}" ${value === preset ? 'checked' : ''}><span>${esc(label)}<small>${esc(hint)}</small></span></label>`).join('')}</fieldset>
      <div data-access-pickers></div></div>
    <div class="row access-foot"><button class="primary" type="submit">${stage ? 'Done' : 'Save'}</button><button class="ghost" type="button" data-access-close>Cancel</button><span class="muted" data-access-status role="status"></span></div></form>`;
  paintPickers();
  const form = dialog.querySelector('form'), status = dialog.querySelector('[data-access-status]');
  dialog.querySelectorAll('[data-access-close]').forEach((button: HTMLElement) => button.onclick = () => settingsPanelClose(dialog));
  // Read whatever the boxes on screen say back into the draft before the preset changes or the form is saved.
  const collect = () => dialog.querySelectorAll('.access-level').forEach((box: HTMLElement) => {
    const level = box.querySelector<HTMLInputElement>('[data-access-everyone]')?.checked ? accessEveryone() : {
      everyone: false,
      ...Object.fromEntries(['people', 'teams', 'bots'].map(key => [key, [...box.querySelectorAll<HTMLInputElement>(`[data-access-pick="${key}"]:checked`)].map(input => input.value)]))};
    (String(box.dataset.accessLevels).split(',') as SaKey[]).forEach(key => { draft[key] = {...level}; });
  });
  form.onchange = (event: Event) => {
    const target = eventElement(event);
    if (!target) return;
    const radio = target.closest<HTMLInputElement>('[name=preset]');
    if (radio) {
      collect();
      const chosen = chosenOf();
      if (radio.value === 'open') ACCESS_LEVELS.forEach(([key]) => { draft[key] = accessEveryone(); });
      else if (radio.value === 'requests') { draft.see = accessEveryone(); draft.write = accessEveryone(); draft.read = chosen; }
      else if (radio.value === 'private') ACCESS_LEVELS.forEach(([key]) => { draft[key] = {...chosen}; });
      else ACCESS_LEVELS.forEach(([key]) => { draft[key] = draft[key] || accessEmpty(); });
      preset = radio.value; paintPickers(); return;
    }
    const every = target.closest<HTMLInputElement>('[data-access-everyone]');
    if (every) {
      const box = every.closest<HTMLElement>('.access-level');
      if (!box) return;
      const lists = box.querySelector<HTMLElement>('[data-access-lists]');
      if (lists) lists.hidden = every.checked;
    }
  };
  form.onsubmit = async (event: SubmitEvent) => {
    event.preventDefault();
    collect();
    if (stage) {
      const policy: SaPolicy = {see: draft.see, read: draft.read, write: draft.write};
      stage(async rev => (await put(`${path}/access`, {...policy, revision: rev}))?.revision ?? rev + 1, policy);
      settingsPanelClose(dialog); return;
    }
    const submit = form.querySelector('[type=submit]'); submit.disabled = true; status.textContent = 'Saving…';
    try {
      const saved = save ? await save(draft.read) : await put(`${path}/access`, {see: draft.see, read: draft.read, write: draft.write, revision: data.revision});
      settingsPanelClose(dialog); if (after) await after(saved); toast('Saved');
    } catch (error) { status.innerHTML = `<span class="err">${esc(errorMessage(error, ''))}</span>`; submit.disabled = false; }
  };
  dialog.onclose = () => { dialog.innerHTML = ''; };
  settingsPanelShow(dialog);
}
