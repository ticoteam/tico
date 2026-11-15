/* ui/app/settings-access-edit.ts — Settings' access editing: a bot's owners and access, the access cell and the mail summary.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   Moved from ui/app/settings-access.ts, whose other code Home runs, into the settings page part (ui/parts.json): only that part
   calls these, so a cold open of Home does not download them (P05b). */
'use strict';

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
  dialog.innerHTML = `<form><div class="tmodal-head"><h2 id="bot-owners-title">Who owns ${esc(e.display_name)}</h2><span class="spacer"></span><button class="ghost" type="button" data-owners-close aria-label="Close"><span class="nav-icon" aria-hidden="true">close</span></button></div>
    <div class="owner-picker-body">${e.owner_id ? `<p class="muted">${esc(settingsPersonName(e.owner_id))} is responsible for this bot and always stays an owner.</p>` : ''}
      <div class="owner-options">${people.map(person => `<label class="owner-option"><input type="checkbox" name="owner" value="${esc(person.id)}" ${listed.has(person.id) || person.id === e.owner_id ? 'checked' : ''} ${person.id === e.owner_id ? 'disabled' : ''}>${personAvatar(person, 22)}<span>${esc(person.name || person.id)}</span></label>`).join('')}</div>
      <div class="row"><button class="ghost" type="button" data-owners-close>Cancel</button><span class="muted" data-owners-status role="status"></span><button class="primary settings-go" type="submit">${stage ? 'Done' : 'Save'}</button></div></div></form>`;
  for (const button of dialog.querySelectorAll('[data-owners-close]') as NodeListOf<HTMLElement>) button.onclick = () => settingsPanelClose(dialog);
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
async function settingsMailSummary(root: ParentNode, slug: string) {
  const cell = root.querySelector(`[data-mail-summary="${CSS.escape(slug)}"]`);
  if (!cell) return;
  try {
    const m = await get<SaMailSettings>(`/v2/bots/${encodeURIComponent(slug)}/mail-settings`);
    cell.textContent = mailSettingText(m);
  } catch { cell.textContent = ''; }
}
async function settingsEditAccess(slug: string, {stage}: {stage?: SaStage | undefined} = {}) {
  const e = S.emps.find(row => row.id === slug);
  if (!e) return;
  return accessEditor({path: `/v2/bots/${encodeURIComponent(slug)}`, name: e.display_name, levels: ACCESS_LEVELS,
    presets: ACCESS_PRESETS, skipBot: slug, stage, panel: true, after: () => loadSettings()});
}
