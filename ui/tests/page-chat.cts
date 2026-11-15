// Chat with a page's owner, from the page (ui/app/page-chat.ts, spec page-owner-chat, style guide "Where chat lives"),
// on Listening against the fake hub in support/automations-fake.cts and the threads fake. Listening calls no page chat
// code of its own for the column: the router mounts it (pageChatRoute).
//  - AC-1 a computer too narrow to dock (1150 px): no chat button in the header; a slim tab on the right edge, the owner's
//    face in a gutter of its own (clear of the header's ⋯), before the column in the page's order, opens the Automations
//    Owner's chat in the shared column over the right of the window with the cursor in its composer; the list keeps its
//    place and width and stays usable; the column's close is a quiet icon (no border, a hover fill, 32 px);
//  - AC-2 a message sent with a listening open carries refs.from_page {page, label, item} and the chat shows
//    "From Listening › <name>"; the chat is the direct one with the bot (GET /threads/dm?with=bot:...);
//  - AC-3 the column's left edge drags it, 260-560 px; dragged to 400 px and reloaded it opens at 400 px; Esc and its
//    close close it and give the focus back to the tab;
//  - wherever the page keeps 680 px beside the column's 260 (#main 940 px wide: 1176 px beside the 236 px sidebar) it is docked
//    beside the page and open at first, at 1440 too; one pixel less and it folds to its tab; resized across, it docks
//    or folds; closed while docked, it stays closed after a reload; Esc there gives the focus to the tab;
//  - a listening's editor takes the column's place while open;
//  - AC-4 a page whose owner a person set to "No owner" has no chat: no column, no tab, no mark;
//  - AC-5 phone, 390 px: the owner's face is the chat mark in the header's one row just before ⋯ (44 px); it opens the
//    chat full screen, ‹ at left, the name in the middle, the composer at the bottom and no tab bar; ‹ or the phone's
//    back (one press) returns to Listening where it was; open, it is a modal dialog and the page behind it is inert.
// TICO_UI_SHOTS=<dir> keeps the screenshots.
// Run: node scripts/ui-tests.cts page-chat
import type {Page} from 'playwright';
import type * as AutomationsFakeSupport from './support/automations-fake.cts';
import type * as ThreadsFakeSupport from './support/threads-fake.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {openAutomations, PHONE, sideways, shot} = require('./support/automations-fake.cts') as AutomationsFakeSupport.Api;
const {threadsFake} = require('./support/threads-fake.cts') as ThreadsFakeSupport.Api;

const OWNER = 'automations-owner';
const NARROW = {width: 1150, height: 900}, DESKTOP = {width: 1440, height: 900};
const TAB = 'body > .pgc-edge:not([hidden]) .pgc-tab';
type Sent = {text?: string; refs?: {from_page?: unknown}};

const box = (page: Page, sel: string) => page.locator(sel).first().evaluate(el => {
  const r = el.getBoundingClientRect();
  return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height), right: Math.round(r.right), bottom: Math.round(r.bottom)};
});

let BROWSER: import('playwright').Browser;
async function openAutomationsWith(owner: string | null, viewport: {width: number; height: number}, hash: string,
                                   answer: ThreadsFakeSupport.ThreadsAnswer, dmReads: string[]) {
  return openAutomations(BROWSER, {viewport, hash,
    tweak: fx => { (fx.employees as Record<string, string>[]).push({name: OWNER, display_name: 'Automations Owner', host: 'keeper', status: 'active'}); },
    routes: async page => {
      await page.route('**/api/**', async route => {
        const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
        const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        if (p === '/api/v2/page-owners') return json({pages: {listening: {bot: owner ? 'bot:' + owner : null, name: 'Listening'}}});
        if (p === '/api/v2/threads/dm' && method === 'GET') dmReads.push(url.searchParams.get('with') || '');
        const body = method === 'GET' ? null : req.postDataJSON?.() ?? null;
        const out = answer(p, url, method, body);
        return out === undefined ? route.fallback() : json(out);
      });
    }});
}

