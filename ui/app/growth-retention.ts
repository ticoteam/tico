/* ui/app/growth-retention.ts — The Retention card on computed retention (docs/growth.md, backend/growth_computed.py):
   when a team has loaded customer facts, the card (backend/cards.py growth_retention `computed`) shows the picked
   month's cohorts with a Cut by switch (channel, segment, plan) and Weekly / Monthly, which read GET
   /api/v2/growth/computed again. A window not yet due says when it fills; a cohort under the team's minimum shows counts
   only ("12 of 19"). Each retention figure opens its customers: the rule, and the keys still customers and churned with
   the date, which the server sends only to the owner, admins and the key readers (everyone else gets the counts and the
   rule). The owner and admins upload customers here and from Settings; a refused upload names the rule and the columns.
   Classic script: it reads growth-page.ts's globals (GR, grDialog, grSeg, grUsd, grPct, grMonth, grAdmin, grReload). */
'use strict';

type GrxFigure = {value: number | null; status: string; origin: string; count?: number | null; denominator?: number; fills_on?: string; rule?: string;
  customers?: {still: string[]; churned: Array<{key: string; churned: string | null}>}};
type GrxCohort = {value: string | null; period: string; start: string; end: string; computed_from: number; too_few: boolean;
  new_customers: GrxFigure; kept_d30: GrxFigure; kept_d90: GrxFigure; kept_d180: GrxFigure; ltv: GrxFigure; cac: GrxFigure};
type GrxLoad = {source: string; customers: number; loaded_at: string};
// A channel's typed month with no customer facts (backend/cards.py typed_retention): kept beside the computed cohorts.
type GrxTyped = {value: string; new_customers: number | null; kept: Array<number | null>; origin: string};
type GrxView = {by: string; grain: string; month: string | null; min_cohort: number; computed_from: number; cohorts: GrxCohort[];
  cac_note: string | null; keys_visible: boolean; loads: GrxLoad[]; typed?: GrxTyped[]};
type GrxWindow = 'kept_d30' | 'kept_d90' | 'kept_d180';
type GrxLoaded = {customers: number; removed: number; unknown_channel: number};

const GRX_CUTS = [['channel', 'Channel'], ['segment', 'Segment'], ['plan', 'Plan']];
const GRX_GRAINS = [['week', 'Weekly'], ['month', 'Monthly']];
const GRX_WINDOWS: Array<[GrxWindow, number]> = [['kept_d30', 30], ['kept_d90', 90], ['kept_d180', 180]];
// The cut and grain picked on this page, kept while it is open; the view drawn last, which a figure opens from.
let GRX_PICK = {by: 'channel', grain: 'month'};
let GRX_SHOWN: GrxView | null = null;
// The typed channels of the month the card data carried; the API's own reads (another cut or grain) carry none.
let GRX_TYPED: {month: string | null; rows: GrxTyped[]} = {month: null, rows: []};
let GRX_READ = 0;

