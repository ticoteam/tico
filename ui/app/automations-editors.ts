// biome-ignore-all lint/suspicious/noThenProperty: `then` is the hub's key for a rule's or a destination's actions (CONTRACT §2); these are data records, never awaited
/* ui/app/automations-editors.ts — The editor beside the flow (a sheet on a phone): the trigger, each step type, the
   spend cap and the versions, with a bot's proposed changes above them
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   TypeScript: npm run build:ui compiles it to build/ui/app/automations-editors.js, which the page runs in this file's place.
   An editor works on a copy; Save writes the whole flow back (PATCH, CONTRACT §2), which makes a new version. Fields
   bind by path (data-b="step.rules.0.when.1.value") into AU.ed; adding or removing a row redraws the editor. With
   can_edit false every field is shown off and nothing saves. */
'use strict';

const AU_LEVELS: Array<[string, string]> = [['low', 'Low'], ['medium', 'Medium'], ['high', 'High']];
const AU_QTYPES: Array<[string, string]> = [['choice', 'Choice'], ['noul', 'Yes/no'], ['score', 'Score']];
const AU_TRIGGER_FIELDS: Record<string, string[]> = {message: ['message.from', 'message.to', 'message.subject', 'message.body'],
  task_received: ['task.title', 'task.body', 'task.from', 'task.tag'], listening: ['item.title', 'item.score', 'item.source'],
  event: ['event.name'], webhook: ['payload.type'], schedule: [], due: ['due.due_on', 'due.repeats']};

// A card's frame options (auFrame): its tag or icon, whether it saves, deletes, and what those buttons say.
interface AuFrameOpts {
  tag?: string;
  icon?: string | undefined;
  save?: boolean;
  del?: boolean;
  label?: string;
  delOp?: string;
  delLabel?: string;
}
// The form controls the editor's events come from.
type AuControl = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;

function auEdOpen(ed: AuEditor): void {
  auMenuClose();
  AU.ed = ed;
  if (ed.kind === 'step' && ed.step?.type === 'decide') auDecideQs(ed);
  if (ed.kind === 'step' && ed.step?.type === 'filter') for (const r of ed.step.rules || []) r.then = (r.then || []).map((x): AuThen => typeof x === 'string' ? {do: x, args: {}} : x);
  if (ed.kind === 'spend') ed.value = AU.cur?.spend_cap?.per_30m ?? '';   // before drawing: a blank field would save no limit
  auDrawPane(); auDrawSide();
  if (ed.kind === 'versions') void auVersionsLoad(ed);
  if (ed.kind === 'step' && ed.step?.type === 'decide') void auLastRun(ed);
  if (ed.kind === 'step' && ed.step?.type === 'decide' && AU.sets === null) void auSetsLoad(ed);
  if (ed.kind === 'step' && ed.step?.type === 'bot_turn' && !TASK_RUN_ROWS) void auModelsLoad(ed);
  // A phone would open its keyboard over the sheet; a computer starts in the first field.
  if (!auPhone()) {
    const first = AU.root.querySelector('.au-ed .au-edb :is(input,textarea,select):not([disabled])');
    if (first instanceof HTMLElement) first.focus({preventScroll: true});
  }
  AU.root.querySelector('.au-ed .au-rb.focus')?.scrollIntoView({block: 'nearest'});
}
function auEdOpenStep(id: string | undefined, path: string | null | undefined): void {
  if (!AU.cur) return;
  if (id === 'trigger') { auEdOpen({kind: 'trigger', trigger: auClone(AU.cur.trigger ?? {})}); return; }
  const hit = auFind(AU.cur.steps, id);
  if (hit) auEdOpen({kind: 'step', id, step: auClone(hit.step), path: path ?? null});
}
function auEdClose(): void { if (!AU?.ed) return; AU.ed = null; auDrawPane(); auDrawSide(); }
// The open trigger editor's copy (a trigger editor always has one).
function auEdTrigger(ed: AuEditor): AuTrigger { ed.trigger ??= {}; return ed.trigger; }

// A Decide step's questions as rows: inline ones, else the saved set's (read-only until one is changed, which makes
// them the step's own, "Custom").
function auDecideQs(ed: AuEditor): void {
  const s = ed.step;
  if (!s) return;
  const qs: Record<string, AuQuestion> = s.questions || auQuestionMap(AU.sets?.find(x => x.id === s.set)?.questions);
  ed.qs = Object.entries(qs).map(([id, q]) => ({...q, id, ask: auAskText(q.instructions), opts: q.type === 'choice' ? auChoiceKeys(q).join(', ') : ''}));
  ed.custom = !s.set;
}
// A saved set's questions as the page reads them: each one a question, or an empty one when the server sent another shape.
function auQuestionMap(qs: unknown): Record<string, AuQuestion> {
  const out: Record<string, AuQuestion> = {};
  if (auIsRecord(qs)) for (const [id, q] of Object.entries(qs)) out[id] = auIsRecord(q) ? auQuestionOf(q) : {};
  return out;
}
// A question with every field the server sent (each one unknown until a field is read).
function auQuestionOf(raw: Record<string, unknown>): AuQuestion {
  const q: AuQuestion = {};
  for (const [k, v] of Object.entries(raw)) q[k] = v;
  return q;
}
// A question's instructions as one line of text (structured ones as their JSON; saved back unchanged if untouched).
const auAskText = (v: unknown): string => typeof v === 'string' ? v : v == null ? '' : JSON.stringify(v);
const auChoiceKeys = (q: AuQuestion): string[] => Array.isArray(q.criteria) ? q.criteria.map(String) : Object.keys(q.criteria || {});
async function auSetsLoad(ed: AuEditor): Promise<void> {
  AU.sets = [];
  try { AU.sets = (await get<import('../types/api').QuestionSets>('/v2/automations/question-sets')).sets || []; } catch { /* the select keeps the step's own set */ }
  if (AU?.ed === ed) { if (!ed.custom && !(ed.qs ?? []).length) auDecideQs(ed); auDrawSide(); auDrawPane(); }
}
// The last run's answers for this Decide step: one bar each.
async function auLastRun(ed: AuEditor): Promise<void> {
  if (AU.lastRun === undefined) {
    AU.lastRun = null;
    const id = AU.cur?.id;
    if (id) {
      try { AU.lastRun = (await get<import('../types/api').AutomationRuns>(`${auApi(id)}/runs?limit=1`)).runs?.[0] || null; } catch { /* no bars */ }
    }
    if (AU?.ed === ed) auDrawSide();
  }
}

const auOpt = (list: Array<[string, string]>, val: unknown): string => list.map(([k, label]) => `<option value="${esc(k)}"${String(k) === String(val ?? '') ? ' selected' : ''}>${esc(label)}</option>`).join('');
const auWithCur = (list: Array<[string, string]>, val: unknown, label?: unknown): Array<[string, string]> => val && !list.some(([k]) => k === val) ? [[String(val), String(label || val)], ...list] : list;
const auBots = (): Array<[string, string]> => (S.emps || []).filter(e => e.id !== AU_BOT && e.status !== 'archived').map((e): [string, string] => ['bot:' + e.id, botDisplayName(e.id)]);
const auPeople = (): Array<[string, string]> => (S.people || []).map((p): [string, string] => ['person:' + p.id, p.name || p.id]);
function auFld(label: string, control: string, cls = ''): string { return `<label class="au-f${cls ? ' ' + cls : ''}"><span class="au-lb">${esc(label)}</span>${control}</label>`; }
function auBox(label: string, body: string): string { return `<div class="au-f"><span class="au-lb">${esc(label)}</span>${body}</div>`; }
const auRO = (): boolean => !AU.cur?.can_edit;
const auDis = (): string => auRO() ? ' disabled' : '';
const auIn = (b: string, v: unknown, ph = '', extra = ''): string => `<input type="text" data-b="${b}" value="${esc(v ?? '')}"${ph ? ` placeholder="${esc(ph)}"` : ''}${auDis()} ${extra}>`;
const auSel = (b: string, list: Array<[string, string]>, v: unknown, extra = ''): string => `<select data-b="${b}"${auDis()} ${extra}>${auOpt(list, v)}</select>`;
const auTa = (b: string, v: unknown, rows = 8): string => `<textarea data-b="${b}" rows="${rows}" spellcheck="true"${auDis()}>${esc(v ?? '')}</textarea>`;
const auX = (at: string, i: number, label = 'Remove'): string => auRO() ? '' : `<button class="au-x" type="button" data-op="rm" data-at="${at}" data-i="${i}" aria-label="${esc(label)}">✕</button>`;
const auPlus = (at: string, tpl: string, label: string): string => auRO() ? '' : `<button class="linkish au-plus" type="button" data-op="add" data-at="${at}" data-tpl="${tpl}">+ ${esc(label)}</button>`;

