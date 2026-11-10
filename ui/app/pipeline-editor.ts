/* ui/app/pipeline-editor.ts — The pipeline editor (docs/pipelines-v3.md S11): a pipeline's Goal, Manager, Factory and
   Prefix (no Private: pipeline_settings refuses a private pipeline until one is defined), then its stages, one panel
   each, closed until clicked. A stage has Purpose, Owner, how tasks leave, a Limit, an Expected time, and the
   requirements to leave it. Done and Closed are not listed: every pipeline ends in them.
   It opens from a stage's header (that stage open) or the page's edit button, for whoever may change the pipeline.
   The whole record is read (GET /api/v2/pipelines/{id}) and saved at once (POST, with the settings_version it was read
   at). When someone else saved first, the editor says what they changed and lets the person load theirs or save theirs
   over it. Any other refusal shows the server's words. What the editor does not show (a stage's On enter, Expected for
   bots only, a requirement's settings) is sent back as it was read.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

interface PeState {
  id: string;
  /** The record as last read: what a save is checked against. */
  base: PipelineRec;
  /** The record being edited. */
  draft: PipelineRec;
  /** The stages whose panel is open. */
  open: WeakSet<PipelineStageRec>;
  factories: FactoryRow[] | null;
  /** Someone saved first: what they changed, and their record. */
  clash: {theirs: PipelineRec; changed: string[]} | null;
  error: string;
  saving: boolean;
}

let PE: PeState | null = null;

/** The kinds a stage's requirement can be. A kind built in a later phase shows, but cannot be picked yet. An answer is
    asked on one task, never set on a stage. */
const PE_KINDS: [string, string, boolean][] = [
  ['owner_done', 'Owner says done', true],
  ['ok', 'OK from someone', true],
  ['linked_done', 'Linked work done', true],
  ['page', 'A page', false],
  ['pr_open', 'PR open', false],
  ['checks_pass', 'Checks pass', false],
  ['pr_merged', 'PR merged', false],
  ['in_release', 'In a release', false],
];
/** Kinds met with a button: they name who meets them, and have a Who OKs. */
const PE_OK_KINDS = ['ok', 'page'];
/** Kinds that can never be pushed past (C3). */
const PE_NEVER_PUSHED = ['pr_merged', 'in_release'];
const PE_WHO_OKS: [string, string][] = [['people_with_exceptions', 'People, with exceptions'], ['people_only', 'People only']];
const PE_UNITS: [string, number][] = [['minutes', 1], ['hours', 60], ['days', 1440]];
const PE_ASSIGNEE = 'assignee';

const peBots = (): {id: string; name: string}[] =>
  (S.emps || []).filter(e => (e.state ?? e.status) === 'active').map(e => ({id: 'bot:' + e.id, name: botDisplayName(e.id)}));
const pePeople = (): {id: string; name: string}[] =>
  (S.people || []).filter(p => !p.hidden).map(p => ({id: 'human:' + p.id, name: p.name || p.id}));

/** A choice among rows, with an empty first choice. A saved value the rows lack stays chosen under its own id. */
function peChoice(rows: {id: string; name: string}[], on: string | null | undefined, none: string): string {
  const all = on && !rows.some(r => r.id === on) ? [...rows, {id: on, name: actorLabel(on) || on}] : rows;
  return `<option value="">${esc(none)}</option>`
    + all.map(r => `<option value="${esc(r.id)}"${r.id === on ? ' selected' : ''}>${esc(r.name)}</option>`).join('');
}

const peCopy = (rec: PipelineRec): PipelineRec => JSON.parse(JSON.stringify(rec)) as PipelineRec;
const peKindName = (kind: string): string => PE_KINDS.find(([k]) => k === kind)?.[1] ?? kind;

/** "2 days": the expected time as the unit it divides into, and the number of them. */
function peExpected(minutes: number | null): {n: string; unit: number} {
  if (!minutes) return {n: '', unit: 60};
  for (const [, size] of [...PE_UNITS].reverse()) if (minutes % size === 0) return {n: String(minutes / size), unit: size};
  return {n: String(minutes), unit: 1};
}

// ---------------------------------------------------------------- drawing

const peF = (label: string, control: string): string => `<label class="pe-f"><span>${esc(label)}</span>${control}</label>`;