const grxDay = (iso: string | null | undefined) => { const [, m, d] = String(iso || '').split('-').map(Number); return m && d ? `${d} ${GR_MON[m - 1]}` : ''; };
// A cohort's name: its channel's, segment or plan; a customer with none is in "No segment" or "No plan".
function grxName(by: string, value: string | null) {
  if (by === 'channel') return value === 'unknown' || !value ? 'Unknown' : grChannelName(value, undefined);
  if (by === 'all') return 'All customers';
  return value ?? (by === 'plan' ? 'No plan' : 'No segment');
}
const grxPeriod = (grain: string, c: GrxCohort) => grain === 'week' ? `Week of ${grxDay(c.start)}` : grMonth(c.period);
function grxSaidShare(f: GrxFigure) {
  if (f.status === 'ok') return `${grPct(f.value)} (${f.count} of ${f.denominator})`;
  return `${f.count} of ${f.denominator}, too few for a rate`;
}
// A retention cell: the share (shaded by it) or the counts, each opening its customers; "not yet" with its fill date.
function grxKept(f: GrxFigure, at: number, field: GrxWindow, days: number) {
  if (f.status === 'not_yet') return `<td class="grx-not muted">not yet · ${esc(grxDay(f.fills_on))}</td>`;
  const open = (text: string, title: string) => `<button type="button" class="grx-open" data-grx-open="${at}" data-grx-field="${field}" title="${esc(title)}">${esc(text)}</button>`;
  if (f.status === 'too_few') return `<td class="grx-few">${open(`${f.count} of ${f.denominator}`, `Too few for a rate. Open the customers kept to day ${days}`)}</td>`;
  return `<td class="grc-k" style="--k:${Math.max(0, Math.min(100, Math.round(Number(f.value))))}">${open(grPct(f.value), `Open the customers kept to day ${days}`)}</td>`;
}
const GRX_SAID: Record<string, string> = {too_few: 'too few', not_yet: 'not yet', not_split: 'not split', spend_monthly: 'monthly'};
const grxMoney = (f: GrxFigure) => `<td class="grc-n${f.status === 'ok' ? '' : ' muted'}">${f.status === 'ok' && f.value != null ? esc(grUsd(f.value)) : esc(GRX_SAID[f.status] || '—')}</td>`;
// "loaded 13 Nov 06:00 from an upload in Growth": the newest load.
function grxLoaded(loads: GrxLoad[]) {
  const last = loads[0];
  if (!last) return '';
  const at = new Date(last.loaded_at);
  const when = Number.isNaN(at.valueOf()) ? '' : `${at.getDate()} ${GR_MON[at.getMonth()]} ${at.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', hour12: false})} `;
  return ` · loaded ${when}from ${last.source === 'upload' ? 'an upload in Growth' : `“${last.source}”`}`;
}
const GRX_NOTE: Record<string, string> = {
  'spend is not split by segment': 'CAC is per channel: spend is not split by segment.',
  'spend is not split by plan': 'CAC is per channel: spend is not split by plan.',
  'spend is monthly': 'Weekly CAC needs daily spend: spend is monthly.',
  'weekly CAC is daily ad spend: bot cost is monthly': 'Weekly CAC counts daily ad spend from Finance: bot cost is monthly, so it is left out.',
};

// The card's body for a computed view; a view of another cut or grain than the one picked reads the picked one.
function grxCard(v: GrxView) {
  if (v.by !== GRX_PICK.by || v.grain !== GRX_PICK.grain) { void grxRead(v.month); return `<div class="grc-ret grx" data-grx data-month="${esc(v.month || '')}" aria-busy="true">${skelRows(3, {dot: false})}</div>`; }
  GRX_SHOWN = v;
  if (v.typed) GRX_TYPED = {month: v.month, rows: v.typed};
  const typed = GRX_TYPED.month === v.month ? GRX_TYPED.rows : [], byChannelMonth = v.by === 'channel' && v.grain === 'month';
  const week = v.grain === 'week', cut = GRX_CUTS.find(c => c[0] === v.by)?.[1] || 'Cohort';
  let lastWeek = '';
  const rows = v.cohorts.map((c, i) => {
    const head = week && c.period !== lastWeek ? `<tr class="grx-wk"><th scope="rowgroup" colspan="7">${esc(grxPeriod('week', c))}</th></tr>` : '';
    lastWeek = c.period;
    return `${head}<tr data-grx-row="${esc(c.value ?? '')}"><th scope="row">${esc(grxName(v.by, c.value))}</th><td class="grc-n">${esc(grX(c.new_customers.value ?? c.computed_from, 0))}</td>
      ${GRX_WINDOWS.map(([f, days]) => grxKept(c[f], i, f, days)).join('')}${grxMoney(c.ltv)}${grxMoney(c.cac)}</tr>`;
  }).join('') + (byChannelMonth ? typed.map(grxTypedRow).join('') : '');
  const notes = [`Computed from ${grX(v.computed_from, 0)} customers${grxLoaded(v.loads)}.`];
  const typedNames = typed.map(t => grxName('channel', t.value)).join(', ');
  if (typed.length) notes.push(byChannelMonth ? 'Rows marked typed were typed by the team: those channels have no customers loaded.'
    : `Typed retention for ${typedNames} shows under Cut by Channel, Monthly.`);
  if (v.cohorts.some(c => c.too_few)) notes.push(`Under ${v.min_cohort} customers a cohort shows counts only: too few for a rate.`);
  if (v.cohorts.some(c => c.kept_d30.status === 'not_yet' || c.kept_d90.status === 'not_yet')) notes.push('A day fills once every customer in the cohort has reached it.');
  if (v.cac_note) notes.push(GRX_NOTE[v.cac_note] || v.cac_note);
  return `<div class="grc-ret grx" data-grx data-month="${esc(v.month || '')}">
    <div class="grx-bar">${grSeg('grx-by', 'Cut by', GRX_CUTS, v.by)}${grSeg('grx-grain', 'Weekly or monthly', GRX_GRAINS, v.grain)}</div>
    ${v.cohorts.length || (byChannelMonth && typed.length) ? `<table class="grc-table grc-ret-t grx-t"><thead><tr><th scope="col">${esc(cut)}</th><th scope="col" class="grc-n">New</th>
      <th scope="col" class="grc-k-h">Day 30</th><th scope="col" class="grc-k-h">Day 90</th><th scope="col" class="grc-k-h">Day 180</th><th scope="col" class="grc-n">LTV</th><th scope="col" class="grc-n">CAC</th></tr></thead>
      <tbody>${rows}</tbody></table>` : `<p class="muted dash-none">No one became a customer in ${esc(grMonth(v.month))}</p>`}
    <p class="grx-foot muted">${notes.map(esc).join(' ')}</p>
    ${grAdmin() ? '<button type="button" class="ghost small grx-up" data-grx-upload>Upload customers</button>' : ''}</div>`;
}