// The fields a condition can test: the trigger's, and every Decide or LLM output in the flow.
function auFieldsList(): string[] {
  const out: string[] = [...(AU_TRIGGER_FIELDS[AU.cur?.trigger?.type ?? ''] || [])];
  function walk(steps: AuStep[] | null | undefined): void {
    for (const s of steps || []) {
      if (s.type === 'decide' && s.out) for (const q of auQuestionIds(s)) out.push(`${s.out}.${q}`);
      if (s.type === 'llm' && s.out) out.push(s.out);
      if (s.type === 'paths') {
        const branches: AuPath[] = [...(s.paths || []), s.otherwise || {}];
        for (const p of branches) walk(p.steps);
      }
    }
  }
  walk(AU.cur?.steps);
  const ed = AU.ed, step = ed?.step;
  if (step?.type === 'decide' && step.out) for (const q of ed?.qs ?? []) out.push(`${step.out}.${q.id}`);
  return [...new Set(out)];
}
function auCondRows(at: string, when: AuCond[] | null | undefined, min = true): string {
  return (when || []).map((c, j) => `<div class="au-cond-row${min ? '' : ' nomin'}">
    ${auIn(`${at}.${j}.field`, c.field, 'Field', 'list="au-fields" aria-label="Field"')}
    ${auSel(`${at}.${j}.op`, Object.entries(min ? AU_OPS : AU_BOARD_OPS), c.op || 'is', 'aria-label="Test"')}
    ${auIn(`${at}.${j}.value`, c.value, 'Value', 'aria-label="Value"')}
    ${min ? `<span class="au-pct"><input type="number" data-b="${at}.${j}.min" data-pct min="0" max="100" step="5" value="${c.min != null && c.min !== '' ? Math.round(Number(c.min) * 100) : ''}" placeholder="Min" aria-label="Minimum confidence, percent"${auDis()}></span>` : ''}
    ${auX(at, j, 'Remove condition')}</div>`).join('');
}

function auEdHTML(): string {
  const ed = AU.ed;
  if (!ed || !AU.cur) return '';
  if (ed.kind === 'versions') return auFrame('Versions', auVersionsHTML(ed), {save: false});
  if (ed.kind === 'spend') return auFrame('Spend limit', auFld('$ per 30 min',
    `<input type="number" data-b="value" min="0" step="0.10" value="${esc(ed.value ?? '')}" placeholder="Bot’s limit" inputmode="decimal">`)
    + `<button class="linkish au-plus" type="button" data-op="nocap">Use the bot’s limit</button>`, {});
  if (ed.kind === 'trigger') {
    const t = auEdTrigger(ed);
    return auFrame('Trigger', auTriggerFields(t, ed), {icon: AU_TRIGGER_ICONS[t.type ?? '']});
  }
  if (ed.kind === 'run') return auFrame('Test run', auFld('Sample input', auTa('text', ed.text, 9), 'au-mono'), {icon: 'play_arrow', label: 'Run'});
  if (ed.kind === 'board') return auBoardEdHTML(ed);
  const s = ed.step;
  if (!s) return '';
  return auFrame(auStepTitle(s), auStepBody(s, ed), {tag: s.type, del: !ed.insert});
}
// A step's own fields, by its type (a type with none draws nothing).
function auStepBody(s: AuStep, ed: AuEditor): string {
  switch (s.type) {
    case 'filter': return auFilterFields(s);
    case 'decide': return auDecideFields(s, ed);
    case 'llm': return auLlmFields(s);
    case 'find': return auFindFields(s);
    case 'bot_turn': return auBotFields(s);
    case 'paths': return auPathsFields(s, ed);
    case 'action': return auActionFields(s);
    default: return '';
  }
}
function auFrame(title: string, body: string, {tag = '', icon = '', save = true, del = false, label = 'Save', delOp = 'del', delLabel = 'Delete step'}: AuFrameOpts = {}): string {
  const ro = auRO();
  return `<div class="au-grab" aria-hidden="true"></div>
    <div class="au-edh">${tag ? auTag(tag) : icon ? `<span class="nav-icon" aria-hidden="true">${icon}</span>` : ''}<h3>${esc(title)}</h3>
      <span class="au-sp"></span><button class="au-x" type="button" data-op="close" aria-label="Close">✕</button></div>
    <form class="au-edb" novalidate>${body}<datalist id="au-fields">${auFieldsList().map(f => `<option value="${esc(f)}">`).join('')}</datalist></form>
    ${save && !ro ? `<div class="au-edf">${del ? `<button class="linkish au-del" type="button" data-op="${delOp}">${esc(delLabel)}</button>` : ''}<span class="au-sp"></span>
      <button class="primary au-save" type="button" data-op="save">${esc(label)}</button></div>` : ''}`;
}

