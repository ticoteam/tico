// Opening Home cold on a real hub (support/real-hub.cts) reads each thing once: no /api URL is requested twice and the
// live stream opens once, with every topic (live.ts liveProbe; the Threads rail's `seq` lets it resume from that read
// instead of reading the rail again, threads.ts). Opening a task from Home then reads the task, its files, its run view
// and who is here once each: the task's own stream resumes from before the task was read (task-modal.ts, shared-edit.ts)
// and the presence heartbeat's answer is the list (backend/presence.py). bench/first-open.cts measures the same opens.
import type {Page, Request} from 'playwright';
import type * as RealHubSupport from './support/real-hub.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {startHub, DESKTOP} = require('./support/real-hub.cts') as RealHubSupport.Api;
const {t} = require('./support/load.cts') as {t: (ms: number) => number};
const {must} = require('./support/e2e.cts') as {must: (hub: RealHubSupport.Hub, who: string, method: string, path: string, body?: unknown) => Promise<{id?: string; task?: {id: string}}>};

declare function liveConnected(): boolean;

// The longest a repeat waited after the stream connected was the rail's re-read, throttled to 3 s (threads.ts
// threadsRecount): 4 s after it connects shows any.
const SETTLE = 4000;

const repeated = (calls: string[]) => {
  const counts = new Map<string, number>();
  for (const c of calls) counts.set(c, (counts.get(c) || 0) + 1);
  return [...counts].filter(([, n]) => n > 1).map(([c, n]) => `${n}x ${c}`);
};

(async () => {
  const hub = await startHub({seeds: ['org', 'toured']});     // toured: no first-visit tour over Home
  let browser;
  try {
    await must(hub, 'dana', 'POST', 'tasks', {title: 'Read once', body: 'Opened from Home.', owner: 'dana'});
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as Parameters<typeof chromium.launch>[0]);
    const context = await browser.newContext({...DESKTOP, serviceWorkers: 'block', extraHTTPHeaders: {Authorization: 'Bearer ' + hub.tokens.dana}});
    const page: Page = await context.newPage();
    const calls: string[] = [];
    page.on('request', (r: Request) => {
      const url = new URL(r.url());
      if (url.pathname.startsWith('/api/')) calls.push(r.method() + ' ' + url.pathname + url.search);
    });
    try {
      await page.goto(hub.url + '/');
      // Home shows its data (boot-timing.ts paint-fresh), the stream is up, and what a connect sets off has had its time.
      await page.waitForFunction(() => performance.getEntriesByName('tico:paint-fresh').length > 0, null, {timeout: t(30000)});
      await page.waitForFunction(() => liveConnected(), null, {timeout: t(20000)});
      await page.waitForTimeout(t(SETTLE));
      assert.deepEqual(repeated(calls), [], 'a cold open of Home requests no /api URL twice:\n' + calls.join('\n'));
      const streams = calls.filter(c => c.startsWith('GET /api/v2/events?'));
      assert.equal(streams.length, 1, 'the live stream opens once:\n' + streams.join('\n'));
      assert.match(streams[0]!, /topics=[^&]*threads/, 'with the Threads topic from the start');
      assert.equal(calls.filter(c => c.startsWith('GET /api/v2/threads/rail')).length, 1, 'the rail is read once');

      calls.length = 0;
      const link = page.locator('#main a[href^="#/task/"]', {hasText: 'Read once'});
      const id = decodeURIComponent((await link.getAttribute('href'))!.slice('#/task/'.length));
      await link.click();
      await page.locator('#task-modal[open] [data-task-presence]').waitFor({state: 'attached', timeout: t(20000)});
      await page.waitForFunction(() => liveConnected(), null, {timeout: t(20000)});
      await page.waitForTimeout(t(SETTLE));
      const task = '/api/v2/tasks/' + id;
      const once = (want: string) => calls.filter(c => c === want).length;
      assert.deepEqual({
        task: once('GET ' + task), files: once('GET ' + task + '/files'), runView: once('GET ' + task + '/run-view'),
        presence: once('GET /api/v2/presence?place=' + encodeURIComponent('task:' + id)),
      }, {task: 1, files: 1, runView: 1, presence: 1}, 'opening a task reads it, its files, run view and presence once each:\n' + calls.join('\n'));
      assert.deepEqual(repeated(calls), [], 'opening a task requests no /api URL twice:\n' + calls.join('\n'));
    } finally {
      await context.close();
    }
    console.log('live-first-open: ok');
  } finally {
    await browser?.close();
    await hub.stop();
  }
})().catch(error => { console.error(error); process.exit(1); });
