/* ui/app/usage.ts — Usage page: estimated model spend per bot
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** What a usage row, a day, the totals or a routine add up to (UsageFigures in ui/types/api.d.ts). */
interface UseFigures {
  runs: number;
  input_tokens: number;
  cached_tokens: number;
  cache_write_tokens: number;
  output_tokens: number;
  est_cost_usd: number | null;
  subscription_equiv_usd: number;
  low_estimate_runs: number;
}
interface UseRow extends UseFigures { bot: string; name?: string; limit?: UseLimit | null; share?: number }
interface UseDay extends UseFigures { day: string }
interface UseRoutine extends UseFigures { title: string }
/** A task (by title) or a model in a bot's detail, with its part of the bot's spend. */
interface UseItem extends UseFigures { value: string | null; name: string; share?: number }
/** GET /api/v2/usage (backend/usage.py): the figures for one group-by, with the rows, days or routines it was asked for. */
interface UseData {
  bot?: string;
  name?: string;
  group: string;
  from: string;
  to: string;
  department?: string | null;
  departments?: string[];
  dimensions?: Record<string, string[]>;
  dimension_labels?: Record<string, Record<string, string>>;
  rows?: UseRow[];
  totals: UseFigures;
  daily?: UseDay[];
  routines?: UseRoutine[];
  tasks?: UseItem[];
  tasks_more?: UseFigures & { count: number };
  models?: UseItem[];
}
/** Who holds a limit (`blocked_by`): the bot itself, or the team or a group, over a period. */
interface UseHeld { scope?: string; name?: string; period?: string }
/** A limit as the server answers it: a bot's (UsageLimitView), a group's or the team's pool (UsagePoolView). The caps are
 *  read by period name (per_30m_usd, hourly_usd, ...), and so are a bot's own caps (own_*), so the rest is unknown. */
interface UseLimit {
  name?: string;
  parent?: string;
  may_edit?: boolean;
  blocked?: string | null;
  blocked_by?: UseHeld | null;
  percent: number;
  spent?: Record<string, number | null>;
  [field: string]: unknown;
}
interface UseDefault { count_subscription?: boolean; [field: string]: unknown }
interface UsePools { company?: UseLimit; groups?: Record<string, UseLimit> }
interface UseLimits {
  bots: Record<string, UseLimit>;
  default: UseDefault;
  may_edit_default: boolean;
  pools: UsePools | null;
}
/** One row of the Limits tab: the default, the team, a group or a bot. */
interface UseLimitRow {
  key: string;
  kind: 'default' | 'company' | 'group' | 'bot';
  name: string;
  title?: string;
  path: string;
  slug?: string;
  state?: UseLimit | null;
  value(key: string): number | null;
  hint(key: string): string;
  removable?: boolean;
}
/** The Limits tab's unsaved edits: rows added, rows struck out, and the last save's error. */
interface UseEdit { added: string[]; removed: Set<string>; error?: string }
interface UseState {
  range: string;
  department: string;
  group: string;
  sort: string;
  filters: Record<string, string>;
  custom: { from: string; to: string };
  open: string;
  data: UseData | null;
  load: number;
  view: string;
  edit: UseEdit | null;
  limits?: UseLimits | null;
  detail?: UseData;
  why?: boolean;
}
/** What a queue purge answers (POST /api/v2/usage/queues/purge). */
interface UsePurged { cancelled: number; kept_people: number; still_running: number }

// ----------------------------------------------------------------- usage
// GET /api/v2/usage (backend/usage.py, docs/usage.md): tokens each run used, priced at list price. Days are UTC.
// Every figure shown is API-equivalent: billed spend (API keys, Tico Gateway) plus what subscription runs would have cost
// at list price. Billed spend alone is in the hover, since a bot's limits count only that.
const USE_RANGES: [string, string][] = [['today', 'Today'], ['7d', '7 days'], ['30d', '30 days'], ['month', 'This month'], ['custom', 'Custom']];
const USE_DIMENSIONS: [string, string][] = [['harness', 'Harness'], ['model', 'Model'], ['effort', 'Effort'], ['subscription', 'Subscription']];
let USE: UseState | null = null;
// The Filter panel's click-outside listener: one at a time, replaced each time the page is drawn (useFilterWire).
let USE_OUTSIDE: ((ev: MouseEvent) => void) | null = null;
// The value of the select an event came from (its own control, so a plain read of the value).
const eventValue = (ev: Event): string => { const el = eventElement(ev); return el && 'value' in el ? String(el.value) : ''; };
const useDay = (date: Date): string => date.toISOString().slice(0, 10);
const useAdd = (day: string, n: number): string => useDay(new Date(Date.parse(day + 'T00:00:00Z') + n * 864e5));
function useSpan(range: string, custom?: { from?: string; to?: string } | null): { from: string; to: string } {
  const today = useDay(new Date());
  if (range === 'today') return {from: today, to: today};
  if (range === '30d') return {from: useAdd(today, -29), to: today};
  if (range === 'month') return {from: today.slice(0, 8) + '01', to: today};
  if (range === 'custom' && custom?.from && custom?.to) return {from: custom.from, to: custom.to};
  return {from: useAdd(today, -6), to: today};
}
function useMoney(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n > 0 && n < 0.01) return '<$0.01';
  return '$' + n.toLocaleString(undefined, n >= 100 ? {maximumFractionDigits: 0} : {minimumFractionDigits: 2, maximumFractionDigits: 2});
}
function useCount(n: number | null | undefined): string {
  n = n || 0;
  if (n < 1000) return String(n);
  if (n < 1e6) return (n / 1e3).toFixed(n < 1e4 ? 1 : 0).replace(/\.0$/, '') + 'K';
  // 5.42B, not 5418.5M: each step past a million is its own unit.
  const [unit, size] = n < 1e9 ? ['M', 1e6] : n < 1e12 ? ['B', 1e9] : ['T', 1e12];
  const v = n / size;
  return v.toFixed(v < 10 ? 2 : 1).replace(/\.?0+$/, '') + unit;
}
// Cache writes are input too: input_tokens leaves them out since runners count them apart.
const useTokens = (r: UseFigures): number =>
  (r.input_tokens || 0) + (r.cached_tokens || 0) + (r.cache_write_tokens || 0) + (r.output_tokens || 0);
