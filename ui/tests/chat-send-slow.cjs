// Offline browser regression (Dev 371): a slow chat send is visible, and a stalled one ends. While a message is out,
// Send shows a spinner at full strength (not the dimmed look of an empty box); after a few seconds a line under the box
// says it is still sending, and that it is trying again when the server answers 503; a send with no answer in time
// ends in "Not confirmed ... still here" with Retry, which resends under the same request id, also when the answer's
// headers came but its body stalled. A slow or failed send leaves
// one console line with numbers only, never the message text. A disabled or dimmed Send (and a pending Stop) says why
// on hover and to a screen reader, and a redraw of the composer's label never puts "Send" back over the reason.
const {chromium, webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const launch = {
  chromium: () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined}),
  // The bug was seen in mobile Safari: the hosted job installs WebKit, so the abort path gets a Safari-engine receipt.
  webkit: () => webkit.launch({headless: true}),
};
// One engine per run, so each fits the browser runner's time per script: Chromium here, WebKit from
// chat-send-slow-webkit.cjs. TICO_UI_ENGINES names others, comma-separated.
const engines = (process.env.TICO_UI_ENGINES || 'chromium').split(',');

async function check(engine) {
  const browser = await launch[engine]();
  let server;
  try {
    const page = await browser.newPage({viewport: {width: 1200, height: 800}, serviceWorkers: 'block'});
    const errors = [], sends = [], keys = [], warnings = [], uploads = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => { if (m.type() === 'warning' && m.text().startsWith('chat send')) warnings.push(m.text()); });
    let answer = 'ok', busy = 0, hold = null, stopHold = null;
    const reads = new Set(), readWaiters = new Set(), serverErrors = [];
    page.on('request', request => {
      const path = new URL(request.url()).pathname;
      if (request.method() === 'GET' && path !== '/api/v2/events' && !path.endsWith('/watch')) reads.add(request);
    });
    const readFinished = request => {
      reads.delete(request);
      if (!reads.size) for (const done of [...readWaiters]) done();
    };
    page.on('requestfinished', readFinished);
    page.on('requestfailed', readFinished);
    const delivered = new Map();                    // stored sends can lose their response before access is revoked
    const refusals = new Set();                     // like the server, replay a stored refusal for the same key
    // Keep same-origin event streams open: a finite intercepted stream reconnects and probes
    // identity during reload, which WebKit can report as an access-control page error.
    server = http.createServer((request, response) => {
      void (async () => {
      const url = new URL(request.url, 'http://127.0.0.1'), p = url.pathname;
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const body = Buffer.concat(chunks);
      const reply = (body, contentType, status = 200) => { response.writeHead(status, {'Content-Type': contentType}); response.end(body); };
      const json = (body, status = 200) => reply(JSON.stringify(body), 'application/json', status);
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return reply(fs.readFileSync(uiFile(ui[1]), 'utf8'), ui[1].endsWith('.css') ? 'text/css' : 'application/javascript');
      if (p.endsWith('.js')) return reply('', 'application/javascript');
      if (p === '/') return reply(html, 'text/html');
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
      if (p === '/api/employees') return json([{name: 'sales', display_name: 'Sales', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/conversations')
        return json({conversations: url.searchParams.get('chat_with') ? [{id: 'c1', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:sales']}] : []});
      if (p.endsWith('/snapshot')) return json({messages: [], execution: null});
      if (p === '/api/v2/events' || p.endsWith('/watch')) {
        response.writeHead(200, {'Content-Type': 'text/event-stream'});
        response.write('event: ready\ndata: {"seq":0}\n\n');
        return;
      }
      if (p === '/api/v2/bots/sales/stop') { await stopHold; return json({ok: true}); }
      if (p === '/api/v2/uploads/chat/sales') {
        uploads.push(body.toString());
        return json({conversation: {id: 'c1'}, message: {id: 'u' + uploads.length, from_actor: 'human:ana', body: 'With a file', created_at: new Date().toISOString()}});
      }
      if (p === '/api/v2/chat/sales') {
        const text = JSON.parse(body).text;
        const key = request.headers['idempotency-key']; keys.push(key);
        // Current access is checked before replay, and this denial does not overwrite the stored result.
        if (answer === 'signed-out') return json({error: {code: 'identity', detail: 'Invalid credential'}}, 401);
        if (answer === 'access-refused') return json({error: {code: 'forbidden', detail: 'Chat access refused'}}, 403);
        if (['stored-drop-refuse', 'stored-busy-refuse'].includes(answer) && delivered.has(key))
          return json({error: {code: 'forbidden', detail: 'Chat access refused'}}, 403);
        if (delivered.has(key)) return json(delivered.get(key));
        if (['stored-stall', 'stored-drop-refuse', 'stored-busy-refuse', 'stored-privacy-refused'].includes(answer)) {
          sends.push(text);
          delivered.set(key, {conversation: {id: 'c1'}, message: {id: 'm' + sends.length, from_actor: 'human:ana', body: text, created_at: new Date().toISOString()}});
          if (answer === 'stored-privacy-refused') return json({error: {code: 'privacy', detail: 'Response access refused'}}, 403);
          if (answer === 'stored-drop-refuse') return response.destroy();
          if (answer === 'stored-busy-refuse') return json({error: {code: 'unavailable', detail: 'Proxy lost the answer'}}, 503);
          return;                                  // saved, but the response never reaches the page
        }
        if (answer === 'busy-refused' && busy > 0) { busy--; return json({error: {code: 'unavailable', detail: 'Busy'}}, 503); }
        if (answer === 'refused' || answer === 'busy-refused') refusals.add(key);
        if (refusals.has(key)) return json({error: {code: 'escape', detail: 'A rule refused this send', idempotency_refused: true}}, 403);
        if (answer === 'stall') return;                       // never answered: the page has to give up on its own
        if (busy > 0) { busy--; return json({error: {code: 'unavailable', detail: 'Busy'}}, 503); }
        if (hold) await hold;
        sends.push(text);
        return json({conversation: {id: 'c1'}, message: {id: 'm' + sends.length, from_actor: 'human:ana', body: text, created_at: new Date().toISOString()}});
      }
      return json(unrouted(p));
      })().catch(error => { serverErrors.push(error.message); if (!response.headersSent) response.writeHead(500); response.end(); });
    });
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
    const origin = 'http://127.0.0.1:' + server.address().port;
    const ready = (allowSignedOut = false) => page.waitForFunction(allowSignedOut => {
      // Sign-in recovery deliberately closes the stream until identity is restored.
      if (allowSignedOut && SESSION.out) return !LIVE.es && !liveConnected();
      if (!SESSION.seen || !V2C?.loaded || !LIVE.conversations.has('c1') || !liveConnected() || LIVE.es?.readyState !== EventSource.OPEN) return false;
      const subscribed = new URL(LIVE.es.url), expected = new URL(liveUrl(), location.href);
      // Receiving ready changes the replay cursor without changing the subscription.
      subscribed.searchParams.delete('after'); expected.searchParams.delete('after');
      return subscribed.href === expected.href;
    }, allowSignedOut);
    // Support starts its first read from a delayed load callback. An empty reads set
    // does not mean that callback has fired. Observe the real response for each
    // document, including its body, so reloads cannot interrupt that startup read.
    // The original WebKit access-control error's exact mechanism is still unproven.
    const loaded = async navigate => {
      const support = page.waitForResponse(response => response.request().method() === 'GET' &&
        new URL(response.url()).pathname === '/api/v2/support/tickets');
      await navigate();
      const response = await support;
      assert.equal(await response.finished(), null, 'the delayed Support startup read finishes');
      await ready();
    };
    const readsSettled = async () => {
      const deadline = Date.now() + 30000;
      do {
        await ready(true);
        // Writes can forget reads still running, and schedule a later snapshot burst.
        await page.waitForFunction(() => !LIVE_SOON.size);
        if (reads.size) await new Promise((resolve, reject) => {
          const done = () => { clearTimeout(timer); readWaiters.delete(done); resolve(); };
          const timer = setTimeout(() => { readWaiters.delete(done); reject(new Error('Fixture reads did not settle')); }, Math.max(1, deadline - Date.now()));
          readWaiters.add(done);
        });
        await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
        assert.ok(Date.now() < deadline, 'fixture reads settle before navigation');
      } while (reads.size || await page.evaluate(() => LIVE_SOON.size > 0));
      assert.equal(await page.evaluate(() => SESSION.out ? !LIVE.es && !liveConnected() :
        liveConnected() && LIVE.es?.readyState === EventSource.OPEN), true,
        'the fixture stream is settled for the current session before navigation');
    };
    await loaded(() => page.goto(origin + '/#/bot/sales'));
    const box = page.locator('#chat-composer textarea'), send = page.locator('#chat-composer .p-send');
    const slow = page.locator('#chat-composer .p-slow');
    await page.evaluate(() => { CHAT_SEND.slow = 300; });

    // The empty box dims Send; a send that is out shows a spinner at full strength, and after a moment says so in words.
    assert.equal(await send.evaluate(b => getComputedStyle(b).opacity), '0.45', 'an empty box dims Send');
    const why = () => send.evaluate(b => [b.title, document.getElementById(b.getAttribute('aria-describedby'))?.textContent || '']);
    assert.deepEqual(await why(), Array(2).fill('Type a message or attach a file to send'), 'the empty box says why');
    let open; hold = new Promise(done => open = done);
    const first = 'Secret words that never reach the console';
    await box.fill(first); await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('#chat-composer .p-send').classList.contains('p-sending'));
    assert.equal(await send.evaluate(b => getComputedStyle(b).opacity), '1', 'sending is not the dimmed empty look');
    assert.equal(await send.evaluate(b => getComputedStyle(b, '::after').content), '""', 'the spinner shows');
    assert.ok(await send.isDisabled(), 'no double send while it is out');
    assert.deepEqual(await why(), Array(2).fill('Sending… waiting for Tico to answer'), 'a send in flight says why at once');
    assert.ok(await page.locator('#chat-composer .p-why').evaluate(e => e.classList.contains('p-why-busy')), 'the phone line has its reason');
    // The bot page redraws the label (pillNeeds) while the send is out: the reason stays, not "Send".
    await page.evaluate(() => pillNeeds(BOT_PILL, null));
    assert.deepEqual(await why(), Array(2).fill('Sending… waiting for Tico to answer'), 'a redraw keeps the reason');
    await slow.waitFor({state: 'visible'});
    assert.equal(await slow.innerText(), 'Still sending…');
    open(); hold = null;
    await page.waitForFunction(() => !document.querySelector('#chat-composer textarea').value && !BOT_PILL.sending);
    assert.ok(await slow.isHidden(), 'the line goes once it is sent');
    assert.ok(!(await send.evaluate(b => b.classList.contains('p-sending'))), 'the spinner goes');
    assert.deepEqual(await why(), Array(2).fill('Type a message or attach a file to send'), 'settled, the empty box says why again');
    await box.fill('typed');
    assert.deepEqual(await why(), ['Send', ''], 'an enabled Send keeps its own title');
    await box.fill('');
    assert.deepEqual(sends, [first]);
    assert.equal(warnings.length, 1, 'a slow send leaves one line');
    assert.match(warnings[0], /chat send/);
    assert.ok(!warnings[0].includes('Secret'), 'never the message text');

    // The server answers 503 twice, then takes it: the line says it is trying again, and it sends once.
    busy = 2;
    await box.fill('Through two busy answers'); await page.keyboard.press('Enter');
    await page.waitForFunction(() => /trying again/.test(document.querySelector('#chat-composer .p-slow')?.textContent || ''));
    assert.equal((await why())[0], 'Sending… trying again');
    await page.waitForFunction(() => !document.querySelector('#chat-composer textarea').value && !BOT_PILL.sending);
    assert.equal(sends.at(-1), 'Through two busy answers');
    assert.equal(new Set(keys.slice(-3)).size, 1, 'the retries reuse one request id');

    // A definitive refusal did not send a message. If its cause changes, explicit Send of the unchanged draft
    // must evaluate that new situation rather than replaying the server's cached refusal for48h.
    answer = 'refused';
    const beforeRefusal = sends.length;
    await box.fill('A rule refused this draft'); await page.keyboard.press('Enter');
    await page.waitForFunction(() => !BOT_PILL.sending && document.querySelector('.toast.err'));
    const refusedKey = keys.at(-1);
    assert.match(await page.locator('.toast.err').innerText(), /Not sent:/, 'first-attempt refusal is definitive');
    assert.equal(sends.length, beforeRefusal, 'first-attempt refusal stores nothing');
    assert.equal(await box.inputValue(), 'A rule refused this draft');
    assert.equal(await page.evaluate(() => BOT_PILL.unconfirmed), null, 'confirmed refusal ends pending delivery');
    answer = 'ok';
    // A redraw can replace the arrow between press and release (a snapshot lands, goal mode flips); the click still sends.
    await send.evaluate(b => b.addEventListener('pointerdown', () => { b.innerHTML = ICON_SEND; }, {once: true}));
    await send.click();
    await page.waitForFunction(() => !document.querySelector('#chat-composer textarea').value && !BOT_PILL.sending);
    assert.notEqual(keys.at(-1), refusedKey, 'confirmed refusal allows a later explicit send to evaluate again');
    assert.equal(sends.at(-1), 'A rule refused this draft');
    await page.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));

    // A recorded rule refusal resolves this identity even after an automatic retry. A bare 503 alone
    // cannot prove whether a proxy lost a successful answer; the refusal receipt supplies that proof.
    answer = 'busy-refused'; busy = 1;
    const beforeBusyRefusal = sends.length, beforeBusyKeys = keys.length;
    await box.fill('A rule refused after a busy answer'); await page.keyboard.press('Enter');
    await page.waitForFunction(() => !BOT_PILL.sending && document.querySelector('.toast.err'));
    const busyRefusedKey = keys.at(-1);
    assert.equal(keys.length, beforeBusyKeys + 2, 'a busy answer retries into a recorded rule refusal');
    assert.equal(keys.at(-2), busyRefusedKey, 'automatic retry keeps its key before refusal');
    assert.equal(sends.length, beforeBusyRefusal, 'the recorded refusal stored no message');
    assert.match(await page.locator('.toast.err').innerText(), /Not sent:/, 'recorded rule refusal is definitive after a retry');
    assert.equal(await page.evaluate(() => BOT_PILL.unconfirmed), null, 'recorded refusal ends the retry identity');
    answer = 'ok';
    await send.click();
    await page.waitForFunction(() => !BOT_PILL.sending && !document.querySelector('#chat-composer textarea').value);
    assert.notEqual(keys.at(-1), busyRefusedKey, 'a later explicit Send re-evaluates a confirmed refusal');
    assert.equal(sends.length, beforeBusyRefusal + 1, 'the later Send reaches the bot exactly once');
    await page.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));

    // A stored send with a lost response is still uncertain if access is refused before same-key replay.
    // Restoring access and sending the unchanged draft must replay that original, never create a second message.
    await page.evaluate(() => { CHAT_SEND.timeout = 200; });
    answer = 'stored-stall';
    const uncertainText = 'Stored before access was revoked', beforeUncertain = sends.length;
    await box.fill(uncertainText); await page.keyboard.press('Enter');
    await page.waitForFunction(() => !BOT_PILL.sending && document.querySelector('.toast.err'));
    const uncertainKey = keys.at(-1), uncertainAt = await page.evaluate(() => BOT_PILL.unconfirmed.at);
    assert.equal(sends.length, beforeUncertain + 1, 'the original reached storage before its response was lost');
    answer = 'signed-out';
    await page.locator('.toast.err button', {hasText: 'Retry'}).click();
    await page.waitForFunction(() => !BOT_PILL.sending);
    assert.equal(keys.at(-1), uncertainKey, 'access-refused Retry uses the original key');
    assert.equal(await page.evaluate(() => BOT_PILL.unconfirmed?.key), uncertainKey, '401 cannot resolve an earlier uncertain send');
    assert.equal(await page.evaluate(() => !!BOT_PILL.unconfirmed?.refused), false, 'the pending send is not a confirmed refusal');
    assert.equal(await page.evaluate(() => SESSION.out), true, '401 activates sign-in recovery');
    assert.ok(await page.locator('#signed-out').isVisible(), 'the sign-in banner offers recovery');
    assert.match(await page.locator('.toast.err').innerText(), /may already have sent.*signed out.*Check the chat before resending/, 'uncertain delivery stays visible alongside sign-in');
    assert.ok(!(await page.locator('.toast.err').allInnerTexts()).some(t => /Not sent:/.test(t)), 'the UI does not claim the original was unsent');
    assert.equal(await box.inputValue(), uncertainText);
    await readsSettled();
    await loaded(() => page.reload());
    // The replay below must succeed, and the deadline covers the sign-in check too: a short one ends it unconfirmed on a busy computer.
    await page.evaluate(() => { CHAT_SEND.timeout = 30000; });
    assert.equal(await page.evaluate(() => BOT_PILL.unconfirmed?.key), uncertainKey, 'reload preserves the uncertain key');
    // Simulate sign-in restoring the session before a separate access denial on the same saved send.
    answer = 'access-refused';
    await page.evaluate(() => { SESSION.out = false; });
    await send.click();
    await page.waitForFunction(() => !BOT_PILL.sending);
    assert.equal(await page.evaluate(() => BOT_PILL.unconfirmed?.key), uncertainKey, '403 after sign-in also keeps the key');
    assert.equal(await page.evaluate(() => BOT_PILL.unconfirmed?.at), uncertainAt, 'another denial after reload keeps the original replay age');
    answer = 'ok';
    await send.click();
    await page.waitForFunction(() => !BOT_PILL.sending);
    // Restoration runs on animation frames after the send changes the DOM. An instant empty box can
    // precede the stale snapshot refilling it; trigger a redraw and check the settled box before typing.
    await page.evaluate(() => new Promise(done => { document.body.append(document.createElement('div')); requestAnimationFrame(() => requestAnimationFrame(done)); }));
    assert.equal(await box.inputValue(), '', 'confirmed text stays cleared after sign-in restoration settles');
    assert.equal(keys.at(-1), uncertainKey, 'restored access replays the same key for an unchanged draft');
    assert.equal(sends.length, beforeUncertain + 1, 'same-key replay leaves exactly one message server-side');
    assert.equal(await page.evaluate(() => BOT_PILL.unconfirmed), null, 'acknowledged replay ends pending delivery');
    await page.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));

    // Payload privacy can reject a response after the first attempt already stored its message.
    answer = 'stored-privacy-refused';
    const beforePrivacy = sends.length;
    await box.fill('Stored before response privacy narrowed'); await page.keyboard.press('Enter');
    await page.waitForFunction(() => !BOT_PILL.sending && document.querySelector('.toast.err'));
    const privacyKey = keys.at(-1);
    assert.equal(await page.evaluate(() => BOT_PILL.unconfirmed?.key), privacyKey, 'post-commit 403 keeps the identity on a first attempt');
    assert.match(await page.locator('.toast.err').innerText(), /Not confirmed:.*may already have sent.*Response access refused.*Check the chat before resending/);
    answer = 'ok';
    await send.click();
    await page.waitForFunction(() => !BOT_PILL.sending && !document.querySelector('#chat-composer textarea').value);
    assert.equal(keys.at(-1), privacyKey, 'post-commit denial replays the stored success');
    assert.equal(sends.length, beforePrivacy + 1, 'post-commit denial does not duplicate delivery');
    await page.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));

    // Switching bots and returning during a send restores a new composer sharing the in-flight identity.
    let switchOpen; hold = new Promise(done => switchOpen = done);
    const switchRequest = page.waitForRequest(r => new URL(r.url()).pathname === '/api/v2/chat/sales');
    await box.fill('Sent while switching bots'); await page.keyboard.press('Enter');
    const switchKey = (await switchRequest).headers()['idempotency-key'];
    await page.evaluate(() => {
      const old = BOT_PILL;
      botPill('other'); const restored = botPill('sales'); old.el.replaceWith(restored.el);
    });
    assert.ok(await send.isDisabled(), 'restored composer waits on the in-flight identity');
    switchOpen(); hold = null;
    await page.waitForFunction(() => !BOT_PILL.unconfirmed?.inFlight);
    assert.equal(await page.evaluate(() => BOT_PILL.unconfirmed), null, 'acknowledgement clears the identity in the restored composer');
    const beforeRepeat = sends.length;
    await send.click();
    await page.waitForFunction(() => !BOT_PILL.sending && !document.querySelector('#chat-composer textarea').value);
    assert.notEqual(keys.at(-1), switchKey, 'explicit Send of the acknowledged words is a new submission');
    assert.equal(sends.length, beforeRepeat + 1, 'the repeated words reach the bot');

    // An automatic retry can lose access after storage, whether its answer was dropped or a proxy returned 503.
    for (const autoMode of ['stored-drop-refuse', 'stored-busy-refuse']) {
      await page.evaluate(() => { CHAT_SEND.timeout = 3000; });
      answer = autoMode;
      const autoText = 'Stored before an automatic retry lost access', beforeAuto = sends.length, beforeAutoKeys = keys.length;
      await box.fill(autoText); await page.keyboard.press('Enter');
      await page.waitForFunction(() => !BOT_PILL.sending && document.querySelector('.toast.err'));
      const autoKey = keys.at(-1);
      assert.equal(keys.length, beforeAutoKeys + 2, 'one Send loses its answer then automatically retries into 403');
      assert.equal(keys.at(-2), autoKey, 'the automatic retry uses the first attempt key');
      assert.equal(await page.evaluate(() => BOT_PILL.unconfirmed?.key), autoKey, 'automatic retry 403 cannot resolve the earlier uncertain attempt');
      assert.match(await page.locator('.toast.err').innerText(), /may already have sent.*access.*refused.*Check the chat before resending/);
      answer = 'ok';
      await send.click();
      await page.waitForFunction(() => !BOT_PILL.sending && !document.querySelector('#chat-composer textarea').value);
      assert.equal(keys.at(-1), autoKey, 'restored access replays the original automatic-attempt identity');
      assert.equal(sends.length, beforeAuto + 1, 'automatic retry plus restored access leave exactly one stored message');
      await page.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));
    }

    // Adding a file intentionally changes an uncertain text submission. A successful upload must not leave its
    // old key behind: typing the same text again is a new message, not a replay of the earlier stored answer.
    await page.evaluate(() => { CHAT_SEND.timeout = 200; });
    answer = 'stored-stall';
    const uploadText = 'Same words after an attachment', beforeUploadText = sends.length;
    await box.fill(uploadText); await page.keyboard.press('Enter');
    await page.waitForFunction(() => !BOT_PILL.sending && document.querySelector('.toast.err'));
    const uploadTextKey = keys.at(-1), uploadSignature = await page.evaluate(() => BOT_PILL.unconfirmed.signature);
    assert.equal(sends.length, beforeUploadText + 1, 'the uncertain text reached storage');
    await page.locator('#chat-composer .p-file').setInputFiles({name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('fixture')});
    answer = 'ok';
    await send.click();
    await page.waitForFunction(() => !BOT_PILL.sending && !document.querySelector('#chat-composer textarea').value);
    assert.equal(uploads.length, 1, 'the changed payload takes the upload route');
    assert.ok(uploads[0].includes(uploadText) && uploads[0].includes('notes.txt'), 'the upload includes the text and attachment');
    assert.equal(await page.evaluate(() => BOT_PILL.unconfirmed), null, 'upload acknowledgement leaves no uncertain identity');
    assert.equal(await page.evaluate(signature => pendingWrites.has(signature), uploadSignature), false, 'the old text request leaves the cache');
    assert.equal(await page.evaluate(() => CHAT_PENDING.has('sales')), false, 'the old identity is removed from the per-tab map');
    await readsSettled();
    await loaded(() => page.reload());
    assert.equal(await page.evaluate(() => BOT_PILL.unconfirmed), null, 'reload cannot restore the acknowledged identity');
    await box.fill(uploadText); await page.keyboard.press('Enter');
    await page.waitForFunction(() => !BOT_PILL.sending && !document.querySelector('#chat-composer textarea').value);
    assert.notEqual(keys.at(-1), uploadTextKey, 'the repeated text gets a new request id after the successful upload');
    assert.equal(sends.length, beforeUploadText + 2, 'the repeated text reaches the bot instead of replaying the old answer');
    await page.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));

    // No answer at all: it ends in time, says so, keeps the draft, and Retry sends it under the same request id.
    await page.evaluate(() => { CHAT_SEND.timeout = 1500; });
    answer = 'stall';
    const stalled = 'Sent into a stalled connection';
    const firstAttempt = page.waitForRequest(r => new URL(r.url()).pathname === '/api/v2/chat/sales');
    await box.fill(stalled); await page.keyboard.press('Enter');
    const firstAttemptKey = (await firstAttempt).headers()['idempotency-key'];
    // A person may type then undo, or clear then restore, while the request is still out. Its outcome is not known
    // yet, so these transient edits must not erase the original key when the same draft is back at timeout.
    await box.fill(stalled + ' edited'); await box.fill(''); await box.fill(stalled);
    assert.equal(await page.evaluate(() => BOT_PILL.unconfirmed?.key), firstAttemptKey, 'inflight draft keeps its identity');
    const toast = page.locator('.toast.err');
    await toast.waitFor({timeout: 6000});
    assert.match(await toast.innerText(), /Not confirmed: No answer from the server in 2 seconds\. It may still arrive\. Your message is still here/);
    assert.equal(await box.inputValue(), stalled, 'the draft stays');
    assert.ok(!(await send.isDisabled()), 'Send is back');
    assert.ok(!(await send.evaluate(b => b.classList.contains('p-sending'))));
    assert.ok(await slow.isHidden());
    assert.ok(warnings.some(w => /timeout/.test(w)), 'a timed-out send is recorded');
    const stalledKey = keys.at(-1);
    answer = 'ok';
    // Retry after the old five-minute cutoff is still the same submission. Advance the browser's wall clock only;
    // real timers and the whole-send performance clock keep running normally.
    await page.evaluate(() => {
      CHAT_SEND.timeout = 30000;
      window.chatRealNow = Date.now;
      const now = Date.now(); Date.now = () => now + 6 * 60000;
      pendingWrites.clear();                         // the pending identity also survives request-cache cleanup
    });
    await toast.locator('button', {hasText: 'Retry'}).click();
    await page.waitForFunction(() => !document.querySelector('#chat-composer textarea').value && !BOT_PILL.sending);
    assert.equal(sends.at(-1), stalled);
    assert.equal(keys.at(-1), stalledKey, 'Retry keeps the request id, so a send that did land is not saved twice');
    await page.evaluate(() => { Date.now = window.chatRealNow; });
    assert.ok(warnings.every(w => !w.includes('Secret') && !w.includes('stalled')), 'no message text in any line');

    // Another timeout, then a retry near the end of the server's replay window: the first attempt's age stays.
    await page.evaluate(() => { CHAT_SEND.timeout = 200; document.querySelectorAll('.toast').forEach(t => t.remove()); });
    answer = 'stall';
    await box.fill('An old pending message'); await page.keyboard.press('Enter');
    await toast.waitFor();
    const expiredKey = keys.at(-1), originalAt = await page.evaluate(() => BOT_PILL.unconfirmed.at);
    await page.evaluate(() => { const now = Date.now(); Date.now = () => now + 47 * 60 * 60000; });
    await toast.locator('button', {hasText: 'Retry'}).click();
    await page.waitForFunction(() => !BOT_PILL.sending);
    await toast.waitFor();
    assert.equal(keys.at(-1), expiredKey, 'a late failed retry keeps the first identity');
    assert.equal(await page.evaluate(() => BOT_PILL.unconfirmed.at), originalAt, 'retry does not restart replay retention');
    await readsSettled();
    await loaded(() => page.reload());
    assert.equal(await page.evaluate(() => BOT_PILL.unconfirmed?.key), expiredKey, 'same-tab reload keeps the key with the draft');
    assert.equal(await page.evaluate(() => BOT_PILL.unconfirmed?.at), originalAt, 'reload keeps original replay age');
    await page.evaluate(() => { window.chatRealNow = Date.now; });
    // Switching away and back in the same tab must not lose the pending identity or its original age.
    await page.evaluate(() => {
      const old = BOT_PILL; chatDraftSave(old); PILLS.delete(old); BOT_PILL = null;
      const restored = botPill('sales'); old.el.replaceWith(restored.el);
      const now = window.chatRealNow(); Date.now = () => now + 49 * 60 * 60000;
      document.querySelectorAll('.toast').forEach(t => t.remove());
      CHAT_SEND.timeout = 30000;
    });
    const beforeExpiry = keys.length;
    answer = 'ok';
    // Exercise the send entry point too: Enter and an automatic retry must be safe even before a button redraw.
    await page.keyboard.press('Escape');
    await box.press('Enter');
    await toast.waitFor();
    assert.match(await toast.innerText(), /may already have sent.*Check the chat before resending/);
    assert.equal(await toast.locator('button', {hasText: 'Retry'}).count(), 0, 'expired replay has no Retry action');
    assert.equal(keys.length, beforeExpiry, 'expired replay sends no request');
    assert.equal(await box.inputValue(), 'An old pending message');
    assert.ok(await send.isDisabled(), 'expired replay disables Send');
    assert.match((await why())[0], /may already have sent/);
    await page.evaluate(() => pillNeeds(BOT_PILL, null));
    assert.match((await why())[0], /may already have sent/, 'label redraw preserves the expiry reason');
    await page.evaluate(() => {
      chatRetryLater(BOT_PILL, 'An old pending message', 'sales', {}, {});
      window.dispatchEvent(new Event('online'));
    });
    assert.equal(keys.length, beforeExpiry, 'online recovery also refuses an expired replay');
    await box.fill('');
    await box.fill('An old pending message');
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => !document.querySelector('#chat-composer textarea').value && !BOT_PILL.sending);
    assert.notEqual(keys.at(-1), expiredKey, 'intentional clear allows a new submission');
    await page.evaluate(() => { Date.now = window.chatRealNow; document.querySelectorAll('.toast').forEach(t => t.remove()); });

    // A late network failure must not add the session probe's separate five seconds to the send budget. Keep a
    // shared probe pending past that budget, then let it answer signed-out late: this write already timed out.
    const lateProbe = await page.evaluate(async () => {
      const realFetch = window.fetch, realProbe = sessionProbe;
      const started = performance.now(); let finishProbe, probeTimer, probedAt, requests = 0;
      window.fetch = (url, init) => {
        if (url !== API + '/v2/chat/sales') return realFetch(url, init);
        requests++; return new Promise((_, fail) => setTimeout(() => fail(new TypeError('offline')), 300));
      };
      sessionProbe = () => {
        probedAt = performance.now() - started;
        return new Promise(done => { finishProbe = done; probeTimer = setTimeout(() => done(true), 1500); });
      };
      try {
        await writeRequestOnce('POST', '/v2/chat/sales', {text: 'late network'}, undefined, {timeout: 400});
        return {resolved: true};
      } catch (e) {
        finishProbe(true);
        return {timedOut: !!e.timedOut, elapsed: performance.now() - started, probedAt, requests,
          kept: pendingWrites.has(e.signature)};
      } finally { clearTimeout(probeTimer); window.fetch = realFetch; sessionProbe = realProbe; }
    });
    assert.equal(lateProbe.timedOut, true, 'the shared sign-in check cannot outlast the write');
    assert.ok(lateProbe.probedAt >= 250, 'network failure happened near the send deadline');
    assert.ok(lateProbe.elapsed < 850, `send settled within its budget plus scheduling tolerance: ${lateProbe.elapsed}ms`);
    assert.equal(lateProbe.requests, 1, 'no request is retried after the budget expires');
    assert.equal(lateProbe.kept, true, 'the timed-out probe keeps the request identity');

    // Clipping to replay retention can leave exactly zero milliseconds. Zero is an expired budget, not no cap.
    const zeroBudget = await page.evaluate(async () => {
      const real = window.fetch; let requests = 0;
      window.fetch = () => { requests++; throw new Error('zero budget must not fetch'); };
      try { await writeRequestOnce('POST', '/v2/chat/sales', {text: 'zero budget'}, undefined, {timeout: 0}); }
      catch (e) { return {timedOut: !!e.timedOut, requests}; }
      finally { window.fetch = real; }
    });
    assert.deepEqual(zeroBudget, {timedOut: true, requests: 0});

    // Headers arrived, then the body stalled: still a timeout, and the request id is kept for the retry.
    const bodyStall = await page.evaluate(async () => {
      const real = window.fetch;
      window.fetch = (url, init) => Promise.resolve({status: 200, ok: true, headers: new Headers(),
        json: () => new Promise((_, fail) => init.signal.addEventListener('abort', () => fail(new DOMException('aborted', 'AbortError'))))});
      try { await writeRequestOnce('POST', '/v2/chat/sales', {text: 'body stall'}, undefined, {timeout: 200}); return 'resolved'; }
      catch (e) { return {timedOut: !!e.timedOut, kept: [...pendingWrites.keys()].some(k => k.includes('body stall'))}; }
      finally { window.fetch = real; }
    });
    assert.deepEqual(bodyStall, {timedOut: true, kept: true}, 'a stalled body times out and keeps its request id');

    // A timed-out "yes" that did land, a cleared box, and "yes" again later: a new request id, so it reaches the bot
    // instead of replaying the first answer.
    await page.evaluate(() => { CHAT_SEND.timeout = 1500; document.querySelectorAll('.toast').forEach(t => t.remove()); });
    answer = 'stall';
    await box.fill('yes'); await page.keyboard.press('Enter');
    await toast.waitFor({timeout: 6000});
    const yesKey = keys.at(-1);
    answer = 'ok';
    await page.evaluate(() => { CHAT_SEND.timeout = 30000; });
    await box.fill('');
    assert.equal(await page.evaluate(() => BOT_PILL.unconfirmed), null, 'clearing the box forgets the kept id');
    await box.fill('yes'); await page.keyboard.press('Enter');
    await page.waitForFunction(() => !document.querySelector('#chat-composer textarea').value && !BOT_PILL.sending);
    assert.notEqual(keys.at(-1), yesKey, 'the later "yes" is a new send');
    assert.equal(sends.at(-1), 'yes');

    // A pending Stop says so until the stop answers.
    let stopped; stopHold = new Promise(done => stopped = done);
    const stop = page.locator('#chat-composer .p-stop');
    // Read in the same task as the click: the fixture's run is not in its snapshots, so a late snapshot redraw clears it.
    assert.deepEqual(await stop.evaluate(b => { BOT_PILL.running = {attempt_id: 'a1'}; pillButtons(BOT_PILL); b.click();
      return [b.disabled, b.title, document.getElementById(b.getAttribute('aria-describedby'))?.textContent]; }),
      [true, 'Stopping…', 'Stopping…']);
    stopped();
    assert.deepEqual(errors, []);
    assert.deepEqual(serverErrors, [], 'no fixture server errors');
  } finally {
    try { await browser.close(); } finally {
      if (server) {
        server.closeAllConnections();
        await new Promise(resolve => server.close(resolve));
      }
    }
  }
}
(async () => { for (const engine of engines) await check(engine); console.log('ok'); })().catch(e => { console.error(e); process.exit(1); });
