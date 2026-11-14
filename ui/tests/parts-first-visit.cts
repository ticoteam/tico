// Page parts on a real hub (support/real-hub.cts), the bundle served as in production (backend/ui_bundle.py, ui/parts.json):
// - a cold open of Home, Tasks or Threads downloads the shell only;
// - every page opens on a cold first visit, its address typed straight in, with no error on the page, and downloads
//   the shell and that page's part (with the parts it needs) and nothing else;
// - a part that cannot load leaves Retry in the main area while the sidebar still works, and Retry opens the page once
//   the network is back;
// - a tab open from before a release, whose part address the server no longer has, reloads to the current release and
//   opens the page.
import type {Browser, Page} from 'playwright';
import type * as RealHubSupport from './support/real-hub.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {startHub, DESKTOP} = require('./support/real-hub.cts') as RealHubSupport.Api;
const {t} = require('./support/load.cts') as {t: (ms: number) => number};
const {must} = require('./support/e2e.cts') as {must: (hub: RealHubSupport.Hub, who: string, method: string, path: string, body?: unknown) => Promise<{id?: string; task?: {id: string}}>};

// The page's own globals the background refresh and the live stream go through.
declare const S: {v2: {status: Record<string, unknown>}};
declare function refresh(quiet: boolean): void;
declare function liveEmit(topic: string, data: unknown): void;
declare function decideTabPages(): {page: string}[];
declare function uiPartLoad(name: string): Promise<void>;

interface PartsTable { build: string; parts: Record<string, {url: string; load: string[]}> }
const PART = /\/tico\/ui\/app\.bundle\.([a-z][a-z0-9-]*)\.js/;
// Pages a cold open draws from the shell alone.
const SHELL = ['#/home', '#/tasks', '#/threads'];
// What a link straight to a task, a thread or Settings downloads besides the shell.
// A task's page brings the parts it draws from (its files, the ask screen and the browser view it opens, its tags), and
// Settings its Tags tab and the assistant's strip: each is its own part so that Home, which uses none of them, does not download them (P05b).
const TASK = ['browser-view', 'decide', 'tags', 'task', 'task-files'];
const ONLY: Record<string, string[]> = {'#/task/': TASK, '#/thread/': [], '#/settings/general': ['assistant', 'settings', 'tags'], '#/settings': ['assistant', 'settings', 'tags']};
// Every page in the sidebar, the account menu, Company, Library and Settings. A bot's, a person's, a task's and a
// thread's page are added from the seeded hub.
const ROUTES = [
  '#/overview', '#/threads/new', '#/updates', '#/calendar', '#/meetings', '#/assistant', '#/assistant/learned',
  '#/roadmap', '#/tags', '#/finance', '#/growth', '#/growth/sources', '#/strategy', '#/strategy/goals', '#/strategy/org',
  '#/strategy/budget', '#/strategy/market', '#/strategy/lenses', '#/strategy/brand', '#/automations', '#/listening',
  '#/rules', '#/company-page', '#/library/contacts', '#/library/skills', '#/library/files', '#/tools', '#/docs',
  '#/learnings', '#/usage', '#/replay', '#/sql', '#/mail', '#/messaging', '#/changelog', '#/settings',
  ...['general', 'people', 'bots', 'devices', 'models', 'tasks', 'contacts', 'security', 'history', 'health', 'billing'].map(tab => '#/settings/' + tab),
];

async function cold(browser: Browser, hub: RealHubSupport.Hub) {
  const context = await browser.newContext({...DESKTOP, serviceWorkers: 'block', extraHTTPHeaders: {Authorization: 'Bearer ' + hub.tokens.dana!}});
  const page = await context.newPage();
  const errors: string[] = [], parts: string[] = [];
  page.on('pageerror', e => { errors.push(String(e)); });
  page.on('console', m => { if (m.type() === 'error' && /is not defined|is not a function|before initialization/.test(m.text())) errors.push(m.text()); });
  page.on('request', r => { const m = PART.exec(r.url()); if (m) parts.push(m[1]!); });
  return {page, errors, parts, close: () => context.close()};
}

