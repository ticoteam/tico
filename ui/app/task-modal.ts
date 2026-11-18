/* ui/app/task-modal.ts — The task modal and its conversation (the task's session as a chat) (its files: ui/app/task-files.ts)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ---- the shapes this file reads: the task dialog (#task-modal) and what the page keeps on it, a task as the
// dialog draws it (a list row, or GET /v2/tasks/{id}'s task with its detail beside it), that detail, and its conversation.
interface ModalLink { kind: string; id?: string; url?: string; title?: string | null; state?: string | null; detail?: string | null; }
// What a session link's `detail` says (JSON): whose session it is, and the thread its resume command opens.
interface ModalSessionDetail { primary?: boolean; bot?: string; thread_id?: string; harness?: string; }
// A milestone the task waits for (backend/milestone_waits.py), and a deferred task's hand-over (backend/quiet_work.py).
interface ModalMilestone { kind: string; label: string; met?: boolean; url?: string | null; sentence: string; note?: string | null; }
interface ModalDeferred { state: string; resume?: unknown; run?: string | null; at?: string | null; words?: string | null; }
// The model a task's runs use (`run_model`), one row of GET /v2/models, and what a Runs on form has picked.
interface ModalRunModel { model?: string; harness?: string; reasoning_effort?: string; compact_at?: number | null; }
interface ModalModelRow { id: string; label?: string; harnesses?: string[]; efforts?: string[]; default_effort?: string; context_tokens?: number | null; deprecated?: boolean; meeting?: boolean; }
interface ModalTask extends TaskRow {
  links?: ModalLink[];
  children?: unknown[];
  run_model?: ModalRunModel | null;
  can_set_run_model?: boolean;
  milestone_waits?: ModalMilestone[] | null;
  deferred?: ModalDeferred | null;
  quiet?: ModalDeferred | null;
  goal_id?: string | null;
  conversation_id?: string;
  /** On its own read: its label, the letters it was made with and its number ("O561"); null without a number. */
  label?: string | null;
}
// The task dialog with what the page keeps on it: the opening a redraw belongs to, where Back returns, the draw and save
// counters, the rail, the page cards' slot (ui/app/pages.ts) and the live edit (ui/app/task-live.ts).
interface ModalDialog extends TfDialog {
  taskOpening?: {pending?: boolean};
  /** Closed by a change of page (taskModalRouteClose): a task the new page opens is not cancelled by this close. */
  routeClose?: boolean;
  /** The task's own address (#/task/<id>) when it opened by it; closing leaves that address. */
  address?: string;
  liveTask?: ModalTask | null;
  returnContext?: {route: string; task: string; opener: HTMLElement | SVGElement | null} | null;
  drawSeq?: number;
  saving?: number;
  saveQueue?: Promise<unknown>;
  taskRail?: TaskRailState;
  tpOff?: (() => void) | null;
  tpEl?: TaskPagesSlot | null;
  /** What shows: Chat, a document (`doc:<name>`) or Details; whether the person picked it (else the window picks once
   *  its conversation is read: taskTabFirst); the tab Details closes back to; and whether ⋯ › Activity and links is
   *  open; kept across redraws. */
  taskTab?: TaskTab;
  taskTabPicked?: boolean;
  taskTabBack?: TaskTab;
  taskActivity?: boolean;
  /** The task's documents as its last full read listed them: a redraw from a list row or a save keeps its tabs. */
  taskDocs?: TaskDoc[] | null;
  /** Where a page open in the window (`page:<id>`) came from: a Spec card ('chat') or the Files tab ('files'). */
  taskPageFrom?: TaskTab;
}
// GET /v2/tasks/{id} as the dialog and its conversation read it.
interface ModalDetail {
  task?: ModalTask | null;
  children?: unknown[];
  parent?: unknown;
  run_model?: ModalRunModel | null;
  can_set_run_model?: boolean;
  comments?: ModalComment[];
  messages?: ModalComment[];
  events?: ModalEvent[];
  runs?: ModalRun[];
  can_comment?: boolean;
  execution?: ModalExecution | null;
  waiting_for?: string | null;
  comment_delivery?: ModalDelivery | null;
  comment_to?: ModalCommentTo | null;
}
// The ways this reader may send a comment (`comment_delivery`), and who it reaches (`comment_to`).
interface ModalDelivery { default?: string | null; default_for?: string; steer?: boolean; queue?: boolean; defer?: boolean; goal?: boolean; }
interface ModalCommentTo { bot?: string; wakes?: boolean; mention?: string[]; }
// The run answering the thread (`execution`): the bot's chat's pending run, with whose it is (`bot_id`, the bot's id as
// backend/views.py conversation_execution sends it) and its steps so far.
interface ModalExecution extends ChatExecution { bot_id?: string; from_person?: boolean; parts?: {text: string}[]; }
// A change in the task's history, a comment, and one line of the conversation (either).
interface ModalEvent { id?: string; created_at: string; field: string; old?: string | null; new?: string | null; note?: string | null; actor?: string; via?: string;
  /** On a `number` line: the task's label ("O561"), laid on by taskCommentsRender. */
  label?: string | null; }
interface ModalReconnectFix { runtime?: string; runner?: string; machine?: string; }
interface ModalReconnect { fix?: ModalReconnectFix; status?: string; person?: string; service?: string; key?: string; }
type ModalRefs = NonNullable<TfMessage['refs']> & {via?: string; quiet?: boolean; turn_id?: string; reconnect?: ModalReconnect | null; credential_request?: string};
interface ModalRunCount { steps?: number; tool_calls?: number; took_s?: number | null; }
interface ModalComment extends TfMessage { kind: string; created_at: string; edited_at?: string | null; run?: ModalRunCount; refs: ModalRefs; }
type ModalLine = {kind: 'comment'; ts: string; message: ModalComment} | ({kind: 'event'; ts: string; message?: undefined} & ModalEvent)
  | {kind: 'run'; ts: string; message?: undefined; run: ModalRun};
// A bot's run on this task (`runs` in the task detail): its bot, when it started and finished.
interface ModalRun { run: string; bot?: string | null; started_at?: string | null; finished_at?: string | null; outcome?: string | null; }
// "worked 14 min": a finished run's length in words; under a minute is said so.
function taskRunLineWords(r: ModalRun): string {
  const s = Math.max(0, (Date.parse(r.finished_at || '') - Date.parse(r.started_at || '')) / 1000);
  if (!Number.isFinite(s)) return '';
  if (s < 60) return 'worked under a minute';
  const m = Math.round(s / 60);
  return m < 60 ? `worked ${m} min` : `worked ${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ''}`;
}
// What a reply's run steps hold: the steps read per turn (a working run's apart), which are open, which are being read.
interface ModalSteps { html: Map<string, string>; live: Map<string, string>; open: Set<string>; reading: Set<string>; }

