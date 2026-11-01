/* ui/app/finance-books.js — Finance › Balance sheet, Cards and Transactions (spec §12).
   Balance sheet: assets, liabilities and equity as of a day (and a day to compare with), each line with the accounts
   that feed it; the totals are the route's. "+ Add" enters an invoice, bill or journal entry (finance-docs.js) or leads
   to Sources to connect one. Cards: each card with its holder, budget, limit and spend this period;
   the rail freezes, unfreezes and sets the limit. Transactions: bank feed rows and entries, searched and filtered,
   paged by cursor; for those who keep the books the rail's one form sets a bank row's line and tags (categorize,
   a dry run first), and tags an entry's or a reconciled row's lines; it matches a new bank row to the payments marked
   paid it is the bank side of (feed_match).
   Reads: GET /api/v2/finance/balance-sheet, /finance/cards, /finance/transactions, /finance/budgets, /finance/mercury-users,
   /finance/chart, /finance/feed/{id}/expected.
   Writes (POST /api/v2/finance/actions): card_create, card_freeze, card_unfreeze, card_update_limit, categorize, feed_match.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- balance sheet
const FIN_BS = {asOf: '', compare: '', sel: ''};
const FIN_BS_SECTIONS = [['assets', 'Assets', 'asset'], ['liabilities', 'Liabilities', 'liability'], ['equity', 'Equity', 'equity']];
function finBalancePage() {
  const b = FIN_BS;
  if (!b.asOf) b.asOf = finToday();
  FIN.bs = {data: null, prev: null, load: 0};
  $('#fin-body').innerHTML = `<div class="fin-bar"><label class="fin-inline"><span class="muted">As of</span><input type="date" data-fin-asof value="${b.asOf}"></label>
      <label class="fin-inline"><span class="muted">Compare</span><input type="date" data-fin-cmp value="${b.compare}"></label>
      <span id="fin-balanced" class="fin-marks"></span><span class="spacer"></span><button type="button" class="ghost" data-fin-export>Export</button>${finAddMenuHtml()}</div>`
    + finSplit(`<div class="fin-scroll" id="fin-bs">${skelRows(8, {dot: false})}</div>`, '', 'Selected line');
  const body = $('#fin-body');
  body.querySelector('[data-fin-asof]').onchange = ev => { b.asOf = ev.target.value || finToday(); void finBsLoad(); };
  body.querySelector('[data-fin-cmp]').onchange = ev => { b.compare = ev.target.value; void finBsLoad(); };
  body.onclick = ev => {
    if (finAddClick(ev)) return;
    if (ev.target.closest('[data-fin-export]')) { finCsv(finTableRows($('#fin-bs table')), `balance-sheet-${b.asOf}.csv`); return; }
    const tr = ev.target.closest('tr[data-fin-line]');
    if (tr) { b.sel = tr.dataset.finLine; finBsDraw(); finBsRail(); }
  };
  void finBsLoad();
}
async function finBsLoad() {
  const state = FIN.bs, b = FIN_BS, load = ++state.load;
  const [cur, prev] = await Promise.allSettled([get(`/v2/finance/balance-sheet?as_of=${b.asOf}${finAs()}`, {fresh: true}),
    b.compare ? get(`/v2/finance/balance-sheet?as_of=${b.compare}${finAs()}`, {fresh: true}) : Promise.resolve(null)]);
  if (FIN?.bs !== state || state.load !== load || !$('#fin-bs')) return;
  if (cur.status === 'rejected') { $('#fin-bs').innerHTML = finFail(cur.reason); finRail(''); return; }
  state.data = cur.value; state.prev = prev.status === 'fulfilled' ? prev.value : null;
  finBsDraw(); finBsRail();
}
// A line's key: its account, or its code for the route's own rows (net income to date).
const finBsKey = r => r.account_id || 'code:' + r.code;
// A section's lines in chart order, each with its depth and its balance with the lines under it (of those shown).
function finBsTree(rows) {
  const ids = new Set(rows.map(r => r.account_id).filter(Boolean)), kids = new Map();
  for (const r of rows) if (r.parent_id && ids.has(r.parent_id)) (kids.get(r.parent_id) || kids.set(r.parent_id, []).get(r.parent_id)).push(r);
  const byCode = (a, b) => String(a.code).localeCompare(String(b.code));
  const out = [];
  const walk = (r, depth) => {
    const at = out.length; out.push({...r, depth, sum: r.balance || 0, kids: 0});
    for (const k of (kids.get(r.account_id) || []).sort(byCode)) { const before = out.length; walk(k, depth + 1); out[at].sum += out[before].sum; out[at].kids++; }
  };
  for (const r of rows.filter(r => !r.parent_id || !ids.has(r.parent_id)).sort(byCode)) walk(r, 0);
  return out;
}
function finBsDraw() {
  const state = FIN.bs, box = $('#fin-bs'); if (!box || !state.data) return;
  const d = state.data, prev = state.prev, b = FIN_BS, totals = d.totals || {};
  const balanced = !d.scoped && totals.asset != null && totals.asset === (totals.liability || 0) + (totals.equity || 0);
  $('#fin-balanced').innerHTML = balanced ? '<span class="pill ok">Balanced</span>' : '';
  if (d.snapshot) { const m = document.createElement('span'); finMarksDraw(m, d); $('#fin-balanced').append(...m.childNodes); }
  const imp = (d.imported || []).map(m => `${m.month} · ${m.basis === 'cash' ? 'Cash' : 'Accrual'}`).join(', ');
  const prevOf = (key, sec) => { if (!prev) return null; const t = finBsTree(prev[sec] || []).find(r => finBsKey(r) === key); return t ? t.sum : null; };
  const source = s => `<span class="fin-src">${esc([finProvider(s.provider), s.name].filter(Boolean).join(' · '))}${s.last4 ? ' ••' + esc(s.last4) : ''}</span>`;
  const cols = prev ? 5 : 3;
  const out = [];
  for (const [sec, label, key] of FIN_BS_SECTIONS) {
    const tree = finBsTree(d[sec] || []);
    out.push(`<tr class="fin-sec"><td>${label}</td><td colspan="${cols - 1}"></td></tr>`);
    for (const r of tree) {
      const k = finBsKey(r), was = prevOf(k, sec), ch = was == null ? null : finDiff(r.sum - was);
      const from = [...(r.sources || []).map(source), ...finBsFrom(r).map(finBsSrc)].join('') || '<span class="muted">—</span>';
      out.push(`<tr class="fin-ln${r.kids ? ' fin-parent' : ''}${b.sel === k ? ' fin-on' : ''}" style="--d:${r.depth}" data-fin-line="${esc(k)}" tabindex="0"><td>${esc(r.name)}</td><td class="fin-n">${finNum(r.sum)}</td>${prev
        ? `<td class="fin-n">${finNum(was)}</td><td class="fin-n ${ch?.cls || ''}">${ch?.text || ''}</td>` : ''}<td><div class="fin-from">${from}</div></td></tr>`);
    }
    const t = totals[key], tp = prev?.totals?.[key], ch = prev && t != null && tp != null ? finDiff(t - tp) : null;
    out.push(`<tr class="fin-subt"><td>Total ${label.toLowerCase()}</td><td class="fin-n">${finNum(t)}</td>${prev ? `<td class="fin-n">${finNum(tp)}</td><td class="fin-n ${ch?.cls || ''}">${ch?.text || ''}</td>` : ''}<td></td></tr>`);
  }
  if (totals.liability != null || totals.equity != null) {
    const le = (totals.liability || 0) + (totals.equity || 0), lp = prev ? (prev.totals?.liability || 0) + (prev.totals?.equity || 0) : null;
    out.push(`<tr class="fin-tot"><td>Liabilities + equity</td><td class="fin-n">${finNum(le)}</td>${prev ? `<td class="fin-n">${finNum(lp)}</td><td class="fin-n ${finDiff(le - lp).cls}">${finDiff(le - lp).text}</td>` : ''}<td></td></tr>`);
  }
  box.innerHTML = `<table class="fin-bs"><thead><tr><th></th><th class="fin-n">${esc(finDayLabel(d.as_of || b.asOf))}${imp ? `<span class="fin-tag" title="${esc(imp)}">Imported</span>` : ''}</th>${prev
    ? `<th class="fin-n">${esc(finDayLabel(prev.as_of || b.compare))}</th><th class="fin-n">Change</th>` : ''}<th>From</th></tr></thead><tbody>${out.join('')}</tbody></table>`;
  box.querySelector('tbody').onkeydown = ev => { const tr = ev.target.closest('tr[data-fin-line]'); if (tr && ev.key === 'Enter') tr.click(); };
}
function finBsRail() {
  const state = FIN.bs, b = FIN_BS; if (!state?.data) return;
  let row = null, label = '';
  for (const [sec, name] of FIN_BS_SECTIONS) { const r = finBsTree(state.data[sec] || []).find(x => finBsKey(x) === b.sel); if (r) { row = r; label = name; break; } }
  const tail = finBsTail();
  if (!row) { finRail(tail); return; }
  const srcs = row.sources || [];
  finRail(`<div class="muted fin-sup">${esc(label)}</div><h2>${esc(row.name)}</h2>
    ${finKv('Balance', finUsd(row.sum))}${row.kids ? finKv('This line', finUsd(row.balance)) : ''}
    ${srcs.length ? `<h3 class="fin-h">Accounts</h3>${srcs.map(s => finKv(`${s.name || s.provider}${s.last4 ? ' ••' + s.last4 : ''}`,
      `${finUsd(s.provider_balance)}${s.balance_as_of ? ` <span class="muted">${esc(finDayLabel(s.balance_as_of))}</span>` : ''}`)).join('')}` : ''}
    ${finBsFrom(row).length ? `<h3 class="fin-h">From</h3><div class="fin-from">${finBsFrom(row).map(finBsSrc).join('')}</div>` : ''}${tail}`);
}
// What a line's entries come from (backend/finance/core/ledger.py SOURCES; pnl: net income, from the P&L). A line fed by a
// money account shows that account, not its bank or card rows again.
const FIN_ENTRY_SRC = {manual: 'Journal', import: 'Import', bank: 'Bank', card: 'Cards', bill: 'Bills', invoice: 'Invoices',
  payment: 'Payments', usage: 'Gateway', sync: 'Books sync', pnl: 'P&L'};
const finBsFrom = r => (r.entry_sources || []).filter(s => !((r.sources || []).length && (s === 'bank' || s === 'card')));
const finBsSrc = s => s === 'pnl' ? `<a class="fin-src" href="${finHref('/pnl')}">P&amp;L</a>` : `<span class="fin-src">${esc(FIN_ENTRY_SRC[s] || s)}</span>`;
const finProvider = p => ({mercury: 'Mercury', stripe: 'Stripe', quickbooks: 'QuickBooks', xero: 'Xero'}[p] || p || '');
// Under any line: payments still on their way out of each money account, and how far the books are closed.
function finBsTail() {
  const d = FIN.bs.data, seen = new Set(), due = [];
  for (const [sec] of FIN_BS_SECTIONS) for (const r of d[sec] || []) for (const s of r.sources || []) {
    if (seen.has(s.money_account_id) || !s.due_out?.count) continue;
    seen.add(s.money_account_id); due.push(s);
  }
  // '' when there is neither, so the empty rail hides (.fin-rail:empty).
  return (due.length ? `<div class="fin-rail-sec"><h3 class="fin-h">Due out</h3>${due.map(s => finKv(`${s.name || s.provider || ''}${s.last4 ? ' ••' + s.last4 : ''} · ${s.due_out.count}`, finUsd(s.due_out.amount))).join('')}</div>` : '')
    + (d.closed_through ? `<div class="fin-rail-sec"><h3 class="fin-h">Close</h3>${finKv('Closed through', esc(finDayLabel(d.closed_through)))}</div>` : '');
}

// ----------------------------------------------------------------- cards
const FIN_CARD_RANK = {active: 0, frozen: 1, cancelled: 2};
const FIN_CARD_STATUS = {active: ['ok', 'Active'], frozen: ['waiting', 'Frozen'], cancelled: ['idle', 'Cancelled']};
function finCardsPage() {
  const caps = finCaps();
  FIN.cards = {rows: [], next: null, sel: '', load: 0};
  $('#fin-body').innerHTML = `<div class="fin-bar"><span class="spacer"></span>${caps.may('finance.cards') ? '<button type="button" class="primary" data-fin-card-add>+ Card</button>' : ''}</div>`
    + finSplit(`<div class="fin-scroll" id="fin-cards">${skelRows(5, {dot: false})}</div>`, '', 'Selected card');
  $('#fin-body').onclick = ev => {
    if (ev.target.closest('[data-fin-card-add]')) { finCardAdd(); return; }
    if (ev.target.closest('[data-fin-more]')) { void finCardsLoad(true); return; }
    const tr = ev.target.closest('tr[data-fin-card]');
    if (tr) { FIN.cards.sel = tr.dataset.finCard; finCardsDraw(); finCardRail(); }
  };
  void finCardsLoad(false);
}
async function finCardsLoad(more) {
  const state = FIN.cards, load = ++state.load;
  const qs = more && state.next ? `?cursor=${encodeURIComponent(state.next)}` : '';
  let d;
  try { d = await get('/v2/finance/cards' + qs + finAs(qs ? '&' : '?'), {fresh: true}); }
  catch (e) { if (FIN?.cards === state && $('#fin-cards')) $('#fin-cards').innerHTML = finFail(e); return; }
  if (FIN?.cards !== state || state.load !== load || !$('#fin-cards')) return;
  // Live cards first, then frozen, then cancelled; by name within each.
  state.rows = (more ? [...state.rows, ...(d.cards || [])] : d.cards || [])
    .sort((x, y) => (FIN_CARD_RANK[x.status] ?? 3) - (FIN_CARD_RANK[y.status] ?? 3) || finCardName(x).localeCompare(finCardName(y)));
  state.next = d.next || null;
  if (!state.rows.some(c => c.id === state.sel)) state.sel = state.rows[0]?.id || '';
  finCardsDraw(); finCardRail();
}
const finCardName = c => `${c.nickname || 'Card'}${c.last4 ? ' · ' + c.last4 : ''}`;
// A frozen card says who froze it: its budget ran out (it unfreezes itself when there is room again) or a person did.
const finCardPill = c => {
  const [cls, label] = FIN_CARD_STATUS[c.status] || ['idle', c.status];
  return [cls, c.status === 'frozen' && c.frozen_by ? `${label} · ${c.frozen_by === 'budget' ? 'Budget' : 'Person'}` : label];
};
// Where a person sees their own card's number: the provider's app (spec §8.3). Tico never reads, keeps or logs one.
const FIN_CARD_APP = {mercury: 'https://app.mercury.com/cards'};
function finCardsDraw() {
  const state = FIN.cards, box = $('#fin-cards'); if (!box) return;
  if (!state.rows.length) { box.innerHTML = finNote('No cards yet.'); return; }
  box.innerHTML = `<table class="fin-t fin-card-t"><thead><tr><th>Card</th><th>Holder</th><th>Budget</th><th class="fin-n">Limit</th><th class="fin-n">Spent</th><th>Status</th></tr></thead><tbody>${state.rows.map(c => {
    const [cls, label] = finCardPill(c);
    return `<tr data-fin-card="${esc(c.id)}"${state.sel === c.id ? ' class="fin-on"' : ''}><td><button type="button" class="fin-link">${esc(finCardName(c))}</button></td><td><span class="fin-who">${finAvatar(c.holder, 18)}${esc(finWho(c.holder))}</span></td>
      <td>${c.budget ? esc(c.budget.name) : '<span class="muted">—</span>'}</td><td class="fin-n">${c.limit == null ? '<span class="muted">—</span>' : finNum(c.limit)}</td><td class="fin-n">${finNum(c.spent)}</td>
      <td><span class="pill ${cls}">${esc(label)}</span>${c.sync_status && c.sync_status !== 'ok' ? ` <span class="muted fin-small">${esc(c.sync_status)}</span>` : ''}</td></tr>`;
  }).join('')}</tbody></table>${state.next ? '<button type="button" class="ghost fin-more-btn" data-fin-more>More</button>' : ''}`;
}
function finCardRail() {
  const state = FIN.cards, c = state.rows.find(x => x.id === state.sel);
  if (!c) { finRail(''); return; }
  const may = finCaps().may('finance.cards') && c.status !== 'cancelled';
  const [cls, label] = finCardPill(c);
  // A budget's freeze lifts when the budget has room again; only a person's freeze is undone by hand.
  const toggle = c.status === 'frozen' ? (c.frozen_by === 'budget' ? '' : 'Unfreeze') : 'Freeze';
  const app = c.holder === finMe() && c.status !== 'cancelled' && FIN_CARD_APP[c.provider || 'mercury'];
  const saved = c.saved_on || [], bot = String(c.holder || '').startsWith('bot:');
  finRail(`<div class="fin-rail-title"><h2>${esc(finCardName(c))}</h2><span class="pill ${cls}">${esc(label)}</span></div>
    ${finKv('Holder', esc(finWho(c.holder)))}${finKv('Budget', c.budget ? esc(c.budget.name) : '—')}
    ${finKv('Limit', c.limit == null ? '—' : `${finUsd(c.limit)}${c.limit_interval ? ` <span class="muted">/ ${esc(c.limit_interval.replace(/ly$/, '').replace('dai', 'day'))}</span>` : ''}`)}
    ${finKv('Spent', finUsd(c.spent))}${c.expires_at ? finKv('Expires', esc(finDayLabel(c.expires_at))) : ''}
    ${app ? finKv('Number', `<a href="${esc(app)}" target="_blank" rel="noopener noreferrer">Show in ${esc(c.provider === 'mercury' || !c.provider ? 'Mercury' : c.provider)}</a>`) : ''}
    ${bot && (saved.length || may) ? `<h3 class="fin-h">Saved on</h3><div class="fin-saved" data-fin-saved>${saved.map(v => `<span class="fin-chip" title="${esc(v.saved_by ? finWho(v.saved_by) : '')}">${esc(v.label || v.vendor)}${
      may ? `<button type="button" data-fin-unsave="${esc(v.vendor)}" aria-label="Remove ${esc(v.label || v.vendor)}">✕</button>` : ''}</span>`).join('')}${
      may && saved.length < 10 ? `<span class="fin-pop-at"><button type="button" class="ghost fin-chip-add" data-fin-save aria-haspopup="dialog" aria-expanded="false" aria-controls="fin-save">+ Vendor</button>
        <form class="fin-pop fin-vpick" id="fin-save" hidden><input name="vendor" list="fin-save-list" maxlength="200" autocomplete="off" placeholder="Vendor" aria-label="Vendor"><datalist id="fin-save-list"></datalist>
        <button type="submit" class="primary">Save</button></form></span>` : ''}</div>` : ''}
    ${may ? `<form class="fin-form fin-rail-form" data-fin-limit><label>Limit<input name="amount" inputmode="decimal" autocomplete="off" value="${c.limit == null ? '' : c.limit / 100}"></label>
      <div class="fin-foot">${toggle ? `<button type="button" class="ghost" data-fin-freeze>${toggle}</button>` : ''}<button type="submit" class="primary">Save limit</button></div></form>` : ''}`);
  const rail = $('#fin-rail');
  finCardSavedWire(c, rail);
  rail.querySelector('[data-fin-freeze]')?.addEventListener('click', async ev => {
    const btn = ev.currentTarget; btn.disabled = true;
    try {
      const r = await finAct(c.status === 'frozen' ? 'card_unfreeze' : 'card_freeze', {card_id: c.id});   // a refusal's reason shows as sent
      if (r.status === 'committed') toast(c.status === 'frozen' ? 'Unfrozen' : 'Frozen');
      void finCardsLoad(false);
    } catch (e) { toast(e.message, true); btn.disabled = false; }
  });
  const form = rail.querySelector('[data-fin-limit]');
  if (form) form.onsubmit = async ev => {
    ev.preventDefault();
    const cents = finParseCents(form.elements.amount.value);
    if (cents == null) { toast('A dollar amount, like 500', true); return; }
    const btn = form.querySelector('[type=submit]'); btn.disabled = true;
    try {
      const r = await finAct('card_update_limit', {card_id: c.id, amount: cents});
      if (r.status === 'committed') toast('Saved');
      void finCardsLoad(false);
    } catch (e) { toast(e.message, true); btn.disabled = false; }
  };
}
// "Saved on": the vendor accounts a person saved this bot card on (card_save, card_unsave). The picker offers known
// vendors (GET /finance/contacts?kind=vendor) and takes a typed name too; a refusal shows the server's words.
function finCardSavedWire(c, rail) {
  rail.querySelectorAll('[data-fin-unsave]').forEach(b => b.addEventListener('click', async () => {
    b.disabled = true;
    try { const r = await finAct('card_unsave', {card_id: c.id, vendor: b.dataset.finUnsave}); if (r.status === 'committed') toast('Removed'); void finCardsLoad(false); }
    catch (e) { toast(e.message, true); b.disabled = false; }
  }));
  const btn = rail.querySelector('[data-fin-save]'), form = rail.querySelector('#fin-save');
  if (!btn || !form) return;
  const list = form.querySelector('datalist'), input = form.elements.vendor;
  let vendors = [], timer = 0;
  const fill = async q => {
    try { vendors = (await get('/v2/finance/contacts?kind=vendor&limit=20' + (q ? '&q=' + encodeURIComponent(q) : ''), {fresh: true}))?.contacts || []; } catch { vendors = []; }
    list.innerHTML = vendors.map(v => `<option value="${esc(v.name)}"></option>`).join('');
  };
  btn.onclick = () => { finPop(btn, form); if (!form.hidden) void fill(''); };
  input.oninput = () => { clearTimeout(timer); timer = setTimeout(() => void fill(input.value.trim()), 250); };
  form.onsubmit = async ev => {
    ev.preventDefault();
    const name = input.value.trim(); if (!name) return;
    const known = vendors.find(v => v.name.toLowerCase() === name.toLowerCase());
    const sub = form.querySelector('[type=submit]'); sub.disabled = true;
    try { const r = await finAct('card_save', {card_id: c.id, vendor: known ? known.id : name}); if (r.status === 'committed') toast('Saved'); void finCardsLoad(false); }
    catch (e) { toast(e.message, true); sub.disabled = false; }
  };
}
async function finCardAdd() {
  const [b, u] = await Promise.allSettled([get('/v2/finance/budgets', {fresh: true}), get('/v2/finance/mercury-users', {fresh: true})]);
  const budgets = b.status === 'fulfilled' ? b.value?.budgets || [] : [], users = u.status === 'fulfilled' ? u.value?.users || [] : [];
  const people = (FIN.acc?.teammates || []);
  finDialog('Card', `<label>Holder<select name="holder" required>${people.map(t => `<option value="${esc(t.teammate)}">${esc(finWho(t.teammate))}</option>`).join('')}</select></label>
    <label>Budget<select name="budget" required>${budgets.filter(b => b.active && !b.catch_all).map(b => `<option value="${esc(b.id)}">${esc(b.name)}</option>`).join('')}</select></label>
    <label>Limit / mo<input name="amount" inputmode="decimal" required autocomplete="off"></label>
    <label>Name<input name="nickname" maxlength="50" autocomplete="off"></label>
    <label>Mercury user<select name="user" required>${users.map(x => `<option value="${esc(x.id)}">${esc(x.name)}${x.email ? ' · ' + esc(x.email) : ''}</option>`).join('')}</select></label>`, 'Create', async form => {
    // backend/finance/cards.py card_create: a budget the card's monthly limit comes out of, and the Mercury user it is
    // issued to (Mercury issues cards to its own users).
    const cents = finParseCents(form.elements.amount.value);
    if (cents == null || cents <= 0) throw new Error('A dollar amount, like 500');
    if (!form.elements.budget.value) throw new Error('Pick a budget');
    const user = form.elements.user.value;
    if (!user) throw new Error('No Mercury users yet');
    const payload = {holder: form.elements.holder.value, budget_id: form.elements.budget.value, amount: cents, provider_user_id: user};
    if (form.elements.nickname.value.trim()) payload.nickname = form.elements.nickname.value.trim();
    const r = await finAct('card_create', payload);
    if (r.status === 'committed') toast('Card created');
    void finCardsLoad(false);
  });
}

// ----------------------------------------------------------------- transactions
const FIN_TX = {q: '', source: 'all', status: '', account: '', org: null};
const FIN_TX_STATUS = [['', 'Any status'], ['new', 'New'], ['matched', 'Matched'], ['posted', 'Posted'], ['pending', 'Pending'], ['ignored', 'Ignored']];
function finTxPage() {
  const t = FIN_TX;
  if (FIN.q.has('q') || FIN.q.has('account_id')) {
    t.q = FIN.q.get('q') || ''; t.account = FIN.q.get('account_id') || ''; t.source = 'all'; t.status = '';
  }
  // The org filter lives in the address only: a bare Transactions link drops one an earlier link set.
  const org = finFilterFrom(FIN.q); t.org = finFilterAny(org) ? org : null;
  // chart: the lines a bank row may go to (undefined until read, null while reading, false when there is no chart read);
  // cat: the selected bank row's picks (finTxCatState).
  FIN.tx = {rows: [], next: null, sel: '', load: 0, lines: [], chart: undefined, cat: null, busy: false};
  const orgChip = t.org ? `<span class="fin-chip">${esc([...finPicked(t.org).map(finPickName), ...(t.org.who !== 'both' ? [t.org.who === 'bots' ? 'Bots' : 'People'] : [])].join(', '))}<button type="button" data-fin-org-clear aria-label="Clear filter">✕</button></span>` : '';
  $('#fin-body').innerHTML = `<div class="fin-bar"><label class="fin-search"><span class="nav-icon" aria-hidden="true">search</span><span class="sr-only">Search</span><input type="search" data-fin-q value="${esc(t.q)}" placeholder="Search" aria-label="Search"></label>
      ${finSeg('source', 'Source', [['all', 'All'], ['feed', 'Bank'], ['entry', 'Entries'], ...(finCaps().balance ? [['transfer', 'Transfers']] : [])], t.source)}
      <select data-fin-status aria-label="Status">${FIN_TX_STATUS.map(([v, l]) => `<option value="${v}"${v === t.status ? ' selected' : ''}>${l}</option>`).join('')}</select>
      <select data-fin-account aria-label="Line"><option value="">All lines</option>${t.account ? `<option value="${esc(t.account)}" selected>${esc(t.account)}</option>` : ''}</select>${orgChip}
      <span class="spacer"></span>${finCaps().may('finance.pay') ? '<button type="button" class="primary" data-fin-xfer-add>+ Transfer</button>' : ''}</div>`
    + finSplit(`<div class="fin-scroll" id="fin-tx">${skelRows(8, {dot: false})}</div>`, '', 'Selected transaction');
  const body = $('#fin-body');
  let timer = 0;
  body.querySelector('[data-fin-q]').oninput = ev => { clearTimeout(timer); timer = setTimeout(() => { t.q = ev.target.value.trim(); void finTxLoad(false); }, 300); };
  body.querySelector('[data-fin-status]').onchange = ev => { t.status = ev.target.value; void finTxLoad(false); };
  body.querySelector('[data-fin-account]').onchange = ev => { t.account = ev.target.value; void finTxLoad(false); };
  body.onclick = ev => {
    const seg = ev.target.closest('[data-fin-seg] button');
    if (seg) { if (seg.dataset.v !== t.source) { t.source = seg.dataset.v; finSegSet(seg); void finTxLoad(false); } return; }
    if (ev.target.closest('[data-fin-xfer-add]')) { void finXferAdd(); return; }
    if (ev.target.closest('[data-fin-more]')) { void finTxLoad(true); return; }
    const clear = ev.target.closest('[data-fin-org-clear]');
    if (clear) {
      t.org = null; clear.closest('.fin-chip').remove();
      for (const k of ['teams', 'teammates', 'who', 'unattributed']) FIN.q.delete(k);
      const rest = FIN.q.toString();
      history.replaceState(history.state, '', `${FIN_ROUTE}/transactions${rest ? '?' + rest : ''}`);
      S.route = LAST_ROUTE = location.hash;
      void finTxLoad(false); return;
    }
    if (ev.target.closest('select[data-fin-tx-line]')) return;
    const tr = ev.target.closest('tr[data-fin-tx]');
    if (tr) { FIN.tx.sel = tr.dataset.finTx; finTxDraw(); finTxRail(); }
  };
  body.addEventListener('change', ev => { const sel = ev.target.closest('select[data-fin-tx-line]'); if (sel) void finTxLineSave(sel); });
  void finTxLines();
  if (finCaps().may('finance.book')) void finTxChart();
  void finTxLoad(false);
}
// The lines to filter by: the P&L's and balance sheet's lines the viewer sees (from the last P&L read, else one now).
async function finTxLines() {
  const state = FIN.tx;
  let rows = FIN_PNL.last?.rows;
  if (!rows) {
    try {
      const caps = finCaps(), [a, z] = finPnlRange('month');
      rows = (await get(`/v2/finance/pnl?start=${a}&end=${z}&period=month&basis=cash&view=${caps.company ? 'company' : 'expenses'}${finAs()}`, {fresh: true}))?.rows || [];
    } catch { rows = []; }
  }
  if (FIN?.tx !== state) return;
  state.lines = rows.filter(r => !r.private).sort((a, b) => String(a.code).localeCompare(String(b.code)));
  const sel = $('#fin-body [data-fin-account]'); if (!sel) return;
  const t = FIN_TX, known = state.lines.some(r => r.account_id === t.account);
  sel.innerHTML = `<option value="">All lines</option>${t.account && !known ? `<option value="${esc(t.account)}" selected>${esc(t.account)}</option>` : ''}${state.lines.map(r =>
    `<option value="${esc(r.account_id)}"${r.account_id === t.account ? ' selected' : ''}>${esc(r.name)}</option>`).join('')}`;
}
async function finTxLoad(more) {
  if (FIN_TX.source === 'transfer' && !finCaps().balance) FIN_TX.source = 'all';
  // Transfers are their own list: the bank filters don't apply to them.
  $('#fin-body').querySelectorAll('[data-fin-status], [data-fin-account]').forEach(el => { el.hidden = FIN_TX.source === 'transfer'; });
  if (FIN_TX.source === 'transfer') return finXferLoad(more);
  const state = FIN.tx, t = FIN_TX, load = ++state.load;
  const qs = new URLSearchParams({source: t.source, limit: '50'});
  if (t.q) qs.set('q', t.q.slice(0, 100));
  if (t.status) qs.set('status', t.status);
  if (t.account) qs.set('account_id', t.account);
  for (const [k, v] of finFilterQs(t.org)) qs.append(k, v);
  if (more && state.next) qs.set('cursor', state.next);
  let d;
  try { d = await get('/v2/finance/transactions?' + qs + finAs(), {fresh: true}); }
  catch (e) { if (FIN?.tx === state && state.load === load && $('#fin-tx')) $('#fin-tx').innerHTML = finFail(e); return; }
  if (FIN?.tx !== state || state.load !== load || !$('#fin-tx')) return;
  state.rows = more ? [...state.rows, ...(d.rows || [])] : d.rows || [];
  state.next = d.next || null;
  if (!state.rows.some(r => finTxKey(r) === state.sel)) state.sel = state.rows[0] ? finTxKey(state.rows[0]) : '';
  finTxDraw(); finTxRail();
}
const finTxKey = r => `${r.kind}:${r.id}`;
// A row's teammate and vendor: an entry's own tags, a feed row's first line's.
// The vendor shown is its name (a bill's or invoice's vendor key is the contact's id).
const finTxTags = r => { const l = r.kind === 'entry' ? r : r.lines?.[0]; return {tags: (r.kind === 'entry' ? r.tags : l?.tags) || {}, vendor: l ? l.vendor_name ?? l.vendor : ''}; };
function finTxDraw() {
  if (FIN_TX.source === 'transfer') { finXferDraw(); return; }
  const state = FIN.tx, box = $('#fin-tx'); if (!box) return;
  if (!state.rows.length) { box.innerHTML = finNote('No transactions.'); return; }
  box.innerHTML = `<table class="fin-t fin-tx-t"><thead><tr><th>Date</th><th>Description</th><th>Account</th><th>Line</th><th class="fin-n">Amount</th><th>Teammate</th><th>Vendor</th><th>Status</th></tr></thead><tbody>${state.rows.map(r => {
    const {tags, vendor} = finTxTags(r), key = finTxKey(r);
    const account = r.kind === 'feed' ? `${r.money_account?.name || ''}${r.money_account?.last4 ? ' ••' + r.money_account.last4 : ''}` : '';
    const status = r.kind === 'feed' ? (r.pending ? 'pending' : r.status) : r.reversed ? 'reversed' : 'posted';
    return `<tr data-fin-tx="${esc(key)}"${state.sel === key ? ' class="fin-on"' : ''}><td class="fin-nowrap">${esc(finDayLabel(r.date))}</td>
      <td><button type="button" class="fin-link">${esc(r.kind === 'feed' ? r.counterparty || r.description || '' : r.description || r.memo || '')}</button></td><td class="fin-cut">${account ? esc(account) : '<span class="muted">—</span>'}</td><td>${finTxLineCell(r)}</td>
      <td class="fin-n">${finCents(r.amount)}</td><td>${tags.teammate ? esc(finWho(tags.teammate)) : '<span class="muted">—</span>'}</td><td class="fin-cut">${vendor ? esc(vendor) : '<span class="muted">—</span>'}</td>
      <td><span class="pill ${{posted: 'ok', matched: 'ok', pending: 'waiting', new: 'waiting', ignored: 'idle', reversed: 'idle'}[status] || 'idle'}">${esc(status)}</span></td></tr>`;
  }).join('')}</tbody></table>${state.next ? '<button type="button" class="ghost fin-more-btn" data-fin-more>More</button>' : ''}`;
}
// A row's line: a picker for a bank or card row its viewer may categorize (one line, or several on the same line), else
// the line's name, or Split when its lines go to several.
function finTxLineCell(r) {
  const lines = r.kind === 'feed' ? r.lines || [] : [], chart = FIN.tx.chart;
  const ids = [...new Set(lines.map(l => l.account_id || ''))];
  if (r.kind !== 'feed') return r.account ? esc(r.account) : '<span class="muted">—</span>';
  if (ids.length > 1) return 'Split';
  const cur = ids[0] || '', name = lines[0]?.account || '';
  // A row split across tags (even on one line) is categorized in the panel, so each part keeps its own tags.
  if (lines.length > 1 || !finTxCatOn(r) || !Array.isArray(chart)) return name ? esc(name) : '<span class="muted">—</span>';
  const opt = (v, l) => `<option value="${esc(v)}"${v === cur ? ' selected' : ''}>${esc(l)}</option>`;
  return `<select class="fin-cell-sel" data-fin-tx-line="${esc(finTxKey(r))}" aria-label="Line"${FIN.tx.busy ? ' disabled' : ''}>${cur ? '' : opt('', '—')}${cur && !chart.some(a => a.id === cur) ? opt(cur, name || cur) : ''}${chart.map(a => opt(a.id, a.name)).join('')}</select>`;
}
// A line picked in the row: the row moves to it, keeping its tags; a line the viewer may only post to with a teammate
// or team tag gets theirs (the chart lists the viewer first) when the row has neither. When the rules ask for approval,
// the rail's form shows the ask.
async function finTxLineSave(sel) {
  const state = FIN.tx, key = sel.dataset.finTxLine, r = state.rows.find(x => finTxKey(x) === key);
  if (!r || !sel.value || state.busy) { finTxDraw(); return; }
  const line = (state.chart || []).find(a => a.id === sel.value), lines = r.lines || [];
  const tags = lines.length ? {...(lines[0].tags || {})} : {};
  delete tags.budget;   // set by the new line's budgets
  if (line?.company_wide === false && !tags.teammate && !tags.team) {
    const first = finTxForOpts(line)[0];
    if (!first) { toast('Not a line you can post to', true); finTxDraw(); return; }
    const [k, v] = first[0].split(/:(.*)/); tags[k] = v;
  }
  const payload = {feed_id: r.id, account_id: sel.value, tags};
  state.busy = true; sel.disabled = true;
  try {
    const res = await finDecide('categorize', payload);
    state.busy = false;
    if (FIN?.tx !== state) return;
    if (res.ask != null) {
      state.sel = key; state.cat = null;
      const c = finTxCatState(r); c.now.account = sel.value; c.ask = {who: res.ask, p: payload, sig: res.sig};
      finTxDraw(); finTxRail(); return;
    }
    if (res.status === 'committed') toast('Saved');
    if (state.cat?.key === key) state.cat = null;
    void finTxLoad(false);
  } catch (e) { state.busy = false; toast(e.message, true); if (FIN?.tx === state) finTxDraw(); }
}
function finTxRail() {
  if (FIN_TX.source === 'transfer') { finXferRail(); return; }
  const state = FIN.tx, r = state.rows.find(x => finTxKey(x) === state.sel);
  if (!r) { finRail(''); return; }
  const lines = r.kind === 'feed' ? (r.lines || []) : r.entry_id && r.id ? [{line_id: r.id, account: r.account, amount: r.amount, vendor: r.vendor, tags: r.tags || {}}] : [];
  const may = finCaps().may('finance.book') && lines.length > 0;
  const cat = finTxCatOn(r);
  const acc = FIN.acc || {};
  // What each field shows: the lines' common value, or Mixed when a split transaction's lines differ.
  const FIN_MIXED = '*';
  const shown = Object.fromEntries(['teammate', 'team', 'project'].map(k => {
    const vals = new Set(lines.map(l => l.tags?.[k] || ''));
    return [k, vals.size > 1 ? FIN_MIXED : [...vals][0] || ''];
  }));
  const mixed = k => shown[k] === FIN_MIXED ? `<option value="${FIN_MIXED}" selected>Mixed</option>` : '';
  const title = r.kind === 'feed' ? r.counterparty || r.description || '' : r.description || r.memo || '';
  const opt = (v, l, cur) => `<option value="${esc(v)}"${v === (cur || '') ? ' selected' : ''}>${esc(l)}</option>`;
  finRail(`<div class="fin-rail-title"><h2>${esc(title)}</h2><span class="fin-big">${finUsd(r.amount)}</span></div>
    ${finKv('Date', esc(finDayLabel(r.date)))}
    ${r.kind === 'feed' ? finKv('Account', esc(`${r.money_account?.name || ''}${r.money_account?.last4 ? ' ••' + r.money_account.last4 : ''}`)) + (r.description && r.description !== title ? finKv('Description', esc(r.description)) : '') : finKv('Source', esc(r.source || ''))}
    ${r.memo ? finKv('Memo', esc(r.memo)) : ''}
    ${lines.length && !(cat && lines.length === 1) ? `<h3 class="fin-h">Lines</h3>${lines.map(l => finKv(l.account || '', finCents(l.amount))).join('')}` : ''}
    <div id="fin-tx-match"></div>
    ${cat ? '<form class="fin-form fin-rail-form fin-cat" data-fin-cat novalidate></form>' : may ? `<form class="fin-form fin-rail-form" data-fin-tag><label>Teammate<select name="teammate">${mixed('teammate')}${opt('', '—', shown.teammate)}${(acc.teammates || []).map(t => opt(t.teammate, finWho(t.teammate), shown.teammate)).join('')}</select></label>
      <label>Team<select name="team">${mixed('team')}${opt('', '—', shown.team)}${(acc.teams || []).map(t => opt(t.id, t.name, shown.team)).join('')}</select></label>
      <label>Project<input name="project" maxlength="80" autocomplete="off" value="${shown.project === FIN_MIXED ? '' : esc(shown.project)}"${shown.project === FIN_MIXED ? ' placeholder="Mixed"' : ''}></label>
      <div class="fin-foot"><button type="submit" class="primary"${state.busy ? ' disabled' : ''}>Save</button></div></form>` : ''}`);
  if (cat) finTxCatDraw(r);
  void finTxExpected(r);
  const form = $('#fin-rail [data-fin-tag]');
  form?.elements.project.addEventListener('input', ev => { ev.target.dataset.finTyped = '1'; });
  if (form) form.onsubmit = async ev => {
    ev.preventDefault();
    // Only the fields the person changed are set (or cleared) on each line; every other tag a line has stays.
    // On a mixed field only Mixed (a Project not typed in) means unchanged: — or an emptied Project clears every line.
    const val = k => k === 'project' && shown.project === FIN_MIXED && !form.elements.project.dataset.finTyped ? FIN_MIXED : form.elements[k].value.trim();
    const changed = ['teammate', 'team', 'project'].map(k => [k, val(k)])
      .filter(([k, v]) => v !== FIN_MIXED && (shown[k] === FIN_MIXED || v !== shown[k]));
    if (!changed.length) return;
    const pick = l => {
      const next = {...(l.tags || {})};
      for (const [k, v] of changed) { if (v) next[k] = v; else delete next[k]; }
      return next;
    };
    if (state.busy) return;   // one categorize or match at a time on the tab (finTxCatDraw)
    const btn = form.querySelector('[type=submit]'); btn.disabled = true; state.busy = true;
    try {
      const res = await finAct('categorize', {lines: lines.map(l => ({line_id: l.line_id, tags: pick(l)})), mode: 'replace'});
      if (res.status === 'committed') toast('Saved');
      state.busy = false;
      void finTxLoad(false);
    } catch (e) { toast(e.message, true); btn.disabled = false; }
    finally { state.busy = false; }
  };
}
// A new bank row whose automatic match to payments marked paid was unclear: the waiting payments it may be the bank
// side of (GET /finance/feed/{id}/expected), checked off to one contact and the row's exact amount, then feed_match.
// Only for those who may match (finance.ap or finance.ar); nothing shows when there are none.
async function finTxExpected(r) {
  const caps = finCaps(), state = FIN.tx, key = finTxKey(r);
  if (r.kind !== 'feed' || r.pending || r.status !== 'new' || !(caps.may('finance.ap') || caps.may('finance.ar'))) return;
  let d;
  try { d = await get(`/v2/finance/feed/${encodeURIComponent(r.id)}/expected${finAs('?')}`, {fresh: true}); } catch { return; }
  const box = $('#fin-tx-match');
  if (FIN?.tx !== state || state.sel !== key || !box || !d?.expected?.length) return;
  const rows = d.expected;
  box.innerHTML = `<h3 class="fin-h">Match</h3>${rows.map(x => `<label class="fin-match"><input type="checkbox" value="${esc(x.settlement_id)}">
      <span>${esc(x.contact_name || '—')}</span><span class="muted">${esc(x.number || '')}</span><span class="fin-n">${finUsd(x.amount)}</span><span class="muted">${esc(finDayLabel(x.marked_on))}</span></label>`).join('')}
    <div class="fin-acts" data-fin-match-acts><button type="button" class="primary" data-fin-match disabled>Match</button></div>`;
  const picked = () => rows.filter(x => box.querySelector(`input[value="${CSS.escape(x.settlement_id)}"]`)?.checked);
  const sync = () => {
    const p = picked(), btn = box.querySelector('[data-fin-match]');
    if (btn) btn.disabled = !p.length || p.reduce((t, x) => t + x.amount, 0) !== d.amount || new Set(p.map(x => x.contact_id)).size !== 1;
  };
  box.addEventListener('change', sync);
  box.onclick = async ev => {
    const btn = ev.target.closest('[data-fin-match], [data-fin-ask-yes], [data-fin-ask-no]');
    if (!btn) return;
    const acts = box.querySelector('[data-fin-match-acts]'), payload = {feed_id: r.id, settlement_ids: picked().map(x => x.settlement_id)};
    if (btn.matches('[data-fin-ask-no]')) { acts.innerHTML = '<button type="button" class="primary" data-fin-match>Match</button>'; sync(); return; }
    if (state.busy) return;   // one categorize or match at a time on the tab
    btn.disabled = true; state.busy = true;
    try {
      const res = await finDecide('feed_match', payload, btn.matches('[data-fin-ask-yes]') ? box.dataset.sig || '' : '');
      if (res.ask != null) {
        box.dataset.sig = res.sig;
        acts.innerHTML = `<span class="fin-ask">${esc(finAskText(res.ask))}</span><button type="button" class="ghost" data-fin-ask-no>Cancel</button><button type="button" class="primary" data-fin-ask-yes>Send</button>`;
        return;
      }
      if (res.status === 'committed') toast('Matched');
      state.busy = false;
      void finTxLoad(false);
    } catch (e) { toast(e.message, true); btn.disabled = false; }
    finally {
      state.busy = false;
      // Categorize, edited while this ran, drew its Save off (finTxCatDraw): draw it again with Save back on.
      if (FIN?.tx === state) finTxCatRedraw();
    }
  };
}

// ----------------------------------------------------------------- a bank row's line
// Those who keep the books set a bank or card row's line and tags in one form; a row still pending at the bank, or
// cleared by a done reconciliation, keeps its line (banking.py categorize_feed). Owners always may (finCaps().may).
// Splits come from posting rules, not from here.
const finTxCatOn = r => r.kind === 'feed' && !r.pending && !r.reconciled && !finTxTransfer(r) && !!finCaps()?.may('finance.book');
// One side of a transfer between our own accounts (backend/finance/core/feeds.py TRANSFER_KINDS), which categorize
// refuses: a transfer-kind row posted with no P&L line, or one whose entry another row on the page shares.
const finTxTransfer = r => !!r.entry_id && ((['internalTransfer', 'treasuryTransfer'].includes(r.provider_kind) && !(r.lines || []).length)
  || (FIN?.tx?.rows || []).some(x => x !== r && x.kind === 'feed' && x.entry_id === r.entry_id));
const FIN_TX_MIXED = '*';   // a field the row's lines differ on

// The lines the viewer may post to (GET /finance/chart), each with whether it takes amounts from them with no team or
// teammate tag and, when it does not, whom they may put it on. With no answer (still loading, or the read failed)
// the form stays hidden.
async function finTxChart() {
  const state = FIN.tx;
  if (state.chart !== undefined) return;
  state.chart = null;
  let rows = null;
  try { rows = (await get('/v2/finance/chart?for=journal' + finAs(), {fresh: true}))?.accounts; } catch { rows = null; }
  if (FIN?.tx !== state) return;
  state.chart = Array.isArray(rows) ? rows : false;
  if (state.rows.length && FIN_TX.source !== 'transfer') finTxDraw();
  finTxCatRedraw();
}
function finTxCatRedraw() {
  const r = FIN?.tx?.rows.find(x => finTxKey(x) === FIN.tx.sel);
  if (r && finTxCatOn(r)) finTxCatDraw(r);
}
// Whom an amount on a line that is not company-wide for the viewer may be for: the chart's list (the viewer first,
// then teams, then teammates; the rule a write checks). [value, label].
const finTxForOpts = line => (line?.attributable || [])
  .map(t => t.team ? ['team:' + t.team, finTeamName(t.team)] : ['teammate:' + t.teammate, finWho(t.teammate)]);
// The selected row's picks, kept while the rail redraws: what its lines have now (`was`) and the form's values.
function finTxCatState(r) {
  const state = FIN.tx, key = finTxKey(r);
  if (state.cat?.key === key) return state.cat;
  const lines = r.lines || [];
  const common = pick => { const v = new Set(lines.map(pick)); return v.size > 1 ? FIN_TX_MIXED : [...v][0] || ''; };
  const was = {account: common(l => l.account_id || ''), teammate: common(l => l.tags?.teammate || ''),
    team: common(l => l.tags?.team || ''), project: common(l => l.tags?.project || '')};
  return state.cat = {key, was, now: {...was, for: ''}, typed: false, ask: null};
}
// One form, one Save, one categorize. A new row: its line, and For when that line is not company-wide for the viewer.
// A posted row: its line, teammate, team and project. A new line reposts the row ({feed_id, account_id, tags});
// the same line only re-tags its lines ({lines, mode: replace}), so a rule's split stays as it is.
function finTxCatDraw(r) {
  const form = $('#fin-rail [data-fin-cat]'); if (!form) return;
  void finTxChart();
  const state = FIN.tx;
  form.hidden = !Array.isArray(state.chart);
  if (form.hidden) { form.innerHTML = ''; return; }
  const c = finTxCatState(r), chart = state.chart, lines = r.lines || [], posted = lines.length > 0;
  const acc = FIN.acc || {}, M = FIN_TX_MIXED, now = c.now, was = c.was;
  const lineOf = id => chart.find(a => a.id === id);
  const scoped = () => lineOf(now.account)?.company_wide === false;
  const opt = (v, l, cur) => `<option value="${esc(v)}"${v === cur ? ' selected' : ''}>${esc(l)}</option>`;
  const mixed = k => was[k] === M ? opt(M, 'Mixed', now[k]) : '';
  // Only the fields the person changed are set (or cleared) on each line; every other tag a line has stays. On a
  // mixed field only Mixed (a Project not typed in) means unchanged: — or an emptied Project clears every line.
  const changed = () => ['teammate', 'team', 'project'].map(k => [k, k === 'project' ? (was.project === M && !c.typed ? M : now.project.trim()) : now[k]])
    .filter(([k, v]) => v !== M && (was[k] === M || v !== was[k]));
  const pick = l => { const next = {...(l.tags || {})}; for (const [k, v] of changed()) { if (v) next[k] = v; else delete next[k]; } return next; };
  const moved = () => now.account !== was.account && now.account !== M;
  const forTag = () => { const [k, v] = now.for.split(/:(.*)/); return scoped() && now.for ? {[k]: v} : {}; };
  const moveTags = () => { const t = pick(lines[0]); delete t.budget; return t; };   // budget: set by the new line's budgets
  const ready = () => !posted ? !!now.account && (!scoped() || !!now.for)
    : moved() ? !!now.account && (!scoped() || !!(moveTags().teammate || moveTags().team)) : changed().length > 0;
  const payload = () => !posted ? {feed_id: r.id, account_id: now.account, tags: forTag()}
    : moved() ? {feed_id: r.id, account_id: now.account, tags: moveTags()}
    : {lines: lines.map(l => ({line_id: l.line_id, tags: pick(l)})), mode: 'replace'};
  const footHtml = () => `<div class="fin-foot">${c.ask != null
    ? `<span class="fin-ask">${esc(finAskText(c.ask.who))}</span><button type="button" class="ghost" data-fin-cat-no>Cancel</button><button type="button" class="primary" data-fin-cat-yes${state.busy ? ' disabled' : ''}>Send</button>`
    : `<button type="submit" class="primary" data-fin-cat-save${ready() && !state.busy ? '' : ' disabled'}>Save</button>`}</div>`;
  const foot = () => { const f = form.querySelector('.fin-foot'); if (f) f.outerHTML = footHtml(); };
  const draw = focus => {
    const forOpts = !posted && scoped() ? finTxForOpts(lineOf(now.account)) : [];
    if (forOpts.length && !forOpts.some(([v]) => v === now.for)) now.for = forOpts[0][0];
    const name = lines.find(l => l.account_id === now.account)?.account || now.account;
    form.innerHTML = `<label>Line<select name="account">${mixed('account')}${opt('', '—', now.account)}${now.account && now.account !== M && !lineOf(now.account) ? opt(now.account, name, now.account) : ''}${chart.map(a => opt(a.id, a.name, now.account)).join('')}</select></label>
      ${forOpts.length ? `<label>For<select name="for">${forOpts.map(([v, l]) => opt(v, l, now.for)).join('')}</select></label>` : ''}
      ${posted ? `<label>Teammate<select name="teammate">${mixed('teammate')}${opt('', '—', now.teammate)}${(acc.teammates || []).map(t => opt(t.teammate, finWho(t.teammate), now.teammate)).join('')}</select></label>
      <label>Team<select name="team">${mixed('team')}${opt('', '—', now.team)}${(acc.teams || []).map(t => opt(t.id, t.name, now.team)).join('')}</select></label>
      <label>Project<input name="project" maxlength="80" autocomplete="off" value="${now.project === M ? '' : esc(now.project)}"${now.project === M ? ' placeholder="Mixed"' : ''}></label>` : ''}${footHtml()}`;
    if (focus) form.querySelector(focus)?.focus();
  };
  // One categorize or match at a time on the tab (state.busy keeps every Save and Send off until it answers). An approval
  // prompt holds the payload its dry run was for, and Send sends that payload; changing the form drops the prompt.
  const send = async asked => {
    if (state.busy) return;
    const p = asked ? asked.p : payload(), sent = JSON.stringify(p);
    state.busy = true; foot();
    const live = () => FIN?.tx === state && state.cat === c && document.contains(form);
    let saved = false;
    try {
      const res = await finDecide('categorize', p, asked ? asked.sig : '');
      if (res.ask != null) {
        // Only for the row and the picks it was asked about; anything changed since means press Save again.
        if (live() && JSON.stringify(payload()) === sent) c.ask = {who: res.ask, p, sig: res.sig};
        return;
      }
      if (res.status === 'committed') toast('Saved');
      saved = true;
    } catch (e) { toast(e.message, true); }
    finally {
      state.busy = false;
      // After a save the reload redraws the rail, so the saved row's picks are rebuilt from its fresh read (another
      // row's stay); otherwise the rail redraws now, with its Save back on.
      if (saved) {
        if (FIN?.tx === state && state.cat?.key === c.key) state.cat = null;
        void finTxLoad(false);
      } else finTxCatRedraw();
    }
  };
  form.onchange = ev => {
    const el = ev.target;
    if (!['account', 'for', 'teammate', 'team'].includes(el.name)) return;
    now[el.name] = el.value; c.ask = null;
    if (el.name !== 'account') { foot(); return; }
    // A posted row moved to a line that needs a teammate or team gets the viewer when it has neither.
    if (posted && scoped() && !now.teammate && !now.team) now.teammate = finCaps()?.who || '';
    draw('select[name="account"]');
  };
  form.oninput = ev => {
    if (ev.target.name !== 'project') return;
    now.project = ev.target.value; c.typed = true; c.ask = null; foot();
  };
  form.onclick = ev => {
    if (ev.target.closest('[data-fin-cat-no]')) { c.ask = null; foot(); }
    else if (ev.target.closest('[data-fin-cat-yes]') && c.ask) void send(c.ask);
  };
  form.onsubmit = ev => { ev.preventDefault(); if (ready() && c.ask == null) void send(null); };
  draw('');
}

// ----------------------------------------------------------------- transfers
// Moves between the company's own bank accounts (payment_create method "internal", backend/finance/transfers.py), and
// payment_cancel while one is still queued. GET /api/v2/finance/transfers?cursor= ->
// {transfers: [{id, from: {id, name, last4}, to: {…}, amount, memo, status, last_error, created_at, created_by}], next_cursor}.
const FIN_XFER_STATUS = {queued: ['waiting', 'Queued'], submitted: ['waiting', 'Sent'], unknown: ['fail', 'Check bank'],
  failed: ['fail', 'Failed'], posted: ['ok', 'Posted'], canceled: ['idle', 'Cancelled']};
const finAcctName = a => a ? `${a.name || ''}${a.last4 ? ' ••' + a.last4 : ''}` : '';
async function finXferLoad(more) {
  const state = FIN.tx, load = ++state.load;
  const qs = new URLSearchParams({limit: '50'});
  if (more && state.next) qs.set('cursor', state.next);
  let d;
  try { d = await get('/v2/finance/transfers?' + qs + finAs(), {fresh: true}); }
  catch (e) { if (FIN?.tx === state && state.load === load && $('#fin-tx')) { $('#fin-tx').innerHTML = finFail(e); finRail(''); } return; }
  if (FIN?.tx !== state || state.load !== load || !$('#fin-tx')) return;
  const rows = (d.transfers || []).map(x => ({...x, kind: 'transfer'}));
  state.rows = more ? [...state.rows, ...rows] : rows;
  state.next = d.next_cursor || null;
  if (!state.rows.some(r => finTxKey(r) === state.sel)) state.sel = state.rows[0] ? finTxKey(state.rows[0]) : '';
  finXferDraw(); finXferRail();
}
function finXferDraw() {
  const state = FIN.tx, box = $('#fin-tx'); if (!box) return;
  if (!state.rows.length) { box.innerHTML = finNote('No transfers.'); return; }
  box.innerHTML = `<table class="fin-t"><thead><tr><th>Date</th><th>From</th><th>To</th><th class="fin-n">Amount</th><th>Memo</th><th>Status</th></tr></thead><tbody>${state.rows.map(r => {
    const key = finTxKey(r), [cls, label] = FIN_XFER_STATUS[r.status] || ['idle', r.status];
    return `<tr data-fin-tx="${esc(key)}"${state.sel === key ? ' class="fin-on"' : ''}><td class="fin-nowrap">${esc(finDayLabel(r.created_at))}</td>
      <td><button type="button" class="fin-link">${esc(finAcctName(r.from))}</button></td><td>${esc(finAcctName(r.to))}</td><td class="fin-n">${finCents(r.amount)}</td>
      <td>${r.memo ? esc(r.memo) : '<span class="muted">—</span>'}</td><td><span class="pill ${cls}">${esc(label)}</span></td></tr>`;
  }).join('')}</tbody></table>${state.next ? '<button type="button" class="ghost fin-more-btn" data-fin-more>More</button>' : ''}`;
}
function finXferRail() {
  const state = FIN.tx, r = state.rows.find(x => finTxKey(x) === state.sel);
  if (!r) { finRail(''); return; }
  const [cls, label] = FIN_XFER_STATUS[r.status] || ['idle', r.status];
  finRail(`<div class="fin-rail-title"><h2>Transfer</h2><span class="pill ${cls}">${esc(label)}</span></div>
    ${finKv('Amount', finUsd(r.amount))}${finKv('From', esc(finAcctName(r.from)))}${finKv('To', esc(finAcctName(r.to)))}
    ${finKv('Date', esc(finDayLabel(r.created_at)))}${r.created_by ? finKv('By', esc(finWho(r.created_by))) : ''}${r.memo ? finKv('Memo', esc(r.memo)) : ''}
    ${r.last_error ? finKv('Error', esc(r.last_error), 'fin-bad') : ''}
    ${r.status === 'queued' && finCaps().may('finance.pay') ? '<div class="fin-foot"><button type="button" class="ghost" data-fin-xfer-cancel>Cancel transfer</button></div>' : ''}`);
  $('#fin-rail [data-fin-xfer-cancel]')?.addEventListener('click', async ev => {
    const btn = ev.currentTarget; btn.disabled = true;
    try { const res = await finAct('payment_cancel', {transfer_id: r.id}); if (res.status === 'committed') toast('Cancelled'); void finTxLoad(false); }
    catch (e) { toast(e.message, true); btn.disabled = false; }
  });
}
async function finXferAdd() {
  let accounts = [];
  try { accounts = await finPayFrom(); } catch (e) { toast(e.message, true); return; }
  if (accounts.length < 2) { toast('Needs two bank accounts', true); return; }
  finDecideDialog('Transfer', `<label>From<select name="from" required>${finOpts(accounts, accounts[0].id)}</select></label>
    <label>To<select name="to" required>${finOpts(accounts, accounts[1].id)}</select></label>
    <label>Amount<input name="amount" inputmode="decimal" required autocomplete="off"></label>
    <label>Memo<input name="memo" maxlength="140" autocomplete="off"></label>`, 'Transfer', form => {
    const el = form.elements, amount = finParseCents(el.amount.value);
    if (!amount) throw new Error('A dollar amount, like 500');
    if (el.from.value === el.to.value) throw new Error('Pick two different accounts');
    return ['payment_create', {method: 'internal', from_account: el.from.value, to_account: el.to.value, amount, ...(el.memo.value.trim() ? {memo: el.memo.value.trim()} : {})}];
  }, 'Queued');
}
