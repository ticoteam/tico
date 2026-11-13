/* ui/app/settings-bots-tasks.ts — Settings > Bots: a bot's task settings (task model, Task effort, Task context length), their row and saving
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// One change to a bot's task settings (settingsTaskBody): a task model (null for Same as bot), a task effort ('' for
// Same as bot) or a task context length (null for Same as bot). A part left out is carried as it is.
interface SettingsTaskChange { choice?: SettingsChoice | null; effort?: string; compact_at?: number | null; }
// POST /v2/bots/<bot>/task-model: what the bot's tasks now run on, whether its computer can run it, and the warning.
interface SettingsTaskSaved {
  task_model?: SettingsTaskModel | null; readiness?: { can_run?: boolean; problem?: string } | null; warning?: string | null; revision: number;
}
// An <option> as the Task effort and Task context length selects draw it.
type SettingsTaskOption = (value: string | number, label: string, selected: boolean, disabled?: boolean) => string;

// The efforts a model takes, as its Effort picker lists them (a Gateway pick by its tier's current version).
function settingsModelEfforts(id: string | null | undefined): string[] {
  const t = settingsTierParse(id), row = (t && settingsModel(`${t.tier}-${SETTINGS_DATA.gateway?.tiers[t.tier]?.current}`)) || settingsModel(id);
  return row?.efforts?.length ? [...row.efforts] : t ? ['low', 'high', 'max'] : [];
}
// What the bot's tasks run on, in a few words: "Claude Haiku 5.5 · high · 1M", "the bot's model · high". The end is the
// model's window, never the task's compaction length (that is the Tasks mark's own "Tasks · 600k").
function settingsTaskWords(task: SettingsTaskModel | null | undefined) {
  return task?.model ? settingsChoiceLabel(task.harness || task.runtime, task.model, task.reasoning_effort || task.effort)
    : ["the bot's model", settingsEffortName(task?.reasoning_effort)].filter(Boolean).join(' · ');
}
// The /task-model body for one change, carrying the other parts as they are: `choice` a task model ({harness, model,
// effort}, null for Same as bot), `effort` a task effort ('' for Same as bot), `compact_at` a task context length (null
// for Same as bot). The effort goes only where the model that runs tasks takes it; a new task model otherwise takes
// its own picker's. Null when all three are the same as the bot.
function settingsTaskBody(e: SettingsBot, change: SettingsTaskChange = {}) {
  const was: SettingsTaskModel = e.task_model || {};
  const choice = 'choice' in change ? change.choice : was.model ? {harness: was.harness || was.runtime, model: was.model, effort: was.reasoning_effort} : null;
  let effort = 'effort' in change ? change.effort || '' : was.reasoning_effort || '';
  const compact_at = 'compact_at' in change ? change.compact_at || null : was.compact_at || null;
  if (effort && !settingsModelEfforts(settingsTaskRunsOn(e, choice?.model)).includes(effort)) effort = '';
  if (choice?.model && !effort) effort = choice.effort || '';
  if (!choice?.model && !effort && !compact_at) return null;
  return choice?.model ? {harness: choice.harness, model: choice.model, effort: effort || null, compact_at}
    : {model: null, effort: effort || null, compact_at};
}
// The Task effort choices for the model that runs tasks; "Same as bot" only while that is the bot's own model.
function settingsTaskEffortPaint(select: HTMLSelectElement, efforts: string[], current: string, own: boolean) {
  const option = (value: string | number, label: string, selected: boolean, disabled = false) => `<option value="${esc(value)}" ${selected ? 'selected' : ''} ${disabled ? 'disabled' : ''}>${esc(label)}</option>`;
  select.innerHTML = settingsTaskEffortOptions(efforts, current, own, option);
  select.disabled = !select.hasAttribute('data-manage') || efforts.length <= (own ? 0 : 1);
}
function settingsTaskEffortOptions(efforts: string[], current: string, own: boolean, option: SettingsTaskOption) {
  return (own ? option('', 'Same as bot', !current) : '')
    + (current && !efforts.includes(current) ? option(current, `${settingsEffortName(current)} (current)`, true, true) : '')
    + efforts.map(id => option(id, id === 'as-configured' ? 'Managed by harness' : settingsEffortName(id), id === current)).join('');
}
// Task effort and Task context length beside the Task model: labelled like its pickers in the bots table's task row
// (`row`), rows like Compact at in the bot editor.
function settingsTaskParts(e: SettingsBot, row = false) {
  const task = e.task_model, own = !task?.model, manage = settingsCanManageBot(e), name = e.display_name || e.slug;
  const effort = task?.reasoning_effort || '', context = task?.compact_at || null;
  const efforts = settingsModelEfforts(settingsTaskRunsOn(e));
  const option = (value: string | number, label: string, selected: boolean, disabled = false) => `<option value="${esc(value)}" ${selected ? 'selected' : ''} ${disabled ? 'disabled' : ''}>${esc(label)}</option>`;
  const sizes: [number, string][] = SETTINGS_COMPACT_AT.some(([size]) => size === context) || !context ? SETTINGS_COMPACT_AT : [...SETTINGS_COMPACT_AT, [context, settingsContextName(context)]];
  const cls = row ? '' : 'class="settings-inline-select"';
  const effortSelect = `<select ${cls} data-task-effort="${esc(e.id)}" data-current="${esc(effort)}" ${manage ? 'data-manage' : ''}
    aria-label="Task effort for ${esc(name)}" ${manage && efforts.length > (own ? 0 : 1) ? '' : 'disabled'}>${settingsTaskEffortOptions(efforts, effort, own, option)}</select>`;
  const contextSelect = `<select ${cls} data-task-context="${esc(e.id)}" data-current="${context || ''}" aria-label="Task context length for ${esc(name)}" ${manage ? '' : 'disabled'}>
    ${option('', 'Same as bot', !context)}${sizes.map(([size, label]) => option(size, label, size === context)).join('')}</select>`;
  // A task on a harness that compacts on its own default (Claude Code) has no context length to pick.
  const ownCompaction = harnessCaps(task?.harness || e.harness || e.runtime || e.resolved_runtime || undefined).own_compaction;
  return row ? `<label class="sb-task-part">Task effort${effortSelect}</label>${ownCompaction ? '' : `<label class="sb-task-part">Task context length${contextSelect}</label>`}`
    : `<div class="sb-row"><span>Task effort</span>${effortSelect}<span></span></div>
    ${ownCompaction ? '' : `<div class="sb-row"><span>Task context length</span>${contextSelect}<span></span></div>`}`;
}
// The bots table's task indicator, beside the model: faint while tasks run as the bot does, marked when any task
// setting differs ("Tasks · 600k" with a task context length). It opens the bot's task row under it
// (SETTINGS_BOTS_VIEW.taskOpen).
function settingsTaskIndicator(e: SettingsBot) {
  const task = e.task_model, set = settingsTaskSet(task), open = SETTINGS_BOTS_VIEW.taskOpen.has(e.id);
  const label = set ? `Tasks run on ${settingsTaskWords(task)}` : 'Tasks run on the same model as the bot';
  const context = settingsContextName(task?.compact_at);
  if (!settingsCanManageBot(e) && !set) return '';
  return `<button type="button" class="sb-task-mark${set ? ' on' : ''}" data-task-open="${esc(e.id)}" aria-expanded="${open}"
    aria-label="${esc(`${label}. Task settings for ${e.display_name || e.slug}`)}" title="${esc(label)}" ${settingsCanManageBot(e) ? '' : 'disabled'}>Tasks${context ? ` · ${esc(context)}` : ''}</button>`;
}
// The row a task indicator opens under its bot: the task model's pickers and Apply, Task effort and Task context
// length (each saves on its own when changed), and Close. The bot's own model never changes here.
function settingsTaskRow(e: SettingsBot, columns: number) {
  return `<tr class="sb-task-row" data-task-row="${esc(e.id)}"><td colspan="${columns}"><div class="sb-task-under">
    <span class="sb-task-head">Tasks · ${esc(e.display_name || e.slug)}</span>${settingsChoiceCombo(e, 'task')}${settingsTaskParts(e, true)}
    <button type="button" class="ghost" data-task-close="${esc(e.id)}">Close</button></div>
    ${settingsTaskWarning(e)}</td></tr>`;
}
// A Task effort or Task context length changed in the bots table's task row: saved at once, the rest kept.
async function settingsSaveTaskPart(select: HTMLSelectElement) {
  const effort = 'taskEffort' in select.dataset, slug = select.dataset.taskEffort || select.dataset.taskContext;
  const e: SettingsBot | undefined = S.emps.find(row => row.id === slug);
  if (!e || select.value === (select.dataset.current || '')) return;
  select.disabled = true;
  try { await settingsSaveTaskModel(e, effort ? {effort: select.value} : {compact_at: Number(select.value) || null}, e.revision); }
  catch (error) { select.value = select.dataset.current || ''; toast(errorMessage(error, ''), true); }
  finally {
    if (select.isConnected) select.disabled = false;
    else $(`[data-task-row] [${effort ? 'data-task-effort' : 'data-task-context'}="${CSS.escape(String(slug))}"]`)?.focus();
  }
}
// The plain-words warning for a cheaper task model on a high-stakes bot, as the server judges it against the bot's
// model now (`task_model_warning`, backend/task_models.py `warning`), under the task model in the row and the editor.
function settingsTaskWarning(e: SettingsBot) {
  return e.task_model_warning ? `<p class="sb-task-warning" role="status" data-task-warning>${esc(e.task_model_warning)}</p>` : '';
}
// POST one change to the bot's task settings (settingsTaskBody: the other parts are carried as they are) and say what
// its tasks now run on. A pick its computer cannot run yet saves all the same, as does a cheaper one on a high-stakes
// bot: the toast and the row say so. The bot editor's Save passes `inEditor`: it reloads and toasts once at the end,
// and takes the revision this made.
async function settingsSaveTaskModel(e: SettingsBot, change: SettingsTaskChange, revision: number | undefined, inEditor = false) {
  const body = settingsTaskBody(e, change);
  const saved: SettingsTaskSaved = await post(`/v2/bots/${encodeURIComponent(e.id)}/task-model`, {task_model: body, expected_revision: revision});
  const task = saved.task_model !== undefined ? saved.task_model : body && {...body, reasoning_effort: body.effort};
  const cannot = saved.readiness?.can_run === false ? ` For now they run on its own model: ${saved.readiness.problem}.` : '';
  const said = `${settingsTaskSet(task) ? `${e.display_name}'s tasks run on ${settingsTaskWords(task)}.`
    : `${e.display_name}'s tasks run on the same model as ${e.display_name}.`}${cannot}${saved.warning ? ` ${saved.warning}` : ''}`;
  if (inEditor) { if (cannot || saved.warning) toast(said); return saved.revision; }
  await loadSettings();
  toast(said);
  return saved.revision;
}