(async () => {
  BROWSER = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    {
      // AC-1: too narrow to dock, closed at first; the tab on the right edge opens the owner's chat at the right of the list.
      const sent: Sent[] = [], dmReads: string[] = [];
      const answer = threadsFake({bot: OWNER, me: 'human:dana', sent: sent as ThreadsFakeSupport.ThreadsPost[], messages: [
        {id: 'm-1', sender_id: 'bot:' + OWNER, body: 'Reddit rate-limited it from 14:00.'}]});
      const {page, errors} = await openAutomationsWith(OWNER, NARROW, '#/listening', answer, dmReads);
      await page.evaluate(() => localStorage.clear());
      await page.reload();
      await page.waitForSelector('.lst-row');
      const chat = page.locator(TAB);
      await chat.waitFor();
      assert.equal(await chat.getAttribute('aria-label'), 'Chat with Automations Owner');
      assert.equal(await chat.getAttribute('aria-expanded'), 'false');
      assert.equal(await page.locator('.au-ph [data-pgc-open]:visible, .au-ph .pgc-btn').count(), 0, 'no chat button in the header');
      // The tab is the owner's face, on the right edge in a gutter #main leaves for it: it covers none of the page.
      const tabBox = await box(page, TAB), main = await box(page, '#main'), more = await box(page, '.au-ph > .ph-more');
      assert.ok(tabBox.right >= NARROW.width - 4 && tabBox.h >= 44, `the tab on the right edge: ${JSON.stringify(tabBox)}`);
      assert.ok(main.right <= tabBox.x && more.right < tabBox.x, `the page and its ⋯ stop short of the tab: ${JSON.stringify({main, more, tabBox})}`);
      assert.equal((await box(page, TAB + ' > *')).w, 22, 'the owner\'s face, 22 px');
      assert.equal(await page.locator('aside.pgc').isHidden(), true, 'too narrow to dock: folded to its tab at first');
      assert.equal(await page.evaluate(() => {
        const edge = document.querySelector('body > .pgc-edge')!, aside = document.querySelector('body > aside.pgc')!;
        return !!(edge.compareDocumentPosition(aside) & Node.DOCUMENT_POSITION_FOLLOWING);
      }), true, 'the tab comes before the column in the page\'s order');
      assert.deepEqual(dmReads, [], 'the chat loads only when opened');
      const listBefore = await box(page, '.au-c');
      await chat.click();
      await page.locator('aside.pgc .pgc-head .pgc-name').waitFor();
      await page.locator('aside.pgc .th-msg[data-mid="m-1"]').waitFor();
      await page.waitForFunction(() => document.activeElement?.matches('aside.pgc .p-text'), null, {timeout: 5000});
      assert.deepEqual(dmReads, ['bot:' + OWNER], 'the chat is the direct one with the page\'s owner');
      // Its close: a quiet icon button, no border or shadow, 32 px, a fill on hover only.
      const x = page.locator('aside.pgc .pgc-head [data-pgc-close]');
      const look = () => x.evaluate(el => { const c = getComputedStyle(el); return {border: c.borderTopWidth, shadow: c.boxShadow, bg: c.backgroundColor, w: el.getBoundingClientRect().width, h: el.getBoundingClientRect().height}; });
      const still = await look();
      assert.deepEqual([still.border, still.shadow, still.w, still.h], ['0px', 'none', 32, 32], `a quiet 32 px close: ${JSON.stringify(still)}`);
      assert.ok(/rgba\(0, 0, 0, 0\)|transparent/.test(still.bg), `no fill at rest: ${still.bg}`);
      await x.hover();
      await page.waitForTimeout(200);
      const hover = await look();
      assert.notEqual(hover.bg, still.bg, 'a fill on hover');
      assert.equal(hover.border, '0px', 'still no border on hover');
      await page.mouse.move(10, 10);
      assert.equal(await page.locator('aside.pgc .pgc-name').innerText(), 'Automations Owner');
      const panel = await box(page, 'aside.pgc'), list = await box(page, '.au-c');
      assert.equal(panel.w, 340, 'the column opens 340 px wide');
      assert.equal(panel.right, NARROW.width, `the column is at the right of the window: ${JSON.stringify(panel)}`);
      assert.equal(await chat.getAttribute('aria-expanded'), 'true');
      assert.deepEqual(list, listBefore, 'too narrow to dock, it floats over the page: Listening keeps its place and width');
      assert.equal(await page.evaluate(() => document.body.classList.contains('pgc-docked')), false);
      assert.equal(await sideways(page), false, 'no sideways scroll with the chat open');
      // The page stays usable: a listening opens from its row with the chat open.
      await shot(page, 'page-chat-1150-open');
      await page.locator('.lst-row[data-lst="au_market"]').click();
      await page.waitForSelector('.lst-blk');
      assert.equal(await page.locator('aside.pgc').isVisible(), true, 'the chat stays open beside the listening');

      // AC-2: a message sent with a listening open names the page and the listening.
      await page.locator('aside.pgc .p-text').fill('Why did this one send nothing yesterday?');
      await page.locator('aside.pgc .p-text').press('Enter');
      await page.locator('aside.pgc .th-from').waitFor();
      assert.deepEqual(sent.at(-1)?.refs?.from_page, {page: 'listening', label: 'Listening', item: {kind: 'automation', id: 'au_market', name: 'Market listening'}});
      assert.equal(await page.locator('aside.pgc .th-from').innerText(), 'From Listening › Market listening');
      assert.equal(await page.locator('aside.pgc .th-from a').getAttribute('href'), '#/listening/au_market');
      await shot(page, 'page-chat-1150-sent');

      // A listening's editor takes the column's place while open.
      await page.locator('.lst-blk').click();
      await page.waitForSelector('.au-ed [data-b="trigger.sources.0.site"]');
      assert.equal(await page.locator('aside.pgc').isVisible(), false, 'the editor takes the chat\'s place');
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.querySelector('#au-root')!.classList.contains('ed-open'));
      assert.equal(await page.locator('aside.pgc').isVisible(), true, 'the chat is back once the editor closes');

      // AC-3: dragged to 400 px, kept; and it does not go past 260-560.
      const grip = await box(page, 'aside.pgc .pgc-grip');
      const y = grip.y + 200, right = (await box(page, 'aside.pgc')).right;
      await page.mouse.move(grip.x + 3, y);
      await page.mouse.down();
      await page.mouse.move(right - 900, y, {steps: 4});
      assert.equal((await box(page, 'aside.pgc')).w, 560, 'no wider than 560 px');
      await page.mouse.move(right - 100, y, {steps: 4});
      assert.equal((await box(page, 'aside.pgc')).w, 260, 'no narrower than 260 px');
      await page.mouse.move(right - 400, y, {steps: 4});
      await page.mouse.up();
      assert.equal((await box(page, 'aside.pgc')).w, 400);
      await page.reload();
      await page.locator(TAB).click();
      await page.locator('aside.pgc .pgc-name').waitFor();
      assert.equal((await box(page, 'aside.pgc')).w, 400, 'reloaded, it opens at 400 px');
      await page.locator('aside.pgc [data-pgc-close]').click();
      assert.equal(await page.locator('aside.pgc').isHidden(), true, 'its close button closes it');
      assert.equal(await page.locator(TAB).getAttribute('aria-expanded'), 'false');
      assert.equal(await page.evaluate(() => document.activeElement?.matches('.pgc-tab')), true, 'the focus is back on the tab');
      // Keyboard: Enter on the tab opens it; Esc in the composer closes it and gives the focus back to the tab.
      await page.keyboard.press('Enter');
      await page.locator('aside.pgc .p-text').click();
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => document.querySelector<HTMLElement>('aside.pgc')!.hidden);
      assert.equal(await page.evaluate(() => document.activeElement?.matches('.pgc-tab')), true, 'Esc gives the focus back to the tab');
      // 1440 px: docked beside the page and open at first; the tab goes (Chris, round 2 A).
      await page.setViewportSize(DESKTOP);
      await page.evaluate(() => { for (const k of Object.keys(localStorage)) if (k.startsWith('tico.page_chat.open')) localStorage.removeItem(k); });
      await page.reload();
      await page.locator('aside.pgc .pgc-name').waitFor();
      const isDocked = () => page.evaluate(() => document.body.classList.contains('pgc-docked'));
      assert.equal(await isDocked(), true, 'docked at 1440 px');
      assert.equal(await page.locator(TAB).count(), 0, 'no tab while it is docked open');
      const docked = await box(page, 'aside.pgc'), mainDocked = await box(page, '#main');
      assert.ok(mainDocked.right <= docked.x + 1, `the page sits beside it: ${JSON.stringify({docked, mainDocked})}`);
      assert.equal(await sideways(page), false, 'nothing sideways, docked');
      await shot(page, 'page-chat-1440-docked');
      // Resized across the docking width: narrower, it folds to its tab; wider again, it is docked open again.
      await page.setViewportSize(NARROW);
      await page.locator(TAB).waitFor();
      assert.equal(await page.locator('aside.pgc').isHidden(), true, 'narrowed past the docking width, folded to its tab');
      await page.setViewportSize(DESKTOP);
      await page.locator('aside.pgc:not([hidden]) .pgc-name').waitFor();
      assert.equal(await isDocked(), true, 'widened again, docked open again');
      // Exactly at the docking width (#main 940 px: 680 for the page beside the column's 260) it docks; a pixel less, not.
      const extra = await page.evaluate(() => window.innerWidth - document.documentElement.clientWidth
        + Math.round(document.getElementById('main')!.getBoundingClientRect().left));
      await page.setViewportSize({width: extra + 940, height: 900});
      // (still docked from 1440: wait for the resize to narrow the column to the width the page leaves it)
      await page.waitForFunction(() => document.body.classList.contains('pgc-docked')
        && Math.round(document.querySelector('aside.pgc')!.getBoundingClientRect().width) === 260, null, {timeout: 5000}).catch(() => {});
      const atEdge = await box(page, 'aside.pgc'), mainAt = await box(page, '#main');
      assert.deepEqual([atEdge.w, mainAt.w], [260, 680], `at the docking width: the column 260, the page 680: ${JSON.stringify({atEdge, mainAt})}`);
      assert.equal(await sideways(page), false, 'nothing sideways at the docking width');
      await page.setViewportSize({width: extra + 939, height: 900});
      await page.locator(TAB).waitFor();
      assert.equal(await page.locator('aside.pgc').isHidden(), true, 'a pixel narrower, folded to its tab');
      await page.setViewportSize(DESKTOP);
      await page.locator('aside.pgc:not([hidden]) .pgc-name').waitFor();
      assert.equal((await box(page, 'aside.pgc')).w, 400, 'docked at the width it was dragged to');
      // Esc in the docked column closes it and gives the focus to the tab that takes its place.
      await page.locator('aside.pgc .p-text').click();
      await page.keyboard.press('Escape');
      await page.locator(TAB).waitFor();
      assert.equal(await page.evaluate(() => document.activeElement?.matches('.pgc-tab')), true, 'Esc, docked: the focus on the tab');
      // Closed while docked, it stays closed after a reload, and the tab opens it again.
      await page.reload();
      await page.locator(TAB).waitFor();
      await page.waitForTimeout(300);
      assert.equal(await page.locator('aside.pgc').isHidden(), true, 'closed, it stays closed after a reload');
      await page.locator(TAB).click();
      await page.locator('aside.pgc:not([hidden]) .pgc-name').waitFor();
      assert.equal(await isDocked(), true, 'opened again, docked');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // AC-4: no owner, no Chat.
      const {page, errors} = await openAutomationsWith(null, DESKTOP, '#/listening', threadsFake({bot: OWNER}), []);
      await page.waitForSelector('.lst-row');
      await page.waitForLoadState('networkidle');
      assert.equal(await page.locator('[data-pgc-open]:visible').count(), 0, 'a page with no owner has no tab and no mark');
      assert.equal(await page.locator('aside.pgc').isHidden(), true);
      assert.equal(await page.evaluate(() => document.body.classList.contains('pgc-tabbed')), false, 'and no gutter for one');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // AC-5: a phone. The owner's face opens the chat full screen; ‹ or back returns to Listening where it was.
      const sent: Sent[] = [];
      const msgs: ThreadsFakeSupport.ThreadsFixture[] = [{id: 'm-1', sender_id: 'bot:' + OWNER, body: 'Reddit rate-limited it from 14:00.'}];
      const answer = threadsFake({bot: OWNER, me: 'human:dana', sent: sent as ThreadsFakeSupport.ThreadsPost[], messages: msgs});
      const {page, errors} = await openAutomationsWith(OWNER, PHONE, '#/listening', answer, []);
      await page.waitForSelector('.lst-row');
      const face = page.locator('.au-ph > .ph-chat > [data-pgc-open]');
      await face.waitFor();
      // The chat mark: the owner's face, 22 px in a 44 px button in the header's one row, just before ⋯.
      const mark = await box(page, '.au-ph > .ph-chat > [data-pgc-open]'), moreBox = await box(page, '.au-ph > .ph-more');
      const avatar = await box(page, '.au-ph > .ph-chat > [data-pgc-open] > *');
      assert.ok(mark.w === 44 && mark.h === 44 && avatar.w === 22, `22 px in a 44 px button: ${JSON.stringify({mark, avatar})}`);
      assert.ok(mark.right <= moreBox.x && Math.abs(mark.y - moreBox.y) < 2, `just before ⋯, in its row: ${JSON.stringify({mark, moreBox})}`);
      assert.equal(await page.locator(TAB).count(), 0, 'no tab on a phone');
      assert.equal(await page.locator('#mobile-nav').isVisible(), true);
      await shot(page, 'page-chat-phone-page');
      const hash = await page.evaluate(() => location.hash);
      await face.click();
      await page.locator('aside.pgc.pgc-full .th-msg[data-mid="m-1"]').waitFor();
      const full = await box(page, 'aside.pgc');
      assert.deepEqual(await page.evaluate(() => {
        const a = document.querySelector('aside.pgc')!;
        return [a.getAttribute('role'), a.getAttribute('aria-modal'), ...['#main', '#mobile-nav'].map(s => !!document.querySelector<HTMLElement>(s)?.inert)];
      }), ['dialog', 'true', true, true], 'full screen: a modal dialog, the page and the tab bar inert behind it');
      const top = await page.evaluate(() => Math.max(0, Math.round(document.getElementById('main')!.getBoundingClientRect().top)));
      assert.deepEqual([full.x, full.y, full.w, full.h], [0, top, PHONE.width, PHONE.height - top], 'full screen, under any banner');
      assert.equal(await page.locator('#mobile-nav').isVisible(), false, 'the chat hides the tab bar');
      const back = await box(page, 'aside.pgc [data-pgc-close]'), name = await box(page, 'aside.pgc .pgc-who');
      assert.equal(await page.locator('aside.pgc [data-pgc-close]').getAttribute('aria-label'), 'Back');
      assert.ok(back.x < 20, `‹ at the left: ${JSON.stringify(back)}`);
      assert.ok(Math.abs(name.x + name.w / 2 - PHONE.width / 2) < 4, `the name in the middle: ${JSON.stringify(name)}`);
      const composer = await box(page, 'aside.pgc .p-text');
      assert.ok(composer.bottom > PHONE.height - 90, `the composer at the bottom: ${JSON.stringify(composer)}`);
      await shot(page, 'page-chat-phone-open');
      await page.locator('aside.pgc [data-pgc-close]').click();
      await page.waitForFunction(() => document.querySelector('aside.pgc')!.hasAttribute('hidden'));
      assert.equal(await page.evaluate(() => location.hash), hash, '‹ returns to Listening');
      assert.equal(await page.locator('#mobile-nav').isVisible(), true, 'the tab bar comes back');
      assert.equal(await page.evaluate(() => document.getElementById('main')!.inert), false, 'the page is live again');
      assert.equal(await page.locator('.lst-row').count(), 2, 'Listening as it was');
      // The phone's back closes it too, in one press, and stays on Listening.
      await face.click();
      await page.locator('aside.pgc.pgc-full').waitFor();
      await page.goBack();
      await page.waitForFunction(() => document.querySelector('aside.pgc')!.hasAttribute('hidden'));
      assert.equal(await page.evaluate(() => location.hash), hash, 'back returns to Listening');
      assert.equal(await page.locator('#mobile-nav').isVisible(), true);
      assert.equal(await page.locator('.lst-row').count(), 2);

      // A listening open on a phone: the face stays at the right of the ‹ Listening row, and a question names it.
      await page.locator('.lst-row[data-lst="au_market"]').click();
      await page.waitForSelector('.lst-blk');
      const crumb = await box(page, '.au-crumb'), onItem = await box(page, '.au-ph > .ph-chat > [data-pgc-open]');
      assert.ok(onItem.w > 0 && onItem.x + onItem.w <= PHONE.width && onItem.x > PHONE.width / 2, `the face at the right: ${JSON.stringify(onItem)}`);
      assert.ok(onItem.y >= crumb.y - 8 && onItem.bottom <= crumb.bottom + 8, `in the ‹ Listening row: ${JSON.stringify([onItem, crumb])}`);
      await shot(page, 'page-chat-phone-listening');
      await face.click();
      await page.locator('aside.pgc.pgc-full .p-text').fill('Why did this one send nothing yesterday?');
      await page.locator('aside.pgc .p-text').press('Enter');
      await page.locator('aside.pgc .th-from').waitFor();
      assert.deepEqual(sent.at(-1)?.refs?.from_page, {page: 'listening', label: 'Listening', item: {kind: 'automation', id: 'au_market', name: 'Market listening'}});
      // A link followed from the chat leaves through the chat's history entry: one back returns to the page before.
      await page.locator('aside.pgc .th-from a').click();
      await page.waitForFunction(() => document.querySelector('aside.pgc')!.hasAttribute('hidden'));
      assert.equal(await page.evaluate(() => location.hash), '#/listening/au_market');
      assert.equal(await page.evaluate(() => document.body.classList.contains('pgc-phone-open')), false, 'the page is back');
      await page.goBack();
      await page.waitForFunction(h => location.hash !== h, '#/listening/au_market');
      assert.equal(await page.evaluate(() => location.hash), hash, 'one back leaves the linked page');
      // A link to another page: opened on the list, "From Listening › Market listening" goes to the listening, and
      // one back returns to the list with no copy of it left in history.
      await page.waitForSelector('.lst-row');
      msgs.push({id: 'm-2', sender_id: 'human:dana', body: 'Why did this one send nothing yesterday?', refs: {from_page: sent.at(-1)?.refs?.from_page}});
      await face.click();
      await page.locator('aside.pgc.pgc-full .th-from a[href="#/listening/au_market"]').first().click();
      await page.waitForFunction(() => location.hash === '#/listening/au_market' && document.querySelector('aside.pgc')!.hasAttribute('hidden'));
      await page.waitForSelector('.lst-blk');
      await page.goBack();
      await page.waitForFunction(h => location.hash === h, hash);
      await page.waitForSelector('.lst-row');
      assert.equal(await page.locator('aside.pgc').isHidden(), true, 'back lands on the list, not on the chat');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('page-chat: ok');
  } finally {
    await BROWSER.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
