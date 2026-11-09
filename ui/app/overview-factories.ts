/* ui/app/overview-factories.ts — Overview › Factories: the flow map. Each factory is a box, its pipelines sit in the order
   work moves through them, a line joins two pipelines when a stage hands tickets on (thicker for more tickets in the
   period), a circle is where work comes in. Tap a pipeline to open it on the Tasks page. A chat icon on a factory or a
   pipeline opens its manager's chat in the rail beside the map; the rail's chips switch between managers.
   The read is GET /api/v2/pipelines/overview?period=1d|7d|30d (backend/pipeline_overview.py); this file only draws it.
   On a phone the factories stack and the counts sit between the boxes.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

interface OfPipeline { id: string; name: string; goal: string | null; manager_id: string | null; factory_id: string | null; cards: number; overdue: number; for_you: number; live: number; }
interface OfScoreboard { merges_per_hour: number; queued_to_merged_median_minutes: number | null; merged_to_production_median_minutes: number | null; queue_size: number; }
interface OfFactory { id: string; name: string; goal: string | null; manager_id: string | null; done: number; overdue: number; live: number; scoreboard?: OfScoreboard; }
interface OfEdge { from: string; to: string; mode: string; count: number; median_age_minutes: number | null; }
/** A cycle of pipelines: laps completed round it, and the median lap in minutes (null under three laps). */
interface OfLoop { pipelines: string[]; laps: number; median_lap_minutes: number | null; }
interface OfInput { label: string; count: number; to: string; }
interface OfMap { factories: OfFactory[]; pipelines: OfPipeline[]; edges: OfEdge[]; inputs: OfInput[]; loops?: OfLoop[]; }
/** One box the map draws: a factory with its pipelines, or the pipelines in no factory (id ''). */
interface OfGroup { id: string; name: string; manager: string | null; sub: string; pipelines: OfPipeline[]; }
/** A factory or pipeline with a manager: a chip in the rail and a chat icon on the map. The key is f:<id> or p:<id>. */
interface OfChatNode { key: string; name: string; manager: string; }

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

