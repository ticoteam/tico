// The Tasks header as one row (ui/app/tasks-page.ts, ui/styles/task-list.css; spec "Pipeline board: one-row header, one
// count per stage, a floating face", part A), offline on the fake server of tasks-page.cts. Checks:
//  - a phone (390×844) on a pipeline: one 44 px row of the picker, Search, Filter, Flow n and ⋯, nothing accent, the title for
//    screen readers only (the UI Owner's call; page-head.cts); at 320 px with a long pipeline name it is still one row
//    and the name ends in "…" (AC-1);
//  - a computer (1440×900 with the manager's chat docked open, and 1000×800 and 900×800 with it folded to its tab): one
//    56 px row of Tasks, the picker, the search, Filter, Flow n, List/Columns, New task and ⋯, nothing sideways; no chat
//    button in the row: the manager is the page chat, docked open or the tab on the right edge (page-chat.ts, style guide
//    "Where chat lives") (AC-2);
//  - List/Columns and New task sit on the row whenever there is room: the search shrinks to its icon first (900), New
//    task goes into ⋯ only on the phone's row (Chris, round 2 C);
//  - ⋯ holds Archived and Edit pipeline on a computer, and New task too on a phone; each does what its button did; C
//    still makes a task; closed, ⋯ is aria-expanded="false" (AC-3);
//  - Flow on a pipeline walks the pipeline; with nothing waiting it is quiet, keeps its width and says
//    "Nothing waits on you in Product" without walking anything (AC-10);
//  - nothing on the row moves when Needs you arrives 2 s late (AC-6, the header's part);
//  - the phone search opens over the row with one ✕, which empties it and closes it (AC-11, in a browser);
//  - Tasks › You shows its count once, in Flow, and the picker reads "You" (AC-13).
// Run: node scripts/ui-tests.cts tasks-header
import type {Page} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, PIPELINES} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;

const PHONE = {width: 390, height: 844};
const box = (page: Page, sel: string) => page.locator(sel).first().evaluate(el => el.getBoundingClientRect().toJSON() as DOMRect);
// Every control shown on the header's row, with its box: the picker, the tools and ⋯.
const rowControls = (page: Page) => page.evaluate(() => {
  const h = document.querySelector<HTMLElement>('#main header.ph')!;
  return [...h.querySelectorAll<HTMLElement>(':scope > #task-scope, :scope > .ph-tools > *, :scope > .ph-act > *, :scope > .ph-more')]
    // shown: a field folded out of sight (the narrowest phone's search, 1 px and clipped) is not on the row
    .filter(el => el.getClientRects().length && el.getBoundingClientRect().width > 1)
    .map(el => { const r = el.getBoundingClientRect(); return {id: el.id || el.className, top: r.top, bottom: r.bottom, left: r.left, right: r.right, w: r.width, h: r.height}; });
});
// The header's problems with being one row: a control off the line, a filled (accent) button, anything sideways.
async function oneRow(page: Page, height: number): Promise<string[]> {
  const bad: string[] = [];
  const h = await box(page, '#main header.ph');
  // the row, above the shared header's 1 px line
  const line = await page.locator('#main header.ph').evaluate(el => parseFloat(getComputedStyle(el).borderBottomWidth));
  if (Math.abs(h.height - line - height) > 0.5 && Math.abs(h.height - height) > 0.5) bad.push(`the header's row is ${h.height - line}px high, not ${height}`);
  const controls = await rowControls(page), mid = (c: {top: number; bottom: number}) => (c.top + c.bottom) / 2;
  const centre = mid(controls[0]!);
  for (const c of controls) {
    if (Math.abs(mid(c) - centre) > 4) bad.push(`${c.id} is off the row`);
    if (c.right > h.right + 0.5) bad.push(`${c.id} runs past the header's right edge`);
  }
  // each control takes a tap at its centre: nothing (the folded search's field) lies over it
  const covered = await page.evaluate(() => [...document.querySelectorAll<HTMLElement>('#main header.ph > #task-scope, #main header.ph > .ph-tools > *, #main header.ph > .ph-more')]
    .filter(el => el.getClientRects().length && el.getBoundingClientRect().width > 1).filter(el => {
      const r = el.getBoundingClientRect(), at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return !at || !el.contains(at);
    }).map(el => el.id || el.className));
  if (covered.length) bad.push(`covered at their centre: ${covered.join(', ')}`);
  if (await page.locator('#main header.ph .primary').count()) bad.push('a filled button on the row');
  const accent = await page.evaluate(() => {
    const probe = document.createElement('i'); probe.style.color = 'var(--accent)'; document.body.append(probe);
    const want = getComputedStyle(probe).color; probe.remove();
    return [...document.querySelectorAll<HTMLElement>('#main header.ph *')].filter(el => el.getClientRects().length
      && [getComputedStyle(el).backgroundColor, getComputedStyle(el).borderColor].includes(want)).map(el => el.id || el.className);
  });
  if (accent.length) bad.push(`accent on ${accent.join(', ')}`);
  const sideways = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  if (sideways > 0) bad.push(`the page scrolls sideways by ${sideways}px`);
  return bad;
}
const menuItems = (page: Page) => page.locator('#task-menu-pop:popover-open :is(button,a):visible').evaluateAll(els =>
  els.map(e => (e.getAttribute('aria-pressed') === 'true' ? '✓ ' : '') + (e.textContent || '').trim()));