function peReqHTML(req: PipelineReqRec, si: number, ri: number): string {
  const at = `data-pe-si="${si}" data-pe-ri="${ri}"`;
  const okKind = PE_OK_KINDS.includes(req.kind), never = PE_NEVER_PUSHED.includes(req.kind);
  const kinds = PE_KINDS.map(([k, name, may]) =>
    `<option value="${k}"${k === req.kind ? ' selected' : ''}${may || k === req.kind ? '' : ' disabled'}>${esc(name)}${may ? '' : ' (later)'}</option>`).join('');
  const who = req.person_id ? actorLabel(req.person_id) : '';
  return `<details class="pe-req"${req.id ? '' : ' open'}><summary><span class="pe-req-name">${esc(req.line || peKindName(req.kind))}</span>`
    + `<span class="pe-req-who muted">${esc(who)}</span></summary><div class="pe-req-body">`
    + peF('Needs', `<select data-pe-rf="kind" ${at}${req.id ? ' disabled' : ''}>${kinds}</select>`)
    // A page's line is what Jev checks (S11); on any other kind it is the requirement's name.
    + peF(req.kind === 'page' ? 'Jev checks' : 'Name', `<input data-pe-rf="line" ${at} maxlength="200" value="${esc(req.line)}" autocomplete="off">`)
    + (okKind ? peF('From', `<select data-pe-rf="person_id" ${at}>${peChoice([...pePeople(), ...peBots()], req.person_id, 'Choose')}</select>`)
      + peF('Who OKs', `<select data-pe-rf="who_oks" ${at}>${PE_WHO_OKS.map(([v, l]) => `<option value="${v}"${v === req.who_oks ? ' selected' : ''}>${l}</option>`).join('')}</select>`) : '')
    + `<label class="pe-check"><input type="checkbox" role="switch" data-pe-rf="can_push_past" ${at}${req.can_push_past && !never ? ' checked' : ''}${never ? ' disabled' : ''}> Can be pushed past</label>`
    + `<button type="button" class="ghost pe-req-x" data-pe-req-x ${at}>Remove</button></div></details>`;
}

function peStageHTML(state: PeState, stage: PipelineStageRec, si: number, count: number): string {
  const at = `data-pe-si="${si}"`;
  const ex = peExpected(stage.expected_minutes);
  const owners = [{id: PE_ASSIGNEE, name: 'Whoever it is assigned to'}, ...peBots(), ...pePeople()];
  const body = peF('Name', `<input data-pe-sf="name" ${at} maxlength="80" value="${esc(stage.name)}" required autocomplete="off">`)
    + peF('Purpose', `<input data-pe-sf="purpose" ${at} maxlength="300" value="${esc(stage.purpose)}" autocomplete="off">`)
    + peF('Owner', `<select data-pe-sf="owner" ${at}>${peChoice(owners, stage.owner, 'Not set')}</select>`)
    + peF('Tasks leave', `<select data-pe-sf="by_hand" ${at}><option value="0"${stage.by_hand ? '' : ' selected'}>When requirements are met</option>`
      + `<option value="1"${stage.by_hand ? ' selected' : ''}>By hand</option></select>`)
    + (si > 0 ? peF('Limit', `<input type="number" min="1" step="1" inputmode="numeric" data-pe-sf="task_limit" ${at} value="${stage.task_limit ?? ''}" placeholder="None">`) : '')
    + `<div class="pe-f"><span>Expected time</span><span class="pe-pair"><input type="number" min="1" step="1" inputmode="numeric" data-pe-sf="expected_n" ${at} value="${ex.n}" placeholder="None" aria-label="Expected time">`
    + `<select data-pe-sf="expected_unit" ${at} aria-label="Unit">${PE_UNITS.map(([l, size]) => `<option value="${size}"${size === ex.unit ? ' selected' : ''}>${l}</option>`).join('')}</select></span></div>`
    + `<h4 class="pe-h">To leave</h4>`
    + stage.requirements.map((req, ri) => peReqHTML(req, si, ri)).join('')
    + `<button type="button" class="ghost pe-add" data-pe-req-add ${at}>+ Requirement</button>`;
  const tools = `<span class="pe-tools"><button type="button" class="ghost" data-pe-up ${at} aria-label="Move up"${si === 0 ? ' disabled' : ''}>↑</button>`
    + `<button type="button" class="ghost" data-pe-down ${at} aria-label="Move down"${si === count - 1 ? ' disabled' : ''}>↓</button>`
    + `<button type="button" class="ghost" data-pe-stage-x ${at}${count < 2 ? ' disabled' : ''}>Remove</button></span>`;
  return `<details class="pe-stage" ${at}${state.open.has(stage) ? ' open' : ''}><summary><span class="pe-stage-name">${esc(stage.name || 'New stage')}</span>${tools}</summary>`
    + `<div class="pe-stage-body">${body}</div></details>`;
}

