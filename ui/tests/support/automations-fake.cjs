// A stand-in hub for the Automations page scripts (ui/tests/automations-*.cjs): every /api/v2/automations route in
// redesign CONTRACT §2 over ui/tests/fixtures/redesign-automations.json (the Acme example), the BotOps chat,
// and the boot stubs. Writes change the copy a page reads back and are recorded in `writes`.
// A flow save (steps or trigger) is a new version; a viewer without edit rights gets 403; a built-in's delete gets 409.
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./page.cjs');
const {unrouted} = require('./routes.cjs');
const uiDir = path.join(__dirname, '../..');
const FIXTURE = path.join(__dirname, '../fixtures/redesign-automations.json');

const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};
const clone = x => JSON.parse(JSON.stringify(x));

// A bot's draft (`draft: true` in openAutomations): off, its version 1 pending until Turn on (CONTRACT §2).
const DRAFT = {id: 'au_draft', name: 'Receipts to archive', owner: 'person:dana', enabled: false, builtin: false, version: 1,
  trigger: {type: 'message', inbox: 'dana@acme.example', filters: []},
  steps: [{id: 'f1', type: 'filter', enabled: true, rules: [
    {when: [{field: 'message.subject', op: 'contains', value: 'receipt'}], match: 'all', then: ['archive'], stop: true}]}]};
const DRAFT_VERSIONS = [{n: 1, actor: 'bot:support', at: '2026-10-28T09:00:00Z', status: 'pending',
  summary: 'Archive receipts from billing'}];

// A system job's mirror (`system: true`): Tico runs it from its own code, so it reads only; the top of the org may pause it.
const SYSTEM = {id: 'au_sys_still', name: 'Still wanted check', owner: 'person:dana', enabled: true, builtin: true, readonly: true,
  kind: 'system', version: 1, schedule: 'Daily at 07:00', can_edit: false, can_pause: true,
  description: 'Asks each owner whether tasks open for 30 days are still wanted.',
  trigger: {type: 'event', name: 'tico.builtin.job.still_wanted'}, steps: [], controls: {on: true},
  source: {kind: 'system_job', id: 'job:still_wanted'}};
