/* ui/app/pipeline-editor.ts — The pipeline editor (docs/pipelines-v3.md S11): a pipeline's Goal, Manager, Factory and
   Prefix (no Private: pipeline_settings refuses a private pipeline until one is defined), then its stages, one panel
   each, closed until clicked. A stage reads in the order a task moves: its Name and Purpose; When a task arrives (On
   enter, a Limit); Who works on it (Owner, an Expected time); To leave (how tasks leave, and the requirements:
   Responses, such as done, an OK or an answer with its name, question and type, and Links, such as a task, a page or a
   PR). An Owner can be "One of several" (backend/stage_pick.py): the bots to pick from, how, a limit each, a preference
   and whether the pick owns the task; a person sets it, and a bot's change is refused by the server. Done and Closed
   are not listed: every pipeline ends in them.
   It opens from a stage's header (that stage open) or the page's edit button, for whoever may change the pipeline.
   The whole record is read (GET /api/v2/pipelines/{id}) and saved at once (POST, with the settings_version it was read
   at). When someone else saved first, the editor says what they changed and lets the person load theirs or save theirs
   over it. Any other refusal shows the server's words. A stage's On enter files a linked task into a pipeline or queues
   its PRs; a page has Jev's check and its switches, In a release its repo and whether pre-releases count. What the
   editor does not show (Expected for bots only, other settings) is sent back as it was read.
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
  /** The saved requirements whose panel is open (a new one is open until it is saved). */
  reqOpen: WeakSet<PipelineReqRec>;
  factories: FactoryRow[] | null;
  /** The pipelines a stage can file into (On enter). */
  pipelines: {id: string; name: string}[];
  /** Someone saved first: what they changed, and their record. */
  clash: {theirs: PipelineRec; changed: string[]} | null;
  error: string;
  saving: boolean;
}

let PE: PeState | null = null;

/** What a stage's requirement can need: a kind, or one of GitHub's conditions (kind `item`, source `github`). A response
    is given by someone (done, an OK, a typed answer); a link is met by other work (a task, a page, a PR). */
const PE_KINDS: [string, string][] = [
  ['owner_done', 'Owner says done'],
  ['ok', 'OK from someone'],
  ['answer', 'Answer'],
  ['linked_done', 'Linked work done'],
  ['page', 'A page'],
  ['pr_open', 'PR open'],
  ['checks_pass', 'Checks pass'],
  ['pr_merged', 'PR merged'],
  ['in_release', 'In a release'],
];
/** A requirement's switches, by kind: the setting and its label. */
const PE_SWITCHES: Record<string, [string, string][]> = {
  page: [['at_head', 'Newest commit only'], ['with_release', 'With its release']],
  in_release: [['prereleases', 'Pre-releases count']],
};
/** The kinds the Needs list puts under Responses; the rest are Links. */
const PE_RESPONSES = ['owner_done', 'ok', 'answer'];
/** A stage's answer (kind `answer`): its types, and what its name looks like (task_requirements.ANSWER_NAME). */
const PE_ANSWER_TYPES: [string, string][] = [['text', 'Text'], ['number', 'Number'], ['choice', 'Choice']];
const PE_YES_NO = ['Yes', 'No'];
/** An answer's name from its title: "Confidence (0-100)" is confidence_0_100. */
const peSlug = (title: string): string =>
  title.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^[^a-z]+/, '').slice(0, 40).replace(/_+$/, '');
/** Kinds met with a button: they name who meets them, and have a Who OKs. */
const PE_OK_KINDS = ['ok', 'page'];
/** Kinds that can never be pushed past (C3). */
const PE_NEVER_PUSHED = ['pr_merged', 'in_release'];
/** GitHub's conditions: a requirement of kind `item` on the task's PRs. */
const PE_GITHUB = ['pr_open', 'checks_pass', 'pr_merged', 'in_release'];

/** What a requirement needs, as the Needs list names it: its kind, or its condition for an item. */
const peNeeds = (req: PipelineReqRec): string => req.kind === 'item' ? req.condition ?? '' : req.kind;
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

/** A field: its name above its control, as every form in the app. `size` narrows it ('short': a number or a prefix). */
const peF = (label: string, control: string, size = ''): string =>
  `<label class="pe-f${size ? ' pe-f-' + size : ''}"><span>${esc(label)}</span>${control}</label>`;
/** Keep-warm hours as typed: a whole number from 0 to 24, or null for empty. */
const peHours = (value: string): number | null =>
  value.trim() === '' || !Number.isFinite(Number(value)) ? null : Math.min(24, Math.max(0, Math.round(Number(value))));
