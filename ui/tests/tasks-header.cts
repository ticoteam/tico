// The Tasks page's one-row header (spec "Pipeline board in one row", part A, task #404), offline against the Tasks
// page's fake server (tasks-page.cts). Checks, as the spec's acceptance criteria give them:
//  - a 390x844 phone on a pipeline: one 44px row, left to right the picker, Search, Filter, ▶ n and ⋯, nothing filled
//    with the accent and no New task on it; no ▶ beside the stage tabs (AC-1); at 320px with a long pipeline name it is
//    still one row and the name is cut with "…";
//  - a 1440x900 computer: one 56px row, Tasks, the picker, a 280px search, Filter, ▶ n and ⋯ (AC-2);
//  - nothing on the row moves or changes size when Needs you arrives 1.5s after the board (AC-6), and ▶ is as wide
//    with nothing waiting as with "99+";
//  - ⋯ lists New task (with its C hint on a computer), Archived, Edit pipeline and, on a computer on a pipeline, List and
//    Columns with the current one ticked; each does what its old button did; C still opens New task; closing ⋯ leaves it
//    aria-expanded="false" (AC-3); a phone's ⋯ has no List/Columns and no C hint;
//  - the archive's bar has ✕ "Leave Archived" at its left, which returns to the board (AC-14);
//  - ▶ on a pipeline opens the review of its first stage with items; with nothing waiting it says "Nothing waits on you
//    in Product" and opens nothing; on You it walks Needs you, or says "Nothing waits on you" (AC-10);
//  - You shows its count once, in ▶; the picker reads "You" (AC-13);
//  - the phone's search opens over the row with one ✕, which empties it and puts it away (AC-11).
import type {Browser, Page} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, fixtures, task, brief, needsOf, PIPELINES} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;
type Need = import('./tasks-page.cts').Need;
declare var TASKS_ST: import('./tasks-page.cts').TasksState;

const PHONE = {width: 390, height: 844};
const HEAD = '#main header.ph.tl-head';
const ROW = ['#task-scope', '.tl-search', '#task-filter', '#task-play', '#task-manager', '#task-menu'];

// Needs you answers only after `ms`, so the board draws first and the count arrives late.
const needsLate = (ms: number, items: Need[]) => async (page: Page) => {
  await page.route('**/api/v2/needs-you*', async r => {
    await new Promise(res => setTimeout(res, ms));
    await r.fulfill({contentType: 'application/json', body: JSON.stringify({actor: 'human:ana', count: items.length, items, version: String(items.length), next: null})});
  });
};

// Each shown control on the header row: its selector and its box.
function rowBoxes(page: Page) {
  return page.evaluate(([head, sels]) => {
    const h = document.querySelector<HTMLElement>(head)!;
    return sels.map(sel => {
      const el = h.querySelector<HTMLElement>(sel);
      if (!el || !el.getClientRects().length) return null;
      const r = el.getBoundingClientRect();
      return {sel, x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)};
    }).filter(Boolean) as {sel: string; x: number; y: number; w: number; h: number}[];
  }, [HEAD, ROW] as const);
}
// The row's own height (the shared header's 1px rule under it left out), and what on it is wrong for AC-1/AC-2.
function rowProblems(page: Page) {
  return page.evaluate(head => {
    const bad: string[] = [], h = document.querySelector<HTMLElement>(head)!;
    const probe = document.createElement('div'); probe.style.background = 'var(--accent)'; document.body.append(probe);
    const accent = getComputedStyle(probe).backgroundColor; probe.remove();
    const shown = [...h.querySelectorAll<HTMLElement>('button, a, input, .tl-search')].filter(el => el.getClientRects().length && el.getBoundingClientRect().width > 1);
    for (const el of shown) if (getComputedStyle(el).backgroundColor === accent) bad.push(`${el.id || el.className} is filled with the accent`);
    if (h.querySelector('.primary, #task-new, #task-archive, #task-pipeline-edit, #task-layout')) bad.push('an old button is still on the row');
    const mid = (el: Element) => { const r = el.getBoundingClientRect(); return (r.top + r.bottom) / 2; };
    const line = mid(h.querySelector('#task-scope')!);
    for (const el of shown) if (Math.abs(mid(el) - line) > 4) bad.push(`${el.id || el.className} is off the one row`);
    const de = document.documentElement;
    if (de.scrollWidth > de.clientWidth) bad.push(`the page scrolls sideways (${de.scrollWidth} > ${de.clientWidth})`);
    // a tap on each control's centre lands on it, not on a neighbour laid over it
    for (const el of h.querySelectorAll<HTMLElement>('#task-scope, #task-filter, #task-play, #task-manager, #task-menu')) {
      if (!el.getClientRects().length) continue;
      const r = el.getBoundingClientRect(), hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      if (!hit || !el.contains(hit)) bad.push(`a tap on ${el.id} lands on ${hit ? hit.id || hit.className : 'nothing'}`);
    }
    const cs = getComputedStyle(h);
    return {bad, height: h.getBoundingClientRect().height - parseFloat(cs.borderBottomWidth)};
  }, HEAD);
}
const menuItems = (page: Page) => page.locator('#task-filter-pop .tl-mi').evaluateAll(els => els.map(e => {
  const words = e.querySelector('.tl-mi-t')?.textContent || '', k = e.querySelector('.tl-mi-k') ? ' (C)' : '';
  const on = e.getAttribute('aria-checked') === 'true' ? ' ✓' : '';
  return words + k + on;
}));
const toastSays = (page: Page, words: string) => page.locator('.toast', {hasText: words}).first().waitFor({timeout: 5000});

