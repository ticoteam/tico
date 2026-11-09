/* ui/app/inboxes-email.js — The Google mailboxes Tico uses (backend/mailboxes.py), under Tools.
   Kind 'mailbox': one mailbox's page (#/tools/mailbox/<address or id>: who can use it, Read or Read and write, its key,
   its message automations, its recent mail, Check now and Stop using in Tico; the sending Limits card is
   ui/app/inboxes-sending.ts's), its Activity tab (#/tools/mailbox/<…>/activity: what was sent from it, GET
   /v2/email/sends) and Add tool > Email (Connect mailbox). On Tools › Settings › Email: the Connection card
   (ui/google-connect.js draws it into #set-google) and What Tico may do. Registry: ui/app/inboxes.ts.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

type MbMailbox = import('../types/api').Mailbox;
type MbMay = import('../types/api').MailboxMay;
type MbList = import('../types/api').MailboxList;
type MbDetail = import('../types/api').MailboxDetail & {domain?: string};
type MbAccess = MbDetail['accesses'][number];
type MbSend = import('../types/api').EmailSend & {decision?: {reasons?: string[]}};
type MbSendList = import('../types/api').EmailSendList;

// The one mailbox page's own expandos on its host: the page's ctx is set by each draw before a handler can run, and the
// mailbox it shows is set by the draw that rendered it.
interface HTMLElement {
  _mbCtx?: InboxCtx;
  _mb?: MbDetail | null;
}

// What Tico may do, kept here rather than in a card so leaving Settings and coming back mid-save keeps it: may counts the
// saves, pending holds what the one in flight sends (by PUT field), known what the last one left the server with, and
// refreshAfterMay that a draw waited for it.
interface MbState {
  run: number;
  may: number;
  pending: Record<string, boolean> | null;
  known: Record<string, boolean> | null;
  refreshAfterMay: boolean;
}
const MB: MbState = {run: 0, may: 0, pending: null, known: null, refreshAfterMay: false};
const MB_LEVELS: [string, string][] = [['read', 'Read'], ['read_write', 'Read and write']];
// What Tico may do (PUT /v2/google/permissions): the field the server keeps, the key GET /v2/mailboxes reads it back as.
const MB_MAY: [string, string, string, string][] = [['organize_mail', 'organize', 'Organize', 'label, archive, draft'], ['send_mail', 'send', 'Send', ''],
  ['change_calendar', 'change_calendar', 'Change calendar', '']];
const mbLevelName = (level: string): string => (MB_LEVELS.find(([k]) => k === level) || [level, level])[1];
const mbMailboxes = (ctx: InboxCtx): Promise<MbList> => ctx.once('mailboxes', () => get<MbList>('/v2/mailboxes', {fresh: true}));
const mbGoogle = (ctx: InboxCtx) => ctx.once('google', () => get('/v2/google', {fresh: true}));
// "read and organize", "read, organize and send": what Tico may do in a mailbox someone has Read and write on.
function mbMayLine(may: MbMay | null | undefined): string {
  const words = ['read', may?.organize && 'organize', may?.send && 'send', may?.change_calendar && 'change calendar'].filter(Boolean) as string[];
  return words.length > 1 ? `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}` : words[0] ?? '';
}
// One status for the list and the page: a connected mailbox a computer can't read needs attention, and a refused
// sign-in says so, since only a person signing in again fixes it.
function mbStatus(m: MbMailbox): [string, string] {
  if (m.state === 'stopped') return ['off', 'Not in use'];
  if (m.state === 'awaiting_person') return ['pending', 'Awaiting person'];
  if (m.state === 'not_connected') return ['attention', 'Not connected'];
  if (m.sync === 'signin') return ['attention', 'Sign-in expired'];
  if (m.sync === 'failed') return ['attention', 'Sync failed'];
  return ['ok', 'Connected'];
}
const mbWho = (m: MbMailbox): string => (m.kind === 'shared' ? 'Shared inbox' : m.person_id ? (m.person_name || m.person_id) : 'Awaiting person');
const mbSynced = (m: MbMailbox): string => (m.synced_at ? `Last sync ${ago(m.synced_at)}` : m.state === 'connected' ? 'Not synced yet' : '');

inboxesKind('mailbox', {label: 'Email', order: 10,
  detail: (host, id, ctx) => renderMailbox(host, id, ctx),
  activity: (host, id, ctx) => mbActivity(host, id, ctx),
  add: {label: 'Email (Google)', admin: true, open: ctx => mbConnectDialog(ctx)}});

// Tools' rows name a mailbox by its address (#/tools/mailbox/ana@acme.example); its own page reads it by id.
async function mbId(ctx: InboxCtx, ref: string): Promise<string> {
  if (!String(ref).includes('@')) return ref;
  const d = await mbMailboxes(ctx), want = String(ref).toLowerCase();
  const all = [...(d.mailboxes || []), d.mine, ...(d.shared || [])].filter(Boolean);
  const hit = all.find(m => String(m?.address).toLowerCase() === want);
  if (!hit) throw new Error('No mailbox here, or you cannot open it.');
  return hit.id;
}

// ---------------------------------------------------------------------------------------------------------- One mailbox
async function renderMailbox(host: HTMLElement, id: string, ctx: InboxCtx): Promise<InboxResolved | null> {
  const run = ++MB.run;
  host._mbCtx = ctx;   // the handlers below are bound once a page; each click uses the newest draw's ctx
  if (!host.dataset.mbBound) {
    host.dataset.mbBound = '1';
    host.addEventListener('click', event => { const b = eventElement(event)?.closest<HTMLButtonElement>('button'); const newest = host._mbCtx; if (b && newest) void mbOneClick(host, b, newest); });
    host.addEventListener('change', event => { const input = eventElement(event)?.closest<HTMLInputElement>('[data-mb-routine]'); if (input) void mbRoutine(input); });
    host.addEventListener('submit', event => {
      const form = eventElement(event)?.closest<HTMLFormElement>('[data-mb-add-form]');
      if (form) { event.preventDefault(); void mbAdd(host, form); }
    });
  }
  const d = await get<MbDetail>(`/v2/mailboxes/${encodeURIComponent(await mbId(ctx, id))}`, {fresh: true});
  if (run !== MB.run || !ctx.live()) return null;
  mbOneDraw(host, d);
  void mbRecent(host, d.mailbox, run);
  return {kind: 'mailbox', id: d.mailbox.id, name: d.mailbox.address, address: d.mailbox.address, ...d};
}

function mbLevelSeg(a: MbAccess): string {
  return `<div class="theme-seg" role="radiogroup" aria-label="Level for ${esc(a.name)}">${MB_LEVELS.map(([k, label]) =>
    `<button type="button" role="radio" aria-checked="${a.level === k}" data-mb-level="${k}" data-mb-access="${esc(a.id)}">${label}</button>`).join('')}</div>`;
}

// Who can use it: one row a person or bot, with their level (a segment for whoever manages it) and Remove.
function mbWhoRow(a: MbAccess, d: MbDetail): string {
  const face = a.grantee_type === 'bot' ? avatar(a.grantee_id || a.name, 22)
    : personAvatar((S.people || []).find(p => p.id === a.grantee_id) || {id: a.grantee_id, name: a.name}, 22);
  const edit = d.can_manage && !a.implicit;
  return `<tr data-mb-access-row="${esc(a.id || a.grantee_type)}">
      <td class="tt-who-name">${face}<span>${esc(a.name)}${a.grantee_type === 'bot' ? ' <span class="muted">· bot</span>' : ''}</span></td>
      <td>${edit ? mbLevelSeg(a) : esc(mbLevelName(a.level))}</td>
      <td class="mb-act">${edit ? `<button type="button" class="ghost tt-x" data-mb-remove="${esc(a.id)}" aria-label="Remove ${esc(a.name)}" title="Remove">×</button>` : ''}</td></tr>`;
}
// The key Tico reads it with: the company's Workspace key, or its person's own sign-in.
function mbKeysHTML(d: MbDetail): string {
  const m = d.mailbox, row = (dt: string, dd: string) => dd ? `<dt>${esc(dt)}</dt><dd>${dd}</dd>` : '';
  const key = m.connection === 'workspace' ? `Google Workspace${d.domain ? ' · ' + esc(d.domain) : ''}` : m.person_id ? `${esc(m.person_name || m.person_id)}'s Google sign-in` : 'Google sign-in';
  const allows = d.may ? esc(mbMayLine(d.may).replace(/^./, c => c.toUpperCase())) : '';
  return `<dl class="tt-kv">${row('Key', key)}${row('Allows', allows)}${row('Last sync', m.synced_at ? `<span title="${esc(fmt(m.synced_at))}">${esc(ago(m.synced_at))}</span>` : '')}</dl>`;
}

function mbOneDraw(host: HTMLElement, d: MbDetail): void {
  const m = d.mailbox, [status, statusText] = mbStatus(m), admin = settingsIsAdmin();
  // What Tico may do here for the person looking: Read only on a Read access, nothing when they have none.
  const may = m.level === 'read_write' ? mbMayLine(d.may) : m.level === 'read' ? 'read' : '';
  const line = [mbWho(m), mbSynced(m), may ? `Tico may ${may}` : ''].filter(Boolean).map(esc).join(' · ');
  const failing = m.sync && m.sync_error ? `<p class="err" data-mb-sync-error>${esc(m.sync_error)}</p>` : '';
  const signin = m.sync === 'signin' ? `<p class="muted" data-mb-signin>Sign in to Google again in <a href="#/tools/settings/email">Tools › Settings</a>.</p>` : '';
  const actions = [m.status === 'active' ? '<button type="button" class="ghost" data-mb-check>Check now</button>' : '',
    admin && m.status === 'active' && m.kind === 'personal' && !m.person_id ? '<button type="button" class="ghost" data-mb-share>Make shared inbox</button>' : '',
    d.can_stop ? '<button type="button" class="ghost danger" data-mb-stop>Stop using in Tico</button>' : ''].join('');
  const pick = d.candidates?.length ? `<form class="mb-form" data-mb-add-form hidden>
      <label class="google-field"><span>Who</span><select name="who">${d.candidates.map(x =>
        `<option value="${esc(x.type)}:${esc(x.id)}">${esc(x.name)}${x.type === 'bot' ? ' (bot)' : ''}</option>`).join('')}</select></label>
      <label class="google-field"><span>Level</span><select name="level">${MB_LEVELS.map(([k, label]) => `<option value="${k}">${label}</option>`).join('')}</select></label>
      <div class="row"><button type="submit" class="primary">Add</button><button type="button" class="ghost" data-mb-add-cancel>Cancel</button></div></form>` : '';
  const routines = d.routines.length ? `<ul class="mb-routines">${d.routines.map(r => `<li data-mb-routine-row="${esc(r.id)}">
      <a href="#/automations/${encodeURIComponent(r.id)}"><span class="nav-icon" aria-hidden="true">bolt</span>${esc(r.name)}</a>
      <label class="mb-on"><input type="checkbox" role="switch" class="people-switch" data-mb-routine="${esc(r.id)}" aria-label="${esc(r.name)} on"${
        r.enabled ? ' checked' : ''}${r.can_edit ? '' : ' disabled'}> On</label></li>`).join('')}</ul>` : '<p class="muted">None</p>';
  const recent = host.querySelector('[data-mb-recent]')?.innerHTML;
  host.innerHTML = `${inboxesHead(host, {name: m.address, status, statusText, sub: 'Mailbox · Google', actions})}
    <p class="muted mb-line" data-mb-status>${line}</p>${failing}${signin}
    <section class="card" id="mailbox-access"><header><h2>Who can use it</h2>
      ${d.can_manage && d.candidates?.length ? '<button class="ghost small settings-head-act" type="button" data-mb-add-open>Add</button>' : ''}</header>
      ${pick}
      <table class="settings-table mb-table tt-who-table"><tbody>${d.accesses.map(a => mbWhoRow(a, d)).join('')}</tbody></table>
      <p class="muted tt-foot">Assistants act as their person.</p></section>
    <section class="card" id="mailbox-keys"><header><h2>Keys</h2>${admin ? '<a class="ghost small settings-head-act" href="#/tools/settings/email">Settings</a>' : ''}</header>${mbKeysHTML(d)}</section>
    <section class="card" id="mailbox-routines"><header><h2>Automations</h2>${inboxesAutomationsLink(m.address)}</header>${routines}</section>
    <section class="card" id="mailbox-recent"><header><h2>Recent</h2>${m.level !== 'none'
      ? `<a class="ghost settings-head-act" href="#/mail?mailbox=${encodeURIComponent(m.address)}">Open mail</a>` : ''}</header>
      <div data-mb-recent>${recent || skelRows(2, {card: false, dot: false})}</div></section>`;
  host._mb = d;
}

// ---------------------------------------------------------------------------------------------------------- Activity
// What was sent from it (GET /v2/email/sends, newest first, a page at a time): when, who, to whom, and how it ended.
const MB_SEND_RESULT: Record<string, [string, string]> = {sent: ['Sent', 'ok'], waiting_approval: ['Waiting for OK', 'waiting'], pending: ['Waiting', 'waiting'],
  dispatching: ['Sending', 'in-progress'], unknown: ['Unknown', 'waiting'], blocked: ['Refused', 'fail'], canceled: ['Canceled', '']};
const MB_SEND_FILTERS: [string, string, (s: MbSend) => boolean][] = [['', 'All', () => true], ['sent', 'Sent', s => s.state === 'sent'],
  ['waiting', 'Waiting', s => ['waiting_approval', 'pending', 'dispatching', 'unknown'].includes(s.state)], ['refused', 'Refused', s => s.state === 'blocked']];
interface MbActState {
  rows: MbSend[];
  cursor: string;
  show: string;
}
async function mbActivity(host: HTMLElement, id: string, ctx: InboxCtx): Promise<InboxResolved | null> {
  const run = ++MB.run;
  const d = await get<MbDetail>(`/v2/mailboxes/${encodeURIComponent(await mbId(ctx, id))}`, {fresh: true});
  if (run !== MB.run || !ctx.live()) return null;
  const m = d.mailbox, [status, statusText] = mbStatus(m);
  const state: MbActState = {rows: [], cursor: '', show: ''};
  host.innerHTML = `${inboxesHead(host, {name: m.address, status, statusText, sub: 'Mailbox · Google'})}
    <div class="tt-bar"><div class="theme-seg" role="radiogroup" aria-label="Show" data-mb-act-filter>${MB_SEND_FILTERS.map(([k, label]) =>
      `<button type="button" role="radio" data-mb-act-show="${k}" aria-checked="${k === ''}">${label}</button>`).join('')}</div></div>
    <div data-mb-act>${skelRows(3)}</div><div class="row" data-mb-act-more hidden><button type="button" class="ghost">More</button></div>`;
  const box = host.querySelector<HTMLElement>('[data-mb-act]'), more = host.querySelector<HTMLElement>('[data-mb-act-more]');
  if (!box || !more) return null;
  const draw = () => {
    const hit = MB_SEND_FILTERS.find(([k]) => k === state.show);
    if (!hit) return;
    const rows = state.rows.filter(hit[2]);
    box.innerHTML = rows.length ? `<table class="tt-log"><thead><tr><th>Time</th><th>Who</th><th>Action</th><th>Message</th><th>Result</th></tr></thead><tbody>${rows.map(x => {
      const [word, tone] = MB_SEND_RESULT[x.state] || [x.state, ''];
      return `<tr data-mb-send="${esc(x.id)}" data-state="${esc(x.state)}"><td class="tnum" title="${esc(fmt(x.created_at))}">${esc(ago(x.created_at))}</td>
        <td>${esc(x.bot ? botDisplayName(x.bot) : x.actor ? actorLabel(x.actor) : '')}</td><td>Send</td>
        <td class="tt-log-what"><strong>${esc(x.subject || '(No subject)')}</strong><span class="muted">${esc((x.recipients || []).join(', '))}</span></td>
        <td><span class="pill${tone ? ' ' + tone : ''}">${esc(word)}</span>${x.state === 'blocked' && x.decision?.reasons?.length ? `<span class="muted tt-log-why">${esc(x.decision.reasons.join(', '))}</span>` : ''}</td></tr>`;
    }).join('')}</tbody></table>` : '<div class="empty" data-mb-act-none>Nothing yet.</div>';
    more.hidden = !state.cursor;
  };
  const load = async () => {
    const r = await get<MbSendList>(`/v2/email/sends?mailbox=${encodeURIComponent(m.address)}${state.cursor ? '&cursor=' + encodeURIComponent(state.cursor) : ''}`, {fresh: true});
    if (!ctx.live()) return;
    state.rows = state.rows.concat(r.sends || []); state.cursor = r.next_cursor || '';
    draw();
  };
  const filter = host.querySelector<HTMLElement>('[data-mb-act-filter]');
  if (!filter) return null;
  filter.onclick = ev => {
    const b = eventElement(ev)?.closest<HTMLButtonElement>('[data-mb-act-show]'); if (!b) return;
    state.show = String(b.dataset.mbActShow);
    host.querySelectorAll('[data-mb-act-show]').forEach(x => x.setAttribute('aria-checked', String(x === b)));
    draw();
  };
  const moreButton = more.querySelector<HTMLButtonElement>('button');
  if (!moreButton) return null;
  moreButton.onclick = () => void load().catch(e => toast(errorMessage(e, ''), true));
  try { await load(); } catch (e) { if (ctx.live()) box.innerHTML = `<div class="empty">${esc(errorMessage(e, ''))}</div>`; }
  return {kind: 'mailbox', id: m.id, name: m.address, address: m.address, ...d};
}

// The newest mail Tico holds for it, for whoever may read it (GET /v2/mail/messages refuses everyone else).
interface MbMessage {
  thread_id?: string | null;
  subject?: string | null;
  from_header?: string | null;
  from_addr?: string | null;
  epoch?: number | null;
}
async function mbRecent(host: HTMLElement, m: MbMailbox, run: number): Promise<void> {
  const slot = (): HTMLElement | null => host.querySelector<HTMLElement>('[data-mb-recent]');
  if (m.level === 'none') { const el = slot(); if (el) el.innerHTML = '<p class="muted">Only people who can use it see its mail.</p>'; return; }
  let d: {messages?: MbMessage[]};
  try { d = await get(`/v2/mail/messages?mailbox=${encodeURIComponent(m.address)}&limit=8`, {fresh: true}); }
  catch (e) { const el = slot(); if (run === MB.run && el) el.innerHTML = `<p class="muted">${esc(errorMessage(e, ''))}</p>`; return; }
  const el = slot();
  if (run !== MB.run || !el) return;
  const list = d.messages || [];
  el.innerHTML = list.length ? `<ul class="ib-recent">${list.map(x => `<li><span class="ib-recent-what">
      <a href="#/mail?mailbox=${encodeURIComponent(m.address)}&thread=${encodeURIComponent(x.thread_id || '')}">${esc(x.subject || '(no subject)')}</a>
      <span class="muted">${esc(x.from_header || x.from_addr || '')}</span></span>
      <span class="ib-recent-when">${x.epoch ? esc(ago(new Date(x.epoch * 1000).toISOString())) : ''}</span></li>`).join('')}</ul>`
    : '<p class="muted">No mail yet.</p>';
}

async function mbOneClick(host: HTMLElement, b: HTMLButtonElement, ctx: InboxCtx): Promise<void> {
  const d = host._mb, id = d?.mailbox?.id;
  if (!id) return;
  const base = `/v2/mailboxes/${encodeURIComponent(id)}`;
  const write = async (run: () => Promise<MbDetail>) => {
    b.disabled = true;
    try { const fresh = await run(); mbOneDraw(host, fresh); void mbRecent(host, fresh.mailbox, MB.run); }
    catch (e) { b.disabled = false; toast(errorMessage(e, ''), true); }
  };
  if (b.hasAttribute('data-mb-add-open')) { const f = host.querySelector<HTMLFormElement>('[data-mb-add-form]'); if (!f) return; f.hidden = false; f.querySelector<HTMLSelectElement>('select')?.focus(); return; }
  if (b.hasAttribute('data-mb-add-cancel')) { const form = b.closest<HTMLFormElement>('form'); if (form) form.hidden = true; return; }
  if (b.dataset.mbLevel && b.getAttribute('aria-checked') !== 'true') {
    return write(() => patch(`${base}/accesses/${encodeURIComponent(String(b.dataset.mbAccess))}`, {level: b.dataset.mbLevel}));
  }
  if (b.dataset.mbRemove) { const removed = String(b.dataset.mbRemove); return write(() => writeRequest('DELETE', `${base}/accesses/${encodeURIComponent(removed)}`)); }
  if (b.hasAttribute('data-mb-share')) return write(() => post(`${base}/share`, {}));
  if (b.hasAttribute('data-mb-check')) return mbCheck(host, b, d, ctx);
  if (b.hasAttribute('data-mb-stop')) {
    if (!await settingsConfirm({title: 'Stop using in Tico?', text: `Tico stops using ${d.mailbox.address}.`, ok: 'Stop using'})) return;
    b.disabled = true;
    try { await writeRequest('DELETE', base); toast('Stopped'); }
    catch (e) { b.disabled = false; toast(errorMessage(e, ''), true); return; }
    return ctx.go('');
  }
}

// Check connection: an admin asks Google whether the company's key reads a mailbox it covers; otherwise, and after,
// the page is read again so its sync status and last sync are current.
async function mbCheck(host: HTMLElement, b: HTMLButtonElement, d: MbDetail, ctx: InboxCtx): Promise<void> {
  const m = d.mailbox;
  b.disabled = true;
  try {
    if (settingsIsAdmin() && m.connection === 'workspace') {
      const r: {ok?: boolean; message?: string} = await post('/v2/google/workspace/check', {mailbox: m.address});
      toast(r.message || (r.ok ? 'Connected' : 'Not connected'), !r.ok);
    }
    const fresh = await get<MbDetail>(`/v2/mailboxes/${encodeURIComponent(m.id)}`, {fresh: true});
    if (!ctx.live()) return;
    mbOneDraw(host, fresh);
    void mbRecent(host, fresh.mailbox, MB.run);
    if (!(settingsIsAdmin() && m.connection === 'workspace')) {
      const [status, text] = mbStatus(fresh.mailbox);
      toast(text, status === 'attention');
    }
  } catch (e) { toast(errorMessage(e, ''), true); }
  finally { if (b.isConnected) b.disabled = false; }
}

async function mbAdd(host: HTMLElement, form: HTMLFormElement): Promise<void> {
  const who = form.querySelector<HTMLSelectElement>('[name=who]'), level = form.querySelector<HTMLSelectElement>('[name=level]');
  const button = form.querySelector<HTMLButtonElement>('[type=submit]'), mailbox = host._mb?.mailbox;
  if (!who || !level || !button || !mailbox) return;
  const [type, ...rest] = who.value.split(':');
  button.disabled = true;
  try {
    const d: MbDetail = await post(`/v2/mailboxes/${encodeURIComponent(mailbox.id)}/accesses`,
      {grantee_type: type, grantee_id: rest.join(':'), level: level.value});
    mbOneDraw(host, d);
    void mbRecent(host, d.mailbox, MB.run);
  } catch (e) { button.disabled = false; settingsFieldError(who, errorMessage(e, '')); }
}

async function mbRoutine(input: HTMLInputElement): Promise<void> {
  input.disabled = true;
  try { await patch(`/v2/automations/${encodeURIComponent(String(input.dataset.mbRoutine))}`, {enabled: input.checked}); }
  catch (e) { input.checked = !input.checked; toast(errorMessage(e, ''), true); }
  input.disabled = false;
}

// Add tool > Email: an admin connects a mailbox. A person's address is theirs; any other is a shared inbox when
// ticked, else it waits for its person.
function mbConnectDialog(ctx: InboxCtx): void {
  const dialog = whDialog('Connect mailbox', `<label>Address<input type="email" name="address" autocomplete="off" spellcheck="false" required></label>
    <label class="mb-check"><input type="checkbox" name="shared"> Shared inbox</label>`,
    '<button class="primary" type="submit" data-mb-connect>Connect</button>');
  dialog.classList.add('mb-dialog');
  whSubmit(dialog, async (data: FormData) => {
    const address = String(data.get('address') || '').trim();
    if (!address) throw new Error('Enter an address.');
    const r: import('../types/api').MailboxResult = await post('/v2/mailboxes', {address, shared: data.get('shared') === 'on'});
    dialog.close(); toast('Connected');
    return ctx.go('mailbox/' + r.mailbox.address);
  });
}

// ---------------------------------------------------------------------------------------------------------- Tools › Settings › Email
// The Connection card (ui/google-connect.js) draws into one #set-google kept across draws and visits: it holds a paused
// check, the way picked and typed values, so leaving Tools › Settings and coming back resumes them.
let MB_GOOGLE_HOST: HTMLElement | null = null;
async function mbMountGoogle(slot: HTMLElement | null): Promise<void> {
  if (!slot) return;
  if (!MB_GOOGLE_HOST) { MB_GOOGLE_HOST = document.createElement('div'); MB_GOOGLE_HOST.id = 'set-google'; }
  slot.replaceWith(MB_GOOGLE_HOST);
  await window.mountGoogleConnect?.(MB_GOOGLE_HOST);
}

inboxesSetting('email-connection', {section: 'email', order: 10,
  render: async (host: HTMLElement, ctx: InboxCtx) => {
    const d: Partial<MbList> = await mbMailboxes(ctx).catch((): Partial<MbList> => ({}));
    if (!ctx.live()) return;
    // Your own mailbox when the company's Workspace key covers it; with Each person signs in, the card says it.
    const mine = d.mine && !d.admin && d.mine.connection === 'workspace' && d.mine.state === 'connected' ? d.mine : null;
    host.innerHTML = inboxesCard('mailboxes-top', ctx.admin ? 'Connection' : 'Your mailbox', `${mine ? `<div class="mb-mine" data-mb-mine-box>
        <p><strong>${esc(mine.address)}</strong> <span class="muted">· connected by your company</span></p>
        <p class="muted">Tico may ${esc(mbMayLine(d.may))}. Only you and your Assistant can use it.</p></div>` : ''}<div data-mb-google></div>`,
      `<span class="muted" data-mb-provider>Google Workspace${d.domain ? ' · ' + esc(d.domain) : ''}</span>`);
    await mbMountGoogle(host.querySelector<HTMLElement>('[data-mb-google]'));
  }});

inboxesSetting('email-may', {section: 'email', order: 20,
  render: async (host: HTMLElement, ctx: InboxCtx) => {
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
    const may: Partial<Record<string, boolean>> = values ? Object.fromEntries(MB_MAY.map(([field, key]): [string, boolean | undefined] => [key, values[field]])) : d.may || {};
    const box = ([field, key, label, note]: [string, string, string, string]) => `<label class="google-may-row"><input type="checkbox" data-google-may="${field}"${
      may[key] ? ' checked' : ''}${MB.pending ? ' disabled' : ''}><span>${label}${note ? `<span class="muted">${note}</span>` : ''}</span></label>`;
    host.innerHTML = inboxesCard('mailboxes-may', 'What Tico may do', `
      <div class="google-may-row" data-google-may-read><span class="google-may-always" aria-hidden="true"></span><span>Read<span class="muted">always</span></span></div>
      ${MB_MAY.map(box).join('')}<p class="muted">Each bot's own sending is in its settings.</p>`);
    host.onchange = (event: Event) => { const input = eventElement(event)?.closest<HTMLInputElement>('[data-google-may]'); if (input) void mbSaveMay(input); };
  }});

// Each box saves on its own change, all three sent together (the server takes the whole setting), one save at a time.
// When it settles, whichever card shows now (the one clicked, or one drawn since) shows what the server kept. The
// Connection card's setup prompt names what Tico asks Google for, so it is drawn again.
const mbMayBoxes = (): HTMLInputElement[] => [...document.querySelectorAll<HTMLInputElement>('#mailboxes-may [data-google-may]')];
async function mbSaveMay(input: HTMLInputElement): Promise<void> {
  if (MB.pending) { input.checked = !input.checked; return; }
  const body: Record<string, boolean> = Object.fromEntries(mbMayBoxes().map((box): [string, boolean] => [String(box.dataset.googleMay), box.checked]));
  const before = {...body, [String(input.dataset.googleMay)]: !input.checked};
  MB.may++; MB.pending = body;
  mbMayBoxes().forEach(box => { box.disabled = true; });
  let saved = false;
  try {
    await put('/v2/google/permissions', body);
    saved = true; toast('Saved');
    void window.mountGoogleConnect?.($('#set-google'), {force: true});
  } catch (e) { toast(errorMessage(e, ''), true); }
  finally {
    MB.known = saved ? body : before; MB.pending = null;
    for (const box of mbMayBoxes()) { box.checked = !!MB.known[String(box.dataset.googleMay)]; box.disabled = false; }
    if (MB.refreshAfterMay) { MB.refreshAfterMay = false; void inboxesRender(); }
  }
}
