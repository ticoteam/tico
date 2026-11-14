// A chat on every page, Release 2: the Goals card (ui/assistant.ts `pageCard`, backend/page_chat_cards.py), in the
// Strategy Owner's chat on Strategy › Goals, against the fake hub in support/strategy-fake.cts and the threads fake:
//  - AC-7 the card lists one row per goal with its owner, and one accent button that counts the kept rows ("Add 4
//    goals"); × drops a row (it stays, struck, with ↺ to bring it back) and the button counts 3; Cancel is on the left;
//  - with every row dropped the button is off; ↺ brings a row back;
//  - Confirm sends the dropped rows' indexes (`drop`) and the card then shows what ran, with no buttons.
//  - at 1680 the chat with its card sits beside the page.
// TICO_UI_SHOTS=<dir> keeps the screenshots (1440 and 1680, light and dark).
// Run: node scripts/ui-tests.cts page-chat-goals-card
import type {Page} from 'playwright';
import type * as StrategyFakeSupport from './support/strategy-fake.cts';
import type * as ThreadsFakeSupport from './support/threads-fake.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {openStrategy, shot} = require('./support/strategy-fake.cts') as StrategyFakeSupport.Api;
const {threadsFake} = require('./support/threads-fake.cts') as ThreadsFakeSupport.Api;

const OWNER = 'strategy-owner';
const GOALS = '#/strategy/goals';
const TITLES = ['120 paying teams by March', 'Churn under 3% a month', 'Ship the Mac app to 50 teams', 'NPS above 40'];
declare const S: {route: string; me?: unknown; emps?: unknown[]};

const step = (title: string) => ({method: 'POST', path: '/api/v2/goals', body: {title, owner: 'human:ana'}});
function action(status = 'pending', kept = TITLES) {
  return {id: 'act-1', status, method: 'POST', path: '/api/v2/assistant/batch', proposer: 'page:goals', proposed_via: 'page:goals',
    by_name: 'Strategy Owner', summary: `Add ${kept.length} goals`, description: `${kept.length} goals, owned by you. Drop any you don't want.`,
    body: {actions: kept.map(step)}, diff: kept.map(t => ({field: t, new: 'Ana'})), result: status === 'done' ? {error: null} : null};
}

let BROWSER: import('playwright').Browser;
async function openCard(theme: 'light' | 'dark', confirms: unknown[], viewport = {width: 1440, height: 900}) {
  const opened = await openStrategy(BROWSER, {viewport, hash: '#/strategy/org'});
  const {page} = opened;
  let state = action();
  const answer = threadsFake({bot: OWNER, me: 'human:ana', messages: [
    {id: 'm-1', sender_id: 'human:ana', body: 'Add these goals:\n' + TITLES.map(t => '- ' + t).join('\n'), refs: {from_page: {page: 'goals', label: 'Goals'}}},
    {id: 'm-2', sender_id: 'bot:' + OWNER, body: 'Needs your OK: Add 4 goals', refs: {assistant: true, action: 'act-1'}}]});
  await page.route('**/api/**', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown) => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/api/v2/page-owners') return json({pages: {goals: {bot: 'bot:' + OWNER, name: 'Goals'}}});
    if (p === '/api/v2/assistant/actions/act-1') return json({action: state});
    if (p === '/api/v2/assistant/actions/act-1/confirm' && method === 'POST') {
      const sent = req.postDataJSON() as {drop?: number[]};
      confirms.push(sent);
      state = action('done', TITLES.filter((_, i) => !(sent.drop || []).includes(i)));
      return json({action: state});
    }
    const out = answer(p, url, method, method === 'GET' ? null : req.postDataJSON?.() ?? null);
    return out === undefined ? route.fallback() : json(out);
  });
  await page.evaluate(t => { localStorage.clear(); localStorage.setItem('tico.theme', t); }, theme);
  await page.reload();
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  await page.evaluate(h => { location.hash = h; }, GOALS);
  await page.locator('.goal-chat [data-pgc-open]').click();
  await page.locator('aside.pgc .pgk[data-action="act-1"]').waitFor();
  return opened;
}

