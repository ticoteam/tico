// Your messages on a chat's edge (ui/app/chat-index.ts; the style guide's chat window, part 19), offline:
//  - the Assistant (#/assistant): no index with two of your messages, a tick each from the third; only yours; a
//    message of files only says what it carried; the strip is a "Your messages" landmark that keeps clear of the words;
//  - pointing grows the tick and its neighbours and shows the message, two lines then the time; clicking scrolls the
//    chat back to it and marks it; the arrow keys move between ticks;
//  - a message landing while the pointer is on the strip waits until it leaves; hundreds of messages share ticks;
//  - a phone has none;
//  - a page's chat (Listening's owner, in the 340 px column): the same ticks, built from the thread's loaded messages.
// Run: node scripts/ui-tests.cts chat-index
import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
import type * as AssistantChat from './support/assistant-chat.cts';
import type * as AutomationsFakeSupport from './support/automations-fake.cts';
import type * as ThreadsFakeSupport from './support/threads-fake.cts';
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;
const {assistantView} = require('./support/assistant-chat.cts') as AssistantChat.Api;
const {openAutomations} = require('./support/automations-fake.cts') as AutomationsFakeSupport.Api;
const {threadsFake} = require('./support/threads-fake.cts') as ThreadsFakeSupport.Api;
type Browser = import('playwright').Browser;
type Page = import('playwright').Page;
interface Msg {id: string; sender_id: string; body: string; created_at: string; refs?: Record<string, unknown>}
declare const V2C: {rendered?: boolean; loaded?: boolean; messages: Msg[]; stick: {atBottom: boolean}};
declare function v2ChatRender(state: typeof V2C): void;

const at = (n: number) => new Date(Date.now() - (400 - n) * 60000).toISOString();
const NAV = 'nav.pidx:not([hidden])';