/** Fields side by side on one line; they wrap on a narrow screen. */
const peRow = (fields: string): string => `<div class="pe-row">${fields}</div>`;

/** A stage's answer: who gives it (nobody named is the task's owner), its name, the question, and its type with a
    number's bounds or a choice's choices. */
function peAnswerHTML(req: PipelineReqRec, at: string): string {
  const s = req.settings, type = String(s['type'] ?? 'text');
  const text = (key: string): string => esc(typeof s[key] === 'string' || typeof s[key] === 'number' ? String(s[key]) : '');
  const bound = (key: string, label: string): string => peF(label, `<input type="number" step="1" inputmode="numeric" data-pe-rf="${key}" ${at} value="${text(key)}">`, 'short');
  const choices = (req.choices ?? []).map((c, ci) => `<span class="pe-choice"><input data-pe-rf="choice" data-pe-ci="${ci}" ${at} maxlength="80" value="${esc(c)}" aria-label="Choice ${ci + 1}" autocomplete="off">`
    + `<button type="button" class="ghost" data-pe-choice-x data-pe-ci="${ci}" ${at} aria-label="Remove ${esc(c)}">×</button></span>`).join('');
  return peRow(peF('From', `<select data-pe-rf="person_id" ${at}>${peChoice([...pePeople(), ...peBots()], req.person_id, 'Task owner')}</select>`)
      + peF('Name', `<input data-pe-rf="answer_name" ${at} maxlength="40" pattern="[a-z][a-z0-9_]{0,39}" value="${text('name')}" autocomplete="off" spellcheck="false">`))
    + peF('Question', `<input data-pe-rf="question" ${at} maxlength="500" value="${text('question')}" autocomplete="off">`)
    + peRow(peF('Type', `<select data-pe-rf="answer_type" ${at}>${PE_ANSWER_TYPES.map(([v, l]) => `<option value="${v}"${v === type ? ' selected' : ''}>${l}</option>`).join('')}</select>`)
      + (type === 'number' ? bound('min', 'Min') + bound('max', 'Max') : ''))
    + (type === 'choice' ? `<div class="pe-choices">${choices}<button type="button" class="ghost" data-pe-choice-add ${at}>+ Choice</button>`
      + `<button type="button" class="ghost" data-pe-yes-no ${at}>Yes / No</button></div>` : '');
}

function peReqHTML(state: PeState, req: PipelineReqRec, si: number, ri: number): string {
  const at = `data-pe-si="${si}" data-pe-ri="${ri}"`;
  const needs = peNeeds(req);
  const okKind = PE_OK_KINDS.includes(needs), never = PE_NEVER_PUSHED.includes(needs), answer = needs === 'answer';
  const opt = ([k, name]: [string, string]): string => `<option value="${k}"${k === needs ? ' selected' : ''}>${esc(name)}</option>`;
  const kinds = `<optgroup label="Responses">${PE_KINDS.filter(([k]) => PE_RESPONSES.includes(k)).map(opt).join('')}</optgroup>`
    + `<optgroup label="Links">${PE_KINDS.filter(([k]) => !PE_RESPONSES.includes(k)).map(opt).join('')}</optgroup>`;
  const page = needs === 'page';
  const person = req.person_id ? actorLabel(req.person_id) : '';
  // S11: a page's row says who checks it, "Jev, then Ana"; any other kind names its person.
  const who = page ? (person ? `Jev, then ${person}` : 'Jev') : person;
  return `<details class="pe-req" ${at}${!req.id || state.reqOpen.has(req) ? ' open' : ''}><summary><span class="pe-req-name">${esc(req.line || peKindName(needs))}</span>`
    + `<span class="pe-req-who muted">${esc(who)}</span></summary><div class="pe-req-body"><div class="pe-row">`
    + peF('Needs', `<select data-pe-rf="kind" ${at}${req.id ? ' disabled' : ''}>${kinds}</select>`)
    // A page's line is what Jev checks (S11); an answer's is its title, beside its name; on any other kind it is the
    // requirement's name.
    + peF(page ? 'Jev checks' : answer ? 'Title' : 'Name', `<input data-pe-rf="line" ${at} maxlength="200" value="${esc(req.line)}" placeholder="${esc(peKindName(needs))}" autocomplete="off">`) + '</div>'
    // A page's person is optional: Jev checks, then that person OKs the version Jev passed.
    + (okKind ? '<div class="pe-row">' + peF(page ? 'Then OK from' : 'From', `<select data-pe-rf="person_id" ${at}>${peChoice([...pePeople(), ...peBots()], req.person_id, page ? 'Nobody' : 'Choose')}</select>`)
      + peF('Who OKs', `<select data-pe-rf="who_oks" ${at}>${PE_WHO_OKS.map(([v, l]) => `<option value="${v}"${v === req.who_oks ? ' selected' : ''}>${l}</option>`).join('')}</select>`) + '</div>' : '')
    + (answer ? peAnswerHTML(req, at) : '')
    + (needs === 'in_release' ? peF('Release repo', `<input data-pe-rf="release_repo" ${at} maxlength="200" value="${esc(String(req.settings['release_repo'] ?? ''))}" placeholder="owner/repo" autocomplete="off">`)
      + peF('Release with', `<input data-pe-rf="release_with" ${at} maxlength="120" value="${esc(String(req.settings['release_with'] ?? ''))}" placeholder="workflow:release.yml or tag" autocomplete="off">`) : '')
    + '<div class="pe-req-foot">' + (PE_SWITCHES[needs] ?? []).map(([key, label]) => {
      const on = needs === 'in_release' && key === 'prereleases' ? req.settings[key] !== false : req.settings[key] === true;
      return `<label class="pe-check"><input type="checkbox" role="switch" data-pe-rs="${key}" ${at}${on ? ' checked' : ''}> ${esc(label)}</label>`;
    }).join('')
    + `<label class="pe-check"><input type="checkbox" role="switch" data-pe-rf="can_push_past" ${at}${req.can_push_past && !never ? ' checked' : ''}${never ? ' disabled' : ''}> Can be pushed past</label>`
    + `<button type="button" class="ghost pe-req-x" data-pe-req-x ${at}>Remove</button></div></div></details>`;
}

