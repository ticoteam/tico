import type {Page, Route} from 'playwright';
// Offline browser check of blocked tasks in a pipeline's view (task-pipelines.ts; spec "A blocked task shows as Blocked,
// never Snoozed", part 3). After `tico task relate 307 depends_on 304`, in the desktop columns, the desktop list and the
// phone's stage (390 px): #307 keeps full colour (not dimmed like a snoozed task), shows a stop mark and its blocker's
// label "E304" where a snoozed task shows its day, and sits after the awake tasks and before the snoozed ones; #304 shows
// "Blocks E307". A blocker with no number is named by its title, never its id, and the name opens the blocker. A
// blocked task holds no slot, so a limited stage's number leaves it out. One blocked by a task the reader may not open
// still shows the mark, with "Blocked". A blocked task that is also snoozed shows as blocked, never Snoozed.
// Run: npm run build:ui && node ui/tests/pipeline-blocked.cts
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
  factory_id: null, preset: null, settings_version: 1, retired_at: null, stages: [stage('spec', 'Spec'), stage('build', 'Build', 5)],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};
const snooze = (until: string | null) => ({state: 'snoozed', who: 'human:ana', line: 'Back after the launch', since: '2026-11-12T09:00:00Z', until});
const blockedTurn = (line: string) => ({state: 'tasks', who: null, line, since: '2026-11-13T05:00:00Z', until: null});
type Dep = {id: string | null; number: number | null; label?: string | null; title?: string | null};
const SHOTS = process.env['TICO_UI_SHOTS'] || '';
const P304 = {id: 'a304', number: 304, label: 'E304', title: 'Growth synced sources part 1'};
const brief = (id: string, number: number, title: string, turn: Record<string, unknown> | null = null,
  deps: {blocked_by?: Dep[]; blocks?: Dep[]; snoozed?: boolean} = {}) => ({id, title, number, pipeline_id: 'eng',
  stage_id: 'build', status: 'open', owner_id: 'human:ana', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null,
  snoozed: deps.snoozed ?? turn?.['state'] === 'snoozed', turn, version: 1, created_at: '2026-11-09T10:00:00Z', updated_at: '2026-11-09T10:00:00Z',
  blocked_by: deps.blocked_by ?? [], blocks: deps.blocks ?? []});