const useSpend = (r: UseFigures): number => (r.est_cost_usd || 0) + (r.subscription_equiv_usd || 0);
const useRuns = (n: number | null | undefined): string => `${(n || 0).toLocaleString()} run${n === 1 ? '' : 's'}`;
// Runs on a model whose long prompts cost more, without each request's tokens, were priced at the lower rate.
const useLowWhy = (n: number): string => `${useRuns(n)} on a model that charges more for long prompts did not report each request's tokens, so they are priced at the lower rate`;
// How the total is counted: behind the help button beside it (usePaint).
const USE_WHY = 'API list price, including runs on subscriptions. Details a runner did not report stay unknown. A run that used a fallback can count in two groups.';
const useLow = (r: UseFigures): string => r.low_estimate_runs > 0 ? `<span class="use-sub use-low" title="${esc(useLowWhy(r.low_estimate_runs))}">estimate, may be low</span>` : '';
// API-equivalent, or a dash for tokens on a model with no list price and no subscription runs.
const useEquiv = (r: UseFigures): number | null => r.est_cost_usd == null && !(r.subscription_equiv_usd > 0) ? null : useSpend(r);
const useBilled = (r: UseFigures): string => (r.est_cost_usd ?? 0) > 0 ? `${useMoney(r.est_cost_usd)} billed on API keys or Tico Gateway; the rest ran on subscriptions` : 'All on subscriptions';
function useCost(r: UseFigures): string {
  return `<span class="use-est" title="${esc(useBilled(r))}">${useMoney(useEquiv(r))}</span>${useLow(r)}`;
}
// Cache rate: the share of input tokens read from cache (cache writes are input too); null with no input tokens.
const useCacheRate = (r: UseFigures): number | null => {
  const input = (r.input_tokens || 0) + (r.cached_tokens || 0) + (r.cache_write_tokens || 0);
  return input ? (r.cached_tokens || 0) / input : null;
};
const useCacheWhy = 'Share of input tokens read from cache. On most models a cache read costs about a tenth of new input.';
const usePct = (n: number | null): string => n == null ? '—' : n > 0 && n < 0.005 ? '<1%' : Math.round(100 * n) + '%';
// The token parts in their fixed order: what each run sent and got back.
const USE_PARTS: [string, string, keyof UseFigures][] = [['read', 'Cache reads', 'cached_tokens'], ['write', 'Cache writes', 'cache_write_tokens'],
                                                         ['new', 'New input', 'input_tokens'], ['output', 'Output', 'output_tokens']];
