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
interface ModalRunPick { model: string; harness: string; effort: string; compact: number | null; }
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
  /** The phone tab shown (Chat, Pages or Info) and whether ⋯ › Activity and links is open, kept across redraws. */
  taskTab?: TaskTab;
  taskActivity?: boolean;
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
// The run answering the thread (`execution`): the bot's chat's pending run, with whose it is and its steps so far.
interface ModalExecution extends ChatExecution { bot?: string; from_person?: boolean; parts?: {text: string}[]; }
// A change in the task's history, a comment, and one line of the conversation (either).
interface ModalEvent { id?: string; created_at: string; field: string; old?: string | null; new?: string | null; note?: string | null; actor?: string; via?: string; }
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
    if (match && tfOpen(d, decodeURIComponent(match[1]!), url.searchParams.get('v'), {scroll: true})) ev.preventDefault();
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
    d.taskTab = 'chat'; d.taskActivity = false;      // a task opens on its chat, Activity and links shut
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
  if (d.open && String(d.dataset.task) === String(task.id) && $('.prop-pop', d)?.matches(':popover-open')) {
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
  d.innerHTML = hubModalHTML(task, {thread: d.classList.contains('thread')});
  adopt(); taskFormAdopt(d, keptForm);
  taskModalBind(d, task);
  taskPropFocus(d); taskLiveFocusRestore(d); taskFormRestore(d, keptForm);
  if (!d.open) {
    const was = document.activeElement as HTMLElement | SVGElement | null;
    const address = '#/task/' + encodeURIComponent(String(task.id));
    d.address = location.hash === address ? address : '';
    if (!TASKS_ST) {
      // Outside Tasks, Back dismisses the detail without rebuilding the bot's chat, tab or scroll position.
      d.returnContext = {route: location.hash, task: String(task.id), opener: was};
      history.pushState({...(history.state || {}), taskModal: 1}, '');
    }
    d.showModal();
    // Opening lands on the title, not on the first header button (a focus ring on "…" read as a stray highlight).
    if (d.open && d.matches(':modal')) $(d.classList.contains('thread') ? '.tthread-title' : '.tmodal-title', d)?.focus({preventScroll: true});
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
    : a.matches('[data-thread-back]') ? '[data-thread-back]' : a.matches('.tthread-title') ? '.tthread-title'
    : a instanceof HTMLElement && a.dataset.prop ? `[data-prop="${CSS.escape(a.dataset.prop)}"]` : '';
  taskModalRunsKeep(d, kept);
  taskLiveFocusKeep(d);
  const fullForm = taskFormCapture(d);
  d.innerHTML = hubModalHTML(full, {thread: d.classList.contains('thread')});
  adopt(); taskFormAdopt(d, fullForm);
  taskModalBind(d, full, true);
  if (keep) $(keep, d)?.focus({preventScroll: true});
  taskPropFocus(d); d.focusProp = ''; taskLiveFocusRestore(d); taskFormRestore(d, fullForm);
  taskLiveEdited(d, detail); taskLiveStart(d, after); d.scrollTop = scroll;
  void taskRailLoad(d, full, detail);
  if (kept && TASK_CHAT === kept && d.contains(kept.host)) taskCommentsRender(kept, detail);
  else void taskChatLoad(task.id, d, detail);
  void tfLoad(d, task.id);
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
  if (details) details.onclick = () => { d.classList.remove('thread'); void taskModalShow(d.liveTask || task, d); };
  void taskGoalTitle(d);
  // Each change goes through the dialog's queue (taskSave), one at a time, each with the latest version.
  const change = (body: PropBody, then?: boolean, field?: string) => taskSaveQueued(d, String(task.id), body, then, field);
  // Done and Close finish with the task: the window closes and the page behind it is where the person was.
  d.querySelectorAll<HTMLButtonElement>('[data-task-finish]').forEach(b => b.onclick = async () => {
    b.disabled = true;
    const saved = await change(b.dataset.taskFinish === 'close' ? {close: true} : {done: true}, false, 'stage');
    if (saved && d.open && String(d.dataset.task) === String(task.id)) dismiss();
    else b.disabled = false;
  });
  const unsnooze = $('[data-task-unsnooze]', d);
  if (unsnooze) unsnooze.onclick = () => change({unsnooze: true}, undefined, 'snooze');
  taskAnswerBind(d, task);
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
  const copy = (value: string, what: string) => `<button type="button" class="tcopy" data-copy-session="${esc(value)}" aria-label="Copy ${esc(what)}" title="Copy ${esc(what)}">${SESSION_COPY}</button>`;
  const drop = myActor() ? (l: ModalLink) => `<button type="button" class="x" data-drop-link="${esc(l.id)}" aria-label="Remove session">×</button>` : () => '';
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

// Runs on (POST /v2/tasks/{id}/model, backend/task_run_models.py): the model this task's runs use, or the bot's task model
// until one is set on it. The model list is GET /v2/models, read once here and by the automation step editor, which offers
// the same models. Gateway tier picks (tico-*) are made in the bot's settings, so they stay out of both.
let TASK_RUN_ROWS: TaskRunRow[] | null = null, TASK_RUN_LOADING: Promise<ModalModelRow[]> | null = null;
function taskRunCatalog() {
  TASK_RUN_LOADING ||= get<{models?: ModalModelRow[]}>('/v2/models').then(d => {
    TASK_RUN_ROWS = (d.models || []).filter(m => !m.deprecated && !m.meeting && !/^tico-/.test(m.id || ''));
    return TASK_RUN_ROWS;
  }, () => { TASK_RUN_LOADING = null; return []; });
  return TASK_RUN_LOADING;
}
const taskRunOpts = (list: [string, string][], val: string) => list.map(([k, label]) => `<option value="${esc(k)}"${k === val ? ' selected' : ''}>${esc(label)}</option>`).join('');
// The choice a form starts on: the task's own model, harness, effort and window, or none (the bot's task model).
const taskRunPick = (run: ModalRunModel | null | undefined): ModalRunPick => ({model: run?.model || '', harness: run?.harness || '',
  effort: run?.reasoning_effort || '', compact: run?.compact_at || null});
function taskRunWords(run: ModalRunModel | null | undefined, rows: ModalModelRow[] | null) {
  const compact = run?.compact_at ? `compacts at ${compactWords(run.compact_at)}` : '';
  if (!run?.model) return ["The bot's task model", compact].filter(Boolean).join(' · ');
  const row = (rows || []).find(m => m.id === run.model);
  return [row?.label || run.model, (row?.harnesses || []).length > 1 ? harnessWords(run.harness) : '', run.reasoning_effort,
    modelContextWords(row?.context_tokens), compact].filter(Boolean).join(' · ');
}
// Only whoever the server says may set the task's model gets the form (`can_set_run_model`, the answer POST
// /v2/tasks/{id}/model gives); anyone else reads what it runs on.
const taskRunMay = (t: ModalTask) => t.can_set_run_model === true;
function taskRunModelBody(run: ModalRunModel | null | undefined, rows: ModalModelRow[] | null, pick: ModalRunPick, may = true) {
  const head = '<h3 class="rail-h">Runs on</h3>', now = `<p class="task-run-now">${esc(taskRunWords(run, rows))}</p>`;
  if (!may) return head + now;
  if (!rows) return `${head}${now}<p class="muted">Loading the models…</p>`;
  const row = rows.find(m => m.id === pick.model), harnesses = (row?.harnesses || []).length > 1 ? row!.harnesses! : [];
  return `${head}${now}<form class="task-run-form" data-run-model-form>
    <label><span>Model</span><select data-run-model>${taskRunOpts([['', "The bot's task model"], ...rows.map((m): [string, string] => [m.id, [m.label || m.id, modelContextWords(m.context_tokens)].filter(Boolean).join(' · ')])], pick.model)}</select></label>
    ${harnesses.length ? `<label><span>Harness</span><select data-run-harness>${taskRunOpts(harnesses.map((h): [string, string] => [h, harnessWords(h) || h]), pick.harness)}</select></label>` : ''}
    ${row?.efforts?.length ? `<label><span>Effort</span><select data-run-effort>${taskRunOpts(row.efforts.map((e): [string, string] => [e, e]), pick.effort)}</select></label>` : ''}
    ${compactsOn(pick.harness || row?.harnesses?.[0]) ? `<label><span>Compact</span><select data-run-compact>${compactOptions(pick.compact, row?.context_tokens)}</select></label>` : ''}
    <button type="submit" class="ghost" data-run-model-apply disabled>Apply</button></form>`;
}
// Shown on a bot's task once GET /v2/tasks/{id} has answered (list rows leave out run_model).
function taskRunModelHTML(t: ModalTask) {
  if (!String(t.owner_id || '').startsWith('bot:') || t.run_model === undefined) return '';
  return `<section class="tsec task-run-model" data-task-run-model aria-label="Runs on">${taskRunModelBody(t.run_model, TASK_RUN_ROWS, taskRunPick(t.run_model), taskRunMay(t))}</section>`;
}
// A model change redraws the harness and effort it offers, starting on that model's own (or the task's saved ones when
// it is the model already saved). Apply saves what is picked; a blank model clears the task's own choice.
function taskRunModelBind(d: ModalDialog, task: ModalTask) {
  const sec = $('[data-task-run-model]', d);
  if (!sec) return;
  const id = String(task.id), run = task.run_model || null, state = {pick: taskRunPick(run)}, may = taskRunMay(task);
  const same = () => state.pick.model === (run?.model || '') && (state.pick.compact || null) === (run?.compact_at || null)
    && (!state.pick.model || (state.pick.harness === (run?.harness || '') && state.pick.effort === (run?.reasoning_effort || '')));
  const paint = () => {
    sec.innerHTML = taskRunModelBody(run, TASK_RUN_ROWS, state.pick, may);
    const apply = sec.querySelector('[data-run-model-apply]'); if (apply) apply.disabled = same();
  };
  if (!TASK_RUN_ROWS) void taskRunCatalog().then(() => { if (sec.isConnected && TASK_RUN_ROWS) paint(); });
  sec.onchange = (ev: Event) => {
    const t = ev.target;
    if (!(t instanceof HTMLSelectElement)) return;
    if (t.matches('[data-run-model]')) {
      const row = (TASK_RUN_ROWS || []).find(m => m.id === t.value);
      // The window picked stays, lowered to the largest size the new model holds (spec scope 8).
      const compact = state.pick.compact, window = row?.context_tokens || 0;
      const fit = compact && window && compact > window ? (COMPACT_SIZES.filter(([size]) => size <= window).pop()?.[0] || null) : compact;
      state.pick = !row ? {...taskRunPick(null), compact} : row.id === run?.model ? taskRunPick(run)
        : {model: row.id, harness: row.harnesses?.[0] || '', effort: row.default_effort || row.efforts?.[0] || '', compact: fit};
      paint(); sec.querySelector('[data-run-model]')?.focus();
      return;
    }
    if (t.matches('[data-run-harness]')) state.pick.harness = t.value;
    if (t.matches('[data-run-effort]')) state.pick.effort = t.value;
    if (t.matches('[data-run-compact]')) state.pick.compact = Number(t.value) || null;
    sec.querySelector('[data-run-model-apply]').disabled = same();
  };
  sec.onsubmit = async (ev: SubmitEvent) => {
    ev.preventDefault();
    const apply = sec.querySelector('[data-run-model-apply]');
    if (!apply || apply.disabled) return;
    apply.disabled = true;
    const {model, harness, effort, compact} = state.pick;
    // No model keeps the bot's; a window alone is still the task's own ("Same as bot 150K"), and null is the bot's.
    const body = {...(model ? Object.fromEntries(Object.entries({model, harness, effort}).filter(([, v]) => v)) : {model: null}),
      compact_at: compact};
    try {
      const saved = await post(`/v2/tasks/${encodeURIComponent(id)}/model`, body);
      if (saved.warning) toast(saved.warning);
      const data = await get(`/v2/tasks/${encodeURIComponent(id)}`);
      if (TASKS_ST) void tasksCatchUp(TASKS_ST);
      if (d.open && String(d.dataset.task) === id && data.task) void taskModalShow(data.task, d);
    } catch (e) { toast(errorMessage(e, ''), true); apply.disabled = same(); }
  };
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
    const where = [k.status === 'open' ? k.stage || '' : k.status, k.since ? `since ${shortDate(k.since)}` : ''].filter(Boolean).join(' · ');
    return `<span class="tlink task"><button type="button" class="linkish" data-open-task="${esc(k.id)}" aria-label="Open ${esc(k.title)}">${esc(ref)} ${esc(clipLine(k.title, 60))}</button>`
      + `${where ? ` · <span class="tchip-note">${esc(where)}</span>` : ''}`
      + `<button type="button" class="x" data-drop-rel="depends_on ${dir} ${esc(k.id)}" aria-label="Remove ${esc(label)}: ${esc(k.title)}">×</button></span>`;
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
    ${groups.map(([kind, dir, label, rows]) => `<div class="trel"><span class="trel-k">${esc(label)}</span><div class="tlinks">${rows.map(k => `<span class="tlink task"><button type="button" class="linkish" data-open-task="${esc(k.id)}" aria-label="Open ${esc(k.title)}">${taskStatusText(k)} ${esc(clipLine(k.title, 70))}</button><button type="button" class="x" data-drop-rel="${esc(kind)} ${esc(dir)} ${esc(k.id)}" aria-label="Remove ${esc(label.toLowerCase())}: ${esc(k.title)}">×</button></span>`).join('')}</div></div>`).join('')}${milestones}</section>`;
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
// One save, by what it asks (task-api.ts): a move ({move: to, why?}, {close}, {done}), a new owner, a snooze, or the
// task's own fields. `quiet` with a move: a refusal for what is missing is answered (the caller asks why) instead of shown.
async function taskSaveSend(cur: ModalTask, body: PropPatch, note: string): Promise<TaskV3> {
  const id = String(cur.id), version = cur.version, why = typeof body.why === 'string' ? body.why : undefined;
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
    if (body.close) toast('Closed', false, {label: 'Undo', run: () => taskMove(id, 'reopen', task.version).then(r => { if (here()) void taskModalShow(r.task, d); }, e => toast(errorMessage(e, ''), true))});
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
  dialog: ModalDialog; host: HTMLElement; path: string; id: string | number; poll: number; sending: boolean; reading: boolean;
  liveOff?: (() => void) | null; cid?: string; stopped?: boolean; data?: ModalDetail; rendered?: string; stopping?: string | undefined;
  reach?: ComposeReach; goalOnce?: boolean; openLines?: Set<string>; count?: number;
  /** How many of the newest lines are drawn (taskChatEarlier adds a page), and whether a page is being added. */
  shown?: number; earlier?: boolean;
}
let TASK_CHAT: TaskChatState | null = null;
let TASK_JUMP = ENTRY_JUMP?.task ? {task: ENTRY_JUMP.task, id: ENTRY_JUMP.id} : null;   // {task, id}: a link's message, once that task's conversation shows
const TASK_DRAFTS = new Map<string, string>();         // an unsent comment, per task, kept across redraws until it is sent or cleared
function taskChatStop() {
  if (TASK_CHAT) { clearInterval(TASK_CHAT.poll); TASK_CHAT.liveOff?.(); }
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
  }, 200);
}
function taskChatCurrent(state: TaskChatState) { return TASK_CHAT === state && state.dialog.open; }
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
// The pipelines' stage names a task's history names its moves by, read once when it has a move and the Tasks page has not read them.
const taskStagesFor = async (data: ModalDetail | null | undefined) => {
  if (!TASK_PIPELINES && data?.events?.some(e => e.field === 'move')) await tasksPipelinesRead().catch(() => []);
};
// Jev's verdict on a page, and an answer to an ask (ok, a send-back, done), in words.
const JEV_WORDS: Record<string, string> = {met: 'met', not_met: 'not met', unsure: 'unsure'};
const ANSWER_WORDS: Record<string, string> = {ok: 'gave the OK', no: 'sent it back', done: 'said done', yes: 'answered yes'};
// A move (backend/task_engine.py) names the stage it went to; a reassign who has it now.
const TASK_EVENT_WORDS: Record<string, (v: string | null | undefined, x: ModalEvent) => string> = {move: id => `moved it to ${taskStageWord(id)}`, reassign: v => `handed it to ${actorLabel(v)}`, status: s => `moved it to ${taskStatusLabel({status: s ?? null})}`, owner: v => `handed it to ${actorLabel(v)}`,
  lane: v => `moved it to the ${v === 'company' ? 'team' : v} lane`, labels: v => { try { const l = JSON.parse(v || '[]'); return l.length ? `set the tags: ${l.join(', ')}` : 'removed the tags'; } catch { return 'changed the tags'; } },
  blocked_by: v => v ? 'marked it blocked' : 'cleared the block', parent_id: v => v ? 'filed it under a parent task' : 'took it out of its parent',
  subtask: v => v ? 'added a subtask' : 'moved a subtask out', blocks: v => v ? 'made it block another task' : 'stopped it blocking a task',
  related: v => v ? 'attached a related task' : 'removed a related task', duplicate_of: v => v ? 'marked it a duplicate' : 'unmarked it as a duplicate',
  duplicated_by: v => v ? 'marked a duplicate of it' : 'unmarked a duplicate of it', follow_up_of: v => v ? 'marked it a follow-up' : 'unmarked it as a follow-up',
  follow_ups: v => v ? 'added a follow-up' : 'removed a follow-up', depends_on: v => v ? 'made it wait for another task' : 'took off a task it waited for',
  reader: (v, x) => v ? `let ${actorLabel(v)} see this task` : `stopped ${actorLabel(x.old)} seeing this task`,
  link: v => v ? `linked ${v}` : 'removed a link', file: (v, x) => v ? `added ${v}` : `removed ${x.old || 'a file'}`, due: v => v ? `set the due date to ${fmt(v)}` : 'cleared the due date',
  lint: v => `noted: ${v}`, rank: () => 'moved it in the queue', children: v => String(v || ''), note: v => `noted: ${clipLine(String(v || ''), 200)}`, comment: () => 'deleted a comment',
  // The rest of the history the server writes (backend/task_engine.py and its neighbours), so no line reads
  // "changed <field>". A page attached carries its chip (taskEventChipHTML).
  page_attached: (_v, x) => x.old ? 'attached a new version of a page' : 'attached a page',
  jev_verdict: v => `Jev's check: ${JEV_WORDS[String(v)] || String(v || '')}`,
  answer: v => ANSWER_WORDS[String(v)] || `answered: ${clipLine(String(v || ''), 80)}`,
  requirement: () => 'asked for an answer', requirement_withdrawn: () => 'withdrew an ask',
  snooze: v => v ? `snoozed it to ${new Date(String(v)).toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'})}` : 'snoozed it',
  unsnooze: () => 'woke it', snooze_refused: () => 'was refused a snooze', woken_again: () => 'woke it again', late: () => 'marked it late',
  handoff_filed: () => 'filed a hand-off task', pick: v => `gave the turn to ${actorLabel(v)}`, give: v => `gave it to ${actorLabel(v)}`,
  github: v => `GitHub: ${v || ''}`, number: v => `numbered it #${v}`, pr_queued: v => `queued ${v} to merge`, pr_dequeued: () => 'took it out of the merge queue',
  pr_queue_removed: v => `dropped ${v || 'it'} from the merge queue`, merge_queue_failed: () => 'the merge queue sent it back',
  session: () => 'started a new session', task_model: v => `ran on ${v}`, before: () => 'set the order', after: () => 'set the order', work: () => 'stopped its work',
  title: () => 'renamed it', body: () => 'edited the request', goal_id: v => v ? 'tied it to a goal' : 'took it off its goal',
  ladder: v => `asked the ${v || 'next person'} instead`, requester: v => v ? `made ${actorLabel(v)} a requester` : 'removed a requester',
  stage_rule: () => 'applied a stage rule', readers: () => 'changed who can see it',
  private: v => ['1', 'true'].includes(String(v)) ? 'made it private' : 'made it visible to the team', from_private: () => 'filed it from a private task',
  visibility: v => v === 'public' ? 'put it on the public roadmap' : 'took it off the public roadmap', size: () => 'set its size',
  type: v => `made it a ${v || 'task'}`, step: v => `moved it to ${v || 'a step'}`};
// The history lines that always show on their own: moves, hand-overs, snoozes, pages, Jev's checks, answers and asks,
// subtasks. Every other change (tags, due, followers, visibility, GitHub, the queue) folds when three or more come in a
// row (taskCommentsRender).
const TASK_EVENT_MILESTONES = new Set(['move', 'status', 'reassign', 'owner', 'give', 'pick', 'page_attached', 'jev_verdict', 'answer', 'requirement',
  'requirement_withdrawn', 'snooze', 'unsnooze', 'subtask', 'handoff_filed', 'merge_queue_failed', 'comment']);
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
    const src = rowSource(x, x.actor), who = commentAuthor(x.actor, x.via, src);
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
  const src = rowSource(m);
  // a question's text is in its block; a body that only repeats it is left out
  const body = ask && ask.questions!.some(q => String(q.question || '').trim() === String(m.body || '').trim()) ? '' : m.body || '';
  const kind = m.kind === 'ask' ? '<span class="pill needs">question</span>' : m.kind === 'answer' ? '<span class="pill">answer</span>' : '';
  // As in a bot's chat with several people: a person's line is a bubble on the right under their name (yours needs
  // none), the bot's reads as page text under its own.
  const person = !!actorPerson(m.sender_id), me = m.sender_id === myActor();
  // A bot's comment takes a row of Copy, 👍 and 👎 under it, as in a chat (ui/app/feedback.ts).
  const fb = m.id && String(m.sender_id || '').startsWith('bot:') ? fbKey('message', m.id) : '';
  return `<div class="tcomment tmsg${person ? ' you' : ''}${me ? ' from-me' : ''}${String(m.sender_id || '').startsWith('bot:') ? ' bot' : ''}" data-message="${esc(m.id || '')}"><div class="tcomment-head"><span class="tcomment-who">${commentAuthor(m.sender_id, m.refs?.via, src)}</span>${kind}${roadmapCommentMark(m, taskId)}
      ${m.refs?.quiet ? '<span class="muted" title="Saved for the bot\'s next run on this task">saved</span>' : ''}
      <span class="spacer"></span>${m.edited_at ? `<span class="muted" title="Edited ${esc(fmt(m.edited_at))}">edited</span>` : ''}<time class="muted tnum" title="${esc(fmt(m.created_at))}">${esc(ago(m.created_at))}</time></div>
    <div class="bubble ${person ? 'you' : 'bot'}"${fb ? ` data-fb-press="${esc(fb)}"` : ''}>${body ? messageMd('md', body, m.sender_id) : ''}
    ${tfCommentFilesHTML(m, files)}${ask ? askHTML(ask, m.answers, m.sender_id, files) : ''}${fbRowHTML(fb, m.body || '')}</div>${taskStepsHTML(m)}</div>`;
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
  const t = pendingRunText(run, run?.bot);
  // Several bots may answer on a task, so the line names the one at work.
  const word = t.word || t.tip;
  const part = working && x ? (x.parts || []).at(-1) : null;
  const step = part ? clipLine(part.text.replace(/\s+/g, ' ').trim(), 120) : '';
  const label = `<span class="typing ${t.mode}" aria-hidden="true"><i></i><i></i><i></i></span><span class="thinking-word">${esc(word)}${
    working ? '…' : ''}</span>${step ? `<span class="task-live-step">${esc(step)}</span>` : ''}`;
  const stop = working && x && x.run_id && settingsCanManageBot(botRow(x.bot))
    ? `<button type="button" class="ghost task-live-stop" data-task-stop="${esc(x.run_id)}" data-bot="${esc(x.bot)}" ${
      stopping === x.run_id ? 'title="Stopping…" disabled>Stopping…' : 'title="Stop this run">Stop'}</button>` : '';
  return `<div class="thinking task-live" role="status" aria-label="${esc(t.tip)}${step ? ': ' + esc(step) : ''}">${
    working && x?.run_id ? taskStepsFold(x.run_id, label, true) : label}${pendingHintHTML(run)}${stop}</div>`;
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
  // Opened from a pipeline's board: the box asks the task's owner, and what is said stays in this task's conversation.
  const owner = state.dialog.dataset.ask ? String(state.data?.task?.owner_id || '') : '';
  if (box) box.placeholder = owner ? `Ask ${actorLabel(owner)} about this task…` : 'Message…';
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
  const lines: ModalLine[] = [
    ...(data.comments || data.messages || []).filter(m => ['say', 'ask', 'answer'].includes(m.kind)).map((m): ModalLine => ({kind: 'comment', ts: m.created_at, message: m})),
    ...(data.events || []).map((e): ModalLine => ({kind: 'event', ts: e.created_at, ...e})),
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
    thread.addEventListener('toggle', (ev: Event) => {
      if (ev.target instanceof HTMLDetailsElement && ev.target.matches?.('details.run-steps')) void taskStepsOpen(ev.target);
      sourceGroupToggle(state.openLines, ev.target);
      if (ev.target instanceof HTMLDetailsElement && ev.target.matches('details.src-group')) state.openLines?.[ev.target.open ? 'add' : 'delete']('g:' + ev.target.dataset.srcGroup);
    }, true);
    thread.addEventListener('scroll', () => { if (thread.scrollTop < 48 && $('[data-task-earlier]', thread)) taskChatEarlier(state); }, {passive: true});
    thread.addEventListener('click', (ev: MouseEvent) => {
      if (eventElement(ev)?.closest('[data-task-earlier]')) { taskChatEarlier(state); return; }
      const stop = eventElement(ev)?.closest<HTMLButtonElement>('[data-task-stop]');
      if (stop && taskChatCurrent(state)) void taskRunStop(state, stop);
    });
  }
  state.data = data;
  taskNeedBannerSync(state.dialog, data.task);
  taskComposerToPaint(state);
  // `can_comment`: false for someone who may read the task but not comment on it; they get no box
  const can = data.can_comment !== false, d = state.dialog;
  $('form', host).hidden = !can;
  if (d.canComment !== can) { d.canComment = can; if (d.tfEl) tfPaint(d); }
  // a new comment may have carried a new version of a file, or answered a question on one
  const count = (data.comments || data.messages || []).length;
  if (state.count != null && count !== state.count) void tfLoad(state.dialog, state.id);
  state.count = count;
  const files = tfFiles(state.dialog);
  // The run answering the thread; otherwise the bot's next run on this task, when it is queued: what it waits for.
  const live = taskLiveHTML(data.execution, state.stopping, sent);
  const wait = live || !data.waiting_for ? live : `<p class="muted task-wait" role="status">${esc(data.waiting_for)}${/^out of credit/i.test(data.waiting_for) ? cloudCreditLinkHTML() : ''}</p>`;
  // Three or more updates in a row (task updates, and the history lines that are not milestones) are one "N updates"
  // row, shut until opened (ui/app/hub-v2.ts sourceRunsHTML).
  const said = lines.filter(x => x.kind !== 'event' || commentLineHTML(x, 0, lines, files, state.id));
  const isUpdate = (x: ModalLine) => x.kind === 'event' ? !TASK_EVENT_MILESTONES.has(x.field)
    : !!x.message && !askOf(x.message) && isSourceLine(x.message) && rowSource(x.message).kind === 'task_update';
  state.openLines ||= new Set();
  state.shown ||= TASK_CHAT_PAGE;
  const openLines = state.openLines;
  // The newest lines are drawn first; scrolling to the top (or its "earlier" line) draws older ones, back to the first.
  const older = Math.max(0, said.length - state.shown);
  const earlier = older ? `<button type="button" class="linkish task-earlier" data-task-earlier>↑ ${older} earlier message${older === 1 ? '' : 's'} and moves</button>` : '';
  const groupKey = (run: ModalLine[]) => { const x = run[0]; return 'g:' + String(!x ? '' : x.kind === 'event' ? x.id : x.kind === 'run' ? x.run.run : x.message.id); };
  const html = (earlier + sourceRunsHTML(said.slice(older), {isUpdate, min: 3, noun: 'updates',
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
// The turn line above the chat: the needs-you banner with Answer when the reader owes the answer, else who has it now.
function taskTurnLineHTML(t: ModalTask): string {
  const me = myActor(), turn = !taskFinished(t) ? t.turn : null;
  const yours = !!turn?.ask && !!turn.requirement_id && !!me && turn.who === me;
  return yours && turn?.line ? `<div class="task-need" data-task-yours role="region" aria-label="Needs you">${taskMarkHTML(t, {space: false})}<span class="task-need-line">${esc(clipLine(turn.line, 400))}</span>${taskCauseLinkHTML(turn.href)}
    <button type="button" class="primary tmodal-act" data-task-answer="${esc(String(turn.requirement_id))}">Answer</button></div>`
    : turn?.line ? `<p class="task-wait">${taskMarkHTML(t, {space: false})}<span>${esc(clipLine(turn.line, 400))}</span>${taskCauseLinkHTML(turn.href)}</p>` : '';
}
// Answer opens the ask's card; bound again whenever a refresh inserts the banner.
function taskAnswerBind(d: ModalDialog, task: ModalTask): void {
  const answer = $('[data-task-answer]', d);
  if (answer) answer.onclick = async () => {
    answer.disabled = true;
    try { await decideOpenAsk(String(task.id), String(answer.dataset.taskAnswer)); } finally { answer.disabled = false; }
  };
}
// The needs-you banner follows the turn: it goes once the task no longer waits on this reader's own ask, and appears when
// a refresh raises one.
function taskNeedBannerSync(d: ModalDialog, task: ModalTask | null | undefined): void {
  if (!task || String(task.id) !== String(d.dataset.task)) return;
  const banner = $('.task-need', d);
  const turn = taskFinished(task) ? null : task.turn;
  const mine = !!(turn?.ask && turn.requirement_id && turn.who === myActor());
  if (banner) {
    if (!(mine && String(turn?.requirement_id) === ($('[data-task-answer]', banner)?.dataset.taskAnswer || ''))) banner.remove();
    else return;
  }
  if (!mine) return;
  const html = taskTurnLineHTML(task);
  if (!html.includes('task-need')) return;
  const old = $('.task-wait', d);
  if (old) old.outerHTML = html;
  else $('.task-tabs', d)?.insertAdjacentHTML('beforebegin', html);
  taskAnswerBind(d, task);
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
      state.stopped = true; clearInterval(state.poll); state.liveOff?.();
      TASK_DRAFTS.delete(String(state.id)); PROP_TASKS = null;
      if (state.dialog.taskLive) taskLiveGone(state.dialog, state.dialog.taskLive);
      else if (TASKS_ST) void tasksCatchUp(TASKS_ST);
      state.dialog.close(); state.dialog.innerHTML = '';
      return;
    }
    if (errorStatus(e) === 400) {state.stopped = true; clearInterval(state.poll); state.liveOff?.();}
    const status = $('[data-task-chat-status]', state.host);
    if (status) status.textContent = `Unable to refresh: ${errorMessage(e, '')}`;
    else state.host.innerHTML = `<p class="err" role="status">Conversation unavailable: ${esc(errorMessage(e, ''))}</p>`;
  } finally { state.reading = false; }
}
async function taskChatLoad(id: string | number, dialog: ModalDialog, data: ModalDetail | null = null) {
  const state: TaskChatState = TASK_CHAT = {dialog, host: $('.task-chat', dialog),
    path: `/v2/tasks/${encodeURIComponent(id)}`, id, poll: 0, sending: false, reading: false};
  liveTasksSync();
  if (data?.task?.conversation_id) state.cid = data.task.conversation_id;
  if (data) { await taskStagesFor(data); taskCommentsRender(state, data); } else await taskChatRead(state);
  if (!taskChatCurrent(state) || state.stopped) return;
  taskJumpPending(state);
  // Without live events, the poll they replaced.
  state.poll = liveFallback(() => {
    if (document.hidden || !taskChatCurrent(state)) return;
    void taskChatRead(state);
    tfLoad(state.dialog, state.id).catch(() => {});     // a new file or version, or an answer on one, without reopening
  }, 180000);
  if (state.poll) return;
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
// A link from outside Tico (the Slack visuals line) names a message: bring it into view and mark it for a moment.
// The task opened only because the reader may read it; a message not on the page loaded says so.
function taskJumpPending(state: TaskChatState) {
  if (!TASK_JUMP || ![String(state.id), state.data?.task?.id].includes(TASK_JUMP.task)) return;
  const {id} = TASK_JUMP; TASK_JUMP = null;
  const thread = $('.task-comments', state.host), el = thread?.querySelector(`[data-message="${CSS.escape(id)}"]`);
  if (!el) { toast('That message is not in this conversation', true); return; }
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
// A deferred task (backend/quiet_work.py): delivered into a run (held until something), eligible for the bot's next
// run, or queued with a run of its own; which run, and when it was handed over. `quiet` is the field's older name.
function taskDeferredHTML(t: ModalTask) {
  const q = t.deferred || t.quiet;
  if (!q || taskFinished(t)) return '';
  const state = q.state === 'delivered' && q.resume ? 'held' : q.state;
  const label = ({queued: 'Queued', eligible: 'Eligible', delivered: 'Delivered', held: 'Held'} as Record<string, string>)[state] || state;
  const more = [q.run ? `run ${String(q.run).slice(0, 8)}` : '', q.at ? `handed over ${ago(q.at)}` : ''].filter(Boolean).join(', ');
  return `<p class="task-wait task-deferred" data-task-deferred="${esc(state)}"><span class="lbl">Deferred: ${esc(label)}</span> ${esc(q.words || '')}${more ? ` <span class="muted">(${esc(more)})</span>` : ''}</p>`;
}
// A paused bot's cause links to the page that shows it (backend/task_wakes.py CAUSE_PAGES): out of credit to the
// strip's Add credit.
const TASK_CAUSE_PAGES: Record<string, string> = {'#/settings/computers': 'Computers', '#/settings/billing': 'Billing'};
function taskCauseLinkHTML(href: string | null | undefined): string {
  if (!href || !TASK_CAUSE_PAGES[href]) return '';
  return (href === '#/settings/billing' && cloudCreditLinkHTML()) || ` <a href="${esc(href)}">${TASK_CAUSE_PAGES[href]}</a>`;
}
// A phone shows the window as three tabs (task-code.css): Chat, the default, then Pages (with a count) and Info. On a
// computer the chat is the left column and the tabs are not shown; the tab shown is kept on the dialog across redraws.
type TaskTab = 'chat' | 'pages' | 'info';
const TASK_TABS: [TaskTab, string][] = [['chat', 'Chat'], ['pages', 'Pages'], ['info', 'Info']];
function taskTabPaint(d: ModalDialog) {
  const tab = d.taskTab || 'chat', body = $('.task-layout', d);
  if (body) body.dataset.taskTabShown = tab;
  for (const b of d.querySelectorAll<HTMLElement>('[data-task-tab]')) b.setAttribute('aria-selected', String(b.dataset.taskTab === tab));
  $('[data-task-activity]', d)?.classList.toggle('open', !!d.taskActivity);
}
function taskTabsBind(d: ModalDialog) {
  taskTabPaint(d);
  for (const b of d.querySelectorAll<HTMLElement>('[data-task-tab]')) b.onclick = () => {
    d.taskTab = b.dataset.taskTab as TaskTab; taskTabPaint(d);
    if (d.taskTab === 'chat' && TASK_CHAT?.dialog === d) stickScroll($('.task-comments', TASK_CHAT.host)).toBottom();
  };
}
// Activity and links: Links, Runs on, Sessions, Runs, Related, Meetings and Files sit in one section at the foot of the
// right column (a phone's Info tab). Each shows only with something in it, and the section only when one does
// (task-code.css); ⋯ › Activity and links opens it with the empty ones too, to add a link or a session.
function taskActivityOpen(d: ModalDialog) {
  d.taskActivity = true; d.taskTab = 'info'; taskTabPaint(d);
  const activity = $('[data-task-activity]', d);
  activity?.scrollIntoView({block: 'nearest'});
  $('.rail-h', activity)?.setAttribute('tabindex', '-1');
  ($('.rail-h', activity) as HTMLElement | null)?.focus({preventScroll: true});
}
// The task in full. The header holds actions only: Snoozed (a button that wakes it), the Screen icon, 👍 👎, the age, then
// Done and Close for whoever may use them as labelled buttons, then "…" and ✕. Who has it now is one line under the title
// (the turn's own words), with Answer when it is your ask; then the details, To leave <stage> and the rest.
function hubModalHTML(t: ModalTask, opts: {thread?: boolean} = {}) {
  const finished = taskFinished(t), me = myActor();
  const turn = !finished ? t.turn : null;
  const original = taskBody(t).trim() === String(t.title || '').trim() ? '' : taskBody(t);   // a quick subtask's details are its title
  const note = String(t.note || '');
  const mover = canMove();
  const links = (t.links || []).filter(l => !['pr', 'worktree', 'session'].includes(l.kind));   // Code is in the rail; Sessions below
  const rights = taskPropRights(t);
  const canDone = !finished && !!me && me === t.owner_id;
  const canClose = t.status === 'open' && (rights.mover || rights.party);
  const snooze = t.snooze || null;
  // The thread view's own header pieces: back, the title in the bar, and Details (task-modal.css hides the rest).
  const thread = !!opts.thread;
  // A task waiting on this reader's own answer: a banner above the chat with its Answer button (it goes when answered).
  const turnLine = taskTurnLineHTML(t);
  return `<div class="tmodal-head">${thread ? `<button class="ghost peek-btn tthread-only tthread-back" type="button" data-thread-back aria-label="Back to chat" title="Back to chat"><svg viewBox="0 0 12 12" width="14" height="14" aria-hidden="true" focusable="false"><path d="M7.5 3l-3 3 3 3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
      <span class="tthread-only tthread-title" tabindex="-1">${esc(t.title)}</span>` : ''}${taskSnoozed(t)
      ? `<button type="button" class="tl-due wake tchip-snoozed" data-task-unsnooze title="${esc(`Snoozed${snooze?.until ? ` until ${fmt(snooze.until)}` : ''}${snooze?.by ? ` by ${actorLabel(snooze.by)}` : ''}${snooze?.why ? ` · ${snooze.why}` : ''}`)}${rights.snooze ? '. Click to bring it back now' : ''}"${rights.snooze ? '' : ' disabled'}>${snoozeDay(t) ? `Snoozed to ${esc(snoozeDay(t))}` : 'Snoozed'}${chipNoteHTML(snooze?.why)}</button>` : ''}
      ${finished ? '' : taskScreenLinkHTML(t.owner_id)}
      <span class="spacer"></span>
      ${fbButtonsHTML(fbTaskKey(t))}
      <span class="muted tnum tmodal-age" title="${esc(`${taskSourceLine(t)}${t.created_at ? ' ' + fmt(t.created_at) : ''} · Updated ${fmt(t.updated_at)}`)}">${esc(ago(t.updated_at || t.created_at))}</span>
      ${canDone ? `<button class="ghost tmodal-act" type="button" data-task-finish="done" title="Mark this task done">${PROP_ICON.check}<span>Done</span></button>` : ''}
      ${canClose ? '<button class="ghost tmodal-act" type="button" data-task-finish="close" title="Close this task">Close</button>' : ''}
      <button class="ghost peek-btn" type="button" data-task-more aria-haspopup="menu" aria-label="More actions" title="More actions">${PROP_ICON.more}</button>
      ${thread ? '<button class="ghost tmodal-act tthread-only" type="button" data-thread-details>Details</button>' : ''}
      <span class="tmodal-sep" aria-hidden="true"></span><button class="ghost peek-btn tmodal-x" type="button" data-modal-close aria-label="Close window" title="Close window (Esc)">${TL_ICON.x}</button></div>
    <h2 class="tmodal-title" tabindex="-1">${esc(t.title)}</h2>
    <div class="task-edit-meta muted"><span data-task-edited></span><span data-task-presence></span><span data-task-edit-error hidden>Not saved · <button type="button" class="linkish" data-task-edit-retry>Retry</button></span></div>
    <div class="muted" data-task-edit-current hidden></div>
    ${t.goal_id ? `<div class="muted tmeta"><a href="#/goals/${encodeURIComponent(t.goal_id)}" class="task-goal" data-goal-title="${esc(t.goal_id)}">serves a goal</a></div>` : ''}
    ${turnLine}
    ${taskDeferredHTML(t)}
    <div class="task-tabs" role="tablist" aria-label="Task">${TASK_TABS.map(([tab, label]) => `<button type="button" role="tab" class="task-tab" data-task-tab="${tab}" aria-selected="${tab === 'chat'}">${label}${tab === 'pages' ? ' <span class="tparts" data-task-page-count></span>' : ''}</button>`).join('')}</div>
    <div class="tmodal-body task-layout" data-task-tab-shown="chat">
      <section class="task-chat" aria-label="Conversation"><p class="muted" role="status">Loading the conversation…</p></section>
      <aside class="task-side" aria-label="Details">
      <div class="tmodal-main">
      ${githubNudgeTaskHostHTML(t)}
      <section class="task-pages" data-task-pages aria-label="Pages" hidden></section>
      <section class="rule-holds" data-rule-holds aria-label="Held by a rule" hidden></section>
      ${taskEditBodyHTML(t, original, turn?.line || '')}
      ${note && note.replace(/\s+/g, ' ').trim() !== clipLine(turn?.line, 400) ? `<details class="task-orig"><summary>Progress</summary><div class="tdesc md">${safeMd(note)}</div></details>` : ''}
      ${(t.acceptance_criteria || []).length ? `<section class="tsec"><h3 class="rail-h">Done looks like</h3><ul class="tcriteria">${t.acceptance_criteria!.map(a => `<li>${esc(a)}</li>`).join('')}</ul></section>` : ''}
      <div class="tsecs">${taskRequirementsHTML(t)}</div>
      </div>
        <section class="task-props" data-task-props aria-label="Properties">${taskPropsHTML(t)}</section>
        <div class="task-rail" data-task-rail hidden></div>
        <section class="task-activity" data-task-activity aria-label="Activity and links"><h3 class="rail-h">Activity and links</h3>
      <section class="tsec task-links${links.length ? '' : ' tsec-empty'}"><h3 class="rail-h">Links<button type="button" class="prop-add" data-link-add aria-label="Add a link" title="Add a link">${TL_ICON.plus}</button></h3>
        ${links.length ? `<div class="tlinks">${links.map(l => `<span class="tlink ${esc(l.kind)} ${esc(l.state || '')}"><a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title || l.url)}</a>${l.state && l.state !== 'open' ? ` · ${esc(l.state)}` : ''}${mover ? `<button type="button" class="x" data-drop-link="${esc(l.id)}" aria-label="Remove link">×</button>` : ''}</span>`).join('')}</div>` : ''}
        <form class="inline task-link-add" data-modal-link hidden><input type="url" inputmode="url" autocomplete="off" spellcheck="false" placeholder="https://…" aria-label="Add a link"></form></section>
      ${roadmapTaskHTML(t)}
      ${taskRunModelHTML(t)}
      ${taskSessionsHTML(t)}
      <section class="tsec task-runs" data-task-runs aria-label="Runs" hidden></section>
      ${t.relations ? taskRelHTML(t) : ''}
      <section class="tsec task-meetings" data-task-meetings aria-label="Meetings" hidden></section>
      <section class="tsec task-files" data-task-files aria-label="Files" hidden></section>
        </section>
      </aside>
    </div>`;
}
