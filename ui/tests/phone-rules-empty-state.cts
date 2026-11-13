// Phone rule 3 (ui/tests/support/phone-rules.cts) flags a waiting state that leads nowhere. An empty state that says
// nothing is waiting ("Nothing waiting on you") waits for nothing, so it is no dead end; a real wait with no way
// forward is still one.
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {chromium}: typeof import('playwright') = require('playwright');
const {audit} = require('./support/phone-rules.cts') as import('./support/phone-rules.cts').Api;

(async () => {
  const channel = process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL;
  const browser = await chromium.launch({headless: true, ...(channel ? {channel} : {})});
  try {
    const page = await browser.newPage({viewport: {width: 400, height: 844}, isMobile: true, hasTouch: true});
    await page.setContent(`<meta name="viewport" content="width=device-width, initial-scale=1">
      <main><section class="card"><p class="muted">Nothing waiting on you</p></section>
        <section class="card"><p>Waiting on the builder's review</p></section></main>`);
    const found = (await page.evaluate(audit, 'as the screen opens')).filter(b => b.rule === 3).map(b => b.element);
    assert.deepEqual(found, ['p "Waiting on the builder\'s review"'], 'only the real wait is a dead end');
    console.log('phone-rules-empty-state: an empty state is not a dead end; a wait with no way forward still is');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
