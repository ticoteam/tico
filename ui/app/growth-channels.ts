/* ui/app/growth-channels.ts — The Growth page's Settings (ui/app/growth-page.ts): the numbers the maths uses (PUT
   /api/v2/growth/settings), the channels (/api/v2/growth/channels: add, edit, retire) and each channel's months, where
   a month is corrected or added by hand (GET|PUT /api/v2/growth/cohorts), and the link to Sources. Each number in a
   month says where it came from: a synced source's (locked, since the source owns it) or who typed it, and a source
   that disagrees with a typed actual offers Keep and Use. The owner or an admin changes them; the cards read again after
   a save.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

type GrowthKeptField = 'kept_d30' | 'kept_d90' | 'kept_d180';
interface ChannelRef { slug: string; name: string; }
interface ChannelLine extends ChannelRef { stage?: string; owner_ref?: string | null; }
interface GrowthSettings { unit?: string | null; revenue_month?: number | null; margin_pct?: number | null; }
interface GrowthChannelRow extends ChannelRef {
  stage: string;
  owner_ref: string | null;
  track: string;
  budget_month: number | null;
  source_id: string | null;
  guardrail_kpi_id: string | null;
  guardrail_max: number | null;
  why: string;
}
interface GrowthCohortSources { spend?: string; new_customers?: string; }
interface GrowthCohort {
  month: string;
  spend: number | null;
  bot_cost: number | null;
  new_customers: number | null;
  kept_d30: number | null;
  kept_d90: number | null;
  kept_d180: number | null;
  revenue_month: number | null;
  margin_pct: number | null;
  quality: 'actual' | 'estimate' | null;
  sources?: GrowthCohortSources | null;
  origins?: Record<string, GrowthOrigin> | null;
}
// One number's origin (backend/growth_sync.py origins_view): synced by a source or typed by someone, and a value a source
// brought that was held back because someone typed an actual.
interface GrowthOrigin {
  origin: 'synced' | 'typed'; quality: string; source_id: string | null; source: string | null; at: string | null; by: string | null;
  conflict: { value: number | null; source_id: string | null; source: string | null; at: string | null } | null;
}
interface GrowthSource { id: string; system: string; truth_for: string; retired_at?: string | null; kind?: string | null; }
interface GrowthKpi { id: string; name: string; }
/** The form's fields by name, as the dialogs read them (grDialog hands the form element); K is the names the dialog reads. */
type GrowthFormFields<K extends string> = HTMLFormControlsCollection & Record<K, { value: string }>;
type SettingsField = 'unit' | 'revenue_month' | 'margin_pct';
type CohortField = 'month' | 'spend' | 'bot_cost' | 'new_customers' | 'quality' | 'src_spend' | 'src_new' | 'revenue_month' | 'margin_pct' | GrowthKeptField;
type ChannelField = 'name' | 'slug' | 'track' | 'stage' | 'owner_ref' | 'budget_month' | 'source_id' | 'guardrail_kpi_id' | 'guardrail_max' | 'why';
interface ChannelBody {
  slug?: string;
  name: string;
  track: string;
  stage: string;
  owner_ref: string | null;
  budget_month: number | null;
  source_id: string | null;
  guardrail_kpi_id: string | null;
  guardrail_max: number | null;
  why: string;
}

const GC_KEPT: ReadonlyArray<readonly [GrowthKeptField, string]> = [['kept_d30', '30'], ['kept_d90', '90'], ['kept_d180', '180']];
const gcQuality = (q: string | null | undefined) => q ? `<span class="pill ${q === 'actual' ? 'ok' : 'waiting'}">${q === 'actual' ? 'Actual' : 'Estimate'}</span>` : '';
const GC_UNKNOWN: ChannelRef = {slug: 'unknown', name: 'Unknown'};

