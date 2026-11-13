/* ui/app/finance-pnl.ts — Finance › P&L (spec §12): one table of actual periods, then shaded projected periods, then
   the budget and vs budget of one period (the current one, or the one picked). Company shows revenue, cost of revenue,
   gross profit, operating expenses and net income; Expenses keeps the expense lines, filtered to teams and teammates
   from the org chart (bots, people or both; Unattributed), with the filter's teammates spent vs budget in the rail. A cell opens the rail for its line and
   period; a future month's budget cell edits on a double-click (budget_update from that month on), and so does this
   month's, on a budget it has. Saving is a dry run first: an allowed change saves, one needing approval names the
   approver and is sent from a small dialog. A line opens its page: by vendor, team or teammate, last month and this
   one, each vendor's budget edited in place.
   Reads: GET /api/v2/finance/pnl, /finance/budgets, /finance/lines/{account_id}. Writes: budget_create and
   budget_update through POST /api/v2/finance/actions.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

type FinPnlApi = import('../types/api').FinancePnl;
type FinPeriod = 'day' | 'week' | 'month' | 'quarter' | 'year';
type FinPnlCol = FinPnlApi['columns'][number];
type FinFigures = (number | null)[] | null;
// A P&L line as the read sends it, plus what the page reads that the generated type leaves out.
type FinPnlRow = Omit<FinPnlApi['rows'][number], 'parent_id' | 'section' | 'actual' | 'budget' | 'projection'> & {
  parent_id?: string | null; section?: string; actual?: FinFigures; budget?: FinFigures; projection?: FinFigures;
  budget_to_date?: FinFigures; projection_method?: string;
};
type FinPnlLine = FinPnlRow & {depth: number};
interface FinPnlSection { top: FinPnlRow; lines: FinPnlLine[]; cogs: boolean }
interface FinPnlModel { sections: FinPnlSection[]; lines: FinPnlLine[] }
interface FinTeammateSpend { teammate: string; kind?: string; spent: number | null; budget: number | null }
// GET /v2/finance/pnl.
type FinPnlData = Omit<FinPnlApi, 'rows' | 'net' | 'expense' | 'excluded' | 'teammates'> & {
  rows?: FinPnlRow[]; net?: FinFigures; expense?: FinFigures; excluded?: boolean; teammates?: FinTeammateSpend[];
  imported?: {month: string; basis: string}[]; snapshot?: {month: string; restatements?: number} | null;
};
// The expenses filter: teams and teammates picked from the org chart, bots or people, Unattributed.
interface FinFilter { teams: string[]; mates: string[]; who: string; unattributed: boolean }
// The P&L's controls, kept for the visit.
interface FinPnlControls extends FinFilter {
  period: FinPeriod; basis: string; start: string; end: string; rangeOf: string; picked: boolean; compare: string;
  sel: {id: string; col: number} | null; budgetCol: number; last: FinPnlData | null; view?: string;
}
// FIN.pnl: the open P&L page (`model` is set whenever `data` is).
interface FinPnlState {
  view: string; data: FinPnlData | null; budgets: FinBudget[]; load: number; busy?: boolean; snap?: FinFilter; compare?: string;
  budgetsRead?: FinBudRead | null; model?: FinPnlModel; opened?: boolean;
}
interface FinPage { pnl?: FinPnlState }
type FinAccess = import('../types/api').FinanceAccess;
interface FinRangeControls { start: HTMLInputElement; end: HTMLInputElement }
// A form whose named controls are typed (form.elements is a collection; its fields are read by name).
type FinFormOf<C> = HTMLFormElement & {elements: HTMLFormControlsCollection & C};
const FIN_PERIODS = [['day', 'Day'], ['week', 'Week'], ['month', 'Month'], ['quarter', 'Quarter'], ['year', 'Year']];
const FIN_AT_LIMIT: Record<string, string> = {warn: 'Warn', ask: 'Ask', decline: 'Decline', hold_payment: 'Hold payment'};

// ----------------------------------------------------------------- periods
// The window a period opens on: a few periods back and a few ahead of today.
function finPnlRange(period: FinPeriod): [string, string] {
  const t = new Date(), y = t.getFullYear(), m = t.getMonth(), d = t.getDate(), mon = d - (t.getDay() + 6) % 7;
  const span = ({day: [new Date(y, m, d - 6), new Date(y, m, d + 3)], week: [new Date(y, m, mon - 35), new Date(y, m, mon + 20)],
    month: [new Date(y, m - 3, 1), new Date(y, m + 4, 0)], quarter: [new Date(y, m - m % 3 - 9, 1), new Date(y, m - m % 3 + 6, 0)],
    year: [new Date(y - 2, 0, 1), new Date(y + 1, 11, 31)]} satisfies Record<FinPeriod, [Date, Date]>)[period];
  return [finIso(span[0]), finIso(span[1])];
}
// A column's short name: Oct, Q4 2026, 2026, Oct 30 (a day, a week's first day in the range: a range can start mid-week,
// and that column only holds the days from there).
function finColLabel(col: FinPnlCol, many: boolean) {
  const p = FIN_PNL.period, d = finDate(p === 'week' && FIN_PNL.start > col.start ? FIN_PNL.start : col.start);
  if (p === 'month') return FIN_MON[d.getMonth()] + (many && (d.getMonth() === 0) ? ' ' + d.getFullYear() : '');
  if (p === 'quarter') return String(col.label).replace(/^(\d{4})-(Q\d)$/, '$2 $1');
  if (p === 'year') return String(col.label);
  return `${FIN_MON[d.getMonth()]} ${d.getDate()}`;
}
const finColLong = (col: FinPnlCol) => FIN_PNL.period === 'month' ? FIN_MONTH[finDate(col.start).getMonth()] : finColLabel(col, true);
const finRangeLabel = (a: string, b: string) => {
  const x = finDate(a), y = finDate(b), day = (t: Date) => `${FIN_MON[t.getMonth()]} ${t.getDate()}`;
  if (FIN_PNL.period === 'day' || FIN_PNL.period === 'week') {
    return x.getFullYear() === y.getFullYear() ? `${day(x)} – ${day(y)}, ${y.getFullYear()}` : `${day(x)}, ${x.getFullYear()} – ${day(y)}, ${y.getFullYear()}`;
  }
  return `${FIN_MON[x.getMonth()]} ${x.getFullYear()} – ${FIN_MON[y.getMonth()]} ${y.getFullYear()}`;
};
// The budget versions to compare with: today's, or as they stood on the first of this month, quarter or year.
function finCompareOptions() {
  const t = new Date(), y = t.getFullYear(), m = t.getMonth();
  return [['', 'Budget'], [finIso(new Date(y, m, 1)), ''], [finIso(new Date(y, m - m % 3, 1)), ''], [finIso(new Date(y, 0, 1)), '']]
    .filter(([v], i, all) => all.findIndex(([w]) => w === v) === i)
    .map(([v, l]) => [v, l || 'Budget on ' + finDayLabel(v)]);
}

// ----------------------------------------------------------------- the page
function finPnlPage() {
  const caps = finNeed(finCaps(), 'the finance caps'), p = FIN_PNL;
  if (!FIN) return;
  const view = FIN.q.get('view') === 'expenses' || !caps.company ? 'expenses' : 'company';
  // A range the person chose stays through a change of period; until then each period opens on its own window.
  if (!p.start || (!p.picked && p.rangeOf !== p.period)) { [p.start, p.end] = finPnlRange(p.period); p.rangeOf = p.period; }
  FIN.pnl = {view, data: null, budgets: [], load: 0}; p.view = view;
  const views = caps.company ? [['company', 'Company'], ['expenses', 'Expenses']] : [['expenses', 'Expenses']];
  const filters = view === 'expenses' ? `<div class="fin-filters" id="fin-filters"></div>` : '';
  $('#fin-body').innerHTML = `<div class="fin-bar">${finSeg('view', 'View', views, view)}${finSeg('period', 'Period', FIN_PERIODS, p.period)}
      <span class="fin-pop-at"><button type="button" class="ghost" data-fin-range aria-expanded="false" aria-controls="fin-range">${esc(finRangeLabel(p.start, p.end))}</button>
        <form class="fin-pop" id="fin-range" hidden><label>From<input type="date" name="start" value="${p.start}" required></label>
          <label>To<input type="date" name="end" value="${p.end}" required></label><button type="submit" class="primary">Apply</button></form></span>
      ${finSeg('basis', 'Basis', [['accrual', 'Accrual'], ['cash', 'Cash']], p.basis)}<span id="fin-pl-marks" class="fin-marks"></span>
      ${view === 'company' ? `<label class="fin-inline"><span class="muted">Compare</span><span class="fin-sel"><span class="fin-sel-v" aria-hidden="true">${esc((finCompareOptions().find(([v]) => v === p.compare) || finCompareOptions()[0])?.[1] || '')}</span><select data-fin-compare>${finCompareOptions().map(([v, l]) =>
        `<option value="${v}"${v === p.compare ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select></span></label>` : ''}
      <span class="spacer"></span><button type="button" class="ghost" data-fin-export>Export</button></div>
    ${filters}` + finSplit(view === 'expenses'
      // The org-chart picker: a column beside the table on a wide screen, "+ Filter" on a phone (finance.css).
      ? `<div class="fin-exp"><div class="fin-tree fin-tree-col" id="fin-tree-col" role="group" aria-label="Filter"></div><div class="fin-scroll" id="fin-pl">${skelRows(8, {dot: false})}</div></div>`
      : `<div class="fin-scroll" id="fin-pl">${skelRows(8, {dot: false})}</div>`, '', view === 'company' ? 'Selected cell' : 'Breakdown')
    + '<section class="fin-subs" id="fin-subs" aria-label="Subscriptions" hidden></section>';
  const body = finNeed<HTMLElement>($('#fin-body'), 'the Finance body');
  body.onclick = ev => finPnlClick(ev);
  body.ondblclick = ev => { const td = eventElement(ev)?.closest<HTMLElement>('td[data-fin-budget]'); if (td) finPnlEdit(td); };
  body.onkeydown = ev => {
    const td = eventElement(ev)?.closest<HTMLElement>('td[data-fin-budget]');
    if (td && ev.key === 'Enter' && !td.querySelector('input')) { ev.preventDefault(); finPnlEdit(td); }
  };
  $('#fin-range').onsubmit = (ev: SubmitEvent) => {
    ev.preventDefault();
    const f = ev.target as FinFormOf<FinRangeControls>, a = f.elements.start.value, z = f.elements.end.value;
    if (!a || !z || z < a) return;
    p.start = a; p.end = z; p.picked = true; p.sel = null; p.budgetCol = -1;
    finPnlPage();
  };
  body.querySelector('[data-fin-compare]')?.addEventListener('change', ev => {
    const pick = ev.target as HTMLSelectElement;
    p.compare = pick.value;
    const shown = pick.parentElement?.querySelector('.fin-sel-v'); if (shown) shown.textContent = pick.selectedOptions[0]?.textContent || '';
    void finPnlLoad();
  });
  if (view === 'expenses') finFiltersDraw();
  void finPnlLoad();
  void finSubsLoad();
}
function finPnlClick(ev: Event) {
  const p = FIN_PNL, t = eventElement(ev);
  if (!t) return;
  const seg = t.closest<HTMLElement>('[data-fin-seg] button');
  if (seg) {
    const name = seg.parentElement?.dataset.finSeg, v = seg.dataset.v as string;
    if (name === 'view') { location.hash = finHref('/pnl', {view: v === 'expenses' ? 'expenses' : ''}); return; }
    if (name === 'period' && v !== p.period) { p.period = v as FinPeriod; p.sel = null; p.budgetCol = -1; finPnlPage(); return; }
    if (name === 'basis' && v !== p.basis) { p.basis = v; finSegSet(seg); void finPnlLoad(); return; }
    if (name === 'who' && v !== p.who) { p.who = v; p.sel = null; finFiltersDraw(); void finPnlLoad(); return; }
    return;
  }
  const range = t.closest<HTMLElement>('[data-fin-range]');
  if (range) { finPop(range, $('#fin-range')); return; }
  const filter = t.closest<HTMLElement>('[data-fin-filter]');
  if (filter) { finPop(filter, $('#fin-tree')); return; }
  const clear = t.closest<HTMLElement>('[data-fin-clear]');
  if (clear) { finPickSet(clear.dataset.finClear, false); p.sel = null; finFiltersDraw(); void finPnlLoad(); return; }
  if (t.closest('[data-fin-export]')) { finPnlExport(); return; }
  const cell = t.closest<HTMLElement>('td[data-fin-cell]');
  if (cell && !t.closest('a')) {
    const [id, col] = (cell.dataset.finCell || '').split('|');
    p.sel = {id: finNeed(id, 'the cell line'), col: Number(col)}; p.budgetCol = Number(col);
    finPnlDraw(); finPnlRail();
  }
}
const finSegSet = (btn: HTMLElement) => btn.parentElement?.querySelectorAll('button').forEach(b => b.setAttribute('aria-checked', String(b === btn)));
// A popover under its button; a click outside or Escape closes it.
function finPop(btn: HTMLElement, pop: HTMLElement | null) {
  if (!pop) return;
  const open = pop.hidden;
  document.querySelectorAll<HTMLElement>('.fin-pop').forEach(x => { x.hidden = true; });
  document.querySelectorAll('[aria-controls]').forEach(b => b.getAttribute('aria-expanded') && b.setAttribute('aria-expanded', 'false'));
  pop.hidden = !open; btn.setAttribute('aria-expanded', String(open));
  if (!open) return;
  pop.querySelector<HTMLElement>('input,button')?.focus();
  const away = (ev: Event) => {
    const t = eventElement(ev);
    if (ev.type === 'keydown' ? (ev as KeyboardEvent).key !== 'Escape' : pop.contains(t) || btn.contains(t)) return;
    pop.hidden = true; btn.setAttribute('aria-expanded', 'false');
    document.removeEventListener('pointerdown', away, true); document.removeEventListener('keydown', away, true);
  };
  document.addEventListener('pointerdown', away, true); document.addEventListener('keydown', away, true);
}

async function finPnlLoad() {
  const state: FinPnlState = finNeed(FIN?.pnl, 'the P&L page'), p = FIN_PNL, caps = finNeed(finCaps(), 'the finance caps'), load = ++state.load;
  const qs = new URLSearchParams({start: p.start, end: p.end, period: p.period, basis: p.basis, view: state.view});
  const compare = state.view === 'company' ? p.compare : '';
  if (compare) qs.set('budget_as_of', compare);
  if (!caps.companyExpenses) p.unattributed = false;
  const snap = state.view === 'expenses' ? finFilterSnap(p) : finFilterSnap({teams: [], mates: [], who: 'both', unattributed: false});
  for (const [k, v] of finFilterQs(snap)) qs.append(k, v);
  state.busy = true;
  $('#fin-pl')?.setAttribute('aria-busy', 'true');
  const editing = caps.may('finance.budgets');
  const [pnl, budgets] = await Promise.allSettled([get<FinPnlData>('/v2/finance/pnl?' + qs + finAs(), {fresh: true}),
    editing ? get<FinBudRead>('/v2/finance/budgets' + finAs('?'), {fresh: true}) : Promise.resolve(null)]);
  if (!FIN || FIN.pnl !== state || state.load !== load || !$('#fin-pl')) return;
  state.busy = false;
  $('#fin-pl').removeAttribute('aria-busy');
  if (pnl.status === 'rejected') { state.data = null; $('#fin-pl').innerHTML = finFail(pnl.reason); finRail(''); return; }
  state.data = p.last = pnl.value;
  state.snap = snap; state.compare = compare;
  state.budgets = budgets.status === 'fulfilled' ? budgets.value?.budgets || [] : [];
  state.budgetsRead = budgets.status === 'fulfilled' ? budgets.value : null;
  state.model = finPnlModel(state.data);
  const cols = state.data.columns || [];
  if (!(p.budgetCol >= 0 && p.budgetCol < cols.length)) {
    const cur = cols.findIndex(c => c.state === 'current'), next = cols.findIndex(c => c.state === 'future');
    p.budgetCol = cur >= 0 ? cur : next >= 0 ? next : cols.length - 1;
  }
  const lines = state.model.lines;
  if (!p.sel || !lines.some(r => r.account_id === p.sel?.id) || p.sel.col >= cols.length) {
    const first = lines.find(r => r.type === 'expense' && r.budget?.[p.budgetCol] != null) || lines.find(r => r.type === 'expense') || lines[0];
    p.sel = first ? {id: first.account_id, col: p.budgetCol} : null;
  }
  finPnlDraw();
  finPnlRail();
  finPnlOpenAt();
}

// ----------------------------------------------------------------- the statement
// Sections from the chart: each top line (no parent shown) with the lines under it in code order. Cost of revenue is
// the section the route says (`section`), so gross profit can sit under it.
function finPnlModel(data: FinPnlData): FinPnlModel {
  const rows = data.rows || [], ids = new Set(rows.map(r => r.account_id)), kids = new Map<string, FinPnlRow[]>();
  for (const r of rows) if (r.parent_id && ids.has(r.parent_id)) (kids.get(r.parent_id) || finNeed(kids.set(r.parent_id, []).get(r.parent_id), 'the group')).push(r);
  const byCode = (a: FinPnlRow, b: FinPnlRow) => String(a.code).localeCompare(String(b.code));
  const tops = rows.filter(r => !r.parent_id || !ids.has(r.parent_id)).sort((a, b) =>
    (a.type === 'income' ? 0 : 1) - (b.type === 'income' ? 0 : 1) || byCode(a, b));
  const own = (r: FinPnlRow) => r.private || [r.actual, r.budget, r.projection].some(v => (v || []).some(x => x));
  const sections: FinPnlSection[] = [], lines: FinPnlLine[] = [];
  for (const top of tops) {
    const sec: FinPnlSection = {top, lines: [], cogs: top.section ? top.section === 'cost_of_revenue' : top.type === 'expense' && String(top.code).startsWith('5')};
    const walk = (r: FinPnlRow, depth: number): void => { sec.lines.push({...r, depth}); for (const k of (kids.get(r.account_id) || []).sort(byCode)) walk(k, depth + 1); };
    const under = (kids.get(top.account_id) || []).sort(byCode);
    if (!under.length || own(top)) sec.lines.push({...top, depth: 0});
    for (const k of under) walk(k, 0);
    sections.push(sec); lines.push(...sec.lines);
  }
  return {sections, lines};
}
// A line's figure in a column: the actual for past and current periods, the projection for future ones.
const finVal = (r: FinPnlRow, cols: FinPnlCol[], i: number) => cols[i]?.state === 'future' ? r.projection?.[i] ?? null : r.actual?.[i] ?? null;
// A line's budget in a column, for totals: a private line's is left out, as its spend is (so totals compare like
// with like).
const finBudgetOf = (i: number) => (r: FinPnlRow) => r.private ? null : r.budget?.[i] ?? null;
const finToDateOf = (i: number) => (r: FinPnlRow) => r.private ? null : r.budget_to_date?.[i] ?? null;
// Sums over the lines shown (never over what the route left out); null when none has a figure.
function finSum<R>(rows: R[], pick: (r: R) => number | null) {
  let any = false, total = 0;
  for (const r of rows) { const v = pick(r); if (v != null) { any = true; total += v; } }
  return any ? total : null;
}
// One side of a difference (gross profit, net income): a side with no lines is 0; a side with lines but no figure for
// the column is unknown, and so is the difference.
const finSide = <R>(rows: R[], f: (r: R) => number | null) => rows.length ? finSum(rows, f) : 0;
const finLess = (a: number | null, b: number | null) => a == null || b == null ? null : a - b;
function finPnlDraw() {
  const state = FIN?.pnl, box = $('#fin-pl'); if (!box || !state?.data) return;
  const data = state.data, cols = data.columns || [], p = FIN_PNL, bi = p.budgetCol, caps = finNeed(finCaps(), 'the finance caps');
  if (!state.model) return;
  const {sections} = state.model;
  if (!sections.length) { box.innerHTML = finNote(state.view !== 'expenses' ? 'No lines yet.' : finFilterAny(state.snap || p) ? 'No expenses in this filter.' : 'No expenses.'); return; }
  const years = new Set(cols.map(c => c.start.slice(0, 4))).size > 1;
  const past = cols.filter(c => c.state !== 'future').length, cur = cols.find(c => c.state === 'current');
  const imported = finImportedCols(data, cols);
  const head = cols.map((c, i) => `<th class="${c.state === 'future' ? 'fin-p' : ''}${i === past && c.state === 'future' ? ' fin-b' : ''}">${esc(finColLabel(c, years))}${imported[i] ? `<span class="fin-tag" title="${esc(imported[i])}">Imported</span>` : ''}</th>`).join('');
  finMarksDraw($('#fin-pl-marks'), data);
  const bcol = cols[bi];
  // The current month's budget to date, next to the full month's (§4.4).
  const showToDate = (data.rows || []).some(r => finToDateOf(bi)(r) != null);
  const toDateCell = (v: number | null) => showToDate ? `<td class="muted">${finNum(v)}</td>` : '';
  const sub = `<tr class="fin-sub-head"><th></th>${past ? `<th colspan="${past}"><span>${cur ? 'Actual · ' + esc(finColLabel(cur, years)) + ' to date' : 'Actual'}</span></th>` : ''}${cols.length - past
    ? `<th class="fin-p fin-b" colspan="${cols.length - past}"><span>Projected</span></th>` : ''}<th class="fin-b" colspan="${showToDate ? 3 : 2}"></th></tr>`;
  const sign = (r: FinPnlRow) => r.type === 'income' ? 1 : -1;
  const fq = finFilterQs(state.snap).toString(), lineQs = fq ? '?' + fq : '';
  // A narrow viewer's totals leave payroll out (§7.5): they say so.
  const excl = finExcl(data), hasPrivate = (rows: FinPnlRow[]) => rows.some(r => r.private);
  // Compare shows the plan as it stood on a past day: not the live budget, so not editable.
  const comparing = state.view === 'company' && !!state.compare;
  const editable = (r: FinPnlRow) => !caps.viewing && !comparing && caps.may('finance.budgets') && p.period === 'month'
    && r.type === 'expense' && !r.private && finBudgetEditable(bcol, finBudgetFor(r.account_id));
  const nums = (vals: (number | null)[], budget: number | null, actualB: number | null | undefined, good: number, toDate: number | null = null) => {
    const diff = budget == null || actualB == null ? null : good * (actualB - budget);
    const d = finDiff(diff);
    return vals.map((v, i) => `<td class="${cols[i]?.state === 'future' ? 'fin-p' : ''}${i === past && cols[i]?.state === 'future' ? ' fin-b' : ''}">${finNum(v)}</td>`).join('')
      + `<td class="fin-b">${finNum(budget)}</td>${toDateCell(toDate)}<td class="${d.cls}">${d.text}</td>`;
  };
  const line = (r: FinPnlLine) => {
    const vals = cols.map((c, i) => finVal(r, cols, i));
    const drill = !r.private && !caps.viewing;
    const name = drill ? `<a href="${FIN_ROUTE}/line/${encodeURIComponent(r.account_id)}${lineQs}">${esc(r.name)} ▸</a>` : `<span${r.private ? ' class="muted"' : ''}>${esc(r.name)}</span>`;
    const budget = r.private ? null : r.budget?.[bi] ?? null, actualB = finVal(r, cols, bi);
    const diff = finDiff(budget == null || actualB == null ? null : sign(r) * (actualB - budget));
    const sel = p.sel?.id === r.account_id ? p.sel.col : -1;
    const edit = editable(r);
    const how = FIN_PROJ[r.projection_method ?? ''] ? ` title="${FIN_PROJ[r.projection_method ?? '']}"` : '';
    const cells = vals.map((v, i) => `<td data-fin-cell="${esc(r.account_id)}|${i}"${cols[i]?.state === 'future' ? how : ''} class="${cols[i]?.state === 'future' ? 'fin-p' : ''}${i === past && cols[i]?.state === 'future' ? ' fin-b' : ''}${sel === i ? ' fin-on' : ''}">${r.private && v == null ? '<span class="muted">—</span>' : finNum(v)}</td>`).join('');
    return `<tr class="fin-ln" style="--d:${r.depth}"><td>${name}</td>${cells}<td class="fin-b${edit ? ' fin-edit' : ''}"${edit
      ? ` data-fin-budget="${esc(r.account_id)}" tabindex="0" aria-label="${esc(r.name)} budget, ${esc(finColLong(finNeed(bcol, 'the budget column')))}"` : ''}>${r.private ? '<span class="muted">—</span>' : finNum(budget)}</td>${toDateCell(finToDateOf(bi)(r))}<td class="${diff.cls}">${diff.text}</td></tr>`;
  };
  const total = (label: string, rows: FinPnlLine[], cls: string, good: number) => {
    const vals = cols.map((c, i) => finSum(rows, r => r.private ? null : finVal(r, cols, i)));
    const budget = finSum(rows, finBudgetOf(bi));
    return `<tr class="${cls}"><td>${esc(label)}</td>${nums(vals, budget, vals[bi], good, finSum(rows, finToDateOf(bi)))}</tr>`;
  };
  const out: string[] = [];
  const income = sections.filter(s => s.top.type === 'income').flatMap(s => s.lines);
  const cogs = sections.filter(s => s.cogs).flatMap(s => s.lines);
  for (const [n, s] of sections.entries()) {
    out.push(`<tr class="fin-sec"><td>${esc(s.top.name)}</td><td colspan="${cols.length + (showToDate ? 3 : 2)}"></td></tr>`);
    out.push(...s.lines.map(line));
    out.push(total('Total ' + s.top.name.toLowerCase() + (hasPrivate(s.lines) ? excl : ''), s.lines, 'fin-subt', s.top.type === 'income' ? 1 : -1));
    const lastCogs = s.cogs && !sections.slice(n + 1).some(x => x.cogs);
    if (state.view === 'company' && lastCogs && income.length) {
      const gp = (i: number) => finLess(finSide(income, r => r.private ? null : finVal(r, cols, i)), finSide(cogs, r => r.private ? null : finVal(r, cols, i)));
      const gpB = finLess(finSide(income, finBudgetOf(bi)), finSide(cogs, finBudgetOf(bi)));
      const gv = cols.map((c, i) => gp(i));
      out.push(`<tr class="fin-subt"><td>Gross profit${hasPrivate([...income, ...cogs]) ? excl : ''}</td>${nums(gv, gpB, gv[bi], 1, finLess(finSide(income, finToDateOf(bi)), finSide(cogs, finToDateOf(bi))))}</tr>`);
      const pct = (i: number) => { if (gv[i] == null) return null; const rev = finSum(income, r => r.private ? null : finVal(r, cols, i)); return rev ? Math.round(gv[i] / rev * 100) : null; };
      const pctB = (() => { const rev = finSum(income, finBudgetOf(bi)); return rev && gpB != null ? Math.round(gpB / rev * 100) : null; })();
      const pc = pct(bi), pd = pctB == null || pc == null ? null : pc - pctB;
      out.push(`<tr class="fin-pct"><td>Gross margin</td>${cols.map((c, i) => `<td class="${c.state === 'future' ? 'fin-p' : ''}${i === past && c.state === 'future' ? ' fin-b' : ''}">${pct(i) == null ? '' : pct(i) + '%'}</td>`).join('')}<td class="fin-b">${pctB == null ? '' : pctB + '%'}</td>${toDateCell(null)}<td class="${pd == null ? '' : pd > 0 ? 'fin-good' : pd < 0 ? 'fin-bad' : 'muted'}">${pd == null ? '' : pd ? (pd > 0 ? '+' : '−') + Math.abs(pd) + ' pts' : '—'}</td></tr>`);
    }
  }
  // The bottom line: the route's own totals for actual periods, the lines' projections for future ones.
  const allLines = sections.flatMap(s => s.lines);
  if (state.view === 'company') {
    const net = cols.map((c, i) => {
      if (c.state !== 'future') return data.net?.[i] ?? null;
      return finLess(finSide(income, r => r.private ? null : finVal(r, cols, i)), finSide(allLines.filter(r => r.type === 'expense'), r => r.private ? null : finVal(r, cols, i)));
    });
    const netB = finLess(finSide(income, finBudgetOf(bi)), finSide(allLines.filter(r => r.type === 'expense'), finBudgetOf(bi)));
    out.push(`<tr class="fin-tot"><td>Net income${excl}</td>${nums(net, netB, net[bi], 1, finLess(finSide(income, finToDateOf(bi)), finSide(allLines.filter(r => r.type === 'expense'), finToDateOf(bi))))}</tr>`);
  } else {
    const spent = cols.map((c, i) => c.state === 'future' ? finSum(allLines, r => r.private ? null : finVal(r, cols, i)) : data.expense?.[i] ?? null);
    out.push(`<tr class="fin-tot"><td>${esc(`Total expenses${excl}${finSliceLabel(state)}`)}</td>${nums(spent, finSum(allLines, finBudgetOf(bi)), spent[bi], -1, finSum(allLines, finToDateOf(bi)))}</tr>`);
  }
  const left = box.scrollLeft;
  box.innerHTML = `<table class="fin-pl"><thead><tr><th class="fin-first"></th>${head}<th class="fin-b">${esc(bcol ? finColLabel(bcol, years) : '')} budget${finExcl(state.budgetsRead)}</th>${showToDate ? '<th>To date</th>' : ''}<th>vs budget</th></tr>${sub}</thead><tbody>${out.join('')}</tbody></table>`;
  box.style.setProperty('--fin-first', `${box.querySelector('th')?.offsetWidth || 0}px`);
  box.scrollLeft = left;   // a redraw stays where it was scrolled
}
// Wider than the page, the table opens on the newest actual (once the rail is drawn, which narrows it).
function finPnlOpenAt() {
  const state: FinPnlState = finNeed(FIN?.pnl, 'the P&L page'), box = $('#fin-pl'), cols = state.data?.columns || [];
  if (state.opened || !box?.querySelector('table')) return;
  state.opened = true;
  const th = box.querySelectorAll('thead tr:first-child th')[cols.filter(c => c.state !== 'future').length];
  if (th && th !== box.querySelector('th')) box.scrollLeft = th.offsetLeft + th.offsetWidth - box.clientWidth;
}
// The budgets a P&L cell sums, the way the route picks them (backend/finance/reads.py _top_budgets, _filter_budgets):
// unfiltered, the top budgets the viewer sees; filtered to a team, those of that team and the teams under it; to a
// teammate, theirs. Never a parent and its child both.
function finScopeBudgets(accountId: string) {
  const state: FinPnlState | undefined = FIN?.pnl, p = state?.snap || finFilterSnap({teams: [], mates: [], who: 'both', unattributed: false});
  const all = (state?.budgets || []).filter(b => b.active && !b.catch_all);
  let picked = all;
  if (state?.view === 'expenses' && finFilterAny(p)) {
    // backend/finance/reads.py _Filter.keeps_budget
    const below = p.teams.length ? new Set(p.teams.flatMap(t => [...finTeamsBelow(t)])) : null;
    const chosen = !!(p.teams.length || p.mates.length || p.unattributed);
    const kind = (m: string) => p.who === 'both' || m.startsWith(p.who === 'bots' ? 'bot:' : 'human:');
    picked = all.filter(b => {
      const inTeams = !!below && !!b.team && below.has(b.team);
      return b.teammate ? kind(b.teammate) && (!chosen || p.mates.includes(b.teammate) || inTeams) : p.who === 'both' && inTeams;
    });
  }
  const ids = new Set(picked.map(b => b.id));
  return picked.filter(b => b.account_id === accountId && !(b.parent_id && ids.has(b.parent_id)));
}
// What a total covers when it isn't the company's (§7.5): the filter's names, or the viewer's own slice.
function finSliceLabel(state: FinPnlState) {
  const p = state?.snap, caps = finCaps(), teams: FinAccess['teams'] = (FIN?.acc || FIN_ACCESS)?.teams || [];
  if (state?.view === 'expenses' && p && finFilterAny(p)) {
    const names = [...p.teams.map(id => teams.find(t => t.id === id)?.name || id), ...p.mates.map(finWho),
      ...(p.unattributed ? ['Unattributed'] : []), ...(p.who !== 'both' ? [p.who === 'bots' ? 'Bots' : 'People'] : [])];
    return ' · ' + (names.length > 2 ? `${names.length} selected` : names.join(', '));
  }
  return caps && !caps.company && !caps.companyExpenses ? finOwnSlice(caps) : '';
}
const finFiltered = () => FIN?.pnl?.view === 'expenses' && !!FIN.pnl.snap && finFilterAny(FIN.pnl.snap);
// A team and every team under it.
function finTeamsBelow(id: string) {
  const teams: FinAccess['teams'] = (FIN?.acc || FIN_ACCESS)?.teams || [], out = new Set([id]);
  for (let grew = true; grew;) { grew = false; for (const t of teams) if (t.parent && out.has(t.parent) && !out.has(t.id)) { out.add(t.id); grew = true; } }
  return out;
}
// The budget a line's month cell edits: the one budget the cell shows (null: none yet, so a new line budget, but only
// unfiltered, since a team's or teammate's budget sits under the line's); false when the cell sums several budgets or
// shows one kept by quarter or year, which a month's cell cannot say.
function finBudgetFor(accountId: string) {
  const tops = finScopeBudgets(accountId);
  if (!tops.length) return finFiltered() ? false : null;
  if (tops.length > 1 || tops[0]?.period !== 'month') return false;
  return tops[0];
}
// Which month's cell takes an edit: any future month's, a budget or a new one; this month's on a budget it has (§4.4: a
// raise goes to the budget's owner, a cut takes the ordinary rules; the dry run says which); never a past month's.
const finBudgetEditable = (col: FinPnlCol | undefined, b: FinBudget | false | null | undefined): col is FinPnlCol => b !== false && (col?.state === 'future' || (col?.state === 'current' && !!b));
// A budget edit in place, dry run first (§12): an allowed one saves and says no more, a refused one says why, and one
// that needs approval shows the change and whom it goes to, and goes when sent. `alive` says the page is still there.
interface FinDecideSteps { title: string; change: string; alive: () => boolean; saved: () => void; undo: () => void }
async function finBudgetDecide(kind: string, payload: object, {title, change, alive, saved, undo}: FinDecideSteps) {
  let r;
  try { r = await finDecide(kind, payload); }
  catch (e) { toast(errorMessage(e, 'Not done'), true); if (alive()) undo(); return; }
  if (r.ask == null) { if (r.status === 'committed') toast('Saved'); if (alive()) saved(); return; }
  if (!alive()) return;
  let sent = false;
  finDialog(title, `${change}<p class="fin-ask">${esc(finAskText(r.ask))}</p>${finImpactHtml(r.impact)}`, 'Send', async () => {
    await finDecide(kind, payload, r.sig);
    sent = true;
    if (alive()) saved();
  }).addEventListener('close', () => { if (!sent && alive()) undo(); });
}
const finBudgetChange = (month: string | undefined, was: number | null, cents: number) => finKv(month, `${was == null ? '' : finUsd(was) + ' → '}${finUsd(cents)}`);
// A line's own budget (the root of its tree: no team, teammate or vendor), which vendor budgets sit under.
function finLineBudget(accountId: string, budgets: FinBudget[] | undefined) {
  const live = (budgets || []).filter(b => b.active && !b.catch_all), ids = new Set(live.map(b => b.id));
  return live.find(b => b.account_id === accountId && !b.team && !b.teammate && !b.vendor && !(b.parent_id && ids.has(b.parent_id))) || null;
}
// Cents as the field shows them: 450, 450.50.
const finCentsField = (c: number | null) => c == null ? '' : c % 100 ? (c / 100).toFixed(2) : String(c / 100);
function finPnlEdit(td: HTMLElement) {
  if (td.querySelector('input')) return;
  const id = td.dataset.finBudget || '', state: FinPnlState = finNeed(FIN?.pnl, 'the P&L page'), cols = finNeed(state.data, 'the P&L').columns, col = cols[FIN_PNL.budgetCol];
  const row = finNeed(state.model, 'the P&L').lines.find(r => r.account_id === id);
  // The budget this cell shows, fixed now: a filter change while typing reloads the table and the edit is dropped.
  const data = state.data, b = finBudgetFor(id);
  if (state.busy || state.compare || !row || !finBudgetEditable(col, b)) return;
  const was = row.budget?.[FIN_PNL.budgetCol] ?? null;
  td.innerHTML = `<input class="fin-cell-in" inputmode="decimal" autocomplete="off" value="${finCentsField(was)}" aria-label="${esc(row.name)} budget, ${esc(finColLong(col))}">`;
  const input = finNeed(td.querySelector('input'), 'the amount input');
  input.focus(); input.select();
  let done = false;
  const finish = async (save: boolean) => {
    if (done) return; done = true;
    const cents = finParseCents(input.value);
    if (!save || cents == null || cents === was || state.busy || state.data !== data) { finPnlDraw(); return; }
    input.disabled = true;
    const [kind, payload] = b ? ['budget_update', {budget_id: b.id, amount: cents, effective_from: col.start}]
      : ['budget_create', {name: row.name, period: 'month', account_id: id, amount: cents, effective_from: col.start}];
    await finBudgetDecide(kind, payload, {title: row.name, change: finBudgetChange(finColLong(col), was, cents), alive: () => FIN?.pnl === state,
      saved: () => void finPnlLoad(), undo: finPnlDraw});
  };
  input.onkeydown = ev => {
    if (ev.key === 'Enter') { ev.preventDefault(); void finish(true); }
    if (ev.key === 'Escape') { ev.preventDefault(); void finish(false); }
  };
  input.onblur = () => void finish(true);
}
function finPnlExport() {
  const p = FIN_PNL, view = FIN?.pnl?.view || 'company';
  finCsv(finTableRows($('#fin-pl table')), `pnl-${view}-${p.period}-${p.start}-${p.end}.csv`);
}

// ----------------------------------------------------------------- the rail
// How a projection was made (the read's projection_method), as a small label.
const FIN_PROJ: Record<string, string> = {trend: 'Trend', run_rate: 'Run rate', recurring: 'Recurring', fixed: 'Fixed', manual: 'Manual', mixed: 'Mixed'};
// A budget a card draws on holds only as tight as the card's limit (§8.2).
const finCardLimitsTag = (b: (FinBudget & {card_limits?: unknown}) | {card_limits?: unknown} | null | undefined) => b?.card_limits ? ' <span class="fin-tag">Enforced by card limits</span>' : '';
const finProjTag = (m: string | undefined) => FIN_PROJ[m ?? ''] ? ` <span class="fin-tag">${FIN_PROJ[m ?? '']}</span>` : '';
// Columns holding a month a spreadsheet's history was imported into on the other basis (the read's `imported`, shown
// only to those who see everything): each such column's months and basis, for its "Imported" label.
function finImportedCols(data: FinPnlData, cols: FinPnlCol[]) {
  const months = data.imported || [];
  return cols.map(c => {
    const a = String(c.start || '').slice(0, 7), z = String(c.end || c.start || '').slice(0, 7);
    const hit = months.filter(m => m.month >= a && m.month <= z);
    return hit.length ? hit.map(m => `${m.month} · ${m.basis === 'cash' ? 'Cash' : 'Accrual'}`).join(', ') : '';
  });
}
// A closed month's snapshot (a window inside one closed month) and how many restatements it has taken since.
function finMarksDraw(box: HTMLElement | null, data: FinPnlData | FinBsData | null) {
  if (!box) return;
  const snap = data?.snapshot;
  box.innerHTML = snap ? `<span class="pill ok">Closed</span>${snap.restatements ? `<span class="pill waiting" title="${esc(snap.month)}">Restated · ${snap.restatements}</span>` : ''}` : '';
}
async function finPnlRail() {
  const state = FIN?.pnl, p = FIN_PNL; if (!state?.data) return;
  const cols = state.data.columns || [], caps = finNeed(finCaps(), 'the finance caps');
  const row = p.sel ? finNeed(state.model, 'the P&L').lines.find(r => r.account_id === p.sel?.id) : null;
  let html = '';
  if (row) {
    const i = finNeed(p.sel, 'the selected cell').col, col = finNeed(cols[i], 'the selected column'), future = col.state === 'future';
    const v = finVal(row, cols, i), budget = row.budget?.[i] ?? null, fb = finBudgetFor(row.account_id) || undefined;
    const sec = finNeed(state.model, 'the P&L').sections.find(s => s.lines.some(r => r.account_id === row.account_id));
    const over = v == null || budget == null ? null : (row.type === 'income' ? budget - v : v - budget);
    const nextAt = cols.findIndex((c, k) => k > i && c.state === 'future');
    html += `${sec && sec.top.account_id !== row.account_id ? `<div class="muted fin-sup">${esc(sec.top.name)}</div>` : ''}
      <h2>${esc(row.name)} · ${esc(finColLong(col))}</h2>
      ${finKv(future ? 'Projected' : 'Actual', finUsd(v) + (future ? finProjTag(row.projection_method) : ''))}${finKv('Budget' + finExcl(fb?.state), finUsd(budget) + finCardLimitsTag(fb))}${row.budget_to_date?.[i] != null ? finKv('Budget to date', finUsd(row.budget_to_date[i])) : ''}
      ${over == null ? '' : over > 0 ? finKv(row.type === 'income' ? 'Under' : 'Over', finUsd(over), 'fin-bad') : finKv(row.type === 'income' ? 'Over' : 'Left', finUsd(-over))}
      ${nextAt >= 0 ? finKv(finColLabel(finNeed(cols[nextAt], 'the next column'), false) + ' projected', finUsd(row.projection?.[nextAt] ?? null) + finProjTag(row.projection_method)) : ''}
      <div id="fin-by"></div>`;
  }
  if (state.view === 'expenses') html += finTeammatesHtml();
  finRail(html);
  // Who spent it: the line's teammates in that period (a read of its own, as the viewer sees them).
  if (!row || row.private || caps.viewing) return;
  const col = finNeed(cols[finNeed(p.sel, 'the selected cell').col], 'the selected column'), sel = p.sel;
  try {
    const fq = finFilterQs(state.snap).toString();
    const d = await get<FinLineRead>(`/v2/finance/lines/${encodeURIComponent(row.account_id)}?start=${col.start}&end=${col.end}&top_n=6&limit=1${fq ? '&' + fq : ''}`, {fresh: true});
    if (FIN?.pnl !== state || p.sel !== sel || !$('#fin-by')) return;
    const by = (d.by_teammate || []).filter(r => r.amount);
    $('#fin-by').innerHTML = by.length ? `<h3 class="fin-h">By teammate</h3>${finBars(by.map(r => ({label: r.key === FIN_OTHER ? r.label : finWho(r.key), amount: r.amount})))}` : '';
  } catch { /* the figures above stand on their own */ }
}
function finTeammatesHtml() {
  const state: FinPnlState = finNeed(FIN?.pnl, 'the P&L page'), p = FIN_PNL, data = finNeed(state.data, 'the P&L');
  const rows = data.teammates || [], picks = finPicked(p);
  const who = picks.length ? picks.map(finPickName).join(', ') : 'Everyone';
  return `<div class="fin-rail-sec"><div class="muted fin-sup">Expenses · ${esc(finRangeLabel(data.start, data.end))}</div><h2>${esc(who)}</h2>
    ${rows.length ? `<h3 class="fin-h">By teammate</h3><div class="fin-tm"><span></span><span class="muted">Spent</span><span class="muted">Budget</span>${rows.map(t =>
      `<span class="fin-tm-who">${finAvatar(t.teammate, 18)}<span>${esc(finWho(t.teammate))}</span></span><span>${finNum(t.spent)}</span><span class="muted">${finNum(t.budget)}</span>`).join('')}</div>` : ''}</div>`;
}