const useShortDay = (day: string): string => new Date(day + 'T00:00:00Z').toLocaleDateString(undefined, {weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC'});
// A number a limit or a pool holds under a field name; anything else reads as no amount.
const useNum = (v: unknown): number | null => typeof v === 'number' ? v : null;
function useSaved(): { range?: string; department?: string } {
  try { return JSON.parse(localStorage.getItem('tico.usage') || '{}') || {}; } catch { return {}; }
}
function useSave(): void {
  try { if (!USE) return; localStorage.setItem('tico.usage', JSON.stringify({range: USE.range, department: USE.department})); } catch {}
}

function pageUsage(): void {
  const saved = useSaved();
  const span = useSpan('7d');
  const state: UseState = USE = {range: USE_RANGES.some(r => r[0] === saved.range) && saved.range !== 'custom' ? String(saved.range) : '7d',
                       department: saved.department || '', group: 'bot', sort: 'cost', filters: {}, custom: {from: span.from, to: span.to}, open: '', data: null, load: 0,
                       view: 'spend', edit: null};
  $('#main').innerHTML = `<div class="use-page">
    ${pageHead({title: 'Usage', hook: 'use-head',
      tabs: [{label: 'Spend', cur: true, attrs: 'data-use-view="spend"'}, {label: 'Limits', attrs: 'data-use-view="limits"'}],
      tabsHook: 'use-tabs', tabsAttrs: 'aria-label="View" hidden'})}
    <div id="use-limits" hidden></div>
    <div id="use-spend"><div class="use-bar-row">
      <div class="use-seg ph-seg" role="tablist" aria-label="Range">${USE_RANGES.map(([k, label]) =>
        `<button type="button" role="tab" data-use-range="${k}" aria-selected="${k === state.range}">${label}</button>`).join('')}</div>
      <span class="use-custom" hidden><input type="date" id="use-from" aria-label="From"><input type="date" id="use-to" aria-label="To"></span>
      <span class="use-filter-at">
        <button type="button" class="ghost use-filter" aria-haspopup="dialog" aria-expanded="false" aria-controls="use-filter-panel"><span class="nav-icon" aria-hidden="true">filter_list</span><span class="use-filter-t">Filter</span></button>
        <div id="use-filter-panel" class="use-filter-panel" role="dialog" aria-label="Filter usage" hidden>
          <label class="use-frow" hidden><span>Group</span><select id="use-dept"></select></label>
          ${USE_DIMENSIONS.map(([key, label]) => `<label class="use-frow" hidden><span>${label}</span><select data-use-filter="${key}" aria-label="Filter ${label}"><option value="">All</option></select></label>`).join('')}
          <hr class="use-fsep">
          <label class="use-frow"><span>Group by</span><select id="use-group">${[['bot', 'Bot'], ...USE_DIMENSIONS].map(([key, label]) => `<option value="${key}">${label}</option>`).join('')}</select></label>
          <label class="use-frow"><span>Sort by</span><select id="use-sort"><option value="cost">Cost</option><option value="tokens">Tokens</option><option value="runs">Runs</option><option value="name">Name</option></select></label>
          <div class="use-ffoot"><button type="button" class="linkish" data-use-reset>Reset</button></div>
        </div>
      </span>
      <button type="button" class="ghost use-default" id="use-purge" hidden>Clear automatic queue</button>
    </div>
    <div id="use-body" aria-live="polite"></div></div>
  </div>`;
  const main = $('#main');
  main.querySelector('.use-seg').onclick = (ev: MouseEvent) => {
    const b = eventElement(ev)?.closest<HTMLElement>('[data-use-range]'); if (!b || b.dataset.useRange === state.range) return;
    state.range = String(b.dataset.useRange); state.open = ''; useSave(); useLoad();
  };
  $('#use-group').onchange = (ev: Event) => { state.group = eventValue(ev); state.open = ''; useLoad(); };
  $('#use-sort').onchange = (ev: Event) => { state.sort = eventValue(ev); usePaint(); };
  main.querySelectorAll('[data-use-filter]').forEach((select: HTMLSelectElement) => select.onchange = () => {
    state.filters[String(select.dataset.useFilter)] = select.value; state.open = ''; useFilterCount(); useLoad();
  });
  $('#use-dept').onchange = (ev: Event) => { state.department = eventValue(ev); state.open = ''; useSave(); useFilterCount(); useLoad(); };
  useFilterWire(state);
  $('#use-from').onchange = $('#use-to').onchange = () => {
    state.custom = {from: $('#use-from').value, to: $('#use-to').value};
    if (state.custom.from && state.custom.to && state.custom.from <= state.custom.to) { state.open = ''; useLoad(); }
  };
  $('#use-body').onclick = (ev: MouseEvent) => {
    const el = eventElement(ev);
    const cap = el?.closest<HTMLElement>('[data-use-limit]');
    if (cap) {
      return void useView('limits', 'bot:' + String(cap.dataset.useLimit));
    }
    const line = el?.closest<HTMLElement>('.use-line');
    if (line && state.group === 'bot') return void useOpen(String(line.closest<HTMLElement>('.use-row')?.dataset.bot));
    if (el?.closest('[data-use-csv]')) useCsv();
    const why = el?.closest<HTMLElement>('.use-why');
    if (why) {
      state.why = !state.why;
      why.setAttribute('aria-expanded', String(state.why));
      const text = $('#use-why-text'); if (text) text.hidden = !state.why;
    }
  };
  main.querySelector('.use-tabs').onclick = (ev: MouseEvent) => { const b = eventElement(ev)?.closest<HTMLElement>('[data-use-view]'); if (b) useView(String(b.dataset.useView)); };
  $('#use-purge').onclick = () => usePurgeDialog(state.limits, (result: UsePurged) => {
    if (USE === state && $('#use-purge')) toast(`Cleared ${result.cancelled} queued · ${result.kept_people} kept · ${result.still_running} running`);
  });
  void useLimits();
  useFilterCount();
  useLoad();
}

// The Filter button reads "Filter · N" for the filters set (Group and the four dimensions; Group by and Sort by are how
// the list is shown, not what it holds). A Group kept from an earlier visit counts from the start.
function useFilterCount(): void {
  const state = USE, b = $('#main')?.querySelector('.use-filter'); if (!state || !b) return;
  const n = +!!state.department + USE_DIMENSIONS.filter(([key]) => state.filters[key]).length;
  b.querySelector('.use-filter-t').textContent = n ? `Filter · ${n}` : 'Filter';
  if (n) b.setAttribute('aria-label', `Filter, ${n} on`); else b.removeAttribute('aria-label');
}
// The panel stays in the page while closed (useLoad fills its selects by id). Opening puts the cursor in its first select;
// Escape, a click outside it or the button again closes it and gives the button the focus back.
function useFilterWire(state: UseState): void {
  const main = $('#main'), b = main.querySelector('.use-filter'), panel = $('#use-filter-panel');
  const close = (refocus: boolean): void => {
    if (panel.hidden) return;
    panel.hidden = true; b.setAttribute('aria-expanded', 'false');
    if (refocus) b.focus();
  };
  const open = (): void => {
    panel.hidden = false; b.setAttribute('aria-expanded', 'true');
    // Under the button, left edges aligned, moved left as far as it takes to stay in the window.
    panel.style.left = '0px';
    const over = panel.getBoundingClientRect().right - (document.documentElement.clientWidth - 12);
    if (over > 0) panel.style.left = -over + 'px';
    [...panel.querySelectorAll('select')].find((s: HTMLSelectElement) => !s.closest('[hidden]'))?.focus();
  };
  b.onclick = () => panel.hidden ? open() : close(true);
  panel.onkeydown = (ev: KeyboardEvent) => { if (ev.key === 'Escape') { ev.preventDefault(); close(true); } };
  panel.querySelector('[data-use-reset]').onclick = () => {
    state.department = ''; state.filters = {}; state.open = '';
    useSave(); useFilterCount(); useLoad();
  };
  const outside = (ev: MouseEvent): void => {
    if (!panel.isConnected) { document.removeEventListener('click', outside, true); if (USE_OUTSIDE === outside) USE_OUTSIDE = null; return; }
    const el = eventElement(ev);
    if (panel.hidden || el?.closest('.use-filter-at')) return;
    const inside = panel.contains(document.activeElement) || document.activeElement === document.body;
    close(inside);
  };
  if (USE_OUTSIDE) document.removeEventListener('click', USE_OUTSIDE, true);
  USE_OUTSIDE = outside;
  document.addEventListener('click', outside, true);
}

// The team default limit, the team's and groups' limits, and each bot's: the Limits tab shows when any is ours to change.
async function useLimits(): Promise<void> {
  const state = USE;
  if (!state) return;
  try { state.limits = await get<UseLimits>('/v2/usage/limits', {fresh: true}); } catch { state.limits = null; }
  if (USE !== state || !$('#use-limits')) return;
  $('#use-purge').hidden = !state.limits?.may_edit_default;
  const may = useLimitRows(state.limits, []).length > 0 || useLimitAddable(state.limits, []).length > 0;
  $('#main').querySelector('.use-tabs').hidden = !may;
  if (!may && state.view === 'limits') useView('spend');
  else if (state.view === 'limits') useLimitsPaint();
}
// Spend or Limits; `focus` (a row key such as bot:<slug>) adds that row if it is not shown and puts the cursor in it.
function useView(view: string, focus?: string): void {
  const state = USE; if (!state) return;
  state.view = view;
  for (const b of $('#main').querySelectorAll('[data-use-view]')) { b.setAttribute('aria-selected', String(b.dataset.useView === view)); b.classList.toggle('cur', b.dataset.useView === view); }
  $('#use-spend').hidden = view !== 'spend';
  $('#use-limits').hidden = view !== 'limits';
  if (view !== 'limits') return;
  state.edit = state.edit || {added: [], removed: new Set<string>()};
  if (focus && !useLimitRows(state.limits, state.edit.added).some(r => r.key === focus)) state.edit.added.push(focus);
  useLimitsPaint();
  if (focus) $('#use-limits').querySelector(`[data-lim-row="${CSS.escape(focus)}"] input`)?.focus();
}

const useParams = (state: UseState, extra: Record<string, string>): { from: string; to: string; query: string } => {
  const {from, to} = useSpan(state.range, state.custom);
  const q = new URLSearchParams({from, to, group: state.group, ...state.filters, ...extra});
  return {from, to, query: q.toString()};
};

async function useLoad(): Promise<void> {
  const state = USE; if (!state || !$('#use-body')) return;
  const mine = ++state.load, range = useParams(state, state.department ? {department: state.department} : {});
  $('#use-body').setAttribute('aria-busy', 'true');
  // Controls first, so a switch shows at once while the numbers load.
  for (const b of $('#main').querySelectorAll('[data-use-range]')) b.setAttribute('aria-selected', String(b.dataset.useRange === state.range));
  $('#main').querySelector('.use-custom').hidden = state.range !== 'custom';
  $('#use-from').value = range.from; $('#use-to').value = range.to;
  let data: UseData;
  try { data = await get<UseData>('/v2/usage?' + range.query); }
  catch (e) { if (USE === state && mine === state.load) $('#use-body').innerHTML = `<div class="err">${esc(errorMessage(e, ''))}</div>`; return; }
  if (USE !== state || mine !== state.load) return;      // a newer switch is already loading
  state.data = data;
  const select = $('#use-dept');
  select.innerHTML = `<option value="">All groups</option>` + (data.departments || []).map(d =>
    `<option value="${esc(d)}"${d === state.department ? ' selected' : ''}>${esc(d)}</option>`).join('');
  select.closest('.use-frow').hidden = !(data.departments || []).length;
  // A dimension shows in Filter when there is a choice to make in it, or one is made.
  for (const [key] of USE_DIMENSIONS) {
    const filter = $(`[data-use-filter="${key}"]`), selected = state.filters[key] || '';
    filter.closest('.use-frow').hidden = !selected && (data.dimensions?.[key] || []).length < 2;
    const values = [...new Set([...(data.dimensions?.[key] || []), ...(selected ? [selected] : [])])].sort();
    filter.innerHTML = '<option value="">All</option>' + values.map(value => `<option value="${esc(value)}">${esc(data.dimension_labels?.[key]?.[value] || (value === '__unknown__' ? 'Not recorded / not applicable' : value))}</option>`).join('');
    filter.value = selected;
  }
  usePaint();
}

function usePaint(): void {
  const state = USE; if (!state) return;
  const data = state.data, body = $('#use-body');
  if (!data || data.group !== state.group) return;
  body.removeAttribute('aria-busy');
  const rank = state.sort === 'tokens' ? useTokens : state.sort === 'runs' ? (r: UseRow) => r.runs : useSpend;
  const rows = [...(data.rows || [])].sort((a, b) => state.sort === 'name'
    ? (a.name || '').localeCompare(b.name || '') : rank(b) - rank(a) || (a.name || '').localeCompare(b.name || ''));
  if (!rows.length) { body.innerHTML = '<div class="empty">No usage yet.</div>'; return; }
  const t = data.totals;
  const top = Math.max(...rows.map(useSpend), 0);
  // The total, then the list; how it is counted waits behind the help button.
  const low = t.low_estimate_runs > 0 ? useLowWhy(t.low_estimate_runs) : '';
  body.innerHTML = `<div class="use-total"><strong id="use-total" title="${esc(useBilled(t))}">${useMoney(useEquiv(t))}</strong> <span class="use-note">API-equivalent · ${useRuns(t.runs)}</span>${
      low ? `<span class="use-low" title="${esc(low)}"> · may be low</span>` : ''}
      <button type="button" class="use-why" aria-expanded="${!!state.why}" aria-controls="use-why-text" aria-label="How this is counted"><span class="nav-icon" aria-hidden="true">help</span></button>
      <p id="use-why-text" class="muted"${state.why ? '' : ' hidden'}>${esc(USE_WHY)}${low ? ' ' + esc(low) + '.' : ''}</p></div>
    <ul class="use-list">${rows.map(r => `<li class="use-row" data-bot="${esc(r.bot)}">
      ${state.group === 'bot' ? useLimitButton(r.bot, r.name, r.limit) : '<span class="use-limit"></span>'}
      <${state.group === 'bot' ? 'button type="button"' : 'div'} class="use-line"${state.group === 'bot' ? ` aria-expanded="${state.open === r.bot}"` : ''}>
        <span class="use-who">${state.group === 'bot' ? botAvatar(r.bot, 28) : ''}<span class="use-name">${esc(r.name || r.bot)}</span></span>
        <span class="use-runs tnum">${useRuns(r.runs)}</span>
        <span class="use-tok tnum">${useCount(useTokens(r))} tokens</span>
        <span class="use-cost tnum">${useCost(r)}</span>
        <span class="use-share" role="img" aria-label="${Math.round((r.share || 0) * 100)}% of the total"><i style="width:${top ? Math.max(useSpend(r) ? 2 : 0, Math.round(100 * useSpend(r) / top)) : 0}%"></i></span>
      </${state.group === 'bot' ? 'button' : 'div'}>
      <div class="use-detail" ${state.open === r.bot ? '' : 'hidden'}></div></li>`).join('')}</ul>`;
  if (state.open) void useDetail(state.open);
}

function useOpen(slug: string): void {
  const state = USE; if (!state) return;
  state.open = state.open === slug ? '' : slug;
  for (const row of $('#use-body').querySelectorAll('.use-row')) {
    const on = row.dataset.bot === state.open;
    row.querySelector('.use-line').setAttribute('aria-expanded', String(on));
    row.querySelector('.use-detail').hidden = !on;
  }
  if (state.open) void useDetail(slug);
}

async function useDetail(slug: string): Promise<void> {
  const state = USE; if (!state) return;
  const row = $('#use-body').querySelector(`.use-row[data-bot="${CSS.escape(slug)}"]`);
  if (!row) return;
  const host = row.querySelector('.use-detail');
  host.innerHTML = '<div class="muted">Loading…</div>';
  let data: UseData;
  try { data = await get<UseData>('/v2/usage?' + useParams(state, {bot: slug}).query); }
  catch (e) { if (USE === state) host.innerHTML = `<div class="err">${esc(errorMessage(e, ''))}</div>`; return; }
  if (USE !== state || state.open !== slug) return;
  state.detail = data;
  host.innerHTML = useDetailHtml(data);
}

// The opened bot's detail: what it cost and how, where its tokens went, the tasks and models that used them, its days
// (only when the range has more than one), its automations and a CSV.
function useDetailHtml(data: UseData): string {
  const t = data.totals, all = useTokens(t), rate = useCacheRate(t);
  const sum = `<div class="use-sum">${useCost(t)} <span class="use-note">API-equivalent</span> · ${useRuns(t.runs)} · ${useCount(all)} tokens ·
    <span class="use-cache" title="${esc(useCacheWhy)}">${rate == null ? 'cache rate —' : usePct(rate) + ' from cache'}</span></div>`;
  const tokens = all ? `<div class="use-mix" role="img" aria-label="${esc(USE_PARTS.map(([, label, key]) => `${label} ${usePct(Number(t[key] || 0) / all)}`).join(', '))}">${
      USE_PARTS.map(([part, , key]) => `<i data-part="${part}" style="width:${(100 * Number(t[key] || 0) / all).toFixed(2)}%"></i>`).join('')}</div>
    <ul class="use-legend">${USE_PARTS.map(([part, label, key]) =>
      `<li><i data-part="${part}"></i>${label} <b class="tnum">${useCount(Number(t[key] || 0))}</b> · ${usePct(Number(t[key] || 0) / all)}</li>`).join('')}</ul>`
    : `<p class="muted">${t.runs ? `No token counts reported for ${useRuns(t.runs)}` : 'No runs in this range.'}</p>`;
  const table = (kind: string, head: string, items: UseItem[], more = '') => `<table class="use-items use-${kind}"><thead><tr><th>${head}</th>
      <th class="use-c-runs">Runs</th><th class="use-c-tok">Tokens</th><th title="${esc(useCacheWhy)}">Cache</th><th class="use-c-cost">Cost</th><th class="use-c-share"><span class="sr-only">Share of cost</span></th></tr></thead>
    <tbody>${items.map(r => `<tr><td class="use-c-name">${esc(r.name)}</td><td class="use-c-runs tnum">${r.runs.toLocaleString()}</td>
      <td class="use-c-tok tnum">${useCount(useTokens(r))}</td><td class="tnum">${usePct(useCacheRate(r))}</td>
      <td class="use-c-cost tnum"><span class="use-cost">${useCost(r)}</span></td>
      <td class="use-c-share"><span class="use-share" role="img" aria-label="${Math.round((r.share || 0) * 100)}% of the bot's cost"><i style="width:${Math.round((r.share || 0) * 100)}%"></i></span></td></tr>`).join('')
      || '<tr><td colspan="6" class="muted">None.</td></tr>'}${more}</tbody></table>`;
  const rest = data.tasks_more;
  const more = rest?.count ? `<tr class="use-more"><td colspan="6">${rest.count} more task${rest.count === 1 ? '' : 's'} · ${useMoney(useEquiv(rest))}</td></tr>` : '';
  const days = data.daily || [], peak = Math.max(...days.map(useSpend), 0);
  const chart = days.length < 2 ? '' : `<section class="use-sec"><h3>Cost per day</h3>
    <div class="use-plot"><span class="use-top tnum">${useMoney(peak)}</span>
      <div class="use-chart" role="img" aria-label="API-equivalent cost per day, ${esc(useShortDay(days[0]!.day))} to ${esc(useShortDay(days[days.length - 1]!.day))}, at most ${esc(useMoney(peak))}">${days.map(d => {
        const v = useSpend(d), r = useCacheRate(d);
        const tip = `${useShortDay(d.day)} · ${useMoney(useEquiv(d))} · ${useRuns(d.runs)}${r == null ? '' : ` · ${usePct(r)} from cache`}`;
        return `<span class="use-col" title="${esc(tip)}" data-day="${esc(d.day)}">${v && peak ? `<i class="use-api" style="height:${(100 * v / peak).toFixed(1)}%"></i>` : ''}</span>`;
      }).join('')}</div></div>
    <div class="use-axis"><span>${esc(useShortDay(days[0]!.day))}</span><span>${esc(useShortDay(days[days.length - 1]!.day))}</span></div></section>`;
  const routines = (data.routines || []).filter(r => r.runs);
  return `${sum}
    <section class="use-sec"><h3>Tokens</h3>${tokens}</section>
    <section class="use-sec"><h3>Tasks</h3>${table('tasks', 'Task', data.tasks || [], more)}</section>
    <section class="use-sec"><h3>Models</h3>${table('models', 'Model', data.models || [])}</section>
    ${chart}
    <section class="use-sec"><h3>Automations</h3><table class="use-routines"><thead><tr><th>Automation</th><th>Runs</th><th>Cost</th></tr></thead><tbody>${routines.map(r =>
      `<tr><td>${esc(r.title)}</td><td class="tnum">${r.runs}</td><td class="tnum"><span class="use-cost">${useCost(r)}</span></td></tr>`).join('')
      || '<tr><td colspan="3" class="muted">None.</td></tr>'}</tbody></table></section>
    <button type="button" class="ghost" data-use-csv>Export CSV</button>`;
}

// The open bot's days as a CSV, in the units the API returns.
function useCsv(): void {
  const data = USE?.detail; if (!data) return;
  const head: (keyof UseDay)[] = ['day', 'runs', 'input_tokens', 'cached_tokens', 'cache_write_tokens', 'output_tokens', 'est_cost_usd', 'subscription_equiv_usd'];
  const cell = (v: unknown): string => v == null ? '' : String(v);
  const rate = (d: UseDay): string => { const r = useCacheRate(d); return r == null ? '' : String(Math.round(r * 1e4) / 1e4); };
  const text = [[...head, 'cache_rate'].join(','), ...(data.daily || []).map(d => [...head.map(k => cell(d[k])), rate(d)].join(','))].join('\n') + '\n';
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([text], {type: 'text/csv'}));
  link.download = `usage-${data.bot}-${data.from}-${data.to}.csv`;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

// ----------------------------------------------------------------- limits
// A cap in estimated USD per 30 minutes, day, week and month (backend/usage_limits.py): on a bot, on the combined spend of
// a group or of the whole team, and the default for each bot with none of its own. Over one, the bots it covers take no
// new job; at 80% someone is told. The hour came before the half hour replaced it: still enforced where it is set, so it
// shows only where it is set and is never sent elsewhere (a new hour would be saved as half of it per 30 minutes).
// [field, column, unit in a row's summary, key of `spent`]
const USE_PERIODS: [string, string, string, string][] = [['per_30m_usd', '30 min', '/30m', '30m'], ['hourly_usd', 'Hour', '/hr', 'hour'], ['daily_usd', 'Day', '/day', 'day'],
                     ['weekly_usd', 'Week', '/wk', 'week'], ['monthly_usd', 'Month', '/mo', 'month']];
const USE_WORDS: Record<string, string> = {per_30m: '30-minute', hourly: 'hourly', daily: 'daily', weekly: 'weekly', monthly: 'monthly'};
const useCap = (n: number): string => '$' + (n >= 100 || Number.isInteger(n) ? n.toLocaleString(undefined, {maximumFractionDigits: 0}) : n.toFixed(2));
const usePlain = (n: number): string => String(Math.round(n * 100) / 100);
function useLimitText(limit: UseLimit | null | undefined): string {
  return USE_PERIODS.filter(([key]) => limit?.[key]).map(([key, , unit]) => useCap(useNum(limit?.[key]) ?? 0) + unit).join(' · ');
}
// What holds a bot, in words: its own limit, or the team's or a group's.
const useHeldBy = (limit: UseLimit | null | undefined): string => limit?.blocked_by && limit.blocked_by.scope !== 'bot'
  ? `${limit.blocked_by.name} is over its ${USE_WORDS[String(limit.blocked_by.period)] || limit.blocked_by.period} limit`
  : `Over its ${USE_WORDS[String(limit?.blocked)] || limit?.blocked} limit`;
// The cell on a Usage row and in Settings > Bots: the limit, a chip when it is near or met, and a click to change it.
function useLimitButton(slug: string, name: string | undefined, limit: UseLimit | null | undefined): string {
  if (!limit?.may_edit) return '<span class="use-limit"></span>';
  const text = useLimitText(limit);
  const chip = limit.blocked ? '<span class="pill fail">Paused</span>' : limit.percent >= 80 ? `<span class="pill waiting">${limit.percent}%</span>` : '';
  return `<button type="button" class="use-limit${text ? '' : ' none'}" data-use-limit="${esc(slug)}" aria-label="Limit for ${esc(name || slug)}"
    title="${limit.blocked ? esc(`${useHeldBy(limit)}: no new work until it resets or the limit is raised`) : 'Change limit'}">${chip}<span>${text ? esc(text) : 'Set limit'}</span></button>`;
}

// Groups ordered as the org nests them: by the names from the top group down.
function useGroupOrder(groups: Record<string, UseLimit>): [string, UseLimit, string[]][] {
  const chain = (id: string): string[] => {
    const out: string[] = []; let at: string | undefined = id;
    while (at && out.length < 8) { const g: UseLimit | undefined = groups[at]; if (!g) break; out.unshift(g.name || at); at = g.parent; }
    return out;
  };
  return Object.entries(groups).map(([id, g]): [string, UseLimit, string[]] => [id, g, chain(id)]).sort((a, b) => a[2].join('\u0000').localeCompare(b[2].join('\u0000')));
}
const useBotName = (slug: string): string => typeof botDisplayName === 'function' ? botDisplayName(slug) : slug;
const useHasCap = (row: UseLimit | null | undefined, prefix = ''): boolean => USE_PERIODS.some(([key]) => row?.[prefix + key] != null);
// The Limits tab's rows: the default for each bot and the whole team (owner and administrators), then each group and bot
// with a limit of its own and those added since the last save. A bot's empty window follows the default, so the default
// is its hint.
function useLimitRows(limits: UseLimits | null | undefined, added: string[]): UseLimitRow[] {
  if (!limits) return [];
  const def: UseDefault = limits.default || {}, pools: UsePools = limits.pools || {}, out: UseLimitRow[] = [];
  if (limits.may_edit_default) out.push({key: 'default', kind: 'default', name: 'Each bot', path: '/v2/usage/limits', value: k => useNum(def[k]), hint: () => ''});
  const company = pools.company;
  if (company) out.push({key: 'company', kind: 'company', name: 'Whole team', path: '/v2/usage/pools/company', state: company,
                         value: k => useNum(company[k]), hint: () => ''});
  for (const [id, g, chain] of useGroupOrder(pools.groups || {}))
    if ((limits.may_edit_default || g.may_edit) && (useHasCap(g) || added.includes('group:' + id)))
      out.push({key: 'group:' + id, kind: 'group', name: g.name || id, title: chain.join(' › '), path: '/v2/usage/pools/groups/' + encodeURIComponent(id),
                state: g, value: k => useNum(g[k]), hint: () => '', removable: true});
  const bots = Object.entries(limits.bots || {}).filter(([slug, b]) => b.may_edit && (useHasCap(b, 'own_') || added.includes('bot:' + slug)))
    .map(([slug, b]): UseLimitRow => ({key: 'bot:' + slug, kind: 'bot', slug, name: useBotName(slug), path: '/v2/usage/limits/' + encodeURIComponent(slug), state: b,
                          value: k => useNum(b['own_' + k]), hint: k => { const d = useNum(def[k]); return d != null ? useCap(d) : ''; }, removable: true}));
  return out.concat(bots.sort((a, b) => a.name.localeCompare(b.name)));
}
// What can be added: the groups and bots this person may limit that have no row.
function useLimitAddable(limits: UseLimits | null | undefined, added: string[]): [string, string][] {
  if (!limits) return [];
  const shown = new Set(useLimitRows(limits, added).map(r => r.key));
  const groups = useGroupOrder(limits.pools?.groups || {}).filter(([id, g]) => (limits.may_edit_default || g.may_edit) && !shown.has('group:' + id))
    .map(([id, g, chain]): [string, string] => ['group:' + id, chain.join(' › ') || g.name || id]);
  const bots = Object.entries(limits.bots || {}).filter(([slug, b]) => b.may_edit && !shown.has('bot:' + slug))
    .map(([slug]): [string, string] => ['bot:' + slug, useBotName(slug)]).sort((a, b) => a[1].localeCompare(b[1]));
  return [...groups, ...bots];
}

function useLimitsPaint(): void {
  const state = USE, host = $('#use-limits'); if (!state || !host) return;
  const limits = state.limits, edit = state.edit;
  if (!limits) { host.innerHTML = limits === undefined ? '<div class="muted">Loading…</div>' : '<div class="err">Limits did not load.</div>'; return; }
  if (!edit) return;
  // What was typed survives a repaint (a row added or removed, a reload after a save that failed part way).
  const typed = Object.fromEntries([...host.querySelectorAll('input[name]')].map((i): [string, string | boolean] => [i.name, i.type === 'checkbox' ? i.checked : i.value]));
  const rows = useLimitRows(limits, edit.added);
  const periods = USE_PERIODS.filter(([key]) => key !== 'hourly_usd' || rows.some(r => r.value(key) != null));
  const add = useLimitAddable(limits, edit.added);
  const opts = (label: string, list: [string, string][]): string => list.length ? `<optgroup label="${label}">${list.map(([k, n]) => `<option value="${esc(k)}">${esc(n)}</option>`).join('')}</optgroup>` : '';
  host.innerHTML = `<div class="use-lim" style="--use-n:${periods.length}" role="table" aria-label="Limits">
      <div class="use-lim-row use-lim-head" role="row"><span role="columnheader"></span>${periods.map(([, label]) => `<span role="columnheader">${label}</span>`).join('')}<span></span></div>
      ${rows.map(r => useLimitRowHtml(r, periods, edit.removed.has(r.key))).join('')}
    </div>
    <div class="use-lim-foot">
      ${add.length ? `<select class="use-lim-add" aria-label="Add a limit"><option value="">Add limit…</option>${opts('Groups', add.filter(([k]) => k.startsWith('group:')))}${opts('Bots', add.filter(([k]) => k.startsWith('bot:')))}</select>` : ''}
      ${limits.may_edit_default ? `<label class="use-check" title="Runs on a ChatGPT or Claude sign-in count at their API price"><input type="checkbox" name="count_subscription"${limits.default?.count_subscription ? ' checked' : ''}>Count subscription runs</label>` : ''}
      <span class="use-lim-gap"></span>
      <span class="err" data-lim-err${edit.error ? '' : ' hidden'}>${esc(edit.error || '')}</span>
      <button type="button" class="ghost" data-lim-discard>Discard</button><button type="button" class="primary" data-lim-save>Save</button>
    </div>`;
  for (const input of host.querySelectorAll('input[name]')) {
    if (!(input.name in typed)) continue;
    const v = typed[input.name];
    if (input.type === 'checkbox') { if (typeof v === 'boolean') input.checked = v; }
    else if (typeof v === 'string') input.value = v;
  }
  host.oninput = host.onchange = (ev: Event) => {
    const pick = eventElement(ev)?.closest<HTMLSelectElement>('.use-lim-add');
    if (!pick) return void useLimitsDirty();
    if (ev.type !== 'change' || !pick.value) return;
    const key = pick.value;
    edit.added.push(key);
    useLimitsPaint();
    host.querySelector(`[data-lim-row="${CSS.escape(key)}"] input`)?.focus();
  };
  host.onkeydown = (ev: KeyboardEvent) => { const el = eventElement(ev); if (ev.key === 'Enter' && el?.matches('input:not([type=checkbox])')) { ev.preventDefault(); void useLimitsSave(); } };
  host.onclick = (ev: MouseEvent) => {
    const x = eventElement(ev)?.closest<HTMLElement>('[data-lim-remove]');
    if (x) {
      const key = String(x.closest<HTMLElement>('[data-lim-row]')?.dataset.limRow), row = useLimitRows(limits, edit.added).find(r => r.key === key);
      // A row added and never saved just goes; one with saved limits is struck out until Save clears them.
      if (row && !USE_PERIODS.some(([k]) => row.value(k) != null)) edit.added = edit.added.filter(k => k !== key);
      else if (edit.removed.has(key)) edit.removed.delete(key); else edit.removed.add(key);
      useLimitsPaint();
      return;
    }
    if (eventElement(ev)?.closest('[data-lim-discard]')) {
      state.edit = {added: [], removed: new Set<string>()};
      host.innerHTML = '';
      useLimitsPaint();
      return;
    }
    if (eventElement(ev)?.closest('[data-lim-save]')) void useLimitsSave();
  };
  useLimitsDirty();
}
function useLimitRowHtml(r: UseLimitRow, periods: [string, string, string, string][], removed: boolean): string {
  const icon = r.kind === 'bot' ? botAvatar(String(r.slug), 20)
    : `<span class="nav-icon use-lim-icon" aria-hidden="true">${r.kind === 'group' ? 'groups' : r.kind === 'company' ? 'account_balance' : 'smart_toy'}</span>`;
  const st = r.state;
  const chip = !st ? '' : st.blocked ? `<span class="pill fail" title="${esc(useHeldBy(st))}">Paused</span>`
    : st.percent >= 80 ? `<span class="pill waiting">${st.percent}%</span>` : '';
  const cells = periods.map(([k, label, , w]) => {
    const v = r.value(k), head = `<span class="use-lim-h" aria-hidden="true">${label}</span>`;
    if (k === 'hourly_usd' && v == null) return `<span class="use-lim-cell" role="cell"></span>`;
    // The bar is what this window has spent against the cap that applies: a bot's own, else the default.
    const cap = (r.kind === 'bot' ? useNum(st?.[k]) : v) ?? 0, spent = st?.spent?.[w];
    const pct = cap > 0 && spent != null && !removed ? Math.min(100, Math.round(100 * spent / cap)) : null;
    const meter = pct == null ? '' : `<i class="use-meter${pct >= 100 ? ' bad' : pct >= 80 ? ' warn' : ''}" title="${esc(`${useMoney(spent)} of ${useCap(cap)}`)}"><b style="width:${pct}%"></b></i>`;
    return `<span class="use-lim-cell" role="cell">${head}<label class="use-money"><span aria-hidden="true">$</span><input name="${esc(r.key + '|' + k)}" inputmode="decimal" autocomplete="off"
      value="${v == null ? '' : usePlain(v)}" placeholder="${esc(r.hint(k) || '—')}" aria-label="${esc(r.name)}, ${label}"${removed ? ' disabled' : ''}></label>${meter}</span>`;
  }).join('');
  const end = r.removable ? `<button type="button" class="ghost use-lim-x" data-lim-remove aria-label="${removed ? 'Keep' : 'Remove'} ${esc(r.name)}" title="${removed ? 'Undo' : 'Remove'}">
      <span aria-hidden="true">${removed ? '↩' : '✕'}</span></button>` : '';
  return `<div class="use-lim-row${removed ? ' removed' : ''}" role="row" data-lim-row="${esc(r.key)}">
    <span class="use-lim-who" role="rowheader"${r.title ? ` title="${esc(r.title)}"` : ''}>${icon}<span class="use-lim-name">${esc(r.name)}</span>${chip}</span>${cells}<span class="use-lim-end">${end}</span></div>`;
}
// What Save sends: for each row, one PUT of only the windows whose amount changed (empty clears one), every window a
// removed row sets cleared, and the default's subscription switch when it moved. Nothing else is sent, so nothing else
// changes. Marks each changed or unreadable field as it goes.
function useLimitChanges(): [string, Record<string, number | boolean | null>][] {
  const state = USE, host = $('#use-limits'), limits = state?.limits, edit = state?.edit;
  const writes: [string, Record<string, number | boolean | null>][] = [];
  if (!state || !edit) return writes;
  let problem = '';
  for (const r of useLimitRows(limits, edit.added)) {
    const body: Record<string, number | boolean | null> = {};
    if (edit.removed.has(r.key)) {
      for (const [k] of USE_PERIODS) if (r.value(k) != null) body[k] = null;
    } else for (const [k, label] of USE_PERIODS) {
      const input = host.querySelector(`input[name="${CSS.escape(r.key + '|' + k)}"]`); if (!input) continue;
      const t = input.value.trim().replace(/^\$\s*/, '').replace(/,/g, '');
      const bad = t !== '' && (!/^\d+(\.\d{1,2})?$/.test(t) || Number(t) <= 0);
      input.toggleAttribute('aria-invalid', bad);
      if (bad) { problem = problem || `${r.name}, ${label}: an amount above $0, or empty for none`; continue; }
      const now = r.value(k), want = t === '' ? null : Number(t);
      const changed = now == null ? want != null : want == null || Math.abs(want - now) > 0.004;
      input.closest('.use-money').classList.toggle('changed', changed);
      if (changed) body[k] = want;
    }
    const box = r.kind === 'default' && host.querySelector('input[name=count_subscription]');
    if (box && box.checked !== !!limits?.default?.count_subscription) body.count_subscription = box.checked;
    if (Object.keys(body).length) writes.push([r.path, body]);
  }
  if (problem) throw new Error(problem);
  return writes;
}
function useLimitsDirty(): void {
  const state = USE, host = $('#use-limits');
  if (!state?.edit) return;
  let writes: [string, Record<string, number | boolean | null>][] = [], bad = false;
  try { writes = useLimitChanges(); } catch { bad = true; }
  const dirty = writes.length > 0 || bad;
  host.querySelector('[data-lim-save]').disabled = !dirty;
  host.querySelector('[data-lim-discard]').disabled = !dirty && !state.edit.added.length;
  host.querySelector('.use-lim-foot').classList.toggle('dirty', dirty);
}
// One PUT per changed row, in order. Whatever saved is read back; what did not stays typed with the error beside Save.
async function useLimitsSave(): Promise<void> {
  const state = USE, host = $('#use-limits'), edit = state?.edit;
  if (!state || !edit) return;
  let writes: [string, Record<string, number | boolean | null>][];
  try { writes = useLimitChanges(); }
  catch (e) { edit.error = errorMessage(e, ''); const err = host.querySelector('[data-lim-err]'); err.textContent = edit.error; err.hidden = false; return; }
  if (!writes.length) return;
  host.querySelector('[data-lim-save]').disabled = true;
  edit.error = '';
  let failed: unknown = null;
  for (const [path, body] of writes) {
    try { await put(path, body); } catch (e) { failed = e; break; }
  }
  if (USE !== state) return;
  if (failed) edit.error = errorMessage(failed, '');
  else { state.edit = {added: [], removed: new Set<string>()}; host.innerHTML = ''; toast('Saved'); }   // read back as saved
  await useLimits();
  void useLoad();
}
// Settings > Bots: one bot's limits in a dialog. The Usage page edits every limit in its Limits tab.
const useAmount = (value: unknown): number | null => { const t = String(value).trim(); return t === '' ? null : Number(t); };
const useAmounts = (form: HTMLFormElement): Record<string, number | null> => Object.fromEntries(USE_PERIODS.filter(([key]) => form[key]).map(([key]) => [key, useAmount(form[key].value)]));
function useDialog<R>(label: string, body: string, save: (form: HTMLFormElement) => R, done?: (result: Awaited<R>) => void, submitLabel = 'Save'): HTMLDialogElement {
  const dialog = document.createElement('dialog');
  dialog.className = 'tmodal use-dialog';
  dialog.setAttribute('aria-label', label);
  dialog.innerHTML = `<div class="tmodal-head"><span class="who">${esc(label)}</span><span class="spacer"></span><button type="button" class="ghost tmodal-x" data-close aria-label="Close">✕</button></div>
    <form class="tmodal-body" novalidate>${body}<p class="err" data-error hidden></p>
      <div class="use-dialog-row"><button type="button" class="ghost" data-close>Cancel</button><button type="submit" class="primary">${esc(submitLabel)}</button></div></form>`;
  document.body.appendChild(dialog);
  dialog.querySelectorAll<HTMLButtonElement>('[data-close]').forEach(b => b.onclick = () => dialog.close());
  dialog.onclose = () => dialog.remove();
  const form = dialog.querySelector<HTMLFormElement>('form'), err = dialog.querySelector<HTMLParagraphElement>('[data-error]');
  if (!form || !err) return dialog;
  form.onsubmit = async ev => {
    ev.preventDefault(); err.hidden = true;
    const submit = form.querySelector<HTMLButtonElement>('[type=submit]'); if (submit) submit.disabled = true;
    try { const result = await save(form); dialog.close(); done && done(result); }
    catch (e) { err.textContent = errorMessage(e, ''); err.hidden = false; if (submit) submit.disabled = false; }
  };
  dialog.showModal();
  form.querySelector('input')?.focus();
  return dialog;
}
function useLimitDialog(slug: string, name: string | undefined, limit: UseLimit | null | undefined, company: UseLimit | UseDefault | null | undefined, done: () => void): void {
  const hint = (key: string): string => { const c = useNum(company?.[key]); return c ? useCap(c) : '—'; };
  const held = limit?.blocked_by && limit.blocked_by.scope !== 'bot' ? `<p class="muted">${esc(useHeldBy(limit))}; raising this bot's own limit does not lift it.</p>` : '';
  const periods = USE_PERIODS.filter(([key]) => key !== 'hourly_usd' || limit?.own_hourly_usd != null);
  const fields = periods.map(([key, label]) => {
    const own = useNum(limit?.['own_' + key]);
    return `<label>${label}<span class="use-money"><span aria-hidden="true">$</span><input name="${key}" inputmode="decimal" autocomplete="off"
      value="${own == null ? '' : usePlain(own)}" placeholder="${esc(hint(key))}"></span></label>`;
  }).join('');
  useDialog(name || slug, held + `<div class="use-fields">${fields}</div>`,
    form => put('/v2/usage/limits/' + encodeURIComponent(slug), useAmounts(form)), done);
}
// Drop queued automatic wakes and waits. Tasks, people's messages and live runs stay.
function usePurgeDialog(limits: UseLimits | null | undefined, done: (result: UsePurged) => void): void {
  const scopes: [string, string][] = [['company', 'Whole team'], ...useGroupOrder(limits?.pools?.groups || {})
    .map(([id, g, chain]): [string, string] => ['group:' + id, '  '.repeat(chain.length) + (g.name || id)])];
  useDialog('Clear automatic queue',
    `<label>Applies to<select name="scope">${scopes.map(([key, label]) => `<option value="${esc(key)}">${esc(label)}</option>`).join('')}</select></label>
     <p>Cancels queued automatic wakes and waits. Keeps tasks, people's messages and running work.</p>`,
    form => post('/v2/usage/queues/purge', {scope: form.scope.value}), done, 'Clear queue');
}
