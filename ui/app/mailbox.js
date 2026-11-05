/* ui/app/mailbox.js — one mailbox's page in Settings > Mailboxes (#/settings/mailboxes/<id>, GET /v2/mailboxes/<id>):
   who can use it (Read, or Read and write), its routines, and Stop using in Tico. The owner of a personal mailbox, or
   an admin for a shared inbox, changes who can use it; everyone else reads.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const MB_LEVELS = [['read', 'Read'], ['read_write', 'Read and write']];
const mbLevelName = level => (MB_LEVELS.find(([k]) => k === level) || [level, level])[1];
const MB_KIND = m => (m.kind === 'shared' ? 'Shared inbox' : m.person_id ? esc(m.person_name || m.person_id) : 'Awaiting person');
const mbBack = '<p class="mb-back"><button type="button" class="linkish" data-mb-back>‹ Mailboxes</button></p>';

async function renderMailbox(id) {
  const el = $('#set-mailbox'); if (!el) return;
  const run = ++MB.run;
  if (el.dataset.mbId !== id) {
    el.dataset.mbId = id;
    el.innerHTML = mbBack + skelRows(3, {card: true, dot: false});
  }
  if (!el.dataset.mbBound) {
    el.dataset.mbBound = '1';
    el.addEventListener('click', event => { const b = event.target.closest('button'); if (b) void mbOneClick(el, b); });
    el.addEventListener('change', event => {
      if (event.target.matches('[data-mb-routine]')) void mbRoutine(el, event.target);
    });
    el.addEventListener('submit', event => {
      if (event.target.matches('[data-mb-add-form]')) { event.preventDefault(); void mbAdd(el, event.target); }
    });
  }
  let d;
  try { d = await get(`/v2/mailboxes/${encodeURIComponent(id)}`, {fresh: true}); }
  catch (e) { if (run === MB.run) el.innerHTML = `${mbBack}<div class="empty">${esc(e.message)}</div>`; return; }
  if (run !== MB.run || MAILBOX_OPEN !== id) return;
  mbOneDraw(el, d);
}

function mbLevelSeg(a) {
  return `<div class="theme-seg" role="radiogroup" aria-label="Level for ${esc(a.name)}">${MB_LEVELS.map(([k, label]) =>
    `<button type="button" role="radio" aria-checked="${a.level === k}" data-mb-level="${k}" data-mb-access="${esc(a.id)}">${label}</button>`).join('')}</div>`;
}

function mbOneDraw(el, d) {
  const m = d.mailbox;
  const state = {connected: 'connected', not_connected: 'not connected', awaiting_person: 'private', stopped: 'not in use'}[m.state] || m.state;
  // What Tico may do here for the person looking: Read only on a Read access, nothing when they have none.
  const may = m.level === 'read_write' ? mbMayLine(d.may) : m.level === 'read' ? 'read' : '';
  const status = [MB_KIND(m), state, may ? `Tico may ${esc(may)}` : ''].filter(Boolean).join(' · ');
  const rows = d.accesses.map(a => `<tr data-mb-access-row="${esc(a.id || a.grantee_type)}">
      <td>${esc(a.name)}${a.grantee_type === 'bot' ? ' <span class="muted">· bot</span>' : ''}</td>
      <td>${d.can_manage && !a.implicit ? mbLevelSeg(a) : esc(mbLevelName(a.level))}</td>
      <td class="mb-act">${d.can_manage && !a.implicit ? `<button type="button" class="ghost danger" data-mb-remove="${esc(a.id)}">Remove</button>` : ''}</td></tr>`).join('');
  const pick = d.candidates?.length ? `<form class="mb-form" data-mb-add-form hidden>
      <label class="google-field"><span>Who</span><select name="who">${d.candidates.map(x =>
        `<option value="${esc(x.type)}:${esc(x.id)}">${esc(x.name)}${x.type === 'bot' ? ' (bot)' : ''}</option>`).join('')}</select></label>
      <label class="google-field"><span>Level</span><select name="level">${MB_LEVELS.map(([k, label]) => `<option value="${k}">${label}</option>`).join('')}</select></label>
      <div class="row"><button type="submit" class="primary">Add</button><button type="button" class="ghost" data-mb-add-cancel>Cancel</button></div></form>` : '';
  const routines = d.routines.length ? `<ul class="mb-routines">${d.routines.map(r => `<li data-mb-routine-row="${esc(r.id)}">
      <a href="#/automations/${encodeURIComponent(r.id)}">${esc(r.name)}</a>
      <label class="mb-on"><input type="checkbox" role="switch" class="people-switch" data-mb-routine="${esc(r.id)}" aria-label="${esc(r.name)} on"${
        r.enabled ? ' checked' : ''}${r.can_edit ? '' : ' disabled'}> On</label></li>`).join('')}</ul>` : '<p class="muted">None</p>';
  el.innerHTML = `${mbBack}
    <section class="card" id="mailbox-head"><header><h2>${esc(m.address)}</h2></header><p class="muted" data-mb-status>${status}</p></section>
    <section class="card" id="mailbox-access"><header><h2>Who can use this mailbox</h2>
      ${d.can_manage && d.candidates?.length ? '<button class="primary settings-head-act" type="button" data-mb-add-open>Add access</button>' : ''}</header>
      ${pick}
      <table class="settings-table mb-table"><tbody>${rows}</tbody></table>
      <p class="muted">Write = organize, draft and send, as far as the company allows. Each person's Assistant gets that person's access.</p></section>
    <section class="card" id="mailbox-routines"><header><h2>Routines</h2>
      <a class="ghost settings-head-act" href="#/automations">Add routine</a></header>${routines}</section>
    ${d.can_stop ? '<div class="row mb-stop"><button type="button" class="ghost danger" data-mb-stop>Stop using in Tico</button></div>' : ''}`;
  el._mb = d;
}

function mbClose() {
  MAILBOX_OPEN = '';
  settingsAddress();
  return renderSettingsMailboxes();
}

async function mbOneClick(el, b) {
  const d = el._mb, id = d?.mailbox?.id;
  if (b.hasAttribute('data-mb-back')) return mbClose();
  if (!id) return;
  const base = `/v2/mailboxes/${encodeURIComponent(id)}`;
  const write = async run => {
    b.disabled = true;
    try { mbOneDraw(el, await run()); }
    catch (e) { b.disabled = false; toast(e.message, true); }
  };
  if (b.hasAttribute('data-mb-add-open')) { const f = el.querySelector('[data-mb-add-form]'); f.hidden = false; f.querySelector('select').focus(); return; }
  if (b.hasAttribute('data-mb-add-cancel')) { b.closest('form').hidden = true; return; }
  if (b.dataset.mbLevel && b.getAttribute('aria-checked') !== 'true') {
    return write(() => patch(`${base}/accesses/${encodeURIComponent(b.dataset.mbAccess)}`, {level: b.dataset.mbLevel}));
  }
  if (b.dataset.mbRemove) return write(() => writeRequest('DELETE', `${base}/accesses/${encodeURIComponent(b.dataset.mbRemove)}`));
  if (b.hasAttribute('data-mb-stop')) {
    if (!await settingsConfirm({title: 'Stop using in Tico?', text: `Tico stops using ${d.mailbox.address}.`, ok: 'Stop using'})) return;
    b.disabled = true;
    try { await writeRequest('DELETE', base); toast('Stopped'); }
    catch (e) { b.disabled = false; toast(e.message, true); return; }
    return mbClose();
  }
}

async function mbAdd(el, form) {
  const [type, ...rest] = form.querySelector('[name=who]').value.split(':');
  const button = form.querySelector('[type=submit]');
  button.disabled = true;
  try {
    const d = await post(`/v2/mailboxes/${encodeURIComponent(el._mb.mailbox.id)}/accesses`,
      {grantee_type: type, grantee_id: rest.join(':'), level: form.querySelector('[name=level]').value});
    mbOneDraw(el, d);
  } catch (e) { button.disabled = false; settingsFieldError(form.querySelector('[name=who]'), e.message); }
}

async function mbRoutine(el, input) {
  input.disabled = true;
  try { await patch(`/v2/automations/${encodeURIComponent(input.dataset.mbRoutine)}`, {enabled: input.checked}); }
  catch (e) { input.checked = !input.checked; toast(e.message, true); }
  input.disabled = false;
}
