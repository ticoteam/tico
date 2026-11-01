// The Needs you card: a bot's question that says what it will do if nobody answers shows it on the card, in plain words, with the
// day it goes ahead (backend/ask_deadlines.py), as a small line at the card's foot, never as the recommendation. A send
// waits for the answer, and the card says so.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const NOW = Date.now(), at = minutes => new Date(NOW - minutes * 60000).toISOString();
const question = (id, title, ifNoAnswer, goesAheadAt) => ({id, kind: 'question', title, body: '', owner: 'bot:writer',
  requester: 'bot:writer', status: 'waiting', waiting_on: 'human:ana', version: 2, created_at: at(400), updated_at: at(360), open_asks: 1,
  ask: {id: 'q-' + id, from_actor: 'bot:writer', to_actor: 'human:ana', body: title, created_at: at(300), goes_ahead_at: goesAheadAt,
    refs: {task: id, questions: [{id: 'decision', header: '', question: title, multi: false, other: true,
      options: [{label: 'Yes'}, {label: 'No'}]}], if_no_answer: ifNoAnswer}}});
const items = [
  question('t-archive', 'Archive the September drafts?', {do: 'Yes', kind: 'reversible'}, '2026-10-16T16:00:00.000000Z'),
  question('t-email', 'Email the venue to confirm?', {do: 'Yes', kind: 'send'}, null),
];
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, hash: '#/tasks'});
    await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items})}));
    await page.route('**/api/v2/tasks/*/comments', route => route.fulfill({contentType: 'application/json', body: '{"comments":[]}'}));
    await page.route('**/api/v2/tasks/*/files', route => route.fulfill({contentType: 'application/json', body: '{"files":[]}'}));
    await page.evaluate(async () => { await v2Refresh(); decideOpen('task:t-archive'); });
    const line = async id => {
      await page.locator(`.decide .dc-card[data-key="task:${id}"]`).waitFor();
      return page.locator('.decide .dc-card .qc-fallback').textContent();
    };
    const day = new Date('2026-10-16T16:00:00Z').toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'});
    assert.equal(await line('t-archive'), `If you don't answer by ${day}: Yes`);
    // At the bottom of the card's words, small, and no button: Do what you recommend is not offered on a question card.
    const foot = await page.evaluate(() => {
      const line = document.querySelector('.decide .qc-fallback'), body = line.parentElement;
      const after = [...body.children].slice([...body.children].indexOf(line) + 1).filter(x => !x.matches('.dc-details'));
      return {after: after.length, small: parseFloat(getComputedStyle(line).fontSize) < parseFloat(getComputedStyle(document.querySelector('.decide .dc-q')).fontSize),
        rec: !!document.querySelector('.decide .dc-rec'), button: line.closest('button') !== null};
    });
    assert.deepEqual(foot, {after: 0, small: true, rec: false, button: false});
    // A send never goes ahead on its own: the same card says the bot waits.
    const waits = await page.evaluate(it => qcardFallbackHTML(it), items[1]);
    assert.match(waits, />If you don&#39;t answer, Writer waits for you before it goes ahead \(Yes\)\.</);
    assert.deepEqual(errors, []);
    console.log('decide-default: ok');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
