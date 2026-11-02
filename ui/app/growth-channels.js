/* ui/app/growth-channels.js — Growth › Channels (#/growth/channels, growth spec §5): the channels being tried, one row
   each with its stage, owner and one month's New · CAC · LTV:CAC from GET /api/v2/growth/economics, then Unknown and
   All channels. A month switcher under the list picks the month (?m=). Choosing a row opens it in place: its guardrail, retention by
   month joined (GET /api/v2/growth/cohorts?channel=), where the numbers come from and its ideas. The owner or an admin
   adds, edits and retires a channel (/api/v2/growth/channels), corrects a month's numbers (PUT /api/v2/growth/cohorts)
   and keeps the settings the maths uses (PUT /api/v2/growth/settings); a bot may record numbers too.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const GC_KEPT = [['kept_d30', '30'], ['kept_d90', '90'], ['kept_d180', '180']];
const gcQuality = q => q ? `<span class="pill ${q === 'actual' ? 'ok' : 'waiting'}">${q === 'actual' ? 'Actual' : 'Estimate'}</span>` : '';
const gcRatio = v => v == null ? '—' : `${grX(v)}×`;
const gcKey = slug => slug || 'unknown';
const gcHref = state => GR_CHANNELS + (state.month ? '?m=' + encodeURIComponent(state.month) : '');

function pageGrowthChannels(state) {
  state.month = new URLSearchParams((S.route || '').split('?')[1] || '').get('m') || '';
  state.pick = null;
  void gcRead(state);
}
async function gcRead(state) {
  let econ, ideas, chans, sources, kpis;
  try {
    [econ, ideas, chans, sources, kpis] = await Promise.all([get('/v2/growth/economics' + (state.month ? '?month=' + encodeURIComponent(state.month) : ''), {fresh: true}),
      get('/v2/growth/ideas').then(r => r.ideas || []).catch(() => []), get('/v2/growth/channels', {fresh: true}).catch(() => ({})),
      get('/v2/growth/sources').then(r => r.sources || []).catch(() => []), get('/v2/growth/kpis').then(r => r.kpis || []).catch(() => [])]);
  } catch (e) { if (GR === state) $('#gr-body').innerHTML = grFail(e); return; }
  if (GR !== state) return;
  // may_edit on the channels read: the caller is the owner or an admin, who keeps channels, settings and numbers.
  state.data = {econ, ideas, chans: chans.channels || [], sources, kpis, may: chans.may_edit ?? grAdmin()};
  state.month = econ.month || state.month;
  gcDraw(state);
}
// The rows: each live channel in its order, any other channel the month has numbers for, then Unknown.
function gcRows(d) {
  const econ = d.econ.rows || [], byKey = new Map(econ.map(r => [gcKey(r.channel), r]));
  const live = d.chans.filter(c => !c.retired_at).sort((a, b) => (a.sort ?? 0) - (b.sort ?? 0) || String(a.name).localeCompare(String(b.name)));
  const rows = live.map(c => ({key: c.slug, name: c.name, ch: c, r: byKey.get(c.slug) || {}}));
  for (const r of econ) if (r.channel && r.channel !== 'unknown' && !rows.some(x => x.key === r.channel)) rows.push({key: r.channel, name: r.name || grChanName(d.chans, r.channel), ch: d.chans.find(c => c.slug === r.channel), r});
  rows.push({key: 'unknown', name: 'Unknown', ch: null, r: byKey.get('unknown') || {}});
  return rows;
}
const gcStats = r => `<span class="gc-ms"><span class="gc-m" data-m="new"><b>${esc(grX(r.new_customers, 0))}</b><small>New</small></span>
  <span class="gc-m" data-m="cac"><b>${esc(grUsd(r.cac))}</b><small>CAC</small></span><span class="gc-m" data-m="ltv_cac"><b>${esc(gcRatio(r.ltv_cac))}</b><small>LTV:CAC</small></span></span>`;
function gcDraw(state) {
  const body = $('#gr-body'), d = state.data;
  if (!body || !d?.econ) return;
  const may = !!d.may;
  patchHTML($('#strat-actions'), may ? '<button class="primary" type="button" data-ch-add>+ Channel</button>' : '');
  const months = (d.econ.months || []).slice().sort().reverse();
  if (state.month && !months.includes(state.month)) months.unshift(state.month);
  const pick = months.length > 1 ? `<label class="gr-inline gc-month"><span class="sr-only">Month</span><select data-gc-month>${months.map(m => `<option value="${esc(m)}"${m === state.month ? ' selected' : ''}>${esc(grMonth(m))}</option>`).join('')}</select></label>` : '';
  // Below the list: the month, when there is a choice, and the settings. The list itself shows the latest month.
  const tools = pick + '<span class="spacer"></span>' + (may ? '<button class="ghost gc-set" type="button" data-gc-settings aria-label="Settings" title="Settings"><span class="nav-icon" aria-hidden="true">settings</span></button>' : '');
  const breached = new Set((d.econ.guardrails || []).filter(g => g.breached).map(g => gcKey(g.channel)));
  const row = x => {
    const on = state.pick === x.key, owner = grWho(x.ch?.owner_ref);
    return `<li class="gc-li${on ? ' gc-on' : ''}${breached.has(x.key) ? ' gc-breach' : ''}"><button type="button" class="gc-row" data-gc-row="${esc(x.key)}" aria-expanded="${on}">
      <span class="gc-name"><span class="gc-n">${esc(x.name)}</span>${x.ch ? grStage(x.ch.stage) : ''}</span>
      <span class="muted gc-own">${esc(owner)}</span>${gcStats(x.r)}</button>${on ? '<div class="gc-detail" id="gc-detail"></div>' : ''}</li>`;
  };
  const t = d.econ.total || {};
  body.innerHTML = `<section class="card gc-list" aria-label="Channels"><ul class="gc-ul">${gcRows(d).map(row).join('')}
    <li class="gc-li gc-all"><div class="gc-row"><span class="gc-name"><span class="gc-n">All channels</span></span><span class="gc-own"></span>${gcStats(t)}</div></li></ul></section>${pick || may ? `<div class="gr-bar gc-bar">${tools}</div>` : ''}`;
  const sel = body.querySelector('[data-gc-month]');
  if (sel) sel.onchange = () => { state.month = sel.value; history.replaceState(history.state, '', gcHref(state)); void gcRead(state); };
  const set = body.querySelector('[data-gc-settings]');
  if (set) set.onclick = () => gcSettings(state);
  const add = $('#strat-actions [data-ch-add]');
  if (add) add.onclick = () => gcChannelDialog(state);
  body.querySelectorAll('[data-gc-row]').forEach(b => { b.onclick = () => { state.pick = state.pick === b.dataset.gcRow ? null : b.dataset.gcRow; gcDraw(state); }; });
  if (state.pick) void gcDetail(state);
}

async function gcDetail(state) {
  const k = state.pick, {econ, ideas, chans, sources} = state.data;
  // The server records numbers for the owner, an admin or a bot; a human channel owner is none of these.
  const ch = chans.find(c => c.slug === k), may = !!state.data.may, mayNums = may || S.me?.role === 'bot';
  const box = $('#gc-detail');
  if (!box) return;
  box.innerHTML = skelRows(3, {dot: false});
  let cohorts = [];
  try { cohorts = (await get('/v2/growth/cohorts?channel=' + encodeURIComponent(k), {fresh: true})).cohorts || []; }
  catch (e) { if (GR === state && state.pick === k) box.innerHTML = grFail(e); return; }
  if (GR !== state || state.pick !== k || !box.isConnected) return;
  // The channel's whole months, newest first (a month split out by buyer is a breakdown of one of these).
  cohorts = cohorts.filter(c => !c.segment_id).sort((a, b) => String(b.month).localeCompare(String(a.month)));
  const guards = (econ.guardrails || []).filter(g => gcKey(g.channel) === k && g.breached);
  const banner = guards.map(g => `<div class="gr-banner gr-amber">${esc(g.kpi_name || 'Guardrail')} ${esc(grX(g.value))} · max ${esc(grX(g.max))}</div>`).join('');
  const tools = [mayNums ? '<button class="ghost small" type="button" data-gc-month-add>+ Month</button>' : '',
    may && ch ? '<button class="ghost small" type="button" data-ch-edit>Edit</button>' : ''].join('');
  const kept = cohorts.length ? `<section class="gr-sec"><h3 class="gr-k">Kept, by month joined</h3>${cohorts.map((c, n) =>
    `<div class="gr-row gc-coh"><span class="gr-row-t">${esc(grMonth(c.month))}</span><span class="gr-num">${esc(grX(c.new_customers, 0))} new</span>
      <span class="gc-kept">${GC_KEPT.map(([f, dd]) => `<span class="gc-h" title="${dd} days"${c[f] == null ? '' : ` style="--k:${Math.max(0, Math.min(100, Number(c[f])))}"`}>${esc(grPct(c[f]))}</span>`).join('')}</span>
      ${gcQuality(c.quality)}${mayNums ? `<button class="ghost small" type="button" data-gc-edit="${n}">Edit</button>` : ''}</div>`).join('')}</section>` : '';
  const r = (econ.rows || []).find(x => gcKey(x.channel) === k) || {};
  const from = {...(r.sources || {})}, src = sources.find(s => s.id === ch?.source_id);
  const where = Object.keys(from).length || src ? `<section class="gr-sec"><h3 class="gr-k">Numbers from</h3>
      ${src ? `<div class="gr-row"><span class="gr-row-t">${esc(src.truth_for)}</span><a href="${GR_SOURCES}">${esc(src.system)}</a></div>` : ''}
      ${Object.entries(from).map(([f, v]) => `<div class="gr-row"><span class="gr-row-t">${esc(f.replace(/_/g, ' '))}</span><span class="muted">${esc(v)}</span></div>`).join('')}</section>` : '';
  const mine = grSort(ideas.filter(i => k === 'unknown' ? !(i.channels || []).length : (i.channels || []).includes(k)));
  const list = mine.length ? `<section class="gr-sec"><h3 class="gr-k">Ideas</h3><div class="gr-flow">${mine.map(i => grIdeaCard(i, chans, {chan: false, result: true})).join('')}</div></section>` : '';
  box.innerHTML = `${tools ? `<div class="gc-dh">${tools}</div>` : ''}${banner}${kept}<div class="gc-dcols">${where}${list}</div>`;
  box.querySelectorAll('[data-gc-edit]').forEach(b => { b.onclick = () => gcCohortDialog(state, k, cohorts[Number(b.dataset.gcEdit)]); });
  const addM = box.querySelector('[data-gc-month-add]');
  if (addM) addM.onclick = () => gcCohortDialog(state, k, {month: state.month || new Date().toISOString().slice(0, 7)}, true);
  const edit = box.querySelector('[data-ch-edit]');
  if (edit) edit.onclick = () => gcChannelDialog(state, ch);
}

// One month's numbers for channel `k`; `fresh` picks the month in the form.
function gcCohortDialog(state, k, c, fresh = false) {
  const n = (name, label, v) => grField(label, `<input name="${name}" inputmode="decimal" value="${esc(v ?? '')}">`);
  const srcs = c.sources || {};
  grDialog([k === 'unknown' ? 'Unknown' : grChanName(state.data.chans, k), fresh ? 'New month' : grMonth(c.month)].join(' · '),
    `${fresh ? grField('Month', `<input name="month" type="month" required value="${esc(c.month)}">`) : ''}
    <div class="gr-pair">${n('spend', 'Spend', c.spend)}${n('bot_cost', 'Bot time', c.bot_cost)}</div>
    <div class="gr-pair">${n('new_customers', 'New', c.new_customers)}${grField('Data', `<select name="quality">${grOpts([['actual', 'Actual'], ['estimate', 'Estimate']], c.quality || 'estimate', null)}</select>`)}</div>
    <div class="gr-trio">${GC_KEPT.map(([f, d]) => n(f, `Kept ${d} days %`, c[f])).join('')}</div>
    <div class="gr-pair">${n('revenue_month', 'Revenue / mo', c.revenue_month)}${n('margin_pct', 'Margin %', c.margin_pct)}</div>
    ${grField('Spend from', `<input name="src_spend" maxlength="300" value="${esc(srcs.spend || '')}">`)}
    ${grField('New from', `<input name="src_new" maxlength="300" value="${esc(srcs.new_customers || '')}">`)}`, 'Save', async form => {
      const v = name => form.elements[name].value;
      // The server merges these keys into what it keeps; an empty one removes that key.
      const sources = {...srcs, spend: v('src_spend').trim(), new_customers: v('src_new').trim()};
      const month = fresh ? v('month') : c.month;
      if (!/^\d{4}-\d{2}$/.test(month)) throw new Error('A month');
      const row = {channel: k, month, spend: grNum(v('spend')), bot_cost: grNum(v('bot_cost')), new_customers: grNum(v('new_customers')),
        kept_d30: grNum(v('kept_d30')), kept_d90: grNum(v('kept_d90')), kept_d180: grNum(v('kept_d180')), revenue_month: grNum(v('revenue_month')),
        margin_pct: grNum(v('margin_pct')), quality: v('quality'), sources};
      await put('/v2/growth/cohorts', row);
      toast('Saved');
      if (fresh && month !== state.month) { state.month = month; history.replaceState(history.state, '', gcHref(state)); }
      void gcRead(state);
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

// Add or edit a channel; editing also retires it. The buyers a channel reaches are kept by the server, not shown here.
function gcChannelDialog(state, c = null) {
  const kp = state.data.kpis || [], live = (state.data.sources || []).filter(s => !s.retired_at);
  const body = `${grField('Name', `<input name="name" maxlength="80" required value="${esc(c?.name || '')}">`)}
    ${c ? '' : grField('Slug', '<input name="slug" maxlength="40" pattern="[a-z0-9][a-z0-9-]*" required autocapitalize="off">')}
    <div class="gr-pair">${grField('Track', `<select name="track">${grOpts([['performance', 'Performance'], ['brand', 'Brand']], c?.track || 'performance', null)}</select>`)}
    ${grField('Stage', `<select name="stage">${grOpts(Object.entries(GR_CH_STAGE).map(([k, v]) => [k, v[0]]), c?.stage || 'idea', null)}</select>`)}</div>
    <div class="gr-pair">${grField('Owner', `<select name="owner_ref">${grOwnerOpts(c?.owner_ref)}</select>`)}
    ${grField('Budget / mo', `<input name="budget_month" inputmode="decimal" value="${esc(c?.budget_month ?? '')}">`)}</div>
    ${grField('Numbers from', `<select name="source_id">${grOpts(live.map(s => [s.id, `${s.system} · ${s.truth_for}`]), c?.source_id, '—')}</select>`)}
    <div class="gr-pair">${grField('Guardrail', `<select name="guardrail_kpi_id">${grOpts(kp.map(k => [k.id, k.name]), c?.guardrail_kpi_id, '—')}</select>`)}
    ${grField('Max', `<input name="guardrail_max" inputmode="decimal" value="${esc(c?.guardrail_max ?? '')}">`)}</div>
    ${grField('Why', `<textarea name="why" rows="2" maxlength="1000">${esc(c?.why || '')}</textarea>`)}
    ${c ? '<button type="button" class="ghost danger gr-retire" data-retire>Retire</button>' : ''}`;
  const dlg = grDialog(c ? c.name : 'New channel', body, c ? 'Save' : 'Add', async form => {
    const el = form.elements, v = n => el[n].value.trim();
    const out = {name: v('name'), track: v('track'), stage: v('stage'), owner_ref: v('owner_ref') || null, budget_month: grNum(v('budget_month')),
      source_id: v('source_id') || null, guardrail_kpi_id: v('guardrail_kpi_id') || null, guardrail_max: grNum(v('guardrail_max')), why: v('why')};
    if (!out.name) throw new Error('A name');
    if (c) await patch(`/v2/growth/channels/${encodeURIComponent(c.slug)}`, out);
    else {
      out.slug = v('slug').toLowerCase();
      if (!/^[a-z0-9][a-z0-9-]{0,39}$/.test(out.slug)) throw new Error('Slug: lowercase letters, digits and -');
      await post('/v2/growth/channels', out);
    }
    toast('Saved'); void gcRead(state);
  });
  if (!c) {
    const name = dlg.querySelector('[name=name]'), slug = dlg.querySelector('[name=slug]');
    name.oninput = () => { if (!slug.dataset.touched) slug.value = name.value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40); };
    slug.oninput = () => { slug.dataset.touched = '1'; };
  }
  const retire = dlg.querySelector('[data-retire]');
  if (retire) grRetireButton(retire, dlg, `/v2/growth/channels/${encodeURIComponent(c.slug)}/retire`, () => { state.pick = null; void gcRead(state); });
}