// Settings: the numbers, then each channel with its Months and Edit, + Channel, and where the numbers come from.
async function gcSettings(state: GrowthState) {
  const [st, list] = await Promise.all([get<GrowthSettings>('/v2/growth/settings', {fresh: true}).catch((): GrowthSettings => ({})),
    get<{channels?: GrowthChannelRow[]}>('/v2/growth/channels', {fresh: true}).then(r => r.channels || []).catch((): GrowthChannelRow[] => [])]);
  if (GR !== state) return;
  const row = (c: ChannelLine, edit: boolean) => `<div class="gr-row gs-ch" data-gs-channel="${esc(c.slug)}"><span class="gr-row-t">${esc(c.name)}</span>
      ${edit ? `<span class="muted">${esc([GR_CH_STAGE[c.stage ?? ''] || c.stage, grWho(c.owner_ref)].filter(Boolean).join(' · '))}</span>` : ''}
      <button class="ghost small" type="button" data-gs-months="${esc(c.slug)}">Months</button>${edit ? `<button class="ghost small" type="button" data-gs-edit="${esc(c.slug)}">Edit</button>` : ''}</div>`;
  const dlg = grDialog('Settings', `<fieldset class="gr-fs"><legend class="gr-k">Numbers</legend><div class="gr-form">
      ${grField('Counted in', `<input name="unit" maxlength="40" value="${esc(st.unit || '')}">`)}
      <div class="gr-pair">${grField('Revenue / customer', `<input name="revenue_month" inputmode="decimal" value="${esc(st.revenue_month ?? '')}">`)}
      ${grField('Margin %', `<input name="margin_pct" inputmode="decimal" value="${esc(st.margin_pct ?? '')}">`)}</div></div></fieldset>
    <section class="gr-sec gs-sec" aria-label="Channels"><h3 class="gr-k">Channels</h3>${list.map(c => row(c, true)).join('')}${row(GC_UNKNOWN, false)}
      <button class="ghost small gs-add" type="button" data-ch-add>+ Channel</button></section>
    <a class="gs-src" href="${GR_SOURCES}" data-gr-sources>Where numbers come from</a>`, 'Save', async form => {
    const v = (n: SettingsField) => (form.elements as GrowthFormFields<SettingsField>)[n].value;
    const body: GrowthSettings = {revenue_month: grNum(v('revenue_month')), margin_pct: grNum(v('margin_pct'))};
    if (v('unit').trim()) body.unit = v('unit').trim();
    await put('/v2/growth/settings', body);
    toast('Saved'); grReload(state);
  });
  const sources = dlg.querySelector<HTMLElement>('[data-gr-sources]');
  if (sources) sources.onclick = () => dlg.close();
  const add = dlg.querySelector<HTMLElement>('[data-ch-add]');
  if (add) add.onclick = () => void gcChannelDialog(state);
  dlg.querySelectorAll<HTMLButtonElement>('[data-gs-edit]').forEach(b => { b.onclick = () => void gcChannelDialog(state, list.find(c => c.slug === b.dataset.gsEdit)); });
  dlg.querySelectorAll<HTMLButtonElement>('[data-gs-months]').forEach(b => { b.onclick = () => void gcMonths(state, list.find(c => c.slug === b.dataset.gsMonths) || GC_UNKNOWN); });
}