/** A stage's On enter: nothing, file a linked task into a pipeline, or queue the task's PRs. A pipeline the list lacks
    stays chosen under its id. */
function peOnEnterOptions(state: PeState, stage: PipelineStageRec): string {
  const into = typeof stage.on_enter['file_into'] === 'string' ? stage.on_enter['file_into'] : '';
  const queue = stage.on_enter['act'] === 'github.queue';
  const targets = into && !state.pipelines.some(p => p.id === into) ? [...state.pipelines, {id: into, name: into}] : state.pipelines;
  const opt = (value: string, label: string, on: boolean): string => `<option value="${esc(value)}"${on ? ' selected' : ''}>${esc(label)}</option>`;
  return opt('', 'Nothing', !into && !queue)
    + targets.filter(p => p.id !== state.id || p.id === into).map(p => opt('file_into:' + p.id, 'File into ' + p.name, p.id === into)).join('')
    + opt('act:github.queue', 'Queue PRs', queue);
}

/** A segmented switch for a choice of two or three: [[value, label]], the chosen one aria-checked and the one tab stop
    (the arrow keys move along it, peSegKey). A click sets the stage's `seg` (peClick). */
function peSeg(seg: string, label: string, options: [string, string][], value: string, at: string): string {
  return `<div class="theme-seg pe-seg" role="radiogroup" aria-label="${esc(label)}" data-pe-seg-of="${seg}">`
    + options.map(([v, l]) => `<button type="button" role="radio" data-v="${esc(v)}" aria-checked="${v === value}" tabindex="${v === value ? 0 : -1}" data-pe-seg="${seg}" ${at}>${esc(l)}</button>`).join('')
    + '</div>';
}

const PE_PICK = 'pick';
const PE_PICK_HOW: [string, string][] = [['turns', 'Take turns'], ['random', 'Random']];
const PE_PICK_PREFER: [string, string][] = [['anyone', 'Anyone'], ['new', 'New to the task'], ['family', 'A different model family']];
/** What "One of several" starts as: nobody yet, taking turns. */
const pePickFresh = (): PipelinePickRec => ({bots: [], how: 'turns', limit_each: null, prefer: 'anyone', owns_task: false});
/** A pick as it is stored, without the families the server adds: what a save sends and what is compared. */
const pePickOwn = (pick: PipelinePickRec): PipelinePickRec =>
  ({bots: [...pick.bots], how: pick.how, limit_each: pick.limit_each, prefer: pick.prefer, owns_task: pick.owns_task});

