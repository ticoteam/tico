// A routine with a due date on a real hub (ui/app/automations*.js, docs/routines.md "Before a due date"): + New, Before a
// due date makes an off routine of Dana's on a due trigger; the trigger editor sets First due, Repeats, the days before
// and the zone, saved as the hub's trigger; the list reads "Due Mar 1 · yearly · 21d before", files it under Due date
// and shows the next due day over when its task opens. A Create task step made in the editor opens a task for a bot,
// and that occurrence task's due is a deadline (Overdue, Due), not a wake date, while a bot's other task still Wakes.
// Phone, then desktop. The page's clock starts at the hub's frozen one. TICO_UI_SHOTS=<dir> saves the list and the
// open editor, dark and light.
const assert = require('node:assert/strict');
const {run, open, go, ok, automationNamed, sideways, t} = require('./support/redesign-e2e.cjs');

const HUB_NOW = new Date('2026-10-20T09:00:00Z');   // evals/factory/world.py START, where the hub's clock stays
const BOT = 'builder-claude';

// Screenshots in both themes, with animations stopped (a loading dot never finishes).
async function themed(page, name) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  for (const theme of ['dark', 'light']) {
    await page.evaluate(v => window.ticoTheme.set(v), theme);
    await page.screenshot({path: require('node:path').join(dir, `${name}-${theme}.png`), animations: 'disabled'});
  }
  await page.evaluate(() => window.ticoTheme.set('dark'));
}

// A run the route answered while another drain still held it: wait until it ends and read it back.
async function settled(page, aid, run) {
  const end = Date.now() + t(10000);
  let r = run;
  while (['queued', 'running'].includes(r.status) && Date.now() < end) {
    await page.waitForTimeout(200);
    r = (await ok(page, 'GET', `automations/${aid}/runs?limit=25`)).runs.find(x => x.id === run.id) || r;
  }
  return r;
}

