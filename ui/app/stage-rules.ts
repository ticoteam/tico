/* ui/app/stage-rules.ts — A stage's rules (Stage rules spec, step 1, part 3): a quiet rules mark at the far right of a
   stage header on a pipeline board, only when the stage has rules of its own (style guide, Pipeline stage: a gavel, no
   word, no count; its name is the stage and the count, "Build: 3 rules of its own"), and the rules in the stage's editor
   (pipeline-editor.ts), which the mark opens: every rule that applies to the stage, in the phase where it acts (On
   entering, While here; srPhaseOf), the stage's own first, the shared ones with a quiet mark, each with what it costs
   (free, or one Jev call per event); the built-in ones in one line at the end. Whoever may change the pipeline adds a
   rule (a phase's + Rule: On starts as this stage, When on that phase), edits it and retires it, in a form over the
   editor that acts at once; Built in is read-only.
   Read from and written to backend/stage_rules_api.py: GET /api/v2/stage-rules?pipeline=<id> once per board for the
   counts, and again when the editor opens. A rule says whether the caller may change it (`can_edit`); the server checks
   again, and a refusal shows as one line. An edit sends the rule's `revision`: a 409 says so.
   The board's mark and its counts are in stage-rules-mark.ts, in the shell; this file is the stage-rules page part
   (ui/parts.json), loaded by the parts that use it (task, company).
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
  status: string; cost: string; can_edit?: boolean; not_acting?: string; created_by?: string; suggests?: string;
  replaces?: StageRuleCore; retires?: boolean; retire_why?: string;
}
/** A behaviour in code that applies at a stage: listed, never edited. */
interface StageBuiltIn { name: string; what?: string; where?: string }
interface StageRulesList { items: StageRule[]; built_in: StageBuiltIn[]; may_add?: boolean }

const SR_EVENTS: [SrEvent, string][] = [['enters', 'A task enters'], ['owner_changes', 'The owner changes'], ['bot_snoozes', 'A bot snoozes'], ['timer', 'A timer']];
const SR_TIMERS: [string, string][] = [['in_stage', 'in the stage'], ['quiet', 'quiet'], ['waiting_person', 'waiting on a person']];
const SR_THENS: [SrThen, string][] = [['block', 'Block'], ['tell_manager', 'Tell the manager'], ['tell_owner', 'Tell the owner'], ['move_back', 'Move back']];
/** Step 1 carries out Tell and Move back on timers alone, so an action When offers Block only (stage_rules.NEEDS_TIMER). */
const SR_ACTION_THENS: [SrThen, string][] = SR_THENS.filter(([k]) => k === 'block');
const SR_FREE = 'free';

/** The Add rule and Edit form's fields by name (`name` and `on` are absent from a stage's form, `on` for a company rule,
 * whose On is fixed). */
interface SrControls {
  name?: HTMLInputElement; on?: HTMLSelectElement; event: HTMLSelectElement; hours: HTMLInputElement; timer_kind: HTMLSelectElement;
  owner_check: HTMLSelectElement; owner_team: HTMLInputElement; label: HTMLInputElement; jev: HTMLTextAreaElement; then: HTMLSelectElement; reason: HTMLInputElement;
}
const srControls = (form: HTMLFormElement) => form.elements as unknown as SrControls;
const srTarget = (ev: Event) => ev.target as HTMLElement;

// ---------------------------------------------------------------- the stage editor's rules, in its phases

/** A stage is edited as its phases (pipeline-editor.ts peStageHTML): On entering, While here, To leave. A rule sits in
 * the phase where it acts, by its When:
 *
 *   When (backend/stage_rules.py EVENTS)            Phase
 *   enters, alone or with other actions          On entering
 *   owner_changes (alone)                        While here
 *   bot_snoozes                                  While here
 *   timer (in the stage, quiet, waiting on a person)  While here
 *   anything else (one this screen has no words for)  While here
 *
 * No event fires as a task leaves yet, so To leave holds no rule and has no + Rule until one does. */
/* Chris, Nov 14: the When is the place a rule sits in the stage, never a field on the rule's form. While here holds
   the timers; what answers something else (a bot's snooze, an owner change on its own, a When with no words here)
   sits in More rules, after To leave. */
type SrPhase = 'enter' | 'here' | 'leave' | 'more';
const srPhaseOf = (event: string): SrPhase => event.split(',').includes('enters') ? 'enter' : event === 'timer' ? 'here' : 'more';
const srIsPhase = (at: string | undefined): at is SrPhase => at === 'enter' || at === 'here' || at === 'leave' || at === 'more';
/** The When a phase's + Rule offers, the first picked. */
const SR_PHASE_EVENTS: Record<SrPhase, [string, string][]> = {
  enter: [['enters', 'A task enters']],
  here: [['timer', 'A timer']],
  leave: [],
  more: [['bot_snoozes', 'A bot snoozes']],
};

/** The rules a form is about: the stage it is On, the rules that apply there, and where it was opened from. */
interface SrOpen {
  pipelineId: string; stageId: string; stageName: string; list: StageRulesList | null;
  /** The phase whose + Rule opened the form: its When starts there. */
  phase?: SrPhase;
  /** The rule whose form was opened last, for the focus back ('' for a new one). */
  last?: string;
  /** Where the form was opened (a stage's phase, or the Rules page): `done` reads its rules again after a save,
   * `focus` names what has the focus after (the rule's row, by id; '' for none). */
  host: {done: () => Promise<void>; focus: (id: string) => HTMLElement | null | undefined};
}
let SR: SrOpen | null = null;

/** The rules the open stage editor shows: every rule that applies anywhere in its pipeline (one read), the built-in ones
 * and whether the viewer may add one. Each stage's phases take the ones that apply there (srStageList). */
let SR_ED: {pipelineId: string; list: StageRulesList | null; note: string} | null = null;

/** Reads the pipeline's rules for the editor and draws its stages' rules again; never throws (a refusal is one line).
 * The rules shown stay until the new ones come. */
async function stageRulesEditorLoad(pipelineId: string): Promise<void> {
  const prior = SR_ED?.pipelineId === pipelineId ? SR_ED.list : null;
  const mine: NonNullable<typeof SR_ED> = {pipelineId, list: prior, note: ''};
  SR_ED = mine;
  try { mine.list = await get<StageRulesList>(`/v2/stage-rules?pipeline=${encodeURIComponent(pipelineId)}`, {fresh: true}); }
  catch (e) { mine.note = ruError(e); }
  if (SR_ED === mine) srSectionsPaint();
}

/** The rules that apply at one stage: its own, the whole pipeline's and the company's (as GET …&stage= answers), its own
 * first. */
