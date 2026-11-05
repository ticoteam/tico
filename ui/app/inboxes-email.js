/* ui/app/inboxes-email.js — Email in Settings > Inboxes: the Google mailboxes Tico uses (backend/mailboxes.py).
   Kind 'mailbox': its rows in the list (an admin sees every mailbox; everyone else their own and the ones shared with
   them), one mailbox's page (#/settings/inboxes/mailbox/<id>: who can use it, Read or Read and write, its message
   automations, its recent mail, Check connection and Stop using in Tico) and Add inbox > Email (Connect mailbox).
   In Inboxes > Settings > Email: the Connection card (ui/google-connect.js draws it into #set-google) and What Tico may
   do. First run's required step is Connect company email. Registry: ui/app/inboxes.js.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// What Tico may do, kept here rather than in a card so leaving Settings and coming back mid-save keeps it: may counts the
// saves, pending holds what the one in flight sends (by PUT field), known what the last one left the server with, and
// refreshAfterMay that a draw waited for it.
const MB = {run: 0, may: 0, pending: null, known: null, refreshAfterMay: false};
const MB_LEVELS = [['read', 'Read'], ['read_write', 'Read and write']];
// What Tico may do (PUT /v2/google/permissions): the field the server keeps, the key GET /v2/mailboxes reads it back as.
const MB_MAY = [['organize_mail', 'organize', 'Organize', 'label, archive, draft'], ['send_mail', 'send', 'Send', ''],
  ['change_calendar', 'change_calendar', 'Change calendar', '']];
const mbLevelName = level => (MB_LEVELS.find(([k]) => k === level) || [level, level])[1];
const mbLink = id => inboxesLink('mailbox', id);
const mbMailboxes = ctx => ctx.once('mailboxes', () => get('/v2/mailboxes', {fresh: true}));
const mbGoogle = ctx => ctx.once('google', () => get('/v2/google', {fresh: true}));
// "read and organize", "read, organize and send": what Tico may do in a mailbox someone has Read and write on.
function mbMayLine(may) {
  const words = ['read', may?.organize && 'organize', may?.send && 'send', may?.change_calendar && 'change calendar'].filter(Boolean);
  return words.length > 1 ? `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}` : words[0];
}
// One status for the list and the page: a connected mailbox a computer can't read needs attention, and a refused
// sign-in says so, since only a person signing in again fixes it.
function mbStatus(m) {
  if (m.state === 'stopped') return ['off', 'Not in use'];
  if (m.state === 'awaiting_person') return ['pending', 'Awaiting person'];
  if (m.state === 'not_connected') return ['attention', 'Not connected'];
  if (m.sync === 'signin') return ['attention', 'Sign-in expired'];
  if (m.sync === 'failed') return ['attention', 'Sync failed'];
  return ['ok', 'Connected'];
}
const mbWho = m => (m.kind === 'shared' ? 'Shared inbox' : m.person_id ? (m.person_name || m.person_id) : 'Awaiting person');
const mbSynced = m => (m.synced_at ? `Last sync ${ago(m.synced_at)}` : m.state === 'connected' ? 'Not synced yet' : '');

inboxesKind('mailbox', {group: 'email', label: 'Email', order: 10,
  rows: async ctx => {
    const d = await mbMailboxes(ctx);
    const boxes = d.admin ? d.mailboxes || [] : [d.mine, ...(d.shared || [])].filter(Boolean);
    return boxes.map(m => {
      const [status, statusText] = mbStatus(m);
      return {id: m.id, name: m.address, sub: mbWho(m), status, statusText,
        latest: m.newest_epoch ? m.newest_epoch * 1000 : null, usedBy: m.used_by,
        search: (m.routines || []).map(r => r.name).join(' ')};
    });
  },
  detail: (host, id, ctx) => renderMailbox(host, id, ctx),
  add: {label: 'Email (Google)', admin: true, open: ctx => mbConnectDialog(ctx)}});

// ---------------------------------------------------------------------------------------------------------- One mailbox
async function renderMailbox(host, id, ctx) {
  const run = ++MB.run;
  host._mbCtx = ctx;   // the handlers below are bound once a page; each click uses the newest draw's ctx
  if (!host.dataset.mbBound) {
    host.dataset.mbBound = '1';
    host.addEventListener('click', event => { const b = event.target.closest('button'); if (b) void mbOneClick(host, b, host._mbCtx); });
    host.addEventListener('change', event => { if (event.target.matches('[data-mb-routine]')) void mbRoutine(event.target); });
    host.addEventListener('submit', event => {
      if (event.target.matches('[data-mb-add-form]')) { event.preventDefault(); void mbAdd(host, event.target); }
    });
  }
  const d = await get(`/v2/mailboxes/${encodeURIComponent(id)}`, {fresh: true});
  if (run !== MB.run || !ctx.live()) return null;
  mbOneDraw(host, d);
  void mbRecent(host, d.mailbox, run);
  return {kind: 'mailbox', id: d.mailbox.id, name: d.mailbox.address, address: d.mailbox.address, ...d};
}

function mbLevelSeg(a) {
  return `<div class="theme-seg" role="radiogroup" aria-label="Level for ${esc(a.name)}">${MB_LEVELS.map(([k, label]) =>
    `<button type="button" role="radio" aria-checked="${a.level === k}" data-mb-level="${k}" data-mb-access="${esc(a.id)}">${label}</button>`).join('')}</div>`;
}

function mbOneDraw(host, d) {
  const m = d.mailbox, [status, statusText] = mbStatus(m), admin = settingsIsAdmin();
  // What Tico may do here for the person looking: Read only on a Read access, nothing when they have none.
  const may = m.level === 'read_write' ? mbMayLine(d.may) : m.level === 'read' ? 'read' : '';
  const line = [mbWho(m), mbSynced(m), may ? `Tico may ${may}` : ''].filter(Boolean).map(esc).join(' · ');
  const failing = m.sync && m.sync_error ? `<p class="err" data-mb-sync-error>${esc(m.sync_error)}</p>` : '';
  const signin = m.sync === 'signin' ? `<p class="muted" data-mb-signin>Sign in to Google again in <a href="#/settings/inboxes/settings/email">Inboxes › Settings</a>.</p>` : '';
  const actions = [m.status === 'active' ? '<button type="button" class="ghost" data-mb-check>Check connection</button>' : '',
    admin && m.status === 'active' && m.kind === 'personal' && !m.person_id ? '<button type="button" class="ghost" data-mb-share>Make shared inbox</button>' : '',
    d.can_stop ? '<button type="button" class="ghost danger" data-mb-stop>Stop using in Tico</button>' : ''].join('');
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
  const recent = host.querySelector('[data-mb-recent]')?.innerHTML;
  host.innerHTML = `${inboxesHead(host, {name: m.address, status, statusText, actions})}
    <p class="muted mb-line" data-mb-status>${line}</p>${failing}${signin}
    <section class="card" id="mailbox-access"><header><h2>Who can use it</h2>
      ${d.can_manage && d.candidates?.length ? '<button class="primary settings-head-act" type="button" data-mb-add-open>Add access</button>' : ''}</header>
      ${pick}
      <table class="settings-table mb-table"><tbody>${rows}</tbody></table></section>
    <section class="card" id="mailbox-routines"><header><h2>Automations</h2>${inboxesAutomationsLink(m.address)}</header>${routines}</section>
    <section class="card" id="mailbox-recent"><header><h2>Recent</h2>${m.level !== 'none'
      ? `<a class="ghost settings-head-act" href="#/mail?mailbox=${encodeURIComponent(m.address)}">Open mail</a>` : ''}</header>
      <div data-mb-recent>${recent || skelRows(2, {card: false, dot: false})}</div></section>`;
  host._mb = d;
}

// The newest mail Tico holds for it, for whoever may read it (GET /v2/mail/messages refuses everyone else).
async function mbRecent(host, m, run) {
  const slot = () => host.querySelector('[data-mb-recent]');
  if (m.level === 'none') { if (slot()) slot().innerHTML = '<p class="muted">Only people who can use it see its mail.</p>'; return; }
  let d;
  try { d = await get(`/v2/mail/messages?mailbox=${encodeURIComponent(m.address)}&limit=8`, {fresh: true}); }
  catch (e) { if (run === MB.run && slot()) slot().innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  if (run !== MB.run || !slot()) return;
  const list = d.messages || [];
  slot().innerHTML = list.length ? `<ul class="ib-recent">${list.map(x => `<li><span class="ib-recent-what">
      <a href="#/mail?mailbox=${encodeURIComponent(m.address)}&thread=${encodeURIComponent(x.thread_id || '')}">${esc(x.subject || '(no subject)')}</a>
      <span class="muted">${esc(x.from_header || x.from_addr || '')}</span></span>
      <span class="ib-recent-when">${x.epoch ? esc(ago(new Date(x.epoch * 1000).toISOString())) : ''}</span></li>`).join('')}</ul>`
    : '<p class="muted">No mail yet.</p>';
}

async function mbOneClick(host, b, ctx) {
  const d = host._mb, id = d?.mailbox?.id;
  if (!id) return;
  const base = `/v2/mailboxes/${encodeURIComponent(id)}`;
  const write = async run => {
    b.disabled = true;
    try { mbOneDraw(host, await run()); void mbRecent(host, host._mb.mailbox, MB.run); }
    catch (e) { b.disabled = false; toast(e.message, true); }
  };
  if (b.hasAttribute('data-mb-add-open')) { const f = host.querySelector('[data-mb-add-form]'); f.hidden = false; f.querySelector('select').focus(); return; }
  if (b.hasAttribute('data-mb-add-cancel')) { b.closest('form').hidden = true; return; }
  if (b.dataset.mbLevel && b.getAttribute('aria-checked') !== 'true') {
    return write(() => patch(`${base}/accesses/${encodeURIComponent(b.dataset.mbAccess)}`, {level: b.dataset.mbLevel}));
  }
  if (b.dataset.mbRemove) return write(() => writeRequest('DELETE', `${base}/accesses/${encodeURIComponent(b.dataset.mbRemove)}`));
  if (b.hasAttribute('data-mb-share')) return write(() => post(`${base}/share`, {}));
  if (b.hasAttribute('data-mb-check')) return mbCheck(host, b, d, ctx);
  if (b.hasAttribute('data-mb-stop')) {
    if (!await settingsConfirm({title: 'Stop using in Tico?', text: `Tico stops using ${d.mailbox.address}.`, ok: 'Stop using'})) return;
    b.disabled = true;
    try { await writeRequest('DELETE', base); toast('Stopped'); }
    catch (e) { b.disabled = false; toast(e.message, true); return; }
    return ctx.go('');
  }
}

// Check connection: an admin asks Google whether the company's key reads a mailbox it covers; otherwise, and after,
// the page is read again so its sync status and last sync are current.
async function mbCheck(host, b, d, ctx) {
  const m = d.mailbox;
  b.disabled = true;
  try {
    if (settingsIsAdmin() && m.connection === 'workspace') {
      const r = await post('/v2/google/workspace/check', {mailbox: m.address});
      toast(r.message || (r.ok ? 'Connected' : 'Not connected'), !r.ok);
    }
    const fresh = await get(`/v2/mailboxes/${encodeURIComponent(m.id)}`, {fresh: true});
    if (!ctx.live()) return;
    mbOneDraw(host, fresh);
    void mbRecent(host, fresh.mailbox, MB.run);
    if (!(settingsIsAdmin() && m.connection === 'workspace')) {
      const [status, text] = mbStatus(fresh.mailbox);
      toast(text, status === 'attention');
    }
  } catch (e) { toast(e.message, true); }
  finally { if (b.isConnected) b.disabled = false; }
}

async function mbAdd(host, form) {
  const [type, ...rest] = form.querySelector('[name=who]').value.split(':');
  const button = form.querySelector('[type=submit]');
  button.disabled = true;
  try {
    const d = await post(`/v2/mailboxes/${encodeURIComponent(host._mb.mailbox.id)}/accesses`,
      {grantee_type: type, grantee_id: rest.join(':'), level: form.querySelector('[name=level]').value});
    mbOneDraw(host, d);
    void mbRecent(host, d.mailbox, MB.run);
  } catch (e) { button.disabled = false; settingsFieldError(form.querySelector('[name=who]'), e.message); }
}

async function mbRoutine(input) {
  input.disabled = true;
  try { await patch(`/v2/automations/${encodeURIComponent(input.dataset.mbRoutine)}`, {enabled: input.checked}); }
  catch (e) { input.checked = !input.checked; toast(e.message, true); }
  input.disabled = false;
}

// Add inbox > Email: an admin connects a mailbox. A person's address is theirs; any other is a shared inbox when
// ticked, else it waits for its person.
function mbConnectDialog(ctx) {
  const dialog = whDialog('Connect mailbox', `<label>Address<input type="email" name="address" autocomplete="off" spellcheck="false" required></label>
    <label class="mb-check"><input type="checkbox" name="shared"> Shared inbox</label>`,
    '<button class="primary" type="submit" data-mb-connect>Connect</button>');
  dialog.classList.add('mb-dialog');
  whSubmit(dialog, async data => {
    const address = String(data.get('address') || '').trim();
    if (!address) throw new Error('Enter an address.');
    const r = await post('/v2/mailboxes', {address, shared: data.get('shared') === 'on'});
    dialog.close(); toast('Connected');
    return ctx.go('mailbox/' + r.mailbox.id);
  });
}

// ---------------------------------------------------------------------------------------------------------- Settings > Email
// The Connection card (ui/google-connect.js) draws into one #set-google kept across draws and visits: it holds a paused
// check, the way picked and typed values, so leaving Inboxes > Settings and coming back resumes them. First run's
// email step and the Settings card are never shown together, so they share it.
let MB_GOOGLE_HOST = null;
async function mbMountGoogle(slot) {
  if (!slot) return;
  if (!MB_GOOGLE_HOST) { MB_GOOGLE_HOST = document.createElement('div'); MB_GOOGLE_HOST.id = 'set-google'; }
  slot.replaceWith(MB_GOOGLE_HOST);
  await window.mountGoogleConnect?.(MB_GOOGLE_HOST);
}

inboxesSetting('email-connection', {section: 'email', order: 10,
  render: async (host, ctx) => {
    const d = await mbMailboxes(ctx).catch(() => ({}));
    if (!ctx.live()) return;
    // Your own mailbox when the company's Workspace key covers it; with Each person signs in, the card says it.
    const mine = d.mine && !d.admin && d.mine.connection === 'workspace' && d.mine.state === 'connected' ? d.mine : null;
    host.innerHTML = inboxesCard('mailboxes-top', ctx.admin ? 'Connection' : 'Your mailbox', `${mine ? `<div class="mb-mine" data-mb-mine-box>
        <p><strong>${esc(mine.address)}</strong> <span class="muted">· connected by your company</span></p>
        <p class="muted">Tico may ${esc(mbMayLine(d.may))}. Only you and your Assistant can use it.</p></div>` : ''}<div data-mb-google></div>`,
      `<span class="muted" data-mb-provider>Google Workspace${d.domain ? ' · ' + esc(d.domain) : ''}</span>`);
    await mbMountGoogle(host.querySelector('[data-mb-google]'));
  }});

inboxesSetting('email-may', {section: 'email', order: 20,
  render: async (host, ctx) => {
    if (!ctx.admin) { host.innerHTML = ''; return; }
    const save = MB.may;
    const d = await mbMailboxes(ctx);
    if (!ctx.live()) return;
    // A read from before or during a save never puts the old permissions back. Drawn controls stay as they are; a card
    // drawn afresh (Settings left and opened again) shows what the save in flight sends, locked, or what the last save
    // left. A draw during a save asks for another once it settles.
    const old = !!MB.pending || save !== MB.may;
    if (MB.pending) MB.refreshAfterMay = true;
    if (old && host.querySelector('[data-google-may]')) return;
    const values = MB.pending || (old && MB.known);
    const may = values ? Object.fromEntries(MB_MAY.map(([field, key]) => [key, values[field]])) : d.may || {};
    const box = ([field, key, label, note]) => `<label class="google-may-row"><input type="checkbox" data-google-may="${field}"${
      may[key] ? ' checked' : ''}${MB.pending ? ' disabled' : ''}><span>${label}${note ? `<span class="muted">${note}</span>` : ''}</span></label>`;
    host.innerHTML = inboxesCard('mailboxes-may', 'What Tico may do', `
      <div class="google-may-row" data-google-may-read><span class="google-may-always" aria-hidden="true"></span><span>Read<span class="muted">always</span></span></div>
      ${MB_MAY.map(box).join('')}<p class="muted">Each bot's own sending is in its settings.</p>`);
    host.onchange = event => { if (event.target.matches('[data-google-may]')) void mbSaveMay(event.target); };
  }});

// Each box saves on its own change, all three sent together (the server takes the whole setting), one save at a time.
// When it settles, whichever card shows now (the one clicked, or one drawn since) shows what the server kept. The
// Connection card's setup prompt names what Tico asks Google for, so it is drawn again.
const mbMayBoxes = () => [...document.querySelectorAll('#mailboxes-may [data-google-may]')];
async function mbSaveMay(input) {
  if (MB.pending) { input.checked = !input.checked; return; }
  const body = Object.fromEntries(mbMayBoxes().map(box => [box.dataset.googleMay, box.checked]));
  const before = {...body, [input.dataset.googleMay]: !input.checked};
  MB.may++; MB.pending = body;
  mbMayBoxes().forEach(box => { box.disabled = true; });
  let saved = false;
  try {
    await put('/v2/google/permissions', body);
    saved = true; toast('Saved');
    void window.mountGoogleConnect?.($('#set-google'), {force: true});
  } catch (e) { toast(e.message, true); }
  finally {
    MB.known = saved ? body : before; MB.pending = null;
    for (const box of mbMayBoxes()) { box.checked = !!MB.known[box.dataset.googleMay]; box.disabled = false; }
    if (MB.refreshAfterMay) { MB.refreshAfterMay = false; void renderSettingsInboxes(); }
  }
}

// ---------------------------------------------------------------------------------------------------------- First run
// Company email is connected once the Workspace key is in (connected, or failing and so on the list) or Each person
// signs in is set up, or any mailbox already reads.
async function mbEmailReady(ctx) {
  const [g, d] = await Promise.all([mbGoogle(ctx).catch(() => ({})), mbMailboxes(ctx).catch(() => ({}))]);
  const ws = g.workspace;
  return !!(ws && ws.status !== 'pending') || !!g.oauth?.configured
    || (d.mailboxes || []).some(m => m.state === 'connected');
}
inboxesStep('email', {order: 10, title: 'Connect company email', optional: false,
  done: mbEmailReady,
  render: async host => {
    host.innerHTML = '<div data-mb-google></div>';
    await mbMountGoogle(host.querySelector('[data-mb-google]'));
  }});
