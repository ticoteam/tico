// Journey: an automation, as Dana. A scheduled automation is made through the API (as the editor saves it); the list shows
// it; a run-now call fires it, and its run appears in the automation's runs. Runs on a phone and a desktop.
const assert = require('node:assert/strict');
const {journey, open, go, must, assertClean, drawn, shot, t} = require('./support/e2e.cjs');

const NAME = 'Friday status check';

journey('e2e-automations', async ({hub, browser, phone}) => {
  const where = phone ? 'phone' : 'desktop';
  const made = await must(hub, 'dana', 'POST', 'automations', {
    name: NAME, owner_id: 'person:dana', enabled: true, builtin: false,
    trigger: {type: 'schedule', cron: '0 16 * * 5', tz: 'UTC'}, steps: [],
  }, 'make the automation');
  const id = made.id || made.automation?.id;
  assert.ok(id, `${where}: the create returns the automation id`);

  const page = await open(browser, hub, 'dana', {phone, hash: '#/automations'});
  await page.locator('#main', {hasText: NAME}).first().waitFor({timeout: t(20000)});
  assertClean(page, `${where}: automations listed`);

  go(page, `#/automations/${id}`);
  await page.locator('#main', {hasText: NAME}).first().waitFor({timeout: t(20000)});
  assertClean(page, `${where}: automation open`);

  await must(hub, 'dana', 'POST', `automations/${id}/run`, {input: {}}, 'fire it now');
  const runs = await must(hub, 'dana', 'GET', `automations/${id}/runs`, undefined, 'read its runs');
  const list = runs.runs || runs.items || [];
  assert.ok(list.length >= 1, `${where}: the hub has a run`);
  await page.reload();
  await page.locator('#main', {hasText: NAME}).first().waitFor({timeout: t(20000)});
  // The run is in the Runs tab: its time and its outcome.
  await page.locator('#main button, #main a, #main [role=tab]', {hasText: /^Runs$/}).first().click();
  const text = await drawn(page, '#main', text => /\d{1,2}:\d{2} [AP]M/.test(text) && /Done/.test(text),
    `${where}: the run row draws its time and outcome`);
  assert.match(text, /Done/, `${where}: the run row shows its outcome`);
  assertClean(page, `${where}: run drawn`);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, `${where}: automations scroll sideways by ${overflow}px`);
  await shot(page, `e2e-automation-${where}`);
});