const SR_SCOPE_ORDER: SrScope[] = ['stage', 'pipeline', 'company'];
const srStageList = (list: StageRulesList, stageId: string): StageRulesList =>
  // (an answer without a list draws as none, never as an editor that fails to open)
  ({...list, items: (list.items ?? []).filter(r => r.scope !== 'stage' || (r.stages ?? []).includes(stageId))
    .sort((x, y) => SR_SCOPE_ORDER.indexOf(x.scope) - SR_SCOPE_ORDER.indexOf(y.scope))});

/** A stage's rules in one phase (pipeline-editor.ts peStageHTML puts it under that phase's fields), or, for `always`,
 * the built-in line at the end of the stage. Its body is drawn again when the rules are read (srSectionsPaint). A stage
 * not saved yet has none. */
function stageRulesPhaseHTML(pipelineId: string, stage: {id?: string | null; name: string}, phase: SrPhase | 'always'): string {
  const id = stage.id ?? '';
  if (!id) return '';
  return `<div class="sr-phase" data-sr-section="${esc(id)}" data-sr-phase="${phase}" data-sr-pipeline="${esc(pipelineId)}" data-sr-stage-name="${esc(stage.name)}">`
    + `<div class="sr-sec-body">${srSectionBodyHTML(pipelineId, id, stage.name, phase)}</div></div>`;
}

function srSectionBodyHTML(pipelineId: string, stageId: string, stageName: string, phase: string): string {
  const ed = SR_ED;
  const always = phase === 'always';
  if (!ed || ed.pipelineId !== pipelineId || (!ed.list && !ed.note)) return always ? '<p class="sr-none muted">Loading rules…</p>' : '';
  // a refusal says so once, at the end of the stage
  const note = ed.note && always ? `<p class="ru-status sr-note" role="status">${esc(ed.note)}</p>` : '';
  if (!ed.list) return note;
  const open = {pipelineId, stageId, stageName, list: srStageList(ed.list, stageId)};
  return note + (always ? srAlwaysHTML(open) : srPhaseRowsHTML(open, phase as SrPhase));
}

/** Draws each stage's rules in the page again from the rules read. */
function srSectionsPaint(only?: HTMLElement): void {
  for (const sec of only ? [only] : document.querySelectorAll<HTMLElement>('[data-sr-section]')) {
    const body = sec.querySelector<HTMLElement>(':scope > .sr-sec-body');
    if (body) body.innerHTML = srSectionBodyHTML(sec.dataset['srPipeline'] ?? '', sec.dataset['srSection'] ?? '', sec.dataset['srStageName'] ?? '', sec.dataset['srPhase'] ?? '');
  }
}

/** A stage's name was typed in its editor: its rules' form says the new one ("Add rule · <stage>"). */
function stageRulesSectionRename(stageEl: Element | null | undefined, name: string): void {
  for (const sec of stageEl?.querySelectorAll<HTMLElement>('[data-sr-section]') ?? []) sec.dataset['srStageName'] = name;
}

/** What has the focus in a stage after a form or a retire: the rule's row (in whichever phase it now sits), else the
 * phase's + Rule (On entering's: the editor's + Rule, else + Action), else the phase's heading. */
function srSectionFocus(stageId: string, id: string, phase: SrPhase | undefined): HTMLElement | null {
  const all = [...document.querySelectorAll<HTMLElement>(`[data-sr-section="${CSS.escape(stageId)}"]`)];
  const row = id ? all.map(sec => sec.querySelector<HTMLElement>(`[data-sr-rule="${CSS.escape(id)}"] .sr-open`)).find(Boolean) : null;
  const sec = all.find(x => x.dataset['srPhase'] === phase) ?? all[0], ph = sec?.closest('.pe-phase');
  return row ?? sec?.querySelector<HTMLElement>('[data-sr-add]') ?? ph?.querySelector<HTMLElement>('[data-pe-enter-add]')
    ?? ph?.querySelector<HTMLElement>('.pe-h') ?? null;
}

/** A new rule's form for a stage's phase, from outside its rules (On entering's + Rule, the editor's): as that
 * phase's + Rule would. False when the rules are not read. */
function stageRulesAdd(sec: HTMLElement): boolean {
  const open = srSectionOpen(sec);
  if (!open) return false;
  SR = open; srFormOpen(null);
  return true;
}

/** A form opened from a stage's phase: its stage, the rules there, and the rules read again after a save. */
function srSectionOpen(sec: HTMLElement): SrOpen | null {
  const ed = SR_ED, stageId = sec.dataset['srSection'] ?? '';
  if (!ed?.list || !stageId) return null;
  const at = sec.dataset['srPhase'], phase = srIsPhase(at) ? at : undefined;
  const open: SrOpen = {pipelineId: ed.pipelineId, stageId, stageName: sec.dataset['srStageName'] ?? '', list: srStageList(ed.list, stageId),
    host: {done: () => stageRulesEditorLoad(ed.pipelineId), focus: id => srSectionFocus(stageId, id, open.phase)}};
  if (phase) open.phase = phase;
  return open;
}

/** A click on a stage's rules (the editor's dialog hands it here first): a read-only row opens in place, Always on
 * opens, a row or ⋯ › Edit opens the rule's form over the editor, a phase's + Rule a new one starting on that phase's
 * When, ⋯ › Retire asks, then retires. The stage's own fields, and what was typed in them, stay as they are. True when
 * the click was on its rules. */
function stageRulesSectionClick(ev: Event): boolean {
  const t = eventElement(ev), sec = t?.closest<HTMLElement>('[data-sr-section]');
  if (!t || !sec) return false;
  if (stageRuleRowToggle(ev)) return true;
  const always = t.closest<HTMLElement>('[data-sr-always]');
  if (always) {
    const on = always.getAttribute('aria-expanded') !== 'true', l = document.getElementById(always.getAttribute('aria-controls') ?? '');
    always.setAttribute('aria-expanded', String(on));
    if (l) l.hidden = !on;
    return true;
  }
  const el = t.closest<HTMLElement>('[data-sr-add],[data-sr-edit],[data-sr-more]'), open = el ? srSectionOpen(sec) : null;
  if (!el || !open) return true;
  const r = open.list?.items.find(x => x.id === (el.dataset['srEdit'] ?? el.dataset['srMore']));
  if (el.matches('[data-sr-add]')) { SR = open; srFormOpen(null); }
  else if (el.matches('[data-sr-edit]') && r) { SR = open; srFormOpen(r); }
  // a pick: Edit opens the form; Retire asks, and its confirm hands the focus back to that ⋯
  else if (r) srMenuOpen(el, r, (what, from) => { SR = open; if (what === 'edit') srFormOpen(r); else void srRetire(r, from); });
  return true;
}

/** "A task enters or the owner changes": a When of one event or several actions. One this screen has no words for reads
 * as plain words, never its stored name. */
