/* ui/app/settings-billing.ts — Settings > Billing: balance and status, credits, usage by day and hour, statements
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// What GET /v2/billing answers (backend/company_billing.py), as this page reads it. Money is whole micro-dollars.
interface BillingCloud { access?: string; reason?: string; plan?: string; trial_days_left?: number | null; billing_url?: string | null; }
interface BillingCredit {
  label: string; amount_micros: number; left_micros: number; expires?: string | null; current: boolean;
  expired?: boolean; expired_micros: number;
}
interface BillingLine { product: string; sku?: string | null; amount_micros: number; }
// One day of usage on the page, or one hour inside a day (GET /v2/billing/usage).
interface BillingBucket { period: string; total_micros: number; lines: BillingLine[]; }
interface BillingStatement {
  opening_micros: number; credits_by_source: Record<string, number>; debits_by_product: Record<string, number>;
  expired_micros?: number | null; closing_micros: number;
}
interface BillingView {
  state: string; month: string; months: string[]; balance_micros: number; min_balance_micros: number;
  grace_until: string | null; booking_url: string | null; record_only: boolean; cloud?: BillingCloud | null;
  credits: BillingCredit[]; usage: BillingBucket[]; statement: BillingStatement;
}
// GET /v2/billing/usage?day=…: one day's hours, or the reason HQ gave none.
interface BillingHoursView { state: string; hours?: BillingBucket[]; }

// Owners and admins only (backend/company_billing.py reads Tico HQ; the server refuses everyone else). Money arrives
// as whole micro-dollars and is written here with integers only: a float would round a statement wrong.
let BILLING_MONTH = '', BILLING_ASKED = 0;
const billingCents = (micros: number): string => {
  const sign = micros < 0 ? '-' : '', abs = Math.abs(micros), cents = Math.floor((abs + 5000) / 10000);
  return `${sign}$${Math.floor(cents / 100).toLocaleString('en-US')}.${String(cents % 100).padStart(2, '0')}`;
};
// A line under one cent says so rather than $0.00; a total is to the cent.
const billingLine = (micros: number): string => micros !== 0 && Math.abs(micros) < 10000 ? (micros < 0 ? '-<$0.01' : '<$0.01') : billingCents(micros);
const BILLING_PRODUCTS: Record<string, string> = {gateway: 'Tico models', hosting: 'Hosting', support: 'Support', refund: 'Refunds'};
const BILLING_SOURCES: Record<string, string> = {grant: 'Grant', adjustment: 'Credit added by Tico', arrangement: 'Credit added by Tico', payment: 'Payment', refund_back: 'Payment'};
const billingModel = (sku: string | null | undefined): string => String(sku || '').split(':')[0] || '—';
function billingStatus(v: BillingView): [string, string] {
  const min = billingCents(v.min_balance_micros);
  if (v.state === 'grace') {
    // grace_until is a date, or null: the page has always drawn null as the epoch, since new Date(null) is 0.
    const at = new Date(v.grace_until ?? 0), day = at.toLocaleDateString('en-US', {timeZone: 'UTC', month: 'long', day: 'numeric', year: 'numeric'});
    const time = at.toLocaleTimeString('en-GB', {timeZone: 'UTC', hour: '2-digit', minute: '2-digit'});
    return ['bad', `Hosting grace period: servers stop on ${day} at ${time} UTC unless credit is added.`];
  }
  if (v.state === 'paused') return ['bad', `Tico models paused: balance reached ${min}. Your bots are using their other models.`];
  if (v.state === 'low') return ['warn', `Low balance: Tico models stop when your balance reaches ${min}`];
  return ['ok', 'Active'];
}
function billingCloudStatus(cloud: BillingCloud): [string, string] {
  if (cloud.access === 'locked') return ['bad', cloud.reason === 'unpaid' ? 'Locked: payment failed' : 'Locked: trial ended'];
  if (cloud.access === 'frozen') return ['bad', 'Out of credit: Tico models and routines paused'];
  if (cloud.plan === 'trial') return ['ok', cloud.trial_days_left === 1 ? 'Trial · 1 day left' : `Trial · ${cloud.trial_days_left ?? 0} days left`];
  return ['ok', cloud.plan === 'starter' ? 'Starter' : 'Active'];
}
// Hours inside a day, split the same way as the day: by product, then model.
function billingSplit(lines: BillingLine[]): string {
  const rows = new Map<string, number>();
  for (const line of lines) {
    const key = `${line.product}\u0000${line.product === 'gateway' ? billingModel(line.sku) : line.sku || ''}`;
    rows.set(key, (rows.get(key) || 0) + line.amount_micros);
  }
  return [...rows].map(([key, micros]) => {
    // The key is the product and the model joined by a NUL, so split always yields both; the defaults satisfy the types.
    const [product = '', model = ''] = key.split('\u0000');
    return `<tr><td>${esc(BILLING_PRODUCTS[product] || product)}</td><td>${esc(model || '—')}</td><td class="num">${billingLine(micros)}</td></tr>`;
  }).join('');
}
function billingCredits(credits: BillingCredit[]): string {
  const row = (c: BillingCredit): string => `<tr><td>${esc(c.label)}</td><td class="num">${billingCents(c.amount_micros)}</td><td class="num">${billingCents(Math.max(c.left_micros, 0))}</td>
    <td>${c.expires ? esc(new Date(c.expires).toLocaleDateString('en-US', {timeZone: 'UTC', month: 'short', day: 'numeric', year: 'numeric'})) : 'Never'}</td>
    ${c.current ? '' : `<td>${c.expired ? `Expired · ${billingCents(c.expired_micros)} expired` : 'Used up'}</td>`}</tr>`;
  const head = (past: boolean): string => `<thead><tr><th>Source</th><th class="num">Amount</th><th class="num">Left</th><th>Ends</th>${past ? '<th></th>' : ''}</tr></thead>`;
  const current = credits.filter(c => c.current), past = credits.filter(c => !c.current);
  return `${current.length ? `<table class="billing-table">${head(false)}<tbody>${current.map(row).join('')}</tbody></table>` : '<p class="muted">No credit left.</p>'}
    ${past.length ? `<details class="billing-past"><summary>Show past credits (${past.length})</summary><table class="billing-table">${head(true)}<tbody>${past.map(row).join('')}</tbody></table></details>` : ''}`;
}
function billingStatement(s: BillingStatement): string {
  const expired: [string, number][] = s.expired_micros ? [['Expired credit', -s.expired_micros]] : [];
  const lines: [string, number][] = [['Opening balance', s.opening_micros],
    ...Object.entries(s.credits_by_source).map(([source, micros]): [string, number] => [`Credits: ${BILLING_SOURCES[source] || source}`, micros]),
    ...Object.entries(s.debits_by_product).map(([product, micros]): [string, number] => [`Debits: ${BILLING_PRODUCTS[product] || product}`, -micros]),
    ...expired, ['Closing balance', s.closing_micros]];
  return `<table class="billing-table billing-statement"><tbody>${lines.map(([label, micros], i) =>
    `<tr${i === 0 || i === lines.length - 1 ? ' class="billing-total"' : ''}><td>${esc(label)}</td><td class="num">${billingCents(micros)}</td></tr>`).join('')}</tbody></table>`;
}
const BILLING_NO_CREDIT = 'No credit yet. Tico models and Hosting start once credit is added.';
const BILLING_EMPTY: Record<string, string> = {
  no_gateway: 'Billing shows here once this install is connected to the Tico Gateway (Settings › Models).',
  refused: 'Tico HQ did not accept this install’s gateway key. Check it on the Tico Gateway card in Settings › Models.',
  unreachable: 'Tico HQ did not answer. Try again in a minute.',
};
// The line for a state (unreachable when unknown), with "Settings › Models" a link to the Tico Gateway card: the
// settings catalogue's address for it (backend/settings_entries.py), which opens Models and scrolls to the card.
const billingEmptyHTML = (state: string): string => esc(BILLING_EMPTY[state] || BILLING_EMPTY.unreachable!)
  .replace('Settings › Models', '<a href="#/settings/models/settings-gateway-card" data-billing-gateway>Settings › Models</a>');
async function renderSettingsBilling(): Promise<void> {
  const host = document.querySelector<HTMLElement>('#set-billing');
  if (!host) return;
  // Months picked quickly can answer out of order: only the newest read draws the page.
  const asked = ++BILLING_ASKED;
  let v: BillingView;
  try { v = await get('/v2/billing' + (BILLING_MONTH ? `?month=${encodeURIComponent(BILLING_MONTH)}` : '')); }
  catch (e) { if (asked === BILLING_ASKED) host.innerHTML = `<div class="empty">${esc(errorMessage(e, ''))}</div>`; return; }
  if (asked !== BILLING_ASKED) return;
  if (BILLING_EMPTY[v.state]) { host.innerHTML = `<div class="empty">${billingEmptyHTML(v.state)}</div>`; return; }
  const talk = (url: string | null | undefined): string => url ? `<a class="primary btn" href="${esc(url)}" target="_blank" rel="noopener noreferrer" data-billing-talk>Talk to us to add credit</a>`
    : '<button class="primary" type="button" disabled data-billing-talk title="Tico has not set up its booking link yet">Talk to us to add credit</button>';
  // No account at HQ yet: the empty state alone. An account with no credit still shows its balance and status (staff
  // may have set a minimum below $0, so it can have usage, be paused or be in Hosting's grace).
  if (v.state === 'no_account') {
    host.innerHTML = `<div class="billing-empty"><p>${BILLING_NO_CREDIT}</p><p class="muted">Usage is recorded but not charged yet.</p>
      <div class="row">${talk(v.booking_url)}</div></div>`;
    return;
  }
  // A Tico Cloud company (backend/cloud_access.py): its plan and access, and its own billing page in place of Talk to us.
  const cloud = v.cloud, pay = cloud ? cloudPayUrl(cloud.billing_url) : '';
  const [tone, line] = cloud ? billingCloudStatus(cloud) : billingStatus(v);
  const action = !cloud ? talk(v.booking_url) : pay ? `<a class="primary btn" href="${esc(pay)}" target="_blank" rel="noopener noreferrer" data-billing-cloud>${cloud.access === 'frozen' ? 'Add credit' : cloud.access === 'locked' ? 'Pay to continue' : 'Manage billing'}</a>` : '';
  host.innerHTML = `<div class="billing-head"><div><div class="billing-balance">${billingCents(v.balance_micros)}</div>
      <p class="billing-status" data-tone="${tone}" role="status">${esc(line)}</p>
      ${v.record_only && !cloud ? '<p class="muted">Usage is recorded but not charged yet.</p>' : ''}</div>
      <div class="row">${action}</div></div>
    <h3>Credits</h3>${v.credits.length ? billingCredits(v.credits) : `<p class="billing-empty">${BILLING_NO_CREDIT}</p>`}
    ${billingUsageHTML(v)}`;
  billingBind(host);
}
function billingUsageHTML(v: BillingView): string {
  const months = v.months.length ? v.months : [v.month];
  return `<h3>Usage <select data-billing-month aria-label="Month">${months.map(m => `<option value="${esc(m)}"${m === v.month ? ' selected' : ''}>${esc(m)}</option>`).join('')}</select></h3>
    ${v.usage.length ? `<div class="billing-days">${v.usage.map(d => `<details data-billing-day="${esc(d.period)}"><summary><span>${esc(d.period)}</span><span class="num">${billingCents(d.total_micros)}</span></summary>
      <table class="billing-table"><tbody>${billingSplit(d.lines)}</tbody></table><div data-billing-hours></div></details>`).join('')}</div>` : '<p class="muted">No usage this month.</p>'}
    <h3>Statement for ${esc(v.month)}</h3>${billingStatement(v.statement)}
    <p><a class="link" href="${API}/v2/billing/statements/${encodeURIComponent(v.month)}.csv" download="tico-statement-${esc(v.month)}.csv" data-billing-csv>Download CSV</a></p>`;
}
function billingBind(host: HTMLElement): void {
  const month = host.querySelector<HTMLSelectElement>('[data-billing-month]');
  if (month) month.onchange = () => { BILLING_MONTH = month.value; void renderSettingsBilling(); };
  host.querySelectorAll<HTMLDetailsElement>('[data-billing-day]').forEach(day => { day.addEventListener('toggle', async () => {
    const hours = day.querySelector<HTMLElement>('[data-billing-hours]')!;
    if (!day.open || hours.dataset.loaded) return;
    hours.dataset.loaded = '1';
    hours.innerHTML = '<p class="muted">Loading hours…</p>';
    try {
      const r: BillingHoursView = await get(`/v2/billing/usage?day=${encodeURIComponent(day.dataset.billingDay ?? '')}`);
      // HQ refused or did not answer: say so, and ask again the next time the day opens.
      if (r.state !== 'ok') { hours.innerHTML = `<p class="muted">${billingEmptyHTML(r.state)}</p>`; delete hours.dataset.loaded; return; }
      hours.innerHTML = r.hours?.length ? `<h4>By hour</h4>${r.hours.map(h => `<div class="billing-hour"><div class="billing-hour-head"><span>${esc(h.period.slice(11))}:00 UTC</span><span class="num">${billingCents(h.total_micros)}</span></div>
        <table class="billing-table"><tbody>${billingSplit(h.lines)}</tbody></table></div>`).join('')}` : '<p class="muted">No hours to show.</p>';
    } catch (e) { hours.innerHTML = `<p class="muted">${esc(errorMessage(e, ''))}</p>`; delete hours.dataset.loaded; }
  }); });
}
