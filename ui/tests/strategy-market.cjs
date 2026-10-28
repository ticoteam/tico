// Strategy › Market's Briefing and Listening (ui/app/redesign-market.js) against the fake hub in
// support/strategy-fake.cjs (the Acme example). Briefing: Market opens on it, a front page with the headline, its first
// lines and the items by kind, Read opens the whole page; with none, one line. Listening: posts per day by source over
// two weeks, the spike marked and lighting its posts, one line per recent post, the destinations folded under Settings.
// A phone first, then a computer.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {openStrategy, PHONE, DESKTOP, sideways, shot} = require('./support/strategy-fake.cjs');

const texts = (page, sel) => page.locator(sel).allInnerTexts().then(a => a.map(t => t.trim()));

// On a phone the Strategy tabs sit above the view switch.
const tabsFirst = page => page.evaluate(() => document.querySelector('.strat-tabs').getBoundingClientRect().bottom <= document.querySelector('.mkt-views').getBoundingClientRect().top + 1);

async function briefing(page, tag) {
  await page.locator('.rdb').waitFor();
  assert.ok(await tabsFirst(page), tag + ': the tabs, then the views');
  assert.equal(await page.evaluate(() => location.hash), '#/strategy/market', tag + ': Market opens on the briefing');
  assert.equal(await page.locator('.mkt-views [data-market-view="briefing"][aria-current="page"]').count(), 1, tag + ': Briefing is the first view');
  assert.equal(await page.locator('.rdb-mast').innerText(), 'The Acme Briefing', tag + ': the masthead');
  assert.equal(await page.locator('.rdb-hl').innerText(), 'Release tooling buyers move to bundles', tag + ': the headline');
  assert.match(await page.locator('.rdb-deck').innerText(), /^Two of Acme's rivals now sell release checks/, tag + ': its first lines');
  assert.deepEqual(await texts(page, '.rdb-cols h3'), ['CHANGES', 'SIGNALS', 'THEMES', 'SIZE'], tag + ': items by kind');
  assert.equal(await page.locator('.rdb-cols p').count(), 5, tag + ': every item');
  assert.equal(await sideways(page), false, tag + ': no sideways scroll');
}

async function listening(page, tag) {
  await page.locator('.rdm-row').first().waitFor();
  assert.ok(await tabsFirst(page), tag + ': the tabs, then the views');
  assert.equal(await page.locator('.mkt-views [data-market-view="listening"][aria-current="page"]').count(), 1, tag + ': a Market view');
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
      await briefing(page, tag);
      await shot(page, 'phone-briefing');
      await page.locator('[data-rdb-read]').click();
      await page.locator('dialog.brief-panel[open]').waitFor();
      assert.match(await page.locator('dialog.brief-panel .market-md').innerText(), /Northwind bundled checks/, tag + ': Read opens the page');
      await page.locator('dialog.brief-panel .tmodal-x').click();
      await page.locator('.mkt-views [data-market-view="listening"]').click();
      await listening(page, tag);
      assert.equal(await page.locator('.rdm-d').first().isVisible(), false, tag + ': destinations leave the line on a phone');
      await shot(page, 'phone-listening');
      await page.locator('[data-rdm-settings]').click();
      await page.locator('[data-listen-dest="leads"]').waitFor();
      assert.equal(await page.locator('[data-rdm-settings]').getAttribute('aria-expanded'), 'true');
      assert.equal(await sideways(page), false, tag + ': settings open, no sideways scroll');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A phone, no briefing yet: one line.
    {
      const {page, errors} = await openStrategy(browser, {hash: '#/strategy/market/briefing', briefing: false});
      await page.locator('.rd-empty').waitFor();
      assert.equal(await page.locator('#rdb-host').innerText(), 'No briefing yet.', 'empty: one line');
      assert.equal(await page.locator('.rdb').count(), 0);
      await shot(page, 'phone-briefing-empty');
      assert.deepEqual(errors, [], 'empty: no page errors');
      await page.close();
    }
    // A computer.
    {
      const tag = 'desktop';
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy/market'});
      await briefing(page, tag);
      assert.equal(await page.locator('.rdb-cols').evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length > 1), true, tag + ': items in columns');
      await shot(page, 'desktop-briefing');
      await page.evaluate(() => { location.hash = '#/strategy/market/listening'; });
      await listening(page, tag);
      assert.equal(await page.locator('.rdm-row').first().locator('.rdm-d').innerText(), 'leads', tag + ': where a post went');
      await shot(page, 'desktop-listening');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    // A computer: no briefing; stats not shared.
    {
      const {page, errors} = await openStrategy(browser, {viewport: DESKTOP, hash: '#/strategy/market/briefing', briefing: false, listening: 403});
      await page.locator('.rd-empty').waitFor();
      await shot(page, 'desktop-briefing-empty');
      await page.evaluate(() => { location.hash = '#/strategy/market/listening'; });
      await page.locator('#rdm-body .rdm-none').waitFor();
      assert.equal(await page.locator('#rdm-body').innerText(), 'Not shared with you.', '403: says so');
      assert.deepEqual(errors, [], '403: no page errors');
      await page.close();
    }
    console.log('strategy market ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
