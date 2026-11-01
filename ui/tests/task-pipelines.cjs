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
    // No seeded step is waiting or declined (backend retire_held_steps); the board shows only the working steps.
    const statuses = ['open', 'doing', 'review', 'ready', 'done', 'closed'];
    let types = [
      {id: 'general', name: 'General', steps: statuses.map((status, position) => ({id: 'general-' + status, type_id: 'general', name: status, status, position}))},
      {id: 'marketing', name: 'Marketing', steps: [
        {id: 'draft', name: 'Draft', status: 'open'},
        {id: 'legal', name: 'Legal review', status: 'review', flow: {check: 'review', on_pass: 'Complete', on_fail: 'Draft'}},
        {id: 'copy', name: 'Copy review', status: 'review'}, {id: 'complete', name: 'Complete', status: 'done'},
        {id: 'archive', name: 'Archive', status: 'closed'}].map((step, position) => ({...step, position, type_id: 'marketing'}))},
      // The roadmap types go by role, whoever manages them; a Dev tickets type made later beside Engineering goes first.
      {id: 'product-roadmap', name: 'Product roadmap', role: 'product-roadmap', manager: 'product-lead', steps: []},
      {id: 'engineering', name: 'Engineering', role: 'engineering', manager: 'engineering-lead', steps: []},
      {id: 'dev-tickets', name: 'Dev tickets', role: 'engineering', manager: null, steps: []}];
    const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
    const tag = {id: 'release-tag', key: 'release-2026-10-02', label: 'release',
      metadata: {date: '2026-10-02'}, markdown: '- [ ] Smoke checks', is_template: false, version: 1};
    const bots = [{name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', can_chat: true},
      {name: 'pr-reviewer-sol', display_name: 'Reviewer GPT', status: 'active', resolved_model: 'GPT 6.1 Sol', resolved_provider: 'OpenAI',
        latest_runtime_model: 'Claude Opus 5.5', latest_runtime_provider: 'Anthropic'},
      {name: 'reviewer-xai', display_name: 'Reviewer xAI', status: 'active', resolved_model: 'Claude Haiku 5.5', resolved_provider: 'Anthropic'}];
    let refuseMove = false;
    const tasks = ['draft', 'copy', 'complete', null].map((stepId, index) => {
      const step = types[1].steps.find(step => step.id === stepId) || null;
      return {id: 'task-' + index, title: ['Write the campaign', 'Review the campaign', 'Finished campaign', 'Unmapped campaign'][index],
        body: 'Please review the campaign.', owner_id: 'bot:ops', requester_id: 'human:ana',
        type_id: 'marketing', step_id: stepId, type: {id: 'marketing', name: 'Marketing'}, step,
        status: step?.status || 'doing', lane: 'company', version: 1, labels: [], links: [], parts: {total: 0, done: 0},
        rank: index, created_at: '2026-10-01T12:00:00Z', updated_at: '2026-10-01T12:00:00Z'};
    });
    tasks.push({...tasks[0], id: 'general-task', title: 'General task', type_id: 'general', step_id: 'general-open',
      type: {id: 'general', name: 'General'}, step: types[0].steps[0]});
    Object.assign(tasks[1], {labels: [tag.key], tags: [tag]});
    // Waiting is a tag on a task that stays in its step.
    tasks[0].status = 'waiting';
    // A Dev ticket at a working step carries when it got there; past four hours its card says so in amber.
    tasks[3].step_since = new Date(Date.now() - 5 * 3600e3).toISOString();
    tasks[1].step_since = new Date(Date.now() - 30 * 60e3).toISOString();
    tasks.push({...tasks[2], id: 'closed-task', title: 'Dropped campaign', step_id: 'archive', step: types[1].steps[4], status: 'closed'});
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
      if (p === '/api/v2/check-pool-candidates') return json({candidates: url.searchParams.get('bots').split(',').map(slug =>
        slug === 'rev-a' ? {slug, name: 'Reviewer xAI', configured: {model: 'Claude Haiku 5.5', provider: 'Anthropic'}, runtime: null}
          : {slug, name: 'Reviewer GPT', configured: {model: 'GPT 6.1 Sol', provider: 'OpenAI'},
            runtime: {model: 'Claude Opus 5.5', provider: 'Anthropic'}})});
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
          if (refuseMove) { refuseMove = false; return json({error: {code: 'identity', detail: 'not yours to change'}}, 422); }
          if ('step_rank' in body) task.step_rank = body.step_rank;
          if (body.owner_id) task.owner_id = body.owner_id.includes(':') ? body.owner_id : 'bot:' + body.owner_id;
          if (body.type) task.type_id = body.type;
          const type = types.find(type => type.id === task.type_id);
          const step = 'step' in body ? type.steps.find(step => step.id === body.step) : type.steps.find(step => step.status === (body.status || task.status));
          Object.assign(task, {type: {id: type.id, name: type.name}, step: step || null, step_id: step?.id || null,
            status: step?.status || body.status || task.status, version: task.version + 1});
        }
        return json({task, children: [], parent: null, comments: [], events: [], messages: [], mover: me.mover});
      }
      // A bulk change by filter: this mock runs the filter, refuses the one task the caller may not change, and changes the rest.
      if (p === '/api/v2/task-bulk' && req.method() === 'POST') {
        const body = req.postDataJSON(), f = body.filter; posted.push({path: p, body});
        const picked = tasks.filter(task => f.ids ? f.ids.includes(task.id)
          : task.type_id === f.type && task.step_id === f.step && f.status.split(',').includes(task.status));
        const skipped = picked.filter(task => task.id === 'brief'), changed = picked.filter(task => task.id !== 'brief');
        for (const task of changed) Object.assign(task, {status: body.action === 'close' ? 'closed' : body.status || task.status, version: task.version + 1});
        const row = task => ({id: task.id, number: 1, title: task.title});
        return json({dry_run: false, matched: picked.length, cap: 200, changed: changed.length, unchanged: 0, skipped: skipped.length,
          tasks: {changed: changed.map(row), skipped: skipped.map(task => ({...row(task), code: 'identity', reason: 'not yours to change'}))}});
      }
      if (p === '/api/v2/conversations') return json({conversations: []});
      if (p.endsWith('/messages')) return json({messages: []});
      if (p === '/api/v2/models') return json({models: [], harnesses: []});
      return json(unrouted(p));
    });
    await page.goto('http://tico-ui.test/#/tasks');
    await page.waitForFunction(() => TASKS_ST && !TASKS_ST.loading && TASKS_ST.type === 'marketing' && document.querySelector('#task-body .bcard'));
    assert.deepEqual(await page.locator('#task-body .bcol h2').allTextContents(),
      ['Draft', 'Legal review', 'Copy review', 'Doing', 'Finished'], 'done and closed steps fold into one strip');
    assert.equal(await page.locator('#task-body .bcard').count(), 3, 'finished work leaves the board');
    assert.deepEqual(await page.locator('#task-body [data-col="draft"] .bcard .tl-waiting').allTextContents(), ['Waiting'],
      'a waiting task stays in its step with a tag');
    assert.equal(await page.locator('#task-body .tl-waiting').count(), 1);
    assert.deepEqual(await page.locator('#task-body [data-task-key="ttask-3"] [data-step-age].late').allTextContents(), ['5h in step']);
    assert.deepEqual(await page.locator('#task-body [data-task-key="ttask-1"] [data-step-age]:not(.late)').allTextContents(), ['30m in step']);
    assert.equal(await page.locator('#task-body [data-step-age]').count(), 2, 'only a ticket with a step clock shows one');
    // An unfolded finished strip is remembered: a fresh page opened straight on the board, before Done or Closed was ever
    // visited, reads the finished tasks for it, and the ordinary refresh keeps them current.
    const boardLoaded = () => page.waitForFunction(() => TASKS_ST && !TASKS_ST.loading && TASKS_ST.type === 'marketing' && document.querySelector('#task-body .bcard'));
    const finishedCol = (col, title) => page.waitForFunction(([col, title]) => {
      const el = document.querySelector(`#task-body [data-col="${col}"]`);
      return el?.querySelector('header .cnt')?.textContent === String(title.length)
        && [...el.querySelectorAll('.bcard-title')].map(n => n.textContent).sort().join('|') === title.join('|');
    }, [col, title], {timeout: t(5000)});
    await page.evaluate(() => localStorage.setItem('tico.board.fold', JSON.stringify({marketing: {finished: false}})));
    await page.reload();
    await boardLoaded();
    assert.equal(await page.evaluate(() => TASKS_ST.view), 'board');
    await finishedCol('complete', ['Finished campaign']);
    await finishedCol('archive', ['Dropped campaign']);
    tasks.push({...tasks[2], id: 'done-later', title: 'Finished later', updated_at: '2026-10-02T12:00:00Z'});
    await page.evaluate(() => tasksLoad(TASKS_ST, {poll: true}));
    await finishedCol('complete', ['Finished campaign', 'Finished later']);
    tasks.pop();
    await page.evaluate(() => localStorage.removeItem('tico.board.fold'));
    await page.reload();
    await boardLoaded();
    assert.equal(await page.locator('#task-body .bcard').count(), 3, 'folded again, finished work leaves the board');
    // Done and Closed are their own tabs, Closed right after Done; each lists only its status.
    assert.deepEqual((await page.locator('#task-view [role=tab]').allTextContents()).slice(-2), ['Done', 'Closed']);
    for (const [view, title] of [['done', 'Finished campaign'], ['closed', 'Dropped campaign']]) {
      await page.locator(`#task-view [data-view="${view}"]`).click();
      await page.waitForFunction(title => [...document.querySelectorAll('#task-body .tl-title')].map(n => n.textContent).join('|') === title, title);
    }
    await page.locator('#task-view [data-view="board"]').click();
    await page.locator('#task-body .bcol').first().waitFor();
    assert.equal(await page.locator('#task-body').getByText('General task', {exact: true}).count(), 0);
    // The saved type appears in the header, and the address carries it.
    assert.equal(await page.locator('#task-type-more').innerText(), 'Marketing');
    assert.deepEqual(await page.locator('#task-type [data-task-type]').evaluateAll(buttons => buttons.map(b => [b.dataset.taskType, b.textContent])),
      [['general', 'General'], ['product-roadmap', 'Roadmap'], ['dev-tickets', 'Dev tickets']]);
    assert.equal(await page.locator('[data-chip="type"]').count(), 0);
    await page.waitForFunction(() => location.hash.includes('type=marketing'));
    // Column headings name their steps without circular status glyphs.
    assert.equal(await page.locator('#task-body .bcol > header > .si').count(), 0);
    assert.equal(await page.locator('#task-body .bcol > header > h2').count(), 4);
    assert.equal(await page.locator('#task-body .bfold [data-board-unfold="finished"]').count(), 1);
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
    // Dragging cards: within a column, to another column, a refusal that puts the card back; and a plain click still peeks.
    const card = (stepId, n, title, over = {}) => ({...tasks[0], id: n, title, step_id: stepId, step: types[1].steps.find(s => s.id === stepId),
      status: 'review', version: 1, rank: 0, ...over});
    tasks.push(card('legal', 'plan', 'Plan the launch', {step_rank: 1}), card('legal', 'brief', 'Brief the agency', {step_rank: 2,
      relations: {blocks: [{id: 'plan', title: 'Plan the launch', status: 'review', owner_id: 'bot:ops', direction: 'in'}]}}));
    tasks[0].step_rank = 1;
    await page.evaluate(() => tasksLoad(TASKS_ST));
    await page.locator('#task-body [data-task-key="tbrief"]').waitFor();
    const titlesIn = col => page.locator(`#task-body [data-col="${col}"] .bcard-title`).allTextContents();
    const drag = async (from, to, dy = 0.5) => {
      const a = await page.locator(`#task-body [data-task-key="${from}"] .bcard-title`).boundingBox(), b = await page.locator(to).boundingBox();
      await page.mouse.move(a.x + 10, a.y + a.height / 2); await page.mouse.down();
      await page.mouse.move(a.x + 30, a.y + a.height / 2 + 20, {steps: 4});
      await page.mouse.move(b.x + b.width / 2, b.y + b.height * dy, {steps: 6});
      const sent = posted.length;
      await page.mouse.up();
      for (let i = 0; posted.length === sent && i < 200; i++) await page.waitForTimeout(t(10));
    };
    // Blocked: the strip names the blocker and focuses the board on the two; X shows every card again.
    assert.equal(await page.locator('#task-body [data-board-blocked="tbrief"]').innerText(), 'Blocked by Plan the launch');
    await page.locator('#task-body [data-board-blocked="tbrief"]').click();
    assert.deepEqual((await page.locator('#task-body .bcard-title').allTextContents()).sort(), ['Brief the agency', 'Plan the launch']);
    await page.locator('#task-body [data-board-unfocus]').waitFor();
    await page.keyboard.press('x');
    await page.waitForFunction(() => document.querySelectorAll('#task-body .bcard').length === 4 && !document.querySelector('[data-board-unfocus]'));
    // Waiting on a bot's prerequisite (`--on bot:ops`): the strip and the task name the bot, with the owner's reason.
    const brief = tasks.find(task => task.id === 'brief');
    Object.assign(brief, {status: 'waiting', note: 'Needs the launch plan reviewed first.'});
    await page.evaluate(() => tasksLoad(TASKS_ST));
    await page.waitForFunction(() => document.querySelector('#task-body [data-board-blocked="tbrief"]')?.innerText === 'Waiting on Ops');
    assert.equal(await page.locator('#task-body [data-board-blocked="tbrief"]').getAttribute('title'),
      'Waiting on Ops: Needs the launch plan reviewed first.\nBlocked by Plan the launch');
    await page.locator('#task-body [data-task-key="tbrief"] .bcard-title').click();
    await page.waitForFunction(() => TASKS_ST.peek === 'tbrief' && document.querySelector('#task-peek')?.open);
    assert.equal((await peek.locator('p.task-wait:not(.task-quiet)').textContent()).replace(/\s+/g, ' ').trim(),
      'Waiting on Ops: Needs the launch plan reviewed first.');
    await peek.locator('[data-modal-close]').click();
    await peek.waitFor({state: 'hidden'});
    // Its Blocked hold is that wait: one line on the card, with the hold's age. A review-gate hold says its own why.
    Object.assign(brief, {holds: [{kind: 'blocked', reason: 'Needs the launch plan reviewed first.', since: '2026-10-02T10:00:00Z', until: null, by: 'keeper', cause: 'dependency'}]});
    await page.evaluate(() => tasksLoad(TASKS_ST));
    await page.waitForFunction(() => /· since/.test(document.querySelector('#task-body [data-board-blocked="tbrief"]')?.title || ''));
    assert.equal(await page.locator('#task-body [data-task-key="tbrief"] .bcard-blocked').count(), 1);
    assert.match(await page.locator('#task-body [data-board-blocked="tbrief"]').getAttribute('title'),
      /^Waiting on Ops: Needs the launch plan reviewed first\. · since .+\nBlocked by Plan the launch$/);
    Object.assign(brief.holds[0], {cause: 'gate', reason: 'Review 1 has no verdict yet'});
    await page.evaluate(() => tasksLoad(TASKS_ST));
    await page.waitForFunction(() => document.querySelector('#task-body [data-board-blocked="tbrief"] span')?.innerText === 'Blocked by Plan the launch');
    assert.match(await page.locator('#task-body [data-task-key="tbrief"] .bcard-blocked').first().innerText(), /^Review 1 has no verdict yet · since/);
    Object.assign(brief, {holds: []});
    // Only the first open blocker's owner is named: a person's task linked ahead of the bot's reads "Blocked by".
    const opsBlocker = brief.relations.blocks[0];
    brief.relations = {blocks: [{id: 'legal-review', title: 'Legal review', status: 'doing', owner_id: 'human:ana', direction: 'in'}, opsBlocker]};
    await page.evaluate(() => tasksLoad(TASKS_ST));
    await page.waitForFunction(() => document.querySelector('#task-body [data-board-blocked="tbrief"] span')?.innerText === 'Blocked by Legal review');
    // A task that waits on a person keeps their ask: on them directly, or through the ask card a bot's task files,
    // which the server serves as a blocker the person owns, linked after the bot's.
    for (const held of [{waiting_on: 'human:ana'}, {relations: {blocks: [opsBlocker,
      {id: 'ask-ana', title: 'Approve the launch post?', status: 'open', owner_id: 'human:ana', direction: 'in'}]}}]) {
      Object.assign(brief, {relations: {blocks: [opsBlocker]}, waiting_on: null}, held);
      await page.evaluate(() => tasksLoad(TASKS_ST));
      await page.waitForFunction(() => document.querySelector('#task-body [data-board-blocked="tbrief"] span')?.innerText === 'Blocked by Plan the launch');
    }
    Object.assign(brief, {status: 'review', note: '', waiting_on: null, relations: {blocks: [opsBlocker]}});
    await page.evaluate(() => tasksLoad(TASKS_ST));
    await page.waitForFunction(() => document.querySelector('#task-body [data-board-blocked="tbrief"]')?.innerText === 'Blocked by Plan the launch');
    await drag('tbrief', '#task-body [data-task-key="tplan"]', 0.1);
    await page.waitForFunction(() => TASKS_ST.tasks.find(t => t.id === 'brief').version === 2);
    assert.deepEqual(posted.at(-1), {path: '/api/v2/tasks/brief', body: {version: 1, step_rank: 0}}, 'above the top card: its rank less one');
    assert.deepEqual(await titlesIn('legal'), ['Brief the agency', 'Plan the launch']);
    // A move rebuilds only the card that moved: the others keep their nodes.
    await page.evaluate(() => { window.keptCard = document.querySelector('#task-body [data-task-key="tbrief"]'); });
    await drag('ttask-0', '#task-body [data-col="copy"]');
    assert.deepEqual(posted.at(-1).body, {version: 1, step: 'copy', step_rank: 1}, 'an empty step starts at 1');
    await page.waitForFunction(() => TASKS_ST.tasks.find(t => t.id === 'task-0').version === 2);
    assert.deepEqual(await titlesIn('copy'), ['Write the campaign']);
    assert.equal(await page.locator('#task-body [data-col="draft"] .bcard').count(), 0);
    assert.equal(await page.evaluate(() => window.keptCard === document.querySelector('#task-body [data-task-key="tbrief"]')), true, 'an unmoved card is not redrawn');
    // The face on a card picks its owner, saved from the card without opening it.
    await page.locator('#task-body [data-task-key="tbrief"] [data-card-owner]').click();
    await page.locator('#task-card-pops .prop-pop .tl-mi', {hasText: 'Ana'}).click();
    await page.waitForFunction(() => document.querySelector('#task-body [data-task-key="tbrief"] [data-card-owner]')?.title === 'You');
    assert.deepEqual(posted.at(-1), {path: '/api/v2/tasks/brief', body: {version: 2, owner_id: 'human:ana'}});
    assert.equal(await page.evaluate(() => TASKS_ST.peek), '', 'picking an owner opens nothing');
    await page.locator('#task-body [data-task-key="tbrief"] [data-card-owner]').click();
    await page.locator('#task-card-pops .prop-pop .tl-mi', {hasText: 'Ops'}).click();
    await page.waitForFunction(() => document.querySelector('#task-body [data-task-key="tbrief"] [data-card-owner]')?.title === 'Ops');
    refuseMove = true;
    await drag('tplan', '#task-body [data-col="copy"] .bcol-body', 0.9);
    // The refusal reads on the card it was tried on, not in a toast.
    await page.locator('#task-body [data-task-key="tplan"] [data-task-refused]', {hasText: 'Not moved'}).waitFor();
    assert.deepEqual(posted.at(-1).body, {version: 1, step: 'copy', step_rank: 2});
    await page.waitForFunction(() => [...document.querySelectorAll('#task-body [data-col="legal"] .bcard-title')].map(n => n.textContent).join() === 'Brief the agency,Plan the launch');
    await page.locator('#task-body [data-task-key="tplan"] .bcard-title').click();
    await page.waitForFunction(() => TASKS_ST.peek === 'tplan' && document.querySelector('#task-peek')?.open);
    await peek.locator('[data-modal-close]').click();
    // The folds: a lone backlog step folds by hand, the finished strip unfolds, and both are remembered.
    await page.locator('#task-body [data-board-fold="backlog"]').click();
    await page.locator('#task-body [data-board-unfold="backlog"]').waitFor();
    await page.locator('#task-body [data-board-unfold="finished"]').click();
    await page.locator('#task-body [data-col="archive"]').waitFor();
    assert.deepEqual(JSON.parse(await page.evaluate(() => localStorage.getItem('tico.board.fold'))).marketing, {backlog: true, finished: false});
    await page.locator('#task-body [data-board-fold="finished"]').click();
    await page.locator('#task-body [data-board-unfold="backlog"]').click();
    await page.locator('#task-body [data-board-unfold="finished"]').waitFor();
    // Q: only my cards (none here), Q again: everyone's.
    await page.keyboard.press('q');
    await page.waitForFunction(() => location.hash.includes('owner=me') && !document.querySelector('#task-body .bcard'));
    await page.keyboard.press('q');
    await page.waitForFunction(() => document.querySelectorAll('#task-body .bcard').length === 4);
    // Select all in this column: a step column is one server filter, and the bulk menu sends it once; a task the
    // server skips stays selected and named, the rest change.
    await page.locator('#task-body [data-col="legal"] [data-board-select]').click();
    assert.equal(await page.locator('.tl-bulk-n').innerText(), 'All 2 in Legal review');
    assert.deepEqual(await page.locator('#task-body .bcard.sel').evaluateAll(cards => cards.map(c => c.dataset.taskKey).sort()), ['tbrief', 'tplan']);
    await page.locator('#task-bulk [data-bulk="close"]').click();
    await page.waitForFunction(() => /1 closed, 1 not changed: Brief the agency/.test(document.querySelector('.tl-bulk-msg')?.textContent || ''));
    assert.deepEqual(posted.filter(item => item.path === '/api/v2/task-bulk').map(item => item.body), [{action: 'close',
      filter: {type: 'marketing', step: 'legal', status: 'open,doing,waiting,review,ready', lane: 'company'}}]);
    assert.equal(posted.filter(item => /\/api\/v2\/tasks\/(plan|brief)$/.test(item.path) && item.body.close).length, 0, 'not one call per task');
    assert.deepEqual(await page.locator('#task-body .bcard.sel').evaluateAll(cards => cards.map(c => c.dataset.taskKey)), ['tbrief']);
    await page.locator('#task-bulk [data-bulk="clear"]').click();
    Object.assign(tasks.find(task => task.id === 'plan'), {status: 'review'});
    await page.evaluate(() => tasksLoad(TASKS_ST));
    await page.waitForFunction(() => document.querySelectorAll('#task-body .bcard').length === 4 && document.querySelector('#task-bulk').hidden);
    await page.locator('[data-task-type="general"]').click();
    await page.waitForFunction(() => location.hash.includes('type=general'));
    assert.equal(await page.locator('#task-body .bcol').count(), 5, 'General has its own working columns and a finished strip');
    assert.equal(await page.locator('#task-body .bcard').count(), 2, 'General never includes Marketing tasks');
    await page.locator('#task-new').click();
    await page.locator('#task-create select[name=type]').waitFor();
    await page.locator('#task-create [data-modal-close]').click();
    await page.locator('#task-new').click();
    await page.locator('#task-create select[name=type]').waitFor();
    assert.equal(await page.locator('#task-create select[name=type]').count(), 1);
    const assigned = await page.locator('#task-create select[name=owner] option[value="pr-reviewer-sol"]').textContent();
    assert.match(assigned, /GPT 6\.1 Sol \(OpenAI\).*Claude Opus 5\.5 \(Anthropic\)/,
      'choosing a checker shows configured and latest runtime families beside its name');
    await page.locator('#task-create [data-modal-close]').click();
    types[1].steps.push({id: 'verified', name: 'Verified', status: 'done', position: 5, type_id: 'marketing'},
      {id: 'filed', name: 'Filed', status: 'closed', position: 6, type_id: 'marketing'});
    const decision = {...tasks[2], id: 'decision', owner_id: 'human:ana', requester_id: 'bot:ops'};
    tasks.push(decision);
    // A task opened uses the types already read; the page's next load reads the edited ones.
    await page.evaluate(async task => { await tasksLoad(TASKS_ST); taskModalShow(task); }, decision);
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
    // Blocked is a real engineering column, before the active build, on desktop and phone.
    const dev = types.find(type => type.id === 'dev-tickets');
    dev.steps = [
      {id: 'dev-deck', name: 'On Deck', status: 'open'},
      {id: 'dev-blocked', name: 'Blocked', status: 'waiting'},
      {id: 'dev-building', name: 'In Progress', status: 'doing'},
      {id: 'dev-review', name: 'Review 1', status: 'review'},
    ].map((step, position) => ({...step, position, type_id: dev.id}));
    const held = {...tasks[0], id: 'dev-held', title: 'Build the export', type_id: dev.id,
      type: {id: dev.id, name: dev.name}, step_id: 'dev-blocked', step: dev.steps[1], status: 'waiting',
      holds: [{kind: 'blocked', reason: 'Waiting for a test machine <script>', since: '2026-10-02T10:00:00Z', until: null, by: 'human:ana', cause: 'manual'}]};
    tasks.push(held);
    for (const width of [1200, 375]) {
      await page.setViewportSize({width, height: 900});
      await page.evaluate(() => { location.hash = '#/tasks?view=board&type=dev-tickets'; });
      await page.waitForFunction(() => TASKS_ST?.type === 'dev-tickets' && document.querySelector('[data-task-key="tdev-held"]'));
      assert.deepEqual(await page.locator('#task-body .bcol h2').allTextContents(), ['On Deck', 'Blocked', 'In Progress', 'Review 1']);
      assert.equal(await page.locator('[data-col="dev-blocked"] [data-task-key="tdev-held"]').count(), 1);
      assert.equal(await page.locator('[data-col="dev-building"] .bcard').count(), 0);
      assert.equal(await page.locator('[data-col="dev-blocked"] [data-board-select]').isVisible(), true, 'select all in a column at every width');
      assert.equal(await page.locator('[data-col="dev-building"] [data-board-select]').count(), 0, 'an empty column has nothing to select');
      assert.match(await page.locator('[data-task-key="tdev-held"] .bcard-blocked').innerText(), /Waiting for a test machine <script>.*since/);
      assert.equal(await page.locator('[data-task-key="tdev-held"] script').count(), 0);
      await page.locator('[data-open-task="tdev-held"]').click();
      await page.locator('#task-peek .task-wait').waitFor();
      assert.match(await page.locator('#task-peek .task-wait').innerText(), /Waiting for a test machine <script>.*since/);
      assert.equal(await page.locator('#task-peek .tstatus').innerText(), 'Blocked');
      await page.locator('#task-peek [data-modal-close]').click();
      // The phone peek closes its history entry asynchronously before the next board action.
      if (width === 375) await page.waitForFunction(() => !history.state?.taskPeek && !PEEK_BACK);
    }
    await page.setViewportSize({width: 1200, height: 900});
    // A phone width clamps the sidebar; rails.js widens it again in the frame after the resize, which moves the board.
    // Measure cards for the drag only once the sidebar has the width this window gives it.
    await page.waitForFunction(() => innerWidth === 1200 && !matchMedia(PHONE).matches &&
      getComputedStyle(document.documentElement).getPropertyValue('--rail-left').trim() ===
        Math.round(Math.max(RAILS.left.min, Math.min(railsWant('left'), railsMax('left')))) + 'px');
    // Owner reason entry is cancellable and sends the reason with the step move.
    Object.assign(held, {step_id: 'dev-building', step: dev.steps[2], status: 'doing', holds: []});
    await page.evaluate(task => {
      TASKS_ST.tasks = TASKS_ST.tasks.map(t => t.id === task.id ? task : t);
      tasksRender(TASKS_ST);
      document.activeElement?.blur();
    }, held);
    await drag('tdev-held', '#task-body [data-col="dev-blocked"]');
    await page.locator('.task-block-reason [data-cancel]').click();
    assert.equal(await page.evaluate(() => document.activeElement?.dataset.openTask), 'tdev-held',
      'cancelling a dragged Blocked move restores focus to its card');
    await page.evaluate(task => taskModalShow(task), held);
    await page.waitForFunction(() => document.querySelector('#task-modal [data-prop="step"]')?.textContent.includes('In Progress'));
    await page.locator('#task-modal [data-prop="step"]').click();
    await page.locator('#task-modal [data-prop-pick="dev-blocked"]').click();
    await page.locator('.task-block-reason [data-cancel]').click();
    await page.locator('.task-block-reason').waitFor({state: 'detached'});
    assert.equal(await page.locator('.task-block-reason').count(), 0);
    await page.locator('#task-modal [data-prop="step"]').click();
    await page.locator('#task-modal [data-prop-pick="dev-blocked"]').click();
    await page.locator('.task-block-reason textarea').fill('Waiting for Ana to bring the machine');
    await page.locator('.task-block-reason button[type="submit"]').click();
    await page.waitForFunction(() => document.querySelector('#task-modal [data-prop="step"]')?.textContent.includes('Blocked'));
    assert(posted.some(item => item.path.endsWith('/dev-held') && item.body.step === 'dev-blocked' && item.body.note === 'Waiting for Ana to bring the machine'));
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
    assert.deepEqual(await page.locator('.task-type-editor [data-steps] .task-step-edit').first().locator('select option').evaluateAll(o => o.map(x => x.value)),
      ['open', 'doing', 'waiting', 'review', 'ready', 'done', 'closed'], 'a step may be waiting, never declined');
    await page.locator('.task-type-editor input[aria-label="Type name"]').fill('Campaigns');
    await page.locator('.task-type-editor [data-steps] .task-step-edit').nth(1).locator('[data-up]').click();
    // A step's check pool and each checker's limit of open checks (backend/check_pools.py).
    const firstStep = page.locator('.task-type-editor [data-steps] .task-step-edit').first();
    await firstStep.locator('[aria-label="Check pool"]').fill('rev-a, rev-b,');
    await page.getByText('Reviewer xAI: Claude Haiku 5.5 (Anthropic)', {exact: false}).waitFor();
    await page.getByText('Reviewer GPT: GPT 6.1 Sol (OpenAI) · latest run: Claude Opus 5.5 (Anthropic); next run may differ',
      {exact: true}).waitFor();
    await firstStep.locator('[aria-label="Open checks each"]').fill('3');
    assert.equal(await page.locator('.task-type-editor [name=typeBots]').inputValue(), 'parties', 'a type starts with its bots kept to their own tasks');
    await page.locator('.task-type-editor [name=typeBots]').selectOption('work');
    // Another model company is a check step's rule: it starts at prefer, shows on no other step, fits a phone, and a
    // changed rule is saved as the step's flow (its step references as ids).
    assert.equal(await firstStep.locator('[aria-label="Another model company"]').inputValue(), 'prefer', 'a check step starts at prefer');
    assert.equal(await page.locator('.task-type-editor [data-steps] .task-step-edit').nth(1).locator('[aria-label="Another model company"]').count(), 0,
      'a step that is no check has no such rule');
    await page.setViewportSize({width: 390, height: 844});
    const rule = await firstStep.locator('[aria-label="Another model company"]').boundingBox();
    assert(rule && rule.x >= 0 && rule.x + rule.width <= 390, 'the rule fits a phone-width editor');
    await page.setViewportSize({width: 1200, height: 900});
    await firstStep.locator('[aria-label="Another model company"]').selectOption('require');
    // A type private by default (the Product roadmap) shows each step's public name too, and a new step left blank
    // is not given its internal name publicly.
    assert.equal(await page.locator('.task-type-editor [name=defaultVisibility]').inputValue(), 'private');
    assert.equal(await page.locator('.task-type-editor [data-steps] .task-step-edit').first().locator('[aria-label="Public step name"]').isVisible(), true,
      'the public step name shows on a private-by-default type');
    await page.locator('.task-type-editor [data-add-step]').click();
    await page.locator('.task-type-editor [data-steps] .task-step-edit').last().locator('[aria-label="Step name"]').fill('Internal triage');
    await page.locator('.task-type-editor [data-steps] .task-step-edit').first().locator('[aria-label="Public step name"]').fill('Under review');
    await page.locator('.task-type-editor button[type=submit]').click();
    await page.getByText('Campaigns', {exact: true}).waitFor();
    const edit = posted.find(item => item.path === '/api/v2/task-types/marketing');
    assert.equal(edit.body.steps[0].id, 'legal', 'editing keeps ids and order');
    assert.equal(edit.body.steps[0].public_name, 'Under review', 'a public name typed is saved');
    const added = edit.body.steps.find(step => step.name === 'Internal triage');
    assert.equal('public_name' in added, false, 'a new step left blank does not send its internal name as its public one');
    assert.equal(edit.body.steps.find(step => step.id === 'draft').public_name, '', 'an existing step left blank stays off public pages');
    assert.equal(edit.body.bots, 'work', 'the editor saves what bots may do with the type');
    assert.deepEqual([edit.body.steps[0].check_pool, edit.body.steps[0].check_limit], [['rev-a', 'rev-b'], 3], 'a step saves its check pool and limit');
    assert.deepEqual([edit.body.steps[1].check_pool, edit.body.steps[1].check_limit], [[], 2], 'a step without one saves none');
    assert.deepEqual(edit.body.steps[0].flow, {check: 'review', on_pass: 'complete', on_fail: 'draft', other_company: 'require'},
      'a changed rule saves the check step\'s whole flow, its step references as ids');
    assert.equal('flow' in edit.body.steps[1], false, 'a step that is no check sends no flow');
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
