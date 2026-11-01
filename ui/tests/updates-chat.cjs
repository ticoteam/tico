// The Updates chat (ui/app/updates-chat.js; spec: Strategy Bot v3, section 10.8, tests 25 to 27): the person's own chat
// bot
// in a right rail beside the feed, the same conversation as their menu tab; a question goes with `place: "updates"`;
// a link in a reply scrolls to and highlights that update, switching the day or Daily/Weekly as needed, while the chat
// stays; the header button hides it and that is remembered; its edge resizes it. On a phone the button opens the chat full screen; a link closes it first. Every request is intercepted (fixtures only).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const shots = process.env.TICO_SCREENSHOT_DIR;

const now = Date.now(), iso = ms => new Date(now + ms).toISOString(), hour = 3600e3;
const today = new Date(now).toLocaleDateString('en-CA', {timeZone: 'America/Los_Angeles'});
const yesterday = new Date(new Date(today + 'T12:00:00Z').getTime() - 86400e3).toISOString().slice(0, 10);
const bots = [['coo', 'Assistant'], ['goal-manager', 'Chief'], ['sales', 'Sales Bot'], ['ops', 'Ops Bot']].map(([name, display_name]) =>
  ({name, display_name, host: 'keeper', status: 'active', can_chat: true, operator: 'cara'}));
const update = (id, bot, body, day, kind = 'daily') => ({id, bot, kind, day, headline: body.slice(2), body, read: true,
  created_at: iso(-hour), updated_at: iso(-hour), replies: 0, drafted_by: null, edited_by: null, slides: null, inputs: []});
const UPDATES = {
  'u-sales': update('u-sales', 'sales', '- Closed the Acme renewal', today),
  'u-ops': update('u-ops', 'ops', '- Moved the warehouse', yesterday),
  'u-week': update('u-week', 'sales', '- A strong week for renewals', yesterday, 'weekly'),
};
const link = u => `#/updates?kind=${u.kind}&day=${u.day}&update=${u.id}`;
const node = (key, author, u) => ({key, author, name: author, direct: true, update: u, children: []});

async function open(browser, me, viewport, chat, store = {}) {
  const phone = viewport.width < 600;
  const context = await browser.newContext({viewport, serviceWorkers: 'block', ...(phone ? {isMobile: true, hasTouch: true} : {})});
  const page = await context.newPage();
  const errors = [], sent = [], prefs = [], trees = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/vendor/fonts/material-symbols-outlined.woff2') return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile('vendor/fonts/material-symbols-outlined.woff2'))});
    if (p === '/vendor/marked.min.js') return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p === '/api/me') return json(me);
    if (p === '/api/employees') return json(bots);
    if (p === '/api/humans') return json({people: [{id: 'cara', name: 'Cara'}, {id: 'ana', name: 'Ana'}]});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/updates/unread') return json({unread: 0});
    if (p === '/api/v2/updates/tree') {
      const kind = url.searchParams.get('kind'), day = url.searchParams.get('day') || today;
      trees.push(url.search);
      const mine = Object.values(UPDATES).filter(u => u.kind === kind && u.day === day);
      return json({kind, day, scope: 'below', me: 'p:' + me.id, unread: 0,
                   roots: [{...node('p:' + me.id, 'human:' + me.id, null), children: mine.map(u => node('b:' + u.bot, u.bot, u))}]});
    }
    const one = p.match(/^\/api\/v2\/updates\/([^/]+)$/);
    if (one && UPDATES[one[1]]) return json({update: {...UPDATES[one[1]], archived: false}, thread: []});
    if (p === '/api/v2/assistant') return json(chat);
    if (p === `/api/v2/conversations/${chat.room_id}/snapshot`) return json({messages: chat.messages, execution: null, has_more: false, next_before: null});
    if (p === '/api/v2/assistant/messages') {
      const body = req.postDataJSON(); sent.push(body);
      const message = {id: 'm' + (chat.messages.length + 1), conversation_id: chat.room_id, from_actor: 'human:' + me.id, to_actor: 'bot:' + chat.bot,
                       body: body.text, created_at: iso(0), refs: {assistant: true, updates: true}};
      chat.messages.push(message, {id: 'r' + chat.messages.length, conversation_id: chat.room_id, from_actor: 'bot:' + chat.bot, to_actor: 'human:' + me.id,
        created_at: iso(1000), refs: {assistant: true},
        body: `Top today: [Sales closed the Acme renewal](${link(UPDATES['u-sales'])}), since it moves the revenue goal. ` +
              `Yesterday: [Ops](${link(UPDATES['u-ops'])}); this week: [the week in review](${link(UPDATES['u-week'])}).`});
      return json({message, reply: null, fast: false, intent: null});
    }
    if (p === '/api/v2/preferences/ui.updates_chat') {
      if (req.method() === 'POST') { prefs.push(req.postDataJSON().value); store.pref = req.postDataJSON().value; return json({key: 'ui.updates_chat', value: store.pref}); }
      return json({key: 'ui.updates_chat', value: store.pref ?? null});
    }
    if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  return {context, page, errors, sent, prefs, trees};
}

