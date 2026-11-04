// A task's Meetings list follows real link visibility on phone and desktop, and Open reaches the meeting.
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
      const r = await request.request.fetch(hub.url + '/api/v2/' + path, {
        method, data, headers: {'Idempotency-Key': require('node:crypto').randomUUID()},
      });
      assert.equal(r.status(), 200, await r.text());
      return r.json();
    };
    const created = await call('POST', 'tasks', {title: 'Review the launch plan', body: 'Review the launch plan.', owner: 'human:dana'});
    const task = created.task || created;
    const meeting = await call('POST', 'meetings/import', {title: 'Launch review', transcript: 'Dana: Review the plan.', review: 'live'});
    for (const phone of [true, false]) {
      const page = await pageAs(browser, hub, 'dana', {phone});
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      try {
        await page.goto(hub.url + '/#/task/' + task.id);
        await page.locator('[data-task-meetings]').waitFor({state: 'attached', timeout: t(20000)});
        assert.equal(await page.locator('[data-task-meetings]').isHidden(), true);
        await call('POST', `meetings/${meeting.id}/tasks`, {task_ids: [task.id]});
        await page.reload();
        const list = page.locator('[data-task-meetings]');
        await list.getByRole('heading', {name: 'Meetings', exact: true}).waitFor({timeout: t(20000)});
        assert.match(await list.innerText(), /Launch review/);
        assert.match(await list.innerText(), /\d{4}-\d{2}-\d{2}/);
        const open = list.getByRole('link', {name: 'Open', exact: true});
        assert.equal(await open.getAttribute('href'), '#/meetings?meeting=' + meeting.id);
        await open.click();
        await page.locator('#meet-heading').waitFor({timeout: t(20000)});
        assert.equal(await page.locator('#meet-heading').innerText(), 'Launch review');
        await page.locator('[data-sec="task"] a', {hasText: 'Open'}).waitFor();
        assert.match(await page.locator('[data-sec="task"]').innerText(), /Review the launch plan/);
        await call('DELETE', `meetings/${meeting.id}/tasks/${task.id}`);
        const loaded = page.waitForResponse(r => r.url().includes(`/tasks/${task.id}/meetings`) && r.status() === 200);
        await page.goto(hub.url + '/#/task/' + task.id);
        await loaded;
        await page.locator('[data-task-meetings]').waitFor({state: 'attached', timeout: t(20000)});
        assert.equal(await page.locator('[data-task-meetings]').isHidden(), true);
        assert.deepEqual(errors, []);
      } finally { await page.context().close(); }
    }
    await request.close();
    console.log('task-meetings: ok');
  } finally { await browser?.close(); await hub.stop(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
