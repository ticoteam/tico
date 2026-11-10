/* ui/app/task-pipelines.ts — One pipeline on the Tasks page (docs/pipelines-v3.md S7, S8, S17.3): its open tasks as
   columns, one per work stage, or as a list grouped by stage in the same order; on a phone, one stage at a time under a
   row of stage tabs. Done and Closed are not columns: they sit behind the page's archive button with Snoozed.
   A stage's header is its name and its number ("Build 2/3" where it has a limit); tapping it opens the stage's settings
   for whoever may change the pipeline. Search and filter are the page's (task-row.ts taskFilterMatch); the page shell
   (tasks-page.ts) draws the header and calls pipelineViewRender. Moving a task between columns is task-board.ts.
   Reads: GET /api/v2/pipelines/{id} and GET /api/v2/tasks (open tasks of the pipeline), through task-api.ts.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

type PvLayout = 'list' | 'columns';
interface PvOpts { layout: PvLayout; q: string; filters: TaskFilters; phone: boolean }
/** A move the engine refused, shown above the stages: a push past to confirm with a why, or the engine's reason. */
interface PvBar { taskId: string; to: string; toName: string; push: boolean; text: string }
interface PvState {
  root: HTMLElement; pipelineId: string; opts: PvOpts;
  rec: PipelineRec | null; tasks: TaskBriefV3[]; loadedAt: number; seq: number; failed: string;
  /** The stage shown on a phone. */
  stage: string;
  /** Moves drawn before the engine answers: task id -> the stage it was moved to. */
  moving: Map<string, string>;
  bar: PvBar | null;
  poll: number;
}

let PV: PvState | null = null;
/** A pipeline's held data is drawn again without a read for this long (a search typed, a filter set). */
const PV_FRESH_MS = 5000;
/** A safety read while the view is open; live task events read sooner (pipelineViewLive). */
const PV_POLL_MS = 180000;
const PV_STAGE_KEY = 'tico.pipeline.stage';

function pvStageKept(pipelineId: string): string {
  try { return (JSON.parse(localStorage.getItem(PV_STAGE_KEY) || '{}') as Record<string, string>)[pipelineId] ?? ''; } catch { return ''; }
}
function pvStageKeep(pipelineId: string, stage: string): void {
  try {
    const all = JSON.parse(localStorage.getItem(PV_STAGE_KEY) || '{}') as Record<string, string>;
    all[pipelineId] = stage;
    localStorage.setItem(PV_STAGE_KEY, JSON.stringify(all));
  } catch { /* storage blocked: not kept */ }
}

/** Draws one pipeline into root. Called again by the page on every search, filter or layout change: the same pipeline is
    drawn from what is held, and read again only when that is older than a few seconds. */
async function pipelineViewRender(root: HTMLElement, pipelineId: string, opts: PvOpts): Promise<void> {
  const same = PV && PV.root === root && PV.pipelineId === pipelineId && root.isConnected ? PV : null;
  const state: PvState = same ?? {root, pipelineId, opts, rec: null, tasks: [], loadedAt: 0, seq: 0, failed: '',
    stage: pvStageKept(pipelineId), moving: new Map(), bar: null, poll: 0};
  if (!same) { pipelineViewLeave(); PV = state; pvBind(state); }
  state.opts = opts;
  if (state.rec) pvDraw(state);
  else root.innerHTML = skelRows(4, {dot: false});
  if (Date.now() - state.loadedAt > PV_FRESH_MS) await pvLoad(state);
}

/** The view is gone (another pipeline, another view, another page): its poll and rail go with it. */
function pipelineViewLeave(): void {
  const state = PV;
  if (!state) return;
  window.clearInterval(state.poll);
  pipelineRailLeave();
  PV = null;
}

/** Live task events touched the tasks: read the pipeline's tasks again (at most once a second). */
let PV_LIVE = 0;
function pipelineViewLive(): void {
  if (!PV || PV_LIVE) return;
  PV_LIVE = window.setTimeout(() => { PV_LIVE = 0; if (PV) void pvLoad(PV); }, 1000);
}

