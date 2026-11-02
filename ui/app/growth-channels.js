/* ui/app/growth-channels.js — The Channels section of the Growth page: one row each with its stage, owner and the picked
   month's New, CAC and LTV:CAC, Unknown last. Choosing a row opens it in place: a larger chart with that channel's task
   ticks, retention, where its numbers come from (its source, GET /api/v2/growth/sources, and the month's notes), its open
   tasks (GET /api/v2/growth/channels/{slug}) and its months (GET /api/v2/growth/cohorts?channel=). Whoever may edit adds,
   edits and retires a channel (/api/v2/growth/channels), corrects a month (PUT /api/v2/growth/cohorts) and keeps the
   settings the maths uses (PUT /api/v2/growth/settings).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const GC_KEPT = [['kept_d30', '30'], ['kept_d90', '90'], ['kept_d180', '180']];
const GC_SHOWN = 10;      // rows before All, when there are clearly more
const gcQuality = q => q ? `<span class="pill ${q === 'actual' ? 'ok' : 'waiting'}">${q === 'actual' ? 'Actual' : 'Estimate'}</span>` : '';
const gcCell = (key, v, fmt, from = '') => `<span class="gc-m${from ? ' gc-old' : ''}" data-m="${key}"${from ? ` title="${esc(grMonthShort(from))}"` : ''}>${v == null ? '' : esc(fmt(v))}</span>`;
// LTV needs retention, which a new cohort has yet to show: the row keeps the channel's last, dimmed.
const gcLastLtv = (c, month) => [...c.series].reverse().find(x => x.month <= month && x.ltv_cac != null);
const gcBreached = state => new Set((state.econ?.guardrails || []).filter(g => g.breached).map(g => g.channel || 'unknown'));

function gcDraw(state) {
  const box = $('#gr-channels'), d = state.data;
  if (!box) return;
  const list = d.channels, many = list.length > GC_SHOWN + 2 && !state.showAll;
  // An open row stays shown when the list folds.
  const shown = many ? list.filter((c, i) => i < GC_SHOWN || c.slug === state.pick) : list;
  const breached = gcBreached(state);
  const row = c => {
    const on = state.pick === c.slug, r = c.series.find(x => x.month === state.month) || {}, ltv = gcLastLtv(c, state.month);
    const meta = [GR_CH_STAGE[c.stage] || c.stage, grWho(c.owner_ref)].filter(Boolean).join(' · ');
    return `<li class="gc-li${on ? ' gc-on' : ''}"><button type="button" class="gc-row" data-gc-row="${esc(c.slug)}" aria-expanded="${on}">
      <span class="gc-name"><span class="gc-n">${esc(c.name)}${breached.has(c.slug) ? '<i class="gc-flag" aria-label="Over its guardrail"></i>' : ''}</span>${meta ? `<span class="gc-meta">${esc(meta)}</span>` : ''}</span>
      ${gcCell('new', r.new_customers, v => grX(v, 0))}${gcCell('cac', r.cac, grUsd)}${gcCell('ltv_cac', ltv?.ltv_cac, grRatio, ltv && ltv.month !== state.month ? ltv.month : '')}</button>
      ${on ? '<div class="gc-detail" id="gc-detail"></div>' : ''}</li>`;
  };
  box.innerHTML = `<h2 class="gr-k">Channels</h2><div class="card gr-list gc-list"><div class="gc-row gc-headrow" aria-hidden="true"><span></span><span>New</span><span>CAC</span><span>LTV:CAC</span></div>
    <ul class="gr-ul">${shown.map(row).join('')}</ul>${many ? '<button class="ghost gr-more" type="button" data-gc-all>All</button>' : ''}</div>`;
  box.querySelectorAll('[data-gc-row]').forEach(b => { b.onclick = () => { state.pick = state.pick === b.dataset.gcRow ? null : b.dataset.gcRow; gcDraw(state); }; });
  const all = box.querySelector('[data-gc-all]');
  if (all) all.onclick = () => { state.showAll = true; gcDraw(state); };
  if (state.pick) void gcDetail(state);
}

async function gcDetail(state) {
  const k = state.pick, box = $('#gc-detail'), d = state.data, ch = d.channels.find(c => c.slug === k);
  if (!box || !ch) return;
  box.innerHTML = skelRows(3, {dot: false});
  let cohorts = [], full = null, sources = [];
  try {
    [cohorts, full, sources] = await Promise.all([
      get('/v2/growth/cohorts?channel=' + encodeURIComponent(k), {fresh: true}).then(r => r.cohorts || []),
      k === 'unknown' ? null : get('/v2/growth/channels/' + encodeURIComponent(k), {fresh: true}),
      get('/v2/growth/sources').then(r => r.sources || []).catch(() => [])]);
  } catch (e) { if (GR === state && state.pick === k) box.innerHTML = grFail(e); return; }
  if (GR !== state || state.pick !== k || !box.isConnected) return;
  cohorts.sort((a, b) => String(b.month).localeCompare(String(a.month)));
  const may = !!d.may_edit, cal = grCalendar(d.months);
  const guards = (state.econ?.guardrails || []).filter(g => (g.channel || 'unknown') === k && g.breached);
  const banner = guards.map(g => `<div class="gr-banner gr-amber">${esc(g.kpi_name || 'Guardrail')} ${esc(grX(g.value))} · max ${esc(grX(g.max))}</div>`).join('');
  const hasChart = ch.series.filter(r => r.new_customers != null).length > 1;
  const kept = GR_RETENTION.map(s => grSeriesStat(s, ch.series, cal, state.month)).join('');
  const notes = cohorts.find(c => c.month === state.month)?.sources || {}, src = sources.find(s => s.id === full?.source_id);
  const from = Object.entries(notes).filter(([, v]) => v);
  const where = src || from.length ? `<section class="gr-sec" data-gc-from><h3 class="gr-k">Numbers from</h3>
      ${src ? `<div class="gr-row"><span class="gr-row-t">${esc(src.truth_for)}</span><a href="${GR_SOURCES}">${esc(src.system)}</a></div>` : ''}
      ${from.map(([f, v]) => `<div class="gr-row"><span class="gr-row-t">${esc(f[0].toUpperCase() + f.slice(1).replace(/_/g, ' '))}</span><span class="muted">${esc(v)}</span></div>`).join('')}</section>` : '';
  const tasks = full?.tasks || [];
  const open = tasks.length ? `<section class="gr-sec" data-gc-tasks><h3 class="gr-k">Open tasks</h3>${tasks.map(t =>
    `<a class="gr-row" href="${grTaskHref(t.task_id)}"><span class="gr-row-t">${esc(t.title)}</span>${t.assignee ? `<span class="muted">${esc(grWho(t.assignee))}</span>` : ''}</a>`).join('')}</section>` : '';
  const cell = (c, f, dd) => `<span class="gc-h" title="${dd} days"${c[f] == null ? '' : ` style="--k:${Math.max(0, Math.min(100, Number(c[f])))}"`}>${c[f] == null ? '' : esc(grPct(c[f]))}</span>`;
  const months = cohorts.length || may ? `<details class="gc-months"><summary>Months</summary>${cohorts.map((c, n) =>
    `<div class="gr-row gc-coh"><span class="gr-row-t">${esc(grMonth(c.month))}</span>${c.new_customers == null ? '' : `<span class="gr-num">${esc(grX(c.new_customers, 0))} new</span>`}
      <span class="gc-kept">${GC_KEPT.map(([f, dd]) => cell(c, f, dd)).join('')}</span>${gcQuality(c.quality)}${may ? `<button class="ghost small" type="button" data-gc-edit="${n}">Edit</button>` : ''}</div>`).join('')}
      ${may ? '<button class="ghost small gc-add" type="button" data-gc-month-add>+ Month</button>' : ''}</details>` : '';
  box.innerHTML = `${banner}${hasChart ? `<div class="gr-sec"><h3 class="gr-k">New customers</h3><div class="gr-chart" data-gc-chart tabindex="0" role="img" aria-label="${esc(ch.name)}"></div></div>` : ''}
    ${kept ? `<div class="gr-sec" data-gc-kept><h3 class="gr-k">Retention</h3><div class="gr-stats">${kept}</div></div>` : ''}
    ${where || open ? `<div class="gc-dcols">${where}${open}</div>` : ''}${months}
    ${may && full ? '<div class="gc-dh"><button class="ghost small" type="button" data-ch-edit>Edit channel</button></div>' : ''}`;
  const host = box.querySelector('[data-gc-chart]');
  if (host) grMonthChart(host, ch.series, cal, state.month, GR_NEW, d.markers.filter(m => (m.channels || []).includes(k)));
  box.querySelectorAll('[data-gc-edit]').forEach(b => { b.onclick = () => gcCohortDialog(state, ch, cohorts[Number(b.dataset.gcEdit)]); });
  const addM = box.querySelector('[data-gc-month-add]');
  if (addM) addM.onclick = () => gcCohortDialog(state, ch, {month: state.month || new Date().toISOString().slice(0, 7)}, true);
  const edit = box.querySelector('[data-ch-edit]');
  if (edit) edit.onclick = () => void gcChannelDialog(state, full);
}

// One month's numbers for a channel; `fresh` picks the month in the form.
function gcCohortDialog(state, ch, c, fresh = false) {
  const n = (name, label, v) => grField(label, `<input name="${name}" inputmode="decimal" value="${esc(v ?? '')}">`);
  const srcs = c.sources || {};
  grDialog([ch.name, fresh ? 'New month' : grMonth(c.month)].join(' · '),
    `${fresh ? grField('Month', `<input name="month" type="month" required value="${esc(c.month)}">`) : ''}
    <div class="gr-pair">${n('spend', 'Spend', c.spend)}${n('bot_cost', 'Bot cost', c.bot_cost)}</div>
    <div class="gr-pair">${n('new_customers', 'New', c.new_customers)}${grField('Data', `<select name="quality">${grOpts([['actual', 'Actual'], ['estimate', 'Estimate']], c.quality || 'estimate', null)}</select>`)}</div>
    <div class="gr-trio">${GC_KEPT.map(([f, d]) => n(f, `Kept ${d} days %`, c[f])).join('')}</div>
    <div class="gr-pair">${n('revenue_month', 'Revenue / customer', c.revenue_month)}${n('margin_pct', 'Margin %', c.margin_pct)}</div>
    ${grField('Spend from', `<input name="src_spend" maxlength="300" value="${esc(srcs.spend || '')}">`)}
    ${grField('New from', `<input name="src_new" maxlength="300" value="${esc(srcs.new_customers || '')}">`)}`, 'Save', async form => {
      const v = name => form.elements[name].value;
      // The server merges these keys into what it keeps; an empty one removes that key.
      const sources = {...srcs, spend: v('src_spend').trim(), new_customers: v('src_new').trim()};
      const month = fresh ? v('month') : c.month;
      if (!/^\d{4}-\d{2}$/.test(month)) throw new Error('A month');
      await put('/v2/growth/cohorts', {channel: ch.slug, month, spend: grNum(v('spend')), bot_cost: grNum(v('bot_cost')), new_customers: grNum(v('new_customers')),
        kept_d30: grNum(v('kept_d30')), kept_d90: grNum(v('kept_d90')), kept_d180: grNum(v('kept_d180')), revenue_month: grNum(v('revenue_month')),
        margin_pct: grNum(v('margin_pct')), quality: v('quality'), sources});
      toast('Saved');
      void grRead(state);
    });
}

async function gcSettings(state) {
  const st = await get('/v2/growth/settings', {fresh: true}).catch(() => ({}));
  if (GR !== state) return;
  grDialog('Settings', `${grField('Counted in', `<input name="unit" maxlength="40" value="${esc(st.unit || '')}">`)}
    <div class="gr-pair">${grField('Revenue / customer', `<input name="revenue_month" inputmode="decimal" value="${esc(st.revenue_month ?? '')}">`)}
    ${grField('Margin %', `<input name="margin_pct" inputmode="decimal" value="${esc(st.margin_pct ?? '')}">`)}</div>`, 'Save', async form => {
    const v = n => form.elements[n].value;
    const body = {revenue_month: grNum(v('revenue_month')), margin_pct: grNum(v('margin_pct'))};
    if (v('unit').trim()) body.unit = v('unit').trim();
    await put('/v2/growth/settings', body);
    toast('Saved'); void grRead(state);
  });
}

// Add or edit a channel; editing also retires it.
async function gcChannelDialog(state, c = null) {
  const [sources, kp] = await Promise.all([get('/v2/growth/sources').then(r => r.sources || []).catch(() => []),
    get('/v2/growth/kpis').then(r => r.kpis || []).catch(() => [])]);
  if (GR !== state) return;
  const live = sources.filter(s => !s.retired_at);
  const body = `${grField('Name', `<input name="name" maxlength="80" required value="${esc(c?.name || '')}">`)}
    ${c ? '' : grField('Slug', '<input name="slug" maxlength="40" pattern="[a-z0-9][a-z0-9-]*" required autocapitalize="off">')}
    <div class="gr-pair">${grField('Track', `<select name="track">${grOpts([['performance', 'Performance'], ['brand', 'Brand']], c?.track || 'performance', null)}</select>`)}
    ${grField('Stage', `<select name="stage">${grOpts(Object.entries(GR_CH_STAGE), c?.stage || 'idea', null)}</select>`)}</div>
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
    toast('Saved'); void grRead(state);
  });
  if (!c) {
    const name = dlg.querySelector('[name=name]'), slug = dlg.querySelector('[name=slug]');
    name.oninput = () => { if (!slug.dataset.touched) slug.value = name.value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40); };
    slug.oninput = () => { slug.dataset.touched = '1'; };
  }
  const retire = dlg.querySelector('[data-retire]');
  if (retire) grRetireButton(retire, dlg, `/v2/growth/channels/${encodeURIComponent(c.slug)}/retire`, () => { state.pick = null; void grRead(state); });
}
