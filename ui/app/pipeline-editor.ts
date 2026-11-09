/* ui/app/pipeline-editor.ts — The pipeline editor: a pipeline's stages, their requirements and Warn after times, the
   links that hand tickets on, and its goal, factory, privacy, owner, manager and prefix. It opens from the pencil by the pipeline's name on the
   Tasks page, which shows only to a reader who may change the pipeline's rules (GET /api/v2/pipelines/{id}/rights).
   Every control calls a route that exists: stage names and order use the task-type route (movers only, so those
   controls show only to a mover), requirements and settings use backend/pipeline_requirements.py, links use
   backend/pipeline_links.py. A part whose route is not there is left out, not shown dead.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.

   openPipelineEditor(pipelineId)    opens the editor for a pipeline.
   pipelineEditSync(pipelineId)      shows or hides the pencil for the pipeline in view. */
'use strict';

interface PeStage { id: string; name: string; status: string; limit_minutes?: number | null; card_cap?: number | null; position?: number; }
interface PeType { id: string; name: string; prefix?: string | null; pipeline_owner_id?: string | null; manager?: string | null; goal?: string | null; steps: PeStage[]; }
interface PeNeeds { formats?: string[]; newer_than_change?: boolean; is_work?: boolean; field?: string; source?: string; }
interface PeReq {
  id: string; name: string; kind: string; needs: PeNeeds; reviewed_by: string; reviewer_id?: string | null;
  backup_reviewer_id?: string | null; rubric?: string | null; applies_to_label?: string | null; position?: number | null;
}
interface PeLink { id: string; from_pipeline_id: string; from_pipeline_stage_id: string; to_pipeline_id: string; mode: string; fires?: string; }
interface PeState {
  type: PeType; reqs: Map<string, PeReq[]>; links: PeLink[] | null; requirements: boolean;
  mover: boolean; warn: string; cap: string; link: string;
  /** Whether the reader may change the pipeline's rules (its owner and admins): the manager is a choice only then. */
  mayEdit: boolean;
  /** The settings block's server view (goal, factory, privacy, members), or null when the route did not answer. */
  settings: PipelineSettings | null;
  /** The live factories for the Factory choice, or null when the list did not load (the choice is then left out). */
  factories: PipelineFactory[] | null;
}

const PE_KINDS: [string, string][] = [['attachment', 'Attachment'], ['field', 'Field'], ['approval', 'Approval'], ['run', 'Run']];
const PE_REVIEWERS: [string, string][] = [['nobody', 'Nobody'], ['bot', 'Bot'], ['person', 'Person'], ['bot_then_person', 'Bot, then person']];
const PE_FORMATS: [string, string][] = [['image', 'Image'], ['video', 'Video'], ['page', 'Page'], ['file', 'File'], ['link', 'Link']];
const PE_FIELDS: [string, string][] = [['pr_linked', 'PR linked'], ['label', 'Label'], ['assignee', 'Assignee'], ['due', 'Due date']];
const PE_RUNS: [string, string][] = [['github_checks', 'GitHub checks'], ['test_receipt', 'Test receipt'], ['check_pool', 'Check pool']];
const PE_UNITS: [string, string, number][] = [['minutes', 'minutes', 1], ['hours', 'hours', 60], ['days', 'days', 1440]];

// The pipelines the Tasks page has read, kept when the editor opens (task-pipelines.js declares TASK_TYPES without a type).
let PE_TYPES: {id: string; name: string}[] = [];
const peTypes = (): {id: string; name: string}[] => PE_TYPES;

// Requirements, settings, stage limits and links are served by backend/pipeline_requirements.py and pipeline_links.py
// (tracks B and A); this branch has neither, so the route check reads them through here, as pipeline-api.ts does.
const peRoute = (path: string): string => path;

let PE: PeState | null = null;
let PE_SEQ = 0;
const PE_RIGHTS = new Map<string, {may: boolean; at: number}>();

const peOptions = (rows: [string, string][], on: string | null | undefined): string =>
  rows.map(([v, l]) => `<option value="${esc(v)}"${v === on ? ' selected' : ''}>${esc(l)}</option>`).join('');
const pePeople = (): {id: string; name: string}[] => (S.people || []).filter(p => !p.hidden).map(p => ({id: 'human:' + p.id, name: p.name || p.id}));
// A pipeline's manager is an active bot: paused, planned and archived bots are not offered. The roster carries the bot's
// state as `state` (`status` on the org roster, the same value).
const peBots = (): {id: string; name: string}[] => (S.emps || []).filter(e => (e.state ?? e.status) === 'active').map(e => ({id: 'bot:' + e.id, name: botDisplayName(e.id)}));
/** A choice of one person or bot. A clearable choice offers `none` as an empty value. One that cannot be cleared (an owner
    or a manager: the server refuses an empty one) shows `none` only while nothing is set, as a disabled placeholder. */
