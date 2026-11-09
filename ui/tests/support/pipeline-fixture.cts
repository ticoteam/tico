// The pipeline views' fake server for the browser tests: one Dev tickets pipeline with requirements on its cards, and the
// review queue, approve, undo, send-back, message-manager and mark-met routes the UI calls (ui/app/pipeline-api.ts).
import type {Page} from 'playwright';
const fs: typeof import('node:fs') = require('node:fs');
const {html, uiFile} = require('./page.cts') as import('./page.cts').Api;
const {unrouted} = require('./routes.cts') as import('./routes.cts').Api;

type Json = Record<string, unknown>;

export interface PipelineInstallOptions {
  refuseApprove?: boolean;
  manager?: boolean;
  refuseMove?: boolean;
  late?: Json[];
  ownerOnly?: boolean;
  member?: boolean;
  claimedBy?: string | null;
  noRequirements?: boolean;
  validate?: boolean;
  bare?: boolean;
  text?: boolean;
  held?: boolean;
  plain?: boolean;
  twoCards?: boolean;
  unopenable?: boolean;
  // `spec`: D52's card has its Spec page attached (the spec the review shows first); `product`: the cards are the Product
  // pipeline's Review stage, so the Why line names Product.
  spec?: boolean;
  product?: boolean;
}

interface Step extends Json {
  id: string;
  name: string;
  status: string;
  position: number;
  type_id?: string;
  limit_minutes?: number | null;
}

// A pipeline type as the task-types list sends it (the Dev tickets type below).
export interface PipelineType extends Json {
  id: string;
  name: string;
  role: string;
  manager: string | null;
  can_edit_rules: boolean;
  has_requirements: boolean;
  steps: Step[];
}

// A ticket as the fake hub holds it: its stage (a step) and its requirements, each with a state and who acts next.
interface Requirement extends Json {
  id: string;
  name: string;
  state: string;
  next_actor?: string | null;
  detail?: string;
  version?: number;
  decision?: Json | null;
  can_mark_met?: boolean;
}

interface Task extends Json {
  id: string;
  step_id?: string;
  step?: Step;
  requirements?: Requirement[];
}

// What the fake pipeline server recorded. Lists grow as the page reads and writes.
export interface PipelineState {
  posts: Json[];
  queueReads: number;
  refuseApprove: boolean;
  refuseMove: boolean;
  moves: Json[];
  prefs: Record<string, unknown>;
  attachments: Json[];
  managerReads: number;
  claims: string[];
  claimedBy: string | null;
  removed: Set<string>;
  lastDone: string | null;
  affectedReads: string[];
  requirementReads: Record<string, number>;
  attachmentReads: number;
  validate: boolean;
  bare: boolean;
  text: boolean;
  held: boolean;
  plain: boolean;
  twoCards: boolean;
  unopenable: boolean;
  spec: boolean;
  product: boolean;
  stage: Record<string, string>;
  // The decision an approval made, with the stage its ticket came from (what an undo puts back).
  moved: Record<string, {task: string; from: string | null}>;
  // Set by the rail's late reads (the rail test reads them); absent until the first one.
  lateReads?: number;
  lateAsks?: (string | null)[];
}

const ME = 'human:ana';
const stamp = '2026-11-07T12:00:00Z';
// The bots the queue names, as /api/employees lists them: a person reads a bot's display name, never its slug.
const EMPLOYEES = [
  {id: 'product-owner', slug: 'product-owner', display_name: 'Product Manager', status: 'active'},
  {id: 'product-manager', slug: 'product-manager', display_name: 'Product Pipeline Manager', status: 'active'},
];
const steps: Step[] = [
  {id: 'spec', name: 'Spec', status: 'open', position: 0},
  {id: 'build', name: 'Build', status: 'doing', position: 1},
  {id: 'review1', name: 'Review 1', status: 'review', position: 2},
];
// The Build stage warns after a day (the phone's "warn after 24h"); the cards' own step copies carry no limit, as the server sends them.
const type: PipelineType = {id: 'dev-tickets', name: 'Dev tickets', role: 'engineering', manager: null, can_edit_rules: true, has_requirements: true,
  steps: steps.map(s => ({...s, type_id: 'dev-tickets', limit_minutes: s.id === 'build' ? 1440 : null}))};

