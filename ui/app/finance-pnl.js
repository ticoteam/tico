/* ui/app/finance-pnl.js — Finance › P&L (spec §12): one table of actual periods, then shaded projected periods, then
   the budget and vs budget of one period (the current one, or the one picked). Company shows revenue, cost of revenue,
   gross profit, operating expenses and net income; Expenses keeps the expense lines, filtered to teams and teammates
   from the org chart (bots, people or both; Unattributed), with the filter's teammates spent vs budget in the rail. A cell opens the rail for its line and
   period; a future month's budget cell edits on a double-click (budget_update from that month on), and so does this
   month's, to raise a budget it has. Saving is a dry run first: an allowed change saves, one needing approval names the
   approver and is sent from a small dialog. A line opens its page: by vendor, team or teammate, last month and this
   one, each vendor's budget edited in place.
   Reads: GET /api/v2/finance/pnl, /finance/budgets, /finance/lines/{account_id}. Writes: budget_create and
   budget_update through POST /api/v2/finance/actions.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The P&L's controls, kept for the visit.
const FIN_PNL = {period: 'month', basis: 'cash', start: '', end: '', rangeOf: '', compare: '', teams: [], mates: [],
  who: 'both', unattributed: false, sel: null, budgetCol: -1, last: null};
const FIN_PERIODS = [['day', 'Day'], ['week', 'Week'], ['month', 'Month'], ['quarter', 'Quarter'], ['year', 'Year']];
const FIN_AT_LIMIT = {warn: 'Warn', ask: 'Ask', decline: 'Decline', hold_payment: 'Hold payment'};

// ----------------------------------------------------------------- periods
// The window a period opens on: a few periods back and a few ahead of today.
function finPnlRange(period) {
  const t = new Date(), y = t.getFullYear(), m = t.getMonth(), d = t.getDate();
  const span = {day: [new Date(y, m, d - 6), new Date(y, m, d + 3)], week: [new Date(y, m, d - 35), new Date(y, m, d + 14)],
    month: [new Date(y, m - 3, 1), new Date(y, m + 4, 0)], quarter: [new Date(y, m - m % 3 - 9, 1), new Date(y, m - m % 3 + 6, 0)],
    year: [new Date(y - 2, 0, 1), new Date(y + 1, 11, 31)]}[period];
  return [finIso(span[0]), finIso(span[1])];
}
// A column's short name: Oct, Q4 2026, 2026, Oct 30 (a day, a week's first day).
function finColLabel(col, many) {
  const d = finDate(col.start), p = FIN_PNL.period;
  if (p === 'month') return FIN_MON[d.getMonth()] + (many && (d.getMonth() === 0) ? ' ' + d.getFullYear() : '');
  if (p === 'quarter') return String(col.label).replace(/^(\d{4})-(Q\d)$/, '$2 $1');
  if (p === 'year') return String(col.label);
  return `${FIN_MON[d.getMonth()]} ${d.getDate()}`;
}
const finColLong = col => FIN_PNL.period === 'month' ? FIN_MONTH[finDate(col.start).getMonth()] : finColLabel(col, true);
const finRangeLabel = (a, b) => {
  const x = finDate(a), y = finDate(b);
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
  const caps = finCaps(), p = FIN_PNL;
  const view = FIN.q.get('view') === 'expenses' || !caps.company ? 'expenses' : 'company';
  if (!p.start || p.rangeOf !== p.period) { [p.start, p.end] = finPnlRange(p.period); p.rangeOf = p.period; }
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
      : `<div class="fin-scroll" id="fin-pl">${skelRows(8, {dot: false})}</div>`, '', view === 'company' ? 'Selected cell' : 'Breakdown');
  const body = $('#fin-body');
  body.onclick = ev => finPnlClick(ev);
  body.ondblclick = ev => { const td = ev.target.closest('td[data-fin-budget]'); if (td) finPnlEdit(td); };
  body.onkeydown = ev => {
    const td = ev.target.closest?.('td[data-fin-budget]');
    if (td && ev.key === 'Enter' && !td.querySelector('input')) { ev.preventDefault(); finPnlEdit(td); }
  };
  $('#fin-range').onsubmit = ev => {
    ev.preventDefault();
    const f = ev.target, a = f.elements.start.value, z = f.elements.end.value;
    if (!a || !z || z < a) return;
    p.start = a; p.end = z; p.sel = null; p.budgetCol = -1;
    finPnlPage();
  };
  body.querySelector('[data-fin-compare]')?.addEventListener('change', ev => {
    p.compare = ev.target.value;
    const shown = ev.target.parentElement.querySelector('.fin-sel-v'); if (shown) shown.textContent = ev.target.selectedOptions[0]?.textContent || '';
    void finPnlLoad();
  });
  if (view === 'expenses') finFiltersDraw();
  void finPnlLoad();
}
function finPnlClick(ev) {
  const p = FIN_PNL, t = ev.target;
  const seg = t.closest('[data-fin-seg] button');
  if (seg) {
    const name = seg.parentElement.dataset.finSeg, v = seg.dataset.v;
    if (name === 'view') { location.hash = finHref('/pnl', {view: v === 'expenses' ? 'expenses' : ''}); return; }
    if (name === 'period' && v !== p.period) { p.period = v; p.sel = null; p.budgetCol = -1; finPnlPage(); return; }
    if (name === 'basis' && v !== p.basis) { p.basis = v; finSegSet(seg); void finPnlLoad(); return; }
    if (name === 'who' && v !== p.who) { p.who = v; p.sel = null; finFiltersDraw(); void finPnlLoad(); return; }
    return;
  }
  const range = t.closest('[data-fin-range]');
  if (range) { finPop(range, $('#fin-range')); return; }
  const filter = t.closest('[data-fin-filter]');
  if (filter) { finPop(filter, $('#fin-tree')); return; }
  const clear = t.closest('[data-fin-clear]');
  if (clear) { finPickSet(clear.dataset.finClear, false); p.sel = null; finFiltersDraw(); void finPnlLoad(); return; }
  if (t.closest('[data-fin-export]')) { finPnlExport(); return; }
  const cell = t.closest('td[data-fin-cell]');
  if (cell && !t.closest('a')) {
    const [id, col] = cell.dataset.finCell.split('|');
    p.sel = {id, col: Number(col)}; p.budgetCol = Number(col);
    finPnlDraw(); finPnlRail();
  }
}
const FIN_ONLY_RAISE = 'This month can only go up';
const finSegSet = btn => btn.parentElement.querySelectorAll('button').forEach(b => b.setAttribute('aria-checked', String(b === btn)));
// A popover under its button; a click outside or Escape closes it.
function finPop(btn, pop) {
  if (!pop) return;
  const open = pop.hidden;
  document.querySelectorAll('.fin-pop').forEach(x => { x.hidden = true; });
  document.querySelectorAll('[aria-controls]').forEach(b => b.getAttribute('aria-expanded') && b.setAttribute('aria-expanded', 'false'));
  pop.hidden = !open; btn.setAttribute('aria-expanded', String(open));
  if (!open) return;
  pop.querySelector('input,button')?.focus();
  const away = ev => {
    if (ev.type === 'keydown' ? ev.key !== 'Escape' : pop.contains(ev.target) || btn.contains(ev.target)) return;
    pop.hidden = true; btn.setAttribute('aria-expanded', 'false');
    document.removeEventListener('pointerdown', away, true); document.removeEventListener('keydown', away, true);
  };
  document.addEventListener('pointerdown', away, true); document.addEventListener('keydown', away, true);
}

async function finPnlLoad() {
  const state = FIN.pnl, p = FIN_PNL, caps = finCaps(), load = ++state.load;
  const qs = new URLSearchParams({start: p.start, end: p.end, period: p.period, basis: p.basis, view: state.view});
  const compare = state.view === 'company' ? p.compare : '';
  if (compare) qs.set('budget_as_of', compare);
  if (!caps.companyExpenses) p.unattributed = false;
  const snap = state.view === 'expenses' ? finFilterSnap(p) : finFilterSnap({teams: [], mates: [], who: 'both', unattributed: false});
  for (const [k, v] of finFilterQs(snap)) qs.append(k, v);
  state.busy = true;
  $('#fin-pl')?.setAttribute('aria-busy', 'true');
  const editing = caps.may('finance.budgets');
  const [pnl, budgets] = await Promise.allSettled([get('/v2/finance/pnl?' + qs + finAs(), {fresh: true}),
    editing ? get('/v2/finance/budgets' + finAs('?'), {fresh: true}) : Promise.resolve(null)]);
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
  if (!p.sel || !lines.some(r => r.account_id === p.sel.id) || p.sel.col >= cols.length) {
    const first = lines.find(r => r.type === 'expense' && r.budget?.[p.budgetCol] != null) || lines.find(r => r.type === 'expense') || lines[0];
    p.sel = first ? {id: first.account_id, col: p.budgetCol} : null;
  }
  finPnlDraw();
  finPnlRail();
}

// ----------------------------------------------------------------- the statement
// Sections from the chart: each top line (no parent shown) with the lines under it in code order. Cost of revenue is
// the section the route says (`section`), so gross profit can sit under it.
function finPnlModel(data) {
  const rows = data.rows || [], ids = new Set(rows.map(r => r.account_id)), kids = new Map();
  for (const r of rows) if (r.parent_id && ids.has(r.parent_id)) (kids.get(r.parent_id) || kids.set(r.parent_id, []).get(r.parent_id)).push(r);
  const byCode = (a, b) => String(a.code).localeCompare(String(b.code));
  const tops = rows.filter(r => !r.parent_id || !ids.has(r.parent_id)).sort((a, b) =>
    (a.type === 'income' ? 0 : 1) - (b.type === 'income' ? 0 : 1) || byCode(a, b));
  const own = r => r.private || [r.actual, r.budget, r.projection].some(v => (v || []).some(x => x));
  const sections = [], lines = [];
  for (const top of tops) {
    const sec = {top, lines: [], cogs: top.section ? top.section === 'cost_of_revenue' : top.type === 'expense' && String(top.code).startsWith('5')};
    const walk = (r, depth) => { sec.lines.push({...r, depth}); for (const k of (kids.get(r.account_id) || []).sort(byCode)) walk(k, depth + 1); };
    const under = (kids.get(top.account_id) || []).sort(byCode);
    if (!under.length || own(top)) sec.lines.push({...top, depth: 0});
    for (const k of under) walk(k, 0);
    sections.push(sec); lines.push(...sec.lines);
  }
  return {sections, lines};
}
// A line's figure in a column: the actual for past and current periods, the projection for future ones.
const finVal = (r, cols, i) => cols[i]?.state === 'future' ? r.projection?.[i] ?? null : r.actual?.[i] ?? null;
// A line's budget in a column, for totals: a private line's is left out, as its spend is (so totals compare like
// with like).
const finBudgetOf = i => r => r.private ? null : r.budget?.[i] ?? null;
// Sums over the lines shown (never over what the route left out); null when none has a figure.
function finSum(rows, pick) {
  let any = false, total = 0;
  for (const r of rows) { const v = pick(r); if (v != null) { any = true; total += v; } }
  return any ? total : null;
}
// One side of a difference (gross profit, net income): a side with no lines is 0; a side with lines but no figure for
// the column is unknown, and so is the difference.
const finSide = (rows, f) => rows.length ? finSum(rows, f) : 0;
const finLess = (a, b) => a == null || b == null ? null : a - b;
function finPnlDraw() {
  const state = FIN.pnl, box = $('#fin-pl'); if (!box || !state?.data) return;
  const data = state.data, cols = data.columns || [], p = FIN_PNL, bi = p.budgetCol, caps = finCaps();
  const {sections} = state.model;
  if (!sections.length) { box.innerHTML = finNote(state.view !== 'expenses' ? 'No lines yet.' : finFilterAny(state.snap || p) ? 'No expenses in this filter.' : 'No expenses.'); return; }
  const years = new Set(cols.map(c => c.start.slice(0, 4))).size > 1;
  const past = cols.filter(c => c.state !== 'future').length, cur = cols.find(c => c.state === 'current');
  const imported = finImportedCols(data, cols);
  const head = cols.map((c, i) => `<th class="${c.state === 'future' ? 'fin-p' : ''}${i === past && c.state === 'future' ? ' fin-b' : ''}">${esc(finColLabel(c, years))}${imported[i] ? `<span class="fin-tag" title="${esc(imported[i])}">Imported</span>` : ''}</th>`).join('');
  finMarksDraw($('#fin-pl-marks'), data);
  const bcol = cols[bi];
  const sub = `<tr class="fin-sub-head"><th></th>${past ? `<th colspan="${past}">${cur ? 'Actual · ' + esc(finColLabel(cur, years)) + ' to date' : 'Actual'}</th>` : ''}${cols.length - past
    ? `<th class="fin-p fin-b" colspan="${cols.length - past}">Projected</th>` : ''}<th class="fin-b" colspan="2"></th></tr>`;
  const sign = r => r.type === 'income' ? 1 : -1;
  const fq = finFilterQs(state.snap).toString(), lineQs = fq ? '?' + fq : '';
  // A narrow viewer's totals leave payroll out (§7.5): they say so.
  const excl = finExcl(data), hasPrivate = rows => rows.some(r => r.private);
  // Compare shows the plan as it stood on a past day: not the live budget, so not editable.
  const comparing = state.view === 'company' && !!state.compare;
  const editable = (r) => !caps.viewing && !comparing && caps.may('finance.budgets') && p.period === 'month'
    && r.type === 'expense' && !r.private && finBudgetEditable(bcol, finBudgetFor(r.account_id));
  const nums = (vals, budget, actualB, good, cls = '') => {
    const diff = budget == null || actualB == null ? null : good * (actualB - budget);
    const d = finDiff(diff);
    return vals.map((v, i) => `<td class="${cols[i].state === 'future' ? 'fin-p' : ''}${i === past && cols[i].state === 'future' ? ' fin-b' : ''}${cls}">${finNum(v)}</td>`).join('')
      + `<td class="fin-b">${finNum(budget)}</td><td class="${d.cls}">${d.text}</td>`;
  };
  const line = r => {
    const vals = cols.map((c, i) => finVal(r, cols, i));
    const drill = !r.private && !caps.viewing;
    const name = drill ? `<a href="${FIN_ROUTE}/line/${encodeURIComponent(r.account_id)}${lineQs}">${esc(r.name)} ▸</a>` : `<span${r.private ? ' class="muted"' : ''}>${esc(r.name)}</span>`;
    const budget = r.private ? null : r.budget?.[bi] ?? null, actualB = finVal(r, cols, bi);
    const diff = finDiff(budget == null || actualB == null ? null : sign(r) * (actualB - budget));
    const sel = p.sel?.id === r.account_id ? p.sel.col : -1;
    const edit = editable(r);
    const how = FIN_PROJ[r.projection_method] ? ` title="${FIN_PROJ[r.projection_method]}"` : '';
    const cells = vals.map((v, i) => `<td data-fin-cell="${esc(r.account_id)}|${i}"${cols[i].state === 'future' ? how : ''} class="${cols[i].state === 'future' ? 'fin-p' : ''}${i === past && cols[i].state === 'future' ? ' fin-b' : ''}${sel === i ? ' fin-on' : ''}">${r.private && v == null ? '<span class="muted">—</span>' : finNum(v)}</td>`).join('');
    return `<tr class="fin-ln" style="--d:${r.depth}"><td>${name}</td>${cells}<td class="fin-b${edit ? ' fin-edit' : ''}"${edit
      ? ` data-fin-budget="${esc(r.account_id)}" tabindex="0" aria-label="${esc(r.name)} budget, ${esc(finColLong(bcol))}"` : ''}>${r.private ? '<span class="muted">—</span>' : finNum(budget)}</td><td class="${diff.cls}">${diff.text}</td></tr>`;
  };
  const total = (label, rows, cls, good) => {
    const vals = cols.map((c, i) => finSum(rows, r => r.private ? null : finVal(r, cols, i)));
    const budget = finSum(rows, finBudgetOf(bi));
    return `<tr class="${cls}"><td>${esc(label)}</td>${nums(vals, budget, vals[bi], good)}</tr>`;
  };
  const out = [];
  const income = sections.filter(s => s.top.type === 'income').flatMap(s => s.lines);
  const cogs = sections.filter(s => s.cogs).flatMap(s => s.lines);
  for (const [n, s] of sections.entries()) {
    out.push(`<tr class="fin-sec"><td>${esc(s.top.name)}</td><td colspan="${cols.length + 2}"></td></tr>`);
    out.push(...s.lines.map(line));
    out.push(total('Total ' + s.top.name.toLowerCase() + (hasPrivate(s.lines) ? excl : ''), s.lines, 'fin-subt', s.top.type === 'income' ? 1 : -1));
    const lastCogs = s.cogs && !sections.slice(n + 1).some(x => x.cogs);
    if (state.view === 'company' && lastCogs && income.length) {
      const gp = i => finLess(finSide(income, r => r.private ? null : finVal(r, cols, i)), finSide(cogs, r => r.private ? null : finVal(r, cols, i)));
      const gpB = finLess(finSide(income, finBudgetOf(bi)), finSide(cogs, finBudgetOf(bi)));
      const gv = cols.map((c, i) => gp(i));
      out.push(`<tr class="fin-subt"><td>Gross profit${hasPrivate([...income, ...cogs]) ? excl : ''}</td>${nums(gv, gpB, gv[bi], 1)}</tr>`);
      const pct = i => { if (gv[i] == null) return null; const rev = finSum(income, r => r.private ? null : finVal(r, cols, i)); return rev ? Math.round(gv[i] / rev * 100) : null; };
      const pctB = (() => { const rev = finSum(income, finBudgetOf(bi)); return rev && gpB != null ? Math.round(gpB / rev * 100) : null; })();
      const pd = pctB == null || pct(bi) == null ? null : pct(bi) - pctB;
      out.push(`<tr class="fin-pct"><td>Gross margin</td>${cols.map((c, i) => `<td class="${c.state === 'future' ? 'fin-p' : ''}${i === past && c.state === 'future' ? ' fin-b' : ''}">${pct(i) == null ? '' : pct(i) + '%'}</td>`).join('')}<td class="fin-b">${pctB == null ? '' : pctB + '%'}</td><td class="${pd == null ? '' : pd > 0 ? 'fin-good' : pd < 0 ? 'fin-bad' : 'muted'}">${pd == null ? '' : pd ? (pd > 0 ? '+' : '−') + Math.abs(pd) + ' pts' : '—'}</td></tr>`);
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
    out.push(`<tr class="fin-tot"><td>Net income${excl}</td>${nums(net, netB, net[bi], 1)}</tr>`);
  } else {
    const spent = cols.map((c, i) => c.state === 'future' ? finSum(allLines, r => r.private ? null : finVal(r, cols, i)) : data.expense?.[i] ?? null);
    out.push(`<tr class="fin-tot"><td>Total expenses${excl}</td>${nums(spent, finSum(allLines, finBudgetOf(bi)), spent[bi], -1)}</tr>`);
  }
  box.innerHTML = `<table class="fin-pl"><thead><tr><th class="fin-first"></th>${head}<th class="fin-b">${esc(bcol ? finColLabel(bcol, years) : '')} budget${finExcl(state.budgetsRead)}</th><th>vs budget</th></tr>${sub}</thead><tbody>${out.join('')}</tbody></table>`;
}
// The budgets a P&L cell sums, the way the route picks them (backend/finance/reads.py _top_budgets, _filter_budgets):
// unfiltered, the top budgets the viewer sees; filtered to a team, those of that team and the teams under it; to a
// teammate, theirs. Never a parent and its child both.
function finScopeBudgets(accountId) {
  const state = FIN.pnl, p = state?.snap || finFilterSnap({teams: [], mates: [], who: 'both', unattributed: false});
  const all = (state?.budgets || []).filter(b => b.active && !b.catch_all);
  let picked = all;
  if (state?.view === 'expenses' && finFilterAny(p)) {
    // backend/finance/reads.py _Filter.keeps_budget
    const below = p.teams.length ? new Set(p.teams.flatMap(t => [...finTeamsBelow(t)])) : null;
    const chosen = !!(p.teams.length || p.mates.length || p.unattributed);
    const kind = m => p.who === 'both' || m.startsWith(p.who === 'bots' ? 'bot:' : 'human:');
    picked = all.filter(b => {
      const inTeams = !!below && !!b.team && below.has(b.team);
      return b.teammate ? kind(b.teammate) && (!chosen || p.mates.includes(b.teammate) || inTeams) : p.who === 'both' && inTeams;
    });
  }
  const ids = new Set(picked.map(b => b.id));
  return picked.filter(b => b.account_id === accountId && !(b.parent_id && ids.has(b.parent_id)));
}
const finFiltered = () => FIN.pnl?.view === 'expenses' && !!FIN.pnl.snap && finFilterAny(FIN.pnl.snap);
// A team and every team under it.
function finTeamsBelow(id) {
  const teams = (FIN?.acc || FIN_ACCESS)?.teams || [], out = new Set([id]);
  for (let grew = true; grew;) { grew = false; for (const t of teams) if (t.parent && out.has(t.parent) && !out.has(t.id)) { out.add(t.id); grew = true; } }
  return out;
}
// The budget a line's month cell edits: the one budget the cell shows (null: none yet, so a new line budget, but only
// unfiltered, since a team's or teammate's budget sits under the line's); false when the cell sums several budgets or
// shows one kept by quarter or year, which a month's cell cannot say.
function finBudgetFor(accountId) {
  const tops = finScopeBudgets(accountId);
  if (!tops.length) return finFiltered() ? false : null;
  if (tops.length > 1 || tops[0].period !== 'month') return false;
  return tops[0];
}
// Which month's cell takes an edit: any future month's, a budget or a new one; this month's only to raise a budget it
// has (§4.4 sends the raise to the budget's owner); never a past month's.
const finBudgetEditable = (col, b) => b !== false && (col?.state === 'future' || (col?.state === 'current' && !!b));
// A budget edit in place, dry run first (§12): an allowed one saves and says no more, a refused one says why, and one
// that needs approval shows the change and whom it goes to, and goes when sent. `alive` says the page is still there.
async function finBudgetDecide(kind, payload, {title, change, alive, saved, undo}) {
  let r;
  try { r = await finDecide(kind, payload); }
  catch (e) { toast(e.message, true); if (alive()) undo(); return; }
  if (r.ask == null) { if (r.status === 'committed') toast('Saved'); if (alive()) saved(); return; }
  if (!alive()) return;
  let sent = false;
  finDialog(title, `${change}<p class="fin-ask">${esc(finAskText(r.ask))}</p>${finImpactHtml(r.impact)}`, 'Send', async () => {
    await finDecide(kind, payload, r.sig);
    sent = true;
    if (alive()) saved();
  }).addEventListener('close', () => { if (!sent && alive()) undo(); });
}
const finBudgetChange = (month, was, cents) => finKv(month, `${was == null ? '' : finUsd(was) + ' → '}${finUsd(cents)}`);
// A line's own budget (the root of its tree: no team, teammate or vendor), which vendor budgets sit under.
function finLineBudget(accountId, budgets) {
  const live = (budgets || []).filter(b => b.active && !b.catch_all), ids = new Set(live.map(b => b.id));
  return live.find(b => b.account_id === accountId && !b.team && !b.teammate && !b.vendor && !(b.parent_id && ids.has(b.parent_id))) || null;
}
// Cents as the field shows them: 450, 450.50.
const finCentsField = c => c == null ? '' : c % 100 ? (c / 100).toFixed(2) : String(c / 100);
function finPnlEdit(td) {
  if (td.querySelector('input')) return;
  const id = td.dataset.finBudget, state = FIN.pnl, cols = state.data.columns, col = cols[FIN_PNL.budgetCol];
  const row = state.model.lines.find(r => r.account_id === id);
  // The budget this cell shows, fixed now: a filter change while typing reloads the table and the edit is dropped.
  const data = state.data, b = finBudgetFor(id);
  if (state.busy || state.compare || !row || !finBudgetEditable(col, b)) return;
  const was = row.budget?.[FIN_PNL.budgetCol] ?? null;
  td.innerHTML = `<input class="fin-cell-in" inputmode="decimal" autocomplete="off" value="${finCentsField(was)}" aria-label="${esc(row.name)} budget, ${esc(finColLong(col))}">`;
  const input = td.querySelector('input');
  input.focus(); input.select();
  let done = false;
  const finish = async save => {
    if (done) return; done = true;
    const cents = finParseCents(input.value);
    if (!save || cents == null || cents === was || state.busy || state.data !== data) { finPnlDraw(); return; }
    if (col.state === 'current' && was != null && cents < was) { toast(FIN_ONLY_RAISE, true); finPnlDraw(); return; }
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
  const p = FIN_PNL, view = FIN.pnl?.view || 'company';
  finCsv(finTableRows($('#fin-pl table')), `pnl-${view}-${p.period}-${p.start}-${p.end}.csv`);
}

// ----------------------------------------------------------------- the rail
// How a projection was made (the read's projection_method), as a small label.
const FIN_PROJ = {trend: 'Trend', run_rate: 'Run rate', recurring: 'Recurring', fixed: 'Fixed', manual: 'Manual', mixed: 'Mixed'};
// A budget a card draws on holds only as tight as the card's limit (§8.2).
const finCardLimitsTag = b => b?.card_limits ? ' <span class="fin-tag">Enforced by card limits</span>' : '';
const finProjTag = m => FIN_PROJ[m] ? ` <span class="fin-tag">${FIN_PROJ[m]}</span>` : '';
// Columns holding a month a spreadsheet's history was imported into on the other basis (the read's `imported`, shown
// only to those who see everything): each such column's months and basis, for its "Imported" label.
function finImportedCols(data, cols) {
  const months = data.imported || [];
  return cols.map(c => {
    const a = String(c.start || '').slice(0, 7), z = String(c.end || c.start || '').slice(0, 7);
    const hit = months.filter(m => m.month >= a && m.month <= z);
    return hit.length ? hit.map(m => `${m.month} · ${m.basis === 'cash' ? 'Cash' : 'Accrual'}`).join(', ') : '';
  });
}
// A closed month's snapshot (a window inside one closed month) and how many restatements it has taken since.
function finMarksDraw(box, data) {
  if (!box) return;
  const snap = data?.snapshot;
  box.innerHTML = snap ? `<span class="pill ok">Closed</span>${snap.restatements ? `<span class="pill waiting" title="${esc(snap.month)}">Restated · ${snap.restatements}</span>` : ''}` : '';
}
async function finPnlRail() {
  const state = FIN.pnl, p = FIN_PNL; if (!state?.data) return;
  const cols = state.data.columns || [], caps = finCaps();
  const row = p.sel ? state.model.lines.find(r => r.account_id === p.sel.id) : null;
  let html = '';
  if (row) {
    const i = p.sel.col, col = cols[i], future = col.state === 'future';
    const v = finVal(row, cols, i), budget = row.budget?.[i] ?? null;
    const sec = state.model.sections.find(s => s.lines.some(r => r.account_id === row.account_id));
    const over = v == null || budget == null ? null : (row.type === 'income' ? budget - v : v - budget);
    const nextAt = cols.findIndex((c, k) => k > i && c.state === 'future');
    html += `${sec && sec.top.account_id !== row.account_id ? `<div class="muted fin-sup">${esc(sec.top.name)}</div>` : ''}
      <h2>${esc(row.name)} · ${esc(finColLong(col))}</h2>
      ${finKv(future ? 'Projected' : 'Actual', finUsd(v) + (future ? finProjTag(row.projection_method) : ''))}${finKv('Budget' + finExcl(finBudgetFor(row.account_id)?.state), finUsd(budget) + finCardLimitsTag(finBudgetFor(row.account_id)))}
      ${over == null ? '' : over > 0 ? finKv(row.type === 'income' ? 'Under' : 'Over', finUsd(over), 'fin-bad') : finKv(row.type === 'income' ? 'Over' : 'Left', finUsd(-over))}
      ${nextAt >= 0 ? finKv(finColLabel(cols[nextAt], false) + ' projected', finUsd(row.projection?.[nextAt] ?? null) + finProjTag(row.projection_method)) : ''}
      <div id="fin-by"></div>`;
  }
  if (state.view === 'expenses') html += finTeammatesHtml();
  finRail(html);
  // Who spent it: the line's teammates in that period (a read of its own, as the viewer sees them).
  if (!row || row.private || caps.viewing) return;
  const col = cols[p.sel.col], sel = p.sel;
  try {
    const fq = finFilterQs(state.snap).toString();
    const d = await get(`/v2/finance/lines/${encodeURIComponent(row.account_id)}?start=${col.start}&end=${col.end}&top_n=6&limit=1${fq ? '&' + fq : ''}`, {fresh: true});
    if (FIN?.pnl !== state || p.sel !== sel || !$('#fin-by')) return;
    const by = (d.by_teammate || []).filter(r => r.amount);
    $('#fin-by').innerHTML = by.length ? `<h3 class="fin-h">By teammate</h3>${finBars(by.map(r => ({label: r.key === FIN_OTHER ? r.label : finWho(r.key), amount: r.amount})))}` : '';
  } catch { /* the figures above stand on their own */ }
}
function finTeammatesHtml() {
  const state = FIN.pnl, p = FIN_PNL;
  const rows = state.data.teammates || [], picks = finPicked(p);
  const who = picks.length ? picks.map(finPickName).join(', ') : 'Everyone';
  return `<div class="fin-rail-sec"><div class="muted fin-sup">Expenses · ${esc(finRangeLabel(state.data.start, state.data.end))}</div><h2>${esc(who)}</h2>
    ${rows.length ? `<h3 class="fin-h">By teammate</h3><div class="fin-tm"><span></span><span class="muted">Spent</span><span class="muted">Budget</span>${rows.map(t =>
      `<span class="fin-tm-who">${finAvatar(t.teammate, 18)}<span>${esc(finWho(t.teammate))}</span></span><span>${finNum(t.spent)}</span><span class="muted">${finNum(t.budget)}</span>`).join('')}</div>` : ''}</div>`;
}

