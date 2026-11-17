/* ui/app/task-pipelines.ts — One pipeline on the Tasks page (docs/dev/pipelines-v3.md S7, S8, S17.3): its open tasks as
   columns, one per work stage, or as a list grouped by stage in the same order; on a phone, one stage at a time under a
   row of stage tabs. Done and Closed are not columns: they sit behind the page's archive button with Snoozed. A snoozed
   task stays in its stage, after every task that is not, dimmed, with the day it wakes (or Snoozed) and a moon that is
   its one button: Unsnooze. It holds no slot, so a stage's number leaves it out. A blocked task (it depends on a task
   still open) keeps full colour, with a stop mark and "#304" where a snoozed one shows its day, after the awake tasks and
   before the snoozed ones; it holds no slot either. A task others depend on says "Blocks #307".
   A stage's header is its name and its number ("Build 2/3" where it has a limit); tapping it opens the stage's settings
   for whoever may change the pipeline. Beside it, the quiet rules mark (a gavel, struck through when none apply) opens
   the stage's rules (stage-rules.ts); then Flow's mark and how many items in the stage wait on the person opens Flow on
   the stage (decide.ts decideStageOpen); there is no mark where none wait. On a phone the rules mark sits at the end of the stage
   tabs. The columns scroll sideways in their own box, a fade on each edge that has more beyond it; a stage with no
   tasks is as tall as its header. A stage draws its first 30 tasks, then "Show 250 more" for the rest of it (PV_CAP); a
   draw changes only the columns that changed (pvPaint), so a flood of live changes does not draw the whole board. The
   page (tasks-page.ts) owns the header, loading the pipeline and its tasks, search, filter, the archive and opening a
   task (any [data-task] element); it calls the hooks set at the end of this file. Moving a task between columns is
   task-board.ts; a refused move shows the engine's reason above the stages.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

interface PvOpts { layout: 'list' | 'columns'; phone: boolean; q: string; filters: TaskFilters }
/** A move the engine refused, shown above the stages: a push past to confirm with a why, or the engine's reason. */
interface PvBar { taskId: string; to: string; push: boolean; text: string }
interface PvState {
  rec: PipelineRec; tasks: TaskAny[]; opts: PvOpts;
  /** Where the page put the view; drawn again here after a move or a tab. */
  root: HTMLElement | null;
  /** The stage shown on a phone. */
  stage: string;
  /** Moves drawn before the engine answers: task id -> the stage it was moved to. */
  moving: Map<string, string>;
  bar: PvBar | null;
  /** Where the columns were scrolled (scrollLeft, scrollTop), put back after each draw. */
  x: number;
  y: number;
  /** The stages whose every card is shown (Show N more); the others show their first PV_CAP. */
  open: Set<string>;
  /** What was last drawn in the root, so a draw changes only what changed. */
  drawn: PvDrawn | null;
  /** The last draw replaced the whole view: the columns' scroll is put back (bind). A draw of some columns keeps the
   * columns' element and its scroll, and reads and sets nothing (each read would lay the board out on the spot). */
  whole: boolean;
  /** Selecting (⋯ › Select, or a shift-, ⌘- or Ctrl-click on a card): a click on a card picks it instead of opening it. */
  selecting: boolean;
  /** The tasks picked, by id, and the stage of the last one picked (All in <stage>). */
  sel: Set<string>;
  selStage?: string;
}
/** The view as last drawn: its element, what it was drawn as (the layout and the stages), the bar, and each column's header
 * and cards by stage (columns), or the whole view's HTML (the list, a phone). */
interface PvDrawn { el: Element; frame: string; bar: string; cols: Map<string, {head: string; body: string}>; whole: string }
/** A stage draws its first PV_CAP cards and says how many more: a column of 300 is not 300 cards. */
const PV_CAP = 30;

let PV: PvState | null = null;
const PV_BOUND = new WeakSet<HTMLElement>();
const PV_STAGE_KEY = 'tico.pipeline.stage';
const PV_SCROLL_KEY = 'tico.pipeline.scroll';

/** The stage each pipeline last showed on a phone. Throws where storage is blocked. */
const pvStagesKept = (): Record<string, string> => JSON.parse(localStorage.getItem(PV_STAGE_KEY) || '{}') as Record<string, string>;
function pvStageKept(pipelineId: string): string {
  try { return pvStagesKept()[pipelineId] ?? ''; } catch { return ''; }
}
function pvStageKeep(pipelineId: string, stage: string): void {
  try {
    const all = pvStagesKept();
    all[pipelineId] = stage;
    localStorage.setItem(PV_STAGE_KEY, JSON.stringify(all));
  } catch { /* storage blocked: not kept */ }
}

