// Make page's task box and progress card at phone and desktop widths. The request and pin use a real hub;
// the source-checked publish and durable status contracts are covered in backend/tests/test_make_page.py.
const assert = require('node:assert/strict');
const {journey, open, go, must, assertClean, expectFailure, shot, t, SEEDS} = require('./support/e2e.cjs');

journey('make-page', async ({hub, browser, phone}) => {
  await must(hub, 'dana', 'POST', 'assistant/turn-on', {});
  const made = await must(hub, 'dana', 'POST', 'tasks', {owner: 'person:dana', title: 'Prepare the alert flow', body: 'Three scenarios.'});
  const id = (made.task || made).id;
  const page = await open(browser, hub, 'priya', {phone, hash: '#/task/' + id});
  let status = null, cap = false;
  await page.route('**/api/v2/tasks/' + id + '/make-page', async route => {
    if (route.request().method() === 'GET') return route.fulfill({json: {make_page: status}});
    if (cap) return route.fulfill({status: 409, json: {error: {code: 'conflict', detail: 'This task has 60 pins', retryable: false}}});
    const response = await route.fetch();
    assert.equal(response.status(), 200, await response.text());
    const sent = (await response.json()).message;
    assert.deepEqual(sent.refs.make_page, {task: id});
    assert.equal(sent.sender_id, 'human:priya');
    status = {message_id: sent.id, conversation_id: sent.conversation_id, state: 'making'};
    return route.fulfill({response});
  });
  const section = page.locator('[data-task-pages]'), field = section.getByRole('textbox', {name: 'Describe the page'});
  await field.fill('The alert flow: three scenarios as tabs.');
  await section.getByRole('button', {name: 'Make page', exact: true}).click();
  const card = section.locator('.tp-making');
  await card.filter({hasText: 'Making page…'}).waitFor({timeout: t(10000)});
  assert.equal(await field.evaluate(el => el.matches(':user-invalid')), false, 'a sent request leaves no validation error');
  assert.equal(await section.getByRole('button', {name: 'Make page', exact: true}).isDisabled(), true);
  await shot(page, 'make-page-' + (phone ? 'phone' : 'desktop'));
  // Reopening the task must restore the placeholder rather than losing the in-flight request.
  await go(page, '#/tasks');
  await section.waitFor({state: 'hidden', timeout: t(10000)});
  await go(page, '#/task/' + id);
  await card.filter({hasText: 'Making page…'}).waitFor({timeout: t(10000)});
  status.state = 'made';
  const published = await must(hub, 'engineering-lead', 'POST', 'pages', {name: 'alert-flow.html', title: 'Alert flow', task: id,
    text: '<h1>Alert flow</h1><p>Three scenarios.</p>'});
  await section.locator('.pg-card[data-page="' + published.page.file_id + '"]').waitFor({timeout: t(15000)});
  await card.waitFor({state: 'hidden', timeout: t(10000)});

  await field.fill('A second page.');
  await section.getByRole('button', {name: 'Make page', exact: true}).click();
  await card.filter({hasText: 'Making page…'}).waitFor({timeout: t(10000)});
  status.state = 'failed';
  await page.evaluate(cid => liveEmit('runs', {conversation_id: cid, state: 'completed'}), status.conversation_id);
  await card.getByRole('link', {name: 'Open chat'}).waitFor({timeout: t(10000)});
  assert.match(await card.innerText(), /Not made/);
  await card.getByRole('link', {name: 'Open chat'}).click();
  await page.waitForURL(/#\/assistant$/, {timeout: t(10000)});
  await go(page, '#/task/' + id);
  await card.getByRole('link', {name: 'Open chat'}).waitFor({timeout: t(10000)});

  cap = true;
  expectFailure(page, 409, '/make-page$', 'POST');
  await field.fill('Keep this draft.');
  await section.getByRole('button', {name: 'Make page', exact: true}).click();
  await section.getByRole('alert').filter({hasText: 'This task has 60 pins'}).waitFor({timeout: t(10000)});
  assert.equal(await field.inputValue(), 'Keep this draft.');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, 'Make page fits the task at this width');
  assertClean(page, phone ? 'phone Make page' : 'desktop Make page');
  await page.context().close();
}, [...SEEDS, 'pages']);
