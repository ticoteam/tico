// Synthetic mobile checks on the Assistant's composer (the bot chat's own composer is gone). Viewport movement does not
// establish physical iPhone keyboard acceptance.
const {chromium, webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {assistantView} = require('./support/assistant-chat.cjs');
const launch = () => process.env.TICO_UI_ENGINE === 'webkit' ? webkit.launch({headless:true}) : chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});

(async () => {
  const browser = await launch();
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true, serviceWorkers: 'block'});
    const errors = [], sends = [], thread = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url()), p = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
      if (p === '/api/employees') return json([{name: 'coo', display_name: 'Assistant', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/assistant') return json(assistantView('c1', {messages: thread}));
      if (p === '/api/v2/conversations/c1/snapshot') return json({messages: thread, execution: null});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p === '/api/v2/assistant/messages') {
        const text = route.request().postDataJSON().text;
        sends.push(text);
        const message = {id: 'm' + sends.length, from_actor: 'human:ana', body: text, created: new Date().toISOString()};
        thread.push(message);
        return json({message, fast: true});
      }
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/assistant');
    const box = page.locator('#chat-composer textarea'), send = page.locator('#chat-composer .p-send');
    await box.waitFor();
    await page.waitForFunction(() => V2C?.loaded);
    const rect = locator => locator.evaluate(el => { const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}; });
    for (const width of [320,390,760,1200]) {
      await page.setViewportSize({width,height:844});
      const bounds = await rect(send);
      assert.equal(bounds.width, 44, 'Send reserves a 44px tap target');
      assert.equal(bounds.height, 44);
      assert.equal(await send.evaluate(el => el.clientWidth), 34, 'the painted circle keeps its 34px look');
      assert.ok(bounds.x >= 0 && bounds.right <= width, 'Send stays inside the viewport');
      const r = await rect(page.locator('#chat-composer .p-attach'));
      assert.ok(r.right <= bounds.x || r.x >= bounds.right, '.p-attach does not overlap Send');
    }
    await page.setViewportSize({width:390,height:844});
    await box.focus();
    // An empty tap gives visible, accessible words even though no request starts.
    await send.tap();
    const why = page.locator('#chat-composer .p-why');
    assert.match(await why.innerText(), /Type a message or attach a file/);
    assert.equal(await why.isVisible(), true, 'empty tap exposes a visible reason');
    assert.equal(sends.length, 0);
    assert.equal(await why.getAttribute('role'), 'status');

    await box.fill('Tap the outer edge');
    await page.evaluate(() => {
      window.pressSeen = [];
      window.addEventListener('pointerdown', event => {
        if (event.target.closest('.p-send')) pressSeen.push(event.target.classList.contains('p-pressed'));
      });
    });
    const edge = await rect(send);
    await page.touchscreen.tap(edge.x + 1, edge.y + edge.height / 2);
    await page.waitForFunction(() => !ASSISTANT_PILL.sending && !document.querySelector('#chat-composer textarea').value);
    assert.deepEqual(sends, ['Tap the outer edge'], 'the transparent edge sends with the textarea focused');
    assert.equal(await box.evaluate(el => document.activeElement === el), true, 'sending retains composer focus');
    assert.deepEqual(await page.evaluate(() => pressSeen), [true], 'pointerdown is acknowledged immediately');
    await page.waitForFunction(() => !document.querySelector('#chat-composer .p-send').classList.contains('p-pressed'));
    await send.dispatchEvent('pointerdown', {button:0});
    assert.equal(await send.evaluate(el => el.classList.contains('p-pressed')), true);
    await send.dispatchEvent('pointercancel');
    assert.equal(await send.evaluate(el => el.classList.contains('p-pressed')), false, 'cancellation clears feedback');

    // A changed viewport simulates composer movement; this is not a real iPhone keyboard test.
    await page.setViewportSize({width:390, height:520});
    await box.fill('After composer movement');
    await send.tap();
    await page.waitForFunction(() => !ASSISTANT_PILL.sending && !document.querySelector('#chat-composer textarea').value);
    assert.equal(sends.at(-1), 'After composer movement');
    assert.deepEqual(errors, []);
    console.log('touch target, press, cancellation, movement, empty-tap reason: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