// ----------------------------------------------------------------- the expenses filter
// Teams (with the teams under them) and teammates picked together from the org chart, and Unattributed for those who
// see company-wide expenses; bots, people or both. The route does the filtering (pnl teams, teammates, who,
// unattributed).
const finPicked = (p: FinFilter) => [...p.teams.map(t => 'team:' + t), ...p.mates.map(m => 'mate:' + m), ...(p.unattributed ? ['none:'] : [])];
const finPickName = (k: string) => k.startsWith('team:') ? finTeamName(k.slice(5)) : k.startsWith('mate:') ? finWho(k.slice(5)) : 'Unattributed';
const finFilterAny = (p: FinFilter) => !!(p.teams.length || p.mates.length || p.unattributed || p.who !== 'both');
const finFilterSnap = (p: FinFilter): FinFilter => ({teams: [...p.teams], mates: [...p.mates], who: p.who, unattributed: !!p.unattributed});
// The filter as the routes take it (pnl, lines/{id}): teams, teammates, who, unattributed.
function finFilterQs(f: FinFilter | null | undefined) {
  const qs = new URLSearchParams();
  if (!f) return qs;
  for (const t of f.teams) qs.append('teams', t);
  for (const m of f.mates) qs.append('teammates', m);
  if (f.who !== 'both') qs.set('who', f.who);
  if (f.unattributed) qs.set('unattributed', 'true');
  return qs;
}
// A filter from an address (a line page opened from a filtered P&L).
const finFilterFrom = (q: URLSearchParams): FinFilter => ({teams: q.getAll('teams'), mates: q.getAll('teammates'), who: ['bots', 'people'].includes(q.get('who') ?? '') ? q.get('who') ?? 'both' : 'both',
  unattributed: q.get('unattributed') === 'true'});
