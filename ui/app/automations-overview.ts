/* ui/app/automations-overview.ts — The Automations list: filters on the left, every automation the viewer may see as
   a dense table with its runs over the time range, and a Runs tab with every run across them
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   Payloads: CONTRACT §2 (GET /api/v2/automations, /automations/stats?range=, /automations/runs?range=). Search,
   status and team narrow on the client; the Runs tab sends trigger and owner, and the ids those leave when it can. */
'use strict';

// What the list reads of an automation (GET /api/v2/automations): a list row (AuRow, ui/types/automations.d.ts) with
// the fields only a built-in or a system job carries.
interface AuItem extends AuRow {
  group?: string | null;
  controls?: { schedule?: string | null } | null;
  description?: string | null;
}
// One day-or-hour of runs: the done and failed counts of a bucket.
interface AuBucket {
  ok: number;
  failed: number;
}
// An automation's counts for the range (GET /v2/automations/stats), per automation id.
interface AuPerStat {
  runs: number;
  failed: number;
  cost: number;
  buckets: AuBucket[];
}
interface AuStatsResponse {
  error?: string | null;
  totals?: { buckets?: unknown[] | null } | null;
  automations?: (AuPerStat & { id: string })[] | null;
}
interface AuStats {
  range: string;
  at: number;
  error: string;
  n: number;
  per: Map<string, AuPerStat>;
}
// One run on the Runs tab (GET /v2/automations/runs).
interface AuOverviewRun {
  id: string;
  automation_id: string;
  automation_name?: string | null;
  status?: string | null;
  trigger?: string | null;
  started_by?: string | null;
  outcome?: string | null;
  count?: number | null;
  step_count?: number | null;
  cost?: number | null;
  started_at: string;
  finished_at?: string | null;
}
interface AuRunsPage {
  runs?: AuOverviewRun[] | null;
  next?: string | null;
}
interface AuAll {
  key: string;
  rows: AuOverviewRun[];
  next: string | null;
  loading: boolean;
  error: string;
  done?: boolean;
}
interface AuFilters {
  trig: Set<string>;
  status: string;
  team: string;
  owner: string;
  q: string;
}
// The filter sections a phone shows as headers.
type AuSection = 'trig' | 'status' | 'team' | 'owner';
// The table's columns, by key.
type AuColKey = 'name' | 'trigger' | 'owner' | 'last' | 'next' | 'trend' | 'runs' | 'failed' | 'cost';
type AuCol = [string, string, (r: AuRowT) => string | number];
// The overview's own state (AU, which automations.js owns and this file reads and writes through auOverviewState).
interface AuOverviewView {
  range: string;
  tab: string;
  f: AuFilters;
  group: string;
  sort: string;
  rstatus: string;
  closed: Set<string>;
  fopen: Set<string>;
  stats: AuStats | null;
  all: AuAll | null;
  scroll: Record<string, number>;
  cols: AuColKey[] | null;
}
// One row of the table: the automation and its counts for the range.
interface AuRowT {
  a: AuItem;
  s: AuPerStat;
}

const AU_RANGES: Record<string, string> = {'5m': '30 s', '1h': '5 min', '24h': 'hour', '7d': '6 hours'};
const AU_RANGE_SECONDS: Record<string, number> = {'5m': 300, '1h': 3600, '24h': 86400, '7d': 604800};
const AU_RANGE_KEY = 'tico.automations.range';
const AU_KINDS: string[] = ['message', 'task_received', 'schedule', 'webhook', 'event', 'due', 'system'];
const AU_KIND_WORDS: Record<string, string> = {message: 'Message', task_received: 'Task received', schedule: 'Schedule', webhook: 'Webhook',
  event: 'Event', due: 'Due date', system: 'System job'};
const AU_KIND_ICONS: Record<string, string> = {...AU_TRIGGER_ICONS, system: 'settings'};
const AU_STATES: [string, string, string][] = [['on', 'On', 'ok'], ['failing', 'Last run failed', 'fail'], ['hold', 'On hold', 'wait'], ['paused', 'Paused', 'wait'], ['off', 'Off', 'idle']];
const AU_SORTS: [string, string][] = [['last', 'Last run'], ['fail', 'Most failures'], ['runs', 'Most runs'], ['cost', 'Cost'], ['name', 'Name']];
const AU_RUN_PILLS: Record<string, string> = {ok: 'ok', empty: 'ok', skipped: 'ok', failed: 'fail', waiting: 'waiting', running: 'in-progress', queued: 'in-progress', held: 'waiting'};
// Columns that leave the table when no automation has a value in them for the range (auTableHTML).
const AU_OPTIONAL_COLS: AuColKey[] = ['next', 'trend', 'runs', 'failed', 'cost'];
const AU_ALL_PAGE = 50;
const AU_IDS_MAX = 200;   // ids sent with a Runs page; past this the page is narrowed on the client only

