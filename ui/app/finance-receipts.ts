// Receipts above the Transactions list (backend/finance/receipts.py): the Library files filed as receipts, each with the
// amount, date and vendor it reads as and the bank or card row it is suggested on or attached to. A suggested match is
// attached or refused here, and an attached receipt can be undone (receipt_match, receipt_match_undo). A receipt not read
// yet says so; its read is set through the API. The first page of the receipts the viewer sees, newest first; nothing on
// the transfers view. Phone width: each receipt is one stacked row.

'use strict';

interface FinReceipt {
  id: string;
  file: {id: string; name: string};
  read: {amount: number | null; date: string | null; vendor: {id: string; name: string} | null};
  match: {feed: {id: string; date: string; amount: number; description: string}; confidence: number | null} | null;
  status: string;
  as_of: string;
}

interface FinReceiptsRead {rows: FinReceipt[]; next: string | null; as_of: string}

const FIN_RC: {rows: FinReceipt[]} = {rows: []};
const FIN_RC_LABEL: Record<string, string> = {unread: 'To read', suggested: 'Suggested match', unmatched: 'No match yet', attached: 'Matched', rejected: 'Refused'};
const FIN_RC_PILL: Record<string, string> = {suggested: 'waiting', unread: 'idle', unmatched: 'idle', attached: 'ok', rejected: 'idle'};

// The first page of receipts. A failed read shows nothing: the Transactions list below it is the page.
async function finReceiptsLoad(): Promise<void> {
  const box = $('#fin-receipts');
  if (!box) return;
  if (FIN_TX.source === 'transfer') { FIN_RC.rows = []; box.innerHTML = ''; return; }
  let d: FinReceiptsRead | null = null;
  try { d = await get<FinReceiptsRead>('/v2/finance/receipts?limit=20' + finAs(), {fresh: true}); }
  catch { d = null; }
  if (!$('#fin-receipts')) return;
  FIN_RC.rows = (d && d.rows) || [];
  finReceiptsDraw();
}

function finReceiptsDraw(): void {
  const box = $('#fin-receipts');
  if (!box) return;
  if (!FIN_RC.rows.length) { box.innerHTML = ''; return; }
  box.innerHTML = `<section class="fin-receipts" aria-label="Receipts"><h3 class="fin-receipts-h">Receipts</h3><ul class="fin-receipt-list">${FIN_RC.rows.map(finReceiptRow).join('')}</ul></section>`;
  box.onclick = (e: MouseEvent) => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>('button[data-fin-rc-act]');
    if (b) void finReceiptAct(b);
  };
}

function finReceiptRow(r: FinReceipt): string {
  const bits: string[] = [];
  if (r.read.amount != null) bits.push(finCents(r.read.amount));
  if (r.read.date) bits.push(finDayLabel(r.read.date));
  if (r.read.vendor) bits.push(r.read.vendor.name);
  const read = bits.length ? bits.join(' · ') : 'Not read yet';
  const feed = r.match
    ? `<div class="muted">${r.status === 'attached' ? 'Filed to' : 'Suggested on'}: ${esc(r.match.feed.description || 'a bank or card row')} · ${finCents(Math.abs(r.match.feed.amount))} · ${esc(finDayLabel(r.match.feed.date))}</div>`
    : '';
  let acts = '';
  if (r.status === 'suggested') acts = `<button type="button" data-fin-rc-act="accept" data-fin-rc="${esc(r.id)}">Attach</button><button type="button" class="ghost" data-fin-rc-act="reject" data-fin-rc="${esc(r.id)}">Refuse</button>`;
  else if (r.status === 'attached' && r.match) acts = `<button type="button" class="ghost" data-fin-rc-act="undo" data-fin-rc="${esc(r.id)}">Undo</button>`;
  return `<li class="fin-receipt" data-fin-receipt="${esc(r.id)}"><div class="fin-receipt-head"><strong>${esc(r.file.name || 'Receipt')}</strong><span class="pill ${FIN_RC_PILL[r.status] || 'idle'}">${esc(FIN_RC_LABEL[r.status] || r.status)}</span></div><div class="muted">${esc(read)}</div>${feed}${acts ? `<div class="fin-receipt-acts">${acts}</div>` : ''}</li>`;
}

// Attach or refuse a suggestion, or undo an attach; the list then reads again so it shows what was saved.
async function finReceiptAct(btn: HTMLButtonElement): Promise<void> {
  const id = btn.dataset.finRc ?? '', act = btn.dataset.finRcAct ?? '';
  const kind = act === 'undo' ? 'receipt_match_undo' : 'receipt_match';
  const payload: Record<string, string> = act === 'undo' ? {receipt_id: id} : {receipt_id: id, decision: act === 'accept' ? 'accept' : 'reject'};
  btn.disabled = true;
  try {
    // finAct toasts a request sent for approval itself; a committed write is confirmed here.
    const r = await finAct(kind, payload);
    if (r?.status === 'committed') toast(act === 'accept' ? 'Attached' : act === 'reject' ? 'Refused' : 'Undone');
  } catch (e) { toast(e instanceof Error ? e.message : 'Not saved', true); }
  await finReceiptsLoad();
}