// ---- the task modal: one item, in full, with what you can do to it
// The task's window (#task-modal, a modal dialog): its content and its clicks.
function taskDialogWire(d: ModalDialog) {
  tfWire(d);
  // A link in the task to one of its files opens that file's tile; any other file link opens the viewer (viewer.js).
  d.addEventListener('click', ev => {
    const anchor = eventElement(ev)?.closest<HTMLAnchorElement>('a[href]');
    if (!anchor || anchor.hasAttribute('download') || ev.defaultPrevented) return;
    const url = new URL(anchor.href, location.href);
    const match = url.origin === location.origin && url.pathname.match(/^\/api\/v2\/files\/([^/]+)$/);
    if (match && tfOpen(d, decodeURIComponent(match[1]!), url.searchParams.get('v'))) ev.preventDefault();
  });
  // A Spec card or a page row of Files opens the page in the window, in place of the chat (taskPageDocOf); a Spec card
  // that is open goes back to the chat. A click for a new tab or window still opens the page's own view.
  d.addEventListener('click', ev => {
    if (ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    const el = eventElement(ev)?.closest<HTMLElement>('[data-task-spec], [data-tft-page]');
    if (!el || el.closest('dialog') !== d) return;
    const key = 'page:' + (el.dataset.taskSpec || el.dataset.tftPage || '');
    if (!taskPageDocOf(d, key)) return;
    ev.preventDefault();
    const spec = el.hasAttribute('data-task-spec');
    if (spec && d.taskTab === key) { taskTabShow(d, 'chat'); return; }
    d.taskPageFrom = spec ? 'chat' : TASK_FILES_TAB;
    taskTabShow(d, key);
  });
  d.addEventListener('issue-acted', () => d.close());
  d.addEventListener('close', () => {
    // The close event is queued: a newer opening may still be awaiting its first draw.
    if (d.open || d.taskOpening?.pending) return;
    if (!TASK_CHAT || TASK_CHAT.dialog === d) taskChatStop();
    d.tfEl?.querySelector<HTMLMediaElement>('video, audio')?.pause();
    d.tfEl = null; d.tfOpen = null;
    d.tpOff?.(); d.tpOff = null; d.tpEl = null;
    taskLiveStop(d);
    d.taskOpening = {};
  });
}
function taskModal(): ModalDialog {
  let d: ModalDialog | null = $('#task-modal');
  if (d) return d;
  d = document.createElement('dialog'); d.id = 'task-modal'; d.className = 'tmodal';
  d.setAttribute('aria-label', 'Task');
  d.addEventListener('click', ev => { if (ev.target === d) d.close(); });
  taskDialogWire(d);
  d.addEventListener('close', () => {
    const routed = d.routeClose; d.routeClose = false;
    if (d.open || d.taskOpening?.pending) return;
    // A close by hand cancels an opening still on its way; a change of page cancels its own (taskModalOpen checks the
    // route), and the next task's address opening now is not undone by the old one's queued close.
    if (!routed) TASK_MODAL_LOAD++;
    document.body.classList.remove('task-modal-open');
    // A task opened by its address (#/task/<id>) leaves it when it closes, so the address opens it again.
    const address = d.address; d.address = '';
    if (!routed && TASKS_ST && address && location.hash === address) tasksURLWrite(TASKS_ST);
    const back = d.returnContext; d.returnContext = null;
    if (!back) return;
    if (history.state?.taskModal && location.hash === back.route && !TASK_MODAL_BACK) { TASK_MODAL_BACK = true; history.back(); }
    const opener = back.opener?.isConnected ? back.opener : $(`[data-task-detail="${CSS.escape(back.task)}"] [data-task-detail-open]`);
    if (!document.activeElement || document.activeElement === document.body || d.contains(document.activeElement)) opener?.focus({preventScroll: true});
  });
  document.body.appendChild(d);
  return d;
}
// The router's close of the task window when the page changes (ui/app/router.ts).
function taskModalRouteClose() {
  const d: ModalDialog | null = $('#task-modal');
  if (!d?.open) return;
  d.routeClose = true;
  d.close();
}
let TASK_MODAL_BACK = false;
let TASK_MODAL_LOAD = 0;
window.addEventListener('popstate', () => {
  TASK_MODAL_BACK = false;
  const d = $('#task-modal');
  if (d?.open && d.returnContext && !history.state?.taskModal) d.close();
});
// A task that could not be read says so: "Task not found" for a 404 or 403, anything else (a 500, a network error) with
// Retry, which opens the task by its id again.
// A task opened from inside the open window (a related or sub-task row) says so on that window: the page behind a modal
// takes no clicks, so Retry there could not be pressed.
function taskModalLoadFailed(id: string, error: unknown, d?: ModalDialog) {
  const status = errorStatus(error), opts = d?.open ? {host: d} : {};
  if (status === 404 || status === 403) { toast('Task not found', true, null, opts); return; }
  toast(errorMessage(error, 'The task did not load'), true, {label: 'Retry', run: () => taskModalOpen(id, d)}, opts);
}
// Opens a task by its id in the task's window.
// `opts` as taskModalShow takes them: a pipeline's board on a phone opens a task at its conversation, asking its owner;
// `direct`: the task page itself, even when the task waits on the person's answer (Open task ↗ on the ask screen).
async function taskModalOpen(id: string, d?: ModalDialog, opts: {thread?: boolean; ask?: boolean; direct?: boolean} = {}) {
  const from = S.route, load = ++TASK_MODAL_LOAD, after = LIVE.after;
  let detail: ModalDetail | null;
  try { detail = await get<ModalDetail>(`/v2/tasks/${encodeURIComponent(id)}`); }
  catch (error) {
    if (S.route === from && load === TASK_MODAL_LOAD) taskModalLoadFailed(id, error, d);
    return;
  }
  if (S.route !== from || load !== TASK_MODAL_LOAD || !detail?.task) return;
  void taskModalShow(detail.task, d, {...opts, detail, after});
}
// The runs section drawn for the open task, held across a redraw of that task (taskModalShow).
const taskModalRuns = new WeakMap<ModalDialog, HTMLElement>();
function taskModalRunsKeep(d: ModalDialog, keep: TaskChatState | null) {
  const runs = keep && $('[data-task-runs]', d);
  if (runs) { runs.dataset.task = String(d.dataset.task); taskModalRuns.set(d, runs); } else taskModalRuns.delete(d);
}
// The one way a task opens in full: from a list, from a link, from a chat card.
// `opts.thread`: just the task's own conversation, as a bot's chat opens it (taskThreadShow); Details shows the rest.
// `opts.ask`: its box asks the task's owner ("Ask Product Owner about this task…"), as a pipeline's board on a phone opens it.
// `opts.after`: the live change number from before `opts.detail` was read (taskLiveStart resumes the task's stream there).
async function taskModalShow(task: ModalTask, d: ModalDialog = taskModal(), opts: {thread?: boolean; ask?: boolean; detail?: ModalDetail; direct?: boolean; after?: number | null} = {}) {
  // A task opened (not redrawn) while it waits on the person's own answer opens on that ask, from wherever it was opened
  // (the board, a list, a #/task/ link, a link in chat, a bot's page); its Open task ↗ comes back here with `direct`. The
  // task's conversation asked for by name (opts.thread, as a bot's chat opens it) opens as it is; a phone board's card
  // (thread with ask) is the board, so it opens on the ask too.
  if (!d?.open && !opts.direct && (!opts.thread || opts.ask)) {
    const load = ++TASK_MODAL_LOAD;
    if (await decideTaskOpen(task)) return;
    if (load !== TASK_MODAL_LOAD) return;      // another task was opened meanwhile
  }
  TASK_MODAL_LOAD++;
  if (!d) d = taskModal();
  const sameTask = d.open && String(d.dataset.task) === String(task.id);
  // A fresh open starts with no file open, whether or not the last close's event has run yet.
  if (!d.open || String(d.dataset.task) !== String(task.id)) {
    taskLiveStop(d);
    d.tpOff?.(); d.tpOff = null; d.tpEl = null;
    d.taskOpening = {pending: !d.open}; d.tfEl = null; d.tfOpen = null;
    d.classList.toggle('thread', !!opts.thread);     // a redraw of the same task keeps the view it had
    // A task opens on its chat (or, once read, on a document waiting on the reader), Activity and links shut.
    d.taskTab = 'chat'; d.taskTabBack = 'chat'; d.taskTabPicked = !!opts.thread; d.taskActivity = false; d.taskDocs = null;
    d.dataset.ask = opts.ask ? '1' : '';
  }
  const opening = d.taskOpening, scroll = opts.detail && d.open ? d.scrollTop : 0;
  // A redraw of the open task (a property saved, the task changed) keeps its conversation and its runs as they are, as
  // the files keep theirs (tfAdopt): the thread stays on screen, scrolled where it was, with what is typed in its box,
  // instead of "Loading the conversation…" and a fresh read every time something on the task is saved.
  const kept = d.open && String(d.dataset.task) === String(task.id) && TASK_CHAT?.dialog === d && d.contains(TASK_CHAT.host) ? TASK_CHAT : null;
  const adopt = () => {
    const host = kept && TASK_CHAT === kept ? kept.host : null, runs = taskModalRuns.get(d);
    if (host && !d.contains(host)) $('.task-chat', d)?.replaceWith(host);
    if (runs && runs.dataset.task === String(task.id) && !d.contains(runs)) $('[data-task-runs]', d)?.replaceWith(runs);
  };
  if (!kept) taskChatStop();
  // A menu open on this task (a property picked while a save was still on its way) is never pulled away: the redraw
  // waits for it to close, then draws the newest copy.
  if (d.open && String(d.dataset.task) === String(task.id) && taskMenuOpen(d)) {
    await taskMenuSettled(d);
    if (!d.open || String(d.dataset.task) !== String(task.id) || d.taskOpening !== opening) return;
  }
  if (d.dataset.task !== String(task.id)) { d.propsErrs = null; d.propsAnyway = null; d.focusProp = ''; d.liveTask = null; }
  // never draw an older copy of the task over the newer one a save returned
  else if (d.liveTask && Number(task.version) < Number(d.liveTask.version)) task = d.liveTask;
  const seq = d.drawSeq = (d.drawSeq || 0) + 1;
  d.dataset.task = task.id;
  // a property that has the focus now keeps it through the redraw (over the one a save asked for)
  const onProp = d.contains(document.activeElement) && document.activeElement instanceof HTMLElement ? document.activeElement.dataset.prop : '';
  if (onProp) d.focusProp = onProp;
  // The detail is asked for at once; the first draw waits only for the CLI's manifest (session copy commands need it
  // even when Tasks is the first page opened), read once.
  // A read that fails is kept, so the window that closes for it says why (taskModalLoadFailed).
  let readFailed: unknown = null;
  const after = opts.detail ? opts.after ?? null : LIVE.after;
  const reading = Promise.all([opts.detail || get(`/v2/tasks/${encodeURIComponent(task.id)}`).catch((error: unknown) => { readFailed = error; return null; })]);
  // A previous close's Back navigation must settle before a new dialog takes its place in history.
  if (TASK_MODAL_BACK) await new Promise(resolve => window.addEventListener('popstate', resolve, {once: true}));
  await loadHarnessRows();
  if (d.drawSeq !== seq || d.taskOpening !== opening) return;
  if (!opening) return;
  opening.pending = false;
  taskModalRunsKeep(d, kept);
  taskLiveFocusKeep(d);
  const keptForm = sameTask ? taskFormCapture(d) : {fields: [], folds: []};
  d.innerHTML = hubModalHTML(task, {thread: d.classList.contains('thread'), docs: taskDocsOf(d, task), tab: d.taskTab});
  adopt(); taskFormAdopt(d, keptForm);
  taskModalBind(d, task);
  taskPropFocus(d); taskLiveFocusRestore(d); taskFormRestore(d, keptForm);
  if (!d.open) {
    const was = document.activeElement as HTMLElement | SVGElement | null;
    const address = '#/task/' + encodeURIComponent(String(task.id));
    d.address = location.hash === address ? address : '';
    if (d.address) tasksBehindTask(task);
    if (!TASKS_ST) {
      // Outside Tasks, Back dismisses the detail without rebuilding the bot's chat, tab or scroll position.
      d.returnContext = {route: location.hash, task: String(task.id), opener: was};
      history.pushState({...(history.state || {}), taskModal: 1}, '');
    }
    d.showModal();
    // Opening lands on the window itself (Back in the thread view): not the title, whose focus would open it for editing
    // and show all of a long one, nor a control, which would wear a focus ring before the keyboard moves. Tab goes on.
    if (d.open && d.matches(':modal')) {
      if (d.classList.contains('thread')) $('[data-thread-back]', d)?.focus(TASK_QUIET_FOCUS);
      else { d.tabIndex = -1; d.focus({preventScroll: true}); }
    }
  }
  document.body.classList.add('task-modal-open');
  d.scrollTop = scroll;
  // the list knows the task; the detail adds its parts, its parent and the comments
  const [detail] = await reading;
  if (!d.open || String(d.dataset.task) !== String(task.id) || d.drawSeq !== seq) return;
  if (!detail?.task) {
    d.close(); d.innerHTML = ''; TASK_DRAFTS.delete(String(task.id));
    if (readFailed) taskModalLoadFailed(String(task.id), readFailed, d);
    return;
  }
  await taskMenuSettled(d);                 // never pull a menu out from under the pointer
  if (!d.open || String(d.dataset.task) !== String(task.id) || d.drawSeq !== seq) return;
  if (d.liveTask && Number(detail.task.version) < Number(d.liveTask.version)) return;   // a save landed since this was asked for
  // The task's own model sits beside the task in the detail (GET /v2/tasks/{id} `run_model`), as its children do, with
  // whether this reader may set it (`can_set_run_model`).
  const full = {...detail.task, children: detail.children || [], parent: detail.parent || null, ...('run_model' in detail
    ? {run_model: detail.run_model, can_set_run_model: detail.can_set_run_model} : {})};
  // the redraw keeps the focus where it was (a header button, the title, a property)
  const a = d.contains(document.activeElement) ? document.activeElement : null;
  const keep = !a ? '' : a.matches('[data-modal-close]') ? '[data-modal-close]' : a.matches('[data-task-more]') ? '[data-task-more]' : a.matches('.tmodal-title') ? '.tmodal-title'
    : a.matches('[data-thread-back]') ? '[data-thread-back]' : a instanceof HTMLElement && a.dataset.taskTab ? `[data-task-tab="${CSS.escape(a.dataset.taskTab)}"]`
    : a instanceof HTMLElement && a.dataset.prop ? `[data-prop="${CSS.escape(a.dataset.prop)}"]` : '';
  const quiet = !!a && (a as HTMLElement).dataset?.quietFocus !== undefined;
  taskModalRunsKeep(d, kept);
  taskLiveFocusKeep(d);
  const fullForm = taskFormCapture(d);
  d.innerHTML = hubModalHTML(full, {thread: d.classList.contains('thread'), docs: taskDocsOf(d, full), tab: d.taskTab});
  adopt(); taskFormAdopt(d, fullForm);
  taskModalBind(d, full, true);
  if (keep && quiet) taskQuietFocus($(keep, d) as HTMLElement | null);
  else if (keep) $(keep, d)?.focus({preventScroll: true});
  taskPropFocus(d); d.focusProp = ''; taskLiveFocusRestore(d); taskFormRestore(d, fullForm);
  taskTabFirst(d, detail); taskLiveStart(d, after); d.scrollTop = scroll;
  void taskRailLoad(d, full, detail);
  if (kept && TASK_CHAT === kept && d.contains(kept.host)) taskCommentsRender(kept, detail);
  else void taskChatLoad(task.id, d, detail);
  void tfLoad(d, task.id);
  void taskFilesTabLoad(d, task.id);
  void taskRunsLoad(d, task.id);
  void githubNudgeTask(d, full);
}
// A task's own thread: the full modal showing only its header and its conversation, so what is typed there is plainly
// said in the task (POST /v2/tasks/{id}/comments), never in the bot's main chat. Back returns to the chat.
function taskThreadShow(task: ModalTask) { return taskModalShow(task, taskModal(), {thread: true}); }
async function taskThreadOpen(id: string) {
  try {
    const data = await get<ModalDetail>('/v2/tasks/' + encodeURIComponent(id));
    if (data?.task) await taskThreadShow(data.task);
  } catch (error) { toast(errorMessage(error, ''), true); }
}
// After a property saves, the redrawn row gets the focus back.
function taskPropFocus(d: ModalDialog) {
  if (d.focusProp) $(`[data-prop="${CSS.escape(d.focusProp)}"]`, d)?.focus({preventScroll: true});
}
// `full`: the task as GET /v2/tasks/{id} answers it (list rows leave out what the rail's Code section needs).
function taskModalBind(d: ModalDialog, task: ModalTask, full = false) {
  fbSync();                     // the task's own 👍 / 👎 in the header, when a bot asked for it
  tfAdopt(d, task);
  taskMeetingsAdopt(d, task);
  taskPagesAdopt(d, task);      // the task's pages, as cards at its top (ui/app/pages.ts)
  ruleHoldsAdopt(d, task);      // a rule review's held outputs and their actions (ui/app/org-rule-holds.ts)
  taskLiveBind(d, task);
  d.dataset.version = String(task.version ?? '');
  if (!d.liveTask || String(d.liveTask.id) !== String(task.id) || Number(task.version) >= Number(d.liveTask.version)) d.liveTask = task;
  if (d.taskRail?.id !== String(task.id)) d.taskRail = {id: String(task.id), open: new Set()};
  if (full) d.taskRail.full = task;
  taskRailPaint(d, task); taskRailBind(d, task);
  taskTabsBind(d);
  const dismiss = () => d.close();
  $('[data-modal-close]', d).onclick = dismiss;
  const back = $('[data-thread-back]', d), details = $('[data-thread-details]', d);
  if (back) back.onclick = dismiss;
  if (back && d.dataset.ask) { back.setAttribute('aria-label', 'Back to the board'); back.title = 'Back to the board'; }
  // Details leaves the thread view for the whole task, on its Details tab.
  if (details) details.onclick = () => { d.classList.remove('thread'); d.taskTabBack = 'chat'; d.taskTab = 'details'; d.taskTabPicked = true; void taskModalShow(d.liveTask || task, d); };
  // Each change goes through the dialog's queue (taskSave), one at a time, each with the latest version.
  const change = (body: PropBody, then?: boolean, field?: string) => taskSaveQueued(d, String(task.id), body, then, field);
  // Done and Archive finish with the task: the window closes and the page behind it is where the person was.
  d.querySelectorAll<HTMLButtonElement>('[data-task-finish]').forEach(b => b.onclick = async () => {
    b.disabled = true;
    const saved = await change(b.dataset.taskFinish === 'close' ? {close: true} : {done: true}, false, 'stage');
    if (saved && d.open && String(d.dataset.task) === String(task.id)) dismiss();
    else b.disabled = false;
  });
  // A finished task's way back is where Archive was: it asks why (docs/dev/pipelines-v3.md C24), and Enter reopens it.
  const reopen = $('[data-task-reopen]', d);
  if (reopen) reopen.onclick = () => propMenu(d, reopen, {label: 'Reopen', entry: 'Why reopen it?', none: '',
    onEntry: v => void change({move: 'reopen', why: v}, undefined, 'stage')});
  const unsnooze = $('[data-task-unsnooze]', d);
  if (unsnooze) unsnooze.onclick = () => change({unsnooze: true}, undefined, 'snooze');
  taskRequirementsBind(d, task, fresh => { if (d.open && String(d.dataset.task) === String(task.id)) { d.liveTask = fresh; void taskModalShow(fresh, d); } });
  taskPropsBind(d, task, change);
  roadmapTaskBind(d, task, change);
  d.querySelectorAll<HTMLElement>('[data-drop-label]').forEach(b => b.onclick = () => change(cur => ({labels: (cur.labels || []).filter(l => l !== b.dataset.dropLabel)}), undefined, 'tags'));
  const opening = d.taskOpening;
  const linkCurrent = () => d.open && String(d.dataset.task) === String(task.id) && d.taskOpening === opening;
  const link = $('[data-modal-link]', d);
  const addLink = $('[data-link-add]', d);
  if (addLink && link) {
    addLink.onclick = () => { link.hidden = !link.hidden; if (!link.hidden) link.querySelector('input').focus(); };
    link.querySelector('input').onkeydown = (ev: KeyboardEvent) => { if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); link.hidden = true; addLink.focus(); } };
  }
  if (link) link.onsubmit = async (ev: SubmitEvent) => {
    ev.preventDefault();
    const url = link.querySelector('input').value.trim(); if (!url) return;
    try {
      await post(`/v2/tasks/${encodeURIComponent(task.id)}/links`, {url});
      const data = await get(`/v2/tasks/${encodeURIComponent(task.id)}`);
      if (TASKS_ST) void tasksCatchUp(TASKS_ST);
      if (linkCurrent() && data.task) taskModalShow(data.task, d);
    } catch (e) { toast(errorMessage(e, ''), true); }
  };
  // Sessions: + opens one input for a URL or a resume command, sent as a session link.
  const session = $('[data-modal-session]', d);
  const addSession = $('[data-session-add]', d);
  if (addSession && session) {
    addSession.onclick = () => { session.hidden = !session.hidden; if (!session.hidden) session.querySelector('input').focus(); };
    session.querySelector('input').onkeydown = (ev: KeyboardEvent) => { if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); session.hidden = true; addSession.focus(); } };
    session.onsubmit = async (ev: SubmitEvent) => {
      ev.preventDefault();
      const url = session.querySelector('input').value.trim(); if (!url) return;
      try {
        await post(`/v2/tasks/${encodeURIComponent(task.id)}/links`, {url, kind: 'session'});
        const data = await get(`/v2/tasks/${encodeURIComponent(task.id)}`);
        if (linkCurrent() && data.task) taskModalShow(data.task, d);
      } catch (e) { toast(errorMessage(e, ''), true); }
    };
  }
  taskRunModelBind(d, task);
  d.querySelectorAll<HTMLElement>('[data-copy-session]').forEach(b => b.onclick = () =>
    void copyText(b.dataset.copySession!).then(() => toast('Copied'), () => toast('Could not copy', true)));
  d.querySelectorAll<HTMLElement>('[data-drop-link]').forEach(b => b.onclick = async () => {
    // A session is asked about once: its × sits beside Copy, and a removed bot session cannot be added back as it was.
    if (b.closest('.task-sessions') && !(await askConfirm('Remove this session from the task?', b))) return;
    try {
      await post(`/v2/tasks/${encodeURIComponent(task.id)}/links`, {remove: b.dataset.dropLink});
      const data = await get(`/v2/tasks/${encodeURIComponent(task.id)}`);
      if (linkCurrent() && data.task) taskModalShow(data.task, d);
    } catch (e) { toast(errorMessage(e, ''), true); }
  });
  // Related tasks, by kind: + asks which kind, then finds the task; each one opens, × takes it off (from both tasks).
  // The same finder picks the task a blocked task depends on: "Add another" under Blocked by opens it at once.
  const findTask = async (anchor: HTMLElement, kind: string, text: string) => {
    const have = new Set(Object.values(task.relations || {}).flat().map(r => String(r.id)));
    const pick = (rows: PropCandidate[]) => rows.filter(x => !have.has(String(x.id))).map(x => ({value: x.id, text: `${x.title} ${actorLabel(x.owner_id)}`,
      html: `${taskStatusText(x)}<span class="tl-mi-t">${esc(clipLine(x.title, 70))}</span><span class="tl-mi-sub">${esc(actorLabel(x.owner_id))}</span>`}));
    const first = await taskPropCandidates(task);
    if (!anchor.isConnected) return;
    propMenu(d, anchor, {label: text, find: 'Find a task', none: 'No open tasks', items: pick(first.rows),
      ...(first.more ? {search: async (q: string) => pick((await taskPropCandidates(task, q)).rows)} : {}),
      onPick: it => taskRelate(d, task, {task: it.value, kind})});
  };
  const addRelated = $('[data-related-add]', d);
  if (addRelated) addRelated.onclick = () => propMenu(d, addRelated, {label: 'Relation', items: TASK_REL_ADD.map(([kind, text]) => ({value: kind, text, html: `<span>${esc(text)}</span>`})),
    onPick: kind => setTimeout(() => void findTask(addRelated, kind.value, kind.text), 0)});
  const addBlocker = $('[data-depends-add]', d);
  if (addBlocker) addBlocker.onclick = () => void findTask(addBlocker, 'depends_on', 'Blocked by');
  d.querySelectorAll<HTMLElement>('[data-drop-rel]').forEach(b => b.onclick = () => {
    const [kind, direction, other] = b.dataset.dropRel!.split(' ');
    // A row this task is the far end of is taken off from the other task's side.
    if (direction === 'in') void taskRelate(d, task, {task: String(task.id), kind, remove: true}, other);
    else void taskRelate(d, task, {task: other, kind, remove: true});
  });
  d.querySelectorAll<HTMLElement>('[data-open-task]').forEach(b => b.onclick = ev => { ev.preventDefault(); void taskModalOpen(b.dataset.openTask!, d); });
}
// The kinds the Related section adds, and the groups it shows: [kind, direction, label].
// Sessions working the task: the owning bot's (primary; Tico records it) first, then ones people linked to
// track. Tracking only: task chat goes to the owning bot, never to a linked session.
const SESSION_COPY = '<svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true" focusable="false"><rect x="4" y="4" width="6.5" height="6.5" rx="1" fill="none" stroke="currentColor" stroke-width="1.2"/><path d="M8 4V2.5a1 1 0 0 0-1-1H2.5a1 1 0 0 0-1 1V7a1 1 0 0 0 1 1H4" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>';
function taskSessions(t: ModalTask) {
  return (t.links || []).filter(l => l.kind === 'session').map(l => {
    let d: ModalSessionDetail = {}; try { d = JSON.parse(l.detail || '{}'); } catch {}
    return {...l, d, value: String(l.url || '').replace(/^session:/, '')};
  }).sort((a, b) => (b.d.primary ? 1 : 0) - (a.d.primary ? 1 : 0));
}
function taskSessionsHTML(t: ModalTask) {
  // Copy and Remove stay small marks in the chip; on a phone each has a 44px tap area round it (.tap-mark, task-modal.css).
  const copy = (value: string, what: string) => `<button type="button" class="tcopy tap-mark" data-copy-session="${esc(value)}" aria-label="Copy ${esc(what)}" title="Copy ${esc(what)}">${SESSION_COPY}</button>`;
  const drop = myActor() ? (l: ModalLink) => `<button type="button" class="x tap-mark" data-drop-link="${esc(l.id)}" aria-label="Remove session">×</button>` : () => '';
  const rows = taskSessions(t).map(l => {
    // A bot's session (the primary, or an earlier owner's) copies its full resume command, folder included.
    if (l.d.bot) {
      const who = 'bot:' + l.d.bot;
      const cmd = l.d.thread_id ? botResumeCommand(l.d.harness, l.d.thread_id, l.d.bot) : '';
      return `<span class="tlink session${l.d.primary ? ' primary" title="Primary' : ''}">${actorFace(who, 14)}<span class="tsess-name">${esc(actorLabel(who))}${l.d.harness ? ` · ${esc(l.d.harness)}` : ''}</span>${cmd ? copy(cmd, 'resume command') : ''}${l.d.primary ? '' : drop(l)}</span>`;
    }
    const label = esc(l.title || l.value);
    return /^https?:\/\//.test(String(l.url))
      ? `<span class="tlink session"><a class="tsess-name" href="${esc(l.url)}" target="_blank" rel="noopener">${label}</a>${drop(l)}</span>`
      : `<span class="tlink session"><span class="tsess-name" title="${esc(l.value)}">${label}</span>${copy(l.value, 'session')}${drop(l)}</span>`;
  });
  return `<section class="tsec task-sessions${rows.length ? '' : ' tsec-empty'}"><h3 class="rail-h">Sessions<button type="button" class="prop-add" data-session-add aria-label="Add a session" title="Add a session">${TL_ICON.plus}</button></h3>
    ${rows.length ? `<div class="tlinks">${rows.join('')}</div>` : ''}
    <form class="inline task-link-add" data-modal-session hidden><input type="text" autocomplete="off" spellcheck="false" placeholder="URL or resume command" aria-label="Add a session"></form></section>`;
}