const ofPipelineHref = (id: string): string => `${TASKS}?type=${encodeURIComponent(id)}&view=board`;
const ofAge = (minutes: number): string => minutes >= 1440 ? `${Math.round(minutes / 1440)}d` : minutes >= 60 ? `${Math.round(minutes / 60)}h` : `${minutes}m`;
const OF_CHAT_ICON = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 5h16v11H9l-5 4z"/></svg>';
// The chat icon for a factory or pipeline: nothing when it has no manager (no dead button).
const ofChatHTML = (key: string, manager: string | null, name: string): string => {
  if (!manager) return '';
  const who = actorLabel(pipelineManagerActor(manager));
  return `<button type="button" class="ghost of-chat" data-of-chat="${esc(key)}" aria-pressed="false" aria-label="Chat with ${esc(who)}, ${esc(name)} manager" title="Chat with ${esc(who)}">${OF_CHAT_ICON}</button>`;
};
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
    <span class="of-phone-only">${chips}${onward}</span></a>${ofChatHTML('p:' + p.id, p.manager_id, p.name)}</div>`;
}

// The boxes the map draws, in order: each factory with its pipelines, then the pipelines in no factory. A box with no
// pipeline is not drawn.
function ofGroups(map: OfMap): OfGroup[] {
  const groups: OfGroup[] = map.factories.map(f => ({
    id: f.id, name: f.name, manager: f.manager_id,
    sub: [`<span class="tnum">${f.done}</span> done`, f.overdue ? `<span class="of-late tnum">${f.overdue} Late</span>` : '', f.live ? `<span class="of-run tnum">${f.live} running</span>` : ''].filter(Boolean).join(' · '),
    pipelines: map.pipelines.filter(p => p.factory_id === f.id),
  }));
  const loose = map.pipelines.filter(p => !p.factory_id);
  if (loose.length) groups.push({id: '', name: groups.length ? 'Other pipelines' : 'Pipelines', manager: null, sub: '', pipelines: loose});
  return groups.filter(g => g.pipelines.length);
}

// The rail's chips, in the order the map draws them: a factory, then its pipelines.
function ofChatNodes(groups: OfGroup[]): OfChatNode[] {
  const nodes: OfChatNode[] = [];
  for (const g of groups) {
    if (g.manager) nodes.push({key: 'f:' + g.id, name: g.name, manager: pipelineManagerActor(g.manager)});
    for (const p of g.pipelines) if (p.manager_id) nodes.push({key: 'p:' + p.id, name: p.name, manager: pipelineManagerActor(p.manager_id)});
  }
  return nodes;
}

function ofHTML(map: OfMap): string {
  const names = new Map(map.pipelines.map(p => [p.id, p.name] as [string, string]));
  const live = ofGroups(map);
  const inputsFor = (id: string): OfInput[] => map.inputs.filter(i => i.to === id);
  const scoreboard = (factory: OfFactory | undefined): string => {
    if (!factory?.scoreboard) return '';
    const s = factory.scoreboard;
    const value = (minutes: number | null): string => minutes == null ? '—' : ofAge(minutes);
    return `<section class="of-scoreboard" aria-label="Factory scoreboard">
      <p class="of-scoreboard-window">Last 24 hours</p><dl class="of-scoreboard-grid">
      <div><dt>Merges / hour</dt><dd class="tnum">${s.merges_per_hour.toFixed(2)}</dd></div>
      <div><dt>Queued to merged median</dt><dd class="tnum">${value(s.queued_to_merged_median_minutes)}</dd></div>
      <div><dt>Merged to production median</dt><dd class="tnum">${value(s.merged_to_production_median_minutes)}</dd></div>
      <div><dt title="Cards currently at In QA or Review 2">Queue size (now)</dt><dd class="tnum">${s.queue_size}</dd></div>
    </dl></section>`;
  };
  const factoryById = new Map(map.factories.map(f => [f.id, f]));
  const box = (g: OfGroup, n: number): string => `<section class="of-factory" aria-label="${esc(g.name)}">
      <header><h2>${esc(g.name)}</h2><span class="of-sub">${g.sub}</span>${ofChatHTML('f:' + g.id, g.manager, g.name)}</header>
      ${scoreboard(factoryById.get(g.id))}
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
    paths += `<path class="of-edge${e.mode === 'spawn' ? ' of-edge-spawn' : ''}" d="${d}" style="stroke-width:${width(e.count)}"></path>`;
    const mx = (x1 + x2) / 2, my = back ? Math.max(y1, y2) + 36 : (y1 + y2) / 2 - 8;
    paths += `<text class="of-edge-l tnum" x="${mx.toFixed(1)}" y="${my.toFixed(1)}" text-anchor="middle">${esc(ofEdgeText(e))}</text>`;
  }
  // A loop is a line back from its last pipeline to its first, labelled like the others: laps · median lap.
  for (const loop of map.loops ?? []) {
    const first = rect(loop.pipelines[0] ?? ''), last = rect(loop.pipelines[loop.pipelines.length - 1] ?? '');
    if (!first || !last || loop.pipelines.length < 2) continue;
    const x1 = last.left + last.width / 2 - origin.left, y1 = last.bottom - origin.top;
    const x2 = first.left + first.width / 2 - origin.left, y2 = first.bottom - origin.top;
    paths += `<path class="of-edge of-loop" d="M${x1},${y1} C${x1},${y1 + 60} ${x2},${y2 + 60} ${x2},${y2}" style="stroke-width:${width(loop.laps)}"></path>`;
    const label = loop.median_lap_minutes == null ? `${loop.laps} laps` : `${loop.laps} · ${ofAge(loop.median_lap_minutes)}`;
    paths += `<text class="of-edge-l of-loop-l tnum" x="${((x1 + x2) / 2).toFixed(1)}" y="${(Math.max(y1, y2) + 44).toFixed(1)}" text-anchor="middle">${esc(label)}</text>`;
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
    OF_CHAT_NODES = ofChatNodes(ofGroups(map));
    if (OF_CHAT_KEY && !OF_CHAT_NODES.some(n => n.key === OF_CHAT_KEY)) ofRailShut();
    body.innerHTML = map.pipelines.length ? ofHTML(map) : '<p class="of-empty muted">No pipelines yet.</p>';
    ofRailPaint();
    if (!map.pipelines.length) return;
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
  ofRailShut();             // a thread the last visit mounted goes before its page is replaced
  OF_CHAT_NODES = [];
  const main = $('#main'); main.classList.add('overview-layout');
  let period = ofPeriodRead();
  main.innerHTML = `<section class="overview-page of-page" aria-label="Factories">
    <div class="overview-tabs"><h1>Company</h1>${dashTabsHTML('factories')}</div>
    <div class="of-bar"><div class="og-seg" role="group" aria-label="Period" id="of-period">${OF_PERIODS.map(([p, l]) =>
      `<button type="button" data-of-period="${p}" aria-pressed="${p === period}">${l}</button>`).join('')}</div>
      ${ofMayMakeFactory() ? '<button type="button" class="ghost of-new-b" id="of-new-b" aria-expanded="false" aria-controls="of-new">+ Factory</button>' : ''}</div>
    ${ofMayMakeFactory() ? `<form class="of-new" id="of-new" hidden>
      <label>Name <input name="name" required maxlength="80" autocomplete="off"></label>
      <label>Goal <input name="goal" maxlength="500" autocomplete="off" placeholder="Optional"></label>
      <div class="of-new-acts"><button type="button" class="ghost" data-of-new-cancel>Cancel</button><button type="submit">Save</button></div>
      <p class="of-new-err muted" role="alert" hidden></p></form>` : ''}
    <div class="of-main"><div class="of-body" id="of-body">${skelRows(3, {dot: false})}</div>
      <aside class="of-rail" id="of-rail" aria-label="Chat" hidden>
        <header class="of-rail-head"><div class="of-chips" id="of-chips" role="group" aria-label="Managers"></div>
          <button type="button" class="ghost of-rail-hide" data-of-rail-hide aria-label="Hide" title="Hide">${PIPELINE_RAIL_HIDE_ICON}</button></header>
        <div class="of-rail-chat" id="of-rail-chat"></div>
      </aside></div></section>`;
  document.getElementById('of-rail')?.addEventListener('click', ev => {
    const t = eventElement(ev);
    if (t?.closest('[data-of-rail-hide]')) { ofRailShut(); return; }
    const chip = t?.closest<HTMLElement>('[data-of-chip]');
    if (chip?.dataset['ofChip']) ofRailShow(chip.dataset['ofChip']);
  });
  document.getElementById('of-body')?.addEventListener('click', ev => {
    const chat = eventElement(ev)?.closest<HTMLElement>('[data-of-chat]');
    if (chat?.dataset['ofChat']) ofRailShow(chat.dataset['ofChat']);
  });
  document.getElementById('of-period')?.addEventListener('click', ev => {
    const pick = eventElement(ev)?.closest<HTMLElement>('[data-of-period]');
    if (!pick?.dataset['ofPeriod'] || pick.dataset['ofPeriod'] === period) return;
    period = pick.dataset['ofPeriod'];
    ofPeriodKeep(period);
    for (const b of document.querySelectorAll<HTMLElement>('[data-of-period]')) b.setAttribute('aria-pressed', String(b.dataset['ofPeriod'] === period));
    void ofLoad(period);
  });
  document.getElementById('of-new-b')?.addEventListener('click', () => ofNewToggle(true));
  document.querySelector('[data-of-new-cancel]')?.addEventListener('click', () => ofNewToggle(false));
  document.getElementById('of-new')?.addEventListener('submit', ev => {
    ev.preventDefault();
    void ofNewSave(ev.currentTarget as HTMLFormElement, () => period);
  });
  void ofLoad(period);
}

