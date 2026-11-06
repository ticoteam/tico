// A stand-in hub for the Automations page scripts (ui/tests/automations-*.cjs): every /api/v2/automations route in
// redesign CONTRACT §2 over ui/tests/fixtures/redesign-automations.json (the Acme example), the BotOps chat,
// and the boot stubs. Writes change the copy a page reads back and are recorded in `writes`.
// A flow save (steps, trigger or destinations) is a new version; a viewer without edit rights gets 403; a built-in's delete gets 409.
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./page.cjs');
const {unrouted} = require('./routes.cjs');
const uiDir = path.join(__dirname, '../..');
const FIXTURE = path.join(__dirname, '../fixtures/redesign-automations.json');

const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};
const clone = x => JSON.parse(JSON.stringify(x));
// The hub stores a flow as json.dumps(flow, sort_keys=True) (backend/automations.py), so every object's keys come back
// sorted, at any depth; arrays keep their order. A flow saved here, and one loaded from the fixture, see the same.
const sortKeys = x => Array.isArray(x) ? x.map(sortKeys)
  : x && typeof x === 'object' ? Object.fromEntries(Object.keys(x).sort().map(k => [k, sortKeys(x[k])])) : x;
const sortFlow = a => {
  const out = {...a};
  for (const k of ['trigger', 'steps', 'destinations']) if (out[k] !== undefined) out[k] = sortKeys(out[k]);
  return out;
};
// A version's flow: the trigger, the steps and (a board's) destinations.
const flowOf = a => ({trigger: clone(a.trigger), steps: clone(a.steps), ...(a.destinations ? {destinations: clone(a.destinations)} : {})});
// The hub's own refusals that a board save could trip (backend/automation_flow.py): a decide question needs
// instructions, a choice two options, an llm step a prompt and a choose's default one of its to; steps nest in paths
// and branches.
function flowProblem(steps) {
  for (const s of steps || []) {
    if (!s || typeof s !== 'object') continue;
    if (s.type === 'llm' && !String(s.prompt ?? '').trim()) return `${s.id}: an llm step needs a prompt`;
    if (s.type === 'llm' && s.choose && !(s.choose.to || []).includes(s.choose.default)) return `${s.id}: choose.default is one of choose.to`;
    if (s.type === 'decide') for (const [id, q] of Object.entries(s.questions || {})) {
      if (q?.instructions == null || (typeof q.instructions === 'string' && !q.instructions.trim())) return `${s.id}.${id}: a question needs instructions`;
      // clients/judge.py MAX_INSTRUCTIONS, counted in characters as Python counts them.
      if ([...(typeof q?.instructions === 'string' ? q.instructions : JSON.stringify(q?.instructions ?? ''))].length > 8000) return `${id}: instructions over 8000 characters`;
      if (q?.type === 'choice' && (Array.isArray(q.criteria) ? q.criteria : Object.keys(q.criteria || {})).length < 2) return `${s.id}.${id}: a choice is a criteria map of at least two options`;
    }
    for (const kid of [s.steps, s.then, s.else, ...(Array.isArray(s.paths) ? s.paths.map(x => x?.steps) : [])])
      if (Array.isArray(kid)) { const bad = flowProblem(kid); if (bad) return bad; }
  }
  return '';
}

// The inbox templates (backend/automation_templates): the listing, and a fixed flow for each. Blank is the real one's
// shape: no rules, no questions, Sort to Me or Archive. The others fill to one flow (their names aside): a rule, a
// question, and an LLM that is a draft turn as a person's or a bot's owner, and Sort, every option routed, for a group.
const TEMPLATES = [{id: 'founder', name: 'Founder', destinations: ['Me', 'Legal', 'Engineering', 'Archive']},
  {id: 'support', name: 'Support desk', destinations: ['Support', 'Engineering', 'Archive']},
  {id: 'sales', name: 'Sales', destinations: ['Sales', 'Archive']}, {id: 'blank', name: 'Blank', destinations: ['Me', 'Archive']}];
