// A text note must not optimistically remove a still-open decision.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {hash: '#/tasks'});
    await page.route('**/api/v2/messages/text-question/answer', route => {
      const decided = route.request().postDataJSON().text === 'Publish';
      return route.fulfill({contentType: 'application/json', body: JSON.stringify(decided ? {answer: {}} : {
        decision_open: true, feedback: 'Note saved. Decision still open; choose one of the options.'})});
    });
    await page.evaluate(() => {
      v2Refresh = async () => {};
      const host = document.createElement('div');
      host.id = 'text-answer-test';
      host.innerHTML = looseAskRow({id: 'text-question', title: 'Publish the draft?', created: new Date().toISOString()});
      document.querySelector('#main').prepend(host);
      host.querySelector('details').open = true;
    });
    const card = page.locator('#text-answer-test details');
    await card.locator('textarea').fill('Maybe later');
    await card.locator('button[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#text-answer-test [data-ask-msg]')?.textContent.includes('still open'));
    assert.equal(await card.count(), 1);
    assert.equal(await card.locator('button[type=submit]').isEnabled(), true);
    await card.locator('textarea').fill('Publish');
    await card.locator('button[type=submit]').click();
    await page.waitForFunction(() => !document.querySelector('#text-answer-test details'));
    assert.deepEqual(errors, []);
    console.log('Text answer: unresolved note stays visible; settled answer removes the card');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
