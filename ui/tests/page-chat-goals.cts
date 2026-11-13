// A chat on every page, Release 1 (ui/app/page-chat.ts, spec chat-on-every-page), on Strategy › Goals against the fake
// hub in support/strategy-fake.cts and the threads fake:
//  - AC-1 1440×900: Chat opens the Strategy Owner's chat in the shared column over the right of the window; the goals
//    keep their width; Esc inside the column closes it;
//  - AC-6 the goal last opened shows as a chip above the composer; the sent message carries it and reads
//    "From Goals › <title>"; drawing Goals again (a goal's own address) keeps the chat as it was;
//  - AC-4 ⌘J opens the chat with the cursor in its composer and, pressed again with the focus on the page, closes it;
//    on Threads ⌘J opens Ask;
//  - AC-5 opening Ask closes the chat, and opening the chat closes Ask;
//  - leaving Goals for another Strategy tab closes the chat with its page;
//  - AC-2 1680×1050: the column sits beside the page, which narrows by its width;
//  - AC-11 a 390 px phone: the owner's face opens the chat full screen, as on Listening.
// TICO_UI_SHOTS=<dir> keeps the screenshots (1440 floating, 1680 docked, light and dark).
// Run: node scripts/ui-tests.cts page-chat-goals
import type {Page} from 'playwright';
import type * as StrategyFakeSupport from './support/strategy-fake.cts';
import type * as ThreadsFakeSupport from './support/threads-fake.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {openStrategy, PHONE, shot} = require('./support/strategy-fake.cts') as StrategyFakeSupport.Api;
const {threadsFake} = require('./support/threads-fake.cts') as ThreadsFakeSupport.Api;

const OWNER = 'strategy-owner';
const GOALS = '#/strategy/goals';
type Sent = {text?: string; refs?: {from_page?: unknown}};
declare const S: {route: string; me?: unknown; emps?: unknown[]};
type Box = {x: number; y: number; w: number; h: number; right: number; bottom: number};

const box = (page: Page, sel: string): Promise<Box> => page.locator(sel).first().evaluate(el => {
  const r = el.getBoundingClientRect();
  return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height), right: Math.round(r.right), bottom: Math.round(r.bottom)};
});
const docked = (page: Page) => page.evaluate(() => document.body.classList.contains('pgc-docked'));
const askShown = (page: Page) => page.evaluate(() => !!document.querySelector('#ask-panel')?.classList.contains('ask-shown'));
const chatShown = (page: Page) => page.locator('aside.pgc').isVisible();

let BROWSER: import('playwright').Browser;
// Goals with its owner's chat: opened first on another tab, so the page-owner and threads routes are in place before
// Goals asks for them, then moved to Goals with nothing kept in this browser.
async function openGoals(viewport: {width: number; height: number}, sent: Sent[], theme: 'light' | 'dark' = 'light') {
  const opened = await openStrategy(BROWSER, {viewport, hash: '#/strategy/org'});
  const {page} = opened;
  const answer = threadsFake({bot: OWNER, me: 'human:ana', sent: sent as ThreadsFakeSupport.ThreadsPost[], messages: [
    {id: 'm-1', sender_id: 'bot:' + OWNER, body: 'Two goals have no measure yet.'}]});
  await page.route('**/api/**', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown) => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/api/v2/page-owners') return json({pages: {listening: {bot: null, name: 'Listening'}, goals: {bot: 'bot:' + OWNER, name: 'Goals'}}});
    const out = answer(p, url, method, method === 'GET' ? null : req.postDataJSON?.() ?? null);
    return out === undefined ? route.fallback() : json(out);
  });
  await page.evaluate(t => { localStorage.clear(); localStorage.setItem('tico.theme', t); }, theme);
  await page.reload();
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  await page.evaluate(h => { location.hash = h; }, GOALS);
  await page.locator('#goal-tree .gt-row[data-goal="g-co"]').waitFor();
  await page.locator('.goal-chat [data-pgc-open]').waitFor();
  return opened;
}

