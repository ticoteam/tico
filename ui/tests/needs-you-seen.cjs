// Needs you: a question to me is delivered once my list holds it (sent once, never again for the same list) and read
// once its card shows me it, option cards included (decide.js decideAskRead); a question to someone else, or one already
// delivered, is not sent (hub-v2.js needsSeen). A task's questions are read on each open of the full task.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const at = minutes => new Date(Date.now() - minutes * 60000).toISOString();
const ask = (id, to, delivered = null) => ({id, recipient_id: to, sender_id: 'bot:writer', body: 'Approve the venue?', delivered_at: delivered, read_at: null});
const request = (id, title, a) => ({id, kind: 'task', title, body: 'Which venue?', owner: 'human:ana', requester: 'bot:writer', status: 'open',
  version: 1, labels: [], created: at(60), updated: at(60), conversation_id: 'c-' + id, ask: a, open_asks: 1});
const loose = (id, to) => ({id, kind: 'ask', body: 'Tacos or pizza?', requester: 'bot:writer', created: at(30), ask: ask(id, to)});
const items = [request('t-mine', 'Pick the venue', ask('m-mine', 'human:ana')),
  request('t-old', 'Pick the date', ask('m-old', 'human:ana', at(5))),
  request('t-ben', 'Approve the budget', ask('m-ben', 'human:ben')),
  loose('m-loose', 'human:ana')];
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {hash: '#/tasks'});
    const sent = [];
    page.on('request', r => { if (r.method() === 'POST' && /\/(seen|ack)$/.test(new URL(r.url()).pathname)) sent.push({p: new URL(r.url()).pathname, body: r.postDataJSON()}); });
    await page.evaluate(items => needsTake({items: structuredClone(items), version: 'v1', count: items.length}), items);
    await page.evaluate(items => needsTake({items: structuredClone(items), version: 'v1', count: items.length}), items);
    await page.waitForTimeout(200);
    assert.deepEqual(sent, [{p: '/api/v2/needs-you/seen', body: {asks: ['m-mine', 'm-loose']}}], 'my undelivered questions, once');

    await page.evaluate(() => { decideStop(); decideOpen('task:t-mine'); });
    await page.waitForTimeout(200);
    assert.deepEqual(sent.slice(1), [{p: '/api/v2/tasks/t-mine/seen', body: {}}], 'showing my task card reads its question');
    await page.evaluate(() => { decideStop(); decideOpen('task:t-ben'); });
    await page.waitForTimeout(200);
    assert.equal(sent.length, 2, 'showing a card that asks someone else reads nothing');
    await page.evaluate(() => { decideStop(); decideOpen('ask:m-loose'); });
    await page.waitForTimeout(200);
    assert.deepEqual(sent.slice(2), [{p: '/api/v2/messages/m-loose/ack', body: {}}], 'showing my loose question reads it');

    // An option question (two to six choices) on its card: delivered on Needs you, then read when its card shows it,
    // without an answer.
    const pick = {id: 't-pick', kind: 'question', title: 'Pick the caterer', body: 'Which caterer?', owner: 'human:ana',
      requester: 'bot:writer', status: 'open', version: 1, labels: [], created: at(20), updated: at(20), conversation_id: 'c-t-pick',
      ask: {id: 'm-pick', sender_id: 'bot:writer', recipient_id: 'human:ana', body: 'Which caterer?', delivered_at: null, read_at: null,
        created_at: at(20), refs: {questions: [{id: 'caterer', header: '', question: 'Which caterer?', multi: false, other: false,
          options: [{label: 'Tacos'}, {label: 'Pizza'}]}]}}};
    let mark = sent.length;
    await page.evaluate(items => needsTake({items: structuredClone(items), version: 'v2', count: items.length}), [pick]);
    await page.waitForTimeout(200);
    assert.deepEqual(sent.slice(mark), [{p: '/api/v2/needs-you/seen', body: {asks: ['m-pick']}}], 'the option question is delivered');
    mark = sent.length;
    await page.evaluate(() => { decideStop(); decideOpen('task:t-pick'); });
    await page.waitForTimeout(200);
    assert.deepEqual(sent.slice(mark), [{p: '/api/v2/tasks/t-pick/seen', body: {}}], 'showing an option card reads its question');

    // A question read on a task, then a second one asked on the same task: its card reads the second one, and opening
    // the full task again sends a request too (a question asked after the first open is read only by a request then).
    mark = sent.length;
    const second = request('t-mine', 'Pick the venue', ask('m-second', 'human:ana'));
    await page.evaluate(item => needsTake({items: [structuredClone(item)], version: 'v3', count: 1}), second);
    await page.waitForTimeout(200);
    assert.deepEqual(sent.slice(mark), [{p: '/api/v2/needs-you/seen', body: {asks: ['m-second']}}], 'the second question is delivered');
    mark = sent.length;
    await page.evaluate(() => { decideStop(); decideOpen('task:t-mine'); });
    await page.waitForTimeout(200);
    assert.deepEqual(sent.slice(mark), [{p: '/api/v2/tasks/t-mine/seen', body: {}}], 'its card reads the second question');
    mark = sent.length;
    await page.evaluate(() => taskAsksRead('t-mine'));          // opening the full task on the same page
    await page.waitForTimeout(200);
    assert.deepEqual(sent.slice(mark), [{p: '/api/v2/tasks/t-mine/seen', body: {}}], 'reopening the full task sends again');

    // A complete feed can contain more than one delivery batch. Every question is delivered, with bounded writes.
    mark = sent.length;
    await page.evaluate(items => needsTake({items, version: 'many', count: items.length}),
      Array.from({length: 205}, (_, n) => loose('batch-' + n, 'human:ana')));
    await page.waitForTimeout(200);
    const delivered = sent.slice(mark).filter(r => r.p === '/api/v2/needs-you/seen').map(r => r.body.asks);
    assert.deepEqual(delivered.map(ids => ids.length), [200, 5]);
    assert.equal(new Set(delivered.flat()).size, 205, 'no question is lost or delivered twice');
    assert.deepEqual(errors, []);
    console.log('Needs you: my questions are delivered once and read when their card shows them');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
