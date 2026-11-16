// A task that waits on the person's own answer opens on its ask (the ask screen, ui/app/decide.ts), from wherever it is
// opened; the ask screen links to the task's own page; and a note can carry screenshots and files.
// - opened by a #/task/ link, a list row's open (taskModalOpen) or a link in chat: the ask screen on that ask, not the task page;
// - a task waiting on someone else opens the task page as before; one whose first wait is someone else's but which also
//   waits on the person's own ask opens on that ask;
// - "#N ↗" on the ask opens the task page, which stays open; closing it leaves no ask screen behind;
// - the task's chat is a 420 px column; a file picked in its box goes up to the task before the arrow sends it back,
//   and is named at the end of the words; one that fails is named and nothing is sent;
// - on a 390 px phone the box and the buttons are on screen and nothing scrolls sideways.
// Fixtures only (tasks-page.cts).
import type {Page} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, fixtures, needsOf} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;
// The page's own opener (ui/app/task-modal.ts), as list rows and board cards call it.
declare function taskModalOpen(id: string, d?: unknown, opts?: {thread?: boolean; ask?: boolean; direct?: boolean}): Promise<void>;

const card = (page: Page) => page.locator('#need-card[open]');
const taskPage = (page: Page) => page.locator('#task-modal[open] .tmodal-title');

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // Opened by its address (a link, a notification): the ask screen on t-1's OK, with the same top bar as from Needs you.
    {
      const {page, errors} = await open(browser, {hash: '#/task/t-1'});
      try {
        await card(page).waitFor({timeout: 5000});
        assert.equal(await card(page).locator('.dc-q').innerText(), 'Approve the spec for Checkout flow');
        assert.equal(await card(page).locator('.dc-crumb').textContent(), 'All mine', 'walks the whole of Needs you, as from Needs you');
        assert.equal(await page.locator('#task-modal[open]').count(), 0, 'the task page does not open under it');
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // A list row or a board card opens a task with taskModalOpen (tasks-page.ts); a chat link with taskModalShow.
    {
      const {page, errors} = await open(browser);
      try {
        await page.evaluate(() => { void taskModalOpen('t-2'); });
        await card(page).waitFor({timeout: 5000});
        assert.equal(await card(page).locator('.dc-q').innerText(), 'Which plan do new signups get?');
        await card(page).locator('[data-dc="close"]').click();
        // A pipeline board's card on a phone opens at the conversation asking its owner (tasks-page.ts): still the ask.
        await page.evaluate(() => { void taskModalOpen('t-2', undefined, {thread: true, ask: true}); });
        await card(page).waitFor({timeout: 5000});
        assert.equal(await card(page).locator('.dc-q').innerText(), 'Which plan do new signups get?');
        await card(page).locator('[data-dc="close"]').click();
        // The task's conversation asked for by name (a bot's chat, taskThreadShow) opens as it is.
        await page.evaluate(() => { void taskModalOpen('t-2', undefined, {thread: true}); });
        await page.locator('#task-modal[open]').waitFor({timeout: 5000});
        assert.equal(await card(page).count(), 0, 'the conversation, not the ask');
        await page.keyboard.press('Escape');
        // The dialog's close event comes a moment after it closes, and a close by hand cancels an open still on its way
        // (task-modal.ts): open the next task once that close is handled.
        await page.waitForFunction(() => !document.body.classList.contains('task-modal-open'));
        // Not waiting on Ana: the task page, as today.
        await page.evaluate(() => { void taskModalOpen('t-4'); });
        await taskPage(page).waitFor({timeout: 5000});
        assert.equal(await card(page).count(), 0);
        await page.keyboard.press('Escape');
        // A link to the task in a message.
        await page.evaluate(() => {
          const md = document.createElement('div'); md.className = 'md'; md.style.cssText = 'position:fixed;top:200px;left:700px;z-index:9';
          md.innerHTML = '<a href="#/task/t-1">Checkout flow</a>';
          document.body.appendChild(md);
        });
        await page.locator('.md a[href="#/task/t-1"]').click();
        await card(page).waitFor({timeout: 5000});
        assert.equal(await card(page).locator('.dc-q').innerText(), 'Approve the spec for Checkout flow');
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // The first wait is someone else's (a list row carries only that one), and the person's own ask is behind it: the ask.
    {
      const tasks = fixtures(), needs = needsOf(tasks), t2 = tasks.find(t => t.id === 't-2')!;
      t2.turn = {...t2.turn!, who: 'human:sam', line: 'Waiting on Sam', requirement_id: 'r9'};
      const {page, errors} = await open(browser, {tasks, needs});
      try {
        await page.evaluate(() => { void taskModalOpen('t-2'); });
        await card(page).waitFor({timeout: 5000});
        assert.equal(await card(page).locator('.dc-q').innerText(), 'Which plan do new signups get?');
        assert.equal(await page.locator('#task-modal[open]').count(), 0);
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // Open task ↗: the task page opens and stays open; closing it leaves no ask screen behind.
    {
      const {page, errors} = await open(browser);
      try {
        await page.evaluate(() => { void taskModalOpen('t-1'); });
        await card(page).waitFor({timeout: 5000});
        const link = card(page).locator('[data-dc="task"]');
        assert.equal(await link.innerText(), '#1 ↗');
        await link.click();
        await taskPage(page).waitFor({timeout: 5000});
        assert.equal(await taskPage(page).innerText(), 'Checkout flow');
        await page.waitForTimeout(400);
        assert.equal(await card(page).count(), 0, 'the ask screen does not come back over the task page');
        assert.equal(await taskPage(page).count(), 1, 'the task page stays open');
        // Its Answer goes back to the ask.
        await page.locator('#task-modal[open] [data-task-answer]').click();
        await card(page).waitFor({timeout: 5000});
        await card(page).locator('[data-dc="close"]').click();
        await page.locator('#task-modal [data-modal-close]').click();
        await page.waitForTimeout(200);
        assert.equal(await card(page).count() + await page.locator('#task-modal[open]').count(), 0, 'back where it started');
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // The task's chat: a file picked with 📎 goes up to the task first and is named at the end of the words the arrow
    // sends back; one that fails is named, nothing is sent, and the words and file stay to send again.
    {
      let fail = true, uploads = 0;
      const {page, errors, posts} = await open(browser, {viewport: {width: 1040, height: 760}, beforeGoto: async p => {
        await p.route(u => /^\/api\/v2\/tasks\/t-1\/files$/.test(u.pathname), async route => {
          if (route.request().method() !== 'POST') return route.fallback();
          const n = ++uploads;
          if (fail) { fail = false; return route.fulfill({status: 500, contentType: 'application/json', body: JSON.stringify({error: 'internal', detail: 'Disk full'})}); }
          return route.fulfill({contentType: 'application/json', body: JSON.stringify({file_id: `file-${n}`, version: 1, file: {name: 'notes.txt'}})});
        });
      }});
      try {
        await page.evaluate(() => { void taskModalOpen('t-1'); });
        const box = card(page).locator('.dc-chat .p-text');
        await box.waitFor({timeout: 5000});
        const side = await page.locator('.decide .dc-side').boundingBox();
        assert.ok(side && Math.abs(side.width - 420) <= 1, `the chat column is 420 px wide, got ${side?.width}`);
        await card(page).locator('.dc-chat .p-file').setInputFiles({name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('hello')});
        await box.fill('Needs a mobile layout');
        await card(page).locator('.dc-chat .p-send').click();
        await card(page).locator('.err', {hasText: 'notes.txt'}).waitFor({timeout: 5000});
        assert.equal(posts.filter(x => x.p.endsWith('/answer')).length, 0, 'nothing sent while a file failed');
        assert.equal(await box.inputValue(), 'Needs a mobile layout', 'the words stay');
        await card(page).locator('.dc-chat .p-send').click();
        await page.waitForFunction(() => document.querySelector('#need-card .dc-q')?.textContent !== 'Approve the spec for Checkout flow', null, {timeout: 5000});
        const sent = posts.filter(x => x.p.endsWith('/tasks/t-1/asks/r1/answer'));
        assert.equal(sent.length, 1);
        assert.equal(sent[0]!.body['value'], 'no');
        assert.equal(String(sent[0]!.body['note']), 'Needs a mobile layout\n\nFiles:\n- notes.txt (file-2)');
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }

    // A phone: the chat's box and the buttons are on screen, nothing scrolls sideways.
    {
      const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, tasks: fixtures()});
      try {
        await page.evaluate(() => { void taskModalOpen('t-1'); });
        // An OK opens on its document tab: the chat is the Chat tab.
        await card(page).locator('[data-dc="tab"][data-dc-tab="chat"]').click();
        await card(page).locator('.dc-chat .p-text').waitFor({timeout: 5000});
        await card(page).locator('.dc-chat .p-text').fill('A note typed on a phone. '.repeat(8));
        for (const what of ['.dc-chat .p-text', '[data-dc="approve"]', '[data-dc="snooze"]']) {
          const box = await card(page).locator(what).boundingBox();
          assert.ok(box && box.y >= 0 && box.y + box.height <= 844, `${what} is on screen`);
        }
        const sideways = await page.evaluate(() => Math.max(document.documentElement.scrollWidth, document.querySelector('#need-card')!.scrollWidth) - innerWidth);
        assert.ok(sideways <= 0, `nothing scrolls sideways (${sideways} px)`);
        assert.deepEqual(errors, []);
      } finally { await page.close(); }
    }
    console.log('PASS: a task waiting on you opens on its ask; Open task ↗; a bigger note with screenshots and files.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
