// A chat on every page, Release 3 (ui/app/page-chat.ts, spec chat-on-every-page) on a real hub, each page's owner set
// in Settings' Page owners (PUT /v2/page-owners/<page>) to one of the hub's bots:
//  - AC-9 desktop, 1440 px: Strategy's other tabs, Finance, Growth, Automations, Docs, Files, Updates and Rules each have
//    Chat at the top right with that page's owner, which opens the owner's chat floating over the page (the page keeps
//    its width); moving between Strategy's tabs, or between Docs and Files, keeps the chat open; each page's "From
//    <page>" line links back to the page;
//  - Meetings and Docs have no Ask button; Calendar's and Meetings' Chat has the Assistant's face and opens Ask, never a
//    page chat;
//  - AC-10 Rules with a rule open: Chat closes the rule's detail and the chat takes its place; opening a rule closes it;
//  - AC-11 a 390 px phone: each page's owner's face opens the chat full screen and ‹ returns to the page; Calendar's
//    and Meetings' face opens Ask; no page scrolls sideways.
// Listening's editor (AC-3) is page-chat.cts's, and Goals' chip and "From Goals ›" (AC-6) page-chat-goals.cts's.
// TICO_UI_SHOTS=<dir> keeps a screenshot of each page with its chat open.
// Run: TICO_PYTHON=$PWD/.venv/bin/python node scripts/ui-tests.cts -j 1 page-chat-pages
import type {Page} from 'playwright';
import type * as RE from './support/redesign-e2e.cts';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {run, open, go, ok, sideways, shot, t} = require('./support/redesign-e2e.cts') as RE.Api;

declare function actorLabel(actor: string): string;
declare function askName(): string;
declare const THV_FROM_PAGES: Record<string, () => string>;

// Each page's key, the bot set as its owner here (the hub's own bots: the defaults are not installed on it), and the
// addresses that show it, each with the place its header holds Chat (a page's code may still be loading after the address
// changes, so the test waits for that page's own Chat).
const PAGES: [string, string, string[]][] = [  // hash|slot
  ['strategy', 'engineering-manager', ['#/strategy|strat-chat', '#/strategy/org|strat-chat', '#/strategy/budget|strat-chat', '#/strategy/market|strat-chat']],
  ['finance', 'release-manager', ['#/finance|fin-chat']],
  ['growth', 'builder-claude', ['#/growth|gr-chat']],
  ['automations', 'builder-openai', ['#/automations|au-chat']],
  ['docs', 'engineering-manager', ['#/docs|docs-chat', '#/library/files|lf-chat']],
  ['updates', 'release-manager', ['#/updates|upd-chat']],
  ['rules', 'builder-claude', ['#/rules|ru-chat']],
];
const ASK_PAGES = ['#/calendar|cal-chat', '#/meetings|meet-chat'];
// Where each page's "From <page>" line leads.
const BACK: Record<string, string> = {strategy: '#/strategy', finance: '#/finance', growth: '#/growth', automations: '#/automations',
  docs: '#/docs', updates: '#/updates', rules: '#/rules'};

// To an address, marking what #main holds now as old: a page's code may still be loading after the address changes, and
// until the page draws, the last page's header (and its Chat) is still there. NEW scopes a selector to the new page.
const NEW = '#main > :not([data-old])';
const at = async (page: Page, hash: string) => {
  await page.evaluate(() => { for (const el of document.querySelectorAll('#main > *')) el.setAttribute('data-old', ''); });
  await go(page, hash);
  await page.waitForFunction(h => location.hash.split('?')[0] === h.split('?')[0], hash, {timeout: t(10000)});
};
const box = (page: Page, sel: string) => page.locator(sel).first().evaluate(el => {
  const r = el.getBoundingClientRect();
  return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), right: Math.round(r.right), bottom: Math.round(r.bottom)};
});
const chatShown = (page: Page) => page.locator('body > aside.pgc').isVisible();
const askShown = (page: Page) => page.evaluate(() => !!document.querySelector('#ask-panel')?.classList.contains('ask-shown'));