/** How far each pipeline's columns were scrolled sideways in this tab: a reload or another page and back opens there, a new
 * tab at the first stage. Throws where storage is blocked. */
const pvScrollsKept = (): Record<string, number> => JSON.parse(sessionStorage.getItem(PV_SCROLL_KEY) || '{}') as Record<string, number>;
function pvScrollKept(pipelineId: string): number {
  try { const x = pvScrollsKept()[pipelineId]; return typeof x === 'number' && x > 0 ? x : 0; } catch { return 0; }
}
function pvScrollKeep(pipelineId: string, x: number): void {
  try {
    const all = pvScrollsKept();
    all[pipelineId] = x;
    sessionStorage.setItem(PV_SCROLL_KEY, JSON.stringify(all));
  } catch { /* storage blocked: not kept */ }
}

/** The view's state for a pipeline: the same one while the page draws the same pipeline again. */
function pvStateFor(rec: PipelineRec): PvState {
  if (PV && PV.rec.id === rec.id) return PV;
  PV = {rec, tasks: [], opts: {layout: 'columns', phone: false, q: '', filters: {}}, root: null,
    stage: pvStageKept(rec.id), moving: new Map(), bar: null, x: pvScrollKept(rec.id), y: 0, open: new Set(), drawn: null, whole: true,
    selecting: false, sel: new Set()};
  return PV;
}

/** A task set aside in its stage (`snoozed`): drawn last there, and holding no slot. */
const pvSnoozed = (t: TaskAny): boolean => !!(t as {snoozed?: boolean}).snoozed;

/** A task that depends on another one still open (`blocked_by`): drawn in full colour after the awake ones and before
 * the snoozed ones, with a stop mark and "#304" where a snoozed one shows its day. It holds no slot. Blocked wins over
 * a snooze: no list says Snoozed for a blocked task. */
const pvBlocked = (t: TaskAny): boolean => taskBlockedBy(t).length > 0;

// ---------------------------------------------------------------- drawing

/** A task as the view draws it: in the stage a move put it in while the engine has not answered. */
const pvShown = (state: PvState, t: TaskAny): TaskAny => {
  const to = state.moving.get(t.id);
  return to ? {...t, stage_id: to} : t;
};

/** A list row that waits only on a person's OK or answer: it holds no slot in a stage with a limit (`on_person`). */
const pvOnPerson = (t: TaskAny): boolean => !!(t as {on_person?: boolean}).on_person;

/** How many of a limited stage's open tasks wait only on a person; 0 for a stage without a limit, where every task
 * counts the same. */
function pvPersonCount(state: PvState, stage: PipelineStageRec): number {
  if (!stage.task_limit) return 0;
  return state.tasks.filter(t => pvShown(state, t).stage_id === stage.id && pvOnPerson(t) && !pvSnoozed(t) && !pvBlocked(t)).length;
}

/** "Ideas 12", "Spec 20": the number of tasks the stage lists, the ones a person sees in its column or on its phone tab
 * (snoozed and blocked ones included, search and filters applied), so the number is what is below it. */
function pvStageCount(state: PvState, stage: PipelineStageRec): number {
  return pvInStage(state, stage.id ?? '').length;
}

/** The slots a limited stage's tasks hold: awake, not blocked, and not waiting only on a person's OK or answer
 * (task_engine.has_room; a blocked task holds no slot either, task_depends). */
function pvSlotsUsed(state: PvState, stage: PipelineStageRec): number {
  const holding = state.tasks.filter(t => pvShown(state, t).stage_id === stage.id && !pvSnoozed(t) && !pvBlocked(t));
  return holding.length - holding.filter(pvOnPerson).length;
}

/** A limited stage with every slot used: the one time its limit is shown, on its number (pvStageCount). */
const pvStageFull = (state: PvState, stage: PipelineStageRec): boolean => !!stage.task_limit && pvSlotsUsed(state, stage) >= stage.task_limit;

/** S8: a stage's header. It opens the stage's editor (its fields, then its Rules) for whoever may change the pipeline,
 * and is plain text otherwise; the rules mark at the header's right opens the same editor at its Rules, for anyone.
 * Beside a limited stage's number, a person mark and a count keeps the tasks that hold no slot in view. */
function pvStageHeadHTML(state: PvState, stage: PipelineStageRec): string {
  const tip = pvLimitTip(state, stage);
  const inner = `<span class="pv-stage-name">${esc(stage.name)}</span><span class="pv-stage-n tnum${pvStageFull(state, stage) ? ' full' : ''}"${tip ? ` title="${esc(tip)}" aria-label="${esc(tip)}"` : ''}>${pvStageCount(state, stage)}</span>`;
  return state.rec.may_change
    ? `<button type="button" class="pv-stage-h" data-pv-settings="${esc(stage.id ?? '')}" title="${esc(`Edit ${stage.name}`)}">${inner}</button>`
    : `<span class="pv-stage-h">${inner}</span>`;
}

