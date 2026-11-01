// Journey: Needs you, as Dana. A bot asks her a question and asks for an approval (both made through the API as the bot
// does). Needs you draws both; she answers the question and approves the approval in the page, and the hub records both.
// Runs on a phone and a desktop.
const assert = require('node:assert/strict');
const {journey, open, go, must, assertClean, shot, t, until} = require('./support/e2e.cjs');

const QUESTION = 'Ship the beta on Nov 3?';
const ANSWER = 'Yes, Nov 3.';

journey('e2e-needs-you', async ({hub, browser, phone}) => {
  const where = phone ? 'phone' : 'desktop';
  const asked = await must(hub, 'engineering-lead', 'POST', 'messages', {
    to: 'dana', text: QUESTION, kind: 'ask', needs_action: true,
  }, 'the bot asks a question');
  const questionId = asked.id;
  const approval = await must(hub, 'builder-claude', 'POST', 'approvals', {
    kind: 'spend_limit', payload: {bot: 'builder-claude', daily_usd: 40},
  }, 'the bot asks for an approval');
  const approvalId = approval.id || approval.approval?.id;
  assert.ok(questionId && approvalId, `${where}: both are recorded`);

  const page = await open(browser, hub, 'dana', {phone, hash: '#/tasks?view=foryou'});
  const card = page.locator('[data-need-card]');
  await card.first().waitFor({timeout: t(20000)});
  assertClean(page, `${where}: needs you drawn`);

  // The question: answer it on its card.
  await page.locator(`[data-need-card="ask:${questionId}"]`).first().click();
  await page.locator('#need-card', {hasText: QUESTION}).first().waitFor({timeout: t(20000)});
  await page.locator('#need-card .dc-note textarea[aria-label="Your answer"]').fill(ANSWER);
  await page.locator('#need-card [data-dc="answer"]').first().click();
  // The card sends the answer when its undo toast runs out, so the hub is read until the answer is there.
  const reply = await until(async () => {
    const thread = await must(hub, 'dana', 'GET', `conversations/${asked.conversation_id}/messages`, undefined, 'read the thread');
    return (thread.messages || []).find(m => m.kind === 'answer' && m.in_reply_to === questionId);
  }, `${where}: the answer on the hub`);
  assert.equal(reply.body, ANSWER, `${where}: the hub has the answer to the question`);
  assertClean(page, `${where}: answered`);

  // The approval: approve it on its card.
  go(page, '#/tasks?view=foryou');
  await page.locator(`[data-need-card="approval:${approvalId}"]`).first().click();
  await page.locator('#need-card [data-dc="yes"]').first().click();
  await until(async () => {
    const decided = await must(hub, 'dana', 'GET', `approvals/${approvalId}`, undefined, 'read the approval');
    return decided.decision === 'approved';
  }, `${where}: the approval approved on the hub`);
  assertClean(page, `${where}: approved`);
  await shot(page, `e2e-needs-you-${where}`);
});
