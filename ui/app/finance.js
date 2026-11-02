/* ui/app/finance.js — The Finance tab (spec §12): its place in the sidebar, the page head with its tabs (P&L, Balance
   sheet, Payables, Receivables, Cards, Transactions, Sources, Access), the owner's
   "View as", and what every tab shares: money in cents shown as dollars, a right rail that stacks under the table on
   a phone, CSV export of what is on screen, and actions (POST /api/v2/finance/actions, a fresh Idempotency-Key each).
   Every number is the route's, cut to the viewer (§7.5); nothing hidden is worked out here.
   Reads: GET /api/v2/finance/access (who sees what: the sidebar link and the tabs follow it).
   The tabs are their own files: finance-pnl.js (P&L and a line's page), finance-books.js (Balance sheet, Cards,
   Transactions), finance-docs.js (Payables, Receivables, and entering a bill, invoice or journal entry),
   finance-admin.js (Sources, Access).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const FIN_ROUTE = '#/finance';
const FIN_TABS = [['pnl', 'P&L'], ['balance', 'Balance sheet'], ['payables', 'Payables'], ['receivables', 'Receivables'],
  ['cards', 'Cards'], ['transactions', 'Transactions'], ['tax', 'Tax'], ['sources', 'Sources'], ['access', 'Access']];
let FIN = null;           // the open page: {load, tab, arg, q, acc, caps}
let FIN_ACCESS = null;    // the last GET /v2/finance/access, or null (not set up, or nothing to see)
let FIN_PROBE = null;     // the sidebar's one read per visit
let FIN_VIEW_AS = '';     // the owner viewing Finance as a teammate (read only): ?view_as=… in the address

// ----------------------------------------------------------------- who sees what
const finMe = () => S.me?.id ? 'human:' + S.me.id : '';
// What a teammate's Finance holds, from their access row: the tabs, and the rights behind each button.
function finCapsOf(acc, who) {
  const row = (acc?.teammates || []).find(t => t.teammate === who);
  if (!row) return null;
  const live = (row.grants || []).filter(g => g.live && g.status === 'active');
  const what = new Set(live.map(g => g.what));
  const owner = row.default === 'everything';
  const all = owner || what.has('everything');
  const rights = new Set(row.rights || []);
  const may = right => owner || rights.has(right) || rights.has('finance.admin');
  return {row, owner, all, rights, may,
    any: all || row.default !== 'none' || live.length > 0,
    company: all || what.has('pnl') || what.has('pnl_lines'),
    // Company-wide expenses (backend/finance/core/view.py company_expenses), which the Unattributed filter needs.
    companyExpenses: all || live.some(g => g.whose === 'company' && (g.what === 'pnl' || g.what === 'expenses')),
    balance: all || what.has('balance_sheet') || what.has('bs_lines'),
    sources: all || what.has('sources'),
    // Bills and invoices show to a payables or receivables view, or to the right whose work they are
    // (backend/finance/tab_ap.py sees_side).
    payables: all || what.has('payables') || may('finance.ap'),
    receivables: all || what.has('receivables') || may('finance.ar')};
}
// The viewer's own: what the reads run as (the teammate viewed as, for the owner), and whether writes are open.
function finCaps() {
  const acc = FIN?.acc || FIN_ACCESS, me = finMe();
  const who = FIN_VIEW_AS && acc?.can_view_as ? FIN_VIEW_AS : me;
  const caps = finCapsOf(acc, who);
  if (!caps) return null;
  const viewing = who !== me;
  return {...caps, who, viewing, may: right => !viewing && caps.may(right)};
}
// Tax workpapers are filed whole, so only a viewer who sees everything reads them (backend/finance/tax.py).
const finTabShown = (tab, caps, acc) => !!caps?.any && (tab === 'balance' ? caps.balance : tab === 'sources' ? caps.sources : tab === 'tax' ? caps.all
  : tab === 'payables' ? caps.payables : tab === 'receivables' ? caps.receivables : tab === 'access' ? !caps.viewing && (!!acc?.can_view_as || (acc?.teammates || []).length > 1) : true);
// ?view_as=… on every read while the owner views as someone.
const finAs = (sep = '&') => FIN_VIEW_AS && finCaps()?.viewing ? `${sep}view_as=${encodeURIComponent(FIN_VIEW_AS)}` : '';
// A Finance address that keeps "View as": the view is in the address, so Back and Forward carry it.
function finHref(path, params = {}) {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== ''));
  if (FIN_VIEW_AS) q.set('view_as', FIN_VIEW_AS);
  const qs = q.toString();
  return FIN_ROUTE + path + (qs ? '?' + qs : '');
}

// ----------------------------------------------------------------- the sidebar
// Finance shows in the rail once Finance is set up and the viewer sees something in it; it sits under Strategy and Growth.
function finNavSync() {
  const nav = $('#side .side-scroll'); if (!nav) return;
  const link = nav.querySelector('a[data-nav="finance"]');
  if (!finCapsOf(FIN_ACCESS, finMe())?.any) { if (link) { link.remove(); renderTree(); } return; }
  if (link) return;
  const a = Object.assign(document.createElement('a'), {className: 'nav-link', href: FIN_ROUTE,
    innerHTML: '<span class="nav-icon" aria-hidden="true">account_balance_wallet</span>Finance'});
  a.dataset.nav = 'finance';
  const after = nav.querySelector('a[data-nav="growth"]') || nav.querySelector('a[data-nav="strategy"]');
  if (after) after.after(a); else nav.prepend(a);
  renderTree();
}
async function finProbe() {
  try { const acc = await get('/v2/finance/access', {fresh: true}); FIN_ACCESS = Array.isArray(acc?.teammates) ? acc : null; }
  catch { FIN_ACCESS = null; }
  finNavSync();
  return FIN_ACCESS;
}
// Once a visit, after the first page is drawn (who is signed in is known by then).
function finProbeOnce() { finBooksReturn(); if (!FIN_PROBE && S.me?.id) FIN_PROBE = finProbe(); }

const FIN_BOOKS = {quickbooks: 'QuickBooks', xero: 'Xero'};
// Back from connecting QuickBooks or Xero: the server sends the browser to #/finance?books=connected or
// ?books=error&reason=<code> (backend/finance/books_link.py codes). Says how it went once, lands on Sources and cleans
// the address.
function finBooksReturn() {
  const [hashPath, hashQ = ''] = location.hash.split('?'), h = new URLSearchParams(hashQ);
  if (!h.has('books') || !S.me?.id) return;
  const state = h.get('books'), reason = h.get('reason') || '';
  h.delete('books'); h.delete('reason');
  const hs = h.toString(), path = hashPath && hashPath !== FIN_ROUTE && rdUnder(hashPath, FIN_ROUTE) ? hashPath : FIN_ROUTE + '/sources';
  history.replaceState(history.state, '', location.pathname + location.search + path + (hs ? '?' + hs : ''));
  if (state === 'connected') toast('Books connected');
  else if (reason === 'reconnect_required') toast('Reconnect books', true);
  else toast('Books not connected' + (FIN_BOOKS_WHY[reason] ? ': ' + FIN_BOOKS_WHY[reason] : ''), true);
  setTimeout(() => route(), 0);   // draw the cleaned address (this runs inside a route draw)
}
const FIN_BOOKS_WHY = {access_denied: 'access denied', not_connected: 'connect again', vault_write_failed: 'sign-in not saved',
  no_refresh_token: 'connect again', gateway_refused: 'try again', bad_answer: 'try again', unknown: 'try again',
  connected_elsewhere: 'already connected elsewhere', already_connected: 'already has a connection'};

// A narrow viewer's figures leave payroll out (§7.5): a read (or row) with `excluded` says so, as the P&L's totals do.
const finExcl = x => x?.excluded ? ' excl. payroll' : '';

// ----------------------------------------------------------------- formats
const FIN_MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const FIN_MONTH = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
  'November', 'December'];
const finPad = n => String(n).padStart(2, '0');
const finIso = d => `${d.getFullYear()}-${finPad(d.getMonth() + 1)}-${finPad(d.getDate())}`;
const finDate = s => { const [y, m, d] = String(s || '').slice(0, 10).split('-').map(Number); return new Date(y, (m || 1) - 1, d || 1); };
const finToday = () => finIso(new Date());
// "Oct 30", "Oct 30, 2025" when not this year.
const finDayLabel = s => {
  if (!s) return '';
  const d = finDate(s);
  return `${FIN_MON[d.getMonth()]} ${d.getDate()}${d.getFullYear() !== new Date().getFullYear() ? ', ' + d.getFullYear() : ''}`;
};
// Whole dollars from cents for statements: 18,200; a negative in parentheses, (21,740).
const finNum = c => {
  if (c == null) return '';
  const v = Math.round(c / 100);
  return v < 0 ? `(${Math.abs(v).toLocaleString('en-US')})` : v.toLocaleString('en-US');
};
// "$8,940", "$5.70": cents only when there are any.
const finUsd = c => {
  if (c == null) return '—';
  const v = Math.abs(c) / 100;
  return (c < 0 ? '−$' : '$') + v.toLocaleString('en-US', {minimumFractionDigits: c % 100 ? 2 : 0, maximumFractionDigits: 2});
};
// "420.00", "−12.50": a transaction's amount.
const finCents = c => c == null ? '' : (c < 0 ? '−' : '') + (Math.abs(c) / 100).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
// A difference that is good when positive: +300 in green, −940 in red, — when it rounds to nothing.
const finDiff = d => {
  if (d == null) return {text: '', cls: ''};
  const v = Math.round(d / 100);
  if (!v) return {text: '—', cls: 'muted'};
  return {text: (v > 0 ? '+' : '−') + Math.abs(v).toLocaleString('en-US'), cls: v > 0 ? 'fin-good' : 'fin-bad'};
};
// A typed dollar amount to cents: "1,200", "$45.50"; null when it is not one.
function finParseCents(text) {
  const t = String(text ?? '').trim().replace(/^\$\s*/, '').replace(/,/g, '');
  if (!/^\d+(\.\d{1,2})?$/.test(t)) return null;
  return Math.round(Number(t) * 100);
}
// The group with no vendor, team or teammate (backend/finance/reads.py NONE): no real key starts with a space.
const FIN_NONE = ' unattributed';
// bot:<id>, human:<id>, a team id, or FIN_NONE.
function finWho(ref) {
  const s = String(ref || '');
  if (!s || s === FIN_NONE) return 'Unattributed';
  if (s.startsWith('bot:')) return botDisplayName(s.slice(4));
  if (s.startsWith('human:')) return personDisplay(s.slice(6));
  return s;
}
const finTeamName = id => ((FIN?.acc || FIN_ACCESS)?.teams || []).find(t => t.id === id)?.name
  || (S.orgGroups || []).find(g => g.id === id)?.name || id || '';