/** A limited stage's slots, in its number's tooltip: "5 of 20 slots used. 15 more wait on a person and hold no slot." */
function pvLimitTip(state: PvState, stage: PipelineStageRec): string {
  if (!stage.task_limit) return '';
  const person = pvPersonCount(state, stage);
  return `${pvSlotsUsed(state, stage)} of ${stage.task_limit} slots used.` + (person ? ` ${person} more ${person === 1 ? 'waits' : 'wait'} on a person and ${person === 1 ? 'holds' : 'hold'} no slot.` : '');
}

/** Flow n: Flow on the stage, with how many of its items wait on the person; '' when none do. */
function pvPlayHTML(stage: PipelineStageRec): string {
  const n = stage.id ? needsStageCount(stage.id) : 0;
  // Its slot is kept when none wait, so nothing moves when the count arrives.
  if (!n) return '<span class="pv-play pv-play-none" aria-hidden="true"></span>';
  return `<button type="button" class="pv-play" data-pv-play="${esc(stage.id ?? '')}" aria-label="${esc(`Flow, ${stage.name}, ${n}`)}" title="Flow">`
    + `${flowMarkHTML(13)}<span class="tnum">${n}</span></button>`;
}

/** The tasks of one stage that the search and the filters let through, in the order the page gave them: the awake
 * ones, then the blocked ones, then the snoozed ones. */
function pvInStage(state: PvState, stageId: string): TaskAny[] {
  const shown = state.tasks.map(t => pvShown(state, t))
    .filter(t => t.stage_id === stageId && taskFilterMatch(t, state.opts.q, state.opts.filters));
  const blocked = shown.filter(pvBlocked), rest = shown.filter(t => !pvBlocked(t));
  return [...rest.filter(t => !pvSnoozed(t)), ...blocked, ...rest.filter(pvSnoozed)];
}

/** A stop sign: the mark on a blocked task. */
const PV_STOP_ICON = '<svg viewBox="0 0 14 14" width="12" height="12" aria-hidden="true" focusable="false">'
  + '<path d="M4.7 1.5h4.6l3.2 3.2v4.6l-3.2 3.2H4.7L1.5 9.3V4.7z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>'
  + '<path d="M4.6 7h4.8" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>';

/** Where a blocked task's age goes: the stop mark and the tasks it waits for, "E304" or "E304, E321", or a few words of
 * the title of one with no number; never an id ("Blocked" when the reader may open none of them). Each opens the task it
 * names (its own data-task: the click is the row's, and the innermost task wins). Its tip is the task's line, "Blocked by
 * E304 Growth synced sources part 1, in Build". */
function pvBlockedTailHTML(t: TaskAny): string {
  const deps = taskBlockedBy(t).filter(r => r.id && taskDepName(r)), names = deps.map(taskDepName);
  const label = names.length ? `Blocked by ${names.join(', ')}` : 'Blocked';
  const refs = deps.map((r, i) => `<button type="button" class="pv-blocked-ref" data-task="${esc(r.id || '')}" aria-label="Open ${esc(names[i] || '')}">${esc(names[i] || '')}</button>`);
  return `<span class="tk-age pv-blocked-by" aria-label="${esc(label)}" title="${esc(label)}">`
    + `<span class="pv-stop">${PV_STOP_ICON}</span>${refs.join(', ') || 'Blocked'}</span>`;
}

/** A crescent moon: the sleep mark on a snoozed task. */
const PV_SLEEP_ICON = '<svg viewBox="0 0 14 14" width="12" height="12" aria-hidden="true" focusable="false">'
  + '<path d="M11.6 8.9A5 5 0 0 1 5.1 2.4a5 5 0 1 0 6.5 6.5z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>';

/** When a snoozed task wakes, in the shared day words ("Tomorrow", "Nov 18"), or "Snoozed" when its snooze has no end.
 * Its turn is the snooze (waiting_on). */
const pvWake = (t: TaskAny): string => shortDate(t.turn?.state === 'snoozed' ? t.turn.until : null) || 'Snoozed';

/** A snoozed task, dimmed: the title, the moon and the day it wakes. The moon is the card's one button: Unsnooze, in one
 * click (its label and title; a 44 px tap area on a phone, .tap-mark, that stays inside the card's gap). `card` in a column, else a row of the list or the phone's
 * stage. Its tip is the task's line, as an awake one's is: for a snoozed task, the snooze's why. */
