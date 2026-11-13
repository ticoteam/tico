import type {Page, Route} from 'playwright';
// Offline browser check of blocked tasks in a pipeline's view (task-pipelines.ts; spec "A blocked task shows as Blocked,
// never Snoozed", part 3). After `tico task relate 307 depends_on 304`, in the desktop columns, the desktop list and the
// phone's stage (390 px): #307 keeps full colour (not dimmed like a snoozed task), shows a stop mark and "#304" where a
// snoozed task shows its day, and sits after the awake tasks and before the snoozed ones; #304 shows "Blocks #307". A
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
type Dep = {id: string | null; number: number | null};
const brief = (id: string, number: number, title: string, turn: Record<string, unknown> | null = null,
  deps: {blocked_by?: Dep[]; blocks?: Dep[]; snoozed?: boolean} = {}) => ({id, title, number, pipeline_id: 'eng',
  stage_id: 'build', status: 'open', owner_id: 'human:ana', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null,
  snoozed: deps.snoozed ?? turn?.['state'] === 'snoozed', turn, version: 1, created_at: '2026-11-09T10:00:00Z', updated_at: '2026-11-09T10:00:00Z',
  blocked_by: deps.blocked_by ?? [], blocks: deps.blocks ?? []});
// The queue order the server gives: blocked and snoozed tasks among the others. #307 depends on #304; #309 on a task the
// reader may not open; #310 depends on #304 too and was also snoozed by a bot before the dependency was written.
const TASKS = [
  brief('b307', 307, 'Growth synced sources part 2', blockedTurn('Blocked by #304 Growth synced sources part 1, in Build'), {blocked_by: [{id: 'a304', number: 304}]}),
  brief('s1', 301, 'Pricing page copy', snooze('2026-11-14T08:00:00Z')),
  brief('a304', 304, 'Growth synced sources part 1', null, {blocks: [{id: 'b307', number: 307}, {id: 'b310', number: 310}]}),
  brief('b309', 309, 'Ledger export', blockedTurn("Blocked by a task you can't open"), {blocked_by: [{id: null, number: null}]}),
  brief('b310', 310, 'Growth synced sources part 3', blockedTurn('Blocked by #304 Growth synced sources part 1, in Build'), {blocked_by: [{id: 'a304', number: 304}], snoozed: true}),
  brief('a2', 302, 'Refund flow'),
];
const AWAKE = ['a304', 'a2'], BLOCKED = ['b307', 'b309', 'b310'], ASLEEP = ['s1'];

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
  assert.deepEqual(by['b307']!.stop, {label: 'Blocked by #304', icon: true, text: '#304'}, `${where}: #307 shows #304`);
  assert.deepEqual(by['b309']!.stop, {label: 'Blocked', icon: true, text: 'Blocked'}, `${where}: a blocker it may not open is not named`);
  assert.equal(by['a304']!.blocks, 'Blocks #307, #310', `${where}: #304 says what it blocks`);
  assert.equal(by['a304']!.stop, null, `${where}: #304 is not blocked`);
  for (const id of ['a2', 'b307', 's1']) assert.equal(by[id]!.blocks, null, `${where}: ${id} blocks nothing`);
  assert.ok(by['s1']!.dim && by['s1']!.sleep, `${where}: a snoozed task is still drawn asleep`);
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [];
  try {
    const desk = await browser.newPage({viewport: {width: 1200, height: 900}, serviceWorkers: 'block', locale: 'en-US', timezoneId: 'UTC'});
    await serve(desk, errors);
    await desk.goto('http://tico-ui.test/#/tasks?pipeline=eng&layout=columns');
    check('columns', await drawn(desk, '[data-pv-col="build"] .tk-card'));
    // Build has a limit of 5: the snoozed and the three blocked tasks hold no slot.
    assert.equal((await desk.locator('[data-pv-col="build"] .pv-stage-n').innerText()).trim(), '2/5', 'blocked tasks hold no slot');

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

    assert.deepEqual(errors, []);
    console.log('pipeline blocked: ok');
  } finally {
    await browser.close();
  }
})().catch((error: unknown) => { console.error(error); process.exit(1); });
