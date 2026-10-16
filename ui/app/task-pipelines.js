'use strict';

let TASK_TYPES = [];
let TASK_TYPES_LOADING = null;
const PIPELINE_STATUSES = ['open', 'doing', 'waiting', 'review', 'ready', 'done', 'closed', 'declined'];
const pipelineTypeId = task => task.type_id || task.type?.id || 'general';
const pipelineType = task => TASK_TYPES.find(type => type.id === pipelineTypeId(task));
// A steps-only type's tasks change status only by a step move: no Done, Close or Reopen of their own.
const pipelineStepsOnly = task => !!pipelineType(task)?.steps_only;
const pipelineSelectedType = state => TASK_TYPES.find(type => type.id === state?.type);
const pipelineActionStatus = (task, body) => body.status || pipelineType(task)?.steps.find(step => step.id === body.step)?.status;
const pipelineStepName = id => TASK_TYPES.flatMap(type => type.steps).find(step => step.id === id)?.name || id;
async function taskTypesLoad() {
  if (!S.me?.cloud) return TASK_TYPES;
  if (!TASK_TYPES_LOADING) TASK_TYPES_LOADING = get('/v2/task-types').then(data => {
    TASK_TYPES = data.types || [];
    return TASK_TYPES;
  }).finally(() => { TASK_TYPES_LOADING = null; });
  return TASK_TYPES_LOADING;
}
function taskPipelineState(state) {
  state.type = 'general';
  if (!S.me?.cloud) return;
  try { state.type = localStorage.getItem('tico.tasks.type') || 'general'; } catch {}
}
function taskPipelineRemember(state) {
  try { localStorage.setItem('tico.tasks.type', state.type || 'general'); } catch {}
}
// Every task view belongs to one type; a selected type turns the board into its steps.
function taskPipelineMatches(task, state) {
  return pipelineTypeId(task) === (state.type || 'general');
}
// Common types stay one click away; other team-defined types share the last segment.
function taskPipelineDevType() {
  return TASK_TYPES.find(type => type.manager === 'engineering-lead' || type.id === 'engineering') || TASK_TYPES.find(type => /^dev(?:[ -]?tickets?)?$/i.test(type.name))
    || TASK_TYPES.find(type => /^dev(?:[ -]?tickets?)?$/i.test(type.id));
}
function taskPipelineTools(state) {
  const host = $('#task-type');
  if (!host) return;
  host.hidden = state.view === 'recurring';
  const dev = taskPipelineDevType();
  const product = TASK_TYPES.find(type => type.manager === 'product-manager' || type.id === 'product-roadmap');
  const other = TASK_TYPES.filter(type => type.id !== 'general' && type.id !== dev?.id && type.id !== product?.id);
  const selected = other.find(type => type.id === state.type);
  const segment = (id, label, disabled = false) => `<button type="button" id="task-type-${esc(id)}" data-task-type="${esc(id)}" aria-pressed="${state.type === id}"${disabled ? ' disabled title="This team has no Dev ticket type"' : ''}>${esc(label)}</button>`;
  tasksPaint(host, segment('general', 'General') + (product ? segment(product.id, 'Roadmap') : '') + segment(dev?.id || 'dev', dev?.id === 'engineering' ? 'Engineering' : 'Dev tickets', !dev)
    + `<button type="button" id="task-type-more" aria-haspopup="menu" aria-controls="task-filter-pop" aria-expanded="false" aria-pressed="${!!selected}" aria-label="${esc(selected ? 'Task type: ' + selected.name + '. More task types' : 'More task types')}"${other.length ? '' : ' disabled'}>${selected ? `<span>${esc(selected.name)}</span>` : ''}${TL_ICON.caret}</button>`);
  $('#task-q')?.setAttribute('aria-label', `Search ${pipelineSelectedType(state)?.name || 'General'} tasks`);
}
function taskPipelineSelect(state, id) {
  if (id !== 'general' && !TASK_TYPES.some(type => type.id === id)) return;
  tasksMenuClose();
  state.typeChosen = true; // A late preference read cannot undo a choice made in this page.
  if (state.type === id) { tasksRemember(state); return; }
  state.type = id;
  state.memo = new Map();
  tasksSelectionClear(state);
  taskPipelineRemember(state); tasksRemember(state);
  tasksFiltersChanged(state); tasksPeekSync(state);
  if (state.view === 'board' && !state.doneLoaded && !state.doneLoading) void tasksLoadDone(state, true);
}
function taskPipelineMenu(state, anchor) {
  const dev = taskPipelineDevType();
  const product = TASK_TYPES.find(type => type.manager === 'product-manager' || type.id === 'product-roadmap');
  const other = TASK_TYPES.filter(type => type.id !== 'general' && type.id !== dev?.id && type.id !== product?.id);
  tasksMenu(state, anchor, `<div class="tl-menu" role="menu" aria-label="More task types">
    <div class="tl-menu-h" aria-hidden="true">Task type</div>
    ${other.map(type => `<button type="button" role="menuitemradio" aria-checked="${state.type === type.id}" class="tl-mi" data-pick-type="${esc(type.id)}"><span>${esc(type.name)}</span>${state.type === type.id ? '<span class="tl-mi-on" aria-hidden="true">✓</span>' : ''}</button>`).join('')}</div>`, pop => {
    pop.onclick = ev => {
      const button = ev.target.closest('[data-pick-type]');
      if (!button) return;
      taskPipelineSelect(state, button.dataset.pickType);
      $('#task-type-more')?.focus();
    };
  });
  anchor.setAttribute('aria-expanded', String($('#task-filter-pop').matches(':popover-open')));
}
function taskPipelineBoard(items, state) {
  const type = pipelineSelectedType(state);
  if (!type) return null;
  const columns = type.steps.map(step => ({id: step.id, name: step.name,
    items: items.filter(item => item.task.step_id === step.id)}));
  const unmapped = items.filter(item => !type.steps.some(step => step.id === item.task.step_id));
  for (const status of PIPELINE_STATUSES) {
    const list = unmapped.filter(item => item.task.status === status);
    if (list.length) columns.push({id: 'status-' + status, name: STATUS_WORD[status] || status, items: list});
  }
  const STEP_KIND = {open: 'starting', doing: 'doing', waiting: 'waiting', review: 'review', ready: 'review', done: 'done', closed: 'closed', declined: 'needs'};
  for (const column of columns) {
    column.items.sort((a, b) => (a.task.step_rank ?? Infinity) - (b.task.step_rank ?? Infinity)
      || String(a.task.created).localeCompare(String(b.task.created)) || String(a.task.id).localeCompare(String(b.task.id)));
    const step = type.steps.find(step => step.id === column.id);
    column.kind = STEP_KIND[step?.status || column.id.replace(/^status-/, '')] || '';
  }
  return boardColumnsHTML(columns) + (state.doneNext != null ? '<button class="ghost tl-more" type="button" id="board-more">Show more</button>' : '');
}
// A task's Type and Step are rows in its properties (ui/app/task-props.js).
async function taskPipelineCreate(form, selected = '') {
  form.dataset.taskType = selected || 'general';
  try { await taskTypesLoad(); } catch { return; }
  if (!form.isConnected || TASK_TYPES.length < 2 || form.elements.type) return;
  const row = document.createElement('label'); row.textContent = 'Type ';
  const select = document.createElement('select'); select.name = 'type'; select.setAttribute('aria-label', 'Type');
  select.innerHTML = TASK_TYPES.map(type => `<option value="${esc(type.id)}">${esc(type.name)}</option>`).join('');
  select.value = TASK_TYPES.some(type => type.id === selected) ? selected : 'general';
  row.append(select); form.querySelector('label').after(row);
  const visibility = document.createElement('label'); visibility.className = 'prop-check roadmap-check';
  const flag = document.createElement('input'); flag.type = 'checkbox'; flag.name = 'public'; flag.setAttribute('aria-label', 'Public');
  visibility.append(flag, 'Public');
  // Public text is written here, never copied from the internal title and body.
  const publicText = document.createElement('div'); publicText.className = 'roadmap-publication roadmap-create-public';
  publicText.innerHTML = '<label>Public title<input name="publicTitle" aria-label="Public title" maxlength="300" placeholder="Needed to publish"></label>'
    + '<label>Public summary<textarea name="publicSummary" aria-label="Public summary" rows="2" maxlength="8000" placeholder="Optional"></textarea></label>';
  const showPublicText = () => { publicText.hidden = visibility.hidden || !flag.checked; publicText.querySelector('input').required = !publicText.hidden; };
  flag.addEventListener('change', showPublicText);
  let ownerTouched = !!form.elements.owner.value && !TASK_TYPES.find(t => t.id === select.value)?.manager;
  form.elements.owner.addEventListener('change', () => { ownerTouched = true; });
  const defaults = () => {
    const type = TASK_TYPES.find(t => t.id === select.value);
    visibility.hidden = type?.manager !== 'product-manager';
    flag.checked = type?.default_visibility === 'public' && !form.elements.private?.checked;
    flag.disabled = !!form.elements.private?.checked;
    showPublicText();
    const manager = S.emps.find(e => e.name === type?.manager || type?.manager && e.template === type.manager);
    const value = manager && [...form.elements.owner.options].find(o => o.value === manager.name || o.value === 'bot:' + manager.name)?.value;
    if (value && !ownerTouched) form.elements.owner.value = value;
  };
  select.addEventListener('change', defaults); form.elements.private?.addEventListener('change', defaults); defaults(); row.after(visibility); visibility.after(publicText);
}
function taskPipelineCreatePayload(form, payload) {
  payload.type = form.elements.type?.value || form.dataset.taskType || 'general';
  if (form.elements.public && !form.elements.public.closest('label').hidden) payload.visibility = form.elements.public.checked ? 'public' : 'private';
  if (payload.visibility === 'public') {
    payload.public_title = form.elements.publicTitle?.value.trim() || '';
    payload.public_summary = form.elements.publicSummary?.value.trim() || '';
  }
}

