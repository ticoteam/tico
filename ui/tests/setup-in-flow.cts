// Setup in Flow (ui/app/setup-gaps.ts) on the Strategy fake (support/strategy-fake.cts): the Strategy tabs carry Flow's
// mark for each setup area GET /v2/setup/gaps says waits on the owner (Market while the market is empty, Strategy while
// the brand is). A tap on a mark stays on the page, pings that area's bot (POST /v2/flow/ping, scope setup, the area)
// and opens Flow on that bot alone, where its ❓ lands as its chat; an empty Market's one button does the same. The `setup` event a filling write rings reads the
// gaps again, and the mark goes. A member is shown none. On that bot alone the chat's title row leaves its name out (the
// crumb names it); All mine puts the name back. A computer, then a phone; screenshots (TICO_UI_SHOTS) of each in light and dark.
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
    const soloTitles: [string, string | null][] = [];   // each width's title row while Flow is on Librarian alone
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

        // The empty Market's one action does what the mark's tap does.
        await page.evaluate(() => { location.hash = '#/strategy/market'; });
        const map = page.locator('#market-shell [data-setup-act="market"] button[data-setup-area="market"]');
        await map.waitFor();
        assert.ok(!(await sideways(page)), tag + ': the empty Market does not scroll sideways');
        await themed(page, `setup-market-${name}`);

        // A tap on Market's mark (on a phone, Market's button): the page stays, Librarian pinged for Market, Flow on
        // Librarian with its ❓.
        const hash = name === 'phone' ? '#/strategy/market' : '#/strategy';
        if (name !== 'phone') {
          await page.evaluate(() => { location.hash = '#/strategy'; });
          await page.locator('#strat-body').waitFor();
        }
        const pinged = page.waitForResponse(r => r.url().endsWith('/api/v2/flow/ping'));
        await (name === 'phone' ? map : page.locator(tabMark('market'))).click();
        await page.waitForFunction(() => document.querySelector('#need-card[open]'));
        assert.equal(await page.evaluate(() => location.hash), hash, tag + ': the tap stays on the page');
        await pinged;
        assert.deepEqual(writes.filter(w => w.path === '/api/v2/flow/ping').map(w => w.body), [{scope: 'setup', id: 'market'}], tag + ': one ping, for Market');
        await page.evaluate(() => liveEmit('needs', {version: 'v-setup'}));
        const q = page.locator('#need-card[open] .dc-side .dc-chat [data-message="m-q-market"] .qmark');
        await q.waitFor();
        assert.ok(await q.isVisible(), tag + ': the bot\'s ❓ is in view');
        assert.match(await page.locator('#need-card[open] .dc-crumb').innerText(), /Librarian/, tag + ': Flow on the Librarian');
        // The crumb names Librarian, so the chat's title row leaves the name out (checked after both widths, so both
        // widths' screens are kept whatever the title says).
        soloTitles.push([tag, await page.locator('#need-card[open] .dc-tabs-t').textContent()]);
        await themed(page, `setup-flow-${name}`);
        // All mine, from the crumb's menu: the same ❓ with its name back in the title row.
        await page.locator('#need-card[open] .dc-crumb').click();
        await page.locator('#need-card[open] .dc-menu [role="menuitemradio"]', {hasText: 'All mine'}).click();
        await page.waitForFunction(() => !document.querySelector('#need-card[open] .dc-crumb')?.textContent?.includes('Librarian'));
        assert.equal(await page.locator('#need-card[open] .dc-tabs-t').textContent(), 'Librarian', tag + ': All mine keeps the name');
        await themed(page, `setup-flow-all-mine-${name}`);
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
        assert.equal(await page.locator('#market-shell [data-setup-act] button').count(), 0, tag + ': and Market\'s button with it');
        await themed(page, `setup-closed-${name}`);
        assert.deepEqual(errors, [], tag + ': no page errors');
      } finally { await page.close(); }
    }
    for (const [tag, title] of soloTitles) assert.equal(title, '', tag + ': on Librarian alone the title row leaves its name out');
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
