import type {Page, Route} from 'playwright';
// Offline browser check of dropping a task on another task's card on the Tasks board (docs/tasks.md, "Merge and make a
// subtask"): held over a card, that card is outlined and its column is not; letting go opens the dialog with Make it a
// subtask and Merge, the owner choice only while Merge is picked when the owners differ (the dropped-on task's owner
// first), and what a merge moves. The button says what it does. A refusal stays in the dialog with the engine's line; a
// choice the server says cannot be done is disabled with its line. A card the person may not change is not outlined and
// a drop there does nothing. Letting go over a column's empty space still moves the stage.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const config = {version: '0.1.0', app_name: 'Tico', local: true};
const req = {kind: 'owner_done', settings: {}, can_push_past: true, who_oks: 'none', person_id: null, line: ''};
const stage = (id: string, name: string) => ({id, name, purpose: '', owner: null, by_hand: false, task_limit: null,
  expected_minutes: null, expected_bots_only: false, on_enter: {}, requirements: [req]});
const pipeline = (mayChange: boolean) => ({id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana',
  private: false, factory_id: null, preset: null, settings_version: 1, retired_at: null, stages: [stage('spec', 'Spec'), stage('build', 'Build')],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: mayChange});
const brief = (id: string, number: number, title: string, stageId: string, owner: string) => ({id, title, number, pipeline_id: 'product',
  stage_id: stageId, status: 'open', owner_id: owner, requester_id: owner, assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: null, version: 1, created_at: '2026-11-09T10:00:00Z', updated_at: '2026-11-09T10:00:00Z'});
const end = (t: ReturnType<typeof brief>, name: string) => ({id: t.id, number: t.number, title: t.title, owner_id: t.owner_id, owner: name});

interface Seen { checks: string[]; merges: {id: string; body: Record<string, unknown>}[]; parents: {id: string; body: Record<string, unknown>}[]; moves: string[] }

async function open(browser: import('playwright').Browser, me: Record<string, unknown>, mayChange: boolean,
  tasks: ReturnType<typeof brief>[], answer: (id: string, into: string) => Record<string, unknown>, refuseMerge: () => string | null,
  seen: Seen, errors: string[]): Promise<Page> {
  const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block'});
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
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'cara', name: 'Cara'}], teams: {}});
    if (p === '/api/v2/pipelines') return json({pipelines: [pipeline(mayChange)]});
    if (p === '/api/v2/pipelines/product') return json({pipeline: pipeline(mayChange)});
    if (p === '/api/v2/factories') return json({factories: [], pipelines: []});
    if (p === '/api/v2/tasks' && r.method() === 'GET') return json({tasks});
    const merge = p.match(/^\/api\/v2\/tasks\/([^/]+)\/merge$/);
    if (merge && r.method() === 'GET') {
      seen.checks.push(`${merge[1]}>${url.searchParams.get('into')}`);
      return json(answer(merge[1]!, url.searchParams.get('into') ?? ''));
    }
    if (merge && r.method() === 'POST') {
      const body = JSON.parse(r.postData() || '{}') as Record<string, unknown>;
      seen.merges.push({id: merge[1]!, body});
      const refused = refuseMerge();
      if (refused) return json({error: 'identity', detail: refused}, 403);
      const kept = tasks.find(x => x.id === body['into'])!, gone = tasks.find(x => x.id === merge[1])!;
      tasks.splice(tasks.indexOf(gone), 1);
      if (body['owner']) kept.owner_id = String(body['owner']);
      return json({task: kept, closed: {...gone, status: 'closed'}, note: ''});
    }
    const parent = p.match(/^\/api\/v2\/tasks\/([^/]+)\/parent$/);
    if (parent && r.method() === 'POST') {
      seen.parents.push({id: parent[1]!, body: JSON.parse(r.postData() || '{}') as Record<string, unknown>});
      return json({task: tasks.find(x => x.id === parent[1])});
    }
    const move = p.match(/^\/api\/v2\/tasks\/([^/]+)\/move$/);
    if (move && r.method() === 'POST') {
      const t = tasks.find(x => x.id === move[1])!, body = JSON.parse(r.postData() || '{}') as Record<string, unknown>;
      seen.moves.push(`${t.id}>${String(body['to'])}`);
      Object.assign(t, {stage_id: 'build', version: t.version + 1});
      return json({task: t});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/#/tasks?pipeline=product&layout=columns');
  await page.locator('.tk-card[data-task]').first().waitFor();
  return page;
}

/** Holds task `id` over `to` (a card's task id, or a column's empty space with `col:`), in steps as a person would.
    Returns while still held, so the outline can be read; `up()` lets go. */
