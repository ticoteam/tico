// The owner talks to the CEO Bot in place of an Assistant (ui/app/assistant-page.js, ui/app/state.js meCeo): the menu tab
// and the page read the CEO Bot's name (renamed or not) with no "Assistant" wording, and the owner's own Assistant chats
// from before stay readable under "Earlier chats". Everyone else, an admin included, keeps the Assistant. Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const now = new Date().toISOString();
const msg = (id, from, body) => ({id, from_actor: from, body, refs: {}, created: now, kind: 'say'});
const bot = (name, display_name) => ({name, display_name, host: 'keeper', status: 'active', can_chat: false});

async function open(browser, me, view, ceoName) {
  const page = await browser.newPage({viewport: {width: 1280, height: 900}, serviceWorkers: 'block'});
  const errors = [], read = [];
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
    if (p === '/api/employees') return json([bot('coo', 'Assistant'), bot('botops', 'BotOps'), bot('goal-manager', ceoName)]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/assistant') return json(view);
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
  return {page, errors, read};
}

const view = (over) => ({available: true, state: 'active', can_turn_on: false, room_id: 'room1', has_more: false, next_before: null,
  execution: null, actions: {}, pending: [], earlier: null, messages: [], ...over});

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    // The owner: the tab and the page are the CEO Bot, by the name they gave it, and nothing says "Assistant".
    const owner = {id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true};
    const ceo = view({bot: 'goal-manager', ceo: true, name: 'Chief', room_id: 'ceo1', messages: [msg('c1', 'bot:goal-manager', 'Revenue is on track.')],
      earlier: {room_id: 'old1', bot: 'coo', name: 'Morgan'}});
    {
      const {page, errors, read} = await open(browser, owner, ceo, 'Chief');
      await page.goto('https://tico-ui.test/#/assistant');
      await page.locator('#chat-composer .p-text').waitFor();
      assert.equal(await page.locator('#nav-assistant [data-nav-name]').innerText(), 'Chief', 'owner: the menu tab is the CEO Bot by its name');
      assert.equal(await page.locator('#main .asst-top h1').innerText(), 'Chief', 'owner: the page is the CEO Bot');
      assert.doesNotMatch(await page.locator('#main').innerText(), /Assistant/, 'owner: no Assistant wording on the page');
      assert.doesNotMatch(await page.locator('#nav-assistant').innerText(), /Assistant/, 'owner: nor on the tab');
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
      assert.equal(await page.locator('#ptabs button').first().innerText(), 'Chief', 'owner: the tab on their own page');
      assert.deepEqual(errors, [], 'owner: no page errors');
      await page.close();
    }
    // An admin is not the owner: the Assistant, as before, and no Earlier chats.
    {
      const admin = {id: 'ben', role: 'human', bot_admin: true, name: 'Ben', email: 'ben@example.test', cloud: true};
      const {page, errors} = await open(browser, admin, view({bot: 'coo', ceo: false, name: 'Assistant'}), 'CEO Bot');
      await page.goto('https://tico-ui.test/#/assistant');
      await page.locator('#chat-composer .p-text').waitFor();
      assert.equal(await page.locator('#nav-assistant [data-nav-name]').innerText(), 'Assistant', 'admin: the Assistant tab');
      assert.equal(await page.locator('#main .asst-top h1').innerText(), 'Assistant', 'admin: the Assistant page');
      assert.equal(await page.locator('#asst-earlier').isHidden(), true, 'admin: no Earlier chats');
      assert.deepEqual(errors, [], 'admin: no page errors');
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
