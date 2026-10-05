// A task's files and questions: the strip of tiles (lazy, sized, skeletons), a tile opened in place with its versions
// and Compare, image arrows and Esc; files a comment carried; asks on a file version and on comments (single, multi
// with Other, Dismiss; the asker sees no buttons); the modal's poll bringing new files without breaking a playing
// video or a half-filled answer; a reader's questions (no controls) and an older runner's plain answer; linked
// images inline; board covers and the open-question dot; Pin to the sidebar; the task view preference and file
// input on a local install; no sideways scroll at phone width. Every request is intercepted.
// TICO_SHOTS=<dir> saves screenshots (desktop and phone, dark and light).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const {html, uiFile} = require('./support/page.cjs');
const shots = process.env.TICO_SHOTS || '';

// A small striped PNG, made here so the test needs no image files.
function png(w, h, rgb) {
  const crcTable = Array.from({length: 256}, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
  const crc = buf => { let c = 0xffffffff; for (const b of buf) c = crcTable[(c ^ b) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
  const chunk = (type, data) => { const len = Buffer.alloc(4), sum = Buffer.alloc(4); len.writeUInt32BE(data.length); const td = Buffer.concat([Buffer.from(type), data]); sum.writeUInt32BE(crc(td)); return Buffer.concat([len, td, sum]); };
  const head = Buffer.alloc(13); head.writeUInt32BE(w, 0); head.writeUInt32BE(h, 4); head[8] = 8; head[9] = 2;
  const rows = [];
  for (let y = 0; y < h; y++) { rows.push(0); for (let x = 0; x < w; x++) rows.push(...rgb(x, y)); }
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', head), chunk('IDAT', zlib.deflateSync(Buffer.from(rows))), chunk('IEND', Buffer.alloc(0))]);
}
const TEAL = png(160, 90, (x, y) => [30 + (x >> 1), 120 + y, 140]);
const AMBER = png(160, 90, (x, y) => [220, 110 + (y >> 1), 40 + (x >> 2)]);

(async () => {
  const t0 = Date.now();
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}, serviceWorkers: 'block'});
    const errors = [], posted = [], requested = [];
    page.on('pageerror', e => errors.push(e.message));
    // The open task reads again when live events say it changed (taskChatLive); with window.fastPoll set, a change
    // arrives every 200ms.
    await page.addInitScript(() => {
      setInterval(() => { if (window.fastPoll && typeof taskChatLive === 'function' && TASK_CHAT) taskChatLive(TASK_CHAT); }, 200);
    });
    const now = Date.now(), at = mins => new Date(now - mins * 60000).toISOString();
    const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: false};
    const bots = [{name: 'editor', display_name: 'Editor', host: 'keeper', status: 'active', can_chat: true}];
    const url = (id, n, part = '') => `/api/v2/files/${id}${part}?v=${n}`;
    const version = (id, n, over) => ({n, size: 2048, mime: '', sha256: 'x' + n, created: at(200 - n * 30), by: 'bot:editor', comment_id: null, note: null,
      ask: null, answers: [], width: null, height: null, duration_ms: null, media_state: 'none', url: url(id, n), poster_url: null, thumb_url: null, ...over});
    const verdict = {questions: [{id: 'verdict', header: 'Rough cut', question: 'Is v3 ready to publish?',
      options: [{label: 'Approve'}, {label: 'Request changes', description: 'Say what to change'}], multi: false, other: true}], who: null};
    const files = [
      {id: 'f-cut', name: 'cut.mp4', mime: 'video/mp4', current_version: 3, archived: false, versions: [3, 2, 1].map(n => version('f-cut', n,
        {mime: 'video/mp4', width: 1920, height: 1080, duration_ms: 42000, media_state: 'ready', poster_url: url('f-cut', n, '/poster'), thumb_url: url('f-cut', n, '/thumb'),
         note: {3: 'Tighter intro, new music.', 2: 'Second pass.', 1: 'First cut.'}[n], comment_id: n === 2 ? 'm1' : null, ask: n === 3 ? verdict : null}))},
      {id: 'f-script', name: 'script.md', mime: 'text/markdown', current_version: 2, archived: false, versions: [2, 1].map(n => version('f-script', n, {mime: 'text/markdown'}))},
      {id: 'f-frame', name: 'frame.png', mime: 'image/png', current_version: 1, archived: false,
        versions: [version('f-frame', 1, {mime: 'image/png', width: 160, height: 90, thumb_url: url('f-frame', 1, '/thumb'), comment_id: 'm1'})]},
      {id: 'f-still', name: 'still.png', mime: 'image/png', current_version: 1, archived: false,
        versions: [version('f-still', 1, {mime: 'image/png', width: 160, height: 90, thumb_url: url('f-still', 1, '/thumb')})]},
      {id: 'f-rows', name: 'shots.csv', mime: 'text/csv', current_version: 1, archived: false, versions: [version('f-rows', 1, {mime: 'text/csv'})]},
      {id: 'f-logo', name: 'logo.svg', mime: 'image/svg+xml', current_version: 1, archived: false, versions: [version('f-logo', 1, {mime: 'image/svg+xml'})]},
    ];
    const scripts = {1: '# Script\n\nIntro line.\n\nScene one.\nScene two.\n', 2: '# Script\n\nA tighter intro line.\n\nScene one.\nScene two.\nScene three.\n'};
    const csv = 'shot,length\n' + Array.from({length: 260}, (_, i) => `s${i + 1},${i % 7 + 1}s`).join('\n');
    const task = {id: 't1', title: 'Cut the launch video', owner: 'bot:editor', requester: 'human:ana', status: 'review', lane: 'company', rank: 1,
      labels: [], links: [], parts: {total: 0, done: 0}, version: 4, created: at(400), updated: at(5), open_asks: 1,
      body: 'Reference frame: ![frame](https://img.example.com/ref.png)\n\nMood: https://img.example.com/mood.jpg',
      attachments: [{id: 'f-frame', name: 'frame.png', content_type: 'image/png', size: 2048, url: '/api/v2/files/f-frame'}]};
    const plain = {...task, id: 't2', title: 'Write the captions', status: 'doing', open_asks: 0, attachments: [], body: 'Captions for the cut.', updated: at(60)};
    // t2 is one Ana may read but not comment on; one of its questions was answered by an older runner: {by, text, at}
    const plainComments = [
      {id: 'p1', kind: 'ask', from_actor: 'bot:editor', body: 'Which tone?', created: at(50), answers: [{by: 'human:sam', text: 'Keep it warm and short.', at: at(40)}],
        refs: {task: 't2', questions: [{id: 'tone', question: 'Which tone?', options: [{label: 'Warm'}, {label: 'Plain'}], other: true}]}},
      {id: 'p2', kind: 'ask', from_actor: 'bot:editor', body: 'Which font?', created: at(30), answers: [],
        refs: {task: 't2', questions: [{id: 'font', question: 'Which font?', options: [{label: 'Serif'}, {label: 'Sans'}], other: true}]}},
    ];
    const plainFiles = [{id: 'f-notes', name: 'captions.md', mime: 'text/markdown', current_version: 1, archived: false,
      versions: [version('f-notes', 1, {mime: 'text/markdown', ask: {questions: [{id: 'ok', question: 'Good to go?', options: [{label: 'Yes'}, {label: 'No'}]}], who: null}})]}];
    const comments = [
      {id: 'm1', kind: 'say', from_actor: 'bot:editor', body: 'Second pass and a frame grab.', created: at(140), refs: {task: 't1', comment: true}},
      {id: 'm2', kind: 'ask', from_actor: 'bot:editor', body: 'Which music and thumbnail?', created: at(90), answers: [], refs: {task: 't1',
        questions: [{id: 'music', header: 'Music', question: 'Which tracks fit?', options: [{label: 'Upbeat'}, {label: 'Calm'}, {label: 'None'}], multi: true, other: true},
          {id: 'thumb', header: 'Thumbnail', question: 'Which thumbnail?', options: [{label: 'A', file: 'f-frame@1'}, {label: 'B', file: 'f-still@1'}], multi: false, other: false}]}},
      {id: 'm3', kind: 'ask', from_actor: 'bot:editor', body: 'Ship today?', created: at(80), answers: [], refs: {task: 't1',
        questions: [{id: 'ship', question: 'Ship today?', options: [{label: 'Yes'}, {label: 'No'}], multi: false, other: false}]}},
      {id: 'm4', kind: 'ask', from_actor: 'human:ana', body: 'Captions too?', created: at(70), answers: [], refs: {task: 't1',
        questions: [{id: 'cap', question: 'Captions too?', options: [{label: 'Yes'}, {label: 'No'}]}]}},
    ];
    let prefs = {};
    let thumbDelay = 250;
    await page.route('**/*', async route => {
      const req = route.request(), u = new URL(req.url()), p = u.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (u.origin === 'https://img.example.com') { requested.push(u.href); return route.fulfill({contentType: 'image/png', body: p.includes('mood') ? AMBER : TEAL}); }
      if (u.origin !== 'http://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui) { const file = uiFile(ui[1]); if (fs.existsSync(file)) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')}); }
      if (p.startsWith('/vendor/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({body: fs.readFileSync(uiFile(p.slice(1))),
        contentType: p.endsWith('.js') ? 'application/javascript' : p.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream'});
      if (p === '/api/me') return json(me);
      if (p === '/api/employees') return json(bots);
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}, {id: 'sam', name: 'Sam', email: 'sam@acme.example'}]});
      if (p === '/api/issues') return json([]);
      if (p === '/api/status') return json({active: [], employees: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/routines') return json({routines: []});
      if (p === '/api/v2/task-types') return json({types: []});
      if (p.startsWith('/api/v2/preferences/')) {
        const key = p.split('/').at(-1);
        if (req.method() === 'POST') { const body = req.postDataJSON(); posted.push({path: p, body}); prefs[key] = body.value; return json({key, value: body.value}); }
        requested.push(p);
        return json({key, value: key === 'tasks.view' ? {view: 'board'} : prefs[key] ?? null});
      }
      const file = p.match(/^\/api\/v2\/files\/([^/]+)(\/poster|\/thumb)?$/);
      if (file) {
        requested.push(p + u.search);
        const [id, part] = [file[1], file[2] || ''], n = u.searchParams.get('v');
        if (part || id === 'f-frame' || id === 'f-still') {
          await new Promise(r => setTimeout(r, thumbDelay));
          return route.fulfill({contentType: 'image/png', headers: {'Cache-Control': 'private, max-age=31536000, immutable'}, body: id === 'f-still' || (id === 'f-cut' && n === '1') ? AMBER : TEAL});
        }
        if (id === 'f-script' || id === 'f-notes') return route.fulfill({contentType: 'text/markdown', body: scripts[n]});
        if (id === 'f-rows') return route.fulfill({contentType: 'text/csv', body: csv});
        if (id === 'f-cut') return new Promise(() => {});        // a video that is still on its way: the poster shows
        return route.fulfill({contentType: 'application/octet-stream', body: 'not a real file'});
      }
      if (p === '/api/v2/tasks' && req.method() === 'GET') return json({tasks: [task, plain], next_offset: null});
      if (p === '/api/v2/tasks/t1/files') return json({files});
      if (p === '/api/v2/tasks/t2/files') return json({files: plainFiles});
      if (p === '/api/v2/tasks/t1/answers' && req.method() === 'POST') {
        const body = req.postDataJSON(); posted.push({path: p, body});
        const answer = {...body, by: 'human:ana', at: new Date().toISOString()};
        let text;
        if (body.target.file) {
          const f = files.find(x => x.id === body.target.file), v = f.versions.find(x => x.n === body.target.version);
          v.answers.push(answer);
          text = `Ana approved "${f.name}" v${v.n}.`;
        } else {
          const c = comments.find(x => x.id === body.target.comment);
          c.answers.push(answer);
          text = body.dismiss ? 'Ana dismissed the question.' : `Ana answered "${c.refs.questions[0].question}": ${Object.values(body.answers).flat().join(', ')}.`;
        }
        task.open_asks = Math.max(0, task.open_asks - 1);
        comments.push({id: 'a' + comments.length, kind: 'answer', from_actor: 'human:ana', body: text, created: new Date().toISOString(), answer, refs: {task: 't1'}});
        return json({comment: comments.at(-1)});
      }
      const one = p.match(/^\/api\/v2\/tasks\/([^/]+)$/);
      if (one) {
        const t = [task, plain].find(x => x.id === one[1]);
        return json({task: t, comments: t === task ? comments : plainComments, events: [], children: [], parent: null, mover: true, messages: [], can_comment: t === task});
      }
      if (p === '/api/v2/conversations') return json({conversations: []});
      return json({});
    });
    const theme = async mode => page.evaluate(m => document.documentElement.setAttribute('data-theme', m), mode);
    const shot = async name => { if (shots) await page.screenshot({path: path.join(shots, name + '.png')}); };
    const noSideways = async (where) => {
      const r = await page.evaluate(() => ({doc: document.documentElement.scrollWidth, win: innerWidth,
        dialogs: [...document.querySelectorAll('dialog[open]')].map(d => [d.scrollWidth, d.clientWidth])}));
      assert(r.doc <= r.win, `${where}: the page scrolls sideways ${JSON.stringify(r)}`);
      for (const [sw, cw] of r.dialogs) assert(sw <= cw + 1, `${where}: a dialog scrolls sideways ${JSON.stringify(r)}`);
    };

    // ---- the board: a cover for the task with a picture, the open-question dot, none for the other
    await page.goto('http://tico-ui.test/#/tasks?view=board');
    await page.waitForFunction(() => TASKS_ST && !TASKS_ST.loading && document.querySelectorAll('#task-body .bcard').length === 2);
    const card = page.locator('#task-body .bcard[data-task-key="tt1"]');
    assert.equal(await card.locator('.bcard-cover img').getAttribute('data-tf-src'), '/api/v2/files/f-frame/thumb');
    await card.locator('.bcard-cover.ready').waitFor();
    assert.equal(await card.locator('.ask-dot').count(), 1, 'an open question shows as a dot');
    assert.equal(await page.locator('#task-body .bcard[data-task-key="tt2"] .bcard-cover, #task-body .bcard[data-task-key="tt2"] .ask-dot').count(), 0);
    // a local install (no `cloud`) still reads the saved view and offers files on a new task
    assert(requested.includes('/api/v2/preferences/tasks.view'), 'the saved task view is read');
    await page.evaluate(() => openTaskCreate());
    assert.equal(await page.locator('#task-create-form input[type=file][name=files]').count(), 1);
    await page.keyboard.press('Escape');
    await page.locator('#task-create-form').waitFor({state: 'hidden'});

    // ---- Pin to sidebar, and unpin from its ✕
    assert.equal(await page.locator('#nav-pins').isHidden(), true);
    await page.locator('#task-pin').click();
    await page.locator('#pin-list a[href="#/tasks?type=general&view=board"]').waitFor();
    for (const mode of ['dark', 'light']) { await theme(mode); await shot(`board-covers-desktop-${mode}`); }
    await theme('dark');
    assert.equal(await page.locator('#nav-pins-h').textContent(), 'Pipelines');
    assert.match(await page.locator('#pin-list').innerText(), /General/);
    assert.equal(await page.locator('#task-pin').getAttribute('aria-pressed'), 'true');
    assert.deepEqual(posted.at(-1).body.value.items, [{hash: '#/tasks?type=general&view=board', name: 'General'}]);
    await page.locator('#pin-list .pin-row').hover();
    await page.locator('#pin-list [data-unpin="0"]').click();
    await page.waitForFunction(() => document.querySelector('#nav-pins').hidden);
    assert.deepEqual(posted.at(-1).body.value.items, []);
    assert.equal(await page.locator('#task-pin').getAttribute('aria-pressed'), 'false');

    // ---- the strip: one tile per file at its newest version, sized, a skeleton until the thumb arrives
    thumbDelay = 400;
    await page.evaluate(() => taskModalShow(TASKS_ST.tasks.find(t => t.id === 't1')));
    const modal = page.locator('#task-modal');
    await modal.locator('.tf-tile[data-tf-file="f-cut"]').waitFor();
    assert.deepEqual(await modal.locator('.tf-tile .tf-name').allTextContents(), ['cut.mp4', 'script.md', 'frame.png', 'still.png', 'shots.csv', 'logo.svg']);
    const cutTile = modal.locator('.tf-tile[data-tf-file="f-cut"]');
    assert.match(await cutTile.locator('.tf-meta').innerText(), /^v3 · \d+h$/);
    assert.equal(await cutTile.locator('.ask-dot').count(), 1, 'a file with an open question has a dot');
    assert.equal(await cutTile.locator('.tf-thumb.tf-skel:not(.ready)').count(), 1, 'a skeleton until the thumb arrives');
    assert.equal(await cutTile.evaluate(el => el.style.width), '128px', 'sized from the version\'s width and height');
    await cutTile.locator('.tf-thumb.ready').waitFor();
    assert(requested.includes('/api/v2/files/f-cut/thumb?v=3'), 'the versioned thumb');
    assert.equal(await modal.locator('.tf-tile[data-tf-file="f-logo"] .tf-thumb img').count(), 0, 'no picture: a type mark');
    thumbDelay = 0;
    // a comment that carried files: a small thumb for the image, a chip for the video at its version
    const m1 = modal.locator('.tcomment', {hasText: 'Second pass and a frame grab.'});
    await m1.locator('.tc-thumb[data-tf-jump="f-frame"]').waitFor();
    assert.equal((await m1.locator('.tc-file[data-tf-jump="f-cut"]').textContent()).replace(/\s+/g, ' ').trim(), 'VIDEOcut.mp4 v2');
    assert.equal(await m1.locator('video, .tf-media').count(), 0, 'no big media in a comment');
    // the linked images in the details show inline, lazily, and open full size
    await modal.locator('.tdesc img[src="https://img.example.com/ref.png"].ready').waitFor();
    assert.equal(await modal.locator('.tdesc > p > img').getAttribute('loading'), 'lazy');
    await modal.locator('.tdesc .inline-thumb.ready').waitFor();
    for (const mode of ['dark', 'light']) { await theme(mode); await shot(`task-files-strip-desktop-${mode}`); }
    await theme('dark');

    // ---- a tile opens in place: the video at v3 with its poster, the versions, who added it and its note
    await m1.locator('.tc-file[data-tf-jump="f-cut"]').click();
    const view = modal.locator('.tf-view');
    await view.locator('video').waitFor();
    assert.equal(await view.locator('video').getAttribute('src'), '/api/v2/files/f-cut?v=2', 'the comment\'s chip opens its version');
    await view.locator('[data-tf-v="3"]').click();
    assert.equal(await view.locator('video').getAttribute('src'), '/api/v2/files/f-cut?v=3');
    assert.equal(await view.locator('video').getAttribute('poster'), '/api/v2/files/f-cut/poster?v=3');
    assert.deepEqual(await view.locator('.tf-vers button').allTextContents(), ['v1', 'v2', 'v3']);
    assert.match(await view.locator('.tf-vmeta').innerText(), /Editor · \d+h ago[\s\S]*Tighter intro, new music\./);
    assert.equal(await cutTile.getAttribute('aria-expanded'), 'true');
    // the file's question: chip, question, options with their description
    const fileAsk = view.locator('.ask');
    assert.match(await fileAsk.locator('.ask-line').innerText(), /ROUGH CUT|Rough cut/);
    assert.equal(await fileAsk.locator('.ask-opt').nth(1).getAttribute('title'), 'Say what to change');
    for (const mode of ['dark', 'light']) { await theme(mode); await shot(`task-video-versions-desktop-${mode}`); }
    await theme('dark');
    // Compare: two versions side by side
    await view.locator('[data-tf-cmp]').click();
    assert.deepEqual(await view.locator('.tf-pair .tf-side-v').allTextContents(), ['v2', 'v3']);
    assert.equal(await view.locator('.tf-pair video').count(), 2);
    await view.locator('[data-tf-with]').selectOption('1');
    assert.deepEqual(await view.locator('.tf-pair .tf-side-v').allTextContents(), ['v1', 'v3']);
    // Esc folds the file, not the task
    await page.keyboard.press('Escape');
    await view.waitFor({state: 'hidden'});
    assert.equal(await modal.isVisible(), true, 'Esc folded the file and left the task open');
    // markdown: compare reads as a line diff
    await modal.locator('.tf-tile[data-tf-file="f-script"]').click();
    await view.locator('.tf-doc .md h1').waitFor();
    await view.locator('[data-tf-cmp]').click();
    await view.locator('.tf-diff-lines').waitFor();
    assert.deepEqual(await view.locator('.tf-diff-lines .del').allTextContents(), ['- Intro line.']);
    assert.deepEqual(await view.locator('.tf-diff-lines .add').allTextContents(), ['+ A tighter intro line.', '+ Scene three.']);
    // CSV: the first 200 rows under a header that stays put
    await modal.locator('.tf-tile[data-tf-file="f-rows"]').click();
    await view.locator('table.csv').waitFor();
    assert.equal(await view.locator('table.csv tbody tr').count(), 200);
    assert.equal(await view.locator('table.csv th').first().evaluate(th => getComputedStyle(th).position), 'sticky');
    // SVG: a download card only
    await modal.locator('.tf-tile[data-tf-file="f-logo"]').click();
    assert.equal(await view.locator('.tf-card a[download]').getAttribute('href'), '/api/v2/files/f-logo?v=1');
    assert.equal(await view.locator('img, iframe, object').count(), 0);
    // images: full size, ←/→ through the task's images
    await modal.locator('.tf-tile[data-tf-file="f-frame"]').click();
    await view.locator('.tf-media.image.ready').waitFor();
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => document.querySelector('#task-modal .tf-view img')?.getAttribute('src') === '/api/v2/files/f-still?v=1');
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => document.querySelector('#task-modal .tf-view img')?.getAttribute('src') === '/api/v2/files/f-frame?v=1');
    await modal.locator('.tf-tile[data-tf-file="f-frame"]').click();
    await view.waitFor({state: 'hidden'});

    // ---- answering: one question, one click; the thread shows the answer comment and the ask folds to its answers
    await cutTile.click();
    await view.locator('.ask .ask-opt', {hasText: 'Approve'}).click();
    await modal.locator('.tcomment', {hasText: 'Ana approved "cut.mp4" v3.'}).waitFor();
    assert.deepEqual(posted.find(x => x.path.endsWith('/answers')).body, {target: {file: 'f-cut', version: 3}, answers: {verdict: ['Approve']}});
    await view.locator('.ask.answered').waitFor();
    assert.match(await view.locator('.ask-answers').innerText(), /You\s*Approve|Ana\s*Approve/);
    assert.equal(await view.locator('.ask .ask-opt').first().isVisible(), false, 'answered: folded to its answers');
    assert.equal(await cutTile.locator('.ask-dot').count(), 0);
    // two questions: multi-select with Other, then Send
    const m2 = modal.locator('.tcomment', {has: page.locator('.ask-q[data-qid="music"]')});
    assert.equal(await m2.locator('.ask-opt .ask-pic').count(), 2, 'variants that point at a file show it');
    await m2.locator('.ask-opt', {hasText: 'Upbeat'}).click();
    await m2.locator('.ask-opt', {hasText: 'Calm'}).click();
    await m2.locator('.ask-opt[data-label="B"]').click();
    await m2.locator('.ask-other').fill('Softer at the end');
    for (const mode of ['dark', 'light']) { await theme(mode); await m2.scrollIntoViewIfNeeded(); await shot(`task-open-ask-desktop-${mode}`); }
    await theme('dark');
    await m2.locator('[data-ask-send]').click();
    await modal.locator('.tcomment', {hasText: 'Ana answered "Which tracks fit?"'}).waitFor();
    assert.deepEqual(posted.filter(x => x.path.endsWith('/answers')).at(-1).body,
      {target: {comment: 'm2'}, answers: {music: ['Upbeat', 'Calm'], thumb: ['B']}, other: 'Softer at the end'});
    assert.match(await m2.locator('.ask-answers').innerText(), /Music: Upbeat, Calm; Thumbnail: B[\s\S]*Softer at the end/);
    // Dismiss
    const m3 = modal.locator('.tcomment', {has: page.locator('.ask-q[data-qid="ship"]')});
    await m3.locator('[data-ask-dismiss]').click();
    await modal.locator('.tcomment', {hasText: 'Ana dismissed the question.'}).waitFor();
    assert.deepEqual(posted.filter(x => x.path.endsWith('/answers')).at(-1).body, {target: {comment: 'm3'}, answers: {}, dismiss: true});
    assert.match(await m3.locator('.ask-answers').innerText(), /dismissed/);
    // the asker sees its own question without buttons
    const m4 = modal.locator('.tcomment', {has: page.locator('.ask-q[data-qid="cap"]')});
    assert.equal(await m4.locator('.ask-opt, .ask-form').count(), 0);
    assert.equal(await m4.locator('.ask-text').innerText(), 'Captions too?');
    assert.deepEqual(errors, []);

    // ---- the modal's poll brings a new file and version; a playing video and a half-filled answer wait for it
    const video = view.locator('video');
    await video.evaluate(v => { v.dataset.keep = '1'; Object.defineProperty(v, 'paused', {configurable: true, get: () => false}); });
    comments.push({id: 'm6', kind: 'ask', from_actor: 'bot:editor', body: 'Which end card?', created: new Date().toISOString(), answers: [], refs: {task: 't1',
      questions: [{id: 'end', question: 'Which end card?', options: [{label: 'Logo'}, {label: 'Link'}], multi: true, other: true}]}});
    await page.evaluate(() => { window.fastPoll = true; });
    const m6 = modal.locator('.tcomment', {has: page.locator('.ask-q[data-qid="end"]')});
    await m6.locator('.ask-opt', {hasText: 'Logo'}).click();
    await m6.locator('.ask-other').fill('Short');
    await page.evaluate(() => document.activeElement.blur());
    files.push({id: 'f-new', name: 'end-card.png', mime: 'image/png', current_version: 1, archived: false,
      versions: [version('f-new', 1, {mime: 'image/png', width: 160, height: 90, thumb_url: url('f-new', 1, '/thumb')})]});
    files[0].versions.unshift(version('f-cut', 4, {mime: 'video/mp4', width: 1920, height: 1080, media_state: 'ready', poster_url: url('f-cut', 4, '/poster'), thumb_url: url('f-cut', 4, '/thumb')}));
    files[0].current_version = 4;
    comments.push({id: 'm7', kind: 'say', from_actor: 'bot:editor', body: 'End card added.', created: new Date().toISOString(), refs: {task: 't1', comment: true}});
    await modal.locator('.tf-tile[data-tf-file="f-new"]').waitFor();
    await page.waitForTimeout(500);       // a few more polls
    assert.equal(await view.locator('video[data-keep="1"]').count(), 1, 'the playing video was left alone');
    assert.equal(await view.locator('[data-tf-v="4"]').count(), 0);
    assert.equal(await m6.locator('.ask-opt[aria-pressed="true"]').count(), 1, 'the picked choice stays');
    assert.equal(await m6.locator('.ask-other').inputValue(), 'Short');
    assert.equal(await modal.locator('.tcomment', {hasText: 'End card added.'}).count(), 0, 'the thread waits for the answer');
    // the answer is cleared and the video stops: the next poll catches up
    await m6.locator('.ask-opt', {hasText: 'Logo'}).click();
    await m6.locator('.ask-other').fill('');
    await page.evaluate(() => document.activeElement.blur());
    await video.evaluate(v => { delete v.paused; });
    await view.locator('[data-tf-v="4"]').waitFor();
    await modal.locator('.tcomment', {hasText: 'End card added.'}).waitFor();
    await page.evaluate(() => { window.fastPoll = false; });
    assert.deepEqual(errors, []);

    // ---- phone width: no sideways scroll, with the strip and an open video
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await cutTile.getAttribute('aria-expanded'), 'true', 'still open after answering');
    await view.locator('video').waitFor();
    await noSideways('phone, task with an open video');
    for (const mode of ['dark', 'light']) { await theme(mode); await modal.evaluate(d => { d.scrollTop = 0; }); await shot(`task-files-strip-phone-${mode}`); await view.scrollIntoViewIfNeeded(); await shot(`task-video-versions-phone-${mode}`); }
    // the multi ask, re-opened for a later answer, at phone width
    await m2.locator('[data-ask-again]').click();
    await noSideways('phone, an open ask');
    for (const mode of ['dark', 'light']) { await theme(mode); await m2.scrollIntoViewIfNeeded(); await shot(`task-open-ask-phone-${mode}`); }
    await modal.locator('[data-modal-close]').click();
    await page.evaluate(() => { TASKS_ST.view = 'board'; tasksRender(TASKS_ST); });
    await noSideways('phone, the board');
    for (const mode of ['dark', 'light']) { await theme(mode); await shot(`board-covers-phone-${mode}`); }

    // ---- a reader (can_comment false): the questions, their choices and answers, no controls and no comment box;
    // an older runner's answer reads as its text, folded
    const answersBefore = posted.filter(x => x.path.endsWith('/answers')).length;
    for (const [width, height, where] of [[390, 844, 'phone'], [1280, 900, 'desktop']]) {
      await page.setViewportSize({width, height});
      await page.evaluate(() => taskModalShow(TASKS_ST.tasks.find(t => t.id === 't2')));
      const legacy = modal.locator('.tcomment', {has: page.locator('.ask-q[data-qid="tone"]')});
      await legacy.locator('.ask.answered').waitFor();
      assert.match(await legacy.locator('.ask-answers').innerText(), /Sam\s*Keep it warm and short\./);
      assert.equal(await legacy.locator('.ask-opt').first().isVisible(), false, 'answered: folded');
      const font = modal.locator('.tcomment', {has: page.locator('.ask-q[data-qid="font"]')});
      assert.equal(await font.locator('.ask-opt.ro').count(), 2);
      assert.equal(await modal.locator('.task-chat .ask button, .task-chat .ask input').count(), 0, 'no answer controls');
      assert.equal(await modal.locator('.task-chat form').isHidden(), true, 'no comment box');
      await font.locator('.ask-opt.ro').first().click();
      await modal.locator('.tf-tile[data-tf-file="f-notes"]').click();
      await view.locator('.ask-q[data-qid="ok"]').waitFor();
      assert.equal(await view.locator('.ask button, .ask input').count(), 0);
      await noSideways(`${where}, a reader's task`);
      for (const mode of ['dark', 'light']) { await theme(mode); await legacy.scrollIntoViewIfNeeded(); await shot(`legacy-answer-${where}-${mode}`); }
      await modal.locator('[data-modal-close]').click();
    }
    assert.equal(posted.filter(x => x.path.endsWith('/answers')).length, answersBefore, 'a reader sends nothing');
    assert.deepEqual(errors, []);
    console.log(`PASS: files strip, viewer, versions, compare, asks, comment files, linked images, covers, pins, phone (${((Date.now() - t0) / 1000).toFixed(1)}s)`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