// An event trigger's event: the catalog in words, or Custom… with the exact name typed (custom.<x>, any name the
// catalog does not list). A built-in's tico.builtin.* stays its words.
const AU_EV_CUSTOM = '__custom';
const auEvCustom = (t: AuTrigger, ed: AuEditor): boolean => ed.evCustom ?? (!!t.name && !AU_EVENTS[t.name] && !AU_EVENT_MORE[t.name] && !String(t.name).startsWith('tico.builtin.'));
function auEventField(t: AuTrigger, ed: AuEditor): string {
  const custom = auEvCustom(t, ed);
  const list: Array<[string, string]> = [...auWithCur(Object.entries(AU_EVENTS), custom ? '' : t.name, auEventWords(t.name)), [AU_EV_CUSTOM, 'Custom…']];
  const pick = auSel('evpick', list, custom ? AU_EV_CUSTOM : t.name, `aria-label="Event"${t.name && !custom ? ` title="${esc(t.name)}"` : ''}`);
  return auFld('Event', pick + (custom ? auIn('trigger.name', t.name, 'custom.name', 'aria-label="Custom event"') : ''));
}
function auTriggerFields(t: AuTrigger, ed: AuEditor): string {
  const src: Record<string, () => string> = {
    message: () => auFld('Inbox', auIn('trigger.inbox', t.inbox)),
    task_received: () => auFld('To', auSel('trigger.to', auWithCur([...auPeople(), ...auBots()], t.to, auActorName(t.to)), t.to)),
    schedule: () => auFld('Cron', auIn('trigger.cron', t.cron, '0 7 * * 1-5')) + auFld('Time zone', auIn('trigger.tz', t.tz)),
    webhook: () => auFld('Webhook', `<input type="text" value="${esc(t.name || t.id || '')}" disabled>`),
    listening: () => auFld('Destination', auIn('trigger.destination', t.destination)),
    event: () => auEventField(t, ed),
    due: () => auFld('First due', `<input type="date" data-b="trigger.first_due" value="${esc(t.first_due || '')}"${auDis()}>`)
      + auFld('Repeats', auSel('trigger.repeats', AU_REPEATS, t.repeats || 'yearly'))
      + auBox('Open task', `<span class="au-ir au-days"><input type="number" data-b="trigger.open_days" data-num min="0" max="366" step="1" value="${esc(t.open_days ?? 14)}" aria-label="Days before"${auDis()}><span class="muted">days before</span></span>`)
      + auFld('Time zone', auIn('trigger.tz', t.tz)),
  };
  // When: a schedule, an event or a due date (a kind made elsewhere, such as a message, shows as itself).
  const when = auFld('When', auSel('ttype', auWithCur(AU_WHEN, t.type, AU_TRIGGER_WORDS[t.type ?? ''] || t.type), t.type));
  const kind = src[t.type ?? ''];
  return when + (kind ? kind() : '')
    + auBox('Filters', auCondRows('trigger.filters', t.filters) + auPlus('trigger.filters', 'cond', 'Filter'));
}
function auFilterFields(s: AuStep): string {
  return auBox('Rules', (s.rules || []).map((r, i) => `<div class="au-rb">
    <div class="au-rbh"><span class="muted">Rule ${i + 1}</span>${auSel(`step.rules.${i}.match`, [['all', 'All of'], ['any', 'Any of']], r.match || 'all', 'aria-label="Match"')}
      <span class="au-sp"></span>${auX('step.rules', i, 'Remove rule')}</div>
    ${auCondRows(`step.rules.${i}.when`, r.when)}${auPlus(`step.rules.${i}.when`, 'cond', 'Condition')}
    <div class="au-then"><span class="nav-icon" aria-hidden="true">arrow_forward</span>${(r.then || []).map((x, j) =>
      `<span class="au-ir">${auSel(`step.rules.${i}.then.${j}.do`, Object.entries(AU_ACTIONS), auThenDo(x), 'aria-label="Then"')}${(r.then || []).length > 1 ? auX(`step.rules.${i}.then`, j) : ''}</span>`).join('')}
      ${auPlus(`step.rules.${i}.then`, 'then', 'Action')}</div>
    <label class="au-chk"><input type="checkbox" data-b="step.rules.${i}.stop"${auRuleNext(r) === 'end' ? ' checked' : ''}${auDis()}>Stop here</label></div>`).join('')
    + auPlus('step.rules', 'rule', 'Rule'));
}
function auDecideFields(s: AuStep, ed: AuEditor): string {
  const sets = auWithCur((AU.sets || []).map((x): [string, string] => [x.id, auSetWords(x.id)]), s.set, auSetWords(s.set));
  const rows = ed.qs ?? [];
  const qs = rows.map((q, i) => `<div class="au-rb"><div class="au-ir">
      ${auIn(`qs.${i}.id`, q.id, 'Name', 'aria-label="Question name"')}${auSel(`qs.${i}.type`, AU_QTYPES, q.type, 'data-redraw aria-label="Answer"')}${auX('qs', i, 'Remove question')}</div>
      ${auIn(`qs.${i}.ask`, q.ask, 'What to ask', 'aria-label="Instructions"')}
      ${q.type === 'choice' ? auIn(`qs.${i}.opts`, q.opts, 'Options, comma separated', 'aria-label="Options"') : ''}</div>`).join('');
  const run = (AU.lastRun?.steps || []).find(x => x.id === s.id);
  const outputs = run ? auStepView(run).outputs : {};
  const bars = run ? Object.entries(outputs).filter((e): e is [string, Record<string, unknown>] => auIsRecord(e[1])).map(([q, v]) => {
    const {text, p} = auAnswer(q, v, rows.find(x => x.id === q));
    return `<div class="au-eb"><span>${esc(text)}</span><span class="au-bar"><i style="width:${Math.round(Math.max(0, Math.min(1, p)) * 100)}%"></i></span></div>`;
  }).join('') : '';
  return auFld('Question set', auSel('set', [['', 'Custom'], ...sets], ed.custom ? '' : s.set, 'data-redraw'))
    + auFld('Level', auSel('step.level', AU_LEVELS, s.level || 'medium'))
    + auBox('Questions', qs + auPlus('qs', 'q', 'Question'))
    + (bars ? auBox('Last run', bars) : '');
}
// One judge answer (CONTRACT §2: choice + confidence + probabilities, score, or yes/no) as words and a 0..1 bar.
function auAnswer(qid: string, v: Record<string, unknown>, q?: AuQuestion): {text: string; p: number} {
  const p: Record<string, unknown> = auIsRecord(v.probabilities) ? v.probabilities : {};
  if (v.score != null) { const max = Number(q?.max ?? v.max ?? 3) || 3; return {text: `${qid} ${Number(v.score).toFixed(1)} of ${max}`, p: Number(v.score) / max}; }
  const yes: unknown = v.noul != null ? Number(v.noul) : p.yes;
  if (yes != null && (v.noul != null || Object.keys(p).every(k => k === 'yes' || k === 'no'))) return {text: `${qid} ${auPct(yes)}`, p: Number(yes)};
  const conf = Number(v.confidence ?? p[String(v.choice)] ?? 0);
  return {text: `${v.choice ?? qid} ${auPct(conf)}`, p: conf};
}
function auLlmFields(s: AuStep): string {
  const mine = 'assistant:' + (S.me?.id || '');
  return auFld('Runs as', auSel('step.as', auWithCur([[mine, auActorName(mine)], ...auBots()], s.as, auActorName(s.as)), s.as))
    + auFld('Prompt', auTa('step.prompt', s.prompt));
}
function auFindFields(s: AuStep): string {
  return auFld('Pulls', auSel('step.source', Object.entries(AU_FIND), s.source))
    + auFld('Query', auIn('step.query', s.query, 'Optional'))
    + `<div class="au-two">${auFld('Since', auSel('step.since', [['last_run', 'Last run'], ['', 'Any time']], s.since || ''))}
      ${auFld('Limit', `<input type="number" data-b="step.limit" data-num min="1" max="200" value="${esc(s.limit ?? '')}"${auDis()}>`)}</div>`;
}
function auBotFields(s: AuStep): string {
  const bot = s.bot ? 'bot:' + String(s.bot).replace(/^bot:/, '') : '';
  return auFld('Bot', auSel('step.bot', auWithCur(auBots(), bot, auActorName(bot)), bot, 'data-slug'))
    + auFld('Prompt', auTa('step.prompt', s.prompt)) + auBotRunsFields(s);
}
// Runs on: the model the turn's task runs on, stamped on the task its run opens (backend/task_run_models.py). Blank is the
// bot's task model; the harness and effort follow the model picked, and a blank one is left out of the step.
function auBotRunsFields(s: AuStep): string {
  const rows: AuRunModel[] = TASK_RUN_ROWS || [];
  const model = s.model || '', row = rows.find(m => m.id === model);
  const models = auWithCur([['', "The bot's task model"], ...rows.map((m): [string, string] => [m.id, m.label || m.id])], model, model);
  const hs = row?.harnesses || [];
  const harnesses: Array<[string, string]> = hs.length > 1 ? hs.map((h): [string, string] => [h, harnessWords(h) || h]) : [];
  return auFld('Runs on', auSel('step.model', models, model, 'data-blank data-redraw'))
    + (harnesses.length ? auFld('Harness', auSel('step.harness', harnesses, s.harness || hs[0], 'data-blank')) : '')
    + (row?.efforts?.length ? auFld('Effort', auSel('step.effort', [['', 'Default'], ...row.efforts.map((e): [string, string] => [e, e])], s.effort || '', 'data-blank')) : '');
}
// The models the Runs on field lists (the task picker's catalog, read once); the editor draws again when they arrive.
async function auModelsLoad(ed: AuEditor): Promise<void> {
  await taskRunCatalog();
  if (AU.ed === ed) auDrawSide();
}
function auActionFields(s: AuStep): string {
  const a: Record<string, unknown> = s.args || {};
  const who = [...auPeople(), ...auBots()];
  const args: Record<string, () => string> = {
    create_task: () => auFld('Title', auIn('step.args.title', a.title))
      + auFld('Assign to', auSel('step.args.owner', [['', 'Owner'], ...auWithCur(who, a.owner, auActorName(a.owner))], a.owner || '', 'data-blank')),
    assign: () => auFld('Assign to', auSel('step.args.owner', auWithCur(who, a.owner, auActorName(a.owner)), a.owner || '')),
    wake_bot: () => auFld('Bot', auSel('step.args.bot', auWithCur(auBots(), a.bot, auActorName(a.bot)), a.bot || '')),
    forward: () => auFld('To', auIn('step.args.to', auArg('forward', a), 'name@acme.example')),
    label: () => auFld('Label', auIn('step.args.name', auArg('label', a))),
    note: () => auFld('Text', auTa('step.args.text', auArg('note', a), 4)),
    post_update: () => auFld('Text', auTa('step.args.text', auArg('post_update', a), 4)),
    call_webhook: () => auFld('Webhook', auIn('step.args.webhook', auArg('call_webhook', a))),
  };
  const fields = args[s.do ?? ''];
  return auFld('Action', auSel('step.do', Object.entries(AU_ACTIONS), s.do, 'data-redraw')) + (fields ? fields() : '');
}
function auPathsFields(s: AuStep, ed: AuEditor): string {
  return auBox('Paths', (s.paths || []).map((p, i) => `<div class="au-rb${String(ed.path) === String(i) ? ' focus' : ''}" data-path-ed="${i}">
      <div class="au-ir">${auIn(`step.paths.${i}.name`, p.name, 'Name', 'aria-label="Path name"')}${(s.paths || []).length > 1 ? auX('step.paths', i, 'Remove path') : ''}</div>
      ${auCondRows(`step.paths.${i}.when`, p.when)}${auPlus(`step.paths.${i}.when`, 'cond', 'Condition')}</div>`).join('')
    + `<div class="au-rb fb"><div class="au-ir"><input type="text" value="Otherwise" disabled aria-label="Path name"></div><span class="muted">Nothing else</span></div>`
    + ((s.paths || []).length < AU_MAX_PATHS ? auPlus('step.paths', 'path', 'Path') : ''));
}