const systemRuns = (now = Date.now()) => Array.from({length: 8}, (_, i) => ({id: 'sys_' + (50 - i), automation_id: SYSTEM.id,
  started_at: new Date(now - (i * 3 + 1) * 3600000).toISOString(), finished_at: new Date(now - (i * 3 + 1) * 3600000 + 4000).toISOString(),
  status: i === 2 ? 'failed' : 'ok', cost: 0, steps: [], started_by: 'Schedule', outcome: i === 2 ? 'Mail down' : 'Asked 3 owners',
  summary: i === 2 ? 'Mail down' : 'Asked 3 owners'}));

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
                                         proposal = false, system = false} = {}) {
  const fx = clone(JSON.parse(fs.readFileSync(FIXTURE, 'utf8')));
  if (system) fx.automations.push(clone(SYSTEM));
  if (draft) { fx.automations.push(clone(DRAFT)); fx.versions = {...fx.versions, au_draft: clone(DRAFT_VERSIONS)}; }
  tweak?.(fx);
  const owner = viewer === fx.me.id;
  const me = owner ? fx.me : {id: viewer, name: fx.people.find(p => p.id === viewer)?.name || viewer, email: viewer + '@acme.example',
    role: 'member', company_role: 'member', cloud: true};
  const config = {};
  const db = {
    list: fx.automations.map(a => ({...a, can_edit: owner && a.can_edit !== false, ...(a.can_pause != null ? {can_pause: owner && a.can_pause} : {})})),
    versions: {}, runs: {au_inbox: inboxRuns(), ...(system ? {[SYSTEM.id]: systemRuns()} : {})}, conv: null, messages: [],
    held: {},   // id → {release, fail}: that automation's GET waits for release(), then answers (or fails)
  };
  for (const a of db.list) {
    const old = fx.versions[a.id] || [{n: a.version, actor: a.owner, at: '2026-10-27T15:00:00Z'}];
    db.versions[a.id] = old.map(v => ({...v, status: v.status || 'active', flow: {trigger: clone(a.trigger), steps: clone(a.steps)}}));
  }
  if (proposal) {
    const a = db.list.find(x => x.id === 'au_inbox'), steps = clone(a.steps);
    steps.find(s => s.type === 'filter')?.rules.unshift(clone(fx.proposal.rule));
    db.versions.au_inbox.push({n: a.version + 1, actor: 'bot:support', at: '2026-10-28T09:00:00Z', status: 'pending',
      summary: fx.proposal.summary, flow: {trigger: clone(a.trigger), steps}});
  }
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
    db.versions[a.id].push({n: a.version, actor, at: new Date().toISOString(), status: 'active', flow: {trigger: clone(a.trigger), steps: clone(a.steps)}});
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
    if (method !== 'GET') writes.push({method, path: p, body});

    if (p === '/api/v2/automations/question-sets') return json({sets: fx.question_sets});
    if (p === '/api/v2/automations' && method === 'POST') {
      // A new one (Blank): the viewer's own, version 1.
      const a = {...clone(body), id: 'au_' + (db.list.length + 1), version: 1, can_edit: true};
      db.list.push(a);
      db.versions[a.id] = [{n: 1, actor: 'person:' + me.id, at: new Date().toISOString(), status: 'active', flow: {trigger: clone(a.trigger), steps: clone(a.steps)}}];
      return json(full(a));
    }
    if (p === '/api/v2/automations') return json({automations: db.list.map(row), next: null});
    // The overview's reads: every run in the range across the list, newest first, and its counts per bucket.
    const RANGES = {'5m': [300, 10], '1h': [3600, 12], '24h': [86400, 24], '7d': [604800, 28]};
    const inRange = range => {
      const [secs] = RANGES[range] || RANGES['1h'], since = Date.now() - secs * 1000;
      return Object.values(db.runs).flat().filter(r => new Date(r.started_at).getTime() >= since && find(r.automation_id))
        .sort((x, y) => y.started_at.localeCompare(x.started_at));
    };
    if (p === '/api/v2/automations/runs') {
      const q = url.searchParams, ids = (q.get('ids') || '').split(',').filter(Boolean), status = q.get('status') || '';
      const all = inRange(q.get('range')).filter(r => (!ids.length || ids.includes(r.automation_id))
        && (!status || (status === 'done' ? ['ok', 'empty', 'skipped'].includes(r.status) : r.status === status)));
      const from = q.get('before') ? all.findIndex(r => r.id === q.get('before')) + 1 : 0, limit = Number(q.get('limit') || 50);
      const rows = all.slice(from, from + limit).map(r => {
        const a = find(r.automation_id);
        return {...r, steps: undefined, automation_name: a.name, trigger: a.kind || a.trigger.type, owner: a.owner,
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
        if (body.steps || body.trigger) {
          if (body.steps) a.steps = body.steps;
          if (body.trigger) a.trigger = body.trigger;
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
        return json({versions: db.versions[a.id].filter(v => !status || v.status === status)});
      }
      if ((m = rest.match(/^versions\/(\d+)\/restore$/)) && method === 'POST') {
        const v = db.versions[a.id].find(x => x.n === +m[1]);
        if (!v) return refuse(404, 'not_found', 'No such version');
        a.trigger = clone(v.flow.trigger); a.steps = clone(v.flow.steps);
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
      const mine = {id: 'msg' + n, from_actor: 'human:' + me.id, body: body.text, refs: body.refs || {}, created_at: at, kind: 'say'};
      db.messages.push(mine);
      const a = find(body.refs?.automation);
      let reply = 'Which inbox?';
      if (a) {
        a.steps.find(s => s.type === 'filter')?.rules.unshift(clone(fx.proposal.rule));
        newVersion(a);
        reply = `Saved v${a.version}: receipts are archived first.`;
      } else if (!/\?/.test(body.text)) {
        const on = /turn it on/i.test(body.text);
        db.list.push({id: 'au_new', name: 'Sales leads', owner: 'person:' + me.id, enabled: on, builtin: false, version: 1, can_edit: true,
          trigger: {type: 'message', inbox: 'sales@acme.example', filters: []},
          steps: [{id: 'f1', type: 'filter', enabled: true, rules: [{when: [{field: 'message.subject', op: 'contains', value: 'demo'}], match: 'all', then: [{do: 'label', args: {name: 'Lead'}}], stop: false}]}]});
        db.versions.au_new = [{n: 1, actor: 'person:' + me.id, at, status: 'active', flow: {trigger: clone(db.list.at(-1).trigger), steps: clone(db.list.at(-1).steps)}}];
        reply = `Saved Sales leads: mail to sales@acme.example about a demo gets the Lead label. It is ${on ? 'on' : 'off'}.`;
      }
      db.messages.push({id: 'msg' + (n + 1), from_actor: 'bot:botops', body: reply, refs: {}, created_at: at, kind: 'say'});
      return json({conversation: db.conv, message: mine});
    }
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  // Hold an automation's GET until release() (fail: then answer 500), to land a slow answer late.
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
  await page.evaluate(() => Promise.all(document.getAnimations().map(a => a.finished.catch(() => {}))));   // the route's fade
  await page.screenshot({path: path.join(dir, name + '.png')});
}

module.exports = {openAutomations, PHONE, DESKTOP, sideways, shot};
