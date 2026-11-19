/* ui/app/pipeline-editor.ts — The pipeline editor (docs/dev/pipelines-v3.md S11): a pipeline's Goal, Manager, Factory and
   Prefix (no Private: pipeline_settings refuses a private pipeline until one is defined), then its stages, one panel
   each, closed until clicked. A stage is edited as its phases, in the order a task moves (style guide, Stage editor):
   its Name and Goal (stored as `purpose`); On entering, one list of what happens as a task enters (its Limit, its
   actions: the owner it changes to and what it opens or queues, its rules: a row each, then + Rule and + Action); While
   here (Late after: the expected time); To leave
   (how tasks leave, and the requirements, always open: Responses, such as done or an OK, and Links, such as a task, a
   page or a PR; each added from + Requirement's list, with a few
   presets, its kind fixed from then on). A rule sits in the phase where it acts, under its fields, with that phase's
   + Rule (stage-rules.ts stageRulesPhaseHTML); the built-in rules are one line at the end. A rule acts at once, in a
   form over the editor, and Save saves the stage's fields alone (its Limit and actions among them). The owner a task
   changes to as it enters (the stage's `owner`: a teammate, the task's assignee, or "One of several", backend/stage_pick.py:
   the bots to pick from, how, a limit each, a preference and whether the pick owns the task) is set by a person; a bot's
   change is refused by the server. With none set, the pipeline's manager is woken to pick one (C25). Done and Closed are not listed: every pipeline ends in them.
   Every stage row but the first has a Side switch: a side stage (`side`) is indented under its stage with ↳, always by
   hand, with no Limit, no Late after and no requirements (their fields are not shown); turning it off leaves by hand on.
   It opens from a stage's header (that stage alone, its fields as the body), its rules mark (the same, at its first
   phase with rules) or
   ⋯ › Edit pipeline, for whoever may change the pipeline; anyone else who opens a stage's rules reads the same view with
   its fields off and no Save. The header (its title) and the footer (+ Stage, Save) stay; the body scrolls.
   The whole record is read (GET /api/v2/pipelines/{id}) and saved at once (POST, with the settings_version it was read
   at). When someone else saved first, the editor says what they changed and lets the person load theirs or save theirs
   over it. Any other refusal shows the server's words. A stage's action on entering opens a linked task in a pipeline
   or adds the task's PRs to the merge queue; a page has Jev's check and its switches, In a release its repo and whether pre-releases count. What the
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
  factories: FactoryRow[] | null;
  /** The pipelines a stage can file into (On enter). */
  pipelines: {id: string; name: string}[];
  /** Someone saved first: what they changed, and their record. */
  clash: {theirs: PipelineRec; changed: string[]} | null;
  error: string;
  saving: boolean;
  /** The stage, by id, the editor was opened for from its header on the board: only that stage is shown (style guide,
   * Pipeline stage: "The name opens that stage's editor, and only that stage"). '' is the whole pipeline. */
  only: string;
  /** The viewer may not change the pipeline (its `may_change` is false): the fields are shown off, with no Save. */
  readOnly: boolean;
  /** The row whose editor is open, as `<stage index>:<key>`: On entering's `limit`, `owner` or `act`, To leave's `req:<index>` or `hand`; '' for none. */
  rowOpen?: string;
  /** A stage removed that holds tasks (the save's 409 stage_has_tasks): the line that says so, where they go
   * (`<pipeline id>/<stage id>`, the server's suggestion first) and the stages of every pipeline to pick from. */
  moveOut?: {line: string; to: string; options: {value: string; text: string}[]} | null;
}

let PE: PeState | null = null;

/** What a stage's requirement can need: a kind, or one of GitHub's conditions (kind `item`, source `github`). A response
    is given by someone (done, an OK); a link is met by other work (a task, a page, a PR). */
