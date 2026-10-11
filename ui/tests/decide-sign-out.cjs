// Decide keeps actions not yet confirmed in this browser so they go out on the next load. Signing out leaves none of
// them behind, a person's note included: what still waits goes out as on leaving the page, then the saved copy goes.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const KEY = 'tico.decide.outbox';
const saved = JSON.stringify([{me: 'ana', key: 'task:t-q1', writes: [
  {path: '/v2/tasks/t-q1/comments', body: {text: 'Private note: only after the board call'}, key: 'dc-1'}]}]);
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {hash: '#/tasks', local: {[KEY]: saved}});
    const sent = [];
    await page.route('**/api/v2/tasks/t-q1/comments', route => { sent.push(route.request().postDataJSON()); });  // never confirmed
    for (let i = 0; i < 300 && !sent.length; i++) await page.waitForTimeout(20);     // the next load sends it again
    assert.equal(sent.length, 1, 'a saved action goes out on the next load');
    assert.equal(await page.evaluate(k => localStorage.getItem(k), KEY), saved, 'kept until confirmed or sign-out');
    // The click as a person makes it, then the page leaving (pagehide); the navigation itself is not followed.
    await page.evaluate(() => {
      document.querySelector('#sign-out').hidden = false;
      window.addEventListener('click', e => { if (e.target.closest('#sign-out')) e.preventDefault(); });
    });
    await page.evaluate(() => { document.querySelector('#sign-out').click(); window.dispatchEvent(new Event('pagehide')); });
    for (let i = 0; i < 100 && sent.length < 2; i++) await page.waitForTimeout(20);
    assert.equal(await page.evaluate(k => localStorage.getItem(k), KEY), null, 'nothing waits in this browser after sign-out');
    assert.ok(sent.length >= 2 && sent.every(b => b.text === 'Private note: only after the board call'), 'it still went out');
    assert.deepEqual(errors, []);
    console.log('Decide: unsent actions go out at sign-out and none stay in this browser');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
