// Visuals with inline serving off (hosted, no visual origin: every attachment's `src` is null) fall back to the usual
// file chips and thumbnails in chat and task comments. Those show the title the sender gave the file, not its file
// name (Dev 245 QA); the name stays in data-name for the viewer and the download. Fixtures only.
const {chromium, webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const PNG = Buffer.from('89504e470d0a1a0a0000000d4948445200000001000000010806000000' + '1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082', 'hex');
const A = (id, name, content_type, kind, title) => ({id, file_id: id, version: 1, ref: id + '@1', title, kind, name, size: 1000, content_type,
  url: `/api/v2/files/${id}?v=1`, removed: false, current_version: 1, src: null});

(async () => {
  const browser = process.env.TICO_BROWSER === 'webkit' ? await webkit.launch({headless: true})
    : await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined, headless: true});
  const now = new Date().toISOString();
  const messages = [{id: 'm1', sender_id: 'bot:designer', body: 'This week.', created_at: now, refs: {attachments: [
    A('f-chart', 'chart.png', 'image/png', 'image', 'Weekly signups'), A('f-page', 'local.html', 'text/html', 'page', 'Embedded pair'),
    A('f-csv', 'data.csv', 'text/csv', 'file', 'data.csv')]}}];
  const task = {id: 't1', title: 'Metrics', owner_id: 'bot:designer', requester_id: 'human:ana', status: 'review', lane: 'company', rank: 1, labels: [], links: [],
    parts: {total: 0, done: 0}, version: 1, created_at: now, updated_at: now, body: 'Metrics.', attachments: []};
  const tver = (id, mime) => ({n: 1, size: 1000, mime, sha256: 'x', created_at: now, by: 'bot:designer', comment_id: 'c1', note: null, ask: null, answers: [],
    width: null, height: null, duration_ms: null, media_state: 'none', url: `/api/v2/files/${id}?v=1`, poster_url: null, thumb_url: null});
  const taskFiles = [{id: 'f-metrics', name: 'metrics.png', mime: 'image/png', current_version: 1, archived: false, versions: [tver('f-metrics', 'image/png')]},
    {id: 'f-remote', name: 'remote.html', mime: 'text/html', current_version: 1, archived: false, versions: [tver('f-remote', 'text/html')]},
    {id: 'f-notes', name: 'notes.csv', mime: 'text/csv', current_version: 1, archived: false, versions: [tver('f-notes', 'text/csv')]},
    {id: 'f-plain', name: 'untitled.png', mime: 'image/png', current_version: 1, archived: false, versions: [tver('f-plain', 'image/png')]}];
  const taskComments = [{id: 'c1', kind: 'say', sender_id: 'bot:designer', body: 'Metrics attached.', created_at: now, answers: [], refs: {task: 't1', comment: true,
    attachments: [A('f-metrics', 'metrics.png', 'image/png', 'image', 'Private metrics'), A('f-remote', 'remote.html', 'text/html', 'page', 'Remote reference'),
      A('f-gone', 'old.html', 'text/html', 'page', 'Earlier draft'),
      // sent without a title: the server stores the file's name as its title (backend/visuals.py)
      A('f-notes', 'notes.csv', 'text/csv', 'file', 'notes.csv'), A('f-plain', 'untitled.png', 'image/png', 'image', 'untitled.png')]}}];

  const context = await browser.newContext({viewport: {width: 1200, height: 900}, serviceWorkers: 'block'});
  await context.addCookies([{name: 'tico_session', value: 'secret', domain: 'tico-ui.test', path: '/', secure: true, httpOnly: true}]);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await context.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.startsWith('/api/v2/files/')) return route.fulfill({contentType: 'image/png', body: PNG});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
    if (p === '/api/employees') return json([{name: 'designer', display_name: 'Designer', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
    if (p === '/api/issues') return json([]);
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    if (p === '/api/v2/events') return;                                 // live events, held open
    if (p === '/api/v2/conversations') return json({conversations: url.searchParams.get('chat_with') ? [{id: 'c-designer', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:designer']}] : []});
    if (p === '/api/v2/conversations/c-designer/snapshot') return json({messages, execution: null});
    if (p === '/api/v2/tasks' && req.method() === 'GET') return json({tasks: [task], next_offset: null});
    if (p === '/api/v2/tasks/t1/files') return json({files: taskFiles});
    if (p === '/api/v2/tasks/t1') return json({task, comments: taskComments, events: [], children: [], parent: null, mover: true, messages: [], can_comment: true});
    return json(unrouted(p));
  });

  // ---- chat: a titled picture is a thumbnail captioned and named by its title; a titled page is a chip with the title
  await page.goto('https://tico-ui.test/#/bot/designer/chat');
  const m1 = page.locator('[data-message="m1"]');
  await m1.locator('.chat-files .chat-thumb.ready').waitFor();
  assert.equal(await m1.locator('figure.vcard').count(), 0, 'no visual address: no cards');
  const thumb = m1.locator('.chat-thumb');
  assert.deepEqual(await thumb.evaluate(b => [b.getAttribute('aria-label'), b.title, b.querySelector('img').alt, b.dataset.name]),
    ['View Weekly signups', 'Weekly signups', 'Weekly signups', 'chart.png'], 'the thumbnail is named by its title; the file keeps its name');
  assert.equal(await m1.locator('.chat-file-title').innerText(), 'Weekly signups', 'the title shows under the thumbnail');
  const pills = m1.locator('.chat-files a.pill');
  assert.deepEqual(await pills.allInnerTexts(), ['Embedded pair', 'data.csv'], 'a chip shows the title, or the name when there is none');
  assert.deepEqual(await pills.evaluateAll(as => as.map(a => [a.dataset.name, a.title])), [['local.html', 'local.html'], ['data.csv', '']]);
  assert.equal(await m1.locator('.chat-files > *').count(), 3, 'one chip or thumbnail per file');

  // ---- task comments: the same, at that version
  await page.goto('https://tico-ui.test/#/tasks?view=board');
  await page.waitForFunction(() => typeof TASKS_ST !== 'undefined' && TASKS_ST && !TASKS_ST.loading);
  await page.evaluate(() => taskModalShow(TASKS_ST.tasks.find(t => t.id === 't1')));
  const c1 = page.locator('#task-modal .tcomment', {hasText: 'Metrics attached.'});
  await c1.locator('.tc-thumb[data-tf-jump="f-metrics"]').waitFor();
  assert.equal(await c1.locator('figure.vcard').count(), 0, 'no visual address: no cards');
  assert.deepEqual(await c1.locator('.tc-thumb').evaluateAll(bs => bs.map(b => [b.getAttribute('aria-label'), b.title])),
    [['Open Private metrics (metrics.png v1)', 'Private metrics (metrics.png v1)'], ['Open untitled.png v1', 'untitled.png v1']],
    'a titled thumb is named by its title; an untitled one (title = its name) as before');
  assert.deepEqual(await c1.locator('.tc-file-title').allInnerTexts(), ['Private metrics'], 'only the titled thumb has a caption');
  const chips = c1.locator('.tc-files .tc-file');
  assert.deepEqual((await chips.allInnerTexts()).map(t => t.replace(/\s+/g, ' ').trim()), ['HTML Remote reference v1', 'CSV notes.csv v1', 'Earlier draft'],
    'a chip shows the title, or the name when there is none');
  assert.deepEqual(await chips.evaluateAll(bs => bs.map(b => [b.getAttribute('aria-label'), b.getAttribute('title')])),
    [['Open Remote reference (remote.html v1)', 'remote.html'], ['Open notes.csv v1', null], [null, 'old.html']]);
  await chips.first().click();
  assert.deepEqual(await page.evaluate(() => [$('#task-modal').tfOpen?.id, $('#task-modal').tfOpen?.n]), ['f-remote', 1], 'a titled chip still opens its file');

  assert.deepEqual(errors, []);
  await browser.close();
  console.log('visual chip titles: ok');
})().catch(e => { console.error(e); process.exit(1); });
