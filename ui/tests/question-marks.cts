// A bot's ❓ on its own chat message (backend/question_marks.py), in Flow and in its chat:
// - Tasks › You lists it under Answer like any ask: its task's title, its question under it, the bot beside it; in a
//   direct chat with the bot (no task) the bot's name is the title;
// - it is never an OK (silent break 5): its Flow item IS its chat, the marked message in view with a plain muted "?",
//   the composer under it, no Open, Approve or Send back; Snooze and ⋯ stay; the reply goes to that chat and Flow moves on;
// - on a phone it opens on the Chat tab; a direct chat is its chat alone, no tabs.
// Fixtures only (tasks-page.cts).
import type {Page} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, fixtures, needsOf, brief} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;
declare function decideKind(it: unknown): string;

const LINE = 'Should the launch note go out on Friday?', DM_LINE = 'Can I send the beta invite today?';
const ago = (m: number) => new Date(Date.now() - m * 60000).toISOString();

function world() {
  const tasks = fixtures(), t4 = brief(tasks.find(t => t.id === 't-4')!);
  const mark = {kind: 'question', id: 'qmark:q1', mark_id: 'q1', task_id: 't-4', message_id: 'm-q1', conversation_id: 'c-t-4',
    root_id: 'c-t-4', from_id: 'bot:eng', requirement_id: null, line: LINE, since: ago(30), task: t4};
  const dm = {kind: 'question', id: 'qmark:q2', mark_id: 'q2', task_id: null, message_id: 'm-q2', conversation_id: 'c-dm',
    root_id: 'c-dm', from_id: 'bot:eng', requirement_id: null, line: DM_LINE, since: ago(20), task: null};
  const needs = [...needsOf(tasks).filter(n => n.kind !== 'task'), mark, dm] as ReturnType<typeof needsOf>;
  const say = (id: string, body: string, m: number, refs: Record<string, unknown>) => ({id, kind: 'say', sender_id: 'bot:eng', body, created_at: ago(m), refs});
  const comments = {
    't-4': [say('m-q1', LINE, 30, {task: 't-4', comment: true, question: {id: 'q1', to: 'human:ana'}})],
    'c-dm': [say('m-q2', DM_LINE, 20, {question: {id: 'q2', to: 'human:ana'}})],
  };
  return {tasks, needs, mark, comments};
}
const groupOf = (page: Page, id: string) => page.locator(`[data-need="${id}"]`)
  .evaluate(el => el.closest('.tk-section')?.querySelector('.tk-group')?.firstChild?.textContent?.trim());

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const viewport of [{width: 1440, height: 900}, {width: 390, height: 844}]) {
      const phone = viewport.width < 640;
      const {tasks, needs, mark, comments} = world();
      const {page, errors, posts} = await open(browser, {viewport, tasks, needs, comments});
      try {
        const row = page.locator('[data-need="qmark:q1"]');
        await row.waitFor();
        assert.equal(await row.locator('.tk-title').innerText(), tasks.find(t => t.id === 't-4')!.title, 'its task heads it');
        assert.equal(await row.locator('.tk-ask').innerText(), LINE, 'its question under the title');
        assert.ok((await row.locator('.tk-who').innerHTML()).length > 0, 'the bot beside it');
        assert.equal(await groupOf(page, 'qmark:q1'), 'Answer');
        assert.equal(await groupOf(page, 'r1'), 'Review', 'the OK beside it is still an OK');
        const dmRow = page.locator('[data-need="qmark:q2"]');
        assert.equal(await dmRow.locator('.tk-title').innerText(), 'Engineer', "a direct chat's ❓ is headed by the bot");
        assert.equal(await dmRow.locator('.tk-ask').innerText(), DM_LINE);
        assert.equal(await page.evaluate(it => decideKind(it), mark), 'mention', 'a ❓ is answered, never approved');

        // Its Flow item is its chat (silent break 5: no Approve or Send back), opened from the row.
        await row.click();
        const side = page.locator('#need-card[open] .dc-side');
        const q = side.locator('.dc-chat [data-message="m-q1"] .qmark');
        await q.waitFor();
        assert.equal(await q.innerText(), '?', 'a plain mark, no emoji');
        assert.equal(await q.getAttribute('title'), 'Needs your reply');
        assert.ok(await q.isVisible(), 'the marked message is in view');
        assert.equal(await page.locator('#need-card[open] :is([data-dc="open"], [data-dc="approve"], [data-dc="sendback"])').count(), 0, 'no Open, Approve or Send back');
        const card = page.locator('#need-card[open] .decide');
        assert.equal(await card.locator('[data-dc="snooze"]').count(), 1, 'Snooze, keyed on the mark');
        assert.equal(await card.locator('[data-dc="ask-more"]').count(), 1, '⋯ with Give to…');
        assert.equal(await side.locator('.dc-chat .p-foot').count(), 0, 'no "To <name>" line');
        assert.equal(await side.locator('.dc-chat .p-text').getAttribute('placeholder'), 'Message Engineer', "worded as the task's window");
        assert.equal(await side.locator('.th-focus').evaluate(el => getComputedStyle(el).boxShadow), 'none', 'a faint tint, no bar');
        // Snooze and ⋯: on the header row on a wide screen (the composer ends the column), on the title row on a phone.
        assert.equal(await page.locator(phone ? '#need-card .dc-tabs ~ [data-key] [data-dc="snooze"]' : '#need-card .dc-head .dc-head-acts [data-dc="snooze"]').count(), 1);
        if (!phone) {
          await card.locator('[data-dc="snooze"]').click();
          const menu = await card.locator('.dc-head-acts .dc-snz').boundingBox();
          assert.ok(menu && menu.y > 40 && menu.y + menu.height <= viewport.height, `its menu opens down, on screen (${JSON.stringify(menu)})`);
          await card.locator('[data-dc="snooze"]').click();
        }
        if (phone) {
          assert.equal(await side.locator('.dc-tabs [role="tab"]').count(), 0, 'a task with no documents: its chat alone, no tabs');
          const box = await side.locator('.dc-chat .thv-composer').boundingBox();
          assert.ok(box && box.width >= viewport.width - 40, `the composer spans the phone (${box?.width}px)`);
        }
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth) <= 1, `nothing scrolls sideways at ${viewport.width}px`);

        // A reply from its composer goes to that chat, and Flow moves on.
        const text = side.locator('.dc-chat .thv-composer textarea');
        await text.fill('Yes, Friday.');
        await side.locator('.dc-chat .thv-composer .p-send').click();
        await page.waitForFunction(() => !document.querySelector('#need-card[open] .dc-side [data-message="m-q1"]'));
        assert.deepEqual(posts.filter(x => x.p.startsWith('/api/v2/threads/')), [{p: '/api/v2/threads/c-t-4/messages', body: {text: 'Yes, Friday.'}}]);
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // A ❓ in a direct chat: the chat alone, headed by the bot, no tabs.
    {
      const {tasks, needs, comments} = world();
      const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, tasks, needs, comments});
      try {
        await page.locator('[data-need="qmark:q2"]').click();
        const side = page.locator('#need-card[open] .dc-side');
        await side.locator('.dc-chat [data-message="m-q2"] .qmark').waitFor();
        assert.equal(await side.locator('.dc-tabs-t').innerText(), 'Engineer');
        assert.equal(await side.locator('.dc-tabs [role="tab"]').count(), 0, 'its chat is all there is: no tabs');
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }
    // Someone else's ❓ (Sam's): the mark in the timestamp's muted tone. Opening the task from a link lands on the
    // window itself, with no focus ring on any control.
    {
      const {tasks, comments} = world();
      (comments['t-4'][0]!.refs as Record<string, unknown>)['question'] = {id: 'q1', to: 'human:sam'};
      const {page, errors} = await open(browser, {tasks, needs: [], comments, hash: '#/task/t-4'});
      try {
        const q = page.locator('#task-modal[open] [data-message="m-q1"] .qmark');
        await q.waitFor();
        assert.equal(await q.getAttribute('title'), "Needs Sam's reply");
        const tone = await q.evaluate(el => [getComputedStyle(el).color, getComputedStyle(el.parentElement!.querySelector('time')!).color]);
        assert.equal(tone[0], tone[1], 'the same tone as the time');
        await page.waitForFunction(() => document.activeElement?.matches('#task-modal[open]'));
        assert.equal(await page.locator('#task-modal[open]').evaluate(el => getComputedStyle(el).outlineStyle), 'none', 'no ring on opening');
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }
    console.log('question marks: an Answer row like any ask; its Flow item is its chat, never an OK; a reply moves on: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