function srWhen(event: string): string {
  const said = event.split(',').filter(Boolean).map((e, i) => {
    const w = SR_EVENTS.find(([k]) => k === e)?.[1] ?? 'Something happens';
    return i ? w.charAt(0).toLowerCase() + w.slice(1) : w;
  });
  return said.join(' or ') || 'Something happens';
}

function srHeadHTML(title: string): string {
  return '<div class="ru-side-h">'
    + `<h2>${esc(title)}</h2><span class="spacer"></span><button class="ghost tmodal-x" type="button" data-sr-close aria-label="Close">✕</button></div>`;
}

/** "The owner changes → owner not on engineering → Block": the rule in one line. `short`: a Decision model question reads "Decision model
 * decides" (a row's line; the question itself is in the rule's opened view and its form). */
function srSentence(r: StageRuleCore, short = false): string {
  const {when, ifs, act} = srParts(r, short);
  return `${when}${ifs ? ` → ${ifs}` : ''} → ${act}`;
}

/** An If's condition in plain words (backend/stage_rules.py CONDITIONS); one this screen does not know reads "a condition
 * holds", never its stored name. */
function srCond(k: string, v: unknown): string {
  const val = typeof v === 'string' || typeof v === 'number' ? String(v) : '';
  if (k === 'owner_on_team' || k === 'owner_not_on_team') return `owner ${k === 'owner_on_team' ? 'on' : 'not on'} ${val || "the manager's team"}`;
  if (k === 'owner_kind') return `owner is a ${val === 'person' ? 'person' : 'bot'}`;
  if (k === 'turn') return `it is ${val === 'person' ? "a person's" : "a bot's"} turn`;
  if (k === 'snooze_hours_over') return `snoozed over ${val} h`;
  if (k === 'label') return `labelled ${val}`;
  // true: asked by a person; false: asked by a bot (backend/stage_rules.py matches either when it is not null)
  if (k === 'requester_person') return v ? 'asked by a person' : 'asked by a bot';
  if (k === 'sent_back') return val ? `sent back ${val} times` : 'sent back';
  return 'a condition holds';
}

/** A rule's When, If (empty for none) and Then (`act`) in words, each on its own so a change can show what differs. */
function srParts(r: StageRuleCore, short = false): {when: string; ifs: string; act: string} {
  let when = srWhen(r.event);
  if (r.event === 'timer' && r.timer?.hours) when = `${r.timer.hours} h ${SR_TIMERS.find(([k]) => k === r.timer?.kind)?.[1] ?? 'in the stage'}`;
  const ifs = Object.entries(r.cond || {}).flatMap(([k, v]): string[] => {
    // An owner's team that is empty means the pipeline manager's team (backend/stage_rules.py CONDITIONS).
    if (k === 'owner_on_team' || k === 'owner_not_on_team') return [srCond(k, v)];
    // an unset condition says nothing; false is one only for requester_person ("asked by a bot", backend/stage_rules.py
    // reads it whenever it is not null), elsewhere false is off
    if (v === '' || v === null || v === undefined || (v === false && k !== 'requester_person')) return [];
    return [srCond(k, v)];
  });
  if (r.jev) ifs.push(short ? 'Decision model decides' : `Decision model: ${r.jev}`);
  return {when, ifs: ifs.join(', '), act: SR_THENS.find(([k]) => k === r.then)?.[1] ?? 'it acts'};
}

/** "Not acting: …": a rule saved before Tell and Move back needed a timer; it never acts until it is changed. */
const srIdleHTML = (r: StageRule) => r.not_acting ? `<p class="sr-idle" data-sr-idle>Not acting: ${esc(r.not_acting)}</p>` : '';

// ---------------------------------------------------------------- one rule's row (here and on the Rules page)

/** One outcome vocabulary for every rule (icon subset: ui/vendor/fonts/icons.txt): a stage rule's Then and a company
 * rule's If broken (org-rules.ts RU_BROKEN) draw the same mark for the same outcome. Stored values are unchanged. */
const RULE_OUTCOME: Record<string, {icon: string; word: string}> = {
  block: {icon: 'block', word: 'Block'},                          // stopped (a company rule's Stop)
  escalate: {icon: 'thumb_up', word: 'Ask first'},                // needs an OK
  warn: {icon: 'warning', word: 'Warn'},                          // warned
  tell_manager: {icon: 'supervisor_account', word: 'Tell the manager'},   // someone is told
  tell_owner: {icon: 'person', word: 'Tell the owner'},
  move_back: {icon: 'assignment_return', word: 'Move back'},      // moved back
};
/** A rule's outcome as a mark, its word as its name and tip ('' for none). `stopWord` names a company rule's block. */
function ruleOutcomeIconHTML(outcome: string | null | undefined, stopWord = '', hidden = false): string {
  const o = outcome ? RULE_OUTCOME[outcome] : undefined;
  if (!o) return '';
  const word = outcome === 'block' && stopWord ? stopWord : o.word;
  // The glyph is drawn from data-icon (stage-rules.css), so the text around it reads as the rule alone (a link's text).
  // `hidden`: the words beside it already end with the outcome (a stage rule's sentence), so a reader hears it once.
  const named = hidden ? 'aria-hidden="true"' : `role="img" aria-label="${esc(word)}"`;
  return `<span class="nav-icon rule-outcome" ${named} title="${esc(word)}" data-icon="${o.icon}" data-sr-then="${esc(outcome ?? '')}"></span>`;
}
/** A row's leading mark: what the rule does, its word as its tip (the sentence beside it says it to a reader); an empty
 * column for a built-in. */
const srThenHTML = (then: string | undefined): string => `<span class="sr-then">${ruleOutcomeIconHTML(then, '', true)}</span>`;
/** A quiet mark after the text: an icon with its words as its name and tip (role img: it is not a control). */
const srMarkHTML = (cls: string, icon: string, words: string, attr = ''): string =>
  `<span class="sr-m ${cls} nav-icon" role="img" aria-label="${esc(words)}" title="${esc(words)}"${attr ? ' ' + attr : ''}>${icon}</span>`;
const srCostWords = (cost: string): string => cost.charAt(0).toUpperCase() + cost.slice(1);