async function pvLoad(state: PvState): Promise<void> {
  const seq = ++state.seq;
  try {
    const [rec, tasks] = await Promise.all([
      pipelineRec(state.pipelineId),
      taskBriefs({pipeline: state.pipelineId, status: 'open', limit: 500}),
    ]);
    if (PV !== state || seq !== state.seq) return;
    state.rec = rec;
    state.tasks = tasks.filter(t => t.pipeline_id === state.pipelineId && t.status === 'open');
    state.loadedAt = Date.now();
    state.failed = '';
    // A move the engine has answered shows as the read has it.
    for (const [id, to] of state.moving) if (state.tasks.find(t => t.id === id)?.stage_id === to) state.moving.delete(id);
  } catch (error) {
    if (PV !== state || seq !== state.seq) return;
    state.failed = errorMessage(error, 'Could not load this pipeline.');
  }
  pvDraw(state);
}

function pvBind(state: PvState): void {
  state.poll = window.setInterval(() => {
    if (!state.root.isConnected) { if (PV === state) pipelineViewLeave(); return; }
    if (!document.hidden) void pvLoad(state);
  }, PV_POLL_MS);
  state.root.addEventListener('click', ev => pvClick(state, ev));
  state.root.addEventListener('submit', ev => pvSubmit(state, ev));
  taskBoardBind(state);
}

// ---------------------------------------------------------------- drawing

/** A task as the view draws it: in the stage a move put it in while the engine has not answered. */
const pvShown = (state: PvState, t: TaskBriefV3): TaskBriefV3 => {
  const to = state.moving.get(t.id);
  return to ? {...t, stage_id: to} : t;
};

/** "Ideas 12", "Spec 2/2": the stage's name and its number of open tasks, out of its limit when it has one. */
function pvStageCount(state: PvState, stage: PipelineStageRec): string {
  const n = state.tasks.filter(t => pvShown(state, t).stage_id === stage.id).length;
  return stage.task_limit ? `${n}/${stage.task_limit}` : String(n);
}

/** S8: a stage's header. It opens the stage's settings for whoever may change the pipeline, and is plain text otherwise. */
function pvStageHeadHTML(state: PvState, stage: PipelineStageRec): string {
  const inner = `<span class="pv-stage-name">${esc(stage.name)}</span><span class="pv-stage-n tnum">${esc(pvStageCount(state, stage))}</span>`;
  return state.rec?.may_change
    ? `<button type="button" class="pv-stage-h" data-pv-settings="${esc(stage.id ?? '')}">${inner}</button>`
    : `<span class="pv-stage-h">${inner}</span>`;
}

/** The tasks of one stage that the search and the filters let through, in the list's order. */
function pvInStage(state: PvState, stageId: string): TaskBriefV3[] {
  return state.tasks.map(t => pvShown(state, t))
    .filter(t => t.stage_id === stageId && taskFilterMatch(t, state.opts.q, state.opts.filters));
}

const pvSearching = (opts: PvOpts): boolean => !!opts.q.trim() || Object.values(opts.filters).some(v => Array.isArray(v) ? v.length > 0 : !!v);

function pvColumnsHTML(state: PvState, stages: PipelineStageRec[]): string {
  const cols = stages.map(stage => {
    const id = stage.id ?? '';
    const cards = pvInStage(state, id).map(t => `<div class="pv-card" data-pv-task="${esc(t.id)}">${taskCardHTML(t)}</div>`).join('');
    return `<section class="pv-col" data-pv-col="${esc(id)}" aria-label="${esc(stage.name)}"><header class="pv-col-head">${pvStageHeadHTML(state, stage)}</header>
      <div class="pv-col-body">${cards}</div></section>`;
  }).join('');
  return `<div class="pv-cols" data-pv-drag>${cols}</div>`;
}

function pvListHTML(state: PvState, stages: PipelineStageRec[]): string {
  const searching = pvSearching(state.opts);
  const groups = stages.map(stage => {
    const rows = pvInStage(state, stage.id ?? '');
    if (!rows.length && searching) return '';
    return `<section class="pv-group" aria-label="${esc(stage.name)}"><h2 class="pv-group-head">${pvStageHeadHTML(state, stage)}</h2>
      <div class="pv-rows">${rows.map(t => taskRowHTML(t, {look: 'list'})).join('')}</div></section>`;
  }).join('');
  return groups || '<p class="pv-empty muted">Nothing found</p>';
}

