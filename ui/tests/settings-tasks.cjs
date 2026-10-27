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
// The listing's Task Trigger vocabulary and the shipped close after 24 hours, as the server describes them.
const TASK_VOCAB = JSON.parse(execFileSync(process.env.TICO_PYTHON || 'python3', ['-c',
  "import json; from backend import task_trigger_rules as T; print(json.dumps({'task_triggers': T.TRIGGERS, 'task_when_keys': T.CONDITIONS, 'task_actions': T.ACTIONS}))"],
  {cwd: path.join(__dirname, '../..')}).toString());
const CLOSE_JOB = JSON.parse(execFileSync(process.env.TICO_PYTHON || 'python3', ['-c',
  "import json; from backend.task_settings import describe_job; print(json.dumps(describe_job({'key': 'task-rule-close-after-done', 'kind': 'task_rule', 'name': 'Close 24h after done', 'switch': True, 'on': True, 'schedule': 'Every 15 minutes', 'department': {'id': 'engineering', 'name': 'Engineering'}, 'triggers': [{'key': 'done_untouched', 'hours': 24}]})))"],
  {cwd: path.join(__dirname, '../..')}).toString());
const FAILED_AT = '2026-10-27T09:15:00Z';
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
    ...['routine', 'schedule', 'next_run', 'background_done', '*'].map((event,i) => ({id: 'r-other-' + i, scope_kind: 'bot', scope_id: 'ops', event, when: {}, then: 'note', params: {}, enabled: true, can_change: role === 'owner'})),
    // Task Trigger rows (backend/rules.py view + task_settings.describe_rule): one whose last Action failed, one paused.
    {id: 'r-failed', scope_kind: 'type', scope_id: 'dev', event: 'task.done', when: {}, then: 'action', params: {action: 'send_webhook', webhook: 'wh1', type: 'custom.done'},
      enabled: true, revision: 1, can_change: role === 'owner', task_trigger: 'finished', runs_7d: 0, paused: false, last_error: 'Action: webhook delivery failed (HTTP 500)', last_error_at: FAILED_AT},
    {id: 'r-paused', scope_kind: 'team', scope_id: '', event: 'task.created', when: {}, then: 'action', params: {action: 'wake_bot', bot: 'ops'},
      enabled: true, revision: 1, can_change: role === 'owner', task_trigger: 'created', runs_7d: 0, paused: true, pause_reason: 'Paused: too many runs', paused_at: FAILED_AT}
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
    if (p === '/api/v2/system-routines') return json({jobs: [{...CLOSE_JOB, can_change: role === 'owner'}], task_rule_departments: []});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', status: 'active', can_manage: role === 'owner'},
      {name: 'pr-reviewer', display_name: 'PR Reviewer', status: 'active', can_manage: role === 'owner'}]);
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
      return json({task_builtins: TASK_BUILTINS, rules: pageRows, more: paged && offset + 4 < rows.length, next_offset: offset + 4, events: ['person_message', 'comment', 'step_entered', ...TASK_VOCAB.task_triggers], outcomes: ['wake', 'note', 'batch', 'ignore'], scopes: [],
        sourceless_events: CATALOG.filter(e => e.sourceless).map(e => e.name), ...TASK_VOCAB});
    }
    if (p === '/api/v2/rules') {
      const body = req.postDataJSON();
      writes.push({path: p, body});
      if (body.event === 'person_message' && body.then !== 'wake') return json({error: {detail: FLOOR_422, code: 'invalid'}}, 422);
      const made = {...body, id: 'r-new', revision: 1, enabled: body.on};
      // The listing reads it back with its section (describe_rule) and the viewer's rights.
      if (body.event.startsWith('task.')) rules.push({...made, can_change: true, task_trigger: {'task.created': 'created', 'task.step_changed': 'step'}[body.event] || 'finished', paused: false});
      return json({rule: made});
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
    assert.equal(await newForm.locator('[name="event"]').inputValue(), 'task.step_changed');
    assert.equal(await newForm.locator('[name="step"]').inputValue(), 'In QA');
    await newForm.getByText('Cancel', {exact: true}).click();
    await page.locator('[data-settings-tab="tasks"]').click();

    // 6. On a type, each opening line names it as "a task of type Dev tickets", never "a Dev tickets task".
    const intro = key => card(page).locator(`[data-task-trigger="${key}"] > p`).first().innerText();
    assert.equal(await intro('created'), 'Tico does these the moment a task of type Dev tickets is filed.');
    assert.equal(await intro('step'), 'Tico does these when a task of type Dev tickets moves into a step.');
    assert.equal(await intro('finished'), 'Tico does these when a task of type Dev tickets is done, declined or closed.');
    assert.equal(await intro('open'), 'Tico checks open tasks of type Dev tickets on a schedule. Changes and messages also act as they happen.');
    assert.equal(await intro('after'), 'Tico tidies finished tasks of type Dev tickets on a schedule.');
    assert.doesNotMatch(await card(page).innerText(), /Dev tickets task|Dev ticketss/);

    // 3 and 4. A failed last Action shows its detail and time; a paused row says so, on the card.
    const failedAt = await page.evaluate(iso => fmt(iso), FAILED_AT);
    const failed = settingRow(page, 'r-failed');
    assert.equal(await card(page).locator('[data-task-finish="Done"] [data-task-setting="r-failed"]').count(), 1, 'a done Trigger shows under Done');
    assert.match(await failed.innerText(), /When a task of type Dev tickets is done, send custom\.done to Zapier\./);
    assert.equal(await failed.locator('[data-rule-last-error]').innerText(), `Action: webhook delivery failed (HTTP 500) · ${failedAt}`);
    const paused = settingRow(page, 'r-paused');
    assert.equal(await card(page).locator('[data-task-trigger="created"] [data-task-setting="r-paused"]').count(), 1);
    assert.match(await paused.innerText(), /Paused: too many runs/);
    assert.match(await paused.innerText(), /Switch on/);
    assert.doesNotMatch(await paused.innerText(), /\bOn\b/);

    // 8. The shipped close after 24 hours reads and links as it did before Task Triggers.
    const closeRow = settingRow(page, 'task-rule-close-after-done');
    assert.equal(await card(page).locator('[data-task-trigger="after"] [data-task-setting="task-rule-close-after-done"]').count(), 1);
    assert.equal((await closeRow.innerText()).replace(/\s+/g, ' ').trim(), 'Action Close a done task after 24 hours untouched; skips tasks waiting on a person, snoozed, pinned, or with an open question or approval; at most 50 a pass. Trigger: Every 15 minutes Department: Engineering Quiet Run count unavailable On Switch off Edit');
    assert.deepEqual(await closeRow.locator('a').evaluateAll(a => a.map(x => x.getAttribute('href'))), ['#/settings/rules?job=task-rule-close-after-done', '#/settings/rules?job=task-rule-close-after-done']);
    assert.equal(await card(page).locator('[data-task-trigger="after"]').getByText('Add action', {exact: true}).count(), 0, 'After it is finished runs on a schedule: no Trigger to add');
    assert.equal(await settingRow(page, 'task-setting-dedupe').locator('[data-rule-last-error]').count(), 0);

    // 1 and 7. Add action under "created" opens the Trigger and Action form on that Trigger and this type.
    await card(page).locator('[data-task-trigger="created"]').getByText('Add action', {exact: true}).last().click();
    const tf = page.locator('[data-task-trigger-form]'); await tf.waitFor();
    assert.equal(await tf.locator('[name="event"]').inputValue(), 'task.created');
    assert.equal(await tf.locator('[name="event"] option:checked').innerText(), 'A task is created');
    assert.equal(await tf.locator('[name="scope_kind"]').inputValue(), 'type');
    assert.equal(await tf.locator('[name="scope_id"] option:checked').innerText(), 'Dev tickets');
    assert.deepEqual(await tf.locator('[name="event"] option').allInnerTexts(),
      ['A task is created', 'A task enters a step', 'A task is done', 'A task is declined', 'A task is closed']);
    assert.deepEqual(await tf.locator('[name="action"] option').allInnerTexts(),
      ['Add a note', 'Set a wait', 'Link a task', 'Move the step', 'Close the task', 'Wake a bot', 'Create a task', 'Send a webhook']);
    assert.deepEqual(await tf.locator('[name="scope_kind"] option').allInnerTexts(), ['Team', 'Task type', 'Bot that owns the task']);
    assert.deepEqual(await tf.locator('[data-rule-when-task] label').evaluateAll(l => l.map(x => x.firstChild.textContent.trim())), ['Type', 'Step', 'Tag', 'Owner', 'Requester']);
    assert.doesNotMatch(await tf.innerText(), /\b(WHEN|THEN|rule)\b/i);

    // 2. "Enters a step" does not save until its step is chosen.
    await tf.locator('[name="event"]').selectOption('task.step_changed');
    await tf.locator('[name="action"]').selectOption('note');
    await tf.locator('[name="param_note"]:not([disabled])').fill('Entered');
    const before = writes.length;
    await tf.getByRole('button', {name: 'Save Action', exact: true}).click();
    assert.equal(await tf.locator('[name="step"]').evaluate(el => el.validity.valueMissing), true);
    await page.waitForTimeout(200);
    assert.equal(writes.length, before, 'no step, no save');
    assert.equal(await tf.isVisible(), true);
    assert.deepEqual(await tf.locator('[name="step"] option').allInnerTexts(), ['Choose', 'In QA', 'QA Fixes', 'Pending Release']);
    // A step the newly chosen type lacks is cleared, not kept, so the Trigger cannot be saved on it.
    await tf.locator('[name="step"]').selectOption('QA Fixes');
    await tf.locator('[name="scope_id"]').selectOption('general');
    assert.equal(await tf.locator('[name="step"]').inputValue(), '');
    assert.deepEqual(await tf.locator('[name="step"] option').allInnerTexts(), ['Choose', 'Open']);
    await tf.locator('[name="scope_id"]').selectOption('dev');
    // Back to the section's Trigger: create a task for PR Reviewer, saved once under Dev tickets.
    await tf.locator('[name="event"]').selectOption('task.created');
    await tf.locator('[name="action"]').selectOption('create_task');
    await tf.locator('[name="param_owner"]').selectOption('pr-reviewer');
    await tf.locator('[name="param_title"]').fill('Review {{task.title}}');
    await tf.getByRole('button', {name: 'Save Action', exact: true}).click();
    await tf.waitFor({state: 'detached'});
    const saved = writes.slice(before);
    assert.equal(saved.length, 1);
    assert.deepEqual(saved[0], {path: '/api/v2/rules', body: {event: 'task.created', then: 'action', when: {}, note: '', on: true, scope_kind: 'type', scope_id: 'dev',
      params: {action: 'create_task', owner: 'pr-reviewer', title: 'Review {{task.title}}'}}});
    await page.locator('[data-settings-tab="tasks"]').click();
    await page.waitForFunction(() => document.querySelector('[data-task-trigger="created"] [data-task-setting="r-new"]'));
    assert.match(await settingRow(page, 'r-new').innerText(), /When a task of type Dev tickets is created, create a task for PR Reviewer\./);
    assert.match(await settingRow(page, 'r-new').innerText(), /This type/);
    assert.match(await settingRow(page, 'r-new').innerText(), /Wakes/, 'a new task wakes the bot that gets it');

    // 3 and 4 in the Rules list: Trigger and Action words, the failed Action's detail and time, the paused state.
    await page.evaluate(() => { location.hash = '#/settings/rules'; });
    const listRow = id => page.locator(`#set-rules [data-rule="${id}"]`);
    await listRow('r-failed').waitFor();
    assert.equal((await listRow('r-failed').locator('[data-rule-text]').innerText()).replace(/\s+/g, ' ').split(' Action:')[0], 'Trigger a task is done Action send custom.done to Zapier');
    assert.equal(await listRow('r-failed').locator('[data-rule-last-error]').innerText(), `Action: webhook delivery failed (HTTP 500) · ${failedAt}`);
    assert.match(await listRow('r-paused').innerText(), /Paused: too many runs/);
    assert.equal(await listRow('r-paused').locator('[data-rule-on]').isChecked(), false, 'a paused row runs again only when switched on');
    // Editing a Task Trigger opens its own form, which says it is paused; the general Add rule offers the Triggers too.
    await listRow('r-paused').getByRole('button', {name: 'Edit', exact: true}).click();
    await tf.waitFor();
    assert.equal(await tf.locator('[name="event"]').inputValue(), 'task.created');
    assert.equal(await tf.locator('[name="action"]').inputValue(), 'wake_bot');
    assert.match(await tf.locator('[data-rule-paused]').innerText(), /Paused: too many runs/);
    await tf.getByText('Cancel', {exact: true}).click();
    await page.locator('#set-rules [data-rule-new]').click();
    const general = page.locator('[data-rule-form]:not([data-task-trigger-form])'); await general.waitFor();
    assert.deepEqual(await general.locator('[name="event"] optgroup[label="Task Trigger"] option').evaluateAll(o => o.map(x => x.value)), TASK_VOCAB.task_triggers);
    await general.locator('[name="event"]').selectOption('task.closed');
    await tf.waitFor();
    assert.equal(await general.count(), 0);
    assert.equal(await tf.locator('[name="event"]').inputValue(), 'task.closed');
    await tf.getByText('Cancel', {exact: true}).click();
    assert.deepEqual(errors, []);
    await page.close();
    const member = await open(browser, 'member', '#/settings/tasks', {width: 390, height: 844});
    const c = card(member.page);
    assert.match(await c.innerText(), /Ask Ana to change these/);
    assert.equal(await c.getByText('Edit', {exact: true}).count(), 0);
    assert.equal(await c.getByText('Add action', {exact: true}).count(), 0);
    assert.equal(await c.getByRole('switch').count(), 0);
    // 5. Without rights to change Rules, a Task Trigger row shows its state and error but no control.
    assert.match(await c.locator('[data-task-setting="r-paused"]').innerText(), /Paused: too many runs/);
    assert.equal(await c.locator('[data-task-setting="r-paused"] a').count(), 0);
    // 6. On All tasks the opening lines read as before.
    assert.equal(await c.locator('[data-task-trigger="created"] > p').first().innerText(), 'Tico does these the moment a task is filed.');
    assert.equal(await c.locator('[data-task-trigger="open"] > p').first().innerText(), 'Tico checks open tasks on a schedule. Changes and messages also act as they happen.');
    assert.equal(await c.locator('[data-task-trigger="after"] > p').first().innerText(), 'Tico tidies finished tasks on a schedule.');
    assert.equal(await c.locator('[data-task-setting="r-qa"]').count(), 0, 'step section is hidden on All tasks');
    await c.locator('select').selectOption('dev');
    assert.equal(await c.locator('[data-task-setting="r-qa"]').count(), 1);
    assert.equal(await c.getByText('Add action', {exact: true}).count(), 0);
    assert.equal(await c.locator('[data-task-setting="r-failed"] a').count(), 0);
    assert.match(await c.locator('[data-task-setting="r-failed"] [data-rule-last-error]').innerText(), /webhook delivery failed/);
    await c.locator('select').selectOption('general');
    assert.match(await c.locator('[data-task-step="Open"]').innerText(), /Nothing happens/);
    assert.equal(await c.locator('[data-task-setting="task-setting-roadmap"]').count(), 0);
    assert.ok(await member.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    // 5 in the Rules list: the row's state shows, with no switch or Edit.
    await member.page.evaluate(() => { location.hash = '#/settings/rules'; });
    const memberRow = member.page.locator('#set-rules [data-rule="r-paused"]'); await memberRow.waitFor();
    assert.match(await memberRow.innerText(), /Trigger a task is created Action wake Ops/);
    assert.equal(await memberRow.locator('.sysr-state').innerText(), 'Paused: too many runs');
    assert.equal(await memberRow.locator('[data-rule-edit], [data-rule-on]').count(), 0);
    assert.equal(await member.page.locator('#set-rules [data-rule-new]').count(), 0);
    assert.deepEqual(member.errors, []);
    await member.page.close();
    console.log('settings-tasks: lifecycle projection, pagination, permissions, editor presets and save refresh passed');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
