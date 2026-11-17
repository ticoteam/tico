// Page parts on a real hub (support/real-hub.cts), the bundle served as in production (scripts/build-ui.cjs, ui/parts.json):
// - a cold open of Home or Tasks downloads the shell only, and so does an owner's first visit and Home's refresh;
// - a part that cannot load leaves Retry in the main area while the sidebar still works, and Retry opens the page once
//   the network is back;
// - a tab open from before a release, whose part address the server no longer has, reloads to the current release and
//   opens the page.
// Every other page's first visit is parts-every-page.cts: together they ran too close to the browser-script cap.
import type {Browser} from 'playwright';
import type * as RealHubSupport from './support/real-hub.cts';
import type * as PartsSupport from './support/parts.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {startHub} = require('./support/real-hub.cts') as RealHubSupport.Api;
const {t} = require('./support/load.cts') as {t: (ms: number) => number};
const {must} = require('./support/e2e.cts') as {must: (hub: RealHubSupport.Hub, who: string, method: string, path: string, body?: unknown) => Promise<unknown>};
const {PART, cold, drawn, table} = require('./support/parts.cts') as PartsSupport.Api;

// The page's own globals the background refresh and the live stream go through.
declare const S: {v2: {status: Record<string, unknown>}};
declare function refresh(quiet: boolean): void;
declare function liveEmit(topic: string, data: unknown): void;
declare function decideTabPages(): {page: string}[];
declare function uiPartLoad(name: string): Promise<void>;

// Pages a cold open draws from the shell alone.
const SHELL = ['#/home', '#/tasks'];

(async () => {
  const hub = await startHub({seeds: ['org', 'toured']});     // toured: no first-visit tour over Home
  let browser: Browser | undefined;
  try {
    browser = await chromium.launch();

    // A cold open of a shell page downloads no page part.
    for (const route of SHELL) {
      const open = await cold(browser, hub);
      await open.page.goto(hub.url + '/' + route);
      await drawn(open.page, route);
      if (route === '#/home') assert.ok(Object.keys((await table(open.page)).parts).length >= 10, 'the page lists its parts');
      assert.deepEqual(open.errors, [], route + ': errors on the page');
      assert.deepEqual(open.parts, [], route + ': a cold open of a shell page downloads only the shell');
      await open.close();
    }

    // An owner's first visit, the tour not seen yet: the first-visit tour starts from the shell, and the welcome's code
    // (with Settings') loads only for a team that came in from the company file, which this one did not.
    {
      await must(hub, 'dana', 'POST', 'setup/getting-started/state', {tour: false});
      const open = await cold(browser, hub);
      await open.page.goto(hub.url + '/#/home');
      await drawn(open.page, '#/home (tour not seen)');
      await open.page.waitForTimeout(1000);
      assert.deepEqual(open.errors, [], "an owner's first visit: errors on the page");
      assert.deepEqual(open.parts, [], "an owner's first visit downloads no page part");
      await must(hub, 'dana', 'POST', 'setup/getting-started/state', {tour: true});
      await open.close();
    }

    // Home stays the shell alone after it opens: the background refresh (every 2 minutes), a bot's status change on the
    // live stream, and browser notifications kept at boot for someone who allowed them load no page's code.
    {
      await must(hub, 'dana', 'POST', 'humans/dana', {notify_browser: true});
      const open = await cold(browser, hub);
      // Headless Chromium answers "denied" whatever is granted: the page is told the person allowed them.
      await open.page.addInitScript(() => { Object.defineProperty(Notification, 'permission', {get: () => 'granted'}); });
      await open.page.goto(hub.url + '/#/home');
      await drawn(open.page, '#/home');
      await open.page.evaluate(() => {
        refresh(false);
        const bot = Object.keys(S.v2.status)[0] || 'ops';
        liveEmit('bots', {bot, status: S.v2.status[bot]});
      });
      await open.page.waitForTimeout(1000);
      await open.page.waitForLoadState('networkidle', {timeout: t(15000)}).catch(() => {});
      assert.deepEqual(open.errors, [], 'Home after a refresh: errors on the page');
      assert.deepEqual(open.parts, [], "Home's refresh, a bot's status change and notifications download no page part");
      // A "Page → Tab" phrase in Needs you (the ask screen, its own part since P05b) links Rules' and Settings' tabs
      // with neither page's code: opening the ask screen's code downloads that part alone.
      assert.deepEqual(await open.page.evaluate(async () => { await uiPartLoad('decide'); return decideTabPages().map(p => p.page).filter(p => p !== 'Finance'); }),
        ['Rules', 'Settings'], "the ask screen knows Rules' and Settings' tabs");
      assert.deepEqual([...new Set(open.parts)].sort(), ['browser-view', 'decide', 'task-docs'], "the ask screen's code downloads its own part, the browser view and a task's documents only");
      await must(hub, 'dana', 'POST', 'humans/dana', {notify_browser: false});
      await open.close();
    }

    // No network for a page's code: Retry in the main area, the sidebar still works, Retry opens it once it is back.
    {
      const open = await cold(browser, hub);
      const {page} = open;
      await page.goto(hub.url + '/#/home');
      await drawn(page, '#/home');
      const down = (route: import('playwright').Route) => route.abort('internetdisconnected');
      await page.route(PART, down);
      await page.route('**/api/v2/config', down);
      await page.evaluate(() => { location.hash = '#/growth'; });
      const retry = page.locator('#main [data-part-retry]');
      await retry.waitFor({timeout: t(15000)});
      assert.match(await page.locator('#main .part-error').innerText(), /Couldn't load this page\.[\s\S]*Check your connection and try again\./);
      await page.locator('#side a[data-nav="tasks"]').click();
      await page.waitForFunction(() => location.hash.startsWith('#/tasks') && !document.querySelector('#main [data-part-retry]'), null, {timeout: t(15000)});
      await page.evaluate(() => { location.hash = '#/growth'; });
      await retry.waitFor({timeout: t(15000)});
      await page.unroute(PART, down);
      await page.unroute('**/api/v2/config', down);
      await retry.click();
      await drawn(page, '#/growth after Retry');
      assert.ok(open.parts.includes('growth'), 'Retry asked for the page again');
      assert.deepEqual(open.errors, [], 'no errors after Retry');
      await open.close();
    }

    // A tab from before a release: its part address is gone (404) and the server's build is another. The page reloads
    // to the current release, once, and the page opens there.
    {
      const open = await cold(browser, hub);
      const {page} = open;
      await page.goto(hub.url + '/#/home');
      await drawn(page, '#/home');
      // Only the first ask for the part, and the config read after it, answer as after a release.
      let released = true;
      await page.route(PART, route => released ? route.fulfill({status: 404, body: 'Not found'}) : route.continue());
      await page.route('**/api/v2/config', async route => {
        if (!released) return route.continue();
        released = false;
        const answer = await route.fetch();
        return route.fulfill({response: answer, json: {...await answer.json() as object, ui_build: 'a-newer-release.build'}});
      });
      const reloaded = page.waitForEvent('load', {timeout: t(20000)});
      await page.evaluate(() => { location.hash = '#/growth'; });
      await reloaded;
      await page.waitForFunction(() => location.hash === '#/growth', null, {timeout: t(10000)});
      await drawn(page, '#/growth after the reload');
      await open.close();
    }
    console.log('parts first visit: ok');
  } finally {
    await browser?.close();
    await hub.stop();
  }
})().catch(e => { console.error(e); process.exit(1); });
