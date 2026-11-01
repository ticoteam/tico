// Threads per role, at desktop and phone width (docs/dev/threads-contract.md, plan §9 "UI per role"): the sidebar's
// order and cap, a thread with its sub-threads inline, tapping into a sub-thread and back by the breadcrumb, a reply
// shown as "Checking…" until the server answers, a held message, the bot page's Home and DM with the filtered link,
// a person's Home with an update card, New DM, and a teammate who may see a bot but not read it getting no Home.
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

// Fourteen threads: one needs attention, two are unread, the rest are older. The sidebar shows ten and then All.
const listed = Array.from({length: 13}, (_, i) => thread('t' + i, `Thread ${i}`, i + 1));
listed[11].me = me(3); listed[4].me = me(1);
listed[9].me = {...me(), activity: true};
const plan = thread('t-plan', 'Launch plan', 40, {me: me(0, {can_manage: true})});
const attention = [plan];
const dm = {id: 't-dm', title: '', audience: 'personal', last_seq: 5, last_unread_seq: 5, last_message_at: at(3), me: me(1),
  members: [{actor: 'human:ana'}, {actor: 'human:bea'}]};

const msg = (id, seq, from, body, extra = {}) => ({id, seq, thread_id: 't-plan', from_actor: from, body, created: at(60 - seq), depth: 0, ...extra});
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
  'h-bea': () => ({thread: {id: 'h-bea', audience: 'home', home: {actor: 'human:bea'}, last_seq: 1, me: me(0, {can_post: false})}, breadcrumb: [], next_cursor: null, messages: [
    {...msg('b1', 1, 'human:bea', 'Daily update, Oct 31', {kind: 'notice', refs: {card: 'update', update: {id: 'u2', kind: 'daily', day: '2026-10-31'}}}), thread_id: 'h-bea'},
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

async function open(browser, who, {phone = false} = {}) {
  const page = await browser.newPage({viewport: phone ? {width: 390, height: 844} : {width: 1300, height: 860}, serviceWorkers: 'block', reducedMotion: 'reduce',
    ...(phone ? {isMobile: true, hasTouch: true} : {})});
  const seen = {errors: [], posts: [], reads: [], readSeqs: [], homes: [], steps: [], tasks: [], calls: [], catchUps: [], follows: [], earlier: [], withdrawn: []};
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
          this.localParticipant = {setMicrophoneEnabled: async on => { room.mic = on; }, setScreenShareEnabled: async () => {}}; window.__rooms.push(this); }
        on() { return this; }
        async connect(url, token) { this.token = token; await new Promise(r => setTimeout(r, 200)); }
        disconnect() { this.gone = true; }
      },
    };
  });
  const bob = who === 'bob';
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
    if (p === '/api/v2/events') return route.abort();
    if (p === '/api/v2/threads' && method === 'GET') return json({attention: bob ? [] : attention, threads: bob ? [] : listed, dms: bob ? [] : [dm], next_cursor: null});
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
    const one = p.match(/^\/api\/v2\/threads\/([^/]+)\/read$/);
    if (one) { seen.reads.push(one[1]); seen.readSeqs.push(one[1] + ':' + JSON.parse(req.postData() || '{}').seq); return json({ok: true}); }
    if (/^\/api\/v2\/threads\/[^/]+\/calls$/.test(p)) return json({calls: [], next_before: null});
    const send = p.match(/^\/api\/v2\/threads\/([^/]+)\/messages$/);
    if (send && method === 'POST') {
      const body = JSON.parse(req.postData() || '{}'); seen.posts.push(body);
      if (/secret/i.test(body.text)) return json({held: {id: 'hold1', outcome: 'blocked', rules: [{id: 'r1', text: 'No secrets in threads'}]}});
      await new Promise(r => setTimeout(r, 600));     // long enough to see "Checking…"
      return json({message: msg('n' + seen.posts.length, 9 + seen.posts.length, 'human:ana', body.text, {thread_id: send[1], parent_message_id: body.parent_message_id || null})});
    }
    if (p === '/api/v2/rule-holds/hold1') return json({id: 'hold1', can_send: false});
    if (p === '/api/v2/rule-holds/hold2') return json({id: 'hold2', operation: 'threads_edit', message_id: 'ed1', can_send: false});
    if (p === '/api/v2/rule-holds/hold2/withdraw') { seen.withdrawn.push('hold2'); return json({ok: true}); }
    const edit = p.match(/^\/api\/v2\/messages\/([^/]+)$/);
    if (edit && method === 'PATCH') {
      const body = JSON.parse(req.postData() || '{}');
      if (/secret/i.test(body.text)) return json({held: {id: 'hold2', outcome: 'blocked', rules: [{id: 'r1', text: 'No secrets in threads'}]}});
      return json({...msg(edit[1], 1, 'human:ana', body.text), thread_id: 't-edit', edited_at: at(0)});
    }
    const asked = p.match(/^\/api\/v2\/threads\/([^/]+)\/(catch-up|follow|open-earlier)$/);
    if (asked) {
      if (asked[2] === 'catch-up') { seen.catchUps.push({thread: asked[1], ...JSON.parse(req.postData() || '{}')}); return json({thread_id: asked[1], messages: 2, posted: {id: 'cu1'}}); }
      if (asked[2] === 'follow') { const body = JSON.parse(req.postData() || '{}'); seen.follows.push(body.notify); return json({notify: body.notify}); }
      seen.earlier.push(asked[1]); return json({home: 'bot:ops', messages: 2});
    }
    const react = p.match(/^\/api\/v2\/messages\/([^/]+)\/reactions\/(.+)$/);
    // The server answers a reaction with the message's reactions, a bare list.
    if (react) return json(method === 'PUT' ? [{emoji: decodeURIComponent(react[2]), count: 1, actors: ['human:ana']}] : []);
    const task = p.match(/^\/api\/v2\/threads\/messages\/([^/]+)\/task$/);
    if (task) { seen.tasks.push({message: task[1], ...JSON.parse(req.postData() || '{}')}); return json({id: 'task1', private: true}); }
    if (p === '/api/v2/tasks' && method === 'POST') { seen.tasks.push({copied: true}); return json({id: 'task-copy'}); }
    if (p === '/api/v2/threads/search') return json({hits: url.searchParams.get('q') === 'price' ? [{message_id: 'm2', thread_id: 't-plan', title: 'Launch plan',
      from_actor: 'human:ana', created: at(58), excerpt: 'What price do we open with?'}] : [], next_cursor: null});
    const call = p.match(/^\/api\/v2\/calls\/([^/]+)\/(join|leave|end|token)$/);
    if (call) {
      seen.calls.push(call[2] + ':' + call[1]);
      if (call[2] === 'join') { await new Promise(r => setTimeout(r, 150)); return json({token: 'tok-' + call[1], url: 'wss://meet.acme.example', call: {id: call[1], connector: 'human:ana'}}); }
      if (call[2] === 'end') return json({error: {code: 'internal', detail: 'Could not end'}}, 500);
      return json({ok: true});
    }
    const steps = p.match(/^\/api\/v2\/turns\/([^/]+)\/steps$/);
    if (steps) { seen.steps.push(steps[1]); return json({steps: [{kind: 'tool', tool: 'search', text: 'pricing notes', at: at(57)}]}); }
    return json(unrouted(p));
  });
  return {page, seen};
}
async function shot(page, name) {
  if (!shots) return;
  fs.mkdirSync(shots, {recursive: true});
  await page.screenshot({path: path.join(shots, `threads-${name}.png`)});
}