/** An Owner that is "One of several" (spec "A stage picks its owner from several"): the bots to pick from, each with its
    model family, Take turns or Random, Limit each, Prefer, whether the pick becomes the task's owner, and who gets the
    next task in one sentence. The sentence is the server's, for the setting as saved: an unsaved change says so. */
function pePickHTML(state: PeState, stage: PipelineStageRec, at: string): string {
  const pick = stage.pick ?? pePickFresh();
  // Every bot's family, so one not ticked yet shows it too; a stage's own list where the record has no such map.
  const families = {...pick.families, ...state.draft.bot_families};
  const bots = peBots();
  for (const id of pick.bots) if (!bots.some(b => b.id === id)) bots.push({id, name: actorLabel(id) || id});
  const list = bots.map(b => `<label class="pe-check"><input type="checkbox" data-pe-pick-bot="${esc(b.id)}" ${at}${pick.bots.includes(b.id) ? ' checked' : ''}> ${esc(b.name)} `
    + (families[b.id] ? `<span class="pe-family muted">${esc(families[b.id])}</span>` : '') + '</label>').join('');
  return `<div class="pe-pick"><div class="pe-f"><span>Bots</span><div class="pe-pick-bots" role="group" aria-label="Bots">${list}</div></div>`
    + peRow(`<div class="pe-f"><span>Pick</span>${peSeg('pick_how', 'Pick', PE_PICK_HOW, pick.how, at)}</div>`
      + peF('Limit each', `<input type="number" min="1" step="1" inputmode="numeric" data-pe-pick="limit_each" ${at} value="${pick.limit_each ?? ''}" placeholder="None">`, 'short'))
    + `<div class="pe-f"><span>Prefer</span>${peSeg('pick_prefer', 'Prefer', PE_PICK_PREFER, pick.prefer, at)}</div>`
    + `<label class="pe-check"><input type="checkbox" role="switch" data-pe-pick="owns_task" ${at}${pick.owns_task ? ' checked' : ''}> The pick becomes the task's owner</label>`
    + pePickNextHTML(state, stage) + '</div>';
}

/** Who gets the next task: the server's sentence while the pick is as saved; after a change, that a save tells. */
function pePickNextHTML(state: PeState, stage: PipelineStageRec): string {
  const saved = state.base.stages.find(s => s.id && s.id === stage.id);
  const same = !!saved && saved.owner === PE_PICK && !!saved.pick && !!stage.pick && JSON.stringify(pePickOwn(saved.pick)) === JSON.stringify(pePickOwn(stage.pick));
  return same && stage.pick_next ? `<p class="pe-pick-next">${esc(stage.pick_next)}</p>`
    : '<p class="pe-pick-next muted">Save to see who gets the next task.</p>';
}

/** A stage, in the order a task moves (spec "A stage picks its owner from several", 1): its name and purpose, then When
    a task arrives (On enter, Limit), Who works on it (Owner, Expected time) and To leave (how tasks leave, then the
    requirements). */
