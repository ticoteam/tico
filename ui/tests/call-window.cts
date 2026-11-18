// The meeting window of a Tico Meet call (#/call/<id>, ui/app/call-window.ts, docs/meet.md "The meeting window"): small
// by default, with one face per person in the call's view and per bot (a ring on who speaks, the mute mark winning over
// it, a breathing ring on a thinking bot, no face for anyone the room has that the view does not, the recorder included)
// and the caption with the voice's note; the transcript applied from the meeting's event stream; the chat without the
// call's own posts and its + menu; ringing a person; a bot's face tapped out with Undo and back in; wide while someone
// shares; the window joining only when opened to (`?join=1`); a big roster keeping the controls on screen; the phone
// layout at 390px; the call ending while the window is open; and the call's notes (summary, items with owners, the
// transcript). `bots` comes both ways: a slug, or the bot with its state; the states come in `voice.bots`.
// TICO_SCREENSHOT_DIR=<dir> also saves the screenshots: call-window-<name>-<desktop|phone>-<dark|light>.png.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile, isUiPath, serveUi} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
type Handler = (...a: unknown[]) => void;
type FakeRoom = {handlers: Map<string, Handler[]>; remoteParticipants: Map<string, Record<string, unknown>>; emit(ev: string, ...a: unknown[]): void};
declare const window: Window & typeof globalThis & {LivekitClient: unknown; __rooms: FakeRoom[]; __disconnects: number; __streams: EventSource[]};
declare function callViewRead(id: string): Promise<unknown>;
declare function callVoiceDraw(box: HTMLElement, voice: unknown): void;

type Page = import('playwright').Page;
const shots = process.env.TICO_SCREENSHOT_DIR;
const now = Date.now(), at = (sec: number) => new Date(now - sec * 1000).toISOString();
const me = () => ({member: true, following: true, notify: 'live', last_read_seq: 9, unread: 0, activity: false, can_post: true, can_manage: false, pinned: false});
const plan = {id: 't-plan', title: 'Growth weekly', audience: 'team', audience_group_id: 'growth', last_seq: 4, last_message_at: at(60), me: me(), members: [], live_call: null};
const msg = (id: string, seq: number, from: string, body: string, extra: Record<string, unknown> = {}) =>
  ({id, seq, thread_id: 't-plan', sender_id: from, body, created_at: at(600 - seq), reply_to: null, routed: null, ...extra});
const joined = (actor: string, sec: number) => ({actor, joined_at: at(sec), left_at: null, removed_at: null});
type Bot = string | {slug: string; state?: string; caption?: string | null};
// The call as Ana's view says, live: Ana, Thomaz and Bea in it; the Growth Manager a plain slug whose state (thinking)
// comes in `voice`, and Analytics a bot with its state (an older server).
const liveCall = (big = false) => ({id: 'c1', thread_id: 't-plan', title: 'Call', state: 'live', provider: 'tico', connector: 'human:ana', meeting_id: 'lm1',
  started_at: at(1122), ended_at: null as string | null, duration_s: null as number | null, ring: false, live_count: 3, transcript_message_id: null as string | null,
  participants: [joined('human:ana', 1122), joined('human:thomaz', 1000), joined('human:bea', 900),
    ...(big ? Array.from({length: 20}, (_, i) => joined(`human:p${i}`, 800)) : [])],
  bots: ['growth-manager', {slug: 'analytics', state: 'listening'}] as Bot[],
  voice: {bots: [{slug: 'growth-manager', state: 'thinking', caption: null}], note: null as string | null}});
const endedCall = () => ({...liveCall(), id: 'c2', state: 'ended', ended_at: at(0), duration_s: 1440, meeting_id: 'lm2', bots: [] as Bot[], voice: null,
  transcript_message_id: 'n2'});
const notes = (owner: string) => ({pending: false, message_id: 'n1', summary: ['Signups are up 14%, most from the Reddit post.', 'Four demos no-showed on Tuesday.',
  'Another post goes out on Thursday.'], items: [{title: 'Draft Thursday\'s Reddit post', owner}, {title: 'Find why four demos no-showed', owner: 'human:thomaz'},
  {title: 'Track Reddit as a source in the weekly report', owner: ''}]});