const peWho = (rows: {id: string; name: string}[], on: string | null | undefined, none: string, clearable = true): string => {
  const empty = clearable ? `<option value="">${esc(none)}</option>` : on ? '' : `<option value="" disabled selected>${esc(none)}</option>`;
  return `${empty}${rows.map(r => `<option value="${esc(r.id)}"${r.id === on ? ' selected' : ''}>${esc(r.name)}</option>`).join('')}`;
};

/** "2 days", "90 minutes": a Warn after time the way the person would say it. */
function peAfter(minutes: number | null | undefined): string {
  if (!minutes) return 'Off';
  for (const [, label, size] of [...PE_UNITS].reverse()) if (minutes % size === 0) return `${minutes / size} ${label}`;
  return `${minutes} minutes`;
}

function peFail(error: unknown): void {
  toast(error instanceof Error && error.message ? error.message : 'Could not save', true);
}

// ---- the pencil: shown to a reader who may change the pipeline's rules.
async function pipelineEditSync(pipelineId: string | null | undefined): Promise<void> {
  const button = document.getElementById('task-pipeline-edit');
  if (!button) return;
  const seq = ++PE_SEQ;
  if (!pipelineId) { button.hidden = true; return; }
  button.dataset['pipeline'] = pipelineId;
  const kept = PE_RIGHTS.get(pipelineId);
  if (kept !== undefined && Date.now() - kept.at < 60000) { button.hidden = !kept.may; return; }
  button.hidden = true;
  try {
    const rights: {may_edit_rules?: boolean} = await get(`/v2/pipelines/${encodeURIComponent(pipelineId)}/rights`);
    PE_RIGHTS.set(pipelineId, {may: !!rights.may_edit_rules, at: Date.now()});
    if (seq !== PE_SEQ) return;
    button.hidden = !rights.may_edit_rules;
  } catch { /* no rights known: the pencil stays hidden */ }
}

async function peLoad(id: string): Promise<PeState> {
  const type: PeType = (await get(`/v2/task-types/${encodeURIComponent(id)}`, {fresh: true})).type;
  const state: PeState = {type, reqs: new Map(), links: null, requirements: true, mover: canMove(), mayEdit: false, warn: '', cap: '', link: '', settings: null, factories: null};
  try { state.mayEdit = !!(await get(`/v2/pipelines/${encodeURIComponent(id)}/rights`, {fresh: true})).may_edit_rules; } catch { state.mayEdit = false; }
  try { state.settings = await pipelineSettings(id); } catch { state.settings = null; }
  try { state.factories = await pipelineFactories(); } catch { state.factories = null; }
  try { state.links = (await get(peRoute(`/v2/pipeline-links?pipeline=${encodeURIComponent(id)}`), {fresh: true})).links; } catch { state.links = null; }
  try {
    for (const stage of type.steps) state.reqs.set(stage.id, (await get(peRoute(`/v2/pipeline-stages/${encodeURIComponent(stage.id)}/requirements`), {fresh: true})).requirements ?? []);
  } catch { state.requirements = false; }
  return state;
}

// ---- drawing
function peNeedsText(r: PeReq): string {
  if (r.kind === 'attachment') return (r.needs.formats || []).join(', ');
  if (r.kind === 'field') return PE_FIELDS.find(([v]) => v === r.needs.field)?.[1] ?? '';
  if (r.kind === 'run') return PE_RUNS.find(([v]) => v === r.needs.source)?.[1] ?? '';
  return '';
}
const peWhoName = (actor: string | null | undefined): string => pePeople().find(p => p.id === actor)?.name ?? '';

function peReqHTML(r: PeReq): string {
  const by = r.reviewed_by === 'nobody' ? '' : `${PE_REVIEWERS.find(([v]) => v === r.reviewed_by)?.[1] ?? ''}${r.reviewer_id ? ' ' + peWhoName(r.reviewer_id) : ''}`;
  return `<li class="pe-req"><button type="button" class="pe-req-main" data-pe-req="${esc(r.id)}"><span class="pe-req-name">${esc(r.name)}</span>
    <span class="pe-req-meta">${esc([PE_KINDS.find(([v]) => v === r.kind)?.[1] ?? r.kind, peNeedsText(r), by].filter(Boolean).join(' · '))}</span></button></li>`;
}

