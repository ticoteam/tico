// The open Tasks list never drifts for long (ui/app/refresh.js, tasks-page.js, pull-refresh.js). After one full read
// the page takes only what the change log names (a delta read from its cursor, live events), so a change it never
// names stayed out until a reload, and an installed phone app has no reload button. The fixture's delta read misses
// every change and its full read answers 304 to any tag the page holds, as the server does when the change log has
// not moved:
//  - a question asked outside any task, folded on For you, does not stop the 2-minute refresh (it held the list for
//    hours: its answer form counted as someone typing);
//  - pulling down at the top on a phone shows a spinner, holds the page still, and on release reads the list in full
//    with no tag, bringing in the change the delta read missed; a pull mid-list only scrolls;
//  - a full read that fails leaves the list as it was, also when its first page came and a later one failed: the
//    rows past the first page stay, the failure shows, and a retry read to the end drops only what left the list;
//  - coming back after more than 10 minutes away (hidden, or asleep with the timers stopped) reads in full and
//    reconnects the live stream; a short time away reads only the delta;
//  - a list read in full 15 minutes ago is read in full again, but not while an answer is being written in it.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();

const now = new Date().toISOString();
const task = (id, title, over) => ({id, title, body: 'Details.', owner_id: 'human:ana', requester_id: 'bot:ops', status: 'open', lane: 'company',
  labels: [], tags: [], links: [], parts: {total: 0, done: 0}, version: 3, created_at: now, updated_at: now, ...over});