(async () => {
  const browser = await chromium.launch();

  // ---- Ana at desktop width
  {
    const {page, seen} = await open(browser, 'ana');
    await page.goto('https://tico-ui.test/#/tasks');
    await page.locator('#th-list .th-row').first().waitFor();
    const rows = await page.locator('#th-list .th-row').evaluateAll(els => els.map(a => ({title: a.querySelector('.th-row-title').textContent, attn: a.classList.contains('attn'), unread: a.classList.contains('unread')})));
    assert.equal(rows[0].title, 'Launch plan', 'Needs attention comes first');
    assert.ok(rows[0].attn);
    assert.deepEqual(rows.slice(1, 3).map(r => r.unread), [true, true], 'then the unread threads');
    assert.equal(rows.length, 11, 'ten rows, then All');
    assert.equal(rows.at(-1).title, 'All');
    assert.equal(await page.locator('#th-list .th-activity').count(), 1, 'activity is a dot, not a count');
    assert.equal(await page.locator('#dm-list .th-row').count(), 1);
    assert.match(await page.locator('#dm-list .th-row-title').textContent(), /Bea/);
    await shot(page, 'sidebar-desktop');

    // One thread: its sub-threads inline under their message, indented, deepest capped.
    await page.goto('https://tico-ui.test/#/thread/t-plan');
    await page.getByText('And churn was low.').waitFor();
    const order = await page.locator('.thv-list .th-msg .th-body').allTextContents();
    assert.deepEqual(order.slice(0, 5).map(s => s.trim()), ['Kickoff is Monday.', 'What price do we open with?', 'Last quarter we opened at 40.', 'And churn was low.', 'Then 45 this time.'], 'tree order');
    assert.equal(await page.locator('.th-msg.th-sub[data-mid="s1a"]').getAttribute('style'), '--d:2');
    assert.equal((await page.locator('.th-notshown[data-mid="h1"]').textContent()).trim(), 'Not shown', 'a message the viewer may not read is one quiet row');
    // Each sub-thread opens with one header row that links to it; its replies follow, indented.
    assert.deepEqual(await page.locator('.th-subhead').evaluateAll(els => els.map(a => a.getAttribute('href'))), ['#/thread/t-sub', '#/thread/t-sub2']);
    assert.equal(await page.locator('.th-msg[data-mid="m2"] + .th-subhead + .th-msg[data-mid="s1"]').count(), 1, 'the header sits between the message and its replies');
    // A title only if it was given one: otherwise the row doesn't repeat the line above it.
    assert.deepEqual(await page.locator('.th-subhead-title').allTextContents(), ['Pricing']);
    assert.match(await page.locator('.th-msg[data-mid="s1"] + .th-subhead').innerText(), /^1 reply$/);
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

    // Reply in sub-thread: one inline box under the message's sub-thread.
    await page.locator('.th-msg[data-mid="m1"]').hover();
    await page.locator('.th-msg[data-mid="m1"] [data-th-more]').click();
    await page.locator('.thv-pop').getByText('Reply in sub-thread').click();
    await page.locator('.th-reply-text').fill('Monday at ten.');
    await page.locator('[data-th-reply-send]').click();
    await page.waitForFunction(() => document.querySelectorAll('.th-reply').length === 0);
    assert.equal(seen.posts.at(-1).parent_message_id, 'm1');

    // Acknowledge: mine comes from the reactors the server lists, and its bare-list answer settles the row.
    await page.locator('.th-msg[data-mid="m1"]').hover();
    await page.locator('.th-msg[data-mid="m1"] [data-th-ack]').click();
    await page.locator('.th-msg[data-mid="m1"] .th-react.me[aria-pressed="true"]').waitFor();
    await page.locator('.th-msg[data-mid="m1"] .th-react').click();
    await page.waitForFunction(() => !document.querySelector('.th-msg[data-mid="m1"] .th-react'));

    // Make task: the server makes it from the message; the page never copies the words into a task of its own.
    await page.locator('.th-msg[data-mid="m1"]').hover();
    await page.locator('.th-msg[data-mid="m1"] [data-th-more]').click();
    await page.locator('.thv-pop').getByText('Make task').click();
    await page.locator('#thread-task button[type="submit"]').click();
    await page.waitForFunction(() => !document.querySelector('#thread-task[open]'));
    assert.deepEqual(seen.tasks.map(t => t.message), ['m1']);
    assert.ok(!seen.tasks.some(t => t.copied), 'no task made from copied text');
    await page.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));

    // A late page from the server keeps a newer message the stream already showed.
    await page.evaluate(() => threadViewsLive('messages', {id: 'live1', thread_id: 't-plan', root_id: 't-plan',
      message: {id: 'live1', seq: 60, revision: 1, thread_id: 't-plan', from_actor: 'human:bea', body: 'Arrived live.', created: new Date().toISOString()}}));
    await page.getByText('Arrived live.').waitFor();
    await page.evaluate(() => thvRefresh([...THV_VIEWS].find(v => v.id === 't-plan')));
    assert.equal(await page.locator('.th-msg[data-mid="live1"]').count(), 1, 'the refresh did not wipe the live message');

    // A whole tree inline: a long branch keeps its latest messages under "N more", and what is drawn is what is read.
    await page.goto('https://tico-ui.test/#/thread/t-tree');
    await page.getByText('Unread inline.').waitFor();
    await page.waitForFunction(() => document.querySelector('.th-msg[data-mid="n1"]'));
    const mids = () => page.locator('.thv-list [data-mid]').evaluateAll(els => els.map(e => e.dataset.mid).filter(id => !/^f\d/.test(id)));
    assert.deepEqual(await mids(), ['p1', 'r1', 'p2', 'more:t-lb', 'b3', 'b4', 'p3', 'n1']);
    assert.match(await page.locator('.th-more').textContent(), /2 more.*new/);
    assert.equal((await page.locator('.th-msg[data-mid="p3"] + .th-subhead').innerText()).replace(/\s+/g, ' '), '1 reply · 1 new');
    await page.waitForTimeout(150);
    assert.ok(seen.readSeqs.includes('t-nb:24'), 'a sub-thread drawn inline is read: ' + seen.readSeqs);
    assert.ok(seen.readSeqs.includes('t-tree:59') && !seen.reads.includes('t-rb'), 'read as far as drawn; nothing new, nothing sent: ' + seen.readSeqs);
    assert.ok(!seen.reads.includes('t-lb'), 'a collapsed part with news stays unread: ' + seen.readSeqs);
    assert.equal(await page.locator('.th-msg[data-mid="n1"].th-new').count(), 1, 'the mark is its own thread\'s, not the parent\'s');
    assert.equal(await page.locator('.th-msg[data-mid="r1"].th-new').count(), 0);
    await page.evaluate(() => document.querySelector('.th-msg[data-mid="p1"]').scrollIntoView());
    await shot(page, 'subthreads-desktop');
    await page.locator('.th-more').click();
    await page.getByText('Long one.', {exact: true}).waitFor();
    assert.deepEqual((await mids()).slice(2, 7), ['p2', 'b1', 'b2', 'b3', 'b4'], 'the older part takes the row\'s place');
    await page.waitForTimeout(150);
    assert.ok(seen.readSeqs.includes('t-lb:22'), 'opened, it reads to the last message: ' + seen.readSeqs);
    // A reply in an old sub-thread lands under its parent, new, and Unread goes there.
    await page.evaluate(() => { const b = document.querySelector('.thv-scroll'); b.scrollTop = b.scrollHeight; });
    await page.evaluate(() => threadViewsLive('messages', {id: 'lv', thread_id: 't-rb', root_id: 't-tree',
      message: {id: 'lv', seq: 70, revision: 0, thread_id: 't-rb', from_actor: 'human:bea', body: 'Late reply.', created: new Date().toISOString()}}));
    assert.deepEqual((await mids()).slice(0, 3), ['p1', 'r1', 'lv']);
    assert.equal(await page.locator('.th-msg[data-mid="lv"].th-new').count(), 1);
    assert.match(await page.locator('.th-msg[data-mid="p1"] + .th-subhead').textContent(), /2 replies/);
    await page.locator('.thv-unread').click();
    await page.waitForFunction(() => { const b = document.querySelector('.thv-scroll').getBoundingClientRect(), r = document.querySelector('[data-mid="lv"]').getBoundingClientRect(); return r.top >= b.top && r.bottom <= b.bottom; });
    await page.waitForTimeout(100);
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

    // Tapping a sub-thread message opens the sub-thread, with the way back.
    await page.locator('.th-msg[data-mid="s2"] .th-body').click();
    await page.waitForFunction(() => location.hash === '#/thread/t-sub');
    await page.locator('.thv-crumbs a', {hasText: 'Launch plan'}).waitFor();
    // Opened, it shows its own sub-threads the same way, one step in from itself.
    assert.equal(await page.locator('.th-msg.th-sub[data-mid="s1a"]').getAttribute('style'), '--d:1');
    assert.equal(await page.locator('.th-msg[data-mid="s1"] + .th-subhead + .th-msg[data-mid="s1a"]').count(), 1);
    await page.locator('.thv-back').click();
    await page.waitForFunction(() => location.hash === '#/thread/t-plan');

    // The bot page: Home and DM, and a quiet link to every thread with the bot.
    await page.goto('https://tico-ui.test/#/bot/ops');
    await page.getByText('Weekly numbers are in the doc.').waitFor();
    const tabs = await page.locator('#btabs [data-bt]').evaluateAll(els => els.map(b => b.dataset.bt));
    assert.ok(tabs.includes('home') && tabs.includes('dm'), 'Home and DM: ' + tabs);
    assert.ok(!tabs.includes('chat'));
    assert.equal(await page.locator('.bot-threads-link').getAttribute('href'), '#/threads?with=bot%3Aops');
    await shot(page, 'bot-home-desktop');
    // The Home's actions (the call and the menu) sit in the Home/DM bar; the thread has no title line.
    await page.locator('#pane-chat .bot-talk-acts [data-thv-menu]').waitFor();
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

    // A person's Home under their profile, with the update card.
    await page.goto('https://tico-ui.test/#/person/bea');
    await page.locator('#person-home .th-update').waitFor();
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

    // A held edit: the message keeps its words, the held text waits under it, and Edit takes it back into the box.
    await page.goto('https://tico-ui.test/#/thread/t-edit');
    const body = page.locator('.th-msg[data-mid="ed1"] .th-body');
    await body.getByText('Open at 40.').waitFor();
    const editWith = async text => {
      await page.locator('.th-msg[data-mid="ed1"]').hover();
      await page.locator('.th-msg[data-mid="ed1"] [data-th-more]').click();
      await page.locator('.thv-pop').getByText('Edit', {exact: true}).click();
      await page.locator('.th-edit-text').fill(text);
      await page.locator('[data-th-edit-save]').click();
    };
    await editWith('Open at the secret price.');
    await page.locator('.th-held').getByText('No secrets in threads').waitFor();
    assert.equal((await body.textContent()).trim(), 'Open at 40.', 'a held edit changes nothing yet');
    await shot(page, 'held-edit-desktop');
    await page.locator('.th-held [data-held-edit]').click();
    assert.equal(await page.locator('.th-edit-text').inputValue(), 'Open at the secret price.');
    await page.waitForTimeout(100);
    assert.deepEqual(seen.withdrawn, ['hold2'], 'editing it takes the held one back');
    await page.locator('.th-edit-text').fill('Open at 45.');
    await page.locator('[data-th-edit-save]').click();
    await body.getByText('Open at 45.').waitFor();
    assert.equal(await page.locator('.th-held').count(), 0);
    await editWith('Another secret.');
    await page.locator('.th-held [data-held-drop]').click();
    await page.waitForFunction(() => !document.querySelector('.th-held'));
    await page.waitForTimeout(100);
    assert.equal((await body.textContent()).trim(), 'Open at 45.', 'Discard leaves the message alone');
    assert.equal(seen.withdrawn.length, 2);

    // The thread menu: Catch up, and one reader's notification setting.
    const pick = async (...labels) => {
      await page.locator('[data-thv-menu]').click();
      for (const label of labels) await page.locator('.thv-pop').getByText(label, {exact: true}).click();
    };
    await pick('Catch up');
    await page.locator('.toast', {hasText: 'Catch-up ready'}).waitFor();
    assert.deepEqual(seen.catchUps, [{thread: 't-edit', scope: 'subtree'}]);
    await pick('Notifications', 'Digest');
    await page.waitForTimeout(100);
    assert.deepEqual(seen.follows, ['digest']);
    await page.locator('[data-thv-menu]').click();
    await page.locator('.thv-pop').getByText('Notifications', {exact: true}).click();
    assert.equal(await page.locator('.thv-pop [aria-checked="true"]').textContent(), 'Digest', 'the menu shows the current setting');
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

    // A team the viewer has Write on can join a thread; a DM stays people and bots.
    await page.goto('https://tico-ui.test/#/threads');
    await page.locator('#th-new').click();
    await page.locator('#thread-new .thd-pick').first().waitFor();
    assert.equal(await page.locator('#thread-new input[value="group:ops"]').count(), 1);
    assert.equal(await page.locator('#thread-new input[value="group:legal"]').count(), 0, 'no Write, not offered');
    await page.keyboard.press('Escape');

    // Search in Threads: hits from the threads search, each opening its thread at the message.
    await page.goto('https://tico-ui.test/#/threads');
    await page.locator('#th-search').click();
    await page.locator('#thp-q').fill('price');
    await page.locator('.thp-hit').first().click();
    await page.waitForFunction(() => location.hash === '#/thread/t-plan?m=m2');
    await page.locator('.th-msg.th-focus[data-mid="m2"]').waitFor();

    // New DM: a picker of teammates.
    await page.goto('https://tico-ui.test/#/threads');
    await page.locator('#dm-new').click();
    await page.locator('#thread-new .thd-pick').first().waitFor();
    const picks = await page.locator('#thread-new .thd-pick span').allTextContents();
    assert.ok(picks.includes('Bea') && picks.includes('Ops') && !picks.includes('Ana'), 'teammates, not yourself: ' + picks);
    assert.equal(await page.locator('#thread-new input[value^="group:"]').count(), 0);
    await shot(page, 'new-dm-desktop');
    await page.keyboard.press('Escape');

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
    assert.equal(await page.locator('#mobile-threads .nav-badge').textContent(), '5', 'the tab carries the unread total');
    // Threads takes Search's place in the bar, so search over everything is in the drawer.
    assert.equal(await page.locator('#mobile-search').isVisible(), false);
    await page.locator('#mobile-more').click();
    await page.locator('#search-open').waitFor();
    await page.waitForTimeout(300);     // the drawer slides in
    await shot(page, 'drawer-phone');
    await page.locator('#search-open').click();
    await page.locator('#search-modal[open]').waitFor();
    assert.equal(await page.evaluate(() => document.body.classList.contains('drawer')), false, 'the drawer closes behind it');
    await page.keyboard.press('Escape');
    await page.locator('#search-modal[open]').waitFor({state: 'detached'});
    await page.locator('#mobile-threads').click();
    await page.waitForFunction(() => location.hash === '#/threads');
    await page.locator('.threads-page .th-row').first().waitFor();
    await shot(page, 'sidebar-phone');
    await page.goto('https://tico-ui.test/#/thread/t-plan');
    await page.getByText('And churn was low.').waitFor();
    // On a phone the tree reads the same, indented less, with no sideways scroll.
    assert.equal(await page.locator('.th-msg[data-mid="s1a"]').isVisible(), true);
    // On a phone each message shows only ⋯; Acknowledge is in its menu.
    assert.equal(await page.locator('.th-msg[data-mid="m1"] [data-th-ack]').isVisible(), false);
    assert.equal(await page.locator('.th-msg[data-mid="m1"] [data-th-more]').isVisible(), true);
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
    await page.locator('#person-home .th-update').waitFor();
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
    await page.goto('https://tico-ui.test/#/threads?view=dms');
    await page.locator('[data-thp-new]').click();
    await page.locator('#thread-new .thd-pick').first().waitFor();
    await shot(page, 'new-dm-phone');
    assert.deepEqual(seen.errors, []);
    await page.close();
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
})().catch(error => { console.error(error); process.exitCode = 1; });