run('page-chat-pages', ['org', 'toured'], async ({hub, browser, phone, tag}) => {
  const page = await open(browser, hub, 'dana', {phone, hash: '#/updates'});
  for (const [key, bot] of PAGES) await ok(page, 'PUT', 'page-owners/' + key, {bot});
  const rule = await ok<{id: string}>(page, 'POST', 'rules', {kind: 'internal', text: 'Reply to customers within one business day', who: {all_bots: true}});

  for (const [key, bot, hashes] of PAGES) {
    const name = await page.evaluate(b => actorLabel('bot:' + b), bot);
    for (const [n, entry] of hashes.entries()) {
      const [hash = '', slot = ''] = entry.split('|');
      await at(page, hash);
      const chat = page.locator(`${NEW} .${slot} [data-pgc-open]`);
      await chat.waitFor({timeout: t(15000)});
      assert.equal(await chat.getAttribute('aria-label'), 'Chat with ' + name, `${tag} ${hash}: Chat with its owner`);
      assert.equal(await page.locator('#main [data-ask-open], #docs-ask, #meet-ask').count(), 0, `${tag} ${hash}: no Ask button`);
      const btn = await box(page, `${NEW} .${slot} [data-pgc-open]`);
      assert.ok(btn.y < 140, `${tag} ${hash}: Chat is in the header at the top: ${JSON.stringify(btn)}`);
      if (phone) {
        // AC-11: the face (no word) opens the chat full screen; ‹ returns to the page.
        assert.equal(await chat.locator('.pgc-word').isVisible(), false, `${tag} ${hash}: the face alone`);
        await chat.click();
        await page.locator('aside.pgc.pgc-full .pgc-name').waitFor({timeout: t(10000)});
        assert.equal(await page.locator('aside.pgc .pgc-name').innerText(), name);
        await shot(page, `page-chat-${tag}-${hash.slice(2).replace(/\//g, '-')}`);
        await page.locator('aside.pgc [data-pgc-close]').click();
        await page.locator('aside.pgc').waitFor({state: 'hidden', timeout: t(5000)});
        // ‹ goes back through the chat's own history entry: wait for it before the next address.
        await page.waitForFunction(() => !history.state?.pageChat, null, {timeout: t(5000)});
        assert.equal(await page.evaluate(() => location.hash.split('?')[0]), hash, `${tag} ${hash}: back on the page`);
        assert.equal(await sideways(page), false, `${tag} ${hash}: no sideways scroll`);
        continue;
      }
      assert.ok(btn.x > 1440 / 2, `${tag} ${hash}: Chat at the right: ${JSON.stringify(btn)}`);
      // Strategy's tabs, and Docs and Files, share one chat: opened on the first, it is still open on the next.
      const first = n === 0;
      if (first) {
        assert.equal(await chatShown(page), false, `${tag} ${hash}: closed until asked for`);
        const before = await box(page, '#main');
        await chat.click();
        await page.locator('aside.pgc .pgc-name').waitFor({timeout: t(10000)});
        // Below 1600 px the column floats: the page keeps its width under it.
        assert.deepEqual(await box(page, '#main'), before, `${tag} ${hash}: the page keeps its width`);
      }
      assert.equal(await chatShown(page), true, `${tag} ${hash}: the chat is open`);
      assert.equal(await page.locator('aside.pgc .pgc-name').innerText(), name, `${tag} ${hash}: the owner's chat`);
      assert.equal(await chat.getAttribute('aria-pressed'), 'true');
      await page.locator('aside.pgc .thv-composer').waitFor({timeout: t(10000)});
      await shot(page, `page-chat-${tag}-${hash.slice(2).replace(/\//g, '-')}`);
    }
    // The "From <page>" line under a message sent from the page links back to it.
    if (!phone) assert.equal(await page.evaluate(k => THV_FROM_PAGES[k]?.(), key), BACK[key], `${key}: From links to its page`);
  }

  // Calendar and Meetings: Chat with the Assistant's face opens Ask; no Ask button of their own, no page chat.
  const assistant = await page.evaluate(() => askName());
  for (const entry of ASK_PAGES) {
    const [hash = '', slot = ''] = entry.split('|');
    await at(page, hash);
    const chat = page.locator(`${NEW} .${slot} [data-pgc-ask]`);
    await chat.waitFor({timeout: t(15000)});
    assert.equal(await chat.getAttribute('aria-label'), 'Chat with ' + assistant, `${tag} ${hash}: Chat with the Assistant`);
    assert.equal(await page.locator('#main [data-ask-open], #meet-ask, #main [data-pgc-open]').count(), 0, `${tag} ${hash}: no Ask button, no page chat`);
    assert.equal(await askShown(page), false);
    await chat.click();
    await page.waitForFunction(() => document.querySelector('#ask-panel')?.classList.contains('ask-shown'), null, {timeout: t(5000)});
    assert.equal(await chatShown(page), false, `${tag} ${hash}: Ask, not a second chat`);
    assert.equal(await chat.getAttribute('aria-pressed'), 'true', `${tag} ${hash}: pressed while Ask is open`);
    await shot(page, `page-chat-${tag}-${hash.slice(2)}-ask`);
    await page.locator('#ask-panel [data-ask-close]').first().click();
    await page.waitForFunction(() => !document.querySelector('#ask-panel')?.classList.contains('ask-shown'), null, {timeout: t(5000)});
    assert.equal(await chat.getAttribute('aria-pressed'), 'false', `${tag} ${hash}: not pressed once Ask closes`);
    assert.equal(await sideways(page), false, `${tag} ${hash}: no sideways scroll`);
  }
  if (phone) return;

  // AC-10: Rules with a rule open: Chat closes the rule's detail and takes its place; opening a rule closes the chat.
  await at(page, '#/rules');
  await page.locator('#main .ru-chat [data-pgc-open]').waitFor({timeout: t(15000)});
  // Kept open from above; below 1600 px the open column floats over the header's right, Chat included: › closes it.
  if (await chatShown(page)) await page.locator('aside.pgc [data-pgc-close]').click();
  await page.locator('body > aside.pgc').waitFor({state: 'hidden', timeout: t(5000)});
  await at(page, '#/rules?rule=' + rule.id);
  const side = page.locator('#ru-side');
  await side.locator('.ru-side-h').waitFor({timeout: t(10000)});
  await page.locator('#main .ru-chat [data-pgc-open]').click();
  await page.locator('aside.pgc .pgc-name').waitFor({timeout: t(10000)});
  await side.waitFor({state: 'hidden', timeout: t(5000)});
  assert.equal(await page.evaluate(() => new URLSearchParams(location.hash.split('?')[1] || '').get('rule')), null, 'the rule is closed');
  assert.equal(await chatShown(page), true, 'the chat in its place');
  await shot(page, 'page-chat-desktop-rules-swap');
  await at(page, '#/rules?rule=' + rule.id);
  await side.locator('.ru-side-h').waitFor({timeout: t(10000)});
  await page.locator('body > aside.pgc').waitFor({state: 'hidden', timeout: t(5000)});
  assert.equal(await page.locator('#main .ru-chat [data-pgc-open]').getAttribute('aria-pressed'), 'false', 'opening a rule closes the chat');
  // Reloaded with the rule in the address, the rule keeps its place even where the chat was last left open.
  // The owner is read late here, so the chat is set up after the rule has drawn: no later rule opening closes it.
  await page.evaluate(() => localStorage.setItem('tico.page_chat.open.rules', '1'));
  await page.route('**/api/v2/page-owners', async route => { await new Promise(r => setTimeout(r, 1500)); await route.fallback(); });
  await page.reload();
  await side.locator('.ru-side-h').waitFor({timeout: t(15000)});
  await page.locator('#main .ru-chat [data-pgc-open]').waitFor({timeout: t(15000)});
  assert.equal(await chatShown(page), false, 'a rule the address opens keeps its place');
  assert.deepEqual(page.errors, []);
});