function pvSnoozedHTML(t: TaskAny, card: boolean): string {
  const wake = pvWake(t);
  const mark = `<button type="button" class="tk-mark pv-sleep tap-mark" data-pv-unsnooze="${esc(t.id)}" aria-label="Unsnooze" title="Unsnooze">${PV_SLEEP_ICON}</button>`;
  const title = `<span class="tk-title">${esc(t.title || '')}</span>`, when = `<span class="tk-age pv-wake">${esc(wake)}</span>`;
  const tip = esc(taskLine(t)), attrs = `data-task="${esc(t.id)}" role="button" tabindex="0"${tip ? ` data-tip="${tip}"` : ''}`;
  return card
    ? `<div class="tk-card pv-snoozed" ${attrs}>${title}${mark}${when}</div>`
    : `<div class="tk-row tk-row-dim pv-snoozed" ${attrs}>${mark}${title}${when}</div>`;
}
const pvCardHTML = (t: TaskAny): string => pvBlocked(t) ? taskCardHTML(t, pvBlockedTailHTML(t))
  : pvSnoozed(t) ? pvSnoozedHTML(t, true) : taskCardHTML(t);
const pvRowHTML = (t: TaskAny): string => pvBlocked(t) ? taskRowHTML({...t, snoozed: false}, {look: 'list', tail: pvBlockedTailHTML(t)})
  : pvSnoozed(t) ? pvSnoozedHTML(t, false) : taskRowHTML(t, {look: 'list'});

const pvSearching = (opts: PvOpts): boolean => !!opts.q.trim() || taskFiltersOn(opts.filters);

/** A stage's tasks drawn as `each` draws one: its first PV_CAP, then Show N more, unless it was opened in full. */
function pvStageItemsHTML(state: PvState, stageId: string, tasks: TaskAny[], each: (t: TaskAny) => string): string {
  const all = state.open.has(stageId) || tasks.length <= PV_CAP, rest = all ? 0 : tasks.length - PV_CAP;
  return (all ? tasks : tasks.slice(0, PV_CAP)).map(each).join('')
    + (rest ? `<button type="button" class="ghost pv-more" data-pv-more="${esc(stageId)}">Show ${rest} more</button>` : '');
}

/** A stage's ⋯ at its header's right: what can be done to every task it shows (stage-menu.ts). None on a stage that
 * shows no task, unless the viewer may change the pipeline (Edit stage); none in a read-only view. */
/** The stage whose ⋯ menu is open: a live draw while it is open draws its ⋯ still expanded. */
let PV_COL_OPEN = '';
function pvColMenuHTML(state: PvState, stage: PipelineStageRec): string {
  const id = stage.id ?? '';
  if (!id || S.me?.read_only_view || (!state.rec.may_change && !pvInStage(state, id).length)) return '';
  return `<button type="button" class="pv-col-more" data-pv-col-menu="${esc(id)}" aria-haspopup="menu" aria-expanded="${PV_COL_OPEN === id}"`
    + ` aria-label="${esc(`More, ${stage.name}`)}" title="More"><span class="nav-icon" aria-hidden="true">more_horiz</span></button>`;
}

/** A column's header and cards, apart: the board draws again only the parts that changed (pvPaint). */
const pvColHeadHTML = (state: PvState, stage: PipelineStageRec): string =>
  `${pvStageHeadHTML(state, stage)}${pvPlayHTML(stage)}${stageRulesButtonHTML(state.rec.id, stage)}${pvColMenuHTML(state, stage)}`;
const pvColBodyHTML = (state: PvState, stage: PipelineStageRec): string =>
  pvStageItemsHTML(state, stage.id ?? '', pvInStage(state, stage.id ?? ''), pvCardHTML);

/** The columns; `parts` takes each column's header and cards, by stage. */
function pvColumnsHTML(state: PvState, parts?: Map<string, {head: string; body: string}>): string {
  const cols = state.rec.stages.map(stage => {
    const id = stage.id ?? '', head = pvColHeadHTML(state, stage), body = pvColBodyHTML(state, stage);
    parts?.set(id, {head, body});
    return `<section class="pv-col" data-pv-col="${esc(id)}" aria-label="${esc(stage.name)}"><header class="pv-col-head">${head}</header>
      <div class="pv-col-body">${body}</div></section>`;
  }).join('');
  return `<div class="pv-cols" data-pv-drag>${cols}</div>`;
}

function pvListHTML(state: PvState): string {
  const searching = pvSearching(state.opts);
  const groups = state.rec.stages.map(stage => {
    const rows = pvInStage(state, stage.id ?? '');
    if (!rows.length && searching) return '';
    return `<section class="pv-group" aria-label="${esc(stage.name)}"><h2 class="pv-group-head">${pvColHeadHTML(state, stage)}</h2>
      <div class="pv-rows">${pvStageItemsHTML(state, stage.id ?? '', rows, pvRowHTML)}</div></section>`;
  }).join('');
  return groups || '<p class="pv-empty muted">Nothing found</p>';
}