function filledTemplate(tid, owner, inbox) {
  const destinations = [{id: 'me', name: 'Me', color: 'amber', then: [{do: 'leave_for_you', args: {}}]},
    {id: 'legal', name: 'Legal', color: 'orange', then: [{do: 'create_task', args: {title: 'Legal: {{message.subject}}', owner: 'bot:support'}}]},
    {id: 'eng', name: 'Engineering', color: 'violet', then: [{do: 'create_task', args: {title: 'Bug: {{message.subject}}', owner: 'bot:eng-lead'}}]},
    {id: 'archive', name: 'Archive', color: 'gray', then: [{do: 'archive', args: {}}]}];
  const name = TEMPLATES.find(t => t.id === tid).name, trigger = {type: 'message', inbox};
  if (tid === 'blank') {
    const two = [destinations[0], destinations[3]];
    return {name, owner, enabled: false, trigger, destinations: two, steps: [
      {id: 'rules', type: 'filter', rules: []},
      {id: 'sort', type: 'decide', level: 'high', out: 'sort', questions: {route: {type: 'choice',
        instructions: 'Where does this message belong? Pick the one that fits best.', criteria: {me: 'The owner should see it', archive: 'Nothing is asked of anyone'}}}},
      {id: 'route', type: 'filter', rules: [...two.map(d => ({when: [{field: 'sort.route', op: 'is', value: d.id, min: 0.5}], to: d.id, next: 'end', stop: true})),
        {when: [], to: 'me', next: 'end', stop: true}]}]};
  }
  const to = destinations.map(d => d.id), [kind, id] = [owner.split(':')[0], owner.split(':').slice(1).join(':')];
  const steps = [
    {id: 'rules', type: 'filter', rules: [{when: [{field: 'message.subject', op: 'matches', value: 'subpoena|lawsuit'}], match: 'all',
      then: [{do: 'label', args: {name: 'STARRED'}}], to: 'legal', next: 'end', stop: true}]},
    {id: 'ask', type: 'decide', level: 'low', out: 'q', questions: {bug: {type: 'noul', instructions: 'Is it a bug report?'}}},
    {id: 'route', type: 'filter', rules: [{when: [{field: 'q.bug', op: 'is', value: 'yes', min: 0.75}], to: 'eng', next: 'end', stop: true}]}];
  if (kind === 'group') steps.push(
    {id: 'draft', type: 'decide', level: 'high', out: 'draft', questions: {route: {type: 'choice', instructions: 'Where does this message belong?',
      criteria: Object.fromEntries(destinations.map(d => [d.id, d.name]))}}},
    {id: 'draft_route', type: 'filter', rules: [...to.map(d => ({when: [{field: 'draft.route', op: 'is', value: d, min: 0.5}], to: d, next: 'end', stop: true})),
      {when: [], to: 'me', next: 'end', stop: true}]});
  else steps.push({id: 'draft', type: 'llm', out: 'draft', as: (kind === 'person' ? 'assistant:' : 'bot:') + id,
    prompt: 'Route this message first. When unsure, choose me.', choose: {to, default: 'me', mode: 'draft'}});
  return {name, owner, enabled: false, trigger, steps, destinations};
}

// A bot's draft (`draft: true` in openAutomations): off, its version 1 pending until Turn on (CONTRACT §2).
const DRAFT = {id: 'au_draft', name: 'Receipts to archive', owner_id: 'person:dana', enabled: false, builtin: false, version: 1,
  trigger: {type: 'message', inbox: 'dana@acme.example', filters: []},
  steps: [{id: 'f1', type: 'filter', enabled: true, rules: [
    {when: [{field: 'message.subject', op: 'contains', value: 'receipt'}], match: 'all', then: ['archive'], stop: true}]}]};
const DRAFT_VERSIONS = [{n: 1, actor: 'bot:support', at: '2026-10-28T09:00:00Z', status: 'pending',
  summary: 'Archive receipts from billing'}];

// A system job's mirror (`system: true`): Tico runs it from its own code, so it reads only; the top of the org may pause it.
const SYSTEM = {id: 'au_sys_still', name: 'KPI stale asks check', owner_id: 'person:dana', enabled: true, builtin: true, readonly: true,
  kind: 'system', version: 1, schedule: 'Daily at 07:00', can_edit: false, can_pause: true,
  description: 'Asks a KPI keeper for a reading when it goes out of date.',
  trigger: {type: 'event', name: 'tico.builtin.job.kpi_asks'}, steps: [], controls: {on: true},
  source: {kind: 'system_job', id: 'job:kpi_asks'}};
