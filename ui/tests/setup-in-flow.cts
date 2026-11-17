// Setup in Flow (ui/app/setup-gaps.ts) on the Strategy fake (support/strategy-fake.cts): the Strategy tabs carry Flow's
// mark for each setup area GET /v2/setup/gaps says waits on the owner (Market while the market is empty, Strategy while
// the brand is). A tap on a mark stays on the page, pings that area's bot (POST /v2/flow/ping, scope setup, the area)
// and opens Flow on that bot alone, where its ❓ lands as its chat. The `setup` event a filling write rings reads the
// gaps again, and the mark goes. A member is shown none. A computer, then a phone; screenshots (TICO_UI_SHOTS) of each
// in light and dark.
import type {Page} from 'playwright';
import type * as StrategyFakeSupport from './support/strategy-fake.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {openStrategy, DESKTOP, PHONE, sideways, shot} = require('./support/strategy-fake.cts') as StrategyFakeSupport.Api;
declare function liveEmit(topic: string, data: unknown): void;
declare const SETUP_GAPS: {list: unknown[]};

const MARK = '.needs-mark[aria-label="Needs you"]';
const tabMark = (area: string) => `.strat-tabs [data-strat-tab="${area}"] ${MARK}`;
const themed = async (page: Page, name: string) => {
  for (const theme of ['light', 'dark']) {
    await page.evaluate(t => { document.documentElement.dataset.theme = t; }, theme);
    await page.waitForTimeout(150);
    await shot(page, `${name}-${theme}`);
  }
};

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const [name, viewport] of [['desktop', DESKTOP], ['phone', PHONE]] as const) {
      const tag = name;
      const {page, errors, writes, db} = await openStrategy(browser, {viewport, hash: '#/strategy', market: false});
      try {
        await page.locator(tabMark('market')).waitFor();
        await page.locator(tabMark('strategy')).waitFor();
        assert.equal(await page.locator(`.strat-tabs ${MARK}`).count(), 2, tag + ': Market and Strategy wait on the owner, nothing else');
        assert.equal(await page.locator(tabMark('market')).getAttribute('data-setup-area'), 'market');
        assert.ok(!(await sideways(page)), tag + ': nothing scrolls sideways');
        await themed(page, `setup-marks-${name}`);

        // A tap on Market's mark: still on Strategy, Librarian pinged for Market, Flow on Librarian with its ❓.
        const pinged = page.waitForResponse(r => r.url().endsWith('/api/v2/flow/ping'));
        await page.locator(tabMark('market')).click();
        await page.waitForFunction(() => document.querySelector('#need-card[open]'));
        assert.equal(await page.evaluate(() => location.hash), '#/strategy', tag + ': the tap does not follow the tab');
        await pinged;
        assert.deepEqual(writes.filter(w => w.path === '/api/v2/flow/ping').map(w => w.body), [{scope: 'setup', id: 'market'}], tag + ': one ping, for Market');
        await page.evaluate(() => liveEmit('needs', {version: 'v-setup'}));
        const q = page.locator('#need-card[open] .dc-side .dc-chat [data-message="m-q-market"] .qmark');
        await q.waitFor();
        assert.ok(await q.isVisible(), tag + ': the bot\'s ❓ is in view');
        assert.match(await page.locator('#need-card[open] .dc-crumb').innerText(), /Librarian/, tag + ': Flow on the Librarian');
        await themed(page, `setup-flow-${name}`);
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => !document.querySelector('#need-card[open]'));

        // Market is filled (by the Librarian, from the reply): its `setup` event takes the mark away; Strategy's stays.
        db.filled.add('market');
        await page.evaluate(() => liveEmit('setup', {area: 'market'}));
        await page.waitForFunction(m => !document.querySelector(m), tabMark('market'));
        assert.equal(await page.locator(`.strat-tabs ${MARK}`).count(), 1, tag + ': only Strategy is still marked');
        await page.evaluate(() => { location.hash = '#/strategy/market'; });
        await page.locator('.strat-tabs [data-strat-tab="market"][aria-current="page"]').waitFor();
        await page.locator('#market-shell').waitFor();
        assert.equal(await page.locator(tabMark('market')).count(), 0, tag + ': and stays gone on the next tab');
        await themed(page, `setup-closed-${name}`);
        assert.deepEqual(errors, [], tag + ': no page errors');
      } finally { await page.close(); }
    }
    // A member is not waited on: no mark on any tab.
    {
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy', market: false, viewer: 'ben'});
      try {
        await page.locator('#strat-body').waitFor();
        await page.waitForFunction(() => typeof SETUP_GAPS !== 'undefined' && SETUP_GAPS.list.length > 0);
        assert.equal(await page.locator(MARK).count(), 0, 'member: no mark');
        assert.deepEqual(errors, [], 'member: no page errors');
      } finally { await page.close(); }
    }
    console.log('setup in flow: marks from the gaps, a tap pings the area\'s bot and opens Flow on its ❓, a filled area loses its mark: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