// The page drawn: something in the main area, no Retry, and a moment for what it loads next.
async function drawn(page: Page, route: string) {
  await page.waitForFunction(() => (document.querySelector('#main')?.children.length || 0) > 0, null, {timeout: t(20000)});
  await page.waitForLoadState('networkidle', {timeout: t(15000)}).catch(() => {});
  await page.waitForTimeout(500);
  assert.equal(await page.locator('#main [data-part-retry]').count(), 0, route + ': the page did not load its code');
}

const table = async (page: Page): Promise<PartsTable> => JSON.parse(await page.locator('#ui-parts').textContent() || '{}') as PartsTable;

(async () => {
  const hub = await startHub({seeds: ['org', 'toured']});     // toured: no first-visit tour over Home
  let browser: Browser | undefined;
  try {
    const made = await must(hub, 'dana', 'POST', 'tasks', {title: 'Parts', body: 'A task to open by its link.', owner: 'dana'});
    const taskId = made.task?.id || made.id;
    browser = await chromium.launch();

    // A cold open of a shell page downloads no page part.
    let groups: string[][] = [];
    const routes = [...ROUTES, '#/task/' + taskId];
    for (const route of SHELL) {
      const open = await cold(browser, hub);
      await open.page.goto(hub.url + '/' + route);
      await drawn(open.page, route);
      if (route === '#/home') {
        const parts = await table(open.page);
        assert.ok(Object.keys(parts.parts).length >= 10, 'the page lists its parts');
        groups = Object.values(parts.parts).map(p => p.load);
        // A bot's and a person's page, from the sidebar's links.
        for (const sel of ['a[href^="#/bot/"]', 'a[href^="#/person/"]', 'a[href^="#/thread/"]']) {
          const href = await open.page.locator(sel).first().getAttribute('href').catch(() => null);
          if (href) routes.push(href);
        }
      }
      assert.deepEqual(open.errors, [], route + ': errors on the page');
      assert.deepEqual(open.parts, [], route + ': a cold open of a shell page downloads only the shell');
      await open.close();
    }
    assert.ok(routes.some(r => r.startsWith('#/bot/')), 'the sidebar links a bot');

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
      assert.deepEqual([...new Set(open.parts)].sort(), ['browser-view', 'decide'], "the ask screen's code downloads its own part and the browser view only");
      await must(hub, 'dana', 'POST', 'humans/dana', {notify_browser: false});
      await open.close();
    }

    // Every page on its first visit, a few at a time: it opens, and it downloads one page's group of parts at most.
    const queue = [...routes];
    const visit = async () => {
      for (let route = queue.shift(); route; route = queue.shift()) {
        const open = await cold(browser!, hub);
        try {
          await open.page.goto(hub.url + '/' + route);
          await drawn(open.page, route);
          assert.deepEqual(open.errors, [], route + ': errors on the page');
          const got = [...new Set(open.parts)].sort();
          // A page may call into another page's part (Settings › Contacts draws Library's contacts settings), but a
          // link to a task, a thread or Settings downloads that page's own code only.
          const own = ONLY[route.replace(/^#\/(task|thread)\/.*$/, '#/$1/')];
          if (own) assert.deepEqual(got, own, route + ': downloads only its own part');
          assert.ok(got.length < groups.length, route + ': downloads only some of the parts: ' + got.join(', '));
        } finally { await open.close(); }
      }
    };
    await Promise.all([visit(), visit(), visit(), visit()]);

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
    console.log(`parts first visit: ok (${routes.length} pages, ${groups.length} parts)`);
  } finally {
    await browser?.close();
    await hub.stop();
  }
})().catch(e => { console.error(e); process.exit(1); });
