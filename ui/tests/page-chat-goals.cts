// A chat on every page, Release 1 (ui/app/page-chat.ts, spec chat-on-every-page), on Strategy › Goals against the fake
// hub in support/strategy-fake.cts and the threads fake:
//  - AC-1 1150×900, too narrow to dock: no chat button in the header (the router mounts the chat, page-chat.ts); the tab on the right edge
//    opens the Strategy Owner's chat in the shared column over the right of the window; the goals keep their width;
//    Esc inside the column closes it;
//  - AC-6 the goal last opened shows as a chip above the composer; the sent message carries it and reads
//    "From Goals › <title>"; drawing Goals again (a goal's own address) keeps the chat as it was;
//  - AC-4 ⌘J opens the chat with the cursor in its composer and, pressed again with the focus on the page, closes it;
//    on Threads ⌘J opens Ask;
//  - the Assistant opens in the sidebar beside the open chat: both at once, at 1150 (floating) and 1440 (docked),
//    nothing overlapping;
//  - leaving Goals for another Strategy tab with no owner closes the chat with its page; where Strategy's owner is
//    Goals' too, the chat stays open and mounted across the two, and back;
//  - AC-2 1440×900 (and 1680×1050): the column is open at first and sits beside the page, which narrows by its width;
//    closed, it folds to the tab; too narrow to dock (1150) it folds to the tab again;
//  - AC-11 a 390 px phone: the owner's face, the header's chat mark, opens the chat full screen, as on Listening.
// TICO_UI_SHOTS=<dir> keeps the screenshots (1150 floating, 1440 docked, light and dark).
// Run: node scripts/ui-tests.cts page-chat-goals
import type {Page} from 'playwright';
import type * as StrategyFakeSupport from './support/strategy-fake.cts';
import type * as ThreadsFakeSupport from './support/threads-fake.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {openStrategy, PHONE, shot} = require('./support/strategy-fake.cts') as StrategyFakeSupport.Api;
const {threadsFake} = require('./support/threads-fake.cts') as ThreadsFakeSupport.Api;

const OWNER = 'strategy-owner';
const TAB = 'body > .pgc-edge:not([hidden]) .pgc-tab', MARK = '.rd-strat-head > .ph-chat > [data-pgc-open]';
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
async function openGoals(viewport: {width: number; height: number}, sent: Sent[], theme: 'light' | 'dark' = 'light', strategy: string | null = null) {
  const opened = await openStrategy(BROWSER, {viewport, hash: '#/strategy/org'});
  const {page} = opened;
  const answer = threadsFake({bot: OWNER, me: 'human:ana', sent: sent as ThreadsFakeSupport.ThreadsPost[], messages: [
    {id: 'm-1', sender_id: 'bot:' + OWNER, body: 'Two goals have no measure yet.'}]});
  await page.route('**/api/**', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown) => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/api/v2/page-owners') return json({pages: {listening: {bot: null, name: 'Listening'}, goals: {bot: 'bot:' + OWNER, name: 'Goals'},
      ...(strategy ? {strategy: {bot: strategy, name: 'Strategy'}} : {})}});
    const out = answer(p, url, method, method === 'GET' ? null : req.postDataJSON?.() ?? null);
    return out === undefined ? route.fallback() : json(out);
  });
  await page.evaluate(t => { localStorage.clear(); localStorage.setItem('tico.theme', t); }, theme);
  await page.reload();
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  await page.evaluate(h => { location.hash = h; }, GOALS);
  await page.locator('#goal-tree .gt-row[data-goal="g-co"]').waitFor();
  await page.locator(viewport.width <= 760 ? MARK : TAB).or(page.locator('aside.pgc .pgc-name')).first().waitFor();
  return opened;
}

