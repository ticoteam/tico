/* ui/app/overview-factories.ts — Overview › Factories: the flow map. Each factory is a box, its pipelines sit in the order
   work moves through them, a line joins two pipelines when a stage hands tickets on (thicker for more tickets in the
   period), a circle is where work comes in. Tap a pipeline to open it on the Tasks page.
   The read is GET /api/v2/pipelines/overview?period=1d|7d|30d (backend/pipeline_overview.py); this file only draws it.
   On a phone the factories stack and the counts sit between the boxes.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

interface OfPipeline { id: string; name: string; goal: string | null; manager_id: string | null; factory_id: string | null; cards: number; overdue: number; for_you: number; live: number; }
interface OfFactory { id: string; name: string; goal: string | null; manager_id: string | null; done: number; overdue: number; live: number; }
interface OfEdge { from: string; to: string; count: number; median_age_minutes: number | null; }
interface OfInput { label: string; count: number; to: string; }
interface OfMap { factories: OfFactory[]; pipelines: OfPipeline[]; edges: OfEdge[]; inputs: OfInput[]; }

const OF_PERIODS: [string, string][] = [['1d', 'Day'], ['7d', 'Week'], ['30d', 'Month']];
const OF_PERIOD_KEY = 'tico.factories.period';
let OF_RUN = 0;
let OF_ABORT: AbortController | null = null;

// The period this viewer last chose. Browser storage may be blocked, so a miss is the week.
function ofPeriodRead(): string {
  try {
    const kept = localStorage.getItem(OF_PERIOD_KEY);
    if (kept && OF_PERIODS.some(([p]) => p === kept)) return kept;
  } catch { /* storage blocked: the default */ }
  return '7d';
}
function ofPeriodKeep(period: string): void {
  try { localStorage.setItem(OF_PERIOD_KEY, period); } catch { /* storage blocked: not kept */ }
}

const ofPipelineHref = (id: string): string => `${TASKS}?type=${encodeURIComponent(id)}&view=list`;
const ofAge = (minutes: number): string => minutes >= 1440 ? `${Math.round(minutes / 1440)}d` : minutes >= 60 ? `${Math.round(minutes / 60)}h` : `${minutes}m`;
const OF_CHAT_ICON = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 5h16v11H9l-5 4z"/></svg>';
// The chat icon for a manager: nothing when the pipeline or factory has none (no dead button).
const ofChatHTML = (manager: string | null, what: string): string => manager
  ? `<button type="button" class="ghost of-chat" data-of-chat="${esc(manager)}" data-of-what="${esc(what)}" aria-label="Chat with ${esc(actorLabel(manager))}, ${esc(what)} manager" title="Chat with ${esc(actorLabel(manager))}">${OF_CHAT_ICON}</button>` : '';
const ofPhone = (): boolean => window.matchMedia('(max-width: 720px)').matches;

// "38 · 2d": the count that moved and the median time they spent in the source pipeline.
const ofEdgeText = (e: OfEdge): string => e.median_age_minutes == null ? String(e.count) : `${e.count} · ${ofAge(e.median_age_minutes)}`;

function ofNodeHTML(p: OfPipeline, inputs: OfInput[], out: OfEdge[], names: Map<string, string>): string {
  const stats = [
    `<span class="of-n tnum">${p.cards}</span> open`,
    p.overdue ? `<span class="of-late tnum">${p.overdue} Late</span>` : '',
    p.live ? `<span class="of-run tnum">${p.live} running</span>` : '',
  ].filter(Boolean).join(' · ');
  const chips = inputs.map(i => `<span class="of-in-chip tnum">${esc(i.label)} ${i.count}</span>`).join('');
  const onward = out.map(e => `<span class="of-onward tnum">${esc(ofEdgeText(e))} to ${esc(names.get(e.to) || '')}</span>`).join('');
  return `<div class="of-node" data-of-pl="${esc(p.id)}"><a class="of-link" href="${ofPipelineHref(p.id)}"${p.goal ? ` title="${esc(p.goal)}"` : ''}>
    <span class="of-name">${esc(p.name)}</span>
    <span class="of-stats">${stats}</span>
    ${p.for_you ? `<span class="of-you tnum">${p.for_you} for you</span>` : ''}
    <span class="of-phone-only">${chips}${onward}</span></a>${ofChatHTML(p.manager_id, p.name)}</div>`;
}

