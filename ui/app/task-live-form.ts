/* ui/app/task-live-form.ts — A task's form kept as it is typed: capturing, restoring and adopting its fields, the edit box and its live binding.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   Moved from ui/app/task-live.ts, whose other code Home runs, into the task page part (ui/parts.json): only that part
   calls these, so a cold open of Home does not download them (P05b). */
'use strict';

function taskFormCapture(root: Element): TaskFormCapture {
  const fields = [...root.querySelectorAll<TaskField>('input, textarea, select, [contenteditable]')].map((el): TaskFormSaved => {
    const text = el.hasAttribute('contenteditable'), focused = el === document.activeElement;
    const selection = window.getSelection();
    const offset = (node: Node, at: number) => { const range = document.createRange(); range.selectNodeContents(el); range.setEnd(node, at); return range.toString().length; };
    const modified = text || el.tagName === 'SELECT' || (['checkbox', 'radio'].includes(String(el.type))
      ? el.checked !== el.defaultChecked : el.value !== el.defaultValue);
    return {modified, base: el.closest<TaskDialog>('dialog')?.taskLive?.fields[String(el.dataset.taskField)], key: taskFormKey(el, root), el, taskField: el.hasAttribute('data-task-field'), value: text ? el.textContent ?? '' : el.value ?? '',
      checked: el.checked, focused,
      caret: typeof el.selectionStart === 'number' && typeof el.selectionEnd === 'number' && typeof el.selectionDirection === 'string' ? [el.selectionStart, el.selectionEnd, el.selectionDirection] : null,
      text: text && focused && selection && selection.rangeCount && el.contains(selection.anchorNode) && el.contains(selection.focusNode) && selection.anchorNode && selection.focusNode
        ? [offset(selection.anchorNode, selection.anchorOffset), offset(selection.focusNode, selection.focusOffset)] : null};
  });
  const folds = [...root.querySelectorAll<TaskField>('form[hidden], form:not([hidden]), details')]
    .map((el): TaskFormFold => ({key: taskFormKey(el, root), hidden: el.hidden, open: el.open}));
  return {fields, folds};
}
function taskFormAdopt(root: Element, kept: TaskFormCapture) {
  // URL inputs expose no selection API, and file inputs cannot be assigned a value. Their native state stays on
  // the input itself; move it into the new form before binding that form's handlers.
  for (const field of kept.fields) {
    if (!field.el.matches('input[type="url"], input[type="file"]') || root.contains(field.el)) continue;
    root.querySelector(field.key)?.replaceWith(field.el);
  }
}
function taskFormRestore(root: Element, kept: TaskFormCapture) {
  for (const fold of kept.folds) {
    const el = root.querySelector<TaskField>(fold.key); if (!el) continue;
    el.hidden = fold.hidden; if (el.tagName === 'DETAILS') el.open = fold.open;
  }
  for (const field of kept.fields) {
    const el = root.contains(field.el) ? field.el : root.querySelector<TaskField>(field.key); if (!el) continue;
    // Versioned editors are painted from their field drafts; clean, unfocused fields adopt the server's value.
    if (field.taskField && field.focused) {
      const live = el.closest<TaskDialog>('dialog')?.taskLive, key = String(el.dataset.taskField);
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
      const point = (offset: number): [Node, number] => {
        const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
        for (let node = walk.nextNode(); node; node = walk.nextNode()) { if (node instanceof Text) { if (offset <= node.length) return [node, offset]; offset -= node.length; } }
        return [el, el.childNodes.length];
      };
      window.getSelection()?.setBaseAndExtent(...point(field.text[0]), ...point(field.text[1]));
    }
  }
}
// The Description tab's request: who asked and when, Edit for whoever may change it, then the words.
function taskEditBodyHTML(t: PropTask, original: string) {
  const may = taskPropRights(t).edit, who = taskAskerLabel(t);
  const preview = `<div class="tdesc md" data-task-body-preview>${original ? safeMd(original) : '<p class="muted">No details.</p>'}</div>`;
  // "Fri, Nov 13, 2:21 PM" (format.ts timeWords: month first, 12-hour, the year when it is not this one).
  const at = t.created_at ? timeWords(t.created_at) : '';
  const head = `<div class="task-desc-h"><span class="muted">${esc([who, at].filter(Boolean).join(' · '))}</span>${
    may ? '<button type="button" class="linkish" data-task-body-edit aria-label="Edit details">Edit</button>' : ''}</div>`;
  return `<section data-task-edit-body>${head}${preview}</section>`;
}
function taskLiveBind(d: TaskDialog, task: PropTask) {
  const id = String(task.id);
  if (d.taskLive?.id !== id) taskLiveStop(d);
  d.taskLive ||= {id, identity: sessionIdentity(), owner: newRequestId(), draft: TASK_EDIT_DRAFTS.get(id) || taskLiveDraft(id), nodes: {}, fields: {}};
  const live = d.taskLive;
  const draft = live.draft;
  draft.owner = live.owner;
  TASK_EDIT_DRAFTS.set(id, draft);
  if (!draft.base || Number(task.version) >= Number(draft.base.version)) draft.base = task;
  // These nodes move into each redraw. No focused editor is destroyed and recreated by an invalidation or save.
  for (const selector of ['.tmodal-title', '[data-task-edit-body]', '[data-task-field="public_title"]', '[data-task-field="public_summary"]', '[data-task-field="due"]', '[data-task-field="size"]']) {
    const slot = $(selector, d), kept = live.nodes[selector];
    if (slot && kept && slot !== kept && slot.readOnly === kept.readOnly && slot.disabled === kept.disabled && !!slot.querySelector('[data-task-body-edit]') === !!kept.querySelector('[data-task-body-edit]')) {
      slot.replaceWith(kept);
    }
    else if (slot) live.nodes[selector] = slot;
  }
  const may = taskPropRights(task).edit;
  const title = $('.tmodal-title', d);
  const titleMode = (editing: boolean) => {
    live.titleEditing = may && editing;
    title.contentEditable = live.titleEditing ? 'plaintext-only' : 'false';
    if (live.titleEditing) title.setAttribute('role', 'textbox'); else title.removeAttribute('role');
  };
  title.tabIndex = may ? 0 : -1;
  titleMode(live.titleEditing || (live.focus?.field === title && !!live.focus?.text));
  title.onclick = () => { if (may) { titleMode(true); title.focus(); } };
  if (may) { title.dataset.taskField = 'title'; title.setAttribute('aria-label', 'Title'); }
  else {
    delete title.dataset.taskField; title.removeAttribute('role'); title.removeAttribute('aria-label');
    title.oninput = title.onblur = title.onkeydown = null;
  }
  let details = $('[data-task-field="body"]', d);
  if (!details && may && 'body' in draft.dirty) {
    details = document.createElement('textarea'); details.dataset.taskField = 'body'; details.setAttribute('aria-label', 'Details');
    $('.task-desc-h', d)?.after(details);
    const preview = $('[data-task-body-preview]', d); if (preview) preview.hidden = true;
  }
  const edit = $('[data-task-body-edit]', d);
  if (details) details.disabled = !may;
  const wireField = (field: TaskField) => {
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
  d.querySelectorAll<TaskField>('[data-task-field]').forEach(wireField);
  if (edit) {
    edit.hidden = !may;
    edit.onclick = () => {
      if (!may) return;
      if (!details) {
        details = document.createElement('textarea'); details.dataset.taskField = 'body'; details.setAttribute('aria-label', 'Details');
        (edit.closest('.task-desc-h') || edit).after(details); wireField(details);
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
// `after`: a live change number from before the task on screen was read, when known (taskModalShow): the stream resumes
// there, so its first connect replays what changed since instead of reading the task again.
function taskLiveStart(d: TaskDialog, after: number | null = null) {
  const live = d.taskLive;
  if (!live || live.revoked) return;
  sharedPlaceStart(live, {
    id: live.id, place: 'task:' + live.id, topic: 'tasks', filter: 'task', after,
    changed: () => taskLiveRead(d, live),
    gone: () => taskLiveGone(d, live),
    present: (words: string) => { const el = $('[data-task-presence]', d); if (d.taskLive === live && el) el.textContent = words; }
  });
}

function taskLiveStop(d: TaskDialog) {
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
function taskLiveDraft(id: string): TaskLiveDraft {
  const draft: TaskLiveDraft = Object.assign(sharedDraft(), {bases: {}});
  if (!sessionIdentity()) return draft;
  for (const key of TASK_EDIT_FIELDS) {
    try {
      const kept = JSON.parse(localStorage.getItem(taskLiveStorageKey(id, key)) || 'null');
      const value = (v: unknown) => typeof v === 'string' || key === 'size' && (v === null || Number.isFinite(v));
      if (!value(kept?.value) || !value(kept?.base?.value) || !Number.isFinite(kept.base?.version)) continue;
      draft.patch[key] = kept.value; draft.dirty[key] = kept.base.value; draft.bases[key] = kept.base;
      draft.error = typeof kept.error === 'string' ? kept.error : '';
    } catch {}
  }
  return draft;
}
// Capture immediately before replacing HTML: async reads must not snapshot text before the person finishes typing.
function taskFormKey(el: Element, root: Element): string {
  const path: string[] = [];
  for (let node: Element | null = el; node && node !== root; node = node.parentElement) {
    const here: Element = node;
    const attr = ['id', 'data-task-field', 'name', 'aria-label', ...here.getAttributeNames().filter(k => k.startsWith('data-') && !['data-busy', 'data-loaded'].includes(k))]
      .find(key => here.hasAttribute(key));
    path.unshift(here.tagName.toLowerCase() + (attr ? `[${attr}="${CSS.escape(String(here.getAttribute(attr)))}"]`
      : `:nth-of-type(${[...(here.parentElement?.children ?? [])].filter(n => n.tagName === here.tagName).indexOf(here) + 1})`));
    // A unique semantic key survives inserted sections.
    if (attr && root.querySelectorAll(path.join(' > ')).length === 1) return path.join(' > ');
  }
  return path.join(' > ');
}
function taskLiveWireField(d: TaskDialog, live: TaskLive, field: TaskField) {
  if (field.readOnly || field.disabled) { field.oninput = field.onblur = null; return; }
  const draft = live.draft, key = String(field.dataset.taskField), immediate = ['due', 'size'].includes(key);
  const edit = () => {
    const text = (field.hasAttribute('contenteditable') ? field.textContent : field.value) ?? '';
    let value: TaskFieldValue = text;
    if (key === 'due') {
      const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
      if (!m) return;
      const y = Number(m[1]), mo = Number(m[2]), day = Number(m[3]), when = new Date(y, mo - 1, day, 17, 0);
      if (y < 2000 || y > 2200 || when.getMonth() !== mo - 1 || when.getDate() !== day) {
        if (d.propsErrs?.id !== live.id) d.propsErrs = {id: live.id, map: new Map()};
        d.propsErrs.map.set('due', 'That date is not valid.'); taskPropsMsgPaint(d); return;
      }
      value = when.toISOString();
    }
    if (key === 'size') { if (!field.checkValidity()) return; value = value === '' ? null : Number(value); }
    if (value === live.fields[key]?.value && !(key in draft.dirty)) return;
    if (!(key in draft.dirty)) draft.bases[key] = taskBaseCopy(live.fields, key);
    sharedDraftPatch(draft, key, value);
    draft.dirty[key] = taskBaseAt(draft.bases, key).value;
    taskLivePersist(live);
    if (!immediate) taskLiveSchedule(d, live);
  };
  field.oninput = edit;
  field.onblur = () => { if (!draft.error) void taskLiveSave(d, live); taskLivePaint(d, live); };
  if (immediate) field.onchange = () => { edit(); if (!draft.error) void taskLiveSave(d, live); };
}
