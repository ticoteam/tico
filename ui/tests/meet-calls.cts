// Google Meet calls in a thread (docs/dev/meet-calls-contract.md): the header's phone starts a Meet in a new tab (its
// link carries the viewer's Google account) and opens the Tico Recorder on a Mac that has it; the call card live (faces,
// a clock, the recording dot, Join) and ended (its length, Transcript); the header's green pill; the rail's green phone
// coming and going with the call; the ring (a card at the top right, × remembered across a reload, gone when the call
// ends, Join opening the Meet) and on a phone the sheet at 390px, and a push link's `?call=` showing it without a sound.
// TICO_SCREENSHOT_DIR=<dir> also saves the screenshots: meet-calls-<name>-<desktop|phone>-<dark|light>.png.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
declare const window: Window & typeof globalThis & {__chirps: number};

type Page = import('playwright').Page;
type Call = Record<string, unknown> & {id: string; thread_id: string; state: string; ring: boolean};
type ThvView = {id: string};
declare var THV_VIEWS: ThvView[];
declare var threadViewsLive: (kind: string, event: unknown) => unknown;
declare var RING_SOUND: {timer: number};

const shots = process.env.TICO_SCREENSHOT_DIR;
const now = Date.now(), at = (sec: number) => new Date(now - sec * 1000).toISOString();
const MEET = 'https://meet.google.com/abc-defg-hij';
const me = () => ({member: true, following: true, notify: 'live', last_read_seq: 99, unread: 0, activity: false, can_post: true, can_manage: false, pinned: false});
const plan = {id: 't-plan', title: 'launch-plan', audience: 'team', audience_group_id: 'ops', last_seq: 2, last_message_at: at(60), me: me(), members: [], live_call: null};
const alerts = {id: 't-alerts', title: 'aws-alerts', audience: 'team', audience_group_id: 'ops', last_seq: 1, last_message_at: at(60), me: me(), members: [], live_call: null};
const msg = (id: string, seq: number, from: string, body: string, thread = 't-plan', extra: Record<string, unknown> = {}) =>
  ({id, seq, thread_id: thread, sender_id: from, body, created_at: at(600 - seq), reply_to: null, routed: null, ...extra});
// A Google Meet call as Ana's view of it says.
const meetCall = (id: string, thread: string, connector: string, extra: Record<string, unknown> = {}): Call => ({id, thread_id: thread, title: 'Call',
  state: 'live', connector, provider: 'google_meet', meet_uri: MEET, join_url: MEET + '?authuser=ana@acme.example', live_count: 0, status_source: null,
  seen_at: null, started_at: at(30), ended_at: null, duration_s: null, ring: false, card_message_id: 'card-' + id, transcript_message_id: null,
  recorder: {source: null, installed: true}, participants: [{actor: connector, joined_at: at(30), left_at: null, removed_at: null, in_call: 0}], ...extra});

