/* ui/app/settings-mail.js — Settings > Mail: the company's mailing addresses, ordering one, mail by email, recent letters
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// backend/postal_mail.py and docs/physical-mail.md. Addresses and orders come from Tico Gateway (GET /v2/postal/addresses),
// letters from this server (GET /v2/postal/items). Ordering, the intake inbox and a retry are an owner's or admin's;
// everyone else reads.
const PM_STATES = [['DE', 'Delaware'], ['CA', 'California'], ['TX', 'Texas'], ['FL', 'Florida'], ['NV', 'Nevada']];
const PM_KINDS = {shared: 'Shared', private: 'Private'};
const PM_KIND_LINES = {shared: 'Mailing address and registered agent', private: 'Own suite number and registered agent'};
// Tico's prices (the plan's catalogue); Tico Gateway's `prices`, when it sends them, win. A scan covers 10 pages.
const PM_PRICES = {shared: '$10/mo', private: 'from $25/mo'};
const PM_SCAN = {scan: 1, page_over_10: 0.25};
const PM_ADDRESS_STATUS = {pending_1583: ['waiting', 'Waiting for Form 1583'], active: ['ok', 'Active'], closed: ['', 'Closed']};
const PM_LETTER_KINDS = {legal: 'Legal', government_tax: 'Government or tax', bill: 'Bill', check: 'Check', financial: 'Financial',
  junk: 'Junk', other: 'Other'};
const PM_LETTER_STATUS = {new: ['waiting', 'Reading'], filed: ['ok', 'Filed'], archived: ['', 'Archived'], failed: ['fail', 'Failed']};
const PM = {addresses: null, orders: [], prices: {}, gatewayOff: false, error: '', run: 0};

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

async function renderSettingsMail() {
  const el = $('#set-mail'); if (!el) return;
  const run = ++PM.run, admin = settingsIsAdmin();
  if (!el.querySelector('[data-pm-addresses]')) {
    el.innerHTML = `<section class="card" id="settings-mail-addresses"><header><h2>Addresses</h2>
        ${admin ? '<div class="row"><button class="primary" type="button" data-pm-order>Get an address</button></div>' : ''}</header>
        <div data-pm-addresses>${skelRows(2, {card: true, dot: false})}</div></section>
      <section class="card" id="settings-mail-intake"><header><h2>Bring your own</h2></header><div data-pm-intake>${skelRows(1, {card: true, dot: false})}</div></section>
      <section class="card" id="settings-mail-letters"><header><h2>Recent letters</h2></header><div data-pm-letters>${skelRows(3, {card: true, dot: false})}</div></section>`;
    el.querySelector('[data-pm-order]')?.addEventListener('click', pmOrderDialog);
  }
  await Promise.all([pmAddresses(run), pmIntake(run), pmLetters(run)]);
}

// ---------------------------------------------------------------------------------------------------------- Addresses
async function pmAddresses(run) {
  try {
    const d = await get('/v2/postal/addresses', {fresh: true});
    Object.assign(PM, {addresses: d.addresses || [], orders: d.orders || [], prices: d.prices || {}, gatewayOff: false, error: ''});
  } catch (e) {
    Object.assign(PM, {addresses: null, orders: [], gatewayOff: e.status === 409 && e.body?.error?.code === 'gateway', error: e.message});
  }
  if (run === PM.run) pmAddressesDraw();
}
function pmAddressesDraw() {
  const host = $('#set-mail [data-pm-addresses]'); if (!host) return;
  const order = $('#set-mail [data-pm-order]');
  if (order) order.disabled = PM.gatewayOff;
  if (!PM.addresses) {
    host.innerHTML = PM.gatewayOff
      ? `<p class="muted" data-pm-gateway>${esc(PM.error.replace(/\.$/, ''))}. <a href="#/settings/models">Key and connection</a></p>`
      : `<p class="err">${esc(PM.error)}</p>`;
    return;
  }
  const open = PM.orders.filter(o => o.status === 'open');
  if (!PM.addresses.length && !open.length) { host.innerHTML = '<div class="empty">No addresses yet.</div>'; return; }
  const row = (key, id, state, kind, address, agent, status) => `<tr ${key}="${esc(id)}"><td class="pm-place">${esc(state)}</td>
    <td class="pm-kind-cell">${esc(PM_KINDS[kind] || kind)}</td><td class="pm-address">${address}</td>
    <td class="pm-agent">${agent}</td><td class="pm-status">${status}</td></tr>`;
  host.innerHTML = `<table class="pm-table" data-pm-address-table><thead><tr><th>State</th><th>Kind</th><th>Address</th>
      <th>Registered agent</th><th>Status</th></tr></thead><tbody>
    ${PM.addresses.map(a => row('data-pm-address', a.assignment_id || a.id || '', pmStateName(a.state), a.kind,
      a.status === 'active' && a.address ? esc(a.address) : PM_NONE,
      `<span class="pm-agent-label">Registered agent: </span>${a.agent ? 'Yes' : 'No'}`,
      pmPill(PM_ADDRESS_STATUS[a.status] || ['', a.status || '']))).join('')}
    ${open.map(o => row('data-pm-pending', o.order_id || '', pmStateName(o.state), o.kind, PM_NONE,
      '', pmPill(['waiting', 'Pending']))).join('')}</tbody></table>`;
}
function pmOrderDialog() {
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
    dialog.close(); toast('Ordered'); pmAddressesDraw();
  });
}

// ---------------------------------------------------------------------------------------------------------- Bring your own
async function pmIntake(run) {
  const host = $('#set-mail [data-pm-intake]'); if (!host) return;
  const [intake, inboxes] = await Promise.all([get('/v2/postal/intake', {fresh: true}).then(d => d.intake, e => ({error: e.message})),
    get('/v2/inbound/inboxes', {fresh: true}).then(d => (d.inboxes || []).filter(i => i.kind === 'email'), () => null)]);
  if (run !== PM.run) return;
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
    host.innerHTML = `${line}<p class="muted" data-pm-no-inbox>No email inbox yet. <a href="#/settings/inbound">Email &amp; Phone</a></p>`;
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

// ---------------------------------------------------------------------------------------------------------- Letters
async function pmLetters(run) {
  const host = $('#set-mail [data-pm-letters]'); if (!host) return;
  let items;
  try { items = (await get('/v2/postal/items?limit=25', {fresh: true})).items || []; }
  catch (e) { if (run === PM.run) host.innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  if (run !== PM.run) return;
  if (!items.length) { host.innerHTML = '<div class="empty">No letters yet.</div>'; return; }
  const admin = settingsIsAdmin();
  host.innerHTML = `<table class="pm-table pm-letters" data-pm-letter-table><thead><tr><th>Date</th><th>From</th><th>Kind</th><th>Went to</th><th>Status</th></tr></thead><tbody>
    ${items.map(m => `<tr data-pm-letter="${esc(m.id)}"><td class="pm-date tnum" title="${esc(fmt(m.received_at))}">${esc(pmDay(m.received_at))}</td>
      <td class="pm-from">${m.sender ? `<strong>${esc(m.sender)}</strong>` : ''}${m.summary ? `<span class="muted">${esc(m.summary)}</span>` : ''}${!m.sender && !m.summary ? '<span class="muted">Not read yet</span>' : ''}</td>
      <td class="pm-kind-cell">${m.kind ? esc(PM_LETTER_KINDS[m.kind] || m.kind) : PM_NONE}</td>
      <td class="pm-where">${m.task_id ? `<a href="#/task/${encodeURIComponent(m.task_id)}" data-pm-task>Task</a>` : m.status === 'archived' ? '<span class="muted">Archived</span>' : PM_NONE}</td>
      <td class="pm-status">${pmPill(PM_LETTER_STATUS[m.status] || ['', m.status || ''])}${m.status === 'failed' && admin
        ? ` <button class="ghost" type="button" data-pm-retry="${esc(m.id)}">Retry</button>` : ''}</td></tr>`).join('')}</tbody></table>`;
  host.querySelectorAll('[data-pm-retry]').forEach(b => { b.onclick = async () => {
    b.disabled = true;
    try { await post(`/v2/postal/items/${encodeURIComponent(b.dataset.pmRetry)}/retry`, {}); toast('Retrying'); void pmLetters(PM.run); }
    catch (e) { b.disabled = false; settingsFieldError(b, e.message); }
  }; });
}
