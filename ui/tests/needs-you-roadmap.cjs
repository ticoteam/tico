// Needs you and Decide on a Product roadmap item: it finishes or comes back only by a step move, so neither offers
// Done, Reopen or Close on it, as the task window does not; a plain task keeps all three. Decide's card opens the task
// instead, and its D key sends nothing.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const at = minutes => new Date(Date.now() - minutes * 60000).toISOString();
const card = (id, title, extra = {}) => ({id, kind: 'task', title, body: '', owner: 'human:ana', requester: 'bot:writer', status: 'open',
  version: 1, labels: [], created: at(60), updated: at(60), conversation_id: 'c-' + id, ask: null, open_asks: 0, ...extra});
const roadmap = {type_id: 'product-roadmap', type: {id: 'product-roadmap', name: 'Product roadmap'}, steps_only: true};
const items = [card('t-idea', 'Dark mode for the roadmap', roadmap),
  card('t-shipped', 'Bulk export', {...roadmap, status: 'done'}), card('t-plain', 'Book the venue', {steps_only: false})];
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors, posts} = await open(browser, {viewport: {width: 390, height: 844}, hash: '#/tasks', extraTasks: items.map(x => ({...x}))});
    const acts = await page.evaluate(items => Object.fromEntries(items.map(it => {
      const host = document.createElement('div'); host.innerHTML = needsV2Item(it, false);
      return [it.id, [...host.querySelectorAll('[data-v2-status], [data-v2-close]')].map(b => b.textContent.trim())];
    })), items);
    assert.deepEqual(acts['t-idea'], [], 'no Done or Close on a roadmap item');
    assert.deepEqual(acts['t-shipped'], [], 'no Reopen or Close on a shipped one');
    assert.deepEqual(acts['t-plain'], ['Done', 'Close'], 'a plain task keeps Done and Close');
    // A card read before the server said so is still known by its type, once the types are loaded.
    assert.equal(await page.evaluate(() => { TASK_TYPES.push({id: 'product-roadmap', name: 'Product roadmap', steps_only: true, steps: []});
      return needsStepsOnly({type_id: 'product-roadmap'}); }), true);
    // Yes or No on the person's own roadmap item goes as a comment, since the server refuses its bare status write;
    // on their own plain task it still finishes or declines it.
    const said = await page.evaluate(items => {
      const me = myActor(), [idea, , plain] = items.map(it => ({type: 'task', it: {...it, owner: me}}));
      return Object.fromEntries([['idea', idea], ['plain', plain]].flatMap(([k, c]) => [['yes', ''], ['no', 'not this quarter']]
        .map(([what, note]) => [`${k}-${what}`, decideWriteBody(c, what, 0, note).writes.map(w => [w.path, w.body.text ?? w.body.status])])));
    }, items);
    assert.deepEqual(said, {
      'idea-yes': [['/v2/tasks/t-idea/comments', 'Yes.']], 'idea-no': [['/v2/tasks/t-idea/comments', 'No. not this quarter']],
      'plain-yes': [['/v2/tasks/t-plain', 'done']], 'plain-no': [['/v2/tasks/t-plain', 'declined']]});

    await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items: [items[0]]})}));
    await page.route('**/api/v2/tasks/*/comments', route => route.fulfill({contentType: 'application/json', body: '{"comments":[]}'}));
    await page.route('**/api/v2/tasks/*/files', route => route.fulfill({contentType: 'application/json', body: '{"files":[]}'}));
    await page.evaluate(async () => { await v2Refresh(); location.hash = DECIDE; });
    await page.locator('.decide .dc-card[data-key="task:t-idea"]').waitFor();
    await page.waitForFunction(() => DEC && !DEC.busy && performance.now() >= (DEC.lockUntil || 0));
    assert.equal(await page.locator('.decide .dc-actions [data-dc="done"]').count(), 0, 'Decide offers no Done on it');
    assert.equal(await page.locator('.decide .dc-actions a.button').getAttribute('href'), '#/task/t-idea', 'it opens the task to move its step');
    await page.keyboard.press('d');
    await page.waitForTimeout(300);
    assert.equal(posts.filter(x => x.p === '/api/v2/tasks/t-idea').length, 0, 'D sends no status');
    assert.equal(await page.locator('.decide .dc-card[data-key="task:t-idea"]').count(), 1, 'the card stays');
    assert.deepEqual(errors, []);
    console.log('Needs you and Decide: no Done, Reopen or Close on a Product roadmap item; a plain task keeps them');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