function peStageHTML(state: PeState, stage: PipelineStageRec, si: number, count: number): string {
  const at = `data-pe-si="${si}"`;
  const ex = peExpected(stage.expected_minutes);
  const owners = [{id: PE_ASSIGNEE, name: 'Whoever it is assigned to'}, {id: PE_PICK, name: 'One of several'}, ...peBots(), ...pePeople()];
  const picks = stage.owner === PE_PICK;
  const body = peRow(peF('Name', `<input data-pe-sf="name" ${at} maxlength="80" value="${esc(stage.name)}" required autocomplete="off">`, 'name')
    + peF('Purpose', `<input data-pe-sf="purpose" ${at} maxlength="300" value="${esc(stage.purpose)}" autocomplete="off">`))
    + `<h4 class="pe-h">When a task arrives</h4>`
    + peRow(peF('On enter', `<select data-pe-sf="on_enter" ${at}>${peOnEnterOptions(state, stage)}</select>`)
      + (si > 0 ? peF('Limit', `<input type="number" min="1" step="1" inputmode="numeric" data-pe-sf="task_limit" ${at} value="${stage.task_limit ?? ''}" placeholder="None">`, 'short') : ''))
    + `<h4 class="pe-h">Who works on it</h4>`
    + peRow(peF('Owner', `<select data-pe-sf="owner" ${at}>${peChoice(owners, stage.owner, 'Not set')}</select>`)
      + `<div class="pe-f pe-f-time"><span>Expected time</span><span class="pe-pair"><input type="number" min="1" step="1" inputmode="numeric" data-pe-sf="expected_n" ${at} value="${ex.n}" placeholder="None" aria-label="Expected time">`
      + `<select data-pe-sf="expected_unit" ${at} aria-label="Unit">${PE_UNITS.map(([l, size]) => `<option value="${size}"${size === ex.unit ? ' selected' : ''}>${l}</option>`).join('')}</select></span></div>`
      // Empty: the pipeline's hours.
      + peF('Keep warm (h)', `<input type="number" min="0" max="24" step="1" inputmode="numeric" data-pe-sf="keep_warm_hours" ${at} value="${stage.keep_warm_hours ?? ''}" placeholder="${state.draft.keep_warm_hours ?? 8}">`, 'short'))
    + (picks ? pePickHTML(state, stage, at) : '')
    + `<h4 class="pe-h">To leave</h4>`
    + `<div class="pe-f"><span>Tasks leave</span>${peSeg('by_hand', 'Tasks leave', [['0', 'When requirements are met'], ['1', 'By hand']], stage.by_hand ? '1' : '0', at)}</div>`
    // A by-hand stage only: whether a task a person files there moves on by itself when the next stage has room.
    + (stage.by_hand ? `<label class="pe-check"><input type="checkbox" role="switch" data-pe-sf="requests_move_on" ${at}${stage.requests_move_on ? ' checked' : ''}> A person's request moves on</label>` : '')
    + stage.requirements.map((req, ri) => peReqHTML(state, req, si, ri)).join('')
    + `<button type="button" class="ghost pe-add" data-pe-req-add ${at}>+ Requirement</button>`;
  // The closed row: its name, who owns it ("One of 2" for a stage that picks), and how many requirements it has to leave
  // (named when pointed at).
  const owner = stage.owner === PE_ASSIGNEE || picks ? '' : stage.owner;
  const reqs = stage.requirements.map(r => r.line || peKindName(r.kind));
  const pool = stage.pick?.bots ?? [];
  const meta = `<span class="pe-stage-meta">`
    + (reqs.length ? `<span class="pe-stage-reqs" title="${esc(reqs.join(', '))}" aria-label="${reqs.length} to leave">✓ ${reqs.length}</span>` : '')
    + (owner ? `<span class="pe-stage-owner">${actorAvatar(owner, 18)}<span>${esc(actorLabel(owner) || owner)}</span></span>` : '')
    + (picks ? `<span class="pe-stage-owner pe-stage-pool" title="${esc(pool.map(b => actorLabel(b) || b).join(', '))}"><span>One of ${pool.length}</span></span>` : '')
    + `</span>`;
  const tools = `<span class="pe-tools"><button type="button" class="ghost" data-pe-up ${at} aria-label="Move up"${si === 0 ? ' disabled' : ''}>↑</button>`
    + `<button type="button" class="ghost" data-pe-down ${at} aria-label="Move down"${si === count - 1 ? ' disabled' : ''}>↓</button>`
    + `<button type="button" class="ghost" data-pe-stage-x ${at}${count < 2 ? ' disabled' : ''}>Remove</button></span>`;
  return `<details class="pe-stage" ${at}${state.open.has(stage) ? ' open' : ''}><summary><span class="pe-stage-name">${esc(stage.name || 'New stage')}</span>${meta}${tools}</summary>`
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
    + peRow(peF('Manager', `<select data-pe-pf="manager_id">${peChoice(peBots(), d.manager_id, 'Not set')}</select>`)
    + (state.factories ? peF('Factory', `<select data-pe-pf="factory_id">${peChoice(factories, d.factory_id, 'None')}</select>`) : '')
    // An empty prefix shows the pipeline's first letter as the example.
    + peF('Prefix', `<input data-pe-pf="prefix" maxlength="2" value="${esc(d.prefix ?? '')}" placeholder="${esc(d.name.trim().charAt(0).toUpperCase())}" autocomplete="off">`, 'short')
    // Hours an active task's session is kept warm after its last work (backend/keep_warm.py); 0 is off.
    + peF('Keep warm (h)', `<input type="number" min="0" max="24" step="1" inputmode="numeric" data-pe-pf="keep_warm_hours" value="${d.keep_warm_hours ?? 8}">`, 'short'));
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
    else if (pf === 'keep_warm_hours') d.keep_warm_hours = peHours(value) ?? 0;
    return;
  }
  const stage = d.stages[peIndex(el, 'peSi')];
  if (!stage) return;
  const sf = el.dataset['peSf'];
  if (sf) {
    if (sf === 'name') stage.name = value;
    else if (sf === 'purpose') stage.purpose = value;
    else if (sf === 'owner') {
      // "One of several" starts with nobody on its list; any other owner has no list (a save sends none).
      const was = stage.owner;
      stage.owner = value || null;
      if (value === PE_PICK) stage.pick = stage.pick ?? pePickFresh();
      else { delete stage.pick; delete stage.pick_next; }
      if ((was === PE_PICK) !== (value === PE_PICK)) peDraw(state);
    }
    else if (sf === 'requests_move_on') stage.requests_move_on = checked;
    else if (sf === 'on_enter') stage.on_enter = peOnEnter(value);
    else if (sf === 'task_limit') stage.task_limit = Number(value) > 0 ? Math.round(Number(value)) : null;
    else if (sf === 'keep_warm_hours') stage.keep_warm_hours = peHours(value);
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
  const bot = el.dataset['pePickBot'], pk = el.dataset['pePick'];
  if (stage.pick && (bot || pk)) {
    const pick = stage.pick;
    if (bot) {
      // The list keeps the order the window shows (peBots), so ticking a bot again never changes whose turn is next.
      const on = new Set(checked ? [...pick.bots, bot] : pick.bots.filter(b => b !== bot));
      const shown = peBots().map(b => b.id);
      pick.bots = [...shown.filter(b => on.has(b)), ...[...on].filter(b => !shown.includes(b))];
    }
    else if (pk === 'limit_each') pick.limit_each = Number(value) > 0 ? Math.round(Number(value)) : null;
    else if (pk === 'owns_task') pick.owns_task = checked;
    peNextSaid(state, stage);
    return;
  }
  const req = stage.requirements[peIndex(el, 'peRi')];
  const rs = el.dataset['peRs'];
  if (req && rs) {
    // Omission enables prereleases; the off position must keep an explicit opt-out.
    const value = peNeeds(req) === 'in_release' && rs === 'prereleases' ? checked : checked || null;
    req.settings = peSetting(req.settings, rs, value);
    return;
  }
  const rf = el.dataset['peRf'];
  if (!req || !rf) return;
  if (rf === 'line') {
    // An answer's name follows its title until it is edited by hand.
    const name = req.kind === 'answer' ? String(req.settings['name'] ?? '') : null;
    if (name !== null && (!name || name === peSlug(req.line))) {
      req.settings = peSetting(req.settings, 'name', peSlug(value) || null);
      const box = el.closest('.pe-req')?.querySelector<HTMLInputElement>('[data-pe-rf="answer_name"]');
      if (box) box.value = peSlug(value);
    }
    req.line = value;
  } else if (rf === 'answer_name') req.settings = peSetting(req.settings, 'name', value.trim() || null);
  else if (rf === 'question') req.settings = peSetting(req.settings, 'question', value.trim() ? value : null);
  else if (rf === 'min' || rf === 'max') {
    // What is not a whole number is sent as typed, for the server to refuse in its words.
    const n = value.trim() === '' ? NaN : Number(value);
    req.settings = peSetting(req.settings, rf, Number.isFinite(n) ? n : null);
  } else if (rf === 'choice') {
    const choices = [...(req.choices ?? [])];
    choices[peIndex(el, 'peCi')] = value;
    req.choices = choices;
  } else if (rf === 'answer_type') {
    let settings = peSetting(req.settings, 'type', value);
    if (value !== 'number') settings = peSetting(peSetting(settings, 'min', null), 'max', null);
    req.settings = settings;
    req.choices = value === 'choice' ? req.choices ?? [] : null;
    peDraw(state);
  }
  else if (rf === 'release_repo' || rf === 'release_with') req.settings = peSetting(req.settings, rf, value.trim() || null);
  else if (rf === 'person_id') req.person_id = value || null;
  else if (rf === 'who_oks') req.who_oks = value as PipelineReqRec['who_oks'];
  else if (rf === 'can_push_past') req.can_push_past = checked;
  else if (rf === 'kind') {
    const github = PE_GITHUB.includes(value);
    req.kind = github ? 'item' : value;
    req.source = github ? 'github' : null;
    req.condition = github ? value : null;
    req.settings = value === 'answer' ? {type: 'text', ...(peSlug(req.line) ? {name: peSlug(req.line)} : {})} : {};
    req.choices = null;
    const ok = PE_OK_KINDS.includes(value);
    req.who_oks = ok ? 'people_with_exceptions' : 'none';
    if (!ok && value !== 'answer') req.person_id = null;
    req.can_push_past = !PE_NEVER_PUSHED.includes(value);
    peDraw(state);
  }
}

