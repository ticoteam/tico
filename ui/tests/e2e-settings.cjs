// Journey: Settings loads and draws the org's data, as Dana. The people tab names the four people on the scenario org;
// the bots tab names the bot. Runs on a phone and a desktop.
const assert = require('node:assert/strict');
const {journey, open, go, assertClean, expectFailure, t} = require('./support/e2e.cjs');

journey('e2e-settings', async ({hub, browser, phone}) => {
  const where = phone ? 'phone' : 'desktop';
  const page = await open(browser, hub, 'dana', {phone, hash: '#/settings/people'});
  // No team icon is set on the scenario install, so the icon request Settings makes (when its icon control is drawn, on
  // whichever tab loads first) is answered 404 ("No team icon is set"). Expected on every tab of this journey.
  expectFailure(page, 404, '^/api/v2/team/icon$', 'GET');
  await page.locator('#main', {hasText: 'Omar'}).waitFor({timeout: t(20000)});
  let text = await page.locator('#main').innerText();
  for (const name of ['Dana', 'Omar', 'Priya', 'Gina']) assert.match(text, new RegExp(name), `${where}: people tab names ${name}`);
  assertClean(page, `${where}: people`);

  go(page, '#/settings/bots');
  await page.locator('#main', {hasText: 'engineering-lead'}).waitFor({timeout: t(20000)});
  text = await page.locator('#main').innerText();
  assert.match(text, /engineering-lead/, `${where}: bots tab names the bot`);
  assertClean(page, `${where}: bots`);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, `${where}: settings scrolls sideways by ${overflow}px`);
});