function peWarnHTML(stage: PeStage, on: boolean): string {
  const unit = PE_UNITS.slice().reverse().find(([, , size]) => !!stage.limit_minutes && stage.limit_minutes % size === 0) ?? PE_UNITS[0]!;
  const shown = stage.limit_minutes ? stage.limit_minutes / unit[2] : 1;
  return `<span class="pe-warn"><button type="button" class="ghost pe-warn-b" data-pe-warn="${esc(stage.id)}" aria-expanded="${on}">Warn after: ${esc(peAfter(stage.limit_minutes))}</button>${on ? `
    <span class="pe-pop" role="group" aria-label="Warn after"><input type="number" min="1" step="1" value="${shown}" data-pe-warn-n aria-label="Warn after">
      <select data-pe-warn-u aria-label="Unit">${PE_UNITS.map(([v, l, size]) => `<option value="${size}"${unit[0] === v ? ' selected' : ''}>${l}</option>`).join('')}</select>
      <button type="button" class="primary" data-pe-warn-save="${esc(stage.id)}">Save</button>
      <button type="button" class="ghost" data-pe-warn-off="${esc(stage.id)}">Don't warn</button></span>` : ''}</span>`;
}

// "At most": how many cards the stage holds at once (D68). Blank is no cap; 0 is On ice (D69), a stage nobody works.
function peCapHTML(stage: PeStage, on: boolean): string {
  return `<span class="pe-cap"><button type="button" class="ghost pe-cap-b" data-pe-cap="${esc(stage.id)}" aria-expanded="${on}">At most: ${stage.card_cap == null ? 'No cap' : stage.card_cap}</button>${on ? `
    <span class="pe-pop" role="group" aria-label="At most"><input type="number" min="0" max="500" step="1" value="${stage.card_cap ?? ''}" data-pe-cap-n aria-label="At most">
      <button type="button" class="primary" data-pe-cap-save="${esc(stage.id)}">Save</button>
      <button type="button" class="ghost" data-pe-cap-off="${esc(stage.id)}">No cap</button></span>` : ''}</span>`;
}

function peLinkHTML(state: PeState, stage: PeStage): string {
  if (state.links === null) return '';
  const link = state.links.find(l => l.from_pipeline_stage_id === stage.id && l.from_pipeline_id === state.type.id);
  if (link) {
    const name = peTypes().find(t => t.id === link.to_pipeline_id)?.name ?? link.to_pipeline_id;
    const how = link.mode !== 'spawn' ? 'move' : link.fires === 'enter' ? 'new ticket when the card enters' : 'new ticket';
    return `<span class="pe-then">Then <b>${esc(name)}</b> (${how}) <button type="button" class="ghost" data-pe-unlink="${esc(link.id)}" aria-label="Remove link">Remove</button></span>`;
  }
  if (stage.status === 'done' || stage.status === 'closed') return '';
  if (state.link !== stage.id) return `<button type="button" class="ghost pe-then-add" data-pe-link="${esc(stage.id)}">Then…</button>`;
  const others = peTypes().filter(t => t.id !== state.type.id && t.id !== 'general');
  return `<span class="pe-pop" role="group" aria-label="Then"><select data-pe-link-to aria-label="Then go to">${others.map(t => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('')}</select>
    <select data-pe-link-mode aria-label="How"><option value="move">Move</option><option value="spawn">New ticket</option></select>
    <select data-pe-link-fires aria-label="When" hidden><option value="leave">when the card leaves</option><option value="enter">when the card enters</option></select>
    <button type="button" class="primary" data-pe-link-save="${esc(stage.id)}">Add</button><button type="button" class="ghost" data-pe-link-cancel>Cancel</button></span>`;
}

function peStageHTML(state: PeState, stage: PeStage, i: number, count: number): string {
  const name = state.mover
    ? `<input class="pe-stage-name" value="${esc(stage.name)}" maxlength="80" data-pe-rename="${esc(stage.id)}" aria-label="Stage name">
       <span class="pe-order"><button type="button" class="ghost" data-pe-up="${esc(stage.id)}" aria-label="Move up"${i === 0 ? ' disabled' : ''}>↑</button><button type="button" class="ghost" data-pe-down="${esc(stage.id)}" aria-label="Move down"${i === count - 1 ? ' disabled' : ''}>↓</button><button type="button" class="ghost" data-pe-drop="${esc(stage.id)}" aria-label="Remove stage">Remove</button></span>`
    : `<span class="pe-stage-name">${esc(stage.name)}</span>`;
  const reqs = state.reqs.get(stage.id) ?? [];
  return `<li class="pe-stage"><div class="pe-stage-head">${name}</div>
    <div class="pe-stage-tools">${peWarnHTML(stage, state.warn === stage.id)}${peCapHTML(stage, state.cap === stage.id)}${peLinkHTML(state, stage)}</div>
    ${state.requirements ? `<ul class="pe-reqs">${reqs.map(peReqHTML).join('')}</ul><button type="button" class="ghost pe-req-add" data-pe-new="${esc(stage.id)}">+ Require</button>` : ''}
    <div class="pe-form-slot" data-pe-slot="${esc(stage.id)}"></div></li>`;
}

