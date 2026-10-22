// Offline browser regression (Dev 371): a slow chat send is visible, and a stalled one ends. While a message is out,
// Send shows a spinner at full strength (not the dimmed look of an empty box); after a few seconds a line under the box
// says it is still sending, and that it is trying again when the server answers 503; a send with no answer in time
// ends in "Not confirmed ... still here" with Retry, which resends under the same request id, also when the answer's
// headers came but its body stalled. A slow or failed send leaves
// one console line with numbers only, never the message text.
const {chromium, webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const launch = {
  chromium: () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined}),
  // The bug was seen in mobile Safari: the hosted job installs WebKit, so the abort path gets a Safari-engine receipt.
  webkit: () => webkit.launch({headless: true}),
};
// TICO_UI_ENGINES=chromium runs one engine where WebKit's system libraries are missing.
const engines = (process.env.TICO_UI_ENGINES || 'chromium,webkit').split(',');

async function check(engine) {
  const browser = await launch[engine]();
  try {
    const page = await browser.newPage({viewport: {width: 1200, height: 800}, serviceWorkers: 'block'});
    const errors = [], sends = [], keys = [], warnings = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => { if (m.type() === 'warning' && m.text().startsWith('chat send')) warnings.push(m.text()); });
    let answer = 'ok', busy = 0, hold = null;
    await page.route('**/*', async route => {
      const url = new URL(route.request().url()), p = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
      if (p === '/api/employees') return json([{name: 'botops', display_name: 'BotOps', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/conversations')
        return json({conversations: url.searchParams.get('chat_with') ? [{id: 'c1', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:botops']}] : []});
      if (p.endsWith('/snapshot')) return json({messages: [], execution: null});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p === '/api/v2/chat/botops') {
        const text = route.request().postDataJSON().text;
        keys.push(route.request().headers()['idempotency-key']);
        if (answer === 'stall') return;                       // never answered: the page has to give up on its own
        if (busy > 0) { busy--; return json({error: {code: 'unavailable', detail: 'Busy'}}, 503); }
        if (hold) await hold;
        sends.push(text);
        return json({conversation: {id: 'c1'}, message: {id: 'm' + sends.length, from_actor: 'human:ana', body: text, created: new Date().toISOString()}});
      }
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/bot/botops');
    const box = page.locator('#chat-composer textarea'), send = page.locator('#chat-composer .p-send');
    const slow = page.locator('#chat-composer .p-slow');
    await page.waitForFunction(() => V2C?.loaded);
    await page.evaluate(() => { CHAT_SEND.slow = 300; });

    // The empty box dims Send; a send that is out shows a spinner at full strength, and after a moment says so in words.
    assert.equal(await send.evaluate(b => getComputedStyle(b).opacity), '0.45', 'an empty box dims Send');
    let open; hold = new Promise(done => open = done);
    const first = 'Secret words that never reach the console';
    await box.fill(first); await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('#chat-composer .p-send').classList.contains('p-sending'));
    assert.equal(await send.evaluate(b => getComputedStyle(b).opacity), '1', 'sending is not the dimmed empty look');
    assert.equal(await send.evaluate(b => getComputedStyle(b, '::after').content), '""', 'the spinner shows');
    assert.ok(await send.isDisabled(), 'no double send while it is out');
    await slow.waitFor({state: 'visible'});
    assert.equal(await slow.innerText(), 'Still sending…');
    open(); hold = null;
    await page.waitForFunction(() => !document.querySelector('#chat-composer textarea').value && !BOT_PILL.sending);
    assert.ok(await slow.isHidden(), 'the line goes once it is sent');
    assert.ok(!(await send.evaluate(b => b.classList.contains('p-sending'))), 'the spinner goes');
    assert.deepEqual(sends, [first]);
    assert.equal(warnings.length, 1, 'a slow send leaves one line');
    assert.match(warnings[0], /chat send/);
    assert.ok(!warnings[0].includes('Secret'), 'never the message text');

    // The server answers 503 twice, then takes it: the line says it is trying again, and it sends once.
    busy = 2;
    await box.fill('Through two busy answers'); await page.keyboard.press('Enter');
    await page.waitForFunction(() => /trying again/.test(document.querySelector('#chat-composer .p-slow')?.textContent || ''));
    await page.waitForFunction(() => !document.querySelector('#chat-composer textarea').value && !BOT_PILL.sending);
    assert.equal(sends.at(-1), 'Through two busy answers');
    assert.equal(new Set(keys.slice(-3)).size, 1, 'the retries reuse one request id');

    // No answer at all: it ends in time, says so, keeps the draft, and Retry sends it under the same request id.
    await page.evaluate(() => { CHAT_SEND.timeout = 1500; });
    answer = 'stall';
    const stalled = 'Sent into a stalled connection';
    await box.fill(stalled); await page.keyboard.press('Enter');
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
    await page.evaluate(() => { CHAT_SEND.timeout = 30000; });
    await toast.locator('button', {hasText: 'Retry'}).click();
    await page.waitForFunction(() => !document.querySelector('#chat-composer textarea').value && !BOT_PILL.sending);
    assert.equal(sends.at(-1), stalled);
    assert.equal(keys.at(-1), stalledKey, 'Retry keeps the request id, so a send that did land is not saved twice');
    assert.ok(warnings.every(w => !w.includes('Secret') && !w.includes('stalled')), 'no message text in any line');

    // Headers arrived, then the body stalled: still a timeout, and the request id is kept for the retry.
    const bodyStall = await page.evaluate(async () => {
      const real = window.fetch;
      window.fetch = (url, init) => Promise.resolve({status: 200, ok: true, headers: new Headers(),
        json: () => new Promise((_, fail) => init.signal.addEventListener('abort', () => fail(new DOMException('aborted', 'AbortError'))))});
      try { await writeRequestOnce('POST', '/v2/chat/botops', {text: 'body stall'}, undefined, {timeout: 200}); return 'resolved'; }
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
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
}
(async () => { for (const engine of engines) await check(engine); console.log('ok'); })().catch(e => { console.error(e); process.exit(1); });