/** The On enter choice as the record stores it. */
function peOnEnter(value: string): Record<string, unknown> {
  if (value.startsWith('act:')) return {act: value.slice('act:'.length)};
  if (value.startsWith('file_into:')) return {file_into: value.slice('file_into:'.length)};
  return {};
}

/** A requirement's settings with one set, or dropped when `value` is null (a switch off, an empty field). */
function peSetting(settings: Record<string, unknown>, key: string, value: unknown): Record<string, unknown> {
  const next = {...settings};
  if (value === null) delete next[key]; else next[key] = value;
  return next;
}

/** A pick changed without a redraw (a tick, a number typed): its next-task sentence now waits for a save. */
function peNextSaid(state: PeState, stage: PipelineStageRec): void {
  const si = state.draft.stages.indexOf(stage);
  const said = document.getElementById('pe-body')?.querySelector(`.pe-stage[data-pe-si="${si}"] .pe-pick-next`);
  if (said) said.outerHTML = pePickNextHTML(state, stage);
}

function peClick(state: PeState, target: Element): void {
  const el = target.closest<HTMLElement>('button');
  if (!el) return;
  const d = state.draft, si = peIndex(el, 'peSi'), ri = peIndex(el, 'peRi'), stage = d.stages[si], req = stage?.requirements[ri];
  if (el.hasAttribute('data-pe-save')) { void peSave(state); return; }
  if (el.hasAttribute('data-pe-theirs') && state.clash) {
    state.base = state.clash.theirs; state.draft = peCopy(state.clash.theirs); state.clash = null; state.error = '';
    state.open = new WeakSet(); state.reqOpen = new WeakSet();
    peDraw(state); return;
  }
  if (el.hasAttribute('data-pe-mine') && state.clash) {
    state.base = state.clash.theirs; state.clash = null;
    void peSave(state); return;
  }
  if (el.hasAttribute('data-pe-stage-add')) {
    const fresh: PipelineStageRec = {name: '', purpose: '', owner: null, by_hand: false, requests_move_on: false, task_limit: null, expected_minutes: null,
      expected_bots_only: false, on_enter: {}, requirements: []};
    d.stages.push(fresh);
    state.open.add(fresh);
    peDraw(state);
    document.querySelector<HTMLInputElement>('#pe-body .pe-stage:last-of-type [data-pe-sf="name"]')?.focus();
    return;
  }
  if (!stage) return;
  const seg = el.dataset['peSeg'], v = el.dataset['v'] ?? '';
  if (seg && el.hasAttribute('data-pe-seg')) {
    if (seg === 'by_hand') {
      stage.by_hand = v === '1';
      if (!stage.by_hand) stage.requests_move_on = false;
    } else if (seg === 'pick_how' && stage.pick && (v === 'turns' || v === 'random')) stage.pick.how = v;
    else if (seg === 'pick_prefer' && stage.pick && (v === 'anyone' || v === 'new' || v === 'family')) stage.pick.prefer = v;
    peDraw(state);
    // The redraw replaces the window's content: the keyboard stays on the choice just made.
    document.getElementById('pe-body')?.querySelector<HTMLElement>(`[data-pe-si="${si}"][data-pe-seg="${seg}"][data-v="${v}"]`)?.focus();
    return;
  }
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
    stage.requirements.push({kind: 'owner_done', source: null, condition: null, settings: {}, can_push_past: true, who_oks: 'none', person_id: null, line: '', choices: null});
    peDraw(state);
  } else if (el.hasAttribute('data-pe-req-x')) { stage.requirements.splice(ri, 1); peDraw(state); }
  else if (req && el.hasAttribute('data-pe-yes-no')) { req.choices = [...PE_YES_NO]; peDraw(state); }
  else if (req && el.hasAttribute('data-pe-choice-x')) { req.choices = (req.choices ?? []).filter((_, i) => i !== peIndex(el, 'peCi')); peDraw(state); }
  else if (req && el.hasAttribute('data-pe-choice-add')) {
    req.choices = [...(req.choices ?? []), ''];
    peDraw(state);
    document.querySelector<HTMLInputElement>(`#pe-body [data-pe-si="${si}"][data-pe-ri="${ri}"][data-pe-rf="choice"][data-pe-ci="${req.choices.length - 1}"]`)?.focus();
  }
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
    else if (JSON.stringify(peStageOwn(before)) !== JSON.stringify(peStageOwn(s))) out.push(s.name);
  }
  const kept = new Set(b.stages.map(s => s.id));
  for (const s of a.stages) if (!kept.has(s.id)) out.push(`${s.name} (removed)`);
  if (!out.length && a.stages.map(s => s.id).join() !== b.stages.map(s => s.id).join()) out.push('The order of the stages');
  return out;
}