/** The Goal, Factory and Private rows of the settings block. They show only when the server sent the settings. */
function peGoalHTML(state: PeState): string {
  const s = state.settings;
  if (!s) return '';
  const isPrivate = s.visibility === 'members';
  const factories = (state.factories ?? []).map(f => ({id: f.id, name: f.name}));
  // A member the roster does not show (a hidden person, an archived bot) still gets a checked box, so saving keeps it.
  const roster = [...pePeople(), ...peBots()];
  const unlisted = s.members.filter(id => !roster.some(p => p.id === id)).map(id => ({id, name: id.replace(/^(human|bot):/, '')}));
  const members = isPrivate
    ? `<fieldset class="pe-members"><legend>Who can see it</legend>${[...roster, ...unlisted].map(p =>
        `<label class="pe-check"><input type="checkbox" data-pe-member value="${esc(p.id)}"${s.members.includes(p.id) ? ' checked' : ''}> ${esc(p.name)}</label>`).join('')}</fieldset>`
    : '';
  return `<label class="pe-f"><span>Goal</span><input name="goal" maxlength="200" value="${esc(s.goal ?? '')}" data-pe-setting="goal" autocomplete="off"></label>
      ${state.factories ? `<label class="pe-f"><span>Factory</span><select data-pe-setting="factory_id"${isPrivate ? ' disabled' : ''}>${peWho(factories, s.factory_id, 'None')}</select></label>` : ''}
      ${peStuckHTML(s)}
      <label class="pe-check"><input type="checkbox" role="switch" data-pe-private${isPrivate ? ' checked' : ''}> Private</label>
      ${members}`;
}

/** The manager as a reader who may not change it sees it: the saved bot's name, with its state when it is not active. The
    roster leaves archived bots out, so a saved bot it does not list is said to be no longer on the team. */
function peManagerName(saved: string): string {
  if (!saved) return 'Not set';
  const bot = (S.emps || []).find(e => e.id === saved);
  if (!bot) return 'A bot no longer on the team';
  const botState = bot.state ?? bot.status;
  return botState === 'active' ? botDisplayName(saved) : `${botDisplayName(saved)} (${botState})`;
}

/** The Pipeline Manager field. An owner or admin picks an active bot, or Not set; anyone else reads the name. A saved
    manager that is not an active bot (archived, paused, planned or gone) shows as Not set, with a line saying so. */
function peManagerHTML(state: PeState): string {
  const bots = peBots();
  const saved = (state.type.manager ?? '').replace(/^bot:/, '');
  const current = bots.some(b => b.id === 'bot:' + saved) ? saved : '';
  const stale = !!saved && !current;
  if (!state.mayEdit) return `<span class="pe-readonly" data-pe-manager-readonly>${esc(peManagerName(saved))}</span>`;
  const hint = `<span class="pe-hint">The bot that checks this pipeline's stuck tasks. Only active bots can be picked.</span>`;
  const warn = stale ? `<span class="pe-hint pe-warn-line" data-pe-manager-stale>The saved manager is not an active bot. Pick one.</span>` : '';
  if (!bots.length) return `<select data-pe-setting="manager_id" disabled><option value="">No active bots</option></select>${hint}${warn}`;
  return `<select data-pe-setting="manager_id">${peWho(bots, current ? 'bot:' + current : '', 'Not set')}</select>${hint}${warn}`;
}

/** The stuck rule's three numbers: empty is the default, shown as the placeholder. */
const PE_STUCK: [keyof PipelineSettings['stuck_rule_defaults'], string][] = [
  ['stuck_after_hours', 'Stuck after (h)'],
  ['ask_person_after_hours', 'Ask a person after (h)'],
  ['question_expiry_days', 'Questions expire (days)'],
];

function peStuckHTML(s: PipelineSettings): string {
  if (!s.stuck_rule_defaults) return '';
  return PE_STUCK.map(([key, label]) => `<label class="pe-f"><span>${label}</span><input type="number" min="1" step="1" inputmode="numeric" value="${esc(String(s[key] ?? ''))}" placeholder="${esc(String(s.stuck_rule_defaults[key]))}" data-pe-number="${key}" autocomplete="off"></label>`).join('');
}

function peBodyHTML(state: PeState): string {
  const t = state.type;
  const from = (state.links ?? []).filter(l => l.to_pipeline_id === t.id).map(l => {
    const src = peTypes().find(x => x.id === l.from_pipeline_id);
    return `<span class="pe-from">${esc(src?.name ?? l.from_pipeline_id)}</span>`;
  }).join('');
  return `<div class="pe-settings">
      <label class="pe-f"><span>Prefix</span><input name="prefix" maxlength="2" value="${esc(t.prefix ?? '')}" data-pe-setting="prefix" autocomplete="off"></label>
      <label class="pe-f"><span>Owner</span><select data-pe-setting="owner_id">${peWho(pePeople(), t.pipeline_owner_id, 'Not set', false)}</select></label>
      <label class="pe-f"><span>Pipeline Manager</span>${peManagerHTML(state)}</label>
      ${peGoalHTML(state)}
      ${from ? `<p class="pe-froms"><span>From</span> ${from}</p>` : ''}</div>
    <ol class="pe-stages">${t.steps.map((s, i) => peStageHTML(state, s, i, t.steps.length)).join('')}</ol>
    ${state.mover ? '<form class="pe-add-stage" data-pe-add-stage><input name="name" maxlength="80" placeholder="New stage" aria-label="New stage" autocomplete="off"><button type="submit" class="ghost">Add stage</button></form>' : ''}`;
}

