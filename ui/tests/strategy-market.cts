// Strategy › Market, one page (ui/app/redesign-market.ts) against the fake hub in support/strategy-fake.cts (the Acme
// example): the header and its three tiles, then the five questions in order, each with the briefing's answer line
// when it has one and none when it does not. What changed? lists the items by kind with the bets they touch, and Read
// opens the whole briefing; Who are we up against? marks our row and opens a player's note; What are buyers saying?
// draws posts per day by source over two weeks, the spike lighting its posts, with a link to Listening (#/listening) for an admin.
// An old view's address scrolls to its question. No briefing and no listening leave those questions out; an empty
// market keeps its empty state. A phone first, then a computer.
import type {Page} from 'playwright';
import type * as StrategyFakeSupport from './support/strategy-fake.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {openStrategy, DESKTOP, sideways, shot} = require('./support/strategy-fake.cts') as StrategyFakeSupport.Api;
const fs = require('node:fs');
const path = require('node:path');
declare function liveEmit(topic: string, data: unknown): void;

const texts = (page: Page, sel: string) => page.locator(sel).allInnerTexts().then(a => a.map(t => t.trim()));
const QUESTIONS = ['What changed?', 'Where is the money going?', 'Who are we up against?', 'How does it reach buyers?', 'What are buyers saying?'];

// The question is at the top of the page's scroll, or the page is scrolled to its end with the question in view.
const scrolledTo = (page: Page, key: string) => page.evaluate((k: string) => {
  const shell = document.querySelector<HTMLElement>('#market-shell')!, top = document.querySelector<HTMLElement>('#mkt-q-' + k)!.getBoundingClientRect().top - shell.getBoundingClientRect().top;
  return Math.abs(top) <= 30 || (shell.scrollTop >= shell.scrollHeight - shell.clientHeight - 1 && top < shell.clientHeight);
}, key);