const systemRuns = (now = Date.now()) => Array.from({length: 8}, (_, i) => ({id: 'sys_' + (50 - i), automation_id: SYSTEM.id,
  started_at: new Date(now - (i * 3 + 1) * 3600000).toISOString(), finished_at: new Date(now - (i * 3 + 1) * 3600000 + 4000).toISOString(),
  status: i === 2 ? 'failed' : 'ok', cost: 0, steps: [], started_by: 'Schedule', outcome: i === 2 ? 'Mail down' : 'Asked 3 owners',
  summary: i === 2 ? 'Mail down' : 'Asked 3 owners'}));

// Maintenance (`maintenance: true`): backend/maintenance.py's listing at the migration's defaults, Learn's beta numbers
// (backend/learnings.py beta_stats), and 30 hourly runs, newest first: one failed at Dream, one a Run now of one step.
const MAINT_ORDER = ['collect', 'clean_up', 'check_health', 'find_stuck', 'learn', 'suggest', 'update', 'dream'];
const MAINT_NAMES = {collect: 'Collect', clean_up: 'Clean up', check_health: 'Check health', find_stuck: 'Find stuck', learn: 'Learn',
  suggest: 'Suggest', update: 'Update', dream: 'Dream'};
const MAINT_EVERY_RUN = ['collect', 'clean_up', 'check_health'], MAINT_BOTS = ['find_stuck', 'dream'];
function maintSteps() {
  const d = {find_stuck: {frequency: 'custom', custom_every: 6, custom_unit: 'hours', max_bots: 10}, learn: {enabled: false, frequency: 'day'},
    suggest: {frequency: 'day'}, update: {frequency: 'day'}, dream: {frequency: 'day', max_bots: 3}};
  return MAINT_ORDER.map(key => ({key, name: MAINT_NAMES[key], enabled: true, frequency: 'run', custom_every: null, custom_unit: null,
    max_bots: null, ...d[key], fixed: MAINT_EVERY_RUN.includes(key),
    last_started_at: null, last_ran_at: key === 'learn' ? null : new Date(Date.now() - 3600000).toISOString(), cursor_at: null,
    ...(key === 'suggest' ? {consent: false} : {}),
    ...(key === 'learn' ? {stats: {days: 7, runs: 6, items_read: 42, items_sent: {support: 3, 'eng-lead': 2}, saved: 4, dropped: 1,
      unreported: 0, not_sent: 0, expired: 0, cost_usd: 0.18}} : {})}));
}
function maintRuns(now = Date.now()) {
  return Array.from({length: 30}, (_, i) => {
    const at = now - (i + 1) * 3600000, failed = i === 2, one = i === 4;
    const line = (step, status, extra = {}) => ({step, status, reason: '', checked: null, found: null, woke: null, cost_usd: 0,
      started_at: new Date(at).toISOString(), finished_at: new Date(at + 2000).toISOString(), ...extra});
    const steps = one ? [line('dream', 'ran', {checked: 5, found: 2, woke: 2, cost_usd: 0.05})] : [
      line('collect', 'ran', {checked: 120, found: 14}), line('clean_up', 'ran'), line('check_health', 'skipped', {reason: 'not due'}),
      line('find_stuck', 'skipped', {reason: 'not due'}), line('learn', 'off'), line('suggest', 'skipped', {reason: 'Privacy consent is off'}),
      line('update', 'skipped', {reason: 'not due'}),
      line('dream', failed ? 'failed' : 'skipped', failed ? {reason: 'model timed out', cost_usd: 0.02} : {reason: 'not due'})];
    return {id: 'mrun_' + (100 - i), occurrence_key: one ? null : 'hourly:' + i, trigger: one ? 'run_now' : 'schedule', step: one ? 'dream' : null,
      started_at: new Date(at).toISOString(), finished_at: new Date(at + 9000).toISOString(), heartbeat_at: new Date(at + 9000).toISOString(),
      ok: !failed, result: failed ? '1 failed, 2 ran, 4 skipped' : one ? '1 ran' : '2 ran, 5 skipped',
      cost_usd: steps.reduce((t, x) => t + x.cost_usd, 0), steps};
  });
}

