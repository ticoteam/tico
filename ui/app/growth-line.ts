/* ui/app/growth-line.ts — The Growth line (docs/growth.md "The Growth line", backend/growth_line.py): one block on the
   Growth page, under Get started and over the cards, that reads the picked month left to right: each source and its
   health, the channels it fills, each channel's month numbers and unit economics, and the KPIs they move. One sentence
   over it says the month. At rest it is plain rows, one per channel, its sources' chips on the left and its KPIs' on the
   right; selecting a source, a channel or a KPI draws gray links for that selection only and fades the rest. Any number
   opens, under its own row, where it comes from. An agent reads the same `line` from GET /api/v2/growth/numbers.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

type GrlHealth = 'fresh' | 'stale' | 'failing' | 'waiting' | 'not_synced' | null;
interface GrlSource { id: string; name: string; kind: string | null; health: GrlHealth; since: string | null; error: string | null; fills: Array<{channel: string; number: string}> }
interface GrlCell {
  value: number | null; says: string | null; origin: 'synced' | 'typed' | 'computed' | null; quality: string | null; sources: string[]; source_names: string[];
  synced_at: string | null; stale: boolean; status: string | null; computed_from: number | null; conflict: boolean;
  budget?: number | null; over_budget?: number | null; budget_says?: string | null;
}
interface GrlEcon { value: number | null; says: string | null; formula: string; trace: string | null; stale: boolean; status: string | null }
interface GrlGuard { kpi_id: string; kpi_name: string; value: number | null; max: number; breached: boolean; over: number | null; says: string | null }
interface GrlChannel {
  slug: string; name: string; owner_ref: string | null; sources: string[];
  numbers: Record<string, GrlCell>; economics: Record<string, GrlEcon>; input_kpi: string | null; guardrail: GrlGuard | null;
}
interface GrlKpi { id: string; name: string; unit: string; role: string | null; value: number | null; says: string | null; target: number | null; moved_by: string[]; held_by: string[]; breached: boolean; over: number | null }
interface GrlLine { month: string; sources: GrlSource[]; channels: GrlChannel[]; kpis: GrlKpi[]; headline: {sentence: string} }
interface GrlNumbers { line: GrlLine | null; channels?: Array<{slug: string; series: Array<Record<string, unknown>>}>; series?: Array<Record<string, unknown>> }
interface GrlState { dash: DashState; seq: number; view: unknown; line: GrlLine | null; history: GrlNumbers | null; pick: string; open: string }

let GRL: GrlState | null = null;
let GRL_RESIZE = false;   // the one window listener that redraws a selection's links
const GRL_USED = new Set<string>();   // a use is told to the server once per page load (it counts once a day)
const GRL_NUMBERS: Array<[string, string]> = [['spend', 'Spend'], ['new_customers', 'New'], ['kept_d30', 'Kept 30d']];
const GRL_ECON: Array<[string, string]> = [['cac', 'CAC'], ['ltv_cac', 'LTV:CAC'], ['payback_months', 'Payback']];
const GRL_WORD: Record<string, string> = {spend: 'spend', bot_cost: 'bot cost', new_customers: 'new customers', kept_d30: 'kept to day 30',
  cac: 'CAC', ltv_cac: 'LTV:CAC', payback_months: 'payback'};
const grlBad = (h: GrlHealth) => h === 'stale' || h === 'failing';
// A date on the line, as the server writes it in sentences ("11 Oct"): one form for "since" everywhere.
const grlDay = (iso: string | null) => { if (!iso) return ''; const d = new Date(iso); return Number.isNaN(d.valueOf()) ? '' : `${d.getUTCDate()} ${GR_MON[d.getUTCMonth()]}`; };
const grlHealthText = (s: GrlSource) => s.health === 'stale' ? `Stale since ${grlDay(s.since)}` : s.health === 'failing' ? `Failing${s.error ? `: ${s.error}` : ''}`
  : s.health === 'fresh' ? 'Fresh' : s.health === 'waiting' ? 'Waiting for its first sync' : s.kind === 'typed' ? '' : s.kind === 'upload' ? '' : 'Not synced';

// Mounted once per Growth page, right over the cards (the Get started panel stays over it).
function grLineMount(dash: DashState, root: HTMLElement) {
  const grid = root.querySelector('#dash-grid');
  if (!grid) return;   // G: the dashboard draws its grid before a place is wired
  const box = document.createElement('section');
  box.className = 'grl'; box.hidden = true; box.setAttribute('aria-labelledby', 'grl-h'); box.dataset.grLine = '';
  grid.before(box);
  GRL = {dash, seq: 0, view: null, line: null, history: null, pick: '', open: ''};
  box.addEventListener('click', ev => {
    const t = eventElement(ev);
    const pick = t?.closest<HTMLElement>('[data-grl-pick]');
    if (pick) { grlPick(pick.dataset.grlPick || ''); return; }
    const num = t?.closest<HTMLElement>('[data-grl-trace]');
    if (num) { grlTrace(num.dataset.grlTrace || ''); return; }
    const link = t?.closest<HTMLElement>('[data-grl-link-kpi]');
    if (link) { void grlLinkKpi(link.dataset.grlLinkKpi || ''); return; }
    const who = t?.closest<HTMLElement>('[data-grl-customers]');
    if (who) grlCustomers(who.dataset.grlCustomers || '');
  });
  if (!GRL_RESIZE) { GRL_RESIZE = true; window.addEventListener('resize', () => { if (GRL?.pick) grlLinks(); }); }   // once a session
}

// Each time the cards draw (a month picked, a save): the line for that month, with six months behind it for the trace.
async function grLineRead(dash: DashState) {
  const st = GRL;
  if (!st || st.dash !== dash) return;
  // Once per view the server sends (a month picked, a save, a refresh), not on every redraw of the same one.
  if (st.view === dash.view && st.line) { grlDraw(); return; }
  st.view = dash.view;
  const seq = ++st.seq, month = dash.view?.month || '';
  let data: GrlNumbers;
  try { data = await get<GrlNumbers>(`/v2/growth/numbers?months=6${month ? `&month=${encodeURIComponent(month)}` : ''}`, {fresh: true}); }
  catch { return; }   // the cards still show; no line rather than a wrong one
  if (GRL !== st || seq !== st.seq) return;
  st.line = data.line; st.history = data;
  grlDraw();
}

function grlDraw() {
  const st = GRL, box = st?.dash.root?.querySelector<HTMLElement>('[data-gr-line]');
  if (!st || !box) return;
  const ln = st.line;
  box.hidden = !ln;
  // The headline says what the stale-sources banner said, so the banner gives way while the line shows.
  const banner = st.dash.root?.querySelector<HTMLElement>('[data-gr-health]');
  if (banner && ln) banner.hidden = true;
  if (!ln) { box.innerHTML = ''; return; }
  const src = new Map(ln.sources.map(s => [s.id, s]));
  const kpi = new Map(ln.kpis.map(k => [k.id, k]));
  const chip = (id: string) => {
    const s = src.get(id);
    if (!s) return '';
    return `<button type="button" class="grl-chip${grlBad(s.health) ? ' grl-bad' : ''}" data-grl-pick="source:${esc(id)}" data-grl-src="${esc(id)}" aria-pressed="false">${esc(s.name)}${grlBad(s.health) ? `<span class="grl-chip-h">${esc(grlHealthText(s))}</span>` : ''}</button>`;
  };
  const mark = (c: GrlCell) => c.value == null ? '' : `<span class="grl-mark${c.stale ? ' grl-stale' : ''}">${esc(c.stale ? 'stale' : c.origin === 'synced' ? 'synced' : c.origin === 'computed' ? 'computed' : c.quality === 'estimate' ? 'estimate' : 'typed')}</span>`;
  const value = (c: {value: number | null; says: string | null; status: string | null}) => c.value != null ? esc(c.says || '') : c.status === 'not_yet' ? '<span class="muted">not yet</span>' : c.status === 'too_few' ? '<span class="muted">too few</span>' : '<span class="muted">—</span>';
  const num = (ch: GrlChannel, key: string, label: string) => {
    const c = ch.numbers[key];
    if (!c) return '';
    const bar = key === 'spend' && c.value != null && c.budget ? (() => {
      const w = Math.min(100, c.value / Math.max(c.value, c.budget) * 100), tick = Math.min(100, c.budget / Math.max(c.value, c.budget) * 100);
      return `<span class="grl-bar" aria-hidden="true"><i style="width:${w.toFixed(1)}%"></i><b style="left:${tick.toFixed(1)}%"></b></span>`;
    })() : '';
    const over = c.budget_says ? `<span class="grl-over">${esc(c.budget_says)}</span>` : '';
    return `<button type="button" class="grl-num" data-grl-trace="${esc(ch.slug)}:${key}" aria-expanded="false"><span class="grl-k">${esc(label)}</span><span class="grl-v">${value(c)}</span>${bar}${over}${mark(c)}</button>`;
  };
  const eco = (ch: GrlChannel, key: string, label: string) => {
    const e = ch.economics[key];
    if (!e) return '';
    return `<button type="button" class="grl-num" data-grl-trace="${esc(ch.slug)}:${key}" aria-expanded="false"${e.value == null && !e.status ? ' disabled' : ''}><span class="grl-k">${esc(label)}</span><span class="grl-v">${value(e)}</span>${e.stale ? '<span class="grl-mark grl-stale">stale</span>' : ''}</button>`;
  };
  const kpiChip = (id: string, held: GrlGuard | null) => {
    const k = kpi.get(id);
    if (!k) return '';
    return `<button type="button" class="grl-chip grl-kchip" data-grl-pick="kpi:${esc(id)}" data-grl-kpi="${esc(id)}" aria-pressed="false">${held ? 'Held to ' : 'Moves '}${esc(k.name)}${held?.says ? `<span class="grl-over">${esc(held.says)}</span>` : ''}</button>`;
  };
  const rows = ln.channels.map(ch => {
    const named = ch.slug !== 'unknown' && ch.slug !== 'all';
    const kpis = [ch.input_kpi ? kpiChip(ch.input_kpi, null) : named ? (grAdmin() ? `<button type="button" class="linkish grl-nolink" data-grl-link-kpi="${esc(ch.slug)}">No KPI linked</button>` : '<span class="muted grl-nolink">No KPI linked</span>') : '',
      ch.guardrail ? kpiChip(ch.guardrail.kpi_id, ch.guardrail) : ''].join('');
    // Read aloud as text: which sources fill it and which KPIs it moves (the links are pictures only).
    const said = `${ch.name}: from ${ch.sources.map(id => src.get(id)?.name).filter(Boolean).join(', ') || 'no source'}${ch.input_kpi && kpi.get(ch.input_kpi) ? `, moves ${kpi.get(ch.input_kpi)?.name}` : ''}`;
    return `<li class="grl-row${ch.slug === 'all' ? ' grl-all' : ''}" data-grl-row="${esc(ch.slug)}" data-grl-srcs="${esc(ch.sources.join(' '))}" data-grl-kpis="${esc([ch.input_kpi, ch.guardrail?.kpi_id].filter(Boolean).join(' '))}">
      <div class="grl-cells"><span class="grl-c grl-c-src">${ch.sources.map(chip).join('')}</span>
      <span class="grl-c grl-c-ch">${named ? `<button type="button" class="grl-name" data-grl-pick="channel:${esc(ch.slug)}" aria-pressed="false" aria-label="${esc(said)}">${esc(ch.name)}</button>` : `<span class="grl-name">${esc(ch.name)}</span>`}${ch.owner_ref ? `<span class="muted grl-owner">${esc(grWho(ch.owner_ref))}</span>` : ''}</span>
      <span class="grl-c grl-c-num">${GRL_NUMBERS.map(([k, l]) => num(ch, k, l)).join('')}</span>
      <span class="grl-c grl-c-eco">${GRL_ECON.map(([k, l]) => eco(ch, k, l)).join('')}</span>
      <span class="grl-c grl-c-kpi">${kpis}</span></div>
      ${named ? `<p class="grl-phone muted">${esc(`From ${ch.sources.map(id => src.get(id)?.name).filter(Boolean).join(', ') || 'no source'}${ch.input_kpi && kpi.get(ch.input_kpi) ? ` → moves ${kpi.get(ch.input_kpi)?.name}` : ''}`)}</p>` : ''}
      <div class="grl-trace" data-grl-trace-for="${esc(ch.slug)}" hidden></div></li>`;
  }).join('');
  const sources = ln.sources.map(s => `<button type="button" class="grl-chip${grlBad(s.health) ? ' grl-bad' : ''}" data-grl-pick="source:${esc(s.id)}" data-grl-src="${esc(s.id)}" data-grl-strip aria-pressed="false">${esc(s.name)}${grlHealthText(s) ? `<span class="grl-chip-h">${esc(grlHealthText(s))}</span>` : ''}</button>`).join('');
  const kpis = ln.kpis.map(k => `<button type="button" class="grl-chip grl-kchip${k.breached ? ' grl-bad' : ''}" data-grl-pick="kpi:${esc(k.id)}" data-grl-kpi="${esc(k.id)}" data-grl-strip aria-pressed="false">${k.role === 'north_star' ? '★ ' : ''}${esc(k.name)} <span class="grl-v">${esc(k.says || '—')}</span>${k.target != null ? `<span class="muted"> of ${esc(kpiNum(k.target, k.unit))}</span>` : ''}${k.over != null ? `<span class="grl-over">${esc(kpiNum(k.over, k.unit))} over guardrail</span>` : ''}</button>`).join('');
  box.innerHTML = `<div class="grl-head"><h2 id="grl-h" class="grl-title">The Growth line</h2><span class="muted">${esc(grMonth(ln.month))}</span></div>
    ${ln.headline.sentence ? `<p class="grl-headline" data-grl-headline>${esc(ln.headline.sentence)}</p>` : ''}
    <div class="grl-frame" data-grl-frame>
      <div class="grl-strip"><span class="grl-label">Sources</span>${sources}</div>
      <div class="grl-cols" aria-hidden="true"><span>Sources</span><span>Channels</span><span>This month</span><span>Unit economics</span><span>KPIs</span></div>
      <ol class="grl-rows">${rows}</ol>
      <div class="grl-strip"><span class="grl-label">KPIs</span>${kpis || '<span class="muted">No KPI linked yet</span>'}</div>
      <svg class="grl-links" aria-hidden="true" focusable="false"></svg>
    </div>`;
  if (st.pick) grlPick(st.pick, true);
  if (st.open) { const was = st.open; st.open = ''; grlTrace(was, true); }
}

// Select a source, a channel or a KPI: its rows stay, the rest fade, and links join them. Again clears it.
function grlPick(key: string, again = false) {
  const st = GRL, box = st?.dash.root?.querySelector<HTMLElement>('[data-gr-line]');
  if (!st || !box || !st.line) return;
  st.pick = !again && st.pick === key ? '' : key;
  const [kind, id] = st.pick.split(/:(.*)/s);
  if (kind === 'source' && !again) grlUse('source');
  box.classList.toggle('grl-picking', !!st.pick);
  box.querySelectorAll<HTMLElement>('[data-grl-pick]').forEach(b => { b.setAttribute('aria-pressed', String(b.dataset.grlPick === st.pick)); });
  box.querySelectorAll<HTMLElement>('[data-grl-row]').forEach(row => {
    const srcs = (row.dataset.grlSrcs || '').split(' '), kpis = (row.dataset.grlKpis || '').split(' ');
    const on = !st.pick || (kind === 'source' && srcs.includes(id || '')) || (kind === 'kpi' && kpis.includes(id || '')) || (kind === 'channel' && row.dataset.grlRow === id);
    row.classList.toggle('grl-on', !!st.pick && on);
    row.classList.toggle('grl-off', !!st.pick && !on);
  });
  grlLinks();
}

// The links for the selection only: from each source chip in the strip to the rows it fills, and from each row to the
// KPIs it moves. Gray; dashed from a stale or failing source. Under 600 px the rows stack and no link is drawn.
function grlLinks() {
  const st = GRL, frame = st?.dash.root?.querySelector<HTMLElement>('[data-grl-frame]'), svg = frame?.querySelector<SVGSVGElement>('.grl-links');
  if (!st || !frame || !svg) return;
  svg.innerHTML = '';
  if (!st.pick || !st.line || window.matchMedia('(max-width: 600px)').matches) return;
  const box = frame.getBoundingClientRect();
  svg.setAttribute('width', String(box.width)); svg.setAttribute('height', String(box.height));
  const at = (el: Element, side: 'top' | 'bottom' | 'left' | 'right') => {
    const r = el.getBoundingClientRect();
    const x = side === 'left' ? r.left : side === 'right' ? r.right : r.left + r.width / 2;
    const y = side === 'top' ? r.top : side === 'bottom' ? r.bottom : r.top + r.height / 2;
    return [x - box.left, y - box.top];
  };
  // Links run along a gutter at each edge of the frame, never across the rows: a source's from its chip in the strip
  // down the left gutter into the row's chip; a KPI's from the row's chip out to the right gutter and down to the strip.
  const d = (pts: number[][], dashed: boolean) => `<path d="${pts.map(([x = 0, y = 0], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join('')}"${dashed ? ' class="grl-dash"' : ''}/>`;
  const left = 5, right = box.width - 5;
  const bad = new Set(st.line.sources.filter(s => grlBad(s.health)).map(s => s.id));
  let out = '';
  frame.querySelectorAll<HTMLElement>('.grl-row.grl-on').forEach(row => {
    for (const id of (row.dataset.grlSrcs || '').split(' ').filter(Boolean)) {
      const from = frame.querySelector(`[data-grl-strip][data-grl-src="${CSS.escape(id)}"]`), to = row.querySelector(`.grl-c-src [data-grl-src="${CSS.escape(id)}"]`);
      if (!from || !to) continue;
      const [x1 = 0, y1 = 0] = at(from, 'bottom'), [x2 = 0, y2 = 0] = at(to, 'left');
      out += d([[x1, y1], [x1, y1 + 6], [left, y1 + 6], [left, y2], [x2, y2]], bad.has(id));
    }
    for (const id of (row.dataset.grlKpis || '').split(' ').filter(Boolean)) {
      const from = row.querySelector(`.grl-c-kpi [data-grl-kpi="${CSS.escape(id)}"]`), to = frame.querySelector(`[data-grl-strip][data-grl-kpi="${CSS.escape(id)}"]`);
      if (!from || !to) continue;
      const [x1 = 0, y1 = 0] = at(from, 'right'), [x2 = 0, y2 = 0] = at(to, 'top');
      out += d([[x1, y1], [right, y1], [right, y2 - 6], [x2, y2 - 6], [x2, y2]], false);
    }
  });
  svg.innerHTML = out;
}

// "Where this number comes from", under its own row: the number as a sum with today's values, each input naming its
// source, then the KPI it moves and its last six months. Again closes it.
function grlTrace(key: string, again = false) {
  const st = GRL, box = st?.dash.root?.querySelector<HTMLElement>('[data-gr-line]');
  if (!st || !box || !st.line) return;
  const [slug = '', field = ''] = key.split(':');
  const ch = st.line.channels.find(c => c.slug === slug);
  box.querySelectorAll<HTMLElement>('.grl-trace').forEach(t => { t.hidden = true; t.innerHTML = ''; });
  box.querySelectorAll<HTMLElement>('[data-grl-trace]').forEach(b => { b.setAttribute('aria-expanded', 'false'); });
  if ((!again && st.open === key) || !ch) { st.open = ''; return; }
  st.open = key;
  if (!again) grlUse('trace');
  const panel = box.querySelector<HTMLElement>(`[data-grl-trace-for="${CSS.escape(slug)}"]`);
  box.querySelector<HTMLElement>(`[data-grl-trace="${CSS.escape(key)}"]`)?.setAttribute('aria-expanded', 'true');
  if (!panel) return;
  const kpi = st.line.kpis.find(k => k.id === ch.input_kpi);
  const e = ch.economics[field], c = ch.numbers[field];
  let said = '';
  if (e) said = e.trace || (e.status === 'not_yet' ? `Not yet: ${GRL_WORD[field]} needs kept to day 30, which comes due 30 days after the month ends.` : `No ${GRL_WORD[field]} for ${grMonth(st.line.month)}: it is ${e.formula}.`);
  else if (c) {
    const where = c.source_names.join(', ') || 'no source';
    said = c.value == null ? (c.status === 'not_yet' ? 'Not yet: this month\'s customers are under 30 days old.' : `No ${GRL_WORD[field]} for ${grMonth(st.line.month)}.`)
      : `${c.says} ${GRL_WORD[field]} from ${where}${c.origin === 'synced' && c.synced_at ? `, synced ${grlDay(c.synced_at)}` : ''}${c.origin === 'computed' ? `, computed from ${grX(c.computed_from || 0, 0)} customers` : ''}${c.stale ? ', stale' : ''}${c.quality === 'estimate' ? ', an estimate' : ''}.${c.budget_says ? ` ${c.budget_says} (budget ${grUsd(c.budget)}).` : ''}`;
  }
  const hist = slug === 'all' ? st.history?.series : st.history?.channels?.find(x => x.slug === slug)?.series;
  const values = (hist || []).map(p => { const v = p[field]; return typeof v === 'number' ? v : null; });
  panel.innerHTML = `<p class="grl-said">${esc(said)}</p>${kpi ? `<p class="muted">Moves ${esc(kpi.name)}${kpi.says ? `, now ${esc(kpi.says)}` : ''}.</p>` : ''}
    ${c?.origin === 'computed' ? `<button type="button" class="linkish" data-grl-customers="${esc(slug)}">Open its customers</button>` : ''}
    ${values.length > 1 ? `<div class="grl-spark" aria-label="${esc(`Last ${values.length} months`)}">${grSpark(values, values.length - 1, {w: 160, h: 28})}</div>` : ''}`;
  panel.hidden = false;
}

// A use, told once per page load; the server counts each person or bot once a day.
function grlUse(use: string) {
  if (GRL_USED.has(use)) return;
  GRL_USED.add(use);
  post('/v2/growth/line/used', {use}).catch(() => GRL_USED.delete(use));
}

// "No KPI linked", for whoever may edit the channel: its edit dialog, where Moves picks the KPI.
async function grlLinkKpi(slug: string) {
  if (!GR) return;
  try { await gcChannelDialog(GR, await get<GrowthChannelRow>(`/v2/growth/channels/${encodeURIComponent(slug)}`, {fresh: true})); }
  catch (e) { toast(errorMessage(e, 'Did not open'), true); }
}

// A computed number's customers are the Retention card's to show: open that row's day-30 customers there.
function grlCustomers(slug: string) {
  const grid = GRL?.dash.root?.querySelector('#dash-grid');
  const open = grid?.querySelector<HTMLElement>(`[data-grx-row="${CSS.escape(slug)}"] [data-grx-field="kept_d30"]`) || grid?.querySelector<HTMLElement>('[data-grx]');
  open?.scrollIntoView({block: 'center'});
  if (open?.matches('[data-grx-field]')) open.click();
}