function auRangeSaved(): string {
  try { const r = localStorage.getItem(AU_RANGE_KEY); return r && AU_RANGES[r] ? r : '1h'; } catch { return '1h'; }
}
function auOverviewState(): AuOverviewView {
  return {range: auRangeSaved(), tab: 'list', f: {trig: new Set(), status: '', team: '', owner: '', q: ''},
          group: 'trigger', sort: 'last', rstatus: '', closed: new Set(), fopen: new Set(), stats: null, all: null,
          scroll: {list: 0, runs: 0}, cols: null};
}

// What each row is: its trigger as the filters group it, its state, its team and its trigger in plain words.
const auKind = (a: Partial<AuItem>): string => a.kind || a.trigger?.type || '';
const auState = (a: AuItem): string => a.paused ? 'paused' : a.enabled === false ? 'off' : a.hold ? 'hold' : a.last_run?.status === 'failed' ? 'failing' : 'on';
function auTeam(a: AuItem): string {
  const g = a.group || (String(a.owner_id || '').startsWith('group:') ? a.owner_id : '');
  if (g) return auGroupName(g);
  const home = typeof rdoHome === 'function' && a.owner_id ? rdoHome(String(a.owner_id)) : '';
  return home ? auGroupName(home) : '';
}
const auTrigLine = (a: AuItem): string => auKind(a) === 'system' ? a.schedule || a.controls?.schedule || 'Tico' : auSource(a.trigger) || (AU_TRIGGER_WORDS as Record<string, string>)[a.trigger?.type ?? ''] || '';
const auActive = (f: AuFilters): number => f.trig.size + +!!f.status + +!!f.team + +!!f.owner;
// How many of a filter section's items are picked: the phone's section headers and which of them start open.
const auPicked = (f: AuFilters, sec: AuSection): number => sec === 'trig' ? f.trig.size : +!!f[sec];

function auMatches(a: AuItem, f: AuFilters = auPage().f): boolean {
  if (f.trig.size && !f.trig.has(auKind(a))) return false;
  if (f.status && auState(a) !== f.status) return false;
  if (f.team && auTeam(a) !== f.team) return false;
  if (f.owner && a.owner_id !== f.owner) return false;
  return !f.q || [a.name, auTrigLine(a), auActorName(a.owner_id), AU_KIND_WORDS[auKind(a)], a.description]
    .join(' ').toLowerCase().includes(f.q);
}
const auShown = (): AuItem[] => (auPage().list || []).filter((a: AuItem) => auMatches(a));

// Compact ages: "now", "4m", "3h", "2d".
function auAge(iso: string | null | undefined, ahead = false): string {
  const t = new Date(iso ?? '').getTime();
  if (!iso || isNaN(t)) return '';
  const s = Math.abs(ahead ? t - Date.now() : Date.now() - t) / 1000;
  return s < 60 ? 'now' : s < 3600 ? Math.round(s / 60) + 'm' : s < 86400 ? Math.round(s / 3600) + 'h' : Math.round(s / 86400) + 'd';
}
const auDur = (r: AuOverviewRun): string => { const s = r.finished_at ? (new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()) / 1000 : NaN;
  return isNaN(s) || s < 0 ? '' : s < 60 ? Math.round(s) + 's' : s < 3600 ? Math.round(s / 60) + 'm' : Math.round(s / 3600) + 'h'; };
const auIcon = (name: string | undefined, cls = ''): string => `<span class="nav-icon${cls ? ' ' + cls : ''}" aria-hidden="true">${name}</span>`;

