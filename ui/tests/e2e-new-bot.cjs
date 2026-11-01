// Journey: a bot made after bots got stable ids (its id a UUIDv7, its slug a label) works in every page, and keeps
// working when its slug is renamed. Dana (the owner) adds Sales Helper; a link by its slug opens its page at its id; she
// chats with it, gives it a task in the Tasks page, renames its slug to revenue-helper in its settings, and the links
// still go to its id while the labels show the new slug. Runs on a phone and a desktop.
const assert = require('node:assert/strict');
const {journey, open, go, must, call, assertClean, drawn, expectFailure, until, t} = require('./support/e2e.cjs');

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const TITLE = 'Send the pricing sheet to Acme';

journey('e2e-new-bot', async ({hub, browser, phone}) => {
  const where = phone ? 'phone' : 'desktop';
  await must(hub, 'dana', 'POST', 'bots', {slug: 'sales-helper', display_name: 'Sales Helper', status: 'active',
    description: 'Answers sales questions for the Acme team.'}, 'add Sales Helper');
  const bot = (await must(hub, 'dana', 'GET', 'bots/sales-helper')).id;
  assert.match(bot, UUID, `${where}: a new bot's id is a UUIDv7`);

  // A link by its slug moves to its id; the page names it.
  const page = await open(browser, hub, 'dana', {phone, hash: '#/bot/sales-helper'});
  // No team icon is set on the scenario install: Settings asks for it and is answered 404 ("No team icon is set").
  expectFailure(page, 404, '^/api/v2/team/icon$', 'GET');
  await page.waitForFunction(id => location.hash === '#/bot/' + id, bot, {timeout: t(20000)});
  await drawn(page, '.bot-nameline h1', text => text.includes('Sales Helper'), `${where}: the bot's page names it`);

  // Chat: the message goes to the bot, by its id.
  const composer = page.locator('#conv textarea.p-text, textarea[aria-label="Message this bot"]').first();
  await composer.waitFor({timeout: t(20000)});
  const sent = page.waitForRequest(r => r.method() === 'POST' && /\/api\/v2\/chat\//.test(new URL(r.url()).pathname));
  await composer.fill('What did we sell last week?');
  await page.locator('#conv button.p-send, button.p-send[aria-label="Send"]').first().click();
  assert.equal(new URL((await sent).url()).pathname, '/api/v2/chat/' + bot, `${where}: chat posts to the bot's id`);
  await page.locator('#conv .bubble.you', {hasText: 'What did we sell last week?'}).first().waitFor({timeout: t(20000)});
  assertClean(page, `${where}: chat`);

  // A task for it, made in the Tasks page: the owner choice is its id, labelled with its name.
  go(page, '#/tasks?view=list');
  await page.click('#task-new');
  const form = page.locator('#task-create-form');
  await form.locator('input[name=title]').fill(TITLE);
  const option = form.locator('select[name=owner] option', {hasText: 'Sales Helper'}).first();
  assert.equal(await option.getAttribute('value'), bot, `${where}: the owner choice is the bot's id`);
  await form.locator('select[name=owner]').selectOption(bot);
  await form.locator('textarea[name=body]').fill('The sheet is in Files; send it with a short note.');
  await form.locator('button[type=submit]').click();
  const row = page.locator('.tl-row', {hasText: TITLE}).first();
  await row.waitFor({timeout: t(20000)});
  const task = (await row.getAttribute('data-task-key')).slice(1);
  assert.equal((await must(hub, 'dana', 'GET', 'tasks/' + task)).task.owner_id, 'bot:' + bot, `${where}: the task's owner is bot:<id>`);
  assertClean(page, `${where}: task made`);

  // Rename its slug in its settings.
  go(page, `#/bot/${encodeURIComponent(bot)}/settings`);
  const editor = page.locator('#pane-settings form').first();
  await editor.waitFor({timeout: t(20000)});
  await editor.locator('details[data-bot-advanced] summary').click();
  const slug = editor.locator('input[name=slug]');
  assert.equal(await slug.inputValue(), 'sales-helper', `${where}: the slug field shows its slug`);
  await slug.fill('revenue-helper');
  const saved = page.waitForResponse(r => r.request().method() === 'POST'
    && new URL(r.url()).pathname === `/api/v2/bots/${bot}/definition`);
  await editor.locator('button[type=submit]').click();
  assert.equal((await saved).status(), 200, `${where}: the rename saves`);
  await until(async () => (await call(hub, 'dana', 'GET', 'bots/revenue-helper')).body?.id === bot, `${where}: the new slug names it`);
  assert.equal((await call(hub, 'dana', 'GET', 'bots/sales-helper')).status, 404, `${where}: the old slug names nothing`);
  assertClean(page, `${where}: renamed`);

  // After the rename: Settings > Bots names it and links to its id; its settings show the new slug.
  go(page, '#/settings/bots');
  await drawn(page, '#set-bots', text => text.includes('Sales Helper'), `${where}: the bots list names it`);
  const links = await page.locator('#set-bots a[href^="#/bot/"]').evaluateAll(as => as.map(a => a.getAttribute('href')));
  assert.ok(links.includes('#/bot/' + encodeURIComponent(bot)), `${where}: the bots list links to its id`);
  assert.ok(!links.some(href => /sales-helper|revenue-helper/.test(href)), `${where}: no link names it by slug`);
  go(page, `#/bot/${encodeURIComponent(bot)}/settings`);
  await until(async () => (await page.locator('#pane-settings input[name=slug]').first().inputValue().catch(() => '')) === 'revenue-helper',
    `${where}: its settings show the new slug`);

  // A link by its new slug opens the same page; its task is still its own.
  go(page, '#/bot/revenue-helper');
  await page.waitForFunction(id => location.hash === '#/bot/' + id, bot, {timeout: t(20000)});
  await drawn(page, '.bot-nameline h1', text => text.includes('Sales Helper'), `${where}: the renamed bot's page`);
  const mine = await must(hub, 'dana', 'GET', `tasks?owner=${encodeURIComponent('bot:' + bot)}`);
  assert.ok((Array.isArray(mine) ? mine : mine.tasks || mine.items).some(item => item.id === task), `${where}: the task stays the bot's`);
  go(page, '#/task/' + task);
  await page.waitForFunction(text => document.querySelector('#task-modal')?.open
    && document.querySelector('#task-modal .tmodal-title')?.textContent.trim() === text, TITLE, {timeout: t(20000)});
  const owner = await page.evaluate(() => [...document.querySelectorAll('#task-modal .prop')]
    .find(r => r.querySelector('.prop-k')?.textContent.trim() === 'Owner')?.innerText || '');
  assert.match(owner, /Sales Helper|revenue-helper/, `${where}: the task's owner row names the bot`);
  assert.doesNotMatch(owner, new RegExp(bot), `${where}: the owner row shows no id`);
  assertClean(page, `${where}: after the rename`);
});