function peClashHTML(state: PeState): string {
  const clash = state.clash;
  if (!clash) return '';
  return `<div class="pe-clash" role="alert"><p>Someone saved this pipeline while you had it open. They changed: ${esc(clash.changed.join(', ') || 'nothing you can see here')}.</p>`
    + `<span class="pe-clash-do"><button type="button" class="ghost" data-pe-theirs>Load theirs</button>`
    + `<button type="button" class="primary" data-pe-mine>Save mine</button></span></div>`;
}

function peBodyHTML(state: PeState): string {
  const d = state.draft;
  const factories = (state.factories ?? []).map(f => ({id: f.id, name: f.name}));
  const fields = peF('Goal', `<input data-pe-pf="goal" maxlength="300" value="${esc(d.goal)}" autocomplete="off">`)
    + peF('Manager', `<select data-pe-pf="manager_id">${peChoice(peBots(), d.manager_id, 'Not set')}</select>`)
    + (state.factories ? peF('Factory', `<select data-pe-pf="factory_id">${peChoice(factories, d.factory_id, 'None')}</select>`) : '')
    + peF('Prefix', `<input data-pe-pf="prefix" maxlength="2" value="${esc(d.prefix ?? '')}" autocomplete="off">`);
  const stages = d.stages.map((s, i) => peStageHTML(state, s, i, d.stages.length)).join('');
  return `${peClashHTML(state)}<div class="pe-fields">${fields}</div><div class="pe-stages">${stages}</div>`
    + `<p class="err" role="alert" data-pe-error${state.error ? '' : ' hidden'}>${esc(state.error)}</p>`
    + `<div class="pe-foot"><button type="button" class="ghost" data-pe-stage-add>+ Stage</button>`
    + `<button type="button" class="primary" data-pe-save${state.saving ? ' disabled' : ''}>Save</button></div>`;
}

function peDraw(state: PeState): void {
  const body = document.getElementById('pe-body');
  if (PE !== state || !body) return;
  const scroll = body.scrollTop;
  body.innerHTML = peBodyHTML(state);
  body.scrollTop = scroll;
}

// ---------------------------------------------------------------- editing the draft

const peIndex = (el: HTMLElement, key: string): number => Number(el.dataset[key] ?? -1);

/** A field changed: the draft follows it. A change that alters what the panel shows (a requirement's kind) draws again. */
function peInput(state: PeState, el: HTMLInputElement | HTMLSelectElement): void {
  const value = el.value, checked = el instanceof HTMLInputElement && el.type === 'checkbox' ? el.checked : false;
  const d = state.draft;
  const pf = el.dataset['pePf'];
  if (pf) {
    if (pf === 'goal') d.goal = value;
    else if (pf === 'prefix') d.prefix = value.trim() || null;
    else if (pf === 'manager_id') d.manager_id = value || null;
    else if (pf === 'factory_id') d.factory_id = value || null;
    return;
  }
  const stage = d.stages[peIndex(el, 'peSi')];
  if (!stage) return;
  const sf = el.dataset['peSf'];
  if (sf) {
    if (sf === 'name') stage.name = value;
    else if (sf === 'purpose') stage.purpose = value;
    else if (sf === 'owner') stage.owner = value || null;
    else if (sf === 'by_hand') stage.by_hand = value === '1';
    else if (sf === 'task_limit') stage.task_limit = Number(value) > 0 ? Math.round(Number(value)) : null;
    else if (sf === 'expected_n' || sf === 'expected_unit') {
      const panel = el.closest('.pe-pair');
      const n = Number(panel?.querySelector<HTMLInputElement>('[data-pe-sf="expected_n"]')?.value ?? '');
      const unit = Number(panel?.querySelector<HTMLSelectElement>('[data-pe-sf="expected_unit"]')?.value ?? 60);
      stage.expected_minutes = n > 0 ? Math.round(n * unit) : null;
    }
    if (sf === 'name') {
      const summary = el.closest('.pe-stage')?.querySelector('.pe-stage-name');
      if (summary) summary.textContent = value || 'New stage';
    }
    return;
  }
  const req = stage.requirements[peIndex(el, 'peRi')];
  const rf = el.dataset['peRf'];
  if (!req || !rf) return;
  if (rf === 'line') req.line = value;
  else if (rf === 'person_id') req.person_id = value || null;
  else if (rf === 'who_oks') req.who_oks = value as PipelineReqRec['who_oks'];
  else if (rf === 'can_push_past') req.can_push_past = checked;
  else if (rf === 'kind') {
    req.kind = value;
    const ok = PE_OK_KINDS.includes(value);
    req.who_oks = ok ? 'people_with_exceptions' : 'none';
    if (!ok) req.person_id = null;
    req.can_push_past = !PE_NEVER_PUSHED.includes(value);
    peDraw(state);
  }
}

