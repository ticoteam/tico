/* ui/app/task-stats.ts — Task stats: how tasks moved through their statuses or steps over time
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** A stage on offer: its key and its label. */
type FlowStage = [string, string];
/** A chart unit: id, button label, bars shown, buckets in the moving average. */
type FlowUnitRow = [string, string, number, number];
/** One day's row in GET /v2/tasks/flow: the day, then a count per stage. */
interface FlowDay { day: string; [stage: string]: unknown }
/** One person's row in By owner: the actor and the counts. */
interface FlowPerson { actor: string; created?: number; [stage: string]: unknown }
/** A stage's time: the median and the 90th percentile in hours, over n tasks. */
interface FlowSpan { n: number; median_hours: number | null; p90_hours: number | null }
/** GET /v2/tasks/flow: the stage totals per day, the spans, the people, and a type's steps. */
interface FlowRead {
  days?: FlowDay[];
  totals?: Record<string, number>;
  steps?: Array<{ id: string; name: string }>;
  stages?: Record<string, FlowSpan | undefined>;
  people?: FlowPerson[];
}
/** The task list's state as this dialog reads it (task-list.ts passes it on). */
interface FlowListState {
  type?: string;
  labels?: Array<string | { key?: string; name?: string }>;
  filters?: { owner?: string[] };
}
/** A chart bucket before its counts: its span of local days and its label. */
interface FlowSlot { start: string; end: string; label: string; short: string; from: Date }
/** A chart bucket with its count of the stage, its moving average, and whether it is the running one. */
interface FlowBucket extends FlowSlot { n: number; none: boolean; running: boolean; avg: number | null }
/** The dialog's state (FLOW_ST): the filters, the stage and unit picked, and the reads. */
interface FlowState {
  days: number; type: string; owner: string; kind: string; label: string; recurring: string; stage: string; unit: string; who: string;
  data: FlowRead | null; prev: FlowRead | null; chart: FlowRead | null; seq: number; chartSeq: number;
  cache: Map<string, Promise<FlowRead | null>>;
  [field: string]: unknown;
}

// Read from the status and step changes the hub already records (`/v2/tasks/flow`); nothing is
// snapshotted. A tile picks the stage charted; the filters narrow every number at once.
const FLOW_STAGES: FlowStage[] = [['created', 'New'], ['doing', 'Started'], ['waiting', 'Waiting'], ['review', 'In review'],
  ['ready', 'Ready'], ['done', 'Done'], ['closed', 'Closed'], ['declined', 'Declined'], ['open', 'Reopened']];
const FLOW_ALWAYS = ['created', 'doing', 'waiting', 'done', 'closed'];
const FLOW_SPAN_WORD: Record<string, string> = {open: 'Before start', doing: 'Started', waiting: 'Waiting', review: 'In review',
  ready: 'Ready', done: 'Done, before close', declined: 'Declined'};
const flowHours = (h: number | null | undefined) => h == null ? '' : h < 1 / 60 ? '<1m' : h < 1 ? `${Math.round(h * 60)}m`
  : h < 48 ? `${h.toFixed(h < 10 ? 1 : 0)}h` : `${(h / 24).toFixed(1)}d`;
const flowDay = (day: string) => new Date(day + 'T12:00:00').toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
const flowLabelKey = (l: string | { key?: string; name?: string } | undefined) => typeof l === 'string' ? l : l?.key || l?.name || '';
const FLOW_RANGES: [number, string][] = [[7, '7d'], [30, '30d'], [90, '90d'], [365, '1y']];
// The chart's buckets: id, button, bars shown, buckets in the moving average.
const FLOW_UNITS: [FlowUnitRow, FlowUnitRow, FlowUnitRow] = [['day', 'Days', 14, 7], ['week', 'Weeks', 12, 4], ['month', 'Months', 12, 3]];
const FLOW_MAX_DAYS = 365;   // the most /v2/tasks/flow reads back
const flowKey = (at: Date) => `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, '0')}-${String(at.getDate()).padStart(2, '0')}`;
const flowToday = () => { const n = new Date(); return new Date(n.getFullYear(), n.getMonth(), n.getDate()); };
const flowAgo = (from: Date, to: Date) => Math.round((to.getTime() - from.getTime()) / 864e5);
// The last stage on offer: the list is never empty (the statuses that always show are on it).
const flowLast = (list: FlowStage[]): FlowStage => list[list.length - 1] ?? ['created', 'New'];
let FLOW_ST: FlowState | null = null;

