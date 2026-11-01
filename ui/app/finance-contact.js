/* ui/app/finance-contact.js — a vendor's or customer's panel in the Payables and Receivables rail. A customer's: their sites
   and equipment, open estimates and invoices, and due dates, "everything we service" for a person or a bot. A vendor's: payment
   terms, owner, renewal date and notice period, and linked contract docs; who can pay stays a Finance right.
   Opens from a document's title or from an address with ?contact=<id>. Every part is its own read, so one that is missing or
   not in the viewer's view just leaves its heading out.
   Reads: GET /api/v2/finance/contacts/{id}, or the contact's list row; /api/v2/locations, /api/v2/equipment, /api/v2/due-dates,
   /api/v2/estimates, /api/v2/finance/invoices. Write: contact_update through POST /api/v2/finance/actions.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

let FIN_CONTACT = null;   // the panel open in the rail: {id, kind, name, load}
const finContactOpen = () => !!FIN_CONTACT;
function finContactClose() { FIN_CONTACT = null; }
const finContactLink = c => c?.id ? `<button type="button" class="fin-link" data-fin-contact="${esc(c.id)}">${esc(c.name || '—')}</button>` : esc(c?.name || '—');
// A click on a contact's name, or on the panel's way back; true when it was one.
function finContactClick(ev, kind) {
  const link = ev.target.closest('[data-fin-contact]');
  if (!link) return false;
  void finContactShow({id: link.dataset.finContact, name: link.textContent, kind});
  return true;
}
function finContactFromAddress() {
  finContactClose();
  const id = FIN.q?.get('contact');
  if (id) void finContactShow({id, name: '', kind: FIN.tab === 'payables' ? 'vendor' : 'customer'});
}
function finContactBack() {
  finContactClose();
  if (FIN?.doc) void finDocOne();
  if (FIN?.ord) void finOrdOne();
}
async function finContactShow({id, name, kind}) {
  const me = FIN_CONTACT = {id, name, kind}, page = FIN;
  finContactDraw(me, null);
  $('#fin-rail')?.scrollIntoView?.({block: 'nearest'});
  const parts = await finContactRead(me);
  if (FIN_CONTACT !== me || FIN !== page) return;
  finContactDraw(me, parts);
}
// A read that may not exist yet, or not be in the viewer's view: its rows, or nothing.
const finTry = (path, key) => get(path, {fresh: true}).then(d => key ? d?.[key] || [] : d, () => key ? [] : null);
const finOnly = (rows, test) => (rows || []).filter(test);
async function finContactRead(me) {
  const id = encodeURIComponent(me.id), as = finAs();
  const one = await finTry(`/v2/finance/contacts/${id}${finAs('?')}`);
  const contact = one?.contact || one || (await finTry(`/v2/finance/contacts?limit=50&q=${encodeURIComponent(me.name.slice(0, 100))}${as}`, 'contacts')).find(c => c.id === me.id) || {id: me.id, name: me.name};
  if (contact.name) me.name = contact.name;
  if (contact.kind) me.kind = contact.kind === 'both' ? me.kind : contact.kind;
  if (me.kind === 'vendor') return {contact};
  // The reads filter on the server where they can; the checks here keep a read that does not yet take the filter from
  // putting another customer's rows on this panel.
  const [sites, gear, dates, estimates, invoices] = await Promise.all([
    one?.locations || finTry(`/v2/locations?customer_id=${id}&limit=200${as}`, 'locations').then(r => finOnly(r, l => l.customer_id === me.id)),
    one?.equipment || finTry(`/v2/equipment?customer_id=${id}&limit=200${as}`, 'equipment').then(r => finOnly(r, e => e.customer_id === me.id)),
    one?.due_dates || finTry(`/v2/due-dates?limit=200${as}`, 'due_dates'),
    one?.estimates || (finHolds('finance.ar') ? finTry(`/v2/estimates?customer_id=${id}&limit=50${as}`, 'estimates').then(r => finOnly(r, e => ['draft', 'sent', 'accepted'].includes(e.status))) : []),
    one?.invoices || (finCaps()?.receivables ? finTry(`/v2/finance/invoices?customer_id=${id}&status=draft,unpaid&limit=50${as}`, 'invoices') : []),
  ]);
  const where = new Set([...sites.map(s => 'location:' + s.id), ...gear.map(e => 'equipment:' + e.id), 'contact:' + me.id]);
  const due = one?.due_dates ? dates : finOnly(dates, d => where.has(`${d.subject_type}:${d.subject_id}`));
  return {contact, sites, gear, due, estimates, invoices};
}
function finContactDraw(me, parts) {
  const head = `<button type="button" class="ghost fin-rail-back" data-fin-contact-back>‹ Back</button><div class="fin-rail-title"><h2>${esc(me.name || '—')}</h2></div>`;
  if (!parts) { finRail(head); finContactWire(me, null); return; }
  const c = parts.contact, out = [head];
  if (me.kind === 'vendor') {
    const docs = c.contract_docs || c.docs || [];
    out.push(finKv('Payment terms', c.payment_terms_days != null ? `Net ${esc(c.payment_terms_days)}` : '—'),
      finKv('Owner', c.owner_id ? esc(finWho(c.owner_id)) : '—'),
      finKv('Renews', c.renewal_on ? esc(finDayLabel(c.renewal_on)) : '—'),
      finKv('Notice', c.renewal_notice_days != null ? `${esc(c.renewal_notice_days)} days` : '—'));
    if (docs.length) out.push(`<h3 class="fin-h">Contracts</h3><div class="fin-files">${docs.map(d =>
      `<a href="${esc(d.url || '/api/v2/files/' + encodeURIComponent(d.file_id || d.id))}" target="_blank" rel="noopener">${esc(d.name || d.title || 'Contract')}</a>`).join('')}</div>`);
    if (finCaps()?.may('finance.ap')) out.push('<div class="fin-acts"><button type="button" class="ghost" data-fin-contact-edit>Edit</button></div>');
  } else {
    const list = (title, rows, row) => rows?.length ? `<h3 class="fin-h">${title}</h3>${rows.map(row).join('')}` : '';
    const open = (href, label) => `<a class="fin-link" href="${esc(href)}">${esc(label)}</a>`;
    out.push(list('Sites', parts.sites, s => finKv(s.name, esc([s.address?.city, s.address?.state].filter(Boolean).join(', ')))),
      list('Equipment', parts.gear, e => finKv(e.name, esc([e.make, e.model].filter(Boolean).join(' ')))),
      list('Estimates', parts.estimates, e => finKv(e.number || 'Estimate', open(finHref('/receivables/estimates', {doc: e.id}), finUsd(e.total)))),
      list('Invoices', parts.invoices, i => finKv(i.number || 'Invoice', open(finHref('/receivables', {doc: i.id}), finUsd(i.status === 'draft' ? i.total : i.remaining ?? i.total)))),
      list('Due dates', parts.due, d => finKv(d.name, d.next_due_on ? esc(finDayLabel(d.next_due_on)) : '—')));
  }
  finRail(out.join(''));
  finContactWire(me, c);
}
function finContactWire(me, c) {
  const rail = $('#fin-rail'); if (!rail) return;
  rail.onclick = ev => {
    if (ev.target.closest('[data-fin-contact-back]')) { finContactBack(); return; }
    if (c && ev.target.closest('[data-fin-contact-edit]')) finVendorEdit(me, c);
  };
}
// Payment terms, owner, renewal and notice: only what changed goes to contact_update.
function finVendorEdit(me, c) {
  const mates = (FIN?.acc || FIN_ACCESS)?.teammates || [];
  const owners = mates.map(m => ({id: m.teammate, name: finWho(m.teammate)}));
  finDecideDialog('Vendor', `<label>Payment terms (days)<input name="terms" type="number" min="0" max="365" inputmode="numeric" value="${esc(c.payment_terms_days ?? '')}"></label>
    <label>Owner<select name="owner"><option value=""></option>${finOpts(owners, c.owner_id || '')}</select></label>
    <div class="fin-pair"><label>Renews<input type="date" name="renews" value="${esc(c.renewal_on || '')}"></label>
    <label>Notice (days)<input name="notice" type="number" min="0" max="365" inputmode="numeric" value="${esc(c.renewal_notice_days ?? '')}"></label></div>`, 'Save', form => {
    const el = form.elements, payload = {contact_id: me.id};
    const whole = v => v.trim() === '' ? null : Number(v);
    if (whole(el.terms.value) !== (c.payment_terms_days ?? null)) payload.payment_terms_days = whole(el.terms.value);
    if (whole(el.notice.value) !== (c.renewal_notice_days ?? null)) payload.renewal_notice_days = whole(el.notice.value);
    if (el.owner.value !== (c.owner_id || '')) payload.owner_id = el.owner.value;
    if (el.renews.value !== (c.renewal_on || '')) payload.renewal_on = el.renews.value;
    if (Object.keys(payload).length === 1) throw new Error('Nothing changed');
    return ['contact_update', payload];
  }, 'Saved');
  const form = document.querySelector('dialog.fin-dlg form');
  form?.addEventListener('submit', () => {
    // After the dialog closes the panel reads again, as the document rail does after a decision.
    const dlg = form.closest('dialog');
    dlg?.addEventListener('close', () => { if (FIN_CONTACT === me) void finContactShow(me); }, {once: true});
  }, {once: true});
}