/** A stage as a person set it: without what the server writes on a read (the next-task sentence, each bot's family),
    which moves on without anyone changing the setting. */
function peStageOwn(stage: PipelineStageRec): PipelineStageRec {
  const {pick_next: _said, pick, ...own} = stage;
  return pick ? {...own, pick: pePickOwn(pick)} : own;
}

/** The draft as it is sent: an answer's empty choices left out, no bot families, and a stage as a person set it (peStageOwn). */
function peOut(draft: PipelineRec): PipelineRec {
  const out = peCopy(draft);
  delete out.bot_families;
  out.stages = out.stages.map(peStageOwn);
  for (const stage of out.stages) for (const req of stage.requirements)
    if (req.choices) req.choices = req.choices.map(c => c.trim()).filter(Boolean);
  return out;
}

async function peSave(state: PeState): Promise<void> {
  if (state.saving) return;
  state.saving = true; state.error = '';
  peDraw(state);
  try {
    const saved = await pipelineSave(state.id, {...peOut(state.draft), settings_version: state.base.settings_version});
    if (PE !== state) return;
    // The editor stays as S11 draws it, now on the saved record: its ids and its new settings_version.
    state.saving = false;
    const draft = peCopy(saved);
    state.open = peOpenSame(state.open, state.draft.stages, draft);
    state.base = saved; state.draft = draft; state.reqOpen = new WeakSet();
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

/** The arrow keys in a segmented switch choose the choice beside the one in focus, as in any radio group. */
function peSegKey(ev: KeyboardEvent): void {
  const on = ev.target instanceof HTMLElement ? ev.target.closest<HTMLElement>('[data-pe-seg]') : null;
  const by = {ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1}[ev.key];
  if (!on || !by) return;
  const all = [...(on.parentElement?.querySelectorAll<HTMLElement>('[data-pe-seg]') ?? [])];
  const next = all[(all.indexOf(on) + by + all.length) % all.length];
  if (!next || next === on) return;
  ev.preventDefault();
  next.click();
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
  // A stage's panel, and a requirement's, stays open or closed across redraws.
  dialog.addEventListener('toggle', ev => {
    const panel = ev.target instanceof HTMLDetailsElement ? ev.target : null;
    const stage = panel && PE ? PE.draft.stages[peIndex(panel, 'peSi')] : undefined;
    if (!PE || !panel || !stage) return;
    if (panel.classList.contains('pe-stage')) { if (panel.open) PE.open.add(stage); else PE.open.delete(stage); return; }
    const req = panel.classList.contains('pe-req') ? stage.requirements[peIndex(panel, 'peRi')] : undefined;
    if (req) { if (panel.open) PE.reqOpen.add(req); else PE.reqOpen.delete(req); }
  }, true);
  const onField = (ev: Event): void => {
    const t = ev.target;
    if (PE && (t instanceof HTMLInputElement || t instanceof HTMLSelectElement)) peInput(PE, t);
  };
  // Typing follows each key; a choice or a switch follows its change.
  const choice = (t: EventTarget | null): boolean => t instanceof HTMLSelectElement || (t instanceof HTMLInputElement && t.type === 'checkbox');
  dialog.addEventListener('input', ev => { if (!choice(ev.target)) onField(ev); });
  dialog.addEventListener('change', ev => { if (choice(ev.target)) onField(ev); });
  dialog.addEventListener('keydown', peSegKey);
  dialog.showModal();
  try {
    const [rec, factories, all] = await Promise.all([
      pipelineRec(pipelineId),
      factoriesRead('7d').then(map => map.factories, () => null),
      pipelinesAll().catch(() => [] as PipelineRec[]),
    ]);
    if (!dialog.isConnected) return;
    const pipelines = all.filter(p => !p.retired_at).map(p => ({id: p.id, name: p.name}));
    const state: PeState = {id: pipelineId, base: rec, draft: peCopy(rec), open: new WeakSet(), reqOpen: new WeakSet(), factories, pipelines, clash: null, error: '', saving: false};
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
