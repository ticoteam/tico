import type {Browser, Page, Route} from 'playwright';
// Offline browser check of a stage's ⋯ on a pipeline board (stage-menu.ts; docs/tasks.md, the stage ⋯): the ⋯ ends each
// column's header, icon only, and opens the board's menu (task-props.ts propMenu) with what can be done to every task the
// column shows: Move all to…, Close all, Assign all to…, Snooze all…, Edit stage. Move all to… lists this pipeline's
// stages first, by name alone, then Done, then the other pipelines'; one line confirms with the count, and one request
// moves them. Close all asks "Close 2 tasks?" in the menu (no browser confirm), closes in one request, and its toast's
// Undo reopens them in one more. What stayed is said above the stages, with why. A phone has the ⋯ beside the stage tabs.
// Run: npm run build:ui && node ui/tests/stage-column-menu.cts   (TICO_SHOTS=<dir> also writes screenshots)
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const stage = (id: string, name: string, byHand = false) => ({id, name, purpose: '', owner: null, by_hand: byHand, task_limit: null,
  expected_minutes: null, expected_bots_only: false, on_enter: {}, requirements: []});
const pipeline = (id: string, name: string, prefix: string, stages: ReturnType<typeof stage>[]) => ({id, name, goal: '', prefix,
  manager_id: null, owner_id: 'human:ana', private: false, factory_id: null, preset: null, settings_version: 1, retired_at: null, stages,
  done: {id: `${id}-done`, name: 'Done'}, closed: {id: `${id}-closed`, name: 'Closed'}, may_change: true});
const PIPELINES = [pipeline('dev', 'Dev', 'D', [stage('ideas', 'Ideas', true), stage('build', 'Build'), stage('ship', 'Ship')]),
  pipeline('backlog', 'Backlog', 'B', [stage('on-ice', 'On ice', true)])];
const brief = (id: string, number: number, title: string, stageId: string) => ({id, title, number, pipeline_id: 'dev', stage_id: stageId,
  status: 'open', owner_id: 'human:ana', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: null, version: 1, created_at: '2026-11-09T10:00:00Z', updated_at: '2026-11-09T10:00:00Z'});
const fixtures = () => [brief('t1', 101, 'Old Trello idea', 'ideas'), brief('t2', 102, 'Another old idea', 'ideas'),
  brief('t3', 103, 'Export', 'build'), brief('t4', 104, 'Import', 'build'), brief('t5', 105, 'Release notes', 'ship')];

interface Served { page: Page; errors: string[]; writes: {path: string; body: Record<string, unknown>}[]; close: () => Promise<void> }

