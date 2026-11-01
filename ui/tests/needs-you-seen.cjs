// Needs you: a question to me is delivered once my list holds it (sent once, never again for the same list) and read
// once its card shows me it (decide.js decideAskRead); a question to someone else, or one already delivered, is not
// sent (hub-v2.js needsSeen).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const at = minutes => new Date(Date.now() - minutes * 60000).toISOString();
const ask = (id, to, delivered = null) => ({id, to_actor: to, from_actor: 'bot:writer', body: 'Approve the venue?', delivered_at: delivered, read_at: null});
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
    assert.deepEqual(errors, []);
    console.log('Needs you: my questions are delivered once and read when their card shows them');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
