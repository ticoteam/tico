'use strict';

// The task types the page has read (get /v2/task-types). The rail also reads who owns a pipeline (pipeline_owner_id).
type PipelineTaskType = import('../types/api').TaskType & { pipeline_owner_id?: string | null };
// The part of a task a pipeline reads: its type, as a task row or the task dialog carries it (steps_only: a decision item
// carries its type's flag too, decide.ts).
type PipelineTaskRef = { type_id?: string | null; type?: { id: string } | null; steps_only?: boolean };
// The part of the Tasks page's state a pipeline reads: the type chosen, and the view shown (the board shows the type's steps).
type PipelineTypeChoice = { type?: string; view?: string };
type PipelineStep = import('../types/api').TaskStep;
// The Tasks page's state, with the type choice a person made on it (tasks-page.ts reads typeChosen).
type PipelineState = TaskState & { typeChosen?: boolean };
// A step row's own state: its flow (the check's company rule) and the pool hints' request counter.
interface HTMLDivElement {
  _flow?: Record<string, unknown> | null;
  _poolHintsRequest?: number;
}
// The create form's controls by name (the task create form, tasks-page.ts).
interface PipelineCreateElements extends HTMLFormControlsCollection {
  owner: HTMLSelectElement;
  type?: HTMLSelectElement;
  private?: HTMLInputElement;
  public?: HTMLInputElement;
  publicTitle?: HTMLInputElement;
  publicSummary?: HTMLTextAreaElement;
}
interface PipelineCreateForm extends HTMLFormElement {
  readonly elements: PipelineCreateElements;
}
// What the check pool hints read (GET /v2/check-pool-candidates): each slug's configured model and its latest run's.
interface CheckPoolCandidates {
  candidates?: { name: string; configured: { model: string; provider: string }; runtime?: { model: string; provider: string } | null }[];
}

