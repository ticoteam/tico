// Journey: a conversation with a bot, as Dana. She opens her DM with the engineering-lead bot, sends a message, and the thread
// draws it as hers, with its time. The bot answers through its runner (not in this journey). Runs on a phone and a desktop.
const assert = require('node:assert/strict');
const {journey, open, assertClean, expectFailure, shot, t} = require('./support/e2e.cjs');

journey('e2e-chat', async ({hub, browser, phone}) => {
  const where = phone ? 'phone' : 'desktop';
  const page = await open(browser, hub, 'dana', {phone, hash: '#/bot/engineering-lead/dm'});
  // Her DM with it is made by her first message: until then the lookup answers 404 (the empty DM).
  expectFailure(page, 404, '^/api/v2/threads/dm$', 'GET');
  const composer = page.locator('#bot-thread .thv-composer textarea.p-text').first();
  await composer.waitFor({timeout: t(20000)});
  assertClean(page, `${where}: chat opened`);

  const text = 'Can you list the open launch tasks?';
  await composer.fill(text);
  await page.locator('#bot-thread .thv-composer button.p-send').first().click();

  const bubble = page.locator('#bot-thread [data-message]', {hasText: text}).first();
  await bubble.waitFor({timeout: t(20000)});
  const who = (await bubble.locator('.th-who').first().innerText()).trim();
  assert.ok(who.length > 0, `${where}: the message names its sender`);
  const when = await bubble.evaluate(el => el.querySelector('time')?.getAttribute('datetime')
    || el.querySelector('time')?.getAttribute('title') || el.querySelector('[title]')?.getAttribute('title') || '');
  assert.ok(when.length > 0, `${where}: the message carries its time`);
  assert.ok(!(await page.locator('#bot-thread .thv-gone, #bot-thread .thv-error').count()), `${where}: thread loaded`);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, `${where}: chat page scrolls sideways by ${overflow}px`);
  assertClean(page, `${where}: after send`);
  await shot(page, `e2e-chat-${where}`);
});