// Bound fields: a path into AU.ed; "set" picks a saved question set (or Custom).
function auPathGet(root: unknown, path: string): unknown {
  return path.split('.').reduce<unknown>((o, k) => auIsRecord(o) ? o[k] : undefined, root);
}
// Writes a bound field. A path through a primitive throws a TypeError, as an assignment into one always did.
function auPathSet(root: AuEditor, path: string, v: unknown): void {
  const keys = path.split('.'), last = keys.pop() ?? '';
  let o: unknown = root;
  for (let n = 0; n < keys.length; n++) {
    const k = keys[n] ?? '';
    if (!auIsRecord(o)) throw new TypeError(`Cannot set ${path}`);
    o[k] ??= /^\d+$/.test(keys[n + 1] ?? last) ? [] : {};
    o = o[k];
  }
  if (!auIsRecord(o)) throw new TypeError(`Cannot set ${path}`);
  if (v === undefined) delete o[last]; else o[last] = v;
}
// The form control an event came from, or null for any other element.
function auControl(el: Element | null): AuControl | null {
  return el instanceof HTMLInputElement || el instanceof HTMLSelectElement || el instanceof HTMLTextAreaElement ? el : null;
}
// Wired once on the editor's box (it outlives each drawing); every handler reads the editor open at that moment.
function auEdWire(box: HTMLElement): void {
  const onField = (e: Event): void => {
    const t = auControl(eventElement(e));
    if (!t) return;
    const ed = AU?.ed;
    if (ed?.kind === 'board' && auBoardField(t, e, ed)) return;
    const b = t.dataset.b;
    if (!b || !ed) return;
    let v: unknown = t instanceof HTMLInputElement && t.type === 'checkbox' ? t.checked : t.value;
    if (t.hasAttribute('data-pct')) v = v === '' ? undefined : Math.max(0, Math.min(100, Number(v))) / 100;
    else if (t.hasAttribute('data-num')) v = v === '' ? undefined : Number(v);
    else if (t.hasAttribute('data-blank')) v = v === '' ? undefined : v;
    else if (t.hasAttribute('data-slug')) v = String(v).replace(/^bot:/, '');
    if (b === 'evpick') {
      if (e.type !== 'change') return;
      const tr = auEdTrigger(ed), pick = String(v);
      ed.evCustom = pick === AU_EV_CUSTOM;
      if (!ed.evCustom) tr.name = pick;
      else if (AU_EVENTS[tr.name ?? ''] || AU_EVENT_MORE[tr.name ?? '']) tr.name = '';
      auDrawSide();
      const nameField = box.querySelector('[data-b="trigger.name"]');
      if (ed.evCustom && nameField instanceof HTMLElement) nameField.focus();
      return;
    }
    if (b === 'ttype') {
      // Another kind of trigger starts fresh (its filters test another kind's fields); back to the saved kind restores it.
      if (e.type !== 'change') return;
      ed.saved ??= auClone(AU.cur?.trigger ?? {});
      ed.trigger = v === ed.saved.type ? auClone(ed.saved) : auNewTrigger(String(v));
      ed.evCustom = undefined;
      auDrawSide();
      return;
    }
    if (b === 'set') {
      ed.custom = !v;
      if (v) {
        if (ed.step) { ed.step.set = String(v); delete ed.step.questions; }
        auDecideQs(ed);
      }
      auDrawSide();
      return;
    }
    if (b.startsWith('qs.')) ed.custom = true;
    auPathSet(ed, b, v);
    // An action's text edited under its own name: the other names the hub reads first go, so the edit is what runs.
    const argAt = /^(.*)\.args\.([a-z_]+)$/.exec(b);
    const actRaw: unknown = argAt ? auPathGet(ed, argAt[1] ?? '') : undefined;
    const act = auIsRecord(actRaw) ? actRaw : null;
    const names = act && typeof act.do === 'string' ? AU_ARG_NAMES[act.do] : undefined;
    const actArgs = act?.args;
    if (argAt && names && names.key === argAt[2] && auIsRecord(actArgs)) for (const k of [...names.keys, ...(names.drop || [])]) if (k !== names.key) delete actArgs[k];
    if (argAt && act) auArgsMark(act.args, argAt[2] ?? '');
    // Stop here changes where a rule goes: a rule that names its next gets the new one (a rule that moved on, unstopped,
    // goes to the next step); a rule that doesn't goes by stop alone.
    const stopAt = /^step\.rules\.(\d+)\.stop$/.exec(b);
    const sr = stopAt ? ed.step?.rules?.[Number(stopAt[1])] : undefined;
    if (sr && 'next' in sr) sr.next = v ? 'end' : sr.next === 'end' ? 'step' : sr.next;
    // Another model has its own harnesses and efforts: the ones kept for the last model do not carry over.
    if (b === 'step.model') { auPathSet(ed, 'step.harness', undefined); auPathSet(ed, 'step.effort', undefined); }
    if (t.hasAttribute('data-redraw') && e.type === 'change') { if (b === 'step.do' && ed.step) ed.step.args = {}; auDrawSide(); }
  };
  box.addEventListener('input', onField);
  box.addEventListener('change', onField);
  box.addEventListener('submit', e => { e.preventDefault(); void auEdSave(); });
  box.addEventListener('click', e => {
    const hit = eventElement(e)?.closest('[data-op]');
    const ed = AU?.ed;
    if (!(hit instanceof HTMLElement) || !ed) return;
    const op = hit.dataset.op ?? '', btn = hit instanceof HTMLButtonElement ? hit : null;
    if (op === 'close') { auEdClose(); return; }
    if (op === 'save') { void auEdSave(btn); return; }
    if (op === 'del') { void auStepDelete(ed.id); return; }
    if (op === 'bdel') { void auBoardDelete(ed, btn); return; }
    if (op === 'bmode') { if (ed.item && hit.dataset.v !== undefined) ed.item.mode = hit.dataset.v; auDrawSide(); return; }
    if (op === 'nocap') { void auSpendSave(null); return; }
    if (op === 'restore') { void auRestore(Number(hit.dataset.n)); return; }
    if (['diff', 'prop-on', 'discard'].includes(op)) { void auPropAct(op, Number(hit.dataset.n), btn); return; }
    const at = hit.dataset.at ?? '', found = auPathGet(ed, at);
    const list: unknown[] = Array.isArray(found) ? found : [];
    if (op === 'rm') list.splice(Number(hit.dataset.i), 1);
    if (op === 'add') {
      if (!found) auPathSet(ed, at, list);
      const made: Record<string, unknown> = {cond: {field: '', op: 'is', value: ''}, then: {do: 'archive', args: {}}, bact: auBoardNewAct(), q: {id: 'question', type: 'noul', ask: '', opts: ''},
        rule: {when: [{field: '', op: 'is', value: ''}], match: 'all', then: [{do: 'archive', args: {}}], stop: true},
        path: {name: 'Path ' + (list.length + 1), when: [], steps: []}};
      list.push(made[hit.dataset.tpl ?? '']);
    }
    if (at === 'qs') ed.custom = true;
    const scroll = box.querySelector('.au-edb')?.scrollTop || 0;
    auDrawSide();
    const body = box.querySelector('.au-edb'); if (body) body.scrollTop = scroll;
  });
}

