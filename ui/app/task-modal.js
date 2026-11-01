/* ui/app/task-modal.js — The task modal and its conversation (the task's session as a chat) (its files: ui/app/task-files.js)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ---- the task modal: one item, in full, with what you can do to it
// The full task (#task-modal, a modal dialog) and the side peek (#task-peek, a dialog shown beside the list) draw the
// same content and wire the same clicks.
function taskDialogWire(d) {
  tfWire(d);
  // A link in the task to one of its files opens that file's tile; any other file link opens the viewer (viewer.js).
  d.addEventListener('click', ev => {
    const anchor = ev.target.closest('a[href]');
    if (!anchor || anchor.hasAttribute('download') || ev.defaultPrevented) return;
    const url = new URL(anchor.href, location.href);
    const match = url.origin === location.origin && url.pathname.match(/^\/api\/v2\/files\/([^/]+)$/);
    if (match && tfOpen(d, decodeURIComponent(match[1]), url.searchParams.get('v'), {scroll: true})) ev.preventDefault();
  });
  d.addEventListener('issue-acted', () => d.close());
  d.addEventListener('close', () => {
    // The close event is queued: a newer opening may still be awaiting its first draw.
    if (d.open || d.taskOpening?.pending) return;
    if (!TASK_CHAT || TASK_CHAT.dialog === d) taskChatStop();
    d.tfEl?.querySelector('video, audio')?.pause();
    d.tfEl = null; d.tfOpen = null; d.giEl = null;
    d.taskOpening = {}; d.askDrafts = new Map();
  });
}
function taskModal() {
  let d = $('#task-modal');
  if (d) return d;
  d = document.createElement('dialog'); d.id = 'task-modal'; d.className = 'tmodal';
  d.setAttribute('aria-label', 'Task');
  d.addEventListener('click', ev => { if (ev.target === d) d.close(); });
  taskDialogWire(d);
  d.addEventListener('close', () => {
    if (d.open || d.taskOpening?.pending) return;
    TASK_MODAL_LOAD++;
    document.body.classList.remove('task-modal-open');
    const back = d.returnContext; d.returnContext = null;
    if (!back) return;
    if (history.state?.taskModal && location.hash === back.route && !TASK_MODAL_BACK) { TASK_MODAL_BACK = true; history.back(); }
    const opener = back.opener?.isConnected ? back.opener : $(`[data-task-detail="${CSS.escape(back.task)}"] [data-task-detail-open]`);
    if (!document.activeElement || document.activeElement === document.body || d.contains(document.activeElement)) opener?.focus({preventScroll: true});
  });
  document.body.appendChild(d);
  return d;
}
let TASK_MODAL_BACK = false;
let TASK_MODAL_LOAD = 0;
window.addEventListener('popstate', () => {
  TASK_MODAL_BACK = false;
  const d = $('#task-modal');
  if (d?.open && d.returnContext && !history.state?.taskModal) d.close();
});
// `d` is the full modal unless a caller passes the peek.
async function taskModalOpen(key, d) {
  const state = TASKS_ST, from = S.route, load = ++TASK_MODAL_LOAD;
  let it = (state?.tasks || []).map(taskItem).find(x => x.key === key)
    || [...S.issues.map(issueItem)].find(x => x.key === key);
  if (!it && /^t/.test(key)) {
    const detail = await v2Get(`/v2/tasks/${encodeURIComponent(key.slice(1))}`);
    if (S.route !== from || load !== TASK_MODAL_LOAD) return;
    if (detail?.task) it = taskItem(detail.task);
  }
  if (!it) return;
  if (it.kind === 'schedule') {
    const d = taskModal();
    d.innerHTML = `<h2>${esc(it.title)}</h2><p>Scheduled for ${esc(fmt(it.schedule.next))}</p><button data-modal-close>Close</button>`;
    $('[data-modal-close]', d).onclick = () => d.close();
    if (!d.open) d.showModal();
    return;
  }
  if (it.kind === 'issue') {
    taskChatStop();
    const d = taskModal();
    d.innerHTML = issueModalHTML(it.issue);
    $('[data-modal-close]', d).onclick = () => d.close();
    d.querySelectorAll('[data-modal-issue]').forEach(b => {
      b.onclick = () => issueComposer($('.issue-compose', d), it.issue.number, b.dataset.modalIssue);
    });
    if (!d.open) d.showModal();
    return;
  }
  taskModalShow(it.task, d);
}
// The runs section drawn for the open task, held across a redraw of that task (taskModalShow).
const taskModalRuns = new WeakMap();
function taskModalRunsKeep(d, keep) {
  const runs = keep && $('[data-task-runs]', d);
  if (runs) { runs.dataset.task = String(d.dataset.task); taskModalRuns.set(d, runs); } else taskModalRuns.delete(d);
}
// The one way a hub task opens in full: from the board, from a link, from a chat card. In the peek it opens beside the list.
// `opts.thread`: just the task's own conversation, as a bot's chat opens it (taskThreadShow); Details shows the rest.
async function taskModalShow(task, d = taskModal(), opts = {}) {
  TASK_MODAL_LOAD++;
  if (!d) d = taskModal();
  const peek = !!d.dataset.peek;
  // A fresh open starts with no file open, whether or not the last close's event has run yet.
  if (!d.open || String(d.dataset.task) !== String(task.id)) {
    d.taskOpening = {pending: !d.open}; d.askDrafts = new Map(); d.tfEl = null; d.tfOpen = null;
    d.classList.toggle('thread', !peek && !!opts.thread);     // a redraw of the same task keeps the view it had
  } else askDraftCapture(d);
  const opening = d.taskOpening;
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
  if (d.dataset.task !== String(task.id)) { d.propsErrs = null; d.focusProp = ''; d.liveTask = null; }
  // never draw an older copy of the task over the newer one a save returned
  else if (d.liveTask && Number(task.version) < Number(d.liveTask.version)) task = d.liveTask;
  const seq = d.drawSeq = (d.drawSeq || 0) + 1;
  const typing = d.contains(document.activeElement) && document.activeElement.matches('.task-chat textarea');
  if (peek && TASKS_ST && TASKS_ST.peek !== 't' + task.id && tasksById(TASKS_ST).has(String(task.id))) {
    taskPeekOpen(TASKS_ST, 't' + task.id, {via: 'link'}); return;   // a parent or subtask opened from the peek: the list follows it
  }
  d.dataset.task = task.id;
  // a property that has the focus now keeps it through the redraw (over the one a save asked for)
  const onProp = d.contains(document.activeElement) ? document.activeElement.dataset.prop : '';
  if (onProp) d.focusProp = onProp;
  // The detail is asked for at once; the first draw waits only for the CLI's manifest (session copy commands need it
  // even when Tasks is the first page opened), read once.
  const reading = Promise.all([v2Get(`/v2/tasks/${encodeURIComponent(task.id)}`), taskTypesLoad({cached: true}).catch(() => TASK_TYPES)]);
  // A previous close's Back navigation must settle before a new dialog takes its place in history.
  if (!peek && TASK_MODAL_BACK) await new Promise(resolve => window.addEventListener('popstate', resolve, {once: true}));
  await loadHarnessRows();
  if (d.drawSeq !== seq || d.taskOpening !== opening) return;
  opening.pending = false;
  taskModalRunsKeep(d, kept);
  d.innerHTML = hubModalHTML(task, taskItem(task), {peek, thread: d.classList.contains('thread')});
  adopt();
  taskModalBind(d, task);
  taskPropFocus(d);
  if (!d.open) {
    // The peek sits beside the list: opening it leaves the focus on the row, so ↑/↓ keep moving through the list.
    const was = document.activeElement;
    if (!peek && !TASKS_ST) {
      // Outside Tasks, Back dismisses the detail without rebuilding the bot's chat, tab or scroll position.
      d.returnContext = {route: location.hash, task: String(task.id), opener: was};
      history.pushState({...(history.state || {}), taskModal: 1}, '');
    }
    if (peek && matchMedia('(max-width:760px)').matches) d.showModal();     // a phone's sheet covers the list: modal
    else if (peek) { d.show(); if (was && was !== document.body && was.isConnected) was.focus({preventScroll: true}); }
    else d.showModal();
    // Opening lands on the title, not on the first header button (a focus ring on "…" read as a stray highlight).
    if (d.open && d.matches(':modal')) $(d.classList.contains('thread') ? '.tthread-title' : '.tmodal-title', d)?.focus({preventScroll: true});
  }
  if (!peek) document.body.classList.add('task-modal-open');
  d.scrollTop = 0;
  // the list knows the task; the detail adds its parts, its parent and the comments
  const [detail] = await reading;
  if (!d.open || String(d.dataset.task) !== String(task.id) || d.drawSeq !== seq) return;
  if (!detail?.task) { d.close(); d.innerHTML = ''; TASK_DRAFTS.delete(String(task.id)); return; }
  await taskMenuSettled(d);                 // never pull a menu out from under the pointer
  if (!d.open || String(d.dataset.task) !== String(task.id) || d.drawSeq !== seq) return;
  if (d.liveTask && Number(detail.task.version) < Number(d.liveTask.version)) return;   // a save landed since this was asked for
  // The task's own model sits beside the task in the detail (GET /v2/tasks/{id} `run_model`), as its children do, with
  // whether this reader may set it (`can_set_run_model`).
  const full = {...detail.task, children: detail.children || [], ...('run_model' in detail
    ? {run_model: detail.run_model, can_set_run_model: detail.can_set_run_model} : {})};
  // the redraw keeps the focus where it was (a header button, the title, a property)
  const a = d.contains(document.activeElement) ? document.activeElement : null;
  const keep = !a ? '' : a.matches('[data-modal-close]') ? '[data-modal-close]' : a.matches('[data-task-more]') ? '[data-task-more]' : a.matches('.tmodal-title') ? '.tmodal-title'
    : a.matches('[data-thread-back]') ? '[data-thread-back]' : a.matches('.tthread-title') ? '.tthread-title'
    : a.dataset.prop ? `[data-prop="${CSS.escape(a.dataset.prop)}"]` : '';
  askDraftCapture(d);
  taskModalRunsKeep(d, kept);
  d.innerHTML = hubModalHTML(full, taskItem(full), {peek, thread: d.classList.contains('thread')});
  adopt();
  taskModalBind(d, full, true);
  if (keep) $(keep, d)?.focus({preventScroll: true});
  taskPropFocus(d); d.focusProp = '';
  void taskRailLoad(d, full, detail);
  if (kept && TASK_CHAT === kept && d.contains(kept.host)) taskCommentsRender(kept, detail);
  else void taskChatLoad(task.id, d, detail);
  void tfLoad(d, task.id);
  void taskRunsLoad(d, task.id);
  if (typing) { const box = $('.task-chat textarea', d); box?.focus(); box?.setSelectionRange(box.value.length, box.value.length); }
}
// A task's own thread: the full modal showing only its header and its conversation, so what is typed there is plainly
// said in the task (POST /v2/tasks/{id}/comments), never in the bot's main chat. Back returns to the chat.
function taskThreadShow(task) { return taskModalShow(task, taskModal(), {thread: true}); }
async function taskThreadOpen(id) {
  try {
    const data = await get('/v2/tasks/' + encodeURIComponent(id));
    if (data?.task) await taskThreadShow(data.task);
  } catch (error) { toast(error.message, true); }
}
// After a property saves, the redrawn row gets the focus back.
function taskPropFocus(d) {
  if (d.focusProp) $(`[data-prop="${CSS.escape(d.focusProp)}"]`, d)?.focus({preventScroll: true});
}
// `full`: the task as GET /v2/tasks/{id} answers it (list rows leave out what the rail's Code section needs).
function taskModalBind(d, task, full = false) {
  tfAdopt(d, task);
  taskPagesAdopt(d, task);      // the task's pages, as cards at its top (ui/app/pages.js)
  ruleHoldsAdopt(d, task);      // a rule review's held outputs and their actions (ui/app/org-rule-holds.js)
  growthIdeaAdopt(d, task);     // an Idea task's hypothesis, test and verdict (ui/app/growth-idea.js)
  d.dataset.version = String(task.version ?? '');
  if (!d.liveTask || String(d.liveTask.id) !== String(task.id) || Number(task.version) >= Number(d.liveTask.version)) d.liveTask = task;
  if (d.taskRail?.id !== String(task.id)) d.taskRail = {id: String(task.id), open: new Set()};
  if (full) d.taskRail.full = task;
  taskRailPaint(d, task); taskRailBind(d, task);
  const dismiss = () => d.dataset.peek && TASKS_ST ? taskPeekClose(TASKS_ST) : d.close();
  $('[data-modal-close]', d).onclick = dismiss;
  const back = $('[data-thread-back]', d), details = $('[data-thread-details]', d);
  if (back) back.onclick = dismiss;
  if (details) details.onclick = () => { d.classList.remove('thread'); void taskModalShow(d.liveTask || task, d); };
  void taskGoalTitle(d);
  // Each change goes through the dialog's queue (taskSave), one at a time, each with the latest version.
  const change = (body, then, field) => taskSaveQueued(d, String(task.id), body, then, field);
  // Done and Close finish with the task: the window closes and the page behind it is where the person was.
  d.querySelectorAll('[data-task-finish]').forEach(b => b.onclick = async () => {
    b.disabled = true;
    const saved = await change(b.dataset.taskFinish === 'close' ? {close: true} : {status: 'done'}, false, 'status');
    if (saved && d.open && String(d.dataset.task) === String(task.id)) dismiss();
    else b.disabled = false;
  });
  const unsnooze = $('[data-task-unsnooze]', d);
  if (unsnooze) unsnooze.onclick = () => change({unsnooze: true}, undefined, 'snooze');
  taskPropsBind(d, task, change);
  roadmapTaskBind(d, task, change);
  const privacy = $('[data-task-private]', d);
  if (privacy) privacy.onchange = () => change({private: privacy.checked}, undefined, 'private');
  d.querySelectorAll('[data-drop-label]').forEach(b => b.onclick = () => change(cur => ({labels: (cur.labels || []).filter(l => l !== b.dataset.dropLabel)}), undefined, 'tags'));
  const opening = d.taskOpening;
  const linkCurrent = () => d.open && String(d.dataset.task) === String(task.id) && d.taskOpening === opening;
  const link = $('[data-modal-link]', d);
  const addLink = $('[data-link-add]', d);
  if (addLink && link) {
    addLink.onclick = () => { link.hidden = !link.hidden; if (!link.hidden) link.querySelector('input').focus(); };
    link.querySelector('input').onkeydown = ev => { if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); link.hidden = true; addLink.focus(); } };
  }
  if (link) link.onsubmit = async ev => {
    ev.preventDefault();
    const url = link.querySelector('input').value.trim(); if (!url) return;
    try {
      await post(`/v2/tasks/${encodeURIComponent(task.id)}/links`, {url});
      const data = await get(`/v2/tasks/${encodeURIComponent(task.id)}`);
      if (TASKS_ST) void tasksLoad(TASKS_ST);
      if (linkCurrent() && data.task) taskModalShow(data.task, d);
    } catch (e) { toast(e.message, true); }
  };
  // Sessions: + opens one input for a URL or a resume command, sent as a session link.
  const session = $('[data-modal-session]', d);
  const addSession = $('[data-session-add]', d);
  if (addSession && session) {
    addSession.onclick = () => { session.hidden = !session.hidden; if (!session.hidden) session.querySelector('input').focus(); };
    session.querySelector('input').onkeydown = ev => { if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); session.hidden = true; addSession.focus(); } };
    session.onsubmit = async ev => {
      ev.preventDefault();
      const url = session.querySelector('input').value.trim(); if (!url) return;
      try {
        await post(`/v2/tasks/${encodeURIComponent(task.id)}/links`, {url, kind: 'session'});
        const data = await get(`/v2/tasks/${encodeURIComponent(task.id)}`);
        if (linkCurrent() && data.task) taskModalShow(data.task, d);
      } catch (e) { toast(e.message, true); }
    };
  }
  taskRunModelBind(d, task);
  d.querySelectorAll('[data-copy-session]').forEach(b => b.onclick = () =>
    void copyText(b.dataset.copySession).then(() => toast('Copied'), () => toast('Could not copy', true)));
  d.querySelectorAll('[data-drop-link]').forEach(b => b.onclick = async () => {
    try {
      await post(`/v2/tasks/${encodeURIComponent(task.id)}/links`, {remove: b.dataset.dropLink});
      const data = await get(`/v2/tasks/${encodeURIComponent(task.id)}`);
      if (linkCurrent() && data.task) taskModalShow(data.task, d);
    } catch (e) { toast(e.message, true); }
  });
  // Related tasks, by kind: + asks which kind, then finds the task; each one opens, × takes it off (from both tasks).
  const addRelated = $('[data-related-add]', d);
  if (addRelated) addRelated.onclick = () => propMenu(d, addRelated, {label: 'Relation', items: TASK_REL_ADD.map(([kind, text]) => ({value: kind, text, html: `<span>${esc(text)}</span>`})),
    onPick: kind => setTimeout(async () => {
      const have = new Set(Object.values(task.relations || {}).flat().map(r => String(r.id)));
      const rows = (await taskPropCandidates(task, kind.value)).filter(x => !have.has(String(x.id)));
      if (!addRelated.isConnected) return;
      propMenu(d, addRelated, {label: kind.text, find: 'Find a task', none: 'No open tasks', items: rows.map(x => ({value: x.id, text: `${x.title} ${actorLabel(x.owner_id)}`,
        html: `${taskStatusText(x)}<span class="tl-mi-t">${esc(clipLine(x.title, 70))}</span><span class="tl-mi-sub">${esc(actorLabel(x.owner_id))}</span>`})),
        onPick: it => taskRelate(d, task, {task: it.value, kind: kind.value})});
    }, 0)});
  d.querySelectorAll('[data-drop-rel]').forEach(b => b.onclick = () => {
    const [kind, direction, other] = b.dataset.dropRel.split(' ');
    // A row this task is the far end of is taken off from the other task's side.
    if (direction === 'in') void taskRelate(d, task, {task: String(task.id), kind, remove: true}, other);
    else void taskRelate(d, task, {task: other, kind, remove: true});
  });
  d.querySelectorAll('[data-open-task]').forEach(b => b.onclick = ev => { ev.preventDefault(); void taskModalOpen(b.dataset.openTask, d); });
}
// The kinds the Related section adds, and the groups it shows: [kind, direction, label].
// Sessions working the task: the owning bot's (primary; Tico records it) first, then ones people linked to
// track. Tracking only: task chat goes to the owning bot, never to a linked session.
const SESSION_COPY = '<svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true" focusable="false"><rect x="4" y="4" width="6.5" height="6.5" rx="1" fill="none" stroke="currentColor" stroke-width="1.2"/><path d="M8 4V2.5a1 1 0 0 0-1-1H2.5a1 1 0 0 0-1 1V7a1 1 0 0 0 1 1H4" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>';
function taskSessions(t) {
  return (t.links || []).filter(l => l.kind === 'session').map(l => {
    let d = {}; try { d = JSON.parse(l.detail || '{}'); } catch {}
    return {...l, d, value: String(l.url || '').replace(/^session:/, '')};
  }).sort((a, b) => (b.d.primary ? 1 : 0) - (a.d.primary ? 1 : 0));
}
function taskSessionsHTML(t) {
  const copy = (value, what) => `<button type="button" class="tcopy" data-copy-session="${esc(value)}" aria-label="Copy ${esc(what)}" title="Copy ${esc(what)}">${SESSION_COPY}</button>`;
  const drop = myActor() ? l => `<button type="button" class="x" data-drop-link="${esc(l.id)}" aria-label="Remove session">×</button>` : () => '';
  const rows = taskSessions(t).map(l => {
    // A bot's session (the primary, or an earlier owner's) copies its full resume command, folder included.
    if (l.d.bot) {
      const who = 'bot:' + l.d.bot;
      const cmd = l.d.thread_id ? botResumeCommand(l.d.harness, l.d.thread_id, l.d.bot) : '';
      return `<span class="tlink session${l.d.primary ? ' primary" title="Primary' : ''}">${actorFace(who, 14)}<span>${esc(actorLabel(who))}${l.d.harness ? ` · ${esc(l.d.harness)}` : ''}</span>${cmd ? copy(cmd, 'resume command') : ''}${l.d.primary ? '' : drop(l)}</span>`;
    }
    const label = esc(l.title || l.value);
    return /^https?:\/\//.test(l.url)
      ? `<span class="tlink session"><a href="${esc(l.url)}" target="_blank" rel="noopener">${label}</a>${drop(l)}</span>`
      : `<span class="tlink session"><span title="${esc(l.value)}">${label}</span>${copy(l.value, 'session')}${drop(l)}</span>`;
  });
  return `<section class="tsec task-sessions${rows.length ? '' : ' tsec-empty'}"><h3 class="rail-h">Sessions<button type="button" class="prop-add" data-session-add aria-label="Add a session" title="Add a session">${TL_ICON.plus}</button></h3>
    ${rows.length ? `<div class="tlinks">${rows.join('')}</div>` : ''}
    <form class="inline task-link-add" data-modal-session hidden><input type="text" autocomplete="off" spellcheck="false" placeholder="URL or resume command" aria-label="Add a session"></form></section>`;
}

// Runs on (POST /v2/tasks/{id}/model, backend/task_run_models.py): the model this task's runs use, or the bot's task model
// until one is set on it. The model list is GET /v2/models, read once here and by the automation step editor, which offers
// the same models. Gateway tier picks (tico-*) are made in the bot's settings, so they stay out of both.
let TASK_RUN_ROWS = null, TASK_RUN_LOADING = null;
function taskRunCatalog() {
  TASK_RUN_LOADING ||= get('/v2/models').then(d => {
    TASK_RUN_ROWS = (d.models || []).filter(m => !m.deprecated && !m.meeting && !/^tico-/.test(m.id || ''));
    return TASK_RUN_ROWS;
  }, () => { TASK_RUN_LOADING = null; return []; });
  return TASK_RUN_LOADING;
}
const taskRunOpts = (list, val) => list.map(([k, label]) => `<option value="${esc(k)}"${k === val ? ' selected' : ''}>${esc(label)}</option>`).join('');
// The choice a form starts on: the task's own model, harness and effort, or none (the bot's task model).
const taskRunPick = run => ({model: run?.model || '', harness: run?.harness || '', effort: run?.reasoning_effort || ''});
function taskRunWords(run, rows) {
  if (!run?.model) return "The bot's task model";
  const row = (rows || []).find(m => m.id === run.model);
  return [row?.label || run.model, (row?.harnesses || []).length > 1 ? harnessWords(run.harness) : '', run.reasoning_effort].filter(Boolean).join(' · ');
}
// Only whoever the server says may set the task's model gets the form (`can_set_run_model`, the answer POST
// /v2/tasks/{id}/model gives); anyone else reads what it runs on.
const taskRunMay = t => t.can_set_run_model === true;
function taskRunModelBody(run, rows, pick, may = true) {
  const head = '<h3 class="rail-h">Runs on</h3>', now = `<p class="task-run-now">${esc(taskRunWords(run, rows))}</p>`;
  if (!may) return head + now;
  if (!rows) return `${head}${now}<p class="muted">Loading the models…</p>`;
  const row = rows.find(m => m.id === pick.model), harnesses = (row?.harnesses || []).length > 1 ? row.harnesses : [];
  return `${head}${now}<form class="task-run-form" data-run-model-form>
    <label><span>Model</span><select data-run-model>${taskRunOpts([['', "The bot's task model"], ...rows.map(m => [m.id, m.label || m.id])], pick.model)}</select></label>
    ${harnesses.length ? `<label><span>Harness</span><select data-run-harness>${taskRunOpts(harnesses.map(h => [h, harnessWords(h) || h]), pick.harness)}</select></label>` : ''}
    ${row?.efforts?.length ? `<label><span>Effort</span><select data-run-effort>${taskRunOpts(row.efforts.map(e => [e, e]), pick.effort)}</select></label>` : ''}
    <button type="submit" class="ghost" data-run-model-apply disabled>Apply</button></form>`;
}
// Shown on a bot's task once GET /v2/tasks/{id} has answered (list rows leave out run_model).
function taskRunModelHTML(t) {
  if (!String(t.owner_id || '').startsWith('bot:') || t.run_model === undefined) return '';
  return `<section class="tsec task-run-model" data-task-run-model aria-label="Runs on">${taskRunModelBody(t.run_model, TASK_RUN_ROWS, taskRunPick(t.run_model), taskRunMay(t))}</section>`;
}
// A model change redraws the harness and effort it offers, starting on that model's own (or the task's saved ones when
// it is the model already saved). Apply saves what is picked; a blank model clears the task's own choice.
function taskRunModelBind(d, task) {
  const sec = $('[data-task-run-model]', d);
  if (!sec) return;
  const id = String(task.id), run = task.run_model || null, state = {pick: taskRunPick(run)}, may = taskRunMay(task);
  const same = () => state.pick.model === (run?.model || '') && (!state.pick.model || (state.pick.harness === (run.harness || '')
    && state.pick.effort === (run.reasoning_effort || '')));
  const paint = () => {
    sec.innerHTML = taskRunModelBody(run, TASK_RUN_ROWS, state.pick, may);
    const apply = sec.querySelector('[data-run-model-apply]'); if (apply) apply.disabled = same();
  };
  if (!TASK_RUN_ROWS) void taskRunCatalog().then(() => { if (sec.isConnected && TASK_RUN_ROWS) paint(); });
  sec.onchange = ev => {
    const t = ev.target;
    if (t.matches('[data-run-model]')) {
      const row = (TASK_RUN_ROWS || []).find(m => m.id === t.value);
      state.pick = !row ? taskRunPick(null) : row.id === run?.model ? taskRunPick(run)
        : {model: row.id, harness: row.harnesses?.[0] || '', effort: row.default_effort || row.efforts?.[0] || ''};
      paint(); sec.querySelector('[data-run-model]')?.focus();
      return;
    }
    if (t.matches('[data-run-harness]')) state.pick.harness = t.value;
    if (t.matches('[data-run-effort]')) state.pick.effort = t.value;
    sec.querySelector('[data-run-model-apply]').disabled = same();
  };
  sec.onsubmit = async ev => {
    ev.preventDefault();
    const apply = sec.querySelector('[data-run-model-apply]');
    if (!apply || apply.disabled) return;
    apply.disabled = true;
    const {model, harness, effort} = state.pick;
    const body = model ? Object.fromEntries(Object.entries({model, harness, effort}).filter(([, v]) => v)) : {model: null};
    try {
      const saved = await post(`/v2/tasks/${encodeURIComponent(id)}/model`, body);
      if (saved.warning) toast(saved.warning);
      const data = await get(`/v2/tasks/${encodeURIComponent(id)}`);
      if (TASKS_ST) void tasksLoad(TASKS_ST);
      if (d.open && String(d.dataset.task) === id && data.task) void taskModalShow(data.task, d);
    } catch (e) { toast(e.message, true); apply.disabled = same(); }
  };
}
const TASK_REL_ADD = [['related', 'Related'], ['blocked_by', 'Blocked by'], ['blocks', 'Blocks'], ['duplicate_of', 'Duplicate of'], ['follow_up', 'Follow-up of']];
const TASK_REL_GROUPS = [['blocks', 'in', 'Blocked by'], ['blocks', 'out', 'Blocks'], ['related', 'both', 'Related'], ['duplicate_of', 'out', 'Duplicate of'],
  ['duplicate_of', 'in', 'Duplicated by'], ['follow_up', 'out', 'Follow-up of'], ['follow_up', 'in', 'Follow-ups']];
// One relation added or taken off (POST /v2/tasks/{id}/relations), then the task redrawn. `on` is the task whose
// endpoint takes it, when that is the other end.
async function taskRelate(d, task, body, on = task.id) {
  try {
    await post(`/v2/tasks/${encodeURIComponent(on)}/relations`, body);
    const data = await get(`/v2/tasks/${encodeURIComponent(task.id)}`);
    if (TASKS_ST) void tasksLoad(TASKS_ST);
    if (d.open && String(d.dataset.task) === String(task.id) && data.task) taskModalShow(data.task, d);
  } catch (e) { toast(e.message, true); }
}
// A snooze's or a wait's note after the chip's own text, clipped by CSS; the chip's title carries it in full.
function chipNoteHTML(note) {
  return note ? ` · <span class="tchip-note">${esc(note)}</span>` : '';
}
// A milestone the task waits for besides tasks (backend/milestone_waits.py): "PR #943 to merge", then "merged".
function taskMilestoneHTML(t) {
  const waits = Array.isArray(t.milestone_waits) ? t.milestone_waits : [];
  if (!waits.length) return '';
  const done = w => w.kind === 'pr_merged' ? 'merged' : 'published';
  return `<div class="trel task-milestones"><span class="trel-k">Waits for</span><div class="tlinks">${waits.map(w => {
    const text = `${w.label} ${w.met ? done(w) : w.kind === 'pr_merged' ? 'to merge' : 'to be published'}`;
    return `<span class="tlink milestone${w.met ? ' met' : ''}" title="${esc(w.sentence + (w.note ? ` · ${w.note}` : ''))}">${w.url
      ? `<a href="${esc(w.url)}" target="_blank" rel="noopener">${esc(text)}</a>` : esc(text)}${chipNoteHTML(w.note)}</span>`;
  }).join('')}</div></div>`;
}
function taskRelHTML(t) {
  const groups = TASK_REL_GROUPS.map(([kind, dir, label]) => [kind, dir, label, taskRels(t, kind, dir)]).filter(g => g[3].length);
  const milestones = taskMilestoneHTML(t);
  return `<section class="tsec task-related${groups.length || milestones ? '' : ' tsec-empty'}"><h3 class="rail-h">Related<button type="button" class="prop-add" data-related-add aria-haspopup="menu" aria-label="Relate a task" title="Relate a task">${TL_ICON.plus}</button></h3>
    ${groups.map(([kind, dir, label, rows]) => `<div class="trel"><span class="trel-k">${esc(label)}</span><div class="tlinks">${rows.map(k => `<span class="tlink task"><button type="button" class="linkish" data-open-task="t${esc(k.id)}" aria-label="Open ${esc(k.title)}">${taskStatusText(k)} ${esc(clipLine(k.title, 70))}</button><button type="button" class="x" data-drop-rel="${esc(kind)} ${esc(dir)} ${esc(k.id)}" aria-label="Remove ${esc(label.toLowerCase())}: ${esc(k.title)}">×</button></span>`).join('')}</div></div>`).join('')}${milestones}</section>`;
}
// ---- saving a task's properties. One save at a time per dialog, each with the version the last one returned, so a
// quick second edit never trips over the first. The dialog redraws at once with the task the server sent back; the
// list reloads behind it. A refused save shows its reason (under the properties) until that property next saves.
function taskSaveQueued(d, id, body, then, field) {
  field = field || d.focusProp || (typeof body === 'function' ? 'task' : Object.keys(body).find(k => k !== 'note')) || 'task';
  d.saving = (d.saving || 0) + 1;
  const run = () => taskSave(d, id, body, then, field).finally(() => { d.saving--; });
  return (d.saveQueue = (d.saveQueue || Promise.resolve()).then(run, run));
}
async function taskSave(d, id, body, then, field) {
  const here = () => d.open && String(d.dataset.task) === id;
  let cur = d.liveTask && String(d.liveTask.id) === id ? d.liveTask : null;
  if (!cur) cur = (await v2Get(`/v2/tasks/${encodeURIComponent(id)}`))?.task;
  if (!cur) { toast('That task could not be loaded.', true); return false; }
  if (typeof body === 'function') { body = body(cur); if (!body) return true; }
  if (!d.propsErrs || d.propsErrs.id !== id) d.propsErrs = {id, map: new Map()};
  const errs = d.propsErrs.map;
  let note = '';
  try {
    const outcome = body.close ? 'closed' : pipelineActionStatus(cur, body);
    if ((outcome === 'done' || outcome === 'closed') && outcome !== cur.status) {
      note = taskOutcomeNote(cur, outcome === 'closed' ? {close: true} : body, TASK_DRAFTS.get(String(id)));
      if (note) body = {...body, note};
    }
    const state = d.dataset.peek && TASKS_ST, key = 't' + id;
    const at = state ? tasksVisibleKeys().indexOf(key) : -1;
    const path = `/v2/tasks/${encodeURIComponent(id)}`;
    const data = body.snooze ? await post(path + '/snooze', {until: body.snooze})
      : body.unsnooze ? await post(path + '/unsnooze', {})
      : await post(path, {version: cur.version, ...body});
    if (note) taskOutcomeTaken(cur);
    errs.delete(field);
    if (data.task && String(d.liveTask?.id) === id) d.liveTask = data.task;
    if (here() && then !== false && data.task) void taskModalShow(data.task, d);       // the server's answer, at once
    if (BOT) {
      if (isKeeper(BOT.slug)) void loadBotTasksV2(BOT.slug);
      void loadBotChatTasks(BOT.slug);
    }
    void personTasksReload();
    // The list reloads behind it. If the task left the view (done, closed, handed on, declined), the list and the
    // peek move on to the next row: checked by whichever load lands last (a poll may overtake this one). Not for a
    // task reached from inside the peek (a parent's subtask): its row's neighbour is some other task, and edits made
    // after the save would land on it, so the peek stays on the task that was saved.
    if (state && state.peekVia !== 'link') state.pendingFinish = {key, at, after: state.loadSeq};
    // The list shows the saved task at once (a card moves to its new step); the load that follows brings the rest.
    if (TASKS_ST && data.task && TASKS_ST.tasks?.some(x => String(x.id) === String(data.task.id))) {
      TASKS_ST.tasks = mergeTaskRows(TASKS_ST.tasks, [data.task]);
      tasksRender(TASKS_ST);
    }
    if (TASKS_ST) void tasksLoad(TASKS_ST);
    roadmapRankingRefresh();
    return true;
  } catch (e) {
    if (!here()) { toast(e.message, true); return false; }
    errs.set(field, e.status === 409 ? 'Changed elsewhere. Showing the latest.' : `Not saved: ${e.message || 'refused'}`);
    const fresh = await v2Get(`/v2/tasks/${encodeURIComponent(id)}`);
    if (fresh?.task && here()) {
      d.liveTask = fresh.task;
      void taskModalShow(fresh.task, d);
      if (TASKS_ST) void tasksLoad(TASKS_ST);
    } else taskPropsMsgPaint(d);
    return false;
  }
}
// ---- the conversation: the task's session as a chat. What people and the bot said are messages (the bot's replies
// open to the steps behind them), what changed is a one-line marker between them, and while the owner's run works a
// "Working" line shows its newest step, with Stop for whoever may stop it. The box says it in the task (a comment),
// which wakes its bot. `taskChatStop` and `taskChatLoad` keep their names: the router and the chat cards call them.
let TASK_CHAT = null;
let TASK_JUMP = ENTRY_JUMP?.task ? {task: ENTRY_JUMP.task, id: ENTRY_JUMP.id} : null;   // {task, id}: a link's message, once that task's conversation shows
const TASK_DRAFTS = new Map();         // an unsent comment, per task, kept across redraws until it is sent or cleared
function taskChatStop() {
  if (TASK_CHAT) { clearInterval(TASK_CHAT.poll); TASK_CHAT.liveOff?.(); }
  TASK_CHAT = null;
  liveTasksSync();                     // the router calls this on every page change too (ui/app/live.js)
}
// The open task changed, or a comment landed in its conversation (live events, ui/app/live.js): read it again, with
// its files (a new file or version, or an answer on one), once per burst.
function taskChatLive(state) {
  if (!taskChatCurrent(state) || state.stopped) { if (TASK_CHAT === state && !state.dialog.open) taskChatStop(); return; }
  liveSoon('task:' + state.id, () => {
    if (!taskChatCurrent(state) || state.stopped) return;
    void taskChatRead(state);
    tfLoad(state.dialog, state.id).catch(() => {});
  }, 200);
}
function taskChatCurrent(state) { return TASK_CHAT === state && state.dialog.open; }
const commentAuthor = (a, via) => {
  if (a === 'external:roadmap') return '<span class="who">Visitor</span>';
  const pid = actorPerson(a);
  // The Assistant acted for this person (docs/assistant.md): "<name> (via Assistant)".
  const by = via === 'assistant' ? ' (via Assistant)' : '';
  if (pid) return `${personCircle(personDisplay(pid), 16)}<span class="who">${esc((pid === S.me?.id ? 'You' : personDisplay(pid)) + by)}</span>`;
  if (a === 'keeper') return `<span class="who">${esc(assistantName())}</span>`;
  const slug = actorSlug(a);
  return `${slug ? avatar(slug, 16, stateOf(slug)) : ''}<span class="who">${esc(actorLabel(a))}</span>`;
};
const TASK_EVENT_WORDS = {step: id => id ? `moved it to ${pipelineStepName(id)}` : 'cleared the step', type: id => `set the type to ${TASK_TYPES.find(type => type.id === id)?.name || id}`, status: s => `moved it to ${STATUS_WORD[s] || s}`, owner: v => `handed it to ${actorLabel(v)}`,
  lane: v => `moved it to the ${v === 'company' ? 'team' : v} lane`, labels: v => { try { const l = JSON.parse(v || '[]'); return l.length ? `set the tags: ${l.join(', ')}` : 'removed the tags'; } catch { return 'changed the tags'; } },
  blocked_by: v => v ? 'marked it blocked' : 'cleared the block', parent_id: v => v ? 'filed it under a parent task' : 'took it out of its parent',
  subtask: v => v ? 'added a subtask' : 'moved a subtask out', blocks: v => v ? 'made it block another task' : 'stopped it blocking a task',
  related: v => v ? 'attached a related task' : 'removed a related task', duplicate_of: v => v ? 'marked it a duplicate' : 'unmarked it as a duplicate',
  duplicated_by: v => v ? 'marked a duplicate of it' : 'unmarked a duplicate of it', follow_up_of: v => v ? 'marked it a follow-up' : 'unmarked it as a follow-up',
  follow_ups: v => v ? 'added a follow-up' : 'removed a follow-up',
  link: v => v ? `linked ${v}` : 'removed a link', file: (v, x) => v ? `added ${v}` : `removed ${x.old || 'a file'}`, due: v => v ? `set the due date to ${fmt(v)}` : 'cleared the due date',
  qa_waiver: v => { let w = {}; try { w = JSON.parse(v || '{}'); } catch { /* an unreadable record still says what it is */ }
    return `waived QA${w.head ? ` on ${String(w.head).slice(0, 7)}` : ''}: a waiver, not a QA pass`; },
  verdict: v => `recorded the verdict ${v || 'none'}`,
  lint: v => `noted: ${v}`, rank: () => 'moved it in the queue', children: v => String(v || ''), note: v => `noted: ${clipLine(String(v || ''), 200)}`, comment: () => 'deleted a comment'};
