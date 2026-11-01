// Journey: a meeting import, as Dana. A meeting with a transcript is imported through the API (as the import tool does);
// it is listed on Meetings by its title, and its review page opens and draws the transcript. Runs on a phone and a desktop.
const assert = require('node:assert/strict');
const {journey, open, go, must, assertClean, shot, t} = require('./support/e2e.cjs');

const TITLE = 'Beta go/no-go';
const LINE = 'We agreed to ship the beta on Nov 3 if the review queue stays under six hours.';

journey('e2e-meetings', async ({hub, browser, phone}) => {
  const where = phone ? 'phone' : 'desktop';
  const made = await must(hub, 'dana', 'POST', 'meetings/import', {
    title: TITLE, started_at: '2026-10-30T15:00:00Z', duration_seconds: 1800,
    participants: ['Dana', 'Omar'], source: 'e2e', external_id: 'e2e-beta-1',
    transcript: `Dana: ${LINE}\nOmar: Review time is at six hours today.`, format: 'text',
    notes: 'Go for Nov 3.', review: 'pending',
  }, 'import the meeting');
  const id = made.id || made.meeting?.id;
  assert.ok(id, `${where}: the import returns the meeting id`);

  const page = await open(browser, hub, 'dana', {phone, hash: '#/meetings'});
  await page.locator('#main', {hasText: TITLE}).first().waitFor({timeout: t(20000)});
  assertClean(page, `${where}: meetings listed`);

  go(page, `#/meetings?meeting=${id}`);
  await page.locator('#main', {hasText: LINE}).first().waitFor({timeout: t(20000)});
  const text = await page.locator('#main').innerText();
  assert.match(text, new RegExp(TITLE), `${where}: the review page names the meeting`);
  assertClean(page, `${where}: review page`);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, `${where}: meetings scroll sideways by ${overflow}px`);
  await shot(page, `e2e-meeting-${where}`);
});
