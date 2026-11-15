import type {Route} from 'playwright';
// Offline browser check of Flow's top bar and end (ui/app/decide.ts), against a fake of the new routes, on a desktop:
// - the crumb's menu lists only the places where something waits, and a row switches the walk to that place;
// - Snooze on an ask whose task the person may set aside (may_snooze) stands where Skip was: Tomorrow sends tomorrow's
//   date, the ask leaves the walk, and Undo unsnoozes it and brings it back; an ask they may not snooze keeps Skip;
// - Snooze on your own task (1 hour) sends a moment, takes the item out of the walk, and the end counts it; no Skip;
// - the mic turns the mark into the wave and back, and nothing is sent;
// - decideLineSet puts Tico's line in the bar and makes its pick the main button; null puts it back.
// FLOW_SHOTS=<folder> saves the screen with a line on it.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const brief = (id: string, stage: string, ask = true) => ({id, title: `Task ${id}`, number: 1, pipeline_id: 'product', stage_id: stage, status: 'open',
  owner_id: ask ? 'bot:pm' : 'human:ana', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-09T10:00:00Z', until: null, ask, requirement_id: null},
  version: 3});
const question = (n: number, stage: string, line: string, snooze = false) => ({kind: 'ask', id: `r${n}`, task_id: `t${n}`, requirement_id: `r${n}`, line,
  since: `2026-11-09T1${n}:00:00Z`, task: brief(`t${n}`, stage), may_snooze: snooze,
  ask: {requirement_id: `r${n}`, kind: 'answer', line, who: 'human:ana', asked_by: 'bot:pm', choices: ['Yes', 'No'], may: ['answer'],
    met: false, shows: [], standing: 'waiting', answer: null}});
const mine = (n: number, stage: string, line: string) => ({kind: 'task', id: `t${n}`, task_id: `t${n}`, line, since: `2026-11-09T1${n}:00:00Z`,
  task: brief(`t${n}`, stage, false)});
const stage = (id: string, name: string) => ({id, name, purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
  expected_bots_only: false, on_enter: {}, requirements: []});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [stage('spec', 'Spec'), stage('build', 'Build'), stage('ship', 'Ship')],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};
