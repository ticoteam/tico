/* Task fields share the meeting item's autosave and place lifecycle. */
'use strict';

function taskLiveStop(d) {
  const live = d.taskLive;
  if (!live) return;
  sharedPlaceStop(live);
  clearTimeout(live.draft.timer); live.draft.timer = 0;
  // A blur starts the last save before a dialog leaves. Keep a failed draft for reopening this task.
  if (!live.draft.error) void taskLiveSave(d, live);
  taskLivePersist(live);
  if (live.draft.owner === live.owner && !live.draft.flight && !Object.keys(live.draft.patch).length) TASK_EDIT_DRAFTS.delete(live.id);
  d.taskLive = null;
}
function taskLiveGone(d, live) {
  if (d.taskLive !== live) return;
  live.revoked = true;
  clearTimeout(live.draft.timer); live.draft.timer = 0;
  TASK_EDIT_DRAFTS.delete(live.id); TASK_DRAFTS.delete(live.id);
  taskLiveForget(live.id, live.identity);
  sharedPlaceStop(live);
  d.close(); d.innerHTML = '';
  if (TASKS_ST?.tasks.some(task => String(task.id) === live.id)) {
    TASKS_ST.tasks = TASKS_ST.tasks.filter(task => String(task.id) !== live.id);
    tasksRender(TASKS_ST);
  }
}
const TASK_EDIT_DRAFTS = new Map();
const TASK_EDIT_STORAGE = 'tico.task-edit.';
const TASK_EDIT_FIELDS = ['title', 'body', 'public_title', 'public_summary', 'due', 'size'];
const taskLiveStoragePrefix = identity => TASK_EDIT_STORAGE + encodeURIComponent(identity) + '.';
const taskLiveStorageKey = (id, field, identity = sessionIdentity()) => identity ? taskLiveStoragePrefix(identity) + [id, field].map(encodeURIComponent).join('.') : '';
function taskLiveForget(id, identity = sessionIdentity()) {
  if (!identity) return;
  try { for (const key of TASK_EDIT_FIELDS) localStorage.removeItem(taskLiveStorageKey(id, key, identity)); } catch {}
}
function taskLiveAcknowledge(live, patch) {
  // Another window may have newer unsaved words. Only a confirmed save of the stored text consumes them.
  try {
    for (const [key, value] of Object.entries(patch)) {
      const storage = taskLiveStorageKey(live.id, key, live.identity);
      if (JSON.parse(localStorage.getItem(storage) || 'null')?.value === value) localStorage.removeItem(storage);
    }
  } catch {}
}
function taskLivePersist(live) {
  const draft = live.draft;
  if (live.revoked || SESSION.out || !live.identity || live.identity !== sessionIdentity() || draft.owner !== live.owner) return;
  const patch = {...draft.inflightPatch, ...draft.patch};
  try {
    for (const key of TASK_EDIT_FIELDS) if (key in patch)
      localStorage.setItem(taskLiveStorageKey(live.id, key, live.identity), JSON.stringify({value: patch[key], base: draft.bases[key], error: draft.error}));
  } catch {}
}
function taskLiveDraft(id) {
  const draft = sharedDraft(); draft.bases = {};
  if (!sessionIdentity()) return draft;
  for (const key of TASK_EDIT_FIELDS) {
    try {
      const kept = JSON.parse(localStorage.getItem(taskLiveStorageKey(id, key)) || 'null');
      const value = v => typeof v === 'string' || key === 'size' && (v === null || Number.isFinite(v));
      if (!value(kept?.value) || !value(kept?.base?.value) || !Number.isFinite(kept.base?.version)) continue;
      draft.patch[key] = kept.value; draft.dirty[key] = kept.base.value; draft.bases[key] = kept.base;
      draft.error = typeof kept.error === 'string' ? kept.error : '';
    } catch {}
  }
  return draft;
}
// Expiry stops the old session's work; identity-bound storage survives until sign-in identifies its owner.
function taskLiveSuspend() {
  for (const d of document.querySelectorAll('dialog')) if (d.taskLive) { d.taskLive.revoked = true; sharedPlaceStop(d.taskLive); }
  for (const draft of TASK_EDIT_DRAFTS.values()) { clearTimeout(draft.timer); draft.owner = null; }
  TASK_EDIT_DRAFTS.clear();
  TASK_DRAFTS.clear(); QC_DRAFTS.clear();
}
function taskLiveClear(identity = sessionIdentity()) {
  taskLiveSuspend();
  try {
    for (const key of Object.keys(localStorage))
      if (identity && key.startsWith(taskLiveStoragePrefix(identity))) localStorage.removeItem(key);
  } catch {}
}
function taskLiveIdentity(identity) {
  if (SESSION.identity && SESSION.identity !== identity) taskLiveClear(SESSION.identity);
  // Discard drafts from older people/orgs, including the former keys that had no org identity.
  try {
    for (const key of Object.keys(localStorage))
      if (key.startsWith(TASK_EDIT_STORAGE) && (!identity || !key.startsWith(taskLiveStoragePrefix(identity)))) localStorage.removeItem(key);
  } catch {}
}
window.addEventListener('beforeunload', event => {
  if (SESSION.out || ![...TASK_EDIT_DRAFTS.values()].some(draft => draft.flight || draft.error)) return;
  event.preventDefault(); event.returnValue = '';
});
document.addEventListener('click', event => {
  const link = event.target.closest?.('a[href]');
  if (link && /\/(?:logout|cdn-cgi\/access\/logout)(?:[?#]|$)/.test(link.getAttribute('href'))) {
    taskLiveClear(); SESSION.restoring = null; SESSION.reopen = null; SESSION.discarded = true;
    SESSION.watch?.disconnect(); SESSION.watch = null;
    try { sessionStorage.removeItem(SESSION_RETURN); } catch {}
  }
}, true);
// Capture immediately before replacing HTML: async reads must not snapshot text before the person finishes typing.
function taskFormKey(el, root) {
  const path = [], question = el.closest('[data-qc-key]');
  const scope = question && root.contains(question) ? question : root;
  const scoped = key => scope !== root ? `[data-qc-key="${CSS.escape(question.dataset.qcKey)}"] ${key}` : key;
  for (let node = el; node && node !== scope; node = node.parentElement) {
    const attr = ['id', 'data-task-field', 'name', 'aria-label', ...node.getAttributeNames().filter(k => k.startsWith('data-') && !['data-busy', 'data-loaded'].includes(k))]
      .find(key => node.hasAttribute(key));
    path.unshift(node.tagName.toLowerCase() + (attr ? `[${attr}="${CSS.escape(node.getAttribute(attr))}"]`
      : `:nth-of-type(${[...node.parentElement.children].filter(n => n.tagName === node.tagName).indexOf(node) + 1})`));
    // A unique semantic key survives inserted sections. Only repeated fields need their containing form's scope.
    if (attr && scope.querySelectorAll(path.join(' > ')).length === 1) return scoped(path.join(' > '));
  }
  return scoped(path.join(' > '));
}
function taskFormCapture(root) {
  const fields = [...root.querySelectorAll('input, textarea, select, [contenteditable]')].map(el => {
    const text = el.hasAttribute('contenteditable'), focused = el === document.activeElement;
    const selection = window.getSelection();
    const offset = (node, at) => { const range = document.createRange(); range.selectNodeContents(el); range.setEnd(node, at); return range.toString().length; };
    const modified = text || el.tagName === 'SELECT' || (['checkbox', 'radio'].includes(el.type)
      ? el.checked !== el.defaultChecked : el.value !== el.defaultValue);
    return {modified, base: el.closest('dialog')?.taskLive?.fields[el.dataset.taskField], key: taskFormKey(el, root), el, taskField: el.hasAttribute('data-task-field'), value: text ? el.textContent : el.value,
      checked: el.checked, focused, caret: typeof el.selectionStart === 'number' ? [el.selectionStart, el.selectionEnd, el.selectionDirection] : null,
      text: text && focused && selection.rangeCount && el.contains(selection.anchorNode) && el.contains(selection.focusNode)
        ? [offset(selection.anchorNode, selection.anchorOffset), offset(selection.focusNode, selection.focusOffset)] : null};
  });
  const folds = [...root.querySelectorAll('form[hidden], form:not([hidden]), details')]
    .map(el => ({key: taskFormKey(el, root), hidden: el.hidden, open: el.open}));
  return {fields, folds};
}
function taskFormAdopt(root, kept) {
  // URL inputs expose no selection API, and file inputs cannot be assigned a value. Their native state stays on
  // the input itself; move it into the new form before binding that form's handlers.
  for (const field of kept.fields) {
    if (!field.el.matches('input[type="url"], input[type="file"]') || root.contains(field.el)) continue;
    root.querySelector(field.key)?.replaceWith(field.el);
  }
}
function taskFormRestore(root, kept) {
  for (const fold of kept.folds) {
    const el = root.querySelector(fold.key); if (!el) continue;
    el.hidden = fold.hidden; if (el.tagName === 'DETAILS') el.open = fold.open;
  }
  for (const field of kept.fields) {
    const el = root.contains(field.el) ? field.el : root.querySelector(field.key); if (!el) continue;
    // Versioned editors are painted from their field drafts; clean, unfocused fields adopt the server's value.
    if (field.taskField && field.focused) {
      const live = el.closest('dialog')?.taskLive, key = el.dataset.taskField;
      if (live && !(key in live.draft.dirty) && !live.draft.current && field.base) {
        live.fields[key] = field.base;
        if (el.hasAttribute('contenteditable')) el.textContent = field.value; else el.value = field.value;
      }
    }
    if (!field.taskField && (field.modified || field.focused) && el.type !== 'file') {
      if (el.hasAttribute('contenteditable')) el.textContent = field.value;
      else el.value = field.value;
      if (typeof field.checked === 'boolean') el.checked = field.checked;
    }
    if (!field.focused) continue;
    el.focus({preventScroll: true});
    if (field.caret) el.setSelectionRange(...field.caret);
    if (field.text) {
      const point = offset => {
        const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT); let node;
        while ((node = walk.nextNode())) { if (offset <= node.length) return [node, offset]; offset -= node.length; }
        return [el, el.childNodes.length];
      };
      window.getSelection().setBaseAndExtent(...point(field.text[0]), ...point(field.text[1]));
    }
  }
}
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
function taskEditBodyHTML(t, original, waitText) {
  const may = taskPropRights(t).edit;
  const preview = `<div class="tdesc md" data-task-body-preview>${original ? safeMd(original) : '<p class="muted">No details.</p>'}</div>`;
  const body = `<section data-task-edit-body>${may ? '<button type="button" class="linkish" data-task-body-edit aria-label="Edit details">Edit</button>' : ''}${preview}</section>`;
  return original && waitText ? `<details class="task-orig"><summary>Original request</summary>${body}</details>` : body;
}
function taskLiveBind(d, task) {
  const id = String(task.id);
  if (d.taskLive?.id !== id) taskLiveStop(d);
  const live = d.taskLive ||= {id, identity: sessionIdentity(), owner: newRequestId(), draft: TASK_EDIT_DRAFTS.get(id) || taskLiveDraft(id), nodes: {}, fields: {}};
  const draft = live.draft;
  draft.owner = live.owner;
  TASK_EDIT_DRAFTS.set(id, draft);
  if (!draft.base || Number(task.version) >= Number(draft.base.version)) draft.base = task;
  // These nodes move into each redraw. No focused editor is destroyed and recreated by an invalidation or save.
  for (const selector of ['.tmodal-title', '[data-task-edit-body]', '[data-task-field="public_title"]', '[data-task-field="public_summary"]', '[data-task-field="due"]', '[data-task-field="size"]']) {
    const slot = $(selector, d), kept = live.nodes[selector];
    if (slot && kept && slot !== kept && slot.readOnly === kept.readOnly && slot.disabled === kept.disabled && !!slot.querySelector('[data-task-body-edit]') === !!kept.querySelector('[data-task-body-edit]')) {
      const fold = slot.closest('details.task-orig'), prior = kept.closest('details.task-orig');
      if (fold && prior) fold.open = prior.open;
      slot.replaceWith(kept);
    }
    else if (slot) live.nodes[selector] = slot;
  }
  const may = taskPropRights(task).edit;
  const title = $('.tmodal-title', d);
  const titleMode = editing => {
    live.titleEditing = may && editing;
    title.contentEditable = live.titleEditing ? 'plaintext-only' : 'false';
    if (live.titleEditing) title.setAttribute('role', 'textbox'); else title.removeAttribute('role');
  };
  title.tabIndex = may ? 0 : -1;
  titleMode(live.titleEditing || (live.focus?.field === title && !!live.focus.text));
  title.onclick = () => { if (may) { titleMode(true); title.focus(); } };
  if (may) { title.dataset.taskField = 'title'; title.setAttribute('aria-label', 'Title'); }
  else {
    delete title.dataset.taskField; title.removeAttribute('role'); title.removeAttribute('aria-label');
    title.oninput = title.onblur = title.onkeydown = null;
  }
  let details = $('[data-task-field="body"]', d);
  if (!details && may && 'body' in draft.dirty) {
    details = document.createElement('textarea'); details.dataset.taskField = 'body'; details.setAttribute('aria-label', 'Details');
    $('[data-task-body-edit]', d)?.after(details);
    const preview = $('[data-task-body-preview]', d); if (preview) preview.hidden = true;
  }
  const edit = $('[data-task-body-edit]', d);
  if (details) details.disabled = !may;
  const wireField = field => {
    taskLiveWireField(d, live, field);
    if (field === title) {
      const blur = field.onblur;
      field.onblur = () => { titleMode(false); blur?.(); };
      field.onkeydown = event => {
        if (event.key !== 'Enter') return;
        event.preventDefault(); event.stopPropagation();
        if (live.titleEditing) field.blur(); else titleMode(true);
      };
    }
  };
  d.querySelectorAll('[data-task-field]').forEach(wireField);
  if (edit) {
    edit.hidden = !may;
    edit.onclick = () => {
      if (!may) return;
      if (!details) {
        details = document.createElement('textarea'); details.dataset.taskField = 'body'; details.setAttribute('aria-label', 'Details');
        edit.after(details); wireField(details);
      }
      $('[data-task-body-preview]', d).hidden = true;
      taskLivePaint(d, live); details.focus();
    };
  }
  $('[data-task-edit-retry]', d).onclick = () => taskLiveSave(d, live);
  taskLivePaint(d, live);
  // Restored debounce drafts use their kept revision; failed drafts still wait for Retry.
  if (!draft.error && !draft.flight && Object.keys(draft.patch).length && !draft.timer)
    taskLiveSchedule(d, live);
}
function taskLiveSchedule(d, live) {
  clearTimeout(live.draft.timer); live.draft.timer = 0;
  if (!live.draft.error && !SESSION.out) live.draft.timer = setTimeout(() => taskLiveSave(d, live), 700);
}
const taskLiveValue = (task, key) => sharedDraftValue(task, key === 'due' ? 'due_at' : key);
function taskLiveDisplay(value, key, task) {
  if (key !== 'due' || !value) return value ?? '';
  const at = new Date(occurrenceDay(task) || value);
  if (!Number.isFinite(at.getTime())) return '';
  return new Date(at - at.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}
function taskLiveWireField(d, live, field) {
  if (field.readOnly || field.disabled) { field.oninput = field.onblur = null; return; }
  const draft = live.draft, key = field.dataset.taskField, immediate = ['due', 'size'].includes(key);
  const edit = () => {
    let value = field.hasAttribute('contenteditable') ? field.textContent : field.value;
    if (key === 'due') {
      const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
      if (!m) return;
      const [y, mo, day] = m.slice(1).map(Number), when = new Date(y, mo - 1, day, 17, 0);
      if (y < 2000 || y > 2200 || when.getMonth() !== mo - 1 || when.getDate() !== day) {
        if (d.propsErrs?.id !== live.id) d.propsErrs = {id: live.id, map: new Map()};
        d.propsErrs.map.set('due', 'That date is not valid.'); taskPropsMsgPaint(d); return;
      }
      value = when.toISOString();
    }
    if (key === 'size') { if (!field.checkValidity()) return; value = value === '' ? null : Number(value); }
    if (value === live.fields[key]?.value && !(key in draft.dirty)) return;
    if (!(key in draft.dirty)) draft.bases[key] = {...live.fields[key]};
    sharedDraftPatch(draft, key, value);
    draft.dirty[key] = draft.bases[key].value;
    taskLivePersist(live);
    if (!immediate) taskLiveSchedule(d, live);
  };
  field.oninput = edit;
  field.onblur = () => { if (!draft.error) void taskLiveSave(d, live); taskLivePaint(d, live); };
  if (immediate) field.onchange = () => { edit(); if (!draft.error) void taskLiveSave(d, live); };
}
function taskLivePaint(d, live) {
  if (d.taskLive !== live) return;
  const draft = live.draft, pending = {...draft.inflightPatch, ...draft.patch}, shown = {...draft.base, ...pending};
  for (const field of d.querySelectorAll('[data-task-field], .tmodal-title')) {
    const key = field.dataset.taskField || 'title', value = taskLiveDisplay(key in pending ? pending[key] : taskLiveValue(draft.base, key), key, key in pending ? null : draft.base);
    if ((field === document.activeElement || field === live.focus?.field) && !draft.current) continue;
    live.fields[key] = key in draft.dirty ? {...draft.bases[key]} : {value: taskLiveValue(draft.base, key), version: draft.base.version};
    if (field.matches('input, textarea')) {
      if (field.value === value) continue;
      const selection = field === document.activeElement && typeof field.selectionStart === 'number' ? [field.selectionStart, field.selectionEnd, field.selectionDirection] : null;
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
  if (preview && preview.taskBody !== original) {
    preview.taskBody = original;
    preview.innerHTML = original ? safeMd(original) : '<p class="muted">No details.</p>';
  }
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
  if (live.revoked || SESSION.out || live.identity !== sessionIdentity()) return;
  clearTimeout(live.draft.timer); live.draft.timer = 0;
  return sharedDraftSave(live.draft, {
    write: patch => {
      const run = async () => {
        if (live.revoked || SESSION.out || live.identity !== sessionIdentity()) throw new Error('Access lost');
        // Every field carries the revision its displayed text came from. Mixing revisions takes the oldest,
        // so a focused stale field always reaches the ordinary 409 path before it can overwrite another edit.
        const version = Math.min(...Object.keys(patch).map(key => live.draft.bases[key].version));
        const reply = await post('/v2/tasks/' + encodeURIComponent(live.id), {...patch, version});
        if (!reply.task) throw new Error('Not saved');
        if (live.revoked) return reply.task;
        taskLiveAcknowledge(live, patch);
        for (const key of Object.keys(patch)) {
          const base = live.draft.bases[key] = {value: taskLiveValue(reply.task, key), version: reply.task.version};
          if (d.taskLive?.draft === live.draft) d.taskLive.fields[key] = {...base};
        }
        if (d.taskLive === live) {
          for (const key of Object.keys(patch)) d.propsErrs?.map.delete(key);
          taskPropsMsgPaint(d);
        }
        if (d.taskLive === live && Number(reply.task.version) >= Number(d.liveTask?.version || 0)) d.liveTask = reply.task;
        // Text edits update the held list directly, with no whole-list read.
        if (TASKS_ST?.tasks.some(task => String(task.id) === live.id)) {
          TASKS_ST.tasks = mergeTaskRows(TASKS_ST.tasks, [reply.task]); tasksRender(TASKS_ST);
        }
        return reply.task;
      };
      return d.saveQueue = (d.saveQueue || Promise.resolve()).then(run, run);
    },
    current: async error => {
      if (error.status !== 409) return null;
      try {
        const current = (await get('/v2/tasks/' + encodeURIComponent(live.id), {fresh: true})).task;
        if (current) for (const key of Object.keys(live.draft.dirty))
          live.draft.bases[key] = {value: taskLiveValue(current, key), version: current.version};
        return current;
      }
      catch (failed) { if ([403, 404].includes(failed.status)) taskLiveGone(d, live); return null; }
    },
    settled: () => {
      if (live.draft.owner === live.owner && d.taskLive !== live && TASK_EDIT_DRAFTS.get(live.id) === live.draft && !live.draft.error && !Object.keys(live.draft.patch).length) TASK_EDIT_DRAFTS.delete(live.id);
      // Reopening transfers ownership. The old response may settle the shared save, but cannot evict that view.
      const active = d.taskLive?.draft === live.draft ? d.taskLive : live;
      taskLivePersist(active);
      taskLivePaint(d, active);
      return taskLiveRead(d, active);
    }
  });
}
