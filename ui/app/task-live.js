/* The task dialog's title and details share the meeting item's autosave and place lifecycle. */
'use strict';

function taskLiveStop(d) {
  const live = d.taskLive;
  if (!live) return;
  sharedPlaceStop(live);
  clearTimeout(live.draft.timer);
  // A blur starts the last save before a dialog leaves. Keep a failed draft for reopening this task.
  if (!live.draft.error) void taskLiveSave(d, live);
  if (!live.draft.flight && !Object.keys(live.draft.patch).length) TASK_EDIT_DRAFTS.delete(live.id);
  d.taskLive = null;
}
function taskLiveGone(d, live) {
  if (d.taskLive !== live) return;
  live.revoked = true;
  clearTimeout(live.draft.timer);
  TASK_EDIT_DRAFTS.delete(live.id); TASK_DRAFTS.delete(live.id);
  sharedPlaceStop(live);
  d.close(); d.innerHTML = '';
}
const TASK_EDIT_DRAFTS = new Map();
function taskLiveFocusKeep(d) {
  const live = d.taskLive, field = document.activeElement;
  if (!live || !d.contains(field) || !field.matches('[data-task-field]')) return;
  const selection = window.getSelection();
  const offset = (node, at) => { const range = document.createRange(); range.selectNodeContents(field); range.setEnd(node, at); return range.toString().length; };
  live.focus = {field, text: field.isContentEditable && selection.rangeCount && field.contains(selection.anchorNode) && field.contains(selection.focusNode)
    ? [offset(selection.anchorNode, selection.anchorOffset), offset(selection.focusNode, selection.focusOffset)] : null,
    caret: field.tagName === 'TEXTAREA' ? [field.selectionStart, field.selectionEnd, field.selectionDirection] : null};
}
function taskLiveFocusRestore(d) {
  const live = d.taskLive, focus = live?.focus;
  if (!focus) return;
  live.focus = null;
  if (!focus.field.isConnected) return;
  focus.field.focus({preventScroll: true});
  if (focus.caret) focus.field.setSelectionRange(...focus.caret);
  if (focus.text) {
    const point = offset => {
      const walk = document.createTreeWalker(focus.field, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walk.nextNode())) { if (offset <= node.length) return [node, offset]; offset -= node.length; }
      return [focus.field, focus.field.childNodes.length];
    };
    window.getSelection().setBaseAndExtent(...point(focus.text[0]), ...point(focus.text[1]));
  }
}
function taskLiveHTML(t, original, waitText) {
  const may = taskPropRights(t).edit;
  const preview = `<div class="tdesc md" data-task-body-preview>${original ? safeMd(original) : '<p class="muted">No details.</p>'}</div>`;
  const body = `<section data-task-edit-body>${may ? '<button type="button" class="linkish" data-task-body-edit aria-label="Edit details">Edit</button><textarea data-task-field="body" aria-label="Details" hidden></textarea>' : ''}${preview}</section>`;
  return original && waitText ? `<details class="task-orig"><summary>Original request</summary>${body}</details>` : body;
}
function taskLiveBind(d, task) {
  const id = String(task.id);
  if (d.taskLive?.id !== id) taskLiveStop(d);
  const live = d.taskLive ||= {id, draft: TASK_EDIT_DRAFTS.get(id) || sharedDraft(), nodes: {}};
  const draft = live.draft;
  TASK_EDIT_DRAFTS.set(id, draft);
  if (!draft.base || Number(task.version) >= Number(draft.base.version)) draft.base = task;
  if (!draft.flight && !Object.keys(draft.dirty).length) draft.version = draft.base.version;
  // These nodes move into each redraw. No focused editor is destroyed and recreated by an invalidation or save.
  for (const selector of ['.tmodal-title', '[data-task-edit-body]']) {
    const slot = $(selector, d), kept = live.nodes[selector];
    if (slot && kept && slot !== kept && !!slot.querySelector('textarea') === !!kept.querySelector('textarea')) {
      const fold = slot.closest('details.task-orig'), prior = kept.closest('details.task-orig');
      if (fold && prior) fold.open = prior.open;
      slot.replaceWith(kept);
    }
    else if (slot) live.nodes[selector] = slot;
  }
  const may = taskPropRights(task).edit;
  const title = $('.tmodal-title', d);
  title.contentEditable = may ? 'plaintext-only' : 'false';
  if (may) { title.dataset.taskField = 'title'; title.setAttribute('role', 'textbox'); title.setAttribute('aria-label', 'Title'); }
  else { delete title.dataset.taskField; title.removeAttribute('role'); title.removeAttribute('aria-label'); }
  const details = $('[data-task-field="body"]', d), edit = $('[data-task-body-edit]', d);
  if (details) details.disabled = !may;
  if (edit) { edit.hidden = !may; edit.onclick = () => { details.hidden = false; $('[data-task-body-preview]', d).hidden = true; details.focus(); }; }
  for (const field of d.querySelectorAll('[data-task-field]')) {
    field.oninput = () => {
      sharedDraftPatch(draft, field.dataset.taskField, field.tagName === 'TEXTAREA' ? field.value : field.textContent);
      clearTimeout(draft.timer);
      if (!draft.error) draft.timer = setTimeout(() => taskLiveSave(d, live), 700);
    };
    field.onblur = () => { if (!draft.error) void taskLiveSave(d, live); taskLivePaint(d, live); };
    if (field === title) field.onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); field.blur(); } };
  }
  $('[data-task-edit-retry]', d).onclick = () => taskLiveSave(d, live);
  taskLivePaint(d, live);
}
function taskLivePaint(d, live) {
  if (d.taskLive !== live) return;
  const draft = live.draft, shown = {...draft.base, ...draft.inflightPatch, ...draft.patch};
  for (const field of d.querySelectorAll('[data-task-field], .tmodal-title')) {
    const value = shown[field.dataset.taskField || 'title'] || '';
    if ((field === document.activeElement || field === live.focus?.field) && !draft.current) continue;
    if (field.tagName === 'TEXTAREA') {
      if (field.value === value) continue;
      const selection = field === document.activeElement ? [field.selectionStart, field.selectionEnd, field.selectionDirection] : null;
      field.value = value;
      if (selection) field.setSelectionRange(...selection);
    } else if (field.textContent !== value) {
      const focused = field === document.activeElement;
      if (focused) taskLiveFocusKeep(d);
      field.textContent = value;
      if (focused) taskLiveFocusRestore(d);
    }
  }
  const preview = $('[data-task-body-preview]', d);
  const body = taskBody(shown), original = body.trim() === String(shown.title || '').trim() ? '' : body;
  if (preview) preview.innerHTML = original ? safeMd(original) : '<p class="muted">No details.</p>';
  const error = $('[data-task-edit-error]', d);
  if (error) { error.hidden = !draft.error; error.title = draft.error || ''; }
  const conflict = $('[data-task-edit-current]', d);
  if (conflict) { conflict.hidden = !draft.current; conflict.textContent = draft.current ? `Current: ${draft.current.title} · ${draft.current.body || ''}` : ''; }
}
function taskLiveStart(d) {
  const live = d.taskLive;
  if (!live || live.revoked) return;
  sharedPlaceStart(live, {
    id: live.id, place: 'task:' + live.id, topic: 'tasks', filter: 'task',
    changed: () => taskLiveRead(d, live),
    gone: () => taskLiveGone(d, live),
    present: words => { const el = $('[data-task-presence]', d); if (d.taskLive === live && el) el.textContent = words; }
  });
}
async function taskLiveRead(d, live) {
  if (d.taskLive !== live || !d.open || live.revoked) return;
  if (live.reading) { live.again = true; return; }
  live.reading = true;
  try {
    const detail = await get('/v2/tasks/' + encodeURIComponent(live.id), {fresh: true});
    if (d.taskLive !== live || !d.open || live.revoked) return;
    if (!detail.task) return;
    await taskModalShow(detail.task, d, {detail});
  } catch (error) { if ([403, 404].includes(error.status)) taskLiveGone(d, live); }
  finally {
    live.reading = false;
    if (live.again) { live.again = false; void taskLiveRead(d, live); }
  }
}
function taskLiveEdited(d, detail) {
  const event = (detail.events || []).filter(e => ['title', 'body', 'owner', 'due'].includes(e.field))
    .reverse().sort((a, b) => String(b.ts || b.created_at).localeCompare(String(a.ts || a.created_at)))[0];
  const el = $('[data-task-edited]', d);
  if (el) { el.textContent = sharedEdited(event?.actor); el.title = event ? fmt(event.ts || event.created_at) : ''; }
}
async function taskLiveSave(d, live) {
  if (live.revoked) return;
  return sharedDraftSave(live.draft, {
    write: (patch, version) => {
      const run = async () => {
        if (live.revoked) throw new Error('Access lost');
        const reply = await post('/v2/tasks/' + encodeURIComponent(live.id), {...patch, version});
        if (!reply.task) throw new Error('Not saved');
        if (d.taskLive === live && Number(reply.task.version) >= Number(d.liveTask?.version || 0)) d.liveTask = reply.task;
        // Text edits update the held list directly, with no whole-list read.
        if (TASKS_ST) { TASKS_ST.tasks = mergeTaskRows(TASKS_ST.tasks, [reply.task]); tasksRender(TASKS_ST); }
        return reply.task;
      };
      return d.saveQueue = (d.saveQueue || Promise.resolve()).then(run, run);
    },
    current: async error => {
      if ([403, 404].includes(error.status)) { taskLiveGone(d, live); return null; }
      if (error.status !== 409) return null;
      try { return (await get('/v2/tasks/' + encodeURIComponent(live.id), {fresh: true})).task; }
      catch (failed) { if ([403, 404].includes(failed.status)) taskLiveGone(d, live); return null; }
    },
    settled: () => {
      if (d.taskLive !== live && !live.draft.error && !Object.keys(live.draft.patch).length) TASK_EDIT_DRAFTS.delete(live.id);
      taskLivePaint(d, live);
      if (!live.draft.error) return taskLiveRead(d, live);
    }
  });
}