function finPickSet(k: string | undefined, on: boolean) {
  const p = FIN_PNL, v = String(k || '').slice(5);
  const put = (arr: string[], x: string) => on ? (arr.includes(x) ? arr : [...arr, x]) : arr.filter(y => y !== x);
  if (!k) { p.teams = []; p.mates = []; p.unattributed = false; }
  else if (k.startsWith('team:')) p.teams = put(p.teams, v);
  else if (k.startsWith('mate:')) p.mates = put(p.mates, v);
  else p.unattributed = !!on;
}
function finChipsDraw() {
  const box = $('#fin-chips'); if (!box) return;
  box.innerHTML = finPicked(FIN_PNL).map(k => { const n = finPickName(k); return `<span class="fin-chip">${esc(n)}<button type="button" data-fin-clear="${esc(k)}" aria-label="Clear ${esc(n)}">✕</button></span>`; }).join('');
}
function finFiltersDraw() {
  const box = $('#fin-filters'); if (!box) return;
  const p = FIN_PNL, acc: FinAccess = finNeed(FIN?.acc, 'the access list'), caps = finCaps(), on = new Set(finPicked(p));
  const kind = ({bots: 'bot', people: 'person'} as Record<string, string>)[p.who];
  const teams = acc.teams || [], members = (acc.teammates || []).filter(t => !kind || t.kind === kind);
  const kids = (id: string): FinAccess['teams'] => finTeamKids(teams, id);
  const helpers = members.filter(m => finHelper(m.teammate)), staff = members.filter(m => !finHelper(m.teammate));
  const count = (id: string) => { const ids = finTeamsBelow(id); return staff.filter(m => ids.has(m.team ?? '')).length; };
  const pick = (k: string, label: string, d: number) => `<label class="fin-tree-r" style="--d:${d}"><input type="checkbox" data-fin-pick="${esc(k)}"${on.has(k) ? ' checked' : ''}>${label}</label>`;
  const mate = (m: FinAccess['teammates'][number], d: number) => pick('mate:' + m.teammate, finAvatar(m.teammate, 16) + esc(finWho(m.teammate)), d);
  const out: string[] = [`<label class="fin-tree-r"><input type="checkbox" data-fin-pick=""${!on.size ? ' checked' : ''}>Everyone</label>`];
  const walk = (team: FinAccess['teams'][number], d: number): void => {
    const n = count(team.id);
    out.push(pick('team:' + team.id, `${esc(team.name)}${n ? `<span class="muted fin-n">${n}</span>` : ''}`, d));
    for (const m of staff.filter(m => m.team === team.id)) out.push(mate(m, d + 1));
    for (const k of kids(team.id)) if (d < 12) walk(k, d + 1);
  };
  for (const t of kids('')) walk(t, 0);
  for (const m of staff.filter(m => !m.team || !teams.some(t => t.id === m.team))) out.push(mate(m, 0));
  if (helpers.length) out.push('<div class="fin-tree-h">Helpers</div>', ...helpers.map(m => mate(m, 0)));
  if (caps?.companyExpenses) out.push(pick('none:', 'Unattributed', 0));
  box.innerHTML = `<span class="fin-chips" id="fin-chips"></span><span class="fin-pop-at fin-filter-pop"><button type="button" class="ghost fin-pill" data-fin-filter aria-expanded="false" aria-controls="fin-tree">+ Filter</button>
      <div class="fin-pop fin-tree" id="fin-tree" role="group" aria-label="Filter" hidden>${out.join('')}</div></span>
    ${finSeg('who', 'Who', [['both', 'Bots + people'], ['bots', 'Bots'], ['people', 'People']], p.who)}`;
  const col = $('#fin-tree-col'); if (col) col.innerHTML = out.join('');
  finChipsDraw();
  const trees: HTMLElement[] = [box.querySelector('#fin-tree'), col].filter(Boolean);
  const change = (ev: Event) => {
    const picked = ev.target as HTMLInputElement, k = picked.dataset.finPick; if (k == null) return;
    finPickSet(k, picked.checked);
    const now = new Set(finPicked(p));
    for (const t of trees) t.querySelectorAll<HTMLInputElement>('[data-fin-pick]').forEach(x => { x.checked = x.dataset.finPick ? now.has(x.dataset.finPick) : !now.size; });
    p.sel = null; finChipsDraw(); void finPnlLoad();
  };
  for (const t of trees) t.onchange = change;
}

