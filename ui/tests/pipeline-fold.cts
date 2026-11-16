import type {Page, Route} from 'playwright';
// Offline browser check of a pipeline's list with empty stages (task-pipelines.ts pvListHTML, #500): each run of empty
// stages is one quiet line of stage chips in its place, so a pipeline with tasks in two of thirteen stages shows every
// task without scrolling at 1440, 1280 and 1100 px in an 800 px window, and a long chip line wraps with no chip past the
// list's edge. A chip opens its stage's editor on a click and from the keyboard, with a pill-shaped focus ring. A
// pipeline with nothing open is one empty state whose New task opens the new-task form with this pipeline chosen.
// pipeline-view-unit.cts checks what is drawn in each case (runs, snoozed, search, paging, columns, phone).
// Run: npm run build:ui && node ui/tests/pipeline-fold.cts
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const req = {kind: 'owner_done', settings: {}, can_push_past: true, who_oks: 'none', person_id: null, line: ''};
const NAMES = ['Pitch', 'Spec', 'Related input', 'OK spec', 'Build', 'Review 1', 'Fixes 1', 'Review 2', 'Fixes 2', 'QA', 'OK release', 'Merge', 'Validate'];
const stages = NAMES.map((name, i) => ({id: `s${i}`, name, purpose: '', owner: null, by_hand: false, task_limit: null,
  expected_minutes: null, expected_bots_only: false, on_enter: {}, requirements: [req]}));
const pipeline = {id: 'eng', name: 'Engineering', goal: '', prefix: 'E', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null, stages,
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};
const brief = (id: string, stageId: string) => ({id, title: `Task ${id}`, number: 1, pipeline_id: 'eng', stage_id: stageId,
  status: 'open', owner_id: 'human:ana', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: null, version: 1, created_at: '2026-11-09T10:00:00Z', updated_at: '2026-11-09T10:00:00Z'});
let TASKS: ReturnType<typeof brief>[] = [];

async function serve(page: Page, errors: string[]): Promise<void> {
  page.on('pageerror', (error: Error) => errors.push(error.message));
  await page.route('**/*', (route: Route) => {
    const r = route.request(), url = new URL(r.url()), p = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json([]);
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
    if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
    if (p === '/api/v2/pipelines/eng') return json({pipeline});
    if (p === '/api/v2/factories') return json({factories: [], pipelines: []});
    if (p === '/api/v2/tasks' && r.method() === 'GET') return json({tasks: TASKS});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
}

async function list(page: Page, ready: string): Promise<void> {
  await page.goto(`http://tico-ui.test/?r=${Math.random()}#/tasks?pipeline=eng&layout=list`);
  await page.locator(ready).first().waitFor();
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [];
  try {
    // Tasks in Build and Fixes 1 only: three chip lines, every row on screen without scrolling.
    TASKS = [brief('a', 's4'), brief('b', 's6')];
    for (const width of [1440, 1280, 1100]) {
      const page = await browser.newPage({viewport: {width, height: 800}, serviceWorkers: 'block', locale: 'en-US', timezoneId: 'UTC'});
      await serve(page, errors);
      await list(page, '.pv-fold');
      const seen = await page.evaluate(() => ({
        folds: [...document.querySelectorAll('.pv-fold')].map(f => f.getAttribute('aria-label')),
        rows: [...document.querySelectorAll('.tk-row')].map(r => Math.round(r.getBoundingClientRect().bottom)),
      }));
      assert.deepEqual(seen.folds, ['Empty stages: Pitch, Spec, Related input, OK spec', 'Empty stages: Review 1',
        'Empty stages: Review 2, Fixes 2, QA, OK release, Merge, Validate'], `${width}: three chip lines, in place`);
      assert.equal(seen.rows.length, 2);
      for (const bottom of seen.rows) assert.ok(bottom <= 800, `${width}: every task row shows without scrolling (${bottom} px)`);
      await page.close();
    }

    // One task, in Validate: twelve chips wrap at 1100 px, none past the list's edge, and the page does not scroll sideways.
    TASKS = [brief('a', 's12')];
    const narrow = await browser.newPage({viewport: {width: 1100, height: 800}, serviceWorkers: 'block', locale: 'en-US', timezoneId: 'UTC'});
    await serve(narrow, errors);
    await list(narrow, '.pv-fold');
    const wrap = await narrow.evaluate(() => {
      const fold = document.querySelector('.pv-fold')!, edge = (document.querySelector('.pv') ?? fold.parentElement!).getBoundingClientRect().right;
      const chips = [...fold.querySelectorAll('.pv-chip')].map(c => c.getBoundingClientRect());
      return {n: chips.length, past: chips.filter(c => c.right > edge + 0.5).length, lines: new Set(chips.map(c => Math.round(c.top))).size,
        sideways: document.documentElement.scrollWidth > window.innerWidth};
    });
    assert.equal(wrap.n, 12);
    assert.equal(wrap.past, 0, 'no chip past the list\'s edge');
    assert.ok(!wrap.sideways, `the page does not scroll sideways (${JSON.stringify(wrap)})`);

    // A chip opens its stage's editor: by click, and by Tab and Enter with a pill-shaped focus ring.
    await narrow.locator('.pv-chip', {hasText: 'Pitch'}).click();
    await narrow.locator('#pe-dialog[open]').waitFor();
    await narrow.keyboard.press('Escape');
    await narrow.waitForFunction(() => !document.querySelector('#pe-dialog'));
    await narrow.locator('.pv-chip', {hasText: 'Pitch'}).focus();
    await narrow.keyboard.press('Tab');
    const ring = await narrow.evaluate(() => {
      const el = document.activeElement as HTMLElement;
      return {name: el.textContent, visible: el.matches(':focus-visible'), radius: getComputedStyle(el).borderTopLeftRadius};
    });
    assert.deepEqual(ring, {name: 'Spec', visible: true, radius: '999px'}, 'Tab reaches the next chip, its focus ring a pill');
    await narrow.keyboard.press('Enter');
    await narrow.locator('#pe-dialog[open]').waitFor();
    await narrow.close();

    // Nothing open: one empty state; New task opens the form with this pipeline chosen.
    TASKS = [];
    const empty = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block', locale: 'en-US', timezoneId: 'UTC'});
    await serve(empty, errors);
    await list(empty, '.pv-none');
    assert.equal((await empty.locator('.pv-none').innerText()).replace(/\s+/g, ' ').trim(), 'No open tasks in Engineering New task');
    assert.equal(await empty.locator('.pv-chip, .pv-group').count(), 0, 'no stage names in the empty state');
    await empty.locator('.pv-none [data-pv-new]').click();
    const pick = empty.locator('#task-create-form select[name="pipeline"]');
    await pick.waitFor();
    await empty.waitForFunction(() => (document.querySelector('#task-create-form select[name="pipeline"]') as HTMLSelectElement | null)?.value === 'eng');
    await empty.close();

    assert.deepEqual(errors, []);
    console.log('pipeline fold: ok');
  } finally {
    await browser.close();
  }
})().catch((error: unknown) => { console.error(error); process.exit(1); });
