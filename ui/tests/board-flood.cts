// A big board in a flood of live changes (ui/app/tasks-page.ts tasksLiveApply, tasksEventsPut, tasksNeedsLive,
// tasksRender; ui/app/task-pipelines.ts pvHTML, pvColumnsHTML; ui/app/refresh.ts liveTasksApply). On TIDY a bot made
// about 430 tasks and closed about 260 in 20 minutes; with the Dev board open the page froze and clicks did not
// register while the server stayed fast, because each burst of events drew the whole board again.
// This opens a 13-stage board holding 1,000 open tasks (one stage 300, like an Ideas column), then sends 500 live
// events over five seconds, twice: new tasks, moves between stages, closes and Needs you changes, spread out as a
// real stream sends them. It measures:
//  - the first draw: from the task list's answer to the frame after the board is drawn;
//  - during each burst: the long tasks (the longest, and the total blocking time, each over 50 ms) and how long a
//    click waits for its handler (the title is clicked every 100 ms; the wait is from the click's own time stamp);
//  - after: each column's count and cards are right (the order the page reads, snoozed last), the board's DOM stays
//    small, and the bursts read no task list.
// Headless timing is noisy, and a shared CI VM is several times slower than a laptop. So each burst's numbers are taken
// separately and the better of the two is held to the budget, and the budgets are for the Mac they were set on, scaled
// by how much slower this box is: a fixed piece of drawing work (BENCH, the best of five) takes BENCH_MS there; on a box
// where it takes 4 times as long, every time budget is 4 times as large (at most 10, at least 1; and TICO_UI_SLOWDOWN
// on top, as for every wait). Scaled, a draw of the whole board per change still fails by far: it blocked for seconds
// on the Mac.
// Before (Nov 16: every card drawn, the whole board's HTML built, compared and put in again for each burst and for each
// Needs you change), on the Mac: first draw 405 ms; longest task 3.3 s and 5.6 s, blocking 8.8 s and 10.0 s in a 5 s
// burst, a click waiting 2.4 s to 3.2 s (p95); 13,012 elements. After (live changes draw at most every 250 ms, only
// the columns that changed, 30 cards a column, cards out of view not laid out): under 50 ms; no task over 50 ms, so no
// blocking; a click waits 16 to 18 ms; 4,969 elements.
import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
import type * as TasksPage from './tasks-page.cts';
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;
const {task, brief} = require('./tasks-page.cts') as TasksPage.Api;
const {t}: {t: (ms: number) => number} = (() => { try { return require('./support/load.cts') as {t: (ms: number) => number}; } catch { return {t: (ms: number) => ms}; } })();
type Task = ReturnType<typeof task>;
interface LiveStream {emit(type: string, data: unknown): void}
interface Sent {at: number; type: string; data: unknown}
interface Marks {
  EventSource: unknown;
  __liveStream?: LiveStream;
  __long: {start: number; duration: number}[];
  __waits: {at: number; wait: number}[];
  __burst?: (events: Sent[]) => Promise<{start: number; end: number}>;
}
declare const TASKS_ST: {loading?: boolean} | null;

// Budgets (the better of two bursts; times, on the Mac, scaled by the box's speed).
const FIRST_DRAW = 400;    // ms from the list's answer to the board drawn
// ms, the longest task during a burst. 100 on the Mac (where none passes 50), raised to 200 for CI: its 4-vCPU VM runs
// four browsers at once and paints in software, which BENCH does not see; there a task took 255 ms with BENCH at 2.1x
// (Nov 16). A draw of the whole board per change took 3,450 ms on the Mac, so it still fails at any scale.
const LONGEST = 200;
const CLICK_P95 = 150;     // ms a click waits for its handler, 95th percentile
const CEILING = 5;         // requests for a whole burst (ui/tests/live-events-load.cts): none of them a list read
const NODES = 6000;        // elements on the page after the bursts: about 390 cards of 1,129 (every card drawn is 13,000)
const BENCH_MS = 25;       // ms BENCH takes on the Mac the budgets were set on (an M-series MacBook, Nov 16)

