import type * as Tasks from './tasks-page.cts';
import type {Page} from 'playwright';
// A task's documents in the task window (ui/app/task-docs.ts, task-modal.ts; backend/task_documents.py), on the v3
// fixture (ui/tests/tasks-page.cts); every request is intercepted:
// - a task with no documents shows no tabs at all, not even Chat: just its chat, Details behind its icon;
// - a task with a Spec and a QA report: the tabs sit on the title row, right of the title, Chat first, then the
//   documents, most recently changed first; ←/→ move along them;
// - the window opens with the focus on itself: no control wears a ring before the keyboard moves;
// - a document opens under one small line, "v2 · Writer · 2h", which opens its versions (each once, with its note, the
//   one shown in bold) and Compare; a version picked shows; Compare shows the two versions' text, line by line;
// - a document takes the app's colours, light or dark;
// - Details opens over a document and closes back to it;
// - a phone at 390 px: the head holds the title (40% or more, up to two lines), the tabs, ⋯ and ✕; Details and Archive
//   are in ⋯; the tabs scroll so the one selected shows, Chat from the start; nothing scrolls sideways.
// TICO_SHOTS=<dir> saves each state, desktop and phone, dark and light.
declare function liveEmit(topic: string, data: unknown): void;
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const path: typeof import('node:path') = require('node:path');
const {open, task} = require('./tasks-page.cts') as Tasks.Api;

