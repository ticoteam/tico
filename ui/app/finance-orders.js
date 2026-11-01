/* ui/app/finance-orders.js — Finance › Payables › Purchase orders and Receivables › Estimates (plan §7.1), and what a line
   form picks from. An order's rail: Approve when it waits on the viewer, Convert to bill while approved, Close, Void, and the
   bills on it. An estimate's: Send, Mark accepted, Convert to invoice, Mark declined. Every decision is a dry run first
   (finDecide), as a bill's are, so the rules, approvals and audit stay the one system.
   Reads: GET /api/v2/purchase-orders[/{id}], /api/v2/estimates[/{id}], for the forms /api/v2/items, /locations, /projects.
   Writes (POST /api/v2/finance/actions): po_create, po_approve, po_close, po_void, bill_create, estimate_create,
   estimate_send, estimate_accept, estimate_convert, estimate_decline. An approval asked of the viewer is answered on its own
   route (POST /api/v2/approvals/{id}), the one Needs you uses.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const FIN_ORD = {
  po: {path: 'purchase-orders', list: 'orders', id: 'po_id', contactKind: 'vendor', tab: 'payables', sub: 'orders', noun: 'purchase orders',
    search: 'Search vendors, orders', filters: [['', 'All'], ['draft', 'Draft'], ['approved', 'Approved'], ['partial', 'Part billed'], ['closed', 'Closed']]},
  estimate: {path: 'estimates', list: 'estimates', id: 'estimate_id', contactKind: 'customer', tab: 'receivables', sub: 'estimates', noun: 'estimates',
    search: 'Search customers, estimates', filters: [['', 'All'], ['draft', 'Draft'], ['sent', 'Sent'], ['accepted', 'Accepted'], ['invoiced', 'Invoiced'], ['declined', 'Declined']]},
};
const FIN_ORD_F = {po: {q: '', status: ''}, estimate: {q: '', status: ''}};
const FIN_ORD_STATUS = {draft: ['waiting', 'Draft'], sent: ['', 'Sent'], approved: ['ready', 'Approved'], partial: ['ready', 'Part billed'],
  closed: ['ok', 'Closed'], void: ['', 'Void'], accepted: ['ready', 'Accepted'], declined: ['', 'Declined'], invoiced: ['ok', 'Invoiced']};
const finOrdStatus = d => finPill(FIN_ORD_STATUS[d.status] || ['', d.status]);
const finNames = rows => (rows || []).map(r => r.name).join(', ');

// ----------------------------------------------------------------- Bills | Purchase orders, Invoices | Estimates
// The switch at the head of Payables and Receivables; only for a viewer whose read of the other half will answer.
function finOrdViews(kind) {
  const po = kind === 'bill' || kind === 'po', other = po ? finHolds('finance.ap') : finHolds('finance.ar');
  if (!other) return '';
  const on = kind === 'po' || kind === 'estimate' ? 'other' : 'docs';
  return finSeg('view', po ? 'Payables' : 'Receivables', po ? [['docs', 'Bills'], ['other', 'Purchase orders']] : [['docs', 'Invoices'], ['other', 'Estimates']], on);
}
function finOrdViewGo(v) {
  const payables = FIN.tab === 'payables';
  location.hash = finHref(`/${FIN.tab}${v === 'other' ? '/' + (payables ? 'orders' : 'estimates') : ''}`);
}

// ----------------------------------------------------------------- the page
function finOrdPage(kind) {
  const k = FIN_ORD[kind], f = FIN_ORD_F[kind], caps = finCaps(), want = FIN.q?.get('doc') || '';
  FIN.ord = {kind, rows: [], next: null, sel: want, want, load: 0, one: null, oneLoad: 0};
  $('#fin-body').innerHTML = `<div class="fin-bar">${finOrdViews(kind)}<label class="fin-search"><span class="nav-icon" aria-hidden="true">search</span><input type="search" data-fin-q value="${esc(f.q)}" placeholder="${esc(k.search)}" aria-label="${esc(k.search)}"></label>
      ${finSeg('status', 'Status', k.filters, f.status)}<span class="spacer"></span>
      ${caps.may(FIN_DOC[kind].right) ? `<button type="button" class="primary" data-fin-ord-add>${esc(FIN_DOC[kind].adding)}</button>` : ''}</div>`
    + finSplit(`<div class="fin-scroll" id="fin-ords">${skelRows(6, {dot: false})}</div>`, '', FIN_DOC[kind].label);
  const body = $('#fin-body');
  let timer = 0;
  body.querySelector('[data-fin-q]').oninput = ev => { clearTimeout(timer); timer = setTimeout(() => { f.q = ev.target.value.trim(); void finOrdLoad(false); }, 300); };
  body.onclick = ev => {
    const seg = ev.target.closest('[data-fin-seg] button');
    if (seg?.parentElement.dataset.finSeg === 'view') { finOrdViewGo(seg.dataset.v); return; }
    if (seg) { if (seg.dataset.v !== f.status) { f.status = seg.dataset.v; finSegSet(seg); void finOrdLoad(false); } return; }
    if (ev.target.closest('[data-fin-more]')) { void finOrdLoad(true); return; }
    if (ev.target.closest('[data-fin-ord-add]')) { void finDocAdd(kind); return; }
    const tr = ev.target.closest('tr[data-fin-ord]');
    if (tr && (tr.dataset.finOrd !== FIN.ord.sel || finContactOpen())) { FIN.ord.sel = tr.dataset.finOrd; finContactClose(); finOrdDraw(); void finOrdOne(); }
  };
  void finOrdLoad(false);
  finContactFromAddress();
}
async function finOrdLoad(more) {
  const state = FIN.ord, k = FIN_ORD[state.kind], f = FIN_ORD_F[state.kind], load = ++state.load;
  const qs = new URLSearchParams({limit: '50'});
  if (f.q) qs.set('q', f.q.slice(0, 100));
  if (f.status) qs.set('status', f.status);
  if (more && state.next) qs.set('cursor', state.next);
  let d;
  try { d = await get(`/v2/${k.path}?${qs}${finAs()}`, {fresh: true}); }
  catch (e) { if (FIN?.ord === state && state.load === load && $('#fin-ords')) { $('#fin-ords').innerHTML = finFail(e); finRail(''); } return; }
  if (FIN?.ord !== state || state.load !== load || !$('#fin-ords')) return;
  state.rows = more ? [...state.rows, ...(d[k.list] || [])] : d[k.list] || [];
  state.next = d.next_cursor || null;
  if (!state.rows.some(r => r.id === state.sel) && (!state.want || state.sel !== state.want)) state.sel = state.rows[0]?.id || '';
  finOrdDraw(); void finOrdOne();
}
function finOrdDraw() {
  const state = FIN.ord, box = $('#fin-ords'); if (!box) return;
  const po = state.kind === 'po';
  const cell = v => v ? esc(v) : '<span class="muted">—</span>';
  const tr = (r, cells) => `<tr data-fin-ord="${esc(r.id)}"${state.sel === r.id ? ' class="fin-on"' : ''}><td><button type="button" class="fin-link">${esc(r.number || '—')}</button></td><td>${esc(r.contact?.name || '—')}</td>${cells}</tr>`;
  const table = !state.rows.length ? finNote(`No ${FIN_ORD[state.kind].noun}.`) : po
    ? `<table class="fin-t fin-doc-t"><thead><tr><th>Number</th><th>Vendor</th><th class="fin-wide">Location</th><th class="fin-n">Total</th><th class="fin-n fin-wide">Billed</th><th class="fin-n fin-wide">Held</th><th>Status</th></tr></thead><tbody>${state.rows.map(r => tr(r,
      `<td class="fin-wide">${cell(finNames(r.locations))}</td><td class="fin-n">${finCents(r.total)}</td><td class="fin-n fin-wide">${finCents(r.billed)}</td><td class="fin-n fin-wide">${finCents(r.held)}</td><td>${finOrdStatus(r)}</td>`)).join('')}</tbody></table>`
    : `<table class="fin-t fin-doc-t"><thead><tr><th>Number</th><th>Customer</th><th class="fin-wide">Project</th><th class="fin-n">Total</th><th>Status</th></tr></thead><tbody>${state.rows.map(r => tr(r,
      `<td class="fin-wide">${cell(finNames(r.projects))}</td><td class="fin-n">${finCents(r.total)}</td><td>${finOrdStatus(r)}</td>`)).join('')}</tbody></table>`;
  box.innerHTML = table + (state.next ? '<button type="button" class="ghost fin-more-btn" data-fin-more>More</button>' : '');
}
// The selected record: from its row at once, then whole (lines, bills, approvals, decisions).
async function finOrdOne() {
  const state = FIN.ord, id = state.sel, load = ++state.oneLoad;
  state.one = null;
  const row = state.rows.find(r => r.id === id);
  if (!id || (!row && id !== state.want)) { finRail(''); return; }
  if (finContactOpen()) return;
  if (row) finOrdRail(row, false);
  let d;
  try { d = await get(`/v2/${FIN_ORD[state.kind].path}/${encodeURIComponent(id)}${finAs('?')}`, {fresh: true}); }
  catch (e) { if (FIN?.ord === state && state.oneLoad === load) finRail(finFail(e)); return; }
  if (FIN?.ord !== state || state.oneLoad !== load || finContactOpen()) return;
  state.one = d;
  finOrdRail(d, true);
}
// An approval on this record still waiting, if any (the actions on it, newest first).
const finOrdAsked = d => (d.actions || []).find(a => a.status === 'needs_approval' && a.approval_id);
function finOrdRail(d, full) {
  const state = FIN.ord, po = state.kind === 'po', k = FIN_ORD[state.kind];
  const contact = {...d.contact, kind: k.contactKind};
  const rows = [finKv(FIN_DOC[state.kind].label, esc(d.number || '—')), finKv('Date', d.date ? esc(finDayLabel(d.date)) : '—'), finKv('Status', finOrdStatus(d))];
  if (d.locations?.length) rows.push(finKv('Location', esc(finNames(d.locations))));
  if (d.projects?.length) rows.push(finKv('Project', esc(finNames(d.projects))));
  if (po) rows.push(finKv('Billed', finUsd(d.billed)), finKv('Held', finUsd(d.held)));
  // Whoever answered an approval approved it; the order records the teammate who asked.
  const approver = po && (full && (d.actions || []).find(a => a.kind === 'po_approve' && a.status === 'committed')?.approval_to || d.approved_by);
  if (approver) rows.push(finKv('Approved by', esc(finWho(approver))));
  if (!po && d.invoice_id) rows.push(finKv('Invoice', `<a class="fin-link" href="${esc(finHref('/receivables', {doc: d.invoice_id}))}">Open</a>`));
  if (d.memo) rows.push(finKv('Memo', esc(d.memo)));
  const parts = [`<div class="fin-rail-title"><h2>${finContactLink(contact)}</h2><span class="fin-big">${finUsd(d.total)}</span></div>`, rows.join('')];
  if (full) {
    const name = l => [l.item_name, l.account?.name, l.description, ...Object.values(l.tag_names || {}), l.taxable === true ? 'Taxable' : ''].filter(Boolean).join(' · ') || '—';
    if (d.lines?.length) parts.push(`<h3 class="fin-h">Lines</h3>${d.lines.map(l => finKv(name(l), po && l.billed ? `${finCents(l.billed)} of ${finCents(l.amount)}` : finCents(l.amount))).join('')}`);
    if (d.bills?.length) parts.push(`<h3 class="fin-h">Bills</h3>${d.bills.map(b => finKv(`${b.number || 'Bill'} · ${finDayLabel(b.date)}${b.vendor && b.vendor !== d.contact?.name ? ' · ' + b.vendor : ''}`,
      `<a class="fin-link" href="${esc(finHref('/payables', {doc: b.id}))}">${finCents(b.on_order)}</a>`)).join('')}`);
    const asked = finOrdAsked(d);
    if (asked) parts.push(finKv('Approval', esc(finAskText(asked.approval_to))));
    parts.push(finOrdActsHtml(d));
  }
  finRail(parts.join(''));
  const rail = $('#fin-rail');
  if (rail) rail.onclick = ev => {
    if (finContactClick(ev, k.contactKind)) return;
    const b = full && ev.target.closest('[data-fin-do]');
    if (b) finOrdDo(b.dataset.finDo, d, b);
  };
}
// Only the decisions the viewer may take on this record now. An order waits on the viewer when an approval is asked of them,
// or when nobody has been asked yet and the viewer holds the right (approving then says whom the rules ask). While it waits
// on someone else, it can't be approved from here.
function finOrdActs(d) {
  const kind = FIN.ord.kind;
  if (!finCaps()?.may(FIN_DOC[kind].right)) return [];
  const asked = finOrdAsked(d), mine = asked && asked.approval_to === finCaps().who;
  if (kind === 'po') {
    if (mine) return [['decline', 'Decline'], ['answer', 'Approve']];
    if (d.status === 'draft' || d.status === 'sent') return [['void', 'Void'], ...(asked ? [] : [['approve', 'Approve']])];
    if (d.status === 'approved') return [['void', 'Void'], ['close', 'Close'], ['bill', 'Convert to bill']];
    if (d.status === 'partial') return [['close', 'Close'], ['bill', 'Convert to bill']];
    return [];
  }
  if (mine) return [['decline', 'Decline'], ['answer', 'Approve']];
  if (d.status === 'draft') return asked ? [] : [['send', 'Send']];
  if (d.status === 'sent') return [['declined', 'Mark declined'], ['accepted', 'Mark accepted']];
  if (d.status === 'accepted') return [['invoice', 'Convert to invoice']];
  return [];
}
const finOrdActsHtml = d => {
  const acts = finOrdActs(d);
  return acts.length ? `<div class="fin-acts" id="fin-doc-acts">${acts.map(([v, l], i) =>
    `<button type="button" class="${i === acts.length - 1 ? 'primary' : 'ghost'}" data-fin-do="${v}">${esc(l)}</button>`).join('')}</div>` : '';
};
function finOrdDo(what, d, btn) {
  const id = {[FIN_ORD[FIN.ord.kind].id]: d.id};
  const run = (kind, done) => void finDocRun(kind, id, done, btn, () => finOrdActsHtml(FIN.ord.one || d));
  if (what === 'answer' || what === 'decline') void finOrdAnswer(d, what === 'answer' ? 'approved' : 'declined', btn);
  else if (what === 'approve') run('po_approve', 'Approved');
  else if (what === 'close') run('po_close', 'Closed');
  else if (what === 'void') run('po_void', 'Voided');
  else if (what === 'bill') finOrdBill(d);
  else if (what === 'send') run('estimate_send', 'Sent');
  else if (what === 'accepted') run('estimate_accept', 'Accepted');
  else if (what === 'declined') run('estimate_decline', 'Declined');
  else if (what === 'invoice') run('estimate_convert', 'Invoice made');
}
async function finOrdAnswer(d, decision, btn) {
  const asked = finOrdAsked(d); if (!asked) return;
  btn.disabled = true;
  try { await post(`/v2/approvals/${encodeURIComponent(asked.approval_id)}`, {decision}); toast(decision === 'approved' ? 'Approved' : 'Declined'); finDocsReload(); }
  catch (e) { toast(e.message, true); btn.disabled = false; }
}
// A bill for all or part of an approved order: each line's amount starts at what it has left, and a line left empty is
// not on the bill. The account and tags come from the order's line.
function finOrdBill(d) {
  const lines = (d.lines || []).map(l => ({...l, left: l.amount - (l.billed || 0)})).filter(l => l.left > 0);
  const name = l => [l.item_name, l.account?.name, l.description].filter(Boolean).join(' · ') || 'Line';
  finDecideDialog('Convert to bill', `<label>Number<input name="number" maxlength="60" autocomplete="off"></label>
    <div class="fin-pair"><label>Date<input type="date" name="date" required value="${finToday()}"></label><label>Due<input type="date" name="due"></label></div>
    ${lines.map(l => `<label>${esc(name(l))}<input name="line-${esc(l.id)}" data-fin-po-line="${esc(l.id)}" inputmode="decimal" autocomplete="off" value="${finCentsValue(l.left)}"></label>`).join('')}`, 'Save', form => {
    const el = form.elements, out = [];
    for (const input of form.querySelectorAll('[data-fin-po-line]')) {
      const text = input.value.trim();
      if (!text) continue;
      const amount = finParseCents(text);
      if (!amount) throw new Error('A dollar amount on each line, like 500');
      out.push({po_line_id: input.dataset.finPoLine, amount});
    }
    if (!out.length) throw new Error('A dollar amount, like 500');
    if (!el.date.value) throw new Error('Pick a date');
    return ['bill_create', {vendor_id: d.contact.id, bill_date: el.date.value, origin: 'manual', lines: out,
      ...(el.number.value.trim() ? {number: el.number.value.trim()} : {}), ...(el.due.value ? {due_date: el.due.value} : {})}];
  }, 'Saved');
}

// ----------------------------------------------------------------- what a line form picks from
// Every page of a record read, up to a thousand rows: a <select> that stopped at the first page would hide the rest.
async function finPaged(path, key) {
  const out = [];
  let cursor = '';
  for (let n = 0; n < 5; n++) {
    const d = await get(`${path}?limit=200${cursor ? '&cursor=' + encodeURIComponent(cursor) : ''}${finAs()}`, {fresh: true});
    out.push(...(d?.[key] || []));
    cursor = d?.next_cursor || '';
    if (!cursor) break;
  }
  return out;
}
// The reads leave archived records out. A category groups items in the books and can't go on a line. One read per page.
function finPicks() {
  if (FIN.picks) return FIN.picks;
  const read = (path, key) => finPaged(path, key).catch(() => []);
  return FIN.picks = Promise.all([read('/v2/items', 'items'), read('/v2/locations', 'locations'), read('/v2/projects', 'projects')])
    .then(([items, locations, projects]) => ({items: items.filter(i => i.kind !== 'category'), locations, projects}));
}
const finItemHtml = picks => picks.items.length ? `<label class="fin-il-item">Item<select name="item"><option value=""></option>${finOpts(picks.items)}</select></label>` : '';
// Location, Project and Class, each only when the company has any.
function finDimsHtml(picks) {
  const dims = [['location', 'Location', picks.locations], ['project', 'Project', picks.projects], ['class', 'Class', FIN?.classes || []]].filter(([, , rows]) => rows.length);
  return dims.length ? `<div class="fin-dims">${dims.map(([k, l, rows]) => `<label>${l}<select name="dim-${k}"><option value=""></option>${finOpts(rows)}</select></label>`).join('')}</div>` : '';
}
// A line's chosen location, project and class as its tags; `box` is the line's row (or the form, for a bill's one line).
function finDimTags(box) {
  const tags = {};
  for (const k of ['location', 'project', 'class']) { const v = box.querySelector(`[name="dim-${k}"]`)?.value; if (v) tags[k] = v; }
  return tags;
}
// Picking an item fills its line and, on a sale, its price. An item's price is what it sells for, so a bill or an order
// keeps the amount typed. A line the viewer can't see stays unnamed, and the server posts to the item's own.
function finItemWatch(form, picks, sale) {
  form.addEventListener('change', ev => {
    if (ev.target.name !== 'item') return;
    const row = ev.target.closest('.fin-il') || form, item = picks.items.find(i => i.id === ev.target.value);
    if (!item) return;
    const acct = row.querySelector('[name=acct],[name=line]'), account = sale ? item.income_account_id : item.expense_account_id;
    if (acct) {
      acct.value = account && [...acct.options].some(o => o.value === account) ? account : '';
      if (!acct.value && !account) acct.selectedIndex = 0;   // the item names no line: the person picks one
      acct.dispatchEvent(new Event('change', {bubbles: true}));
    }
    const amount = row.querySelector('[name=amt],[name=amount]');
    if (sale && amount && item.price != null && (!amount.value || amount.dataset.filled === amount.value)) {
      amount.value = amount.dataset.filled = finCentsValue(item.price);
    }
  });
}
