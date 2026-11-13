/* ui/app/pipeline-rail.ts — The manager's rail beside a pipeline's stages (docs/pipelines-v3.md S10), on a desktop.
   Closed until the chat icon in the page's header opens it (open or closed is kept in this browser); open, it is a
   column at the right of the page. Top: the manager's face and name, and a button that closes it. Tabs: Log,
   Tasks, and Learned once it has a line. Above the tab's list, the Late tasks: title and how long, no mark, no avatar. Below, the chat with the
   manager: the ordinary direct thread (thread-view.ts threadMount), never a chat of its own.
   Tasks lists the open tasks the manager was woken about (it owns none, C25); the Log shows each push past and each
   snooze with no end a bot made. Tasks show by title. Any [data-task] row opens the task, by click, Enter or Space (prClick, prKey).
   The page (tasks-page.ts) gives the host through the pipeline view's rail hook (task-pipelines.ts).
   Reads: the tasks the page read for the view, and GET /api/v2/pipelines/{id}/log (pipeline-api.ts).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

type PrTab = 'log' | 'tasks' | 'learned';
interface PrState {
  host: HTMLElement; pipelineId: string; manager: string; tab: PrTab;
  log: PipelineLog | null; failed: boolean; readAt: number; chat: ThvView | null;
}

let PR: PrState | null = null;
const PR_OPEN_KEY = 'tico.pipeline.rail_open';
const PR_READ_MS = 60000;
const PR_TABS: [PrTab, string][] = [['log', 'Log'], ['tasks', 'Tasks'], ['learned', 'Learned']];
const PR_HIDE_ICON = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" '
  + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';

/** Closed unless this browser opened it. */
function prOpen(): boolean {
  try { return localStorage.getItem(PR_OPEN_KEY) === '1'; } catch { return false; }
}
function prKeepOpen(open: boolean): void {
  try { localStorage.setItem(PR_OPEN_KEY, open ? '1' : '0'); } catch { /* storage blocked: not kept */ }
}
/** The page's column for the rail: there only while the rail is open. */
function prPlace(host: HTMLElement, open: boolean): void {
  host.hidden = !open;
  host.closest('.tasks-page')?.classList.toggle('has-rail', open);
}

/** A manager's actor id from its reference, stored with or without bot:. */
const prActor = (ref: string): string => 'bot:' + ref.replace(/^bot:/, '');

/** "07:50" today, "Nov 8" before. */
function prClock(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return '';
  return at.toDateString() === new Date().toDateString()
    ? at.toLocaleTimeString(undefined, {hour: '2-digit', minute: '2-digit', hour12: false})
    : at.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
}

function prLateHTML(tasks: TaskAny[]): string {
  const late = tasks.filter(t => t.status === 'open' && t.late);
  if (!late.length) return '';
  const rows = late.map(t => `<div class="pm-row" data-task="${esc(t.id)}" role="button" tabindex="0">`
    + `<span class="pm-title">${esc(t.title || '')}</span><span class="pm-age tnum">${esc(ageShort(t.late?.since))}</span></div>`).join('');
  return `<section class="pm-group" aria-label="Late"><h3 class="pm-h">Late <span class="tnum">${late.length}</span></h3>${rows}</section>`;
}

function prLinesHTML(lines: PipelineLogLine[], tasks: TaskAny[]): string {
  if (!lines.length) return '<p class="pm-empty muted">Nothing yet</p>';
  return lines.map(line => {
    const id = line.task_id ?? '';
    const open = id ? ` data-task="${esc(id)}" role="button" tabindex="0"` : '';
    const title = id ? tasks.find(t => t.id === id)?.title : '';
    const tip = title ? ` title="${esc(title)}"` : '';
    const why = line.why ? ` · ${line.why}` : '';
    return `<div class="pm-line"${open}${tip}><span class="pm-at tnum">${esc(prClock(line.at))}</span><span>${esc(line.text + why)}</span></div>`;
  }).join('');
}

/** The tab's list: the Log, the tasks the manager was woken about, or what it learned. */
function prTabHTML(state: PrState, tasks: TaskAny[]): string {
  if (state.tab === 'tasks') {
    const mine = prTasks(state, tasks);
    return mine.length ? mine.map(t => taskRowHTML(t, {look: 'list'})).join('') : '<p class="pm-empty muted">Nothing yet</p>';
  }
  if (!state.log) return state.failed ? '<p class="pm-empty muted">Could not load</p>' : skelRows(3, {dot: false});
  return prLinesHTML(state.tab === 'log' ? state.log.log : state.log.learned, tasks);
}

/** The open tasks whose turn is the manager's: the ones it was woken about. */
const prTasks = (state: PrState, tasks: TaskAny[]): TaskAny[] =>
  tasks.filter(t => t.status === 'open' && t.turn?.who === state.manager);

