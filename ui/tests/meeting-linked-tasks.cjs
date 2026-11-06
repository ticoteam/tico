// Meetings on one real hub and one browser: what were e2e-meetings, task-meetings and meeting-linked-tasks. A meeting with
// a transcript is imported as Dana and listed by its title, and its review page draws the transcript (meetingsImport, on a
// phone and a desktop); a task's Meetings list follows real link visibility and Open reaches the meeting, and an expanded
// list survives task updates and link changes without a reread (taskMeetings, phone and desktop); open meeting details
// apply authorized task and link events without rereading the linked-task list (linkedTasks, phone and desktop).
// Each keeps its own assertions and names itself in a failure ("[taskMeetings] ...").
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {startHub, pageAs} = require('./support/real-hub.cjs');
const {t} = require('./support/load.cjs');
const e2e = require('./support/e2e.cjs');

// e2e-meetings: the import, the list and the review page, at each width.
async function meetingsImport({hub, browser}) {
  const {open, go, must, assertClean, drawn, shot} = e2e;
  const TITLE = 'Beta go/no-go';
  const LINE = 'We agreed to ship the beta on Nov 3 if the review queue stays under six hours.';
  for (const phone of [true, false]) {
    const where = phone ? 'phone' : 'desktop';
    const external = 'e2e-beta-' + where;
    const made = await must(hub, 'dana', 'POST', 'meetings/import', {
      title: TITLE, started_at: '2026-10-30T15:00:00Z', duration_seconds: 1800,
      participants: ['Dana', 'Omar'], source: 'e2e', external_id: external,
      transcript: `Dana: ${LINE}\nOmar: Review time is at six hours today.`, format: 'text',
      notes: 'Go for Nov 3.', review: 'pending',
    }, 'import the meeting');
    const id = made.id || made.meeting?.id;
    assert.ok(id, `${where}: the import returns the meeting id`);

    const page = await open(browser, hub, 'dana', {phone, hash: '#/meetings'});
    await page.locator('#main', {hasText: TITLE}).first().waitFor({timeout: t(20000)});
    assertClean(page, `${where}: meetings listed`);

    go(page, `#/meetings?meeting=${id}`);
    // The transcript is always shown beside the meeting.
    const text = await drawn(page, '#main', text => text.includes(LINE), `${where}: the review page draws the transcript`);
    assert.match(text, new RegExp(TITLE), `${where}: the review page names the meeting`);
    assertClean(page, `${where}: review page`);

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    assert.ok(overflow <= 1, `${where}: meetings scroll sideways by ${overflow}px`);
    await shot(page, `e2e-meeting-${where}`);
  }
}

// task-meetings: a task's Meetings list.
async function taskMeetings({hub, browser}) {
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
}

// meeting-linked-tasks: the meeting pane's linked tasks.
async function linkedTasks({hub, browser}) {
      const request = await browser.newContext({extraHTTPHeaders: {Authorization: 'Bearer ' + hub.tokens.dana}});
      const call = async (method, path, data) => {
        const reply = await request.request.fetch(hub.url + '/api/v2/' + path, {method, data,
          headers: {'Idempotency-Key': require('node:crypto').randomUUID()}});
        assert.equal(reply.status(), 200, await reply.text());
        const result = await reply.json();
        return result.task || result;
      };
      for (const phone of [true, false]) {
        const surface = phone ? 'phone' : 'desktop';
        const meeting = await call('POST', 'meetings/import', {title: 'Release planning', transcript: 'Dana: Review the plan.', review: 'live'});
        let task = await call('POST', 'tasks', {title: 'Review the public plan on ' + surface, body: 'Review the plan.', owner: 'human:dana', private: false});
        const hidden = await call('POST', 'tasks', {title: 'Private review on ' + surface, body: 'Private.', owner: 'human:dana', private: true});
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
          task = await call('POST', 'tasks/' + task.id, {version: task.version, title: 'Review the final plan on ' + surface, status: 'doing'});
          await page.waitForFunction(id => {
            const text = document.querySelector(`[data-linked-task="${id}"]`)?.textContent || '';
            return text.includes('Review the final plan') && text.toLowerCase().includes('doing');
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
}

(async () => {
  const hub = await startHub({seeds: ['org', 'toured']});
  let browser;
  try {
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
    for (const [name, step] of Object.entries({meetingsImport, taskMeetings, linkedTasks})) {
      try { await step({hub, browser}); }
      catch (error) { error.message = `[${name}] ${error.message}`; throw error; }
    }
    console.log('meeting-linked-tasks: ok');
  } finally { await browser?.close(); await hub.stop(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