async function phoneRow(browser: Browser) {
  const needs = needsOf(fixtures());
  const {page, errors} = await open(browser, {viewport: PHONE, hash: '#/tasks?pipeline=product', needs, beforeGoto: needsLate(1500, needs)});
  await page.locator('.pv-tab').first().waitFor();
  await page.waitForTimeout(200);
  const before = await rowBoxes(page);
  assert.deepEqual(before.map(b => b.sel), ['#task-scope', '.tl-search', '#task-filter', '#task-play', '#task-menu'], 'picker, Search, Filter, ▶ and ⋯, in that order');
  assert.ok(before.every((b, i) => i === 0 || b.x > before[i - 1]!.x), `left to right: ${JSON.stringify(before)}`);
  await page.waitForFunction(() => document.querySelector('#task-play .tl-review-n')?.textContent === '2', null, {timeout: 10000});
  assert.deepEqual(await rowBoxes(page), before, 'nothing on the row moves when Needs you arrives');
  const {bad, height} = await rowProblems(page);
  assert.deepEqual(bad, []);
  assert.equal(height, 44, 'one 44px row');
  assert.equal(await page.locator('.pv-phone-head .pv-play').count(), 0, 'no ▶ beside the stage tabs: the header\'s ▶ opens them');
  assert.equal(await page.locator('#task-play').getAttribute('aria-label'), 'Review: 2 wait on you');
  // ⋯ on a phone: no List/Columns, no C hint.
  await page.locator('#task-menu').click();
  assert.deepEqual(await menuItems(page), ['New task', 'Archived', 'Edit pipeline']);
  await page.keyboard.press('Escape');
  // Search: opens over the row with one ✕; ✕ empties it and puts it away.
  await page.locator('.tl-search').click();
  await page.keyboard.type('Checkout');
  await page.waitForFunction(() => TASKS_ST.q === 'Checkout');
  const x = page.locator('.tl-search-x');
  assert.equal(await x.isVisible(), true, 'the open search has its ✕');
  const over = await page.evaluate(h => { const s = document.querySelector('.tl-search')!.getBoundingClientRect(), r = document.querySelector(h)!.getBoundingClientRect();
    return Math.round(s.width) >= Math.round(r.width) - 1; }, HEAD);
  assert.ok(over, 'it opens over the whole row');
  await x.click();
  await page.waitForFunction(() => TASKS_ST.q === '' && document.activeElement?.id !== 'task-q');
  assert.equal(await page.locator('#task-q').inputValue(), '');
  assert.equal(await x.isVisible(), false, 'put away, the ✕ goes with it');
  assert.deepEqual(errors, []);
  await page.close();

  // 320px, a long name: still one row, the name ends in "…".
  const long = async (p: Page) => {
    await p.route('**/api/v2/pipelines', r => r.fulfill({contentType: 'application/json',
      body: JSON.stringify({pipelines: PIPELINES.map(x => x.id === 'product' ? {...x, name: 'Product discovery and customer research'} : x)})}));
  };
  const small = await open(browser, {viewport: {width: 320, height: 700}, hash: '#/tasks?pipeline=product', beforeGoto: long});
  await small.page.locator('.pv-tab').first().waitFor();
  await small.page.waitForTimeout(200);
  const r = await rowProblems(small.page);
  assert.deepEqual(r.bad, []);
  assert.equal(r.height, 44, 'one 44px row at 320px');
  const cut = await small.page.locator('#task-scope .tl-scope-l').evaluate(e => getComputedStyle(e).textOverflow === 'ellipsis' && e.scrollWidth > e.clientWidth);
  assert.ok(cut, 'the long name is cut with "…"');
  assert.deepEqual(small.errors, []);
  await small.page.close();
  console.log('a phone: one 44px row (picker, Search, Filter, ▶ n, ⋯), still at 320px; nothing moves; search ✕: ok');
}