function ofHTML(map: OfMap): string {
  const names = new Map(map.pipelines.map(p => [p.id, p.name] as [string, string]));
  const groups: {id: string; name: string; manager: string | null; sub: string; pipelines: OfPipeline[]}[] = map.factories.map(f => ({
    id: f.id, name: f.name, manager: f.manager_id,
    sub: [`<span class="tnum">${f.done}</span> done`, f.overdue ? `<span class="of-late tnum">${f.overdue} Late</span>` : '', f.live ? `<span class="of-run tnum">${f.live} running</span>` : ''].filter(Boolean).join(' · '),
    pipelines: map.pipelines.filter(p => p.factory_id === f.id),
  }));
  const loose = map.pipelines.filter(p => !p.factory_id);
  if (loose.length) groups.push({id: '', name: groups.length ? 'Other pipelines' : 'Pipelines', manager: null, sub: '', pipelines: loose});
  const live = groups.filter(g => g.pipelines.length);
  const inputsFor = (id: string): OfInput[] => map.inputs.filter(i => i.to === id);
  const box = (g: typeof groups[number], n: number): string => `<section class="of-factory" aria-label="${esc(g.name)}">
      <header><h2>${esc(g.name)}</h2><span class="of-sub">${g.sub}</span>${ofChatHTML(g.manager, g.name)}</header>
      <div class="of-row">${g.pipelines.map(p => ofNodeHTML(p, inputsFor(p.id), map.edges.filter(e => e.from === p.id), names)).join('')}</div></section>${n < live.length - 1 ? '<div class="of-gap" aria-hidden="true"></div>' : ''}`;
  const inputs = map.inputs.map(i => `<span class="of-input" data-of-in="${esc(i.to)}"><span class="of-dot tnum">${i.count}</span><span class="of-in-l">${esc(i.label)}</span></span>`).join('');
  return `${inputs ? `<div class="of-inputs" aria-label="Where work comes in">${inputs}</div>` : ''}
    <div class="of-stage" id="of-stage"><svg class="of-lines" id="of-lines" aria-hidden="true"></svg>
      <div class="of-factories">${live.map(box).join('')}</div></div>`;
}

// Draw the lines once the boxes have a size: one curve per edge, input circles to their pipeline.
function ofLines(map: OfMap): void {
  const stage = document.getElementById('of-stage'), svg = document.getElementById('of-lines');
  if (!stage || !svg) return;
  if (ofPhone()) { svg.innerHTML = ''; return; }
  const origin = stage.getBoundingClientRect();
  svg.setAttribute('width', String(origin.width)); svg.setAttribute('height', String(origin.height));
  const rect = (id: string): DOMRect | null => stage.querySelector(`[data-of-pl="${CSS.escape(id)}"]`)?.getBoundingClientRect() ?? null;
  const max = Math.max(1, ...map.edges.map(e => e.count), ...map.inputs.map(i => i.count));
  const width = (n: number): string => (1.5 + 4.5 * n / max).toFixed(2);
  let paths = '';
  for (const e of map.edges) {
    const a = rect(e.from), b = rect(e.to);
    if (!a || !b) continue;
    const back = b.left <= a.left;
    const x1 = (back ? a.left + a.width / 2 : a.right) - origin.left, y1 = (back ? a.bottom : a.top + a.height / 2) - origin.top;
    const x2 = (back ? b.left + b.width / 2 : b.left) - origin.left, y2 = (back ? b.bottom : b.top + b.height / 2) - origin.top;
    const d = back ? `M${x1},${y1} C${x1},${y1 + 48} ${x2},${y2 + 48} ${x2},${y2}` : `M${x1},${y1} C${x1 + 40},${y1} ${x2 - 40},${y2} ${x2},${y2}`;
    paths += `<path class="of-edge" d="${d}" style="stroke-width:${width(e.count)}"></path>`;
    const mx = (x1 + x2) / 2, my = back ? Math.max(y1, y2) + 36 : (y1 + y2) / 2 - 8;
    paths += `<text class="of-edge-l tnum" x="${mx.toFixed(1)}" y="${my.toFixed(1)}" text-anchor="middle">${esc(ofEdgeText(e))}</text>`;
  }
  for (const i of map.inputs) {
    const dot = document.querySelector(`[data-of-in="${CSS.escape(i.to)}"] .of-dot`)?.getBoundingClientRect(), b = rect(i.to);
    if (!dot || !b) continue;
    const x1 = dot.right - origin.left, y1 = dot.top + dot.height / 2 - origin.top, x2 = b.left - origin.left, y2 = b.top + 14 - origin.top;
    paths += `<path class="of-edge of-edge-in" d="M${x1},${y1} C${x1 + 40},${y1} ${x2 - 40},${y2} ${x2},${y2}" style="stroke-width:${width(i.count)}"></path>`;
  }
  svg.innerHTML = paths;
}