// The meeting's stream: one piece corrected, one new (a bot's).
const STREAM = 'retry: 600000\n\n'
  + 'event: meeting.chunk_corrected\ndata: {"seq":2,"revision":2,"speaker":"thomaz","start_ms":1100000,"end_ms":1104000,"text":"Analytics, which post drove the signup spike?"}\n\n'
  + 'event: meeting.chunk\ndata: {"seq":3,"revision":1,"speaker":"growth-manager","start_ms":1110000,"end_ms":1112000,"text":"Reddit, the Thursday post."}\n\n';

async function open(browser: import('playwright').Browser, {phone = false, big = false, lm2 = true}: {phone?: boolean; big?: boolean; lm2?: boolean} = {}) {
  const context = await browser.newContext({viewport: phone ? {width: 390, height: 844} : {width: 420, height: 680}, serviceWorkers: 'block', reducedMotion: 'reduce',
    ...(phone ? {isMobile: true, hasTouch: true} : {})});
  const page = await context.newPage();
  const call = liveCall(big);
  const seen = {errors: [] as string[], joins: 0, botWrites: [] as string[], tasks: [] as unknown[], rings: [] as string[], memberWrites: 0, meetingReads: 0,
    streams: [] as string[], notes: notes('bot:content-social')};
  page.on('pageerror', e => seen.errors.push(e.message));
  // A stand-in media client: its rooms keep their handlers so the test can say who speaks and who shares. The room has
  // people the call's view does not list (the recorder, someone already gone): they are no one's face.
  await page.addInitScript(() => {
    window.__rooms = [];
    window.__disconnects = 0;
    window.__streams = [];
    const Stream = window.EventSource;
    window.EventSource = class extends Stream { constructor(url: string | URL, init?: EventSourceInit) { super(url, init); window.__streams.push(this); } };
    window.LivekitClient = {
      RoomEvent: {TrackSubscribed: 'ts', TrackUnsubscribed: 'tu', ParticipantConnected: 'pc', ParticipantDisconnected: 'pd', LocalTrackUnpublished: 'lu',
        Disconnected: 'd', ActiveSpeakersChanged: 'as', TrackMuted: 'tm', TrackUnmuted: 'tum'},
      Track: {Source: {ScreenShare: 'screen'}}, DisconnectReason: {PARTICIPANT_REMOVED: 'removed'},
      Room: class {
        handlers = new Map<string, Handler[]>();
        remoteParticipants = new Map<string, Record<string, unknown>>([['tico-recorder-c1', {identity: 'tico-recorder-c1', isSpeaking: true}],
          ['human:ghost', {identity: 'human:ghost', isSpeaking: true, isMicrophoneEnabled: true}],
          ['human:thomaz', {identity: 'human:thomaz', isSpeaking: true, isMicrophoneEnabled: true}],
          // Muted wins: the room hears Bea, but her mic is off.
          ['human:bea', {identity: 'human:bea', isSpeaking: true, isMicrophoneEnabled: false}]]);
        localParticipant = {identity: 'human:ana', isSpeaking: false, trackPublications: new Map(), setMicrophoneEnabled: async () => {}, setScreenShareEnabled: async () => {}};
        canPlaybackAudio = true;
        constructor() { window.__rooms.push(this as unknown as FakeRoom); }
        on(ev: string, fn: Handler) { this.handlers.set(ev, [...(this.handlers.get(ev) || []), fn]); return this; }
        emit(ev: string, ...a: unknown[]) { for (const fn of this.handlers.get(ev) || []) fn(...a); }
        async connect() {}
        disconnect() { window.__disconnects++; }
      },
    };
  });
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.hostname !== 'tico-ui.test') return route.fallback();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    // The fonts too: the screenshots show the icons.
    if (p.startsWith('/vendor/fonts/') && isUiPath(p)) return serveUi(route, p);
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/api/me') return json({id: 'ana', name: 'Chris', role: 'owner', registered: true});
    const bot = (name: string, display_name: string) => ({name, display_name, host: 'h', status: 'active', state: 'active', can_chat: true, schedules: [],
      my_access: {see: true, read: true, write: true}});
    if (p === '/api/employees') return json([bot('growth-manager', 'Growth Manager'), bot('analytics', 'Analytics'), bot('content-social', 'Content & Social'),
      bot('growth-analyst', 'Growth Analyst')]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Chris', email: 'chris@acme.example'}, {id: 'thomaz', name: 'Thomaz', email: 'thomaz@acme.example'},
      {id: 'bea', name: 'Bea', email: 'bea@acme.example'}, {id: 'dana', name: 'Dana', email: 'dana@acme.example'}],
      org_groups: [{id: 'growth', name: 'Growth', bots: ['growth-manager', 'analytics', 'content-social', 'growth-analyst'], my_access: {see: true, read: true, write: true}}]});
    if (p === '/api/v2/live-meetings/lm1/events') {
      seen.streams.push(url.search);
      return route.fulfill({contentType: 'text/event-stream', body: STREAM});
    }
    if (p === '/api/v2/events' || /\/events$/.test(p)) return route.abort();
    if (p === '/api/v2/threads/rail') return json({pinned: [], threads: [plan], catch_up: 0, next_cursor: null, dms: {}});
    if (p === '/api/v2/threads' && method === 'GET') return json({attention: [], threads: [plan], dms: [], next_cursor: null});
    // The call's own posts are in the thread; its chat leaves them out.
    if (p === '/api/v2/threads/t-plan/timeline') return json({thread: plan, breadcrumb: [], next_cursor: null, bot_status: [], messages: [
      msg('m1', 1, 'bot:analytics', 'Demos dipped 4%. Four no-shows on Tuesday.'),
      msg('m2', 2, 'human:ana', 'Growth Manager, what moved signups?'),
      msg('n1', 3, 'human:ana', 'Action items: Call\n- Draft Thursday\'s Reddit post\n- Find why four demos no-showed', {refs: {call: {id: 'c2'}, live_meeting: {id: 'lm2'}}}),
      msg('n2', 4, 'human:ana', 'Call transcript: Call\nana: Numbers first, then the launch post.', {refs: {call: {id: 'c2'}, live_meeting: {id: 'lm2'}}})]});
    if (p === '/api/v2/threads/t-plan') return json(plan);
    if (p === '/api/v2/threads/t-plan/members' && method === 'GET') return json({members: [{actor: 'human:ana'}, {actor: 'human:thomaz'}, {actor: 'bot:content-social'}], next_cursor: null});
    if (p.startsWith('/api/v2/threads/t-plan/members')) { seen.memberWrites++; return json({ok: true}); }
    if (p === '/api/v2/threads/t-plan/calls') return json({calls: [call], next_before: null, available: true, providers: ['tico', 'google_meet']});
    if (p === '/api/v2/calls/c1') return json(call);
    if (p === '/api/v2/calls/c2') return json(endedCall());
    if (p === '/api/v2/calls/c1/notes' || p === '/api/v2/calls/c2/notes') return json(seen.notes);
    if (p === '/api/v2/calls/c1/join') { seen.joins++; return json({call, token: 'tok', url: 'wss://lk.test'}); }
    if (p === '/api/v2/calls/c1/leave') return json(call);
    if (p === '/api/v2/calls/c1/ring' && method === 'POST') {
      const {actor} = JSON.parse(req.postData() || '{}') as {actor: string};
      seen.rings.push(actor);
      return json({actor, added: true});
    }
    if (p === '/api/v2/calls/c1/bots' && method === 'POST') {
      const {bots} = JSON.parse(req.postData() || '{}') as {bots: string[]};
      seen.botWrites.push('add:' + bots.join(','));
      call.bots = [...call.bots, ...bots];
      return json(call);
    }
    const out = p.match(/^\/api\/v2\/calls\/c1\/bots\/([^/]+)$/);
    if (out && method === 'DELETE') {
      seen.botWrites.push('remove:' + out[1]);
      call.bots = call.bots.filter(b => (typeof b === 'string' ? b : b.slug) !== out[1]);
      return json(call);
    }
    if (p === '/api/v2/live-meetings/lm1') {
      seen.meetingReads++;
      return json({id: 'lm1', event_id: 7, bots: [], chunks: [{seq: 1, revision: 1, speaker: 'ana', start_ms: 1000, text: 'Numbers first, then the launch post.'},
        {seq: 2, revision: 1, speaker: 'thomaz', start_ms: 1100000, text: 'Analytics, which post drove the spike?'}]});
    }
    if (p === '/api/v2/live-meetings/lm2') {
      if (!lm2) return json({error: {code: 'not_found'}}, 404);
      return json({id: 'lm2', event_id: 9, bots: [], chunks: [{seq: 1, speaker: 'ana', start_ms: 42000, text: 'Numbers first, then the launch post.'},
        {seq: 2, speaker: 'thomaz', start_ms: 1290000, text: 'Let\'s do another one on Thursday.'},
        {seq: 3, speaker: 'content-social', start_ms: 1298000, text: 'I\'ll draft it today.'}]});
    }
    // A viewer who cannot read the meeting: the transcript the call posted.
    if (p === '/api/v2/messages/n2') return json(msg('n2', 4, 'human:ana', 'Call transcript: Call\nana: Numbers first, then the launch post.\nthomaz: Let\'s do another one on Thursday.'));
    if (p === '/api/v2/tasks' && method === 'POST') {
      const body = JSON.parse(req.postData() || '{}');
      seen.tasks.push(body);
      return json({task: {id: 'k' + seen.tasks.length, title: body.title}});
    }
    if (/\/read$/.test(p)) return json({ok: true});
    if (p === '/api/v2/rule-holds') return json({holds: [], next_cursor: null});
    return json(unrouted(p));
  });
  return {context, page, seen, call};
}
async function themed(page: Page, name: string, phone = false) {
  if (!shots) return;
  fs.mkdirSync(shots, {recursive: true});
  for (const theme of ['dark', 'light']) {
    // The theme is drawn by the next frame.
    await page.evaluate(t => { document.documentElement.dataset.theme = t; return new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))); }, theme);
    await page.screenshot({path: path.join(shots, `call-window-${name}-${phone ? 'phone' : 'desktop'}-${theme}.png`)});
  }
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
}
const faces = (page: Page) => page.locator('.cw-faces .cw-face:not(.cw-add):not(.cw-more)').evaluateAll(els => els.map(e =>
  `${e.querySelector('.cw-name')?.textContent}${e.classList.contains('cw-speaking') ? ' speaking' : ''}${e.classList.contains('cw-thinking') ? ' thinking' : ''}${
    e.querySelector('.cw-mute') ? ' muted' : ''}${e.classList.contains('cw-out') ? ' out' : ''}`));
