// The task window's Details, run lines and the reader's own ask (the style guide's Task window).
// - 1280 × 800: Details is the icon before ⋯; its facts are one line each: Stage, Owner, Asked by, Visible to, Followers, Tags; a
//   Human or a due date not set waits hidden, and ⋯ shows it and opens its editor. No "Details" fold.
// - A bot's finished run is one chat line, "Builder worked 14 min", in order with the messages.
// - A task waiting on the reader's own answer opens on Chat, whose last row is that ask with Answer (the Chat tab has a
//   no tabs: it has no documents); answering removes it.
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
        comments: {'t-1': [{...say(0, 'Draft the chat-first window.'), refs: {task: 't-1', request: true}}, say(1, 'First line'), say(5, 'Last line')]}, runs});
      const win = page.locator('#task-modal[open]');
      await win.locator('.task-comments').getByText('Last line').waitFor();
      // The run is one line, between the messages it fell between.
      const marks = win.locator('.task-comments [data-run-line]');
      assert.equal(await marks.count(), 1);
      assert.match((await marks.innerText()).replace(/\s+/g, ' '), /^Writer worked 14 min/);
      const order = await win.locator('.task-comments [data-message], .task-comments [data-run-line]').evaluateAll(els => els.map(e => e.getAttribute('data-message') || 'run'));
      // A task is a title and a chat: what it asks is the chat's first message, a message like any other; then the
      // story in order.
      assert.deepEqual(order, ['m0', 'm1', 'run', 'm5']);
      const request = win.locator('.task-comments [data-message="m0"]');
      assert.match(await request.locator('.th-body').innerText(), /Draft the chat-first window/);
      assert.equal(await request.locator('.th-head time').count(), 1, 'when it was asked');
      // The facts are under the Details icon, one line each; no fold.
      assert.equal(await win.locator('details.task-details').count(), 0, 'no Details fold');
      assert.equal(await win.locator('[data-prop-row="owner"]').isVisible(), false, 'a tab away');
      await win.getByRole('button', {name: /^Details/}).click();
      await win.locator('[data-prop-row="owner"]').waitFor({state: 'visible'});
      assert.equal(await win.locator('#task-pane-details').getByText(/Draft the chat-first window|No details/).count(), 0, 'no description in Details');
      const rows = await win.locator('[data-task-props] [data-prop-row]:not([hidden])').evaluateAll(els => els.map(e => e.getAttribute('data-prop-row')));
      assert.deepEqual(rows, ['stage', 'owner', 'asker', 'visible', 'followers', 'tags'].filter(r => rows.includes(r)), `in order: ${rows}`);
      for (const r of ['stage', 'owner', 'asker', 'visible', 'tags']) assert.ok(rows.includes(r), `${r} row`);
      assert.equal(await win.locator('[data-prop-row="parent"]').count(), 0, 'no parent: the row hides');
      // A Human and a due date not set wait hidden; ⋯ › Set a due date… shows the row and opens its editor.
      for (const r of ['human', 'due']) assert.equal(await win.locator(`[data-prop-row="${r}"]`).isVisible(), false, `no ${r}: hidden`);
      await win.locator('[data-task-more]').click();
      await page.locator('.prop-pop:popover-open').getByRole('menuitem', {name: 'Set the Human…'}).click();
      await win.locator('[data-prop-row="human"]').waitFor({state: 'visible'});
      await page.locator('.prop-pop:popover-open').getByText('Human').first().waitFor();
      await page.keyboard.press('Escape');
      assert.ok(await win.locator('[data-prop-row="owner"]').isVisible());
      assert.deepEqual(errors, []);
      await page.close();
    }
    // ---- the banner
    {
      // The ask is one of the stage's requirements, and says who asked (PM): the row names them.
      const req = {requirement_id: RID, kind: 'ok', line: 'OK the signup plans?', who: 'human:ana', asked_by: 'bot:pm',
        may: ['ok', 'no'], met: false, shows: [], standing: 'waiting', answer: null};
      const t: Task = task('t-2', 'Signup plans', 'spec', {to_leave: [req], turn: {state: 'person', who: 'human:ana', line: 'OK the signup plans?', since: null, until: null, ask: true, requirement_id: RID}});
      const need: Need = {kind: 'ask', id: `ask-b:${RID}`, task_id: 't-2', requirement_id: RID, line: 'OK the signup plans?', since: at(1), task: brief(t),
        ask: {requirement_id: RID, kind: 'ok', line: 'OK the signup plans?', who: 'human:ana', asked_by: 'bot:pm',
          may: ['ok', 'no'], met: false, shows: [], standing: 'waiting', answer: null}};
      const {page, errors} = await open(browser, {viewport: {width: 1280, height: 800}, hash: '#/task/t-2', tasks: [t], needs: [need]});
      const card = page.locator('#need-card[open]');
      await card.waitFor();
      await card.locator('[data-dc="task"]').click();
      const win = page.locator('#task-modal[open]');
      const banner = win.locator('.task-need');
      await banner.waitFor();
      assert.match(await banner.innerText(), /OK the signup plans\?/);
      const head = (await banner.locator('.th-head').innerText()).replace(/\s+/g, ' ').trim();
      assert.match(head, /^PM waiting on you$/, `the asker, then "waiting on you": ${head}`);
      assert.equal(await banner.locator('.th-av').count(), 1, 'the asker\'s face');
      assert.doesNotMatch(await banner.innerText(), /You waiting on you/);
      // The ask is the chat's last row, and the window opens on the chat; a task with no documents has Chat and Files, no document tab.
      assert.equal(await win.locator('.task-chat').isVisible(), true);
      assert.deepEqual(await win.locator('.tw-tabs [data-task-tab]').evaluateAll(els => els.map(e => (e as HTMLElement).dataset.taskTab)), ['chat', 'files'], 'Chat and Files, no document');
      const last = await page.evaluate(() => { const rows = [...document.querySelectorAll('#task-modal .task-comments > *')]; return rows.at(-1)?.matches('.task-need'); });
      assert.ok(last, 'the ask is the last row of the chat');
      await banner.getByRole('button', {name: 'Answer'}).click();
      await card.waitFor();
      await card.locator('[data-dc="approve"]:not([disabled])').click();
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
      await win.getByRole('button', {name: /^Details/}).click();
      assert.equal(await win.locator('[data-prop-row="due"]').isVisible(), false, 'no due date: the row waits hidden');
      assert.equal(await win.locator('[data-prop-row="tags"]').isVisible(), false, 'no tags: the row hides even when editable');
      await win.getByRole('button', {name: /^Details/}).click();   // pressed again: back to the chat
      assert.equal(await win.locator('.task-need').count(), 0);
      t.turn = {state: 'person', who: 'human:ana', line: 'Ship it?', since: null, until: null, ask: true, requirement_id: RID};
      t.version += 1;
      await page.evaluate(() => { void (window as unknown as {taskAskSettled: (id: string) => void}).taskAskSettled('t-3'); });
      await win.locator('.task-need').waitFor();
      assert.match(await win.locator('.task-need').innerText(), /Ship it\?/);
      // No ask on record says who asked: the row leads with "Waiting on you", no name, no face.
      assert.match((await win.locator('.task-need .th-head').innerText()).trim(), /^Waiting on you$/);
      assert.equal(await win.locator('.task-need .th-av').count(), 0);
      assert.equal(await win.locator('.task-need').getByRole('button', {name: 'Answer'}).count(), 1);
      // ⋯ › Add a file… opens Files and the file picker.
      assert.equal(await win.locator('.task-side [data-task-files]').count(), 0);
      const chooser = page.waitForEvent('filechooser');
      await win.locator('[data-task-more]').click();
      await page.locator('.prop-pop:popover-open').getByRole('menuitem', {name: 'Add a file…'}).click();
      await chooser;
      assert.equal(await win.getByRole('tab', {name: 'Files', exact: true}).getAttribute('aria-selected'), 'true');
      // No tags: ⋯ › Add a tag… shows the row and opens its menu.
      await win.locator('[data-task-more]').click();
      await page.locator('.prop-pop:popover-open').getByRole('menuitem', {name: 'Add a tag…'}).click();
      await win.locator('[data-prop-row="tags"]').waitFor({state: 'visible'});
      await page.locator('.prop-pop:popover-open').getByRole('textbox', {name: 'Tag'}).waitFor();
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('task window details: Details tab, run lines, your ask at the end of the chat: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
