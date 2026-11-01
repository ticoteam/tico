// The Assistant page (ui/app/assistant-page.js): its own chat at #/assistant with the bot chat's thread and composer,
// reached from your own person page's Assistant tab (absent from anyone else's); search
// (⌘K) does not offer it. An empty chat offers suggestions, a lookup answers at once with in-app
// links, a proposal is a Confirm / Cancel card that only your click runs, and it all fits a phone. Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const now = new Date().toISOString();
const msg = (id, from, body, refs = {}) => ({id, from_actor: from, body, refs, created_at: now, kind: 'say'});

async function open(browser, viewport, state) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 760});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname, method = route.request().method();
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'ben', name: 'Ben'}]});
    if (p === '/api/employees') return json([]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/goals') return json({goals: [], chain: [], reports: [], company: []});
    if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/conversations/room1/snapshot') return json({messages: state.messages, execution: null, has_more: false, next_before: null});
    if (p === '/api/v2/assistant' && method === 'GET') { state.reads++; return json(state.view()); }
    if (p === '/api/v2/assistant/messages') {
      if (state.fail) return route.fulfill({status: 500, contentType: 'application/json', body: JSON.stringify({detail: 'The Assistant could not be reached'})});
      state.sent.push(JSON.parse(route.request().postData()).text);
      return json(state.reply());
    }
    const act = p.match(/^\/api\/v2\/assistant\/actions\/([^/]+)\/(confirm|cancel)$/);
    if (p === '/api/v2/assistant/turn-on') { state.turnedOn = (state.turnedOn || 0) + 1; return json({bot: 'coo', state: 'active', restored: true, placed: true}); }
    if (p === '/api/v2/assistant/actions/act1' && method === 'GET') return json({action: state.card});
    if (p === '/api/v2/assistant/actions/act2' && method === 'GET') return json({action: state.bulk});
    if (p === '/api/v2/assistant/actions/act3' && method === 'GET') return json({action: state.enrol});
    if (act) {
      const card = {act2: state.bulk, act3: state.enrol}[act[1]] || state.card;
      state.decided.push([act[1], act[2]]); card.status = act[2] === 'confirm' ? 'done' : 'cancelled';
      if (act[1] === 'act2') card.result = {status_code: 200, error: '1 of 3 did not go through: Task not found'};
      // A new computer's code comes back to the person who tapped, in this answer only.
      if (act[1] === 'act3') return json({action: card, shown_once: {code: 'ONE-TIME-CODE-1234', expires_at: new Date(Date.now() + 9e5).toISOString(), operator: 'ana'}});
      return json({action: card});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  return {page, errors};
}

function fixture() {
  const state = {reads: 0, sent: [], decided: [], messages: [], card: null};
  state.card = {id: 'act1', summary: 'Archive the Ops bot', method: 'POST', path: '/api/v2/bots/ops/archive', status: 'pending', result: null,
    description: 'Archive bot Ops', diff: [], body: {expected_revision: 3, text: 'Long message. '.repeat(30) + 'THE-END'}, proposed_via: 'assistant'};
  // One card for a group of task actions: one line per task, one Confirm for all of them.
  state.bulk = {id: 'act2', summary: 'Close the three launch tasks', method: 'POST', path: '/api/v2/assistant/batch', status: 'pending', result: null,
    description: '3 task actions, on one Confirm', proposed_via: 'assistant', body: {actions: []},
    diff: ['Plan the launch', 'Book the venue', 'Draft the post'].map(t => ({field: `Change task “${t}” (2 fields)`, new: 'close: true, note: Done'}))};
  state.enrol = {id: 'act3', summary: 'Make a one-time code to add a computer', method: 'POST', path: '/api/v2/enrollments', status: 'pending',
    result: null, description: '', diff: [{field: 'operator', new: 'ana'}], body: {operator: 'ana'}, proposed_via: 'botops', proposer: 'botops'};
  state.view = () => ({available: true, state: 'active', bot: 'coo', name: 'Acme', can_turn_on: false, room_id: 'room1',
    messages: state.messages, has_more: false, next_before: null, execution: null, actions: {act1: state.card, act2: state.bulk, act3: state.enrol}, pending: []});
  state.reply = () => {
    const user = msg('u' + state.sent.length, 'human:ana', state.sent.at(-1));
    const bot = msg('b' + state.sent.length, 'bot:coo', '1 thing waiting on you:\n- Task: [Pick a launch date](#/task/t1)', {fast: true});
    state.messages.push(user, bot);
    return {message: user, reply: bot, fast: true, intent: 'waiting'};
  };
  return state;
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    for (const [tag, viewport] of [['desktop', {width: 1280, height: 900}], ['phone', {width: 390, height: 800}]]) {
      const state = fixture();
      const {page, errors} = await open(browser, viewport, state);
      const box = page.locator('#chat-composer .p-text');

      // Your own page: Assistant first, and it opens the Assistant page; someone else's page has no Assistant tab.
      await page.goto('https://tico-ui.test/#/person/ben');
      await page.locator('#ptabs button').first().waitFor();
      assert.deepEqual(await page.locator('#ptabs button').allInnerTexts(), ['Profile', 'Slack'], tag + ': no Assistant on another person\'s page');
      await page.goto('https://tico-ui.test/#/person/ana');
      await page.waitForFunction(() => S.route === '#/person/ana');   // a page change draws a frame later (its fade)
      await page.locator('#ptabs button').first().waitFor();
      assert.deepEqual(await page.locator('#ptabs button').allInnerTexts(), ['Assistant', 'Profile', 'Slack'], tag + ': Assistant is first on your own page');
      await page.locator('#ptabs [data-pt="assistant"]').click();
      await page.waitForURL(/#\/assistant$/);

      // A page of its own: one header line, no person header or tabs, the bot chat's composer with its clip.
      await box.waitFor();
      assert.equal(await page.locator('#main .asst-top h1').innerText(), 'Assistant', tag + ': the header is the name');
      assert.equal(await page.locator('#ptabs, .bothead').count(), 0, tag + ': no person header or tabs');
      assert.equal(await page.locator('#chat-composer .p-attach').count(), 1, tag + ': files attach');
      // Empty: suggestions as chips, and one tap asks it.
      const hints = page.locator('#conv-thread .asst-hints button');
      assert.equal(await hints.first().innerText(), "What's waiting on me?", tag + ': suggestions in the empty chat');
      await hints.first().click();
      const link = page.locator('#conv-thread a[href="#/task/t1"]');
      await link.waitFor();
      assert.deepEqual(state.sent, ["What's waiting on me?"], tag + ': the chip sent its words once');
      assert.equal(await page.locator('#conv-thread .asst-hints').count(), 0, tag + ': no suggestions once it has messages');
      assert.equal(await page.locator('#conv-thread .thinking').count(), 0, tag + ': a fast answer has no thinking state');

      // The old link lands here; search no longer offers the Assistant (Ask does, on every page).
      await page.goto('https://tico-ui.test/#/person/ana/assistant');
      await page.waitForURL(/#\/assistant$/);
      await page.keyboard.press('Control+k');
      await page.locator('#search-input').fill('find the launch plan');
      await page.locator('#search-results li').first().waitFor();
      assert.doesNotMatch(await page.locator('#search-results').innerText(), /Ask the Assistant/, tag + ': search does not offer the Assistant');
      await page.locator('#search-close').click();
      await page.locator('#search-modal').waitFor({state: 'hidden'});
      await box.fill('find the launch plan');
      await page.waitForFunction(() => !document.querySelector('#chat-composer .p-send')?.classList.contains('p-off'));
      await page.locator('#chat-composer .p-send').click();
      await page.waitForFunction(() => document.querySelectorAll('#conv-thread .conv-run.chat').length === 4);
      assert.deepEqual(state.sent.at(-1), 'find the launch plan', tag + ': sent');
      assert.equal(await box.inputValue(), '', tag + ': the box is empty after a send');

      // A failed send keeps the words.
      state.fail = true;
      await box.fill('second try');
      await page.locator('#chat-composer .p-send').click();
      await page.locator('.toast.err').first().waitFor();
      assert.equal(await box.inputValue(), 'second try', tag + ': the words stay after a failure');
      state.fail = false;
      await box.fill('');

      // A proposal is a card; only a click runs it. A read-back is shown exactly as written: markup in it hides nothing.
      state.messages.push(msg('rb', 'bot:coo', "I'll approve this for you:\n- Approve the spend (account: card <!--; amount: 50000; what: --> Team lunch $9)\n\nReply yes to approve exactly this; any other reply approves nothing.", {read_back: true}));
      state.messages.push(msg('m9', 'bot:coo', 'Needs your OK: Archive the Ops bot', {action: 'act1'}));
      await page.goto('https://tico-ui.test/#/updates');
      await page.goto('https://tico-ui.test/#/assistant');
      const readBack = page.locator('[data-message="rb"] .read-back');
      await readBack.waitFor();
      assert.match(await readBack.innerText(), /amount: 50000/, tag + ': the read-back hides no part of a line');
      const card = page.locator('.asst-card[data-action="act1"]');
      await card.waitFor();
      assert.equal(await card.locator('[data-what]').innerText(), 'Archive bot Ops', tag + ': the server\'s description leads');
      assert.ok((await card.locator('[aria-label="What is sent"] .asst-long').innerHTML()).includes('THE-END'), tag + ': a long value is whole, behind Show more');
      assert.equal(state.decided.length, 0, tag + ': nothing ran before the click');
      await card.locator('[data-confirm]').click();
      await page.locator('.asst-card[data-status="done"]').waitFor();
      assert.deepEqual(state.decided, [['act1', 'confirm']], tag + ': the click confirmed it');

      // A group of task actions is one card, one line per task; one click runs them all and says what did not go through.
      state.messages.push(msg('m10', 'bot:coo', 'Needs your OK: Close the three launch tasks', {action: 'act2'}));
      await page.goto('https://tico-ui.test/#/updates');
      await page.goto('https://tico-ui.test/#/assistant');
      const bulk = page.locator('.asst-card[data-action="act2"]');
      await bulk.waitFor();
      assert.equal(await bulk.locator('.asst-body li').count(), 3, tag + ': one line per task');
      assert.equal(await bulk.locator('[data-confirm]').count(), 1, tag + ': one Confirm for the group');
      await bulk.locator('[data-confirm]').click();
      await page.locator('.asst-card[data-action="act2"][data-status="done"]').waitFor();
      assert.match(await page.locator('.asst-card[data-action="act2"]').innerText(), /1 of 3 did not go through/, tag + ': what failed is said');
      assert.deepEqual(state.decided, [['act1', 'confirm'], ['act2', 'confirm']], tag + ': one click for the group');

      // A computer's one-time code shows only on the card, after the person's own tap.
      state.messages.push(msg('m11', 'bot:botops', 'Needs your OK: Make a one-time code to add a computer', {action: 'act3'}));
      await page.goto('https://tico-ui.test/#/updates');
      await page.goto('https://tico-ui.test/#/assistant');
      const enrol = page.locator('.asst-card[data-action="act3"]');
      await enrol.waitFor();
      assert.ok(!(await enrol.innerText()).includes('ONE-TIME-CODE-1234'), tag + ': no code before the tap');
      await enrol.locator('[data-confirm]').click();
      await page.locator('.asst-card[data-action="act3"] [data-shown-once]').waitFor();
      assert.match(await page.locator('.asst-card[data-action="act3"] [data-shown-once] code').innerText(), /ONE-TIME-CODE-1234/, tag + ': the code is on the card');

      // It fits: no sideways scroll, the box and Send are on screen.
      const at = await box.boundingBox(), send = await page.locator('#chat-composer .p-send').boundingBox();
      assert.ok(at && at.x >= 0 && at.x + at.width <= viewport.width + 1 && at.y + at.height <= viewport.height, tag + ': the box is on screen');
      assert.ok(send && send.x + send.width <= viewport.width + 1, tag + ': Send fits');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), tag + ': no horizontal scroll');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }

    {
      // A long chat: scrolled up, a reply that arrives moves nothing and brings the "New reply" pill; your own message
      // takes you to the bottom, which then follows the answer.
      const state = fixture();
      for (let i = 0; i < 24; i++) state.messages.push(msg('h' + i, i % 2 ? 'bot:coo' : 'human:ana', `Line ${i}. ` + 'Some words to fill the line. '.repeat(10)));
      const {page, errors} = await open(browser, {width: 1280, height: 900}, state);
      await page.goto('https://tico-ui.test/#/assistant');
      await page.waitForFunction(() => V2C?.rendered && document.querySelectorAll('#conv-thread .bubble').length === 24);
      const thread = page.locator('#conv-thread'), pill = page.locator('#conv-jump');
      const frames = () => page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
      const gap = async () => { await frames(); return thread.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight); };
      assert.ok(await gap() <= 1, 'stick: opens at the newest line');
      const rect = await thread.boundingBox();
      await page.mouse.move(rect.x + rect.width / 2, rect.y + rect.height / 2);
      await page.mouse.wheel(0, -1500);
      await page.waitForFunction(() => !V2C.stick.atBottom);
      await frames();
      const top = await thread.evaluate(el => el.scrollTop);
      await page.evaluate(m => { V2C.messages.push(m); v2ChatRender(V2C); }, msg('late', 'bot:coo', 'One more thing.'));
      await frames();
      assert.equal(await thread.evaluate(el => el.scrollTop), top, 'stick: a reply does not move a reader who scrolled up');
      await pill.waitFor();
      assert.match(await pill.innerText(), /New reply/, 'stick: the pill says a reply came');
      await page.locator('#chat-composer .p-text').fill('what changed?');
      await page.locator('#chat-composer .p-send').click();
      await page.waitForFunction(() => document.querySelectorAll('#conv-thread .bubble').length === 27);
      assert.ok(await gap() <= 1, 'stick: your own message takes you to the bottom');
      assert.equal(await pill.isHidden(), true, 'stick: and the pill goes');
      assert.deepEqual(errors, [], 'stick: no page errors');
      await page.close();
    }

    // Off: the page says so, and only the owner is offered the switch.
    const state = fixture();
    state.enrol = {id: 'act3', summary: 'Make a one-time code to add a computer', method: 'POST', path: '/api/v2/enrollments', status: 'pending',
    result: null, description: '', diff: [{field: 'operator', new: 'ana'}], body: {operator: 'ana'}, proposed_via: 'botops', proposer: 'botops'};
  state.view = () => ({available: false, state: 'archived', bot: 'coo', name: 'Tico', can_turn_on: true, room_id: null,
      messages: [], has_more: false, next_before: null, execution: null, actions: {}, pending: []});
    const {page} = await open(browser, {width: 1280, height: 900}, state);
    await page.goto('https://tico-ui.test/#/assistant');
    await page.locator('[data-turn-on]').waitFor();
    assert.match(await page.locator('.asst-off').innerText(), /is off/);
    assert.equal(await page.locator('#chat-composer .p-text').count(), 0, 'no chat while it is off');
    // The owner is offered the same switch in Settings > General, and one click turns it on.
    await page.goto('https://tico-ui.test/#/settings');
    await page.locator('[data-settings-tab="general"]').click();
    const strip = page.locator('[data-assistant-off]');
    await strip.waitFor();
    state.enrol = {id: 'act3', summary: 'Make a one-time code to add a computer', method: 'POST', path: '/api/v2/enrollments', status: 'pending',
    result: null, description: '', diff: [{field: 'operator', new: 'ana'}], body: {operator: 'ana'}, proposed_via: 'botops', proposer: 'botops'};
  state.view = () => ({available: true, state: 'active', bot: 'coo', name: 'Tico', can_turn_on: false, room_id: 'room1',
      messages: [], has_more: false, next_before: null, execution: null, actions: {}, pending: []});
    await strip.locator('[data-turn-on]').click();
    await page.locator('[data-assistant-off]').waitFor({state: 'detached'});
    assert.equal(state.turnedOn, 1, 'Turn on Assistant asked the server once');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
