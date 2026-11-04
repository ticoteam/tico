// Threads per role, at desktop and phone width (docs/dev/threads-contract.md, plan §9 "UI per role"): the sidebar's
// Threads link and its Catch up count, the thread rail (Pinned, Threads, Find, Browse) beside one thread, the pin,
// Catch up, New thread, a thread's flat timeline (an older server's sub-threads drawn flat, each reply quoting what it
// answers), a message shown as "Checking…" until the server answers, a held message, the bot page's Home and DM, a person's Home,
// New DM, a server without the rail, and a teammate who may see a bot but not read it getting no Home.
// TICO_SCREENSHOT_DIR=<dir> also saves the screenshots.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');

const shots = process.env.TICO_SCREENSHOT_DIR;
const now = Date.now(), at = min => new Date(now - min * 60000).toISOString();
const me = (unread = 0, extra = {}) => ({member: true, following: true, notify: 'live', last_read_seq: 0, unread, activity: false, can_post: true, can_manage: false, ...extra});
const thread = (id, title, seq, extra = {}) => ({id, title, audience: 'team', audience_group_id: 'ops', last_seq: seq, last_unread_seq: 0,
  last_message_at: at(100 - seq), me: me(), members: [], ...extra});

// Thirteen threads on the rail: two unread, one with activity under it, the rest read. Launch plan is pinned.
const listed = Array.from({length: 13}, (_, i) => thread('t' + i, `Thread ${i}`, i + 1));
listed[11].me = me(3); listed[4].me = me(1);
listed[9].me = {...me(), activity: true};
const plan = thread('t-plan', 'Launch plan', 40, {me: me(0, {can_manage: true, pinned: true}), pinned_for_everyone: false});
// The plain list (GET /v2/threads), for a server without the rail: Launch plan waits on Ana there.
const attention = [plan];
const dm = {id: 't-dm', title: 'Chat with Bea', audience: 'personal', last_seq: 5, last_unread_seq: 5, last_message_at: at(3), me: me(1),
  members: [{actor: 'human:ana'}, {actor: 'human:bea'}], dm_with: [{actor: 'human:bea', name: 'Bea'}], dm_with_count: 1};
// A DM with one bot: not on the rail; its unread is counted on the bot in Team (`bot_dms`, or the rail's `dms`).
const botDm = {id: 't-botdm', title: 'Chat with Ops', audience: 'personal', last_seq: 4, last_unread_seq: 4, last_message_at: at(2), me: me(2),
  dm_with: [{actor: 'bot:ops', name: 'Ops'}], dm_with_count: 1};
// A group chat: on the rail with the threads, named by its people.
const groupDm = {id: 't-gdm', title: '', audience: 'personal', last_seq: 3, last_unread_seq: 3, me: me(1),
  dm_with: [{actor: 'human:bea', name: 'Bea'}, {actor: 'human:cy', name: 'Cy'}], dm_with_count: 2};
// A company thread Ana reads but is not in: Browse threads offers it.
const openHouse = {...thread('t-open', 'Open house', 3, {audience: 'company', audience_group_id: null}), member_count: 4,
  me: {...me(), member: false, following: false, pinned: false}};

const msg = (id, seq, from, body, extra = {}) => ({id, seq, thread_id: 't-plan', sender_id: from, body, created_at: at(60 - seq), depth: 0, ...extra});
// Catch up's cards (GET /v2/threads/catch-up), in the server's order.
const catchUp = () => [
  {thread: listed[11], new_count: 3, mention: true, more: 1, messages: [msg('c1', 10, 'human:bea', 'Can someone check the venue?', {thread_id: 't11'}),
    msg('c2', 11, 'bot:ops', 'Booked for Friday.', {thread_id: 't11', depth: 1, clipped: true})]},
  {thread: listed[4], new_count: 1, mention: false, more: 0, messages: [msg('c3', 3, 'human:cy', 'The draft is up.', {thread_id: 't4'})]},
  {thread: groupDm, new_count: 1, mention: false, more: 0, messages: [msg('c4', 2, 'human:bea', 'Lunch at noon?', {thread_id: 't-gdm'})]},
];
const sub = {id: 't-sub', title: 'Pricing', audience: 'team', audience_group_id: 'ops', parent_id: 't-plan', parent_message_id: 'm2',
  last_seq: 4, me: me(), members: []};
const timeline = {
  't-plan': () => ({thread: plan, breadcrumb: [{id: 't-plan', title: 'Launch plan'}], next_cursor: null, messages: [
    msg('m1', 1, 'human:bea', 'Kickoff is Monday.'),
    msg('m2', 2, 'human:ana', 'What price do we open with?', {sub_thread: {id: 't-sub', title: 'Pricing', count: 3, last_seq: 4, unread: 0, more: 0}}),
    msg('s1', 1, 'bot:ops', 'Last quarter we opened at 40.', {thread_id: 't-sub', depth: 1, sub_thread: {id: 't-sub2', count: 1, last_seq: 1, unread: 0}}),
    msg('s1a', 1, 'human:bea', 'And churn was low.', {thread_id: 't-sub2', depth: 2}),
    msg('s2', 2, 'human:bea', 'Then 45 this time.', {thread_id: 't-sub', depth: 1}),
    {id: 'h1', seq: 3, thread_id: 't-plan', hidden: true},
    msg('m4', 4, 'bot:ops', 'Daily update, Oct 31', {kind: 'notice', refs: {card: 'update', update: {id: 'u1', kind: 'daily', day: '2026-10-31'}}}),
    msg('m5', 5, 'bot:ops', 'Checked the notes.', {refs: {turn_id: 'turn1'}, run: {steps: 3, tool_calls: 1, took_s: 4}}),
  ]}),
  't-sub': () => ({thread: sub, breadcrumb: [{id: 't-plan', title: 'Launch plan'}, {id: 't-sub', title: 'Pricing'}], next_cursor: null, messages: [
    msg('s1', 1, 'bot:ops', 'Last quarter we opened at 40.', {thread_id: 't-sub', depth: 1, sub_thread: {id: 't-sub2', count: 1, last_seq: 1, unread: 0}}),
    msg('s1a', 1, 'human:bea', 'And churn was low.', {thread_id: 't-sub2', depth: 2}),
    msg('s2', 2, 'human:bea', 'Then 45 this time.', {thread_id: 't-sub', depth: 1}),
  ]}),
  // A tree with seqs as the server gives them: t-rb is read, t-lb is long ("2 more", unread), t-nb has an unread reply.
  't-tree': () => ({thread: thread('t-tree', 'Vendors', 60, {me: me(0, {last_read_seq: 58})}), breadcrumb: [{id: 't-tree', title: 'Vendors'}], next_cursor: null, messages: [
    msg('p1', 10, 'human:bea', 'Which vendor?', {thread_id: 't-tree', sub_thread: {id: 't-rb', title: '', count: 1, last_seq: 11, last_read_seq: 11, unread: 0, more: 0}}),
    msg('r1', 11, 'human:bea', 'Old reply.', {thread_id: 't-rb', depth: 1}),
    msg('p2', 12, 'human:bea', 'A long one.', {thread_id: 't-tree', sub_thread: {id: 't-lb', title: '', count: 4, last_seq: 22, last_read_seq: 0, unread: 4, more: 2}}),
    {id: 'more:t-lb', thread_id: 't-lb', depth: 1, collapsed: true, count: 2, cursor: 21, unread: true},
    msg('b3', 21, 'human:bea', 'Long three.', {thread_id: 't-lb', depth: 1}),
    msg('b4', 22, 'human:bea', 'Long four.', {thread_id: 't-lb', depth: 1}),
    msg('p3', 23, 'human:bea', 'Has news.', {thread_id: 't-tree', sub_thread: {id: 't-nb', title: '', count: 1, last_seq: 24, last_read_seq: 0, unread: 1, more: 0}}),
    msg('n1', 24, 'human:bea', 'Unread inline.', {thread_id: 't-nb', depth: 1}),
    ...Array.from({length: 30}, (_, i) => msg('f' + i, 30 + i, 'human:bea', `Later ${i}.`, {thread_id: 't-tree'})),
  ]}),
  't-lb': () => ({thread: thread('t-lb', 'A long one.', 22), breadcrumb: [], next_cursor: null, messages: [
    msg('b1', 13, 'human:bea', 'Long one.', {thread_id: 't-lb', depth: 1}),
    msg('b2', 14, 'human:bea', 'Long two.', {thread_id: 't-lb', depth: 1}),
  ]}),
  'h-ops': () => ({thread: {id: 'h-ops', audience: 'home', home: {actor: 'bot:ops'}, last_seq: 2, me: me()}, breadcrumb: [], next_cursor: null, messages: [
    {...msg('o1', 1, 'bot:ops', 'Weekly numbers are in the doc.'), thread_id: 'h-ops'},
    {...msg('o2', 2, 'bot:ops', 'Daily update, Oct 31', {kind: 'notice', refs: {card: 'update', update: {id: 'u1', kind: 'daily', day: '2026-10-31'}}}), thread_id: 'h-ops'},
  ]}),
  'h-bea': () => ({thread: {id: 'h-bea', audience: 'home', home: {actor: 'human:bea'}, last_seq: 2, me: me(0, {can_post: false})}, breadcrumb: [], next_cursor: null, messages: [
    {...msg('b1', 1, 'human:bea', 'Daily update, Oct 31', {kind: 'notice', refs: {card: 'update', update: {id: 'u2', kind: 'daily', day: '2026-10-31'}}}), thread_id: 'h-bea'},
    {...msg('b2', 2, 'human:bea', 'Out Friday afternoon.'), thread_id: 'h-bea'},
  ]}),
  // Empty Homes: one the viewer may post to, one they may not.
  'h-cy': () => ({thread: {id: 'h-cy', audience: 'home', home: {actor: 'human:cy'}, last_seq: 0, me: me()}, breadcrumb: [], next_cursor: null, messages: []}),
  'h-di': () => ({thread: {id: 'h-di', audience: 'home', home: {actor: 'human:di'}, last_seq: 0, me: me(0, {can_post: false})}, breadcrumb: [], next_cursor: null, messages: []}),
  // A migrated Home: its closed history is one row, with the control for the bot's manager.
  'h-old': () => ({thread: {id: 'h-old', title: 'Old room', audience: 'home', home: {actor: 'bot:ops'}, last_seq: 3, earlier: {count: 2}, me: me()}, breadcrumb: [], next_cursor: null, messages: [
    {id: 'e1', seq: 1, thread_id: 'h-old', depth: 0, hidden: true, earlier: true},
    {...msg('e3', 3, 'bot:ops', 'New plan.'), thread_id: 'h-old'},
  ]}),
  't-notice': () => ({thread: {id: 't-notice', title: '', audience: 'personal', last_seq: 1, me: me(), members: [{actor: 'human:ana'}, {actor: 'bot:ops'}]}, breadcrumb: [], next_cursor: null, messages: [
    {...msg('nt1', 1, 'keeper', "Ops's earlier messages are closed to its Home's readers.", {kind: 'notice', refs: {earlier_messages: {thread_id: 'h-old'}}}), thread_id: 't-notice'},
  ]}),
  't-edit': () => ({thread: thread('t-edit', 'Edits', 1), breadcrumb: [{id: 't-edit', title: 'Edits'}], next_cursor: null, messages: [
    {...msg('ed1', 1, 'human:ana', 'Open at 40.'), thread_id: 't-edit'},
  ]}),
};
const homes = {'bot:ops': 'h-ops', 'human:bea': 'h-bea', 'human:cy': 'h-cy', 'human:di': 'h-di', 'group:ops': ''};

