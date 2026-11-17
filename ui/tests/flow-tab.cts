import type {Page} from 'playwright';
// Offline browser check of Flow's way in (ui/app/decide.ts decideFlow, tasks.ts flowTabPaint), against a fake of the new routes:
// - at phone width the bar is Team, Flow, Assistant, Tasks, More; Flow's mark is the tabs' grey with nothing waiting and
//   jade with something, with no number, and the Tasks tab has no mark;
// - with nothing waiting Flow opens on All done, with no suggested rules (removed) and no Close of its own, and ✕ gives focus
//   back to the tab;
// - with something waiting the tab starts Flow on the first item;
// - Threads is in More, and More carries its count;
// - at desktop width Flow's mark beside Tasks is a button: hidden with nothing waiting, and pressed it starts Flow
//   without following the Tasks link, which still opens Tasks.
// FLOW_SHOTS=<folder> saves the phone bar with nothing and with something waiting, the More drawer, and the sidebar.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const path: typeof import('node:path') = require('node:path');
const {html, isUiPath, serveUi} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const brief = (id: string, stage: string) => ({id, title: `Task ${id}`, number: 1, pipeline_id: 'product', stage_id: stage, status: 'open',
  owner_id: 'bot:pm', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-09T10:00:00Z', until: null, ask: true, requirement_id: null},
  version: 3});
const question = (n: number, stage: string, line: string) => ({kind: 'ask', id: `r${n}`, task_id: `t${n}`, requirement_id: `r${n}`, line,
  since: `2026-11-09T1${n}:00:00Z`, task: brief(`t${n}`, stage),
  ask: {requirement_id: `r${n}`, kind: 'answer', line, who: 'human:ana', asked_by: 'bot:pm', choices: ['Yes', 'No'], may: ['answer'],
    met: false, shows: [], standing: 'waiting', answer: null}});
const stage = (id: string, name: string) => ({id, name, purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
  expected_bots_only: false, on_enter: {}, requirements: []});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null, stages: [stage('spec', 'Spec'), stage('build', 'Build')],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};