async function open(browser: import('playwright').Browser, {phone = false}: {phone?: boolean} = {}) {
  const context = await browser.newContext({viewport: phone ? {width: 390, height: 844} : {width: 1280, height: 800}, serviceWorkers: 'block', reducedMotion: 'reduce',
    ...(phone ? {isMobile: true, hasTouch: true} : {})});
  const page = await context.newPage();
  const calls = new Map<string, Call>();
  const timeline: Record<string, ReturnType<typeof msg>[]> = {'t-plan': [msg('m1', 1, 'human:bea', 'Pricing page is ready for a look.')], 't-alerts': [msg('a1', 1, 'human:bea', 'All quiet.', 't-alerts')]};
  const seen = {errors: [] as string[], starts: [] as unknown[], joins: [] as string[], ringing: 0};
  const sse: {queue: unknown[]; seq: number; latest: import('playwright').Request | null; done: boolean} = {queue: [], seq: 0, latest: null, done: false};
  page.on('pageerror', e => seen.errors.push(e.message));
  page.on('close', () => { sse.done = true; });
  // A Mac, and a ring that counts its tones instead of sounding them.
  await context.addInitScript(() => {
    Object.defineProperty(navigator, 'userAgentData', {configurable: true, value: {platform: 'macOS'}});
    window.__chirps = 0;
    const Real = window.AudioContext;
    window.AudioContext = class extends Real {
      override createOscillator() { window.__chirps++; return super.createOscillator(); }
    } as typeof AudioContext;
  });
  // The Meet tab lands on a stand-in for Google's page.
  await context.route('https://meet.google.com/**', route => route.fulfill({contentType: 'text/html', body: '<title>Meet</title>'}));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.hostname !== 'tico-ui.test') return route.fallback();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', registered: true});
    if (p === '/api/employees') return json([]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}, {id: 'bea', name: 'Bea', email: 'bea@acme.example'},
      {id: 'cy', name: 'Cy', email: 'cy@acme.example'}], org_groups: [{id: 'ops', name: 'Ops team', my_access: {see: true, read: true, write: true}}]});
    if (p === '/api/v2/events') {
      // A stand-in stream: the newest connection with the threads topic waits for the events the test sends, then ends.
      if (!(url.searchParams.get('topics') || '').split(',').includes('threads')) return route.abort();
      sse.latest = req;
      while (!sse.queue.length || sse.latest !== req) {
        if (sse.latest !== req || sse.done) return route.abort().catch(() => {});
        await new Promise(r => setTimeout(r, 50));
      }
      const body = 'event: ready\ndata: {"seq":1}\n\n' + sse.queue.splice(0).map(d => `event: threads\nid: ${++sse.seq}\ndata: ${JSON.stringify(d)}\n\n`).join('');
      return route.fulfill({status: 200, contentType: 'text/event-stream', body}).catch(() => {});
    }
    const row = (t: typeof plan) => {
      const live = [...calls.values()].find(c => c.thread_id === t.id && c.state === 'live');
      return {...t, live_call: live ? {id: live.id, live_count: 1} : null};
    };
    if (p === '/api/v2/threads/rail') return json({pinned: [], threads: [row(alerts), row(plan)], catch_up: 0, next_cursor: null, dms: {}});
    if (p === '/api/v2/threads' && method === 'GET') return json({attention: [], threads: [row(alerts), row(plan)], dms: [], next_cursor: null});
    const tl = p.match(/^\/api\/v2\/threads\/([^/]+)\/timeline$/);
    if (tl && timeline[tl[1]!]) return json({thread: tl[1] === 't-plan' ? plan : alerts, breadcrumb: [], next_cursor: null, messages: timeline[tl[1]!], bot_status: []});
    const one = p.match(/^\/api\/v2\/threads\/(t-plan|t-alerts)$/);
    if (one) return json(one[1] === 't-plan' ? row(plan) : row(alerts));
    if (/^\/api\/v2\/threads\/[^/]+\/members$/.test(p))
      return json({members: [{actor: 'human:ana'}, {actor: 'human:bea'}, {actor: 'human:cy'}], next_cursor: null});
    const list = p.match(/^\/api\/v2\/threads\/([^/]+)\/calls$/);
    if (list && method === 'POST') {
      seen.starts.push(JSON.parse(req.postData() || '{}'));
      const call = meetCall('c1', list[1]!, 'human:ana', {participants: [{actor: 'human:ana', joined_at: at(0), left_at: null, removed_at: null, in_call: 0}], started_at: at(0)});
      calls.set(call.id, call);
      timeline[list[1]!]!.push(msg('card-c1', 2, 'human:ana', 'Call', list[1]!, {refs: {call: {id: 'c1', card: true}}}));
      await new Promise(r => setTimeout(r, 150));
      return json({call});
    }
    if (list) return json({calls: [...calls.values()].filter(c => c.thread_id === list[1] && c.state === 'live'), next_before: null, available: true, providers: ['google_meet', 'tico']});
    if (p === '/api/v2/calls/ringing') { seen.ringing++; return json({calls: [...calls.values()].filter(c => c.ring && c.state === 'live')}); }
    const call = p.match(/^\/api\/v2\/calls\/([^/]+)(\/join)?$/);
    if (call) {
      const c = calls.get(call[1]!);
      if (!c) return json({error: {code: 'not_found'}}, 404);
      if (call[2]) { seen.joins.push(c.id); c.ring = false; return json({call: c, url: c.join_url}); }
      return json(c);
    }
    if (/^\/api\/v2\/threads\/[^/]+\/read$/.test(p)) return json({ok: true});
    if (p === '/api/v2/rule-holds') return json({holds: [], next_cursor: null});
    return json(unrouted(p));
  });
  return {context, page, seen, sse, calls, timeline};
}
async function shot(page: Page, name: string) {
  if (!shots) return;
  fs.mkdirSync(shots, {recursive: true});
  await page.screenshot({path: path.join(shots, `meet-calls-${name}.png`)});
}
async function themed(page: Page, name: string, phone = false) {
  if (!shots) return;
  for (const theme of ['dark', 'light']) {
    await page.evaluate(t => { document.documentElement.dataset.theme = t; }, theme);
    await page.waitForTimeout(80);
    await shot(page, `${name}-${phone ? 'phone' : 'desktop'}-${theme}`);
  }
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
}
// A change on the `threads` topic for one call, as the server sends it.
const callEvent = (sse: {queue: unknown[]}, kind: string, c: Call) => sse.queue.push({kind, call_id: c.id, thread_id: c.thread_id});
// The stand-in stream says `ready` only with an event: one that changes nothing lets a fresh page connect, as a real
// server's stream does at once (the ring reads GET /calls/ringing then).
const connect = async (sse: {queue: unknown[]}, seen: {ringing: number}, page: Page) => {
  const was = seen.ringing;
  sse.queue.push({kind: 'noop'});
  for (let i = 0; i < 100 && seen.ringing === was; i++) await page.waitForTimeout(50);
  assert.ok(seen.ringing > was, 'the ring read what rings on connect');
  await page.waitForTimeout(150);
};
const sideways = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth);