// What a block below changes for the pages it opens: DM rows as the server sends them, a server with calls off, and
// one without the rail (GET /v2/threads/rail answers 404).
let dmRows = null, callsOff = false, railOff = false, threadsOff = false, threadsGate = null;
// Start call as the server answers it: the call it makes (after a moment), or none.
let callMade = null, joinRefused = false, startWait = 150;
let startGate = null, leaveGate = null, callLive = null;
async function open(browser, who, {phone = false, sse = null} = {}) {
  const page = await browser.newPage({viewport: phone ? {width: 390, height: 844} : {width: 1300, height: 860}, serviceWorkers: 'block', reducedMotion: 'reduce',
    ...(phone ? {isMobile: true, hasTouch: true} : {})});
  const seen = {errors: [], posts: [], reads: [], readSeqs: [], homes: [], steps: [], tasks: [], calls: [], catchUps: [], follows: [], earlier: [], withdrawn: [], patches: [],
    readAll: [], pins: [], created: [], browsed: [], rails: 0, threadGets: []};
  page.on('pageerror', e => seen.errors.push(e.message));
  page.on('dialog', d => d.accept());
  // A stand-in media client: rooms connect after a moment and remember whether their microphone is on.
  await page.addInitScript(() => {
    window.__rooms = [];
    window.LivekitClient = {
      RoomEvent: {TrackSubscribed: 'ts', TrackUnsubscribed: 'tu', ParticipantConnected: 'pc', ParticipantDisconnected: 'pd', LocalTrackUnpublished: 'lu', Disconnected: 'd'},
      Track: {Source: {ScreenShare: 'screen'}}, DisconnectReason: {PARTICIPANT_REMOVED: 'removed'},
      Room: class {
        constructor() { this.mic = false; this.gone = false; this.remoteParticipants = new Map(); const room = this;
          // The microphone's track, as a publication: stopped is what a hidden page must do to it.
          this.mic_track = {stopped: false, stop() { this.stopped = true; }};
          this.localParticipant = {trackPublications: new Map(), setMicrophoneEnabled: async on => {
            room.mic = on; if (on) room.localParticipant.trackPublications.set('mic', {track: room.mic_track}); }, setScreenShareEnabled: async () => {}};
          window.__rooms.push(this); }
        on() { return this; }
        async connect(url, token) { this.token = token; await new Promise(r => setTimeout(r, 200)); }
        disconnect() { this.gone = true; }
      },
    };
  });
  const bob = who === 'bob';
  // The rail as the server keeps it for this page: Ana's pins, and threads she joined from Browse.
  const pins = new Set(['t-plan']), joined = [];
  const railRows = () => [plan, ...listed, groupDm, ...(dmRows ? [dmRows[1]] : []), ...joined];
  const known = id => [...railRows(), openHouse].find(t => t.id === id);
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/api/me') return json(bob ? {id: 'bob', name: 'Bob', role: 'member', registered: true} : {id: 'ana', name: 'Ana', role: 'owner', registered: true});
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', state: 'active', can_chat: true, schedules: [],
      my_access: bob ? {see: true, read: false, write: false} : {see: true, read: true, write: true}}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}, {id: 'bea', name: 'Bea', email: 'bea@acme.example'},
      {id: 'bob', name: 'Bob', email: 'bob@acme.example'},
      {id: 'cy', name: 'Cy', email: 'cy@acme.example', reports_to: 'ana', my_access: {see: true, read: true, write: true, manage: true}},
      {id: 'di', name: 'Di', email: 'di@acme.example', my_access: {see: true, read: true, write: true, manage: false}}],
      org_groups: [{id: 'ops', name: 'Ops team', my_access: {see: true, read: true, write: true}}, {id: 'legal', name: 'Legal', my_access: {see: true, read: true, write: false}}]});
    if (p === '/api/issues') return json([]);
    if (p === '/api/v2/events') {
      if (!sse) return route.abort();
      // A stand-in stream (`sse`): the newest connection with the threads topic waits for the events the test sends,
      // then ends, and the page reconnects for the next ones.
      const req = route.request();
      if (!(new URL(req.url()).searchParams.get('topics') || '').split(',').includes('threads')) return route.abort();
      sse.latest = req;
      while (!sse.queue.length || sse.latest !== req) {
        if (sse.latest !== req || sse.done) return route.abort().catch(() => {});
        await new Promise(r => setTimeout(r, 50));
      }
      const body = 'event: ready\ndata: {"seq":1}\n\n' + sse.queue.splice(0).map(d => `event: threads\nid: ${++sse.seq}\ndata: ${JSON.stringify(d)}\n\n`).join('');
      return route.fulfill({status: 200, contentType: 'text/event-stream', body}).catch(() => {});
    }
    if ((p === '/api/v2/threads' || p === '/api/v2/threads/rail') && method === 'GET') {
      if (threadsOff === 'odd') return json(p.endsWith('/rail') ? {error: 'not_found'} : {nothing: true}, p.endsWith('/rail') ? 404 : 200);
      if (threadsOff) return json({error: 'not_found'}, 404);
      if (threadsGate) await new Promise(go => threadsGate.push(go));
    }
    if (p === '/api/v2/threads' && method === 'GET') return json({attention: bob ? [] : attention, threads: bob ? [] : listed, dms: bob ? [] : dmRows || [dm, botDm],
      bot_dms: bob ? {} : {'bot:ops': 2}, next_cursor: null});
    if (p === '/api/v2/threads/rail') {
      seen.rails++;
      if (railOff) return json({error: 'not_found'}, 404);
      const pin = t => ({...t, me: {...t.me, pinned: pins.has(t.id)}});
      return json(bob ? {pinned: [], threads: [], catch_up: 0, next_cursor: null, dms: {}} : {pinned: railRows().filter(t => pins.has(t.id)).map(pin),
        threads: railRows().filter(t => !pins.has(t.id)).map(pin), catch_up: 3, catch_up_ids: ['t11', 't4', 't-gdm'], next_cursor: null,
        dms: {'bot:ops': 2, 'human:bea': 1}});
    }
    if (p === '/api/v2/threads/catch-up') return json(bob ? {items: [], count: 0, next_cursor: null} : {items: catchUp(), count: 3, next_cursor: null});
    if (p === '/api/v2/threads/browse') { seen.browsed.push(url.searchParams.get('q')); return json({threads: [openHouse], next_cursor: null}); }
    if (p === '/api/v2/threads' && method === 'POST') {
      const body = JSON.parse(req.postData() || '{}'); seen.created.push(body);
      if (!body.title) return json({error: {code: 'name_required', message: 'Group chats need a name.'}}, 422);
      const made = thread('t-new', body.title, 0, {audience: body.audience, me: me(0, {pinned: !!body.pin_for_members})});
      timeline['t-new'] = () => ({thread: made, breadcrumb: [{id: 't-new', title: body.title}], next_cursor: null, messages: []});
      return json(made);
    }
    const readAll = p.match(/^\/api\/v2\/threads\/([^/]+)\/read-all$/);
    if (readAll) { seen.readAll.push(readAll[1]); return json({ok: true}); }
    const pinned = p.match(/^\/api\/v2\/threads\/([^/]+)\/pin$/);
    if (pinned) {
      seen.pins.push(method + ':' + pinned[1]);
      if (method === 'PUT') pins.add(pinned[1]); else pins.delete(pinned[1]);
      return json({...known(pinned[1]), me: {...known(pinned[1]).me, pinned: pins.has(pinned[1])}});
    }
    if (/^\/api\/v2\/threads\/[^/]+\/members$/.test(p))
      return json({members: [{actor: 'human:ana'}, {actor: 'human:bea'}, {actor: 'bot:ops'}], next_cursor: null});
    const home = p.match(/^\/api\/v2\/threads\/home\/(.+)$/);
    if (home) {
      const actor = decodeURIComponent(home[1]); seen.homes.push(actor);
      // Bob may see the bot but not read it: its Home is not his to open.
      if (bob && actor === 'bot:ops') return json({error: 'not_found'}, 404);
      return homes[actor] ? json(timeline[homes[actor]]().thread) : json({error: 'not_found'}, 404);
    }
    if (p === '/api/v2/threads/dm') return json({error: 'not_found'}, 404);
    const tl = p.match(/^\/api\/v2\/threads\/([^/]+)\/timeline$/);
    if (tl && timeline[tl[1]]) return json(timeline[tl[1]]());
    if (tl && known(tl[1])) return json({thread: known(tl[1]), breadcrumb: [{id: tl[1], title: known(tl[1]).title}], next_cursor: null,
      messages: [{...msg('x1', 1, 'human:bea', 'Morning.'), thread_id: tl[1]}]});
    const one = p.match(/^\/api\/v2\/threads\/([^/]+)\/read$/);
    if (one) { seen.reads.push(one[1]); seen.readSeqs.push(one[1] + ':' + JSON.parse(req.postData() || '{}').seq); return json({ok: true}); }
    if (callMade && method === 'POST' && /^\/api\/v2\/threads\/[^/]+\/calls$/.test(p)) {
      seen.calls.push('start:' + callMade.id);
      if (startGate) await new Promise(go => startGate.push(go));
      else await new Promise(r => setTimeout(r, startWait));
      return json({call: callMade});
    }
    if (/^\/api\/v2\/threads\/[^/]+\/calls$/.test(p)) return callsOff && method === 'POST' ? json({error: {code: 'meet_unavailable'}}, 503) : json({calls: callLive ? [callLive] : [], next_before: null, available: !callsOff});
    const settings = p.match(/^\/api\/v2\/threads\/([^/]+)$/);
    if (settings && method === 'PATCH') {
      const body = JSON.parse(req.postData() || '{}');
      seen.patches.push(body);
      return json({...timeline[settings[1]]().thread, ...body});
    }
    const send = p.match(/^\/api\/v2\/threads\/([^/]+)\/messages$/);
    if (send && method === 'POST') {
      const body = JSON.parse(req.postData() || '{}'); seen.posts.push(body);
      if (/secret/i.test(body.text)) return json({held: {id: 'hold1', outcome: 'blocked', rules: [{id: 'r1', text: 'No secrets in threads'}]}});
      await new Promise(r => setTimeout(r, 600));     // long enough to see "Checking…"
      return json({message: msg('n' + seen.posts.length, 9 + seen.posts.length, 'human:ana', body.text, {thread_id: send[1], parent_message_id: body.parent_message_id || null})});
    }
    // A message held for Ana's review in a thread: Inbox lists it with its review card.
    if (p === '/api/v2/needs-you' && !bob) return json({items: [{kind: 'thread_held', id: 'hold3', conversation_id: null, thread_id: 't-plan',
      title: 'A message is held for your review', requester_id: 'Bea', created_at: at(5)}], count: 1, version: 'v1'});
    if (p === '/api/v2/rule-holds/hold3') return json({id: 'hold3', operation: 'threads', can_send: true, reason: 'Names a customer'});
    if (p === '/api/v2/rule-holds/hold1') return json({id: 'hold1', can_send: false});
    const asked = p.match(/^\/api\/v2\/threads\/([^/]+)\/(catch-up|follow|open-earlier)$/);
    if (asked) {
      if (asked[2] === 'catch-up') { seen.catchUps.push({thread: asked[1], ...JSON.parse(req.postData() || '{}')}); return json({thread_id: asked[1], messages: 2, posted: {id: 'cu1'}}); }
      if (asked[2] === 'follow') {
        const body = JSON.parse(req.postData() || '{}'); seen.follows.push(body.notify);
        if (asked[1] === 't-open') joined.push({...openHouse, me: {...openHouse.me, following: true}});
        return json({notify: body.notify});
      }
      seen.earlier.push(asked[1]); return json({home: 'bot:ops', messages: 2});
    }
    const react = p.match(/^\/api\/v2\/messages\/([^/]+)\/reactions\/(.+)$/);
    // The server answers a reaction with the message's reactions, a bare list.
    if (react) return json(method === 'PUT' ? [{emoji: decodeURIComponent(react[2]), count: 1, actors: ['human:ana']}] : []);
    if (p === '/api/v2/threads/search') return json({hits: url.searchParams.get('q') === 'price' ? [{message_id: 'm2', thread_id: 't-plan', title: 'Launch plan',
      sender_id: 'human:ana', created_at: at(58), excerpt: 'What price do we open with?'}] : [], next_cursor: null});
    const call = p.match(/^\/api\/v2\/calls\/([^/]+)\/(join|leave|end|token)$/);
    if (call) {
      seen.calls.push(call[2] + ':' + call[1]);
      if (call[2] === 'leave' && leaveGate) await new Promise(go => leaveGate.push(go));
      if (call[2] === 'join' && joinRefused) return json({error: {code: 'call_access_removed'}}, 403);
      if (call[2] === 'join') { await new Promise(r => setTimeout(r, 150)); return json({token: 'tok-' + call[1], url: 'wss://meet.acme.example', call: {id: call[1], connector: 'human:ana'}}); }
      if (call[2] === 'end') return json({error: {code: 'internal', detail: 'Could not end'}}, 500);
      return json({ok: true});
    }
    const steps = p.match(/^\/api\/v2\/turns\/([^/]+)\/steps$/);
    if (steps) { seen.steps.push(steps[1]); return json({steps: [{kind: 'tool', tool: 'search', text: 'pricing notes', at: at(57)}]}); }
    // One thread's view (Join reads it after following).
    if (settings && method === 'GET' && known(settings[1])) { seen.threadGets.push(settings[1]); return json(known(settings[1])); }
    return json(unrouted(p));
  });
  return {page, seen};
}
async function shot(page, name) {
  if (!shots) return;
  fs.mkdirSync(shots, {recursive: true});
  await page.screenshot({path: path.join(shots, `threads-${name}.png`)});
}
// A screen in both themes, desktop at 1280x800 or the phone's 390x844: threads-<name>-<desktop|phone>-<dark|light>.png.
async function themed(page, name, phone = false) {
  if (!shots) return;
  const was = page.viewportSize();
  if (!phone) await page.setViewportSize({width: 1280, height: 800});
  for (const theme of ['dark', 'light']) {
    await page.evaluate(t => { document.documentElement.dataset.theme = t; }, theme);
    // A dialog that just opened may still be animating in.
    await page.evaluate(() => Promise.race([new Promise(r => setTimeout(r, 1000)),
      Promise.all(document.getAnimations().filter(a => a.effect?.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {})))]));
    await page.waitForTimeout(80);
    await shot(page, `${name}-${phone ? 'phone' : 'desktop'}-${theme}`);
  }
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
  if (!phone) await page.setViewportSize(was);
}
const sideways = page => page.evaluate(() => document.documentElement.scrollWidth);