function taskTypesSettingsMount() {
  const pane = $('#settings-types');
  if (!S.me?.cloud || !pane) return;
  pane.innerHTML = '<section class="card"><header><h2>Task types</h2></header><div id="set-types"></div></section>';
}
function taskTypesSettingsShow(tab) {
  if (tab === 'tasks' && S.me?.cloud) void renderTaskTypes();
}

async function renderTaskTypes() {
  const host = $('#set-types'); if (!host) return;
  host.textContent = 'Loading…';
  try {
    await taskTypesLoad();
    if (!host.isConnected) return;
    const mover = canMove();
    host.innerHTML = TASK_TYPES.map(type => `<div class="task-type-row"><strong>${esc(type.name)}</strong>
      <div>${type.steps.map(step => `<span class="tlabel">${esc(step.name)}</span>`).join(' ')}</div>
      ${mover && type.id !== 'general' ? `<button class="ghost" type="button" data-edit-type="${esc(type.id)}">Edit</button>` : ''}</div>`).join('')
      + (mover ? '<button class="primary" type="button" data-new-type>New type</button>' : '');
    host.querySelectorAll('[data-edit-type]').forEach(button => button.onclick = () => taskTypeEditor(TASK_TYPES.find(type => type.id === button.dataset.editType)));
    const add = host.querySelector('[data-new-type]'); if (add) add.onclick = () => taskTypeEditor();
  } catch (error) { host.textContent = error.message; }
}
// What every bot may do with company tasks beyond reading (docs/tasks.md, "Types bots work on").
const TYPE_BOTS = [['parties', 'Participants change their tasks'], ['read', 'Every bot may comment and create subtasks'],
  ['work', 'Every bot reads and works on all of them']];
