/* ui/app/automations-overview.js — The Automations list: filters on the left, every automation the viewer may see as
   a dense table with its runs over the time range, and a Runs tab with every run across them
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   Payloads: CONTRACT §2 (GET /api/v2/automations, /automations/stats?range=, /automations/runs?range=). Search,
   status and team narrow on the client; the Runs tab sends trigger and owner, and the ids those leave when it can. */
'use strict';

const AU_RANGES = {'5m': '30 s', '1h': '5 min', '24h': 'hour', '7d': '6 hours'};
const AU_RANGE_SECONDS = {'5m': 300, '1h': 3600, '24h': 86400, '7d': 604800};
const AU_RANGE_KEY = 'tico.automations.range';
const AU_KINDS = ['message', 'task_received', 'schedule', 'webhook', 'listening', 'event', 'due', 'system'];
const AU_KIND_WORDS = {message: 'Message', task_received: 'Task received', schedule: 'Schedule', webhook: 'Webhook',
  listening: 'Listening', event: 'Event', due: 'Due date', system: 'System job'};
const AU_KIND_ICONS = {...AU_TRIGGER_ICONS, system: 'settings'};
const AU_STATES = [['on', 'On', 'ok'], ['failing', 'Last run failed', 'fail'], ['hold', 'On hold', 'wait'], ['paused', 'Paused', 'wait'], ['off', 'Off', 'idle']];
const AU_SORTS = [['last', 'Last run'], ['fail', 'Most failures'], ['runs', 'Most runs'], ['cost', 'Cost'], ['name', 'Name']];
const AU_RUN_PILLS = {ok: 'ok', empty: 'ok', skipped: 'ok', failed: 'fail', waiting: 'waiting', running: 'in-progress', queued: 'in-progress', held: 'waiting'};
const AU_ALL_PAGE = 50;
const AU_IDS_MAX = 200;   // ids sent with a Runs page; past this the page is narrowed on the client only

function auRangeSaved() {
  try { const r = localStorage.getItem(AU_RANGE_KEY); return AU_RANGES[r] ? r : '1h'; } catch { return '1h'; }
}
function auOverviewState() {
  return {range: auRangeSaved(), tab: 'list', f: {trig: new Set(), status: '', team: '', owner: '', q: ''},
          group: 'trigger', sort: 'last', rstatus: '', closed: new Set(), stats: null, all: null, scroll: {list: 0, runs: 0}};
}

// What each row is: its trigger as the filters group it, its state, its team and its trigger in plain words.
const auKind = a => a.kind || a.trigger?.type || '';
const auState = a => a.paused ? 'paused' : a.enabled === false ? 'off' : a.hold ? 'hold' : a.last_run?.status === 'failed' ? 'failing' : 'on';
function auTeam(a) {
  const g = a.group || (String(a.owner || '').startsWith('group:') ? a.owner : '');
  if (g) return auGroupName(g);
  const home = typeof rdoHome === 'function' && a.owner ? rdoHome(String(a.owner)) : '';
  return home ? auGroupName(home) : '';
}
const auTrigLine = a => auKind(a) === 'system' ? a.schedule || a.controls?.schedule || 'Tico' : auSource(a.trigger) || AU_TRIGGER_WORDS[a.trigger?.type] || '';
const auActive = f => f.trig.size + !!f.status + !!f.team + !!f.owner;

function auMatches(a, f = AU.f) {
  if (f.trig.size && !f.trig.has(auKind(a))) return false;
  if (f.status && auState(a) !== f.status) return false;
  if (f.team && auTeam(a) !== f.team) return false;
  if (f.owner && a.owner !== f.owner) return false;
  return !f.q || [a.name, auTrigLine(a), auActorName(a.owner), AU_KIND_WORDS[auKind(a)], a.description]
    .join(' ').toLowerCase().includes(f.q);
}
const auShown = () => (AU.list || []).filter(a => auMatches(a));