async function assistant(browser: Browser, messages: Msg[], width = 1280) {
  const page = await browser.newPage({viewport: {width, height: 800}, serviceWorkers: 'block'});
  const errors: string[] = [];
  page.on('pageerror', (e: Error) => errors.push(e.message));
  const snapshot = {messages, execution: null};
  await page.context().route('**/*', route => {
    const p = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui) {
      const file = uiFile(ui[1]!);
      if (fs.existsSync(file)) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')});
    }
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(path.join(__dirname, '../vendor/marked.min.js'), 'utf8')});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
    if (p === '/api/employees') return json([{name: 'assistant', display_name: 'Assistant', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
    if (p === '/api/issues') return json([]);
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    if (p === '/api/v2/assistant') return json(assistantView('c-idx', snapshot));
    if (p === '/api/v2/conversations/c-idx/snapshot') return json(snapshot);
    if (p === '/api/v2/support/tickets') return json({enabled: false});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/assistant');
  await page.waitForFunction(() => V2C?.rendered && V2C.loaded);
  return {page, errors};
}
// One of yours, then the Assistant's long answer, n times: enough to scroll.
const talk = (n: number, from = 0): Msg[] => Array.from({length: n}, (_, i) => [
  {id: 'h' + (from + i), sender_id: 'human:ana', body: `Question number ${from + i}: what is waiting on me today, and what can wait until Monday?`, created_at: at(2 * (from + i))},
  {id: 'b' + (from + i), sender_id: 'bot:assistant', body: 'An answer.\n\n' + 'Here is a longer line of the answer that takes some room in the chat. '.repeat(4), created_at: at(2 * (from + i) + 1)}]).flat();
const ticks = (page: Page) => page.locator(`${NAV} .pidx-t`);
const add = (page: Page, m: Msg) => page.evaluate(m => { V2C.messages.push(m); v2ChatRender(V2C); }, m);

async function shows(browser: Browser) {
  // Two of yours (and a bot's): no index.
  const {page, errors} = await assistant(browser, talk(2));
  await page.waitForTimeout(150);
  assert.equal(await page.locator(NAV).count(), 0, 'two of your messages: no index');
  // The third shows it: one tick per message you sent, only yours.
  await add(page, {id: 'h2', sender_id: 'human:ana', body: 'Send the budget review with the notes.', created_at: at(10)});
  await page.locator(NAV).waitFor();
  assert.equal(await ticks(page).count(), 3, 'three of yours, three ticks; the bot\'s are not indexed');
  assert.equal(await page.getByRole('navigation', {name: 'Your messages'}).count(), 1, 'a landmark');
  // Inset from the top and bottom, on the right edge, clear of the words and of the scrollbar.
  const geo = await page.evaluate(() => {
    const nav = document.querySelector<HTMLElement>('nav.pidx')!.getBoundingClientRect(), feed = document.querySelector<HTMLElement>('#conv-thread')!;
    const f = feed.getBoundingClientRect(), bar = feed.offsetWidth - feed.clientWidth;
    const words = [...document.querySelectorAll('#conv-thread .md, #conv-thread .th-head')].flatMap(el => [...(el.getClientRects() as unknown as DOMRect[])]);
    return {inset: nav.top >= f.top + 20 && nav.bottom <= f.bottom - 20, edge: f.right - bar - nav.right >= 0 && f.right - bar - nav.right < 8,
      clear: words.every(r => r.right <= nav.left + 1), narrow: nav.width <= 24};
  });
  assert.deepEqual(geo, {inset: true, edge: true, clear: true, narrow: true}, 'on the edge, inset, clear of the words');
  // A message of files only says what it carried.
  await add(page, {id: 'h3', sender_id: 'human:ana', body: '', created_at: at(11), refs: {attachments: [{id: 'f1', name: 'shot.png', content_type: 'image/png'}]}});
  await page.waitForFunction(() => document.querySelectorAll('nav.pidx .pidx-t').length === 4);
  assert.match(String(await ticks(page).last().getAttribute('aria-label')), /^Photo, (Today|Yesterday), \d/);
  // A long message shows in full: nothing is cut below its last line, and there is no Show more.
  await add(page, {id: 'long', sender_id: 'bot:assistant', body: 'The two items are the release notes and the budget review. '.repeat(40), created_at: at(12)});
  const long = page.locator('[data-message="long"]'), body = long.locator('.bubble > .md');
  await body.waitFor();
  const lines = await body.evaluate(el => Math.round(el.clientHeight / parseFloat(getComputedStyle(el).lineHeight)));
  assert.ok(lines > 10, 'a long message runs past ten lines');
  assert.equal(await body.evaluate(el => el.scrollHeight <= el.clientHeight + 2), true, 'nothing of a long message is cut off');
  assert.equal(await long.getByRole('button', {name: 'Show more'}).count(), 0, 'a long message has no Show more');
  assert.deepEqual(errors, []);
  await page.close();
  console.log('chat index shows from three of yours: ok');
}

async function pointAndGo(browser: Browser) {
  const {page, errors} = await assistant(browser, talk(12));
  await page.locator(NAV).waitFor();
  assert.equal(await ticks(page).count(), 12);
  // Read at the end: the ticks for what is on screen are brighter, the oldest are not.
  assert.equal(await ticks(page).last().evaluate(el => el.classList.contains('on')), true, 'on screen: brighter');
  assert.equal(await ticks(page).first().evaluate(el => el.classList.contains('on')), false, 'off screen: faint');
  // Pointing grows the tick and its neighbours, a dock; the message shows beside it, its words then its time.
  await ticks(page).nth(5).hover();
  const tip = page.locator(`${NAV} .pidx-tip`);
  await tip.waitFor();
  assert.equal(await tip.locator('.pidx-tip-t').innerText(), 'Question number 5: what is waiting on me today, and what can wait until Monday?');
  assert.match(await tip.locator('time').innerText(), /^(Today|Yesterday), \d{1,2}:\d{2}\s?[AP]M$/);
  // The growth is a short transition: read the widths once the pointed tick has reached its full size.
  await page.waitForFunction(sel => parseFloat(getComputedStyle(document.querySelectorAll(sel)[5]!, '::before').width) >= 18, `${NAV} .pidx-t`);
  const widths = await ticks(page).evaluateAll(els => els.map(el => parseFloat(getComputedStyle(el, '::before').width)));
  assert.ok(widths[5]! > widths[4]! && widths[4]! > widths[3]! && widths[3]! > widths[2]! && widths[2] === widths[0], 'grows like a dock: ' + widths);
  assert.equal(widths[4], widths[6]);
  const tipBox = (await tip.boundingBox())!, navBox = (await page.locator(NAV).boundingBox())!;
  assert.ok(tipBox.x + tipBox.width <= navBox.x, 'the message shows beside the ticks, never over them');
  // A message landing while the pointer is on the strip waits until it leaves: the tick under it does not move.
  await add(page, {id: 'h99', sender_id: 'human:ana', body: 'One more.', created_at: at(300)});
  await page.waitForTimeout(150);
  assert.equal(await ticks(page).count(), 12, 'no redraw under the pointer');
  // Clicking brings the message back and marks it.
  await ticks(page).nth(2).click();
  await page.waitForFunction(() => { const t = document.querySelector<HTMLElement>('#conv-thread')!.getBoundingClientRect(), r = document.querySelector<HTMLElement>('[data-message="h2"]')!.getBoundingClientRect();
    return r.top >= t.top - 1 && r.bottom <= t.bottom + 1; });
  await page.locator('#conv-thread .jump-mark[data-message="h2"]').waitFor();
  assert.equal(await page.evaluate(() => V2C.stick.atBottom), false, 'a jump stops following the end');
  await page.mouse.move(5, 400);
  await page.waitForFunction(() => document.querySelectorAll('nav.pidx .pidx-t').length === 13);
  // The keyboard: one tick in the tab order, arrows move between them, Enter goes.
  assert.equal(await ticks(page).evaluateAll(els => els.filter(el => (el as HTMLElement).tabIndex === 0).length), 1);
  await ticks(page).nth(4).focus();
  await page.keyboard.press('ArrowDown');
  assert.match(await page.evaluate(() => document.activeElement?.getAttribute('aria-label') || ''), /^Question number 5:/);
  await page.keyboard.press('Home');
  assert.match(await page.evaluate(() => document.activeElement?.getAttribute('aria-label') || ''), /^Question number 0:/);
  await page.keyboard.press('Enter');
  await page.locator('#conv-thread .jump-mark[data-message="h0"]').waitFor();
  // Reduced motion: nothing slides.
  await page.emulateMedia({reducedMotion: 'reduce'});
  assert.ok(parseFloat(await ticks(page).first().evaluate(el => getComputedStyle(el, '::before').transitionDuration)) < 0.01, 'no motion');
  // A phone has none; its Outline serves it.
  await page.setViewportSize({width: 390, height: 844});
  await page.waitForFunction(() => !document.querySelector('nav.pidx:not([hidden])'));
  assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('#conv-thread')!).paddingRight), '4px', 'the feed gets its own padding back');
  assert.deepEqual(errors, []);
  await page.close();
  console.log('chat index points, goes back, keys, waits under the pointer: ok');
}

