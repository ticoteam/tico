// Journey: the task board and the task detail, as Dana (the owner, who asks for what she makes). Dana makes a task in the
// page for the engineering-lead bot, types it Launch and moves it to Build; the detail draws the title, the owner, who
// asked, the created time, the status and the history. The bot marks it done (the owner's act, so Dana sees no Done
// button); Dana closes it and reopens it; the board draws the card. Runs on a phone and on a desktop.
const assert = require('node:assert/strict');
const {journey, open, go, must, assertClean, shot, t} = require('./support/e2e.cjs');

const TITLE = 'Draft the launch note';
const CREATED = /[A-Z][a-z]{2}, [A-Z][a-z]{2} \d{1,2}, \d{1,2}:\d{2} [AP]M/;

// The task sheet as drawn: its title, status pill, the rows the sheet labels Owner and Asked by, created time, history.
async function detail(page) {
  return page.evaluate(() => {
    const m = document.querySelector('#task-modal');
    const row = label => [...(m?.querySelectorAll('.prop') || [])]
      .find(r => r.querySelector('.prop-k')?.textContent.trim() === label)?.innerText.replace(/\s+/g, ' ').trim() || '';
    return {
      open: !!m?.open,
      title: m?.querySelector('.tmodal-title')?.textContent.trim() || '',
      status: m?.querySelector('.tstatus')?.textContent.trim() || '',
      created_at: m?.querySelector('.tmodal-age')?.getAttribute('title') || '',
      owner_id: row('Owner'),
      asked: row('Asked by'),
      history: m?.querySelector('.task-comments')?.innerText.replace(/\s+/g, ' ').trim() || '',
      canDone: !!m?.querySelector('[data-task-finish="done"]'),
      canClose: !!m?.querySelector('[data-task-finish="close"]'),
    };
  });
}

// Waits until the sheet is open on this task, then reads it.
async function sheet(page, title) {
  await page.waitForFunction(text => {
    const m = document.querySelector('#task-modal');
    return m?.open && m.querySelector('.tmodal-title')?.textContent.trim() === text;
  }, title, {timeout: t(20000)});
  return detail(page);
}

// Waits until the sheet's status pill matches `re`.
async function waitStatus(page, re) {
  await page.waitForFunction(src => new RegExp(src, 'i').test(document.querySelector('#task-modal .tstatus')?.textContent || ''),
    re.source, {timeout: t(15000)});
}

// Picks an item from the open menu (a popover of .tl-mi buttons).
async function pick(page, text) {
  await page.locator('.prop-pop:popover-open .tl-mi', {hasText: text}).first().click();
}

