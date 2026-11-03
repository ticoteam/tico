// Reads the live stream makes unneeded (ui/app/threads.js threadsSync):
//  - each stream connect names the Needs you version; a reconnect that carried `threads` over naming the version the
//    threads list was read at reads nothing again; a version that moved reads it, also when another read already took
//    the new version; a connect that did not carry `threads` (the first, or the one adding it) reads it.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {threadsFake} = require('./support/threads-fake.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({viewport: {width: 1300, height: 820}, serviceWorkers: 'block'});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(() => {
    window.EventSource = class {
      constructor(url) { this.url = url; this.on = {}; setTimeout(() => this.emit('ready', {seq: 10, needs_version: 'v1'}), 0); }
      addEventListener(type, fn) { (this.on[type] ||= []).push(fn); }
      close() { this.closed = true; }
      emit(type, data) { for (const fn of this.on[type] || []) fn(new MessageEvent(type, {data: JSON.stringify(data)})); }
    };
  });
  const reads = {needs: 0, threads: 0};
  const threads = threadsFake({bot: 'ops'});
  await page.route('**/*', async route => {
    const url = new URL(route.request().url()), p = url.pathname, method = route.request().method();
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
    if (p === '/api/v2/needs-you' && !url.searchParams.get('count')) {
      reads.needs++;
      const version = reads.version || 'v1';
      return json(url.searchParams.get('version') === version ? {count: 0, version, unchanged: true} : {items: [], count: 0, version, next: null});
    }
    if (p === '/api/v2/threads' && method === 'GET') {
      reads.threads++;
      if (reads.hold) await reads.hold;
      if (reads.failThreads) return route.fulfill({status: 500, contentType: 'application/json', body: '{"error":"down"}'});
    }
    const answer = threads(p, url, method);
    if (answer !== undefined) return json(answer);
    return json(unrouted(p));
  });
  const quiet = () => page.waitForTimeout(t(800));

  await page.goto('https://tico-ui.test/#/updates');
  await page.waitForFunction(() => S.v2.needsVersion === 'v1' && THREADS.needsSeen === 'v1' && liveConnected() && THREADS.on && !THREADS.loading, null, {timeout: t(8000)});
  await quiet();

  // The first connect named v1 and the list was read (the wait above). A reconnect naming it: nothing is read again.
  let before = {...reads};
  await page.evaluate(() => liveEmit('needs', {version: 'v1', connect: true, carried: ['needs', 'threads']}));
  await page.waitForTimeout(t(3500));
  assert.deepEqual(reads, before, 'the version held reads neither Needs you nor the threads list');
  // A version that moved: Needs you and the threads list are read.
  reads.version = 'v2';
  await page.evaluate(() => liveEmit('needs', {version: 'v2', connect: true, carried: ['needs', 'threads']}));
  await page.waitForFunction(() => S.v2.needsVersion === 'v2', null, {timeout: t(5000)});
  for (const end = Date.now() + t(5000); reads.threads === before.threads && Date.now() < end;) await page.waitForTimeout(50);
  assert.ok(reads.threads > before.threads, 'a moved version reads the threads list');
  assert.ok(reads.needs > before.needs, 'a moved version reads Needs you');
  await quiet();

  // A connect that did not carry `threads` over (the first, or the one adding it) still reads the list.
  before = {...reads};
  await page.evaluate(() => liveEmit('needs', {version: THREADS.needsSeen, connect: true, carried: []}));
  for (const end = Date.now() + t(5000); reads.threads === before.threads && Date.now() < end;) await page.waitForTimeout(50);
  assert.ok(reads.threads > before.threads, 'a first connect reads the threads list');
  await quiet();

  // Needs you read at a newer version by another read (the 2-minute poll) before its event comes: the threads list
  // was read at the older one, so the event still reads it.
  before = {...reads};
  await page.evaluate(() => { S.v2.needsVersion = 'v3'; liveEmit('needs', {version: 'v3', connect: true, carried: ['needs', 'threads']}); });
  for (const end = Date.now() + t(5000); reads.threads === before.threads && Date.now() < end;) await page.waitForTimeout(50);
  assert.ok(reads.threads > before.threads, 'a version the threads list was not read at reads it');

  // A read that fails leaves nothing to compare with: the next connect reads the list again.
  await page.waitForFunction(() => !THREADS.loading, null, {timeout: t(5000)});
  reads.failThreads = true;
  await page.evaluate(() => threadsLoad());
  reads.failThreads = false;
  before = {...reads};
  await page.evaluate(() => liveEmit('needs', {version: S.v2.needsVersion, connect: true, carried: ['needs', 'threads']}));
  for (const end = Date.now() + t(5000); reads.threads === before.threads && Date.now() < end;) await page.waitForTimeout(50);
  assert.ok(reads.threads > before.threads, 'a connect after a failed read reads the list');

  // A read 5 minutes old: a connect reads the list again, whatever it carried (a change no event names).
  await page.evaluate(() => threadsLoad());
  await page.waitForFunction(() => !THREADS.loading && THREADS.needsSeen, null, {timeout: t(5000)});
  before = {...reads};
  await page.evaluate(() => { THREADS.readAt = Date.now() - 301e3; liveEmit('needs', {version: THREADS.needsSeen, connect: true, carried: ['needs', 'threads']}); });
  for (const end = Date.now() + t(5000); reads.threads === before.threads && Date.now() < end;) await page.waitForTimeout(50);
  assert.ok(reads.threads > before.threads, 'a connect 5 minutes after the last read reads the list');
  await quiet();
  // A thread change while a read is on its way: that read vouches for nothing, so the next connect reads again.
  assert.ok(await page.evaluate(() => { const g = THREADS.gen; liveEmit('threads', {kind: 'ranked'}); return THREADS.gen > g; }), 'a thread event counts as a change');
  await page.waitForTimeout(t(3500));
  await page.waitForFunction(() => !THREADS.loading, null, {timeout: t(5000)});
  let release;
  reads.hold = new Promise(r => { release = r; });
  before = {...reads};
  await page.evaluate(() => { void threadsLoad(); });
  for (const end = Date.now() + t(5000); reads.threads === before.threads && Date.now() < end;) await page.waitForTimeout(20);
  await page.evaluate(() => { THREADS.gen++; });       // a change while the read is held on its way
  release(); reads.hold = null;
  await page.waitForFunction(() => !THREADS.loading, null, {timeout: t(5000)});
  assert.equal(await page.evaluate(() => THREADS.needsSeen), null, 'a read a change overtook holds no version');

  assert.deepEqual(errors.filter(e => !/500|failed/i.test(e)), []);
  await browser.close();
  console.log('threads-needs-version: ok');
})().catch(e => { console.error(e); process.exit(1); });