const EVENTS = 500, BURST_MS = 5000;
// TIDY's Dev board: 13 stages, Build with a limit.
const STAGES = ['Ideas', 'Triage', 'Spec', 'Spec OK', 'Build', 'Review', 'QA', 'Fixes', 'Fixes 2', 'Pre-release', 'Release', 'Deploy', 'Verify']
  .map((name, i) => ({id: 's' + i, name}));
const SIZES = [300, 120, 90, 80, 70, 60, 60, 50, 45, 40, 35, 30, 20];   // 1,000
const LIMIT: Record<string, number> = {s4: 80};
const PIPE = {id: 'dev', name: 'Dev', goal: '', prefix: 'D', manager_id: null, owner_id: 'human:ana', private: false, factory_id: null, preset: null,
  settings_version: 1, retired_at: null, done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true,
  stages: STAGES.map(s => ({id: s.id, name: s.name, purpose: '', owner: null, by_hand: false, task_limit: LIMIT[s.id] ?? null, expected_minutes: null,
    expected_bots_only: false, on_enter: {}, requirements: []}))};
const OWNERS = ['bot:pm', 'bot:eng', 'bot:ops', 'bot:writer', 'human:ana'];

// The same numbers on every run.
let seed = 7;
const rand = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
const pick = <T,>(xs: T[]): T => xs[Math.floor(rand() * xs.length)]!;
const T0 = Date.now() - 40 * 86400e3;
let made = 0;
const NOW = Date.now();
function devTask(stageAt: number, over: Partial<Task> = {}): Task {
  const n = ++made, s = STAGES[stageAt]!, snoozed = n % 25 === 0;
  const until = new Date(NOW + 3 * 86400e3).toISOString();
  return task('d' + n, `Dev task ${n} ${pick(['checkout', 'signup', 'invoices', 'calendar', 'search', 'payouts'])}`, s.id, {
    pipeline_id: 'dev', pipeline: {id: 'dev', name: 'Dev'}, stage_id: s.id, stage: {id: s.id, name: s.name, kind: 'work', by_hand: false},
    owner_id: pick(OWNERS), created_at: new Date(T0 + n * 60000).toISOString(),
    ...(snoozed ? {snoozed: true, snooze: {by: 'human:ana', until, why: null},
      turn: {state: 'snoozed', who: null, line: 'Snoozed', since: new Date(NOW).toISOString(), until, ask: false, requirement_id: null}} : {}),
    ...over});
}
// The server's open tasks of the board, by id.
const OPEN = new Map<string, Task>();
SIZES.forEach((size, at) => { for (let i = 0; i < size; i++) { const d = devTask(at); OPEN.set(d.id, d); } });
// Needs you: one task item per open task in some stages; each `needs` event takes one away (a mend, nothing read).
const NEEDS = [...OPEN.values()].filter((_, i) => i % 9 === 0).slice(0, 110)
  .map(d => ({kind: 'task', id: 'task:' + d.id, task_id: d.id, line: d.title, since: d.created_at, task: brief(d)}));
let needsVersion = 0;

let seq = 10;
/** EVENTS events over BURST_MS, in the shape backend/events.py sends: 30% new tasks (a third into Ideas), 40% moves (most
 * to the next stage), 20% closes, 10% Needs you. The model above follows them, so the board's end state is known. */
