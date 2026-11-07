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
const type = {id: 'dev-tickets', name: 'Dev tickets', role: 'engineering', manager: null, steps: steps.map(s => ({...s, type_id: 'dev-tickets'}))};

function task(id, title, stepId, requirements) {
  const step = steps.find(s => s.id === stepId);
  return {id, title, body: '', owner_id: 'bot:b2', requester_id: 'human:ana', status: 'doing', lane: 'company', version: 1,
    labels: [], links: [], parts: {total: 0, done: 0}, rank: 1, created_at: stamp, updated_at: stamp,
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

// The review queue for the pipeline: two items for Ana (D52 screenshots, D61 spec), the first with three attachments.
function queue() {
  return {items: [
    {task_id: 'D52', requirement_id: 'r-shots', task_title: 'Assistant can approve a spec', stage_name: 'Build', pipeline_id: 'dev-tickets',
      assignee: {id: 'bot:b2', name: 'Builder 2'}, why: 'The bot is unsure about shot 2', bot_note: '2 of 3 match the spec',
      attachments: [{id: 'a1', label: 'Screenshots · 3', kind: 'image', url: 'https://example.test/shot.png'},
        {id: 'a2', label: 'Spec v1.3', kind: 'page', detail: 'Spec page'}, {id: 'a3', label: 'Test run', kind: 'link', detail: 'CI run 8812'}],
      claim: null, moves_to: 'Review 1'},
    {task_id: 'D61', requirement_id: 'r-spec61', task_title: 'Pipeline stages + manager', stage_name: 'Spec', pipeline_id: 'dev-tickets',
      assignee: {id: 'bot:b3', name: 'Builder 3'}, why: 'Spec needs your sign-off', bot_note: '', attachments: [{id: 'b1', label: 'Spec v2', kind: 'page', detail: 'Spec page'}],
      claim: null, moves_to: null},
  ]};
}

// Installs the fake server on a page. `state` collects what the tests check: posts, refusals and queue reads.
async function install(page, {refuseApprove = false} = {}) {
  const state = {posts: [], queueReads: 0, refuseApprove};
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
    if (p === '/api/v2/task-types') return json({types: [type]});
    if (p === '/api/v2/tasks' && req.method() === 'GET') return json({tasks: tasks(), next_offset: null});
    const one = p.match(/^\/api\/v2\/tasks\/([^/]+)$/);
    if (one && req.method() === 'GET') {
      const found = tasks().find(t => t.id === one[1]);
      return json({task: found, children: [], parent: null, comments: [], events: [], messages: [], mover: true});
    }
    const pipe = p.match(/^\/api\/v2\/pipelines\/([^/]+)\/review-queue$/);
    if (pipe) { state.queueReads++; return json(queue()); }
    const action = p.match(/^\/api\/v2\/pipelines\/(.+)$/);
    if (action && req.method() === 'POST') {
      const body = req.postDataJSON() || {}, path = action[1];
      state.posts.push({path, body});
      if (path.endsWith('/approve')) {
        if (state.refuseApprove) { state.refuseApprove = false; return json({error: {code: 'stale', detail: 'changed'}}, 409); }
        return json({decision_id: 'dec-1', moved_to: null});
      }
      if (path.startsWith('decisions/') || path.endsWith('/undo')) return json({ok: true});
      if (path.endsWith('/send-back') || path.endsWith('/message-manager')) return json({ok: true, decision_id: 'dec-2'});
      if (path.endsWith('/mark-met')) return json({decision_id: 'dec-3'});
    }
    return json(unrouted(p));
  });
  return state;
}

module.exports = {install, type, tasks};
