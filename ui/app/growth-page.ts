/* ui/app/growth-page.ts — The Growth page (#/growth, docs/growth.md): a head (the North Star, the month picker, the
   Growth board and Settings) and, until Growth is set up, the Get started panel (growth-start.ts), over the Dashboard's
   grid of cards for the place `growth` (ui/app/dashboard.ts, backend/cards.py), which the owner, admins and bots
   arrange. Its cards read the month picked here (GET /api/v2/places/growth?month=); this file draws them: a Number, the
   Funnel, Channels, Retention and the Growth board. Settings (growth-channels.js) keeps the numbers the maths uses, the
   channels and their months; Sources (growth-sources.js) is where the numbers come from. Also what those files, Brand
   (brand-page.js) and the KPI views on Goals share: formats, a small dialog, form fields, the owner picker. Old
   addresses land on what replaced them. Every write is the server's to allow.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const GR_ROUTE = '#/growth', GR_SOURCES = GR_ROUTE + '/sources', BRAND_ROUTE = '#/strategy/brand', GR_GOALS = '#/strategy/goals';
// Where Growth and Go-to-market lived, under Strategy: redesignRoute sends them on.
const GR_OLD_ROUTE = '#/strategy/growth', GTM_OLD_ROUTE = '#/strategy/gtm';
// The Growth board is Tasks on the pipeline the growth packs file ideas into, Growth Experiments (backend/
// pipeline_presets_more.py); with several, the first live one; one channel's ideas are those tagged `channel:<slug>`.
// Unknown (undefined) until the pipelines are read, null with none: then no board link shows (grBoardLinks).
let GR_BOARD_PIPE: string | null | undefined;
const GR_BOARD_PRESET = 'growth-experiments';
const grBoardHref = (slug = '') => GR_BOARD_PIPE ? `${TASKS}?pipeline=${encodeURIComponent(GR_BOARD_PIPE)}&who=all${slug ? '&tag=' + encodeURIComponent('channel:' + slug) : ''}` : '';
// A board link, hidden until the board is known; grBoardLinks points or hides it.
const grBoardLink = (cls: string, text: string, slug = '', attrs = '') => {
  const href = grBoardHref(slug);
  return `<a class="${cls}" data-gr-board-link="${esc(slug)}"${attrs}${href ? ` href="${href}"` : ' hidden'}>${esc(text)}</a>`;
};
function grBoardLinks(root: ParentNode) {
  root.querySelectorAll<HTMLAnchorElement>('a[data-gr-board-link]').forEach(a => {
    const href = grBoardHref(a.dataset.grBoardLink || '');
    a.hidden = !href;
    if (href) a.setAttribute('href', href); else a.removeAttribute('href');
  });
}
// The board's pipeline among those read: undefined while none were read yet, null when none is Growth Experiments.
const grBoardOf = (pipes: PipelineRec[] | null): string | null | undefined => pipes ? pipes.find(p => p.preset === GR_BOARD_PRESET)?.id ?? null : undefined;
// Reads the pipelines once per Growth page (the list already read draws the links meanwhile); a failed read shows no board link rather than a wrong one.
async function grBoardRead(load: number) {
  let pipe: string | null = null;
  try { pipe = grBoardOf(await tasksPipelinesRead()) ?? null; } catch { /* no link */ }
  if (load !== GR_LOAD) return;   // G: another page opened meanwhile
  GR_BOARD_PIPE = pipe;
  if (DASH?.place === 'growth' && DASH.root) grBoardLinks(DASH.root);
}
// The Sources list as the server answers it (GET /api/v2/growth/sources): one row per source of a truth. A source with a
// `kind` syncs (backend/growth_sync.py view): what it fills, the columns it reads, and its health.
type GrHealth = 'fresh' | 'stale' | 'failing' | 'waiting' | 'not_synced';
interface GrSyncMap { month?: string; channel?: string; fields?: Record<string, string> }
interface GrSource {
  id: string; truth_for: string; system: string; reach_kind: string; reach_ref?: string | null; may: string; owner_ref: string | null; retired_at?: string | null;
  kind?: string | null; fills?: string[]; map?: GrSyncMap; health?: GrHealth; health_since?: string | null; last_good_run?: string | null; last_error?: string | null; has_file?: boolean;
}
interface GrSourcesData { src: GrSource[]; conflicts: string[]; may: boolean }
interface GrState { load: number; sub: string; month: string; data: GrSourcesData | null }
// The card data the Growth cards read (backend/cards.py growth_*). A Card's data is unknown per template, so each card's
// reader casts it once to the shape written down here.
type GrSeriesPoint = { month: string; value: number | null };
type GrNumberData = { label?: string; channel_name?: string; value: number | null; prev: number | null; format: string; good: string | null; month: string; from?: string | null; series?: GrSeriesPoint[]; origin?: GrOrigin | null };
// Where a Number's figure came from (backend/cards.py growth_origin): its synced sources, its typed numbers, the inputs a
// worked-out figure is made from, and the numbers a source disagrees with.
type GrOriginSource = { source_id: string; source: string; at: string; health: GrHealth | null; since: string | null };
type GrConflict = { channel: string; channel_name: string; month: string; field: string; typed: number | null; value: number | null; source: string | null; may: boolean };
type GrOrigin = { fields: string[]; synced: GrOriginSource[]; typed: { count: number; by: string | null; at: string | null; quality: string } | null; conflicts: GrConflict[]; conflict_count: number };
type GrNorthStar = { id: string; name: string; value: number | null; unit: string; prev: number | null; target: number | null };
type GrFunnelStage = { stage: string; label: string; kpi: string | null; name: string | null; value: number | null; unit: string; prev: number | null; conv: number | null; series?: GrValue[]; north_star?: boolean };
type GrFunnelData = { stages: GrFunnelStage[]; north_star?: GrNorthStar | null };
type GrTask = { id: string; title: string; stage: string };
type GrChannelRow = { slug: string; name: string; new_customers: number | null; cac: number | null; ltv_cac: number | null; ltv_from?: string | null; series?: GrValue[]; tasks?: GrTask[]; more?: number | null };
type GrChannelsData = { rows: GrChannelRow[] };
type GrRetentionData = { columns: string[]; rows: Array<{ month: string; new_customers: number | null; kept: Array<number | null> }> };
type GrBoardCard = { id: string; title?: string | null; channel?: string | null; owner_id?: string | null };
type GrBoardData = { lanes: Array<{ stage: string; label: string; count: number; cards: GrBoardCard[] }> };
// A KPI as the Goals views hand it over: its latest reading and unit.
type GrKpiVal = { latest?: number | null; unit?: string };
let GR: GrState | null = null;            // the open Growth page: {load, sub, month}, and Sources' {data}
let GR_LOAD = 0;
const GR_STAGES = [['reach', 'Reach'], ['acquire', 'Acquire'], ['activate', 'Activate'], ['retain', 'Retain'], ['refer', 'Refer']];
const GR_CH_STAGE: Record<string, string> = {idea: 'Idea', testing: 'Testing', scaling: 'Scaling', paused: 'Paused'};
// Where a growth idea is, by its labels (backend/cards.py growth_board, backend/marketing_tasks.py STAGES).
const GR_TASK_STAGE: Record<string, string> = {idea: 'On deck', building: 'In development', testing: 'Live', scaling: 'Working',
  measuring: 'Measuring'};

