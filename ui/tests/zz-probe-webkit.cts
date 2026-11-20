import type {Browser, Page, Route} from 'playwright';
// Offline browser check of side stages (a stage off the main path, like Rejected off Bot QA): on the board its column sits
// right after its stage, outlined in dashes with no fill and ↳ before its quieter name; a phone's tab for it is dashed with
// ↳; the list's group head has ↳. A drop into or out of it is a plain move (its stage's id, never `next`), and a move on
// from the stage before it skips it (`next`). The task window says "↳ Rejected · from Bot QA · 2h", and Send back moves
// it to the stage it came from. In the editor every stage but the first has a Side switch; on, the row is indented with
// ↳ and Limit, Late after and the requirements go (the save says by hand, nothing else to leave); off keeps by hand.
// TICO_UI_SHOTS=<dir> keeps screenshots.
const {webkit, chromium, devices} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

type Rec = Record<string, unknown>;
const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const NOW = Date.now();
const at = (minutes: number) => new Date(NOW - minutes * 60000).toISOString();
const done = {id: 'q0', kind: 'owner_done', source: null, condition: null, settings: {}, can_push_past: true, who_oks: 'none', person_id: null, line: ''};
const stage = (id: string, name: string, extra: Rec = {}) => ({id, name, purpose: '', owner: null, by_hand: false, requests_move_on: false,
  task_limit: null, expected_minutes: null, expected_bots_only: false, side: false, on_enter: {}, requirements: [{...done, id: `q-${id}`}], ...extra});
// Bot QA queues its PRs as a task enters (a stored action: no Side until it is removed and saved).
const STAGES = () => [stage('deck', 'On Deck'), stage('build', 'In progress'), stage('review', 'Review'), stage('rejected','Rejected',{side:true,by_hand:true,requirements:[]}), stage('qa', 'QA'), stage('ship', 'Ship', {task_limit: 4})];
const pipelineRec = (): Rec & {stages: Rec[]; settings_version: number} => ({id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null,
  owner_id: 'human:ana', private: false, factory_id: null, preset: null, settings_version: 1, retired_at: null, stages: STAGES(),
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true});

// A task as the fake server keeps it. A list row (TaskBrief) carries stage_since, and came_from only when set; the full
// read (TaskDetail) has came_from always (null or {id, name}), and stage_since where `read_since` gives it (a server from
// before it was added sends none).
interface Task {
  id: string; title: string; number: number; stage_id: string; version: number; stage_since: string;
  came_from: {id: string; name: string} | null;
  read_since?: string | null;
}
const task = (id: string, title: string, stageId: string, extra: Partial<Task> = {}): Task =>
  ({id, title, number: Number(id.slice(1)), stage_id: stageId, version: 1, stage_since: at(30), came_from: null, ...extra});
const row = (t: Task) => ({id: t.id, title: t.title, number: t.number, status: 'open', pipeline_id: 'product', stage_id: t.stage_id,
  stage_since: t.stage_since, owner_id: 'human:ana', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: null, version: t.version, created_at: at(60 * 24), updated_at: at(30), ...(t.came_from ? {came_from: t.came_from} : {})});
function full(t: Task, rec: {stages: Rec[]}) {
  const {stage_since: _since, ...brief} = row(t);
  const since = t.read_since === undefined ? {} : {stage_since: t.read_since};
  const s = rec.stages.find(x => x['id'] === t.stage_id)!;
  return {...brief, ...since, came_from: t.came_from, pipeline: {id: 'product', name: 'Product'}, stage: {id: s['id'], name: s['name'], kind: 'work', by_hand: !!s['by_hand']},
    snooze: null, labels: [], links: [], private: false, readers: [], relations: {}, conversation_id: 'c-' + t.id};
}

interface Fake { pipeline: ReturnType<typeof pipelineRec>; tasks: Task[]; moves: string[]; saves: {stages: Rec[]}[] }

