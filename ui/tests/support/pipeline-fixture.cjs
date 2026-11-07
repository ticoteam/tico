// The pipeline views' fake server for the browser tests: one Dev tickets pipeline with requirements on its cards, and the
// review queue, approve, undo, send-back, message-manager and mark-met routes the UI calls (ui/app/pipeline-api.ts).
const fs = require('node:fs');
const {html, uiFile} = require('./page.cjs');
const {unrouted} = require('./routes.cjs');

const ME = 'human:ana';
const stamp = '2026-11-07T12:00:00Z';
const steps = [
  {id: 'spec', name: 'Spec', status: 'open', position: 0},
  {id: 'build', name: 'Build', status: 'doing', position: 1},
  {id: 'review1', name: 'Review 1', status: 'review', position: 2},
];
const type = {id: 'dev-tickets', name: 'Dev tickets', role: 'engineering', manager: null, can_edit_rules: true, steps: steps.map(s => ({...s, type_id: 'dev-tickets'}))};

function task(id, title, stepId, requirements) {
  const step = steps.find(s => s.id === stepId);
  return {id, title, body: '', owner_id: 'bot:b2', requester_id: 'human:ana', status: 'doing', lane: 'company', version: 1,
    labels: [], links: [], parts: {total: 0, done: 0}, rank: 1, created_at: stamp, updated_at: stamp, step_since: '2026-11-04T12:00:00Z',
    type_id: 'dev-tickets', type: {id: 'dev-tickets', name: 'Dev tickets'}, step_id: stepId, step: {...step, type_id: 'dev-tickets'},
    ...(requirements ? {requirements} : {})};
}

// The tickets: D52 waits on Ana for its screenshots (one dot to tap); D55 is out of date; D17 has no requirements at all.
function tasks() {
  return [
    task('D52', 'Assistant can approve a spec', 'build', [
      {id: 'r-spec', name: 'Spec page', state: 'met', next_actor: null, detail: 'Approved by Chris'},
      {id: 'r-tests', name: 'Tests pass', state: 'met', next_actor: null, detail: 'Passed on 3f2a9c1'},
      {id: 'r-shots', name: 'Screenshots', state: 'waiting', next_actor: ME, detail: '3 images', can_mark_met: true}]),
    task('D55', 'Review page: missing', 'review1', [
      {id: 'r-shots55', name: 'Screenshots', state: 'out_of_date', next_actor: 'bot:b1', detail: 'Shots out of date'},
      {id: 'r-tests55', name: 'Tests pass', state: 'met', next_actor: null}]),
    task('D61', 'Pipeline stages + manager', 'spec', [
      {id: 'r-spec61', name: 'Spec page', state: 'waiting', next_actor: ME}]),
    task('D17', 'Sim stuck checks', 'build'),
  ];
}

// The review queue for the pipeline, as the server sends it (GET /v2/pipelines/{id}/review/queue): two items for Ana.
function queue() {
  const row = (task_id, title, stage, requirement_id, requirement, why, attachment) => ({task_id, title, pipeline_id: 'dev-tickets',
    pipeline: 'Dev tickets', stage_id: stage.toLowerCase(), stage, requirement_id, requirement, requirement_version: 1, kind: 'attachment',
    work_version: 3, attachment, attachment_digest: attachment ? 'sha256:aa' : null, why, since: stamp, late: false, claimed_by: null, claimed_by_me: false,
    assignee: {id: 'bot:b1', name: 'Builder'}, bot_note: task_id === 'D52' ? 'Shot 2 is cropped' : '', moves_to: task_id === 'D61' ? {stage_id: 'build', name: 'Build'} : null,
    reviewer_id: ME, can_mark_met: true});
  return {items: [
    row('D52', 'Assistant can approve a spec', 'Build', 'r-shots', 'Screenshots', 'The bot is unsure about shot 2',
      {id: 'a1', source: 'link', url: 'https://example.test/shot.png'}),
    row('D61', 'Pipeline stages + manager', 'Spec', 'r-spec61', 'Spec page', 'Spec needs your sign-off', {id: 'b1', source: 'file', url: null, file_id: 'f1'}),
  ]};
}