/** One rule as a dense row (style guide: rows are a line with marks, not words). */
interface StageRuleRow {
  /** Unique on the page: names the row's read view. */
  key: string;
  /** The row element's own attributes (data-sr-rule, data-stage-rule, data-sr-kind…). */
  attrs: string;
  tag?: 'li' | 'div';
  /** Where it applies beyond this stage ("In every pipeline"): a quiet mark, in the stage editor; none for its own. */
  shared?: string | undefined;
  /** Its Then (block, tell_manager…); none for a built-in. */
  outcome?: string | undefined;
  /** The rule in one line (srSentence; a built-in's what): cut with "…" on the row, whole in its tip and name. */
  text: string;
  /** What checking it costs; none, or free, has no mark. */
  cost?: string | undefined;
  /** The cost mark's glyph: bolt in a stage (a decision model checks it); the brain elsewhere. */
  costIcon?: string | undefined;
  /** Why it never acts (not_acting). */
  idle?: string | undefined;
  /** A bot's suggestion: the mark's words ("Suggested change"). */
  suggest?: string | undefined;
  builtIn?: boolean | undefined;
  /** What the row button does: its attributes (data-sr-edit="id", data-sr-open-ru…); none opens the read view below. */
  open?: string | undefined;
  /** A leading glyph in place of a Then's mark: in a stage's phase lists, the rule mark or the action mark (SR_ROW_ICONS). */
  icon?: string | undefined;
  /** With `open`: what sits under the row, such as its own small editor opened in place (pipeline-editor.ts). */
  below?: string | undefined;
  /** The rule's id for its ⋯ (Edit, Retire), where the viewer may change it. */
  more?: string | undefined;
  /** The rule's name: ⋯ is named for it ("More for Owner on the team"). */
  name?: string | undefined;
  /** The read view's HTML (the name, the whole rule, what it says, its cost, why it is not acting, actions). */
  detail?: string | undefined;
  /** HTML right after the text, inside the row button: a requirement's faces (who OKs it). */
  after?: string | undefined;
  /** More marks after its own, at the right: a requirement's lock (it can't be pushed past). */
  marks?: string | undefined;
}
/** The row: what it does (a mark), the rule cut to one line, its marks (costs a Jev call, not acting, a bot's
 * suggestion, built in), then ⋯ where the viewer may change it. The row is one button: it opens the rule for editing,
 * or, read only, its whole text in place under the row (aria-expanded). 36 px on a computer, 44 px on a phone. */
function stageRuleRowHTML(o: StageRuleRow): string {
  const tag = o.tag ?? 'li', det = `sr-d-${o.key}`;
  const marks = (o.cost && o.cost !== SR_FREE ? srMarkHTML('sr-m-cost', o.costIcon ?? 'psychology', srCostWords(o.cost), 'data-sr-cost') : '')
    + (o.idle ? srMarkHTML('sr-m-idle', 'error', `Not acting: ${o.idle}`, 'data-sr-idle') : '')
    + (o.suggest ? srMarkHTML('sr-m-suggest', 'lightbulb', o.suggest, 'data-sr-suggest') : '')
    + (o.shared ? srMarkHTML('sr-m-shared', 'dynamic_feed', o.shared, 'data-sr-shared') : '') + (o.marks ?? '');
  const how = o.open || `data-sr-read aria-expanded="false" aria-controls="${esc(det)}"`;
  // ⋯, or its empty slot, so every row's marks sit in the same column
  const more = o.more ? `<button type="button" class="sr-more" data-sr-more="${esc(o.more)}" aria-haspopup="menu" aria-expanded="false" aria-label="${esc(`More for ${o.name || o.text}`)}" title="More">`
    + '<span class="nav-icon" aria-hidden="true">more_horiz</span></button>' : '<span class="sr-more-slot" aria-hidden="true"></span>';
  const lead = o.icon ? `<span class="sr-then"><span class="nav-icon rule-outcome" aria-hidden="true" data-icon="${esc(o.icon)}"></span></span>` : srThenHTML(o.outcome);
  return `<${tag} class="sr-r${o.builtIn ? ' sr-builtin' : ''}" ${o.attrs}><div class="sr-line">`
    + `<button type="button" class="sr-open" ${how} title="${esc(o.text)}">${lead}<span class="sr-text">${esc(o.text)}</span>${o.after ?? ''}</button>`
    + (marks ? `<span class="sr-marks">${marks}</span>` : '') + more + '</div>'
    + (o.open ? o.below ?? '' : `<div class="sr-detail" id="${esc(det)}" hidden>${o.detail ?? ''}</div>`) + `</${tag}>`;
}
/** A rule's read view: its name, the whole rule, what it says, its cost and why it is not acting. `where` is said first
 * where the list does not (the Rules page's suggestions). */
function stageRuleReadHTML(r: StageRule, where = ''): string {
  return `<p class="sr-d-name">${esc(r.name)}</p>${where ? `<p class="muted">${esc(where)}</p>` : ''}<p>${esc(srSentence(r))}</p>`
    + (r.reason ? `<p class="muted">“${esc(r.reason)}”</p>` : '') + (r.cost && r.cost !== SR_FREE ? `<p class="muted">Cost: ${esc(r.cost)}</p>` : '') + srIdleHTML(r);
}
/** A built-in's row: what it does, and its name in the read view (they sit under "Always on, can't be changed"). */
const builtInRowHTML = (b: StageBuiltIn, key: string, tag: 'li' | 'div' = 'li', attrs = ''): string => stageRuleRowHTML({
  key, tag, attrs, text: b.what || b.name, builtIn: true,
  // where in the code it lives is a developer's detail, not shown
  detail: `<p class="sr-d-name">${esc(b.name)}</p>${b.what ? `<p>${esc(b.what)}</p>` : ''}`});

/** A row's read view, opened or closed from its button; one is open at a time. True when the click was a row's. */
function stageRuleRowToggle(ev: Event): boolean {
  const btn = eventElement(ev)?.closest<HTMLElement>('[data-sr-read]');
  if (!btn) return false;
  const open = btn.getAttribute('aria-expanded') !== 'true';
  for (const other of document.querySelectorAll<HTMLElement>('[data-sr-read][aria-expanded="true"]')) if (other !== btn) srReadShow(other, false);
  srReadShow(btn, open);
  return true;
}
function srReadShow(btn: HTMLElement, open: boolean): void {
  btn.setAttribute('aria-expanded', String(open));
  const det = document.getElementById(btn.getAttribute('aria-controls') ?? '');
  if (det) det.hidden = !open;
  btn.closest('.sr-r')?.classList.toggle('sr-r-open', open);
}
/** Esc inside an open read view (or on its row) closes it, and the row has the focus back; the panel stays. */
function stageRuleRowKey(ev: KeyboardEvent): void {
  if (ev.key !== 'Escape') return;
  const row = eventElement(ev)?.closest<HTMLElement>('.sr-r-open');
  const btn = row?.querySelector<HTMLElement>(':scope > .sr-line > [data-sr-read]');
  if (!btn) return;
  ev.preventDefault(); ev.stopPropagation();
  srReadShow(btn, false);
  btn.focus();
}

/** The two leading marks of a stage's phase lists (Chris, Nov 14: "all the rules have a rule icon, and all the actions
 * have an action icon"): every rule (a stage's Limit too) the board's rules gavel, every action on entering a play mark
 * (never the bolt: that is Jev's, and Automations'), every requirement to leave a done mark, its lock when it can't be
 * pushed past. The Rules page keeps each rule's outcome mark (srThenHTML). Icon subset: ui/vendor/fonts/icons.txt. */
