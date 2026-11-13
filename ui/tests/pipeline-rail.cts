// A pipeline on the Tasks page, desktop (docs/pipelines-v3.md S7, S10), offline on the fake server of tasks-page.cts:
//  - the columns are the pipeline's work stages, in order (Done and Closed are not columns), and they share the width;
//  - the manager's rail is closed at first: the header's chat icon opens it as a column at the RIGHT of the stages
//    (never under them), its close button closes it, and open or closed is kept in this browser;
//  - the rail is the manager's chat alone: its name, the close button, the messages and the box; no Log, Tasks,
//    Learned or Late, and the app never reads the pipeline's log;
//  - the box is always inside the window, the newest message just above it: 760, 1280 and 1600 px wide, 700, 900 and
//    1200 px high, with 1, 6 or 200 messages; ten messages sent in a row leave it where it was; scrolling a board taller
//    than the window moves neither the rail nor its box;
//  - a task link in a message opens the task;
//  - a pipeline with no manager, or a phone, has no chat icon and no rail; a phone's Ask bar opens the chat alone, its
//    box in view.
// Run: node scripts/ui-tests.cts pipeline-rail
import type {Browser, Page, Route} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, PIPELINES, fixtures, task} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;

const box = (page: Page, sel: string) => page.locator(sel).first().evaluate(el => {
  const r = el.getBoundingClientRect();
  return {x: r.x, y: r.y, w: r.width, h: r.height, top: r.top, bottom: r.bottom, hidden: (el as HTMLElement).hidden};
});

type Msg = Record<string, unknown>;
/** The manager's direct chat with `count` messages, the last one "Newest N"; messages sent go in it. */
function chat(count: number, last?: string) {
  const at = Date.now() - count * 60e3;
  const messages: Msg[] = Array.from({length: count}, (_, i) => ({
    id: 'm-' + (i + 1), thread_id: 'dm-pm', root_id: 'dm-pm', seq: i + 1, kind: 'say',
    sender_id: i % 2 ? 'bot:pm' : 'human:ana', created_at: new Date(at + i * 60e3).toISOString(), refs: {},
    body: i === count - 1 ? (last ?? `Newest ${count}`) : `Message ${i + 1}: ` + 'the stage moved on and the checks ran. '.repeat(1 + (i % 3))}));
  const thread = {id: 'dm-pm', kind: 'dm', title: '', members: ['human:ana', 'bot:pm'], me: {can_post: true, last_read_seq: count}};
  return {messages, route: async (pg: Page) => pg.route(u => u.pathname.startsWith('/api/v2/threads/'), async (route: Route) => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/api/v2/threads/dm') return json(thread);
    if (p === '/api/v2/threads/dm-pm/timeline') return json({thread, messages, next_cursor: null});
    if (p === '/api/v2/threads/dm-pm/messages' && method === 'POST') {
      const body = req.postDataJSON() as {text: string};
      const m = {id: 'm-' + (messages.length + 1), thread_id: 'dm-pm', root_id: 'dm-pm', seq: messages.length + 1, sender_id: 'human:ana',
        kind: 'say', body: body.text, refs: {}, created_at: new Date().toISOString()};
      messages.push(m);
      return json(m);
    }
    if (p.startsWith('/api/v2/threads/dm-pm')) return json({});
    return route.fallback();
  })};
}

/** Opens the product pipeline with its rail open (kept in this browser) on a chat of `count` messages. */
async function railOpen(browser: Browser, viewport: {width: number; height: number}, count: number,
  {tasks = fixtures(), last}: {tasks?: ReturnType<typeof fixtures>; last?: string} = {}) {
  const c = chat(count, last), logReads: string[] = [];
  const opened = await open(browser, {viewport, tasks, needs: [], hash: '#/tasks?pipeline=product&layout=columns', beforeGoto: async (pg: Page) => {
    await pg.addInitScript(() => { try { localStorage.setItem('tico.pipeline.rail_open', '1'); } catch {} });
    // A log as long as a real one (mostly "saved the pipeline" lines), answered if anything still asks for it.
    const log = {log: Array.from({length: 30}, (_, i) => ({at: new Date(Date.now() - i * 3600e3).toISOString(), text: 'Ana saved the pipeline', task_id: null})), learned: []};
    await pg.route('**/api/v2/pipelines/*/log', r => { logReads.push(r.request().url()); return r.fulfill({contentType: 'application/json', body: JSON.stringify(log)}); });
    await c.route(pg);
  }});
  const {page} = opened;
  await page.locator('#task-rail .thv-composer textarea').waitFor();
  await page.locator(`#task-rail .th-msg[data-mid="m-${count}"]`).waitFor();
  return {...opened, chat: c, logReads};
}

