import type * as Tasks from './tasks-page.cts';
import type {Page, Route} from 'playwright';
// A task's Spec pages and its Files tab in the task window (ui/app/task-docs.ts taskSpecsHTML, ui/app/task-pages.ts,
// ui/app/task-modal.ts), on the v3 fixture (ui/tests/tasks-page.cts); every request is intercepted:
// - each Spec page is a card under the title, not a tab; a page shared with another task says "also the Spec of D-…";
//   a page changed since the OK that covered it has a dot; a card opens the page; no Spec, no cards;
// - the Files tab, "Files N", lists every page and file linked to the task (GET /api/v2/tasks/{id}/files?all=1): a
//   page with its roles as chips, a file by its name, a page already on the task not offered again by the picker;
// - Link a page or file: the picker links a page as related (POST /api/v2/pages/{id}/links);
// - New page: a title and a text, saved as the task's related page (POST /api/v2/pages).
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, task} = require('./tasks-page.cts') as Tasks.Api;

const ago = (min: number) => new Date(Date.now() - min * 60000).toISOString();
const SPEC_A = 'file-spec-a', SPEC_B = 'file-spec-b', BRIEF = 'file-brief', CSV = 'file-csv', OLD = 'file-old';
const SHARED = 'Saved carts that survive closing the tab';
// Two Specs: A is also the Spec of D-18463 (another task), B changed since its OK.
const docs = () => [
  {name: 'Spec', file_id: SPEC_A, page_id: SPEC_A, version: 2, by: 'bot:writer', at: ago(30), changed_since_ok: false,
    versions: [{n: 2, by: 'bot:writer', at: ago(30), note: 'Adds the empty state'}],
    links: [{target: 'bot:writer', role: 'writer', name: null}, {target: 'task:t-2', role: 'Spec', name: SHARED},
      {target: 'task:t-9', role: 'Spec', name: 'D-18463 Saved carts'}]},
  {name: 'Spec', file_id: SPEC_B, page_id: SPEC_B, version: 1, by: 'bot:writer', at: ago(10), changed_since_ok: true,
    versions: [{n: 1, by: 'bot:writer', at: ago(10), note: null}],
    links: [{target: 'bot:writer', role: 'writer', name: null}, {target: 'task:t-2', role: 'Spec', name: 'Cart rules'}]},
];
const tasks = () => [
  task('t-1', 'Fix the login redirect', 'spec', {owner_id: 'bot:writer'}),
  task('t-2', 'Saved carts that survive closing the tab', 'spec', {owner_id: 'bot:writer', documents: docs()} as Parameters<typeof task>[3]),
];
// The task's linked pages and files (the Files list): two Spec pages, a page linked as related, and a file.
const linked = () => [
  {id: SPEC_A, title: 'Saved carts', name: 'saved-carts.html', current_version: 2, updated_at: ago(30),
    links: [{target: 'task:t-2', role: 'Spec', name: SHARED}, {target: 'task:t-9', role: 'Spec', name: 'D-18463 Saved carts'}],
    writers: [{actor: 'bot:writer', name: null}], versions: [{n: 2, by: 'bot:writer', created_at: ago(30)}]},
  {id: SPEC_B, title: 'Cart rules', name: 'cart-rules.html', current_version: 1, updated_at: ago(10),
    links: [{target: 'task:t-2', role: 'Spec', name: 'Cart rules'}],
    writers: [{actor: 'bot:writer', name: null}], versions: [{n: 1, by: 'bot:writer', created_at: ago(10)}]},
  {id: BRIEF, title: 'Saved carts brief', name: 'brief.html', current_version: 1, updated_at: ago(600),
    links: [{target: 'task:t-2', role: 'related', name: null}],
    writers: [{actor: 'human:ana', name: 'Ana'}], versions: [{n: 1, by: 'human:ana', created_at: ago(600)}]},
  {id: CSV, name: 'carts.csv', current_version: 3, updated_at: ago(60), versions: [{n: 3, by: 'bot:writer', created_at: ago(60)}]},
];
// What the server sent back for the writes, in order.
const seen = {links: [] as unknown[], pages: [] as unknown[]};
const setup = async (page: Page) => {
  // Places: the task can be linked to; the pinned list is hidden now (a link is what shows a page).
  await page.route(url => url.pathname.startsWith('/api/v2/places/'), route => route.fulfill({contentType: 'application/json', body: JSON.stringify({can_link: true, pins: []})}));
  // The task's files: with all=1 every page linked to it and the files; without, the files only (the Details tiles).
  await page.route(url => url.pathname === '/api/v2/tasks/t-2/files', route => {
    const all = new URL(route.request().url()).searchParams.get('all') === '1';
    return route.fulfill({contentType: 'application/json', body: JSON.stringify({files: all ? linked() : [linked()[3]]})});
  });
  // The picker's search: the newest pages, the one already on the task left out by the window.
  await page.route(url => url.pathname === '/api/v2/files', route => route.fulfill({contentType: 'application/json',
    body: JSON.stringify({files: [{id: BRIEF, title: 'Saved carts brief'}, {id: OLD, title: 'Old cart notes'}]})}));
  await page.route(url => url.pathname === `/api/v2/pages/${OLD}/links`, (route: Route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    seen.links.push(JSON.parse(route.request().postData() || 'null'));
    return route.fulfill({contentType: 'application/json', body: JSON.stringify({id: OLD, links: []})});
  });
  await page.route(url => url.pathname === '/api/v2/pages', (route: Route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    seen.pages.push(JSON.parse(route.request().postData() || 'null'));
    return route.fulfill({contentType: 'application/json', body: JSON.stringify({id: 'file-new', version: 1})});
  });
};
const tabs = (win: ReturnType<Page['locator']>) => win.getByRole('tab').evaluateAll(els => els.map(e => (e.textContent || '').trim()));

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {viewport: {width: 1280, height: 800}, theme: 'light', hash: '#/task/t-2', tasks: tasks(), needs: [], beforeGoto: setup});
    const win = page.locator('#task-modal[open][data-task="t-2"]');
    await win.locator('.task-chat textarea').waitFor();

    // The Spec pages: two cards under the title, with their titles from the Files list once it is read.
    await win.locator('[data-task-specs] .task-spec-t', {hasText: 'Cart rules'}).waitFor();
    const cards = await win.locator('[data-task-specs] .task-spec').evaluateAll(els => els.map(e => ({
      id: (e as HTMLElement).dataset.taskSpec,
      href: e.getAttribute('href'),
      title: e.querySelector('.task-spec-t')?.textContent?.trim(),
      meta: e.querySelector('.task-spec-m')?.textContent?.trim(),
      dot: !!e.querySelector('.task-spec-dot[aria-label="Changed since OK"]'),
    })));
    assert.deepEqual(cards, [
      {id: SPEC_A, href: `#/page/${SPEC_A}`, title: 'Saved carts', meta: 'v2 · Writer · also the Spec of D-18463', dot: false},
      {id: SPEC_B, href: `#/page/${SPEC_B}`, title: 'Cart rules', meta: 'v1 · Writer', dot: true},
    ], 'two Spec cards: the shared one says so, the changed one has the dot');
    // A Spec is a card, not a tab: the tabs are Chat and Files (its count, once read: four).
    await win.getByRole('tab', {name: 'Files 4'}).waitFor();
    assert.deepEqual(await tabs(win), ['Chat', 'Files 4'], 'no tab for a Spec');

    // The Files tab lists the pages (their roles as chips) and the file.
    await win.getByRole('tab', {name: 'Files 4'}).click();
    await win.locator('.task-files-tab .tft-list .tft-row').first().waitFor();
    assert.equal(await win.locator('.task-chat').isVisible(), false, 'the Files tab shows instead of the chat');
    const rows = await win.locator('.tft-list .tft-row').evaluateAll(els => els.map(e => ({
      kind: e.tagName === 'A' ? 'page' : 'file',
      href: e.getAttribute('href'),
      title: e.querySelector('.tft-t')?.textContent?.trim(),
      meta: e.querySelector('.tft-m')?.textContent?.trim(),
      chips: [...e.querySelectorAll('.tft-chip')].map(c => c.textContent?.trim()),
    })));
    assert.deepEqual(rows.map(r => [r.kind, r.title, r.chips]), [
      ['page', 'Saved carts', ['Spec']],
      ['page', 'Cart rules', ['Spec']],
      ['page', 'Saved carts brief', ['related']],
      ['file', 'carts.csv', []],
    ], 'pages with their roles on this task, and the file');
    assert.match(rows[0]!.meta ?? '', /^v2 · Writer · /, 'a page: v<version> · writers · time');
    assert.equal(rows[2]!.href, `#/page/${BRIEF}`, 'a page row opens the page');
    assert.match(rows[3]!.meta ?? '', /^v3 · /, 'a file: its version and time');
    assert.equal(await win.locator('.task-side :is([data-task-files], [data-tft-list])').count(), 0);

    // Link a page or file: the search leaves out the page already on the task, and the pick links it as related.
    await win.locator('[data-tp-add]').click();
    const picker = win.locator('.task-pages .tp-picker');
    await picker.locator('.tp-result').first().waitFor();
    assert.deepEqual(await picker.locator('.tp-result').evaluateAll(els => els.map(e => e.getAttribute('data-tp-page'))), [OLD],
      'the page already on the task is not offered again');
    await picker.locator(`.tp-result[data-tp-page="${OLD}"]`).click();
    await picker.waitFor({state: 'hidden'});
    assert.deepEqual(seen.links, [{target: 'task:t-2', role: 'related'}], 'linked as related to this task');

    // New page: a title and a text, saved as the task's related page.
    await win.locator('[data-tft-new]').click();
    const dialog = page.locator('dialog.tft-new');
    await dialog.waitFor();
    await dialog.locator('input[name="title"]').fill('Refund rules');
    await dialog.locator('textarea[name="text"]').fill('Refunds within 14 days of purchase.');
    await dialog.locator('button[type="submit"]').click();
    await dialog.waitFor({state: 'detached'});
    assert.deepEqual(seen.pages, [{title: 'Refund rules', text: 'Refunds within 14 days of purchase.', task: 't-2', as: 'related', name: 'refund-rules.html'}],
      'the page is posted as the task\'s related page');

    // A task with no Spec: no cards.
    await page.evaluate(() => { location.hash = '#/task/t-1'; });
    const plain = page.locator('#task-modal[open][data-task="t-1"]');
    await plain.locator('.task-chat textarea').waitFor();
    assert.equal(await plain.locator('[data-task-specs]').count(), 0, 'no Spec, no cards');
    assert.deepEqual(await tabs(plain), ['Chat', 'Files'], 'Chat and Files');

    assert.deepEqual(errors, []);
    await page.close();
    console.log('PASS: a task\'s Spec pages are cards under its title (shared and changed ones marked); the Files tab lists its pages with roles and its files; the picker links a page as related; New page posts the page.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