/** S17.3: a row of stage tabs, then the chosen stage's tasks. */
function pvPhoneHTML(state: PvState, stages: PipelineStageRec[]): string {
  const ids = stages.map(s => s.id ?? '');
  const shown = ids.includes(state.stage) ? state.stage : ids.find(id => pvInStage(state, id).length) ?? ids[0] ?? '';
  state.stage = shown;
  const tabs = stages.map(stage => {
    const on = stage.id === shown;
    return `<button type="button" role="tab" class="pv-tab${on ? ' on' : ''}" data-pv-tab="${esc(stage.id ?? '')}" aria-selected="${on}">`
      + `${esc(stage.name)} <span class="tnum">${esc(pvStageCount(state, stage))}</span></button>`;
  }).join('');
  const rows = pvInStage(state, shown).map(t => taskRowHTML(t, {look: 'list'})).join('');
  return `<div class="pv-tabs" role="tablist" aria-label="Stages">${tabs}</div><div class="pv-rows pv-phone">${rows}</div>`;
}

function pvBarHTML(bar: PvBar | null): string {
  if (!bar) return '';
  if (bar.push) {
    return `<form class="pv-bar" data-pv-push role="alert"><span class="pv-bar-text">${esc(bar.text)}</span>
      <input name="why" required maxlength="500" placeholder="Why" aria-label="Why" autocomplete="off">
      <button type="submit" class="primary">Move</button>
      <button type="button" class="ghost" data-pv-bar-x aria-label="Close">✕</button></form>`;
  }
  return `<div class="pv-bar" role="alert"><span class="pv-bar-text">${esc(bar.text)}</span>
    <button type="button" class="ghost" data-pv-bar-x aria-label="Close">✕</button></div>`;
}

function pvDraw(state: PvState): void {
  if (PV !== state || !state.root.isConnected) return;
  const rec = state.rec;
  if (!rec) {
    state.root.innerHTML = state.failed ? `<p class="pv-empty muted" role="alert">${esc(state.failed)}</p>` : skelRows(4, {dot: false});
    return;
  }
  const body = state.opts.phone ? pvPhoneHTML(state, rec.stages)
    : state.opts.layout === 'columns' ? pvColumnsHTML(state, rec.stages) : pvListHTML(state, rec.stages);
  // The shell stays across draws, so the manager's rail beside the stages (and its chat) is not drawn again.
  let shell = state.root.querySelector<HTMLElement>(':scope > .pv');
  if (!shell) {
    state.root.innerHTML = '<div class="pv"><div class="pv-main"></div></div>';
    shell = state.root.querySelector<HTMLElement>(':scope > .pv');
  }
  const main = shell?.querySelector<HTMLElement>(':scope > .pv-main');
  if (!shell || !main) return;
  shell.classList.toggle('pv-is-phone', state.opts.phone);
  const focus = pvFocusKeep(main);
  main.innerHTML = pvBarHTML(state.bar) + body;
  focus();
  pipelineRailSync(shell, rec, state.tasks, state.opts.phone);
}

/** The why being typed survives a redraw (a live read lands while it is typed). */
function pvFocusKeep(root: HTMLElement): () => void {
  const field = root.querySelector<HTMLInputElement>('[data-pv-push] input[name="why"]');
  if (!field) return () => undefined;
  const text = field.value, typing = document.activeElement === field;
  return () => {
    const next = root.querySelector<HTMLInputElement>('[data-pv-push] input[name="why"]');
    if (!next) return;
    next.value = text;
    if (typing) next.focus();
  };
}

// ---------------------------------------------------------------- what a person does here

function pvClick(state: PvState, ev: MouseEvent): void {
  const target = eventElement(ev);
  if (!target) return;
  const settings = target.closest<HTMLElement>('[data-pv-settings]');
  if (settings) { void openPipelineEditor(state.pipelineId, settings.dataset['pvSettings'] ?? ''); return; }
  const tab = target.closest<HTMLElement>('[data-pv-tab]');
  if (tab) { pvStageShow(state, tab.dataset['pvTab'] ?? ''); return; }
  if (target.closest('[data-pv-bar-x]')) { state.bar = null; pvDraw(state); }
}