// The teams directly under `id` ('' for the top, which takes in teams whose parent isn't shown), in the org chart's
// order (S.orgGroups), then by name.
function finTeamKids(teams, id) {
  const rank = t => (S.orgGroups || []).find(g => g.id === t.id)?.order ?? 1e9;
  return teams.filter(t => (t.parent || '') === (id || '') || (!id && t.parent && !teams.some(x => x.id === t.parent)))
    .sort((a, b) => rank(a) - rank(b) || String(a.name).localeCompare(String(b.name)));
}
// A built-in helper (the Assistant, BotOps, Strategy Bot, Librarian, Task Manager, Bookkeeper, Health Monitor…; ui/app/sidebar.js
// isHelperBot): listed together as Helpers, after the org chart.
function finHelper(actor) {
  if (!String(actor || '').startsWith('bot:')) return false;
  const id = actor.slice(4), e = (S.emps || []).find(x => x.id === id);
  return e ? isHelperBot(e) : isBuiltInBot(id);
}
const finAvatar = (ref, size = 18) => String(ref || '').startsWith('bot:') ? avatar(ref.slice(4), size)
  : personAvatar((S.people || []).find(p => p.id === String(ref).slice(6)) || {name: finWho(ref)}, size);

// ----------------------------------------------------------------- shared pieces
// A segmented switch: [[value, label]], the chosen one aria-checked.
const finSeg = (name, label, options, value) => `<div class="theme-seg fin-seg" role="radiogroup" aria-label="${esc(label)}" data-fin-seg="${esc(name)}">${options.map(([v, l]) =>
  `<button type="button" role="radio" data-v="${esc(v)}" aria-checked="${v === value}">${esc(l)}</button>`).join('')}</div>`;