// ----------------------------------------------------------------- shared formats and pieces
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
const grPct = (v: unknown) => `${Math.round(Number(v))}%`;
const grX = (v: unknown, d = 1) => Number(v).toLocaleString('en-US', {maximumFractionDigits: d});
const grRatio = (v: unknown) => `${grX(v)}×`;
const GR_MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const grMonth = (m: unknown) => { const [y, n] = String(m || '').split('-').map(Number); return n ? `${GR_MON[n - 1]} ${y}` : String(m || ''); };
const grMonthShort = (m: unknown) => { const [y, n] = String(m || '').split('-').map(Number); return n ? `${GR_MON[n - 1]} ${String(y).slice(2)}` : ''; };
const grDay = (iso: string | null | undefined) => {
  if (!iso) return '';
  const d = new Date(String(iso).length <= 10 ? iso + 'T12:00:00' : iso);
  if (isNaN(d.valueOf())) return String(iso);
  return `${GR_MON[d.getMonth()]} ${d.getDate()}${d.getFullYear() === new Date().getFullYear() ? '' : `, ${d.getFullYear()}`}`;
};
const grKpiVal = (k: GrKpiVal | null | undefined) => k?.latest == null ? '—' : kpiNum(k.latest, k.unit);
const grTaskHref = (id: string) => '#/task/' + encodeURIComponent(id);
const grGoalHref = (id: string) => `${GR_GOALS}?kpi=${encodeURIComponent(id)}`;
const grFail = (e: unknown) => `<p class="muted gr-note">${esc(errorStatus(e) === 403 ? 'Not in your view.' : errorMessage(e, 'Did not load.'))}</p>`;
const grOpts = (pairs: string[][], value: string | number | null | undefined, blank: string | null = '') => (blank !== null ? `<option value="">${esc(blank)}</option>` : '')
  + pairs.map(([v, l]) => `<option value="${esc(v)}"${String(v) === String(value ?? '') ? ' selected' : ''}>${esc(l)}</option>`).join('');
// A segmented switch: [[value, label]], the chosen one aria-checked.
const grSeg = (name: string, label: string, options: string[][], value: string | undefined) => `<div class="theme-seg gr-seg" role="radiogroup" aria-label="${esc(label)}" data-gr-seg="${esc(name)}">${options.map(([v, l]) =>
  `<button type="button" role="radio" data-v="${esc(v)}" aria-checked="${v === value}">${esc(l)}</button>`).join('')}</div>`;