// 30 runs of the inbox automation, newest first: most reply, a few fail at the LLM, a few hit the spend limit.
function inboxRuns(now = Date.now()) {
  return Array.from({length: 30}, (_, i) => {
    const failed = i % 9 === 4, capped = i % 7 === 3 && !failed;
    const steps = [
      {id: 'f1', type: 'filter', status: 'ok', outputs: {rule: null}, cost: 0},
      {id: 'dc1', type: 'decide', status: 'ok', cost: 0.004, outputs: {
        bucket: {choice: 'reply', confidence: 0.82, probabilities: {reply: 0.82, archive: 0.1, route: 0.08}},
        is_ask: {noul: 0.64}, money: {noul: 0.12}, legal: {noul: 0.03}}},
      {id: 'p1', type: 'paths', status: 'ok', outputs: {path: 0}, cost: 0},
      {id: 'l1s', type: 'llm', status: failed ? 'failed' : capped ? 'spend_cap' : 'ok', cost: failed || capped ? 0 : 0.03,
        outputs: failed ? {error: 'model timed out'} : capped ? {} : {text: 'Thanks Sam, Thursday at 3 works.'}},
    ];
    if (!failed && !capped) steps.push({id: 'a1', type: 'action', status: 'ok', outputs: {result: 'draft saved'}, cost: 0});
    return {id: 'run_' + (100 - i), automation_id: 'au_inbox', version: i < 12 ? 7 : 6, trigger_ref: 'mail:m' + i,
      started_at: new Date(now - i * 17 * 60000).toISOString(), finished_at: new Date(now - i * 17 * 60000 + 9000).toISOString(),
      status: failed ? 'failed' : 'ok', cost: steps.reduce((t, s) => t + s.cost, 0), steps};
  });
}