/** Where the box and the newest message are, against the window (and the phone shell's bottom bar where it shows). */
const placed = (page: Page) => page.evaluate(() => {
  const rail = document.querySelector('#task-rail')!, composer = rail.querySelector('.thv-composer')!, scroll = rail.querySelector('.thv-scroll')!;
  const msgs = rail.querySelectorAll('.th-msg[data-mid]'), last = msgs[msgs.length - 1]!;
  const nav = document.querySelector('#mobile-nav'), navTop = nav && getComputedStyle(nav).display !== 'none' ? nav.getBoundingClientRect().top : innerHeight;
  const r = (el: Element) => { const b = el.getBoundingClientRect(); return {top: Math.round(b.top), bottom: Math.round(b.bottom), left: Math.round(b.left), right: Math.round(b.right)}; };
  // Reachable, not just inside the window: what a click at the box's field and at the newest message's last line hits.
  const field = composer.querySelector('textarea')!.getBoundingClientRect(), lb = last.getBoundingClientRect();
  const hit = (x: number, y: number) => { const el = document.elementFromPoint(x, y); return !!el && rail.contains(el); };
  return {win: {w: innerWidth, h: innerHeight, floor: Math.round(Math.min(innerHeight, navTop))}, rail: r(rail), composer: r(composer), scroll: r(scroll),
    last: r(last), scrollY: scrollY,
    reach: {box: hit(field.left + field.width / 2, field.top + field.height / 2), last: hit(lb.left + lb.width / 2, lb.bottom - 6)}};
});

function assertPlaced(p: Awaited<ReturnType<typeof placed>>, where: string) {
  const {win, rail, composer, scroll, last, reach} = p;
  assert.ok(composer.top >= 0 && composer.bottom <= win.floor + 1 && composer.left >= 0 && composer.right <= win.w + 1,
    `${where}: the box is inside the window: ${JSON.stringify(p)}`);
  assert.ok(last.bottom <= composer.top + 1 && last.bottom > scroll.top && last.bottom <= scroll.bottom + 1,
    `${where}: the newest message is in view just above the box: ${JSON.stringify(p)}`);
  assert.ok(rail.bottom - composer.bottom <= 16, `${where}: the chat fills the rail, its box at the bottom: ${JSON.stringify(p)}`);
  assert.deepEqual(reach, {box: true, last: true}, `${where}: a click on the box and on the newest message reaches the rail: ${JSON.stringify(p)}`);
}

