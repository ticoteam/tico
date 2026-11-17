// A bot's ❓ on its own chat message (backend/question_marks.py), in Flow and in the task's chat:
// - Tasks › You lists it under Answer by its question alone, the bot beside it, next to an OK under Review;
// - it is never an OK: the ask screen has no Approve or Send back for it (silent break 5), only Open, Snooze and ⋯;
// - its row opens the task's chat at the marked message, which carries the small ❓ and nothing else.
// Fixtures only (tasks-page.cts).
import type {Page} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, fixtures, needsOf, brief} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;
declare function decideOpen(id?: string): boolean;
declare function decideKind(it: unknown): string;

const LINE = 'Should the launch note go out on Friday?';

function world() {
  const tasks = fixtures(), t4 = brief(tasks.find(t => t.id === 't-4')!);
  const mark = {kind: 'question', id: 'qmark:q1', mark_id: 'q1', task_id: 't-4', message_id: 'm-q1', conversation_id: 'c-t4',
    root_id: 'c-t4', from_id: 'bot:eng', requirement_id: null, line: LINE, since: new Date(Date.now() - 30 * 60000).toISOString(), task: t4};
  const needs = [...needsOf(tasks).filter(n => n.kind !== 'task'), mark] as ReturnType<typeof needsOf>;
  const comments = {'t-4': [{id: 'm-q1', kind: 'say', sender_id: 'bot:eng', body: LINE, created_at: mark.since,
    refs: {task: 't-4', comment: true, question: {id: 'q1', to: 'human:ana'}}}]};
  return {tasks, needs, mark, comments};
}
const groupOf = (page: Page, id: string) => page.locator(`[data-need="${id}"]`)
  .evaluate(el => el.closest('.tk-section')?.querySelector('.tk-group')?.firstChild?.textContent?.trim());

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const viewport of [{width: 1440, height: 900}, {width: 390, height: 844}]) {
      const {tasks, needs, mark, comments} = world();
      const {page, errors} = await open(browser, {viewport, tasks, needs, comments});
      try {
        const row = page.locator('[data-need="qmark:q1"]');
        await row.waitFor();
        assert.equal(await row.locator('.tk-title').innerText(), LINE, 'its question is the title');
        assert.equal(await row.locator('.tk-ask').count(), 0, 'one line');
        assert.ok((await row.locator('.tk-who').innerHTML()).length > 0, 'the bot beside it');
        assert.equal(await groupOf(page, 'qmark:q1'), 'Answer');
        assert.equal(await groupOf(page, 'r1'), 'Review', 'the OK beside it is still an OK');
        assert.equal(await page.evaluate(it => decideKind(it), mark), 'mention', 'a ❓ is answered, never approved');

        // Silent break 5: the ask screen on it has no Approve or Send back.
        await page.evaluate(() => { decideOpen('qmark:q1'); });
        const card = page.locator('#need-card[open]');
        await card.locator('[data-dc="open"]').waitFor();
        assert.equal(await card.locator('.dc-q').innerText(), LINE);
        assert.equal(await card.locator('.dc-thing .dc-quote').count(), 0, 'the question once, on the right');
        assert.equal(await card.locator('[data-dc="approve"], [data-dc="sendback"]').count(), 0, 'no Approve or Send back');
        assert.equal(await card.locator('[data-dc="snooze"]').count(), 1, 'Snooze, keyed on the mark');
        await card.locator('[data-dc="open"]').click();

        // Open: the task's chat, at the marked message, with its ❓.
        const q = page.locator('#task-modal[open] [data-message="m-q1"] .qmark');
        await q.waitFor();
        assert.equal(await q.getAttribute('aria-label'), 'Needs your reply');
        assert.ok(await q.isVisible(), 'the marked message is in view');
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth) <= 1, `nothing scrolls sideways at ${viewport.width}px`);
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // The row itself opens the chat (no ask screen first).
    {
      const {tasks, needs, comments} = world();
      const {page, errors} = await open(browser, {tasks, needs, comments});
      try {
        await page.locator('[data-need="qmark:q1"]').click();
        await page.locator('#task-modal[open] [data-message="m-q1"] .qmark').waitFor();
        assert.equal(await page.locator('#need-card[open]').count(), 0);
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }
    console.log('question marks: an Answer row by its question, never an OK, opens the chat at the ❓: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
