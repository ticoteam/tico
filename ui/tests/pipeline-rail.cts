// A pipeline on the Tasks page, desktop (docs/dev/pipelines-v3.md S7, S10), offline on the fake server of tasks-page.cts:
//  - the columns are the pipeline's work stages, in order (Done and Closed are not columns), and they share the width;
//  - the pipeline's manager is the Tasks page's chat (pipeline-rail.ts, page-chat.ts, style guide "Where chat lives"):
//    no face button in a computer's header; too narrow to dock (1150 px) the page chat's tab on the right edge opens the
//    manager's chat over the stages; where it docks (1440 px) it is beside them and open at first, and the stages give it
//    room; its close folds it to the tab; at 1600 px with the column at its widest nothing scrolls sideways but the
//    columns;
//  - switched to another pipeline with the same manager, the chat stays as it is and the chip names the new board, even
//    after ✕ on the first; a message carries the new board;
//  - the manager answers only while live: a planned manager gives way to the page's owner once the roster comes (late);
//    a page owner list that comes late still brings the chat; Tasks set to "No owner" has no chat, managed pipeline or not;
//  - the column is the manager's chat alone: its name, the close button, the messages and the box; no Log, Tasks,
//    Learned or Late, and the app never reads the pipeline's log; the board on screen is a chip above the box and each
//    message carries it as refs.board;
//  - the box is always inside the window, the newest message just above it: 1000, 1280 and 1600 px wide, 700, 900 and
//    1200 px high, with 1, 6 or 200 messages; ten messages sent in a row leave it where it was; scrolling a board taller
//    than the window moves neither the column nor its box;
//  - a task link in a message opens the task, over the page and its chat;
//  - a pipeline with no manager has the page's own owner's chat, with the board; a phone has no column, and the header's
//    face (its chat mark) opens the chat alone, its box in view.
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
  const refs: Record<string, unknown>[] = [];
  const messages: Msg[] = Array.from({length: count}, (_, i) => ({
    id: 'm-' + (i + 1), thread_id: 'dm-pm', root_id: 'dm-pm', seq: i + 1, kind: 'say',
    sender_id: i % 2 ? 'bot:pm' : 'human:ana', created_at: new Date(at + i * 60e3).toISOString(), refs: {},
    body: i === count - 1 ? (last ?? `Newest ${count}`) : `Message ${i + 1}: ` + 'the stage moved on and the checks ran. '.repeat(1 + (i % 3))}));
  const thread = {id: 'dm-pm', kind: 'dm', title: '', members: ['human:ana', 'bot:pm'], me: {can_post: true, last_read_seq: count}};
  return {messages, refs, route: async (pg: Page) => pg.route(u => u.pathname.startsWith('/api/v2/threads/'), async (route: Route) => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/api/v2/threads/dm') return json(thread);
    if (p === '/api/v2/threads/dm-pm/timeline') return json({thread, messages, next_cursor: null});
    if (p === '/api/v2/threads/dm-pm/messages' && method === 'POST') {
      const body = req.postDataJSON() as {text: string; refs?: Record<string, unknown>};
      refs.push(body.refs || {});
      const m = {id: 'm-' + (messages.length + 1), thread_id: 'dm-pm', root_id: 'dm-pm', seq: messages.length + 1, sender_id: 'human:ana',
        kind: 'say', body: body.text, refs: {}, created_at: new Date().toISOString()};
      messages.push(m);
      return json(m);
    }
    if (p.startsWith('/api/v2/threads/dm-pm')) return json({});
    return route.fallback();
  })};
}

// The page chat's tab and column (page-chat.ts); the Tasks page's own owner, for a pipeline with no manager.
const TAB = 'body > .pgc-edge:not([hidden]) .pgc-tab', COL = 'aside.pgc';
const owners = (pg: Page) => pg.route('**/api/v2/page-owners', r => r.fulfill({contentType: 'application/json',
  body: JSON.stringify({pages: {tasks: {bot: 'bot:ops', name: 'Tasks'}}})}));

