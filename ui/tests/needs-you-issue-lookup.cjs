// The issues inbox: a request card fetches its full request by number when opened. One whose number is not a positive
// whole number asks for nothing (cards without a number once asked for /api/issues/NaN).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {hash: '#/tasks'});
    const issueGets = [];
    page.on('request', r => { const m = new URL(r.url()).pathname.match(/^\/api\/issues\/([^/]+)$/); if (m) issueGets.push(m[1]); });
    // Every card opened, in the order its toggle event ran.
    await page.evaluate(() => {
      window.TOGGLED = [];
      document.addEventListener('toggle', ev => { if (ev.target.matches?.('details.req') && ev.target.open) TOGGLED.push('n:' + ev.target.dataset.n); }, true);
    });
    // Cards whose number is not a positive whole number, then a valid one. Toggle events run in order, so once the
    // valid card's fetch is seen every earlier card's toggle has run.
    const BAD = ['abc', '1.5', '0', '-3'];
    const inbox = page.waitForRequest(r => new URL(r.url()).pathname === '/api/issues/7');
    await page.evaluate(numbers => {
      const host = document.createElement('div');
      host.innerHTML = numbers.map(number => needsYouItem({number, title: 'Pick a plan', owner_id: 'human:ana', body: 'Monthly or annual?'}, false)).join('');
      document.querySelector('#main').append(host);
      host.querySelectorAll('details').forEach(d => { d.open = true; });
    }, [...BAD, 7]);
    await inbox;
    assert.deepEqual(await page.evaluate(() => TOGGLED), [...BAD.map(n => 'n:' + n), 'n:7'], 'every card opened, before the valid one');
    assert.deepEqual(issueGets, ['7'], 'only the valid card asks for its issue, by number');
    assert.deepEqual(errors, []);
    console.log('Issues inbox: a malformed request number asks for no issue; a valid card still fetches its own');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