async function computerRow(browser: Browser) {
  const needs = needsOf(fixtures());
  const {page, errors} = await open(browser, {hash: '#/tasks?pipeline=product&layout=columns', needs, beforeGoto: needsLate(1500, needs)});
  await page.locator('.pv-col').first().waitFor();
  await page.waitForTimeout(200);
  const before = await rowBoxes(page);
  assert.deepEqual(before.map(b => b.sel), ['#task-scope', '.tl-search', '#task-filter', '#task-play', '#task-menu'], 'the fixture\'s pipeline names no manager: no face');
  assert.equal(before.find(b => b.sel === '.tl-search')!.w, 280, 'a 280px search');
  await page.waitForFunction(() => document.querySelector('#task-play .tl-review-n')?.textContent === '2', null, {timeout: 10000});
  assert.deepEqual(await rowBoxes(page), before, 'nothing on the row moves when Needs you arrives');
  const {bad, height} = await rowProblems(page);
  assert.deepEqual(bad, []);
  assert.equal(height, 56, 'one 56px row');
  assert.equal(await page.locator(`${HEAD} > h1.ph-t`).innerText(), 'Tasks');

  // ⋯: what moved there, the current layout ticked; Escape closes it back to ⋯, no longer expanded.
  const more = page.locator('#task-menu');
  await more.click();
  assert.equal(await more.getAttribute('aria-expanded'), 'true');
  assert.deepEqual(await menuItems(page), ['New task (C)', 'Archived', 'Edit pipeline', 'List', 'Columns ✓']);
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => document.querySelector('#task-menu')?.getAttribute('aria-expanded') === 'false');
  assert.equal(await page.evaluate(() => document.activeElement?.id), 'task-menu', 'focus back on ⋯');
  // List
  await more.click();
  await page.locator('#task-filter-pop [data-tm-layout="list"]').click();
  await page.waitForFunction(() => TASKS_ST.layout === 'list' && !!document.querySelector('.pv-group-head'));
  assert.equal(await more.getAttribute('aria-expanded'), 'false', 'a pick leaves ⋯ not expanded');
  // Archived, then ✕ in the bar.
  await more.click();
  await page.locator('#task-filter-pop [data-tm="archive"]').click();
  await page.waitForFunction(() => TASKS_ST.archive === 'snoozed' && !TASKS_ST.loading);
  const bar = await page.locator('#task-bar > :not([hidden])').evaluateAll(els => els.map(e => e.id || e.className));
  assert.equal(bar[0], 'task-archive-x', `✕ "Leave Archived" first in the bar: ${bar.join(', ')}`);
  assert.equal(await page.locator('#task-archive-x').getAttribute('aria-label'), 'Leave Archived');
  await more.click();
  assert.deepEqual(await menuItems(page), ['New task (C)', 'Leave Archived'], 'in the archive: no pipeline items');
  await page.keyboard.press('Escape');
  await page.locator('#task-archive-x').click();
  await page.waitForFunction(() => !TASKS_ST.archive && !TASKS_ST.loading);
  assert.equal(await page.locator('#task-archive-x').isVisible(), false);
  // New task from ⋯, and from C.
  await more.click();
  await page.locator('#task-filter-pop [data-tm="new"]').click();
  await page.locator('#task-create-form').waitFor();
  await page.keyboard.press('Escape');
  await page.locator('#task-create-form').waitFor({state: 'hidden'});
  await page.locator('#task-body').click({position: {x: 5, y: 5}});
  await page.keyboard.press('c');
  await page.locator('#task-create-form').waitFor();
  assert.deepEqual(errors, []);
  await page.close();
  console.log('a computer: one 56px row (Tasks, picker, 280px search, Filter, ▶ n, ⋯); ⋯ does what the old buttons did; C: ok');
}