/** On a phone's stage tab, a dot when items there wait on the person; it sits in the tab's own padding, so a tab is
    as wide with it as without. The header's ▶ Review opens them. */
function pvTabDotHTML(stage: PipelineStageRec): string {
  const n = stage.id ? needsStageCount(stage.id) : 0;
  return n ? '<span class="pv-tab-dot" aria-hidden="true"></span>' : '';
}

/** The stage a phone shows: the one picked (a tab, a swipe, a review's link), else the first with tasks, else the
    first. The fallback is not kept as a pick: a draw before the view's tasks land, or of an earlier read, would hold
    the tab on a stage the tasks have since left. */
function pvPhoneStage(state: PvState): string {
  const ids = state.rec.stages.map(s => s.id ?? '');
  return ids.includes(state.stage) ? state.stage : ids.find(id => pvInStage(state, id).length) ?? ids[0] ?? '';
}

/** S17.3: a row of stage tabs, then the chosen stage's tasks. */
function pvPhoneHTML(state: PvState): string {
  const stages = state.rec.stages, shown = pvPhoneStage(state);
  const tabs = stages.map(stage => {
    const on = stage.id === shown;
    const waits = stage.id ? needsStageCount(stage.id) : 0;
    const label = `${stage.name} ${pvStageCount(state, stage)}${waits ? `, ${waits} ${waits === 1 ? 'waits' : 'wait'} on you` : ''}`;
    return `<button type="button" role="tab" class="pv-tab${on ? ' on' : ''}" data-pv-tab="${esc(stage.id ?? '')}" aria-selected="${on}" aria-label="${esc(label)}">`
      + `${esc(stage.name)} <span class="tnum${pvStageFull(state, stage) ? ' full' : ''}">${pvStageCount(state, stage)}</span>${pvTabDotHTML(stage)}</button>`;
  }).join('');
  // The column's cards (task #220); an empty stage says so in one line (style guide: empty lists say so in one line).
  const rows = pvStageItemsHTML(state, shown, pvInStage(state, shown), pvCardHTML)
    || `<p class="pv-empty muted">${pvSearching(state.opts) ? 'Nothing found' : 'Nothing here'}</p>`;
  // A phone has no tooltip: a limited stage whose tasks wait on a person says so in one muted line above its cards.
  // No ▶ beside the tabs: the header's ▶ Review opens the open stage's items (tasks-page.ts tasksReview).
  const on = stages.find(s => s.id === shown);
  const rules = on ? stageRulesButtonHTML(state.rec.id, on) + pvColMenuHTML(state, on) : '';
  const tip = on && pvPersonCount(state, on) ? pvLimitTip(state, on) : '';
  return `<div class="pv-phone-head"><div class="pv-tabs" role="tablist" aria-label="Stages">${tabs}</div>${rules}</div>`
    + `${tip ? `<p class="pv-slots">${esc(tip)}</p>` : ''}<div class="pv-phone">${rows}</div>`;
}

/** The picking bar and the picked cards (and the bar's clicks): task-pick.ts sets both when it loads. A pick or Select
    loads it first, and nothing is picked before that, so until then there is nothing to draw. */
const PV_PICK: {html: (state: PvState) => string; paint: (state: PvState, el: Element | null) => void} = {html: () => '', paint: () => {}};

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

function pvHTML(state: PvState, parts?: Map<string, {head: string; body: string}>): string {
  const body = state.opts.phone ? pvPhoneHTML(state) : state.opts.layout === 'columns' ? pvColumnsHTML(state, parts) : pvListHTML(state);
  return `<div class="pv${state.opts.phone ? ' pv-is-phone' : ''}">${pvBarHTML(state.bar)}${PV_PICK.html(state)}${body}</div>`;
}

/** Draws the view into `root`, changing only what changed since the last draw there: the columns keep their elements,
 * and a column whose header or cards changed has only that part drawn again (a live flood of task changes touches a
 * few columns, not the whole board). The list and a phone are drawn whole, and only when they changed. */