async function ofLoad(period: string): Promise<void> {
  const body = document.getElementById('of-body');
  if (!body) return;
  const run = ++OF_RUN;
  try {
    const map: OfMap = await get(`/v2/pipelines/overview?period=${encodeURIComponent(period)}`, {fresh: true});
    if (run !== OF_RUN || !document.getElementById('of-body')) return;
    if (!map.pipelines.length) { body.innerHTML = '<p class="of-empty muted">No pipelines yet.</p>'; return; }
    body.innerHTML = ofHTML(map);
    // The inputs sit in their own row on the desktop map; a phone shows them on the pipeline they feed.
    const draw = (): void => ofLines(map);
    draw();
    requestAnimationFrame(draw);
    const stage = document.getElementById('of-stage');
    if (stage && OF_ABORT) {
      const ro = new ResizeObserver(draw);
      ro.observe(stage);
      OF_ABORT.signal.addEventListener('abort', () => ro.disconnect());
    }
  } catch {
    if (run === OF_RUN) body.innerHTML = '<p class="of-empty muted">Could not load the map.</p>';
  }
}

function pageOverviewFactories(): void {
  OF_ABORT?.abort();
  OF_ABORT = new AbortController();
  const main = $('#main'); main.classList.add('overview-layout');
  let period = ofPeriodRead();
  main.innerHTML = `<section class="overview-page of-page" aria-label="Factories">
    <div class="overview-tabs"><h1>Company</h1>${dashTabsHTML('factories')}</div>
    <div class="of-bar"><div class="og-seg" role="group" aria-label="Period" id="of-period">${OF_PERIODS.map(([p, l]) =>
      `<button type="button" data-of-period="${p}" aria-pressed="${p === period}">${l}</button>`).join('')}</div></div>
    <div class="of-main"><div class="of-body" id="of-body">${skelRows(3, {dot: false})}</div>
      <aside class="of-rail" id="of-rail" aria-label="Chat" hidden></aside></div></section>`;
  document.getElementById('of-rail')?.addEventListener('click', ev => {
    const t = eventElement(ev);
    if (t?.closest('[data-of-rail-close]')) ofRailShut();
    else if (t?.closest('[data-of-rail-open]')) void ofRailOpenChat();
  });
  document.getElementById('of-body')?.addEventListener('click', ev => {
    const chat = eventElement(ev)?.closest<HTMLElement>('[data-of-chat]');
    if (chat?.dataset['ofChat']) ofRailShow(chat.dataset['ofChat'], chat.dataset['ofWhat'] ?? '');
  });
  document.getElementById('of-period')?.addEventListener('click', ev => {
    const pick = eventElement(ev)?.closest<HTMLElement>('[data-of-period]');
    if (!pick?.dataset['ofPeriod'] || pick.dataset['ofPeriod'] === period) return;
    period = pick.dataset['ofPeriod'];
    ofPeriodKeep(period);
    for (const b of document.querySelectorAll<HTMLElement>('[data-of-period]')) b.setAttribute('aria-pressed', String(b.dataset['ofPeriod'] === period));
    void ofLoad(period);
  });
  void ofLoad(period);
}

// ---- the chat rail: the manager of the factory or pipeline last tapped. Another icon swaps it. A phone shows it as a
// full-screen sheet (overview-factories.css). It opens the manager's existing direct thread; it builds no chat of its own.
let OF_CHAT = '';
function ofRailShow(manager: string, what: string): void {
  const rail = document.getElementById('of-rail');
  if (!rail) return;
  OF_CHAT = manager;
  rail.hidden = false;
  rail.innerHTML = `<header><div><b>${esc(actorLabel(manager))}</b><span class="muted">${esc(what)} manager</span></div>
    <button type="button" class="ghost" data-of-rail-close aria-label="Close">✕</button></header>
    <div class="of-rail-body"><button type="button" class="primary" data-of-rail-open>Open chat</button></div>`;
  for (const b of document.querySelectorAll<HTMLElement>('[data-of-chat]')) b.setAttribute('aria-pressed', String(b.dataset['ofChat'] === manager));
}
function ofRailShut(): void {
  const rail = document.getElementById('of-rail');
  if (rail) rail.hidden = true;
  OF_CHAT = '';
  for (const b of document.querySelectorAll<HTMLElement>('[data-of-chat]')) b.removeAttribute('aria-pressed');
}
async function ofRailOpenChat(): Promise<void> {
  if (!OF_CHAT) return;
  try {
    const r = await get(`/v2/threads/dm?with=${encodeURIComponent(OF_CHAT)}`, {fresh: true});
    const id: string | undefined = (r?.thread ?? r)?.id;
    if (id) { ofRailShut(); location.hash = threadHref(id); }
  } catch (e) { toast(e instanceof Error && e.message ? e.message : 'Could not open the chat', true); }
}
