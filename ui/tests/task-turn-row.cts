// The task window's "whose turn it is" row at the end of the chat (task-modal.ts taskStateHTML), in the turn's own words
// with no "Turn:" label, on the v3 fixture (ui/tests/tasks-page.cts); every request is intercepted.
// - A bot's or a person's turn leads with who it is; a bot at work says "working".
// - Someone else's ask says what is owed: "Sam to answer", then the question.
// - A task it waits on is named by whoever has that task (a subtask's owner); one the reader cannot see leads with
//   "Waiting on a task", never its id.
// - GitHub's turn is its own words ("Checks running"), with no raw source name; a turn with no words shows no row.
import type * as Tasks from './tasks-page.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, task} = require('./tasks-page.cts') as Tasks.Api;

const HIDDEN = '9b1c2f4e-5d6a-4b7c-8e9f-0a1b2c3d4e5f';
const turn = (state: string, who: string | null, line: string, over: Record<string, unknown> = {}) =>
  ({state, who, line, requirement_id: null, since: null, until: null, ask: false, ...over});
// [task id, its turn, the row's words (face text left out), or null for no row]
const CASES: [string, ReturnType<typeof turn>, string | null][] = [
  ['t-bot', turn('bot', 'bot:pm', 'Say when the work is done'), 'PM Say when the work is done'],
  ['t-run', turn('bot', 'bot:pm', 'Draft the launch plan', {running: true}), 'PM working Draft the launch plan'],
  ['t-ask', turn('person', 'human:sam', 'Ship it on Friday?', {ask: true, requirement_id: 'r-9'}), 'Sam to answer Ship it on Friday?'],
  ['t-sub', turn('tasks', 't-kid', 'Write the launch copy'), 'Writer Write the launch copy'],
  ['t-hid', turn('tasks', HIDDEN, 'A sub-task is not finished'), 'Waiting on a task A sub-task is not finished'],
  ['t-gh', turn('source', 'github', 'Checks running'), 'Checks running'],
  ['t-none', turn('room', 'stage-x', ''), null],
];

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const width of [1440, 390]) {
      const tasks = CASES.map(([id, tn]) => task(id, `Task ${id}`, 'spec', {turn: tn as never}));
      const {page, errors} = await open(browser, {viewport: {width, height: 844}, hash: '#/tasks', tasks, needs: [],
        // The subtask the parent waits on comes with the parent's read, as GET /v2/tasks/{id} sends `children`.
        beforeGoto: async p => {
          await p.route('**/api/v2/tasks/t-sub', route => route.fulfill({json: {task: tasks.find(t => t.id === 't-sub'),
            children: [{id: 't-kid', title: 'Write the launch copy', status: 'open', owner_id: 'bot:writer', children: []}], comments: [], events: [], runs: [], messages: []}}));
        }});
      try {
        for (const [id, , want] of CASES) {
          await page.evaluate(h => { location.hash = h; }, '#/task/' + id);
          const win = page.locator(`#task-modal[open][data-task="${id}"]`);
          await win.locator('.task-chat .task-comments').waitFor({state: 'attached'});
          const row = win.locator('.task-comments .th-status-turn');
          if (want === null) { assert.equal(await row.count(), 0, `${id}: no row when the turn has no words`); continue; }
          await row.waitFor();
          const said = (await row.locator('.th-main').innerText()).replace(/\s+/g, ' ').trim();
          assert.equal(said, want, `${id} at ${width}`);
          assert.ok(!said.includes(HIDDEN) && !/\bgithub\b/.test(said) && !/^Turn:/.test(said), `${id}: no raw id, source or label`);
          // A row about someone has their face; one about nobody has none.
          assert.equal(await row.locator('.th-av').count(), /^(Waiting on a task|Checks running)/.test(want) ? 0 : 1, `${id}: face`);
        }
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }
    console.log('task turn row: who, what is owed, tasks waited on, GitHub, no empty row: ok');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
