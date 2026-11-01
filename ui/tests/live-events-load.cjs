// Live task events at scale (ui/app/live.js, ui/app/refresh.js): every open tab gets every task change, and a busy
// team's bots create and change tasks every few seconds. When each change made a tab read whole task lists, a few
// open tabs kept the server at 100% CPU. This sends 50 task events over about three seconds and caps the requests
// the page makes in reply:
//  - the task board takes new and changed tasks from the events and reads nothing;
//  - a bot's page reads its task lists at most once for the burst;
//  - a hidden tab reads nothing.
// The stream is a stand-in EventSource the test drives, so the events land spread out as they do on a real team,
// not in one batch.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();

const EVENTS = 50;
const CEILING = 5;   // requests a page may make for the whole burst; reading lists per change makes 15 or more
const now = new Date().toISOString();
const task = over => ({id: 't1', title: 'Draft the release notes', body: 'Details.', owner_id: 'bot:ops', requester_id: 'human:ana',
  status: 'doing', lane: 'company', labels: [], tags: [], links: [], parts: {total: 0, done: 0}, version: 3, created_at: now, updated_at: now, ...over});

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({viewport: {width: 1300, height: 820}, serviceWorkers: 'block'});
  const errors = [], requests = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(() => {
    window.EventSource = class {
      constructor(url) {
        this.url = url; this.on = {}; window.__liveStream = this;
        setTimeout(() => this.emit('ready', {seq: 10}), 0);
      }
      addEventListener(type, fn) { (this.on[type] ||= []).push(fn); }
      close() { this.closed = true; }
      emit(type, data) {
        const ev = new MessageEvent(type, {data: JSON.stringify(data), lastEventId: String(data.seq || '')});
        for (const fn of this.on[type] || []) fn(ev);
      }
    };
  });
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname;
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    // Not counted: boot.js re-reads the observability config on a timer, events or not, and on a slow
    // box that read can land inside a burst.
    if (p.startsWith('/api/') && p !== '/api/v2/observability') requests.push(p + url.search);
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true, registered: true});
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', state: 'active', can_chat: true, schedules: []}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
    if (p === '/api/issues') return json([]);
    if (p === '/api/v2/conversations') return json({conversations: url.searchParams.get('chat_with')
      ? [{id: 'c-ops', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:ops']}] : []});
    if (p === '/api/v2/conversations/c-ops/snapshot') return json({messages: [{id: 'm1', sender_id: 'human:ana', body: 'Draft the release notes', created_at: now}], execution: null});
    if (p === '/api/v2/tasks') return json({tasks: [task()], next_offset: null, cursor: '12.a'});
    if (p === '/api/v2/tasks/labels') return json({labels: [], tags: []});
    if (p === '/api/v2/needs-you') return json({items: [], version: 'v0'});
    return json(unrouted(p));
  });

  // Waits until the page has made no request for a while: what it reads on opening is not counted.
  const settle = async () => {
    for (let seen = -1; seen !== requests.length;) { seen = requests.length; await page.waitForTimeout(t(1000)); }
  };
  // EVENTS task events, one every 60 ms: a third new tasks, the rest changes to the bot's own task. Returns what
  // the page asked for while they arrived and for a second after.
  let seq = 10;
  const burst = async () => {
    const before = requests.length;
    for (let i = 0; i < EVENTS; i++) {
      const fresh = i % 3 === 0;
      const d = fresh ? task({id: `n${i}`, title: `Check release link ${i}`, status: 'open'}) : task({title: `Draft the release notes, pass ${i}`, version: 4 + i});
      seq++;
      await page.evaluate(([s, d]) => window.__liveStream.emit('tasks', {seq: s, id: d.id, actor: 'bot:ops', task: d}), [seq, d]);
      await page.waitForTimeout(60);
    }
    await page.waitForTimeout(t(1000));
    return requests.slice(before);
  };

  // The task board: new tasks arrive whole and changes in place; no list is read again.
  await page.goto('https://tico-ui.test/#/board');
  await page.waitForFunction(() => window.__liveStream && TASKS_ST && !TASKS_ST.loading
    && document.querySelector('#task-body .tl-row, #task-body .bcard'), null, {timeout: t(8000)});
  await settle();
  const board = await burst();
  assert.ok(board.length <= CEILING, `the board made ${board.length} requests for ${EVENTS} task events: ${board.slice(0, 8).join(' ')}`);
  await page.locator('#task-body', {hasText: `Check release link ${EVENTS - 2}`}).waitFor();

  // A bot's page in a hidden tab reads nothing while hidden.
  await page.evaluate(() => { location.hash = '#/bot/ops/chat'; });
  await page.waitForFunction(() => V2C?.rendered, null, {timeout: t(8000)});
  await settle();
  const visible = shown => page.evaluate(shown => {
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