const grCrumbs = (base: string, baseLabel: string, title: string, tools = '') => `<div class="gr-crumbs"><a href="${base}">${esc(baseLabel)}</a><span class="muted" aria-hidden="true">/</span><h2>${esc(title)}</h2><span class="spacer"></span>${tools}</div>`;
// A labelled field: the caption over the control.
const grField = (label: string, control: string, cls = '') => `<label class="gr-f${cls ? ' ' + cls : ''}"><span>${esc(label)}</span>${control}</label>`;
const grNum = (v: unknown, d: number | null = null): number | null => { const t = String(v ?? '').trim().replace(/[$,%\s]/g, ''); if (t === '') return d; const n = Number(t); return Number.isFinite(n) ? n : d; };
// The people and bots a row can belong to, as human:<id> and bot:<id>.
function grOwnerOpts(selected: unknown) {
  const sel = String(selected || ''), picked = sel.startsWith('human:') ? '' : botByRef(sel)?.id;
  const people = (S.people || []).filter(p => !p.hidden).slice().sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)));
  const bots = (S.emps || []).filter(e => e.status !== 'archived').slice().sort((a, b) => String(a.display_name || a.slug).localeCompare(String(b.display_name || b.slug)));
  return `<option value="">—</option><optgroup label="People">${people.map(p => `<option value="human:${esc(p.id)}"${sel === 'human:' + p.id ? ' selected' : ''}>${esc(p.name || p.id)}</option>`).join('')}</optgroup>
    <optgroup label="Bots">${bots.map(e => `<option value="bot:${esc(e.id)}"${picked === e.id ? ' selected' : ''}>${esc(e.display_name || e.slug)}</option>`).join('')}</optgroup>`;
}
// Retire asks once more on the same button (no window.confirm); `after` runs once it is retired.
function grRetireButton(btn: HTMLButtonElement, dlg: HTMLDialogElement, path: string, after?: () => void) {
  btn.onclick = async () => {
    if (!btn.dataset.armed) { btn.dataset.armed = '1'; btn.textContent = 'Retire for good'; return; }
    btn.disabled = true;
    try { await post(path, {}); toast('Retired'); dlg.close(); after?.(); }
    catch (e) { toast(errorMessage(e, 'Not retired'), true); btn.disabled = false; }
  };
}

// A small dialog: a title, a form, Cancel and the primary button (none, and Close, with no `saveLabel`). `onSave(form)`
// throws to show an error.
function grDialog(title: string, body: string, saveLabel: string | null | undefined, onSave: ((form: HTMLFormElement) => unknown) | null) {
  document.querySelectorAll<HTMLDialogElement>('dialog.gr-dlg').forEach(d => d.close());
  const dlg = document.createElement('dialog');
  dlg.className = 'tmodal gr-dlg';
  dlg.setAttribute('aria-label', title);
  dlg.innerHTML = `<div class="tmodal-head"><b>${esc(title)}</b><span class="spacer"></span>
      <button type="button" class="ghost tmodal-x" data-close aria-label="Close">✕</button></div>
    <form class="tmodal-body gr-form" novalidate>${body}<p class="err" data-error hidden></p>
      <div class="gr-foot"><button type="button" class="ghost" data-close>${saveLabel ? 'Cancel' : 'Close'}</button>${saveLabel ? `<button type="submit" class="primary">${esc(saveLabel)}</button>` : ''}</div></form>`;
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
const grChecks = (name: string, pairs: string[][], on: Array<string | undefined> = []) => `<div class="gr-checks" role="group">${pairs.map(([v, l]) =>
  `<label class="gr-check"><input type="checkbox" name="${esc(name)}" value="${esc(v)}"${on.includes(v) ? ' checked' : ''}>${esc(l)}</label>`).join('')}</div>`;
const grChecked = (form: HTMLFormElement, name: string) => [...form.querySelectorAll<HTMLInputElement>(`input[name="${CSS.escape(name)}"]:checked`)].map(i => i.value);

// The change from `prev`: an arrow and a share, or points for a figure that is itself a share. `good` is the direction
// that is good news; with none, it stays neutral.
function grDelta(value: unknown, prev: unknown, {good = null, points = false}: {good?: string | null; points?: boolean} = {}) {
  if (value == null || prev == null) return '';
  const diff = Number(value) - Number(prev), dir = Math.abs(diff) < 1e-9 ? 0 : Math.sign(diff);
  const size = points ? `${grX(Math.abs(diff), 0)} ${Math.round(Math.abs(diff)) === 1 ? 'pt' : 'pts'}` : Number(prev) ? `${grX(Math.abs(diff / Number(prev)) * 100, 0)}%` : grX(Math.abs(diff));
  const tone = !dir || !good ? '' : (dir > 0) === (good === 'up') ? ' gr-d-good' : ' gr-d-bad';
  return `<span class="gr-d${tone}" aria-label="${['Down', 'Flat', 'Up'][dir + 1]} ${esc(size)}"><span aria-hidden="true">${['↓', '→', '↑'][dir + 1]}</span> ${esc(size)}</span>`;
}

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
  if (offGrowth && offBrand) document.querySelectorAll<HTMLDialogElement>('dialog.gr-dlg').forEach(d => d.close());
};
// Old addresses: what was on Growth's tabs is on this one page, and who we sell to and how went to Strategy.
function grMoved(parts: string[]) {
  if (!parts[0] || parts[0] === 'sources') return '';
  if (parts[0] === 'gtm') return parts[1] === 'sources' ? GR_SOURCES : '#/strategy';
  return ['buyers', 'segments'].includes(parts[0]) ? '#/strategy' : GR_ROUTE;
}
const GR_MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/;
function pageGrowth() {
  const [path = '', query] = (S.route || '').split('?');
  const parts = path.slice(GR_ROUTE.length + 1).split('/'), sub = parts[0] || '';
  const moved = grMoved(parts);
  if (moved) { location.replace(moved); return; }
  document.querySelectorAll<HTMLDialogElement>('dialog.gr-dlg').forEach(d => d.close());
  const load = ++GR_LOAD, m = new URLSearchParams(query || '').get('m') || '';
  GR = {load, sub, month: GR_MONTH_RE.test(m) ? m : '', data: null};
  if (sub !== 'sources') { GR_BOARD_PIPE = grBoardOf(TASK_PIPELINES); void grBoardRead(load); void pageDashboard('growth', {month: GR.month}); return; }
  const main = $('#main');
  main.classList.add('strategy-layout', 'growth-layout');
  main.innerHTML = `<div class="strat-head rd-strat-head gr-head"><a class="gr-back" href="${GR_ROUTE}" data-gr-back>Growth</a><span class="muted" aria-hidden="true">/</span><h1>Sources</h1><span class="spacer"></span>
    <div class="strat-actions" id="strat-actions"></div></div><div id="gr-body" class="gr-body">${skelRows(6, {dot: false})}</div>`;
  pageGrowthSources(GR);
}
// After Settings or a channel saves: the cards and the Get started panel read again.
function grReload(state: unknown) {
  dashChoicesStale();   // the Number form's channels
  if (GR === state && DASH?.place === 'growth') void dashRefresh(DASH);
  if (GR === state && GR) void grStartRead(GR);   // the Get started panel's steps
}

