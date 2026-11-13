// Chat with a page's owner, from the page (ui/app/page-chat.ts, spec page-owner-chat), on Listening against the fake
// hub in support/automations-fake.cts and the threads fake:
//  - AC-1 desktop, 1280 px: Chat at the top right opens the Automations Owner's chat as a column at the right; the
//    list stays where it was and usable beside it;
//  - AC-2 a message sent with a listening open carries refs.from_page {page, label, item} and the chat shows
//    "From Listening › <name>"; the chat is the direct one with the bot (GET /threads/dm?with=bot:...);
//  - AC-3 the column's left edge drags it, 260-560 px; dragged to 400 px and reloaded it opens at 400 px; closed, it
//    stays closed after a reload;
//  - a listening's editor takes the column's place while open;
//  - AC-4 a page with no owner has no Chat;
//  - AC-5 phone, 390 px: the owner's face opens the chat full screen, ‹ at left, the name in the middle, the composer
//    at the bottom and no tab bar; ‹ or the phone's back returns to Listening where it was.
// TICO_UI_SHOTS=<dir> keeps the screenshots (the 1280 px one is the spec's risk: what Listening keeps beside the chat).
// Run: node scripts/ui-tests.cts page-chat
import type {Page} from 'playwright';
import type * as AutomationsFakeSupport from './support/automations-fake.cts';
import type * as ThreadsFakeSupport from './support/threads-fake.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {openAutomations, PHONE, sideways, shot} = require('./support/automations-fake.cts') as AutomationsFakeSupport.Api;
const {threadsFake} = require('./support/threads-fake.cts') as ThreadsFakeSupport.Api;

