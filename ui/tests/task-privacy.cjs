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
// The icon font is subset (ui/vendor/fonts/icons.txt); a ligature missing from that list renders as a stray glyph.
{
  const subset = require('node:fs').readFileSync(require('node:path').join(__dirname, '../vendor/fonts/icons.txt'), 'utf8').split('\n').filter(Boolean);
  for (const name of ['chevron_right', ...[...html.matchAll(/class="nav-icon" aria-hidden="true">([a-z_]+)</g)].map(m => m[1])])
    assert(subset.includes(name), `icon ${name} is not in ui/vendor/fonts/icons.txt (run scripts/build-icon-font.py)`);
  assert.deepEqual(subset, [...subset].sort(), 'icons.txt must stay alphabetical for Google Fonts');
}
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1200, height: 900}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const bots = [['coo', 'COO', 'leadership'], ['cpo', 'AI CPO', 'product'], ['cmo', 'AI CMO', 'marketing'],
      ['cto', 'CTO', 'engineering']].map(([name, display_name, team]) =>
      ({name, display_name, host: 'keeper', status: 'active', can_chat: true, team,
        runtime: {cpo: 'codex', cmo: 'claude'}[name], private_tasks_default: name === 'cpo',
        update_subscriptions: name === 'cpo' ? ['templates'] : []}));
    const now = new Date().toISOString();
    const task = (id, over) => ({id, title: id, body: 'Details.', owner_id: 'bot:cmo', requester_id: 'human:reviewer', status: 'open',
      lane: 'company', rank: 1, labels: [], links: [], parts: {total: 0, done: 0}, version: 3, created_at: now, updated_at: now, ...over});
    let me = {id: 'reviewer', name: 'Test Reviewer', email: 'reviewer@example.test', role: 'owner', mover: true, cloud: true};
    const tags = [
      {id: 'tag-newsletter', key: 'newsletter', label: 'release', metadata: {date: '2026-10-02'},
        markdown: '# Release\n\n- [ ] Smoke checks\n- [x] Tell the team\n\n```md\n- [ ] Literal example\n```\n\n<script>window.tagUnsafe = true</script>',
        is_template: false, template_id: null, owner_id: 'human:reviewer', version: 1},
      {id: 'tag-template', key: 'release-checklist', label: 'release', metadata: {channel: 'stable'},
        markdown: '- [ ] Migrations / scripts to run', is_template: true, template_id: null, owner_id: 'human:reviewer', version: 1},
    ];
    let staleTag = false, raceStarter = false;
    let tasks = [
      task('Draft the newsletter', {private: true, rank: 2, labels: ['newsletter'], tags: [tags[0]], body: 'Review [packet](http://tico-ui.test/api/v2/files/doc).',
        attachments: [{id: 'doc', name: 'review-packet.md'}, {id: 'clip', name: 'first-cut.mp4'}, {id: 'archive', name: 'source.zip'}]}),
      task('Approve the budget', {owner_id: 'human:reviewer', requester_id: 'bot:coo', rank: 1, labels: ['finance'], note: 'Waiting on finance approval'}),
      task('Write the copy', {rank: 1, labels: ['newsletter', 'copy']}),
      task('Ship the pricing page', {owner_id: 'bot:cpo', lane: 'product', status: 'review', rank: 1,
        links: [{id: 'l1', kind: 'pr', url: 'https://github.com/ticoteam/tico/pull/412', title: 'tico#412', state: 'open'}], parts: {total: 2, done: 1}}),
      task('Fix the checkout bug', {owner_id: 'bot:cpo', lane: 'product', status: 'open', rank: 2, labels: ['bug']}),
    ];
    const comments = [{id: 'm1', kind: 'say', sender_id: 'human:ben', recipient_id: 'bot:cmo', body: 'Use the September numbers.', created_at: now, edited_at: now, refs: {task: 'Draft the newsletter', comment: true, attachments: [{id: 'doc', name: 'review-packet.md'}]}}];
    const events = [{id: 'e1', task_id: 'Draft the newsletter', ts: now, actor: 'human:reviewer', field: 'status', old: null, new: 'open', note: ''},
                    {id: 'e2', task_id: 'Draft the newsletter', ts: now, actor: 'bot:cmo', field: 'status', old: 'open', new: 'doing', note: 'Drafting from the brief'},
                    {id: 'e3', task_id: 'Draft the newsletter', ts: now, actor: 'bot:cmo', field: 'note', old: '', new: 'Drafting from the brief', note: 'Drafting from the brief'},
                    {id: 'e4', task_id: 'Draft the newsletter', ts: now, actor: 'bot:cmo', field: 'note', old: '', new: 'Waiting on the logo', note: ''},
                    {id: 'e5', task_id: 'Draft the newsletter', ts: now, actor: 'human:ben', field: 'comment', old: 'm1', new: 'm1', note: ''},
                    {id: 'e6', task_id: 'Draft the newsletter', ts: now, actor: 'human:ben', field: 'comment', old: 'm0', new: null, note: ''}];
    const posted = [];
    const people = [];
    let preferencePending = true, tasksStartedBeforePreference = false;
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui) { const file = uiFile(ui[1]); if (fs.existsSync(file)) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')}); }
      if (p === '/vendor/fonts/material-symbols-outlined.woff2') return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile('vendor/fonts/material-symbols-outlined.woff2'))});
      if (p === '/vendor/marked.min.js') return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
      if (p === '/api/employees') return json(bots);
      if (p === '/api/issues') return json([]);
      if (p === '/api/me') return json(me);
      if (p === '/api/status') return json({active: [], employees: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/humans') return json({people});
      if (p === '/api/v2/tasks/labels') return json({labels: ['newsletter', 'copy', 'bug', 'finance'], tags: [tags[0]]});
      if (p === '/api/v2/tags' && req.method() === 'GET') return json({tags});
      if (p === '/api/v2/tags' && req.method() === 'POST') {
        const body = req.postDataJSON(); posted.push({path: p, body});
        const tag = {...body, id: 'tag-starter', metadata: {}, owner_id: 'human:reviewer', version: 1};
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
        await new Promise(resolve => setTimeout(resolve, t(150)));
        preferencePending = false;
        return json({key: 'tasks.view', value: null});
      }
      if (p === '/api/v2/tasks' && req.method() === 'GET') {
        if (preferencePending) tasksStartedBeforePreference = true;
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
        if (owner) rows = rows.filter(task => task.owner_id === owner || task.owner_id === 'bot:' + owner);
        if (url.searchParams.get('sort') === 'finished') rows.sort((a, b) => String(b.closed_at || b.updated_at).localeCompare(String(a.closed_at || a.updated_at)));
        const offset = Number(url.searchParams.get('offset') || 0), limit = Number(url.searchParams.get('limit') || 500);
        return json({tasks: rows.slice(offset, offset + limit), next_offset: rows.length > offset + limit ? offset + limit : null});
      }
      if (p === '/api/v2/tasks' && req.method() === 'POST') {
        const body = req.postDataJSON(); posted.push({path: p, body});
        return json({task: task('created-private', body)});
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
        comments.push({id: 'm' + comments.length, kind: 'say', sender_id: 'human:reviewer', recipient_id: 'bot:cmo', body: body.text, created_at: new Date().toISOString(), refs: {task: decodeURIComponent(cm[1]), comment: true}});
        return json({comment: comments.at(-1), comments, woke: true});
      }
      if (/^\/api\/v2\/bots\/[^/]+\/definition$/.test(p) && req.method() === 'POST') {
        const body = req.postDataJSON(); posted.push({path: p, body});
        return json({});
      }
      if (p === '/api/v2/conversations') return json({conversations: []});
      if (p.endsWith('/messages')) return json({messages: []});
      return json(unrouted(p));
    });
    await page.goto('http://tico-ui.test/#/tasks');
    await page.waitForFunction(() => S.me?.id === 'reviewer' && TASKS_ST?.tasks?.length === 3);
    if (screenshotDir) fs.mkdirSync(screenshotDir, {recursive: true});
    const shot = async name => { await page.evaluate(() => document.fonts.ready); if (screenshotDir) await page.screenshot({path: path.join(screenshotDir, name + '.png')}); };
    for (const [device, width, height] of [['desktop', 1200, 900], ['phone', 390, 844]]) {
      await page.setViewportSize({width, height});
      await page.evaluate(() => openTaskCreate('bot:cpo'));
      const create = page.locator('#task-create[open]');
      const checkbox = create.locator('[name=private]');
      assert(await checkbox.isChecked(), 'effective sensitive-bot default is checked');
      assert(await checkbox.isEnabled(), 'human can choose company at creation');
      await shot('private-create-' + device);
      await checkbox.uncheck();
      await create.locator('[name=owner]').selectOption('cmo');
      assert(!await checkbox.isChecked(), 'manual company choice survives assignee changes');
      await checkbox.check();
      await create.locator('[name=owner]').selectOption('cto');
      assert(await checkbox.isChecked(), 'manual private choice survives ordinary assignee changes');
      await create.locator('[data-modal-close]').click();
      await page.evaluate(() => openTaskCreate('bot:cmo', {parent: {id: 'parent', title: 'Private parent', private: true}}));
      assert(await checkbox.isChecked() && await checkbox.isDisabled(), 'private parent forces privacy');
      await create.locator('[data-modal-close]').click();
      await page.evaluate(() => { location.hash = '#/task/' + encodeURIComponent('Draft the newsletter'); });
      const edit = page.locator('#task-modal[open]');
      await edit.locator('[data-task-private]').waitFor();
      assert(await edit.locator('[data-task-private]').isChecked());
      assert(await edit.locator('[data-task-private]').isEnabled(), 'human requester may publish');
      await shot('private-edit-' + device);
      await edit.locator('[data-modal-close]').click();
      await page.evaluate(() => { location.hash = '#/tasks'; });
      await page.locator('#task-new').waitFor();
    }
    await page.evaluate(() => openTaskCreate('bot:cpo'));
    const create = page.locator('#task-create[open]');
    await create.locator('[name=private]').uncheck();
    await create.locator('[name=title]').fill('Review public terms');
    await create.locator('[name=body]').fill('Review the company terms.');
    await create.locator('[type=submit]').click();
    await page.waitForFunction(() => !document.querySelector('#task-create').open);
    assert.equal(posted.find(p => p.path === '/api/v2/tasks').body.private, false, 'explicit human company choice reaches API');
    await page.setViewportSize({width: 1200, height: 900});
    await page.evaluate(() => { location.hash = '#/settings'; });
    await page.locator('#bot-editor').waitFor({state: 'attached'});
    await page.evaluate(async () => { await loadSettings(); settingsEditBot('cpo'); });
    const settings = page.locator('#pane-settings #bot-editor');
    await settings.locator('[name=private_tasks_default]').waitFor();
    assert(await settings.locator('[name=private_tasks_default]').isChecked());
    await shot('private-bot-settings-desktop');
    // Subscribe to updates starts from the saved choice and sends only a change, at either width.
    assert(await settings.locator('[name=update_templates]').isChecked());
    assert(!(await settings.locator('[name=update_tico]').isChecked()));
    await page.setViewportSize({width: 390, height: 844});
    await settings.locator('[name=update_tico]').check();
    await settings.locator('[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#bot-editor [data-bot-status]')?.textContent === 'Saved');
    assert.deepEqual(posted.find(p => p.path === '/api/v2/bots/cpo/definition').body.update_subscriptions, ['templates', 'tico']);
    assert.deepEqual(errors, []);
    console.log('task privacy UI passed: desktop/phone create/edit, default, manual intent, private parent, explicit company payload');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