// ----------------------------------------------------------------- the expenses filter
// Teams (with the teams under them) and teammates picked together from the org chart, and Unattributed for those who
// see company-wide expenses; bots, people or both. The route does the filtering (pnl teams, teammates, who,
// unattributed).
const finPicked = p => [...p.teams.map(t => 'team:' + t), ...p.mates.map(m => 'mate:' + m), ...(p.unattributed ? ['none:'] : [])];
const finPickName = k => k.startsWith('team:') ? finTeamName(k.slice(5)) : k.startsWith('mate:') ? finWho(k.slice(5)) : 'Unattributed';
const finFilterAny = p => !!(p.teams.length || p.mates.length || p.unattributed || p.who !== 'both');
const finFilterSnap = p => ({teams: [...p.teams], mates: [...p.mates], who: p.who, unattributed: !!p.unattributed});
// The filter as the routes take it (pnl, lines/{id}): teams, teammates, who, unattributed.
function finFilterQs(f) {
  const qs = new URLSearchParams();
  if (!f) return qs;
  for (const t of f.teams) qs.append('teams', t);
  for (const m of f.mates) qs.append('teammates', m);
  if (f.who !== 'both') qs.set('who', f.who);
  if (f.unattributed) qs.set('unattributed', 'true');
  return qs;
}
// A filter from an address (a line page opened from a filtered P&L).
const finFilterFrom = q => ({teams: q.getAll('teams'), mates: q.getAll('teammates'), who: ['bots', 'people'].includes(q.get('who')) ? q.get('who') : 'both',
  unattributed: q.get('unattributed') === 'true'});
