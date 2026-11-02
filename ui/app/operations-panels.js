/* ui/app/operations-panels.js — A record's panel on the Operations page (#/operations/<tab>?id=<id>), or a customer's
   (?customer=<contact>): beside the table on a computer, a full-screen sheet on a phone. It shows information; money and
   Finance documents appear only when their read answers, cut to the viewer, and a partial cut says "Your view".
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const opsSec = (title, inner, tools = '') => `<section class="ops-sec"><header class="ops-sec-h"><h3>${esc(title)}</h3>${tools}</header>${inner}</section>`;
const opsKv = (k, v) => v ? finKv(k, v) : '';
const opsLinks = rows => `<ul class="ops-links">${rows.map(([href, label, note = '']) =>
  `<li>${href ? `<a href="${esc(href)}">${esc(label)}</a>` : `<span>${esc(label)}</span>`}${note ? `<span class="muted">${note}</span>` : ''}</li>`).join('')}</ul>`;

// Opens what the address names, or closes the panel.
function opsSelect() {
  const state = OPS, panel = $('#ops-panel'), split = $('#ops-split');
  if (!state || !panel || !split) return;
  const key = state.sel ? 'id:' + state.sel : state.customer ? 'customer:' + state.customer : '', changed = key !== (state.panelKey || '');
  state.panelKey = key;
  if (state.sel) state.fromRow = state.sel;
  document.querySelectorAll('#ops-table tr[data-ops-row]').forEach(tr => tr.classList.toggle('ops-on', tr.dataset.opsRow === state.sel));
  const load = ++state.panelLoad;
  if (!state.sel && !state.customer) { panel.hidden = true; panel.innerHTML = ''; split.classList.remove('has-panel'); document.body.classList.remove('ops-sheet-open');
    // Back to the row it opened from.
    if (changed && state.fromRow) document.querySelector(`#ops-table tr[data-ops-row="${CSS.escape(state.fromRow)}"] a.ops-name`)?.focus({preventScroll: true});
    return;
  }
  panel.hidden = false; split.classList.add('has-panel');
  document.body.classList.add('ops-sheet-open');
  panel.innerHTML = `<div class="ops-panel-head"><span class="spacer"></span><a class="ghost ops-x" href="${opsHref(state.tab)}" aria-label="Close">✕</a></div>${skelRows(4, {dot: false})}`;
  // Into the panel when it opens; a reload of the same record leaves focus where it is.
  if (changed) panel.focus({preventScroll: true});
  void (state.customer ? opsCustomerLoad(state, load) : opsPanelLoad(state, load));
}
// A due date moved: the table's Next due catches up, the open panel and any More pages stay.
async function opsRowRefresh(state, id) {
  const one = OPS_ONE[state.tab], read = await get(`/v2/${state.tab}/${encodeURIComponent(id)}`, {fresh: true}).catch(() => null), row = read?.[one] || read;
  if (OPS !== state || !row?.id) return;
  const inactive = OPS_FILTERS[state.tab].inactive;
  state.rows = state.rows.map(r => r.id === id ? {...r, ...row} : r).filter(r => r.id !== id || !r.archived_at || inactive);
  opsTable(state);
}
const opsLive = (state, load) => OPS === state && state.panelLoad === load && !!$('#ops-panel');
async function opsPanelLoad(state, load) {
  const one = OPS_ONE[state.tab], listed = state.rows.find(r => r.id === state.sel);
  let row = listed;
  try {
    const read = await get(`/v2/${state.tab}/${encodeURIComponent(state.sel)}`, {fresh: true});
    row = read?.[one] || read || listed;
  } catch (e) {
    if (!opsLive(state, load)) return;
    if (!row) { $('#ops-panel').innerHTML = `<div class="ops-panel-head"><span class="spacer"></span><a class="ghost ops-x" href="${opsHref(state.tab)}" aria-label="Close">✕</a></div>${opsFail(e)}`; return; }
  }
  if (!opsLive(state, load)) return;
  opsPanelDraw(state, load, {...listed, ...row});
}
function opsPanelDraw(state, load, r) {
  const tab = state.tab, edit = opsEditor();
  const facts = {locations: opsLocationFacts, items: opsItemFacts, projects: opsProjectFacts, equipment: opsEquipmentFacts}[tab](r);
  $('#ops-panel').innerHTML = `<div class="ops-panel-head"><h2>${esc(r.name)}</h2>${opsSourceIcon(r)}<span class="spacer"></span><a class="ghost ops-x" href="${opsHref(tab)}" aria-label="Close">✕</a></div>
    ${r.archived_at ? '<p class="ops-off-note"><span class="pill">Inactive</span></p>' : ''}
    ${opsCopyLine(r) ? `<p class="ops-copy muted" data-ops-copy>${esc(opsCopyLine(r))}${opsStale(r)}</p>` : ''}
    <div class="ops-facts">${facts}</div>
    <div id="ops-m-files"></div>
    <div id="ops-panel-more"></div>
    ${edit ? `<div class="ops-panel-foot"><button type="button" class="ghost" data-ops-edit>Edit</button><button type="button" class="ghost" data-ops-archive>${r.archived_at ? 'Make active' : 'Make inactive'}</button></div>` : ''}`;
  const panel = $('#ops-panel'), on = (sel, run) => { const el = panel.querySelector(sel); if (el) el.onclick = run; };
  on('[data-ops-edit]', () => void opsForm(tab, r));
  on('[data-ops-archive]', async ev => {
    ev.target.disabled = true;
    try { await opsSetActive(tab, r, !!r.archived_at); }
    catch (e) { toast(e.message || 'Not saved', true); ev.target.disabled = false; }
  });
  void opsPanelMore(state, load, r);
}
// A customer's name opens the customer's panel, for any teammate; its documents show only to whoever sees receivables.
const opsCustomerFact = (tab, r) => r.customer_id && opsCustomerName(r) ? `<a href="${opsCustomerHref(tab, r.customer_id)}" data-ops-customer>${esc(opsCustomerName(r))}</a>` : '';
function opsLocationFacts(r) {
  const a = r.address || {};
  const address = [a.street, [a.city, a.state].filter(Boolean).join(', '), a.postal_code].filter(Boolean).map(esc).join('<br>');
  return opsKv('Kind', esc(opsLabel(OPS_LOCATION_KINDS, r.kind))) + opsKv('Address', address)
    + opsKv('Parent', r.parent_id && r.parent_name ? `<a href="${opsHref('locations', r.parent_id)}">${esc(r.parent_name)}</a>` : '')
    + opsKv('Customer', opsCustomerFact('locations', r)) + opsKv('In books', r.in_books == null ? '' : r.in_books ? 'Yes' : 'No');
}
function opsItemFacts(r) {
  const named = id => (OPS_REFS?.accounts || []).find(a => a.id === id)?.name || '';
  const accounts = [['Income account', 'income_account_id'], ['Expense account', 'expense_account_id'], ['Asset account', 'asset_account_id']]
    .map(([label, id]) => opsKv(label, esc(named(r[id])))).join('');
  return (r.description ? `<p class="ops-desc">${esc(r.description)}</p>` : '') + opsKv('Kind', esc(opsLabel(OPS_ITEM_KINDS, r.kind))) + opsKv('SKU', esc(r.sku || ''))
    + opsKv('Unit', esc(r.unit || '')) + opsKv('Price', opsMoney(r)) + opsKv('Taxable', r.taxable == null ? '' : r.taxable ? 'Yes' : 'No')
    + opsKv('On hand', r.quantity_on_hand == null ? '' : esc(String(r.quantity_on_hand)) + (r.quantity_synced_at ? ` <span class="muted">as of ${esc(opsDay(r.quantity_synced_at))}</span>` : ''))
    + accounts;
}
function opsProjectFacts(r) {
  return opsKv('Customer', opsCustomerFact('projects', r)) + opsKv('Status', esc(opsLabel(OPS_PROJECT_STATUSES, r.status)));
}
function opsEquipmentFacts(r) {
  const photo = r.photo_file_id ? `<a class="ops-photo" href="${API}/v2/files/${encodeURIComponent(r.photo_file_id)}" target="_blank" rel="noopener"><img src="${API}/v2/files/${encodeURIComponent(r.photo_file_id)}" alt="Photo of ${esc(r.name)}" loading="lazy"></a>` : '';
  return photo + opsKv('Kind', esc(opsLabel(OPS_EQUIPMENT_KINDS, r.kind))) + opsKv('Customer', opsCustomerFact('equipment', r))
    + opsKv('Location', r.location_id && opsLocationName(r) ? `<a href="${opsHref('locations', r.location_id)}">${esc(opsLocationName(r))}</a>` : '')
    + opsKv('Make', esc(r.make || '')) + opsKv('Model', esc(r.model || '')) + opsKv('Serial', esc(r.serial || ''))
    + opsKv('Installed', esc(opsDay(r.installed_on))) + opsKv('Warranty ends', esc(opsDay(r.warranty_ends_on)));
}

// ----------------------------------------------------------------- what each panel reads beyond the record
const OPS_DOC_READS = {
  po: ['Open POs', 'ops-m-pos', 'purchase-orders', 'orders', {}, ['draft', 'sent', 'approved', 'partial'], '/payables/orders'],
  estimate: ['Estimates', 'ops-m-est', 'estimates', 'estimates', {}, ['draft', 'sent', 'accepted'], '/receivables/estimates'],
  invoice: ['Invoices', 'ops-m-inv', 'finance/invoices', 'invoices', {status: 'unpaid'}, null, '/receivables'],
};
function opsPanelMore(state, load, r) {
  if (state.tab !== 'projects') void opsPanelFiles(state, load, r);
  const more = $('#ops-panel-more'), docs = key => { for (const kind of Object.keys(OPS_DOC_READS)) void opsPanelDocs(state, load, kind, key, r.id); };
  const hosts = '<div id="ops-m-pnl"></div><div id="ops-m-pos"></div><div id="ops-m-est"></div><div id="ops-m-inv"></div>';
  if (state.tab === 'locations') {
    more.innerHTML = hosts + '<div id="ops-m-equipment"></div><div id="ops-m-due"></div><div id="ops-m-tasks"></div>';
    void opsPanelPnl(state, load, r, 'location', 'ops-m-pnl');
    docs('location_id');
    void opsPanelEquipment(state, load, r);
    opsDueMount('ops-m-due', 'location', r.id, {onLoad: dues => opsPanelTasks(state, load, dues)});
  } else if (state.tab === 'equipment') {
    more.innerHTML = '<div id="ops-m-due"></div>';
    opsDueMount('ops-m-due', 'equipment', r.id, {onWrite: () => void opsRowRefresh(state, r.id)});
  } else if (state.tab === 'projects') {
    more.innerHTML = hosts;
    void opsPanelPnl(state, load, r, 'project', 'ops-m-pnl');
    docs('project_id');
  } else more.innerHTML = '';
}
function opsPut(state, load, id, html) { if (opsLive(state, load)) { const el = document.getElementById(id); if (el) el.innerHTML = html; } }
// This month on a location, all time on a project, from the same cut of the books the P&L reads; nothing when it is refused.
async function opsPanelPnl(state, load, r, by, host) {
  const window_ = by === 'project' ? {start: '2000-01-01', end: finToday()} : {};
  // This record's own read: a project outside the list's largest 200 still has its figures.
  const report = await get('/v2/finance/reports/pnl_by_account?' + opsQs({by, id: r.id, ...window_}), {fresh: true}).catch(() => null);
  if (!report) return;
  const your = opsPartial(report) ? ' <span class="pill" data-ops-your-view>Your view</span>' : '';
  const open = `<a href="${FIN_ROUTE}/pnl?split=${by}" data-ops-pnl>Open in P&amp;L</a>`;
  const title = by === 'project' ? 'P&L' : 'P&L this month';
  opsPut(state, load, host, `<section class="ops-sec"><header class="ops-sec-h"><h3>${esc(title)}</h3>${your}<span class="spacer"></span>${open}</header>
    ${finKv('Income', finUsd(report.income ?? 0))}${finKv(by === 'project' ? 'Costs' : 'Expenses', finUsd(report.expense ?? 0))}${finKv(by === 'project' ? 'Profit' : 'Net', finUsd(report.net ?? 0))}${by === 'project' ? opsPnlGross(report) + opsPnlAccounts(report) : ''}</section>`);
}
// As Finance's drill-down words them, when the read carries them.
const opsPnlGross = report => (report.cost_of_goods == null ? '' : finKv('Cost of goods', finUsd(report.cost_of_goods))) + (report.gross_margin == null ? '' : finKv('Gross margin', finUsd(report.gross_margin)));
const opsPnlAccounts = report => report?.rows?.length ? `<table class="fin-t ops-pnl-accounts" data-ops-pnl-accounts><tbody>${report.rows.map(x =>
  `<tr><td>${esc(x.label)}</td><td class="fin-n">${finUsd(x.amount)}</td></tr>`).join('')}</tbody></table>` : '';
// The open purchase orders, estimates and invoices that name this record on a line. A refused read shows nothing.
async function opsPanelDocs(state, load, kind, key, id) {
  const [title, host, path, list, params, open, route] = OPS_DOC_READS[kind];
  const read = await get(`/v2/${path}?${opsQs({[key]: id, limit: 50, ...params})}`, {fresh: true}).catch(() => null);
  const docs = (read?.[list] || []).filter(d => !open || open.includes(d.status));
  if (docs.length) opsPut(state, load, host, opsSec(title, opsDocLinks(docs, route)));
}
// The Library files this record links, drawn and changed as a contact's are (finance-contact.js).
async function opsPanelFiles(state, load, r) {
  const edit = opsEditor();
  if (!r.files?.length && !edit) { opsPut(state, load, 'ops-m-files', ''); return; }
  const files = await recordFileNames(r.files);
  if (!opsLive(state, load)) return;
  opsPut(state, load, 'ops-m-files', opsSec('Files', files.length ? recordFileRows(files, edit) : '<p class="muted ops-note">None yet</p>', edit ? '<span class="spacer"></span><button type="button" class="ghost ops-mini" data-file-add>+ Add</button>' : ''));
  const host = $('#ops-m-files');
  if (!host || !edit) return;
  const again = async () => {
    const read = await get(`/v2/${state.tab}/${encodeURIComponent(r.id)}`, {fresh: true}).catch(() => null);
    if (read && opsLive(state, load)) await opsPanelFiles(state, load, {...r, ...read[OPS_ONE[state.tab]]});
  };
  host.querySelector('[data-file-add]').onclick = () => void recordFilePick(OPS_ONE[state.tab], r.id, files, again);
  host.querySelectorAll('[data-file-remove]').forEach(b => { b.onclick = () => void recordFileRemove(b, again); });
}
// Each opens that document in Finance (finOrdPage and finDocPage read ?doc=).
const opsDocLinks = (docs, route) => opsLinks(docs.map(d => [finHref(route, {doc: d.id}), `${d.number || ''} ${d.contact?.name || d.customer?.name || ''}`.trim() || '—', finUsd(d.total)]));
async function opsPanelEquipment(state, load, r) {
  const read = await get('/v2/equipment?' + opsQs({location_id: r.id, limit: 50}), {fresh: true}).catch(() => null);
  const here = read?.equipment || [];
  if (here.length) opsPut(state, load, 'ops-m-equipment', opsSec('Equipment here', opsLinks(here.map(x => [opsHref('equipment', x.id), x.name, esc([x.make, x.model].filter(Boolean).join(' '))]))));
}
// A location's open tasks: the ones its due dates opened (tasks themselves carry no location).
function opsPanelTasks(state, load, dues) {
  const seen = new Set(), open = [];
  for (const d of dues) for (const o of d.history || []) if (o.task_id && !o.completed_at && !seen.has(o.task_id)) { seen.add(o.task_id); open.push([`#/task/${encodeURIComponent(o.task_id)}`, d.name, esc(opsDay(o.due_on))]); }
  opsPut(state, load, 'ops-m-tasks', open.length ? opsSec('Open tasks', opsLinks(open)) : '');
}

// ----------------------------------------------------------------- a customer's panel
// GET /v2/customers/{id}: the contact, its locations, equipment and due dates to every teammate; its open estimates and
// invoices to whoever sees receivables, null to anyone else.
async function opsCustomerLoad(state, load) {
  const tab = state.tab, close = `<a class="ghost ops-x" href="${opsHref(tab)}" aria-label="Close">✕</a>`;
  let read;
  try { read = await get(`/v2/customers/${encodeURIComponent(state.customer)}`, {fresh: true}); }
  catch (e) {
    if (opsLive(state, load)) $('#ops-panel').innerHTML = `<div class="ops-panel-head"><span class="spacer"></span>${close}</div>${opsFail(e)}`;
    return;
  }
  if (!opsLive(state, load)) return;
  const c = read.contact || {}, emails = (c.emails || []).map(m => `<a href="mailto:${esc(m)}">${esc(m)}</a>`).join('<br>');
  const list = (title, rows, row) => rows?.length ? opsSec(title, opsLinks(rows.map(row))) : '';
  const estimates = read.estimates || [], invoices = read.invoices?.invoices || [];
  $('#ops-panel').innerHTML = `<div class="ops-panel-head"><h2 data-ops-customer-panel>${esc(c.name || '')}</h2><span class="spacer"></span>${close}</div>
    ${emails ? `<div class="ops-facts">${opsKv('Email', emails)}</div>` : ''}
    ${list('Locations', read.locations, l => [opsHref('locations', l.id), l.name, esc(opsLabel(OPS_LOCATION_KINDS, l.kind))])}
    ${list('Equipment', read.equipment, x => [opsHref('equipment', x.id), x.name, esc([x.make, x.model].filter(Boolean).join(' '))])}
    ${list('Due dates', read.due_dates, opsCustomerDue)}
    ${estimates.length ? opsSec('Estimates', opsDocLinks(estimates, OPS_DOC_READS.estimate[6])) : ''}
    ${invoices.length ? opsSec('Invoices', opsDocLinks(invoices, OPS_DOC_READS.invoice[6])) : ''}`;
}
function opsCustomerDue(d) {
  const tab = {location: 'locations', equipment: 'equipment'}[d.subject_type];
  const note = [d.subject_type === 'contact' ? '' : d.subject_name, d.next_due_on ? opsDay(d.next_due_on) : 'Finished'].filter(Boolean).join(' · ');
  return [tab ? opsHref(tab, d.subject_id) : '', d.name, esc(note) + (d.status === 'overdue' ? ' <span class="pill fail">Overdue</span>' : '')];
}
