import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
// Ask (ui/app/ask-panel.ts): the person's chat bot on every page. On a computer the "Tico Assistant" row at the bottom of
// the sidebar, with Search's icon at its end (or ⌘J), opens "Tico Assistant", a panel straight above the row that names the
// page it sees and sends it with the message, and a close mark takes the row's dot place; on a phone the dot in the middle of the tab bar opens a sheet and closes it again. Its dot flies up to
// its place beside the panel's name and lands there exactly, and flies back down on close; Tico's turns carry no dot.
// The header has no +, no Show chat and nothing of Tico support (the Tico team has its own chat, ui/support.ts); a
// #/support link in a reply opens that chat over the page. A Confirm card BotOps
// left in its own chat and bots' questions sit in the thread by when they were asked, answered here. No BotOps or
// Assistant rows in the rail. Fixtures only.
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;
type AskMsg = {id: string; sender_id: string; body: string; refs: Record<string, unknown>; created_at: string; kind: string};
type AskTicket = {id: string; status: string; unread: boolean; title: string; message: string; created_at: string; messages: {body: string; from: string; created_at: string}[]};
type AskNeed = {kind: string; id: string; task_id: string; requirement_id: string; line: string; since: string; ask: Record<string, unknown>};
type AskCard = {id: string; summary: string; method: string; path: string; status: string; result: null; description: string; diff: unknown[];
  body: Record<string, unknown>; proposed_via: string; created_at: string};
type AskExecution = {job_id: string; message_id: string; bot_id: string; state: string; label: string; text: string; started_at: string;
  parts: {kind: string; text: string; at: string}[]};
type AskState = {messages: AskMsg[]; sent: {text: string; page: {path: string}}[]; read: string[]; decided: string[]; answers: unknown[];
  tickets: AskTicket[]; needs: AskNeed[]; card?: AskCard | null; execution?: AskExecution | null; picked?: unknown[][];
  uploaded?: {text: string | undefined; page: {path: string; title: string}; files: string[]}[]; supportOff?: boolean; unreadCount?: number;
  replies?: unknown[]; filed?: unknown[]};
declare const ASK: {draw(): void; refresh(): void};
declare function v2Refresh(): Promise<void>;

const now = new Date().toISOString(), ago = (min: number) => new Date(Date.now() - min * 60000).toISOString();
// An ask to Ben in Needs you, as GET /v2/needs-you sends it: the item's id is the requirement's, `ask` its requirement.
const need = (rid: string, task: string, who: string | null, line: string, since: string, extra: Record<string, unknown> = {}): AskNeed => ({kind: 'ask', id: rid,
  task_id: task, requirement_id: rid, line, since, ask: {requirement_id: rid, kind: 'answer', line, asked_by: who, who: 'human:ben', choices: null,
    may: ['answer', 'give'], met: false, shows: [], standing: 'Waiting on Ben', answer: null, ...extra}});
const msg = (id: string, from: string, body: string, created: string = now): AskMsg => ({id, sender_id: from, body, refs: {}, created_at: created, kind: 'say'});

