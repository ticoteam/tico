// Run with Playwright available: NODE_PATH=/path/to/node_modules node ui/tests/task-board.cjs
// Every request is intercepted; this test never contacts the hub, Slack, or a bot.
// Labels and PR links, a comment thread
// with the author on every line (not a chat), and mover-only controls. The list itself (rows, groups, chips, the peek,
// bulk changes, the keys) is ui/tests/tasks-page.cjs.
// The Product lane is retired: no lane switch, no product board, and the page
// only asks the hub for company tasks (the product rows below must never show).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();   // load.cjs arrives with #254
const screenshotDir = process.env.TICO_SCREENSHOT_DIR;
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1200, height: 900}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const flowAsked = [];
    const bots = [['coo', 'COO', 'leadership'], ['cpo', 'AI CPO', 'product'], ['cmo', 'AI CMO', 'marketing'],
      ['cto', 'CTO', 'engineering']].map(([name, display_name, team]) =>
      ({name, display_name, host: 'keeper', status: 'active', can_chat: true, team,
        runtime: {cpo: 'codex', cmo: 'claude'}[name], ...{
          cpo: {resolved_model: 'gpt-6.1-sol', resolved_effort: 'high', model_source: 'bot'},
          cmo: {resolved_model: 'claude-opus-5-5', resolved_effort: '', model_source: 'company'}}[name]}));
    const now = new Date().toISOString();
    const task = (id, over) => ({id, title: id, body: 'Details.', owner: 'bot:cmo', requester: 'human:reviewer', status: 'open',
      lane: 'company', rank: 1, labels: [], links: [], parts: {total: 0, done: 0}, version: 3, created_at: now, updated_at: now, ...over});
    let me = {id: 'reviewer', name: 'Test Reviewer', email: 'reviewer@example.test', role: 'owner', mover: true, cloud: true};
    const tags = [
      {id: 'tag-newsletter', key: 'newsletter', label: 'release', metadata: {date: '2026-10-02'},
        markdown: '# Release\n\n- [ ] Smoke checks\n- [x] Tell the team\n\n```md\n- [ ] Literal example\n```\n\n<script>window.tagUnsafe = true</script>',
        is_template: false, template_id: null, owner: 'human:reviewer', version: 1},
      {id: 'tag-template', key: 'release-checklist', label: 'release', metadata: {channel: 'stable'},
        markdown: '- [ ] Migrations / scripts to run', is_template: true, template_id: null, owner: 'human:reviewer', version: 1},
    ];
    let staleTag = false, raceStarter = false;
    let tasks = [
      task('Draft the newsletter', {rank: 2, labels: ['newsletter'], tags: [tags[0]], body: 'Review [packet](http://tico-ui.test/api/v2/files/doc).',
        attachments: [{id: 'doc', name: 'review-packet.md'}, {id: 'clip', name: 'first-cut.mp4'}, {id: 'archive', name: 'source.zip'}]}),
      task('Approve the budget', {owner: 'human:reviewer', requester: 'bot:coo', rank: 1, labels: ['finance'], note: 'Waiting on finance approval'}),
      task('Write the copy', {rank: 1, labels: ['newsletter', 'copy'], relations: {
        related: [{id: 'Draft the newsletter', title: 'Draft the newsletter', status: 'open', owner: 'bot:cmo', direction: 'both'}],
        duplicate_of: [{id: 'Fix the checkout bug', title: 'Fix the checkout bug', status: 'open', owner: 'bot:cpo', direction: 'in'}]}}),
      task('Ship the pricing page', {owner: 'bot:cpo', lane: 'product', status: 'review', rank: 1,
        links: [{id: 'l1', kind: 'pr', url: 'https://github.com/ticoteam/tico/pull/412', title: 'tico#412', state: 'open'}], parts: {total: 2, done: 1}}),
      task('Fix the checkout bug', {owner: 'bot:cpo', lane: 'product', status: 'open', rank: 2, labels: ['bug']}),
    ];
    const comments = [{id: 'm1', kind: 'say', from_actor: 'human:ben', to_actor: 'bot:cmo', body: 'Use the September numbers.', created_at: now, edited_at: now, refs: {task: 'Draft the newsletter', comment: true, attachments: [{id: 'doc', name: 'review-packet.md'}]}}];
    const events = [{id: 'e1', task_id: 'Draft the newsletter', ts: now, actor: 'human:reviewer', field: 'status', old: null, new: 'open', note: ''},
                    {id: 'e2', task_id: 'Draft the newsletter', ts: now, actor: 'bot:cmo', field: 'status', old: 'open', new: 'doing', note: 'Drafting from the brief'},
                    {id: 'e3', task_id: 'Draft the newsletter', ts: now, actor: 'bot:cmo', field: 'note', old: '', new: 'Drafting from the brief', note: 'Drafting from the brief'},
                    {id: 'e4', task_id: 'Draft the newsletter', ts: now, actor: 'bot:cmo', field: 'note', old: '', new: 'Waiting on the logo', note: ''},
                    {id: 'e5', task_id: 'Draft the newsletter', ts: now, actor: 'human:ben', field: 'comment', old: 'm1', new: 'm1', note: ''},
                    {id: 'e6', task_id: 'Draft the newsletter', ts: now, actor: 'human:ben', field: 'comment', old: 'm0', new: null, note: ''}];
    const posted = [];
    const people = [];
    let preferencePending = true, tasksStartedBeforePreference = false;
    let releaseInitialTasks, signalInitialTasksRequest;
    const initialTasksRequest = new Promise(resolve => { signalInitialTasksRequest = resolve; });
    const initialTasksGate = new Promise(resolve => { releaseInitialTasks = resolve; });
    let holdInitialTasks = !process.env.TICO_TAG_TEST_ONLY;
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui) { const file = uiFile(ui[1]); if (fs.existsSync(file)) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')}); }
      if (p === '/vendor/marked.min.js') return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
      if (p === '/api/employees') return json(bots);
      if (p === '/api/issues') return json([]);
      if (p === '/api/me') return json(me);
      if (p === '/api/status') return json({active: [], employees: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/humans') return json({people});
      if (p === '/api/v2/tasks/flow') {
        flowAsked.push(Object.fromEntries(url.searchParams));
        const day = back => { const d = new Date(); d.setDate(d.getDate() - back);
          return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
        const days = Array.from({length: 20}, (_, i) => ({day: day(i), created_at: 3 + (i % 5), doing: 2 + (i % 3), done: 1 + (i * 7 % 6), closed: i % 4}));
        const totals = {}; for (const d of days) for (const [k, v] of Object.entries(d)) if (k !== 'day') totals[k] = (totals[k] || 0) + v;
        return json({since: now, days, totals, steps: [], stages: {open: {n: 40, median_hours: 0.4, p90_hours: 9}, doing: {n: 38, median_hours: 2.5, p90_hours: 30},
          done: {n: 20, median_hours: 52, p90_hours: 80}}, people: [{actor: 'bot:cmo', created_at: 30, done: 22, closed: 10}, {actor: 'human:reviewer', created_at: 8, done: 3}]});
      }
      if (p === '/api/v2/tasks/labels') return json({labels: ['newsletter', 'copy', 'bug', 'finance'], tags: [tags[0]]});
      if (p === '/api/v2/tags' && req.method() === 'GET') return json({tags});
      if (p === '/api/v2/tags' && req.method() === 'POST') {
        const body = req.postDataJSON(); posted.push({path: p, body});
        const tag = {...body, id: 'tag-starter', metadata: {}, owner: 'human:reviewer', version: 1};
        tags.push(tag);
        if (raceStarter) {
          Object.assign(tag, {is_template: false, label: 'Existing release', markdown: '- [x] Keep our notes'});
          raceStarter = false;
          return json({error: {code: 'duplicate', detail: 'Tag already exists'}}, 422);
        }
        return json({tag});
      }
      const tagRoute = p.match(/^\/api\/v2\/tags\/([^/]+)(\/instances)?$/);
      if (tagRoute) {
        const tag = tags.find(tag => tag.id === decodeURIComponent(tagRoute[1]) || tag.key === decodeURIComponent(tagRoute[1]));
        if (!tag) return json({error: {detail: 'Tag not found'}}, 404);
        if (req.method() === 'GET') return json({tag, editable: me.mover,
          tasks: tasks.filter(task => task.labels.includes(tag.key)), next_offset: null});
        const body = req.postDataJSON(); posted.push({path: p, body});
        if (tagRoute[2]) {
          const instance = {...tag, ...body, id: 'tag-instance', metadata: {...tag.metadata, ...body.metadata},
            is_template: false, template_id: tag.id, version: 1};
          tags.push(instance); return json({tag: instance});
        }
        if (staleTag || body.version !== tag.version) {
          if (staleTag) tag.version += 1;
          staleTag = false; return json({error: {code: 'version_conflict', detail: 'Tag changed; fetch it and retry your update'}}, 409);
        }
        Object.assign(tag, body, {version: tag.version + 1}); return json({tag});
      }
      if (p.startsWith('/api/v2/files/')) {
        const id = p.split('/').at(-1);
        const file = {doc: {name: 'review-packet.md', body: '# Script one\n\nApprove the storyboard. <script>window.previewUnsafe = true</script>'},
          clip: {name: 'first-cut.mp4', body: 'not a real video'}, archive: {name: 'source.zip', body: 'not a real zip'}}[id];
        if (!file) return json({}, 404);
        return route.fulfill({contentType: 'application/octet-stream', headers: {'Content-Disposition': `attachment; filename*=UTF-8''${encodeURIComponent(file.name)}`}, body: file.body});
      }
      if (p.startsWith('/api/v2/preferences/') && req.method() === 'GET') {
        // the saved preference answers only once the first task list was asked for (bounded, so a regression fails)
        if (!process.env.TICO_TAG_TEST_ONLY) await Promise.race([initialTasksRequest, new Promise(resolve => setTimeout(resolve, t(10000)))]);
        preferencePending = false;
        return json({key: 'tasks.view', value: null});
      }
      if (p === '/api/v2/tasks' && req.method() === 'GET') {
        if (preferencePending) tasksStartedBeforePreference = true;
        if (holdInitialTasks) {
          holdInitialTasks = false;
          signalInitialTasksRequest();
          await initialTasksGate;
        }
        await new Promise(resolve => setTimeout(resolve, t(50)));
        let rows = tasks.slice();
        const statuses = url.searchParams.get('status');
        if (statuses && statuses !== 'all') {
          const wanted = new Set(statuses.split(','));
          rows = rows.filter(task => wanted.has(task.status));
        }
        const lane = url.searchParams.get('lane');
        if (lane) rows = rows.filter(task => (task.lane || 'company') === lane);
        const owner = url.searchParams.get('owner');
        if (owner) rows = rows.filter(task => task.owner === owner || task.owner === 'bot:' + owner);
        if (url.searchParams.get('sort') === 'finished') rows.sort((a, b) => String(b.closed_at || b.updated_at).localeCompare(String(a.closed_at || a.updated_at)));
        const offset = Number(url.searchParams.get('offset') || 0), limit = Number(url.searchParams.get('limit') || 500);
        return json({tasks: rows.slice(offset, offset + limit), next_offset: rows.length > offset + limit ? offset + limit : null});
      }
      const one = p.match(/^\/api\/v2\/tasks\/([^/]+)$/);
      if (one && req.method() === 'GET') {
        const t = tasks.find(x => x.id === decodeURIComponent(one[1]));
        return json({task: t, events: events.filter(e => e.task_id === t.id), comments: comments.filter(c => c.refs.task === t.id), children: [], parent: null, mover: me.mover, messages: []});
      }
      if (one && req.method() === 'POST') {
        const body = req.postDataJSON(); posted.push({path: p, body});
        const t = tasks.find(x => x.id === decodeURIComponent(one[1]));
        Object.assign(t, body, {version: t.version + 1}); delete t.close;
        return json({task: t});
      }
      const cm = p.match(/^\/api\/v2\/tasks\/([^/]+)\/comments$/);
      if (cm && req.method() === 'POST') {
        const body = req.postDataJSON(); posted.push({path: p, body});
        comments.push({id: 'm' + comments.length, kind: 'say', from_actor: 'human:reviewer', to_actor: 'bot:cmo', body: body.text, created_at: new Date().toISOString(), refs: {task: decodeURIComponent(cm[1]), comment: true}});
        return json({comment: comments.at(-1), comments, woke: true});
      }
      if (p === '/api/v2/conversations') return json({conversations: []});
      if (p.endsWith('/messages')) return json({messages: []});
      return json(unrouted(p));
    });
    if (!process.env.TICO_TAG_TEST_ONLY) {
    await page.goto('http://tico-ui.test/#/tasks');
    await initialTasksRequest;
    try {
      await page.locator('#main .skel[aria-busy="true"]').waitFor();
    } finally {
      releaseInitialTasks();
    }
    await page.waitForFunction(() => S.me?.id === 'reviewer' && TASKS_ST && TASKS_ST.tasks.length === 3);
    assert.equal(tasksStartedBeforePreference, true, 'task loading starts before the saved preference returns');
    assert.equal(await page.evaluate(() => TASKS_ST.tasks.some(t => t.lane === 'product')), false, 'product tasks are never loaded');
    assert.equal(await page.locator('#task-lane, [data-lane]').count(), 0, 'no lane switch');
    const checkDesktopToolbar = async (width, oneLine = true) => {
      await page.setViewportSize({width, height: 900});
      const result = await page.evaluate(() => {
        const head = document.querySelector('.tl-head');
        const items = ['h1', '.tl-search', '#task-new', '#task-view'].map(s => head.querySelector(s).getBoundingClientRect());
        return {tops: items.map(r => Math.round(r.top)), right: Math.max(...items.map(r => r.right)), edge: head.getBoundingClientRect().right,
          search: items[1].width, overflow: head.scrollWidth > head.clientWidth + 1};
      });
      if (oneLine) assert(Math.max(...result.tops) - Math.min(...result.tops) <= 8, `${width}px toolbar wraps: ${JSON.stringify(result)}`);
      assert(result.right <= result.edge + 1 && !result.overflow, `${width}px toolbar overflows: ${JSON.stringify(result)}`);
      assert(result.search >= 60, `${width}px search is too narrow: ${JSON.stringify(result)}`);
    };
    await checkDesktopToolbar(1280);
    await checkDesktopToolbar(1000, false);
    await checkDesktopToolbar(768, false);    // a narrow window puts the tabs on their own line, never off the edge
    await page.setViewportSize({width: 1200, height: 900});
    // Labelled tabs (no icon-only buttons), the selected one marked for screen readers.
    assert.deepEqual(await page.locator('#task-view [role=tab]').evaluateAll(tabs => tabs.map(b => [b.firstChild.textContent, b.getAttribute('aria-selected')])),
      [['Needs you', 'true'], ['Open', 'false'], ['Board', 'false'], ['Done', 'false'], ['Closed', 'false']]);
    assert.equal(await page.locator('#task-view .nav-icon').count(), 0);
    await page.locator('#task-q').focus();
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'task-new');
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'task-who');
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'task-filter');
    await page.locator('#task-q').focus();
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.evaluate(() => document.activeElement.dataset.taskType), 'general', 'the type selector precedes search');
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.evaluate(() => document.activeElement.dataset.view), 'foryou', 'one tab stop for the view tabs');
    if (screenshotDir) await page.screenshot({path: path.join(screenshotDir, 'task-toolbar-desktop.png')});

    // List and Board are every open task again, by column; For you stays who needs me.
    await page.locator('#task-view [data-view="list"]').click();
    await page.waitForFunction(() => document.querySelectorAll('#task-body .tl-row').length === 3);
    assert.ok((await page.locator('#task-body .tl-gname').count()) > 0, 'grouped');
    await page.locator('#task-view [data-view="board"]').click();
    await page.waitForFunction(() => document.querySelectorAll('#task-body .bcol').length === 4 && document.querySelectorAll('#task-body .bcard').length === 3);
    if (screenshotDir) await page.screenshot({path: path.join(screenshotDir, 'task-board-kanban.png')});
    // Stats: status changes over time. A tile picks the charted stage; an owner row narrows to that owner.
    await page.locator('#task-stats').click();
    await page.locator('#task-stats-dialog .flow-chart .flow-bar').first().waitFor();
    assert.equal(await page.locator('#task-stats-dialog .flow-col').count(), 12, 'twelve weeks by default');
    assert.equal(await page.locator('#task-stats-dialog .flow-bar.so-far').count(), 1, 'the running week is marked');
    assert.ok(await page.locator('#task-stats-dialog .flow-avg').count(), 'a moving-average line');
    assert.match(await page.locator('#task-stats-dialog .flow-tile.cur').innerText(), /Done/);
    assert.ok(flowAsked.some(q => q.days === '60'), 'the previous period is read too');
    await page.locator('#task-stats-dialog [data-stage="created"]').click();
    assert.match(await page.locator('#task-stats-dialog .flow-chart-head').innerText(), /New per week/);
    await page.locator('#task-stats-dialog [data-unit="day"]').click();
    await page.waitForFunction(() => document.querySelectorAll('#task-stats-dialog .flow-col').length === 14);
    const personFlowRead = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/v2/tasks/flow' && url.searchParams.get('owner') === 'human:reviewer';
    });
    await page.locator('#task-stats-dialog select[data-flow-who]').selectOption('human:reviewer');
    await personFlowRead;
    assert.equal(flowAsked.at(-1).owner, 'human:reviewer', 'the chart reads one person');
    await page.locator('#task-stats-dialog [data-days="365"]').click();
    await page.waitForFunction(() => !document.querySelector('#task-stats-dialog .flow-tile-d'), null, {timeout: 5000});   // a year has no previous period
    await page.locator('#task-stats-dialog [data-days="7"]').click();
    await page.locator('#task-stats-dialog .flow-people tr[data-owner="cmo"]').click();
    await page.waitForFunction(() => document.querySelector('#task-stats-dialog select[data-flow="owner"]').value === 'cmo');
    assert.ok(flowAsked.some(q => q.owner === 'cmo' && q.days === '7'));
    if (screenshotDir) await page.screenshot({path: path.join(screenshotDir, 'task-stats.png')});
    await page.locator('#task-stats-dialog [data-close]').click();
    // A card with related tasks shows how many, like an attachment count; one without shows nothing.
    const relCounts = await page.locator('#task-body .bcard').evaluateAll(cs => cs.map(c => c.querySelector('.bcard-rel')?.getAttribute('aria-label') || ''));
    assert.deepEqual(relCounts.filter(Boolean), ['2 related tasks']);
    await page.locator('#task-body .bcard').first().click();
    await page.locator('#task-peek[open]').waitFor();
    assert.equal(await page.locator('#task-modal[open]').count(), 0, 'a card opens beside the board, not over it');
    await page.locator('#task-peek [data-modal-close]').click();
    // Needs you: the tasks that wait on me, grouped by what they take; grouped by who asks, a way into that bot's chat.
    await page.locator('#task-view [data-view="foryou"]').click();
    await page.locator('#task-body .tl-row').first().waitFor();
    assert.equal(await page.locator('#task-group .v').innerText(), 'What it takes');
    await page.locator('#task-group').click();
    await page.locator('[data-pick-group="requester"]').click();
    await page.locator('#task-body .tl-ghead a.tl-gchat').waitFor();
    assert.equal(await page.locator('#task-body .tl-row').count(), 1);
    assert.match(await page.locator('#task-body .tl-gname').innerText(), /Assistant/i);
    assert.match(await page.locator('#task-body .tl-row').innerText(), /Approve the budget/);
    assert.equal(await page.locator('#task-body .tl-ghead a.tl-gchat').getAttribute('href'), '#/bot/coo');
    assert.doesNotMatch(await page.locator('#task-body').innerText(), /Open chat|bots and people|bot or person/);
    if (screenshotDir) { fs.mkdirSync(screenshotDir, {recursive: true}); await page.screenshot({path: path.join(screenshotDir, 'task-board-company.png')}); }

    // List filters, search, Done and recurring controls are exercised in tasks-page.cjs.

    // The modal: comments with authors, state changes inline, one box; the mover controls are there
    await page.evaluate(() => taskModalShow(TASKS_ST.tasks.find(t => t.id === 'Draft the newsletter')));
    await page.locator('#task-modal .task-comments .tcomment', {hasText: 'Use the September numbers.'}).waitFor();
    assert.match(await page.locator('#task-modal .task-comments').innerText(), /ben/i, 'the author is on the comment');
    assert.match(await page.locator('#task-modal .task-comments').innerText(), /moved it to Doing/);
    const thread = await page.locator('#task-modal .task-comments').innerText();
    assert.equal(thread.match(/Drafting from the brief/g).length, 1, 'a note saved with a status change is shown once');
    assert.match(thread, /noted: Waiting on the logo/, 'a note on its own shows its text');
    assert.doesNotMatch(thread, /left a note/);
    // An edited comment says so beside its time; a deleted one leaves only a line in the history.
    assert.equal(await page.locator('#task-modal .tcomment', {hasText: 'Use the September numbers.'}).locator('[title^="Edited"]').count(), 1);
    assert.match(await page.locator('#task-modal .task-comments').innerText(), /deleted a comment/);
    assert.doesNotMatch(await page.locator('#task-modal .task-comments').innerText(), /changed comment|edited a comment/);
    assert.equal(await page.getByRole('heading', {name: 'Conversation'}).count(), 1);
    assert.equal(await page.locator('#task-modal button[data-prop="status"]').count(), 1, 'a mover can change the status');
    assert.equal(await page.locator('#task-modal select').count(), 0, 'properties, not a form of selects');
    assert.equal(await page.locator('#task-modal [data-modal-lane]').count(), 0, 'no lane control');
    // Files show as tiles (an older server: the attachments, each at v1); a tile opens its file in place.
    assert.deepEqual(await page.locator('#task-modal .tf-tile .tf-name').allTextContents(), ['review-packet.md', 'first-cut.mp4', 'source.zip']);
    await page.locator('#task-modal .tf-tile[data-tf-file="doc"]').click();
    await page.getByText(/Script one.*Approve the storyboard/s).waitFor();
    assert.match(await page.locator('#task-modal .tf-view').innerText(), /Script one.*Approve the storyboard/s);
    assert.equal(await page.locator('#task-modal .tf-view script').count(), 0, 'untrusted Markdown is sanitized');
    assert.equal(await page.locator('#task-modal .task-comments [data-tf-jump="doc"]').count(), 1, 'a comment\'s file links to its tile');
    if (screenshotDir) await page.screenshot({path: path.join(screenshotDir, 'task-attachment-preview.png')});
    await page.locator('#task-modal .tf-tile[data-tf-file="clip"]').click();
    await page.locator('#task-modal .tf-view video').waitFor();
    assert.equal(await page.locator('#task-modal .tf-view video').getAttribute('controls'), '');
    await page.locator('#task-modal .tf-tile[data-tf-file="archive"]').click();
    await page.locator('#task-modal .tf-view .tf-card a[download]').waitFor();
    await page.locator('#task-modal [data-tf-close]').click();
    assert.equal(await page.locator('#task-modal .tf-view').isHidden(), true);
    await page.evaluate(() => {
      const link = document.createElement('a');
      link.href = '/api/v2/files/doc'; link.textContent = 'review-packet.md';
      document.querySelector('#task-modal .tdesc').appendChild(link);
    });
    await page.locator('#task-modal .tdesc a[href="/api/v2/files/doc"]').click();
    await page.locator('#task-modal .tf-tile[data-tf-file="doc"][aria-expanded="true"]').waitFor();
    await page.getByText(/Script one.*Approve the storyboard/s).waitFor();
    await page.locator('#task-modal').getByRole('textbox', {name: 'Message'}).fill('Add the promo line.');
    await page.locator('#task-modal').getByRole('button', {name: 'Send', exact: true}).click();
    await page.locator('#task-modal .task-comments .tcomment', {hasText: 'Add the promo line.'}).waitFor();
    assert.deepEqual(posted.at(-1), {path: '/api/v2/tasks/Draft%20the%20newsletter/comments', body: {text: 'Add the promo line.', public: false}});
    assert.equal(await page.locator('#task-modal').getByText(/Send to/).count(), 0, 'no chat framing');
    if (screenshotDir) await page.screenshot({path: path.join(screenshotDir, 'task-modal-comments.png')});
    // a tag added from the properties posts the new tag set with the version
    await page.locator('#task-modal [data-prop="tags"]').click();
    await page.keyboard.type('promo');
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('#task-modal [data-prop-row="tags"]')?.textContent.includes('promo'));
    await page.waitForFunction(() => window.__posted !== 'never');
    const labelPost = posted.find(x => x.body.labels);
    assert.deepEqual(labelPost.body, {version: 3, labels: ['newsletter', 'promo']});
    await page.locator('#task-modal [data-modal-close]').click();
    await page.waitForFunction(() => TASK_CHAT === null);

    // Finishing a request from a bot is one tap: nothing is asked. A comment typed but not sent goes with Done as the
    // result, and leaves the box.
    await page.evaluate(() => taskModalShow(TASKS_ST.tasks.find(t => t.id === 'Approve the budget')));
    await page.locator('#task-modal').getByRole('textbox', {name: 'Message'}).fill('Keep the budget on hold.');
    // Done is a status like any other: the Status menu, then Done.
    await page.locator('#task-modal [data-prop="status"]').click();
    await page.locator('#task-modal .prop-pop [data-prop-pick="done"]').click();
    await page.locator('#task-body [data-task-key="tApprove the budget"]').waitFor({state:'detached'});
    assert.equal(await page.locator('dialog.task-outcome').count(), 0, 'nothing asks what was decided');
    const resultPost = posted.find(x => x.path === '/api/v2/tasks/Approve%20the%20budget' && x.body.status === 'done');
    assert.equal(resultPost.body.note, 'Keep the budget on hold.');
    assert.equal(await page.evaluate(() => TASK_DRAFTS.has('Approve the budget')), false, 'the comment went with Done, not left to send again');
    assert.equal(posted.filter(x => x.path.endsWith('/comments') && x.body.text === 'Keep the budget on hold.').length, 0, 'not also sent as a comment');

    // Someone who may not move tasks (here the one who asked): no tag + and no Part of picker, but the comment box
    me = {...me, role: 'viewer', mover: false};
    const draftDetail = page.waitForResponse(r => new URL(r.url()).pathname === '/api/v2/tasks/Draft%20the%20newsletter');
    await page.evaluate(() => { S.me = {...S.me, mover: false}; taskModalShow(TASKS_ST.tasks.find(t => t.id === 'Draft the newsletter')); });
    await draftDetail;
    await page.waitForFunction(() => { const d = document.querySelector('#task-modal'); return d.open && d.dataset.task === 'Draft the newsletter'; });
    await page.locator('#task-modal .task-comments').waitFor();
    await page.locator('#task-modal [data-task-props]').waitFor();
    assert.equal(await page.locator('#task-modal [data-prop="tags"], #task-modal [data-prop="parent"]').count(), 0);
    assert.equal(await page.locator('#task-modal [data-prop-row="parent"] .prop-v.ro').count(), 1, 'shown, read-only');
    assert.equal(await page.locator('#task-modal').getByRole('textbox', {name: 'Message'}).count(), 1);
    await page.locator('#task-modal [data-modal-close]').click();
    // Reopened on the server, not through the page: a read already on its way shares its answer with a load that
    // joins it (api.js GET_INFLIGHT), so the change waits until no read is out, and the list waits until it is open.
    await page.waitForFunction(() => GET_INFLIGHT.size === 0);
    tasks.find(t => t.id === 'Approve the budget').status = 'open';
    await page.evaluate(() => tasksLoad(TASKS_ST));
    await page.waitForFunction(() => TASKS_ST.tasks.some(t => t.id === 'Approve the budget' && t.status === 'open'));

    await page.setViewportSize({width: 375, height: 844});
    // the sidebar's drawer transition has started and ended (two frames, then no finite animation running)
    await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
    await page.waitForFunction(() => document.getAnimations().every(a => a.playState !== 'running' || a.effect?.getComputedTiming().iterations === Infinity));
    await page.locator('#task-view [data-view="foryou"]').click();
    await page.locator('#task-body .tl').waitFor();
    const mobile = await page.evaluate(() => {
      const head = document.querySelector('.tl-head');
      const rect = s => head.querySelector(s).getBoundingClientRect();
      return {top: [rect('#task-type').top, rect('.tl-search').top], strip: rect('#task-view').top,
        right: rect('#task-new').right, edge: head.getBoundingClientRect().right};
    });
    assert(Math.max(...mobile.top) - Math.min(...mobile.top) <= 8, 'phone type selector and search share a line');
    assert(mobile.strip > mobile.top[0] && mobile.right <= mobile.edge + 1, 'phone view switch is on its own line');
    // An empty search is its icon at the row's end, so the type keeps its words; tapped, it takes the row; words keep it open.
    const searchRow = () => page.evaluate(() => {
      const r = s => document.querySelector(s).getBoundingClientRect();
      return {box: Math.round(r('.tl-search').width), row: Math.round(r('.tl-type-search').width), end: Math.round(r('.tl-type-search').right - r('.tl-search').right),
        cut: [...document.querySelectorAll('#task-type button')].filter(b => b.scrollWidth > b.clientWidth + 1).length};
    });
    let row = await searchRow();
    assert(row.box <= 34 && row.end <= 1 && row.cut === 0, `an empty search is an icon on a phone: ${JSON.stringify(row)}`);
    await page.locator('.tl-search').click();
    assert.equal(await page.evaluate(() => document.activeElement.id), 'task-q');
    row = await searchRow();
    assert(Math.abs(row.box - row.row) <= 1, `tapped, the search takes the row: ${JSON.stringify(row)}`);
    await page.locator('#task-q').fill('budget');
    await page.locator('.tl-head h1').click();
    row = await searchRow();
    assert(Math.abs(row.box - row.row) <= 1, 'a search with words stays open');
    await page.locator('#task-q').fill('');
    await page.locator('.tl-head h1').click();
    assert((await searchRow()).box <= 34, 'emptied, it is an icon again');
    // The filter bar scrolls sideways on a narrow phone: its edge fades while more is to the right, not at the end.
    await page.setViewportSize({width: 330, height: 844});
    await page.waitForFunction(() => document.querySelector('#task-bar').classList.contains('more'));
    await page.locator('#task-bar').evaluate(bar => { bar.scrollLeft = bar.scrollWidth; });
    await page.waitForFunction(() => !document.querySelector('#task-bar').classList.contains('more'));
    await page.locator('#task-bar').evaluate(bar => { bar.scrollLeft = 0; });
    await page.setViewportSize({width: 375, height: 844});
    await page.locator('#task-filter').click();
    await page.waitForFunction(() => document.querySelector('#task-filter-pop').matches(':popover-open'));
    assert.equal(await page.locator('#task-filter-pop').evaluate(el => Math.round(el.getBoundingClientRect().bottom)), 844, 'filter is a bottom sheet');
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#task-filter-pop').evaluate(el => el.matches(':popover-open')), false);
    await page.locator('#task-filter').click();
    await page.locator('.tl-head h1').click();
    assert.equal(await page.locator('#task-filter-pop').evaluate(el => el.matches(':popover-open')), false, 'outside tap closes the sheet');
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
      if (screenshotDir) await page.screenshot({path: path.join(screenshotDir, `task-toolbar-mobile-${theme}.png`)});
    }
    if (screenshotDir) await page.screenshot({path: path.join(screenshotDir, 'task-board-mobile.png')});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'no sideways scroll on a phone');
    for (const [route, view] of [['#/board', 'board'], ['#/issues', 'list']]) {
      await page.goto('http://tico-ui.test/' + route);
      const activeCount = tasks.filter(task => task.lane === 'company' && !['done', 'closed'].includes(task.status)).length;
      await page.waitForFunction(({view, activeCount}) => TASKS_ST?.view === view && TASKS_ST.tasks.length === activeCount,
        {view, activeCount});
      assert.equal(await page.locator(`#task-view [data-view="${view}"]`).getAttribute('aria-selected'), 'true');
    }
    // A view other than Needs you stays in the address, so a board link is stable; #/tasks/<id> opens the task.
    await page.goto('http://tico-ui.test/#/tasks?view=board');
    await page.waitForFunction(() => TASKS_ST?.view === 'board');
    await page.waitForFunction(() => location.hash.includes('type='));    // the type is written into the address
    assert.equal(new URL(page.url()).hash, '#/tasks?type=general&view=board');
    await page.locator('#task-view [data-view="list"]').click();
    await page.waitForFunction(() => location.hash === '#/tasks?type=general&view=list');
    await page.locator('#task-view [data-view="foryou"]').click();
    await page.waitForFunction(() => location.hash === '#/tasks?type=general&view=foryou');
    await page.goto('http://tico-ui.test/#/tasks/Approve%20the%20budget');
    await page.locator('#task-modal', {hasText: 'Approve the budget'}).waitFor();
    // Links copied into chat or email do not depend on clients preserving a URL fragment.
    await page.goto('http://tico-ui.test/?task=Approve%20the%20budget');
    await page.locator('#task-modal', {hasText: 'Approve the budget'}).waitFor();
    assert.equal(new URL(page.url()).hash, '#/task/Approve%20the%20budget');
    await page.goto('http://tico-ui.test/?bot=cmo&tab=tasks');
    await page.waitForFunction(() => location.hash === '#/bot/cmo/tasks');
    assert.equal(new URL(page.url()).search, '', 'the entry query is removed after routing');
    // The Slack "1 visual - view in Tico" line links to one message: the task opens at it, marked for a moment.
    await page.goto('http://tico-ui.test/?task=Draft%20the%20newsletter&message=m1');
    await page.locator('#task-modal .tcomment.jump-mark[data-message="m1"]').waitFor();
    assert.equal(new URL(page.url()).hash, '#/task/Draft%20the%20newsletter');
    assert.equal(new URL(page.url()).search, '', 'the message query is removed too');
    // A message that is not in the conversation says so rather than jumping somewhere else.
    await page.goto('http://tico-ui.test/?task=Draft%20the%20newsletter&message=gone');
    await page.locator('.toast', {hasText: 'That message is not in this conversation'}).waitFor();
    assert.equal(await page.locator('#task-modal .tcomment.jump-mark').count(), 0);
    // The org panel shows a bot's harness as a small mark, not a word; a bot with no harness shows none.
    // Hover and screen readers say harness, model and effort, nothing more; no effort, nothing after the model.
    const rt = name => page.locator(`#tree a.node[href="#/bot/${name}"] .rt`);
    assert.equal(await rt('cpo').getAttribute('aria-label'), 'Codex GPT 6.1 Sol High');
    assert.equal(await rt('cpo').getAttribute('title'), 'Codex GPT 6.1 Sol High');
    assert.equal(await rt('cmo').getAttribute('aria-label'), 'Claude Code Opus 5.5');
    assert.equal(await rt('cmo').getAttribute('title'), 'Claude Code Opus 5.5');
    assert.equal(await rt('cpo').locator('svg').count(), 1);
    assert.equal((await rt('cpo').innerText()).trim(), '', 'the mark has no text');
    assert.equal(await rt('cto').count(), 0, 'no harness, no mark');
    const mark = await rt('cpo').boundingBox();
    assert(mark.width >= 12 && mark.width <= 14, 'about 13px: ' + mark.width);
    }
    // Rich tag chips keep their keys while opening a shared checklist.
    page.setDefaultTimeout(t(5000));
    me = {...me, role: 'owner', mover: true};
    await page.goto('http://tico-ui.test/#/tasks');
    await page.locator('#task-view [data-view="list"]').click();
    await page.locator('#task-filter').click();
    await page.locator('#task-filter-pop [data-pick-field="tag"]').click();
    assert.equal(await page.locator('#task-filter-pop input[value="newsletter"]').locator('xpath=..').innerText(), 'release · Oct 2');
    assert.equal(await page.locator('#task-filter-pop .tl-menu').getAttribute('aria-label'), 'Tag');
    await page.keyboard.press('Escape');
    const richRow = page.locator('#task-body [data-task-key="tDraft the newsletter"]');
    assert.equal(await richRow.locator('.tlabel').first().innerText(), 'release · Oct 2');
    assert.equal(await richRow.locator('[data-tag-key]').count(), 0, 'rows have no nested links');
    await richRow.locator('.tl-title').click();
    const richChip = page.locator('#task-peek [data-tag-key="newsletter"]');
    await richChip.waitFor();
    assert.equal(await richChip.innerText(), 'release · Oct 2');
    assert(!(await page.locator('#task-peek .tlabels').innerText()).includes('||'), 'empty-tag fallback is rendered');
    await richChip.click();
    await page.locator('[data-tag-notes] input').first().waitFor();
    assert.equal(new URL(page.url()).hash, '#/tag/newsletter');
    assert.equal(await page.locator('#task-modal[open], #task-peek[open]').count(), 0, 'a chip opens its tag, not its task');
    assert.equal(await page.locator('[data-tag-notes] input').count(), 2, 'code examples are never tickable');
    assert.equal(await page.evaluate(() => window.tagUnsafe), undefined, 'tag Markdown is sanitized');
    assert.equal(await page.locator('[data-tag-tasks] a').count(), 2);
    await page.locator('[data-tag-notes] input').first().check();
    await page.waitForFunction(() => document.activeElement?.dataset.tagCheck === '0' && document.querySelector('[data-tag-notes] input')?.checked);
    const tick = posted.find(post => post.path === '/api/v2/tags/tag-newsletter');
    assert.equal(tick.body.version, 1);
    assert(tick.body.markdown.includes('- [x] Smoke checks'));
    assert(tick.body.markdown.includes('```md\n- [ ] Literal example\n```'));
    staleTag = true;
    await page.locator('[data-tag-notes] input').nth(1).click();
    await page.locator('[data-tag-reload]').waitFor();
    assert(tags[0].markdown.includes('- [x] Tell the team'), 'stale edits never overwrite saved notes');
    await page.locator('[data-tag-reload]').click();
    await page.locator('[data-tag-notes] input').first().waitFor();
    // The notes editor keeps the version it opened with, even after a checkbox saves.
    await page.locator('[data-tag-edit]').click();
    const editor = page.locator('[data-tag-editor]');
    await editor.locator('textarea[name="markdown"]').fill('Draft notes to keep');
    const editVersion = tags[0].version;
    await page.locator('[data-tag-notes] input').first().click();
    await page.waitForFunction(() => document.activeElement?.dataset.tagCheck === '0' && !document.querySelector('[data-tag-notes] input')?.disabled);
    staleTag = true;
    await page.locator('[data-tag-notes] input').nth(1).click();
    await page.locator('[data-tag-reload]').waitFor();
    await page.locator('[data-tag-reload]').click();
    await page.locator('[data-tag-notes] input').first().waitFor();
    assert.equal(await editor.locator('textarea[name="markdown"]').inputValue(), 'Draft notes to keep', 'checkbox conflict reload preserves an open draft');
    await editor.locator('button[type="submit"]').click();
    await editor.locator('[data-tag-refresh]').waitFor();
    assert.equal(posted.at(-1).body.version, editVersion, 'the editor uses its original version');
    assert.equal(await editor.locator('textarea[name="markdown"]').inputValue(), 'Draft notes to keep');
    await editor.locator('[data-tag-refresh]').click();
    await page.waitForFunction(() => document.querySelector('[data-tag-error]')?.textContent.includes('Current notes loaded'));
    assert.equal(await editor.locator('textarea[name="markdown"]').inputValue(), 'Draft notes to keep', 'loading current notes preserves the draft');
    await editor.locator('[data-tag-cancel]').click();
    me = {...me, mover: false};
    await page.reload();
    await page.locator('[data-tag-notes] input').first().waitFor();
    assert.equal(await page.locator('[data-tag-notes] input').first().isDisabled(), true);
    assert.equal(await page.locator('[data-tag-edit]').count(), 0);
    me = {...me, mover: true};
    await page.goto('http://tico-ui.test/#/settings');
    await page.locator('[data-settings-tab="tasks"]').click();
    const beforeStarter = posted.length;
    await page.locator('#settings-tags [data-release-starter]').click();
    await page.locator('.page-tags [data-tag-create]').waitFor();
    assert.equal(posted.length, beforeStarter, 'the starter opens the existing key without overwriting it');
    await page.locator('[data-tag-create] input[name="key"]').fill('release-2026-10-02');
    await page.locator('[data-tag-create] textarea[name="metadata"]').fill('{"date":"2026-10-02"}');
    assert.equal(await page.locator('[data-tag-create]').evaluate(form => typeof form.onsubmit === 'function' && form.checkValidity()), true);
    await page.locator('[data-tag-create] button[type="submit"]').click();
    await page.waitForURL('**/#/tag/release-2026-10-02').catch(async error => { console.error('Create error:', await page.locator('[data-tag-error]').innerText(), 'last post:', posted.at(-1), 'page errors:', errors); throw error; });
    await page.locator('[data-tag-notes] input').first().waitFor();
    assert.deepEqual(posted.at(-1), {path: '/api/v2/tags/tag-template/instances',
      body: {key: 'release-2026-10-02', metadata: {date: '2026-10-02'}}});
    assert.deepEqual(tags.at(-1).metadata, {channel: 'stable', date: '2026-10-02'});

    // A long name makes the same 40-character key the server saves, shown live under Advanced (C2).
    const newTag = async () => {
      await page.goto('http://tico-ui.test/#/settings/tasks');
      const form = page.locator('#settings-tags [data-tag-create]');
      await form.waitFor();
      await form.locator('.tag-advanced > summary').click();
      return form;
    };
    let form = await newTag();
    // The pane's refresh can land after Advanced is opened; it stays open.
    await page.evaluate(() => renderTags($('#settings-tags')));
    assert.equal(await form.locator('.tag-advanced').evaluate(d => d.open), true);
    assert.equal(await form.locator('input[name="key"]').getAttribute('maxlength'), '40');
    assert.equal(await form.locator('[data-tag-key-line]').innerText(), 'Key: made from the name');
    await form.locator('input[name="label"]').fill('Quarterly roadmap review for the platform team now');
    assert.equal(await form.locator('[data-tag-key-line]').innerText(), 'Key: quarterly-roadmap-review-for-the-platfor');
    await form.locator('button[type="submit"]').click();
    await page.waitForURL('**/#/tag/quarterly-roadmap-review-for-the-platfor');
    assert.equal(posted.at(-1).body.key, 'quarterly-roadmap-review-for-the-platfor');
    // A cut that lands on a word gap drops the hyphen, so the key is 39 characters and the server keeps it as posted.
    form = await newTag();
    await form.locator('input[name="label"]').fill('Notes on the customer interviews we ran in spring 2027');
    assert.equal(await form.locator('[data-tag-key-line]').innerText(), 'Key: notes-on-the-customer-interviews-we-ran');
    await form.locator('input[name="key"]').fill('  Spring Interviews ');
    assert.equal(await form.locator('[data-tag-key-line]').innerText(), 'Key: spring-interviews', 'a typed key shows as it will be saved');
    assert.equal(await page.evaluate(() => tagKeyTyped('a'.repeat(39) + ' b')), 'a'.repeat(39), 'a typed key cut at a gap ends without a hyphen');
    await form.locator('button[type="submit"]').click();
    await page.waitForURL('**/#/tag/spring-interviews');
    assert.equal(posted.at(-1).body.key, 'spring-interviews');
    for (const key of ['quarterly-roadmap-review-for-the-platfor', 'spring-interviews']) tags.splice(tags.findIndex(tag => tag.key === key), 1);

    // The starter creates only on click and handles a key another person creates while the list is open.
    tags.splice(tags.findIndex(tag => tag.key === 'release-checklist'), 1);
    const settingsTags = async () => {
      // The Tasks tab is remembered. Clicking it again starts a second tag-list render that
      // can replace the starter button between pointer down and pointer up.
      await page.goto('http://tico-ui.test/#/settings/tasks');
      await page.locator('#settings-tags [data-release-starter]').waitFor();
    };
    await settingsTags();
    assert.equal(tags.some(tag => tag.key === 'release-checklist'), false, 'opening Tags does not seed the starter');
    await page.locator('#settings-tags [data-release-starter]').click();
    await page.waitForURL('**/#/tag/release-checklist');
    await page.locator('[data-tag-notes] input').nth(3).waitFor();
    const starter = tags.find(tag => tag.key === 'release-checklist');
    assert.equal(starter.label, 'release');
    assert.equal(starter.is_template, true);
    assert.match(starter.markdown, /scripts and migrations[\s\S]*Deploy[\s\S]*smoke checks[\s\S]*Tell the team/);
    tags.splice(tags.indexOf(starter), 1);
    await settingsTags();
    raceStarter = true;
    await page.locator('#settings-tags [data-release-starter]').click();
    await page.waitForURL('**/#/tag/release-checklist');
    await page.locator('[data-tag-notes] input:checked').waitFor();
    assert.equal(await page.locator('[data-tag-heading]').innerText(), 'Existing release');
    assert.equal(await page.locator('.page-tags [data-tag-create]').count(), 0, 'an existing non-template key is opened as it is');
    await settingsTags();
    const afterRace = posted.length;
    await page.locator('#settings-tags [data-release-starter]').click();
    await page.waitForURL('**/#/tag/release-checklist');
    await page.locator('[data-tag-notes] input:checked').waitFor();
    assert.equal(posted.length, afterRace);
    assert.deepEqual(errors, []);
    console.log('PASS: task board, comments, mobile, rich tags, checklist versions, permissions and templates.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
