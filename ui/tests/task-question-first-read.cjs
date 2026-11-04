// A task with a question to you, opened before the first Needs you list is held (boot sends that read after the roster
// and the opening page): the plain ask shows, and its question box takes its place once a list lands, without
// reopening the task. A first read that fails keeps the wait for the next one that answers (ui/app/task-modal.js
// taskQcardWait, ui/app/hub-v2.js NEEDS_FIRST).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const NOW = Date.now(), at = hours => new Date(NOW - hours * 3600e3).toISOString();
const late = {id: 't-late', kind: 'question', title: 'Which launch date?', body: '', owner_id: 'bot:writer', requester_id: 'bot:writer',
  status: 'waiting', waiting_on: 'human:ana', version: 1, created_at: at(5), updated_at: at(5), open_asks: 1, labels: [], tags: [],
  links: [], lane: 'company', rank: null, parts: {total: 0, done: 0}, tier: 1,
  ask: {id: 'q-late', sender_id: 'bot:writer', recipient_id: 'human:ana', body: 'Launch on Monday or Thursday?', created_at: at(5),
    refs: {task: 't-late', questions: [{id: 'when', header: '', question: 'Launch on Monday or Thursday?', multi: false, other: true,
      options: [{label: 'Monday', then: 'It goes out Monday'}, {label: 'Thursday', then: 'It goes out Thursday'}]}]}}};

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    let release, reads = 0;
    const held = new Promise(done => { release = done; });
    const {page, errors} = await open(browser, {hash: '#/tasks', extraTasks: [{...late}], beforeGoto: async p => {
      await p.route('**/api/v2/needs-you*', async route => {
        // the first read waits, then fails; the next one answers
        if (++reads === 1) { await held; return route.fulfill({status: 500, contentType: 'application/json', body: '{"error":{"detail":"busy"}}'}); }
        await route.fulfill({contentType: 'application/json', body: JSON.stringify({items: [late], version: 'v1', count: 1})});
      });
      await p.route('**/api/v2/tasks/*/comments', route => route.fulfill({contentType: 'application/json', body: '{"comments":[]}'}));
      await p.route('**/api/v2/tasks/*/files', route => route.fulfill({contentType: 'application/json', body: '{"files":[]}'}));
    }});
    assert.equal(await page.evaluate(() => needsFirstIn()), false, 'the first Needs you read is still out');
    await page.evaluate(() => { location.hash = '#/task/t-late'; });
    const wait = page.locator('.task-ask[data-qcard-wait="t-late"]'), box = page.locator('.qcard[data-qcard="t-late"]');
    await wait.waitFor();
    assert.equal(await box.count(), 0);
    release();
    await page.waitForFunction(() => S.statusPending === false);       // boot's reads are back, the Needs you one failed
    await page.waitForTimeout(200);
    assert.equal(await page.evaluate(() => needsFirstIn()), false, 'a failed read holds no list');
    assert.equal(await wait.count(), 1, 'the plain ask still waits');
    await page.evaluate(() => v2Refresh());                           // the next refresh answers
    await box.waitFor();
    assert.equal(await wait.count(), 0);
    // The list held the item, so no read of it alone followed.
    assert.equal(reads, 2);
    assert.deepEqual(errors, []);
    console.log('Task question before the first Needs you list: plain ask first, question box once a list lands, through a failed first read');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