// Compact ages: "now", "4m", "3h", "2d".
function auAge(iso, ahead = false) {
  const t = new Date(iso).getTime();
  if (!iso || isNaN(t)) return '';
  const s = Math.abs(ahead ? t - Date.now() : Date.now() - t) / 1000;
  return s < 60 ? 'now' : s < 3600 ? Math.round(s / 60) + 'm' : s < 86400 ? Math.round(s / 3600) + 'h' : Math.round(s / 86400) + 'd';
}
const auDur = r => { const s = r.finished_at ? (new Date(r.finished_at) - new Date(r.started_at)) / 1000 : NaN;
  return isNaN(s) || s < 0 ? '' : s < 60 ? Math.round(s) + 's' : s < 3600 ? Math.round(s / 60) + 'm' : Math.round(s / 3600) + 'h'; };
const auIcon = (name, cls = '') => `<span class="nav-icon${cls ? ' ' + cls : ''}" aria-hidden="true">${name}</span>`;

// -------------------------------------------------------------------------------------------- loading
async function auStatsLoad(state) {
  const range = state.range;
  let s;
  try { s = await get('/v2/automations/stats?range=' + range); }
  catch (e) { s = {error: e.message}; }
  if (AU !== state || state.range !== range) return;
  state.stats = {range, at: Date.now(), error: s.error || '', n: s.totals?.buckets?.length || 0,
                 per: new Map((s.automations || []).map(x => [x.id, x]))};
  auDrawTable();
}
function auAllQuery() {
  const f = AU.f, p = new URLSearchParams({range: AU.range});
  if (AU.rstatus) p.set('status', AU.rstatus);
  if (f.trig.size) p.set('trigger', [...f.trig].join(','));
  if (f.owner) p.set('owner', f.owner);
  if (f.q || f.status || f.team) {
    const ids = auShown().map(a => a.id);
    if (ids.length <= AU_IDS_MAX) p.set('ids', ids.join(',') || 'none');
  }
  return p.toString();
}
async function auAllLoad(state, more = false) {
  const key = auAllQuery();
  let r = state.all;
  if (!r || r.key !== key) r = state.all = {key, rows: [], next: null, loading: false, error: ''};
  if (r.loading || (more && !r.next) || (!more && r.done)) return;
  r.loading = true; r.error = '';
  if (!more) auDrawTable();
  try {
    const page = await get('/v2/automations/runs?' + key + '&limit=' + AU_ALL_PAGE + (more ? '&before=' + encodeURIComponent(r.next) : ''));
    if (state.all !== r) return;
    r.rows = more ? r.rows.concat(page.runs || []) : page.runs || [];
    r.next = page.next || null; r.done = true;
  } catch (e) { r.error = e.message; }
  r.loading = false;
  if (AU === state) auDrawTable();
}

