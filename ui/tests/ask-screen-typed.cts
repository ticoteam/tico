import type {Route} from 'playwright';
// Offline browser check of a stage's typed answer on the ask screen (docs/pipelines-v3.md S3, kind 6): the item asks
// its question; a number is a whole-number box with its range, and a value the server refuses stays typed under the
// server's own line until a good one is sent; a choice is its buttons, with no free text; text is a box and Send.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const brief = (id: string, title: string) => ({id, title, number: 1, pipeline_id: 'eng', stage_id: 'build', status: 'open',
  owner_id: 'bot:builder', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: {state: 'person', who: 'human:ana', line: 'Waiting on Ana', since: '2026-11-10T10:00:00Z', until: null, ask: true, requirement_id: null},
  version: 4});
type Typed = {name: string; type: 'text' | 'number' | 'choice'; min: number | null; max: number | null; question: string};
// As the task's read gives a stage's answer (task_calls.task_view): `typed`, choices for a choice, may "answer" only.
const ask = (rid: string, line: string, typed: Typed, choices: string[] | null = null) => ({requirement_id: rid, kind: 'answer', line,
  who: 'human:ana', asked_by: null, choices, typed, may: ['answer'], met: false, shows: [], standing: line, answer: null});
const item = (rid: string, task: string, line: string, typed: Typed, choices: string[] | null = null, since = '10') =>
  ({kind: 'ask', id: rid, task_id: task, requirement_id: rid, line, since: `2026-11-10T${since}:00:00Z`, task: brief(task, line), ask: ask(rid, line, typed, choices)});
const pipeline = {id: 'eng', name: 'Engineering', goal: '', prefix: 'E', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [{id: 'build', name: 'Build', purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
    expected_bots_only: false, on_enter: {}, requirements: []}],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [];
  let needs = [
    item('r1', 't1', 'Confidence', {name: 'confidence', type: 'number', min: 0, max: 100, question: 'How sure are you that it works?'}, null, '10'),
    item('r2', 't2', 'Risky', {name: 'risky', type: 'choice', min: null, max: null, question: ''}, ['Yes', 'No'], '11'),
    item('r3', 't3', 'Rollback plan', {name: 'rollback', type: 'text', min: null, max: null, question: 'How do we roll it back?'}, null, '12'),
  ];
  const writes: {p: string; body: Record<string, unknown>}[] = [];
  try {
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
      const answer = p.match(/^\/api\/v2\/tasks\/(t\d)\/asks\/(r\d)\/answer$/);
      if (answer && r.method() === 'POST') {
        const body = JSON.parse(r.postData() || '{}') as Record<string, unknown>;
        writes.push({p, body});
        // As answers.answer refuses a value that does not fit: the rule's one line.
        if (answer[2] === 'r1' && body['value'] !== '80')
          return json({error: 'task_requirement', detail: 'Confidence is a whole number from 0 to 100', missing: [], next: [], version: 4}, 409);
        needs = needs.filter(n => n.id !== answer[2]);
        return json({task: brief(answer[1]!, ''), answer_id: 'a' + answer[2], requirement_id: answer[2]});
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('http://tico-ui.test/#/tasks');
    await page.locator('[data-need="r1"]').waitFor();
    await page.locator('#task-play').click();
    const dialog = page.locator('#need-card');
    await dialog.locator('.dc-q').waitFor();

    // A number: its question, a whole-number box with its range, Send off until something is typed.
    assert.equal(await dialog.locator('.dc-q').innerText(), 'How sure are you that it works?');
    const box = dialog.locator('input#dc-other');
    assert.deepEqual([await box.getAttribute('type'), await box.getAttribute('min'), await box.getAttribute('max'), await box.getAttribute('step')],
      ['number', '0', '100', '1']);
    assert.equal(await dialog.locator('.dc-notes > span').innerText(), '0–100');
    assert.equal(await dialog.locator('[data-dc-choice]').count(), 0);
    const send = dialog.locator('[data-dc="other"]');
    assert.equal(await send.isDisabled(), true);
    await box.fill('120');
    await send.click();
    await dialog.locator('.err[role="alert"]').waitFor();
    assert.equal(await dialog.locator('.err[role="alert"]').innerText(), 'Confidence is a whole number from 0 to 100', "the server's line, as is");
    assert.equal(await box.inputValue(), '120', 'what was typed stays');
    assert.deepEqual(writes[0], {p: '/api/v2/tasks/t1/asks/r1/answer', body: {value: '120', version: 4}});
    await box.fill('80');
    await box.press('Enter');
    await dialog.locator('.dc-bar:not([hidden])').waitFor();
    assert.deepEqual(writes[1], {p: '/api/v2/tasks/t1/asks/r1/answer', body: {value: '80', version: 4}});

    // A choice: its buttons and nothing to type; with no question, its title is asked.
    await page.waitForFunction(() => document.querySelector('#need-card .dc-q')?.textContent === 'Risky');
    assert.deepEqual(await dialog.locator('[data-dc-choice]').allInnerTexts(), ['Yes', 'No']);
    assert.equal(await dialog.locator('#dc-other').count(), 0, 'a choice is strict');
    await dialog.locator('[data-dc-choice="No"]').click();
    await page.waitForFunction(() => document.querySelector('#need-card .dc-q')?.textContent === 'How do we roll it back?');
    assert.deepEqual(writes[2], {p: '/api/v2/tasks/t2/asks/r2/answer', body: {value: 'No', version: 4}});

    // Text: a box and Send.
    assert.equal(await dialog.locator('textarea#dc-other').count(), 1);
    await dialog.locator('textarea#dc-other').fill('Revert the PR and redeploy');
    await dialog.locator('[data-dc="other"]').click();
    await dialog.getByText('All caught up').waitFor();
    assert.deepEqual(writes[3], {p: '/api/v2/tasks/t3/asks/r3/answer', body: {value: 'Revert the PR and redeploy', version: 4}});
    assert.equal(await dialog.locator('[data-dc="up"]').isHidden(), true, 'a stage answer takes no thumb');

    assert.deepEqual(errors, []);
    console.log('PASS: a number, a choice and text are asked as typed; a refused number shows the server line and stays typed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
