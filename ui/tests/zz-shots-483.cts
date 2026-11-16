import type {Page, Route} from 'playwright';
// Screenshots for spec #483 (not a test): a Product board, desktop 1440 and phone 390. Synthetic data. TICO_SHOTS=<dir>, TICO_TAG=before|after.
const {chromium} = require('playwright') as typeof import('playwright');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const people = [{id: 'ana', name: 'Ana'}, {id: 'sam', name: 'Sam Rivera'}];
const bots = [{name: 'tasks-po', display_name: 'Tasks Product Owner', host: 'keeper', status: 'active', can_chat: true, schedules: []}];
const req = {kind: 'owner_done', settings: {}, can_push_past: true, who_oks: 'none', person_id: null, line: ''};
const stage = (id: string, name: string, limit: number | null = null, mins: number | null = null) => ({id, name, purpose: '', owner: null, by_hand: false, task_limit: limit,
  expected_minutes: mins, expected_bots_only: false, on_enter: {}, requirements: [req]});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null,
  stages: [stage('ideas', 'Pitch'), stage('spec', 'Spec', 20, 2880), stage('build', 'Build', 18, 1440), stage('review-1', 'Review 1', null, 120), stage('ok-release', 'OK release'), stage('validate', 'Validate', null, 1440)],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};
const ago = (h: number) => new Date(Date.now() - h * 3600_000).toISOString();
let n = 0;
const t = (st: string, title: string, h: number) => ({id: `t${++n}`, title, number: 400 + n, pipeline_id: 'product', stage_id: st, status: 'open',
  owner_id: 'bot:tasks-po', requester_id: 'human:sam', assignee_id: null, due_at: null, late: null, snoozed: false, turn: null, version: 1,
  created_at: ago(200), updated_at: ago(1), stage_since: ago(h)});
const TASKS = [t('ideas', 'Saved filters on the board', 79), t('ideas', 'Bulk move tasks between stages', 30), t('ideas', 'Due dates on stages', 6),
  t('spec', 'Pipeline boards show where work waits', 2), t('spec', 'Ask carries its OK to the next stage', 37),
  t('build', 'Stage limits count blocked tasks', 5), t('review-1', 'Reopen a closed task', 0.2),
  t('ok-release', 'Bot notes read clearly', 1.4), t('validate', 'Snooze needs a date', 50), t('validate', 'Owner shown on every card', 3)];
async function serve(page: Page) {
  await page.route('**/*', (route: Route) => {
    const r = route.request(), url = new URL(r.url()), p = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p.startsWith('/vendor/fonts/')) { const f = path.join(__dirname, '../..', 'ui', p); return fs.existsSync(f) ? route.fulfill({body: fs.readFileSync(f), contentType: 'font/woff2'}) : route.abort(); }
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
(async () => {
  const browser = await chromium.launch({headless: true, channel: 'chrome'});
  const dir = process.env['TICO_SHOTS']!, tag = process.env['TICO_TAG']!;
  fs.mkdirSync(dir, {recursive: true});
  try {
    const d = await browser.newPage({viewport: {width: 1440, height: 900}, locale: 'en-US', timezoneId: 'UTC', serviceWorkers: 'block'});
    await serve(d); await d.goto('http://tico-ui.test/#/tasks?pipeline=product&layout=columns');
    await d.locator('[data-pv-col="spec"] .tk-card').first().waitFor(); await d.waitForTimeout(500);
    await d.screenshot({path: path.join(dir, `board-${tag}-1440.png`), clip: {x: 0, y: 0, width: 1440, height: 520}});
    const ph = await browser.newPage({viewport: {width: 390, height: 844}, deviceScaleFactor: 2, isMobile: true, hasTouch: true, locale: 'en-US', timezoneId: 'UTC', serviceWorkers: 'block'});
    await serve(ph); await ph.goto('http://tico-ui.test/#/tasks?pipeline=product');
    await ph.locator('.pv-is-phone').waitFor(); await ph.locator('[data-pv-tab="build"]').click(); await ph.waitForTimeout(500);
    await ph.screenshot({path: path.join(dir, `board-${tag}-390.png`), clip: {x: 0, y: 0, width: 390, height: 520}});
    console.log('ok', tag);
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