function peRender(): void {
  const body = document.getElementById('pe-body');
  if (!body || !PE) return;
  const scroll = body.scrollTop;
  // A setting the reader is typing in keeps its unsaved text, caret and focus across a redraw (another save, a live
  // update). The redraw replaces every field, so without this a half-typed Goal lost its focus and its words.
  const typing = document.activeElement;
  const kept = typing instanceof HTMLInputElement && body.contains(typing) && typing.dataset['peSetting']
    ? {key: typing.dataset['peSetting'], text: typing.value, start: typing.selectionStart, end: typing.selectionEnd} : null;
  body.innerHTML = peBodyHTML(PE);
  body.scrollTop = scroll;
  if (!kept) return;
  const field = body.querySelector<HTMLInputElement>(`[data-pe-setting="${CSS.escape(kept.key)}"]`);
  if (!field) return;
  field.focus();
  field.value = kept.text;
  try { field.setSelectionRange(kept.start, kept.end); } catch { /* a field with no caret keeps its place */ }
}

async function peReload(): Promise<void> {
  if (!PE) return;
  PE = await peLoad(PE.type.id);
  peRender();
  void pipelineEditSync(PE.type.id);
}

// ---- a requirement's form
function peReqFormHTML(stageId: string, r: PeReq | null): string {
  const kind = r?.kind ?? 'attachment', by = r?.reviewed_by ?? 'nobody', needs = r?.needs ?? {};
  const people = pePeople();
  return `<form class="pe-form" data-pe-form="${esc(stageId)}" data-pe-id="${esc(r?.id ?? '')}" novalidate>
    <label class="pe-f"><span>Name</span><input name="name" maxlength="80" value="${esc(r?.name ?? '')}" required autocomplete="off"></label>
    <label class="pe-f"><span>Kind</span><select name="kind">${peOptions(PE_KINDS, kind)}</select></label>
    <div class="pe-needs" data-pe-needs="attachment"${kind === 'attachment' ? '' : ' hidden'}>
      ${PE_FORMATS.map(([v, l]) => `<label class="pe-check"><input type="checkbox" name="format" value="${v}"${(needs.formats ?? []).includes(v) ? ' checked' : ''}> ${l}</label>`).join('')}
      <label class="pe-check"><input type="checkbox" name="newer"${needs.newer_than_change === false ? '' : ' checked'}> Newer than the last change</label>
      <label class="pe-check"><input type="checkbox" name="is_work"${needs.is_work ? ' checked' : ''}> Is the work itself</label></div>
    <label class="pe-f" data-pe-needs="field"${kind === 'field' ? '' : ' hidden'}><span>Field</span><select name="field">${peOptions(PE_FIELDS, needs.field)}</select></label>
    <label class="pe-f" data-pe-needs="run"${kind === 'run' ? '' : ' hidden'}><span>Source</span><select name="source">${peOptions(PE_RUNS, needs.source)}</select></label>
    <label class="pe-f"><span>Reviewed by</span><select name="reviewed_by">${peOptions(PE_REVIEWERS, by)}</select></label>
    <label class="pe-f" data-pe-by="person"><span>Reviewer</span><select name="reviewer_id">${peWho(people, r?.reviewer_id, 'Choose a person')}</select></label>
    <label class="pe-f" data-pe-by="backup"><span>Backup reviewer</span><select name="backup_reviewer_id">${peWho(people, r?.backup_reviewer_id, 'None')}</select></label>
    <label class="pe-f" data-pe-by="rubric"><span>Rubric</span><textarea name="rubric" rows="3" maxlength="2000">${esc(r?.rubric ?? '')}</textarea></label>
    <label class="pe-f"><span>Only for label</span><input name="applies_to_label" maxlength="80" value="${esc(r?.applies_to_label ?? '')}" autocomplete="off"></label>
    <p class="pe-affected muted" data-pe-affected hidden></p>
    <p class="err" role="alert" data-pe-error hidden></p>
    <div class="pe-form-foot"><button type="submit" class="primary">Save</button><button type="button" class="ghost" data-pe-cancel>Cancel</button>
      ${r ? `<button type="button" class="ghost pe-retire" data-pe-retire="${esc(r.id)}">Retire</button>` : ''}</div></form>`;
}

