// The owner's chat is their Assistant like everyone else's (backend/assistant.py chat_bot, ui/app/state.js myChatBot): the
// page and their own page's tab read the Assistant, the composer takes words only, and their earlier chat with the
// Strategy Bot stays readable, read-only, under "Earlier chats". An admin keeps the Assistant, with no Earlier chats.
// Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const now = new Date().toISOString();
const msg = (id, from, body) => ({id, from_actor: from, body, refs: {}, created: now, kind: 'say'});
const bot = (name, display_name) => ({name, display_name, host: 'keeper', status: 'active', can_chat: false});

async function open(browser, me, view, strategyName) {
  const context = await browser.newContext({viewport: {width: 1280, height: 900}, serviceWorkers: 'block'});
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], {origin: 'https://tico-ui.test'});
  const page = await context.newPage();
  const errors = [], read = [], sent = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname;
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json(me);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'ben', name: 'Ben'}]});
    if (p === '/api/employees') return json([bot('coo', 'Assistant'), bot('botops', 'BotOps'), bot('goal-manager', strategyName)]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/assistant') return json(view);
    // A send: words as JSON, files as a form, like any bot chat (ui/app/api.js cloudCompose).
    if (/^\/api\/v2\/(?:uploads\/)?assistant\/messages$/.test(p)) {
      const request = route.request(), form = !p.includes('/uploads/');
      sent.push({path: p, body: form ? request.postDataJSON() : request.postDataBuffer().toString('latin1')});
      return json({message: msg('s' + sent.length, 'human:' + me.id, 'sent'), reply: null, fast: false, intent: null});
    }
    if (p === `/api/v2/conversations/${view.room_id}/snapshot`) return json({messages: view.messages, execution: null, has_more: false, next_before: null});
    if (p === '/api/v2/conversations/old1/messages') {
      read.push(url.searchParams.get('before'));
      return json(url.searchParams.get('before')
        ? {messages: [msg('o1', 'human:ana', 'Book the offsite')], has_more: false, next_before: null}
        : {messages: [msg('o2', 'human:ana', 'Plan my week'), msg('o3', 'bot:coo', 'Done: three tasks for Monday')], has_more: true, next_before: 'o2'});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  return {page, errors, read, sent};
}

// The person's own copy and paste: words, or a screenshot, put on the clipboard and pasted with the keyboard.
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
async function paste(page, what) {
  await page.evaluate(async ([what, png]) => {
    if (what === 'text') return navigator.clipboard.writeText('Why did revenue dip in March?');
    const bytes = Uint8Array.from(atob(png), c => c.charCodeAt(0));
    await navigator.clipboard.write([new ClipboardItem({'image/png': new Blob([bytes], {type: 'image/png'})})]);
  }, [what, PNG]);
  await page.locator('#chat-composer .p-text').focus();
  await page.keyboard.press('ControlOrMeta+V');
}

const view = (over) => ({available: true, state: 'active', can_turn_on: false, room_id: 'room1', has_more: false, next_before: null,
  execution: null, actions: {}, pending: [], earlier: null, messages: [], ...over});

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    // The owner: the Assistant, by name, and the earlier Strategy Bot chat under Earlier chats.
    const owner = {id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true};
    const mine = view({bot: 'coo', name: 'Assistant', room_id: 'assistant1', messages: [msg('c1', 'bot:coo', 'Revenue is on track.')],
      earlier: {room_id: 'old1', bot: 'goal-manager', name: 'Chief'}});
    {
      const {page, errors, read, sent} = await open(browser, owner, mine, 'Chief');
      await page.goto('https://tico-ui.test/#/assistant');
      await page.locator('#chat-composer .p-text').waitFor();
      assert.equal(await page.locator('#main .asst-top h1').innerText(), 'Assistant', 'owner: the page is their Assistant');
      // Words only, as for everyone: pasted words land and send, a pasted picture attaches nothing.
      const box = page.locator('#chat-composer .p-text');
      assert.equal(await page.locator('#chat-composer .p-attach').count(), 0, 'owner: no attach button');
      await paste(page, 'image');
      await paste(page, 'text');
      assert.equal(await box.inputValue(), 'Why did revenue dip in March?', 'owner: pasted words land in the box');
      assert.equal(await page.locator('#chat-composer .p-chips').innerHTML(), '', 'owner: no picture attached');
      await box.press('Enter');
      await page.waitForFunction(() => !document.querySelector('#chat-composer .p-text').value);
      assert.deepEqual(sent.map(s => [s.path, s.body]), [['/api/v2/assistant/messages', {text: 'Why did revenue dip in March?'}]], 'owner: words send as words');
      // Earlier chats: closed, loaded when opened, read-only, older pages on request.
      const earlier = page.locator('#asst-earlier');
      assert.equal(await earlier.isVisible(), true, 'owner: Earlier chats is there');
      assert.deepEqual(read, [], 'owner: nothing loaded until opened');
      await earlier.locator('summary').click();
      await earlier.getByText('Done: three tasks for Monday').waitFor();
      await earlier.locator('[data-earlier-more]').click();
      await earlier.getByText('Book the offsite').waitFor();
      assert.deepEqual(read, [null, 'o2'], 'owner: newest page, then the older one');
      assert.equal(await earlier.locator('textarea, input, [data-confirm]').count(), 0, 'owner: nothing to send from there');
      await page.goto('https://tico-ui.test/#/person/ana');
      await page.locator('#ptabs button').first().waitFor();
      assert.equal(await page.locator('#ptabs button').first().innerText(), 'Assistant', 'owner: the tab on their own page');
      assert.deepEqual(errors, [], 'owner: no page errors');
      await page.close();
    }
    // An admin is not the owner: the Assistant, as before, and no Earlier chats.
    {
      const admin = {id: 'ben', role: 'human', bot_admin: true, name: 'Ben', email: 'ben@example.test', cloud: true};
      const {page, errors, sent} = await open(browser, admin, view({bot: 'coo', name: 'Assistant'}), 'Strategy Bot');
      await page.goto('https://tico-ui.test/#/assistant');
      await page.locator('#chat-composer .p-text').waitFor();
      assert.equal(await page.locator('#main .asst-top h1').innerText(), 'Assistant', 'admin: the Assistant page');
      assert.equal(await page.locator('#asst-earlier').isHidden(), true, 'admin: no Earlier chats');
      // The Assistant takes words only: pasted words land, a pasted picture attaches nothing.
      assert.equal(await page.locator('#chat-composer .p-attach').count(), 0, 'admin: no attach button');
      await paste(page, 'image');
      await paste(page, 'text');
      assert.equal(await page.locator('#chat-composer .p-text').inputValue(), 'Why did revenue dip in March?', 'admin: pasted words land');
      assert.equal(await page.locator('#chat-composer .p-chips').innerHTML(), '', 'admin: no picture attached');
      await page.locator('#chat-composer .p-text').press('Enter');
      await page.waitForFunction(() => !document.querySelector('#chat-composer .p-text').value);
      assert.deepEqual(sent.map(s => s.path), ['/api/v2/assistant/messages'], 'admin: words only');
      assert.deepEqual(errors, [], 'admin: no page errors');
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
