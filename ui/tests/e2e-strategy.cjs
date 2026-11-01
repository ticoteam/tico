// Journey: Strategy draws seeded data, as Dana. A bet and a goal are made through the API (as Dana makes them); the
// strategy page shows the bet's statement, and the goals tab shows the goal. Runs on a phone and a desktop.
const assert = require('node:assert/strict');
const {journey, open, go, must, assertClean, t} = require('./support/e2e.cjs');

const BET = 'Win the small-team segment this quarter';
const GOAL = 'Reach 100 paying teams';

journey('e2e-strategy', async ({hub, browser, phone}) => {
  const where = phone ? 'phone' : 'desktop';
  await must(hub, 'dana', 'POST', 'strategy/bets', {statement: BET, why: 'Small teams close fastest.', status: 'active'}, 'make a bet');
  await must(hub, 'dana', 'POST', 'goals', {title: GOAL, owner_id: 'dana', top: true}, 'make a goal');

  const page = await open(browser, hub, 'dana', {phone, hash: '#/strategy'});
  await page.locator('#main', {hasText: BET}).first().waitFor({timeout: t(20000)});
  assertClean(page, `${where}: strategy`);

  go(page, '#/strategy/goals');
  await page.locator('#main', {hasText: GOAL}).first().waitFor({timeout: t(20000)});
  assertClean(page, `${where}: goals`);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, `${where}: strategy scrolls sideways by ${overflow}px`);
});
