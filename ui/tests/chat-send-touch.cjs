// Synthetic mobile checks. Viewport movement does not establish physical iPhone keyboard acceptance.
const {chromium, webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const launch = () => process.env.TICO_UI_ENGINE === 'webkit' ? webkit.launch({headless:true}) : chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});

(async () => {
  const browser = await launch();
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true, serviceWorkers: 'block'});
    const errors = [], sends = [], uploads = [];
    page.on('pageerror', e => errors.push(e.message));
    let release; const snapshotHeld = new Promise(done => release = done);
    let answer = 'ok', hold = null;
    await page.route('**/*', async route => {
      const url = new URL(route.request().url()), p = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
      if (p === '/api/employees') return json([{name: 'sales', display_name: 'Sales', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/conversations')
        return json({conversations: url.searchParams.get('chat_with') ? [{id: 'c1', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:sales']}] : []});
      if (p.endsWith('/snapshot')) { await snapshotHeld; return json({messages: [], execution: null}); }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p === '/api/v2/uploads/chat/sales') {
        uploads.push(route.request().postDataBuffer().length);
        if (answer === 'upload-auth') return json({error:{code:'forbidden',detail:'Access refused'}}, 403);
        return json({error:{code:'too_large',detail:'Request exceeds the upload limit'}}, 413);
      }
      if (p === '/api/v2/chat/sales') {
        const text = route.request().postDataJSON().text;
        if (answer === 'long') return json({error:{code:'validation', detail:'body.text: String should have at most 200000 characters', retryable:false}}, 422);
        if (answer === 'unavailable') return json({error:{code:'not_found', detail:'Not found', idempotency_refused:true}}, 404);
        if (answer === 'rate-limit') return json({error:{code:'rate_limit',detail:'Try later'}}, 429);
        if (answer === 'server-error') return json({error:{code:'internal',detail:'Server error'}}, 500);
        if (answer === 'busy') return json({error:{code:'unavailable', detail:'Try later'}}, 503);
        if (answer === 'offline') return route.abort('internetdisconnected');
        if (answer === 'signed-out') return json({error:{code:'identity',detail:'Signed out'}}, 401);
        if (hold) await hold;
        sends.push(text);
        return json({conversation: {id: 'c1'}, message: {id: 'm' + sends.length, from_actor: 'human:ana', body: text, created_at: new Date().toISOString()}});
      }
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/bot/sales');
    release();
    const box = page.locator('#chat-composer textarea'), send = page.locator('#chat-composer .p-send');
    await box.waitFor();
    await page.waitForFunction(() => V2C?.loaded);
    const rect = locator => locator.evaluate(el => { const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}; });
    for (const width of [320,390,760,1200]) {
      await page.setViewportSize({width,height:844});
      await page.evaluate(() => document.querySelector('#chat-composer .p-goal').hidden = false);
      const bounds = await rect(send);
      assert.equal(bounds.width, 44, 'Send reserves a 44px tap target');
      assert.equal(bounds.height, 44);
      assert.equal(await send.evaluate(el => el.clientWidth), 34, 'the painted circle keeps its 34px look');
      assert.ok(bounds.x >= 0 && bounds.right <= width, 'Send stays inside the viewport');
      for (const selector of ['.p-attach', '.p-goal']) {
        const r = await rect(page.locator('#chat-composer ' + selector));
        assert.ok(r.right <= bounds.x || r.x >= bounds.right, selector + ' does not overlap Send');
      }
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

    // An actual oversize file takes the upload route, leaves its draft and attachment intact,
    // and must point to the file rather than asking the person to shorten unrelated text.
    await page.locator('#chat-composer .p-file').setInputFiles({name:'oversize.bin',mimeType:'application/octet-stream',buffer:Buffer.alloc(20_000_001)});
    assert.equal(await why.isVisible(), false, 'attaching a file clears the obsolete empty-tap explanation');
    await box.fill('A short caption');
    await send.tap();
    await page.waitForFunction(() => !BOT_PILL.sending);
    assert.match((await page.locator('.toast.err').allInnerTexts()).join(' '), /attachments are too large/i);
    assert.equal(await box.inputValue(), 'A short caption');
    assert.equal(await page.locator('#chat-composer .p-chips [data-rm]').count(), 1, 'refused upload retains its attachment');
    assert.equal(uploads.length, 1, 'one upload attempt');
    assert.ok(uploads[0] > 20_000_000, 'multipart upload exceeds the person upload limit');
    await page.locator('#chat-composer .p-chips [data-rm]').click();
    await page.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));
    await box.fill('');
    const stop = page.locator('#chat-composer .p-stop');
    await page.evaluate(() => { BOT_PILL.running = {attempt_id:'fixture'}; pillButtons(BOT_PILL); });
    assert.equal(await stop.isVisible(), true);
    assert.equal((await rect(stop)).width, 44);
    await stop.dispatchEvent('pointerdown', {button:0});
    assert.equal(await stop.evaluate(el => el.classList.contains('p-pressed')), true, 'Stop acknowledges the same immediate press');
    await stop.dispatchEvent('pointercancel');
    assert.equal(await stop.evaluate(el => el.classList.contains('p-pressed')), false);
    await page.evaluate(() => { BOT_PILL.running = null; pillButtons(BOT_PILL); });

    await page.evaluate(() => {
      window.diagnostics = [];
      TicoObservability.chatSend = () => properties => diagnostics.push(properties);
    });
    // A response with no JSON reason or HTTP status text still explains an upload failure.
    const uploadFailure = await page.evaluate(async () => {
      const original = window.fetch;
      window.fetch = (url, opts) => String(url).endsWith('/v2/uploads/tasks')
        ? Promise.resolve(new Response('', {status:400, statusText:''})) : original(url, opts);
      try {
        await cloudCompose('/v2/tasks', {title:'Fixture'}, [new File(['x'], 'fixture.txt')]);
        return null;
      } catch (error) { return {message:error.message, status:error.status, body:error.body}; }
      finally { window.fetch = original; }
    });
    assert.ok(uploadFailure?.message, 'non-chat upload refusals always have an explanation');
    assert.equal(uploadFailure.status, 400, 'fallback preserves the structured refusal');
    assert.deepEqual(uploadFailure.body, {});
    answer = 'upload-auth';
    await page.locator('#chat-composer .p-file').setInputFiles({name:'fixture.txt',mimeType:'text/plain',buffer:Buffer.from('x')});
    await box.fill('A refused upload');
    await send.tap();
    await page.waitForFunction(() => !BOT_PILL.sending);
    assert.deepEqual(await page.evaluate(() => ({...diagnostics.at(-1), duration_ms:0})),
      {outcome:'refused',duration_ms:0,retries:0,status:403}, 'auth refusal without pending text is reported as refused');
    assert.equal(await box.inputValue(), 'A refused upload');
    assert.equal(await page.locator('#chat-composer .p-chips [data-rm]').count(), 1);
    await page.locator('#chat-composer .p-chips [data-rm]').click();
    answer = 'ok';
    await box.fill('Tap the outer edge');
    await page.evaluate(() => {
      window.pressSeen = [];
      window.addEventListener('pointerdown', event => {
        if (event.target.closest('.p-send')) pressSeen.push(event.target.classList.contains('p-pressed'));
      });
    });
    const edge = await rect(send);
    await page.touchscreen.tap(edge.x + 1, edge.y + edge.height / 2);
    await page.waitForFunction(() => !BOT_PILL.sending && !document.querySelector('#chat-composer textarea').value);
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
    await page.waitForFunction(() => !BOT_PILL.sending && !document.querySelector('#chat-composer textarea').value);
    assert.equal(sends.at(-1), 'After composer movement');
    let resume;
    hold = new Promise(done => resume = done);
    await page.evaluate(() => CHAT_SEND.slow = 50);
    await box.fill('Slow private text never enters diagnostics');
    await send.tap();
    await page.locator('#chat-composer .p-slow:not([hidden])').waitFor();
    assert.equal(await page.evaluate(() => diagnostics.at(-1).outcome), 'slow', 'report while a request is still pending');
    resume(); hold = null;
    await page.waitForFunction(() => !BOT_PILL.sending);
    assert.equal(await page.evaluate(() => diagnostics.at(-1).outcome), 'sent');
    await page.evaluate(() => CHAT_SEND.slow = 5000);
    for (const [mode, words] of [['long', /too long/i], ['unavailable', /bot is unavailable/i], ['busy', /server is busy/i], ['rate-limit', /server is busy/i], ['server-error', /server could not send/i], ['offline', /Offline or connection lost/i], ['signed-out', /signed out/i]]) {
      answer = mode;
      await page.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));
      await box.fill('Retain this draft ' + mode);
      await send.tap();
      await page.waitForFunction(() => !BOT_PILL.sending);
      const feedback = (await page.locator('.toast, #signed-out:not([hidden])').allInnerTexts()).join(' ');
      assert.match(feedback, words, mode + ' is explained in words');
      assert.equal(await box.inputValue(), 'Retain this draft ' + mode);
      if (mode === 'busy') assert.deepEqual(await page.evaluate(() => ({...diagnostics.at(-1),duration_ms:0})),
        {outcome:'network',duration_ms:0,retries:2,status:503});
      if (mode === 'signed-out') assert.equal(await page.evaluate(() => diagnostics.at(-1).status),401);
      assert.doesNotMatch(JSON.stringify(await page.evaluate(() => diagnostics)), /Retain this draft|Slow private text/);
      await page.evaluate(() => { clearTimeout(BOT_PILL.retryTimer); BOT_PILL.retryTries = 0; });
    }
    assert.deepEqual(errors, []);
    console.log('touch target, press, cancellation, movement, oversize-upload, empty/offline/signed-out/busy/server/long/unavailable reasons: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