// -------------------------------------------------------------------------------------------- drawing
function auRangeDraw() {
  const seg = AU.root.querySelector('.au-range');
  seg.innerHTML = Object.keys(AU_RANGES).map(k => `<button type="button" data-range="${k}" aria-pressed="${AU.range === k}">${k}</button>`).join('');
}
function auFacetsDraw() {
  const box = AU.root.querySelector('.au-facets'), list = AU.list || [], f = AU.f;
  const count = pred => list.filter(pred).length;
  const btn = (attr, val, on, lead, label, n) => `<button type="button" class="au-fb" ${attr}="${esc(val)}" aria-pressed="${on}">${lead}<span class="au-ft">${esc(label)}</span><span class="au-fn">${n}</span></button>`;
  const teams = [...new Set(list.map(auTeam).filter(Boolean))].sort((x, y) => x.localeCompare(y));
  const owners = [...new Set(list.map(a => a.owner).filter(Boolean))].map(o => [o, auActorName(o)]).sort((x, y) => x[1].localeCompare(y[1]));
  box.innerHTML = `<div class="au-fg" role="group" aria-label="Trigger"><div class="au-lb">Trigger</div>
      ${AU_KINDS.map(k => btn('data-ftrig', k, f.trig.has(k), auIcon(AU_KIND_ICONS[k]), AU_KIND_WORDS[k], count(a => auKind(a) === k))).join('')}</div>
    <div class="au-fg" role="group" aria-label="Status"><div class="au-lb">Status</div>
      ${AU_STATES.map(([k, l, dot]) => btn('data-fstatus', k, f.status === k, `<span class="au-dot ${dot}" aria-hidden="true"></span>`, l, count(a => auState(a) === k))).join('')}</div>
    ${teams.length ? `<div class="au-fg" role="group" aria-label="Team"><div class="au-lb">Team</div>
      ${teams.map(t => btn('data-fteam', t, f.team === t, auIcon('groups'), t, count(a => auTeam(a) === t))).join('')}</div>` : ''}
    <div class="au-fg" role="group" aria-label="Owner"><div class="au-lb">Owner</div>
      ${owners.map(([o, name]) => btn('data-fowner', o, f.owner === o, personCircle(name, 16), name, count(a => a.owner === o))).join('')}</div>`;
}
function auCtlDraw() {
  const ctl = AU.root.querySelector('.au-ctl'), list = AU.tab === 'list', n = auActive(AU.f);
  const opts = (pairs, cur) => pairs.map(([v, l]) => `<option value="${v}"${v === cur ? ' selected' : ''}>${l}</option>`).join('');
  ctl.innerHTML = `<div class="au-tabs" role="tablist" aria-label="View">
      <button type="button" role="tab" data-tab="list" aria-selected="${list}">Automations</button>
      <button type="button" role="tab" data-tab="runs" aria-selected="${!list}">Runs</button></div>
    <button type="button" class="au-chip au-fbtn" data-filters aria-expanded="${AU.root.classList.contains('filters-open')}">${auIcon('filter_alt')}Filters${n ? ' · ' + n : ''}</button>
    <span class="au-sp"></span>
    ${list ? `<label class="au-sel">Group <select data-group>${opts([['trigger', 'Trigger'], ['owner', 'Owner'], ['none', 'None']], AU.group)}</select></label>
      <label class="au-sel">Sort <select data-sort>${opts(AU_SORTS, AU.sort)}</select></label>`
    : `<label class="au-sel">Status <select data-rstatus>${opts([['', 'All'], ['failed', 'Failed'], ['waiting', 'Waiting'], ['running', 'Running'], ['done', 'Done']], AU.rstatus)}</select></label>`}`;
}
// The overview's parts: the filters, the bar above the table and the table (skipped while an automation is open).
function auDrawList() {
  if (!AU?.root) return;
  auFacetsDraw(); auCtlDraw(); auDrawTable();
}

function auSpark(b, w = 84, h = 18) {
  const n = b.length || 1, max = Math.max(1, ...b.map(d => d.ok + d.failed)), bw = w / n;
  return `<svg class="au-spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">${b.map((d, i) => {
    const v = d.ok + d.failed, hv = v ? Math.max(2, v / max * h) : 1, hf = d.failed ? Math.max(2, d.failed / max * h) : 0;
    const x = (i * bw + .5).toFixed(1), wd = Math.max(1, bw - 1.5).toFixed(1);
    return `<rect x="${x}" y="${h - hv}" width="${wd}" height="${hv}" rx="1" fill="${v ? 'var(--g5)' : 'var(--g3)'}"/>`
      + (hf ? `<rect x="${x}" y="${h - hf}" width="${wd}" height="${hf}" rx="1" fill="var(--fail)"/>` : '');
  }).join('')}</svg>`;
}
// A wake rule that names a step its type does not have never fires: one short line, on the row and in the detail.
function auWarn(a) {
  const steps = [...new Set((a.problems || []).map(p => p.step))];
  return steps.length ? `<span class="au-warn"><span class="nav-icon" aria-hidden="true">warning</span>`
    + `${esc('Names a step its type does not have: ' + steps.join(', '))}</span>` : '';
}
function auPill(a) {
  const st = auState(a);
  return (a.pending ? '<span class="pill">Pending</span>' : '')
    + (st === 'paused' ? '<span class="pill waiting">Paused</span>' : st === 'off' ? '<span class="pill">Off</span>'
      : st === 'hold' ? '<span class="pill waiting">On hold</span>' : '');
}
const AU_DOTS = {ok: 'ok', empty: 'ok', skipped: 'ok', failed: 'fail', waiting: 'wait'};
// The Next cell: when it next runs; for a due date, the due day over when its task opens.
function auNextHTML(a) {
  if (a.enabled === false || !a.next_run) return '—';
  const at = new Date(a.next_run).getTime(), age = at > Date.now() ? auAge(a.next_run, true) : 'now';
  if (auKind(a) === 'due' && a.next_due) {
    const far = Date.parse(a.next_due) - Date.now() > 330 * 864e5;   // the year only once it could be mistaken
    return `<span class="au-nx"><span class="au-nx-d">Due ${esc(auDay(a.next_due, far))}</span><span>${age === 'now' ? 'opens now' : 'opens in ' + esc(age)}</span></span>`;
  }
  return age === 'now' ? 'due' : 'in ' + esc(age);
}
// On a phone (auPhone, automations.js) the table's columns come in another order: redraw when the width crosses over.
matchMedia('(max-width:760px)').addEventListener?.('change', () => { if (AU?.root && document.body.contains(AU.root)) auDrawTable(); });

