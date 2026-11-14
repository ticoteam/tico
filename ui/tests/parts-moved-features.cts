// Code moved out of the shell to keep a cold open of Home under 300 KB (P05b, backend/ui_bundle.py SHELL_JS_CEILING),
// on a real hub with the bundle served as in production, in Chromium and in WebKit (Safari):
// - a cold open of Home downloads none of the moved parts;
// - each moved feature works on its first use: Tags, What's new, a thread's settings and Members, the Tools chat's key
//   drop, the Assistant's settings strip, and a task's page with its files, Needs you and the browser view;
// - with no network, the first use of a moved feature says so (a page shows Retry, a control a toast), the rest of the
//   screen keeps working, and it works once the network is back; the Tools chat whose key drop failed to load sends
//   once it is back, still checked for keys.
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

async function run(engine: BrowserType, name: string, hub: RealHubSupport.Hub, thread: string, task: string) {
  const browser = await engine.launch({headless: true});
  try {
    // Home, cold: none of the moved parts.
    {
      const open = await cold(browser, hub);
      await open.page.goto(hub.url + '/#/home');
      await drawn(open.page, name + ' #/home');
      await open.page.locator('#side a[data-nav="tasks"]').waitFor({timeout: t(10000)});
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
      await members.waitFor({timeout: t(10000)});
      await members.click();
      await page.locator('#thread-members[open]').waitFor({timeout: t(10000)});
      assert.deepEqual(open.errors, [], name + ': errors in a thread\'s settings');
      await open.close();
    }

    // The Tools chat: its key drop comes with the chat's first open, and Mark as secret shows in its composer.
    {
      const open = await cold(browser, hub);
      const {page} = open;
      await page.goto(hub.url + '/#/tools');
      await page.locator('[data-pgc-open]').first().waitFor({timeout: t(20000)});
      if (await page.locator('.pgc-chat').count() === 0) await page.locator('[data-pgc-open]').first().click();
      await page.locator('.pgc-chat [data-kd-mark]').waitFor({timeout: t(15000)});
      assert.ok(open.parts.includes('key-drop'), name + ': the Tools chat downloaded its key drop');
      assert.deepEqual(open.errors, [], name + ': errors in the Tools chat');
      await open.close();
    }

    // The Assistant's settings strip, drawn by Settings for the owner.
    {
      const open = await cold(browser, hub);
      await open.page.goto(hub.url + '/#/settings');
      await open.page.locator('#settings-assistant-pane').waitFor({timeout: t(20000)});
      await open.page.waitForFunction(() => !!(window as unknown as {assistantChat?: unknown}).assistantChat, null, {timeout: t(15000)});
      assert.ok(open.parts.includes('assistant'), name + ': Settings downloaded the Assistant');
      assert.deepEqual(open.errors, [], name + ': errors in Settings');
      await open.close();
    }

    // A task's page: its files, Needs you and the browser view come with it, and each runs.
    {
      const open = await cold(browser, hub);
      const {page} = open;
      await page.goto(hub.url + '/#/task/' + task);
      await page.locator('#main').getByText('Moved code task').first().waitFor({timeout: t(20000)});
      await page.waitForFunction(() => ['tfTileHTML', 'tfFromAttachments', 'decideGoThrough', 'bvMount']
        .every(f => typeof (window as unknown as Record<string, unknown>)[f] === 'function'), null, {timeout: t(15000)});
      for (const part of ['task-files', 'decide', 'browser-view']) assert.ok(open.parts.includes(part), `${name}: a task's page downloaded ${part}`);
      const ran = await page.evaluate(async () => {
        const w = window as unknown as {
          tfTileHTML: (f: unknown, open: boolean) => string; tfFromAttachments: (list: unknown[]) => unknown[];
          decideGoThrough: () => Promise<boolean>;
          bvMount: (host: HTMLElement, o: {toolId: string; accountId: string}) => {stop: () => void; root: HTMLElement};
        };
        const host = document.createElement('div');
        document.body.append(host);
        host.innerHTML = w.tfTileHTML(w.tfFromAttachments([{id: 'f1', name: 'notes.txt', content_type: 'text/plain', size: 10}])[0], false);
        const tile = !!host.querySelector('[data-tf-file="f1"]');
        const through = typeof await w.decideGoThrough();
        const view = w.bvMount(host, {toolId: 'none', accountId: 'none'});
        const browserView = !!view.root.querySelector('[data-bv-stage]') || !!view.root.querySelector('[data-bv-launch]');
        view.stop(); host.remove();
        return {tile, through, browserView};
      });
      assert.deepEqual(ran, {tile: true, through: 'boolean', browserView: true}, name + ': a task\'s files, Needs you and the browser view run');
      assert.deepEqual(open.errors, [], name + ': errors on a task\'s page');
      await open.close();
    }

    // No network for the key drop when the Tools chat opens: Send waits, and once it is back the words are still
    // checked for keys and then sent.
    {
      const open = await cold(browser, hub);
      const {page} = open;
      await page.goto(hub.url + '/#/tools');
      await page.locator('[data-pgc-open]').first().waitFor({timeout: t(20000)});
      if (await page.locator('.pgc-chat').count()) await page.locator('[data-pgc-open]').first().click();
      await page.waitForFunction(() => !document.querySelector('.pgc-chat'), null, {timeout: t(10000)});
      await page.route(PART, down);
      await page.route('**/api/v2/config', down);
      await page.locator('[data-pgc-open]').first().click();
      const box = page.locator('.pgc-chat .p-text');
      await box.waitFor({timeout: t(15000)});
      await page.locator('.toast.err', {hasText: "Couldn't load this part of Tico. Check your connection and try again."}).first().waitFor({timeout: t(15000)});
      assert.equal(await page.locator('.pgc-chat [data-kd-mark]').count(), 0, name + ': no key drop without its code');
      const key = 'ghp_' + 'a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8';
      await box.fill('Here is the key ' + key);
      const tried = page.waitForEvent('requestfailed', {predicate: r => /\.key-drop\.js/.test(r.url()), timeout: t(15000)});
      await page.locator('.pgc-chat .p-send').click();
      await tried;
      assert.ok((await box.inputValue()).includes(key), name + ': nothing is sent while the key drop cannot load');
      await page.unroute(PART, down);
      await page.unroute('**/api/v2/config', down);
      await page.locator('.pgc-chat .p-send').click();
      await page.waitForFunction(k => !(document.querySelector<HTMLTextAreaElement>('.pgc-chat .p-text')?.value || '').includes(k), key, {timeout: t(15000)});
      assert.match(await box.inputValue(), /\[key:[A-Z0-9_]+\]/, name + ': the key is locked before anything is sent');
      await page.locator('.pgc-chat [data-kd-mark]').waitFor({timeout: t(10000)});
      await box.fill('Hello once the network is back');
      await page.locator('.pgc-chat .p-send').click();
      await page.locator('.pgc-chat').getByText('Hello once the network is back').first().waitFor({timeout: t(15000)});
      await page.waitForFunction(() => (document.querySelector<HTMLTextAreaElement>('.pgc-chat .p-text')?.value || '') === '', null, {timeout: t(15000)});
      assert.deepEqual(open.errors, [], name + ': errors in the Tools chat after the network came back');
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
    const made2 = await must(hub, 'dana', 'POST', 'tasks', {title: 'Moved code task', body: 'A task to open.', owner: 'dana'}) as {task?: {id: string}; id?: string};
    const task = (made2.task || made2).id;
    assert.ok(task, 'a task to open');
    // The Tools page's Chat opens its owner's chat, with the key drop (tools-key-drop.ts).
    const bot = await must(hub, 'dana', 'GET', 'bots/engineering-manager');
    await must(hub, 'dana', 'PUT', 'page-owners/tools', {bot: bot.id});
    await run(chromium, 'Chromium', hub, thread, task);
    await run(webkit, 'WebKit', hub, thread, task);
    console.log('parts moved features: ok (Chromium, WebKit)');
  } finally {
    await hub.stop();
  }
})().catch(e => { console.error(e); process.exit(1); });