const sideways = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth);
const inView = async (page: Page, selector: string, height: number) => { const b = (await page.locator(selector).boundingBox())!; return b.y >= 0 && b.y + b.height <= height; };
let browser: import('playwright').Browser | null = null;
(async () => {
  browser = await chromium.launch();

  // ---- the small window, opened to join: faces, caption, tabs
  {
    const {context, page, seen, call} = await open(browser);
    await page.goto('https://tico-ui.test/#/call/c1?join=1');
    await page.locator('.cw-bar .cw-leave').waitFor();
    assert.equal(seen.joins, 1, 'the window joins the call it was opened to');
    assert.equal(await page.evaluate(() => location.hash), '#/call/c1', 'the join flag is used once');
    assert.equal(await page.locator('.cw-title').innerText(), 'Growth weekly');
    assert.match(await page.locator('.cw-meta').innerText(), /^Live · 18:4\d$/);
    await page.evaluate(() => window.__rooms[0]!.emit('as'));
    await page.waitForFunction(() => document.querySelector('.cw-face.cw-speaking'));
    assert.deepEqual(await faces(page), ['Chris', 'Thomaz speaking', 'Bea muted', 'Growth Manager thinking', 'Analytics', 'Content & Social out'],
      'the people of the call view, then bots (a thread member bot faint); nobody else the room has');
    assert.equal(await page.locator('.cw-faces .cw-breath').count(), 1, 'a breathing ring on the thinking bot');
    assert.equal(await page.locator('.cw-faces .cw-add').count(), 1);
    // The transcript: read once, then the stream from where the read left off (a corrected piece, a new one); never read again.
    await page.waitForFunction(() => document.querySelectorAll('.cw-transcript .cw-turn').length === 3);
    assert.deepEqual(seen.streams, ['?after=7']);
    assert.equal(seen.meetingReads, 1, 'the meeting is read once');
    assert.match(await page.locator('.cw-transcript .cw-turn[data-seq="2"]').innerText(), /signup spike/);
    // The caption is whoever the room hears now (Thomaz, his latest words), and only his face has the ring.
    await page.waitForFunction(() => document.querySelector('.cw-caption-head')?.textContent?.includes('signup spike'));
    assert.match(await page.locator('.cw-caption-head .cw-said').innerText(), /^Thomaz\s+Analytics, which post drove the signup spike\?$/);
    assert.equal(await page.locator('.cw-faces .cw-speaking').count(), 1);
    assert.equal(await page.locator('.cw-caption-head .cw-note').count(), 0, 'no note');
    // The faces and the + fit one row at 420px.
    const tops = await page.locator('.cw-faces .cw-face').evaluateAll(els => new Set(els.map(e => Math.round(e.getBoundingClientRect().top))).size);
    assert.equal(tops, 1, 'one row of faces');
    assert.deepEqual(await page.locator('.cw-tab').allInnerTexts(), ['Chat', 'Transcript', 'People 5']);
    // The bots' voice in the thread's call bar (thread-call-live, which the window loads too): dots while a bot thinks, its sentence while it
    // speaks, a note when none answers.
    assert.deepEqual(await page.evaluate(() => {
      const box = document.createElement('div');
      const shown = (voice: unknown) => { callVoiceDraw(box, voice); return box.hidden ? null : [box.querySelector('.typing') ? 'dots' : '', box.querySelector('.call-caption')?.textContent ?? '']; };
      return [
        shown(null),
        shown({bots: [{slug: 'ops', state: 'listening'}], note: null}),
        shown({bots: [{slug: 'ops', state: 'thinking'}], note: null}),
        shown({bots: [{slug: 'ops', state: 'waiting'}, {slug: 'finance', state: 'speaking', caption: 'It costs less.'}], note: null}),
        shown({bots: [{slug: 'ops', state: 'listening'}], note: 'Please repeat that'}),
      ];
    }), [null, null, ['dots', ''], ['', 'finance It costs less.'], ['', 'Please repeat that']]);
    const box = (await page.locator('#call-window').boundingBox())!;
    assert.ok(box.width <= 440, 'small: ' + box.width);
    assert.equal(await page.locator('#side').isVisible(), false, 'no sidebar in the window');
    assert.ok(await sideways(page) <= 420);
    // The chat: the thread without the call's own posts; send shows once there is text.
    await page.locator('.cw-thread .th-msg').first().waitFor();
    assert.equal(await page.locator('.cw-thread .th-msg:visible').count(), 2, 'no action items or transcript posts in the chat');
    assert.equal(await page.locator('.cw .p-text').getAttribute('placeholder'), 'Message');
    assert.equal(await page.locator('.cw .p-send').isVisible(), false);
    await page.locator('.cw .p-text').fill('On it');
    assert.equal(await page.locator('.cw .p-send').isVisible(), true);
    await page.locator('.cw .p-text').fill('');
    await themed(page, 'small');
    // The + in front of the field: upload, link a file, link a task, new task.
    await page.locator('.cw .p-attach').click();
    assert.deepEqual(await page.locator('.cw .thv-attach-menu button').evaluateAll(els => els.map(e => [e.querySelector('.nav-icon')?.textContent, e.lastChild?.textContent])),
      [['upload', 'Upload file'], ['attach_file', 'Link a file'], ['link', 'Link a task'], ['add_task', 'New task']]);
    await themed(page, 'plusmenu');
    await page.locator('.cw .p-attach').click();
    // People: who is in the call (you first, as yourself); typing shows only what it finds.
    await page.locator('.cw-add').click();
    await page.locator('.cw-people-list .cw-row').first().waitFor();
    assert.match(await page.locator('.cw-people-list .cw-row-name').first().innerText(), /^Chris\s+You$/);
    assert.equal(await page.locator('.cw-people-list .cw-h').innerText(), 'In call · 5');
    await page.locator('[data-cw-q]').fill('gro');
    await page.locator('.cw-team [data-cw-team="growth"]').waitFor();
    assert.equal(await page.locator('.cw-people-list .cw-h').count(), 0, 'results instead of the call');
    assert.equal(await page.locator('.cw-people-list .cw-row', {hasText: 'Growth Manager'}).locator('.cw-in').count(), 1);
    const plus = (await page.locator('.cw-people-list .cw-plus').first().boundingBox())!;
    assert.deepEqual([plus.width, plus.height], [24, 24]);
    await themed(page, 'people');
    // A person is rung into the call: never a thread member write from here (the server keeps an owner an owner).
    await page.locator('[data-cw-q]').fill('dana');
    await page.locator('[data-cw-person="human:dana"]').click();
    await page.locator('.toast', {hasText: 'Dana added'}).waitFor();
    assert.deepEqual(seen.rings, ['human:dana']);
    assert.equal(seen.memberWrites, 0);
    await page.locator('[data-cw-q]').fill('');
    await page.locator('.cw-tab[data-cw-tab="chat"]').click();

    // ---- a bot tapped out leaves at once, with Undo; Undo brings it back
    const analytics = page.locator('.cw-faces [data-cw-bot="analytics"]');
    await analytics.click();
    const toast = page.locator('.toast', {hasText: 'Analytics left'});
    await toast.waitFor();
    await page.waitForFunction(() => document.querySelector('.cw-faces [data-cw-bot="analytics"]')?.classList.contains('cw-out'));
    assert.deepEqual(seen.botWrites, ['remove:analytics']);
    assert.deepEqual(await page.locator('.cw-tab').allInnerTexts(), ['Chat', 'Transcript', 'People 4'], 'a bot tapped out is not counted');
    assert.equal(await page.locator('.toast').count(), 1, 'one toast at a time');
    const pill = (await toast.boundingBox())!;
    assert.equal(Math.round(pill.height), 34);
    assert.ok(Math.abs(pill.x + pill.width / 2 - 210) <= 1, 'centred: ' + pill.x);
    await themed(page, 'undo');
    await toast.locator('button', {hasText: 'Undo'}).click();
    await page.waitForFunction(() => document.querySelector('.cw-faces [data-cw-bot="analytics"][data-on="true"]'));
    assert.deepEqual(seen.botWrites, ['remove:analytics', 'add:analytics']);
    // A faint thread member is one tap into the call.
    await page.locator('.cw-faces [data-cw-bot="content-social"]').click();
    await page.waitForFunction(() => document.querySelector('.cw-faces [data-cw-bot="content-social"][data-on="true"]'));
    assert.deepEqual(seen.botWrites.slice(-1), ['add:content-social']);

    // A note from the voice shows under the caption for a while.
    (call.voice as {note: string | null}).note = 'Please repeat that';
    await page.evaluate(() => callViewRead('c1'));
    await page.locator('.cw-caption-head .cw-note', {hasText: 'Please repeat that'}).waitFor();
    (call.voice as {note: string | null}).note = null;
    await page.evaluate(() => callViewRead('c1'));
    await page.waitForFunction(() => !document.querySelector('.cw-caption-head .cw-note'));

    // ---- someone shares: the wide layout, the screen beside the tabs; it stops: small again
    await page.setViewportSize({width: 1280, height: 800});
    await page.evaluate(() => {
      const video = document.createElement('video');
      const track = {kind: 'video', source: 'screen', attach: () => video, detach: () => [video]};
      (window as unknown as {__track: unknown}).__track = track;
      window.__rooms[0]!.emit('ts', track, {}, {identity: 'human:thomaz'});
    });
    await page.waitForFunction(() => document.querySelector('#call-window.cw-wide'));
    assert.match(await page.locator('.cw-meta').innerText(), /Thomaz sharing$/);
    assert.ok(await page.locator('#cw-screen video').isVisible());
    const wide = (await page.locator('#call-window').boundingBox())!;
    assert.ok(wide.width > 1000, 'wide: ' + wide.width);
    assert.ok(await page.locator('.cw-caption-stage').isVisible() && !(await page.locator('.cw-caption-head').isVisible()), 'the caption under the screen');
    assert.equal(await page.locator('.cw-full').isVisible(), false, 'full screen is a phone\'s');
    await themed(page, 'expanded');
    await page.evaluate(() => window.__rooms[0]!.emit('tu', (window as unknown as {__track: unknown}).__track));
    await page.waitForFunction(() => !document.querySelector('#call-window.cw-wide'));
    assert.ok((await page.locator('#call-window').boundingBox())!.width <= 440, 'small again');

    // ---- the call ends while the window is open: the room, its timer and the stream stop, and the notes show here
    call.state = 'ended'; call.ended_at = at(0); call.duration_s = 1140;
    await page.evaluate(() => callViewRead('c1'));
    await page.locator('#call-window.cw-ended .cw-item').nth(2).waitFor();
    assert.equal(await page.evaluate(() => window.__disconnects), 1, 'the room is left');
    assert.deepEqual(await page.evaluate(() => window.__streams.filter(s => s.url.includes('/live-meetings/')).map(s => [s.url.replace(location.origin, ''), s.readyState])), [['/api/v2/live-meetings/lm1/events?after=7', 2]], 'the stream is closed');
    assert.equal(await page.locator('.cw-summary li').count(), 3);
    assert.equal(await page.locator('.cw-thread .th-msg').count(), 0, 'the chat is gone');
    assert.equal(await page.locator('.cw-bar button').count(), 0);
    assert.deepEqual(seen.errors, []);
    await context.close();
  }

  // ---- a plain visit (a copied link) shows Join; a big roster keeps the controls on screen
  {
    const {context, page, seen} = await open(browser, {big: true});
    await page.goto('https://tico-ui.test/#/call/c1');
    await page.locator('.cw-bar .cw-join').waitFor();
    await page.locator('.cw-thread .th-msg').first().waitFor();
    assert.equal(seen.joins, 0, 'no join without being opened to');
    assert.match(await page.locator('.cw-faces .cw-more').innerText(), /^\+17\b/, 'two rows of faces, then +N');
    assert.ok(await inView(page, '.cw-bar .cw-join', 680), 'Join stays on screen');
    await page.locator('.cw-bar .cw-join').click();
    await page.locator('.cw-bar .cw-leave').waitFor();
    assert.equal(seen.joins, 1);
    assert.ok(await inView(page, '.cw-bar .cw-leave', 680), 'Leave stays on screen');
    await page.locator('.cw-faces .cw-more').click();
    await page.locator('.cw-people-list .cw-row').nth(20).waitFor();
    assert.deepEqual(seen.errors, []);
    await context.close();
  }

  // ---- a phone at 390px: the whole screen, finger-sized controls
  {
    const {context, page, seen} = await open(browser, {phone: true});
    await page.goto('https://tico-ui.test/#/call/c1?join=1');
    await page.locator('.cw-bar .cw-leave').waitFor();
    const box = (await page.locator('#call-window').boundingBox())!;
    assert.equal(Math.round(box.width), 390);
    assert.ok(await sideways(page) <= 390);
    for (const b of await page.locator('.cw-bar button').all()) assert.ok((await b.boundingBox())!.height >= 44, 'a finger-sized control');
    assert.equal(await page.locator('#mobile-nav').isVisible(), false);
    assert.equal(await page.locator('.cw-tools [data-cw="down"]').count(), 1, 'back to the thread');
    const face = (await page.locator('.cw-faces .cw-face .cw-av').first().boundingBox())!;
    assert.equal(Math.round(face.width), 60);
    await page.locator('.cw-thread .th-msg').first().waitFor();
    await themed(page, 'small', true);
    await page.evaluate(() => {
      const video = document.createElement('video');
      window.__rooms[0]!.emit('ts', {kind: 'video', source: 'screen', attach: () => video, detach: () => [video]}, {}, {identity: 'human:thomaz'});
    });
    await page.waitForFunction(() => document.querySelector('#call-window.cw-wide'));
    assert.ok(await sideways(page) <= 390);
    const full = (await page.locator('.cw-full').boundingBox())!;
    assert.deepEqual([full.width, full.height], [44, 44], 'a full screen button for the shared screen');
    await themed(page, 'expanded', true);
    assert.deepEqual(seen.errors, []);
    await context.close();
  }

  // ---- after the call: its notes
  for (const phone of [false, true]) {
    const {context, page, seen} = await open(browser, {phone});
    if (!phone) await page.setViewportSize({width: 1280, height: 800});
    await page.goto('https://tico-ui.test/#/call/c2');
    await page.locator('#call-window.cw-ended .cw-item').nth(2).waitFor();
    assert.equal(seen.joins, 0, 'an ended call is not joined');
    assert.match(await page.locator('.cw-meta').innerText(), /^Today, .+ · 24 min$/);
    assert.equal(await page.locator('.cw-bar button, .cw-tabs .cw-tab').count(), 0);
    assert.deepEqual(await page.locator('.cw-summary li').allInnerTexts(), seen.notes.summary);
    await page.locator('.cw-after-turns .cw-turn').nth(1).waitFor();
    assert.match(await page.locator('.cw-after-turns').innerText(), /Thomaz\s*(?:21:30)?\s+Let's do another one on Thursday\./);
    assert.doesNotMatch(await page.locator('.cw-after-turns').innerText(), /\bYou\b/, 'real names');
    assert.equal(await page.locator('.cw-item .cw-owner').count(), 2);
    assert.match(await page.locator('.cw-after-turns .cw-turn').first().innerText(), /^Chris\s*0:42/);
    const create = (await page.locator('.cw-create').first().boundingBox())!;
    assert.equal(Math.round(create.height), 26, 'a small Create');
    if (!phone) {
      assert.deepEqual(await faces(page), ['Chris', 'Thomaz', 'Bea', 'Content & Social'], 'the bots that were in it too');
      assert.match(await page.locator('.cw-item').first().locator('.cw-owner').innerText(), /Content & Social/);
    } else assert.equal(await page.locator('.cw-item .cw-owner-n').first().isVisible(), false, 'a phone shows the owner\'s face only');
    await themed(page, 'after', phone);
    if (!phone) {
      // The task goes to who took the item on.
      await page.locator('.cw-item').first().locator('[data-cw-item="create"]').click();
      await page.locator('.cw-item').first().locator('.cw-item-made').waitFor();
      assert.deepEqual(seen.tasks, [{title: 'Draft Thursday\'s Reddit post', owner: 'bot:content-social', live_meeting_id: 'lm2'}]);
      await page.locator('.cw-item').nth(1).locator('[data-cw-item="hide"]').click();
      await page.waitForFunction(() => document.querySelectorAll('.cw-item').length === 2);
      // Search the transcript.
      await page.locator('[data-cw="tsearch"]').click();
      await page.locator('[data-cw-tq]').fill('thursday');
      await page.waitForFunction(() => document.querySelectorAll('.cw-after-turns .cw-turn').length === 1);
      assert.equal(await page.locator('.cw-after-turns mark').innerText(), 'Thursday');
    }
    assert.ok(await sideways(page) <= (phone ? 390 : 1280));
    assert.deepEqual(seen.errors, []);
    await context.close();
  }
  // ---- a viewer who cannot read the meeting: the transcript is the one the call posted
  {
    const {context, page, seen} = await open(browser, {lm2: false});
    await page.goto('https://tico-ui.test/#/call/c2');
    await page.locator('.cw-after-turns .cw-turn').nth(1).waitFor();
    assert.match(await page.locator('.cw-after-turns').innerText(), /Thomaz\s+Let's do another one on Thursday\./);
    assert.deepEqual(seen.errors, []);
    await context.close();
  }
  console.log('call window: ok');
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(() => browser?.close());
