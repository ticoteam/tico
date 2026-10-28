// Needs you: a question to me is delivered once my list holds it (sent once, never again for the same list) and read
// once I open its card; a question to someone else, or one already delivered, is not sent (hub-v2.js needsSeen).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const at = minutes => new Date(Date.now() - minutes * 60000).toISOString();
const ask = (id, to, delivered = null) => ({id, to_actor: to, from_actor: 'bot:writer', delivered_at: delivered, read_at: null});
const request = (id, title, a) => ({id, kind: 'task', title, body: '', owner: 'human:ana', requester: 'bot:writer', status: 'open',
  version: 1, labels: [], created: at(60), updated: at(60), conversation_id: 'c-' + id, ask: a, open_asks: 1});
const items = [request('t-mine', 'Pick the venue', ask('m-mine', 'human:ana')),
  request('t-old', 'Pick the date', ask('m-old', 'human:ana', at(5))),
  request('t-ben', 'Approve the budget', ask('m-ben', 'human:ben'))];
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {hash: '#/tasks'});
    const seen = [];
    page.on('request', r => { if (r.method() === 'POST' && /\/seen$/.test(new URL(r.url()).pathname)) seen.push({p: new URL(r.url()).pathname, body: r.postDataJSON()}); });
    await page.evaluate(items => {
      needsTake({items: structuredClone(items), version: 'v1', count: items.length});
      needsTake({items: structuredClone(items), version: 'v1', count: items.length});
      const host = document.createElement('div');
      host.id = 'needs-you-seen';
      host.innerHTML = items.map(it => needsV2Item(it, false)).join('');
      document.querySelector('#main').prepend(host);
    }, items);
    await page.waitForTimeout(200);
    assert.deepEqual(seen, [{p: '/api/v2/needs-you/seen', body: {asks: ['m-mine']}}], 'only my undelivered question, once');

    await page.evaluate(() => { document.querySelector('#needs-you-seen details[data-v2-id="t-mine"]').open = true; });
    await page.waitForTimeout(200);
    assert.deepEqual(seen.slice(1), [{p: '/api/v2/tasks/t-mine/seen', body: {}}], 'opening my card reads its question');
    await page.evaluate(() => { document.querySelector('#needs-you-seen details[data-v2-id="t-ben"]').open = true; });
    await page.waitForTimeout(200);
    assert.equal(seen.length, 2, "opening a card that asks someone else reads nothing");
    assert.deepEqual(errors, []);
    console.log('Needs you: my questions are delivered once and read when I open their card');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
