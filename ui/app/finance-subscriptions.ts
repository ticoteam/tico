// Subscriptions under the P&L table (finance-pnl.ts): the recurring charges the daily look suggests and the Bookkeeper
// confirms (backend/finance/subscriptions.py), with the monthly cost, cadence, next charge and seats of each. A suggested
// row shows Confirm and Ignore to a reader with finance.book; everyone else sees its status. The section is hidden when
// there are none. Confirm and Ignore post subscription_confirm, and the section reads again. Phone width: the table scrolls
// in its container.

'use strict';

interface FinSubRow {
  id: string;
  vendor: {id: string; name: string};
  status: 'suggested' | 'active' | 'ignored' | 'cancelled';
  cadence: 'monthly' | 'annual';
  amount: number;
  monthly: number;
  seats: number | null;
  next_charge_on: string | null;
  card: {id: string; name: string} | null;
  attribution: {to: string; share: number}[];
  source: string;
  as_of: string;
}

interface FinSubRead {
  rows: FinSubRow[];
  monthly_total: number;
  as_of: string;
}

const FIN_SUB_STATUS: Record<FinSubRow['status'], [string, string]> = {
  suggested: ['Suggested', 'needs'],
  active: ['Active', 'active'],
  ignored: ['Ignored', ''],
  cancelled: ['Cancelled', ''],
};

// The table: the vendor, its monthly cost in whole cents as dollars, its cadence, its next charge, its seats and its
// status (Confirm and Ignore on a suggestion, for a reader who may confirm it).
function finSubTable(read: FinSubRead, may: boolean): string {
  const body = read.rows.map(r => {
    const [label, tone] = FIN_SUB_STATUS[r.status];
    const status = may && r.status === 'suggested'
      ? `<button type="button" class="ghost" data-fin-sub-id="${esc(r.id)}" data-fin-sub-status="active">Confirm</button>
         <button type="button" class="ghost" data-fin-sub-id="${esc(r.id)}" data-fin-sub-status="ignored">Ignore</button>`
      : `<span class="pill ${tone}">${label}</span>`;
    return `<tr data-fin-sub="${esc(r.id)}"><td>${esc(r.vendor.name || '—')}</td>
      <td class="fin-n">${finUsd(r.monthly)}</td><td>${r.cadence === 'annual' ? 'Annual' : 'Monthly'}</td>
      <td>${r.next_charge_on ? esc(finDayLabel(r.next_charge_on)) : '—'}</td><td class="fin-n">${r.seats ?? '—'}</td>
      <td>${status}</td></tr>`;
  }).join('');
  return `<h2 class="fin-h2">Subscriptions <span class="muted">${finUsd(read.monthly_total)} / mo</span></h2>
    <div class="scroll"><table class="fin-t fin-sub-t"><thead><tr><th>Vendor</th><th class="fin-n">Monthly</th><th>Cadence</th>
    <th>Next charge</th><th class="fin-n">Seats</th><th>Status</th></tr></thead><tbody>${body}</tbody></table></div>`;
}

// Reads the subscriptions into the section (hidden when there are none, or when they do not load).
async function finSubsLoad(): Promise<void> {
  const box = document.getElementById('fin-subs');
  if (!box) return;
  const may = finCaps()?.may('finance.book') === true;
  let read: FinSubRead;
  try {
    read = await get<FinSubRead>('/v2/finance/subscriptions' + finAs('?'), {fresh: true});
  } catch {
    box.hidden = true;
    box.innerHTML = '';
    return;
  }
  box.hidden = read.rows.length === 0;
  box.innerHTML = read.rows.length ? finSubTable(read, may) : '';
  box.onclick = ev => {
    const btn = eventElement(ev)?.closest<HTMLButtonElement>('button[data-fin-sub-id]');
    if (btn) void finSubSet(btn);
  };
}

// Confirm or Ignore a suggestion: subscription_confirm with the status picked, then the section reads again.
async function finSubSet(btn: HTMLButtonElement): Promise<void> {
  const payload = {subscription_id: btn.dataset.finSubId ?? '', status: btn.dataset.finSubStatus ?? ''};
  btn.disabled = true;
  try { await finAct('subscription_confirm', payload); }
  catch (e) { toast(e instanceof Error ? e.message : 'Not saved', true); }
  await finSubsLoad();
}