// Build's limit is a day: D17 has sat in Build for five hours (late by the server's clock); the other cards arrived a couple of
// hours ago. Relative to now, so the late check does not depend on the date the test runs.
const LATE_SINCE = new Date(Date.now() - 5 * 36e5).toISOString(), RECENT_SINCE = new Date(Date.now() - 2 * 36e5).toISOString();

function task(id: string, title: string, stepId: string, requirements?: Requirement[]): Task {
  const step = steps.find(s => s.id === stepId)!;
  return {id, title, body: '', owner_id: 'bot:b2', requester_id: 'human:ana', status: 'doing', lane: 'company', version: 1,
    labels: [], links: [], parts: {total: 0, done: 0}, rank: 1, created_at: stamp, updated_at: stamp, step_since: id === 'D17' ? LATE_SINCE : RECENT_SINCE,
    type_id: 'dev-tickets', type: {id: 'dev-tickets', name: 'Dev tickets'}, step_id: stepId, step: {...step, type_id: 'dev-tickets'},
    ...(requirements ? {requirements} : {})};
}

// The tickets: D52 waits on Ana for its screenshots (one dot to tap); D55 is out of date; D17 has no requirements at all.
function tasks(): Task[] {
  return [
    task('D52', 'Assistant can approve a spec', 'build', [
      // Met by Ana under version 1 of the rule; the rule is now at version 2, so the ticket says "Met under v1".
      {id: 'r-spec', name: 'Spec page', state: 'met', next_actor: null, detail: 'Approved by Ana', version: 2,
        decision: {id: 'dec-spec', judge: 'person', judge_actor: ME, verdict: 'met', requirement_version: 1, at: stamp}},
      {id: 'r-tests', name: 'Tests pass', state: 'met', next_actor: null, detail: 'Passed on 3f2a9c1',
        decision: {id: 'dec-tests', judge: 'bot', judge_actor: 'bot:b1', verdict: 'met', requirement_version: 1, at: stamp}},
      {id: 'r-shots', name: 'Screenshots', state: 'waiting', next_actor: ME, detail: '3 images', can_mark_met: true}]),
    task('D55', 'Review page: missing', 'review1', [
      {id: 'r-shots55', name: 'Screenshots', state: 'out_of_date', next_actor: 'bot:b1', detail: 'Shots out of date'},
      {id: 'r-tests55', name: 'Tests pass', state: 'met', next_actor: null}]),
    task('D61', 'Pipeline stages, requirements and the manager rules', 'spec', [
      {id: 'r-spec61', name: 'Spec page', state: 'waiting', next_actor: ME},
      // No dot is drawn for a requirement the stage does not have yet: the server sends "none", drawn hollow.
      {id: 'r-tests61', name: 'Tests pass', state: 'none', next_actor: null}]),
    task('D17', 'Sim stuck checks', 'build'),
    // D38 is the manager's own ticket: its open tasks are the rail's Tasks tab.
    {...task('D38', 'Stage rules spec', 'spec'), owner_id: 'bot:product-manager'},
  ];
}

// The tasks with the stage the fake server has moved them to (state.stage), so a list or a ticket read shows the move.
function placed(list: Task[], state: PipelineState): Task[] {
  return list.map(t => {
    // With `held`, D17 waits on Ana's question as the server sends it: a waiting card, its open question as the blocker, and
    // whom it waits on (backend/question_tasks.py waiting_on), so the board says "Waiting on Ana", not "Blocked:".
    const base: Task = state.held && t.id === 'D17'
      ? {...t, status: 'waiting', waiting_on: ME, relations: {blocks: [{id: 'Q1', title: QUESTION_TEXT, status: 'open', direction: 'in', owner_id: ME}]}}
      : t;
    const stage = state.stage[t.id];
    if (!stage) return base;
    return {...base, step_id: stage, step: {...steps.find(s => s.id === stage)!, type_id: 'dev-tickets'}};
  });
}

// The attachments a ticket's requirement shows (GET /v2/tasks/{id}/requirements): the file pinned to the version it was
// attached as, and no link. The read view (backend/requirement_attachments.py views) gives format, name and href.
const ATTACHED: Record<string, Json[]> = {
  'r-shots': [{id: 'att-shot-3', source: 'file', format: 'image', name: 'shot-2.png', href: '/api/v2/files/f9/versions/3', file_id: 'f9',
    file_version: 3, attached_by: ME, created_at: stamp}],
};