// A channel's months, newest first, each with its retention and an Edit; + Month adds one.
async function gcMonths(state: GrowthState, ch: ChannelRef) {
  let cohorts: GrowthCohort[], live: Set<string>;
  try {
    const [rows, sources] = await Promise.all([get<{cohorts?: GrowthCohort[]}>('/v2/growth/cohorts?channel=' + encodeURIComponent(ch.slug), {fresh: true}).then(r => r.cohorts || []),
      get<{sources?: GrowthSource[]}>('/v2/growth/sources', {fresh: true}).then(r => r.sources || []).catch((): GrowthSource[] => [])]);
    cohorts = rows;
    live = new Set(sources.filter(x => x.kind && !x.retired_at).map(x => x.id));
  } catch (e) { toast(errorMessage(e, 'Did not load'), true); return; }
  if (GR !== state) return;
  cohorts.sort((a, b) => String(b.month).localeCompare(String(a.month)));
  const cell = (c: GrowthCohort, f: GrowthKeptField, dd: string) => `<span class="gc-h" title="${dd} days"${c[f] == null ? '' : ` style="--k:${Math.max(0, Math.min(100, Number(c[f])))}"`}>${c[f] == null ? '' : esc(grPct(c[f]))}</span>`;
  const dlg = grDialog(`${ch.name} · Months`, `<div class="gr-sec gc-months">${cohorts.map((c, n) =>
    `<div class="gr-row gc-coh"><span class="gr-row-t">${esc(grMonth(c.month))}</span>${c.new_customers == null ? '' : `<span class="gr-num">${esc(grX(c.new_customers, 0))} new</span>`}
      <span class="gc-kept">${GC_KEPT.map(([f, dd]) => cell(c, f, dd)).join('')}</span>${gcQuality(c.quality)}${gcSyncedMark(c)}<button class="ghost small" type="button" data-gc-edit="${n}">Edit</button></div>`).join('')
    || '<p class="muted gr-note">No months yet</p>'}</div>
    <button class="ghost small gc-add" type="button" data-gc-month-add>+ Month</button>`, '', null);
  dlg.querySelectorAll<HTMLButtonElement>('[data-gc-edit]').forEach(b => { b.onclick = () => { const cohort = cohorts[Number(b.dataset.gcEdit)]; if (cohort) gcCohortDialog(state, ch, cohort, false, live); }; });
  const monthAdd = dlg.querySelector<HTMLElement>('[data-gc-month-add]');
  if (monthAdd) monthAdd.onclick = () => gcCohortDialog(state, ch, {month: state.month || new Date().toISOString().slice(0, 7)}, true, live);
}
// A month's row says which sources filled some of it, and when a source disagrees with a typed number.
function gcSyncedMark(c: GrowthCohort) {
  const all = Object.values(c.origins || {});
  const names = [...new Set(all.filter(o => o.origin === 'synced' && o.source).map(o => String(o.source)))];
  const held = all.filter(o => o.conflict).length;
  return `${names.length ? `<span class="muted gc-synced" title="Synced from ${esc(names.join(', '))}">${esc(names.join(', '))}</span>` : ''}${held ? `<span class="pill waiting" data-gc-differs>Source differs</span>` : ''}`;
}
// One of a month's numbers by its field name.
const gcValue = (c: Partial<GrowthCohort>, field: string): number | null => { const v = (c as Record<string, unknown>)[field]; return typeof v === 'number' ? v : null; };
// Under a number in a month: where it came from, and Keep / Use when a source disagrees with what was typed.
function gcOriginHint(ch: ChannelRef, month: string, field: string, c: Partial<GrowthCohort>, o: GrowthOrigin | undefined) {
  if (!o) return '';
  const line = o.origin === 'synced' ? `From ${o.source || 'a source'}${o.at ? ` · ${ago(o.at)}` : ''} · actual`
    : `Typed${o.by ? ` by ${grWho(o.by)}` : ''}${o.at ? `, ${grDay(o.at)}` : ''} · ${o.quality === 'estimate' ? 'estimate' : 'actual'}`;
  const typed = gcValue(c, field);
  const conflict: GrConflict | null = o.conflict ? {channel: ch.slug, channel_name: ch.name, month, field, typed: typed ?? null, value: o.conflict.value, source: o.conflict.source, may: true} : null;
  return `<span class="gc-origin muted" data-gc-origin="${esc(field)}">${esc(line)}</span>${conflict ? `<span class="gr-conflict">${grConflictLine(conflict, false)}</span>` : ''}`;
}

