/* ui/app/finance.ts — The Finance tab (spec §12): its place in the sidebar, the page head with its tabs (P&L, Budgets,
   Balance sheet, Payables, Receivables, Cards, Transactions, Tax, Sources, Access), the owner's
   "View as", and what every tab shares: money in cents shown as dollars, a right rail that stacks under the table on
   a phone, CSV export of what is on screen, and actions (POST /api/v2/finance/actions, a fresh Idempotency-Key each).
   Every number is the route's, cut to the viewer (§7.5); nothing hidden is worked out here.
   Reads: GET /api/v2/finance/access (who sees what: the sidebar link and the tabs follow it).
   The tabs are their own files: finance-pnl.ts (P&L and a line's page), finance-budgets.ts (Budgets), finance-books.ts
   (Balance sheet, Cards, Transactions), finance-docs.ts (Payables, Receivables, and entering a bill, invoice or journal
   entry), finance-admin.ts (Sources, Access), finance-attribution.ts (Attribution: split chips, the line's shares, the rules).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   TypeScript: npm run build:ui compiles it to build/ui/app/finance.js, which the page runs in this file's place. */
'use strict';

// GET /api/v2/finance/access with its grants (backend/finance/tab.py _grant_row) and the owner's finance approver.
type FinGrant = {
  id: string; what: string; whose: string; whose_team: string | null; account_ids: string[];
  granted_by: string | null; expires_at: string | null; status: string; live?: boolean;
};
type FinAccMate = Omit<FinAccess['teammates'][number], 'grants'> & {grants: FinGrant[]};
type FinAccData = Omit<FinAccess, 'teammates'> & {teammates: FinAccMate[]; finance_approver?: string | null};
// The open Finance page (pageFinance), from its open until financeStop clears it on leaving Finance. Each tab's own
// state (FIN.pnl, FIN.tx, ...) is declared beside the tab, in its file (this interface merges across the files).
interface FinPage {
  load: number; tab: string; arg: string; q: URLSearchParams;
  acc: FinAccData | null;
}
// The viewer's own Finance rights, as finCaps reads them.
type FinCapsNow = NonNullable<ReturnType<typeof finCaps>>;
// POST /api/v2/finance/actions: committed, sent for approval (to the approver), or refused (with its reason).
type FinActResult = import('../types/api').FinanceAction & {approver?: string | null; result?: FinActDone};
// What a committed action made or did (the ids its callers read).
interface FinActDone { bill_id?: string; invoice_id?: string; contact_id?: string; account_id?: string; months?: string[]; new?: number }

// The tabs in the header's order: the shared header (page-head.ts) keeps the first five on the row and lists the rest in
// its ⋯ (style guide "Views of one thing are tabs: one row, five at most"), so the daily views come first.
const FIN_TABS: [string, string][] = [['mine', 'My budget'], ['pnl', 'P&L'], ['budgets', 'Budgets'], ['transactions', 'Transactions'], ['payables', 'Payables'], ['receivables', 'Receivables'],
  ['balance', 'Balance sheet'], ['tax', 'Tax'], ['attribution', 'Attribution'], ['cards', 'Cards'], ['sources', 'Sources'], ['access', 'Access']];
let FIN: FinPage | null = null;           // the open page: {load, tab, arg, q, acc, caps}
let FIN_VIEW_AS = '';     // the owner viewing Finance as a teammate (read only): ?view_as=… in the address

