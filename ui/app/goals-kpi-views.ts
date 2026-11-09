/* ui/app/goals-kpi-views.ts — The KPI views under the Goals tab (#/strategy/goals): a switch Tree · Loops · Journey over
   the growth KPIs from GET /api/v2/growth/kpis. Tree is the North Star with its inputs under it, then the guardrails;
   Loops groups KPIs by loop name in step order; Journey puts them in columns by stage. A KPI the caller may edit
   (may_edit on the read, the KPI update route's check) opens its place in them: what it feeds, its role, stage and
   loop (POST /api/v2/kpis/{id}). The chosen view is remembered in this browser. ?kpi=<id> (a stat on the Growth page)
   opens Tree and brings that KPI into view.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const GKV_VIEWS: Array<[string, string]> = [['tree', 'Tree'], ['loops', 'Loops'], ['journey', 'Journey']];
const GKV_ROLES: Array<[string, string]> = [['north_star', 'North Star'], ['input', 'Input'], ['guardrail', 'Guardrail']];
const GKV_KEY = 'tico.goals.kpiView';
// A KPI as GET /api/v2/growth/kpis lists it (only the fields these views read).
interface GkvKpi { id: string; name: string; parent_kpi_id: string | null; growth_role: string | null; stage: string | null; loop_name: string | null; loop_step: number | null; target: number | null; unit: string; latest: number | null; may_edit?: boolean }
interface GkvKpisResponse { kpis?: GkvKpi[] }
interface GkvNode extends GkvKpi { depth: number }
interface GkvState { host: HTMLElement; kpis: GkvKpi[]; view: string; find: string }
type GkvPlaceControls = {parent_kpi_id: HTMLSelectElement; growth_role: HTMLSelectElement; stage: HTMLSelectElement; loop_name: HTMLInputElement; loop_step: HTMLInputElement};
let GKV: GkvState | null = null;           // the drawn views: {host, kpis, view, find}

// The KPIs below `rootId` in tree order: each input, then its own inputs.
function gkvTree(kpis: GkvKpi[], rootId: string): GkvNode[] {
  const out: GkvNode[] = [], seen = new Set<string>([rootId]);
  const walk = (pid: string, depth: number) => { for (const k of kpis.filter(k => k.parent_kpi_id === pid && !seen.has(k.id))) { seen.add(k.id); out.push({...k, depth}); walk(k.id, depth + 1); } };
  walk(rootId, 0);
  return out;
}
// After every route (growthStop): off Goals, forget the views and shut a KPI's dialog, so it can't save over another page.
function gkvStop(route: string) {
  if (!GKV || rdUnder(route, goalsBase())) return;
  GKV = null;
  document.querySelectorAll<HTMLDialogElement>('dialog.gr-dlg').forEach(d => d.close());
}
// Mounts the views in `host` (pageGoals); hidden until there is a KPI to show.
function goalsKpiViews(host: HTMLElement) {
  let view = 'tree';
  try { view = localStorage.getItem(GKV_KEY) || 'tree'; } catch { /* not kept */ }
  const find = new URLSearchParams((S.route || '').split('?')[1] || '').get('kpi') || '';
  const state: GkvState = GKV = {host, kpis: [], view: find ? 'tree' : GKV_VIEWS.some(([v]) => v === view) ? view : 'tree', find};
  void gkvRead(state);
}
async function gkvRead(state: GkvState) {
  const r = await get<GkvKpisResponse>('/v2/growth/kpis', {fresh: true}).catch(() => null);
  if (GKV !== state || !state.host.isConnected) return;
  state.kpis = r?.kpis || [];
  gkvDraw(state);
  if (state.find) {
    const hit = state.host.querySelector<HTMLElement>(`[data-kpi="${CSS.escape(state.find)}"]`);
    hit?.classList.add('gkv-hit');
    hit?.scrollIntoView({block: 'center'});
    state.find = '';
  }
}
const gkvK = (k: GkvKpi, cls = '') => {
  const inner = `<span class="gkv-n">${esc(k.name)}</span><span class="gkv-v">${esc(grKpiVal(k))}${k.target != null ? `<span class="muted"> / ${esc(kpiNum(k.target, k.unit))}</span>` : ''}</span>`;
  return k.may_edit === true ? `<button type="button" class="gkv-k ${cls}" data-kpi="${esc(k.id)}" data-kpi-place="${esc(k.id)}">${inner}</button>` : `<div class="gkv-k ${cls}" data-kpi="${esc(k.id)}">${inner}</div>`;
};
const gkvNone = '<p class="muted gkv-none">None</p>';
function gkvTreeHtml(kpis: GkvKpi[]) {
  const ns = kpis.find(k => k.growth_role === 'north_star'), tree = ns ? gkvTree(kpis, ns.id) : [];
  const inTree = new Set([ns?.id, ...tree.map(k => k.id)]);
  const guards = kpis.filter(k => k.growth_role === 'guardrail' && !inTree.has(k.id));
  const rest = kpis.filter(k => !inTree.has(k.id) && k.growth_role !== 'guardrail');
  const grp = (label: string, list: GkvKpi[]) => list.length ? `<div class="gkv-grp"><h3 class="gr-k">${esc(label)}</h3>${list.map(k => gkvK(k, label === 'Guardrails' ? 'gkv-guard' : '')).join('')}</div>` : '';
  const html = (ns ? `<div class="gkv-tree">${gkvK(ns, 'gkv-ns')}${tree.map(k => `<div class="gkv-in" style="--d:${k.depth}">${gkvK(k, k.growth_role === 'guardrail' ? 'gkv-guard' : '')}</div>`).join('')}</div>` : '')
    // KPIs with no place yet show only to someone who can place one.
    + grp('Guardrails', guards) + (rest.some(k => k.may_edit === true) ? grp('Other', rest) : '');
  return html || gkvNone;
}
function gkvLoopsHtml(kpis: GkvKpi[]) {
  const loops = new Map<string | null, GkvKpi[]>();
  for (const k of kpis.filter(k => k.loop_name)) { let steps = loops.get(k.loop_name); if (!steps) { steps = []; loops.set(k.loop_name, steps); } steps.push(k); }
  return [...loops].map(([name, steps]) => {
    steps.sort((a, b) => (a.loop_step ?? 1e9) - (b.loop_step ?? 1e9));
    return `<section class="gkv-loop"><h3 class="gr-k">${esc(name)}</h3><ol class="gkv-steps">${steps.map(k => `<li>${gkvK(k)}</li>`).join('')}</ol></section>`;
  }).join('') || gkvNone;
}
function gkvJourneyHtml(kpis: GkvKpi[]) {
  if (!kpis.some(k => GR_STAGES.some(([id]) => id === k.stage))) return gkvNone;
  return `<div class="gkv-journey">${GR_STAGES.map(([id, label]) => `<section class="gkv-col"><h3 class="gr-k">${esc(label)}</h3>
    ${kpis.filter(k => k.stage === id).map(k => gkvK(k)).join('')}</section>`).join('')}</div>`;
}
function gkvDraw(state: GkvState) {
  const host = state.host;
  host.hidden = !state.kpis.length;
  if (!state.kpis.length) { host.innerHTML = ''; return; }
  const body = state.view === 'loops' ? gkvLoopsHtml(state.kpis) : state.view === 'journey' ? gkvJourneyHtml(state.kpis) : gkvTreeHtml(state.kpis);
  host.innerHTML = `<div class="gkv-h"><h2 class="strat-k">KPIs</h2><span class="spacer"></span>${grSeg('gkv', 'KPI view', GKV_VIEWS, state.view)}</div>
    <div class="gkv-body" data-gkv="${esc(state.view)}">${body}</div>`;
  const seg = host.querySelector<HTMLElement>('[data-gr-seg="gkv"]');
  if (!seg) return;
  seg.onclick = ev => {
    const b = eventElement(ev)?.closest<HTMLButtonElement>('button[data-v]'); if (!b || b.dataset.v === state.view) return;
    state.view = String(b.dataset.v);
    try { localStorage.setItem(GKV_KEY, state.view); } catch { /* not kept */ }
    gkvDraw(state);
  };
  host.querySelectorAll<HTMLElement>('[data-kpi-place]').forEach(b => { b.onclick = () => gkvPlace(state, state.kpis.find(k => k.id === b.dataset.kpiPlace)); });
}
// A KPI's place: what it feeds (never itself or anything under it), its role, stage and loop.
function gkvPlace(state: GkvState, k: GkvKpi | undefined) {
  if (!k) return;
  const below = new Set([k.id, ...gkvTree(state.kpis, k.id).map(x => x.id)]);
  grDialog(k.name, `${grField('Feeds', `<select name="parent_kpi_id">${grOpts(state.kpis.filter(x => !below.has(x.id)).map(x => [x.id, x.name]), k.parent_kpi_id, '—')}</select>`)}
    <div class="gr-pair">${grField('Role', `<select name="growth_role">${grOpts(GKV_ROLES, k.growth_role, '—')}</select>`)}
    ${grField('Stage', `<select name="stage">${grOpts(GR_STAGES, k.stage, '—')}</select>`)}</div>
    <div class="gr-pair">${grField('Loop', `<input name="loop_name" maxlength="80" value="${esc(k.loop_name || '')}">`)}
    ${grField('Step', `<input name="loop_step" type="number" min="0" step="1" value="${esc(k.loop_step ?? '')}">`)}</div>`, 'Save', async form => {
    const v = (n: keyof GkvPlaceControls) => (form.elements as HTMLFormControlsCollection & GkvPlaceControls)[n].value.trim();
    await post(`/v2/kpis/${encodeURIComponent(k.id)}`, {parent_kpi_id: v('parent_kpi_id') || null, growth_role: v('growth_role') || null,
      stage: v('stage') || null, loop_name: v('loop_name') || null, loop_step: v('loop_step') === '' ? null : Number(v('loop_step'))});
    toast('Saved');
    void gkvRead(state);
  });
}