// Show the parts of the form its kind and reviewer need.
function peFormSync(form: HTMLFormElement): void {
  const kind = String(new FormData(form).get('kind') ?? '');
  const select = form.elements.namedItem('reviewed_by') as HTMLSelectElement;
  if (kind === 'approval') select.value = 'person';
  for (const option of Array.from(select.options)) option.disabled = kind === 'approval' && option.value !== 'person';
  const by = select.value;
  for (const el of form.querySelectorAll<HTMLElement>('[data-pe-needs]')) el.hidden = el.dataset['peNeeds'] !== kind;
  const show = (key: string, on: boolean): void => { const el = form.querySelector<HTMLElement>(`[data-pe-by="${key}"]`); if (el) el.hidden = !on; };
  show('person', by === 'person' || by === 'bot_then_person');
  show('backup', by !== 'nobody');
  show('rubric', by === 'bot' || by === 'bot_then_person');
}

/** The requirement's body as its review mode allows: a reviewer only for a person's review, a backup and a rubric only when
    someone reviews. A field the mode does not use is cleared, not sent (the server refuses a Nobody with a reviewer). */
function peReqBody(form: HTMLFormElement): Record<string, unknown> {
  const data = new FormData(form), text = (k: string): string => String(data.get(k) ?? '').trim();
  const kind = text('kind'), by = text('reviewed_by');
  const needs: Record<string, unknown> = kind === 'attachment'
    ? {formats: data.getAll('format').map(String), newer_than_change: data.has('newer'), is_work: data.has('is_work')}
    : kind === 'field' ? {field: text('field')} : kind === 'run' ? {source: text('source')} : {};
  const person = by === 'person' || by === 'bot_then_person', someone = by !== 'nobody';
  return {
    name: text('name'), kind, needs, reviewed_by: by,
    reviewer_id: person ? text('reviewer_id') || null : null,
    backup_reviewer_id: someone ? text('backup_reviewer_id') || null : null,
    rubric: someone ? text('rubric') || null : null, applies_to_label: text('applies_to_label') || null,
  };
}

/** The line under the form: how many open cards at the stage the requirement would apply to right now. Read again when the
    label changes, and only written when the label it was read for is still the one typed. */
async function peAffectedSync(form: HTMLFormElement): Promise<void> {
  const note = form.querySelector<HTMLElement>('[data-pe-affected]');
  const stageId = form.dataset['peForm'] ?? '';
  if (!note || !stageId) return;
  const label = String(new FormData(form).get('applies_to_label') ?? '').trim();
  try {
    const {count, stage} = await pipelineAffectedCount(stageId, label);
    if (!form.isConnected || String(new FormData(form).get('applies_to_label') ?? '').trim() !== label) return;
    note.textContent = count === 0 ? `Applies to no cards in ${stage} now.` : `Applies to the ${count} card${count === 1 ? '' : 's'} in ${stage} now.`;
    note.hidden = false;
  } catch { note.hidden = true; }
}

async function peSaveReq(form: HTMLFormElement): Promise<void> {
  const stageId = form.dataset['peForm'] ?? '', id = form.dataset['peId'] ?? '';
  const error = form.querySelector<HTMLElement>('[data-pe-error]');
  const body = peReqBody(form);
  if (!body['name']) { if (error) { error.textContent = 'Name it'; error.hidden = false; } return; }
  try {
    if (id) await writeRequest('PUT', peRoute(`/v2/requirements/${encodeURIComponent(id)}`), body);
    else await writeRequest('POST', peRoute(`/v2/pipeline-stages/${encodeURIComponent(stageId)}/requirements`), body);
    await peReload();
  } catch (e) {
    if (error) { error.textContent = e instanceof Error && e.message ? e.message : 'Could not save'; error.hidden = false; }
  }
}

function peOpenForm(stageId: string, r: PeReq | null): void {
  const slot = document.querySelector<HTMLElement>(`[data-pe-slot="${CSS.escape(stageId)}"]`);
  if (!slot) return;
  for (const other of document.querySelectorAll<HTMLElement>('[data-pe-slot]')) other.innerHTML = '';
  slot.innerHTML = peReqFormHTML(stageId, r);
  const form = slot.querySelector('form');
  if (form) { peFormSync(form); void peAffectedSync(form); form.querySelector<HTMLInputElement>('[name="name"]')?.focus(); }
}

// ---- stage changes go through the task-type route, which takes the whole list of stages
async function peSaveStages(steps: {id?: string; name: string; status: string}[]): Promise<void> {
  if (!PE) return;
  try {
    await writeRequest('POST', `/v2/task-types/${encodeURIComponent(PE.type.id)}`, {steps});
    taskTypesLoad().catch(() => undefined);
  } catch (e) { peFail(e); }
  await peReload();
}
const peSteps = (): {id?: string; name: string; status: string}[] => (PE?.type.steps ?? []).map(s => ({id: s.id, name: s.name, status: s.status}));

