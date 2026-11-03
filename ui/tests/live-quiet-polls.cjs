// Reads the live stream makes unneeded (ui/app/hub-v2.js v2Refresh, ui/app/threads.js threadsSync):
//  - each stream connect names the Needs you version; the version the page already holds reads nothing again, not the
//    threads list either; a version that moved reads it;
//  - the 2-minute clock's poll reads Needs you only every 5 minutes while the stream is connected (the stream mends the
//    list as it changes); any other refresh reads it at once.
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
  await page.route('**/*', route => {
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
    if (p === '/api/v2/threads' && method === 'GET') reads.threads++;
    const answer = threads(p, url, method);
    if (answer !== undefined) return json(answer);
    return json(unrouted(p));
  });
  const quiet = () => page.waitForTimeout(t(800));

  await page.goto('https://tico-ui.test/#/updates');
  await page.waitForFunction(() => S.v2.needsVersion === 'v1' && liveConnected() && THREADS.on && !THREADS.loading, null, {timeout: t(8000)});
  await quiet();

  // A connect naming the version held: nothing is read again.
  let before = {...reads};
  await page.evaluate(() => liveEmit('needs', {version: 'v1'}));
  await page.waitForTimeout(t(3500));
  assert.deepEqual(reads, before, 'the version held reads neither Needs you nor the threads list');
  // A version that moved: Needs you and the threads list are read.
  reads.version = 'v2';
  await page.evaluate(() => liveEmit('needs', {version: 'v2'}));
  await page.waitForFunction(() => S.v2.needsVersion === 'v2', null, {timeout: t(5000)});
  for (const end = Date.now() + t(5000); reads.threads === before.threads && Date.now() < end;) await page.waitForTimeout(50);
  assert.ok(reads.threads > before.threads, 'a moved version reads the threads list');
  assert.ok(reads.needs > before.needs, 'a moved version reads Needs you');
  await quiet();

  // The 2-minute clock's poll while the stream is connected: Needs you only every 5 minutes.
  before = {...reads};
  await page.evaluate(() => refresh(false, true));
  assert.equal(reads.needs, before.needs, 'a poll right after a read leaves Needs you to the stream');
  await page.evaluate(() => { NEEDS_AT = Date.now() - 301e3; return refresh(false, true); });
  assert.equal(reads.needs, before.needs + 1, 'five minutes on, the poll reads it as a precaution');
  await page.evaluate(() => refresh(false));
  assert.equal(reads.needs, before.needs + 2, 'a refresh after something the person did reads it at once');
  // Without the stream, every poll reads it.
  await page.evaluate(() => { LIVE.connected = false; return refresh(false, true); });
  assert.equal(reads.needs, before.needs + 3, 'no stream: every poll reads it');

  assert.deepEqual(errors, []);
  await browser.close();
  console.log('live-quiet-polls: ok');
})().catch(e => { console.error(e); process.exit(1); });