function burstOf(): Sent[] {
  const out: Sent[] = [];
  for (let i = 0; i < EVENTS; i++) {
    const at = Math.round((i + rand()) * BURST_MS / EVENTS), r = rand();
    if (r < 0.1 && NEEDS.length) {
      const gone = NEEDS.splice(Math.floor(rand() * NEEDS.length), 1)[0]!;
      needsVersion++;
      out.push({at, type: 'needs', data: {seq: ++seq, version: 'v' + needsVersion, base: 'v' + (needsVersion - 1), changed: [], removed: [gone.id], count: NEEDS.length}});
      continue;
    }
    let d: Task, kind = 'update';
    if (r < 0.4) { d = devTask(rand() < 0.33 ? 0 : Math.floor(rand() * STAGES.length)); kind = 'insert'; OPEN.set(d.id, d); }
    else {
      const was = pick([...OPEN.values()]);
      if (r < 0.8) {
        const from = STAGES.findIndex(s => s.id === was.stage_id);
        const to = rand() < 0.7 ? Math.min(from + 1, STAGES.length - 1) : Math.floor(rand() * STAGES.length), s = STAGES[to]!;
        d = {...was, stage_id: s.id, stage: {id: s.id, name: s.name, kind: 'work', by_hand: false}, version: was.version + 1, updated_at: new Date().toISOString()};
        OPEN.set(d.id, d);
      } else {
        d = {...was, status: 'closed', version: was.version + 1, updated_at: new Date().toISOString(), turn: null};
        OPEN.delete(d.id);
      }
    }
    out.push({at, type: 'tasks', data: {seq: ++seq, id: d.id, kind, kinds: [kind], task: d}});
  }
  return out;
}

/** Each stage's cards as the page orders them (rank, then oldest first; snoozed last), and its header's number: the
 * tasks the stage lists. */
function expected(): Map<string, {ids: string[]; count: string}> {
  const order = (a: Task, b: Task) => String(a.created_at).localeCompare(String(b.created_at)) || a.id.localeCompare(b.id);
  return new Map(STAGES.map(s => {
    const here = [...OPEN.values()].filter(d => d.stage_id === s.id).sort(order);
    const awake = here.filter(d => !d.snoozed);
    return [s.id, {ids: [...awake, ...here.filter(d => d.snoozed)].map(d => d.id), count: String(here.length)}];
  }));
}

/** The box's speed: 1,000 card-like boxes built, parsed and laid out, the best of five runs, in ms. */
const BENCH = () => {
  const runs: number[] = [];
  for (let r = 0; r < 5; r++) {
    const t0 = performance.now();
    let html = '';
    for (let i = 0; i < 1000; i++) {
      html += '<div style="display:flex;flex-direction:column;gap:4px;padding:8px;border:1px solid #888">'
        + `<span>Card ${i} ${'word '.repeat(i % 7)}</span><span style="display:flex;gap:6px"><span style="width:16px;height:16px;border-radius:50%;background:#456"></span>`
        + `<span style="flex:1"></span><span>${i}m</span></span></div>`;
    }
    const box = document.createElement('div');
    box.style.cssText = 'position:absolute;left:-10000px;top:0;width:300px;display:flex;flex-direction:column;gap:8px';
    box.innerHTML = html;
    document.body.append(box);
    void box.offsetHeight;
    box.remove();
    runs.push(performance.now() - t0);
  }
  return Math.min(...runs);
};

