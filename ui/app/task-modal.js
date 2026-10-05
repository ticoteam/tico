/* ui/app/task-modal.js — The task modal and its comment thread (its files: ui/app/task-files.js)
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
    if (!TASK_CHAT || TASK_CHAT.dialog === d) taskChatStop();
    d.tfEl?.querySelector('video, audio')?.pause();
    d.tfEl = null; d.tfOpen = null;
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
    if (d.open) return;
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
// The one way a hub task opens in full: from the board, from a link, from a chat card. In the peek it opens beside the list.
async function taskModalShow(task, d = taskModal()) {
  TASK_MODAL_LOAD++;
  if (!d) d = taskModal();
  const peek = !!d.dataset.peek;
  if (!d.open || String(d.dataset.task) !== String(task.id)) { d.taskOpening = {}; d.askDrafts = new Map(); }
  else askDraftCapture(d);
  const opening = d.taskOpening;
  taskChatStop();
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
    taskPeekOpen(TASKS_ST, 't' + task.id); return;           // a parent or subtask opened from the peek: the list follows it
  }
  d.dataset.task = task.id;
  // a property that has the focus now keeps it through the redraw (over the one a save asked for)
  const onProp = d.contains(document.activeElement) ? document.activeElement.dataset.prop : '';
  if (onProp) d.focusProp = onProp;
  d.innerHTML = hubModalHTML(task, taskItem(task), {peek});
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
  }
  if (!peek) document.body.classList.add('task-modal-open');
  d.scrollTop = 0;
  // the list knows the task; the detail adds its parts, its parent and the comments
  const [detail] = await Promise.all([v2Get(`/v2/tasks/${encodeURIComponent(task.id)}`), taskTypesLoad().catch(() => TASK_TYPES)]);
  if (!d.open || String(d.dataset.task) !== String(task.id) || d.drawSeq !== seq) return;
  if (!detail?.task) { d.close(); d.innerHTML = ''; TASK_DRAFTS.delete(String(task.id)); return; }
  await taskMenuSettled(d);                 // never pull a menu out from under the pointer
  if (!d.open || String(d.dataset.task) !== String(task.id) || d.drawSeq !== seq) return;
  if (d.liveTask && Number(detail.task.version) < Number(d.liveTask.version)) return;   // a save landed since this was asked for
  const full = {...detail.task, children: detail.children || []};
  // the redraw keeps the focus where it was (a header button, the title, a property)
  const a = d.contains(document.activeElement) ? document.activeElement : null;
  const keep = !a ? '' : a.matches('[data-modal-close]') ? '[data-modal-close]' : a.matches('[data-task-more]') ? '[data-task-more]' : a.matches('.tmodal-title') ? '.tmodal-title'
    : a.dataset.prop ? `[data-prop="${CSS.escape(a.dataset.prop)}"]` : '';
  askDraftCapture(d);
  d.innerHTML = hubModalHTML(full, taskItem(full), {peek});
  taskModalBind(d, full, true);
  if (keep) $(keep, d)?.focus({preventScroll: true});
  taskPropFocus(d); d.focusProp = '';
  void taskRailLoad(d, full, detail);
  void taskChatLoad(task.id, d, detail);
  void tfLoad(d, task.id);
  if (typing) { const box = $('.task-chat textarea', d); box?.focus(); box?.setSelectionRange(box.value.length, box.value.length); }
}
// After a property saves, the redrawn row gets the focus back.
function taskPropFocus(d) {
  if (d.focusProp) $(`[data-prop="${CSS.escape(d.focusProp)}"]`, d)?.focus({preventScroll: true});
}
// `full`: the task as GET /v2/tasks/{id} answers it (list rows leave out what the rail's Code section needs).
function taskModalBind(d, task, full = false) {
  tfAdopt(d, task);
  d.dataset.version = String(task.version ?? '');
  if (!d.liveTask || String(d.liveTask.id) !== String(task.id) || Number(task.version) >= Number(d.liveTask.version)) d.liveTask = task;
  if (d.taskRail?.id !== String(task.id)) d.taskRail = {id: String(task.id), open: new Set()};
  if (full) d.taskRail.full = task;
  taskRailPaint(d, task); taskRailBind(d, task);
  $('[data-modal-close]', d).onclick = () => d.dataset.peek && TASKS_ST ? taskPeekClose(TASKS_ST) : d.close();
  void taskGoalTitle(d);
  // Each change goes through the dialog's queue (taskSave), one at a time, each with the latest version.
  const change = (body, then, field) => taskSaveQueued(d, String(task.id), body, then, field);
  taskPropsBind(d, task, change);
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
      propMenu(d, addRelated, {label: kind.text, find: 'Find a task', none: 'No open tasks', items: rows.map(x => ({value: x.id, text: `${x.title} ${actorLabel(x.owner)}`,
        html: `${taskStatusText(x)}<span class="tl-mi-t">${esc(clipLine(x.title, 70))}</span><span class="tl-mi-sub">${esc(actorLabel(x.owner))}</span>`})),
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
function taskRelHTML(t) {
  const groups = TASK_REL_GROUPS.map(([kind, dir, label]) => [kind, dir, label, taskRels(t, kind, dir)]).filter(g => g[3].length);
  return `<section class="tsec task-related"><h3 class="rail-h">Related<button type="button" class="prop-add" data-related-add aria-haspopup="menu" aria-label="Relate a task" title="Relate a task">${TL_ICON.plus}</button></h3>
    ${groups.map(([kind, dir, label, rows]) => `<div class="trel"><span class="trel-k">${esc(label)}</span><div class="tlinks">${rows.map(k => `<span class="tlink task"><button type="button" class="linkish" data-open-task="t${esc(k.id)}" aria-label="Open ${esc(k.title)}">${taskStatusText(k)} ${esc(clipLine(k.title, 70))}</button><button type="button" class="x" data-drop-rel="${esc(kind)} ${esc(dir)} ${esc(k.id)}" aria-label="Remove ${esc(label.toLowerCase())}: ${esc(k.title)}">×</button></span>`).join('')}</div></div>`).join('')}</section>`;
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
  try {
    const outcome = body.close ? 'closed' : pipelineActionStatus(cur, body);
    if ((outcome === 'done' || outcome === 'closed') && outcome !== cur.status) {
      const note = await taskOutcomeNote(cur, outcome === 'closed' ? {close: true} : body);
      if (note === null) return false;
      if (note) body = {...body, note};
    }
    const state = d.dataset.peek && TASKS_ST, key = 't' + id;
    const at = state ? tasksVisibleKeys().indexOf(key) : -1;
    const data = await post(`/v2/tasks/${encodeURIComponent(id)}`, {version: cur.version, ...body});
    errs.delete(field);
    if (data.task && String(d.liveTask?.id) === id) d.liveTask = data.task;
    if (here() && then !== false && data.task) void taskModalShow(data.task, d);       // the server's answer, at once
    if (BOT) {
      if (isKeeper(BOT.slug)) void loadBotTasksV2(BOT.slug);
      void loadBotChatTasks(BOT.slug);
    }
    void personTasksReload();
    // The list reloads behind it. If the task left the view (done, closed, handed on, declined), the list and the
    // peek move on to the next row: checked by whichever load lands last (a poll may overtake this one).
    if (state) state.pendingFinish = {key, at, after: state.loadSeq};
    if (TASKS_ST) void tasksLoad(TASKS_ST);
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
// ---- comments: one flat list of what was said and what changed, then a box. Not a chat.
// `taskChatStop` and `taskChatLoad` keep their names: the router and the chat cards call them.
let TASK_CHAT = null;
const TASK_DRAFTS = new Map();         // an unsent comment, per task, kept across redraws until it is sent or cleared
function taskChatStop() {
  if (TASK_CHAT) clearInterval(TASK_CHAT.poll);
  TASK_CHAT = null;
}
function taskChatCurrent(state) { return TASK_CHAT === state && state.dialog.open; }
const commentAuthor = (a, via) => {
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
  link: v => v ? `linked ${v}` : 'removed a link', due: v => v ? `set the due date to ${fmt(v)}` : 'cleared the due date',
  lint: v => `noted: ${v}`, note: v => `noted: ${clipLine(String(v || ''), 200)}`, comment: () => 'deleted a comment'};
// `files`: the task's files (ui/app/task-files.js), for the files a comment carried.
function commentLineHTML(x, i, all, files = [], taskId = '', canAnswer = true) {
  if (x.kind === 'event') {
    if (x.field === 'status' && x.old == null) return '';           // created: the header says so
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
    const say = TASK_EVENT_WORDS[x.field] ? TASK_EVENT_WORDS[x.field](x.new) : `changed ${x.field}`;
    // A result mirrored into a comment keeps its full text there; the event still records the status move.
    const mirrored = x.note && all?.some(y => y.kind === 'comment' && y.message?.from_actor === x.actor
      && String(y.message.body || '').trim() === String(x.note).trim() && Math.abs(Date.parse(y.ts) - Date.parse(x.ts)) < 5000);
    return `<div class="tcomment sys"><span class="tcomment-who">${commentAuthor(x.actor, x.via)}</span> <span class="muted">${esc(say)}${x.note && x.field !== 'note' && !mirrored ? ` — ${esc(clipLine(x.note, 200))}` : ''}</span>
      <time class="muted tnum" title="${esc(fmt(x.ts))}">${esc(ago(x.ts))}</time></div>`;
  }
  const m = x.message, ask = askOf(m);
  // a question's text is in its block; a body that only repeats it is left out
  const body = ask && ask.questions.some(q => String(q.question || '').trim() === String(m.body || '').trim()) ? '' : m.body || '';
  const kind = m.kind === 'ask' ? '<span class="pill needs">question</span>' : m.kind === 'answer' ? '<span class="pill">answer</span>' : '';
  return `<div class="tcomment${String(m.from_actor || '').startsWith('bot:') ? ' bot' : ''}"><div class="tcomment-head"><span class="tcomment-who">${commentAuthor(m.from_actor, m.refs?.via)}</span>${kind}
      ${m.refs?.quiet ? '<span class="muted" title="Saved for the bot\'s next run on this task">saved</span>' : ''}
      <span class="spacer"></span>${m.edited_at ? `<span class="muted" title="Edited ${esc(fmt(m.edited_at))}">edited</span>` : ''}<time class="muted tnum" title="${esc(fmt(m.created))}">${esc(ago(m.created))}</time></div>
    ${body ? `<div class="md">${safeMd(body)}</div>` : ''}
    ${tfCommentFilesHTML(m, files)}${ask ? askHTML(ask, m.answers, askTargetOf(m), m.from_actor, taskId, files, canAnswer) : ''}</div>`;
}
function taskCommentsRender(state, data) {
  const host = state.host;
  const lines = [
    ...(data.comments || data.messages || []).filter(m => ['say', 'ask', 'answer'].includes(m.kind)).map(m => ({kind: 'comment', ts: m.created, message: m})),
    ...(data.events || []).map(e => ({kind: 'event', ts: e.ts, ...e})),
  ].sort((a, b) => String(a.ts).localeCompare(String(b.ts)));
  if (!$('form', host)) {
    host.innerHTML = `<h3>Comments</h3>
      <div class="task-comments" role="log" aria-live="polite"></div>
      <form><textarea rows="3" maxlength="4000" required aria-label="Add a comment" placeholder="Add a comment…"></textarea>
        <div class="row"><button class="primary" type="submit">Comment</button><span class="muted" data-task-chat-status role="status"></span></div></form>`;
    $('form', host).onsubmit = ev => { ev.preventDefault(); void taskCommentSend(state); };
    const box = $('textarea', host);
    box.value = TASK_DRAFTS.get(String(state.id)) || '';
    box.oninput = () => { if (box.value) TASK_DRAFTS.set(String(state.id), box.value); else TASK_DRAFTS.delete(String(state.id)); };
  }
  state.data = data;
  // `can_comment`: false for someone who may read the task but not comment on it; they get no box and no answer controls
  const can = data.can_comment !== false, d = state.dialog;
  $('form', host).hidden = !can;
  if (d.canComment !== can) { d.canComment = can; if (d.tfEl) tfViewPaint(d); }
  // a new comment may have carried a new version of a file, or answered a question on one
  const count = (data.comments || data.messages || []).length;
  if (state.count != null && count !== state.count) void tfLoad(state.dialog, state.id);
  state.count = count;
  const files = tfFiles(state.dialog);
  const html = lines.map((x, i, all) => commentLineHTML(x, i, all, files, state.id, can)).join('') || '<p class="muted">Nothing said yet.</p>';
  const thread = $('.task-comments', host);
  if (!can) d.askDrafts?.clear();
  if (can && askBusy(thread)) return;     // never under someone answering
  if (html !== state.rendered) {
    const atEnd = !state.rendered || thread.scrollTop + thread.clientHeight >= thread.scrollHeight - 40;
    thread.innerHTML = html;
    askDraftRestore(thread, d);
    if (atEnd) thread.scrollTop = thread.scrollHeight;
    state.rendered = html;
  }
}
async function taskChatRead(state) {
  if (!taskChatCurrent(state) || state.sending || state.reading) return;
  state.reading = true;
  try {
    const data = await get(state.path);
    if (taskChatCurrent(state) && !state.sending) taskCommentsRender(state, data);
  } catch (e) {
    if (!taskChatCurrent(state)) return;
    if ([403, 404].includes(e.status)) {
      state.stopped = true; clearInterval(state.poll);
      TASK_DRAFTS.delete(String(state.id)); PROP_TASKS = null;
      state.dialog.close(); state.dialog.innerHTML = '';
      if (TASKS_ST) void tasksLoad(TASKS_ST);
      return;
    }
    if (e.status === 400) {state.stopped = true; clearInterval(state.poll);}
    const status = $('[data-task-chat-status]', state.host);
    if (status) status.textContent = `Unable to refresh: ${e.message}`;
    else state.host.innerHTML = `<p class="err" role="status">Comments unavailable: ${esc(e.message)}</p>`;
  } finally { state.reading = false; }
}
async function taskChatLoad(id, dialog, data = null) {
  const state = TASK_CHAT = {dialog, host: $('.task-chat', dialog),
    path: `/v2/tasks/${encodeURIComponent(id)}`, id, poll: 0, sending: false, reading: false};
  if (data) taskCommentsRender(state, data); else await taskChatRead(state);
  if (taskChatCurrent(state) && !state.stopped) state.poll = setInterval(() => {
    if (document.hidden || !taskChatCurrent(state)) return;
    void taskChatRead(state);
    tfLoad(state.dialog, state.id).catch(() => {});     // a new file or version, or an answer on one, without reopening
  }, 8000);
}
async function taskCommentSend(state) {
  if (!taskChatCurrent(state) || state.sending) return;
  const form = $('form', state.host);
  const box = $('textarea', form), text = box.value.trim();
  if (!text) { box.focus(); return; }
  state.sending = true;
  const button = $('button[type=submit]', form), status = $('[data-task-chat-status]', form);
  button.disabled = box.disabled = true; button.textContent = 'Sending…'; status.textContent = '';
  try {
    const data = await post(`/v2/tasks/${encodeURIComponent(state.id)}/comments`, {text});
    if (!taskChatCurrent(state)) return;
    box.value = ''; TASK_DRAFTS.delete(String(state.id)); state.sending = false;
    await taskChatRead(state);
    status.textContent = data.woke ? '' : 'Saved. The bot reads it on its next run on this task.';
  } catch (e) {
    if (taskChatCurrent(state)) status.textContent = `Not saved: ${e.message}`;
  } finally {
    state.sending = false;
    if (taskChatCurrent(state)) { button.disabled = box.disabled = false; button.textContent = 'Comment'; }
  }
}
async function taskChatSend(state) { return taskCommentSend(state); }
function issueModalHTML(i) {
  const running = new Set((S.status?.active || []).map(a => a.issue));
  const tag = boardTag(i, running);
  return `<div class="tmodal-head">${avatar(i.owner, 22, stateOf(i.owner))}<span class="who">${empName(i.owner)}</span>
      <span class="mono muted">#${esc(i.number)}</span>${statusPill(i)}${i.priority && i.priority !== 'p2' ? prioPill(i) : ''}
      ${tag ? `<span class="tag">${esc(tag)}</span>` : ''}${isRecurringIssue(i) ? '<span class="gl" title="a routine">⟳</span>' : ''}
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
// The task in full or in the peek. The header: its status (icon and name, as everywhere else), its owner, its age,
// a "…" menu and ✕. Then the title, who added it and when, the properties, the details, Code, Subtasks, Comments.
function hubModalHTML(t, it, opts = {}) {
  const askToYou = taskAskToPerson(t);
  const finished = taskFinished(t);
  // What it waits on: a question to you reads as one; any other waiting note is a quiet line. A blocker is in Related.
  const blockers = taskBlockers(t);
  const waitText = clipLine(blockers.length && !askToYou ? '' : taskWaitLine(t), 400);
  const waitLabel = askToYou ? needsWho(t) : t.status === 'waiting' ? 'Waiting on' : '';
  const original = taskBody(t).trim() === String(t.title || '').trim() ? '' : taskBody(t);   // a quick subtask's details are its title
  const note = String(t.note || '');
  const statusWord = taskStatusLabel(t);
  const mover = canMove();
  const links = (t.links || []).filter(l => l.kind !== 'pr' && l.kind !== 'worktree');   // those are Code, in the rail
  const pos = opts.peek ? taskPeekPos(t.id) : '';
  return `<div class="tmodal-head"><span class="pill tstatus">${esc(statusWord)}</span>${t.private ? '<span title="Only the requester and assignee can see this task" aria-label="Private"><span class="nav-icon" aria-hidden="true">lock</span></span>' : ''}${blockers.length && !finished
      ? `<span class="tchip-blocked" title="Blocked by ${esc(blockers.map(b => b.title).join(', '))}">blocked</span>` : ''}
      <span class="tmodal-owner">${actorFace(t.owner, 18)}<span class="who">${esc(actorLabel(t.owner))}</span></span>
      <span class="spacer"></span>${pos ? `<span class="peek-pos tnum" title="J / K or ↑ / ↓ move to the next or previous task">${esc(pos)}</span>` : ''}
      <span class="muted tnum tmodal-age" title="Updated ${esc(fmt(it.updated))}">${esc(ago(it.updated))}</span>
      <button class="ghost peek-btn" type="button" data-task-more aria-haspopup="menu" aria-label="More actions" title="More">${PROP_ICON.more}</button>
      <button class="ghost tmodal-x" type="button" data-modal-close aria-label="Close" title="Close (Esc)">✕</button></div>
    <h2 class="tmodal-title"${opts.peek ? ' tabindex="-1"' : ''}>${esc(t.title)}</h2>
    <div class="muted tmeta">${esc(taskSourceLine(t))} · ${esc(ago(t.created))}${t.goal_id ? ` · <a href="#/goals/${encodeURIComponent(t.goal_id)}" class="task-goal" data-goal-title="${esc(t.goal_id)}">serves a goal</a>` : ''}</div>
    <div class="tmodal-body task-layout">
      <div class="tmodal-main">
      ${waitLabel && waitText ? (askToYou ? `<div class="task-ask"><div class="lbl">${esc(waitLabel)}</div><div class="task-ask-body">${esc(waitText)}</div></div>`
        : `<p class="task-wait"><span class="lbl">${esc(waitLabel)}</span> ${esc(waitText)}</p>`) : ''}
      ${original ? (waitText ? `<details class="task-orig"><summary>Original request</summary><div class="tdesc md">${safeMd(original)}</div></details>`
        : `<div class="tdesc md">${safeMd(original)}</div>`) : (waitText ? '' : '<p class="muted tdesc">No details.</p>')}
      ${note && note.replace(/\s+/g, ' ').trim() !== waitText ? `<details class="task-orig"><summary>Progress</summary><div class="tdesc md">${safeMd(note)}</div></details>` : ''}
      ${(t.acceptance_criteria || []).length ? `<section class="tsec"><h3 class="rail-h">Done looks like</h3><ul class="tcriteria">${t.acceptance_criteria.map(a => `<li>${esc(a)}</li>`).join('')}</ul></section>` : ''}
      <section class="tsec task-links"><h3 class="rail-h">Links<button type="button" class="prop-add" data-link-add aria-label="Add a link" title="Add a link">${TL_ICON.plus}</button></h3>
        ${links.length ? `<div class="tlinks">${links.map(l => `<span class="tlink ${esc(l.kind)} ${esc(l.state || '')}"><a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title || l.url)}</a>${l.state && l.state !== 'open' ? ` · ${esc(l.state)}` : ''}${mover ? `<button type="button" class="x" data-drop-link="${esc(l.id)}" aria-label="Remove link">×</button>` : ''}</span>`).join('')}</div>` : ''}
        <form class="inline task-link-add" data-modal-link hidden><input type="url" inputmode="url" autocomplete="off" spellcheck="false" placeholder="https://…" aria-label="Add a link"></form></section>
      ${t.relations ? taskRelHTML(t) : ''}
      <section class="tsec task-files" data-task-files aria-label="Files" hidden></section>
      </div>
      <aside class="task-side" aria-label="Details">
        <section class="task-props" data-task-props aria-label="Properties">${taskPropsHTML(t, opts)}</section>
        <div class="task-rail" data-task-rail hidden></div>
      </aside>
      <section class="task-chat" aria-label="Comments"><p class="muted" role="status">Loading comments…</p></section>
    </div>`;
}
