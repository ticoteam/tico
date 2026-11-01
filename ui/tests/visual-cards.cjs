// Visuals in replies (Visual replies 2/4): pictures, SVG and visual pages show as cards under a chat message and a
// task comment, from the sign-in-free visual address only; pages run in the agreed sandbox with the server's real
// content policy and height reporter (backend/visuals.py); cap of 4, removed, newer version, failure, Expand, phone.
// Fixtures only.
const {chromium, webkit} = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const fs = require('node:fs');

const ROOT = path.resolve(__dirname, '..', '..');
// The server's own page treatment: cleaned, the height reporter added, and its content policy.
const server = JSON.parse(execFileSync('python3', ['-I', '-c', `
import json, sys
sys.path.insert(0, ${JSON.stringify(ROOT)})
from backend import visuals
pages = json.loads(sys.stdin.read())
out = {"policy": visuals.policy("page", ["https://tico-ui.test"]), "svg_policy": visuals.policy("svg", ["https://tico-ui.test"]), "pages": {}}
for name, (raw, clean) in pages.items():
    data = raw.encode()
    if clean:
        data, _ = visuals.clean(data, "page")
    out["pages"][name] = visuals.with_reporter(data).decode()
print(json.dumps(out))
`], {input: JSON.stringify({
  short: ['<!doctype html><html><head><title>Short</title></head><body style="margin:0"><h1 style="margin:0;height:150px">Short page</h1>'
    + '<a id="out" href="https://outside.test/read">Read more</a></body></html>', true],
  tall: ['<!doctype html><html><body style="margin:0"><div style="height:2000px;background:linear-gradient(#0a0,#00a)">Tall page</div></body></html>', true],
  // Sized by the screen, not by their content: one as tall as the frame, one always taller than it (a heading, then
  // min-height 100dvh, inside the default margins).
  vh: ['<!doctype html><html><body style="margin:0"><div style="height:100vh;overflow:hidden;background:#0a0">Full screen</div></body></html>', true],
  // Sized by their content: one under the 24px minimum, one between the phone cap and the desktop cap.
  // Sized by its content and used for nothing else: the clamp and zero-width checks post fake heights to it.
  clamp: ['<!doctype html><html><body style="margin:0"><div style="height:120px;background:#eee">Plain page</div></body></html>', true],
  tiny: ['<!doctype html><html><body style="margin:0"><div style="height:10px;font-size:8px;line-height:10px">Hi</div></body></html>', true],
  mid: ['<!doctype html><html><body style="margin:0"><div style="height:400px;background:#eee">Mid page</div></body></html>', true],
  dvh: ['<!doctype html><html><body><h1>Report</h1><main style="min-height:100dvh;background:#00a;color:#fff">Fills the screen</main></body></html>', true],
  // Not cleaned on purpose: the frame's sandbox and the content policy alone must stop all of it.
  hostile: ['<!doctype html><html><body style="margin:0"><p style="height:90px">Hostile page</p>'
    + '<script>parent.postMessage({ticoVisualHeight: 5}, "*"); fetch("https://evil.test/fetch"); window.open("https://evil.test/popup");'
    + 'try { document.cookie = "x=1"; localStorage.setItem("x", "1"); parent.document.body.dataset.owned = "yes" } catch (e) {}</script>'
    + '<img src="https://evil.test/pixel.png"><iframe src="https://evil.test/frame"></iframe><img src=x onerror="fetch(\'https://evil.test/onerror\')">'
    + '<form id="f" action="https://evil.test/form"><button id="go">Go</button></form></body></html>', false],
}), encoding: 'utf8'}));

const PNG = Buffer.from('89504e470d0a1a0a0000000d4948445200000001000000010806000000' + '1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082', 'hex');
const SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"><text x="10" y="60" font-size="40">Hi</text></svg>';
const VIS = 'https://visuals.test/visuals/';
// A 60 s silent clip (8 kHz, 8-bit mono WAV), for the redraw check on a playing clip.
const CLIP = (() => {
  const n = 8000 * 60, b = Buffer.alloc(44 + n, 0x80);
  b.write('RIFF', 0); b.writeUInt32LE(36 + n, 4); b.write('WAVEfmt ', 8); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22);
  b.writeUInt32LE(8000, 24); b.writeUInt32LE(8000, 28); b.writeUInt16LE(1, 32); b.writeUInt16LE(8, 34); b.write('data', 36); b.writeUInt32LE(n, 40);
  return b;
})();
const A = (id, kind, over = {}) => ({id, file_id: id, version: 1, ref: id + '@1', title: 'Title ' + id, kind, name: id + ({image: '.png', svg: '.svg', page: '.html'}[kind] || ''),
  size: 1000, content_type: {image: 'image/png', svg: 'image/svg+xml', page: 'text/html'}[kind], url: `/api/v2/files/${id}?v=1`, removed: false,
  current_version: 1, src: VIS + 'tok-' + id, ...over});