const SR_ROW_ICONS = {rule: 'gavel', act: 'play_arrow', req: 'task_alt', lock: 'lock'};
/** A rule in a stage's phase: one the person may change opens its edit form, with ⋯ holding Edit and Retire; any other
 * opens its read view. A rule that applies beyond the stage has the quiet shared mark. `k` keeps its read view's id
 * unique (the whole pipeline's editor lists a shared rule under each stage). */
/** A stage row's line: what the rule checks, in its own words. Its phase says when (so entering is not said), its
 * action is a mark at the right, and a model's question is the question itself. */
function srStageLine(r: StageRuleCore): string {
  const {when, ifs} = srParts(r), q = (r.jev ?? '').trim();
  const conds = q ? ifs.replace(`Decision model: ${r.jev}`, '').replace(/, $/, '') : ifs;
  const said = [r.event.split(',').includes('enters') ? '' : when, conds, q].filter(Boolean).join(' · ');
  return said ? said.charAt(0).toUpperCase() + said.slice(1) : when;
}
const srRowHTML = (r: StageRule, k: string, shared = ''): string => stageRuleRowHTML({
  key: `${k}-${r.id}`, attrs: `data-sr-rule="${esc(r.id)}"`, icon: SR_ROW_ICONS.rule, text: srStageLine(r), cost: r.cost, costIcon: 'bolt', idle: r.not_acting, name: r.name, shared,
  marks: ruleOutcomeIconHTML(r.then),
  ...(r.can_edit ? {open: `data-sr-edit="${esc(r.id)}"`, more: r.id} : {detail: stageRuleReadHTML(r)})});

/** A rule's ⋯: Edit, then after a line, red and last, Retire. The menu lives in the open dialog when there is one (the
 * page behind a modal dialog takes no clicks), else in the page; the shared menu look (.tl-pop, .tl-menu). Arrow keys
 * move, Esc closes it and the focus goes back to ⋯; a second click on ⋯ closes it. `pick` does what was picked. */
function srMenuOpen(button: HTMLElement, r: StageRule, pick: (what: 'edit' | 'retire', from: HTMLElement) => void): void {
  const host = button.closest('dialog') ?? document.body, id = r.id;
  let pop = host.querySelector<HTMLElement>(':scope > .sr-pop');
  // a second click on ⋯ closes the menu (its press may have closed it already: light dismiss)
  if (popClosedByPress(pop, id)) { if (pop?.matches(':popover-open')) pop.hidePopover(); return; }
  if (pop?.matches(':popover-open')) { const same = pop.dataset['for'] === id; pop.hidePopover(); if (same) return; }
  if (!pop) {
    const made = document.createElement('div');
    made.id = host === document.body ? 'sr-menu-page' : 'sr-menu'; made.className = 'tl-pop sr-pop'; made.popover = 'auto';
    made.addEventListener('keydown', tasksMenuKeys);
    made.addEventListener('toggle', ev => {
      if ((ev as Event & {newState?: string}).newState !== 'closed') return;
      const from = host.querySelector<HTMLElement>(`[data-sr-more="${CSS.escape(made.dataset['for'] ?? '')}"]`);
      from?.setAttribute('aria-expanded', 'false');
      if (from && (!document.activeElement || document.activeElement === document.body || made.contains(document.activeElement))) from.focus();
    });
    host.append(made);
    pop = made;
  }
  const menu = pop;
  menu.dataset['for'] = id;
  menu.innerHTML = `<div class="tl-menu" role="menu" aria-label="${esc(r.name)}"><button type="button" class="tl-mi" role="menuitem" data-sr-pick="edit" data-sr-edit="${esc(id)}">Edit</button>`
    + `<div class="tl-menu-sep" role="separator"></div><button type="button" class="tl-mi sr-mi-danger" role="menuitem" data-sr-pick="retire" data-sr-retire="${esc(id)}">Retire</button></div>`;
  // the menu's own clicks: what is picked, after it closes (its toggle hands the focus to ⋯ first)
  menu.onclick = ev => {
    const b = eventElement(ev)?.closest<HTMLElement>('[data-sr-pick]');
    if (!b) return;
    ev.stopPropagation();
    menu.hidePopover();
    pick(b.dataset['srPick'] === 'retire' ? 'retire' : 'edit', button);
  };
  menu.showPopover();
  button.setAttribute('aria-expanded', 'true');
  popPlace(menu, button);
  // right-aligned to ⋯, the last control on its row
  const bx = button.getBoundingClientRect();
  if (!matchMedia(PHONE).matches) menu.style.left = `${Math.max(8, Math.round(bx.right - menu.offsetWidth))}px`;
  menu.querySelector<HTMLElement>('.tl-mi')?.focus();
}

/** The Rules page's Edit (a row, or its ⋯): the panel's own form for that one rule, in the same dialog. When it is saved,
 * or its Back or Cancel is pressed, the dialog closes and `from` has the focus back; a save reads the page again. */
function stageRuleEdit(r: StageRule, from: HTMLElement, done: () => Promise<void>): void {
  const stage = r.scope === 'stage';
  SR = {pipelineId: r.pipeline_id, stageId: stage ? r.stages[0] ?? '' : '', stageName: stage ? r.stage_names?.[0] ?? '' : '',
    list: {items: [r], built_in: [], may_add: false}, last: r.id,
    host: {done, focus: id => document.querySelector<HTMLElement>(`[data-stage-rule="${CSS.escape(id)}"] .sr-open`) ?? (from.isConnected ? from : null)}};
  srFormOpen(r);
}
/** The Rules page's Retire (a row's ⋯): asks first, retires it at the revision read, reads the page again, and gives the
 * next row the focus (the one before when it was last). */
async function stageRuleRetire(r: StageRule, from: HTMLElement, done: () => Promise<void>): Promise<void> {
  if (!await askConfirm(`Retire "${r.name}"? It stops applying.`, from)) return;
  const rows = [...document.querySelectorAll<HTMLElement>('[data-stage-rule]')].map(e => e.dataset['stageRule'] ?? ''), at = rows.indexOf(r.id);
  const next = rows[at + 1] ?? rows[at - 1] ?? '';
  try {
    await post(`/v2/stage-rules/${encodeURIComponent(r.id)}/retire`, {revision: r.revision});
    toast('Retired');
    SR_BOARD.clear();
    await done();
    if (next) document.querySelector<HTMLElement>(`[data-stage-rule="${CSS.escape(next)}"] .sr-open`)?.focus();
  } catch (e) { toast(ruError(e)); }
}

/** The pipeline's name, for "In every stage of <pipeline>": from its rules, else the pipelines read. */
function srPipelineName(open: Pick<SrOpen, 'pipelineId' | 'list'>): string {
  const fromRule = open.list?.items.find(r => r.pipeline_id === open.pipelineId && r.pipeline_name)?.pipeline_name;
  return fromRule || (typeof TASK_PIPELINES !== 'undefined' && TASK_PIPELINES ? TASK_PIPELINES.find(p => p.id === open.pipelineId)?.name : '') || 'this pipeline';
}