/** Save some of the pipeline's settings (only the fields given), then read the editor again. */
async function peSaveSettings(fields: Record<string, unknown>): Promise<void> {
  if (!PE) return;
  try { await pipelinePatchSettings(PE.type.id, fields); } catch (e) { peFail(e); }
  await peReload();
}

async function peSetting(key: string, value: string): Promise<void> {
  await peSaveSettings({[key]: value});
}

async function peWarnSave(target: Element, stage: string, off: boolean): Promise<void> {
  const root = target.closest('.pe-warn');
  const n = Number(root?.querySelector<HTMLInputElement>('[data-pe-warn-n]')?.value);
  const size = Number(root?.querySelector<HTMLSelectElement>('[data-pe-warn-u]')?.value) || 1;
  const minutes = off ? null : Math.round(n * size);
  if (!off && !(minutes && minutes > 0)) { toast('Enter a time', true); return; }
  try { await writeRequest('PATCH', peRoute(`/v2/pipeline-stages/${encodeURIComponent(stage)}`), {limit_minutes: minutes}); if (PE) PE.warn = ''; } catch (e) { peFail(e); }
  await peReload();
}

async function peCapSave(target: Element, stage: string, off: boolean): Promise<void> {
  const raw = target.closest('.pe-cap')?.querySelector<HTMLInputElement>('[data-pe-cap-n]')?.value.trim() ?? '';
  const cap = off ? null : Number(raw);
  if (!off && !(raw !== '' && Number.isInteger(cap) && (cap as number) >= 0)) { toast('Enter a whole number, 0 or more', true); return; }
  try { await writeRequest('PATCH', peRoute(`/v2/pipeline-stages/${encodeURIComponent(stage)}`), {card_cap: cap}); if (PE) PE.cap = ''; } catch (e) { peFail(e); }
  await peReload();
}

async function peClick(target: Element): Promise<void> {
  if (!PE) return;
  const hit = (attr: string): string | null => target.closest<HTMLElement>(`[${attr}]`)?.getAttribute(attr) ?? null;
  const warn = hit('data-pe-warn');
  if (warn) { PE.warn = PE.warn === warn ? '' : warn; peRender(); return; }
  const save = hit('data-pe-warn-save'), off = hit('data-pe-warn-off');
  if (save || off) { await peWarnSave(target, save ?? off ?? '', !save); return; }
  const cap = hit('data-pe-cap');
  if (cap) { PE.cap = PE.cap === cap ? '' : cap; peRender(); return; }
  const capSave = hit('data-pe-cap-save'), capOff = hit('data-pe-cap-off');
  if (capSave || capOff) { await peCapSave(target, capSave ?? capOff ?? '', !capSave); return; }
  const req = hit('data-pe-req');
  if (req) {
    const stage = [...PE.reqs.entries()].find(([, rs]) => rs.some(r => r.id === req))?.[0];
    const found = [...PE.reqs.values()].flat().find(r => r.id === req);
    if (found && stage) peOpenForm(stage, found);
    return;
  }
  const fresh = hit('data-pe-new');
  if (fresh) { peOpenForm(fresh, null); return; }
  if (target.closest('[data-pe-cancel]')) { for (const slot of document.querySelectorAll<HTMLElement>('[data-pe-slot]')) slot.innerHTML = ''; return; }
  const retire = hit('data-pe-retire');
  if (retire) { try { await writeRequest('DELETE', peRoute(`/v2/requirements/${encodeURIComponent(retire)}`), {}); } catch (e) { peFail(e); } await peReload(); return; }
  const link = hit('data-pe-link');
  if (link) { PE.link = link; peRender(); return; }
  if (target.closest('[data-pe-link-cancel]')) { PE.link = ''; peRender(); return; }
  const linkSave = hit('data-pe-link-save');
  if (linkSave) {
    const root = target.closest('.pe-pop');
    const to = root?.querySelector<HTMLSelectElement>('[data-pe-link-to]')?.value, mode = root?.querySelector<HTMLSelectElement>('[data-pe-link-mode]')?.value ?? 'move';
    const fires = root?.querySelector<HTMLSelectElement>('[data-pe-link-fires]')?.value ?? 'leave';
    if (!to) return;
    const body: Record<string, string> = {from_stage_id: linkSave, to_pipeline_id: to, mode};
    if (mode === 'spawn') body['fires'] = fires;
    try { await writeRequest('POST', peRoute('/v2/pipeline-links'), body); PE.link = ''; } catch (e) { peFail(e); }
    await peReload();
    return;
  }
  const unlink = hit('data-pe-unlink');
  if (unlink) { try { await writeRequest('POST', peRoute(`/v2/pipeline-links/${encodeURIComponent(unlink)}/retire`), {}); } catch (e) { peFail(e); } await peReload(); return; }
  const steps = peSteps();
  const move = (stage: string, by: number): void => {
    const i = steps.findIndex(s => s.id === stage), a = steps[i], b = steps[i + by];
    if (a && b) { steps[i] = b; steps[i + by] = a; void peSaveStages(steps); }
  };
  const up = hit('data-pe-up'), down = hit('data-pe-down'), drop = hit('data-pe-drop');
  if (up) move(up, -1);
  else if (down) move(down, 1);
  else if (drop) {
    if (steps.length < 2) { toast('A pipeline keeps at least one stage', true); return; }
    void peSaveStages(steps.filter(s => s.id !== drop));
  }
}