// Closed on failure too: a browser left open keeps this process alive until the runner's time limit stops it.
let browser = null;
(async () => {
  browser = await chromium.launch();

  // ---- Ana at desktop width
  {
    const {page, seen} = await open(browser, 'ana');
    await page.goto('https://tico-ui.test/#/tasks');
    // The sidebar's Threads link: its count is what Catch up has left, 99+ past 99.
    const link = page.locator('#nav-threads');
    await page.locator('#nav-threads:not([hidden]) .nav-badge:not([hidden])').waitFor();
    assert.equal(await link.getAttribute('href'), '#/threads');
    assert.equal(await link.locator('.nav-badge').textContent(), '3');
    await page.evaluate(() => { const was = THREADS.cu; THREADS.cu = new Set(Array.from({length: 140}, (_, i) => 'x' + i)); threadsSideRender(); THREADS.cu = was; });
    assert.equal(await link.locator('.nav-badge').textContent(), '99+');
    await page.evaluate(() => threadsSideRender());
    // Each 1:1 DM's unread is counted on its teammate in Team, a bot or a person.
    assert.equal(await page.locator('#tree [data-org="b:ops"] .tree-dm').textContent(), '2');
    assert.equal(await page.locator('#tree [data-org="p:bea"] .tree-dm').textContent(), '1');
    await shot(page, 'sidebar-desktop');

    // Threads: nothing opened before, so the first pinned thread opens beside the rail.
    await link.click();
    await page.waitForFunction(() => location.hash === '#/thread/t-plan');
    await page.getByText('And churn was low.').waitFor();
    assert.equal(await link.getAttribute('aria-current'), 'page', 'the link is the page on a thread');
    const rail = page.locator('#thread-rail');
    const titles = list => rail.locator(`[data-list="${list}"] .tr-title`).allTextContents();
    assert.deepEqual(await titles('pinned'), ['Launch plan']);
    const rest = await titles('threads');
    assert.ok(rest.includes('Thread 0') && rest.includes('Bea, Cy'), 'threads and group chats: ' + rest);
    assert.ok(!rest.includes('Bea') && !rest.includes('Ops'), 'a 1:1 DM is on its teammate, not here: ' + rest);
    assert.equal(await rail.locator('.tr-row.cur').getAttribute('data-thread'), 't-plan');
    // Bold for unread or activity; a count only for unread; never a bar.
    const rowState = id => page.evaluate(id => {
      const a = document.querySelector(`#thread-rail .tr-row[data-thread="${id}"]`);
      return {bold: getComputedStyle(a).fontWeight >= 600, n: a.querySelector('.tr-count')?.textContent || ''};
    }, id);
    assert.deepEqual(await rowState('t11'), {bold: true, n: '3'});
    assert.deepEqual(await rowState('t9'), {bold: true, n: ''}, 'activity is bold, with no count');
    assert.deepEqual(await rowState('t0'), {bold: false, n: ''});
    assert.equal(await rail.locator('[data-tr-catch] .tr-badge').textContent(), '3');
    // Find a thread narrows both lists by title.
    await page.locator('#tr-find').fill('thread 1');
    assert.deepEqual(await titles('threads'), ['Thread 1', 'Thread 10', 'Thread 11', 'Thread 12']);
    assert.deepEqual(await titles('pinned'), []);
    await page.locator('#tr-find').fill('');
    // The header: the name with its #, the pin, and who is in it as faces and a count.
    await page.locator('.thv-head .thv-people-n').waitFor();
    assert.equal(await page.locator('.thv-head .thv-people-n').textContent(), '3');
    assert.match(await page.locator('.thv-head .thv-title').innerText(), /^#\s*Launch plan$/);
    assert.ok(await sideways(page) <= 1300, 'no sideways scroll');
    await themed(page, 'rail-thread');
    // Another thread opens in the same pane; the rail stays as it was.
    const railBefore = await page.$('#thread-rail');
    await rail.locator('.tr-row[data-thread="t4"]').click();
    await page.waitForFunction(() => location.hash === '#/thread/t4');
    await page.getByText('Morning.').waitFor();
    assert.ok(await railBefore.evaluate(e => e.isConnected), 'the rail is not drawn again');
    assert.equal(await rail.locator('.tr-row.cur').getAttribute('data-thread'), 't4');
    await rail.locator('.tr-row[data-thread="t-plan"]').click();
    await page.getByText('And churn was low.').waitFor();
    // #/threads opens the thread last opened.
    await page.goto('https://tico-ui.test/#/threads');
    await page.waitForFunction(() => location.hash === '#/thread/t-plan');
    // The pin: unpinned, the thread moves to Threads; pinned again, back to Pinned.
    await page.locator('.thv-head .thv-pin[aria-pressed="true"]').click();
    await rail.locator('[data-list="threads"] .tr-row[data-thread="t-plan"]').waitFor();
    assert.deepEqual(await titles('pinned'), []);
    await page.locator('.thv-head .thv-pin[aria-pressed="false"]').click();
    await rail.locator('[data-list="pinned"] .tr-row[data-thread="t-plan"]').waitFor();
    assert.deepEqual(seen.pins, ['DELETE:t-plan', 'PUT:t-plan']);
    // A manager pins it for everyone from Thread settings.
    await page.locator('[data-thv-menu]').click();
    await page.locator('#thread-settings[open] #ths-pin').click();
    for (let i = 0; i < 40 && !seen.patches.length; i++) await page.waitForTimeout(50);
    assert.deepEqual(seen.patches, [{pinned_for_everyone: true}]);
    seen.patches.length = 0;
    await page.keyboard.press('Escape');
    await page.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));

    // One thread, flat: an older server's tree drawn in its order with no indent, each sub-thread reply quoting the
    // message its sub-thread opened under.
    await page.goto('https://tico-ui.test/#/thread/t-plan');
    await page.getByText('And churn was low.').waitFor();
    const order = await page.locator('.thv-list .th-msg .th-body').allTextContents();
    assert.deepEqual(order.slice(0, 5).map(s => s.trim()), ['Kickoff is Monday.', 'What price do we open with?', 'Last quarter we opened at 40.', 'And churn was low.', 'Then 45 this time.'], 'time order');
    assert.equal((await page.locator('.th-notshown[data-mid="h1"]').textContent()).trim(), 'Not shown', 'a message the viewer may not read is one quiet row');
    const lefts = await page.locator('.thv-list .th-msg[data-mid] .th-av').evaluateAll(els => [...new Set(els.map(e => Math.round(e.getBoundingClientRect().left)))]);
    assert.equal(lefts.length, 1, 'no indent: ' + lefts);
    assert.equal(await page.locator('.th-subhead, .th-sub').count(), 0, 'no sub-thread heads');
    const quoted = id => page.locator(`.th-msg[data-mid="${id}"] .th-quote`).getAttribute('data-th-quote');
    assert.equal(await quoted('s1'), 'm2');
    assert.equal(await quoted('s1a'), 's1');
    assert.equal(await quoted('s2'), 'm2');
    assert.equal(await page.locator('.th-msg[data-mid="s1"] .th-quote-who').textContent(), 'You', 'the viewer\'s own message, as their name reads');
    assert.equal(await page.locator('.th-msg[data-mid="m1"] .th-quote').count(), 0);
    // A turn's steps load when their fold opens.
    await page.locator('.th-msg[data-mid="m5"] details.run-steps summary').click();
    await page.locator('.th-msg[data-mid="m5"] .steps-list .step-tool').waitFor();
    assert.deepEqual(seen.steps, ['turn1']);
    assert.equal(await page.locator('.th-update').getAttribute('href'), '#/updates?kind=daily&day=2026-10-31&update=u1', 'the update card links to the update');
    await shot(page, 'thread-desktop');

    // A reply shows "Checking…" until the server answers, then the message.
    await page.locator('.thv-composer .p-text').fill('Monday works.');
    await page.locator('.thv-composer .p-send').click();
    await page.getByText('Checking…').waitFor();
    await page.locator('.th-msg[data-mid="n1"]').waitFor();
    assert.equal(seen.posts.at(-1).text, 'Monday works.');

    // A held message stays with its author with the rule that held it.
    await page.locator('.thv-composer .p-text').fill('The secret code is 1234');
    await page.locator('.thv-composer .p-send').click();
    await page.locator('.th-held').getByText('No secrets in threads').waitFor();
    assert.equal(await page.locator('.th-held [data-held-send]').count(), 0, 'Send anyway only when the hold allows it');

    // A reaction: picked from the hover bar; mine comes from the reactors the server lists, and its bare-list answer
    // settles the row.
    await page.locator('.th-msg[data-mid="m1"]').hover();
    await page.locator('.th-msg[data-mid="m1"] [data-th-pick]').click();
    await page.locator('.thv-emoji button[data-k="👍"]').click();
    await page.locator('.th-msg[data-mid="m1"] .th-react.me[aria-pressed="true"]').waitFor();
    await page.locator('.th-msg[data-mid="m1"] .th-react').click();
    await page.waitForFunction(() => !document.querySelector('.th-msg[data-mid="m1"] .th-react'));

    // A late page from the server keeps a newer message the stream already showed.
    await page.evaluate(() => threadViewsLive('messages', {id: 'live1', thread_id: 't-plan', root_id: 't-plan',
      message: {id: 'live1', seq: 60, revision: 1, thread_id: 't-plan', sender_id: 'human:bea', body: 'Arrived live.', created_at: new Date().toISOString()}}));
    await page.getByText('Arrived live.').waitFor();
    await page.evaluate(() => thvRefresh([...THV_VIEWS].find(v => v.id === 't-plan')));
    assert.equal(await page.locator('.th-msg[data-mid="live1"]').count(), 1, 'the refresh did not wipe the live message');

    // An older server's whole tree, drawn flat: a long branch's start is one link to it, and what is drawn is what is read.
    await page.goto('https://tico-ui.test/#/thread/t-tree');
    await page.getByText('Unread inline.').waitFor();
    await page.waitForFunction(() => document.querySelector('.th-msg[data-mid="n1"]'));
    const mids = () => page.locator('.thv-list [data-mid]').evaluateAll(els => els.map(e => e.dataset.mid).filter(id => !/^f\d/.test(id)));
    assert.deepEqual(await mids(), ['p1', 'r1', 'p2', 'more:t-lb', 'b3', 'b4', 'p3', 'n1']);
    assert.equal(await page.locator('.th-more').getAttribute('href'), '#/thread/t-lb');
    await page.waitForTimeout(150);
    assert.ok(seen.readSeqs.includes('t-nb:24'), 'a sub-thread drawn inline is read: ' + seen.readSeqs);
    assert.ok(seen.readSeqs.includes('t-tree:59') && !seen.reads.includes('t-rb'), 'read as far as drawn; nothing new, nothing sent: ' + seen.readSeqs);
    assert.ok(!seen.reads.includes('t-lb'), 'a collapsed part with news stays unread: ' + seen.readSeqs);
    assert.equal(await page.locator('.th-msg[data-mid="n1"].th-new').count(), 1, 'the mark is its own thread\'s, not the parent\'s');
    assert.equal(await page.locator('.th-msg[data-mid="r1"].th-new').count(), 0);
    // One "New" line, above the first message not read when the thread opened.
    assert.equal(await page.locator('.th-newline').count(), 1);
    assert.ok(await page.evaluate(() => document.querySelector('.th-newline').nextElementSibling?.classList.contains('th-new')), 'the line sits on the first new one');
    await page.evaluate(() => document.querySelector('.th-msg[data-mid="p1"]').scrollIntoView());
    await shot(page, 'subthreads-desktop');
    // A reply arriving live in an old sub-thread lands at the bottom, in time order, quoting its sub-thread's message.
    await page.evaluate(() => { const b = document.querySelector('.thv-scroll'); b.scrollTop = b.scrollHeight; });
    await page.evaluate(() => threadViewsLive('messages', {id: 'lv', thread_id: 't-rb', root_id: 't-tree',
      message: {id: 'lv', seq: 70, revision: 0, thread_id: 't-rb', sender_id: 'human:bea', body: 'Late reply.', created_at: new Date().toISOString()}}));
    assert.equal((await mids()).at(-1), 'lv');
    assert.equal(await page.locator('.th-msg[data-mid="lv"].th-new').count(), 0, 'arriving at the bottom is not news, in any thread');
    assert.equal(await page.locator('.th-msg[data-mid="lv"] .th-quote').getAttribute('data-th-quote'), 'p1');
    await page.waitForTimeout(150);
    assert.ok(seen.readSeqs.includes('t-rb:70'), seen.readSeqs);
    await page.goto('https://tico-ui.test/#/thread/t-plan');
    await page.getByText('And churn was low.').waitFor();

    // Posting closed (archived) hides the composer; reopened, it is back with what was typed.
    await page.locator('.thv-composer .p-text').fill('Kept while archived');
    await page.evaluate(() => { const v = [...THV_VIEWS].find(x => x.id === 't-plan'); v.thread = {...v.thread, me: {...v.thread.me, can_post: false}}; thvDraw(v); });
    assert.equal(await page.locator('.thv-composer').isHidden(), true);
    await page.evaluate(() => { const v = [...THV_VIEWS].find(x => x.id === 't-plan'); v.thread = {...v.thread, me: {...v.thread.me, can_post: true}}; thvDraw(v); });
    assert.equal(await page.locator('.thv-composer .p-text').inputValue(), 'Kept while archived');
    await page.locator('.thv-composer .p-text').fill('');

    // Two joins at once: the later one wins, the earlier seat is given back, and only one microphone is ever on.
    await page.evaluate(() => { void callJoin('t-plan', {id: 'c1'}, 'A').catch(() => {}); void callJoin('t-plan', {id: 'c2'}, 'B').catch(() => {}); });
    await page.locator('#call-bar [data-cb="end"]').waitFor();
    await page.waitForFunction(() => window.__rooms.some(r => r.mic));
    await page.waitForTimeout(400);
    const rooms = await page.evaluate(() => window.__rooms.map(r => ({token: r.token, live: r.mic && !r.gone})));
    assert.deepEqual(rooms.filter(r => r.live).map(r => r.token), ['tok-c2'], 'one live microphone: ' + JSON.stringify(rooms));
    assert.ok(seen.calls.includes('leave:c1'), 'the overtaken join gives its seat back');
    // End that fails is not Leave: the call goes on and End stays to try again.
    await page.locator('#call-bar [data-cb="end"]').click();
    await page.locator('.toast.err', {hasText: 'Could not end'}).waitFor();
    assert.equal(await page.locator('#call-bar [data-cb="end"]').count(), 1);
    await page.locator('#call-bar [data-cb="leave"]').click();
    await page.waitForFunction(() => !document.querySelector('#call-bar'));
    await page.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));

    // The desktop app hides the page to show another org's (window.ticoPageHidden; no pagehide comes then): a live
    // call stops its microphone and leaves at once, and a join still on its way never turns the microphone on.
    assert.deepEqual(await page.evaluate(() => window.ticoPageHidden()), {inCall: false});
    await page.evaluate(() => { window.__rooms.length = 0; void callJoin('t-plan', {id: 'c3'}, 'C').catch(() => {}); });
    await page.waitForFunction(() => window.__rooms.some(r => r.mic));
    assert.deepEqual(await page.evaluate(() => window.ticoPageHidden()), {inCall: true});
    assert.deepEqual(await page.evaluate(() => window.__rooms.map(r => ({stopped: r.mic_track.stopped, gone: r.gone}))), [{stopped: true, gone: true}]);
    assert.equal(await page.locator('#call-bar').count(), 0, 'the call bar goes with the call');
    for (let i = 0; i < 20 && !seen.calls.includes('leave:c3'); i++) await page.waitForTimeout(50);
    assert.ok(seen.calls.includes('leave:c3'), 'the hidden page gives its seat back');
    for (const wait of [0, 260]) {      // still joining on the server, then connecting to the room
      await page.evaluate(() => { window.__rooms.length = 0; window.__joining = callJoin('t-plan', {id: 'c4'}, 'D').catch(() => {}); });
      await page.waitForTimeout(wait);
      assert.deepEqual(await page.evaluate(() => window.ticoPageHidden()), {inCall: true}, 'a join on its way counts as in a call');
      await page.evaluate(() => window.__joining);
      await page.waitForTimeout(250);
      assert.deepEqual(await page.evaluate(() => window.__rooms.map(r => ({mic: r.mic, gone: r.gone}))), wait ? [{mic: false, gone: true}] : [],
        'no microphone after the page went hidden');
      assert.equal(await page.locator('#call-bar').count(), 0);
    }
    assert.equal(seen.calls.filter(c => c === 'leave:c4').length, 2, 'each overtaken join gives its seat back: ' + seen.calls);
    // Hidden while Start call is still making the call: in a call already, the call made is never joined, and the seat
    // creation gave goes back.
    callMade = {id: 'c5', state: 'live', connector: 'human:ana'};
    await page.evaluate(() => { window.__rooms.length = 0; window.__starting = callAction([...THV_VIEWS].find(v => v.id === 't-plan'), 'start'); });
    for (let i = 0; i < 20 && !seen.calls.includes('start:c5'); i++) await page.waitForTimeout(25);
    assert.deepEqual(await page.evaluate(() => window.ticoPageHidden()), {inCall: true}, 'a call being made counts as in a call');
    await page.evaluate(() => window.__starting);
    await page.waitForTimeout(450);
    assert.ok(!seen.calls.includes('join:c5'), 'the call made while hidden is not joined: ' + seen.calls);
    assert.equal(seen.calls.filter(c => c === 'leave:c5').length, 1, 'the call made while hidden gives its seat back: ' + seen.calls);
    assert.deepEqual(await page.evaluate(() => window.__rooms.length), 0);
    assert.equal(await page.locator('#call-bar').count(), 0);
    // A join the server refuses still says so: Start call's own join number is the one the join checks.
    callMade = {id: 'c6', state: 'live', connector: 'human:ana'}; joinRefused = true;
    await page.evaluate(() => callAction([...THV_VIEWS].find(v => v.id === 't-plan'), 'start'));
    assert.ok(seen.calls.includes('join:c6'), 'the call made is joined: ' + seen.calls);
    await page.locator('.thv-call-note', {hasText: 'Removed from call'}).waitFor();
    await page.evaluate(() => { const v = [...THV_VIEWS].find(x => x.id === 't-plan'); CALL_THREADS.set('t-plan', {...CALL_THREADS.get('t-plan'), removed: false}); thvDraw(v); });
    callMade = null; joinRefused = false;
    // Start call still on its way, the call's start redraws the header to Join, and Join gets in first: when Start
    // answers, its cleanup leaves that seat alone and the call stays connected.
    callMade = {id: 'c7', state: 'live', connector: 'human:ana'}; startWait = 800;
    await page.evaluate(() => { window.__rooms.length = 0; window.__starting = callAction([...THV_VIEWS].find(v => v.id === 't-plan'), 'start'); });
    for (let i = 0; i < 20 && !seen.calls.includes('start:c7'); i++) await page.waitForTimeout(25);
    await page.evaluate(() => { const v = [...THV_VIEWS].find(x => x.id === 't-plan'); CALL_THREADS.set('t-plan', {...CALL_THREADS.get('t-plan'), call: {id: 'c7', state: 'live'}}); thvDraw(v); });
    await page.locator('.thv-call[data-call="join"]').first().click();
    await page.waitForFunction(() => CALL?.id === 'c7' && window.__rooms.some(r => r.mic));
    await page.evaluate(() => window.__starting);
    await page.waitForTimeout(150);
    assert.ok(!seen.calls.includes('leave:c7'), 'the Join that got in first keeps its seat: ' + seen.calls);
    assert.equal(seen.calls.filter(c => c === 'join:c7').length, 1, 'Start call does not join again: ' + seen.calls);
    assert.deepEqual(await page.evaluate(() => [CALL?.id, window.__rooms.filter(r => !r.gone).length]), ['c7', 1], 'the call stays connected');
    assert.equal(await page.locator('#call-bar').count(), 1);
    await page.locator('#call-bar [data-cb="leave"]').click();
    await page.waitForFunction(() => !document.querySelector('#call-bar'));
    callMade = null; startWait = 150;

    // Start B while in A; its event redraws Join before Start answers. Join waits for A's held leave, then Start
    // answers and leaves the seat to Join. Hidden or refused, Join owes that seat back once; connected, it keeps it.
    for (const [id, finish] of [['c8', 'hidden'], ['c9', 'refused'], ['c10', 'joined']]) {
      await page.evaluate(async id => { window.__rooms.length = 0; await callJoin('t4', {id: id + '-a'}, 'A'); }, id);
      await page.locator('.thv-call[data-call="start"]').first().waitFor();
      callMade = {id, state: 'live', connector: 'human:ana'}; startGate = []; leaveGate = [];
      await page.evaluate(() => { window.__starting = callAction([...THV_VIEWS].find(v => v.id === 't-plan'), 'start'); });
      for (let i = 0; i < 20 && !startGate.length; i++) await page.waitForTimeout(25);
      assert.equal(startGate.length, 1, 'Start B waits for its response');
      callLive = callMade;
      await page.evaluate(() => threadViewsLive('threads', {kind: 'call.started', thread_id: 't-plan'}));
      await page.locator('.thv-call[data-call="join"]').first().click();
      for (let i = 0; i < 20 && !leaveGate.length; i++) await page.waitForTimeout(25);
      assert.equal(leaveGate.length, 1, 'Join B waits for A to leave');
      const starts = startGate; startGate = null; starts.forEach(go => go());
      await page.evaluate(() => window.__starting);
      assert.deepEqual(await page.evaluate(id => [CALL?.id, CALL_JOINING.get(id)], id), [id + '-a', 1]);
      assert.equal(seen.calls.filter(c => c === 'leave:' + id).length, 0, 'Start B defers cleanup to Join');
      if (finish === 'hidden') assert.deepEqual(await page.evaluate(() => window.ticoPageHidden()), {inCall: true});
      joinRefused = finish === 'refused';
      const leaves = leaveGate; leaveGate = null; leaves.forEach(go => go());
      await page.waitForFunction(() => CALL_JOINS === 0);
      await page.waitForTimeout(150);
      assert.equal(seen.calls.filter(c => c === 'leave:' + id).length, finish === 'joined' ? 0 : 1,
        'only an abandoned Join gives Start B\'s seat back, once: ' + seen.calls);
      assert.equal(seen.calls.filter(c => c === 'join:' + id).length, finish === 'hidden' ? 0 : 1);
      if (finish === 'joined') {
        assert.deepEqual(await page.evaluate(() => [CALL?.id, window.__rooms.filter(r => r.mic && !r.gone).length]), [id, 1]);
        callLive = null;
        await page.locator('#call-bar [data-cb="leave"]').click();
        await page.waitForFunction(() => !document.querySelector('#call-bar'));
      } else {
        assert.deepEqual(await page.evaluate(() => [CALL, window.__rooms.filter(r => !r.gone).length]), [null, 0]);
        assert.equal(await page.locator('#call-bar').count(), 0);
      }
      callMade = callLive = null; joinRefused = false;
      await page.evaluate(() => { const v = [...THV_VIEWS].find(x => x.id === 't-plan'); CALL_THREADS.set('t-plan', {call: null}); thvDraw(v); });
    }

    // An older sub-thread opened by its link keeps the way back, and reads flat too.
    await page.goto('https://tico-ui.test/#/thread/t-sub');
    await page.locator('.thv-crumbs a', {hasText: 'Launch plan'}).waitFor();
    assert.equal(await page.locator('.th-msg[data-mid="s1a"] .th-quote').getAttribute('data-th-quote'), 's1');
    await page.locator('.thv-back').click();
    await page.waitForFunction(() => location.hash === '#/thread/t-plan');

    // The bot page: Home and DM, and a quiet link to every thread with the bot.
    await page.goto('https://tico-ui.test/#/bot/ops');
    await page.getByText('Weekly numbers are in the doc.').waitFor();
    const tabs = await page.locator('#btabs [data-bt]').evaluateAll(els => els.map(b => b.dataset.bt));
    assert.ok(tabs.includes('home') && tabs.includes('dm'), 'Home and DM: ' + tabs);
    assert.ok(!tabs.includes('chat'));
    assert.equal(await page.locator('.bot-threads-link').count(), 0, 'no Threads with link on a Home');
    await shot(page, 'bot-home-desktop');
    // The Home's actions (the call and the menu) sit in the page's top line; the thread has no title line.
    await page.locator('#bot-top #bot-top-acts [data-thv-menu]').waitFor();
    assert.equal(await page.locator('#bot-thread .thv-title').count(), 0);
    // Wide, the top tabs give way to Tasks beside the thread; the switch above the thread picks Home or DM.
    assert.equal(await page.locator('#pane-chat [data-talk="home"]').getAttribute('aria-selected'), 'true');
    await page.locator('#pane-chat [data-talk="dm"]').click();
    await page.waitForFunction(() => location.hash === '#/bot/ops/dm');
    await page.locator('#pane-chat [data-talk="dm"][aria-selected="true"]').waitFor();
    await page.locator('#bot-thread .thv-composer .p-text').waitFor();
    await shot(page, 'bot-dm-desktop');
    // A draft survives a switch to Home and back.
    await page.locator('#bot-thread .thv-composer .p-text').fill('Half a thought');
    await page.locator('#pane-chat [data-talk="home"]').click();
    await page.getByText('Weekly numbers are in the doc.').waitFor();
    await page.locator('#pane-chat [data-talk="dm"]').click();
    await page.locator('#pane-chat [data-talk="dm"][aria-selected="true"]').waitFor();
    assert.equal(await page.locator('#bot-thread .thv-composer .p-text').inputValue(), 'Half a thought');

    // A person's Home under their profile: updates live on the Updates page, never as cards in a Home.
    await page.goto('https://tico-ui.test/#/person/bea');
    await page.getByText('Out Friday afternoon.').waitFor();
    assert.equal(await page.locator('#person-home .th-update').count(), 0, 'no update cards in a Home');
    await shot(page, 'person-desktop');

    // A Home with nothing in it is its composer, never a blank timeline; its actions sit in the card's header.
    await page.goto('https://tico-ui.test/#/person/cy');
    await page.locator('#person-home .thv-composer .p-text').waitFor();
    assert.equal(await page.locator('#person-home .thv-scroll').isVisible(), false);
    await page.locator('#person-home-acts [data-call="start"]').waitFor();    // a call starts from a person's Home
    await shot(page, 'person-empty-home-desktop');
    assert.equal(await page.locator('#person-notify-browser').count(), 0, 'browser notifications are the viewer\'s own, on their own page');
    assert.equal(await page.locator('#person-github').count(), 1, 'their manager gets the profile\'s edit controls');
    // One the viewer may not post to has nothing to show at all.
    await page.goto('https://tico-ui.test/#/person/di');
    await page.locator('h1', {hasText: 'Di'}).waitFor();
    await page.waitForFunction(() => document.getElementById('person-home')?.hidden === true);
    assert.equal(await page.locator('#person-github').count(), 0, 'no Manage, no edit controls');

    // A sub-thread opened on its own starts a call too.
    await page.goto('https://tico-ui.test/#/thread/t-sub');
    await page.locator('.thv-head [data-call="start"]').waitFor();

    await page.goto('https://tico-ui.test/#/thread/t-edit');
    await page.locator('.th-msg[data-mid="ed1"] .th-body').getByText('Open at 40.').waitFor();

    // Thread settings: Summarize, and one reader's notification setting.
    const settings = async () => { await page.locator('[data-thv-menu]').click(); return page.locator('#thread-settings[open]'); };
    await (await settings()).locator('[data-ths="catchup"]').click();
    await page.locator('.toast', {hasText: 'Summary ready'}).waitFor();
    assert.deepEqual(seen.catchUps, [{thread: 't-edit', scope: 'subtree'}]);
    await (await settings()).locator('[data-notify="digest"]').click();
    for (let i = 0; i < 40 && !seen.follows.length; i++) await page.waitForTimeout(50);
    assert.deepEqual(seen.follows, ['digest']);
    await page.keyboard.press('Escape');
    assert.equal(await (await settings()).locator('[data-notify][aria-pressed="true"]').textContent(), 'Digest', 'the panel shows the current setting');
    await page.keyboard.press('Escape');
    await page.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));

    // A migrated Home: one "Earlier messages" row and the manager's control; the notice in a DM opens the same history.
    await page.goto('https://tico-ui.test/#/thread/h-old');
    await page.getByText('New plan.').waitFor();
    assert.equal(await page.locator('.th-earlier').count(), 1);
    assert.match(await page.locator('.th-earlier').textContent(), /Earlier messages/);
    await shot(page, 'earlier-desktop');
    await page.locator('.th-earlier [data-th-earlier]').click();
    await page.locator('.toast', {hasText: 'Earlier messages open'}).waitFor();
    assert.deepEqual(seen.earlier, ['h-old']);
    await page.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));
    await page.goto('https://tico-ui.test/#/thread/t-notice');
    await page.locator('.th-earlier-open').click();
    await page.waitForFunction(() => document.querySelector('.toast')?.textContent.includes('Earlier messages open'));
    assert.deepEqual(seen.earlier, ['h-old', 'h-old']);
    await page.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));

    // New thread: members as chips (a team she has Write on among them, not one without), a name, who sees it, a pin.
    await page.goto('https://tico-ui.test/#/thread/t-plan');
    await page.getByText('And churn was low.').waitFor();
    await rail.locator('[data-tr-new]').click();
    const nt = page.locator('#thread-new[open]');
    await nt.locator('#nt-add').waitFor();
    await nt.locator('#nt-add').focus();
    const offered = await nt.locator('#nt-sugg li > span:last-child').allTextContents();
    assert.ok(offered.includes('Ops team') && !offered.includes('Legal') && !offered.includes('Ana'), 'teammates she may add: ' + offered);
    assert.equal(await nt.locator('[type=submit]').isDisabled(), true, 'no name, no thread');
    await nt.locator('#nt-sugg li', {hasText: 'Bea'}).click();
    await nt.locator('#nt-sugg li', {hasText: 'Cy'}).click();
    assert.deepEqual(await nt.locator('.nt-chip').evaluateAll(els => els.map(e => e.dataset.actor)), ['human:bea', 'human:cy']);
    assert.equal(await nt.locator('.nt-why').textContent(), 'Group chats need a name.');
    assert.equal(await nt.locator('[type=submit]').isDisabled(), true);
    await nt.locator('#nt-name').focus();      // the shot shows the form, not the suggestions
    await themed(page, 'new-thread');
    await nt.locator('#nt-name').fill('#launch-review');
    assert.equal(await nt.locator('.nt-why').textContent(), '');
    await nt.locator('[type=submit]').click();
    await page.waitForFunction(() => location.hash === '#/thread/t-new');
    assert.deepEqual(seen.created, [{title: 'launch-review', audience: 'members', audience_group_id: null, members: ['human:bea', 'human:cy'], pin_for_members: true}]);

    // Catch up: one thread at a time; Mark read goes on, Later puts one at the end, Esc closes.
    await page.goto('https://tico-ui.test/#/thread/t-plan');
    await page.getByText('And churn was low.').waitFor();
    await rail.locator('[data-tr-catch]').click();
    const cu = page.locator('#catch-up[open]');
    await cu.locator('.cu-card h3', {hasText: 'Thread 11'}).waitFor();
    assert.equal(await cu.locator('.cu-pos').textContent(), '1 of 3');
    assert.equal(await cu.locator('.cu-you').count(), 1, 'it says the viewer is named');
    assert.match(await cu.locator('.cu-msgs').innerText(), /Booked for Friday\.\s*…/, 'a clipped message ends in …');
    await themed(page, 'catch-up');
    await cu.locator('[data-cu-read]').click();
    await cu.locator('.cu-card h3', {hasText: 'Thread 4'}).waitFor();
    assert.equal(await cu.locator('.cu-pos').textContent(), '2 of 3');
    await cu.locator('[data-cu-later]').click();
    await cu.locator('.cu-card h3', {hasText: 'Bea, Cy'}).waitFor();
    assert.equal(await cu.locator('.cu-pos').textContent(), '2 of 3', 'Later keeps the place');
    await page.keyboard.press('ArrowRight');
    await cu.locator('.cu-card h3', {hasText: 'Thread 4'}).waitFor();
    assert.equal(await cu.locator('.cu-pos').textContent(), '3 of 3', 'what was put off comes last');
    await cu.locator('[data-cu-mute]').click();
    await cu.getByText('All caught up').waitFor();
    for (let i = 0; i < 40 && seen.readAll.length < 3; i++) await page.waitForTimeout(50);
    assert.deepEqual(seen.readAll, ['t11', 't-gdm', 't4']);
    assert.equal(seen.follows.at(-1), 'muted');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('#catch-up')?.open);
    // Opened again, Esc leaves from the first card too.
    await rail.locator('[data-tr-catch]').click();
    await cu.locator('.cu-card h3').waitFor();
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('#catch-up')?.open);

    // Browse threads: one she reads but is not in; Join puts it on the rail.
    await rail.locator('[data-tr-browse]').click();
    const br = page.locator('#thread-browse[open]');
    await br.locator('li[data-thread="t-open"] [data-join]').click();
    await rail.locator('.tr-row[data-thread="t-open"]').waitFor();
    assert.equal(seen.follows.at(-1), 'live');
    await page.keyboard.press('Escape');

    // A search hit opens its thread at the message.
    await page.goto('https://tico-ui.test/#/thread/t-plan?m=m2');
    await page.locator('.th-msg.th-focus[data-mid="m2"]').waitFor();

    // New DM (on a teammate's page): a picker of teammates, never groups.
    await page.goto('https://tico-ui.test/#/person/bea');
    await page.locator('#person-dm').click();
    await page.locator('#thread-new .thd-pick').first().waitFor();
    const picks = await page.locator('#thread-new .thd-pick span').allTextContents();
    assert.ok(picks.includes('Bea') && picks.includes('Ops') && !picks.includes('Ana'), 'teammates, not yourself: ' + picks);
    assert.equal(await page.locator('#thread-new input[value^="group:"]').count(), 0);
    await shot(page, 'new-dm-desktop');
    await page.keyboard.press('Escape');

    // Members: each avatar keeps its round size (a group's initials, a bot's shape), names start in one column, and
    // Owner or Remove sits at the row's end.
    await page.route('**/api/v2/threads/t-plan/members*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({
      members: [{actor: 'human:ana', role: 'owner'}, {actor: 'group:ops'}, {actor: 'bot:ops'}, {actor: 'human:bea'}], next_cursor: null})}));
    await page.goto('https://tico-ui.test/#/thread/t-plan');
    await page.locator('.thv-head [data-thv-members]').click();
    const mem = page.locator('#thread-members[open]');
    await mem.locator('.thd-list li .thd-name').nth(3).waitFor();
    const rows = await mem.locator('.thd-list li').evaluateAll(lis => lis.map(li => {
      const box = e => e && e.getBoundingClientRect(), r = box(li), av = box(li.querySelector('.av')), name = box(li.querySelector('.thd-name'));
      const end = box(li.querySelector('.thd-tag, [data-remove]'));
      return {w: av.width, h: av.height, avX: av.left, nameX: name.left, endGap: end ? r.right - end.right : null, tag: li.querySelector('.thd-tag')?.textContent || ''};
    }));
    for (const [i, r] of rows.entries()) assert.ok(Math.abs(r.w - r.h) < 1 && r.w <= 24, `row ${i}: the avatar is round at its size: ${r.w}x${r.h}`);
    assert.equal(new Set(rows.map(r => Math.round(r.avX))).size, 1, 'avatars line up');
    assert.equal(new Set(rows.map(r => Math.round(r.nameX))).size, 1, 'names start at one x');
    assert.equal(rows[0].tag, 'Owner');
    for (const [i, r] of rows.entries()) assert.ok(r.endGap !== null && r.endGap <= 8, `row ${i}: Owner or Remove ends the row (${r.endGap}px from its end)`);
    // Add is a plain link, with no disclosure triangle.
    const summary = mem.locator('.thd-add>summary');
    assert.equal(await summary.evaluate(e => getComputedStyle(e).display), 'inline-flex');
    assert.equal(await summary.locator('svg').count(), 1);
    await themed(page, 'members');
    await page.keyboard.press('Escape');
    await page.unroute('**/api/v2/threads/t-plan/members*');

    assert.ok(seen.reads.includes('t-plan'), 'opening a thread marks it read');
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  // ---- Bob, who may see the bot but not read it: no Home on its page, and no Home asked for.
  {
    const {page, seen} = await open(browser, 'bob');
    await page.goto('https://tico-ui.test/#/bot/ops');
    await page.locator('#btabs').waitFor({state: 'attached'});   // wide, the top tabs are hidden (Tasks sits beside the thread)
    await page.waitForTimeout(300);
    const tabs = await page.locator('#btabs [data-bt]').evaluateAll(els => els.map(b => b.dataset.bt));
    assert.ok(!tabs.includes('home'), 'no Home without Read: ' + tabs);
    assert.ok(!tabs.includes('dm'), 'no DM without Write: ' + tabs);
    // Nothing for Bob to catch up on: the Threads link with no count.
    await page.locator('#nav-threads:not([hidden])').waitFor();
    assert.equal(await page.locator('#nav-threads .nav-badge').isVisible(), false);
    assert.equal(await page.locator('.bot-threads-link').count(), 0);
    assert.equal(await page.locator('#pane-chat [data-talk]').count(), 0);
    assert.ok(!seen.homes.includes('bot:ops'), 'the Home is never asked for');
    await shot(page, 'bob-bot-desktop');
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  // ---- Ana at phone width: the Threads tab in the bottom bar, the list, one thread.
  {
    const {page, seen} = await open(browser, 'ana', {phone: true});
    await page.goto('https://tico-ui.test/#/tasks');
    await page.locator('#mobile-threads:not([hidden])').waitFor();
    assert.equal(await page.locator('#mobile-threads .nav-badge').textContent(), '3', 'the tab carries what Catch up has left');
    assert.equal((await page.locator('#mobile-threads .mobile-nav-label').textContent()).trim(), 'Threads');
    // Threads takes Search's place in the bar, so search over everything is in the drawer.
    assert.equal(await page.locator('#mobile-search').isVisible(), false);
    await page.locator('#mobile-more').click();
    await page.locator('#search-open').waitFor();
    await page.waitForTimeout(300);     // the drawer slides in
    assert.equal(await page.locator('#nav-threads').isVisible(), true);
    await shot(page, 'drawer-phone');
    await page.locator('#search-open').click();
    await page.locator('#search-modal[open]').waitFor();
    assert.equal(await page.evaluate(() => document.body.classList.contains('drawer')), false, 'the drawer closes behind it');
    await page.keyboard.press('Escape');
    await page.locator('#search-modal[open]').waitFor({state: 'detached'});
    // The tab opens the rail on its own; a thread takes the screen, and Back returns to the rail.
    await page.locator('#mobile-threads').click();
    await page.waitForFunction(() => location.hash === '#/threads');
    await page.locator('#thread-rail .tr-row[data-thread="t-plan"]').waitFor();
    assert.equal(await page.locator('#thread-pane').isVisible(), false);
    assert.ok(await sideways(page) <= 390, 'no sideways scroll on the rail');
    const small = await page.$$eval('#thread-rail .tr-row, #thread-rail [data-tr-catch], #thread-rail [data-tr-new], #thread-rail #tr-find',
      els => els.filter(e => e.getBoundingClientRect().height < 44).map(e => e.className || e.id));
    assert.deepEqual(small, [], 'every tap target is at least 44px tall');
    await themed(page, 'rail', true);
    await page.locator('#thread-rail .tr-row[data-thread="t-plan"]').tap();
    await page.waitForFunction(() => location.hash === '#/thread/t-plan');
    await page.getByText('And churn was low.').waitFor();
    assert.equal(await page.locator('#thread-rail').isVisible(), false);
    assert.ok(await sideways(page) <= 390, 'no sideways scroll on a thread');
    const head = await page.locator('.tr-pane .thv-titleline').boundingBox();
    assert.ok(head.height <= 48, 'the phone header is one line: ' + head.height);
    await themed(page, 'rail-thread', true);
    await page.locator('.thv-back-rail').tap();
    await page.waitForFunction(() => location.hash === '#/threads');
    await page.locator('#thread-rail .tr-row[data-thread="t-plan"]').waitFor({state: 'visible'});
    // Catch up takes the screen above the tab bar, with no Mute; Esc closes it.
    await page.locator('#thread-rail [data-tr-catch]').tap();
    await page.locator('#catch-up[open] .cu-card h3').waitFor();
    assert.equal(await page.locator('#catch-up [data-cu-mute]').isVisible(), false);
    const cuBox = await page.locator('#catch-up').boundingBox(), navBox = await page.locator('#mobile-nav').boundingBox();
    assert.ok(cuBox.width >= 389 && cuBox.y + cuBox.height <= navBox.y + 1, 'above the tab bar, the full width: ' + JSON.stringify([cuBox, navBox]));
    assert.ok(await sideways(page) <= 390);
    await themed(page, 'catch-up', true);
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('#catch-up')?.open);
    await page.locator('#thread-rail [data-tr-new]').tap();
    await page.locator('#thread-new[open] #nt-name').waitFor();
    assert.ok(await sideways(page) <= 390);
    await themed(page, 'new-thread', true);
    // Esc closes the suggestions first, then the dialog.
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#thread-new[open]').count(), 1);
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('#thread-new')?.open);
    await page.goto('https://tico-ui.test/#/thread/t-plan');
    await page.getByText('And churn was low.').waitFor();
    // On a phone the timeline reads the same, with no sideways scroll; a long press, not a hover bar, holds the actions.
    assert.equal(await page.locator('.th-msg[data-mid="s1a"]').isVisible(), true);
    assert.equal(await page.locator('.th-msg[data-mid="m1"] [data-th-reply]').isVisible(), false);
    const width = await page.evaluate(() => document.documentElement.scrollWidth);
    assert.ok(width <= 390, 'no sideways scroll: ' + width);
    await shot(page, 'thread-phone');
    await page.goto('https://tico-ui.test/#/thread/t-tree');
    await page.getByText('Unread inline.').waitFor();
    await page.evaluate(() => document.querySelector('.th-msg[data-mid="p1"]').scrollIntoView());
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth) <= 390);
    await shot(page, 'subthreads-phone');
    await page.goto('https://tico-ui.test/#/bot/ops');
    await page.getByText('Weekly numbers are in the doc.').waitFor();
    await shot(page, 'bot-home-phone');
    await page.goto('https://tico-ui.test/#/person/bea');
    await page.getByText('Out Friday afternoon.').waitFor();
    await shot(page, 'person-phone');
    await page.goto('https://tico-ui.test/#/person/cy');
    await page.locator('#person-home .thv-composer .p-text').waitFor();
    await page.evaluate(() => document.querySelector('#person-home').scrollIntoView());
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth) <= 390);
    await shot(page, 'person-empty-home-phone');
    await page.goto('https://tico-ui.test/#/thread/t-plan');
    await page.locator('.th-notshown').waitFor();
    await page.evaluate(() => document.querySelector('.th-notshown').scrollIntoView({block: 'center'}));
    await shot(page, 'not-shown-phone');
    await page.goto('https://tico-ui.test/#/person/bea');
    await page.locator('#person-dm').tap();
    await page.locator('#thread-new .thd-pick').first().waitFor();
    await shot(page, 'new-dm-phone');
    await page.keyboard.press('Escape');
    assert.deepEqual(seen.errors, []);
    await page.close();
  }
  // ---- Ana at phone width: the way around a bot and a person, rows as the server sends them, a thread's settings.
  {
    dmRows = [
      {id: 't-zed', title: '', audience: 'personal', last_seq: 9, last_unread_seq: 9, me: me(), dm_with: [{actor: 'human:zed', name: 'Zed Stone'}], dm_with_count: 1},
      {id: 't-group', title: '', audience: 'personal', last_seq: 8, last_unread_seq: 8, me: me(),
        dm_with: [{actor: 'human:bea', name: 'Bea'}, {actor: 'human:cy', name: 'Cy'}, {actor: 'human:di', name: ''}, {actor: 'bot:ops', name: 'Ops'}], dm_with_count: 6},
      {id: 't-none', title: '', audience: 'personal', last_seq: 7, last_unread_seq: 7, me: me(), dm_with: [], dm_with_count: 0},
    ];
    timeline['t-zed'] = () => ({thread: dmRows[0], breadcrumb: [], next_cursor: null, messages: [msg('z1', 1, 'human:zed', 'Got a minute?', {thread_id: 't-zed'})]});
    const {page, seen} = await open(browser, 'ana', {phone: true});

    // Home and DM by name in the top line, each switching the page, and all of it on the screen.
    await page.goto('https://tico-ui.test/#/bot/ops');
    const tabs = page.locator('#btabs [data-bt]');
    await tabs.first().waitFor();
    assert.deepEqual((await tabs.allInnerTexts()).slice(0, 2), ['Home', 'DM']);
    for (const width of [390, 320]) {
      await page.setViewportSize({width, height: 700});
      for (const box of await tabs.evaluateAll(els => els.map(el => el.getBoundingClientRect().toJSON()))) assert.ok(box.right <= width && box.height >= 36, `a tab at ${width}: ` + JSON.stringify(box));
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth) <= width, 'no sideways scroll at ' + width);
    }
    await page.setViewportSize({width: 390, height: 844});
    await shot(page, 'bot-tabs-phone');
    await page.locator('#btabs [data-bt="dm"]').tap();
    await page.waitForFunction(() => location.hash.endsWith('/dm') && document.querySelector('#btabs [data-bt="dm"]').getAttribute('aria-selected') === 'true');
    await page.locator('#btabs [data-bt="home"]').tap();
    await page.waitForFunction(() => document.querySelector('#btabs [data-bt="home"]').getAttribute('aria-selected') === 'true');
    await page.getByText('Weekly numbers are in the doc.').waitFor();

    // A person's page starts a DM with them; your own does not offer one.
    await page.goto('https://tico-ui.test/#/person/bea');
    await page.locator('#person-dm').tap();
    assert.equal(await page.locator('#thread-new input[value="human:bea"]').isChecked(), true, 'the DM starts with this person');
    await page.keyboard.press('Escape');
    await page.goto('https://tico-ui.test/#/person/ana');
    await page.locator('#ptabs').waitFor();
    assert.equal(await page.locator('#person-dm').count(), 0);

    // A group chat on the rail is named by who the server says is in it; a 1:1 DM is not on the rail.
    await page.goto('https://tico-ui.test/#/threads');
    await page.locator('#thread-rail [data-thread="t-group"]').waitFor();
    assert.equal(await page.locator('#thread-rail [data-thread="t-group"] .tr-title').innerText(), 'Bea, Cy, Di +3');
    assert.equal(await page.locator('#thread-rail [data-thread="t-zed"], #thread-rail [data-thread="t-none"]').count(), 0);
    assert.ok(await sideways(page) <= 390);
    await shot(page, 'dm-rows-phone');
    // The name has room on a phone, next to Start call and Members.
    await page.goto('https://tico-ui.test/#/thread/t-zed');
    await page.locator('.thv-title', {hasText: 'Zed Stone'}).waitFor();
    // Measured by the page, once it has a box: a re-render can leave the title without one for a moment, and boundingBox()
    // then returns null (the CI failure, Nov 2). The poll waits for the box; the assert reports the width it got.
    const titleBox = () => page.evaluate(() => [...document.querySelectorAll('.thv-title')].find(e => e.textContent.includes('Zed Stone'))?.getBoundingClientRect().width || 0);
    await page.waitForFunction(() => [...document.querySelectorAll('.thv-title')].some(e => e.textContent.includes('Zed Stone') && e.getBoundingClientRect().width > 0));
    const width = await titleBox();
    assert.ok(width >= 100, `the title is not squeezed: ${width}px wide`);
    await shot(page, 'dm-head-phone');

    // A manager sets how long a thread keeps its messages, forever until they say otherwise.
    await page.goto('https://tico-ui.test/#/thread/t-plan');
    // It saves as it changes, from Thread settings.
    const keep = async () => {
      await page.locator('[data-thv-menu]').tap();
      return page.locator('#thread-settings[open] #ths-keep');
    };
    const done = async () => {
      await page.locator('#thread-settings .tmodal-x').tap();
      await page.waitForFunction(() => !document.querySelector('#thread-settings')?.open);
    };
    const select = await keep();
    assert.equal(await select.inputValue(), '', 'forever by default');
    await shot(page, 'keep-messages-phone');
    await select.selectOption('90');
    for (let i = 0; i < 40 && !seen.patches.length; i++) await page.waitForTimeout(50);
    assert.deepEqual(seen.patches, [{retention_days: 90}]);
    await done();
    assert.equal(await (await keep()).inputValue(), '90');
    await select.selectOption('');
    for (let i = 0; i < 40 && seen.patches.length < 2; i++) await page.waitForTimeout(50);
    assert.deepEqual(seen.patches[1], {retention_days: null});
    await done();

    // Calls off: nobody gets a control that cannot work, and no "Set up calls" note either.
    callsOff = true;
    await page.goto('https://tico-ui.test/#/thread/t-sub');
    await page.locator('.thv-title').waitFor();
    await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('.thv-call-note, [data-call]').count(), 0);
    assert.deepEqual(seen.errors, []);
    await page.close();
    const member = await open(browser, 'bob', {phone: true});
    await member.page.goto('https://tico-ui.test/#/thread/t-sub');
    await member.page.locator('.thv-title').waitFor();
    await member.page.waitForLoadState('networkidle');
    assert.equal(await member.page.locator('.thv-call-note, [data-call]').count(), 0);
    await member.page.close();
    dmRows = null; callsOff = false;
  }
  // ---- Live: the rail patches itself from the stream. An unread message counts on its row and joins Catch up, the
  // viewer's own read clears both, a pin moves a row (a thread not on the rail is read alone), and none of it reads the
  // whole rail again.
  {
    const sse = {queue: [], seq: 0, latest: null, done: false};
    const {page, seen} = await open(browser, 'ana', {sse});
    await page.goto('https://tico-ui.test/#/thread/t-plan');
    const rail = page.locator('#thread-rail');
    await rail.locator('.tr-row[data-thread="t0"]').waitFor();
    await page.waitForFunction(() => typeof LIVE !== 'undefined' && LIVE_TOPICS.includes('threads'));
    const rails = seen.rails;
    const badges = () => page.evaluate(() => ['#nav-threads .nav-badge', '#mobile-threads .nav-badge', '#thread-rail [data-tr-catch] .tr-badge']
      .map(q => { const b = document.querySelector(q); return b && !b.hidden ? b.textContent : ''; }));
    const row = id => page.evaluate(id => {
      const a = document.querySelector(`#thread-rail .tr-row[data-thread="${id}"]`);
      return a ? {list: a.closest('[data-list]')?.dataset.list, bold: getComputedStyle(a).fontWeight >= 600, n: a.querySelector('.tr-count')?.textContent || ''} : null;
    }, id);
    // Events come a stream at a time, after the page reconnects: wait up to 10 s for what one should do.
    const until = async (fn, want) => {
      for (let i = 0; i < 100; i++) { if (JSON.stringify(await fn()) === JSON.stringify(want)) return; await page.waitForTimeout(100); }
      assert.deepEqual(await fn(), want);
    };
    assert.deepEqual(await badges(), ['3', '3', '3']);
    // Bea's new message in Thread 0, sent twice (an edit sends its activity again): one more unread, once, and Catch up
    // takes the root. A bot's message in Thread 1 is activity only.
    const said = {kind: 'activity', thread_id: 't0', root_id: 't0', message_id: 'm-live-1', unread: true, actor: 'human:bea', counters: {last_seq: 9, last_unread_seq: 9}};
    sse.queue.push(said, said, {kind: 'activity', thread_id: 't1', root_id: 't1', message_id: 'm-live-2', unread: false, actor: 'bot:ops', counters: {last_seq: 9, last_unread_seq: 2}});
    await until(() => row('t0'), {list: 'threads', bold: true, n: '1'});
    // Three messages in one batch come as one event with `unread_count`: Thread 4 counts all three.
    sse.queue.push({kind: 'activity', thread_id: 't4', root_id: 't4', message_id: 'm-live-4', unread: true, unread_count: 3, actor: 'human:bea', counters: {last_seq: 12, last_unread_seq: 12}});
    await until(() => row('t4'), {list: 'threads', bold: true, n: '4'});
    assert.deepEqual(await badges(), ['4', '4', '4'], 'the same count in the sidebar, on the phone tab and on Catch up');
    await until(() => row('t1'), {list: 'threads', bold: true, n: ''});
    // A message in the open thread is read there: no count on its row.
    sse.queue.push({kind: 'activity', thread_id: 't-plan', root_id: 't-plan', message_id: 'm-live-3', unread: true, actor: 'human:bea', counters: {last_seq: 41, last_unread_seq: 41}});
    // Ana read Thread 11 on another device: its row is quiet and Catch up lets it go.
    sse.queue.push({kind: 'read', thread_id: 't11', root_id: 't11', read: {last_read_seq: 99}});
    await until(() => row('t11'), {list: 'threads', bold: false, n: ''});
    assert.deepEqual(await badges(), ['3', '3', '3']);
    assert.deepEqual(await row('t-plan'), {list: 'pinned', bold: false, n: ''});
    // Pins: Thread 3 moves to Pinned; the open house, not on the rail, is read alone and joins Pinned.
    sse.queue.push({kind: 'pinned', thread_id: 't3', root_id: 't3', pinned: true, pinned_for_everyone: false},
      {kind: 'pinned', thread_id: 't-open', root_id: 't-open', pinned: true, pinned_for_everyone: true});
    await until(() => row('t3'), {list: 'pinned', bold: false, n: ''});
    await until(async () => (await row('t-open'))?.list, 'pinned');
    assert.deepEqual(seen.threadGets, ['t-open'], 'only the thread not on the rail is read');
    assert.equal(seen.rails, rails, 'no event read the whole rail again');
    assert.deepEqual(seen.errors, []);
    sse.done = true;
    await page.close();
  }
  // ---- A server without the rail (GET /v2/threads/rail 404): its threads and group chats by title, no pins or Catch up.
  {
    railOff = true;
    const {page, seen} = await open(browser, 'ana');
    await page.goto('https://tico-ui.test/#/threads');
    await page.locator('#thread-rail [data-list="threads"] .tr-row[data-thread="t11"]').waitFor();
    const rows = await page.locator('#thread-rail [data-list="threads"] .tr-row').evaluateAll(els => els.map(a => a.dataset.thread));
    assert.ok(rows.includes('t0') && !rows.includes('t-dm') && !rows.includes('t-botdm'), 'threads, not 1:1 DMs: ' + rows);
    assert.equal(await page.locator('#thread-rail [data-tr-catch]').isVisible(), false);
    assert.equal(await page.locator('#thread-rail [data-tr-browse]').isVisible(), false);
    assert.equal(await page.locator('#nav-threads .nav-badge').isVisible(), false);
    // The DM counts still come from the list: Ops's from bot_dms, Bea's from her DM.
    assert.equal(await page.locator('#tree [data-org="b:ops"] .tree-dm').textContent(), '2');
    assert.equal(await page.locator('#tree [data-org="p:bea"] .tree-dm').textContent(), '1');
    await page.locator('.tr-empty [data-tr-new]').waitFor();    // nothing pinned, nothing opened before: New thread
    assert.deepEqual(seen.errors, []);
    await page.close();
    railOff = false;
  }
  // ---- Threads is in the sidebar and the phone bar from the first paint, before its read lands; only a server without
  // threads (its reads answer 404) takes it away.
  {
    for (const phone of [false, true]) {
      threadsGate = [];
      const {page} = await open(browser, 'ana', {phone});
      await page.goto('https://tico-ui.test/#/tasks');
      await page.locator(phone ? '#mobile-threads' : '#nav-threads').waitFor();
      assert.equal(await page.evaluate(() => THREADS.on), null, 'there before the first threads read lands');
      if (phone) assert.equal(await page.locator('#mobile-search').isVisible(), false, 'Threads holds the bar\'s place, not Search');
      const gate = threadsGate; threadsGate = null; gate.forEach(go => go());
      await page.waitForFunction(() => THREADS.on === true);
      await page.close();
    }
    // A 404, and a list read that answers something other than a list.
    for (const [off, phone] of [[true, false], [true, true], ['odd', false], ['odd', true]]) {
      threadsOff = off;
      const {page} = await open(browser, 'ana', {phone});
      await page.goto('https://tico-ui.test/#/tasks');
      await page.waitForFunction(() => THREADS.on === false);
      assert.equal(await page.locator(phone ? '#mobile-threads' : '#nav-threads').isVisible(), false, 'a server without threads has no link');
      if (phone) await page.locator('#mobile-search').waitFor();
      await page.close();
    }
    threadsOff = false;
  }
  {
    const {page} = await open(browser, 'bob', {phone: true});
    await page.goto('https://tico-ui.test/#/bot/ops');
    await page.locator('#btabs').waitFor();
    await page.waitForTimeout(300);
    await shot(page, 'bob-bot-phone');
    await page.close();
  }

  await browser.close();
  console.log('threads: ok');
})().catch(async error => {
  console.error(error);
  process.exitCode = 1;
  await browser?.close().catch(() => {});
});