// The card each ticket shows when nothing is attached: the queue row carries its body, acceptance criteria and checkpoint.
const CARDS: Record<string, {body: string; acceptance: string[]; checkpoint: string}> = {
  D52: {body: 'Assistant can approve a spec when every check passes.', acceptance: ['Approved in one tap', 'Works on a phone'],
    checkpoint: 'Screenshots taken on the dev branch; shot 2 is cropped.'},
};

// The markdown file on D61 when `state.text` is set (a ticket file, not the requirement's): the preview shows it as text.
const SPEC_MARKDOWN = '# Pipeline manager spec\n\nThe manager asks for a spec before a card moves on.\n\n- Every stage names its reviewer\n- Markdown shows as written\n';
// A stand-in picture for the example.test links (the screenshot's shot.png), drawn as SVG so the card looks like a screen.
// D52's Spec page (`spec` option): a Tico page the card's Specifying stage holds, shown first in Review as the spec.
const ASSISTANT_SPEC_MARKDOWN = '# Assistant can approve a spec\n\nA reviewer approves a spec in one tap, once every check passes.\n\n- Approved in one tap\n- Works on a phone\n';
const SPEC_PAGE_52 = {id: 'spec-page-52', source: 'file', format: 'text', name: 'Assistant spec v2.md', href: '/api/v2/files/spec-page-52', file_id: null, file_version: null, from: 'spec_page'};
// A stand-in picture for the example.test links (the screenshot's shot.png), drawn as SVG so the card looks like a screen.
const PLACEHOLDER_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400" viewBox="0 0 640 400"><rect width="640" height="400" fill="#334155"/>'
  + '<rect x="60" y="60" width="520" height="280" rx="16" fill="#1e293b"/><circle cx="200" cy="170" r="48" fill="#38bdf8"/>'
  + '<rect x="290" y="140" width="250" height="18" rx="9" fill="#94a3b8"/><rect x="290" y="176" width="200" height="18" rx="9" fill="#64748b"/>'
  + '<rect x="100" y="260" width="440" height="24" rx="12" fill="#475569"/></svg>';

// The held forms (`held` option): a question Ana is asked on D17's card (the question task is the answer's target, as
// backend/question_tasks.py as_ask builds it) and two subtasks of Ana's on D52.
const QUESTION_TEXT = "Does 'close all' include bot-owned tasks?";
const QUESTION: Json = {id: 'Q1', kind: 'ask', question_task: true, sender_id: 'bot:product-owner', recipient_id: ME, body: QUESTION_TEXT,
  created_at: RECENT_SINCE, conversation_id: null, read_at: null, delivered_at: null,
  refs: {questions: [{id: 'decision', question: QUESTION_TEXT, options: [{label: 'Yes'}, {label: 'No'}]}], task: 'Q1', task_question: true,
    target: {task: 'Q1'}}};
// A plain question (`plain` option): no picks, so the ▶ answers it in words, as the task page does.
const PLAIN_QUESTION: Json = {...QUESTION, refs: {...(QUESTION.refs as Json), questions: [{id: 'decision', question: QUESTION_TEXT, options: []}]}};
// A subtask's card carries what the task view shows: its body, acceptance criteria and files (the server's held item).
function subtask(id: string, title: string, card: Json = {}): Json {
  return {form: 'subtask', task_id: 'D52', title: 'Assistant can approve a spec', number: 52, pipeline_id: 'dev-tickets', pipeline: 'Dev tickets',
    stage_id: 'build', stage: 'Build', subject_id: id, subject_title: title, subject_version: 2, ask: null, holds_up: null,
    body: '', acceptance_criteria: [], task_files: [], ...card,
    why: 'Your subtask · Dev tickets', since: stamp, late: false, reviewer_id: ME};
}

// `state.unopenable`: D52's page is one the reader cannot open. The server sends it redacted, with no id, blob or digest.
const UNOPENABLE: Json = {id: null, source: 'file', format: 'file', name: "A page you can't open", url: null, href: null, file_id: null,
  file_version: null, blob_id: null, commit_sha: null, removed: true};