async function many(browser: Browser) {
  const {page, errors} = await assistant(browser, talk(300));
  await page.locator(NAV).waitFor();
  const n = await ticks(page).count(), h = (await page.locator(NAV).boundingBox())!.height;
  assert.ok(n < 300 && n <= Math.floor(h / 8) + 1 && n > 20, `hundreds share ticks that fit (${n} in ${h}px)`);
  assert.match(String(await ticks(page).first().getAttribute('aria-label')), /^Question number 0:/);
  assert.deepEqual(errors, []);
  await page.close();
  console.log('chat index stays calm with hundreds: ok');
}

// A page's chat: Listening's owner in the 340 px column, from the thread's own messages.
async function pageChat(browser: Browser) {
  const OWNER = 'automations-owner';
  const rows = Array.from({length: 4}, (_, i) => [
    {id: 'q' + i, sender_id: 'human:dana', body: `Why did listening ${i} stop?`, created_at: at(i * 2)},
    {id: 'a' + i, sender_id: 'bot:' + OWNER, body: 'Reddit rate-limited it. '.repeat(30), created_at: at(i * 2 + 1)}]).flat();
  const answer = threadsFake({bot: OWNER, me: 'human:dana', messages: rows});
  const {page, errors} = await openAutomations(browser, {viewport: {width: 1440, height: 900}, hash: '#/listening',
    tweak: fx => { (fx.employees as Record<string, string>[]).push({name: OWNER, display_name: 'Automations Owner', host: 'keeper', status: 'active'}); },
    routes: async p => {
      await p.route('**/api/**', async route => {
        const req = route.request(), url = new URL(req.url()), path = url.pathname, method = req.method();
        const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
        if (path === '/api/v2/page-owners') return json({pages: {listening: {bot: 'bot:' + OWNER, name: 'Listening'}}});
        const out = answer(path, url, method, method === 'GET' ? null : req.postDataJSON?.() ?? null);
        return out === undefined ? route.fallback() : json(out);
      });
    }});
  // (1440: the page chat is docked open at first, page-chat.ts)
  await page.locator('aside.pgc .th-msg[data-mid="a3"]').waitFor();
  const nav = page.locator('aside.pgc nav.pidx:not([hidden])');
  await nav.waitFor();
  assert.equal(await nav.locator('.pidx-t').count(), 4, 'one tick per message you sent');
  assert.match(String(await nav.locator('.pidx-t').first().getAttribute('aria-label')), /^Why did listening 0 stop\?, /);
  await nav.locator('.pidx-t').first().click();
  await page.locator('aside.pgc .th-flash[data-mid="q0"]').waitFor();
  const fits = await page.evaluate(() => { const n = document.querySelector('aside.pgc nav.pidx')!.getBoundingClientRect(), a = document.querySelector('aside.pgc')!.getBoundingClientRect();
    return n.right <= a.right && n.left >= a.left; });
  assert.equal(fits, true, 'inside the column');
  assert.deepEqual(errors, []);
  await page.close();
  console.log('chat index in a page\'s chat column: ok');
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined, headless: true} as import('playwright').LaunchOptions);
  try {
    await shows(browser);
    await pointAndGo(browser);
    await many(browser);
    await pageChat(browser);
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
