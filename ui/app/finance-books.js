/* ui/app/finance-books.js — Finance › Balance sheet, Cards and Transactions (spec §12).
   Balance sheet: assets, liabilities and equity as of a day (and a day to compare with), each line with the accounts
   that feed it; the totals are the route's. Cards: each card with its holder, budget, limit and spend this period;
   the rail freezes, unfreezes and sets the limit. Transactions: bank feed rows and entries, searched and filtered,
   paged by cursor; the rail tags a row's lines (categorize) for those who keep the books.
   Reads: GET /api/v2/finance/balance-sheet, /finance/cards, /finance/transactions, /finance/budgets.
   Writes (POST /api/v2/finance/actions): card_create, card_freeze, card_unfreeze, card_update_limit, categorize.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- balance sheet
const FIN_BS = {asOf: '', compare: '', sel: ''};
const FIN_BS_SECTIONS = [['assets', 'Assets', 'asset'], ['liabilities', 'Liabilities', 'liability'], ['equity', 'Equity', 'equity']];
function finBalancePage() {
  const b = FIN_BS, caps = finCaps();
  if (!b.asOf) b.asOf = finToday();
  FIN.bs = {data: null, prev: null, load: 0};
  $('#fin-body').innerHTML = `<div class="fin-bar"><label class="fin-inline"><span class="muted">As of</span><input type="date" data-fin-asof value="${b.asOf}"></label>
      <label class="fin-inline"><span class="muted">Compare</span><input type="date" data-fin-cmp value="${b.compare}"></label>
      <span id="fin-balanced"></span><span class="spacer"></span><button type="button" class="ghost" data-fin-export>Export</button>
      ${caps.may('finance.admin') ? '<button type="button" class="primary" data-fin-bank>+ Bank account</button>' : ''}</div>`
    + finSplit(`<div class="fin-scroll" id="fin-bs">${skelRows(8, {dot: false})}</div>`, '', 'Selected line');
  const body = $('#fin-body');
  body.querySelector('[data-fin-asof]').onchange = ev => { b.asOf = ev.target.value || finToday(); void finBsLoad(); };
  body.querySelector('[data-fin-cmp]').onchange = ev => { b.compare = ev.target.value; void finBsLoad(); };
  body.onclick = ev => {
    if (ev.target.closest('[data-fin-export]')) { finCsv(finTableRows($('#fin-bs table')), `balance-sheet-${b.asOf}.csv`); return; }
    if (ev.target.closest('[data-fin-bank]')) { finConnectMercury(); return; }
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
  const prevOf = (key, sec) => { if (!prev) return null; const t = finBsTree(prev[sec] || []).find(r => finBsKey(r) === key); return t ? t.sum : null; };
  const source = s => `<span class="fin-src">${esc([s.provider, s.name].filter(Boolean).join(' · '))}${s.last4 ? ' ••' + esc(s.last4) : ''}</span>`;
  const cols = prev ? 5 : 3;
  const out = [];
  for (const [sec, label, key] of FIN_BS_SECTIONS) {
    const tree = finBsTree(d[sec] || []);
    out.push(`<tr class="fin-sec"><td>${label}</td><td colspan="${cols - 1}"></td></tr>`);
    for (const r of tree) {
      const k = finBsKey(r), was = prevOf(k, sec), ch = was == null ? null : finDiff(r.sum - was);
      const from = [...(r.sources || []).map(source), ...(r.entry_sources || []).map(s => `<span class="fin-src">${esc(s)}</span>`)].join('');
      out.push(`<tr class="fin-ln${r.kids ? ' fin-parent' : ''}${b.sel === k ? ' fin-on' : ''}" style="--d:${r.depth}" data-fin-line="${esc(k)}" tabindex="0"><td>${esc(r.name)}</td><td class="fin-n">${finNum(r.sum)}</td>${prev
        ? `<td class="fin-n">${finNum(was)}</td><td class="fin-n ${ch?.cls || ''}">${ch?.text || ''}</td>` : ''}<td class="fin-from">${from}</td></tr>`);
    }
    const t = totals[key], tp = prev?.totals?.[key], ch = prev && t != null && tp != null ? finDiff(t - tp) : null;
    out.push(`<tr class="fin-subt"><td>Total ${label.toLowerCase()}</td><td class="fin-n">${finNum(t)}</td>${prev ? `<td class="fin-n">${finNum(tp)}</td><td class="fin-n ${ch?.cls || ''}">${ch?.text || ''}</td>` : ''}<td></td></tr>`);
  }
  if (totals.liability != null || totals.equity != null) {
    const le = (totals.liability || 0) + (totals.equity || 0), lp = prev ? (prev.totals?.liability || 0) + (prev.totals?.equity || 0) : null;
    out.push(`<tr class="fin-tot"><td>Liabilities + equity</td><td class="fin-n">${finNum(le)}</td>${prev ? `<td class="fin-n">${finNum(lp)}</td><td class="fin-n ${finDiff(le - lp).cls}">${finDiff(le - lp).text}</td>` : ''}<td></td></tr>`);
  }
  box.innerHTML = `<table class="fin-bs"><thead><tr><th></th><th class="fin-n">${esc(finDayLabel(d.as_of || b.asOf))}</th>${prev
    ? `<th class="fin-n">${esc(finDayLabel(prev.as_of || b.compare))}</th><th class="fin-n">Change</th>` : ''}<th>From</th></tr></thead><tbody>${out.join('')}</tbody></table>`;
  box.querySelector('tbody').onkeydown = ev => { const tr = ev.target.closest('tr[data-fin-line]'); if (tr && ev.key === 'Enter') tr.click(); };
}
function finBsRail() {
  const state = FIN.bs, b = FIN_BS; if (!state?.data) return;
  let row = null, label = '';
  for (const [sec, name] of FIN_BS_SECTIONS) { const r = finBsTree(state.data[sec] || []).find(x => finBsKey(x) === b.sel); if (r) { row = r; label = name; break; } }
  if (!row) { finRail(''); return; }
  const srcs = row.sources || [];
  finRail(`<div class="muted fin-sup">${esc(label)}</div><h2>${esc(row.name)}</h2>
    ${finKv('Balance', finUsd(row.sum))}${row.kids ? finKv('This line', finUsd(row.balance)) : ''}
    ${srcs.length ? `<h3 class="fin-h">Accounts</h3>${srcs.map(s => finKv(`${s.name || s.provider}${s.last4 ? ' ••' + s.last4 : ''}`,
      `${finUsd(s.provider_balance)}${s.balance_as_of ? ` <span class="muted">${esc(finDayLabel(s.balance_as_of))}</span>` : ''}`)).join('')}` : ''}
    ${(row.entry_sources || []).length ? `<h3 class="fin-h">From</h3>${row.entry_sources.map(s => `<span class="fin-src">${esc(s)}</span>`).join('')}` : ''}`);
}

// ----------------------------------------------------------------- cards
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
  state.rows = more ? [...state.rows, ...(d.cards || [])] : d.cards || [];
  state.next = d.next || null;
  if (!state.rows.some(c => c.id === state.sel)) state.sel = state.rows[0]?.id || '';
  finCardsDraw(); finCardRail();
}
const finCardName = c => `${c.nickname || 'Card'}${c.last4 ? ' · ' + c.last4 : ''}`;
function finCardsDraw() {
  const state = FIN.cards, box = $('#fin-cards'); if (!box) return;
  if (!state.rows.length) { box.innerHTML = finNote('No cards yet.'); return; }
  box.innerHTML = `<table class="fin-t"><thead><tr><th>Card</th><th>Holder</th><th>Budget</th><th class="fin-n">Limit</th><th class="fin-n">Spent</th><th>Status</th></tr></thead><tbody>${state.rows.map(c => {
    const [cls, label] = FIN_CARD_STATUS[c.status] || ['idle', c.status];
    return `<tr data-fin-card="${esc(c.id)}"${state.sel === c.id ? ' class="fin-on"' : ''}><td><button type="button" class="fin-link">${esc(finCardName(c))}</button></td><td><span class="fin-who">${finAvatar(c.holder, 18)}${esc(finWho(c.holder))}</span></td>
      <td>${c.budget ? esc(c.budget.name) : '<span class="muted">—</span>'}</td><td class="fin-n">${c.limit == null ? '<span class="muted">—</span>' : finNum(c.limit)}</td><td class="fin-n">${finNum(c.spent)}</td>
      <td><span class="pill ${cls}">${esc(label)}</span>${c.sync_status && c.sync_status !== 'ok' ? ` <span class="muted fin-small">${esc(c.sync_status)}</span>` : ''}</td></tr>`;
  }).join('')}</tbody></table>${state.next ? '<button type="button" class="ghost fin-more-btn" data-fin-more>More</button>' : ''}`;
}
function finCardRail() {
  const state = FIN.cards, c = state.rows.find(x => x.id === state.sel);
  if (!c) { finRail(''); return; }
  const may = finCaps().may('finance.cards') && c.status !== 'cancelled';
  const [cls, label] = FIN_CARD_STATUS[c.status] || ['idle', c.status];
  finRail(`<div class="fin-rail-title"><h2>${esc(finCardName(c))}</h2><span class="pill ${cls}">${esc(label)}</span></div>
    ${finKv('Holder', esc(finWho(c.holder)))}${finKv('Budget', c.budget ? esc(c.budget.name) : '—')}
    ${finKv('Limit', c.limit == null ? '—' : `${finUsd(c.limit)}${c.limit_interval ? ` <span class="muted">/ ${esc(c.limit_interval.replace(/ly$/, '').replace('dai', 'day'))}</span>` : ''}`)}
    ${finKv('Spent', finUsd(c.spent))}${c.expires_at ? finKv('Expires', esc(finDayLabel(c.expires_at))) : ''}
    ${may ? `<form class="fin-form fin-rail-form" data-fin-limit><label>Limit<input name="amount" inputmode="decimal" autocomplete="off" value="${c.limit == null ? '' : c.limit / 100}"></label>
      <div class="fin-foot"><button type="button" class="ghost" data-fin-freeze>${c.status === 'frozen' ? 'Unfreeze' : 'Freeze'}</button><button type="submit" class="primary">Save limit</button></div></form>` : ''}`);
  const rail = $('#fin-rail');
  rail.querySelector('[data-fin-freeze]')?.addEventListener('click', async ev => {
    const btn = ev.currentTarget; btn.disabled = true;
    try {
      const r = await finAct(c.status === 'frozen' ? 'card_unfreeze' : 'card_freeze', {card_id: c.id});
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
async function finCardAdd() {
  let budgets = [];
  try { budgets = (await get('/v2/finance/budgets', {fresh: true}))?.budgets || []; } catch { /* a card can go without one */ }
  const people = (FIN.acc?.teammates || []);
  finDialog('Card', `<label>Holder<select name="holder" required>${people.map(t => `<option value="${esc(t.teammate)}">${esc(finWho(t.teammate))}</option>`).join('')}</select></label>
    <label>Budget<select name="budget"><option value="">None</option>${budgets.filter(b => b.active).map(b => `<option value="${esc(b.id)}">${esc(b.name)}</option>`).join('')}</select></label>
    <label>Limit / mo<input name="amount" inputmode="decimal" required autocomplete="off"></label>
    <label>Name<input name="nickname" maxlength="60" autocomplete="off"></label>`, 'Create', async form => {
    const cents = finParseCents(form.elements.amount.value);
    if (cents == null) throw new Error('A dollar amount, like 500');
    const payload = {holder: form.elements.holder.value, amount: cents};
    if (form.elements.budget.value) payload.budget_id = form.elements.budget.value;
    if (form.elements.nickname.value.trim()) payload.nickname = form.elements.nickname.value.trim();
    const r = await finAct('card_create', payload);
    if (r.status === 'committed') toast('Card created');
    void finCardsLoad(false);
  });
}

