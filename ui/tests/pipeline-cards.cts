import type {Browser, Page, Route} from 'playwright';
// Offline browser check of a pipeline board's cards (task-row.ts taskCardHTML, task #220): who asked and who owns each
// card, read from the board alone, in room to read and still scannable.
// - At 1440x900 with 25 Ideas, at least 7 cards are fully in view, 8 px apart.
// - A long title wraps to two lines and stops there; pointing at the card shows the whole title.
// - Under the title, the asker's face and name and the time in this stage; the owner's face at the right, saying
//   "Owner: <name>", only when someone else owns it.
// - A phone (400 px) shows the same card: title, asker and age.
// TICO_SHOTS=<dir> saves the desktop and phone screenshots.
// Run: npm run build:ui && node ui/tests/pipeline-cards.cts
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const me = {id: 'chris', name: 'Chris', email: 'chris@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const people = [{id: 'chris', name: 'Chris'}, {id: 'sam', name: 'Sam Rivera'}];
const bots = [{name: 'ui-owner', display_name: 'UI Owner', host: 'keeper', status: 'active', can_chat: true, schedules: []},
  {name: 'perf', display_name: 'Performance Owner', host: 'keeper', status: 'active', can_chat: true, schedules: []}];
const req = {kind: 'owner_done', settings: {}, can_push_past: true, who_oks: 'none', person_id: null, line: ''};
const stage = (id: string, name: string) => ({id, name, purpose: '', owner: null, by_hand: false, task_limit: null,
  expected_minutes: null, expected_bots_only: false, on_enter: {}, requirements: [req]});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:chris', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [stage('ideas', 'Ideas'), stage('spec', 'Spec'), stage('dev', 'In development')],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};
const ago = (hours: number) => new Date(Date.now() - hours * 3600_000).toISOString();
const LONG = 'Product brief: bots can read the app (read-only) so Product Owners validate in production themselves, '
  + 'with every screen they need and nothing they may not see';
const brief = (id: string, title: string, extra: Record<string, unknown> = {}) => ({id, title, number: 1, pipeline_id: 'product',
  stage_id: 'ideas', status: 'open', owner_id: 'bot:ui-owner', requester_id: 'human:sam', assignee_id: null, due_at: null, late: null,
  snoozed: false, turn: null, version: 1, created_at: ago(400), updated_at: ago(1), stage_since: ago(72), ...extra});
// The worst case for room: every title but one runs to two lines, as 23 of 25 "Product brief: …" Ideas did (Nov 12).
const title = (i: number) => i === 0 ? LONG : i === 4 ? 'Saved carts' : `Product brief: idea number ${i + 1}, a title long enough to need its second line`;
const IDEAS = Array.from({length: 25}, (_, i) => brief(`i${i}`, title(i),
  i === 1 ? {owner_id: 'human:sam'} : i === 2 ? {requester_id: 'bot:perf', owner_id: 'bot:perf', late: {since: ago(5), level: 'told'}} : {}));
const TASKS = [...IDEAS, brief('s1', 'Pipeline board: see who asked and who owns each card', {stage_id: 'spec', requester_id: 'human:chris',
  turn: {state: 'person', who: 'human:chris', line: 'Waiting on Chris', since: ago(2)}})];

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
    if (p === '/api/employees') return json(bots);
    if (p === '/api/humans' || p === '/api/people') return json({people, teams: {}});
    if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
    if (p === '/api/v2/pipelines/product') return json({pipeline});
    if (p === '/api/v2/factories') return json({factories: [], pipelines: []});
    if (p === '/api/v2/tasks' && r.method() === 'GET') return json({tasks: TASKS});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
}

