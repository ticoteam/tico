/* ui/app/pipeline-editor.ts — The pipeline editor: a pipeline's stages, their requirements and Warn after times, the
   links that hand tickets on, and its owner, manager and prefix. It opens from the pencil by the pipeline's name on the
   Tasks page, which shows only to a reader who may change the pipeline's rules (GET /api/v2/pipelines/{id}/rights).
   Every control calls a route that exists: stage names and order use the task-type route (movers only, so those
   controls show only to a mover), requirements and settings use backend/pipeline_requirements.py, links use
   backend/pipeline_links.py. A part whose route is not there is left out, not shown dead.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.

   openPipelineEditor(pipelineId)    opens the editor for a pipeline.
   pipelineEditSync(pipelineId)      shows or hides the pencil for the pipeline in view. */
'use strict';

interface PeStage { id: string; name: string; status: string; limit_minutes?: number | null; position?: number; }
interface PeType { id: string; name: string; prefix?: string | null; pipeline_owner_id?: string | null; manager?: string | null; goal?: string | null; steps: PeStage[]; }
interface PeNeeds { formats?: string[]; newer_than_change?: boolean; is_work?: boolean; field?: string; source?: string; }
interface PeReq {
  id: string; name: string; kind: string; needs: PeNeeds; reviewed_by: string; reviewer_id?: string | null;
  backup_reviewer_id?: string | null; rubric?: string | null; applies_to_label?: string | null; position?: number | null;
}
interface PeLink { id: string; from_pipeline_id: string; from_pipeline_stage_id: string; to_pipeline_id: string; mode: string; }
interface PeState {
  type: PeType; reqs: Map<string, PeReq[]>; links: PeLink[] | null; requirements: boolean;
  mover: boolean; warn: string; link: string;
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
const peBots = (): {id: string; name: string}[] => (S.emps || []).filter(e => e.state !== 'archived').map(e => ({id: 'bot:' + e.id, name: botDisplayName(e.id)}));
const peWho = (rows: {id: string; name: string}[], on: string | null | undefined, none: string): string =>
  `<option value="">${esc(none)}</option>${rows.map(r => `<option value="${esc(r.id)}"${r.id === on ? ' selected' : ''}>${esc(r.name)}</option>`).join('')}`;

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
  const state: PeState = {type, reqs: new Map(), links: null, requirements: true, mover: canMove(), warn: '', link: ''};
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

function peLinkHTML(state: PeState, stage: PeStage): string {
  if (state.links === null) return '';
  const link = state.links.find(l => l.from_pipeline_stage_id === stage.id && l.from_pipeline_id === state.type.id);
  if (link) {
    const name = peTypes().find(t => t.id === link.to_pipeline_id)?.name ?? link.to_pipeline_id;
    return `<span class="pe-then">Then <b>${esc(name)}</b> (${link.mode === 'spawn' ? 'new ticket' : 'move'}) <button type="button" class="ghost" data-pe-unlink="${esc(link.id)}" aria-label="Remove link">Remove</button></span>`;
  }
  if (stage.status === 'done' || stage.status === 'closed') return '';
  if (state.link !== stage.id) return `<button type="button" class="ghost pe-then-add" data-pe-link="${esc(stage.id)}">Then…</button>`;
  const others = peTypes().filter(t => t.id !== state.type.id && t.id !== 'general');
  return `<span class="pe-pop" role="group" aria-label="Then"><select data-pe-link-to aria-label="Then go to">${others.map(t => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('')}</select>
    <select data-pe-link-mode aria-label="How"><option value="move">Move</option><option value="spawn">New ticket</option></select>
    <button type="button" class="primary" data-pe-link-save="${esc(stage.id)}">Add</button><button type="button" class="ghost" data-pe-link-cancel>Cancel</button></span>`;
}

function peStageHTML(state: PeState, stage: PeStage, i: number, count: number): string {
  const name = state.mover
    ? `<input class="pe-stage-name" value="${esc(stage.name)}" maxlength="80" data-pe-rename="${esc(stage.id)}" aria-label="Stage name">
       <span class="pe-order"><button type="button" class="ghost" data-pe-up="${esc(stage.id)}" aria-label="Move up"${i === 0 ? ' disabled' : ''}>↑</button><button type="button" class="ghost" data-pe-down="${esc(stage.id)}" aria-label="Move down"${i === count - 1 ? ' disabled' : ''}>↓</button><button type="button" class="ghost" data-pe-drop="${esc(stage.id)}" aria-label="Remove stage">Remove</button></span>`
    : `<span class="pe-stage-name">${esc(stage.name)}</span>`;
  const reqs = state.reqs.get(stage.id) ?? [];
  return `<li class="pe-stage"><div class="pe-stage-head">${name}</div>
    <div class="pe-stage-tools">${peWarnHTML(stage, state.warn === stage.id)}${peLinkHTML(state, stage)}</div>
    ${state.requirements ? `<ul class="pe-reqs">${reqs.map(peReqHTML).join('')}</ul><button type="button" class="ghost pe-req-add" data-pe-new="${esc(stage.id)}">+ Require</button>` : ''}
    <div class="pe-form-slot" data-pe-slot="${esc(stage.id)}"></div></li>`;
}

function peBodyHTML(state: PeState): string {
  const t = state.type;
  const from = (state.links ?? []).filter(l => l.to_pipeline_id === t.id).map(l => {
    const src = peTypes().find(x => x.id === l.from_pipeline_id);
    return `<span class="pe-from">${esc(src?.name ?? l.from_pipeline_id)}</span>`;
  }).join('');
  return `<div class="pe-settings">
      <label class="pe-f"><span>Prefix</span><input name="prefix" maxlength="2" value="${esc(t.prefix ?? '')}" data-pe-setting="prefix" autocomplete="off"></label>
      <label class="pe-f"><span>Owner</span><select data-pe-setting="owner_id">${peWho(pePeople(), t.pipeline_owner_id, 'No owner')}</select></label>
      <label class="pe-f"><span>Pipeline Manager</span><select data-pe-setting="manager_id">${peWho(peBots(), t.manager, 'No manager')}</select></label>
      ${t.goal ? `<p class="pe-goal"><span>Goal</span> ${esc(t.goal)}</p>` : ''}
      ${from ? `<p class="pe-froms"><span>From</span> ${from}</p>` : ''}</div>
    <ol class="pe-stages">${t.steps.map((s, i) => peStageHTML(state, s, i, t.steps.length)).join('')}</ol>
    ${state.mover ? '<form class="pe-add-stage" data-pe-add-stage><input name="name" maxlength="80" placeholder="New stage" aria-label="New stage" autocomplete="off"><button type="submit" class="ghost">Add stage</button></form>' : ''}`;
}

function peRender(): void {
  const body = document.getElementById('pe-body');
  if (!body || !PE) return;
  const scroll = body.scrollTop;
  body.innerHTML = peBodyHTML(PE);
  body.scrollTop = scroll;
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

function peReqBody(form: HTMLFormElement): Record<string, unknown> {
  const data = new FormData(form), text = (k: string): string => String(data.get(k) ?? '').trim();
  const kind = text('kind');
  const needs: Record<string, unknown> = kind === 'attachment'
    ? {formats: data.getAll('format').map(String), newer_than_change: data.has('newer'), is_work: data.has('is_work')}
    : kind === 'field' ? {field: text('field')} : kind === 'run' ? {source: text('source')} : {};
  return {
    name: text('name'), kind, needs, reviewed_by: text('reviewed_by'),
    reviewer_id: text('reviewer_id') || null, backup_reviewer_id: text('backup_reviewer_id') || null,
    rubric: text('rubric') || null, applies_to_label: text('applies_to_label') || null,
  };
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
  if (form) { peFormSync(form); form.querySelector<HTMLInputElement>('[name="name"]')?.focus(); }
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

async function peSetting(key: string, value: string): Promise<void> {
  if (!PE) return;
  try { await writeRequest('PATCH', peRoute(`/v2/pipelines/${encodeURIComponent(PE.type.id)}/settings`), {[key]: value}); } catch (e) { peFail(e); }
  await peReload();
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

async function peClick(target: Element): Promise<void> {
  if (!PE) return;
  const hit = (attr: string): string | null => target.closest<HTMLElement>(`[${attr}]`)?.getAttribute(attr) ?? null;
  const warn = hit('data-pe-warn');
  if (warn) { PE.warn = PE.warn === warn ? '' : warn; peRender(); return; }
  const save = hit('data-pe-warn-save'), off = hit('data-pe-warn-off');
  if (save || off) { await peWarnSave(target, save ?? off ?? '', !save); return; }
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
    if (!to) return;
    try { await writeRequest('POST', peRoute('/v2/pipeline-links'), {from_stage_id: linkSave, to_pipeline_id: to, mode}); PE.link = ''; } catch (e) { peFail(e); }
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
  dialog.addEventListener('change', ev => {
    const t = ev.target;
    if (t instanceof HTMLElement && t.closest('[data-pe-form]')) { const form = t.closest('form'); if (form) peFormSync(form); return; }
    if (t instanceof HTMLInputElement || t instanceof HTMLSelectElement) {
      const key = t.dataset['peSetting'], renamed = t.dataset['peRename'];
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