// ----------------------------------------------------------------- transactions
const FIN_TX = {q: '', source: 'all', status: '', account: ''};
const FIN_TX_STATUS = [['', 'Any status'], ['new', 'New'], ['matched', 'Matched'], ['posted', 'Posted'], ['pending', 'Pending'], ['ignored', 'Ignored']];
function finTxPage() {
  const t = FIN_TX;
  if (FIN.q.has('q') || FIN.q.has('account_id')) { t.q = FIN.q.get('q') || ''; t.account = FIN.q.get('account_id') || ''; t.source = 'all'; t.status = ''; }
  FIN.tx = {rows: [], next: null, sel: '', load: 0, lines: []};
  $('#fin-body').innerHTML = `<div class="fin-bar"><label class="fin-search"><span class="nav-icon" aria-hidden="true">search</span><span class="sr-only">Search</span><input type="search" data-fin-q value="${esc(t.q)}" placeholder="Search" aria-label="Search"></label>
      ${finSeg('source', 'Source', [['all', 'All'], ['feed', 'Bank'], ['entry', 'Entries']], t.source)}
      <select data-fin-status aria-label="Status">${FIN_TX_STATUS.map(([v, l]) => `<option value="${v}"${v === t.status ? ' selected' : ''}>${l}</option>`).join('')}</select>
      <select data-fin-account aria-label="Line"><option value="">All lines</option>${t.account ? `<option value="${esc(t.account)}" selected>${esc(t.account)}</option>` : ''}</select></div>`
    + finSplit(`<div class="fin-scroll" id="fin-tx">${skelRows(8, {dot: false})}</div>`, '', 'Selected transaction');
  const body = $('#fin-body');
  let timer = 0;
  body.querySelector('[data-fin-q]').oninput = ev => { clearTimeout(timer); timer = setTimeout(() => { t.q = ev.target.value.trim(); void finTxLoad(false); }, 300); };
  body.querySelector('[data-fin-status]').onchange = ev => { t.status = ev.target.value; void finTxLoad(false); };
  body.querySelector('[data-fin-account]').onchange = ev => { t.account = ev.target.value; void finTxLoad(false); };
  body.onclick = ev => {
    const seg = ev.target.closest('[data-fin-seg] button');
    if (seg) { if (seg.dataset.v !== t.source) { t.source = seg.dataset.v; finSegSet(seg); void finTxLoad(false); } return; }
    if (ev.target.closest('[data-fin-more]')) { void finTxLoad(true); return; }
    const tr = ev.target.closest('tr[data-fin-tx]');
    if (tr) { FIN.tx.sel = tr.dataset.finTx; finTxDraw(); finTxRail(); }
  };
  void finTxLines();
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
  const state = FIN.tx, t = FIN_TX, load = ++state.load;
  const qs = new URLSearchParams({source: t.source, limit: '50'});
  if (t.q) qs.set('q', t.q.slice(0, 100));
  if (t.status) qs.set('status', t.status);
  if (t.account) qs.set('account_id', t.account);
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
const finTxTags = r => r.kind === 'entry' ? {tags: r.tags || {}, vendor: r.vendor} : {tags: r.lines?.[0]?.tags || {}, vendor: r.lines?.[0]?.vendor};
function finTxDraw() {
  const state = FIN.tx, box = $('#fin-tx'); if (!box) return;
  if (!state.rows.length) { box.innerHTML = finNote('No transactions.'); return; }
  box.innerHTML = `<table class="fin-t"><thead><tr><th>Date</th><th>Description</th><th>Account</th><th class="fin-n">Amount</th><th>Teammate</th><th>Vendor</th><th>Status</th></tr></thead><tbody>${state.rows.map(r => {
    const {tags, vendor} = finTxTags(r), key = finTxKey(r);
    const account = r.kind === 'feed' ? `${r.money_account?.name || ''}${r.money_account?.last4 ? ' ••' + r.money_account.last4 : ''}` : r.account || '';
    const status = r.kind === 'feed' ? (r.pending ? 'pending' : r.status) : r.reversed ? 'reversed' : 'posted';
    return `<tr data-fin-tx="${esc(key)}"${state.sel === key ? ' class="fin-on"' : ''}><td class="fin-nowrap">${esc(finDayLabel(r.date))}</td>
      <td><button type="button" class="fin-link">${esc(r.kind === 'feed' ? r.counterparty || r.description || '' : r.description || r.memo || '')}</button></td><td>${esc(account)}</td>
      <td class="fin-n">${finCents(r.amount)}</td><td>${tags.teammate ? esc(finWho(tags.teammate)) : '<span class="muted">—</span>'}</td><td>${vendor ? esc(vendor) : '<span class="muted">—</span>'}</td>
      <td><span class="pill ${{posted: 'ok', matched: 'ok', pending: 'waiting', new: 'waiting', ignored: 'idle', reversed: 'idle'}[status] || 'idle'}">${esc(status)}</span></td></tr>`;
  }).join('')}</tbody></table>${state.next ? '<button type="button" class="ghost fin-more-btn" data-fin-more>More</button>' : ''}`;
}
function finTxRail() {
  const state = FIN.tx, r = state.rows.find(x => finTxKey(x) === state.sel);
  if (!r) { finRail(''); return; }
  const lines = r.kind === 'feed' ? (r.lines || []) : r.entry_id && r.id ? [{line_id: r.id, account: r.account, amount: r.amount, vendor: r.vendor, tags: r.tags || {}}] : [];
  const may = finCaps().may('finance.book') && lines.length > 0;
  const {tags} = finTxTags(r), acc = FIN.acc || {};
  const title = r.kind === 'feed' ? r.counterparty || r.description || '' : r.description || r.memo || '';
  const opt = (v, l, cur) => `<option value="${esc(v)}"${v === (cur || '') ? ' selected' : ''}>${esc(l)}</option>`;
  finRail(`<div class="fin-rail-title"><h2>${esc(title)}</h2><span class="fin-big">${finUsd(r.amount)}</span></div>
    ${finKv('Date', esc(finDayLabel(r.date)))}
    ${r.kind === 'feed' ? finKv('Account', esc(`${r.money_account?.name || ''}${r.money_account?.last4 ? ' ••' + r.money_account.last4 : ''}`)) + (r.description && r.description !== title ? finKv('Description', esc(r.description)) : '') : finKv('Source', esc(r.source || ''))}
    ${r.memo ? finKv('Memo', esc(r.memo)) : ''}
    ${lines.length ? `<h3 class="fin-h">Lines</h3>${lines.map(l => finKv(l.account || '', finCents(l.amount))).join('')}` : ''}
    ${may ? `<form class="fin-form fin-rail-form" data-fin-tag><label>Teammate<select name="teammate">${opt('', '—', tags.teammate)}${(acc.teammates || []).map(t => opt(t.teammate, finWho(t.teammate), tags.teammate)).join('')}</select></label>
      <label>Team<select name="team">${opt('', '—', tags.team)}${(acc.teams || []).map(t => opt(t.id, t.name, tags.team)).join('')}</select></label>
      <label>Project<input name="project" maxlength="80" autocomplete="off" value="${esc(tags.project || '')}"></label>
      <div class="fin-foot"><button type="submit" class="primary">Save</button></div></form>` : ''}`);
  const form = $('#fin-rail [data-fin-tag]');
  if (form) form.onsubmit = async ev => {
    ev.preventDefault();
    // Each line keeps its other tags (budget, custom); the three here are set or cleared.
    const pick = l => {
      const next = {...(l.tags || {})};
      for (const k of ['teammate', 'team', 'project']) { const v = form.elements[k].value.trim(); if (v) next[k] = v; else delete next[k]; }
      return next;
    };
    const btn = form.querySelector('[type=submit]'); btn.disabled = true;
    try {
      const res = await finAct('categorize', {lines: lines.map(l => ({line_id: l.line_id, tags: pick(l)})), mode: 'replace'});
      if (res.status === 'committed') toast('Saved');
      void finTxLoad(false);
    } catch (e) { toast(e.message, true); btn.disabled = false; }
  };
}