/** Each card of a stage as drawn: where it is, its title's lines, the asker's name, the age and the owner's face. */
async function cards(page: Page, col: string) {
  await page.locator(`${col} .tk-card[data-task]`).first().waitFor();
  return page.locator(`${col} .tk-card[data-task]`).evaluateAll(els => els.map(el => {
    const box = el.getBoundingClientRect(), title = el.querySelector<HTMLElement>('.tk-title')!;
    const lh = parseFloat(getComputedStyle(title).lineHeight);
    return {id: (el as HTMLElement).dataset['task'], top: box.top, bottom: box.bottom, tip: el.getAttribute('title'),
      lines: Math.round(title.getBoundingClientRect().height / lh), cut: title.scrollHeight > title.clientHeight + 1,
      asker: el.querySelector<HTMLElement>('.tk-asker .tk-name')?.innerText ?? null, askerFace: !!el.querySelector('.tk-asker .av'),
      age: el.querySelector<HTMLElement>('.tk-meta .tk-age')?.innerText ?? null,
      owner: el.querySelector('.tk-owner')?.getAttribute('title') ?? null, ownerFace: !!el.querySelector('.tk-owner .av'),
      late: !!el.querySelector('.tk-meta .tk-late'), mark: !!el.querySelector('.tk-meta .tk-mark-person')};
  }));
}

async function opened(browser: Browser, viewport: {width: number; height: number}, phone: boolean, errors: string[]) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', locale: 'en-US', timezoneId: 'UTC',
    ...(phone ? {isMobile: true, hasTouch: true} : {})});
  await serve(page, errors);
  await page.goto(`http://tico-ui.test/#/tasks?pipeline=product${phone ? '' : '&layout=columns'}`);
  return page;
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [], shots = process.env['TICO_SHOTS'];
  try {
    const desk = await opened(browser, {width: 1440, height: 900}, false, errors);
    const ideas = await cards(desk, '[data-pv-col="ideas"]');
    assert.equal(ideas.length, 25);
    const inView = ideas.filter(c => c.top >= 0 && c.bottom <= 900);
    assert.ok(inView.length >= 7, `at least 7 cards fully in view at 1440x900: ${inView.length}`);
    const gaps = ideas.slice(1).map((c, i) => Math.round(c.top - ideas[i]!.bottom));
    assert.deepEqual([...new Set(gaps)], [8], 'cards are 8 px apart');
    const [long, ownsIt, both] = ideas;
    assert.equal(long!.lines, 2, 'a long title wraps to two lines');
    assert.ok(long!.cut, 'and stops there');
    assert.equal(long!.tip, LONG, 'pointing at the card shows the whole title');
    assert.equal(ideas[4]!.lines, 1, 'a short title keeps one line');
    assert.ok(ideas.filter(c => c.lines === 2).length >= 23, 'the rest run to two lines');
    assert.deepEqual([long!.asker, long!.askerFace, long!.age], ['Sam Rivera', true, '3d'], "the asker's face and name, and 3 days in this stage");
    assert.deepEqual([long!.owner, long!.ownerFace], ['Owner: UI Owner', true], "someone else owns it: the owner's face, saying whose");
    assert.deepEqual([ownsIt!.asker, ownsIt!.owner], ['Sam Rivera', null], 'the asker owns it: the name once, no second face');
    assert.deepEqual([both!.asker, both!.owner, both!.late], ['Performance Owner', null, true], 'Late is on the meta line');
    const [spec] = await cards(desk, '[data-pv-col="spec"]');
    assert.deepEqual([spec!.asker, spec!.mark, spec!.owner], ['You', true, 'Owner: UI Owner'], 'the waiting-on-a-person mark is on the meta line');
    console.log(`desktop: ${inView.length} of 25 Ideas in view, card ${Math.round(long!.bottom - long!.top)} px tall`);
    if (shots) { fs.mkdirSync(shots, {recursive: true}); await desk.screenshot({path: path.join(shots, 'board-1440x900.png')}); }

    const phone = await opened(browser, {width: 400, height: 860}, true, errors);
    await phone.locator('.pv-is-phone').waitFor();
    const onPhone = await cards(phone, '.pv-phone');
    assert.equal(onPhone.length, 25, "a phone's stage shows the stage's cards");
    assert.deepEqual([onPhone[0]!.lines, onPhone[0]!.asker, onPhone[0]!.age, onPhone[0]!.tip], [2, 'Sam Rivera', '3d', LONG], 'the same title, asker and age');
    const wide = await phone.evaluate(() => document.documentElement.scrollWidth);
    assert.ok(wide <= 400, `the phone page does not scroll sideways (${wide} px)`);
    if (shots) await phone.screenshot({path: path.join(shots, 'board-phone-400.png')});

    assert.deepEqual(errors, []);
    console.log('pipeline cards: ok');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
