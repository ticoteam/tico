// Journey: Finance draws its books, as Dana. The install's Finance is set up (every install has it; the finance seed runs
// the product's setup), so the page draws the chart of accounts it started with. Runs on a phone and a desktop.
const assert = require('node:assert/strict');
const {journey, open, assertClean, drawn, t} = require('./support/e2e.cjs');

journey('e2e-finance', async ({hub, browser, phone}) => {
  const where = phone ? 'phone' : 'desktop';
  const page = await open(browser, hub, 'dana', {phone, hash: '#/finance'});
  await page.locator('#main', {hasText: 'Revenue'}).first().waitFor({timeout: t(20000)});
  // The balance sheet lists the accounts Finance was set up with.
  await page.locator('#main button, #main a', {hasText: /^Balance sheet$/}).first().click();
  const text = await drawn(page, '#main', text => ['Bank', "Owner's equity"].every(account => text.includes(account)),
    `${where}: the balance sheet draws its accounts`);
  for (const account of ['Bank', "Owner's equity"]) {
    assert.match(text, new RegExp(account), `${where}: balance sheet draws ${account}`);
  }
  assertClean(page, `${where}: finance`);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, `${where}: finance scrolls sideways by ${overflow}px`);
});