async function menu(page: Page): Promise<string[]> {
  await page.locator('#task-menu').click();
  await page.locator('#task-menu-pop:popover-open').waitFor();
  return menuItems(page);
}

(async () => {
  const product = PIPELINES.find(p => p.id === 'product')!;
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    {
      // A phone on Product: one 44 px row; Spec's two asks are Flow 2. A board with no bot has no face on it.
      const {page, errors} = await open(browser, {viewport: PHONE, hash: '#/tasks?pipeline=product'});
      await page.locator('#task-body .pv-phone').waitFor();
      await page.waitForFunction(() => document.querySelector('#task-play .tl-review-n')?.textContent === '2');
      assert.deepEqual(await oneRow(page, 44), []);
      assert.deepEqual((await rowControls(page)).map(c => c.id), ['task-scope', 'tl-search', 'task-filter', 'task-play', 'task-menu'],
        'the picker, Search, Filter, Flow and ⋯, in that order');
      assert.equal(await page.locator('#task-play').getAttribute('aria-label'), 'Flow, 2');
      // ⋯ on a phone: New task, Archived, Edit pipeline; no List/Columns (a phone shows one stage at a time).
      assert.deepEqual(await menu(page), ['New task', 'Archived', 'Edit pipeline']);
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => document.querySelector('#task-menu')?.getAttribute('aria-expanded') === 'false');

      // Search: tapped, it opens over the row with one ✕; ✕ empties it, shows every task again and closes it.
      const rows = await page.locator('#task-body .pv-phone [data-task]').count();
      await page.locator('#task-q').click();
      const field = await box(page, '#main header.ph .tl-search'), row = await box(page, '#main header.ph');
      assert.ok(Math.abs(field.left - row.left) < 1 && Math.abs(field.right - row.right) < 1 && Math.abs(field.height - 44) < 1,
        `the search opens over the whole row: ${JSON.stringify({field, row})}`);
      await page.locator('#task-q').fill('Checkout');
      await page.waitForFunction(() => document.querySelectorAll('#task-body .pv-phone [data-task]').length === 1);
      assert.equal(await page.locator('#main header.ph .tl-search-x').isVisible(), true, 'its ✕');
      await page.locator('#main header.ph .tl-search-x').click();
      await page.waitForFunction(n => document.querySelectorAll('#task-body .pv-phone [data-task]').length === n, rows);
      assert.equal(await page.locator('#task-q').inputValue(), '');
      assert.equal(await page.locator('#task-q').evaluate(el => el === document.activeElement), false, 'closed');
      assert.equal(await page.locator('#main header.ph .tl-search-x').isVisible(), false);

      // Flow walks the pipeline.
      await page.locator('#task-body .pv-tab[data-pv-tab="spec"]').click();
      await page.locator('#task-play').click();
      await page.waitForFunction(() => document.querySelector('#need-card .dc-crumb')?.textContent === 'All mine/Product');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // 320 px, the tightest: a long pipeline name on a pipeline with a manager, so the board's face is on the row too.
      // Still one row; the search's icon folds into ⋯ first, so the name keeps at least eight letters before its "…";
      // ⋯'s Search opens the search over the row.
      const name = product.name;
      (product as {name: string}).name = 'Product launches and everything around them';
      (product as {manager_id: string | null}).manager_id = 'bot:pm';
      try {
        const {page, errors} = await open(browser, {viewport: {width: 320, height: 640}, hash: '#/tasks?pipeline=product'});
        await page.locator('#task-body .pv-phone').waitFor();
        await page.locator('#task-manager').waitFor();
        assert.deepEqual(await oneRow(page, 44), []);
        assert.deepEqual((await rowControls(page)).map(c => c.id), ['task-scope', 'task-filter', 'task-play', 'task-manager', 'task-menu'],
          'the picker, Filter, Flow, the face and ⋯; the search is in ⋯');
        const cut = await page.locator('#task-scope .tl-scope-l').evaluate(el => {
          const range = document.createRange(), text = el.firstChild!;
          range.setStart(text, 0); range.setEnd(text, 8);
          return {over: el.scrollWidth > el.clientWidth, how: getComputedStyle(el).textOverflow, eight: el.clientWidth >= range.getBoundingClientRect().width};
        });
        assert.deepEqual(cut, {over: true, how: 'ellipsis', eight: true}, 'the name ends in …, after at least eight letters');
        assert.deepEqual(await menu(page), ['Search', 'New task', 'Archived', 'Edit pipeline']);
        await page.locator('#task-mi-search').click();
        await page.waitForFunction(() => document.activeElement?.id === 'task-q');
        const field = await box(page, '#main header.ph .tl-search'), row = await box(page, '#main header.ph');
        assert.ok(Math.abs(field.left - row.left) < 1 && Math.abs(field.right - row.right) < 1 && Math.abs(field.height - 44) < 1,
          `Search opens over the whole row: ${JSON.stringify({field, row})}`);
        assert.deepEqual(errors, []);
        await page.close();
      } finally { (product as {name: string}).name = name; (product as {manager_id: string | null}).manager_id = null; }
    }
    {
      // A 700 px window (a small tablet, a split screen) gets the phone's row while its pane is wider than the narrow-pane
      // rule's: the folded search's field stays its 44 px icon, not the shared header's 160 px search minimum.
      const {page, errors} = await open(browser, {viewport: {width: 700, height: 900}, hash: '#/tasks?pipeline=product'});
      await page.locator('#task-body .pv-phone').waitFor();
      assert.deepEqual(await oneRow(page, 44), []);
      const field = await box(page, '#task-q'), icon = await box(page, '#main header.ph .tl-search');
      assert.ok(Math.abs(field.width - icon.width) < 0.5 && Math.abs(icon.width - 44) < 0.5, `the folded field is the 44 px icon: ${field.width} in ${icon.width}`);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Nothing waits on the person: Flow is quiet, keeps its width, and a tap says so and walks nothing.
      const {page, errors} = await open(browser, {viewport: PHONE, needs: [], hash: '#/tasks?pipeline=product'});
      await page.locator('#task-body .pv-phone').waitFor();
      const play = page.locator('#task-play');
      assert.equal(await play.isVisible(), true, 'Flow stays on the row');
      assert.equal(await play.evaluate(el => el.classList.contains('none')), true);
      assert.equal(await play.getAttribute('aria-label'), 'Flow');
      // One width with or without a count, up to "99+".
      const quiet = (await box(page, '#task-play')).width;
      const widths = await page.locator('#task-play').evaluate(el => ['1', '12', '99', '99+'].map(n => {
        el.classList.remove('none'); el.querySelector('.tl-review-n')!.textContent = n;
        return el.getBoundingClientRect().width;
      }));
      assert.ok(widths.every(w => Math.abs(w - quiet) < 0.1), `Flow is ${quiet}px at 0 and ${widths.join(', ')} with 1, 12, 99, 99+`);
      await page.locator('#task-play').evaluate(el => { el.classList.add('none'); el.querySelector('.tl-review-n')!.textContent = ''; });
      await play.click();
      await page.locator('.toast', {hasText: 'Nothing waits on you in Product'}).waitFor();
      assert.equal(await page.locator('#need-card[open]').count(), 0, 'no review opens');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A computer on Product with a manager: one 56 px row, the manager's chat docked open, List/Columns and New task on
      // the row; nothing moves when Needs you arrives 2 s after the board draws.
      (product as {manager_id: string | null}).manager_id = 'bot:pm';
      try {
        const late = async (pg: Page) => pg.route('**/api/v2/needs-you*', async route => { await new Promise(r => setTimeout(r, 2000)); await route.fallback(); });
        const {page, errors} = await open(browser, {hash: '#/tasks?pipeline=product', beforeGoto: late});
        await page.locator('#task-body .pv').waitFor();
        const before = await rowControls(page);
        await page.waitForFunction(() => document.querySelector('#task-play .tl-review-n')?.textContent === '2', null, {timeout: 10_000});
        const after = await rowControls(page);
        assert.deepEqual(after.map(c => [c.id, Math.round(c.left), Math.round(c.w)]), before.map(c => [c.id, Math.round(c.left), Math.round(c.w)]),
          'no header control moves or changes size when Needs you arrives');
        assert.deepEqual(await oneRow(page, 56), []);
        assert.deepEqual(after.map(c => c.id), ['task-scope', 'tl-search', 'task-filter', 'task-play', 'task-mi-layout', 'task-new', 'task-menu']);
        const searchW = (await box(page, '#main header.ph .tl-search')).width;
        assert.ok(Math.abs(searchW - 280) < 1, `a 280 px search: ${searchW}`);
        // The manager's chat is docked open at 1440 (Chris, round 2 A); closed, the tab on the right edge brings it back.
        await page.locator('aside.pgc:not([hidden]) .pgc-head').waitFor();
        await page.locator('aside.pgc [data-pgc-close]').click();
        await page.waitForFunction(() => document.querySelector<HTMLElement>('aside.pgc')!.hidden);
        assert.equal(await page.locator('body > .pgc-edge:not([hidden]) .pgc-tab').getAttribute('aria-label'), 'Chat with PM');

        // List/Columns on the row, the current one pressed; ⋯ keeps Archived and Edit pipeline.
        const pressed = () => page.locator('#task-mi-layout [aria-pressed="true"]').getAttribute('data-layout');
        assert.equal(await pressed(), 'list');
        assert.deepEqual(await menu(page), ['Archived', 'Edit pipeline']);
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => document.querySelector('#task-menu')?.getAttribute('aria-expanded') === 'false');
        await page.locator('#task-mi-layout [data-layout="columns"]').click();
        await page.waitForFunction(() => location.hash.includes('layout=columns'));
        assert.equal(await pressed(), 'columns');
        await page.locator('#task-menu').click();
        await page.locator('#task-menu-pop:popover-open').waitFor();
        await page.locator('#task-mi-archive').click();
        await page.waitForFunction(() => location.hash.includes('archive=snoozed'));
        assert.equal(await page.locator('#task-bar #task-archive-x').isVisible(), true, '✕ Leave Archived at the bar\'s left');
        assert.deepEqual(await menu(page), ['Leave Archived']);
        await page.locator('#task-mi-archive').click();
        await page.waitForFunction(() => !location.hash.includes('archive='));
        await page.locator('#task-new').click();
        await page.locator('#task-create-form').waitFor();
        await page.keyboard.press('Escape');
        await page.locator('#task-create-form').waitFor({state: 'hidden', timeout: 5000});
        // C still makes a task.
        await page.locator('#task-scope').focus();
        await page.keyboard.press('c');
        await page.locator('#task-create-form').waitFor();
        assert.deepEqual(errors, []);
        await page.close();

        // 1000×800 with the manager's chat folded to its tab: still one row, nothing sideways, and the tab covers none
        // of the row (it has its own gutter on the right edge, past ⋯). Open, the chat lies over the page by design.
        const small = await open(browser, {viewport: {width: 1000, height: 800}, hash: '#/tasks?pipeline=product&layout=columns'});
        await small.page.locator('#task-body .pv').waitFor();
        await small.page.locator('body > .pgc-edge:not([hidden]) .pgc-tab').waitFor();
        await small.page.waitForTimeout(100);
        assert.deepEqual(await oneRow(small.page, 56), []);
        const edge = await box(small.page, 'body > .pgc-edge'), more = await box(small.page, '#task-menu');
        assert.ok(more.right <= edge.left, `⋯ ends before the tab's gutter: ${JSON.stringify({more, edge})}`);
        assert.deepEqual((await rowControls(small.page)).map(c => c.id),
          ['task-scope', 'tl-search', 'task-filter', 'task-play', 'task-mi-layout', 'task-new', 'task-menu']);
        assert.deepEqual(small.errors, []);
        await small.page.close();

        // 900×800, narrow: the search has shrunk to its icon, List/Columns and New task are still on the row.
        const narrow = await open(browser, {viewport: {width: 900, height: 800}, hash: '#/tasks?pipeline=product'});
        await narrow.page.locator('#task-body .pv').waitFor();
        await narrow.page.locator('body > .pgc-edge:not([hidden]) .pgc-tab').waitFor();
        assert.deepEqual(await oneRow(narrow.page, 56), []);
        assert.deepEqual((await rowControls(narrow.page)).map(c => c.id),
          ['task-scope', 'tl-search', 'task-filter', 'task-play', 'task-mi-layout', 'task-new', 'task-menu']);
        const icon = (await box(narrow.page, '#main header.ph .tl-search')).width;
        assert.ok(icon < 50, `the search folds to its icon first: ${icon}px`);
        assert.deepEqual(await menu(narrow.page), ['Archived', 'Edit pipeline']);
        assert.deepEqual(narrow.errors, []);
        await narrow.page.close();
      } finally { (product as {manager_id: string | null}).manager_id = null; }
    }
    {
      // Tasks › You: the count once, in Flow; the picker reads "You". No List/Columns off a pipeline; New task on the row.
      const {page, errors} = await open(browser, {hash: '#/tasks?pipeline='});
      await page.waitForFunction(() => document.querySelector('#task-play .tl-review-n')?.textContent === '3');
      assert.equal((await page.locator('#task-scope').innerText()).trim(), 'You');
      assert.equal(await page.locator('#task-mi-layout').isVisible(), false);
      assert.equal(await page.locator('#task-new').isVisible(), true);
      assert.deepEqual(await menu(page), ['Archived']);
      await page.keyboard.press('Escape');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('the Tasks header: one row on a phone and a computer, ⋯, Flow, search ✕, You\'s one count: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
