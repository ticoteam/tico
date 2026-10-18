// The Stale tickets card in Needs you (backend/stale_review.py): one card, a row per task with its reason and
// suggestion; a row with nothing to accept has only Skip. Accept sends the task version its row showed, and Accept all
// sends the rows on screen with theirs, so a suggestion that arrived after the card was drawn is never applied unseen.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const row = (id, task, title, action, version) => ({id, task, title, status: 'open', reason: 'Nothing happened on it for 9 days',
  suggest: action === 'done' ? 'Mark done' : 'Check it is still needed', action, version});
const card = {kind: 'stale', id: 'stale:human:ana', title: 'Stale tickets: 3 suggestions', first_line: '',
  created: new Date().toISOString(), need: 'decide',
  rows: [row('s-1', 't-1', 'Old chore one', 'done', 4), row('s-2', 't-2', 'Old chore two', null, 2),
    row('s-3', 't-3', 'Old chore three', 'done', 9)]};
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {hash: '#/tasks'});
    const sent = [];
    await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items: [card]})}));
    await page.route('**/api/v2/stale-review/*', route => {
      const p = new URL(route.request().url()).pathname;
      sent.push([p.split('/').pop(), route.request().postDataJSON()]);
      const body = p.endsWith('accept-all') ? {accepted: [{id: 's-1', task: 't-1', did: 'done'}], refused: []} : {id: 's-1', outcome: 'accepted'};
      return route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    });
    await page.evaluate(async () => { await v2Refresh(); tasksRender(TASKS_ST); });
    await page.locator('#task-view [data-view="foryou"]').click();
    const shown = page.locator('[data-group="stale"] details[data-stale-card]');
    await shown.first().waitFor();
    assert.equal(await shown.count(), 1, 'one card for every suggestion');
    assert.equal((await shown.locator('.req-text').textContent()).trim(), 'Tico: Stale tickets: 3 suggestions');
    const rows = shown.locator('[data-stale-row]');
    assert.equal(await rows.count(), 3);
    assert.equal(await rows.nth(1).locator('[data-stale-decision="accept"]').count(), 0, 'nothing to accept: Skip only');
    assert.equal(await rows.nth(1).locator('[data-stale-decision="skip"]').count(), 1);

    await rows.nth(0).locator('[data-stale-decision="accept"]').click();
    for (let i = 0; i < 50 && !sent.length; i++) await page.waitForTimeout(20);
    assert.deepEqual(sent.shift(), ['s-1', {decision: 'accept', version: 4}], 'Accept names the version the row showed');

    // A suggestion reaches the page's list but the card is not drawn again yet: Accept all sends what is on screen.
    await page.evaluate(() => {
      const it = S.v2.needs.find(x => x.kind === 'stale');
      it.rows.push({id: 's-4', task: 't-4', title: 'Unseen', status: 'open', reason: 'x', suggest: 'Mark done', action: 'done', version: 1});
    });
    await shown.locator('[data-stale-all]').click();
    for (let i = 0; i < 50 && !sent.length; i++) await page.waitForTimeout(20);
    assert.deepEqual(sent.shift(), ['accept-all', {rows: [{id: 's-1', version: 4}, {id: 's-3', version: 9}]}],
      'Accept all sends the rows shown that have an action, never one added since');
    assert.deepEqual(errors, []);
    console.log('Needs you stale card: one card, Skip-only rows, Accept and Accept all send what the card showed');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