async function onePage(page: Page, tag: string) {
  await page.locator('.mkt-pg').waitFor();
  await page.locator('.mkt-map .mkt-box').first().waitFor();
  // One title, the Strategy header's: no second "Market" heading over the page. What was beside it lives in the
  // header's ⋯, last on the line: Open map, and the Definition, which names the market.
  assert.equal(await page.locator('#main h1, #main .mkt-top').count(), 1, tag + ': one title');
  assert.ok(await page.evaluate(() => document.querySelector<HTMLElement>('.strat-tabs')!.getBoundingClientRect().bottom <= document.querySelector<HTMLElement>('.mkt-pg')!.getBoundingClientRect().top + document.querySelector<HTMLElement>('#market-shell')!.scrollTop + 1), tag + ': the tabs, then the page');
  await page.locator('.rd-strat-head > .ph-more').click();
  const menu = page.locator('.rd-strat-head > .ph-menu');
  assert.equal(await menu.locator('[data-market-map]').getAttribute('href'), '#/strategy/market/map', tag + ': Open map in ⋯');
  await menu.locator('[data-mkt-def]').click();
  const def = page.locator('.mkt-panel[open]');
  await def.waitFor();
  assert.equal(await def.locator('.mkt-ph small').innerText(), 'Release tooling, Global', tag + ': the Definition names the market');
  await page.keyboard.press('Escape');
  await def.waitFor({state: 'hidden'});
  assert.deepEqual(await texts(page, '.mkt-tile-k .mkt-lab'), ['Yearly spend', 'Fastest growing', 'Acme share'], tag + ': three tiles, named in sentence case');
  assert.deepEqual(await texts(page, '.mkt-tile-k .mkt-big'), ['$1.2B', 'Release checks', '0.5%']);
  assert.deepEqual(await texts(page, '.mkt-q h3'), QUESTIONS, tag + ': the five questions in order');
  // Answer lines: the briefing answers two; the other three have none.
  assert.equal(await page.locator('#mkt-q-changed .mkt-ans').innerText(), 'Two rivals now bundle release checks into their CI plans.');
  assert.equal(await page.locator('#mkt-q-players .mkt-ans').innerText(), 'Northwind leads on bundles; Globex competes on price.');
  for (const key of ['money', 'reach', 'saying']) assert.equal(await page.locator(`#mkt-q-${key} .mkt-ans`).count(), 0, `${tag}: no ${key} line`);
  // What changed?
  assert.equal(await page.locator('.mkt-items li').count(), 5, tag + ': every item');
  assert.deepEqual(await texts(page, '.mkt-items .mkt-kind'), ['Change', 'Change', 'Signal', 'Theme', 'Size']);
  assert.equal(await page.locator('.mkt-items li').first().locator('.mkt-bet').innerText(), 'Bet 1', tag + ': the item tags its bet');
  assert.equal(await page.locator('.mkt-items .mkt-bet').count(), 1, tag + ': only that item');
  assert.equal(await page.locator('.mkt-bet').getAttribute('href'), '#/strategy');
  // Who are we up against?
  assert.deepEqual(await texts(page, '.mkt-pl tbody .mkt-pn'), ['Northwind', 'Globex', 'Acme'], tag + ': biggest first');
  assert.equal(await page.locator('.mkt-pl tr.us .mkt-pn').innerText(), 'Acme', tag + ': our row');
  assert.match(await page.locator('.mkt-pl tbody tr').first().innerText(), /\$70M[\s\S]*Northwind bundled release checks/, tag + ': revenue over both segments, the last move');
  assert.equal(await page.locator('#mkt-q-players .mkt-more').innerText(), '3 tracked');
  // How does it reach buyers? Empty layers are left out; a buyer segment carries its spend.
  assert.deepEqual(await texts(page, '.mkt-layer h4'), ['Suppliers', 'Platforms', 'Us and rivals', 'Buyers']);
  assert.equal(await page.locator('.mkt-node[data-node="segment/large-teams"] b').innerText(), '$700M');
  // What are buyers saying?
  assert.equal(await page.locator('.rdm-bars .rdm-col').count(), 14, tag + ': two weeks of days');
  assert.deepEqual(await texts(page, '#rdm-leg > span'), ['X', 'Reddit', 'Hacker News', 'News'], tag + ': sources, biggest first');
  assert.equal(await page.locator('.rdm-spk').innerText(), '58', tag + ': the spike is marked with its count');
  assert.equal(await page.locator('.rdm-row').count(), 5, tag + ': one line per recent post');
  const first = page.locator('.rdm-row').first();
  assert.match(await first.locator('.rdm-t').innerText(), /^Anyone moved their release checks to Acme\?/, tag + ': newest first');
  assert.equal(await first.getAttribute('href'), 'https://reddit.example/r/devops/acme-1', tag + ': a line opens the post');
  assert.ok(await page.locator('.rdm-row').evaluateAll((rows: Element[]) => rows.every(r => r.getBoundingClientRect().height <= 44)), tag + ': one line each');
  await page.locator('.rdm-spk').click();
  assert.deepEqual(await page.locator('.rdm-row.hl').evaluateAll((r: Element[]) => r.map(x => (x as HTMLElement).dataset.rdmItem)), ['li_6', 'li_5'], tag + ': the spike lights its posts');
  assert.equal(await page.locator('#mkt-q-saying a[href="#/listening"]').count(), 1, tag + ': Listening is a link to its page');
  assert.equal(await page.locator('#set-listening, [data-rdm-settings]').count(), 0, tag + ': no settings form here');
  assert.equal(await sideways(page), false, tag + ': no sideways scroll');
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // A phone.
    {
      const tag = 'phone';
      const {page, errors} = await openStrategy(browser, {hash: '#/strategy/market'});
      await onePage(page, tag);
      assert.equal(await page.locator('.mkt-two').evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length), 1, tag + ': one column');
      assert.equal(await page.locator('.mkt-tbl').evaluate(el => getComputedStyle(el).overflowX), 'auto', tag + ': the table scrolls in its own box');
      assert.equal(await page.locator('.rdm-d').first().isVisible(), false, tag + ': destinations leave the line on a phone');
      await shot(page, 'phone-market');
      await page.locator('[data-rdb-read]').click();
      await page.locator('dialog.brief-panel[open]').waitFor();
      assert.match(await page.locator('dialog.brief-panel .market-md').innerText(), /Northwind bundled checks/, tag + ': Read opens the page');
      await page.locator('dialog.brief-panel .tmodal-x').click();
      await page.locator('.mkt-pl .mkt-pn', {hasText: 'Northwind'}).click();
      await page.locator('dialog.mkt-note[open] h1', {hasText: 'Northwind'}).waitFor();
      assert.equal(await sideways(page), false, tag + ': the note open, no sideways scroll');
      await page.locator('dialog.mkt-note .tmodal-x').click();
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A computer, from an old Spend link: the same page, scrolled to its question; Briefing goes back up to its own.
    {
      const tag = 'desktop';
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy/market/spend'});
      await onePage(page, tag);
      assert.ok(await scrolledTo(page, 'money'), tag + ': Spend scrolls to Where is the money going?');
      assert.equal(await page.locator('.mkt-two').evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length), 2, tag + ': the treemap beside Size vs growth');
      assert.equal(await page.locator('.rdm-row').first().locator('.rdm-d').innerText(), 'leads', tag + ': where a post went');
      await page.evaluate(() => { location.hash = '#/strategy/market/briefing'; });
      await page.waitForFunction(() => Math.abs(document.querySelector<HTMLElement>('#mkt-q-changed')!.getBoundingClientRect().top - document.querySelector<HTMLElement>('#market-shell')!.getBoundingClientRect().top) <= 30);
      await page.evaluate(() => { location.hash = '#/strategy/market'; });
      await page.waitForFunction(() => document.querySelector<HTMLElement>('#market-shell')!.scrollTop === 0);
      await shot(page, 'desktop-market');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // No briefing, and the stats not shared: those two questions are left out and the rest stand.
    {
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy/market', briefing: false, listening: 403});
      await page.locator('.mkt-pg #mkt-q-money').waitFor();
      assert.deepEqual(await texts(page, '.mkt-q h3'), QUESTIONS.slice(1, 4));
      assert.equal(await page.locator('.mkt-ans').count(), 0, 'no briefing, no answer lines');
      assert.equal(await page.locator('.mkt-pl .mkt-move').count(), 0, 'no last moves without a briefing');
      assert.deepEqual(errors, [], 'no briefing: no page errors');
      await page.close();
    }
    // A slow Market left for Strategy before its reads come back: when they land, Market's ⋯ (Open map, Definition) is
    // not written into Strategy's header, and Strategy's own ⋯ (Lenses) stays.
    {
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy/org'});
      let release!: (value?: unknown) => void;
      const gate = new Promise(resolve => { release = resolve; });
      let held = 0;
      await page.route('**/api/v2/market/spend*', async route => { held++; await gate; await route.fallback(); });
      await page.evaluate(() => { location.hash = '#/strategy/market'; });
      await page.waitForFunction(() => document.querySelector('#market-shell'));
      for (let i = 0; i < 100 && !held; i++) await page.waitForTimeout(20);
      assert.ok(held > 0, 'the market reads are held');
      await page.evaluate(() => { location.hash = '#/strategy'; });
      await page.locator('#strat-body').waitFor();
      release();
      await page.waitForTimeout(600);   // the held reads land and the old Market mount finishes
      await page.locator('.rd-strat-head > .ph-more').click();
      const menu = page.locator('.rd-strat-head > .ph-menu');
      assert.equal(await menu.locator('[data-strat-lenses]').count(), 1, 'Strategy keeps its own ⋯ items');
      assert.equal(await menu.locator('[data-market-map], [data-mkt-def]').count(), 0, 'nothing of Market\'s in it');
      assert.equal(await page.locator('.mkt-pg').count(), 0, 'and no market page drawn over Strategy');
      assert.deepEqual(errors, [], 'slow market: no page errors');
      await page.close();
    }
    // A market not sized yet still names itself: Definition is in ⋯ and its panel says the market's name.
    {
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy/org'});
      await page.route('**/api/v2/market/spend*', route => route.fulfill({contentType: 'application/json',
        body: JSON.stringify({name: 'Release tooling', definition: {geography: 'Global', description: 'What teams spend on release checks'}, segments: []})}));
      await page.evaluate(() => { location.hash = '#/strategy/market'; });
      await page.locator('.mkt-pg').waitFor();
      assert.equal(await page.locator('.mkt-map').count(), 0, 'nothing sized, no treemap');
      await page.locator('.rd-strat-head > .ph-more').click();
      await page.locator('.rd-strat-head > .ph-menu [data-mkt-def]').click();
      const def = page.locator('.mkt-panel[open]');
      await def.waitFor();
      assert.equal(await def.locator('.mkt-ph small').innerText(), 'Release tooling, Global', 'the name, with no money drawn');
      assert.match(await def.innerText(), /What teams spend on release checks/);
      await def.locator('.tmodal-x').click();
      await def.waitFor({state: 'detached'});
      assert.deepEqual(errors, [], 'unsized: no page errors');
      await page.close();
    }
    // An empty market keeps its empty state.
    {
      const {page, errors} = await openStrategy(browser, {hash: '#/strategy/market', market: false});
      await page.locator('.market-shell-blank').waitFor();
      assert.equal(await page.locator('.mkt-pg').count(), 0);
      assert.deepEqual(errors, [], 'empty: no page errors');
      await page.close();
    }
    // The Flow mark on the question still waiting on the owner: Where is the money going? before anything is sized.
    // Starting the sizing takes its form away, so the mark goes and stays gone once the sizes arrive. The tabs carry
    // what GET /v2/setup/gaps says (ui/app/setup-gaps.ts): Strategy's while the brand is empty, on every tab, and
    // none on Market's, whose market is set up; a member sees none.
    {
      const tag = 'marks', mark = '.needs-mark[aria-label="Needs you"]';
      const {page, errors, writes, db} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy/market', sized: false});
      await page.locator('#mkt-q-money [data-mkt-size]').waitFor();
      await page.locator(`.strat-tabs [data-strat-tab="strategy"] ${mark}`).waitFor();
      assert.equal(await page.locator(`#mkt-q-money h3 ${mark}`).count(), 1, tag + ': the sizing question is marked');
      assert.equal(await page.locator(`.strat-tabs [data-strat-tab="market"] ${mark}`).count(), 0, tag + ': Market is set up, so its tab is not marked');
      assert.equal(await page.locator(mark).count(), 2, tag + ': nothing else on the page is');
      await page.evaluate(() => { location.hash = '#/strategy'; });
      await page.locator('#strat-setup .needs-mark').waitFor();
      assert.equal(await page.locator(`.strat-tabs [data-strat-tab="strategy"] ${mark}`).count(), 1, tag + ': Strategy is marked while the owner has no brand');
      assert.equal(await page.locator(`.strat-tabs [data-strat-tab="market"] ${mark}`).count(), 0, tag + ': Market\'s tab is still not');
      await page.evaluate(() => { location.hash = '#/strategy/market'; });
      await page.locator('[data-mkt-size]').waitFor();
      await page.locator('[data-mkt-size] input[name="description"]').fill('Release tooling for small teams');
      await page.locator('[data-mkt-size] [type="submit"]').click();
      await page.locator('[data-mkt-researching]').waitFor();
      assert.equal(await page.locator(`#mkt-q-money h3 ${mark}`).count(), 0, tag + ': sizing started, so the section is no longer marked');
      assert.equal(await page.locator(`.strat-tabs [data-strat-tab="strategy"] ${mark}`).count(), 1, tag + ': Strategy keeps its mark, its brand is still unset');
      assert.deepEqual(writes.filter(w => w.method === 'POST').map(w => [w.path, w.body]), [['/api/v2/market/spend/research', {description: 'Release tooling for small teams'}]], tag + ': the sizing is filed once');
      // A reload keeps the open task: the server has it, not the page.
      await page.reload();
      await page.locator('#mkt-q-money [data-mkt-researching] a[href="#/task/task-sizing"]').waitFor();
      assert.equal(await page.locator(`#mkt-q-money h3 ${mark}`).count(), 0, tag + ': still not marked after a reload');
      db.market.sized = true; db.market.sizing = null;
      await page.reload();
      await page.locator('.mkt-map .mkt-box').first().waitFor();
      await page.locator(`.strat-tabs [data-strat-tab="strategy"] ${mark}`).waitFor();
      assert.equal(await page.locator(mark).count(), 1, tag + ': sized, no mark on the page, only Strategy\'s tab');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // Saving the brand (the Set up brand button) clears the row's mark as soon as it is saved and the Strategy tab's on the
    // `setup` event the save rings (backend/events.py), and a return to Strategy paints the saved brand: no row, no mark.
    {
      const tag = 'brand', mark = '.needs-mark[aria-label="Needs you"]';
      const {page, errors, writes} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy'});
      await page.locator('#strat-setup .needs-mark').waitFor();
      await page.locator(`.strat-tabs [data-strat-tab="strategy"] ${mark}`).waitFor();
      assert.equal(await page.locator(`.strat-tabs [data-strat-tab="strategy"] ${mark}`).count(), 1, tag + ': Strategy is marked before the save');
      await page.locator('#strat-setup a').click();
      await page.locator('form[data-sec="positioning"] input[name="positioning"]').fill('Plain tools for small teams');
      const saved = page.waitForResponse(r => r.url().endsWith('/api/v2/brand/draft'));
      await page.locator('form[data-sec="positioning"] [type="submit"]').click();
      await saved;
      await page.evaluate(() => liveEmit('setup', {area: 'strategy'}));
      await page.waitForFunction(() => !document.querySelector('.strat-tabs [data-strat-tab="strategy"] .needs-mark'));
      assert.equal(await page.locator(`.strat-tabs [data-strat-tab="strategy"] ${mark}`).count(), 0, tag + ': the Strategy tab is unmarked once the brand is saved');
      const puts = writes.filter(w => w.method === 'PUT');
      assert.equal(puts.length, 1, tag + ': one section saved');
      assert.equal(puts[0]?.path, '/api/v2/brand/draft', tag + ': saved into the brand draft');
      assert.equal((puts[0]?.body as {sections: {positioning: string}}).sections.positioning, 'Plain tools for small teams', tag + ': the positioning is what was typed');
      const read = page.waitForResponse(r => r.url().endsWith('/api/v2/brand') && r.request().method() === 'GET');
      await page.evaluate(() => { location.hash = '#/strategy'; });
      await read;
      await page.locator('#strat-brand .br-strip').waitFor();
      assert.equal(await page.locator('#strat-setup .needs-mark').count(), 0, tag + ': back on Strategy, no setup mark');
      assert.equal(await page.locator('#strat-setup a').count(), 0, tag + ': back on Strategy, no setup button');
      assert.equal(await page.locator(`.strat-tabs [data-strat-tab="strategy"] ${mark}`).count(), 0, tag + ': back on Strategy, its tab is unmarked');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A member is asked nothing: an unsized market shows no mark on the page or on any tab.
    {
      const tag = 'member', mark = '.needs-mark[aria-label="Needs you"]';
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy/market', sized: false, viewer: 'ben'});
      await page.locator('.mkt-pg').waitFor();
      assert.equal(await page.locator('#mkt-q-money').count(), 0, tag + ': no sizing question for a member');
      assert.equal(await page.locator(mark).count(), 0, tag + ': no mark');
      await page.evaluate(() => { location.hash = '#/strategy'; });
      await page.locator('#strat-body').waitFor();
      assert.equal(await page.locator(mark).count(), 0, tag + ': no mark on Strategy either');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // Market not set up yet, its setup begun: its first question is the owner's to answer, so it and Market's tab are marked.
    {
      const mark = '.needs-mark[aria-label="Needs you"]';
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy/market', market: false, setup: true});
      await page.locator('[data-market-setup]').waitFor();
      await page.locator(`.strat-tabs [data-strat-tab="market"] ${mark}`).waitFor();
      assert.equal(await page.locator(`[data-market-setup] h1 ${mark}`).count(), 1, 'the setup question is marked');
      assert.equal(await page.locator(`.strat-tabs [data-strat-tab="market"] ${mark}`).count(), 1, 'Market\'s tab is marked during setup');
      assert.deepEqual(errors, [], 'setup: no page errors');
      await page.close();
    }
    // Review screenshots, when TICO_UI_SHOTS names a folder: the whole page at 1280 and 400 wide, light and dark, the
    // window as tall as the page so nothing is cut off. Each market before sizing (its marks) and after it (none).
    for (const sized of [true, false]) for (const [name, width] of (process.env.TICO_UI_SHOTS ? [['desktop', 1280], ['phone', 400]] : []) as [string, number][]) {
      const {page} = await openStrategy(browser, {viewport: {width, height: 900}, hash: '#/strategy/market', sized});
      await page.locator(sized ? '.mkt-map .mkt-box' : '[data-mkt-size]').first().waitFor();
      for (const theme of ['light', 'dark']) {
        await page.evaluate(t => { document.documentElement.dataset.theme = t; }, theme);
        const height = await page.evaluate(() => { const s = document.querySelector<HTMLElement>('#market-shell')!; return Math.ceil(s.getBoundingClientRect().top + s.scrollHeight + 80); });
        await page.setViewportSize({width, height});
        await page.waitForTimeout(300);
        await shot(page, `market-${sized ? 'sized' : 'unsized'}-${name}-${theme}`);
      }
      await page.close();
    }
    // The Strategy tab with the brand still empty: the Set up brand button with its mark, desktop in light and a phone in
    // dark, the whole page and a close-up of the row.
    if (process.env.TICO_UI_SHOTS) {
      fs.mkdirSync(process.env.TICO_UI_SHOTS, {recursive: true});
      for (const [name, width, theme] of [['desktop', 1280, 'light'], ['phone', 400, 'dark']] as [string, number, string][]) {
        const {page} = await openStrategy(browser, {viewport: {width, height: 900}, hash: '#/strategy'});
        await page.locator('#strat-setup .needs-mark').waitFor();
        await page.evaluate(t => { document.documentElement.dataset.theme = t; }, theme);
        await page.waitForTimeout(300);
        await page.screenshot({path: path.join(process.env.TICO_UI_SHOTS, `strategy-setup-${name}-${theme}.png`), fullPage: true});
        await page.locator('#strat-setup').screenshot({path: path.join(process.env.TICO_UI_SHOTS, `strategy-setup-row-${name}-${theme}.png`)});
        await page.close();
      }
    }
    console.log('strategy market ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
