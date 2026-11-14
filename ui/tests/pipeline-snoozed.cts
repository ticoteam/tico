import type {Page, Route} from 'playwright';
// Offline browser check of snoozed tasks in a pipeline's view (task-pipelines.ts): in the desktop columns, the desktop
// list and the phone's stage (390 px), a snoozed task stays in its stage after every task that is not, dimmed, with the
// day it wakes (or Snoozed when its snooze has no end) and a moon that is its one button: Unsnooze (its label and title,
// no words beside it, a 40 px tap area on a phone). One click wakes it without opening it; a refusal says why. A snoozed
// task holds no slot, so the stage's number leaves it out. The fake server answers `snoozed=hide` as backend/hubdb.py
// does: it leaves snoozed tasks out.
// Run: npm run build:ui && node ui/tests/pipeline-snoozed.cts
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const req = {kind: 'owner_done', settings: {}, can_push_past: true, who_oks: 'none', person_id: null, line: ''};
const stage = (id: string, name: string, limit: number | null = null) => ({id, name, purpose: '', owner: null, by_hand: false,
  task_limit: limit, expected_minutes: null, expected_bots_only: false, on_enter: {}, requirements: [req]});
const pipeline = {id: 'eng', name: 'Engineering', goal: '', prefix: 'E', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null, stages: [stage('spec', 'Spec'), stage('build', 'Build', 3)],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};
const snooze = (until: string | null) => ({state: 'snoozed', who: 'human:ana', line: 'Back after the launch', since: '2026-11-12T09:00:00Z', until});
const brief = (id: string, title: string, turn: Record<string, unknown> | null = null) => ({id, title, number: 1, pipeline_id: 'eng',
  stage_id: 'build', status: 'open', owner_id: 'human:ana', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null,
  snoozed: turn?.['state'] === 'snoozed', turn, version: 1, created_at: '2026-11-09T10:00:00Z', updated_at: '2026-11-09T10:00:00Z'});
// The queue order the server gives: snoozed tasks among the others.
const TASKS = [brief('s1', 'Pricing page copy', snooze('2026-11-14T08:00:00Z')), brief('a1', 'Saved carts'),
  brief('s2', 'Old importer', snooze(null)), brief('a2', 'Refund flow')];
const AWAKE = ['a1', 'a2'], ASLEEP = ['s1', 's2'];
const woke: string[] = [];

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
    if (p === '/api/v2/tasks' && r.method() === 'GET') {
      const snoozed = url.searchParams.get('snoozed');
      return json({tasks: TASKS.filter(t => snoozed === 'hide' ? !t.snoozed : snoozed === 'only' ? t.snoozed : true)});
    }
    const wake = p.match(/^\/api\/v2\/tasks\/([^/]+)\/unsnooze$/);
    if (wake && r.method() === 'POST') {
      woke.push(wake[1]!);
      if (wake[1] === 's2') {
        return json({error: 'idea_filed_snoozed', detail: "A bot's idea stays snoozed until a person unsnoozes it.", missing: []}, 409);
      }
      const t = TASKS.find(x => x.id === wake[1])!;
      return json({task: {...t, snoozed: false, turn: null, version: 2}});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
}

/** The rows or boxes of the Build stage, as drawn: id, whether dimmed, the sleep mark's label and title and its own text,
 * and the row's text. */
async function drawn(page: Page, rows: string) {
  await page.locator(`${rows}[data-task="a1"]`).waitFor();
  return page.locator(rows).evaluateAll(els => els.map(el => {
    const mark = el.querySelector('.pv-sleep'), title = el.querySelector('.tk-title');
    return {id: (el as HTMLElement).dataset['task'], dim: el.classList.contains('pv-snoozed'),
      color: title ? getComputedStyle(title).color + '/' + getComputedStyle(title).opacity : '',
      rowTip: el.getAttribute('data-tip'), label: mark?.getAttribute('aria-label') ?? null, tip: mark?.getAttribute('title') ?? null, tag: mark?.tagName ?? null,
      buttons: el.querySelectorAll('button, a, input').length,
      markText: mark?.textContent ?? null, icon: !!mark?.querySelector('svg'), text: (el as HTMLElement).innerText.replace(/\s+/g, ' ').trim()};
  }));
}

