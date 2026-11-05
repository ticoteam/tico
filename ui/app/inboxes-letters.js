/* ui/app/inboxes-letters.js — Letters in, in Settings > Inboxes: the company's mailing addresses and mail by email
   (backend/postal_mail.py, docs/physical-mail.md). Addresses and orders come from Tico Gateway (GET /v2/postal/addresses),
   letters from this server (GET /v2/postal/items). Kind 'postal': a row an address, an open order and the mail-by-email
   inbox (kept, as Not in use, while it is off and has letters); one address's page shows its Form 1583 progress and
   its letters (sender, summary, scan, task). Scan and forward requests are not offered: Tico Gateway does not take
   them. Ordering, the intake inbox, a retry, Read grants and legal-mail routing are an owner's or admin's; everyone
   else reads. An address's Who can use it gives Read on that address. In Inboxes > Settings > Letters in: Addresses
   (Get an address), Bring your own, Who can use it (Read on all addresses) and Legal mail. Registry: ui/app/inboxes.js.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const PM_STATES = [['DE', 'Delaware'], ['CA', 'California'], ['TX', 'Texas'], ['FL', 'Florida'], ['NV', 'Nevada']];
const PM_KINDS = {shared: 'Shared', private: 'Private'};
const PM_KIND_LINES = {shared: 'Mailing address and registered agent', private: 'Own suite number and registered agent'};
// Tico's prices (the plan's catalogue); Tico Gateway's `prices`, when it sends them, win. A scan covers 10 pages.
const PM_PRICES = {shared: '$10/mo', private: 'from $25/mo'};
const PM_SCAN = {scan: 1, page_over_10: 0.25};
const PM_ADDRESS_STATUS = {pending_1583: ['pending', 'Waiting for Form 1583'], active: ['ok', 'Connected'], closed: ['off', 'Closed']};
const PM_LETTER_KINDS = {legal: 'Legal', government_tax: 'Government or tax', bill: 'Bill', check: 'Check', financial: 'Financial',
  junk: 'Junk', other: 'Other'};
const PM_LETTER_STATUS = {new: ['waiting', 'Reading'], filed: ['ok', 'Filed'], archived: ['', 'Archived'], failed: ['fail', 'Failed']};
const PM_STEPS = [['ordered', 'Ordered'], ['pending_1583', 'Form 1583'], ['active', 'Receiving']];
// legalSave: one more each Legal mail save; legalSaving: one is in flight.
const PM = {addresses: null, orders: [], prices: {}, gatewayOff: false, error: '', legalSave: 0, legalSaving: false};

const pmStateName = code => (PM_STATES.find(([c]) => c === code) || [code, code])[1];
const PM_NONE = '<span class="muted pm-none">—</span>';
const pmPill = ([tone, label]) => `<span class="pill${tone ? ' ' + tone : ''}">${esc(label)}</span>`;
const pmDay = iso => iso ? new Date(iso).toLocaleDateString(undefined, {month: 'short', day: 'numeric'}) : '';
const pmNum = v => { const n = typeof v === 'string' && /^\d+(\.\d+)?$/.test(v.trim()) ? Number(v) : v;
  return typeof n === 'number' && Number.isFinite(n) && n > 0 ? n : null; };
const pmDollars = n => `$${Number.isInteger(n) ? n : n.toFixed(2)}`;
function pmPrice(kind) {
  const v = PM.prices?.[kind], n = pmNum(v);
  // Tico Gateway sends dollars a month; a plain label passes through as it is.
  if (n) return `${pmDollars(n)}/mo`;
  return typeof v === 'string' && v.trim() ? v : PM_PRICES[kind];
}
// Per letter, in dollars, from Tico Gateway when it sends them.
const pmScanLine = () => `Scans ${pmDollars(pmNum(PM.prices?.scan) || PM_SCAN.scan)} each, `
  + `${pmDollars(pmNum(PM.prices?.page_over_10) || PM_SCAN.page_over_10)} a page after 10`;
const pmId = a => String(a.assignment_id || a.id || '');
// What a Read grant on `a` names: its Tico Gateway assignment, or an older active address by its exact text.
const pmScope = a => pmId(a) || (a.status === 'active' && a.address) || '';
const pmName = a => (a.status === 'active' && a.address ? a.address : `${pmStateName(a.state)} · ${PM_KINDS[a.kind] || a.kind}`);
const pmNorm = s => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');

// The addresses and open orders, once a draw. With the gateway off there are none, and nothing is wrong.
async function pmLoad(ctx) {
  return ctx.once('postal', async () => {
    try {
      const d = await get('/v2/postal/addresses', {fresh: true});
      Object.assign(PM, {addresses: d.addresses || [], orders: d.orders || [], prices: d.prices || {}, gatewayOff: false, error: ''});
    } catch (e) {
      Object.assign(PM, {addresses: null, orders: [], gatewayOff: e.status === 409 && e.body?.error?.code === 'gateway', error: e.message});
    }
    return PM;
  });
}
const pmItems = ctx => ctx.once('postal-items', () => get('/v2/postal/items?limit=100', {fresh: true}).then(d => d.items || []));
const pmIntakeGet = ctx => ctx.once('postal-intake', () => get('/v2/postal/intake', {fresh: true}).then(d => d.intake || {}));
// Which row a letter belongs to: mail by email goes to the intake, a scan to the address it names, else to the only
// (or first) address, so every letter has a page.
function pmHome(item, addresses) {
  if (item.provider === 'email') return 'intake';
  const want = pmNorm(item.address), open = (addresses || []).filter(a => a.status !== 'closed');
  const hit = want && (addresses || []).find(a => pmNorm(pmId(a)) === want || (pmNorm(a.address) && (pmNorm(a.address).includes(want) || want.includes(pmNorm(a.address)))));
  return hit ? pmId(hit) : open[0] ? pmId(open[0]) : addresses?.[0] ? pmId(addresses[0]) : '';
}

inboxesKind('postal', {group: 'letters-in', label: 'Letters in', order: 40,
  rows: async ctx => {
    const [pm, items, intake] = await Promise.all([pmLoad(ctx), pmItems(ctx).catch(() => []), pmIntakeGet(ctx).catch(() => ({}))]);
    if (!pm.addresses && !pm.gatewayOff) throw new Error(pm.error);
    const latest = {};
    for (const m of items) { const home = pmHome(m, pm.addresses); if (home && m.received_at) latest[home] = Math.max(latest[home] || 0, Date.parse(m.received_at)); }
    const readers = {people: 1, bots: 0, names: ['Task readers']};
    const rows = (pm.addresses || []).map(a => {
      const [status, statusText] = PM_ADDRESS_STATUS[a.status] || ['pending', a.status || 'Pending'];
      return {id: pmId(a), name: pmName(a), sub: `${pmStateName(a.state)} · ${PM_KINDS[a.kind] || a.kind}`, status, statusText,
        latest: latest[pmId(a)] || null, usedBy: readers};
    });
    for (const o of pm.orders.filter(o => o.status === 'open')) {
      rows.push({id: 'order-' + (o.order_id || ''), name: `${pmStateName(o.state)} · ${PM_KINDS[o.kind] || o.kind}`, sub: 'Ordered',
        status: 'pending', statusText: 'Ordered', latest: null, usedBy: {people: 0, bots: 0, names: []}});
    }
    // Turned off, mail by email stays listed while it has letters, so a failed one can still be retried.
    const on = !!(intake.inbox_id || intake.hidden);
    if (on || items.some(m => m.provider === 'email')) {
      rows.push({id: 'intake', name: 'Mail by email', sub: !on ? 'Letters emailed earlier' : intake.hidden ? 'An inbox you can’t read' : intake.address || intake.inbox_id,
        status: on ? 'ok' : 'off', latest: latest.intake || null, usedBy: readers, search: (intake.senders || []).join(' ')});
    }
    return rows;
  },
  detail: (host, id, ctx) => pmDetail(host, id, ctx),
  add: {label: 'Mailing address', admin: true, open: ctx => pmOrderDialog(ctx)}});

// ---------------------------------------------------------------------------------------------------------- One address
async function pmDetail(host, id, ctx) {
  const [pm, intake] = await Promise.all([pmLoad(ctx), pmIntakeGet(ctx).catch(() => ({}))]);
  if (!ctx.live()) return null;
  const readers = '<p>Each letter goes to whoever may read the task it is filed on.</p>';
  const recent = '<section class="card" id="inbox-letters"><header><h2>Recent</h2></header><div data-pm-letters>' + skelRows(2, {dot: false}) + '</div></section>';
  if (id === 'intake') {
    if (!intake.inbox_id && !intake.hidden) {
      // Off: the letters it brought in, with Retry, and where to turn it on again.
      const items = await pmItems(ctx).catch(() => []);
      if (!ctx.live()) return null;
      if (!items.some(m => m.provider === 'email')) throw new Error('Mail by email is off');
      host.innerHTML = `${inboxesHead(host, {name: 'Mail by email', status: 'off', sub: 'Letters emailed earlier. New ones are not brought in.',
          actions: ctx.admin ? '<a class="ghost" href="#/settings/inboxes/settings/letters-in" data-pm-intake-on>Turn on</a>' : ''})}${recent}`;
      await pmLetters(host, ctx, 'intake');
      return {kind: 'postal', id, name: 'Mail by email', address: ''};
    }
    const address = intake.address || '';
    host.innerHTML = `${inboxesHead(host, {name: 'Mail by email', status: 'ok', sub: esc(intake.hidden ? 'An inbox you can’t read' : address),
        actions: ctx.admin ? '<button type="button" class="ghost danger" data-pm-intake-stop>Stop using in Tico</button>' : ''})}
      ${inboxesCard('inbox-access', 'Who can use it', `${readers}${(intake.senders || []).length
        ? `<p class="muted">From ${esc(intake.senders.join(', '))}</p>` : ''}`)}
      ${inboxesCard('inbox-automations', 'Automations', '<div data-pm-autos></div>', inboxesAutomationsLink(address))}${recent}`;
    host.querySelector('[data-pm-intake-stop]')?.addEventListener('click', () => void pmIntakeStop(ctx));
    void inboxesAutomations(ctx, address).then(h => { const s = host.querySelector('[data-pm-autos]'); if (s) s.innerHTML = h; });
    await pmLetters(host, ctx, 'intake');
    return {kind: 'postal', id, name: 'Mail by email', address};
  }
  if (id.startsWith('order-')) {
    const o = pm.orders.find(x => 'order-' + (x.order_id || '') === id);
    if (!o) throw new Error('No such address');
    host.innerHTML = `${inboxesHead(host, {name: `${pmStateName(o.state)} · ${PM_KINDS[o.kind] || o.kind}`, status: 'pending', statusText: 'Ordered'})}
      ${inboxesCard('inbox-provisioning', 'Setup', pmProgress('ordered'))}`;
    return {kind: 'postal', id, name: pmStateName(o.state), address: '', order: o};
  }
  if (!pm.addresses) throw new Error(pm.gatewayOff ? pm.error : pm.error || 'No such address');
  const a = pm.addresses.find(x => pmId(x) === id);
  if (!a) throw new Error('No such address');
  const [status, statusText] = PM_ADDRESS_STATUS[a.status] || ['pending', a.status || 'Pending'];
  const scope = ctx.admin ? pmScope(a) : '';
  const access = scope ? `<p class="muted">Read lets someone read every letter and scan here, private ones included.
    <a href="${inboxesLink('settings', 'letters-in')}">All addresses</a></p><div data-pm-grants-slot></div>` : '';
  host.innerHTML = `${inboxesHead(host, {name: pmName(a), status, statusText,
      sub: esc(`${pmStateName(a.state)} · ${PM_KINDS[a.kind] || a.kind} · Registered agent: ${a.agent ? 'Yes' : 'No'}`),
      actions: '<button type="button" class="ghost" data-pm-check>Check connection</button>'})}
    ${a.status !== 'active' ? inboxesCard('inbox-provisioning', 'Setup', pmProgress(a.status)) : ''}
    ${inboxesCard('inbox-access', 'Who can use it', readers + access)}
    ${inboxesCard('inbox-automations', 'Automations', '<div data-pm-autos></div>', inboxesAutomationsLink(a.address || ''))}${recent}`;
  host.querySelector('[data-pm-check]').addEventListener('click', async event => {
    const b = event.currentTarget; b.disabled = true;
    try { await ctx.refresh(); toast(PM.addresses ? (PM_ADDRESS_STATUS[a.status] || ['', a.status])[1] : PM.error, !PM.addresses); }
    finally { if (b.isConnected) b.disabled = false; }
  });
  void inboxesAutomations(ctx, a.address || '').then(h => { const s = host.querySelector('[data-pm-autos]'); if (s) s.innerHTML = h; });
  await Promise.all([pmLetters(host, ctx, id), scope && pmGrants(host.querySelector('[data-pm-grants-slot]'), scope, ctx)]);
  return {kind: 'postal', id, name: pmName(a), address: a.address || '', postal: a};
}

// Ordered, then Form 1583 signed and accepted, then receiving: where this address is.
function pmProgress(status) {
  const at = PM_STEPS.findIndex(([k]) => k === status);
  return `<ol class="pm-steps" data-pm-progress="${esc(status)}">${PM_STEPS.map(([k, label], n) =>
    `<li class="${n < at ? 'done' : n === at ? 'cur' : ''}"${n === at ? ' aria-current="step"' : ''}>${esc(label)}</li>`).join('')}</ol>
    ${status === 'pending_1583' ? '<p class="muted">Tico Gateway emails the Form 1583 to sign. Letters arrive once it is accepted.</p>' : ''}`;
}

async function pmLetters(host, ctx, home) {
  const slot = () => host.querySelector('[data-pm-letters]');
  let items;
  try { items = await pmItems(ctx); }
  catch (e) { if (slot()) slot().innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  if (!ctx.live() || !slot()) return;
  const mine = items.filter(m => pmHome(m, PM.addresses) === home);
  if (!mine.length) { slot().innerHTML = '<div class="empty">No letters yet.</div>'; return; }
  const admin = settingsIsAdmin();
  slot().innerHTML = `<table class="pm-table pm-letters" data-pm-letter-table><thead><tr><th>Date</th><th>From</th><th>Kind</th><th>Scan</th><th>Went to</th><th>Status</th></tr></thead><tbody>
    ${mine.map(m => `<tr data-pm-letter="${esc(m.id)}"><td class="pm-date tnum" title="${esc(fmt(m.received_at))}">${esc(pmDay(m.received_at))}</td>
      <td class="pm-from">${m.sender ? `<strong>${esc(m.sender)}</strong>` : ''}${m.summary ? `<span class="muted">${esc(m.summary)}</span>` : ''}${!m.sender && !m.summary ? '<span class="muted">Not read yet</span>' : ''}</td>
      <td class="pm-kind-cell">${m.kind ? esc(PM_LETTER_KINDS[m.kind] || m.kind) : PM_NONE}</td>
      <td class="pm-scan-cell">${m.file_url ? `<a href="${esc(m.file_url)}" target="_blank" rel="noopener" data-pm-pdf>PDF</a>` : PM_NONE}</td>
      <td class="pm-where">${m.task_id ? `<a href="#/task/${encodeURIComponent(m.task_id)}" data-pm-task>Task</a>` : m.status === 'archived' ? '<span class="muted">Archived</span>' : PM_NONE}</td>
      <td class="pm-status">${pmPill(PM_LETTER_STATUS[m.status] || ['', m.status || ''])}${m.status === 'failed' && admin
        ? ` <button class="ghost" type="button" data-pm-retry="${esc(m.id)}">Retry</button>` : ''}</td></tr>`).join('')}</tbody></table>`;
  slot().querySelectorAll('[data-pm-retry]').forEach(b => { b.onclick = async () => {
    b.disabled = true;
    try { await post(`/v2/postal/items/${encodeURIComponent(b.dataset.pmRetry)}/retry`, {}); toast('Retrying'); void ctx.refresh(); }
    catch (e) { b.disabled = false; settingsFieldError(b, e.message); }
  }; });
}

async function pmIntakeStop(ctx) {
  if (!await settingsConfirm({title: 'Stop using in Tico?', text: 'Letters emailed to this inbox stop becoming letters.', ok: 'Stop using'})) return;
  try { await put('/v2/postal/intake', {inbox_id: null, senders: []}); toast('Stopped'); return ctx.go(''); }
  catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------------------------------------------------- Read grants
// Read on an address (or on 'all') lets one person or bot read its letters and scans, private legal mail included,
// without the letter's task. Only an owner or admin sees, gives or removes it. A bot that can't take mail (its host only
// syncs chat) shows with the reason and can't be picked; the server refuses it too.
const PM_GRANTS = new Map();   // scope -> its block: kept across redraws, so a pick waiting for its button survives a poll
const pmMembers = ctx => ctx.once('postal-members', async () => {
  const bots = async () => {
    const all = []; let after = '';
    do {
      const page = await get('/v2/postal/bots?limit=100' + (after ? '&after=' + encodeURIComponent(after) : ''), {fresh: true});
      all.push(...(page.bots || [])); after = page.next || '';
    } while (after);
    return all;
  };
  const [b, p] = await Promise.all([bots(), get('/people').catch(() => ({people: []}))]);
  return [...b.filter(x => x.state === 'active').map(x => ({type: 'bot', id: x.id || x.slug, name: x.display_name || x.slug,
      readError: x.read_error || '', routingError: x.routing_error || ''})),
    ...(p.people || []).filter(x => !x.hidden).map(x => ({type: 'person', id: x.id, name: x.name || x.id}))];
});
const pmOption = (value, name, chosen = '', why = '') => `<option value="${esc(value)}"${value === chosen ? ' selected' : ''}${
  why ? ' disabled' : ''}>${esc(why ? `${name} — ${why}` : name)}</option>`;
const pmGrantPath = (scope, who) => {
  const [type, ...id] = who.split(':');
  return `/v2/postal/addresses/${encodeURIComponent(scope)}/grants/${type}/${encodeURIComponent(id.join(':'))}`;
};

// Puts `scope`'s grants block in `slot` (the same block as last draw, when there was one) and rereads its list.
async function pmGrants(slot, scope, ctx) {
  let block = PM_GRANTS.get(scope);
  if (!block) {
    block = document.createElement('div');
    block.className = 'pm-grants'; block.dataset.pmGrantsFor = scope;
    block.innerHTML = `<div data-pm-grants>${skelRows(1, {dot: false})}</div>
      <form class="pm-grant-form" data-pm-grant-form><label>Person or bot<select data-pm-grantee required><option value="">Choose…</option></select></label>
        <button type="submit" class="ghost">Give Read access</button></form><p class="err" data-pm-access-error role="status"></p>`;
    pmGrantsBind(block, scope);
    PM_GRANTS.set(scope, block);
  }
  if (slot && slot !== block) slot.replaceWith(block);
  const picker = block.querySelector('[data-pm-grantee]'), error = block.querySelector('[data-pm-access-error]');
  let members;
  try { members = await pmMembers(ctx); }
  catch (e) { if (ctx.live()) error.textContent = e.message; return; }
  if (!ctx.live() || !block.isConnected) return;
  block.pmMembers = members;
  if (!picker.value) picker.innerHTML = '<option value="">Choose…</option>'
    + members.map(m => pmOption(`${m.type}:${m.id}`, m.name, '', m.readError)).join('');
  await pmGrantsList(block, scope);
}

// The grants, a page at a time: a read that a newer one overtook draws nothing.
async function pmGrantsList(block, scope, after = '') {
  const list = block.querySelector('[data-pm-grants]'), error = block.querySelector('[data-pm-access-error]');
  const read = block.pmRead = (block.pmRead || 0) + 1;
  let d;
  try { d = await get(`/v2/postal/addresses/${encodeURIComponent(scope)}/grants?limit=50${after ? '&after=' + encodeURIComponent(after) : ''}`, {fresh: true}); }
  catch (e) { if (read === block.pmRead) error.textContent = e.message; return; }
  if (read !== block.pmRead) return;
  if (!after) list.replaceChildren();
  list.querySelector('[data-pm-more-grants]')?.remove();
  const name = g => (block.pmMembers || []).find(m => m.type === g.grantee_type && m.id === g.grantee_id)?.name || g.grantee_id;
  for (const g of d.grants || []) {
    const row = document.createElement('div');
    row.className = 'row pm-grant'; row.dataset.pmGrant = `${g.grantee_type}:${g.grantee_id}`;
    row.innerHTML = `<span>${esc(name(g))} · Read</span><button type="button" class="ghost" data-pm-revoke>Remove</button>`;
    list.append(row);
  }
  if (!list.querySelector('[data-pm-grant]')) list.innerHTML = '<p class="muted" data-pm-grants-empty>No one yet.</p>';
  if (d.next) list.insertAdjacentHTML('beforeend', `<button type="button" class="ghost" data-pm-more-grants="${esc(d.next)}">More</button>`);
}

function pmGrantsBind(block, scope) {
  const form = block.querySelector('[data-pm-grant-form]'), picker = form.querySelector('select');
  const error = block.querySelector('[data-pm-access-error]');
  block.addEventListener('click', async event => {
    const more = event.target.closest('[data-pm-more-grants]');
    if (more) { more.disabled = true; return pmGrantsList(block, scope, more.dataset.pmMoreGrants); }
    const revoke = event.target.closest('[data-pm-revoke]');
    if (!revoke) return;
    revoke.disabled = true; error.textContent = '';
    try { await writeRequest('DELETE', pmGrantPath(scope, revoke.closest('[data-pm-grant]').dataset.pmGrant)); await pmGrantsList(block, scope); }
    catch (e) { error.textContent = e.message; revoke.disabled = false; }
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!picker.value.includes(':')) return;
    const button = form.querySelector('button');
    button.disabled = true; error.textContent = '';
    try { await put(pmGrantPath(scope, picker.value), {level: 'read'}); picker.value = ''; await pmGrantsList(block, scope); }
    catch (e) { error.textContent = e.message; }
    finally { button.disabled = false; }
  });
}

// ---------------------------------------------------------------------------------------------------------- Order
function pmOrderDialog(ctx) {
  const state = PM_STATES[0][0], name = S.config?.company_name || '';
  const dialog = whDialog('Get an address', `<label>State<select name="state" data-pm-state>${PM_STATES.map(([c, n]) =>
      `<option value="${c}"${c === state ? ' selected' : ''}>${esc(n)}</option>`).join('')}</select></label>
    <div class="pm-kinds" role="radiogroup" aria-label="Kind">${Object.keys(PM_KINDS).map(k => `<label class="pm-kind" data-pm-kind="${k}">
      <input type="radio" name="kind" value="${k}"${k === 'shared' ? ' checked' : ''}>
      <span class="pm-kind-name">${esc(PM_KINDS[k])}</span><span class="pm-kind-price tnum" data-pm-price>${esc(pmPrice(k))}</span>
      <span class="pm-kind-line">${esc(PM_KIND_LINES[k])}</span></label>`).join('')}</div>
    <p class="muted pm-scan tnum" data-pm-scan>${esc(pmScanLine())}</p>
    <label>Company name<input name="company_name" type="text" maxlength="200" required autocomplete="organization" value="${esc(name)}"></label>`,
    '<button class="primary" type="submit" data-pm-submit>Order</button>');
  dialog.classList.add('pm-dialog');
  whSubmit(dialog, async data => {
    const body = {kind: data.get('kind'), state: data.get('state'), company_name: String(data.get('company_name') || '').trim()};
    if (!body.company_name) throw new Error('Enter the company name.');
    const r = await post('/v2/postal/orders', body);
    const made = r.order || {};
    PM.orders = [...PM.orders.filter(o => !made.order_id || o.order_id !== made.order_id),
      {order_id: made.order_id || made.id || '', kind: body.kind, state: body.state, ...made, status: made.status === 'open' || !made.status ? 'open' : made.status}];
    if (!PM.addresses) PM.addresses = [];
    dialog.close(); toast('Ordered');
    if (ctx) void ctx.refresh();
  });
}

// ---------------------------------------------------------------------------------------------------------- Settings
inboxesSetting('letters-addresses', {section: 'letters-in', order: 10,
  render: async (host, ctx) => {
    host.innerHTML = inboxesCard('settings-mail-addresses', 'Addresses', `<div data-pm-addresses>${skelRows(2, {card: true, dot: false})}</div>`,
      ctx.admin ? '<button class="primary settings-head-act" type="button" data-pm-order>Get an address</button>' : '');
    host.querySelector('[data-pm-order]')?.addEventListener('click', () => pmOrderDialog(ctx));
    await pmLoad(ctx);
    if (ctx.live()) pmAddressesDraw(host);
  }});

function pmAddressesDraw(card) {
  const host = card.querySelector('[data-pm-addresses]'); if (!host) return;
  const order = card.querySelector('[data-pm-order]');
  if (order) order.disabled = PM.gatewayOff;
  if (!PM.addresses) {
    host.innerHTML = PM.gatewayOff
      ? `<p class="muted" data-pm-gateway>${esc(PM.error.replace(/\.$/, ''))}. <a href="#/settings/models">Key and connection</a></p>`
      : `<p class="err">${esc(PM.error)}</p>`;
    return;
  }
  const open = PM.orders.filter(o => o.status === 'open');
  if (!PM.addresses.length && !open.length) { host.innerHTML = '<div class="empty">No addresses yet.</div>'; return; }
  const row = (key, id, link, state, kind, address, agent, status) => `<tr ${key}="${esc(id)}"><td class="pm-place"><a href="${link}">${esc(state)}</a></td>
    <td class="pm-kind-cell">${esc(PM_KINDS[kind] || kind)}</td><td class="pm-address">${address}</td>
    <td class="pm-agent">${agent}</td><td class="pm-status">${status}</td></tr>`;
  host.innerHTML = `<table class="pm-table" data-pm-address-table><thead><tr><th>State</th><th>Kind</th><th>Address</th>
      <th>Registered agent</th><th>Status</th></tr></thead><tbody>
    ${PM.addresses.map(a => row('data-pm-address', pmId(a), inboxesLink('postal', pmId(a)), pmStateName(a.state), a.kind,
      a.status === 'active' && a.address ? esc(a.address) : PM_NONE,
      `<span class="pm-agent-label">Registered agent: </span>${a.agent ? 'Yes' : 'No'}`,
      inboxesPill(...(PM_ADDRESS_STATUS[a.status] || ['pending', a.status || ''])))).join('')}
    ${open.map(o => row('data-pm-pending', o.order_id || '', inboxesLink('postal', 'order-' + (o.order_id || '')), pmStateName(o.state), o.kind, PM_NONE,
      '', inboxesPill('pending', 'Pending'))).join('')}</tbody></table>`;
}

inboxesSetting('letters-intake', {section: 'letters-in', order: 20,
  render: async (host, ctx) => {
    host.innerHTML = inboxesCard('settings-mail-intake', 'Bring your own', `<div data-pm-intake>${skelRows(1, {card: true, dot: false})}</div>`);
    await pmIntake(host.querySelector('[data-pm-intake]'), ctx);
  }});

async function pmIntake(host, ctx) {
  const [intake, inboxes] = await Promise.all([pmIntakeGet(ctx).catch(e => ({error: e.message})),
    get('/v2/inbound/inboxes', {fresh: true}).then(d => (d.inboxes || []).filter(i => i.kind === 'email'), () => null)]);
  if (!ctx.live() || !host.isConnected) return;
  if (intake?.error) { host.innerHTML = `<p class="err">${esc(intake.error)}</p>`; return; }
  const cur = intake?.inbox_id || '', line = '<p class="muted">PDFs and images emailed to this inbox from these senders become letters.</p>';
  const senders = (intake?.senders || []).join(', ');
  const list = [...(inboxes || [])];
  if (cur && !list.some(i => i.inbox_id === cur)) list.unshift({inbox_id: cur, name: '', address: intake.address || cur});
  if (!settingsIsAdmin()) {
    host.innerHTML = `${line}<p data-pm-intake-shown>${intake?.hidden ? 'An inbox you can’t read' : cur ? esc(intake.address || cur) : 'None'}</p>${cur && senders ? `<p class="muted" data-pm-intake-senders-shown>From ${esc(senders)}</p>` : ''}`;
    return;
  }
  if (!list.length) {
    host.innerHTML = `${line}<p class="muted" data-pm-no-inbox>No Tico address yet. <a href="#/settings/inboxes">Add inbox</a></p>`;
    return;
  }
  host.innerHTML = `${line}<label class="pm-intake">Inbox<select data-pm-intake-select>
      <option value="">None</option>${list.map(i => `<option value="${esc(i.inbox_id)}"${i.inbox_id === cur ? ' selected' : ''}>${esc(i.name ? `${i.name} · ${i.address}` : i.address)}</option>`).join('')}
    </select></label>
    <label class="pm-intake">Senders<input data-pm-intake-senders value="${esc(senders)}" placeholder="scans@provider.com, @provider.com" autocomplete="off"></label>`;
  const select = host.querySelector('[data-pm-intake-select]'), from = host.querySelector('[data-pm-intake-senders]');
  // A failed save goes back to what the server last took, not to what the page first loaded. Picking an inbox also
  // blurs the senders field, so one edit can fire both handlers: saves run one at a time and an unchanged one is skipped.
  let saved = {inbox: cur, senders}, busy = Promise.resolve();
  const save = field => (busy = busy.then(() => saveNow(field)));
  const saveNow = async (field) => {
    const list = [...new Set(from.value.toLowerCase().split(/[\s,;]+/).filter(Boolean))];
    settingsFieldClear(select); settingsFieldClear(from);
    if (select.value && !list.length) { settingsFieldError(from, 'Add your mail provider\'s address or @domain'); return; }
    if (select.value === saved.inbox && (select.value ? list.join(', ') : '') === saved.senders) return;
    select.disabled = from.disabled = true;
    try {
      const d = await put('/v2/postal/intake', {inbox_id: select.value || null, senders: select.value ? list : []});
      saved = {inbox: d.intake?.inbox_id || '', senders: (d.intake?.senders || []).join(', ')};
      from.value = saved.senders; toast('Saved');
    } catch (e) { select.value = saved.inbox; from.value = saved.senders; settingsFieldError(field, e.message); }
    finally { select.disabled = from.disabled = false; }
  };
  select.onchange = () => save(select);
  from.onchange = () => { if (select.value) save(from); };
}

inboxesSetting('letters-access', {section: 'letters-in', order: 30,
  render: async (host, ctx) => {
    if (!ctx.admin) { host.innerHTML = ''; return; }
    host.innerHTML = inboxesCard('settings-mail-access', 'Who can use it', `<p class="muted">Read on all addresses lets someone
      read every letter and scan, private ones included. An address’s own page gives Read on that address.</p><div data-pm-grants-slot></div>`);
    await pmGrants(host.querySelector('[data-pm-grants-slot]'), 'all', ctx);
  }});

// Legal mail: future legal letters go to the owner, or to an active bot an owner or admin picks; the owner hears of each
// either way. Existing tasks stay where they are. A read from before a save never replaces the choice during or after it.
inboxesSetting('letters-legal', {section: 'letters-in', order: 40,
  render: async (host, ctx) => {
    const save = PM.legalSave;
    const [d, members] = await Promise.all([get('/v2/postal/settings', {fresh: true}), ctx.admin ? pmMembers(ctx) : []])
      .catch(e => [{error: e.message}, []]);
    if (!ctx.live() || (PM.legalSaving || save !== PM.legalSave) && host.querySelector('[data-pm-legal]')) return;
    const chosen = d.settings?.legal_bot_id || '';
    const card = body => { host.innerHTML = inboxesCard('settings-mail-legal', 'Legal mail', body); };
    if (d.error) return card(`<p class="err">${esc(d.error)}</p>`);
    if (!ctx.admin) return card(`<p data-pm-legal-shown>Goes to ${esc(chosen || 'the owner')}</p>`);
    const bots = members.filter(m => m.type === 'bot');
    if (chosen && !bots.some(b => b.id === chosen)) bots.push({id: chosen, name: chosen});
    card(`<label class="pm-pick">Goes to<select data-pm-legal>${pmOption('', 'Owner', chosen)}${
        bots.map(b => pmOption(b.id, b.name, chosen, b.routingError)).join('')}</select></label>
      <p class="muted">The owner receives every legal letter.</p><p class="err" data-pm-legal-error role="status"></p>`);
    const select = host.querySelector('[data-pm-legal]'), error = host.querySelector('[data-pm-legal-error]');
    let saved = chosen;
    select.onchange = async () => {
      PM.legalSave++; PM.legalSaving = true; select.disabled = true; error.textContent = '';
      try { await put('/v2/postal/settings', {legal_bot_id: select.value || null}); saved = select.value; toast('Saved'); }
      catch (e) { select.value = saved; error.textContent = e.message; }
      finally { PM.legalSaving = false; select.disabled = false; }
    };
  }});

// ---------------------------------------------------------------------------------------------------------- First run
inboxesStep('letters-in', {order: 30, title: 'Receive letters', optional: true,
  done: async ctx => { const pm = await pmLoad(ctx); return !!(pm.addresses?.length || pm.orders.some(o => o.status === 'open')); },
  render: async (host, ctx, done) => {
    const pm = await pmLoad(ctx);
    host.innerHTML = done ? '<p class="muted">Ordered.</p>'
      : `<p><button type="button" class="ghost" data-pm-order${pm.gatewayOff ? ' disabled' : ''}>Get an address</button>${
        pm.gatewayOff ? ` <span class="muted">${esc(pm.error)}</span>` : ''}</p>`;
    host.querySelector('[data-pm-order]')?.addEventListener('click', () => pmOrderDialog(ctx));
  }});