async function auEdSave(button: HTMLButtonElement | null = null): Promise<void> {
  const ed = AU.ed, a = AU.cur;
  if (!ed || !a || !a.can_edit) return;
  if (ed.kind === 'spend') { const v = String(ed.value ?? '').trim(); await auSpendSave(v === '' ? null : Number(v)); return; }
  if (ed.kind === 'run') { await auRunNow(ed, button); return; }
  if (ed.kind === 'board') { await auBoardEdSave(ed, button); return; }
  if (button) button.disabled = true;
  let body: unknown;
  if (ed.kind === 'trigger') {
    const tr = auEdTrigger(ed);
    let trigger: AuTrigger = tr.type === 'event' ? {...tr, name: String(tr.name || '').trim()} : tr;
    if (tr.type === 'event' && !trigger.name) { toast('Name the event', true); if (button) button.disabled = false; return; }
    if (tr.type === 'due') {
      const days = Number(trigger.open_days ?? 14);
      const bad = !/^\d{4}-\d{2}-\d{2}$/.test(String(trigger.first_due || '')) ? 'Pick the first due date'
        : !(Number.isInteger(days) && days >= 0 && days <= 366) ? 'Days before is a whole number from 0 to 366' : '';
      if (bad) { toast(bad, true); if (button) button.disabled = false; return; }
      trigger = {...trigger, open_days: days, tz: String(trigger.tz || '').trim() || auZone()};
    }
    body = {trigger};
  }
  else {
    const s = ed.step;
    if (!s) { if (button) button.disabled = false; return; }
    const step = auClone(s);
    if (step.type === 'decide' && ed.custom) {
      const old = step.questions || {}, kept = (ed.qs ?? []).filter(q => q.id.trim());
      if (kept.some(q => !String(q.ask ?? '').trim())) { toast('Each question needs what to ask', true); if (button) button.disabled = false; return; }
      step.questions = Object.fromEntries(kept.map(({id, opts, ask, ...q}): [string, AuQuestion] => {
        const def = {...(old[id] || {}), ...q}, was = old[id]?.instructions;
        def.instructions = typeof was !== 'string' && was != null && ask === auAskText(was) ? was : String(ask).trim();
        if (def.type === 'choice') {
          const keep: Record<string, string> = Array.isArray(def.criteria) ? {} : def.criteria || {};
          def.criteria = Object.fromEntries(String(opts || '').split(',').map(x => x.trim()).filter(Boolean).map((k): [string, string] => [k, keep[k] || '']));
        } else if (def.type === 'score') {
          // Levels are not edited here: a score keeps its own, or starts with three.
          if (!Array.isArray(def.criteria) || def.criteria.length < 2) def.criteria = ['Low', 'Medium', 'High'];
        } else if (!def.criteria || Array.isArray(def.criteria) || Object.keys(def.criteria).some(k => k !== 'true' && k !== 'false')) delete def.criteria;
        return [id.trim(), def];
      }));
      delete step.set;
    }
    // A rule's own next is what runs, so a stop it carries follows it (the hub refuses the two disagreeing); a stop it
    // never had stays off the rule.
    if (step.type === 'filter') for (const r of step.rules || []) if ('next' in r && 'stop' in r) r.stop = r.next === 'end';
    const steps = auClone(a.steps);
    if (ed.insert) auContainer(steps, ed.insert.key)?.arr.splice(ed.insert.index, 0, step);
    else { const hit = auFind(steps, ed.id); if (hit) hit.arr[hit.i] = step; }
    body = auFlowBody(steps);
  }
  const out = await auWrite(body, x => `Saved · v${x.version}`);
  if (out && AU?.ed === ed) { AU.ed = null; auDrawPane(); auDrawSide(); }
  else if (button?.isConnected) button.disabled = false;
}
async function auStepDelete(id: string | undefined): Promise<void> {
  const steps = auClone(AU.cur?.steps), hit = auFind(steps, id);
  if (!hit) return;
  hit.arr.splice(hit.i, 1);
  const ed = AU.ed;
  const out = await auWrite(auFlowBody(steps), x => `Step deleted · v${x.version}`);
  if (out && AU?.ed === ed) { AU.ed = null; auDrawPane(); auDrawSide(); }
}
async function auSpendSave(v: number | null): Promise<void> {
  if (v != null && !(v >= 0)) { toast('Enter an amount in dollars', true); return; }
  const ed = AU.ed;
  const out = await auWrite({spend_cap: v == null ? null : {per_30m: Math.round(v * 100) / 100}}, x => 'Spend limit · ' + auSpendWords(x));
  if (out && AU?.ed === ed) { AU.ed = null; auDrawSide(); }
}

// Versions: newest first; any but the current one restores as a new version. Proposals (pending) come first.
async function auVersionsLoad(ed: AuEditor): Promise<void> {
  try {
    const r = await get<import('../types/api').AutomationVersions>(auApi(AU.cur?.id ?? '') + '/versions');
    const all = (r.versions || []).sort((x, y) => y.n - x.n);
    ed.rows = all.filter(v => v.status !== 'pending');
    ed.props = all.filter(v => v.status === 'pending').map((v): AuEdProp => ({
      ...v,
      // The server's flow for a version, in the shape the editors read (trigger, steps, destinations). The API types it as a
      // plain record and nothing validates it on the way in, so this is the one place the page takes that shape as given.
      flow: v.flow as AuFlowData,
      diff: false,
    }));
  } catch (e) { ed.error = errorMessage(e, ''); }
  if (AU?.ed === ed) auDrawSide();
}
function auVersionsHTML(ed: AuEditor): string {
  if (ed.error) return `<p class="err">${esc(ed.error)}</p>`;
  if (!ed.rows) return '<p class="muted">Loading…</p>';
  return `${auPropsHTML(ed)}<div class="au-vers">${ed.rows.map(v => `<div class="au-vr"><strong>v${esc(v.n)}</strong>
    <span class="muted" title="${esc(fmt(v.created_at))}">${esc(auActorName(v.actor))} · ${esc(auRunTime(v.created_at))}</span><span class="au-sp"></span>
    ${v.n === AU.cur?.version ? '<span class="pill ok">Current</span>' : AU.cur?.can_edit ? `<button class="ghost" type="button" data-op="restore" data-n="${esc(v.n)}">Restore</button>` : ''}</div>`).join('')}</div>`;
}
async function auRestore(n: number): Promise<AuAutomation | null> {
  const a = AU.cur, ed = AU.ed, state = AU;
  try {
    const out = auNormal(await post(`${auApi(a?.id ?? '')}/versions/${n}/restore`, {}));
    if (AU !== state) return null;
    auAdopt(out);
    toast(`Restored v${n} · now v${out.version}`);
    if (AU.ed === ed && ed?.kind === 'versions') { ed.rows = null; auDrawSide(); void auVersionsLoad(ed); }
    return out;
  } catch (e) { toast(errorMessage(e, ''), true); return null; }
}

// A bot's proposal is a pending version, listed above the versions: View diff, Turn on restores it (POST
// .../versions/{n}/restore, keeping its number), Discard deletes it; either way Versions is read again.
function auPropsHTML(ed: AuEditor): string {
  const a = AU.cur;
  return (ed.props || []).map(p => {
    const lines = auDiff(a, p.flow || {});
    const summary = p.summary || lines.slice(0, 2).join(' · ') || 'No changes';
    return `<div class="au-prop" data-n="${esc(p.n)}">
      <div class="au-prop-t"><strong>v${esc(p.n)}</strong><span class="muted">${esc(auActorName(p.actor))}</span><span class="pill">Proposed</span></div>
      <div class="au-prop-x">${esc(summary)}</div>
      ${p.diff ? `<pre class="au-diff">${lines.map(l => `<span class="${l[0] === '+' ? 'add' : 'del'}">${esc(l)}</span>`).join('\n') || 'No changes'}</pre>` : ''}
      <div class="au-prop-b"><button class="ghost" type="button" data-op="diff" data-n="${esc(p.n)}" aria-expanded="${!!p.diff}">View diff</button>
        ${a?.can_edit ? `<button class="primary" type="button" data-op="prop-on" data-n="${esc(p.n)}">Turn on</button><button class="linkish" type="button" data-op="discard" data-n="${esc(p.n)}">Discard</button>` : ''}</div>
    </div>`;
  }).join('');
}
// A proposal settled: the list row stops counting it as pending.
function auPendingDone(id: string): void {
  const row = (AU.list || []).find(x => x.id === id);
  if (row?.pending) row.pending -= 1;
}
async function auPropAct(op: string, n: number, button: HTMLButtonElement | null): Promise<void> {
  const ed = AU.ed, a = AU.cur, state = AU, p = (ed?.props || []).find(x => x.n === n);
  if (!p || !a || !ed) return;
  if (op === 'diff') { p.diff = !p.diff; auDrawSide(); return; }
  if (button) button.disabled = true;
  try {
    if (op === 'prop-on') {
      const out = auNormal(await post(`${auApi(a.id)}/versions/${n}/restore`, {}));
      if (AU !== state) return;
      auPendingDone(a.id);
      auAdopt(out);
      toast(`${out.name} v${out.version} is on`);
    } else {
      await writeRequest('DELETE', `${auApi(a.id)}/versions/${n}`, undefined);
      if (AU !== state) return;
      auPendingDone(a.id); auDrawList(); auDrawHead();
      toast(`v${n} discarded`);
    }
    if (AU.ed === ed) { ed.rows = null; auDrawSide(); void auVersionsLoad(ed); }
  } catch (e) { toast(errorMessage(e, ''), true); if (button?.isConnected) button.disabled = false; }
}

