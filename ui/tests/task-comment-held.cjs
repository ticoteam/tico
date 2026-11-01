// Offline browser regression: a person's task comment an org rule holds is not a saved comment. Nothing is written, the
// box keeps the text, no "Saved" line shows, and the task shows the held comment with its rule and Edit and resend.
// Every request is intercepted (tasks-page.cjs).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const TEXT = 'Our favorite color is gold.';
const HOLD = {id: 'h-task', operation: 'comment', author: 'human:ana', author_name: 'Ana', state: 'held', outcome: 'blocked',
  rules: [{id: 'r-color', kind: 'internal', text: 'Acme does not name a favorite color.', version: 1, status: 'active',
           if_broken: 'block'}],
  text: TEXT, to: ['Ops'], reviewer: null, task_id: null, created: new Date().toISOString(), can_withdraw: true,
  can_send: false};

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined
    ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const {page, errors} = await open(browser);
    // Later routes win: the comment POST is held, anything else falls through to the fixtures.
    await page.route('**/api/v2/tasks/*/comments', async route => {
      if (route.request().method() !== 'POST') return route.fallback();
      return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({held: HOLD})});
    });
    const peek = page.locator('#task-peek');
    await page.locator('[data-task-key="tt-inbox"] .tl-title').click();
    await peek.locator('.task-comments .tcomment').first().waitFor();
    await peek.locator('.task-chat textarea').fill(TEXT);
    await peek.locator('.task-chat button[type=submit]').click();
    await peek.locator('[data-rule-holds] [data-hold="h-task"]').waitFor();
    assert.equal(await peek.locator('.task-chat textarea').inputValue(), TEXT, 'the held text stays in the box');
    assert.equal((await peek.locator('[data-task-chat-status]').innerText()).includes('Saved'), false, 'no "Saved" line');
    assert.equal(await peek.locator('[data-rule-holds]').isVisible(), true, 'the hold list shows the card');
    assert.match(await peek.locator('[data-hold="h-task"] .rh-msg').innerText(), /favorite color is gold/);
    assert.equal(await peek.locator('[data-hold="h-task"] [data-hold-edit]').count(), 1, 'Edit and resend is offered');
    assert.deepEqual(errors, [], 'no page errors');
    console.log('task comment held: ok');
    await page.context().close();
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
