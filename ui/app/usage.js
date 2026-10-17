/* ui/app/usage.js — Usage page: estimated model spend per bot
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- usage
// GET /api/v2/usage (backend/usage.py, docs/usage.md): tokens each run used, priced at list price. Days are UTC.
// A run on a ChatGPT or Claude sign-in is not spend: its figure is "API-equivalent" and stays apart.
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
  return (n / 1e6).toFixed(n < 1e7 ? 2 : 1).replace(/\.?0+$/, '') + 'M';
}
const useTokens = r => (r.input_tokens || 0) + (r.cached_tokens || 0) + (r.output_tokens || 0);
const useSpend = r => (r.est_cost_usd || 0) + (r.subscription_equiv_usd || 0);
const useRuns = n => `${n} run${n === 1 ? '' : 's'}`;
// Runs on a model whose long prompts cost more, without each request's tokens, were priced at the lower rate.
const useLowWhy = n => `${useRuns(n)} on a model that charges more for long prompts did not report each request's tokens, so they are priced at the lower rate`;
const useLow = r => r.low_estimate_runs > 0 ? `<span class="use-sub use-low" title="${esc(useLowWhy(r.low_estimate_runs))}">estimate, may be low</span>` : '';
// The estimate, or a dash for tokens on a model with no list price; a subscription's figure beside it, never in it.
function useCost(r) {
  const sub = r.subscription_equiv_usd > 0 ? `<span class="use-sub">≈ ${useMoney(r.subscription_equiv_usd)} API-equivalent</span>` : '';
  const est = r.est_cost_usd == null ? '—' : r.est_cost_usd > 0 || !sub ? useMoney(r.est_cost_usd) : '';
  return (est ? `<span class="use-est">${est}</span>` : '') + sub + useLow(r);
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
                       department: saved.department || '', group: 'bot', sort: 'cost', filters: {}, custom: {from: span.from, to: span.to}, open: '', data: null, load: 0};
  $('#main').innerHTML = `<div class="use-page">
    <div class="use-head"><h1>Usage</h1></div>
    <div class="use-bar-row">
      <div class="use-seg" role="tablist" aria-label="Range">${USE_RANGES.map(([k, label]) =>
        `<button type="button" role="tab" data-use-range="${k}" aria-selected="${k === state.range}">${label}</button>`).join('')}</div>
      <select id="use-dept" aria-label="Group" hidden></select>
      <span class="use-custom" hidden><input type="date" id="use-from" aria-label="From"><input type="date" id="use-to" aria-label="To"></span>
      <button type="button" class="ghost use-default" id="use-default" hidden>Default limit</button>
    </div>
    <div class="use-bar-row">
      <label>Group by <select id="use-group">${[['bot', 'Bot'], ...USE_DIMENSIONS].map(([key, label]) => `<option value="${key}">${label}</option>`).join('')}</select></label>
      <label>Sort by <select id="use-sort"><option value="cost">Estimated cost</option><option value="tokens">Tokens</option><option value="runs">Runs</option><option value="name">Name</option></select></label>
      ${USE_DIMENSIONS.map(([key, label]) => `<label>${label} <select data-use-filter="${key}" aria-label="Filter ${label}"><option value="">All</option></select></label>`).join('')}
    </div>
    <p class="muted">Historical details not reported by a runner remain unknown. A run using a fallback can appear in more than one group.</p>
    <div id="use-body" aria-live="polite"></div>
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
      const row = (state.data?.rows || []).find(r => r.bot === cap.dataset.useLimit);
      return void useLimitDialog(cap.dataset.useLimit, row?.name, row?.limit, state.limits?.default, () => void useLoad());
    }
    const line = ev.target.closest('.use-line');
    if (line && state.group === 'bot') return void useOpen(line.closest('.use-row').dataset.bot);
    if (ev.target.closest('[data-use-csv]')) useCsv();
  };
  $('#use-default').onclick = () => useDefaultDialog(state.limits, () => { void useLimits(); void useLoad(); });
  void useLimits();
  useLoad();
}

// The team default limit, and whether this human may change it.
async function useLimits() {
  const state = USE;
  try { state.limits = await get('/v2/usage/limits'); } catch { state.limits = null; }
  const button = $('#use-default');
  if (USE === state && button) button.hidden = !state.limits?.may_edit_default;
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
  body.innerHTML = `<div class="use-total"><span class="use-note">Estimated</span>
      <strong id="use-total">${t.est_cost_usd == null ? '—' : useMoney(t.est_cost_usd)}</strong> · ${useRuns(t.runs)}
      ${t.subscription_equiv_usd > 0 ? `<div class="use-sub">≈ ${useMoney(t.subscription_equiv_usd)} API-equivalent on subscriptions</div>` : ''}
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
  const both = days.some(d => d.subscription_equiv_usd > 0) && days.some(d => d.est_cost_usd > 0);
  const bars = days.map(d => {
    const api = d.est_cost_usd || 0, sub = d.subscription_equiv_usd || 0;
    const h = v => peak ? (100 * v / peak).toFixed(1) : 0;
    const tip = `${d.day} · ${useMoney(api + sub || (d.est_cost_usd == null ? null : 0))} · ${useRuns(d.runs)}`;
    return `<span class="use-col" title="${esc(tip)}" data-day="${esc(d.day)}">${api ? `<i class="use-api" style="height:${h(api)}%"></i>` : ''}${sub ? `<i class="use-subs" style="height:${h(sub)}%"></i>` : ''}</span>`;
  }).join('');
  const routines = (data.routines || []).filter(r => r.runs);
  host.innerHTML = `<div class="use-chart" role="img" aria-label="Daily estimated spend, ${esc(days[0]?.day || '')} to ${esc(days[days.length - 1]?.day || '')}">${bars}</div>
    <div class="use-axis"><span>${esc(days[0]?.day || '')}</span><span>${peak ? useMoney(peak) + ' a day at most' : ''}</span><span>${esc(days[days.length - 1]?.day || '')}</span></div>
    ${both ? `<div class="use-key"><span><i class="use-api"></i>Spend</span><span><i class="use-subs"></i>API-equivalent</span></div>` : ''}
    <table class="use-routines"><thead><tr><th>Routine</th><th>Runs</th><th>Estimate</th></tr></thead><tbody>${routines.map(r =>
      `<tr><td>${esc(r.title)}</td><td class="tnum">${r.runs}</td><td class="tnum"><span class="use-cost">${useCost(r)}</span></td></tr>`).join('')
      || '<tr><td colspan="3" class="muted">No routines.</td></tr>'}</tbody></table>
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
// A bot's daily and monthly cap in estimated USD (backend/usage_limits.py). Over one, it takes no new job; at 80% its owner is told.
const useCap = n => '$' + (n >= 100 || Number.isInteger(n) ? n.toLocaleString(undefined, {maximumFractionDigits: 0}) : n.toFixed(2));
function useLimitText(limit) {
  const parts = [];
  if (limit?.daily_usd) parts.push(`${useCap(limit.daily_usd)}/day`);
  if (limit?.monthly_usd) parts.push(`${useCap(limit.monthly_usd)}/mo`);
  return parts.join(' · ');
}
// The cell on a Usage row and in Settings > Bots: the limit, a chip when it is near or met, and a click to change it.
function useLimitButton(slug, name, limit) {
  if (!limit?.may_edit) return '<span class="use-limit"></span>';
  const text = useLimitText(limit);
  const chip = limit.blocked ? '<span class="pill fail">Paused</span>' : limit.percent >= 80 ? `<span class="pill waiting">${limit.percent}%</span>` : '';
  return `<button type="button" class="use-limit${text ? '' : ' none'}" data-use-limit="${esc(slug)}" aria-label="Limit for ${esc(name || slug)}"
    title="${limit.blocked ? esc(`Over its ${limit.blocked} limit: no new work until it resets or the limit is raised`) : 'Change limit'}">${chip}<span>${text ? esc(text) : 'Set limit'}</span></button>`;
}
const useAmount = value => { const t = String(value).trim(); return t === '' ? null : Number(t); };
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
const useField = (name, label, value, hint) => `<label>${label}<input type="number" name="${name}" min="0" step="any" inputmode="decimal" value="${value ?? ''}" placeholder="${esc(hint || 'No limit')}"></label>`;
function useLimitDialog(slug, name, limit, company, done) {
  const hint = key => company?.[key] ? 'Default ' + useCap(company[key]) : 'No limit';
  useDialog(`Limit · ${name || slug}`,
    useField('daily_usd', 'Daily, USD', limit?.own_daily_usd, hint('daily_usd')) + useField('monthly_usd', 'Monthly, USD', limit?.own_monthly_usd, hint('monthly_usd')),
    form => put('/v2/usage/limits/' + encodeURIComponent(slug), {daily_usd: useAmount(form.daily_usd.value), monthly_usd: useAmount(form.monthly_usd.value)}), done);
}
function useDefaultDialog(limits, done) {
  const now = limits?.default || {};
  useDialog('Default limit',
    useField('daily_usd', 'Daily, USD', now.daily_usd) + useField('monthly_usd', 'Monthly, USD', now.monthly_usd) +
    `<label class="use-check"><input type="checkbox" name="count_subscription"${now.count_subscription ? ' checked' : ''}> Count subscription runs</label>`,
    form => put('/v2/usage/limits', {daily_usd: useAmount(form.daily_usd.value), monthly_usd: useAmount(form.monthly_usd.value),
                                    count_subscription: form.count_subscription.checked}), done);
}
