/* ui/app/pipeline-rail.ts — The pipeline manager's rail beside a pipeline's stages, and the phone's one-stage view.
   Desktop: a side panel (avatar, name, "Ran HH:MM"; tabs Log, Tasks, Learned; the Late list; chat with the manager bot).
   Its hide button folds it to a thin strip, and the choice is kept in the person's preference pipeline.rail_hidden.
   Phone (600px and narrower): one stage at a time with a row of stage chips (swipe or tap to change), and the rail
   becomes a bar at the bottom that opens the same panel as a sheet. The chat is the ordinary DM thread view with the
   manager bot (thread-view.js threadMount), never a chat of its own. Data: GET /v2/pipelines/{id}/manager.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const PIPELINE_RAIL_PREF = 'pipeline.rail_hidden';
const PIPELINE_PHONE_QUERY = '(max-width:600px)';
const PIPELINE_RAIL_REFRESH_MS = 60000;

type PipelineRailTab = 'log' | 'tasks' | 'learned';

/** The Tasks page's state (TaskState in tasks.ts): this file reads and writes its phone stage and its tasks. */
type PipelineTasksState = TaskState;

interface PipelineRail {
  pipelineId: string;
  /** The manager's actor id (bot:<ref>). */
  manager: string;
  view: PipelineManagerView | null;
  tab: PipelineRailTab;
  sheet: boolean;
  host: HTMLElement;
  chat: ThvView | null;
  timer: number;
  /** What is drawn, so a repaint that changes nothing touches nothing. */
  drawn: string;
}

let pipelineRail: PipelineRail | null = null;
let pipelineRailHidden = false;
/** Tickets past their stage's "Warn after", from the server's Late list: the cards that show "Nh in step" from its clock. */
const pipelineLateClocks = new Map<string, PipelineLateClock>();
/** The pipeline on screen whose Late list is held, when it was last read, and the pending live reload and poll. */
let pipelineLateFor = '';
let pipelineLateAt = 0;
/** The ticket ids the last Late read asked for (joined), and the read's sequence: only the latest read's answer is shown. */
let pipelineLateIds = '';
let pipelineLateSeq = 0;
let pipelineLateTimer = 0;
let pipelineLatePoll = 0;
/** Ends the current follow (its timers, its listeners, its list). A read that was in flight when it ended is ignored. */
let pipelineLateStop: () => void = () => undefined;
let pipelineLateGen = 0;
/** A live reload waits at least this long after the last read; the poll keeps the times moving with no event. */
const PIPELINE_LATE_MS = 30000;
const PIPELINE_LATE_POLL_MS = 300000;  // safety poll; live events do the rest
/** The chip order of the phone's stages as last drawn, for swipes. */
let pipelineStageOrder: string[] = [];

const pipelinePhone = (): boolean => window.matchMedia(PIPELINE_PHONE_QUERY).matches;

/** A card's time in its stage shows only when the server's Late list (late_tickets) says it is past the stage's Warn
    after: this is that clock, or null. The server pauses the clock on holds and waiting tickets; the client does not
    repeat that rule, and does no arithmetic on the step's start. */
function pipelineLateClock(task: { id: string | number }): PipelineLateClock | null {
  return pipelineLateClocks.get(String(task.id)) ?? null;
}

/** Whether this person may change the pipeline's rules (and so move a ticket past its requirements, or mark one met):
    the pipeline's owner or an Admin. The task types list says so per pipeline (can_edit_rules, backend/pipeline_rights.py),
    and that answer is read. Only when a server sends no such field does this guess the same rule from what the page knows;
    the server still decides. */
function pipelineMayEditRules(typeId: string | null | undefined): boolean {
  const type = TASK_TYPES.find(item => item.id === typeId);
  if (typeof type?.can_edit_rules === 'boolean') return type.can_edit_rules;
  const me = myActor();
  if (!me) return false;
  return type?.pipeline_owner_id === me || S.me?.role === 'owner' || S.me?.bot_admin === true;
}

// ---------------------------------------------------------------- words

