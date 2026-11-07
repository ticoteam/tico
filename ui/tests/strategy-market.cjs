// Strategy › Market, one page (ui/app/redesign-market.ts) against the fake hub in support/strategy-fake.cjs (the Acme
// example): the header and its three tiles, then the five questions in order, each with the briefing's answer line
// when it has one and none when it does not. What changed? lists the items by kind with the bets they touch, and Read
// opens the whole briefing; Who are we up against? marks our row and opens a player's note; What are buyers saying?
// draws posts per day by source over two weeks, the spike lighting its posts, with the destinations under Settings.
// An old view's address scrolls to its question. No briefing and no listening leave those questions out; an empty
// market keeps its empty state. A phone first, then a computer.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {openStrategy, DESKTOP, sideways, shot} = require('./support/strategy-fake.cjs');

const texts = (page, sel) => page.locator(sel).allInnerTexts().then(a => a.map(t => t.trim()));
const QUESTIONS = ['What changed?', 'Where is the money going?', 'Who are we up against?', 'How does it reach buyers?', 'What are buyers saying?'];

// The question is at the top of the page's scroll, or the page is scrolled to its end with the question in view.
const scrolledTo = (page, key) => page.evaluate(k => {
  const shell = document.querySelector('#market-shell'), top = document.querySelector('#mkt-q-' + k).getBoundingClientRect().top - shell.getBoundingClientRect().top;
  return Math.abs(top) <= 30 || (shell.scrollTop >= shell.scrollHeight - shell.clientHeight - 1 && top < shell.clientHeight);
}, key);