const pct = (xs: number[], p: number) => { const s = [...xs].sort((a, b) => a - b); return s.length ? s[Math.min(s.length - 1, Math.floor(p * s.length))]! : 0; };

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
  const errors: string[] = [], requests: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(() => {
    const w = window as unknown as Marks;
    w.__long = []; w.__waits = [];
    new PerformanceObserver(list => { for (const e of list.getEntries()) w.__long.push({start: e.startTime, duration: e.duration}); })
      .observe({type: 'longtask', buffered: true});
    // How long a click waited for the page: from the input's own time stamp to its first handler.
    for (const type of ['pointerdown', 'click']) document.addEventListener(type, ev => { w.__waits.push({at: performance.now(), wait: performance.now() - ev.timeStamp}); }, true);
    w.EventSource = class {
      declare on: Record<string, ((ev: MessageEvent) => void)[]>;
      constructor() { this.on = {}; w.__liveStream = this; setTimeout(() => this.emit('ready', {seq: 10}), 0); }
      addEventListener(type: string, fn: (ev: MessageEvent) => void) { (this.on[type] ||= []).push(fn); }
      close() {}
      emit(type: string, data: {seq?: number}) {
        const ev = new MessageEvent(type, {data: JSON.stringify(data), lastEventId: String(data.seq || '')});
        for (const fn of this.on[type] || []) fn(ev);
      }
    };
    // The stream as a server sends it: each event at its time, and what fell due while the page was busy at once.
    w.__burst = events => new Promise(done => {
      const start = performance.now();
      let i = 0;
      const tick = () => {
        const now = performance.now() - start;
        while (i < events.length && events[i]!.at <= now) { const e = events[i++]!; w.__liveStream!.emit(e.type, e.data); }
        if (i < events.length) setTimeout(tick, 4); else done({start, end: performance.now()});
      };
      tick();
    });
  });
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname;
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1] as string)))
      return route.fulfill({contentType: (ui[1] as string).endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1] as string), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p.startsWith('/api/') && p !== '/api/v2/observability') requests.push(p + url.search);
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true, registered: true});
    if (p === '/api/employees') return json(['pm', 'eng', 'ops', 'writer'].map(name => ({name, display_name: name.toUpperCase(), host: 'keeper', status: 'active', state: 'active', can_chat: true, schedules: []})));
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
    if (p === '/api/issues') return json([]);
    // The whole board in one answer, as after More: the page draws what it holds.
    if (p === '/api/v2/tasks') return json({tasks: [...OPEN.values()].map(brief), more: false});
    if (p === '/api/v2/pipelines') return json({pipelines: [PIPE]});
    if (p === '/api/v2/needs-you') return json({items: NEEDS, version: 'v' + needsVersion, count: NEEDS.length, next: null});
    return json(unrouted(p));
  });

  await page.goto('https://tico-ui.test/#/tasks?pipeline=dev&layout=columns');
  await page.waitForFunction(() => (window as unknown as Marks).__liveStream && TASKS_ST && !TASKS_ST.loading
    && document.querySelectorAll('#task-body [data-pv-col]').length === 13 && document.querySelector('#task-body [data-task]'), null, {timeout: t(15000)});
  // The first draw: the list's answer to the frame after the board went in.
  const firstDraw = await page.evaluate(() => new Promise<number>(done => requestAnimationFrame(() => requestAnimationFrame(() => {
    const answer = performance.getEntriesByType('resource').filter(e => e.name.includes('/api/v2/tasks')).map(e => (e as PerformanceResourceTiming).responseEnd)[0] ?? 0;
    const w = window as unknown as Marks, after = w.__long.filter(l => l.start >= answer - 1);
    // The board went in during the long tasks that followed the answer; without one, it was quicker than 50 ms.
    done(after.length ? Math.max(...after.map(l => l.start + l.duration)) - answer : 50);
  }))));
  // Nothing more to read before the bursts.
  for (let seen = -1; seen !== requests.length;) { seen = requests.length; await page.waitForTimeout(t(800)); }
  const bench = await page.evaluate(BENCH), scale = Math.min(10, Math.max(1, bench / BENCH_MS));
  const budget = (ms: number) => Math.round(t(ms * scale));

  const title = (await page.locator('.tl-head .ph-t').boundingBox())!;
  const runs: {longest: number; blocking: number; p95: number; clicks: number; reads: string[]}[] = [];
  for (let round = 0; round < 2; round++) {
    const events = burstOf(), before = requests.length;
    const sent = page.evaluate(events => (window as unknown as Marks).__burst!(events), events);
    let sending = true;
    void sent.finally(() => { sending = false; });
    const from = await page.evaluate(() => performance.now());
    while (sending) {
      await page.mouse.click(title.x + 10, title.y + title.height / 2);
      await page.waitForTimeout(100);
    }
    const {end} = await sent;
    await page.waitForTimeout(t(1500));   // the last batch is drawn (live.ts folds a burst for 200 ms)
    const marks = await page.evaluate(([from, until]) => {
      const w = window as unknown as Marks;
      return {long: w.__long.filter(l => l.start >= from && l.start <= until).map(l => l.duration),
        waits: w.__waits.filter(c => c.at >= from && c.at <= until).map(c => c.wait)};
    }, [from, end + 1000] as [number, number]);
    runs.push({longest: Math.max(0, ...marks.long), blocking: marks.long.reduce((sum, d) => sum + Math.max(0, d - 50), 0),
      p95: pct(marks.waits, 0.95), clicks: marks.waits.length, reads: requests.slice(before)});
  }

  // After: every column's number and cards, as the server holds them.
  const want = expected();
  const drawn = await page.evaluate(() => [...document.querySelectorAll<HTMLElement>('#task-body [data-pv-col]')].map(col => ({
    id: col.dataset['pvCol'] ?? '', count: col.querySelector('.pv-stage-n')?.textContent ?? '',
    ids: [...col.querySelectorAll<HTMLElement>('.pv-col-body [data-task]')].map(c => c.dataset['task'] ?? ''),
    more: Number(col.querySelector('[data-pv-more]')?.textContent?.match(/\d+/)?.[0] ?? 0)})));
  assert.equal(drawn.length, STAGES.length);
  for (const col of drawn) {
    const w = want.get(col.id)!;
    assert.equal(col.count, w.count, `${col.id}'s number`);
    assert.deepEqual(col.ids, w.ids.slice(0, col.ids.length), `${col.id}'s cards are its first tasks in order`);
    assert.equal(col.ids.length + col.more, w.ids.length, `${col.id} shows its cards or says how many more`);
  }
  const nodes = await page.evaluate(() => document.getElementsByTagName('*').length);
  // Show N more draws the rest of that stage, and the stages beside it stay as they were.
  const ideas = want.get('s0')!.ids, spec = drawn.find(c => c.id === 's2')!.ids;
  await page.locator('[data-pv-col="s0"] [data-pv-more]').click();
  await page.locator('[data-pv-col="s0"] [data-pv-more]').waitFor({state: 'detached'});
  assert.deepEqual(await page.locator('[data-pv-col="s0"] .pv-col-body [data-task]').evaluateAll(cards => cards.map(c => (c as HTMLElement).dataset['task'])), ideas);
  assert.deepEqual(await page.locator('[data-pv-col="s2"] .pv-col-body [data-task]').evaluateAll(cards => cards.map(c => (c as HTMLElement).dataset['task'])), spec);

  const best = {longest: Math.min(...runs.map(r => r.longest)), blocking: Math.min(...runs.map(r => r.blocking)), p95: Math.min(...runs.map(r => r.p95))};
  const line = `first draw ${Math.round(firstDraw)} ms; longest task ${runs.map(r => Math.round(r.longest)).join('/')} ms, `
    + `blocking ${runs.map(r => Math.round(r.blocking)).join('/')} ms, click wait p95 ${runs.map(r => Math.round(r.p95)).join('/')} ms `
    + `(${runs.map(r => r.clicks).join('/')} clicks); ${nodes} elements; ${OPEN.size} open tasks; bench ${Math.round(bench)} ms (budgets x${scale.toFixed(1)})`;
  console.log(line);
  for (const r of runs) {
    assert.deepEqual(r.reads.filter(q => q.startsWith('/api/v2/tasks') || q.startsWith('/api/v2/needs-you')), [], 'a burst read a list');
    assert.ok(r.reads.length <= CEILING, `a burst made ${r.reads.length} requests: ${r.reads.slice(0, 8).join(' ')}`);
  }
  assert.ok(firstDraw < budget(FIRST_DRAW), `the first draw took ${Math.round(firstDraw)} ms (budget ${budget(FIRST_DRAW)})`);
  assert.ok(best.longest < budget(LONGEST), `the longest task in a burst was ${Math.round(best.longest)} ms (budget ${budget(LONGEST)})`);
  assert.ok(best.p95 < budget(CLICK_P95), `a click waited ${Math.round(best.p95)} ms (p95; budget ${budget(CLICK_P95)})`);
  assert.ok(nodes < NODES, `${nodes} elements on the page (budget ${NODES})`);
  assert.deepEqual(errors, []);
  console.log('a 1,000-task board in a flood of live changes: ok');
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
