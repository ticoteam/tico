// A chat on every page (ui/app/page-chat.ts, spec chat-on-every-page, style guide "Where chat lives") on a real hub,
// each page's owner set in Settings' Page owners (PUT /v2/page-owners/<page>) to one of the hub's bots. None of these
// pages calls any page chat code for its column: the router mounts it (pageChatRoute).
//  - a computer too narrow to dock the chat (1150 px): no chat button and no Ask button in any header; the page's tab on the right edge, the owner's
//    face, sits past the header's ⋯ in a gutter of its own and opens the owner's chat over the page, which keeps its
//    width; moving between Strategy's tabs, or between Docs and Files, keeps the chat open; each page's "From <page>"
//    line leads back to the page;
//  - 1440 px: the chat is docked beside the page and open at first, and a message from a page of no code of its own
//    (Finance) carries refs.from_page {page, label};
//  - a page whose owner a person set to "No owner" has no chat: no column, no tab;
//  - no page chat on a bot's page, Threads, the Assistant or the style guide;
//  - Calendar and Meetings are the Assistant's: their tab (the Assistant's face) opens Ask in the sidebar, never a
//    second Assistant chat and never a column;
//  - Rules with a rule open: the rule's detail holds the chat's place; the tab closes the rule and the chat takes its
//    place; opening a rule again puts the chat away; closing the rule brings it back;
//  - a 390 px phone: each page's chat mark (the owner's face in the header's row) opens the chat full screen and ‹
//    returns to the page; Meetings' opens Ask; no page scrolls sideways.
// Listening's editor and the column's drag and keyboard are page-chat.cts's; Goals' chip page-chat-goals.cts's.
// TICO_UI_SHOTS=<dir> keeps a screenshot of each page with its chat open.
// Run: TICO_PYTHON=$PWD/.venv/bin/python node scripts/ui-tests.cts -j 1 page-chat-pages
import type {Page} from 'playwright';
import type * as RE from './support/redesign-e2e.cts';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {run, open, go, ok, sideways, shot, t} = require('./support/redesign-e2e.cts') as RE.Api;

declare function actorLabel(actor: string): string;
declare const S: {emps?: unknown[]};
declare function askName(): string;
declare function pageChatHome(page: string): string;

// Each page's key, the bot set as its owner here (the hub's own bots: the defaults are not installed on it), and the
// addresses that show it.
const PAGES: [string, string, string[]][] = [
  ['strategy', 'engineering-manager', ['#/strategy', '#/strategy/org', '#/strategy/budget', '#/strategy/market']],
  ['finance', 'release-manager', ['#/finance']],
  ['growth', 'builder-claude', ['#/growth']],
  ['risk', 'release-manager', ['#/risk', '#/risk/disputes']],
  ['automations', 'builder-openai', ['#/automations']],
  ['docs', 'engineering-manager', ['#/docs', '#/library/files']],
  ['updates', 'release-manager', ['#/updates']],
  ['rules', 'builder-claude', ['#/rules']],
  ['company', 'builder-openai', ['#/home']],
  ['skills', 'release-manager', ['#/library/skills']],
  ['settings', 'engineering-manager', ['#/settings']],
  ['usage', 'builder-claude', ['#/usage']],
];
const ASK_PAGES = ['#/calendar', '#/meetings'];
const NONE = ['#/bot/engineering-manager', '#/threads', '#/assistant', '#/style-guide'];
// Where each page's "From <page>" line leads.
const BACK: Record<string, string> = {strategy: '#/strategy', finance: '#/finance', growth: '#/growth', risk: '#/risk', automations: '#/automations',
  docs: '#/docs', updates: '#/updates', rules: '#/rules', company: '#/home', skills: '#/library/skills', settings: '#/settings', usage: '#/usage'};

