/* ui/app/inboxes-texts.js — the inboxes Tico hosts itself (backend/inbound.py): Tico addresses (email Tico receives at
   its own domain, apart from Google email) and Texts in (US numbers that receive SMS only), in Settings > Inboxes.
   Kinds 'tico' and 'number': their rows, one inbox's page (#/settings/inboxes/<kind>/<id>: who reads it, its message
   automations, its recent messages with Reveal code, Check connection and Stop using in Tico) and a phone request's
   page while TICO prices and approves it. Add inbox makes a Tico address, or asks for a number (an admin). In
   Inboxes > Settings: the company connection (Email) and phone requests (Texts). Bodies are external text, never HTML.
   Registry: ui/app/inboxes.js.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const IN_PAGES = 10;   // pages of 100 read for one list; the API is paged and byte-capped
const IN_LIVE = ['requested', 'quoted', 'accepted', 'approved'];   // a phone request still open
let INBOUND_TIMER = null;
const inPrice = value => (value / 1000000).toLocaleString('en-US', {style: 'currency', currency: 'USD'});

async function inAll(path, key) {
  const first = await get(path, {fresh: true});
  const list = [...(first[key] || [])];
  let after = first.next;
  for (let n = 1; after && n < IN_PAGES; n++) {
    const more = await get(`${path}?after=${encodeURIComponent(after)}`, {fresh: true});
    list.push(...(more[key] || [])); after = more.next;
  }
  return {...first, [key]: list, more: !!after};
}
const inInboxes = ctx => ctx.once('inbound', () => inAll('/v2/inbound/inboxes', 'inboxes'));
const inRequests = ctx => ctx.once('phone-requests', () => (ctx.admin ? inAll('/v2/inbound/phone-requests', 'phone_requests')
  : Promise.resolve({phone_requests: []})));
// The newest message of each inbox, from one read of the newest 50.
const inLatest = ctx => ctx.once('inbound-latest', () => get('/v2/inbound/messages?limit=50', {fresh: true}).then(d => {
  const out = {};
  for (const m of d.messages || []) out[m.inbox_id] = Math.max(out[m.inbox_id] || 0, m.received_at * 1000);
  return out;
}, () => ({})));
function inReaders(i) {
  const groups = S.orgGroups || [], here = i.audience === 'team' ? i.group_id : '';
  return here ? groups.find(g => g.id === here)?.name || here : 'Everyone';
}
// Active is Connected everywhere in Inboxes; a sync the server reports failing needs attention.
function inStatus(i, d) {
  if (!d.configured) return ['attention', 'Not connected'];
  if (i.state === 'active') return d.error ? ['attention', 'Sync failed'] : ['ok', i.cancel_pending ? 'Renewal off' : 'Connected'];
  if (i.state === 'error') return ['attention', 'Sync failed'];
  if (['off', 'paused', 'released'].includes(i.state)) return ['off', i.state === 'released' ? 'Released' : 'Not in use'];
  return ['pending', 'Pending'];
}
function inSyncLine(d) {
  return !d.configured ? 'Tico addresses are not connected. Ask your owner to set them up.'
    : d.error ? 'Messages could not sync. Tico will retry.'
    : d.gaps?.length ? `${d.gaps.reduce((sum, g) => sum + g.count, 0)} messages expired before Tico reconnected.`
    : d.last_synced_at ? `Last synced ${new Date(d.last_synced_at * 1000).toLocaleString()}.` : 'Waiting for the first sync.';
}
function inRequestStatus(r) {
  const quoted = r.state === 'quoted' && r.price_micros > 0 && r.quoted_until > Date.now() / 1000;
  return {quoted, cancellable: IN_LIVE.includes(r.state),
    text: r.state === 'quoted' ? (quoted ? `Quote ${inPrice(r.price_micros)} / 30 days` : 'Quote expired')
      : r.state === 'requested' ? 'Awaiting quote' : r.state.charAt(0).toUpperCase() + r.state.slice(1)};
}

async function inRows(ctx, kind) {
  const [d, latest] = await Promise.all([inInboxes(ctx), inLatest(ctx)]);
  const rows = d.inboxes.filter(i => i.kind === kind && i.state !== 'released').map(i => {
    const [status, statusText] = inStatus(i, d);
    return {id: i.inbox_id, name: i.name || i.address, sub: i.name ? i.address : '', status, statusText,
      latest: latest[i.inbox_id] || null, usedBy: {people: 1, bots: 0, names: [inReaders(i)]}, search: i.used_for || ''};
  });
  if (kind === 'sms') {
    const r = await inRequests(ctx).catch(() => ({phone_requests: []}));
    for (const p of r.phone_requests.filter(p => IN_LIVE.includes(p.state))) {
      rows.push({id: p.request_id, name: p.name, sub: 'Phone request', status: 'pending', statusText: inRequestStatus(p).text,
        latest: null, usedBy: {people: 0, bots: 0, names: []}});
    }
  }
  return rows;
}

inboxesKind('tico', {group: 'email', label: 'Tico address', order: 20,
  rows: ctx => inRows(ctx, 'email'),
  detail: (host, id, ctx) => inDetail(host, id, ctx),
  add: {label: 'Tico address', admin: false, open: ctx => inCreateDialog(ctx)}});
inboxesKind('number', {group: 'texts', label: 'Texts in', order: 30,
  rows: ctx => inRows(ctx, 'sms'),
  detail: (host, id, ctx) => inDetail(host, id, ctx),
  add: {label: 'Phone number', admin: true, open: ctx => inPhoneDialog(ctx)}});

// ---------------------------------------------------------------------------------------------------------- Add inbox
function inCreateDialog(ctx) {
  const dialog = whDialog('New Tico address', '<label>Inbox name<input name="name" maxlength="80" required placeholder="Account logins"></label>',
    '<button class="primary" type="submit">Create email inbox</button>');
  dialog.id = 'inbound-create';
  whSubmit(dialog, async data => {
    const made = await post('/v2/inbound/inboxes', {kind: 'email', name: String(data.get('name') || '')});
    dialog.close(); toast('Created');
    return ctx.go(made?.inbox_id ? 'tico/' + made.inbox_id : '');
  });
}
const IN_PHONE_LINE = 'Monthly non-VoIP US numbers receive SMS only. Rentals require TICO approval until billing is available. '
  + 'Accepting a quote records your agreement to that price; it does not buy a number.';
function inPhoneDialog(ctx) {
  const dialog = whDialog('Request a US mobile number', `<p class="muted">${esc(IN_PHONE_LINE)}</p>
    <label>Name<input name="name" maxlength="80" required placeholder="Mobile number" value="Mobile number"></label>`,
    '<button class="primary" type="submit" id="inbound-phone">Request</button>');
  whSubmit(dialog, async data => {
    const r = await post('/v2/inbound/phone-requests', {name: String(data.get('name') || '') || 'Mobile number'});
    dialog.close(); toast('Phone request sent for TICO review. No number has been purchased.');
    const id = r?.phone_request?.request_id;
    return ctx.go(id ? 'number/' + id : '');
  });
}

// ---------------------------------------------------------------------------------------------------------- One inbox
async function inDetail(host, id, ctx) {
  clearInterval(INBOUND_TIMER);
  const d = await inInboxes(ctx);
  if (!ctx.live()) return null;
  const inbox = d.inboxes.find(i => i.inbox_id === id);
  if (!inbox) {
    const r = await inRequests(ctx).catch(() => ({phone_requests: []}));
    const request = r.phone_requests.find(p => p.request_id === id);
    if (!request) throw new Error('No such inbox');
    inRequestDraw(host, request, ctx);
    return {kind: 'number', id, name: request.name, address: '', request};
  }
  inDraw(host, inbox, d, ctx);
  return {kind: inbox.kind === 'sms' ? 'number' : 'tico', id, name: inbox.name || inbox.address, address: inbox.address, inbox};
}

function inDraw(host, i, d, ctx) {
  const admin = settingsIsAdmin(), [status, statusText] = inStatus(i, d);
  const sub = [i.name ? i.address : '', i.period_end ? `Paid through ${new Date(i.period_end * 1000).toLocaleDateString()}` : '',
    inSyncLine(d)].filter(Boolean).map(esc).join(' · ');
  const actions = ['<button type="button" class="ghost" data-in-check>Check connection</button>',
    i.kind !== 'sms' || admin ? '<button type="button" class="ghost" data-inbound-edit>Edit</button>' : '',
    admin && i.state !== 'released' && !(i.kind === 'sms' && i.cancel_pending)
      ? '<button type="button" class="ghost danger" data-inbound-release>Stop using in Tico</button>' : ''].join('');
  const groups = S.orgGroups || [], here = i.audience === 'team' ? i.group_id : '';
  const readers = admin ? `<select data-inbound-audience="${esc(i.inbox_id)}" aria-label="Readers of ${esc(i.name)}"><option value="">Everyone</option>
      ${groups.map(g => `<option value="${esc(g.id)}"${g.id === here ? ' selected' : ''}>${esc(g.name || g.id)}</option>`).join('')}</select>`
    : `<span data-in-readers>${esc(inReaders(i))}</span>`;
  host.innerHTML = `${inboxesHead(host, {name: i.name || i.address, status, statusText, sub, actions})}
    <section class="card" id="inbox-access"><header><h2>Who can use it</h2></header>
      <div class="ib-field"><span class="ib-field-label">Reads it</span><div>${readers}</div></div>
      <div class="ib-field"><span class="ib-field-label">Used for</span><div data-in-used-for>${i.used_for ? esc(i.used_for) : '<span class="muted">—</span>'}</div></div></section>
    <section class="card" id="inbox-automations"><header><h2>Automations</h2>${inboxesAutomationsLink(i.address)}</header><div data-in-autos>${skelRows(1, {dot: false})}</div></section>
    <section class="card" id="inbox-recent"><header><h2>Recent</h2></header>
      <form id="inbound-filter" class="inbound-filters">
        <label>From <input name="sender" maxlength="200"></label><label>Search messages <input name="q" type="search" maxlength="200"></label>
        <label><input name="has_code" type="checkbox"> Has a code</label><label><input name="spam" type="checkbox"> Include spam</label>
        <button class="ghost" type="submit">Filter</button></form>
      <div class="inbound-scroll"><table class="inbound-table"><thead><tr><th>Received</th><th>From</th><th>Message</th><th>Code</th></tr></thead><tbody id="inbound-messages"></tbody></table></div>
      <div class="row"><button type="button" class="ghost" id="inbound-newest">Newest</button><button type="button" class="ghost" id="inbound-older" hidden>Older messages</button></div>
      <p id="inbound-status" class="muted" role="status"></p></section>`;
  host._in = {inbox: i, before: 0, next: null};
  void inboxesAutomations(ctx, i.address).then(html => { const s = host.querySelector('[data-in-autos]'); if (s && ctx.live()) s.innerHTML = html; });
  inBind(host, ctx);
  const active = () => ctx.live() && host.isConnected && !host.closest('.settings-pane')?.hidden;
  void inMessages(host).catch(e => inFail(host, e));
  // New messages show while the page is open, as they did on the old table.
  INBOUND_TIMER = setInterval(() => {
    if (!active()) { clearInterval(INBOUND_TIMER); return; }
    if (document.hidden || formBusy(host.querySelector('#inbound-filter')) || host._in.before) return;
    void inMessages(host).catch(e => inFail(host, e));
  }, 15000);
}
const inFail = (host, error) => { const s = host.querySelector('#inbound-status'); if (s) s.textContent = error.message; };

async function inMessages(host) {
  const st = host._in, form = host.querySelector('#inbound-filter'); if (!form) return;
  const params = new URLSearchParams({limit: '50', inbox_ids: st.inbox.inbox_id});
  if (form.elements.q.value) params.set('q', form.elements.q.value);
  if (form.elements.sender.value) params.set('from', form.elements.sender.value);
  for (const name of ['has_code', 'spam']) if (form.elements[name].checked) params.set(name, 'true');
  if (st.before) params.set('before', String(st.before));
  const data = await get('/v2/inbound/messages?' + params, {fresh: true});
  if (host._in !== st || !host.isConnected) return;
  st.next = data.next;
  host.querySelector('#inbound-older').hidden = !st.next;
  host.querySelector('#inbound-messages').innerHTML = data.messages.length ? data.messages.map(m => `<tr>
      <td>${esc(new Date(m.received_at * 1000).toLocaleString())}</td><td>${esc(m.from)}</td>
      <td><button class="linkish" data-inbound-read="${esc(m.message_id)}">${esc(m.subject || m.preview || (m.retention_removed ? 'Removed by retention' : 'View message'))}</button>${
        m.subject ? `<p>${esc(m.preview)}</p>` : ''}${m.spam ? '<span class="muted">Spam</span>' : ''}${m.truncated ? '<span class="muted"> · Truncated</span>' : ''}</td>
      <td>${m.has_code ? `<button class="linkish" data-inbound-code="${esc(m.message_id)}">Reveal code</button>` : '—'}</td></tr>`).join('')
    : '<tr><td colspan="4">No messages match these filters.</td></tr>';
}

function inBind(host, first) {
  host._inCtx = first;   // bound once a page; each handler uses the newest draw's ctx
  if (host.dataset.inBound) return;
  host.dataset.inBound = '1';
  const ctx = {live: () => host._inCtx.live(), refresh: () => host._inCtx.refresh(), go: view => host._inCtx.go(view),
    once: (name, load) => host._inCtx.once(name, load), get admin() { return host._inCtx.admin; }};
  host.addEventListener('submit', event => {
    if (event.target.id !== 'inbound-filter') return;
    event.preventDefault(); host._in.before = 0; void inMessages(host).catch(e => inFail(host, e));
  });
  host.addEventListener('change', event => {
    const pick = event.target.closest('[data-inbound-audience]');
    if (pick) void inAudience(host, pick, ctx);
  });
  host.addEventListener('click', event => {
    const b = event.target.closest('button'); if (!b || !host._in && !host._inRequest) return;
    if (b.id === 'inbound-newest') { host._in.before = 0; void inMessages(host).catch(e => inFail(host, e)); }
    else if (b.id === 'inbound-older') { host._in.before = host._in.next; void inMessages(host).catch(e => inFail(host, e)); }
    else if (b.dataset.inboundRead || b.dataset.inboundCode) void inReveal(b.dataset.inboundRead || b.dataset.inboundCode, !!b.dataset.inboundCode).catch(e => inFail(host, e));
    else if (b.hasAttribute('data-in-check')) void inCheck(host, b, ctx);
    else if (b.hasAttribute('data-inbound-edit')) inEditDialog(host, ctx);
    else if (b.hasAttribute('data-inbound-release')) void inRelease(host, ctx);
    else if (b.dataset.inboundPhoneAccept || b.dataset.inboundPhoneCancel) void inRequestAct(host, b, ctx);
  });
}

// Who reads an inbox: the company, or one group and the groups under it. Admins change it here.
async function inAudience(host, pick, ctx) {
  pick.disabled = true;
  try {
    await put(`/v2/inbound/inboxes/${encodeURIComponent(pick.dataset.inboundAudience)}/audience`,
      pick.value ? {audience: 'team', group_id: pick.value} : {audience: 'company'});
    toast('Saved');
  } catch (error) { inFail(host, error); toast(error.message, true); await ctx.refresh(); }
  finally { pick.disabled = false; }
}

async function inReveal(id, code) {
  const dialog = document.createElement('dialog'); dialog.className = 'tmodal inbound-dialog';
  dialog.setAttribute('aria-label', code ? 'Verification code' : 'Incoming message');
  dialog.innerHTML = '<h2>Incoming message</h2><pre></pre><div class="row"><button type="button" class="ghost" data-more hidden>Read more</button><button type="button" class="ghost" data-close>Close</button></div>';
  document.body.append(dialog); dialog.onclose = () => dialog.remove(); dialog.querySelector('[data-close]').onclick = () => dialog.close(); dialog.showModal();
  let offset = 0;
  const load = async () => {
    const data = await get('/v2/inbound/messages/' + encodeURIComponent(id) + (code ? '/code' : '?offset=' + offset));
    dialog.querySelector('pre').textContent += code ? data.code || 'No code found.' : data.text;
    dialog.querySelector('[data-more]').hidden = data.next_offset === null || code;
    offset = data.next_offset;
  };
  dialog.querySelector('[data-more]').onclick = () => void load().catch(e => { dialog.querySelector('pre').textContent += '\n' + e.message; });
  await load();
}

// Check connection reads the inbox list again: whether the service is connected and when it last synced.
async function inCheck(host, b, ctx) {
  b.disabled = true;
  try {
    const d = await inAll('/v2/inbound/inboxes', 'inboxes');
    if (!host.isConnected) return;
    const i = d.inboxes.find(x => x.inbox_id === host._in.inbox.inbox_id);
    if (!i) return ctx.go('');
    inDraw(host, i, d, ctx);
    toast(inSyncLine(d), !d.configured || !!d.error);
  } catch (e) { toast(e.message, true); }
  finally { if (b.isConnected) b.disabled = false; }
}

function inEditDialog(host, ctx) {
  const i = host._in.inbox;
  const dialog = whDialog('Edit inbox', `<label>Inbox name<input name="name" maxlength="80" required value="${esc(i.name || '')}"></label>
    <label>Used for<input name="used_for" maxlength="500" value="${esc(i.used_for || '')}" placeholder="What accounts or workflows depend on this inbox?"></label>`,
    '<button class="primary" type="submit" data-in-save>Save</button>');
  whSubmit(dialog, async data => {
    await writeRequestOnce('PATCH', '/v2/inbound/inboxes/' + i.inbox_id, {name: String(data.get('name') || ''), used_for: String(data.get('used_for') || '')});
    dialog.close(); toast('Saved');
    return ctx.refresh();
  });
}

// Stop using in Tico: a Tico address is released for good; a number's renewal is cancelled and it receives until its
// paid period ends. An inbox something depends on asks for its address typed back.
async function inRelease(host, ctx) {
  const i = host._in.inbox;
  const warning = i.kind === 'sms' ? 'Cancel renewal? The number keeps receiving until the paid period ends.'
    : 'Release this address permanently? Future messages will no longer be delivered.';
  const send = async confirm => {
    await post('/v2/inbound/inboxes/' + i.inbox_id + '/release', {confirm});
    toast(i.kind === 'sms' ? 'Renewal cancelled' : 'Released');
    return i.kind === 'sms' ? ctx.refresh() : ctx.go('');
  };
  if (!i.used_for) {
    if (!await settingsConfirm({title: 'Stop using in Tico?', text: warning, ok: i.kind === 'sms' ? 'Cancel renewal' : 'Release'})) return;
    try { await send(''); } catch (e) { toast(e.message, true); }
    return;
  }
  const dialog = whDialog('Stop using in Tico?', `<p>${esc(warning)}</p><p class="muted">Used for: ${esc(i.used_for)}</p>
    <label>Type ${esc(i.address)} to confirm<input name="confirm" autocomplete="off" spellcheck="false" required></label>`,
    `<button class="primary people-danger" type="submit" data-in-release-ok>${i.kind === 'sms' ? 'Cancel renewal' : 'Release'}</button>`);
  whSubmit(dialog, async data => {
    const typed = String(data.get('confirm') || '').trim();
    if (typed !== i.address) throw new Error(`Type ${i.address} to confirm.`);
    await send(typed);
    dialog.close();
  });
}

// ---------------------------------------------------------------------------------------------------------- A phone request
function inRequestDraw(host, r, ctx) {
  const s = inRequestStatus(r);
  const actions = [s.quoted ? `<button type="button" class="primary" data-inbound-phone-accept="${esc(r.request_id)}">Accept quote</button>` : '',
    s.cancellable ? `<button type="button" class="ghost danger" data-inbound-phone-cancel="${esc(r.request_id)}">Cancel request</button>` : ''].join('');
  host.innerHTML = `${inboxesHead(host, {name: r.name, status: s.cancellable ? 'pending' : 'off', statusText: s.text, sub: 'Phone request', actions})}
    ${inboxesCard('inbox-request', 'Price', `<p>${r.price_micros > 0 ? esc(inPrice(r.price_micros)) + ' / 30 days' : 'Awaiting quote'}${
      r.quoted_until ? ` <span class="muted">· quote valid until ${esc(new Date(r.quoted_until * 1000).toLocaleString())}</span>` : ''}</p>
      <p class="muted">${esc(IN_PHONE_LINE)}</p>`)}`;
  host._in = null; host._inRequest = r;
  inBind(host, ctx);
}

async function inRequestAct(host, b, ctx) {
  const accept = !!b.dataset.inboundPhoneAccept, id = b.dataset.inboundPhoneAccept || b.dataset.inboundPhoneCancel;
  const r = (await inRequests(ctx).catch(() => ({phone_requests: []}))).phone_requests.find(p => p.request_id === id)
    || (host._inRequest?.request_id === id ? host._inRequest : null) || INBOUND_SETTINGS.requests.find(p => p.request_id === id);
  if (!r) return;
  const ok = await settingsConfirm(accept
    ? {title: 'Accept quote?', text: `Accept ${inPrice(r.price_micros)} per 30 days for ${r.name}? Incoming SMS is included. TICO must still approve the rental; this does not buy a number.`, ok: 'Accept', danger: false}
    : {title: 'Cancel request?', text: `Cancel the phone request for ${r.name}?`, ok: 'Cancel request'});
  if (!ok) return;
  b.disabled = true;
  try {
    await post('/v2/inbound/phone-requests/' + encodeURIComponent(id) + (accept ? '/accept' : '/cancel'), accept ? {price_micros: r.price_micros} : {});
    toast(accept ? 'Quote accepted' : 'Request cancelled');
  } catch (e) { toast(e.message, true); }
  finally { if (b.isConnected) b.disabled = false; }
  await ctx.refresh();
}

// ---------------------------------------------------------------------------------------------------------- Settings
// Email: the hosted service's state and, for an admin, joining a Tico company account's shared Inbox with a code.
inboxesSetting('tico-connection', {section: 'email', order: 30,
  render: async (host, ctx) => {
    const d = await inInboxes(ctx).catch(e => ({error: e.message, configured: false, inboxes: []}));
    if (!ctx.live()) return;
    host.innerHTML = inboxesCard('set-inbound', 'Tico addresses', `<p id="inbound-sync" class="muted" role="status">${esc(inSyncLine(d))}</p>
      ${ctx.admin ? `<details id="inbound-connection"><summary>Connect Inbox to Tico account/company</summary>
      <p>Get a connection code for your company from <a href="https://app.tico.dev/inbox" target="_blank" rel="noopener noreferrer">Inbox in the customer portal</a>. The code expires after five minutes.</p>
      <form id="inbound-connection-form"><label>Connection code <input name="code" type="password" autocomplete="off" spellcheck="false" minlength="43" maxlength="43" required></label>
        <button type="submit" class="ghost">Preview company</button>
        <div id="inbound-connection-preview" hidden><p id="inbound-connection-company"></p>
          <p>This workspace’s existing inboxes and messages will join the company’s shared Inbox. This workspace and its agents will also gain access to the company’s shared Inbox, including messages from other connected workspaces. Inbox IDs and addresses stay the same.</p>
          <p>Disconnecting in the portal stops future access. It cannot undo merged message history or remove copies already saved in a workspace.</p>
          <label><input name="consent" type="checkbox"> I agree to share Inbox in both directions with this company.</label>
          <button type="button" class="primary" id="inbound-connection-connect" disabled>Connect Inbox</button></div>
        <p id="inbound-connection-status" role="status"></p>
      </form></details>` : ''}`);
    if (ctx.admin) inConnectionBind(host, ctx);
  }});

function inConnectionBind(host, ctx) {
  const connectionForm = host.querySelector('#inbound-connection-form');
  let shown = null, generation = 0, busy = false;
  const input = connectionForm.elements.code, consent = connectionForm.elements.consent;
  const preview = host.querySelector('#inbound-connection-preview'), connect = host.querySelector('#inbound-connection-connect');
  const connectionStatus = host.querySelector('#inbound-connection-status'), submit = connectionForm.querySelector('[type="submit"]');
  const active = () => host.isConnected && !host.closest('.settings-pane')?.hidden;
  const clearPreview = () => {
    generation++; shown = null; preview.hidden = true; consent.checked = false; connect.disabled = true;
    host.querySelector('#inbound-connection-company').textContent = '';
  };
  input.oninput = () => { clearPreview(); connectionStatus.textContent = ''; };
  consent.onchange = () => { connect.disabled = busy || !shown || !consent.checked; };
  async function connectionAction(action) {
    if (busy) return;
    if (action === 'connect' && (!shown || !consent.checked)) return;
    if (action === 'connect' && shown.expires_at * 1000 <= Date.now()) {
      clearPreview(); connectionStatus.textContent = 'This code expired. Get a new code in the portal.'; return;
    }
    const code = input.value, target = shown?.company.company_id;
    if (action === 'preview') clearPreview();
    const pending = generation;
    busy = true; submit.disabled = true; connect.disabled = true;
    if (action === 'connect') { input.disabled = true; consent.disabled = true; }
    connectionStatus.textContent = action === 'preview' ? 'Checking company…' : 'Connecting Inbox…';
    try {
      const result = await post('/v2/inbound/company-connection', {action, code});
      if (!active() || generation !== pending || input.value !== code) return;
      if (action === 'preview') {
        shown = result;
        host.querySelector('#inbound-connection-company').textContent = `Company: ${result.company.name} · ${result.company.company_id}`;
        preview.hidden = false; connectionStatus.textContent = 'Review the company and shared Inbox access before connecting.';
      } else {
        if (result.company.company_id !== target) throw new Error('Could not confirm the selected company. Check the portal.');
        input.value = ''; clearPreview();
        connectionStatus.textContent = `Inbox connected to ${result.company.name} (${result.company.company_id}). Manage or disconnect it in the customer portal.`;
        const fresh = await inAll('/v2/inbound/inboxes', 'inboxes').catch(() => null);
        if (fresh && host.querySelector('#inbound-sync')) host.querySelector('#inbound-sync').textContent = inSyncLine(fresh);
      }
    } catch (error) {
      if (active() && generation === pending) {
        if ([403, 409, 422].includes(error.status)) clearPreview();
        connectionStatus.textContent = error.message;
      }
    } finally {
      busy = false; input.disabled = false; consent.disabled = false;
      if (active()) { submit.disabled = false; connect.disabled = !shown || !consent.checked; }
    }
  }
  connectionForm.onsubmit = event => { event.preventDefault(); void connectionAction('preview'); };
  connect.onclick = () => void connectionAction('connect');
}

// Texts: phone requests, for an admin. Asking for a number sends nothing to buy; TICO prices it and approves it.
const INBOUND_SETTINGS = {requests: []};
inboxesSetting('texts-numbers', {section: 'texts', order: 10,
  render: async (host, ctx) => {
    if (!ctx.admin) { host.innerHTML = ''; return; }
    const [d, r] = await Promise.all([inInboxes(ctx).catch(() => ({configured: false})), inRequests(ctx).catch(e => ({error: e.message, phone_requests: []}))]);
    if (!ctx.live()) return;
    INBOUND_SETTINGS.requests = r.phone_requests;
    const rows = r.phone_requests.map(p => {
      const s = inRequestStatus(p);
      return `<tr data-in-request="${esc(p.request_id)}"><td><a href="${inboxesLink('number', p.request_id)}">${esc(p.name)}</a></td><td>${esc(s.text)}</td>
        <td>${s.quoted ? `<button class="linkish" data-inbound-phone-accept="${esc(p.request_id)}">Accept quote</button> ` : ''}${
          s.cancellable ? `<button class="linkish" data-inbound-phone-cancel="${esc(p.request_id)}">Cancel request</button>` : '—'}</td></tr>`;
    }).join('');
    host.innerHTML = inboxesCard('inbound-phone-card', 'Numbers', `<p class="muted">${esc(IN_PHONE_LINE)}</p>
      ${r.error ? `<p class="err">${esc(r.error)}</p>` : ''}
      <div id="inbound-phone-requests" class="inbound-scroll">${rows ? `<table class="inbound-table"><thead><tr><th>Name</th><th>Status</th><th>Actions</th></tr></thead><tbody>${rows}</tbody></table>`
        : '<p class="muted">No phone requests yet.</p>'}</div>
      <p id="inbound-phone-status" class="muted" role="status">${r.more ? 'Showing the first 1,000 requests.' : ''}</p>`,
      `<button type="button" class="primary settings-head-act" data-in-phone-open${d.configured ? '' : ' disabled'}>Request a number</button>`);
    host.onclick = event => {
      const b = event.target.closest('button'); if (!b) return;
      if (b.hasAttribute('data-in-phone-open')) inPhoneDialog(ctx);
      else if (b.dataset.inboundPhoneAccept || b.dataset.inboundPhoneCancel) void inRequestAct(host, b, ctx);
    };
  }});

// ---------------------------------------------------------------------------------------------------------- First run
inboxesStep('phone', {order: 20, title: 'Phone number', optional: true,
  done: async ctx => {
    const [d, r] = await Promise.all([inInboxes(ctx), inRequests(ctx).catch(() => ({phone_requests: []}))]);
    return d.inboxes.some(i => i.kind === 'sms') || r.phone_requests.some(p => IN_LIVE.includes(p.state));
  },
  render: (host, ctx, done) => {
    host.innerHTML = done ? '<p class="muted">Requested.</p>' : '<p><button type="button" class="ghost" data-in-phone-open>Request a number</button></p>';
    host.onclick = event => { if (event.target.closest('[data-in-phone-open]')) inPhoneDialog(ctx); };
  }});
