// Live task events at scale (ui/app/live.ts, ui/app/refresh.ts): every open tab gets every task change, and a busy
// team's bots create and change tasks every few seconds. When each change made a tab read whole task lists, a few
// open tabs kept the server at 100% CPU. This sends 50 task events over about three seconds and caps the requests
// the page makes in reply:
//  - a pipeline on the Tasks page puts each event's task in place and reads nothing;
//  - a bot's page reads its task lists at most once for the burst;
//  - a hidden tab reads nothing.
// The stream is a stand-in EventSource the test drives, so the events land spread out as they do on a real team,
// not in one batch.
import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
import type * as ThreadsFake from './support/threads-fake.cts';
import type * as TasksPage from './tasks-page.cts';
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;
const {threadsFake} = require('./support/threads-fake.cts') as ThreadsFake.Api;
const {task, brief, PIPELINES} = require('./tasks-page.cts') as TasksPage.Api;
const {t}: {t: (ms: number) => number} = (() => { try { return require('./support/load.cts') as {t: (ms: number) => number}; } catch { return {t: (ms: number) => ms}; } })();
interface LiveStream {emit(type: string, data: Record<string, unknown>): void}
interface PageWin {EventSource: unknown; __liveStream?: LiveStream}
declare const TASKS_ST: {loading?: boolean} | null;

const EVENTS = 50;
const CEILING = 5;   // requests a page may make for the whole burst; reading lists per change makes 15 or more
// The server's tasks (v3 Task, as GET /api/v2/tasks/{id} and a `tasks` event's `task` carry it), by id.
const SERVER = new Map([['t1', task('t1', 'Draft the release notes', 'spec', {owner_id: 'bot:ops'})]]);

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({viewport: {width: 1300, height: 820}, serviceWorkers: 'block'});
  const errors: string[] = [], requests: string[] = [];
  const threads = threadsFake({bot: 'ops', messages: [{id: 'm1', sender_id: 'human:ana', body: 'Draft the release notes'}]});
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(() => {
    (window as unknown as PageWin).EventSource = class {
      declare url: string;
      declare on: Record<string, ((ev: MessageEvent) => void)[]>;
      declare closed?: boolean;
      constructor(url: string) {
        this.url = url; this.on = {}; (window as unknown as PageWin).__liveStream = this;
        setTimeout(() => this.emit('ready', {seq: 10}), 0);
      }
      addEventListener(type: string, fn: (ev: MessageEvent) => void) { (this.on[type] ||= []).push(fn); }
      close() { this.closed = true; }
      emit(type: string, data: {seq?: number}) {
        const ev = new MessageEvent(type, {data: JSON.stringify(data), lastEventId: String(data.seq || '')});
        for (const fn of this.on[type] || []) fn(ev);
      }
    };
  });
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname;
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1] as string)))
      return route.fulfill({contentType: (ui[1] as string).endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1] as string), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    // Not counted: boot.js re-reads the observability config on a timer, events or not, and on a slow
    // box that read can land inside a burst.
    if (p.startsWith('/api/') && p !== '/api/v2/observability') requests.push(p + url.search);
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true, registered: true});
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', state: 'active', can_chat: true, schedules: []}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
    if (p === '/api/issues') return json([]);
    const answer = threads(p, url, route.request().method());
    if (answer !== undefined) return json(answer);
    if (p === '/api/v2/tasks') return json({tasks: [...SERVER.values()].map(brief)});
    if (p === '/api/v2/pipelines') return json({pipelines: PIPELINES});
    if (p === '/api/v2/needs-you') return json({items: [], version: 'v0'});
    return json(unrouted(p));
  });

  // Waits until the page has made no request for a while: what it reads on opening is not counted.
  const settle = async () => {
    for (let seen = -1; seen !== requests.length;) { seen = requests.length; await page.waitForTimeout(t(1000)); }
  };
  // EVENTS task events, one every 60 ms: a third new tasks, the rest changes to the bot's own task, each in the shape
  // backend/events.py _tasks sends ({id, kind, kinds, task}). Returns what the page asked for while they arrived and
  // for a second after.
  let seq = 10;
  const burst = async () => {
    const before = requests.length;
    for (let i = 0; i < EVENTS; i++) {
      const fresh = i % 3 === 0;
      const d = fresh ? task(`n${i}`, `Check release link ${i}`, 'spec', {owner_id: 'bot:ops'})
        : task('t1', `Draft the release notes, pass ${i}`, 'spec', {owner_id: 'bot:ops', version: 4 + i});
      SERVER.set(d.id, d);
      const kind = fresh ? 'insert' : 'update';
      seq++;
      await page.evaluate(([s, d, kind]: [number, {id: string}, string]) => (window as unknown as PageWin).__liveStream!.emit('tasks', {seq: s, id: d.id, kind, kinds: [kind], task: d}),
        [seq, d, kind] as [number, {id: string}, string]);
      await page.waitForTimeout(60);
    }
    await page.waitForTimeout(t(1000));
    return requests.slice(before);
  };

  // A pipeline on the Tasks page: the burst reads nothing; the rows come from the events.
  await page.goto('https://tico-ui.test/#/tasks?pipeline=product');
  await page.waitForFunction(() => (window as unknown as PageWin).__liveStream && TASKS_ST && !TASKS_ST.loading
    && document.querySelector('#task-body')?.textContent?.includes('Draft the release notes'), null, {timeout: t(8000)});
  await settle();
  const board = await burst();
  assert.deepEqual(board.filter(r => r.startsWith('/api/v2/tasks') || r.startsWith('/api/v2/needs-you')), [], `the Tasks page read lists for ${EVENTS} task events`);
  assert.ok(board.length <= CEILING, `the Tasks page made ${board.length} requests for ${EVENTS} task events: ${board.slice(0, 8).join(' ')}`);
  await page.locator('#task-body', {hasText: `Check release link ${EVENTS - 2}`}).waitFor();

  // A bot's page in a hidden tab reads nothing while hidden.
  await page.evaluate(() => { location.hash = '#/bot/ops'; });
  await page.locator('#bot-thread').waitFor({timeout: t(8000)});
  await settle();
  const visible = (shown: boolean) => page.evaluate((shown: boolean) => {
    Object.defineProperty(document, 'hidden', {configurable: true, get: () => !shown});
    document.dispatchEvent(new Event('visibilitychange'));
  }, shown);
  await visible(false);
  const hidden = await burst();
  assert.deepEqual(hidden, [], `a hidden tab made ${hidden.length} requests for ${EVENTS} task events`);

  // Shown again, it reads once; then its task lists read at most once for a whole burst.
  await visible(true);
  await settle();
  const bot = await burst();
  assert.ok(bot.length <= CEILING, `the bot page made ${bot.length} requests for ${EVENTS} task events: ${bot.slice(0, 8).join(' ')}`);

  assert.deepEqual(errors, []);
  console.log(`live events at scale: ${EVENTS} task events, board ${board.length}, bot page ${bot.length}, hidden tab 0 requests: ok`);
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