const chatOf = over => ({available: true, state: 'active', can_turn_on: false, has_more: false, next_before: null, execution: null,
  actions: {}, pending: [], earlier: null, ...over});

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    // Desktop, a member: their Assistant beside the feed.
    {
      const cara = {id: 'cara', name: 'Cara', email: 'cara@example.test', role: 'member', cloud: true};
      const chat = chatOf({bot: 'coo', name: 'Assistant', room_id: 'room-cara',
        messages: [{id: 'm0', conversation_id: 'room-cara', from_actor: 'human:cara', to_actor: 'bot:coo', body: 'Asked from the menu tab', created_at: iso(-hour), refs: {assistant: true}}]});
      const store = {};
      const {context, page, errors, sent, prefs, trees} = await open(browser, cara, {width: 1400, height: 900}, chat, store);
      await page.goto('http://tico-ui.test/#/updates');
      const rail = page.locator('#upd-chat');
      await rail.locator('.p-text').waitFor();
      await page.locator('#upd-feed [data-upd="u-sales"]').waitFor();
      assert.equal(await rail.locator('.updc-head h2').innerText(), 'Assistant', 'labelled with their bot');
      assert.equal(await page.locator('#upd-chat-btn').getAttribute('aria-pressed'), 'true', 'shown by default');
      assert.match(await rail.locator('[data-updc-thread]').innerText(), /Asked from the menu tab/, 'the same conversation as the menu tab');
      assert.ok(await page.locator('#main').evaluate(el => el.classList.contains('upd-chat-layout')), 'a right rail');
      const feedBox = await page.locator('#upd-feed').boundingBox(), railBox = await rail.boundingBox();
      assert.ok(railBox.x >= feedBox.x + feedBox.width, 'the chat is to the right of the feed');
      assert.ok(Math.abs(railBox.width - 360) <= 2, 'at its default width: ' + railBox.width);
      await rail.evaluate(el => { el.dataset.kept = '1'; });
      // Every icon is in the font (a missing name overflows as letters).
      await page.evaluate(() => document.fonts.ready);
      assert.deepEqual(await page.$$eval('.upd-head .nav-icon, #upd-chat .nav-icon', els => els.filter(el => el.scrollWidth > el.clientWidth + 1).map(el => el.textContent)), []);

      // Test 25: ask the top update; the reply's link scrolls to and highlights it, on this page.
      await rail.locator('.p-text').fill("What's the top update today?");
      await rail.locator('.p-send').click();
      await rail.locator('a[href*="update=u-sales"]').waitFor();
      assert.deepEqual(sent, [{text: "What's the top update today?", place: 'updates'}], 'sent from Updates');
      const treesBefore = trees.length;
      await rail.locator('a[href*="update=u-sales"]').click();
      const sales = page.locator('#upd-feed [data-upd="u-sales"]');
      await page.waitForFunction(() => document.querySelector('#upd-feed [data-upd="u-sales"]')?.classList.contains('upd-flash'));
      assert.ok(await sales.evaluate(el => el.classList.contains('sel')), 'selected');
      assert.equal(trees.length, treesBefore, 'a card on the page is not loaded again');
      if (shots) await page.screenshot({path: `${shots}/updates-chat-desktop.png`});
      assert.equal(new URL(page.url()).hash, '#/updates', 'the address keeps no link part');

      // Another day's update: the tree switches to that day and the chat stays.
      await rail.locator('a[href*="update=u-ops"]').click();
      await page.waitForFunction(() => document.querySelector('#upd-feed [data-upd="u-ops"]')?.classList.contains('upd-flash'));
      assert.ok(trees.at(-1).includes('day=' + yesterday), 'that day\'s tree: ' + trees.at(-1));
      assert.ok(await page.locator('#upd-day').isVisible(), 'the day shows, with a way back');
      assert.equal(await rail.getAttribute('data-kept'), '1', 'the same chat, not drawn again');
      assert.doesNotMatch(new URL(page.url()).hash, /update=/, 'the link part goes once followed');
      // A week in review: Weekly.
      await rail.locator('a[href*="update=u-week"]').click();
      await page.waitForFunction(() => document.querySelector('#upd-feed [data-upd="u-week"]')?.classList.contains('upd-flash'));
      assert.equal(await page.locator('[data-upd-kind="weekly"]').getAttribute('aria-selected'), 'true', 'Weekly');
      assert.equal(await rail.getAttribute('data-kept'), '1', 'still the same chat');

      // Resized like the other right rails: the edge's arrow keys, kept with the other widths.
      const edge = page.locator('.rail-edge[data-rail="chat"]');
      assert.ok(await edge.isVisible(), 'an edge to drag');
      await edge.focus();
      await page.keyboard.press('ArrowLeft');
      assert.equal(Math.round((await rail.boundingBox()).width), 376, 'one step wider');
      assert.equal(JSON.parse(await page.evaluate(() => localStorage.getItem('tico.rails'))).chat, 376, 'kept');

      // Test 26: hide it and reload: still hidden; shown again, the same conversation.
      await page.locator('#upd-chat-btn').click();
      assert.ok(await rail.isHidden(), 'hidden');
      assert.equal(prefs.at(-1).shown, false, 'remembered with the account');
      await page.reload();
      await page.locator('#upd-feed [data-upd="u-week"]').waitFor();          // Weekly still, from the address
      assert.ok(await page.locator('#upd-chat').isHidden(), 'still hidden after a reload');
      assert.equal(await page.locator('#upd-chat-btn').getAttribute('aria-pressed'), 'false');
      assert.ok(!(await page.locator('#main').evaluate(el => el.classList.contains('upd-chat-layout'))), 'the feed has the page');
      await page.evaluate(() => localStorage.clear());
      await page.reload();
      await page.locator('#upd-feed [data-upd="u-week"]').waitFor();          // Weekly still, from the address
      await page.waitForFunction(() => document.querySelector('#upd-chat-btn')?.getAttribute('aria-pressed') === 'false');
      assert.ok(await page.locator('#upd-chat').isHidden(), 'hidden on a new device too (the account\'s copy)');
      await page.locator('#upd-chat-btn').click();
      await page.locator('#upd-chat [data-updc-thread] a[href*="update=u-sales"]').waitFor();
      assert.match(await page.locator('#upd-chat [data-updc-thread]').innerText(), /Asked from the menu tab[\s\S]*What's the top update today\?/, 'the same conversation');
      assert.equal(prefs.at(-1).shown, true);
      assert.deepEqual(errors, []);
      await context.close();
    }
    // Test 27, a phone: the owner's chat is their Assistant like everyone's, not a menu row; the Updates button opens it
    // full screen; a link closes it.
    {
      const ana = {id: 'ana', name: 'Ana', email: 'ana@example.test', role: 'owner', cloud: true};
      const chat = chatOf({bot: 'coo', name: 'Assistant', room_id: 'room-ana', messages: []});
      const {context, page, errors, sent} = await open(browser, ana, {width: 390, height: 844}, chat);
      await page.goto('http://tico-ui.test/#/updates');
      await page.locator('#upd-feed [data-upd="u-sales"]').waitFor();
      assert.ok(await page.locator('#upd-chat').isHidden(), 'no rail on a phone');
      await page.locator('#mobile-more').click();
      assert.ok(await page.locator('#nav-assistant').isHidden(), 'the Assistant is not in the menu');
      await page.keyboard.press('Escape');
      await page.evaluate(() => document.body.classList.remove('drawer'));
      await page.locator('#upd-chat-btn').click();
      const rail = page.locator('#upd-chat');
      await rail.locator('.p-text').waitFor({state: 'visible'});
      const box = await rail.boundingBox();
      assert.deepEqual([box.x, box.y, box.width, box.height], [0, 0, 390, 844], 'full screen');
      assert.equal(await rail.locator('.updc-head h2').innerText(), 'Assistant');
      assert.ok(await page.locator('#mobile-nav').isHidden(), 'over the phone\'s bar');
      if (shots) await page.screenshot({path: `${shots}/updates-chat-phone.png`});
      await rail.locator('[data-updc-hint]').click();               // an empty chat offers the question
      await rail.locator('a[href*="update=u-ops"]').waitFor();
      assert.deepEqual(sent, [{text: "What's the top update today?", place: 'updates'}]);
      await rail.locator('a[href*="update=u-ops"]').click();
      await page.waitForFunction(() => document.querySelector('#upd-feed [data-upd="u-ops"]')?.classList.contains('upd-flash'));
      assert.ok(await rail.isHidden(), 'the chat closed first');
      // Back closes it too.
      await page.locator('#upd-chat-btn').click();
      await rail.locator('.p-text').waitFor({state: 'visible'});
      await page.goBack();
      await page.waitForFunction(() => !document.body.classList.contains('upd-chat-full'));
      assert.match(new URL(page.url()).hash, /^#\/updates/, 'Back closed the chat and stayed on Updates');
      assert.deepEqual(errors, []);
      await context.close();
    }
    // The Assistant off: one line with Turn on for whoever may turn it on; for anyone else no rail and no button.
    for (const canTurnOn of [true, false]) {
      const dana = {id: 'dana', name: 'Dana', email: 'dana@example.test', role: canTurnOn ? 'owner' : 'member', cloud: true};
      const chat = chatOf({available: false, state: 'archived', bot: 'coo', name: 'Assistant', room_id: null, messages: [], can_turn_on: canTurnOn});
      const {context, page, errors} = await open(browser, dana, {width: 1400, height: 900}, chat);
      await page.goto('http://tico-ui.test/#/updates');
      await page.locator('#upd-feed [data-upd="u-sales"]').waitFor();
      const rail = page.locator('#upd-chat');
      if (canTurnOn) {
        await rail.locator('[data-turn-on]').waitFor();
        assert.equal(await rail.locator('.asst-off > span').first().innerText(), 'The Assistant is off.', 'one line');
        assert.equal(await rail.locator('[data-turn-on]').innerText(), 'Turn on');
        assert.equal(await rail.locator('.asst-off p').count(), 0, 'no paragraph');
      } else {
        await page.waitForFunction(() => document.querySelector('#upd-chat')?.hidden && document.querySelector('#upd-chat-btn')?.hidden);
        assert.ok(!(await page.locator('#main').evaluate(el => el.classList.contains('upd-chat-layout'))), 'the feed has the page');
        assert.equal(await page.getByText('Ask the owner').count(), 0);
      }
      assert.deepEqual(errors, []);
      await context.close();
    }
    console.log('updates-chat: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
