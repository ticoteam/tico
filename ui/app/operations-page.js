/* ui/app/operations-page.js — The Operations section (#/operations): its head and tabs, each tab's table, search, filters,
   Show inactive, + Add and the record forms. A tab's address is #/operations/<tab>; ?id=<record> opens that record's panel
   (operations-panels.js) and ?customer=<contact> a customer's, so Back closes it and any page may link to one. Due dates are operations-due.js; Import CSV is
   operations-import.js.
   Reads GET /api/v2/{locations,items,projects,equipment}; every write is POST /api/v2/finance/actions (finAct), so gates,
   approvals and audit stay one system. Money shows only when the API returns it, and what the viewer sees is the API's cut.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const OPS_ROUTE = '#/operations';
const OPS_TABS = [['locations', 'Locations'], ['items', 'Products & services'], ['projects', 'Projects'], ['equipment', 'Equipment']];
// A tab's sidebar key is its name in the API, so Products & services is operations.products (backend/sidebar.py).
const opsTabKey = tab => 'operations.' + (tab === 'items' ? 'products' : tab);
const OPS_ONE = {locations: 'location', items: 'item', projects: 'project', equipment: 'equipment'};
const OPS_LOCATION_KINDS = [['store', 'Store'], ['office', 'Office'], ['warehouse', 'Warehouse'], ['plant', 'Plant'], ['customer_site', 'Customer site'],
  ['vehicle', 'Vehicle'], ['other', 'Other']];
const OPS_ITEM_KINDS = [['service', 'Service'], ['inventory', 'Inventory'], ['non_inventory', 'Non-inventory'], ['bundle', 'Bundle'], ['category', 'Category'],
  ['raw_material', 'Raw material'], ['assembly', 'Assembly']];
const OPS_EQUIPMENT_KINDS = [['owned', 'Owned'], ['customer', 'Customer']];
const OPS_PROJECT_STATUSES = [['open', 'Open'], ['closed', 'Closed']];
const OPS_SOURCE_ICONS = {quickbooks: 'account_balance', xero: 'account_balance', stripe: 'payments', square: 'store', shopify: 'shopping_cart', csv: 'description', tico: 'edit_note'};
const OPS_SOURCE_NAMES = {quickbooks: 'QuickBooks', xero: 'Xero', stripe: 'Stripe', square: 'Square', shopify: 'Shopify', csv: 'CSV import', tico: 'Tico'};
let OPS = null;           // the open page: {load, tab, sel, rows, next, money, refs, panelLoad}
const OPS_FILTERS = {};   // each tab's search and filters, kept while the person moves between tabs
let OPS_REFS = null;      // what the forms pick from and rows name: {customers, locations, accounts}, read once a visit

const opsHref = (tab, id = '') => `${OPS_ROUTE}/${tab}${id ? '?id=' + encodeURIComponent(id) : ''}`;
const opsCustomerHref = (tab, id) => `${OPS_ROUTE}/${tab}?customer=${encodeURIComponent(id)}`;
const opsQs = obj => new URLSearchParams(Object.entries(obj).filter(([, v]) => v != null && v !== '')).toString();
const opsLabel = (pairs, value) => (pairs.find(([v]) => v === value) || [])[1] || value || '';
const opsDay = s => s ? finDayLabel(s) : '';
const opsFail = e => `<p class="muted ops-note">${esc(e?.status === 403 ? 'Not in your view.' : e?.message || 'Did not load.')}</p>`;
// A route a sibling feature has not shipped answers 404: the same as having nothing yet.
const opsAbsent = e => e?.status === 404 || e?.status === 405;
// In the row's own currency: finUsd says dollars, so only USD goes through it.
function opsMoney(r, c = r.price) {
  if (c == null) return '';
  if (!r.currency || r.currency === 'USD') return finUsd(c);
  try { return esc((c / 100).toLocaleString('en-US', {style: 'currency', currency: r.currency})); }
  catch { return esc(`${(c / 100).toFixed(2)} ${r.currency}`); }
}
// Who holds the records right: Admins by default, anyone else by a grant (GET /v2/finance/access). The server decides.
const opsEditor = () => settingsIsAdmin() || !!finCapsOf(FIN_ACCESS, finMe())?.may('finance.records');
// Part of the company's lines: figures are labeled, never called a total.
const opsPartial = report => !!report?.excluded || !finCapsOf(FIN_ACCESS, finMe())?.company;
const opsSourceKey = r => String(r.source || 'tico').toLowerCase();
// A provider's copy says when it last synced; Tico's own record has no copy to date. as_of is null on a copy never synced.
function opsCopyLine(r) {
  const key = opsSourceKey(r), name = OPS_SOURCE_NAMES[key] || key;
  if (key === 'tico' || r.as_of === undefined) return '';
  return `${name} copy · ${r.as_of ? 'synced ' + ago(r.as_of) : 'not synced yet'}`;
}
function opsSourceIcon(r) {
  const key = opsSourceKey(r), name = OPS_SOURCE_NAMES[key] || key, says = opsCopyLine(r) || name;
  return `<span class="nav-icon ops-src" title="${esc(says)}" role="img" aria-label="${esc(says)}">${esc(OPS_SOURCE_ICONS[key] || 'description')}</span>`;
}
const opsStale = r => opsSourceKey(r) !== 'tico' && r.stale ? '<span class="ops-stale" data-ops-stale>May be out of date</span>' : '';
const opsSourceCell = r => opsSourceIcon(r) + opsStale(r);
const opsCustomerName = r => r.customer_name || '';
const opsLocationName = r => r.location_name || '';

// The tabs in this company's sidebar choice, keeping the one a link opened even when it is hidden. All hidden lists all:
// the page opened by a bookmark still has its tabs.
function opsTabsShown(current = '') {
  const shown = OPS_TABS.filter(([id]) => !rdHiddenKeys().has(opsTabKey(id)) || id === current);
  return shown.length ? shown : OPS_TABS;
}
function opsHead(tab) {
  return `<div class="strat-head rd-strat-head ops-head"><h1>Operations</h1>
    <nav class="tabs slim strat-tabs" aria-label="Operations">${opsTabsShown(tab).map(([id, label]) =>
      `<a href="${opsHref(id)}" data-ops-tab="${id}"${tab === id ? ' class="cur" aria-current="page"' : ''}>${esc(label)}</a>`).join('')}</nav></div>`;
}

// ----------------------------------------------------------------- the page
function pageOperations() {
  const [path, query] = (S.route || '').split('?');
  const tab = path.slice(OPS_ROUTE.length + 1).split('/')[0], ask = new URLSearchParams(query || '');
  const id = ask.get('id') || '', customer = ask.get('customer') || '';
  if (!OPS_TABS.some(([t]) => t === tab)) { location.replace(opsHref(opsTabsShown()[0][0])); return; }
  const main = $('#main');
  main.classList.add('strategy-layout', 'operations-layout');
  // Same tab: a panel opened or closed, and the table stays as it is.
  if (OPS?.tab === tab && $('#ops-body')) { OPS.sel = id; OPS.customer = customer; opsSelect(); return; }
  document.querySelectorAll('dialog.fin-dlg').forEach(d => d.close());
  const state = OPS = {load: 0, tab, sel: id, customer, rows: [], next: null, money: null, moneyPartial: false, panelLoad: 0, loaded: false};
  OPS_FILTERS[tab] ||= {q: '', inactive: false, customer: '', location: '', due: '', kind: '', status: ''};
  main.innerHTML = opsHead(tab) + `<div id="ops-body" class="ops-body">${skelRows(6, {dot: false})}</div>`;
  void (async () => {
    await opsRefs();
    if (OPS !== state) return;
    opsShell(state);
    await opsLoad(state);
  })();
}
function operationsStop() {
  if (OPS && !rdUnder(S.route || '', OPS_ROUTE)) { OPS = null; OPS_REFS = null; document.body.classList.remove('ops-sheet-open'); document.querySelectorAll('dialog.fin-dlg').forEach(d => d.close()); }
}
// Who may edit, the customers (every teammate reads their names), the locations, and the accounts for whoever holds the
// books. Each may be refused or absent; the page draws without it.
async function opsRefs(fresh = false) {
  if (OPS_REFS && !fresh) return OPS_REFS;
  const list = (promise, key) => promise.then(r => Array.isArray(r) ? r : r?.[key] || []).catch(() => []);
  const [, customers, locations] = await Promise.all([finProbe(), list(finPaged('/v2/customers', 'customers'), 'x'), list(finPaged('/v2/locations', 'locations'), 'x')]);
  // Accounts are for whoever may set them; a teammate holding only the records right never sees the picker.
  const accounts = finCapsOf(FIN_ACCESS, finMe())?.may('finance.book') ? await list(get('/v2/finance/chart', {fresh: true}), 'accounts') : [];
  return (OPS_REFS = {customers, locations, accounts});
}

// ----------------------------------------------------------------- the columns
const OPS_COLS = {
  locations: state => [['Name', r => opsNameCell(r)], ['Kind', r => esc(opsLabel(OPS_LOCATION_KINDS, r.kind))], ['Customer', r => esc(opsCustomerName(r))],
    ['City, State', r => esc([r.address?.city, r.address?.state].filter(Boolean).join(', '))], ...(state.rows.some(r => r.in_books != null) ? [['In books', r => r.in_books == null ? '' : r.in_books ? 'Yes' : 'No']] : []), ['Source', opsSourceCell]],
  // On hand shows once a row carries it.
  items: state => [['Name', r => opsNameCell(r)], ['SKU', r => esc(r.sku || '')], ['Kind', r => esc(opsLabel(OPS_ITEM_KINDS, r.kind))],
    ['Price', r => opsMoney(r), 'ops-n'],
    ...(state.rows.some(r => r.quantity_on_hand != null) ? [['On hand', r => r.quantity_on_hand == null ? '' : esc(String(r.quantity_on_hand)), 'ops-n']] : []), ['Source', opsSourceCell]],
  projects: state => {
    const money = state.money;
    const cell = field => r => { const m = money?.get(r.id); return m ? finUsd(m[field]) : ''; };
    return [['Name', r => opsNameCell(r)], ['Customer', r => esc(opsCustomerName(r))], ['Status', r => esc(opsLabel(OPS_PROJECT_STATUSES, r.status))],
      ...(money ? [['Income', cell('income'), 'ops-n'], ['Costs', cell('expense'), 'ops-n'], ['Profit', cell('net'), 'ops-n']] : []), ['Source', opsSourceCell]];
  },
  equipment: () => [['Name', r => opsNameCell(r)], ['Kind', r => esc(opsLabel(OPS_EQUIPMENT_KINDS, r.kind))], ['Customer', r => esc(opsCustomerName(r))], ['Location', r => esc(opsLocationName(r))],
    ['Make / model', r => esc([r.make, r.model].filter(Boolean).join(' '))], ['Serial', r => esc(r.serial || '')], ['Next due', opsNextDue], ['Source', opsSourceCell]],
};
const opsNameCell = r => `<a href="${opsHref(OPS.tab, r.id)}" class="ops-name">${esc(r.name)}</a>${r.archived_at ? ' <span class="pill">Inactive</span>' : ''}`;
function opsNextDue(r) {
  if (!r.next_due_on) return '';
  const when = r.next_due_status;
  return `<span class="${when === 'overdue' ? 'ops-overdue' : when === 'due' ? 'ops-soon' : ''}">${esc(opsDay(r.next_due_on))}</span>`;
}

// ----------------------------------------------------------------- the bar and the table
function opsFilterControls(state) {
  const f = OPS_FILTERS[state.tab], pick = (key, label, pairs, all) => `<select data-ops-f="${key}" aria-label="${label}">${grOpts(pairs, f[key], all)}</select>`;
  const customers = () => pick('customer', 'Customer', opsCustomers(), 'All customers');
  if (state.tab === 'locations') return customers() + pick('kind', 'Kind', OPS_LOCATION_KINDS, 'All kinds');
  if (state.tab === 'items') return pick('kind', 'Kind', OPS_ITEM_KINDS, 'All kinds');
  if (state.tab === 'projects') return pick('status', 'Status', OPS_PROJECT_STATUSES, 'All statuses') + customers();
  return customers() + pick('location', 'Location', opsLocations(), 'All locations')
    + pick('due', 'Due', [['soon', 'Due soon'], ['overdue', 'Overdue'], ['warranty', 'Warranty ending']], 'Any date');
}
function opsShell(state) {
  const f = OPS_FILTERS[state.tab], edit = opsEditor(), one = {locations: 'locations', items: 'products and services', projects: 'projects', equipment: 'equipment'}[state.tab];
  const importable = state.tab !== 'projects';
  $('#ops-body').innerHTML = `<div class="ops-bar"><label class="fin-search"><span class="nav-icon" aria-hidden="true">search</span><input type="search" data-ops-q value="${esc(f.q)}" placeholder="Search" aria-label="Search ${esc(one)}"></label>
      <span class="ops-filters" id="ops-filters">${opsFilterControls(state)}</span>
      <label class="ops-inline"><input type="checkbox" data-ops-inactive${f.inactive ? ' checked' : ''}><span>Show inactive</span></label>
      <span class="spacer"></span>
      ${edit && importable ? '<button type="button" class="ghost" data-ops-import>Import CSV</button>' : ''}
      ${edit && state.tab === 'equipment' ? '<button type="button" class="ghost" data-ops-photo>+ Add from photo</button><input type="file" accept="image/*" data-ops-photo-file hidden aria-label="Data plate photo">' : ''}
      ${edit ? '<button type="button" class="primary" data-ops-add>+ Add</button>' : ''}</div>
    <div class="ops-split" id="ops-split"><div class="ops-main"><div class="ops-scroll" id="ops-table">${skelRows(6, {dot: false})}</div></div><aside class="ops-panel" id="ops-panel" aria-label="Details" tabindex="-1" hidden></aside></div>`;
  const body = $('#ops-body');
  let timer = 0;
  body.querySelector('[data-ops-q]').oninput = ev => { clearTimeout(timer); timer = setTimeout(() => { f.q = ev.target.value.trim(); void opsLoad(state); }, 300); };
  body.querySelector('[data-ops-inactive]').onchange = ev => { f.inactive = ev.target.checked; void opsLoad(state); };
  body.onchange = ev => { const k = ev.target.dataset?.opsF; if (k) { f[k] = ev.target.value; void opsLoad(state); } };
  // Esc closes the panel; the search box keeps Esc for itself, and an open dialog sits outside this body.
  body.onkeydown = ev => { if (ev.key === 'Escape' && !ev.target.matches?.('input,select,textarea') && (state.sel || state.customer)) location.hash = opsHref(state.tab); };
  const on = (sel, run) => { const el = body.querySelector(sel); if (el) el.onclick = run; };
  on('[data-ops-add]', () => opsForm(state.tab, null));
  on('[data-ops-import]', () => opsImportOpen(state.tab));
  on('[data-ops-photo]', () => body.querySelector('[data-ops-photo-file]').click());
  const photo = body.querySelector('[data-ops-photo-file]');
  if (photo) photo.onchange = () => { const file = photo.files[0]; photo.value = ''; if (file) void opsPhotoDraft(file); };
  $('#ops-table').onclick = ev => {
    if (ev.target.closest('a,button')) return;
    const tr = ev.target.closest('tr[data-ops-row]');
    if (tr) location.hash = opsHref(state.tab, tr.dataset.opsRow);
  };
}
async function opsLoad(state, more = false) {
  const f = OPS_FILTERS[state.tab], load = ++state.load;
  const params = {q: f.q, archived: f.inactive ? 'all' : 'hide', limit: 50, cursor: more ? state.next : '', customer_id: f.customer, kind: f.kind, status: f.status};
  if (state.tab === 'equipment') {
    params.location_id = f.location;
    if (f.due === 'warranty') params.warranty = 'ending';
    else params.due = f.due;
  }
  let page, money = state.money;
  try {
    const moneyRead = state.tab === 'projects' ? get('/v2/finance/reports/pnl_by?' + opsQs({by: 'project', start: '2000-01-01', end: finToday(), top_n: 200}), {fresh: true}).catch(() => null) : null;
    const [rows, report] = await Promise.all([get(`/v2/${state.tab}?${opsQs(params)}`, {fresh: true}), moneyRead]);
    page = rows;
    if (state.tab === 'projects') {
      money = report ? new Map((report.rows || []).map(r => [r.key, r])) : null;
      state.moneyPartial = opsPartial(report);
    }
  } catch (e) {
    if (OPS !== state || state.load !== load) return;
    if (more) { toast(e?.message || 'Did not load', true); opsTable(state); return; }   // the rows already here stay
    if (!opsAbsent(e)) { $('#ops-table').innerHTML = opsFail(e); return; }
    page = {[state.tab]: [], next_cursor: null};
  }
  if (OPS !== state || state.load !== load) return;
  state.rows = [...(more ? state.rows : []), ...(page?.[state.tab] || [])];
  state.next = page?.next_cursor || null;
  state.money = money;
  const first = !state.loaded;
  state.loaded = true;
  opsTable(state);
  if (first) opsSelect();   // later reads leave the open panel as it is: opsTable marks its row
}
function opsTable(state) {
  const host = $('#ops-table'); if (!host) return;
  const filters = $('#ops-filters');
  if (filters && !filters.contains(document.activeElement)) patchHTML(filters, opsFilterControls(state));
  const rows = state.rows, cols = OPS_COLS[state.tab](state);
  const f = OPS_FILTERS[state.tab], narrowed = !!(f.q || f.customer || f.location || f.kind || f.status || f.due);
  if (!rows.length) {
    const noun = {locations: 'locations', items: 'products or services', projects: 'projects', equipment: 'equipment'}[state.tab];
    host.innerHTML = `<p class="muted ops-note">No ${noun}${narrowed ? (state.tab === 'equipment' ? ' matches' : ' match') : ''}</p>`;
    return;
  }
  const your = state.tab === 'projects' && state.money && state.moneyPartial;
  const moneyCol = label => ['Income', 'Costs', 'Profit'].includes(label);
  host.innerHTML = `<table class="ops-t ops-t-${state.tab}"><thead><tr>${cols.map(([label, , cls]) =>
      `<th scope="col" class="${cls || ''}">${esc(label)}${your && label === 'Income' ? ' <span class="pill" data-ops-your-view>Your view</span>' : ''}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(r => `<tr data-ops-row="${esc(r.id)}" class="${r.archived_at ? 'ops-off' : ''}${r.id === state.sel ? ' ops-on' : ''}">${cols.map(([label, cell, cls]) => {
      const html = cell(r);
      return `<td data-l="${esc(label)}${your && moneyCol(label) ? ' · Your view' : ''}" class="${cls || ''}">${html}</td>`;
    }).join('')}</tr>`).join('')}</tbody></table>
    ${state.next ? '<button type="button" class="ghost ops-more" data-ops-more>More</button>' : ''}`;
  const more = host.querySelector('[data-ops-more]');
  if (more) more.onclick = () => { more.disabled = true; void opsLoad(state, true); };
}

// ----------------------------------------------------------------- the forms
const opsField = (label, control, attrs = '') => `<label${attrs ? ' ' + attrs : ''}><span>${esc(label)}</span>${control}</label>`;
const opsInput = (label, name, value = '', attrs = '') => opsField(label, `<input name="${name}" value="${esc(value ?? '')}"${attrs ? ' ' + attrs : ''}>`);
const opsCustomers = () => (OPS_REFS?.customers || []).map(c => [c.id, c.name]);
const opsLocations = (but = '') => (OPS_REFS?.locations || []).filter(l => !l.archived_at && l.id !== but).map(l => [l.id, l.name]);
// A pick list, holding on to the record's own value when the list no longer offers it (archived, or out of the viewer's list).
function opsPick(label, name, pairs, value, blank = '—', ownName = '') {
  const all = value && !pairs.some(([v]) => v === value) ? [...pairs, [value, ownName || value]] : pairs;
  return opsField(label, `<select name="${name}">${grOpts(all, value, blank)}</select>`);
}
const OPS_LOCAL_KINDS = ['customer_site', 'vehicle'];
const opsLocationFields = r => {
  const a = r?.address || {};
  return `${opsInput('Name', 'name', r?.name, 'maxlength="200" required')}
    ${opsPick('Kind', 'kind', OPS_LOCATION_KINDS, r?.kind || 'store', null)}
    ${opsPick('Parent', 'parent_id', opsLocations(r?.id), r?.parent_id, '—', r?.parent_name)}
    ${opsPick('Customer', 'customer_id', opsCustomers(), r?.customer_id, '—', r && opsCustomerName(r))}
    ${opsInput('Street', 'street', a.street)}
    ${opsInput('City', 'city', a.city)}${opsInput('State', 'state', a.state)}${opsInput('Postal code', 'postal_code', a.postal_code)}
    ${r ? '' : `<label class="ops-inline"><input type="checkbox" name="in_books" checked><span>In books</span></label>`}`;
};
const opsItemFields = r => {
  const accounts = OPS_REFS?.accounts || [];
  const account = (label, name) => opsPick(label, name, accounts.map(a => [a.id, `${a.number || ''} ${a.name}`.trim()]), r?.[name], '—');
  const price = r && r.price != null ? (r.price / 100).toFixed(2) : '';
  return `${opsInput('Name', 'name', r?.name, 'maxlength="200" required')}
    ${opsPick('Kind', 'kind', OPS_ITEM_KINDS, r?.kind || 'service', null)}
    ${opsInput('SKU', 'sku', r?.sku)}${opsInput('Unit', 'unit', r?.unit)}
    ${r && !('price' in r) ? '' : opsInput('Price', 'price', price, 'inputmode="decimal"')}
    ${opsField('Description', `<textarea name="description" rows="2">${esc(r?.description || '')}</textarea>`)}
    <label class="ops-inline"><input type="checkbox" name="taxable"${r?.taxable ? ' checked' : ''}><span>Taxable</span></label>
    ${accounts.length ? account('Income account', 'income_account_id') + account('Expense account', 'expense_account_id') + account('Asset account', 'asset_account_id') : ''}`;
};
const opsProjectFields = r => `${opsInput('Name', 'name', r?.name, 'maxlength="200" required')}
    ${opsPick('Customer', 'customer_id', opsCustomers(), r?.customer_id, '—', r && opsCustomerName(r))}
    ${opsPick('Status', 'status', OPS_PROJECT_STATUSES, r?.status || 'open', null)}`;
const opsEquipmentFields = r => `${opsInput('Name', 'name', r?.name, 'maxlength="200" required')}
    ${opsPick('Kind', 'kind', OPS_EQUIPMENT_KINDS, r?.kind || 'owned', null)}
    ${opsPick('Customer', 'customer_id', opsCustomers(), r?.customer_id, '—', r && opsCustomerName(r))}
    ${opsPick('Location', 'location_id', opsLocations(), r?.location_id, '—', r && opsLocationName(r))}
    ${opsInput('Make', 'make', r?.make)}${opsInput('Model', 'model', r?.model)}${opsInput('Serial', 'serial', r?.serial)}
    ${opsInput('Installed', 'installed_on', r?.installed_on, 'type="date"')}${opsInput('Warranty ends', 'warranty_ends_on', r?.warranty_ends_on, 'type="date"')}`;

// What the form says, per record type: every field the person can edit.
function opsFormValues(tab, form, was) {
  const v = n => form.elements[n]?.value.trim() ?? '';
  if (tab === 'locations') {
    // Over the old address: a key the form doesn't show (an import's country) stays.
    const address = {...was?.address};
    for (const k of ['street', 'city', 'state', 'postal_code']) { if (v(k)) address[k] = v(k); else delete address[k]; }
    return {name: v('name'), kind: v('kind'), parent_id: v('parent_id'), customer_id: v('customer_id'), address};
  }
  if (tab === 'items') {
    const out = {name: v('name'), kind: v('kind'), sku: v('sku'), unit: v('unit'), description: v('description'), taxable: !!form.elements.taxable?.checked};
    if (form.elements.price) {
      const text = v('price'), cents = text === '' ? null : finParseCents(text);
      if (text !== '' && cents == null) throw new Error('A price like 89.00');
      out.price = cents;
    }
    for (const k of ['income_account_id', 'expense_account_id', 'asset_account_id']) if (form.elements[k]) out[k] = v(k);
    return out;
  }
  if (tab === 'projects') return {name: v('name'), customer_id: v('customer_id'), status: v('status')};
  return {name: v('name'), kind: v('kind'), customer_id: v('customer_id'), location_id: v('location_id'), make: v('make'), model: v('model'), serial: v('serial'),
    installed_on: v('installed_on'), warranty_ends_on: v('warranty_ends_on')};
}
const opsEmpty = x => x == null || x === '' || x === false || (typeof x === 'object' && !Object.keys(x).length);
// Key by key: the API may order an address differently from the form.
const opsSorted = x => x && typeof x === 'object' && !Array.isArray(x) ? Object.entries(x).sort(([a], [b]) => a.localeCompare(b)) : x ?? '';
const opsSame = (a, b) => JSON.stringify(opsSorted(a)) === JSON.stringify(opsSorted(b)) || (opsEmpty(a) && opsEmpty(b));
// A create sends what was filled in; an edit, only what changed.
function opsPayload(tab, values, was) {
  const one = OPS_ONE[tab];
  if (!was) return Object.fromEntries(Object.entries(values).filter(([, x]) => !opsEmpty(x)));
  return {[one + '_id']: was.id, ...Object.fromEntries(Object.entries(values).filter(([k, x]) => !opsSame(x, was[k])))};
}
function opsForm(tab, was) {
  const one = OPS_ONE[tab], title = (was ? 'Edit ' : 'New ') + {locations: 'location', items: 'product or service', projects: 'project', equipment: 'equipment'}[tab];
  const fields = {locations: opsLocationFields, items: opsItemFields, projects: opsProjectFields, equipment: opsEquipmentFields}[tab](was);
  const dlg = finDialog(title, fields, was ? 'Save' : 'Add', async form => {
    const values = opsFormValues(tab, form, was);
    if (!values.name) throw new Error('A name');
    const payload = opsPayload(tab, values, was);
    if (tab === 'locations' && !was) payload.in_books = !!form.elements.in_books?.checked;
    if (was && Object.keys(payload).length === 1) return;
    await finAct(`${one}_${was ? 'update' : 'create'}`, payload);
    await opsAfterWrite(was?.id);
  });
  // In books follows the kind until the person sets it themselves (customer sites and vehicles stay out of the books).
  const form = dlg.querySelector('form'), books = form.elements.in_books, kind = form.elements.kind;
  if (books && kind) {
    let touched = false;
    books.onchange = () => { touched = true; };
    kind.onchange = () => { if (!touched) books.checked = !OPS_LOCAL_KINDS.includes(kind.value); };
  }
}
// A write's effect: the pick lists read again, and the record's row and panel, in place, so a person deep in the list stays
// there. A new record or an import reads the list again.
async function opsAfterWrite(id = '') {
  await opsRefs(true);
  if (!OPS || !$('#ops-table')) return;
  if (id) { await opsRowRefresh(OPS, id); opsSelect(); }
  else await opsLoad(OPS);
}
async function opsSetActive(tab, r, active) {
  const done = await finAct(`${OPS_ONE[tab]}_update`, {[OPS_ONE[tab] + '_id']: r.id, active});
  if (done?.status === 'committed') toast(active ? 'Restored' : 'Marked inactive');   // otherwise finAct said who it went to
  await opsAfterWrite(r.id);
}

// ----------------------------------------------------------------- + Add from photo
// The bot that drafts equipment from a data plate: the first Operations role this company has, else its Assistant.
const OPS_PHOTO_BOTS = ['dispatcher', 'office-manager', 'operations-manager', 'logistics-coordinator'];
function opsPhotoBot() {
  const live = (S.emps || []).filter(e => e.status !== 'archived');
  for (const slug of OPS_PHOTO_BOTS) { const e = live.find(x => x.template === slug || x.name === slug); if (e) return e; }
  return live.find(e => e.name === assistantBot()) || null;
}
const OPS_PHOTO_BODY = `Draft equipment from the data plate in the attached photo.

1. Read the make, model and serial from the plate. If it can't be read, say so here and ask for another photo; save nothing.
2. Find the customer and site this task names or the photo shows. If you can't tell, ask here.
3. Save the photo to your files with \`hub_file_publish\` (the id it returns is what the record takes; this task's attachment id is not).
4. Create it with \`hub_finance_act\`, kind \`equipment_create\`: name, kind (owned or customer), customer_id, location_id, make, model, serial, and photo_file_id set to that file's id.

A person confirms the draft on the approval. Don't mark this done until they have.`;
// The task and its photo arrive together, so the bot's first look already has the plate.
async function opsPhotoDraft(file) {
  const bot = opsPhotoBot();
  if (!bot) { toast('No bot to draft this', true); return; }
  const button = $('#ops-body [data-ops-photo]');
  if (button) button.disabled = true;
  try {
    const out = await cloudCompose('/v2/tasks', {title: 'Draft equipment from a photo', body: OPS_PHOTO_BODY, owner: bot.name}, [file]);
    const id = out?.task?.id;
    toast(`Sent to ${bot.display_name || bot.name}`, false, id ? {label: 'Open', run: async () => { location.hash = '#/task/' + encodeURIComponent(id); }} : undefined);
  } catch (e) { toast(e.message || 'Not sent', true); }
  finally { if (button) button.disabled = false; }
}