// A flow as lines, for the diff: the trigger, then each step, paths indented under their fork.
// A chain's actions with their arguments, so a changed owner, label or text shows in a diff.
const auThenFull = (then: AuThen[] | null | undefined): string[] => (then || []).map(x => {
  const k = auThenDo(x), args = Object.entries(argsOf(x)).filter(([, v]) => v != null && v !== '')
    .map(([n, v]) => `${n}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(', ');
  return (AU_ACTIONS[k] || AU_BOARD_ACTS[k] || k) + (args ? ` (${args})` : '');
});
function auFlowLines(flow: AuFlowData | null | undefined): string[] {
  const out: string[] = [];
  const t: AuTrigger = flow?.trigger || {};
  out.push(`trigger ${AU_TRIGGER_WORDS[t.type ?? ''] || t.type || ''} · ${auSource(t)}`.trim());
  for (const c of t.filters || []) out.push('  filter ' + auCondText(c));
  const nextWords: Record<string, string> = {rule: ' · move on', step: ' · next step'};
  const walk = (steps: AuStep[] | null | undefined, pad: string): void => {
    for (const s of steps || []) {
      const off = s.enabled === false ? ' (off)' : '';
      if (s.type === 'filter') {
        for (const r of s.rules || []) {
          const to = r.to ? ` → ${auDestLabel(r.to, flow?.destinations)}` : '', next = r.to ? '' : nextWords[auRuleNext(r)] || '';
          out.push(`${pad}filter ${auCondsText(r.when, r.match)} → ${auThenFull(r.then).join(', ')}${to}${next}${off}`);
        }
      } else if (s.type === 'decide') {
        out.push(`${pad}decide ${s.set ? auSetWords(s.set) : 'custom'} · ${s.level || 'medium'}${off}`);
        if (!s.set) for (const [q, d] of Object.entries(s.questions || {})) out.push(`${pad}  question ${q} · ${AU_QTYPES.find(([k]) => k === d.type)?.[1] ?? d.type}${typeof d.instructions === 'string' && d.instructions ? ': ' + d.instructions : ''}`);
      } else if (s.type === 'llm') {
        out.push(`${pad}llm as ${auActorName(s.as)}: ${s.prompt || ''}${off}`);
        const c = s.choose;
        if (c) out.push(`${pad}  ${c.mode || ''} → ${(c.to || []).map(id => auDestLabel(id, flow?.destinations)).join(', ')} · unsure → ${auDestLabel(c.default, flow?.destinations)}`);
      } else if (s.type === 'bot_turn') out.push(`${pad}bot turn ${auActorName(s.bot)}: ${s.prompt || ''}${off}`);
      else if (s.type === 'find') out.push(`${pad}find ${AU_FIND[s.source ?? ''] || s.source} ${s.query || ''}${off}`.trim());
      else if (s.type === 'action') out.push(`${pad}${AU_ACTIONS[s.do ?? ''] || s.do}${auActionArgs(s) ? ' ' + auActionArgs(s) : ''}${off}`);
      else if (s.type === 'paths') {
        for (const p of s.paths || []) { out.push(`${pad}path ${p.name} · ${auCondsText(p.when, p.match) || 'no condition'}`); walk(p.steps, pad + '  '); }
        out.push(`${pad}path Otherwise`); walk(s.otherwise?.steps, pad + '  ');
      }
    }
  };
  walk(flow?.steps, '');
  for (const d of flow?.destinations || []) out.push(`destination ${d.name || d.id}${d.name && d.name !== d.id ? ` (${d.id})` : ''}${d.color ? ' · ' + d.color : ''} → ${auThenFull(d.then).join(', ') || 'nothing'}`);
  return out;
}
// The changed lines between two flows (a longest-common-subsequence diff): "+ added", "- removed".
function auDiff(before: AuFlowData | null | undefined, after: AuFlowData | null | undefined): string[] {
  const a = auFlowLines(before), b = auFlowLines(after), n = a.length, m = b.length;
  const L: number[][] = Array.from({length: n + 1}, () => new Array<number>(m + 1).fill(0));
  // A cell of the table; every index is in range, the ?? 0 only satisfies the index type (noUncheckedIndexedAccess).
  const cell = (i: number, j: number): number => L[i]?.[j] ?? 0;
  for (let i = n - 1; i >= 0; i--) {
    const row = L[i];
    for (let j = m - 1; j >= 0; j--) if (row) row[j] = a[i] === b[j] ? cell(i + 1, j + 1) + 1 : Math.max(cell(i + 1, j), cell(i, j + 1));
  }
  const out: string[] = [];
  let i = 0, j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) { i++; j++; }
    else if (j < m && (i >= n || cell(i, j + 1) >= cell(i + 1, j))) out.push('+ ' + (b[j++] ?? '').trim());
    else out.push('- ' + (a[i++] ?? '').trim());
  }
  return out;
}
// Test run: the trigger's output as JSON (CONTRACT §2 Feeding input), or nothing. A sample the trigger's filters drop
// makes no run; a run opens expanded in Runs.
// A due routine's sample is the object its scheduler starts a run with (automations.py `due_input`) for its next due day.
function auSample(t: AuTrigger = {}, nextDue = ''): string {
  if (t.type === 'due') {
    const due = {due_on: nextDue || t.first_due || '', first_due: t.first_due || '', repeats: t.repeats || 'once',
      open_days: t.open_days ?? 14, ...(t.tz ? {tz: t.tz} : {}), ...(t.rule_due_id ? {rule_due_id: t.rule_due_id} : {})};
    return JSON.stringify({due}, null, 2);
  }
  const samples: Record<string, unknown> = {message: {message: {from: 'sam@acme.example', subject: '', body: ''}}, task_received: {task: {title: '', body: ''}},
    listening: {item: {title: '', source: ''}}, event: {event: {name: t.name || ''}}, webhook: {body: {}}};
  const sample = samples[t.type ?? ''];
  return sample ? JSON.stringify(sample, null, 2) : '';
}
async function auRunNow(ed: AuEditor, button: HTMLButtonElement | null): Promise<void> {
  let input: unknown;
  try { input = String(ed.text || '').trim() ? JSON.parse(ed.text ?? '') : undefined; }
  catch { toast('Sample input isn’t valid JSON', true); return; }
  const a = AU.cur, state = AU;
  if (!a) return;
  if (button) button.disabled = true;
  try {
    const out = await post(auApi(a.id) + '/run', input ? {input} : {});
    if (AU !== state) return;
    if (out.dropped) { toast('Dropped by the trigger’s filters'); if (button?.isConnected) button.disabled = false; return; }
    AU.ed = null; AU.runs = null; AU.openRun = out.run?.id || null;
    toast('Ran · ' + (out.run ? auRunOutcome(out.run) : 'queued'));
    if (AU.view === 'runs') { auDrawSide(); void auRunsLoad(AU); } else location.hash = auHref(a.id, 'runs');
  } catch (e) { toast(errorMessage(e, ''), true); if (button?.isConnected) button.disabled = false; }
}

/* The board's pop-ups (automations-flow.ts auBoardShape): a rule, a question, a destination or the LLM, one at a time
   over the board (a sheet on a phone). Each edits a copy of its part (AU.ed.item); Save reads the board from the flow
   again, puts the part back and writes the steps and destinations as one new version. */
const auFreeId = (taken: string[], prefix: string): string => { let n = 1; while (taken.includes(prefix + n)) n++; return prefix + n; };
// A new destination's id, from its name: lower case, letters first, unique on the board.
function auDestId(name: unknown, dests: AuDest[]): string {
  let base = String(name || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 32);
  if (!/^[a-z]/.test(base)) base = 'dest' + (base ? '_' + base : '');
  const taken = dests.map(d => d.id);
  if (!taken.includes(base)) return base;
  let n = 2; while (taken.includes(`${base}_${n}`)) n++;
  return `${base}_${n}`;
}
function auBoardOpen(key: string | undefined): void {
  const a = AU.cur, b = a ? auBoardShape(a) : null;
  if (!a || !b) return;
  const [part = '', at] = (key ?? '').split(':'), i = at === 'new' ? -1 : Number(at);
  if (i < 0 && !a.can_edit) return;
  const field = (AU_TRIGGER_FIELDS[a.trigger?.type ?? ''] || [])[0] || '';
  const makers: Record<string, () => AuEdItem | undefined> = {
    rule: () => i < 0 ? {when: [{field, op: 'contains', value: ''}], match: 'all', then: [], next: 'end'} : b.rules[i],
    q: () => i < 0 ? {id: auFreeId(b.questions.map(q => q.id), 'q'), text: '', min: AU_Q_MIN, then: [], to: ''} : b.questions[i],
    dest: () => i < 0 ? {id: '', name: '', color: AU_DEST_COLORS[b.dests.length % AU_DEST_COLORS.length] ?? '', then: []} : b.dests[i],
    llm: () => b.llm,
  };
  let item = makers[part ?? '']?.();
  if (!item) return;
  item = auClone(item);
  if (item.then) item.then = item.then.map((x): AuAct => typeof x === 'string' ? {do: x, args: {}} : {...x, args: x.args || {}});
  for (const x of item.then || []) if (typeof x === 'object' && x.args) AU_ARGS_OPENED.add(x.args);
  auEdOpen({kind: 'board', part, i, item});
}
// The actions a chain offers: mail actions first on a message flow; off mail, none of them (there is no message).
function auBoardActs(): Array<[string, string]> {
  const all = Object.entries(AU_BOARD_ACTS);
  return AU.cur?.trigger?.type === 'message' ? all : all.filter(([k]) => !AU_MAIL_ACTS.includes(k));
}
const auBoardNewAct = (): AuAct => AU?.cur?.trigger?.type === 'message' ? {do: 'archive', args: {}} : {do: 'create_task', args: {title: ''}};
const auBoardTa = (b: string, v: unknown, ph: string): string => `<textarea data-b="${b}" rows="3" spellcheck="true" placeholder="${esc(ph)}" aria-label="${esc(ph)}"${auDis()}>${esc(v ?? '')}</textarea>`;
// An action's arguments, under it: what the hub reads for each (automation_flow.ACTION_ARGS).
function auArgRows(at: string, k: string, a: Record<string, unknown>): string {
  const who = [...auPeople(), ...auBots()];
  const fields: Record<string, () => string> = {
    create_task: () => auIn(`${at}.title`, a.title, 'Title', 'aria-label="Task title"')
      + auSel(`${at}.owner`, [['', 'Owner'], ...auWithCur(who, a.owner, auActorName(a.owner))], a.owner || '', 'data-blank aria-label="Assign to"'),
    assign: () => auSel(`${at}.owner`, auWithCur(who, a.owner, auActorName(a.owner)), a.owner || '', 'aria-label="Assign to"'),
    wake_bot: () => auSel(`${at}.bot`, [['', 'Owner’s bot'], ...auWithCur(auBots(), a.bot, auActorName(a.bot))], a.bot || '', 'data-blank aria-label="Bot"')
      + auIn(`${at}.text`, auArg('wake_bot', a), 'Message', 'aria-label="Message"'),
    forward: () => auIn(`${at}.to`, auArg('forward', a), 'name@acme.example', 'aria-label="Forward to"'),
    label: () => auIn(`${at}.name`, auArg('label', a), 'Label', 'aria-label="Label"'),
    draft_reply: () => auBoardTa(`${at}.body`, auArg('draft_reply', a), 'Reply'),
    send_reply: () => auBoardTa(`${at}.body`, auArg('send_reply', a), 'Reply'),
    note: () => auBoardTa(`${at}.text`, auArg('note', a), 'Note'),
    post_update: () => auBoardTa(`${at}.text`, auArg('post_update', a), 'Update'),
    call_webhook: () => auIn(`${at}.webhook`, auArg('call_webhook', a), 'Webhook id', 'aria-label="Webhook"'),
  };
  const f = fields[k];
  return f ? `<div class="au-act-a">${f()}</div>` : '';
}
// An action as the editor reads it: a bare name is the action with no arguments (an open action is always an object).
const auActOf = (x: AuThen): AuAct => typeof x === 'string' ? {do: x, args: {}} : x;
function auActRows(at: string, then: AuThen[] | null | undefined): string {
  const acts = auBoardActs();
  return (then || []).map((x, j) => {
    const k = auActKey(x);
    return `<div class="au-act"><span class="au-actn">${j + 1}</span><span class="nav-icon" aria-hidden="true">${AU_ACTION_ICONS[k] || 'bolt'}</span>
      <select data-act="${at}.${j}" aria-label="Action ${j + 1}"${auDis()}>${auOpt(auWithCur(acts, k, AU_ACTIONS[k] || k), k)}</select>${auX(at, j, 'Remove action')}
      ${auArgRows(`${at}.${j}.args`, k, argsOf(x))}</div>`;
  }).join('');
}
// The chain's last item: End, Move on (rules only) or go to a destination.
function auEndRow(it: AuEdItem, n: number, move: boolean, dests: AuDest[]): string {
  const v = it.to ? 'to:' + it.to : move && it.next === 'rule' ? 'rule' : 'end', d = dests.find(x => x.id === it.to);
  const go = dests.length ? `<optgroup label="Go to">${auOpt(dests.map((x): [string, string] => ['to:' + x.id, x.name || x.id]), v)}</optgroup>` : '';
  const endOpts: Array<[string, string]> = move ? [['end', 'End'], ['rule', 'Move on']] : [['end', 'End']];
  return `<div class="au-act au-act-end"><span class="au-actn">${n}</span><span class="nav-icon" aria-hidden="true">${d ? auDestIcon(d) : v === 'rule' ? 'south' : 'block'}</span>
    <select data-end aria-label="Then"${auDis()}>${auOpt(endOpts, v)}${go}</select></div>`;
}
function auBoardEdHTML(ed: AuEditor): string {
  const it = ed.item;
  if (!it) return '';
  const fresh = Number(ed.i) < 0, cur = AU.cur, dests = auBoardShape(cur)?.dests || [];
  const chain = (move: boolean, end = true): string => auBox('Then, in order', auActRows('item.then', it.then)
    + (end ? auEndRow(it, (it.then || []).length + 1, move, dests) : '') + auPlus('item.then', 'bact', 'Action'));
  let title = '', body = '';
  if (ed.part === 'rule') {
    title = fresh ? 'New rule' : `Rule ${Number(ed.i) + 1}`;
    body = auBox('When', ((it.when || []).length > 1 ? auSel('item.match', [['all', 'All of'], ['any', 'Any of']], it.match || 'all', 'aria-label="Match"') : '')
      + auCondRows('item.when', it.when, false) + auPlus('item.when', 'cond', 'Condition')) + chain(true);
  } else if (ed.part === 'q') {
    title = fresh ? 'New question' : `Question ${Number(ed.i) + 1}`;
    body = auFld('Question', auBoardTa('item.text', it.text, 'Question'))
      + auFld('Min confidence', `<span class="au-pct"><input type="number" data-b="item.min" data-pct min="0" max="100" step="5" value="${it.min == null || it.min === '' ? '' : Math.round(Number(it.min) * 100)}" placeholder="${Math.round(AU_NOUL_MIN * 100)}" aria-label="Min confidence, percent"${auDis()}></span>`)
      + chain(false);
  } else if (ed.part === 'dest') {
    title = fresh ? 'New destination' : it.name || it.id || '';
    body = auFld('Name', auIn('item.name', it.name, 'Name'))
      + auBox('Color', `<div class="au-swatches" role="radiogroup" aria-label="Color">${AU_DEST_COLORS.map(c =>
        `<label style="--c:var(--au-${c})"><input type="radio" name="au-dc" data-b="item.color" value="${c}" aria-label="${c}"${(it.color || '') === c ? ' checked' : ''}${auDis()}></label>`).join('')}</div>`)
      + chain(false, false);
  } else if (ed.part === 'llm') {
    title = 'LLM';
    const modes = cur ? auBoardModes(cur) : [];
    const to = Array.isArray(it.to) ? it.to : [];
    body = (modes.length < 2 ? '' : auBox('Mode', `<span class="au-bseg" role="group" aria-label="Mode">${modes.map(([k, w]) =>
        `<button type="button" data-op="bmode" data-v="${k}" aria-pressed="${it.mode === k}"${auDis()}>${esc(w)}</button>`).join('')}</span>`))
      // Sort with one destination asks nothing (everything goes there), so it has no instructions to keep.
      + (it.mode === 'sort' && to.length < 2 ? '' : auFld('Instructions', auTa('item.prompt', it.prompt, 6)))
      + auBox('Destinations', `<div class="au-bto">${dests.map(d => `<label class="au-chk"><input type="checkbox" data-bto="${esc(d.id)}"${to.includes(d.id) ? ' checked' : ''}${auDis()}>${esc(d.name || d.id)}</label>`).join('')}</div>`)
      + (to.length < 2 ? '' : auFld('Unsure', auSel('item.default', [...new Set([...to, it.default])]
        .filter((x): x is string => Boolean(x)).map((id): [string, string] => [id, auDestName(id, dests)]), it.default)));
  }
  return auFrame(title, body, {del: !fresh && ed.part !== 'llm' && !(ed.part === 'dest' && dests.length < 2), delOp: 'bdel', delLabel: 'Delete'});
}
// The board's own fields: an action picked (Star is a STARRED label), where a chain ends, the LLM's destinations.
function auBoardField(t: AuControl, e: Event, ed: AuEditor): boolean {
  const act = t.dataset.act, end = t.hasAttribute('data-end'), bto = t.dataset.bto;
  if (act == null && !end && bto == null) return false;
  if (e.type !== 'change') return true;
  const it = ed.item;
  if (!it) return true;
  if (act != null) auPathSet(ed, act, t.value === 'star' ? {do: 'label', args: {name: 'STARRED'}} : {do: t.value, args: t.value === 'create_task' ? {title: ''} : {}});
  else if (end) {
    if (t.value.startsWith('to:')) Object.assign(it, {to: t.value.slice(3), next: 'end'});
    else Object.assign(it, {to: '', next: t.value});
  } else if (bto != null) {
    const to = Array.isArray(it.to) ? it.to : [];
    const on = new Set(to.filter(x => x !== bto));
    if (t instanceof HTMLInputElement && t.checked) on.add(bto);
    it.to = (auBoardShape(AU.cur)?.dests || []).map(d => d.id).filter(id => on.has(id));
    if (!it.to.includes(it.default ?? '')) it.default = it.to[0] ?? '';
  }
  auDrawSide();
  return true;
}
// Arguments as the hub reads them: a field the editor set and left blank says nothing. An argument nobody touched
// stays as it was, empty or not (a webhook's data: [] is a payload); a new action's are all the editor's.
const AU_ARGS_OPENED = new WeakSet<object>(), AU_ARGS_SET = new WeakMap<object, Set<string>>();
function auArgsMark(args: unknown, key: string): void {
  if (!args || typeof args !== 'object') return;
  const set = AU_ARGS_SET.get(args) || new Set<string>();
  AU_ARGS_SET.set(args, set.add(key));
}
const auArgsClean = (a: Record<string, unknown> | null | undefined): Record<string, unknown> => {
  const kept = !!a && AU_ARGS_OPENED.has(a), set = a ? AU_ARGS_SET.get(a) : undefined;
  return Object.fromEntries(Object.entries(a || {}).filter(([k, v]) => (kept && !set?.has(k)) || (v != null && String(v).trim() !== '')));
};
async function auBoardEdSave(ed: AuEditor, button: HTMLButtonElement | null): Promise<void> {
  const a = AU.cur, b = a ? auBoardShape(a) : null, item = ed.item;
  if (!a || !b || !item) return;
  const it = auClone(item), mail = a.trigger?.type === 'message';
  // The board's list the edited copy goes into: the new one at the end, or the one it was opened from.
  const put = (list: unknown[], x: unknown): void => { const i = Number(ed.i); if (i < 0) list.push(x); else list[i] = x; };
  // Mail actions queue on the message and the rest run at once, so a chain lists them first.
  const isMail = (x: AuThen): boolean => AU_MAIL_ACTS.includes(auActKey(x));
  if (it.then) {
    it.then = (item.then || []).map((x): AuAct => { const act = auActOf(x); return {do: act.do, args: auArgsClean(act.args)}; });   // the copy's own args, which the marks know
    if (mail) it.then = [...it.then.filter(isMail), ...it.then.filter(x => !isMail(x))];
  }
  let problem = '';
  if (ed.part === 'rule') {
    if (!(it.when || []).every(c => String(c.field || '').trim())) problem = 'Each condition needs a field';
    put(b.rules, it);
  } else if (ed.part === 'q') {
    it.text = String(it.text || '').trim();
    if (!it.text) problem = 'Add the question';
    put(b.questions, it);
  } else if (ed.part === 'dest') {
    it.name = String(it.name || '').trim();
    if (!it.name) problem = 'Name the destination';
    else if (Number(ed.i) < 0) { const id = auDestId(it.name, b.dests); b.dests.push({...it, id}); b.llm.to.push(id); }
    else b.dests[Number(ed.i)] = {...it, id: it.id ?? ''};
  } else if (ed.part === 'llm') {
    const to = Array.isArray(it.to) ? it.to : [];
    if (!to.length) problem = 'Pick a destination';
    b.llm = {...b.llm, ...it, to, prompt: String(it.prompt || '').trim()};
    if (b.llm.mode !== 'sort') b.llm.as ||= auOwnerAs(a);
  }
  if (problem) { toast(problem, true); return; }
  if (button) button.disabled = true;
  const out = await auWrite(auBoardFlow(b), x => `Saved · v${x.version}`);
  if (out && AU?.ed === ed) { AU.ed = null; auDrawPane(); auDrawSide(); }
  else if (button?.isConnected) button.disabled = false;
}
// Delete a rule, a question or a destination; whatever went to that destination ends instead.
async function auBoardDelete(ed: AuEditor, button: HTMLButtonElement | null): Promise<void> {
  const b = auBoardShape(AU.cur), i = Number(ed.i);
  if (!b || i < 0) return;
  if (ed.part === 'rule') b.rules.splice(i, 1);
  else if (ed.part === 'q') b.questions.splice(i, 1);
  else if (ed.part === 'dest') {
    const d = b.dests[i];
    if (!d || b.dests.length < 2 || !confirm(`Delete ${d.name || d.id}?`)) return;
    b.dests.splice(i, 1);
    for (const r of [...b.rules, ...b.questions]) if (r.to === d.id) Object.assign(r, {to: '', next: 'end'});
    b.llm.to = b.llm.to.filter(x => x !== d.id);
    if (!b.llm.to.length) b.llm.to = [b.dests[0]?.id ?? ''];
    if (!b.llm.to.includes(b.llm.default)) b.llm.default = b.llm.to[0] ?? '';
  } else return;
  if (button) button.disabled = true;
  const out = await auWrite(auBoardFlow(b), x => `Deleted · v${x.version}`);
  if (out && AU?.ed === ed) { AU.ed = null; auDrawPane(); auDrawSide(); }
  else if (button?.isConnected) button.disabled = false;
}
// The LLM card's mode switch saves at once, as a step's own switch does.
async function auBoardMode(mode: string | undefined): Promise<void> {
  const a = AU.cur;
  if (!a || !a.can_edit || mode === undefined) return;
  const b = auBoardShape(a);
  if (!b || b.llm.mode === mode) return;
  b.llm.mode = mode;
  if (mode !== 'sort') b.llm.as ||= auOwnerAs(a);
  await auWrite(auBoardFlow(b), x => `Saved · v${x.version}`);
}