const TAB = 'body > .pgc-edge:not([hidden]) .pgc-tab', MARK = '#main header.ph > .ph-chat > [data-pgc-open]';
const at = async (page: Page, hash: string) => {
  await go(page, hash);
  await page.waitForFunction(h => (location.hash.split('?')[0] ?? '').startsWith(h.split('?')[0] ?? h), hash, {timeout: t(10000)});
};
const box = (page: Page, sel: string) => page.locator(sel).first().evaluate(el => {
  const r = el.getBoundingClientRect();
  return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), right: Math.round(r.right), bottom: Math.round(r.bottom)};
});
const chatShown = (page: Page) => page.locator('body > aside.pgc').isVisible();
const askShown = (page: Page) => page.evaluate(() => !!document.querySelector('#ask-panel')?.classList.contains('ask-shown'));
// The tab (a computer) or the mark (a phone) names whose chat it opens.
const way = (page: Page, phone: boolean, name: string) => page.locator(phone ? MARK : TAB).and(page.locator(`[aria-label="Chat with ${name}"]`));

run('page-chat-pages', ['org', 'toured'], async ({hub, browser, phone, tag}) => {
  const page = await open(browser, hub, 'dana', {phone, hash: '#/updates'});
  if (!phone) await page.setViewportSize({width: 1150, height: 900});   // too narrow to dock: the tab
  await page.evaluate(() => { for (const k of Object.keys(localStorage)) if (k.startsWith('tico.page_chat')) localStorage.removeItem(k); });
  for (const [key, bot] of PAGES) await ok(page, 'PUT', 'page-owners/' + key, {bot});
  // The bots' names are the roster's, once it has loaded (before, a name is the bot's slug).
  await page.waitForFunction(() => (S.emps || []).length > 0, null, {timeout: t(15000)});

  for (const [key, bot, hashes] of PAGES) {
    const name = await page.evaluate(b => actorLabel('bot:' + b), bot);
    for (const [n, hash] of hashes.entries()) {
      await at(page, hash);
      const chat = way(page, phone, name);
      await chat.waitFor({timeout: t(15000)}).catch(async e => {
        throw new Error(`${tag} ${hash}: no way to ${name}'s chat; the page shows ${JSON.stringify(await page.evaluate(() =>
          [...document.querySelectorAll('.pgc-tab, .ph-chat > *')].map(el => [el.className, el.getAttribute('aria-label'), (el.closest('[hidden]') ? 'hidden' : 'shown')])))}: ${(e as Error).message.split('\n')[0]}`);
      });
      assert.equal(await page.locator('#main [data-ask-open], #docs-ask, #meet-ask, #main .pgc-btn').count(), 0, `${tag} ${hash}: no Ask or Chat button`);
      if (phone) {
        // The mark opens the chat full screen; ‹ returns to the page.
        if (n) continue;
        await chat.click();
        await page.locator('aside.pgc.pgc-full .pgc-name').waitFor({timeout: t(10000)});
        assert.equal(await page.locator('aside.pgc .pgc-name').innerText(), name);
        await shot(page, `page-chat-${tag}-${hash.slice(2).replace(/\//g, '-')}`);
        await page.locator('aside.pgc [data-pgc-close]').click();
        await page.locator('aside.pgc').waitFor({state: 'hidden', timeout: t(5000)});
        await page.waitForFunction(() => !history.state?.pageChat, null, {timeout: t(5000)});
        // (Settings writes its tab into the address, #/settings/general.)
        assert.ok((await page.evaluate(() => location.hash.split('?')[0] ?? '')).startsWith(hash), `${tag} ${hash}: back on the page`);
        assert.equal(await sideways(page), false, `${tag} ${hash}: no sideways scroll`);
        continue;
      }
      assert.equal(await page.locator('#main header.ph [data-pgc-open]:visible').count(), 0, `${tag} ${hash}: nothing of the chat in the header`);
      // The tab has its own gutter: the page, and its header's ⋯, stop short of it.
      const edge = await box(page, 'body > .pgc-edge'), main = await box(page, '#main');
      const more = page.locator('#main header.ph > .ph-more');
      if (await more.count()) assert.ok((await box(page, '#main header.ph > .ph-more')).right <= edge.x, `${tag} ${hash}: ⋯ before the tab`);
      // Strategy's tabs, and Docs and Files, share one chat: opened on the first, it is still open on the next.
      if (n === 0) {
        assert.ok(main.right <= edge.x, `${tag} ${hash}: the page stops at the tab's gutter: ${JSON.stringify({main, edge})}`);
        assert.equal(await chatShown(page), false, `${tag} ${hash}: folded to its tab where it does not dock`);
        await chat.click();
        await page.locator('aside.pgc .pgc-name').waitFor({timeout: t(10000)});
        assert.deepEqual(await box(page, '#main'), main, `${tag} ${hash}: the page keeps its width under it`);
      }
      assert.equal(await chatShown(page), true, `${tag} ${hash}: the chat is open`);
      assert.equal(await page.locator('aside.pgc .pgc-name').innerText(), name, `${tag} ${hash}: the owner's chat`);
      await page.locator('aside.pgc .thv-composer').waitFor({timeout: t(10000)});
      await shot(page, `page-chat-${tag}-${hash.slice(2).replace(/\//g, '-')}`);
    }
    if (!phone) {
      assert.equal(await page.evaluate(k => pageChatHome(k), key), BACK[key], `${key}: From leads to its page`);
      await page.locator('aside.pgc [data-pgc-close]').click();
    }
  }

  // Calendar and Meetings: the Assistant's face opens Ask; no column, no second Assistant chat.
  const assistant = await page.evaluate(() => askName());
  // (Calendar draws its own header, not the shared one, so a phone has no mark there yet: its Ask is the tab bar's.)
  for (const hash of ASK_PAGES.filter(h => !phone || h !== '#/calendar')) {
    await at(page, hash);
    const chat = way(page, phone, assistant);
    await chat.waitFor({timeout: t(15000)});
    assert.equal(await page.locator('#main [data-ask-open], #meet-ask').count(), 0, `${tag} ${hash}: no Ask button`);
    assert.equal(await askShown(page), false);
    await chat.click();
    await page.waitForFunction(() => document.querySelector('#ask-panel')?.classList.contains('ask-shown'), null, {timeout: t(5000)});
    assert.equal(await page.locator('aside.pgc:not([hidden])').count(), 0, `${tag} ${hash}: Ask, not a second chat`);
    if (!phone) assert.equal(await chat.getAttribute('aria-expanded'), 'true', `${tag} ${hash}: says Ask is open`);
    await shot(page, `page-chat-${tag}-${hash.slice(2)}-ask`);
    await page.locator('#ask-panel [data-ask-close]').first().click();
    await page.waitForFunction(() => !document.querySelector('#ask-panel')?.classList.contains('ask-shown'), null, {timeout: t(5000)});
    assert.equal(await sideways(page), false, `${tag} ${hash}: no sideways scroll`);
  }

  // No page chat where the chat is the page, or on the style guide.
  for (const hash of NONE) {
    await at(page, hash);
    await page.waitForTimeout(t(800));
    assert.equal(await page.locator(`${TAB}, ${MARK}, aside.pgc:not([hidden])`).count(), 0, `${tag} ${hash}: no page chat`);
  }

  // "No owner", chosen by a person: no chat at all.
  await ok(page, 'PUT', 'page-owners/growth', {bot: null});
  await at(page, '#/growth');
  await page.locator('#main header.ph h1.ph-t', {hasText: 'Growth'}).first().waitFor({timeout: t(15000)});
  await page.waitForTimeout(t(800));
  assert.equal(await page.locator(`${TAB}, ${MARK}, aside.pgc:not([hidden])`).count(), 0, `${tag}: no owner, no chat`);
  if (phone) { assert.deepEqual(page.errors, []); return; }

  // Settings › General › Page owners: one compact row per page, its owner's select; Growth says "No owner" as set above;
  // a search narrows the long list.
  await at(page, '#/settings/general');
  const card = page.locator('#set-page-owners');
  await card.locator('table tbody tr').first().waitFor({timeout: t(15000)});
  const rows = await card.locator('table tbody tr').count();
  const pages = Object.keys((await ok<{pages: Record<string, unknown>}>(page, 'GET', 'page-owners')).pages);
  assert.equal(rows, pages.length, 'a row per page');
  assert.equal(await card.locator('select[data-page-owner="growth"]').inputValue(), '', 'Growth: No owner');
  assert.equal(await card.locator('select[data-page-owner="finance"]').inputValue(), 'release-manager');
  await card.locator('[data-page-owners-q]').fill('fin');
  assert.deepEqual(await card.locator('tbody tr:visible td.po-page').allInnerTexts(), ['Finance'], 'the search narrows the list');
  await card.locator('[data-page-owners-q]').fill('');
  await shot(page, 'page-chat-desktop-settings-page-owners');

  // 1440 px: docked beside the page and open at first (closed above on each page: reset that). A message carries the page.
  await page.evaluate(() => { for (const k of Object.keys(localStorage)) if (k.startsWith('tico.page_chat.open')) localStorage.removeItem(k); });
  await page.setViewportSize({width: 1440, height: 900});
  await at(page, '#/updates');
  await at(page, '#/finance');
  await page.locator('aside.pgc:not([hidden]) .pgc-name').waitFor({timeout: t(15000)});
  assert.equal(await page.evaluate(() => document.body.classList.contains('pgc-docked')), true, 'docked at 1440 px');
  assert.ok((await box(page, '#main')).right <= (await box(page, 'aside.pgc')).x + 1, 'the page beside it');
  await shot(page, 'page-chat-desktop-1440-finance');
  const posted = page.waitForRequest(r => r.method() === 'POST' && /\/api\/v2\/threads\/[^/]+\/messages$/.test(new URL(r.url()).pathname), {timeout: t(15000)});
  await page.locator('aside.pgc .thv-composer .p-text').fill('What moved this week?');
  await page.locator('aside.pgc .thv-composer .p-text').press('Enter');
  const body = (await posted).postDataJSON() as {refs?: {from_page?: unknown}};
  assert.deepEqual(body.refs?.from_page, {page: 'finance', label: 'Finance'}, `a message from Finance carries the page: ${JSON.stringify(body)}`);
  await page.setViewportSize({width: 1150, height: 900});

  // Rules with a rule open: the detail holds the chat's place; the tab closes the rule and the chat takes it.
  const rule = await ok<{id: string}>(page, 'POST', 'rules', {kind: 'internal', text: 'Reply to customers within one business day', who: {all_bots: true}});
  await at(page, '#/rules?rule=' + rule.id);
  const side = page.locator('#ru-side');
  await side.locator('.ru-side-h').waitFor({timeout: t(10000)});
  await page.locator(TAB).click();
  await page.locator('aside.pgc .pgc-name').waitFor({timeout: t(10000)});
  await page.waitForFunction(() => !new URLSearchParams(location.hash.split('?')[1] || '').has('rule'), null, {timeout: t(5000)});
  await side.waitFor({state: 'hidden', timeout: t(5000)}).catch(() => {});
  assert.equal(await chatShown(page), true, 'the chat in the rule\'s place');
  await shot(page, 'page-chat-desktop-rules-swap');
  await at(page, '#/rules?rule=' + rule.id);
  await side.locator('.ru-side-h').waitFor({timeout: t(10000)});
  await page.locator('body > aside.pgc').waitFor({state: 'hidden', timeout: t(5000)});
  assert.equal(await page.locator(TAB).getAttribute('aria-expanded'), 'false', 'opening a rule puts the chat away');
  await at(page, '#/rules');
  await page.locator('aside.pgc .pgc-name').waitFor({timeout: t(10000)});
  assert.equal(await chatShown(page), true, 'the rule closed, the chat is back');
  assert.deepEqual(page.errors, []);
});