/** The shared mark's words: where a rule applies beyond the stage ('' for the stage's own). */
function srSharedWords(r: StageRule, open: Pick<SrOpen, 'pipelineId' | 'list'>): string {
  if (r.scope === 'company') return 'In every pipeline';
  return r.scope === 'pipeline' ? `In every stage of ${srPipelineName(open)}` : '';
}

/** A phase's rules: a thin line under the phase's fields, the rules acting there (the stage's own, then the shared ones
 * with their quiet mark), then + Rule, quiet, where the viewer may add one and the phase has a When to start on. A phase
 * with no rules shows only its + Rule. On entering has no + Rule of its own: its rules end the phase's one list (Limit,
 * actions, rules), whose + Rule and + Action are the editor's (pipeline-editor.ts; stageRulesAdd). */
function srPhaseRowsHTML(open: Pick<SrOpen, 'pipelineId' | 'stageId' | 'stageName' | 'list'>, phase: SrPhase): string {
  const list = open.list;
  if (!list) return '';
  const rows = list.items.filter(r => srPhaseOf(r.event) === phase);
  const add = list.may_add && phase !== 'enter' && SR_PHASE_EVENTS[phase].length ? '<button type="button" class="ghost pe-add sr-add" data-sr-add>+ Rule</button>' : '';
  return (rows.length ? `<ul class="sr-list">${rows.map(r => srRowHTML(r, `${open.stageId}-${phase}`, srSharedWords(r, open))).join('')}</ul>` : '') + add;
}

/** The end of a stage: one line, "Always on, can't be changed (7)", that opens the built-in rules, read only (they name
 * no When, so they sit in no phase); and, for a viewer who may not change them, who may. */
function srAlwaysHTML(open: Pick<SrOpen, 'stageId' | 'list'>): string {
  const list = open.list;
  if (!list) return '';
  const k = open.stageId, built = list.built_in || [], al = `sr-al-${k}`;
  // listed as they are: More rules (pipeline-editor.ts peMoreHTML) is the line that opens them
  const builtHTML = built.length ? `<section class="sr-group sr-always" data-sr-group="builtin"><p class="sr-always-cap muted">Always on</p>`
    + `<ul class="sr-list" id="${esc(al)}">${built.map((b, i) => builtInRowHTML(b, `${k}-b${i}`)).join('')}</ul></section>` : '';
  const who = list.may_add ? '' : '<p class="muted sr-who">Only the pipeline\'s owner, its manager and Admins change its rules.</p>';
  return builtHTML + who;
}

// ---------------------------------------------------------------- add and edit

/** The Add rule and Edit form's dialog: over the stage editor (or the Rules page), which stays as it was. */
function srDialog(): HTMLDialogElement {
  let dlg = document.getElementById('sr-panel') as HTMLDialogElement | null;
  if (!dlg) {
    dlg = document.createElement('dialog');
    dlg.id = 'sr-panel'; dlg.className = 'ru-modal sr-modal';
    dlg.addEventListener('click', ev => {
      if (ev.target === dlg) { srFormClose(); return; }
      if (eventElement(ev)?.closest('[data-sr-close],[data-sr-back]')) srFormClose();
    });
    dlg.addEventListener('submit', ev => { void srSubmit(ev as SubmitEvent); });
    dlg.addEventListener('change', ev => { srFormSync(srTarget(ev).closest('form')); });
    dlg.addEventListener('input', ev => { srFormSync(srTarget(ev).closest('form')); });
    document.body.appendChild(dlg);
  }
  return dlg;
}

/** Cancel, ✕ or a click outside: the form closes, and the row it was opened from (+ Rule for a new one) has the focus. */
function srFormClose(): void {
  srDialog().close();
  SR?.host.focus(SR.last ?? '')?.focus();
}

function srOptions<K extends string>(pairs: [K, string][], current: string): string {
  return pairs.map(([k, l]) => `<option value="${esc(k)}"${k === current ? ' selected' : ''}>${esc(l)}</option>`).join('');
}

/** The Add rule and Edit form: When, If, Then, and what it costs. Opened from a stage (Chris, Nov 14: "we don't need
 * 'name' or 'on' because its only on this stage anyway"), it has no Name (kept on an edit, made from the rule's words
 * for a new one: srBody) and no On: a new rule is the stage's, an edited one keeps where it applies, and a shared one
 * says where in one quiet line. Elsewhere (the Rules page) Name and On come first, as before. */
function srFormHTML(r: StageRule | null): string {
  const open = SR;
  if (!open) return '';
  const staged = !!open.stageId;
  const on = r ? r.scope : 'stage';
  const shared = staged && r ? srSharedWords(r, open) : '';
  const cond = r?.cond || {};
  const onField = r && r.scope === 'company'
    ? '<p class="muted">Company: every pipeline</p>'
    : `<select name="on">${srOptions<string>([...(open.stageId ? [['stage', `This stage (${open.stageName})`] as [string, string]] : []), ['pipeline', 'The whole pipeline']], on)}</select>`;
  const str = (v: unknown) => (typeof v === 'string' || typeof v === 'number' ? String(v) : '');
  const owner = 'owner_on_team' in cond ? 'on' : 'owner_not_on_team' in cond ? 'not_on' : '';
  const team = str(cond['owner_on_team'] ?? cond['owner_not_on_team']);
  // A new rule from a phase's + Rule offers that phase's When, the first picked. A When of several actions (a default
  // rule's) is kept as it is unless another is picked.
  const phased = !r && open.phase ? SR_PHASE_EVENTS[open.phase] : [];
  const first = r?.event ?? phased[0]?.[0] ?? 'enters';
  const events: [string, string][] = phased.length ? phased
    : SR_EVENTS.some(([k]) => k === first) ? SR_EVENTS : [...SR_EVENTS, [first, srWhen(first)]];
  if (staged) return srStagedFormHTML(r, first, shared);
  return `${srHeadHTML(srFormTitle(r))}
    <form class="ru-form sr-form" data-sr-form="${esc(r?.id ?? '')}" data-sr-revision="${r?.revision ?? ''}">
      ${staged ? (shared ? `<p class="sr-form-shared muted">${srMarkHTML('sr-m-shared', 'dynamic_feed', shared)}${esc(shared)}</p>` : '')
    : `<label class="ru-fl"><span class="ru-lbl">Name</span><input name="name" maxlength="200" required value="${esc(r?.name ?? '')}"></label>
      <div class="ru-fl"><span class="ru-lbl">On</span>${onField}</div>`}
      ${staged ? `<input type="hidden" name="event" value="${esc(first)}">`
    : `<label class="ru-fl"><span class="ru-lbl">When</span><select name="event">${srOptions(events, first)}</select></label>`}
      <div class="ru-fl sr-timer" data-sr-timer><span class="ru-lbl">${staged ? 'After' : 'Timer'}</span><span class="sr-row">
        <input name="hours" type="number" min="1" max="720" value="${esc(str(r?.timer?.hours) || '6')}" aria-label="Hours"> h
        <select name="timer_kind" aria-label="Timer">${srOptions(SR_TIMERS, r?.timer?.kind ?? 'quiet')}</select></span></div>
      <div class="ru-fl"><span class="ru-lbl">If <span class="ru-opt">(optional)</span></span>
        <span class="sr-row"><select name="owner_check" aria-label="The owner's team">${srOptions<string>([['', 'Any owner'], ['on', 'Owner on team'], ['not_on', 'Owner not on team']], owner)}</select>
        <input name="owner_team" maxlength="200" placeholder="A team's id; empty: the manager's team" aria-label="Team" value="${esc(team)}"></span>
        <input name="label" maxlength="200" placeholder="Has label" aria-label="Has label" value="${esc(str(cond['label']))}">
        <textarea name="jev" rows="2" maxlength="1000" placeholder="A question Decision model answers" aria-label="A question Decision model answers">${esc(r?.jev ?? '')}</textarea></div>
      <label class="ru-fl"><span class="ru-lbl">Then</span><select name="then" required>${srOptions(SR_THENS, r?.then ?? 'block')}</select></label>
      <p class="sr-idle" data-sr-thenline hidden></p>
      <label class="ru-fl"><span class="ru-lbl">Says</span><input name="reason" maxlength="1000" value="${esc(r?.reason ?? '')}" placeholder="What the Block or Tell says"></label>
      <p class="sr-costline muted" data-sr-costline></p>
      <p class="ru-status" role="status"></p>
      <div class="ru-foot"><span class="spacer"></span><button class="ghost" type="button" data-sr-back>Cancel</button><button class="primary" type="submit">Save</button></div>
    </form>`;
}