function pvPaint(state: PvState, root: HTMLElement): void {
  const was = state.drawn, el = root.firstElementChild, columns = !state.opts.phone && state.opts.layout === 'columns';
  const frame = `${state.opts.phone ? 'phone' : state.opts.layout}|${state.rec.stages.map(s => `${s.id ?? ''}:${s.name}`).join('|')}`;
  const bar = pvBarHTML(state.bar) + PV_PICK.html(state);
  if (columns && was && el && was.el === el && was.frame === frame && !was.whole) {
    const cols = el.querySelector(':scope > .pv-cols');
    const found = state.rec.stages.map(stage => {
      const col = cols?.querySelector<HTMLElement>(`:scope > [data-pv-col="${CSS.escape(stage.id ?? '')}"]`);
      return {stage, head: col?.querySelector<HTMLElement>(':scope > .pv-col-head'), body: col?.querySelector<HTMLElement>(':scope > .pv-col-body')};
    });
    if (found.every(f => f.head && f.body && was.cols.has(f.stage.id ?? ''))) {
      if (bar !== was.bar) {
        for (const old of el.querySelectorAll(':scope > .pv-bar')) old.remove();
        if (bar) el.insertAdjacentHTML('afterbegin', bar);
        was.bar = bar;
      }
      for (const {stage, head, body} of found) {
        const kept = was.cols.get(stage.id ?? '')!, h = pvColHeadHTML(state, stage), b = pvColBodyHTML(state, stage);
        if (h !== kept.head) { head!.innerHTML = h; kept.head = h; }
        if (b !== kept.body) { body!.innerHTML = b; kept.body = b; }
      }
      state.whole = false;
      PV_PICK.paint(state, el);
      return;
    }
  }
  const parts = new Map<string, {head: string; body: string}>(), html = pvHTML(state, parts);
  // Nothing changed, so the picked marks still stand: a change to the picks changes the picking bar's count.
  if (!columns && was && el && was.el === el && was.whole === html) { state.whole = false; return; }
  state.whole = true;
  // Where the columns are scrolled, taken while they are still there; a phone's stage tabs keep theirs.
  if (state.root === root) pvScrollRead(state);
  const left = root.querySelector('.pv-tabs')?.scrollLeft || 0;
  root.innerHTML = html;
  const tabs = root.querySelector('.pv-tabs');
  if (tabs && left) tabs.scrollLeft = left;
  state.drawn = root.firstElementChild ? {el: root.firstElementChild, frame, bar, cols: parts, whole: columns ? '' : html} : null;
  PV_PICK.paint(state, root.firstElementChild);
}

/** The columns drawn in the view's root now; null in the list and on a phone. */
const pvColsOf = (state: PvState): HTMLElement | null => state.root?.querySelector<HTMLElement>(':scope > .pv > .pv-cols') ?? null;

/** Takes where the columns are scrolled now, before a draw replaces them (it catches a drag's own scrolling too). */
function pvScrollRead(state: PvState): void {
  // After another page and back the root is a detached element whose columns read 0: keep what was kept instead.
  const cols = state.root?.isConnected ? pvColsOf(state) : null;
  if (cols) { state.x = cols.scrollLeft; state.y = cols.scrollTop; }
}

/** Puts the columns back where they were after a draw. */
function pvScrollRestore(state: PvState): void {
  const cols = pvColsOf(state);
  if (!cols) return;
  if (cols.scrollLeft !== state.x) cols.scrollLeft = state.x;
  if (cols.scrollTop !== state.y) cols.scrollTop = state.y;
  pvEdges(cols);
}

/** The scroll cue of the columns, and of a phone's stage tabs: a fade on each side that has stages beyond it
 * (task-pipelines.css), none where they end. */
function pvEdges(cols: HTMLElement): void {
  const room = cols.scrollWidth - cols.clientWidth;
  // The room its scrollbars take, kept clear of the fade (0 where scrollbars overlay the content).
  cols.style.setProperty('--sb-v', `${Math.max(0, cols.offsetWidth - cols.clientWidth)}px`);
  cols.style.setProperty('--sb-h', `${Math.max(0, cols.offsetHeight - cols.clientHeight)}px`);
  cols.classList.toggle('fade-l', room > 1 && cols.scrollLeft > 1);
  cols.classList.toggle('fade-r', room > 1 && cols.scrollLeft < room - 1);
}

/** A phone's stage tabs drawn in the root, their fades set. */
function pvTabsEdges(root: HTMLElement): void {
  const tabs = root.querySelector<HTMLElement>('.pv-tabs');
  if (tabs) pvEdges(tabs);
}

/** Draws the view again where the page put it, keeping a why being typed and where the columns were scrolled. */
function pvDraw(state: PvState): void {
  const root = state.root;
  if (PV !== state || !root?.isConnected) return;
  pvScrollRead(state);
  const field = root.querySelector<HTMLInputElement>('[data-pv-push] input[name="why"]');
  const text = field?.value ?? '', typing = !!field && document.activeElement === field;
  pvPaint(state, root);
  pvScrollRestore(state);
  pvTabsEdges(root);
  const next = root.querySelector<HTMLInputElement>('[data-pv-push] input[name="why"]');
  if (next) { next.value = text; if (typing) next.focus(); }
  pipelineAskPaint();     // the face's menu and the chat's chip follow the tab
}

