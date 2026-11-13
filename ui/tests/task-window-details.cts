// The task window's Details line, run lines and needs-you banner (task #419, the rest of the task-window spec).
// - 1280 × 800: the right column ends in one line "Owner · Team · N tags ›" until opened; opened it holds Owner, Asked by,
//   Visible to, Followers, Due ("None" when unset), Tags and Part of; empty rows hide.
// - A bot's finished run is one chat line, "Builder worked 14 min", in order with the messages.
// - A task waiting on the reader's own answer shows a banner above the chat with Answer; answering removes it.
// Fixtures only (tasks-page.cts).
import type {Need, Task} from './tasks-page.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, task, brief} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;

const T0 = Date.now() - 3 * 3600e3;
const at = (min: number) => new Date(T0 + min * 60000).toISOString();
const say = (n: number, body: string) => ({id: `m${n}`, sender_id: 'human:ana', kind: 'say', body, refs: {}, created_at: at(n * 10)});
const RID = 'spec-ok-ana';

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // ---- Details and a run line, on a task nobody waits on the reader for
    {
      const t = task('t-1', 'Chat first in the task window', 'spec', {owner_id: 'bot:writer', labels: ['ui', 'chat']});
      const runs = {'t-1': [{run: 'a1', bot: 'bot:writer', started_at: at(15), finished_at: at(29), outcome: 'ok', ran_on: null}]};
      const {page, errors} = await open(browser, {viewport: {width: 1280, height: 800}, hash: '#/task/t-1', tasks: [t], needs: [],
        comments: {'t-1': [say(1, 'First line'), say(5, 'Last line')]}, runs});
      const win = page.locator('#task-modal[open]');
      await win.locator('.task-comments').getByText('Last line').waitFor();
      // The run is one line, between the messages it fell between.
      const marks = win.locator('.task-comments [data-run-line]');
      assert.equal(await marks.count(), 1);
      assert.match((await marks.innerText()).replace(/\s+/g, ' '), /^Writer worked 14 min/);
      const order = await win.locator('.task-comments [data-message], .task-comments [data-run-line]').evaluateAll(els => els.map(e => e.getAttribute('data-message') || 'run'));
      assert.deepEqual(order, ['m1', 'run', 'm5']);
      // Details is one line until opened.
      const details = win.locator('details.task-details');
      assert.equal(await details.evaluate(d => (d as HTMLDetailsElement).open), false);
      assert.match((await details.locator('> summary').innerText()).replace(/\s+/g, ' '), /^Details\s+Writer · Team · 2 tags/);
      const inColumn = await details.evaluate(d => { const r = d.getBoundingClientRect(), s = document.querySelector('.task-side')!.getBoundingClientRect(); return r.bottom <= s.bottom + 1 && r.height < 60; });
      assert.ok(inColumn, 'one line at the foot of the right column');
      assert.equal(await win.locator('[data-prop-row="owner"]').isVisible(), false, 'its rows wait');
      await details.locator('> summary').click();
      const rows = await win.locator('details.task-details [data-prop-row]').evaluateAll(els => els.map(e => e.getAttribute('data-prop-row')));
      assert.deepEqual(rows, ['owner', 'asker', 'visible', 'followers', 'due', 'tags'].filter(r => rows.includes(r)));
      for (const r of ['owner', 'asker', 'visible', 'due', 'tags']) assert.ok(rows.includes(r), `${r} row`);
      assert.equal(await win.locator('[data-prop-row="parent"]').count(), 0, 'no parent: the row hides');
      assert.match(await win.locator('[data-prop-row="due"]').innerText(), /None/);
      assert.ok(await win.locator('[data-prop-row="owner"]').isVisible());
      assert.deepEqual(errors, []);
      await page.close();
    }
    // ---- the banner
    {
      const t: Task = task('t-2', 'Signup plans', 'spec', {turn: {state: 'person', who: 'human:ana', line: 'Which plan do signups get?', since: null, until: null, ask: true, requirement_id: RID}});
      const need: Need = {kind: 'ask', id: `ask-b:${RID}`, task_id: 't-2', requirement_id: RID, line: 'Which plan do signups get?', since: at(1), task: brief(t),
        ask: {requirement_id: RID, kind: 'answer', line: 'Which plan do signups get?', who: 'human:ana', asked_by: 'bot:pm', choices: ['Pro trial', 'Free'],
          may: ['answer', 'thumbs'], met: false, shows: [], standing: 'waiting', answer: null}};
      const {page, errors} = await open(browser, {viewport: {width: 1280, height: 800}, hash: '#/task/t-2', tasks: [t], needs: [need]});
      const card = page.locator('#need-card[open]');
      await card.waitFor();
      await card.locator('[data-dc="task"]').click();
      const win = page.locator('#task-modal[open]');
      const banner = win.locator('.task-need');
      await banner.waitFor();
      assert.match(await banner.innerText(), /Which plan do signups get\?/);
      const above = await page.evaluate(() => document.querySelector('#task-modal .task-need')!.getBoundingClientRect().bottom <= document.querySelector('#task-modal .task-chat')!.getBoundingClientRect().top + 1);
      assert.ok(above, 'the banner is above the chat');
      await banner.getByRole('button', {name: 'Answer'}).click();
      await card.waitFor();
      await card.getByRole('button', {name: 'Pro trial'}).click();
      await win.locator('.task-need').waitFor({state: 'detached'});
      assert.equal(await win.locator('.task-need').count(), 0, 'answered: the banner goes');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // ---- an editable task without tags hides its Tags row; a request raised while the window is open shows the banner
    {
      const t: Task = task('t-3', 'Pricing page', 'spec', {owner_id: 'human:ana'});
      const {page, errors} = await open(browser, {viewport: {width: 1280, height: 800}, hash: '#/task/t-3', tasks: [t], needs: []});
      const win = page.locator('#task-modal[open]');
      await win.locator('details.task-details > summary').click();
      assert.ok(await win.locator('[data-prop-row="due"]').isVisible());
      assert.equal(await win.locator('[data-prop-row="tags"]').count(), 0, 'no tags: the row hides even when editable');
      assert.equal(await win.locator('.task-need').count(), 0);
      t.turn = {state: 'person', who: 'human:ana', line: 'Ship it?', since: null, until: null, ask: true, requirement_id: RID};
      t.version += 1;
      await page.evaluate(() => { void (window as unknown as {taskAskSettled: (id: string) => void}).taskAskSettled('t-3'); });
      await win.locator('.task-need').waitFor();
      assert.match(await win.locator('.task-need').innerText(), /Ship it\?/);
      assert.equal(await win.locator('.task-need').getByRole('button', {name: 'Answer'}).count(), 1);
      assert.equal(await win.locator('.task-need [data-task-answer]').evaluate(b => typeof (b as HTMLButtonElement).onclick), 'function', 'the refreshed Answer button is bound');
      assert.equal(await win.locator('[data-prop-row="add-tag"] [data-prop="tags"]').count(), 1, 'no tags: a button still adds the first tag');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('task window details: Details line, run lines, needs-you banner: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