// The table and its rail: on a phone the rail stacks under the table, and the table scrolls inside its box.
const finSplit = (main, rail, label) => `<div class="fin-split"><div class="fin-main">${main}</div><aside class="fin-rail" id="fin-rail" aria-label="${esc(label)}">${rail}</aside></div>`;
const finKv = (k, v, cls = '') => `<div class="fin-kv"><span>${esc(k)}</span><span class="${cls}">${v}</span></div>`;
const finNote = text => `<p class="muted fin-note">${esc(text)}</p>`;
const finFail = e => finNote(e?.status === 403 ? 'Not in your view.' : e?.message || 'Did not load.');
function finRail(html) { const rail = $('#fin-rail'); if (rail) rail.innerHTML = html; }

// An action with a fresh Idempotency-Key: committed, or sent to someone for approval; anything else is an error.
async function finAct(kind, payload) {
  const r = await post('/v2/finance/actions', {kind, payload});
  if (r?.status === 'committed') return r;
  if (r?.status === 'needs_approval') { toast(r.approver ? `Sent to ${finWho(r.approver)} for approval` : 'Sent for approval'); return r; }
  throw new Error(r?.reason || 'Not done');
}

// What is on screen as a CSV file.
function finCsv(rows, name) {
  const cell = v => { const s = String(v ?? ''); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const text = rows.map(r => r.map(cell).join(',')).join('\n') + '\n';
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([text], {type: 'text/csv'}));
  link.download = name;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}