// ---- + Factory: the company's owner and Admins make a factory (POST /v2/factories). The map refreshes after a save.
function ofMayMakeFactory(): boolean {
  return S.me?.role === 'owner' || S.me?.bot_admin === true;
}

function ofNewToggle(open: boolean): void {
  const form = document.getElementById('of-new') as HTMLFormElement | null;
  const button = document.getElementById('of-new-b');
  if (!form) return;
  form.hidden = !open;
  button?.setAttribute('aria-expanded', String(open));
  if (open) (form.elements.namedItem('name') as HTMLInputElement | null)?.focus();
  else form.reset();
}

async function ofNewSave(form: HTMLFormElement, period: () => string): Promise<void> {
  const name = String((form.elements.namedItem('name') as HTMLInputElement).value).trim();
  const goal = String((form.elements.namedItem('goal') as HTMLInputElement).value).trim();
  const err = form.querySelector<HTMLElement>('.of-new-err');
  if (!name) return;
  try {
    await writeRequest('POST', '/v2/factories', {name, goal: goal || null});
    ofNewToggle(false);
    void ofLoad(period());
  } catch {
    if (err) { err.textContent = 'Could not save the factory.'; err.hidden = false; }
  }
}

// ---- the chat rail: one chip for each factory and pipeline with a manager. A chip, or a chat icon on the map, shows that
// manager's direct thread, mounted by the Tasks page's rail (pipelineManagerChatMount in pipeline-rail.ts); this file builds
// no chat of its own. Hide closes the rail and unmounts the thread. A phone shows the rail as a full-screen sheet.
let OF_CHAT_NODES: OfChatNode[] = [];
let OF_CHAT_KEY = '';
let OF_CHAT_ACTOR = '';
let OF_CHAT_VIEW: ThvView | null = null;