// `files`: the task's files (ui/app/task-files.js), for the files a comment carried.
function commentLineHTML(x, i, all, files = [], taskId = '', canAnswer = true) {
  if (x.kind === 'event') {
    if (x.field === 'status' && x.old == null) return '';           // created: the header says so
    // The type and first step a task is filed with are part of its creation too, not moves.
    if ((x.field === 'type' || x.field === 'step') && x.old == null
      && all?.some(y => y.kind === 'event' && y.field === 'status' && y.old == null && Math.abs(Date.parse(y.ts) - Date.parse(x.ts)) < 2000)) return '';
    if (x.field === 'comment' && x.new) return '';                  // an edit: the comment itself says edited
    // A step move already names where the task went; its status change would say it twice.
    if (x.field === 'status' && all?.some(y => y.kind === 'event' && y.field === 'step' && y.new && y.ts === x.ts)) return '';
    // A note saved with a status change or a comment is already shown there; a note on its own says what it is.
    if (x.field === 'note') {
      const text = String(x.new || '').trim(), at = Date.parse(x.ts);
      const near = y => Math.abs(Date.parse(y.ts) - at) < 5000;
      if (!text || all?.some(y => near(y) && ((y.kind === 'event' && y.field === 'status' && y.note && text.startsWith(String(y.note).trim().slice(0, 40)))
        || (y.kind === 'comment' && String(y.message?.body || '').trim() === text)))) return '';
    }
    const say = TASK_EVENT_WORDS[x.field] ? TASK_EVENT_WORDS[x.field](x.new, x) : `changed ${x.field}`;
    // A result mirrored into a comment keeps its full text there; the event still records the status move.
    const mirrored = x.note && all?.some(y => y.kind === 'comment' && y.message?.sender_id === x.actor
      && String(y.message.body || '').trim() === String(x.note).trim() && Math.abs(Date.parse(y.ts) - Date.parse(x.ts)) < 5000);
    // A QA waiver is a person's decision, never a verdict: it keeps its own mark and its full reason.
    const waived = x.field === 'qa_waiver' ? ' <span class="verdict-chip waived" data-qa-waiver>QA waived</span>' : '';
    return `<div class="tcomment sys tmark${waived ? ' qa-waiver' : ''}"><span class="tcomment-who">${commentAuthor(x.actor, x.via)}</span>${waived} <span class="muted">${esc(say)}${x.note && x.field !== 'note' && !mirrored ? ` — ${esc(waived ? x.note : clipLine(x.note, 200))}` : ''}</span>
      <time class="muted tnum" title="${esc(fmt(x.ts))}">${esc(ago(x.ts))}</time></div>`;
  }
  const m = x.message, ask = askOf(m);
  if (m.refs?.reconnect && typeof m.refs.reconnect === 'object') return reconnectCardHTML(m);
  // a question's text is in its block; a body that only repeats it is left out
  const body = ask && ask.questions.some(q => String(q.question || '').trim() === String(m.body || '').trim()) ? '' : m.body || '';
  const kind = m.kind === 'ask' ? '<span class="pill needs">question</span>' : m.kind === 'answer' ? '<span class="pill">answer</span>' : '';
  // As in a bot's chat with several people: a person's line is a bubble on the right under their name (yours needs
  // none), the bot's reads as page text under its own.
  const person = !!actorPerson(m.sender_id), me = m.sender_id === myActor();
  return `<div class="tcomment tmsg${person ? ' you' : ''}${me ? ' from-me' : ''}${String(m.sender_id || '').startsWith('bot:') ? ' bot' : ''}" data-message="${esc(m.id || '')}"><div class="tcomment-head"><span class="tcomment-who">${commentAuthor(m.sender_id, m.refs?.via)}</span>${kind}${roadmapCommentMark(m, taskId)}
      ${m.refs?.quiet ? '<span class="muted" title="Saved for the bot\'s next run on this task">saved</span>' : ''}
      <span class="spacer"></span>${m.edited_at ? `<span class="muted" title="Edited ${esc(fmt(m.edited_at))}">edited</span>` : ''}<time class="muted tnum" title="${esc(fmt(m.created_at))}">${esc(ago(m.created_at))}</time></div>
    <div class="bubble ${person ? 'you' : 'bot'}">${body ? `<div class="md">${safeMd(body)}</div>` : ''}
    ${tfCommentFilesHTML(m, files)}${ask ? askHTML(ask, m.answers, askTargetOf(m), m.sender_id, taskId, files, canAnswer) : ''}</div>${taskStepsHTML(m)}</div>`;
}
// A bot's work on this task stopped on an account (backend/reconnects.py): its one line, the fix for the person who
// can make it, and Reconnected once it works. A missing secret's fix is its credential card (ui/credential-card.js).
function reconnectCardHTML(m) {
  const r = m.refs.reconnect, fix = r.fix || {}, status = r.status || 'open', fixed = status === 'fixed', mine = r.person === myActor();
  const head = `<div class="tcomment-head"><span class="tcomment-who">${commentAuthor(m.sender_id)}</span><span class="spacer"></span><time class="muted tnum" title="${esc(fmt(m.created_at))}">${esc(ago(m.created_at))}</time></div>`;
  const wrap = inner => `<div class="tcomment tmsg bot" data-message="${esc(m.id || '')}">${head}${inner}</div>`;
  // A retired card asks nothing: its request is cancelled, so it shows Not needed, never a card to save.
  if (r.service === 'secret' && mine && m.refs.credential_request && status !== 'retired') {
    return wrap(`<div data-credential-host="${esc(m.refs.credential_request)}"><div class="cc-done">Loading the card…</div></div>`);
  }
  let act = '';
  if (fixed) act = `<span class="rc-done">${r.service === 'secret' ? 'Saved' : 'Reconnected'}</span>`;
  else if (status !== 'open') act = `<span class="muted">${status === 'cancelled' ? 'Cancelled' : 'Not needed'}</span>`;
  else if (!mine) act = `<span class="muted">Waiting on ${esc(actorLabel(r.person) || 'a person')}</span>`;
  else if (r.service === 'google') act = '<button type="button" class="primary" data-reconnect-google>Reconnect Google</button>';
  else if (r.service === 'login' && typeof harnessCaps === 'function' && harnessCaps(fix.runtime).browser_sign_in) {
    act = `<button type="button" class="primary" data-model-login data-runner="${esc(fix.runner || '')}" data-runtime="${esc(fix.runtime || '')}" data-machine="${esc(fix.machine || '')}">Sign in</button>`;
  } else act = `<a class="button primary" href="#/settings/computers">${r.service === 'login' ? 'Sign in' : 'Open settings'}</a>`;
  return wrap(`<div class="rc-card" data-reconnect="${esc(r.key || '')}" data-status="${esc(status)}"><span class="rc-line">${esc(m.body || '')}</span>${act}</div>`);
}
// Reconnect Google: the person's own Connect, as Settings > Inbox starts it (ui/google-connect.js).
document.addEventListener('click', async ev => {
  const button = ev.target.closest('[data-reconnect-google]');
  if (!button) return;
  button.disabled = true;
  try {
    const started = await post('/v2/mail/google/connect', {});
    // Only Google's own sign-in page, never an address from anywhere else.
    if (!/^https:\/\/accounts\.google\.com\//.test(started.auth_url || '')) throw new Error('Unexpected sign-in address');
    location.href = started.auth_url;
  } catch (e) { toast(e.message, true); button.disabled = false; }
});
// A reply's run, folded to one line ("6 steps · 4 tool calls · 2m"); opening it reads the steps once (GET
// /v2/turns/{id}/steps) and they stay open across redraws. `TASK_STEPS` holds what was read, per turn.
const TASK_STEPS = {html: new Map(), live: new Map(), open: new Set(), reading: new Set()};
function taskStepsFold(turn, label, live = false) {
  // a working run's fold keeps the steps last read while the next read is on its way
  const open = TASK_STEPS.open.has(turn), html = (live ? TASK_STEPS.live : TASK_STEPS.html).get(turn);
  return `<details class="run-steps" data-turn="${esc(turn)}"${live ? ' data-live' : ''}${open ? ' open' : ''}><summary>${label}</summary><div class="steps-list">${
    html || '<div class="step muted">Loading…</div>'}</div></details>`;
}
function taskStepsHTML(m) {
  const run = m.run || {}, turn = m.refs?.turn_id;
  if (!run.steps || !turn) return '';
  return taskStepsFold(turn, esc([`${run.steps} step${run.steps === 1 ? '' : 's'}`,
    run.tool_calls ? `${run.tool_calls} tool call${run.tool_calls === 1 ? '' : 's'}` : '', tookWords(run.took_s)].filter(Boolean).join(' · ')));
}
async function taskStepsOpen(el) {
  const turn = el.dataset.turn, live = el.hasAttribute('data-live');
  if (!el.open) { TASK_STEPS.open.delete(turn); return; }
  TASK_STEPS.open.add(turn);
  // A finished run's steps are read once; a working run's are read again each time it is opened or moves on.
  if (!live && TASK_STEPS.html.has(turn) || TASK_STEPS.reading.has(turn)) return;
  TASK_STEPS.reading.add(turn);
  let html;
  try {
    html = v2StepsHTML(await get(`/v2/turns/${encodeURIComponent(turn)}/steps`));
    (live ? TASK_STEPS.live : TASK_STEPS.html).set(turn, html);
  } catch (error) {
    html = `<div class="step err">${esc(error.status === 403 ? 'Only people who may read this bot’s runs see its steps.' : error.message)}</div>`;
  }
  finally { TASK_STEPS.reading.delete(turn); }
  // a redraw while it was read put a new fold in its place
  const list = $(`.task-comments details.run-steps[data-turn="${CSS.escape(turn)}"] .steps-list`, document);
  if (list) list.innerHTML = html;
}
// The line under the thread while a run answers it: the bot chat's pending line (pendingRunText, ui/app/chat.js), so
// Sent, Queued and why, Starting, "<Bot> is working" with its newest step, or Reply failed. `execution` is the owner's
// run or the run for a person's message, whichever bot that went to (GET /v2/tasks/{id}); a queued run for anything
// else is `waiting_for`'s. The steps so far are one click away, and Stop is there for whoever manages the bot
// (POST /v2/bots/{slug}/stop, as in the bot's chat). `sent`: the id of a message just sent; until its own run is read,
// it is Sent, never the state of an earlier message's run.
function taskLiveHTML(x, stopping = '', sent = '') {
  if (sent && x?.message_id !== sent) x = null;
  const working = !!x && ['leased', 'running'].includes(x.state);
  const run = working || x?.from_person && v2RunLive(x) ? x : null;
  if (!run && !sent) return '';
  const t = pendingRunText(run, run?.bot);
  // Several bots may answer on a task, so the line names the one at work.
  const word = t.word || t.tip;
  const part = working ? (x.parts || []).at(-1) : null;
  const step = part ? clipLine(part.text.replace(/\s+/g, ' ').trim(), 120) : '';
  const label = `<span class="typing ${t.mode}" aria-hidden="true"><i></i><i></i><i></i></span><span class="thinking-word">${esc(word)}${
    working ? '…' : ''}</span>${step ? `<span class="task-live-step">${esc(step)}</span>` : ''}`;
  const stop = working && x.run_id && settingsCanManageBot(botRow(x.bot))
    ? `<button type="button" class="ghost task-live-stop" data-task-stop="${esc(x.run_id)}" data-bot="${esc(x.bot)}" ${
      stopping === x.run_id ? 'title="Stopping…" disabled>Stopping…' : 'title="Stop this run">Stop'}</button>` : '';
  return `<div class="thinking task-live" role="status" aria-label="${esc(t.tip)}${step ? ': ' + esc(step) : ''}">${
    working && x.run_id ? taskStepsFold(x.run_id, label, true) : label}${pendingHintHTML(run)}${stop}</div>`;
}
// Whether the draft @mentions one of `names`, as the server reads an @mention (backend/hubdb.py _at): the name, not
// inside a word or another @name.
const taskNamed = (names, draft) => (names || []).some(n =>
  new RegExp('(?<![\\w@])@' + n.replace(/[.*+?^$()|[\]\\{}]/g, '\\$&') + '(?![\\w-])', 'i').test(draft || ''));
// Who the comment being written will reach (GET /v2/tasks/{id} `comment_to`), said before it is sent: no bot at all,
// or a bot that reads it on its next run on the task unless the draft @mentions it.
function taskComposerTo(to, draft) {
  if (!to) return '';
  if (!to.bot) return 'No bot gets this message: it is saved on the task for the people on it.';
  if (to.wakes || taskNamed(to.mention, draft)) return '';
  return `Saved for ${botDisplayName(to.bot)}'s next run on this task.${to.mention?.length ? ` Write @${to.mention[0]} to ask now.` : ''}`;
}
// How a comment reaches its bot (docs/tasks.md "Comments"), in the bot chat's words (deliveryMenuHTML, ui/app/pill.js):
// Steer into its running turn, Queue after its current turn, Defer until its next turn on this task (at the latest
// after a while), or Set as goal of its task session. With no pick nothing is sent and the server decides; a pick is
// kept per task. The key is new because the first version's "queue" meant what Defer means now.
const TASK_DELIVERY_KEY = 'hub.task.delivery.v2';
const TASK_DELIVERY_MAX = 200;
let TASK_MENU_CLOSE = null;                 // closes the open delivery menu; a press elsewhere closes it
document.addEventListener('pointerdown', ev => {
  if (TASK_MENU_CLOSE && !ev.target.closest?.('.task-send')) TASK_MENU_CLOSE();
});
// [task id, choice] pairs, oldest first, so the cap drops the task chosen longest ago.
function taskDeliveryRows() {
  try {
    const rows = JSON.parse(localStorage.getItem(TASK_DELIVERY_KEY) || '[]');
    return Array.isArray(rows) ? rows.filter(r => Array.isArray(r) && r.length === 2) : [];
  } catch { return []; }
}
function taskDeliveryStore(id, mode) {
  try {
    const rows = taskDeliveryRows().filter(([k]) => k !== String(id));
    localStorage.setItem(TASK_DELIVERY_KEY, JSON.stringify([...rows, [String(id), mode]].slice(-TASK_DELIVERY_MAX)));
  } catch {}
}
// The send is split only where a bot would get the comment (GET /v2/tasks/{id} `comment_delivery`, null without one).
const taskDeliverySplit = state => state.data?.comment_delivery?.default != null && !!state.data?.comment_to?.bot;
// The ways this reader may send: steer only for someone who may steer, queue only for someone who may wake the bot,
// goal only where the session can take one; anyone who may comment may defer.
function taskDeliveryWays(state) {
  const cd = state.data?.comment_delivery || {};
  return ['steer', 'queue', 'defer', 'goal'].filter(w => w === 'defer' || (w === 'queue' ? cd.queue !== false : !!cd[w]));
}
const taskDeferDefault = state => {
  const f = state.data?.comment_delivery?.default_for;
  return DELIVERY_FOR.includes(f) ? f : '24h';
};
// The way the person picked for this task from the caret menu, or null.
function taskDeliveryPicked(state) {
  const saved = String(taskDeliveryRows().find(([k]) => k === String(state.id))?.[1] || '');
  const way = deliveryWay(saved);
  if (!taskDeliveryWays(state).includes(way)) return null;
  return way !== 'defer' || DELIVERY_FOR.includes(deliveryFor(saved)) ? saved : null;
}
// The way the next comment is shown to go: the pick, else the way the server reads the draft (queue when it @mentions
// the bot, else the server's default; a reader who may not wake the bot only defers).
function taskDeliveryMode(state) {
  const picked = taskDeliveryPicked(state);
  if (picked) return picked;
  const ways = taskDeliveryWays(state), to = state.data?.comment_to, draft = $('textarea', state.host)?.value;
  let way = taskNamed(to?.mention, draft) && ways.includes('queue') ? 'queue' : state.data?.comment_delivery?.default;
  if (!ways.includes(way)) way = ways.includes('queue') ? 'queue' : 'defer';
  return way === 'defer' ? 'defer:' + taskDeferDefault(state) : way;
}
// What the chosen way does, said before sending.
function taskDeliveryHint(state) {
  const who = botDisplayName(state.data.comment_to.bot), mode = taskDeliveryMode(state), way = deliveryWay(mode);
  const cd = state.data.comment_delivery, mention = state.data.comment_to.mention || [];
  if (way === 'queue') return `Queue: ${who} gets it after its current turn, or now.`;
  if (way === 'goal') return `Set as goal: ${who} works toward this text in the task's session.`;
  if (way === 'steer') return `Steer: into ${who}'s running turn now${cd.steers ? '' : ", or after it if it can't steer"}.`;
  const f = deliveryFor(mode);
  const nudge = cd.default === 'defer' && mention.length && !taskDeliveryPicked(state) && taskDeliveryWays(state).includes('queue')
    ? ` Write @${mention[0]} to queue it now.` : '';
  return `Defer: ${who} gets it with its next turn on this task${f === 'never' ? '' : `, at the latest in ${f}`}.${nudge}`;
}
// The main button says the way the comment is sent; without a bot it stays the plain arrow.
function taskDeliveryPaint(state) {
  const main = $('[data-task-main]', state.host), caret = $('[data-task-caret]', state.host);
  if (!main || !caret) return;
  const split = taskDeliverySplit(state), mode = split ? taskDeliveryMode(state) : 'icon';
  caret.hidden = !split;            // Defer always has its durations to pick from
  if (!split) taskDeliveryMenu(state, false);
  if (main.dataset.shows !== mode) {
    main.dataset.shows = mode;
    main.classList.toggle('p-send-goal', split);
    main.innerHTML = split ? esc(deliveryWord(mode)) : ICON_SEND;
  }
  main.setAttribute('aria-label', split ? `Send: ${deliveryWord(mode)}` : 'Send');
  if (!main.disabled) main.title = split ? taskDeliveryHint(state) : 'Send';
}
// The ways to send, opening upward from the caret; choosing one keeps the draft and only sets the way.
function taskDeliveryMenu(state, open) {
  const menu = $('[data-task-menu]', state.host), caret = $('[data-task-caret]', state.host);
  if (!menu || !caret) return;
  if (!open && menu.hidden) return;
  const ways = taskDeliveryWays(state), mode = open ? taskDeliveryMode(state) : '';
  menu.innerHTML = open ? deliveryMenuHTML({steer: ways.includes('steer'), queue: ways.includes('queue'), goal: ways.includes('goal'),
    current: mode, deferFor: deliveryWay(mode) === 'defer' ? deliveryFor(mode) : taskDeferDefault(state)}) : '';
  menu.hidden = !open;
  caret.setAttribute('aria-expanded', String(open));
  TASK_MENU_CLOSE = open ? () => taskDeliveryMenu(state, false) : null;
  if (open) (menu.querySelector('[aria-checked="true"]') || menu.querySelector('button'))?.focus();
}
function taskComposerToPaint(state) {
  const hint = $('[data-task-chat-to]', state.host), box = $('textarea', state.host);
  if (hint) hint.textContent = taskDeliverySplit(state) ? taskDeliveryHint(state)
    : taskComposerTo(state.data?.comment_to, box?.value);
  taskDeliveryPaint(state);
}
async function taskRunStop(state, button) {
  button.disabled = true; button.title = 'Stopping…';
  try {
    await post(`/v2/bots/${encodeURIComponent(button.dataset.bot)}/stop`, {run_id: button.dataset.taskStop});
    state.stopping = button.dataset.taskStop;      // the redraws keep "Stopping…" until the run has ended
    button.textContent = 'Stopping…';
    taskChatLive(state);
  } catch (e) { button.disabled = false; button.title = 'Stop this run'; toast(`Not stopped: ${e.message}`, true); }
}
function taskCommentsRender(state, data, sent = '') {
  const host = state.host;
  const lines = [
    ...(data.comments || data.messages || []).filter(m => ['say', 'ask', 'answer'].includes(m.kind)).map(m => ({kind: 'comment', ts: m.created_at, message: m})),
    ...(data.events || []).map(e => ({kind: 'event', ts: e.ts, ...e})),
  ].sort((a, b) => String(a.ts).localeCompare(String(b.ts)));
  if (!$('form', host)) {
    // The box is the main chat's composer: a pill, one line that grows, the send arrow, Return sends.
    host.innerHTML = `<h3>Conversation</h3>
      <div class="task-comments" role="log" aria-live="polite"></div>
      <form class="task-pill"><span class="muted" data-task-chat-to role="note"></span><div class="p-pill"><textarea class="p-text" rows="1" maxlength="4000" required aria-label="Message" placeholder="Message…"></textarea>
          <span class="task-send" data-task-send><button class="p-send p-send-icon" type="submit" aria-label="Send" title="Send" data-task-main>${ICON_SEND}</button><button class="task-send-caret" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="Ways to send" title="Ways to send" data-task-caret hidden><span class="nav-icon" aria-hidden="true">expand_more</span></button><div class="p-menu p-ways" role="menu" aria-label="Ways to send" data-task-menu hidden></div></span></div>
          <span class="muted" data-task-chat-status role="status"></span></form>`;
    $('form', host).onsubmit = ev => { ev.preventDefault(); void taskCommentSend(state); };
    const caret = $('[data-task-caret]', host), menu = $('[data-task-menu]', host);
    caret.onclick = () => taskDeliveryMenu(state, menu.hidden);
    menu.onclick = ev => {
      const pick = ev.target.closest?.('[data-deliver]');
      if (!pick) return;
      taskDeliveryStore(state.id, pick.dataset.deliver);
      taskDeliveryMenu(state, false); taskComposerToPaint(state); $('textarea', host).focus();
    };
    menu.addEventListener('keydown', ev => deliveryMenuKeys(menu, ev));
    // Escape anywhere in the form (Safari leaves focus in the box) closes an open menu, not the task.
    $('form', host).addEventListener('keydown', ev => {
      if (ev.key !== 'Escape' || menu.hidden) return;
      ev.preventDefault(); ev.stopPropagation();      // closes the menu, not the task
      taskDeliveryMenu(state, false); caret.focus();
    });
    const box = $('textarea', host);
    box.value = TASK_DRAFTS.get(String(state.id)) || '';
    const grow = () => { box.style.height = 'auto'; box.style.height = box.scrollHeight + 'px'; };
    box.oninput = () => {
      grow(); taskComposerToPaint(state);
      if (box.value) TASK_DRAFTS.set(String(state.id), box.value); else TASK_DRAFTS.delete(String(state.id));
    };
    if (box.value) requestAnimationFrame(grow);
    // As in the main chat: on a computer Return sends and Shift+Return is a new line; on a phone Return is a line.
    box.onkeydown = ev => {
      if (ev.key !== 'Enter' || ev.isComposing) return;
      if (ev.metaKey || ev.ctrlKey || (!ev.shiftKey && !touchKeyboard())) { ev.preventDefault(); void taskCommentSend(state); }
    };
    const thread = $('.task-comments', host);
    thread.addEventListener('toggle', ev => { if (ev.target.matches?.('details.run-steps')) void taskStepsOpen(ev.target); }, true);
    thread.addEventListener('click', ev => {
      const stop = ev.target.closest('[data-task-stop]');
      if (stop && taskChatCurrent(state)) void taskRunStop(state, stop);
    });
  }
  state.data = data;
  taskComposerToPaint(state);
  // `can_comment`: false for someone who may read the task but not comment on it; they get no box and no answer controls
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
  const wait = live || !data.waiting_for ? live : `<p class="muted task-wait" role="status">${esc(data.waiting_for)}</p>`;
  const html = (lines.map((x, i, all) => commentLineHTML(x, i, all, files, state.id, can)).join('') || '<p class="muted">Nothing said yet.</p>') + wait;
  const thread = $('.task-comments', host);
  if (!can) d.askDrafts?.clear();
  if (can && askBusy(thread)) return;     // never under someone answering
  if (html !== state.rendered) {
    // At the newest comment, new ones keep it in view; scrolled up, nothing moves (ui/app/stick-scroll.js).
    const stick = stickScroll(thread);
    if (!state.rendered) stick.toBottom();
    stick.update(() => { visualSwap(thread, html); askDraftRestore(thread, d); }, {fresh: !!state.rendered});
    // A working run's steps, left open, follow it as it moves on.
    const liveSteps = $('details.run-steps[data-live][open]', thread);
    if (liveSteps) void taskStepsOpen(liveSteps);
    roadmapCommentsBind(state);
    if (thread.querySelector('[data-credential-host]')) void window.credentialCards?.mount(thread, {get, post, esc, toast,
      reload: () => taskChatRead(state)});
    state.rendered = html;
  }
}
async function taskChatRead(state) {
  if (!taskChatCurrent(state) || state.sending || state.reading) return;
  state.reading = true;
  try {
    const data = await get(state.path);
    if (data?.task?.conversation_id) state.cid = data.task.conversation_id;
    if (taskChatCurrent(state) && !state.sending) taskCommentsRender(state, data);
  } catch (e) {
    if (!taskChatCurrent(state)) return;
    if ([403, 404].includes(e.status)) {
      state.stopped = true; clearInterval(state.poll); state.liveOff?.();
      TASK_DRAFTS.delete(String(state.id)); PROP_TASKS = null;
      state.dialog.close(); state.dialog.innerHTML = '';
      if (TASKS_ST) void tasksLoad(TASKS_ST);
      return;
    }
    if (e.status === 400) {state.stopped = true; clearInterval(state.poll); state.liveOff?.();}
    const status = $('[data-task-chat-status]', state.host);
    if (status) status.textContent = `Unable to refresh: ${e.message}`;
    else state.host.innerHTML = `<p class="err" role="status">Conversation unavailable: ${esc(e.message)}</p>`;
  } finally { state.reading = false; }
}
async function taskChatLoad(id, dialog, data = null) {
  const state = TASK_CHAT = {dialog, host: $('.task-chat', dialog),
    path: `/v2/tasks/${encodeURIComponent(id)}`, id, poll: 0, sending: false, reading: false};
  liveTasksSync();
  if (data?.task?.conversation_id) state.cid = data.task.conversation_id;
  if (data) taskCommentsRender(state, data); else await taskChatRead(state);
  if (!taskChatCurrent(state) || state.stopped) return;
  taskJumpPending(state);
  // Without live events, the poll they replaced.
  state.poll = liveFallback(() => {
    if (document.hidden || !taskChatCurrent(state)) return;
    void taskChatRead(state);
    tfLoad(state.dialog, state.id).catch(() => {});     // a new file or version, or an answer on one, without reopening
  }, 8000);
  if (state.poll) return;
  // A change to the task itself reaches taskChatLive from the page's task events (ui/app/refresh.js); its comments
  // are messages in its conversation.
  const cid = state.cid;
  const offs = cid ? [liveFollow(cid), liveOn('messages', d => { if (d.conversation_id === cid) taskChatLive(state); }),
    liveOn('runs', d => {
      if (d.conversation_id !== cid) return;
      if (!d.output) { taskChatLive(state); return; }
      // Output moves the "Working" line on: the thread alone, at most every 1.5 s and not while the tab is hidden.
      liveThrottle('task-run:' + state.id, () => { if (taskChatCurrent(state) && !state.stopped) void taskChatRead(state); }, 1500);
    })] : [];
  state.liveOff = () => { offs.forEach(off => off()); state.liveOff = null; };
}
// A link from outside Tico (the Slack visuals line) names a message: bring it into view and mark it for a moment.
// The task opened only because the reader may read it; a message not on the page loaded says so.
function taskJumpPending(state) {
  if (!TASK_JUMP || ![String(state.id), state.data?.task?.id].includes(TASK_JUMP.task)) return;
  const {id} = TASK_JUMP; TASK_JUMP = null;
  const thread = $('.task-comments', state.host), el = thread?.querySelector(`[data-message="${CSS.escape(id)}"]`);
  if (!el) { toast('That message is not in this conversation', true); return; }
  stickScroll(thread).release();           // the first paint follows the newest comment; the jump stops that
  el.scrollIntoView({block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
  el.classList.add('jump-mark');
  setTimeout(() => el.classList.remove('jump-mark'), 2000);
}
async function taskCommentSend(state) {
  if (!taskChatCurrent(state) || state.sending) return;
  const form = $('form', state.host);
  const box = $('textarea', form), text = box.value.trim();
  if (!text) { box.focus(); return; }
  state.sending = true;
  const button = $('button[type=submit]', form), caret = $('[data-task-caret]', form);
  const status = $('[data-task-chat-status]', form);
  const split = taskDeliverySplit(state), shown = split ? deliveryWay(taskDeliveryMode(state)) : '';
  // A disabled Send says why on hover; with an aria-label, the title is its screen-reader description too.
  taskDeliveryMenu(state, false);
  button.disabled = box.disabled = true; caret.disabled = true; button.title = 'Sending…'; status.textContent = '';
  try {
    const picked = split ? taskDeliveryPicked(state) : null;
    const body = {text, public: !!$('[data-comment-public]', form)?.checked, ...deliveryBody(picked)};
    const data = await post(`/v2/tasks/${encodeURIComponent(state.id)}/comments`, body);
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
    if (taskChatCurrent(state)) status.textContent = `Not saved: ${e.message}`;
  } finally {
    state.sending = false;
    if (taskChatCurrent(state)) {
      button.disabled = box.disabled = caret.disabled = false;
      taskDeliveryPaint(state); box.focus();
    }
  }
}
async function taskChatSend(state) { return taskCommentSend(state); }
function issueModalHTML(i) {
  const running = new Set((S.status?.active || []).map(a => a.issue));
  const tag = boardTag(i, running);
  return `<div class="tmodal-head">${avatar(i.owner_id, 22, stateOf(i.owner_id))}<span class="who">${empName(i.owner_id)}</span>
      <span class="mono muted">#${esc(i.number)}</span>${statusPill(i)}${i.priority && i.priority !== 'p2' ? prioPill(i) : ''}
      ${tag ? `<span class="tag">${esc(tag)}</span>` : ''}${isRecurringIssue(i) ? '<span class="gl" title="Scheduled">⟳</span>' : ''}
      <span class="spacer"></span><span class="muted tnum">${esc(ago(i.state === 'CLOSED' ? i.closedAt : i.updatedAt))}</span>
      <button class="ghost tmodal-x" type="button" data-modal-close aria-label="Close">✕</button></div>
    <h2 class="tmodal-title">${esc(i.title)}</h2>
    <div class="tmodal-body">${i.needs_human ? needsYouBlock([i])
      : i.body ? `<div class="q">${esc(i.body)}</div>` : i.last_comment ? `<div class="q">${esc(i.last_comment)}</div>` : '<div class="muted">No details yet.</div>'}
      <div class="issue-actions">
        ${i.state === 'OPEN' ? `<button class="linkish" type="button" data-modal-issue="comment">Comment</button>
          <button class="linkish danger" type="button" data-modal-issue="close">Close</button>` : '<span class="muted">Closed</span>'}
        <a class="github" href="${esc(i.url)}" target="_blank" rel="noopener">GitHub ↗</a></div>
      <div class="issue-compose" hidden></div></div>`;
}
// Where the peek's task sits in the list ("3 / 16"); j/k and ↑/↓ move.
function taskPeekPos(id) {
  if (!TASKS_ST || typeof tasksVisibleKeys !== 'function') return '';
  const keys = tasksVisibleKeys(), i = keys.indexOf('t' + id);
  return i < 0 ? '' : `${i + 1} / ${keys.length}`;
}
// A deferred task (backend/quiet_work.py): delivered into a run (held until something), eligible for the bot's next
// run, or queued with a run of its own; which run, and when it was handed over. `quiet` is the field's older name.
function taskDeferredHTML(t) {
  const q = t.deferred || t.quiet;
  if (!q || taskFinished(t)) return '';
  const state = q.state === 'delivered' && q.resume ? 'held' : q.state;
  const label = {queued: 'Queued', eligible: 'Eligible', delivered: 'Delivered', held: 'Held'}[state] || state;
  const more = [q.run ? `run ${String(q.run).slice(0, 8)}` : '', q.at ? `handed over ${ago(q.at)}` : ''].filter(Boolean).join(', ');
  return `<p class="task-wait task-deferred" data-task-deferred="${esc(state)}"><span class="lbl">Deferred: ${esc(label)}</span> ${esc(q.words || '')}${more ? ` <span class="muted">(${esc(more)})</span>` : ''}</p>`;
}
// A question to you on a task past the Needs you pages held: its item is read alone, and its question box takes the
// plain ask's place once it lands.
function taskQcardWait(t) {
  if (needsAll() || needsOneHeld(t.id)) return '';
  void v2NeedsOne(t.id).then(() => {
    const el = qcardItem(t) && document.querySelector(`[data-qcard-wait="${CSS.escape(String(t.id))}"]`);
    if (el) el.outerHTML = qcardBoxHTML(t);
  });
  return ` data-qcard-wait="${esc(String(t.id))}"`;
}
// The task in full or in the peek. The header: its status, its age, Done and Close task for whoever may use them (the
// two most common actions) as labelled buttons, then the quiet icon buttons: "…" and, past a rule, ✕ for the window.
// The owner and who asked are properties, so the header does not repeat them; who added it and when is the age's
// tooltip. Then the title, the properties, the details, Code, Subtasks, Comments.
function hubModalHTML(t, it, opts = {}) {
  const askToYou = taskAskToPerson(t);
  const finished = taskFinished(t);
  // What it waits on: a question to you reads as one; any other waiting note is a quiet line. A blocker is in Related.
  // Waiting on a bot's prerequisite work reads "Waiting on <bot>: <reason>" (taskBotWait).
  const blockers = taskBlockers(t), botWait = !askToYou && !finished ? taskBotWait(t) : null;
  const waitText = clipLine(botWait ? `${botWait.name}${botWait.reason ? `: ${botWait.reason}` : ''}${botWait.since}`
    : blockers.length && !askToYou && !taskBlockedHold(t) ? '' : taskWaitLine(t), 400);
  const waitLabel = askToYou ? needsWho(t) : botWait || t.status === 'waiting' ? 'Waiting on' : '';
  const original = taskBody(t).trim() === String(t.title || '').trim() ? '' : taskBody(t);   // a quick subtask's details are its title
  const note = String(t.note || '');
  const statusWord = taskStatusLabel(t);
  const mover = canMove();
  const links = (t.links || []).filter(l => !['pr', 'worktree', 'session'].includes(l.kind));   // Code is in the rail; Sessions below
  const pos = opts.peek ? taskPeekPos(t.id) : '';
  // Done for the owner; Close as the "…" menu allows it (who may move tasks, or the task's parties)
  const rights = taskPropRights(t);
  const canDone = !finished && t.status !== 'done' && !!myActor() && myActor() === t.owner_id && !pipelineStepsOnly(t);
  const snooze = taskHold(t, 'snoozed') || {};
  const canClose = t.status !== 'closed' && (rights.mover || rights.party) && !pipelineStepsOnly(t);
  // The thread view's own header pieces: back, the title in the bar, and Details (task-modal.css hides the rest).
  const thread = !!opts.thread;
  return `<div class="tmodal-head">${thread ? `<button class="ghost peek-btn tthread-only tthread-back" type="button" data-thread-back aria-label="Back to chat" title="Back to chat"><svg viewBox="0 0 12 12" width="14" height="14" aria-hidden="true" focusable="false"><path d="M7.5 3l-3 3 3 3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
      <span class="tthread-only tthread-title" tabindex="-1">${esc(t.title)}</span>` : ''}<span class="pill tstatus">${esc(statusWord)}</span>${statusWord === 'Waiting' || finished ? '' : taskWaitTag(t)}${blockers.length && !finished
      ? `<span class="tchip-blocked" title="Blocked by ${esc(blockers.map(b => b.title).join(', '))}">blocked</span>` : ''}${taskSnoozed(t)
      ? `<button type="button" class="tl-due wake tchip-snoozed" data-task-unsnooze title="Snoozed until ${esc(fmt(snooze.until))} by ${esc(actorLabel(snooze.by))}${snooze.reason ? ` · ${esc(snooze.reason)}` : ''}${rights.snooze ? '. Click to bring it back now' : ''}"${rights.snooze ? '' : ' disabled'}>Snoozed to ${esc(snoozeDay(t))}${chipNoteHTML(snooze.reason)}</button>` : ''}
      ${finished ? '' : taskScreenLinkHTML(t.owner_id)}
      <span class="spacer"></span>${pos ? `<span class="peek-pos tnum" title="J / K or ↑ / ↓ move to the next or previous task">${esc(pos)}</span>` : ''}
      <span class="muted tnum tmodal-age" title="${esc(`${taskSourceLine(t)}${t.created_at ? ' ' + fmt(t.created_at) : ''} · Updated ${fmt(it.updated_at)}`)}">${esc(ago(it.updated_at))}</span>
      ${canDone ? `<button class="ghost tmodal-act" type="button" data-task-finish="done" title="Mark this task done">${PROP_ICON.check}<span>Done</span></button>` : ''}
      ${canClose ? `<button class="ghost tmodal-act" type="button" data-task-finish="close" title="Close this task">Close task</button>` : ''}
      <button class="ghost peek-btn" type="button" data-task-more aria-haspopup="menu" aria-label="More actions" title="More actions">${PROP_ICON.more}</button>
      ${thread ? '<button class="ghost tmodal-act tthread-only" type="button" data-thread-details>Details</button>' : ''}
      <span class="tmodal-sep" aria-hidden="true"></span><button class="ghost peek-btn tmodal-x" type="button" data-modal-close aria-label="Close window" title="Close window (Esc)">${TL_ICON.x}</button></div>
    <h2 class="tmodal-title" tabindex="-1">${esc(t.title)}</h2>
    ${taskBuilderNotices(taskBuilderActors(t))}
    ${t.goal_id ? `<div class="muted tmeta"><a href="#/goals/${encodeURIComponent(t.goal_id)}" class="task-goal" data-goal-title="${esc(t.goal_id)}">serves a goal</a></div>` : ''}
    <div class="tmodal-body task-layout">
      <div class="tmodal-main">
      <section class="gr-idea" data-growth-idea aria-label="Idea" hidden></section>
      <section class="task-pages" data-task-pages aria-label="Pages" hidden></section>
      <section class="rule-holds" data-rule-holds aria-label="Held by a rule" hidden></section>
      ${askToYou && qcardItem(t) ? qcardBoxHTML(t) : waitLabel && waitText ? (askToYou ? `<div class="task-ask"${taskQcardWait(t)}><div class="lbl">${esc(waitLabel)}</div><div class="task-ask-body">${esc(waitText)}</div></div>`
        : `<p class="task-wait"><span class="lbl">${esc(waitLabel)}</span> ${esc(waitText)}</p>`) : ''}
      ${taskDeferredHTML(t)}
      ${original ? (waitText ? `<details class="task-orig"><summary>Original request</summary><div class="tdesc md">${safeMd(original)}</div></details>`
        : `<div class="tdesc md">${safeMd(original)}</div>`) : (waitText ? '' : '<p class="muted tdesc">No details.</p>')}
      ${note && ![waitText, botWait && clipLine(botWait.reason, 400)].includes(note.replace(/\s+/g, ' ').trim()) ? `<details class="task-orig"><summary>Progress</summary><div class="tdesc md">${safeMd(note)}</div></details>` : ''}
      ${(t.acceptance_criteria || []).length ? `<section class="tsec"><h3 class="rail-h">Done looks like</h3><ul class="tcriteria">${t.acceptance_criteria.map(a => `<li>${esc(a)}</li>`).join('')}</ul></section>` : ''}
      <div class="tsecs">
      <section class="tsec task-links${links.length ? '' : ' tsec-empty'}"><h3 class="rail-h">Links<button type="button" class="prop-add" data-link-add aria-label="Add a link" title="Add a link">${TL_ICON.plus}</button></h3>
        ${links.length ? `<div class="tlinks">${links.map(l => `<span class="tlink ${esc(l.kind)} ${esc(l.state || '')}"><a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title || l.url)}</a>${l.state && l.state !== 'open' ? ` · ${esc(l.state)}` : ''}${mover ? `<button type="button" class="x" data-drop-link="${esc(l.id)}" aria-label="Remove link">×</button>` : ''}</span>`).join('')}</div>` : ''}
        <form class="inline task-link-add" data-modal-link hidden><input type="url" inputmode="url" autocomplete="off" spellcheck="false" placeholder="https://…" aria-label="Add a link"></form></section>
      ${roadmapTaskHTML(t)}
      ${taskRunModelHTML(t)}
      ${taskSessionsHTML(t)}
      <section class="tsec task-runs" data-task-runs aria-label="Runs" hidden></section>
      ${t.relations ? taskRelHTML(t) : ''}
      <section class="tsec task-files" data-task-files aria-label="Files" hidden></section>
      </div>
      </div>
      <aside class="task-side" aria-label="Details">
        <section class="task-props" data-task-props aria-label="Properties">${taskPropsHTML(t, opts)}</section>
        <div class="task-rail" data-task-rail hidden></div>
      </aside>
      <section class="task-chat" aria-label="Conversation"><p class="muted" role="status">Loading the conversation…</p></section>
    </div>`;
}
