import type * as Tasks from './tasks-page.cts';
// Offline browser regression: a person's task comment an org rule holds is not a saved comment. Nothing is written, the
// box keeps the text, no "Saved" line shows, and the task shows the held comment with its rule and Edit and resend.
// Every request is intercepted (tasks-page.cts).
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cts') as Tasks.Api;
const TEXT = 'Our favorite color is gold.';
const HOLD = {id: 'h-task', operation: 'comment', author: 'human:ana', author_name: 'Ana', state: 'held', outcome: 'blocked',
  rules: [{id: 'r-color', kind: 'internal', text: 'Acme does not name a favorite color.', version: 1, status: 'active',
           if_broken: 'block'}],
  text: TEXT, to: ['Ops'], reviewer: null, task_id: null, created_at: new Date().toISOString(), can_withdraw: true,
  can_send: false};

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined
    ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as import('playwright').LaunchOptions);
  try {
    const {page, errors} = await open(browser, {hash: '#/task/t-3', comments: {'t-3': [{id: 'm1', kind: 'say', sender_id: 'human:sam',
      body: 'Any Friday works.', created_at: new Date().toISOString(), refs: {task: 't-3', comment: true}}]}});
    // Later routes win: the comment POST is held, anything else falls through to the fixtures.
    await page.route('**/api/v2/tasks/*/comments', async route => {
      if (route.request().method() !== 'POST') return route.fallback();
      return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({held: HOLD})});
    });
    const win = page.locator('#task-modal');
    await win.locator('.task-comments .tcomment').first().waitFor();
    await win.locator('.task-chat textarea').fill(TEXT);
    await win.locator('.task-chat button[type=submit]').click();
    await win.locator('[data-rule-holds] [data-hold="h-task"]').waitFor();
    assert.equal(await win.locator('.task-chat textarea').inputValue(), TEXT, 'the held text stays in the box');
    assert.equal((await win.locator('[data-task-chat-status]').innerText()).includes('Saved'), false, 'no "Saved" line');
    assert.equal(await win.locator('[data-rule-holds]').isVisible(), true, 'the hold list shows the card');
    assert.match(await win.locator('[data-hold="h-task"] .rh-msg').innerText(), /favorite color is gold/);
    assert.equal(await win.locator('[data-hold="h-task"] [data-hold-edit]').count(), 1, 'Edit and resend is offered');
    assert.deepEqual(errors, [], 'no page errors');
    console.log('task comment held: ok');
    await page.context().close();
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