/** Opens the product pipeline with the manager's chat open (the tab, too narrow to dock) on a chat of `count` messages. */
const DOCKS = 1176;   // #main 940 px (the page's 680 beside the column's 260) beside the default 236 px sidebar
async function railOpen(browser: Browser, viewport: {width: number; height: number}, count: number,
  {tasks = fixtures(), last}: {tasks?: ReturnType<typeof fixtures>; last?: string} = {}) {
  const c = chat(count, last), logReads: string[] = [];
  const opened = await open(browser, {viewport, tasks, needs: [], hash: '#/tasks?pipeline=product&layout=columns', beforeGoto: async (pg: Page) => {
    await owners(pg);
    // A log as long as a real one (mostly "saved the pipeline" lines), answered if anything still asks for it.
    const log = {log: Array.from({length: 30}, (_, i) => ({at: new Date(Date.now() - i * 3600e3).toISOString(), text: 'Ana saved the pipeline', task_id: null})), learned: []};
    await pg.route('**/api/v2/pipelines/*/log', r => { logReads.push(r.request().url()); return r.fulfill({contentType: 'application/json', body: JSON.stringify(log)}); });
    await c.route(pg);
  }});
  const {page} = opened;
  await page.locator('#task-body .pv-col, #task-body .pv-tab').first().waitFor();
  if (viewport.width < DOCKS) await page.locator(TAB).click();
  await page.locator(`${COL} .thv-composer textarea`).waitFor();
  await page.locator(`aside.pgc .th-msg[data-mid="m-${count}"]`).waitFor();
  return {...opened, chat: c, logReads};
}

