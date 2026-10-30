// Ask (ui/app/ask-panel.js): the person's chat bot on every page. On a computer the "Ask Tico" pill at the bottom left
// (or ⌘J) opens a panel that names the page it sees and sends it with the message, and the pill scales away; on a
// phone the dot in the middle of the tab bar opens a sheet and closes it again. Its dot flies into the panel to the
// newest Tico turn, on to a newer one, and back. "Loop in Tico Support" (one switch under the thread) has an open
// request's messages in the chat as blue-gray bubbles under a headset, a chip in the header, the box writing to it,
// and a line saying support came in; the chip's × or the switch takes it out, with a line saying so. The + still opens
// Support or the Tico team. A Confirm card BotOps left in its own chat and a bot's question come at the end as cards,
// answered here; on the phone a peek beside the dot points at a question and opens the chat on it. No BotOps or
// Assistant rows in the rail. Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const now = new Date().toISOString(), ago = min => new Date(Date.now() - min * 60000).toISOString();
const msg = (id, from, body) => ({id, from_actor: from, body, refs: {}, created: now, kind: 'say'});

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
    if (p === '/api/me') return json({id: 'ben', role: 'human', name: 'Ben', email: 'ben@example.test', cloud: true});
    if (p === '/api/humans') return json({people: [{id: 'ben', name: 'Ben'}]});
    if (p === '/api/employees') return json([{name: 'coo', display_name: 'Assistant'}, {name: 'botops', display_name: 'BotOps'}]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/conversations/room1/snapshot') return json({messages: state.messages, execution: null, has_more: false, next_before: null});
    if (p === '/api/v2/assistant' && method === 'GET') return json({available: true, state: 'active', bot: 'coo', name: 'Assistant',
      can_turn_on: false, room_id: 'room1', messages: state.messages, has_more: false, next_before: null, execution: null, actions: {},
      pending: state.card?.status === 'pending' ? [state.card] : []});
    if (p === '/api/v2/assistant/actions/act9' && method === 'GET') return json({action: state.card});
    if (p === '/api/v2/assistant/actions/act9/confirm') { state.decided.push('act9'); state.card.status = 'done'; return json({action: state.card}); }
    if (p === '/api/v2/needs-you') return json({actor: 'human:ben', count: state.needs.length, version: 'v' + state.needs.length, items: state.needs});
    if (p === '/api/v2/messages/m9/answer') { state.answers.push(JSON.parse(route.request().postData()).text); state.needs = []; return json({ok: true}); }
    if (p === '/api/v2/assistant/messages') {
      const body = JSON.parse(route.request().postData());
      state.sent.push(body);
      const user = msg('u' + state.sent.length, 'human:ben', body.text);
      const bot = msg('b' + state.sent.length, 'bot:coo', "I can't fix that one. [Loop in Tico support](#/support)");
      state.messages.push(user, bot);
      return json({message: user, reply: bot, fast: true});
    }
    const ticket = p.match(/^\/api\/v2\/support\/tickets\/([^/]+)\/(messages|read)$/), listing = () => json({enabled: true, tickets: state.tickets, unread: state.tickets.filter(t => t.unread).length});
    if (ticket) {
      const t = state.tickets.find(x => x.id === ticket[1]);
      if (ticket[2] === 'read') { state.read.push(t.id); t.unread = false; }
      else t.messages.push({body: JSON.parse(route.request().postData()).message, from: 'you', created: new Date().toISOString()});
      return json(t);
    }
    if (p === '/api/v2/support/tickets' || p === '/api/v2/support/tickets/refresh') return listing();
    if (p === '/api/v2/support/compose') return json({email: 'ben@example.test', diagnostics: {}});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/tasks');
  await page.waitForFunction(() => !document.querySelector('#account .account-email')?.textContent.includes('Signing in'));
  return {page, errors};
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    // A computer.
    let state = {messages: [msg('a1', 'human:ben', 'Where is the launch plan?'), msg('a2', 'bot:coo', 'In Docs.')], sent: [], read: [], decided: [], answers: [],
      tickets: [{id: 'sup_1', status: 'answered', unread: true, title: 'Board will not load', message: 'Board will not load', created: ago(30),
                 messages: [{body: 'Try a hard reload.', from: 'staff', created: ago(20)}]}],
      card: {id: 'act9', summary: 'Add Cara as an admin', method: 'POST', path: '/api/v2/people', status: 'pending', result: null,
             description: 'Add Cara as an admin', diff: [], body: {}, proposed_via: 'botops'},
      needs: [{kind: 'ask', id: 'm9', created: now, ask: {id: 'm9', from_actor: 'bot:botops', to_actor: 'human:ben', body: 'Which inbox should the bot answer?', created: now, refs: {}}},
              {kind: 'question', id: 't4', created: now, ask: {id: 'q4', from_actor: 'bot:coo', to_actor: 'human:ben', body: 'Two launch questions', created: now,
               refs: {questions: [{question: 'Launch day?', options: [{label: 'Mon'}, {label: 'Tue'}]}, {question: 'Price?', options: [{label: '$9'}, {label: '$19'}]}]}}}]};
    let {page, errors} = await open(browser, {width: 1280, height: 900}, state);
    assert.equal(await page.locator('#nav-assistant, #nav-botops, #help-open, #mobile-support').count(), 0, 'no BotOps, Assistant or ? in the rail');
    const fab = page.locator('#ask-fab'), panel = page.locator('#ask-panel');
    await fab.waitFor();
    // The unread reply is counted inside the panel only: on Tico support in the + menu, not on the button.
    await page.waitForFunction(() => document.querySelector('#ask-support')?.getAttribute('data-support-count') === '1');
    assert.equal(await fab.evaluate(el => getComputedStyle(el, '::after').content), 'none', 'no count on the button');
    await fab.click();
    await panel.waitFor();
    assert.equal(await fab.getAttribute('aria-expanded'), 'true');
    await page.waitForFunction(() => document.querySelector('#ask-fab').classList.contains('ask-gone'));
    assert.equal(await fab.isVisible(), false, 'the pill scaled away');
    // The pill's dot flies into the panel, to the newest Tico turn, and lands there: the one live dot while it is open
    // (the pill's and the panel's own hold still).
    const dots = () => page.evaluate(() => ['#ask-fab [data-ask-dot]', '#ask-panel [data-ask-dot]', '#ask-panel .ask-turn-dot']
      .map(s => document.querySelector(s)?.dataset.state || ''));
    const landed = () => page.waitForFunction(() => !document.querySelector('.ldot-fly, .ldot-away'));
    await page.waitForFunction(() => document.querySelector('.ldot-fly'));
    await landed();
    assert.equal(await panel.locator('.conv-run[data-message="a2"] .ask-turn-dot').count(), 1);
    assert.deepEqual(await dots(), ['still', 'still', 'idle']);
    const box = panel.locator('.ask-form .p-text');
    await box.waitFor();
    await page.waitForFunction(() => document.activeElement?.matches('#ask-panel .p-text'));
    assert.equal(await panel.locator('[data-ask-sees-name]').innerText(), await page.locator('#main h1, #main h2').first().innerText(), 'sees the page');
    // Tico's turns have no bubble; the newest has the dot beside it.
    assert.equal(await panel.locator('.conv-run[data-message="a2"] .bubble').evaluate(el => getComputedStyle(el).backgroundColor), 'rgba(0, 0, 0, 0)');
    // An open request is in this chat: the switch is on, its chip is in the header, a line says support came in, and
    // its messages flow in (support's blue-gray under a headset, Ben's on the right) and are read. No members bar.
    const thread = panel.locator('[data-ask-thread]'), sw = panel.getByRole('switch', {name: 'Loop in Tico Support'});
    const chip = panel.locator('[data-ask-people] .ask-sup-chip');
    await chip.waitFor();
    assert.equal(await sw.getAttribute('aria-checked'), 'true');
    assert.match(await chip.innerText(), /Tico Support/);
    assert.equal(await panel.locator('[data-ask-members], [data-ask-ticket]').count(), 0, 'no members bar');
    const them = panel.locator('.ask-sup.them', {hasText: 'Try a hard reload.'});
    await them.waitFor();
    assert.equal(await them.locator('.ask-sav').innerText(), 'headset_mic');
    assert.equal(await them.locator('small').innerText(), 'Tico Support');
    assert.equal(await panel.locator('.ask-sup.you .ask-sup-body').first().innerText(), 'Board will not load');
    assert.deepEqual(await panel.locator('.ask-sys').allInnerTexts(), ['You added Tico Support. They can now see chats.']);
    await page.waitForFunction(() => document.querySelector('#ask-support')?.getAttribute('data-support-count') === null);
    assert.deepEqual(state.read, ['sup_1'], 'a reply seen here is read');
    // What waits on Ben from elsewhere: BotOps' Confirm card, confirmed here, and a bot's question, answered here.
    const card = thread.locator('[data-action-host="act9"]');
    await card.getByRole('button', {name: 'Confirm'}).click();
    await page.waitForFunction(() => !document.querySelector('#ask-panel [data-action-host="act9"] [data-confirm]'));
    assert.deepEqual(state.decided, ['act9']);
    // Several structured questions are answered on the task, not with a line of words.
    const several = thread.locator('.ask-card[data-ask-q="t4"]');
    assert.equal(await several.locator('a[href="#/task/t4"]').count(), 1);
    assert.equal(await several.locator('form').count(), 0);
    const question = thread.locator('.ask-card', {hasText: 'Which inbox should the bot answer?'});
    assert.match(await question.innerText(), /BotOps asks/);
    await question.getByRole('textbox', {name: 'Answer BotOps'}).fill('support@acme.test');
    await question.getByRole('button', {name: 'Send'}).click();
    await question.waitFor({state: 'detached'});
    assert.deepEqual(state.answers, ['support@acme.test']);
    // While support is in, the box writes to it, not to the Assistant.
    assert.equal(await box.getAttribute('placeholder'), 'Message Tico Support');
    await box.fill('Still blank after a reload');
    await box.press('Enter');
    await panel.locator('.ask-sup.you', {hasText: 'Still blank after a reload'}).waitFor();
    assert.equal(state.sent.length, 0, 'not sent to the Assistant');
    assert.equal(await thread.locator('.bubble', {hasText: 'In Docs.'}).count(), 1, 'the whole chat stays');
    // The chip's × takes support out: the switch goes off, a line says so, what support said stays for this visit,
    // and the request stays open in Support (taken out of this chat only).
    await chip.getByRole('button', {name: 'Remove Tico Support'}).click();
    assert.equal(await sw.getAttribute('aria-checked'), 'false');
    assert.equal(await chip.count(), 0);
    assert.deepEqual(await panel.locator('.ask-sys').allInnerTexts(),
      ['You added Tico Support. They can now see chats.', 'Tico Support can no longer see chats.']);
    assert.equal(await them.count(), 1, 'what support said stays');
    assert.equal(await box.getAttribute('placeholder'), 'Ask anything…');
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('tico.ask.hidden.ben'))), ['sup_1']);
    await page.waitForFunction(() => !document.querySelector('.ask-fly'));
    // The switch brings it back, and takes it out again.
    await sw.click();
    await chip.waitFor();
    assert.equal(await sw.getAttribute('aria-checked'), 'true');
    assert.equal(await panel.locator('.ask-sys').last().innerText(), 'You added Tico Support. They can now see chats.');
    await page.waitForFunction(() => !document.querySelector('.ask-fly'));
    assert.equal(await chip.isVisible(), true, 'the chip landed');
    await sw.click();
    assert.equal(await sw.getAttribute('aria-checked'), 'false');
    assert.equal(await panel.locator('.ask-sys').last().innerText(), 'Tico Support can no longer see chats.');
    // A message goes with the page it was asked from; a link to Support in the reply opens Support over the page.
    await box.fill('The board will not load');
    await box.press('Enter');
    await panel.getByRole('link', {name: 'Loop in Tico support'}).waitFor();
    await landed();
    assert.equal(await thread.locator('.conv-run[data-message]').last().locator('.ask-turn-dot').count(), 1, 'the newest Tico turn took the dot');
    assert.equal(await panel.locator('.ask-turn-dot').count(), 1);
    assert.equal(state.sent[0].text, 'The board will not load');
    assert.equal(state.sent[0].page.path, '#/tasks');
    await panel.getByRole('link', {name: 'Loop in Tico support'}).click();
    await page.locator('#support-pop[open]').waitFor();
    assert.equal(await page.evaluate(() => location.hash), '#/tasks', 'the page behind stays');
    await page.keyboard.press('Escape');
    await page.locator('#support-pop[open]').waitFor({state: 'detached'}).catch(() => {});
    await page.waitForFunction(() => !document.querySelector('#support-pop')?.open);
    // The + offers Tico support and the Tico team; Esc closes the menu, then the panel; ⌘J opens it again.
    await page.locator('#ask-add').click();
    const menu = panel.getByRole('menu');
    assert.deepEqual((await menu.getByRole('menuitem').allInnerTexts()).map(t => t.replace(/\s+/g, ' ')), ['Tico support Free', 'Tico team Full management']);
    await page.keyboard.press('Escape');
    assert.equal(await menu.isHidden(), true);
    await box.focus();
    await page.keyboard.press('Escape');
    await panel.waitFor({state: 'hidden'});
    await landed();
    assert.deepEqual(await dots(), ['idle', 'still', 'still'], 'closed: the dot is back on the pill');
    await page.keyboard.press('Control+j');
    await panel.waitFor();
    // Show chat opens the Assistant page, where the button is not needed.
    await panel.getByRole('link', {name: 'Show chat'}).click();
    await page.waitForFunction(() => location.hash === '#/assistant');
    await fab.waitFor({state: 'hidden'});
    assert.deepEqual(errors, []);
    await page.close();

    // A phone: the dot in the middle of the tab bar opens a sheet over the page, above the tab bar, and closes it.
    const ask = (id, who, body) => ({kind: 'ask', id, created: now, ask: {id, from_actor: who, to_actor: 'human:ben', body, created: now, refs: {}}});
    state = {messages: [], sent: [], read: [], decided: [], answers: [], tickets: [], card: null,
      needs: [ask('m8', 'bot:botops', 'Which inbox should the bot answer?'), ask('m7', 'bot:coo', 'Ship the launch post today?')]};
    ({page, errors} = await open(browser, {width: 390, height: 844}, state));
    const tab = page.locator('#mobile-ask');
    // The Ask tab is the pill too, words and all: the dot, or a close mark in its place while the sheet is open.
    const glyph = async () => {
      await page.waitForFunction(() => !document.querySelector('.ldot-fly')
        && !document.querySelector('#mobile-ask').getAnimations({subtree: true}).some(a => a instanceof CSSTransition));
      return tab.evaluate(el => { const on = x => getComputedStyle(x).opacity === '1', x = el.querySelector('.ask-x');
        return on(el.querySelector('.ask-glyph .ldot')) ? 'dot' : on(x) ? x.textContent : ''; });
    };
    assert.equal(await tab.locator('.ask-label').isVisible(), true, 'not icon-only');
    assert.deepEqual([await tab.getAttribute('aria-label'), await glyph()], [null, 'dot']);
    // The dot sits in the middle of the bar, live, and there is no second, floating Ask button.
    const centre = await page.evaluate(() => { const t = document.querySelector('#mobile-ask').getBoundingClientRect(); return t.left + t.width / 2; });
    assert.ok(Math.abs(centre - 195) < 24, `the dot is in the middle of the tab bar (${centre})`);
    assert.equal(await page.locator('#ask-fab').isVisible(), false, 'no floating Ask button on a phone');
    assert.equal(await tab.locator('[data-ask-dot]').getAttribute('data-state'), 'idle');
    // A peek: after a few seconds, one line above the bar about the first question waiting on Ben; × dismisses it.
    const peek = page.locator('#ask-peek');
    await peek.waitFor({timeout: 8000});
    assert.equal(await peek.locator('[data-peek-text]').innerText(), 'BotOps asks: Which inbox should the bot answer?');
    await peek.getByRole('button', {name: 'Dismiss'}).click();
    assert.equal(await peek.isHidden(), true);
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('tico.ask.peek.ben')).dismissed), ['m8']);
    await tab.click();
    await page.locator('#ask-panel').waitFor();
    await page.locator('#ask-panel').evaluate(el => Promise.all(el.getAnimations().map(a => a.finished)));
    const sheet = await page.locator('#ask-panel').boundingBox(), bar = await page.locator('#mobile-nav').boundingBox();
    assert.ok(sheet.x === 0 && sheet.width === 390 && Math.abs(sheet.y + sheet.height - bar.y) < 2, `a sheet above the tab bar (${JSON.stringify([sheet, bar])})`);
    assert.deepEqual([await tab.getAttribute('aria-label'), await glyph()], ['Close Ask Tico', 'close']);
    assert.equal(await page.locator('#ask-panel').evaluate(el => [...el.querySelectorAll('*')].every(x => x.getBoundingClientRect().right <= innerWidth + 1)), true, 'no horizontal scroll');
    assert.equal(await peek.isHidden(), true, 'no peek while the chat is open');
    await tab.click();
    await page.locator('#ask-panel').waitFor({state: 'hidden'});
    assert.deepEqual([await tab.getAttribute('aria-label'), await glyph()], [null, 'dot']);
    // The next question peeks once the chat closes; tapping it opens the chat on that question.
    await peek.waitFor();
    assert.equal(await peek.locator('[data-peek-text]').innerText(), 'Assistant asks: Ship the launch post today?');
    await peek.locator('[data-peek-go]').click();
    await page.locator('#ask-panel').waitFor();
    await page.locator('#ask-panel .ask-card[data-ask-q="m7"]').waitFor();
    assert.equal(await page.locator('#ask-panel .ask-card[data-ask-q="m7"]').isVisible(), true);
    assert.equal(await peek.isHidden(), true);
    await tab.click();
    await page.locator('#ask-panel').waitFor({state: 'hidden'});
    assert.equal(await peek.isHidden(), true, 'each question peeks once');
    assert.deepEqual(errors, []);
    await page.close();
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