async function hold(page: Page, id: string, to: string): Promise<() => Promise<void>> {
  const boxes = await page.waitForFunction(([t, target]) => {
    const a = document.querySelector(`.tk-card[data-task="${t}"]`)?.getBoundingClientRect();
    const b = (target!.startsWith('col:') ? document.querySelector(`[data-pv-col="${target!.slice(4)}"]`)
      : document.querySelector(`.tk-card[data-task="${target}"]`))?.getBoundingClientRect();
    return a?.width && b?.width ? {from: {x: a.x, y: a.y, height: a.height}, to: {x: b.x, y: b.y, width: b.width, height: b.height}} : null;
  }, [id, to]);
  const {from, to: box} = await boxes.jsonValue() as {from: {x: number; y: number; height: number}; to: {x: number; y: number; width: number; height: number}};
  const y = to.startsWith('col:') ? box.y + box.height - 40 : box.y + box.height / 2;
  await page.mouse.move(from.x + 20, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(from.x + 40, from.y + from.height / 2, {steps: 3});
  await page.mouse.move(box.x + box.width / 2, y, {steps: 8});
  return async () => { await page.mouse.up(); };
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [];
  try {
    // Ana moves tasks. #51 (Cara's) is the duplicate of #121 (Ana's); #7 is Ana's too.
    const dup = brief('t51', 51, 'Annual refunds broken', 'spec', 'human:cara');
    const kept = brief('t121', 121, 'Refunds fail for annual plans', 'spec', 'human:ana');
    const other = brief('t7', 7, 'Refund email copy', 'spec', 'human:ana');
    const copy = brief('t8', 8, 'Refund FAQ', 'spec', 'human:ana');
    const tasks = [kept, dup, other, copy];
    const seen: Seen = {checks: [], merges: [], parents: [], moves: []};
    let refusal: string | null = 'Cara cannot take tasks from you: ask them first.';
    const moves = {comments: 7, asks: ['Which plan did it fail on?', 'Is it every annual customer?'], subtasks: 1, files: 2, followers: 1,
      requesters: 1, labels: 1, links: 0, description: true, due: null};
    const answer = (id: string, into: string) => {
      const a = tasks.find(x => x.id === id)!, b = tasks.find(x => x.id === into)!;
      const name = (t: ReturnType<typeof brief>) => t.owner_id === 'human:ana' ? 'Ana' : 'Cara';
      return {task: end(a, name(a)), into: end(b, name(b)), same_owner: a.owner_id === b.owner_id,
        subtask: null, merge: id === 't7' ? '#7 has an open pull request.' : null, moves};
    };
    const page = await open(browser, {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true}, true,
      tasks, answer, () => refusal, seen, errors);

    // 1. Held over a card: that card is outlined, its column is not.
    let up = await hold(page, 't51', 't121');
    assert.equal(await page.locator('.tk-card[data-task="t121"].drop-on').count(), 1, 'the card is outlined');
    assert.equal(await page.locator('.pv-col.over').count(), 0, 'the column is not');
    await up();
    const dlg = page.locator('#tb-drop');
    await dlg.locator('[name="choice"][value="subtask"]').waitFor();
    assert.deepEqual(seen.checks, ['t51>t121'], 'the dialog reads both tasks before anyone confirms');
    assert.equal(await page.locator('.tk-card.drop-on').count(), 0, 'the outline goes when it is let go');

    // 2. Two choices; the owners differ, so the owner choice shows only while Merge is picked, the kept task's owner first.
    assert.equal((await dlg.locator('h2').innerText()).trim(), 'Drop #51 on #121');
    const go = dlg.locator('[data-tb-go]');
    assert.equal((await go.innerText()).trim(), 'Make subtask');
    assert.ok(await dlg.locator('[data-tb-owner]').isHidden(), 'no owner choice for a subtask');
    await dlg.locator('label:has([value="merge"])').click();
    assert.equal((await go.innerText()).trim(), 'Merge');
    assert.ok(await dlg.locator('[data-tb-owner]').isVisible(), 'the owner choice shows with Merge');
    assert.equal(await dlg.locator('[name="owner"]:checked').getAttribute('value'), 'human:ana', 'the dropped-on task keeps its owner by default');
    // 4. What moves, counted from the real task.
    const what = await dlg.locator('[data-tb-moves] li').allInnerTexts();
    assert.ok(what.some(x => x.startsWith('7 comments: stay on #51')), what.join(' | '));
    assert.ok(what.some(x => x.startsWith('2 open asks: end with #51')), what.join(' | '));
    assert.ok(what.some(x => x.startsWith('1 open subtask: move under #121')), what.join(' | '));
    assert.ok(what.includes('#51: closed as a duplicate of #121'), what.join(' | '));
    if (process.env['TICO_SHOTS']) await dlg.screenshot({path: `${process.env['TICO_SHOTS']}/pipeline-drop-merge.png`});

    // 5. The other owner, refused: nothing merges and the dialog says why.
    await dlg.locator('label:has([name="owner"][value="human:cara"])').click();
    await go.click();
    await page.waitForFunction(() => document.querySelector('#tb-drop [data-tb-err]')?.textContent);
    assert.equal(await dlg.locator('[data-tb-err]').innerText(), 'Cara cannot take tasks from you: ask them first.');
    assert.deepEqual(seen.merges[0], {id: 't51', body: {into: 't121', owner: 'human:cara'}});
    assert.ok(await dlg.isVisible(), 'the dialog stays open');
    assert.equal(await page.locator('.tk-card[data-task="t51"]').count(), 1, 'nothing merged');
    // Then it goes through: the duplicate leaves the board.
    refusal = null;
    await go.click();
    await page.waitForFunction(() => !document.querySelector('#tb-drop')?.hasAttribute('open'));
    assert.deepEqual(seen.merges[1], {id: 't51', body: {into: 't121', owner: 'human:cara'}});
    await page.waitForFunction(() => !document.querySelector('.tk-card[data-task="t51"]'));
    assert.ok(!(await page.evaluate(() => location.hash)).startsWith('#/task/'), 'the drop opened no task');

    // 2. The same owner: no owner choice at all.
    up = await hold(page, 't7', 't8');
    await up();
    await page.waitForFunction(() => document.querySelector('#tb-drop h2')?.textContent === 'Drop #7 on #8');
    assert.equal(await dlg.locator('[data-tb-owner]').count(), 0, 'same owner: no owner choice');
    await dlg.getByRole('button', {name: 'Cancel'}).click();
    assert.equal(seen.parents.length + seen.merges.length, 2, 'Cancel changes nothing');

    // 7. Make it a subtask (#121 is Cara's now, #7 Ana's): no owner choice while it is picked.
    up = await hold(page, 't7', 't121');
    await up();
    await page.waitForFunction(() => document.querySelector('#tb-drop h2')?.textContent === 'Drop #7 on #121');
    assert.ok(await dlg.locator('[data-tb-owner]').isHidden(), 'a subtask keeps its owner: no owner choice');
    // 8. A choice the server refuses is disabled, with its line.
    assert.ok(await dlg.locator('[name="choice"][value="merge"]').isDisabled());
    assert.ok((await dlg.locator('label:has([value="merge"])').innerText()).includes('#7 has an open pull request.'));
    await go.click();
    await page.waitForFunction(() => !document.querySelector('#tb-drop')?.hasAttribute('open'));
    assert.deepEqual(seen.parents, [{id: 't7', body: {parent: 't121'}}]);

    // 1. Letting go over a column's empty space still moves the stage.
    up = await hold(page, 't7', 'col:build');
    assert.equal(await page.locator('.pv-col.over[data-pv-col="build"]').count(), 1);
    await up();
    await page.waitForFunction(() => document.querySelector('[data-pv-col="build"] .tk-card[data-task="t7"]'));
    assert.deepEqual(seen.moves, ['t7>next'], 'moved to the next stage, as today');
    await page.close();

    // 9. Cara, who moves no tasks and changes no pipeline, holds her task over Ana's: no outline, and the drop does nothing.
    const hers = brief('h1', 60, 'Cara’s task', 'spec', 'human:cara'), ana = brief('h2', 61, 'Ana’s task', 'spec', 'human:ana');
    const seen2: Seen = {checks: [], merges: [], parents: [], moves: []};
    const page2 = await open(browser, {id: 'cara', name: 'Cara', email: 'cara@acme.example', role: 'human', mover: false, cloud: true}, false,
      [hers, ana], answer, () => null, seen2, errors);
    up = await hold(page2, 'h1', 'h2');
    assert.equal(await page2.locator('.tk-card.drop-on').count(), 0, 'not outlined');
    assert.equal(await page2.locator('.pv-col.over').count(), 0, 'nor its column');
    await up();
    await page2.waitForTimeout(150);
    assert.equal(await page2.locator('#tb-drop[open]').count(), 0, 'no dialog');
    assert.deepEqual(seen2, {checks: [], merges: [], parents: [], moves: []}, 'nothing was asked');

    assert.deepEqual(errors, []);
    console.log('PASS: a task dropped on a card opens Make it a subtask or Merge; the owner choice, what moves and refusals show; a column still moves.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