(async () => {
  const browser = process.env.TICO_BROWSER === 'webkit' ? await webkit.launch({headless: true})
    : await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined, headless: true});
  const now = new Date().toISOString();
  const MESSAGES = [
    {id: 'm1', sender_id: 'bot:designer', body: 'Here is the chart, the logo and two pages.', created_at: now, refs: {attachments: [
      A('f-chart', 'image', {width: 800, height: 400}), A('f-logo', 'svg'), A('f-short', 'page'), A('f-tall', 'page')]}},
    {id: 'm2', sender_id: 'bot:designer', body: 'Six pictures.', created_at: now, refs: {attachments:
      [1, 2, 3, 4, 5, 6].map(i => A('f-six' + i, 'image', {width: 400, height: 200}))}},
    {id: 'm3', sender_id: 'bot:designer', body: 'Older ones.', created_at: now, refs: {attachments: [
      A('f-moved', 'image', {width: 400, height: 200, current_version: 3}), A('f-gone', 'page', {removed: true, src: undefined, current_version: null}),
      A('f-bad', 'page'), A('f-nosrc', 'image', {src: null, width: 400, height: 200}), A('f-hostile', 'page')]}},
    {id: 'm4', sender_id: 'human:ana', body: 'My photo', created_at: now, refs: {attachments: [A('f-mine', 'image', {width: 300, height: 300, title: 'Ana photo'})]}},
    {id: 'm5', sender_id: 'bot:designer', body: 'Two full-screen pages.', created_at: now, refs: {attachments: [A('f-vh', 'page'), A('f-dvh', 'page'), A('f-tiny', 'page'), A('f-mid', 'page')]}},
    {id: 'm7', sender_id: 'bot:designer', body: 'A plain page.', created_at: now, refs: {attachments: [A('f-clamp', 'page')]}},
  ];
  const run = async ({viewport, hasTouch = false, label}) => {
    const context = await browser.newContext({viewport, hasTouch, isMobile: hasTouch, serviceWorkers: 'block'});
    await context.addCookies([{name: 'tico_session', value: 'secret', domain: 'tico-ui.test', path: '/', secure: true, httpOnly: true}]);
    const page = await context.newPage();
    const errors = [], outside = [], visualReqs = [], fileReqs = [], held = [];
    // The snapshot as the server sends it: each read signs every visual's address afresh (gen), as on a live update.
    const messages = [...MESSAGES];
    let hold = null, gen = 1;
    const signed = () => gen === 1 ? messages : messages.map(m => ({...m, refs: {...m.refs, attachments: m.refs.attachments.map(a => a.src ? {...a, src: `${a.src}.${gen}`} : a)}}));
    page.on('pageerror', e => { if (!/sandboxed/i.test(e.message)) errors.push(e.message); });
    await context.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      if (url.hostname === 'evil.test') { outside.push(url.href); return route.fulfill({contentType: 'text/plain', body: 'no'}); }
      if (url.hostname === 'outside.test') return route.fulfill({contentType: 'text/html', body: '<p>Outside</p>'});
      if (url.hostname === 'visuals.test') {
        visualReqs.push({path: p, cookie: req.headers().cookie || ''});
        const id = p.replace('/visuals/tok-', '').replace(/\.\d+$/, '');
        if (hold && id === 'f-chart') await hold;
        const headers = {'Cross-Origin-Resource-Policy': 'cross-origin', 'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff'};
        if (id === 'f-bad' || id === 'f-gone') return route.fulfill({status: 404, headers: {...headers, 'Content-Security-Policy': "default-src 'none'; sandbox"}, body: ''});
        const page = {'f-short': 'short', 'f-tall': 'tall', 'f-hostile': 'hostile', 'f-vh': 'vh', 'f-dvh': 'dvh', 'f-tiny': 'tiny', 'f-mid': 'mid', 'f-clamp': 'clamp', 'f-live-page': 'short'}[id];
        if (page) return route.fulfill({contentType: 'text/html', headers: {...headers, 'Content-Security-Policy': server.policy}, body: server.pages[page]});
        if (id === 'f-clip') {
          const m = /bytes=(\d+)-(\d*)/.exec(req.headers().range || ''), from = m ? +m[1] : 0, to = m && m[2] ? +m[2] : CLIP.length - 1;
          return route.fulfill({status: m ? 206 : 200, contentType: 'audio/wav', body: CLIP.subarray(from, to + 1),
            headers: {...headers, 'Accept-Ranges': 'bytes', ...(m ? {'Content-Range': `bytes ${from}-${to}/${CLIP.length}`} : {})}});
        }
        if (id === 'f-logo') return route.fulfill({contentType: 'image/svg+xml', headers: {...headers, 'Content-Security-Policy': server.svg_policy}, body: SVG});
        return route.fulfill({contentType: 'image/png', headers, body: PNG});
      }
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.startsWith('/api/v2/files/')) fileReqs.push(p + url.search);
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
      if (p === '/api/employees') return json([{name: 'designer', display_name: 'Designer', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
      if (p === '/api/issues') return json([]);
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p === '/api/v2/events') { held.push(route); return; }      // live events (ui/app/live.js), held open
      if (p === '/api/v2/conversations') return json({conversations: url.searchParams.get('chat_with') ? [{id: 'c-designer', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:designer']}] : []});
      if (p === '/api/v2/conversations/c-designer/snapshot') return json({messages: signed(), execution: null});
      if (p === '/api/v2/tasks' && req.method() === 'GET') return json({tasks: [task], next_offset: null});
      if (p === '/api/v2/tasks/t1/files') return json({files: taskFiles});
      if (p === '/api/v2/tasks/t1') return json({task, comments: taskComments, events: [], children: [], parent: null, mover: true, messages: [], can_comment: true});
      return json(unrouted(p));
    });
    const task = {id: 't1', title: 'Launch page', owner_id: 'bot:designer', requester_id: 'human:ana', status: 'review', lane: 'company', rank: 1, labels: [], links: [],
      parts: {total: 0, done: 0}, version: 1, created_at: now, updated_at: now, body: 'Make the page.', attachments: []};
    const tver = (id, mime) => ({n: 1, size: 1000, mime, sha256: 'x', created_at: now, by: 'bot:designer', comment_id: 'c1', note: null, ask: null, answers: [],
      width: null, height: null, duration_ms: null, media_state: 'none', url: `/api/v2/files/${id}?v=1`, poster_url: null, thumb_url: null});
    const taskFiles = [{id: 'f-chart', name: 'f-chart.png', mime: 'image/png', current_version: 1, archived: false, versions: [tver('f-chart', 'image/png')]},
      {id: 'f-short', name: 'f-short.html', mime: 'text/html', current_version: 1, archived: false, versions: [tver('f-short', 'text/html')]}];
    const taskComments = [{id: 'c1', kind: 'say', sender_id: 'bot:designer', body: 'Chart and page attached.', created_at: now, answers: [],
      refs: {task: 't1', comment: true, attachments: [A('f-chart', 'image', {width: 800, height: 400}), A('f-short', 'page')]}}];

    const noSideways = async where => {
      const r = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
      assert(r[0] <= r[1], `${label} ${where}: the page scrolls sideways ${r}`);
    };
    // ---- chat: hold the chart so its held space can be measured before it loads
    let release; hold = new Promise(r => { release = r; });
    await page.goto('https://tico-ui.test/#/bot/designer/chat');
    const m1 = page.locator('[data-message="m1"]');
    await m1.locator('figure.vcard').nth(3).waitFor();
    const chart = m1.locator('figure.vcard[data-kind="image"] .vcard-body');
    const before = await chart.evaluate(el => [el.getBoundingClientRect().width, el.getBoundingClientRect().height]);
    // a picture is a quarter-column thumbnail (Dev 338): 76x38 on a phone
    assert(before[1] > 20 && Math.abs(before[1] - before[0] / 2) < 2, `${label}: the chart holds its 2:1 space before it loads ${before}`);
    release(); hold = null;
    await m1.locator('figure.vcard[data-kind="image"] .vcard-body.ready').waitFor();
    const after = await chart.evaluate(el => el.getBoundingClientRect().height);
    assert(Math.abs(after - before[1]) < 1, `${label}: no jump when the chart arrives ${before[1]} -> ${after}`);
    assert.deepEqual(await m1.locator('figure.vcard').evaluateAll(els => els.map(e => e.dataset.kind)), ['image', 'svg', 'page', 'page'], 'in attach order');
    assert.deepEqual(await m1.locator('.vcard-title').allTextContents(), ['Title f-chart', 'Title f-logo', 'Title f-short', 'Title f-tall'], 'caption = title');
    assert.equal(await m1.locator('img.vcard-img').first().getAttribute('alt'), 'Title f-chart', 'the title is the accessible description');
    assert.equal(await m1.locator('iframe.vcard-frame').first().getAttribute('title'), 'Title f-short');
    for (const src of await page.locator('figure.vcard img.vcard-img, figure.vcard iframe').evaluateAll(els => els.map(e => e.getAttribute('src'))))
      assert(src.startsWith(VIS), `${label}: a card shows only from the visual address, not ${src}`);
    // pages: the agreed sandbox; a short page at its own height with no scrollbar; a tall one cut at the cap
    for (const s of await page.locator('iframe.vcard-frame').evaluateAll(els => els.map(e => e.getAttribute('sandbox'))))
      assert.equal(s, 'allow-scripts allow-popups allow-popups-to-escape-sandbox');
    const short = m1.locator('figure.vcard[data-file="f-short"]'), tall = m1.locator('figure.vcard[data-file="f-tall"]');
    await short.locator('.vcard-body.ready').waitFor();
    await tall.locator('.vcard-body.ready').waitFor();
    const sh = await short.evaluate(c => [c.querySelector('.vcard-body').getBoundingClientRect().height, c.querySelector('iframe').getBoundingClientRect().height, c.classList.contains('capped')]);
    assert(sh[0] >= 160 && sh[0] < 260 && Math.abs(sh[0] - sh[1]) < 1 && !sh[2], `${label}: a short page shows at its own height ${sh}`);
    const cap = viewport.width > 760 ? 480 : Math.round(viewport.height * 0.6);
    const th = await tall.evaluate(c => [c.querySelector('.vcard-body').getBoundingClientRect().height, c.querySelector('iframe').getBoundingClientRect().height, c.classList.contains('capped')]);
    assert(Math.abs(th[0] - cap) <= 1 && th[1] >= 2000 && th[2], `${label}: a tall page is cut at the cap ${th} (cap ${cap})`);
    assert(await tall.locator('.vcard-expand').isVisible(), 'Expand on a cut page');
    // a height message from anywhere but the card's own frame is ignored
    await page.evaluate(() => { window.postMessage({ticoVisualHeight: 7}, '*'); for (const f of document.querySelectorAll('iframe')) f.dispatchEvent(new MessageEvent('message', {data: {ticoVisualHeight: 7}})); });
    await page.waitForTimeout(100);
    assert(Math.abs(await short.evaluate(c => c.querySelector('iframe').getBoundingClientRect().height) - sh[1]) < 1, 'only the frame itself sets its height');
    // dark mode: SVG and pages sit on a light card
    await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'));
    await m1.locator('figure.vcard[data-kind="svg"] .vcard-body.ready').waitFor();
    for (const kind of ['svg', 'page']) assert.equal(await m1.locator(`figure.vcard[data-kind="${kind}"] .vcard-body`).first().evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(255, 255, 255)');
    // cap of 4: the other two are chips
    const m2 = page.locator('[data-message="m2"]');
    assert.equal(await m2.locator('figure.vcard').count(), 4);
    assert.equal(await m2.locator('.chat-files > *').count(), 2, 'visuals 5 and 6 are file chips');
    // newer version, removed, failed, no address (chip), hostile page
    const m3 = page.locator('[data-message="m3"]');
    assert(await m3.locator('figure.vcard[data-file="f-moved"] .vcard-newer').isVisible(), 'Newer version link');
    assert.equal((await m3.locator('figure.vcard[data-file="f-gone"] .vcard-body').innerText()).trim(), 'This visual was removed');
    await m3.locator('figure.vcard[data-file="f-bad"][data-state="failed"]').waitFor({timeout: 10000});
    assert.match(await m3.locator('figure.vcard[data-file="f-bad"] .vcard-body').innerText(), /Couldn't show this visual\s+Open in file viewer/);
    assert.equal(await m3.locator('figure.vcard[data-file="f-nosrc"]').count(), 0, 'no visual address: a chip');
    assert.equal(await m3.locator('.chat-files .chat-thumb, .chat-files .pill').count(), 1);
    const hostile = m3.locator('figure.vcard[data-file="f-hostile"]');
    await hostile.locator('.vcard-body.ready').waitFor();
    const hh = await hostile.evaluate(c => c.querySelector('iframe').getBoundingClientRect().height);
    assert(hh >= 90, `${label}: the page's own height message never runs (got ${hh})`);
    // people's attachments too
    assert.equal(await page.locator('[data-message="m4"] figure.vcard img').getAttribute('alt'), 'Ana photo');
    // a page sized by the screen (100vh, 100dvh) settles at the cap with Expand: no sliver, no grow/shrink loop
    // between the reporter and the frame (lead, Oct 19: a known limit; bot builders size pages by content)
    const m5 = page.locator('[data-message="m5"]');
    for (const id of ['f-vh', 'f-dvh']) {
      const card = m5.locator(`figure.vcard[data-file="${id}"]`);
      await card.scrollIntoViewIfNeeded();                         // a frame off screen is laid out once it is scrolled to
      await m5.locator(`figure.vcard.capped[data-file="${id}"] .vcard-body.ready`).waitFor({timeout: 5000});
      const size = () => card.evaluate(c => [c.querySelector('.vcard-body'), c.querySelector('iframe')].map(e => Math.round(e.getBoundingClientRect().height)));
      const first = await size();
      await page.waitForTimeout(600);
      assert.deepEqual(await size(), first, `${label} ${id}: the height settles`);
      assert(Math.abs(first[0] - cap) <= 1 && Math.abs(first[1] - cap) <= 1, `${label} ${id}: shown at the cap ${first} (cap ${cap})`);
      assert(await card.locator('.vcard-expand').isVisible(), `${label} ${id}: Expand is offered`);
    }
    // a page sized by its content stays its own size, however small: the 24px minimum, not the cap (Review 1, item 1)
    const pageLook = card => card.evaluate(c => [Math.round(c.querySelector('.vcard-body').getBoundingClientRect().height), c.classList.contains('capped'),
      getComputedStyle(c.querySelector('.vcard-expand')).display !== 'none', getComputedStyle(c.querySelector('.vcard-fade')).display !== 'none']);
    const tiny = m5.locator('figure.vcard[data-file="f-tiny"]'), mid = m5.locator('figure.vcard[data-file="f-mid"]'), vhCard = m5.locator('figure.vcard[data-file="f-vh"]');
    await tiny.scrollIntoViewIfNeeded();
    await tiny.locator('.vcard-body.ready').waitFor();
    await page.waitForTimeout(600);                                // its frame resized: any answer has come
    assert.deepEqual(await pageLook(tiny), [24, false, false, false], `${label}: a tiny content page is not taken for a full-screen one`);
    // the cap follows the screen: the same card is cut, and offers Expand, or not, as the window changes (item 3)
    if (!hasTouch) {
      await mid.locator('.vcard-body.ready').waitFor();
      assert.deepEqual(await pageLook(mid), [400, false, false, false], `${label}: a 400px page under the 480px cap`);
      await page.setViewportSize({width: 390, height: 500});
      await page.waitForTimeout(300);
      assert.deepEqual(await pageLook(mid), [300, true, true, true], `${label}: the same page under a 300px phone cap is cut, with fade and Expand`);
      assert.deepEqual((await pageLook(vhCard)).slice(0, 2), [300, true], `${label}: a full-screen page follows the cap down`);
      await page.setViewportSize(viewport);
      await page.waitForTimeout(300);
      assert.deepEqual(await pageLook(mid), [400, false, false, false], `${label}: back under the 480px cap, whole again`);
      assert.deepEqual((await pageLook(vhCard)).slice(0, 2), [480, true], `${label}: a full-screen page follows the cap up`);
    }
    // guards (item 4): only the visual route is ever a card's address
    assert.deepEqual(await page.evaluate(() => ['https://visuals.test/visuals/tok-a', '/api/v2/files/f-chart?v=1', 'https://visuals.test/api/v2/files/f?v=1',
      'blob:https://tico-ui.test/0f0e', 'data:image/png;base64,AA', 'javascript:alert(1)', 'https://visuals.test/visuals/tok?x=1',
      'https://visuals.test/visuals/a/b', 'ftp://visuals.test/visuals/tok', 'not a url', 42].map(src => visualSrc({src}))),
      ['https://visuals.test/visuals/tok-a', null, null, null, null, null, null, null, null, null, null], `${label}: visualSrc takes only /visuals/<token>`);
    assert.equal(await page.evaluate(() => visualCardsHTML([{file_id: 'f', version: 1, kind: 'image', src: '/api/v2/files/f?v=1'}], {chips: rest => `chips:${rest.length}`})),
      'chips:1', 'the app file route is a chip, never a card');
    // a card is kept only while its key, state and newest version all stay the same
    assert.deepEqual(await page.evaluate(() => [
      ['data-state="ready"', 'data-state="removed"'], ['data-state="failed"', 'data-state="ready"'], ['data-state="ready"', 'data-state="ready" data-current="2"'],
      ['data-state="ready" data-current="2"', 'data-state="ready" data-current="3"'], ['data-state="ready"', 'data-state="ready"'],
    ].map(([a, b]) => {
      const one = document.createElement('div'), two = document.createElement('div');
      one.innerHTML = `<figure class="vcard" data-visual-key="m:f@1" ${a}></figure>`; two.innerHTML = `<figure class="vcard" data-visual-key="m:f@1" ${b}></figure>`;
      const was = one.firstChild; visualKeep(one, two); return two.firstChild === was;
    })), [false, false, false, false, true], `${label}: a same-key card whose state or newest version changed is redrawn`);
    // a height from the card's own frame: a number, finite, clamped to 24..20000
    // on a plain page of its own: a page already taken for a screen-sized one (the hostile page was, on the hosted
    // run) follows no reports at all, so it would prove nothing here
    console.log(`${label}: the hostile page settled ${await hostile.evaluate(c => { const f = c.querySelector('iframe'); return `${f.visualFill ? 'as screen-sized (at the cap)' : 'by its content'}, ${f.visualHeight}px`; })}`);
    const plain = page.locator('[data-message="m7"] figure.vcard[data-file="f-clamp"]');
    await plain.scrollIntoViewIfNeeded();
    await plain.locator('.vcard-body.ready').waitFor();
    assert.equal(await plain.evaluate(c => c.querySelector('iframe').visualFill || false), false, `${label}: the plain page is sized by its content`);
    const plainFrame = page.frames().find(f => f.url().includes('tok-f-clamp'));
    const frameH = () => plain.evaluate(c => [c.querySelector('iframe').visualHeight, Math.round(c.querySelector('iframe').getBoundingClientRect().height)]);
    const settled = await frameH();
    for (const v of ["'5000'", 'NaN', 'Infinity', '-Infinity', 'null', '{}', '[900]']) await plainFrame.evaluate(`parent.postMessage({ticoVisualHeight: ${v}}, '*')`);
    await page.waitForTimeout(200);
    assert.deepEqual(await frameH(), settled, `${label}: a height that is not a finite number is ignored, even from the card's own frame`);
    // the height a message sets is the one recorded first: the page then answers its new size with its real height
    // (1/4's reporter reports again on resize), which it settles at. Each check waits until the page has stopped
    // answering (no report for 500 ms), so a late answer cannot land in the next one.
    await page.evaluate(() => { window.vhReports = 0; addEventListener('message', ev => { if (ev.data && 'ticoVisualHeight' in ev.data) window.vhReports++; }); });
    const quiet = () => page.evaluate(() => new Promise(done => { let n = -1; const t = setInterval(() => { if (window.vhReports === n) { clearInterval(t); done(); } n = window.vhReports; }, 500); }));
    const clampTo = async v => {
      await plain.evaluate(c => { const f = c.querySelector('iframe'); f.seen = []; f.mo = new MutationObserver(() => f.seen.push(f.style.height)); f.mo.observe(f, {attributes: true, attributeFilter: ['style']}); });
      await plainFrame.evaluate(`parent.postMessage({ticoVisualHeight: ${v}}, '*')`);
      await quiet();
      return plain.evaluate(c => { const f = c.querySelector('iframe'); f.mo.disconnect(); return f.seen[0]; });
    };
    assert.equal(await clampTo('1e9'), '20000px', `${label}: a huge height is clamped to 20000`);
    assert.equal(await clampTo('-50'), '24px', `${label}: a negative height is clamped to 24`);
    // a frame shown at no width measures nothing real: its report is dropped
    const shown = await plain.evaluate(c => c.querySelector('iframe').visualHeight);
    await plain.evaluate(c => { c.style.display = 'none'; });
    await plainFrame.evaluate("parent.postMessage({ticoVisualHeight: 900}, '*')");
    await page.waitForTimeout(200);
    const hidden = await plain.evaluate(c => c.querySelector('iframe').visualHeight);
    await plain.evaluate(c => { c.style.display = ''; });
    assert.equal(hidden, shown, `${label}: a height from a frame shown at no width is dropped`);
    await noSideways('chat');

    // ---- a link in a page opens a new tab only when clicked; the conversation stays
    if (!hasTouch) {
      const where = page.url();
      const [tab] = await Promise.all([context.waitForEvent('page'), short.frameLocator('iframe').locator('#out').click()]);
      await tab.waitForLoadState();
      assert.equal(tab.url(), 'https://outside.test/read');
      await tab.close();
      assert.equal(page.url(), where, 'the conversation stays');
    }

    // ---- Expand: full screen, zoom, Esc / swipe down back to the same place
    const thread = page.locator('#conv-thread');
    await m1.locator('figure.vcard[data-kind="image"] .vcard-hit').click();
    const full = page.locator('#vcard-full[open]');
    await full.locator('img.vcard-full-img').waitFor();
    assert.equal(await full.locator('img').getAttribute('src'), VIS + 'tok-f-chart');
    await full.locator('img').click();
    assert(await full.evaluate(d => d.classList.contains('zoomed')), 'a click or tap zooms in');
    await full.locator('[data-vfull-close]').click();
    await page.locator('#vcard-full').waitFor({state: 'hidden'});
    // opened again after closing zoomed: fitted, its own gestures only, and swipe down still closes it (Review 1, item 2)
    await m1.locator('figure.vcard[data-kind="image"] .vcard-hit').click();
    await full.locator('img.vcard-full-img').waitFor();
    const top = await thread.evaluate(el => el.scrollTop);        // where the reader was (the click scrolled to the card)
    const fitted = () => full.evaluate(d => {
      const b = d.querySelector('.vcard-full-body'), i = d.querySelector('img').getBoundingClientRect();
      return [d.classList.contains('zoomed'), !!d.zoomed, i.width <= b.clientWidth + 1 && i.height <= b.clientHeight + 1];
    });
    assert.deepEqual(await fitted(), [false, false, true], `${label}: reopened fitted, not zoomed`);
    await full.evaluate(d => d.querySelector('.vcard-full-body').dispatchEvent(new WheelEvent('wheel', {ctrlKey: true, deltaY: 50, clientX: 100, clientY: 100, bubbles: true, cancelable: true})));
    assert.deepEqual(await fitted(), [false, false, true], `${label}: a pinch out on a fitted picture leaves it fitted (no gestures left from the last one)`);
    if (browser.browserType().name() === 'chromium') {           // the last picture's zoom gestures are gone, not stacked
      const cdp = await context.newCDPSession(page);
      const {result} = await cdp.send('Runtime.evaluate', {expression: "document.querySelector('#vcard-full .vcard-full-body')"});
      const {listeners} = await cdp.send('DOMDebugger.getEventListeners', {objectId: result.objectId});
      assert.deepEqual(['wheel', 'pointerdown', 'pointermove', 'pointerup', 'pointercancel'].map(t => listeners.filter(l => l.type === t).length), [1, 1, 1, 1, 1],
        `${label}: one set of zoom gestures on the reopened view`);
      await cdp.detach();
    }
    if (!hasTouch) {
      await page.keyboard.press('Escape');
    } else {
      await full.evaluate(d => {
        const ev = (type, y) => new PointerEvent(type, {bubbles: true, pointerType: 'touch', pointerId: 7, clientX: 100, clientY: y});
        const bar = d.querySelector('.vcard-full-bar');
        bar.dispatchEvent(ev('pointerdown', 100)); bar.dispatchEvent(ev('pointerup', 260));
      });
    }
    await page.locator('#vcard-full').waitFor({state: 'hidden'});
    assert.equal(await thread.evaluate(el => el.scrollTop), top, `${label}: back at the same place`);
    // closing goes Back, and the browser finishes that step a moment later
    assert(await page.waitForFunction(() => !history.state?.vcardFull, null, {timeout: 3000}).then(() => true, () => false), `${label}: its history entry is gone`);
    // a page expands into the same sandbox and scrolls there
    await (hasTouch ? tall.locator('.vcard-tap').tap() : tall.locator('.vcard-expand').click());
    await full.locator('iframe.vcard-full-frame').waitFor();
    assert.equal(await full.locator('iframe').getAttribute('sandbox'), 'allow-scripts allow-popups allow-popups-to-escape-sandbox');
    await full.locator('[data-vfull-close]').click();
    await page.locator('#vcard-full').waitFor({state: 'hidden'});

    // ---- a redraw keeps a card's node (and its loaded page); a new version is a new card
    const kept = await page.evaluate(() => {
      const old = document.querySelector('figure.vcard[data-file="f-tall"]');
      V2C.html = ''; v2ChatRender(V2C);
      const a = document.createElement('div'), b = document.createElement('div');
      a.innerHTML = '<figure class="vcard" data-visual-key="m:f@1" data-state="ready" data-fresh-src="old"></figure>';
      b.innerHTML = '<figure class="vcard" data-visual-key="m:f@2" data-state="ready" data-fresh-src="new"></figure><figure class="vcard" data-visual-key="m:f@1" data-state="ready" data-fresh-src="fresh"></figure>';
      const was = a.firstChild; visualKeep(a, b);
      return [old.isConnected, b.children[0] !== was, b.children[1] === was, was.dataset.freshSrc];
    });
    assert.deepEqual(kept, [true, true, true, 'fresh']);
    // the redraw keeps a playing clip where it was: the same element, still playing, not back at 0:00 (frame defect
    // from 3/4: a kept node moved through another document reloads). Both ways: moving the card in place
    // (moveBefore) and, where a browser has no moveBefore, taking it out and putting it back.
    const noMove = on => page.evaluate(on => { if (on) { window.__mb = Element.prototype.moveBefore; delete Element.prototype.moveBefore; } else Element.prototype.moveBefore = window.__mb; }, on);
    for (const way of ['moveBefore', 'no moveBefore']) {
      await page.evaluate(src => {
        const card = `<figure class="vcard" data-visual-key="m:clip@1" data-state="ready"><audio src="${src}" muted preload="auto"></audio></figure>`;
        const root = document.createElement('div');
        root.id = 'clip-root'; root.dataset.card = card; document.body.append(root);
        visualSwap(root, card);
      }, VIS + 'tok-f-clip');
      await page.evaluate(async () => {
        const a = document.querySelector('#clip-root audio');
        await a.play();
        a.currentTime = 40;
        await new Promise(r => a.addEventListener('seeked', r, {once: true}));
      });
      if (way === 'no moveBefore') await noMove(true);
      const clip = await page.evaluate(async () => {
        const root = document.querySelector('#clip-root'), a = root.querySelector('audio');
        visualSwap(root, '<p>Another message</p>' + root.dataset.card);
        const now = [root.querySelector('audio') === a, a.currentTime >= 39, !a.paused];
        await new Promise(r => setTimeout(r, 500));
        const later = [root.querySelector('audio') === a, a.currentTime >= 39, !a.paused];
        root.remove();
        return [now, later];
      });
      if (way === 'no moveBefore') await noMove(false);
      assert.deepEqual(clip, [[true, true, true], [true, true, true]], `${label} (${way}): a clip at 40 s is the same element, at >= 39 s and playing after a redraw`);
    }
    // the same for a page card: the kept frame is the same element, and whether it loads again is recorded. Moved in
    // place it does not, and its card holds its height through the redraw: no collapse, no jump. Given a pin, the redraw
    // runs in the same task as the card takes that height from its page's message: the page answers its new size with its
    // real height within milliseconds, so a separate wait for the pinned height can miss it and wait forever.
    const redrawPage = pin => page.evaluate(async pin => {
      const card = document.querySelector('[data-message="m1"] figure.vcard[data-file="f-short"]'), f = card.querySelector('iframe'), body = card.querySelector('.vcard-body');
      const h = () => Math.round(body.getBoundingClientRect().height), seen = [];
      let loads = 0;
      f.addEventListener('load', () => loads++);
      const before = await new Promise((go, fail) => {
        const redraw = () => { const b = h(); V2C.html = ''; v2ChatRender(V2C); go(b); };
        if (!pin) return redraw();
        const t = setTimeout(() => fail(new Error(`the card never took the height ${pin} its page sent`)), 10000);
        addEventListener('message', function took() { if (f.visualHeight !== pin) return; removeEventListener('message', took); clearTimeout(t); redraw(); });
        window.vcardPinning = true;
      });
      const same = document.querySelector('[data-message="m1"] figure.vcard[data-file="f-short"] iframe') === f;
      for (let i = 0; i < 40; i++) { seen.push(h()); await new Promise(r => requestAnimationFrame(r)); }
      await new Promise(r => setTimeout(r, 800));
      seen.push(h());
      return {same, loads, before, heights: [...new Set(seen)]};
    }, pin);
    const moved = await redrawPage();
    console.log(`${label}: a kept page card's frame loaded ${moved.loads} time(s) on a redraw (moveBefore); heights ${moved.heights}`);
    assert.deepEqual([moved.same, moved.loads, moved.heights], [true, 0, [moved.before]], `${label}: a kept page card is the same frame, not loaded again, and holds its height through a redraw ${JSON.stringify(moved)}`);
    // without moveBefore (WebKit: iPhone Safari, the Mac app) the frame loads again on each redraw, and the card still
    // holds its height through ten of them; the page, scrolled to, settles at its own height again (lead, Oct 19)
    await noMove(true);
    const rounds = [];
    for (let i = 0; i < 10; i++) rounds.push(await redrawPage());
    await noMove(false);
    const shortCard = m1.locator('figure.vcard[data-file="f-short"]');
    await shortCard.scrollIntoViewIfNeeded();
    await page.waitForFunction(() => { const f = document.querySelector('[data-message="m1"] figure.vcard[data-file="f-short"] iframe'); return f.getBoundingClientRect().height > 1; }, null, {timeout: 5000}).catch(() => {});
    const settledShort = await shortCard.evaluate(c => [c.querySelector('.vcard-body'), c.querySelector('iframe')].map(e => Math.round(e.getBoundingClientRect().height)));
    console.log(`${label}: without moveBefore, the frame loaded ${rounds.map(r => r.loads)} time(s) over ten redraws; heights ${[...new Set(rounds.flatMap(r => r.heights))]}, then ${settledShort}`);
    assert(rounds.every(r => r.same && r.heights.every(h => Math.abs(h - moved.before) <= 1)) && settledShort.every(h => Math.abs(h - moved.before) <= 1),
      `${label}: without moveBefore, a page card at ${moved.before} holds it through ten redraws and settles there ${JSON.stringify(rounds)} ${settledShort}`);
    // and the page loaded again sets the height down as well as up: a card pinned taller than its page (200, as a page
    // measured at no width would leave it) comes back to the page's own height after a redraw, holding 200 until then
    const shortFrame = page.frames().find(f => f.url().includes('tok-f-short'));
    await noMove(true);
    const pinned = redrawPage(200);
    await page.waitForFunction(() => window.vcardPinning);
    await shortFrame.evaluate("parent.postMessage({ticoVisualHeight: 200}, '*')");
    const down = await pinned;
    await noMove(false);
    await shortCard.scrollIntoViewIfNeeded();
    await page.waitForFunction(() => { const f = document.querySelector('[data-message="m1"] figure.vcard[data-file="f-short"] iframe'); return f.getBoundingClientRect().height > 1; }, null, {timeout: 5000}).catch(() => {});
    const downTo = await shortCard.evaluate(c => [c.querySelector('.vcard-body'), c.querySelector('iframe')].map(e => Math.round(e.getBoundingClientRect().height)));
    assert(down.before === 200 && down.heights.every(h => h === 200 || Math.abs(h - moved.before) <= 1) && downTo.every(h => Math.abs(h - moved.before) <= 1),
      `${label}: without moveBefore, a card pinned at 200 comes back down to ${moved.before} after a redraw ${JSON.stringify(down)} ${downTo}`);
    // a page measured at one width is re-measured at another: narrowed, the short page wraps and grows; widened back,
    // it comes back down (a page measures at least its frame, so it could not say so by itself)
    const shortH = () => shortCard.evaluate(c => [c.querySelector('iframe').visualHeight, Math.round(c.querySelector('iframe').getBoundingClientRect().width)]);
    const widthTo = async w => {
      await shortCard.evaluate((c, w) => { c.style.width = w; c.style.maxWidth = w; }, w);
      await quiet();
      return shortH();
    };
    const [narrowH, narrowW] = await widthTo('50px'), [wideH, wideW] = await widthTo('');
    assert(narrowW < 70 && narrowH > moved.before + 10 && Math.abs(wideH - moved.before) <= 1,
      `${label}: a page card narrowed grows (${narrowH} at ${narrowW}px) and widened back comes down to ${moved.before} (${wideH} at ${wideW}px)`);

    // ---- live: a new message comes on the event stream; the snapshot read again signs every address afresh. A card
    // already on screen keeps its node and its height and takes the new address; the new message's cards show.
    const was = await tall.evaluate(c => { c.dataset.mark = 'kept'; return c.querySelector('.vcard-body').getBoundingClientRect().height; });
    for (let i = 0; !held.length && i < 100; i++) await page.waitForTimeout(50);
    assert(held.length, `${label}: the page follows the live event stream`);
    gen = 2;
    messages.push({id: 'm6', sender_id: 'bot:designer', body: 'Live one.', created_at: now, refs: {attachments: [A('f-live', 'image', {width: 400, height: 200}), A('f-live-page', 'page')]}});
    await held.at(-1).fulfill({contentType: 'text/event-stream', body: `id: 9\nevent: messages\ndata: ${JSON.stringify({seq: 9, conversation_id: 'c-designer'})}\n\n`});
    const m6 = page.locator('[data-message="m6"]');
    await m6.locator('figure.vcard[data-kind="page"] .vcard-body.ready').waitFor();
    assert.deepEqual(await m6.locator('img.vcard-img, iframe.vcard-frame').evaluateAll(els => els.map(e => e.getAttribute('src'))),
      [VIS + 'tok-f-live.2', VIS + 'tok-f-live-page.2'], `${label}: a live message's cards show from their visual address`);
    assert.deepEqual(await tall.evaluate(c => [c.dataset.mark, c.dataset.freshSrc, Math.round(c.querySelector('.vcard-body').getBoundingClientRect().height)]),
      ['kept', VIS + 'tok-f-tall.2', Math.round(was)], `${label}: a card on screen is kept through a live update, with the fresh address`);

    // cards never read the signed-in file route (visuals 5 and 6 and the one with no visual address are chips, which still do)
    assert.deepEqual(fileReqs.filter(r => !/f-(six[56]|nosrc)\b/.test(r)), [], `${label}: a card read /api/v2/files`);

    // ---- task comments: the same cards; Open in file viewer opens the task's own file panel at that version
    await page.goto('https://tico-ui.test/#/tasks?view=board');
    await page.waitForFunction(() => typeof TASKS_ST !== 'undefined' && TASKS_ST && !TASKS_ST.loading);
    await page.evaluate(() => taskModalShow(TASKS_ST.tasks.find(t => t.id === 't1')));
    const c1 = page.locator('#task-modal .tcomment', {hasText: 'Chart and page attached.'});
    await c1.locator('figure.vcard').nth(1).waitFor();
    assert.equal(await c1.locator('.tc-thumb, .tc-file').count(), 0, 'a file shown as a card is not also a chip');
    await c1.locator('figure.vcard[data-kind="page"] .vcard-body.ready').waitFor();
    await c1.locator('figure.vcard[data-kind="image"] .vcard-menu > summary').click();
    await c1.locator('figure.vcard[data-kind="image"] [data-vcard-act="open"]').click();
    assert.deepEqual(await page.evaluate(() => [$('#task-modal').tfOpen?.id, $('#task-modal').tfOpen?.n]), ['f-chart', 1]);
    await noSideways('task');

    // ---- nothing reached outside; visuals never carried the app's cookie
    await page.waitForTimeout(300);
    assert.deepEqual(outside, [], `${label}: a page loaded something from outside`);
    assert(visualReqs.length && visualReqs.every(r => !r.cookie), `${label}: a visual request carried a cookie`);
    assert.equal(await page.evaluate(() => document.body.dataset.owned), undefined);
    assert.deepEqual(errors, []);
    await context.close();
  };
  try {
    await run({viewport: {width: 1200, height: 800}, label: 'desktop'});
    await run({viewport: {width: 390, height: 844}, hasTouch: true, label: 'phone'});
    console.log('visual cards ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
