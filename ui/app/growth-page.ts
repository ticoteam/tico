/* ui/app/growth-page.ts — The Growth page (#/growth, docs/growth.md): one read-only view of the one Growth read (GET
   /api/v2/growth/read, backend/growth_core.py). Tabs pick the scope (All and each segment), the header's month picker
   the month; then the headline, the channels, twelve months of trends, the latest cohorts' retention, Growth's KPIs and
   one line on the data's health. Every change happens in chat: the page's chat (page-chat.ts) is the Growth Manager's,
   and each message from it carries the scope and month on screen. A figure the read cannot give says why in a muted
   word, never 0. Also what Brand (brand-page.ts) and the KPI views on Goals share: formats, a small dialog, form
   fields. Old addresses land on what replaced them.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The read as backend/growth_core.py `read` answers it with detail=series,retention.
interface GpFig { value?: number | null; status?: string; verdict?: string | null; n?: number; cohort?: string; window?: string; with_effort?: number | null; retention?: number | null }
interface GpHeadline {
  signups?: GpFig; activated_rate?: GpFig; paid_rate?: GpFig; new_customers?: GpFig; cost?: GpFig; cac?: GpFig; cac_loaded?: GpFig; kept?: GpFig;
  ltv?: GpFig; ltv_cac?: GpFig; payback_months?: GpFig; value?: {kept_d30?: GpFig; cost_per_retained?: GpFig};
}
interface GpChannel {
  slug: string; name?: string; signups?: number; new_customers: number; new_share: number | null; owner?: string | null; budget?: number | null; over_budget?: number | null;
  cost?: number | null; cac?: number | null; status?: string; cost_share?: number | null; verdict?: string | null; with_effort?: number | null;
}
interface GpPoint { period: string; signups: number | null; new_customers: number | null; cost: number | null; cac: number | null; kept: number | null }
type GpWin = 'kept_d30' | 'kept_d90' | 'kept_d180';
type GpCohort = { cohort: string; entered: number } & Partial<Record<GpWin, {value: number | null; n: number; status: string}>>;
interface GpMetric { id: string; name: string; unit: string; value: number | null; estimate?: boolean }
interface GpHealth { unknown_share?: number | null; no_segment?: number; mismatch?: number; reconciled?: string; stale_sources?: string[] }
interface GpRead { scope: string; period: string; headline: GpHeadline; channels: GpChannel[]; health: GpHealth; metrics: GpMetric[]; series?: GpPoint[]; retention?: GpCohort[]; partial?: boolean }
interface GpSegment { slug: string; name: string }
interface GrState { load: number; scope: string; month: string; read: GpRead | null; segments: GpSegment[] }
// A KPI as the Goals views hand it over: its latest reading and unit.
type GrKpiVal = { latest?: number | null; unit?: string };
let GR: GrState | null = null;            // the open Growth page
let GR_LOAD = 0;
const GR_STAGES = [['reach', 'Reach'], ['acquire', 'Acquire'], ['activate', 'Activate'], ['retain', 'Retain'], ['refer', 'Refer']];

// Who may change company-level growth records: the owner or an admin (the server checks and is the one that enforces it).
const grAdmin = () => S.me?.role === 'owner' || !!S.me?.bot_admin;
// An owner ref as Tico writes them: bot:<slug>, human:<id>, or a bare bot slug.
const grWho = (ref: unknown) => {
  const s = String(ref || '');
  if (!s) return '';
  if (s.includes(':')) return actorLabel(s);
  const bot = botByRef(s);
  return bot ? botDisplayName(bot.id) : personDisplay(s);
};
function grUsd(v: unknown) {
  const n = Number(v), a = Math.abs(n);
  return (n < 0 ? '−$' : '$') + (a >= 1e5 ? grShort(a) : a.toLocaleString('en-US', {maximumFractionDigits: a < 10 ? 2 : 0}));
}
const grX = (v: unknown, d = 1) => Number(v).toLocaleString('en-US', {maximumFractionDigits: d});
const GR_MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const grMonth = (m: unknown) => { const [y, n] = String(m || '').split('-').map(Number); return n ? `${GR_MON[n - 1]} ${y}` : String(m || ''); };
const grDay = (iso: string | null | undefined) => {
  if (!iso) return '';
  const d = new Date(String(iso).length <= 10 ? iso + 'T12:00:00' : iso);
  if (Number.isNaN(d.valueOf())) return String(iso);
  return `${GR_MON[d.getMonth()]} ${d.getDate()}${d.getFullYear() === new Date().getFullYear() ? '' : `, ${d.getFullYear()}`}`;
};
const grKpiVal = (k: GrKpiVal | null | undefined) => k?.latest == null ? '—' : kpiNum(k.latest, k.unit);
// A read that failed says what happened and carries the fix: Try again draws the page again (none for a refusal).
const grFail = (e: unknown) => errorStatus(e) === 403 ? '<p class="muted gr-note">Not in your view.</p>'
  : `<p class="muted gr-note" role="alert">${esc(errorMessage(e, 'Did not load.'))} <button type="button" class="linkish tap-mark" data-gr-retry>Try again</button></p>`;
document.addEventListener('click', ev => { if (eventElement(ev)?.closest('#main [data-gr-retry]')) route(); });
const grOpts = (pairs: string[][], value: string | number | null | undefined, blank: string | null = '') => (blank !== null ? `<option value="">${esc(blank)}</option>` : '')
  + pairs.map(([v, l]) => `<option value="${esc(v)}"${String(v) === String(value ?? '') ? ' selected' : ''}>${esc(l)}</option>`).join('');
// A segmented switch: [[value, label]], the chosen one aria-checked.
const grSeg = (name: string, label: string, options: string[][], value: string | undefined) => `<div class="theme-seg gr-seg" role="radiogroup" aria-label="${esc(label)}" data-gr-seg="${esc(name)}">${options.map(([v, l]) =>
  `<button type="button" role="radio" data-v="${esc(v)}" aria-checked="${v === value}">${esc(l)}</button>`).join('')}</div>`;
// A labelled field: the caption over the control.
const grField = (label: string, control: string, cls = '') => `<label class="gr-f${cls ? ' ' + cls : ''}"><span>${esc(label)}</span>${control}</label>`;

// A small dialog: a title, a form, Cancel and the primary button (with no `saveLabel`, no foot: ✕ dismisses it, style
// guide "Overlays"). `onSave(form)` throws to show an error.
function grDialog(title: string, body: string, saveLabel: string | null | undefined, onSave: ((form: HTMLFormElement) => unknown) | null) {
  document.querySelectorAll<HTMLDialogElement>('dialog.gr-dlg').forEach(d => { d.close(); });
  const dlg = document.createElement('dialog');
  dlg.className = 'tmodal gr-dlg';
  dlg.setAttribute('aria-label', title);
  dlg.innerHTML = `<div class="tmodal-head"><b>${esc(title)}</b><span class="spacer"></span>
      <button type="button" class="ghost tmodal-x" data-close aria-label="Close">✕</button></div>
    <form class="tmodal-body gr-form" novalidate>${body}<p class="err" data-error hidden></p>
      ${saveLabel ? `<div class="gr-foot"><button type="button" class="ghost" data-close>Cancel</button><button type="submit" class="primary">${esc(saveLabel)}</button></div>` : ''}</form>`;
  document.body.appendChild(dlg);
  dlg.querySelectorAll<HTMLElement>('[data-close]').forEach(b => { b.onclick = () => dlg.close(); });
  dlg.onclose = () => dlg.remove();
  const form = dlg.querySelector<HTMLFormElement>('form'), err = dlg.querySelector<HTMLElement>('[data-error]'), save = dlg.querySelector<HTMLButtonElement>('[type=submit]');
  if (!form || !err) return dlg;   // G: the markup above always has both
  form.onsubmit = async ev => {
    ev.preventDefault();
    if (!save || !onSave) return;
    err.hidden = true; save.disabled = true;
    try { if (await onSave(form) === false) save.disabled = false; else dlg.close(); }
    catch (e) { err.textContent = errorMessage(e, 'Not saved'); err.hidden = false; save.disabled = false; }
  };
  dlg.showModal();
  form.querySelector<HTMLElement>('input:not([type=hidden]):not([type=checkbox]),select,textarea')?.focus();
  return dlg;
}

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
  if (offGrowth && offBrand) document.querySelectorAll<HTMLDialogElement>('dialog.gr-dlg').forEach(d => { d.close(); });
};
// Old addresses: who we sell to and how went to Strategy; every other old Growth address (Sources, a channel) is this page.
function grMoved(parts: string[]) {
  if (!parts[0]) return '';
  return ['buyers', 'segments'].includes(parts[0]) || (parts[0] === 'gtm' && parts[1] !== 'sources') ? '#/strategy' : GR_ROUTE;
}
const GR_MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/;
// The page's address for a scope and month; All and the read's own month (the last full one) are left out.
const gpHref = (scope: string, month: string) => {
  const q = new URLSearchParams();
  if (scope !== 'all') q.set('scope', scope);
  if (month) q.set('m', month);
  const text = q.toString();
  return GR_ROUTE + (text ? '?' + text : '');
};
const gpScopeName = (state: GrState) => state.scope === 'all' ? 'All' : state.segments.find(s => 'segment:' + s.slug === state.scope)?.name || state.scope;
// What each message from the page's chat carries: the scope and month on screen.
function gpChatItem(): PageChatItem | null {
  const state = GR, month = state?.read?.period || state?.month;
  return state && month ? {kind: 'growth', id: `${state.scope}@${month}`, name: `${gpScopeName(state)} · ${grMonth(month)}`} : null;
}

async function pageGrowth() {
  const [path = '', query] = (S.route || '').split('?');
  const moved = grMoved(path.slice(GR_ROUTE.length + 1).split('/'));
  if (moved) { location.replace(moved); return; }
  document.querySelectorAll<HTMLDialogElement>('dialog.gr-dlg').forEach(d => { d.close(); });
  const q = new URLSearchParams(query || ''), m = q.get('m') || '';
  const state: GrState = {load: ++GR_LOAD, scope: q.get('scope') || 'all', month: GR_MONTH_RE.test(m) ? m : '', read: null, segments: []};
  GR = state;
  const main = $('#main');
  main.classList.add('strategy-layout', 'growth-layout');
  main.innerHTML = pageHead({title: 'Growth', hook: 'gr-head', more: PAGE_HEAD_COPY}) + `<div id="gr-body" class="gr-body">${skelRows(6, {dot: false})}</div>`;
  pageChatUse('growth', {item: gpChatItem});
  const read = `/v2/growth/read?scope=${encodeURIComponent(state.scope)}&detail=series,retention${state.month ? '&period=' + state.month : ''}`;
  try {
    const [segs, out] = await Promise.all([get<{segments?: GpSegment[]}>('/v2/growth/segments'), get<GpRead>(read)]);
    state.segments = segs.segments || []; state.read = out;
  } catch (e) { if (GR === state) $('#gr-body').innerHTML = grFail(e); return; }
  if (GR !== state) return;
  main.innerHTML = gpHead(state, state.read) + `<div id="gr-body" class="gr-body gp">${gpBody(state.read)}</div>`;
  pageChatUse('growth', {item: gpChatItem});   // the chip names the read's month now
}
// Twelve months back from the last full one, whichever month is picked, so an older pick keeps the same list.
const gpYear = () => Array.from({length: 12}, (_, k) => { const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - 1 - k); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; });
// The header: All and a tab per segment (none without segments), and the months to pick, newest first.
function gpHead(state: GrState, r: GpRead) {
  const tab = (scope: string, label: string) => ({label, href: gpHref(scope, state.month), cur: state.scope === scope});
  const tabs = state.segments.length ? [tab('all', 'All'), ...state.segments.map(s => tab('segment:' + s.slug, s.name))] : undefined;
  const months = [...new Set([r.period, ...gpYear()])].sort().reverse();
  const tools = `<label class="gp-month"><span class="sr-only">Month</span><select data-gp-month>${months.map(m =>
    `<option value="${esc(m)}"${m === r.period ? ' selected' : ''}>${esc(grMonth(m))}</option>`).join('')}</select>${r.partial ? ' ' + gpWord('partial') : ''}</label>`;
  return pageHead({title: 'Growth', hook: 'gr-head', tabs, tabsAttrs: 'aria-label="Segment"', tools, more: PAGE_HEAD_COPY});
}
// The picker may be folded into ⋯, which the header draws again: the page listens rather than the element.
document.addEventListener('change', ev => {
  const sel = eventElement(ev)?.closest<HTMLSelectElement>('#main select[data-gp-month]');
  if (sel && GR && GR_MONTH_RE.test(sel.value)) location.hash = gpHref(GR.scope, sel.value);
});

const GP_WORD: Record<string, string> = {too_few: 'too few', not_supplied: 'not supplied', no_spend: 'no spend', not_yet: 'not yet', no_customers: 'no customers', not_split: 'not split', partial: 'partial'};
const gpWord = (status: string | undefined) => `<span class="muted gp-word">${esc(GP_WORD[status || ''] || '—')}</span>`;
const gpCount = (v: number) => grX(v, 0);
const gpRate = (v: number) => `${grX(v * 100, v < 0.1 ? 1 : 0)}%`;
const GP_WINDOW: Record<string, string> = {d30: '30 days', d90: '90 days', d180: '180 days', d365: '365 days'};
const GP_ARROW: Record<string, string> = {up: '↑', down: '↓', flat: '→'};
// The verdict against the three months before; `good` is the direction that is good news (CAC's is down).
function gpVerdict(verdict: string | null | undefined, good: string) {
  if (verdict === 'too_few') return ' ' + gpWord(verdict);
  const arrow = GP_ARROW[verdict || ''];
  if (!arrow) return '';
  const tone = verdict === 'flat' ? '' : verdict === good ? ' gr-d-good' : ' gr-d-bad';
  return ` <span class="gr-d${tone}" title="${esc(verdict)}"><span aria-hidden="true">${arrow}</span><span class="sr-only">${esc(verdict)}</span></span>`;
}
// A headline tile: the figure or the word for why there is none, its verdict, and what it rests on in small text.
function gpTile(key: string, label: string, f: GpFig | undefined, fmt: (v: number) => string, good = 'up', notes: string[] = []) {
  const small = [f?.status === 'estimate' ? 'estimate' : '', f?.cohort ? grMonth(f.cohort) : '', GP_WINDOW[f?.window || ''] || '',
    f?.n != null ? `n=${gpCount(f.n)}` : '', ...notes].filter(Boolean);
  return `<div class="gp-tile" data-k="${key}"><span class="gr-k">${esc(label)}</span><span class="gp-v">${f?.value == null ? gpWord(f?.status) : esc(fmt(f.value)) + gpVerdict(f.verdict, good)}</span>${small.length ? `<span class="gp-s muted">${esc(small.join(' · '))}</span>` : ''}</div>`;
}
function gpTiles(h: GpHeadline) {
  const effort = (f: GpFig | undefined) => f?.with_effort != null ? [`with effort ${grUsd(f.with_effort)}`] : [];
  const months = (v: number) => `${grX(v)} mo`;
  return `<div class="gp-tiles">${[
    gpTile('signups', 'Sign-ups', h.signups, gpCount),
    gpTile('activated', 'Activated', h.activated_rate, gpRate),
    gpTile('paid', 'Paid', h.paid_rate, gpRate),
    gpTile('new', 'New customers', h.new_customers, gpCount),
    gpTile('cost', 'Cost', h.cost, grUsd, '', [...effort(h.cost), ...h.cost?.retention != null ? [`retention ${grUsd(h.cost.retention)}`] : []]),
    gpTile('cac', 'CAC', h.cac, grUsd, 'down', [...effort(h.cac), ...h.cac_loaded?.value != null ? [`loaded ${grUsd(h.cac_loaded.value)}`] : []]),
    // A retention scope's cost per retained is day 30's: Kept beside it is too.
    h.value ? gpTile('kept', 'Kept', h.value.kept_d30, gpRate, 'up', ['30 days']) : gpTile('kept', 'Kept', h.kept, gpRate),
    ...h.value ? [gpTile('per-retained', 'Cost per retained', h.value.cost_per_retained, grUsd, 'down', ['30 days'])]
      : [gpTile('ltv', 'LTV', h.ltv, grUsd), gpTile('ltv-cac', 'LTV:CAC', h.ltv_cac, v => `${grX(v)}×`), gpTile('payback', 'Payback', h.payback_months, months, 'down')],
  ].join('')}</div>`;
}
// Channels as the read orders them, Unknown last: a no-spend channel says so, with its CAC counting effort.
function gpChannels(rows: GpChannel[]) {
  if (!rows.length) return '';
  const td = (html: string) => `<td class="num">${html}</td>`;
  const share = (v: number | null | undefined) => td(v == null ? '' : esc(gpRate(v)));
  const row = (c: GpChannel) => {
    if (c.slug === 'unknown') return `<tr data-channel="unknown"><th scope="row" class="muted">Unknown</th><td></td>${td(esc(gpCount(c.signups ?? 0)))}${td(esc(gpCount(c.new_customers)))}<td></td><td></td><td></td><td></td>${share(c.new_share)}</tr>`;
    const spend = c.status === 'no_spend' || c.status === 'not_split';
    const cac = c.cac != null ? esc(grUsd(c.cac)) + gpVerdict(c.verdict, 'down') : c.with_effort != null ? `${esc(grUsd(c.with_effort))} <small class="muted">effort</small>`
      : c.status === 'not_split' || c.status === 'no_customers' ? gpWord(c.status) : '';
    const budget = c.budget == null ? '' : esc(grUsd(c.budget)) + (c.over_budget ? ` <small class="gp-over">+${esc(grUsd(c.over_budget))}</small>` : '');
    return `<tr data-channel="${esc(c.slug)}"><th scope="row">${esc(c.name || c.slug)}</th><td>${esc(grWho(c.owner))}</td>${td(esc(gpCount(c.signups ?? 0)))}${td(esc(gpCount(c.new_customers)))}
      ${td(c.cost != null ? esc(grUsd(c.cost)) : spend ? gpWord(c.status) : '')}${td(cac)}${td(budget)}${share(c.cost_share)}${share(c.new_share)}</tr>`;
  };
  return `<section class="card gp-sec"><h2 class="gp-h">Channels</h2><div class="gp-scroll"><table class="grc-table gp-ch"><thead><tr><th scope="col">Channel</th><th scope="col">Owner</th>
    <th scope="col" class="num">Sign-ups</th><th scope="col" class="num">New</th><th scope="col" class="num">Cost</th><th scope="col" class="num">CAC</th><th scope="col" class="num">Budget</th>
    <th scope="col" class="num">Cost share</th><th scope="col" class="num">New share</th></tr></thead><tbody>${rows.map(row).join('')}</tbody></table></div></section>`;
}
// Twelve months: counts as bars, rates and costs as lines, the picked month marked; a chart with nothing to draw
// says why in the headline's word.
function gpCharts(series: GpPoint[], r: GpRead) {
  if (!series.length) return '';
  const at = series.findIndex(p => p.period === r.period), h = r.headline;
  const axis = `<p class="gp-axis muted"><span>${esc(grMonth(series[0]?.period))}</span><span>${esc(grMonth(series[series.length - 1]?.period))}</span></p>`;
  const chart = (label: string, svg: string, why: GpFig | undefined) => `<figure class="gp-chart"><figcaption class="gr-k">${esc(label)}</figcaption>${svg ? svg + axis : `<p class="gp-none">${gpWord(why?.status)}</p>`}</figure>`;
  const opts = {w: 240, h: 64};
  return `<section class="card gp-sec"><div class="gp-charts">
    ${chart('Sign-ups', grBars(series.map(p => p.signups), at, opts), h.signups)}
    ${chart('New customers', grBars(series.map(p => p.new_customers), at, opts), h.new_customers)}
    ${chart('CAC', grSpark(series.map(p => p.cac), at, opts), h.cac)}
    ${chart('Kept', grSpark(series.map(p => p.kept), at, opts), h.kept)}
  </div></section>`;
}
// The latest six cohorts and the share kept at each window the data has, shaded by it, with how many it counts.
const GP_WINS: [GpWin, string][] = [['kept_d30', '30 days'], ['kept_d90', '90 days'], ['kept_d180', '180 days']];
function gpRetention(rows: GpCohort[]) {
  const recent = rows.slice(0, 6), wins = GP_WINS.filter(([k]) => recent.some(r => r[k]));
  if (!recent.length || !wins.length) return '';
  const cell = (k: GpCohort[GpWin]) => !k ? '<td class="grc-k grc-k-none"></td>' : k.value == null ? `<td class="grc-k grc-k-none">${gpWord(k.status)}</td>`
    : `<td class="grc-k" style="--k:${Math.round(k.value * 100)}">${esc(gpRate(k.value))} <small>${esc(gpCount(k.n))}</small></td>`;
  return `<section class="card gp-sec"><h2 class="gp-h">Retention</h2><div class="grc-ret"><table class="grc-table grc-ret-t"><thead><tr><th scope="col">Joined</th><th scope="col" class="num">New</th>
    ${wins.map(([, l]) => `<th scope="col" class="grc-k-h">${l}</th>`).join('')}</tr></thead><tbody>${recent.map(r => `<tr><th scope="row">${esc(grMonth(r.cohort))}</th>
    <td class="num">${esc(gpCount(r.entered))}</td>${wins.map(([k]) => cell(r[k])).join('')}</tr>`).join('')}</tbody></table></div></section>`;
}
const gpKpis = (ms: GpMetric[]) => ms.length ? `<section class="card gp-sec"><h2 class="gp-h">KPIs</h2><ul class="gp-kpis">${ms.map(m =>
  `<li><a href="${GR_GOALS}?kpi=${encodeURIComponent(m.id)}">${esc(m.name)}</a> <b>${m.value == null ? '—' : esc(kpiNum(m.value, m.unit))}</b>${m.estimate ? ' <small class="muted">estimate</small>' : ''}</li>`).join('')}</ul></section>` : '';
function gpHealth(h: GpHealth) {
  const parts = [h.unknown_share != null ? `Unknown ${gpRate(h.unknown_share)}` : '', h.no_segment != null ? `No segment ${gpCount(h.no_segment)}` : '',
    h.mismatch != null ? `Mismatch ${gpCount(h.mismatch)}` : '', h.reconciled === 'not_reconciled' ? 'Not reconciled' : h.reconciled ? 'Reconciled' : '',
    h.stale_sources?.length ? `Stale: ${h.stale_sources.join(', ')}` : ''].filter(Boolean);
  return parts.length ? `<p class="muted gp-health">${esc(parts.join(' · '))}</p>` : '';
}
const gpBody = (r: GpRead) => gpTiles(r.headline) + gpChannels(r.channels) + gpCharts(r.series || [], r) + gpRetention(r.retention || []) + gpKpis(r.metrics) + gpHealth(r.health);
