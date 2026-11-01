// Offline browser regression: the Assistant's Send never does nothing (ui/app/assistant-page.js). A send the server
// refuses, or answers without a message (a sign-in page), says so and keeps the draft; text that arrived without an
// input event (autofill, a tool filling the box) still sends on a click and shows in the thread. The bot chat's retry,
// slow-send line and Return-during-a-send queue went with its composer; a bot's DM composer is in threads.cjs.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {assistantView} = require('./support/assistant-chat.cjs');
const launch = () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});

(async () => {
  const browser = await launch();
  try {
    const page = await browser.newPage({viewport: {width: 1200, height: 800}, serviceWorkers: 'block'});
    const errors = [], sends = [], thread = [];
    page.on('pageerror', e => errors.push(e.message));
    let answer = 'ok';
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
        if (answer === 'error') return json({error: {code: 'internal', detail: 'Server error'}}, 500);
        if (answer === 'html') return route.fulfill({contentType: 'text/html', body: '<!doctype html><title>Sign in</title>'});
        sends.push(text);
        const message = {id: 'm' + sends.length, from_actor: 'human:ana', body: text, created: new Date().toISOString()};
        thread.push(message);
        return json({message, fast: true});
      }
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/assistant');
    const box = page.locator('#chat-composer textarea');
    const value = () => box.inputValue();
    await box.waitFor();
    await page.waitForFunction(() => V2C?.loaded);
    const clearToasts = () => page.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));

    // The server refuses: an error shows and the draft stays.
    answer = 'error';
    await box.fill('Refused by the server'); await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('.toast.err'));
    assert.equal(await value(), 'Refused by the server', 'a refused send keeps the draft');
    await clearToasts();

    // A 200 that created nothing (a sign-in page in front of the API) is not a send either.
    answer = 'html';
    await box.fill('Answered by a sign-in page'); await page.click('#chat-composer .p-send');
    await page.waitForFunction(() => document.querySelector('.toast.err'));
    assert.equal(await value(), 'Answered by a sign-in page', 'the draft stays');
    assert.deepEqual(sends, []);
    await clearToasts();

    // Text put in the box without an input event still sends on a click.
    answer = 'ok';
    await page.evaluate(() => { document.querySelector('#chat-composer textarea').value = 'Filled by a tool'; });
    await page.click('#chat-composer .p-send');
    await page.waitForFunction(() => !document.querySelector('#chat-composer textarea').value);
    assert.deepEqual(sends, ['Filled by a tool']);
    await page.locator('#conv-thread', {hasText: 'Filled by a tool'}).waitFor();
    assert.deepEqual(errors, []);
    console.log('ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