// ----------------------------------------------------------------- who sees what
// The viewer's own: what the reads run as (the teammate viewed as, for the owner), and whether writes are open.
function finCaps() {
  const acc = FIN?.acc || FIN_ACCESS, me = finMe();
  const who = FIN_VIEW_AS && acc?.can_view_as ? FIN_VIEW_AS : me;
  const caps = finCapsOf(acc, who);
  if (!caps) return null;
  const viewing = who !== me;
  return {...caps, who, viewing, may: (right: string) => !viewing && caps.may(right)};
}
// Tax workpapers are filed whole, so only a viewer who sees everything reads them (backend/finance/tax.py).
const finTabShown = (tab: string, caps: FinCapsNow | null, acc: FinAccData | null | undefined) => !!caps?.any && FIN_TABS.some(([id]) => id === tab) && (tab === 'mine' ? caps.mine : tab === 'attribution' ? caps.attribute : !caps.sees ? false : tab === 'balance' ? caps.balance : tab === 'budgets' ? caps.budgets : tab === 'sources' ? caps.sources : tab === 'tax' ? caps.all
  : tab === 'payables' ? caps.payables : tab === 'receivables' ? caps.receivables : tab === 'access' ? !caps.viewing && (!!acc?.can_view_as || caps.rights.has('finance.admin') || (acc?.teammates || []).length > 1) : true);
// ?view_as=… on every read while the owner views as someone.
const finAs = (sep = '&') => FIN_VIEW_AS && finCaps()?.viewing ? `${sep}view_as=${encodeURIComponent(FIN_VIEW_AS)}` : '';
// A Finance address that keeps "View as": the view is in the address, so Back and Forward carry it.
function finHref(path: string, params: Record<string, string> = {}) {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== ''));
  if (FIN_VIEW_AS) q.set('view_as', FIN_VIEW_AS);
  const qs = q.toString();
  return FIN_ROUTE + path + (qs ? '?' + qs : '');
}

const FIN_BOOKS = {quickbooks: 'QuickBooks', xero: 'Xero'};

// A narrow viewer's figures leave payroll out (§7.5): a read (or row) with `excluded` says so, as the P&L's totals do.
const finExcl = (x: {excluded?: unknown; [key: string]: unknown} | null | undefined) => x?.excluded ? ' excl. payroll' : '';
// A viewer's totals over the slice they see, labeled so they aren't read as the company's (§7.5).
const finOwnSlice = (caps: FinCapsNow) => caps.viewing ? ` · ${finWho(caps.who)}'s view` : ' · Your view';

// ----------------------------------------------------------------- formats
const FIN_MONTH = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
  'November', 'December'];
const finToday = () => finIso(new Date());
// Whole dollars from cents for statements: 18,200; a negative in parentheses, (21,740).
const finNum = (c: number | null | undefined) => {
  if (c == null) return '';
  const v = Math.round(c / 100);
  return v < 0 ? `(${Math.abs(v).toLocaleString('en-US')})` : v.toLocaleString('en-US');
};
// "$8,940", "$5.70": cents only when there are any.
const finUsd = (c: number | null | undefined) => {
  if (c == null) return '—';
  const v = Math.abs(c) / 100;
  return (c < 0 ? '−$' : '$') + v.toLocaleString('en-US', {minimumFractionDigits: c % 100 ? 2 : 0, maximumFractionDigits: 2});
};
// "420.00", "−12.50": a transaction's amount.
const finCents = (c: number | null | undefined) => c == null ? '' : (c < 0 ? '−' : '') + (Math.abs(c) / 100).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
// A difference that is good when positive: +300 in green, −940 in red, — when it rounds to nothing.
const finDiff = (d: number | null | undefined) => {
  if (d == null) return {text: '', cls: ''};
  const v = Math.round(d / 100);
  if (!v) return {text: '—', cls: 'muted'};
  return {text: (v > 0 ? '+' : '−') + Math.abs(v).toLocaleString('en-US'), cls: v > 0 ? 'fin-good' : 'fin-bad'};
};
// The group with no vendor, team or teammate (backend/finance/reads.py NONE): no real key starts with a space.
const FIN_NONE = ' unattributed';
// bot:<id>, human:<id>, a team id, or FIN_NONE.
function finWho(ref: string | null | undefined) {
  const s = String(ref || '');
  if (!s || s === FIN_NONE) return 'Unattributed';
  if (s.startsWith('bot:')) return botDisplayName(s.slice(4));
  if (s.startsWith('human:')) return personDisplay(s.slice(6));
  return s;
}
const finTeamName = (id: string | null | undefined) => ((FIN?.acc || FIN_ACCESS)?.teams || []).find(t => t.id === id)?.name
  || (S.orgGroups || []).find(g => g.id === id)?.name || id || '';
