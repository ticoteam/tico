// No billboard numbers (#355): on a real hub seeded with the scenario org and its finance, Home, Growth, Usage and
// Finance show no visible text in the main area above 16px, at 1440px and on a 390px phone. The sidebar and tab bar
// are outside #main; icon-font glyphs (Material Symbols) are excepted.
import type {Page} from 'playwright';
import type * as RealHubSupport from './support/real-hub.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {startHub, pageAs} = require('./support/real-hub.cts') as RealHubSupport.Api;
const {t} = require('./support/load.cts') as {t: (ms: number) => number};

// Each visible element in #main with text of its own above 16px: "<size> <tag.class>: <text>".
const bigText = (page: Page) => page.evaluate(() => [...document.querySelectorAll('#main *')].flatMap(el => {
  if (!(el instanceof HTMLElement) || !el.checkVisibility()) return [];
  const own = [...el.childNodes].filter(n => n.nodeType === 3 && n.textContent!.trim()).map(n => n.textContent!.trim()).join(' ');
  const cs = getComputedStyle(el);
  if (!own || parseFloat(cs.fontSize) <= 16 || /Material Symbols/i.test(cs.fontFamily)) return [];
  return [`${cs.fontSize} ${el.tagName.toLowerCase()}${[...el.classList].map(c => '.' + c).join('')}: ${own.slice(0, 40)}`];
}));

(async () => {
  const hub = await startHub({seeds: ['org', 'finance']});
  let browser;
  try {
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as Parameters<typeof chromium.launch>[0]);
    for (const phone of [false, true]) {
      const page = await pageAs(browser, hub, 'dana', {phone});
      try {
        // Home's default cards are count cards; pin totals too (Spend, Done per week), so a big total is caught here.
        if (!phone) {
          for (const template of ['spend', 'done_per_week']) {
            const status = await page.evaluate(async t => (await fetch('/api/v2/cards', {method: 'POST', headers: {'Content-Type': 'application/json', 'Idempotency-Key': 'nb-' + t},
              body: JSON.stringify({place: 'person:me', template: t, settings: {}, size: 'medium'})})).status, template);
            assert.equal(status, 200, `pinned ${template} on Home`);
          }
          await page.reload();
        }
        const errors: string[] = [];
        page.on('pageerror', e => errors.push(e.message));
        for (const [route, ready] of [['#/home', '#dash-grid .dash-pin-body > *'], ['#/growth', '#main h1'], ['#/usage', '#main h1'], ['#/finance', '#main h1']] as const) {
          await page.evaluate(to => { location.hash = to; }, route);
          await page.locator(ready).first().waitFor({timeout: t(20000)});
          // Home keeps a live stream open, so it never goes network-idle: wait for its totals instead.
          if (route === '#/home') await page.locator('#dash-grid .dash-big strong').nth(1).waitFor({timeout: t(20000)});
          else await page.waitForLoadState('networkidle');
          assert.deepEqual(await bigText(page), [], `nothing above 16px on ${route} at ${phone ? 390 : 1440}px`);
          if (route === '#/home') assert.ok(await page.locator('#dash-grid .dash-big strong').count() >= 2, 'Home shows the totals it checks');
        }
        assert.deepEqual(errors, []);
      } finally {
        await page.context().close();
      }
    }
    console.log('No billboard numbers: Home, Growth, Usage and Finance have nothing above 16px in the main area, desktop and phone');
  } finally {
    await browser?.close();
    await hub.stop();
  }
})().catch(e => { console.error(e); process.exit(1); });