function prDraw(state: PrState): void {
  if (PR !== state) return;
  const host = state.host, tasks = pvTasksOf(state.pipelineId), open = prOpen();
  const name = actorLabel(state.manager);
  host.classList.add('pm-rail');
  prPlace(host, open);
  document.getElementById('task-manager')?.setAttribute('aria-pressed', String(open));
  if (!open) return;
  let shell = host.querySelector<HTMLElement>(':scope > .pm-shell');
  if (!shell) {
    host.innerHTML = `<div class="pm-shell"><div class="pm-panel"><header class="pm-head"></header><div class="pm-tabs" role="tablist"></div>
      <div class="pm-body"></div><div class="pm-chat"></div></div></div>`;
    shell = host.querySelector<HTMLElement>(':scope > .pm-shell');
  }
  if (!shell) return;
  const head = shell.querySelector<HTMLElement>('.pm-head');
  if (head) {
    head.innerHTML = `<span class="pm-face">${actorFace(state.manager, 32)}</span><b class="pm-name">${esc(name)}</b>`
      + `<button type="button" class="ghost pm-x" data-pm-hide aria-label="Close" title="Close">${PR_HIDE_ICON}</button>`;
  }
  const count = prTasks(state, tasks).length;
  const tabs = shell.querySelector<HTMLElement>('.pm-tabs');
  if (tabs) {
    // Learned shows once the manager has learned something.
    const shown = PR_TABS.filter(([id]) => id !== 'learned' || !!state.log?.learned.length);
    if (!shown.some(([id]) => id === state.tab)) state.tab = 'log';
    tabs.innerHTML = shown.map(([id, label]) => {
      const on = state.tab === id, n = id === 'tasks' && count ? ` <span class="tnum">${count}</span>` : '';
      return `<button type="button" role="tab" class="pm-tab${on ? ' on' : ''}" data-pm-tab="${id}" aria-selected="${on}">${label}${n}</button>`;
    }).join('');
  }
  const body = shell.querySelector<HTMLElement>('.pm-body');
  if (body) body.innerHTML = prLateHTML(tasks) + prTabHTML(state, tasks);
  const chat = shell.querySelector<HTMLElement>('.pm-chat');
  if (chat && !state.chat) state.chat = threadMount(chat, {dm: state.manager, card: true, header: false});
}

async function prRead(state: PrState): Promise<void> {
  state.readAt = Date.now();
  try {
    const log = await pipelineLogRead(state.pipelineId);
    if (PR !== state) return;
    state.log = log; state.failed = false;
  } catch {
    if (PR !== state) return;
    state.failed = true;
  }
  prDraw(state);
}

function prClick(ev: MouseEvent): void {
  const state = PR, target = eventElement(ev);
  if (!state || !target || !state.host.contains(target)) return;
  // The rail sits outside #task-body, so the page's row handler never sees its rows: it opens them itself.
  const row = target.closest<HTMLElement>('[data-task]');
  if (row?.dataset['task']) { void taskModalOpen(row.dataset['task']); return; }
  const tab = target.closest<HTMLElement>('[data-pm-tab]');
  if (tab) { state.tab = (tab.dataset['pmTab'] as PrTab | undefined) ?? 'log'; prDraw(state); return; }
  if (target.closest('[data-pm-hide]')) pipelineRailToggle(false);
}

/** Enter or Space on a focused task row opens it, as on the page (tasks-page.ts); keys typed in the chat stay there. */
function prKey(ev: KeyboardEvent): void {
  const target = eventElement(ev), row = target?.closest<HTMLElement>('[data-task]');
  if ((ev.key === 'Enter' || ev.key === ' ') && row && row === target) { ev.preventDefault(); row.click(); }
}

/** The header's chat icon: opens or closes the rail of the pipeline in view. */
function pipelineRailToggle(open = !prOpen()): void {
  prKeepOpen(open);
  const state = PR;
  if (!state) return;
  if (!open) { threadUnmount(state.chat); state.chat = null; }
  prDraw(state);
}

/** Whether the pipeline has a rail here: a manager, and not a phone. The header shows the chat icon for it. */
const pipelineRailFor = (rec: PipelineRec | null | undefined): boolean =>
  !!rec?.manager_id && !window.matchMedia('(max-width:600px)').matches;

/** Mounts the rail into the page's host for a pipeline with a manager, or draws it again with the tasks now held; a
    pipeline with no manager, or a phone, has no rail. */
function pipelineRailSync(host: HTMLElement, rec: PipelineRec): void {
  if (!rec.manager_id || !pipelineRailFor(rec)) {
    if (PR?.host === host) pipelineRailLeave();
    prPlace(host, false);
    return;
  }
  const manager = prActor(rec.manager_id);
  if (!PR || PR.host !== host || PR.pipelineId !== rec.id || PR.manager !== manager || !host.isConnected) {
    pipelineRailLeave();
    host.replaceChildren();
    PR = {host, pipelineId: rec.id, manager, tab: 'log', log: null, failed: false, readAt: 0, chat: null};
    host.addEventListener('click', prClick);
    host.addEventListener('keydown', prKey);
  }
  const state = PR;
  prDraw(state);
  if (Date.now() - state.readAt > PR_READ_MS) void prRead(state);
}

/** The rail is gone (another pipeline, another page): its chat goes with it. */
function pipelineRailLeave(): void {
  const state = PR;
  if (!state) return;
  PR = null;
  state.host.removeEventListener('click', prClick);
  state.host.removeEventListener('keydown', prKey);
  threadUnmount(state.chat);
  state.host.closest('.tasks-page')?.classList.remove('has-rail');
}
