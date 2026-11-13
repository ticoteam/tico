/* ui/app/stage-rules.ts — A stage's rules (Stage rules spec, step 1, part 3): the Rules button with its count on every
   stage header of a pipeline board, and the panel it opens: every rule that applies to the stage, grouped (this stage,
   the whole pipeline, company, built in), each with what it costs (free, or one Jev call per event). Whoever may change
   the pipeline adds a rule (On starts as this stage), edits it and retires it; Built in is read-only.
   Read from and written to backend/stage_rules_api.py: GET /api/v2/stage-rules?pipeline=<id> once per board for the
   counts, and again with &stage=<id> when the panel opens. A rule says whether the caller may change it (`can_edit`);
   the server checks again, and a refusal shows as one line. An edit sends the rule's `revision`: a 409 says so.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

type SrEvent = 'enters' | 'owner_changes' | 'bot_snoozes' | 'timer';
/** When: one event, or actions joined by commas (`enters,owner_changes`). */
type SrWhen = SrEvent | (string & {});
type SrThen = 'block' | 'tell_manager' | 'tell_owner' | 'move_back';
type SrScope = 'stage' | 'pipeline' | 'company';
/** What a stage rule says: On, When, If, Then. */
interface StageRuleCore {
  id: string; name: string; pipeline_id: string; stages: string[]; stage_names?: string[]; pipeline_name?: string;
  event: SrWhen; timer?: {kind?: string; hours?: number}; cond?: Record<string, unknown>; jev?: string;
  then: SrThen; reason?: string; scope: SrScope; revision: number;
}
/** A stage rule as the API sends it (backend/stage_rules_api.py view: names, `can_edit`, and for a bot's suggested
 *  change or retire the rule it `replaces` and whether it `retires` it). */
interface StageRule extends StageRuleCore {
  status: string; cost: string; can_edit?: boolean; created_by?: string; suggests?: string;
  replaces?: StageRuleCore; retires?: boolean; retire_why?: string;
}
/** A behaviour in code that applies at a stage: listed, never edited. */
interface StageBuiltIn { name: string; what?: string; where?: string }
interface StageRulesList { items: StageRule[]; built_in: StageBuiltIn[]; may_add?: boolean }

const SR_EVENTS: [SrEvent, string][] = [['enters', 'A task enters'], ['owner_changes', 'The owner changes'], ['bot_snoozes', 'A bot snoozes'], ['timer', 'A timer']];
const SR_TIMERS: [string, string][] = [['in_stage', 'in the stage'], ['quiet', 'quiet'], ['waiting_person', 'waiting on a person']];
const SR_THENS: [SrThen, string][] = [['block', 'Block'], ['tell_manager', 'Tell the manager'], ['tell_owner', 'Tell the owner'], ['move_back', 'Move back']];
const SR_GROUPS: [SrScope, string][] = [['stage', 'This stage'], ['pipeline', 'The whole pipeline'], ['company', 'Company']];
const SR_FREE = 'free';

/** The rules that apply in each pipeline the board has drawn: pipeline id -> its rules, or the read in flight. */
const SR_BOARD = new Map<string, StageRule[] | Promise<void>>();

/** The Add rule and Edit form's fields by name (`on` is absent for a company rule, whose On is fixed). */
interface SrControls {
  name: HTMLInputElement; on?: HTMLSelectElement; event: HTMLSelectElement; hours: HTMLInputElement; timer_kind: HTMLSelectElement;
  owner_check: HTMLSelectElement; owner_team: HTMLInputElement; label: HTMLInputElement; jev: HTMLTextAreaElement; then: HTMLSelectElement; reason: HTMLInputElement;
}
const srControls = (form: HTMLFormElement) => form.elements as unknown as SrControls;
const srTarget = (ev: Event) => ev.target as HTMLElement;

const srWord = <K extends string>(pairs: [K, string][], key: string | undefined) => pairs.find(([k]) => k === key)?.[1] ?? (key || '');

/** Whether a rule of the pipeline's list applies at this stage: the company's, the whole pipeline's, or one naming it. */
const srApplies = (r: StageRule, stageId: string) => r.scope !== 'stage' || r.stages.includes(stageId);

/** How many rules apply at a stage, or null while the board's read is out. */
function srCount(pipelineId: string, stageId: string): number | null {
  const rows = SR_BOARD.get(pipelineId);
  return Array.isArray(rows) ? rows.filter(r => srApplies(r, stageId)).length : null;
}