let TASK_TYPES: PipelineTaskType[] = [];
let TASK_TYPES_LOADING: Promise<PipelineTaskType[]> | null = null;
const PIPELINE_STATUSES = ['open', 'doing', 'waiting', 'review', 'ready', 'done', 'closed', 'declined'];
// A step's status is one of these. Waiting is a tag on a task in its step, so a waiting step is a team's own column for
// parked work that only a move by hand fills; declining goes to the closed step.
const STEP_STATUSES = ['open', 'doing', 'waiting', 'review', 'ready', 'done', 'closed'];
// The board shows a type's working steps; its done and closed tasks are under Done and Closed.
const BOARD_HIDDEN = new Set(['done', 'closed', 'declined']);
const pipelineTypeId = (task: PipelineTaskRef) => task.type_id || task.type?.id || 'general';
const pipelineType = (task: PipelineTaskRef) => TASK_TYPES.find(type => type.id === pipelineTypeId(task));
// A steps-only type's tasks change status only by a step move: no Done or Reopen of their own. A person can still close one.
const pipelineStepsOnly = (task: PipelineTaskRef) => !!pipelineType(task)?.steps_only;
const pipelineSelectedType = (state: PipelineTypeChoice) => TASK_TYPES.find(type => type.id === state?.type);
const pipelineActionStatus = (task: PipelineTaskRef, body: { status?: string; step?: string | null }) => body.status || pipelineType(task)?.steps.find(step => step.id === body.step)?.status;
// A person's move into a full stage stands, and the server says so (over_cap, D68): one line, not a refusal.
const taskOverCapWarn = (task: PipelineTaskRef, body: {step?: string}, data: {over_cap?: boolean} | null | undefined): void => {
  if (!data?.over_cap) return;
  const step = pipelineType(task)?.steps.find(s => s.id === body.step);
  toast(`${step?.name ?? 'That stage'} is over its cap of ${step?.card_cap ?? ''}.`);
};
// A stage's held count on the pipeline (D68): the unfinished cards the task list holds in it now, so a card a live change
// moved out is not still counted (the server's step.held is only as fresh as the last types read), plus step.unseen: the
// cards there the pipeline's rule editor cannot read, which the server counts for it. Its count is then never below the
// one the cap is enforced against.
function pipelineStageHeld(tasks: {step_id?: string | null; status?: unknown}[], step: {id: string; unseen?: number}): number {
  return tasks.filter(t => t.step_id === step.id && !BOARD_HIDDEN.has(String(t.status))).length + (step.unseen ?? 0);
}
const pipelineStepName = (id: string) => TASK_TYPES.flatMap(type => type.steps).find(step => step.id === id)?.name || id;
// The Product roadmap and Dev tickets types go by their role (backend/roadmap_flow.py), never by who manages them.
const pipelineRoleType = (role: string | null | undefined) => TASK_TYPES.find(type => type.role === role);
const pipelineProductType = () => pipelineRoleType('product-roadmap');
let TASK_TYPES_READ = false;
// `cached`: the types already read do (a task opened); the Tasks page's loads read them again, so an edit elsewhere shows.
async function taskTypesLoad({cached = false}: {cached?: boolean} = {}) {
  if (!S.me?.cloud) return TASK_TYPES;
  if (cached && TASK_TYPES_READ) return TASK_TYPES;
  if (!TASK_TYPES_LOADING) TASK_TYPES_LOADING = get<import('../types/api').TaskTypeList>('/v2/task-types').then(data => {
    TASK_TYPES = data.types || [];
    TASK_TYPES_READ = true;
    return TASK_TYPES;
  }).finally(() => { TASK_TYPES_LOADING = null; });
  return TASK_TYPES_LOADING;
}
function taskPipelineState(state: TaskState) {
  state.type = 'general';
  if (!S.me?.cloud) return;
  try { state.type = localStorage.getItem('tico.tasks.type') || 'general'; } catch {}
}
function taskPipelineRemember(state: PipelineTypeChoice) {
  try { localStorage.setItem('tico.tasks.type', state.type || 'general'); } catch {}
}
// Open, Board and finished tasks belong to one type; a selected type turns the board into its steps.
function taskPipelineMatches(task: PipelineTaskRef, state: PipelineTypeChoice) {
  return pipelineTypeId(task) === (state.type || 'general');
}
// Common types stay one click away; other team-defined types share the last segment.
// Engineering and a Dev tickets type made later may both have the engineering role; Dev tickets goes first.
function taskPipelineDevType() {
  const dev = (type: PipelineTaskType) => /^dev(?:[ -]?tickets?)?$/i.test(type.name) || /^dev(?:[ -]?tickets?)?$/i.test(type.id);
  const engineering = TASK_TYPES.filter(type => type.role === 'engineering');
  return engineering.find(dev) || engineering[0] || TASK_TYPES.find(dev);
}
// The types the scope menu lists (task-list.js tasksScopeMenu): General, the product and engineering pipelines by
// their role, then the team's other types. Each goes by its own current name.
function taskPipelineScopeTypes() {
  const dev = taskPipelineDevType();
  const product = pipelineProductType();
  const other = TASK_TYPES.filter(type => type.id !== 'general' && type.id !== dev?.id && type.id !== product?.id);
  return [{id: 'general', label: 'General'}, ...(product ? [{id: product.id, label: product.name}] : []),
    ...(dev ? [{id: dev.id, label: dev.name}] : []),
    ...other.map(type => ({id: type.id, label: type.name, other: true}))];
}
const taskPipelineLabel = (id: string | undefined) => taskPipelineScopeTypes().find(t => t.id === id)?.label || 'General';
function taskPipelineTools(state: PipelineTypeChoice) {
  $('#task-q')?.setAttribute('aria-label', `Search ${pipelineSelectedType(state)?.name || 'General'} tasks`);
}
function taskPipelineSelect(state: PipelineState, id: string) {
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
// A type's board: a column per step, in the type's order, that cards are dragged between (ui/app/task-board.ts). Its
// leading backlog steps (open, before the first step with work under way) and its trailing finished steps each fold
// into one strip; a finished step part way along stays off the board.
function taskPipelineBoard(items: TaskItem[], state: TaskState, head = '') {
  const type = pipelineSelectedType(state);
  if (!type) return null;
  items = items.map(item => taskBoardLive(state, item));
  const steps = type.steps;
  let end = steps.length;
  while (end > 0 && BOARD_HIDDEN.has(steps[end - 1]!.status)) end--;
  const finished = steps.slice(end), working = steps.slice(0, end).filter(step => !BOARD_HIDDEN.has(step.status));
  let lead = 0;
  while (lead < working.length && working[lead]!.status === 'open') lead++;
  // A phone shows every pipeline one stage at a time, with a chip per stage, requirements or not.
  const phone = pipelinePhone();
  const backlog = working.slice(0, lead), folds = taskBoardFolds(state, type, backlog.length);
  const inStep = (step: PipelineStep) => items.filter(item => item.task.step_id === step.id);
  // The Product board's In development column alone reads "In <Engineering> · N": the column the work waits in while Engineering builds it.
  const headed = (step: PipelineStep) => pipelineProductType()?.id === type.id && step.name === 'In development';
  const stepColumn = (step: PipelineStep) => ({id: step.id, name: step.name, label: (headed(step) && stageHandoffLabel(type.id, step.id)) || undefined, drop: step.id, items: inStep(step), scope: {type: type.id, step: step.id}, limit: step.limit_minutes, cap: step.card_cap ?? null, held: pipelineStageHeld(state.tasks || [], step)});
  const strip = (fold: string, run: PipelineStep[], drop: string, name: string) => ({id: 'fold-' + fold, fold, drop, items: [], name: run.length === 1 ? run[0]!.name : name,
    hint: run.map(step => step.name).join(', '), count: fold === 'backlog' ? run.reduce((n, step) => n + inStep(step).length, 0) : ''});
  const columns: BoardColumn[] = [];
  if (backlog.length && folds.backlog) columns.push(strip('backlog', backlog, backlog.at(-1)!.id, 'Backlog'));
  else backlog.forEach((step, i) => columns.push({...stepColumn(step), foldable: i ? '' : 'backlog'}));
  working.slice(lead).forEach(step => columns.push(stepColumn(step)));
  const shown = [...working, ...(folds.finished ? [] : finished)];
  const unmapped = items.filter(item => !shown.some(step => step.id === item.task.step_id) && !backlog.some(step => step.id === item.task.step_id));
  for (const status of PIPELINE_STATUSES.filter(status => !BOARD_HIDDEN.has(status))) {
    const list = unmapped.filter(item => item.task.status === status);
    if (list.length) columns.push({id: 'status-' + status, name: STATUS_WORD[status] || status, items: list});
  }
  if (finished.length && folds.finished) columns.push(strip('finished', finished, finished[0]!.id, 'Finished'));
  else finished.forEach((step, i) => columns.push({...stepColumn(step), foldable: i ? '' : 'finished'}));
  const STEP_KIND: Record<string, string> = {open: 'starting', doing: 'doing', waiting: 'waiting', review: 'review', ready: 'review', done: 'done', closed: 'closed', declined: 'needs'};
  for (const column of columns) {
    column.items.sort(taskByStepRank);
    Object.assign(column, {play: pipelineForYouHTML(pipelineForYouCount(column.items), type.id, column.id, true)});
    const step = type.steps.find(step => step.id === column.id);
    column.kind = STEP_KIND[step?.status || column.id.replace(/^status-/, '')] || '';
  }
  const one = phone ? pipelinePhoneBoard(state, columns) : null;
  if (one) return head + one.chips + pipePhoneStageHTML(one.column as BoardColumn, type, one.next);
  return boardColumnsHTML(columns, {head});
}
// A phone's one stage: its cards full width, under a quiet line with the "warn after" limit, the compact ▶ N and select-all.
// The stage's name is on its chip only, so the line does not repeat it. The column box and its header do not show.
// The "Swipe for <next stage>" hint sits under the last card, and only on a stage with cards.
function pipePhoneStageHTML(column: BoardColumn, type: import('../types/api').TaskType, next = '') {
  const limit = column.limit ? `warn after ${pipelineLimit(column.limit)}` : '';
  const scope = column.scope ? ` data-board-type="${esc(column.scope.type)}" data-board-step="${esc(column.scope.step)}"` : '';
  const all = column.items.length && tasksCanSelect()
    ? `<button type="button" class="bcol-all" data-board-select="${esc(column.id)}"${scope} aria-label="${esc(`Select all in ${column.name}`)}" title="Select all in this column">${TL_ICON.all}</button>` : '';
  const play = pipelineForYouHTML(pipelineForYouCount(column.items), type.id, column.id, true);
  const release = RELEASE_COLUMN.test(column.name) ? '<span class="board-release-slot" data-release-slot></span>' : '';
  const line = limit || play || release || all
    ? `<div class="pipe-stage-line">${limit ? `<span class="pipe-stage-limit">${esc(limit)}</span>` : ''}${play}${release}${all}</div>` : '';
  const notices = taskBuilderNotices([...new Set(column.items.flatMap(it => taskBuilderActors(it.task)))], true);
  const cards = column.items.map(taskCard).join('') || '<div class="empty">Nothing here</div>';
  return `<div class="pipe-phone"><section class="pipe-stage" data-col="${esc(column.id)}" aria-label="${esc(column.name)}">
    ${line}${notices}<div class="pipe-stage-cards">${cards}</div>${column.items.length ? next : ''}</section></div>`;
}
const taskByStepRank = (a: TaskItem, b: TaskItem) => (a.task.step_rank ?? Infinity) - (b.task.step_rank ?? Infinity)
  || String(a.task.created_at).localeCompare(String(b.task.created_at)) || String(a.task.id).localeCompare(String(b.task.id));
// A task's Type and Step are rows in its properties (ui/app/task-props.ts).
async function taskPipelineCreate(form: PipelineCreateForm, selected = '') {
  form.dataset.taskType = selected || 'general';
  try { await taskTypesLoad(); } catch { return; }
  if (!form.isConnected || TASK_TYPES.length < 2 || form.elements.type) return;
  const row = document.createElement('label'); row.textContent = 'Type ';
  const select = document.createElement('select'); select.name = 'type'; select.setAttribute('aria-label', 'Type');
  select.innerHTML = TASK_TYPES.map(type => `<option value="${esc(type.id)}">${esc(type.name)}</option>`).join('');
  select.value = TASK_TYPES.some(type => type.id === selected) ? selected : 'general';
  row.append(select); form.querySelector('label')?.after(row);
  const visibility = document.createElement('label'); visibility.className = 'prop-check roadmap-check';
  const flag = document.createElement('input'); flag.type = 'checkbox'; flag.name = 'public'; flag.setAttribute('aria-label', 'Public');
  visibility.append(flag, 'Public');
  // Public text is written here, never copied from the internal title and body.
  const publicText = document.createElement('div'); publicText.className = 'roadmap-publication roadmap-create-public';
  publicText.innerHTML = '<label>Public title<input name="publicTitle" aria-label="Public title" maxlength="300" placeholder="Needed to publish"></label>'
    + '<label>Public summary<textarea name="publicSummary" aria-label="Public summary" rows="2" maxlength="8000" placeholder="Optional"></textarea></label>';
  const showPublicText = () => {
    publicText.hidden = visibility.hidden || !flag.checked;
    const titleInput = publicText.querySelector('input');
    if (titleInput) titleInput.required = !publicText.hidden;
  };
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
function taskPipelineCreatePayload(form: PipelineCreateForm, payload: Record<string, unknown>) {
  payload.type = form.elements.type?.value || form.dataset.taskType || 'general';
  if (form.elements.public && !form.elements.public.closest('label')?.hidden) payload.visibility = form.elements.public.checked ? 'public' : 'private';
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
function taskTypesSettingsShow(tab: string) {
  if (tab === 'tasks' && S.me?.cloud) void renderTaskTypes();
}

async function renderTaskTypes() {
  const host: HTMLElement | null = $('#set-types'); if (!host) return;
  host.textContent = 'Loading…';
  try {
    await taskTypesLoad();
    if (!host.isConnected) return;
    const mover = canMove();
    host.innerHTML = TASK_TYPES.map(type => `<div class="task-type-row"><strong>${esc(type.name)}</strong>
      <div>${type.steps.map(step => `<span class="tlabel"${step.check_pool?.length ? ` title="${esc('Check pool: ' + step.check_pool.map(botSlug).join(', ') + ' · ' + (step.check_limit || 2) + ' open checks each')}"` : ''}>${esc(step.name)}${step.check_pool?.length ? ` · ${step.check_pool.length} checker${step.check_pool.length === 1 ? '' : 's'}` : ''}</span>`).join(' ')}</div>
      ${mover && type.id !== 'general' ? `<button class="ghost" type="button" data-edit-type="${esc(type.id)}">Edit</button>` : ''}</div>`).join('')
      + (mover ? '<button class="primary" type="button" data-new-type>New type</button>' : '');
    host.querySelectorAll<HTMLButtonElement>('[data-edit-type]').forEach(button => button.onclick = () => taskTypeEditor(TASK_TYPES.find(type => type.id === button.dataset.editType)));
    const add = host.querySelector<HTMLButtonElement>('[data-new-type]'); if (add) add.onclick = () => taskTypeEditor();
  } catch (error) { host.textContent = errorMessage(error, ''); }
}
// What every bot may do with company tasks beyond reading (docs/tasks.md, "Types bots work on").
const TYPE_BOTS = [['parties', 'Participants change their tasks'], ['read', 'Every bot may comment and create subtasks'],
  ['work', 'Every bot reads and works on all of them']];
function taskTypeEditor(type: PipelineTaskType | null = null) {
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
  const close = dialog.querySelector<HTMLButtonElement>('[data-close]');
  if (close) close.onclick = () => dialog.close();
  const rows = dialog.querySelector<HTMLDivElement>('[data-steps]');
  if (!rows) return;
  const poolHints = async (row: HTMLDivElement) => {
    const target = row.querySelector<HTMLElement>('[data-pool-models]');
    const pool = row.querySelector<HTMLInputElement>('[aria-label="Check pool"]');
    if (!target || !pool) return;
    const bots = [...new Set(pool.value.split(',').map(slug => slug.trim()).filter(Boolean))].slice(0, 40);
    const request = (row._poolHintsRequest || 0) + 1;
    row._poolHintsRequest = request;
    if (!bots.length) { target.textContent = 'Enter bot slugs to see each configured model and provider.'; return; }
    target.textContent = 'Loading model details…';
    try {
      const data = await get<CheckPoolCandidates>('/v2/check-pool-candidates?bots=' + encodeURIComponent(bots.join(',')));
      if (request !== row._poolHintsRequest || !target.isConnected) return;
      target.innerHTML = (data.candidates || []).map(bot => {
        const configured = `${bot.configured.model} (${bot.configured.provider})`;
        const runtime = bot.runtime ? ` · latest run: ${bot.runtime.model} (${bot.runtime.provider}); next run may differ` : '';
        return `<span>${esc(bot.name)}: ${esc(configured + runtime)}</span>`;
      }).join('') || 'No visible bots found for these slugs.';
    } catch (error) {
      if (request === row._poolHintsRequest && target.isConnected) target.textContent = `Model details unavailable: ${errorMessage(error, '')}`;
    }
  };
  // A check step's model company rule (docs/tasks.md, Step flow): prefer another company's bot, or require it.
  const companyRule = (step: Partial<PipelineStep> | null | undefined) => {
    const flow = step?.flow;
    return flow?.check
      ? `<select aria-label="Another model company" title="Prefer: a bot of another model company than the builder's goes first, and one of the builder's company takes the check only when no other is free. Require: only a bot of another company takes it, and the check waits when none is free.">${['prefer', 'require'].map(rule => `<option value="${rule}"${(flow.other_company || 'prefer') === rule ? ' selected' : ''}>Another model company: ${rule === 'prefer' ? 'Prefer' : 'Require'}</option>`).join('')}</select>`
      : '';
  };
  const addStep = (step?: Partial<PipelineStep> | null) => {
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
    const up = row.querySelector<HTMLButtonElement>('[data-up]');
    const down = row.querySelector<HTMLButtonElement>('[data-down]');
    const removeButton = row.querySelector<HTMLButtonElement>('[data-remove]');
    const pool = row.querySelector<HTMLInputElement>('[aria-label="Check pool"]');
    if (!up || !down || !removeButton || !pool) return;
    up.onclick = () => { if (row.previousElementSibling) rows.insertBefore(row, row.previousElementSibling); };
    down.onclick = () => { if (row.nextElementSibling) rows.insertBefore(row.nextElementSibling, row); };
    removeButton.onclick = () => row.remove();
    pool.addEventListener('input', () => { void poolHints(row); });
    rows.append(row);
    if (step?.check_pool?.length) void poolHints(row);
  };
  (type?.steps || [{name: 'Open', status: 'open'}]).forEach(addStep);
  const addButton = dialog.querySelector<HTMLButtonElement>('[data-add-step]');
  if (addButton) addButton.onclick = () => addStep();
  const saved = async (body: unknown) => {
    const submit = dialog.querySelector<HTMLButtonElement>('[type=submit]');
    if (!submit) return;
    submit.disabled = true;
    try {
      await post('/v2/task-types' + (type ? '/' + encodeURIComponent(type.id) : ''), body);
      dialog.close(); await renderTaskTypes();
      if (TASKS_ST && $('#task-filter')) await tasksLoad(TASKS_ST);
    } catch (error) {
      const errorLine = dialog.querySelector<HTMLElement>('[data-error]');
      if (errorLine) errorLine.textContent = errorMessage(error, '');
      submit.disabled = false;
    }
  };
  const form = dialog.querySelector<HTMLFormElement>('form');
  if (form) form.onsubmit = event => {
    event.preventDefault();
    // A public item on a type private by default shows its step's public name, never the internal one: the field is
    // always there, and a new step left blank gets the server's default (its name only on a type public by default).
    // A step's flow replaces the whole flow when it is sent, and names what it points at by name: so a changed rule is
    // sent with the flow's step references as ids (a name would not resolve once this save renames its step).
    const ids: Record<string, string> = Object.fromEntries((type?.steps || []).map(step => [step.name, step.id]));
    const flowFor = (row: HTMLDivElement) => {
      const select = row.querySelector<HTMLSelectElement>('[aria-label="Another model company"]');
      if (!select || select.value === (row._flow?.other_company || 'prefer')) return {};
      const flow = Object.fromEntries(Object.entries(row._flow || {}).map(([key, value]) =>
        [key, ['on_pass', 'on_fail', 'after_green'].includes(key) && typeof value === 'string' && ids[value] ? ids[value] : value]));
      return {flow: {...flow, other_company: select.value}};
    };
    const steps = [...rows.children].filter((row): row is HTMLDivElement => row instanceof HTMLDivElement).map((row, position) => {
      const shown = (row.querySelector<HTMLInputElement>('[aria-label="Public step name"]')?.value ?? '').trim();
      return {...(row.dataset.stepId ? {id: row.dataset.stepId} : {}), name: row.querySelector<HTMLInputElement>('input')?.value ?? '',
        ...(shown || row.dataset.stepId ? {public_name: shown} : {}), status: row.querySelector<HTMLSelectElement>('[aria-label="Step status"]')?.value ?? '', position,
        ...flowFor(row),
        check_pool: (row.querySelector<HTMLInputElement>('[aria-label="Check pool"]')?.value ?? '').split(',').map(slug => slug.trim()).filter(Boolean),
        check_limit: Math.min(20, Math.max(1, parseInt(row.querySelector<HTMLInputElement>('[aria-label="Open checks each"]')?.value ?? '', 10) || 2))};
    });
    void saved({name: dialog.querySelector<HTMLInputElement>('[name=typeName]')?.value ?? '', steps, bots: dialog.querySelector<HTMLSelectElement>('[name=typeBots]')?.value ?? '', default_visibility: dialog.querySelector<HTMLSelectElement>('[name=defaultVisibility]')?.value ?? ''});
  };
  const remove = dialog.querySelector<HTMLButtonElement>('[data-delete-type]');
  if (remove && type) remove.onclick = async () => {
    remove.disabled = true;
    try { await post('/v2/task-types/' + encodeURIComponent(type.id) + '/delete'); dialog.close(); await renderTaskTypes(); }
    catch (error) {
      const errorLine = dialog.querySelector<HTMLElement>('[data-error]');
      if (errorLine) errorLine.textContent = errorMessage(error, '');
      remove.disabled = false;
    }
  };
  dialog.querySelector<HTMLInputElement>('[name=typeName]')?.focus();
}
