// Pictures in chat (Dev 338, Chris Oct 21): a picture is a thumbnail about a quarter of the message column wide, on a
// desktop and a phone, whole (no crop), several side by side, so the reply's words keep the room. Tap or click opens
// the viewer with the whole picture fitted (no scroll, not at full size); from there it zooms in (wheel, click, +/-,
// pinch), pans by dragging, and goes back to fitted. Both paths: a card (visual address) and a thumbnail (no visual
// address, inline serving off). Fixtures only.
const {chromium, webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const zlib = require('node:zlib');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

// A plain picture of w x h, so a wrong fit or a crop shows in the numbers.
function png(w, h) {
  const chunk = (type, data) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
    const td = Buffer.concat([Buffer.from(type), data]), crc = Buffer.alloc(4); crc.writeUInt32BE(zlib.crc32(td));
    return Buffer.concat([len, td, crc]);
  };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  const row = Buffer.alloc(1 + w * 3, 0x60); row[0] = 0;
  return Buffer.concat([Buffer.from('89504e470d0a1a0a', 'hex'), chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(Buffer.concat(Array(h).fill(row)))), chunk('IEND', Buffer.alloc(0))]);
}
const SIZES = {'f-wide': [1600, 900], 'f-tall': [600, 2400], 'f-square': [1000, 1000], 'f-mine': [1200, 900], 'f-thumb': [700, 2100]};
const PICS = Object.fromEntries(Object.entries(SIZES).map(([id, [w, h]]) => [id, png(w, h)]));
const VIS = 'https://visuals.test/visuals/';
const A = (id, over = {}) => ({id, file_id: id, version: 1, ref: id + '@1', title: 'Shot ' + id, kind: 'image', name: id + '.png', size: 1000,
  content_type: 'image/png', url: `/api/v2/files/${id}?v=1`, removed: false, current_version: 1, src: VIS + 'tok-' + id,
  width: SIZES[id][0], height: SIZES[id][1], ...over});

