// Run with Playwright available: NODE_PATH=/path/to/node_modules node ui/tests/task-phone-board.cjs
// The Tasks board on a phone, 390 px wide, light and dark, for a pipeline and for General: the header's one row (the
// picker, ⋯, ✎, the run button and +) with ✎ a 44 px target, the stage chips and their edge fades, one stage's cards with
// the swipe hint under the last card (none on an empty stage), the activity bar's manager name read from Hub and its
// "ask:" line, and General's chips in its Hub step order.
import type {Browser, Page} from 'playwright';
import type * as PipelineFixtureSupport from './support/pipeline-fixture.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {install, type: devType} = require('./support/pipeline-fixture.cts') as PipelineFixtureSupport.Api;
const {t} = (() => { try { return require('./support/load.cjs') as {t: (ms: number) => number}; } catch { return {t: (ms: number) => ms}; } })();

// Page globals from the Tasks app (the page defines them; this spec reads and pokes them).
declare const S: {emps: Record<string, unknown>[]};
declare const TASKS_ST: unknown;
declare function tasksRender(state: unknown): void;

const HOST = 'http://tico-ui.test';
const GENERAL = {id: 'general', name: 'General', role: null, manager: null, has_requirements: false, steps: [
  ['Open', 'open'], ['Doing', 'doing'], ['Review', 'review'], ['Ready', 'ready'], ['Done', 'done'], ['Closed', 'closed']].map(([name, status], i) =>
  ({id: 'g-' + status, name, status, position: i, type_id: 'general', limit_minutes: null}))};
const generalTask = (id: string, title: string, status: string) => ({id, title, body: '', owner_id: 'bot:b2', requester_id: 'human:ana', status, lane: 'company', version: 1,
  labels: [], links: [], parts: {total: 0, done: 0}, rank: 1, created_at: '2026-11-07T12:00:00Z', updated_at: '2026-11-07T12:00:00Z',
  step_since: '2026-11-07T12:00:00Z', type_id: 'general', type: {id: 'general', name: 'General'}, step_id: 'g-' + status,
  step: {id: 'g-' + status, name: status, status, type_id: 'general'}});
const MANAGER = {id: 'pm-1', slug: 'product-manager', display_name: 'Product Pipeline Manager', status: 'active', kind: 'bot', role: 'bot', name: 'Product Pipeline Manager'};

const isPhoneOk = async (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= 390);
// Contrast of the ✎ icon against the page background, from the computed colours (WCAG ratio).
const contrast = (page: Page) => page.evaluate(() => {
  const lum = (rgb: number[]) => { const [r, g, b] = rgb.map(v => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; }) as [number, number, number]; return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const parse = (css: string) => css.match(/[\d.]+/g)!.slice(0, 3).map(Number);
  const fg = parse(getComputedStyle(document.querySelector('#task-pipeline-edit')!).color);
  const bg = parse(getComputedStyle(document.body).backgroundColor);
  const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x) as [number, number];
  return (a + 0.05) / (b + 0.05);
});

// Opens the Tasks board on a phone for one theme and one board. `general` swaps in General's steps and tasks; the rest of the
// fake server (install) answers the Dev tickets pipeline, whose manager and ✎ are on.
async function openBoard(browser: Browser, theme: string, board: string, {manager = null}: {manager?: unknown} = {}) {
  const context = await browser.newContext({viewport: {width: 390, height: 844}, serviceWorkers: 'block', hasTouch: true});
  await context.addInitScript(([theme, board]: [string, string]) => {
    try { localStorage.setItem('tico.theme', theme); localStorage.setItem('tico.tasks.type', board); } catch {}
  }, [theme, board] as [string, string]);
  const page = await context.newPage();
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await install(page, {manager: board === 'dev-tickets', refuseMove: true});
  await page.route(HOST + '/api/employees', route => route.fulfill({contentType: 'application/json', body: JSON.stringify([MANAGER])}));
  await page.route('**/api/v2/pipelines/*/rights', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({may_edit_rules: true})}));
  if (board === 'general') {
    await page.route(HOST + '/api/v2/task-types', route => route.fulfill({contentType: 'application/json',
      body: JSON.stringify({types: [GENERAL, {...devType, manager: null}]})}));
    await page.route(/\/api\/v2\/tasks(\?.*)?$/, route => route.fulfill({contentType: 'application/json', body: JSON.stringify({
      tasks: [generalTask('G1', 'Write the launch note', 'open'), generalTask('G2', 'Review the Q4 plan', 'review'), generalTask('G3', 'Ship it', 'ready')],
      next_offset: null})}));
  }
  if (manager) await page.route(HOST + '/api/v2/pipelines/dev-tickets/manager', route => route.fulfill({contentType: 'application/json', body: JSON.stringify(manager)}));
  await page.goto(`${HOST}/#/tasks?type=${board}&view=board`);
  await page.waitForSelector('#task-body .pchip', {state: 'attached', timeout: t(8000)});
  await page.waitForTimeout(t(300));
  return {context, page, errors};
}