// The review queue for the pipeline, as the server sends it (GET /v2/pipelines/{id}/review/queue): two items for Ana.
// `state.removed` holds the items an approval or send-back took out, so a reload shows the queue as it now is.
// `state.bare`: D52 has nothing attached, so its card shows the body, acceptance criteria and checkpoint instead.
// `state.text`: D61's attachment is the ticket's own markdown file (source ticket, format text), not a requirement's PDF.
// `why` is the server's code (reviewer, sent_back or backup); the review words it with the requirement and pipeline.
function queue(state: PipelineState): {items: Json[]} {
  const row = (task_id: string, title: string, stage: string, requirement_id: string, requirement: string, why: string, attachment: Json | null): Json => {
    const card = CARDS[task_id] || {body: '', acceptance: [], checkpoint: ''};
    const shown = state.bare && task_id === 'D52' ? null : state.unopenable && task_id === 'D52' ? UNOPENABLE : attachment;
    // `product`: the live Product Review step, its requirement named as the live one is ("Spec approved").
    const pipeline = state.product ? 'Product' : 'Dev tickets';
    const stageName = state.product ? 'Review' : stage;
    const requirementName = state.product ? 'Spec approved' : requirement;
    return {form: 'review', task_id, title, pipeline_id: 'dev-tickets', pipeline, stage_id: stageName.toLowerCase(), stage: stageName, requirement_id, requirement: requirementName,
      requirement_version: 1, kind: shown ? shown.format : 'approval', work_version: 3, attachment: shown,
      attachment_digest: shown && shown.source !== 'ticket' && !shown.removed ? 'sha256:aa' : null, why, since: stamp, late: false, claimed_by: null, claimed_by_me: false,
      assignee: {id: 'bot:product-owner', name: 'Product Manager'}, manager: {id: 'bot:product-manager', name: 'Product Pipeline Manager'},
      body: card.body, acceptance_criteria: card.acceptance, checkpoint: card.checkpoint,
      spec: state.spec && task_id === 'D52' ? SPEC_PAGE_52 : null,
      bot_note: task_id === 'D52' ? 'Shot 2 is cropped' : '', moves_to: task_id === 'D61' ? {stage_id: 'build', name: 'Build'}
        : state.product && task_id === 'D52' ? {stage_id: 'in-development', name: 'In development'} : null,
      reviewer_id: ME, can_mark_met: true, can_not_validate: stage === 'Validate', stage_visit_id: stage === 'Validate' ? 'v-validate' : null};
  };
  const all: Json[] = [
    row('D52', 'Assistant can approve a spec', 'Build', 'r-shots', 'Screenshots', 'reviewer',
      {id: 'a1', source: 'link', format: 'image', name: 'shot.png', href: 'https://example.test/shot.png', file_id: null, file_version: null}),
    row('D61', 'Pipeline stages, requirements and the manager rules', 'Spec', 'r-spec61', 'Spec page', 'reviewer', state.text
      ? {id: 'blob-spec', source: 'ticket', format: 'text', name: 'Pipeline manager spec v1.md', href: '/api/v2/files/blob-spec', url: null, file_id: null, file_version: null}
      : {id: 'b1', source: 'file', format: 'pdf', name: 'spec.pdf', href: '/api/v2/files/f1/versions/2', file_id: 'f1', file_version: 2}),
  ];
  // `validate`: a Validate item comes first, so a test can reach it without stepping through the others.
  if (state.validate) all.unshift(row('D70', 'Validate the spec', 'Validate', 'r-val70', 'Validation', 'reviewer',
    {id: 'c1', source: 'link', format: 'page', name: 'Measures', href: 'https://example.test/measures', file_id: null, file_version: null}));
  // `held`: the question and the two subtasks follow the reviews (the server orders the whole queue by stage, then age; the
  // fixture lists them here, after the reviews the walk starts on). Answering or finishing one removes it by its subject.
  if (state.held) {
    const ask = state.plain ? PLAIN_QUESTION : QUESTION;
    const questionRow = (task_id: string, title: string, number: number): Json => ({form: 'question', task_id, title, number, pipeline_id: 'dev-tickets', pipeline: 'Dev tickets',
      stage_id: 'build', stage: 'Build', subject_id: 'Q1', subject_title: QUESTION_TEXT, subject_version: 1, ask,
      holds_up: {id: task_id, title}, why: 'Question for you · Dev tickets', since: stamp, late: false, reviewer_id: ME});
    all.push(questionRow('D17', 'Sim stuck checks', 17));
    // `twoCards`: the same question holds a second card, so the queue has a row for each (answering takes both out).
    if (state.twoCards) all.push(questionRow('D52', 'Assistant can approve a spec', 52));
    all.push(subtask('S1', 'Check the shot crop', {body: 'Crop the second shot to the frame.', acceptance_criteria: ['Shot 2 is cropped to the frame'],
      task_files: [{id: 'blob-crop', name: 'crop-notes.txt', size: 120, content_type: 'text/plain', url: '/api/v2/files/blob-crop'}]}),
    subtask('S2', 'Write the release note'));
  }
  return {items: all.filter(r => !state.removed.has(r.form === 'review' ? `${r.task_id}:${r.requirement_id}` : String(r.subject_id)))};
}

