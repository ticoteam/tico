import type * as Tasks from './tasks-page.cts';
import type {Page} from 'playwright';
// The task window and the list behind it (ui/app/task-docs.ts, task-modal.ts, tasks-page.ts, task-row.ts;
// backend/task_documents.py), on the v3 fixture (ui/tests/tasks-page.cts); every request is intercepted:
// - a task's Markdown file is a tab, as an HTML document is, and reads as Markdown in the app's own type;
// - beside the tabs, a long title keeps its two lines and 40% of the row, on a computer and a phone;
// - a task opened by its address has its own pipeline behind it, not the view last left there; the address stays the
//   task's, and closing the window writes the pipeline's;
// - a row's avatar is the task's owner, though the turn is someone else's.
// TICO_SHOTS=<dir> saves each state, desktop and phone, light and dark.
declare var TASKS_ST: Tasks.Globals['TASKS_ST'];
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const path: typeof import('node:path') = require('node:path');
const {open, task} = require('./tasks-page.cts') as Tasks.Api;

const shots = process.env.TICO_SHOTS || '';
const ago = (min: number) => new Date(Date.now() - min * 60000).toISOString();
const MD = 'file-md-how', TITLE = 'Write down how the UI works today, from the sidebar to the task window, for whoever starts on it next week';
const NOTES = '# How the UI works today\n\nThe sidebar lists **Tasks**, Flow and Calendar.\n\n- A task opens in its own window\n- Its documents are tabs\n';
const waiting = {state: 'person' as const, who: 'human:ana', line: 'Waiting on Ana', since: ago(60), until: null, ask: false, requirement_id: null};
const tasks = () => [
  // An Operations task, a bot's, its turn Ana's: the list's avatar is still the bot.
  task('t-1', TITLE, 'todo', {owner_id: 'bot:writer', turn: waiting, documents: [{name: 'How UI works today', file_id: MD, kind: 'markdown', version: 1,
    by: 'bot:writer', at: ago(20), versions: [{n: 1, by: 'bot:writer', at: ago(20), note: null}]}]}),
  task('t-2', 'Saved carts', 'spec', {owner_id: 'bot:pm'}),
];
const setup = async (page: Page) => {
  // The view last left: Product. The address names an Operations task.
  await page.addInitScript(() => { try { localStorage.setItem('tico.tasks.view', JSON.stringify({pipeline: 'product', layout: 'list'})); } catch {} });
  await page.route(url => url.pathname.startsWith('/api/v2/places/'), route => route.fulfill({contentType: 'application/json', body: JSON.stringify({can_link: true, pins: []})}));
  await page.route(url => url.pathname === `/api/v2/files/${MD}`, route => route.fulfill({contentType: 'text/markdown', body: NOTES}));
};
const shot = async (page: Page, name: string) => {
  if (!shots) return;
  await page.waitForTimeout(250);
  await page.screenshot({path: path.join(shots, name + '.png')});
};

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const [device, viewport] of [['desktop', {width: 1280, height: 800}], ['phone', {width: 390, height: 844}]] as const) {
      for (const theme of ['light', 'dark']) {
        const tag = `${device}, ${theme}`;
        const {page, errors} = await open(browser, {viewport, theme, hash: '#/task/t-1', tasks: tasks(), needs: [], beforeGoto: setup});
        const win = page.locator('#task-modal[open][data-task="t-1"]');
        await win.locator('.tw-tabs').waitFor();

        // Its own pipeline behind it; the address stays the task's.
        await page.waitForFunction(() => TASKS_ST && TASKS_ST.pipeline === 'ops' && !TASKS_ST.loading);
        assert.equal(await page.evaluate(() => location.hash), '#/task/t-1', `${tag}: the address stays the task's`);

        // The title beside the tabs: two lines, 40% of the row or more.
        const title = await win.locator('.tmodal-title').evaluate(el => {
          const cs = getComputedStyle(el), lines = (el.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom)) / parseFloat(cs.lineHeight);
          return {lines: Math.round(lines), width: el.getBoundingClientRect().width, row: el.parentElement!.getBoundingClientRect().width};
        });
        assert.equal(title.lines, 2, `${tag}: the title keeps two lines beside the tabs`);
        assert.ok(title.width >= title.row * 0.4 - 8, `${tag}: and 40% of the row (${title.width} of ${title.row})`);
        await shot(page, `title-tabs-${device}-${theme}`);

        // The Markdown file is a tab; it reads as Markdown, no page frame.
        await win.getByRole('tab', {name: 'How UI works today'}).click();
        const md = win.locator('.task-doc .doc-md');
        await md.locator('h1').waitFor();
        assert.equal(await md.locator('h1').innerText(), 'How the UI works today');
        assert.equal(await md.locator('strong').innerText(), 'Tasks');
        assert.equal(await md.locator('li').count(), 2);
        assert.equal(await win.locator('.task-doc iframe').count(), 0, `${tag}: no page frame for Markdown`);
        assert.match((await win.locator('.task-doc [data-doc-ver]').innerText()).trim(), /^v1 · Writer · 20m/);
        const ink = await md.evaluate(el => [getComputedStyle(el).color, getComputedStyle(document.body).color]);
        assert.equal(ink[0], ink[1], `${tag}: in the app's colours`);
        await shot(page, `markdown-tab-${device}-${theme}`);

        // Closed: the Operations list, its address written; the row's avatar is the owner, the turn Ana's.
        await win.locator('[data-modal-close]').click();
        await page.waitForFunction(() => /pipeline=ops/.test(location.hash));
        assert.equal((await page.locator('#task-scope').innerText()).trim(), 'Operations', `${tag}: Operations behind it`);
        if (device === 'desktop') {
          // A phone's board shows cards: who asked, and the owner's face at the right.
          const row = page.locator('#task-body .tk-row[data-task="t-1"]');
          await row.waitFor();
          assert.match(String(await row.getAttribute('data-tip')), /^Writer · /, `${tag}: the owner's avatar, not whose turn it is`);
        } else await page.locator('#task-body .tk-card[data-task="t-1"] .tk-owner[title="Owner: Writer"]').waitFor();
        await shot(page, `list-owner-${device}-${theme}`);
        assert.deepEqual(errors, []);
        await page.close();
      }
    }
    console.log('PASS: a Markdown file is a tab read as Markdown; the title keeps two lines beside the tabs; a task by its address has its pipeline behind it; a row shows its owner.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
