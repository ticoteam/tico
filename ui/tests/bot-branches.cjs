// Offline branch picker, computer ownership and definition editor contracts.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}, serviceWorkers: 'block'});
    page.setDefaultTimeout(5000);
    const errors = [], writes = [];
    page.on('pageerror', error => errors.push(error.message));
    const base = {host: 'keeper', status: 'active', state: 'active', can_chat: true, can_manage: true,
      my_access: {see: true, read: true, write: true}, schedules: [], revision: 1, thread_mode: 'personal', users: []};
    const original = {...base, name: 'architect', slug: 'architect', display_name: 'Architect', operator: 'sam', shared: true, repo: 'bot-architect'};
    const bots = [original];
    const people = [{id: 'ana', name: 'Ana'}, {id: 'sam', name: 'Sam'}];
    let computers = [
      {id: 'ana-mac', label: 'My Mac', operator: 'ana'},
      {id: 'sam-mac', label: 'Other Mac', operator: 'sam'},
      {id: 'old-mac', label: 'Old Mac', operator: 'ana', revoked_at: '2026-01-01'}];
    let collision = true, stale = true;
    let assignmentAllocator = false;
    let assignment = {id: 'assignment-1', source_bot: 'architect', assignment_key: 'feature-1', generation: 1,
      bot: 'architect-work-abc-g1', task_id: 'task-1', display_name: 'Pro Workflow Engineer', phase: 'working',
      revision: 1, runner_id: 'ana-mac', checkpoint: {next: 'review'},
      task: {id: 'task-1', title: 'Ship the scoped feature', status: 'review', owner: 'bot:architect-work-abc-g1'}};
    await page.route('**/*', route => {
      const request = route.request(), url = new URL(request.url()), p = url.pathname;
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
      if (p === '/api/employees') return json(bots);
      if (p === '/api/humans') return json({people});
      if (p === '/api/issues') return json([]);
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], schedules: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/goals') return json({goals: [], chain: [], company: [], reports: []});
      if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, today: {}});
      if (p === '/api/v2/tasks') return json({tasks: []});
      if (p === '/api/v2/tasks/task-1') return json({task: assignment.task});
      if (p === '/api/v2/conversations') return json({conversations: []});
      if (p === '/api/v2/computers') return json({computers});
      if (p === '/api/v2/bots/architect/assignment-branches' && request.method() === 'GET')
        return json({source: 'architect', enabled: true, allocator_enabled: assignmentAllocator,
          capacity: 3, active: assignment.phase === 'waiting_review' ? 0 : 1, assignments: [assignment]});
      if (p === '/api/v2/bots/architect/assignment-branches/policy') {
        const body = JSON.parse(request.postData()); writes.push([p, body]); assignmentAllocator = body.enabled;
        return json({source: 'architect', enabled: assignmentAllocator, allocator: 'bot:lead', revision: 2});
      }
      if (p === '/api/v2/bots/architect/assignment-branches' && request.method() === 'POST') {
        const body = JSON.parse(request.postData()); writes.push([p, body]);
        const made = {...base, name: 'architect-work-new-g1', slug: 'architect-work-new-g1', can_manage: true};
        bots.push(made); return json({bot: made.name});
      }
      if (p === '/api/v2/assignment-branches/assignment-1/events')
        return json({events: [{actor: 'human:ana', action: 'working', detail_json: '{}', created: '2026-10-05T00:00:00Z'}]});
      if (p === '/api/v2/assignment-branches/assignment-1' && request.method() === 'PATCH') {
        const body = JSON.parse(request.postData()); writes.push([p, body]);
        assignment = {...assignment, phase: body.phase || assignment.phase, revision: assignment.revision + 1};
        return json({...assignment, ...(body.confirm_learning_review ? {learning_message_id: 'learning-message'} : {})});
      }
      if (p === '/api/v2/assignment-branches/assignment-1/cleanup' && request.method() === 'POST') {
        const body = JSON.parse(request.postData()); writes.push([p, body]);
        assignment = {...assignment, cleanup: {state: 'requested', detail: ''}};
        return json({assignment_id: assignment.id, state: 'requested'});
      }
      if (p.endsWith('/branches')) return json({original: 'architect', shared: original.shared,
        branches: bots.filter(b => b.shared_from).map(b => ({...b, slug: b.name}))});
      if (p.endsWith('/copies')) {
        writes.push([p, JSON.parse(request.postData())]);
        if (collision) return route.fulfill({status: 409, contentType: 'application/json', body: JSON.stringify({error: {detail: 'That computer already runs the original or a branch of it. Choose another computer.'}})});
        const branch = {...base, name: 'architect-ana', slug: 'architect-ana', display_name: 'Architect', operator: 'ana', shared_from: 'architect',
          status: JSON.parse(request.postData()).runner_id ? 'active' : 'planned'};
        bots.push(branch); return json(branch);
      }
      if (p.endsWith('/definition')) {
        writes.push([p, JSON.parse(request.postData())]);
        if (p.includes('architect-ana') && stale) {
          stale = false;
          return route.fulfill({status: 409, contentType: 'application/json', body: JSON.stringify({error: {code: 'version_conflict', detail: 'Bot configuration changed; refresh before saving'}})});
        }
        return json({revision: 3});
      }
      if (p === '/api/v2/bots/architect-ana') return json({revision: 2});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p.endsWith('/files')) return json({files: []});
      return json({});
    });
    await page.goto('https://tico-ui.test/#/bot/architect');
    await page.locator('[data-branch-make]').waitFor();
    await page.locator('#bot-assignment-branches .assignment-card').waitFor();
    assert.match(await page.locator('#bot-assignment-branches').innerText(), /1\/3 active/);
    assert.match(await page.locator('#bot-assignment-branches').innerText(), /Ship the scoped feature/);
    assert.match(await page.locator('#bot-assignment-branches').innerText(), /Parent role:/);
    assert.match(await page.locator('#bot-assignment-branches').innerText(), /checkpoint:.*review/);
    await page.locator('[data-assignment-policy]').check();
    assert.deepEqual(writes.find(([path]) => path.endsWith('/assignment-branches/policy'))[1],
      {enabled: true, expected_revision: 1});
    page.once('dialog', dialog => dialog.accept('waiting_review'));
    await page.locator('[data-assignment-manage="assignment-1"]').click();
    await page.waitForFunction(() => document.querySelector('#bot-assignment-branches .assignment-phase')?.textContent === 'waiting_review');
    assert.equal(writes.filter(([path]) => path.endsWith('/assignment-branches/assignment-1')).at(-1)[1].phase, 'waiting_review');
    assert.equal(await page.locator('[data-branch-picker]').count(), 0);
    await page.locator('[data-branch-make]').click();
    let dialog = page.locator('#branch-editor');
    await dialog.waitFor({state: 'visible'});
    assert.deepEqual(await dialog.locator('[name=runner_id] option').allTextContents(), ['My Mac']);
    await dialog.locator('[type=submit]').click();
    await dialog.locator('[data-branch-status]').filter({hasText: 'already runs'}).waitFor();
    assert.equal(await dialog.locator('[type=submit]').isEnabled(), true);
    collision = false;
    await dialog.locator('[type=submit]').click();
    await page.waitForFunction(() => BOT?.slug === 'architect-ana');
    await page.locator('[data-branch-picker] option[value=architect-ana]').waitFor({state: 'attached'});
    assert.equal(await page.locator('[data-branch-picker]').inputValue(), 'architect-ana');
    assert.equal(await page.locator('[data-branch-make]').count(), 0);
    assert.equal(await page.locator('#bot-branches a').getAttribute('href'), '#/bot/architect');
    assert.deepEqual(writes[0][1], {runner_id: 'ana-mac'});
    original.shared = false;
    await page.evaluate(() => botBranchesLoad('architect-ana'));
    assert.match(await page.locator('#bot-branches').innerText(), /branches off/);
    await page.evaluate(() => settingsEditBot('architect-ana'));
    assert.equal(await dialog.locator('[name=description]').count(), 0);
    assert.equal(await dialog.locator('[name=model_effort]').count(), 0);
    await dialog.locator('[name=status]').selectOption('paused');
    await dialog.locator('[type=submit]').click();
    assignment = {...assignment, phase: 'archived', revision: 5, cleanup: null,
      task: {...assignment.task, status: 'done'}};
    await page.evaluate(() => assignmentBranchesLoad('architect'));
    await page.locator('[data-assignment-cleanup="assignment-1"]').waitFor();
    page.once('dialog', dialog => dialog.accept());
    await page.locator('[data-assignment-cleanup="assignment-1"]').click();
    await page.locator('[data-assignment-cleanup="assignment-1"][disabled]').waitFor();
    assert.deepEqual(writes.find(([path]) => path.endsWith('/assignment-1/cleanup'))[1], {expected_revision: 5});
    await dialog.locator('[data-branch-status]').filter({hasText: 'configuration changed'}).waitFor();
    await page.waitForFunction(() => !document.querySelector('#branch-editor [type=submit]').disabled);
    await dialog.locator('[type=submit]').click();
    await page.waitForFunction(() => !document.querySelector('#branch-editor'));
    assert.deepEqual(writes.at(-1)[1], {status: 'paused', expected_revision: 2});
    await page.waitForFunction(() => document.querySelector('#bot-branches select'));
    await page.evaluate(() => { location.hash = '#/bot/architect/more'; });
    await page.waitForFunction(() => BOT?.slug === 'architect' && BOT?.tab === 'more');
    await page.locator('[data-branch-picker]').waitFor();
    assert.equal(await page.locator('[data-branch-picker] option[value=architect-ana]').count(), 1);
    dialog = page.locator('#bot-editor');
    await page.evaluate(() => { S.emps.find(e => e.name === 'architect').shared = false; SETTINGS_DATA.models = []; SETTINGS_DATA.people = S.people; SETTINGS_DATA.machines = []; settingsEditBot('architect'); });
    const allow = dialog.locator('[name=shared]');
    await allow.waitFor();
    assert.equal(await allow.isChecked(), false);
    assert.match(await allow.locator('..').innerText(), /Allow branches/);
    await allow.check();
    await dialog.locator('[type=submit]').click();
    await page.waitForFunction(() => !document.querySelector('#bot-editor').open);
    assert.equal(writes.at(-1)[1].shared, true);
    // Eligibility comes from both the current computer assignments and the roster's family.
    original.shared = true;
    computers = [{id: 'ana-mac', label: 'My Mac', operator: 'ana', bots: ['architect']},
      {id: 'ana-other', label: 'Second Mac', operator: 'ana', bots: []}];
    await page.evaluate(() => botBranchCreate('architect'));
    dialog = page.locator('#branch-editor');
    assert.deepEqual(await dialog.locator('[name=runner_id] option').allTextContents(), ['Second Mac']);
    await dialog.locator('[data-branch-close]').click();
    // No submit is needed to discover the collision, and a planned branch posts the existing empty payload.
    computers.pop();
    await page.evaluate(() => botBranchCreate('architect'));
    assert.match(await dialog.innerText(), /Your computers already run the original or a branch/);
    assert.equal(await dialog.locator('[name=runner_id]').count(), 0);
    assert.equal(await dialog.locator('[type=submit]').innerText(), 'Create planned branch');
    await dialog.locator('[type=submit]').click();
    await page.waitForFunction(() => !document.querySelector('#branch-editor'));
    assert.deepEqual(writes.at(-1)[1], {});
    computers = [{id: 'ana-mac', label: 'My Mac', operator: 'ana', bots: ['architect-sam']}];
    await page.evaluate(() => {
      S.emps.push({name: 'architect-sam', shared_from: 'architect', operator: 'sam', machine: {runner_id: 'ana-mac'}});
      return botBranchCreate('architect');
    });
    assert.match(await dialog.innerText(), /Your computers already run/);
    await dialog.locator('[data-branch-close]').click();
    computers = [];
    await page.evaluate(() => botBranchCreate('architect'));
    assert.match(await dialog.innerText(), /You have no computer yet/);
    assert.equal(await dialog.locator('[type=submit]').isEnabled(), true);
    await dialog.locator('[data-branch-close]').click();
    await page.evaluate(() => { location.hash = '#/bot/architect'; });
    await page.waitForFunction(() => BOT?.slug === 'architect');
    page.on('dialog', async dialog => {
      if (dialog.type === 'prompt') await dialog.accept('Use bounded retries for transient reads.');
      else await dialog.accept();
    });
    await page.locator('[data-assignment-learning="assignment-1"]').click();
    await page.waitForFunction(() => document.querySelector('#bot-assignment-branches .assignment-phase')?.textContent === 'waiting_review');
    const lessonWrite = writes.filter(([path]) => path.endsWith('/assignment-branches/assignment-1')).at(-1)[1];
    assert.equal(lessonWrite.confirm_learning_review, true);
    assert.equal(lessonWrite.reviewed_learning_note, 'Use bounded retries for transient reads.');
    await page.locator('[data-assignment-create]').click();
    dialog = page.locator('#branch-editor');
    await dialog.locator('[name=task_id]').fill('task-new');
    await dialog.locator('[name=display_name]').fill('Pro Workflow Engineer');
    await dialog.locator('[type=submit]').click();
    await page.waitForFunction(() => !document.querySelector('#branch-editor'));
    const createWrite = writes.find(([path]) => path === '/api/v2/bots/architect/assignment-branches');
    const stableKey = await page.evaluate(async () => {
      const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode('task-new'));
      return 'task-' + Array.from(new Uint8Array(digest).slice(0, 18), byte => byte.toString(16).padStart(2, '0')).join('');
    });
    assert.deepEqual(createWrite[1], {
      assignment_key: stableKey, generation: 1, task_id: 'task-new', display_name: 'Pro Workflow Engineer'
    });
    assert.deepEqual(errors, []);
    console.log('bot branches: passed');
    await page.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
