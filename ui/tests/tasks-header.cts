// The Tasks header as one row (ui/app/tasks-page.ts, ui/styles/task-list.css; spec "Pipeline board: one-row header, one
// count per stage, a floating face", part A), offline on the fake server of tasks-page.cts. Checks:
//  - a phone (390×844) on a pipeline: one 44 px row of the picker, Search, Filter, ▶ n and ⋯, nothing accent; at 320 px
//    with a long pipeline name it is still one row and the name ends in "…" (AC-1);
//  - a computer (1440×900, and 1000×800 with the manager's panel open): one 56 px row of Tasks, the picker, a 280 px
//    search, Filter, ▶ n, the manager's 20 px face and ⋯, nothing sideways; the face opens and closes the panel (AC-2);
//  - ⋯ holds New task, Archived, Edit pipeline and, on a computer on a pipeline, List and Columns with the current one
//    ticked; each does what its button did; C still makes a task; closed, ⋯ is aria-expanded="false" (AC-3);
//  - ▶ on a pipeline opens the review on the open stage; with nothing waiting it is quiet, keeps its width and says
//    "Nothing waits on you in Product" without walking anything (AC-10);
//  - nothing on the row moves when Needs you arrives 2 s late (AC-6, the header's part);
//  - the phone search opens over the row with one ✕, which empties it and closes it (AC-11, in a browser);
//  - Tasks › You shows its count once, in ▶, and the picker reads "You" (AC-13).
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
  return [...h.querySelectorAll<HTMLElement>(':scope > #task-scope, :scope > .ph-tools > *, :scope > .ph-more, :scope > .ph-act *')]
    .filter(el => el.getClientRects().length)
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
    .filter(el => el.getClientRects().length).filter(el => {
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
  els.map(e => (e.getAttribute('aria-checked') === 'true' ? '✓ ' : '') + (e.textContent || '').trim()));
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
      // A phone on Product: one 44 px row; Spec's two asks are ▶ 2.
      const {page, errors} = await open(browser, {viewport: PHONE, hash: '#/tasks?pipeline=product'});
      await page.locator('#task-body .pv-phone').waitFor();
      await page.waitForFunction(() => document.querySelector('#task-play .tl-review-n')?.textContent === '2');
      assert.deepEqual(await oneRow(page, 44), []);
      assert.deepEqual((await rowControls(page)).map(c => c.id), ['task-scope', 'tl-search', 'task-filter', 'task-play', 'task-menu'],
        'the picker, Search, Filter, ▶ and ⋯, in that order');
      assert.equal(await page.locator('#task-play').getAttribute('aria-label'), 'Review: 2 wait on you');
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

      // ▶ opens the review of the open stage.
      await page.locator('#task-body .pv-tab[data-pv-tab="spec"]').click();
      await page.locator('#task-play').click();
      await page.waitForFunction(() => /^Spec · 1 of 2$/.test(document.querySelector('#need-card .dc-count')?.textContent || ''));
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // 320 px and a long pipeline name: still one row, the name cut with "…".
      const name = product.name;
      (product as {name: string}).name = 'Product launches and everything around them';
      try {
        const {page, errors} = await open(browser, {viewport: {width: 320, height: 640}, hash: '#/tasks?pipeline=product'});
        await page.locator('#task-body .pv-phone').waitFor();
        assert.deepEqual(await oneRow(page, 44), []);
        const cut = await page.locator('#task-scope .tl-scope-l').evaluate(el => ({over: el.scrollWidth > el.clientWidth, how: getComputedStyle(el).textOverflow}));
        assert.deepEqual(cut, {over: true, how: 'ellipsis'}, 'the name ends in …');
        assert.deepEqual(errors, []);
        await page.close();
      } finally { (product as {name: string}).name = name; }
    }
    {
      // Nothing waits on the person: ▶ is quiet, keeps its width, and a tap says so and walks nothing.
      const {page, errors} = await open(browser, {viewport: PHONE, needs: [], hash: '#/tasks?pipeline=product'});
      await page.locator('#task-body .pv-phone').waitFor();
      const play = page.locator('#task-play');
      assert.equal(await play.isVisible(), true, '▶ stays on the row');
      assert.equal(await play.evaluate(el => el.classList.contains('none')), true);
      assert.equal(await play.getAttribute('aria-label'), 'Review: nothing waits on you');
      assert.ok((await box(page, '#task-play')).width >= 60, 'one width with or without a count');
      await play.click();
      await page.locator('.toast', {hasText: 'Nothing waits on you in Product'}).waitFor();
      assert.equal(await page.locator('#need-card[open]').count(), 0, 'no review opens');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A computer on Product with a manager: one 56 px row, the manager's face, ⋯ with List/Columns; nothing moves when
      // Needs you arrives 2 s after the board draws.
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
        assert.deepEqual(after.map(c => c.id), ['task-scope', 'tl-search', 'task-filter', 'task-play', 'task-manager', 'task-menu']);
        const searchW = (await box(page, '#main header.ph .tl-search')).width;
        assert.ok(Math.abs(searchW - 280) < 1, `a 280 px search: ${searchW}`);
        assert.equal(await page.locator('#task-manager').getAttribute('aria-label'), 'Chat with PM');
        await page.locator('#task-manager').click();
        await page.locator('#task-rail .pm-head').waitFor();
        await page.locator('#task-manager').click();
        await page.waitForFunction(() => document.querySelector<HTMLElement>('#task-rail')!.hidden);

        // ⋯: each item does what its button did.
        assert.deepEqual(await menu(page), ['New task', 'Archived', 'Edit pipeline', '✓ List', 'Columns']);
        await page.locator('#task-menu-pop [data-layout="columns"]').click();
        await page.waitForFunction(() => location.hash.includes('layout=columns'));
        await page.waitForFunction(() => document.querySelector('#task-menu')?.getAttribute('aria-expanded') === 'false');   // a pick closes ⋯
        assert.deepEqual(await menu(page), ['New task', 'Archived', 'Edit pipeline', 'List', '✓ Columns']);
        await page.locator('#task-mi-archive').click();
        await page.waitForFunction(() => location.hash.includes('archive=snoozed'));
        assert.equal(await page.locator('#task-bar #task-archive-x').isVisible(), true, '✕ Leave Archived at the bar\'s left');
        assert.deepEqual(await menu(page), ['New task', 'Leave Archived']);
        await page.locator('#task-mi-archive').click();
        await page.waitForFunction(() => !location.hash.includes('archive='));
        await page.locator('#task-menu').click();
        await page.locator('#task-mi-new').click();
        await page.locator('#task-create-form').waitFor();
        await page.keyboard.press('Escape');
        await page.locator('#task-create-form').waitFor({state: 'hidden', timeout: 5000});
        // C still makes a task.
        await page.locator('#task-scope').focus();
        await page.keyboard.press('c');
        await page.locator('#task-create-form').waitFor();
        assert.deepEqual(errors, []);
        await page.close();

        // 1000×800 with the manager's panel open: still one row, nothing sideways.
        const small = await open(browser, {viewport: {width: 1000, height: 800}, hash: '#/tasks?pipeline=product&layout=columns'});
        await small.page.locator('#task-body .pv').waitFor();
        await small.page.locator('#task-manager').click();
        await small.page.locator('#task-rail .pm-head').waitFor();
        await small.page.waitForTimeout(100);
        assert.deepEqual(await oneRow(small.page, 56), []);
        assert.deepEqual(small.errors, []);
        await small.page.close();
      } finally { (product as {manager_id: string | null}).manager_id = null; }
    }
    {
      // Tasks › You: the count once, in ▶; the picker reads "You". ⋯ has no List/Columns off a pipeline.
      const {page, errors} = await open(browser, {hash: '#/tasks?pipeline='});
      await page.waitForFunction(() => document.querySelector('#task-play .tl-review-n')?.textContent === '3');
      assert.equal((await page.locator('#task-scope').innerText()).trim(), 'You');
      assert.deepEqual(await menu(page), ['New task', 'Archived']);
      await page.keyboard.press('Escape');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('the Tasks header: one row on a phone and a computer, ⋯, ▶ Review, search ✕, You\'s one count: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