// ----------------------------------------------------------------- a line's page
type FinLineBy = 'vendor' | 'team' | 'teammate';
interface FinLineShare { key: string; label: string; amount: number | null }
interface FinLineEntry { date: string; amount: number; tags?: {team?: string; teammate?: string} }
// GET /v2/finance/lines/{account_id}: a line's spend by vendor, team and teammate, with next month's projection.
interface FinLineRead {
  account?: {name?: string}; by_vendor?: FinLineShare[]; by_team?: FinLineShare[]; by_teammate?: FinLineShare[]; entries?: FinLineEntry[];
  next_by?: Partial<Record<FinLineBy, Record<string, number>>>; next_labels?: Partial<Record<FinLineBy, Record<string, string>>>;
  next_month?: {month?: string; projection?: number | null; projection_method?: string};
  seats?: number | null; renewal_on?: string | null; paid_by?: {kind: string; name: string; amount: number}[];
}
// FIN.line: the open line page.
interface FinLineState {
  id: string; cur: FinLineRead | null; prev: FinLineRead | null; budgets: FinBudget[]; budgetsRead?: FinBudRead | null;
  vendors: Record<string, {data: FinLineRead | null}>; load: number; fq: string;
}
interface FinPage { line?: FinLineState }
interface FinLineKey { key: string; label: string; cur: number | null; prev: number | null; proj: number | null }
const FIN_LINE: {month: string; by: FinLineBy; sel: string} = {month: '', by: 'vendor', sel: ''};
function finLinePage(accountId: string) {
  const caps = finNeed(finCaps(), 'the finance caps');
  if (caps.viewing) { location.replace(finHref('/pnl')); return; }
  const L = FIN_LINE;
  if (!L.month) L.month = finToday().slice(0, 7);
  if (!FIN) return;
  const filter = finFilterFrom(FIN.q), fq = finFilterQs(filter).toString();
  FIN.line = {id: accountId, cur: null, prev: null, budgets: [], vendors: {}, load: 0, fq};
  const rows = FIN_PNL.last?.rows || [], me = rows.find(r => r.account_id === accountId), parent = me && rows.find(r => r.account_id === me.parent_id);
  const chips = [...finPicked(filter).map(finPickName), ...(filter.who !== 'both' ? [filter.who === 'bots' ? 'Bots' : 'People'] : [])]
    .map(n => `<span class="fin-chip">${esc(n)}</span>`).join('');
  $('#fin-body').innerHTML = `<nav class="fin-crumbs" aria-label="Breadcrumb"><a href="${finHref('/pnl', {view: fq || FIN_PNL.view === 'expenses' ? 'expenses' : ''})}">P&amp;L</a>${parent ? `<span class="muted">›</span><span class="muted">${esc(parent.name)}</span>` : ''}<span class="muted">›</span><h2 id="fin-line-name">${esc(me?.name || '')}</h2></nav>
    <div class="fin-bar"><label class="fin-inline"><span class="sr-only">Month</span><input type="month" data-fin-month value="${L.month}" aria-label="Month"></label>
      <label class="fin-inline"><span class="muted">Rows</span><select data-fin-by>${[['vendor', 'Vendor'], ['team', 'Team'], ['teammate', 'Teammate']].map(([v, l]) =>
        `<option value="${v}"${v === L.by ? ' selected' : ''}>${l}</option>`).join('')}</select></label>
      ${chips}<span class="spacer"></span><span id="fin-line-add"></span></div>`
    + finSplit(`<div class="fin-scroll" id="fin-line">${skelRows(6, {dot: false})}</div>`, '', 'Selected row');
  const body = finNeed<HTMLElement>($('#fin-body'), 'the Finance body');
  const monthIn = finNeed(body.querySelector<HTMLInputElement>('[data-fin-month]'), 'the month input'), byIn = finNeed(body.querySelector<HTMLSelectElement>('[data-fin-by]'), 'the rows picker');
  monthIn.onchange = () => { if (monthIn.value) { L.month = monthIn.value; L.sel = ''; void finLineLoad(); } };
  byIn.onchange = () => { L.by = byIn.value as FinLineBy; L.sel = ''; finLineDraw(); finLineRail(); };
  body.onclick = ev => {
    const t = eventElement(ev);
    if (t?.closest('[data-fin-vendor-add]')) { finVendorAdd(); return; }
    if (t?.closest('[data-fin-line-add]')) { finLineBudgetAdd(); return; }
    const tr = t?.closest<HTMLElement>('tr[data-fin-key]');
    if (tr && !t?.closest('input,a,button')) { L.sel = tr.dataset.finKey ?? ''; finLineDraw(); finLineRail(); }
  };
  body.addEventListener('change', ev => { const input = eventElement(ev)?.closest<HTMLInputElement>('input[data-fin-vb]'); if (input) void finVendorSave(input); });
  body.addEventListener('keydown', ev => { const input = eventElement(ev)?.closest<HTMLInputElement>('input[data-fin-vb]'); if (input && ev.key === 'Enter') { ev.preventDefault(); input.blur(); } });
  void finLineLoad();
}
const finMonthBounds = (ym: string) => { const [y = 0, m = 0] = ym.split('-').map(Number); return [finIso(new Date(y, m - 1, 1)), finIso(new Date(y, m, 0))]; };
const finNextMonth = (ym: string) => { const [y = 0, m = 0] = ym.split('-').map(Number); const d = new Date(y, m, 1); return `${d.getFullYear()}-${finPad(d.getMonth() + 1)}`; };
// Budgets change from a future month on, as on the P&L.
const finLineFuture = () => FIN_LINE.month > finToday().slice(0, 7);
// ...and this month's, on a budget it already has (finBudgetEditable).
const finLineNow = () => FIN_LINE.month === finToday().slice(0, 7);
const finVendorEditable = (b: FinBudget | undefined | null, parent: FinBudget | null) => finLineFuture() ? (b ? b.period === 'month' : !!parent) : finLineNow() && b?.period === 'month';
// The line's own monthly budget, which vendor budgets sit under; null when it has none (or keeps one by quarter or year).
const finLineParent = () => { const line = finNeed(FIN?.line, 'the line page'), b = finLineBudget(line.id, line.budgets); return b && b.period === 'month' ? b : null; };
const finPrevMonth = (ym: string) => { const [y = 0, m = 0] = ym.split('-').map(Number); const d = new Date(y, m - 2, 1); return `${d.getFullYear()}-${finPad(d.getMonth() + 1)}`; };
async function finLineLoad() {
  const state: FinLineState = finNeed(FIN?.line, 'the line page'), L = FIN_LINE, load = ++state.load, id = encodeURIComponent(state.id);
  const [a, z] = finMonthBounds(L.month), [pa, pz] = finMonthBounds(finPrevMonth(L.month));
  const fq = state.fq ? '&' + state.fq : '';
  const [cur, prev, budgets] = await Promise.allSettled([get<FinLineRead>(`/v2/finance/lines/${id}?start=${a}&end=${z}&top_n=${FIN_LINE_TOP}&limit=200${fq}`, {fresh: true}),
    get<FinLineRead>(`/v2/finance/lines/${id}?start=${pa}&end=${pz}&top_n=${FIN_LINE_TOP}&limit=1${fq}`, {fresh: true}),
    // Each budget as it stands in the month shown (the version in effect then), so a future month shows its own amount.
    get<FinBudRead>(`/v2/finance/budgets?at=${a}`, {fresh: true})]);
  if (FIN?.line !== state || state.load !== load || !$('#fin-line')) return;
  if (cur.status === 'rejected') { $('#fin-line').innerHTML = finFail(cur.reason); finRail(''); return; }
  state.cur = cur.value; state.prev = prev.status === 'fulfilled' ? prev.value : null;
  state.budgets = budgets.status === 'fulfilled' ? budgets.value?.budgets || [] : [];
  state.budgetsRead = budgets.status === 'fulfilled' ? budgets.value : null;
  state.vendors = {};
  const name = $('#fin-line-name'); if (name && !name.textContent) name.textContent = state.cur.account?.name || '';
  finLineDraw(); finLineRail();
}
const FIN_PER: Record<string, string> = {month: 'mo', quarter: 'qtr', year: 'yr'};
const finVendorBudget = (vendor: string): FinBudget | undefined => (FIN?.line?.budgets || []).find((b: FinBudget) => b.active && b.account_id === finNeed(FIN?.line, 'the line page').id && b.vendor === vendor && !b.team && !b.teammate);
const FIN_LINE_TOP = 50;     // the drill-down's top_n: rows past it fold into "Other · n"
const FIN_OTHER = ' other';  // that row's key (backend/finance/reads.py OTHER): no real key starts with a space
function finLineKeys(): FinLineKey[] {
  const state: FinLineState = finNeed(FIN?.line, 'the line page'), by = ('by_' + FIN_LINE.by) as `by_${FinLineBy}`, cur = state.cur?.[by] || [], prev = state.prev?.[by] || [];
  const keys = [...cur.map(r => r.key), ...prev.map(r => r.key).filter(k => !cur.some(r => r.key === k))];
  if (FIN_LINE.by === 'vendor') for (const b of state.budgets) if (b.active && b.account_id === state.id && b.vendor && !b.team && !b.teammate && !keys.includes(b.vendor)) keys.push(b.vendor);
  // Next month's projection by row (next_by). A key with a projection but no spend in either month gets a row while the
  // reads were not capped; otherwise (or past the cap) it goes into Other, so the column adds up to the line's.
  const proj: Record<string, number> = state.cur?.next_by?.[FIN_LINE.by] || {}, names: Record<string, string> = state.cur?.next_labels?.[FIN_LINE.by] || {};
  const extra = Object.keys(proj).filter(k => !keys.includes(k)).sort((x, y) => (proj[y] ?? 0) - (proj[x] ?? 0) || x.localeCompare(y));
  const room = keys.includes(FIN_OTHER) ? 0 : Math.max(0, FIN_LINE_TOP - keys.length);
  const fold = extra.slice(room);
  keys.push(...extra.slice(0, room));
  if (fold.length && !keys.includes(FIN_OTHER)) keys.push(FIN_OTHER);
  // "Other · n" last, whichever read it came from.
  keys.sort((x, y) => Number(x === FIN_OTHER) - Number(y === FIN_OTHER));
  const folded = fold.length ? fold.reduce((n, k) => n + (proj[k] ?? 0), 0) : null;
  return keys.map(k => ({key: k, label: (cur.find(r => r.key === k) || prev.find(r => r.key === k))?.label || names[k] || (k === FIN_OTHER ? `Other · ${fold.length}` : k),
    cur: cur.find(r => r.key === k)?.amount ?? null, prev: prev.find(r => r.key === k)?.amount ?? null,
    proj: k === FIN_OTHER ? folded : proj[k] ?? null}));
}
function finLineLabel(key: string, label: string) {
  if (key === FIN_OTHER) return label;
  if (FIN_LINE.by === 'teammate') return key === FIN_NONE ? 'Unattributed' : finWho(key);
  if (FIN_LINE.by === 'team') return key === FIN_NONE ? 'Unattributed' : key === 'no_team' ? 'No team' : finTeamName(key) || label;
  // A vendor's label is a contact's name or "" (a contact the viewer can't see), never the key: a bill's key is an id.
  if (FIN_LINE.by === 'vendor') return label || '—';
  return label || key;
}
function finLineDraw() {
  const state = FIN?.line, box = $('#fin-line'); if (!box || !state?.cur) return;
  const L = FIN_LINE, vendor = L.by === 'vendor', may = finNeed(finCaps(), 'the finance caps').may('finance.budgets');
  const rows = finLineKeys(), parent = finLineParent();
  const add = $('#fin-line-add');
  if (add) add.innerHTML = !may ? '' : parent ? '<button type="button" class="primary" data-fin-vendor-add>+ Vendor budget</button>'
    : finLineBudget(state.id, state.budgets) ? '' : '<button type="button" class="primary" data-fin-line-add>+ Line budget</button>';
  if (!L.sel || !rows.some(r => r.key === L.sel)) L.sel = rows[0]?.key || '';
  const [cm, pm] = [finDate(L.month + '-01').getMonth(), finDate(finPrevMonth(L.month) + '-01').getMonth()];
  // Next month's projection for each row (the line's default method on that row's history).
  const nm = state.cur.next_month?.month;
  const head = `<tr><th>${{vendor: 'Vendor', team: 'Team', teammate: 'Teammate'}[L.by]}</th>${vendor ? '<th>Team</th>' : ''}<th class="fin-n">${FIN_MON[pm]}</th><th class="fin-n">${FIN_MON[cm]}</th>${nm ? `<th class="fin-n fin-p">${FIN_MON[Number(nm.slice(5, 7)) - 1]} proj.</th>` : ''}${vendor ? `<th class="fin-n fin-b">Budget / mo${finExcl(state.budgetsRead)}</th><th>At limit</th>` : ''}</tr>`;
  const body = rows.map(r => {
    const b = vendor && finIsVendor(r.key) ? finVendorBudget(r.key) : null;
    const amount = b?.state?.amount ?? null;
    const edit = may && finIsVendor(r.key) && finVendorEditable(b, parent);
    const per = b && b.period !== 'month' ? ` <span class="muted">/ ${FIN_PER[b.period] || b.period}</span>` : '';
    const budgetCell = !vendor ? '' : !finIsVendor(r.key) ? '<td class="fin-n fin-b"></td><td></td>'
      : `<td class="fin-n fin-b">${edit ? `<input class="fin-in" inputmode="decimal" autocomplete="off" data-fin-vb="${esc(r.key)}" value="${finCentsField(amount)}" aria-label="${esc(r.label)} budget a month from ${esc(FIN_MONTH[cm])}">` : finNum(amount) + per}</td><td${b ? '' : ' class="muted"'}>${b ? esc(FIN_AT_LIMIT[b.at_limit] || b.at_limit) : ''}</td>`;
    return `<tr data-fin-key="${esc(r.key)}"${L.sel === r.key ? ' class="fin-on"' : ''}><td>${esc(finLineLabel(r.key, r.label))}</td>${vendor ? `<td>${b ? esc(b.team ? finTeamName(b.team) : 'All') : ''}</td>` : ''}<td class="fin-n">${r.prev == null ? '<span class="muted">—</span>' : finNum(r.prev)}</td><td class="fin-n">${r.cur == null ? '<span class="muted">—</span>' : finNum(r.cur)}</td>${nm ? `<td class="fin-n fin-p">${r.proj == null ? '<span class="muted">—</span>' : finNum(r.proj)}</td>` : ''}${budgetCell}</tr>`;
  }).join('');
  box.innerHTML = rows.length ? `<table class="fin-t"><thead>${head}</thead><tbody>${body}</tbody></table>` : finNote('No spend on this line yet.');
}
// Other (the rest, rolled up) and No vendor (spend with none, key unattributed) are not vendors a budget can name.
const finIsVendor = (k: string) => k !== FIN_OTHER && k !== FIN_NONE;
function finLineRail() {
  const state = FIN?.line, L = FIN_LINE; if (!state?.cur) return;
  const row = finLineKeys().find(r => r.key === L.sel);
  if (!row) { finRail(''); return; }
  const vendor = L.by === 'vendor', b = vendor && finIsVendor(row.key) ? finVendorBudget(row.key) : null, month = FIN_MON[finDate(L.month + '-01').getMonth()];
  const amount = b?.state?.amount ?? null, spent = row.cur || 0;
  const pct = amount ? Math.min(100, Math.round(spent / amount * 100)) : null;
  // A vendor's own read (lines/{id}?vendor=): how it was paid, by team, its transactions.
  const one = vendor && row.key !== FIN_OTHER, got = one ? state.vendors[row.key] : null, d = got?.data || null;
  const match = (e: FinLineEntry) => L.by === 'team' ? (e.tags?.team || (e.tags?.teammate ? 'no_team' : FIN_NONE)) === row.key : (e.tags?.teammate || FIN_NONE) === row.key;
  const entries = one ? (d?.entries || []).slice(0, 5) : row.key === FIN_OTHER ? [] : (state.cur.entries || []).filter(match).slice(0, 8);
  const paid = one ? d?.paid_by || [] : [];
  // Who it went to: by team and by teammate, each when the data names one; with no names at all, one Unattributed row.
  const teams = one ? (d?.by_team || []).filter(r => r.amount) : [], mates = one ? (d?.by_teammate || []).filter(r => r.amount) : [];
  const namesTeam = teams.some(r => r.key !== FIN_NONE && r.key !== 'no_team'), namesMate = mates.some(r => r.key !== FIN_NONE);
  const name = finLineLabel(row.key, row.label), lineName = state.cur.account?.name || '';
  const nm = state.cur.next_month, nmLabel = nm?.month ? FIN_MON[Number(nm.month.slice(5, 7)) - 1] : '';
  const q = new URLSearchParams({account_id: state.id}); if (vendor && row.key !== FIN_OTHER && row.key !== FIN_NONE) q.set('q', row.key);
  const paidLabel = (r: {kind: string; name: string}) => r.kind === 'card' ? 'Card · ' + r.name : r.kind === 'bank' ? r.name : r.name === 'manual' ? 'Journal' : r.name;
  finRail(`<div class="fin-rail-title"><h2>${esc(name)}</h2><span class="fin-big">${finUsd(row.cur)}${vendor ? ' / mo' : ''}</span></div>
    ${pct == null ? '' : `<div class="fin-bar-m${pct >= 100 ? ' bad' : b && pct >= b.warn_pct ? ' warn' : ''}" aria-hidden="true"><i style="width:${pct}%"></i></div><div class="muted fin-small">${finUsd(spent)} of ${finUsd(amount)} · ${month}</div>`}
    ${b ? finKv('Budget' + finExcl(b.state), `${finUsd(amount)} <span class="muted">/ ${{month: 'month', quarter: 'quarter', year: 'year'}[b.period] || b.period}</span>${finCardLimitsTag(b)}`) + finKv('Warn at', b.warn_pct + '%') + finKv('At limit', esc((FIN_AT_LIMIT[b.at_limit] || b.at_limit) + (b.at_limit === 'ask' && b.owner ? ' ' + finWho(b.owner) : ''))) : ''}
    ${nm && nm.projection != null ? finKv(`${lineName} · ${nmLabel} projected`, finUsd(nm.projection) + finProjTag(nm.projection_method)) : ''}
    ${one && d?.seats != null ? finKv('Seats', esc(Number(d.seats).toLocaleString('en-US'))) : ''}${one && d?.renewal_on ? finKv('Renews', esc(finDayLabel(d.renewal_on))) : ''}
    ${paid.length ? `<h3 class="fin-h">Paid by</h3>${paid.map(r => finKv(paidLabel(r), finUsd(r.amount))).join('')}` : ''}
    ${teams.length && (namesTeam || !namesMate) ? `<h3 class="fin-h">By team · ${month}</h3>${finBars(teams.map(r => ({label: r.key === FIN_NONE ? 'Unattributed' : r.key === FIN_OTHER ? r.label : finTeamName(r.key) || r.label, amount: r.amount})))}` : ''}
    ${namesMate ? `<h3 class="fin-h">By teammate · ${month}</h3>${finBars(mates.map(r => ({label: r.key === FIN_NONE ? 'Unattributed' : r.key === FIN_OTHER ? r.label : finWho(r.key), amount: r.amount})))}` : ''}
    ${entries.length ? `<h3 class="fin-h">Transactions</h3>${entries.map(e => finKv(finDayLabel(e.date), finCents(e.amount))).join('')}` : ''}
    ${row.key !== FIN_OTHER ? `<a class="fin-more" href="${FIN_ROUTE}/transactions?${q}${state.fq ? '&' + state.fq : ''}">All ${esc(name)} transactions</a>` : ''}`);
  if (!one || got) return;
  const ask: {data: FinLineRead | null} = state.vendors[row.key] = {data: null}, [a, z] = finMonthBounds(L.month);
  const v = row.key === FIN_NONE ? '' : row.key;
  get<FinLineRead>(`/v2/finance/lines/${encodeURIComponent(state.id)}?start=${a}&end=${z}&top_n=10&limit=5&vendor=${encodeURIComponent(v)}${state.fq ? '&' + state.fq : ''}`, {fresh: true})
    .then(x => { ask.data = x || {}; }, () => { ask.data = {}; })
    .then(() => { if (FIN?.line === state && state.vendors[row.key] === ask && FIN_LINE.sel === row.key) finLineRail(); });
}
// A vendor's budget a month, typed in its cell: changed when it has one, created (under the line's budget) when not.
async function finVendorSave(input: HTMLInputElement) {
  const vendor = input.dataset.finVb as string, b = finVendorBudget(vendor), was = b?.state?.amount ?? null;
  const text = input.value.trim();
  if (!text && b == null) return;
  const cents = finParseCents(text);
  if (cents == null) { toast('A dollar amount, like 450', true); input.value = finCentsField(was); return; }
  if (cents === was) return;
  const from = FIN_LINE.month + '-01', parent = finLineParent(), account = finNeed(FIN?.line, 'the line page').id;
  if (!finVendorEditable(b, parent)) { input.value = finCentsField(was); return; }
  input.disabled = true;
  const state: FinLineState = finNeed(FIN?.line, 'the line page'), name = finLineKeys().find(r => r.key === vendor)?.label || vendor;
  const [kind, payload] = b ? ['budget_update', {budget_id: b.id, amount: cents, effective_from: from}]
    : ['budget_create', {name, period: 'month', account_id: account, vendor, parent_id: finNeed(parent, 'the line budget').id, amount: cents, effective_from: from}];
  await finBudgetDecide(kind, payload, {title: name, change: finBudgetChange(FIN_MONTH[finDate(from).getMonth()], was, cents), alive: () => FIN?.line === state,
    saved: () => void finLineLoad(), undo: () => { input.disabled = false; input.value = finCentsField(was); }});
}
// The fields a new budget's dialog shares: a line's Line budget and Vendor budget here, and Finance › Budgets'
// + Budget (finance-budgets.js).
const finAmountField = (label = 'Budget / mo') => `<label>${esc(label)}<input name="amount" inputmode="decimal" required autocomplete="off"></label>`;
function finAmountValue(form: HTMLFormElement) {
  const cents = finParseCents((form.elements as HTMLFormControlsCollection & {amount: HTMLInputElement}).amount.value);
  if (cents == null) throw new Error('A dollar amount, like 450');
  return cents;
}
const finAtLimitField = (chosen = 'ask') => `<label>At limit<select name="at_limit">${Object.entries(FIN_AT_LIMIT).map(([v, l]) =>
  `<option value="${v}"${v === chosen ? ' selected' : ''}>${l}</option>`).join('')}</select></label>`;
