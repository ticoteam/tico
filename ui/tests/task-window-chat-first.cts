import type * as Tasks from './tasks-page.cts';
import type {Locator, Page} from 'playwright';
// The task window, chat first (the style guide's Task window: the chat, Details from its icon, and the Files tab beside
// Chat, with a tab for each document, ui/tests/task-documents.cts), on the v3 fixture
// (ui/tests/tasks-page.cts); every request is intercepted.
// - 1280 × 800: a task with messages opens on Chat; the newest line and the message box are in view without scrolling.
// - 60 lines: the newest show, and scrolling up draws older ones until the first message.
// - A page attached (a chip with its version), the Decision model's check and a send-back (its note's first line) are one-line
//   dividers in order; no line reads "changed <field>".
// - Two or more other updates in a row are one "N updates" line that opens.
// - One Visible to row (Team, Public roadmap or Private · names); no Private or Public box, no Size, no Who can see.
// - On the Files tab a page row is its icon and name and opens the page; "Link a page or file" opens the page search (recent pages, "or drop a file to
//   upload"); ⋯ › Make a page… opens the Describe box.
// - A phone at 390 px: Chat and Files only, open on the chat, the box at the foot of the window.
// - No links, runs or meetings: none of those sections show; ⋯ › Activity and links opens them.
declare function liveEmit(topic: string, data: unknown): void;
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, task} = require('./tasks-page.cts') as Tasks.Api;

const T0 = Date.parse('2026-11-10T09:00:00Z');
const at = (min: number) => new Date(T0 + min * 60000).toISOString();
const say = (n: number) => ({id: `m${n}`, sender_id: n % 2 ? 'human:ana' : 'bot:writer', kind: 'say', body: `Line ${n}`, refs: {}, created_at: at(n * 10)});
// 60 messages, then the history: a page, the Decision model's check, a send-back, then three quiet changes in a row, then a last word.
const comments = [...Array.from({length: 60}, (_, i) => say(i + 1)), {...say(99), body: 'The newest line', created_at: at(700)}];
const ev = (id: string, min: number, field: string, x: Record<string, string | null> = {}) => ({id, created_at: at(min), field, actor: 'keeper', old: null, new: null, note: null, ...x});
const events = [
  ev('e1', 601, 'page_attached', {actor: 'bot:writer', new: 'page:file-spec@3'}),
  ev('e2', 602, 'jev_verdict', {new: 'met', note: 'Decision model found it meets the check (p 0.79)'}),
  ev('e3', 603, 'answer', {actor: 'human:ana', new: 'no', note: 'The chat should be higher\nand the rest smaller'}),
  ev('e4', 610, 'labels', {actor: 'human:ana', new: '["ui"]'}),
  ev('e5', 611, 'github', {new: 'tico#12 open', note: 'GitHub: tico#12 open (state)'}),
  ev('e6', 612, 'number', {new: '409'}),
  ev('e7', 613, 'woken_again', {new: 'bot:writer'}),
];
const PAGE = {file_id: 'file-spec', title: 'Task window spec', version: 3, replaced_by_id: null, archived: false};
const tasks = () => [task('t-1', 'Chat first in the task window', 'spec', {owner_id: 'bot:writer'}), task('t-2', 'A task the reader cannot add pages to', 'spec', {owner_id: 'bot:writer'})];
// Each task's pages (GET /api/v2/places/task:<id>) and its Make page request (GET and POST /api/v2/tasks/<id>/make-page).
const PIN = {id: 'pin-1', size: 'medium', can_unpin: true, page: PAGE};
// The page is linked to the task as related: the Files tab lists it (GET /api/v2/tasks/{id}/files?all=1).
const LINKED = {id: 'file-spec', title: 'Task window spec', name: 'task-window-spec.html', current_version: 3, updated_at: at(600),
  links: [{target: 'task:t-1', role: 'related', name: null}], writers: [{actor: 'bot:writer', name: null}], versions: [{n: 3, by: 'bot:writer', created_at: at(600)}]};