const finAvatar = (ref: string, size = 18) => String(ref || '').startsWith('bot:') ? avatar(ref.slice(4), size)
  : personAvatar((S.people || []).find(p => p.id === String(ref).slice(6)) || {name: finWho(ref)}, size);

// ----------------------------------------------------------------- shared pieces
// A segmented switch: [[value, label]], the chosen one aria-checked.
const finSeg = (name: string, label: string, options: readonly (readonly string[])[], value: string | undefined) => `<div class="theme-seg fin-seg" role="radiogroup" aria-label="${esc(label)}" data-fin-seg="${esc(name)}">${options.map(([v, l]) =>
  `<button type="button" role="radio" data-v="${esc(v)}" aria-checked="${v === value}">${esc(l)}</button>`).join('')}</div>`;
// The table and its rail: on a phone the rail stacks under the table, and the table scrolls inside its box.
const finSplit = (main: string, rail: string, label: string) => `<div class="fin-split"><div class="fin-main">${main}</div><aside class="fin-rail" id="fin-rail" aria-label="${esc(label)}">${rail}</aside></div>`;
const finKv = (k: string | undefined, v: string, cls = '') => `<div class="fin-kv"><span>${esc(k)}</span><span class="${cls}">${v}</span></div>`;
const finNote = (text: string) => `<p class="muted fin-note">${esc(text)}</p>`;
// A read that failed says what happened and carries the fix (style guide "A great page"): Try again draws the tab again.
// A refusal (403) is not fixed by trying again, so it has none.
const finFail = (e: unknown) => errorStatus(e) === 403 ? finNote('Not in your view.')
  : `<p class="muted fin-note" role="alert">${esc(errorMessage(e, 'Did not load.'))} <button type="button" class="linkish tap-mark" data-fin-retry>Try again</button></p>`;
document.addEventListener('click', ev => { if (eventElement(ev)?.closest('#main [data-fin-retry]')) route(); });

// ----------------------------------------------------------------- tables
// A column is drawn only when some row has a value (style guide, "A table of things you manage": no column that says
// the same nothing on every row; an empty cell shows nothing). Decided per table for the visit, from every row drawn
// since the tab opened: a column once shown stays, so a search or filter that narrows the rows never makes the columns
// jump. A head marked data-fin-keep always stays. Cells, rows that span several columns included, are kept in step.
const FIN_COLS_SEEN = new Map<string, Set<number>>();
const finCellHas = (cell: Element | undefined) => !!cell && (cell.textContent.trim() !== '' || !!cell.querySelector('input, select, button, [role=img]'));
// A list table becomes rows on a phone (finance.css .fin-rows): its CSS display changes, which drops a table's semantics
// in WebKit, so each one carries them as roles (table, row, columnheader, cell) and its head stays for screen readers
// (clipped, not hidden). Set as the tables reach the page, whichever tab or dialog draws them.
function finRowsRoles(table: HTMLTableElement) {
  if (table.getAttribute('role') === 'table') return;
  table.setAttribute('role', 'table');
  for (const part of table.querySelectorAll(':scope > thead, :scope > tbody')) part.setAttribute('role', 'rowgroup');
  for (const tr of table.rows) {
    tr.setAttribute('role', 'row');
    for (const c of tr.cells) c.setAttribute('role', c.tagName === 'TH' ? (c.closest('thead') ? 'columnheader' : 'rowheader') : 'cell');
  }
}
new MutationObserver(records => {
  for (const r of records) for (const n of r.addedNodes) {
    if (!(n instanceof Element)) continue;
    if (n.matches('table.fin-rows')) finRowsRoles(n as HTMLTableElement);
    n.querySelectorAll<HTMLTableElement>('table.fin-rows').forEach(finRowsRoles);
  }
}).observe(document.documentElement, {childList: true, subtree: true});
// On a phone a row's record (the rail) stacks under the list: picking a row brings it into view. Only a pick: a row
// that folds a group, or a control in a row, leaves the page where it is.
document.addEventListener('click', ev => {
  const at = eventElement(ev), tr = at?.closest<HTMLTableRowElement>('#fin-body .fin-main tbody tr');
  if (!tr || !matchMedia('(max-width:760px)').matches || at?.closest('input, select, a, [data-fin-budget], [data-fin-bud-amt]')) return;
  const attr = tr.getAttributeNames().find(n => n.startsWith('data-fin-'));
  if (!attr) return;
  const sel = `#fin-body tr.fin-on[${attr}="${CSS.escape(tr.getAttribute(attr) || '')}"]`;
  setTimeout(() => {
    const rail = $('#fin-rail') as HTMLElement | null;
    if (rail?.firstElementChild && document.querySelector(sel)) rail.scrollIntoView({block: 'start', behavior: 'smooth'});
  }, 0);
});
// A table's cells as text, the way they read (for Export).
const finTableRows = (table: Element | null | undefined) => [...(table?.querySelectorAll('tr') || [])].filter(tr => !tr.hidden)
  .map(tr => [...tr.children].flatMap(td => [td.querySelector('input')?.value ?? td.textContent.replace(/[▸▾]/g, '').trim(),
    ...Array(Math.max(0, (Number(td.getAttribute('colspan')) || 1) - 1)).fill('')]));