async function open(browser: Browser, fake: Fake, viewport: {width: number; height: number}, hash: string, errors: string[]): Promise<Page> {
  const page = await browser.newPage(process.env.ENG==='chromium'?{viewport,serviceWorkers:'block'}:{...devices['iPhone 13'], serviceWorkers: 'block'});
  page.on('pageerror', (error: Error) => errors.push(error.message));
  await page.route('**/*', (route: Route) => {
    const r = route.request(), url = new URL(r.url()), p = url.pathname, method = r.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json([]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/pipelines') return json({pipelines: [fake.pipeline]});
    // As backend/pipeline_settings.py save: the record as sent, one version on.
    if (p === '/api/v2/pipelines/product' && method === 'POST') {
      const body = JSON.parse(r.postData() || '{}') as {stages: Rec[]};
      fake.saves.push(body);
      const {settings_version: _seen, ...given} = body as Rec;
      Object.assign(fake.pipeline, JSON.parse(JSON.stringify(given)), {settings_version: fake.pipeline.settings_version + 1});
      return json({pipeline: fake.pipeline});
    }
    if (p === '/api/v2/pipelines/product') return json({pipeline: fake.pipeline});
    if (p === '/api/v2/factories') return json({factories: [], pipelines: []});
    if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: 0, items: [], version: '0', next: null});
    if (p === '/api/v2/tasks/labels') return json({labels: [], tags: []});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/tasks' && method === 'GET') return json({tasks: fake.tasks.map(row), cursor: 'c1', next_offset: null, next_after: null});
    // A move as backend/task_engine.py makes it: `next` is the next main stage (side stages are skipped); into a side
    // stage, the task keeps the stage it came from.
    const move = p.match(/^\/api\/v2\/tasks\/([^/]+)\/move$/);
    if (move && method === 'POST') {
      const t = fake.tasks.find(x => x.id === move[1])!, body = JSON.parse(r.postData() || '{}') as Rec;
      fake.moves.push(`${t.id}>${String(body['to'])}`);
      const stages = fake.pipeline.stages, from = stages.findIndex(s => s['id'] === t.stage_id);
      const to = body['to'] === 'next' ? stages.slice(from + 1).find(s => !s['side']) : stages.find(s => s['id'] === body['to']);
      if (!to) return json({error: 'not_found', detail: 'No such stage'}, 404);
      const was = stages[from]!;
      Object.assign(t, {stage_id: to['id'], version: t.version + 1, stage_since: new Date().toISOString(),
        came_from: to['side'] ? {id: String(was['id']), name: String(was['name'])} : null});
      return json({task: full(t, fake.pipeline)});
    }
    const tree = p.match(/^\/api\/v2\/tasks\/([^/]+)\/tree$/);
    if (tree) return json([]);
    const files = p.match(/^\/api\/v2\/tasks\/([^/]+)\/files$/);
    if (files) return json({files: []});
    const one = p.match(/^\/api\/v2\/tasks\/([^/]+)$/);
    if (one && method === 'GET') {
      const t = fake.tasks.find(x => x.id === decodeURIComponent(one[1]!));
      if (!t) return json({error: 'not_found', detail: 'Not found'}, 404);
      return json({task: full(t, fake.pipeline), children: [], comments: [], events: [], runs: [], messages: [], has_more: false, mover: true});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  return page;
}

/** Holds task `id` over the head of column `col` (a short column has no empty space under its cards), in steps as a
    person would, and lets go. */
async function drop(page: Page, id: string, col: string): Promise<void> {
  const boxes = await page.waitForFunction(([t, c]) => {
    const a = document.querySelector(`.tk-card[data-task="${t}"]`)?.getBoundingClientRect();
    const b = document.querySelector(`[data-pv-col="${c}"] > .pv-col-head`)?.getBoundingClientRect();
    return a?.width && b?.width ? {from: {x: a.x, y: a.y, height: a.height}, to: {x: b.x, y: b.y, width: b.width, height: b.height}} : null;
  }, [id, col]);
  const {from, to} = await boxes.jsonValue() as {from: {x: number; y: number; height: number}; to: {x: number; y: number; width: number; height: number}};
  await page.mouse.move(from.x + 20, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(from.x + 40, from.y + from.height / 2, {steps: 3});
  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, {steps: 8});
  // The card is drawn where it went at once, and again when the engine answers: the next drop measures after that.
  const answered = page.waitForResponse(r => r.url().endsWith(`/api/v2/tasks/${id}/move`));
  await page.mouse.up();
  await answered;
  await page.locator(`[data-pv-col="${col}"] .tk-card[data-task="${id}"]`).waitFor();
  await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
}

const shot = async (page: Page, name: string, selector?: string) => {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  await (selector ? page.locator(selector) : page).screenshot({path: path.join(dir, `side-stages-${name}.png`)});
};


(async () => {
  const eng = process.env.ENG==='chromium' ? chromium : webkit;
  const browser = await eng.launch({headless: true});
  const errors: string[] = [];
  const logs: string[] = [];
  try {
    const N = Number(process.env.N||'173');
    const tasks: Task[] = [];
    for (let i=0;i<N;i++) tasks.push(task('t'+(1000+i), 'Deck task '+i, 'deck'));
    for (let i=0;i<83;i++) tasks.push(task('t'+(3000+i), 'Build task '+i, 'build'));
    for (let i=0;i<9;i++) tasks.push(task('t'+(5000+i), 'Review task '+i, 'review'));
    const fake: Fake = {pipeline: pipelineRec(), moves: [], saves: [], tasks};
    const phone = await open(browser, fake, {width: 390, height: 844}, '#/tasks?pipeline=product', errors);
    phone.on('console', m => logs.push(m.type()+': '+m.text()));
    await phone.locator('.pv-tab').first().waitFor({timeout: 20000});
    await phone.waitForTimeout(2500);
    const info = await phone.evaluate(() => {
      const r = (s: string) => { const e = document.querySelector(s) as HTMLElement|null; if (!e) return null; const b = e.getBoundingClientRect(); return {x:b.x,y:b.y,w:b.width,h:b.height, cls:e.className, style:e.getAttribute('style'), mask:getComputedStyle(e).webkitMaskImage?.slice(0,60), msize:(getComputedStyle(e) as any).webkitMaskSize, oh:e.offsetHeight, ch:e.clientHeight}; };
      return {tabs:r('.pv-tabs'), tab:r('.pv-tab'), head:r('.pv-phone-head'), phone:r('.pv-phone'), cards: document.querySelectorAll('.pv-phone .tk-card').length, firstCard: r('.pv-phone .tk-card'), empty: document.querySelector('.pv-empty')?.textContent};
    });
    console.log(JSON.stringify({tabs:info.tabs,cards:info.cards}));
    const OUT=process.env.OUT||'/tmp/probe'; fs.mkdirSync(OUT,{recursive:true});
    await phone.evaluate(() => { const m = document.querySelector('#main') as HTMLElement; window.scrollTo(0,1500); if (m) m.scrollTop = 1500; }); await phone.waitForTimeout(500);
    await phone.screenshot({path: OUT+'/a-scrolled.png'});
    await phone.locator('.pv-tab').nth(1).click(); await phone.waitForTimeout(800);
    await phone.screenshot({path: OUT+'/b-tab2.png'});
    console.log(await phone.evaluate(() => ({sy: scrollY, tabsY: document.querySelector('.pv-tabs')!.getBoundingClientRect().y, cards: document.querySelectorAll('.pv-phone .tk-card').length, firstY: document.querySelector('.pv-phone .tk-card')?.getBoundingClientRect().y, scroller: (()=>{let e:any=document.querySelector('.pv-phone'); const out=[]; while(e){const cs=getComputedStyle(e); if(/auto|scroll/.test(cs.overflowY)) out.push(e.id||e.className+':'+e.scrollTop); e=e.parentElement;} return out;})()})));
    await phone.locator('.pv-tabs').evaluate((e: HTMLElement) => { e.scrollLeft = 400; }); await phone.waitForTimeout(500);
    await phone.locator('.pv-tab').nth(2).click(); await phone.waitForTimeout(800);
    await phone.screenshot({path: OUT+'/c-tab3.png'});
    fs.mkdirSync(process.env.OUT||'/tmp/probe',{recursive:true});
    await phone.screenshot({path: path.join(process.env.OUT||'/tmp/probe', (process.env.ENG||'webkit')+'-'+N+'.png')});
    console.log('errors', errors); console.log('logs', logs.slice(0,20));
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