// ---------------------------------------------------------------- what a person does here

function pvClick(state: PvState, ev: MouseEvent): void {
  const target = eventElement(ev);
  if (!target) return;
  const rules = target.closest<HTMLElement>('[data-sr-open]');
  // the stage's rules mark: its editor, at its Rules (read only for whoever may not change the pipeline)
  if (rules) { void openPipelineEditor(state.rec.id, rules.dataset['srOpen'] ?? '', 'rules'); return; }
  const settings = target.closest<HTMLElement>('[data-pv-settings]');
  if (settings) { void openPipelineEditor(state.rec.id, settings.dataset['pvSettings'] ?? ''); return; }
  const colMenu = target.closest<HTMLElement>('[data-pv-col-menu]');
  if (colMenu) { pvColMenu(state, colMenu.dataset['pvColMenu'] ?? '', colMenu); return; }
  const play = target.closest<HTMLElement>('[data-pv-play]');
  if (play) { void decideStageOpen(play.dataset['pvPlay'] ?? ''); return; }
  const tab = target.closest<HTMLElement>('[data-pv-tab]');
  if (tab) { pvStageShow(state, tab.dataset['pvTab'] ?? ''); return; }
  const more = target.closest<HTMLElement>('[data-pv-more]');
  if (more) { state.open.add(more.dataset['pvMore'] ?? ''); pvDraw(state); return; }
  if (target.closest('[data-pv-bar-x]')) { state.bar = null; pvDraw(state); return; }
  const wake = target.closest<HTMLElement>('[data-pv-unsnooze]');
  if (wake) { ev.stopPropagation(); pvUnsnooze(state, wake.dataset['pvUnsnooze'] ?? ''); }
}

/** The moon on a snoozed task: it wakes now, and is drawn awake from the engine's answer. A refusal (a bot's own idea
    is a person's to unsnooze) says the engine's reason above the stages. */
function pvUnsnooze(state: PvState, id: string): void {
  const t = state.tasks.find(x => x.id === id);
  if (!t) return;
  state.bar = null;
  void (async () => {
    try {
      const woke = await taskUnsnooze(id);
      if (PV !== state) return;
      state.tasks = state.tasks.map(x => x.id === woke.task.id ? woke.task : x).filter(x => x.status === 'open');
    } catch (error) {
      if (PV !== state) return;
      const words = taskRefusedOf(error)?.detail || errorMessage(error, 'Not unsnoozed.');
      state.bar = {taskId: id, to: t.stage_id ?? '', push: false, text: `${t.title}: ${words}`};
    }
    pvDraw(state);
  })();
}

function pvStageShow(state: PvState, stage: string): void {
  if (!stage || stage === state.stage) return;
  state.stage = stage;
  pvStageKeep(state.rec.id, stage);
  pvDraw(state);
  state.root?.querySelector('.pv-tab.on')?.scrollIntoView({inline: 'center', block: 'nearest'});
}