/** The board's rules, read once; each stage's count is written in place when they come. */
function srBoardLoad(pipelineId: string, fresh = false): void {
  if (!fresh && SR_BOARD.has(pipelineId)) return;
  const read = get<StageRulesList>(`/v2/stage-rules?pipeline=${encodeURIComponent(pipelineId)}`, {fresh: true})
    .then(got => { SR_BOARD.set(pipelineId, got.items || []); srCountsPaint(pipelineId); })
    .catch(() => { SR_BOARD.delete(pipelineId); });
  SR_BOARD.set(pipelineId, read);
}

function srCountsPaint(pipelineId: string): void {
  document.querySelectorAll<HTMLElement>(`[data-sr-open][data-sr-pipeline="${CSS.escape(pipelineId)}"]`).forEach(b => {
    const n = srCount(pipelineId, b.dataset['srOpen'] ?? '');
    const num = b.querySelector('.tnum');
    if (num) num.textContent = n === null ? '' : String(n);
    b.setAttribute('aria-label', srButtonLabel(b.dataset['srName'] ?? '', n));
  });
}

const srButtonLabel = (stageName: string, n: number | null) => `Rules for ${stageName}${n === null ? '' : `: ${n}`}`;

/** "Rules 3": the stage's Rules button, for a stage header. Its count fills in when the board's rules are read. */
function stageRulesButtonHTML(pipelineId: string, stage: {id?: string | null; name: string}): string {
  const id = stage.id ?? '';
  if (!id) return '';
  srBoardLoad(pipelineId);
  const n = srCount(pipelineId, id);
  return `<button type="button" class="sr-btn" data-sr-open="${esc(id)}" data-sr-pipeline="${esc(pipelineId)}" data-sr-name="${esc(stage.name)}"`
    + ` aria-label="${esc(srButtonLabel(stage.name, n))}" title="Rules">Rules <span class="tnum">${n === null ? '' : n}</span></button>`;
}

// ---------------------------------------------------------------- the panel

interface SrOpen { pipelineId: string; stageId: string; stageName: string; list: StageRulesList | null; note: string }
let SR: SrOpen | null = null;

function srDialog(): HTMLDialogElement {
  let dlg = document.getElementById('sr-panel') as HTMLDialogElement | null;
  if (!dlg) {
    dlg = document.createElement('dialog');
    dlg.id = 'sr-panel'; dlg.className = 'ru-modal sr-modal';
    dlg.addEventListener('click', ev => {
      if (ev.target === dlg) { dlg?.close(); return; }
      void srClick(ev);
    });
    dlg.addEventListener('submit', ev => { void srSubmit(ev as SubmitEvent); });
    dlg.addEventListener('change', ev => { srFormSync(srTarget(ev).closest('form')); });
    dlg.addEventListener('input', ev => { srFormSync(srTarget(ev).closest('form')); });
    document.body.appendChild(dlg);
  }
  return dlg;
}

/** Open a stage's panel: every rule that applies there, grouped. */
async function stageRulesOpen(pipelineId: string, stageId: string, stageName: string): Promise<void> {
  SR = {pipelineId, stageId, stageName, list: null, note: ''};
  const dlg = srDialog();
  dlg.setAttribute('aria-label', `Rules for ${stageName}`);
  dlg.innerHTML = `${srHeadHTML(stageName)}<p class="sr-pad muted">Loading…</p>`;
  if (!dlg.open) dlg.showModal();
  await srReload();
}

async function srReload(note = ''): Promise<void> {
  const open = SR;
  if (!open) return;
  try {
    const q = `pipeline=${encodeURIComponent(open.pipelineId)}&stage=${encodeURIComponent(open.stageId)}`;
    open.list = await get<StageRulesList>(`/v2/stage-rules?${q}`, {fresh: true});
  } catch (e) { open.list = null; note = ruError(e); }
  if (SR !== open) return;
  open.note = note;
  srDraw();
}

/** "A task enters or the owner changes": a When of one event or several actions. */
function srWhen(event: string): string {
  return event.split(',').filter(Boolean).map((e, i) => {
    const w = srWord(SR_EVENTS, e);
    return i ? w.charAt(0).toLowerCase() + w.slice(1) : w;
  }).join(' or ');
}

