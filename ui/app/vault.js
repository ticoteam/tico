/* ui/app/vault.js — Credential vault dialogs (reveal, add or edit, share, delete) for Library › Tools
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Credentials are listed as metadata; reveal is a separate authorized, audited request.
let VAULT = null, VAULT_REVEAL_TIMER = null, VAULT_DIALOG_GENERATION = 0;
function vaultClose() {
  VAULT_DIALOG_GENERATION++;
  clearTimeout(VAULT_REVEAL_TIMER);
  const dialog = $('#credential-dialog');
  if (!dialog) return;
  dialog.querySelectorAll('input,textarea').forEach(input => input.value = '');
  if (dialog.open) dialog.close();
  dialog.replaceChildren();
}
// Read the vault again after a change and let Library › Tools redraw what it shows of it (ui/app/redesign-library.js).
async function vaultLoad() {
  try { await vaultFetch(); }
  catch (error) { toast(error.message, true); }
  lcVaultChanged();
}
function vaultSubject(subject) {
  if (subject === 'computers') return 'Every computer (signs models in)';
  const [kind,id] = subject.split(':');
  if (kind === 'computer') {
    const computer = (VAULT.computers || []).find(row => row.id === id);
    return `${computer?.label || id} (computer)`;
  }
  const row = (kind === 'human' ? VAULT.people : VAULT.bots).find(row => row.id === id);
  return row?.name || row?.email || id;
}
async function vaultDelete(id) {
  try {
    await writeRequest('DELETE', `/v2/credentials/${encodeURIComponent(id)}`);
    vaultClose();
    await vaultLoad();
  } catch(error) {toast(error.message);}
}
function vaultDialog(title,body) {
  vaultClose();
  const dialog = $('#credential-dialog');
  dialog.innerHTML = `<header><h2>${esc(title)}</h2><button type="button" class="ghost tmodal-act" data-vault-close aria-label="Close credential">Close</button></header>${body}`;
  dialog.querySelector('[data-vault-close]').onclick = vaultClose;
  dialog.oncancel = event => {event.preventDefault();vaultClose();};
  dialog.showModal(); return dialog;
}
async function vaultReveal(id) {
  const row = VAULT.credentials.find(row => row.id === id);
  const dialog = vaultDialog(row.name,'<p class="muted">Loading…</p>');
  const generation = VAULT_DIALOG_GENERATION;
  try {
    const result = await post(`/v2/credentials/${encodeURIComponent(id)}/reveal`);
    if (!dialog.open || !dialog.isConnected || generation !== VAULT_DIALOG_GENERATION) return;
    dialog.querySelector('p').remove();
    const label = document.createElement('label'); label.textContent = 'Value';
    const value = document.createElement('textarea'); value.readOnly = true; value.value = result.value; value.setAttribute('aria-label','Value'); label.appendChild(value);dialog.appendChild(label);
    const copy = document.createElement('button');copy.type='button';copy.className='primary';copy.textContent='Copy';
    copy.onclick = async () => {try {await navigator.clipboard.writeText(value.value);toast('Copied');} catch {value.focus();value.select();toast('Select and copy the value shown.');}};
    dialog.appendChild(copy); VAULT_REVEAL_TIMER = setTimeout(vaultClose,60000);
  } catch(error) { if(dialog.open && generation === VAULT_DIALOG_GENERATION) dialog.querySelector('p').textContent=error.message; }
}
// `row` is a stored credential to edit, or for Add a template ({name, env, source}: a tool's variable) to start from.
function vaultEdit(row) {
  const editing = !!row?.id;
  const dialog = vaultDialog(editing ? 'Edit credential' : 'Add credential',`<form id="vault-form" class="bot-editor-grid">
    <label>Name<input name="name" type="text" required maxlength="150" autocomplete="off" value="${esc(row?.name || '')}"></label>
    <label>Username (optional)<input name="username" type="text" maxlength="250" value="${esc(row?.username || '')}" autocomplete="off" spellcheck="false"></label>
    <label>Type<select name="kind">${['api_key','password','token','file','connection'].map(kind=>`<option value="${kind}" ${row?.kind===kind?'selected':''}>${esc(kind.replace('_',' '))}</option>`).join('')}</select></label>
    <label>Bot variable name (optional)<input name="env" type="text" pattern="[A-Z_][A-Z0-9_]*" maxlength="100" autocomplete="off" spellcheck="false" placeholder="POSTHOG_API_KEY" value="${esc(row?.env || '')}"></label>
    <label class="bot-editor-wide">${row?.stored ? 'New value (blank keeps it)' : 'Value'}<textarea name="secret" aria-label="New value" autocomplete="off" spellcheck="false"></textarea></label>
    <p class="err bot-editor-wide" id="vault-error"></p>
    <button class="primary" type="submit">Save credential</button></form>`);
  dialog.querySelector('form').onsubmit = async event => {
    event.preventDefault(); const form=event.target,button=form.querySelector('[type=submit]');button.disabled=true;
    const data={name:form.elements.name.value,username:form.elements.username.value,kind:form.elements.kind.value,env:form.elements.env.value,source:row?.source || ''};
    if(form.elements.secret.value)data.secret=form.elements.secret.value;
    if(editing)data.expected_revision=row.revision;
    try {await post('/v2/credentials'+(editing?'/'+encodeURIComponent(row.id):''),data);vaultClose();await vaultLoad();}
    catch(error){$('#vault-error').textContent=error.message;button.disabled=false;}
  };
}
function vaultShare(id) {
  const row=VAULT.credentials.find(row=>row.id===id);
  const modelKey=['OPENAI_API_KEY','ANTHROPIC_API_KEY','CLAUDE_CODE_OAUTH_TOKEN','CURSOR_API_KEY'].includes(row.env)&&['api_key','token'].includes(row.kind)&&row.stored;
  const choices=[...(VAULT.can_manage&&modelKey?[{subject:'computers',label:'Every computer (signs models in)'}]:[]),
    ...(VAULT.can_manage ? VAULT.people.map(person=>({subject:'human:'+person.id,label:person.name || person.email})) : []),
    ...VAULT.bots.map(bot=>({subject:'bot:'+bot.id,label:bot.name+' (bot)'}))];
  const dialog=vaultDialog('Access to '+row.name,`<p class="muted">Revoking access also revokes the access they gave their bots.</p>
    <div>${row.grants.map(grant=>`<div class="row"><span>${esc(vaultSubject(grant.subject))}${grant.parent_id?' · delegated':''}</span>${grant.can_revoke?`<button class="ghost" type="button" data-revoke="${esc(grant.id)}">Revoke</button>`:''}</div>`).join('') || '<p>Owners only.</p>'}</div>
    <form id="vault-grant-form"><label>Grant access to<select name="subject" required>${choices.map(choice=>`<option value="${esc(choice.subject)}">${esc(choice.label)}</option>`).join('')}</select></label>
    <button class="primary" type="submit" ${choices.length?'':'disabled'}>Grant access</button><p class="err" id="vault-error"></p></form>`);
  dialog.querySelector('form').onsubmit=async event=>{event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;
    try{await post(`/v2/credentials/${encodeURIComponent(id)}/grants`,{subject:event.target.elements.subject.value});vaultClose();await vaultLoad();}
    catch(error){$('#vault-error').textContent=error.message;button.disabled=false;}};
  dialog.querySelectorAll('[data-revoke]').forEach(button=>button.onclick=async()=>{button.disabled=true;
    try{await post(`/v2/credentials/${encodeURIComponent(id)}/grants/${encodeURIComponent(button.dataset.revoke)}/revoke`);vaultClose();await vaultLoad();}
    catch(error){$('#vault-error').textContent=error.message;button.disabled=false;}});
}