// ----------------------------------------------------------------- the head over the grid
// A KPI's figure: a word unit is the KPI's name's to say, a sign stays on the number.
const grKpiFig = (v: number | null | undefined, unit?: string) => ['$', '€', '£', '%', 'pp'].includes(String(unit || '').trim()) ? kpiNum(v, unit) : kpiNum(v);
DASH_PLACES.growth = {
  first: 'growth_number',
  // A bot pins only cards here (backend/cards.py).
  pages: () => S.me?.role !== 'bot',
  head: () => `<h1>Growth</h1><a class="grh-ns" data-gr-ns hidden></a><span class="spacer"></span>
    <label class="grh-month" hidden><span class="sr-only">Month</span><select data-gr-month></select></label>
    ${grBoardLink('gr-btn grh-btn', 'Growth board', '', ' data-gr-board')}
    ${grAdmin() ? '<button type="button" class="ghost grh-btn" data-gr-settings>Settings</button>' : `<a class="gr-btn grh-btn" href="${GR_SOURCES}" data-gr-sources>Sources</a>`}`,
  wire(state) {
    const root = state.root;
    if (!root) return;   // G: the grid is there before a place is wired
    root.querySelector('[data-gr-settings]')?.addEventListener('click', () => { if (GR) void gcSettings(GR); });
    if (GR) grStartMount(GR, root);   // the Get started panel under the head while setup is not done
    // A stale or failing source is said once, right under the head and over every other panel (grHealthRead).
    root.querySelector('.dash-head')?.insertAdjacentHTML('afterend', '<div class="gr-banner gr-amber gr-health" data-gr-health role="status" hidden></div>');
    grResolveWire(root);
    const sel = root.querySelector<HTMLSelectElement>('[data-gr-month]');
    if (!sel) return;   // G
    // A month picked reads every card again for it; the address keeps it.
    sel.addEventListener('change', ev => {
      const month = ev.target instanceof HTMLSelectElement ? ev.target.value : '';
      if (!GR_MONTH_RE.test(month) || DASH !== state) return;
      state.month = month; state.key = dashKey(state);
      if (GR) GR.month = month;
      const hash = `${GR_ROUTE}?m=${encodeURIComponent(month)}`;
      history.replaceState(history.state, '', location.pathname + location.search + hash);
      S.route = LAST_ROUTE = hash;
      void dashRefresh(state);
    });
  },
  drawn(state) {
    const v = state.view, root = state.root;
    if (!v || !root) return;   // G: drawn only once the place has its view and its grid
    grBoardLinks(root);
    void grHealthRead(state);
    // The month picker: the months with numbers, newest first, and the one the cards read.
    const months = [...new Set([...(v.months || []), ...(v.month ? [v.month] : [])])].sort().reverse();
    const sel = root.querySelector<HTMLSelectElement>('[data-gr-month]');
    if (!sel) return;   // G
    const picker = sel.closest<HTMLLabelElement>('label');
    if (picker) picker.hidden = months.length < 2;   // G
    patchHTML(sel, months.map(m => `<option value="${esc(m)}"${m === v.month ? ' selected' : ''}>${esc(grMonth(m))}</option>`).join(''));
    sel.value = v.month || '';
    // The North Star line, from the Funnel card's when one is pinned.
    const ns = (v.pins.find(p => p.card?.template === 'growth_funnel' && p.card.data?.north_star)?.card?.data as GrFunnelData | null | undefined)?.north_star;
    const line = root.querySelector<HTMLAnchorElement>('[data-gr-ns]');
    if (!line) return;   // G
    line.hidden = ns?.value == null;
    if (line.hidden || !ns) return;   // G: a hidden line has no North Star
    line.href = grGoalHref(ns.id);
    patchHTML(line, `<span class="muted">North Star</span> <span class="grh-ns-n">${esc(ns.name)}</span> <b>${esc(grKpiFig(ns.value, ns.unit))}</b>
      ${grDelta(ns.value, ns.prev, {good: 'up'})}${ns.target != null ? ` <span class="muted">· Target ${esc(grKpiFig(ns.target, ns.unit))}</span>` : ''}`);
  },
};