// A typed channel's month: its new customers and each day's typed share, shaded as computed ones are; nothing to open.
function grxTypedRow(t: GrxTyped) {
  const kept = (v: number | null | undefined) => v == null ? '<td class="grx-not muted">—</td>'
    : `<td class="grc-k" style="--k:${Math.max(0, Math.min(100, Math.round(Number(v))))}">${esc(grPct(v))}</td>`;
  return `<tr data-grx-row="${esc(t.value)}" data-origin="typed"><th scope="row">${esc(grxName('channel', t.value))} <span class="muted grx-tag">typed</span></th>
    <td class="grc-n">${t.new_customers == null ? '—' : esc(grX(t.new_customers, 0))}</td>${[0, 1, 2].map(i => kept(t.kept[i])).join('')}<td class="grc-n muted">—</td><td class="grc-n muted">—</td></tr>`;
}

// Reads the picked cut and grain for the month and draws every computed Retention card on the page again.
async function grxRead(month: string | null) {
  const read = ++GRX_READ, load = GR?.load;
  const q = new URLSearchParams({by: GRX_PICK.by, grain: GRX_PICK.grain});
  if (month) q.set('month', month);
  let html: string;
  try {
    const v = await get<GrxView>('/v2/growth/computed?' + q.toString(), {fresh: true});
    if (read !== GRX_READ || GR?.load !== load) return;
    html = grxCard({...v, month});
  } catch (e) {
    if (read !== GRX_READ) return;
    html = `<div class="grc-ret grx" data-grx data-month="${esc(month || '')}">${grFail(e)}</div>`;
  }
  document.querySelectorAll<HTMLElement>('#dash-grid [data-grx]').forEach(el => { el.outerHTML = html; });
}