function taskRunCatalog() {
  TASK_RUN_LOADING ||= get<{models?: ModalModelRow[]}>('/v2/models').then(d => {
    TASK_RUN_ROWS = (d.models || []).filter(m => !m.deprecated && !m.meeting && !/^tico-/.test(m.id || ''));
    return TASK_RUN_ROWS;
  }, () => { TASK_RUN_LOADING = null; return []; });
  return TASK_RUN_LOADING;
}
// Only whoever the server says may set the task's model may pick (`can_set_run_model`, the answer POST
// /v2/tasks/{id}/model gives); anyone else reads what it runs on.
const taskRunMay = (t: ModalTask) => t.can_set_run_model === true;
// Runs on, one line among the facts (shown on a bot's task once GET /v2/tasks/{id} has answered; list rows leave out
// run_model): the shared model picker (settings-bots-choice.ts), "Same as bot" first; closed it reads "Same as bot" or
// "GPT 6 Sol HIGH 400K".
function taskRunModelRowHTML(t: ModalTask) {
  if (!String(t.owner_id || '').startsWith('bot:') || t.run_model === undefined) return '';
  return `<div class="prop" data-prop-row="runs-on" data-task-run-model><span class="prop-k" aria-hidden="true">Runs on</span><span class="prop-vwrap">
    <span class="task-run-pick" data-run-picker><span class="muted">Loading the models…</span></span></span></div>`;
}
// Apply saves the pick at once: a model with its harness, effort and window; Same as bot with a window alone keeps the
// bot's model ("Same as bot 150K"); Same as bot with none clears the task's own choice.
function taskRunModelBind(d: ModalDialog, task: ModalTask) {
  const host = $('[data-task-run-model] [data-run-picker]', d);
  if (!host) return;
  const id = String(task.id), run = task.run_model || null;
  void settingsRunPickerMount(host, {bot: String(task.owner_id), name: 'Runs on', readonly: !taskRunMay(task),
    run: run ? {model: run.model || '', harness: run.harness || '', effort: run.reasoning_effort || '', compact_at: run.compact_at || null} : null,
    onPick: async pick => {
      const body = pick.model ? {model: pick.model, harness: pick.harness, effort: pick.effort, compact_at: pick.compact_at} : {model: null, compact_at: pick.compact_at};
      try {
        const saved = await post(`/v2/tasks/${encodeURIComponent(id)}/model`, body);
        if (saved.warning) toast(saved.warning);
        const data = await get(`/v2/tasks/${encodeURIComponent(id)}`);
        if (TASKS_ST) void tasksCatchUp(TASKS_ST);
        if (d.open && String(d.dataset.task) === id && data.task) void taskModalShow(data.task, d);
      } catch (e) { toast(errorMessage(e, ''), true); }
    }});
}
// The relations backend/task_relations.py relate writes (a parent is fixed when the task is filed).
const TASK_REL_ADD: [string, string][] = [['related', 'Related'], ['duplicate_of', 'Duplicate of'], ['follow_up', 'Follow-up of'],
  ['depends_on', 'Blocked by']];
const TASK_REL_GROUPS: [string, string, string][] = [['related', 'both', 'Related'], ['duplicate_of', 'out', 'Duplicate of'],
  ['duplicate_of', 'in', 'Duplicated by'], ['follow_up', 'out', 'Follow-up of'], ['follow_up', 'in', 'Follow-ups']];
// One relation added or taken off (POST /v2/tasks/{id}/relations), then the task redrawn. `on` is the task whose
// endpoint takes it, when that is the other end.
async function taskRelate(d: ModalDialog, task: ModalTask, body: PropPatch, on = task.id) {
  try {
    await post(`/v2/tasks/${encodeURIComponent(on)}/relations`, body);
    const data = await get(`/v2/tasks/${encodeURIComponent(task.id)}`);
    if (TASKS_ST) void tasksCatchUp(TASKS_ST);
    if (d.open && String(d.dataset.task) === String(task.id) && data.task) taskModalShow(data.task, d);
  } catch (e) { toast(errorMessage(e, ''), true); }
}
// A snooze's or a wait's note after the chip's own text, clipped by CSS; the chip's title carries it in full.
function chipNoteHTML(note: string | null | undefined) {
  return note ? ` · <span class="tchip-note">${esc(note)}</span>` : '';
}
// A milestone the task waits for besides tasks (backend/milestone_waits.py): "PR #943 to merge", then "merged".
function taskMilestoneHTML(t: ModalTask) {
  const waits = Array.isArray(t.milestone_waits) ? t.milestone_waits : [];
  if (!waits.length) return '';
  const done = (w: ModalMilestone) => w.kind === 'pr_merged' ? 'merged' : 'published';
  return `<div class="trel task-milestones"><span class="trel-k">Waits for</span><div class="tlinks">${waits.map(w => {
    const text = `${w.label} ${w.met ? done(w) : w.kind === 'pr_merged' ? 'to merge' : 'to be published'}`;
    return `<span class="tlink milestone${w.met ? ' met' : ''}" title="${esc(w.sentence + (w.note ? ` · ${w.note}` : ''))}">${w.url
      ? `<a href="${esc(w.url)}" target="_blank" rel="noopener">${esc(text)}</a>` : esc(text)}${chipNoteHTML(w.note)}</span>`;
  }).join('')}</div></div>`;
}
// The order to merge in (backend/merge_order.py): "After #40 · Merge" on the later task, "Before #42 · Build" on the
// earlier one. Read-only lines: an order is set and taken off with `tico task after`, not here.
function taskOrderHTML(t: ModalTask) {
  return ([['out', 'After'], ['in', 'Before']] as const).map(([dir, label]) => {
    const rows = taskRels(t, 'after', dir);
    if (!rows.length) return '';
    return `<div class="trel task-order"><span class="trel-k">${label}</span><div class="tlinks">${rows.map(k => {
      const ref = k.number ? `#${k.number}` : '';
      const where = k.status === 'open' ? k.stage || '' : k.status;
      return `<span class="tlink task"><button type="button" class="linkish" data-open-task="${esc(k.id)}" aria-label="Open ${esc(k.title)}">${esc(ref)} ${esc(clipLine(k.title, 60))}</button>${where ? ` · <span class="tchip-note">${esc(where)}</span>` : ''}</span>`;
    }).join('')}</div></div>`;
  }).join('');
}
// A dependency (backend/task_depends.py), from both ends. Blocked by: each task it waits for, "#304 Growth synced sources
// part 1 · Build · since Nov 13", with × to take it off, and Add another (the same task finder as +). Blocks: the tasks
// waiting for this one, under "Blocks 4 tasks". A dependency comes off when its task ends, so these are open tasks.
function taskDependsHTML(t: ModalTask) {
  const row = (k: TaskRelRow, dir: string, label: string) => {
    const ref = k.number ? `#${k.number}` : '';
    const where = [k.status === 'open' ? k.stage || '' : k.status, k.since ? `since ${lowerDay(shortDate(k.since))}` : ''].filter(Boolean).join(' · ');
    return `<span class="tlink task"><button type="button" class="linkish" data-open-task="${esc(k.id)}" aria-label="Open ${esc(k.title)}">${esc(ref)} ${esc(clipLine(k.title, 60))}</button>`
      + `${where ? ` · <span class="tchip-note">${esc(where)}</span>` : ''}`
      + `<button type="button" class="x tap-mark" data-drop-rel="depends_on ${dir} ${esc(k.id)}" aria-label="Remove ${esc(label)}: ${esc(k.title)}">×</button></span>`;
  };
  const on = taskRels(t, 'depends_on', 'out'), of = taskRels(t, 'depends_on', 'in');
  const blocked = on.length ? `<div class="trel task-depends"><span class="trel-k">Blocked by</span><div class="tlinks">${on.map(k => row(k, 'out', 'blocked by')).join('')}`
    + `<button type="button" class="linkish task-depends-add" data-depends-add aria-haspopup="menu">Add another</button></div></div>` : '';
  const blocks = of.length ? `<div class="trel task-blocks"><span class="trel-k">${esc(`Blocks ${of.length} task${of.length === 1 ? '' : 's'}`)}</span><div class="tlinks">${of.map(k => row(k, 'in', 'blocks')).join('')}</div></div>` : '';
  return blocked + blocks;
}
function taskRelHTML(t: ModalTask) {
  const groups = TASK_REL_GROUPS.map(([kind, dir, label]): [string, string, string, TaskRelRow[]] => [kind, dir, label, taskRels(t, kind, dir)]).filter(g => g[3].length);
  const milestones = taskDependsHTML(t) + taskMilestoneHTML(t) + taskOrderHTML(t);
  return `<section class="tsec task-related${groups.length || milestones ? '' : ' tsec-empty'}"><h3 class="rail-h">Related<button type="button" class="prop-add" data-related-add aria-haspopup="menu" aria-label="Relate a task" title="Relate a task">${TL_ICON.plus}</button></h3>
    ${groups.map(([kind, dir, label, rows]) => `<div class="trel"><span class="trel-k">${esc(label)}</span><div class="tlinks">${rows.map(k => `<span class="tlink task"><button type="button" class="linkish" data-open-task="${esc(k.id)}" aria-label="Open ${esc(k.title)}">${taskStatusText(k)} ${esc(clipLine(k.title, 70))}</button><button type="button" class="x tap-mark" data-drop-rel="${esc(kind)} ${esc(dir)} ${esc(k.id)}" aria-label="Remove ${esc(label.toLowerCase())}: ${esc(k.title)}">×</button></span>`).join('')}</div></div>`).join('')}${milestones}</section>`;
}
// ---- saving a task's properties. One save at a time per dialog, each with the version the last one returned, so a
// quick second edit never trips over the first. The dialog redraws at once with the task the server sent back; the
// list reloads behind it. A refused save shows its reason (under the properties) until that property next saves.
function taskSaveQueued(d: ModalDialog, id: string, body: PropBody, then?: boolean, field?: string) {
  field = field || d.focusProp || (typeof body === 'function' ? 'task' : Object.keys(body).find(k => k !== 'note')) || 'task';
  d.saving = (d.saving || 0) + 1;
  const run = () => taskSave(d, id, body, then, field).finally(() => { d.saving!--; });
  return (d.saveQueue = (d.saveQueue || Promise.resolve()).then(run, run));
}
/** To a stage of another pipeline: the same task, its number and label kept. */
const taskMoveTo = (id: string, pipeline: string, stage: string, version: number | null, why?: string): Promise<TaskMoved> =>
  post(`${taskPath(id)}/move`, withVersion(version, {to_pipeline: pipeline, to: stage, ...(why ? {why} : {})}), newRequestId());