// Each stage chip's name, without its count.
const chipNames = (page: Page) => page.locator('#task-body .pchip').evaluateAll((els: Element[]) => els.map(el => el.textContent!.replace(/\d+/g, '').replace(/[·•]/g, '').trim()));

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const theme of ['light', 'dark']) {
      const label = theme + ' 390';

      // A pipeline: Dev tickets, with its manager and the run button for the two cards waiting on Ana.
      {
        const {context, page, errors} = await openBoard(browser, theme, 'dev-tickets');
        const header = await page.evaluate(() => ['#task-scope', '#task-more', '#task-pipeline-edit', '#task-play', '#task-new'].map(sel => {
          const el = document.querySelector<HTMLElement>(sel);
          if (!el || el.hidden || !el.getClientRects().length) return null;
          const b = el.getBoundingClientRect();
          return {sel, x: b.x, y: b.y, w: b.width, h: b.height};
        }).filter(Boolean) as {sel: string; x: number; y: number; w: number; h: number}[]);
        // T1: the five controls share one row, in order, all on the screen.
        assert.equal(header.length, 5, label + ': the picker, ⋯, ✎, the run button and + are all shown');
        const mid = (item: {y: number; h: number}) => item.y + item.h / 2;
        assert.ok(header.every(item => Math.abs(mid(item) - mid(header[0]!)) <= 1), label + ': one row');
        assert.ok(header.every((item, i) => i === 0 || item.x > header[i - 1]!.x), label + ': left to right in order');
        assert.ok(header.every(item => item.x >= 0 && item.x + item.w <= 390), label + ': all on the screen');
        // T2: ✎ is the same icon size as ⋯ and +, with a tap area of at least 44 × 44 px, and visible in this theme.
        const edit = header.find(item => item.sel === '#task-pipeline-edit')!, more = header.find(item => item.sel === '#task-more')!, add = header.find(item => item.sel === '#task-new')!;
        assert.ok(Math.abs(edit.w - more.w) <= 1 && Math.abs(edit.h - more.h) <= 1 && Math.abs(edit.w - add.w) <= 1 && Math.abs(edit.h - add.h) <= 1, label + ': ✎ is the size of ⋯ and +');
        const reach = await page.evaluate(() => {
          const before = getComputedStyle(document.querySelector('#task-pipeline-edit')!, '::before');
          return {w: parseFloat(before.width), h: parseFloat(before.height)};
        });
        assert.ok(reach.w >= 44 && reach.h >= 44, label + ': ✎ reaches a 44 px target (' + JSON.stringify(reach) + ')');
        assert.ok(await contrast(page) >= 3, label + ': ✎ is visible against the background');
        // T3: the stage's name is on its chip and nowhere else (the line under the chips does not repeat it).
        const chips = await chipNames(page);
        assert.deepEqual(chips.slice(0, 3), ['Spec', 'Build', 'Review'], label + ': one chip per stage, in Hub order');
        const selected = await page.locator('#task-body .pchip.on').innerText();
        assert.match(selected, /^Spec/, label + ': the selected chip names the stage');
        const lines = await page.locator('#task-body .pipe-stage-line').allInnerTexts();
        assert.ok(lines.every(line => !/Spec/.test(line)), label + ': no line under the chips repeats the stage name');
        // T4 / T5: three chips fit at 390 px, so no edge is faded.
        assert.equal(await page.locator('#task-body .pchips').getAttribute('class'), 'pchips', label + ': no fade when every chip fits');
        // T6: the swipe hint sits directly under the last card of the stage on show.
        const hint = await page.locator('#task-body .pipe-next').boundingBox();
        const lastCard = await page.locator('#task-body .pipe-stage-cards .bcard').last().boundingBox();
        assert.ok(hint && lastCard && hint.y - (lastCard.y + lastCard.height) <= 24, label + ': the swipe hint sits under the last card');
        assert.match(await page.locator('#task-body .pipe-next').innerText(), /^Swipe for Build ›$/, label + ': the swipe hint names the next stage');
        // T8: the bar names the manager by its display name in Hub.
        assert.equal((await page.locator('#pipe-rail-bar b').innerText()).trim(), 'Product Pipeline Manager', label + ': the bar names the bot by its Hub display name');
        assert.ok(await isPhoneOk(page), label + ': no sideways scroll');
        assert.deepEqual(errors, [], label + ': no page errors');
        await context.close();
      }

      // General: the same header, the one-stage view, and its Hub steps as chips.
      {
        const {context, page, errors} = await openBoard(browser, theme, 'general');
        // T10: one stage at a time, with chips, and no side-by-side status columns.
        assert.equal(await page.locator('#task-body .pipe-stage').count(), 1, label + ' General: one stage at a time');
        assert.equal(await page.locator('#task-body .bcol').count(), 0, label + ' General: no side-by-side columns');
        // T11: General's chips are its Hub steps in order.
        assert.deepEqual(await chipNames(page), ['Open', 'Doing', 'Review', 'Ready', 'Done', 'Closed'], label + ' General: the chips are the Hub steps in order');
        // T4: the six chips are wider than the screen: the row scrolls, and a fade shows on the right edge only.
        const row = page.locator('#task-body .pchips');
        const cls = async () => row.getAttribute('class');
        assert.ok(await row.evaluate(el => el.scrollWidth > el.clientWidth), label + ' General: the chips are cut off at the right');
        assert.match(await cls(), /fade-r/, label + ' General: the right edge fades where chips are cut off');
        assert.doesNotMatch(await cls(), /fade-l/, label + ' General: no fade on the left at the start');
        assert.notEqual(await row.evaluate(el => getComputedStyle(el).maskImage), 'none', label + ' General: the fade is drawn');
        // T5: scrolled to the last chip: no fade on the right, and one on the left.
        await row.evaluate(el => { el.scrollLeft = el.scrollWidth; });
        await page.waitForFunction(() => !document.querySelector('#task-body .pchips')!.classList.contains('fade-r')
          && document.querySelector('#task-body .pchips')!.classList.contains('fade-l'), null, {timeout: t(3000)});
        // T7: a stage with no cards shows no swipe hint: Doing has none.
        await page.locator('#task-body .pchip', {hasText: 'Doing'}).click();
        await page.waitForSelector('#task-body .pipe-stage', {state: 'attached'});
        assert.equal(await page.locator('#task-body .pipe-next').count(), 0, label + ' General: no hint on an empty stage');
        assert.match(await page.locator('#task-body .pipe-stage').innerText(), /Nothing here/, label + ' General: the empty stage keeps its empty state');
        // T6: on a stage with cards, the hint sits under the last card.
        await page.locator('#task-body .pchip', {hasText: 'Open'}).click();
        await page.waitForSelector('#task-body .pipe-next', {state: 'attached'});
        const hint = (await page.locator('#task-body .pipe-next').boundingBox())!;
        const card = (await page.locator('#task-body .pipe-stage-cards .bcard').last().boundingBox())!;
        assert.ok(hint.y - (card.y + card.height) <= 24, label + ' General: the hint sits under the last card');
        // T1 for General: ✎ is a rights answer; the four controls that show share one row.
        const row1 = await page.evaluate(() => ['#task-scope', '#task-more', '#task-new'].map(sel => { const b = document.querySelector(sel)!.getBoundingClientRect(); return b.y + b.height / 2; }));
        assert.ok(row1.every(y => Math.abs(y - row1[0]!) <= 1), label + ' General: the header is one row');
        assert.ok(await isPhoneOk(page), label + ' General: no sideways scroll');
        assert.deepEqual(errors, [], label + ' General: no page errors');
        await context.close();
      }
    }

    // T9: a line that begins "ask:" shows without the prefix, its first letter capitalised, and the rest unchanged.
    {
      const manager = {pipeline_id: 'dev-tickets', manager: 'product-manager', every_minutes: 60, missed_runs: 0,
        last_run: {id: 'r1', started_at: '2026-11-07T12:10:00Z', finished_at: '2026-11-07T12:10:00Z', status: 'ok', error: null},
        over_limit: [], log: [{id: 'a1', task_id: 'D52', action: 'ask', target_actor: 'human:ana', note: 'spec approved on Monday, see D52', created_at: '2026-11-07T12:10:00Z'}]};
      const {context, page, errors} = await openBoard(browser, 'light', 'dev-tickets', {manager});
      await page.waitForFunction(() => document.querySelector('#pipe-rail-bar .pr-bar-text span')?.textContent?.startsWith('Spec'), null, {timeout: t(5000)});
      assert.equal((await page.locator('#pipe-rail-bar .pr-bar-text span').innerText()).trim(), 'Spec approved on Monday, see D52', 'the ask prefix is gone and the first letter is capitalised');
      assert.deepEqual(errors, [], 'ask line: no page errors');
      await context.close();
    }

    // T8 (rename): the name is read when the bar draws, so a renamed bot shows with no release.
    {
      const {context, page, errors} = await openBoard(browser, 'dark', 'dev-tickets', {});
      assert.equal((await page.locator('#pipe-rail-bar b').innerText()).trim(), 'Product Pipeline Manager');
      await page.evaluate(() => {
        S.emps = S.emps.map(e => e.slug === 'product-manager' ? {...e, display_name: 'Pipeline Lead'} : e);
        tasksRender(TASKS_ST);
      });
      await page.waitForFunction(() => document.querySelector('#pipe-rail-bar b')?.textContent?.trim() === 'Pipeline Lead', null, {timeout: t(3000)});
      assert.deepEqual(errors, [], 'rename: no page errors');
      await context.close();
    }
  } finally {
    await browser.close();
  }
  console.log('task-phone-board: ok');
})().catch(error => { console.error(error); process.exit(1); });