const OWNER = 'automations-owner';
const DESKTOP = {width: 1280, height: 800};
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
      // AC-1: closed at first; Chat at the top right opens the owner's chat at the right of the list.
      const sent: Sent[] = [], dmReads: string[] = [];
      const answer = threadsFake({bot: OWNER, me: 'human:dana', sent: sent as ThreadsFakeSupport.ThreadsPost[], messages: [
        {id: 'm-1', sender_id: 'bot:' + OWNER, body: 'Reddit rate-limited it from 14:00.'}]});
      const {page, errors} = await openAutomationsWith(OWNER, DESKTOP, '#/listening', answer, dmReads);
      await page.evaluate(() => localStorage.clear());
      await page.reload();
      await page.waitForSelector('.lst-row');
      const chat = page.locator('.au-ph [data-pgc-open]');
      await chat.waitFor();
      assert.equal(await chat.locator('.pgc-word').innerText(), 'Chat', 'the button says Chat beside the owner\'s face');
      assert.equal(await chat.getAttribute('aria-label'), 'Chat with Automations Owner');
      const head = await box(page, '.au-ph'), btn = await box(page, '.au-ph [data-pgc-open]');
      assert.ok(btn.right >= head.right - 40 && btn.y < head.bottom, `Chat sits at the top right: ${JSON.stringify({btn, head})}`);
      assert.equal(await page.locator('.au-pgc').isHidden(), true, 'closed at first');
      assert.deepEqual(dmReads, [], 'the chat loads only when opened');
      const listBefore = await box(page, '.au-c');
      await chat.click();
      await page.locator('.au-pgc .pgc-head .pgc-name').waitFor();
      await page.locator('.au-pgc .th-msg[data-mid="m-1"]').waitFor();
      assert.deepEqual(dmReads, ['bot:' + OWNER], 'the chat is the direct one with the page\'s owner');
      assert.equal(await page.locator('.au-pgc .pgc-name').innerText(), 'Automations Owner');
      const panel = await box(page, '.au-pgc'), list = await box(page, '.au-c');
      assert.equal(panel.w, 340, 'the column opens 340 px wide');
      assert.ok(panel.x >= list.right - 1, `the column is at the right of the list: ${JSON.stringify({panel, list})}`);
      assert.equal(list.y, listBefore.y, 'the page stays where it was');
      assert.ok(list.w >= 700, `Listening keeps about 720 px beside the chat at 1280: ${list.w}`);
      assert.equal(await sideways(page), false, 'no sideways scroll with the chat open');
      // The page stays usable: a listening opens from its row with the chat open.
      await shot(page, 'page-chat-1280-open');
      await page.locator('.lst-row[data-lst="au_market"]').click();
      await page.waitForSelector('.lst-blk');
      assert.equal(await page.locator('.au-pgc').isVisible(), true, 'the chat stays open beside the listening');

      // AC-2: a message sent with a listening open names the page and the listening.
      await page.locator('.au-pgc .p-text').fill('Why did this one send nothing yesterday?');
      await page.locator('.au-pgc .p-text').press('Enter');
      await page.locator('.au-pgc .th-from').waitFor();
      assert.deepEqual(sent.at(-1)?.refs?.from_page, {page: 'listening', label: 'Listening', item: {kind: 'automation', id: 'au_market', name: 'Market listening'}});
      assert.equal(await page.locator('.au-pgc .th-from').innerText(), 'From Listening › Market listening');
      assert.equal(await page.locator('.au-pgc .th-from a').getAttribute('href'), '#/listening/au_market');
      await shot(page, 'page-chat-1280-sent');

      // A listening's editor takes the column's place while open.
      await page.locator('.lst-blk').click();
      await page.waitForSelector('.au-ed [data-b="trigger.sources.0.site"]');
      assert.equal(await page.locator('.au-pgc').isVisible(), false, 'the editor takes the chat\'s place');
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.querySelector('#au-root')!.classList.contains('ed-open'));
      assert.equal(await page.locator('.au-pgc').isVisible(), true, 'the chat is back once the editor closes');

      // AC-3: dragged to 400 px, kept; and it does not go past 260-560.
      const grip = await box(page, '.au-pgc .pgc-grip');
      const y = grip.y + 200, right = (await box(page, '.au-pgc')).right;
      await page.mouse.move(grip.x + 3, y);
      await page.mouse.down();
      await page.mouse.move(right - 900, y, {steps: 4});
      assert.equal((await box(page, '.au-pgc')).w, 560, 'no wider than 560 px');
      await page.mouse.move(right - 100, y, {steps: 4});
      assert.equal((await box(page, '.au-pgc')).w, 260, 'no narrower than 260 px');
      await page.mouse.move(right - 400, y, {steps: 4});
      await page.mouse.up();
      assert.equal((await box(page, '.au-pgc')).w, 400);
      await page.reload();
      await page.locator('.au-pgc .pgc-name').waitFor();
      assert.equal((await box(page, '.au-pgc')).w, 400, 'reloaded, it opens at 400 px');
      await page.locator('.au-pgc [data-pgc-close]').click();
      assert.equal(await page.locator('.au-pgc').isHidden(), true, 'its close button closes it');
      assert.equal(await page.locator('.au-ph [data-pgc-open]').getAttribute('aria-pressed'), 'false');
      await page.reload();
      await page.locator('.au-ph [data-pgc-open]').waitFor();
      assert.equal(await page.locator('.au-pgc').isHidden(), true, 'closed, it stays closed after a reload');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // AC-4: no owner, no Chat.
      const {page, errors} = await openAutomationsWith(null, DESKTOP, '#/listening', threadsFake({bot: OWNER}), []);
      await page.waitForSelector('.lst-row');
      await page.waitForLoadState('networkidle');
      assert.equal(await page.locator('[data-pgc-open]').count(), 0, 'a page with no owner has no Chat');
      assert.equal(await page.locator('.au-pgc').isHidden(), true);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // AC-5: a phone. The owner's face opens the chat full screen; ‹ or back returns to Listening where it was.
      const sent: Sent[] = [];
      const answer = threadsFake({bot: OWNER, me: 'human:dana', sent: sent as ThreadsFakeSupport.ThreadsPost[], messages: [
        {id: 'm-1', sender_id: 'bot:' + OWNER, body: 'Reddit rate-limited it from 14:00.'}]});
      const {page, errors} = await openAutomationsWith(OWNER, PHONE, '#/listening', answer, []);
      await page.waitForSelector('.lst-row');
      const face = page.locator('.au-ph [data-pgc-open]');
      await face.waitFor();
      assert.equal(await face.locator('.pgc-word').isVisible(), false, 'on a phone the button is the owner\'s face');
      assert.equal(await page.locator('#mobile-nav').isVisible(), true);
      await shot(page, 'page-chat-phone-page');
      const hash = await page.evaluate(() => location.hash);
      await face.click();
      await page.locator('.au-pgc.pgc-full .th-msg[data-mid="m-1"]').waitFor();
      const full = await box(page, '.au-pgc');
      assert.deepEqual([full.x, full.y, full.w, full.h], [0, 0, PHONE.width, PHONE.height], 'full screen');
      assert.equal(await page.locator('#mobile-nav').isVisible(), false, 'the chat hides the tab bar');
      const back = await box(page, '.au-pgc [data-pgc-close]'), name = await box(page, '.au-pgc .pgc-who');
      assert.equal(await page.locator('.au-pgc [data-pgc-close]').getAttribute('aria-label'), 'Back');
      assert.ok(back.x < 20, `‹ at the left: ${JSON.stringify(back)}`);
      assert.ok(Math.abs(name.x + name.w / 2 - PHONE.width / 2) < 4, `the name in the middle: ${JSON.stringify(name)}`);
      const composer = await box(page, '.au-pgc .p-text');
      assert.ok(composer.bottom > PHONE.height - 90, `the composer at the bottom: ${JSON.stringify(composer)}`);
      await shot(page, 'page-chat-phone-open');
      await page.locator('.au-pgc [data-pgc-close]').click();
      await page.waitForFunction(() => document.querySelector('.au-pgc')!.hasAttribute('hidden'));
      assert.equal(await page.evaluate(() => location.hash), hash, '‹ returns to Listening');
      assert.equal(await page.locator('#mobile-nav').isVisible(), true, 'the tab bar comes back');
      assert.equal(await page.locator('.lst-row').count(), 2, 'Listening as it was');
      // The phone's back closes it too, and stays on Listening.
      await face.click();
      await page.locator('.au-pgc.pgc-full').waitFor();
      await page.goBack();
      await page.waitForFunction(() => document.querySelector('.au-pgc')!.hasAttribute('hidden'));
      assert.equal(await page.evaluate(() => location.hash), hash, 'back returns to Listening');
      assert.equal(await page.locator('#mobile-nav').isVisible(), true);
      assert.equal(await page.locator('.lst-row').count(), 2);

      // A listening open on a phone: the face stays at the right of the ‹ Listening row, and a question names it.
      await page.locator('.lst-row[data-lst="au_market"]').click();
      await page.waitForSelector('.lst-blk');
      const crumb = await box(page, '.au-crumb'), onItem = await box(page, '.au-ph [data-pgc-open]');
      assert.ok(onItem.w > 0 && onItem.x + onItem.w <= PHONE.width && onItem.x > PHONE.width / 2, `the face at the right: ${JSON.stringify(onItem)}`);
      assert.ok(onItem.y >= crumb.y - 8 && onItem.bottom <= crumb.bottom + 8, `in the ‹ Listening row: ${JSON.stringify([onItem, crumb])}`);
      await shot(page, 'page-chat-phone-listening');
      await face.click();
      await page.locator('.au-pgc.pgc-full .p-text').fill('Why did this one send nothing yesterday?');
      await page.locator('.au-pgc .p-text').press('Enter');
      await page.locator('.au-pgc .th-from').waitFor();
      assert.deepEqual(sent.at(-1)?.refs?.from_page, {page: 'listening', label: 'Listening', item: {kind: 'automation', id: 'au_market', name: 'Market listening'}});
      // A link followed from the chat leaves through the chat's history entry: one back returns to the page before.
      await page.locator('.au-pgc .th-from a').click();
      await page.waitForFunction(() => document.querySelector('.au-pgc')!.hasAttribute('hidden'));
      assert.equal(await page.evaluate(() => location.hash), '#/listening/au_market');
      assert.equal(await page.evaluate(() => document.body.classList.contains('pgc-phone-open')), false, 'the page is back');
      await page.goBack();
      await page.waitForFunction(h => location.hash !== h, '#/listening/au_market');
      assert.equal(await page.evaluate(() => location.hash), hash, 'one back leaves the linked page');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('page-chat: ok');
  } finally {
    await BROWSER.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
