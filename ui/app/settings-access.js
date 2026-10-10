/* ui/app/settings-access.js — Who may see, read and write a bot: the access editor
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----- Access: who may see, read and write to a bot (backend/bot_access.py, docs/permissions.md).
// Each level is everyone, or humans, groups and bots. Open is all three Everyone; the owner, the bot
// itself and the humans it reports up to always have all three.
const ACCESS_LEVELS = [
  ['see', 'See', 'the bot in the team chart and lists: its name, role, who owns it and who it reports to'],
  ['read', 'Read', 'its work: tasks, updates, files, status, run log and routines'],
  ['write', 'Write', 'send it messages, tasks, notes and comments'],
];
const ACCESS_PRESETS = [
  ['open', 'Open', 'Everyone'],
  ['requests', 'Requests only', 'Everyone sends requests; chosen humans read its work'],
  ['private', 'Private', 'Chosen humans only'],
  ['custom', 'Custom', 'See, read and write separately'],
];
const accessTeamName = id => (S.orgGroups || []).find(g => g.id === id)?.name || teamLabel(id);
const accessEmpty = () => ({everyone: false, people: [], teams: [], bots: []});
const accessEveryone = () => ({everyone: true, people: [], teams: [], bots: []});
function accessLevelText(level) {
  if (!level || level.everyone) return 'Everyone';
  const names = [...(level.teams || []).map(accessTeamName), ...(level.people || []).map(id => firstName(settingsPersonName(id)) || id),
    ...(level.bots || []).map(settingsBotName)];
  if (!names.length) return 'Managers only';
  return names.length > 3 ? `${names.slice(0, 2).join(', ')} +${names.length - 2}` : names.join(', ');
}
function accessSummary(policy) {
  if (!policy) return 'Open';
  if (ACCESS_LEVELS.every(([key]) => policy[key]?.everyone)) return 'Open';
  return ACCESS_LEVELS.map(([key, label]) => `${label}: ${accessLevelText(policy[key])}`).join(' · ');
}
// One word for the table row; the editor shows the full sentence (accessSummary).
const ACCESS_WORD = {open: 'Open', requests: 'Requests only', private: 'Private', custom: 'Custom'};
function settingsAccessWord(e) {
  const mine = e.my_access || {see: true, read: true, write: true};
  return e.access_policy ? ACCESS_WORD[accessPresetOf(e.access_policy)] || 'Custom'
    : `You: ${ACCESS_LEVELS.filter(([key]) => mine[key]).map(([, label]) => label).join(' · ') || 'See'}`;
}
function settingsAccessCell(e) {
  const word = settingsAccessWord(e);
  return `<span class="sb-access" data-access-summary title="${esc(e.access_policy ? accessSummary(e.access_policy) : word)}">${esc(word)}</span>`;
}
// Who owns a bot: its creator and co-owners (and its operator). Whoever it reports up to and the admins own it too,
// without being listed. Any owner adds or removes humans here; the server checks it (docs/permissions.md).
async function settingsEditBotOwners(slug) {
  const e = S.emps.find(row => row.name === slug);
  if (!e) return;
  let dialog = $('#bot-owners-editor');
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.className = 'owner-picker'; dialog.id = 'bot-owners-editor'; dialog.setAttribute('aria-labelledby', 'bot-owners-title');
    document.body.append(dialog);
  }
  const listed = new Set((e.bot_owners || []).map(o => o.id));
  const people = SETTINGS_DATA.people?.length ? SETTINGS_DATA.people : (S.people || []).filter(p => !p.hidden);
  dialog.innerHTML = `<form><div class="tmodal-head"><h2 id="bot-owners-title">Who owns ${esc(e.display_name)}</h2><span class="spacer"></span><button class="ghost" type="button" data-owners-close aria-label="Close">✕</button></div>
    <div class="owner-picker-body">${e.operator ? `<p class="muted">${esc(settingsPersonName(e.operator))} is responsible for this bot and always stays an owner.</p>` : ''}
      <div class="owner-options">${people.map(person => `<label class="owner-option"><input type="checkbox" name="owner" value="${esc(person.id)}" ${listed.has(person.id) || person.id === e.operator ? 'checked' : ''} ${person.id === e.operator ? 'disabled' : ''}>${personAvatar(person, 22)}<span>${esc(person.name || person.id)}</span></label>`).join('')}</div>
      <div class="row"><button class="primary" type="submit">Save</button><button class="ghost" type="button" data-owners-close>Cancel</button><span class="muted" data-owners-status role="status"></span></div></div></form>`;
  dialog.querySelectorAll('[data-owners-close]').forEach(button => button.onclick = () => dialog.close());
  dialog.querySelector('form').onsubmit = async event => {
    event.preventDefault();
    const chosen = new Set([...dialog.querySelectorAll('input[name=owner]:checked:not(:disabled)')].map(input => input.value));
    const add = [...chosen].filter(id => !listed.has(id) && id !== e.operator), remove = [...listed].filter(id => !chosen.has(id) && id !== e.operator);
    const status = dialog.querySelector('[data-owners-status]');
    if (!add.length && !remove.length) { dialog.close(); return; }
    dialog.querySelectorAll('button,input').forEach(control => { control.disabled = true; }); status.textContent = 'Saving…';
    try {
      await post(`/v2/bots/${encodeURIComponent(slug)}/co-owners`, {add, remove});
      dialog.close(); await loadSettings(); toast(`Updated who owns ${e.display_name}`);
    } catch (error) {
      status.innerHTML = `<span class="err">${esc(error.message)}</span>`;
      dialog.querySelectorAll('button,input').forEach(control => { control.disabled = false; });
      dialog.querySelectorAll('input[name=owner][value]').forEach(input => { if (input.value === e.operator) input.disabled = true; });
    }
  };
  dialog.onclose = () => { dialog.innerHTML = ''; };
  dialog.showModal();
}
// Mail sending (backend/mail_settings.py, docs/mail.md "Turning sending on"): whether a bot sends without a
// per-message approval and its forward targets. Held in Tico and set by a person who manages the bot; bot.yaml only asks.
function mailSettingText(m) {
  if (!m.set) return 'Off: no one has turned it on';
  return m.outbound_send ? `On${m.forward_to.length ? ` · forwards to ${m.forward_to.join(', ')}` : ''}` : 'Off';
}
async function settingsMailSummary(root, slug) {
  const cell = root.querySelector(`[data-mail-summary="${CSS.escape(slug)}"]`);
  if (!cell) return;
  try {
    const m = await get(`/v2/bots/${encodeURIComponent(slug)}/mail-settings`);
    cell.textContent = mailSettingText(m) + (m.unapproved ? ' · bot.yaml asks for more' : '');
  } catch { cell.textContent = ''; }
}
async function settingsEditMail(slug, after) {
  const e = S.emps.find(row => row.name === slug);
  if (!e) return;
  let dialog = $('#bot-mail-editor');
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.className = 'owner-picker'; dialog.id = 'bot-mail-editor'; dialog.setAttribute('aria-labelledby', 'bot-mail-title');
    document.body.append(dialog);
  }
  let m;
  try { m = await get(`/v2/bots/${encodeURIComponent(slug)}/mail-settings`); } catch (error) { toast(error.message); return; }
  const asks = m.requested ? `bot.yaml asks: ${m.requested.outbound_send ? 'send without approval' : 'no sending'}${m.requested.forward_to.length ? `, forward to ${m.requested.forward_to.join(', ')}` : ''}.` : 'bot.yaml asks for nothing.';
  dialog.innerHTML = `<form><div class="tmodal-head"><h2 id="bot-mail-title">Mail sending for ${esc(e.display_name)}</h2><span class="spacer"></span><button class="ghost" type="button" data-mail-close aria-label="Close">✕</button></div>
    <div class="owner-picker-body">
      <p class="muted">With sending on, ${esc(e.display_name)} mails people in the team, the sender of a thread it answers and the forward addresses below without asking each time. Everyone else still needs an approval. ${esc(asks)}</p>
      <label class="owner-option"><input type="checkbox" name="outbound_send" ${m.outbound_send ? 'checked' : ''}><span>Send without a per-message approval</span></label>
      <label>Forward addresses<input name="forward_to" type="text" autocomplete="off" spellcheck="false" value="${esc((m.forward_to || []).join(', '))}" placeholder="me@personal.example"></label>
      <div class="row"><button class="primary" type="submit">Save</button><button class="ghost" type="button" data-mail-close>Cancel</button><span class="muted" data-mail-status role="status"></span></div></div></form>`;
  dialog.querySelectorAll('[data-mail-close]').forEach(button => button.onclick = () => dialog.close());
  dialog.querySelector('form').onsubmit = async event => {
    event.preventDefault();
    const form = event.target, status = dialog.querySelector('[data-mail-status]');
    const forward_to = form.elements.forward_to.value.split(/[,;\s]+/).map(a => a.trim()).filter(Boolean);
    dialog.querySelectorAll('button,input').forEach(control => { control.disabled = true; }); status.textContent = 'Saving…';
    try {
      await post(`/v2/bots/${encodeURIComponent(slug)}/mail-settings`, {outbound_send: form.elements.outbound_send.checked, forward_to});
      dialog.close(); toast(`Updated mail sending for ${e.display_name}`); if (after) void after();
    } catch (error) {
      status.innerHTML = `<span class="err">${esc(error.message)}</span>`;
      dialog.querySelectorAll('button,input').forEach(control => { control.disabled = false; });
    }
  };
  dialog.onclose = () => { dialog.innerHTML = ''; };
  dialog.showModal();
}
// Which preset a stored policy is: Open, Visible-requests-only (See and Write Everyone, Read chosen),
// Private (all three the same chosen audience), else Custom.
function accessPresetOf(policy) {
  const same = (a, b) => JSON.stringify([!!a.everyone, a.people, a.teams, a.bots]) === JSON.stringify([!!b.everyone, b.people, b.teams, b.bots]);
  if (ACCESS_LEVELS.every(([key]) => policy[key].everyone)) return 'open';
  if (policy.see.everyone && policy.write.everyone && !policy.read.everyone) return 'requests';
  if (!policy.see.everyone && same(policy.see, policy.read) && same(policy.read, policy.write)) return 'private';
  return 'custom';
}
async function settingsEditAccess(slug) {
  const e = S.emps.find(row => row.name === slug);
  if (!e) return;
  let data;
  try { data = await get(`/v2/bots/${encodeURIComponent(slug)}/access`); }
  catch (error) { toast(error.message, true); return; }
  let dialog = $('#access-editor');
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.className = 'access-editor'; dialog.id = 'access-editor'; dialog.setAttribute('aria-labelledby', 'access-editor-title');
    document.body.append(dialog);
  }
  const people = (SETTINGS_DATA.people?.length ? SETTINGS_DATA.people : (S.people || []).filter(p => !p.hidden));
  const bots = S.emps.filter(row => row.name !== slug && row.status !== 'archived');
  const teams = data.teams || [];
  const draft = {see: {...data.see}, read: {...data.read}, write: {...data.write}};
  let preset = accessPresetOf(draft);
  // What the two "chosen" presets edit: the Read audience (requests only), or one audience for all three (Private).
  const chosenOf = () => {
    const pick = ['read', 'see', 'write'].map(key => draft[key]).find(level => !level.everyone) || accessEmpty();
    return {everyone: false, people: [...pick.people], teams: [...pick.teams], bots: [...pick.bots]};
  };
  const list = (level, key, rows, label) => `<div class="access-group"><span>${label}</span><div class="access-list">${rows.length ? rows.map(row =>
    `<label><input type="checkbox" data-access-pick="${key}" value="${esc(row.id)}" ${(level[key] || []).includes(row.id) ? 'checked' : ''}>${esc(row.name)}</label>`).join('')
    : '<span class="muted">None</span>'}</div></div>`;
  const picker = (levels, title, hint, withEveryone) => {
    const level = draft[levels[0]];
    return `<fieldset class="access-level" data-access-levels="${levels.join(',')}"><legend>${esc(title)}${hint ? `<small>${esc(hint)}</small>` : ''}</legend>
      ${withEveryone ? `<label class="access-everyone"><input type="checkbox" data-access-everyone ${level.everyone ? 'checked' : ''}> Everyone</label>` : ''}
      <div data-access-lists ${level.everyone ? 'hidden' : ''}>${list(level, 'people', people.map(p => ({id: p.id, name: p.name || p.id})), 'Humans')}
      ${teams.length ? list(level, 'teams', teams, 'Groups') : ''}${bots.length ? list(level, 'bots', bots.map(b => ({id: b.name, name: b.display_name || b.name})), 'Bots') : ''}</div></fieldset>`;
  };
  const paintPickers = () => {
    const host = dialog.querySelector('[data-access-pickers]');
    host.innerHTML = preset === 'open' ? ''
      : preset === 'requests' ? picker(['read'], 'Who can read its work', '', false)
      : preset === 'private' ? picker(['see', 'read', 'write'], 'Who can use it', '', false)
      : ACCESS_LEVELS.map(([key, label, what]) => picker([key], label, what, true)).join('');
  };
  dialog.innerHTML = `<form><div class="tmodal-head"><h2 id="access-editor-title">Access to ${esc(e.display_name)}</h2><span class="spacer"></span><button class="ghost" type="button" data-access-close aria-label="Close">✕</button></div>
    <div class="access-body">
      <fieldset class="access-presets"><legend>Access</legend>${ACCESS_PRESETS.map(([value, label, hint]) =>
        `<label><input type="radio" name="preset" value="${value}" ${value === preset ? 'checked' : ''}><span>${esc(label)}<small>${esc(hint)}</small></span></label>`).join('')}</fieldset>
      <div data-access-pickers></div>
      <div class="row"><button class="primary" type="submit">Save</button><button class="ghost" type="button" data-access-close>Cancel</button><span class="muted" data-access-status role="status"></span></div></div></form>`;
  paintPickers();
  const form = dialog.querySelector('form'), status = dialog.querySelector('[data-access-status]');
  dialog.querySelectorAll('[data-access-close]').forEach(button => button.onclick = () => dialog.close());
  // Read whatever the boxes on screen say back into the draft before the preset changes or the form is saved.
  const collect = () => dialog.querySelectorAll('.access-level').forEach(box => {
    const level = box.querySelector('[data-access-everyone]')?.checked ? accessEveryone() : {
      everyone: false,
      ...Object.fromEntries(['people', 'teams', 'bots'].map(key => [key, [...box.querySelectorAll(`[data-access-pick="${key}"]:checked`)].map(input => input.value)]))};
    box.dataset.accessLevels.split(',').forEach(key => { draft[key] = {...level}; });
  });
  form.onchange = event => {
    const radio = event.target.closest('[name=preset]');
    if (radio) {
      collect();
      const chosen = chosenOf();
      if (radio.value === 'open') ACCESS_LEVELS.forEach(([key]) => { draft[key] = accessEveryone(); });
      else if (radio.value === 'requests') { draft.see = accessEveryone(); draft.write = accessEveryone(); draft.read = chosen; }
      else if (radio.value === 'private') ACCESS_LEVELS.forEach(([key]) => { draft[key] = {...chosen}; });
      else ACCESS_LEVELS.forEach(([key]) => { draft[key] = draft[key] || accessEmpty(); });
      preset = radio.value; paintPickers(); return;
    }
    const every = event.target.closest('[data-access-everyone]');
    if (every) every.closest('.access-level').querySelector('[data-access-lists]').hidden = every.checked;
  };
  form.onsubmit = async event => {
    event.preventDefault();
    collect();
    const submit = form.querySelector('[type=submit]'); submit.disabled = true; status.textContent = 'Saving…';
    try {
      await put(`/v2/bots/${encodeURIComponent(slug)}/access`, {see: draft.see, read: draft.read, write: draft.write, revision: data.revision});
      dialog.close(); await loadSettings(); toast(`Updated access to ${e.display_name}`);
    } catch (error) { status.innerHTML = `<span class="err">${esc(error.message)}</span>`; submit.disabled = false; }
  };
  dialog.onclose = () => { dialog.innerHTML = ''; };
  dialog.showModal();
}
