// The real-hub helper (ui/tests/support/real-hub.cjs) on a real hub seeded with the scenario org:
// the owner opens the app on a phone and on a desktop and the shell renders, and a non-owner teammate signs in as
// itself. The reference for writing a real-hub test (ui/README.md).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {startHub, pageAs} = require('./support/real-hub.cjs');
const {t} = require('./support/load.cjs');

const whoami = page => page.evaluate(async () => (await fetch('/api/v2/me', {cache: 'no-store'})).json());

(async () => {
  const hub = await startHub({seeds: ['org']});
  let browser;
  try {
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
    assert.deepEqual(Object.keys(hub.tokens).sort(),
      ['ana', 'builder-claude', 'builder-openai', 'dana', 'engineering-lead', 'omar', 'priya', 'release-manager']);

    for (const phone of [true, false]) {
      const page = await pageAs(browser, hub, 'dana', {phone});
      try {
        await page.waitForFunction(() => {
          const who = document.querySelector('#account .account-email')?.textContent || '';
          return who !== '' && who !== 'Signing in…' && document.querySelector('#main')?.children.length > 0;
        }, null, {timeout: t(20000)});
        assert.equal(await page.evaluate(() => innerWidth), phone ? 390 : 1440, 'the viewport is the one asked for');
        assert.equal(await page.locator('#account .account-email').innerText(), 'dana@acme.example');
        const me = await whoami(page);
        assert.equal(me.actor, 'human:dana');
        assert.equal(me.role, 'owner');
      } finally {
        await page.context().close();
      }
    }

    const page = await pageAs(browser, hub, 'engineering-lead');
    try {
      const me = await whoami(page);
      assert.equal(me.actor, 'bot:engineering-lead', 'the page is signed in as the Engineering Lead');
      assert.equal(me.role, 'bot');
    } finally {
      await page.context().close();
    }
    console.log('real-hub-smoke: ok');
  } finally {
    await browser?.close();
    await hub.stop();       // also when the browser never launched: the hub process must not outlive the test
  }
})().catch(error => { console.error(error); process.exit(1); });
