// Offline browser regression: Settings > Tasks > Task types, Add action on a type's row. It makes a new automation of the
// viewer's, off and with no steps, whose trigger is a task created of that type (its filter names the type), and opens it
// in Automations. The type reads as its name, not its id, in the automation's trigger.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

let mover = true;   // who may change Task types (Edit and Add action show only to them)
const types = [{id: 'dev', name: 'Dev tickets', steps: [], roles: []}, {id: 'general', name: 'General', steps: [], roles: []}];

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  const errors = [], posts = [];
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
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, role: 'owner', mover, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json([]);
      if (p === '/api/v2/task-types') return json({types});
      if (p === '/api/issues') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/needs-you') return json({items: []});
      if (p === '/api/v2/tags') return json({tags: []});
      if (p === '/api/v2/roadmaps/settings') return json({labels: [], cors_origins: [], action_limit: 10, suggestion_limit: 20, bot_public_approval: true, suggestions_bot: null, suggestions_receiver: {bot: null, name: null, source: 'automatic'}});
      if (p === '/api/v2/automations' && req.method() === 'POST') {
        const body = req.postDataJSON(); posts.push(body);
        made = {id: 'au_new', name: body.name, owner: body.owner, enabled: false, builtin: false, can_edit: true, trigger: body.trigger, steps: []};
        return json(made, 201);
      }
      if (p === '/api/v2/automations/au_new') return json(made);
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/settings/tasks');
    const row = page.locator('.task-type-row', {hasText: 'Dev tickets'});
    await row.locator('[data-add-action="dev"]').waitFor();
    await row.locator('[data-add-action="dev"]').click();
    await page.waitForFunction(() => location.hash === '#/automations/au_new', null, {timeout: 5000});

    // One automation, off, owned by the viewer, whose trigger is a task created of the Dev tickets type, and no steps yet.
    assert.equal(posts.length, 1);
    assert.deepEqual(posts[0].trigger, {type: 'event', name: 'task.created',
      filters: [{field: 'event.data.type_id', op: 'is', value: 'dev'}]});
    assert.equal(posts[0].enabled, false);
    assert.equal(posts[0].owner, 'person:ana');
    assert.deepEqual(posts[0].steps, []);
    assert.deepEqual(errors, [], 'no page errors');
    // A person who may not change Task types sees no Add action and no Edit on the rows.
    mover = false;
    await page.reload();
    await page.locator('.task-type-row', {hasText: 'Dev tickets'}).waitFor();
    assert.equal(await page.locator('[data-add-action]').count(), 0, 'no Add action without the right');
    assert.equal(await page.locator('[data-edit-type]').count(), 0, 'no Edit without the right');
    console.log('task-types-add-action: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