// Installs the fake server on a page. `state` collects what the tests check: posts, refusals and queue reads.
async function install(page, {refuseApprove = false, manager = false, refuseMove = false, late = [{task_id: 'D17', title: 'Sim stuck checks', stage: 'Build', hours: 52, limit_hours: 48, since: stamp}]} = {}) {
  const state = {posts: [], queueReads: 0, refuseApprove, refuseMove, moves: [], prefs: {}, attachments: [], managerReads: 0};
  const shown = manager ? {...type, manager: 'product-manager'} : type;
  const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui) { const file = uiFile(ui[1]); if (fs.existsSync(file)) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')}); }
    if (p === '/api/me') return json(me);
    if (p === '/api/employees') return json([]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}]});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/task-types') return json({types: [shown]});
    if (p === '/api/v2/pipelines/dev-tickets/manager') {
      state.managerReads++;
      const run = (id, at, status, error) => ({id, started_at: at, finished_at: at, status, error});
      return json({pipeline_id: 'dev-tickets', manager: 'product-manager', every_minutes: 60, missed_runs: 0, last_run: run('r1', '2026-11-07T12:10:00Z', 'ok', null),
        over_limit: late,
        log: [run('r1', '2026-11-07T12:10:00Z', 'ok', null), run('r2', '2026-11-07T11:10:00Z', 'failed', 'The model was unreachable'),
          {id: 'a1', task_id: 'D52', action: 'ask', target_actor: ME, note: 'Screenshots are waiting on you.', created_at: '2026-11-07T12:10:00Z'}]});
    }
    const pref = p.match(/^\/api\/v2\/preferences\/([a-z0-9_.-]+)$/);
    if (pref) {
      if (req.method() === 'POST') { state.prefs[pref[1]] = (req.postDataJSON() || {}).value; return json({ok: true}); }
      return json(pref[1] in state.prefs ? {value: state.prefs[pref[1]]} : {});
    }
    const reqs = p.match(/^\/api\/v2\/tasks\/([^/]+)\/requirements$/);
    if (reqs && req.method() === 'GET') {
      const found = tasks().find(x => x.id === reqs[1]);
      return json({requirements: (found?.requirements || []).map(r => ({id: r.id, name: r.name, kind: r.id === 'r-shots' ? 'attachment' : 'run', stage_id: found.step_id,
        stage: found.step.name, state: r.state === 'missing' ? 'none' : r.state, why: r.detail || null, reviewed_by: 'person', reviewer_id: r.next_actor, version: 1, decision: null, attachment: null}))});
    }
    const atts = p.match(/^\/api\/v2\/tasks\/([^/]+)\/requirements\/([^/]+)\/attachments$/);
    if (atts) {
      if (req.method() === 'POST') { const body = req.postDataJSON() || {}; state.attachments.push(body); state.posts.push({path: 'attach', body});
        return json({attachment: {id: 'att-' + state.attachments.length, source: body.source, url: body.url || null, file_id: body.file_id || null, by: ME, at: stamp, work_version: 1}}); }
      return json({attachments: state.attachments.map((a, i) => ({id: 'att-' + (i + 1), source: a.source, url: a.url || null, file_id: a.file_id || null, by: ME, at: stamp, work_version: 1}))});
    }
    const mv = p.match(/^\/api\/v2\/tasks\/([^/]+)$/);
    if (mv && req.method() === 'POST') {
      const body = req.postDataJSON() || {};
      state.moves.push(body);
      if (state.refuseMove && !body.override_requirements) return json({error: {code: 'requirement', detail: 'Screenshots is not met'}}, 422);
      return json({task: {...tasks().find(t => t.id === mv[1]), ...(body.step ? {step_id: body.step, step: {...steps.find(x => x.id === body.step), type_id: 'dev-tickets'}} : {}), version: 2}});
    }
    if (p === '/api/v2/tasks' && req.method() === 'GET') return json({tasks: tasks(), next_offset: null});
    const one = p.match(/^\/api\/v2\/tasks\/([^/]+)$/);
    if (one && req.method() === 'GET') {
      const found = tasks().find(t => t.id === one[1]);
      return json({task: found, children: [], parent: null, comments: [], events: [], messages: [], mover: true});
    }
    const pipe = p.match(/^\/api\/v2\/pipelines\/([^/]+)\/review\/queue$/);
    if (pipe) { state.queueReads++; return json(queue()); }
    const action = p.match(/^\/api\/v2\/pipelines\/(.+)$/);
    if (action && req.method() === 'POST') {
      const body = req.postDataJSON() || {}, path = action[1];
      state.posts.push({path, body});
      if (path.endsWith('/approve')) {
        if (state.refuseApprove) { state.refuseApprove = false; return json({error: {code: 'stale', detail: 'changed'}}, 409); }
        return json({decision_id: 'dec-1', advanced: false});
      }
      if (path.startsWith('decisions/') || path.endsWith('/undo')) return json({undone: true});
      if (path.endsWith('/send-back') || path.endsWith('/message-manager')) return json({ok: true, decision_id: 'dec-2'});
      if (path.endsWith('/mark-met')) return json({decision_id: 'dec-3'});
    }
    return json(unrouted(p));
  });
  return state;
}

module.exports = {install, type, tasks};
