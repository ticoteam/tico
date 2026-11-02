// Journey: Growth draws seeded data, as Dana. A channel is added through the API (as the owner adds one); the Growth tab
// lists it by name. Runs on a phone and a desktop.
const assert = require('node:assert/strict');
const {journey, open, must, assertClean, drawn} = require('./support/e2e.cjs');

const CHANNEL = 'Search ads';

journey('e2e-growth', async ({hub, browser, phone}) => {
  const where = phone ? 'phone' : 'desktop';
  await must(hub, 'dana', 'POST', 'growth/channels', {slug: 'search-ads', name: CHANNEL, track: 'performance',
    stage: 'testing', why: 'Pays back fast on small teams.'}, 'add a channel');

  const page = await open(browser, hub, 'dana', {phone, hash: '#/strategy/growth/channels'});
  const text = await drawn(page, '#main', text => text.includes(CHANNEL), `${where}: the channel is drawn`);
  assert.match(text, new RegExp(CHANNEL), `${where}: the channel is drawn`);
  assertClean(page, `${where}: growth`);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, `${where}: growth scrolls sideways by ${overflow}px`);
});
