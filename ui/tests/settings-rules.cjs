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

const FLOOR_422 = 'Person message always wakes the bot: a rule cannot make it wait';
const builtins = [
  {id: 'builtin:person_message', built_in: true, scope_kind: 'builtin', scope_id: '', group: 'always', event: 'person_message',
    words: 'a person messages it', then: 'wake', enabled: true, runs_7d: 14, no_op_rate_7d: 0.1},
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
const TYPES = [{id: 'dev', name: 'Dev tickets', steps: ['In QA', 'QA Fixes', 'Pending Release'].map((name, i) => ({id: 'dev-' + i, name, status: 'doing'}))}];
const suggestionsFor = () => [
  {id: 's1', rule_id: 'r-team', kind: 'note', scope_kind: 'team', scope_id: '', event: 'comment', status: 'open', runs_7d: 42, no_op_7d: 31,
    no_op_rate_7d: 0.738, suggestion: 'Make this rule a note', can_change: true},
  {id: 's2', rule_id: 'builtin:comment', kind: 'override', scope_kind: 'team', scope_id: '', event: 'comment', status: 'open', runs_7d: 30,
    no_op_7d: 18, no_op_rate_7d: 0.6, suggestion: 'Add a team rule that makes this built-in rule a note', can_change: true}];
const taskRow = {id: 'r-task', scope_kind: 'task', scope_id: 't-acme', event: 'comment', when: {}, then: 'ignore', params: {}, note: '',
  enabled: true, revision: 1, can_change: true, runs_7d: 2, no_op_rate_7d: 1};

async function open(browser, role, hash = '#/settings/rules', viewport = {width: 1440, height: 900}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors = [], writes = [], lists = [];
  const rules = fixture(role), suggestions = suggestionsFor();
  page.on('pageerror', e => errors.push(e.stack || e.message));
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
      return json({rules: rows, events: ['person_message', 'comment', 'step_entered'], outcomes: ['wake', 'note', 'batch', 'ignore'], scopes: [], more: false,
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
  await page.locator('#set-rules [data-rules-list]').waitFor();
  return {page, errors, writes, lists};
}
const row = (page, id) => page.locator(`[data-rule="${id}"]`);

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    {
      // A member reads the rows: no Add, no switch, no Edit; the built-ins never have controls.
      const {page, errors} = await open(browser, 'member');
      await row(page, 'r-team').waitFor();
      assert.equal(await page.locator('[data-rule-new]').count(), 0);
      assert.equal(await page.locator('#set-rules [data-rule-on], #set-rules [data-rule-edit]').count(), 0);
      await page.locator('#set-release [data-release-pending]').waitFor();
      assert.equal(await page.locator('[data-release-now], [data-release-note]').count(), 0, 'no Release now without can_release');
      assert.equal(await page.locator('[data-rules-suggestions]').isHidden(), true, 'no suggestions: no block');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      const {page, errors, writes} = await open(browser, 'owner');
      await row(page, 'r-team').waitFor();
      assert.equal(await page.locator('[data-rule-new]').count(), 1);
      assert.equal(await row(page, 'builtin:comment').locator('[data-rule-on], [data-rule-edit]').count(), 0, 'a built-in has no controls');
      // Edit round-trip: the row's revision goes with the write and the row reads back the new outcome.
      await row(page, 'r-team').locator('[data-rule-edit]').click();
      const form = page.locator('[data-rule-form]');
      await form.locator('select[name=then]').selectOption('batch');
      await form.locator('button[type=submit]').click();
      await form.waitFor({state: 'detached'});
      assert.match(await row(page, 'r-team').locator('[data-rule-text]').innerText(), /THEN batch it$/);
      assert.equal(writes[0].path, '/api/v2/rules/r-team');
      assert.equal(writes[0].body.expected_revision, 3);
      assert.equal(writes[0].body.then, 'batch');
      // The switch sends the revision the edit answered, so a second write is not refused as stale.
      await row(page, 'r-team').locator('[data-rule-on]').uncheck({force: true});
      await page.waitForFunction(() => document.querySelector('[data-rule="r-team"] [data-rule-status]')?.textContent === 'Saved');
      assert.deepEqual(writes[1].body, {expected_revision: 4, on: false});
      assert.equal(await row(page, 'r-team').locator('[data-rule-on]').isChecked(), false);
      assert.equal(await row(page, 'r-team').locator('[data-rule-usage]').innerText(), '8 runs · 50% no-op', 'the figures survive a write');
      // A rule that makes an always-wake event wait: the server's words in the form, which stays open.
      await page.locator('[data-rule-new]').click();
      await form.locator('select[name=event]').selectOption('person_message');
      await form.locator('select[name=then]').selectOption('ignore');
      await form.locator('button[type=submit]').click();
      await page.waitForFunction(text => document.querySelector('[data-rule-error]')?.textContent === text, FLOOR_422);
      assert.equal(await form.isVisible(), true);
      assert.equal(writes[2].body.scope_kind, 'team');
      await form.locator('[data-close]').click();
      // Release now asks once and says what happened.
      await page.locator('#set-release [data-release-now]').click();
      await page.waitForFunction(() => /2 tickets/.test(document.querySelector('#set-release [data-release-status]')?.textContent || ''));
      assert.deepEqual(writes.at(-1), {path: '/api/v2/release/now', body: {}});
      await page.locator('#set-release [data-release-note]').fill('PRs merged to main');
      await page.locator('#set-release [data-release-now]').click();
      await page.waitForFunction(() => document.querySelector('#set-release [data-release-note]')?.value === '');
      assert.deepEqual(writes.at(-1), {path: '/api/v2/release/now', body: {note: 'PRs merged to main'}});
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Suggestions: Apply posts and the rule below is read again; on the applied row Dismiss is Undo, and puts it back.
      const {page, errors, writes} = await open(browser, 'owner');
      const sug = id => page.locator(`[data-rule-suggestion="${id}"]`);
      await sug('s1').waitFor();
      assert.equal(await sug('s2').count(), 1);
      await sug('s1').locator('[data-suggest-apply]').click();
      await page.waitForFunction(() => document.querySelector('[data-rule-suggestion="s1"]')?.dataset.suggestStatus === 'applied');
      assert.deepEqual(writes[0], {path: '/api/v2/rules/suggestions/s1/apply', body: {}});
      await page.waitForFunction(() => /THEN leave a note/.test(document.querySelector('[data-rule="r-team"] [data-rule-text]')?.textContent || ''));
      assert.equal(await sug('s1').locator('[data-suggest-apply]').count(), 0);
      assert.equal(await sug('s1').locator('[data-suggest-dismiss]').innerText(), 'Undo');
      await sug('s1').locator('[data-suggest-dismiss]').click();
      await sug('s1').waitFor({state: 'detached'});
      assert.deepEqual(writes[1], {path: '/api/v2/rules/suggestions/s1/dismiss', body: {}});
      await page.waitForFunction(() => /THEN wake the bot/.test(document.querySelector('[data-rule="r-team"] [data-rule-text]')?.textContent || ''));
      await sug('s2').locator('[data-suggest-dismiss]').click();
      await sug('s2').waitFor({state: 'detached'});
      assert.equal(await page.locator('[data-rules-suggestions]').isHidden(), true, 'the last one gone: the block hides');
      // A seeded Dev tickets row: Edit and Delete; its step is one of the type's steps; QA and the Release Manager are roles.
      await row(page, 'r-type').locator('[data-rule-edit]').click();
      const form = page.locator('[data-rule-form]');
      await form.waitFor();   // the form opens once the event catalog and endpoints are read
      assert.equal(await form.locator('[data-rule-delete]').count(), 1);
      assert.equal(await form.locator('select[name=step]').inputValue(), 'QA Fixes');
      assert.deepEqual(await form.locator('select[name=step] option').evaluateAll(els => els.map(el => el.value)), ['', 'In QA', 'QA Fixes', 'Pending Release']);
      const roles = await form.locator('select[name=role] option').evaluateAll(els => els.map(el => el.value));
      assert.ok(roles.includes('qa') && roles.includes('release_manager'), roles.join());
      assert.equal(await form.locator('select[name=event]').inputValue(), 'step_entered');
      // An action: only its own fields show, and a hidden required one does not stop Save.
      await form.locator('select[name=then]').selectOption('action');
      await form.locator('select[name=action]').selectOption('move_step');
      assert.equal(await form.locator('[name=param_waiting_on]').isVisible(), false);
      await form.locator('select[name=param_step]').selectOption('Pending Release');
      await form.locator('[name=param_note]').fill('Moved by rule');
      await form.locator('button[type=submit]').click();
      await form.waitFor({state: 'detached'});
      const saved = writes.at(-1);
      assert.equal(saved.path, '/api/v2/rules/r-type');
      assert.deepEqual(saved.body.params, {action: 'move_step', step: 'Pending Release', note: 'Moved by rule'});
      assert.deepEqual(saved.body.when, {role: 'owner', step: 'QA Fixes'});
      assert.equal(saved.body.then, 'action');
      assert.match(await row(page, 'r-type').locator('[data-rule-text]').innerText(), /THEN move it to Pending Release\n/);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // webhook.received: its own WHEN, team/department/bot scopes, the event actions only, and data that must be JSON.
      const {page, errors, writes} = await open(browser, 'owner');
      await row(page, 'r-team').waitFor();
      await page.locator('[data-rule-new]').click();
      const form = page.locator('[data-rule-form]');
      await form.locator('select[name=event]').selectOption('webhook.received');
      assert.equal(await form.locator('[name=actor]').isVisible(), false, 'no task conditions');
      assert.deepEqual(await form.locator('select[name=when_trigger] option').evaluateAll(els => els.map(e => e.value)), ['', 'shop']);
      await form.locator('select[name=when_trigger]').selectOption('shop');
      await form.locator('[name=when_type]').fill('order.*');
      const off = sel => form.locator(sel).evaluateAll(els => els.filter(e => e.disabled).map(e => e.value));
      assert.deepEqual(await off('select[name=scope_kind] option'), ['type', 'task']);
      assert.deepEqual(await off('select[name=then] option'), ['batch']);
      await form.locator('select[name=then]').selectOption('action');
      assert.deepEqual(await off('select[name=action] option'), ['close', 'move_step', 'set_wait', 'relate', 'note']);
      assert.equal(await form.locator('select[name=action]').inputValue(), 'wake_bot');
      await form.locator('select[name=action]').selectOption('send_webhook');
      assert.equal(await form.locator('[name=param_note]').isVisible(), false);
      await form.locator('select[name=param_webhook]').selectOption('wh1');
      await form.locator('[name=param_type]').fill('custom.order');
      await form.locator('[name=param_data]').fill('{"id": {{payload.id}}}');
      await form.locator('button[type=submit]').click();
      await page.waitForFunction(() => document.querySelector('[data-rule-error]')?.textContent === 'Data is not valid JSON');
      assert.equal(writes.length, 0, 'nothing sent while the data does not parse');
      await form.locator('[name=param_data]').fill('{"id": "{{payload.id}}", "via": "tico"}');
      await form.locator('button[type=submit]').click();
      await form.waitFor({state: 'detached'});
      assert.deepEqual(writes[0].body, {event: 'webhook.received', then: 'action', when: {trigger: 'shop', type: 'order.*'}, note: '', on: true,
        params: {action: 'send_webhook', webhook: 'wh1', type: 'custom.order', data: {id: '{{payload.id}}', via: 'tico'}}, scope_kind: 'team', scope_id: ''});
      // A wake at the team names the bot to tell, with its text.
      await page.locator('[data-rule-new]').click();
      await form.locator('select[name=event]').selectOption('webhook.received');
      await form.locator('select[name=then]').selectOption('wake');
      await form.locator('select[name=tell_bot]').selectOption('ops');
      await form.locator('[name=tell_text]').fill('A new order came in');
      await form.locator('button[type=submit]').click();
      await form.waitFor({state: 'detached'});
      assert.deepEqual(writes[1].body.params, {bot: 'ops', text: 'A new order came in'});
      assert.deepEqual(writes[1].body.when, {});
      // Make a task and forward run only on an event with no task; forward only on webhook.received.
      await page.locator('[data-rule-new]').click();
      await form.locator('select[name=event]').selectOption('comment');
      await form.locator('select[name=then]').selectOption('action');
      assert.deepEqual(await off('select[name=action] option'), ['create_task', 'forward']);
      await form.locator('select[name=event]').selectOption('webhook.received');
      await form.locator('select[name=action]').selectOption('create_task');
      await form.locator('select[name=param_owner]').selectOption('ops');
      await form.locator('[name=param_title]').fill('{{type}} from {{trigger.name}}');
      await form.locator('[name=param_body]').fill('Order {{payload.id}}');
      assert.equal(await form.locator('[name=param_webhook]').isVisible(), false);
      await form.locator('button[type=submit]').click();
      await form.waitFor({state: 'detached'});
      assert.deepEqual(writes[2].body.params, {action: 'create_task', owner: 'ops', title: '{{type}} from {{trigger.name}}', body: 'Order {{payload.id}}'});
      await page.locator('[data-rule-new]').click();
      await form.locator('select[name=event]').selectOption('webhook.received');
      await form.locator('select[name=then]').selectOption('action');
      await form.locator('select[name=action]').selectOption('forward');
      assert.equal(await form.locator('[name=param_type]').isVisible(), false);
      await form.locator('select[name=param_webhook]').selectOption('wh1');
      await form.locator('button[type=submit]').click();
      await form.waitFor({state: 'detached'});
      assert.deepEqual(writes[3].body.params, {action: 'forward', webhook: 'wh1'});
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // The task modal's link: that task's rows only, named, with Open task and All rules as the ways out.
      const {page, errors, lists} = await open(browser, 'member', '#/settings/rules?task=t-acme');
      await row(page, 'r-task').waitFor();
      assert.ok(lists.some(q => q.includes('scope_kind=task') && q.includes('scope_id=t-acme')), lists.join(' | '));
      assert.equal(await page.locator('#set-rules [data-rule]').count(), 1, 'the built-ins are read for their words only');
      await page.waitForFunction(() => /Renew the acme\.example domain/.test(document.querySelector('[data-rules-filter]')?.textContent || ''));
      assert.equal(await page.locator('[data-rules-filter] a[href="#/task/t-acme"]').count(), 1);
      await page.locator('[data-rules-all]').click();
      await row(page, 'r-team').waitFor();
      assert.equal(await page.locator('[data-rules-filter]').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A bot's rules (the profile picker's Wake rules link): its profile rows read-only under the profile, its own editable.
      const {page, errors, lists} = await open(browser, 'owner', '#/settings/rules?bot=ops');
      await row(page, 'profile_lead_blocker').waitFor();
      assert.ok(lists.some(q => q.includes('scope_kind=bot') && q.includes('scope_id=ops')), lists.join(' | '));
      assert.equal(await page.locator('[data-rule-profile="lead"]').count(), 1);
      assert.equal(await row(page, 'profile_lead_blocker').locator('[data-rule-on], [data-rule-edit]').count(), 0);
      assert.equal(await row(page, 'r-bot').locator('[data-rule-edit]').count(), 1);
      assert.equal(await page.locator('[data-rules-filter] a[href="#/bot/ops/settings"]').count(), 1);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Catalogue refusal links and tab links leave another page for the right Settings section at both widths.
      for (const viewport of [{width: 1440, height: 900}, {width: 390, height: 844}]) {
        const {page, errors} = await open(browser, 'owner', '#/settings/rules', viewport);
        await row(page, 'r-team').waitFor();
        await page.evaluate(() => { S.emps ||= []; });
        const links = [
          ['#/settings/ceo', 'ceo'],       // includes the CEO-level person-only refusal link
          ['#/settings/devices', 'devices'],
          ['#/settings/bots', 'bots'],
          ['#/settings/recurring', 'recurring'],
          ['#/settings/tasks', 'tasks'],
          ['#/settings/health', 'health'],
          ['#/settings/bots/settings-turn-budgets', 'bots'], // existing tab/card form
        ];
        for (const [href, tab] of links) {
          await page.evaluate(() => { location.hash = '#/updates'; });
          await page.waitForFunction(() => S.route === '#/updates');
          await page.evaluate(value => { location.hash = value; }, href);
          await page.waitForFunction(expected => location.hash === '#/settings' && SETTINGS_TAB === expected, tab);
          assert.equal(await page.locator(`[data-settings-tab="${tab}"]`).getAttribute('aria-selected'), 'true', href);
          assert.deepEqual(errors, [], href);
        }
        await page.evaluate(() => { location.hash = '#/updates'; });
        await page.waitForFunction(() => S.route === '#/updates');
        await page.evaluate(() => { location.hash = '#/settings/webhooks/outgoing'; });
        await page.waitForFunction(() => location.hash === '#/settings' && SETTINGS_TAB === 'webhooks' && WH.tab === 'outgoing');
        assert.deepEqual(errors, []);
        await page.close();
      }
    }
    {
      // Phone width: the rows wrap; nothing in the page scrolls sideways.
      const {page, errors} = await open(browser, 'owner', '#/settings/rules', {width: 390, height: 844});
      await row(page, 'r-team').waitFor();
      const wide = await page.evaluate(() => [...document.querySelectorAll('#set-rules-card, #set-rules-card .wr-row, #set-rules-card .settings-bots-filters')]
        .filter(el => el.getBoundingClientRect().right > innerWidth + 1).map(el => el.className || el.id));
      assert.deepEqual(wide, []);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'no sideways scroll');
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally { await browser.close(); }
  console.log('settings-rules ok');
})().catch(error => { console.error(error); process.exit(1); });