function srHeadHTML(title: string, back = false): string {
  return `<div class="ru-side-h">${back ? '<button class="ghost" type="button" data-sr-back aria-label="Back">‹</button>' : ''}`
    + `<h2>${esc(title)}</h2><span class="spacer"></span><button class="ghost tmodal-x" type="button" data-sr-close aria-label="Close">✕</button></div>`;
}

/** "When the owner changes → If owner not on engineering → Then Block": the rule in one line. */
function srSentence(r: StageRuleCore): string {
  const {when, ifs, act} = srParts(r);
  return `${when}${ifs ? ` → ${ifs}` : ''} → ${act}`;
}

/** A rule's When, If (empty for none) and Then (`act`) in words, each on its own so a change can show what differs. */
function srParts(r: StageRuleCore): {when: string; ifs: string; act: string} {
  let when = srWhen(r.event);
  if (r.event === 'timer' && r.timer?.hours) when = `${r.timer.hours} h ${srWord(SR_TIMERS, r.timer.kind)}`;
  const ifs = Object.entries(r.cond || {}).flatMap(([k, v]): string[] => {
    // An owner's team that is empty means the pipeline manager's team (backend/stage_rules.py CONDITIONS).
    if (k === 'owner_on_team' || k === 'owner_not_on_team') {
      return [`owner ${k === 'owner_on_team' ? 'on' : 'not on'} ${typeof v === 'string' && v ? v : "the manager's team"}`];
    }
    if (v === '' || v === null || v === undefined) return [];
    return [`${k.replace(/_/g, ' ')} ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`];
  });
  if (r.jev) ifs.push(`Jev: ${r.jev}`);
  return {when, ifs: ifs.join(', '), act: srWord(SR_THENS, r.then)};
}

function srRowHTML(r: StageRule): string {
  const cost = r.cost === SR_FREE ? 'Free' : r.cost.charAt(0).toUpperCase() + r.cost.slice(1);
  const acts = r.can_edit
    ? `<span class="sr-acts"><button type="button" class="ghost sr-act" data-sr-edit="${esc(r.id)}">Edit</button>`
      + `<button type="button" class="ghost sr-act" data-sr-retire="${esc(r.id)}">Retire</button></span>` : '';
  return `<li class="sr-rule" data-sr-rule="${esc(r.id)}"><div class="sr-line"><span class="sr-name">${esc(r.name)}</span>`
    + `<span class="sr-cost${r.cost === SR_FREE ? '' : ' sr-cost-jev'}" data-sr-cost>${esc(cost)}</span>${acts}</div>`
    + `<div class="sr-what muted">${esc(srSentence(r))}</div>${r.reason ? `<div class="sr-says muted">“${esc(r.reason)}”</div>` : ''}</li>`;
}

function srDraw(): void {
  const open = SR, dlg = srDialog();
  if (!open) return;
  const list = open.list;
  if (!list) {
    dlg.innerHTML = `${srHeadHTML(`Rules · ${open.stageName}`)}<p class="sr-pad ru-status" role="status">${esc(open.note)}</p>`;
    return;
  }
  const groups = SR_GROUPS.map(([scope, label]) => {
    const rows = list.items.filter(r => r.scope === scope);
    const body = rows.length ? `<ul class="sr-list">${rows.map(srRowHTML).join('')}</ul>` : '<p class="sr-none muted">None</p>';
    return `<section class="sr-group" data-sr-group="${scope}"><h3>${esc(label)} <span class="ru-n tnum">${rows.length}</span></h3>${body}</section>`;
  }).join('');
  const built = list.built_in || [];
  const builtHTML = `<section class="sr-group" data-sr-group="builtin"><h3>Built in <span class="ru-n tnum">${built.length}</span> <span class="sr-incode muted">in code, read-only</span></h3>`
    + (built.length ? `<ul class="sr-list">${built.map(b => `<li class="sr-rule sr-builtin"><div class="sr-line"><span class="sr-name">${esc(b.name)}</span></div>`
      + `${b.what ? `<div class="sr-what muted">${esc(b.what)}</div>` : ''}</li>`).join('')}</ul>` : '<p class="sr-none muted">None</p>') + '</section>';
  const foot = list.may_add
    ? '<div class="ru-foot sr-foot"><span class="spacer"></span><button class="primary" type="button" data-sr-add>Add rule</button></div>'
    : '<p class="sr-pad muted sr-who">Only the pipeline\'s owner, its manager and Admins change its rules.</p>';
  dlg.innerHTML = `${srHeadHTML(`Rules · ${open.stageName}`)}<p class="sr-pad ru-status" role="status">${esc(open.note)}</p>`
    + `<div class="sr-groups">${groups}${builtHTML}</div>${foot}`;
}