// The tab keys on the tab row: Chat and Files, then one per document (none here).
const tabKeys = (win: Locator) => win.locator('.tw-tabs [data-task-tab]').evaluateAll(els => els.map(e => (e as HTMLElement).dataset.taskTab));
const PLACES: Record<string, {can_link: boolean; pins: unknown[]}> = {};
let making: string | null = null;
const pages = async (page: Page) => {
  PLACES['t-1'] = {can_link: true, pins: [PIN]}; PLACES['t-2'] = {can_link: false, pins: [PIN]}; making = null;
  await page.route(url => url.pathname.startsWith('/api/v2/places/'), route => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop()!).replace(/^task:/, '');
    return route.fulfill({contentType: 'application/json', body: JSON.stringify(PLACES[id] || {can_link: false, pins: []})});
  });
  await page.route(url => url.pathname.endsWith('/make-page'), route => {
    if (route.request().method() === 'POST') { making = 'made'; return route.fulfill({contentType: 'application/json', body: JSON.stringify({message: {conversation_id: 'c-make'}})}); }
    return route.fulfill({contentType: 'application/json', body: JSON.stringify({make_page: making ? {state: making, conversation_id: 'c-make'} : null})});
  });
  await page.route(url => /^\/api\/v2\/tasks\/[^/]+\/files$/.test(url.pathname), route => {
    const all = new URL(route.request().url()).searchParams.get('all') === '1';
    return route.fulfill({contentType: 'application/json', body: JSON.stringify({files: all ? [LINKED] : []})});
  });
};

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
      // Opening lands on the window itself, not the title (a focused title would open in full) nor a control (no ring).
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'task-modal', 'the focus is on the window');
      // No documents: Chat and Files, no document tab; the chat, and Details behind its icon.
      assert.deepEqual(await tabKeys(win), ['chat', 'files'], 'Chat and Files, no document');
      assert.equal(await win.locator('[data-task-tab="details"]').getAttribute('aria-pressed'), 'false');
      assert.equal(await win.locator('.task-side').isVisible(), false, 'Details is behind its icon');
      // The title is top left, on the same row as the actions; the window's ✕ is last, after Archive and ⋯.
      const title = (await win.locator('.tw-head .tmodal-title').boundingBox())!, close = (await win.locator('[data-modal-close]').boundingBox())!;
      assert.ok(Math.abs(title.y - close.y) < 12 && title.x < close.x, `title and actions share the head row (${title.y} / ${close.y})`);
      assert.deepEqual(await win.locator('.tw-acts > button:not([hidden])').evaluateAll(els => els.map(e => e.getAttribute('aria-label')?.replace(/,.*$/, ''))).then(l => l.slice(-4)), ['Archive', 'Details', 'More actions', 'Close window']);
      assert.equal(await win.getByRole('button', {name: 'Close', exact: true}).count(), 0, 'no "Close" word button');

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

      // 3. Page attached, the Decision model's check and the send-back are dividers, in order; the send-back shows its note's first line.
      const marks = await thread.locator('.tmark').evaluateAll(els => els.map(e => (e.textContent || '').replace(/\s+/g, ' ').trim()));
      const first = (re: RegExp) => marks.findIndex(m => re.test(m));
      const iPage = first(/attached a page/), iJev = first(/Decision model's check: met/), iBack = first(/sent it back — The chat should be higher (?!and)/);
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
      await group.getByText(/numbered #409/i).waitFor();   // Tico gives the number: a quiet line, no "it" (task-session.cts)

      // 5. One Visible to row; no Private or Public box, no Size, no Who can see.
      const props = win.locator('[data-task-props]');
      await win.getByRole('button', {name: /^Details/}).click();
      assert.equal(await win.locator('details.task-details').count(), 0, 'no Details fold: the facts are one line each');
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

      // 6. The Files tab: a page row is its icon and name, its role as a chip, and opens the page.
      await win.locator('.tw-tabs [data-task-tab="files"]').click();
      const row = win.locator('.tft-list .tft-row');
      await row.waitFor();
      assert.equal((await row.locator('.tft-t').innerText()).trim(), 'Task window spec');
      assert.equal((await row.locator('.tft-chip').innerText()).trim(), 'related');
      assert.equal(await win.locator('[data-task-pages] .pg-card').count(), 0, 'no card, no Open, no Updated line');
      assert.equal(await row.getAttribute('href'), '#/page/file-spec');
      // "Link a page or file" opens the page search, focused, with "or drop a file to upload" under the results; Esc closes it.
      const pagesBox = win.locator('[data-task-pages]');
      assert.equal(await pagesBox.getByRole('textbox', {name: 'Describe the page'}).isVisible(), false);
      await pagesBox.getByRole('button', {name: 'Link a page or file'}).click();
      const find = pagesBox.getByRole('searchbox', {name: 'Find a page'});
      await find.waitFor();
      assert.ok(await find.evaluate(e => e === document.activeElement), 'the search has the focus');
      assert.equal(await pagesBox.getByRole('button', {name: 'or drop a file to upload'}).isVisible(), true);
      assert.equal(await pagesBox.getByRole('button', {name: 'Make page', exact: true}).count(), 0, 'no Make page / Link page choice');
      await page.keyboard.press('Escape');
      await find.waitFor({state: 'hidden'});
      assert.equal(await win.getAttribute('open'), '', 'Esc closed the search, not the task');
      // Make page is ⋯ › Make a page…: the Describe box shows.
      await win.locator('[data-task-more]').click();
      await page.locator('.prop-pop:popover-open').getByRole('menuitem', {name: 'Make a page…'}).click();
      const describe = pagesBox.getByRole('textbox', {name: 'Describe the page'});
      await describe.waitFor();
      // Sent and made: the Describe box folds back to "+".
      await describe.fill('A map of the flow');
      await describe.press('Enter');
      await describe.waitFor({state: 'hidden'});
      assert.equal(making, 'made', 'the request was sent');
      // A file dropped on the page search uploads to the task (its Files, task-files.ts) and closes the search.
      const uploaded = page.waitForRequest(r => r.method() === 'POST' && new URL(r.url()).pathname === '/api/v2/tasks/t-1/files');
      await pagesBox.getByRole('button', {name: 'Link a page or file'}).click();
      await pagesBox.locator('.tp-picker').evaluate(zone => {
        const data = new DataTransfer();
        data.items.add(new File(['flow'], 'flow.png', {type: 'image/png'}));
        for (const type of ['dragenter', 'dragover', 'drop']) zone.dispatchEvent(new DragEvent(type, {bubbles: true, cancelable: true, dataTransfer: data}));
      });
      await uploaded;
      await find.waitFor({state: 'hidden'});

      // ⋯: the window's other actions, with icons; Delete is last, in red, after a line. No Close or Activity word button.
      await win.locator('[data-task-more]').click();
      const menu8 = page.locator('.prop-pop:popover-open');
      const said = (await menu8.locator('.tl-mi').allInnerTexts()).map(x => x.replace(/^\S+\s+/, '').trim());
      for (const want of ['Copy link', 'Snooze…', 'Move to a stage…', 'Activity and links', 'Delete']) assert.ok(said.includes(want), `⋯ has ${want}: ${said}`);
      assert.equal(said.at(-1), 'Delete', 'Delete is last');
      assert.ok(await menu8.locator('.tl-mi.danger').last().evaluate(b => b.previousElementSibling?.matches('.tl-menu-sep')), 'a line above Delete');
      assert.equal(await menu8.locator('.tl-mi', {hasText: /^Close$/}).count(), 0, 'Archive is in the head, not here');
      await page.keyboard.press('Escape');

      // 8. No links, runs or meetings: none of those sections show until ⋯ › Activity and links. They live in Details,
      // which the Files tab above has covered.
      await win.getByRole('button', {name: /^Details/}).click();
      for (const sel of ['.task-links', '[data-task-runs]', '[data-task-meetings]'])
        assert.equal(await win.locator(sel).isVisible(), false, `${sel} is hidden`);
      // ...and nor does the Activity and links heading: never a heading with nothing under it.
      assert.ok(await win.locator('[data-task-props]').isVisible(), 'Details is in view');
      assert.equal(await win.locator('[data-task-activity] > .rail-h').isVisible(), false, 'no empty Activity and links heading');
      await win.locator('[data-task-more]').click();
      await page.locator('.prop-pop:popover-open').getByRole('menuitem', {name: 'Activity and links'}).click();
      await win.locator('[data-task-activity]').waitFor({state: 'visible'});
      assert.ok(await win.locator('[data-task-activity] .task-links').isVisible(), 'Links is under Activity and links');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // ---- 7. a phone at 390 px: Chat and Files only, the chat first, the box at the window's foot
    {
      const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, hash: '#/task/t-1', tasks: tasks(), needs: [],
        comments: {'t-1': comments}, events: {'t-1': events}, beforeGoto: pages});
      const win = page.locator('#task-modal[open]');
      await win.locator('.task-comments').getByText('The newest line').waitFor();
      assert.deepEqual(await tabKeys(win), ['chat', 'files'], 'Chat and Files only');
      assert.equal(await win.locator('[data-task-props]').isVisible(), false, 'Details is behind its icon');
      // The head keeps Archive, Details, ⋯ and ✕, each a 44px target; the age goes into ⋯.
      for (const name of ['Archive', 'Details', 'More actions', 'Close window']) {
        const b = (await (name === 'Details' ? win.locator('[data-task-tab="details"]') : win.getByRole('button', {name, exact: true})).boundingBox())!;
        assert.ok(b.width >= 44 && b.height >= 44, `${name} is a 44px target (${b.width}×${b.height})`);
      }
      assert.equal(await win.locator('.tmodal-age').isVisible(), false, 'the age is in ⋯ on a phone');
      const box = (await win.locator('.task-chat textarea').boundingBox())!;
      assert.ok(box.y + box.height <= 844 && box.y > 844 - 160, `the box sits at the foot (y ${box.y})`);
      // With the keyboard up the visible rectangle shrinks (ui/app/viewport.ts); the box stays inside it.
      await page.evaluate(() => document.documentElement.style.setProperty('--chat-viewport-height', '500px'));
      const up = (await win.locator('.task-chat textarea').boundingBox())!;
      assert.ok(up.y + up.height <= 500, `the box stays above the keyboard (bottom ${up.y + up.height})`);
      await page.evaluate(() => document.documentElement.style.removeProperty('--chat-viewport-height'));
      await win.getByRole('button', {name: /^Details/}).click();
      await win.locator('[data-task-props]').waitFor({state: 'visible'});
      await win.locator('[data-prop-row="visible"]').waitFor({state: 'visible'});
      assert.equal(await win.locator('.task-chat').isVisible(), false);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth) <= 390, 'nothing scrolls sideways');
      // A reader without link permission still sees readable pages and can make a page if they can comment.
      await page.evaluate(() => { location.hash = '#/task/t-2'; });
      const other = page.locator('#task-modal[open][data-task="t-2"]');
      await other.locator('.tw-tabs [data-task-tab="files"]').click();
      await other.locator('.tft-list .tft-row').waitFor();
      assert.equal(await other.locator('[data-tp-add]').isVisible(), false, 'no + for a reader who may not link pages');
      PLACES['t-2'] = {can_link: false, pins: []};
      await page.evaluate(() => liveEmit('tasks', {id: 't-2', kind: 'update', kinds: ['files']}));
      assert.equal(await other.locator('[data-tp-add]').isVisible(), false);
      assert.equal(await other.locator('[data-tft-new]').isVisible(), true);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('task window chat first: tabs, head, history, updates, Visible to, pages and search, phone, Activity and links: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
