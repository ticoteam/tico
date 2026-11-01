// Offline browser check: a message the server sent into a fresh session because its topic changed
// (backend/topic_change.py, `refs.topic_changed`) shows one quiet "Topic changed" line above it, the same divider as
// "New session started", and nothing else: no undo link, no line on any other message. Both when the send's own answer
// adopts it and when a later snapshot lists it; desktop and phone.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const launch = () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});

async function run(browser, viewport) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const at = n => new Date(Date.UTC(2026, 9, 18, 12, n)).toISOString();
  const earlier = [
    {id: 'a1', sender_id: 'human:ana', recipient_id: 'bot:sales', kind: 'say', body: 'Fix the login redirect bug', created_at: at(0)},
    {id: 'a2', sender_id: 'bot:sales', recipient_id: 'human:ana', kind: 'say', in_reply_to: 'a1', body: 'Done.', created_at: at(1)},
  ];
  let server = earlier.slice();
  await page.route('**/*', async route => {
    const url = new URL(route.request().url()), p = url.pathname;
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
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
    if (p.endsWith('/snapshot')) return json({messages: server.slice(), execution: null});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    if (p === '/api/v2/chat/sales') {
      const text = route.request().postDataJSON().text;
      const message = {id: 'm1', sender_id: 'human:ana', recipient_id: 'bot:sales', kind: 'say', body: text, created_at: at(9),
                       conversation_id: 'c1', refs: {topic_changed: true}};
      server = [...server, message];
      return json({conversation: {id: 'c1'}, message});
    }
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/bot/sales');
  const box = page.locator('#chat-composer textarea');
  await box.waitFor();
  await page.waitForFunction(() => V2C?.loaded);
  assert.equal(await page.locator('#conv-thread .chat-topic-break').count(), 0, 'no line before a topic changes');

  await box.click(); await box.fill('Draft a blog post about our new pricing');
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => [...document.querySelectorAll('#conv-thread [data-message="m1"]')].length === 1);
  const check = async when => {
    const lines = page.locator('#conv-thread .chat-topic-break');
    assert.equal(await lines.count(), 1, `${when}: one line`);
    assert.equal((await lines.innerText()).trim().toLowerCase(), 'topic changed', `${when}: it says Topic changed`);
    assert.equal(await lines.locator('a, button').count(), 0, `${when}: no undo link or button`);
    // Right above the new message, in the same divider style as "New session started".
    assert.equal(await page.evaluate(() => document.querySelector('#conv-thread .chat-topic-break').nextElementSibling?.dataset.message), 'm1', `${when}: above the message`);
    assert.equal(await lines.evaluate(el => el.classList.contains('conv-break')), true, `${when}: the session divider`);
    const box = await lines.boundingBox();
    assert.ok(box && box.width > 0 && box.width <= viewport.width, `${when}: visible within the screen`);
  };
  await check('after the send');
  // A redraw from the server's snapshot keeps exactly one line, still above the same message.
  await page.evaluate(() => { BOT = null; route(); });
  await page.waitForFunction(() => V2C?.loaded && document.querySelector('#conv-thread [data-message="m1"]'));
  await check('after a reload of the thread');
  assert.deepEqual(errors, []);
  await page.close();
}

(async () => {
  const browser = await launch();
  try {
    await run(browser, {width: 1200, height: 800});
    await run(browser, {width: 390, height: 844});
    console.log('Topic changed: one quiet line above the message, no undo, desktop and phone: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
