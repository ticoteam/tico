// Pipeline columns, step edits, and Types settings use the task's stable status contract.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();   // load.cjs arrives with #254
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1200, height: 900}, serviceWorkers: 'block'});
    const errors = [], posted = [];
    page.on('pageerror', error => errors.push(error.message));
    const statuses = ['open', 'doing', 'waiting', 'review', 'ready', 'done', 'closed', 'declined'];
    let types = [
      {id: 'general', name: 'General', steps: statuses.map((status, position) => ({id: 'general-' + status, type_id: 'general', name: status, status, position}))},
      {id: 'marketing', name: 'Marketing', steps: [
        {id: 'draft', name: 'Draft', status: 'open'}, {id: 'legal', name: 'Legal review', status: 'review'},
        {id: 'copy', name: 'Copy review', status: 'review'}, {id: 'complete', name: 'Complete', status: 'done'},
        {id: 'archive', name: 'Archive', status: 'closed'}].map((step, position) => ({...step, position, type_id: 'marketing'}))},
      // The roadmap types go by role, whoever manages them; a Dev tickets type made later beside Engineering goes first.
      {id: 'product-roadmap', name: 'Product roadmap', role: 'product-roadmap', manager: 'product-lead', steps: []},
      {id: 'engineering', name: 'Engineering', role: 'engineering', manager: 'engineering-lead', steps: []},
      {id: 'dev-tickets', name: 'Dev tickets', role: 'engineering', manager: null, steps: []}];
    const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
    const tag = {id: 'release-tag', key: 'release-2026-10-02', label: 'release',
      metadata: {date: '2026-10-02'}, markdown: '- [ ] Smoke checks', is_template: false, version: 1};
    const bots = [{name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', can_chat: true}];
    const tasks = ['draft', 'copy', 'complete', null].map((stepId, index) => {
      const step = types[1].steps.find(step => step.id === stepId) || null;
      return {id: 'task-' + index, title: ['Write the campaign', 'Review the campaign', 'Finished campaign', 'Unmapped campaign'][index],
        body: 'Please review the campaign.', owner: 'bot:ops', requester: 'human:ana',
        type_id: 'marketing', step_id: stepId, type: {id: 'marketing', name: 'Marketing'}, step,
        status: step?.status || 'doing', lane: 'company', version: 1, labels: [], links: [], parts: {total: 0, done: 0},
        rank: index, created: '2026-10-01T12:00:00Z', updated: '2026-10-01T12:00:00Z'};
    });
    tasks.push({...tasks[0], id: 'general-task', title: 'General task', type_id: 'general', step_id: 'general-open',
      type: {id: 'general', name: 'General'}, step: types[0].steps[0]});
    Object.assign(tasks[1], {labels: [tag.key], tags: [tag]});
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui) { const file = uiFile(ui[1]); if (fs.existsSync(file)) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')}); }
      if (p === '/api/me') return json(me);
      if (p === '/api/employees') return json(bots);
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}]});
      if (p === '/api/issues') return json([]);
      if (p === '/api/status') return json({active: [], employees: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/routines') return json({routines: []});
      if (p === '/api/v2/tasks/labels') return json({labels: [tag.key], tags: [tag]});
      if (p === '/api/v2/tags' && req.method() === 'GET') return json({tags: [tag]});
      if (p.startsWith('/api/v2/preferences/') && req.method() === 'GET') {
        await new Promise(resolve => setTimeout(resolve, t(100)));
        return json({value: {type: 'marketing', view: 'board', views: 2}});
      }
      if (p === '/api/v2/task-types' && req.method() === 'GET') return json({types});
      if (p.startsWith('/api/v2/task-types') && req.method() === 'POST') {
        const body = req.postDataJSON(); posted.push({path: p, body});
        const id = p.split('/')[4];
        if (p.endsWith('/delete')) { types = types.filter(type => type.id !== id); return json({type: {id}}); }
        const type = id ? types.find(type => type.id === id) : {id: 'new-type', name: body.name};
        if (!id) types.push(type);
        if (body.name) type.name = body.name;
        if (body.steps) type.steps = body.steps.map((step, position) => ({...step, id: step.id || 'new-step-' + position, type_id: type.id}));
        return json({type});
      }
      if (p === '/api/v2/tasks' && req.method() === 'GET') {
        const wanted = url.searchParams.get('status')?.split(',');
        return json({tasks: wanted ? tasks.filter(task => wanted.includes(task.status)) : tasks, next_offset: null});
      }
      const one = p.match(/^\/api\/v2\/tasks\/([^/]+)$/);
      if (one) {
        const task = tasks.find(task => task.id === one[1]);
        if (req.method() === 'POST') {
          const body = req.postDataJSON(); posted.push({path: p, body});
          if (body.type) task.type_id = body.type;
          const type = types.find(type => type.id === task.type_id);
          const step = 'step' in body ? type.steps.find(step => step.id === body.step) : type.steps.find(step => step.status === (body.status || task.status));
          Object.assign(task, {type: {id: type.id, name: type.name}, step: step || null, step_id: step?.id || null,
            status: step?.status || body.status || task.status, version: task.version + 1});
        }
        return json({task, children: [], parent: null, comments: [], events: [], messages: [], mover: me.mover});
      }
      if (p === '/api/v2/conversations') return json({conversations: []});
      if (p.endsWith('/messages')) return json({messages: []});
      if (p === '/api/v2/models') return json({models: [], harnesses: []});
      return json(unrouted(p));
    });
    await page.goto('http://tico-ui.test/#/tasks');
    await page.waitForFunction(() => TASKS_ST?.doneLoaded && TASKS_ST.type === 'marketing');
    assert.deepEqual(await page.locator('#task-body .bcol h2').allTextContents(),
      ['Draft', 'Legal review', 'Copy review', 'Complete', 'Archive', 'Doing']);
    assert.equal(await page.locator('#task-body .bcard').count(), 4, 'a saved type includes finished work');
    assert.equal(await page.locator('#task-body').getByText('General task', {exact: true}).count(), 0);
    // The saved type appears in the header, and the address carries it.
    assert.equal(await page.locator('#task-type-more').innerText(), 'Marketing');
    assert.deepEqual(await page.locator('#task-type [data-task-type]').evaluateAll(buttons => buttons.map(b => [b.dataset.taskType, b.textContent])),
      [['general', 'General'], ['product-roadmap', 'Roadmap'], ['dev-tickets', 'Dev tickets']]);
    assert.equal(await page.locator('[data-chip="type"]').count(), 0);
    await page.waitForFunction(() => location.hash.includes('type=marketing'));
    // Column headings name their steps without circular status glyphs.
    assert.equal(await page.locator('#task-body .bcol > header > .si').count(), 0);
    assert.equal(await page.locator('#task-body .bcol > header > h2').count(), 6);
    assert.equal(await page.locator('#task-body .bcard .task-status').count(), 0, 'custom step columns supply status context too');
    await page.locator('#task-filter').click();
    await page.locator('#task-filter-pop [data-pick-field="tag"]').click();
    await page.locator(`#task-filter-pop input[value="${tag.key}"]`).check();
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#task-body .bcard').count(), 1, 'tag and type filters combine');
    assert.equal(await page.locator('#task-body .tlabel').innerText(), 'release · Oct 2');
    await page.locator('[data-chip-drop="tag"]').click();
    await page.locator('#task-body [data-open-task="ttask-1"]').click();
    const peek = page.locator('#task-peek');
    // A typed task's properties have Type and Step rows; its Status menu lists the type's steps.
    const step = peek.locator('[data-prop="step"]');
    await step.waitFor();
    assert.match(await step.innerText(), /Copy review/);
    assert.match(await peek.locator('[data-prop="type"]').innerText(), /Marketing/);
    assert.equal(await peek.locator('.pill').first().textContent(), 'Copy review');
    assert.equal(await peek.locator('[data-tag-key]').innerText(), 'release · Oct 2');
    assert.equal(await peek.locator('[data-prop="tags"]').count(), 1);
    await peek.locator('[data-prop="status"]').click();
    assert.deepEqual((await peek.locator('.prop-pop .tl-mi').allInnerTexts()).map(t => t.replace('✓', '').trim()), ['Draft', 'Legal review', 'Copy review', 'Complete', 'Archive']);
    assert.match(await peek.locator('.prop-pop .tl-mi[aria-checked="true"]').innerText(), /Copy review/);
    await page.keyboard.press('Escape');
    await step.click();
    await peek.locator('.prop-pop [data-prop-pick="legal"]').click();
    await page.waitForFunction(() => /Legal review/.test(document.querySelector('#task-peek [data-prop="step"]')?.textContent || '') && TASKS_ST.tasks.find(task => task.id === 'task-1').version === 2);
    assert.deepEqual(posted.find(item => item.path.endsWith('/task-1')).body, {version: 1, step: 'legal'});
    assert.equal(await peek.locator('[data-tag-key]').innerText(), 'release · Oct 2', 'step moves keep tags');
    await peek.locator('[data-prop="type"]').click();
    await peek.locator('.prop-pop [data-prop-pick="general"]').click();
    // It leaves the Marketing board, so the list and the peek move on to the next card.
    await page.waitForFunction(() => TASKS_ST.peek && TASKS_ST.peek !== 'ttask-1');
    assert.deepEqual(posted.filter(item => item.path.endsWith('/task-1')).at(-1).body, {version: 2, type: 'general'});
    assert.equal(tasks.find(task => task.id === 'task-1').status, 'review', 'a General task keeps its status');
    await peek.locator('[data-modal-close]').click();
    await peek.waitFor({state: 'hidden'});
    await page.locator('[data-task-type="general"]').click();
    await page.waitForFunction(() => location.hash.includes('type=general'));
    assert.equal(await page.locator('#task-body .bcol').count(), statuses.length, 'General has its own columns');
    assert.equal(await page.locator('#task-body .bcard').count(), 2, 'General never includes Marketing tasks');
    await page.locator('#task-new').click();
    await page.locator('#task-create select[name=type]').waitFor();
    await page.locator('#task-create [data-modal-close]').click();
    await page.locator('#task-new').click();
    await page.locator('#task-create select[name=type]').waitFor();
    assert.equal(await page.locator('#task-create select[name=type]').count(), 1);
    await page.locator('#task-create [data-modal-close]').click();
    types[1].steps.push({id: 'verified', name: 'Verified', status: 'done', position: 5, type_id: 'marketing'},
      {id: 'filed', name: 'Filed', status: 'closed', position: 6, type_id: 'marketing'});
    const decision = {...tasks[2], id: 'decision', owner: 'human:ana', requester: 'bot:ops'};
    tasks.push(decision);
    await page.evaluate(task => taskModalShow(task), decision);
    const pickStep = async (id, name) => {
      await page.locator('#task-modal [data-prop="step"]').click();
      await page.locator(`#task-modal .prop-pop [data-prop-pick="${id}"]`).click();
      await page.waitForFunction(name => document.querySelector('#task-modal [data-prop="step"]')?.textContent.includes(name), name);
    };
    await page.locator('#task-modal [data-prop="step"]').waitFor();
    await pickStep('verified', 'Verified');
    assert.equal(await page.locator('.task-outcome').count(), 0, 'same-status steps add no result prompt');
    await pickStep('archive', 'Archive');
    await pickStep('filed', 'Filed');
    assert.equal(await page.locator('.task-outcome').count(), 0, 'closed-to-closed step move adds no result prompt');
    await page.locator('#task-modal [data-modal-close]').click();
    await page.evaluate(() => pageSettings());
    await page.locator('[data-settings-tab=tasks]').click();
    await page.locator('#settings-tags [href="#/tag/release-2026-10-02"]').waitFor();
    await page.locator('#set-types [data-edit-type=marketing]').waitFor();
    assert.equal(await page.locator('#settings-types').isVisible(), true, 'Task types share the Tasks pane');
    assert.equal(await page.locator('#settings-tags').isVisible(), true, 'Task tags share the Tasks pane');
    for (const legacy of ['tags', 'types']) {
      await page.evaluate(tab => settingsShow(tab), legacy);
      assert.equal(await page.locator('[data-settings-tab=tasks]').getAttribute('aria-selected'), 'true', 'saved routes select Tasks');
    }
    await page.locator('#set-types [data-edit-type=marketing]').click();
    await page.locator('.task-type-editor input[aria-label="Type name"]').fill('Campaigns');
    await page.locator('.task-type-editor [data-steps] .task-step-edit').nth(1).locator('[data-up]').click();
    assert.equal(await page.locator('.task-type-editor [name=typeBots]').inputValue(), 'parties', 'a type starts with its bots kept to their own tasks');
    await page.locator('.task-type-editor [name=typeBots]').selectOption('work');
    await page.locator('.task-type-editor button[type=submit]').click();
    await page.getByText('Campaigns', {exact: true}).waitFor();
    const edit = posted.find(item => item.path === '/api/v2/task-types/marketing');
    assert.equal(edit.body.steps[0].id, 'legal', 'editing keeps ids and order');
    assert.equal(edit.body.bots, 'work', 'the editor saves what bots may do with the type');
    await page.locator('#set-types [data-new-type]').click();
    await page.locator('.task-type-editor input[aria-label="Type name"]').fill('Design');
    await page.locator('.task-type-editor button[type=submit]').click();
    await page.getByText('Design', {exact: true}).waitFor();
    await page.locator('#set-types [data-edit-type=new-type]').click();
    await page.locator('.task-type-editor [data-delete-type]').click();
    await page.waitForFunction(() => !document.querySelector('#set-types [data-edit-type=new-type]'));
    assert(posted.some(item => item.path.endsWith('/new-type/delete')));
    assert.deepEqual(errors, []);
    console.log('PASS: pipeline columns, status fallback, saved type, modal step mapping, General control, Types CRUD.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