function peClick(state: PeState, target: Element): void {
  const el = target.closest<HTMLElement>('button');
  if (!el) return;
  const d = state.draft, si = peIndex(el, 'peSi'), ri = peIndex(el, 'peRi'), stage = d.stages[si];
  if (el.hasAttribute('data-pe-save')) { void peSave(state); return; }
  if (el.hasAttribute('data-pe-theirs') && state.clash) {
    state.base = state.clash.theirs; state.draft = peCopy(state.clash.theirs); state.clash = null; state.error = '';
    state.open = new WeakSet();
    peDraw(state); return;
  }
  if (el.hasAttribute('data-pe-mine') && state.clash) {
    state.base = state.clash.theirs; state.clash = null;
    void peSave(state); return;
  }
  if (el.hasAttribute('data-pe-stage-add')) {
    const fresh: PipelineStageRec = {name: '', purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
      expected_bots_only: false, on_enter: {}, requirements: []};
    d.stages.push(fresh);
    state.open.add(fresh);
    peDraw(state);
    document.querySelector<HTMLInputElement>('#pe-body .pe-stage:last-of-type [data-pe-sf="name"]')?.focus();
    return;
  }
  if (!stage) return;
  const swap = (by: number): void => {
    const other = d.stages[si + by];
    if (!other) return;
    d.stages[si + by] = stage; d.stages[si] = other;
    peDraw(state);
  };
  if (el.hasAttribute('data-pe-up')) swap(-1);
  else if (el.hasAttribute('data-pe-down')) swap(1);
  else if (el.hasAttribute('data-pe-stage-x')) { if (d.stages.length > 1) { d.stages.splice(si, 1); peDraw(state); } }
  else if (el.hasAttribute('data-pe-req-add')) {
    stage.requirements.push({kind: 'owner_done', settings: {}, can_push_past: true, who_oks: 'none', person_id: null, line: ''});
    peDraw(state);
  } else if (el.hasAttribute('data-pe-req-x')) { stage.requirements.splice(ri, 1); peDraw(state); }
}

// ---------------------------------------------------------------- saving

/** The panels open before a save, found again in the saved record by their place. */
function peOpenSame(open: WeakSet<PipelineStageRec>, before: PipelineStageRec[], saved: PipelineRec): WeakSet<PipelineStageRec> {
  const next = new WeakSet<PipelineStageRec>();
  saved.stages.forEach((stage, i) => { const was = before[i]; if (was && open.has(was)) next.add(stage); });
  return next;
}

/** What changed between two records, as a person names it: a pipeline field, or a stage by its name. */
function peChanged(a: PipelineRec, b: PipelineRec): string[] {
  const out: string[] = [];
  const fields: [keyof PipelineRec, string][] = [['name', 'Name'], ['goal', 'Goal'], ['manager_id', 'Manager'],
    ['factory_id', 'Factory'], ['prefix', 'Prefix'], ['private', 'Private'], ['owner_id', 'Owner']];
  for (const [key, label] of fields) if (JSON.stringify(a[key]) !== JSON.stringify(b[key])) out.push(label);
  const byId = new Map(a.stages.map(s => [s.id, s]));
  for (const s of b.stages) {
    const before = s.id ? byId.get(s.id) : undefined;
    if (!before) out.push(`${s.name} (added)`);
    else if (JSON.stringify(before) !== JSON.stringify(s)) out.push(s.name);
  }
  const kept = new Set(b.stages.map(s => s.id));
  for (const s of a.stages) if (!kept.has(s.id)) out.push(`${s.name} (removed)`);
  if (!out.length && a.stages.map(s => s.id).join() !== b.stages.map(s => s.id).join()) out.push('The order of the stages');
  return out;
}