// One page on the fake hub. viewer: the person signed in (any but the fixture's owner sees nothing editable).
// tweak(fx) changes the fixture copy before the page reads it. proposal: a bot's pending version on the inbox one.
async function openAutomations(browser, {viewport = PHONE, hash = '#/automations', viewer = 'dana', tweak = null, draft = false,
                                         proposal = false, system = false, maintenance = false} = {}) {
  const fx = clone(JSON.parse(fs.readFileSync(FIXTURE, 'utf8')));
  if (system) fx.automations.push(clone(SYSTEM));
  if (draft) { fx.automations.push(clone(DRAFT)); fx.versions = {...fx.versions, au_draft: clone(DRAFT_VERSIONS)}; }
  tweak?.(fx);
  const owner = viewer === fx.me.id;
  const me = owner ? fx.me : {id: viewer, name: fx.people.find(p => p.id === viewer)?.name || viewer, email: viewer + '@acme.example',
    role: 'member', company_role: 'member', cloud: true};
  const config = {};
  const db = {
    list: fx.automations.map(a => ({...sortFlow(a), can_edit: owner && a.can_edit !== false, ...(a.can_pause != null ? {can_pause: owner && a.can_pause} : {})})),
    versions: {}, runs: {au_inbox: inboxRuns(), ...(system ? {[SYSTEM.id]: systemRuns()} : {})}, conv: null, messages: [],
    held: {},   // id → {wait, fail}: an automation's GET or Maintenance's PATCH waits for release()
  };
  for (const a of db.list) {
    const old = fx.versions[a.id] || [{n: a.version, actor: a.owner_id, at: '2026-10-27T15:00:00Z'}];
    db.versions[a.id] = old.map(v => ({...v, status: v.status || 'active', flow: flowOf(a)}));
  }
  if (proposal) {
    const a = db.list.find(x => x.id === 'au_inbox'), steps = clone(a.steps);
    steps.find(s => s.type === 'filter')?.rules.unshift(sortKeys(fx.proposal.rule));
    db.versions.au_inbox.push({n: a.version + 1, actor: 'bot:support', at: '2026-10-28T09:00:00Z', status: 'pending',
      summary: fx.proposal.summary, flow: {trigger: clone(a.trigger), steps}});
  }
  if (maintenance) db.maint = {settings: {schedule: 'hourly', paused: false, monthly_cap_usd: null}, cap_env: 50, steps: maintSteps(),
    runs: maintRuns(), live: null};
  // GET only reads; the test ends a live run as the backend's worker does.
  const maintListing = () => {
    const mt = db.maint;
    return clone({settings: {...mt.settings, monthly_cap_usd_in_effect: mt.settings.monthly_cap_usd ?? mt.cap_env}, steps: mt.steps,
      last_run: mt.runs[0] || null, live_run_id: mt.live?.id || null, can_change: owner,
      next_run_at: mt.settings.paused ? null : new Date(Date.now() + 1800000).toISOString()});
  };
  const writes = [];
  const find = id => db.list.find(a => a.id === id);
  // A Decide step on a saved set comes back with the set's questions alongside, read-only (CONTRACT §2).
  const withSets = steps => (steps || []).map(s => {
    const out = {...s};
    if (s.type === 'decide' && s.set) out.questions = clone(fx.question_sets.find(q => q.id === s.set)?.questions || {});
    if (s.type === 'paths') {
      out.paths = (s.paths || []).map(p => ({...p, steps: withSets(p.steps)}));
      out.otherwise = {steps: withSets(s.otherwise?.steps)};
    }
    return out;
  });
  const full = a => ({...clone(a), steps: withSets(a.steps)});
  // A list row carries how many versions wait for a person (the Pending pill)
  // and its newest run (the Last run column).
  const row = a => {
    const {steps, ...rest} = clone(a), last = db.runs[a.id]?.[0];
    return {...rest, pending: db.versions[a.id].filter(v => v.status === 'pending').length,
      ...(last && !rest.last_run ? {last_run: {id: last.id, started_at: last.started_at, status: last.status}} : {})};
  };
  const nextN = id => Math.max(...db.versions[id].map(v => v.n)) + 1;
  const newVersion = (a, actor = 'person:' + me.id) => {
    a.version = nextN(a.id);
    db.versions[a.id].push({n: a.version, actor, at: new Date().toISOString(), status: 'active', flow: flowOf(a)});
  };

  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    const refuse = (status, code, detail) => json({error: {code, detail}}, status);
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events') return;
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/employees') return json(fx.employees);
    if (p === '/api/humans') return json({people: fx.people.map(x => ({...x, org_parent: ''})), org_groups: []});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/groups') return json({groups: fx.groups});
    const body = method === 'GET' ? null : req.postDataJSON?.() ?? null;
    let m0;
    if (method !== 'GET') writes.push({method, path: p, body});

    if (db.maint && p.startsWith('/api/v2/maintenance')) {
      const mt = db.maint;
      if (method !== 'GET' && !owner) return refuse(403, 'forbidden', 'Only an owner or an admin changes Maintenance');
      if (p === '/api/v2/maintenance' && method === 'GET') return json(maintListing());
      if (p === '/api/v2/maintenance' && method === 'PATCH') {
        const hold = db.held.maintenance;
        if (hold) { await hold.wait; if (hold.fail) return refuse(500, 'boom', 'The hub stalled'); }
        // backend/maintenance.py save_settings and save_step's refusals.
        if (body.schedule != null && !['hourly', '2h', '3h', '6h', '12h', 'daily'].includes(body.schedule)) return refuse(422, 'invalid', 'No such schedule');
        for (const ch of body.steps || []) {
          const st = mt.steps.find(x => x.key === ch.key);
          if (!st) return refuse(404, 'not_found', 'No such Maintenance step');
          const after = {...st, ...Object.fromEntries(Object.entries(ch).filter(([, v]) => v != null))};
          if (MAINT_EVERY_RUN.includes(st.key) && after.frequency !== 'run') return refuse(422, 'invalid', `${st.name} runs every run`);
          if (st.key === 'collect' && !after.enabled) return refuse(422, 'invalid', 'Collect is always on');
          if (after.frequency === 'custom' && (after.custom_every == null || !['hours', 'days'].includes(after.custom_unit))) return refuse(422, 'invalid', 'A custom schedule needs a number and hours or days');
          if (ch.max_bots != null && !MAINT_BOTS.includes(st.key)) return refuse(422, 'invalid', `${st.name} takes no number of bots`);
        }
        for (const k of ['schedule', 'paused', 'monthly_cap_usd']) if (body[k] != null) mt.settings[k] = body[k];
        for (const ch of body.steps || []) {
          const st = mt.steps.find(x => x.key === ch.key);
          for (const [k, v] of Object.entries(ch)) if (k !== 'key' && v != null) st[k] = v;
          if (st.frequency !== 'custom') st.custom_every = st.custom_unit = null;
        }
        return json(maintListing());
      }
      if (p === '/api/v2/maintenance/runs') {
        const limit = Number(url.searchParams.get('limit') || 25), before = url.searchParams.get('before');
        const rows = mt.runs.filter(r => !before || r.started_at < before).slice(0, limit);
        return json({runs: clone(rows), next: rows.length === limit ? rows.at(-1).started_at : null});
      }
      if (p === '/api/v2/maintenance/run' && method === 'POST') {
        if (mt.live) return json({run: clone(mt.live), started: false});
        const at = new Date().toISOString();
        mt.live = {id: 'mrun_now', occurrence_key: null, trigger: 'run_now', step: body?.step || null, started_at: at, finished_at: null,
          heartbeat_at: at, ok: null, result: null, cost_usd: 0, steps: [{step: 'collect', status: 'ran', reason: '', checked: 3, found: 1, woke: null,
            cost_usd: 0, started_at: at, finished_at: at}]};
        mt.runs.unshift(mt.live);
        return json({run: clone(mt.live), started: true});
      }
    }
    if (p === '/api/v2/automations/question-sets') return json({sets: fx.question_sets});
    if (p === '/api/v2/task-types') return json({types: fx.task_types || []});
    // A template fill saves nothing; like the hub it refuses an owner the viewer may not make an automation for (another
    // person) and one that can't hear the inbox (a group on an email address).
    if (p === '/api/v2/automations/templates') return json({templates: clone(TEMPLATES)});
    if ((m0 = p.match(/^\/api\/v2\/automations\/templates\/([^/]+)$/))) {
      const tid = decodeURIComponent(m0[1]), owner = url.searchParams.get('owner') || '', inbox = url.searchParams.get('inbox') || '';
      if (!TEMPLATES.some(t => t.id === tid)) return refuse(404, 'not_found', 'No such template');
      if (!/^(person|bot|group):./.test(owner) || !inbox) return refuse(422, 'invalid', 'owner is person:<id>, bot:<slug> or group:<id>');
      if (owner.startsWith('person:') && owner !== 'person:' + me.id) return refuse(403, 'forbidden', `You may not make an automation for ${owner}`);
      if (owner.startsWith('group:') && inbox.includes('@')) return refuse(403, 'forbidden', `${owner} may not read ${inbox}`);
      return json(filledTemplate(tid, owner, inbox));
    }
    if (p === '/api/v2/automations' && method === 'POST') {
      // A new one (Blank, or New inbox's): version 1.
      const bad = flowProblem(body.steps);
      if (bad) return refuse(422, 'invalid_flow', bad);
      const a = {...sortFlow(body), owner_id: body.owner, id: 'au_' + (db.list.length + 1), version: 1, can_edit: true};
      db.list.push(a);
      db.versions[a.id] = [{n: 1, actor: 'person:' + me.id, at: new Date().toISOString(), status: 'active', flow: flowOf(a)}];
      return json(full(a));
    }
    if (p === '/api/v2/automations') return json({automations: db.list.map(row), next: null});
    // The overview's reads: every run in the range across the list, newest first, and its counts per bucket.
    const RANGES = {'5m': [300, 10], '1h': [3600, 12], '24h': [86400, 24], '7d': [604800, 28]};
    // The Maintenance run's log is listed under its row, as backend/automation_overview.py does (`maint_row`).
    const maintRuns = () => (db.maint?.runs || []).map(r => {
      const status = !r.finished_at ? 'running' : r.ok ? 'ok' : 'failed';
      return {id: 'mnt_' + r.id, automation_id: 'maintenance', automation_name: 'Maintenance', trigger: 'system', owner_id: 'tico',
        started_at: r.started_at, finished_at: r.finished_at, status, cost: r.cost_usd || 0,
        started_by: r.trigger === 'run_now' ? 'Run now' : 'Scheduled', outcome: r.result || (status === 'ok' ? 'Done' : status === 'failed' ? 'Failed' : 'Running'),
        step_count: null, count: 1, maint: true};
    });
    const inRange = range => {
      const [secs] = RANGES[range] || RANGES['1h'], since = Date.now() - secs * 1000;
      return [...Object.values(db.runs).flat().filter(r => find(r.automation_id)), ...maintRuns()]
        .filter(r => new Date(r.started_at).getTime() >= since)
        .sort((x, y) => y.started_at.localeCompare(x.started_at));
    };
    if (p === '/api/v2/automations/runs') {
      const q = url.searchParams, ids = (q.get('ids') || '').split(',').filter(Boolean), status = q.get('status') || '';
      const all = inRange(q.get('range')).filter(r => (!ids.length || ids.includes(r.automation_id))
        && (!status || (status === 'done' ? ['ok', 'empty', 'skipped'].includes(r.status) : r.status === status)));
      const from = q.get('before') ? all.findIndex(r => r.id === q.get('before')) + 1 : 0, limit = Number(q.get('limit') || 50);
      const rows = all.slice(from, from + limit).map(r => {
        if (r.maint) return {...r, maint: undefined};
        const a = find(r.automation_id);
        return {...r, steps: undefined, automation_name: a.name, trigger: a.kind || a.trigger.type, owner_id: a.owner_id,
          started_by: r.started_by || 'From Sam Lee · Thursday?', outcome: r.outcome || (r.status === 'failed' ? 'LLM failed: model timed out' : 'Draft reply'),
          step_count: (r.steps || []).length, count: 1};
      });
      return json({runs: rows, next: from + limit < all.length ? rows.at(-1).id : null});
    }
    if (p === '/api/v2/automations/stats') {
      const range = url.searchParams.get('range') || '1h', [secs, n] = RANGES[range], width = secs * 1000 / n, since = Date.now() - secs * 1000;
      const per = new Map(), blank = () => Array.from({length: n}, () => ({ok: 0, failed: 0}));
      for (const r of inRange(range)) {
        const s = per.get(r.automation_id) || {id: r.automation_id, runs: 0, failed: 0, cost: 0, buckets: blank()};
        const b = Math.min(n - 1, Math.floor((new Date(r.started_at).getTime() - since) / width)), bad = r.status === 'failed';
        s.runs++; s.failed += bad; s.cost += r.cost || 0; s.buckets[b][bad ? 'failed' : 'ok']++;
        per.set(r.automation_id, s);
      }
      const totals = {runs: 0, failed: 0, cost: 0, buckets: blank()};
      for (const s of per.values()) { totals.runs += s.runs; totals.failed += s.failed; totals.cost += s.cost;
        s.buckets.forEach((x, i) => { totals.buckets[i].ok += x.ok; totals.buckets[i].failed += x.failed; }); }
      return json({range, bucket_seconds: width / 1000, automations: [...per.values()], totals});
    }
    let m =p.match(/^\/api\/v2\/automations\/([^/]+)(?:\/(.*))?$/);
    if (m) {
      const a = find(decodeURIComponent(m[1])), rest = m[2] || '';
      if (!a) return refuse(404, 'not_found', 'No such automation');
      if (method !== 'GET' && !a.can_edit) return refuse(403, 'forbidden', 'You can’t change this automation');
      if (!rest && method === 'GET') {
        const hold = db.held[a.id];
        if (hold) { await hold.wait; if (hold.fail) return refuse(500, 'boom', 'The hub stalled'); }
        return json(full(a));
      }
      if (!rest && method === 'PATCH') {
        for (const k of ['enabled', 'spend_cap', 'name']) if (k in body) { if (body[k] === null) delete a[k]; else a[k] = body[k]; }
        if (body.steps || body.trigger || body.destinations) {
          const bad = body.steps && flowProblem(body.steps);
          if (bad) return refuse(422, 'invalid_flow', bad);
          if (body.steps) a.steps = sortKeys(body.steps);
          if (body.trigger) a.trigger = sortKeys(body.trigger);
          if (Array.isArray(body.destinations)) a.destinations = sortKeys(body.destinations);
          newVersion(a);
        }
        return json(full(a));
      }
      if (!rest && method === 'DELETE') {
        if (a.builtin) return refuse(409, 'builtin', 'Built-in automations can’t be deleted');
        db.list = db.list.filter(x => x !== a);
        return json({ok: true});
      }
      if (rest === 'runs') {
        const all = db.runs[a.id] || [], limit = Number(url.searchParams.get('limit') || 25), before = url.searchParams.get('before');
        const from = before ? all.findIndex(r => r.id === before) + 1 : 0, rows = all.slice(from, from + limit);
        return json({runs: rows, next: from + limit < all.length ? rows.at(-1).id : null});
      }
      // The run guard's hold: release queues its held runs, drop skips them (backend/automations.py `_unhold`).
      if (rest === 'held' && method === 'POST') {
        if (!a.hold) return refuse(409, 'not_on_hold', 'The automation is not on hold');
        const moved = a.hold.held;
        delete a.hold;
        for (const r of db.runs[a.id] || []) if (r.status === 'held') r.status = body.action === 'release' ? 'queued' : 'skipped';
        return json({...full(a), moved});
      }
      if (rest === 'versions') {
        const status = url.searchParams.get('status');
        // A version's flow comes back key-sorted as the hub stores it, including one a test put in the list directly.
        return json({versions: db.versions[a.id].filter(v => !status || v.status === status).map(v => ({...v, flow: sortFlow(v.flow)}))});
      }
      if ((m = rest.match(/^versions\/(\d+)\/restore$/)) && method === 'POST') {
        const v = db.versions[a.id].find(x => x.n === +m[1]);
        if (!v) return refuse(404, 'not_found', 'No such version');
        const f = sortFlow(v.flow);
        a.trigger = f.trigger; a.steps = f.steps;
        if (f.destinations) a.destinations = f.destinations; else delete a.destinations;
        // A pending version turned on becomes the current one; an older one comes back as a new version. A draft (off,
        // no version live yet) is turned on by its first pending version.
        if (v.status === 'pending') {
          const draftNow = !a.enabled && !db.versions[a.id].some(x => x.status === 'active');
          v.status = 'active'; a.version = v.n;
          if (draftNow) a.enabled = true;
        } else newVersion(a);
        return json(full(a));
      }
      if ((m = rest.match(/^versions\/(\d+)$/)) && method === 'DELETE') {
        const v = db.versions[a.id].find(x => x.n === +m[1] && x.status === 'pending');
        if (!v) return refuse(409, 'not_pending', 'Only a proposal can be discarded');
        db.versions[a.id] = db.versions[a.id].filter(x => x !== v);
        return json({ok: true});
      }
    }

    // BotOps, with the viewer's rights: a message about an automation saves the change as its new version; one from
    // New makes an automation, off unless the message says to turn it on; "?" in the text gets a question back.
    if (p === '/api/v2/conversations') return json({conversations: db.conv && url.searchParams.get('chat_with') === 'botops' ? [db.conv] : []});
    if (db.conv && p === `/api/v2/conversations/${db.conv.id}/snapshot`) return json({messages: db.messages, execution: null, has_more: false, next_before: null});
    if (p === '/api/v2/chat/botops' && method === 'POST') {
      db.conv ||= {id: 'conv_au', kind: 'chat', title: 'BotOps'};
      const n = db.messages.length, at = new Date().toISOString();
      const mine = {id: 'msg' + n, sender_id: 'human:' + me.id, body: body.text, refs: body.refs || {}, created_at: at, kind: 'say'};
      db.messages.push(mine);
      const a = find(body.refs?.automation);
      let reply = 'Which inbox?';
      if (a) {
        a.steps.find(s => s.type === 'filter')?.rules.unshift(sortKeys(fx.proposal.rule));
        newVersion(a);
        reply = `Saved v${a.version}: receipts are archived first.`;
      } else if (!/\?/.test(body.text)) {
        const on = /turn it on/i.test(body.text);
        db.list.push(sortFlow({id: 'au_new', name: 'Sales leads', owner_id: 'person:' + me.id, enabled: on, builtin: false, version: 1, can_edit: true,
          trigger: {type: 'message', inbox: 'sales@acme.example', filters: []},
          steps: [{id: 'f1', type: 'filter', enabled: true, rules: [{when: [{field: 'message.subject', op: 'contains', value: 'demo'}], match: 'all', then: [{do: 'label', args: {name: 'Lead'}}], stop: false}]}]}));
        db.versions.au_new = [{n: 1, actor: 'person:' + me.id, at, status: 'active', flow: {trigger: clone(db.list.at(-1).trigger), steps: clone(db.list.at(-1).steps)}}];
        reply = `Saved Sales leads: mail to sales@acme.example about a demo gets the Lead label. It is ${on ? 'on' : 'off'}.`;
      }
      db.messages.push({id: 'msg' + (n + 1), sender_id: 'bot:botops', body: reply, refs: {}, created_at: at, kind: 'say'});
      return json({conversation: db.conv, message: mine});
    }
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  // Hold an automation's GET or Maintenance's PATCH until release() (fail: then answer 500).
  const hold = (id, fail = false) => { let release; const wait = new Promise(r => { release = r; }); db.held[id] = {wait, fail}; return () => { delete db.held[id]; release(); }; };
  return {page, errors, writes, db, me, hold};
}

// No sideways scroll on the page itself (a path strip scrolls inside its own box).
const sideways = page => page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
// A screenshot for the build notes, when TICO_UI_SHOTS names a folder.
async function shot(page, name) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  // The route's fade and other one-offs; the Liquid Dot breathes forever, so an endless animation is not waited on.
  await page.evaluate(() => Promise.all(document.getAnimations().filter(a => a.effect?.getTiming().iterations !== Infinity)
    .map(a => a.finished.catch(() => {}))));
  await page.screenshot({path: path.join(dir, name + '.png')});
}

module.exports = {openAutomations, filledTemplate, PHONE, DESKTOP, sideways, shot};
