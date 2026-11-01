/* ui/app/operations-panels.js — A record's panel on the Operations page (#/operations/<tab>?id=<id>): beside the table on a
   computer, a full-screen sheet on a phone. It shows information; money and Finance documents appear only when their
   read answers, cut to the viewer, and a partial cut says "Your view". Rows the API has not linked to this record stay out.
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
  document.querySelectorAll('#ops-table tr[data-ops-row]').forEach(tr => tr.classList.toggle('ops-on', tr.dataset.opsRow === state.sel));
  const load = ++state.panelLoad;
  if (!state.sel) { panel.hidden = true; panel.innerHTML = ''; split.classList.remove('has-panel'); document.body.classList.remove('ops-sheet-open'); return; }
  panel.hidden = false; split.classList.add('has-panel');
  document.body.classList.add('ops-sheet-open');
  panel.innerHTML = `<div class="ops-panel-head"><span class="spacer"></span><a class="ghost ops-x" href="${opsHref(state.tab)}" aria-label="Close">✕</a></div>${skelRows(4, {dot: false})}`;
  void opsPanelLoad(state, load);
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
    <div class="ops-facts">${facts}</div>
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
function opsSources(r) {
  const list = Array.isArray(r.sources) ? r.sources : [];
  if (!list.length) return '';
  return opsSec('Sources', opsLinks(list.map(s => {
    const name = OPS_SOURCE_NAMES[String(s.system || '').toLowerCase()] || s.system || '';
    const note = s.available === false ? 'Not available' : s.as_of ? `as of ${esc(opsDay(s.as_of))}${s.stale ? ' · stale' : ''}` : '';
    return [s.url || '', name, note];
  })));
}
function opsLocationFacts(r) {
  const a = r.address || {};
  const address = [a.street || a.line1, a.line2, [a.city, a.state].filter(Boolean).join(', '), a.postal_code].filter(Boolean).map(esc).join('<br>');
  const parent = r.parent_id && (OPS_REFS?.locations || []).find(l => l.id === r.parent_id);
  return opsKv('Kind', esc(opsLabel(OPS_LOCATION_KINDS, r.kind))) + opsKv('Address', address)
    + opsKv('Parent', parent ? `<a href="${opsHref('locations', parent.id)}">${esc(parent.name)}</a>` : '')
    + opsKv('Customer', esc(opsCustomerName(r))) + opsKv('In books', r.books_pending ? 'Pending' : r.in_books == null ? '' : r.in_books ? 'Yes' : 'No') + opsSources(r);
}
function opsItemFacts(r) {
  const prices = Array.isArray(r.prices) && r.prices.length
    ? r.prices.map(p => `${opsMoney(p, p.amount ?? p.price)}${p.interval ? ' / ' + esc(p.interval) : ''}`).join('<br>') : opsMoney(r);
  const accounts = [['Income account', 'income_account_id', 'income_account'], ['Expense account', 'expense_account_id', 'expense_account'], ['Asset account', 'asset_account_id', 'asset_account']]
    .map(([label, id, name]) => opsKv(label, esc(r[name]?.name || r[name] || (r[id] ? r[id] : '')))).join('');
  return (r.description ? `<p class="ops-desc">${esc(r.description)}</p>` : '') + opsKv('Kind', esc(opsLabel(OPS_ITEM_KINDS, r.kind))) + opsKv('SKU', esc(r.sku || ''))
    + opsKv('Unit', esc(r.unit || '')) + opsKv('Price', prices) + opsKv('Taxable', r.taxable == null ? '' : r.taxable ? 'Yes' : 'No')
    + opsKv('On hand', r.quantity_on_hand == null ? '' : esc(String(r.quantity_on_hand)) + (r.quantity_synced_at ? ` <span class="muted">as of ${esc(opsDay(r.quantity_synced_at))}</span>` : ''))
    + accounts + opsDocs(r) + opsSources(r);
}
function opsProjectFacts(r) {
  return opsKv('Customer', esc(opsCustomerName(r))) + opsKv('Status', esc(opsLabel(OPS_PROJECT_STATUSES, r.status))) + opsSources(r);
}
function opsEquipmentFacts(r) {
  const photo = r.photo_file_id ? `<a class="ops-photo" href="${API}/v2/files/${encodeURIComponent(r.photo_file_id)}" target="_blank" rel="noopener"><img src="${API}/v2/files/${encodeURIComponent(r.photo_file_id)}" alt="Photo of ${esc(r.name)}" loading="lazy"></a>` : '';
  return photo + opsKv('Kind', esc(opsLabel(OPS_EQUIPMENT_KINDS, r.kind))) + opsKv('Customer', esc(opsCustomerName(r)))
    + opsKv('Location', r.location_id && opsLocationName(r) ? `<a href="${opsHref('locations', r.location_id)}">${esc(opsLocationName(r))}</a>` : '')
    + opsKv('Make', esc(r.make || '')) + opsKv('Model', esc(r.model || '')) + opsKv('Serial', esc(r.serial || ''))
    + opsKv('Installed', esc(opsDay(r.installed_on))) + opsKv('Warranty ends', esc(opsDay(r.warranty_ends_on))) + opsDocs(r) + opsSources(r);
}
const opsDocs = r => Array.isArray(r.docs) && r.docs.length ? opsSec('Docs', opsLinks(r.docs.map(d => [d.url || (d.id ? `#/docs/${encodeURIComponent(d.id)}` : ''), d.name || d.title || '']))) : '';

// ----------------------------------------------------------------- what each panel reads beyond the record
function opsPanelMore(state, load, r) {
  const more = $('#ops-panel-more');
  if (state.tab === 'locations') {
    more.innerHTML = '<div id="ops-m-pnl"></div><div id="ops-m-pos"></div><div id="ops-m-equipment"></div><div id="ops-m-due"></div><div id="ops-m-tasks"></div>';
    void opsPanelPnl(state, load, r, 'location', 'ops-m-pnl');
    void opsPanelOrders(state, load, r);
    void opsPanelEquipment(state, load, r);
    opsDueMount('ops-m-due', 'location', r.id, {onLoad: dues => opsPanelTasks(state, load, dues)});
  } else if (state.tab === 'equipment') {
    more.innerHTML = '<div id="ops-m-due"></div>';
    opsDueMount('ops-m-due', 'equipment', r.id);
  } else if (state.tab === 'projects') {
    more.innerHTML = '<div id="ops-m-pnl"></div><div id="ops-m-pos"></div><div id="ops-m-est"></div><div id="ops-m-inv"></div>';
    void opsPanelPnl(state, load, r, 'project', 'ops-m-pnl');
    void opsPanelOrders(state, load, r, 'project_id');
    void opsPanelDocs(state, load, r, 'ops-m-est', 'Estimates', `/v2/estimates?${opsQs({project_id: r.id, limit: 50})}`, 'estimates', x => [`${FIN_ROUTE}/receivables`, `${x.number || ''} ${x.contact?.name || x.customer?.name || ''}`.trim(), finUsd(x.total)]);
    void opsPanelDocs(state, load, r, 'ops-m-inv', 'Invoices', `/v2/finance/invoices?${opsQs({project_id: r.id, limit: 50})}`, 'invoices', x => [`${FIN_ROUTE}/receivables`, `${x.number || ''} ${x.customer?.name || x.contact?.name || ''}`.trim(), finUsd(x.total ?? x.amount)]);
  } else more.innerHTML = '';
}
function opsPut(state, load, id, html) { if (opsLive(state, load)) { const el = document.getElementById(id); if (el) el.innerHTML = html; } }
// This month on a location, all time on a project, from the same cut of the books the P&L reads; nothing when it is refused.
async function opsPanelPnl(state, load, r, by, host) {
  const window_ = by === 'project' ? {start: '2000-01-01', end: finToday()} : {};
  const report = await get('/v2/finance/reports/pnl_by?' + opsQs({by, top_n: 200, ...window_}), {fresh: true}).catch(() => null);
  if (!report) return;
  const row = (report.rows || []).find(x => x.key === r.id);
  const your = opsPartial(report) ? ' <span class="pill" data-ops-your-view>Your view</span>' : '';
  const open = `<a href="${FIN_ROUTE}/pnl">Open in P&amp;L</a>`;
  const title = by === 'project' ? 'P&L' : 'P&L this month';
  opsPut(state, load, host, `<section class="ops-sec"><header class="ops-sec-h"><h3>${esc(title)}</h3>${your}<span class="spacer"></span>${open}</header>
    ${finKv('Income', finUsd(row?.income ?? 0))}${finKv(by === 'project' ? 'Costs' : 'Expenses', finUsd(row?.expense ?? 0))}${finKv(by === 'project' ? 'Profit' : 'Net', finUsd(row?.net ?? 0))}</section>`);
}
async function opsPanelOrders(state, load, r, key = 'location_id') {
  const read = await get('/v2/purchase-orders?' + opsQs({[key]: r.id, status: 'open', limit: 50}), {fresh: true}).catch(() => null);
  const open = (read?.orders || []).filter(o => o[key] === r.id || (o[key + 's'] || []).includes(r.id));
  if (!open.length) return;
  opsPut(state, load, 'ops-m-pos', opsSec('Open POs', opsLinks(open.map(o => [`${FIN_ROUTE}/payables`, `${o.number || ''} ${o.contact?.name || ''}`.trim(), finUsd(o.total)]))));
}
async function opsPanelDocs(state, load, r, host, title, path, key, row) {
  const read = await get(path, {fresh: true}).catch(() => null);
  const docs = (read?.[key] || []).filter(d => d.project_id === r.id || (d.project_ids || []).includes(r.id));
  if (docs.length) opsPut(state, load, host, opsSec(title, opsLinks(docs.map(row))));
}
async function opsPanelEquipment(state, load, r) {
  const read = await get('/v2/equipment?' + opsQs({location_id: r.id, limit: 50}), {fresh: true}).catch(() => null);
  const here = (read?.equipment || []).filter(x => x.location_id === r.id);
  if (here.length) opsPut(state, load, 'ops-m-equipment', opsSec('Equipment here', opsLinks(here.map(x => [opsHref('equipment', x.id), x.name, esc([x.make, x.model].filter(Boolean).join(' '))]))));
}
// A location's open tasks: the ones its due dates opened (tasks themselves carry no location).
function opsPanelTasks(state, load, dues) {
  const seen = new Set(), open = [];
  for (const d of dues) for (const o of d.occurrences || []) if (o.task_id && !o.completed_at && !seen.has(o.task_id)) { seen.add(o.task_id); open.push([`#/task/${encodeURIComponent(o.task_id)}`, d.name, esc(opsDay(o.due_on))]); }
  opsPut(state, load, 'ops-m-tasks', open.length ? opsSec('Open tasks', opsLinks(open)) : '');
}
