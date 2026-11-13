import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
// The Assistant's row in the Team list, and its unread count, open the person's own Assistant page (#/assistant), where
// its conversation is: the bot page at #/bot/assistant has no Home or DM for it (ui/app/bot-page.ts botTabs). That bot
// page opened directly goes on to #/assistant too, its settings tabs stay, and opening the Assistant reads the chat, so
// the count goes. Desktop 1280 and phone 390. Synthetic fixtures only.
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;

type State = {unread: number; readAll: string[]};
const now = new Date().toISOString();
const say = (id: string, from: string, body: string) => ({id, sender_id: from, body, refs: {}, created_at: now, kind: 'say'});
const chat = [say('m1', 'human:ana', 'What is waiting on me?'), say('m2', 'bot:assistant', 'Three tasks wait on you.')];

async function open(browser: import('playwright').Browser, viewport: {width: number; height: number}, state: State) {
  const phone = viewport.width < 760;
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: phone, isMobile: phone, reducedMotion: 'reduce'});
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
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true, registered: true});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], org_groups: [{id: 'built-in', name: 'General', my_access: {see: true, read: true, write: true}}]});
    if (p === '/api/employees') return json([{name: 'assistant', display_name: 'Assistant', status: 'active', state: 'active', can_chat: true, schedules: [],
      org_parent: 'p:ana', my_access: {see: true, read: true, write: true}}]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/tasks') return json({tasks: [], next_offset: null, cursor: 'c1'});
    // The person's chat with the Assistant is a 1:1 DM: its unread is counted on the Assistant in Team (`bot_dms`).
    if (p === '/api/v2/threads' && method === 'GET') return json({attention: [], threads: [], dms: [], bot_dms: state.unread ? {'bot:assistant': state.unread} : {}, next_cursor: null});
    if (p === '/api/v2/threads/room1/read-all' && method === 'POST') { state.readAll.push(p); state.unread = 0; return json({thread_id: 'room1'}); }
    if (p === '/api/v2/assistant' && method === 'GET') return json({available: true, state: 'active', bot: 'assistant', name: 'Assistant', can_turn_on: false,
      room_id: 'room1', messages: chat, has_more: false, next_before: null, execution: null, actions: {}, pending: []});
    if (p === '/api/v2/conversations/room1/snapshot') return json({messages: chat, execution: null, has_more: false, next_before: null});
    if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  return {page, errors};
}

let browser: import('playwright').Browser | null = null;
(async () => {
  browser = await chromium.launch();
  for (const viewport of [{width: 1280, height: 900}, {width: 390, height: 844}]) {
    const label = viewport.width < 760 ? 'phone' : 'desktop';
    const state: State = {unread: 19, readAll: []};
    const {page, errors} = await open(browser, viewport, state);
    await page.goto('https://tico-ui.test/#/tasks');
    if (label === 'phone') await page.locator('#mobile-more').click();
    const row = page.locator('#tree a.node[data-org="b:assistant"]'), badge = row.locator('.tree-dm');
    await badge.waitFor({state: 'visible'});
    assert.equal(await badge.textContent(), '19', `${label}: the unread count is on the Assistant's row`);
    assert.equal(await row.getAttribute('href'), '#/assistant', `${label}: the Assistant's row opens the Assistant page`);
    // The count itself is what the person taps.
    await badge.click();
    await page.waitForURL('**/#/assistant');
    await page.locator('#conv-thread').getByText('Three tasks wait on you.').waitFor();
    // Reading it is opening it: the chat is marked read on the server and the count leaves the row.
    await page.waitForFunction(() => !document.querySelector('#tree a.node[data-org="b:assistant"] .tree-dm'));
    assert.deepEqual(state.readAll, ['/api/v2/threads/room1/read-all'], `${label}: the Assistant chat is read once`);
    assert.equal(await row.getAttribute('aria-current'), 'page', `${label}: the row is current on the Assistant page`);

    // The bot page opened directly, or its Home or DM tab, goes on to the Assistant page; its settings tabs stay.
    for (const from of ['#/bot/assistant', '#/bot/assistant/dm', '#/bot/assistant/home']) {
      await page.goto('https://tico-ui.test/#/tasks');
      await page.locator('#tree').waitFor({state: 'attached'});
      await page.evaluate(to => { location.hash = to; }, from);
      await page.waitForURL('**/#/assistant');
      await page.locator('#conv-thread').getByText('Three tasks wait on you.').waitFor();
    }
    await page.evaluate(() => { location.hash = '#/bot/assistant/settings'; });
    await page.locator('.bot-ident').waitFor();
    assert.match(page.url(), /#\/bot\/assistant\/settings$/, `${label}: the Assistant's settings tab is still its bot page`);
    assert.deepEqual(errors, [], `${label}: no page errors`);
    await page.close();
  }
  console.log('PASS assistant sidebar: row and unread count open #/assistant, bot page goes there, opening reads it (1280, 390)');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => browser?.close());