// Installs the fake server on a page. `state` collects what the tests check: posts, refusals and queue reads. `held` adds
// the question and the subtasks to the queue (see queue()).
// `ownerOnly`: the type carries no can_edit_rules, only its owner, and the viewer is a member (E3: the rules controls follow
// the server's answer, and the owner check reads pipeline_owner_id). `claimedBy`: another reviewer holds every item.
async function install(page: Page, {refuseApprove = false, manager = false, refuseMove = false, late = [{task_id: 'D17', title: 'Sim stuck checks', stage: 'Build', hours: 52, limit_hours: 48, since: stamp}],
  ownerOnly = false, member = false, claimedBy = null, noRequirements = false, validate = false, bare = false, text = false, held = false,
  plain = false, twoCards = false, unopenable = false, spec = false, product = false}: PipelineInstallOptions = {}): Promise<PipelineState> {
  const state: PipelineState = {posts: [], queueReads: 0, refuseApprove, refuseMove, moves: [], prefs: {}, attachments: [], managerReads: 0, claims: [], claimedBy,
    removed: new Set(), lastDone: null, affectedReads: [], requirementReads: {}, attachmentReads: 0, validate, bare, text, held, plain, twoCards, unopenable, spec, product,
    // The stage each ticket was moved to by an approval, and the stage an undo puts it back in (keyed by the decision).
    stage: {}, moved: {}};
  let shown: Json = manager ? {...type, manager: 'product-manager'} : type;
  if (ownerOnly) { const {can_edit_rules, ...rest} = shown; shown = {...rest, pipeline_owner_id: ME}; }
  if (noRequirements) shown = {...shown, has_requirements: false};
  const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: member ? 'member' : 'owner', mover: true, cloud: true};
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.hostname === 'example.test') return route.fulfill({contentType: 'image/svg+xml', body: PLACEHOLDER_SVG});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    // theme.ts is a classic script the page loads as TypeScript: uiFile serves its compiled file, so the theme (data-theme) applies.
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css|ts))$/);
    if (ui) { const file = uiFile(ui[1] ?? ''); if (fs.existsSync(file)) return route.fulfill({contentType: (ui[1] ?? '').endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')}); }
    if (p === '/api/me') return json(me);
    if (p === '/api/employees') return json(EMPLOYEES);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}]});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/files/blob-spec') return route.fulfill({contentType: 'text/markdown; charset=utf-8', body: SPEC_MARKDOWN});
    if (p === '/api/v2/files/spec-page-52') return route.fulfill({contentType: 'text/markdown; charset=utf-8', body: ASSISTANT_SPEC_MARKDOWN});
    if (p === '/api/v2/task-types') return json({types: [shown]});
    const affected = p.match(/^\/api\/v2\/pipeline-stages\/([^/]+)\/affected$/);
    if (affected && req.method() === 'GET') {
      const label = url.searchParams.get('label') || '';
      state.affectedReads.push(label);
      return json({stage: 'Build', label: label || null, count: label === 'urgent' ? 1 : 3});
    }
    if (p === '/api/v2/pipelines/dev-tickets/late') {
      // The Late list the cards read (no manager needed): every late ticket of the pipeline, keyed by id.
      state.lateReads = (state.lateReads || 0) + 1;
      // Like the server: `ids` asks for those tickets only; without it the list is capped at 500 (LATE_CAP).
      const ids = url.searchParams.get('ids');
      state.lateAsks = [...(state.lateAsks || []), ids];
      const pick = ids === null ? late.slice(0, 500) : late.filter(item => ids.split(',').includes(String(item.task_id)));
      return json({late: Object.fromEntries(pick.map((item): [string, Json] => [String(item.task_id), {since: item.since, hours: item.hours, limit_hours: item.limit_hours}]))});
    }
    if (p === '/api/v2/pipelines/dev-tickets/manager') {
      state.managerReads++;
      const run = (id: string, at: string, status: string, error: string | null): Json => ({id, started_at: at, finished_at: at, status, error});
      return json({pipeline_id: 'dev-tickets', manager: 'product-manager', every_minutes: 60, missed_runs: 0, last_run: run('r1', '2026-11-07T12:10:00Z', 'ok', null),
        over_limit: late,
        log: [run('r1', '2026-11-07T12:10:00Z', 'ok', null), run('r2', '2026-11-07T11:10:00Z', 'failed', 'The model was unreachable'),
          {id: 'a1', task_id: 'D52', action: 'ask', target_actor: ME, note: 'Screenshots are waiting on you.', created_at: '2026-11-07T12:10:00Z'}]});
    }
    const pref = p.match(/^\/api\/v2\/preferences\/([a-z0-9_.-]+)$/);
    if (pref) {
      const key = pref[1] ?? '';
      if (req.method() === 'POST') { const posted: Json = req.postDataJSON() || {}; state.prefs[key] = posted.value; return json({ok: true}); }
      return json(key in state.prefs ? {value: state.prefs[key]} : {});
    }
    const reqs = p.match(/^\/api\/v2\/tasks\/([^/]+)\/requirements$/);
    if (reqs && req.method() === 'GET') {
      // One request answers the ticket: each requirement carries its attachments (the server bounds them per requirement).
      const taskId = reqs[1] ?? '';
      state.requirementReads[taskId] = (state.requirementReads[taskId] || 0) + 1;
      const found = tasks().find(x => x.id === taskId);
      return json({requirements: (found?.requirements || []).map(r => ({id: r.id, name: r.name, kind: r.id === 'r-shots' ? 'attachment' : 'run', stage_id: found?.step_id,
        stage: found?.step?.name, state: r.state, why: r.detail || null, reviewed_by: 'person', reviewer_id: r.next_actor, version: r.version ?? 1,
        decision: r.decision ?? null, attachment: null, attachments: ATTACHED[r.id] ?? []}))});
    }
    const atts = p.match(/^\/api\/v2\/tasks\/([^/]+)\/requirements\/([^/]+)\/attachments$/);
    if (atts) {
      state.attachmentReads++;
      if (req.method() === 'POST') { const body: Json = req.postDataJSON() || {}; state.attachments.push(body); state.posts.push({path: 'attach', body});
        return json({attachment: {id: 'att-' + state.attachments.length, source: body.source, url: body.url || null, file_id: body.file_id || null, by: ME, at: stamp, work_version: 1}}); }
      return json({attachments: state.attachments.map((a, i) => ({id: 'att-' + (i + 1), source: a.source, url: a.url || null, file_id: a.file_id || null, by: ME, at: stamp, work_version: 1}))});
    }
    // A question's answer (the question card's call) closes it; a subtask's Done or Send back is a status POST on that task
    // (decide.ts). Either one takes the item out of the queue, as the server does.
    const answers = p.match(/^\/api\/v2\/tasks\/([^/]+)\/answers$/);
    if (answers && req.method() === 'POST') {
      state.posts.push({path: `/v2/tasks/${answers[1]}/answers`, body: req.postDataJSON() || {}});
      state.removed.add(answers[1] ?? '');
      return json({ok: true});
    }
    const mv = p.match(/^\/api\/v2\/tasks\/([^/]+)$/);
    if (mv && req.method() === 'POST') {
      const taskId = mv[1] ?? '';
      const body: Json = req.postDataJSON() || {};
      if (body.status && !body.step) {
        state.posts.push({path: `/v2/tasks/${taskId}`, body});
        state.removed.add(taskId);
        return json({task: {id: taskId, status: body.status, version: ((body.version as number | undefined) || 1) + 1}});
      }
      state.moves.push(body);
      if (state.refuseMove && !body.override_requirements) return json({error: {code: 'requirement', detail: 'Screenshots is not met'}}, 422);
      return json({task: {...tasks().find(t => t.id === taskId), ...(body.step ? {step_id: body.step, step: {...steps.find(x => x.id === body.step), type_id: 'dev-tickets'}} : {}), version: 2}});
    }
    if (p === '/api/v2/tasks' && req.method() === 'GET') return json({tasks: placed(tasks(), state), next_offset: null});
    const one = p.match(/^\/api\/v2\/tasks\/([^/]+)$/);
    if (one && req.method() === 'GET') {
      const found = placed(tasks(), state).find(t => t.id === one[1]);
      return json({task: found, children: [], parent: null, comments: [], events: [], messages: [], mover: true});
    }
    const pipe = p.match(/^\/api\/v2\/pipelines\/([^/]+)\/review\/queue$/);
    if (pipe) { state.queueReads++; return json(queue(state)); }
    // The 10-minute claim on the item on show: another reviewer's claim answers 409 with who holds it.
    const claim = p.match(/^\/api\/v2\/pipelines\/(tasks\/[^/]+\/requirements\/[^/]+)\/claim$/);
    if (claim && req.method() === 'POST') {
      if (state.claimedBy) return json({error: {code: 'claimed', detail: 'Bob is reviewing', claimed_by: state.claimedBy, expires_at: stamp}}, 409);
      state.claims.push(claim[1] ?? '');
      return json({expires_at: '2026-11-07T12:10:00Z'});
    }
    const action = p.match(/^\/api\/v2\/pipelines\/(.+)$/);
    if (action && req.method() === 'POST') {
      const body: Json = req.postDataJSON() || {}, path = action[1] ?? '';
      state.posts.push({path, body});
      if (path.endsWith('/approve')) {
        if (state.refuseApprove) { state.refuseApprove = false; return json({error: {code: 'stale', detail: 'changed'}}, 409); }
        const approve = path.match(/^tasks\/([^/]+)\/requirements\/([^/]+)\/approve$/)!;
        const taskId = approve[1] ?? '', reqId = approve[2] ?? '';
        state.lastDone = `${taskId}:${reqId}`;
        state.removed.add(state.lastDone);
        // The approval is the ticket's last requirement when every other one is met or not on this stage: the ticket then
        // moves to the next stage, and the decision remembers where it was so Undo can put it back.
        const found = tasks().find(t => t.id === taskId);
        const last = (found?.requirements || []).every(r => r.id === reqId || r.state === 'met' || r.state === 'none');
        if (last) {
          const at = state.stage[taskId] || found?.step_id || '';
          const next = steps[steps.findIndex(x => x.id === at) + 1];
          state.moved['dec-1'] = {task: taskId, from: state.stage[taskId] ?? null};
          if (next) state.stage[taskId] = next.id;
        }
        return json({decision_id: 'dec-1', advanced: last});
      }
      if (path.startsWith('decisions/') || path.endsWith('/undo')) {
        if (state.lastDone) state.removed.delete(state.lastDone);
        const moved = state.moved['dec-1'];
        if (moved) {
          if (moved.from) state.stage[moved.task] = moved.from; else delete state.stage[moved.task];
          delete state.moved['dec-1'];
        }
        return json({undone: true});
      }
      if (path.endsWith('/send-back') || path.endsWith('/message-manager')) {
        if (path.endsWith('/send-back')) state.removed.add(path.replace(/^tasks\/([^/]+)\/requirements\/([^/]+)\/send-back$/, '$1:$2'));
        return json({ok: true, decision_id: 'dec-2'});
      }
      if (path.endsWith('/mark-met')) return json({decision_id: 'dec-3'});
      if (path.endsWith('/not-validated')) {
        // The server moves the item back and files one follow-up (D71): the item leaves the queue.
        state.removed.add(path.replace(/^tasks\/([^/]+)\/requirements\/([^/]+)\/not-validated$/, '$1:$2'));
        return json({decision_id: 'dec-4', verdict: 'failed', moved_back_to: 'In development', follow_up: 'D71'});
      }
    }
    return json(unrouted(p));
  });
  return state;
}

const api = {install, type, tasks};
export type Api = typeof api;
module.exports = api;
