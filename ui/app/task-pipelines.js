'use strict';

let TASK_TYPES = [];
let TASK_TYPES_LOADING = null;
const PIPELINE_STATUSES = ['open', 'doing', 'waiting', 'review', 'ready', 'done', 'closed', 'declined'];
// A step's status is one of these. Waiting is a tag on a task in its step, so a waiting step is a team's own column for
// parked work that only a move by hand fills; declining goes to the closed step.
const STEP_STATUSES = ['open', 'doing', 'waiting', 'review', 'ready', 'done', 'closed'];
// The board shows a type's working steps; its done and closed tasks are under Done and Closed.
const BOARD_HIDDEN = new Set(['done', 'closed', 'declined']);
const pipelineTypeId = task => task.type_id || task.type?.id || 'general';
const pipelineType = task => TASK_TYPES.find(type => type.id === pipelineTypeId(task));
// A steps-only type's tasks change status only by a step move: no Done or Reopen of their own. A person can still close one.
const pipelineStepsOnly = task => !!pipelineType(task)?.steps_only;
const pipelineSelectedType = state => TASK_TYPES.find(type => type.id === state?.type);
const pipelineActionStatus = (task, body) => body.status || pipelineType(task)?.steps.find(step => step.id === body.step)?.status;
const pipelineStepName = id => TASK_TYPES.flatMap(type => type.steps).find(step => step.id === id)?.name || id;
// The Product roadmap and Dev tickets types go by their role (backend/roadmap_flow.py), never by who manages them.
const pipelineRoleType = role => TASK_TYPES.find(type => type.role === role);
const pipelineProductType = () => pipelineRoleType('product-roadmap');
let TASK_TYPES_READ = false;
// `cached`: the types already read do (a task opened); the Tasks page's loads read them again, so an edit elsewhere shows.
async function taskTypesLoad({cached = false} = {}) {
  if (!S.me?.cloud) return TASK_TYPES;
  if (cached && TASK_TYPES_READ) return TASK_TYPES;
  if (!TASK_TYPES_LOADING) TASK_TYPES_LOADING = get('/v2/task-types').then(data => {
    TASK_TYPES = data.types || [];
    TASK_TYPES_READ = true;
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
// Engineering and a Dev tickets type made later may both have the engineering role; Dev tickets goes first.
function taskPipelineDevType() {
  const dev = type => /^dev(?:[ -]?tickets?)?$/i.test(type.name) || /^dev(?:[ -]?tickets?)?$/i.test(type.id);
  const engineering = TASK_TYPES.filter(type => type.role === 'engineering');
  return engineering.find(dev) || engineering[0] || TASK_TYPES.find(dev);
}
function taskPipelineTools(state) {
  const host = $('#task-type');
  if (!host) return;
  const dev = taskPipelineDevType();
  const product = pipelineProductType();
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
  tasksDoneEnsure(state);          // this type's board may have its finished steps unfolded
}
function taskPipelineMenu(state, anchor) {
  const dev = taskPipelineDevType();
  const product = pipelineProductType();
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
// A type's board: a column per step, in the type's order, that cards are dragged between (ui/app/task-board.js). Its
// leading backlog steps (open, before the first step with work under way) and its trailing finished steps each fold
// into one strip; a finished step part way along stays off the board.
function taskPipelineBoard(items, state, head = '') {
  const type = pipelineSelectedType(state);
  if (!type) return null;
  items = items.map(item => taskBoardLive(state, item));
  const steps = type.steps;
  let end = steps.length;
  while (end > 0 && BOARD_HIDDEN.has(steps[end - 1].status)) end--;
  const finished = steps.slice(end), working = steps.slice(0, end).filter(step => !BOARD_HIDDEN.has(step.status));
  let lead = 0;
  while (lead < working.length && working[lead].status === 'open') lead++;
  const backlog = working.slice(0, lead), folds = taskBoardFolds(state, type, backlog.length);
  const inStep = step => items.filter(item => item.task.step_id === step.id);
  const stepColumn = step => ({id: step.id, name: step.name, drop: step.id, items: inStep(step), scope: {type: type.id, step: step.id}});
  const strip = (fold, run, drop, name) => ({id: 'fold-' + fold, fold, drop, items: [], name: run.length === 1 ? run[0].name : name,
    hint: run.map(step => step.name).join(', '), count: fold === 'backlog' ? run.reduce((n, step) => n + inStep(step).length, 0) : ''});
  const columns = [];
  if (backlog.length && folds.backlog) columns.push(strip('backlog', backlog, backlog.at(-1).id, 'Backlog'));
  else backlog.forEach((step, i) => columns.push({...stepColumn(step), foldable: i ? '' : 'backlog'}));
  working.slice(lead).forEach(step => columns.push(stepColumn(step)));
  const shown = [...working, ...(folds.finished ? [] : finished)];
  const unmapped = items.filter(item => !shown.some(step => step.id === item.task.step_id) && !backlog.some(step => step.id === item.task.step_id));
  for (const status of PIPELINE_STATUSES.filter(status => !BOARD_HIDDEN.has(status))) {
    const list = unmapped.filter(item => item.task.status === status);
    if (list.length) columns.push({id: 'status-' + status, name: STATUS_WORD[status] || status, items: list});
  }
  if (finished.length && folds.finished) columns.push(strip('finished', finished, finished[0].id, 'Finished'));
  else finished.forEach((step, i) => columns.push({...stepColumn(step), foldable: i ? '' : 'finished'}));
  const STEP_KIND = {open: 'starting', doing: 'doing', waiting: 'waiting', review: 'review', ready: 'review', done: 'done', closed: 'closed', declined: 'needs'};
  for (const column of columns) {
    column.items.sort(taskByStepRank);
    const step = type.steps.find(step => step.id === column.id);
    column.kind = STEP_KIND[step?.status || column.id.replace(/^status-/, '')] || '';
  }
  return boardColumnsHTML(columns, {head});
}
const taskByStepRank = (a, b) => (a.task.step_rank ?? Infinity) - (b.task.step_rank ?? Infinity)
  || String(a.task.created_at).localeCompare(String(b.task.created_at)) || String(a.task.id).localeCompare(String(b.task.id));
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
    visibility.hidden = type?.role !== 'product-roadmap';
    flag.checked = type?.default_visibility === 'public' && !form.elements.private?.checked;
    flag.disabled = !!form.elements.private?.checked;
    showPublicText();
    const manager = botByRef(type?.manager) || S.emps.find(e => type?.manager && e.template === type.manager);
    const value = manager && [...form.elements.owner.options].find(o => o.value === manager.id || o.value === 'bot:' + manager.id)?.value;
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
      <div>${type.steps.map(step => `<span class="tlabel"${step.check_pool?.length ? ` title="${esc('Check pool: ' + step.check_pool.map(botSlug).join(', ') + ' · ' + (step.check_limit || 2) + ' open checks each')}"` : ''}>${esc(step.name)}${step.check_pool?.length ? ` · ${step.check_pool.length} checker${step.check_pool.length === 1 ? '' : 's'}` : ''}</span>`).join(' ')}</div>
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
  const poolHints = async row => {
    const target = row.querySelector('[data-pool-models]'), value = row.querySelector('[aria-label="Check pool"]').value;
    const bots = [...new Set(value.split(',').map(slug => slug.trim()).filter(Boolean))].slice(0, 40);
    const request = (row._poolHintsRequest || 0) + 1;
    row._poolHintsRequest = request;
    if (!bots.length) { target.textContent = 'Enter bot slugs to see each configured model and provider.'; return; }
    target.textContent = 'Loading model details…';
    try {
      const data = await get('/v2/check-pool-candidates?bots=' + encodeURIComponent(bots.join(',')));
      if (request !== row._poolHintsRequest || !target.isConnected) return;
      target.innerHTML = (data.candidates || []).map(bot => {
        const configured = `${bot.configured.model} (${bot.configured.provider})`;
        const runtime = bot.runtime ? ` · latest run: ${bot.runtime.model} (${bot.runtime.provider}); next run may differ` : '';
        return `<span>${esc(bot.name)}: ${esc(configured + runtime)}</span>`;
      }).join('') || 'No visible bots found for these slugs.';
    } catch (error) {
      if (request === row._poolHintsRequest && target.isConnected) target.textContent = `Model details unavailable: ${error.message}`;
    }
  };
  // A check step's model company rule (docs/tasks.md, Step flow): prefer another company's bot, or require it.
  const companyRule = step => step?.flow?.check
    ? `<select aria-label="Another model company" title="Prefer: a bot of another model company than the builder's goes first, and one of the builder's company takes the check only when no other is free. Require: only a bot of another company takes it, and the check waits when none is free.">${['prefer', 'require'].map(rule => `<option value="${rule}"${(step.flow.other_company || 'prefer') === rule ? ' selected' : ''}>Another model company: ${rule === 'prefer' ? 'Prefer' : 'Require'}</option>`).join('')}</select>`
    : '';
  const addStep = step => {
    const row = document.createElement('div'); row.className = 'task-step-edit'; row.dataset.stepId = step?.id || ''; row._flow = step?.flow || null;
    row.innerHTML = `<input required aria-label="Step name" value="${esc(step?.name || '')}" placeholder="Step name">
      <input aria-label="Public step name" value="${esc(step?.public_name ?? '')}" placeholder="Public step (blank: not shown)" title="What a public roadmap shows for this step; blank shows none">
      <select aria-label="Step status">${PIPELINE_STATUSES.filter(status => STEP_STATUSES.includes(status) || step?.status === status).map(status => `<option value="${status}"${step?.status === status ? ' selected' : ''}>${esc(STATUS_WORD[status] || status)}</option>`).join('')}</select>
      <input aria-label="Check pool" value="${esc((step?.check_pool || []).map(botSlug).join(', '))}" placeholder="Check pool: bots, comma-separated" title="The bots that may do this step's check. Tico gives each check to the least busy one from another model company than the builder.">
      <input type="number" min="1" max="20" aria-label="Open checks each" value="${esc(String(step?.check_limit || 2))}" title="How many open checks each bot in the pool may hold">
      ${companyRule(step)}
      <small class="task-step-models muted" data-pool-models role="status">Enter bot slugs to see each configured model and provider.</small>
      <button class="ghost" type="button" data-up aria-label="Move step up">↑</button><button class="ghost" type="button" data-down aria-label="Move step down">↓</button>
      <button class="ghost danger" type="button" data-remove aria-label="Remove step">×</button>`;
    row.querySelector('[data-up]').onclick = () => { if (row.previousElementSibling) rows.insertBefore(row, row.previousElementSibling); };
    row.querySelector('[data-down]').onclick = () => { if (row.nextElementSibling) rows.insertBefore(row.nextElementSibling, row); };
    row.querySelector('[data-remove]').onclick = () => row.remove();
    row.querySelector('[aria-label="Check pool"]').addEventListener('input', () => { void poolHints(row); });
    rows.append(row);
    if (step?.check_pool?.length) void poolHints(row);
  };
  (type?.steps || [{name: 'Open', status: 'open'}]).forEach(addStep);
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
    // A public item on a type private by default shows its step's public name, never the internal one: the field is
    // always there, and a new step left blank gets the server's default (its name only on a type public by default).
    // A step's flow replaces the whole flow when it is sent, and names what it points at by name: so a changed rule is
    // sent with the flow's step references as ids (a name would not resolve once this save renames its step).
    const ids = Object.fromEntries((type?.steps || []).map(step => [step.name, step.id]));
    const flowFor = row => {
      const select = row.querySelector('[aria-label="Another model company"]');
      if (!select || select.value === (row._flow?.other_company || 'prefer')) return {};
      const flow = Object.fromEntries(Object.entries(row._flow || {}).map(([key, value]) =>
        [key, ['on_pass', 'on_fail', 'after_green'].includes(key) && ids[value] ? ids[value] : value]));
      return {flow: {...flow, other_company: select.value}};
    };
    const steps = [...rows.children].map((row, position) => {
      const shown = row.querySelector('[aria-label="Public step name"]').value.trim();
      return {...(row.dataset.stepId ? {id: row.dataset.stepId} : {}), name: row.querySelector('input').value,
        ...(shown || row.dataset.stepId ? {public_name: shown} : {}), status: row.querySelector('[aria-label="Step status"]').value, position,
        ...flowFor(row),
        check_pool: row.querySelector('[aria-label="Check pool"]').value.split(',').map(slug => slug.trim()).filter(Boolean),
        check_limit: Math.min(20, Math.max(1, parseInt(row.querySelector('[aria-label="Open checks each"]').value, 10) || 2))};
    });
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
