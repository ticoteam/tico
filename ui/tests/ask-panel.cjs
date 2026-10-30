// Ask (ui/app/ask-panel.js): the person's chat bot on every page. On a computer the button at the bottom left (or ⌘J)
// opens a panel that names the page it sees and sends it with the message; on a phone the Ask tab opens a sheet and
// closes it again. The + loops in Tico support or the Tico team (Support, ui/support.js); an open request's messages
// flow into the chat as green bubbles, its name shows only them (the box then writes to support), and its × takes it
// out. A Confirm card BotOps left in its own chat and a bot's question come at the end as cards, answered here. No
// BotOps or Assistant rows in the rail. Fixtures only.
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
    const box = panel.locator('.ask-form .p-text');
    await box.waitFor();
    await page.waitForFunction(() => document.activeElement?.matches('#ask-panel .p-text'));
    assert.equal(await panel.locator('[data-ask-sees-name]').innerText(), await page.locator('#main h1, #main h2').first().innerText(), 'sees the page');
    // An open request is in this chat: support's messages flow in as green bubbles, after the older chat, and are read.
    const members = panel.locator('[data-ask-members]'), thread = panel.locator('[data-ask-thread]');
    await members.waitFor();
    await panel.locator('.ask-sup.them', {hasText: 'Try a hard reload.'}).waitFor();
    assert.equal(await panel.locator('.ask-sup.you .ask-sup-body').first().innerText(), 'Board will not load');
    await page.waitForFunction(() => document.querySelector('#ask-panel [data-ask-ticket]')?.textContent === 'Tico support');
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
    // Its name shows only support, and the box then writes to it; again shows the whole chat.
    const chip = panel.locator('[data-ask-ticket]');
    await chip.click();
    assert.equal(await chip.getAttribute('aria-pressed'), 'true');
    assert.equal(await thread.locator('.bubble').count(), 0, 'only Tico support');
    assert.equal(await box.getAttribute('placeholder'), 'Message Tico support');
    await box.fill('Still blank after a reload');
    await box.press('Enter');
    await panel.locator('.ask-sup.you', {hasText: 'Still blank after a reload'}).waitFor();
    assert.equal(state.sent.length, 0, 'not sent to the Assistant');
    await chip.click();
    assert.equal(await chip.getAttribute('aria-pressed'), 'false');
    await thread.locator('.bubble', {hasText: 'In Docs.'}).waitFor();
    // Its × takes it out of the chat.
    await panel.getByRole('button', {name: 'Remove Tico support from this chat'}).click();
    assert.equal(await members.isHidden(), true);
    assert.equal(await panel.locator('.ask-sup').count(), 0);
    // A message goes with the page it was asked from; a link to Support in the reply opens Support over the page.
    await box.fill('The board will not load');
    await box.press('Enter');
    await panel.getByRole('link', {name: 'Loop in Tico support'}).waitFor();
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
    await page.keyboard.press('Control+j');
    await panel.waitFor();
    // Show chat opens the Assistant page, where the button is not needed.
    await panel.getByRole('link', {name: 'Show chat'}).click();
    await page.waitForFunction(() => location.hash === '#/assistant');
    await fab.waitFor({state: 'hidden'});
    assert.deepEqual(errors, []);
    await page.close();

    // A phone: the Ask tab opens a sheet over the page, above the tab bar, and closes it.
    state = {messages: [], sent: [], read: [], decided: [], answers: [], tickets: [], card: null, needs: []};
    ({page, errors} = await open(browser, {width: 390, height: 844}, state));
    const tab = page.locator('#mobile-ask');
    // The Ask tab is an icon, no word: Ask's mark, a close mark while open, and its name says which.
    const glyph = () => tab.locator('.mobile-nav-icon').innerText();
    assert.equal(await tab.locator('.mobile-nav-label').count(), 0);
    assert.deepEqual([await tab.getAttribute('aria-label'), await glyph()], ['Ask', 'auto_awesome']);
    await tab.click();
    await page.locator('#ask-panel').waitFor();
    await page.locator('#ask-panel').evaluate(el => Promise.all(el.getAnimations().map(a => a.finished)));
    const sheet = await page.locator('#ask-panel').boundingBox(), bar = await page.locator('#mobile-nav').boundingBox();
    assert.ok(sheet.x === 0 && sheet.width === 390 && Math.abs(sheet.y + sheet.height - bar.y) < 2, `a sheet above the tab bar (${JSON.stringify([sheet, bar])})`);
    assert.deepEqual([await tab.getAttribute('aria-label'), await glyph()], ['Close', 'close']);
    assert.equal(await page.locator('#ask-panel').evaluate(el => [...el.querySelectorAll('*')].every(x => x.getBoundingClientRect().right <= innerWidth + 1)), true, 'no horizontal scroll');
    await tab.click();
    await page.locator('#ask-panel').waitFor({state: 'hidden'});
    assert.deepEqual([await tab.getAttribute('aria-label'), await glyph()], ['Ask', 'auto_awesome']);
    assert.deepEqual(errors, []);
    await page.close();
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
