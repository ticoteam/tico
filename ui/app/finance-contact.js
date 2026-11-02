/* ui/app/finance-contact.js — a vendor's or customer's panel in the Payables and Receivables rail. A customer's: their sites
   and equipment, open estimates and invoices, and due dates, "everything we service" for a person or a bot. A vendor's: payment
   terms, owner, renewal date and notice period, and linked contract docs; who can pay stays a Finance right. Either: its Library
   files, and when a provider's books hold it, how fresh this copy is.
   Opens from a document's title or from an address with ?contact=<id>. A part that is missing or not in the viewer's view
   just leaves its heading out.
   Reads: GET /api/v2/customers/{id}, /api/v2/finance/contacts/{id}, /api/v2/files. Writes through POST /api/v2/finance/actions:
   contact_update, record_file_add, record_file_remove.
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
  let parts;
  try { parts = await finContactRead(me); }
  catch (e) { if (FIN_CONTACT === me && FIN === page) { finContactDraw(me, null); $('#fin-rail')?.insertAdjacentHTML('beforeend', finFail(e)); } return; }
  if (FIN_CONTACT !== me || FIN !== page) return;
  finContactDraw(me, parts);
}
// A vendor's panel is the contact read. A customer's adds its panel read; a teammate who can't see the contact's side still
// reads that one, without files. A contact opened from an address may be the other kind.
async function finContactRead(me) {
  const id = encodeURIComponent(me.id), as = finAs('?');
  const record = me.kind === 'customer' ? await get(`/v2/finance/contacts/${id}${as}`, {fresh: true}).catch(() => null)
    : await get(`/v2/finance/contacts/${id}${as}`, {fresh: true});
  if (record) me.name = record.contact.name;
  const files = record ? await finContactFiles(record.files) : null;
  if (me.kind !== 'customer') {
    if (record.contact.kind !== 'customer') return {contact: record.contact, record: record.contact, files};
    me.kind = 'customer';
  }
  const d = await get(`/v2/customers/${id}${as}`, {fresh: true});
  me.name = d.contact?.name || me.name;
  return {contact: d.contact, record: record?.contact, files, sites: d.locations, gear: d.equipment, due: d.due_dates, estimates: d.estimates, invoices: d.invoices?.invoices};
}
// A link names only the file, so each one's name comes from its own meta read.
const finContactFiles = files => Promise.all((files || []).map(async f =>
  ({...f, name: (await get(`/v2/files/${encodeURIComponent(f.file_id)}/meta`).catch(() => null))?.name})));
// A copy from a provider's books: when it last synced, and whether it may be behind. Tico's own record says nothing.
function finContactFresh(c) {
  if (!c?.source || c.source === 'tico') return '';
  return (c.as_of ? finKv('Last sync', esc(fmt(c.as_of))) : '') + (c.stale ? '<p class="fin-stale" data-fin-stale>May be out of date</p>' : '');
}
function finContactFilesHtml(files) {
  const edit = finCaps()?.may('finance.records');
  if (!files || (!files.length && !edit)) return '';
  return `<h3 class="fin-h">Files</h3>${files.map(f => `<div class="fin-rec-file"><a href="/api/v2/files/${encodeURIComponent(f.file_id)}" target="_blank" rel="noopener">${esc(f.name || 'File')}</a>
    ${edit ? `<button type="button" class="ghost tmodal-x" data-fin-file-remove="${esc(f.id)}" aria-label="Remove ${esc(f.name || 'file')}">✕</button>` : ''}</div>`).join('')}
    ${edit ? '<div class="fin-acts"><button type="button" class="ghost" data-fin-file-add>Add file</button></div>' : ''}`;
}
function finContactDraw(me, parts) {
  const head = `<button type="button" class="ghost fin-rail-back" data-fin-contact-back>‹ Back</button><div class="fin-rail-title"><h2>${esc(me.name || '—')}</h2></div>`;
  if (!parts) { finRail(head); finContactWire(me, null); return; }
  const c = parts.contact, out = [head, finContactFresh(parts.record)];
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
  out.push(finContactFilesHtml(parts.files));
  finRail(out.join(''));
  finContactWire(me, c, parts.files);
}
function finContactWire(me, c, files) {
  const rail = $('#fin-rail'); if (!rail) return;
  rail.onclick = ev => {
    if (ev.target.closest('[data-fin-contact-back]')) { finContactBack(); return; }
    if (c && ev.target.closest('[data-fin-contact-edit]')) finVendorEdit(me, c);
    if (ev.target.closest('[data-fin-file-add]')) void finFilePick(me, files || []);
    const gone = ev.target.closest('[data-fin-file-remove]');
    if (gone) void finFileRemove(me, gone);
  };
}
// The same reads and rows as the rules' document picker: recent files whose name fits, then search hits. Stored files only:
// a link has no copy to open from here.
async function finFilePick(me, linked) {
  const taken = new Set(linked.map(f => f.file_id));
  const fits = f => f.locator !== 'remote_link' && !f.url && !f.archived && !taken.has(f.id);
  let pick = '', found = [], recent = [], seq = 0, soon = 0;
  const dlg = finDialog('Add file', `<input name="find" type="search" placeholder="Search files" aria-label="Search files" autocomplete="off">
    <div class="ru-picks" role="listbox" aria-label="Files"></div>`, 'Add', async () => {
    if (!pick) throw new Error('Pick a file');
    const r = await finAct('record_file_add', {subject_type: 'contact', subject_id: me.id, file_id: pick});
    if (r.status === 'committed') { toast('Added'); if (FIN_CONTACT === me) void finContactShow(me); }
  });
  const find = dlg.querySelector('[name=find]'), box = dlg.querySelector('.ru-picks');
  const label = f => f.title || f.name || f.id;
  const draw = () => {
    const q = find.value.trim().toLowerCase();
    const local = recent.filter(f => label(f).toLowerCase().includes(q));
    const rows = [...local, ...found.filter(f => !local.some(x => x.id === f.id))].slice(0, 8);
    box.innerHTML = rows.map(f => `<button type="button" class="ru-pick" role="option" aria-selected="${f.id === pick}" data-id="${esc(f.id)}"><span class="ru-pick-t">${esc(label(f))}</span></button>`).join('')
      || `<p class="ru-pick-none">${q ? 'No files match.' : 'No files yet.'}</p>`;
  };
  box.onclick = ev => { const b = ev.target.closest('.ru-pick'); if (b) { pick = b.dataset.id; draw(); } };
  find.oninput = () => {
    found = []; pick = ''; draw();
    clearTimeout(soon);
    const q = find.value.trim(), mine = ++seq;
    if (q) soon = setTimeout(async () => {
      const r = await get('/v2/files/search?limit=8&q=' + encodeURIComponent(q)).catch(() => null);
      if (mine === seq && dlg.open) { found = (r?.results || []).filter(fits); draw(); }
    }, 250);
  };
  recent = await get('/v2/files?limit=50', {fresh: true}).then(r => (r.files || []).filter(fits)).catch(() => []);
  if (dlg.open) draw();
}
// The file stays in the Library; only the link goes.
async function finFileRemove(me, btn) {
  btn.disabled = true;
  try {
    const r = await finAct('record_file_remove', {record_file_id: btn.dataset.finFileRemove});
    if (r.status === 'committed') { toast('Removed'); if (FIN_CONTACT === me) void finContactShow(me); return; }
  } catch (e) { toast(e.message, true); }
  btn.disabled = false;
}
// Payment terms, owner, renewal and notice: only what changed goes to contact_update.
function finVendorEdit(me, c) {
  // Anyone can own a vendor, Finance access or not, so the whole team (the access read shows a non-admin only their reports).
  const owners = [...(S.people || []).map(p => ({id: `human:${p.id}`, name: p.name || p.id})),
    ...(S.emps || []).map(e => ({id: `bot:${e.name}`, name: e.display_name || e.name}))];
  if (c.owner_id && !owners.some(o => o.id === c.owner_id)) owners.push({id: c.owner_id, name: finWho(c.owner_id)});
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
