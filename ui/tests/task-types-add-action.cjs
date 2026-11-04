// Offline browser regression: Add action in the Task settings card's Trigger section makes an off, viewer-owned
// automation with that Trigger and type, then its saved Action appears in that section. It also checks type copy,
// permissions, failure details and paused reasons on the card.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

let mover = true;   // who may add or edit task Triggers and Actions
const types = [{id: 'dev', name: 'Dev tickets', steps: [], roles: []}, {id: 'general', name: 'General', steps: [], roles: []}];
const taskRules = [
  {id: 'au_failed', name: 'Failed Action', enabled: true, trigger: {type: 'event', name: 'task.created', filters: [{field: 'event.data.type_id', value: 'dev'}]},
    last_run: {status: 'failed', started_at: '2026-11-03T10:00:00Z', finished_at: '2026-11-03T10:00:05Z', error: 'The note could not be saved'}},
  {id: 'au_paused', name: 'Paused Action', enabled: false, paused: true, paused_reason: 'Paused: too many runs',
    trigger: {type: 'event', name: 'task.created', filters: [{field: 'event.data.type_id', value: 'dev'}]}}
];

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  const errors = [], posts = [], patches = [];
  let made = null;
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => {
      const req = route.request(), p = new URL(req.url()).pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const config = {version: '0.1.0', app_name: 'Tico', local: true};
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, role: 'human', mover, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json([{id: 'pr-reviewer', name: 'PR Reviewer', status: 'active'}]);
      if (p === '/api/v2/task-types') return json({types});
      if (p === '/api/v2/automations' && req.method() === 'GET') return json({automations: taskRules, next: null});
      if (p === '/api/issues') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/tags') return json({tags: []});
      if (p === '/api/v2/roadmaps/settings') return json({labels: [], cors_origins: [], action_limit: 10, suggestion_limit: 20, bot_public_approval: true, suggestions_bot: null, suggestions_receiver: {bot: null, name: null, source: 'automatic'}});
      if (p === '/api/v2/automations' && req.method() === 'POST') {
        const body = req.postDataJSON(); posts.push(body);
        made = {id: posts.length === 1 ? 'au_new' : 'au_new_step', name: body.name, owner_id: body.owner, enabled: false, builtin: false, can_edit: true,
          version: 1, trigger: body.trigger, steps: []};
        taskRules.push(made);
        return json(made, 201);
      }
      if (p === `/api/v2/automations/${made?.id}` && req.method() === 'PATCH') {
        const body = req.postDataJSON(); patches.push(body); made = {...made, ...body, version: 2};
        const i = taskRules.findIndex(rule => rule.id === made.id); if (i >= 0) taskRules[i] = made;
        return json(made);
      }
      if (p === `/api/v2/automations/${made?.id}`) return json(made);
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/settings/tasks');
    const row = page.locator('.task-type-row', {hasText: 'Dev tickets'});
    await row.locator('[data-add-action="dev"]').waitFor();
    assert.equal(await row.locator('.task-type-intro').innerText(), 'Trigger: a task of type Dev tickets is created.');
    const card = page.locator('#set-task-automations');
    await card.locator('[data-trigger]').first().waitFor();
    const triggers = [
      ['task.created', 'a task is created'], ['task.step_changed', 'a task enters a step'],
      ['task.done', 'a task is marked done'], ['task.declined', 'a task is declined'], ['task.closed', 'a task is closed'],
    ];
    assert.equal(await card.locator('[data-trigger]').count(), 5, 'the card shows the five task Triggers');
    for (const [trigger, words] of triggers)
      assert.equal(await card.locator(`[data-trigger="${trigger}"] .settings-cell-note`).innerText(), `Trigger: ${words}.`,
        `All tasks opening line for ${trigger}`);
    await page.locator('[data-task-automation-type]').selectOption('dev');
    await card.locator('[data-trigger]').first().waitFor();
    const typed = [
      ['task.created', 'a task of type Dev tickets is created'], ['task.step_changed', 'a task of type Dev tickets enters a step'],
      ['task.done', 'a task of type Dev tickets is marked done'], ['task.declined', 'a task of type Dev tickets is declined'],
      ['task.closed', 'a task of type Dev tickets is closed'],
    ];
    for (const [trigger, words] of typed)
      assert.equal(await card.locator(`[data-trigger="${trigger}"] .settings-cell-note`).innerText(), `Trigger: ${words}.`,
        `selected type opening line for ${trigger}`);
    assert.match(await card.innerText(), /Paused: too many runs/);
    assert.match(await card.innerText(), /Last Action failed.*2026.*The note could not be saved/);
    await card.locator('[data-task-add-action="task.created"]').click();
    await page.waitForFunction(() => location.hash === '#/automations/au_new', null, {timeout: 5000});

    // One automation, off, owned by the viewer, whose trigger is a task created of the Dev tickets type, and no steps yet.
    assert.equal(posts.length, 1);
    assert.deepEqual(posts[0].trigger, {type: 'event', name: 'task.created',
      filters: [{field: 'event.data.type_id', op: 'is', value: 'dev'}]});
    assert.equal(posts[0].enabled, false);
    assert.equal(posts[0].owner, 'person:ana');
    assert.deepEqual(posts[0].steps, []);
    // The created automation keeps its type Trigger when the Action is selected and saved.
    await page.locator('[data-add="root|0"]').click();
    await page.locator('.au-menu [data-ty="action"]').click();
    const actionOptions = await page.locator('.au-ed [data-b="step.do"] option').allInnerTexts();
    assert.deepEqual(actionOptions, ['Note', 'Wake a bot', 'Create task', 'Call a webhook']);
    await page.selectOption('.au-ed [data-b="step.do"]', 'create_task');
    await page.fill('.au-ed [data-b="step.args.title"]', 'Review the change');
    await page.selectOption('.au-ed [data-b="step.args.owner"]', 'bot:pr-reviewer');
    await page.locator('.au-save').click();
    await page.waitForFunction(() => document.querySelector('.au-ed')?.hidden && document.querySelector('.au-ver')?.textContent === 'v2');
    assert.equal(patches.length, 1);
    assert.deepEqual(made.trigger, posts[0].trigger, 'the saved automation keeps the type Trigger');
    assert.deepEqual(patches[0].steps[0], {id: patches[0].steps[0].id, type: 'action', do: 'create_task', enabled: true,
      args: {title: 'Review the change', owner: 'bot:pr-reviewer'}});
    assert.match(await page.locator('.au-trig').innerText(), /Dev tickets/);
    assert.match(await page.locator('.au-card[data-open]').last().innerText(), /Create task[\s\S]*pr-reviewer/);
    assert.deepEqual(errors, [], 'no page errors');
    await page.goto('https://tico-ui.test/#/settings/tasks');
    const settingsCard = page.locator('#set-task-automations');
    await settingsCard.locator('[data-task-automation-type]').waitFor();
    await settingsCard.locator('[data-task-automation-type]').selectOption('dev');
    const saved = settingsCard.locator('[data-trigger="task.created"] .task-automation-rule', {hasText: 'Dev tickets action'});
    await saved.waitFor();
    assert.equal(await saved.locator('a').first().getAttribute('href'), '#/automations/au_new',
      'the saved Action stays in its Trigger section');
    await page.locator('#set-task-automations [data-task-add-action="task.step_changed"]').click();
    await page.waitForFunction(() => location.hash === '#/automations/au_new_step', null, {timeout: 5000});
    assert.deepEqual(posts[1].trigger, {type: 'event', name: 'task.step_changed',
      filters: [{field: 'event.data.type_id', op: 'is', value: 'dev'}]}, 'the card Add action pre-fills its Trigger and type');
    assert.match(await page.locator('.au-trig').innerText(), /Dev tickets/);
    // Add action and edit controls follow the Settings permission check.
    mover = false;
    await page.goto('https://tico-ui.test/?no-cache=1#/settings/tasks');   // a new document, so the page opens on Settings
    await page.locator('.task-type-row', {hasText: 'Dev tickets'}).waitFor();
    assert.equal(await page.locator('[data-add-action]').count(), 0, 'no Task types Add action without the right');
    assert.equal(await page.locator('[data-task-add-action]').count(), 0, 'no card Add action without the right');
    assert.equal(await page.locator('[data-task-edit]').count(), 0, 'no task Trigger or Action edit control without the right');
    assert.equal(await page.locator('[data-edit-type]').count(), 0, 'no Edit without the right');
    assert.match(await page.locator('#set-task-automations').innerText(), /Ask a Settings manager/);
    console.log('task-types-add-action: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
