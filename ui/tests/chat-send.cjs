// Offline browser regression: a bot's Send never does nothing. Typed during the thread's load, the draft survives the
// load and a redraw and sends once; a send the server refuses, or answers without a message (a sign-in page),
// says so and keeps the draft; Return during a send that is still out sends after it; text that arrived without an
// input event (autofill, a tool filling the box) still sends on a click.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const launch = () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});

(async () => {
  const browser = await launch();
  try {
    const page = await browser.newPage({viewport: {width: 1200, height: 800}, serviceWorkers: 'block'});
    const errors = [], sends = [];
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
      if (p === '/api/employees') return json([{name: 'botops', display_name: 'BotOps', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/conversations')
        return json({conversations: url.searchParams.get('chat_with') ? [{id: 'c1', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:botops']}] : []});
      if (p.endsWith('/snapshot')) { await snapshotHeld; return json({messages: [], execution: null}); }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p === '/api/v2/chat/botops') {
        const text = route.request().postDataJSON().text;
        if (answer === 'error') return json({error: {code: 'internal', detail: 'Server error'}}, 500);
        if (answer === 'html') return route.fulfill({contentType: 'text/html', body: '<!doctype html><title>Sign in</title>'});
        if (hold) await hold;
        sends.push(text);
        return json({conversation: {id: 'c1'}, message: {id: 'm' + sends.length, from_actor: 'human:ana', body: text, created: new Date().toISOString()}});
      }
      return json({});
    });
    await page.goto('https://tico-ui.test/#/bot/botops');
    const box = page.locator('#chat-composer textarea');
    const value = () => box.inputValue();
    const toasts = () => page.locator('.toast.err').allInnerTexts();
    await box.waitFor();
    assert.match(await page.locator('#conv-thread').innerText(), /Loading the thread/, 'still loading');

    // Typed while the thread loads; the page redraws and the load finishes under it; Return right after sends once.
    const first = 'Typed while the thread was loading';
    await box.click(); await box.pressSequentially(first);
    await page.evaluate(() => { BOT = null; route(); });
    release();
    await page.waitForFunction(() => V2C?.loaded);
    assert.equal(await value(), first, 'the draft survives the load and the redraw');
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => !document.querySelector('#chat-composer textarea').value);
    assert.deepEqual(sends, [first], 'sent once');

    // The server refuses: an error shows and the draft stays.
    answer = 'error';
    await box.fill('Refused by the server'); await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('.toast.err'));
    assert.equal(await value(), 'Refused by the server', 'a refused send keeps the draft');
    assert.match((await toasts()).join(' '), /Not sent/);
    await page.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));

    // A 200 that created nothing (a sign-in page in front of the API) is not a send either.
    answer = 'html';
    await box.fill('Answered by a sign-in page'); await page.click('#chat-composer .p-send');
    await page.waitForFunction(() => document.querySelector('.toast.err'));
    assert.equal(await value(), 'Answered by a sign-in page', 'the draft stays');
    assert.match((await toasts()).join(' '), /Not sent/);
    assert.deepEqual(sends, [first]);
    await page.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));

    // Return while the previous send is still out: the second message follows it instead of vanishing.
    answer = 'ok';
    let open; hold = new Promise(done => open = done);
    await box.fill('First of two'); await page.keyboard.press('Enter');
    await page.waitForFunction(() => BOT_PILL.sending);
    await box.fill('Second of two'); await page.keyboard.press('Enter');
    open(); hold = null;
    await page.waitForFunction(() => !document.querySelector('#chat-composer textarea').value && !BOT_PILL.sending);
    assert.deepEqual(sends.slice(1), ['First of two', 'Second of two'], 'both are sent, in order');

    // Text put in the box without an input event still sends on a click.
    await page.evaluate(() => { document.querySelector('#chat-composer textarea').value = 'Filled by a tool'; });
    await page.click('#chat-composer .p-send');
    await page.waitForFunction(() => !document.querySelector('#chat-composer textarea').value);
    assert.equal(sends.at(-1), 'Filled by a tool');
    const thread = await page.locator('#conv-thread').innerText();
    for (const text of [first, 'First of two', 'Second of two', 'Filled by a tool']) assert.match(thread, new RegExp(text));
    assert.deepEqual(errors, []);
    console.log('ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