const Q = ['Which plan do new signups get?', 'Ship on Monday?', 'Write the release note'];

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  try {
    const errors: string[] = [], writes: {p: string; body: Record<string, unknown>}[] = [];
    const first = question(1, 'spec', Q[0]!, true);
    let needs: Record<string, unknown>[] = [first, question(2, 'build', Q[1]!), mine(3, 'build', Q[2]!)];
    const page = await browser.newPage({viewport: {width: 1200, height: 900}, serviceWorkers: 'block'});
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
      if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: String(needs.length), next: null});
      if (p === '/api/v2/flow/opinions') return json({opinions: []});   // Tico has none: the screen is as without it
      if (r.method() !== 'GET' && p.startsWith('/api/')) {
        writes.push({p, body: JSON.parse(r.postData() || '{}') as Record<string, unknown>});
        const snooze = p.match(/^\/api\/v2\/tasks\/(t\d)\/snooze$/);
        if (snooze) {
          needs = needs.filter(n => n['task_id'] !== snooze[1]);
          return json({task: {...brief(snooze[1]!, 'build', false), snoozed: true}});
        }
        if (p === '/api/v2/tasks/t1/unsnooze') { needs = [first, ...needs]; return json({task: brief('t1', 'spec')}); }
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('http://tico-ui.test/#/tasks');
    await page.locator('[data-need="r1"]').waitFor();
    await page.locator('#task-play').click();
    const dialog = page.locator('#need-card');
    const left = () => page.evaluate('decideLive(DEC).length') as Promise<number>;
    await dialog.locator('.dc-q', {hasText: Q[0]!}).waitFor();
    assert.equal(await left(), 3, 'All mine walks everything');

    // Snooze on an ask the person may set aside, where Skip was: Tomorrow is tomorrow's date here. Undo brings it back.
    assert.equal(await dialog.locator('[data-dc="skip"]').count(), 0, 'no Skip where Snooze is offered');
    await dialog.locator('[data-dc="snooze"]').click();
    assert.deepEqual(await dialog.locator('.dc-more button').allTextContents(), ['1 hour', 'Tomorrow', 'Next week']);
    await dialog.locator('[data-dc="snooze-day"]').click();
    await dialog.locator('.dc-bar:not([hidden])').waitFor();
    const tomorrow = await page.evaluate(() => { const d = new Date(); d.setDate(d.getDate() + 1); return d.toLocaleDateString('en-CA'); });
    assert.deepEqual(writes, [{p: '/api/v2/tasks/t1/snooze', body: {until: tomorrow, version: 3}}]);
    await dialog.locator('.dc-q', {hasText: Q[1]!}).waitFor();
    assert.equal(await left(), 2, 'the snoozed ask is out of the walk');
    // An ask whose task they may not snooze keeps Skip.
    assert.equal(await dialog.locator('[data-dc="snooze"]').count(), 0);
    assert.equal(await dialog.locator('[data-dc="skip"]').count(), 1);
    await dialog.locator('.dc-bar [data-dc="undo"]').click();
    await page.waitForFunction('decideLive(DEC).length === 3');
    assert.equal(writes[1]!.p, '/api/v2/tasks/t1/unsnooze', 'Undo unsnoozes');
    writes.length = 0;

    // The menu: All mine, the pipeline, then only its stages where something waits (no Ship). Build walks Build's two.
    await dialog.locator('.dc-crumb').click();
    assert.deepEqual(await dialog.locator('.dc-menu [data-dc-walk]').allTextContents(), ['All mine', 'Product', 'Spec', 'Build']);
    await dialog.locator('.dc-menu [data-dc-walk="build"]').click();
    await dialog.locator('.dc-q', {hasText: Q[1]!}).waitFor();
    assert.equal(await dialog.locator('.dc-menu').isHidden(), true, 'the menu closes');
    assert.equal(await left(), 2, "the walk is Build's");
    assert.deepEqual(writes, [], 'switching sends nothing');

    // The mic: the wave stands in the mark's place, and pressing it turns voice off. Nothing is sent.
    await dialog.locator('.dc-mic').click();
    assert.equal(await dialog.locator('.dc-wave').isVisible(), true);
    assert.equal(await dialog.locator('.dc-flow').isHidden(), true);
    assert.notEqual(await dialog.locator('.dc-say').textContent(), '', 'the line says something');
    await dialog.locator('.dc-wave').click();
    assert.equal(await dialog.locator('.dc-wave').isHidden(), true);
    assert.equal(await dialog.locator('.dc-mic').isVisible(), true);
    assert.equal(await dialog.locator('.dc-say').textContent(), '');
    assert.deepEqual(writes, [], 'voice sends nothing');

    // Tico's line: the bar shows it and its pick is the main button; null takes both back.
    await page.evaluate(`decideLineSet('r2', {line: 'Yes. It matches the plan.', pick: 'No'})`);
    assert.equal(await dialog.locator('.dc-say').textContent(), 'Yes. It matches the plan.');
    const main = (choice: string) => dialog.locator(`[data-dc-choice="${choice}"]`).evaluate(b => b.matches('.primary, .dc-rec'));
    assert.equal(await main('No'), true);
    assert.equal(await main('Yes'), false);
    if (process.env.FLOW_SHOTS) {
      await page.waitForTimeout(700);
      await page.screenshot({path: path.join(process.env.FLOW_SHOTS, 'flow-desktop.png')});
      await page.setViewportSize({width: 390, height: 844});
      await page.waitForTimeout(300);
      await page.screenshot({path: path.join(process.env.FLOW_SHOTS, 'flow-phone.png')});
      await page.setViewportSize({width: 1200, height: 900});
    }
    await page.evaluate(`decideLineSet('r2', null)`);
    assert.equal(await dialog.locator('.dc-say').textContent(), '');
    assert.equal(await main('No'), false);

    // Snooze on your own task: 1 hour sends a moment about an hour out; the item leaves the walk; the end counts it.
    await dialog.locator('[data-dc="next"]').click();
    assert.equal(await dialog.locator('[data-dc="skip"]').count(), 0, 'your own task: Snooze, no Skip');
    await dialog.locator('[data-dc="snooze"]').click();
    const t0 = Date.now();
    await dialog.locator('[data-dc="snooze-hour"]').click();
    await dialog.locator('.dc-bar:not([hidden])').waitFor();
    assert.equal(writes.length, 1);
    assert.equal(writes[0]!.p, '/api/v2/tasks/t3/snooze');
    const until = Date.parse(String(writes[0]!.body['until']));
    assert.ok(Math.abs(until - t0 - 3600_000) < 60_000, `an hour out: ${String(writes[0]!.body['until'])}`);
    await dialog.locator('.dc-q', {hasText: Q[1]!}).waitFor();
    assert.equal(await left(), 1, 'the snoozed task is out of the walk');
    await dialog.locator('[data-dc-choice="Yes"]').click();
    await dialog.locator('.dc-done').waitFor();
    assert.match(await dialog.locator('.dc-done').innerText(), /\b1 snoozed\b/);

    assert.deepEqual(errors, []);
    await page.close();
    console.log('PASS: the menu switches the walk; Snooze takes your task out and the end counts it; the mic toggles; Tico\'s line picks.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
