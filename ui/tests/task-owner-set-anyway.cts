// A stage rule that refuses a new owner (409 task_stage_rule) offers a person Set anyway under the properties; it
// resends the same owner with set_anyway, and a save that is not refused by a rule offers nothing.
import type {Browser, Page} from 'playwright';
interface TaskRow {id: string; version: number; owner_id?: string | null; [key: string]: unknown}
interface Fixture {tasks: TaskRow[]}
interface Visit {page: Page; data: Fixture; errors: string[]}
declare function taskModal(): HTMLDialogElement;
declare function taskModalShow(task: TaskRow): Promise<void>;
declare function taskSaveQueued(d: HTMLDialogElement, id: string, body: Record<string, unknown>): Promise<unknown>;
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open} = require('./task-code.cts') as {open: (browser: Browser, opts?: Record<string, unknown>) => Promise<Visit>};
const LINE = "Rule Owner on the team: Ana is not on the Engineering team (rule sr_owner_on_team). Set anyway to give it all the same.";
(async () => {
 const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
 try {
  const {page, data, errors} = await open(browser, {viewport: {width: 1440, height: 900}});
  const task = data.tasks[0]!;
  const sent: Record<string, unknown>[] = [];
  await page.route(`**/api/v2/tasks/${task.id}/owner`, route => {
   const body = route.request().postDataJSON() as Record<string, unknown>; sent.push(body);
   if (body['owner'] === 'human:ana' && !body['set_anyway']) {
    return route.fulfill({status: 409, json: {error: 'task_stage_rule', detail: LINE, missing: [], next: [], version: task.version}});
   }
   Object.assign(task, {owner_id: body['owner'], version: task.version + 1});
   return route.fulfill({json: {task}});
  });
  await page.route(`**/api/v2/tasks/${task.id}`, route => route.request().method() === 'GET'
   ? route.fulfill({json: {task, comments: [], children: [], events: []}}) : route.fallback());
  await page.evaluate((t: TaskRow) => taskModalShow(t), task);
  const msg = page.locator('#task-modal [data-props-msg]'), anyway = msg.locator('[data-set-anyway]');
  await page.evaluate((id: string) => taskSaveQueued(taskModal(), id, {owner: 'human:ana'}), task.id);
  await anyway.waitFor();
  assert.match(await msg.textContent() || '', /Owner on the team/);
  await anyway.click();
  await page.waitForFunction(() => !document.querySelector('#task-modal [data-set-anyway]'));
  await page.waitForFunction(() => document.querySelector('#task-modal [data-props-msg]')?.textContent === '');
  assert.equal(sent.length, 2);
  assert.deepEqual([sent[0]!['set_anyway'], sent[1]!['set_anyway'], sent[1]!['owner']], [undefined, true, 'human:ana']);
  assert.equal(task.owner_id, 'human:ana');
  // Another owner, not refused: nothing to set anyway.
  await page.evaluate((id: string) => taskSaveQueued(taskModal(), id, {owner: 'bot:eng'}), task.id);
  await page.waitForFunction(() => document.querySelector('#task-modal [data-props-msg]')?.textContent === '');
  assert.equal(await anyway.count(), 0);
  assert.deepEqual(errors, []);
  console.log('task-owner-set-anyway: ok');
 } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
