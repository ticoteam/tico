/* ui/app/finance-budgets.js — Finance › Budgets: every budget the viewer sees in one month, as a tree (each budget's
   children under it, the top level grouped by P&L line), with its platform, amount, spend, how much is used, its pace
   to the month's end and where it warns. A double-click on an amount changes it from the month shown (budget_update,
   dry run first, as on the P&L); "+ Budget" adds one under a line, for a teammate or a vendor (budget_create). A row
   opens the rail: amount by month, warn at, at limit, owner. Shown to the owner and whoever holds finance.budgets
   (finTabShown). Platform, pace_end, spent_from and as_of come from ad platform imports; a read without them draws
   those cells blank.
   Reads: GET /api/v2/finance/budgets?at=, /finance/pnl (the lines' names), /finance/reports/budget_vs_actual (a
   budget's amount by month). Writes: budget_create and budget_update through POST /api/v2/finance/actions.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The month shown, the search and the row picked, kept for the visit.
const FIN_BUD = {month: '', q: '', sel: ''};
const FIN_PLATFORM = {google_ads: 'Google Ads', meta_ads: 'Meta Ads'};
const FIN_BUD_PERIODS = [['month', 'Month'], ['quarter', 'Quarter'], ['year', 'Year']];
const FIN_BUD_HISTORY = 6;   // months of amounts in the rail

function finBudgetsPage() {
  const caps = finCaps(), B = FIN_BUD;
  if (!B.month) B.month = finToday().slice(0, 7);
  FIN.bud = {rows: [], lines: {}, kids: new Map(), hist: null, load: 0};
  $('#fin-body').innerHTML = `<div class="fin-bar"><label class="fin-inline"><input type="month" data-fin-month value="${B.month}" aria-label="Month"></label>
      <label class="fin-search"><span class="nav-icon" aria-hidden="true">search</span><input type="search" data-fin-q value="${esc(B.q)}" placeholder="Search budgets" aria-label="Search budgets"></label>
      <span class="spacer"></span>${caps.may('finance.budgets') ? '<button type="button" class="primary" data-fin-bud-add>+ Budget</button>' : ''}</div>`
    + finSplit(`<div class="fin-scroll" id="fin-bud">${skelRows(6, {dot: false})}</div>`, '', 'Selected budget');
  const body = $('#fin-body');
  body.querySelector('[data-fin-month]').onchange = ev => { if (ev.target.value) { B.month = ev.target.value; void finBudLoad(); } };
  body.querySelector('[data-fin-q]').oninput = ev => { B.q = ev.target.value.trim().toLowerCase(); finBudDraw(); };
  body.onclick = ev => {
    if (ev.target.closest('[data-fin-bud-add]')) { finBudAdd(); return; }
    const tr = ev.target.closest('tr[data-fin-bud]');
    if (!tr || ev.target.closest('input')) return;
    // Marked in place, not redrawn, so a double-click on the amount still finds its cell.
    B.sel = tr.dataset.finBud;
    body.querySelectorAll('tr[data-fin-bud]').forEach(r => r.classList.toggle('fin-on', r === tr));
    finBudRail();
  };
  body.ondblclick = ev => { const td = ev.target.closest('td[data-fin-bud-amt]'); if (td) finBudEdit(td); };
  body.onkeydown = ev => {
    const td = ev.target.closest?.('td[data-fin-bud-amt]');
    if (td && ev.key === 'Enter' && ev.target === td) { ev.preventDefault(); finBudEdit(td); }
  };
  void finBudLoad();
}

async function finBudLoad() {
  const state = FIN.bud, load = ++state.load, caps = finCaps(), B = FIN_BUD;
  const [a, z] = finMonthBounds(B.month), today = finToday();
  // This month reads as of today; any other as of its first day (the version in effect then).
  const at = B.month === today.slice(0, 7) ? today : a;
  const view = caps.company ? 'company' : 'expenses';
  const [bud, pnl] = await Promise.allSettled([get(`/v2/finance/budgets?at=${at}${finAs()}`, {fresh: true}),
    get(`/v2/finance/pnl?period=month&start=${a}&end=${z}&view=${view}${finAs()}`, {fresh: true})]);
  if (FIN?.bud !== state || state.load !== load || !$('#fin-bud')) return;
  if (bud.status === 'rejected') { $('#fin-bud').innerHTML = finFail(bud.reason); finRail(''); return; }
  state.read = bud.value || {};
  state.rows = (state.read.budgets || []).filter(b => b.active);
  state.lines = Object.fromEntries((pnl.status === 'fulfilled' ? pnl.value?.rows || [] : []).map(r => [r.account_id, r]));
  state.hist = null;
  const ids = new Set(state.rows.map(b => b.id));
  state.kids = new Map();
  for (const b of state.rows) if (b.parent_id && ids.has(b.parent_id)) (state.kids.get(b.parent_id) || state.kids.set(b.parent_id, []).get(b.parent_id)).push(b);
  finBudDraw(); finBudRail();
}

// The table's rows in order: per line (by code), a group row when the line's top isn't one budget of its own, then each
// budget and its children; budgets with no line known to the viewer last, under Other budgets.
function finBudOrder() {
  const state = FIN.bud, ids = new Set(state.rows.map(b => b.id)), lines = state.lines;
  const roots = state.rows.filter(b => !b.parent_id || !ids.has(b.parent_id));
  const groups = new Map();
  for (const b of roots) {
    const key = !b.catch_all && b.account_id && lines[b.account_id] ? b.account_id : '';
    (groups.get(key) || groups.set(key, []).get(key)).push(b);
  }
  const keys = [...groups.keys()].sort((x, y) => (x ? 0 : 1) - (y ? 0 : 1)
    || String(lines[x]?.code || '').localeCompare(String(lines[y]?.code || '')));
  const byName = (x, y) => String(x.name).localeCompare(String(y.name));
  const out = [];
  for (const key of keys) {
    const tops = groups.get(key).sort(byName);
    const own = key && tops.length === 1 && !tops[0].team && !tops[0].teammate && !tops[0].vendor;
    if (!own) out.push({group: key ? lines[key].name : 'Other budgets', key});
    const walk = (b, depth) => { out.push({b, depth}); for (const k of (state.kids.get(b.id) || []).sort(byName)) walk(k, depth + 1); };
    for (const b of tops) walk(b, own ? 0 : 1);
  }
  return out;
}
// What the search matches: a budget's name, teammate, team, vendor or line.
function finBudMatch(b) {
  const q = FIN_BUD.q;
  if (!q) return true;
  const line = FIN.bud.lines[b.account_id]?.name || '';
  return [b.name, b.vendor, line, b.teammate ? finWho(b.teammate) : '', b.team ? finTeamName(b.team) : '']
    .some(t => String(t || '').toLowerCase().includes(q));
}
const finBudPlatform = b => FIN_PLATFORM[b.platform] || '';
// A team's row: a team budget, or a teammate's whose children are their reports' budgets.
const finBudTeamRow = b => !!b.team && !b.teammate && !b.vendor || (!!b.teammate && (FIN.bud.kids.get(b.id) || []).length > 0 && !b.vendor);
// How much is used, 0-100+; null with no amount or no spend in view.
const finBudPct = b => {
  const s = b.state || {};
  if (s.actual == null || !s.amount) return null;
  return Math.round(s.actual / s.amount * 100);
};
const finBudMeterCls = (b, pct) => pct >= 100 ? ' bad' : pct >= (b.warn_pct || 100) ? ' warn' : '';
// "Platform data as of 9:40 AM" (or "Nov 4, 9:40 AM" on another day) when ad platform spend is in the figure.
function finBudAsOf(b) {
  const s = b.state || {};
  if (!s.as_of || !['platform', 'mixed'].includes(s.spent_from)) return '';
  const d = new Date(s.as_of), time = d.toLocaleTimeString('en-US', {hour: 'numeric', minute: '2-digit'});
  return 'Platform data as of ' + (finIso(d) === finToday() ? time : `${finDayLabel(finIso(d))}, ${time}`);
}
const finBudMay = () => finCaps()?.may('finance.budgets');
// An amount changes from the month shown, this month or a later one, on a monthly budget (as on a line's page).
const finBudEditable = b => !!b && b.period === 'month' && !b.catch_all && FIN_BUD.month >= finToday().slice(0, 7) && finBudMay();
const finBudAmount = b => {
  const amount = b.state?.amount;
  if (amount == null) return '<span class="muted">No amount</span>';
  return finUsd(amount) + (b.period !== 'month' ? ` <span class="muted">/ ${FIN_PER[b.period] || b.period}</span>` : '');
};

function finBudDraw() {
  const state = FIN.bud, box = $('#fin-bud'); if (!box || !state.read) return;
  const B = FIN_BUD, order = finBudOrder();
  // With a search: the matches and the budgets above them, and a group row only over what it still holds.
  let shown = order;
  if (B.q) {
    const keep = new Set(), byId = new Map(state.rows.map(b => [b.id, b]));
    for (const b of state.rows) if (finBudMatch(b)) for (let x = b; x && !keep.has(x.id); x = byId.get(x.parent_id)) keep.add(x.id);
    shown = order.filter((r, i) => r.b ? keep.has(r.b.id) : order.slice(i + 1).find(n => !n.b || keep.has(n.b.id))?.b);
  }
  const [y, m] = B.month.split('-').map(Number), last = new Date(y, m, 0).getDate();
  const rows = shown.map(r => {
    if (!r.b) return `<tr class="fin-grp"><td colspan="7">${esc(r.group)}</td></tr>`;
    const b = r.b, s = b.state || {}, pct = finBudPct(b), kids = (state.kids.get(b.id) || []).length;
    const platform = finBudPlatform(b) ? esc(finBudPlatform(b)) : finBudTeamRow(b) ? '<span class="muted">team</span>' : '';
    const edit = finBudEditable(b);
    const pace = s.pace_end == null ? '' : s.amount != null && s.pace_end > s.amount ? `<span class="pill waiting">${finUsd(s.pace_end)}</span>` : finUsd(s.pace_end);
    const asOf = finBudAsOf(b);
    return `<tr data-fin-bud="${esc(b.id)}" style="--d:${r.depth}"${B.sel === b.id ? ' class="fin-on"' : ''}><td>${esc(b.name)}</td><td>${platform}</td>
      <td class="fin-n${edit ? ' fin-edit' : ''}"${edit ? ` data-fin-bud-amt="${esc(b.id)}" tabindex="0" aria-label="${esc(b.name)} budget, ${esc(FIN_MONTH[m - 1])}"` : ''}>${finBudAmount(b)}</td>
      <td class="fin-n"${asOf ? ` title="${esc(asOf)}"` : ''}>${s.actual == null ? '<span class="muted">—</span>' : finUsd(s.actual)}</td>
      <td>${pct == null ? '' : `<span class="fin-bar-m${finBudMeterCls(b, pct)}" role="img" aria-label="${pct}% used"><i style="width:${Math.min(100, pct)}%"></i></span>`}</td>
      <td class="fin-n">${pace}</td><td class="fin-n">${kids || s.amount == null ? '' : esc(b.warn_pct + '%')}</td></tr>`;
  }).join('');
  const head = `<tr><th>Budget</th><th>Platform</th><th class="fin-n">Budget / mo${finExcl(state.read)}</th><th class="fin-n">Spent</th><th>Used</th><th class="fin-n">Pace by ${FIN_MON[m - 1]} ${last}</th><th class="fin-n">Warn at</th></tr>`;
  box.innerHTML = !state.rows.length ? finNote('No budgets yet.') : !rows ? finNote('No budgets match.')
    : `<table class="fin-t fin-bud-t"><thead>${head}</thead><tbody>${rows}</tbody></table>`;
}

function finBudRail() {
  const state = FIN.bud, B = FIN_BUD;
  const b = state.rows.find(x => x.id === B.sel);
  if (!b) { finRail(''); return; }
  const s = b.state || {}, pct = finBudPct(b), month = FIN_MON[Number(B.month.slice(5, 7)) - 1], line = state.lines[b.account_id]?.name;
  const per = {month: 'month', quarter: 'quarter', year: 'year', once: 'once'}[b.period] || b.period;
  const hist = state.hist?.rows?.[b.id];
  finRail(`<div class="fin-rail-title"><h2>${esc(b.name)}</h2><span class="fin-big">${s.amount == null ? '' : finUsd(s.amount)}</span></div>
    ${pct == null ? '' : `<div class="fin-bar-m${finBudMeterCls(b, pct)}" aria-hidden="true"><i style="width:${Math.min(100, pct)}%"></i></div><div class="muted fin-small">${finUsd(s.actual)} of ${finUsd(s.amount)} · ${month}</div>`}
    ${line ? finKv('Line', esc(line)) : b.catch_all ? finKv('Line', 'Everything else') : ''}${b.team ? finKv('Team', esc(finTeamName(b.team))) : ''}
    ${b.teammate ? finKv('Teammate', esc(finWho(b.teammate))) : ''}${b.vendor ? finKv('Vendor', esc(b.vendor)) : ''}${finBudPlatform(b) ? finKv('Platform', esc(finBudPlatform(b))) : ''}
    ${finKv('Budget' + finExcl(s), s.amount == null ? '<span class="muted">No amount</span>' : `${finUsd(s.amount)} <span class="muted">/ ${esc(per)}</span>${finCardLimitsTag(b)}`)}
    ${s.remaining != null ? finKv('Left', finUsd(s.remaining)) : ''}${s.pace_end != null ? finKv('Pace', finUsd(s.pace_end), s.amount != null && s.pace_end > s.amount ? 'fin-bad' : '') : ''}
    ${finBudAsOf(b) ? finKv('Platform data', esc(finBudAsOf(b).replace(/^Platform data as of /, ''))) : ''}
    ${finKv('Warn at', esc(b.warn_pct + '%'))}${finKv('At limit', esc((FIN_AT_LIMIT[b.at_limit] || b.at_limit || '') + (b.at_limit === 'ask' && b.owner ? ' ' + finWho(b.owner) : '')))}
    ${b.owner ? finKv('Owner', esc(finWho(b.owner))) : ''}
    <h3 class="fin-h">Amount by month</h3><div data-fin-bud-hist>${!state.hist ? '<p class="muted fin-note">…</p>' : hist?.length
      ? hist.map(([label, v]) => finKv(label, v == null ? '<span class="muted">—</span>' : finUsd(v))).join('') : finNote('No amounts yet.')}</div>`);
  if (!state.hist) void finBudHistory();
}
// Each budget's amount by month, the last few months to the one shown: one budget_vs_actual read per month shown.
async function finBudHistory() {
  const state = FIN.bud, load = state.load;
  if (state.histAsk === load) return;
  state.histAsk = load;
  const [y, m] = FIN_BUD.month.split('-').map(Number), a = finIso(new Date(y, m - FIN_BUD_HISTORY, 1)), z = finMonthBounds(FIN_BUD.month)[1];
  let rows = {};
  try {
    const r = await get(`/v2/finance/reports/budget_vs_actual?period=month&start=${a}&end=${z}&top_n=200${finAs()}`, {fresh: true});
    const cols = r?.columns || [];
    for (const b of r?.budgets || []) rows[b.id] = cols.map((c, i) => [FIN_MON[finDate(c.start).getMonth()], b.budget?.[i] ?? null]).reverse();
  } catch { rows = {}; }
  if (FIN?.bud !== state || state.load !== load) return;
  state.hist = {rows};
  finBudRail();
}

// An amount in place: double-click (or Enter on the cell), type, Enter saves from the month shown, Esc puts it back.
function finBudEdit(td) {
  if (td.querySelector('input')) return;
  const state = FIN.bud, b = state.rows.find(x => x.id === td.dataset.finBudAmt), rows = state.rows;
  if (!finBudEditable(b)) return;
  const was = b.state?.amount ?? null, month = FIN_BUD.month;
  td.innerHTML = `<input class="fin-cell-in" inputmode="decimal" autocomplete="off" value="${finCentsField(was)}" aria-label="${esc(b.name)} budget, ${esc(FIN_MONTH[Number(month.slice(5, 7)) - 1])}">`;
  const input = td.querySelector('input');
  input.focus(); input.select();
  let done = false;
  const finish = async save => {
    if (done) return; done = true;
    const cents = finParseCents(input.value);
    if (!save || cents == null || cents === was || state.rows !== rows) { finBudDraw(); return; }
    input.disabled = true;
    await finBudgetDecide('budget_update', {budget_id: b.id, amount: cents, effective_from: month + '-01'},
      {title: b.name, change: finBudgetChange(FIN_MONTH[Number(month.slice(5, 7)) - 1], was, cents), alive: () => FIN?.bud === state,
        saved: () => void finBudLoad(), undo: finBudDraw});
  };
  input.onkeydown = ev => {
    if (ev.key === 'Enter') { ev.preventDefault(); void finish(true); }
    if (ev.key === 'Escape') { ev.preventDefault(); void finish(false); }
  };
  input.onblur = () => void finish(true);
}

// + Budget: a line, optionally under a budget there, for a teammate or a vendor; amount, warn at, period and the month
// it starts. A teammate's or vendor's budget sits under the line's (a budget tree starts at a line), which is the
// default Under. The name is the teammate's, else the vendor's, else the line's.
function finBudAdd() {
  const state = FIN.bud, lines = Object.values(state.lines).filter(l => l.type === 'expense' && !l.private)
    .sort((x, y) => String(x.code).localeCompare(String(y.code)));
  if (!lines.length) { toast('No lines to budget', true); return; }
  const mates = ((FIN?.acc || FIN_ACCESS)?.teammates || []).map(t => t.teammate).filter(Boolean)
    .sort((x, y) => finWho(x).localeCompare(finWho(y)));
  const vendors = [...new Set(['Google Ads', 'Meta Ads', ...state.rows.map(b => b.vendor).filter(Boolean)])];
  const opt = (v, l, on = false) => `<option value="${esc(v)}"${on ? ' selected' : ''}>${esc(l)}</option>`;
  const dlg = finDecideDialog('Budget', `<label>Line<select name="account_id">${lines.map((l, i) => opt(l.account_id, l.name, i === 0)).join('')}</select></label>
    <label>Under<select name="parent_id"></select></label>
    <label>Teammate<select name="teammate">${opt('', '—')}${mates.map(t => opt(t, finWho(t))).join('')}</select></label>
    <label>Vendor<input name="vendor" maxlength="80" autocomplete="off" list="fin-bud-vendors"><datalist id="fin-bud-vendors">${vendors.map(v => `<option value="${esc(v)}">`).join('')}</datalist></label>
    ${finAmountField('Amount')}
    <label>Warn at %<input name="warn_pct" type="number" min="1" max="100" step="1" value="80" required></label>
    <label>Period<select name="period">${FIN_BUD_PERIODS.map(([v, l]) => opt(v, l, v === 'month')).join('')}</select></label>
    ${finFromField(FIN_BUD.month, true)}`, 'Add', form => {
    const f = form.elements, account = f.account_id.value, parent = state.rows.find(b => b.id === f.parent_id.value);
    const teammate = f.teammate.value, vendor = f.vendor.value.trim(), cents = finAmountValue(form);
    const warn = Number(f.warn_pct.value);
    if (!Number.isInteger(warn) || warn < 1 || warn > 100) throw new Error('Warn at is 1 to 100');
    const name = teammate ? finWho(teammate) : vendor || state.lines[account]?.name || 'Budget';
    const payload = {name, period: f.period.value, account_id: account, amount: cents, warn_pct: warn, effective_from: finFromValue(form, true)};
    if (parent) payload.parent_id = parent.id;
    if (teammate) payload.teammate = teammate;
    if (vendor) payload.vendor = vendor;
    return ['budget_create', payload];
  }, 'Saved');
  const form = dlg.querySelector('form'), f = form.elements;
  // Under: the budgets on the line picked or a line above it (a child is on its parent's line or one under it).
  const unders = () => {
    const above = new Set();
    for (let id = f.account_id.value; id && !above.has(id); id = state.lines[id]?.parent_id) above.add(id);
    const fits = state.rows.filter(b => !b.catch_all && above.has(b.account_id));
    const top = finLineBudget(f.account_id.value, state.rows);
    f.parent_id.innerHTML = opt('', '—') + fits.map(b => opt(b.id, b.name, b === top)).join('');
    periodOf();
  };
  // A child budget keeps its parent's period.
  const periodOf = () => {
    const parent = state.rows.find(b => b.id === f.parent_id.value);
    if (parent && FIN_BUD_PERIODS.some(([v]) => v === parent.period)) f.period.value = parent.period;
  };
  f.account_id.addEventListener('change', unders);
  f.parent_id.addEventListener('change', periodOf);
  unders();
}