const rows = (page: Page) => page.locator('aside.pgc .pgk-row').evaluateAll(els => els.map(el => ({
  title: el.querySelector('.pgk-t')?.textContent, owner: el.querySelector('.pgk-o')?.textContent,
  dropped: el.classList.contains('pgk-off'), button: el.querySelector('button')?.textContent?.trim()})));
const add = (page: Page) => page.locator('aside.pgc .pgk [data-confirm]');

(async () => {
  BROWSER = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const theme of ['light', 'dark'] as const) {
      const confirms: unknown[] = [];
      const {page, errors} = await openCard(theme, confirms);
      const card = page.locator('aside.pgc .pgk');
      assert.equal(await card.locator('.asst-who').innerText(), 'Strategy Owner asks');
      assert.equal(await card.locator('[data-what]').innerText(), "4 goals, owned by you. Drop any you don't want.");
      assert.deepEqual(await rows(page), TITLES.map(title => ({title, owner: ' · Ana', dropped: false, button: '×'})));
      assert.equal(await add(page).innerText(), 'Add 4 goals');
      // Cancel on the left, the counted accent button on the right.
      const cancel = await card.locator('[data-cancel]').boundingBox(), go = await add(page).boundingBox();
      assert.ok(cancel && go && cancel.x < go.x, 'Cancel left of Add');
      assert.equal(await add(page).evaluate(el => el.classList.contains('primary')), true);
      // × drops the third row: it stays, struck, with ↺; the button counts 3; focus stays on that row's button.
      await card.locator('.pgk-row').nth(2).locator('button').click();
      assert.deepEqual((await rows(page))[2], {title: TITLES[2], owner: ' · Ana', dropped: true, button: '↺'});
      assert.equal(await add(page).innerText(), 'Add 3 goals');
      assert.equal(await page.evaluate(() => document.activeElement?.closest('.pgk-row')?.querySelector('.pgk-t')?.textContent), TITLES[2]);
      // A quiet × / ↺: no box drawn around it.
      assert.equal(await card.locator('.pgk-row button').first().evaluate(el => getComputedStyle(el).borderTopWidth), '0px');
      await shot(page, `page-chat-goals-card-1440-${theme}`);
      if (theme === 'light') {
        // Every row dropped: the button is off. ↺ brings one back.
        for (const i of [0, 1, 3]) await card.locator('.pgk-row').nth(i).locator('button').click();
        assert.equal(await add(page).isDisabled(), true);
        assert.equal(await add(page).innerText(), 'Add 0 goals');
        for (const i of [0, 1, 3]) await card.locator('.pgk-row').nth(i).locator('button').click();
        assert.equal(await add(page).innerText(), 'Add 3 goals');
        // Confirm sends the dropped row; the card shows the three that ran, with no buttons.
        await add(page).click();
        await page.locator('aside.pgc .pgk[data-status="done"]').waitFor();
        assert.deepEqual(confirms, [{drop: [2]}]);
        assert.deepEqual((await rows(page)).map(r => [r.title, r.button]), TITLES.filter((_, i) => i !== 2).map(t => [t, undefined]));
        assert.equal(await page.locator('aside.pgc .pgk button').count(), 0);
        assert.match(await page.locator('aside.pgc .pgk .asst-state').innerText(), /^Done\./);
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
    for (const theme of ['light', 'dark'] as const) {
      // 1680: the column docks beside the page and the card reads the same.
      const {page, errors} = await openCard(theme, [], {width: 1680, height: 1050});
      assert.equal(await page.evaluate(() => document.body.classList.contains('pgc-docked')), true);
      assert.equal(await add(page).innerText(), 'Add 4 goals');
      await shot(page, `page-chat-goals-card-1680-${theme}`);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('page-chat-goals-card: ok');
  } finally {
    await BROWSER.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