(async () => {
  BROWSER = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    {
      // AC-1: 1440×900, the column floats over the right of the page.
      const sent: Sent[] = [];
      const {page, errors} = await openGoals({width: 1440, height: 900}, sent);
      const chat = page.locator('.goal-chat [data-pgc-open]');
      assert.equal(await chat.getAttribute('aria-label'), 'Chat with Strategy Owner');
      assert.equal(await chat.locator('.pgc-word').innerText(), 'Chat');
      const head = await box(page, '.rd-strat-head'), btn = await box(page, '.goal-chat [data-pgc-open]');
      assert.ok(btn.right >= head.right - 4 && btn.y < head.bottom, `Chat at the top right of the head: ${JSON.stringify({btn, head})}`);
      assert.equal(await chatShown(page), false, 'closed until opened');
      const before = await box(page, '#main'), tree = await box(page, '#goal-tree');
      await chat.click();
      await page.locator('aside.pgc .th-msg[data-mid="m-1"]').waitFor();
      assert.equal(await page.locator('aside.pgc .pgc-name').innerText(), 'Strategy Owner');
      const col = await box(page, 'aside.pgc');
      assert.equal(col.w, 340, 'default 340 px');
      assert.equal(col.right, 1440, 'at the right of the window');
      assert.equal(col.y, before.y, `from the top of #main: ${JSON.stringify({col, before})}`);
      assert.equal(col.bottom, 900, 'to the bottom');
      assert.equal(await docked(page), false, 'floats below 1600 px');
      assert.deepEqual(await box(page, '#main'), before, 'the page keeps its width');
      assert.deepEqual(await box(page, '#goal-tree'), tree, 'the goals list keeps its width');
      assert.notEqual(await page.locator('aside.pgc').evaluate(el => getComputedStyle(el).boxShadow), 'none', 'a shadow on its left edge');
      await shot(page, 'page-chat-goals-1440-light');

      // AC-6: the goal last opened rides along as a chip, and the sent message names it.
      assert.equal(await page.locator('aside.pgc .pgc-chip').isVisible(), false, 'nothing picked, no chip');
      await page.locator('#goal-tree .gt-row[data-goal="g-co"] .gt-goal').click();
      await page.locator('#goal-sheet[open]').waitFor();
      await page.locator('#goal-sheet [data-gs-close]').click();
      const chip = page.locator('aside.pgc .pgc-chip');
      await chip.waitFor();
      assert.equal(await chip.innerText(), 'Reach 100 paying customers');
      const chipBox = await box(page, 'aside.pgc .pgc-chip'), composer = await box(page, 'aside.pgc .thv-composer');
      assert.ok(chipBox.bottom <= composer.y + 1, `the chip sits above the composer: ${JSON.stringify({chipBox, composer})}`);
      assert.equal(await chip.evaluate(el => getComputedStyle(el).fontSize), '12px');
      await page.locator('aside.pgc .p-text').fill('Add a measure to this one');
      await page.locator('aside.pgc .p-text').press('Enter');
      await page.locator('aside.pgc .th-from').waitFor();
      assert.deepEqual(sent.at(-1)?.refs?.from_page, {page: 'goals', label: 'Goals', item: {kind: 'goal', id: 'g-co', name: 'Reach 100 paying customers'}});
      assert.equal(await page.locator('aside.pgc .th-from').innerText(), 'From Goals › Reach 100 paying customers');
      assert.equal(await page.locator('aside.pgc .th-from a').getAttribute('href'), GOALS + '/g-co');

      // A goal's own address draws Goals again: the chat stays as it was, and the chip follows the goal opened.
      await page.locator('aside.pgc .thv').evaluate(el => { (el as HTMLElement & {_kept?: number})._kept = 1; });
      await page.evaluate(h => { location.hash = h; }, GOALS + '/g-sales');
      await page.locator('#goal-panel[open]').waitFor();
      await page.locator('#goal-panel [data-gp-close]').click();
      await page.waitForFunction(() => !document.querySelector('#goal-panel[open]'));
      assert.equal(await page.locator('aside.pgc .thv').evaluate(el => (el as HTMLElement & {_kept?: number})._kept), 1, 'the same chat, not a new one');
      assert.equal(await chip.innerText(), 'Book 40 demos a month');
      // An owner's line opens their panel: the goal it opens becomes the chip.
      await page.locator('#goal-tree .gt-row[data-goal="g-co"] .gt-name').click();
      await page.locator('#goal-panel[open]').waitFor();
      await page.locator('#goal-panel [data-gp-close]').click();
      await page.waitForFunction(() => !document.querySelector('#goal-panel[open]'));
      assert.equal(await chip.innerText(), 'Reach 100 paying customers', 'the goal opened from an owner\'s line');
      // ⌘J with a goal's window open: nothing changes behind it, and Ask stays closed.
      await page.locator('#goal-tree .gt-row[data-goal="g-sales"] .gt-goal').click();
      await page.locator('#goal-sheet[open]').waitFor();
      await page.keyboard.press('ControlOrMeta+j');
      assert.equal(await chatShown(page), true, '⌘J under a window leaves the chat as it was');
      assert.equal(await askShown(page), false, '⌘J under a window opens no Ask');
      assert.equal(await page.evaluate(() => document.activeElement?.closest('#goal-sheet') != null), true, 'the cursor stays in the window');
      await page.locator('#goal-sheet [data-gs-close]').click();
      // A banner that comes while the chat is open: the column still starts where #main starts.
      // The demo strip (html.strip, base.css) is such a banner.
      await page.evaluate(() => document.documentElement.classList.add('strip', 'demo'));
      await page.waitForFunction(() => Math.round(document.querySelector('aside.pgc')!.getBoundingClientRect().top) === Math.round(document.getElementById('main')!.getBoundingClientRect().top), null, {timeout: 3000});
      await page.evaluate(() => document.documentElement.classList.remove('strip', 'demo'));
      await page.waitForFunction(() => Math.round(document.querySelector('aside.pgc')!.getBoundingClientRect().top) === Math.round(document.getElementById('main')!.getBoundingClientRect().top), null, {timeout: 3000});

      // Esc inside the column closes it.
      await page.locator('aside.pgc .p-text').focus();
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => document.querySelector<HTMLElement>('aside.pgc')!.hidden);
      assert.equal(await chat.getAttribute('aria-pressed'), 'false');

      // AC-4: ⌘J opens it with the cursor in the composer; pressed again with the focus on the page, it closes.
      await page.locator('#goal-search').focus();
      await page.keyboard.press('ControlOrMeta+j');
      await page.waitForFunction(() => document.activeElement?.matches('aside.pgc .thv-composer .p-text'));
      assert.equal(await chatShown(page), true);
      assert.equal(await askShown(page), false, '⌘J on Goals is its chat, not Ask');
      await page.locator('#goal-search').focus();
      await page.keyboard.press('ControlOrMeta+j');
      await page.waitForFunction(() => document.querySelector<HTMLElement>('aside.pgc')!.hidden);
      assert.equal(await askShown(page), false);

      // AC-5: Ask and the page's chat are never open together.
      await chat.click();
      await page.locator('aside.pgc .pgc-name').waitFor();
      await page.locator('#ask-row').click();
      await page.waitForFunction(() => document.querySelector('#ask-panel')?.classList.contains('ask-shown'));
      assert.equal(await chatShown(page), false, 'opening Ask closes the chat');
      await chat.click();
      await page.locator('aside.pgc .pgc-name').waitFor();
      await page.waitForFunction(() => !document.querySelector('#ask-panel')?.classList.contains('ask-shown'));
      assert.equal(await chatShown(page), true, 'opening the chat closes Ask');

      // Another Strategy tab is another page: the chat closes with Goals.
      await page.locator('.strat-tabs a[data-strat-tab="org"]').click();
      await page.waitForFunction(() => location.hash === '#/strategy/org');
      await page.waitForFunction(() => !document.querySelector('.goal-chat'));
      assert.equal(await chatShown(page), false, 'no chat on a page without an owner');
      assert.equal(await page.evaluate(() => document.body.classList.contains('pgc-open')), false);

      // AC-4: on Threads, ⌘J opens Ask.
      await page.evaluate(() => { location.hash = '#/threads'; });
      await page.waitForFunction(() => S.route === '#/threads');
      await page.locator('body').click({position: {x: 700, y: 400}});
      await page.keyboard.press('ControlOrMeta+j');
      await page.waitForFunction(() => document.querySelector('#ask-panel')?.classList.contains('ask-shown'));
      assert.equal(await chatShown(page), false);
      assert.deepEqual(errors, []);
      await page.close();
    }
    for (const theme of ['light', 'dark'] as const) {
      // AC-2: 1680×1050, the column sits beside the page, which narrows by its width. Open stays kept per page.
      const {page, errors} = await openGoals({width: 1680, height: 1050}, [], theme);
      const before = await box(page, '#main');
      await page.locator('.goal-chat [data-pgc-open]').click();
      await page.locator('aside.pgc .th-msg[data-mid="m-1"]').waitFor();
      assert.equal(await docked(page), true, 'docks from 1600 px');
      const col = await box(page, 'aside.pgc'), main = await box(page, '#main');
      assert.equal(col.x, 1680 - 340);
      assert.equal(main.right, before.right - 340, `the page narrows by the column's width: ${JSON.stringify({before, main})}`);
      assert.ok(main.right <= col.x, 'nothing of the page under the column');
      await shot(page, `page-chat-goals-1680-${theme}`);
      if (theme === 'light') {
        await page.reload();
        await page.locator('aside.pgc .pgc-name').waitFor();
        assert.equal(await docked(page), true, 'reloaded, it opens as it was');
        await page.setViewportSize({width: 1440, height: 900});
        await page.waitForFunction(() => !document.body.classList.contains('pgc-docked'));
        assert.equal((await box(page, '#main')).right, 1440, 'narrower than 1600 px, it floats again');
      } else {
        await page.setViewportSize({width: 1440, height: 900});
        await page.waitForFunction(() => !document.body.classList.contains('pgc-docked'));
        await shot(page, 'page-chat-goals-1440-dark');
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // AC-11: a phone, as today: the owner's face opens the chat full screen; ‹ returns to Goals.
      const {page, errors} = await openGoals(PHONE, []);
      const face = page.locator('.goal-chat [data-pgc-open]');
      assert.equal(await face.locator('.pgc-word').isVisible(), false, 'the owner\'s face only');
      await face.click();
      await page.locator('aside.pgc.pgc-full .th-msg[data-mid="m-1"]').waitFor();
      const full = await box(page, 'aside.pgc');
      assert.deepEqual([full.x, full.y, full.w, full.h], [0, 0, PHONE.width, PHONE.height], 'full screen');
      assert.equal(await page.locator('#mobile-nav').isVisible(), false);
      await page.locator('aside.pgc [data-pgc-close]').click();
      await page.waitForFunction(() => document.querySelector<HTMLElement>('aside.pgc')!.hidden);
      assert.equal(await page.evaluate(() => location.hash), GOALS);
      assert.equal(await page.locator('#mobile-nav').isVisible(), true);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('page-chat-goals: ok');
  } finally {
    await BROWSER.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
