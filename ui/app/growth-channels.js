/* ui/app/growth-channels.js — The Growth page's Settings (ui/app/growth-page.js): the numbers the maths uses (PUT
   /api/v2/growth/settings), the channels (/api/v2/growth/channels: add, edit, retire) and each channel's months, where
   a month is corrected or added by hand (GET|PUT /api/v2/growth/cohorts), and the link to Sources. The owner or an admin
   changes them; the cards read again after a save.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const GC_KEPT = [['kept_d30', '30'], ['kept_d90', '90'], ['kept_d180', '180']];
const gcQuality = q => q ? `<span class="pill ${q === 'actual' ? 'ok' : 'waiting'}">${q === 'actual' ? 'Actual' : 'Estimate'}</span>` : '';
const GC_UNKNOWN = {slug: 'unknown', name: 'Unknown'};

// Settings: the numbers, then each channel with its Months and Edit, + Channel, and where the numbers come from.
async function gcSettings(state) {
  const [st, list] = await Promise.all([get('/v2/growth/settings', {fresh: true}).catch(() => ({})),
    get('/v2/growth/channels', {fresh: true}).then(r => r.channels || []).catch(() => [])]);
  if (GR !== state) return;
  const row = (c, edit) => `<div class="gr-row gs-ch" data-gs-channel="${esc(c.slug)}"><span class="gr-row-t">${esc(c.name)}</span>
      ${edit ? `<span class="muted">${esc([GR_CH_STAGE[c.stage] || c.stage, grWho(c.owner_ref)].filter(Boolean).join(' · '))}</span>` : ''}
      <button class="ghost small" type="button" data-gs-months="${esc(c.slug)}">Months</button>${edit ? `<button class="ghost small" type="button" data-gs-edit="${esc(c.slug)}">Edit</button>` : ''}</div>`;
  const dlg = grDialog('Settings', `<fieldset class="gr-fs"><legend class="gr-k">Numbers</legend><div class="gr-form">
      ${grField('Counted in', `<input name="unit" maxlength="40" value="${esc(st.unit || '')}">`)}
      <div class="gr-pair">${grField('Revenue / customer', `<input name="revenue_month" inputmode="decimal" value="${esc(st.revenue_month ?? '')}">`)}
      ${grField('Margin %', `<input name="margin_pct" inputmode="decimal" value="${esc(st.margin_pct ?? '')}">`)}</div></div></fieldset>
    <section class="gr-sec gs-sec" aria-label="Channels"><h3 class="gr-k">Channels</h3>${list.map(c => row(c, true)).join('')}${row(GC_UNKNOWN, false)}
      <button class="ghost small gs-add" type="button" data-ch-add>+ Channel</button></section>
    <a class="gs-src" href="${GR_SOURCES}" data-gr-sources>Where numbers come from</a>`, 'Save', async form => {
    const v = n => form.elements[n].value;
    const body = {revenue_month: grNum(v('revenue_month')), margin_pct: grNum(v('margin_pct'))};
    if (v('unit').trim()) body.unit = v('unit').trim();
    await put('/v2/growth/settings', body);
    toast('Saved'); grReload(state);
  });
  dlg.querySelector('[data-gr-sources]').onclick = () => dlg.close();
  dlg.querySelector('[data-ch-add]').onclick = () => void gcChannelDialog(state);
  dlg.querySelectorAll('[data-gs-edit]').forEach(b => { b.onclick = () => void gcChannelDialog(state, list.find(c => c.slug === b.dataset.gsEdit)); });
  dlg.querySelectorAll('[data-gs-months]').forEach(b => { b.onclick = () => void gcMonths(state, list.find(c => c.slug === b.dataset.gsMonths) || GC_UNKNOWN); });
}

// A channel's months, newest first, each with its retention and an Edit; + Month adds one.
async function gcMonths(state, ch) {
  let cohorts;
  try { cohorts = await get('/v2/growth/cohorts?channel=' + encodeURIComponent(ch.slug), {fresh: true}).then(r => r.cohorts || []); }
  catch (e) { toast(e.message || 'Did not load', true); return; }
  if (GR !== state) return;
  cohorts.sort((a, b) => String(b.month).localeCompare(String(a.month)));
  const cell = (c, f, dd) => `<span class="gc-h" title="${dd} days"${c[f] == null ? '' : ` style="--k:${Math.max(0, Math.min(100, Number(c[f])))}"`}>${c[f] == null ? '' : esc(grPct(c[f]))}</span>`;
  const dlg = grDialog(`${ch.name} · Months`, `<div class="gr-sec gc-months">${cohorts.map((c, n) =>
    `<div class="gr-row gc-coh"><span class="gr-row-t">${esc(grMonth(c.month))}</span>${c.new_customers == null ? '' : `<span class="gr-num">${esc(grX(c.new_customers, 0))} new</span>`}
      <span class="gc-kept">${GC_KEPT.map(([f, dd]) => cell(c, f, dd)).join('')}</span>${gcQuality(c.quality)}<button class="ghost small" type="button" data-gc-edit="${n}">Edit</button></div>`).join('')
    || '<p class="muted gr-note">No months yet</p>'}</div>
    <button class="ghost small gc-add" type="button" data-gc-month-add>+ Month</button>`, '', null);
  dlg.querySelectorAll('[data-gc-edit]').forEach(b => { b.onclick = () => gcCohortDialog(state, ch, cohorts[Number(b.dataset.gcEdit)]); });
  dlg.querySelector('[data-gc-month-add]').onclick = () => gcCohortDialog(state, ch, {month: state.month || new Date().toISOString().slice(0, 7)}, true);
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
      grReload(state);
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
    toast('Saved'); grReload(state);
  });
  if (!c) {
    const name = dlg.querySelector('[name=name]'), slug = dlg.querySelector('[name=slug]');
    name.oninput = () => { if (!slug.dataset.touched) slug.value = name.value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40); };
    slug.oninput = () => { slug.dataset.touched = '1'; };
  }
  const retire = dlg.querySelector('[data-retire]');
  if (retire) grRetireButton(retire, dlg, `/v2/growth/channels/${encodeURIComponent(c.slug)}/retire`, () => grReload(state));
}
