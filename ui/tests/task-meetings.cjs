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
        await page.waitForFunction(() => liveConnected(), null, {timeout: t(20000)});
        await call('POST', `meetings/${meeting.id}/tasks`, {task_ids: [task.id]});
        const list = page.locator('[data-task-meetings]');
        await list.getByRole('heading', {name: 'Meetings', exact: true}).waitFor({timeout: t(20000)});
        const meetingText = await list.innerText();
        assert.match(meetingText, /Launch review/);
        assert.match(meetingText, /\d{4}-\d{2}-\d{2}/);
        const open = list.getByRole('link', {name: 'Open', exact: true});
        assert.equal(await open.getAttribute('href'), '#/meetings?meeting=' + meeting.id);
        await open.click();
        await page.locator('#meet-heading').waitFor({timeout: t(20000)});
        assert.equal(await page.locator('#meet-heading').innerText(), 'Launch review');
        const linked = page.locator(`[data-linked-task="${task.id}"] a`);
        await linked.waitFor();
        assert.match(await linked.innerText(), /Review the launch plan/);
        await linked.click();
        await page.locator('[data-task-meetings] h3').waitFor({timeout: t(20000)});
        await call('DELETE', `meetings/${meeting.id}/tasks/${task.id}`);
        await page.waitForFunction(() => document.querySelector('[data-task-meetings]')?.hidden, null, {timeout: t(20000)});
        assert.equal(await page.locator('[data-task-meetings]').isHidden(), true);
        assert.deepEqual(errors, []);
      } finally { await page.context().close(); }
    }
    const expanded = await call('POST', 'tasks', {title: 'Review recurring meetings', body: 'Review the plans.', owner: 'human:dana'});
    let expandedTask = expanded.task || expanded;
    const meetings = [];
    for (let i = 0; i < 25; i++) {
      const saved = await call('POST', 'meetings/import', {title: 'Planning ' + i, transcript: 'Dana: Review the plan.', review: 'live'});
      meetings.push(saved);
      await call('POST', `meetings/${saved.id}/tasks`, {task_ids: [expandedTask.id]});
    }
    for (const phone of [true, false]) {
      const page = await pageAs(browser, hub, 'dana', {phone});
      let reads = 0;
      page.on('request', req => { if (req.method() === 'GET' && new URL(req.url()).pathname === `/api/v2/tasks/${expandedTask.id}/meetings`) reads++; });
      try {
        await page.goto(hub.url + '/#/task/' + expandedTask.id);
        const list = page.locator('[data-task-meetings]');
        await list.locator('[data-meetings-more]').click({timeout: t(20000)});
        await page.waitForFunction(() => document.querySelectorAll('[data-meeting-id]').length === 25 && liveConnected(), null, {timeout: t(20000)});
        const before = reads;
        const retained = meetings[0].id;
        await page.evaluate(id => { window.heldMeeting = document.querySelector(`[data-meeting-id="${id}"]`); }, retained);
        const title = 'Review recurring plans ' + (phone ? 'on phone' : 'on desktop');
        const changed = await call('POST', 'tasks/' + expandedTask.id, {version: expandedTask.version, title});
        expandedTask = changed.task || changed;
        await page.waitForFunction(({id, title}) => TASK_CHAT?.data?.task?.id === id && TASK_CHAT.data.task.title === title, {id: expandedTask.id, title}, {timeout: t(20000)});
        assert.equal(await list.locator('[data-meeting-id]').count(), 25);
        assert.equal(reads, before, 'an ordinary task update does not reread meetings');
        await page.evaluate(() => taskModalShow(TASK_CHAT.data.task, TASK_CHAT.dialog));
        assert.equal(await list.locator('[data-meeting-id]').count(), 25, 'dialog redraw retains expanded rows');
        const extra = await call('POST', 'meetings/import', {title: 'Extra planning', transcript: 'Dana: Review the plan.', review: 'live'});
        await call('POST', `meetings/${extra.id}/tasks`, {task_ids: [expandedTask.id]});
        await list.locator(`[data-meeting-id="${extra.id}"]`).waitFor({timeout: t(20000)});
        assert.equal(await list.locator('[data-meeting-id]').count(), 26);
        await call('DELETE', `meetings/${extra.id}/tasks/${expandedTask.id}`);
        await list.locator(`[data-meeting-id="${extra.id}"]`).waitFor({state: 'detached', timeout: t(20000)});
        assert.equal(await list.locator('[data-meeting-id]').count(), 25);
        assert.equal(await page.evaluate(id => window.heldMeeting === document.querySelector(`[data-meeting-id="${id}"]`), retained), true, 'existing rows survive link changes');
        assert.equal(reads, before, 'link events patch rows without rereading meetings');
      } finally { await page.context().close(); }
    }
    await request.close();
    console.log('task-meetings: ok');
  } finally { await browser?.close(); await hub.stop(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