// ----------------------------------------------------------------- the page
function pageFinance() {
  const [path, query] = (S.route || '').split('?') as [string, string?];
  const parts = path.slice(FIN_ROUTE.length + 1).split('/').filter(Boolean);
  // A dialog belongs to the page it opened on: a Finance route change (Back, Forward, a link) closes it.
  document.querySelectorAll<HTMLDialogElement>('dialog.fin-dlg').forEach(d => d.close());
  FIN = {load: (FIN?.load || 0) + 1, tab: parts[0] || '', arg: decodeURIComponent(parts[1] || ''),
    q: new URLSearchParams(query || ''), acc: FIN_ACCESS};
  FIN_VIEW_AS = FIN.q.get('view_as') || '';
  FIN_COLS_SEEN.clear();
  const main = $('#main');
  main.classList.add('strategy-layout', 'finance-layout');
  main.innerHTML = finHeadHtml() + '<div id="fin-as"></div><div id="fin-brief"></div>' + `<div id="fin-body" class="fin-body">${skelRows(6, {dot: false})}</div>`;
  const state = FIN;
  void (async () => {
    let acc: FinAccData | null = null, error: unknown = null;
    try { acc = await get<FinAccData | null>('/v2/finance/access', {fresh: true}); } catch (e) { error = e; }
    if (FIN !== state || !$('#fin-body')) return;
    FIN_ACCESS = state.acc = Array.isArray(acc?.teammates) ? acc : null;
    finNavSync(error);
    if (!state.acc) { $('#fin-body').innerHTML = finNote(errorMessage(error, 'Finance is not set up.')); return; }
    finShow(state);
  })();
}
function finShow(state: FinPage) {
  const caps = finCaps(), acc = state.acc;
  if (!caps?.any) { finHeadDraw(); $('#fin-body').innerHTML = finNote('Nothing in Finance is in your view.'); return; }
  const tab = state.tab === 'line' ? 'pnl' : state.tab;
  if (!tab || !finTabShown(tab, caps, acc)) {
    location.replace(caps.sees ? finHref('/pnl', {view: caps.company ? '' : 'expenses'}) : caps.mine ? finHref('/mine') : finHref('/attribution'));
    return;
  }
  finHeadDraw();
  // What changed is the P&L's: on every tab it pushed the tab's own table down (style guide "A great page").
  if (state.tab === 'pnl') void finBriefLoad(state);
  if (state.tab === 'line') finLinePage(state.arg);
  else if (tab === 'pnl') finPnlPage();
  else if (tab === 'budgets') finBudgetsPage();
  else if (tab === 'attribution') finAttrPage();
  else if (tab === 'mine') finBudgetsPage(true);
  else if (tab === 'balance') finBalancePage();
  else if (tab === 'payables') finDocPage('bill');
  else if (tab === 'receivables') finDocPage('invoice');
  else if (tab === 'cards') finCardsPage();
  else if (tab === 'transactions') finTxPage();
  else if (tab === 'tax') finTaxPage();
  else if (tab === 'sources') finSourcesPage();
  else if (tab === 'access') finAccessPage();
}
// The one page header (page-head.ts): Finance, its tabs (five on the row, the rest in ⋯), then the open tab's quiet +
// (finHeadAct) and its rarer actions in ⋯ (finHeadMore). Each tab fills those two after the header is drawn.
function finHeadHtml() {
  const caps = FIN?.acc ? finCaps() : null, acc = FIN?.acc, on = FIN?.tab === 'line' ? 'pnl' : FIN?.tab;
  const tabs = caps ? FIN_TABS.filter(([id]) => finTabShown(id, caps, acc)) : [];
  // The P&L tab keeps the view it was left on (Company or Expenses); on the P&L itself, the one in the address.
  const pnlView = FIN?.tab === 'pnl' ? FIN.q?.get('view') : FIN_PNL.view;
  return pageHead({title: 'Finance', id: 'fin-head', hook: 'fin-head', tabsAttrs: 'aria-label="Finance"',
    tabs: tabs.map(([id, label]): PageTab => ({label, href: finHref('/' + id, id === 'pnl' && pnlView === 'expenses' ? {view: 'expenses'} : {}), cur: on === id, attrs: `data-fin-tab="${id}"`})),
    more: PAGE_HEAD_COPY});
}
// "Viewing as" is a state of the whole page, so it is a line under the header, not a tool that folds into ⋯ on a phone.
function finAsHtml() {
  const caps = FIN?.acc ? finCaps() : null;
  return caps?.viewing ? `<div class="fin-as" role="status"><span>Viewing as ${esc(finWho(caps.who))}</span><button type="button" class="ghost tap-mark" data-fin-as-end aria-label="Stop viewing as ${esc(finWho(caps.who))}" title="Stop viewing as ${esc(finWho(caps.who))}">✕</button></div>` : '';
}
function finHeadDraw() {
  const head = $('#fin-head'); if (!head) return;
  head.outerHTML = finHeadHtml();
  const as = $('#fin-as'); if (as) as.innerHTML = finAsHtml();
  const end = $('#fin-as [data-fin-as-end]');
  if (end) end.onclick = () => { FIN_VIEW_AS = ''; location.hash = finHref('/' + (FIN?.tab === 'line' ? 'pnl' : FIN?.tab || 'pnl')); };
  // The header's quiet + and ⋯ items act through the open tab's own click handler on #fin-body, as they did when
  // they sat in its bar (a tab's handler reads only the clicked element).
  ($('#fin-head') as HTMLElement | null)?.addEventListener('click', ev => {
    const t = eventElement(ev);
    if (!t?.closest('.ph-act, .ph-menu') || t.closest('.ph-mtabs, .ph-copy')) return;
    const body = $('#fin-body') as HTMLElement | null;
    body?.onclick?.call(body, ev);
  });
}
// A quiet + that opens a small menu of adds (the Balance sheet's Add, Sources' Connect): the shared + with its menu under it.
const finAddMenu = (label: string, attrs: string, id: string, items: string) => `<span class="fin-pop-at fin-head-pop">${pageHeadAdd(label, `${attrs} aria-haspopup="menu" aria-expanded="false" aria-controls="${id}"`)}
  <div class="fin-pop fin-menu" id="${id}" role="menu" aria-label="${esc(label)}" hidden>${items}</div></span>`;
function financeStop() { if (FIN && !rdUnder(S.route || '', FIN_ROUTE)) { FIN = null; document.querySelectorAll<HTMLDialogElement>('dialog.fin-dlg').forEach(d => d.close()); } }