function finPickSet(k, on) {
  const p = FIN_PNL, v = String(k || '').slice(5);
  const put = (arr, x) => on ? (arr.includes(x) ? arr : [...arr, x]) : arr.filter(y => y !== x);
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
  const p = FIN_PNL, acc = FIN.acc, caps = finCaps(), on = new Set(finPicked(p));
  const kind = {bots: 'bot', people: 'person'}[p.who];
  const teams = acc.teams || [], members = (acc.teammates || []).filter(t => !kind || t.kind === kind);
  const kids = id => finTeamKids(teams, id);
  const helpers = members.filter(m => finHelper(m.teammate)), staff = members.filter(m => !finHelper(m.teammate));
  const count = id => { const ids = finTeamsBelow(id); return staff.filter(m => ids.has(m.team)).length; };
  const pick = (k, label, d) => `<label class="fin-tree-r" style="--d:${d}"><input type="checkbox" data-fin-pick="${esc(k)}"${on.has(k) ? ' checked' : ''}>${label}</label>`;
  const mate = (m, d) => pick('mate:' + m.teammate, finAvatar(m.teammate, 16) + esc(finWho(m.teammate)), d);
  const out = [`<label class="fin-tree-r"><input type="checkbox" data-fin-pick=""${!on.size ? ' checked' : ''}>Everyone</label>`];
  const walk = (team, d) => {
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
  const trees = [box.querySelector('#fin-tree'), col].filter(Boolean);
  const change = ev => {
    const k = ev.target.dataset.finPick; if (k == null) return;
    finPickSet(k, ev.target.checked);
    const now = new Set(finPicked(p));
    for (const t of trees) t.querySelectorAll('[data-fin-pick]').forEach(x => { x.checked = x.dataset.finPick ? now.has(x.dataset.finPick) : !now.size; });
    p.sel = null; finChipsDraw(); void finPnlLoad();
  };
  for (const t of trees) t.onchange = change;
}

// ----------------------------------------------------------------- a line's page
const FIN_LINE = {month: '', by: 'vendor', sel: ''};
function finLinePage(accountId) {
  const caps = finCaps();
  if (caps.viewing) { location.replace(finHref('/pnl')); return; }
  const L = FIN_LINE;
  if (!L.month) L.month = finToday().slice(0, 7);
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
  const body = $('#fin-body');
  body.querySelector('[data-fin-month]').onchange = ev => { if (ev.target.value) { L.month = ev.target.value; L.sel = ''; void finLineLoad(); } };
  body.querySelector('[data-fin-by]').onchange = ev => { L.by = ev.target.value; L.sel = ''; finLineDraw(); finLineRail(); };
  body.onclick = ev => {
    if (ev.target.closest('[data-fin-vendor-add]')) { finVendorAdd(); return; }
    if (ev.target.closest('[data-fin-line-add]')) { finLineBudgetAdd(); return; }
    const tr = ev.target.closest('tr[data-fin-key]');
    if (tr && !ev.target.closest('input,a,button')) { L.sel = tr.dataset.finKey; finLineDraw(); finLineRail(); }
  };
  body.addEventListener('change', ev => { const input = ev.target.closest('input[data-fin-vb]'); if (input) void finVendorSave(input); });
  body.addEventListener('keydown', ev => { const input = ev.target.closest?.('input[data-fin-vb]'); if (input && ev.key === 'Enter') { ev.preventDefault(); input.blur(); } });
  void finLineLoad();
}
const finMonthBounds = ym => { const [y, m] = ym.split('-').map(Number); return [finIso(new Date(y, m - 1, 1)), finIso(new Date(y, m, 0))]; };
const finNextMonth = ym => { const [y, m] = ym.split('-').map(Number); const d = new Date(y, m, 1); return `${d.getFullYear()}-${finPad(d.getMonth() + 1)}`; };
// Budgets change from a future month on, as on the P&L.
const finLineFuture = () => FIN_LINE.month > finToday().slice(0, 7);
// ...and this month's goes up only, on a budget it already has (finBudgetEditable).
const finLineNow = () => FIN_LINE.month === finToday().slice(0, 7);
const finVendorEditable = (b, parent) => finLineFuture() ? (b ? b.period === 'month' : !!parent) : finLineNow() && b?.period === 'month';
// The line's own monthly budget, which vendor budgets sit under; null when it has none (or keeps one by quarter or year).
const finLineParent = () => { const b = finLineBudget(FIN.line.id, FIN.line.budgets); return b && b.period === 'month' ? b : null; };
const finPrevMonth = ym => { const [y, m] = ym.split('-').map(Number); const d = new Date(y, m - 2, 1); return `${d.getFullYear()}-${finPad(d.getMonth() + 1)}`; };
async function finLineLoad() {
  const state = FIN.line, L = FIN_LINE, load = ++state.load, id = encodeURIComponent(state.id);
  const [a, z] = finMonthBounds(L.month), [pa, pz] = finMonthBounds(finPrevMonth(L.month));
  const fq = state.fq ? '&' + state.fq : '';
  const [cur, prev, budgets] = await Promise.allSettled([get(`/v2/finance/lines/${id}?start=${a}&end=${z}&top_n=${FIN_LINE_TOP}&limit=200${fq}`, {fresh: true}),
    get(`/v2/finance/lines/${id}?start=${pa}&end=${pz}&top_n=${FIN_LINE_TOP}&limit=1${fq}`, {fresh: true}),
    // Each budget as it stands in the month shown (the version in effect then), so a future month shows its own amount.
    get(`/v2/finance/budgets?at=${a}`, {fresh: true})]);
  if (FIN?.line !== state || state.load !== load || !$('#fin-line')) return;
  if (cur.status === 'rejected') { $('#fin-line').innerHTML = finFail(cur.reason); finRail(''); return; }
  state.cur = cur.value; state.prev = prev.status === 'fulfilled' ? prev.value : null;
  state.budgets = budgets.status === 'fulfilled' ? budgets.value?.budgets || [] : [];
  state.budgetsRead = budgets.status === 'fulfilled' ? budgets.value : null;
  state.vendors = {};
  const name = $('#fin-line-name'); if (name && !name.textContent) name.textContent = state.cur.account?.name || '';
  finLineDraw(); finLineRail();
}
const FIN_PER = {month: 'mo', quarter: 'qtr', year: 'yr'};
const finVendorBudget = vendor => (FIN.line?.budgets || []).find(b => b.active && b.account_id === FIN.line.id && b.vendor === vendor && !b.team && !b.teammate);
const FIN_LINE_TOP = 50;     // the drill-down's top_n: rows past it fold into "Other · n"
const FIN_OTHER = ' other';  // that row's key (backend/finance/reads.py OTHER): no real key starts with a space
function finLineKeys() {
  const state = FIN.line, by = 'by_' + FIN_LINE.by, cur = state.cur?.[by] || [], prev = state.prev?.[by] || [];
  const keys = [...cur.map(r => r.key), ...prev.map(r => r.key).filter(k => !cur.some(r => r.key === k))];
  if (FIN_LINE.by === 'vendor') for (const b of state.budgets) if (b.active && b.account_id === state.id && b.vendor && !b.team && !b.teammate && !keys.includes(b.vendor)) keys.push(b.vendor);
  // Next month's projection by row (next_by). A key with a projection but no spend in either month gets a row while the
  // reads were not capped; otherwise (or past the cap) it goes into Other, so the column adds up to the line's.
  const proj = state.cur?.next_by?.[FIN_LINE.by] || {}, names = state.cur?.next_labels?.[FIN_LINE.by] || {};
  const extra = Object.keys(proj).filter(k => !keys.includes(k)).sort((x, y) => proj[y] - proj[x] || x.localeCompare(y));
  const room = keys.includes(FIN_OTHER) ? 0 : Math.max(0, FIN_LINE_TOP - keys.length);
  const fold = extra.slice(room);
  keys.push(...extra.slice(0, room));
  if (fold.length && !keys.includes(FIN_OTHER)) keys.push(FIN_OTHER);
  // "Other · n" last, whichever read it came from.
  keys.sort((x, y) => (x === FIN_OTHER) - (y === FIN_OTHER));
  const folded = fold.length ? fold.reduce((n, k) => n + proj[k], 0) : null;
  return keys.map(k => ({key: k, label: (cur.find(r => r.key === k) || prev.find(r => r.key === k))?.label || names[k] || (k === FIN_OTHER ? `Other · ${fold.length}` : k),
    cur: cur.find(r => r.key === k)?.amount ?? null, prev: prev.find(r => r.key === k)?.amount ?? null,
    proj: k === FIN_OTHER ? folded : proj[k] ?? null}));
}
function finLineLabel(key, label) {
  if (key === FIN_OTHER) return label;
  if (FIN_LINE.by === 'teammate') return key === FIN_NONE ? 'Unattributed' : finWho(key);
  if (FIN_LINE.by === 'team') return key === FIN_NONE ? 'Unattributed' : key === 'no_team' ? 'No team' : finTeamName(key) || label;
  // A vendor's label is a contact's name or "" (a contact the viewer can't see), never the key: a bill's key is an id.
  if (FIN_LINE.by === 'vendor') return label || '—';
  return label || key;
}
function finLineDraw() {
  const state = FIN.line, box = $('#fin-line'); if (!box || !state.cur) return;
  const L = FIN_LINE, vendor = L.by === 'vendor', may = finCaps().may('finance.budgets');
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
const finIsVendor = k => k !== FIN_OTHER && k !== FIN_NONE;
function finLineRail() {
  const state = FIN.line, L = FIN_LINE; if (!state?.cur) return;
  const row = finLineKeys().find(r => r.key === L.sel);
  if (!row) { finRail(''); return; }
  const vendor = L.by === 'vendor', b = vendor && finIsVendor(row.key) ? finVendorBudget(row.key) : null, month = FIN_MON[finDate(L.month + '-01').getMonth()];
  const amount = b?.state?.amount ?? null, spent = row.cur || 0;
  const pct = amount ? Math.min(100, Math.round(spent / amount * 100)) : null;
  // A vendor's own read (lines/{id}?vendor=): how it was paid, by team, its transactions.
  const one = vendor && row.key !== FIN_OTHER, got = one ? state.vendors[row.key] : null, d = got?.data || null;
  const match = e => L.by === 'team' ? (e.tags?.team || (e.tags?.teammate ? 'no_team' : FIN_NONE)) === row.key : (e.tags?.teammate || FIN_NONE) === row.key;
  const entries = one ? (d?.entries || []).slice(0, 5) : row.key === FIN_OTHER ? [] : (state.cur.entries || []).filter(match).slice(0, 8);
  const paid = one ? d?.paid_by || [] : [];
  // Who it went to: by team and by teammate, each when the data names one; with no names at all, one Unattributed row.
  const teams = one ? (d?.by_team || []).filter(r => r.amount) : [], mates = one ? (d?.by_teammate || []).filter(r => r.amount) : [];
  const namesTeam = teams.some(r => r.key !== FIN_NONE && r.key !== 'no_team'), namesMate = mates.some(r => r.key !== FIN_NONE);
  const name = finLineLabel(row.key, row.label), lineName = state.cur.account?.name || '';
  const nm = state.cur.next_month, nmLabel = nm?.month ? FIN_MON[Number(nm.month.slice(5, 7)) - 1] : '';
  const q = new URLSearchParams({account_id: state.id}); if (vendor && row.key !== FIN_OTHER && row.key !== FIN_NONE) q.set('q', row.key);
  const paidLabel = r => r.kind === 'card' ? 'Card · ' + r.name : r.kind === 'bank' ? r.name : r.name === 'manual' ? 'Journal' : r.name;
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
  const ask = state.vendors[row.key] = {data: null}, [a, z] = finMonthBounds(L.month);
  const v = row.key === FIN_NONE ? '' : row.key;
  get(`/v2/finance/lines/${encodeURIComponent(state.id)}?start=${a}&end=${z}&top_n=10&limit=5&vendor=${encodeURIComponent(v)}${state.fq ? '&' + state.fq : ''}`, {fresh: true})
    .then(x => { ask.data = x || {}; }, () => { ask.data = {}; })
    .then(() => { if (FIN?.line === state && state.vendors[row.key] === ask && FIN_LINE.sel === row.key) finLineRail(); });
}
// A vendor's budget a month, typed in its cell: changed when it has one, created (under the line's budget) when not.
async function finVendorSave(input) {
  const vendor = input.dataset.finVb, b = finVendorBudget(vendor), was = b?.state?.amount ?? null;
  const text = input.value.trim();
  if (!text && b == null) return;
  const cents = finParseCents(text);
  if (cents == null) { toast('A dollar amount, like 450', true); input.value = finCentsField(was); return; }
  if (cents === was) return;
  const from = FIN_LINE.month + '-01', parent = finLineParent(), account = FIN.line.id;
  if (!finVendorEditable(b, parent)) { input.value = finCentsField(was); return; }
  if (finLineNow() && cents < was) { toast(FIN_ONLY_RAISE, true); input.value = finCentsField(was); return; }
  input.disabled = true;
  const state = FIN.line, name = finLineKeys().find(r => r.key === vendor)?.label || vendor;
  const [kind, payload] = b ? ['budget_update', {budget_id: b.id, amount: cents, effective_from: from}]
    : ['budget_create', {name, period: 'month', account_id: account, vendor, parent_id: parent.id, amount: cents, effective_from: from}];
  await finBudgetDecide(kind, payload, {title: name, change: finBudgetChange(FIN_MONTH[finDate(from).getMonth()], was, cents), alive: () => FIN?.line === state,
    saved: () => void finLineLoad(), undo: () => { input.disabled = false; input.value = finCentsField(was); }});
}
// The month a new budget starts: the month shown when it is ahead, else next month.
const finFromField = () => `<label>From<input type="month" name="from" required min="${finNextMonth(finToday().slice(0, 7))}" value="${finLineFuture() ? FIN_LINE.month : finNextMonth(finToday().slice(0, 7))}"></label>`;
function finFromValue(form) {
  const v = form.elements.from.value;
  if (!v || v <= finToday().slice(0, 7)) throw new Error('Pick a month ahead');
  return v + '-01';
}
function finVendorAdd() {
  const parent = finLineParent(), account = FIN.line.id;
  if (!parent) return;
  finDecideDialog('Vendor budget', `<label>Vendor<input name="vendor" required maxlength="80" autocomplete="off"></label>
    <label>Budget / mo<input name="amount" inputmode="decimal" required autocomplete="off"></label>${finFromField()}
    <label>At limit<select name="at_limit">${Object.entries(FIN_AT_LIMIT).map(([v, l]) => `<option value="${v}"${v === 'ask' ? ' selected' : ''}>${l}</option>`).join('')}</select></label>`,
  'Add', form => {
    const vendor = form.elements.vendor.value.trim(), cents = finParseCents(form.elements.amount.value);
    if (!vendor) throw new Error('Name the vendor');
    if (cents == null) throw new Error('A dollar amount, like 450');
    FIN_LINE.sel = vendor;
    return ['budget_create', {name: vendor, period: 'month', account_id: account, vendor, parent_id: parent.id,
      amount: cents, effective_from: finFromValue(form), at_limit: form.elements.at_limit.value}];
  }, 'Saved');
}
// A vendor budget sits under its line's budget (a budget tree starts at a line), so a line without one gets it first.
function finLineBudgetAdd() {
  const name = FIN.line.cur?.account?.name || $('#fin-line-name')?.textContent || 'Line', account = FIN.line.id;
  finDecideDialog('Line budget', `<label>Budget / mo<input name="amount" inputmode="decimal" required autocomplete="off"></label>${finFromField()}`, 'Add', form => {
    const cents = finParseCents(form.elements.amount.value);
    if (cents == null) throw new Error('A dollar amount, like 450');
    return ['budget_create', {name, period: 'month', account_id: account, amount: cents, effective_from: finFromValue(form)}];
  }, 'Saved');
}
