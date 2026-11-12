/* ui/app/finance-docs.ts — Finance › Payables and Receivables (spec §12), and entering a bill, an invoice or a journal
   entry (the Balance sheet's "+ Add", Payables' "+ Bill", Receivables' "+ Invoice").
   Payables: bills as plain rows (vendor, number, due, aging, amount, status, the day a scheduled payment goes),
   searched and filtered by status; a bill's rail shows its lines, files, payments and approvals, and only the decisions
   the viewer may take: approve, schedule, mark paid, void. The bills email address sits in the bar, copyable.
   Receivables: invoices the same way; the rail marks one paid or writes it off (and sends a draft). Neither draws the
   route's open total: no totals row. Finance Owner chases late invoices; the page shows no chase history.
   Every decision is a dry run first: one the rules send for approval says to whom, and a second press sends it.
   The owner or an admin sets or clears the bills address from the bar (PUT /api/v2/finance/bills-address).
   Reads: GET /api/v2/finance/bills[/{id}], /finance/invoices[/{id}], /finance/contacts, and for the forms the lines
   the viewer may post to (/finance/chart) and the bank accounts they may pay from (/finance/pay-from).
   Writes (POST /api/v2/finance/actions): bill_create, bill_approve, bill_schedule, bill_mark_paid, bill_void,
   invoice_create, invoice_send, invoice_record_payment, invoice_write_off, journal, contact_create.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// One account of the chart of accounts as GET /v2/finance/chart sends it (the journal's and the bill's lines).
type FinChartAccount = import('../types/api').FinanceChart['accounts'][number];
type FinDocKind = 'bill' | 'invoice';
type FinPair = [string, string];
interface FinDocDef {
  list: 'bills' | 'invoices'; contact: 'vendor' | 'customer'; contactKind: string; right: string; label: string; who: string;
  search: string; filters: FinPair[];
}
// A bill or invoice as the list sends it (GET /v2/finance/bills, /invoices); `account` is the line it posts to, or Split.
type FinDocRow = Omit<import('../types/api').FinanceDocumentRow, 'account' | 'attachments'> & {account?: {name?: string} | null};
// The whole document (GET /v2/finance/bills/{id}): the row, plus what the rail draws once it has been read.
type FinDocAny = FinDocRow & {
  actions?: {approval_id: string | null; approval_to: string | null; kind: string; status: string}[];
  attachments?: {file_id: string}[];
  lines?: {account: {name?: string} | null; amount: number; description: string; taxable?: boolean}[];
  schedule?: {money_account_id?: string} | null;
  settlements?: {amount: number; from_kind: string; settled_on: string; voided_at: string | null}[];
};
type FinDocList = {bills?: FinDocRow[]; invoices?: FinDocRow[]; next_cursor?: string | null; bills_email?: string | null};
// FIN.doc: the open Payables or Receivables page.
interface FinDocPage {
  kind: FinDocKind; rows: FinDocRow[]; next: string | null; sel: string; load: number; one: FinDocAny | null; oneLoad: number;
  email: string; paid?: FinDocRow[];
}
// FIN.chart: the lines read per form use (finChart); FIN.payFrom: the accounts a bill may be paid from.
interface FinPage { doc?: FinDocPage; chart?: Record<string, FinChartAccount[] | undefined>; payFrom?: FinPick[] }
type FinContact = import('../types/api').FinanceContacts['contacts'][number];
// What a decision dialog's build returns: the action's kind and its payload.
type FinDecision = [string, object];
type FinImpactBudget = { name: string; remaining: number | null; remaining_after: number; excluded?: boolean };
// A bill form's named controls (an invoice form has the same, bar the one-line pair).
interface FinDocFormControls {
  contact: HTMLSelectElement; name: HTMLInputElement; number: HTMLInputElement; date: HTMLInputElement; due: HTMLInputElement;
  memo: HTMLInputElement; amount: HTMLInputElement; line: HTMLSelectElement; for: HTMLSelectElement;
}

// A part of a dialog's or page's markup that must be there (a plain Error naming it, where a TypeError would have stopped).
function finDocPart<T extends HTMLElement = HTMLElement>(root: ParentNode, sel: string): T {
  return finNeed(root.querySelector<T>(sel), `the ${sel} element`);
}

const FIN_DOC: Record<FinDocKind, FinDocDef> = {
  bill: {list: 'bills', contact: 'vendor', contactKind: 'vendor', right: 'finance.ap', label: 'Bill', who: 'Vendor',
    search: 'Search vendors, bills',
    filters: [['unpaid', 'Open'], ['draft', 'Draft'], ['scheduled', 'Scheduled'], ['paid', 'Paid'], ['', 'All']]},
  invoice: {list: 'invoices', contact: 'customer', contactKind: 'customer', right: 'finance.ar', label: 'Invoice', who: 'Customer',
    search: 'Search customers, invoices',
    filters: [['unpaid', 'Open'], ['late', 'Late'], ['draft', 'Draft'], ['paid', 'Paid'], ['', 'All']]},
};
const FIN_DOC_F: Record<FinDocKind, {q: string; status?: string | undefined}> = {bill: {q: '', status: 'unpaid'}, invoice: {q: '', status: 'unpaid'}};
const FIN_CAME_IN: Record<string, string> = {manual: 'By hand', email: 'Email', upload: 'Upload'};
// A document's line (or Split) and team, from backend/finance/tab_ap.py doc_lines.
const finDocAccount = (r: FinDocRow): string => r.split ? 'Split' : r.account?.name || '';
const finDocTeam = (r: FinDocRow): string => r.team ? finTeamName(r.team) : '';
const FIN_AGING: Record<string, FinPair> = {current: ['', 'Not due'], '1_30': ['waiting', '1–30 late'], '31_60': ['waiting', '31–60 late'],
  '61_90': ['fail', '61–90 late'], over_90: ['fail', '90+ late']};
const FIN_DOC_STATUS: Record<string, FinPair> = {draft: ['waiting', 'Draft'], open: ['', 'Open'], scheduled: ['ready', 'Scheduled'], sent: ['', 'Sent'],
  partial: ['ready', 'Part paid'], paid: ['ok', 'Paid'], void: ['', 'Void']};
const FIN_ACT_WORD: Record<string, string> = {bill_create: 'Enter', bill_update: 'Change', bill_approve: 'Approve', bill_schedule: 'Schedule',
  bill_mark_paid: 'Mark paid', bill_void: 'Void', invoice_create: 'Create', invoice_send: 'Send', invoice_record_payment: 'Mark paid',
  invoice_write_off: 'Write off', invoice_void: 'Void'};
const FIN_ACT_STATUS: Record<string, FinPair> = {needs_approval: ['waiting', 'Waiting'], committed: ['ok', 'Done'], denied: ['fail', 'Denied'],
  failed: ['fail', 'Failed'], withdrawn: ['', 'Withdrawn']};
const FIN_PAID_FROM: Record<string, string> = {payment: 'Payment', feed: 'Bank row', credit: 'Credit', account: 'Recorded', expected: 'Expected'};
const FIN_PAY_STATUS: Record<string, FinPair> = {queued: ['waiting', 'Queued'], sending: ['waiting', 'Sending'], awaiting_bank_approval: ['waiting', 'Approve in Mercury'],
  submitted: ['ready', 'Submitted'], settled: ['ok', 'Settled'], unknown: ['waiting', 'Checking'], failed: ['fail', 'Pay failed'],
  returned: ['fail', 'Returned'], canceled: ['fail', 'Pay canceled'], denied: ['fail', 'Pay denied'],
  expired: ['fail', 'Approval expired'], withdrawn: ['fail', 'Pay withdrawn']};
const FIN_PAY_LIVE = new Set<string>(['queued', 'sending', 'awaiting_bank_approval', 'submitted', 'unknown']);

const finPill = ([cls, label]: FinPair) => `<span class="pill ${cls}">${esc(label)}</span>`;
const finDocStatus = (d: {status: string}) => finPill(FIN_DOC_STATUS[d.status] || ['', d.status]);
// An unpaid bill's payment worth showing: one on its way, or one that ended without paying an open bill.
const finBillPay = (d: FinDocAny) => {
  const p = d.payment;
  if (!p || d.status === 'paid' || d.status === 'void') return null;
  return FIN_PAY_LIVE.has(p.status) || d.status === 'open' ? p : null;
};
const finPayPill = (p: {status: string}) => finPill(FIN_PAY_STATUS[p.status] || ['', p.status]);
// A row's status: a live payment stands in for Scheduled; an ended one sits beside Open.
const finRowStatus = (d: FinDocAny) => {
  const p = finBillPay(d);
  return !p ? finDocStatus(d) : FIN_PAY_LIVE.has(p.status) ? finPayPill(p) : `${finDocStatus(d)} ${finPayPill(p)}`;
};
const finAgingPill = (d: {aging?: string | null}) => d.aging ? finPill(FIN_AGING[d.aging] || ['', d.aging]) : '';
// Cents as a form value: 1800, 45.5.
const finCentsValue = (c: number | null | undefined) => c == null ? '' : (c / 100).toFixed(2).replace(/\.00$/, '');

// A decision, dry run first: one the rules would commit goes straight through; one that needs approval comes back as
// {ask: approver, sig} and is sent only when called again with that sig. A denial throws its reason.
type FinDecided = {ask: string; sig: string; impact: FinDryImpact | undefined; status?: undefined; result?: undefined}
  | (FinActResult & {ask?: undefined; sig?: undefined; impact?: undefined});
async function finDecide(kind: string, payload: object, sig = ''): Promise<FinDecided> {
  const now = JSON.stringify([kind, payload]);
  if (sig !== now) {
    const dry: FinDryRun | null = await post('/v2/finance/actions', {kind, payload, dry_run: true});
    if (dry?.status === 'needs_approval') return {ask: dry.approver || '', sig: now, impact: dry.impact};
    if (dry?.status !== 'committed') throw new Error(dry?.reason || 'Not done');
  }
  return finAct(kind, payload);
}
// What a decision waiting for approval would leave in each budget it charges (the dry run's impact.budgets).
const finImpactHtml = (impact: {budgets?: FinImpactBudget[]} | null | undefined) => (impact?.budgets || []).filter(b => b.remaining != null)
  .map(b => finKv(`${b.name} left${finExcl(b)}`, `${finUsd(b.remaining)} → ${finUsd(b.remaining_after)}`)).join('');
const finAskText = (who: string) => who ? `Approval from ${finWho(who)}` : 'Needs approval';

// A dialog whose Save is a decision: the first Save is the dry run; when the rules ask for approval the dialog names
// the approver and the button becomes Send. Changing the form asks again. `build(form)` returns [kind, payload].
// F defaults to HTMLFormElement; every caller types its form callback, so F is inferred from that annotation.
function finDecideDialog<F extends HTMLFormElement = HTMLFormElement>(title: string, body: string, label: string,
  build: (form: F) => FinDecision | Promise<FinDecision>, done: string) {
  let sig = '';
  const dlg = finDialog(title, body + '<p class="fin-ask" data-ask hidden></p><div data-impact hidden></div>', label, async (form: F) => {
    const [kind, payload] = await build(form);
    const r = await finDecide(kind, payload, sig);
    if (r.ask != null) {
      sig = r.sig;
      const ask = finDocPart(form, '[data-ask]');
      ask.textContent = finAskText(r.ask); ask.hidden = false;
      const hit = finDocPart(form, '[data-impact]'); hit.innerHTML = finImpactHtml(r.impact); hit.hidden = !hit.innerHTML;
      finDocPart(form, '[type=submit]').textContent = 'Send';
      return false;
    }
    if (r.status === 'committed') toast(done);
    if (r.result?.bill_id) finDocShow('bill', r.result.bill_id);
    else if (r.result?.invoice_id) finDocShow('invoice', r.result.invoice_id);
    finDocsReload();
  });
  const form = finNeed(dlg.querySelector('form'), 'the dialog form');
  form.addEventListener('input', () => {
    if (!sig) return;
    sig = ''; finDocPart(form, '[data-ask]').hidden = true; finDocPart(form, '[data-impact]').hidden = true;
    finDocPart(form, '[type=submit]').textContent = label;
  });
  return dlg;
}
// A new bill or invoice is a draft: its list turns to Draft (All stays All) with no search, and it opens in the rail.
function finDocShow(kind: FinDocKind, id: string | number) {
  const f = FIN_DOC_F[kind];
  if (f.status) f.status = 'draft';
  f.q = '';
  if (FIN?.doc?.kind !== kind) return;
  FIN.doc.sel = String(id);
  const body: HTMLElement | null = $('#fin-body'), search = body?.querySelector<HTMLInputElement>('[data-fin-q]'), seg = body?.querySelector<HTMLElement>(`[data-fin-seg="status"] button[data-v="${f.status}"]`);
  if (search) search.value = '';
  if (seg) finSegSet(seg);
}
// After a write: the list (and the open document) read again, wherever the person is.
function finDocsReload() {
  if (FIN?.doc) void finDocLoad(false);
  if (FIN?.bs) void finBsLoad();
  if (FIN?.tx) void finTxLoad(false);
  if (FIN?.line) void finLineLoad();
  if (FIN?.bud) void finBudLoad();
}

// ----------------------------------------------------------------- the bills address
// The owner or an admin sets it (PUT /finance/bills-address), and outside view-as, if they hold finance.admin (the
// server's check, so the dialog says so); everyone else copies it.
const finEmailSetter = () => !finCaps()?.viewing && (S.me?.role === 'owner' || !!S.me?.bot_admin);
function finDocEmailDraw() {
  const box = $('#fin-doc-email'), email = FIN?.doc?.email || '';
  if (!box) return;
  if (finEmailSetter()) box.innerHTML = `<button type="button" class="ghost fin-copy" data-fin-email-set>${email
    ? `Forward to <b>${esc(email)}</b>` : 'Bills address'}</button>`;
  else box.innerHTML = email ? `<button type="button" class="ghost fin-copy" data-fin-copy="${esc(email)}" aria-label="Copy ${esc(email)}">Forward to <b>${esc(email)}</b></button>` : '';
}
// One input: an address sets it, empty clears it. An address no Tico address has is kept but named here.
function finDocEmailSet() {
  const state: FinDocPage = finNeed(FIN?.doc, 'the documents');
  finDialog('Bills address', `<label>Address<input type="email" name="address" maxlength="254" autocomplete="off" value="${esc(state.email || '')}"></label>`, 'Save', async (form: FinFormOf<{address: HTMLInputElement}>) => {
    const address = form.elements.address.value.trim();
    const r = await put('/v2/finance/bills-address', {address: address || null}).catch(e => {
      throw e.status === 403 ? new Error('Only a finance admin sets the bills address') : e;
    });
    if (FIN?.doc === state) { state.email = r?.bills_email || ''; finDocEmailDraw(); }
    if (r?.bills_email && !r.inbox_id) throw new Error('Not an inbox address');
  });
}

// ----------------------------------------------------------------- the page
function finDocPage(kind: FinDocKind) {
  const k = FIN_DOC[kind], f = FIN_DOC_F[kind], caps = finNeed(finCaps(), 'the finance caps');
  finNeed(FIN, 'the Finance page').doc = {kind, rows: [], next: null, sel: '', load: 0, one: null, oneLoad: 0, email: ''};
  $('#fin-body').innerHTML = `<div class="fin-bar"><label class="fin-search"><span class="nav-icon" aria-hidden="true">search</span><input type="search" data-fin-q value="${esc(f.q)}" placeholder="${esc(k.search)}" aria-label="${esc(k.search)}"></label>
      ${finSeg('status', 'Status', k.filters, f.status)}<span class="spacer"></span><span id="fin-doc-email" class="fin-doc-email"></span>
      ${kind === 'bill' && caps.owner && !caps.viewing ? '<label class="fin-inline" data-fin-direct-box hidden><input type="checkbox" role="switch" class="people-switch" data-fin-direct aria-label="Direct send"><span>Direct send</span></label>' : ''}
      ${caps.may(k.right) ? `<button type="button" class="primary" data-fin-doc-add>+ ${k.label}</button>` : ''}</div>`
    + finSplit(`<div class="fin-scroll" id="fin-docs">${skelRows(6, {dot: false})}</div>`, '', k.label);
  const body: HTMLElement = $('#fin-body');
  let timer = 0;
  const search = finDocPart<HTMLInputElement>(body, '[data-fin-q]');
  search.oninput = () => { clearTimeout(timer); timer = setTimeout(() => { f.q = search.value.trim(); void finDocLoad(false); }, 300); };
  body.onclick = (ev: Event) => {
    const t = eventElement(ev);
    if (!t) return;
    const seg = t.closest<HTMLElement>('[data-fin-seg] button');
    if (seg) { if (seg.dataset.v !== f.status) { f.status = seg.dataset.v; finSegSet(seg); void finDocLoad(false); } return; }
    if (t.closest('[data-fin-more]')) { void finDocLoad(true); return; }
    if (t.closest('[data-fin-doc-add]')) { void finDocAdd(kind); return; }
    if (t.closest('[data-fin-email-set]')) { finDocEmailSet(); return; }
    const copy = t.closest<HTMLElement>('[data-fin-copy]');
    if (copy) { void copyText(copy.dataset.finCopy!).then(() => toast('Copied'), () => toast('Could not copy', true)); return; }
    const tr = t.closest<HTMLElement>('tr[data-fin-doc]');
    if (tr && FIN?.doc && tr.dataset.finDoc !== FIN.doc.sel) { FIN.doc.sel = tr.dataset.finDoc!; finDocDraw(); void finDocOne(); }
  };
  void finDocLoad(false);
  void finDirectSend();
}
// The owner's company switch (payment_settings direct_send, backend/finance/payments.py): payments to recipients
// already paid may go straight to Mercury instead of its approval queue. The server checks it again at send. Shown
// once a Mercury payments connection exists (the Sources read carries the switch's state).
async function finDirectSend() {
  const box: HTMLElement | null = $('#fin-body [data-fin-direct-box]'); if (!box) return;
  let row: {payments?: {direct_send?: boolean}} | undefined | null = null;
  try { row = ((await get<{groups?: {rows?: {payments?: {direct_send?: boolean}}[]}[]}>('/v2/finance/sources?group=payments', {fresh: true})).groups || []).flatMap(g => g.rows || []).find(r => r.payments); } catch { return; }
  if (!row || !box.isConnected) return;
  const input = finDocPart<HTMLInputElement>(box, 'input');
  input.checked = !!row.payments?.direct_send; box.hidden = false;
  input.onchange = async () => {
    input.disabled = true;
    try { const r = await finAct('payment_settings', {direct_send: input.checked}); if (r.status === 'committed') toast(input.checked ? 'Direct send on' : 'Direct send off'); }
    catch (e) { toast(errorMessage(e, 'Not done'), true); input.checked = !input.checked; }
    finally { input.disabled = false; }
  };
}
async function finDocLoad(more: boolean) {
  const state: FinDocPage = finNeed(FIN?.doc, 'the documents'), k = FIN_DOC[state.kind], f = FIN_DOC_F[state.kind], load = ++state.load;
  const qs = new URLSearchParams({limit: '50'});
  if (f.q) qs.set('q', f.q.slice(0, 100));
  // Late: open invoices past due (the read's `late`).
  if (f.status === 'late') { qs.set('status', 'unpaid'); qs.set('late', 'true'); }
  else if (f.status) qs.set('status', f.status);
  if (more && state.next) qs.set('cursor', state.next);
  let d: FinDocList;
  try { d = await get<FinDocList>(`/v2/finance/${k.list}?${qs}${finAs()}`, {fresh: true}); }
  catch (e) { if (FIN?.doc === state && state.load === load && $('#fin-docs')) { $('#fin-docs').innerHTML = finFail(e); finRail(''); } return; }
  if (FIN?.doc !== state || state.load !== load || !$('#fin-docs')) return;
  state.rows = more ? [...state.rows, ...(d[k.list] || [])] : d[k.list] || [];
  state.next = d.next_cursor || null;
  if (state.kind === 'bill') { state.email = d.bills_email || ''; finDocEmailDraw(); }
  if (!state.rows.some(r => r.id === state.sel)) state.sel = state.rows[0]?.id || '';
  finDocDraw(); void finDocOne();
  if (!more) void finDocPaidLoad(state);
}
// Under open bills: the last few paid, newest first.
async function finDocPaidLoad(state: FinDocPage) {
  const f = FIN_DOC_F[state.kind];
  state.paid = [];
  if (state.kind !== 'bill' || f.status !== 'unpaid' || f.q) return;
  let d: FinDocList;
  try { d = await get<FinDocList>(`/v2/finance/bills?status=paid&order=paid_on&limit=5${finAs()}`, {fresh: true}); } catch { return; }
  if (FIN?.doc !== state) return;
  state.paid = d.bills || [];
  finDocDraw();
}
function finDocDraw() {
  const state = FIN?.doc, box = $('#fin-docs'); if (!box || !state) return;
  const k = FIN_DOC[state.kind], bill = state.kind === 'bill';
  const paid = bill ? state.paid || [] : [];
  const tr = (r: FinDocRow, cells: string) => `<tr data-fin-doc="${esc(r.id)}"${state.sel === r.id ? ' class="fin-on"' : ''}><td><button type="button" class="fin-link">${esc(r[k.contact]?.name || '—')}</button></td>${cells}</tr>`;
  const cell = (v: string) => v ? esc(v) : '<span class="muted">—</span>';
  // A bill's status carries its aging when late and the day it pays; an invoice's carries its aging.
  const status = (r: FinDocRow) => `${finRowStatus(r)}${r.aging && r.aging !== 'current' ? ' ' + finAgingPill(r) : ''}${bill && r.pay_on && r.status !== 'paid' ? ` <span class="muted fin-small">pays ${esc(finDayLabel(r.pay_on))}</span>` : ''}`;
  const open = !state.rows.length ? finNote(bill ? 'No bills.' : 'No invoices.') : bill
    ? `<table class="fin-t fin-doc-t"><thead><tr><th>Vendor</th><th>Bill</th><th>Due</th><th class="fin-n">Amount</th><th>Account</th><th>Team</th><th>Status</th><th>Came in</th></tr></thead><tbody>${state.rows.map(r => tr(r,
      `<td class="muted">${esc(r.number || '')}</td><td class="fin-nowrap">${esc(finDayLabel(r.due_date))}</td><td class="fin-n">${finCents(r.total)}</td>
      <td>${cell(finDocAccount(r))}</td><td>${cell(finDocTeam(r))}</td><td>${status(r)}</td><td>${esc(FIN_CAME_IN[r.origin ?? ''] || r.origin || '')}</td>`)).join('')}</tbody></table>`
    : `<table class="fin-t fin-doc-t"><thead><tr><th>Customer</th><th>Invoice</th><th>Sent</th><th>Due</th><th class="fin-n">Amount</th><th>Status</th></tr></thead><tbody>${state.rows.map(r => tr(r,
      `<td class="muted">${esc(r.number || '')}</td><td class="fin-nowrap">${r.sent_at ? esc(finDayLabel(r.sent_at)) : '<span class="muted">—</span>'}</td>
      <td class="fin-nowrap">${esc(finDayLabel(r.due_date))}</td><td class="fin-n">${finCents(r.total)}</td><td>${status(r)}</td>`)).join('')}</tbody></table>`;
  box.innerHTML = open + (state.next ? '<button type="button" class="ghost fin-more-btn" data-fin-more>More</button>' : '')
    + (paid.length ? `<h2 class="fin-h2">Recently paid</h2><table class="fin-t fin-doc-t"><thead><tr><th>Vendor</th><th>Paid</th><th class="fin-n">Amount</th><th>Account</th><th>Team</th><th>By</th></tr></thead><tbody>${paid.map(r => tr(r,
      `<td class="fin-nowrap">${r.paid_on ? esc(finDayLabel(r.paid_on)) : ''}</td><td class="fin-n">${finCents(r.total)}</td><td>${cell(finDocAccount(r))}</td>
      <td>${cell(finDocTeam(r))}</td><td>${r.paid_by ? esc(finWho(r.paid_by)) : '<span class="muted">—</span>'}</td>`)).join('')}</tbody></table>` : '');
}
// The selected document: drawn from its row at once, then whole (lines, files, payments, approvals, decisions).
async function finDocOne() {
  const state: FinDocPage = finNeed(FIN?.doc, 'the documents'), id = state.sel, load = ++state.oneLoad;
  state.one = null;
  const row = state.rows.find(r => r.id === id) || (state.paid || []).find(r => r.id === id);
  if (!row) { finRail(''); return; }
  finDocRail(row, false);
  let d: FinDocAny;
  try { d = await get<FinDocAny>(`/v2/finance/${FIN_DOC[state.kind].list}/${encodeURIComponent(id)}${finAs('?')}`, {fresh: true}); }
  catch (e) { if (FIN?.doc === state && state.oneLoad === load) finRail(finFail(e)); return; }
  if (FIN?.doc !== state || state.oneLoad !== load) return;
  state.one = d;
  finDocRail(d, true);
}
function finDocRail(d: FinDocAny, full: boolean) {
  const state: FinDocPage = finNeed(FIN?.doc, 'the documents'), k = FIN_DOC[state.kind], bill = state.kind === 'bill';
  const day = (s: string | null | undefined) => s ? esc(finDayLabel(s)) : '—';
  const rows = [finKv(k.label, esc(d.number || '—')), finKv('Date', day(d.date))];
  if (!bill && d.sent_at) rows.push(finKv('Sent', day(d.sent_at)));
  rows.push(finKv('Due', day(d.due_date)), finKv('Status', finDocStatus(d)));
  if (d.aging) rows.push(finKv('Aging', finAgingPill(d)));
  if (bill && d.pay_on) rows.push(finKv('Pays', day(d.pay_on)));
  const pay = bill ? finBillPay(d) : null;
  if (pay) rows.push(finKv('Payment', `${finPayPill(pay)}${pay.error && !FIN_PAY_LIVE.has(pay.status) ? ' ' + esc(pay.error) : ''}`));
  if (bill && d.approved_by) rows.push(finKv('Approved by', esc(finWho(d.approved_by))));
  if (d.paid) rows.push(finKv('Paid', finUsd(d.paid)));
  if (d.remaining && d.remaining !== d.total) rows.push(finKv('Left', finUsd(d.remaining)));
  if (!bill && d.written_off) rows.push(finKv('Written off', finUsd(d.written_off)));
  if (!bill && /^https:\/\//.test(d.payment_link || '')) rows.push(finKv('Pay link', `<a href="${esc(d.payment_link)}" target="_blank" rel="noopener">Open</a>`));
  if (d.memo) rows.push(finKv('Memo', esc(d.memo)));
  const parts = [`<div class="fin-rail-title"><h2>${esc(d[k.contact]?.name || '—')}</h2><span class="fin-big">${finUsd(d.total)}</span></div>`, rows.join('')];
  if (full) {
    const lines = d.lines || [], files = d.attachments || [], paid = d.settlements || [];
    const asked = (d.actions || []).filter(a => a.approval_id || a.status === 'needs_approval');
    if (lines.length) parts.push(`<h3 class="fin-h">Lines</h3>${lines.map(l => finKv([l.account?.name, l.description, l.taxable === true ? 'Taxable' : ''].filter(Boolean).join(' · ') || '—', finCents(l.amount))).join('')}`);
    if (files.length) parts.push(`<h3 class="fin-h">Files</h3><div class="fin-files">${files.map((f, i) =>
      `<a href="/api/v2/files/${encodeURIComponent(f.file_id)}" target="_blank" rel="noopener">File${files.length > 1 ? ' ' + (i + 1) : ''}</a>`).join('')}</div>`);
    if (paid.length) parts.push(`<h3 class="fin-h">Payments</h3>${paid.map(s => finKv(`${finDayLabel(s.settled_on)} · ${FIN_PAID_FROM[s.from_kind] || s.from_kind}`,
      finUsd(s.amount), s.voided_at ? 'fin-void' : '')).join('')}`);
    if (asked.length) parts.push(`<h3 class="fin-h">Approvals</h3>${asked.map(a => finKv(FIN_ACT_WORD[a.kind] || a.kind,
      `${finPill(FIN_ACT_STATUS[a.status] || ['', a.status])}${a.approval_to ? ' ' + esc(finWho(a.approval_to)) : ''}`)).join('')}`);
    parts.push(finDocActsHtml(d));
  }
  finRail(parts.join(''));
  const rail = $('#fin-rail');
  if (rail) rail.onclick = full ? (ev: Event) => { const b = eventElement(ev)?.closest<HTMLButtonElement>('[data-fin-do]'); if (b) finDocDo(b.dataset.finDo, d, b); } : null;
}
// Only the decisions the viewer may take on this document now.
function finDocActs(d: Partial<FinDocAny>): FinPair[] {
  const kind: FinDocKind = finNeed(FIN?.doc, 'the documents').kind, caps = finCaps();
  if (!caps?.may(FIN_DOC[kind].right)) return [];
  // Scheduling sends money, so it also needs finance.pay. A scheduled bill moves to another day until its payment is live.
  const may = caps.may('finance.pay'), pay: FinPair[] = may ? [['schedule', 'Schedule']] : [];
  const again: FinPair[] = may && !FIN_PAY_LIVE.has(d.payment?.status ?? '') ? [['schedule', 'Reschedule']] : [];
  const acts: Record<string, FinPair[]> = kind === 'bill'
    ? {draft: [['void', 'Void'], ['approve', 'Approve']], open: [['void', 'Void'], ['paid', 'Mark paid'], ...pay],
      scheduled: [['void', 'Void'], ['paid', 'Mark paid'], ...again]}
    : {draft: [['send', 'Send']], sent: [['writeoff', 'Write off'], ['paid', 'Mark paid']], partial: [['writeoff', 'Write off'], ['paid', 'Mark paid']]};
  return acts[d.status ?? ''] || [];
}
const finDocActsHtml = (d: Partial<FinDocAny>) => {
  const acts = finDocActs(d);
  return acts.length ? `<div class="fin-acts" id="fin-doc-acts">${acts.map(([v, l], i) =>
    `<button type="button" class="${i === acts.length - 1 ? 'primary' : 'ghost'}" data-fin-do="${v}">${l}</button>`).join('')}</div>` : '';
};
function finDocDo(what: string | undefined, d: FinDocAny, btn: HTMLButtonElement) {
  const bill = finNeed(FIN?.doc, 'the documents').kind === 'bill', id = bill ? {bill_id: d.id} : {invoice_id: d.id};
  if (what === 'approve') void finDocRun('bill_approve', id, 'Approved', btn);
  else if (what === 'send') void finDocRun('invoice_send', id, 'Sent', btn);
  else if (what === 'schedule') void finBillSchedule(d);
  else if (what === 'paid') void finDocMarkPaid(d);
  else if (what === 'void') finDecideDialog('Void bill', '<label>Reason<input name="reason" maxlength="200" autocomplete="off"></label>', 'Void', (form: FinFormOf<{reason: HTMLInputElement}>) => {
    const reason = form.elements.reason.value.trim();
    return ['bill_void', {...id, ...(reason ? {reason} : {})}];
  }, 'Voided');
  else if (what === 'writeoff') finDecideDialog('Write off', `<label>Amount<input name="amount" inputmode="decimal" required autocomplete="off" value="${finCentsValue(d.remaining)}"></label>
    <label>Memo<input name="memo" maxlength="200" autocomplete="off"></label>`, 'Write off', (form: FinFormOf<{amount: HTMLInputElement; memo: HTMLInputElement}>) => {
    const amount = finParseCents(form.elements.amount.value), memo = form.elements.memo.value.trim();
    if (!amount) throw new Error('A dollar amount, like 500');
    return ['invoice_write_off', {...id, amount, ...(memo ? {memo} : {})}];
  }, 'Written off');
}
// A rail button's decision: straight through, or the approver named in place of the buttons, then Send.
async function finDocRun(kind: string, payload: object, done: string, btn: HTMLButtonElement) {
  btn.disabled = true;
  let r;
  try { r = await finDecide(kind, payload); }
  catch (e) { toast(errorMessage(e, 'Not done'), true); btn.disabled = false; return; }
  if (r.ask == null) { if (r.status === 'committed') toast(done); finDocsReload(); return; }
  const box = $('#fin-doc-acts'); if (!box) return;
  box.innerHTML = `<span class="fin-ask">${esc(finAskText(r.ask))}</span><button type="button" class="ghost" data-fin-ask-no>Cancel</button><button type="button" class="primary" data-fin-ask-yes>Send</button>`;
  box.onclick = async (ev: Event) => {
    ev.stopPropagation();
    const t = eventElement(ev);
    if (t?.closest('[data-fin-ask-no]')) { box.outerHTML = finDocActsHtml(finNeed(FIN?.doc, 'the documents').one || {}); return; }
    const yes = t?.closest<HTMLButtonElement>('[data-fin-ask-yes]'); if (!yes) return;
    yes.disabled = true;
    try { await finDecide(kind, payload, r.sig); finDocsReload(); }
    catch (e) { toast(errorMessage(e, 'Not done'), true); yes.disabled = false; }
  };
}

// ----------------------------------------------------------------- what the forms pick from
// The lines the viewer may post to for a bill, an invoice or a journal entry. Read once per page per form.
async function finChart(use: string): Promise<FinChartAccount[]> {
  const fin = finNeed(FIN, 'the Finance page');
  fin.chart ||= {};
  if (fin.chart[use]) return fin.chart[use];
  const r = await get<import('../types/api').FinanceChart>(`/v2/finance/chart?for=${use}${finAs()}`, {fresh: true});
  return finNeed(FIN?.chart, 'the chart')[use] = r?.accounts || [];
}
// The bank accounts the viewer may pay a bill from (finance.pay).
async function finPayFrom(): Promise<FinPick[]> {
  const fin = finNeed(FIN, 'the Finance page');
  if (fin.payFrom) return fin.payFrom;
  const r = await get<import('../types/api').FinancePayFrom>('/v2/finance/pay-from' + finAs('?'), {fresh: true});
  return finNeed(FIN, 'the Finance page').payFrom = (r?.accounts || []).map(a => ({id: a.id, name: `${a.name}${a.last4 ? ' ••' + a.last4 : ''}`}));
}
const finOpts = (rows: FinPick[], val = '') => rows.map(r => `<option value="${esc(r.id)}"${r.id === val ? ' selected' : ''}>${esc(r.name)}</option>`).join('');
// Whom a viewer without company-wide view of a line may attribute it to: the chart read's `attributable` tags (the
// backend's own rule), with the viewer first, then teams, then teammates.
function finAttribution(line: FinChartAccount | undefined): FinPick[] {
  const me = finCaps()?.who || '', tags = line?.attributable || [];
  const mine = tags.filter(t => t.teammate === me), teams = tags.filter(t => t.team), mates = tags.filter(t => t.teammate && t.teammate !== me);
  return [...mine.map(t => ({id: 'teammate:' + t.teammate, name: finWho(t.teammate)})),
    ...teams.map(t => ({id: 'team:' + t.team, name: finTeamName(t.team)})),
    ...mates.map(t => ({id: 'teammate:' + t.teammate, name: finWho(t.teammate)}))];
}
// A "For" pick as a line's tags.
const finAttrTags = (v: string): Record<string, string> | null => { const i = v.indexOf(':'); return i > 0 ? {[v.slice(0, i)]: v.slice(i + 1)} : null; };

// ----------------------------------------------------------------- decisions with a form
async function finBillSchedule(d: FinDocAny) {
  let banks: FinPick[];
  try { banks = await finPayFrom(); } catch (e) { toast(errorMessage(e, 'Did not load.'), true); return; }
  const today = finToday(), again = d.status === 'scheduled', want = again && d.pay_on ? d.pay_on : d.due_date;
  const on = want && want > today ? want : today, word = again ? 'Reschedule' : 'Schedule';
  finDecideDialog(word, `<label>Pay on<input type="date" name="on" required value="${on}" min="${today}"></label>
    <label>From<select name="from" required>${finOpts(banks, again ? d.schedule?.money_account_id || '' : '')}</select></label>`, word, (form: FinFormOf<{on: HTMLInputElement; from: HTMLSelectElement}>) => {
    const pay_on = form.elements.on.value, from = form.elements.from.value;
    if (!pay_on) throw new Error('Pick a day');
    if (!from) throw new Error('No bank account to pay from');
    return ['bill_schedule', {bill_id: d.id, pay_on, money_account_id: from}];
  }, again ? 'Rescheduled' : 'Scheduled');
}
// Marked paid from (or to) a connected bank account, whose bank row links when it arrives, or "Elsewhere": no account,
// so the payment waits in clearing. Viewers without finance.pay see only "Elsewhere".
async function finDocMarkPaid(d: FinDocAny) {
  const bill = finNeed(FIN?.doc, 'the documents').kind === 'bill';
  const banks = finNeed(finCaps(), 'the finance caps').may('finance.pay') ? await finPayFrom().catch(() => []) : [];
  finDecideDialog('Mark paid', `<label>Amount<input name="amount" inputmode="decimal" required autocomplete="off" value="${finCentsValue(d.remaining)}"></label>
    <label>Date<input type="date" name="date" required value="${finToday()}"></label>
    <label>${bill ? 'From' : 'To'}<select name="from">${finOpts(banks)}<option value="">Elsewhere</option></select></label>`, 'Mark paid', (form: FinFormOf<{amount: HTMLInputElement; date: HTMLInputElement; from: HTMLSelectElement}>) => {
    const amount = finParseCents(form.elements.amount.value), date = form.elements.date.value, from = form.elements.from.value;
    if (!amount) throw new Error('A dollar amount, like 500');
    const payload = {[bill ? 'bill_id' : 'invoice_id']: d.id, amount, ...(date ? {date} : {}), ...(from ? {money_account_id: from} : {})};
    return [bill ? 'bill_mark_paid' : 'invoice_record_payment', payload];
  }, 'Marked paid');
}

// ----------------------------------------------------------------- entering a bill, an invoice, a journal entry
async function finContacts(kind: string): Promise<FinContact[]> {
  try { return (await get<import('../types/api').FinanceContacts>(`/v2/finance/contacts?kind=${kind}&limit=200${finAs()}`, {fresh: true}))?.contacts || []; } catch { return []; }
}
// A bill or invoice: its contact (or a new one), number, dates, one line and its amount, memo.
async function finDocAdd(kind: FinDocKind) {
  const k = FIN_DOC[kind], bill = kind === 'bill';
  let contacts: FinContact[], lines: FinChartAccount[];
  try { [contacts, lines] = await Promise.all([finContacts(k.contactKind), finChart(kind)]); }
  catch (e) { toast(errorMessage(e, 'Did not load.'), true); return; }
  // An invoice may also carry its sales tax: a line on a jurisdiction's sales tax account (a liability).
  if (!bill) lines = [...lines, ...(await finChart('journal').catch(() => [])).filter(l => l.subtype === 'sales_tax')];
  let made: {name: string; id: string} | null = null;   // a new contact made by an earlier Save, so a second Save does not make it twice
  // An invoice takes several lines, each taxable or not (a sales tax line itself is neither); a bill takes one.
  const invLine = () => `<div class="fin-il"><select name="acct" aria-label="Line">${finOpts(lines)}</select>
    <input name="amt" inputmode="decimal" autocomplete="off" aria-label="Amount"><label class="fin-check"><input type="checkbox" name="taxable">Taxable</label></div>`;
  const lineHtml = bill ? `<label>Line<select name="line" required>${finOpts(lines)}</select></label>
    <label data-fin-for hidden>For<select name="for"></select></label>
    <label>Amount<input name="amount" inputmode="decimal" required autocomplete="off"></label>`
    : `<div data-fin-il>${invLine()}</div><button type="button" class="ghost fin-jl-add" data-fin-il-add>+ Line</button>
    <label data-fin-for hidden>For<select name="for"></select></label>`;
  const dlg = finDecideDialog(k.label, `<label>${k.who}<select name="contact" required>${finOpts(contacts)}<option value="+">New ${k.who.toLowerCase()}</option></select></label>
    <label data-fin-new hidden>Name<input name="name" maxlength="120" autocomplete="off"></label>
    <label>Number<input name="number" maxlength="60" autocomplete="off"></label>
    <div class="fin-pair"><label>Date<input type="date" name="date" required value="${finToday()}"></label><label>Due<input type="date" name="due"></label></div>
    ${lineHtml}
    <label>Memo<input name="memo" maxlength="200" autocomplete="off"></label>`, 'Save', async (form: FinFormOf<FinDocFormControls>) => {
    const el = form.elements;
    let docLines: Record<string, unknown>[];
    if (bill) {
      const amount = finParseCents(el.amount.value);
      if (!amount) throw new Error('A dollar amount, like 500');
      if (!el.line.value) throw new Error('No line in your view');
      const line = lines.find(l => l.id === el.line.value), tags = line?.company_wide === false ? finAttrTags(el.for.value) : null;
      if (line?.company_wide === false && !tags) throw new Error('Pick who it is for');
      docLines = [{account_id: el.line.value, amount, ...(tags ? {tags} : {})}];
    } else {
      const who = finAttrTags(el.for.value);
      docLines = [];
      for (const row of form.querySelectorAll<HTMLElement>('[data-fin-il] .fin-il')) {
        const acct = finDocPart<HTMLSelectElement>(row, '[name=acct]').value, text = finDocPart<HTMLInputElement>(row, '[name=amt]').value.trim();
        if (!text) continue;
        const amount = finParseCents(text), line = lines.find(l => l.id === acct);
        if (!amount) throw new Error('A dollar amount on each line, like 500');
        if (!acct) throw new Error('No line in your view');
        if (line?.company_wide === false && !who) throw new Error('Pick who it is for');
        const box = finDocPart<HTMLInputElement>(row, '[name=taxable]'), shown = !finDocPart(row, '.fin-check').hidden;
        docLines.push({account_id: acct, amount, ...(shown ? {taxable: box.checked} : {}), ...(line?.company_wide === false ? {tags: who} : {})});
      }
      if (!docLines.length) throw new Error('A dollar amount, like 500');
    }
    if (!el.date.value) throw new Error('Pick a date');
    let contact = el.contact.value;
    if (contact === '+') {
      const name = el.name.value.trim();
      if (!name) throw new Error('Name the ' + k.who.toLowerCase());
      // A name already taken (any case) picks that contact; only a new name makes one.
      const same = made?.name === name ? null : await finContactNamed(contacts, name, k.contactKind);
      if (same) {
        if (!contacts.some(c => c.id === same.id)) { contacts.push(same); el.contact.insertAdjacentHTML('afterbegin', finOpts([same])); }
        el.contact.value = contact = same.id; sync();
      } else {
        if (made?.name !== name) made = {name, id: await finContactCreate(name, k.contactKind)};
        contact = made.id;
      }
    }
    if (!contact) throw new Error('Pick a ' + k.who.toLowerCase());
    const payload: Record<string, unknown> = {[bill ? 'vendor_id' : 'customer_id']: contact, [bill ? 'bill_date' : 'invoice_date']: el.date.value, lines: docLines};
    if (el.number.value.trim()) payload.number = el.number.value.trim();
    if (el.due.value) payload.due_date = el.due.value;
    if (el.memo.value.trim()) payload.memo = el.memo.value.trim();
    if (bill) payload.origin = 'manual';
    return [bill ? 'bill_create' : 'invoice_create', payload];
  }, 'Saved');
  const form = finNeed(dlg.querySelector<FinFormOf<FinDocFormControls>>('form'), 'the form'), pick = form.elements.contact;
  const sync = () => { finDocPart(form, '[data-fin-new]').hidden = pick.value !== '+'; };
  pick.addEventListener('change', sync); sync();
  if (bill) {
    finForSync(form, () => [lines.find(l => l.id === form.elements.line.value)]);
    form.elements.line.addEventListener('change', () => finForSync(form, () => [lines.find(l => l.id === form.elements.line.value)]));
    return;
  }
  // Taxable follows the server's default (taxable when the invoice has a sales tax line) until the person ticks one.
  let touched = false;
  const rows = () => [...form.querySelectorAll<HTMLElement>('[data-fin-il] .fin-il')];
  const lineOf = (row: HTMLElement) => lines.find(l => l.id === finDocPart<HTMLSelectElement>(row, '[name=acct]').value);
  const taxSync = () => {
    const taxed = rows().some(r => lineOf(r)?.subtype === 'sales_tax');
    for (const r of rows()) {
      const line = lineOf(r), label = finDocPart(r, '.fin-check');
      label.hidden = line?.subtype === 'sales_tax' || (!!line && line.type !== 'income');
      if (!touched) finDocPart<HTMLInputElement>(r, '[name=taxable]').checked = taxed;
    }
    finForSync(form, () => rows().map(lineOf));
  };
  finDocPart(form, '[data-fin-il-add]').onclick = () => { finDocPart(form, '[data-fin-il]').insertAdjacentHTML('beforeend', invLine()); taxSync(); };
  form.addEventListener('change', ev => {
    const target = eventElement(ev), name = target instanceof HTMLInputElement || target instanceof HTMLSelectElement ? target.name : '';
    if (name === 'taxable') touched = true;
    if (name === 'acct' || name === 'taxable') taxSync();
  });
  taxSync();
}
// Shows the "For" pick when a chosen line is not company-wide for the viewer, with whom they may attribute it to
// (the choices every such line allows) and their own scope first; keeps the pick when it still applies.
function finForSync(form: FinFormOf<{for: HTMLSelectElement}>, chosen: () => (FinChartAccount | undefined)[]) {
  const scoped = chosen().filter(l => l && l.company_wide === false), box = finDocPart(form, '[data-fin-for]'), sel = form.elements.for;
  box.hidden = !scoped.length;
  if (!scoped.length) return;
  const sets = scoped.map(l => finAttribution(l)), opts = finNeed(sets[0], 'the first line').filter(o => sets.every(s => s.some(x => x.id === o.id)));
  const keep = sel.value;
  sel.innerHTML = finOpts(opts, opts.some(o => o.id === keep) ? keep : opts[0]?.id || '');
}
// The contact with this name (names are unique, ignoring case): from the picker's list, else by search, since the
// list holds 200 and only this kind. One of the other kind is refused: a vendor can't be a customer's namesake.
async function finContactNamed(contacts: FinContact[], name: string, kind: string): Promise<FinContact | null> {
  const key = name.toLowerCase(), hit = (c: FinContact) => (c.name || '').trim().toLowerCase() === key;
  const listed = contacts.find(hit);
  if (listed) return listed;
  let found: FinContact[] = [];
  try { found = (await get<import('../types/api').FinanceContacts>(`/v2/finance/contacts?q=${encodeURIComponent(name.slice(0, 100))}&limit=50${finAs()}`, {fresh: true}))?.contacts || []; } catch { return null; }
  const same = found.find(hit);
  if (same && same.kind !== kind && same.kind !== 'both') throw new Error(`${same.name} is a ${same.kind}`);
  return same || null;
}
// A new vendor or customer; its id is the action's result.
async function finContactCreate(name: string, kind: string) {
  const r = await finAct('contact_create', {name, kind});
  if (r.status !== 'committed' || !r.result?.contact_id) throw new Error('The new contact waits for approval');
  return String(r.result.contact_id);
}
// A journal entry: a date, a memo and balanced lines (a debit or a credit each).
async function finJournalAdd() {
  let lines: FinChartAccount[];
  try { lines = await finChart('journal'); } catch (e) { toast(errorMessage(e, 'Did not load.'), true); return; }
  const row = () => `<div class="fin-jl"><select name="acct" aria-label="Line"><option value=""></option>${finOpts(lines)}</select>
    <input name="dr" inputmode="decimal" autocomplete="off" aria-label="Debit"><input name="cr" inputmode="decimal" autocomplete="off" aria-label="Credit"></div>`;
  const dlg = finDecideDialog('Journal entry', `<label>Date<input type="date" name="date" required value="${finToday()}"></label>
    <label>Memo<input name="memo" maxlength="200" autocomplete="off"></label>
    <div class="fin-jl fin-jl-head" aria-hidden="true"><span>Line</span><span>Debit</span><span>Credit</span></div>
    <div data-fin-jl>${row()}${row()}</div><button type="button" class="ghost fin-jl-add" data-fin-jl-add>+ Line</button>
    <label data-fin-for hidden>For<select name="for"></select></label>`, 'Post', (form: FinFormOf<{for: HTMLSelectElement; memo: HTMLInputElement; date: HTMLInputElement}>) => {
    const out: {account_id: string; amount: number; tags?: Record<string, string> | null}[] = [], who = finAttrTags(form.elements.for.value);
    for (const r of form.querySelectorAll<HTMLElement>('[data-fin-jl] .fin-jl')) {
      const acct = finDocPart<HTMLSelectElement>(r, '[name=acct]').value, dr = finDocPart<HTMLInputElement>(r, '[name=dr]').value.trim(), cr = finDocPart<HTMLInputElement>(r, '[name=cr]').value.trim();
      if (!acct && !dr && !cr) continue;
      const d = dr ? finParseCents(dr) : 0, c = cr ? finParseCents(cr) : 0;
      if (!acct) throw new Error('Pick a line for each amount');
      if (d == null || c == null || (d && c) || !(d || c)) throw new Error('A debit or a credit on each line, like 500');
      const scoped = lines.find(l => l.id === acct)?.company_wide === false;
      if (scoped && !who) throw new Error('Pick who it is for');
      out.push({account_id: acct, amount: d - c, ...(scoped ? {tags: who} : {})});
    }
    if (out.length < 2) throw new Error('Two lines or more');
    const off = out.reduce((s, l) => s + l.amount, 0);
    if (off) throw new Error(`Debits and credits differ by ${finUsd(Math.abs(off))}`);
    const memo = form.elements.memo.value.trim();
    return ['journal', {date: form.elements.date.value, ...(memo ? {memo} : {}), lines: out}];
  }, 'Posted');
  finDocPart(dlg, '[data-fin-jl-add]').onclick = () => finDocPart(dlg, '[data-fin-jl]').insertAdjacentHTML('beforeend', row());
  const form = finNeed(dlg.querySelector<FinFormOf<{for: HTMLSelectElement}>>('form'), 'the form');
  const chosen = () => [...form.querySelectorAll<HTMLSelectElement>('[data-fin-jl] [name=acct]')].map(s => lines.find(l => l.id === s.value));
  form.addEventListener('change', ev => { const target = eventElement(ev); if (target instanceof HTMLSelectElement && target.name === 'acct') finForSync(form, chosen); });
}

// ----------------------------------------------------------------- a loan, an asset
// A new balance sheet line and its opening entry: a loan is a liability line the money came into an asset from (Dr the
// account it went to, Cr the loan); an asset is an asset line paid from another (Dr the asset, Cr what paid). The line
// is made once (account_create), so a second Save after a failed entry reuses it.
const FIN_NEW_LINE: Record<'loan' | 'asset', {title: string; type: string; subtype: string; from: number; to: number; other: string;
  otherOk: (a: FinChartAccount) => boolean}> = {
  loan: {title: 'Loan', type: 'liability', subtype: 'loan', from: 2500, to: 2999, other: 'Into',
    otherOk: a => a.type === 'asset'},
  asset: {title: 'Asset', type: 'asset', subtype: 'fixed_asset', from: 1500, to: 1999, other: 'Paid from',
    otherOk: a => ['asset', 'liability', 'equity'].includes(a.type)},
};
// The first free code in the line's range (codes are unique in a book).
function finFreeCode(chart: FinChartAccount[], from: number, to: number) {
  const taken = new Set(chart.map(a => String(a.number)));
  for (let n = from; n <= to; n += 10) if (!taken.has(String(n))) return String(n);
  for (let n = from; n <= to; n++) if (!taken.has(String(n))) return String(n);
  return '';
}
async function finNewLineAdd(kind: 'loan' | 'asset') {
  const k = FIN_NEW_LINE[kind];
  let chart: FinChartAccount[];
  try { chart = await finChart('journal'); } catch (e) { toast(errorMessage(e, 'Did not load.'), true); return; }
  const others = chart.filter(k.otherOk);
  let made: {name: string; id: string} | null = null;   // the line an earlier Save made: {name, id}
  finDecideDialog(k.title, `<label>Name<input name="name" required maxlength="120" autocomplete="off"></label>
    <label>Amount<input name="amount" inputmode="decimal" required autocomplete="off"></label>
    <label>Date<input type="date" name="date" required value="${finToday()}"></label>
    <label>${k.other}<select name="other" required>${finOpts(others)}</select></label>`, 'Save', async (form: FinFormOf<{name: HTMLInputElement; amount: HTMLInputElement; date: HTMLInputElement; other: HTMLSelectElement}>) => {
    const el = form.elements, name = el.name.value.trim(), amount = finParseCents(el.amount.value);
    if (!name) throw new Error('Name it');
    if (!amount) throw new Error('A dollar amount, like 500');
    if (!el.other.value) throw new Error('No line to pair it with');
    if (made?.name !== name) {
      const code = finFreeCode(chart, k.from, k.to);
      if (!code) throw new Error('No free account code');
      const r = await finAct('account_create', {code, name, type: k.type, subtype: k.subtype});
      if (r.status !== 'committed' || !r.result?.account_id) throw new Error('The new line waits for approval');
      made = {name, id: String(r.result.account_id)};
      chart.push({id: made.id, number: code, name, type: k.type, subtype: k.subtype});
      const charts = finNeed(FIN, 'the Finance page').chart;
      if (charts) delete charts.journal;
    }
    const lines = kind === 'loan' ? [{account_id: el.other.value, amount}, {account_id: made.id, amount: -amount}]
      : [{account_id: made.id, amount}, {account_id: el.other.value, amount: -amount}];
    return ['journal', {date: el.date.value, memo: name, lines}];
  }, 'Saved');
}

// ----------------------------------------------------------------- the Balance sheet's "+ Add"
// Connect a bank or card account (Mercury) or payments (Stripe); enter an invoice, a bill, a loan or credit line, an asset, a journal
// entry or a spreadsheet's history: only what the viewer may do.
function finAddMenuHtml() {
  const caps = finCaps();
  if (!caps || caps.viewing) return '';
  const book = caps.may('finance.book');
  const items = [caps.receivables && caps.may('finance.ar') ? ['invoice', 'Invoice'] : null, caps.payables && caps.may('finance.ap') ? ['bill', 'Bill'] : null,
    book ? ['loan', 'Loan or credit line'] : null, book ? ['asset', 'Asset'] : null, book ? ['journal', 'Journal entry'] : null,
    book && caps.all ? ['import', 'Import history'] : null].filter(Boolean) as FinPair[];
  // A bank and a card account (Mercury IO credit is a card account) both connect through Mercury; payments through Stripe.
  const connect: FinPair[] = caps.sources && caps.may('finance.admin') ? [['mercury', 'Bank account'], ['mercury-card', 'Card account'], ['stripe', 'Payments']] : [];
  if (!items.length && !connect.length) return '';
  const item = ([v, l]: FinPair) => `<button type="button" role="menuitem" data-fin-add-item="${v}">${l}</button>`;
  return `<span class="fin-pop-at"><button type="button" class="primary" data-fin-add aria-haspopup="menu" aria-expanded="false" aria-controls="fin-add">+ Add</button>
    <div class="fin-pop fin-menu" id="fin-add" role="menu" aria-label="Add" hidden>${connect.length ? `<span class="fin-menu-h">Connect</span>${connect.map(item).join('')}` : ''}${
      items.length ? `${connect.length ? '<hr>' : ''}<span class="fin-menu-h">Enter</span>${items.map(item).join('')}` : ''}</div></span>`;
}
// The Balance sheet's click handler calls this first; true when the click was the menu's.
function finAddClick(ev: Event) {
  const t = eventElement(ev);
  if (!t) return false;
  const btn = t.closest<HTMLElement>('[data-fin-add]');
  if (btn) { finPop(btn, $('#fin-add')); return true; }
  const item = t.closest<HTMLElement>('[data-fin-add-item]');
  if (!item) return false;
  $('#fin-add').hidden = true; $('[data-fin-add]')?.setAttribute('aria-expanded', 'false');
  const v = item.dataset.finAddItem;
  if (v === 'journal') void finJournalAdd();
  else if (v === 'loan' || v === 'asset') void finNewLineAdd(v);
  else if (v === 'import') finImportOpen();
  else if (v === 'mercury' || v === 'mercury-card') finConnectMercury();
  else if (v === 'stripe') finConnectStripe();
  else if (v === 'invoice' || v === 'bill') void finDocAdd(v);
  return true;
}