function auTableHTML() {
  const st = AU.stats, n = st?.n || 0, zero = Array.from({length: n || 12}, () => ({ok: 0, failed: 0}));
  const stat = a => st?.per.get(a.id) || {runs: 0, failed: 0, cost: 0, buckets: zero};
  const rows = auShown().map(a => ({a, s: stat(a)}));
  const last = r => r.a.last_run?.started_at || '';
  const sorters = {last: (x, y) => last(y).localeCompare(last(x)), fail: (x, y) => y.s.failed - x.s.failed || y.s.runs - x.s.runs,
    runs: (x, y) => y.s.runs - x.s.runs, cost: (x, y) => y.s.cost - x.s.cost, name: (x, y) => String(x.a.name).localeCompare(String(y.a.name))};
  rows.sort(sorters[AU.sort] || sorters.last);
  const keyOf = r => AU.group === 'trigger' ? auKind(r.a) : AU.group === 'owner' ? r.a.owner || '' : '';
  const groups = [];
  for (const r of rows) { const k = keyOf(r); let g = groups.find(x => x.k === k); if (!g) groups.push(g = {k, rows: []}); g.rows.push(r); }
  // A kind this page does not know yet goes after the known ones, not before them.
  const rank = k => { const i = AU_KINDS.indexOf(k); return i < 0 ? AU_KINDS.length : i; };
  if (AU.group === 'trigger') groups.sort((x, y) => rank(x.k) - rank(y.k));
  if (AU.group === 'owner') groups.sort((x, y) => auActorName(x.k).localeCompare(auActorName(y.k)));
  const label = k => AU.group === 'trigger' ? auIcon(AU_KIND_ICONS[k] || 'bolt') + esc(AU_KIND_WORDS[k] || auCap(k))
    : personCircle(auActorName(k), 16) + esc(auActorName(k));
  const money = c => c >= 0.005 ? esc(auMoney(c)) : '<span class="muted">—</span>';
  // Each column: its class (widths and folding in automations.css), heading and cell. A phone leads with what is read
  // first (name, last run, runs, failed) and scrolls sideways for the rest.
  const cols = {
    name: ['au-tn', 'Name', ({a}) => `<div><a href="${auHref(a.id)}">${auIcon(AU_KIND_ICONS[auKind(a)] || 'bolt')}<span class="au-nm">${esc(a.name || 'Untitled')}</span></a>${auPill(a)}</div>${auWarn(a)}`],
    trigger: ['au-tt', 'Trigger', ({a}) => esc(auTrigLine(a))],
    owner: ['au-to', 'Owner', ({a}) => `<span title="${esc(auActorName(a.owner))}" aria-label="${esc(auActorName(a.owner))}">${personCircle(auActorName(a.owner), 18)}</span>`],
    last: ['au-cl', 'Last run', ({a}) => a.last_run ? `<span class="au-lr"><span class="au-dot ${AU_DOTS[a.last_run.status] || 'idle'}" role="img" aria-label="${esc(AU_RUN_STATUS[a.last_run.status] || a.last_run.status)}"></span>${esc(auAge(a.last_run.started_at))}</span>` : '<span class="muted">—</span>'],
    next: ['au-cx muted', 'Next', ({a}) => auNextHTML(a)],
    trend: ['au-cs', 'Last ' + AU.range, ({s}) => auSpark(s.buckets || zero)],
    runs: ['au-n au-cn', 'Runs', ({s}) => s.runs ? s.runs.toLocaleString() : '<span class="muted">0</span>'],
    failed: ['au-n au-cf', 'Failed', ({s}) => s.failed || '—'],
    cost: ['au-n au-cc', 'Cost', ({s}) => money(s.cost)],
  };
  const order = auPhone() ? ['name', 'last', 'runs', 'failed', 'trigger', 'owner', 'next', 'trend', 'cost']
    : ['name', 'trigger', 'owner', 'last', 'next', 'trend', 'runs', 'failed', 'cost'];
  const cell = (k, r) => {
    const [cls, , html] = cols[k], extra = k === 'failed' ? (r.s.failed ? ' au-bad' : ' muted') : '';
    return `<td class="${cls}${extra}"${k === 'trigger' ? ` title="${esc(auTrigLine(r.a))}"` : ''}>${html(r)}</td>`;
  };
  const tr = r => `<tr class="au-row${r.a.enabled === false ? ' off' : ''}" data-row="${esc(r.a.id)}">${order.map(k => cell(k, r)).join('')}</tr>`;
  const body = rows.length ? groups.map(g => (AU.group === 'none' ? '' :
    `<tr class="au-grp${AU.closed.has(g.k) ? ' closed' : ''}" data-agrp="${esc(g.k)}" role="button" tabindex="0" aria-expanded="${!AU.closed.has(g.k)}"><td colspan="${order.length}">${auIcon('expand_more', 'au-chev')}${label(g.k)}<span class="au-fn">${g.rows.length}</span></td></tr>`)
    + (AU.closed.has(g.k) && AU.group !== 'none' ? '' : g.rows.map(tr).join(''))).join('')
    : `<tr><td colspan="${order.length}" class="muted au-pad">${(AU.list || []).length ? 'No automations match these filters' : 'No automations'}</td></tr>`;
  return `<table class="au-tbl-t"><thead><tr>${order.map(k => `<th class="${cols[k][0].replace(' muted', '')}">${cols[k][1]}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table>
    ${st?.error ? `<p class="err au-pad">${esc(st.error)}</p>` : ''}`;
}

// The Runs tab: done and failed per bucket for what the filters show, then every run newest first.
function auChartHTML() {
  const st = AU.stats;
  if (!st || st.range !== AU.range || !st.n) return `<div class="au-chart"><p class="muted">${st?.error ? esc(st.error) : 'Loading…'}</p></div>`;
  const shown = new Set(auShown().map(a => a.id)), b = Array.from({length: st.n}, () => ({ok: 0, failed: 0}));
  for (const [id, s] of st.per) if (shown.has(id)) (s.buckets || []).forEach((x, i) => { if (b[i]) { b[i].ok += x.ok; b[i].failed += x.failed; } });
  const ok = b.reduce((t, x) => t + x.ok, 0), failed = b.reduce((t, x) => t + x.failed, 0);
  const W = 720, H = 64, bw = W / b.length, max = Math.max(1, ...b.map(x => x.ok + x.failed));
  const span = AU_RANGE_SECONDS[AU.range], ticks = [0, 1, 2, 3].map(q => auAge(new Date(Date.now() - span * 1000 * (1 - q / 4)).toISOString()));
  return `<div class="au-chart"><div class="au-chart-h"><span class="au-lb">Runs per ${AU_RANGES[AU.range]}</span><span class="au-sp"></span>
      <span class="au-key"><i class="ok"></i>${ok.toLocaleString()} done</span><span class="au-key"><i class="fail"></i>${failed.toLocaleString()} failed</span></div>
    <svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" preserveAspectRatio="none" role="img" aria-label="${ok} done, ${failed} failed in the last ${AU.range}">
      <line x1="0" x2="${W}" y1="${H - .5}" y2="${H - .5}" stroke="var(--line)"/>
      ${b.map((x, i) => { const t = (x.ok + x.failed) / max * (H - 4), f = x.failed / max * (H - 4), X = (i * bw + 2).toFixed(1), w = Math.max(1, bw - 4).toFixed(1);
        return (t - f > 0 ? `<rect x="${X}" y="${(H - t).toFixed(1)}" width="${w}" height="${(t - f).toFixed(1)}" fill="var(--g5)" rx="1.5"/>` : '')
          + (f ? `<rect x="${X}" y="${(H - f).toFixed(1)}" width="${w}" height="${f.toFixed(1)}" fill="var(--fail)" rx="1.5"/>` : ''); }).join('')}
    </svg><div class="au-ticks">${ticks.map(t => `<span>${esc(t)}</span>`).join('')}<span>now</span></div></div>`;
}
function auAllHTML() {
  const r = AU.all, shown = new Set(auShown().map(a => a.id));
  const rows = (r?.rows || []).filter(x => shown.has(x.automation_id));
  const kindOf = id => auKind((AU.list || []).find(a => a.id === id) || {});
  const table = rows.length ? `<table class="au-tbl-t au-all"><thead><tr><th class="au-cw">When</th><th class="au-cst">Status</th><th>Automation</th><th class="au-why">Started by</th><th class="au-out">Outcome</th>
      <th class="au-n au-cx">Steps</th><th class="au-n au-cx">Time</th><th class="au-n au-cc">Cost</th></tr></thead><tbody>
      ${rows.map(x => `<tr class="au-arow" role="button" tabindex="0" data-arun="${esc(x.id)}" data-aid="${esc(x.automation_id)}">
        <td class="muted" title="${esc(fmt(x.started_at))}">${esc(auAge(x.started_at) === 'now' ? 'now' : auAge(x.started_at) + ' ago')}</td>
        <td><span class="pill ${AU_RUN_PILLS[x.status] || ''}">${esc(AU_RUN_STATUS[x.status] || auCap(x.status))}</span></td>
        <td class="au-tn"><div>${auIcon(AU_KIND_ICONS[x.trigger || kindOf(x.automation_id)] || 'bolt')}<span class="au-nm">${esc(x.automation_name || '')}</span></div></td>
        <td class="au-why" title="${esc(x.started_by || '')}">${esc(x.started_by || '')}</td>
        <td class="au-out${x.status === 'failed' ? ' au-bad' : ''}" title="${esc(x.outcome || '')}">${esc(x.outcome || '')}${x.count > 1 ? ` <span class="muted">×${x.count}</span>` : ''}</td>
        <td class="au-n au-cx muted">${x.step_count ?? '—'}</td><td class="au-n au-cx muted">${esc(auDur(x)) || '—'}</td>
        <td class="au-n au-cc muted">${x.cost ? esc(auMoney(x.cost)) : '—'}</td></tr>`).join('')}</tbody></table>`
    : `<p class="muted au-pad">${!r || r.loading ? 'Loading…' : r.error ? '' : 'No runs in the last ' + AU.range}</p>`;
  return auChartHTML() + table
    + (r?.next ? `<div class="au-more"><button class="ghost" type="button" data-all-more${r.loading ? ' disabled' : ''}>${r.loading ? 'Loading…' : 'More'}</button></div>` : '')
    + (r?.error ? `<p class="err au-pad">${esc(r.error)}</p>` : '');
}
function auDrawTable() {
  if (!AU?.root || AU.id) return;
  const box = AU.root.querySelector('.au-tbl');
  if (AU.listError) { box.innerHTML = `<p class="err au-pad">${esc(AU.listError)}</p>`; return; }
  if (AU.list === null) { box.innerHTML = '<p class="muted au-pad">Loading…</p>'; return; }
  patchLiveHTML(box, AU.tab === 'runs' ? auAllHTML() : auTableHTML());   // a fresh read that changed nothing moves nothing
  box.scrollTop = AU.scroll[AU.tab] || 0;
}

// The list is shown: what it reads (the counts for the range, the Runs page) and where it was scrolled.
function auOverviewShow(state) {
  if (!state.stats || state.stats.range !== state.range || Date.now() - state.stats.at > 60000) void auStatsLoad(state);
  if (state.tab === 'runs') void auAllLoad(state);
  auRangeDraw(); auDrawList();
}

// -------------------------------------------------------------------------------------------- events
function auFiltered() {
  AU.scroll.list = AU.scroll.runs = 0;
  if (AU.id) { location.hash = AUTOMATIONS; return; }   // a filter shows the list again
  auDrawList();
  if (AU.tab === 'runs') { clearTimeout(AU.allSoon); AU.allSoon = setTimeout(() => { if (AU) void auAllLoad(AU); }, 250); }
}
function auRangeSet(r) {
  if (!AU_RANGES[r] || AU.range === r) return;
  AU.range = r;
  try { localStorage.setItem(AU_RANGE_KEY, r); } catch { /* kept for this visit only */ }
  AU.stats = null; AU.all = null;
  auRangeDraw();
  void auStatsLoad(AU);
  if (AU.tab === 'runs') void auAllLoad(AU);
  auDrawTable();
}
// Clicks on the list's parts; true when one was handled.
function auOverviewClick(t) {
  let m;
  const f = AU.f, one = (key, v) => { f[key] = f[key] === v ? '' : v; auFiltered(); };
  if ((m = t.closest('[data-range]'))) { auRangeSet(m.dataset.range); return true; }
  if ((m = t.closest('[data-ftrig]'))) { const k = m.dataset.ftrig; if (f.trig.has(k)) f.trig.delete(k); else f.trig.add(k); auFiltered(); return true; }
  if ((m = t.closest('[data-fstatus]'))) { one('status', m.dataset.fstatus); return true; }
  if ((m = t.closest('[data-fteam]'))) { one('team', m.dataset.fteam); return true; }
  if ((m = t.closest('[data-fowner]'))) { one('owner', m.dataset.fowner); return true; }
  if ((m = t.closest('[data-tab]'))) {
    AU.tab = m.dataset.tab; auCtlDraw(); auDrawTable();
    if (AU.tab === 'runs') void auAllLoad(AU);
    return true;
  }
  if ((m = t.closest('[data-filters]'))) { const open = AU.root.classList.toggle('filters-open'); m.setAttribute('aria-expanded', String(open)); return true; }
  if ((m = t.closest('[data-agrp]'))) { const k = m.dataset.agrp; if (AU.closed.has(k)) AU.closed.delete(k); else AU.closed.add(k); auDrawTable(); return true; }
  if ((m = t.closest('[data-all-more]'))) { void auAllLoad(AU, true); return true; }
  if ((m = t.closest('tr[data-arun]'))) {
    if (!m.dataset.arun.startsWith('sys_')) AU.openRun = m.dataset.arun;
    location.hash = auHref(m.dataset.aid, 'runs');
    return true;
  }
  if ((m = t.closest('tr[data-row]')) && !t.closest('a')) { location.hash = auHref(m.dataset.row); return true; }
  return false;
}
function auOverviewChange(e) {
  const t = e.target;
  if (t.matches('[data-group]')) { AU.group = t.value; AU.closed.clear(); auDrawTable(); }
  else if (t.matches('[data-sort]')) { AU.sort = t.value; auDrawTable(); }
  else if (t.matches('[data-rstatus]')) { AU.rstatus = t.value; AU.scroll.runs = 0; void auAllLoad(AU); }
}
function auOverviewWire(root) {
  root.querySelector('.au-q').addEventListener('input', e => { AU.f.q = e.target.value.trim().toLowerCase(); auFiltered(); });
  root.addEventListener('change', auOverviewChange);
  const box = root.querySelector('.au-tbl');
  box.addEventListener('scroll', () => {
    if (!AU || AU.id) return;
    AU.scroll[AU.tab] = box.scrollTop;
    // Scrolling near the end of the Runs tab reads the next page.
    if (AU.tab === 'runs' && AU.all?.next && !AU.all.loading && box.scrollTop + box.clientHeight > box.scrollHeight - 200) void auAllLoad(AU, true);
  }, {passive: true});
}
