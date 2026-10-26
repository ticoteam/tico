// Task settings lifecycle, permissions, pagination and editor handoff.
// Based on the existing Rules fixture.
// Settings > Rules, What wakes a bot (ui/app/settings-rules.js). Guards: who gets Add, Edit and the switch (the server's
// can_change; a member gets none); an edit and a switch send the row's revision and the row reads back what was saved;
// a rule that makes an always-wake event wait shows the server's 422 in the form, not a toast; the task filter route
// asks for that task's rows and has its way back; at phone width nothing scrolls sideways; and the Releases card's
// Release now shows only to those the server lets ask (`can_release`). Suggestions: Apply and Undo post and the rule
// below is read again; the block hides when empty. A type row edits like any other: its step from the type's steps,
// and an action's own fields saved as params. An event with no task (webhook.received) swaps the task conditions for
// which webhook and the call's type, offers only wake_bot and send_webhook, and checks send_webhook's data is JSON
// (with {{…}} inside strings) before saving. Fixtures only; the event catalog is the backend's own.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {execFileSync} = require('node:child_process');
const path = require('node:path');
const CATALOG = JSON.parse(execFileSync(process.env.TICO_PYTHON || 'python3', ['-c', 'import json; from backend.event_catalog import listing; print(json.dumps(listing()))'],
  {cwd: path.join(__dirname, '../..')}).toString());

const TASK_BUILTINS = JSON.parse(execFileSync(process.env.TICO_PYTHON || 'python3', ['-c',
  "import json,sqlite3; from backend.task_settings import builtins; c=sqlite3.connect(':memory:'); c.execute('create table task_types(id,name,role)'); c.execute(\"insert into task_types values ('dev','Dev tickets','engineering')\"); print(json.dumps(builtins(c)))"],
  {cwd: path.join(__dirname, '../..')}).toString());
const FLOOR_422 = 'Person message always wakes the bot: a rule cannot make it wait';
const builtins = [
  {id: 'builtin:person_message', built_in: true, scope_kind: 'builtin', scope_id: '', group: 'always', event: 'person_message',
    words: 'a person messages it', then: 'wake', enabled: true, runs_7d: 14, no_op_rate_7d: 0.1},
  // A narrower filter on the same event comes first, as on the server; a team rule must not borrow its words.
  {id: 'builtin:own_write', built_in: true, scope_kind: 'builtin', scope_id: '', group: 'filter', event: 'comment', task_trigger: null,
    words: 'the bot\'s own write caused it', then: 'ignore', enabled: true, runs_7d: 0, no_op_rate_7d: null},
  {id: 'builtin:comment', built_in: true, scope_kind: 'builtin', scope_id: '', group: 'wake', event: 'comment',
    words: 'a comment on its task', then: 'wake', enabled: true, runs_7d: 30, no_op_rate_7d: 0.6},
];
function fixture(role) {
  const change = role === 'owner';
  return [
    ...builtins.map(r => ({...r})),
    {id: 'r-team', scope_kind: 'team', scope_id: '', event: 'comment', when: {actor: 'bot'}, then: 'note', params: {}, note: '',
      enabled: true, revision: 3, can_change: change, runs_7d: 8, no_op_rate_7d: 0.5},
    {id: 'r-type', scope_kind: 'type', scope_id: 'dev', event: 'step_entered', when: {step: 'QA Fixes', role: 'owner'}, then: 'wake', params: {},
      note: 'QA Fixes entered: wake the Builder who owns the ticket', enabled: true, revision: 1, can_change: change, runs_7d: 0, no_op_rate_7d: null},
    {id: 'r-bot', scope_kind: 'bot', scope_id: 'ops', event: 'comment', when: {}, then: 'batch', params: {}, note: '',
      enabled: true, revision: 1, can_change: change, runs_7d: 0, no_op_rate_7d: null},
  ];
}
// What a bot's own listing adds (backend/rules.py profile_listing): its profile's rows, built-in and read-only.
const profileRows = [{id: 'profile_lead_blocker', scope_kind: 'bot', scope_id: 'ops', event: 'blocker', when: {}, then: 'wake', params: {},
  enabled: true, built_in: true, group: 'profile', profile: 'lead', words: 'a blocker needs it', can_change: false, runs_7d: 1, no_op_rate_7d: 0}];