// -------------------------------------------------------------------------------------------- loading
async function auStatsLoad(state: AuOverviewView): Promise<void> {
  const range = state.range;
  let s: AuStatsResponse;
  try { s = await get<AuStatsResponse>('/v2/automations/stats?range=' + range); }
  catch (e) { s = {error: errorMessage(e, '')}; }
  if (AU !== state || state.range !== range) return;
  const n = s.totals?.buckets?.length || 0, per = new Map((s.automations || []).map((x): [string, AuPerStat] => [x.id, x]));
  state.stats = {range, at: Date.now(), error: s.error || '', n, per};
  auDrawTable();
}
function auAllQuery(): string {
  auHave(AU);
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
async function auAllLoad(state: AuOverviewView, more = false): Promise<void> {
  const key = auAllQuery();
  let r = state.all;
  if (!r || r.key !== key) r = state.all = {key, rows: [], next: null, loading: false, error: ''};
  if (r.loading || (more && !r.next) || (!more && r.done)) return;
  r.loading = true; r.error = '';
  if (!more) auDrawTable();
  try {
    const page = await get<AuRunsPage>('/v2/automations/runs?' + key + '&limit=' + AU_ALL_PAGE + (more ? '&before=' + encodeURIComponent(r.next || '') : ''));
    if (state.all !== r) return;
    r.rows = more ? r.rows.concat(page.runs || []) : page.runs || [];
    r.next = page.next || null; r.done = true;
  } catch (e) { r.error = errorMessage(e, ''); }
  r.loading = false;
  if (AU === state) auDrawTable();
}

// -------------------------------------------------------------------------------------------- drawing
function auRangeDraw(): void {
  auDrawn(AU);
  const seg = auEl(AU.root, '.au-range');
  seg.innerHTML = Object.keys(AU_RANGES).map(k => `<button type="button" data-range="${k}" aria-pressed="${auPage().range === k}">${k}</button>`).join('');
}
// The filters: every section open on a computer; on a phone each is a header that opens its list (AU.fopen).
function auFacetsDraw(): void {
  auDrawn(AU);
  if (AU.lst) return;   // Listening has a search and no filters
  const box = auEl(AU.root, '.au-facets'), list: AuItem[] = AU.list || [], f: AuFilters = AU.f, phone = auPhone();
  const count = (pred: (a: AuItem) => boolean): number => list.filter(pred).length;
  // A value no automation has is left out, unless it is the one picked (it then shows its 0).
  const btn = (attr: string, val: string, on: boolean, lead: string, label: string | undefined, n: number): string => !n && !on ? '' : `<button type="button" class="au-fb" ${attr}="${esc(val)}" aria-pressed="${on}">${lead}<span class="au-ft">${esc(label)}</span><span class="au-fn">${n}</span></button>`;
  const teams = [...new Set(list.map(auTeam).filter(Boolean))].sort((x, y) => x.localeCompare(y));
  const owners = [...new Set(list.map(a => a.owner_id).filter((o): o is string => Boolean(o)))].map((o): [string, string] => [o, auActorName(o)]).sort((x, y) => x[1].localeCompare(y[1]));
  // A section with nothing left to pick is not drawn.
  const sec = (key: AuSection, label: string, items: () => string): string => {
    const html = items();
    if (!html) return '';
    if (!phone) return `<div class="au-fg" role="group" aria-label="${label}"><div class="au-lb">${label}</div>${html}</div>`;
    const open = auPage().fopen.has(key), n = auPicked(f, key);
    return `<div class="au-fg" role="group" aria-label="${label}"><button type="button" class="au-fh" data-fsec="${key}" aria-expanded="${open}">`
      + `<span class="au-ft">${label}</span>${n ? `<span class="au-fhn">${n}</span>` : ''}${auIcon('chevron_right', 'au-chev')}</button>${open ? html : ''}</div>`;
  };
  box.innerHTML = sec('trig', 'Trigger', () => AU_KINDS.map(k => btn('data-ftrig', k, f.trig.has(k), auIcon(AU_KIND_ICONS[k]), AU_KIND_WORDS[k], count(a => auKind(a) === k))).join(''))
    + sec('status', 'Status', () => AU_STATES.map(([k, l, dot]) => btn('data-fstatus', k, f.status === k, `<span class="au-dot ${dot}" aria-hidden="true"></span>`, l, count(a => auState(a) === k))).join(''))
    + (teams.length ? sec('team', 'Team', () => teams.map(t => btn('data-fteam', t, f.team === t, auIcon('groups'), t, count(a => auTeam(a) === t))).join('')) : '')
    + sec('owner', 'Owner', () => owners.map(([o, name]) => btn('data-fowner', o, f.owner === o, personCircle(name, 16), name, count(a => a.owner_id === o))).join(''));
}
// The tabs and the filter button are drawn once (auShell) and moved between widths (auPlace); this sets their state and
// draws the bar's Group and Sort, or the Runs tab's Status.
function auCtlDraw(): void {
  auDrawn(AU);
  const r = AU.root, list = AU.tab === 'list', n = auActive(AU.f);
  const opts = (pairs: [string, string][], cur: string): string => pairs.map(([v, l]) => `<option value="${v}"${v === cur ? ' selected' : ''}>${l}</option>`).join('');
  for (const b of r.querySelectorAll<HTMLElement>('[data-tab]')) b.setAttribute('aria-selected', String((b.dataset.tab === 'list') === list));
  const fb = auEl(r, '[data-filters]'), badge = fb.querySelector<HTMLElement>('.au-fbn');
  fb.setAttribute('aria-expanded', String(r.classList.contains('filters-open')));
  auSure(badge, '.au-fbn');
  badge.hidden = !n; badge.textContent = String(n || '');
  auEl(r, '.au-sels').innerHTML = list
    ? `<label class="au-sel">Group <select data-group>${opts([['trigger', 'Trigger'], ['owner', 'Owner'], ['none', 'None']], AU.group)}</select></label>
      <label class="au-sel">Sort <select data-sort>${opts(AU_SORTS, AU.sort)}</select></label>`
    : `<label class="au-sel">Status <select data-rstatus>${opts([['', 'All'], ['failed', 'Failed'], ['waiting', 'Waiting'], ['running', 'Running'], ['done', 'Done']], AU.rstatus)}</select></label>`;
}
// A phone's header tools hold the search, which a computer shows in the filters: one search, moved when the width
// crosses over, so a typed search stays put. Listening's search sits in the header's tools at every width.
function auPlace(): void {
  auDrawn(AU);
  // G: the shell (auShell) always has these parts: the header's tools, the search and the rail.
  const r = AU.root, tools = auEl(r, '.au-ph > .ph-tools'), s = auEl(r, '.au-s');
  if (AU.lst || auPhone()) tools.prepend(s);
  else auEl(r, '.au-rail').prepend(s);
}
// The overview's parts: the filters, the bar above the table and the table (skipped while an automation is open).
function auDrawList(): void {
  if (!AU?.root) return;
  if (AU.lst) { auDrawTable(); return; }
  auFacetsDraw(); auCtlDraw(); auDrawTable();
}

function auSpark(b: AuBucket[], w = 84, h = 18): string {
  const n = b.length || 1, max = Math.max(1, ...b.map(d => d.ok + d.failed)), bw = w / n;
  return `<svg class="au-spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">${b.map((d, i) => {
    const v = d.ok + d.failed, hv = v ? Math.max(2, v / max * h) : 1, hf = d.failed ? Math.max(2, d.failed / max * h) : 0;
    const x = (i * bw + .5).toFixed(1), wd = Math.max(1, bw - 1.5).toFixed(1);
    return `<rect x="${x}" y="${h - hv}" width="${wd}" height="${hv}" rx="1" fill="${v ? 'var(--g5)' : 'var(--g3)'}"/>`
      + (hf ? `<rect x="${x}" y="${h - hf}" width="${wd}" height="${hf}" rx="1" fill="var(--fail)"/>` : '');
  }).join('')}</svg>`;
}
// A wake rule that names a step its type does not have never fires: one short line, on the row and in the detail.
function auWarn(a: {problems?: AuItem['problems'] | undefined}): string {
  const steps = [...new Set((a.problems || []).map(p => p.step))];
  return steps.length ? `<span class="au-warn"><span class="nav-icon" aria-hidden="true">warning</span>`
    + `${esc('Names a step its type does not have: ' + steps.join(', '))}</span>` : '';
}
function auPill(a: AuItem): string {
  const st = auState(a);
  // The built-ins with screens of their own (automations-maintenance.js, automations-heartbeat.js, settings-webhooks.ts) say so.
  return ([AU_MAINT, AU_HEART, AU_SYSJOBS, AU_HOOKS].includes(a.id) ? AU_BUILTIN_PILL : '') + (a.pending ? '<span class="pill">Pending</span>' : '')
    + (st === 'paused' ? '<span class="pill waiting">Paused</span>' : st === 'off' ? '<span class="pill">Off</span>'
      : st === 'hold' ? '<span class="pill waiting">On hold</span>' : '');
}
const AU_DOTS: Record<string, string> = {ok: 'ok', empty: 'ok', skipped: 'ok', failed: 'fail', waiting: 'wait'};
// The Next cell: when it next runs; for a due date, the due day over when its task opens.
function auNextHTML(a: AuItem): string {
  if (a.enabled === false || !a.next_run) return '';
  const at = new Date(a.next_run).getTime(), age = at > Date.now() ? auAge(a.next_run, true) : 'now';
  if (auKind(a) === 'due' && a.next_due_at) {
    const far = Date.parse(a.next_due_at) - Date.now() > 330 * 864e5;   // the year only once it could be mistaken
    return `<span class="au-nx"><span class="au-nx-d">Due ${esc(auDay(a.next_due_at, far))}</span><span>${age === 'now' ? 'opens now' : 'opens in ' + esc(age)}</span></span>`;
  }
  return age === 'now' ? 'due' : 'in ' + esc(age);
}
// On a phone (auPhone, automations.js) the table's columns come in another order: redraw when the width crosses over.
// The header's parts and the filters change places and shape too.
matchMedia('(max-width:760px)').addEventListener?.('change', () => {
  if (!AU?.root || !document.body.contains(AU.root)) return;
  auPlace(); auFacetsDraw(); auDrawTable();
});

function auTableHTML(): string {
  auHave(AU);
  const st: AuStats | null = AU.stats, n = st?.n || 0, zero: AuBucket[] = Array.from({length: n || 12}, () => ({ok: 0, failed: 0}));
  const stat = (a: AuItem): AuPerStat => st?.per.get(a.id) || {runs: 0, failed: 0, cost: 0, buckets: zero};
  const rows: AuRowT[] = auShown().map(a => ({a, s: stat(a)}));
  const last = (r: AuRowT): string => r.a.last_run?.started_at || '';
  const sorters: Record<string, (x: AuRowT, y: AuRowT) => number> = {last: (x, y) => last(y).localeCompare(last(x)), fail: (x, y) => y.s.failed - x.s.failed || y.s.runs - x.s.runs,
    runs: (x, y) => y.s.runs - x.s.runs, cost: (x, y) => y.s.cost - x.s.cost, name: (x, y) => String(x.a.name).localeCompare(String(y.a.name))};
  rows.sort(sorters[AU.sort] || sorters.last);
  const keyOf = (r: AuRowT): string => auPage().group === 'trigger' ? auKind(r.a) : auPage().group === 'owner' ? r.a.owner_id || '' : '';
  const groups: {k: string; rows: AuRowT[]}[] = [];
  for (const r of rows) { const k = keyOf(r); let g = groups.find(x => x.k === k); if (!g) groups.push(g = {k, rows: []}); g.rows.push(r); }
  // A kind this page does not know yet goes after the known ones, not before them.
  const rank = (k: string): number => { const i = AU_KINDS.indexOf(k); return i < 0 ? AU_KINDS.length : i; };
  if (AU.group === 'trigger') groups.sort((x, y) => rank(x.k) - rank(y.k));
  if (AU.group === 'owner') groups.sort((x, y) => auActorName(x.k).localeCompare(auActorName(y.k)));
  const label = (k: string): string => auPage().group === 'trigger' ? auIcon(AU_KIND_ICONS[k] || 'bolt') + esc(AU_KIND_WORDS[k] || auCap(k))
    : personCircle(auActorName(k), 16) + esc(auActorName(k));
  const money = (c: number): string => c >= 0.005 ? esc(auMoney(c)) : '';
  // Each column: its class (widths and folding in automations.css), heading and cell. A phone leads with what is read
  // first (name, last run, runs, failed) and scrolls sideways for the rest. A cell holding the usual value (no runs, no
  // failures, no cost, no next run) is empty, so the row that differs is the one that shows.
  const cols: Record<AuColKey, AuCol> = {
    name: ['au-tn', 'Name', ({a}) => `<div><a href="${auHref(a.id)}">${auIcon(AU_KIND_ICONS[auKind(a)] || 'bolt')}<span class="au-nm">${esc(a.name || 'Untitled')}</span></a>${auPill(a)}</div>${auWarn(a)}`],
    trigger: ['au-tt', 'Trigger', ({a}) => esc(auTrigLine(a))],
    owner: ['au-to', 'Owner', ({a}) => `<span title="${esc(auActorName(a.owner_id))}" aria-label="${esc(auActorName(a.owner_id))}">${personCircle(auActorName(a.owner_id), 18)}</span>`],
    last: ['au-cl', 'Last run', ({a}) => a.last_run ? `<span class="au-lr"><span class="au-dot ${AU_DOTS[a.last_run.status ?? ''] || 'idle'}" role="img" aria-label="${esc(AU_RUN_STATUS[a.last_run.status ?? ''] || a.last_run.status)}"></span>${esc(auAge(a.last_run.started_at))}</span>` : ''],
    next: ['au-cx muted', 'Next', ({a}) => auNextHTML(a)],
    trend: ['au-cs', 'Last ' + AU.range, ({s}) => s.runs ? auSpark(s.buckets || zero) : ''],
    runs: ['au-n au-cn', 'Runs', ({s}) => s.runs ? s.runs.toLocaleString() : ''],
    failed: ['au-n au-cf', 'Failed', ({s}) => s.failed || ''],
    cost: ['au-n au-cc', 'Cost', ({s}) => money(s.cost)],
  };
  // A column empty for every automation leaves, decided from the whole list once the range's counts are in (not from the
  // rows a search or filter shows, so typing never moves a column). Until then the last decision holds, so switching
  // range does not flash columns away and back. Dropped from the order, not hidden: the table's layout is fixed.
  if (st && st.range === AU.range) {
    const every: AuRowT[] = (AU.list || []).map((a: AuItem) => ({a, s: stat(a)}));
    AU.cols = AU_OPTIONAL_COLS.filter(k => every.some(r => cols[k][2](r) !== ''));
  }
  const kept = AU.cols;
  const order: AuColKey[] = (auPhone() ? ['name', 'last', 'runs', 'failed', 'trigger', 'owner', 'next', 'trend', 'cost'] as AuColKey[]
    : ['name', 'trigger', 'owner', 'last', 'next', 'trend', 'runs', 'failed', 'cost'] as AuColKey[])
    .filter(k => !kept || !AU_OPTIONAL_COLS.includes(k) || kept.includes(k));
  const cell = (k: AuColKey, r: AuRowT): string => {
    const [cls, , html] = cols[k], extra = k === 'failed' ? (r.s.failed ? ' au-bad' : ' muted') : '';
    return `<td class="${cls}${extra}"${k === 'trigger' ? ` title="${esc(auTrigLine(r.a))}"` : ''}>${html(r)}</td>`;
  };
  const tr = (r: AuRowT): string => `<tr class="au-row${r.a.enabled === false ? ' off' : ''}" data-row="${esc(r.a.id)}">${order.map(k => cell(k, r)).join('')}</tr>`;
  const body = rows.length ? groups.map(g => (auPage().group === 'none' ? '' :
    `<tr class="au-grp${auPage().closed.has(g.k) ? ' closed' : ''}" data-agrp="${esc(g.k)}" role="button" tabindex="0" aria-expanded="${!auPage().closed.has(g.k)}"><td colspan="${order.length}">${auIcon('expand_more', 'au-chev')}${label(g.k)}<span class="au-fn">${g.rows.length}</span></td></tr>`)
    + (auPage().closed.has(g.k) && auPage().group !== 'none' ? '' : g.rows.map(tr).join(''))).join('')
    : `<tr><td colspan="${order.length}" class="muted au-pad">${(AU.list || []).length ? 'No automations match these filters' : 'No automations'}</td></tr>`;
  return `<table class="au-tbl-t" style="--au-cols:${order.length}"><thead><tr>${order.map(k => `<th class="${cols[k][0].replace(' muted', '')}">${cols[k][1]}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table>
    ${st?.error ? `<p class="err au-pad">${esc(st.error)}</p>` : ''}`;
}

// The Runs tab: done and failed per bucket for what the filters show, then every run newest first.
function auChartHTML(): string {
  auHave(AU);
  const st: AuStats | null = AU.stats;
  if (!st || st.range !== AU.range || !st.n) return `<div class="au-chart"><p class="muted">${st?.error ? esc(st.error) : 'Loading…'}</p></div>`;
  const shown = new Set(auShown().map(a => a.id)), b: AuBucket[] = Array.from({length: st.n}, () => ({ok: 0, failed: 0}));
  for (const [id, s] of st.per) if (shown.has(id)) (s.buckets || []).forEach((x, i) => { if (b[i]) { b[i].ok += x.ok; b[i].failed += x.failed; } });
  const ok = b.reduce((t, x) => t + x.ok, 0), failed = b.reduce((t, x) => t + x.failed, 0);
  const W = 720, H = 64, bw = W / b.length, max = Math.max(1, ...b.map(x => x.ok + x.failed));
  const span = AU_RANGE_SECONDS[AU.range] ?? 0, ticks = [0, 1, 2, 3].map(q => auAge(new Date(Date.now() - span * 1000 * (1 - q / 4)).toISOString()));
  return `<div class="au-chart"><div class="au-chart-h"><span class="au-lb">Runs per ${AU_RANGES[AU.range]}</span><span class="au-sp"></span>
      <span class="au-key"><i class="ok"></i>${ok.toLocaleString()} done</span><span class="au-key"><i class="fail"></i>${failed.toLocaleString()} failed</span></div>
    <svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" preserveAspectRatio="none" role="img" aria-label="${ok} done, ${failed} failed in the last ${AU.range}">
      <line x1="0" x2="${W}" y1="${H - .5}" y2="${H - .5}" stroke="var(--line)"/>
      ${b.map((x, i) => { const t = (x.ok + x.failed) / max * (H - 4), f = x.failed / max * (H - 4), X = (i * bw + 2).toFixed(1), w = Math.max(1, bw - 4).toFixed(1);
        return (t - f > 0 ? `<rect x="${X}" y="${(H - t).toFixed(1)}" width="${w}" height="${(t - f).toFixed(1)}" fill="var(--g5)" rx="1.5"/>` : '')
          + (f ? `<rect x="${X}" y="${(H - f).toFixed(1)}" width="${w}" height="${f.toFixed(1)}" fill="var(--fail)" rx="1.5"/>` : ''); }).join('')}
    </svg><div class="au-ticks">${ticks.map(t => `<span>${esc(t)}</span>`).join('')}<span>now</span></div></div>`;
}
function auAllHTML(): string {
  auHave(AU);
  const r: AuAll | null = AU.all, shown = new Set(auShown().map(a => a.id));
  const rows = (r?.rows || []).filter(x => shown.has(x.automation_id));
  const kindOf = (id: string): string => auKind((auPage().list || []).find((a: AuItem) => a.id === id) || {});
  const table = rows.length ? `<table class="au-tbl-t au-all"><thead><tr><th class="au-cw">When</th><th class="au-cst">Status</th><th>Automation</th><th class="au-why">Started by</th><th class="au-out">Outcome</th>
      <th class="au-n au-cx">Steps</th><th class="au-n au-cx">Time</th><th class="au-n au-cc">Cost</th></tr></thead><tbody>
      ${rows.map(x => `<tr class="au-arow" role="button" tabindex="0" data-arun="${esc(x.id)}" data-aid="${esc(x.automation_id)}">
        <td class="muted" title="${esc(fmt(x.started_at))}">${esc(auAge(x.started_at) === 'now' ? 'now' : auAge(x.started_at) + ' ago')}</td>
        <td><span class="pill ${AU_RUN_PILLS[x.status ?? ''] || ''}">${esc(AU_RUN_STATUS[x.status ?? ''] || auCap(x.status))}</span></td>
        <td class="au-tn"><div>${auIcon(AU_KIND_ICONS[x.trigger || kindOf(x.automation_id)] || 'bolt')}<span class="au-nm">${esc(x.automation_name || '')}</span></div></td>
        <td class="au-why" title="${esc(x.started_by || '')}">${esc(x.started_by || '')}</td>
        <td class="au-out${x.status === 'failed' ? ' au-bad' : ''}" title="${esc(x.outcome || '')}">${esc(x.outcome || '')}${(x.count ?? 0) > 1 ? ` <span class="muted">×${x.count}</span>` : ''}</td>
        <td class="au-n au-cx muted">${x.step_count ?? '—'}</td><td class="au-n au-cx muted">${esc(auDur(x)) || '—'}</td>
        <td class="au-n au-cc muted">${x.cost ? esc(auMoney(x.cost)) : '—'}</td></tr>`).join('')}</tbody></table>`
    : `<p class="muted au-pad">${!r || r.loading ? 'Loading…' : r.error ? '' : 'No runs in the last ' + AU.range}</p>`;
  return auChartHTML() + table
    + (r?.next ? `<div class="au-more"><button class="ghost" type="button" data-all-more${r.loading ? ' disabled' : ''}>${r.loading ? 'Loading…' : 'More'}</button></div>` : '')
    + (r?.error ? `<p class="err au-pad">${esc(r.error)}</p>` : '');
}
function auDrawTable(): void {
  if (!AU?.root || AU.id) return;
  const box = auEl(AU.root, '.au-tbl');
  if (AU.listError) { box.innerHTML = `<p class="err au-pad">${esc(AU.listError)}</p>`; return; }
  if (AU.list === null) { box.innerHTML = '<p class="muted au-pad">Loading…</p>'; return; }
  patchLiveHTML(box, AU.lst ? lstListHTML(AU) : AU.tab === 'runs' ? auAllHTML() : auTableHTML());   // a fresh read that changed nothing moves nothing
  box.scrollTop = AU.scroll[AU.tab] || 0;
}

// The list is shown: what it reads (the counts for the range, the Runs page) and where it was scrolled.
function auOverviewShow(state: AuOverviewView): void {
  if (auPage().lst) { auDrawTable(); return; }
  if (!state.stats || state.stats.range !== state.range || Date.now() - state.stats.at > 60000) void auStatsLoad(state);
  if (state.tab === 'runs') void auAllLoad(state);
  auRangeDraw(); auDrawList();
}

// -------------------------------------------------------------------------------------------- events
function auFiltered(): void {
  auHave(AU);
  AU.scroll.list = AU.scroll.runs = 0;
  if (AU.id) { location.hash = auBase(); return; }   // a filter shows the list again
  auDrawList();
  if (AU.tab === 'runs') { clearTimeout(AU.allSoon); AU.allSoon = setTimeout(() => { if (AU) void auAllLoad(AU); }, 250); }
}
function auRangeSet(r: string): void {
  if (!AU_RANGES[r] || !auIs(AU, 'the Automations page') || AU.range === r) return;
  AU.range = r;
  try { localStorage.setItem(AU_RANGE_KEY, r); } catch { /* kept for this visit only */ }
  AU.stats = null; AU.all = null;
  auRangeDraw();
  void auStatsLoad(AU);
  if (AU.tab === 'runs') void auAllLoad(AU);
  auDrawTable();
}
// Clicks on the list's parts; true when one was handled.
function auOverviewClick(t: Element): boolean {
  let m: HTMLElement | null;
  auHave(AU);
  const f = AU.f, one = (key: 'status' | 'team' | 'owner', v: string) => { f[key] = f[key] === v ? '' : v; auFiltered(); };
  if ((m = t.closest<HTMLElement>('[data-range]'))) { auRangeSet(m.dataset.range || ''); return true; }
  if ((m = t.closest<HTMLElement>('[data-ftrig]'))) { const k = m.dataset.ftrig || ''; if (f.trig.has(k)) f.trig.delete(k); else f.trig.add(k); auFiltered(); return true; }
  if ((m = t.closest<HTMLElement>('[data-fstatus]'))) { one('status', m.dataset.fstatus || ''); return true; }
  if ((m = t.closest<HTMLElement>('[data-fteam]'))) { one('team', m.dataset.fteam || ''); return true; }
  if ((m = t.closest<HTMLElement>('[data-fowner]'))) { one('owner', m.dataset.fowner || ''); return true; }
  if ((m = t.closest<HTMLElement>('[data-tab]'))) {
    AU.tab = m.dataset.tab ?? ''; auCtlDraw(); auDrawTable();
    if (AU.tab === 'runs') void auAllLoad(AU);
    return true;
  }
  if ((m = t.closest<HTMLElement>('[data-filters]'))) {
    // Opening starts with the sections that have a filter on open and the rest closed.
    const open = auNeed(AU.root, '#au-root').classList.toggle('filters-open');
    if (open) { AU.fopen = new Set((['trig', 'status', 'team', 'owner'] as AuSection[]).filter(k => auPicked(f, k))); auFacetsDraw(); }
    m.setAttribute('aria-expanded', String(open));
    return true;
  }
  if ((m = t.closest<HTMLElement>('[data-fsec]'))) {
    const k = m.dataset.fsec || '';
    if (AU.fopen.has(k)) AU.fopen.delete(k); else AU.fopen.add(k);
    auFacetsDraw();
    auNeed(AU.root, '#au-root').querySelector<HTMLElement>(`[data-fsec="${k}"]`)?.focus();
    return true;
  }
  if ((m = t.closest<HTMLElement>('[data-agrp]'))) { const k = m.dataset.agrp || ''; if (AU.closed.has(k)) AU.closed.delete(k); else AU.closed.add(k); auDrawTable(); return true; }
  if ((m = t.closest<HTMLElement>('[data-all-more]'))) { void auAllLoad(AU, true); return true; }
  if ((m = t.closest<HTMLElement>('tr[data-arun]'))) {
    const id = m.dataset.arun || '';
    if (id.startsWith('mnt_')) AU.mtOpenRun = id.slice(4);
    else if (!id.startsWith('sys_') && !id.startsWith('hb_')) AU.openRun = id;   // a Heartbeat run opens its log
    location.hash = auHref(m.dataset.aid, 'runs');
    return true;
  }
  if ((m = t.closest<HTMLElement>('tr[data-row]')) && !t.closest('a')) { location.hash = auHref(m.dataset.row); return true; }
  return false;
}
function auOverviewChange(e: Event): void {
  const t = e.target;
  if (!(t instanceof HTMLSelectElement)) return;   // the list's changes are its selects
  if (t.matches('[data-group]')) { auHave(AU); AU.group = t.value; AU.closed.clear(); auDrawTable(); }
  else if (t.matches('[data-sort]')) { auHave(AU); AU.sort = t.value; auDrawTable(); }
  else if (t.matches('[data-rstatus]')) { auHave(AU); AU.rstatus = t.value; AU.scroll.runs = 0; void auAllLoad(AU); }
}
function auOverviewWire(root: HTMLElement): void {
  const q = root.querySelector<HTMLInputElement>('.au-q'), box = root.querySelector<HTMLElement>('.au-tbl');
  if (!q || !box) return;   // the page always has both; without them there is nothing to wire
  q.addEventListener('input', () => { auPage().f.q = q.value.trim().toLowerCase(); auFiltered(); });
  root.addEventListener('change', auOverviewChange);
  box.addEventListener('scroll', () => {
    if (!AU || AU.id) return;
    AU.scroll[AU.tab] = box.scrollTop;
    // Scrolling near the end of the Runs tab reads the next page.
    if (AU.tab === 'runs' && AU.all?.next && !AU.all.loading && box.scrollTop + box.clientHeight > box.scrollHeight - 200) void auAllLoad(AU, true);
  }, {passive: true});
}