async function review(browser: Browser) {
  // On a pipeline with items: its first stage with items.
  {
    const {page, errors} = await open(browser, {hash: '#/tasks?pipeline=product&layout=columns'});
    await page.waitForFunction(() => document.querySelector('#task-play .tl-review-n')?.textContent === '2');
    await page.locator('#task-play').click();
    await page.waitForFunction(() => /^Spec · 1 of 2$/.test(document.querySelector('#need-card .dc-count')?.textContent || ''));
    assert.deepEqual(errors, []);
    await page.close();
  }
  // On a pipeline with nothing waiting on it, though Needs you holds another pipeline's item: quiet, says so, opens nothing.
  {
    const list = fixtures(), needs = needsOf(list).filter(n => n.task_id === 't-3');
    const {page, errors} = await open(browser, {hash: '#/tasks?pipeline=product&layout=columns', tasks: list, needs});
    await page.locator('.pv-col').first().waitFor();
    // Needs you is in: the app's own badge counts the other pipeline's item
    await page.waitForFunction(() => [...document.querySelectorAll('[data-needs-badge]')].some(b => b.textContent === '1'));
    assert.equal(await page.locator('#task-play').evaluate(e => e.classList.contains('none')), true, 'the quiet look at 0');
    assert.equal(await page.locator('#task-play').getAttribute('aria-label'), 'Review: nothing waits on you');
    await page.locator('#task-play').click();
    await toastSays(page, 'Nothing waits on you in Product');
    assert.equal(await page.locator('#need-card[open]').count(), 0, 'it never walks another pipeline\'s items');
    assert.deepEqual(errors, []);
    await page.close();
  }
  // You: the count once, in ▶; ▶ walks all of Needs you.
  {
    const {page, errors} = await open(browser, {hash: '#/tasks?pipeline='});
    await page.waitForFunction(() => document.querySelector('#task-play .tl-review-n')?.textContent === '3');
    assert.equal((await page.locator('#task-scope').innerText()).trim(), 'You', 'the picker reads "You", no count');
    await page.locator('#task-play').click();
    await page.waitForFunction(() => /^1 of 3$/.test(document.querySelector('#need-card .dc-count')?.textContent || ''));
    assert.deepEqual(errors, []);
    await page.close();
  }
  // You with nothing: says so. ▶ is as wide at 0 as with "99+".
  let empty = 0;
  {
    const {page, errors} = await open(browser, {hash: '#/tasks?pipeline=', needs: []});
    await page.locator('#task-play').waitFor();
    await page.waitForTimeout(300);
    empty = await page.locator('#task-play').evaluate(e => e.getBoundingClientRect().width);
    await page.locator('#task-play').click();
    await toastSays(page, 'Nothing waits on you');
    assert.deepEqual(errors, []);
    await page.close();
  }
  {
    const t1 = brief(task('t-1', 'Checkout flow', 'spec'));
    const many: Need[] = Array.from({length: 120}, (_, i) => ({kind: 'task', id: `task:m-${i}`, task_id: 't-1', line: 'Checkout flow', since: new Date().toISOString(), task: t1}));
    const {page, errors} = await open(browser, {hash: '#/tasks?pipeline=', needs: many});
    await page.waitForFunction(() => document.querySelector('#task-play .tl-review-n')?.textContent === '99+');
    const full = await page.locator('#task-play').evaluate(e => e.getBoundingClientRect().width);
    assert.equal(full, empty, `▶ keeps one width: ${empty}px empty, ${full}px at 99+`);
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log('▶ Review: the pipeline\'s first stage with items, quiet at 0 and says so; You walks all, its count once; one width: ok');
}

if (require.main === module) (async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const run of [phoneRow, computerRow, review]) await run(browser);
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