function pvStageShow(state: PvState, stage: string): void {
  if (!stage || stage === state.stage) return;
  state.stage = stage;
  pvStageKeep(state.pipelineId, stage);
  pvDraw(state);
  state.root.querySelector('.pv-tab.on')?.scrollIntoView({inline: 'center', block: 'nearest'});
}

/** A phone swipe steps to the next (1) or the previous (-1) stage. */
function pvStageStep(state: PvState, by: number): void {
  const ids = (state.rec?.stages ?? []).map(s => s.id ?? '');
  const to = ids[ids.indexOf(state.stage) + by];
  if (to) pvStageShow(state, to);
}

let PV_SWIPE: {x: number; y: number} | null = null;
document.addEventListener('touchstart', (ev: TouchEvent) => {
  const target = ev.target instanceof Element ? ev.target : null, first = ev.touches[0];
  PV_SWIPE = first && target?.closest('.pv-is-phone') ? {x: first.clientX, y: first.clientY} : null;
}, {passive: true});
document.addEventListener('touchend', (ev: TouchEvent) => {
  const start = PV_SWIPE, end = ev.changedTouches[0];
  PV_SWIPE = null;
  if (!start || !end || !PV) return;
  const dx = end.clientX - start.x, dy = end.clientY - start.y;
  if (Math.abs(dx) > 60 && Math.abs(dx) > 2 * Math.abs(dy)) pvStageStep(PV, dx < 0 ? 1 : -1);
}, {passive: true});

function pvSubmit(state: PvState, ev: SubmitEvent): void {
  const form = ev.target instanceof HTMLFormElement && ev.target.hasAttribute('data-pv-push') ? ev.target : null;
  const bar = state.bar;
  if (!form || !bar) return;
  ev.preventDefault();
  const why = String(new FormData(form).get('why') ?? '').trim();
  if (!why) return;
  state.bar = null;
  const t = state.tasks.find(x => x.id === bar.taskId);
  if (t) pvMove(state, t, bar.to, why);
}

/** Moves a task to a stage of this pipeline: `next` when it is the stage after its own, else the stage's id. The task is
    drawn there at once; a refusal puts it back and says what the engine said: what is missing, with a why to push past it
    when it can be pushed past. */
function pvMove(state: PvState, t: TaskBriefV3, stageId: string, why = ''): void {
  const stages = state.rec?.stages ?? [];
  const from = stages.findIndex(s => s.id === t.stage_id), to = stages.findIndex(s => s.id === stageId);
  const target = stages[to];
  if (to < 0 || !target || from === to) return;
  const where = to === from + 1 ? 'next' : stageId;
  state.moving.set(t.id, stageId);
  state.bar = null;
  pvDraw(state);
  void (async () => {
    try {
      await taskMove(t.id, where, t.version, why || undefined);
      if (PV === state) await pvLoad(state);
    } catch (error) {
      if (PV !== state) return;
      state.moving.delete(t.id);
      state.bar = pvRefusedBar(t, stageId, target.name, error, !why);
      if (taskRefusedOf(error)?.error === 'task_changed') await pvLoad(state);
      else pvDraw(state);
    }
  })();
}

/** What the view says about a refused move: the engine's own words. A move forward that misses requirements a person may
    push past asks for the why ("Move to Review 1 without Checks pass?"); anything else is the engine's reason. */
function pvRefusedBar(t: TaskBriefV3, stageId: string, stageName: string, error: unknown, mayPush: boolean): PvBar {
  const refused = taskRefusedOf(error);
  const missing = refused?.missing ?? [];
  if (refused?.error === 'task_not_met' && mayPush && missing.length) {
    return {taskId: t.id, to: stageId, toName: stageName, push: true, text: `Move to ${stageName} without ${missing.join(', ')}?`};
  }
  const words = refused ? [refused.detail, missing.length ? `Missing: ${missing.join(', ')}.` : ''].filter(Boolean).join(' ')
    : errorMessage(error, 'Not moved.');
  return {taskId: t.id, to: stageId, toName: stageName, push: false, text: `${t.title}: ${words}`};
}