// The month a new budget starts: `month` when it is open (ahead; or this one, with `now`), else the first open month.
const finFromMin = (now: boolean) => now ? finToday().slice(0, 7) : finNextMonth(finToday().slice(0, 7));
const finFromField = (month = '', now = false) => {
  const min = finFromMin(now);
  return `<label>From<input type="month" name="from" required min="${min}" value="${month && month >= min ? month : min}"></label>`;
};
function finFromValue(form: HTMLFormElement, now = false) {
  const v = (form.elements as HTMLFormControlsCollection & {from: HTMLInputElement}).from.value;
  if (!v || v < finFromMin(now)) throw new Error(now ? 'Pick this month or one ahead' : 'Pick a month ahead');
  return v + '-01';
}
function finVendorAdd() {
  const parent = finLineParent(), account = finNeed(FIN?.line, 'the line page').id;
  if (!parent) return;
  finDecideDialog('Vendor budget', `<label>Vendor<input name="vendor" required maxlength="80" autocomplete="off"></label>
    ${finAmountField()}${finFromField(FIN_LINE.month)}${finAtLimitField()}`,
  'Add', (form: FinFormOf<{vendor: HTMLInputElement; amount: HTMLInputElement; from: HTMLInputElement; at_limit: HTMLSelectElement}>) => {
    const vendor = form.elements.vendor.value.trim();
    if (!vendor) throw new Error('Name the vendor');
    const cents = finAmountValue(form);
    FIN_LINE.sel = vendor;
    return ['budget_create', {name: vendor, period: 'month', account_id: account, vendor, parent_id: parent.id,
      amount: cents, effective_from: finFromValue(form), at_limit: form.elements.at_limit.value}];
  }, 'Saved');
}
// A vendor budget sits under its line's budget (a budget tree starts at a line), so a line without one gets it first.
function finLineBudgetAdd() {
  const line = finNeed(FIN?.line, 'the line page'), name = line.cur?.account?.name || $('#fin-line-name')?.textContent || 'Line', account = line.id;
  finDecideDialog('Line budget', finAmountField() + finFromField(FIN_LINE.month), 'Add', (form: FinFormOf<{amount: HTMLInputElement; from: HTMLInputElement}>) => {
    const cents = finAmountValue(form);
    return ['budget_create', {name, period: 'month', account_id: account, amount: cents, effective_from: finFromValue(form)}];
  }, 'Saved');
}
