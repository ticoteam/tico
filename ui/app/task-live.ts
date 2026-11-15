/* Task fields share the meeting item's autosave and place lifecycle. */
'use strict';

// The value a task field holds in a draft: text, a size (a number or null), or nothing yet.
type TaskFieldValue = string | number | null | undefined;
// A field this file edits or reads: a task's input, textarea or contenteditable box, or a form or details fold.
interface TaskField extends HTMLElement {
  readOnly?: boolean;
  disabled?: boolean;
  value?: string;
  type?: string;
  checked?: boolean;
  defaultChecked?: boolean;
  defaultValue?: string;
  open?: boolean | undefined;
  selectionStart?: number | null;
  selectionEnd?: number | null;
  selectionDirection?: 'forward' | 'backward' | 'none' | null;
  // The title's handler is set to a plain function by this file (titleMode), not to an event handler.
  onblur: (() => void) | null;
  checkValidity(): boolean;
  setSelectionRange(start: number, end: number, direction?: 'forward' | 'backward' | 'none'): void;
}
// The value and the server version one field's text was read at (live.fields, draft.bases).
interface TaskFieldBase { value: TaskFieldValue; version: number; }
// The base a field was read at. Every read here follows the write that set it (a patch sets its base first), so a
// missing one is a bug: the throw is the TypeError the bare read gave.
function taskBaseAt(table: Record<string, TaskFieldBase>, key: string): TaskFieldBase {
  const base = table[key];
  if (!base) throw new TypeError(`No base for ${key}`);
  return base;
}
// A copy of a field's base. A missing base copies as {} exactly as the bare spread did before (no throw).
function taskBaseCopy(table: Record<string, TaskFieldBase>, key: string): TaskFieldBase {
  return {...table[key]} as TaskFieldBase;
}
// The edit draft a task keeps across redraws: unsent patches, the versions they were made against, and the save's state.
interface TaskLiveDraft {
  patch: Record<string, TaskFieldValue>;
  dirty: Record<string, TaskFieldValue>;
  bases: Record<string, TaskFieldBase>;
  base?: PropTask;
  flight: Promise<unknown> | null;
  inflightPatch?: Record<string, TaskFieldValue> | null;
  owner?: string | null;
  error: string;
  timer: number;
  version: number | null;
  current: PropTask | null;
  closed?: boolean;
  closedAcknowledged?: boolean;
  sending?: boolean;
}
// Where the caret or the selected text sat in the field the person had focused.
interface TaskFocus { field: HTMLElement; text: [number, number] | null; caret: [number, number, 'forward' | 'backward' | 'none'] | null; }
// The task dialog's live edit state (taskLiveBind): the draft and the nodes and fields it keeps between redraws.
interface TaskLive {
  id: string;
  identity: string;
  owner: string;
  draft: TaskLiveDraft;
  nodes: Record<string, TaskField>;
  fields: Record<string, TaskFieldBase>;
  revoked?: boolean;
  reading?: boolean;
  again?: boolean;
  titleEditing?: boolean;
  focus?: TaskFocus | null;
  shared?: SharedPlaceHandle | null | undefined;
}
// One control as taskFormCapture saved it, one fold (a form or details element), and the whole capture.
interface TaskFormSaved {
  modified: boolean;
  base: TaskFieldBase | undefined;
  key: string;
  el: TaskField;
  taskField: boolean;
  value: string;
  checked: boolean | undefined;
  focused: boolean;
  caret: [number, number, 'forward' | 'backward' | 'none'] | null;
  text: [number, number] | null;
}
interface TaskFormFold { key: string; hidden: boolean; open: boolean | undefined; }
interface TaskFormCapture { fields: TaskFormSaved[]; folds: TaskFormFold[]; }

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
function taskLiveGone(d: TaskDialog, live: TaskLive) {
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
const TASK_EDIT_DRAFTS = new Map<string, TaskLiveDraft>();
const TASK_EDIT_STORAGE = 'tico.task-edit.';
const TASK_EDIT_FIELDS = ['title', 'body', 'public_title', 'public_summary', 'due', 'size'];
const taskLiveStoragePrefix = (identity: string) => TASK_EDIT_STORAGE + encodeURIComponent(identity) + '.';
const taskLiveStorageKey = (id: string, field: string, identity: string = sessionIdentity()) => identity ? taskLiveStoragePrefix(identity) + [id, field].map(encodeURIComponent).join('.') : '';
function taskLiveForget(id: string, identity: string = sessionIdentity()) {
  if (!identity) return;
  try { for (const key of TASK_EDIT_FIELDS) localStorage.removeItem(taskLiveStorageKey(id, key, identity)); } catch {}
}
function taskLiveAcknowledge(live: TaskLive, patch: SharedValues) {
  // Another window may have newer unsaved words. Only a confirmed save of the stored text consumes them.
  try {
    for (const [key, value] of Object.entries(patch)) {
      const storage = taskLiveStorageKey(live.id, key, live.identity);
      if (JSON.parse(localStorage.getItem(storage) || 'null')?.value === value) localStorage.removeItem(storage);
    }
  } catch {}
}
function taskLivePersist(live: TaskLive) {
  const draft = live.draft;
  if (live.revoked || SESSION.out || !live.identity || live.identity !== sessionIdentity() || draft.owner !== live.owner) return;
  const patch = {...draft.inflightPatch, ...draft.patch};
  try {
    for (const key of TASK_EDIT_FIELDS) if (key in patch)
      localStorage.setItem(taskLiveStorageKey(live.id, key, live.identity), JSON.stringify({value: patch[key], base: draft.bases[key], error: draft.error}));
  } catch {}
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
// Expiry stops the old session's work; identity-bound storage survives until sign-in identifies its owner.
function taskLiveSuspend() {
  for (const d of document.querySelectorAll<TaskDialog>('dialog')) if (d.taskLive) { d.taskLive.revoked = true; sharedPlaceStop(d.taskLive); }
  for (const draft of TASK_EDIT_DRAFTS.values()) { clearTimeout(draft.timer); draft.owner = null; }
  TASK_EDIT_DRAFTS.clear();
  TASK_DRAFTS.clear();
}
function taskLiveClear(identity: string = sessionIdentity()) {
  taskLiveSuspend();
  try {
    for (const key of Object.keys(localStorage))
      if (identity && key.startsWith(taskLiveStoragePrefix(identity))) localStorage.removeItem(key);
  } catch {}
}
function taskLiveIdentity(identity: string) {
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
  const link = event.target instanceof Element ? event.target.closest('a[href]') : null;
  if (link && /\/(?:logout|cdn-cgi\/access\/logout)(?:[?#]|$)/.test(String(link.getAttribute('href')))) {
    taskLiveClear(); SESSION.restoring = null; SESSION.reopen = null; SESSION.discarded = true;
    SESSION.watch?.disconnect(); SESSION.watch = null;
    try { sessionStorage.removeItem(SESSION_RETURN); } catch {}
  }
}, true);
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
function taskLiveFocusKeep(d: TaskDialog) {
  const live = d.taskLive, field = document.activeElement;
  if (!live || !(field instanceof HTMLElement) || !d.contains(field) || !field.matches('[data-task-field]')) return;
  const selection = window.getSelection();
  const offset = (node: Node, at: number) => { const range = document.createRange(); range.selectNodeContents(field); range.setEnd(node, at); return range.toString().length; };
  live.focus = {field, text: field.isContentEditable && selection && selection.rangeCount && field.contains(selection.anchorNode) && field.contains(selection.focusNode) && selection.anchorNode && selection.focusNode
    ? [offset(selection.anchorNode, selection.anchorOffset), offset(selection.focusNode, selection.focusOffset)] : null,
    caret: field instanceof HTMLTextAreaElement ? [field.selectionStart, field.selectionEnd, field.selectionDirection] : null};
}
function taskLiveFocusRestore(d: TaskDialog) {
  const live = d.taskLive, focus = live?.focus;
  if (!live || !focus) return;
  live.focus = null;
  if (!focus.field.isConnected) return;
  focus.field.focus({preventScroll: true});
  if (focus.caret && focus.field instanceof HTMLTextAreaElement) focus.field.setSelectionRange(...focus.caret);
  if (focus.text) {
    const point = (offset: number): [Node, number] => {
      const walk = document.createTreeWalker(focus.field, NodeFilter.SHOW_TEXT);
      let node: Node | null;
      while ((node = walk.nextNode())) { if (node instanceof Text) { if (offset <= node.length) return [node, offset]; offset -= node.length; } }
      return [focus.field, focus.field.childNodes.length];
    };
    window.getSelection()?.setBaseAndExtent(...point(focus.text[0]), ...point(focus.text[1]));
  }
}
function taskLiveSchedule(d: TaskDialog, live: TaskLive) {
  clearTimeout(live.draft.timer); live.draft.timer = 0;
  if (!live.draft.error && !SESSION.out) live.draft.timer = setTimeout(() => taskLiveSave(d, live), 700);
}
// A task field's value as the field shows it (sharedDraftValue reads it untyped; missing reads as '').
const taskLiveValue = (task: PropTask | undefined, key: string): TaskFieldValue => sharedDraftValue(task, key === 'due' ? 'due_at' : key) as TaskFieldValue;
function taskLiveDisplay(value: TaskFieldValue, key: string, task: PropTask | null | undefined) {
  if (key !== 'due' || !value) return value ?? '';
  const at = new Date(occurrenceDay(task) || value);
  if (!Number.isFinite(at.getTime())) return '';
  return new Date(at.getTime() - at.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
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
function taskLivePaint(d: TaskDialog, live: TaskLive) {
  if (d.taskLive !== live) return;
  const draft = live.draft, base = draft.base;
  if (!base) return;
  const pending = {...draft.inflightPatch, ...draft.patch}, shown = {...base, ...pending};
  for (const field of d.querySelectorAll<TaskField>('[data-task-field], .tmodal-title')) {
    const key = field.dataset.taskField || 'title', value = String(taskLiveDisplay(key in pending ? pending[key] : taskLiveValue(base, key), key, key in pending ? null : base) ?? '');
    if ((field === document.activeElement || field === live.focus?.field) && !draft.current) continue;
    live.fields[key] = key in draft.dirty ? taskBaseCopy(draft.bases, key) : {value: taskLiveValue(base, key), version: base.version};
    if (field.matches('input, textarea')) {
      if (field.value === value) continue;
      const selection: [number, number, 'forward' | 'backward' | 'none'] | null = field === document.activeElement && typeof field.selectionStart === 'number' && typeof field.selectionEnd === 'number' && typeof field.selectionDirection === 'string' ? [field.selectionStart, field.selectionEnd, field.selectionDirection] : null;
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
async function taskLiveRead(d: TaskDialog, live: TaskLive) {
  if (d.taskLive !== live || !d.open || live.revoked) return;
  if (live.reading) { live.again = true; return; }
  live.reading = true;
  try {
    const detail = await get<{task?: PropTask}>('/v2/tasks/' + encodeURIComponent(live.id), {fresh: true});
    if (d.taskLive !== live || !d.open || live.revoked) return;
    if (!detail.task) return;
    await taskModalShow(detail.task, d, {detail});
  } catch (error) { const status = errorStatus(error); if (status === 403 || status === 404) taskLiveGone(d, live); }
  finally {
    live.reading = false;
    if (live.again) { live.again = false; void taskLiveRead(d, live); }
  }
}
async function taskLiveSave(d: TaskDialog, live: TaskLive) {
  if (live.revoked || SESSION.out || live.identity !== sessionIdentity()) return;
  clearTimeout(live.draft.timer); live.draft.timer = 0;
  return sharedDraftSave(live.draft, {
    write: (patch: SharedValues) => {
      const run = async () => {
        if (live.revoked || SESSION.out || live.identity !== sessionIdentity()) throw new Error('Access lost');
        // Every field carries the revision its displayed text came from. Mixing revisions takes the oldest,
        // so a focused stale field always reaches the ordinary 409 path before it can overwrite another edit.
        const version = Math.min(...Object.keys(patch).map(key => taskBaseAt(live.draft.bases, key).version));
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
        if (TASKS_ST) tasksTaskPut(TASKS_ST, reply.task);     // the Tasks page behind takes the saved task
        return reply.task;
      };
      return d.saveQueue = (d.saveQueue || Promise.resolve()).then(run, run);
    },
    current: async (error: unknown) => {
      if (errorStatus(error) !== 409) return null;
      try {
        const current = (await get<{task?: PropTask}>('/v2/tasks/' + encodeURIComponent(live.id), {fresh: true})).task;
        if (current) for (const key of Object.keys(live.draft.dirty))
          live.draft.bases[key] = {value: taskLiveValue(current, key), version: current.version};
        return current;
      }
      catch (failed) { const status = errorStatus(failed); if (status === 403 || status === 404) taskLiveGone(d, live); return null; }
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
