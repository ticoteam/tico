// Run with Playwright available: NODE_PATH=/path/to/node_modules node ui/tests/pipeline-rail.cjs
// The manager rail beside a pipeline (log with errors first, Late, hide and remember, chat), the phone's one-stage view
// with its chips, bottom bar and sheet, Move anyway on a refused drag, and Attach to on a ticket. 1440 and 390 wide,
// dark and light.
import type * as PipelineFixtureSupport from './support/pipeline-fixture.cts';
import type {Browser, Page} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {install} = require('./support/pipeline-fixture.cts') as PipelineFixtureSupport.Api;
const {t} = (() => { try { return require('./support/load.cjs') as {t: (ms: number) => number}; } catch { return {t: (ms: number) => ms}; } })();

// What the fake server records, plus the Late reads this spec counts (not yet in the fixture's d.cts).
type RailState = PipelineFixtureSupport.PipelineState & {lateReads: number; lateAsks: (string | null)[]};
type ContextOptions = NonNullable<Parameters<Browser['newContext']>[0]>;
// Page globals the Tasks page defines (the spec calls them through page.evaluate).
declare const TASKS_ST: {tasks: {id: string}[]};
declare const TASK_TYPES: {id: string; name: string; steps: {id: string; name: string; status: string; position: number; type_id: string}[]}[];
declare function taskBoardDrop(state: unknown, drop: unknown): void;
declare function taskModalShow(task: unknown): void;
declare function tasksRender(state: unknown): void;
declare function taskPipelineLabel(typeId: string): string;

const overflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
// Waits for something the fake server records (a read) without a fixed sleep.
async function until(check: () => boolean, ms = 5000) {
  const end = Date.now() + ms;
  while (!check()) { if (Date.now() > end) throw new Error('timed out waiting for the server to record it'); await new Promise(r => setTimeout(r, 50)); }
}
const newPage = async (browser: Browser, viewport: {width: number; height: number}, extra: ContextOptions = {}) => {
  const context = await browser.newContext({viewport, serviceWorkers: 'block', ...extra});
  await context.addInitScript(() => {
    try { localStorage.setItem('tico.theme', 'dark'); localStorage.setItem('tico.tasks.type', 'dev-tickets'); } catch {}
  });
  const page = await context.newPage();
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  return {context, page, errors};
};
// The composer sits inside its panel with a gutter on both sides, never on the viewport's edge.
const composerInside = async (page: Page, panel: string) => {
  const box = (await page.locator(panel).boundingBox())!, composer = (await page.locator('.pr-chat .thv-composer').boundingBox())!;
  return composer.x >= box.x + 12 && composer.x + composer.width <= box.x + box.width - 12;
};

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const theme of ['dark', 'light']) for (const width of [1440, 390]) {
      const phone = width < 600, label = `${theme} ${width}`;
      const context = await browser.newContext({viewport: {width, height: phone ? 844 : 940}, serviceWorkers: 'block', hasTouch: phone});
      await context.addInitScript(([theme]: [string]) => {
        try { localStorage.setItem('tico.theme', theme); localStorage.setItem('tico.tasks.type', 'dev-tickets'); } catch {}
      }, [theme] as [string]);
      const page = await context.newPage();
      const errors: string[] = [];
      page.on('pageerror', e => errors.push(e.message));
      const state = await install(page, {manager: true, refuseMove: true});
      await page.goto('http://tico-ui.test/#/tasks?type=dev-tickets&view=board');
      await page.waitForSelector('#pipe-rail', {state: 'attached', timeout: t(8000)});
      await page.waitForFunction(() => document.querySelector('.pr-body .pr-late'), null, {timeout: t(8000)});
      assert.ok(await page.evaluate(() => document.querySelector('.tasks-page')!.classList.contains('has-rail')), label);


      const stepAge = async () => {
        // The cards past their stage's limit say so; others show nothing.
        await page.waitForSelector('[data-task-key="tD17"] [data-step-age]', {timeout: t(5000)});
        assert.equal(await page.locator('[data-task-key="tD52"] [data-step-age]').count(), 0, label + ': on time shows no time');
      };
      if (!phone) {
        await stepAge();
        // The rail: the manager, when it ran, three tabs, the Late list, the log with the failed run before the newer ok one.
        assert.ok((await page.locator('.pr-head b').innerText()).length > 0, 'the manager is named');
        assert.match(await page.locator('.pr-ran').innerText(), /Ran \d\d:\d\d/);
        assert.deepEqual(await page.locator('.pr-tab').allInnerTexts().then(a => a.map(s => s.replace(/\s+\d+$/, '').trim())), ['Log', 'Tasks', 'Learned']);
        assert.equal((await page.locator('.pr-tab').nth(1).innerText()).trim(), 'Tasks 1', 'the Tasks tab counts the manager\'s open tasks');
        assert.match(await page.locator('.pr-late').first().innerText(), /D17/);
        const lines = await page.locator('.pr-log .pr-line').allInnerTexts();
        assert.match(lines[0]!, /unreachable/, 'errors first');
        assert.equal(await page.locator('.pr-chat').count(), 1, 'chat with the manager is in the rail');
        await page.waitForSelector('.pr-chat .thv-composer', {timeout: t(5000)});
        assert.ok(await composerInside(page, '.pr-panel'), label + ': the composer sits inside the rail');
        // Hide to a strip and remember it as a user pref; a fresh load reads it back.
        await page.locator('[data-rail-hide]').click();
        await page.waitForFunction(() => document.querySelector('#pipe-rail')!.classList.contains('hidden') || getComputedStyle(document.querySelector('.pr-panel')!).display === 'none');
        assert.equal(state.prefs['pipeline.rail_hidden'], true, 'hide is remembered');
        await page.reload();
        await page.waitForSelector('#pipe-rail', {state: 'attached', timeout: t(8000)});
        await page.waitForFunction(() => getComputedStyle(document.querySelector('.pr-panel')!).display === 'none', null, {timeout: t(5000)});
        await page.locator('[data-rail-show]').click();
        await page.waitForFunction(() => getComputedStyle(document.querySelector('.pr-panel')!).display !== 'none');
        assert.equal(state.prefs['pipeline.rail_hidden'], false);
      } else {
        // The phone: one stage with its chips, the rail as a bar, the sheet from the bar.
        assert.ok(await page.locator('.pchips .pchip').count() >= 3, label + ': a chip for each stage');
        assert.equal(await page.locator('#task-body .pipe-stage').count(), 1, label + ': one stage at a time');
        const chip = (await page.locator('.pchips .pchip').first().boundingBox())!;
        const reach = await page.evaluate(([x, y]: [number, number]) => document.elementFromPoint(x, y)?.closest('.pchip') !== null, [chip.x + chip.width / 2, chip.y - 5] as [number, number]);
        assert.ok(chip.height >= 32 && reach, label + ': chips reach a 44px target');
        await page.locator('.pchips .pchip').nth(1).click();
        await page.waitForFunction(() => document.querySelector<HTMLElement>('.pchip.on')?.dataset.pipeChip === 'build');
        await stepAge();
        assert.equal((await page.locator('.pipe-stage-limit').innerText()).trim(), 'warn after 1d', label + ': the stage line names its limit');
        const stageCards = (await page.locator('.pipe-stage-cards').boundingBox())!, card = (await page.locator('[data-task-key="tD52"]').boundingBox())!;
        assert.ok(Math.abs(card.x - (stageCards.x + 16)) <= 1 && Math.abs(card.width - (stageCards.width - 32)) <= 1, label + ': cards run full width with 16px gutters');
        assert.ok(await page.locator('[data-task-key="tD52"]').count(), label + ': Build shows D52');
        assert.ok(await page.locator('.pchip-dot').count() >= 1, label + ': a dot where something waits on you');
        const bar = page.locator('#pipe-rail-bar');
        assert.ok(await bar.isVisible(), label + ': the bar shows');
        assert.match(await bar.innerText(), /1 late/);
        assert.ok((await bar.boundingBox())!.height >= 44, label + ': the bar is a 44px target');
        await bar.click();
        await page.waitForFunction(() => document.querySelector('#pipe-rail')?.classList.contains('sheet-open'));
        await page.locator('.pr-late').first().waitFor({state: 'visible'});
        assert.match(await page.locator('.pr-late').first().innerText(), /D17/);
        await page.waitForSelector('.pr-chat .thv-composer', {timeout: t(5000)});
        assert.ok(await composerInside(page, '.pr-panel'), label + ': the composer sits inside the sheet');
        await page.locator('.pr-x[data-rail-close]').click();
        await page.waitForFunction(() => !document.querySelector('#pipe-rail')!.classList.contains('sheet-open'));
      }
      assert.ok(await overflow(page) <= 0, label + ': no horizontal scroll');

      // Move anyway: a refused drag asks for a reason, then sends the move again with override.
      await page.evaluate(() => taskBoardDrop(TASKS_ST, {key: 'tD52', target: {dataset: {dropStep: 'review1'}}, from: {}, prev: null, next: null, card: document.body}));
      await page.waitForSelector('dialog.pipe-override', {timeout: t(5000)});
      await page.locator('dialog.pipe-override button[type=submit]').click();
      assert.equal(await page.locator('dialog.pipe-override').count(), 1, label + ': no reason, no move');
      assert.equal(state.moves.length, 1);
      await page.locator('dialog.pipe-override input[name=why]').fill('Shots reviewed on a call');
      await page.locator('dialog.pipe-override button[type=submit]').click();
      await page.waitForFunction(() => !document.querySelector('dialog.pipe-override'));
      await new Promise(r => setTimeout(r, 200));
      const retry = state.moves.at(-1)!;
      assert.equal(retry.override_requirements, true, label + ': the retry carries override');
      assert.equal(retry.note, 'Shots reviewed on a call');
      assert.equal(state.moves.filter(m => !m.override_requirements).length, 1, label + ': one refused move first');

      // Attach to: a link on the Screenshots requirement lists it.
      await page.evaluate(() => { location.hash = '#/tasks?type=dev-tickets&view=board'; });
      await page.evaluate(() => { const task = TASKS_ST.tasks.find(x => x.id === 'D52'); taskModalShow(task); });
      await page.waitForSelector('.task-requirements [data-req-attach-open]', {timeout: t(5000)});
      await page.locator('[data-req-attach-open]').click();
      await page.locator('[data-req-attach="r-shots"]').click();
      await page.locator('.task-req-attach-form input[name=url]').fill('https://example.test/shot.png');
      await page.locator('.task-req-attach-form button[type=submit]').click();
      await page.waitForSelector('.task-req-files li', {timeout: t(5000)});
      assert.deepEqual(state.attachments.at(-1), {source: 'link', url: 'https://example.test/shot.png'}, label + ': the link is attached');
      assert.ok(await overflow(page) <= 0, label + ': no horizontal scroll with the ticket open');

      assert.deepEqual(errors, [], label + ': no page errors');
      await context.close();
    }

    // E3: a member who owns the pipeline may mark met: the server says so per pipeline (can_edit_rules absent here), and the
    // owner check reads pipeline_owner_id. The rules controls follow that answer.
    {
      const {context, page, errors} = await newPage(browser, {width: 1440, height: 940});
      await install(page, {ownerOnly: true, member: true});
      await page.goto('http://tico-ui.test/#/tasks?type=dev-tickets&view=board');
      await page.waitForSelector('[data-task-key="tD52"] .bcard-open', {timeout: t(8000)});
      await page.locator('[data-task-key="tD52"] .bcard-open').click();
      await page.waitForSelector('.task-req[data-req-id="r-shots"] [data-req-more]', {timeout: t(5000)});
      assert.equal(await page.locator('.task-req[data-req-id="r-spec"] [data-req-more]').count(), 0, 'a met requirement offers no mark');
      assert.deepEqual(errors, [], 'owner controls: no page errors');
      await context.close();
    }

    // E10: a card past its stage's loaded limit shows the late clock with no manager beside the board.
    {
      const {context, page, errors} = await newPage(browser, {width: 1440, height: 940});
      await install(page);
      await page.goto('http://tico-ui.test/#/tasks?type=dev-tickets&view=board');
      await page.waitForSelector('[data-task-key="tD17"] [data-step-age]', {timeout: t(8000)});
      assert.equal(await page.locator('[data-task-key="tD52"] [data-step-age]').count(), 0, 'a card within its limit shows nothing');
      assert.equal(await page.locator('#pipe-rail').count(), 0, 'no manager, so no rail');
      assert.deepEqual(errors, [], 'late without a rail: no page errors');
      await context.close();
    }

    // E12: the manager poll runs while Tasks is open and stops when the page is left.
    {
      const {context, page, errors} = await newPage(browser, {width: 1440, height: 940});
      await page.clock.install();
      const state = await install(page, {manager: true});
      await page.goto('http://tico-ui.test/#/tasks?type=dev-tickets&view=board');
      await page.waitForSelector('#pipe-rail', {state: 'attached', timeout: t(8000)});
      await until(() => state.managerReads >= 1);
      const before = state.managerReads;
      await page.clock.fastForward(61000);
      await until(() => state.managerReads > before);
      await page.evaluate(() => { location.hash = '#/overview'; });
      await new Promise(r => setTimeout(r, 500));
      const left = state.managerReads;
      await page.clock.fastForward(10 * 60000);
      await new Promise(r => setTimeout(r, 300));
      assert.equal(state.managerReads, left, 'the manager poll stops when Tasks is left');
      assert.deepEqual(errors, [], 'poll: no page errors');
      await context.close();
    }

    // F13: the Late list is read only while Tasks shows the pipeline. A hidden tab reads nothing and reads once when it is
    // shown again; leaving Tasks stops the reads for good.
    {
      const {context, page, errors} = await newPage(browser, {width: 1440, height: 940});
      await page.clock.install();
      const state = await install(page) as RailState;
      await page.goto('http://tico-ui.test/#/tasks?type=dev-tickets&view=board');
      await page.waitForSelector('[data-task-key="tD17"] [data-step-age]', {timeout: t(8000)});
      await until(() => state.lateReads >= 1);
      await page.evaluate(() => Object.defineProperty(document, 'hidden', {configurable: true, get: () => true}));
      const hiddenFrom = state.lateReads;
      await page.clock.fastForward(10 * 60000);
      await new Promise(r => setTimeout(r, 300));
      assert.equal(state.lateReads, hiddenFrom, 'a hidden tab reads no Late list');
      await page.evaluate(() => Object.defineProperty(document, 'hidden', {configurable: true, get: () => false}));
      await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
      await until(() => state.lateReads === hiddenFrom + 1);
      await page.evaluate(() => { location.hash = '#/overview'; });
      await new Promise(r => setTimeout(r, 500));
      const left = state.lateReads;
      await page.clock.fastForward(10 * 60000);
      await new Promise(r => setTimeout(r, 300));
      assert.equal(state.lateReads, left, 'the Late list is not read after Tasks is left');
      assert.deepEqual(errors, [], 'late polling: no page errors');
      await context.close();
    }

    // F14: a card's late time is the server's clock. The step began five hours ago, but the server says the ticket is two
    // hours in step, so the card says 2h.
    {
      const {context, page, errors} = await newPage(browser, {width: 1440, height: 940});
      await install(page, {late: [{task_id: 'D17', title: 'Sim stuck checks', stage: 'Build', hours: 2, limit_hours: 1, since: new Date().toISOString()}]});
      await page.goto('http://tico-ui.test/#/tasks?type=dev-tickets&view=board');
      await page.waitForSelector('[data-task-key="tD17"] [data-step-age]', {timeout: t(8000)});
      assert.equal((await page.locator('[data-task-key="tD17"] [data-step-age]').innerText()).trim(), '2h in step', 'the server clock shows');
      assert.deepEqual(errors, [], 'late clock: no page errors');
      await context.close();
    }

    // F15: a card's late time comes from the Late clocks of the tickets on screen, asked for by id. A pipeline-wide list
    // is capped at 500 tickets; this one has 600 late tickets before D17, so D17 is past the cap and shows only when the
    // page asks for its id.
    {
      const {context, page, errors} = await newPage(browser, {width: 1440, height: 940});
      const filler = Array.from({length: 600}, (_, i) => ({task_id: 'X' + i, title: 'Filler ' + i, stage: 'Build', hours: 50, limit_hours: 48, since: new Date().toISOString()}));
      const state = await install(page, {late: [...filler, {task_id: 'D17', title: 'Sim stuck checks', stage: 'Build', hours: 3, limit_hours: 1, since: new Date().toISOString()}]}) as RailState;
      await page.goto('http://tico-ui.test/#/tasks?type=dev-tickets&view=board');
      await page.waitForSelector('[data-task-key="tD17"] [data-step-age]', {timeout: t(8000)});
      assert.equal((await page.locator('[data-task-key="tD17"] [data-step-age]').innerText()).trim(), '3h in step', 'the late time shows past the cap');
      assert.ok(state.lateAsks.some(ids => ids && ids.split(',').includes('D17')), 'the page asked for the ticket on screen by id');
      assert.ok(state.lateAsks.every(ids => ids === null || ids.split(',').length <= 100), 'each request asks for at most 100 ids');
      assert.deepEqual(errors, [], 'late by id: no page errors');
      await context.close();
    }

    // E-extra: a phone shows every pipeline one stage at a time, with requirements or without (no old board).
    for (const [has, label] of [[false, 'no requirements'], [true, 'with requirements']] as [boolean, string][]) {
      const {context, page, errors} = await newPage(browser, {width: 390, height: 844}, {hasTouch: true});
      await install(page, {noRequirements: !has});
      await page.goto('http://tico-ui.test/#/tasks?type=dev-tickets&view=board');
      await page.waitForSelector('#task-body [data-task-key]', {state: 'attached', timeout: t(8000)});
      assert.equal(await page.locator('#task-body .pipe-stage').count() > 0, true, label + ': the one-stage phone view');
      assert.equal(await page.locator('#task-body .pchips [data-pipe-chip]').count() > 0, true, label + ': stage chips');
      assert.equal(await page.locator('#task-body .bcol h2').count(), 0, label + ': no board columns');
      // Finished stages are chips on a phone too, not folded away.
      await page.evaluate(() => { const type = TASK_TYPES.find(type => type.id === 'dev-tickets')!; type.steps.push({id: 'done-step', name: 'Done', status: 'done', position: 9, type_id: type.id}); tasksRender(TASKS_ST); });
      await page.waitForSelector('#task-body .pchips [data-pipe-chip="done-step"]', {timeout: t(5000)});
      // The picker names a pipeline by its own current name, never a fixed "Dev tickets" or "Roadmap".
      assert.equal(await page.evaluate(() => { TASK_TYPES.find(type => type.id === 'dev-tickets')!.name = 'Engineering'; return taskPipelineLabel('dev-tickets'); }), 'Engineering', label + ': picker label');
      assert.deepEqual(errors, [], label + ': no page errors');
      await context.close();
    }

    // FHI9: on the phone's one stage, Select all selects that stage's cards.
    {
      const {context, page, errors} = await newPage(browser, {width: 390, height: 844}, {hasTouch: true});
      await install(page);
      await page.goto('http://tico-ui.test/#/tasks?type=dev-tickets&view=board');
      await page.waitForSelector('#task-body .pipe-stage .bcol-all', {timeout: t(8000)});
      await page.locator('#task-body .pipe-stage .bcol-all').click();
      await page.waitForSelector('#task-body.has-sel', {timeout: t(5000)});
      assert.ok(await page.locator('#task-body .pipe-stage [data-task-key].sel').count() >= 2, 'Select all takes the stage cards');
      assert.deepEqual(errors, [], 'select all: no page errors');
      await context.close();
    }
  } finally {
    await browser.close();
  }
  console.log('pipeline-rail: ok');
})().catch(error => { console.error(error); process.exit(1); });
