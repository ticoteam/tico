// "What it has learned" (ui/app/learned.js): linked from the top of your chat; the owner sees the company layer and their
// own, everyone else only their own; edit and delete send exactly that entry's change and redraw without it. Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const now = new Date().toISOString();
const bot = (name, display_name) => ({name, display_name, host: 'keeper', status: 'active', can_chat: false});
const entry = (id, layer, text, taught) => ({id, layer, text, created_at: now, updated_at: null, taught_in_chat: taught});

async function open(browser, me, learned, chat) {
  const page = await browser.newPage({viewport: {width: 1280, height: 900}, serviceWorkers: 'block'});
  const errors = [], writes = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json(me);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'ben', name: 'Ben'}]});
    if (p === '/api/employees') return json([bot('coo', 'Assistant'), bot('goal-manager', 'Chief')]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/assistant') return json(chat);
    if (p === `/api/v2/conversations/${chat.room_id}/snapshot`) return json({messages: [], execution: null, has_more: false, next_before: null});
    if (p === '/api/v2/assistant/learned' && req.method() === 'GET') return json(learned);
    const one = p.match(/^\/api\/v2\/assistant\/learned\/([^/]+)$/);
    if (one) {
      const body = req.postDataJSON?.() ?? null;
      writes.push([req.method(), one[1], body]);
      if (req.method() === 'PATCH') return json({entry: {...entry(one[1], 'personal', body.text, true), updated_at: now}});
      if (req.method() === 'DELETE') return json({deleted: one[1]});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  return {page, errors, writes};
}

const chat = over => ({available: true, state: 'active', can_turn_on: false, room_id: 'room1', has_more: false, next_before: null,
  execution: null, actions: {}, pending: [], earlier: null, messages: [], ...over});

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    // The owner: from their Assistant chat to the page, with both layers; they edit their own entry and delete a
    // company one.
    {
      const owner = {id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true};
      const learned = {personal: [entry('p1', 'personal', 'Short summaries, numbers first', true)],
        company: {vision: 'Every team runs on bots', description: '', priorities: [{id: 'g1', title: 'Ship the desktop app', status: 'green'}],
          entries: [entry('c1', 'company', 'Launches matter more than hiring news', true), entry('c2', 'company', 'Partners before press', false)]}};
      // The owner's chat is their Assistant like everyone else's (backend/assistant.py chat_bot).
      const {page, errors, writes} = await open(browser, owner, learned, chat({bot: 'coo', name: 'Assistant', room_id: 'assistant1'}));
      await page.goto('https://tico-ui.test/#/assistant');
      await page.locator('#chat-composer .p-text').waitFor();
      await page.locator('#main .asst-learned').click();
      await page.locator('#learned-company').waitFor();
      assert.equal(new URL(page.url()).hash, '#/assistant/learned', 'owner: the link opens the page');
      assert.match(await page.locator('.learned-head').innerText(), /Assistant reads this before every reply/, 'owner: names their Assistant');
      const personal = page.locator('#learned-personal'), company = page.locator('#learned-company');
      assert.match(await personal.innerText(), /Short summaries, numbers first[\s\S]*Taught in chat/, 'owner: their own layer');
      assert.match(await company.innerText(), /Every team runs on bots[\s\S]*Ship the desktop app[\s\S]*Launches matter more than hiring news[\s\S]*Partners before press/,
        'owner: vision, priorities and what they taught it');
      // Edit: Enter saves exactly that entry's new words.
      await personal.locator('[data-learned="p1"] [data-learned-edit]').click();
      await personal.locator('.learned-input').fill('Numbers first, then one line');
      await personal.locator('.learned-input').press('Enter');
      await personal.getByText('Numbers first, then one line').waitFor();
      assert.deepEqual(writes, [['PATCH', 'p1', {text: 'Numbers first, then one line'}]], 'owner: one PATCH for that entry');
      // Delete asks once more on the same button, then that entry is gone and only it.
      const del = company.locator('[data-learned="c1"] [data-learned-delete]');
      await del.click();
      assert.equal(await del.innerText(), 'Delete for good?', 'owner: delete asks once more');
      assert.equal(writes.length, 1, 'owner: nothing deleted on the first press');
      await del.click();
      await company.locator('[data-learned="c1"]').waitFor({state: 'detached'});
      assert.deepEqual(writes[1].slice(0, 2), ['DELETE', 'c1'], 'owner: one DELETE for that entry');
      assert.equal(await company.locator('[data-learned="c2"]').count(), 1, 'owner: the other entry stays');
      assert.deepEqual(errors, [], 'owner: no page errors');
      await page.close();
    }
    // A member: their own layer only, named for their Assistant; with nothing taught yet it says how to teach it.
    {
      const member = {id: 'ben', role: 'human', name: 'Ben', email: 'ben@example.test', cloud: true};
      const {page, errors} = await open(browser, member, {personal: [], company: null}, chat({bot: 'coo', name: 'Assistant'}));
      await page.goto('https://tico-ui.test/#/assistant/learned');
      await page.locator('#learned-personal').waitFor();
      assert.equal(await page.locator('#learned-company').count(), 0, 'member: no company layer');
      assert.match(await page.locator('#learned-personal').innerText(), /Nothing yet\. Tell Assistant in your chat/, 'member: how to teach it');
      assert.deepEqual(errors, [], 'member: no page errors');
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
