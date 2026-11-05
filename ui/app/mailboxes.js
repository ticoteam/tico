/* ui/app/mailboxes.js — Settings > Mailboxes: which inboxes Tico uses and who may use each (backend/mailboxes.py).
   An admin sees the Connection card (ui/google-connect.js draws its body into #set-google), every mailbox with its
   person, status and routines, Connect mailbox, and What Tico may do. Everyone else sees their own mailbox and the
   ones shared with them. One mailbox's page is ui/app/mailbox.js (#/settings/mailboxes/<id>).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const MB = {run: 0, data: null};
const MB_STATE = {connected: ['ok', 'Connected'], not_connected: ['waiting', 'Not connected'],
  awaiting_person: ['', 'Private'], stopped: ['', 'Not in use']};
// What Tico may do (PUT /v2/google/permissions): the field the server keeps, the key GET /v2/mailboxes reads it back as.
const MB_MAY = [['organize_mail', 'organize', 'Organize', 'label, archive, draft'], ['send_mail', 'send', 'Send', ''],
  ['change_calendar', 'change_calendar', 'Change calendar', '']];
const mbPill = state => { const [tone, label] = MB_STATE[state] || ['', state]; return `<span class="pill${tone ? ' ' + tone : ''}">${esc(label)}</span>`; };
const mbLink = id => `#/settings/mailboxes/${encodeURIComponent(id)}`;
// "read and organize", "read, organize and send": what Tico may do in a mailbox someone has Read and write on.
function mbMayLine(may) {
  const words = ['read', may?.organize && 'organize', may?.send && 'send', may?.change_calendar && 'change calendar'].filter(Boolean);
  return words.length > 1 ? `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}` : words[0];
}

function mbShell(el, admin) {
  el.dataset.mbAdmin = String(admin);
  el.innerHTML = `<div data-mb-list>
      <section class="card" id="mailboxes-top"><header><h2>${admin ? 'Connection' : 'Your mailbox'}</h2>
        <span class="muted" data-mb-provider>Google Workspace</span></header>
        <div data-mb-mine></div><div id="set-google"></div></section>
      <div data-mb-body>${skelRows(3, {card: true, dot: false})}</div>
    </div>
    <div id="set-mailbox" hidden></div>`;
  if (el.dataset.mbBound) return;
  el.dataset.mbBound = '1';
  el.addEventListener('click', event => { const b = event.target.closest('button'); if (b) void mbClick(el, b); });
  el.addEventListener('submit', event => {
    if (event.target.matches('[data-mb-form]')) { event.preventDefault(); void mbConnect(el, event.target); }
  });
  el.addEventListener('change', event => { if (event.target.matches('[data-google-may]')) void mbSaveMay(el, event.target); });
}

async function renderSettingsMailboxes() {
  const el = $('#set-mailboxes'); if (!el) return;
  const admin = settingsIsAdmin();
  if (el.dataset.mbAdmin !== String(admin)) mbShell(el, admin);
  const list = el.querySelector('[data-mb-list]'), one = $('#set-mailbox');
  list.hidden = !!MAILBOX_OPEN; one.hidden = !MAILBOX_OPEN;
  if (MAILBOX_OPEN) return renderMailbox(MAILBOX_OPEN);
  const run = ++MB.run;
  void window.mountGoogleConnect?.($('#set-google'));
  let d;
  try { d = await get('/v2/mailboxes', {fresh: true}); }
  catch (e) {
    if (run === MB.run) el.querySelector('[data-mb-body]').innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    return;
  }
  if (run !== MB.run || MAILBOX_OPEN) return;
  MB.data = d;
  mbDraw(el, d);
}

function mbDraw(el, d) {
  el.querySelector('[data-mb-provider]').textContent = `Google Workspace${d.domain ? ' · ' + d.domain : ''}`;
  // Your own mailbox when the company's Workspace key covers it; with Each person signs in, Connect Google says it.
  const mine = d.mine && !d.admin && d.mine.connection === 'workspace' && d.mine.state === 'connected' ? d.mine : null;
  el.querySelector('[data-mb-mine]').innerHTML = mine ? `<div class="mb-mine" data-mb-mine-box>
      <p><strong>${esc(mine.address)}</strong> <span class="muted">· connected by your company</span></p>
      <p class="muted">Tico may ${esc(mbMayLine(d.may))}. Only you and your Assistant can use it.</p></div>` : '';
  const body = el.querySelector('[data-mb-body]');
  if (formBusy(body)) return;   // a half-typed address stays put
  body.innerHTML = d.admin ? mbAdminBody(d) : mbMemberBody(d);
}

function mbPerson(m) {
  if (m.kind === 'shared') return 'Shared inbox';
  if (m.person_id) return esc(m.person_name || m.person_id);
  if (m.status !== 'active') return '<span class="muted">Awaiting person</span>';
  return `<span class="muted">Awaiting person</span> <button type="button" class="ghost" data-mb-share="${esc(m.id)}">Make shared inbox</button>`;
}

function mbAdminBody(d) {
  const rows = (d.mailboxes || []).map(m => `<tr data-mb-row="${esc(m.id)}">
      <td><a href="${mbLink(m.id)}">${esc(m.address)}</a></td>
      <td>${mbPerson(m)}</td>
      <td>${mbPill(m.state)}</td>
      <td>${m.routines?.length ? m.routines.map(r => esc(r.name)).join(', ') : '<span class="muted">None</span>'}</td></tr>`).join('');
  const table = rows ? `<table class="settings-table mb-table"><thead><tr><th>Address</th><th>Person</th><th>Status</th><th>Routines</th></tr></thead>
      <tbody>${rows}</tbody></table>` : '<div class="empty">No mailboxes yet.</div>';
  const may = d.may || {};
  const box = ([field, key, label, note]) => `<label class="google-may-row"><input type="checkbox" data-google-may="${field}"${
    may[key] ? ' checked' : ''}><span>${label}${note ? `<span class="muted">${note}</span>` : ''}</span></label>`;
  return `<section class="card" id="mailboxes-list"><header><h2>Mailboxes</h2>
      <button class="primary settings-head-act" type="button" data-mb-connect-open>Connect mailbox</button></header>
      <form class="mb-form" data-mb-form hidden>
        <label class="google-field"><span>Address</span><input type="email" name="address" autocomplete="off" spellcheck="false" required></label>
        <label class="mb-check"><input type="checkbox" name="shared"> Shared inbox</label>
        <div class="row"><button type="submit" class="primary">Connect</button><button type="button" class="ghost" data-mb-connect-cancel>Cancel</button></div>
      </form>
      ${table}</section>
    <section class="card" id="mailboxes-may"><header><h2>What Tico may do</h2></header>
      <div class="google-may-row" data-google-may-read><span class="google-may-always" aria-hidden="true"></span><span>Read<span class="muted">always</span></span></div>
      ${MB_MAY.map(box).join('')}</section>`;
}

function mbMemberBody(d) {
  const rows = (d.shared || []).map(m => `<li data-mb-row="${esc(m.id)}"><a href="${mbLink(m.id)}">${esc(m.address)}</a>
      <span class="muted">· ${m.kind === 'shared' ? 'shared inbox' : esc(m.person_name || '')}</span></li>`).join('');
  return rows ? `<section class="card" id="mailboxes-shared"><header><h2>Shared with you</h2></header><ul class="mb-shared">${rows}</ul></section>` : '';
}

async function mbClick(el, b) {
  if (b.hasAttribute('data-mb-connect-open')) {
    const form = el.querySelector('[data-mb-form]'); form.hidden = false; form.querySelector('[name=address]').focus(); return;
  }
  if (b.hasAttribute('data-mb-connect-cancel')) { const form = b.closest('form'); form.reset(); form.hidden = true; return; }
  if (b.dataset.mbShare) {
    b.disabled = true;
    try { await post(`/v2/mailboxes/${encodeURIComponent(b.dataset.mbShare)}/share`, {}); toast('Shared inbox'); }
    catch (e) { b.disabled = false; toast(e.message, true); return; }
    return renderSettingsMailboxes();
  }
}

async function mbConnect(el, form) {
  const input = form.querySelector('[name=address]'), button = form.querySelector('[type=submit]');
  const address = input.value.trim();
  if (!address) return settingsFieldError(input, 'Enter an address.');
  button.disabled = true;
  try {
    await post('/v2/mailboxes', {address, shared: form.querySelector('[name=shared]').checked});
    form.reset(); form.hidden = true;
    toast('Connected');
  } catch (e) { button.disabled = false; settingsFieldError(input, e.message); return; }
  await renderSettingsMailboxes();
}

// Each box saves on its own change, all three sent together (the server takes the whole setting). The Connection
// card's setup prompt names what Tico asks Google for, so it is drawn again.
async function mbSaveMay(el, input) {
  const boxes = [...el.querySelectorAll('[data-google-may]')];
  const body = Object.fromEntries(boxes.map(box => [box.dataset.googleMay, box.checked]));
  boxes.forEach(box => { box.disabled = true; });
  try {
    await put('/v2/google/permissions', body);
    toast('Saved');
    boxes.forEach(box => { box.disabled = false; });
    void window.mountGoogleConnect?.($('#set-google'), {force: true});
  } catch (e) {
    input.checked = !input.checked;
    boxes.forEach(box => { box.disabled = false; });
    toast(e.message, true);
  }
}