/** A phone swipe steps to the next (1) or the previous (-1) stage. */
function pvStageStep(state: PvState, by: number): void {
  const ids = state.rec.stages.map(s => s.id ?? '');
  const to = ids[ids.indexOf(pvPhoneStage(state)) + by];
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
    drawn there at once; the page's live read shows where the engine put it. A refusal puts it back and says what the
    engine said: what is missing, with a why to push past it when it can be pushed past. */
function pvMove(state: PvState, t: TaskAny, stageId: string, why = ''): void {
  const stages = state.rec.stages;
  const from = stages.findIndex(s => s.id === t.stage_id), to = stages.findIndex(s => s.id === stageId);
  const target = stages[to];
  if (to < 0 || !target || from === to) return;
  const where = to === from + 1 ? 'next' : stageId;
  state.moving.set(t.id, stageId);
  state.bar = null;
  pvDraw(state);
  void (async () => {
    try {
      const moved = await taskMove(t.id, where, t.version, why || undefined);
      if (PV !== state) return;
      // The engine's answer is the task as it is now: draw it there until the page's next read.
      state.moving.delete(t.id);
      state.tasks = state.tasks.map(x => x.id === moved.task.id ? moved.task : x).filter(x => x.status === 'open');
      pvDraw(state);
    } catch (error) {
      if (PV !== state) return;
      state.moving.delete(t.id);
      state.bar = pvRefusedBar(t, stageId, target.name, error, !why);
      pvDraw(state);
    }
  })();
}

/** What the view says about a refused move: the engine's own words. A move that misses requirements a person may push
    past asks for the why ("Move to Review without Checks pass?"); anything else is the engine's reason. */
function pvRefusedBar(t: TaskAny, stageId: string, stageName: string, error: unknown, mayPush: boolean): PvBar {
  const refused = taskRefusedOf(error);
  const missing = refused?.missing ?? [];
  if (refused?.error === 'task_not_met' && mayPush && missing.length) {
    return {taskId: t.id, to: stageId, push: true, text: `Move to ${stageName} without ${missing.join(', ')}?`};
  }
  const words = refused ? [refused.detail, missing.length ? `Missing: ${missing.join(', ')}.` : ''].filter(Boolean).join(' ')
    : errorMessage(error, 'Not moved.');
  return {taskId: t.id, to: stageId, push: false, text: `${t.title}: ${words}`};
}

// ---------------------------------------------------------------- the page's hooks

/** The view's state for what the page gives it: the pipeline, its open tasks and how to show them. */
function pvTake(rec: PipelineRec, tasks: TaskAny[], opts: PvOpts): PvState {
  const state = pvStateFor(rec);
  state.whole = true;   // as `html` draws it; pvPaint says otherwise
  state.rec = rec;
  state.opts = {layout: opts.layout, phone: opts.phone, q: opts.q, filters: opts.filters};
  state.tasks = tasks.filter(t => t.pipeline_id === rec.id && t.status === 'open');
  // A move the read already shows is no longer drawn by hand.
  if (state.moving.size) {
    const stageOf = new Map(state.tasks.map(t => [t.id, t.stage_id]));
    for (const [id, to] of state.moving) if (stageOf.get(id) === to) state.moving.delete(id);
  }
  return state;
}

TASK_PAGE_HOOKS.pipeline = {
  html(rec, tasks, opts) {
    const state = pvTake(rec, tasks, opts);
    // The page is about to replace the columns: take where they are scrolled while they are still there.
    pvScrollRead(state);
    return pvHTML(state);
  },
  draw(root, rec, tasks, opts) { pvPaint(pvTake(rec, tasks, opts), root); },
  bind(root, rec) {
    const state = pvStateFor(rec);
    state.root = root;
    // The page calls bind after each draw. The browser clamps a restore to the columns drawn, so this holds only while
    // the first board draw has every stage (it does: columns are drawn from the pipeline, not as tasks arrive). A draw
    // of some columns kept the columns where they were.
    if (state.whole) { pvScrollRestore(state); pvTabsEdges(root); }
    if (PV_BOUND.has(root)) return;
    PV_BOUND.add(root);
    // Bound once per element; each event reads the view drawn there now.
    // Picking comes first: while selecting, or with shift, ⌘ or Ctrl, a click on a card picks it and opens nothing.
    root.addEventListener('click', ev => {
      const state = PV?.root === root ? PV : null, card = eventElement(ev)?.closest<HTMLElement>('[data-task]');
      if (!state || !card || eventElement(ev)?.closest('button, a, input')) return;
      if (!state.selecting && !ev.shiftKey && !ev.metaKey && !ev.ctrlKey) return;
      ev.preventDefault(); ev.stopPropagation();
      pvPick(state, card);
    }, {capture: true});
    root.addEventListener('click', ev => { if (PV?.root === root) pvClick(PV, ev); });
    // Scroll does not bubble: listen in the capture phase. Sideways is kept for the tab, once a frame.
    let saving = 0;
    root.addEventListener('scroll', ev => {
      if (ev.target instanceof HTMLElement && ev.target.classList.contains('pv-tabs')) { pvEdges(ev.target); return; }
      const cols = ev.target instanceof HTMLElement && ev.target.classList.contains('pv-cols') ? ev.target : null;
      const state = PV;
      if (!cols || !state || state.root !== root) return;
      state.x = cols.scrollLeft; state.y = cols.scrollTop;
      pvEdges(cols);
      if (saving) return;
      saving = requestAnimationFrame(() => { saving = 0; pvScrollKeep(state.rec.id, state.x); });
    }, {capture: true, passive: true});
    root.addEventListener('submit', ev => { if (PV?.root === root) pvSubmit(PV, ev); });
    // The window, or the page's chat docking beside the board, changes how much of the columns shows: the fades follow.
    new ResizeObserver(() => { const cols = PV?.root === root ? pvColsOf(PV) : null; if (cols) pvEdges(cols); pvTabsEdges(root); }).observe(root);
    taskBoardBind(root);
  },
  rail(rec, drawn) { pipelineRailSync(rec, drawn); },
  ask(host, rec, phone) { pipelineAskSync(host, rec, phone); },
  edit(rec) { void openPipelineEditor(rec.id); },
  select(rec) { void pvPick(pvStateFor(rec)); },
};