// ---------------------------------------------------------------- add and edit

function srOptions<K extends string>(pairs: [K, string][], current: string): string {
  return pairs.map(([k, l]) => `<option value="${esc(k)}"${k === current ? ' selected' : ''}>${esc(l)}</option>`).join('');
}

/** The Add rule and Edit form: On, When, If, Then, and what it costs. A new rule starts On this stage. */
function srFormHTML(r: StageRule | null): string {
  const open = SR;
  if (!open) return '';
  const on = r ? r.scope : 'stage';
  const cond = r?.cond || {};
  const onField = r && r.scope === 'company'
    ? '<p class="muted">Company: every pipeline</p>'
    : `<select name="on">${srOptions<string>([['stage', `This stage (${open.stageName})`], ['pipeline', 'The whole pipeline']], on)}</select>`;
  const str = (v: unknown) => (typeof v === 'string' || typeof v === 'number' ? String(v) : '');
  const owner = 'owner_on_team' in cond ? 'on' : 'owner_not_on_team' in cond ? 'not_on' : '';
  const team = str(cond['owner_on_team'] ?? cond['owner_not_on_team']);
  // A When of several actions (a default rule's) is kept as it is unless another is picked.
  const events: [string, string][] = SR_EVENTS.some(([k]) => k === (r?.event ?? 'enters')) ? SR_EVENTS : [...SR_EVENTS, [r?.event ?? '', srWhen(r?.event ?? '')]];
  return `${srHeadHTML(r ? `Edit · ${r.name}` : `Add rule · ${open.stageName}`, true)}
    <form class="ru-form sr-form" data-sr-form="${esc(r?.id ?? '')}" data-sr-revision="${r?.revision ?? ''}">
      <label class="ru-fl"><span class="ru-lbl">Name</span><input name="name" maxlength="200" required value="${esc(r?.name ?? '')}"></label>
      <div class="ru-fl"><span class="ru-lbl">On</span>${onField}</div>
      <label class="ru-fl"><span class="ru-lbl">When</span><select name="event">${srOptions(events, r?.event ?? 'enters')}</select></label>
      <div class="ru-fl sr-timer" data-sr-timer><span class="ru-lbl">Timer</span><span class="sr-row">
        <input name="hours" type="number" min="1" max="720" value="${esc(str(r?.timer?.hours) || '6')}" aria-label="Hours"> h
        <select name="timer_kind" aria-label="Timer">${srOptions(SR_TIMERS, r?.timer?.kind ?? 'quiet')}</select></span></div>
      <div class="ru-fl"><span class="ru-lbl">If <span class="ru-opt">(optional)</span></span>
        <span class="sr-row"><select name="owner_check" aria-label="The owner's team">${srOptions<string>([['', 'Any owner'], ['on', 'Owner on team'], ['not_on', 'Owner not on team']], owner)}</select>
        <input name="owner_team" maxlength="200" placeholder="A team's id; empty: the manager's team" aria-label="Team" value="${esc(team)}"></span>
        <input name="label" maxlength="200" placeholder="Has label" aria-label="Has label" value="${esc(str(cond['label']))}">
        <textarea name="jev" rows="2" maxlength="1000" placeholder="A question Jev answers" aria-label="A question Jev answers">${esc(r?.jev ?? '')}</textarea></div>
      <label class="ru-fl"><span class="ru-lbl">Then</span><select name="then">${srOptions(SR_THENS, r?.then ?? 'tell_manager')}</select></label>
      <label class="ru-fl"><span class="ru-lbl">Says</span><input name="reason" maxlength="1000" value="${esc(r?.reason ?? '')}" placeholder="What the Block or Tell says"></label>
      <p class="sr-costline muted" data-sr-costline></p>
      <p class="ru-status" role="status"></p>
      <div class="ru-foot"><span class="spacer"></span><button class="ghost" type="button" data-sr-back>Cancel</button><button class="primary" type="submit">Save</button></div>
    </form>`;
}

