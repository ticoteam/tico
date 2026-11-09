// The Needs you card on a Product roadmap item: it finishes or comes back only by a step move, so the card offers no
// Done, as the task window does not; it opens the task instead, and its D key sends nothing. A yes or no on the
// person's own roadmap item goes as a comment; on a plain task it still finishes or declines it.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const at = minutes => new Date(Date.now() - minutes * 60000).toISOString();
const card = (id, title, extra = {}) => ({id, kind: 'task', title, body: '', owner_id: 'human:ana', requester_id: 'bot:writer', status: 'open',
  version: 1, labels: [], tags: [], links: [], lane: 'company', rank: null, parts: {total: 0, done: 0}, created_at: at(60), updated_at: at(60),
  conversation_id: 'c-' + id, ask: null, open_asks: 0, ...extra});
const roadmap = {type_id: 'product-roadmap', type: {id: 'product-roadmap', name: 'Product roadmap'}, steps_only: true};
const REC = {ask: 'q-idea', do: 'Ship it in November', option: 'November', kind: 'reversible'};
const items = [card('t-idea', 'Dark mode for the roadmap', {...roadmap, recommend: REC}), card('t-plain', 'Book the venue', {steps_only: false})];
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors, posts} = await open(browser, {viewport: {width: 390, height: 844}, hash: '#/tasks', extraTasks: items.map(x => ({...x}))});
    // A card read before the server said so is still known by its type, once the types are loaded.
    assert.equal(await page.evaluate(() => { TASK_TYPES.push({id: 'product-roadmap', name: 'Product roadmap', steps_only: true, steps: []});
      return decideStepsOnly({type: 'task', it: {type_id: 'product-roadmap'}}); }), true);
    const said = await page.evaluate(items => {
      const me = myActor(), [idea, plain] = items.map(it => ({type: 'task', it: {...it, owner_id: me}}));
      return Object.fromEntries([['idea', idea], ['plain', plain]].flatMap(([k, c]) => [['yes', ''], ['no', 'not this quarter']]
        .map(([what, note]) => [`${k}-${what}`, decideWriteBody(c, what, 0, note).writes.map(w => [w.path, w.body.text ?? w.body.status])])));
    }, items);
    assert.deepEqual(said, {
      'idea-yes': [['/v2/tasks/t-idea/comments', 'Yes.']], 'idea-no': [['/v2/tasks/t-idea/comments', 'No. not this quarter']],
      'plain-yes': [['/v2/tasks/t-plain', 'done']], 'plain-no': [['/v2/tasks/t-plain', 'declined']]});

    await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items: [items[0]]})}));
    await page.route('**/api/v2/tasks/*/comments', route => route.fulfill({contentType: 'application/json', body: '{"comments":[]}'}));
    await page.route('**/api/v2/tasks/*/files', route => route.fulfill({contentType: 'application/json', body: '{"files":[]}'}));
    await page.evaluate(async () => { await v2Refresh(); decideOpen('task:t-idea'); });
    await page.locator('#need-card .dc-card[data-key="task:t-idea"]').waitFor();
    await page.waitForFunction(() => DEC && !DEC.busy && performance.now() >= (DEC.lockUntil || 0));
    assert.equal(await page.locator('#need-card .dc-actions [data-dc="done"]').count(), 0, 'the card offers no Done on it');
    assert.equal(await page.locator('#need-card .dc-actions a.button').getAttribute('href'), '#/task/t-idea', 'it opens the task to move its step');
    await page.keyboard.press('d');
    await page.waitForTimeout(300);
    assert.equal(posts.filter(x => x.p === '/api/v2/tasks/t-idea').length, 0, 'D sends no status');
    assert.equal(await page.locator('#need-card .dc-card[data-key="task:t-idea"]').count(), 1, 'the card stays');
    assert.deepEqual(errors, []);
    console.log('Needs you card: no Done on a Product roadmap item, its yes or no goes as a comment; a plain task keeps them');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
