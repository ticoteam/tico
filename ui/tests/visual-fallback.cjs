// Visuals always on 1/3 (Dev 313): a company with no separate visual address. Pictures, video and audio show inline
// from the app's own /visuals route in chat and task conversations (spec test 1); a visual page and an SVG are each a
// card with its title, its still preview when it has one, and Open, which shows it in the file viewer (test 2); a
// page or SVG never loads in a frame or picture from the app's own address. Settings shows one read-only line, no
// switch, for either setup (test 7). Fixtures only.
const {chromium, webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const PNG = Buffer.from('89504e470d0a1a0a0000000d4948445200000001000000010806000000' + '1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082', 'hex');
const ORIGIN = 'https://tico-ui.test';
const TYPES = {image: ['.png', 'image/png'], video: ['.mp4', 'video/mp4'], audio: ['.wav', 'audio/wav'], svg: ['.svg', 'image/svg+xml'], page: ['.html', 'text/html']};
// As backend/visuals.py decorate sends them without a visual address: media on the app's own route, pages and SVG
// with `open` and no `src`.
const A = (id, kind, over = {}) => ({id, file_id: id, version: 1, ref: id + '@1', title: 'Title ' + id, kind, name: id + TYPES[kind][0], size: 1000,
  content_type: TYPES[kind][1], url: `/api/v2/files/${id}?v=1`, removed: false, current_version: 1,
  ...(kind === 'page' || kind === 'svg' ? {src: null, open: true} : {src: '/visuals/tok-' + id}), ...over});

(async () => {
  const browser = process.env.TICO_BROWSER === 'webkit' ? await webkit.launch({headless: true})
    : await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined, headless: true});
  const now = new Date().toISOString();
  const MESSAGES = [
    {id: 'm1', from_actor: 'bot:designer', body: 'A chart, a clip and a voice note.', created_at: now, refs: {attachments: [
      A('f-chart', 'image', {width: 800, height: 400}), A('f-clip', 'video', {width: 640, height: 360}), A('f-note', 'audio', {duration_ms: 1000})]}},
    {id: 'm2', from_actor: 'bot:designer', body: 'The funnel page and the logo.', created_at: now, refs: {attachments: [
      A('f-funnel', 'page', {preview_url: '/visuals/tok-f-funnel-preview'}), A('f-logo', 'svg')]}},
  ];
  const run = async ({viewport, hasTouch = false, label}) => {
    const context = await browser.newContext({viewport, hasTouch, isMobile: hasTouch, serviceWorkers: 'block'});
    const page = await context.newPage();
    const errors = [], visualReqs = [];
    let config = {company_name: 'Acme', visuals: 'media', visual_origin: ''};
    page.on('pageerror', e => errors.push(e.message));
    await context.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      if (p.startsWith('/visuals/')) {
        visualReqs.push(p);
        if (p.endsWith('-broken')) return route.fulfill({status: 404, body: ''});
        return route.fulfill({contentType: 'image/png', headers: {'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "default-src 'none'; sandbox"}, body: PNG});
      }
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/v2/system/storage') return route.fulfill({status: 503, contentType: 'application/json', body: '{"error":{"detail":"Not in this fixture"}}'});
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true, config});
      if (p === '/api/employees') return json([{name: 'designer', display_name: 'Designer', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
      if (p === '/api/issues') return json([]);
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p === '/api/v2/events') return;                                // live events, held open
      if (p === '/api/v2/conversations') return json({conversations: url.searchParams.get('chat_with') ? [{id: 'c-designer', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:designer']}] : []});
      if (p === '/api/v2/conversations/c-designer/snapshot') return json({messages: MESSAGES, execution: null});
      if (p === '/api/v2/tasks' && req.method() === 'GET') return json({tasks: [task], next_offset: null});
      if (p === '/api/v2/tasks/t1/files') return json({files: taskFiles});
      if (p === '/api/v2/tasks/t1') return json({task, comments: taskComments, events: [], children: [], parent: null, mover: true, messages: [], can_comment: true});
      if (p.startsWith('/api/v2/files/')) {                             // the file viewer's own signed-in read
        const svg = /logo$/.test(p);
        return route.fulfill({contentType: svg ? 'image/svg+xml' : 'text/html', headers: {'Content-Disposition': `inline; filename="${p.split('/').pop()}${svg ? '.svg' : '.html'}"`},
          body: svg ? '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>' : '<p>Funnel</p>'});
      }
      return json(unrouted(p));
    });
    const task = {id: 't1', title: 'Launch page', owner: 'bot:designer', requester: 'human:ana', status: 'review', lane: 'company', rank: 1, labels: [], links: [],
      parts: {total: 0, done: 0}, version: 1, created_at: now, updated_at: now, body: 'Make the page.', attachments: []};
    const tver = (id, mime) => ({n: 1, size: 1000, mime, sha256: 'x', created_at: now, by: 'bot:designer', comment_id: 'c1', note: null, ask: null, answers: [],
      width: null, height: null, duration_ms: null, media_state: 'none', url: `/api/v2/files/${id}?v=1`, poster_url: null, thumb_url: null});
    const taskFiles = [['f-tchart', 'image'], ['f-tclip', 'video'], ['f-tnote', 'audio'], ['f-tpage', 'page'], ['f-tlogo', 'svg']].map(([id, kind]) =>
      ({id, name: id + TYPES[kind][0], mime: TYPES[kind][1], current_version: 1, archived: false, versions: [tver(id, TYPES[kind][1])]}));
    const taskComments = [
      {id: 'c1', kind: 'say', from_actor: 'bot:designer', body: 'Media attached.', created_at: now, answers: [],
        refs: {task: 't1', comment: true, attachments: [A('f-tchart', 'image', {width: 800, height: 400}), A('f-tclip', 'video'), A('f-tnote', 'audio')]}},
      {id: 'c2', kind: 'say', from_actor: 'bot:designer', body: 'Page and logo attached.', created_at: now, answers: [],
        refs: {task: 't1', comment: true, attachments: [A('f-tpage', 'page'), A('f-tlogo', 'svg')]}}];

    // ---- chat: media inline from the app's own address; page and SVG are Open cards
    await page.goto(ORIGIN + '/#/bot/designer/chat');
    const m1 = page.locator('[data-message="m1"]'), m2 = page.locator('[data-message="m2"]');
    await m1.locator('figure.vcard').nth(2).waitFor();
    // Each card is drawn with its address on the app's own route (the fixture bytes are a picture, so the players may
    // then say they can't play it: what is checked here is that they are inline cards, not chips).
    assert.deepEqual(await m1.locator('figure.vcard').evaluateAll(els => els.map(e => [e.dataset.kind, e.dataset.freshSrc])),
      [['image', ORIGIN + '/visuals/tok-f-chart'], ['video', ORIGIN + '/visuals/tok-f-clip'], ['audio', ORIGIN + '/visuals/tok-f-note']],
      `${label}: picture, video and audio are inline cards`);
    await m1.locator('img.vcard-img').waitFor();
    assert.equal(await m1.locator('img.vcard-img').getAttribute('src'), ORIGIN + '/visuals/tok-f-chart', `${label}: the picture is from the app's own route`);
    await m2.locator('figure.vcard').nth(1).waitFor();
    assert.deepEqual(await m2.locator('figure.vcard').evaluateAll(els => els.map(e => [e.dataset.kind, e.dataset.state])),
      [['page', 'open'], ['svg', 'open']], `${label}: a page and an SVG are Open cards`);
    assert.deepEqual(await m2.locator('.vcard-title').allTextContents(), ['Title f-funnel', 'Title f-logo'], `${label}: each card has its title`);
    assert.equal(await m2.locator('figure.vcard[data-kind="page"] img.vcard-preview').getAttribute('src'), ORIGIN + '/visuals/tok-f-funnel-preview', `${label}: the still preview`);
    // A redraw keeps the Open card and takes the preview's renewed address; a preview that fails to load is hidden.
    assert.deepEqual(await page.evaluate(async () => {
      const root = document.createElement('div'), f = {file_id: 'f-x', version: 1, kind: 'page', title: 'X', name: 'x.html', open: true, src: null,
        preview_url: '/visuals/tok-x-broken', current_version: 1};
      document.body.append(root);
      root.innerHTML = visualCardsHTML([f], {key: 'k'});
      const img = root.querySelector('img.vcard-preview');
      for (let i = 0; !img.hidden && i < 100; i++) await new Promise(r => setTimeout(r, 20));
      const hiddenAfterError = img.hidden;
      root.querySelector('figure.vcard').dataset.mark = 'kept';
      visualSwap(root, visualCardsHTML([{...f, preview_url: '/visuals/tok-x.2'}], {key: 'k'}));
      const now = root.querySelector('figure.vcard'), out = [hiddenAfterError, now.dataset.mark, now.querySelector('img.vcard-preview').getAttribute('src'),
        now.querySelector('img.vcard-preview').hidden];
      root.remove();
      return out;
    }), [true, 'kept', ORIGIN + '/visuals/tok-x.2', false], `${label}: a kept Open card takes the renewed preview`);
    assert.equal(await m2.locator('figure.vcard[data-kind="svg"] img.vcard-preview').count(), 0, `${label}: no preview when there is none`);
    assert.equal(await page.locator('figure.vcard iframe').count(), 0, `${label}: no page frame on the app's own address`);
    assert.equal(await m2.locator('.chat-files').count(), 0, `${label}: an Open card is not also a file chip`);
    await m2.locator('figure.vcard[data-kind="svg"] .vcard-open-btn').click();
    await page.waitForSelector('dialog#doc-viewer[open]');
    assert.deepEqual(await page.evaluate(() => [VIEW.items[0].fileId, VIEW.items[0].version]), ['f-logo', 1], `${label}: Open shows the SVG in the file viewer`);
    await page.evaluate(() => $('#doc-viewer').close());

    // ---- task conversation: the same; Open shows the page in the task's own file panel
    await page.goto(ORIGIN + '/#/tasks?view=board');
    await page.waitForFunction(() => typeof TASKS_ST !== 'undefined' && TASKS_ST && !TASKS_ST.loading);
    await page.evaluate(() => taskModalShow(TASKS_ST.tasks.find(t => t.id === 't1')));
    const c1 = page.locator('#task-modal .tcomment', {hasText: 'Media attached.'}), c2 = page.locator('#task-modal .tcomment', {hasText: 'Page and logo attached.'});
    await c1.locator('figure.vcard').nth(2).waitFor();
    assert.deepEqual(await c1.locator('figure.vcard').evaluateAll(els => els.map(e => [e.dataset.kind, e.dataset.freshSrc])),
      [['image', ORIGIN + '/visuals/tok-f-tchart'], ['video', ORIGIN + '/visuals/tok-f-tclip'], ['audio', ORIGIN + '/visuals/tok-f-tnote']],
      `${label}: task media inline`);
    await c2.locator('figure.vcard').nth(1).waitFor();
    assert.deepEqual(await c2.locator('figure.vcard').evaluateAll(els => els.map(e => [e.dataset.kind, e.dataset.state])),
      [['page', 'open'], ['svg', 'open']], `${label}: task page and SVG are Open cards`);
    assert.equal(await c2.locator('.tc-thumb, .tc-file').count(), 0, `${label}: not also chips`);
    await c2.locator('figure.vcard[data-kind="page"] .vcard-open-btn').click();
    assert.deepEqual(await page.evaluate(() => [$('#task-modal').tfOpen?.id, $('#task-modal').tfOpen?.n]), ['f-tpage', 1], `${label}: Open shows the page in the file panel`);
    assert(visualReqs.every(p => !/f-(funnel|logo|tpage|tlogo)$/.test(p)), `${label}: a page or SVG was read from the app's own visual route: ${visualReqs}`);

    // ---- Settings: one read-only line, no switch, for each setup
    for (const [mode, words] of [['media', "Visuals: pages and SVG can't show inline. Add a visual address or Tico file hosting."],
      ['hosted', 'Visuals: full, from Tico file hosting'], ['full', 'Visuals: full']]) {
      config = {company_name: 'Acme', visuals: mode, visual_origin: mode === 'full' ? 'https://files.acme.example' : ''};
      await page.goto(ORIGIN + '/?setup=' + mode + '#/settings');
      await page.locator('[data-settings-tab="general"]').click();
      const line = page.locator('#settings-visuals');
      await line.waitFor();
      assert.equal((await line.innerText()).replace(/\s+/g, ' ').trim(), words + ' How visuals show', `${label}: settings line for ${mode}`);
      assert.match(await line.locator('a').getAttribute('href'), /docs\/visuals\.md/);
      assert.equal(await line.locator('input, button, select, [role="switch"]').count(), 0, `${label}: the line has no control`);
      assert.equal(await page.locator('#settings-general').getByText(/visual/i).count(), 1, `${label}: nothing else about visuals in settings, no switch`);
    }
    assert.deepEqual(errors, [], String(errors));
    await context.close();
  };
  try {
    await run({viewport: {width: 1200, height: 800}, label: 'desktop'});
    await run({viewport: {width: 390, height: 844}, hasTouch: true, label: 'phone'});
    console.log('visual fallback ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
