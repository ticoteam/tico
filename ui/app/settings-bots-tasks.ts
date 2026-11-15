/* ui/app/settings-bots-tasks.ts — Settings > Bots: a bot's task settings (task model, effort and window, all in the Task
   model picker), their row and saving
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// One change to a bot's task settings (settingsTaskBody): a task model (null for Same as bot), a task effort ('' for
// Same as bot) or a task context length (null for Same as bot). A part left out is carried as it is.
interface SettingsTaskChange { choice?: SettingsChoice | null; effort?: string; compact_at?: number | null; }
// POST /v2/bots/<bot>/task-model: what the bot's tasks now run on, whether its computer can run it, and the warning.
interface SettingsTaskSaved {
  task_model?: SettingsTaskModel | null; readiness?: { can_run?: boolean; problem?: string } | null; warning?: string | null; revision: number;
}

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
// The whole task-model change a Task model picker's Apply makes (its value and window): model, effort and window in
// one save. A value with no model is Same as bot, at its own effort when it holds one ('::::high').
function settingsTaskChangeOf(value: string, compact: number | null): SettingsTaskChange {
  const c = settingsChoiceFromValue(value);
  return {choice: c.model ? c : null, effort: c.effort, compact_at: compact};
}
// The bots table's task line, beside the model: faint "Tasks: same" while tasks run as the bot does; the task model in
// plain words ("Tasks: Opus 5.5 MED") when it differs, or "Tasks · HIGH · 600k" when only the effort or context length
// does. It opens
// the bot's task row under it (SETTINGS_BOTS_VIEW.taskOpen).
function settingsTaskIndicator(e: SettingsBot) {
  const task = e.task_model, set = settingsTaskSet(task), open = SETTINGS_BOTS_VIEW.taskOpen.has(e.id);
  const label = set ? `Tasks run on ${settingsTaskWords(task)}` : 'Tasks run on the same model as the bot';
  const context = settingsContextName(task?.compact_at);
  if (!settingsCanManageBot(e) && !set) return '';
  const spec = settingsChoiceSpecOf(e, 'task');
  const effort = settingsEffortShort(task?.reasoning_effort);
  const words = task?.model ? `Tasks: ${settingsPickWords(spec, spec.current, spec.compact).short}`
    : `Tasks${set ? '' : ': same'}${effort ? ` · ${effort}` : ''}${context ? ` · ${context}` : ''}`;
  return `<button type="button" class="sb-task-mark${set ? ' on' : ''}" data-task-open="${esc(e.id)}" aria-expanded="${open}"
    aria-label="${esc(`${label}. Task settings for ${e.display_name || e.slug}`)}" title="${esc(label)}" ${settingsCanManageBot(e) ? '' : 'disabled'}>${esc(words)}</button>`;
}
// The row a task line opens under its bot: Task model (model, effort and window, saved on Apply) and Fallback, each
// under its own small label, then the mark that folds the row away. The bot's own model never changes here.
function settingsTaskRow(e: SettingsBot, columns: number) {
  return `<tr class="sb-task-row" data-task-row="${esc(e.id)}"><td colspan="${columns}"><div class="sb-task-under">
    <div class="sb-task-part sb-task-model"><span class="sb-task-l">Task model</span>${settingsChoiceCombo(e, 'task')}</div>
    <div class="sb-task-part sb-cell-fallback"><span class="sb-task-l">Fallback</span>${settingsChoiceCell(e, 'fallback')}</div>
    <button type="button" class="ghost peek-btn sb-task-x" data-task-close="${esc(e.id)}" aria-label="Fold task settings" title="Fold"><span class="nav-icon" aria-hidden="true">close</span></button></div>
    ${settingsTaskWarning(e)}</td></tr>`;
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