// ----------------------------------------------------------------- the cards (DASH_BODY, ui/app/dashboard.ts)
const GR_FORMAT: Record<string, (v: unknown) => string> = {count: v => grX(v, 0), usd: grUsd, pct: grPct, ratio: grRatio, months: v => `${grX(v)} mo`};
const GR_METRIC: Record<string, string> = {new_customers: 'New customers', spend: 'Spend', bot_cost: 'Bot cost', cac: 'CAC', revenue_month: 'Revenue / customer',
  margin_pct: 'Margin', ltv: 'LTV', ltv_cac: 'LTV:CAC', payback_months: 'Payback', kept_d30: 'Kept 30 days', kept_d90: 'Kept 90 days', kept_d180: 'Kept 180 days'};
const grFmt = (format: string, v: unknown) => v == null ? '—' : (GR_FORMAT[format] || ((x: unknown) => grX(x)))(v);
const grStageWord = (stage: string) => GR_TASK_STAGE[stage] || stage;
// A channel's name as stored: from the card itself, else the Channels card on the same page, else its slug.
function grChannelName(slug: string, card: ApiCard | undefined): string {
  const named = (card?.data as GrNumberData | null | undefined)?.channel_name;
  if (named) return named;
  for (const pin of DASH?.view?.pins || []) {
    const row = pin.card?.template === 'growth_channels' && (pin.card.data as GrChannelsData | null)?.rows?.find(r => r.slug === slug);
    if (row) return row.name;
  }
  return slug;
}
const grInitials = (name: unknown) => (String(name || '').split(/[^\p{L}\p{N}]+/u).filter(Boolean).slice(0, 2).map(w => w[0]).join('') || '?').toUpperCase();
// A Number's heading is its metric's, and its channel's when it reads one.
DASH_HEADING.growth_number = card => {
  const label = (card.data as GrNumberData | null)?.label || GR_METRIC[String(card.settings?.metric)] || '';
  return label && (card.settings?.channel ? `${label} · ${grChannelName(String(card.settings.channel), card)}` : label);
};
// An empty card names the step that fills it (DASH_NONE, ui/app/growth-start.ts).

// A Number: the picked month's value with its change from the month before and its trend; a value carried back from
// an earlier month (retention, LTV) says which.
function grNumberCard(d: GrNumberData) {
  if (d.value == null) return `<p class="muted dash-none">None for ${esc(grMonth(d.month))}</p>${grConflicts(d.origin)}`;
  const series = d.series || [], at = d.from ? series.findIndex(p => p.month === d.from) : series.length - 1;
  return `<div class="grc-num"><span class="grc-num-v">${esc(grFmt(d.format, d.value))}</span>${grSpark(series.map(p => p.value), at, {w: 72, h: 22})}</div>
    <div class="grc-num-d">${grDelta(d.value, d.prev, {good: d.good, points: d.format === 'pct'})}${d.from ? `<span class="muted">${esc(grMonthShort(d.from))}</span>` : ''}</div>
    ${grOriginLine(d.origin)}${grConflicts(d.origin)}`;
}

