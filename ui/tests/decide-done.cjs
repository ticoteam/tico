// Decide: Done on a bot's request is one tap. With the note box empty the card goes and Done carries no note; with
// words in it, they go with Done as the outcome. Nothing asks what was decided first.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const at = minutes => new Date(Date.now() - minutes * 60000).toISOString();
const request = (id, title, created) => ({id, kind: 'task', title, body: '', owner: 'human:ana', requester: 'bot:writer', status: 'open',
  version: 1, labels: [], created: at(created), updated: at(created), ask: null, open_asks: 0});
const items = [request('t-tap', 'Approve the launch post', 200), request('t-noted', 'Pick the launch date', 300)];   // newest first
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors, posts} = await open(browser, {viewport: {width: 390, height: 844}, hash: '#/tasks', extraTasks: items.map(x => ({...x}))});
    await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items})}));
    await page.route('**/api/v2/tasks/*/comments', route => route.fulfill({contentType: 'application/json', body: '{"comments":[]}'}));
    await page.route('**/api/v2/tasks/*/files', route => route.fulfill({contentType: 'application/json', body: '{"files":[]}'}));
    await page.evaluate(async () => { await v2Refresh(); location.hash = DECIDE; });
    const ready = async id => {
      await page.locator(`.decide .dc-card[data-key="task:${id}"]`).waitFor();
      await page.waitForFunction(() => DEC && !DEC.busy && performance.now() >= (DEC.lockUntil || 0));
    };
    await ready('t-tap');
    assert.equal(await page.locator('.decide .dc-note textarea').getAttribute('placeholder'), 'Add a comment', 'the box does not ask for a decision');
    await page.locator('.decide .dc-actions [data-dc="done"]').click();
    await ready('t-noted');                        // one tap: the card went, nothing was asked
    await page.locator('.decide .dc-note textarea').fill('Tuesday the 20th.');
    await page.locator('.decide .dc-actions [data-dc="done"]').click();
    await page.waitForFunction(() => !DEC?.pending && !DEC_OUTBOX.size, null, {timeout: 10000});
    const done = id => posts.filter(x => x.p === `/api/v2/tasks/${id}`).map(x => x.body);
    assert.deepEqual(done('t-tap'), [{status: 'done', version: 1}]);
    assert.deepEqual(done('t-noted'), [{status: 'done', version: 1, note: 'Tuesday the 20th.'}]);
    assert.deepEqual(errors, []);
    console.log("Decide: Done on a bot's request is one tap; a typed note goes with it");
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
