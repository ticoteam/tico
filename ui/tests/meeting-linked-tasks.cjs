// Open meeting details apply authorized task/link events without rereading the linked-task list.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {startHub, pageAs} = require('./support/real-hub.cjs');
const {t} = require('./support/load.cjs');

(async () => {
  const hub = await startHub({seeds: ['org']});
  let browser;
  try {
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
    const request = await browser.newContext({extraHTTPHeaders: {Authorization: 'Bearer ' + hub.tokens.dana}});
    const call = async (method, path, data) => {
      const reply = await request.request.fetch(hub.url + '/api/v2/' + path, {method, data,
        headers: {'Idempotency-Key': require('node:crypto').randomUUID()}});
      assert.equal(reply.status(), 200, await reply.text());
      const result = await reply.json();
      return result.task || result;
    };
    for (const phone of [true, false]) {
      const meeting = await call('POST', 'meetings/import', {title: 'Release planning', transcript: 'Dana: Review the plan.', review: 'live'});
      let task = await call('POST', 'tasks', {title: 'Review the public plan', body: 'Review the plan.', owner: 'human:dana', private: false});
      const hidden = await call('POST', 'tasks', {title: 'Private review', body: 'Private.', owner: 'human:dana', private: true});
      const page = await pageAs(browser, hub, 'priya', {phone});
      const errors = [];
      let reads = 0;
      page.on('pageerror', error => errors.push(error.message));
      page.on('request', req => { if (req.method() === 'GET' && new URL(req.url()).pathname === `/api/v2/meetings/${meeting.id}/tasks`) reads++; });
      try {
        await page.goto(hub.url + '/#/meetings?meeting=' + meeting.id);
        await page.waitForFunction(id => liveConnected() && MEET?.linkedTasks?.[id], meeting.id, {timeout: t(20000)});
        const initialReads = reads;
        await call('POST', `meetings/${meeting.id}/tasks`, {task_ids: [task.id, hidden.id]});
        const row = page.locator(`[data-linked-task="${task.id}"]`);
        await row.waitFor({timeout: t(20000)});
        assert.match(await row.innerText(), /Review the public plan/);
        assert.equal(await page.locator(`[data-linked-task="${hidden.id}"]`).count(), 0);
        await page.evaluate(id => { window.heldLinkedTask = document.querySelector(`[data-linked-task="${id}"]`); }, task.id);
        task = await call('POST', 'tasks/' + task.id, {version: task.version, title: 'Review the final plan', status: 'doing'});
        await page.waitForFunction(id => {
          const text = document.querySelector(`[data-linked-task="${id}"]`)?.textContent || '';
          return text.includes('Review the final plan') && text.includes('doing');
        }, task.id, {timeout: t(20000)});
        assert.equal(await page.evaluate(id => window.heldLinkedTask === document.querySelector(`[data-linked-task="${id}"]`), task.id), true);
        await call('DELETE', `meetings/${meeting.id}/tasks/${task.id}`);
        await row.waitFor({state: 'detached', timeout: t(20000)});
        await call('POST', `meetings/${meeting.id}/tasks`, {task_ids: [task.id]});
        await row.waitFor({timeout: t(20000)});
        task = await call('POST', 'tasks/' + task.id, {version: task.version, private: true});
        await row.waitFor({state: 'detached', timeout: t(20000)});
        assert.equal(await page.locator('[data-linked-task]').count(), 0, 'hidden tasks stay hidden');
        assert.equal(reads, initialReads, 'link and task events patch the pane without a list read');
        assert.deepEqual(errors, []);
      } finally { await page.context().close(); }
    }
    await request.close();
    console.log('meeting-linked-tasks: ok');
  } finally { await browser?.close(); await hub.stop(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
