// Journey: a conversation with a bot, as Dana. She opens the engineering-lead bot's chat, sends a message, and the thread
// draws it as hers, with its time. The bot answers through its runner (not in this journey). Runs on a phone and a desktop.
const assert = require('node:assert/strict');
const {journey, open, assertClean, shot, t} = require('./support/e2e.cjs');

journey('e2e-chat', async ({hub, browser, phone}) => {
  const where = phone ? 'phone' : 'desktop';
  const page = await open(browser, hub, 'dana', {phone, hash: '#/bot/engineering-lead'});
  const composer = page.locator('#conv textarea.p-text, textarea[aria-label="Message this bot"]').first();
  await composer.waitFor({timeout: t(20000)});
  assertClean(page, `${where}: chat opened`);

  const text = 'Can you list the open launch tasks?';
  await composer.fill(text);
  await page.locator('#conv button.p-send, button.p-send[aria-label="Send"]').first().click();

  const bubble = page.locator('#conv .bubble.you', {hasText: text}).first();
  await bubble.waitFor({timeout: t(20000)});
  const who = (await bubble.locator('.who').innerText()).trim();
  assert.ok(who.length > 0, `${where}: the message names its sender`);
  const when = await bubble.evaluate(el => el.querySelector('time')?.getAttribute('datetime')
    || el.querySelector('time')?.getAttribute('title') || el.querySelector('[title]')?.getAttribute('title') || '');
  assert.ok(when.length > 0, `${where}: the message carries its time`);
  assert.ok(!(await page.locator('#conv .muted', {hasText: 'Conversation unavailable'}).count()), `${where}: thread loaded`);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, `${where}: chat page scrolls sideways by ${overflow}px`);
  assertClean(page, `${where}: after send`);
  await shot(page, `e2e-chat-${where}`);
});
