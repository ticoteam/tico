/* ui/app/vault.ts — Credential vault dialogs (reveal, add or edit, share, delete) for Tools
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The vault as GET /v2/credentials answers it (integrations.js vaultFetch): credentials as metadata, and who they are shared with.
interface VaultGrant { id: string; subject: string; parent_id?: string | null; can_revoke?: boolean; }
interface VaultCredential { id: string; name: string; kind: string; env?: string; source?: string; username?: string; stored?: boolean; revision?: number; grants: VaultGrant[]; }
// A person or a bot a credential can be shared with.
interface VaultParty { id: string; name?: string; email?: string; }
interface VaultState { credentials: VaultCredential[]; computers?: { id: string; label?: string }[]; people: VaultParty[]; bots: VaultParty[]; can_manage?: boolean; }
// What the edit dialog starts from: a stored credential to change, a template (a tool's variable) to add, or nothing.
interface VaultEditRow { id?: string; name?: string; username?: string; kind?: string; env?: string; source?: string; stored?: boolean; revision?: number; }

// Credentials are listed as metadata; reveal is a separate authorized, audited request.
let VAULT: VaultState | null = null, VAULT_REVEAL_TIMER: ReturnType<typeof setTimeout> | undefined, VAULT_DIALOG_GENERATION = 0;
function vaultClose() {
  VAULT_DIALOG_GENERATION++;
  clearTimeout(VAULT_REVEAL_TIMER);
  const dialog: HTMLDialogElement | null = $('#credential-dialog');
  if (!dialog) return;
  dialog.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('input,textarea').forEach(input => { input.value = ''; });
  if (dialog.open) dialog.close();
  dialog.replaceChildren();
}
// Read the vault again after a change and let Tools redraw what it shows of it (ui/app/redesign-library.js).
async function vaultLoad() {
  try { await vaultFetch(); }
  catch (error) { toast(errorMessage(error, ''), true); }
  lcVaultChanged();
}
function vaultSubject(subject: string): string | undefined {
  if (subject === 'computers') return 'Every computer (signs models in)';
  const [kind, id] = subject.split(':');
  if (!VAULT) return undefined;
  if (kind === 'computer') {
    const computer = (VAULT.computers || []).find(row => row.id === id);
    return `${computer?.label || id} (computer)`;
  }
  const row = (kind === 'human' ? VAULT.people : VAULT.bots).find(row => row.id === id);
  return row?.name || row?.email || id;
}
async function vaultDelete(id: string) {
  try {
    await writeRequest('DELETE', `/v2/credentials/${encodeURIComponent(id)}`, undefined);
    vaultClose();
    await vaultLoad();
  } catch(error) {toast(errorMessage(error, ''));}
}
function vaultDialog(title: string, body: string): HTMLDialogElement | null {
  vaultClose();
  const dialog: HTMLDialogElement | null = $('#credential-dialog');
  if (!dialog) return null;
  dialog.innerHTML = `<header><h2>${esc(title)}</h2><button type="button" class="ghost tmodal-act" data-vault-close aria-label="Close credential">Close</button></header>${body}`;
  dialog.querySelector<HTMLButtonElement>('[data-vault-close]')!.onclick = vaultClose;   // drawn just above
  dialog.oncancel = event => {event.preventDefault();vaultClose();};
  dialog.showModal(); return dialog;
}
async function vaultReveal(id: string) {
  const row = VAULT?.credentials.find(row => row.id === id);
  if (!row) return;
  const dialog = vaultDialog(row.name, '<p class="muted">Loading…</p>');
  if (!dialog) return;
  const generation = VAULT_DIALOG_GENERATION;
  try {
    const result = await post(`/v2/credentials/${encodeURIComponent(id)}/reveal`);
    if (!dialog.open || !dialog.isConnected || generation !== VAULT_DIALOG_GENERATION) return;
    const loading = dialog.querySelector<HTMLElement>('p');
    if (!loading) throw new Error('The vault dialog did not draw its Loading line');
    loading.remove();   // the Loading line vaultDialog drew
    const label = document.createElement('label'); label.textContent = 'Value';
    const value = document.createElement('textarea'); value.readOnly = true; value.value = result.value; value.setAttribute('aria-label','Value'); label.appendChild(value);dialog.appendChild(label);
    const copy = document.createElement('button');copy.type='button';copy.className='primary';copy.textContent='Copy';
    copy.onclick = async () => {try {await navigator.clipboard.writeText(value.value);toast('Copied');} catch {value.focus();value.select();toast('Select and copy the value shown.');}};
    dialog.appendChild(copy); VAULT_REVEAL_TIMER = setTimeout(vaultClose,60000);
    // The error replaces the Loading line, which is still drawn while this dialog is open.
  } catch(error) {
    if(dialog.open && generation === VAULT_DIALOG_GENERATION) {
      const loading = dialog.querySelector<HTMLElement>('p');
      if (!loading) throw new Error('The vault dialog did not draw its Loading line');
      loading.textContent=errorMessage(error, '');
    }
  }
}
// `row` is a stored credential to edit, or for Add a template ({name, env, source}: a tool's variable) to start from.
function vaultEdit(row?: VaultEditRow) {
  const editing = !!row?.id;
  const dialog = vaultDialog(editing ? 'Edit credential' : 'Add credential',`<form id="vault-form" class="bot-editor-grid">
    <label>Name<input name="name" type="text" required maxlength="150" autocomplete="off" value="${esc(row?.name || '')}"></label>
    <label>Username (optional)<input name="username" type="text" maxlength="250" value="${esc(row?.username || '')}" autocomplete="off" spellcheck="false"></label>
    <label>Type<select name="kind">${['api_key','password','token','file','connection'].map(kind=>`<option value="${kind}" ${row?.kind===kind?'selected':''}>${esc(kind.replace('_',' '))}</option>`).join('')}</select></label>
    <label>Bot variable name (optional)<input name="env" type="text" pattern="[A-Z_][A-Z0-9_]*" maxlength="100" autocomplete="off" spellcheck="false" placeholder="POSTHOG_API_KEY" value="${esc(row?.env || '')}"></label>
    <label class="bot-editor-wide">${row?.stored ? 'New value (blank keeps it)' : 'Value'}<textarea name="secret" aria-label="New value" autocomplete="off" spellcheck="false"></textarea></label>
    <p class="err bot-editor-wide" id="vault-error"></p>
    <button class="primary" type="submit">Save credential</button></form>`);
  if (!dialog) return;
  // The template just above draws the form and its fields; without a form there is nothing to wire.
  const form = dialog.querySelector<HTMLFormElement>('form');
  if (!form) return;
  const field = (name: string) => form.querySelector<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(`[name=${name}]`)!;
  form.onsubmit = async event => {
    event.preventDefault(); const button = form.querySelector<HTMLButtonElement>('[type=submit]')!; button.disabled=true;
    const data: {name: string; username: string; kind: string; env: string; source: string; secret?: string; expected_revision?: number | undefined} = {name:field('name').value,username:field('username').value,kind:field('kind').value,env:field('env').value,source:row?.source || ''};
    const secret = field('secret').value;
    if(secret)data.secret=secret;
    if(editing)data.expected_revision=row?.revision;
    try {await post('/v2/credentials'+(editing?'/'+encodeURIComponent(row?.id ?? ''):''),data);vaultClose();await vaultLoad();}
    catch(error){$('#vault-error').textContent=errorMessage(error, '');button.disabled=false;}
  };
}
function vaultShare(id: string) {
  if (!VAULT) return;
  const row = VAULT.credentials.find(row=>row.id===id);
  if (!row) return;
  const modelKey=['OPENAI_API_KEY','ANTHROPIC_API_KEY','CLAUDE_CODE_OAUTH_TOKEN','CURSOR_API_KEY'].includes(row.env ?? '')&&['api_key','token'].includes(row.kind)&&row.stored;
  const choices=[...(VAULT.can_manage&&modelKey?[{subject:'computers',label:'Every computer (signs models in)'}]:[]),
    ...(VAULT.can_manage ? VAULT.people.map(person=>({subject:'human:'+person.id,label:person.name || person.email})) : []),
    ...VAULT.bots.map(bot=>({subject:'bot:'+bot.id,label:`${bot.name} (bot)`}))];
  const dialog=vaultDialog('Access to '+row.name,`<p class="muted">Revoking access also revokes the access they gave their bots.</p>
    <div>${row.grants.map(grant=>`<div class="row"><span>${esc(vaultSubject(grant.subject))}${grant.parent_id?' · delegated':''}</span>${grant.can_revoke?`<button class="ghost" type="button" data-revoke="${esc(grant.id)}">Revoke</button>`:''}</div>`).join('') || '<p>Owners only.</p>'}</div>
    <form id="vault-grant-form"><label>Grant access to<select name="subject" required>${choices.map(choice=>`<option value="${esc(choice.subject)}">${esc(choice.label)}</option>`).join('')}</select></label>
    <button class="primary" type="submit" ${choices.length?'':'disabled'}>Grant access</button><p class="err" id="vault-error"></p></form>`);
  if (!dialog) return;
  // The form, its submit button, its subject select and the revoke buttons are drawn just above.
  const form = dialog.querySelector<HTMLFormElement>('form');
  if (!form) return;
  form.onsubmit=async event=>{event.preventDefault();const button=form.querySelector<HTMLButtonElement>('button')!;button.disabled=true;
    try{await post(`/v2/credentials/${encodeURIComponent(id)}/grants`,{subject:form.querySelector<HTMLSelectElement>('[name=subject]')!.value});vaultClose();await vaultLoad();}
    catch(error){$('#vault-error').textContent=errorMessage(error, '');button.disabled=false;}};
  dialog.querySelectorAll<HTMLButtonElement>('[data-revoke]').forEach(button=>{button.onclick=async()=>{button.disabled=true;
    try{await post(`/v2/credentials/${encodeURIComponent(id)}/grants/${encodeURIComponent(button.dataset.revoke ?? '')}/revoke`);vaultClose();await vaultLoad();}
    catch(error){$('#vault-error').textContent=errorMessage(error, '');button.disabled=false;}};});
}