async function open(browser: Browser, {viewport = {width: 1280, height: 800}, theme = 'light', hash = '#/tasks?pipeline=dev&layout=columns'} = {}): Promise<Served> {
  const phone = viewport.width < 600;
  const context = await browser.newContext({viewport, serviceWorkers: 'block', hasTouch: phone, isMobile: phone, colorScheme: theme === 'dark' ? 'dark' : 'light'});
  await context.addInitScript((t: string) => { try { localStorage.setItem('tico.theme', t); } catch {} }, theme);
  const page = await context.newPage(), errors: string[] = [], writes: Served['writes'] = [];
  let tasks = fixtures();
  page.on('pageerror', (error: Error) => errors.push(error.message));
  await page.route('**/*', (route: Route) => {
    const r = route.request(), url = new URL(r.url()), p = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    // The UI's files, and its vendored icon font (the screenshots show the icons, not their names).
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(__dirname, '..', p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: /\.(?:js|ts)$/.test(file) ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json([]);
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
    if (p === '/api/v2/pipelines') return json({pipelines: PIPELINES});
    if (p.startsWith('/api/v2/pipelines/')) return json({pipeline: PIPELINES.find(x => p.endsWith(`/${x.id}`))});
    if (p === '/api/v2/factories') return json({factories: [], pipelines: []});
    if (p === '/api/v2/tasks' && r.method() === 'GET') return json({tasks: tasks.filter(t => t.status === 'open')});
    if (p === '/api/v2/tasks/move' && r.method() === 'POST') {
      const body = JSON.parse(r.postData() || '{}') as {ids: string[]; to: string; to_pipeline?: string};
      writes.push({path: p, body});
      // t4 is in a bot's running turn: it stays; the others go.
      const moved = body.ids.filter(id => id !== 't4');
      for (const t of tasks) if (moved.includes(t.id)) {
        if (body.to === 'closed') t.status = 'closed';
        else if (body.to === 'reopen') t.status = 'open';
        else Object.assign(t, body.to_pipeline ? {pipeline_id: body.to_pipeline} : {}, {stage_id: body.to, version: t.version + 1});
      }
      return json({batch_id: 'b1', moved, moved_unseen: 0,
        skipped: body.ids.includes('t4') ? [{id: 't4', code: 'task_turn_running', reason: 'Builder is working on this task now.'}] : []});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto(`http://tico-ui.test/${hash}`);
  return {page, errors, writes, close: () => context.close()};
}

const menuItems = async (page: Page) => (await page.locator('.prop-pop:popover-open .tl-mi:not([hidden])').allInnerTexts())
  .map(t => t.replace(/\s+/g, ' ').trim());
const shots = process.env['TICO_SHOTS'] ?? '';
const shot = async (page: Page, name: string) => {
  if (!shots) return;
  // The icon font loaded, and the menu's sheet and fades finished.
  await page.evaluate(async () => { await document.fonts.ready; await Promise.all(document.getAnimations().filter(a => a.effect?.getComputedTiming().iterations !== Infinity).map(a => a.finished.catch(() => {}))); });
  await page.screenshot({path: `${shots}/${name}.png`});
};

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  try {
    for (const theme of shots ? ['light', 'dark'] : ['light']) {
      // 1. Desktop: the ⋯ ends the header, icon only, named for its stage; it opens the menu with the first item focused.
      const {page, errors, writes, close} = await open(browser, {theme});
      await page.locator('.tk-card[data-task="t1"]').waitFor();
      const more = page.locator('[data-pv-col-menu="ideas"]');
      assert.equal(await more.getAttribute('aria-label'), 'More, Ideas');
      assert.equal((await more.innerText()).trim(), 'more_horiz', 'an icon, no words');
      assert.equal(await page.locator('.pv-col[data-pv-col="ideas"] .pv-col-head > :last-child').getAttribute('data-pv-col-menu'), 'ideas');
      await more.focus();
      await page.keyboard.press('Enter');
      await page.locator('.prop-pop:popover-open').waitFor();
      assert.deepEqual((await menuItems(page)).map(t => t.replace(/^\w+ /, '')), ['Move all to…', 'Close all', 'Assign all to…', 'Snooze all…', 'Edit stage']);
      assert.equal(await more.getAttribute('aria-expanded'), 'true');
      // The first item has the focus (propMenu moves it there once the menu is shown).
      await page.waitForFunction(() => document.activeElement?.textContent?.trim() === 'swap_horizMove all to…');
      await shot(page, `menu-desktop-${theme}`);

      // 2. Move all to…: this pipeline's stages by name, Done, then the other pipeline's; the count asks first; one write.
      await page.keyboard.press('Enter');
      await page.locator('.prop-pop:popover-open .tl-menu-q').waitFor();
      assert.deepEqual(await menuItems(page), ['Build', 'Ship', 'Done', 'Backlog › On ice']);
      await page.locator('.prop-pop:popover-open .tl-mi', {hasText: 'Build'}).click();
      await page.locator('.prop-pop:popover-open .tl-menu-h', {hasText: 'Move 2 tasks to Build?'}).waitFor();
      await shot(page, `move-confirm-desktop-${theme}`);
      await page.keyboard.press('Enter');
      await page.locator('.pv-col[data-pv-col="build"] .tk-card[data-task="t1"]').waitFor();
      assert.deepEqual(writes.map(w => [w.path, w.body]), [['/api/v2/tasks/move', {ids: ['t1', 't2'], to: 'build'}]]);
      assert.equal(await page.locator('.pv-bar:not(.pv-sel)').count(), 0, 'all moved: nothing to say');

      // 3. Close all in Build: the menu asks with the count (no browser confirm); one write; t4 stays, and the bar says why.
      page.on('dialog', d => { errors.push(`browser dialog: ${d.message()}`); void d.dismiss(); });
      await page.locator('[data-pv-col-menu="build"]').click();
      await page.locator('.prop-pop:popover-open .tl-mi', {hasText: 'Close all'}).click();
      await page.locator('.prop-pop:popover-open .tl-menu-h', {hasText: 'Close 4 tasks?'}).waitFor();
      await shot(page, `close-confirm-desktop-${theme}`);
      await page.locator('.prop-pop:popover-open .tl-mi', {hasText: 'Close'}).click();
      await page.locator('.pv-bar:not(.pv-sel)').waitFor();
      assert.deepEqual(writes[1]!.body, {ids: ['t1', 't2', 't3', 't4'], to: 'closed'});
      assert.equal((await page.locator('.pv-bar:not(.pv-sel) .pv-bar-text').innerText()).trim(), "1 didn't close: in a bot's running turn.", 'the toast says what closed');
      await page.locator('.tk-card[data-task="t3"]').waitFor({state: 'detached'});
      assert.equal(await page.locator('.pv-col[data-pv-col="build"] .tk-card').count(), 1);
      await shot(page, `close-after-desktop-${theme}`);

      // 4. Undo on the toast reopens the ones it closed, in one write.
      const toast = page.locator('.toast', {hasText: 'Closed 3 tasks'});
      await toast.locator('button', {hasText: 'Undo'}).click();
      await page.locator('.tk-card[data-task="t3"]').waitFor();
      assert.deepEqual(writes[2]!.body, {ids: ['t1', 't2', 't3'], to: 'reopen', why: 'Undone'});
      assert.equal(writes.length, 3);
      assert.deepEqual(errors, []);
      await close();

      // 5. A phone: the ⋯ sits beside the stage tabs, 44 px, for the stage shown; its menu is the same.
      const phone = await open(browser, {viewport: {width: 390, height: 844}, theme});
      await phone.page.locator('.pv-phone .tk-card').first().waitFor();
      const pm = phone.page.locator('.pv-phone-head .pv-col-more');
      const box = (await pm.boundingBox())!;
      assert.ok(box.width >= 44 && box.height >= 44, `44 px: ${box.width}x${box.height}`);
      assert.ok(box.x + box.width <= 390, 'on screen');
      assert.equal(await phone.page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, 'no sideways scroll');
      await pm.click();
      await phone.page.locator('.prop-pop:popover-open').waitFor();
      assert.ok((await menuItems(phone.page)).some(t => t.endsWith('Close all')));
      await shot(phone.page, `menu-phone-${theme}`);
      await phone.page.locator('.prop-pop:popover-open .tl-mi', {hasText: 'Close all'}).click();
      await phone.page.locator('.prop-pop:popover-open .tl-menu-h', {hasText: /^Close \d tasks?\?$/}).waitFor();
      await shot(phone.page, `close-confirm-phone-${theme}`);
      await phone.page.locator('.prop-pop:popover-open .tl-mi', {hasText: 'Close'}).click();
      await phone.page.locator('.toast', {hasText: /^Closed/}).waitFor();
      await shot(phone.page, `close-after-phone-${theme}`);
      assert.deepEqual(phone.errors, []);
      await phone.close();
    }
    console.log('stage-column-menu: ok');
  } finally {
    await browser.close();
  }
})().catch((error: unknown) => { console.error(error); process.exit(1); });