// One save, by what it asks (task-api.ts): a move ({move: to, why?}, {close}, {done}), a new owner, a snooze, or the
// task's own fields. `quiet` with a move: a refusal for what is missing is answered (the caller asks why) instead of shown.
async function taskSaveSend(cur: ModalTask, body: PropPatch, note: string): Promise<TaskV3> {
  const id = String(cur.id), version = cur.version, why = typeof body.why === 'string' ? body.why : undefined;
  if (typeof body.move === 'string' && typeof body.pipeline === 'string') return (await taskMoveTo(id, body.pipeline, body.move, version, why)).task;
  if (typeof body.move === 'string') return (await taskMove(id, body.move, version, why)).task;
  if (body.close) return (await taskMove(id, 'closed', version)).task;
  if (body.done) return (await taskDone(id, version, note || undefined)).task;
  if (typeof body.owner === 'string') return (await taskReassign(id, body.owner, version, undefined, body.set_anyway === true)).task;
  if (typeof body.snooze === 'string') return (await taskSnooze(id, body.snooze, version)).task;
  if (body.unsnooze) return (await taskUnsnooze(id)).task;
  return taskEdit(id, version, body);
}
async function taskSave(d: ModalDialog, id: string, body: PropBody | null, then: boolean | undefined, field: string): Promise<boolean | TaskRefused> {
  const here = () => d.open && String(d.dataset.task) === id;
  let cur = d.liveTask && String(d.liveTask.id) === id ? d.liveTask : null;
  if (!cur) cur = (await v2Get(`/v2/tasks/${encodeURIComponent(id)}`))?.task;
  if (!cur) { toast('That task could not be loaded.', true); return false; }
  if (typeof body === 'function') { body = body(cur); if (!body) return true; }
  if (!body) return true;
  if (!d.propsErrs || d.propsErrs.id !== id) d.propsErrs = {id, map: new Map()};
  const errs = d.propsErrs.map;
  const note = body.done ? taskOutcomeNote(cur, {}, TASK_DRAFTS.get(String(id))) : '';
  const quiet = !!body.quiet;
  try {
    const task = await taskSaveSend(cur, body, note);
    if (note) taskOutcomeTaken(cur);
    errs.delete(field);
    if (String(d.liveTask?.id) === id) d.liveTask = task;
    if (here() && then !== false) void taskModalShow(task, d);       // the server's answer, at once
    // Closing says so with Undo for a while (S5): Undo reopens it.
    // Another pipeline: the task keeps its number, so the toast names it by that.
    if (typeof body.where === 'string') toast(`Moved ${task.number ? `#${task.number}` : task.title} to ${body.where}`);
    if (body.close) toast('Closed', false, {label: 'Undo', run: () => taskMove(id, 'reopen', task.version, 'Undone').then(r => { if (here()) void taskModalShow(r.task, d); }, e => toast(errorMessage(e, ''), true))});
    if (BOT) void botTasksCatchUp(BOT.slug);
    void personTasksReload();
    if (TASKS_ST) void tasksCatchUp(TASKS_ST);
    roadmapRankingRefresh();
    return true;
  } catch (e) {
    const refused = taskRefusedOf(e);
    if (quiet && refused?.missing.length) return refused;
    // A stage rule refused this owner: a person may give it all the same (taskPropsMsgPaint draws Set anyway).
    d.propsAnyway = refused?.error === 'task_stage_rule' && typeof body.owner === 'string' && body.set_anyway !== true ? {id, owner: body.owner} : null;
    if (!here()) { toast(errorMessage(e, ''), true); return false; }
    errs.set(field, refused?.error === 'task_changed' || errorStatus(e) === 409 && !refused?.detail ? 'Changed elsewhere. Showing the latest.'
      : `Not saved: ${refused?.detail || errorMessage(e, 'refused')}`);
    const fresh = await v2Get(`/v2/tasks/${encodeURIComponent(id)}`);
    if (fresh?.task && here()) {
      d.liveTask = fresh.task;
      void taskModalShow(fresh.task, d);
    } else taskPropsMsgPaint(d);
    return false;
  }
}
// ---- the conversation: the task's session as a chat. What people and the bot said are messages (the bot's replies
// open to the steps behind them), what changed is a one-line marker between them, and while the owner's run works a
// "Working" line shows its newest step, with Stop for whoever may stop it. The box says it in the task (a comment),
// which wakes its bot. `taskChatStop` and `taskChatLoad` keep their names: the router and the chat cards call them.
/** The open task's conversation: ui/app/refresh.ts reads it on every live change. */
// Its dialog and the box's host, the task it reads and how (the poll, or the live events' off switch), what was last read
// and drawn, the run being stopped, the composer's reach and Set as goal, and which task-update groups are open.
interface TaskChatState {
  dialog: ModalDialog; host: HTMLElement; path: string; id: string | number; sending: boolean; reading: boolean;
  liveOff?: (() => void) | null; cid?: string; stopped?: boolean; data?: ModalDetail; rendered?: string; stopping?: string | undefined;
  reach?: ComposeReach; goalOnce?: boolean; openLines?: Set<string>; count?: number;
  /** How many of the newest lines are drawn (taskChatEarlier adds a page), and whether a page is being added. */
  shown?: number; earlier?: boolean;
}
let TASK_JUMP = ENTRY_JUMP?.task ? {task: ENTRY_JUMP.task, id: ENTRY_JUMP.id} : null;   // {task, id}: a link's message, once that task's conversation shows
function taskChatStop() {
  if (TASK_CHAT) TASK_CHAT.liveOff?.();
  TASK_CHAT = null;
  liveTasksSync();                     // the router calls this on every page change too (ui/app/live.ts)
}
// The open task changed, or a comment landed in its conversation (live events, ui/app/live.ts): read it again, with
// its files (a new file or version, or an answer on one), once per burst.
function taskChatLive(state: TaskChatState) {
  if (!taskChatCurrent(state) || state.stopped) { if (TASK_CHAT === state && !state.dialog.open) taskChatStop(); return; }
  liveSoon('task:' + state.id, () => {
    if (!taskChatCurrent(state) || state.stopped) return;
    void taskChatRead(state);
    tfLoad(state.dialog, state.id).catch(() => {});
    void taskFilesTabLoad(state.dialog, state.id);
  }, 200);
}
const commentAuthor = (a: string | null | undefined, via?: string | null, src: {kind: string; name?: string} | null = null) => {
  if (a === 'external:roadmap') return '<span class="who">Visitor</span>';
  // The Assistant passing on a person's words reads as "Ana's Assistant"; the server itself is named by what made the row.
  if (src?.kind === 'assistant_for') return `${avatar(assistantBot(), 16)}<span class="who">${esc(src.name)}</span>`;
  if (src && SOURCE_LINES.has(src.kind)) return src.name ? `<span class="who">${esc(src.name)}</span>` : '';
  const pid = actorPerson(a);
  // The Assistant acted for this person (docs/assistant.md): "<name> (via Assistant)".
  const by = via === 'assistant' ? ' (via Assistant)' : '';
  if (pid) return `${personCircle(personDisplay(pid), 16)}<span class="who">${esc((pid === S.me?.id ? 'You' : personDisplay(pid)) + by)}</span>`;
  if (a === 'keeper') return '';
  const slug = actorSlug(a);
  return `${slug ? avatar(slug, 16, stateOf(slug)) : ''}<span class="who">${esc(actorLabel(a))}</span>`;
};
// A stage's name by its id, from the pipelines the Tasks page has read; else the id.
const taskStageWord = (id: string | null | undefined) => ((typeof TASK_PIPELINES !== 'undefined' && TASK_PIPELINES) || []).flatMap(p => p.stages).find(st => st.id === id)?.name || String(id || '');
// A stage with its pipeline ("Backlog › On ice"), by its id: how a move to another pipeline names both ends.
function taskStagePlace(id: string | null | undefined): {pipeline: string; words: string} | null {
  const p = ((typeof TASK_PIPELINES !== 'undefined' && TASK_PIPELINES) || []).find(x => x.stages.some(st => st.id === id)
    || x.done?.id === id || x.closed?.id === id);
  const st = p ? [...p.stages, p.done, p.closed].find(x => x?.id === id) : null;
  return p && st ? {pipeline: p.id, words: `${p.name} › ${st.name}`} : null;
}
// The pipelines' stage names a task's history names its moves by, read once when it has a move and the Tasks page has not read them.
const taskStagesFor = async (data: ModalDetail | null | undefined) => {
  if (!TASK_PIPELINES && data?.events?.some(e => e.field === 'move')) await tasksPipelinesRead().catch(() => []);
};
// Jev's verdict on a page, and an answer to an ask (ok, a send-back, done), in words.
const JEV_WORDS: Record<string, string> = {met: 'met', not_met: 'not met', unsure: 'unsure'};
const ANSWER_WORDS: Record<string, string> = {ok: 'gave the OK', no: 'sent it back', done: 'said done', yes: 'answered yes'};
// A move (backend/task_engine.py) names the stage it went to; a reassign who has it now.
const TASK_EVENT_WORDS: Record<string, (v: string | null | undefined, x: ModalEvent) => string> = {move: (id, x) => {
  // To another pipeline: "moved it from Dev › Ideas to Backlog › On ice".
  const from = taskStagePlace(x.old), to = taskStagePlace(id);
  return from && to && from.pipeline !== to.pipeline ? `moved it from ${from.words} to ${to.words}` : `moved it to ${taskStageWord(id)}`;
}, reassign: v => `handed it to ${actorLabel(v)}`, status: s => `moved it to ${taskStatusLabel({status: s ?? null})}`, owner: v => `handed it to ${actorLabel(v)}`,
  lane: v => `moved it to the ${v === 'company' ? 'team' : v} lane`, labels: v => { try { const l = JSON.parse(v || '[]'); return l.length ? `set the tags: ${l.join(', ')}` : 'removed the tags'; } catch { return 'changed the tags'; } },
  blocked_by: v => v ? 'marked it blocked' : 'cleared the block', parent_id: v => v ? 'filed it under a parent task' : 'took it out of its parent',
  subtask: v => v ? 'added a subtask' : 'moved a subtask out', blocks: v => v ? 'made it block another task' : 'stopped it blocking a task',
  related: v => v ? 'attached a related task' : 'removed a related task', duplicate_of: v => v ? 'marked it a duplicate' : 'unmarked it as a duplicate',
  duplicated_by: v => v ? 'marked a duplicate of it' : 'unmarked a duplicate of it', follow_up_of: v => v ? 'marked it a follow-up' : 'unmarked it as a follow-up',
  follow_ups: v => v ? 'added a follow-up' : 'removed a follow-up', depends_on: v => v ? 'made it wait for another task' : 'took off a task it waited for',
  reader: (v, x) => v ? `let ${actorLabel(v)} see this task` : `stopped ${actorLabel(x.old)} seeing this task`,
  link: v => v ? `linked ${v}` : 'removed a link', file: (v, x) => v ? `added ${v}` : `removed ${x.old || 'a file'}`, document: v => `wrote ${v}`, due: v => v ? `set the due date to ${timeSaid(v)}` : 'cleared the due date',
  lint: v => `noted: ${v}`, rank: () => 'moved it in the queue', children: v => String(v || ''), note: v => `noted: ${clipLine(String(v || ''), 200)}`, comment: () => 'deleted a comment',
  // The rest of the history the server writes (backend/task_engine.py and its neighbours), so no line reads
  // "changed <field>". A page attached carries its chip (taskEventChipHTML).
  page_attached: (_v, x) => x.old ? 'attached a new version of a page' : 'attached a page',
  jev_verdict: v => `Decision model's check: ${JEV_WORDS[String(v)] || String(v || '')}`,
  ok_kept: v => String(v || 'kept the OK on the new version'),
  answer: v => ANSWER_WORDS[String(v)] || `answered: ${clipLine(String(v || ''), 80)}`,
  requirement: () => 'asked for an answer', requirement_withdrawn: () => 'withdrew an ask',
  snooze: v => v ? `snoozed it to ${new Date(String(v)).toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'})}` : 'snoozed it',
  unsnooze: () => 'woke it', snooze_refused: () => 'was refused a snooze', woken_again: () => 'woke it again', late: () => 'marked it late',
  handoff_filed: () => 'filed a hand-off task', pick: v => `gave the turn to ${actorLabel(v)}`, give: v => `gave it to ${actorLabel(v)}`,
  github: v => `GitHub: ${v || ''}`, number: (v, x) => `numbered ${x.label || `#${v}`}`, pr_queued: v => `queued ${v} to merge`, pr_dequeued: () => 'took it out of the merge queue',
  pr_queue_removed: v => `dropped ${v || 'it'} from the merge queue`, merge_queue_failed: () => 'the merge queue sent it back',
  session: () => 'started a new session', task_model: v => `ran on ${v}`, before: () => 'set the order', after: () => 'set the order', work: () => 'stopped its work',
  title: () => 'renamed it', body: () => 'edited the request', goal_id: v => v ? 'tied it to a goal' : 'took it off its goal', human_id: v => v ? `made ${actorLabel(v)} its Human` : 'took its Human off',
  assignee_id: v => v ? `made ${actorLabel(v)} its developer` : 'took its developer off',
  ladder: v => `asked the ${v || 'next person'} instead`, requester: v => v ? `made ${actorLabel(v)} a requester` : 'removed a requester',
  stage_rule: () => 'applied a stage rule', readers: () => 'changed who can see it',
  private_readers: v => `was told who else reads it: ${clipLine(String(v || ''), 400)}`,
  private: v => ['1', 'true'].includes(String(v)) ? 'made it private' : 'made it visible to the team', from_private: () => 'filed it from a private task',
  visibility: v => v === 'public' ? 'put it on the public roadmap' : 'took it off the public roadmap', size: () => 'set its size',
  type: v => `made it a ${v || 'task'}`, step: v => `moved it to ${v || 'a step'}`};
// The history lines that always show on their own, because each carries something to read or open: a page attached
// (its chip), a kept OK, Jev's check, an answer (a send-back's reason) and the merge queue sending it back. Every other
// change (who filed, numbered, moved, handed over or snoozed it, tags, GitHub) folds when two or more come in a row
// (taskCommentsRender).
const TASK_EVENT_MILESTONES = new Set(['page_attached', 'ok_kept', 'jev_verdict', 'answer', 'merge_queue_failed']);
// The history lines Tico writes itself, whoever's action set them off: the number a numbered pipeline gives, a stage
// rule's Block, Tell or Move back, and the hand-off task a stage files. They read as a quiet line with no name
// ("Numbered O561"), never as the person or bot who moved or filed the task.
const TASK_EVENT_SYSTEM = new Set(['number', 'stage_rule', 'handoff_filed']);
// A page attached is a chip that opens that version of the page: `page:<file id>@<version>`.
function taskEventChipHTML(x: ModalLine) {
  if (x.kind !== 'event' || x.field !== 'page_attached') return '';
  const m = /^page:([^@\s]+)(?:@(\d+))?$/.exec(String(x.new || ''));
  if (!m) return '';
  return ` <a class="tpage-chip" href="${PAGE_ROUTE}${encodeURIComponent(m[1]!)}${m[2] ? `?v=${m[2]}` : ''}">${PROP_ICON.open}<span>Page${m[2] ? ` v${m[2]}` : ''}</span></a>`;
}
// The note an event carries reads as its first line (a send-back's reason, Jev's finding); GitHub's repeats the line.
const taskEventNote = (x: ModalLine) => x.kind === 'event' && x.field !== 'github' ? String(x.note || '').trim().split('\n')[0] || '' : '';
// `files`: the task's files (ui/app/task-files.ts), for the files a comment carried.
function commentLineHTML(x: ModalLine, i: number, all: ModalLine[] | null | undefined, files: TfFile[] = [], taskId: string | number = '') {
  if (x.kind === 'run') {
    const bot = String(x.run.bot || ''), words = taskRunLineWords(x.run);
    return `<div class="tcomment sys tmark trun" data-run-line="${esc(x.run.run)}"><span class="tcomment-who">${esc(actorLabel(bot.includes(':') ? bot : 'bot:' + bot))}</span> <span class="muted">${esc(words)}</span>
      <time class="muted tnum" title="${esc(fmt(x.run.finished_at))}">${esc(ago(x.run.finished_at))}</time></div>`;
  }
  if (x.kind === 'event') {
    if ((x.field === 'status' || (x.field === 'move' && !x.note)) && x.old == null) return '';   // created: the header says so
    // The type and first step a task is filed with are part of its creation too, not moves.
    if ((x.field === 'type' || x.field === 'step') && x.old == null
      && all?.some(y => y.kind === 'event' && y.field === 'status' && y.old == null && Math.abs(Date.parse(y.ts) - Date.parse(x.ts)) < 2000)) return '';
    if (x.field === 'comment' && x.new) return '';                  // an edit: the comment itself says edited
    if (x.field === 'closed_with') return '';                       // its close's note already says so
    // A step move already names where the task went; its status change would say it twice.
    if (x.field === 'status' && all?.some(y => y.kind === 'event' && y.field === 'step' && y.new && y.ts === x.ts)) return '';
    // A note saved with a status change or a comment is already shown there; a note on its own says what it is.
    if (x.field === 'note') {
      const text = String(x.new || '').trim(), at = Date.parse(x.ts);
      const near = (y: ModalLine) => Math.abs(Date.parse(y.ts) - at) < 5000;
      if (!text || all?.some(y => near(y) && ((y.kind === 'event' && y.field === 'status' && y.note && text.startsWith(String(y.note).trim().slice(0, 40)))
        || (y.kind === 'comment' && String(y.message?.body || '').trim() === text)))) return '';
    }
    const words = TASK_EVENT_WORDS[x.field];
    const say = words ? words(x.new, x) : 'updated the task';
    // A result mirrored into a comment keeps its full text there; the event still records the status move.
    const mirrored = x.note && all?.some(y => y.kind === 'comment' && y.message?.sender_id === x.actor
      && String(y.message.body || '').trim() === String(x.note).trim() && Math.abs(Date.parse(y.ts) - Date.parse(x.ts)) < 5000);
    const src = rowSource(x, x.actor), who = TASK_EVENT_SYSTEM.has(x.field) ? '' : commentAuthor(x.actor, x.via, src);
    // A change the server made with no name to give it (a system line) reads as a sentence of its own.
    const said = who ? say : say.charAt(0).toUpperCase() + say.slice(1);
    const note = x.field !== 'note' && !mirrored ? taskEventNote(x) : '';
    return `<div class="tcomment sys tmark" data-event="${esc(x.field)}">${who ? `<span class="tcomment-who">${who}</span>` : ''} <span class="muted">${esc(said)}${note ? ` — ${esc(clipLine(note, 200))}` : ''}</span>${taskEventChipHTML(x)}
      <time class="muted tnum" title="${esc(fmt(x.ts))}">${esc(ago(x.ts))}</time></div>`;
  }
  const m = x.message, ask = askOf(m);
  if (m.refs?.reconnect && typeof m.refs.reconnect === 'object') return reconnectCardHTML(m);
  // A task update, a built-in's, an automation's or a system line: one gray line, no sender row (rowSource).
  if (!ask && isSourceLine(m)) return sourceLineHTML(m, {cls: 'tcomment tline'});
  // a question's text is in its block; a body that only repeats it is left out
  const body = ask && ask.questions!.some(q => String(q.question || '').trim() === String(m.body || '').trim()) ? '' : m.body || '';
  const kind = m.kind === 'ask' ? '<span class="pill needs">question</span>' : '';
  // The chat's own row (ui/app/thread-view.ts thvMsgHTML, styles/threads.css): the face, then the name and time, then
  // the words. No bubbles, nothing on the right; yours is marked th-mine. A bot's reply opens to its run first.
  const bot = String(m.sender_id || '').startsWith('bot:'), me = !!m.sender_id && m.sender_id === myActor();
  // A bot's or another person's comment takes a row of Copy, 👍 and 👎 under it, as in a chat (ui/app/feedback.ts).
  const fb = m.id && fbRates(m.sender_id) ? fbKey('message', m.id) : '';
  return `<div class="th-msg tcomment tmsg${me ? ' th-mine from-me' : ''}${bot ? ' bot' : ''}" data-message="${esc(m.id || '')}"><div class="th-av">${taskMsgFace(m)}</div>
    <div class="th-main"><div class="th-head">${taskMsgWho(m)}${kind}${roadmapCommentMark(m, taskId)}<time datetime="${esc(m.created_at)}" title="${esc(fmt(m.created_at))}">${esc(ago(m.created_at))}</time>${
      m.refs?.quiet ? '<span class="th-state muted" title="Saved for the bot\'s next run on this task">saved</span>' : ''}${qmarkHTML(m.refs)}</div>
    ${taskStepsHTML(m)}<div class="th-body-wrap"${fb ? ` data-fb-press="${esc(fb)}"` : ''}>${body ? messageMd('th-body md', body, m.sender_id) : ''}
    ${tfCommentFilesHTML(m, files)}${ask ? askHTML(ask, m.answers, m.sender_id) : ''}${fbRowHTML(fb, m.body || '')}</div></div></div>`;
}
// A message's face and name, as the chat shows them: the Assistant passing on a person's words is that person's
// Assistant; yours reads "You"; a bot's or a person's name opens their page.
function taskMsgFace(m: ModalComment) {
  const src = rowSource(m);
  if (src?.kind === 'assistant_for') return avatar(assistantBot(), 28);
  if (m.sender_id === 'external:roadmap') return personCircle('Visitor', 28);
  return actorAvatar(m.sender_id, 28);
}
function taskMsgWho(m: ModalComment) {
  const a = m.sender_id, src = rowSource(m), via = m.refs?.via === 'assistant' ? ' (via Assistant)' : '';
  if (a === 'external:roadmap') return '<span class="th-who">Visitor</span>';
  if (src?.kind === 'assistant_for') return `<span class="th-who">${esc(src.name)}</span>`;
  if (a && a === myActor()) return `<span class="th-who">You${esc(via)}</span>`;
  const slug = actorSlug(a), pid = actorPerson(a);
  if (slug && botRow(slug)) return `<a class="th-who" href="#/bot/${esc(encodeURIComponent(slug))}">${esc(actorLabel(a))}</a>`;
  if (pid) return `<a class="th-who" href="#/person/${esc(encodeURIComponent(pid))}">${esc(personDisplay(pid) + via)}</a>`;
  return `<span class="th-who">${esc(actorLabel(a) || 'Someone')}</span>`;
}
// A bot's work on this task stopped on an account (backend/reconnects.py): its one line, the fix for the person who
// can make it, and Reconnected once it works. A missing secret's fix is its credential card (ui/credential-card.ts).
function reconnectCardHTML(m: ModalComment) {
  const r = m.refs.reconnect!, fix: ModalReconnectFix = r.fix || {}, status = r.status || 'open', fixed = status === 'fixed', mine = r.person === myActor();
  const head = `<div class="tcomment-head"><span class="tcomment-who">${commentAuthor(m.sender_id)}</span><span class="spacer"></span><time class="muted tnum" title="${esc(fmt(m.created_at))}">${esc(ago(m.created_at))}</time></div>`;
  const wrap = (inner: string) => `<div class="tcomment tmsg bot" data-message="${esc(m.id || '')}">${head}${inner}</div>`;
  // A retired card asks nothing: its request is canceled, so it shows Not needed, never a card to save.
  if (r.service === 'secret' && mine && m.refs.credential_request && status !== 'retired') {
    return wrap(`<div data-credential-host="${esc(m.refs.credential_request)}"><div class="cc-done">Loading the card…</div></div>`);
  }
  let act = '';
  if (fixed) act = `<span class="rc-done">${r.service === 'secret' ? 'Saved' : 'Reconnected'}</span>`;
  else if (status !== 'open') act = `<span class="muted">${status === 'canceled' ? 'Canceled' : 'Not needed'}</span>`;
  else if (!mine) act = `<span class="muted">Waiting on ${esc(actorLabel(r.person) || 'a person')}</span>`;
  else if (r.service === 'google') act = '<button type="button" class="primary" data-reconnect-google>Reconnect Google</button>';
  else if (r.service === 'login' && typeof harnessCaps === 'function' && harnessCaps(fix.runtime).browser_sign_in) {
    act = `<button type="button" class="primary" data-model-login data-runner="${esc(fix.runner || '')}" data-runtime="${esc(fix.runtime || '')}" data-machine="${esc(fix.machine || '')}">Sign in</button>`;
  } else act = `<a class="button primary" href="#/settings/computers">${r.service === 'login' ? 'Sign in' : 'Open settings'}</a>`;
  return wrap(`<div class="rc-card" data-reconnect="${esc(r.key || '')}" data-status="${esc(status)}"><span class="rc-line">${esc(m.body || '')}</span>${act}</div>`);
}
// Reconnect Google: the person's own Connect, as Tools › Settings starts it (ui/google-connect.ts).
document.addEventListener('click', async ev => {
  const button = eventElement(ev)?.closest<HTMLButtonElement>('[data-reconnect-google]');
  if (!button) return;
  button.disabled = true;
  try {
    const started = await post('/v2/mail/google/connect', {});
    // Only Google's own sign-in page, never an address from anywhere else.
    if (!/^https:\/\/accounts\.google\.com\//.test(started.auth_url || '')) throw new Error('Unexpected sign-in address');
    location.href = started.auth_url;
  } catch (e) { toast(errorMessage(e, ''), true); button.disabled = false; }
});
// A reply's run, folded to one line ("6 steps · 4 tool calls · 2m"); opening it reads the steps once (GET
// /v2/turns/{id}/steps) and they stay open across redraws. `TASK_STEPS` holds what was read, per turn.
const TASK_STEPS: ModalSteps = {html: new Map(), live: new Map(), open: new Set(), reading: new Set()};
function taskStepsFold(turn: string, label: string, live = false) {
  // a working run's fold keeps the steps last read while the next read is on its way
  const open = TASK_STEPS.open.has(turn), html = (live ? TASK_STEPS.live : TASK_STEPS.html).get(turn);
  return `<details class="run-steps" data-turn="${esc(turn)}"${live ? ' data-live' : ''}${open ? ' open' : ''}><summary>${label}</summary><div class="steps-list">${
    html || '<div class="step muted">Loading…</div>'}</div></details>`;
}
function taskStepsHTML(m: ModalComment) {
  const run: ModalRunCount = m.run || {}, turn = m.refs?.turn_id;
  if (!run.steps || !turn) return '';
  return taskStepsFold(turn, esc([`${run.steps} step${run.steps === 1 ? '' : 's'}`,
    run.tool_calls ? `${run.tool_calls} tool call${run.tool_calls === 1 ? '' : 's'}` : '', tookWords(run.took_s)].filter(Boolean).join(' · ')));
}
async function taskStepsOpen(el: HTMLDetailsElement) {
  const turn = el.dataset.turn, live = el.hasAttribute('data-live');
  if (turn === undefined) return;
  if (!el.open) { TASK_STEPS.open.delete(turn); return; }
  TASK_STEPS.open.add(turn);
  // A finished run's steps are read once; a working run's are read again each time it is opened or moves on.
  if (!live && TASK_STEPS.html.has(turn) || TASK_STEPS.reading.has(turn)) return;
  TASK_STEPS.reading.add(turn);
  let html: string;
  try {
    html = v2StepsHTML(await get(`/v2/turns/${encodeURIComponent(turn)}/steps`));
    (live ? TASK_STEPS.live : TASK_STEPS.html).set(turn, html);
  } catch (error) {
    html = `<div class="step err">${esc(errorStatus(error) === 403 ? 'Only people who may read this bot’s runs see its steps.' : errorMessage(error, ''))}</div>`;
  }
  finally { TASK_STEPS.reading.delete(turn); }
  // a redraw while it was read put a new fold in its place
  const list = $(`.task-comments details.run-steps[data-turn="${CSS.escape(turn)}"] .steps-list`, document);
  if (list) list.innerHTML = html;
}
// The line under the thread while a run answers it: the bot chat's pending line (pendingRunText, ui/app/chat.ts), so
// Sent, Queued and why, Starting, "<Bot> is working" with its newest step, or Reply failed. `execution` is the owner's
// run or the run for a person's message, whichever bot that went to (GET /v2/tasks/{id}); a queued run for anything
// else is `waiting_for`'s. The steps so far are one click away, and Stop is there for whoever manages the bot
// (POST /v2/bots/{slug}/stop, as in the bot's chat). `sent`: the id of a message just sent; until its own run is read,
// it is Sent, never the state of an earlier message's run.
function taskLiveHTML(x: ModalExecution | null | undefined, stopping = '', sent = '') {
  if (sent && x?.message_id !== sent) x = null;
  const working = !!x && ['leased', 'running'].includes(x.state);
  const run = working || x?.from_person && v2RunLive(x) ? x : null;
  if (!run && !sent) return '';
  // The bot at work, by its id (`bot_id`): never the reader, whoever sent the message the run answers.
  const slug = String(run?.bot_id || '').replace(/^bot:/, '');
  const t = pendingRunText(run, slug || undefined);
  // The chat's status row (styles/threads.css .th-status): the bot at work, its state in a word, then its newest step.
  // Several bots may answer on a task, so the row names the one at work.
  const bot = slug ? 'bot:' + slug : '';
  // Its state in a word: working, starting, up next, sent; a run that ended badly (Reply failed) can't reply.
  const state = !run ? 'sent' : run.state === 'running' && !run.waiting ? 'working' : run.state === 'leased' && !run.waiting ? 'starting'
    : run.state === 'queued' || run.waiting ? 'up next' : "can't reply";
  const part = working && x ? (x.parts || []).at(-1) : null;
  const step = part ? clipLine(part.text.replace(/\s+/g, ' ').trim(), 120) : '';
  const said = step || (t.word && t.word.toLowerCase() !== state ? t.word : '') || (state === 'sent' ? '' : t.tip);
  const line = said ? `${working ? '<span class="th-status-dot" aria-hidden="true"></span>' : ''}<span class="task-live-step">${esc(said)}</span>` : '';
  const stop = working && x && x.run_id && slug && settingsCanManageBot(botRow(slug))
    ? `<button type="button" class="ghost small th-status-act task-live-stop" data-task-stop="${esc(x.run_id)}" data-bot="${esc(slug)}" ${
      stopping === x.run_id ? 'title="Stopping…" disabled>Stopping…' : 'title="Stop this run">Stop'}</button>` : '';
  const detail = working && x?.run_id ? taskStepsFold(x.run_id, line || esc(state), true) : line;
  return `<div class="th-status th-status-${working ? 'working' : state === "can't reply" ? 'cant_reply' : 'up_next'} task-live" role="status" aria-label="${esc(t.tip)}${step ? ': ' + esc(step) : ''}">
    <div class="th-av">${bot ? actorAvatar(bot, 28) : actorAvatar(myActor(), 28)}</div>
    <div class="th-main"><div class="th-head"><span class="th-who">${esc(bot ? actorLabel(bot) : 'You')}</span><span class="th-status-word thinking-word">${esc(state)}</span></div>${
      detail ? `<div class="th-status-detail">${detail}</div>` : ''}${pendingHintHTML(run)}</div>${stop}</div>`;
}
// Who has a task this one waits on (its turn's `who` is that task's id): from the subtasks or the related tasks the
// reader was sent, '' when it is neither (a task the reader may not open).
function taskWaitHolder(data: ModalDetail, t: ModalTask | null, id: string): string {
  const rows = [...(data.children || []) as {id?: unknown; owner_id?: unknown}[], ...Object.values(t?.relations || {}).flat()];
  const owner = String(rows.find(k => String(k.id) === id)?.owner_id || '');
  return owner.includes(':') ? owner : '';
}
// A ❓ waits under its mark's id, `qmark:<mark id>` (backend/question_marks.py wait_id).
const taskTurnIsQmark = (id: unknown) => String(id || '').startsWith('qmark:');
// The rows at the end of the chat that say what is happening now: a run at work (taskLiveHTML), else what the bot's next
// run waits for, else whose turn it is; a deferred hand-over; and, whatever else shows, the reader's own OK with Answer.
// The reader's own ❓ has no row: it is the bot's message with its ? mark, and their reply in the composer clears it.
function taskStateHTML(state: TaskChatState, data: ModalDetail, sent = '') {
  const t = data.task || state.dialog.liveTask || null;
  const live = taskLiveHTML(data.execution, state.stopping, sent);
  const turn = t && !taskFinished(t) ? t.turn : null, me = myActor();
  const myQmark = !!turn && !!me && turn.who === me && taskTurnIsQmark(turn.requirement_id);
  const yours = !!turn?.ask && !!turn.requirement_id && !!me && turn.who === me && !!turn.line && !myQmark;
  const owner = String(t?.owner_id || '');
  // A row names whoever it is about, face first; with nobody to name it has no face, and a word alone leads it
  // ("Waiting on you"). A line about another task (`open`: one it depends on or filed) opens that task.
  const row = (who: string, word: string, line: string, cls: string, extra = '', act = '', open = '') => {
    const head = who ? `<span class="th-who">${esc(who === me ? 'You' : actorLabel(who))}</span>${word ? `<span class="th-status-word">${esc(word)}</span>` : ''}`
      : word ? `<span class="th-status-word th-status-lead">${esc(word.charAt(0).toUpperCase() + word.slice(1))}</span>` : '';
    const said = open ? `<a class="task-state-line" href="#/task/${encodeURIComponent(open)}" data-task-wait-open="${esc(open)}">${esc(line)}</a>`
      : `<span class="task-state-line">${esc(line)}</span>`;
    return `<div class="th-status ${cls}${who ? '' : ' th-status-bare'}" role="status">${who ? `<div class="th-av">${actorAvatar(who, 28)}</div>` : ''}<div class="th-main">${
      head ? `<div class="th-head">${head}</div>` : ''}${line ? `<div class="th-status-detail">${said}${extra}</div>` : extra}</div>${act}</div>`;
  };
  let now = live;
  if (!now && data.waiting_for) now = row(owner.startsWith('bot:') ? owner : '', 'up next', data.waiting_for, 'th-status-up_next task-wait',
    /^out of credit/i.test(data.waiting_for) ? cloudCreditLinkHTML() : '');
  // Whose turn it is, in the turn's own words ("Checks running", "Waiting on Ana's OK"), when no run says more. A person's
  // or a bot's turn always leads with who it is; a raw id never shows. Someone else's ask says what is owed ("Ben to
  // answer"), so the question does not read as theirs. A task it waits on (one it filed, one it depends on) is named by
  // whoever has that task when the reader can see it, else the row leads with "Waiting on a task".
  if (!now && turn?.line && !yours && !myQmark) {
    const other = turn.state === 'tasks' && turn.who ? String(turn.who) : '';
    const who = String(turn.who || '').includes(':') ? String(turn.who) : other ? taskWaitHolder(data, t, other) : '';
    const word = who && turn.ask ? 'to answer' : who && turn.state === 'bot' && turn.running ? 'working' : other && !who ? 'waiting on a task' : '';
    now = row(who, word, clipLine(turn.line, 400), 'th-status-turn task-wait', taskCauseLinkHTML(turn.href), '', other);
  }
  const q = t && !taskFinished(t) ? t.deferred || t.quiet : null;
  let deferred = '';
  if (q) {
    const st = q.state === 'delivered' && q.resume ? 'held' : q.state;
    const label = ({queued: 'Queued', eligible: 'Eligible', delivered: 'Delivered', held: 'Held'} as Record<string, string>)[st] || st;
    const more = [q.run ? `run ${String(q.run).slice(0, 8)}` : '', q.at ? `handed over ${agoSaid(q.at)}` : ''].filter(Boolean).join(', ');
    deferred = `<p class="task-deferred src-line" data-task-deferred="${esc(st)}"><span class="lbl">Deferred: ${esc(label)}</span> ${esc(q.words || '')}${more ? ` <span class="muted">(${esc(more)})</span>` : ''}</p>`;
  }
  // A task waiting on the reader's own answer: the last row, with Answer, so it is the first thing seen. It names who
  // asked when the ask says (its `asked_by`), else it leads with "Waiting on you".
  const asker = yours && t && turn ? String((t.to_leave || []).find(r => String(r.requirement_id) === String(turn.requirement_id))?.asked_by || '') : '';
  const ask = yours && t && turn ? row(asker.includes(':') && asker !== me ? asker : '', 'waiting on you', clipLine(turn.line, 400), 'th-status-ask task-need', taskCauseLinkHTML(turn.href),
    `<button type="button" class="primary small th-status-act" data-task-yours data-task-answer="${esc(String(turn.requirement_id))}">Answer</button>`) : '';
  return deferred + now + ask;
}
// Whether the draft @mentions one of `names`, as the server reads an @mention (backend/hubdb.py _at): the name, not
// inside a word or another @name.
const taskNamed = (names: string[] | null | undefined, draft: string | null | undefined) => (names || []).some(n =>
  new RegExp('(?<![\\w@])@' + n.replace(/[.*+?^$()|[\]\\{}]/g, '\\$&') + '(?![\\w-])', 'i').test(draft || ''));
// Who the comment being written will reach (GET /v2/tasks/{id} `comment_to`), said before it is sent: no bot at all,
// or a bot that reads it on its next run on the task unless the draft @mentions it.
function taskComposerTo(to: ModalCommentTo | null | undefined, draft: string | null | undefined) {
  if (!to) return '';
  if (!to.bot) return 'No bot gets this message: it is saved on the task for the people on it.';
  if (to.wakes || taskNamed(to.mention, draft)) return '';
  return `Saved for ${botDisplayName(to.bot)}'s next run on this task.${to.mention?.length ? ` Write @${to.mention[0]} to ask now.` : ''}`;
}
// How a comment reaches its bot (docs/tasks.md "Comments"), in the bot chat's words (deliveryMenuHTML, ui/app/pill.ts):
// Steer into its running turn, Queue after its current turn, Defer until its next turn on this task (at the latest
// after a while), or Set as goal of its task session. Send shows the person's kept way (sendWayPick, ui/app/pill.ts)
// and the comment always names it; Set as goal is for the next comment only.
let TASK_MENU_CLOSE: (() => void) | null = null;                 // closes the open delivery menu; a press elsewhere closes it
document.addEventListener('pointerdown', ev => {
  if (TASK_MENU_CLOSE && !eventElement(ev)?.closest('.task-send')) TASK_MENU_CLOSE();
});
try { localStorage.removeItem('hub.task.delivery.v2'); } catch {}     // the per-task picks the kept way replaced
SEND_WAY_PAINT.add(() => { if (TASK_CHAT?.data && taskChatCurrent(TASK_CHAT)) taskComposerToPaint(TASK_CHAT); });
// The send has ways only where a bot would get the comment (GET /v2/tasks/{id} `comment_delivery`, null without one).
const taskDeliverySplit = (state: TaskChatState) => state.data?.comment_delivery?.default != null && !!state.data?.comment_to?.bot;
// The ways this reader may send: steer only for someone who may steer, queue only for someone who may wake the bot,
// goal only where the session can take one; anyone who may comment may defer.
function taskDeliveryWays(state: TaskChatState): string[] {
  const cd: ModalDelivery = state.data?.comment_delivery || {};
  return (['steer', 'queue', 'defer', 'goal'] as const).filter(w => w === 'defer' || (w === 'queue' ? cd.queue !== false : !!cd[w]));
}
const taskDeferDefault = (state: TaskChatState) => {
  const f = state.data?.comment_delivery?.default_for;
  return typeof f === 'string' && DELIVERY_FOR.includes(f) ? f : '24h';
};
// Whether this reader may wake the task's bot. A deadline wakes it as a steer would, so a reader who may not defers
// with none (the server refuses any other defer_for with a 403).
const taskDeliveryWakes = (state: TaskChatState) => taskDeliveryWays(state).some(w => w === 'steer' || w === 'queue');
// The way the next comment goes: Set as goal when picked for it, else the kept way, or the nearest one this reader may
// use (Steer becomes Queue; without either, Defer with no deadline).
function taskDeliveryMode(state: TaskChatState) {
  const ways = taskDeliveryWays(state);
  if (state.goalOnce && ways.includes('goal')) return 'goal';
  if (!taskDeliveryWakes(state)) return 'defer:never';
  const kept = SEND_WAY, way = deliveryWay(kept);
  if (typeof way === 'string' && ways.includes(way)) return kept;
  if (way === 'steer' && ways.includes('queue')) return 'queue';
  return 'defer:' + taskDeferDefault(state);
}
// Send's icon says the way the comment is sent; without a bot it is the plain arrow, with no caret.
function taskDeliveryPaint(state: TaskChatState) {
  const main = $('[data-task-main]', state.host), caret = $('[data-task-caret]', state.host);
  if (!main || !caret) return;
  const split = taskDeliverySplit(state);
  caret.hidden = !split;
  if (!split) taskDeliveryMenu(state, false);
  sendWayPaint(main, split ? taskDeliveryMode(state) : 'steer');
}
// The ways to send, opening upward from the caret; choosing one keeps the draft and only sets the way.
function taskDeliveryMenu(state: TaskChatState, open: boolean) {
  const menu = $('[data-task-menu]', state.host), caret = $('[data-task-caret]', state.host);
  if (!menu || !caret) return;
  if (!open && menu.hidden) return;
  if (open && !taskDeliverySplit(state)) return;
  const ways = taskDeliveryWays(state), mode = open ? taskDeliveryMode(state) : '';
  menu.innerHTML = open ? deliveryMenuHTML({steer: ways.includes('steer'), queue: ways.includes('queue'), goal: ways.includes('goal'),
    deadlines: taskDeliveryWakes(state), current: mode, deferFor: deliveryWay(mode) === 'defer' ? deliveryFor(mode) : taskDeferDefault(state)}) : '';
  menu.hidden = !open;
  caret.setAttribute('aria-expanded', String(open));
  TASK_MENU_CLOSE = open ? () => taskDeliveryMenu(state, false) : null;
  if (open) (menu.querySelector('[aria-checked="true"]') || menu.querySelector('button'))?.focus();
}
function taskComposerToPaint(state: TaskChatState) {
  const hint = $('[data-task-chat-to]', state.host), box = $('textarea', state.host);
  // No line for the way Send shows: Reaches N says it. This line only holds the no-bot notice.
  if (hint) hint.textContent = taskDeliverySplit(state) ? '' : taskComposerTo(state.data?.comment_to, box?.value);
  // The box names who reads it: the bot the comment reaches, else the task's owner when that is someone else. Opened
  // from a pipeline's board, it asks the task's owner, and what is said stays in this task's conversation.
  const owner = String(state.data?.task?.owner_id || ''), bot = state.data?.comment_to?.bot;
  const reader = bot ? botDisplayName(bot) : owner && owner !== myActor() ? actorLabel(owner) : '';
  if (box) box.placeholder = state.dialog.dataset.ask && owner ? `Ask ${actorLabel(owner)} about this task…` : reader ? `Message ${reader}` : 'Message';
  taskDeliveryPaint(state);
  taskFootPaint(state);
}
// Follow and "Reaches N" under the box (pill.js composeReach), for a task with a thread of its own (its `followers`
// list; null for a task in a shared chat). Follow shows until you follow; a ticked Follow follows once the comment is in.
const taskFollowersOf = (state: TaskChatState) => {
  const id = state.data?.task?.id;
  return id != null && TASK_FOLLOWERS.has(String(id)) ? TASK_FOLLOWERS.get(String(id)) : state.data?.task?.followers;
};
const taskOwnThread = (state: TaskChatState) => Array.isArray(taskFollowersOf(state)) ? state.data?.task?.conversation_id || '' : '';
// The way the reach is asked for: the way Send shows, or the server's Steer where the comment names none.
const taskReachMode = (state: TaskChatState) => taskDeliverySplit(state) ? taskDeliveryMode(state) : '';
function taskFootPaint(state: TaskChatState) {
  const foot = $('.p-foot', state.host);
  if (!foot) return;
  state.reach?.update();
}
// The reach line under the box (pill.ts) says "To Firmware Engineer"; when the box's placeholder already names that same
// one reader ("Message Firmware Engineer") it says nothing new, so it stays empty, as pill.ts leaves a bot's Home quiet:
// only one reader, nobody outside ("Not reached") and nobody newly let in ("can now see it"). Anything else still shows.
// The line is drawn by pill.ts on its own schedule, so this watches it.
function taskReachQuiet(state: TaskChatState, host: HTMLElement) {
  const foot = $('.p-foot', host), reach = foot && $('.p-reach', foot), box = $('textarea', host);
  if (!reach || !box) return;
  const quiet = () => {
    const reader = /^Message (.+)$/.exec(box.placeholder || '')?.[1], d = state.reach?.data, only = d?.reaches.length === 1 ? d.reaches[0] : null;
    if (reach.hidden || !reader || !only || (d?.outside || []).length || only.adds_reader || reach.textContent !== 'To ' + reader) return;
    reach.hidden = true; foot.hidden = true;
  };
  new MutationObserver(quiet).observe(reach, {attributes: true, attributeFilter: ['hidden'], childList: true, characterData: true, subtree: true});
  quiet();
}
// The Followers row changed (task-props.js): who the box reaches changes with it.
function taskFootRepaint() { if (TASK_CHAT?.data && taskChatCurrent(TASK_CHAT)) taskFootPaint(TASK_CHAT); }
async function taskRunStop(state: TaskChatState, button: HTMLButtonElement) {
  button.disabled = true; button.title = 'Stopping…';
  try {
    await post(`/v2/bots/${encodeURIComponent(button.dataset.bot!)}/stop`, {run_id: button.dataset.taskStop});
    state.stopping = button.dataset.taskStop;      // the redraws keep "Stopping…" until the run has ended
    button.textContent = 'Stopping…';
    taskChatLive(state);
  } catch (e) { button.disabled = false; button.title = 'Stop this run'; toast(`Not stopped: ${errorMessage(e, '')}`, true); }
}
function taskCommentsRender(state: TaskChatState, data: ModalDetail, sent = '') {
  const host = state.host;
  const comments = (data.comments || data.messages || []).filter(m => ['say', 'ask', 'answer'].includes(m.kind));
  const lines: ModalLine[] = [
    ...comments.map((m): ModalLine => ({kind: 'comment', ts: m.created_at, message: m})),
    // A number reads as the task's label ("O561"), which its own read carries.
    ...(data.events || []).map((e): ModalLine => ({kind: 'event', ts: e.created_at, ...e,
      ...(e.field === 'number' && data.task?.label && String(data.task.number) === String(e.new) ? {label: data.task.label} : {})})),
    // Each finished bot run is one line: "Builder 2 worked 14 min".
    ...(data.runs || []).filter(r => r.started_at && r.finished_at && r.bot).map((r): ModalLine => ({kind: 'run', ts: String(r.finished_at), run: r})),
  ].sort((a, b) => String(a.ts).localeCompare(String(b.ts)));
  if (!$('form', host)) {
    // The box is the main chat's composer: a pill, one line that grows, the send arrow, Return sends.
    host.innerHTML = `<h3>Conversation</h3>
      <div class="task-comments" role="log" aria-live="polite"></div>
      <form class="task-pill"><span class="muted" data-task-chat-to role="note"></span><div class="p-pill"><textarea class="p-text" rows="1" maxlength="4000" required aria-label="Message" placeholder="Message…"></textarea>
          <span class="task-send" data-task-send><button class="task-send-caret p-way" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="Ways to send" title="Ways to send" data-task-caret hidden><span class="nav-icon" aria-hidden="true">expand_more</span></button><button class="p-send p-send-icon" type="submit" aria-label="Send" title="Send" data-task-main>${ICON_SEND}</button><div class="p-menu p-ways" role="menu" aria-label="Ways to send" data-task-menu hidden></div></span></div>
          ${composeFootHTML()}<span class="muted" data-task-chat-status role="status"></span></form>`;
    $('form', host).onsubmit = (ev: SubmitEvent) => { ev.preventDefault(); void taskCommentSend(state); };
    state.reach = composeReach($('.p-foot', host), {thread: () => taskOwnThread(state), mode: () => taskReachMode(state),
      text: () => $('textarea', host)?.value || ''});
    taskReachQuiet(state, host);
    const caret = $('[data-task-caret]', host), menu = $('[data-task-menu]', host);
    caret.onclick = () => taskDeliveryMenu(state, menu.hidden);
    sendWayHold($('[data-task-main]', host), () => taskDeliveryMenu(state, true), () => taskDeliverySplit(state));
    menu.onclick = (ev: MouseEvent) => {
      const pick = eventElement(ev)?.closest<HTMLElement>('[data-deliver]')?.dataset.deliver;
      if (!pick) return;
      // Set as goal is for the next comment; any other pick is the person's kept way, in every composer.
      state.goalOnce = pick === 'goal';
      if (pick !== 'goal') sendWayPick(pick);
      taskDeliveryMenu(state, false); taskComposerToPaint(state); $('textarea', host).focus();
    };
    menu.addEventListener('keydown', (ev: KeyboardEvent) => deliveryMenuKeys(menu, ev));
    // Escape anywhere in the form (Safari leaves focus in the box) closes an open menu, not the task.
    $('form', host).addEventListener('keydown', (ev: KeyboardEvent) => {
      if (ev.key !== 'Escape' || menu.hidden) return;
      ev.preventDefault(); ev.stopPropagation();      // closes the menu, not the task
      taskDeliveryMenu(state, false); caret.focus();
    });
    const box = $('textarea', host);
    box.value = TASK_DRAFTS.get(String(state.id)) || '';
    const grow = () => pillGrow(box);
    box.oninput = () => {
      grow(); taskComposerToPaint(state);
      if (box.value) TASK_DRAFTS.set(String(state.id), box.value); else TASK_DRAFTS.delete(String(state.id));
    };
    if (box.value) requestAnimationFrame(grow);
    // As in the main chat: on a computer Return sends and Shift+Return is a new line; on a phone Return is a line.
    box.onkeydown = (ev: KeyboardEvent) => {
      if (ev.key !== 'Enter' || ev.isComposing) return;
      if (ev.metaKey || ev.ctrlKey || (!ev.shiftKey && !touchKeyboard())) { ev.preventDefault(); void taskCommentSend(state); }
    };
    const thread = $('.task-comments', host);
    taskChatIndexMount(thread);        // your messages on the feed's edge (ui/app/chat-index.ts)
    thread.addEventListener('toggle', (ev: Event) => {
      if (ev.target instanceof HTMLDetailsElement && ev.target.matches?.('details.run-steps')) void taskStepsOpen(ev.target);
      sourceGroupToggle(state.openLines, ev.target);
      if (ev.target instanceof HTMLDetailsElement && ev.target.matches('details.src-group')) state.openLines?.[ev.target.open ? 'add' : 'delete']('g:' + ev.target.dataset.srcGroup);
    }, true);
    thread.addEventListener('scroll', () => { if (thread.scrollTop < 48 && $('[data-task-earlier]', thread)) taskChatEarlier(state); }, {passive: true});
    thread.addEventListener('click', (ev: MouseEvent) => {
      if (eventElement(ev)?.closest('[data-task-earlier]')) { taskChatEarlier(state); return; }
      const stop = eventElement(ev)?.closest<HTMLButtonElement>('[data-task-stop]');
      if (stop && taskChatCurrent(state)) { void taskRunStop(state, stop); return; }
      const answer = eventElement(ev)?.closest<HTMLButtonElement>('[data-task-answer]');
      if (answer && taskChatCurrent(state)) { void taskAnswerOpen(state, answer); return; }
      // The task this one waits on, named in the last row: it opens in this window, as a related task does.
      const open = eventElement(ev)?.closest<HTMLElement>('[data-task-wait-open]');
      if (open && taskChatCurrent(state)) { ev.preventDefault(); void taskModalOpen(open.dataset.taskWaitOpen!, state.dialog); }
    });
  }
  state.data = data;
  taskYoursDotSync(state.dialog, data.task);
  taskDocsSync(state.dialog, data.task);
  taskComposerToPaint(state);
  // `can_comment`: false for someone who may read the task but not comment on it; they get no box
  const can = data.can_comment !== false, d = state.dialog;
  $('form', host).hidden = !can;
  if (d.canComment !== can) { d.canComment = can; if (d.tfEl) tfPaint(d); taskPagesActions(d); }
  // a new comment may have carried a new version of a file, or answered a question on one
  const count = (data.comments || data.messages || []).length;
  if (state.count != null && count !== state.count) { void tfLoad(state.dialog, state.id); void taskFilesTabLoad(state.dialog, state.id); }
  state.count = count;
  const files = tfFiles(state.dialog);
  // What is happening now is the chat's last row: a run at work, what the next run waits for, whose turn it is, and
  // the reader's own ask with Answer.
  const wait = taskStateHTML(state, data, sent);
  // Two or more updates in a row (who filed, numbered, moved or took the turn, the bot's finished runs and task updates)
  // are one quiet "N updates" row, shut until opened (ui/app/hub-v2.ts sourceRunsHTML). A page attached, Jev's check and
  // an answer stay lines of their own.
  const said = lines.filter(x => x.kind !== 'event' || commentLineHTML(x, 0, lines, files, state.id));
  const isUpdate = (x: ModalLine) => x.kind === 'event' ? !TASK_EVENT_MILESTONES.has(x.field) : x.kind === 'run'
    || (!!x.message && !askOf(x.message) && isSourceLine(x.message) && rowSource(x.message).kind === 'task_update');
  state.openLines ||= new Set();
  state.shown ||= TASK_CHAT_PAGE;
  const openLines = state.openLines;
  // The newest lines are drawn first; scrolling to the top (or its "earlier" line) draws older ones, back to the first.
  const older = Math.max(0, said.length - state.shown);
  const earlier = older ? `<button type="button" class="linkish task-earlier" data-task-earlier>↑ ${older} earlier message${older === 1 ? '' : 's'} and moves</button>` : '';
  const groupKey = (run: ModalLine[]) => { const x = run[0]; return 'g:' + String(!x ? '' : x.kind === 'event' ? x.id : x.kind === 'run' ? x.run.run : x.message.id); };
  const html = (earlier + sourceRunsHTML(said.slice(older), {isUpdate, min: 2, noun: 'updates',
    open: (run: ModalLine[]) => openLines.has(groupKey(run)) || run.some(x => !!x.message && openLines.has(x.message.id)),
    line: (x: ModalLine) => commentLineHTML(x, 0, lines, files, state.id), draw: (x: ModalLine) => commentLineHTML(x, 0, lines, files, state.id)}) || '<p class="muted">Nothing said yet.</p>') + wait;
  const thread = $('.task-comments', host);
  if (html !== state.rendered) {
    // At the newest comment, new ones keep it in view; scrolled up, nothing moves (ui/app/stick-scroll.ts).
    const stick = stickScroll(thread);
    if (!state.rendered) stick.toBottom();
    stick.update(() => { visualSwap(thread, html); }, {fresh: !!state.rendered});
    // A working run's steps, left open, follow it as it moves on.
    const liveSteps = $('details.run-steps[data-live][open]', thread);
    if (liveSteps) void taskStepsOpen(liveSteps);
    roadmapCommentsBind(state);
    fbSync();
    if (thread.querySelector('[data-credential-host]')) void window.credentialCards?.mount(thread, {get, post, esc, toast,
      reload: () => taskChatRead(state)});
    state.rendered = html;
  }
}
// Draws the next older page of lines above the ones shown, keeping the line that was at the top where it was.
const TASK_CHAT_PAGE = 50;
function taskChatEarlier(state: TaskChatState) {
  const thread = $('.task-comments', state.host);
  if (!thread || !state.data || state.earlier) return;
  state.earlier = true;
  const fromBottom = thread.scrollHeight - thread.scrollTop;
  state.shown = (state.shown || TASK_CHAT_PAGE) + TASK_CHAT_PAGE;
  taskCommentsRender(state, state.data);
  thread.scrollTop = thread.scrollHeight - fromBottom;
  requestAnimationFrame(() => { state.earlier = false; });
}
// The Chat tab's dot (a task with documents) follows the reader's own ask: it goes once the ask is answered
// (taskAskSettled reads the task again), and comes when a read raises one.
function taskYoursDotSync(d: ModalDialog, t: ModalTask | null | undefined) {
  const tab = $('.tw-tabs [data-task-tab="chat"]', d) as HTMLElement | null;
  if (!tab || !t || String(t.id) !== String(d.dataset.task)) return;
  const dot = $('.tw-dot', tab), yours = taskYours(t);
  if (dot && !yours) dot.remove();
  else if (!dot && yours) tab.insertAdjacentHTML('beforeend', '<span class="tw-dot" role="img" aria-label="Waiting on you"></span>');
}
// Answer (the reader's own ask, the chat's last row) opens the ask's card.
async function taskAnswerOpen(state: TaskChatState, answer: HTMLButtonElement) {
  answer.disabled = true;
  try { await decideOpenAsk(String(state.id), String(answer.dataset.taskAnswer)); } finally { answer.disabled = false; }
}
// An ask of this task was answered in the Needs you card: read the open task again so its banner goes.
function taskAskSettled(taskId: string | number): void {
  if (TASK_CHAT && taskChatCurrent(TASK_CHAT) && String(TASK_CHAT.id) === String(taskId)) void taskChatRead(TASK_CHAT);
}
async function taskChatRead(state: TaskChatState) {
  if (!taskChatCurrent(state) || state.sending || state.reading) return;
  state.reading = true;
  try {
    const data = await get<ModalDetail>(state.path);
    await taskStagesFor(data);
    if (data?.task?.conversation_id) state.cid = data.task.conversation_id;
    if (taskChatCurrent(state) && !state.sending) taskCommentsRender(state, data);
  } catch (e) {
    if (!taskChatCurrent(state)) return;
    if ([403, 404].includes(errorStatus(e) ?? 0)) {
      state.stopped = true; state.liveOff?.();
      TASK_DRAFTS.delete(String(state.id)); PROP_TASKS = null;
      if (state.dialog.taskLive) taskLiveGone(state.dialog, state.dialog.taskLive);
      else if (TASKS_ST) void tasksCatchUp(TASKS_ST);
      state.dialog.close(); state.dialog.innerHTML = '';
      return;
    }
    if (errorStatus(e) === 400) {state.stopped = true; state.liveOff?.();}
    const status = $('[data-task-chat-status]', state.host);
    if (status) status.textContent = `Unable to refresh: ${errorMessage(e, '')}`;
    else state.host.innerHTML = `<p class="err" role="status">Conversation unavailable: ${esc(errorMessage(e, ''))}</p>`;
  } finally { state.reading = false; }
}
async function taskChatLoad(id: string | number, dialog: ModalDialog, data: ModalDetail | null = null) {
  const state: TaskChatState = TASK_CHAT = {dialog, host: $('.task-chat', dialog),
    path: `/v2/tasks/${encodeURIComponent(id)}`, id, sending: false, reading: false};
  liveTasksSync();
  if (data?.task?.conversation_id) state.cid = data.task.conversation_id;
  if (data) { await taskStagesFor(data); taskCommentsRender(state, data); } else await taskChatRead(state);
  if (!taskChatCurrent(state) || state.stopped) return;
  taskJumpPending(state);
  // A change to the task itself reaches taskChatLive from the page's task events (ui/app/refresh.ts); its comments
  // are messages in its conversation.
  const cid = state.cid;
  const offs = cid ? [liveFollow(cid), liveOn('messages', (d: {conversation_id?: string}) => { if (d.conversation_id === cid) taskChatLive(state); }),
    liveOn('runs', (d: {conversation_id?: string; output?: unknown}) => {
      if (d.conversation_id !== cid) return;
      if (!d.output) { taskChatLive(state); return; }
      // Output moves the "Working" line on: the thread alone, at most every 1.5 s and not while the tab is hidden.
      liveThrottle('task-run:' + state.id, () => { if (taskChatCurrent(state) && !state.stopped) void taskChatRead(state); }, 1500);
    })] : [];
  state.liveOff = () => { offs.forEach(off => off()); state.liveOff = null; };
}
/** A bot's ❓ from Flow (decide.ts questionOpen): the task's chat, at the marked message. */
function taskChatJump(task: string, message: string) {
  if (message) TASK_JUMP = {task, id: message};
  return taskModalOpen(task, undefined, {thread: true, direct: true});
}
// A link from outside Tico (the Slack visuals line) names a message: bring it into view and mark it for a moment.
// The task opened only because the reader may read it; a message not on the page loaded says so.
function taskJumpPending(state: TaskChatState) {
  if (!TASK_JUMP || ![String(state.id), state.data?.task?.id].includes(TASK_JUMP.task)) return;
  const {id} = TASK_JUMP; TASK_JUMP = null;
  const thread = $('.task-comments', state.host), el = thread?.querySelector(`[data-message="${CSS.escape(id)}"]`);
  if (!el) { toast('That message is not in this conversation', true); return; }
  taskTabShow(state.dialog, 'chat');        // the message is in the chat, whichever tab the task opened on
  stickScroll(thread).release();           // the first paint follows the newest comment; the jump stops that
  el.scrollIntoView({block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
  el.classList.add('jump-mark');
  setTimeout(() => el.classList.remove('jump-mark'), 2000);
}
async function taskCommentSend(state: TaskChatState) {
  if (!taskChatCurrent(state) || state.sending) return;
  const form = $('form', state.host);
  const box = $('textarea', form), text = box.value.trim();
  if (!text) { box.focus(); return; }
  state.sending = true;
  const button = $('button[type=submit]', form), caret = $('[data-task-caret]', form);
  const status = $('[data-task-chat-status]', form);
  // The way Send shows is the way it goes; a task with no bot names none.
  const split = taskDeliverySplit(state), mode = split ? taskDeliveryMode(state) : '', shown = deliveryWay(mode);
  // A disabled Send says why on hover; with an aria-label, the title is its screen-reader description too.
  taskDeliveryMenu(state, false);
  button.disabled = box.disabled = true; caret.disabled = true; button.title = 'Sending…'; status.textContent = '';
  try {
    const body = {text, public: !!$('[data-comment-public]', form)?.checked, ...deliveryBody(mode)};
    const data = await post(`/v2/tasks/${encodeURIComponent(state.id)}/comments`, body);
    if (data.held) {
      // Held by an org rule: nothing was written, so the text stays in the box and the hold shows here with its actions.
      if (taskChatCurrent(state)) ruleTaskHeld(state, data.held);
      status.textContent = '';
      return;
    }
    if (shown === 'goal') state.goalOnce = false;      // Set as goal was for this comment: back to the kept way
    if (!taskChatCurrent(state)) return;
    box.value = ''; box.style.height = ''; TASK_DRAFTS.delete(String(state.id)); state.sending = false;
    const thread = $('.task-comments', state.host);
    if (thread) stickScroll(thread).toBottom();      // your comment is where you look
    // Your comment and "Sent" at once, as in the bot's chat; the read that follows has its run.
    if (data.comments && state.data) taskCommentsRender(state, {...state.data, comments: data.comments}, data.woke ? data.comment?.id : '');
    await taskChatRead(state);
    // The composer said so before sending; the server has the last word, so a different way it took is said (a defer
    // while the bot works on this task goes in queued).
    const as = data.sent_as?.delivery;
    if (as) status.textContent = shown && shown !== 'goal' && as !== shown ? `Sent as ${deliveryWord(as)}.` : '';
    else status.textContent = data.woke || data.delivered || taskComposerTo(state.data?.comment_to, text) ? '' : 'Saved. The bot reads it on its next run on this task.';
  } catch (e) {
    if (taskChatCurrent(state)) status.textContent = `Not saved: ${errorMessage(e, '')}`;
  } finally {
    state.sending = false;
    if (taskChatCurrent(state)) {
      button.disabled = box.disabled = caret.disabled = false;
      taskDeliveryPaint(state); box.focus();
    }
  }
}
// A paused bot's cause links to the page that shows it (backend/task_wakes.py CAUSE_PAGES): out of credit to the
// strip's Add credit.
const TASK_CAUSE_PAGES: Record<string, string> = {'#/settings/computers': 'Computers', '#/settings/billing': 'Billing'};
function taskCauseLinkHTML(href: string | null | undefined): string {
  if (!href || !TASK_CAUSE_PAGES[href]) return '';
  return (href === '#/settings/billing' && cloudCreditLinkHTML()) || ` <a href="${esc(href)}">${TASK_CAUSE_PAGES[href]}</a>`;
}
// The window shows one thing at a time with the full width: the task's chat; its Files (the pages and files linked to it,
// task-pages.ts); or one of its documents (QA report…, ui/app/task-docs.ts), from the tabs on the title row (Chat, Files N,
// then the documents; a Spec is a card under the title, taskSpecsHTML); or Details (what it is, what it needs to leave
// its stage and a short list of facts), from its icon in the head, which closes back to what showed before. The
// style guide's Task window; kept on the dialog across redraws.
type TaskTab = string;   // 'chat', 'files', 'details', a document's key or a page open in the window (`page:<id>`)
/** The documents the window draws: the task's own read's, else the last read's for the same task. */
const taskTabRow = (d: ModalDialog) => d.querySelector<HTMLElement>('.tw-tabs');
const taskDetailsButton = (d: ModalDialog) => d.querySelector<HTMLElement>('[data-task-tab="details"]');
function taskDocsOf(d: ModalDialog, t: ModalTask): TaskDoc[] {
  if (Array.isArray(t.documents)) d.taskDocs = t.documents;
  return d.taskDocs || [];
}
function taskTabPaint(d: ModalDialog) {
  // A document or page that is gone (archived, unreadable now), or a Spec as a document key (its card sits under the
  // title and opens it as a page), shows the chat instead.
  const gone = (key: TaskTab | undefined) => (key?.startsWith('doc:') && !taskDocOf(d, key)) || (key?.startsWith('page:') && !taskPageDocOf(d, key));
  if (gone(d.taskTab)) d.taskTab = 'chat';
  if (gone(d.taskTabBack)) d.taskTabBack = 'chat';
  const tab = d.taskTab || 'chat', body = $('.task-layout', d), doc = taskDocOf(d, tab) || taskPageDocOf(d, tab);
  if (body) body.dataset.taskTabShown = doc ? 'doc' : tab;
  // The tab lit: under Details or an open page, the tab it came from; a Spec's page lights its card, no tab.
  const under = tab === 'details' ? d.taskTabBack || 'chat' : tab, page = under.startsWith('page:') ? under.slice(5) : null;
  const lit = page ? d.taskPageFrom || 'chat' : under;
  const row = taskTabRow(d);
  if (row) docTabsPaint(row, lit, b => b.dataset.taskTab, tab !== 'details' && !(page && lit === 'chat'));   // Details covers them: none selected
  for (const card of d.querySelectorAll<HTMLElement>('[data-task-spec]')) card.setAttribute('aria-current', String(tab === 'page:' + card.dataset.taskSpec));
  taskDetailsButton(d)?.setAttribute('aria-pressed', String(tab === 'details'));
  const pane = $('.task-doc', d) as HTMLElement | null;
  if (pane && doc) docViewMount(pane, doc);
  $('[data-task-activity]', d)?.classList.toggle('open', !!d.taskActivity);
}
// Shows a tab. `picked`: the person chose it (a click, a key, a menu item), so the window no longer picks one for them.
function taskTabShow(d: ModalDialog, tab: TaskTab, picked = true) {
  if (picked) d.taskTabPicked = true;
  const was = d.taskTab;
  if (tab === 'details' && was !== 'details') d.taskTabBack = was || 'chat';
  if (tab !== 'details') d.taskTabBack = tab;
  d.taskTab = tab; taskTabPaint(d);
  if (tab === 'chat' && was !== 'chat' && TASK_CHAT?.dialog === d) {
    const thread = $('.task-comments', TASK_CHAT.host);
    if (thread) stickScroll(thread).toBottom();
  }
}
function taskTabsBind(d: ModalDialog) {
  taskTabPaint(d);
  const row = taskTabRow(d);
  if (row) {
    row.onclick = (ev: MouseEvent) => { const b = eventElement(ev)?.closest<HTMLElement>('[data-task-tab]'); if (b) taskTabShow(d, b.dataset.taskTab || 'chat'); };
    docTabsKeys(row, b => taskTabShow(d, b.dataset.taskTab || 'chat'));
  }
  // Details opens over what shows, and closes back to it.
  const details = taskDetailsButton(d);
  if (details) details.onclick = () => taskTabShow(d, d.taskTab === 'details' ? d.taskTabBack || 'chat' : 'details');
}
// Spec pages have cards under the title. Every other document gets a tab after Files.
// The documents with a tab (a Spec page is a card instead), in the order they first appeared (docsInPlace).
const taskTabDocs = (docs: TaskDoc[] | null | undefined): TaskDoc[] => docsInPlace((docs || []).filter(d => d.name !== 'Spec' || d.kind === 'markdown'));
const taskDocOf = (d: ModalDialog, key: string | null | undefined): TaskDoc | null => docOf(taskTabDocs(d.taskDocs), key);
/** A page open in the window (`page:<id>`): one of the task's Spec pages, else a page row of its Files tab. */
function taskPageDocOf(d: ModalDialog, key: string | null | undefined): TaskDoc | null {
  if (!key?.startsWith('page:')) return null;
  const id = key.slice(5);
  return (d.taskDocs || []).find(x => x.name === 'Spec' && x.kind !== 'markdown' && (x.page_id || x.file_id) === id) || TFT_PAGES.get(id) || null;
}
/** The tabs on the title row, and the change of a document as the task is read again: a new tab, a new version, one gone. */
function taskDocTabsHTML(t: ModalTask, docs: TaskDoc[] | null | undefined, tab: TaskTab | undefined, place = '') {
  const on = tab === 'details' || !tab ? 'chat' : tab, shown = taskTabDocs(docs);
  const tabs: DocTab[] = [{key: 'chat', label: 'Chat', dot: taskYours(t)}, {key: TASK_FILES_TAB, label: taskFilesLabel(TFT_COUNT.get(String(t.id)))},
    ...shown.map(x => docTab(x, shown))];
  const valid = tabs.some(x => x.key === on);
  return docTabsHTML(tabs, valid ? on : 'chat', key => `data-task-tab="${esc(key)}"`, 'Task', place).replace('class="doc-tabs"', 'class="doc-tabs tw-tabs"');
}
function taskDocsSync(d: ModalDialog, t: ModalTask | null | undefined) {
  if (!t || String(t.id) !== String(d.dataset.task) || !Array.isArray(t.documents) || d.classList.contains('thread')) return;
  const was = JSON.stringify(d.taskDocs || []);
  d.taskDocs = t.documents;
  if (JSON.stringify(t.documents) === was) return;
  const row = taskTabRow(d), html = taskDocTabsHTML(t, t.documents, d.taskTab, docTabsPlace(row, b => b.dataset.taskTab));
  if (row) row.outerHTML = html;
  else $('.tw-head .tmodal-title', d)?.insertAdjacentHTML('afterend', html);
  // the Spec cards under the title
  const specs = taskSpecsHTML(t.documents, String(t.id), TFT_TITLES), card = d.querySelector<HTMLElement>('[data-task-specs]');
  if (card) { if (specs) card.outerHTML = specs; else card.remove(); }
  else if (specs) d.querySelector<HTMLElement>('.tmodal-head')?.insertAdjacentHTML('afterend', specs);
  taskFilesPaint(d as unknown as TftHost, String(t.id));   // a page that became a document leaves the Files list
  taskTabsBind(d);
}
// The tab a task opens on, once its conversation is read: Chat, where the box is and what is happening now. A tab the
// person picked, a thread view and a link to a message are left alone.
function taskTabFirst(d: ModalDialog, _detail: ModalDetail) {
  if (d.taskTabPicked || d.classList.contains('thread')) return;
  d.taskTabPicked = true;
  if (d.taskTab !== 'chat') taskTabShow(d, 'chat', false);
}
// Activity and links: Links, the public roadmap's text, Sessions, Runs, Related and Meetings, at the foot of Details. Only
// the ones with something in them show (task-code.css); ⋯ › Activity and links opens it with the empty ones too, to add
// a link or a session.
function taskActivityOpen(d: ModalDialog, focus = '.rail-h') {
  d.taskActivity = true; taskTabShow(d, 'details');
  const activity = $('[data-task-activity]', d);
  if (!activity) return;
  activity.scrollIntoView({block: 'start'});
  const el = $(focus, activity) as HTMLElement | null;
  if (el && !el.matches('input, button, a')) el.setAttribute('tabindex', '-1');
  el?.focus({preventScroll: true});
}
/** Where opening puts the focus, with no ring: Chrome draws one on script focus when nothing was clicked first (a link,
    a reload). data-quiet-focus keeps it off (forms.css) until the focus moves; a redraw keeps it quiet. */
// Focus without a ring (focusVisible is newer than the DOM types) and without scrolling.
const TASK_QUIET_FOCUS = {preventScroll: true, focusVisible: false} as FocusOptions;
function taskQuietFocus(el: HTMLElement | null) {
  if (!el) return;
  el.dataset.quietFocus = '';
  el.addEventListener('blur', () => { delete el.dataset.quietFocus; }, {once: true});
  el.focus(TASK_QUIET_FOCUS);
}
// Whether the task waits on the reader's own answer (its turn is the reader's ask).
const taskYours = (t: ModalTask) => { const turn = !taskFinished(t) ? t.turn : null, me = myActor(); return !!turn?.ask && !!turn.requirement_id && !!me && turn.who === me && !!turn.line; };
// What still blocks the stage: the count Details carries.
const taskBlocking = (t: ModalTask) => t.status === 'open' ? (t.to_leave || []).filter(r => !r.met).length : 0;
// The head is one row: the title top left, then the actions: Snoozed (a button that wakes it), 👍 👎, the age (its
// tooltip says when it was opened and updated), Done for its owner, Archive (or the way back for a finished task), ⋯,
// then ✕ after a thin line. A phone keeps Archive, ⋯ and ✕; 👍 👎 and the age move into ⋯ (task-code.css).
function taskHeadHTML(t: ModalTask, thread: boolean, docs: TaskDoc[] = [], tab?: TaskTab | undefined) {
  const finished = taskFinished(t), me = myActor(), rights = taskPropRights(t), snooze = t.snooze || null;
  const canDone = !finished && !!me && me === t.owner_id;
  const canArchive = t.status === 'open' && (rights.mover || rights.party);
  const ic = (name: string) => `<span class="nav-icon" aria-hidden="true">${name}</span>`;
  const act = finished && rights.reopen
    ? `<button class="ghost peek-btn tw-ic" type="button" data-task-reopen aria-haspopup="menu" aria-label="Reopen" title="Reopen">${ic('unarchive')}</button>`
    : canArchive ? `<button class="ghost peek-btn tw-ic" type="button" data-task-finish="close" aria-label="Archive" title="Archive">${ic('archive')}</button>` : '';
  const snoozed = taskSnoozed(t)
    ? `<button type="button" class="tl-due wake tchip-snoozed" data-task-unsnooze title="${esc(`Snoozed${snooze?.until ? ` until ${timeSaid(snooze.until)}` : ''}${snooze?.by ? ` by ${actorLabel(snooze.by)}` : ''}${snooze?.why ? ` · ${snooze.why}` : ''}`)}${rights.snooze ? '. Click to bring it back now' : ''}"${rights.snooze ? '' : ' disabled'}>${snoozeDay(t) ? `Snoozed to ${esc(snoozeDay(t))}` : 'Snoozed'}${chipNoteHTML(snooze?.why)}</button>` : '';
  return `<div class="tmodal-head tw-head">${thread ? `<button class="ghost peek-btn tthread-only tthread-back" type="button" data-thread-back aria-label="Back to chat" title="Back to chat"><svg viewBox="0 0 12 12" width="14" height="14" aria-hidden="true" focusable="false"><path d="M7.5 3l-3 3 3 3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></button>` : ''}
    <h2 class="tmodal-title${thread ? ' tthread-title' : ''}" tabindex="-1">${esc(t.title)}</h2>${thread ? '' : taskDocTabsHTML(t, docs, tab)}
    <span class="tw-acts">${snoozed}<span class="tw-quiet">${fbButtonsHTML(fbTaskKey(t))}<span class="muted tnum tmodal-age" title="${esc(`${taskSourceLine(t)}${t.created_at ? ' ' + fmt(t.created_at) : ''} · Updated ${timeSaid(t.updated_at)}`)}">${esc(ago(t.updated_at || t.created_at))}</span></span>
      ${canDone ? `<button class="ghost peek-btn tw-ic tw-done" type="button" data-task-finish="done" aria-label="Done" title="Mark done">${ic('task_alt')}</button>` : ''}${act}
      ${thread ? '<button class="ghost tmodal-act tthread-only" type="button" data-thread-details>Details</button>' : ''}${thread ? '' : taskDetailsButtonHTML(t)}
      <button class="ghost peek-btn tw-ic" type="button" data-task-more aria-haspopup="menu" aria-label="More actions" title="More actions">${ic('more_horiz')}</button>
      <span class="tmodal-sep" aria-hidden="true"></span><button class="ghost peek-btn tw-ic tmodal-x" type="button" data-modal-close aria-label="Close window" title="Close window (Esc)">${ic('close')}</button></span></div>`;
}
// Details: an icon in the head, pressed while Details shows, with what still blocks the stage beside it.
function taskDetailsButtonHTML(t: ModalTask) {
  const blocking = taskBlocking(t);
  return `<button class="ghost peek-btn tw-ic tw-details" type="button" data-task-tab="details" aria-controls="task-pane-details" aria-pressed="false" aria-label="Details${blocking ? `, ${blocking} to leave the stage` : ''}" title="Details">`
    + `<span class="nav-icon" aria-hidden="true">fact_check</span>${blocking ? `<span class="tw-count tnum" data-task-blocking aria-hidden="true">${blocking}</span>` : ''}</button>`;
}
// The task in full: the head, a quiet line only while someone else is here or an edit did not save, then the panes:
// the chat, the Files tab, the document open, and Details (what the task is first). What is happening now (the turn, Answer, a deferred hand-over, a working run) is the chat's last row
// (taskCommentsRender), not a line under the title.
function hubModalHTML(t: ModalTask, opts: {thread?: boolean; docs?: TaskDoc[]; tab?: TaskTab | undefined} = {}) {
  const owner = String(t.owner_id || ''), note = String(t.note || '').trim();
  const links = (t.links || []).filter(l => !['pr', 'worktree', 'session'].includes(l.kind));   // Code is a fact; Sessions below
  const mover = canMove();
  // The owner's latest note, when it says more than the turn line at the end of the chat already does.
  const latest = note && note.replace(/\s+/g, ' ') !== clipLine(t.turn?.line, 400)
    ? `<section class="tw-part task-note"><h3 class="tw-h">${esc(owner ? `Latest from ${actorLabel(owner)}` : 'Latest note')}</h3><div class="tdesc md">${safeMd(note)}</div></section>` : '';
  const criteria = (t.acceptance_criteria || []).length
    ? `<section class="tw-part"><h3 class="tw-h">Done looks like</h3><ul class="tcriteria">${t.acceptance_criteria!.map(a => `<li>${esc(a)}</li>`).join('')}</ul></section>` : '';
  return `${taskHeadHTML(t, !!opts.thread, opts.docs, opts.tab)}${opts.thread ? '' : taskSpecsHTML(opts.docs, String(t.id), TFT_TITLES)}
    <div class="task-edit-meta muted" role="status"><span data-task-presence></span><span data-task-edit-error hidden>Not saved · <button type="button" class="linkish" data-task-edit-retry>Retry</button></span></div>
    <div class="muted task-edit-current" data-task-edit-current hidden></div>
    <section class="rule-holds" data-rule-holds aria-label="Held by a rule" hidden></section>
    <div data-task-files></div>
    <div class="tmodal-body task-layout" data-task-tab-shown="chat">
      <section class="task-chat" id="task-pane-chat" role="tabpanel" aria-label="Chat"><p class="muted" role="status">Loading the conversation…</p></section>
      <section class="task-doc" role="tabpanel" aria-label="Document"></section>
      <section class="task-files-tab" id="task-pane-files" role="tabpanel" aria-label="Files">
        <div class="tft-list" data-tft-list>${TFT_ROWS.get(String(t.id)) || ''}</div>
        <section class="task-pages" data-task-pages aria-label="Link a page or file" hidden></section>
      </section>
      <aside class="task-side" id="task-pane-details" role="region" aria-label="Details">
        ${criteria || latest ? `<section class="task-desc">${criteria}${latest}</section>` : ''}
        ${githubNudgeTaskHostHTML(t)}
        ${taskRequirementsHTML(t)}
        <section class="task-props" data-task-props aria-label="Facts">${taskPropsHTML(t, {runsOn: taskRunModelRowHTML(t), goal: t.goal_id || ''})}</section>
        <div class="task-rail" data-task-rail hidden></div>
        <section class="task-activity" data-task-activity aria-label="Activity and links"><h3 class="tw-h rail-h">Activity and links</h3>
          <section class="tsec task-links${links.length ? '' : ' tsec-empty'}"><h3 class="rail-h">Links<button type="button" class="prop-add" data-link-add aria-label="Add a link" title="Add a link">${TL_ICON.plus}</button></h3>
            ${links.length ? `<div class="tlinks">${links.map(l => `<span class="tlink ${esc(l.kind)} ${esc(l.state || '')}"><a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title || l.url)}</a>${l.state && l.state !== 'open' ? ` · ${esc(l.state)}` : ''}${mover ? `<button type="button" class="x tap-mark" data-drop-link="${esc(l.id)}" aria-label="Remove link">×</button>` : ''}</span>`).join('')}</div>` : ''}
            <form class="inline task-link-add" data-modal-link hidden><input type="url" inputmode="url" autocomplete="off" spellcheck="false" placeholder="https://…" aria-label="Add a link"></form></section>
          ${roadmapTaskHTML(t)}
          ${taskSessionsHTML(t)}
          <section class="tsec task-runs" data-task-runs aria-label="Runs" hidden></section>
          ${t.relations ? taskRelHTML(t) : ''}
          <section class="tsec task-meetings" data-task-meetings aria-label="Meetings" hidden></section>
        </section>
      </aside>
    </div>`;
}