function pipelineClock(iso: string): string {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? '' : at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

/** "Ran 14:50", or "Not running since 14:50" when the manager has missed its runs. */
function pipelineRailStatus(view: PipelineManagerView | null): string {
  if (!view) return '';
  if (!view.last_run) return 'Not run yet';
  const at = pipelineClock(view.last_run.started_at);
  return view.missed_runs > 0 ? `Not running since ${at}` : `Ran ${at}`;
}

/** An age: 40m, 27h, 3d. */
function pipelineSpan(minutes: number): string {
  if (minutes < 60) return `${Math.max(1, Math.round(minutes))}m`;
  if (minutes < 2880) return `${Math.round(minutes / 60)}h`;
  return `${Math.round(minutes / 1440)}d`;
}

/** A limit as it was set: 1d, 24h, 45m. */
function pipelineLimit(minutes: number): string {
  if (minutes > 0 && minutes % 1440 === 0) return `${minutes / 1440}d`;
  if (minutes > 0 && minutes % 60 === 0) return `${minutes / 60}h`;
  return `${minutes}m`;
}

// ---------------------------------------------------------------- the panel

const PIPELINE_RAIL_TABS: { id: PipelineRailTab; label: string }[] = [
  { id: 'log', label: 'Log' }, { id: 'tasks', label: 'Tasks' }, { id: 'learned', label: 'Learned' },
];

const PIPELINE_RAIL_HIDE_ICON = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" '
  + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16M9 10l2 2-2 2"/></svg>';

/** Open tasks the manager holds in this pipeline: the Tasks tab's "assignee: manager" list. */
function pipelineRailTasks(state: PipelineTasksState, rail: PipelineRail): PipelineTasksState['tasks'] {
  return state.tasks.filter(task => task.owner_id === rail.manager && task.type_id === rail.pipelineId
    && !['done', 'declined', 'closed'].includes(String(task.status)));
}

function pipelineRailLateHTML(view: PipelineManagerView): string {
  if (!view.late.length) return '';
  const rows = view.late.map(item => `<li><button type="button" class="pr-late" data-rail-task="${esc(item.task_id)}">
      <span class="pr-late-id">${esc(item.task_id)}</span>
      <span class="pr-late-what">${esc(item.stage_name)} · warn after ${esc(pipelineLimit(item.limit_minutes))}</span>
      <span class="pr-late-age tnum">${esc(pipelineSpan(item.age_minutes))}</span></button></li>`).join('');
  return `<section class="pr-sec"><h3 class="pipe-h">Late · ${view.late.length}</h3><ul class="pr-list">${rows}</ul></section>`;
}

function pipelineRailLogHTML(lines: PipelineManagerLogLine[], empty: string): string {
  if (!lines.length) return `<p class="pr-empty">${esc(empty)}</p>`;
  return `<ul class="pr-list pr-log">${lines.map(line => `<li class="pr-line pr-${esc(line.kind)}">
      <span class="pr-when tnum">${esc(pipelineClock(line.at))}</span><span class="pr-dot" aria-hidden="true"></span>
      <span class="pr-text">${esc(line.text)}</span></li>`).join('')}</ul>`;
}

function pipelineRailTabHTML(state: PipelineTasksState, rail: PipelineRail): string {
  const view = rail.view;
  if (!view) return '<p class="pr-empty">Loading</p>';
  if (rail.tab === 'tasks') {
    const tasks = pipelineRailTasks(state, rail);
    return tasks.length
      ? `<ul class="pr-list">${tasks.map(task => `<li><button type="button" class="pr-late" data-rail-task="${esc(String(task.id))}">
          <span class="pr-late-id">${esc(String(task.id))}</span><span class="pr-late-what">${esc(task.title || '')}</span></button></li>`).join('')}</ul>`
      : '<p class="pr-empty">Nothing</p>';
  }
  if (rail.tab === 'learned') return pipelineRailLogHTML(view.log.filter(line => line.action === 'propose_rule'), 'Nothing yet');
  return pipelineRailLogHTML(view.log, 'Nothing yet');
}

function pipelineRailBar(): HTMLElement | null {
  return document.getElementById('pipe-rail-bar');
}

/** A manager's name as Hub has it now: its display name, read each time the panel or the bar draws, so a rename needs no release.
    The roster is looked up by the bot's row (botDisplayName takes an id, not a slug). */
function pipelineManagerName(manager: string): string {
  const row = botByRef(manager);
  return row ? botDisplayName(row.id) : actorLabel(manager);
}

/** The bar's line as the person reads it: a line that begins "ask:" shows without the prefix, its first letter capitalised. */
function pipelineActivityText(text: string): string {
  const bare = text.replace(/^ask:\s*/, '');
  return bare === text ? text : bare.charAt(0).toUpperCase() + bare.slice(1);
}

/** The manager's panel: head, tabs, Late and the tab's list; and the phone's bar. Chat is mounted beside it and never
    redrawn here. */
function pipelineRailPaint(state: PipelineTasksState | null): void {
  const rail = pipelineRail;
  if (!rail || !state) return;  // the Tasks page is gone: nothing to draw into or from
  const view = rail.view;
  const tasks = pipelineRailTasks(state, rail);
  const phone = pipelinePhone();
  const name = pipelineManagerName(rail.manager);
  const signature = JSON.stringify([view, rail.tab, rail.sheet, pipelineRailHidden, phone, name, tasks.map(task => [task.id, task.title])]);
  if (signature === rail.drawn) return;
  rail.drawn = signature;
  const host = rail.host;
  const status = pipelineRailStatus(view);
  host.classList.toggle('collapsed', pipelineRailHidden && !phone);
  host.classList.toggle('sheet-open', rail.sheet && phone);
  document.querySelector('.tasks-page')?.classList.toggle('rail-hidden', pipelineRailHidden && !phone);
  document.body.classList.toggle('pipe-sheet-open', rail.sheet && phone);
  const strip = host.querySelector('.pr-strip');
  strip?.setAttribute('aria-label', `Show ${name}`);
  strip?.setAttribute('title', `Show ${name}`);
  strip?.replaceChildren(...htmlNodes(actorFace(rail.manager, 28)));
  const control = phone
    ? '<button type="button" class="pr-x" data-rail-close aria-label="Close">×</button>'
    : `<button type="button" class="pr-x" data-rail-hide aria-label="Hide" title="Hide">${PIPELINE_RAIL_HIDE_ICON}</button>`;
  const head = host.querySelector('.pr-head');
  if (head) {
    head.innerHTML = `<span class="pr-face">${actorFace(rail.manager, 32)}</span>
      <div class="pr-who"><b>${esc(name)}</b><span class="pr-ran${view && view.missed_runs > 0 ? ' warn' : ''}">${esc(status)}</span></div>${control}`;
  }
  const tabs = host.querySelector('.pr-tabs');
  if (tabs) {
    tabs.innerHTML = PIPELINE_RAIL_TABS.map(tab => {
      const count = tab.id === 'tasks' && tasks.length ? ` <span class="tnum">${tasks.length}</span>` : '';
      return `<button type="button" role="tab" class="pr-tab${rail.tab === tab.id ? ' on' : ''}" data-rail-tab="${tab.id}" aria-selected="${rail.tab === tab.id}">${tab.label}${count}</button>`;
    }).join('');
  }
  const body = host.querySelector('.pr-body');
  if (body) body.innerHTML = (view ? pipelineRailLateHTML(view) : '') + pipelineRailTabHTML(state, rail);
  const bar = pipelineRailBar();
  if (bar) {
    const late = view?.late.length || 0;
    bar.innerHTML = `<span class="pr-face">${actorFace(rail.manager, 28)}</span>
      <span class="pr-bar-text"><b>${esc(name)}</b><span>${esc(pipelineActivityText(view?.log[0]?.text || status))}</span></span>
      ${late ? `<span class="pr-bar-late">${late} late</span>` : ''}<span class="pr-chev" aria-hidden="true">⌃</span>`;
    bar.setAttribute('aria-label', `${name}${late ? `, ${late} late` : ''}`);
  }
  pipelineRailChatEnsure();
}

function htmlNodes(html: string): Node[] {
  const box = document.createElement('div');
  box.innerHTML = html;
  return [...box.childNodes];
}

/** A manager's actor id from its reference: task_types.manager and factories.manager_id are stored with or without bot:. */
function pipelineManagerActor(ref: string): string {
  return 'bot:' + ref.replace(/^bot:/, '');
}

/** The manager's direct thread drawn into a host. The Tasks page's rail and the Factories rail both mount it here. */
function pipelineManagerChatMount(host: HTMLElement, manager: string): ThvView {
  return threadMount(host, { dm: pipelineManagerActor(manager), card: true, header: false });
}

/** Mounts the DM thread with the manager once the panel can be seen (the desktop rail open, or the phone's sheet). */
function pipelineRailChatEnsure(): void {
  const rail = pipelineRail;
  if (!rail || rail.chat) return;
  if (pipelinePhone() ? !rail.sheet : pipelineRailHidden) return;
  const host = rail.host.querySelector<HTMLElement>('.pr-chat');
  if (host) rail.chat = pipelineManagerChatMount(host, rail.manager);
}

// ---------------------------------------------------------------- loading and keeping it

/** Whether two Late lists show the same cards with the same hours (the card shows whole hours). */
function pipelineLateSame(a: Map<string, PipelineLateClock>, b: Map<string, PipelineLateClock>): boolean {
  if (a.size !== b.size) return false;
  for (const [id, clock] of a) {
    const other = b.get(id);
    if (!other || Math.floor(other.hours) !== Math.floor(clock.hours)) return false;
  }
  return true;
}

/** The ids of the tickets on screen (their rows and cards on the Tasks page), sorted. A row's key is "t" plus the id. */
function pipelineShownIds(): string[] {
  const ids = new Set<string>();
  document.querySelectorAll<HTMLElement>('#task-body [data-task-key]').forEach(el => {
    const key = el.dataset.taskKey ?? '';
    if (key.startsWith('t') && el.getClientRects().length) ids.add(key.slice(1));
  });
  return [...ids].sort();
}

/** Reads the Late clocks of the tickets on screen, rail open or not, and repaints the cards when they changed. Only those
    tickets are asked for, so the server's cap on a pipeline-wide list never hides a card. A hidden tab reads nothing; it
    reads once when it is shown again. */
async function pipelineLateLoad(): Promise<void> {
  const pipelineId = pipelineLateFor, gen = pipelineLateGen;
  if (!pipelineId || document.hidden) return;
  pipelineLateAt = Date.now();
  const ids = pipelineShownIds();
  pipelineLateIds = ids.join(',');
  const seq = ++pipelineLateSeq;
  let next: Map<string, PipelineLateClock>;
  try { next = await pipelineLateTickets(pipelineId, ids); } catch { return; }
  if (gen !== pipelineLateGen || seq !== pipelineLateSeq) return;
  const changed = !pipelineLateSame(next, pipelineLateClocks);
  pipelineLateClocks.clear();
  next.forEach((clock, id) => { pipelineLateClocks.set(id, clock); });
  if (changed && TASKS_ST) tasksRender(TASKS_ST);
}

/** After a draw: when the tickets on screen are not the ones the last read asked for, their Late clocks are read again. */
function pipelineLateShownSync(): void {
  if (!pipelineLateFor || document.hidden) return;
  if (pipelineShownIds().join(',') !== pipelineLateIds) void pipelineLateLoad();
}

/** Follows the pipeline on screen: its Late list is read when it shows and again every 5 minutes, so a card's time moves
    on with no event. Another pipeline (or none) drops the old list. The follow belongs to the Tasks page: the page's
    signal ends it (timers, listeners, the list), and a tab that is shown again reads the list once. */
function pipelineLateFollow(pipelineId: string, signal?: AbortSignal): void {
  if (pipelineId === pipelineLateFor) return;
  pipelineLateStop();
  pipelineLateFor = pipelineId;
  pipelineLateClocks.clear();
  if (!pipelineId) return;
  const onShown = (): void => { if (!document.hidden) void pipelineLateLoad(); };
  const stop = (): void => {
    window.clearInterval(pipelineLatePoll); pipelineLatePoll = 0;
    window.clearTimeout(pipelineLateTimer); pipelineLateTimer = 0;
    document.removeEventListener('visibilitychange', onShown);
    signal?.removeEventListener('abort', stop);
    pipelineLateGen++;
    pipelineLateFor = '';
    pipelineLateClocks.clear();
    pipelineLateStop = () => undefined;
  };
  pipelineLateStop = stop;
  pipelineLatePoll = window.setInterval(() => { void pipelineLateLoad(); }, PIPELINE_LATE_POLL_MS);
  document.addEventListener('visibilitychange', onShown);
  signal?.addEventListener('abort', stop, {once: true});
  void pipelineLateLoad();
}

/** Called for each batch of live task events: when one touches the pipeline on screen, its Late list is read again,
    at most once per PIPELINE_LATE_MS. */
function pipelineLateLive(events: { id?: unknown; bulk?: unknown; gone?: unknown; task?: { type_id?: unknown } | null }[]): void {
  if (!pipelineLateFor) return;
  const cards = TASKS_ST?.tasks ?? [];
  const touches = events.some(d => d.bulk || d.gone || d.task?.type_id === pipelineLateFor
    || cards.some(t => String(t.id) === String(d.id) && t.type_id === pipelineLateFor));
  if (!touches || pipelineLateTimer) return;
  const wait = Math.max(0, pipelineLateAt + PIPELINE_LATE_MS - Date.now());
  pipelineLateTimer = window.setTimeout(() => { pipelineLateTimer = 0; void pipelineLateLoad(); }, wait);
}

async function pipelineRailLoad(): Promise<void> {
  const rail = pipelineRail;
  if (!rail) return;
  try {
    const view = await pipelineManagerView(rail.pipelineId);
    if (pipelineRail !== rail) return;
    rail.view = view;
    pipelineRailPaint(TASKS_ST);
  } catch {
    // The manager's status is not there (an older server, or a pipeline with none): the rail shows what it has.
    if (pipelineRail === rail) pipelineRailPaint(TASKS_ST);
  }
}

async function pipelineRailPrefRead(): Promise<void> {
  if (!S.me?.cloud) return;
  const saved = (await v2Get('/v2/preferences/' + PIPELINE_RAIL_PREF) as { value?: unknown } | null)?.value;
  if (typeof saved !== 'boolean' || saved === pipelineRailHidden) return;
  pipelineRailHidden = saved;
  if (pipelineRail) { pipelineRail.drawn = ''; pipelineRailPaint(TASKS_ST); }
}

function pipelineRailHide(hidden: boolean): void {
  pipelineRailHidden = hidden;
  if (pipelineRail) { pipelineRail.drawn = ''; pipelineRailPaint(TASKS_ST); }
  if (S.me?.cloud) void post('/v2/preferences/' + PIPELINE_RAIL_PREF, { value: hidden }).catch(() => undefined);
}

function pipelineRailEnter(type: { id: string }, manager: string, page: Element, signal?: AbortSignal): void {
  pipelineRailLeave();
  const host = document.createElement('aside');
  host.id = 'pipe-rail';
  host.className = 'pipe-rail';
  host.setAttribute('aria-label', 'Manager');
  host.innerHTML = `<button type="button" class="pr-strip" data-rail-show aria-label="Show"></button>
    <div class="pr-panel"><header class="pr-head"></header><div class="pr-tabs" role="tablist"></div>
      <div class="pr-body"></div><div class="pr-chat"></div></div>`;
  const bar = document.createElement('button');
  bar.type = 'button';
  bar.id = 'pipe-rail-bar';
  bar.className = 'pr-bar';
  bar.dataset.railBar = '';
  const scrim = document.createElement('div');
  scrim.className = 'pr-scrim';
  scrim.dataset.railClose = '';
  page.append(host);
  page.after(bar, scrim);
  page.classList.add('has-rail');
  const rail: PipelineRail = { pipelineId: type.id, manager, view: null, tab: 'log', sheet: false, host, chat: null, timer: 0, drawn: '' };
  pipelineRail = rail;
  rail.timer = window.setInterval(() => { void pipelineRailLoad(); }, PIPELINE_RAIL_REFRESH_MS);
  // The poll belongs to the Tasks page: leaving it (the router aborts the page's signal) takes the rail and its timer away.
  signal?.addEventListener('abort', () => { if (pipelineRail === rail) pipelineRailLeave(); }, { once: true });
  void pipelineRailPrefRead();
  void pipelineRailLoad();
}

function pipelineRailLeave(): void {
  const rail = pipelineRail;
  if (!rail) return;
  window.clearInterval(rail.timer);
  if (rail.chat) threadUnmount(rail.chat);
  document.querySelector('.tasks-page')?.classList.remove('has-rail', 'rail-hidden');
  document.body.classList.remove('pipe-sheet-open');
  document.querySelectorAll('.pr-scrim, #pipe-rail-bar').forEach(node => { node.remove(); });
  rail.host.remove();
  pipelineRail = null;
}

/** Called after every draw of the Tasks page: keeps the rail for the pipeline on show (one with a manager, on the
    board) and takes it away otherwise. */
function pipelineRailSync(state: PipelineTasksState): void {
  const type = state.view === 'board' ? (pipelineSelectedType(state) as { id: string; manager?: string | null } | null) : null;
  // The type names its manager by slug; the tasks it holds carry the bot's id, so the actor is the bot row's id when there is one.
  const ref = type?.manager ? String(type.manager).replace(/^bot:/, '') : '';
  const manager = ref ? 'bot:' + (botByRef(ref)?.id ?? ref) : '';
  const page = document.querySelector('.tasks-page');
  pipelineLateFollow(type?.id ?? '', state.layoutAbort?.signal);
  pipelineLateShownSync();
  if (type) void pipelineMineSync(type.id);
  if (!type || !manager || !page) { pipelineRailLeave(); return; }
  if (!pipelineRail || pipelineRail.pipelineId !== type.id || pipelineRail.manager !== manager || !pipelineRail.host.isConnected) {
    pipelineRailEnter(type, manager, page, state.layoutAbort?.signal);
  }
  pipelineRailPaint(state);
}

// ---------------------------------------------------------------- the phone's one stage

interface PipelinePhoneColumn {
  id: string;
  name: string;
  fold?: string;
  items: { task: { id: string } }[];
}

/** The stage on show on a phone (the one chosen, else the first with tickets), the row of stage chips and the way on. */
function pipelinePhoneBoard(state: PipelineTasksState, columns: PipelinePhoneColumn[]): { column: PipelinePhoneColumn; chips: string; next: string } | null {
  const stages = columns.filter(column => !column.fold);
  if (!stages.length) return null;
  pipelineStageOrder = stages.map(column => column.id);
  const active = stages.find(column => column.id === state.pipeStage) || stages.find(column => column.items.length) || stages[0]!;
  const chips = stages.map(column => {
    const waiting = pipelineForYouCount(column.items) > 0;
    return `<button type="button" class="pchip${column === active ? ' on' : ''}" data-pipe-chip="${esc(column.id)}" aria-pressed="${column === active}">`
      + `${esc(column.name)}<span class="tnum pchip-n">${column.items.length}</span>${waiting ? '<span class="pchip-dot" role="img" aria-label="Waiting on you"></span>' : ''}</button>`;
  }).join('');
  const after = stages[stages.indexOf(active) + 1];
  requestAnimationFrame(() => document.querySelectorAll<HTMLElement>('.pchips').forEach(pipelineChipsEdges));
  return {
    column: active,
    chips: `<div class="pchips" role="group" aria-label="Stages">${chips}</div>`,
    next: after ? `<button type="button" class="pipe-next" data-pipe-chip="${esc(after.id)}">Swipe for ${esc(after.name)} ›</button>` : '',
  };
}

function pipelinePhoneShow(stageId: string): void {
  const state = TASKS_ST;
  if (!state || !stageId) return;
  state.pipeStage = stageId;
  tasksRender(state);
  requestAnimationFrame(() => document.querySelector('.pchip.on')?.scrollIntoView({ inline: 'center', block: 'nearest' }));
}

/** A chip row fades on each edge where chips are cut off, and on no edge with nothing beyond it (the .fade-l and .fade-r classes). */
function pipelineChipsEdges(row: HTMLElement): void {
  const box = row.getBoundingClientRect();
  const chips = [...row.children];
  const first = chips[0]?.getBoundingClientRect(), last = chips.at(-1)?.getBoundingClientRect();
  row.classList.toggle('fade-l', !!first && first.left < box.left - 1);
  row.classList.toggle('fade-r', !!last && last.right > box.right + 1);
}
// A scroll of any chip row (scroll does not bubble, so the capture listener sees it) updates its fades.
document.addEventListener('scroll', (event: Event) => {
  const target = event.target instanceof HTMLElement ? event.target : null;
  if (target?.classList.contains('pchips')) pipelineChipsEdges(target);
}, {capture: true, passive: true});

/** Steps to the next (1) or the previous (-1) stage; the ends stay put. */
function pipelinePhoneStep(delta: number): void {
  const here = document.querySelector<HTMLElement>('.pchip.on')?.dataset.pipeChip || '';
  const to = pipelineStageOrder[pipelineStageOrder.indexOf(here) + delta];
  if (here && to) pipelinePhoneShow(to);
}

let pipelineSwipe: { x: number; y: number } | null = null;
document.addEventListener('touchstart', (event: TouchEvent) => {
  const target = event.target instanceof Element ? event.target : null;
  const first = event.touches[0];
  pipelineSwipe = first && target?.closest('.pipe-phone') ? { x: first.clientX, y: first.clientY } : null;
}, { passive: true });
document.addEventListener('touchend', (event: TouchEvent) => {
  const start = pipelineSwipe, end = event.changedTouches[0];
  pipelineSwipe = null;
  if (!start || !end) return;
  const dx = end.clientX - start.x, dy = end.clientY - start.y;
  if (Math.abs(dx) > 60 && Math.abs(dx) > 2 * Math.abs(dy)) pipelinePhoneStep(dx < 0 ? 1 : -1);
}, { passive: true });

// A window that crosses the phone width draws the other layout.
window.matchMedia(PIPELINE_PHONE_QUERY).addEventListener('change', () => {
  const state = TASKS_ST;
  if (!state || state.view !== 'board') return;
  if (pipelineRail) { pipelineRail.sheet = false; pipelineRail.drawn = ''; }
  tasksRender(state);
});

// ---------------------------------------------------------------- clicks

function pipelineRailSheet(open: boolean): void {
  if (!pipelineRail) return;
  pipelineRail.sheet = open;
  pipelineRail.drawn = '';
  pipelineRailPaint(TASKS_ST);
  if (open) void pipelineRailLoad();
}

document.addEventListener('click', (event: MouseEvent) => {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) return;
  const chip = target.closest<HTMLElement>('[data-pipe-chip]');
  if (chip?.dataset.pipeChip) { pipelinePhoneShow(chip.dataset.pipeChip); return; }
  const rail = pipelineRail;
  if (!rail) return;
  if (target.closest('[data-rail-hide]')) return pipelineRailHide(true);
  if (target.closest('[data-rail-show]')) return pipelineRailHide(false);
  if (target.closest('[data-rail-bar]')) return pipelineRailSheet(true);
  if (target.closest('[data-rail-close]')) return pipelineRailSheet(false);
  const tab = target.closest<HTMLElement>('[data-rail-tab]');
  if (tab && PIPELINE_RAIL_TABS.some(item => item.id === tab.dataset.railTab)) {
    rail.tab = tab.dataset.railTab as PipelineRailTab;
    rail.drawn = '';
    pipelineRailPaint(TASKS_ST);
    return;
  }
  const task = target.closest<HTMLElement>('[data-rail-task]');
  if (task?.dataset.railTask) {
    if (pipelinePhone()) pipelineRailSheet(false);
    if (TASKS_ST) taskPeekOpen(TASKS_ST, 't' + task.dataset.railTask);
  }
});
document.addEventListener('keydown', (event: KeyboardEvent) => {
  if (event.key === 'Escape' && pipelineRail?.sheet) pipelineRailSheet(false);
});
