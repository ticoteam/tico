/* ui/app/settings-bot-editor.js — The bot editor dialog, owners, and model/computer transitions
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// What the bot editor shows for an existing bot beyond its definition: who may use it, who it works for and who
// owns it (each opens its own dialog), and its model, fallback and computer (each applies as soon as it is picked).
function settingsBotEditorRows(e) {
  const chips = list => (list || []).map(o => `<span class="pchip">${personCircle(o.name || o.id, 16)}<span>${esc(firstName(o.name) || o.id)}</span></span>`).join('');
  const manage = settingsCanManageBot(e);
  const change = (attr, what) => manage ? `<button class="ghost" type="button" ${attr}="${esc(e.name)}" aria-label="Change ${what} for ${esc(e.display_name)}">Change</button>` : '<span></span>';
  return `${manage ? `<div class="sb-row"><span>Instructions</span><button class="ghost" type="button" data-edit-instructions="${esc(e.name)}">Edit Instructions</button></div>` : ''}<div class="sb-row"><span>Access</span><span data-access-summary>${esc(accessSummary(e.access_policy))}</span>${change('data-edit-access', 'access')}</div>
    <div class="sb-row"><span>Works for</span><div class="settings-owner-list">${chips(e.users) || '<span class="muted">Nobody</span>'}</div>${change('data-edit-owners', 'who it works for')}</div>
    <div class="sb-row"><span>Owners</span><div class="settings-owner-list" data-bot-owners>${chips(e.bot_owners) || '<span class="muted">Its owner</span>'}</div>${change('data-edit-bot-owners', 'owners')}</div>
    ${manage ? `<div class="sb-row"><span>Mail sending</span><span class="muted" data-mail-summary="${esc(e.name)}">…</span>${change('data-edit-mail', 'mail sending')}</div>` : ''}
    ${e.agent ? `<div class="sb-row"><span>Computer</span>${settingsAgentCell(e)}</div>`
      : `<div class="sb-row"><span>Model</span>${settingsChoiceCombo(e, 'model')}<span></span></div>
    <div class="sb-sub" data-bot-sub-line hidden></div>
    <div class="sb-row"><span>Fallback</span>${settingsChoiceCombo(e, 'fallback')}<span></span></div>
    <div class="sb-row"><span>Computer</span>${settingsMachineSelect(e)}<span></span></div>
    <div class="sb-row" data-bot-sub-row hidden><span>Subscription</span><select class="settings-inline-select" data-bot-sub="${esc(e.name)}" aria-label="Subscription for ${esc(e.display_name)}"></select><span></span></div>`}`;
}
function settingsEditBot(slug = '') {
  const editing = !!slug, e = editing ? S.emps.find(row => row.name === slug) : null;
  const dialog = $('#bot-editor');
  if (!dialog || editing && !e) return;
  if (e?.shared_from) return settingsEditBranch(e);
  SETTINGS_KEEP_MODEL = e?.model || '';
  // A new bot starts on the team default; nothing here names a vendor.
  const enabledModels = SETTINGS_DATA.models.filter(model => !model.deprecated && model.provider &&
    (SETTINGS_DATA.enabledProviders || []).includes(model.provider));
  const fallbackModel = SETTINGS_DATA.models.find(model => model.id === SETTINGS_DATA.defaultModel && !model.deprecated)
    || enabledModels[0] || SETTINGS_DATA.models[0] || {};
  const effort = e?.reasoning_effort || e?.effort || fallbackModel.default_effort || 'high';
  const defaultHarness = e?.harness || fallbackModel.harnesses?.[0] || fallbackModel.runtime || '';
  const currentModel = settingsChoiceValue(defaultHarness, e?.model || fallbackModel.id, effort);
  const parentOptions = S.emps.filter(row => row.name !== slug &&
    (S.me?.role === 'owner' || settingsCanManageBot(row))).map(row =>
    `<option value="${esc(row.name)}" ${e?.reports_to === row.name ? 'selected' : ''}>${esc(row.display_name || row.name)}</option>`).join('');
  const operator = e?.operator || S.me?.id || SETTINGS_DATA.people[0]?.id || '';
  // A member's bot goes on a computer an admin has opened to members' bots (the server refuses the rest).
  const machineOptions = SETTINGS_DATA.machines.filter(machine => !machine.revoked_at &&
    (settingsIsAdmin() || machine.accepts_member_bots)).map(machine =>
    `<option value="${esc(machine.id)}" data-operator="${esc(machine.operator)}">${esc(machine.label)} · ${esc(settingsPersonName(machine.operator))}</option>`).join('');
  dialog.innerHTML = `<form><div class="tmodal-head"><h2 id="bot-editor-title">${editing ? `Edit ${esc(e.display_name)}` : 'Add bot'}</h2><span class="spacer"></span><button class="ghost" type="button" data-bot-close aria-label="Close">✕</button></div>
    <div class="bot-editor-body">
      <div class="bot-editor-grid">
        <label>Bot slug<input name="slug" type="text" autocomplete="off" spellcheck="false" value="${esc(slug)}" placeholder="release-captain" pattern="[a-z0-9]+(?:-[a-z0-9]+)*" maxlength="80" ${editing ? 'readonly' : 'required'}></label>
        <label>Display name<input name="display_name" type="text" autocomplete="off" value="${esc(e?.display_name || '')}" placeholder="Release Captain" maxlength="100" required></label>
        <label class="bot-editor-wide">Description<textarea name="description" maxlength="2000" placeholder="What this bot owns and does">${esc(e?.description || '')}</textarea></label>
        <label>Reports to<select name="reports_to"><option value="">Top level</option>${parentOptions}</select></label>
        <label>Other bots<select name="bot_contact"><option value="open" ${(e?.bot_contact || 'open') === 'open' ? 'selected' : ''}>May chat and assign</option><option value="replies" ${e?.bot_contact === 'replies' ? 'selected' : ''}>Replies only</option><option value="tasks" ${e?.bot_contact === 'tasks' ? 'selected' : ''}>Tasks only</option></select><small>Applies to bots only. Humans are never affected.</small></label>
        <label>Status<select name="status">${['planned','active','paused'].map(value => `<option value="${value}" ${(e?.status || 'planned') === value ? 'selected' : ''}>${value === 'planned' ? 'Setting up' : value[0].toUpperCase() + value.slice(1)}</option>`).join('')}</select></label>
        <label>Repository<input name="repo" type="text" autocomplete="off" spellcheck="false" value="${esc(e?.repo || (slug ? `bot-${slug}` : ''))}" placeholder="bot-release-captain" maxlength="200" required></label>
        ${editing ? '<fieldset class="bot-editor-wide brepo" data-bot-repos hidden></fieldset>' : ''}
        <label class="bot-editor-check"><input type="checkbox" name="shared" ${e?.shared ? 'checked' : ''}> Allow branches</label>
        <label class="bot-editor-check"><input type="checkbox" name="temp" ${e?.temp ? 'checked' : ''}> Temp bot</label>
        <label class="bot-editor-wide"><span><input type="checkbox" name="private_tasks_default" ${e?.private_tasks_default ? 'checked' : ''}> Create private tasks by default</span><small>Tasks created by or assigned to this bot start private. A human requester can choose company visibility.</small></label>
        <label>Conversation<select name="thread_mode"><option value="personal" ${(e?.thread_mode || 'personal') === 'personal' ? 'selected' : ''}>Private per human</option><option value="shared" ${e?.thread_mode === 'shared' ? 'selected' : ''}>Shared room</option></select></label>
        ${editing ? '<div class="bot-editor-wide sb-rows" data-bot-people></div>' : ''}
        ${editing ? '' : `<div class="bot-editor-wide" data-add-choice>
          ${settingsChoiceFields(currentModel)}
          <input type="hidden" name="model_effort" value="${esc(currentModel)}">
        </div>
        <label>Computer owner<select name="operator" ${S.me?.role === 'owner' ? '' : 'disabled'}>${SETTINGS_DATA.people.map(person => `<option value="${esc(person.id)}" ${person.id === operator ? 'selected' : ''}>${esc(person.name || person.id)}</option>`).join('')}</select></label>
        <label class="bot-editor-wide">Registered computer<select name="runner_id"><option value="">Assign later</option>${machineOptions}</select><small data-computer-note></small></label>
        <p class="bot-editor-wide muted">Everyone can use it. Change that under Access once it is added.</p>`}
      </div>
      <div class="row" style="margin-top:16px"><button class="primary" type="submit">${editing ? 'Save bot' : 'Add bot'}</button><button class="ghost" type="button" data-bot-close>Cancel</button><span class="muted" data-bot-status></span>${editing && !isBuiltInBot(e.name) ? `<span class="spacer"></span><select name="successor" aria-label="Hand its work to"><option value="">Hand its work to ${esc(settingsPersonName(e.operator))}</option>${S.emps.filter(row => row.name !== slug).map(row => `<option value="${esc(row.name)}">Hand its work to ${esc(row.display_name || row.name)}</option>`).join('')}</select>${['hermes', 'openclaw'].includes(e.agent?.harness) ? `<label class="bot-editor-check" title="Its agent stops either way; revoking makes it stop reporting in"><input type="checkbox" name="revoke_agent" checked> Revoke its credential</label>` : ''}<button class="ghost danger" type="button" data-bot-remove>Remove bot</button>` : ''}</div></div></form>`;
  const form = dialog.querySelector('form'), status = dialog.querySelector('[data-bot-status]');
  dialog.querySelectorAll('[data-bot-close]').forEach(button => button.onclick = () => dialog.close());
  // The rows for who owns it, its model and its computer: they save on their own, so keep them fresh, and
  // once one has changed the bot use the newer revision for Save.
  let rev = e?.revision, touched = false;
  const rows = dialog.querySelector('[data-bot-people]');
  if (rows) {
    const paint = () => { rows.innerHTML = settingsBotEditorRows(S.emps.find(row => row.name === slug) || e); settingsWireCombos(rows); void subsBotMount(rows, slug); void settingsMailSummary(rows, slug); };
    const refresh = () => { if (!dialog.open) return; if (touched) rev = (S.emps.find(row => row.name === slug) || e).revision; paint(); };
    paint();
    document.addEventListener('tico:settings-loaded', refresh);
    dialog.addEventListener('close', () => document.removeEventListener('tico:settings-loaded', refresh), {once: true});
    rows.addEventListener('click', event => {
      touched = true;
      const access = event.target.closest('[data-edit-access]'), owners = event.target.closest('[data-edit-owners]'), botOwners = event.target.closest('[data-edit-bot-owners]');
      if (access) void settingsEditAccess(access.dataset.editAccess);
      if (owners) settingsEditOwners(owners.dataset.editOwners);
      if (botOwners) void settingsEditBotOwners(botOwners.dataset.editBotOwners);
      const mail = event.target.closest('[data-edit-mail]');
      if (mail) void settingsEditMail(mail.dataset.editMail, () => settingsMailSummary(rows, slug));
      const credential = event.target.closest('[data-agent-credential]'), revoke = event.target.closest('[data-agent-revoke]'), pairing = event.target.closest('[data-agent-pair]');
      if (pairing) void settingsAgentPair(pairing.dataset.agentPair);
      if (credential) void settingsAgentCredential(credential.dataset.agentCredential);
      if (revoke) void settingsAgentRevoke(revoke.dataset.agentRevoke);
    });
    rows.addEventListener('change', event => {
      const machine = event.target.closest('[data-bot-machine]');
      if (machine) { touched = true; void settingsMoveBot(machine); }
    });
  }
  void botReposMount(dialog.querySelector('[data-bot-repos]'), slug);   // ui/app/settings-repositories.js
  const remove = dialog.querySelector('[data-bot-remove]');
  if (remove) remove.onclick = async () => {
    // Remove = archive (#535): off the chart, no routines or new work; open tasks go to the picked heir.
    const successor = form.elements.successor.value;
    const hermes = ['hermes', 'openclaw'].includes(e.agent?.harness);
    remove.disabled = true; status.textContent = 'Removing…';
    try {
      const done = await post(`/v2/bots/${encodeURIComponent(slug)}/archive`, {successor: successor || null, expected_revision: e.revision,
        ...(hermes ? {revoke_agent: !!form.elements.revoke_agent?.checked} : {})});
      SETTINGS_ARCHIVED = null;
      dialog.close(); await loadSettings(); settingsShow('bots');
      toast(done.agent?.detail || `Removed ${e.display_name}`);
    } catch (error) { status.innerHTML = `<span class="err">${esc(error.message)}</span>`; remove.disabled = false; }
  };
  if (!editing) {
    const slugInput = form.elements.slug, repo = form.elements.repo;
    let repoEdited = false, operatorEdited = false;
    repo.addEventListener('input', () => { repoEdited = true; });
    slugInput.addEventListener('input', () => { if (!repoEdited) repo.value = slugInput.value ? `bot-${slugInput.value}` : ''; });
    form.elements.operator.addEventListener('change', () => { operatorEdited = true; });
    form.elements.reports_to.addEventListener('change', event => {
      const parent = S.emps.find(row => row.name === event.target.value);
      if (!operatorEdited && !form.elements.runner_id.value && parent?.operator) form.elements.operator.value = parent.operator;
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
    let added = null;
    try {
      if (editing) {
        await post(`/v2/bots/${encodeURIComponent(slug)}/definition`, {
          display_name: form.elements.display_name.value, description: form.elements.description.value,
          reports_to: form.elements.reports_to.value || null, status: form.elements.status.value,
          bot_contact: form.elements.bot_contact.value,
          private_tasks_default: form.elements.private_tasks_default.checked,
          repo: form.elements.repo.value, thread_mode: form.elements.thread_mode.value,
          temp: form.elements.temp.checked, shared: form.elements.shared.checked, expected_revision: rev});
      } else {
        const choice = settingsChoiceFromValue(form.elements.model_effort.value);
        if (!choice.harness || !choice.model || !choice.effort) throw new Error('Choose a harness, model and effort.');
        added = await post('/v2/bots', {slug: form.elements.slug.value, display_name: form.elements.display_name.value,
          description: form.elements.description.value, reports_to: form.elements.reports_to.value || null,
          status: form.elements.status.value, repo: form.elements.repo.value,
          private_tasks_default: form.elements.private_tasks_default.checked,
          thread_mode: form.elements.thread_mode.value, shared: form.elements.shared.checked, model: choice.model, effort: choice.effort,
          harness: choice.harness, operator: form.elements.operator.value || S.me?.id,
          runner_id: form.elements.runner_id.value || null});
      }
      dialog.close(); await loadSettings(); settingsShow('bots');
      if (editing && BOT?.slug === slug) void botBranchesLoad(slug);   // Allow branches shows the picker without a reload
      toast(editing ? `Updated ${form.elements.display_name.value}` : added?.note || `Added ${form.elements.display_name.value}`);
    } catch (error) { status.innerHTML = `<span class="err">${esc(error.message)}</span>`; submit.disabled = false; }
  };
  dialog.onclose = () => { dialog.innerHTML = ''; };
  dialog.showModal();
}
function settingsEditOwners(slug) {
  const e = S.emps.find(row => row.name === slug), dialog = $('#owner-picker');
  if (!e || !dialog) return;
  const selected = new Set((e.users || []).map(person => person.id));
  dialog.innerHTML = `<form><div class="tmodal-head"><h2 id="owner-picker-title">Who ${esc(e.display_name)} works for</h2><span class="spacer"></span><button class="ghost" type="button" data-owner-close aria-label="Close">✕</button></div>
    <div class="owner-picker-body">
      <div class="owner-options">${SETTINGS_DATA.people.map(person => `<label class="owner-option"><input type="checkbox" name="owner" value="${esc(person.id)}" ${selected.has(person.id) ? 'checked' : ''}>${personAvatar(person, 22)}<span>${esc(person.name || person.id)}</span></label>`).join('')}</div>
      <div class="row"><button class="primary" type="submit">Save</button><button class="ghost" type="button" data-owner-close>Cancel</button><span id="owner-picker-status" class="muted"></span></div></div></form>`;
  dialog.querySelectorAll('[data-owner-close]').forEach(button => button.onclick = () => dialog.close());
  dialog.querySelector('form').onsubmit = async event => {
    event.preventDefault();
    const owners = [...dialog.querySelectorAll('input[name=owner]:checked')].map(input => input.value);
    const status = $('#owner-picker-status', dialog);
    if (!owners.length) { status.innerHTML = '<span class="err">Choose at least one human.</span>'; return; }
    dialog.querySelectorAll('button,input').forEach(control => control.disabled = true); status.textContent = 'Saving…';
    try {
      await post(`/v2/bots/${encodeURIComponent(slug)}/owners`, {owners, expected_revision: e.revision});
      dialog.close(); await loadSettings(); toast(`Updated who ${e.display_name} works for`);
    } catch (error) {
      status.innerHTML = `<span class="err">${esc(error.message)}</span>`;
      dialog.querySelectorAll('button,input').forEach(control => control.disabled = false);
    }
  };
  dialog.onclose = () => { dialog.innerHTML = ''; };
  dialog.showModal();
}
async function settingsMoveBot(select) {
  const slug = select.dataset.botMachine, e = S.emps.find(row => row.name === slug);
  const current = select.dataset.current || '', machine = SETTINGS_DATA.machines.find(row => row.id === select.value);
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
    const transition = await post(`/v2/bots/${encodeURIComponent(e.name)}/transitions`, body);
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
      if (!confirm(`Change without a prepared checkpoint? Raw conversation history stays in ${appName()}, but persistent session context will be lost.`)) return;
      button.currentTarget.disabled = true;
      try { await post(`/v2/settings/transitions/${encodeURIComponent(id)}/apply-without-checkpoint`, {change_without_checkpoint:true}); await paint(); }
      catch (error) {toast(error.message, true); button.currentTarget.disabled = false;}
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
  void paint(); SETTINGS_TRANSITION_TIMER = setInterval(paint, 2000);
}

function settingsEditInstructions(slug) {
  const bot = S.emps.find(row => row.name === slug);
  if (!bot || bot.shared_from || !settingsCanManageBot(bot)) return;
  const dialog = document.createElement('dialog'); dialog.className = 'tmodal';
  dialog.innerHTML = `<form><header><h2>Edit Instructions</h2><button class="ghost" type="button" data-close>Close</button></header>
    <label>Changes for ${esc(bot.display_name)}<textarea name="changes" required rows="6" aria-label="Instructions changes"></textarea></label>
    <p class="muted">BotOps updates AGENT.md on the bot's computer.</p>
    <button class="primary" type="submit">Ask BotOps to change</button><p class="err" data-error></p></form>`;
  dialog.querySelector('[data-close]').onclick = () => dialog.close();
  dialog.onclose = () => dialog.remove();
  dialog.querySelector('form').onsubmit = async event => {
    event.preventDefault();
    const form = event.target, changes = form.elements.changes.value.trim(), button = form.querySelector('[type=submit]');
    if (!changes) return;
    button.disabled = true;
    try {
      await post('/v2/chat/botops', {text: `Update the Instructions (AGENT.md) for ${bot.display_name} [${slug}]. Apply these changes and verify the file on its computer:\n\n${changes}`});
      dialog.close(); location.hash = '#/bot/botops/chat'; toast('Instructions changes sent to BotOps');
    } catch (error) { form.querySelector('[data-error]').textContent = error.message; button.disabled = false; }
  };
  document.body.appendChild(dialog); dialog.showModal(); dialog.querySelector('textarea').focus();
}
document.addEventListener('click', event => {
  const button = event.target.closest('[data-edit-instructions]');
  if (button) settingsEditInstructions(button.dataset.editInstructions);
});