function peWire(dialog: HTMLDialogElement): void {
  dialog.addEventListener('click', ev => {
    const t = eventElement(ev);
    if (!t) return;
    if (t.closest('[data-pe-close]')) { dialog.close(); return; }
    if (t.closest('button')) void peClick(t);
  });
  // A setting or the Goal saves on Enter as well as on change (the Goal sits in no form, so nothing else would send it).
  dialog.addEventListener('keydown', ev => {
    const t = ev.target;
    if (ev.key !== 'Enter' || !(t instanceof HTMLInputElement) || !t.dataset['peSetting']) return;
    ev.preventDefault();
    void peSetting(t.dataset['peSetting'], t.value);
  });
  dialog.addEventListener('change', ev => {
    const t = ev.target;
    if (t instanceof HTMLSelectElement && t.hasAttribute('data-pe-link-mode')) {
      const when = t.closest('.pe-pop')?.querySelector<HTMLSelectElement>('[data-pe-link-fires]');
      if (when) when.hidden = t.value !== 'spawn';
      return;
    }
    if (t instanceof HTMLElement && t.closest('[data-pe-form]')) { const form = t.closest('form'); if (form) { peFormSync(form); void peAffectedSync(form); } return; }
    if (t instanceof HTMLInputElement || t instanceof HTMLSelectElement) {
      const key = t.dataset['peSetting'], renamed = t.dataset['peRename'];
      if (t instanceof HTMLInputElement && t.hasAttribute('data-pe-private')) { void peSaveSettings({visibility: t.checked ? 'members' : 'company'}); return; }
      if (t instanceof HTMLInputElement && t.hasAttribute('data-pe-member')) {
        void peSaveSettings({members: [...dialog.querySelectorAll<HTMLInputElement>('[data-pe-member]:checked')].map(box => box.value)});
        return;
      }
      const number = t.dataset['peNumber'];
      if (number) { const raw = t.value.trim(); void peSaveSettings({[number]: raw === '' ? null : Number(raw)}); return; }
      if (key) void peSetting(key, t.value);
      else if (renamed && PE) {
        const name = t.value.trim();
        if (!name) { void peReload(); return; }
        void peSaveStages(peSteps().map(s => s.id === renamed ? {...s, name} : s));
      }
    }
  });
  dialog.addEventListener('submit', ev => {
    ev.preventDefault();
    const form = ev.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.hasAttribute('data-pe-form')) { void peSaveReq(form); return; }
    if (form.hasAttribute('data-pe-add-stage')) {
      const name = String(new FormData(form).get('name') ?? '').trim();
      if (!name || !PE) return;
      const steps = peSteps();
      // A new stage goes before the closing ones, as a working stage.
      const at = steps.findIndex(s => s.status === 'done' || s.status === 'closed');
      steps.splice(at < 0 ? steps.length : at, 0, {name, status: 'doing'});
      void peSaveStages(steps);
    }
  });
}

async function openPipelineEditor(pipelineId: string | null): Promise<void> {
  if (!pipelineId) return;
  document.getElementById('pe-dialog')?.remove();
  const dialog = document.createElement('dialog');
  dialog.id = 'pe-dialog'; dialog.className = 'tmodal tmodal-shell pe-dialog';
  dialog.setAttribute('aria-labelledby', 'pe-title');
  dialog.innerHTML = `<div class="tmodal-head"><h2 id="pe-title">Edit pipeline</h2><button class="ghost tmodal-x" type="button" data-pe-close aria-label="Close">✕</button></div>
    <div class="tmodal-body pe-body" id="pe-body">${skelRows(3, {dot: false})}</div>`;
  document.body.append(dialog);
  const leave = (): void => dialog.close();
  window.addEventListener('hashchange', leave);
  dialog.addEventListener('close', () => { window.removeEventListener('hashchange', leave); dialog.remove(); PE = null; });
  peWire(dialog);
  dialog.showModal();
  try {
    PE_TYPES = (await taskTypesLoad({cached: true})) ?? [];
    PE = await peLoad(pipelineId);
    const title = document.getElementById('pe-title');
    if (title) title.textContent = PE.type.name;
    peRender();
  } catch (e) { peFail(e); dialog.close(); }
}

document.addEventListener('click', ev => {
  if (eventElement(ev)?.closest('#task-pipeline-edit')) void openPipelineEditor(document.getElementById('task-pipeline-edit')?.dataset['pipeline'] ?? null);
});