const shots = process.env.TICO_SHOTS || '';
const ago = (min: number) => new Date(Date.now() - min * 60000).toISOString();
const SPEC = 'file-doc-spec', QA = 'file-doc-qa';
const docs = () => [
  {name: 'QA report', file_id: QA, version: 1, by: 'bot:writer', at: ago(20), versions: [{n: 1, by: 'bot:writer', at: ago(20), note: 'All six checks pass'}]},
  {name: 'Spec', file_id: SPEC, version: 2, by: 'bot:writer', at: ago(125), versions: [
    {n: 2, by: 'bot:writer', at: ago(125), note: 'Adds the empty state'}, {n: 1, by: 'human:ana', at: ago(60 * 26), note: 'First draft'}]},
];
const PAGES: Record<string, string> = {
  [`${SPEC}@1`]: '<h1>Saved carts</h1><p>A cart survives closing the tab.</p><p>It expires after 7 days.</p>',
  [`${SPEC}@2`]: '<h1>Saved carts</h1><p>A cart survives closing the tab.</p><p>It expires after 30 days.</p><p>Empty state: "Nothing saved yet."</p>',
  [`${QA}@1`]: '<h1>QA report</h1><ul><li>Cart survives closing the tab ✓</li><li>Expires after 30 days ✓</li></ul>',
};
const tasks = () => [
  task('t-1', 'Fix the login redirect', 'spec', {owner_id: 'bot:writer'}),
  task('t-2', 'Saved carts that survive closing the tab', 'spec', {owner_id: 'bot:writer', documents: docs()}),
];
const files = async (page: Page) => {
  await page.route(url => url.pathname.startsWith('/api/v2/files/'), route => {
    const url = new URL(route.request().url()), id = decodeURIComponent(url.pathname.split('/').pop()!);
    const body = PAGES[`${id}@${url.searchParams.get('v') || 1}`];
    return body ? route.fulfill({contentType: 'text/html', body}) : route.fulfill({status: 404, contentType: 'application/json', body: '{}'});
  });
};
const shot = async (page: Page, name: string) => {
  if (!shots) return;
  await page.waitForTimeout(250);
  await page.screenshot({path: path.join(shots, name + '.png')});
};
const texts = (page: Page) => page.locator('#task-modal[open] [role="tab"]').evaluateAll(els => els.map(e => (e.textContent || '').trim()));

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const theme of ['light', 'dark']) {
      // ---- a computer: no documents, no tabs
      const {page, errors} = await open(browser, {viewport: {width: 1280, height: 800}, theme, hash: '#/task/t-1', tasks: tasks(), needs: [], beforeGoto: files});
      const win = page.locator('#task-modal[open]');
      await win.locator('.task-chat .task-comments').waitFor();
      await win.locator('.task-chat textarea').waitFor();
      assert.equal(await win.locator('.doc-tabs').count(), 0, 'a task with no documents has no tab row');
      assert.equal(await win.getByRole('tab').count(), 0, 'not even a Chat tab');
      assert.equal(await win.locator('.task-chat').isVisible(), true, 'just its chat');
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'task-modal', 'the focus is on the window');
      assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll(':focus-visible')].map(e => [e.id || e.tagName, getComputedStyle(e).outlineStyle])),
        [['task-modal', 'none']], 'no focus ring');
      await shot(page, `no-documents-desktop-${theme}`);

      // ---- a Spec and a QA report: the tabs on the title row, Chat first, then newest change first
      await page.evaluate(() => { location.hash = '#/task/t-2'; });
      const docWin = page.locator('#task-modal[open][data-task="t-2"]');
      await docWin.locator('.tw-tabs').waitFor();
      assert.deepEqual(await texts(page), ['Chat', 'QA report', 'Spec']);
      assert.equal(await docWin.getByRole('tab', {name: 'Chat'}).getAttribute('aria-selected'), 'true', 'it opens on the chat');
      const [title, row, close] = [await docWin.locator('.tmodal-title').boundingBox(), await docWin.locator('.tw-tabs').boundingBox(), await docWin.locator('[data-modal-close]').boundingBox()];
      assert.ok(title && row && close && row.x > title.x + title.width - 1 && row.x + row.width <= close.x, `the tabs sit right of the title, before the actions (${JSON.stringify([title, row, close])})`);
      assert.ok(row && title && Math.abs((row.y + row.height / 2) - (title.y + title.height / 2)) < 12, 'on the title row');
      await shot(page, `tabs-chat-desktop-${theme}`);

      // A document opens under its one version line.
      await docWin.getByRole('tab', {name: 'Spec'}).click();
      const view = docWin.locator('.task-doc'), line = view.locator('[data-doc-ver]');
      await view.locator('iframe.html-view').waitFor();
      assert.equal(await docWin.locator('.task-chat').isVisible(), false, 'the chat waits in its tab');
      assert.match((await line.innerText()).trim(), /^v2 · Writer · 2h/);
      const one = (await line.boundingBox())!;
      assert.ok(one.height <= 24, `the version line is one small line (${one.height}px)`);
      assert.equal(await view.locator('.doc-v').count(), 0, 'its versions wait behind it');
      assert.equal(await view.locator('iframe.html-view').getAttribute('title'), 'Spec v2');
      await shot(page, `spec-desktop-${theme}`);
      // The document is in the app's colours.
      const [paper, app] = [await view.frameLocator('iframe.html-view').locator('html').evaluate(e => getComputedStyle(e).backgroundColor),
        await page.evaluate(() => getComputedStyle(document.body).backgroundColor)];
      assert.equal(paper, app, `the document's background is the app's (${paper} vs ${app})`);
      await line.click();
      assert.equal(await line.getAttribute('aria-expanded'), 'true');
      assert.equal(await view.locator('.doc-ver').isVisible(), false, 'open: no row of its own over the list, no version said twice');
      assert.equal(await view.locator('.doc-v-cur [data-doc-compare]').count(), 1, 'Compare at the end of the version shown');
      assert.equal(await view.locator('.doc-v[aria-current="true"]').evaluate(e => Number(getComputedStyle(e).fontWeight)), 600, 'the one shown, in bold');
      const vs = await view.locator('.doc-v').evaluateAll(els => els.map(e => [...e.children].map(c => (c.textContent || '').trim())));
      assert.equal(vs.length, 2);
      assert.match(vs[0]!.join(' | '), /^v2 · Writer · 2h \| Adds the empty state$/);
      assert.match(vs[1]!.join(' | '), /^v1 · You · \S.* \| First draft$/, 'your own version says You');
      await shot(page, `spec-versions-desktop-${theme}`);
      await line.click();
      assert.equal(await view.locator('.doc-v').count(), 0, 'the version line tapped again shuts the list');
      await line.click();

      // Compare: the two versions' text, line by line.
      await view.locator('[data-doc-compare]').click();
      const diff = view.locator('.doc-diff');
      await diff.waitFor();
      assert.match(await diff.locator('.add').allInnerTexts().then(l => l.join('\n')), /30 days[\s\S]*Nothing saved yet/);
      assert.match(await diff.locator('.del').allInnerTexts().then(l => l.join('\n')), /7 days/);
      assert.equal(await view.locator('[data-doc-from]').inputValue(), '1');
      assert.equal(await view.locator('[data-doc-to]').inputValue(), '2');
      await shot(page, `spec-compare-desktop-${theme}`);
      await view.locator('[data-doc-compare-off]').click();
      await view.locator('iframe.html-view').waitFor();

      // A version picked shows.
      await line.click();
      await view.locator('[data-doc-v="1"]').click();
      await page.waitForFunction(() => document.querySelector('#task-modal .task-doc iframe')?.getAttribute('title') === 'Spec v1');
      assert.match((await line.innerText()).trim(), /^v1 · You · /);
      assert.equal(await view.locator('.doc-v').count(), 0, 'picked: the list shuts');

      // Details opens over the document and closes back to it.
      const details = docWin.locator('[data-task-tab="details"]');
      await details.click();
      assert.equal(await details.getAttribute('aria-pressed'), 'true');
      assert.equal(await docWin.locator('[data-task-props]').isVisible(), true, 'Details shows');
      assert.equal(await view.isVisible(), false);
      await details.click();
      assert.equal(await view.isVisible(), true, 'and closes back to the document');
      assert.equal(await docWin.getByRole('tab', {name: 'Spec'}).getAttribute('aria-selected'), 'true');

      // ←/→ move along the tabs.
      await docWin.getByRole('tab', {name: 'Chat'}).focus();
      await page.keyboard.press('ArrowRight');
      assert.equal(await docWin.getByRole('tab', {name: 'QA report'}).getAttribute('aria-selected'), 'true');
      await view.locator('iframe[title="QA report v1"]').waitFor();
      assert.match((await line.innerText()).trim(), /^v1 · Writer · 20m/);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // ---- a phone: the same row on the title line, the title cut first; nothing scrolls sideways
    for (const theme of ['light', 'dark']) {
      const {page, errors, tasks: fixture} = await open(browser, {viewport: {width: 390, height: 844}, theme, hash: '#/task/t-1', tasks: tasks(), needs: [], beforeGoto: files});
      const win = page.locator('#task-modal[open]');
      await win.locator('.task-chat textarea').waitFor();
      assert.equal(await win.getByRole('tab').count(), 0, 'no documents, no tabs');
      await shot(page, `no-documents-phone-${theme}`);
      await page.evaluate(() => { location.hash = '#/task/t-2'; });
      const docWin = page.locator('#task-modal[open][data-task="t-2"]');
      await docWin.locator('.tw-tabs').waitFor();
      const [title, row] = [await docWin.locator('.tmodal-title').boundingBox(), await docWin.locator('.tw-tabs').boundingBox()];
      assert.ok(title && row && Math.abs((row.y + row.height / 2) - (title.y + title.height / 2)) < 12, `one row (${JSON.stringify([title, row])})`);
      const head = (await docWin.locator('.tw-head').boundingBox())!;
      assert.ok(title!.width >= head.width * 0.4 - 8, `the title keeps 40% of the row (${title!.width} of ${head.width})`);
      assert.deepEqual(await docWin.locator('.tw-acts > button:visible').evaluateAll(els => els.map(e => e.getAttribute('aria-label'))), ['More actions', 'Close window'], 'the head: tabs, ⋯ and ✕');
      for (const b of await docWin.locator('.tw-tabs [role="tab"]').all()) assert.ok(((await b.boundingBox())?.height ?? 0) >= 44, 'each tab a 44 px target');
      const inStrip = (name: string) => docWin.locator('.tw-tabs').evaluate((strip, n) => {
        const t = [...strip.querySelectorAll('[role="tab"]')].find(e => e.textContent?.trim() === n)!, a = t.getBoundingClientRect(), s = strip.getBoundingClientRect();
        return a.left >= s.left - 1 && a.right <= s.right + 1;
      }, name);
      assert.equal(await inStrip('Chat'), true, 'Chat selected: the strip starts at Chat');
      await shot(page, `tabs-chat-phone-${theme}`);
      await docWin.locator('[data-task-more]').click();
      const menu = page.locator('.prop-pop:popover-open');
      await menu.waitFor();
      const said = await menu.locator('.tl-mi').allInnerTexts();
      assert.ok(['Details', 'Archive'].every(w => said.some(s => s.includes(w))), `Details and Archive are in ⋯ (${said})`);
      await shot(page, `more-menu-phone-${theme}`);
      await page.keyboard.press('Escape');
      await menu.waitFor({state: 'hidden'});
      await docWin.getByRole('tab', {name: 'Spec'}).click();
      await docWin.locator('.task-doc iframe.html-view').waitFor();
      assert.equal(await inStrip('Spec'), true, 'the tab selected shows');
      await shot(page, `spec-phone-${theme}`);
      await docWin.locator('.task-doc [data-doc-ver]').click();
      await shot(page, `spec-versions-phone-${theme}`);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth) <= 390, 'nothing scrolls sideways');

      // A document written while the window is open: its tab comes as the task is read again.
      const t1 = page.locator('#task-modal[open][data-task="t-1"]');
      await page.evaluate(() => { location.hash = '#/task/t-1'; });
      await t1.locator('.task-chat textarea').waitFor();
      assert.equal(await t1.getByRole('tab').count(), 0);
      fixture.find(t => t.id === 't-1')!['documents'] = [docs()[0]];
      await page.evaluate(() => liveEmit('tasks', {id: 't-1', kind: 'update', kinds: ['files']}));
      await t1.locator('.tw-tabs').waitFor();
      assert.deepEqual(await texts(page), ['Chat', 'QA report']);
      assert.equal(await t1.getByRole('tab', {name: 'Chat'}).getAttribute('aria-selected'), 'true', 'the chat stays shown');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('PASS: a task\'s documents: no documents, no tabs; Chat then documents newest first on the title row; one version line that opens the versions and Compare; Details over a document; a phone\'s same row.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
