// Ask (ui/app/ask-panel.js): the person's chat bot on every page. On a computer the "Ask Tico" row at the bottom of
// the sidebar, with Search's icon at its end (or ⌘J), opens "Tico Assistant", a panel straight above the row that names the
// page it sees and sends it with the message, and a close mark takes the row's dot place; on a phone the dot in the middle of the tab bar opens a sheet and closes it again. Its dot flies up to
// its place beside the panel's name and lands there exactly, and flies back down on close; Tico's turns carry no dot.
// The header has no + and no Show chat: "Loop in Support", a link at the top right, has an open request's messages
// in the chat as blue-gray bubbles under a headset, a chip in the header, the box writing to it, and a line saying
// support came in (the link hides meanwhile); the chip's × takes it out, with a line saying so. A Confirm card BotOps
// left in its own chat and bots' questions sit in the thread by when they were asked, answered here. A peek beside
// the row says in six words at most that something waits (never who asked) and opens the chat on it. No BotOps or
// Assistant rows in the rail. Fixtures only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const now = new Date().toISOString(), ago = min => new Date(Date.now() - min * 60000).toISOString();
const msg = (id, from, body, created = now) => ({id, from_actor: from, body, refs: {}, created_at: created, kind: 'say'});

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
    if (p === '/api/v2/tasks/t7/answers') { state.picked.push(JSON.parse(route.request().postData()).answers['tone-2']); state.needs = []; return json({ok: true}); }
    if (p === '/api/v2/assistant/messages') {
      const body = JSON.parse(route.request().postData());
      state.sent.push(body);
      const user = msg('u' + state.sent.length, 'human:ben', body.text, new Date().toISOString());
      const bot = msg('b' + state.sent.length, 'bot:coo', "I can't fix that one. [Loop in Tico support](#/support)", new Date().toISOString());
      state.messages.push(user, bot);
      return json({message: user, reply: bot, fast: true});
    }
    if (p === '/api/v2/uploads/assistant/messages') {
      const raw = route.request().postDataBuffer().toString('latin1'), field = name => raw.match(new RegExp(`name="${name}"\\r\\n\\r\\n([^\\r]*)`))?.[1];
      const body = {text: field('text'), page: JSON.parse(field('page') || 'null'), files: [...raw.matchAll(/filename="([^"]+)"/g)].map(m => m[1])};
      state.uploaded = (state.uploaded || []).concat([body]);
      const user = msg('f' + state.uploaded.length, 'human:ben', body.text, new Date().toISOString());
      state.messages.push(user, msg('g' + state.uploaded.length, 'bot:coo', 'Read it.', new Date().toISOString()));   // the turn's reply, by the snapshot
      return json({message: user, reply: null, fast: false});
    }
    const ticket = p.match(/^\/api\/v2\/support\/tickets\/([^/]+)\/(messages|read)$/), listing = () => json({enabled: true, tickets: state.tickets, unread: state.tickets.filter(t => t.unread).length});
    if (ticket) {
      const t = state.tickets.find(x => x.id === ticket[1]);
      if (ticket[2] === 'read') { state.read.push(t.id); t.unread = false; }
      else t.messages.push({body: JSON.parse(route.request().postData()).message, from: 'you', created_at: new Date().toISOString()});
      return json(t);
    }
    if (p === '/api/v2/support/tickets' && method === 'POST') {
      const body = JSON.parse(route.request().postData()), t = {id: 'sup_' + (state.tickets.length + 1), status: 'open', unread: false,
        title: body.message.slice(0, 40), message: body.message, created_at: new Date().toISOString(), messages: []};
      (state.filed ||= []).push(body); state.tickets.unshift(t);
      return route.fulfill({status: 201, contentType: 'application/json', body: JSON.stringify(t)});
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

// The dot's flight after `act`: where the copy lands (its box once its transform is spent), and, once it has, where
// `dot` is. The watch starts before `act`: a quick flight can be over before a wait inside `act` returns.
async function flight(page, dot, act) {
  const seen = page.waitForFunction(() => { const g = document.querySelector('.ldot-fly');
    return g && {left: parseFloat(g.style.left), top: parseFloat(g.style.top), width: parseFloat(g.style.width),
      took: g.getAnimations()[0]?.effect.getTiming().duration}; });
  await act();
  const ghost = await seen.then(h => h.jsonValue());
  await page.waitForFunction(() => !document.querySelector('.ldot-fly, .ldot-away'));
  await page.locator('#ask-panel').evaluate(el => Promise.all(el.getAnimations().map(a => a.finished)));
  const r = await page.locator(dot).evaluate(el => { const b = el.getBoundingClientRect(); return {left: b.left, top: b.top, width: b.width}; });
  // the dot breathes (a scale of 3% at most), so its centre is what must match
  const off = Math.hypot(ghost.left + ghost.width / 2 - r.left - r.width / 2, ghost.top + ghost.width / 2 - r.top - r.width / 2);
  assert.ok(off < 2, `the dot landed ${off.toFixed(1)}px off ${dot}: ${JSON.stringify({ghost, r})}`);
  return ghost;
}
const words = text => text.trim().split(/\s+/).length;

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    // A computer. Support's request came first, then Ben asked, BotOps left a card and asked a question, and the
    // Assistant answered after both; the launch questions came last.
    let state = {messages: [msg('a1', 'human:ben', 'Where is the launch plan?', ago(15)), msg('a2', 'bot:coo', 'In Docs.', ago(10))], sent: [], read: [], decided: [], answers: [],
      tickets: [{id: 'sup_1', status: 'answered', unread: true, title: 'Board will not load', message: 'Board will not load', created_at: ago(30),
                 messages: [{body: 'Try a hard reload.', from: 'staff', created_at: ago(20)}]}],
      card: {id: 'act9', summary: 'Add Cara as an admin', method: 'POST', path: '/api/v2/people', status: 'pending', result: null,
             description: 'Add Cara as an admin', diff: [], body: {}, proposed_via: 'botops', created_at: ago(13)},
      needs: [{kind: 'ask', id: 'm9', created_at: ago(12), ask: {id: 'm9', from_actor: 'bot:botops', to_actor: 'human:ben', body: 'Which inbox should the bot answer?', created_at: ago(12), refs: {}}},
              {kind: 'question', id: 't4', created_at: now, ask: {id: 'q4', from_actor: 'bot:coo', to_actor: 'human:ben', body: 'Two launch questions', created_at: now,
               refs: {questions: [{question: 'Launch day?', options: [{label: 'Mon'}, {label: 'Tue'}]}, {question: 'Price?', options: [{label: '$9'}, {label: '$19'}]}]}}}]};
    let {page, errors} = await open(browser, {width: 1280, height: 900}, state);
    assert.equal(await page.locator('#nav-assistant, #nav-botops, #help-open, #mobile-support').count(), 0, 'no BotOps, Assistant or ? in the rail');
    const fab = page.locator('#ask-row'), panel = page.locator('#ask-panel');
    await fab.waitFor();
    assert.equal(await fab.evaluate(el => getComputedStyle(el, '::after').content), 'none', 'no count on the button');
    // The row sits in the sidebar's footer, above the account, nothing floating over the page; Search is an icon at its
    // end (⌘K in its name) and opens Search.
    const where = await page.evaluate(() => { const row = document.querySelector('#ask-row'), b = el => el.getBoundingClientRect();
      return {inFooter: !!row.closest('#side .side-footer'), above: b(row).bottom <= b(document.querySelector('.side-utility')).top + 1,
        next: row.nextElementSibling?.id, rail: b(document.querySelector('#side')).right, right: b(row).right, height: Math.round(b(row).height)}; });
    assert.deepEqual([where.inFooter, where.above, where.next], [true, true, 'search-open'], JSON.stringify(where));
    assert.ok(where.right < where.rail && where.height <= 36, `one plain row in the rail: ${JSON.stringify(where)}`);
    const search = page.locator('#search-open');
    assert.equal(await search.getAttribute('aria-label'), 'Search (⌘K)');
    assert.equal(await search.innerText(), 'search', 'an icon, no words beside Ask');
    const hit = await search.evaluate(el => { const b = el.getBoundingClientRect(); return {w: b.width, h: b.height}; });
    assert.ok(hit.w >= 44 && hit.h >= 34, `Search is easy to press: ${JSON.stringify(hit)}`);
    await search.click();
    await page.locator('#search-modal[open]').waitFor();
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('#search-modal').open);
    // A peek: Tico, in a few words, that two questions wait; never who asked or the question. × puts both away.
    const peek = page.locator('#ask-peek');
    await peek.waitFor({timeout: 8000});
    const said = await peek.locator('[data-peek-text]').innerText();
    assert.equal(said, '2 questions are waiting on you');
    // It comes out of the rail, level with the row.
    const level = await page.evaluate(() => { const b = s => document.querySelector(s).getBoundingClientRect(), p = b('#ask-peek'), r = b('#ask-row');
      return {dy: Math.abs((p.top + p.bottom) / 2 - (r.top + r.bottom) / 2), gap: p.left - b('#side').right}; });
    assert.ok(level.dy < 2 && level.gap > 0 && level.gap < 16, `the peek sits beside the row: ${JSON.stringify(level)}`);
    assert.ok(words(said) <= 6 && !/asks/i.test(said), said);
    assert.equal(await peek.locator('[data-peek-go]').innerText(), said, 'the line is the action');
    await peek.getByRole('button', {name: 'Dismiss'}).click();
    assert.equal(await peek.isHidden(), true);
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('tico.ask.peek.ben')).dismissed), ['m9', 't4']);
    // Open: the row's dot flies up to its place beside the panel's name and lands exactly there. The row stays, a close
    // mark in its dot's place; the panel stands straight above the row, over the sidebar, and leaves the row in view.
    // The flight is quick: under 350 ms however far it goes.
    const {took} = await flight(page, '#ask-panel .ask-head [data-ask-dot]', async () => { await fab.click(); await panel.waitFor(); });
    assert.ok(took <= 350, `a quick flight: ${took}ms`);
    assert.deepEqual([await fab.getAttribute('aria-expanded'), await fab.getAttribute('aria-label')], ['true', 'Close Ask Tico']);
    assert.equal(await fab.isVisible(), true, 'the row stays');
    assert.equal(await fab.evaluate(el => getComputedStyle(el.querySelector('.ask-x')).opacity), '1', 'a close mark holds its place');
    const above = await page.evaluate(() => { const b = s => document.querySelector(s).getBoundingClientRect(), p = b('#ask-panel'), r = b('#ask-row');
      return {left: p.left - b('#side').left, gap: r.top - p.bottom, top: p.top}; });
    assert.ok(above.left >= 0 && above.left <= 12 && above.gap >= 0 && above.gap <= 12 && above.top >= 0,
      `the panel stands on the row: ${JSON.stringify(above)}`);
    // The header's dot is the one live dot while it is open; Tico's turns carry none.
    const dots = () => page.evaluate(() => ['#ask-row [data-ask-dot]', '#ask-panel [data-ask-dot]'].map(s => document.querySelector(s)?.dataset.state || ''));
    const landed = () => page.waitForFunction(() => !document.querySelector('.ldot-fly, .ldot-away'));
    assert.deepEqual(await dots(), ['still', 'idle']);
    // The header: the dot, "Tico Assistant", the page it sees, support's chip; no +, no Show chat.
    assert.equal(await panel.locator('#ask-title').innerText(), 'Tico Assistant');
    assert.equal(await panel.getAttribute('aria-label'), 'Tico Assistant');
    assert.equal(await panel.locator('#ask-add, [data-ask-menu], .ask-full, [data-ask-switch]').count(), 0, 'no +, menu, Show chat or switch');
    assert.equal(await panel.getByText('Show chat').count(), 0);
    const box = panel.locator('.ask-form .p-text');
    await box.waitFor();
    await page.waitForFunction(() => document.activeElement?.matches('#ask-panel .p-text'));
    assert.equal(await panel.locator('[data-ask-sees-name]').innerText(), await page.locator('#main h1, #main h2').first().innerText(), 'sees the page');
    const thread = panel.locator('[data-ask-thread]');
    await thread.locator('.conv-run[data-message="a2"]').waitFor();
    assert.equal(await panel.locator('.ask-turn-dot, [data-ask-thread] .ldot').count(), 0, "Tico's turns carry no dot");
    assert.equal(await panel.locator('.conv-run[data-message="a2"] .bubble').evaluate(el => getComputedStyle(el).backgroundColor), 'rgba(0, 0, 0, 0)');
    // An open request is in this chat: its chip is in the header (and the link is not), a line says support came in,
    // and its messages flow in (support's blue-gray under a headset, Ben's on the right) and are read.
    const chip = panel.locator('[data-ask-people] .ask-sup-chip'), loop = panel.getByRole('button', {name: 'Loop in Support'});
    await chip.waitFor();
    assert.equal(await loop.isHidden(), true, 'no link while support is in');
    assert.match(await chip.innerText(), /Tico Support/);
    assert.equal(await panel.locator('[data-ask-members], [data-ask-ticket]').count(), 0, 'no members bar');
    const them = panel.locator('.ask-sup.them', {hasText: 'Try a hard reload.'});
    await them.waitFor();
    assert.equal(await them.locator('.ask-sav').innerText(), 'headset_mic');
    assert.equal(await them.locator('small').innerText(), 'Tico Support');
    assert.equal(await panel.locator('.ask-sup.you .ask-sup-body').first().innerText(), 'Board will not load');
    assert.deepEqual(await panel.locator('.ask-sys').allInnerTexts(), ['You added Tico Support. They can now see chats.']);
    await page.waitForFunction(() => !window.supportState?.().open?.some(t => t.unread));
    assert.deepEqual(state.read, ['sup_1'], 'a reply seen here is read');
    // Everything in time order, cards too: BotOps' card and question sit between Ben's question and the answer, which
    // came after them; the launch questions, newest, are last. The thread opens at the newest.
    await thread.locator('[data-action-host="act9"] [data-confirm]').waitFor();
    const order = await thread.evaluate(el => [...el.children].map(c => c.matches('.ask-sys') ? 'sys' : c.matches('.ask-sup') ? 'sup'
      : c.dataset.message || c.dataset.askQ || c.querySelector('[data-action-host]')?.dataset.actionHost || '?'));
    assert.deepEqual(order, ['sys', 'sup', 'sup', 'a1', 'act9', 'm9', 'a2', 't4'], `in time order: ${order}`);
    assert.match(await thread.locator('.ask-card[data-ask-q="m9"]').innerText(), /BotOps asks/, 'a card says who asks');
    assert.ok(await thread.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight < 4), 'opens at the newest');
    // BotOps' Confirm card, confirmed here, and its question, answered here.
    await thread.locator('[data-action-host="act9"]').getByRole('button', {name: 'Confirm'}).click();
    await page.waitForFunction(() => !document.querySelector('#ask-panel [data-action-host="act9"] [data-confirm]'));
    assert.deepEqual(state.decided, ['act9']);
    // Several structured questions are answered on the task, not with a line of words.
    const several = thread.locator('.ask-card[data-ask-q="t4"]');
    assert.equal(await several.locator('a[href="#/task/t4"]').count(), 1);
    assert.equal(await several.locator('form').count(), 0);
    const question = thread.locator('.ask-card', {hasText: 'Which inbox should the bot answer?'});
    await question.getByRole('textbox', {name: 'Answer BotOps'}).fill('support@acme.test');
    await question.getByRole('button', {name: 'Send'}).click();
    await question.waitFor({state: 'detached'});
    assert.deepEqual(state.answers, ['support@acme.test']);
    // Three picks or more are a list and Send, and the one chosen survives the thread being drawn again.
    const need = (id, extra) => ({kind: 'question', id, created_at: now, ask: {id: 'q' + id, from_actor: 'bot:botops', to_actor: 'human:ben', created_at: now, ...extra}});
    state.picked = [];
    state.needs = [need('t7', {body: 'Tone?', refs: {questions: [{id: 'tone', question: 'Which tone?', options: [{label: 'Plain'}, {label: 'Friendly'}, {label: 'Formal'}]}]}})];
    await page.evaluate(async () => { await v2Refresh(); ASK.draw(); });
    const tone = thread.locator('.ask-card[data-ask-q="t7"]');
    await tone.locator('select').selectOption('2');
    state.needs = [...state.needs, {...need('m10', {body: 'Anything else?', refs: {}}), kind: 'ask'}];
    await page.evaluate(async () => { await v2Refresh(); ASK.draw(); });
    await thread.locator('.ask-card[data-ask-q="m10"]').waitFor();
    assert.equal(await tone.locator('select').inputValue(), '2', 'the pick stays across a redraw');
    // Reworded (a new question id), the old pick is gone: the recommended option shows. Back to the pick for Send.
    state.needs = [need('t7', {body: 'Tone?', refs: {questions: [{id: 'tone-2', question: 'Which tone now?', options: [{label: 'Plain'}, {label: 'Friendly', recommended: true}, {label: 'Formal'}]}]}}), state.needs[1]];
    await page.evaluate(async () => { await v2Refresh(); ASK.draw(); });
    await tone.getByText('Which tone now?').waitFor();
    assert.equal(await tone.locator('select').inputValue(), '1', 'a reworded question starts at its recommended option');
    await tone.locator('select').selectOption('2');
    await tone.getByRole('button', {name: 'Send'}).click();
    await tone.waitFor({state: 'detached'});
    assert.deepEqual(state.picked, [['Formal']]);
    // While support is in, the box writes to it, not to the Assistant.
    assert.equal(await box.getAttribute('placeholder'), 'Message Tico Support');
    await box.fill('Still blank after a reload');
    await box.press('Enter');
    await panel.locator('.ask-sup.you', {hasText: 'Still blank after a reload'}).waitFor();
    assert.equal(state.sent.length, 0, 'not sent to the Assistant');
    assert.equal(await thread.locator('.bubble', {hasText: 'In Docs.'}).count(), 1, 'the whole chat stays');
    // The chip's × takes support out: the link comes back, a line says so, what support said stays for this visit,
    // and the request stays open in Support (taken out of this chat only).
    await chip.getByRole('button', {name: 'Remove Tico Support'}).click();
    assert.equal(await chip.count(), 0);
    await loop.waitFor();
    assert.deepEqual(await panel.locator('.ask-sys').allInnerTexts(),
      ['You added Tico Support. They can now see chats.', 'Tico Support can no longer see chats.']);
    assert.equal(await them.count(), 1, 'what support said stays');
    assert.equal(await box.getAttribute('placeholder'), 'Message Tico Assistant');
    assert.equal(await box.getAttribute('aria-label'), 'Message Tico Assistant');
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('tico.ask.hidden.ben'))), ['sup_1']);
    await page.waitForFunction(() => !document.querySelector('.ask-fly'));
    // "Loop in Support" brings it back, sharing the chat with it (not drawn again: it is on screen): support's bubble
    // pops out of the dot and lands as the chip, the line follows, and the link hides.
    await loop.click();
    await page.waitForFunction(() => document.querySelector('.ask-sav.ask-fly'));
    await chip.waitFor();
    assert.equal(await loop.isHidden(), true, 'the link hides while support is in');
    assert.equal(state.tickets[0].messages.at(-1).body, 'Shared from my chat with Assistant:\n\nMe: Where is the launch plan?\nAssistant: In Docs.',
      'the chat with the Assistant went to the request');
    assert.equal(await thread.locator('.ask-sup', {hasText: 'Shared from'}).count(), 0);
    assert.equal(await panel.locator('.ask-sys').last().innerText(), 'You added Tico Support. They can now see chats.');
    await page.waitForFunction(() => !document.querySelector('.ask-fly'));
    assert.equal(await chip.isVisible(), true, 'the chip landed');
    await chip.getByRole('button', {name: 'Remove Tico Support'}).click();
    await loop.waitFor();
    assert.equal(await panel.locator('.ask-sys').last().innerText(), 'Tico Support can no longer see chats.');
    // A message goes with the page it was asked from; a link to Support in the reply opens Support over the page.
    await box.fill('The board will not load');
    await box.press('Enter');
    await panel.getByRole('link', {name: 'Loop in Tico support'}).waitFor();
    await landed();
    assert.equal(await panel.locator('[data-ask-thread] .ldot').count(), 0, 'a new turn takes no dot either');
    assert.equal(state.sent[0].text, 'The board will not load');
    assert.equal(state.sent[0].page.path, '#/tasks');
    // The clip attaches a file (as a drop or a paste does); it goes with the words and the page.
    await panel.locator('.p-attach').waitFor();
    await panel.locator('.p-file').setInputFiles({name: 'plan.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4')});
    await box.fill('Read this');
    await box.press('Enter');
    await page.waitForFunction(() => document.querySelector('#ask-panel .p-text').value === '');
    assert.deepEqual(state.uploaded, [{text: 'Read this', page: {path: '#/tasks', title: state.uploaded[0].page.title}, files: ['plan.pdf']}]);
    assert.equal(state.sent.length, 1, 'with a file it goes up as an upload');
    // A file dropped anywhere on the panel attaches too.
    await page.evaluate(() => { const dt = new DataTransfer(), panel = document.querySelector('#ask-panel');
      dt.items.add(new File(['notes'], 'notes.txt', {type: 'text/plain'}));
      for (const type of ['dragenter', 'dragover', 'drop']) panel.querySelector('[data-ask-thread]').dispatchEvent(new DragEvent(type, {dataTransfer: dt, bubbles: true, cancelable: true})); });
    await panel.locator('.p-chips > *').first().waitFor();
    await box.fill('And this');
    await box.press('Enter');
    await page.waitForFunction(() => !document.querySelector('#ask-panel .p-chips')?.children.length);
    assert.deepEqual(state.uploaded.map(u => u.files), [['plan.pdf'], ['notes.txt']]);
    await panel.getByRole('link', {name: 'Loop in Tico support'}).first().click();
    await page.locator('#support-pop[open]').waitFor();
    assert.equal(await page.evaluate(() => location.hash), '#/tasks', 'the page behind stays');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('#support-pop')?.open);
    // Esc closes the panel: the dot flies back down to the row and lands on its dot, focus back on the row; ⌘J opens it
    // again.
    await box.focus();
    await flight(page, '#ask-row [data-ask-dot]', async () => { await page.keyboard.press('Escape'); await panel.waitFor({state: 'hidden'}); });
    assert.deepEqual(await dots(), ['idle', 'still'], 'closed: the dot is back on the row');
    assert.equal(await page.evaluate(() => document.activeElement?.id), 'ask-row');
    assert.equal(await fab.getAttribute('aria-label'), null);
    await flight(page, '#ask-panel .ask-head [data-ask-dot]', async () => { await page.keyboard.press('Control+j'); await panel.waitFor(); });
    // The Assistant page is this chat already: there the row is not needed, and Search is the whole row again.
    await page.evaluate(() => { location.hash = '#/assistant'; });
    await fab.waitFor({state: 'hidden'});
    assert.equal((await search.innerText()).replace(/\s+/g, ' '), 'search Search ⌘K');
    assert.deepEqual(errors, []);
    await page.close();

    // A phone: the dot in the middle of the tab bar opens a sheet over the page, above the tab bar, and closes it.
    const ask = (id, who, body) => ({kind: 'ask', id, created_at: now, ask: {id, from_actor: who, to_actor: 'human:ben', body, created_at: now, refs: {}}});
    state = {messages: [msg('p1', 'human:ben', 'Our key is sk-live1234567890abcdef, board blank', ago(5)), msg('p2', 'bot:coo', 'Try a reload.', ago(4))],
      sent: [], read: [], decided: [], answers: [], tickets: [], card: null,
      needs: [ask('m8', 'bot:botops', 'Which inbox should the bot answer?'), ask('m7', 'bot:coo', 'Ship the launch post today?')]};
    ({page, errors} = await open(browser, {width: 390, height: 844}, state));
    const tab = page.locator('#mobile-ask');
    // The Ask tab is the row too, words and all: the dot, or a close mark in its place while the sheet is open.
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
    assert.equal(await page.locator('#ask-row').isVisible(), false, 'no Ask row on a phone');
    assert.equal(await tab.locator('[data-ask-dot]').getAttribute('data-state'), 'idle');
    // A peek above the bar after a few seconds; tapping it opens the chat on the first question, the dot flying up to
    // the sheet's header.
    const peek2 = page.locator('#ask-peek');
    await peek2.waitFor({timeout: 8000});
    const said2 = await peek2.locator('[data-peek-text]').innerText();
    assert.equal(said2, '2 questions are waiting on you');
    assert.ok(words(said2) <= 6 && !/asks/i.test(said2), said2);
    await flight(page, '#ask-panel .ask-head [data-ask-dot]', async () => { await peek2.locator('[data-peek-go]').click(); await page.locator('#ask-panel').waitFor(); });
    await page.locator('#ask-panel .ask-card[data-ask-q="m8"]').waitFor();
    assert.equal(await page.locator('#ask-panel .ask-card[data-ask-q="m8"]').isVisible(), true);
    assert.equal(await peek2.isHidden(), true, 'no peek while the chat is open');
    const sheet = await page.locator('#ask-panel').boundingBox(), bar = await page.locator('#mobile-nav').boundingBox();
    assert.ok(sheet.x === 0 && sheet.width === 390 && Math.abs(sheet.y + sheet.height - bar.y) < 2, `a sheet above the tab bar (${JSON.stringify([sheet, bar])})`);
    assert.deepEqual([await tab.getAttribute('aria-label'), await glyph()], ['Close Ask Tico', 'close']);
    assert.equal(await page.locator('#ask-panel').evaluate(el => [...el.querySelectorAll('*')].every(x => x.getBoundingClientRect().right <= innerWidth + 1)), true, 'no horizontal scroll');
    // No open request: "Loop in Support" sends a new one at once (no form), holding the chat with its key cut out.
    const ploop = page.locator('#ask-panel').getByRole('button', {name: 'Loop in Support'});
    await ploop.click();
    await page.locator('#ask-panel [data-ask-people] .ask-sup-chip').waitFor();
    assert.equal(await ploop.isHidden(), true);
    assert.deepEqual(state.filed.map(f => f.message), ['Shared from my chat with Assistant:\n\nMe: Our key is [key], board blank\nAssistant: Try a reload.']);
    assert.equal(await page.locator('#support-pop[open]').count(), 0, 'sent at once, no form');
    assert.equal(await page.locator('#ask-panel .ask-sup', {hasText: 'Shared from'}).count(), 0, 'not drawn again');
    assert.equal(await page.locator('#ask-panel .ask-sys').last().innerText(), 'You added Tico Support. They can now see chats.');
    await page.waitForFunction(() => !document.querySelector('.ask-fly'));
    assert.equal(await page.locator('#ask-panel').evaluate(el => [...el.querySelectorAll('*')].every(x => x.getBoundingClientRect().right <= innerWidth + 1)), true, 'no horizontal scroll with the chip');
    // Closing flies the dot back down into the tab bar.
    await flight(page, '#mobile-ask [data-ask-dot]', async () => { await tab.click(); await page.locator('#ask-panel').waitFor({state: 'hidden'}); });
    assert.deepEqual([await tab.getAttribute('aria-label'), await glyph()], [null, 'dot']);
    await page.waitForTimeout(300);
    assert.equal(await peek2.isHidden(), true, 'the line spoke for both questions: no second peek');
    // A "things need you" sheet already open: the chat opens over it, not under it.
    await page.evaluate(() => { const s = document.createElement('section'); s.className = 'needs-you'; s.style.height = '600px'; document.body.appendChild(s); });
    await tab.click();
    await page.locator('#ask-panel').waitFor();
    await page.locator('#ask-panel').evaluate(el => Promise.all(el.getAnimations().map(a => a.finished)));
    assert.equal(await page.evaluate(() => { const r = document.querySelector('#ask-panel').getBoundingClientRect();
      return !!document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)?.closest('#ask-panel'); }), true, 'the chat is above the needs-you sheet');
    await tab.click();
    await page.locator('#ask-panel').waitFor({state: 'hidden'});
    assert.deepEqual(errors, []);
    await page.close();
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