/** A rule's form opened from a stage (Chris, Nov 14: "we need to present this more clearly"; "I don't like the 'when'
 * being in the rule card, i want it in the stages"). The place it was opened from is its When, so each kind shows
 * only what that kind can use, in words:
 *   a snooze rule   "Refuse the snooze when" (the Jev question, in the person's words), then what the bot is told;
 *   on entering     "Refuse an owner who is" not on / on a team (a picker, never an id), then what they are told;
 *   a timer         "After" N h of a clock, "Then" tell or move back, then what it says.
 * Narrowing (a team, a label) is one quiet "Only for…" that opens. What a kind cannot use rides along unseen, so an
 * edit never drops it. The field names are the full form's: srBody and srFormSync read both. */
function srStagedFormHTML(r: StageRule | null, event: string, shared: string): string {
  const cond = r?.cond || {};
  const str = (v: unknown) => (typeof v === 'string' || typeof v === 'number' ? String(v) : '');
  const kind = event === 'timer' ? 'timer' : event.split(',').includes('enters') ? 'enter' : event === 'bot_snoozes' ? 'snooze' : 'other';
  const owner = 'owner_on_team' in cond ? 'on' : 'owner_not_on_team' in cond ? 'not_on' : '';
  const team = str(cond['owner_on_team'] ?? cond['owner_not_on_team']);
  const groups = (S.orgGroups || []).filter(g => g.id).map((g): [string, string] => [String(g.id), String(g.name || g.id)]);
  const teams: [string, string][] = [['', "the manager's team"], ...groups, ...(team && !groups.some(([id]) => id === team) ? [[team, team] as [string, string]] : [])];
  const teamPick = `<select name="owner_team" aria-label="Team">${srOptions(teams, team)}</select>`;
  const label = `<input name="label" maxlength="200" placeholder="Label" aria-label="Has label" value="${esc(str(cond['label']))}">`;
  const hidden = (name: string, value: string) => `<input type="hidden" name="${name}" value="${esc(value)}">`;
  const says = (word: string, hint: string) => `<label class="ru-fl"><span class="ru-lbl">${word}</span><input name="reason" maxlength="1000" value="${esc(r?.reason ?? '')}" placeholder="${esc(hint)}"></label>`;
  // narrowing, shut unless something is set
  const only = (rows: string, set: boolean) => `<details class="sr-only-for"${set ? ' open' : ''}><summary>Only for…</summary><div class="sr-only-rows">${rows}</div></details>`;
  const ownerRow = (words: [string, string][]) => `<span class="sr-row"><select name="owner_check" aria-label="The owner's team">${srOptions<string>(words, owner)}</select>${teamPick}</span>`;
  const narrowOwner = ownerRow([['', 'any owner'], ['on', 'an owner on'], ['not_on', 'an owner not on']]);
  let body: string;
  if (kind === 'timer') {
    body = `<div class="ru-fl sr-timer" data-sr-timer><span class="ru-lbl">After</span><span class="sr-row">
        <input name="hours" type="number" min="1" max="720" value="${esc(str(r?.timer?.hours) || '6')}" aria-label="Hours"> h
        <select name="timer_kind" aria-label="Timer">${srOptions(SR_TIMERS, r?.timer?.kind ?? 'quiet')}</select></span></div>
      <label class="ru-fl"><span class="ru-lbl">Then</span><select name="then" required>${srOptions(SR_THENS, r?.then ?? 'tell_manager')}</select></label>
      ${says('Message', 'What it says')}
      ${hidden('jev', r?.jev ?? '')}
      ${only(narrowOwner + label, !!owner || !!cond['label'])}`;
  } else if (kind === 'enter') {
    body = `<div class="ru-fl"><span class="ru-lbl">Refuse an owner who is</span>
        ${ownerRow([['not_on', 'not on'], ['on', 'on'], ...(owner ? [] : r ? [['', 'anyone at all'] as [string, string]] : [])])}</div>
      ${hidden('then', 'block')}${hidden('jev', r?.jev ?? '')}${hidden('hours', '6')}${hidden('timer_kind', 'quiet')}
      ${says('They are told', 'Why, and what to do instead')}
      ${only(label, !!cond['label'])}`;
  } else {
    body = `<label class="ru-fl"><span class="ru-lbl">${kind === 'snooze' ? 'Refuse the snooze when' : 'Refuse the change when'}</span>
        <textarea name="jev" rows="4" maxlength="1000" placeholder="${kind === 'snooze' ? 'It is not really waiting on a person or another task' : 'Describe it in your own words'}" aria-label="When">${esc(r?.jev ?? '')}</textarea></label>
      ${hidden('then', 'block')}${hidden('hours', '6')}${hidden('timer_kind', 'quiet')}
      ${says(kind === 'snooze' ? 'The bot is told' : 'They are told', 'Why, and what to do instead')}
      ${only(narrowOwner + label, !!owner || !!cond['label'])}`;
  }
  return `${srHeadHTML(srFormTitle(r))}
    <form class="ru-form sr-form sr-staged" data-sr-form="${esc(r?.id ?? '')}" data-sr-revision="${r?.revision ?? ''}">
      ${shared ? `<p class="sr-form-shared muted">${srMarkHTML('sr-m-shared', 'dynamic_feed', shared)}${esc(shared)}</p>` : ''}
      ${hidden('event', event)}
      ${body}
      <p class="sr-idle" data-sr-thenline hidden></p>
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
  // An action When offers Block alone: Tell and Move back need a timer in step 1.
  // A Then the When no longer offers (an old rule that never acted) is never swapped for another in silence: the
  // choice is left empty, and the required select stops Save until the person picks.
  const staged = form.classList.contains('sr-staged');
  // a timer never blocks: from a stage it offers what a timer does
  const thens = timer ? (staged ? SR_THENS.filter(([k]) => k !== 'block') : SR_THENS) : SR_ACTION_THENS, picked = f.then.value, offered = thens.some(([k]) => k === picked);
  const shown = `${offered ? '' : '-,'}${thens.map(([k]) => k).join(',')}`;
  if (f.then instanceof HTMLSelectElement && f.then.dataset['srThens'] !== shown) {
    f.then.dataset['srThens'] = shown;
    f.then.innerHTML = (offered ? '' : `<option value="" selected disabled>${esc(timer ? 'Pick a Then' : 'Pick Block, or a timer for Tell')}</option>`)
      + srOptions(thens, offered ? picked : '');
  }
  // The yellow line shows only when the rule as set would not act: a Then an action cannot carry out (an old rule's,
  // left empty above), or a Jev question on entering or an owner change (stage_rules.owner_check never asks Jev; only
  // a bot's snooze does). A free rule says nothing about its cost.
  const jev = !!f.jev.value.trim();
  const idle = timer ? '' : !f.then.value ? 'Tell and Move back need a timer in step 1: pick N h in the stage, quiet or waiting on a person.'
    : jev && !f.event.value.split(',').includes('bot_snoozes') ? 'A Decision model question is asked only when a bot snoozes: on entering or an owner change this rule never acts.' : '';
  const thenLine = form.querySelector<HTMLElement>('[data-sr-thenline]');
  if (thenLine) {
    thenLine.hidden = !idle;
    thenLine.textContent = idle;
  }
  const line = form.querySelector<HTMLElement>('[data-sr-costline]');
  if (line) {
    line.hidden = !jev || timer;
    line.textContent = jev && !timer ? (staged ? '' : 'Cost: one Decision model call per event') : '';
  }
}

/** From a stage the form has no When: a rule in More rules is named for what it answers, the others plainly. */
function srStagedTitle(r: StageRule | null): string {
  const event = r?.event ?? (SR?.phase ? SR_PHASE_EVENTS[SR.phase][0]?.[0] : '') ?? '';
  return event === 'timer' ? 'Timer' : event === 'bot_snoozes' ? 'Snooze rule' : event === 'owner_changes' ? 'Owner change rule' : r ? 'Edit rule' : 'New rule';
}

/** The form's title: from a stage, plain (its rows show no name either); elsewhere the rule's name, or the stage's. */
const srFormTitle = (r: StageRule | null): string => SR?.stageId ? srStagedTitle(r)
  : r ? `Edit · ${r.name}` : `Add rule · ${SR?.stageName ?? ''}`;

/** Opens the form for a rule (a new one for null) in its dialog, over what it was opened from. */
function srFormOpen(r: StageRule | null): void {
  const dlg = srDialog();
  if (SR) SR.last = r?.id ?? '';
  dlg.setAttribute('aria-label', srFormTitle(r));
  dlg.innerHTML = srFormHTML(r);
  if (!dlg.open) dlg.showModal();
  const form = dlg.querySelector<HTMLFormElement>('form');
  srFormSync(form);
  // its first field has the focus: Name, or (from a stage) When
  form?.querySelector<HTMLElement>('[name=name], select[name=event], input[name=hours]:not([type=hidden]), textarea[name=jev], select[name=owner_check]')?.focus();
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
    name: f.name?.value.trim() ?? editing?.name ?? '', event, cond, jev: f.jev.value.trim(), reason: f.reason.value.trim(),
    timer: event === 'timer' ? {kind: f.timer_kind.value, hours: Number(f.hours.value) || 1} : {},
  };
  body['then'] = f.then.value;  // set apart: an object literal with a `then` key reads as a promise to the linter
  // a new rule from a stage's form has no Name field: the server needs one, so it is the rule's own words
  if (!body['name']) body['name'] = srSentence(body as unknown as StageRuleCore, true).slice(0, 200);
  if (!editing || editing.scope !== 'company') {
    body.pipeline = open.pipelineId;
    // no On field (a stage's form): a new rule is the stage's, an edited one stays where it applies
    const on = f.on?.value ?? editing?.scope ?? 'stage';
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
    // the dialog closes, what it was opened from reads its rules again, and the saved rule's row has the focus (+ Rule
    // when it went to Suggested)
    srDialog().close();
    await open.host.done();
    open.host.focus(saved.status === 'suggested' ? '' : String((saved as {id?: string}).id || id))?.focus();
  } catch (e) {
    if (button) button.disabled = false;
    if (status) status.textContent = errorStatus(e) === 409 ? 'This rule changed since you opened it. Cancel, then open it again.' : ruError(e);
  }
}

/** Retire, from a rule's ⋯ in a stage's phase: asks first, retires it at the revision read, reads the rules again, and
 * the next row in the phase has the focus (the one before when it was last; else the phase's + Rule). A refusal is the
 * stage's one line. */
async function srRetire(r: StageRule, from: HTMLElement): Promise<void> {
  const open = SR;
  if (!open) return;
  if (!await askConfirm(`Retire "${r.name}"? It stops applying.`, from)) return;
  const sec = from.closest<HTMLElement>('[data-sr-section]');
  const at0 = sec?.dataset['srPhase'];
  if (srIsPhase(at0)) open.phase = at0;
  const rows = [...(sec?.querySelectorAll<HTMLElement>('[data-sr-rule]') ?? [])].map(e => e.dataset['srRule'] ?? ''), at = rows.indexOf(r.id);
  const next = rows[at + 1] ?? rows[at - 1] ?? '';
  try {
    await post(`/v2/stage-rules/${encodeURIComponent(r.id)}/retire`, {revision: r.revision});
    toast('Retired');
    srBoardLoad(open.pipelineId, true);
    await open.host.done();
    open.host.focus(next)?.focus();
  } catch (e) {
    if (SR_ED) SR_ED.note = ruError(e);
    srSectionsPaint();
  }
}
