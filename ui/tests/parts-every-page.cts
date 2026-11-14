// Page parts on a real hub (support/real-hub.cts), the bundle served as in production (backend/ui_bundle.py, ui/parts.json):
// every page opens on a cold first visit, its address typed straight in, with no error on the page, and downloads the
// shell and that page's part (with the parts it needs) and nothing else. Home, Retry and a tab from before a release are
// parts-first-visit.cts.
import type {Browser} from 'playwright';
import type * as RealHubSupport from './support/real-hub.cts';
import type * as PartsSupport from './support/parts.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {startHub} = require('./support/real-hub.cts') as RealHubSupport.Api;
const {must} = require('./support/e2e.cts') as {must: (hub: RealHubSupport.Hub, who: string, method: string, path: string, body?: unknown) => Promise<{id?: string; task?: {id: string}; thread?: {id: string}}>};
const {cold, drawn, table} = require('./support/parts.cts') as PartsSupport.Api;

// What a link straight to a task, a thread or Settings downloads besides the shell.
// A task's page brings the parts it draws from (its files, the ask screen and the browser view it opens, its tags), and
// Settings its Tags tab and the assistant's strip: each is its own part so that Home, which uses none of them, does not download them (P05b).
const TASK = ['browser-view', 'decide', 'tags', 'task', 'task-files'];
const ONLY: Record<string, string[]> = {'#/task/': TASK, '#/thread/': [], '#/settings/general': ['assistant', 'settings', 'tags'], '#/settings': ['assistant', 'settings', 'tags']};
// Every page in the sidebar, the account menu, Company, Library and Settings. A bot's and a person's page are added from
// the sidebar's links, a task's and a thread's from ones made on the hub.
const ROUTES = [
  '#/overview', '#/threads/new', '#/updates', '#/calendar', '#/meetings', '#/assistant', '#/assistant/learned',
  '#/roadmap', '#/tags', '#/finance', '#/growth', '#/growth/sources', '#/strategy', '#/strategy/goals', '#/strategy/org',
  '#/strategy/budget', '#/strategy/market', '#/strategy/lenses', '#/strategy/brand', '#/automations', '#/listening',
  '#/rules', '#/company-page', '#/library/contacts', '#/library/skills', '#/library/files', '#/tools', '#/docs',
  '#/learnings', '#/usage', '#/replay', '#/sql', '#/mail', '#/messaging', '#/changelog', '#/settings',
  ...['general', 'people', 'bots', 'devices', 'models', 'tasks', 'contacts', 'security', 'history', 'health', 'billing'].map(tab => '#/settings/' + tab),
];

(async () => {
  const hub = await startHub({seeds: ['org', 'toured']});     // toured: no first-visit tour over Home
  let browser: Browser | undefined;
  try {
    const made = await must(hub, 'dana', 'POST', 'tasks', {title: 'Parts', body: 'A task to open by its link.', owner: 'dana'});
    const taskId = made.task?.id || made.id;
    const started = await must(hub, 'dana', 'POST', 'threads', {title: 'Parts', body: 'A thread to open by its link.'});
    const threadId = started.thread?.id || started.id;
    assert.ok(taskId && threadId, 'the hub made the task and the thread to open');
    browser = await chromium.launch();

    // Home's parts table and its sidebar's links to a bot and a person, read as drawn: a locator would wait its full
    // timeout (30 s) for a link the sidebar does not have.
    const routes = [...ROUTES, '#/task/' + taskId, '#/thread/' + threadId];
    let groups: string[][];
    {
      const open = await cold(browser, hub);
      await open.page.goto(hub.url + '/#/home');
      await drawn(open.page, '#/home');
      groups = Object.values((await table(open.page)).parts).map(p => p.load);
      assert.ok(groups.length >= 10, 'the page lists its parts');
      routes.push(...await open.page.evaluate(() => ['#/bot/', '#/person/'].map(start =>
        document.querySelector(`a[href^="${start}"]`)?.getAttribute('href')).filter((href): href is string => !!href)));
      await open.close();
    }
    assert.ok(routes.some(r => r.startsWith('#/bot/')), 'the sidebar links a bot');
    assert.ok(routes.some(r => r.startsWith('#/person/')), 'the sidebar links a person');

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
    console.log(`parts every page: ok (${routes.length} pages, ${groups.length} parts)`);
  } finally {
    await browser?.close();
    await hub.stop();
  }
})().catch(e => { console.error(e); process.exit(1); });