(async () => {
  const browser = process.env.TICO_BROWSER === 'webkit' ? await webkit.launch({headless: true})
    : await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined, headless: true});
  const now = new Date().toISOString();
  const messages = [
    {id: 'm1', sender_id: 'bot:designer', body: 'Three screenshots, and the words about them that must stay readable.', created_at: now,
      refs: {attachments: [A('f-wide'), A('f-tall'), A('f-square')]}},
    {id: 'm2', sender_id: 'human:ana', body: 'My upload', created_at: now, refs: {attachments: [A('f-mine')]}},
    {id: 'm3', sender_id: 'bot:designer', body: 'Inline serving off.', created_at: now, refs: {attachments: [A('f-thumb', {src: null})]}},
  ];
  const run = async ({viewport, hasTouch = false, label}) => {
    const context = await browser.newContext({viewport, hasTouch, isMobile: hasTouch, serviceWorkers: 'block'});
    await context.addCookies([{name: 'tico_session', value: 'secret', domain: 'tico-ui.test', path: '/', secure: true, httpOnly: true}]);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await context.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      if (url.hostname === 'visuals.test') return route.fulfill({contentType: 'image/png', headers: {'Cross-Origin-Resource-Policy': 'cross-origin'}, body: PICS[p.replace('/visuals/tok-', '')]});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.startsWith('/api/v2/files/')) {
        const id = decodeURIComponent(p.split('/')[4]);
        return route.fulfill({contentType: 'image/png', headers: {'Content-Disposition': `attachment; filename="${id}.png"`}, body: PICS[id]});
      }
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
      if (p === '/api/employees') return json([{name: 'designer', display_name: 'Designer', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
      if (p === '/api/issues') return json([]);
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p === '/api/v2/events') return;                                 // live events, held open
      if (p === '/api/v2/conversations') return json({conversations: url.searchParams.get('chat_with') ? [{id: 'c-designer', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:designer']}] : []});
      if (p === '/api/v2/conversations/c-designer/snapshot') return json({messages, execution: null});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/bot/designer/chat');
    const m1 = page.locator('[data-message="m1"]');
    await m1.locator('figure.vcard .vcard-body.ready').nth(2).waitFor();

    // ---- thumbnails: about a quarter of the column, whole, side by side
    const cards = await m1.locator('figure.vcard').evaluateAll(els => els.map(c => {
      const r = c.getBoundingClientRect(), i = c.querySelector('img').getBoundingClientRect(), img = c.querySelector('img');
      return {w: r.width, h: r.height, top: r.top, col: c.closest('.vcards').getBoundingClientRect().width, iw: i.width, ih: i.height, nw: img.naturalWidth, nh: img.naturalHeight,
        fit: getComputedStyle(img).objectFit};
    }));
    for (const c of cards) {
      assert(c.w <= c.col * 0.3 && c.w >= Math.min(c.col * 0.2, 72), `${label}: a picture is about a quarter of the column wide ${JSON.stringify(c)}`);
      // whole: its box has the picture's shape, or the picture is contained in it (a tall one, letterboxed)
      assert(Math.abs(c.iw / c.ih - c.nw / c.nh) < 0.03 || c.fit === 'contain', `${label}: the whole picture, not cropped ${JSON.stringify(c)}`);
      assert(c.h <= 300, `${label}: a tall picture stays short ${JSON.stringify(c)}`);
    }
    assert(cards.every(c => Math.abs(c.top - cards[0].top) < 1), `${label}: three pictures sit side by side ${cards.map(c => c.top)}`);
    // the words above keep the column
    const words = await m1.locator('.bubble').evaluate(b => b.getBoundingClientRect().height);
    assert(words < 120, `${label}: the reply's words are not pushed away ${words}`);
    // your own upload too, on your side
    const mine = await page.locator('[data-message="m2"] figure.vcard').evaluate(c => [c.getBoundingClientRect().width, c.closest('.vcards').getBoundingClientRect().width,
      c.getBoundingClientRect().right, c.closest('.vcards').getBoundingClientRect().right]);
    assert(mine[0] <= mine[1] * 0.3 && Math.abs(mine[2] - mine[3]) < 2, `${label}: your own picture is a thumbnail on your side ${mine}`);
    // no visual address: the thumbnail is the same size and whole
    const t = page.locator('[data-message="m3"] .chat-thumb.ready');
    await t.waitFor();
    await page.waitForFunction(() => document.querySelector('[data-message="m3"] .chat-thumb img')?.naturalWidth > 0);
    const thumb = await t.evaluate(b => {
      const r = b.getBoundingClientRect(), i = b.querySelector('img').getBoundingClientRect();
      return {w: r.width, col: b.closest('.conv-run').getBoundingClientRect().width, ratio: i.width / i.height, fit: getComputedStyle(b.querySelector('img')).objectFit};
    });
    assert(thumb.w <= Math.min(thumb.col, 800) * 0.3 && thumb.w >= 60, `${label}: the fallback thumbnail is about a quarter of the column ${JSON.stringify(thumb)}`);
    assert(Math.abs(thumb.ratio - 700 / 2100) < 0.03 || thumb.fit === 'contain', `${label}: the fallback thumbnail is whole, not cropped ${JSON.stringify(thumb)}`);

    // a thumbnail's menu, wider than the thumbnail, opens on the screen from the leftmost one
    await m1.locator('figure.vcard').first().locator('.vcard-menu > summary').click();
    const menu = await m1.locator('figure.vcard').first().locator('.vcard-menu-list').evaluate(l => [l.getBoundingClientRect().left, l.getBoundingClientRect().right, innerWidth]);
    assert(menu[0] >= 0 && menu[1] <= menu[2], `${label}: the leftmost thumbnail's menu is on the screen ${menu}`);
    await m1.locator('figure.vcard').first().locator('.vcard-menu > summary').click();
    assert.equal(await m1.locator('details.vcard-menu[open]').count(), 0, `${label}: the menu closes again`);

    // ---- the viewer: opens fitted, zooms, pans, back to fitted
    const view = async ({open, dialog, body, bar, img, nw, nh}) => {
      await open();
      const d = page.locator(dialog);
      await d.locator(img).waitFor();
      await page.waitForFunction(sel => document.querySelector(sel)?.complete && document.querySelector(sel).naturalWidth > 0, `${dialog} ${img}`);
      const box = () => d.evaluate((el, [b, i]) => {
        const body = el.querySelector(b), br = body.getBoundingClientRect(), ir = el.querySelector(i).getBoundingClientRect();
        return {bw: br.width, bh: br.height, iw: ir.width, ih: ir.height, il: ir.left - br.left, it: ir.top - br.top,
          scroll: body.scrollHeight > body.clientHeight + 1 || body.scrollWidth > body.clientWidth + 1};
      }, [body, img]);
      const fit = await box();
      assert(!fit.scroll && fit.iw <= fit.bw + 1 && fit.ih <= fit.bh + 1 && fit.il >= -1 && fit.it >= -1,
        `${label} ${dialog}: opens with the whole picture fitted, no scroll ${JSON.stringify(fit)}`);
      assert(fit.ih < nh && Math.abs(fit.iw / fit.ih - nw / nh) < 0.03, `${label} ${dialog}: fitted, not at full size ${JSON.stringify(fit)}`);
      // + zooms in at the middle, - back out, 0 back to fitted
      await page.keyboard.press('+');
      const in1 = await box();
      assert(in1.iw > fit.iw * 1.3, `${label} ${dialog}: + zooms in ${JSON.stringify(in1)}`);
      await page.keyboard.press('-');
      const out1 = await box();
      assert(out1.iw < in1.iw, `${label} ${dialog}: - zooms out`);
      await page.keyboard.press('0');
      assert(Math.abs((await box()).iw - fit.iw) < 1, `${label} ${dialog}: 0 is fitted again`);
      // the buttons
      await d.locator('[data-zoom="in"]').click();
      await d.locator('[data-zoom="in"]').click();
      const in2 = await box();
      assert(in2.iw > fit.iw * 1.5, `${label} ${dialog}: the + button zooms in`);
      // drag pans (mouse) while zoomed
      const b = await d.locator(body).boundingBox();
      await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
      await page.mouse.down(); await page.mouse.move(b.x + b.width / 2 + 60, b.y + b.height / 2 + 40, {steps: 4}); await page.mouse.up();
      const panned = await box();
      assert(Math.abs(panned.il - in2.il) > 5 || Math.abs(panned.it - in2.it) > 5, `${label} ${dialog}: a drag pans ${JSON.stringify([in2, panned])}`);
      assert(Math.abs(panned.iw - in2.iw) < 1, `${label} ${dialog}: a drag's click does not toggle the zoom`);
      // a drag let go over the bar, or past the viewer (its backdrop), ends there: back over the picture with no
      // button held, nothing moves, and the viewer stays open
      const barBox = await d.locator(bar).boundingBox(), dlg = await d.boundingBox();
      const outside = [[barBox.x + 20, barBox.y + barBox.height / 2]];
      if (dlg.x > 6) outside.push([dlg.x - 4, dlg.y + dlg.height / 2]);
      for (const [x, y] of outside) {
        await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
        await page.mouse.down(); await page.mouse.move(x, y, {steps: 6}); await page.mouse.up();
        const let_go = await box();
        await page.mouse.move(b.x + b.width / 2 - 50, b.y + b.height / 2 + 50, {steps: 4});
        const hover = await box();
        assert(Math.abs(hover.il - let_go.il) < 1 && Math.abs(hover.it - let_go.it) < 1, `${label} ${dialog}: no pan after a release at ${x},${y} ${JSON.stringify([let_go, hover])}`);
        assert(await page.locator(dialog).isVisible(), `${label} ${dialog}: still open after a release at ${x},${y}`);
      }
      // zoomed, a two-finger scroll (a plain wheel) moves around the picture without zooming
      const before = await box();
      await d.locator(body).evaluate(el => el.dispatchEvent(new WheelEvent('wheel', {deltaX: 30, deltaY: 30, clientX: el.getBoundingClientRect().left + 40, clientY: el.getBoundingClientRect().top + 40, bubbles: true, cancelable: true})));
      const scrolled = await box();
      assert(Math.abs(scrolled.iw - before.iw) < 1 && (Math.abs(scrolled.il - before.il) > 1 || Math.abs(scrolled.it - before.it) > 1), `${label} ${dialog}: a scroll pans a zoomed picture ${JSON.stringify([before, scrolled])}`);
      await d.locator('[data-zoom="fit"]').click();
      const back = await box();
      assert(Math.abs(back.iw - fit.iw) < 1 && Math.abs(back.il - fit.il) < 1, `${label} ${dialog}: Fit is fitted again ${JSON.stringify([fit, back])}`);
      // the wheel zooms in where it points
      await d.locator(body).evaluate(el => el.dispatchEvent(new WheelEvent('wheel', {deltaY: -200, clientX: el.getBoundingClientRect().left + 40, clientY: el.getBoundingClientRect().top + 40, bubbles: true, cancelable: true})));
      assert((await box()).iw > fit.iw * 1.3, `${label} ${dialog}: the wheel zooms in`);
      await d.locator('[data-zoom="fit"]').click();
      // a click or tap on the picture zooms to 2x there, and another goes back to fitted
      await d.locator(img).click();
      assert((await box()).iw > fit.iw * 1.8, `${label} ${dialog}: a click zooms in`);
      await d.locator(img).click();
      assert(Math.abs((await box()).iw - fit.iw) < 1, `${label} ${dialog}: a second click is fitted again`);
      // two fingers apart zoom in; one lifted, the other pans on
      const pinched = await d.locator(body).evaluate(el => {
        const r = el.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
        const ev = (type, id, x) => el.dispatchEvent(new PointerEvent(type, {bubbles: true, cancelable: true, pointerType: 'touch', pointerId: id, isPrimary: id === 21, button: 0, buttons: type === 'pointerup' ? 0 : 1, clientX: x, clientY: cy}));
        ev('pointerdown', 21, cx - 20); ev('pointerdown', 22, cx + 20);
        for (const s of [20, 40, 60, 80]) { ev('pointermove', 21, cx - s); ev('pointermove', 22, cx + s); }
        const w1 = el.querySelector('img').getBoundingClientRect().width;
        ev('pointerup', 22, cx + 80);
        const l1 = el.querySelector('img').getBoundingClientRect().left;
        ev('pointermove', 21, cx - 40);
        const l2 = el.querySelector('img').getBoundingClientRect().left;
        ev('pointerup', 21, cx - 40);
        return [w1, l1, l2];
      });
      assert(pinched[0] > fit.iw * 1.5, `${label} ${dialog}: a pinch zooms in ${pinched}`);
      assert(Math.abs(pinched[2] - pinched[1]) > 5, `${label} ${dialog}: the finger left after a pinch pans ${pinched}`);
      await d.locator('[data-zoom="fit"]').click();
      await page.keyboard.press('Escape');
      await page.locator(dialog).waitFor({state: 'hidden'});
    };
    await view({open: () => m1.locator('figure.vcard[data-file="f-tall"] .vcard-hit').click(), dialog: '#vcard-full', body: '.vcard-full-body', bar: '.vcard-full-bar', img: 'img.vcard-full-img', nw: 600, nh: 2400});
    await view({open: () => m1.locator('figure.vcard[data-file="f-wide"] .vcard-hit').click(), dialog: '#vcard-full', body: '.vcard-full-body', bar: '.vcard-full-bar', img: 'img.vcard-full-img', nw: 1600, nh: 900});
    await view({open: () => t.click(), dialog: '#doc-viewer', body: '.doc-viewer-body', bar: '.doc-viewer-head', img: 'img.viewer-img', nw: 700, nh: 2100});
    assert.deepEqual(errors, [], label);
    await context.close();
  };
  await run({viewport: {width: 1200, height: 800}, label: 'desktop'});
  await run({viewport: {width: 390, height: 844}, hasTouch: true, label: 'phone'});
  await browser.close();
  console.log('chat image size: ok');
})().catch(e => { console.error(e); process.exit(1); });