async function open(browser: import('playwright').Browser, viewport: {width: number; height: number}, state: AskState) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport.width < 760});
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname, method = route.request().method();
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ben', role: 'human', name: 'Ben', email: 'ben@example.test', cloud: true});
    if (p === '/api/humans') return json({people: [{id: 'ben', name: 'Ben'}]});
    if (p === '/api/employees') return json([{name: 'assistant', display_name: 'Assistant'}, {name: 'botops', display_name: 'BotOps'}]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/tasks') return json({tasks: []});
    if (p === '/api/v2/conversations/room1/snapshot') return json({messages: state.messages, execution: null, has_more: false, next_before: null});
    if (p === '/api/v2/assistant' && method === 'GET') return json({available: true, state: 'active', bot: 'assistant', name: 'Assistant',
      can_turn_on: false, room_id: 'room1', messages: state.messages, has_more: false, next_before: null, execution: state.execution || null, actions: {},
      pending: state.card?.status === 'pending' ? [state.card] : []});
    if (p === '/api/v2/assistant/actions/act9' && method === 'GET') return json({action: state.card});
    if (p === '/api/v2/assistant/actions/act9/confirm') { state.decided.push('act9'); state.card!.status = 'done'; return json({action: state.card}); }
    if (p === '/api/v2/needs-you') return json({actor: 'human:ben', count: state.needs.length, version: 'v' + state.needs.length, items: state.needs});
    const answer = p.match(/^\/api\/v2\/tasks\/([^/]+)\/asks\/([^/]+)\/answer$/);
    if (answer && method === 'POST') {
      state.picked!.push([answer[1], answer[2], JSON.parse(route.request().postData()!).value]);
      state.needs = state.needs.filter(n => n.id !== answer[2]); return json({task: {id: answer[1]}});
    }
    if (p === '/api/v2/assistant/messages') {
      const body = JSON.parse(route.request().postData()!);
      state.sent.push(body);
      const user = msg('u' + state.sent.length, 'human:ben', body.text!, new Date().toISOString());
      const bot = msg('b' + state.sent.length, 'bot:assistant', "I can't fix that one. [Talk to the Tico team](#/support)", new Date().toISOString());
      state.messages.push(user, bot);
      return json({message: user});
    }
    if (p === '/api/v2/uploads/assistant/messages') {
      const raw = route.request().postDataBuffer()!.toString('latin1'), field = (name: string) => raw.match(new RegExp(`name="${name}"\\r\\n\\r\\n([^\\r]*)`))?.[1];
      const body = {text: field('text'), page: JSON.parse(field('page') || 'null'), files: [...raw.matchAll(/filename="([^"]+)"/g)].map(m => m[1]!)};
      state.uploaded = (state.uploaded || []).concat([body]);
      const user = msg('f' + state.uploaded.length, 'human:ben', body.text!, new Date().toISOString());
      state.messages.push(user, msg('g' + state.uploaded.length, 'bot:assistant', 'Read it.', new Date().toISOString()));   // the turn's reply, by the snapshot
      return json({message: user});
    }
    const ticket = p.match(/^\/api\/v2\/support\/tickets\/([^/]+)\/(messages|read)$/), listing = () => json({enabled: state.supportOff !== true, tickets: state.tickets, unread: state.unreadCount ?? state.tickets.filter(t => t.unread).length});
    if (ticket) {
      const t = state.tickets.find(x => x.id === ticket[1])!;
      if (ticket[2] === 'read') { state.read.push(t.id); t.unread = false; }
      else { const body = JSON.parse(route.request().postData()!); (state.replies ||= []).push(body); t.messages.push({body: body.message, from: 'you', created_at: new Date().toISOString()}); }
      return json(t);
    }
    if (p === '/api/v2/support/tickets' && method === 'POST') {
      const body = JSON.parse(route.request().postData()!), t = {id: 'sup_' + (state.tickets.length + 1), status: 'open', unread: false,
        title: body.message.slice(0, 40), message: body.message, created_at: new Date().toISOString(), messages: []};
      (state.filed ||= []).push(body); state.tickets.unshift(t);
      return route.fulfill({status: 201, contentType: 'application/json', body: JSON.stringify(t)});
    }
    if (p === '/api/v2/support/tickets' || p === '/api/v2/support/tickets/refresh') return listing();
    if (p === '/api/v2/support/compose') return json({email: 'ben@example.test', diagnostics: {}});
    if (p === '/api/v2/support/diagnostics/capture' || p === '/api/v2/support/diagnostics') return json({id: 'diag_1', text: '{\n  "format": 1\n}', bytes: 20});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/tasks');
  await page.waitForFunction(() => !document.querySelector('#account .account-email')?.textContent.includes('Signing in'));
  return {page, errors};
}

// The dot's flight after `act`: where the copy lands (its box once its transform is spent), and, once it has, where
// `dot` is. The watch starts before `act`: a quick flight can be over before a wait inside `act` returns.
async function flight(page: import('playwright').Page, dot: string, act: () => Promise<void>) {
  const seen = page.waitForFunction(() => { const g = document.querySelector<HTMLElement>('.ldot-fly');
    return g && {left: parseFloat(g.style.left), top: parseFloat(g.style.top), width: parseFloat(g.style.width),
      took: g.getAnimations()[0]?.effect!.getTiming().duration}; });
  await act();
  const ghost = await seen.then(h => h.jsonValue()) as {left: number; top: number; width: number; took: number};
  await page.waitForFunction(() => !document.querySelector('.ldot-fly, .ldot-away'));
  await page.locator('#ask-panel').evaluate(el => Promise.all(el.getAnimations().map(a => a.finished)));
  const r = await page.locator(dot).evaluate(el => { const b = el.getBoundingClientRect(); return {left: b.left, top: b.top, width: b.width}; });
  // the dot breathes (a scale of 3% at most), so its centre is what must match
  const off = Math.hypot(ghost.left + ghost.width / 2 - r.left - r.width / 2, ghost.top + ghost.width / 2 - r.top - r.width / 2);
  assert.ok(off < 2, `the dot landed ${off.toFixed(1)}px off ${dot}: ${JSON.stringify({ghost, r})}`);
  return ghost;
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as Parameters<typeof chromium.launch>[0]);
  try {
    // A computer. A Tico team ticket is open (it stays out of Ask), then Ben asked, BotOps left a card, and the Assistant
    // answered. Three asks wait on Ben in Needs you: BotOps' question, an OK and a stage's Spec page.
    let state: AskState = {messages: [msg('a1', 'human:ben', 'Where is the launch plan?', ago(15)), msg('a2', 'bot:assistant', 'In Docs.', ago(10))], sent: [], read: [], decided: [], answers: [],
      tickets: [{id: 'sup_1', status: 'answered', unread: true, title: 'Board will not load', message: 'Board will not load', created_at: ago(30),
                 messages: [{body: 'Try a hard reload.', from: 'staff', created_at: ago(20)}]}],
      card: {id: 'act9', summary: 'Add Cara as an admin', method: 'POST', path: '/api/v2/people', status: 'pending', result: null,
             description: 'Add Cara as an admin', diff: [], body: {}, proposed_via: 'botops', created_at: ago(13)},
      picked: [],
      needs: [need('r9', 't9', 'bot:botops', 'Which inbox should the bot answer?', ago(12)),
              need('r4', 't4', 'bot:assistant', 'OK the launch post', now, {kind: 'ok', may: ['ok', 'no', 'give']}),
              need('r5', 't5', null, 'Spec for Share Assistant chat, ready for your OK', ago(11), {kind: 'page', may: ['ok', 'no']})]};
    let {page, errors} = await open(browser, {width: 1280, height: 900}, state);
    assert.equal(await page.locator('#nav-assistant, #nav-botops, #help-open, #mobile-support').count(), 0, 'no BotOps or Assistant rows, no old Help link in the rail');
    const fab = page.locator('#ask-row'), panel = page.locator('#ask-panel');
    await fab.waitFor();
    assert.equal(await fab.evaluate(el => getComputedStyle(el, '::after').content), 'none', 'no count on the button');
    // The row sits in the sidebar's footer, above the account, nothing floating over the page; Search is an icon at its
    // end (⌘K in its name) and opens Search.
    const where = await page.evaluate(() => { const row = document.querySelector<HTMLElement>('#ask-row')!, b = (el: Element) => el.getBoundingClientRect();
      return {inFooter: !!row.closest('#side .side-footer'), above: b(row).bottom <= b(document.querySelector('.side-utility')!).top + 1,
        next: row.nextElementSibling?.id, rail: b(document.querySelector('#side')!).right, right: b(row).right, height: Math.round(b(row).height)}; });
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
    await page.waitForFunction(() => !document.querySelector<HTMLDialogElement>('#search-modal')!.open);
    // Nothing speaks up beside the row: the questions wait in the panel. A team reply still opens its chat by itself.
    assert.equal(await page.locator('#ask-peek').count(), 0, 'no Ask peek');
    await page.locator('#support-pop[open]').waitFor({timeout: 8000});
    await page.keyboard.press('Escape');
    await page.locator('#support-pop').waitFor({state: 'hidden'});
    // Open: the row's dot flies up to its place beside the panel's name and lands exactly there. The row stays, a close
    // mark in its dot's place; the panel stands straight above the row, over the sidebar, and leaves the row in view.
    // The flight is quick: under 350 ms however far it goes.
    const {took} = await flight(page, '#ask-panel .ask-head [data-ask-dot]', async () => { await fab.click(); await panel.waitFor(); });
    assert.ok(took <= 350, `a quick flight: ${took}ms`);
    assert.deepEqual([await fab.getAttribute('aria-expanded'), await fab.getAttribute('aria-label')], ['true', 'Close Tico Assistant']);
    assert.equal(await fab.isVisible(), true, 'the row stays');
    assert.equal(await fab.evaluate(el => getComputedStyle(el.querySelector('.ask-x')!).opacity), '1', 'a close mark holds its place');
    const above = await page.evaluate(() => { const b = (s: string) => document.querySelector(s)!.getBoundingClientRect(), p = b('#ask-panel'), r = b('#ask-row');
      return {left: p.left - b('#side').left, gap: r.top - p.bottom, top: p.top}; });
    assert.ok(above.left >= 0 && above.left <= 12 && above.gap >= 0 && above.gap <= 12 && above.top >= 0,
      `the panel stands on the row: ${JSON.stringify(above)}`);
    // The header's dot is the one live dot while it is open; Tico's turns carry none.
    const dots = () => page.evaluate(() => ['#ask-row [data-ask-dot]', '#ask-panel [data-ask-dot]'].map(s => document.querySelector<HTMLElement>(s)?.dataset.state || ''));
    const landed = () => page.waitForFunction(() => !document.querySelector('.ldot-fly, .ldot-away'));
    assert.deepEqual(await dots(), ['still', 'idle']);
    // The header: the dot, "Tico Assistant", the page it sees; no +, no Show chat.
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
    // Nothing of Tico support in Ask: no link, chip, list or support bubbles, though the person has a ticket open.
    assert.equal(await panel.locator('[data-ask-loop], [data-ask-requests], [data-ask-people], .ask-sup, .ask-sup-chip, .ask-sys, [data-ask-loop-note]').count(), 0, 'no support in Ask');
    assert.doesNotMatch(await panel.innerText(), /Loop in|Your requests|Tico Support|Try a hard reload/);
    // Only the conversation, in time order: BotOps' Confirm card (a preview this person asked for) sits between Ben's
    // question and the answer, which came after it. The asks waiting on Ben in Needs you add nothing to the chat: no
    // card, no line about them (spec #330). The thread opens at the newest.
    await thread.locator('[data-action-host="act9"] [data-confirm]').waitFor();
    const order = () => thread.evaluate(el => [...(el.children as unknown as HTMLElement[])].map(c => c.dataset.message || c.querySelector<HTMLElement>('[data-action-host]')?.dataset.actionHost || '?'));
    assert.deepEqual(await order(), ['a1', 'act9', 'a2'], 'only the conversation');
    assert.equal(await thread.locator('[data-ask-q], .ask-card').count(), 0, 'no Needs-you card in the chat');
    assert.doesNotMatch(await thread.innerText(), /Which inbox|OK the launch post|Spec for|Review page|Open/, 'nothing of the asks is said');
    assert.ok(await thread.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight < 4), 'opens at the newest');
    // BotOps' Confirm card, confirmed here.
    await thread.locator('[data-action-host="act9"]').getByRole('button', {name: 'Confirm'}).click();
    await page.waitForFunction(() => !document.querySelector('#ask-panel [data-action-host="act9"] [data-confirm]'));
    assert.deepEqual(state.decided, ['act9']);
    // An ask that arrives while the panel is open is not added to the chat either; it waits in Needs you.
    state.needs = [...state.needs, need('r10', 't10', 'bot:botops', 'Anything else?', now, {choices: ['Yes', 'No']})];
    const before = await order();
    await page.evaluate(async () => { await v2Refresh(); ASK.draw(); });
    assert.deepEqual(await order(), before, 'an ask arriving mid-chat adds nothing');
    assert.equal(await thread.locator('[data-ask-q], .ask-card').count(), 0);
    assert.deepEqual(state.picked, [], 'nothing answered from the chat');
    assert.equal(await box.getAttribute('placeholder'), 'Message Tico Assistant');
    assert.equal(await box.getAttribute('aria-label'), 'Message Tico Assistant');
    // A message goes with the page it was asked from; a #/support link in the reply opens the Tico team chat over the page.
    await box.fill('The board will not load');
    await box.press('Enter');
    await panel.getByRole('link', {name: 'Talk to the Tico team'}).waitFor();
    await landed();
    assert.equal(await panel.locator('[data-ask-thread] .ldot').count(), 0, 'a new turn takes no dot either');
    assert.equal(state.sent[0]!.text, 'The board will not load');
    assert.equal(state.sent[0]!.page.path, '#/tasks');
    // The clip attaches a file (as a drop or a paste does); it goes with the words and the page.
    await panel.locator('.p-attach').waitFor();
    await panel.locator('.p-file').setInputFiles({name: 'plan.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4')});
    await box.fill('Read this');
    await box.press('Enter');
    await page.waitForFunction(() => document.querySelector<HTMLTextAreaElement>('#ask-panel .p-text')!.value === '');
    assert.deepEqual(state.uploaded, [{text: 'Read this', page: {path: '#/tasks', title: state.uploaded![0]!.page.title}, files: ['plan.pdf']}]);
    assert.equal(state.sent.length, 1, 'with a file it goes up as an upload');
    // A file dropped anywhere on the panel attaches too.
    await page.evaluate(() => { const dt = new DataTransfer(), panel = document.querySelector<HTMLElement>('#ask-panel')!;
      dt.items.add(new File(['notes'], 'notes.txt', {type: 'text/plain'}));
      for (const type of ['dragenter', 'dragover', 'drop']) panel.querySelector('[data-ask-thread]')!.dispatchEvent(new DragEvent(type, {dataTransfer: dt, bubbles: true, cancelable: true})); });
    await panel.locator('.p-chips > *').first().waitFor();
    await box.fill('And this');
    await box.press('Enter');
    await page.waitForFunction(() => !document.querySelector<HTMLElement>('#ask-panel .p-chips')?.children.length);
    assert.deepEqual(state.uploaded!.map(u => u.files), [['plan.pdf'], ['notes.txt']]);
    await panel.getByRole('link', {name: 'Talk to the Tico team'}).first().click();
    await page.locator('#support-pop[open]').waitFor();
    assert.equal(await page.evaluate(() => location.hash), '#/tasks', 'the page behind stays');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector<HTMLDialogElement>('#support-pop')?.open);
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

    // A phone: the dot in the middle of the tab bar opens a full page above the tab bar, and closes it.
    const ask = (id: string, who: string, body: string): AskNeed => need(id, 't-' + id, who, body, now);
    state = {messages: [msg('p1', 'human:ben', 'Our key is sk-live1234567890abcdef, board blank', ago(5)), msg('p2', 'bot:assistant', 'Try a reload.', ago(4))],
      sent: [], read: [], decided: [], answers: [], tickets: [], card: null,
      needs: [ask('m8', 'bot:botops', 'Which inbox should the bot answer?'), ask('m7', 'bot:assistant', 'Ship the launch post today?')]};
    ({page, errors} = await open(browser, {width: 390, height: 844}, state));
    const tab = page.locator('#mobile-ask');
    // The Ask tab is the row too, words and all: the dot, or a close mark in its place while the sheet is open.
    const glyph = async () => {
      await page.waitForFunction(() => !document.querySelector('.ldot-fly')
        && !document.querySelector<HTMLElement>('#mobile-ask')!.getAnimations({subtree: true}).some(a => a instanceof CSSTransition));
      return tab.evaluate(el => { const on = (x: Element | null) => getComputedStyle(x!).opacity === '1', x = el.querySelector('.ask-x');
        return on(el.querySelector('.ask-glyph .ldot')) ? 'dot' : on(x) ? x!.textContent : ''; });
    };
    assert.equal(await tab.locator('.ask-label').isVisible(), true, 'not icon-only');
    assert.deepEqual([await tab.getAttribute('aria-label'), await glyph()], [null, 'dot']);
    // The dot sits in the middle of the bar, live, and there is no second, floating Ask button.
    const centre = await page.evaluate(() => { const t = document.querySelector<HTMLElement>('#mobile-ask')!.getBoundingClientRect(); return t.left + t.width / 2; });
    assert.ok(Math.abs(centre - 195) < 24, `the dot is in the middle of the tab bar (${centre})`);
    assert.equal(await page.locator('#ask-row').isVisible(), false, 'no Ask row on a phone');
    assert.equal(await tab.locator('[data-ask-dot]').getAttribute('data-state'), 'idle');
    // No peek above the bar. Tapping the tab opens the chat, the dot flying up to the sheet's header.
    assert.equal(await page.locator('#ask-peek').count(), 0, 'no Ask peek');
    await flight(page, '#ask-panel .ask-head [data-ask-dot]', async () => { await tab.click(); await page.locator('#ask-panel').waitFor(); });
    await page.locator('#ask-panel .conv-run[data-message="p2"]').waitFor();
    assert.equal(await page.locator('#ask-panel [data-ask-q], #ask-panel .ask-card').count(), 0, 'no Needs-you card on a phone either');
    const sheet = (await page.locator('#ask-panel').boundingBox())!, bar = (await page.locator('#mobile-nav').boundingBox())!;
    assert.ok(sheet.x === 0 && sheet.y === 0 && sheet.width === 390 && Math.abs(sheet.y + sheet.height - bar.y) < 2, `a full page above the tab bar (${JSON.stringify([sheet, bar])})`);
    assert.equal(await page.locator('#ask-panel').evaluate(el => getComputedStyle(el).borderTopLeftRadius), '0px', 'square, like a page');
    assert.deepEqual([await tab.getAttribute('aria-label'), await glyph()], ['Close Tico Assistant', 'close']);
    assert.equal(await page.locator('#ask-panel').evaluate(el => [...(el.querySelectorAll('*') as unknown as Element[])].every(x => x.getBoundingClientRect().right <= innerWidth + 1)), true, 'no horizontal scroll');
    assert.equal(await page.locator('#ask-panel [data-ask-loop], #ask-panel [data-ask-requests]').count(), 0, 'no support links on a phone either');
    // Its box is one line, as the bot chat's was: Attach, the words, Send in one row, not the words over the buttons.
    const row = await page.locator('#ask-panel .p-pill').evaluate(el => {
      const mid = (x: string) => { const r = el.querySelector(x)!.getBoundingClientRect(); return r.top + r.height / 2; };
      return {height: el.getBoundingClientRect().height, text: mid('.p-text'), attach: mid('.p-attach'), send: mid('.p-send')};
    });
    assert.ok(row.height < 60 && Math.abs(row.text - row.send) < 4 && Math.abs(row.attach - row.send) < 4, `one line (${JSON.stringify(row)})`);
    // Closing flies the dot back down into the tab bar.
    await flight(page, '#mobile-ask [data-ask-dot]', async () => { await tab.click(); await page.locator('#ask-panel').waitFor({state: 'hidden'}); });
    assert.deepEqual([await tab.getAttribute('aria-label'), await glyph()], [null, 'dot']);
    // A page of its own: tapping another tab, even the one already under it, leaves Ask.
    await tab.click();
    await page.locator('#ask-panel').waitFor();
    await page.locator('#mobile-nav [data-nav="tasks"]').click();
    await page.locator('#ask-panel').waitFor({state: 'hidden'});
    await page.locator('#mobile-nav [data-nav="tasks"]').click();
    assert.equal(await page.locator('#ask-panel').isHidden(), true, 'a tab never opens Ask');
    await tab.click();
    await page.locator('#ask-panel').waitFor();
    await tab.click();
    await page.locator('#ask-panel').waitFor({state: 'hidden'});
    assert.deepEqual(errors, []);
    await page.close();

    // A turn at work with no words yet: its label, how long it has run and its newest step, never the tool's arguments.
    // Once it replies, the reply instead.
    state = {messages: [msg('w1', 'human:ben', 'Help me clear out my task list', ago(5))], sent: [], read: [], decided: [], answers: [], tickets: [], needs: [],
      execution: {job_id: 'j1', message_id: 'w1', bot_id: 'assistant', state: 'running', label: 'Working', text: '', started_at: ago(4.3), parts: [
        {kind: 'tool', text: 'Ran hub hub task list', at: ago(4)}, {kind: 'tool', text: 'Ran hub task bulk close', at: ago(1)}]}};
    ({page, errors} = await open(browser, {width: 1280, height: 900}, state));
    await page.locator('#ask-row').click();
    const working = page.locator('#ask-panel [data-ask-working]');
    await working.waitFor();
    assert.equal(await working.innerText(), 'Working · 4m · Closing tasks');
    // The poll's next draw of the same chat, 5 s on, keeps the thread (and replies typed into it): only the line's time moves.
    state.execution = {...state.execution!, started_at: ago(0.25)};
    await page.evaluate(() => ASK.refresh());
    await page.evaluate(() => { (document.querySelector<HTMLElement>('#ask-panel [data-ask-thread]')!.firstElementChild as HTMLElement).dataset.kept = '1';
      const real = Date.now; Date.now = () => real() + 5000; ASK.draw(); Date.now = real; });
    assert.equal(await page.locator('#ask-panel [data-ask-thread] [data-kept]').count(), 1, 'a redraw of the same chat leaves the thread be');
    assert.match(await working.innerText(), /^Working · 2\ds · Closing tasks$/);
    // A new step on the same chat: the thread stays too, the line names the step.
    state.execution = {...state.execution!, parts: [...state.execution!.parts, {kind: 'tool', text: 'Ran hub task list', at: ago(0)}]};
    await page.evaluate(() => ASK.refresh());
    assert.equal(await page.locator('#ask-panel [data-ask-thread] [data-kept]').count(), 1, 'a new step leaves the thread be');
    assert.match(await working.innerText(), /^Working · \d+s · Reading tasks$/);
    // Two minutes on, a message's "5m ago" reads "7m ago": its time moves in place, the thread stays.
    const stamp = page.locator('#ask-panel [data-ask-thread] [data-ago]').first();
    assert.equal(await stamp.innerText(), '5m ago');
    await page.evaluate(() => { const real = Date.now; Date.now = () => real() + 120000; ASK.draw(); Date.now = real; });
    assert.equal(await page.locator('#ask-panel [data-ask-thread] [data-kept]').count(), 1, 'a message\'s time ticking leaves the thread be');
    assert.equal(await stamp.innerText(), '7m ago');
    state.execution = {...state.execution!, state: 'completed', label: 'Complete', parts: []};
    state.messages.push(msg('w2', 'bot:assistant', 'Closed 12 tasks.'));
    await page.evaluate(() => ASK.refresh());
    await page.locator('#ask-panel').getByText('Closed 12 tasks.').waitFor();
    assert.equal(await working.count(), 0, 'no working line once it replied');
    assert.deepEqual(errors, []);
    await page.close();

  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