// A table's cells as text, the way they read (for Export).
const finTableRows = table => [...(table?.querySelectorAll('tr') || [])].filter(tr => !tr.hidden)
  .map(tr => [...tr.children].flatMap(td => [td.querySelector('input')?.value ?? td.textContent.replace(/[▸▾]/g, '').trim(),
    ...Array(Math.max(0, (Number(td.getAttribute('colspan')) || 1) - 1)).fill('')]));

// A small dialog: a title, a form, Cancel and the primary button. `onSave(form)` throws to show an error, and returns
// false to keep the dialog open (a decision that now names its approver).
function finDialog(title, body, saveLabel, onSave) {
  document.querySelectorAll('dialog.fin-dlg').forEach(d => d.close());
  const dlg = document.createElement('dialog');
  dlg.className = 'tmodal fin-dlg';
  dlg.setAttribute('aria-label', title);
  dlg.innerHTML = `<div class="tmodal-head"><b>${esc(title)}</b><span class="spacer"></span>
      <button type="button" class="ghost tmodal-x" data-close aria-label="Close">✕</button></div>
    <form class="tmodal-body fin-form" novalidate>${body}<p class="err" data-error hidden></p>
      <div class="fin-foot"><button type="button" class="ghost" data-close>Cancel</button><button type="submit" class="primary">${esc(saveLabel)}</button></div></form>`;
  document.body.appendChild(dlg);
  dlg.querySelectorAll('[data-close]').forEach(b => { b.onclick = () => dlg.close(); });
  dlg.onclose = () => dlg.remove();
  const form = dlg.querySelector('form'), err = dlg.querySelector('[data-error]'), save = dlg.querySelector('[type=submit]');
  form.onsubmit = async ev => {
    ev.preventDefault(); err.hidden = true; save.disabled = true;
    try { if (await onSave(form) === false) save.disabled = false; else dlg.close(); }
    catch (e) { err.textContent = e.message; err.hidden = false; save.disabled = false; }
  };
  dlg.showModal();
  form.querySelector('input:not([type=hidden]),select')?.focus();
  return dlg;
}

