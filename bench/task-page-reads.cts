// How often an open task page reads the task (GET /api/v2/tasks/{id}) and its run's line (GET .../execution), on a
// real hub seeded with the scenario org (ui/tests/support/real-hub.cts):
//
//     node bench/task-page-reads.cts       (needs the backend requirements in python3 or $TICO_PYTHON, and playwright's Chrome)
//
// With the task open it plays four bursts and counts the reads each makes, after a settle:
//   comments  another person's comments, each a messages event and a tasks event (backend/events.py)
//   changes   field changes to the task, each a tasks event on the page's stream and on the task's own
//   output    a working run's output events, 4 a second for 30 s (the runs topic's `output`, as a bot's steps send them)
//   hidden    comments and changes while the tab is hidden, then the count when it shows again
// and prints them as JSON, with reads per minute of output.
import type {Page, Request} from 'playwright';
import type * as RealHubSupport from '../ui/tests/support/real-hub.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const {startHub, DESKTOP} = require('../ui/tests/support/real-hub.cts') as RealHubSupport.Api;
const {must} = require('../ui/tests/support/e2e.cts') as {must: (hub: RealHubSupport.Hub, who: string, method: string, path: string, body?: unknown) => Promise<{id?: string; task?: {id: string}}>};

declare function liveConnected(): boolean;
declare function liveEmit(topic: string, data: unknown): void;
declare const TASK_CHAT: {cid?: string} | null;

const SETTLE = 3000;
const OUTPUT_SECONDS = 30;
const BURST = 5;

async function setHidden(page: Page, hidden: boolean) {
  await page.evaluate(hidden => {
    Object.defineProperty(document, 'hidden', {configurable: true, get: () => hidden});
    Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => hidden ? 'hidden' : 'visible'});
    document.dispatchEvent(new Event('visibilitychange'));
  }, hidden);
}

(async () => {
  const hub = await startHub({seeds: ['org', 'toured']});
  let browser;
  try {
    const made = await must(hub, 'dana', 'POST', 'tasks', {title: 'Acme launch notes', body: 'Read as it changes.', owner: 'dana'});
    const id = made.task?.id || made.id!;
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as Parameters<typeof chromium.launch>[0]);
    const context = await browser.newContext({...DESKTOP, serviceWorkers: 'block', extraHTTPHeaders: {Authorization: 'Bearer ' + hub.tokens.dana}});
    const page: Page = await context.newPage();
    const reads = {task: 0, execution: 0};
    const task = '/api/v2/tasks/' + id;
    page.on('request', (r: Request) => {
      if (r.method() !== 'GET') return;
      const path = new URL(r.url()).pathname;
      if (path === task) reads.task++;
      if (path === task + '/execution') reads.execution++;
    });
    await page.goto(hub.url + '/#/task/' + encodeURIComponent(id));
    await page.locator('#task-modal[open] [data-task-presence]').waitFor({state: 'attached', timeout: 30000});
    await page.waitForFunction(() => liveConnected(), null, {timeout: 20000});
    await page.waitForTimeout(SETTLE);
    const counted = async (play: () => Promise<void>) => {
      const before = {...reads};
      await play();
      await page.waitForTimeout(SETTLE);
      return {task: reads.task - before.task, execution: reads.execution - before.execution};
    };
    const comment = (n: number) => must(hub, 'omar', 'POST', `tasks/${id}/comments`, {text: `Acme note ${n}`});
    const change = (n: number) => must(hub, 'dana', 'POST', `tasks/${id}`, {size: n + 1});
    const result: Record<string, unknown> = {};
    result.comments = await counted(async () => { for (let n = 0; n < BURST; n++) { await comment(n); await page.waitForTimeout(1500); } });
    result.changes = await counted(async () => { for (let n = 0; n < BURST; n++) { await change(n); await page.waitForTimeout(1500); } });
    const cid = await page.evaluate(() => TASK_CHAT?.cid || '');
    const output = await counted(async () => {
      for (let n = 0; n < OUTPUT_SECONDS * 4; n++) {
        await page.evaluate(cid => liveEmit('runs', {conversation_id: cid, output: {kind: 'tool'}}), cid);
        await page.waitForTimeout(250);
      }
    });
    result.output = {...output, per_minute: {task: output.task * 60 / OUTPUT_SECONDS, execution: output.execution * 60 / OUTPUT_SECONDS}};
    await setHidden(page, true);
    const hidden = await counted(async () => { for (let n = 0; n < BURST; n++) { await comment(100 + n); await change(100 + n); await page.waitForTimeout(1000); } });
    const shown = await counted(() => setHidden(page, false));
    result.hidden = {while_hidden: hidden, on_show: shown};
    result.shows_latest = await page.locator('#task-modal[open]').getByText(`Acme note ${100 + BURST - 1}`).count() > 0;
    console.log(JSON.stringify(result));
    await context.close();
  } finally {
    await browser?.close();
    await hub.stop();
  }
})().catch(error => { console.error(error); process.exit(1); });
