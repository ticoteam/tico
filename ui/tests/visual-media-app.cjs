// Video and audio in the real app (Visual replies 3/4): a chat message and a task comment show players in their cards,
// from the sign-in-free visual address only and paused; a person's clip too; video and audio count toward the cap of
// 4; a clip playing in the chat keeps playing, in place, when a live update redraws the thread with freshly signed
// addresses, and takes the new address for later. Every request is intercepted; the clips are WAV files made here.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

function wav(seconds) {
  const rate = 8000, n = rate * seconds, data = Buffer.alloc(n * 2);
  for (let i = 0; i < n; i++) data.writeInt16LE(Math.round(800 * Math.sin(i / 8)), i * 2);
  const h = Buffer.alloc(44);
  h.write('RIFF', 0); h.writeUInt32LE(36 + data.length, 4); h.write('WAVE', 8); h.write('fmt ', 12);
  h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22); h.writeUInt32LE(rate, 24);
  h.writeUInt32LE(rate * 2, 28); h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34); h.write('data', 36); h.writeUInt32LE(data.length, 40);
  return Buffer.concat([h, data]);
}
const CLIP = wav(60);
const PNG = Buffer.from('89504e470d0a1a0a0000000d4948445200000001000000010806000000' + '1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082', 'hex');
const VIS = 'https://visuals.test/visuals/';
const TYPE = {image: 'image/png', video: 'video/mp4', audio: 'audio/wav'};
const A = (id, kind, over = {}) => ({id, file_id: id, version: 1, ref: id + '@1', title: 'Title ' + id, kind, name: id + {image: '.png', video: '.mp4', audio: '.wav'}[kind],
  size: CLIP.length, content_type: TYPE[kind], url: `/api/v2/files/${id}?v=1`, removed: false, current_version: 1, src: VIS + 'tok-' + id, ...over});

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined,
    headless: true, args: ['--autoplay-policy=no-user-gesture-required']});
  const now = new Date().toISOString();
  try {
    const context = await browser.newContext({viewport: {width: 390, height: 844}, hasTouch: true, isMobile: true, serviceWorkers: 'block'});
    await context.addCookies([{name: 'tico_session', value: 'secret', domain: 'tico-ui.test', path: '/', secure: true, httpOnly: true}]);
    const page = await context.newPage();
    const errors = [], visualReqs = [], fileReqs = [], held = [];
    page.on('pageerror', e => errors.push(e.message));
    const messages = [
      {id: 'm1', sender_id: 'bot:editor', body: 'The cut and the voice note.', created_at: now, refs: {attachments: [
        A('f-cut', 'video', {width: 1280, height: 720, poster_url: VIS + 'tok-f-cut-poster'}), A('f-voice', 'audio')]}},
      {id: 'm2', sender_id: 'human:ana', body: 'My recording', created_at: now, refs: {attachments: [A('f-mine', 'audio', {title: 'Ana recording'})]}},
      {id: 'm3', sender_id: 'bot:editor', body: 'Five.', created_at: now, refs: {attachments: [
        A('f-p1', 'image', {width: 400, height: 200}), A('f-a1', 'audio'), A('f-p2', 'image', {width: 400, height: 200}), A('f-v1', 'video'), A('f-a2', 'audio')]}},
    ];
    let gen = 1;
    const signed = () => gen === 1 ? messages : messages.map(m => ({...m, refs: {...m.refs, attachments: m.refs.attachments.map(a => ({...a, src: `${a.src}.${gen}`}))}}));
    await context.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      if (url.hostname === 'visuals.test') {
        visualReqs.push({path: p, cookie: req.headers().cookie || '', range: req.headers().range || ''});
        const headers = {'Cross-Origin-Resource-Policy': 'cross-origin', 'Accept-Ranges': 'bytes'};
        if (p.endsWith('-poster') || /tok-f-p\d/.test(p)) return route.fulfill({contentType: 'image/png', headers, body: PNG});
        const m = /bytes=(\d+)-(\d*)/.exec(req.headers().range || '');
        if (!m) return route.fulfill({contentType: 'audio/wav', headers, body: CLIP});
        const start = Number(m[1]), end = Math.min(m[2] ? Number(m[2]) : CLIP.length - 1, start + 65535, CLIP.length - 1);
        return route.fulfill({status: 206, contentType: 'audio/wav', headers: {...headers, 'Content-Range': `bytes ${start}-${end}/${CLIP.length}`}, body: CLIP.subarray(start, end + 1)});
      }
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.startsWith('/api/v2/files/')) fileReqs.push(p + url.search);
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
      if (p === '/api/employees') return json([{name: 'editor', display_name: 'Editor', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
      if (p === '/api/issues') return json([]);
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p === '/api/v2/events') { held.push(route); return; }
      if (p === '/api/v2/conversations') return json({conversations: url.searchParams.get('chat_with') ? [{id: 'c-editor', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:editor']}] : []});
      if (p === '/api/v2/conversations/c-editor/snapshot') return json({messages: signed(), execution: null});
      if (p === '/api/v2/tasks' && req.method() === 'GET') return json({tasks: [task], next_offset: null});
      if (p === '/api/v2/tasks/t1/files') return json({files: []});
      if (p === '/api/v2/tasks/t1') return json({task, comments: taskComments, events: [], children: [], parent: null, mover: true, messages: [], can_comment: true});
      return json(unrouted(p));
    });
    const task = {id: 't1', title: 'Launch video', owner_id: 'bot:editor', requester_id: 'human:ana', status: 'review', lane: 'company', rank: 1, labels: [], links: [],
      parts: {total: 0, done: 0}, version: 1, created_at: now, updated_at: now, body: 'Cut it.', attachments: []};
    const taskComments = [{id: 'c1', kind: 'say', sender_id: 'bot:editor', body: 'Cut attached.', created_at: now, answers: [],
      refs: {task: 't1', comment: true, attachments: [A('f-cut', 'video', {width: 1280, height: 720})]}}];

    // ---- chat: players in the cards, from the visual address, paused; a person's clip too
    await page.goto('https://tico-ui.test/#/bot/editor/chat');
    const m1 = page.locator('[data-message="m1"]');
    await m1.locator('figure.vcard audio').waitFor();
    const video = m1.locator('figure.vcard[data-kind="video"] video'), voice = m1.locator('figure.vcard[data-kind="audio"] audio');
    assert.equal(await video.getAttribute('src'), VIS + 'tok-f-cut');
    assert.equal(await video.getAttribute('poster'), VIS + 'tok-f-cut-poster');
    assert.equal(await voice.getAttribute('src'), VIS + 'tok-f-voice');
    assert.deepEqual(await m1.locator('.vcard-title').allTextContents(), ['Title f-cut', 'Title f-voice']);
    assert.equal(await page.locator('[data-message="m2"] figure.vcard[data-kind="audio"] .vcard-title').textContent(), 'Ana recording', 'a person\'s clip is a card too');
    await page.waitForTimeout(300);
    assert.ok(await page.evaluate(() => [...document.querySelectorAll('figure.vcard video, figure.vcard audio')].every(m => m.paused && !m.autoplay)), 'nothing plays by itself');
    // the cap of 4 counts video and audio with pictures: the fifth is a chip
    const m3 = page.locator('[data-message="m3"]');
    assert.deepEqual(await m3.locator('figure.vcard').evaluateAll(els => els.map(e => e.dataset.kind)), ['image', 'audio', 'image', 'video']);
    assert.equal(await m3.locator('.chat-files > *').count(), 1, 'the fifth visual is a file chip');
    const r = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
    assert.ok(r[0] <= r[1], `no sideways scroll at phone width ${r}`);

    // ---- a live update redraws the thread with newly signed addresses: the playing clip keeps playing, in place, where
    // it was. Both ways the frame redraws: moving the card in place (moveBefore), and where a browser has no
    // moveBefore (Safari, the desktop app's webviews), taking it out and putting it back.
    await voice.evaluate(m => m.play());
    await voice.evaluate(m => { m.dataset.mark = 'kept'; });
    assert.equal(await page.evaluate(() => typeof Element.prototype.moveBefore), 'function', 'this browser moves nodes in place');
    let answered = 0;
    for (const [way, at, id] of [['moveBefore', 20, 'm4'], ['no moveBefore', 35, 'm5']]) {
      await voice.evaluate((m, at) => new Promise(r => { m.addEventListener('seeked', r, {once: true}); m.currentTime = at; }), at);
      for (let i = 0; held.length <= answered && i < 100; i++) await page.waitForTimeout(50);
      assert.ok(held.length > answered, 'the page follows the live event stream (and reconnects after each event)');
      const stream = held.at(-1);
      answered = held.length;
      if (way === 'no moveBefore') await page.evaluate(() => { window.__mb = Element.prototype.moveBefore; delete Element.prototype.moveBefore; });
      gen += 1;
      messages.push({id, sender_id: 'bot:editor', body: 'One more.', created_at: now, refs: {attachments: [A('f-more-' + id, 'audio')]}});
      const seq = 8 + gen;
      await stream.fulfill({contentType: 'text/event-stream', body: `id: ${seq}\nevent: messages\ndata: ${JSON.stringify({seq, conversation_id: 'c-editor'})}\n\n`});
      await page.locator(`[data-message="${id}"] figure.vcard audio`).waitFor();
      const kept = await page.evaluate(() => { const m = document.querySelector('[data-message="m1"] figure.vcard[data-kind="audio"] audio');
        return [m.dataset.mark, m.currentTime, m.paused]; });
      if (way === 'no moveBefore') await page.evaluate(() => { Element.prototype.moveBefore = window.__mb; });
      assert.equal(kept[0], 'kept', `${way}: the playing audio element survived the redraw`);
      assert.ok(kept[1] >= at && !kept[2], `${way}: right after the live redraw it is at ${kept[1]}s (not before ${at}s) and playing`);
      assert.equal(await m1.locator('figure.vcard[data-kind="audio"]').getAttribute('data-fresh-src'), `${VIS}tok-f-voice.${gen}`, 'it holds the new address for later');
      await page.waitForFunction(t => { const m = document.querySelector('audio[data-mark="kept"]'); return m && !m.paused && m.currentTime > t; }, kept[1]);
    }
    // starting the new clip pauses it
    await page.locator('[data-message="m5"] figure.vcard audio').evaluate(m => m.play());
    await page.waitForFunction(() => document.querySelector('audio[data-mark="kept"]').paused);
    await page.locator('[data-message="m5"] figure.vcard audio').evaluate(m => m.pause());

    // ---- task comments: the same player
    await page.goto('https://tico-ui.test/#/tasks?view=board');
    await page.waitForFunction(() => typeof TASKS_ST !== 'undefined' && TASKS_ST && !TASKS_ST.loading);
    await page.evaluate(() => taskModalShow(TASKS_ST.tasks.find(t => t.id === 't1')));
    const c1 = page.locator('#task-modal .tcomment', {hasText: 'Cut attached.'});
    await c1.locator('figure.vcard[data-kind="video"] video').waitFor();
    assert.equal(await c1.locator('figure.vcard video').getAttribute('src'), VIS + 'tok-f-cut');
    assert.equal(await c1.locator('figure.vcard video').getAttribute('preload'), 'metadata');
    assert.equal(await c1.locator('.tc-thumb, .tc-file').count(), 0, 'a clip shown as a card is not also a chip');

    // ---- media never read the signed-in file route nor carried the app's cookie; every media read is a range read
    assert.deepEqual(fileReqs, [], 'no card read /api/v2/files');
    assert.ok(visualReqs.length && visualReqs.every(q => !q.cookie), 'no visual request carried a cookie');
    assert.ok(visualReqs.filter(q => q.path.includes('tok-f-') && !/poster|tok-f-p\d/.test(q.path)).every(q => q.range), 'media are read by range');
    assert.deepEqual(errors, []);
    await context.close();
    console.log('visual media in the app ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
