/* ui/app/usage.js — Usage page: estimated model spend per bot
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- usage
// GET /api/v2/usage (backend/usage.py, docs/usage.md): tokens each run used, priced at list price. Days are UTC.
// Every figure shown is API-equivalent: billed spend (API keys, Tico Gateway) plus what subscription runs would have cost
// at list price. Billed spend alone is in the hover, since a bot's limits count only that.
const USE_RANGES = [['today', 'Today'], ['7d', '7 days'], ['30d', '30 days'], ['month', 'This month'], ['custom', 'Custom']];
const USE_DIMENSIONS = [['harness', 'Harness'], ['model', 'Model'], ['effort', 'Effort'], ['subscription', 'Subscription']];
let USE = null;
const useDay = date => date.toISOString().slice(0, 10);
const useAdd = (day, n) => useDay(new Date(Date.parse(day + 'T00:00:00Z') + n * 864e5));
function useSpan(range, custom) {
  const today = useDay(new Date());
  if (range === 'today') return {from: today, to: today};
  if (range === '30d') return {from: useAdd(today, -29), to: today};
  if (range === 'month') return {from: today.slice(0, 8) + '01', to: today};
  if (range === 'custom' && custom?.from && custom?.to) return {from: custom.from, to: custom.to};
  return {from: useAdd(today, -6), to: today};
}
function useMoney(n) {
  if (n == null) return '—';
  if (n > 0 && n < 0.01) return '<$0.01';
  return '$' + n.toLocaleString(undefined, n >= 100 ? {maximumFractionDigits: 0} : {minimumFractionDigits: 2, maximumFractionDigits: 2});
}
function useCount(n) {
  n = n || 0;
  if (n < 1000) return String(n);
  if (n < 1e6) return (n / 1e3).toFixed(n < 1e4 ? 1 : 0).replace(/\.0$/, '') + 'K';
  // 5.42B, not 5418.5M: each step past a million is its own unit.
  const [unit, size] = n < 1e9 ? ['M', 1e6] : n < 1e12 ? ['B', 1e9] : ['T', 1e12];
  const v = n / size;
  return v.toFixed(v < 10 ? 2 : 1).replace(/\.?0+$/, '') + unit;
}
const useTokens = r => (r.input_tokens || 0) + (r.cached_tokens || 0) + (r.output_tokens || 0);
const useSpend = r => (r.est_cost_usd || 0) + (r.subscription_equiv_usd || 0);
const useRuns = n => `${(n || 0).toLocaleString()} run${n === 1 ? '' : 's'}`;
// Runs on a model whose long prompts cost more, without each request's tokens, were priced at the lower rate.
const useLowWhy = n => `${useRuns(n)} on a model that charges more for long prompts did not report each request's tokens, so they are priced at the lower rate`;
const useLow = r => r.low_estimate_runs > 0 ? `<span class="use-sub use-low" title="${esc(useLowWhy(r.low_estimate_runs))}">estimate, may be low</span>` : '';
// API-equivalent, or a dash for tokens on a model with no list price and no subscription runs.
const useEquiv = r => r.est_cost_usd == null && !(r.subscription_equiv_usd > 0) ? null : useSpend(r);
const useBilled = r => r.est_cost_usd > 0 ? `${useMoney(r.est_cost_usd)} billed on API keys or Tico Gateway; the rest ran on subscriptions` : 'All on subscriptions';
function useCost(r) {
  return `<span class="use-est" title="${esc(useBilled(r))}">${useMoney(useEquiv(r))}</span>${useLow(r)}`;
}
function useSaved() {
  try { return JSON.parse(localStorage.getItem('tico.usage') || '{}') || {}; } catch { return {}; }
}
function useSave() {
  try { localStorage.setItem('tico.usage', JSON.stringify({range: USE.range, department: USE.department})); } catch {}
}

function pageUsage() {
  const saved = useSaved();
  const span = useSpan('7d');
  const state = USE = {range: USE_RANGES.some(r => r[0] === saved.range) && saved.range !== 'custom' ? saved.range : '7d',
                       department: saved.department || '', group: 'bot', sort: 'cost', filters: {}, custom: {from: span.from, to: span.to}, open: '', data: null, load: 0,
                       view: 'spend', edit: null};
  $('#main').innerHTML = `<div class="use-page">
    <div class="use-head"><h1>Usage</h1>
      <div class="use-tabs" role="tablist" aria-label="View" hidden><button type="button" role="tab" data-use-view="spend" aria-selected="true">Spend</button><button type="button" role="tab" data-use-view="limits" aria-selected="false">Limits</button></div></div>
    <div id="use-limits" hidden></div>
    <div id="use-spend"><div class="use-bar-row">
      <div class="use-seg" role="tablist" aria-label="Range">${USE_RANGES.map(([k, label]) =>
        `<button type="button" role="tab" data-use-range="${k}" aria-selected="${k === state.range}">${label}</button>`).join('')}</div>
      <select id="use-dept" aria-label="Group" hidden></select>
      <span class="use-custom" hidden><input type="date" id="use-from" aria-label="From"><input type="date" id="use-to" aria-label="To"></span>
    </div>
    <div class="use-bar-row">
      <label>Group by <select id="use-group">${[['bot', 'Bot'], ...USE_DIMENSIONS].map(([key, label]) => `<option value="${key}">${label}</option>`).join('')}</select></label>
      <label>Sort by <select id="use-sort"><option value="cost">Cost</option><option value="tokens">Tokens</option><option value="runs">Runs</option><option value="name">Name</option></select></label>
      ${USE_DIMENSIONS.map(([key, label]) => `<label>${label} <select data-use-filter="${key}" aria-label="Filter ${label}"><option value="">All</option></select></label>`).join('')}
    </div>
    <p class="muted">Historical details not reported by a runner remain unknown. A run using a fallback can appear in more than one group.</p>
    <div id="use-body" aria-live="polite"></div></div>
  </div>`;
  const main = $('#main');
  main.querySelector('.use-seg').onclick = ev => {
    const b = ev.target.closest('[data-use-range]'); if (!b || b.dataset.useRange === state.range) return;
    state.range = b.dataset.useRange; state.open = ''; useSave(); useLoad();
  };
  $('#use-group').onchange = ev => { state.group = ev.target.value; state.open = ''; useLoad(); };
  $('#use-sort').onchange = ev => { state.sort = ev.target.value; usePaint(); };
  main.querySelectorAll('[data-use-filter]').forEach(select => select.onchange = () => {
    state.filters[select.dataset.useFilter] = select.value; state.open = ''; useLoad();
  });
  $('#use-dept').onchange = ev => { state.department = ev.target.value; state.open = ''; useSave(); useLoad(); };
  $('#use-from').onchange = $('#use-to').onchange = () => {
    state.custom = {from: $('#use-from').value, to: $('#use-to').value};
    if (state.custom.from && state.custom.to && state.custom.from <= state.custom.to) { state.open = ''; useLoad(); }
  };
  $('#use-body').onclick = ev => {
    const cap = ev.target.closest('[data-use-limit]');
    if (cap) {
      return void useView('limits', 'bot:' + cap.dataset.useLimit);
    }
    const line = ev.target.closest('.use-line');
    if (line && state.group === 'bot') return void useOpen(line.closest('.use-row').dataset.bot);
    if (ev.target.closest('[data-use-csv]')) useCsv();
  };
  main.querySelector('.use-tabs').onclick = ev => { const b = ev.target.closest('[data-use-view]'); if (b) useView(b.dataset.useView); };
  void useLimits();
  useLoad();
}

// The team default limit, the team's and groups' limits, and each bot's: the Limits tab shows when any is ours to change.
async function useLimits() {
  const state = USE;
  try { state.limits = await get('/v2/usage/limits', {fresh: true}); } catch { state.limits = null; }
  if (USE !== state || !$('#use-limits')) return;
  const may = useLimitRows(state.limits, []).length > 0 || useLimitAddable(state.limits, []).length > 0;
  $('#main').querySelector('.use-tabs').hidden = !may;
  if (!may && state.view === 'limits') useView('spend');
  else if (state.view === 'limits') useLimitsPaint();
}
// Spend or Limits; `focus` (a row key such as bot:<slug>) adds that row if it is not shown and puts the cursor in it.
function useView(view, focus) {
  const state = USE; if (!state) return;
  state.view = view;
  for (const b of $('#main').querySelectorAll('[data-use-view]')) b.setAttribute('aria-selected', String(b.dataset.useView === view));
  $('#use-spend').hidden = view !== 'spend';
  $('#use-limits').hidden = view !== 'limits';
  if (view !== 'limits') return;
  state.edit = state.edit || {added: [], removed: new Set()};
  if (focus && !useLimitRows(state.limits, state.edit.added).some(r => r.key === focus)) state.edit.added.push(focus);
  useLimitsPaint();
  if (focus) $('#use-limits').querySelector(`[data-lim-row="${CSS.escape(focus)}"] input`)?.focus();
}

const useParams = (state, extra) => {
  const {from, to} = useSpan(state.range, state.custom);
  const q = new URLSearchParams({from, to, group: state.group, ...state.filters, ...extra});
  return {from, to, query: q.toString()};
};

async function useLoad() {
  const state = USE; if (!state || !$('#use-body')) return;
  const mine = ++state.load, range = useParams(state, state.department ? {department: state.department} : {});
  $('#use-body').setAttribute('aria-busy', 'true');
  // Controls first, so a switch shows at once while the numbers load.
  for (const b of $('#main').querySelectorAll('[data-use-range]')) b.setAttribute('aria-selected', String(b.dataset.useRange === state.range));
  $('#main').querySelector('.use-custom').hidden = state.range !== 'custom';
  $('#use-from').value = range.from; $('#use-to').value = range.to;
  let data;
  try { data = await get('/v2/usage?' + range.query); }
  catch (e) { if (USE === state && mine === state.load) $('#use-body').innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  if (USE !== state || mine !== state.load) return;      // a newer switch is already loading
  state.data = data;
  const select = $('#use-dept');
  select.innerHTML = `<option value="">All groups</option>` + (data.departments || []).map(d =>
    `<option value="${esc(d)}"${d === state.department ? ' selected' : ''}>${esc(d)}</option>`).join('');
  select.hidden = !(data.departments || []).length;
  for (const [key] of USE_DIMENSIONS) {
    const filter = $(`[data-use-filter="${key}"]`), selected = state.filters[key] || '';
    const values = [...new Set([...(data.dimensions?.[key] || []), ...(selected ? [selected] : [])])].sort();
    filter.innerHTML = '<option value="">All</option>' + values.map(value => `<option value="${esc(value)}">${esc(data.dimension_labels?.[key]?.[value] || (value === '__unknown__' ? 'Not recorded / not applicable' : value))}</option>`).join('');
    filter.value = selected;
  }
  usePaint();
}

function usePaint() {
  const state = USE, data = state.data, body = $('#use-body');
  if (!data || data.group !== state.group) return;
  body.removeAttribute('aria-busy');
  const rank = state.sort === 'tokens' ? useTokens : state.sort === 'runs' ? r => r.runs : useSpend;
  const rows = [...(data.rows || [])].sort((a, b) => state.sort === 'name'
    ? (a.name || '').localeCompare(b.name || '') : rank(b) - rank(a) || (a.name || '').localeCompare(b.name || ''));
  if (!rows.length) { body.innerHTML = '<div class="empty">No usage yet.</div>'; return; }
  const t = data.totals;
  const top = Math.max(...rows.map(useSpend), 0);
  body.innerHTML = `<div class="use-total"><strong id="use-total" title="${esc(useBilled(t))}">${useMoney(useEquiv(t))}</strong> <span class="use-note">API-equivalent</span> · ${useRuns(t.runs)}
      ${t.low_estimate_runs > 0 ? `<div class="use-sub use-low">Estimate, may be low: ${esc(useLowWhy(t.low_estimate_runs))}.</div>` : ''}</div>
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

function useOpen(slug) {
  const state = USE;
  state.open = state.open === slug ? '' : slug;
  for (const row of $('#use-body').querySelectorAll('.use-row')) {
    const on = row.dataset.bot === state.open;
    row.querySelector('.use-line').setAttribute('aria-expanded', String(on));
    row.querySelector('.use-detail').hidden = !on;
  }
  if (state.open) void useDetail(slug);
}

async function useDetail(slug) {
  const state = USE, row = $('#use-body').querySelector(`.use-row[data-bot="${CSS.escape(slug)}"]`);
  if (!row) return;
  const host = row.querySelector('.use-detail');
  host.innerHTML = '<div class="muted">Loading…</div>';
  let data;
  try { data = await get('/v2/usage?' + useParams(state, {bot: slug}).query); }
  catch (e) { if (USE === state) host.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  if (USE !== state || state.open !== slug) return;
  state.detail = data;
  const days = data.daily || [], peak = Math.max(...days.map(useSpend), 0);
  const bars = days.map(d => {
    const v = useSpend(d), tip = `${d.day} · ${useMoney(useEquiv(d))} · ${useRuns(d.runs)}`;
    return `<span class="use-col" title="${esc(tip)}" data-day="${esc(d.day)}">${v ? `<i class="use-api" style="height:${(100 * v / peak).toFixed(1)}%"></i>` : ''}</span>`;
  }).join('');
  const routines = (data.routines || []).filter(r => r.runs);
  host.innerHTML = `<div class="use-chart" role="img" aria-label="Daily API-equivalent cost, ${esc(days[0]?.day || '')} to ${esc(days[days.length - 1]?.day || '')}">${bars}</div>
    <div class="use-axis"><span>${esc(days[0]?.day || '')}</span><span>${peak ? useMoney(peak) + ' a day at most' : ''}</span><span>${esc(days[days.length - 1]?.day || '')}</span></div>
    <table class="use-routines"><thead><tr><th>Automation</th><th>Runs</th><th>Cost</th></tr></thead><tbody>${routines.map(r =>
      `<tr><td>${esc(r.title)}</td><td class="tnum">${r.runs}</td><td class="tnum"><span class="use-cost">${useCost(r)}</span></td></tr>`).join('')
      || '<tr><td colspan="3" class="muted">None.</td></tr>'}</tbody></table>
    <button type="button" class="ghost" data-use-csv>Export CSV</button>`;
}

// The open bot's days as a CSV, in the units the API returns.
function useCsv() {
  const data = USE?.detail; if (!data) return;
  const head = ['day', 'runs', 'input_tokens', 'cached_tokens', 'output_tokens', 'est_cost_usd', 'subscription_equiv_usd'];
  const cell = v => v == null ? '' : String(v);
  const text = [head.join(','), ...(data.daily || []).map(d => head.map(k => cell(d[k])).join(','))].join('\n') + '\n';
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
const USE_PERIODS = [['per_30m_usd', '30 min', '/30m', '30m'], ['hourly_usd', 'Hour', '/hr', 'hour'], ['daily_usd', 'Day', '/day', 'day'],
                     ['weekly_usd', 'Week', '/wk', 'week'], ['monthly_usd', 'Month', '/mo', 'month']];
const USE_WORDS = {per_30m: '30-minute', hourly: 'hourly', daily: 'daily', weekly: 'weekly', monthly: 'monthly'};
const useCap = n => '$' + (n >= 100 || Number.isInteger(n) ? n.toLocaleString(undefined, {maximumFractionDigits: 0}) : n.toFixed(2));
const usePlain = n => String(Math.round(n * 100) / 100);
function useLimitText(limit) {
  return USE_PERIODS.filter(([key]) => limit?.[key]).map(([key, , unit]) => useCap(limit[key]) + unit).join(' · ');
}
// What holds a bot, in words: its own limit, or the team's or a group's.
const useHeldBy = limit => limit?.blocked_by && limit.blocked_by.scope !== 'bot'
  ? `${limit.blocked_by.name} is over its ${USE_WORDS[limit.blocked_by.period] || limit.blocked_by.period} limit`
  : `Over its ${USE_WORDS[limit?.blocked] || limit?.blocked} limit`;
// The cell on a Usage row and in Settings > Bots: the limit, a chip when it is near or met, and a click to change it.
function useLimitButton(slug, name, limit) {
  if (!limit?.may_edit) return '<span class="use-limit"></span>';
  const text = useLimitText(limit);
  const chip = limit.blocked ? '<span class="pill fail">Paused</span>' : limit.percent >= 80 ? `<span class="pill waiting">${limit.percent}%</span>` : '';
  return `<button type="button" class="use-limit${text ? '' : ' none'}" data-use-limit="${esc(slug)}" aria-label="Limit for ${esc(name || slug)}"
    title="${limit.blocked ? esc(`${useHeldBy(limit)}: no new work until it resets or the limit is raised`) : 'Change limit'}">${chip}<span>${text ? esc(text) : 'Set limit'}</span></button>`;
}

// Groups ordered as the org nests them: by the names from the top group down.
function useGroupOrder(groups) {
  const chain = id => { const out = []; let at = id; while (at && groups[at] && out.length < 8) { out.unshift(groups[at].name || at); at = groups[at].parent; } return out; };
  return Object.entries(groups).map(([id, g]) => [id, g, chain(id)]).sort((a, b) => a[2].join('\u0000').localeCompare(b[2].join('\u0000')));
}
const useBotName = slug => typeof botDisplayName === 'function' ? botDisplayName(slug) : slug;
const useHasCap = (row, prefix = '') => USE_PERIODS.some(([key]) => row?.[prefix + key] != null);
// The Limits tab's rows, {key, kind, name, path, value(field): what is stored, hint(field), state}: the default for each bot
// and the whole team (owner and administrators), then each group and bot with a limit of its own and those added since
// the last save. A bot's empty window follows the default, so the default is its hint.
function useLimitRows(limits, added) {
  if (!limits) return [];
  const def = limits.default || {}, pools = limits.pools || {}, out = [];
  if (limits.may_edit_default) out.push({key: 'default', kind: 'default', name: 'Each bot', path: '/v2/usage/limits', value: k => def[k] ?? null, hint: () => ''});
  if (pools.company) out.push({key: 'company', kind: 'company', name: 'Whole team', path: '/v2/usage/pools/company', state: pools.company,
                               value: k => pools.company[k] ?? null, hint: () => ''});
  for (const [id, g, chain] of useGroupOrder(pools.groups || {}))
    if ((limits.may_edit_default || g.may_edit) && (useHasCap(g) || added.includes('group:' + id)))
      out.push({key: 'group:' + id, kind: 'group', name: g.name || id, title: chain.join(' › '), path: '/v2/usage/pools/groups/' + encodeURIComponent(id),
                state: g, value: k => g[k] ?? null, hint: () => '', removable: true});
  const bots = Object.entries(limits.bots || {}).filter(([slug, b]) => b.may_edit && (useHasCap(b, 'own_') || added.includes('bot:' + slug)))
    .map(([slug, b]) => ({key: 'bot:' + slug, kind: 'bot', slug, name: useBotName(slug), path: '/v2/usage/limits/' + encodeURIComponent(slug), state: b,
                          value: k => b['own_' + k] ?? null, hint: k => def[k] != null ? useCap(def[k]) : '', removable: true}));
  return out.concat(bots.sort((a, b) => a.name.localeCompare(b.name)));
}
// What can be added: the groups and bots this person may limit that have no row.
function useLimitAddable(limits, added) {
  if (!limits) return [];
  const shown = new Set(useLimitRows(limits, added).map(r => r.key));
  const groups = useGroupOrder(limits.pools?.groups || {}).filter(([id, g]) => (limits.may_edit_default || g.may_edit) && !shown.has('group:' + id))
    .map(([id, g, chain]) => ['group:' + id, chain.join(' › ') || g.name || id]);
  const bots = Object.entries(limits.bots || {}).filter(([slug, b]) => b.may_edit && !shown.has('bot:' + slug))
    .map(([slug]) => ['bot:' + slug, useBotName(slug)]).sort((a, b) => a[1].localeCompare(b[1]));
  return [...groups, ...bots];
}

function useLimitsPaint() {
  const state = USE, host = $('#use-limits'); if (!state || !host) return;
  const limits = state.limits, edit = state.edit;
  if (!limits) { host.innerHTML = limits === undefined ? '<div class="muted">Loading…</div>' : '<div class="err">Limits did not load.</div>'; return; }
  // What was typed survives a repaint (a row added or removed, a reload after a save that failed part way).
  const typed = Object.fromEntries([...host.querySelectorAll('input[name]')].map(i => [i.name, i.type === 'checkbox' ? i.checked : i.value]));
  const rows = useLimitRows(limits, edit.added);
  const periods = USE_PERIODS.filter(([key]) => key !== 'hourly_usd' || rows.some(r => r.value(key) != null));
  const add = useLimitAddable(limits, edit.added);
  const opts = (label, list) => list.length ? `<optgroup label="${label}">${list.map(([k, n]) => `<option value="${esc(k)}">${esc(n)}</option>`).join('')}</optgroup>` : '';
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
    if (input.type === 'checkbox') input.checked = typed[input.name]; else input.value = typed[input.name];
  }
  host.oninput = host.onchange = ev => {
    const pick = ev.target.closest('.use-lim-add');
    if (!pick) return void useLimitsDirty();
    if (ev.type !== 'change' || !pick.value) return;
    const key = pick.value;
    edit.added.push(key);
    useLimitsPaint();
    host.querySelector(`[data-lim-row="${CSS.escape(key)}"] input`)?.focus();
  };
  host.onkeydown = ev => { if (ev.key === 'Enter' && ev.target.matches('input:not([type=checkbox])')) { ev.preventDefault(); void useLimitsSave(); } };
  host.onclick = ev => {
    const x = ev.target.closest('[data-lim-remove]');
    if (x) {
      const key = x.closest('[data-lim-row]').dataset.limRow, row = useLimitRows(limits, edit.added).find(r => r.key === key);
      // A row added and never saved just goes; one with saved limits is struck out until Save clears them.
      if (row && !USE_PERIODS.some(([k]) => row.value(k) != null)) edit.added = edit.added.filter(k => k !== key);
      else if (edit.removed.has(key)) edit.removed.delete(key); else edit.removed.add(key);
      useLimitsPaint();
      return;
    }
    if (ev.target.closest('[data-lim-discard]')) {
      state.edit = {added: [], removed: new Set()};
      host.innerHTML = '';
      useLimitsPaint();
      return;
    }
    if (ev.target.closest('[data-lim-save]')) void useLimitsSave();
  };
  useLimitsDirty();
}
function useLimitRowHtml(r, periods, removed) {
  const icon = r.kind === 'bot' ? botAvatar(r.slug, 20)
    : `<span class="nav-icon use-lim-icon" aria-hidden="true">${r.kind === 'group' ? 'groups' : r.kind === 'company' ? 'account_balance' : 'smart_toy'}</span>`;
  const st = r.state;
  const chip = !st ? '' : st.blocked ? `<span class="pill fail" title="${esc(useHeldBy(st))}">Paused</span>`
    : st.percent >= 80 ? `<span class="pill waiting">${st.percent}%</span>` : '';
  const cells = periods.map(([k, label, , w]) => {
    const v = r.value(k), head = `<span class="use-lim-h" aria-hidden="true">${label}</span>`;
    if (k === 'hourly_usd' && v == null) return `<span class="use-lim-cell" role="cell"></span>`;
    // The bar is what this window has spent against the cap that applies: a bot's own, else the default.
    const cap = r.kind === 'bot' ? st?.[k] : v, spent = st?.spent?.[w];
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
function useLimitChanges() {
  const state = USE, host = $('#use-limits'), limits = state.limits, writes = [];
  let problem = '';
  for (const r of useLimitRows(limits, state.edit.added)) {
    const body = {};
    if (state.edit.removed.has(r.key)) {
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
    if (box && box.checked !== !!limits.default?.count_subscription) body.count_subscription = box.checked;
    if (Object.keys(body).length) writes.push([r.path, body]);
  }
  if (problem) throw new Error(problem);
  return writes;
}
function useLimitsDirty() {
  const state = USE, host = $('#use-limits');
  let writes = [], bad = false;
  try { writes = useLimitChanges(); } catch { bad = true; }
  const dirty = writes.length > 0 || bad;
  host.querySelector('[data-lim-save]').disabled = !dirty;
  host.querySelector('[data-lim-discard]').disabled = !dirty && !state.edit.added.length;
  host.querySelector('.use-lim-foot').classList.toggle('dirty', dirty);
}
// One PUT per changed row, in order. Whatever saved is read back; what did not stays typed with the error beside Save.
async function useLimitsSave() {
  const state = USE, host = $('#use-limits'), edit = state.edit;
  let writes;
  try { writes = useLimitChanges(); }
  catch (e) { edit.error = e.message; const err = host.querySelector('[data-lim-err]'); err.textContent = e.message; err.hidden = false; return; }
  if (!writes.length) return;
  host.querySelector('[data-lim-save]').disabled = true;
  edit.error = '';
  let failed = null;
  for (const [path, body] of writes) {
    try { await put(path, body); } catch (e) { failed = e; break; }
  }
  if (USE !== state) return;
  if (failed) edit.error = failed.message;
  else { state.edit = {added: [], removed: new Set()}; host.innerHTML = ''; toast('Saved'); }   // read back as saved
  await useLimits();
  void useLoad();
}
// Settings > Bots: one bot's limits in a dialog. The Usage page edits every limit in its Limits tab.
const useAmount = value => { const t = String(value).trim(); return t === '' ? null : Number(t); };
const useAmounts = form => Object.fromEntries(USE_PERIODS.filter(([key]) => form[key]).map(([key]) => [key, useAmount(form[key].value)]));
function useDialog(label, body, save, done) {
  const dialog = document.createElement('dialog');
  dialog.className = 'tmodal use-dialog';
  dialog.setAttribute('aria-label', label);
  dialog.innerHTML = `<div class="tmodal-head"><span class="who">${esc(label)}</span><span class="spacer"></span><button type="button" class="ghost tmodal-x" data-close aria-label="Close">✕</button></div>
    <form class="tmodal-body" novalidate>${body}<p class="err" data-error hidden></p>
      <div class="use-dialog-row"><button type="button" class="ghost" data-close>Cancel</button><button type="submit" class="primary">Save</button></div></form>`;
  document.body.appendChild(dialog);
  dialog.querySelectorAll('[data-close]').forEach(b => b.onclick = () => dialog.close());
  dialog.onclose = () => dialog.remove();
  const form = dialog.querySelector('form'), err = dialog.querySelector('[data-error]');
  form.onsubmit = async ev => {
    ev.preventDefault(); err.hidden = true;
    const submit = form.querySelector('[type=submit]'); submit.disabled = true;
    try { await save(form); dialog.close(); done && done(); }
    catch (e) { err.textContent = e.message; err.hidden = false; submit.disabled = false; }
  };
  dialog.showModal();
  form.querySelector('input')?.focus();
  return dialog;
}
function useLimitDialog(slug, name, limit, company, done) {
  const hint = key => company?.[key] ? useCap(company[key]) : '—';
  const held = limit?.blocked_by && limit.blocked_by.scope !== 'bot' ? `<p class="muted">${esc(useHeldBy(limit))}; raising this bot's own limit does not lift it.</p>` : '';
  const periods = USE_PERIODS.filter(([key]) => key !== 'hourly_usd' || limit?.own_hourly_usd != null);
  const fields = periods.map(([key, label]) => `<label>${label}<span class="use-money"><span aria-hidden="true">$</span><input name="${key}" inputmode="decimal" autocomplete="off"
      value="${limit?.['own_' + key] == null ? '' : usePlain(limit['own_' + key])}" placeholder="${esc(hint(key))}"></span></label>`).join('');
  useDialog(name || slug, held + `<div class="use-fields">${fields}</div>`,
    form => put('/v2/usage/limits/' + encodeURIComponent(slug), useAmounts(form)), done);
}
