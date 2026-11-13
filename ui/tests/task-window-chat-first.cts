import type * as Tasks from './tasks-page.cts';
import type {Page} from 'playwright';
// The task window, chat first (task #409, the task-window spec's option A), on the v3 fixture (ui/tests/tasks-page.cts);
// every request is intercepted.
// - 1280 × 800: the chat's newest line and the message box are in view without scrolling.
// - 60 lines: the newest show, and scrolling up draws older ones until the first message.
// - A page attached (a chip with its version), Jev's check and a send-back (its note's first line) are one-line
//   dividers in order; no line reads "changed <field>".
// - Three or more other updates in a row are one "N updates" line that opens.
// - One Visible to row (Team, Public roadmap or Private · names); no Private or Public box, no Size, no Who can see.
// - A page row is its icon and name and opens the page.
// - A phone at 390 px: Chat, Pages and Info tabs, open on Chat, the box at the foot of the window.
// - No links, runs or meetings: none of those sections show; ⋯ › Activity and links opens them.
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, task} = require('./tasks-page.cts') as Tasks.Api;

const T0 = Date.parse('2026-11-10T09:00:00Z');
const at = (min: number) => new Date(T0 + min * 60000).toISOString();
const say = (n: number) => ({id: `m${n}`, sender_id: n % 2 ? 'human:ana' : 'bot:writer', kind: 'say', body: `Line ${n}`, refs: {}, created_at: at(n * 10)});
// 60 messages, then the history: a page, Jev's check, a send-back, then three quiet changes in a row, then a last word.
const comments = [...Array.from({length: 60}, (_, i) => say(i + 1)), {...say(99), body: 'The newest line', created_at: at(700)}];
const ev = (id: string, min: number, field: string, x: Record<string, string | null> = {}) => ({id, created_at: at(min), field, actor: 'keeper', old: null, new: null, note: null, ...x});
const events = [
  ev('e1', 601, 'page_attached', {actor: 'bot:writer', new: 'page:file-spec@3'}),
  ev('e2', 602, 'jev_verdict', {new: 'met', note: 'Jev found it meets the check (p 0.79)'}),
  ev('e3', 603, 'answer', {actor: 'human:ana', new: 'no', note: 'The chat should be higher\nand the rest smaller'}),
  ev('e4', 610, 'labels', {actor: 'human:ana', new: '["ui"]'}),
  ev('e5', 611, 'github', {new: 'tico#12 open', note: 'GitHub: tico#12 open (state)'}),
  ev('e6', 612, 'number', {new: '409'}),
  ev('e7', 613, 'woken_again', {new: 'bot:writer'}),
];
const PAGE = {file_id: 'file-spec', title: 'Task window spec', version: 3, replaced_by_id: null, archived: false};
const tasks = () => [task('t-1', 'Chat first in the task window', 'spec', {owner_id: 'bot:writer'})];
const pages = (page: Page) => page.route(url => url.pathname.startsWith('/api/v2/places/'), route =>
  route.fulfill({contentType: 'application/json', body: JSON.stringify({can_link: true, pins: [{id: 'pin-1', size: 'medium', can_unpin: true, page: PAGE}]})}));

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // ---- a computer at 1280 × 800
    {
      const {page, errors} = await open(browser, {viewport: {width: 1280, height: 800}, hash: '#/task/t-1', tasks: tasks(), needs: [],
        comments: {'t-1': comments}, events: {'t-1': events}, beforeGoto: pages});
      const win = page.locator('#task-modal[open]'), thread = win.locator('.task-comments');
      await thread.getByText('The newest line').waitFor();
      // 1. The newest line and the box are in the viewport, the window unscrolled.
      const inView = async (sel: string) => page.evaluate(s => {
        const r = document.querySelector(s)!.getBoundingClientRect();
        return r.height > 0 && r.top >= 0 && r.bottom <= innerHeight && r.left >= 0 && r.right <= innerWidth;
      }, sel);
      await page.waitForFunction(() => { const t = document.querySelector('#task-modal .task-comments')!; return t.scrollHeight - t.scrollTop - t.clientHeight < 4; });
      assert.ok(await inView('#task-modal .task-comments [data-message="m99"]'), 'the newest line is in view');
      assert.ok(await inView('#task-modal .task-chat textarea'), 'the message box is in view');
      // The chat is the left column; the properties are to its right.
      const chat = (await win.locator('.task-chat').boundingBox())!, side = (await win.locator('.task-side').boundingBox())!;
      assert.ok(chat.x < side.x && chat.y < 200, `the chat is the left column from the top (chat at ${chat.x},${chat.y}; side at ${side.x})`);

      // 2. Only the newest lines are drawn; scrolling up draws older ones until the first message.
      assert.equal(await thread.locator('[data-message="m1"]').count(), 0, 'the first message waits until scrolled to');
      assert.match(await thread.locator('[data-task-earlier]').innerText(), /^↑ \d+ earlier messages and moves$/);
      // Drawing older lines keeps the line that was at the top where it was on screen (taskChatEarlier).
      const kept = await thread.evaluate(t => {
        t.scrollTop = 0;
        const top = t.querySelector<HTMLElement>('[data-message]')!, y = top.getBoundingClientRect().top;
        t.dispatchEvent(new Event('scroll'));
        const now = t.querySelector<HTMLElement>(`[data-message="${top.dataset.message}"]`)!;
        return {id: top.dataset.message, moved: Math.abs(now.getBoundingClientRect().top - y), first: t.querySelector<HTMLElement>('[data-message]')!.dataset.message};
      });
      assert.notEqual(kept.first, kept.id, 'older lines were drawn above');
      assert.ok(kept.moved <= 4, `the line that was at the top stays in place (moved ${kept.moved}px)`);
      await page.waitForTimeout(100);
      for (let i = 0; i < 5 && !(await thread.locator('[data-message="m1"]').count()); i++) {
        await thread.evaluate(t => { t.scrollTop = 0; t.dispatchEvent(new Event('scroll')); });
        await page.waitForTimeout(100);
      }
      assert.equal(await thread.locator('[data-message="m1"]').count(), 1, 'scrolling up reaches the first message');
      assert.equal(await thread.locator('[data-task-earlier]').count(), 0, 'nothing earlier is left');

      // 3. Page attached, Jev's check and the send-back are dividers, in order; the send-back shows its note's first line.
      const marks = await thread.locator('.tmark').evaluateAll(els => els.map(e => (e.textContent || '').replace(/\s+/g, ' ').trim()));
      const first = (re: RegExp) => marks.findIndex(m => re.test(m));
      const iPage = first(/attached a page/), iJev = first(/Jev's check: met/), iBack = first(/sent it back — The chat should be higher \d/);
      assert.ok(iPage >= 0 && iJev > iPage && iBack > iJev, `in order: ${JSON.stringify(marks)}`);
      assert.equal(await thread.locator('.tmark .tpage-chip').getAttribute('href'), '#/page/file-spec?v=3', 'the chip opens that version');
      assert.match(await thread.locator('.tmark .tpage-chip').innerText(), /v3/);
      assert.ok(!marks.some(m => /\bchanged \w/.test(m)), `no line reads "changed <field>": ${JSON.stringify(marks)}`);

      // 4. The four quiet changes in a row are one shut "4 updates" line that opens.
      const group = thread.locator('details.src-group');
      assert.equal(await group.count(), 1);
      assert.match(await group.locator('> summary').innerText(), /^4 updates/);
      assert.equal(await group.evaluate(g => (g as HTMLDetailsElement).open), false);
      await group.locator('> summary').click();
      await group.getByText(/numbered it #409/i).waitFor();

      // 5. One Visible to row; no Private or Public box, no Size, no Who can see.
      const props = win.locator('[data-task-props]');
      assert.equal(await props.locator('[data-prop-row="visible"]').count(), 1);
      assert.match(await props.locator('[data-prop-row="visible"]').innerText(), /Visible to\s+(groups\s*)?Team/);
      for (const row of ['private', 'publication', 'size', 'readers'])
        assert.equal(await props.locator(`[data-prop-row="${row}"]`).count(), 0, `no ${row} row`);
      assert.equal(await props.locator('input[type="checkbox"]').count(), 0, 'no checkbox');
      // Choosing Private saves it and opens the people picker.
      await props.locator('[data-prop="visible"]').click();
      const menu = page.locator('.prop-pop:popover-open');
      // Team is where it is (ticked); the fixture's Product task is a roadmap task, so Public roadmap is offered.
      assert.deepEqual(await menu.locator('[data-prop-pick]').evaluateAll(els => els.map(e => `${e.getAttribute('data-prop-pick')} ${e.getAttribute('aria-checked')}`)),
        ['team true', 'public false', 'private false']);
      await menu.getByRole('menuitemradio', {name: 'Private'}).click();
      await props.locator('[data-prop-row="visible"][data-visible="private"]').waitFor();
      await page.locator('.prop-pop:popover-open').getByText('Who can see it').waitFor();
      await page.keyboard.press('Escape');

      // 6. A page row is its icon and name, and opens the page.
      const row = win.locator('[data-task-pages] .tp-row');
      await row.waitFor();
      assert.equal((await row.innerText()).replace(/\s+/g, ' ').trim(), 'article Task window spec');
      assert.equal(await win.locator('[data-task-pages] .pg-card').count(), 0, 'no card, no Open, no Updated line');
      assert.equal(await row.getAttribute('href'), '#/page/file-spec');
      // "+" offers Make page or Link page; the Describe box shows only after Make page.
      const pagesBox = win.locator('[data-task-pages]');
      assert.equal(await pagesBox.getByRole('textbox', {name: 'Describe the page'}).isVisible(), false);
      await pagesBox.getByRole('button', {name: 'Add a page'}).click();
      await pagesBox.getByRole('button', {name: 'Make page', exact: true}).click();
      await pagesBox.getByRole('textbox', {name: 'Describe the page'}).waitFor();

      // 8. No links, runs or meetings: none of those sections show until ⋯ › Activity and links.
      for (const sel of ['.task-links', '[data-task-runs]', '[data-task-meetings]'])
        assert.equal(await win.locator(sel).isVisible(), false, `${sel} is hidden`);
      await win.locator('[data-task-more]').click();
      await page.locator('.prop-pop:popover-open').getByRole('menuitem', {name: 'Activity and links'}).click();
      await win.locator('[data-task-activity]').waitFor({state: 'visible'});
      assert.ok(await win.locator('[data-task-activity] .task-links').isVisible(), 'Links is under Activity and links');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // ---- 7. a phone at 390 px: three tabs, Chat first, the box at the window's foot
    {
      const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, hash: '#/task/t-1', tasks: tasks(), needs: [],
        comments: {'t-1': comments}, events: {'t-1': events}, beforeGoto: pages});
      const win = page.locator('#task-modal[open]');
      await win.locator('.task-comments').getByText('The newest line').waitFor();
      const tabs = win.getByRole('tab');
      assert.deepEqual((await tabs.allInnerTexts()).map(s => s.replace(/\s+/g, ' ').trim()), ['Chat', 'Pages 1', 'Info']);
      assert.equal(await win.getByRole('tab', {name: 'Chat'}).getAttribute('aria-selected'), 'true');
      assert.equal(await win.locator('[data-task-props]').isVisible(), false, 'Info is a tab away');
      const box = (await win.locator('.task-chat textarea').boundingBox())!;
      assert.ok(box.y + box.height <= 844 && box.y > 844 - 160, `the box sits at the foot (y ${box.y})`);
      // With the keyboard up the visible rectangle shrinks (ui/app/viewport.ts); the box stays inside it.
      await page.evaluate(() => document.documentElement.style.setProperty('--chat-viewport-height', '500px'));
      const up = (await win.locator('.task-chat textarea').boundingBox())!;
      assert.ok(up.y + up.height <= 500, `the box stays above the keyboard (bottom ${up.y + up.height})`);
      await page.evaluate(() => document.documentElement.style.removeProperty('--chat-viewport-height'));
      await win.getByRole('tab', {name: /Pages/}).click();
      await win.locator('[data-task-pages] .tp-row').waitFor({state: 'visible'});
      assert.equal(await win.locator('.task-chat').isVisible(), false);
      await win.getByRole('tab', {name: 'Info'}).click();
      await win.locator('[data-prop-row="visible"]').waitFor({state: 'visible'});
      assert.equal(await win.locator('[data-task-pages]').isVisible(), false);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth) <= 390, 'nothing scrolls sideways');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('task window chat first: layout, history, updates, Visible to, page rows, phone tabs, Activity and links: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