// One month's numbers for a channel; `fresh` picks the month in the form.
// A number a live synced source filled is locked: that source owns it (typing it is refused, naming the source). Only
// the numbers changed are sent, so a month with synced numbers still saves its typed ones.
const GC_NUMBERS = ['spend', 'bot_cost', 'new_customers', 'kept_d30', 'kept_d90', 'kept_d180', 'revenue_month', 'margin_pct'] as const;
function gcCohortDialog(state: GrowthState, ch: ChannelRef, c: Partial<GrowthCohort>, fresh = false, live: Set<string> = new Set()) {
  const origins = c.origins || {}, month = c.month ?? '';
  const locked = (name: string) => { const o = origins[name]; return !!o && o.origin === 'synced' && !!o.source_id && live.has(o.source_id); };
  const n = (name: string, label: string, v: number | null | undefined) => grField(label, `<input name="${name}" inputmode="decimal" value="${esc(v ?? '')}"${locked(name)
    ? ` disabled title="${esc(`${origins[name]?.source || 'A source'} syncs this number: change it there, or retire that source to type it`)}"` : ''}>${fresh ? '' : gcOriginHint(ch, month, name, c, origins[name])}`);
  const srcs: GrowthCohortSources = c.sources || {};
  const dlg = grDialog([ch.name, fresh ? 'New month' : grMonth(c.month)].join(' · '),
    `${fresh ? grField('Month', `<input name="month" type="month" required value="${esc(c.month)}">`) : ''}
    <div class="gr-pair">${n('spend', 'Spend', c.spend)}${n('bot_cost', 'Bot cost', c.bot_cost)}</div>
    <div class="gr-pair">${n('new_customers', 'New', c.new_customers)}${grField('Data', `<select name="quality">${grOpts([['actual', 'Actual'], ['estimate', 'Estimate']], c.quality || 'estimate', null)}</select>`)}</div>
    <div class="gr-trio">${GC_KEPT.map(([f, d]) => n(f, `Kept ${d} days %`, c[f])).join('')}</div>
    <div class="gr-pair">${n('revenue_month', 'Revenue / customer', c.revenue_month)}${n('margin_pct', 'Margin %', c.margin_pct)}</div>
    ${grField('Spend from', `<input name="src_spend" maxlength="300" value="${esc(srcs.spend || '')}">`)}
    ${grField('New from', `<input name="src_new" maxlength="300" value="${esc(srcs.new_customers || '')}">`)}`, 'Save', async form => {
      const v = (name: CohortField) => (form.elements as GrowthFormFields<CohortField>)[name].value;
      // The server merges these keys into what it keeps; an empty one removes that key.
      const sources = {...srcs, spend: v('src_spend').trim(), new_customers: v('src_new').trim()};
      const at = fresh ? v('month') : month;
      if (!/^\d{4}-\d{2}$/.test(at)) throw new Error('A month');
      const numbers: Record<string, number | null> = {};
      for (const f of GC_NUMBERS) {
        const value = grNum(v(f)), was = gcValue(c, f);
        if (!locked(f) && (fresh || value !== was)) numbers[f] = value;
      }
      await put('/v2/growth/cohorts', {channel: ch.slug, month: at, ...numbers, quality: v('quality'), sources});
      toast('Saved');
      grReload(state);
    });
  dlg.dataset.closeOnResolve = '1';   // its fields would show the number from before the choice
  grResolveWire(dlg);
}

// Add or edit a channel; editing also retires it.
async function gcChannelDialog(state: GrowthState, c: GrowthChannelRow | null = null) {
  const [sources, kp] = await Promise.all([get<{sources?: GrowthSource[]}>('/v2/growth/sources').then(r => r.sources || []).catch((): GrowthSource[] => []),
    get<{kpis?: GrowthKpi[]}>('/v2/growth/kpis').then(r => r.kpis || []).catch((): GrowthKpi[] => [])]);
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
    const el = form.elements as GrowthFormFields<ChannelField>, v = (n: ChannelField) => el[n].value.trim();
    const out: ChannelBody = {name: v('name'), track: v('track'), stage: v('stage'), owner_ref: v('owner_ref') || null, budget_month: grNum(v('budget_month')),
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
    const name = dlg.querySelector<HTMLInputElement>('[name=name]'), slug = dlg.querySelector<HTMLInputElement>('[name=slug]');
    if (name && slug) {
      name.oninput = () => { if (!slug.dataset.touched) slug.value = name.value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40); };
      slug.oninput = () => { slug.dataset.touched = '1'; };
    }
  }
  const retire = dlg.querySelector<HTMLButtonElement>('[data-retire]');
  if (retire && c) grRetireButton(retire, dlg, `/v2/growth/channels/${encodeURIComponent(c.slug)}/retire`, () => grReload(state));
}