/** Where the box and the newest message are, against the window (and the phone shell's bottom bar where it shows). */
const placed = (page: Page) => page.evaluate(() => {
  const rail = document.querySelector('aside.pgc')!, composer = rail.querySelector('.thv-composer')!, scroll = rail.querySelector('.thv-scroll')!;
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
      const {page, errors} = await open(browser, {viewport: {width: 1150, height: 900}, needs: [], hash: '#/tasks?pipeline=product&layout=columns', beforeGoto: chat(1).route});
      await page.locator(TAB).waitFor();
      await page.locator('#task-body .pv-col').first().waitFor();
      const cols = await page.locator('#task-body .pv-col').evaluateAll(els => els.map(e => [e.getAttribute('aria-label'), e.getBoundingClientRect().width]));
      assert.deepEqual(cols.map(c => c[0]), ['Spec', 'Build', 'Review'], 'one column per work stage, in order; no Done or Closed');
      assert.ok(cols.every(c => Number(c[1]) > 260), `columns share the width: ${JSON.stringify(cols)}`);
      const pane = await box(page, '#tasks-pane');

      // Too narrow to dock, folded to the tab on the right edge: no face button in the header.
      assert.equal(await page.locator(COL).isVisible(), false, 'closed at first where it does not dock');
      assert.equal(await page.locator('#task-manager').isVisible(), false, 'no chat button in a computer\'s header');
      const icon = page.locator(TAB);
      assert.equal(await icon.getAttribute('aria-label'), 'Chat with PM', 'the tab is the manager\'s face');
      const faceBox = await box(page, TAB + ' > *');
      assert.ok(Math.abs(faceBox.w - 22) < 1, `a 22 px face (avatars are 16, 22, 28 or 36): ${JSON.stringify(faceBox)}`);
      // Opened: over the right of the stages, which keep their width.
      await icon.click();
      await page.locator(`${COL} .pgc-head`).waitFor();
      const rail = await box(page, COL), list = await box(page, '#tasks-pane');
      assert.equal(Math.round(rail.x + rail.w), 1150, `the column is at the right of the window: ${JSON.stringify(rail)}`);
      assert.ok(rail.h > 400, `the full height: ${JSON.stringify(rail)}`);
      assert.deepEqual(list, pane, 'where it does not dock it opens over the stages');
      assert.equal(await icon.getAttribute('aria-expanded'), 'true');
      // Its close folds it to the tab again.
      await page.locator(`${COL} [data-pgc-close]`).click();
      assert.equal(await page.locator(COL).isVisible(), false, 'closed by its button');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // 1440 px: docked beside the stages and open at first; the stages give it room.
      const {page, errors} = await open(browser, {viewport: {width: 1440, height: 900}, needs: [], hash: '#/tasks?pipeline=product&layout=columns', beforeGoto: chat(1).route});
      await page.locator(`${COL} .pgc-head`).waitFor();
      const rail = await box(page, COL), list = await box(page, '#tasks-pane');
      assert.ok(rail.x >= list.x + list.w - 1, `the column is at the right of the stages: ${JSON.stringify({rail, list})}`);
      assert.ok(rail.w >= 260 && rail.w <= 560, `column width ${rail.w}`);
      assert.equal(await page.locator(TAB).count(), 0, 'no tab while docked open');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Another pipeline with the same manager: the chat stays (the same box, its draft), the chip names the new board,
      // and ✕ on the first board's chip does not carry over to the second's. The message carries the board on screen.
      const ops = PIPELINES.find(p => p.id === 'ops')!;
      (ops as {manager_id: string | null}).manager_id = 'bot:pm';
      try {
        const c = chat(2);
        const {page, errors} = await open(browser, {viewport: {width: 1440, height: 900}, needs: [], hash: '#/tasks?pipeline=product&layout=columns', beforeGoto: c.route});
        await page.locator(`${COL} .thv-composer textarea`).waitFor();
        const chip = page.locator(`${COL} .thv-ctx:not([hidden]) .thv-ctx-l`);
        assert.match(await chip.innerText(), /^Product › /);
        await page.locator(`${COL} .thv-ctx-x`).click();
        assert.equal(await page.locator(`${COL} .thv-ctx`).isHidden(), true, '✕ drops the board');
        await page.locator(`${COL} .thv-composer textarea`).fill('Draft that stays');
        await page.locator(`${COL} .pgc-chat > *`).first().evaluate(el => { (el as HTMLElement).dataset['kept'] = '1'; });
        await page.evaluate(() => { location.hash = '#/tasks?pipeline=ops&layout=columns'; });
        await page.locator('#task-body .pv-col[aria-label="To do"]').waitFor();
        await page.waitForFunction(() => /^Operations › /.test(document.querySelector('aside.pgc .thv-ctx:not([hidden]) .thv-ctx-l')?.textContent || ''));
        assert.equal(await page.locator(`${COL} .thv-composer textarea`).inputValue(), 'Draft that stays', 'the draft stays');
        assert.equal(await page.locator(`${COL} .pgc-chat > [data-kept="1"]`).count(), 1, 'the same chat, not drawn again');
        await page.locator(`${COL} .thv-composer textarea`).fill('Anything stuck here?');
        await page.locator(`${COL} .thv-composer textarea`).press('Enter');
        await page.locator(`${COL} .th-msg[data-mid]`, {hasText: 'Anything stuck here?'}).waitFor();
        const board = c.refs.at(-1)?.['board'] as {pipeline_id?: string} | undefined;
        assert.equal(board?.pipeline_id, 'ops', `the message carries the board on screen: ${JSON.stringify(c.refs.at(-1))}`);
        assert.deepEqual(errors, []);
        await page.close();
      } finally { (ops as {manager_id: string | null}).manager_id = null; }
    }
    {
      // A planned manager is not live: once the roster comes (late), the page's own owner answers instead.
      const bots = [['pm', 'PM', 'planned'], ['ops', 'Ops', 'active']].map(([name, display_name, status]) =>
        ({name, display_name, host: 'keeper', status, state: status, can_chat: true}));
      const late = async (pg: Page) => {
        await owners(pg);
        await pg.route('**/api/employees', async r => { await new Promise(res => setTimeout(res, 1500)); await r.fallback(); });
      };
      const {page, errors} = await open(browser, {viewport: {width: 1150, height: 900}, bots, needs: [], hash: '#/tasks?pipeline=product&layout=columns', beforeGoto: late});
      await page.locator('#task-body .pv-col').first().waitFor();
      await page.waitForFunction(() => document.querySelector('body > .pgc-edge:not([hidden]) .pgc-tab')?.getAttribute('aria-label') === 'Chat with Ops',
        null, {timeout: 10_000});
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // The page owners read late: the chat comes when they do, docked open at 1440.
      const late = (pg: Page) => pg.route('**/api/v2/page-owners', async r => {
        await new Promise(res => setTimeout(res, 1500));
        await r.fulfill({contentType: 'application/json', body: JSON.stringify({pages: {tasks: {bot: 'bot:ops', name: 'Tasks', choice: 'default', default: 'bot:ops'}}})});
      });
      const {page, errors} = await open(browser, {viewport: {width: 1440, height: 900}, needs: [], hash: '#/tasks?pipeline=ops&layout=columns', beforeGoto: late});
      await page.locator('#task-body .pv-col').first().waitFor();
      await page.locator(`${COL}:not([hidden]) .pgc-name`).waitFor({timeout: 10_000});
      assert.equal((await page.locator(`${COL} .pgc-name`).innerText()).trim(), 'Ops');
      assert.deepEqual(errors, []);
      await page.close();
    }
    for (const hash of ['#/tasks?pipeline=product&layout=columns', '#/tasks?pipeline=ops&layout=columns']) {
      // Tasks set to "No owner": no chat, not even the pipeline manager's.
      const none = (pg: Page) => pg.route('**/api/v2/page-owners', r => r.fulfill({contentType: 'application/json',
        body: JSON.stringify({pages: {tasks: {bot: null, name: 'Tasks', choice: 'none', default: 'bot:ops'}}})}));
      for (const width of [1150, 1440]) {
        const {page, errors} = await open(browser, {viewport: {width, height: 900}, needs: [], hash, beforeGoto: none});
        await page.locator('#task-body .pv-col').first().waitFor();
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(300);
        assert.equal(await page.locator(`${COL}:not([hidden])`).count(), 0, `${hash} at ${width}: no column`);
        assert.equal(await page.locator('[data-pgc-open]:visible').count(), 0, `${hash} at ${width}: no tab and no mark`);
        assert.deepEqual(errors, []);
        await page.close();
      }
    }
    {
      // The box in view at every width, height and thread length.
      const bad: string[] = [];
      for (const count of [1, 6, 200]) for (const width of [1000, 1280, 1600]) for (const height of [700, 900, 1200]) {
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
      assert.equal((await page.locator('aside.pgc .pgc-name').textContent())?.trim(), 'PM');
      assert.equal(await page.locator('aside.pgc [data-pgc-close]').isVisible(), true);
      assert.equal(await page.locator('aside.pgc [role="tab"], aside.pgc .pm-tabs, aside.pgc .pm-body').count(), 0,
        'no Log, Tasks, Learned or Late');
      const parts = await page.locator('aside.pgc .pgc-shell').evaluate(el => [...el.children].map(c => c.className));
      assert.deepEqual(parts, ['pgc-grip', 'pgc-head', 'pgc-chat'], 'the head, then the chat');
      // The board on screen is a chip above the box, as on a phone's sheet.
      assert.match(await page.locator('aside.pgc .thv-ctx').innerText(), /^Product › Spec · \d+ tasks?/);
      assertPlaced(await placed(page), '1280×900, 6 messages');
      assert.deepEqual(logReads, [], 'the app does not read the pipeline log');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Ten messages in a row: the box stays where it was and the list scrolls.
      const {page, errors, chat: c} = await railOpen(browser, {width: 1280, height: 760}, 6);
      const before = await placed(page);
      const text = page.locator('aside.pgc .thv-composer textarea');
      for (let i = 1; i <= 10; i++) {
        await text.fill(`Follow-up ${i}`);
        await text.press('Enter');
        await page.locator('aside.pgc .th-msg[data-mid]', {hasText: `Follow-up ${i}`}).waitFor();
      }
      await page.waitForFunction(() => document.querySelectorAll('aside.pgc .th-msg[data-mid]').length === 16);
      const after = await placed(page);
      assert.deepEqual(after.composer, before.composer, `the box did not move: ${JSON.stringify({before: before.composer, after: after.composer})}`);
      assertPlaced(after, 'after 10 sent');
      // Each carries the board (refs.board) and the page it was written on (refs.from_page).
      const board = c.refs.at(-1)?.['board'] as {pipeline_id?: string; stage_id?: string} | undefined;
      assert.equal(board?.pipeline_id, 'product', `refs.board: ${JSON.stringify(c.refs.at(-1))}`);
      assert.equal(board?.stage_id, 'spec');
      assert.deepEqual(c.refs.at(-1)?.['from_page'], {page: 'tasks', label: 'Tasks'});
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A board taller than the window: scrolling it moves neither the rail nor its box.
      const many = [...fixtures(), ...Array.from({length: 60}, (_, i) => task('t-x' + i, `Spec task ${i + 1}`, 'spec'))];
      for (const viewport of [{width: 1280, height: 760}, {width: 1000, height: 700}]) {
        const where = `${viewport.width}×${viewport.height}`;
        const {page, errors} = await railOpen(browser, viewport, 200, {tasks: many});
        const before = await placed(page), rail = await box(page, COL);
        const moved = await page.evaluate(() => {
          const pane = document.querySelector<HTMLElement>('#tasks-pane')!;
          // The board scrolls in the pane, the window or its own columns, depending on the layout.
          const cols = document.querySelector<HTMLElement>('#task-body .pv-cols');
          pane.scrollTop = 2000; window.scrollTo(0, 2000);
          if (cols) cols.scrollTop = 2000;
          return pane.scrollTop + window.scrollY + (cols?.scrollTop ?? 0);
        });
        assert.ok(moved > 200, `${where}: the board scrolls: ${moved}`);
        const after = await placed(page), railAfter = await box(page, COL);
        assert.deepEqual(after.composer, before.composer, `${where}: the box stays in place: ${JSON.stringify({before, after})}`);
        assert.equal(Math.round(railAfter.top), Math.round(rail.top), `${where}: the rail stays in place`);
        assertPlaced(after, `${where}, board scrolled`);
        assert.deepEqual(errors, [], where);
        await page.close();
      }
    }
    {
      // A task link in a message opens the task.
      const {page, errors} = await railOpen(browser, {width: 1280, height: 900}, 3, {last: 'Look at [Fix login](#/task/t-1) first.'});
      await page.locator('aside.pgc .th-msg a', {hasText: 'Fix login'}).click();
      await page.locator('#task-modal[open]').waitFor();
      assert.match((await page.locator('#task-modal[open] .tmodal-title').first().textContent()) ?? '', /\S/, 'the task opens');
      // The task window opens over the page and its chat: the chat stays where it was beneath it.
      assert.equal(await page.locator('aside.pgc .thv-composer').count(), 1, 'the chat stays');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Task #339: columns are at least 300px. At 1600×900 with the manager's chat docked at 560 px they no longer fit, so the
      // columns scroll sideways, and their scrollbar is on screen: the board fills the room under the header and is the
      // one scroller, so the wheel over a card scrolls the columns, not the page, and the header stays.
      const tasks = fixtures();
      for (let i = 0; i < 30; i++) tasks.push(task(`b-${i}`, `A long enough title for a two-line card, number ${i}`, 'build',
        {requester_id: i % 2 ? 'bot:cal' : 'bot:perf', late: {level: 'told', since: new Date(Date.now() - 3 * 3600e3).toISOString()}}));
      // Asker names as long as the board's longest today, on a full meta line (Late, the owner's face): none may end in
      // "…" at 300px; at 200px they did.
      const bots = [['pm', 'PM'], ['cal', 'Calendar Product Owner'], ['perf', 'Performance Owner']].map(([name, display_name]) =>
        ({name, display_name, host: 'keeper', status: 'active', state: 'active', can_chat: true}));
      // The chat dragged to its widest (560 px, kept in this browser) leaves the stages too little room.
      const {page, errors} = await open(browser, {viewport: {width: 1600, height: 900}, tasks, bots, needs: [], hash: '#/tasks?pipeline=product&layout=columns',
        beforeGoto: (pg: Page) => pg.addInitScript(() => { try { localStorage.setItem('tico.page_chat.width', '560'); } catch {} })});
      await page.locator('#task-body .pv-col').first().waitFor();
      await page.locator('aside.pgc .pgc-head').waitFor();
      const widths = await page.locator('#task-body .pv-col').evaluateAll(els => els.map(e => e.getBoundingClientRect().width));
      assert.ok(widths.every(w => Math.abs(w - 300) < 1), `every column is 300px with the chat docked at 1600: ${widths}`);
      const names = await page.locator('#task-body .tk-card .tk-asker .tk-name').evaluateAll(els =>
        els.map(e => ({text: e.textContent, cut: e.scrollWidth > e.clientWidth})));
      assert.ok(names.some(n => n.text === 'Calendar Product Owner'), `the long names are drawn: ${JSON.stringify(names.slice(0, 3))}`);
      assert.deepEqual(names.filter(n => n.cut), [], 'no asker name is cut');
      assert.equal(Math.round((await box(page, COL)).w), 560, 'the column at its widest');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), 0,
        'at 1600 with the column at its widest the page itself does not scroll sideways');
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
      // Small windows with the chat open: still 300px columns, and the sideways scrollbar on screen.
      const {page, errors} = await open(browser, {viewport, needs: [], hash: '#/tasks?pipeline=product&layout=columns'});
      await page.locator('#task-body .pv-col').first().waitFor();
      await page.locator(TAB).click();
      await page.locator('aside.pgc .pgc-head').waitFor();
      const widths = await page.locator('#task-body .pv-col').evaluateAll(els => els.map(e => e.getBoundingClientRect().width));
      assert.ok(widths.every(w => Math.abs(w - 300) < 1), `300px columns at ${viewport.width}: ${widths}`);
      const bottom = await page.locator('#task-body .pv-cols').evaluate(el => el.getBoundingClientRect().bottom);
      assert.ok(bottom <= viewport.height, `the sideways scrollbar is on screen at ${viewport.width}: ${bottom}`);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // At 1920×1080 with the chat closed the columns share the width, each at most 480px, as before.
      const {page, errors} = await open(browser, {viewport: {width: 1920, height: 1080}, needs: [], hash: '#/tasks?pipeline=product&layout=columns',
        beforeGoto: (pg: Page) => pg.addInitScript(() => { try { localStorage.setItem('tico.page_chat.open.tasks', '0'); } catch {} })});
      await page.locator('#task-body .pv-col').first().waitFor();
      const widths = await page.locator('#task-body .pv-col').evaluateAll(els => els.map(e => e.getBoundingClientRect().width));
      assert.ok(widths.every(w => Math.abs(w - widths[0]!) < 1 && w > 300 && w <= 480), `equal columns up to 480px: ${widths}`);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // No manager: the Tasks page's own owner answers, with the board.
      const {page, errors} = await open(browser, {viewport: {width: 1150, height: 900}, needs: [], hash: '#/tasks?pipeline=ops', beforeGoto: owners});
      assert.equal(await page.locator('#task-manager').isVisible(), false, 'no face in the header');
      await page.locator(TAB).waitFor();
      assert.equal(await page.locator(TAB).getAttribute('aria-label'), 'Chat with Ops', 'the page\'s own owner');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A phone: no rail; the header's face (the page's chat mark) opens the manager's chat alone, its box in view.
      const c = chat(200);
      const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, needs: [], hash: '#/tasks?pipeline=product', beforeGoto: c.route});
      await page.locator('#task-body .pv-tab[data-pv-tab="spec"]').click();
      assert.equal(await page.locator('#task-manager').isVisible(), true, 'a phone has the face in its header');
      assert.equal(await page.locator('#task-manager').getAttribute('aria-pressed'), null, 'it opens a chat, not a column');
      assert.equal(await page.locator('#main header.ph > .ph-chat > *').count(), 0, 'the face is the page\'s chat mark: no second one');
      assert.equal(await page.locator(TAB).count(), 0, 'no tab on a phone');
      await page.locator('#task-manager').click();
      assert.equal(await page.locator(COL + ':not(.pgc-full)').isVisible(), false, 'no column');
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
    console.log('pipeline columns are its work stages; the manager is the page chat, the chat alone, its box always in view at 27 sizes; task links open: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