function check(where: string, rows: Awaited<ReturnType<typeof drawn>>): void {
  assert.deepEqual(rows.map(r => r.id), [...AWAKE, ...ASLEEP], `${where}: the snoozed tasks sit after all the others`);
  const by = Object.fromEntries(rows.map(r => [r.id, r]));
  for (const id of AWAKE) assert.equal(by[id]!.label, null, `${where}: ${id} is awake, with no sleep mark`);
  for (const id of ASLEEP) {
    const r = by[id]!;
    assert.ok(r.dim, `${where}: ${id} is dimmed`);
    assert.notEqual(r.color, by['a1']!.color, `${where}: ${id}'s title is drawn dimmer than an awake one`);
    assert.equal(r.tag, 'BUTTON', `${where}: ${id}'s moon is a button`);
    assert.equal(r.buttons, 1, `${where}: ${id} has no other button`);
    assert.equal(r.label, 'Unsnooze', `${where}: the moon says Unsnooze to a screen reader`);
    assert.equal(r.tip, 'Unsnooze', `${where}: and on hover`);
    assert.ok(r.icon, `${where}: ${id}'s moon is an icon`);
    assert.equal(r.markText, '', `${where}: ${id}'s moon has no words`);
    assert.match(r.rowTip ?? '', /Back after the launch/, `${where}: ${id}'s tip keeps the snooze's why`);
  }
  assert.match(by['s1']!.text, /Nov 14$/, `${where}: the wake day shows`);
  assert.match(by['s2']!.text, /Snoozed$/, `${where}: a snooze with no end says Snoozed`);
  assert.ok(!rows.some(r => /On ice/.test(r.text)), `${where}: On ice is said nowhere`);
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [];
  try {
    const desk = await browser.newPage({viewport: {width: 1200, height: 900}, serviceWorkers: 'block', locale: 'en-US', timezoneId: 'UTC'});
    await serve(desk, errors);
    await desk.goto('http://tico-ui.test/#/tasks?pipeline=eng&layout=columns');
    check('columns', await drawn(desk, '[data-pv-col="build"] .tk-card'));
    // Build has a limit of 3: the two snoozed tasks hold no slot.
    assert.equal((await desk.locator('[data-pv-col="build"] .pv-stage-n').innerText()).trim(), '2/3', 'snoozed tasks hold no slot');

    await desk.goto('http://tico-ui.test/#/tasks?pipeline=eng&layout=list');
    await desk.locator('.pv-group').first().waitFor();
    check('list', await drawn(desk, '.pv-group[aria-label="Build"] .tk-row'));

    const phone = await browser.newPage({viewport: {width: 390, height: 844}, serviceWorkers: 'block', locale: 'en-US', timezoneId: 'UTC',
      isMobile: true, hasTouch: true});
    await serve(phone, errors);
    await phone.goto('http://tico-ui.test/#/tasks?pipeline=eng');
    await phone.locator('.pv-is-phone').waitFor();
    await phone.locator('[data-pv-tab="build"]').click();
    check('phone', await drawn(phone, '.pv-phone .tk-card'));
    const wide = await phone.evaluate(() => document.documentElement.scrollWidth);
    assert.ok(wide <= 390, `the phone page does not scroll sideways (${wide} px)`);
    const tap = await phone.locator('.pv-phone [data-pv-unsnooze="s1"]').evaluate(el => {
      const after = getComputedStyle(el, '::after');
      return [parseFloat(after.width), parseFloat(after.height)];
    });
    assert.deepEqual(tap, [40, 40], 'the moon is a 40 px tap area on a phone');

    // One click on the moon wakes it, and does not open the task. A refusal says the engine's reason above the stages.
    await desk.goto('http://tico-ui.test/#/tasks?pipeline=eng&layout=columns');
    await desk.locator('[data-pv-col="build"] [data-pv-unsnooze="s1"]').click();
    await desk.waitForFunction(() => !document.querySelector('[data-pv-col="build"] .pv-snoozed[data-task="s1"]'));
    assert.deepEqual(woke, ['s1'], 'one click, one unsnooze');
    assert.equal(await desk.locator('dialog[open]').count(), 0, 'the task is not opened');
    await desk.locator('[data-pv-col="build"] [data-pv-unsnooze="s2"]').click();
    await desk.locator('.pv', {hasText: "A bot's idea stays snoozed until a person unsnoozes it."}).waitFor();
    assert.equal(await desk.locator('dialog[open]').count(), 0, 'nor on a refusal');

    assert.deepEqual(errors, []);
    console.log('pipeline snoozed: ok');
  } finally {
    await browser.close();
  }
})().catch((error: unknown) => { console.error(error); process.exit(1); });
