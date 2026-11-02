// The Needs you card: Done on a bot's request is one tap. With the card's box empty the card goes and Done carries no
// note; with words in it, they go with Done as the outcome. Nothing asks what was decided first. A comment typed
// earlier in the task window (an unsent draft there) never goes with the card's Done and stays for its task.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const at = minutes => new Date(Date.now() - minutes * 60000).toISOString();
// The bot says what it would do, so the request is answered on a card (a request with nothing to pick opens whole).
const request = (id, title, created) => ({id, kind: 'task', title, body: '', owner: 'human:ana', requester: 'bot:writer', status: 'open',
  version: 1, labels: [], tags: [], links: [], lane: 'company', rank: null, parts: {total: 0, done: 0}, created_at: at(created), updated_at: at(created),
  ask: {id: 'q-' + id, from_actor: 'bot:writer', to_actor: 'human:ana', body: 'Which one?', created_at: at(created)}, open_asks: 1,
  recommend: {ask: 'q-' + id, do: 'The first', option: 'The first', kind: 'reversible'}});
const items = [request('t-tap', 'Approve the launch post', 200), request('t-noted', 'Pick the launch date', 300)];   // newest first
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors, posts} = await open(browser, {viewport: {width: 390, height: 844}, hash: '#/tasks', extraTasks: items.map(x => ({...x}))});
    await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items})}));
    await page.route('**/api/v2/tasks/*/comments', route => route.fulfill({contentType: 'application/json', body: '{"comments":[]}'}));
    await page.route('**/api/v2/tasks/*/files', route => route.fulfill({contentType: 'application/json', body: '{"files":[]}'}));
    await page.evaluate(async () => { TASK_DRAFTS.set('t-tap', 'Half a thought from the task window'); await v2Refresh(); decideOpen('task:t-tap'); });
    const ready = async id => {
      await page.locator(`#need-card .dc-card[data-key="task:${id}"]`).waitFor();
      await page.waitForFunction(() => DEC && !DEC.busy && performance.now() >= (DEC.lockUntil || 0));
    };
    await ready('t-tap');
    assert.equal(await page.locator('#need-card .dc-note textarea').getAttribute('placeholder'), 'Add a comment', 'the box does not ask for a decision');
    await page.locator('#need-card .dc-actions [data-dc="done"]').click();
    await ready('t-noted');                        // one tap: the card went, nothing was asked
    await page.locator('#need-card .dc-note textarea').fill('Tuesday the 20th.');
    await page.locator('#need-card .dc-actions [data-dc="done"]').click();
    await page.waitForFunction(() => !DEC?.pending && !DEC_OUTBOX.size, null, {timeout: 10000});
    const done = id => posts.filter(x => x.p === `/api/v2/tasks/${id}`).map(x => x.body);
    assert.deepEqual(done('t-tap'), [{status: 'done', version: 1}], 'the card box was empty: no note');
    assert.equal(await page.evaluate(() => TASK_DRAFTS.get('t-tap')), 'Half a thought from the task window', 'the task window draft stays for its task');
    assert.deepEqual(done('t-noted'), [{status: 'done', version: 1, note: 'Tuesday the 20th.'}], 'the card box goes with Done');
    assert.equal(posts.filter(x => x.p.endsWith('/comments')).length, 0, 'nothing is also sent as a comment');
    assert.deepEqual(errors, []);
    console.log("Needs you card: Done on a bot's request is one tap; only the card's own note goes with it");
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