async function peSave(state: PeState): Promise<void> {
  if (state.saving) return;
  state.saving = true; state.error = '';
  peDraw(state);
  try {
    const saved = await pipelineSave(state.id, {...state.draft, settings_version: state.base.settings_version});
    if (PE !== state) return;
    // The editor stays as S11 draws it, now on the saved record: its ids and its new settings_version.
    state.saving = false;
    const draft = peCopy(saved);
    state.open = peOpenSame(state.open, state.draft.stages, draft);
    state.base = saved; state.draft = draft;
    toast('Saved');
    peDraw(state);
  } catch (error) {
    if (PE !== state) return;
    state.saving = false;
    const refused = taskRefusedOf(error);
    if (refused?.error === 'task_changed') {
      try {
        const theirs = await pipelineRec(state.id);
        if (PE !== state) return;
        state.clash = {theirs, changed: peChanged(state.base, theirs)};
      } catch (again) { state.error = errorMessage(again, 'Could not read the pipeline again.'); }
    } else {
      state.error = refused?.detail || errorMessage(error, 'Could not save.');
    }
    peDraw(state);
  }
}

// ---------------------------------------------------------------- opening

/** Opens the editor for a pipeline; with a stage id, that stage's panel is open and in view. */
async function openPipelineEditor(pipelineId: string, stageId = ''): Promise<void> {
  document.getElementById('pe-dialog')?.remove();
  const dialog = document.createElement('dialog');
  dialog.id = 'pe-dialog'; dialog.className = 'tmodal tmodal-shell pe-dialog';
  dialog.setAttribute('aria-labelledby', 'pe-title');
  dialog.innerHTML = `<div class="tmodal-head"><h2 id="pe-title">Pipeline</h2><button class="ghost tmodal-x" type="button" data-pe-close aria-label="Close">✕</button></div>
    <div class="tmodal-body pe-body" id="pe-body">${skelRows(3, {dot: false})}</div>`;
  document.body.append(dialog);
  const leave = (): void => dialog.close();
  window.addEventListener('hashchange', leave, {once: true});
  dialog.addEventListener('close', () => { window.removeEventListener('hashchange', leave); dialog.remove(); PE = null; });
  dialog.addEventListener('click', ev => {
    const t = eventElement(ev);
    if (!t) return;
    if (t.closest('[data-pe-close]')) { dialog.close(); return; }
    // A button in a stage's summary (up, down, remove) does not also open or close the stage.
    if (t.closest('summary button')) ev.preventDefault();
    if (PE) peClick(PE, t);
  });
  // A stage's panel stays open or closed across redraws.
  dialog.addEventListener('toggle', ev => {
    const panel = ev.target instanceof HTMLDetailsElement && ev.target.classList.contains('pe-stage') ? ev.target : null;
    const stage = panel && PE ? PE.draft.stages[peIndex(panel, 'peSi')] : undefined;
    if (!PE || !panel || !stage) return;
    if (panel.open) PE.open.add(stage); else PE.open.delete(stage);
  }, true);
  const onField = (ev: Event): void => {
    const t = ev.target;
    if (PE && (t instanceof HTMLInputElement || t instanceof HTMLSelectElement)) peInput(PE, t);
  };
  // Typing follows each key; a choice or a switch follows its change.
  const choice = (t: EventTarget | null): boolean => t instanceof HTMLSelectElement || (t instanceof HTMLInputElement && t.type === 'checkbox');
  dialog.addEventListener('input', ev => { if (!choice(ev.target)) onField(ev); });
  dialog.addEventListener('change', ev => { if (choice(ev.target)) onField(ev); });
  dialog.showModal();
  try {
    const [rec, factories] = await Promise.all([
      pipelineRec(pipelineId),
      factoriesRead('7d').then(map => map.factories, () => null),
    ]);
    if (!dialog.isConnected) return;
    const state: PeState = {id: pipelineId, base: rec, draft: peCopy(rec), open: new WeakSet(), factories, clash: null, error: '', saving: false};
    const opened = state.draft.stages.find(s => s.id === stageId);
    if (opened) state.open.add(opened);
    PE = state;
    const title = dialog.querySelector('#pe-title');
    if (title) title.textContent = rec.name;
    peDraw(state);
    if (opened) dialog.querySelector('.pe-stage[open]')?.scrollIntoView({block: 'nearest'});
  } catch (error) {
    toast(errorMessage(error, 'Could not open the pipeline.'), true);
    dialog.close();
  }
}