/** The timer's fields show only for a timer; the cost line follows the Jev question (timers never call a model). */
function srFormSync(form: HTMLFormElement | null): void {
  if (!form || !form.matches('[data-sr-form]')) return;
  const f = srControls(form);
  const timer = f.event.value === 'timer';
  const box = form.querySelector<HTMLElement>('[data-sr-timer]');
  if (box) box.hidden = !timer;
  const jev = !!f.jev.value.trim();
  const line = form.querySelector<HTMLElement>('[data-sr-costline]');
  if (line) line.textContent = jev ? 'Cost: one Jev call per event' : 'Cost: free';
}

function srFormOpen(r: StageRule | null): void {
  const dlg = srDialog();
  dlg.innerHTML = srFormHTML(r);
  const form = dlg.querySelector<HTMLFormElement>('form');
  srFormSync(form);
  form?.querySelector<HTMLInputElement>('[name=name]')?.focus();
}

function srBody(form: HTMLFormElement, editing: StageRule | null): Record<string, unknown> {
  const open = SR as SrOpen;
  const f = srControls(form);
  const event = f.event.value;
  const cond: Record<string, unknown> = {...(editing?.cond || {})};
  delete cond['owner_on_team'];
  delete cond['owner_not_on_team'];
  if (f.owner_check.value) cond[f.owner_check.value === 'on' ? 'owner_on_team' : 'owner_not_on_team'] = f.owner_team.value.trim();
  const label = f.label.value.trim();
  if (label) cond['label'] = label; else delete cond['label'];
  const body: Record<string, unknown> = {
    name: f.name.value.trim(), event, cond, jev: f.jev.value.trim(), reason: f.reason.value.trim(),
    timer: event === 'timer' ? {kind: f.timer_kind.value, hours: Number(f.hours.value) || 1} : {},
  };
  body['then'] = f.then.value;  // set apart: an object literal with a `then` key reads as a promise to the linter
  if (!editing || editing.scope !== 'company') {
    body.pipeline = open.pipelineId;
    const on = f.on?.value ?? 'stage';
    body.stages = on === 'pipeline' ? [] : editing && editing.scope === 'stage' ? editing.stages : [open.stageId];
  }
  return body;
}

async function srSubmit(ev: SubmitEvent): Promise<void> {
  const form = srTarget(ev).closest<HTMLFormElement>('form[data-sr-form]');
  const open = SR;
  if (!form || !open) return;
  ev.preventDefault();
  const id = form.dataset['srForm'] ?? '';
  const editing = id ? open.list?.items.find(r => r.id === id) ?? null : null;
  const status = form.querySelector<HTMLElement>('.ru-status'), button = form.querySelector<HTMLButtonElement>('[type=submit]');
  if (button) button.disabled = true;
  if (status) status.textContent = 'Saving…';
  try {
    const body = srBody(form, editing);
    const saved = id
      ? await post(`/v2/stage-rules/${encodeURIComponent(id)}`, {...body, revision: Number(form.dataset['srRevision'])})
      : await post('/v2/stage-rules', body);
    toast(saved.status === 'suggested' ? 'Added to Suggested' : id ? 'Saved' : 'Added');
    srBoardLoad(open.pipelineId, true);
    await srReload();
  } catch (e) {
    if (button) button.disabled = false;
    if (status) status.textContent = errorStatus(e) === 409 ? 'This rule changed since you opened it. Go back and open it again.' : ruError(e);
  }
}

async function srRetire(id: string): Promise<void> {
  const open = SR, r = open?.list?.items.find(x => x.id === id);
  if (!open || !r) return;
  if (!confirm(`Retire "${r.name}"? It stops applying.`)) return;
  try {
    await post(`/v2/stage-rules/${encodeURIComponent(id)}/retire`, {revision: r.revision});
    toast('Retired');
    srBoardLoad(open.pipelineId, true);
    await srReload();
  } catch (e) { await srReload(ruError(e)); }
}

async function srClick(ev: MouseEvent): Promise<void> {
  const el = srTarget(ev).closest<HTMLElement>('[data-sr-close],[data-sr-back],[data-sr-add],[data-sr-edit],[data-sr-retire]');
  if (!el || !SR) return;
  if (el.matches('[data-sr-close]')) { srDialog().close(); return; }
  if (el.matches('[data-sr-back]')) { srDraw(); return; }
  if (el.matches('[data-sr-add]')) { srFormOpen(null); return; }
  const editId = el.dataset['srEdit'];
  if (editId) { srFormOpen(SR.list?.items.find(r => r.id === editId) ?? null); return; }
  const retireId = el.dataset['srRetire'];
  if (retireId) await srRetire(retireId);
}
