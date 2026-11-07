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

/** The part of the Tasks page's state this file reads and writes. */
interface PipelineTasksState {
  view: string;
  tasks: { id: string | number; title?: string; owner_id?: string; type_id?: string; status?: string }[];
  /** The stage chosen on a phone, by step id. */
  pipeStage?: string;
}

interface PipelineRail {
  pipelineId: string;
  /** The manager's actor id (bot:<ref>). */
  manager: string;
  view: PipelineManagerView | null;
  tab: PipelineRailTab;
  sheet: boolean;
  host: HTMLElement;
  chat: unknown;
  timer: number;
  /** What is drawn, so a repaint that changes nothing touches nothing. */
  drawn: string;
}

let pipelineRail: PipelineRail | null = null;
let pipelineRailHidden = false;
/** Task ids past their stage's "Warn after", from the manager's Late list: the cards that show "Nh in step". */
const pipelineLateIds = new Set<string>();
/** The chip order of the phone's stages as last drawn, for swipes. */
let pipelineStageOrder: string[] = [];

const pipelinePhone = (): boolean => window.matchMedia(PIPELINE_PHONE_QUERY).matches;

/** A card's time in its stage shows only when it is past the stage's limit: the stage's own limit when the server sends
    one, else the manager's Late list. */
function pipelineStepLate(task: { id: string | number; step_since?: string; step?: { limit_minutes?: number | null } | null }): boolean {
  const limit = Number(task.step?.limit_minutes || 0);
  const since = Date.parse(task.step_since || '');
  if (limit > 0 && since) return Date.now() - since >= limit * 60000;
  return pipelineLateIds.has(String(task.id));
}

/** Whether this person may change the pipeline's rules (and so move a ticket past its requirements, or mark one met):
    the pipeline's owner, the company's owner or an Admin. The routes send no such field, so this is the same rule as the
    server's (backend/pipeline_rights.py); the server still decides. */
function pipelineMayEditRules(typeId: string | null | undefined): boolean {
  const me = myActor();
  if (!me) return false;
  const type = (TASK_TYPES as { id: string; owner_id?: string }[]).find(item => item.id === typeId);
  return type?.owner_id === me || S.me?.role === 'owner' || S.me?.bot_admin === true;
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

/** The manager's panel: head, tabs, Late and the tab's list; and the phone's bar. Chat is mounted beside it and never
    redrawn here. */
function pipelineRailPaint(state: PipelineTasksState): void {
  const rail = pipelineRail;
  if (!rail) return;
  const view = rail.view;
  const tasks = pipelineRailTasks(state, rail);
  const phone = pipelinePhone();
  const signature = JSON.stringify([view, rail.tab, rail.sheet, pipelineRailHidden, phone, tasks.map(task => [task.id, task.title])]);
  if (signature === rail.drawn) return;
  rail.drawn = signature;
  const host = rail.host;
  const name = actorLabel(rail.manager);
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
      <span class="pr-bar-text"><b>${esc(name)}</b><span>${esc(view?.log[0]?.text || status)}</span></span>
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

/** Mounts the DM thread with the manager once the panel can be seen (the desktop rail open, or the phone's sheet). */
function pipelineRailChatEnsure(): void {
  const rail = pipelineRail;
  if (!rail || rail.chat) return;
  if (pipelinePhone() ? !rail.sheet : pipelineRailHidden) return;
  const host = rail.host.querySelector<HTMLElement>('.pr-chat');
  if (host) rail.chat = threadMount(host, { dm: rail.manager, card: true, header: false });
}

// ---------------------------------------------------------------- loading and keeping it

async function pipelineRailLoad(): Promise<void> {
  const rail = pipelineRail;
  if (!rail) return;
  try {
    const view = await pipelineManagerView(rail.pipelineId);
    if (pipelineRail !== rail) return;
    rail.view = view;
    const late = new Set(view.late.map(item => item.task_id));
    const changed = late.size !== pipelineLateIds.size || [...late].some(id => !pipelineLateIds.has(id));
    pipelineLateIds.clear();
    late.forEach(id => pipelineLateIds.add(id));
    if (changed) tasksRender(TASKS_ST);       // the cards past their limit say so
    else pipelineRailPaint(TASKS_ST);
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

function pipelineRailEnter(type: { id: string }, manager: string, page: Element): void {
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
  document.querySelectorAll('.pr-scrim, #pipe-rail-bar').forEach(node => node.remove());
  rail.host.remove();
  pipelineRail = null;
  pipelineLateIds.clear();
}

/** Called after every draw of the Tasks page: keeps the rail for the pipeline on show (one with a manager, on the
    board) and takes it away otherwise. */
function pipelineRailSync(state: PipelineTasksState): void {
  const type = state.view === 'board' ? (pipelineSelectedType(state) as { id: string; manager?: string | null } | null) : null;
  const ref = type?.manager ? String(type.manager).replace(/^bot:/, '') : '';
  const page = document.querySelector('.tasks-page');
  if (type) void pipelineMineSync(type.id);
  if (!type || !ref || !page) { pipelineRailLeave(); return; }
  if (!pipelineRail || pipelineRail.pipelineId !== type.id || pipelineRail.manager !== 'bot:' + ref || !pipelineRail.host.isConnected) {
    pipelineRailEnter(type, 'bot:' + ref, page);
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
  return {
    column: active,
    chips: `<div class="pchips" role="group" aria-label="Stages">${chips}</div>`,
    next: after ? `<button type="button" class="pipe-next" data-pipe-chip="${esc(after.id)}">Swipe for ${esc(after.name)} ›</button>` : '',
  };
}

function pipelinePhoneShow(stageId: string): void {
  const state = TASKS_ST as PipelineTasksState | null;
  if (!state || !stageId) return;
  state.pipeStage = stageId;
  tasksRender(state);
  requestAnimationFrame(() => document.querySelector('.pchip.on')?.scrollIntoView({ inline: 'center', block: 'nearest' }));
}

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
  const state = TASKS_ST as PipelineTasksState | null;
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
    taskPeekOpen(TASKS_ST, 't' + task.dataset.railTask);
  }
});
document.addEventListener('keydown', (event: KeyboardEvent) => {
  if (event.key === 'Escape' && pipelineRail?.sheet) pipelineRailSheet(false);
});