run('automations-due', ['org', 'toured', 'automations'], async ({hub, browser, phone, tag}) => {
  const page = await open(browser, hub, 'dana', {phone, hash: '#/automations', time: HUB_NOW});
  await page.locator('tr.au-row').first().waitFor({timeout: t(10000)});

  // + New offers the triggers; Before a due date makes the routine and opens it.
  await page.locator('.au-new').click();
  assert.deepEqual(await page.locator('.au-newm [data-new]').evaluateAll(els => els.map(x => x.lastChild.textContent)),
    ['On a schedule', 'When something happens', 'Before a due date']);
  await page.locator('.au-newm [data-new="due"]').click();
  await page.locator('.au-hd .au-name:text("New automation")').waitFor({timeout: t(10000)});
  const made = await automationNamed(page, 'New automation');
  assert.equal(made.trigger.type, 'due', `${tag}: a due trigger`);
  assert.equal(made.trigger.repeats, 'yearly');
  assert.equal(made.trigger.open_days, 14);
  assert.match(made.trigger.first_due, /^\d{4}-\d{2}-\d{2}$/);
  assert.equal(made.enabled, false);
  assert.match(await page.locator('.au-trig .au-c1').innerText(), /^Due .+ · yearly · 14d before$/);

  // The trigger editor: When, then the due date's fields.
  await page.locator('.au-card[data-open="trigger"]').click();
  const when = page.locator('.au-ed [data-b="ttype"]');
  await when.waitFor();
  assert.equal(await when.inputValue(), 'due');
  await page.fill('.au-ed [data-b="trigger.first_due"]', '2027-03-01');
  await page.locator('.au-ed [data-b="trigger.repeats"]').selectOption('yearly');
  await page.fill('.au-ed [data-b="trigger.open_days"]', '21');
  await page.fill('.au-ed [data-b="trigger.tz"]', 'America/New_York');
  assert.equal(await sideways(page), false);
  await page.locator('.au-ed .au-save').click();
  await page.waitForFunction(() => document.querySelector('.au-ed').hidden && document.querySelector('.au-ver')?.textContent === 'v2',
    null, {timeout: t(10000)});
  const saved = await automationNamed(page, 'New automation');
  assert.deepEqual(saved.trigger, {type: 'due', first_due: '2027-03-01', repeats: 'yearly', open_days: 21, tz: 'America/New_York'});
  assert.equal(saved.next_due, '2027-03-01', `${tag}: the hub's next due day`);
  assert.equal(Date.parse(saved.next_run), Date.parse('2027-02-08T05:00:00Z'), `${tag}: its task opens 21 days before, at midnight in New York`);
  assert.equal(await page.locator('.au-trig .au-c1').innerText(), 'Due Mar 1 · yearly · 21d before');

  // Switching to a schedule and back restores the saved due date unchanged.
  await page.locator('.au-card[data-open="trigger"]').click();
  await when.selectOption('schedule');
  await page.locator('.au-ed [data-b="trigger.cron"]').waitFor();
  assert.equal(await page.locator('.au-ed [data-b="trigger.first_due"]').count(), 0);
  await when.selectOption('due');
  assert.equal(await page.locator('.au-ed [data-b="trigger.first_due"]').inputValue(), '2027-03-01');
  assert.equal(await page.locator('.au-ed [data-b="trigger.open_days"]').inputValue(), '21');
  await page.locator('.au-ed [data-op="close"]').click();

  // A Create task step for a bot, from the editor: title and owner are what the hub reads.
  await page.locator('.au-pane .au-add').last().click();
  await page.locator('.au-menu [data-ty="action"]').click();
  await page.locator('.au-ed [data-b="step.do"]').selectOption('create_task');
  await page.fill('.au-ed [data-b="step.args.title"]', 'File the yearly report');
  await page.locator('.au-ed [data-b="step.args.owner"]').selectOption('bot:' + BOT);
  await page.locator('.au-ed .au-save').click();
  await page.waitForFunction(() => document.querySelector('.au-ed').hidden && document.querySelector('.au-ver')?.textContent === 'v3',
    null, {timeout: t(10000)});
  const step = (await automationNamed(page, 'New automation')).steps[0];
  assert.equal(step.do, 'create_task');
  assert.equal(step.args.title, 'File the yearly report');
  assert.equal(step.args.owner_id, 'bot:' + BOT);

  // On: the list files it under Due date, with its words and the next due day over when the task opens.
  await page.locator('.au-hd .au-sw').check();
  await page.waitForFunction(() => document.querySelector('.au-hd .au-sw')?.checked);
  await go(page, '#/automations');
  const row = page.locator(`tr.au-row[data-row="${made.id}"]`);
  await row.waitFor();
  assert.equal(await row.locator('td.au-tt').innerText(), 'Due Mar 1 · yearly · 21d before');
  assert.deepEqual((await row.locator('.au-nx').innerText()).split('\n'), ['Due Mar 1', 'opens in 111d']);
  const next = await row.locator('td.au-cx').boundingBox();
  assert.ok(next.width >= 90, `${tag}: Next is shown, not folded (${next.width}px)`);
  const groups = await page.locator('tr.au-grp').evaluateAll(r => r.map(x => x.dataset.agrp));
  assert.ok(groups.includes('due') && groups.indexOf('due') === groups.length - 1 - (groups.includes('system') ? 1 : 0),
    `${tag}: Due date after the other kinds, before system jobs (${groups})`);
  // The Due date chip narrows the list to it.
  if (phone) await page.locator('[data-filters]').click();
  await page.locator('[data-ftrig="due"]').click();
  assert.deepEqual(await page.locator('tr.au-row').evaluateAll(r => r.map(x => x.dataset.row)), [made.id]);
  await page.locator('[data-ftrig="due"]').click();
  if (phone) await page.locator('[data-filters]').click();
  assert.equal(await sideways(page), false);
  await themed(page, `${tag}-due-list`);
  if (phone) {   // a phone's table scrolls sideways to Trigger and Next
    await page.locator('.au-tbl').evaluate(box => { box.scrollLeft = box.scrollWidth; });
    await themed(page, `${tag}-due-list-scrolled`);
  }
  // The editor open over the flow, for the screenshots.
  await page.locator(`tr.au-row[data-row="${made.id}"] a`).click();
  await page.locator('.au-card[data-open="trigger"]').click();
  await page.locator('.au-ed [data-b="trigger.first_due"]').waitFor();
  assert.equal(await sideways(page), false);
  await themed(page, `${tag}-due-editor`);
  await page.locator('.au-ed [data-op="close"]').click();

  // Runs for two due days (the scheduler's payload, sent by hand): each opens the bot's task due that day.
  const opened = [];
  for (const day of ['2026-10-15', '2026-11-30']) {
    const r = await settled(page, made.id, (await ok(page, 'POST', `automations/${made.id}/run`, {input: {due_at: {due_on: day, tz: 'UTC'}}})).run);
    assert.equal(r.status, 'ok', `${tag}: the run for ${day} (${JSON.stringify(r).slice(0, 300)})`);
  }
  const tasks = (await ok(page, 'GET', 'tasks?limit=200')).tasks || [];
  for (const day of ['2026-10-15', '2026-11-30']) {
    const task = tasks.find(x => x.occurrence === `${made.id}:${day}`);
    assert.ok(task, `${tag}: the task for ${day}`);
    assert.equal(task.owner_id, 'bot:' + BOT);
    opened.push(task);
  }
  const plain = await ok(page, 'POST', 'tasks', {title: 'Look again at the backlog', body: 'Later', owner_id: 'bot:' + BOT,
    due_at: '2026-10-25T12:00:00Z'});
  const plainId = plain.id || plain.task?.id;

  // The occurrence tasks read as deadlines on their pages (red once past); the bot's other task still wakes.
  const due = async (id, words) => {
    await go(page, '#/task/' + encodeURIComponent(id));
    const cell = page.locator('[data-prop-row="due"] .prop-txt:visible', {hasText: words}).first();
    await cell.waitFor({timeout: t(10000)});
    return {text: await cell.innerText(), cls: await cell.getAttribute('class')};
  };
  const late = await due(opened[0].id, /Oct 15/);
  assert.ok(/\blate\b/.test(late.cls) && !/\bwake\b/.test(late.cls), `${tag}: past due is red (${late.cls})`);
  const ahead = await due(opened[1].id, /Nov 30/);
  assert.ok(!/\b(late|wake)\b/.test(ahead.cls), `${tag}: a coming due is a plain deadline (${ahead.cls})`);
  const wakes = await due(plainId, /Oct 25/);
  assert.match(wakes.text, /^Wakes /);
  // The list's chip says the same.
  const chips = await page.evaluate(ids => Promise.all(ids.map(async id => {
    const r = await fetch('/api/v2/tasks/' + encodeURIComponent(id), {cache: 'no-store'});
    const body = await r.json();
    return taskDueInfo(body.task || body)?.words;
  })), [opened[0].id, opened[1].id, plainId]);
  assert.deepEqual(chips.map(w => w.split(' ')[0]), ['Overdue', 'Due', 'Wakes'], `${tag}: ${chips}`);

  // A no-input run starts the next due day, as the scheduler would: its task is due at the end of Mar 1 in New York,
  // which is Mar 2 in UTC. A viewer whose zone is UTC still reads the task's due day as Mar 1.
  const manual = await settled(page, made.id, (await ok(page, 'POST', `automations/${made.id}/run`, {})).run);
  assert.equal(manual.status, 'ok', `${tag}: the no-input run (${JSON.stringify(manual).slice(0, 300)})`);
  const ny = ((await ok(page, 'GET', 'tasks?limit=200')).tasks || []).find(x => x.occurrence === `${made.id}:2027-03-01`);
  assert.ok(ny, `${tag}: the no-input run made the Mar 1 task`);
  assert.equal(ny.due_at, '2027-03-01T23:59:59-05:00', `${tag}: due at the end of its day in the trigger's zone`);
  const utc = await open(browser, hub, 'dana', {phone, hash: '#/task/' + encodeURIComponent(ny.id), time: HUB_NOW,
    timezone: 'UTC'});
  const utcDue = utc.locator('[data-prop-row="due"] .prop-txt:visible').first();
  await utcDue.waitFor({timeout: t(10000)});
  assert.match((await utcDue.innerText()).trim(), /Mar 1$/, `${tag}: a UTC viewer reads the occurrence's day`);
  await utc.context().close();
  assert.deepEqual(page.errors, [], `${tag}: no page errors`);
  await page.context().close();
});