const Q = ['Which plan do new signups get?', 'Ship on Monday?'];

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const shots = process.env.FLOW_SHOTS || '';
  try {
    let needs: Record<string, unknown>[] = [];
    const errors: string[] = [], writes: string[] = [];
    const open = async (phone: boolean, hash: string): Promise<Page> => {
      const context = await browser.newContext({viewport: phone ? {width: 390, height: 844} : {width: 1280, height: 800}, serviceWorkers: 'block',
        reducedMotion: 'reduce', ...(phone ? {isMobile: true, hasTouch: true} : {})});
      const page = await context.newPage();
      page.on('pageerror', (error: Error) => errors.push(error.message));
      await page.route('**/*', route => {
        const r = route.request(), url = new URL(r.url()), p = url.pathname;
        const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
        if (url.origin !== 'http://tico-ui.test') return route.abort();
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        if (isUiPath(p)) return serveUi(route, p);
        if (p === '/api/me') return json({...me, config});
        if (p === '/api/v2/config') return json(config);
        if (p === '/api/employees') return json([]);
        if (p === '/api/humans' || p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
        if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
        if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: 'v' + needs.length, next: null});
        if (p === '/api/v2/threads/rail') return json({pinned: [], threads: [], catch_up: 3, catch_up_ids: ['a', 'b', 'c'], next_cursor: null, dms: {}});
        if (p === '/api/v2/flow/opinions') return json({opinions: []});
        if (r.method() !== 'GET' && p.startsWith('/api/') && !p.startsWith('/api/v2/flow/')) writes.push(`${r.method()} ${p}`);
        if (p.endsWith('/watch') || p === '/api/v2/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json(unrouted(p));
      });
      await page.goto('http://tico-ui.test/' + hash);
      await page.waitForFunction(() => typeof THREADS !== 'undefined' && THREADS.on === true && !!S.me);
      return page;
    };
    const dialog = (page: Page) => page.locator('#need-card');
    const focused = (page: Page, sel: string) => page.evaluate(s => document.activeElement === document.querySelector(s), sel);
    // The mark's fill, the other tabs' grey, and the dot's jade, each as the browser computes a colour.
    const colours = (page: Page) => page.evaluate(() => {
      const probe = document.createElement('i');
      probe.style.color = 'var(--dot)';
      document.body.append(probe);
      const jade = getComputedStyle(probe).color;
      probe.remove();
      return {mark: getComputedStyle(document.querySelector('#mobile-flow .flow-mark svg')!).fill,
        grey: getComputedStyle(document.querySelector('#mobile-nav a[data-nav="tasks"] .mobile-nav-icon')!).color, jade};
    });
    const shot = async (page: Page, name: string) => {
      if (!shots) return;
      await page.waitForTimeout(400);
      await page.screenshot({path: path.join(shots, `${name}.png`)});
    };

    // ---- A phone, nothing waiting.
    {
      const page = await open(true, '#/updates');
      const bar = await page.locator('#mobile-nav .mobile-nav-item:visible').evaluateAll(els => els.map(e => (e as HTMLElement).dataset.nav || e.id));
      assert.deepEqual(bar, ['org', 'flow', 'mobile-ask', 'tasks', 'more'], 'Flow holds the slot Threads had');
      assert.equal((await page.locator('#mobile-flow .mobile-nav-label').textContent())!.trim(), 'Flow');
      assert.equal(await page.locator('#mobile-flow .nav-badge, #mobile-flow .tnum').count(), 0, 'no number on Flow');
      assert.equal(await page.locator('#mobile-nav a[data-nav="tasks"] .flow-mark').count(), 0, 'the Tasks tab has no mark');
      const box = (await page.locator('#mobile-flow').boundingBox())!;
      assert.ok(box.width >= 44 && box.height >= 44, `the tab is a 44 px target: ${box.width}x${box.height}`);
      let c = await colours(page);
      assert.equal(c.mark, c.grey, 'nothing waiting: the mark is the tabs\' grey');
      assert.notEqual(c.mark, c.jade);
      // More carries the Threads count, as the Threads tab did.
      const more = page.locator('#mobile-more [data-threads-badge]');
      assert.equal(await more.isVisible(), true);
      assert.equal(await more.textContent(), '3');
      assert.equal(await more.getAttribute('aria-label'), '3 to catch up');
      await shot(page, 'phone-nothing-waiting');

      // Nothing waiting: All done, with one button (no Close of its own), and ✕ gives focus back to the tab.
      await page.locator('#mobile-flow').tap();
      await dialog(page).locator('.dc-done h2', {hasText: 'All done'}).waitFor();
      await shot(page, 'phone-all-done');
      assert.equal(await dialog(page).locator('.dc-done [data-dc="close"]').count(), 0, 'All done has no Close of its own');
      await dialog(page).locator('.dc-nav [data-dc="close"]').tap();
      await dialog(page).waitFor({state: 'hidden'});
      assert.equal(await focused(page, '#mobile-flow'), true, '✕: focus is back on Flow');

      // Threads is in More, with its count.
      await page.locator('#mobile-more').tap();
      await page.waitForFunction(() => document.body.classList.contains('drawer'));
      await page.waitForTimeout(300);     // the drawer slides in
      assert.equal(await page.locator('#nav-threads').isVisible(), true);
      assert.equal(await page.locator('#nav-threads .nav-badge').textContent(), '3');
      assert.equal(await page.locator('#search-open').isVisible(), true, 'Search is in More');
      assert.equal(await page.locator('#side .nav-flow').isVisible(), false, 'the drawer leaves the mark to the Flow tab');
      await shot(page, 'phone-more-drawer');
      await page.locator('#nav-threads').tap();
      await page.waitForFunction(() => location.hash === '#/threads');
      assert.equal(await page.locator('#mobile-more').evaluate(b => b.classList.contains('cur')), true, 'More is the current tab on Threads');

      // Something waits: the mark turns jade, and the tab starts Flow on the first item.
      needs = [question(1, 'spec', Q[0]!), question(2, 'build', Q[1]!)];
      await page.evaluate(() => v2Refresh());
      await page.locator('#mobile-flow[data-waiting]').waitFor({state: 'attached'});
      // The page's 1 ms transition on fill (reduced motion) ends: then the mark holds still.
      await page.waitForFunction(() => { const g = document.querySelector('#mobile-flow .flow-mark svg')!; return !g.getAnimations().length; });
      c = await colours(page);
      assert.equal(c.mark, c.jade, 'something waiting: the mark is jade');
      assert.equal(await page.locator('#mobile-flow .nav-badge, #mobile-flow .tnum').count(), 0, 'still no number');
      await shot(page, 'phone-something-waiting');
      await page.locator('#mobile-flow').tap();
      await dialog(page).locator('.dc-q', {hasText: Q[0]!}).waitFor();
      assert.equal(await page.evaluate(() => decideLive(DEC).length), 2, 'it walks everything');
      await page.keyboard.press('Escape');
      await dialog(page).waitFor({state: 'hidden'});
      assert.equal(await focused(page, '#mobile-flow'), true);
      await page.context().close();
    }

    // ---- A computer: the mark beside Tasks.
    {
      needs = [];
      const page = await open(false, '#/updates');
      assert.equal(await page.locator('#side .nav-flow').isVisible(), false, 'nothing waiting: no mark');
      needs = [question(1, 'spec', Q[0]!), question(2, 'build', Q[1]!)];
      await page.evaluate(() => v2Refresh());
      const mark = page.locator('#side').getByRole('button', {name: 'Flow', exact: true});
      await mark.waitFor();
      const link = (await page.locator('#side a[data-nav="tasks"]').boundingBox())!, at = (await mark.boundingBox())!;
      assert.ok(Math.abs((at.y + at.height / 2) - (link.y + link.height / 2)) <= 1, 'the mark sits on the Tasks row');
      await shot(page, 'desktop-sidebar');
      await mark.click();
      await dialog(page).locator('.dc-q', {hasText: Q[0]!}).waitFor();
      assert.equal(await page.evaluate(() => location.hash), '#/updates', 'the mark does not follow the Tasks link');
      await page.keyboard.press('Escape');
      await dialog(page).waitFor({state: 'hidden'});
      // From the keyboard too, and focus comes back to it.
      await mark.focus();
      await page.keyboard.press('Enter');
      await dialog(page).locator('.dc-q', {hasText: Q[0]!}).waitFor();
      await page.keyboard.press('Escape');
      await dialog(page).waitFor({state: 'hidden'});
      assert.equal(await focused(page, '#side .nav-flow'), true);
      // The link itself still opens Tasks.
      await page.locator('#side a[data-nav="tasks"]').click();
      await page.waitForFunction(() => location.hash === '#/tasks');
      assert.equal(await dialog(page).isVisible(), false);
      await page.context().close();
    }

    assert.deepEqual(writes, [], 'opening and closing Flow sends nothing');
    assert.deepEqual(errors, []);
    console.log('PASS: Flow is the phone bar\'s tab (grey, jade while something waits) and the mark beside Tasks; it walks Needs you or shows All done; More carries the Threads count.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

declare const THREADS: {on: boolean | null};
declare const S: {me?: unknown};
declare const DEC: unknown;
declare function v2Refresh(): Promise<void>;
declare function decideLive(st: unknown): string[];