function taskTypeEditor(type = null) {
  const dialog = document.createElement('dialog'); dialog.className = 'tmodal task-type-editor';
  dialog.setAttribute('aria-label', type ? 'Edit type' : 'New type');
  dialog.innerHTML = `<div class="tmodal-head"><h2>${type ? 'Edit type' : 'New type'}</h2><span class="spacer"></span><button class="ghost" type="button" data-close aria-label="Close">✕</button></div>
    <form class="tmodal-body"><label>Name <input name="typeName" required value="${esc(type?.name || '')}" aria-label="Type name"></label>
    <label>Default visibility<select name="defaultVisibility"><option value="private"${type?.default_visibility !== 'public' ? ' selected' : ''}>Private</option><option value="public"${type?.default_visibility === 'public' ? ' selected' : ''}>Public</option></select></label>
    <div data-steps></div><button class="ghost" type="button" data-add-step>Add step</button>
    <label>Bots <select name="typeBots" aria-label="What bots may do">${TYPE_BOTS.map(([value, words]) => `<option value="${value}"${(type?.bots || 'parties') === value ? ' selected' : ''}>${esc(words)}</option>`).join('')}</select></label>
    <div class="row"><button class="primary" type="submit">Save</button>${type ? '<button class="ghost danger" type="button" data-delete-type>Delete</button>' : ''}</div>
    <p data-error class="err" role="status"></p></form>`;
  document.body.append(dialog); dialog.showModal();
  dialog.addEventListener('close', () => dialog.remove());
  dialog.querySelector('[data-close]').onclick = () => dialog.close();
  const rows = dialog.querySelector('[data-steps]');
  const addStep = step => {
    const row = document.createElement('div'); row.className = 'task-step-edit'; row.dataset.stepId = step?.id || '';
    row.innerHTML = `<input required aria-label="Step name" value="${esc(step?.name || '')}" placeholder="Step name">
      <input ${dialog.querySelector('[name=defaultVisibility]').value === 'public' ? '' : 'hidden'} aria-label="Public step name" value="${esc(step?.public_name ?? step?.name ?? '')}" placeholder="Public step">
      <select aria-label="Step status">${PIPELINE_STATUSES.map(status => `<option value="${status}"${step?.status === status ? ' selected' : ''}>${esc(STATUS_WORD[status] || status)}</option>`).join('')}</select>
      <button class="ghost" type="button" data-up aria-label="Move step up">↑</button><button class="ghost" type="button" data-down aria-label="Move step down">↓</button>
      <button class="ghost danger" type="button" data-remove aria-label="Remove step">×</button>`;
    row.querySelector('[data-up]').onclick = () => { if (row.previousElementSibling) rows.insertBefore(row, row.previousElementSibling); };
    row.querySelector('[data-down]').onclick = () => { if (row.nextElementSibling) rows.insertBefore(row.nextElementSibling, row); };
    row.querySelector('[data-remove]').onclick = () => row.remove();
    rows.append(row);
  };
  (type?.steps || [{name: 'Open', status: 'open'}]).forEach(addStep);
  dialog.querySelector('[name=defaultVisibility]').onchange = ev => rows.querySelectorAll('[aria-label="Public step name"]').forEach(input => input.hidden = ev.target.value !== 'public');
  dialog.querySelector('[data-add-step]').onclick = () => addStep();
  const saved = async body => {
    const submit = dialog.querySelector('[type=submit]'); submit.disabled = true;
    try {
      await post('/v2/task-types' + (type ? '/' + encodeURIComponent(type.id) : ''), body);
      dialog.close(); await renderTaskTypes();
      if (TASKS_ST && $('#task-filter')) await tasksLoad(TASKS_ST);
    } catch (error) { dialog.querySelector('[data-error]').textContent = error.message; submit.disabled = false; }
  };
  dialog.querySelector('form').onsubmit = event => {
    event.preventDefault();
    const steps = [...rows.children].map((row, position) => ({...(row.dataset.stepId ? {id: row.dataset.stepId} : {}),
      name: row.querySelector('input').value, public_name: row.querySelector('[aria-label="Public step name"]').value || row.querySelector('input').value, status: row.querySelector('select').value, position}));
    void saved({name: dialog.querySelector('[name=typeName]').value, steps, bots: dialog.querySelector('[name=typeBots]').value, default_visibility: dialog.querySelector('[name=defaultVisibility]').value});
  };
  const remove = dialog.querySelector('[data-delete-type]');
  if (remove) remove.onclick = async () => {
    remove.disabled = true;
    try { await post('/v2/task-types/' + encodeURIComponent(type.id) + '/delete'); dialog.close(); await renderTaskTypes(); }
    catch (error) { dialog.querySelector('[data-error]').textContent = error.message; remove.disabled = false; }
  };
  dialog.querySelector('[name=typeName]').focus();
}
