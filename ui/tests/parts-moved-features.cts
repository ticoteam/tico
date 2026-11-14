// Code moved out of the shell to keep a cold open of Home under 300 KB (P05b, backend/ui_bundle.py SHELL_JS_CEILING),
// on a real hub with the bundle served as in production, in Chromium and in WebKit (Safari):
// - a cold open of Home downloads none of the moved parts;
// - each moved feature works on its first use: Tags, What's new, a thread's settings and Members (the ask screen, a
//   task's files, the key drop, the browser view and the assistant have their own tests, which run on the same bundle);
// - with no network, the first use of a moved feature says so (a page shows Retry, a control a toast), the rest of the
//   screen keeps working, and it works once the network is back.
import type {Browser, BrowserType, Page} from 'playwright';
import type * as RealHubSupport from './support/real-hub.cts';
const {chromium, webkit} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {startHub, DESKTOP} = require('./support/real-hub.cts') as RealHubSupport.Api;
const {t} = require('./support/load.cts') as {t: (ms: number) => number};
const {must} = require('./support/e2e.cts') as {must: (hub: RealHubSupport.Hub, who: string, method: string, path: string, body?: unknown) => Promise<{id?: string}>};

const PART = /\/tico\/ui\/app\.bundle\.([a-z][a-z0-9-]*)\.js/;
// The parts P05b made: none of them is downloaded by a cold open of Home.
const MOVED = ['assistant', 'changelog', 'task-files', 'decide', 'tags', 'key-drop', 'browser-view', 'thread-settings'];

async function cold(browser: Browser, hub: RealHubSupport.Hub) {
  const context = await browser.newContext({...DESKTOP, serviceWorkers: 'block', extraHTTPHeaders: {Authorization: 'Bearer ' + hub.tokens.dana!}});
  const page = await context.newPage();
  const errors: string[] = [], parts: string[] = [];
  page.on('pageerror', e => { errors.push(String(e)); });
  page.on('console', m => { if (m.type() === 'error' && /is not defined|is not a function|before initialization/.test(m.text())) errors.push(m.text()); });
  page.on('request', r => { const m = PART.exec(r.url()); if (m) parts.push(m[1]!); });
  return {page, errors, parts, close: () => context.close()};
}

async function drawn(page: Page, what: string) {
  await page.waitForFunction(() => (document.querySelector('#main')?.children.length || 0) > 0, null, {timeout: t(20000)});
  await page.waitForLoadState('networkidle', {timeout: t(15000)}).catch(() => {});
  assert.equal(await page.locator('#main [data-part-retry]').count(), 0, what + ': the page did not load its code');
}

const down = (route: import('playwright').Route) => route.abort('internetdisconnected');