function taskStatsOpen(tasks: FlowListState | null | undefined): void {
  let d: HTMLDialogElement | null = $('#task-stats-dialog');
  if (!d) {
    d = document.createElement('dialog');
    d.id = 'task-stats-dialog'; d.className = 'tmodal task-stats'; d.setAttribute('aria-label', 'Task stats');
    document.body.appendChild(d);
    d.addEventListener('close', () => { FLOW_ST = null; });
  }
  const owners = tasks?.filters?.owner || [];
  const labels = [...new Set((tasks?.labels || []).map(flowLabelKey).filter(Boolean))];
  const st: FlowState = FLOW_ST = {days: 30, type: tasks?.type && tasks.type !== 'general' ? tasks.type : '',
    owner: owners.length === 1 ? owners[0] ?? '' : '', kind: '', label: '', recurring: '', stage: 'done', unit: 'week', who: '',
    data: null, prev: null, chart: null, seq: 0, chartSeq: 0, cache: new Map<string, Promise<FlowRead | null>>()};
  try {
    const saved: {days?: number; stage?: string; unit?: string} = JSON.parse(localStorage.getItem('tico.tasks.stats') || '{}');
    if (FLOW_RANGES.some(([n]) => n === saved.days)) st.days = Number(saved.days);
    if (saved.stage) st.stage = saved.stage;
    if (FLOW_UNITS.some(([u]) => u === saved.unit)) st.unit = String(saved.unit);
  } catch {}
  const types = (TASK_TYPES || []).filter(t => t.id !== 'general');
  d.innerHTML = `<div class="tmodal-head"><strong class="who">Task stats</strong><span class="spacer"></span>
      <button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></div>
    <div class="flow-filters">
      <div class="flow-days" data-flow-days role="group" aria-label="Range">${FLOW_RANGES.map(([n, label]) =>
        `<button type="button" data-days="${n}">${label}</button>`).join('')}</div>
      ${types.length ? `<select data-flow="type" aria-label="Type"><option value="">Statuses</option>${types.map(t =>
        `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('')}</select>` : ''}
      <select data-flow="owner" aria-label="Owner"><option value="">Anyone</option>${taskOwnerOptions('').replace('<option value="">Who is this for?</option>', '')}</select>
      <select data-flow="kind" aria-label="Owner kind"><option value="">Bots and humans</option><option value="bot">Bots</option><option value="human">Humans</option></select>
      ${labels.length ? `<select data-flow="label" aria-label="Label"><option value="">Any label</option>${labels.map(l =>
        `<option value="${esc(l)}">${esc(l)}</option>`).join('')}</select>` : ''}
      <select data-flow="recurring" aria-label="Recurring"><option value="">Any task</option><option value="false">One-off</option><option value="true">Recurring</option></select>
    </div>
    <div class="tmodal-body" data-flow-body><div class="empty">Loading…</div></div>`;
  const close = d.querySelector<HTMLButtonElement>('[data-close]');
  if (!close) return;
  close.onclick = () => d?.close();
  for (const sel of d.querySelectorAll<HTMLSelectElement>('select[data-flow]')) {
    const key = sel.dataset.flow ?? '';
    sel.value = String(st[key] || '');
    if (sel.value !== (st[key] || '')) sel.value = '';
    st[key] = sel.value;
    sel.onchange = () => { st[key] = sel.value; taskStatsLoad(); };
  }
  const daysBar = d.querySelector<HTMLElement>('[data-flow-days]');
  if (!daysBar) return;
  daysBar.onclick = ev => {
    if (!(ev.target instanceof Element)) return;
    const b = ev.target.closest<HTMLElement>('[data-days]'); if (!b) return;
    st.days = Number(b.dataset.days); taskStatsRemember(); taskStatsLoad();
  };
  if (!d.open) d.showModal();
  taskStatsLoad();
}
function taskStatsRemember(): void {
  const st = FLOW_ST;
  if (!st) return;
  try { localStorage.setItem('tico.tasks.stats', JSON.stringify({days: st.days, stage: st.stage, unit: st.unit})); } catch {}
}
// The filters as a query; `owner` overrides the dialog's owner (the chart's person).
function flowQuery(st: FlowState, days: number, owner: string = st.owner): string {
  const q = new URLSearchParams({days: String(days), tz: String(-new Date().getTimezoneOffset())});
  if (st.type) q.set('type', st.type);
  if (owner) q.set('owner', owner);
  if (st.kind) q.set('owner_kind', st.kind);
  if (st.label) q.set('label', st.label);
  if (st.recurring) q.set('recurring', st.recurring);
  return '/v2/tasks/flow?' + q;
}
// Reads are kept while the dialog is open, so switching ranges, buckets or people back is instant.
function flowGet(st: FlowState, path: string): Promise<FlowRead | null> {
  let hit = st.cache.get(path);
  if (!hit) {
    hit = v2Get(path).then((r: FlowRead | null) => { if (!r) st.cache.delete(path); return r; });
    st.cache.set(path, hit);
  }
  return hit;
}
async function taskStatsLoad(): Promise<void> {
  const st = FLOW_ST, d: HTMLDialogElement | null = $('#task-stats-dialog'); if (!st || !d) return;
  for (const b of d.querySelectorAll<HTMLElement>('[data-days]')) {
    const on = Number(b.dataset.days) === st.days;
    b.classList.toggle('cur', on); b.setAttribute('aria-pressed', String(on));
  }
  const seq = ++st.seq;
  // The tiles compare with the equal period before: twice the range read once, minus this range. A year has no
  // previous one to read.
  const twice = st.days * 2 <= FLOW_MAX_DAYS ? st.days * 2 : 0;
  const [data, prev, chart] = await Promise.all([flowGet(st, flowQuery(st, st.days)),
    twice ? flowGet(st, flowQuery(st, twice)) : null, flowChartGet(st)]);
  if (FLOW_ST !== st || st.seq !== seq) return;
  Object.assign(st, {data, prev, chart});
  taskStatsRender();
}
// The stage totals of the period before the range, from a read of twice the range. Nothing recorded then means
// no comparison, not a rise from zero.
function flowPrevTotals(prev: FlowRead | null, days: number): Record<string, number> | null {
  if (!prev) return null;
  const today = flowToday(), from = flowKey(new Date(today.getFullYear(), today.getMonth(), today.getDate() - days + 1));
  const out: Record<string, number> = {};
  let any = false;
  for (const r of prev.days || []) if (r.day < from) for (const [k, v] of Object.entries(r)) if (k !== 'day') {
    out[k] = (out[k] || 0) + Number(v);
    if (v) any = true;
  }
  return any ? out : null;
}
// The chart's buckets, oldest first: the bars shown plus the ones before them that the first bars' averages reach
// back to, cut to what one read covers. Weeks start on Monday; a bucket is [start, end) in local day keys.
function flowChartPlan(unit: string): {buckets: FlowBucket[]; days: number; since: string; span: number; shown: number} {
  const [, , count, span] = FLOW_UNITS.find(u => u[0] === unit) || FLOW_UNITS[1];
  const today = flowToday(), y = today.getFullYear(), m = today.getMonth(), dd = today.getDate();
  const monday = dd - (today.getDay() + 6) % 7;
  const at = (k: number): [Date, Date] => unit === 'month' ? [new Date(y, m - k, 1), new Date(y, m - k + 1, 1)]
    : unit === 'week' ? [new Date(y, m, monday - 7 * k), new Date(y, m, monday - 7 * k + 7)]
    : [new Date(y, m, dd - k), new Date(y, m, dd - k + 1)];
  const buckets: FlowSlot[] = [];
  for (let k = count + span - 2; k >= 0; k--) {
    const [s, e] = at(k);
    const label = unit === 'month' ? s.toLocaleDateString(undefined, {month: 'long', year: 'numeric'})
      : (unit === 'week' ? 'Week of ' : '') + flowDay(flowKey(s));
    const short = unit === 'month' ? s.toLocaleDateString(undefined, {month: 'short'}) : flowDay(flowKey(s));
    buckets.push({start: flowKey(s), end: flowKey(e), label, short, from: s});
  }
  const days = Math.min(FLOW_MAX_DAYS, flowAgo(buckets[0]?.from ?? today, today) + 1);
  const since = flowKey(new Date(y, m, dd - days + 1));
  // A bar whose bucket starts before the read would show part of it: fewer bars instead (12 months can pass 365 days).
  let shown = count;
  while (shown > 1 && (buckets[buckets.length - shown]?.start ?? since) < since) shown--;
  // flowSeries sets each bucket's count, average and flags before anything reads them.
  return {buckets: buckets as FlowBucket[], days, since, span, shown};
}
function flowChartGet(st: FlowState): Promise<FlowRead | null> {
  // The person is the task's current owner (the endpoint's owner filter), not who moved it.
  return flowGet(st, flowQuery(st, flowChartPlan(st.unit).days, st.owner || st.who));
}
async function taskStatsChartLoad(): Promise<void> {
  const st = FLOW_ST; if (!st) return;
  const seq = ++st.chartSeq;
  const chart = await flowChartGet(st);
  if (FLOW_ST !== st || st.chartSeq !== seq) return;
  st.chart = chart;
  taskStatsChartRender();
}
// Each bar's count of the stage and its moving average. Buckets that start before the read, or end before the
// first thing recorded in it, have no record and stay out of averages; so does the running bucket, which is
// still filling.
function flowSeries(data: FlowRead | null, unit: string, stage: string): {list: FlowBucket[]; span: number} {
  const {buckets, since, span, shown} = flowChartPlan(unit);
  const rows = (data?.days || []).filter(r => Object.keys(r).some(k => k !== 'day' && r[k]));
  const first = rows.reduce<string | null>((min, r) => min == null || r.day < min ? r.day : min, null);
  const last = buckets.length - 1;
  buckets.forEach((b, i) => {
    b.n = rows.reduce((sum, r) => sum + (r.day >= b.start && r.day < b.end ? Number(r[stage]) || 0 : 0), 0);
    b.none = b.start < since || first == null || b.end <= first;
    b.running = i === last;
    b.avg = null;
  });
  buckets.forEach((b, i) => {
    if (b.none || b.running || i < span - 1) return;
    const run = buckets.slice(i - span + 1, i + 1).filter(x => !x.none);
    b.avg = run.reduce((sum, x) => sum + x.n, 0) / run.length;
  });
  return {list: buckets.slice(-shown), span};
}
const flowAvgN = (v: number) => (Math.round(v * 10) / 10).toFixed(1);
// Bars per bucket, the running one lighter, with the moving average over them. Drawn narrower on a phone so the
// labels stay readable; the x labels skip bars when they would collide.
function flowChartHTML(list: FlowBucket[], word: string, unitWord: string, avgWord: string): string {
  const W = matchMedia('(max-width:640px)').matches ? 380 : 640;
  const H = 170, left = 30, bottom = 20, top = 8, plotH = H - top - bottom, plotW = W - left - 4;
  const max = Math.max(1, ...list.map(b => Math.max(b.n, b.avg || 0)));
  const unit = max > 20 ? 5 : 1, step = max <= 4 ? 1 : Math.ceil(max / 4 / unit) * unit;
  const ticks: number[] = []; for (let v = 0; v < max + step; v += step) ticks.push(v);
  const yMax = ticks[ticks.length - 1] ?? 0;
  const slot = plotW / list.length, bw = Math.max(2, Math.min(28, slot - 4));
  const y = (v: number) => top + plotH - (v / yMax) * plotH;
  const lower = word.toLowerCase();
  const tipOf = (b: FlowBucket) => `${b.label}: ${b.n} ${lower}${b.running ? ' so far' : ''}${b.avg != null ? ` · avg ${flowAvgN(b.avg)}` : ''}`;
  const grid = ticks.map(v => `<line class="flow-grid" x1="${left}" x2="${W - 4}" y1="${y(v)}" y2="${y(v)}"/>
    <text class="flow-axis" x="${left - 6}" y="${y(v) + 3.5}" text-anchor="end">${v}</text>`).join('');
  const bars = list.map((b, i) => {
    const x = left + i * slot + (slot - bw) / 2, h = (b.n / yMax) * plotH, rad = Math.min(4, bw / 2, h);
    const mark = b.n ? `<path class="flow-bar${b.running ? ' so-far' : ''}" d="M${x},${top + plotH} v${-(h - rad)} q0,${-rad} ${rad},${-rad} h${bw - 2 * rad} q${rad},0 ${rad},${rad} v${h - rad} z"/>` : '';
    const tip = tipOf(b);
    return `<g class="flow-col" data-tip="${esc(tip)}"><rect class="flow-hit" x="${left + i * slot}" y="${top}" width="${slot}" height="${plotH}"/>${mark}<title>${esc(tip)}</title></g>`;
  }).join('');
  // The average as one line through the bars' middles, broken where a bucket has none.
  const runs: string[][] = [[]];
  list.forEach((b, i) => b.avg == null ? runs.push([]) : runs[runs.length - 1]?.push(`${(left + (i + 0.5) * slot).toFixed(1)},${y(b.avg).toFixed(1)}`));
  const line = runs.filter(r => r.length > 1).map(r => `<polyline class="flow-avg" points="${r.join(' ')}"/>`).join('');
  const every = Math.max(1, Math.ceil((Math.max(...list.map(b => b.short.length)) * 6.5 + 10) / slot));
  const xs = list.map((b, i) => (list.length - 1 - i) % every ? '' : `<text class="flow-axis" x="${left + (i + 0.5) * slot}" y="${H - 5}" text-anchor="middle">${esc(b.short)}</text>`).join('');
  const alt = `${word} per ${unitWord}, ${avgWord} line: ` + list.map(b => `${b.label} ${b.n}${b.running ? ' so far' : ''}`).join(', ');
  return `<svg class="flow-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(alt)}">${grid}${bars}${line}${xs}</svg>`;
}
// The stages on offer: a type's own steps when one is picked, else the statuses that matter here.
function flowStages(data: FlowRead, current: string): FlowStage[] {
  const totals: Record<string, number> = data.totals || {};
  if ((data.steps || []).length) return [['created', 'New'], ...(data.steps || []).map((s): FlowStage => ['step:' + s.id, s.name])];
  return FLOW_STAGES.filter(([k]) => FLOW_ALWAYS.includes(k) || totals[k] || k === current);
}
function taskStatsRender(): void {
  const st = FLOW_ST, body: HTMLElement | null = $('#task-stats-dialog [data-flow-body]'); if (!st || !body) return;
  const data = st.data;
  if (!data) { body.innerHTML = '<div class="empty">Stats did not load. Try again.</div>'; return; }
  const totals: Record<string, number> = data.totals || {};
  const stages = flowStages(data, st.stage);
  // A stage that is not on offer falls back to finishing: Done, or a type's last step.
  if (!stages.some(([k]) => k === st.stage)) st.stage = stages.some(([k]) => k === 'done') ? 'done' : flowLast(stages)[0];
  const before = flowPrevTotals(st.prev, st.days), range = (FLOW_RANGES.find(([n]) => n === st.days) || [])[1] || st.days + 'd';
  const change = (k: string) => {
    if (!before) return '';
    const n = (totals[k] || 0) - (before[k] || 0);
    return ` <span class="flow-tile-d" title="Previous ${esc(range)}: ${before[k] || 0}">${n > 0 ? '▲' + n : n < 0 ? '▼' + -n : '±0'}</span>`;
  };
  const tiles = stages.map(([k, label]) => `<button type="button" class="flow-tile${k === st.stage ? ' cur' : ''}" data-stage="${esc(k)}" aria-pressed="${k === st.stage}">
      <span class="flow-tile-n tnum">${totals[k] || 0}${change(k)}</span><span class="flow-tile-l">${esc(label)}</span></button>`).join('');
  const spanNames: FlowStage[] = (data.steps || []).length ? stages.filter(([k]) => k !== 'created') : Object.entries(FLOW_SPAN_WORD);
  const spans: Record<string, FlowSpan | undefined> = data.stages || {};
  const spanRows = spanNames.map<[string, FlowSpan | undefined]>(([k, label]) => [label, spans[k]]).filter((r): r is [string, FlowSpan] => !!r[1]?.n)
    .map(([label, v]) => `<tr><td>${esc(label)}</td><td class="tnum">${flowHours(v.median_hours)}</td><td class="tnum">${flowHours(v.p90_hours)}</td><td class="tnum muted">${v.n}</td></tr>`).join('');
  // By owner: New, then Done and Closed, or for a type the picked step (its last one while New is picked).
  const stepMode = (data.steps || []).length > 0;
  const cols: FlowStage[] = stepMode ? [stages.find(([k]) => k === st.stage && k !== 'created') || flowLast(stages)]
    : [['done', 'Done'], ['closed', 'Closed']];
  const peopleRows = (data.people || []).slice(0, 12).map(p => `<tr data-owner="${esc(actorSlug(p.actor) || p.actor)}" tabindex="0">
      <td>${esc(actorLabel(p.actor))}</td><td class="tnum">${p.created || 0}</td>${cols.map(([k]) => `<td class="tnum">${p[k] || 0}</td>`).join('')}</tr>`).join('');
  body.innerHTML = `<div class="flow-tiles" role="group" aria-label="Stage">${tiles}</div>
    <div data-flow-chart></div>
    <div class="flow-tables">
      <section><h3>Time in stage</h3>${spanRows ? `<table class="flow-table"><thead><tr><th></th><th>Median</th><th>90%</th><th>Tasks</th></tr></thead><tbody>${spanRows}</tbody></table>` : '<div class="empty">No stage finished in this range.</div>'}</section>
      <section><h3>By owner</h3>${peopleRows ? `<table class="flow-table flow-people"><thead><tr><th></th><th>New</th>${cols.map(([, label]) => `<th>${esc(label)}</th>`).join('')}</tr></thead><tbody>${peopleRows}</tbody></table>` : '<div class="empty">Nothing moved in this range.</div>'}</section>
    </div>`;
  const tilesEl = body.querySelector<HTMLElement>('.flow-tiles');
  if (!tilesEl) return;
  tilesEl.onclick = ev => {
    if (!(ev.target instanceof Element)) return;
    const b = ev.target.closest<HTMLElement>('[data-stage]'); if (!b) return;
    st.stage = b.dataset.stage ?? ''; taskStatsRemember(); taskStatsRender();
  };
  taskStatsChartRender();
  const people = body.querySelector<HTMLElement>('.flow-people');
  if (people) {
    const pick = (row: HTMLElement | null) => {
      const sel: HTMLSelectElement | null = $('#task-stats-dialog select[data-flow="owner"]');
      if (!row || !sel || ![...sel.options].some(o => o.value === row.dataset.owner)) return;
      sel.value = st.owner = row.dataset.owner ?? ''; taskStatsLoad();
    };
    const rowOf = (t: EventTarget | null) => t instanceof Element ? t.closest<HTMLElement>('tr[data-owner]') : null;
    people.onclick = ev => pick(rowOf(ev.target));
    people.onkeydown = ev => { if (ev.key === 'Enter') pick(rowOf(ev.target)); };
  }
}
// Everyone, then the humans on the roster and any other human in By owner, by name.
function flowChartPeople(data: FlowRead | null, who: string): [string, string][] {
  const seen = new Map((S.people || []).filter(p => !p.hidden).map((p): [string, string] => ['human:' + p.id, p.name || p.id]));
  for (const p of data?.people || []) if (String(p.actor).startsWith('human:') && !seen.has(p.actor)) seen.set(p.actor, actorLabel(p.actor));
  if (who && !seen.has(who)) seen.set(who, actorLabel(who));
  return [...seen].sort((a, b) => String(a[1]).localeCompare(String(b[1])));
}
function taskStatsChartRender(): void {
  const st = FLOW_ST, box: HTMLElement | null = $('#task-stats-dialog [data-flow-chart]'); if (!st || !box) return;
  const stages = flowStages(st.data || {}, st.stage);
  const word = (stages.find(([k]) => k === st.stage) || [])[1] || st.stage;
  const [unit, , , span] = FLOW_UNITS.find(u => u[0] === st.unit) || FLOW_UNITS[1];
  const avgWord = `${span}-${unit} avg`;
  const {list} = flowSeries(st.chart, unit, st.stage);
  const avg = [...list].reverse().find(b => b.avg != null)?.avg;
  const key = `<i></i>${esc(avgWord)}${avg != null ? ' ' + flowAvgN(avg) : ''}`;
  // The dialog's owner filter already narrows the chart; the chart's own picker shows only without it.
  const pick = st.owner ? '' : `<select data-flow-who aria-label="Person"><option value="">Everyone</option>${flowChartPeople(st.data, st.who)
    .map(([a, name]) => `<option value="${esc(a)}"${a === st.who ? ' selected' : ''}>${esc(name)}</option>`).join('')}</select>`;
  box.innerHTML = `<div class="flow-chart-head"><strong>${esc(word)} per ${unit}</strong>
      <span class="flow-avg-key muted tnum" data-flow-tip>${key}</span><span class="spacer"></span>${pick}
      <div class="flow-days" data-flow-unit role="group" aria-label="Bars">${FLOW_UNITS.map(([u, label]) =>
        `<button type="button" data-unit="${u}" class="${u === unit ? 'cur' : ''}" aria-pressed="${u === unit}">${label}</button>`).join('')}</div></div>
    ${st.chart ? flowChartHTML(list, word, unit, avgWord) : '<div class="empty">Chart did not load.</div>'}`;
  const unitBar = box.querySelector<HTMLElement>('[data-flow-unit]');
  if (!unitBar) return;
  unitBar.onclick = ev => {
    if (!(ev.target instanceof Element)) return;
    const b = ev.target.closest<HTMLElement>('[data-unit]'); if (!b || b.dataset.unit === st.unit) return;
    st.unit = b.dataset.unit ?? ''; taskStatsRemember(); taskStatsChartLoad();
  };
  const sel = box.querySelector<HTMLSelectElement>('[data-flow-who]');
  if (sel) sel.onchange = () => { st.who = sel.value; taskStatsChartLoad(); };
  const tip = box.querySelector<HTMLElement>('[data-flow-tip]'), chart = box.querySelector<SVGSVGElement>('.flow-chart');
  if (!tip || !chart) return;
  chart.onpointerover = ev => {
    const g = ev.target instanceof Element ? ev.target.closest<HTMLElement>('.flow-col') : null;
    if (g) tip.textContent = g.dataset.tip ?? ''; else tip.innerHTML = key;
  };
  chart.onpointerleave = () => { tip.innerHTML = key; };
}
