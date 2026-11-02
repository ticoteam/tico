/* ui/app/growth-page.js — The Growth page (#/growth, docs/growth.md): one page of numbers telling the story of growth,
   from GET /api/v2/growth/numbers. In order: North Star, Growth, Cost, Retention, Value, Channels (growth-channels.js)
   and Tried, then the month picker, Settings and the link to Sources (growth-sources.js). A stat shows the picked month
   with its change from the month before and a trend over the whole window (growth-charts.js); a stat or section with no
   data is not drawn. Also what those files, Brand (brand-page.js) and the KPI views on Goals share: formats, a small
   dialog, form fields, the owner picker. Old addresses land on what replaced them. Every write is the server's to allow.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const GR_ROUTE = '#/growth', GR_SOURCES = GR_ROUTE + '/sources', BRAND_ROUTE = '#/strategy/brand', GR_GOALS = '#/strategy/goals';
// Where Growth and Go-to-market lived, under Strategy: redesignRoute sends them on.
const GR_OLD_ROUTE = '#/strategy/growth', GTM_OLD_ROUTE = '#/strategy/gtm';
const GR_WINDOW = 24;     // months the page reads
const GR_TRIED_SHOWN = 5;
let GR = null;            // the open Growth page: {load, sub, month, pick, showAll, triedAll, data, econ}
let GR_LOAD = 0;
const GR_STAGES = [['reach', 'Reach'], ['acquire', 'Acquire'], ['activate', 'Activate'], ['retain', 'Retain'], ['refer', 'Refer']];
const GR_ACQUISITION = ['reach', 'acquire', 'activate'];
const GR_CH_STAGE = {idea: 'Idea', testing: 'Testing', scaling: 'Scaling', paused: 'Paused'};
const GR_MOVES = {started_at: 'Started', won: 'Won', lost: 'Lost', inconclusive: 'Inconclusive', dropped: 'Dropped'};

// ----------------------------------------------------------------- shared formats and pieces
// Who may change company-level growth records: the owner or an admin (the server checks and is the one that enforces it).
const grAdmin = () => S.me?.role === 'owner' || !!S.me?.bot_admin;
// An owner ref as Tico writes them: bot:<slug>, human:<id>, or a bare bot slug.
const grWho = ref => {
  const s = String(ref || '');
  if (!s) return '';
  if (s.includes(':')) return actorLabel(s);
  const bot = botByRef(s);
  return bot ? botDisplayName(bot.id) : personDisplay(s);
};
function grUsd(v) {
  const n = Number(v), a = Math.abs(n);
  return (n < 0 ? '−$' : '$') + (a >= 1e5 ? grShort(a) : a.toLocaleString('en-US', {maximumFractionDigits: a < 10 ? 2 : 0}));
}
const grPct = v => `${Math.round(Number(v))}%`;
const grX = (v, d = 1) => Number(v).toLocaleString('en-US', {maximumFractionDigits: d});
const grRatio = v => `${grX(v)}×`;
const GR_MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const grMonth = m => { const [y, n] = String(m || '').split('-').map(Number); return n ? `${GR_MON[n - 1]} ${y}` : String(m || ''); };
const grMonthShort = m => { const [y, n] = String(m || '').split('-').map(Number); return n ? `${GR_MON[n - 1]} ${String(y).slice(2)}` : ''; };
const grDay = iso => {
  if (!iso) return '';
  const d = new Date(String(iso).length <= 10 ? iso + 'T12:00:00' : iso);
  if (isNaN(d)) return String(iso);
  return `${GR_MON[d.getMonth()]} ${d.getDate()}${d.getFullYear() === new Date().getFullYear() ? '' : `, ${d.getFullYear()}`}`;
};
const grKpiVal = k => k?.latest == null ? '—' : kpiNum(k.latest, k.unit);
const grTaskHref = id => '#/task/' + encodeURIComponent(id);
const grGoalHref = id => `${GR_GOALS}?kpi=${encodeURIComponent(id)}`;
const grFail = e => `<p class="muted gr-note">${esc(e?.status === 403 ? 'Not in your view.' : e?.message || 'Did not load.')}</p>`;
const grOpts = (pairs, value, blank = '') => (blank !== null ? `<option value="">${esc(blank)}</option>` : '')
  + pairs.map(([v, l]) => `<option value="${esc(v)}"${String(v) === String(value ?? '') ? ' selected' : ''}>${esc(l)}</option>`).join('');
// A segmented switch: [[value, label]], the chosen one aria-checked.
const grSeg = (name, label, options, value) => `<div class="theme-seg gr-seg" role="radiogroup" aria-label="${esc(label)}" data-gr-seg="${esc(name)}">${options.map(([v, l]) =>
  `<button type="button" role="radio" data-v="${esc(v)}" aria-checked="${v === value}">${esc(l)}</button>`).join('')}</div>`;
const grCrumbs = (base, baseLabel, title, tools = '') => `<div class="gr-crumbs"><a href="${base}">${esc(baseLabel)}</a><span class="muted" aria-hidden="true">/</span><h2>${esc(title)}</h2><span class="spacer"></span>${tools}</div>`;
// A labelled field: the caption over the control.
const grField = (label, control, cls = '') => `<label class="gr-f${cls ? ' ' + cls : ''}"><span>${esc(label)}</span>${control}</label>`;
const grNum = (v, d = null) => { const t = String(v ?? '').trim().replace(/[$,%\s]/g, ''); if (t === '') return d; const n = Number(t); return Number.isFinite(n) ? n : d; };
// The people and bots a row can belong to, as human:<id> and bot:<id>.
function grOwnerOpts(selected) {
  const sel = String(selected || ''), picked = sel.startsWith('human:') ? '' : botByRef(sel)?.id;
  const people = (S.people || []).filter(p => !p.hidden).slice().sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)));
  const bots = (S.emps || []).filter(e => e.status !== 'archived').slice().sort((a, b) => String(a.display_name || a.slug).localeCompare(String(b.display_name || b.slug)));
  return `<option value="">—</option><optgroup label="People">${people.map(p => `<option value="human:${esc(p.id)}"${sel === 'human:' + p.id ? ' selected' : ''}>${esc(p.name || p.id)}</option>`).join('')}</optgroup>
    <optgroup label="Bots">${bots.map(e => `<option value="bot:${esc(e.id)}"${picked === e.id ? ' selected' : ''}>${esc(e.display_name || e.slug)}</option>`).join('')}</optgroup>`;
}
// Retire asks once more on the same button (no window.confirm); `after` runs once it is retired.
function grRetireButton(btn, dlg, path, after) {
  btn.onclick = async () => {
    if (!btn.dataset.armed) { btn.dataset.armed = '1'; btn.textContent = 'Retire for good'; return; }
    btn.disabled = true;
    try { await post(path, {}); toast('Retired'); dlg.close(); after?.(); }
    catch (e) { toast(e.message || 'Not retired', true); btn.disabled = false; }
  };
}

// A small dialog: a title, a form, Cancel and the primary button. `onSave(form)` throws to show an error.
function grDialog(title, body, saveLabel, onSave) {
  document.querySelectorAll('dialog.gr-dlg').forEach(d => d.close());
  const dlg = document.createElement('dialog');
  dlg.className = 'tmodal gr-dlg';
  dlg.setAttribute('aria-label', title);
  dlg.innerHTML = `<div class="tmodal-head"><b>${esc(title)}</b><span class="spacer"></span>
      <button type="button" class="ghost tmodal-x" data-close aria-label="Close">✕</button></div>
    <form class="tmodal-body gr-form" novalidate>${body}<p class="err" data-error hidden></p>
      <div class="gr-foot"><button type="button" class="ghost" data-close>Cancel</button><button type="submit" class="primary">${esc(saveLabel)}</button></div></form>`;
  document.body.appendChild(dlg);
  dlg.querySelectorAll('[data-close]').forEach(b => { b.onclick = () => dlg.close(); });
  dlg.onclose = () => dlg.remove();
  const form = dlg.querySelector('form'), err = dlg.querySelector('[data-error]'), save = dlg.querySelector('[type=submit]');
  form.onsubmit = async ev => {
    ev.preventDefault(); err.hidden = true; save.disabled = true;
    try { if (await onSave(form) === false) save.disabled = false; else dlg.close(); }
    catch (e) { err.textContent = e.message || 'Not saved'; err.hidden = false; save.disabled = false; }
  };
  dlg.showModal();
  form.querySelector('input:not([type=hidden]):not([type=checkbox]),select,textarea')?.focus();
  return dlg;
}
const grChecks = (name, pairs, on = []) => `<div class="gr-checks" role="group">${pairs.map(([v, l]) =>
  `<label class="gr-check"><input type="checkbox" name="${esc(name)}" value="${esc(v)}"${on.includes(v) ? ' checked' : ''}>${esc(l)}</label>`).join('')}</div>`;
const grChecked = (form, name) => [...form.querySelectorAll(`input[name="${CSS.escape(name)}"]:checked`)].map(i => i.value);

// ----------------------------------------------------------------- the section
// After every route: forget the Growth and Brand pages once off them, so a late answer for one never draws over
// another (each page's reads check that its own state is still the open one).
window.growthStop = function growthStop() {
  const r = S.route || '';
  gkvStop(r);
  const offGrowth = !rdUnder(r, GR_ROUTE), offBrand = !rdUnder(r, BRAND_ROUTE);
  if (!(GR && offGrowth) && !(BR && offBrand)) return;
  GR_LOAD++;
  if (offGrowth) GR = null;
  if (offBrand) BR = null;
  if (offGrowth && offBrand) document.querySelectorAll('dialog.gr-dlg').forEach(d => d.close());
};
// Old addresses: what was on Growth's tabs is on this one page, and who we sell to and how went to Strategy.
function grMoved(parts) {
  if (!parts[0] || parts[0] === 'sources') return '';
  if (parts[0] === 'gtm') return parts[1] === 'sources' ? GR_SOURCES : '#/strategy';
  return ['buyers', 'segments'].includes(parts[0]) ? '#/strategy' : GR_ROUTE;
}
function pageGrowth() {
  const [path, query] = (S.route || '').split('?');
  const parts = path.slice(GR_ROUTE.length + 1).split('/'), sub = parts[0] || '';
  const moved = grMoved(parts);
  if (moved) { location.replace(moved); return; }
  document.querySelectorAll('dialog.gr-dlg').forEach(d => d.close());
  const load = ++GR_LOAD;
  GR = {load, sub, month: new URLSearchParams(query || '').get('m') || '', pick: null, showAll: false, triedAll: false, data: null, econ: null};
  const main = $('#main');
  main.classList.add('strategy-layout', 'growth-layout');
  const back = sub === 'sources' ? `<a class="gr-back" href="${GR_ROUTE}" data-gr-back>Growth</a><span class="muted" aria-hidden="true">/</span>` : '';
  main.innerHTML = `<div class="strat-head rd-strat-head gr-head">${back}<h1>${sub === 'sources' ? 'Sources' : 'Growth'}</h1><span class="spacer"></span>
    <div class="strat-actions" id="strat-actions"></div></div><div id="gr-body" class="gr-body">${skelRows(6, {dot: false})}</div>`;
  if (sub === 'sources') return pageGrowthSources(GR);
  void grRead(GR);
}

// ----------------------------------------------------------------- the numbers
async function grRead(state) {
  let data, econ;
  try {
    [data, econ] = await Promise.all([get(`/v2/growth/numbers?months=${GR_WINDOW}`, {fresh: true}),
      get('/v2/growth/economics' + (state.month ? '?month=' + encodeURIComponent(state.month) : ''), {fresh: true}).catch(() => null)]);
  } catch (e) { if (GR === state) $('#gr-body').innerHTML = grFail(e); return; }
  if (GR !== state) return;
  state.data = data; state.econ = econ;
  if (!data.months.includes(state.month)) state.month = data.months.at(-1) || '';
  grDraw(state);
}

// The window's every calendar month, oldest first: a month with no row is a gap, not a neighbour.
function grCalendar(months) {
  if (!months.length) return [];
  const ix = m => { const [y, n] = m.split('-').map(Number); return y * 12 + n - 1; };
  const out = [];
  for (let i = ix(months[0]); i <= ix(months.at(-1)); i++) out.push(`${String(Math.floor(i / 12)).padStart(4, '0')}-${String(i % 12 + 1).padStart(2, '0')}`);
  return out;
}
// One figure over the calendar: its values (null where unknown), the value in the picked month and the change from the
// month before. `carry` shows the newest earlier value when the picked month has none, saying which month: a cohort
// that joined this month has not reached 30 days yet.
function grFigure(rows, cal, month, field, carry = false) {
  const by = new Map(rows.map(r => [r.month, r[field]]));
  const values = cal.map(m => by.get(m) ?? null);
  let at = cal.indexOf(month);
  if (carry) while (at > 0 && values[at] == null) at--;
  return {values, at, value: values[at] ?? null, prev: at > 0 ? values[at - 1] : null, from: at >= 0 && cal[at] !== month ? cal[at] : ''};
}
// The change from `prev`: an arrow and a share, or points for a figure that is itself a share. `good` is the direction
// that is good news; with none, it stays neutral.
function grDelta(value, prev, {good = null, points = false} = {}) {
  if (value == null || prev == null) return '';
  const diff = Number(value) - Number(prev), dir = Math.abs(diff) < 1e-9 ? 0 : Math.sign(diff);
  const size = points ? `${grX(Math.abs(diff), 0)} ${Math.round(Math.abs(diff)) === 1 ? 'pt' : 'pts'}` : Number(prev) ? `${grX(Math.abs(diff / Number(prev)) * 100, 0)}%` : grX(Math.abs(diff));
  const tone = !dir || !good ? '' : (dir > 0) === (good === 'up') ? ' gr-d-good' : ' gr-d-bad';
  return `<span class="gr-d${tone}" aria-label="${['Down', 'Flat', 'Up'][dir + 1]} ${esc(size)}"><span aria-hidden="true">${['↓', '→', '↑'][dir + 1]}</span> ${esc(size)}</span>`;
}
const grStat = ({key, label, value, delta = '', spark = '', href = '', note = ''}) => {
  const inner = `<span class="gr-stat-l">${esc(label)}</span><span class="gr-stat-v">${esc(value)}</span>
    <span class="gr-stat-d">${delta}${note ? `<span class="muted">${esc(note)}</span>` : ''}</span>${spark}`;
  return href ? `<a class="gr-stat" href="${href}" data-stat="${esc(key)}">${inner}</a>` : `<div class="gr-stat" data-stat="${esc(key)}">${inner}</div>`;
};
const GR_RETENTION = [['kept_d30', '30 days'], ['kept_d90', '90 days'], ['kept_d180', '180 days']].map(([field, label]) => ({field, label, fmt: grPct, good: 'up', points: true, carry: true}));
const GR_PARTS = [
  {key: 'cost', label: 'Cost', stats: [{field: 'spend', label: 'Spend', fmt: grUsd}, {field: 'bot_cost', label: 'Bot cost', fmt: grUsd}, {field: 'cac', label: 'CAC', fmt: grUsd, good: 'down'}]},
  {key: 'retention', label: 'Retention', stats: GR_RETENTION},
  {key: 'value', label: 'Value', stats: [{field: 'revenue_month', label: 'Revenue / customer', fmt: grUsd, good: 'up'}, {field: 'margin_pct', label: 'Margin', fmt: grPct, good: 'up', points: true},
    {field: 'ltv', label: 'LTV', fmt: grUsd, good: 'up', carry: true}, {field: 'ltv_cac', label: 'LTV:CAC', fmt: grRatio, good: 'up', carry: true}, {field: 'payback_months', label: 'Payback', fmt: v => `${grX(v)} mo`, good: 'down'}]},
];
const GR_NEW = {field: 'new_customers', label: 'New customers', fmt: v => grX(v, 0), good: 'up'};
function grSeriesStat(def, rows, cal, month) {
  const f = grFigure(rows, cal, month, def.field, def.carry);
  if (f.value == null) return '';
  return grStat({key: def.field, label: def.label, value: def.fmt(f.value), delta: grDelta(f.value, f.prev, def),
    spark: grSpark(f.values, f.at), note: f.from ? grMonthShort(f.from) : ''});
}
const grPart = (key, label, stats) => stats ? `<section class="gr-part" data-part="${key}"><h2 class="gr-k">${esc(label)}</h2><div class="gr-stats">${stats}</div></section>` : '';

// A KPI at the picked month: its last reading by then, and the one before it. A KPI read but with no complete period
// yet still has its latest.
function grKpiAt(k, month) {
  const series = k.series?.length ? k.series : k.latest != null ? [{at: k.latest_at || '', value: k.latest}] : [];
  let at = series.length - 1;
  if (month) while (at >= 0 && String(series[at].at).slice(0, 7) > month) at--;
  return {series, at, value: at >= 0 ? series[at].value : null, prev: at > 0 ? series[at - 1].value : null};
}
function grKpiStat(k, month) {
  const f = grKpiAt(k, month);
  if (f.value == null) return '';
  return grStat({key: k.id, label: k.name, value: kpiNum(f.value, k.unit), href: grGoalHref(k.id),
    delta: grDelta(f.value, f.prev, {good: k.growth_role === 'guardrail' ? null : 'up'}), spark: grSpark(f.series.map(p => p.value), f.at)});
}
const grHeroValue = (v, unit) => {
  const u = String(unit || '').trim();
  return !u || ['$', '€', '£', '%', 'pp'].includes(u) ? esc(kpiNum(v, u)) : `${esc(kpiNum(v))}<span class="gr-hero-u">${esc(kpiNum(v, u).split(' ').slice(1).join(' '))}</span>`;
};
function grNorthStar(d, month) {
  const k = d.kpis.north_star, f = k ? grKpiAt(k, month) : null;
  const hero = f?.value != null ? `<a class="gr-hero" href="${grGoalHref(k.id)}" data-stat="${esc(k.id)}"><span class="gr-hero-n">${esc(k.name)}</span>
      <span class="gr-hero-v">${grHeroValue(f.value, k.unit)}</span>
      <span class="gr-hero-d">${grDelta(f.value, f.prev, {good: 'up'})}${k.target != null ? `<span class="muted">Target ${esc(kpiNum(k.target, k.unit))}</span>` : ''}</span></a>
      ${f.series.length > 1 ? '<div class="gr-chart gr-chart-l" data-gr-ns tabindex="0" role="img" aria-label="' + esc(k.name) + '"></div>' : ''}` : '';
  const group = (label, list) => {
    const stats = list.map(x => grKpiStat(x, month)).join('');
    return stats ? `<div class="gr-sub"><h3 class="gr-sub-k">${esc(label)}</h3><div class="gr-stats">${stats}</div></div>` : '';
  };
  const inner = hero + group('Inputs', d.kpis.inputs) + group('Guardrails', d.kpis.guardrails);
  return inner ? `<section class="gr-part gr-ns" data-part="north-star"><h2 class="gr-k">North Star</h2><div class="card gr-ns-card">${inner}</div></section>` : '';
}
// The tried list: newest first, 5 until All.
function grTried(state) {
  const {markers, channels} = state.data;
  if (!markers.length) return '';
  const names = new Map(channels.map(c => [c.slug, c.name]));
  const shown = state.triedAll ? markers : markers.slice(0, GR_TRIED_SHOWN);
  const row = m => {
    const word = m.kind === 'started' ? 'started' : m.outcome, chans = (m.channels || []).map(s => names.get(s) || s);
    return `<li><a class="gr-try" href="${grTaskHref(m.task_id)}" data-marker="${esc(m.task_id)}"><span class="gr-try-t">${esc(m.title)}</span>
      <span class="gr-try-m"><span class="gr-mk gr-mk-${esc(word)}"><i aria-hidden="true"></i>${esc(GR_MOVES[word] || word)}</span><span>${esc(grDay(m.at))}</span>${chans.length ? `<span>${esc(chans.join(', '))}</span>` : ''}</span></a></li>`;
  };
  return `<section class="gr-part" data-part="tried"><h2 class="gr-k">Tried</h2><div class="card gr-list"><ul class="gr-ul">${shown.map(row).join('')}</ul>
    ${shown.length < markers.length ? '<button class="ghost gr-more" type="button" data-gr-tried-all>All</button>' : ''}</div></section>`;
}
const grBar = state => {
  const {months, may_edit: may} = state.data;
  const pick = months.length > 1 ? `<label class="gr-month"><span class="sr-only">Month</span><select data-gr-month>${months.slice().reverse().map(m =>
    `<option value="${esc(m)}"${m === state.month ? ' selected' : ''}>${esc(grMonth(m))}</option>`).join('')}</select></label>` : '';
  return `<div class="gr-bar gr-foot-bar">${pick}${may ? '<button class="ghost gr-set" type="button" data-gr-settings aria-label="Settings" title="Settings"><span class="nav-icon" aria-hidden="true">settings</span></button>' : ''}
    <span class="spacer"></span><a href="${GR_SOURCES}" data-gr-sources>Where numbers come from</a></div>`;
};
// The chart of one figure over the window's months: a point mid-month, a task tick on its day.
function grMonthChart(host, rows, cal, month, def, marks) {
  const pos = iso => {
    const d = new Date(iso), key = iso.slice(0, 7), i = cal.indexOf(key);
    return i < 0 ? -1 : i + (d.getUTCDate() - 1) / 31;
  };
  const f = grFigure(rows, cal, month, def.field, false);
  const every = Math.max(1, Math.ceil(cal.length / (host.clientWidth < 500 ? 4 : 7)));
  grChartMount(host, {fmt: def.fmt, domain: [0, cal.length], pick: f.at,
    points: cal.map((m, i) => ({x: i + .5, value: f.values[i], label: grMonth(m)})),
    axis: cal.map((m, i) => ({x: i + .5, label: grMonthShort(m), i})).filter(a => a.i === 0 || a.i === cal.length - 1 || (a.i % every === 0 && cal.length - a.i > every / 2)),
    ticks: marks.map(m => ({x: pos(m.at), title: m.title, when: grDay(m.at)})).filter(t => t.x >= 0)});
}
function grMountNorthStar(state) {
  const host = $('#gr-body [data-gr-ns]'), k = state.data.kpis.north_star;
  if (!host) return;
  const f = grKpiAt(k, state.month), at = p => Date.parse(p);
  const marks = state.data.markers.filter(m => m.goal_id && m.goal_id === k.goal_id);
  const x0 = at(f.series[0].at), x1 = Math.max(at(f.series.at(-1).at), ...marks.map(m => at(m.at)));
  const every = host.clientWidth < 500 ? 4 : 7, label = t => { const d = new Date(t); return grMonthShort(`${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`); };
  grChartMount(host, {fmt: v => kpiNum(v, k.unit), domain: [x0, x1], pick: f.at,
    points: f.series.map(p => ({x: at(p.at), value: p.value, label: grDay(p.at)})),
    axis: Array.from({length: every}, (_, i) => { const x = x0 + (x1 - x0) * i / (every - 1); return {x, label: label(x)}; }),
    ticks: marks.map(m => ({x: at(m.at), title: m.title, when: grDay(m.at)}))});
}
function grDraw(state) {
  const body = $('#gr-body'), d = state.data;
  if (!body || !d) return;
  const month = state.month, cal = grCalendar(d.months);
  patchHTML($('#strat-actions'), d.may_edit ? '<button class="primary" type="button" data-ch-add>+ Channel</button>' : '');
  const add = $('#strat-actions [data-ch-add]');
  if (add) add.onclick = () => void gcChannelDialog(state);
  const growth = grSeriesStat(GR_NEW, d.series, cal, month)
    + d.kpis.other.filter(k => GR_ACQUISITION.includes(k.stage)).map(k => grKpiStat(k, month)).join('');
  const parts = grNorthStar(d, month) + grPart('growth', 'Growth', growth)
    + GR_PARTS.map(p => grPart(p.key, p.label, p.stats.map(s => grSeriesStat(s, d.series, cal, month)).join(''))).join('');
  const tried = grTried(state);
  if (!parts && !d.channels.length && !tried) {
    body.innerHTML = `<p class="gr-empty">No numbers yet. <a href="${GR_SOURCES}" data-gr-sources>Where numbers come from</a></p>`;
    return;
  }
  body.innerHTML = `<div class="gr-page">${parts}${d.channels.length ? '<section class="gr-part" data-part="channels" id="gr-channels"></section>' : ''}${tried}${grBar(state)}</div>`;
  grMountNorthStar(state);
  if (d.channels.length) gcDraw(state);
  const all = body.querySelector('[data-gr-tried-all]');
  if (all) all.onclick = () => { state.triedAll = true; grDraw(state); };
  const sel = body.querySelector('[data-gr-month]');
  if (sel) sel.onchange = () => {
    state.month = sel.value;
    history.replaceState(history.state, '', `${GR_ROUTE}?m=${encodeURIComponent(state.month)}`);
    void grRead(state);
  };
  const set = body.querySelector('[data-gr-settings]');
  if (set) set.onclick = () => void gcSettings(state);
}