let browser: import('playwright').Browser | null = null;
(async () => {
  browser = await chromium.launch();

  // ---- Ana starts a call, desktop
  {
    const {context, page, seen, sse, calls} = await open(browser);
    void sideways;
    await page.goto('https://tico-ui.test/#/thread/t-plan');
    const start = page.locator('.thv-head .thv-call[data-call="start"]');
    await start.waitFor();
    await page.locator('.thv-head .thv-people-n').waitFor();
    assert.equal((await start.innerText()).trim(), '', 'the phone, no word');
    // After the members, before the menu.
    assert.ok(await page.evaluate(() => {
      const b = document.querySelector('.thv-head .thv-call')!;
      return b.previousElementSibling?.matches('.thv-people') && !!b.nextElementSibling?.matches('[data-thv-menu]');
    }), 'members, phone, menu');
    await themed(page, 'header-idle');

    // One tap: a tab opens at once and goes to the Meet as Ana's Google account; the Recorder is asked to record it.
    const tabOpened = context.waitForEvent('page');
    await start.click();
    const tab = await tabOpened;
    await tab.waitForURL(u => u.href.startsWith(MEET));
    assert.equal(new URL(tab.url()).searchParams.get('authuser'), 'ana@acme.example');
    assert.deepEqual(seen.starts, [{provider: 'auto'}]);
    const recorder = await page.waitForFunction(() => document.querySelector<HTMLIFrameElement>('iframe.call-recorder-link')?.src || '');
    assert.equal(await recorder.jsonValue(), 'tico-recorder://record?server=' + encodeURIComponent('https://tico-ui.test') + '&call=c1');
    await tab.close();
    // The header is the green pill, counting Ana.
    const pill = page.locator('.thv-head .thv-call.on[data-call="join"]');
    await pill.waitFor();
    assert.equal((await pill.innerText()).trim(), '1');

    // The card the call posted: live, Ana's face, a running clock, Join.
    const c1 = calls.get('c1')!;
    await page.evaluate(() => threadViewsLive('messages', {id: 'card-c1', thread_id: 't-plan', root_id: 't-plan',
      message: {id: 'card-c1', seq: 2, revision: 0, thread_id: 't-plan', sender_id: 'human:ana', body: 'Call', created_at: new Date().toISOString(), refs: {call: {id: 'c1', card: true}}}}));
    const card = page.locator('.th-msg[data-mid="card-c1"] .call-card');
    await card.locator('.call-card-join').waitFor();
    assert.equal(await card.evaluate(e => e.classList.contains('on')), true);
    assert.equal(await card.locator('.call-card-ppl img, .call-card-ppl > span').count(), 1);
    assert.match(await card.locator('.call-card-n').innerText(), /^\d+:\d\d$/);
    assert.equal(await card.locator('.call-rec').count(), 0, 'nobody records it yet');
    // Google sees Bea join and Ana's Recorder takes the call: two faces, the count, the red dot.
    Object.assign(c1, {status_source: 'google', live_count: 2, seen_at: at(252), recorder: {source: 'human:ana', installed: true},
      participants: [{actor: 'human:ana', joined_at: at(252), in_call: 1}, {actor: 'human:bea', joined_at: at(200), in_call: 1}]});
    callEvent(sse, 'call.updated', c1);
    await card.locator('.call-rec[title="Recording"]').waitFor();
    assert.equal(await card.locator('.call-card-ppl > span').count(), 2);
    assert.match(await card.locator('.call-card-n').innerText(), /^4:1\d$/, 'the clock counts from when Google first saw someone');
    await page.waitForFunction(() => document.querySelector('.thv-head .thv-call.on')?.textContent?.trim() === '2');
    await themed(page, 'card-live');

    // Ended: the length and the transcript; the header is the phone again.
    Object.assign(c1, {state: 'ended', ended_at: at(0), duration_s: 1380, transcript_message_id: 'tx1', live_count: 0});
    callEvent(sse, 'call.ended', c1);
    await card.locator('.call-card-link').waitFor();
    assert.equal(await card.evaluate(e => e.classList.contains('on')), false);
    assert.equal(await card.locator('.call-card-n').innerText(), '23 min');
    assert.equal(await card.locator('.call-card-link').getAttribute('href'), '#/thread/t-plan?m=tx1');
    assert.equal(await card.locator('.call-card-join, .call-rec').count(), 0);
    await page.locator('.thv-head .thv-call[data-call="start"]').waitFor();
    await themed(page, 'card-ended');

    // ---- the rail: a green phone on a thread with a live call, gone when it ends
    const mark = page.locator('#thread-rail .tr-row[data-thread="t-alerts"] .tr-call');
    assert.equal(await mark.count(), 0);
    const c2 = meetCall('c2', 't-alerts', 'human:bea');
    calls.set('c2', c2);
    callEvent(sse, 'call.started', c2);
    await mark.waitFor();
    assert.equal(await page.locator('.call-ring-card').count(), 0, 'not rung: only the mark');
    await shot(page, 'rail-mark-desktop-dark');
    c2.state = 'ended';
    callEvent(sse, 'call.ended', c2);
    await mark.waitFor({state: 'detached'});

    // ---- the ring: Bea calls Ana in aws-alerts
    const c3 = meetCall('c3', 't-alerts', 'human:bea', {ring: true});
    calls.set('c3', c3);
    callEvent(sse, 'call.started', c3);
    const ring = page.locator('.call-ring-card[data-ring="c3"]');
    await ring.waitFor();
    assert.equal(await ring.locator('.call-ring-name b').innerText(), 'Bea');
    assert.match(await ring.locator('.call-ring-name .muted').innerText(), /#\s*aws-alerts/);
    const rb = (await ring.boundingBox())!;
    assert.ok(rb.y < 40 && rb.x + rb.width > 1240, 'top right: ' + JSON.stringify(rb));
    assert.ok(await page.evaluate(() => window.__chirps > 0 && RING_SOUND.timer !== 0), 'it rings');
    await themed(page, 'ring');
    // × hides it and the ring stops; the call stays joinable from the thread.
    await ring.locator('.call-ring-x').click();
    await ring.waitFor({state: 'detached'});
    assert.equal(await page.evaluate(() => RING_SOUND.timer), 0);
    // A reload still knows it was dismissed (GET /calls/ringing still lists it).
    await page.reload();
    await page.locator('#thread-rail .tr-row[data-thread="t-alerts"] .tr-call').waitFor();
    await connect(sse, seen, page);
    assert.equal(await page.locator('.call-ring-card').count(), 0, 'dismissed stays dismissed');
    // Another call rings from the page's first read; it goes when that call ends.
    const c4 = meetCall('c4', 't-alerts', 'human:cy', {ring: true});
    calls.set('c4', c4);
    await page.reload();
    await page.locator('#thread-rail .tr-row[data-thread="t-alerts"]').waitFor();
    await connect(sse, seen, page);
    const ring4 = page.locator('.call-ring-card[data-ring="c4"]');
    await ring4.waitFor();
    c4.state = 'ended';
    callEvent(sse, 'call.ended', c4);
    await ring4.waitFor({state: 'detached'});
    // Join on a ring opens the Meet and takes the ring away.
    const c5 = meetCall('c5', 't-alerts', 'human:cy', {ring: true});
    calls.set('c5', c5);
    callEvent(sse, 'call.started', c5);
    const ring5 = page.locator('.call-ring-card[data-ring="c5"]');
    await ring5.waitFor();
    const joinTab = context.waitForEvent('page');
    await ring5.locator('.call-ring-join').click();
    const meet = await joinTab;
    await meet.waitForURL(u => u.href.startsWith(MEET));
    assert.deepEqual(seen.joins, ['c5']);
    await ring5.waitFor({state: 'detached'});
    assert.deepEqual(seen.errors, []);
    await context.close();
  }

  // ---- a phone at 390px: the sheet, and a push link's ?call=
  {
    const {context, page, seen, sse, calls} = await open(browser, {phone: true});
    calls.set('c6', meetCall('c6', 't-alerts', 'human:bea', {ring: true}));
    await page.goto('https://tico-ui.test/#/thread/t-plan');
    await page.locator('.thv-title').waitFor();
    await connect(sse, seen, page);
    const sheet = page.locator('.call-ring-card[data-ring="c6"]');
    await sheet.waitFor();
    const sb = (await sheet.boundingBox())!;
    assert.ok(Math.abs(sb.y + sb.height - 844) < 2 && sb.x === 0 && Math.round(sb.width) === 390, 'a sheet at the bottom: ' + JSON.stringify(sb));
    assert.ok(await sheet.locator('.call-ring-dismiss').isVisible() && await sheet.locator('.call-ring-join').isVisible());
    assert.equal(await sheet.locator('.call-ring-x').isVisible(), false);
    const tap = (await sheet.locator('.call-ring-join').boundingBox())!;
    assert.ok(tap.height >= 44, 'a finger-sized Join');
    assert.ok(await sideways(page) <= 390);
    await themed(page, 'sheet', true);
    await sheet.locator('.call-ring-dismiss').tap();
    await sheet.waitFor({state: 'detached'});
    // A push notification's link: the thread, and the call's Join without a ring (Ana was not rung for this one).
    calls.set('c7', meetCall('c7', 't-alerts', 'human:bea', {status_source: 'google', live_count: 1, seen_at: at(60),
      participants: [{actor: 'human:bea', joined_at: at(60), in_call: 1}]}));
    await page.goto('https://tico-ui.test/#/thread/t-alerts?call=c7');
    await page.locator('.call-ring-card[data-ring="c7"]').waitFor();
    assert.equal(await page.evaluate(() => RING_SOUND.timer), 0, 'no ring for a link');
    await page.locator('.thv-head .thv-call.on').waitFor();
    await themed(page, 'header-live', true);
    assert.deepEqual(seen.errors, []);
    await context.close();
  }
  console.log('meet calls: ok');
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(() => browser?.close());