// The queue order the server gives: blocked and snoozed tasks among the others. #307 depends on #304; #309 on a task the
// reader may not open; #310 depends on #304 too and a bot also snoozed it: the server sends it as blocked, not snoozed.
const TASKS = [
  brief('b307', 307, 'Growth synced sources part 2', blockedTurn('Blocked by E304 Growth synced sources part 1, in Build'), {blocked_by: [P304]}),
  brief('s1', 301, 'Pricing page copy', snooze('2026-11-14T08:00:00Z')),
  brief('a304', 304, 'Growth synced sources part 1', null, {blocks: [{id: 'b307', number: 307, label: 'E307'}, {id: 'b310', number: 310, label: 'E310'}]}),
  brief('b309', 309, 'Ledger export', blockedTurn("Blocked by a task you can't open"), {blocked_by: [{id: null, number: null}]}),
  brief('b310', 310, 'Growth synced sources part 3', blockedTurn('Blocked by E304 Growth synced sources part 1, in Build'), {blocked_by: [P304]}),
  // Blocked by a task with no number (a pipeline that does not number its tasks): named by its title, never "7a0009f1".
  brief('b311', 311, 'Ship the launch', blockedTurn('Blocked by Call the printer, in To do'),
    {blocked_by: [{id: '7a0009f1-0000-4000-8000-000000000001', number: null, label: null, title: 'Call the printer'}]}),
  brief('a2', 302, 'Refund flow'),
];
const AWAKE = ['a304', 'a2'], BLOCKED = ['b307', 'b309', 'b310', 'b311'], ASLEEP = ['s1'];

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
    if (p === '/api/humans' || p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
    if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
    if (p === '/api/v2/pipelines/eng') return json({pipeline});
    if (p === '/api/v2/factories') return json({factories: [], pipelines: []});
    if (p === '/api/v2/tasks' && r.method() === 'GET') {
      const snoozed = url.searchParams.get('snoozed');
      return json({tasks: TASKS.filter(t => snoozed === 'hide' ? !t.snoozed : snoozed === 'only' ? t.snoozed : true)});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
}

/** The rows or boxes of the Build stage, as drawn. */
async function drawn(page: Page, rows: string) {
  await page.locator(`${rows}[data-task="b307"]`).waitFor();
  return page.locator(rows).evaluateAll(els => els.map(el => {
    const stop = el.querySelector('.pv-blocked-by'), title = el.querySelector('.tk-title'), blocks = el.querySelector('.tk-blocks');
    return {id: (el as HTMLElement).dataset['task'], dim: el.classList.contains('pv-snoozed') || el.classList.contains('tk-row-dim'),
      color: title ? getComputedStyle(title).color + '/' + getComputedStyle(title).opacity : '',
      stop: stop ? {label: stop.getAttribute('aria-label'), icon: !!stop.querySelector('.pv-stop svg'), text: (stop as HTMLElement).innerText.trim()} : null,
      sleep: !!el.querySelector('.pv-sleep'), blocks: blocks ? (blocks as HTMLElement).innerText.trim() : null,
      text: (el as HTMLElement).innerText.replace(/\s+/g, ' ').trim()};
  }));
}

function check(where: string, rows: Awaited<ReturnType<typeof drawn>>): void {
  assert.deepEqual(rows.map(r => r.id), [...AWAKE, ...BLOCKED, ...ASLEEP], `${where}: blocked after the awake tasks, before the snoozed`);
  const by = Object.fromEntries(rows.map(r => [r.id, r]));
  for (const id of BLOCKED) {
    const r = by[id]!;
    assert.ok(!r.dim, `${where}: ${id} is not dimmed`);
    assert.equal(r.color, by['a2']!.color, `${where}: ${id}'s title is in full colour, as an awake one's`);
    assert.ok(r.stop?.icon, `${where}: ${id} has the stop mark`);
    assert.ok(!r.sleep, `${where}: ${id} has no moon`);
    assert.ok(!/Snoozed|Nov 14/.test(r.text), `${where}: ${id} never says Snoozed`);
  }
  assert.deepEqual(by['b307']!.stop, {label: 'Blocked by E304', icon: true, text: 'E304'}, `${where}: #307 shows E304`);
  assert.deepEqual(by['b309']!.stop, {label: 'Blocked', icon: true, text: 'Blocked'}, `${where}: a blocker it may not open is not named`);
  assert.deepEqual(by['b311']!.stop, {label: 'Blocked by Call the printer', icon: true, text: 'Call the printer'},
    `${where}: a blocker with no number is named by its title, never its id`);
  assert.equal(by['a304']!.blocks, 'Blocks E307, E310', `${where}: #304 says what it blocks`);
  assert.equal(by['a304']!.stop, null, `${where}: #304 is not blocked`);
  for (const id of ['a2', 'b307', 's1']) assert.equal(by[id]!.blocks, null, `${where}: ${id} blocks nothing`);
  assert.ok(by['s1']!.dim && by['s1']!.sleep, `${where}: a snoozed task is still drawn asleep`);
}

/** The page in the light theme and the dark one (TICO_UI_SHOTS). */
async function shots(page: Page, name: string): Promise<void> {
  if (!SHOTS) return;
  for (const theme of ['light', 'dark']) {
    await page.evaluate(t => { document.documentElement.dataset['theme'] = t; }, theme);
    await page.screenshot({path: require('node:path').join(SHOTS, `${name}-${theme}.png`)});
  }
  await page.evaluate(() => { document.documentElement.dataset['theme'] = 'light'; });
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [];
  try {
    const desk = await browser.newPage({viewport: {width: 1200, height: 900}, serviceWorkers: 'block', locale: 'en-US', timezoneId: 'UTC'});
    await serve(desk, errors);
    await desk.goto('http://tico-ui.test/#/tasks?pipeline=eng&layout=columns');
    check('columns', await drawn(desk, '[data-pv-col="build"] .tk-card'));
    // Build has a limit of 5: the snoozed and the three blocked tasks hold no slot. Its number is the tasks it lists.
    const built = desk.locator('[data-pv-col="build"] .pv-stage-n');
    assert.equal((await built.innerText()).trim(), String(await desk.locator('[data-pv-col="build"] .tk-card').count()), 'the tasks it lists');
    assert.equal(await built.getAttribute('title'), '2 of 5 slots used.', 'blocked tasks hold no slot');

    await desk.goto('http://tico-ui.test/#/tasks?pipeline=eng&layout=list');
    await desk.locator('.pv-group').first().waitFor();
    check('list', await drawn(desk, '.pv-group[aria-label="Build"] .tk-row'));
    await shots(desk, 'pipeline-blocked-desktop');
    // The blocker's name opens the blocker, not the row's own task.
    const opened = desk.waitForRequest(r => /\/api\/v2\/tasks\/[^/?]+$/.test(new URL(r.url()).pathname));
    await desk.locator('.tk-row[data-task="b311"] .pv-blocked-ref').click();
    assert.equal(new URL((await opened).url()).pathname, '/api/v2/tasks/7a0009f1-0000-4000-8000-000000000001');

    const phone = await browser.newPage({viewport: {width: 390, height: 844}, serviceWorkers: 'block', locale: 'en-US', timezoneId: 'UTC',
      isMobile: true, hasTouch: true});
    await serve(phone, errors);
    await phone.goto('http://tico-ui.test/#/tasks?pipeline=eng');
    await phone.locator('.pv-is-phone').waitFor();
    await phone.locator('[data-pv-tab="build"]').click();
    check('phone', await drawn(phone, '.pv-phone .tk-card'));
    await shots(phone, 'pipeline-blocked-phone');
    const wide = await phone.evaluate(() => document.documentElement.scrollWidth);
    assert.ok(wide <= 390, `the phone page does not scroll sideways (${wide} px)`);

    assert.deepEqual(errors, []);
    console.log('pipeline blocked: ok');
  } finally {
    await browser.close();
  }
})().catch((error: unknown) => { console.error(error); process.exit(1); });
