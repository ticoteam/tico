import type {Route} from 'playwright';
// Offline browser check of Flow's top bar and end (ui/app/decide.ts), against a fake of the new routes, on a desktop:
// - the crumb's menu lists only the places where something waits, and a row switches the walk to that place;
// - an ask has Snooze (an icon, for you alone: ask-screen-snooze.cts) and no Skip;
// - Snooze on your own task opens the same menu as an ask's (1 hour, 1 day, 1 week, 1 month, Other) and sends a
//   moment: each an exact span from now, late in the evening in Los Angeles too (a clock and zone faked here); Undo
//   unsnoozes; the end counts it;
// - the mic turns the mark into the wave and back, and nothing is sent;
// - decideLineSet puts Tico's line in the bar and makes its pick the main button; null puts it back.
// FLOW_SHOTS=<folder> saves the screen with a line on it, your own task's snooze choices and the undo after one.
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
const question = (n: number, stage: string, line: string) => ({kind: 'ask', id: `r${n}`, task_id: `t${n}`, requirement_id: `r${n}`, line,
  since: `2026-11-09T1${n}:00:00Z`, task: brief(`t${n}`, stage),
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
    const own = mine(3, 'build', Q[2]!);
    let needs: Record<string, unknown>[] = [question(1, 'spec', Q[0]!), question(2, 'build', Q[1]!), own];
    // 23:30 on Nov 13 in Los Angeles: a bare date for tomorrow would be a midnight the team's zone may already be past.
    const page = await browser.newPage({viewport: {width: 1200, height: 900}, serviceWorkers: 'block', timezoneId: 'America/Los_Angeles'});
    await page.clock.setFixedTime(new Date('2026-11-14T07:30:00Z'));
    page.on('pageerror', (error: Error) => errors.push(error.message));
    await page.route('**/*', (route: Route) => {
      const r = route.request(), url = new URL(r.url()), p = url.pathname;
      const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'http://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      // The icon font, so the shots show icons, not their names.
      if (p.startsWith('/vendor/fonts/')) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(path.join(__dirname, '..', p))});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      if (p === '/api/me') return json({...me, config});
      if (p === '/api/v2/config') return json(config);
      if (p === '/api/employees') return json([]);
      if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
      if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: String(needs.length), next: null});
      if (p === '/api/v2/flow/opinions') return json({opinions: []});   // Tico has none: the screen is as without it
      const read = r.method() === 'GET' ? p.match(/^\/api\/v2\/tasks\/(t\d)$/) : null;
      if (read) return json({task: {...brief(read[1]!, 'build', read[1] !== 't3'), created_at: '2026-11-09T09:00:00Z', attachments: [], links: [],
        body: 'The release goes out Friday. The note lists what changed for customers.'}});
      if (r.method() !== 'GET' && p.startsWith('/api/')) {
        writes.push({p, body: JSON.parse(r.postData() || '{}') as Record<string, unknown>});
        const snooze = p.match(/^\/api\/v2\/tasks\/(t\d)\/snooze$/);
        if (snooze) {
          needs = needs.filter(n => n['task_id'] !== snooze[1]);
          return json({task: {...brief(snooze[1]!, 'build', false), snoozed: true}});
        }
        if (p === '/api/v2/tasks/t3/unsnooze') { needs = [...needs, own]; return json({task: brief('t3', 'build', false)}); }
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('http://tico-ui.test/#/tasks');
    await page.locator('[data-need="r1"]').waitFor();
    await page.locator('#task-play').click();
    const dialog = page.locator('#need-card');
    const left = () => page.evaluate('decideLive(DEC).length') as Promise<number>;
    const shot = async (name: string) => {
      if (!process.env.FLOW_SHOTS) return;
      await page.waitForTimeout(700);
      await page.screenshot({path: path.join(process.env.FLOW_SHOTS, `${name}-desktop.png`)});
      await page.setViewportSize({width: 390, height: 844});
      await page.waitForTimeout(300);
      await page.screenshot({path: path.join(process.env.FLOW_SHOTS, `${name}-phone.png`)});
      await page.setViewportSize({width: 1200, height: 900});
    };
    await dialog.locator('.dc-q', {hasText: Q[0]!}).waitFor();
    assert.equal(await left(), 3, 'All mine walks everything');
    assert.equal(await dialog.locator('[data-dc="snooze"][aria-label="Snooze"]').count(), 1, 'an ask: Snooze');
    assert.equal(await dialog.locator('[data-dc="skip"]').count(), 0, 'an ask: no Skip');

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

    // Tico's line: the bar shows it; a question's choices stay as drawn, none looking chosen before a tap; null takes
    // the line back.
    await page.evaluate(`decideLineSet('r2', {line: 'Yes. It matches the plan.', pick: 'No'})`);
    assert.equal(await dialog.locator('.dc-say').textContent(), 'Yes. It matches the plan.');
    const main = (choice: string) => dialog.locator(`[data-dc-choice="${choice}"]`).evaluate(b => b.matches('.primary, .dc-rec') || b === document.activeElement);
    assert.equal(await main('No'), false);
    assert.equal(await main('Yes'), false);
    await shot('flow');
    await page.evaluate(`decideLineSet('r2', null)`);
    assert.equal(await dialog.locator('.dc-say').textContent(), '');
    assert.equal(await main('No'), false);

    // Snooze on your own task. 1 day and 1 week: a day and seven days from now, each undone.
    const snooze = async (what: string, until: string) => {
      await dialog.locator('.dc-q', {hasText: 'Task t3'}).waitFor();   // your own task's heading is its title
      await dialog.locator('[data-dc="snooze"]').click();
      assert.deepEqual(await dialog.locator('.dc-snz [role="menuitem"]').allTextContents(), ['1 hour', '1 day', '1 week', '1 month', 'Other']);
      if (what === 'snooze-day') await shot('flow-snooze');
      await dialog.locator(`[data-dc="${what}"]`).click();
      await dialog.locator('.dc-bar:not([hidden])').waitFor();
      if (what === 'snooze-day') await shot('undo');
      assert.deepEqual(writes, [{p: '/api/v2/tasks/t3/snooze', body: {until, version: 3}}], what);
      await dialog.locator('.dc-q', {hasText: Q[1]!}).waitFor();
      assert.equal(await left(), 1, 'the snoozed task is out of the walk');
      writes.length = 0;
    };
    const undo = async () => {
      await dialog.locator('.dc-bar [data-dc="undo"]').click();
      await page.waitForFunction('decideLive(DEC).length === 2');
      assert.deepEqual(writes.map(w => w.p), ['/api/v2/tasks/t3/unsnooze'], 'Undo unsnoozes');
      writes.length = 0;
      await dialog.locator('[data-dc="next"]').click();
    };
    await dialog.locator('[data-dc="next"]').click();
    await snooze('snooze-day', '2026-11-15T07:30:00.000Z');
    await undo();
    await snooze('snooze-week', '2026-11-21T07:30:00.000Z');
    await undo();
    await snooze('snooze-month', '2026-12-14T07:30:00.000Z');
    await undo();
    // 1 hour: an hour on; the end counts it.
    await snooze('snooze-hour', '2026-11-14T08:30:00.000Z');
    assert.equal(await left(), 1, 'the snoozed task is out of the walk');
    await dialog.locator('[data-dc-choice="Yes"]').click();
    await dialog.locator('.dc-done').waitFor();
    assert.match(await dialog.locator('.dc-done').innerText(), /\b1 snoozed\b/);

    assert.deepEqual(errors, []);
    await page.close();
    console.log('PASS: the menu switches the walk; Snooze takes your task out and the end counts it; the mic toggles; Tico\'s line picks.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