// Chips and chat icons say which node's manager is on show.
function ofRailPaint(): void {
  const chips = document.getElementById('of-chips');
  if (chips) chips.innerHTML = OF_CHAT_NODES.map(n => `<button type="button" class="of-chip" data-of-chip="${esc(n.key)}" aria-pressed="${n.key === OF_CHAT_KEY}">${esc(n.name)}</button>`).join('');
  for (const b of document.querySelectorAll<HTMLElement>('[data-of-chat]')) b.setAttribute('aria-pressed', String(b.dataset['ofChat'] === OF_CHAT_KEY));
}

function ofRailShow(key: string): void {
  const node = OF_CHAT_NODES.find(n => n.key === key);
  const rail = document.getElementById('of-rail'), host = document.getElementById('of-rail-chat');
  if (!node || !rail || !host) return;
  rail.hidden = false;
  // Chips of one manager share its thread: switching between them keeps the view and what is typed in it.
  if (OF_CHAT_ACTOR !== node.manager) {
    threadUnmount(OF_CHAT_VIEW);
    OF_CHAT_VIEW = pipelineManagerChatMount(host, node.manager);
    OF_CHAT_ACTOR = node.manager;
  }
  OF_CHAT_KEY = key;
  ofRailPaint();
}

function ofRailShut(): void {
  threadUnmount(OF_CHAT_VIEW);   // before the host is emptied: unmounting keeps the draft the composer holds
  OF_CHAT_VIEW = null;
  OF_CHAT_ACTOR = '';
  OF_CHAT_KEY = '';
  const rail = document.getElementById('of-rail');
  if (rail) rail.hidden = true;
  document.getElementById('of-rail-chat')?.replaceChildren();
  ofRailPaint();
}