const PE_KINDS: [string, string][] = [
  ['owner_done', 'Owner says done'],
  ['ok', 'OK from someone'],
  ['linked_done', 'Linked work done'],
  ['page', 'A page'],
  ['related_input', 'Related input'],
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
/** + Requirement's list, in plain words (Chris, Nov 14 mockup to-leave-requirements): an OK is a person's or a bot's (the
 * same kind `ok`, told apart by who it names); "…" opens the new one's editor to choose. "Someone moves it by hand" is
 * the stage's `by_hand`, shown as one more requirement. */
const PE_REQ_PICKS: [string, string][] = [
  ['by_hand', 'OK from anyone'], ['approval', 'Approval…'], ['ok_bot', 'OK from a bot…'], ['owner_done', 'Owner says done'], ['page', 'A page, checked by Decision model…'],
  ['pr_open', 'PR open'], ['checks_pass', 'Checks pass'], ['pr_merged', 'PR merged'], ['in_release', 'In a release'],
  ['linked_done', 'Linked work done'], ['related_input', 'Related input']];
/** The picks whose new requirement opens its editor (something to choose or name); the rest are added as a row. */
const PE_REQ_CHOOSE = ['approval', 'ok_bot', 'page', 'in_release'];
/** The documents a stage approves (task_requirements.APPROVED): a person approves the version the bot submits by
 * writing it in the stage. */
const PE_APPROVALS = ['Spec', 'QA report', 'Release report', 'Invoice', 'Proposal', 'Results', 'Checked in production'];
/** The document a page requirement approves, or '' for a page attached by hand. */
const peDocument = (req: PipelineReqRec): string =>
  req.kind === 'page' && typeof req.settings['document'] === 'string' ? req.settings['document'] : '';
/** Kinds with nothing of their own to set: one of each is enough, so + Requirement no longer offers one that is there. */
const PE_REQ_ONCE = ['owner_done', 'pr_open', 'checks_pass', 'pr_merged', 'linked_done', 'related_input'];
/** By hand is an OK with nobody named (Chris, Nov 14: named approvers and by hand are the same thing). */
const PE_BY_HAND = 'OK from anyone';
/** The by-hand row's key in rowOpen and on its row (`<stage index>:hand`); a requirement's is `req:<index>`. */
const PE_HAND = 'hand';
const PE_LEAVE_NONE = 'Nothing required. Tasks move on when the next stage has room.';
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
/** Fields side by side on one line; they wrap on a narrow screen. */
const peRow = (fields: string): string => `<div class="pe-row">${fields}</div>`;

/** To leave is one plain list of requirements (Chris, Nov 14 mockup to-leave-requirements): one line each in words
 * ("PR open", "OK from Chris" with a face, "Page: Spec page"), the requirement mark first, a lock at the right only on
 * one that can't be pushed past. A tap opens its small editor in place, as On entering's rows: only what its kind has
 * (who OKs it, a page's settings, a release's repo), Can be pushed past, and Remove. Its kind is picked
 * when it is added (+ Requirement), never changed. "Someone moves it by hand" (the stage's by_hand) is its last row. */

/** Who OKs an OK: the people or bots it names. One today (a requirement's person_id); several is the proposed backend
 * change, the words below already read "OK from Chris or Ana". */
const peApprovers = (req: PipelineReqRec): string[] => req.person_id ? [req.person_id] : [];
/** "OK from Chris", "OK from Chris or Ana", "OK from Chris, Ana or Bo". */
function peOkWords(who: string[]): string {
  const names = who.map(w => actorLabel(w) || w);
  return `OK from ${names.length > 1 ? `${names.slice(0, -1).join(', ')} or ${names[names.length - 1]}` : names[0] ?? ''}`;
}
/** An OK from a bot (it names a bot); one naming nobody, or a person, is a person's. */
const peOkByBot = (req: PipelineReqRec): boolean => peNeeds(req) === 'ok' && !!req.person_id?.startsWith('bot:');
/** A requirement that can never be pushed past: by its kind (C3), or switched off. */
const peLocked = (req: PipelineReqRec): boolean => PE_NEVER_PUSHED.includes(peNeeds(req)) || !req.can_push_past;

/** A requirement in words, as its row says it. An approval names its document ("Approve Spec"), its approver as a face
 * after it. An OK naming nobody says what it asks, else who may give it. */
function peReqText(req: PipelineReqRec): string {
  const needs = peNeeds(req), line = req.line.trim(), document = peDocument(req);
  if (document) return `Approve ${document}`;
  if (needs === 'ok') {
    const who = peApprovers(req);
    return who.length ? peOkWords(who) : line || (req.who_oks === 'people_only' ? 'OK from a person' : 'OK from someone');
  }
  if (needs === 'page') return line ? `Page: ${line}` : 'A page, checked by Decision model';
  return line ? `${peKindName(needs)}: ${line}` : peKindName(needs);
}

/** A requirement's small editor, under its row: who OKs it (an OK: one person or one bot; a page: who OKs after the
 * decision model; an approval: its document and approver), its name where its kind has one (an OK's ask, a page's name),
 * its kind's settings, its switches (Can be pushed past last, off and fixed on a kind that never is), and Remove. */
function peReqEdHTML(state: PeState, req: PipelineReqRec, si: number, ri: number, id: string): string {
  const at = `data-pe-si="${si}" data-pe-ri="${ri}"`;
  const needs = peNeeds(req), never = PE_NEVER_PUSHED.includes(needs), page = needs === 'page';
  const line = (label: string, placeholder: string): string =>
    peF(label, `<input data-pe-rf="line" ${at} maxlength="200" value="${esc(req.line)}" placeholder="${esc(placeholder)}" autocomplete="off">`);
  const whoOks = peF('Who OKs', `<select data-pe-rf="who_oks" ${at}>${PE_WHO_OKS.map(([v, l]) => `<option value="${v}"${v === req.who_oks ? ' selected' : ''}>${l}</option>`).join('')}</select>`);
  let fields = '';
  const document = peDocument(req);
  if (document) {
    // the document and who approves it: any person, or one named person; the decision model checks it first
    const docs = PE_APPROVALS.map(d => `<option value="${esc(d)}"${d === document ? ' selected' : ''}>${esc(d)}</option>`).join('');
    fields = peRow(peF('Document', `<select data-pe-rf="document" ${at}>${docs}</select>`)
      + peF('Approver', `<select data-pe-rf="approver" ${at}>${peChoice(pePeople(), req.person_id, 'Any person')}</select>`));
  } else if (needs === 'ok') {
    // one person (or "Any person": nobody named) or one bot; a bot's OK names a bot, so it has no empty choice
    const bot = peOkByBot(req);
    const from = bot ? peChoice(peBots(), req.person_id, '').replace(/^<option value="">.*?<\/option>/, '') : peChoice(pePeople(), req.person_id, 'Any person');
    fields = peF('From', `<select data-pe-rf="person_id" ${at}>${from}</select>`)
      // several named people, the first to OK meets it: not on the server yet
      + (bot ? '' : `<button type="button" class="ghost pe-add pe-soon-b" disabled>+ Person${PE_SOON}</button>` + whoOks) + line('Asks', 'OK');
  } else if (page) {
    // a page's person is optional: Jev checks, then that person OKs the version Jev passed
    fields = line('Page', 'Review page')
      + peF('Then OK from', `<select data-pe-rf="person_id" ${at}>${peChoice([...pePeople(), ...peBots()], req.person_id, 'Nobody')}</select>`) + whoOks;
  } else if (needs === 'in_release') {
    fields = peF('Release repo', `<input data-pe-rf="release_repo" ${at} maxlength="200" value="${esc(String(req.settings['release_repo'] ?? ''))}" placeholder="owner/repo" autocomplete="off">`)
      + peF('Release with', `<input data-pe-rf="release_with" ${at} maxlength="120" value="${esc(String(req.settings['release_with'] ?? ''))}" placeholder="workflow:release.yml or tag" autocomplete="off">`);
  }
  const switches = (document ? [] : PE_SWITCHES[needs] ?? []).map(([key, label]) => {
    const on = needs === 'in_release' && key === 'prereleases' ? req.settings[key] !== false : req.settings[key] === true;
    return `<label class="pe-check"><input type="checkbox" role="switch" data-pe-rs="${key}" ${at}${on ? ' checked' : ''}> ${esc(label)}</label>`;
  }).join('') + `<label class="pe-check"><input type="checkbox" role="switch" data-pe-rf="can_push_past" ${at}${req.can_push_past && !never ? ' checked' : ''}${never ? ' disabled' : ''}> Can be pushed past</label>`;
  return `<div class="pe-row-ed pe-req-ed" id="${esc(id)}">${fields}<div class="pe-req-sw">${switches}</div>`
    + (state.readOnly ? '' : `<button type="button" class="ghost pe-row-x" data-pe-row-x="req:${ri}" ${at}>Remove</button>`) + '</div>';
}

/** One row of To leave (a requirement's, `req:<index>`, or by hand's), On entering's row look (stage-rules.ts
 * stageRuleRowHTML): the requirement mark, its words, an OK's faces, the lock; its editor under it when open. */
function peLeaveRowHTML(state: PeState, stage: PipelineStageRec, si: number, sid: string, key: string): string {
  const at = `data-pe-si="${si}"`, slug = `${sid}-${key.replace(':', '-')}`;
  const id = `pe-ed-${slug}`, open = !state.readOnly && state.rowOpen === `${si}:${key}`;
  const how = state.readOnly ? 'disabled' : `data-pe-row="${key}" ${at} aria-expanded="${open}" aria-controls="${esc(id)}"`;
  const base = {key: slug, attrs: `data-pe-leave-row="${key}" ${at}`, icon: SR_ROW_ICONS.req, open: how};
  if (key === PE_HAND) {
    return stageRuleRowHTML({...base, text: PE_BY_HAND, below: open ? `<div class="pe-row-ed" id="${esc(id)}">`
      // a person's request filed into a by-hand stage moves on by itself when the next stage has room
      + `<label class="pe-check"><input type="checkbox" role="switch" data-pe-sf="requests_move_on" ${at}${stage.requests_move_on ? ' checked' : ''}> A person's request moves on</label>`
      + `<button type="button" class="ghost pe-row-x" data-pe-row-x="${PE_HAND}" ${at}>Remove</button></div>` : ''});
  }
  const ri = Number(key.slice('req:'.length)), req = stage.requirements[ri];
  if (!req) return '';
  const who = PE_OK_KINDS.includes(peNeeds(req)) ? peApprovers(req) : [];
  return stageRuleRowHTML({...base, text: peReqText(req), below: open ? peReqEdHTML(state, req, si, ri, id) : '',
    after: who.length ? `<span class="pe-faces" aria-hidden="true">${who.map(w => actorAvatar(w, 18)).join('')}</span>` : '',
    marks: peLocked(req) ? srMarkHTML('pe-m-lock', SR_ROW_ICONS.lock, "Can't be pushed past", 'data-pe-lock') : ''});
}
/** To leave's list: a row per requirement, the by-hand row last, then + Requirement; with none, the one line that says
 * what then happens (task_engine.advance: the task moves when the next stage has room). Read only, nothing opens. */
function peLeaveHTML(state: PeState, stage: PipelineStageRec, si: number, sid: string): string {
  // a side stage is left by hand alone, with nothing to require: its rules only
  if (stage.side) return `<div class="pe-list">${stageRulesPhaseHTML(state.id, stage, 'leave')}</div>`;
  const keys = [...stage.requirements.map((_, ri) => `req:${ri}`), ...(stage.by_hand ? [PE_HAND] : [])];
  const rows = keys.map(k => peLeaveRowHTML(state, stage, si, sid, k)).join('');
  return `<div class="pe-list">${rows ? `<ul class="sr-list pe-leave-list">${rows}</ul>` : `<p class="pe-none muted">${esc(PE_LEAVE_NONE)}</p>`}`
    + (state.readOnly ? '' : `<div class="pe-adds"><button type="button" class="ghost pe-add" data-pe-req-add data-pe-si="${si}" aria-haspopup="menu" aria-expanded="false">+ Requirement</button></div>`)
    // the rules that act as a task leaves follow it, as each phase's rules follow its fields
    + stageRulesPhaseHTML(state.id, stage, 'leave') + '</div>';
}
/** A field of a requirement changed without a redraw: its row says it at once (words, faces, lock), its editor kept. */
function peLeaveSay(state: PeState, si: number, ri: number): void {
  const stage = state.draft.stages[si];
  const btn = document.querySelector<HTMLElement>(`#pe-body [data-pe-leave-row="req:${ri}"][data-pe-si="${si}"] > .sr-line > .sr-open`);
  if (!stage || !btn) return;
  const made = document.createElement('template');
  made.innerHTML = peLeaveRowHTML({...state, rowOpen: ''}, stage, si, '-', `req:${ri}`);
  const fresh = made.content.querySelector('.sr-open');
  if (!fresh) return;
  // the row button keeps its own attributes (its editor's id, aria-expanded): only what it says changes, and the marks
  btn.innerHTML = fresh.innerHTML;
  btn.title = fresh.getAttribute('title') ?? '';
  btn.parentElement?.querySelector(':scope > .sr-marks')?.remove();
  const marks = made.content.querySelector('.sr-marks');
  if (marks) btn.after(marks);
}

/** A new requirement of a kind (or a GitHub condition), as + Requirement adds it: its own settings, who OKs it, and
 * whether it can be pushed past. An approval starts on the Spec, from any person; a bot's OK on the pipeline's manager,
 * else the first bot. */
function peReqNew(state: PeState, pick: string): PipelineReqRec {
  if (pick === 'approval') {
    const document = PE_APPROVALS[0] ?? 'Spec';
    return {kind: 'page', source: null, condition: null, settings: {document}, can_push_past: true, who_oks: 'people_only',
      person_id: null, line: document};
  }
  const kind = pick === 'ok_bot' ? 'ok' : pick;
  const github = PE_GITHUB.includes(kind), ok = PE_OK_KINDS.includes(kind);
  const person = pick === 'ok_bot' ? (state.draft.manager_id ? prActor(state.draft.manager_id) : peBots()[0]?.id ?? null) : null;
  return {kind: github ? 'item' : kind, source: github ? 'github' : null, condition: github ? kind : null,
    settings: {}, can_push_past: !PE_NEVER_PUSHED.includes(kind), who_oks: ok ? 'people_with_exceptions' : 'none', person_id: person, line: ''};
}
/** What + Requirement offers this stage now: every kind, less one of the plain kinds it has already and by hand when it
 * is set. A bot's OK only where there is a bot. */
function peReqPicks(stage: PipelineStageRec): [string, string][] {
  const have = new Set(stage.requirements.map(peNeeds));
  return PE_REQ_PICKS.filter(([k]) => !(PE_REQ_ONCE.includes(k) && have.has(k)) && !(k === 'by_hand' && stage.by_hand)
    && !(k === 'ok_bot' && !peBots().length));
}
/** + Requirement's pick list, beside the button (the shared menu look, .tl-pop), one list in plain words. Arrow keys
 * move, Esc closes it and the button has the focus back; a second click on the button closes it; on a phone it is a
 * sheet of 44 px rows. */
function peReqPickOpen(state: PeState, button: HTMLElement): void {
  const si = peIndex(button, 'peSi'), stage = state.draft.stages[si];
  if (!stage) return;
  pePickOpen(button, 'pe-req-pick', 'data-pe-req-add', `<div class="tl-menu" role="menu" aria-label="Add a requirement">`
    + peReqPicks(stage).map(([k, l]) => `<button type="button" class="tl-mi" role="menuitem" data-pe-req-kind="${k}" data-pe-si="${si}">${esc(l)}</button>`).join('')
    + peSoonItem('A question Decision model answers…') + peSoonItem('In your own words…') + '</div>');
}
/** A stage's add button's pick list (+ Requirement, On entering's + Rule and + Action), beside it: the popover `id` in the dialog, made
 * once, with the menu's HTML. A second click on the button (`attr`) closes it; closed, the button has the focus back. */
function pePickOpen(button: HTMLElement, id: string, attr: string, menu: string): void {
  const dialog = button.closest('dialog');
  if (!dialog) return;
  const si = button.dataset['peSi'] ?? '';
  let pop = dialog.querySelector<HTMLElement>(`:scope > #${id}`);
  // a second click on the button closes the list (its press may have closed it already: light dismiss)
  if (popClosedByPress(pop, si)) { if (pop?.matches(':popover-open')) pop.hidePopover(); return; }
  if (pop?.matches(':popover-open')) { pop.hidePopover(); return; }
  if (!pop) {
    const made = document.createElement('div');
    made.id = id; made.className = 'tl-pop pe-req-pop'; made.popover = 'auto';
    made.addEventListener('keydown', tasksMenuKeys);
    made.addEventListener('toggle', ev => {
      if ((ev as Event & {newState?: string}).newState !== 'closed') return;
      const from = dialog.querySelector<HTMLElement>(`[${attr}][data-pe-si="${CSS.escape(made.dataset['si'] ?? '')}"]`);
      from?.setAttribute('aria-expanded', 'false');
      if (from && (!document.activeElement || document.activeElement === document.body || made.contains(document.activeElement))) from.focus();
    });
    dialog.append(made);
    pop = made;
  }
  pop.dataset['si'] = si; pop.dataset['for'] = si;
  pop.innerHTML = menu;
  pop.showPopover();
  button.setAttribute('aria-expanded', 'true');
  popPlace(pop, button);
  pop.querySelector<HTMLElement>('.tl-mi')?.focus();
}

/** On entering is one list (Chris, Nov 14: "on entering rules and actions … limit is just another rule"): the Limit,
    the actions (the owner it changes to: "tasks have owners … changing the owner is just another action that can occur
    when they enter a stage"; then what it opens or queues), then the rules that act as a task enters, one row each
    (stage-rules.ts stageRuleRowHTML), then + Rule (a rule, or the Limit: Chris, "limit is just another rule") and
    + Action. Stored as before: the stage's task_limit, its owner (and pick),
    and its on_enter {}, {file_into: id} or {act: 'github.queue'}. The first stage has no Limit (tasks start there). */
const PE_THEN_INTO = 'Open a linked task in…', PE_THEN_QUEUE = 'Add its PRs to the merge queue', PE_THEN_OWNER = 'Change the owner…';
/** The owner a task changes to as it enters, as its row says it; null for none set. One of several names its first two
    bots, how many more, and how it picks. */
function peOwnerOf(stage: PipelineStageRec): {text: string} | null {
  if (!stage.owner) return null;
  if (stage.owner === PE_ASSIGNEE) return {text: "Change the owner to the task's assignee"};
  if (stage.owner === PE_PICK) {
    const bots = (stage.pick?.bots ?? []).map(b => actorLabel(b) || b);
    const names = bots.length ? `: ${bots.slice(0, 2).join(', ')}${bots.length > 2 ? ` +${bots.length - 2}` : ''}` : '';
    const how = stage.pick?.how === 'random' ? 'at random' : 'takes turns';
    return {text: `Change the owner to one of several${names} · ${how}`};
  }
  return {text: `Change the owner to ${actorLabel(stage.owner) || stage.owner}`};
}
/** Who a stage's owner can be: the task's assignee, One of several, a bot, a person. */
const peOwners = (): {id: string; name: string}[] =>
  [{id: PE_ASSIGNEE, name: "The task's assignee"}, {id: PE_PICK, name: 'One of several'}, ...peBots(), ...pePeople()];
/** A Limit the add button starts with; its editor opens, the number chosen, to type over. */
const PE_LIMIT_NEW = 3;
const peLimitText = (n: number | null): string => n ? `At most ${n} task${n === 1 ? '' : 's'} at once` : 'No limit';
/** The stage's action on entering, as a row says it; null for none. A pipeline the list lacks is named by its id; an
    action this editor has no words for by its name. */
function peActOf(state: PeState, stage: PipelineStageRec): {kind: string; text: string} | null {
  const into = stage.on_enter['file_into'], act = stage.on_enter['act'];
  if (typeof into === 'string') {
    const name = into ? peIntoTargets(state, into).find(p => p.id === into)?.name ?? into : '';
    return {kind: 'file_into', text: name ? `Open a linked task in ${name}` : PE_THEN_INTO};
  }
  if (act === 'github.queue') return {kind: 'queue', text: PE_THEN_QUEUE};
  return typeof act === 'string' && act ? {kind: 'act', text: act} : null;
}
/** A row's small editor, opened in place under it: the Limit's number; the Owner, with One of several's controls under
    it; or the action and its Pipeline. Remove last on the first line. */
function peEnterEdHTML(state: PeState, stage: PipelineStageRec, row: 'limit' | 'owner' | 'act', at: string, id: string): string {
  const opt = (value: string, label: string, on: boolean): string => `<option value="${esc(value)}"${on ? ' selected' : ''}>${esc(label)}</option>`;
  const x = `<button type="button" class="ghost pe-row-x" data-pe-row-x="${row}" ${at}>Remove</button>`;
  if (row === 'limit') return `<div class="pe-row-ed" id="${esc(id)}">${peF('Tasks at once', `<input type="number" min="1" step="1" inputmode="numeric" data-pe-sf="task_limit" ${at} value="${stage.task_limit ?? ''}">`, 'short')}${x}</div>`;
  // no empty choice: none set is no row (Remove)
  // a side stage has one owner or none: no One of several (pipeline_settings refuses a pick there)
  const owners = peChoice(peOwners().filter(o => !stage.side || o.id !== PE_PICK), stage.owner, '').replace(/^<option value="">.*?<\/option>/, '');
  if (row === 'owner') return `<div class="pe-row-ed" id="${esc(id)}">${peF('Owner', `<select data-pe-sf="owner" ${at}>${owners}</select>`)}${x}`
    + (stage.owner === PE_PICK ? pePickHTML(state, stage, at) : '') + '</div>';
  const into = typeof stage.on_enter['file_into'] === 'string' ? stage.on_enter['file_into'] : null;
  const queue = stage.on_enter['act'] === 'github.queue';
  // Open a linked task in… only where there is another pipeline to open it in (or it is the action already)
  const intoOk = into !== null || peIntoTargets(state, '').length > 0;
  const kind = `<select data-pe-sf="on_enter_kind" ${at}>${(intoOk ? opt('file_into', PE_THEN_INTO, into !== null) : '') + opt('act:github.queue', PE_THEN_QUEUE, queue)}</select>`;
  const pipe = into !== null ? peF('Pipeline', `<select data-pe-sf="on_enter" ${at}>${peIntoTargets(state, into).map(p => opt('file_into:' + p.id, p.name, p.id === into)).join('')}</select>`) : '';
  return `<div class="pe-row-ed" id="${esc(id)}">${peF('Then', kind)}${pipe}${x}</div>`;
}
/** What + Rule and + Action can add to a stage now: a Rule (where the viewer may add one, on a saved stage), a Limit (none
    set, not the first stage), a change of owner (none set) and an action (none set; the stage stores one). Read only,
    rules alone. */
function peEnterPicks(state: PeState, stage: PipelineStageRec, si: number): [string, string][] {
  const ed = SR_ED, out: [string, string][] = [];
  if (stage.id && ed?.pipelineId === state.id && ed.list?.may_add) out.push(['rule', "The owner's team…"]);
  if (state.readOnly) return out;
  // a side stage has no Limit, and opens or queues nothing (its owner it may change)
  if (si > 0 && !stage.task_limit && !stage.side) out.push(['limit', 'Limit']);
  if (!stage.owner) out.push(['owner', PE_THEN_OWNER]);
  if (!peActOf(state, stage) && !stage.side) {
    if (peIntoTargets(state, '').length) out.push(['file_into', PE_THEN_INTO]);
    out.push(['queue', PE_THEN_QUEUE]);
  }
  return out;
}
/** Which add button offers a pick: + Rule a Rule and the Limit (a limit is a rule too), + Action the actions. */
const PE_RULE_PICKS = ['rule', 'limit'];
const peEnterGroup = (picks: [string, string][], add: string): [string, string][] =>
  picks.filter(([k]) => PE_RULE_PICKS.includes(k) === (add === 'rule'));
/** On entering's list: the Limit row, the action rows (the owner, then what it opens or queues), the rules
    (stage-rules.ts), then + Rule and + Action side by side, each there while it has something to add. A row opens its
    editor in place; read only, the rows are shown with nothing to open. */
function peEnterHTML(state: PeState, stage: PipelineStageRec, si: number, sid: string): string {
  const at = `data-pe-si="${si}"`;
  const row = (kind: 'limit' | 'owner' | 'act', icon: string, text: string): string => {
    const id = `pe-ed-${sid}-${kind}`, open = !state.readOnly && state.rowOpen === `${si}:${kind}`;
    return stageRuleRowHTML({key: `${sid}-${kind}`, attrs: `data-pe-enter-row="${kind}" ${at}`, icon, text,
      open: state.readOnly ? 'disabled' : `data-pe-row="${kind}" ${at} aria-expanded="${open}" aria-controls="${esc(id)}"`,
      below: open ? peEnterEdHTML(state, stage, kind, at, id) : ''});
  };
  // the rules first (the Limit, then the rules that act as a task enters), then the actions: one mark for each kind
  const act = peActOf(state, stage), owner = peOwnerOf(stage);
  const limit = si > 0 && stage.task_limit && !stage.side ? row('limit', SR_ROW_ICONS.rule, peLimitText(stage.task_limit)) : '';
  const acts = (owner ? row('owner', SR_ROW_ICONS.act, owner.text) : '') + (act ? row('act', SR_ROW_ICONS.act, act.text) : '');
  const picks = peEnterPicks(state, stage, si);
  // + Rule goes straight to a new rule when that is all it offers; with a Limit to add too, it lists both
  const add = (kind: string, words: string): string => {
    const of = peEnterGroup(picks, kind);
    if (!of.length) return '';
    const menu = of.length > 1 || of[0]![0] !== 'rule' ? ' aria-haspopup="menu" aria-expanded="false"' : '';
    return `<button type="button" class="ghost pe-add" data-pe-enter-add="${kind}" ${at}${menu}>${words}</button>`;
  };
  const adds = add('rule', '+ Rule') + add('act', '+ Action');
  return `<div class="pe-list">${limit ? `<ul class="sr-list pe-enter-list">${limit}</ul>` : ''}${stageRulesPhaseHTML(state.id, stage, 'enter')}`
    + (acts ? `<ul class="sr-list pe-enter-list pe-acts">${acts}</ul>` : '')
    // what the server does with no owner set (task_engine.owner_for): the stage has nobody and the manager is woken to pick
    + (owner ? '' : '<p class="pe-none muted" data-pe-no-owner>No owner set: the manager picks one.</p>') + (adds ? `<div class="pe-adds">${adds}</div>` : '') + '</div>';
}
/** + Rule's or + Action's pick list (as + Requirement's): only what the stage can take now (peEnterPicks). */
function peEnterPickOpen(state: PeState, button: HTMLElement): void {
  const si = peIndex(button, 'peSi'), stage = state.draft.stages[si], kind = button.dataset['peEnterAdd'] ?? '';
  if (!stage) return;
  const items = peEnterGroup(peEnterPicks(state, stage, si), kind)
    .map(([k, l]) => `<button type="button" class="tl-mi" role="menuitem" data-pe-enter-pick="${k}" data-pe-si="${si}">${esc(l)}</button>`);
  pePickOpen(button, `pe-${kind}-pick`, `data-pe-enter-add="${kind}"`,
    `<div class="tl-menu" role="menu" aria-label="${kind === 'rule' ? 'Add a rule' : 'Add an action'}">${items.join('')}${kind === 'rule' ? peSoonItem('A question Decision model answers…') + peSoonItem('In your own words…') : ''}</div>`);
}
/** A Limit typed, an owner or a bot of several picked, or a Pipeline picked: its row says it at once (the editor stays
    open; a redraw would take the focus). */
function peEnterSay(state: PeState, si: number, kind: 'limit' | 'owner' | 'act'): void {
  const stage = state.draft.stages[si];
  const btn = document.querySelector<HTMLElement>(`#pe-body [data-pe-enter-row="${kind}"][data-pe-si="${si}"] .sr-open`);
  if (!stage || !btn) return;
  const text = kind === 'limit' ? peLimitText(stage.task_limit) : (kind === 'owner' ? peOwnerOf(stage) : peActOf(state, stage))?.text ?? '';
  btn.title = text;
  const said = btn.querySelector('.sr-text');
  if (said) said.textContent = text;
}
/** The pipelines a stage may open a linked task in: every other one, and the one chosen (one the list lacks, by id). */
function peIntoTargets(state: PeState, into: string): {id: string; name: string}[] {
  const all = into && !state.pipelines.some(p => p.id === into) ? [...state.pipelines, {id: into, name: into}] : state.pipelines;
  return all.filter(p => p.id !== state.id || p.id === into);
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

/** A stage's phases (style guide, Stage editor): a heading with a small leading mark, then what happens there. Icons
    from the subset (ui/vendor/fonts/icons.txt): On entering's login pairs with To leave's logout. */
const PE_PHASE_ICONS: Record<SrPhase, [string, string]> = {enter: ['login', 'On entering'], here: ['schedule', 'While here'], leave: ['logout', 'To leave'], more: ['gavel', 'More rules']};

/** "Soon": a small quiet pill beside something drawn as it will be but not working yet (Chris, Nov 14). */
const PE_SOON = '<span class="pill soon">Soon</span>';
/** A pick list's row for something not ready: shown, off, with Soon. */
const peSoonItem = (label: string): string => `<button type="button" class="tl-mi pe-mi-soon" role="menuitem" aria-disabled="true" disabled>${esc(label)}${PE_SOON}</button>`;

/** Stages whose More rules is open (by its id in the page): kept across the editor's redraws. */
const PE_MORE_OPEN = new Set<string>();
/** More rules: one shut line at the end of a stage that opens the rules that act on neither entering nor a timer (a
    bot's snooze, + Rule) and, after them, the built-in ones (read only). */
function peMoreHTML(state: PeState, stage: PipelineStageRec, sid: string): string {
  if (!stage.id) return '';
  const [icon, label] = PE_PHASE_ICONS.more, on = PE_MORE_OPEN.has(sid), body = `pe-more-${sid}`;
  return `<section class="pe-phase pe-more" data-pe-phase="more">`
    + `<button type="button" class="pe-h pe-phase-h pe-more-h" id="pe-ph-${esc(sid)}-more" data-pe-more="${esc(sid)}" aria-expanded="${on}" aria-controls="${esc(body)}">`
    + `<span class="nav-icon" aria-hidden="true">${icon}</span>${label}<span class="sr-cx" aria-hidden="true">›</span></button>`
    + `<div class="pe-more-body" id="${esc(body)}"${on ? '' : ' hidden'}>${stageRulesPhaseHTML(state.id, stage, 'more')}${stageRulesPhaseHTML(state.id, stage, 'always')}</div></section>`;
}
/** A click on More rules opens or shuts it. True when the click was its. */
function peMoreClick(ev: Event): boolean {
  const btn = eventElement(ev)?.closest<HTMLElement>('[data-pe-more]');
  if (!btn) return false;
  const sid = btn.dataset['peMore'] ?? '', on = !PE_MORE_OPEN.has(sid), body = document.getElementById(btn.getAttribute('aria-controls') ?? '');
  if (on) PE_MORE_OPEN.add(sid); else PE_MORE_OPEN.delete(sid);
  btn.setAttribute('aria-expanded', String(on));
  if (body) body.hidden = !on;
  return true;
}

/** A stage's Side switch: on, the stage is a side stage of the stage before it (peInput sets what that means). Off, it
    waits while the stage opens or queues something as a task enters, in the draft or as saved (pipeline_settings refuses
    Side on a stage with a stored action): the action is removed and saved first. */
function peSideHTML(state: PeState, stage: PipelineStageRec, si: number): string {
  const saved = stage.id ? state.base.stages.find(s => s.id === stage.id) : undefined;
  const acts = !stage.side && (!!peActOf(state, stage) || (!!saved && !!peActOf(state, saved)));
  return `<label class="pe-check pe-side"${acts ? ' title="Remove the action first"' : ''}><input type="checkbox" role="switch" data-pe-sf="side" data-pe-si="${si}"`
    + ` aria-label="Side stage: ${esc(stage.name || 'New stage')}"${stage.side ? ' checked' : ''}${state.readOnly || acts ? ' disabled' : ''}> Side</label>`;
}

/** A stage, as its phases in the order a task moves (spec "A stage picks its owner from several", 1): its name and goal;
    On entering, one list (its Limit, its actions, the rules that act as a task enters), + Rule and + Action; While here
    (Late after: the stage's expected time) and the rules that act while it is there; To leave (how tasks leave, then the
    requirements); then the built-in rules in one line. Read only, the fields are off and nothing is added or removed;
    the rules still open to be read. */
function peStageHTML(state: PeState, stage: PipelineStageRec, si: number, count: number): string {
  const at = `data-pe-si="${si}"`;
  const ex = peExpected(stage.expected_minutes);
  const picks = stage.owner === PE_PICK;
  // the fields are off for a viewer who may not change the pipeline; the rules beside them are not (they open to read)
  const ro = (html: string): string => state.readOnly ? `<fieldset class="pe-ro" disabled>${html}</fieldset>` : html;
  const sid = stage.id ? `${state.id}-${stage.id}` : `${state.id}-n${si}`;
  // Side: every stage but the first (a first stage that is one still shows it, to turn it off)
  const side = si > 0 || stage.side ? peSideHTML(state, stage, si) : '';
  // a phase: its heading, then its fields and its rules, or (On entering) its one list
  const phase = (key: SrPhase, fields: string, list = ''): string => {
    const [icon, label] = PE_PHASE_ICONS[key];
    return `<section class="pe-phase" data-pe-phase="${key}" aria-labelledby="pe-ph-${esc(sid)}-${key}">`
      + `<h4 class="pe-h pe-phase-h" id="pe-ph-${esc(sid)}-${key}" tabindex="-1"><span class="nav-icon" aria-hidden="true">${icon}</span>${label}</h4>`
      + (list || ro(fields) + stageRulesPhaseHTML(state.id, stage, key)) + '</section>';
  };
  const inner = ro(peRow(peF('Name', `<input data-pe-sf="name" ${at} maxlength="80" value="${esc(stage.name)}" required autocomplete="off">`, 'name')
      // the stage's goal, stored as `purpose`
      + peF('Goal', `<input data-pe-sf="purpose" ${at} maxlength="300" value="${esc(stage.purpose)}" autocomplete="off">`)
      // beside its Name and Goal too: an open row's Side is here, its row holding the stage's tools (one stage's editor
      // has no row at all)
      + side))
    + phase('enter', '', peEnterHTML(state, stage, si, sid))
    // a side stage has no Late after: its rules only
    + phase('here', stage.side ? '' : peRow(`<div class="pe-f pe-f-time"><span>Late after</span><span class="pe-pair"><input type="number" min="1" step="1" inputmode="numeric" data-pe-sf="expected_n" ${at} value="${ex.n}" placeholder="Never" aria-label="Late after">`
      + `<select data-pe-sf="expected_unit" ${at} aria-label="Unit">${PE_UNITS.map(([l, size]) => `<option value="${size}"${size === ex.unit ? ' selected' : ''}>${l}</option>`).join('')}</select></span></div>`))
    + phase('leave', '', peLeaveHTML(state, stage, si, sid))
    // rules that answer something else than entering or a timer (a bot's snooze): their When is this place
    + peMoreHTML(state, stage, sid);
  // One stage's editor (its name is the dialog's title): the stage's fields are the body, nothing to collapse or move.
  if (state.only && stage.id === state.only) return `<div class="pe-stage pe-stage-one" ${at}><div class="pe-stage-body">${inner}</div></div>`;
  // The closed row: its name, who owns it ("One of 2" for a stage that picks), and how many requirements it has to leave
  // (named when pointed at).
  const owner = stage.owner === PE_ASSIGNEE || picks ? '' : stage.owner;
  const reqs = stage.side ? [] : [...stage.requirements.map(peReqText), ...(stage.by_hand ? [PE_BY_HAND] : [])];
  const pool = stage.pick?.bots ?? [];
  const meta = `<span class="pe-stage-meta">`
    + (reqs.length ? `<span class="pe-stage-reqs" title="${esc(reqs.join(', '))}" aria-label="${reqs.length} to leave">✓ ${reqs.length}</span>` : '')
    + (owner ? `<span class="pe-stage-owner">${actorAvatar(owner, 18)}<span>${esc(actorLabel(owner) || owner)}</span></span>` : '')
    + (picks ? `<span class="pe-stage-owner pe-stage-pool" title="${esc(pool.map(b => actorLabel(b) || b).join(', '))}"><span>One of ${pool.length}</span></span>` : '')
    + `</span>`;
  const tools = `<span class="pe-tools"><button type="button" class="ghost" data-pe-up ${at} aria-label="Move up"${si === 0 ? ' disabled' : ''}>↑</button>`
    + `<button type="button" class="ghost" data-pe-down ${at} aria-label="Move down"${si === count - 1 ? ' disabled' : ''}>↓</button>`
    + `<button type="button" class="ghost" data-pe-stage-x ${at}${count < 2 ? ' disabled' : ''}>Remove</button></span>`;
  return `<details class="pe-stage${stage.side ? ' side' : ''}" ${at}${state.open.has(stage) ? ' open' : ''}><summary>${stage.side ? SIDE_MARK : ''}<span class="pe-stage-name">${esc(stage.name || 'New stage')}</span>${meta}${side}${state.readOnly ? '' : tools}</summary>`
    + `<div class="pe-stage-body">${inner}</div></details>`;
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
    + peF('Prefix', `<input data-pe-pf="prefix" maxlength="2" value="${esc(d.prefix ?? '')}" placeholder="${esc(d.name.trim().charAt(0).toUpperCase())}" autocomplete="off">`, 'short'));
  // One stage's editor (opened from its header): that stage alone, with no pipeline fields, + Stage or moves.
  const one = state.only ? d.stages.findIndex(s => s.id === state.only) : -1;
  const stages = one >= 0 ? peStageHTML(state, d.stages[one]!, one, d.stages.length)
    : d.stages.map((s, i) => peStageHTML(state, s, i, d.stages.length)).join('');
  const pipelineFields = state.readOnly ? `<fieldset class="pe-ro pe-fields" disabled>${fields}</fieldset>` : `<div class="pe-fields">${fields}</div>`;
  return `${peClashHTML(state)}${one >= 0 ? '' : pipelineFields}<div class="pe-stages${one >= 0 ? ' pe-one' : ''}">${stages}</div>`;
}

/** The dialog's footer, which stays while the body scrolls: a refusal's words, then + Stage (the whole pipeline only) at
 * the left and Save at the right, last. */
function peFootHTML(state: PeState): string {
  const one = state.only && state.draft.stages.some(s => s.id === state.only);
  const out = state.moveOut;
  // "Move Speccing's 11 tasks to [Dev › On deck]": a removed stage's tasks go where this says, then it goes.
  const move = out ? `<label class="pe-move-out">${esc(out.line)} <select data-pe-move-to>${out.options.map(o =>
    `<option value="${esc(o.value)}"${o.value === out.to ? ' selected' : ''}>${esc(o.text)}</option>`).join('')}</select></label>` : '';
  return `<p class="err" role="alert" data-pe-error${state.error ? '' : ' hidden'}>${esc(state.error)}</p>${move}`
    + `<div class="pe-foot-row">${one ? '<span></span>' : '<button type="button" class="ghost" data-pe-stage-add>+ Stage</button>'}`
    // While a clash is shown, Save mine is the one filled button; Save stays, quiet.
    + `<button type="button" class="${state.clash ? 'ghost' : 'primary'}" data-pe-save${state.saving ? ' disabled' : ''}>Save</button></div>`;
}

/** Their record in place of this person's draft (Load theirs): one stage's editor stays on that stage, open; a stage
 * they removed leaves it for the whole pipeline. */
function peTakeTheirs(state: PeState, theirs: PipelineRec): void {
  state.base = theirs; state.draft = peCopy(theirs); state.clash = null; state.error = '';
  state.open = new WeakSet();
  const one = state.only ? state.draft.stages.find(x => x.id === state.only) : undefined;
  if (one) state.open.add(one); else state.only = '';
}

function peDraw(state: PeState): void {
  const body = document.getElementById('pe-body');
  if (PE !== state || !body) return;
  // The title is the pipeline's name, and the stage's as it is now in one stage's editor.
  const title = document.getElementById('pe-title'), one = state.only ? state.draft.stages.find(x => x.id === state.only) : undefined;
  if (title) title.textContent = one ? `${state.draft.name} › ${one.name || 'New stage'}` : state.draft.name;
  const scroll = body.scrollTop;
  body.innerHTML = peBodyHTML(state);
  body.scrollTop = scroll;
  const foot = document.getElementById('pe-foot');
  // read only: no footer (nothing to save; a rule saves on its own)
  if (foot) { foot.hidden = state.readOnly; foot.innerHTML = state.readOnly ? '' : peFootHTML(state); }
}

// ---------------------------------------------------------------- editing the draft

const peIndex = (el: HTMLElement, key: string): number => Number(el.dataset[key] ?? -1);
/** A stage's row by its key: On entering's (`limit`, `owner`, `act`) or To leave's (`req:<index>`, `hand`). */
const peRowEl = (si: number, key: string): HTMLElement | null => document.querySelector<HTMLElement>(
  `#pe-body [data-pe-${key === PE_HAND || key.startsWith('req:') ? 'leave' : 'enter'}-row="${key}"][data-pe-si="${si}"]`);

/** A field changed: the draft follows it. A change that alters what the panel shows (an owner of several) draws again; a stage's name is written in place (its row, or one stage's dialog title). */
function peInput(state: PeState, el: HTMLInputElement | HTMLSelectElement): void {
  const value = el.value, checked = el instanceof HTMLInputElement && el.type === 'checkbox' ? el.checked : false;
  if (el.dataset['peMoveTo'] !== undefined) { if (state.moveOut) state.moveOut.to = value; return; }
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
    else if (sf === 'owner') {
      // "One of several" starts with nobody on its list; any other owner has no list (a save sends none).
      const was = stage.owner;
      stage.owner = value || null;
      if (value === PE_PICK) stage.pick = stage.pick ?? pePickFresh();
      else { delete stage.pick; delete stage.pick_next; }
      if ((was === PE_PICK) !== (value === PE_PICK)) {
        // its controls come or go (and the row's mark): the keyboard stays on the Owner
        peDraw(state);
        document.querySelector<HTMLElement>(`#pe-body [data-pe-sf="owner"][data-pe-si="${peIndex(el, 'peSi')}"]`)?.focus();
      } else peEnterSay(state, peIndex(el, 'peSi'), 'owner');
    }
    else if (sf === 'requests_move_on') stage.requests_move_on = checked;
    else if (sf === 'side') {
      // A side stage moves by hand alone, with no Limit, no Late after and nothing to leave by (pipeline_settings refuses
      // them): on clears them; off leaves by hand on, for the person to take off.
      stage.side = checked;
      if (checked) {
        Object.assign(stage, {by_hand: true, requests_move_on: false, task_limit: null, expected_minutes: null, expected_bots_only: false,
          requirements: []});
        // nor One of several: its owner is one, or none
        if (stage.owner === PE_PICK) { stage.owner = null; delete stage.pick; delete stage.pick_next; }
      }
      state.rowOpen = '';
      peDraw(state);
      // the same switch again: the row's, or the one beside Name and Goal
      const where = el.closest('summary') ? 'summary' : '.pe-stage-body';
      document.querySelector<HTMLElement>(`#pe-body .pe-stage[data-pe-si="${peIndex(el, 'peSi')}"] > ${where} [data-pe-sf="side"]`)?.focus();
    }
    else if (sf === 'on_enter') { stage.on_enter = peOnEnter(value); peEnterSay(state, peIndex(el, 'peSi'), 'act'); }
    else if (sf === 'on_enter_kind') {
      // Open a linked task in… starts on the pipeline chosen before, else the first other one; its pick then shows
      const was = typeof stage.on_enter['file_into'] === 'string' ? stage.on_enter['file_into'] : '';
      stage.on_enter = value === 'file_into' ? {file_into: was || peIntoTargets(state, '')[0]?.id || ''} : peOnEnter(value);
      peDraw(state);
      // the redraw replaces the editor: the keyboard stays on this pick
      document.querySelector<HTMLElement>(`#pe-body [data-pe-sf="on_enter_kind"][data-pe-si="${peIndex(el, 'peSi')}"]`)?.focus();
    }
    else if (sf === 'task_limit') {
      stage.task_limit = Number(value) > 0 ? Math.round(Number(value)) : null;
      peEnterSay(state, peIndex(el, 'peSi'), 'limit');
    }
    else if (sf === 'expected_n' || sf === 'expected_unit') {
      const panel = el.closest('.pe-pair');
      const n = Number(panel?.querySelector<HTMLInputElement>('[data-pe-sf="expected_n"]')?.value ?? '');
      const unit = Number(panel?.querySelector<HTMLSelectElement>('[data-pe-sf="expected_unit"]')?.value ?? 60);
      stage.expected_minutes = n > 0 ? Math.round(n * unit) : null;
    }
    if (sf === 'name') {
      const summary = el.closest('.pe-stage')?.querySelector('.pe-stage-name');
      if (summary) summary.textContent = value || 'New stage';
      // its rules' form says the name as typed too ("Add rule · <stage>")
      stageRulesSectionRename(el.closest('.pe-stage'), value || 'New stage');
      // one stage's editor names the stage only in its title: it follows the field as it is typed
      const title = state.only && stage.id === state.only ? document.getElementById('pe-title') : null;
      if (title) title.textContent = `${d.name} › ${value || 'New stage'}`;
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
      peEnterSay(state, peIndex(el, 'peSi'), 'owner');
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
  if (rf === 'line') req.line = value;
  else if (rf === 'release_repo' || rf === 'release_with') req.settings = peSetting(req.settings, rf, value.trim() || null);
  else if (rf === 'person_id') req.person_id = value || null;
  else if (rf === 'document') {
    req.settings = peSetting(req.settings, 'document', value);
    req.line = value;
  } else if (rf === 'approver') {
    // one named person, or any person: never a bot (a person approves a document)
    req.person_id = value || null;
    req.who_oks = 'people_only';
  }
  else if (rf === 'who_oks') req.who_oks = value as PipelineReqRec['who_oks'];
  else if (rf === 'can_push_past') req.can_push_past = checked;
  // what the row says follows: an OK's name and face, a page's title, the lock
  if (['line', 'person_id', 'who_oks', 'can_push_past', 'document', 'approver'].includes(rf)) peLeaveSay(state, peIndex(el, 'peSi'), peIndex(el, 'peRi'));
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
  const d = state.draft, si = peIndex(el, 'peSi'), stage = d.stages[si];
  if (el.hasAttribute('data-pe-save')) { void peSave(state); return; }
  if (el.hasAttribute('data-pe-theirs') && state.clash) {
    peTakeTheirs(state, state.clash.theirs);
    peDraw(state); return;
  }
  if (el.hasAttribute('data-pe-mine') && state.clash) {
    const theirs = state.clash.theirs;
    if (state.only) {
      // One stage's editor saves that stage alone over their record: the rest (their Goal, the other stages) is
      // theirs, which this person never saw and must not undo. A stage they removed meanwhile is not put back.
      const mine = state.draft.stages.find(x => x.id === state.only), next = peCopy(theirs);
      const at = next.stages.findIndex(x => x.id === state.only);
      if (!mine || at < 0) {
        peTakeTheirs(state, theirs);
        state.error = 'Someone removed this stage while you had it open.';
        peDraw(state); return;
      }
      next.stages[at] = mine;
      state.draft = next;
    }
    state.base = theirs; state.clash = null;
    void peSave(state); return;
  }
  if (el.hasAttribute('data-pe-stage-add')) {
    const fresh: PipelineStageRec = {name: '', purpose: '', owner: null, by_hand: false, requests_move_on: false, task_limit: null, expected_minutes: null,
      expected_bots_only: false, on_enter: {}, requirements: [], side: false};
    d.stages.push(fresh);
    state.open.add(fresh);
    peDraw(state);
    document.querySelector<HTMLInputElement>('#pe-body .pe-stage:last-of-type [data-pe-sf="name"]')?.focus();
    return;
  }
  if (!stage) return;
  const seg = el.dataset['peSeg'], v = el.dataset['v'] ?? '';
  if (seg && el.hasAttribute('data-pe-seg')) {
    if (seg === 'pick_how' && stage.pick && (v === 'turns' || v === 'random')) stage.pick.how = v;
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
    state.rowOpen = '';
    peDraw(state);
  };
  const enter = (sel: string): HTMLElement | null => document.querySelector<HTMLElement>(`#pe-body ${sel}[data-pe-si="${si}"]`);
  if (el.hasAttribute('data-pe-up')) swap(-1);
  else if (el.hasAttribute('data-pe-down')) swap(1);
  else if (el.hasAttribute('data-pe-stage-x')) { if (d.stages.length > 1) { d.stages.splice(si, 1); state.rowOpen = ''; peDraw(state); } }
  else if (el.hasAttribute('data-pe-enter-add') && el.hasAttribute('aria-haspopup')) peEnterPickOpen(state, el);
  else if (el.dataset['peEnterPick'] || el.dataset['peEnterAdd'] === 'rule') {
    // a pick in + Rule's or + Action's list, or + Rule with only a rule to add: a Rule opens its form over the editor
    // (the draft stays as typed); a Limit or an action is a row of the draft, its editor open on what to choose (the
    // queue has nothing to choose: its row)
    const kind = el.dataset['peEnterPick'] ?? 'rule';
    el.closest<HTMLElement>('[popover]')?.hidePopover();
    if (kind === 'rule') {
      const sec = document.querySelector<HTMLElement>(`#pe-body .pe-stage[data-pe-si="${si}"] [data-sr-section][data-sr-phase="enter"]`);
      if (sec) stageRulesAdd(sec);
      return;
    }
    if (kind === 'limit') stage.task_limit = PE_LIMIT_NEW;
    // a change of owner starts on the first bot (the task's assignee where there is none); its Owner pick has the focus
    else if (kind === 'owner') stage.owner = peBots()[0]?.id ?? PE_ASSIGNEE;
    else if (kind === 'file_into') stage.on_enter = {file_into: peIntoTargets(state, '')[0]?.id ?? ''};
    else if (kind === 'queue') stage.on_enter = {act: 'github.queue'};
    else return;
    state.rowOpen = kind === 'queue' ? '' : `${si}:${kind === 'limit' || kind === 'owner' ? kind : 'act'}`;
    peDraw(state);
    if (kind === 'limit') { const n = enter('[data-pe-sf="task_limit"]') as HTMLInputElement | null; n?.focus(); n?.select(); }
    else if (kind === 'owner') enter('[data-pe-sf="owner"]')?.focus();
    else if (kind === 'file_into') enter('[data-pe-sf="on_enter"]')?.focus();
    else enter('[data-pe-enter-row="act"]')?.querySelector<HTMLElement>('.sr-open')?.focus();
  } else if (el.dataset['peRow']) {
    // a row opens its editor in place, or closes it; the row keeps the focus closed, its first field has it open
    const of = el.dataset['peRow'] ?? '', key = `${si}:${of}`;
    state.rowOpen = state.rowOpen === key ? '' : key;
    peDraw(state);
    const row = peRowEl(si, of);
    (state.rowOpen ? row?.querySelector<HTMLElement>('.pe-row-ed :is(input, select)') : row?.querySelector<HTMLElement>('.sr-open'))?.focus();
  } else if (el.dataset['peRowX']) {
    const x = el.dataset['peRowX'];
    const leave = x === PE_HAND || x.startsWith('req:');
    if (x === 'limit') stage.task_limit = null;
    else if (x === 'owner') { stage.owner = null; delete stage.pick; delete stage.pick_next; }
    // by hand off: a person's request no longer has anything to move on from (pipeline_settings: by-hand stages only)
    else if (x === PE_HAND) { stage.by_hand = false; stage.requests_move_on = false; }
    else if (x.startsWith('req:')) stage.requirements.splice(Number(x.slice('req:'.length)), 1);
    else stage.on_enter = {};
    state.rowOpen = '';
    peDraw(state);
    // what was removed can be added again: its add button has the focus
    (leave ? enter('[data-pe-req-add]') : enter(`[data-pe-enter-add="${x === 'limit' ? 'rule' : 'act'}"]`) ?? enter('[data-pe-enter-add]'))?.focus();
  }
  else if (el.hasAttribute('data-pe-req-add')) peReqPickOpen(state, el);
  else if (el.dataset['peReqKind']) {
    // A pick in + Requirement's list: the requirement goes last (by hand is the stage's own, its row last of all); one
    // with something to choose opens its editor, its first field with the focus; any other is its row, focused.
    const pick = el.dataset['peReqKind'];
    el.closest<HTMLElement>('[popover]')?.hidePopover();
    let key = PE_HAND;
    if (pick === 'by_hand') stage.by_hand = true;
    else { stage.requirements.push(peReqNew(state, pick)); key = `req:${stage.requirements.length - 1}`; }
    state.rowOpen = PE_REQ_CHOOSE.includes(pick) ? `${si}:${key}` : '';
    peDraw(state);
    const row = peRowEl(si, key);
    (state.rowOpen ? row?.querySelector<HTMLElement>('.pe-row-ed :is(input:not([type=checkbox]), select)') : row?.querySelector<HTMLElement>('.sr-open'))?.focus();
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

/** The draft as it is sent: no bot families, and a stage as a person set it (peStageOwn). */
function peOut(draft: PipelineRec): PipelineRec {
  const out = peCopy(draft);
  delete out.bot_families;
  out.stages = out.stages.map(peStageOwn);
  return out;
}

/** One stage's editor against a save that came first: their record with this stage swapped in when they left this stage
 * as this person read it (compared as stored, peStageOwn), else null (they changed or removed it: a clash). */
function peQuietMerge(state: PeState, theirs: PipelineRec): PipelineRec | null {
  if (!state.only) return null;
  const read = state.base.stages.find(x => x.id === state.only), now = theirs.stages.findIndex(x => x.id === state.only);
  const mine = state.draft.stages.find(x => x.id === state.only), their = theirs.stages[now];
  if (!read || !mine || !their || JSON.stringify(peStageOwn(read)) !== JSON.stringify(peStageOwn(their))) return null;
  const next = peCopy(theirs);
  next.stages[now] = mine;
  return next;
}

/** Save: one at a time. `saving` holds from the press until the last answer, through a clash's re-read and a quiet
 * retry, so Save stays off and a second press cannot send the old version meanwhile. */
async function peSave(state: PeState): Promise<void> {
  if (state.saving) return;
  state.saving = true; state.error = '';
  peDraw(state);
  try { await peSaveTry(state, 0); } finally {
    if (PE === state) { state.saving = false; peDraw(state); }
  }
}

/** One try at the save (peSave holds `saving`). A refusal because someone saved first re-reads their record: one stage's
 * editor whose stage they left as read saves again on theirs (up to three times), else the clash banner. */
async function peSaveTry(state: PeState, retries: number): Promise<void> {
  try {
    const [toPipeline, toStage] = (state.moveOut?.to ?? '').split('/');
    const moving = toPipeline && toStage ? {move_tasks_to: {pipeline: toPipeline, stage: toStage}} : {};
    const saved = await pipelineSave(state.id, {...peOut(state.draft), settings_version: state.base.settings_version, ...moving});
    if (PE !== state) return;
    state.moveOut = null;
    // The editor stays as S11 draws it, now on the saved record: its ids and its new settings_version.
    const draft = peCopy(saved);
    state.open = peOpenSame(state.open, state.draft.stages, draft);
    state.base = saved; state.draft = draft;
    toast('Saved');
  } catch (error) {
    if (PE !== state) return;
    const refused = taskRefusedOf(error);
    if (errorCode(error) === 'stage_has_tasks') { await peMoveOutAsk(state, error); return; }
    if (refused?.error === 'task_changed') {
      try {
        const theirs = await pipelineRec(state.id);
        if (PE !== state) return;
        // One stage's editor: a save that did not touch this stage is no clash for this person. Theirs, with this stage
        // swapped in, goes at their version, with no banner (a few tries, in case saves keep landing meanwhile).
        const quiet = peQuietMerge(state, theirs);
        if (quiet && retries < 3) { state.base = theirs; state.draft = quiet; await peSaveTry(state, retries + 1); return; }
        state.clash = {theirs, changed: peChanged(state.base, theirs)};
      } catch (again) { state.error = errorMessage(again, 'Could not read the pipeline again.'); }
    } else {
      state.error = refused?.detail || errorMessage(error, 'Could not save.');
    }
  }
}

/** The save removed a stage that holds tasks: ask where they go, from every pipeline's stages but those removed, the
 * server's suggestion picked. Save again sends it. */
async function peMoveOutAsk(state: PeState, error: unknown): Promise<void> {
  const said = errorBody(error);
  const gone = Array.isArray(said['stages']) ? said['stages'] as {id: string; name: string; tasks: number}[] : [];
  const hint = isErrorRecord(said['move_to']) ? said['move_to'] : {};
  const all = await pipelinesAll().catch((): PipelineRec[] => []);
  if (PE !== state) return;
  const leaving = new Set(gone.map(g => g.id));
  const options = all.filter(p => !p.retired_at).flatMap(p => p.stages.filter(st => st.id && !leaving.has(st.id))
    .map(st => ({value: `${p.id}/${st.id}`, text: `${p.name} › ${st.name}`})));
  const n = gone.reduce((sum, g) => sum + g.tasks, 0);
  const names = gone.map(g => g.name).join(' and ');
  const suggested = `${String(hint['pipeline'] ?? '')}/${String(hint['stage'] ?? '')}`;
  state.moveOut = {line: `Move ${names}'s ${n} task${n === 1 ? '' : 's'} to`, options,
    to: options.some(o => o.value === suggested) ? suggested : options[0]?.value ?? ''};
  state.error = '';
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

/** Opens the editor for a pipeline; with a stage id (a stage's header), it shows that stage alone, open; `at` 'rules'
 * (the stage's rules mark) scrolls to its first phase with rules, whose heading has the focus. */
async function openPipelineEditor(pipelineId: string, stageId = '', at: '' | 'rules' = ''): Promise<void> {
  document.getElementById('pe-dialog')?.remove();
  const dialog = document.createElement('dialog');
  dialog.id = 'pe-dialog'; dialog.className = 'tmodal tmodal-shell pe-dialog';
  dialog.setAttribute('aria-labelledby', 'pe-title');
  dialog.innerHTML = `<div class="tmodal-head"><h2 id="pe-title">Pipeline</h2><button class="ghost tmodal-x" type="button" data-pe-close aria-label="Close">✕</button></div>
    <div class="tmodal-body pe-body" id="pe-body">${skelRows(3, {dot: false})}</div><div class="tmodal-foot pe-foot" id="pe-foot" hidden></div>`;
  document.body.append(dialog);
  const leave = (): void => dialog.close();
  window.addEventListener('hashchange', leave, {once: true});
  dialog.addEventListener('close', () => { window.removeEventListener('hashchange', leave); dialog.remove(); PE = null; SR_ED = null; });
  dialog.addEventListener('click', ev => {
    const t = eventElement(ev);
    if (!t) return;
    if (t.closest('[data-pe-close]')) { dialog.close(); return; }
    // the phases' rule clicks: a rule's form opens over the editor, the stage's draft stays
    if (peMoreClick(ev)) return;
    if (stageRulesSectionClick(ev)) return;
    // A button in a stage's summary (up, down, remove) does not also open or close the stage; nor does its Side switch's
    // word, which flips the switch alone.
    if (t.closest('summary button')) ev.preventDefault();
    const sideWord = t.closest('summary .pe-side');
    if (sideWord && !(t instanceof HTMLInputElement)) { ev.preventDefault(); sideWord.querySelector('input')?.click(); return; }
    if (PE) peClick(PE, t);
  });
  // A stage's panel stays open or closed across redraws.
  const note = (panel: HTMLDetailsElement): void => {
    const stage = PE ? PE.draft.stages[peIndex(panel, 'peSi')] : undefined;
    if (!PE || !stage) return;
    if (panel.classList.contains('pe-stage')) { if (panel.open) PE.open.add(stage); else PE.open.delete(stage); }
  };
  dialog.addEventListener('toggle', ev => { if (ev.target instanceof HTMLDetailsElement) note(ev.target); }, true);
  const onField = (ev: Event): void => {
    const t = ev.target;
    if (!PE || !(t instanceof HTMLInputElement || t instanceof HTMLSelectElement)) return;
    // The browser reports a panel opening a moment after it opens: a choice made straight away would redraw it shut,
    // so the panels are read as they stand before the change is taken.
    dialog.querySelectorAll<HTMLDetailsElement>('details.pe-stage').forEach(note);
    peInput(PE, t);
  };
  // Typing follows each key; a choice or a switch follows its change.
  const choice = (t: EventTarget | null): boolean => t instanceof HTMLSelectElement || (t instanceof HTMLInputElement && t.type === 'checkbox');
  dialog.addEventListener('input', ev => { if (!choice(ev.target)) onField(ev); });
  dialog.addEventListener('change', ev => { if (choice(ev.target)) onField(ev); });
  dialog.addEventListener('keydown', peSegKey);
  // Esc in a rule's opened read view closes it, not the editor
  dialog.addEventListener('keydown', stageRuleRowKey);
  dialog.showModal();
  try {
    const [rec, factories, all] = await Promise.all([
      pipelineRec(pipelineId),
      factoriesRead('7d').then(map => map.factories, () => null),
      pipelinesAll().catch(() => [] as PipelineRec[]),
      stageRulesEditorLoad(pipelineId),
    ]);
    if (!dialog.isConnected) return;
    const pipelines = all.filter(p => !p.retired_at).map(p => ({id: p.id, name: p.name}));
    const state: PeState = {id: pipelineId, base: rec, draft: peCopy(rec), open: new WeakSet(), factories, pipelines, clash: null, error: '', saving: false, only: '',
      readOnly: rec.may_change === false, rowOpen: ''};
    const opened = state.draft.stages.find(s => s.id === stageId);
    if (opened) { state.open.add(opened); state.only = opened.id ?? ''; }
    PE = state;
    peDraw(state);
    // the rules mark: the first phase with rules (On entering when none has), its heading focused
    const secs = opened && at === 'rules' ? [...dialog.querySelectorAll<HTMLElement>(`[data-sr-section="${CSS.escape(opened.id ?? '')}"]`)] : [];
    const rules = (secs.find(sec => sec.querySelector('[data-sr-rule]')) ?? secs[0])?.closest('.pe-phase')?.querySelector<HTMLElement>('.pe-phase-h') ?? null;
    if (rules) { rules.scrollIntoView({block: 'start'}); rules.focus({preventScroll: true}); }
    else if (opened) dialog.querySelector('.pe-stage[open]')?.scrollIntoView({block: 'nearest'});
  } catch (error) {
    toast(errorMessage(error, 'Could not open the pipeline.'), true);
    dialog.close();
  }
}