(async () => {
  const product = PIPELINES.find(p => p.id === 'product')!;
  (product as {manager_id: string | null}).manager_id = 'bot:pm';
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    {
      const {page, errors} = await open(browser, {needs: [], hash: '#/tasks?pipeline=product&layout=columns', beforeGoto: chat(1).route});
      await page.locator('#task-body .pv-col').first().waitFor();
      const cols = await page.locator('#task-body .pv-col').evaluateAll(els => els.map(e => [e.getAttribute('aria-label'), e.getBoundingClientRect().width]));
      assert.deepEqual(cols.map(c => c[0]), ['Spec', 'Build', 'Review'], 'one column per work stage, in order; no Done or Closed');
      assert.ok(cols.every(c => Number(c[1]) > 260), `columns share the width: ${JSON.stringify(cols)}`);
      const pane = await box(page, '#tasks-pane');

      // Closed at first: no rail under the stages, the chat icon in the header.
      assert.equal((await box(page, '#task-rail')).hidden, true, 'the rail is closed by default');
      const icon = page.locator('#task-manager');
      assert.equal(await icon.isVisible(), true, 'the chat icon shows for a pipeline with a manager');
      assert.equal(await icon.getAttribute('aria-pressed'), 'false');

      // Opened: a column at the right, the full height beside the stages.
      await icon.click();
      await page.locator('#task-rail .pm-head').waitFor();
      const rail = await box(page, '#task-rail'), list = await box(page, '#tasks-pane');
      assert.equal(rail.hidden, false);
      assert.ok(rail.x >= list.x + list.w - 1, `the rail is at the right of the stages: ${JSON.stringify({rail, list})}`);
      assert.ok(Math.abs(rail.y - list.y) < 2 && rail.h > 400, `the rail stands beside the stages, not under them: ${JSON.stringify(rail)}`);
      assert.ok(rail.w >= 280 && rail.w <= 360, `rail width ${rail.w}`);
      assert.ok(list.w < pane.w, 'the stages give it room');
      assert.equal(await icon.getAttribute('aria-pressed'), 'true');

      // Kept: a reload opens it again; its close button closes it.
      await page.reload();
      await page.locator('#task-rail .pm-head').waitFor();
      await page.locator('#task-rail [data-pm-hide]').click();
      assert.equal((await box(page, '#task-rail')).hidden, true, 'closed by its button');
      assert.equal(await page.evaluate(() => document.querySelector('.tasks-page')!.classList.contains('has-rail')), false);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // The box in view at every width, height and thread length.
      const bad: string[] = [];
      for (const count of [1, 6, 200]) for (const width of [760, 1280, 1600]) for (const height of [700, 900, 1200]) {
        const where = `${width}×${height}, ${count} message${count === 1 ? '' : 's'}`;
        const {page, errors} = await railOpen(browser, {width, height}, count);
        try { assertPlaced(await placed(page), where); } catch (e) { bad.push((e as Error).message); }
        assert.deepEqual(errors, [], where);
        await page.close();
      }
      assert.deepEqual(bad, [], `the box is out of view in ${bad.length} of 27`);
    }
    {
      // The chat alone: the name and close on top, then the messages and the box. A late task stays on the board only.
      const tasks = fixtures();
      tasks.find(t => t.id === 't-1')!.late = {level: 'told', since: new Date(Date.now() - 3 * 3600e3).toISOString()};
      const {page, errors, logReads} = await railOpen(browser, {width: 1280, height: 900}, 6, {tasks});
      assert.equal((await page.locator('#task-rail .pm-name').textContent())?.trim(), 'PM');
      assert.equal(await page.locator('#task-rail [data-pm-hide]').isVisible(), true);
      assert.equal(await page.locator('#task-rail .pm-tabs, #task-rail .pm-tab, #task-rail [role="tab"], #task-rail .pm-body, #task-rail .pm-row, #task-rail .pm-line').count(), 0,
        'no Log, Tasks, Learned or Late');
      const parts = await page.locator('#task-rail .pm-panel').evaluate(el => [...el.children].map(c => c.className));
      assert.deepEqual(parts, ['pm-head', 'pm-chat'], 'the head, then the chat');
      assertPlaced(await placed(page), '1280×900, 6 messages');
      assert.deepEqual(logReads, [], 'the app does not read the pipeline log');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Ten messages in a row: the box stays where it was and the list scrolls.
      const {page, errors} = await railOpen(browser, {width: 1280, height: 760}, 6);
      const before = await placed(page);
      const text = page.locator('#task-rail .thv-composer textarea');
      for (let i = 1; i <= 10; i++) {
        await text.fill(`Follow-up ${i}`);
        await text.press('Enter');
        await page.locator('#task-rail .th-msg[data-mid]', {hasText: `Follow-up ${i}`}).waitFor();
      }
      await page.waitForFunction(() => document.querySelectorAll('#task-rail .th-msg[data-mid]').length === 16);
      const after = await placed(page);
      assert.deepEqual(after.composer, before.composer, `the box did not move: ${JSON.stringify({before: before.composer, after: after.composer})}`);
      assertPlaced(after, 'after 10 sent');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A board taller than the window: scrolling it moves neither the rail nor its box.
      const many = [...fixtures(), ...Array.from({length: 60}, (_, i) => task('t-x' + i, `Spec task ${i + 1}`, 'spec'))];
      for (const viewport of [{width: 1280, height: 760}, {width: 760, height: 700}]) {
        const where = `${viewport.width}×${viewport.height}`;
        const {page, errors} = await railOpen(browser, viewport, 200, {tasks: many});
        const before = await placed(page), rail = await box(page, '#task-rail');
        const moved = await page.evaluate(() => {
          const pane = document.querySelector<HTMLElement>('#tasks-pane')!;
          // The board scrolls in the pane, the window or its own columns, depending on the layout.
          const cols = document.querySelector<HTMLElement>('#task-body .pv-cols');
          pane.scrollTop = 2000; window.scrollTo(0, 2000);
          if (cols) cols.scrollTop = 2000;
          return pane.scrollTop + window.scrollY + (cols?.scrollTop ?? 0);
        });
        assert.ok(moved > 200, `${where}: the board scrolls: ${moved}`);
        const after = await placed(page), railAfter = await box(page, '#task-rail');
        assert.deepEqual(after.composer, before.composer, `${where}: the box stays in place: ${JSON.stringify({before, after})}`);
        assert.equal(Math.round(railAfter.top), Math.round(rail.top), `${where}: the rail stays in place`);
        assertPlaced(after, `${where}, board scrolled`);
        if (viewport.width <= 760) {
          // Typing hides the bottom bar: the rail runs to the window's edge and its box stays reachable.
          await page.locator('#task-rail .thv-composer textarea').focus();
          await page.waitForFunction(() => document.body.classList.contains('mobile-composer-focus'));
          const typing = await placed(page);
          assert.ok(Math.abs(typing.rail.bottom - typing.win.h) <= 1, `${where}: the rail reaches the window's bottom while typing: ${JSON.stringify(typing)}`);
          assert.deepEqual(typing.reach, {box: true, last: true}, `${where}: the box is reachable while typing`);
        }
        assert.deepEqual(errors, [], where);
        await page.close();
      }
    }
    {
      // A task link in a message opens the task.
      const {page, errors} = await railOpen(browser, {width: 1280, height: 900}, 3, {last: 'Look at [Fix login](#/task/t-1) first.'});
      await page.locator('#task-rail .th-msg a', {hasText: 'Fix login'}).click();
      await page.locator('#task-modal[open]').waitFor();
      assert.match((await page.locator('#task-modal[open] .tmodal-title').first().textContent()) ?? '', /\S/, 'the task opens');
      assert.equal(await page.locator('#task-rail .thv-composer').isVisible(), true, 'the rail stays');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Task #339: columns are at least 300px. At 1440×900 with the manager's panel open they no longer fit, so the
      // columns scroll sideways, and their scrollbar is on screen: the board fills the room under the header and is the
      // one scroller, so the wheel over a card scrolls the columns, not the page, and the header stays.
      const tasks = fixtures();
      for (let i = 0; i < 30; i++) tasks.push(task(`b-${i}`, `A long enough title for a two-line card, number ${i}`, 'build',
        {requester_id: i % 2 ? 'bot:cal' : 'bot:perf', late: {level: 'told', since: new Date(Date.now() - 3 * 3600e3).toISOString()}}));
      // Asker names as long as the board's longest today, on a full meta line (Late, the owner's face): none may end in
      // "…" at 300px; at 200px they did.
      const bots = [['pm', 'PM'], ['cal', 'Calendar Product Owner'], ['perf', 'Performance Owner']].map(([name, display_name]) =>
        ({name, display_name, host: 'keeper', status: 'active', state: 'active', can_chat: true}));
      const {page, errors} = await open(browser, {tasks, bots, needs: [], hash: '#/tasks?pipeline=product&layout=columns'});
      await page.locator('#task-body .pv-col').first().waitFor();
      await page.locator('#task-manager').click();
      await page.locator('#task-rail .pm-head').waitFor();
      const widths = await page.locator('#task-body .pv-col').evaluateAll(els => els.map(e => e.getBoundingClientRect().width));
      assert.ok(widths.every(w => Math.abs(w - 300) < 1), `every column is 300px with the panel open at 1440: ${widths}`);
      const names = await page.locator('#task-body .tk-card .tk-asker .tk-name').evaluateAll(els =>
        els.map(e => ({text: e.textContent, cut: e.scrollWidth > e.clientWidth})));
      assert.ok(names.some(n => n.text === 'Calendar Product Owner'), `the long names are drawn: ${JSON.stringify(names.slice(0, 3))}`);
      assert.deepEqual(names.filter(n => n.cut), [], 'no asker name is cut');
      const board = await page.locator('#task-body .pv-cols').evaluate(el => ({bottom: el.getBoundingClientRect().bottom,
        sideways: el.scrollWidth > el.clientWidth, tall: el.scrollHeight > el.clientHeight, window: innerHeight}));
      assert.ok(board.sideways, 'the stages do not fit: the columns scroll sideways');
      assert.ok(board.tall, 'thirty cards are taller than the window');
      assert.ok(board.bottom <= board.window, `the columns' bottom, with their sideways scrollbar, is within the window: ${JSON.stringify(board)}`);
      const head = (await box(page, '.tl-head')).y;
      const card = await box(page, '#task-body .tk-card[data-task="b-0"]');
      await page.mouse.move(card.x + card.w / 2, card.y + card.h / 2);
      await page.mouse.wheel(0, 400);
      await page.waitForFunction(() => document.querySelector('#task-body .pv-cols')!.scrollTop > 0);
      assert.equal(await page.locator('#tasks-pane').evaluate(el => el.scrollTop), 0, 'the page itself does not scroll');
      assert.equal((await box(page, '.tl-head')).y, head, 'the header stays');
      assert.deepEqual(errors, []);
      await page.close();
    }
    for (const viewport of [{width: 1000, height: 800}, {width: 800, height: 800}]) {
      // Small windows with the panel open: still 300px columns, and the sideways scrollbar on screen.
      const {page, errors} = await open(browser, {viewport, needs: [], hash: '#/tasks?pipeline=product&layout=columns'});
      await page.locator('#task-body .pv-col').first().waitFor();
      await page.locator('#task-manager').click();
      await page.locator('#task-rail .pm-head').waitFor();
      const widths = await page.locator('#task-body .pv-col').evaluateAll(els => els.map(e => e.getBoundingClientRect().width));
      assert.ok(widths.every(w => Math.abs(w - 300) < 1), `300px columns at ${viewport.width}: ${widths}`);
      const bottom = await page.locator('#task-body .pv-cols').evaluate(el => el.getBoundingClientRect().bottom);
      assert.ok(bottom <= viewport.height, `the sideways scrollbar is on screen at ${viewport.width}: ${bottom}`);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // At 1920×1080 with the panel closed the columns share the width, each at most 480px, as before.
      const {page, errors} = await open(browser, {viewport: {width: 1920, height: 1080}, needs: [], hash: '#/tasks?pipeline=product&layout=columns'});
      await page.locator('#task-body .pv-col').first().waitFor();
      const widths = await page.locator('#task-body .pv-col').evaluateAll(els => els.map(e => e.getBoundingClientRect().width));
      assert.ok(widths.every(w => Math.abs(w - widths[0]!) < 1 && w > 300 && w <= 480), `equal columns up to 480px: ${widths}`);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      const {page, errors} = await open(browser, {needs: [], hash: '#/tasks?pipeline=ops'});
      assert.equal(await page.locator('#task-manager').isVisible(), false, 'no manager, no chat icon');
      assert.equal((await box(page, '#task-rail')).hidden, true);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A phone: no rail; the floating face opens the manager's chat alone, its box in view.
      const c = chat(200);
      const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, needs: [], hash: '#/tasks?pipeline=product', beforeGoto: c.route});
      assert.equal(await page.locator('#task-manager').isVisible(), false, 'a phone has no chat icon');
      assert.equal((await box(page, '#task-rail')).hidden, true, 'nor a rail');
      await page.locator('#task-body .pv-tab[data-pv-tab="spec"]').click();
      await page.locator('#task-ask .pa-fab').click();
      // With more than one bot on the board the face opens a menu: the manager is first.
      const pick = page.locator('#task-ask .pa-pop:not([hidden]) .pa-pick').first();
      if (await pick.isVisible()) await pick.click();
      const sheet = page.locator('dialog.pa-sheet[open]');
      await sheet.locator('.thv-composer .p-text').fill('Anything stuck?');
      await sheet.locator('.thv-composer .p-send').click();
      await sheet.locator('.th-msg[data-mid]', {hasText: 'Anything stuck?'}).waitFor();
      await page.waitForFunction(() => (document.querySelector('dialog.pa-sheet')?.getAnimations({subtree: true}) ?? []).every(x => x.playState !== 'running'));
      assert.equal(await sheet.locator('.pm-tabs, .pm-body, .pm-tab, [role="tab"]').count(), 0, 'the chat alone');
      const composer = await box(page, 'dialog.pa-sheet[open] .thv-composer'), h = await page.evaluate(() => innerHeight);
      assert.ok(composer.top >= 0 && composer.bottom <= h + 1, `the box is in view: ${JSON.stringify(composer)} of ${h}`);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('pipeline columns are its work stages; the manager rail is the chat alone, its box always in view at 27 sizes; task links open: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