const TYPES = [{id: 'general', name: 'General', steps: [{id: 'g1', name: 'Open', status: 'open'}]}, {id: 'dev', name: 'Dev tickets', steps: ['In QA', 'QA Fixes', 'Pending Release'].map((name, i) => ({id: 'dev-' + i, name, status: 'doing'}))}];
const suggestionsFor = () => [
  {id: 's1', rule_id: 'r-team', kind: 'note', scope_kind: 'team', scope_id: '', event: 'comment', status: 'open', runs_7d: 42, no_op_7d: 31,
    no_op_rate_7d: 0.738, suggestion: 'Make this rule a note', can_change: true},
  {id: 's2', rule_id: 'builtin:comment', kind: 'override', scope_kind: 'team', scope_id: '', event: 'comment', status: 'open', runs_7d: 30,
    no_op_7d: 18, no_op_rate_7d: 0.6, suggestion: 'Add a team rule that makes this built-in rule a note', can_change: true}];
const taskRow = {id: 'r-task', scope_kind: 'task', scope_id: 't-acme', event: 'comment', when: {}, then: 'ignore', params: {}, note: '',
  enabled: true, revision: 1, can_change: true, runs_7d: 2, no_op_rate_7d: 1};

async function open(browser, role, hash = '#/settings/tasks', viewport = {width: 1440, height: 900}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors = [], writes = [], lists = [];
  const rules = fixture(role), suggestions = suggestionsFor();
  rules.push(
    {id: 'r-qa', scope_kind: 'type', scope_id: 'dev', event: 'step_entered', when: {step: 'In QA', role: 'qa'}, then: 'wake', params: {}, enabled: true, revision: 1, can_change: role === 'owner', runs_7d: 12, no_op_7d: 9},
    {id: 'r-release', scope_kind: 'type', scope_id: 'dev', event: 'step_entered', when: {step: 'Pending Release', role: 'release_manager'}, then: 'note', params: {}, enabled: true, revision: 1, can_change: role === 'owner'},
    {id: 'r-due', scope_kind: 'bot', scope_id: 'ops', event: 'due', when: {}, then: 'wake', params: {}, enabled: true, revision: 1, can_change: role === 'owner', created_by: 'profile', note: 'From the doer profile: due.'},
    ...['routine', 'schedule', 'next_run', 'background_done', '*'].map((event,i) => ({id: 'r-other-' + i, scope_kind: 'bot', scope_id: 'ops', event, when: {}, then: 'note', params: {}, enabled: true, can_change: role === 'owner'}))
  );
  page.on('pageerror' , e => errors.push(e.stack || e.message));
  await page.route('**/*', route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const config = {version: '0.1.0', app_name: 'Tico'};
    if (p === '/api/me') return json({id: role === 'owner' ? 'ana' : 'cara', role: role === 'owner' ? 'owner' : 'human', mover: role === 'owner',
      name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', role: 'owner'}]});
    if (p === '/api/v2/system-routines') return json({jobs: [], task_rule_departments: []});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', status: 'active', can_manage: role === 'owner'}]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/ceo-bot') return json({name: 'CEO', level: 1, regular_checks: true,
      levels: [{level: 1, name: 'Level 1', sentence: 'A short synthetic description.'}], level_history: []});
    if (p === '/api/v2/tasks/t-acme') return json({task: {id: 't-acme', title: 'Renew the acme.example domain'}});
    if (p === '/api/v2/release-schedule') return json({on: true, times: ['10:00'], bot: '', writer: '', zone: 'UTC', release_manager: 'ops',
      technical_writer: '', next: null, last: null, pending: 2, can_change: role === 'owner', can_release: role === 'owner'});
    if (p === '/api/v2/release/now') { writes.push({path: p, body: req.postDataJSON()}); return json({sent: true, reason: '', task: 't-rel', tickets: 2}); }
    if (p === '/api/v2/task-types') return json({types: TYPES});
    if (p === '/api/v2/event-types') return json({items: CATALOG});
    if (p === '/api/v2/triggers') return json({triggers: [{id: 'shop', name: 'Shop', event: 'webhook.shop', routines: [], builtin: false}]});
    if (p === '/api/v2/webhooks') return json({items: [{id: 'wh1', name: 'Zapier', host: 'hooks.zapier.com', events: ['*'], enabled: true}], more: false});
    // The server lists only the suggestions the viewer may act on.
    if (p === '/api/v2/rules/suggestions') return json({suggestions: role === 'owner' ? suggestions.filter(x => x.status !== 'dismissed') : [], more: false});
    const act = p.match(/^\/api\/v2\/rules\/suggestions\/([^/]+)\/(apply|dismiss)$/);
    if (act) {
      writes.push({path: p, body: req.postDataJSON()});
      const sug = suggestions.find(x => x.id === act[1]), r = rules.find(x => x.id === sug.rule_id);
      if (act[2] === 'apply') { sug.status = 'applied'; if (r && !r.built_in) Object.assign(r, {then: 'note', revision: r.revision + 1}); }
      else { if (sug.status === 'applied' && r && !r.built_in) Object.assign(r, {then: 'wake', revision: r.revision + 1}); sug.status = 'dismissed'; }
      return json({suggestion: sug});
    }
    if (p === '/api/v2/rules' && req.method() === 'GET') {
      lists.push(url.search);
      const kind = url.searchParams.get('scope_kind');
      const rows = kind === 'task' ? [{...taskRow}] : kind === 'builtin' ? builtins : kind === 'bot' ? [...profileRows, rules.find(r => r.id === 'r-bot')] : rules;
      const offset = Number(url.searchParams.get('offset') || 0), paged = !kind, pageRows = paged ? rows.slice(offset, offset + 4) : rows;
      return json({task_builtins: TASK_BUILTINS, rules: pageRows, more: paged && offset + 4 < rows.length, next_offset: offset + 4, events: ['person_message', 'comment', 'step_entered'], outcomes: ['wake', 'note', 'batch', 'ignore'], scopes: [],
        sourceless_events: CATALOG.filter(e => e.sourceless).map(e => e.name)});
    }
    if (p === '/api/v2/rules') {
      const body = req.postDataJSON();
      writes.push({path: p, body});
      if (body.event === 'person_message' && body.then !== 'wake') return json({error: {detail: FLOOR_422, code: 'invalid'}}, 422);
      return json({rule: {...body, id: 'r-new', revision: 1, enabled: body.on}});
    }
    const one = p.match(/^\/api\/v2\/rules\/([^/]+)$/);
    if (one) {
      const body = req.postDataJSON(), r = rules.find(x => x.id === one[1]);
      writes.push({path: p, body});
      if (body.expected_revision !== r.revision) return json({error: {detail: 'Someone else changed this rule', code: 'conflict'}}, 409);
      const {expected_revision, on, ...rest} = body;
      Object.assign(r, rest, on == null ? {} : {enabled: on}, {revision: r.revision + 1});
      const {runs_7d, no_op_rate_7d, can_change, ...answer} = r;   // a write answers the row without its figures
      return json({rule: answer});
    }
    if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/' + hash);
  await page.locator('#set-task-settings select').waitFor();
  return {page, errors, writes, lists};
}

const card = page => page.locator('#set-task-settings');
const settingRow = (page, id) => card(page).locator(`[data-task-setting="${id}"]`);
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const {page, errors, writes, lists} = await open(browser, 'owner');
    assert.equal(await card(page).locator('select').inputValue(), '');
    assert.equal(await card(page).locator('[data-task-trigger="step"]').count(), 0);
    assert.ok(lists.some(q => q.includes('offset=4')), 'later pages must be read');
    assert.match(await card(page).innerText(), /Ops has 5 more wake rules/);
    assert.match(await settingRow(page, 'r-due').innerText(), /Bot: Ops/);
    assert.match(await settingRow(page, 'task-setting-dedupe').innerText(), /Always on/);
    assert.equal(await settingRow(page, 'task-setting-dedupe').locator('a').count(), 0);
    assert.match(await settingRow(page, 'r-team').innerText(), /When a comment on its task/);
    assert.equal(await settingRow(page, 'task-setting-questions').count(), 1, 'a finish row that runs on all three shows once');
    assert.equal(await card(page).getByRole('switch').count(), 0, 'the switch opens the editor, so it is a link');
    await card(page).locator('select').selectOption('dev');
    assert.deepEqual(await card(page).locator('[data-task-step] h4').allTextContents(), TYPES[1].steps.map(s => s.name));
    assert.match(await settingRow(page, 'r-type').innerText(), /wake its owner/);
    assert.match(await settingRow(page, 'r-qa').innerText(), /wake QA/);
    assert.match(await settingRow(page, 'r-qa').innerText(), /12 runs, 9 idle/);
    assert.match(await settingRow(page, 'r-release').innerText(), /tell the Release Manager/);
    assert.match(await settingRow(page, 'r-release').innerText(), /Quiet/);
    assert.doesNotMatch(await card(page).innerText(), /step_entered|r-qa|task-setting-|outcome|\bTHEN\b/);
    if (process.env.TICO_TASK_SETTINGS_SCREENSHOT) await page.screenshot({path: process.env.TICO_TASK_SETTINGS_SCREENSHOT});
    await settingRow(page, 'r-qa').getByText('Edit', {exact: true}).click();
    const form = page.locator('[data-rule-form]');
    await form.waitFor();
    assert.equal(await form.locator('[name="event"]').inputValue(), 'step_entered');
    assert.equal(await form.locator('[name="step"]').inputValue(), 'In QA');
    await form.locator('[name="then"]').selectOption('note');
    await form.getByRole('button', {name: 'Save rule', exact: true}).click();
    await form.waitFor({state: 'detached'});
    await page.getByRole('tab', {name: 'Tasks', exact: true}).click();
    await page.waitForFunction(() => document.querySelector('[data-task-setting="r-qa"]')?.textContent.includes('Quiet'));
    assert.equal(writes.length, 1);
    const add = card(page).locator('[data-task-step="In QA"]').getByText('Add action', {exact: true});
    await add.click();
    const newForm = page.locator('[data-rule-form]'); await newForm.waitFor();
    assert.equal(await newForm.locator('[name="scope_kind"]').inputValue(), 'type');
    assert.equal(await newForm.locator('[name="scope_id"]').inputValue(), 'dev');
    assert.equal(await newForm.locator('[name="event"]').inputValue(), 'step_entered');
    assert.equal(await newForm.locator('[name="step"]').inputValue(), 'In QA');
    await newForm.getByText('Cancel', {exact: true}).click();
    await page.locator('[data-settings-tab="tasks"]').click();
    await card(page).locator('[data-task-trigger="created"]').getByText('Add action', {exact: true}).click();
    await page.locator('[data-rule-form]').waitFor();
    assert.equal(await page.locator('[data-rule-form] [name="event"]').inputValue(), 'task.created');
    assert.equal(await page.locator('[data-rule-form] [type="submit"]').isDisabled(), true);
    await page.locator('[data-rule-form]').getByText('Cancel', {exact: true}).click();
    assert.deepEqual(errors, []);
    await page.close();
    const member = await open(browser, 'member', '#/settings/tasks', {width: 390, height: 844});
    const c = card(member.page);
    assert.match(await c.innerText(), /Ask Ana to change these/);
    assert.equal(await c.getByText('Edit', {exact: true}).count(), 0);
    assert.equal(await c.getByText('Add action', {exact: true}).count(), 0);
    assert.equal(await c.getByRole('switch').count(), 0);
    assert.equal(await c.locator('[data-task-setting="r-qa"]').count(), 0, 'step section is hidden on All tasks');
    await c.locator('select').selectOption('dev');
    assert.equal(await c.locator('[data-task-setting="r-qa"]').count(), 1);
    await c.locator('select').selectOption('general');
    assert.match(await c.locator('[data-task-step="Open"]').innerText(), /Nothing happens/);
    assert.equal(await c.locator('[data-task-setting="task-setting-roadmap"]').count(), 0);
    assert.ok(await member.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.deepEqual(member.errors, []);
    await member.page.close();
    console.log('settings-tasks: lifecycle projection, pagination, permissions, editor presets and save refresh passed');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