// A figure opened: its rule and its customers, read for its one period.
async function grxOpen(at: number, field: GrxWindow) {
  const v = GRX_SHOWN, c = v?.cohorts[at];
  if (!v || !c) return;
  const days = GRX_WINDOWS.find(w => w[0] === field)?.[1] || 30;
  const q = new URLSearchParams({by: v.by, customers: 'true'});
  q.set(v.grain === 'week' ? 'week' : 'month', c.period);
  let figure: GrxFigure | undefined, keys = false;
  try {
    const found = await get<GrxView>('/v2/growth/computed?' + q.toString(), {fresh: true});
    figure = found.cohorts.find(x => x.value === c.value && x.period === c.period)?.[field];
    keys = found.keys_visible;
  } catch (e) { toast(errorMessage(e, 'Did not load'), true); return; }
  if (!figure) { toast('Those customers changed: read the card again', true); return; }
  const kept = figure.count ?? 0, gone = (figure.denominator ?? 0) - kept;
  const rule = figure.rule ? figure.rule[0]!.toUpperCase() + figure.rule.slice(1) + '.' : '';
  const list = (items: string[]) => `<ul class="grx-keys">${items.map(k => `<li>${k}</li>`).join('')}</ul>`;
  const body = keys && figure.customers
    ? `<section class="gr-sec"><h3 class="gr-k">Still customers (${esc(kept)})</h3>${list(figure.customers.still.map(k => `<code>${esc(k)}</code>`))}</section>
      <section class="gr-sec"><h3 class="gr-k">Churned by day ${days} (${esc(gone)})</h3>${list(figure.customers.churned.map(k => `<code>${esc(k.key)}</code> <span class="muted">${esc(grxDay(k.churned))}</span>`))}</section>
      <p class="muted gr-note">Customer keys only, as the team's source gives them. Nothing else about the person.</p>`
    : `<p>Still customers: ${esc(kept)}. Churned by day ${days}: ${esc(gone)}.</p>
      <p class="muted gr-note">Customer keys show only to the owner, admins and the people they name.</p>`;
  grDialog(`${grxName(v.by, c.value)} · ${grxPeriod(v.grain, c)} · day ${days}: ${grxSaidShare(figure)}`,
    `<p class="grx-rule">${esc(rule)}</p>${body}`, null, null).classList.add('grx-dlg');
}

// Upload customers (owner and admins): a CSV of the allowed columns. A refused file loads nothing and says why.
function grxUpload() {
  const dlg = grDialog('Upload customers', `<p class="gr-note">One row per customer. Required: customer key, became customer, channel.
      Also kept: signed up, segment, plan, churned, revenue per month. Any other column is refused.</p>
    <p class="muted gr-note">A customer key is the team's own id, never an email or phone number; a team may tokenise keys before
      uploading. Each upload replaces the customers of the one before, so a customer left out is deleted.</p>
    ${grField('CSV file', '<input type="file" name="file" accept=".csv,text/csv" required>')}
    <div class="grx-refused" data-grx-refused role="alert" hidden></div>`, 'Upload', async form => {
    const input = form.querySelector<HTMLInputElement>('input[name="file"]'), file = input?.files?.[0];
    const refused = form.querySelector<HTMLElement>('[data-grx-refused]');
    if (!file) throw new Error('Choose a CSV file');
    if (refused) refused.hidden = true;
    let done: GrxLoaded;
    try { done = await post('/v2/growth/customers', {csv: await file.text()}) as GrxLoaded; }
    catch (e) {
      const said = errorMessage(e, 'Nothing loaded.');
      if (!refused || !/^Nothing loaded/.test(said)) throw e;
      refused.innerHTML = `<b>Nothing loaded</b><span>${esc(said.replace(/^Nothing loaded[.:]?\s*/, ''))}</span>`;
      refused.hidden = false;
      return false;
    }
    toast(`${grX(done.customers, 0)} customers loaded${done.unknown_channel ? ` · ${grX(done.unknown_channel, 0)} in Unknown: their channel is not one of Growth's` : ''}`);
    grReload(GR);
    return true;
  });
  dlg.classList.add('grx-dlg');
}

// The card's switches, its figures and its upload, for every computed Retention card the grid draws.
document.addEventListener('click', ev => {
  const t = ev.target instanceof Element ? ev.target : null, card = t?.closest<HTMLElement>('[data-grx]');
  if (!t || !card) return;
  const seg = t.closest<HTMLButtonElement>('[data-gr-seg] button'), name = seg?.closest<HTMLElement>('[data-gr-seg]')?.dataset.grSeg;
  if (seg && (name === 'grx-by' || name === 'grx-grain')) {
    const v = seg.dataset.v || '';
    GRX_PICK = name === 'grx-by' ? {...GRX_PICK, by: v} : {...GRX_PICK, grain: v};
    void grxRead(card.dataset.month || null);
    return;
  }
  const open = t.closest<HTMLButtonElement>('[data-grx-open]');
  if (open) { void grxOpen(Number(open.dataset.grxOpen), (open.dataset.grxField || 'kept_d30') as GrxWindow); return; }
  if (t.closest('[data-grx-upload]')) grxUpload();
});