(async () => {
  BROWSER = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    {
      // AC-1: 1150×900, too narrow to dock: the column floats over the right of the page.
      const sent: Sent[] = [];
      const {page, errors} = await openGoals({width: 1150, height: 900}, sent);
      const chat = page.locator(TAB);
      assert.equal(await chat.getAttribute('aria-label'), 'Chat with Strategy Owner');
      // No chat button in the header: "New goal", then ⋯, last; the tab sits beyond the page, on the right edge.
      const head = await box(page, '.rd-strat-head'), btn = await box(page, TAB), go = await box(page, '#goal-new');
      const more = await box(page, '.rd-strat-head > .ph-more');
      assert.equal(await page.locator('.rd-strat-head [data-pgc-open]:visible, .goal-chat').count(), 0, 'no chat button in the header');
      assert.ok(go.right <= more.x && more.right >= head.right - 4 && head.right < btn.x,
        `New goal, ⋯ last, the tab past the header: ${JSON.stringify({btn, go, more, head})}`);
      assert.equal(await chatShown(page), false, 'too narrow to dock: folded to its tab until opened');
      const before = await box(page, '#main'), tree = await box(page, '#goal-tree');
      await chat.click();
      await page.locator('aside.pgc .th-msg[data-mid="m-1"]').waitFor();
      assert.equal(await page.locator('aside.pgc .pgc-name').innerText(), 'Strategy Owner');
      const col = await box(page, 'aside.pgc');
      assert.equal(col.w, 340, 'default 340 px');
      assert.equal(col.right, 1150, 'at the right of the window');
      assert.equal(col.y, before.y, `from the top of #main: ${JSON.stringify({col, before})}`);
      assert.equal(col.bottom, 900, 'to the bottom');
      assert.equal(await docked(page), false, 'floats where it does not dock');
      assert.deepEqual(await box(page, '#main'), before, 'the page keeps its width');
      assert.deepEqual(await box(page, '#goal-tree'), tree, 'the goals list keeps its width');
      assert.notEqual(await page.locator('aside.pgc').evaluate(el => getComputedStyle(el).boxShadow), 'none', 'a shadow on its left edge');
      await shot(page, 'page-chat-goals-1150-light');

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
      // (at 1150 the floating column lies over this row's right end, where the goal's words are)
      await page.locator('#goal-tree .gt-row[data-goal="g-sales"] .gt-goal').dispatchEvent('click');
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
      assert.equal(await chat.getAttribute('aria-expanded'), 'false');
      assert.equal(await page.evaluate(() => document.activeElement?.matches('.pgc-tab')), true, 'the focus is back on the tab');

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

      // The Assistant opens in the sidebar and the page's chat stays on the right: two bots, two places, both open at
      // once and nothing overlapping, floating at 1150 and docked at 1440 (widened past the docking width, the column
      // docks open). Closing one leaves the other.
      await chat.click();
      await page.locator('aside.pgc .pgc-name').waitFor();
      await page.locator('#ask-row').click();
      await page.waitForFunction(() => document.querySelector('#ask-panel')?.classList.contains('ask-shown'));
      assert.equal(await chatShown(page), true, 'opening Ask leaves the chat open');
      for (const width of [1150, 1440]) {
        await page.setViewportSize({width, height: 900});
        await page.waitForFunction(() => document.querySelector('#ask-panel')!.getAnimations().length === 0);
        const ask = await box(page, '#ask-panel'), side = await box(page, '#side'), main = await box(page, '#main'), col = await box(page, 'aside.pgc');
        assert.ok(ask.x >= side.x && ask.right <= side.right + 1, `${width}: the Assistant is inside the sidebar: ${JSON.stringify({ask, side})}`);
        assert.ok(ask.right <= main.x + 1, `${width}: it never covers the page: ${JSON.stringify({ask, main})}`);
        assert.ok(ask.right <= col.x, `${width}: nor the page's chat: ${JSON.stringify({ask, col})}`);
        assert.ok(main.w >= 480, `${width}: the page keeps room: ${JSON.stringify(main)}`);
      }
      await page.setViewportSize({width: 1440, height: 900});
      await page.locator('#ask-row').click();
      await page.waitForFunction(() => !document.querySelector('#ask-panel')?.classList.contains('ask-shown'));
      assert.equal(await chatShown(page), true, 'closing Ask leaves the chat');
      await page.locator('#ask-row').click();
      await page.waitForFunction(() => document.querySelector('#ask-panel')?.classList.contains('ask-shown'));
      await page.locator('aside.pgc [data-pgc-close]').click();
      await page.waitForFunction(() => document.querySelector<HTMLElement>('aside.pgc')!.hidden);
      assert.equal(await askShown(page), true, 'closing the chat leaves Ask');
      await page.locator('#ask-row').click();
      await page.waitForFunction(() => !document.querySelector('#ask-panel')?.classList.contains('ask-shown'));

      // Another Strategy tab is another page: the chat closes with Goals.
      await page.locator('.strat-tabs a[data-strat-tab="org"]').click();
      await page.waitForFunction(() => location.hash === '#/strategy/org');
      await page.waitForFunction(() => !document.querySelector('#goal-tree'));
      assert.equal(await chatShown(page), false, 'no chat on a page without an owner');
      assert.equal(await page.evaluate(() => document.body.classList.contains('pgc-open')), false);
      assert.equal(await page.locator(TAB).count(), 0, 'and no tab');

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
      // AC-2: 1440×900, the column sits beside the page, which narrows by its width. Open stays kept per page.
      const W = theme === 'light' ? 1440 : 1680;
      const {page, errors} = await openGoals({width: W, height: theme === 'light' ? 900 : 1050}, [], theme);
      // Open at first where it docks.
      await page.locator('aside.pgc .th-msg[data-mid="m-1"]').waitFor();
      assert.equal(await docked(page), true, `docks at ${W} px, open at first`);
      assert.equal(await page.locator(TAB).count(), 0, 'no tab while docked open');
      const col = await box(page, 'aside.pgc'), main = await box(page, '#main');
      assert.equal(col.x, W - 340);
      assert.equal(main.right, W - 340, `the page narrows by the column's width: ${JSON.stringify({col, main})}`);
      await shot(page, `page-chat-goals-${W}-${theme}`);
      if (theme === 'light') {
        // Closed, it folds to its tab, and the page takes the room back but the tab's.
        await page.locator('aside.pgc [data-pgc-close]').click();
        await page.locator(TAB).waitFor();
        const edge = await box(page, 'body > .pgc-edge');
        assert.equal((await box(page, '#main')).right, edge.x, 'the page stops at the tab\'s gutter');
        await page.locator(TAB).click();
        await page.locator('aside.pgc .pgc-name').waitFor();
        await page.reload();
        await page.locator('aside.pgc .pgc-name').waitFor();
        assert.equal(await docked(page), true, 'reloaded, it opens as it was');
        await page.setViewportSize({width: 1150, height: 900});
        await page.waitForFunction(() => !document.body.classList.contains('pgc-docked'));
        await page.locator(TAB).waitFor();
        assert.equal(await chatShown(page), false, 'too narrow to dock, it folds to its tab');
      } else {
        await page.setViewportSize({width: 1150, height: 900});
        await page.waitForFunction(() => !document.body.classList.contains('pgc-docked'));
        await shot(page, 'page-chat-goals-1150-dark');
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Strategy's owner is Goals' too: the chat stays open and mounted from Goals to Strategy's other tabs and back.
      const {page, errors} = await openGoals({width: 1440, height: 900}, [], 'light', 'bot:' + OWNER);
      await page.locator('aside.pgc .th-msg[data-mid="m-1"]').waitFor();
      await page.locator('aside.pgc .p-text').fill('A draft that stays');
      await page.locator('aside.pgc .thv').evaluate(el => { (el as HTMLElement & {_kept?: number})._kept = 1; });
      for (const [tab, at] of [['org', '#/strategy/org'], ['goals', GOALS]] as const) {
        await page.locator(`.strat-tabs a[data-strat-tab="${tab}"]`).click();
        await page.waitForFunction(h => location.hash === h, at);
        await page.waitForTimeout(200);
        assert.equal(await chatShown(page), true, `${at}: the chat stays open`);
        assert.equal(await page.locator('aside.pgc .thv').evaluate(el => (el as HTMLElement & {_kept?: number})._kept), 1, `${at}: the same chat`);
        assert.equal(await page.locator('aside.pgc .p-text').inputValue(), 'A draft that stays');
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // AC-11: a phone, as today: the owner's face opens the chat full screen; ‹ returns to Goals.
      const {page, errors} = await openGoals(PHONE, []);
      const face = page.locator(MARK);
      assert.equal(await face.getAttribute('aria-label'), 'Chat with Strategy Owner');
      await face.click();
      await page.locator('aside.pgc.pgc-full .th-msg[data-mid="m-1"]').waitFor();
      const full = await box(page, 'aside.pgc'), top = Math.max(0, (await box(page, '#main')).y);
      assert.deepEqual([full.x, full.y, full.w, full.h], [0, top, PHONE.width, PHONE.height - top], 'full screen');
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