const LOOSE = {kind: 'ask', id: 'm-loose', message_id: 'm-loose', title: 'Is Friday fine for the summary?', first_line: 'Is Friday fine for the summary?',
  body: 'Is Friday fine for the summary?', requester_id: 'bot:ops', origin_actor: 'bot:ops', created_at: now,
  ask: {id: 'm-loose', sender_id: 'bot:ops', recipient_id: 'human:ana', body: 'Is Friday fine for the summary?', created_at: now}};

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true, serviceWorkers: 'block'});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  // `second`: the open tasks past the first page (null: one page); `failSecond` fails the read of that page.
  const server = {title: 'Draft the release notes', fail: false, second: null, failSecond: false};
  const reads = {full: [], delta: 0};
  await page.addInitScript(() => {
    window.__streams = 0;
    window.EventSource = class {
      constructor(url) { this.url = url; this.on = {}; window.__streams++; setTimeout(() => this.emit('ready', {seq: 10}), 0); }
      addEventListener(type, fn) { (this.on[type] ||= []).push(fn); }
      close() { this.closed = true; }
      emit(type, data) { for (const fn of this.on[type] || []) fn(new MessageEvent(type, {data: JSON.stringify(data)})); }
    };
    // The page's visibility, as the test sets it.
    window.__hidden = false;
    Object.defineProperty(document, 'hidden', {configurable: true, get: () => window.__hidden});
    Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => window.__hidden ? 'hidden' : 'visible'});
  });
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname;
    const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true, registered: true});
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', state: 'active', can_chat: true, schedules: []}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
    if (p === '/api/issues') return json([]);
    if (p === '/api/v2/tasks' && url.searchParams.get('changed_after')) { reads.delta++; return json({tasks: [], gone: [], cursor: '13.a', next_offset: null}); }
    if (p === '/api/v2/tasks' && (url.searchParams.get('status') || '').includes('open')) {
      const tag = route.request().headers()['if-none-match'] || '';
      reads.full.push(tag);
      const later = url.searchParams.get('offset') !== '0';
      if (server.fail || later && server.failSecond) return route.fulfill({status: 503, contentType: 'application/json', body: '{"error":"away"}'});
      if (tag) return route.fulfill({status: 304, headers: {ETag: tag}, body: ''});
      const rows = later ? server.second || [] : [task('t1', server.title), ...Array.from({length: 40}, (_, i) => task(`r${i}`, `Check release link ${i}`))];
      const next = later || !server.second ? null : 100;
      return route.fulfill({contentType: 'application/json', headers: {ETag: `W/"v12.${later ? 'p2' : 'p1'}"`}, body: JSON.stringify({tasks: rows, next_offset: next, cursor: '12.a'})});
    }
    if (p === '/api/v2/tasks') return json({tasks: [], next_offset: null});
    if (p === '/api/v2/tasks/labels') return json({labels: [], tags: []});
    if (p === '/api/v2/needs-you') return json({items: [LOOSE], count: 1, version: 'v1'});
    return json(unrouted(p));
  });
  const shows = title => page.locator('#task-body', {hasText: title}).waitFor({timeout: t(8000)});
  const until = async (ok, what) => {
    for (const end = Date.now() + t(8000); !ok();) {
      if (Date.now() > end) throw new Error('timed out: ' + what);
      await new Promise(r => setTimeout(r, 50));
    }
  };
  const settled = () => page.waitForFunction(() => TASKS_ST && !TASKS_ST.fullRead && !LIVE_SOON.has('tasks-catch-up'), null, {timeout: t(8000)});

  await page.goto('https://tico-ui.test/#/tasks');
  await shows('Draft the release notes');
  await page.waitForFunction(() => TASKS_ST.since === '12.a' && TASKS_ST.fullAt > 0);
  await settled();

  // A question asked outside any task is one line on Needs you (its answer is on its card); the poll still reads.
  assert.equal(await page.locator('#task-body [data-need-card="ask:m-loose"]').count(), 1);
  let delta = reads.delta;
  await page.evaluate(() => refresh(false));
  await page.waitForFunction(() => TASKS_ST.since === '13.a', null, {timeout: t(5000)});
  assert.equal(reads.delta, delta + 1, 'the 2-minute refresh reads what changed with a question on the page');

  // The task changed in a way the delta read does not bring: the list still shows the old copy.
  server.title = 'Draft the final release notes';
  delta = reads.delta;
  await page.evaluate(() => refresh(false));
  await until(() => reads.delta > delta, 'the delta read');
  await settled();
  assert.equal(await page.locator('#task-body', {hasText: 'Draft the final release notes'}).count(), 0);

  // Pulling down at the top: a spinner, the page held still, and on release a full read with no tag.
  const cdp = await page.context().newCDPSession(page);
  const touch = (type, y, x = 195) => cdp.send('Input.dispatchTouchEvent', {type, touchPoints: type === 'touchEnd' ? [] : [{x, y}]});
  await page.evaluate(() => {
    window.__prevented = 0;
    document.addEventListener('touchmove', ev => { if (ev.defaultPrevented) window.__prevented++; }, {passive: true});
  });
  const top = async () => (await page.locator('#task-body').boundingBox()).y;
  const before = await top(), fullReads = reads.full.length;
  await touch('touchStart', 220);
  for (let y = 240; y <= 420; y += 30) await touch('touchMove', y);
  await page.locator('.pull-spin.armed').waitFor({timeout: t(3000)});
  assert.equal(await top(), before, 'nothing else moves while pulling');
  assert.equal(await page.evaluate(() => document.scrollingElement.scrollTop), 0);
  assert.ok(await page.evaluate(() => window.__prevented) > 0, 'the pull holds the page still');
  assert.equal(reads.full.length, fullReads, 'nothing is read until the pull is let go');
  await touch('touchEnd');
  await shows('Draft the final release notes');
  assert.deepEqual(reads.full.slice(fullReads), [''], 'one full read, without the tag a 304 would answer');
  await page.waitForFunction(() => !document.querySelector('.pull-spin'), null, {timeout: t(5000)});
  await settled();
  assert.equal(await page.evaluate(() => TASKS_ST.since), '12.a', 'the full read starts a new cursor');

  // A pull too short to arm: the spinner goes back, nothing is read.
  await touch('touchStart', 220);
  for (let y = 230; y <= 280; y += 25) await touch('touchMove', y);
  await page.locator('.pull-spin:not(.armed)').waitFor({timeout: t(3000)});
  await touch('touchEnd');
  await page.waitForFunction(() => !document.querySelector('.pull-spin'), null, {timeout: t(3000)});
  assert.equal(reads.full.length, fullReads + 1);

  // A pull mid-list just scrolls: no spinner, no read, the touch left to the browser.
  await page.evaluate(() => { document.scrollingElement.scrollTop = 600; window.__prevented = 0; });
  assert.ok(await page.evaluate(() => document.scrollingElement.scrollTop) > 0, 'the list is long enough to scroll on a phone');
  await touch('touchStart', 300);
  for (let y = 330; y <= 480; y += 30) {
    await touch('touchMove', y);
    assert.equal(await page.locator('.pull-spin').count(), 0, 'no spinner mid-list');
  }
  await touch('touchEnd');
  await page.waitForTimeout(t(300));
  assert.equal(await page.evaluate(() => window.__prevented), 0, 'a pull mid-list is the browser\'s scroll');
  assert.equal(reads.full.length, fullReads + 1, 'a pull mid-list reads nothing');
  // The swipe's fling ends before the page goes back to the top.
  const still = () => page.waitForFunction(() => new Promise(done => {
    const at = document.scrollingElement.scrollTop;
    setTimeout(() => done(document.scrollingElement.scrollTop === at), 200);
  }), null, {timeout: t(5000)});
  await still();
  await page.evaluate(() => { document.scrollingElement.scrollTop = 0; });
  await still();

  // A full read that fails keeps the list on screen and says so.
  server.fail = true;
  await touch('touchStart', 220);
  for (let y = 240; y <= 420; y += 30) await touch('touchMove', y);
  await page.locator('.pull-spin.armed').waitFor({timeout: t(3000)});
  await touch('touchEnd');
  await page.locator('#toast, .toast', {hasText: 'Could not refresh the tasks'}).first().waitFor({timeout: t(5000)});
  await settled();
  assert.ok(await page.locator('#task-body', {hasText: 'Draft the final release notes'}).count(), 'a failed read leaves the list as it was');
  server.fail = false;
  await page.evaluate(() => tasksReadFull(TASKS_ST));      // back: the list has its cursor again
  await settled();

  // A list of two pages: the second page failing on a refresh keeps its rows, says so, and leaves no cursor; a retry
  // read to the end brings the cursor back and drops only the task that left the list.
  const pull = async () => {
    await page.evaluate(() => document.querySelectorAll('.toast').forEach(el => el.remove()));
    await touch('touchStart', 220);
    for (let y = 240; y <= 420; y += 30) await touch('touchMove', y);
    await page.locator('.pull-spin.armed').waitFor({timeout: t(3000)});
    await touch('touchEnd');
    await page.waitForFunction(() => !document.querySelector('.pull-spin'), null, {timeout: t(8000)});
    await settled();
  };
  const ids = () => page.evaluate(() => TASKS_ST.tasks.map(x => x.id));
  server.second = [task('p1', 'Renew the domain'), task('p2', 'Archive the old board')];
  await page.evaluate(() => tasksReadFull(TASKS_ST));
  await settled();
  assert.ok((await ids()).includes('p2'), 'the second page is read');
  server.title = 'Draft the final release notes, again';
  server.failSecond = true;
  await pull();
  await page.locator('.toast', {hasText: 'Could not refresh the tasks'}).first().waitFor({timeout: t(5000)});
  await shows('Draft the final release notes, again');
  const kept = await ids();
  assert.ok(kept.includes('p1') && kept.includes('p2'), 'the rows past the failed page stay');
  assert.deepEqual(await page.evaluate(() => [TASKS_ST.since, TASKS_ST.activeComplete]), [null, false], 'a part-read list keeps no cursor');
  server.failSecond = false;
  server.second = [task('p1', 'Renew the domain')];
  await pull();
  assert.equal(await page.locator('.toast', {hasText: 'Could not refresh the tasks'}).count(), 0, 'a retry that reads every page says nothing');
  assert.equal(await page.evaluate(() => TASKS_ST.since), '12.a', 'a list read to its end has its cursor again');
  const after = await ids();
  assert.ok(after.includes('p1') && !after.includes('p2') && after.includes('r39'), 'only the task that left the list goes');
  server.second = null;
  await page.evaluate(() => tasksReadFull(TASKS_ST));
  await settled();

  const away = async ms => {
    await page.evaluate(() => { window.__hidden = true; document.dispatchEvent(new Event('visibilitychange')); });
    await page.evaluate(ms => { tasksHiddenAt = Date.now() - ms; window.__hidden = false; document.dispatchEvent(new Event('visibilitychange')); }, ms);
  };
  // A minute away: only what changed (a delta read), as before.
  server.title = 'Draft the release notes, third pass';
  let full = reads.full.length; delta = reads.delta;
  await away(60e3);
  await until(() => reads.delta > delta, 'the catch-up');
  await page.waitForTimeout(t(500));
  await settled();
  assert.equal(reads.full.length, full, 'a short time away reads no full list');
  assert.ok(reads.delta > delta, 'a short time away catches up from the cursor');

  // More than 10 minutes away: the list is read in full and the stream reconnects (it may have died unheard).
  const streams = await page.evaluate(() => window.__streams);
  await away(11 * 60e3);
  await shows('Draft the release notes, third pass');
  assert.equal(reads.full.at(-1), '', 'read without the tag');
  assert.ok(await page.evaluate(() => window.__streams) > streams, 'the live stream reconnects');
  await settled();

  // Back while the server is down: the stream reconnects once, and the ticks that retry the full read leave it to its
  // own backoff.
  server.fail = true;
  let tries = reads.full.length;
  await away(11 * 60e3);
  await until(() => reads.full.length > tries, 'the full read on return');
  await settled();
  const resumed = await page.evaluate(() => window.__streams);
  tries = reads.full.length;
  for (let i = 0; i < 2; i++) { await page.evaluate(() => tasksTick()); await settled(); }
  assert.ok(reads.full.length > tries, 'each tick retries the full read while the list is stale');
  assert.equal(await page.evaluate(() => window.__streams), resumed, 'the retries leave the stream alone');
  server.fail = false;
  await page.evaluate(() => tasksTick());
  await settled();
  assert.equal(await page.evaluate(() => TASKS_ST.stale), false, 'a read that lands clears the stale mark');

  // Asleep with the timers stopped and the page never hidden: the gap between two ticks counts as time away.
  server.title = 'Draft the release notes, fourth pass';
  await page.evaluate(() => { tasksTickAt = Date.now() - 11 * 60e3; tasksTick(); });
  await shows('Draft the release notes, fourth pass');
  await settled();

  // The 15-minute clock: a list read in full that long ago is read again. An answer half written on the question's
  // card, over the list, keeps its words.
  server.title = 'Draft the release notes, fifth pass';
  full = reads.full.length;
  await page.locator('#task-body [data-need-card="ask:m-loose"] .tl-title').click();
  await page.locator('#need-card .dc-note textarea').fill('Friday is fine');
  await page.evaluate(() => { TASKS_ST.fullAt = Date.now() - 16 * 60e3; tasksTick(); });
  await shows('Draft the release notes, fifth pass');
  await settled();
  assert.equal(await page.locator('#need-card .dc-note textarea').inputValue(), 'Friday is fine', 'the half-written answer stays');
  await page.evaluate(() => tasksTick());
  await page.waitForTimeout(t(300));
  assert.equal(reads.full.length, full + 1, 'a list just read in full is not read again on the next tick');

  assert.deepEqual(errors, []);
  await browser.close();
  console.log('tasks-refresh: ok');
})().catch(e => { console.error(e); process.exit(1); });
