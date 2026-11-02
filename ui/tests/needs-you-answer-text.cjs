// A text answer must wait for settlement before the current Needs you card leaves.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {hash: '#/tasks'});
    const ask = {id: 'text-question', kind: 'ask', title: 'Publish the draft?', body: 'Publish the draft?',
      requester_id: 'bot:writer', created_at: new Date().toISOString(), need: 'decide'};
    let items = [ask], release, calls = 0;
    await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items})}));
    await page.route('**/api/v2/messages/text-question/answer', async route => {
      calls++;
      const decided = route.request().postDataJSON().text === 'Publish';
      if (!decided) await new Promise(resolve => { release = resolve; });
      else items = [];
      return route.fulfill({contentType: 'application/json', body: JSON.stringify(decided ? {answer: {}} : {
        decision_open: true, feedback: 'Note saved. Decision still open; choose one of the options.'})});
    });
    await page.evaluate(async () => { await v2Refresh(); decideOpen('ask:text-question'); });
    const card = page.locator('#need-card .dc-card[data-key="ask:text-question"]');
    await card.waitFor();
    await card.locator('textarea').fill('Maybe later');
    await page.locator('#need-card [data-dc="answer"]').click();
    await page.waitForFunction(() => document.querySelector('.decide[aria-busy="true"]'));
    assert.equal(await card.count(), 1, 'unconfirmed text must not hide the decision');
    assert.equal(await card.locator('textarea').isDisabled(), true);
    assert.equal(calls, 1);
    const outbox = await page.evaluate(() => JSON.parse(localStorage.getItem('tico.decide.outbox')));
    assert.equal(outbox.length, 1);
    assert.equal(outbox[0].waitSettlement, true);
    assert.ok(outbox[0].writes[0].key, 'pending text retains its durable request key');
    assert.ok(release, 'the held request reached the server');
    release();
    await card.locator('[role="status"]').waitFor();
    assert.match(await card.innerText(), /Decision still open/);
    assert.equal(await card.locator('textarea').isEnabled(), true);
    assert.equal(await card.locator('textarea').inputValue(), '');
    await card.locator('textarea').fill('Publish');
    await page.locator('#need-card [data-dc="answer"]').click();
    await page.waitForFunction(() => !document.querySelector('#need-card .dc-card[data-key="ask:text-question"]'));
    assert.equal(calls, 2);
    assert.deepEqual(errors, []);
    console.log('Text answer: pending and unresolved notes retain the current card; confirmed settlement removes it');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