async function run(engine: BrowserType, name: string, hub: RealHubSupport.Hub, thread: string) {
  const browser = await engine.launch({headless: true});
  try {
    // Home, cold: none of the moved parts.
    {
      const open = await cold(browser, hub);
      await open.page.goto(hub.url + '/#/home');
      await drawn(open.page, name + ' #/home');
      await open.page.waitForTimeout(500);
      assert.deepEqual(open.parts.filter(p => MOVED.includes(p)), [], name + ': a cold open of Home downloads none of the moved parts');
      assert.deepEqual(open.errors, [], name + ': errors on Home');
      await open.close();
    }

    // Tags and What's new on their first visit.
    for (const [route, part, sees] of [['#/tags', 'tags', '#main'], ['#/changelog', 'changelog', '#main']] as const) {
      const open = await cold(browser, hub);
      await open.page.goto(hub.url + '/' + route);
      await drawn(open.page, name + ' ' + route);
      await open.page.locator(sees).first().waitFor({timeout: t(10000)});
      assert.ok(open.parts.includes(part), `${name} ${route}: its code came with its first visit`);
      assert.deepEqual(open.errors, [], `${name} ${route}: errors on the page`);
      await open.close();
    }

    // A thread's settings and Members, first used from a cold open of the thread.
    {
      const open = await cold(browser, hub);
      const {page} = open;
      await page.goto(hub.url + '/' + thread);
      await page.locator('[data-thv-menu]').first().waitFor({timeout: t(20000)});
      assert.ok(!open.parts.includes('thread-settings'), name + ': opening a thread does not download its settings');
      await page.locator('[data-thv-menu]').first().click();
      await page.locator('#thread-settings[open]').waitFor({timeout: t(10000)});
      await page.locator('#thread-settings [data-close]').first().click();
      const members = page.locator('[data-thv-members]').first();
      if (await members.count()) {
        await members.click();
        await page.locator('#thread-members[open]').waitFor({timeout: t(10000)});
      }
      assert.deepEqual(open.errors, [], name + ': errors in a thread\'s settings');
      await open.close();
    }

    // No network for a control's code: a toast says so, the sidebar still works, and the control works once it is back.
    {
      const open = await cold(browser, hub);
      const {page} = open;
      await page.goto(hub.url + '/' + thread);
      await page.locator('[data-thv-menu]').first().waitFor({timeout: t(20000)});
      await page.route(PART, down);
      await page.route('**/api/v2/config', down);
      await page.locator('[data-thv-menu]').first().click();
      await page.locator('.toast.err', {hasText: "Couldn't load this part of Tico. Check your connection and try again."}).waitFor({timeout: t(15000)});
      assert.equal(await page.locator('#thread-settings[open]').count(), 0, name + ': no settings without their code');
      await page.locator('#side a[data-nav="tasks"]').click();
      await page.waitForFunction(() => location.hash.startsWith('#/tasks') && (document.querySelector('#main')?.children.length || 0) > 0, null, {timeout: t(15000)});
      await page.unroute(PART, down);
      await page.unroute('**/api/v2/config', down);
      await page.goBack();
      await page.locator('[data-thv-menu]').first().waitFor({timeout: t(20000)});
      await page.locator('[data-thv-menu]').first().click();
      await page.locator('#thread-settings[open]').waitFor({timeout: t(10000)});
      assert.deepEqual(open.errors, [], name + ': errors after the network came back');
      await open.close();
    }

    // No network for a moved page's code: Retry in the main area, the sidebar still works, Retry opens it once it is back.
    {
      const open = await cold(browser, hub);
      const {page} = open;
      await page.goto(hub.url + '/#/home');
      await drawn(page, name + ' #/home');
      await page.route(PART, down);
      await page.route('**/api/v2/config', down);
      await page.evaluate(() => { location.hash = '#/tags'; });
      const retry = page.locator('#main [data-part-retry]');
      await retry.waitFor({timeout: t(15000)});
      await page.locator('#side a[data-nav="tasks"]').click();
      await page.waitForFunction(() => location.hash.startsWith('#/tasks') && !document.querySelector('#main [data-part-retry]'), null, {timeout: t(15000)});
      await page.evaluate(() => { location.hash = '#/tags'; });
      await retry.waitFor({timeout: t(15000)});
      await page.unroute(PART, down);
      await page.unroute('**/api/v2/config', down);
      await retry.click();
      await page.waitForFunction(() => !document.querySelector('#main [data-part-retry]'), null, {timeout: t(15000)});
      await drawn(page, name + ' #/tags after Retry');
      assert.ok(open.parts.includes('tags'), name + ': Retry asked for the page again');
      assert.deepEqual(open.errors, [], name + ': no errors after Retry');
      await open.close();
    }
  } finally {
    await browser.close();
  }
}

(async () => {
  const hub = await startHub({seeds: ['org', 'toured']});
  try {
    const made = await must(hub, 'dana', 'POST', 'threads', {title: 'Moved code', audience: 'company', members: [], body: 'A thread to open.'});
    assert.ok(made.id, 'a thread to open');
    const thread = '#/thread/' + made.id;
    await run(chromium, 'Chromium', hub, thread);
    await run(webkit, 'WebKit', hub, thread);
    console.log('parts moved features: ok (Chromium, WebKit)');
  } finally {
    await hub.stop();
  }
})().catch(e => { console.error(e); process.exit(1); });