// ----------------------------------------------------------------- the page
function pageFinance() {
  const [path, query] = (S.route || '').split('?');
  const parts = path.slice(FIN_ROUTE.length + 1).split('/').filter(Boolean);
  // A dialog belongs to the page it opened on: a Finance route change (Back, Forward, a link) closes it.
  document.querySelectorAll('dialog.fin-dlg').forEach(d => d.close());
  FIN = {load: (FIN?.load || 0) + 1, tab: parts[0] || '', arg: decodeURIComponent(parts[1] || ''),
    q: new URLSearchParams(query || ''), acc: FIN_ACCESS};
  FIN_VIEW_AS = FIN.q.get('view_as') || '';
  const main = $('#main');
  main.classList.add('strategy-layout', 'finance-layout');
  main.innerHTML = finHeadHtml() + `<div id="fin-body" class="fin-body">${skelRows(6, {dot: false})}</div>`;
  const state = FIN;
  void (async () => {
    let acc = null, error = null;
    try { acc = await get('/v2/finance/access', {fresh: true}); } catch (e) { error = e; }
    if (FIN !== state || !$('#fin-body')) return;
    FIN_ACCESS = state.acc = Array.isArray(acc?.teammates) ? acc : null;
    finNavSync();
    if (!state.acc) { $('#fin-body').innerHTML = finNote(error?.message || 'Finance is not set up.'); return; }
    finShow(state);
  })();
}
function finShow(state) {
  const caps = finCaps(), acc = state.acc;
  if (!caps?.any) { finHeadDraw(); $('#fin-body').innerHTML = finNote('Nothing in Finance is in your view.'); return; }
  const tab = state.tab === 'line' ? 'pnl' : state.tab;
  if (!tab || !finTabShown(tab, caps, acc)) {
    location.replace(finHref('/pnl', {view: caps.company ? '' : 'expenses'}));
    return;
  }
  finHeadDraw();
  if (state.tab === 'line') finLinePage(state.arg);
  else if (tab === 'pnl') finPnlPage();
  else if (tab === 'balance') finBalancePage();
  else if (tab === 'payables') finDocPage('bill');
  else if (tab === 'receivables') finDocPage('invoice');
  else if (tab === 'cards') finCardsPage();
  else if (tab === 'transactions') finTxPage();
  else if (tab === 'tax') finTaxPage();
  else if (tab === 'sources') finSourcesPage();
  else if (tab === 'access') finAccessPage();
}
function finHeadHtml() {
  const caps = FIN?.acc ? finCaps() : null, acc = FIN?.acc, on = FIN?.tab === 'line' ? 'pnl' : FIN?.tab;
  const tabs = caps ? FIN_TABS.filter(([id]) => finTabShown(id, caps, acc)) : [];
  // The P&L tab keeps the view it was left on (Company or Expenses); on the P&L itself, the one in the address.
  const pnlView = FIN?.tab === 'pnl' ? FIN.q?.get('view') : FIN_PNL.view;
  const viewing = caps?.viewing ? `<span class="fin-as">Viewing as ${esc(finWho(caps.who))}<button type="button" class="ghost" data-fin-as-end aria-label="Stop viewing as ${esc(finWho(caps.who))}">✕</button></span>` : '';
  return `<div class="strat-head rd-strat-head fin-head" id="fin-head"><h1>Finance</h1>
    <nav class="tabs slim strat-tabs" aria-label="Finance">${tabs.map(([id, label]) =>
      `<a href="${finHref('/' + id, id === 'pnl' && pnlView === 'expenses' ? {view: 'expenses'} : {})}" data-fin-tab="${id}"${on === id ? ' class="cur" aria-current="page"' : ''}>${esc(label)}</a>`).join('')}</nav>
    <span class="spacer"></span>${viewing}</div>`;
}
function finHeadDraw() {
  const head = $('#fin-head'); if (!head) return;
  head.outerHTML = finHeadHtml();
  const end = $('#fin-head [data-fin-as-end]');
  if (end) end.onclick = () => { FIN_VIEW_AS = ''; location.hash = finHref('/' + (FIN?.tab === 'line' ? 'pnl' : FIN?.tab || 'pnl')); };
}
// The owner views Finance as a teammate sees it: every tab reads as them, and nothing can be changed meanwhile.
function finViewAs(who) {
  FIN_VIEW_AS = who || '';
  location.hash = finHref('/pnl');
}
function financeStop() { if (FIN && !rdUnder(S.route || '', FIN_ROUTE)) { FIN = null; document.querySelectorAll('dialog.fin-dlg').forEach(d => d.close()); } }

// The rail's bar list: [{label, amount}] scaled to the largest.
function finBars(rows) {
  const top = Math.max(1, ...rows.map(r => Math.abs(r.amount || 0)));
  return `<div class="fin-bars">${rows.map(r => `<span class="fin-bars-k">${esc(r.label)}</span><span class="fin-bars-b" aria-hidden="true"><i style="width:${Math.round(Math.abs(r.amount || 0) / top * 100)}%"></i></span><span class="fin-bars-v">${finNum(r.amount)}</span>`).join('')}</div>`;
}