// ----------------------------------------------------------------- where each number came from (spec AC-9, AC-10, AC-12)
// A cohort number's format, to write a typed value and a source's beside each other.
const GR_FIELD_FMT: Record<string, string> = {spend: 'usd', bot_cost: 'usd', revenue_month: 'usd', new_customers: 'count', kept_d30: 'pct', kept_d90: 'pct', kept_d180: 'pct', margin_pct: 'pct'};
const grFieldWord = (f: string) => (GR_METRIC[f] || f).toLowerCase();
const grList = (words: string[]) => words.length < 2 ? words.join('') : `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`;
const grBadHealth = (h: string | null | undefined) => h === 'stale' || h === 'failing';
// One synced source on a number's line: its name, how long ago it synced, and Stale or Failing when it is.
const grOriginSrc = (s: GrOriginSource) => `<span class="gr-o-src">${esc(s.source)} · ${agoSpan(s.at)}${grBadHealth(s.health)
  ? ` <span class="pill ${s.health === 'failing' ? 'fail' : 'waiting'}" title="${esc(s.health === 'failing' ? 'Its last run failed' : 'No good run in twice its refresh time')}">${s.health === 'failing' ? 'failing' : 'stale'}</span>` : ''}</span>`;
// A number's origin line: each source it was synced from, then who typed the rest (actual or estimate). A worked-out
// figure (CAC, LTV) says it is worked out from its inputs, which the hover names.
function grOriginLine(o: GrOrigin | null | undefined) {
  if (!o || (!o.synced.length && !o.typed)) return '';
  const parts = o.synced.map(grOriginSrc), t = o.typed;
  if (t) {
    const who = `${t.by ? ` by ${esc(grWho(t.by))}` : ''}${t.at ? `, ${esc(grDay(t.at))}` : ''}`;
    parts.push(`<span class="gr-o-typed">${t.count > 1 ? `${t.count} typed${o.synced.length ? '' : `, newest${who}`}` : `typed${o.synced.length ? '' : who}`} · ${t.quality === 'estimate' ? 'estimate' : 'actual'}</span>`);
  }
  // A synced number is always actual (AC-10); with typed ones beside it, their quality is said instead.
  if (!t) parts.push('<span class="gr-o-q">actual</span>');
  const made = o.fields.length ? `Worked out from ${grList(o.fields.map(grFieldWord))}` : '';
  return `<div class="gr-origin muted" data-gr-origin${made ? ` title="${esc(made)}"` : ''}>${made ? '<span class="gr-o-from">from</span> ' : ''}${parts.join('<span aria-hidden="true"> · </span>')}</div>`;
}
// The numbers a source disagrees with: one is said in the line (the typed value, the source's, Keep and Use for its
// owner or an admin); more open a list.
const grResolveAttrs = (c: GrConflict) => `data-gr-resolve data-channel="${esc(c.channel)}" data-month="${esc(c.month)}" data-field="${esc(c.field)}"`;
function grConflictLine(c: GrConflict, withWhere: boolean) {
  const f = (v: number | null) => grFmt(GR_FIELD_FMT[c.field] || 'count', v);
  const where = withWhere ? `${esc(c.channel_name)} · ${esc(grMonthShort(c.month))} · ${esc(grFieldWord(c.field))}: ` : '';
  return `<span>${where}typed ${esc(f(c.typed))}, ${esc(c.source || 'its source')} says ${esc(f(c.value))}</span>${c.may
    ? `<span class="gr-c-acts"><button type="button" class="ghost small" ${grResolveAttrs(c)} data-keep="typed">Keep ${esc(f(c.typed))}</button><button type="button" class="ghost small" ${grResolveAttrs(c)} data-keep="source">Use ${esc(f(c.value))}</button></span>` : ''}`;
}
function grConflicts(o: GrOrigin | null | undefined) {
  if (!o?.conflict_count) return '';
  const only = o.conflict_count === 1 ? o.conflicts[0] : undefined;
  if (only) return `<div class="gr-conflict" data-gr-conflict>${grConflictLine(only, true)}</div>`;
  return `<div class="gr-conflict" data-gr-conflict><span>${o.conflict_count} numbers differ from their source</span>
    <button type="button" class="ghost small" data-gr-conflicts="${esc(JSON.stringify(o.conflicts))}" data-gr-more="${o.conflict_count - o.conflicts.length}">Choose</button></div>`;
}
// Keep or Use, on a card or in the list: the source's owner or an admin chooses (the server checks), then the cards read again.
function grResolveWire(root: HTMLElement) {
  root.addEventListener('click', ev => {
    const t = ev.target instanceof Element ? ev.target : null;
    const list = t?.closest<HTMLButtonElement>('[data-gr-conflicts]');
    if (list) {
      let rows: GrConflict[] = [];
      try { rows = JSON.parse(list.dataset.grConflicts || '[]') as GrConflict[]; } catch { /* none */ }
      const more = Number(list.dataset.grMore) || 0;
      const dlg = grDialog('Numbers that differ from their source', `<p class="muted gr-note">A source never writes over a number someone typed as actual. Keep the typed number, or use the source's.</p>
        <ul class="gr-conflicts">${rows.map(c => `<li class="gr-conflict">${grConflictLine(c, true)}</li>`).join('')}</ul>
        ${more > 0 ? `<p class="muted gr-note" data-gr-conflicts-more>And ${more} more, shown here once these are chosen.</p>` : ''}`, '', null);
      grResolveWire(dlg);
      return;
    }
    const btn = t?.closest<HTMLButtonElement>('[data-gr-resolve]');
    if (btn) void grResolve(btn);
  });
}
async function grResolve(btn: HTMLButtonElement) {
  const {channel, month, field, keep} = btn.dataset;
  const row = btn.closest<HTMLElement>('.gr-conflict');
  row?.querySelectorAll<HTMLButtonElement>('button').forEach(b => { b.disabled = true; });
  try {
    await post('/v2/growth/cohorts/conflicts', {channel, month, field, keep});
    toast(keep === 'typed' ? 'Kept the typed number' : "Using the source's number");
    const dlg = btn.closest<HTMLDialogElement>('dialog');
    if (dlg?.dataset.closeOnResolve) dlg.close();
    row?.remove();
    if (GR) grReload(GR);
  } catch (e) {
    toast(errorMessage(e, 'Not changed'), true);
    row?.querySelectorAll<HTMLButtonElement>('button').forEach(b => { b.disabled = false; });
  }
}
// The banner over the cards: which sources are Failing (since when, and why) or Stale (no good run since when), and
// which numbers they fill, which their cards mark. None while every source is Fresh. Read at most once a minute.
function grHealthBanner(sources: GrSource[]) {
  const live = sources.filter(s => s.kind && !s.retired_at);
  const failing = live.filter(s => s.health === 'failing'), stale = live.filter(s => s.health === 'stale');
  if (!failing.length && !stale.length) return '';
  const count = [failing.length ? `${failing.length} source${failing.length > 1 ? 's' : ''} failing` : '',
    stale.length ? `${stale.length}${failing.length ? '' : ` source${stale.length > 1 ? 's' : ''}`} stale` : ''].filter(Boolean).join(', ');
  const each = [...failing.map(s => `<b>${esc(s.system)}</b> since ${esc(grDay(s.health_since))}${s.last_error ? `: ${esc(s.last_error)}` : ''}`),
    ...stale.map(s => `<b>${esc(s.system)}</b> ${s.last_good_run ? `no good run since ${esc(grDay(s.last_good_run))}` : 'no good run yet'}`)];
  const fills = [...new Set([...failing, ...stale].flatMap(s => s.fills || []))].map(grFieldWord);
  const marked = fills.length ? ` ${esc(grList(fills).replace(/^./, c => c.toUpperCase()))} ${fills.length > 1 ? 'are' : 'is'} marked where shown.` : '';
  return `<span><b>${esc(count)}</b> · ${each.join('; ')}.${marked}</span><span class="spacer"></span><a class="gr-btn" href="${GR_SOURCES}" data-gr-health-sources>Sources</a>`;
}
let GR_HEALTH_READ: {state: DashState; at: number} | null = null;
async function grHealthRead(state: DashState) {
  if (GR_HEALTH_READ?.state === state && Date.now() - GR_HEALTH_READ.at < 60_000) return;
  GR_HEALTH_READ = {state, at: Date.now()};
  let sources: GrSource[];
  try { sources = (await get<{sources?: GrSource[]}>('/v2/growth/sources', {fresh: true})).sources || []; }
  catch { return; }   // no banner rather than a wrong one
  const el = DASH === state ? state.root?.querySelector<HTMLElement>('[data-gr-health]') : null;
  if (!el) return;
  const html = grHealthBanner(sources);
  el.hidden = !html;
  patchHTML(el, html);
}
// The Funnel: Reach to Refer, each stage's KPI with its share of the stage before; the North Star marked, and Refer
// feeding Reach again.
function grFunnelCard(d: GrFunnelData) {
  return `<div class="grc-fun"><ol class="grc-funnel">${d.stages.map(st => {
    const inner = `<span class="grc-st-h"><span class="grc-st-k">${esc(st.label)}</span>${st.conv != null ? `<span class="grc-st-c" title="Of the stage before">${esc(grX(st.conv, st.conv < 10 ? 1 : 0))}%</span>` : ''}</span>
      <span class="grc-st-n">${esc(st.name || 'No KPI')}</span>
      <span class="grc-st-v"><b>${st.value == null ? '—' : esc(kpiNum(st.value, st.unit))}</b>${grDelta(st.value, st.prev, {good: 'up'})}</span>
      <span class="grc-st-s">${grSpark(st.series || [], -1, {w: 120, h: 22})}</span>
      <span class="grc-st-t">${st.north_star ? '★ North Star' : st.stage === 'refer' ? '↺ loops back to Reach' : ''}</span>`;
    return `<li class="grc-st${st.north_star ? ' grc-st-ns' : ''}" data-stage="${esc(st.stage)}">${st.kpi
      ? `<a class="grc-st-in" href="${grGoalHref(st.kpi)}">${inner}</a>` : `<div class="grc-st-in">${inner}</div>`}</li>`;
  }).join('')}</ol></div>`;
}
// Channels: one row each, as stored, with the month's New and CAC, LTV:CAC (carried back, dimmed, when the month has
// none yet), six months of new customers and what is being worked on there.
function grChannelsCard(d: GrChannelsData) {
  const chip = (t: GrTask) => `<a class="grc-chip" href="${grTaskHref(t.id)}" title="${esc(grStageWord(t.stage))}: ${esc(t.title)}"><i class="grc-sq" data-stage="${esc(t.stage)}" aria-hidden="true"></i><span class="sr-only">${esc(grStageWord(t.stage))}: </span><span class="grc-chip-t">${esc(t.title)}</span></a>`;
  // data-k labels a number when a narrow card stacks the rows and drops the table's head.
  const num = (k: string, v: number | null | undefined, fmt: (v: number) => string, from: string | null | undefined = '') => `<td class="grc-n${v == null ? ' muted' : from ? ' grc-old' : ''}" data-k="${k}"${from ? ` title="${esc(grMonth(from))}"` : ''}>${v == null ? '—' : esc(fmt(v))}</td>`;
  const row = (r: GrChannelRow) => `<tr data-channel="${esc(r.slug)}"><th scope="row">${esc(r.name)}</th>${num('New', r.new_customers, v => grX(v, 0))}${num('CAC', r.cac, grUsd)}${num('LTV:CAC', r.ltv_cac, grRatio, r.ltv_from)}
    <td class="grc-sp">${grSpark(r.series || [], -1, {w: 72, h: 20})}</td>
    <td class="grc-tasks">${(r.tasks || []).map(chip).join('')}${r.more ? grBoardLink('grc-more', `+${r.more} more`, r.slug) : ''}</td></tr>`;
  return `<div class="grc-ch"><table class="grc-table"><thead><tr><th scope="col">Channel</th><th scope="col" class="grc-n">New</th><th scope="col" class="grc-n">CAC</th>
    <th scope="col" class="grc-n">LTV:CAC</th><th scope="col" class="grc-sp">6 mo</th><th scope="col" class="grc-tasks">Working on</th></tr></thead>
    <tbody>${d.rows.map(row).join('')}</tbody></table></div>`;
}
// Retention: each month's new customers and the share still with us at 30, 90 and 180 days, shaded by that share.
function grRetentionCard(d: GrRetentionData) {
  const cell = (v: number | null | undefined) => v == null ? '<td class="grc-k grc-k-none"></td>'
    : `<td class="grc-k" style="--k:${Math.max(0, Math.min(100, Math.round(Number(v))))}">${esc(grPct(v))}</td>`;
  return `<div class="grc-ret"><table class="grc-table grc-ret-t"><thead><tr><th scope="col">Joined</th><th scope="col" class="grc-n">New</th>${d.columns.map(c => `<th scope="col" class="grc-k-h">${esc(c)}</th>`).join('')}</tr></thead>
    <tbody>${d.rows.map(r => `<tr><th scope="row">${esc(grMonthShort(r.month))}</th><td class="grc-n">${r.new_customers == null ? '' : esc(grX(r.new_customers, 0))}</td>${r.kept.map(cell).join('')}</tr>`).join('')}</tbody></table></div>`;
}
// The Growth board: Live, Working, In development and On deck, each lane's newest tasks with their channel and owner.
function grBoardCard(d: GrBoardData) {
  const face = (ref: string | null | undefined) => { const pid = actorPerson(ref), who = pid ? personDisplay(pid) : grWho(ref); return who ? `<span class="grc-face" title="${esc(who)}"><span aria-hidden="true">${esc(grInitials(who))}</span><span class="sr-only">${esc(who)}</span></span>` : ''; };
  const lanes = d.lanes.filter(l => l.count);
  return `<div class="grc-board">${lanes.map(l => `<section class="grc-lane" data-lane="${esc(l.stage)}"><h3 class="grc-lane-h"><i class="grc-sq" data-stage="${esc(l.stage)}" aria-hidden="true"></i>${esc(l.label)} <span class="muted">${esc(l.count)}</span></h3>
    <ul class="grc-cards">${l.cards.map(c => `<li><a class="grc-card" href="${grTaskHref(c.id)}"><span class="grc-card-t">${esc(c.title || 'Untitled')}</span>${c.channel ? `<span class="grc-card-c">${esc(c.channel)}</span>` : ''}${face(c.owner_id)}</a></li>`).join('')}</ul>
    ${l.count > l.cards.length ? grBoardLink('grc-more', `+${l.count - l.cards.length} more`) : ''}</section>`).join('')}</div>`;
}
Object.assign(DASH_BODY, {growth_number: grNumberCard, growth_funnel: grFunnelCard, growth_channels: grChannelsCard,
  growth_retention: grRetentionCard, growth_board: grBoardCard});