async function onePage(page, tag) {
  await page.locator('.mkt-pg').waitFor();
  await page.locator('.mkt-map .mkt-box').first().waitFor();
  assert.ok(await page.evaluate(() => document.querySelector('.strat-tabs').getBoundingClientRect().bottom <= document.querySelector('.mkt-top').getBoundingClientRect().top + document.querySelector('#market-shell').scrollTop + 1), tag + ': the tabs, then the page');
  assert.equal(await page.locator('.mkt-top h2').innerText(), 'Market');
  assert.equal(await page.locator('.mkt-name').innerText(), 'Release tooling, Global', tag + ': the market\'s name');
  assert.equal(await page.locator('.mkt-top a', {hasText: 'Open map'}).getAttribute('href'), '#/strategy/market/map');
  assert.deepEqual(await texts(page, '.mkt-tile-k .mkt-lab'), ['YEARLY SPEND', 'FASTEST GROWING', 'ACME SHARE'], tag + ': three tiles');
  assert.deepEqual(await texts(page, '.mkt-tile-k .mkt-big'), ['$1.2B', 'Release checks', '0.5%']);
  assert.deepEqual(await texts(page, '.mkt-q h3'), QUESTIONS, tag + ': the five questions in order');
  // Answer lines: the briefing answers two; the other three have none.
  assert.equal(await page.locator('#mkt-q-changed .mkt-ans').innerText(), 'Two rivals now bundle release checks into their CI plans.');
  assert.equal(await page.locator('#mkt-q-players .mkt-ans').innerText(), 'Northwind leads on bundles; Globex competes on price.');
  for (const key of ['money', 'reach', 'saying']) assert.equal(await page.locator(`#mkt-q-${key} .mkt-ans`).count(), 0, `${tag}: no ${key} line`);
  // What changed?
  assert.equal(await page.locator('.mkt-items li').count(), 5, tag + ': every item');
  assert.deepEqual(await texts(page, '.mkt-items .mkt-kind'), ['CHANGE', 'CHANGE', 'SIGNAL', 'THEME', 'SIZE']);
  assert.equal(await page.locator('.mkt-items li').first().locator('.mkt-bet').innerText(), 'Bet 1', tag + ': the item tags its bet');
  assert.equal(await page.locator('.mkt-items .mkt-bet').count(), 1, tag + ': only that item');
  assert.equal(await page.locator('.mkt-bet').getAttribute('href'), '#/strategy');
  // Who are we up against?
  assert.deepEqual(await texts(page, '.mkt-pl tbody .mkt-pn'), ['Northwind', 'Globex', 'Acme'], tag + ': biggest first');
  assert.equal(await page.locator('.mkt-pl tr.us .mkt-pn').innerText(), 'Acme', tag + ': our row');
  assert.match(await page.locator('.mkt-pl tbody tr').first().innerText(), /\$70M[\s\S]*Northwind bundled release checks/, tag + ': revenue over both segments, the last move');
  assert.equal(await page.locator('#mkt-q-players .mkt-more').innerText(), '3 tracked');
  // How does it reach buyers? Empty layers are left out; a buyer segment carries its spend.
  assert.deepEqual(await texts(page, '.mkt-layer h4'), ['SUPPLIERS', 'PLATFORMS', 'US AND RIVALS', 'BUYERS']);
  assert.equal(await page.locator('.mkt-node[data-node="segment/large-teams"] b').innerText(), '$700M');
  // What are buyers saying?
  assert.equal(await page.locator('.rdm-bars .rdm-col').count(), 14, tag + ': two weeks of days');
  assert.deepEqual(await texts(page, '#rdm-leg > span'), ['X', 'Reddit', 'Hacker News', 'News'], tag + ': sources, biggest first');
  assert.equal(await page.locator('.rdm-spk').innerText(), '58', tag + ': the spike is marked with its count');
  assert.equal(await page.locator('.rdm-row').count(), 5, tag + ': one line per recent post');
  const first = page.locator('.rdm-row').first();
  assert.match(await first.locator('.rdm-t').innerText(), /^Anyone moved their release checks to Acme\?/, tag + ': newest first');
  assert.equal(await first.getAttribute('href'), 'https://reddit.example/r/devops/acme-1', tag + ': a line opens the post');
  assert.ok(await page.locator('.rdm-row').evaluateAll(rows => rows.every(r => r.getBoundingClientRect().height <= 44)), tag + ': one line each');
  await page.locator('.rdm-spk').click();
  assert.deepEqual(await page.locator('.rdm-row.hl').evaluateAll(r => r.map(x => x.dataset.rdmItem)), ['li_6', 'li_5'], tag + ': the spike lights its posts');
  assert.equal(await page.locator('[data-listen-dest="leads"]').isVisible(), false, tag + ': destinations folded');
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
      await page.locator('[data-rdm-settings]').click();
      await page.locator('[data-listen-dest="leads"]').waitFor();
      assert.equal(await page.locator('[data-rdm-settings]').getAttribute('aria-expanded'), 'true');
      assert.equal(await sideways(page), false, tag + ': settings open, no sideways scroll');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A computer, from an old Listening link: the same page, scrolled to its question; Briefing goes back up to its own.
    {
      const tag = 'desktop';
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy/market/listening'});
      await onePage(page, tag);
      assert.ok(await scrolledTo(page, 'saying'), tag + ': Listening scrolls to What are buyers saying?');
      assert.equal(await page.locator('.mkt-two').evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length), 2, tag + ': the treemap beside Size vs growth');
      assert.equal(await page.locator('.rdm-row').first().locator('.rdm-d').innerText(), 'leads', tag + ': where a post went');
      await page.evaluate(() => { location.hash = '#/strategy/market/briefing'; });
      await page.waitForFunction(() => Math.abs(document.querySelector('#mkt-q-changed').getBoundingClientRect().top - document.querySelector('#market-shell').getBoundingClientRect().top) <= 30);
      await page.evaluate(() => { location.hash = '#/strategy/market'; });
      await page.waitForFunction(() => document.querySelector('#market-shell').scrollTop === 0);
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
    // An empty market keeps its empty state.
    {
      const {page, errors} = await openStrategy(browser, {hash: '#/strategy/market', market: false});
      await page.locator('.market-shell-blank').waitFor();
      assert.equal(await page.locator('.mkt-pg').count(), 0);
      assert.deepEqual(errors, [], 'empty: no page errors');
      await page.close();
    }
    // Review screenshots, when TICO_UI_SHOTS names a folder: the whole page at 1280 and 400 wide, light and dark, the
    // window as tall as the page so nothing is cut off.
    for (const [name, width] of process.env.TICO_UI_SHOTS ? [['desktop', 1280], ['phone', 400]] : []) {
      const {page} = await openStrategy(browser, {viewport: {width, height: 900}, hash: '#/strategy/market'});
      await page.locator('.mkt-map .mkt-box').first().waitFor();
      for (const theme of ['light', 'dark']) {
        await page.evaluate(t => { document.documentElement.dataset.theme = t; }, theme);
        const height = await page.evaluate(() => { const s = document.querySelector('#market-shell'); return Math.ceil(s.getBoundingClientRect().top + s.scrollHeight + 80); });
        await page.setViewportSize({width, height});
        await page.waitForTimeout(300);
        await shot(page, `market-${name}-${theme}`);
      }
      await page.close();
    }
    console.log('strategy market ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
