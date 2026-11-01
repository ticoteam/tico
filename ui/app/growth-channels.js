/* ui/app/growth-channels.js — Growth › Channels (#/strategy/growth/channels, growth spec §5): one month's unit economics
   per channel from GET /api/v2/growth/economics (spend, bot time, new customers, CAC, kept at 30/90/180 days, LTV,
   LTV:CAC, payback, how good the data is), with Unknown as its own row and the total. Choosing a row opens the channel
   below: its guardrail, retention by month joined (GET /api/v2/growth/cohorts?channel=), where the numbers come from
   and its ideas. The owner corrects a month's row (PUT /api/v2/growth/cohorts) and the settings the maths uses.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const GC_KEPT = [['kept_d30', '30'], ['kept_d90', '90'], ['kept_d180', '180']];
const gcKeptBars = r => `<span class="gc-kept" role="img" aria-label="${esc(GC_KEPT.map(([k, d]) => `${d} days ${grPct(r[k])}`).join(', '))}">${GC_KEPT.map(([k]) =>
  `<span class="gc-kb" title="${esc(grPct(r[k]))}"><span style="height:${r[k] == null ? 0 : Math.max(4, Math.min(100, Number(r[k])))}%"></span></span>`).join('')}<span class="gc-kv">${esc(GC_KEPT.map(([k]) => r[k] == null ? '—' : Math.round(r[k])).join(' · '))}</span></span>`;
const gcQuality = q => q ? `<span class="pill ${q === 'actual' ? 'ok' : 'waiting'}">${q === 'actual' ? 'Actual' : 'Estimate'}</span>` : '';
const gcMonths = n => n == null ? '—' : `${grX(n)} mo`;
const gcRatio = v => v == null ? '—' : `${grX(v)}×`;
const gcKey = slug => slug || 'unknown';

function pageGrowthChannels(state) {
  state.month = new URLSearchParams((S.route || '').split('?')[1] || '').get('m') || '';
  state.pick = null;
  void gcRead(state);
}
async function gcRead(state) {
  let econ, ideas, chans, sources;
  try {
    [econ, ideas, chans, sources] = await Promise.all([get('/v2/growth/economics' + (state.month ? `?month=${encodeURIComponent(state.month)}` : ''), {fresh: true}),
      get('/v2/growth/ideas').then(r => r.ideas || []).catch(() => []), get('/v2/growth/channels').then(r => r.channels || []).catch(() => []),
      get('/v2/growth/sources').then(r => r.sources || []).catch(() => [])]);
  } catch (e) { if (GR === state) $('#gr-body').innerHTML = grCrumbs(GR_ROUTE, 'Growth', 'Channels') + grFail(e); return; }
  if (GR !== state) return;
  state.data = {econ, ideas, chans, sources};
  state.month = econ.month || state.month;
  gcDraw(state);
}
function gcDraw(state) {
  const body = $('#gr-body'), {econ, chans} = state.data || {};
  if (!body || !econ) return;
  const st = econ.settings || {}, rows = econ.rows || [], may = grAdmin();
  const months = (econ.months || []).slice().sort().reverse();
  if (state.month && !months.includes(state.month)) months.unshift(state.month);
  const pick = months.length ? `<label class="gr-inline"><span class="sr-only">Month</span><select data-gc-month>${months.map(m => `<option value="${esc(m)}"${m === state.month ? ' selected' : ''}>${esc(grMonth(m))}</option>`).join('')}</select></label>` : '';
  const facts = [st.unit ? `Counted in ${st.unit}` : '', st.revenue_month != null ? `${grUsd(st.revenue_month)}/mo` : '', st.margin_pct != null ? `${grPct(st.margin_pct)} margin` : ''].filter(Boolean).join(' · ');
  const tools = `${facts ? `<span class="muted gr-meta">${esc(facts)}</span>` : ''}${may ? '<button class="ghost" type="button" data-gc-settings>Settings</button>' : ''}${pick}`;
  const breached = new Set((econ.guardrails || []).filter(g => g.breached).map(g => gcKey(g.channel)));
  const cell = r => `<td class="num">${esc(grUsd(r.spend))}</td><td class="num">${esc(grUsd(r.bot_cost))}</td><td class="num">${esc(grX(r.new_customers, 0))}</td>
    <td class="num">${esc(grUsd(r.cac))}</td><td>${gcKeptBars(r)}</td><td class="num">${esc(grUsd(r.ltv))}</td><td class="num">${esc(gcRatio(r.ltv_cac))}</td><td class="num">${esc(gcMonths(r.payback_months))}</td>`;
  const tr = r => {
    const k = gcKey(r.channel), on = state.pick === k;
    return `<tr class="${on ? 'gr-on' : ''}${breached.has(k) ? ' gr-breach' : ''}"><th scope="row"><button type="button" class="gr-rowbtn" data-gc-row="${esc(k)}" aria-expanded="${on}">
      ${r.track ? `<span class="gr-track gr-track-${esc(r.track)}" aria-hidden="true"></span>` : ''}${esc(r.name || (r.channel ? grChanName(chans, r.channel) : 'Unknown'))}</button></th>${cell(r)}<td>${gcQuality(r.quality)}</td></tr>`;
  };
  const t = econ.total || {};
  const table = rows.length ? `<div class="card gr-scroll"><table class="gr-t gc-t"><thead><tr><th scope="col">Channel</th><th scope="col" class="num">Spend</th><th scope="col" class="num">Bot time</th>
    <th scope="col" class="num">New</th><th scope="col" class="num">CAC</th><th scope="col">Kept 30 · 90 · 180</th><th scope="col" class="num">LTV</th><th scope="col" class="num">LTV:CAC</th>
    <th scope="col" class="num">Payback</th><th scope="col">Data</th></tr></thead><tbody>${rows.map(tr).join('')}</tbody>
    <tfoot><tr><th scope="row">All channels</th>${cell(t)}<td>${gcQuality(t.quality)}</td></tr></tfoot></table></div>` : '';
  body.innerHTML = grCrumbs(GR_ROUTE, 'Growth', 'Channels', tools) + table + `<div id="gc-detail"></div>`;
  const sel = body.querySelector('[data-gc-month]');
  if (sel) sel.onchange = () => { state.month = sel.value; history.replaceState(history.state, '', `${GR_CHANNELS}?m=${state.month}`); void gcRead(state); };
  const set = body.querySelector('[data-gc-settings]');
  if (set) set.onclick = () => gcSettings(state);
  body.querySelectorAll('[data-gc-row]').forEach(b => { b.onclick = () => { state.pick = state.pick === b.dataset.gcRow ? null : b.dataset.gcRow; gcDraw(state); }; });
  if (state.pick) void gcDetail(state);
}

async function gcDetail(state) {
  const k = state.pick, {econ, ideas, chans, sources} = state.data;
  const r = (econ.rows || []).find(x => gcKey(x.channel) === k) || {channel: k === 'unknown' ? null : k};
  const ch = chans.find(c => c.slug === k), may = grAdmin() || grSame(ch?.owner_ref, grMe());
  const box = $('#gc-detail');
  box.innerHTML = `<section class="card gc-detail">${skelRows(3, {dot: false})}</section>`;
  let cohorts = [];
  try { cohorts = (await get(`/v2/growth/cohorts?channel=${encodeURIComponent(k)}`, {fresh: true})).cohorts || []; }
  catch (e) { if (GR === state && state.pick === k) box.innerHTML = `<section class="card gc-detail">${grFail(e)}</section>`; return; }
  if (GR !== state || state.pick !== k || !box.isConnected) return;
  const guards = (econ.guardrails || []).filter(g => gcKey(g.channel) === k);
  const banner = guards.filter(g => g.breached).map(g => `<div class="gr-banner gr-amber">${esc(g.kpi_name || 'Guardrail')} ${esc(grX(g.value))} · max ${esc(grX(g.max))}</div>`).join('');
  const heat = cohorts.length ? `<section class="gr-sec"><h3 class="gr-k">Kept, by month joined</h3><div class="gr-scroll"><table class="gr-t gc-heat"><thead><tr><th scope="col">Joined</th>
      <th scope="col" class="num">New</th>${GC_KEPT.map(([, d]) => `<th scope="col" class="num">${d} days</th>`).join('')}<th scope="col">Data</th>${may ? '<th scope="col"><span class="sr-only">Edit</span></th>' : ''}</tr></thead>
      <tbody>${cohorts.slice().sort((a, b) => String(b.month).localeCompare(String(a.month))).map(c => `<tr><th scope="row">${esc(grMonth(c.month))}</th><td class="num">${esc(grX(c.new_customers, 0))}</td>
        ${GC_KEPT.map(([f]) => `<td class="num gc-h"${c[f] == null ? '' : ` style="--k:${Math.max(0, Math.min(100, Number(c[f])))}"`}>${esc(grPct(c[f]))}</td>`).join('')}<td>${gcQuality(c.quality)}</td>
        ${may ? `<td><button class="ghost small" type="button" data-gc-edit="${esc(c.month)}">Edit</button></td>` : ''}</tr>`).join('')}</tbody></table></div></section>` : '';
  const from = {...(r.sources || {})};
  const src = sources.find(s => s.id === ch?.source_id);
  const where = Object.keys(from).length || src ? `<section class="gr-sec"><h3 class="gr-k">Where the numbers come from</h3>
      ${src ? `<div class="gr-row"><span class="gr-row-t">${esc(src.truth_for)}</span><a href="${GTM_ROUTE}/sources">${esc(src.system)}</a></div>` : ''}
      ${Object.entries(from).map(([f, v]) => `<div class="gr-row"><span class="gr-row-t">${esc(f.replace(/_/g, ' '))}</span><span class="muted">${esc(v)}</span></div>`).join('')}</section>` : '';
  const mine = ideas.filter(i => k === 'unknown' ? !(i.channels || []).length : (i.channels || []).includes(k));
  const list = mine.length ? `<section class="gr-sec"><h3 class="gr-k">Ideas</h3><div class="gr-flow">${mine.map(i => grIdeaCard(i, chans, {chan: false, result: true})).join('')}</div></section>` : '';
  box.innerHTML = `<section class="card gc-detail" aria-label="${esc(r.name || ch?.name || 'Unknown')}"><div class="gc-dh"><h3>${esc(r.name || ch?.name || 'Unknown')}</h3>
      ${ch ? `<span class="muted">${esc([ch.track === 'brand' ? 'Brand' : 'Performance', grWho(ch.owner_ref)].filter(Boolean).join(' · '))}</span>${grStage(ch.stage)}` : ''}
      <span class="spacer"></span>${may ? '<button class="ghost small" type="button" data-gc-add>+ Month</button>' : ''}</div>${banner}${heat}<div class="gc-dcols">${where}${list}</div></section>`;
  box.querySelectorAll('[data-gc-edit]').forEach(b => { b.onclick = () => gcCohortDialog(state, k, cohorts.find(c => c.month === b.dataset.gcEdit)); });
  const add = box.querySelector('[data-gc-add]');
  if (add) add.onclick = () => gcCohortDialog(state, k, {month: state.month || new Date().toISOString().slice(0, 7)}, true);
}

function gcCohortDialog(state, k, c, fresh = false) {
  const n = (name, label, v, extra = '') => grField(label, `<input name="${name}" inputmode="decimal" value="${esc(v ?? '')}"${extra}>`);
  const srcs = c.sources || {};
  grDialog(`${grChanName(state.data.chans, k === 'unknown' ? 'Unknown' : k)} · ${fresh ? 'New month' : grMonth(c.month)}`,
    `${fresh ? grField('Month', `<input name="month" type="month" required value="${esc(c.month)}">`) : ''}
    <div class="gr-pair">${n('spend', 'Spend', c.spend)}${n('bot_cost', 'Bot time', c.bot_cost)}</div>
    <div class="gr-pair">${n('new_customers', 'New', c.new_customers)}${grField('Data', `<select name="quality">${grOpts([['actual', 'Actual'], ['estimate', 'Estimate']], c.quality || 'estimate', null)}</select>`)}</div>
    <div class="gr-trio">${GC_KEPT.map(([f, d]) => n(f, `Kept ${d} days %`, c[f])).join('')}</div>
    <div class="gr-pair">${n('revenue_month', 'Revenue / mo', c.revenue_month)}${n('margin_pct', 'Margin %', c.margin_pct)}</div>
    ${grField('Spend from', `<input name="src_spend" maxlength="300" value="${esc(srcs.spend || '')}">`)}
    ${grField('New from', `<input name="src_new" maxlength="300" value="${esc(srcs.new_customers || '')}">`)}`, 'Save', async form => {
      const v = name => form.elements[name].value;
      const sources = {...srcs};
      const setSrc = (f, val) => { if (val.trim()) sources[f] = val.trim(); else delete sources[f]; };
      setSrc('spend', v('src_spend')); setSrc('new_customers', v('src_new'));
      const month = fresh ? v('month') : c.month;
      if (!/^\d{4}-\d{2}$/.test(month)) throw new Error('A month');
      await put('/v2/growth/cohorts', {channel: k, month, spend: grNum(v('spend')), bot_cost: grNum(v('bot_cost')), new_customers: grNum(v('new_customers')),
        kept_d30: grNum(v('kept_d30')), kept_d90: grNum(v('kept_d90')), kept_d180: grNum(v('kept_d180')), revenue_month: grNum(v('revenue_month')),
        margin_pct: grNum(v('margin_pct')), quality: v('quality'), sources});
      toast('Saved'); void gcRead(state);
    });
}

function gcSettings(state) {
  const st = state.data.econ.settings || {};
  grDialog('Settings', `${grField('Counted in', `<input name="unit" maxlength="40" value="${esc(st.unit || '')}">`)}
    <div class="gr-pair">${grField('Revenue / mo', `<input name="revenue_month" inputmode="decimal" value="${esc(st.revenue_month ?? '')}">`)}
    ${grField('Margin %', `<input name="margin_pct" inputmode="decimal" value="${esc(st.margin_pct ?? '')}">`)}</div>
    ${grField('A win beats the baseline by %', `<input name="verdict_win_pct" inputmode="decimal" value="${esc(st.verdict_win_pct ?? '')}">`)}`, 'Save', async form => {
    const v = n => form.elements[n].value;
    const body = {revenue_month: grNum(v('revenue_month')), margin_pct: grNum(v('margin_pct')), verdict_win_pct: grNum(v('verdict_win_pct'))};
    if (v('unit').trim()) body.unit = v('unit').trim();
    await put('/v2/growth/settings', body);
    toast('Saved'); void gcRead(state);
  });
}