journey('e2e-tasks', async ({hub, browser, phone}) => {
  const where = phone ? 'phone' : 'desktop';
  const launch = (await must(hub, 'dana', 'POST', 'task-types', {name: 'Launch', steps: [
    {name: 'Plan', status: 'open'}, {name: 'Build', status: 'doing'}, {name: 'Ship', status: 'done'}]}, 'make the Launch type')).type;
  const ship = launch.steps.find(s => s.name === 'Ship');

  // Dana makes the task in the page, for the engineering-lead bot.
  const page = await open(browser, hub, 'dana', {phone, hash: `#/tasks?view=list&type=${launch.id}`});
  await page.click('#task-new');
  const form = page.locator('#task-create-form');
  await form.locator('input[name=title]').fill(TITLE);
  const owner = await form.locator('select[name=owner] option', {hasText: 'engineering-lead'}).getAttribute('value');
  await form.locator('select[name=owner]').selectOption(owner);
  await form.locator('select[name=type]').waitFor({timeout: t(20000)});   // the Type field appears once a second type exists
  await form.locator('select[name=type]').selectOption(launch.id);
  await form.locator('textarea[name=body]').fill('Write the note and send it to the list.');
  await form.locator('button[type=submit]').click();
  const row = page.locator('.tl-row', {hasText: TITLE}).first();
  await row.waitFor({timeout: t(20000)});
  const id = (await row.getAttribute('data-task-key')).slice(1);
  assertClean(page, `${where}: after create`);

  // The sheet draws what the task says. Dana asked for it, so she can close it; she is not its owner, so no Done.
  go(page, '#/task/' + id);
  let d = await sheet(page, TITLE);
  assert.match(d.owner_id, /engineering-lead/, `${where}: owner row names the bot`);
  assert.match(d.asked, /You/, `${where}: asked-by row names the requester`);
  assert.match(d.created_at, CREATED, `${where}: created time is drawn`);
  assert.match(d.status, /Plan/, `${where}: a typed task shows its first step`);
  assert.ok(d.canClose, `${where}: the requester can close`);
  assert.ok(!d.canDone, `${where}: the requester cannot mark it done`);
  assertClean(page, `${where}: sheet`);

  // Moved to Build from the Step menu. The history shows the move.
  await page.locator('#task-modal [data-prop="step"]').click();
  await pick(page, 'Build');
  await waitStatus(page, /Build/);
  await page.waitForFunction(() => document.querySelector('#task-modal .task-comments')?.innerText.includes('Build'),
    null, {timeout: t(15000)});
  d = await detail(page);
  assert.match(d.history, /Build/, `${where}: history shows the step move`);
  assertClean(page, `${where}: after the step move`);

  // The bot (the owner) finishes the work: the step moves to Ship, which is done.
  const current = await must(hub, 'engineering-lead', 'GET', `tasks/${id}`, undefined, 'the bot reads the task');
  const version = (current.task || current).version;
  await must(hub, 'engineering-lead', 'POST', `tasks/${id}`, {version, step: ship.id, status: 'done'}, 'the bot marks it done');
  await page.reload();
  d = await sheet(page, TITLE);
  assert.match(d.status, /Ship/, `${where}: the sheet shows the step the bot moved it to`);
  await page.waitForFunction(() => document.querySelector('#task-modal .task-comments')?.innerText.includes('Ship'),
    null, {timeout: t(15000)});
  d = await detail(page);
  assert.match(d.history, /Ship/, `${where}: history shows the bot's move`);
  assert.equal((await must(hub, 'dana', 'GET', `tasks/${id}`)).task.status, 'done', 'the task is done in the hub');
  assertClean(page, `${where}: after done`);

  // Dana accepts it by closing it: the sheet closes with the task. She opens it again: a closed task offers no close, and
  // its task menu reopens it.
  await page.locator('#task-modal [data-task-finish="close"]').click();
  await page.waitForFunction(() => !document.querySelector('#task-modal')?.open, null, {timeout: t(15000)});
  assert.equal((await must(hub, 'dana', 'GET', `tasks/${id}`)).task.status, 'closed', `${where}: the hub has it closed`);
  assertClean(page, `${where}: after close`);
  go(page, `#/tasks?view=list&type=${launch.id}`);
  go(page, '#/task/' + id);
  d = await sheet(page, TITLE);
  assert.ok(!d.canClose, `${where}: a closed task offers no close`);
  await page.locator('#task-modal [data-task-more]').click();
  await pick(page, 'Reopen');
  await page.waitForFunction(() => !!document.querySelector('#task-modal [data-task-finish="close"]'), null, {timeout: t(15000)});
  assert.equal((await must(hub, 'dana', 'GET', `tasks/${id}`)).task.status, 'open', `${where}: the hub has it reopened`);
  assertClean(page, `${where}: after reopen`);

  // The board for the type draws the card, and the page does not scroll sideways.
  go(page, `#/tasks?view=board&type=${launch.id}`);
  await page.locator(`.bcard[data-task-key="t${id}"]`).waitFor({timeout: t(20000)});
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, `${where}: board page scrolls sideways by ${overflow}px`);
  assertClean(page, `${where}: board`);
  await shot(page, `e2e-tasks-board-${where}`);
});
