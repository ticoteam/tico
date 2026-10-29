/* ui/app/vault.js — Credential vault dialogs (reveal, add or edit, share, delete) for Library › Tools
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Credentials are listed as metadata; reveal is a separate authorized, audited request.
let VAULT = null, VAULT_REVEAL_TIMER = null, VAULT_CODE_TICK = null, VAULT_DIALOG_GENERATION = 0;
function vaultClose() {
  VAULT_DIALOG_GENERATION++;
  clearTimeout(VAULT_REVEAL_TIMER);
  clearInterval(VAULT_CODE_TICK);
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
    // A login's stored value is its password and authenticator key: Reveal shows the password only.
    let shown = result.value;
    if (row.kind === 'login') { try { shown = JSON.parse(result.value).password ?? ''; } catch { /* a plain password */ } }
    const label = document.createElement('label'); label.textContent = row.kind === 'login' ? 'Password' : 'Value';
    const value = document.createElement('textarea'); value.readOnly = true; value.value = shown; value.setAttribute('aria-label','Value'); label.appendChild(value);dialog.appendChild(label);
    const copy = document.createElement('button');copy.type='button';copy.className='primary';copy.textContent='Copy';
    copy.onclick = async () => {try {await navigator.clipboard.writeText(value.value);toast('Copied');} catch {value.focus();value.select();toast('Select and copy the value shown.');}};
    dialog.appendChild(copy); VAULT_REVEAL_TIMER = setTimeout(vaultClose,60000);
  } catch(error) { if(dialog.open && generation === VAULT_DIALOG_GENERATION) dialog.querySelector('p').textContent=error.message; }
}
// A login's sites as the server keeps them: lowercase hostnames, no scheme, port or path.
function vaultSites(text) {
  const hosts = String(text || '').split(/[\s,]+/).filter(Boolean).map(part => {
    try { return new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(part) ? part : 'https://' + part).hostname.toLowerCase(); }
    catch { return part.toLowerCase(); }
  });
  return [...new Set(hosts)];
}
// `row` is a stored credential to edit, or for Add a template ({name, env, source}: a tool's variable) to start from.
// A login (kind `login`) has a password, an optional 2FA setup key (a base32 key or an otpauth:// URI) and its sites
// instead of a value and a bot variable; a stored login stays a login and nothing else becomes one.
function vaultEdit(row) {
  const editing = !!row?.id, kinds = editing ? (row.kind === 'login' ? ['login'] : ['api_key','password','token','file','connection'])
    : ['api_key','password','token','file','connection','login'];
  const attrs = 'autocomplete="off" autocapitalize="off" spellcheck="false" data-lpignore="true" data-1p-ignore';
  const dialog = vaultDialog(editing ? 'Edit credential' : 'Add credential',`<form id="vault-form" class="bot-editor-grid">
    <label>Name<input name="name" type="text" required maxlength="150" autocomplete="off" value="${esc(row?.name || '')}"></label>
    <label>Username (optional)<input name="username" type="text" maxlength="250" value="${esc(row?.username || '')}" autocomplete="off" spellcheck="false"></label>
    <label>Type<select name="kind"${kinds.length === 1 ? ' disabled' : ''}>${kinds.map(kind=>`<option value="${kind}" ${row?.kind===kind?'selected':''}>${esc(kind.replace('_',' '))}</option>`).join('')}</select></label>
    <label data-vault-for="value">Bot variable name (optional)<input name="env" type="text" pattern="[A-Z_][A-Z0-9_]*" maxlength="100" autocomplete="off" spellcheck="false" placeholder="POSTHOG_API_KEY" value="${esc(row?.env || '')}"></label>
    <label class="bot-editor-wide" data-vault-for="value">${row?.stored ? 'New value (blank keeps it)' : 'Value'}<textarea name="secret" aria-label="New value" autocomplete="off" spellcheck="false"></textarea></label>
    <label data-vault-for="login">Sites<input name="sites" type="text" maxlength="1000" ${attrs} placeholder="acme.example" value="${esc((row?.sites || []).join(', '))}"></label>
    <label data-vault-for="login">${editing ? 'New password (blank keeps it)' : 'Password'}<input name="password" type="password" maxlength="1000" ${attrs}></label>
    <label data-vault-for="login">${row?.has_totp ? 'New 2FA setup key (blank keeps it)' : '2FA setup key (optional)'}<input name="totp" type="password" maxlength="1000" ${attrs} placeholder="Key or otpauth:// link"></label>
    ${row?.has_totp ? '<label class="vault-check" data-vault-for="login"><input name="totp_off" type="checkbox">Turn off 2FA</label>' : ''}
    <p class="err bot-editor-wide" id="vault-error"></p>
    <button class="primary" type="submit">Save credential</button></form>`);
  const form = dialog.querySelector('form'), el = form.elements;
  const kindNow = () => el.kind.value;
  const swap = () => {
    const login = kindNow() === 'login';
    form.querySelectorAll('[data-vault-for]').forEach(label => { label.hidden = (label.dataset.vaultFor === 'login') !== login; });
    // A login's name defaults to its first site.
    el.name.required = !login;
    el.sites.required = login;
    el.password.required = login && !editing;
  };
  el.kind.onchange = swap;
  swap();
  form.onsubmit = async event => {
    event.preventDefault(); const button=form.querySelector('[type=submit]');button.disabled=true;
    let data;
    if (kindNow() === 'login') {
      const sites = vaultSites(el.sites.value);
      data = {name: el.name.value.trim() || sites[0] || '', username: el.username.value, kind: 'login', sites, source: row?.source || ''};
      if (el.password.value) data.password = el.password.value;
      if (el.totp.value.trim()) data.totp = el.totp.value.trim();
      else if (el.totp_off?.checked) data.totp = '';      // an empty key turns 2FA off
    } else {
      data={name:el.name.value,username:el.username.value,kind:kindNow(),env:el.env.value,source:row?.source || ''};
      if(el.secret.value)data.secret=el.secret.value;
    }
    if(editing)data.expected_revision=row.revision;
    try {await post('/v2/credentials'+(editing?'/'+encodeURIComponent(row.id):''),data);vaultClose();await vaultLoad();}
    catch(error){$('#vault-error').textContent=error.message;button.disabled=false;}
  };
}
// A login's current authenticator code (GET /v2/credentials/{id}/code, audited): six digits and the seconds it has
// left, read again when it runs out while the dialog is open. The dialog closes itself after two minutes.
function vaultCode(id) {
  const row = VAULT.credentials.find(row => row.id === id);
  const dialog = vaultDialog(row.name, '<div class="vault-code" aria-live="polite"><output class="mono" data-code>······</output><span class="muted tnum" data-left></span></div><p class="err" data-code-error></p>');
  const generation = VAULT_DIALOG_GENERATION, out = dialog.querySelector('[data-code]'), left = dialog.querySelector('[data-left]');
  const copy = document.createElement('button'); copy.type = 'button'; copy.className = 'primary'; copy.textContent = 'Copy'; copy.disabled = true;
  dialog.appendChild(copy);
  let code = '', ends = 0, reading = false;
  const live = () => dialog.open && dialog.isConnected && generation === VAULT_DIALOG_GENERATION;
  const read = async () => {
    reading = true;
    try {
      const result = await get(`/v2/credentials/${encodeURIComponent(id)}/code`);
      if (!live()) return;
      code = String(result.code || '');
      ends = Date.now() + Math.max(1, Number(result.expires_in) || 1) * 1000;
      out.textContent = code.length === 6 ? code.slice(0, 3) + ' ' + code.slice(3) : code;
      copy.disabled = !code;
      tick();
    } catch (error) { if (live()) { dialog.querySelector('[data-code-error]').textContent = error.message; clearInterval(VAULT_CODE_TICK); } }
    finally { reading = false; }
  };
  const tick = () => {
    const s = Math.max(0, Math.ceil((ends - Date.now()) / 1000));
    left.textContent = s + 's';
    if (!s && !reading) void read();
  };
  copy.onclick = async () => { try { await navigator.clipboard.writeText(code); toast('Copied'); } catch { toast('Select and copy the code shown.'); } };
  VAULT_CODE_TICK = setInterval(() => { if (ends) tick(); }, 1000);
  VAULT_REVEAL_TIMER = setTimeout(vaultClose, 120000);
  void read();
}
// A password manager's export (POST /v2/credentials/import): its logins are added with no access granted. The file is
// read here and sent once; the answer says how many were imported and skipped, and why any row failed.
async function vaultImport(file) {
  const format = /\.csv$/i.test(file.name) || file.type === 'text/csv' ? 'keepass' : 'bitwarden';
  let result;
  try { result = await post('/v2/credentials/import', {format, content: await file.text()}); }
  catch (error) { toast(error.message, true); return; }
  const line = `${result.imported || 0} imported, ${result.skipped || 0} skipped`;
  const errors = Array.isArray(result.errors) ? result.errors : [];
  if (errors.length) {
    vaultDialog('Import', `<p>${esc(line)}, ${errors.length} failed</p><div>${errors.slice(0, 50).map(e =>
      `<div class="row"><span>${e.row != null ? 'Row ' + esc(String(e.row)) : 'Row'}</span><span class="muted">${esc(e.reason || '')}</span></div>`).join('')}</div>`);
  } else toast(line);
  await vaultLoad();
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
