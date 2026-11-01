// Journey: Settings loads and draws the org's data, as Dana. The people tab names the four people on the scenario org;
// the bots tab names the bot. Runs on a phone and a desktop.
const assert = require('node:assert/strict');
const {journey, open, go, assertClean, drawn, expectFailure} = require('./support/e2e.cjs');

journey('e2e-settings', async ({hub, browser, phone}) => {
  const where = phone ? 'phone' : 'desktop';
  const page = await open(browser, hub, 'dana', {phone, hash: '#/settings/people'});
  // No team icon is set on the scenario install, so the icon request Settings makes (when its icon control is drawn, on
  // whichever tab loads first) is answered 404 ("No team icon is set"). Expected on every tab of this journey.
  expectFailure(page, 404, '^/api/v2/team/icon$', 'GET');
  const text = await drawn(page, '#main', text => ['Dana', 'Omar', 'Priya', 'Gina'].every(name => text.includes(name)),
    `${where}: the people tab draws the four people`);
  for (const name of ['Dana', 'Omar', 'Priya', 'Gina']) assert.match(text, new RegExp(name), `${where}: people tab names ${name}`);
  assertClean(page, `${where}: people`);

  go(page, '#/settings/bots');
  // The bots list draws into #set-bots after its skeleton rows load: wait for the bot's row there, not elsewhere on the page.
  const bots = await drawn(page, '#set-bots', text => text.includes('engineering-lead'), `${where}: the bots tab draws the bot`);
  assert.match(bots, /engineering-lead/, `${where}: bots tab names the bot`);
  assertClean(page, `${where}: bots`);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, `${where}: settings scrolls sideways by ${overflow}px`);
});
