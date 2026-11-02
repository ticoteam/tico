// Live Meetings surfaces only server-persisted transcript, chat and router events; tests use fixtures.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();   // load.cjs arrives with #254

const makeDetail = id => ({id, title: 'Weekly sync', state: 'live', owner_actor: 'human:ana', seq: 2, event_id: 5,
  window_ms: 30000, cooldown_ms: 60000, reply_cap: 3, imported_meeting_id: null,
  humans: [{actor: 'human:ana', name: 'Ana'}], bots: [{bot: 'ops'}],
  chunks: [{seq: 1, revision: 1, speaker: 'Ana', text: 'We should ship the new plan.'},
    {seq: 2, revision: 2, speaker: 'Ben', text: 'Ops, can you review?', start_ms: 30000}],
  chat: [{id: 'c1', actor: 'human:ana', role: 'human', text: 'Welcome', transcript_seq: 1, created: '2026-10-30T10:00:01.000000Z'}],
  notices: [
    {event_id: 3, kind: 'waiting', bot: 'ops', reason: 'runner_update', detail: 'Test Mac needs a Tico update before it can join private meetings', created: '2026-10-30T10:00:00.000000Z'},
    {event_id: 4, kind: 'deferred', bot: 'ops', reason: 'busy', behind: null, created: '2026-10-30T10:00:02.000000Z'},
    {event_id: 5, kind: 'failed', bot: 'ops', reason: 'private_checkout', detail: "ops can't answer in this private meeting: Test Mac couldn't make a private copy of its repository", created: '2026-10-30T10:00:03.000000Z'}],
  router: {windows: [{window_index: 0, start_ms: 0, end_ms: 30000, outcome: 'pass', trace: {reason: 'below threshold', answers: {ops: 0.2}}}],
    turns: [{id: 't1', bot: 'ops', window_index: null, status: 'pending'}],
    bypasses: [{event_id: 6, bypass: 'named', source_key: 'chunk:2', targets: ['ops'], skipped: []}]}});

async function main() {
  const browser = await chromium.launch({headless: true,
    channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const context = await browser.newContext({viewport: {width: 1280, height: 900}, serviceWorkers: 'block'});
    await context.addInitScript(() => localStorage.setItem('tico.theme', 'dark'));
    const page = await context.newPage(), errors = [], calls = [];
    page.on('pageerror', error => errors.push(error.message));
    const sendChat = async () => {
      await page.waitForFunction(() => {
        const input = document.querySelector('#live-chat-form input[name="text"]');
        const button = input?.form?.querySelector('button[type="submit"]');
        return Boolean(input?.value.trim()) && Boolean(button && !button.disabled);
      });
      await page.locator('#live-chat-form button[type="submit"]').click();
    };
    // A send is over once its follow-up refresh has landed: until then Send stays disabled, and a detail read held now
    // would be that refresh's (a later read of the same path shares the request), not the next one's.
    const chatSettled = () => page.waitForFunction(() => !LIVE_MEETINGS.chatSending.size
      && document.querySelector('#live-chat-form button[type="submit"]')?.disabled === false);
    // A detail read later than `count` is painted: livePaintDetail runs in the same turn state.detail is set.
    const detailPaintedAfter = count => page.waitForFunction(n => LIVE_MEETINGS?.detail?.fixture_read > n, count, {timeout: t(10000)});
    // The chat box as it is on the page now, read in one step (a locator could hold the input a repaint just replaced).
    const chatBox = () => page.evaluate(() => { const input = document.querySelector('#live-chat-form input');
      return {value: input.value, start: input.selectionStart, end: input.selectionEnd, focused: document.activeElement === input}; });
    const signal = () => { let resolve; const promise = new Promise(done => resolve = done); return {promise, resolve}; };
    const world = {meetings: [], detail: null, detailReads: 0, detailHoldCount: 0, detailWaiters: [], detailHeld: null,
      holdChat: false, chatWaiters: [], chatHeld: null, failChat: false};
    await page.route('**/*', async route => {
      const url = new URL(route.request().url()), p = url.pathname, method = route.request().method();
      const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin !== 'https://tico-ui.test') return route.abort();
      const asset = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (asset && fs.existsSync(uiFile(asset[1]))) return route.fulfill({
        contentType: asset[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(asset[1]), 'utf8')});
      if (p === '/vendor/fonts/material-symbols-outlined.woff2') return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile('vendor/fonts/material-symbols-outlined.woff2'))});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'ben', name: 'Ben'}]});
      if (p === '/api/employees') return json([{name: 'ops', display_name: 'Operations'}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
      if (p === '/api/v2/tasks') return json({tasks: []});
      if (p === '/api/v2/setup/getting-started') return json({items: [], done: 0, total: 0, complete: true, dismissed: true, tour_seen: true, cards_dismissed: [],
        can_build: true, owner: true, empty: {docs: true, market: true, tasks: true, updates: true, goals: true, meetings: true}});
      if (p === '/api/v2/live-meetings/bot-candidates') return json({bots: [{slug: 'finance', name: 'Finance', team: 'Operations'}]});
      if (p === '/api/v2/live-meetings' && method === 'GET') return json({meetings: world.meetings, count: world.meetings.length});
      if (p === '/api/v2/live-meetings' && method === 'POST') {
        const body = JSON.parse(route.request().postData()); calls.push(['connect', body]);
        world.detail = makeDetail('live-1'); world.detail.title = body.title; world.meetings = [world.detail];
        return json(world.detail);
      }
      if (p === '/api/v2/live-meetings/live-1/events') return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p === '/api/v2/live-meetings/live-1' && method === 'GET') {
        if (world.detailHoldCount > 0) {
          world.detailHoldCount--;
          await new Promise(resolve => { world.detailWaiters.push(resolve); world.detailHeld?.resolve(); });
        }
        world.detailReads++;
        return route.fulfill({contentType: 'application/json', body: JSON.stringify({...world.detail, fixture_read: world.detailReads})});
      }
      if (p.startsWith('/api/v2/live-meetings/live-1/')) {
        const operation = p.split('/').pop(), body = JSON.parse(route.request().postData() || '{}'); calls.push([operation, body]);
        if (method === 'DELETE' && p.includes('/bots/')) {
          world.detail.bots = world.detail.bots.filter(bot => bot.bot !== operation);
          return json({id: 'live-1', bot: operation, removed: true, cancelled_turns: [], event_id: 10});
        }
        if (operation === 'chat' && world.holdChat) {
          await new Promise(resolve => { world.chatWaiters.push(resolve); world.chatHeld?.resolve(); });
        }
        if (operation === 'chat' && world.failChat) {
          world.failChat = false;
          return route.fulfill({status: 400, contentType: 'application/json', body: JSON.stringify({error: {detail: 'Chat rejected'}})});
        }
        if (operation === 'join') world.detail.humans.push({actor: 'human:ben', name: 'Ben'});
        if (operation === 'bots') world.detail.bots.push({bot: body.bots[0]});
        if (operation === 'control') world.detail.state = body.action === 'end' ? 'ended' : body.action === 'resume' ? 'live' : 'paused';
        if (operation === 'finalize') world.detail.imported_meeting_id = 'meeting-imported';
        if (operation === 'chat') world.detail.chat.push({actor: 'human:ana', role: 'human', text: body.text});
        return json(world.detail);
      }
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/meetings');
    assert.equal(await page.locator('#meet-recorder').getAttribute('href'), 'https://github.com/ticoteam/tico/releases/latest/download/Tico-Recorder.dmg');
    await page.locator('#meet-live-now').click();
    await page.locator('#live-connect').waitFor();
    await page.locator('#live-connect input[name=title]').fill('Weekly sync');
    await page.locator('#live-connect input[name=reply_cap]').fill('2');
    await page.locator('#live-connect input[name=cooldown_seconds]').fill('90');
    await page.locator('#live-connect button[type=submit]').click();
    await page.locator('.live-card').waitFor();
    assert.equal(await page.locator('.live-transcript').innerText().then(text => text.includes('We should ship')), true);
    assert.equal(await page.locator('.live-chat').innerText().then(text => text.includes('Welcome')), true);
    assert.equal(await page.locator('.live-router').innerText().then(text => text.includes('pass')), true);
    assert.equal(await page.locator('.live-chat').innerText().then(text => text.includes('below threshold')), false, 'PASS trace stays out of chat');
    assert.deepEqual(await page.locator('.live-chat > p').evaluateAll(rows => rows.map(row => row.dataset.liveNotice || 'chat')),
      ['waiting', 'chat', 'deferred', 'failed'], 'bot notices sit in the chat in time order');
    assert.equal(await page.locator('.live-chat [data-live-notice=failed]').getAttribute('class'), 'live-notice failed');
    assert.equal(await page.locator('.live-chat [data-live-notice=failed] span').innerText(), "Can't answer in this private meeting: Test Mac couldn't make a private copy of its repository", 'the name is not said twice');
    assert.equal(await page.locator('.live-chat [data-live-notice=deferred] span').innerText(), 'Busy. Will answer after its current work.');
    assert.equal(await page.locator('.live-router').innerText().then(text => text.includes('Named mentions')), true);
    await page.locator('.live-transcript-link').click();
    assert.equal(await page.evaluate(() => document.activeElement.id), 'live-chunk-1', 'chat reply links to its transcript sequence');
    assert.equal(calls[0][0], 'connect');
    assert.ok(calls[0][1].client_id);
    assert.equal(calls[0][1].reply_cap, 2);
    assert.equal(calls[0][1].cooldown_seconds, 90);
    world.detail.owner_actor = 'human:ben';
    await page.locator('[data-live-id=live-1]').click();
    await page.locator('[data-live-control=pause]').waitFor({state: 'detached'});
    await page.locator('#live-bot-picker').waitFor();
    assert.equal(await page.locator('[data-live-control=pause]').count(), 0, 'joined participant can attach without controlling the meeting');
    world.detail.owner_actor = 'human:ana';
    await page.locator('[data-live-id=live-1]').click();
    await page.locator('[data-live-control=pause]').waitFor();
    await page.locator('#live-bot-picker select option[value=finance]').waitFor();
    await page.locator('#live-bot-picker select').selectOption('finance');
    const draft = 'Please review this plan';
    await page.locator('#live-chat-form input').fill(draft);
    await page.locator('#live-chat-form input').evaluate(input => input.setSelectionRange(8, 14));
    // Trigger the same detail-refresh path used by the poll; check the box once that read is painted.
    const priorReads = world.detailReads;
    await page.evaluate(() => { void liveLoadDetail(LIVE_MEETINGS, 'live-1'); });
    await detailPaintedAfter(priorReads);
    const afterPoll = await chatBox();
    assert.deepEqual(afterPoll, {value: draft, start: 8, end: 14, focused: true});
    assert.deepEqual(await page.locator('#live-bot-picker select').evaluate(select =>
      [...select.selectedOptions].map(option => option.value)), ['finance']);
    const readsBeforeEvent = world.detailReads;
    await page.evaluate(() => LIVE_MEETINGS.sources.get('live-1').dispatchEvent(
      new MessageEvent('meeting.chat', {data: '{}', lastEventId: '8'})));
    await detailPaintedAfter(readsBeforeEvent);
    const afterEvent = await chatBox();
    assert.deepEqual(afterEvent, {value: draft, start: 8, end: 14, focused: true});
    assert.deepEqual(await page.locator('#live-bot-picker select').evaluate(select =>
      [...select.selectedOptions].map(option => option.value)), ['finance']);
    await sendChat();
    await page.locator('.live-status').getByText('Message sent.').waitFor();
    const chatCalls = calls.filter(([op]) => op === 'chat');
    assert.equal(chatCalls.length, 1);
    assert.equal(chatCalls[0][1].text, draft);
    assert.deepEqual(await page.locator('#live-bot-picker select').evaluate(select =>
      [...select.selectedOptions].map(option => option.value)), ['finance']);

    const padded = '  Please check the totals  ';
    await page.locator('#live-chat-form input').fill(padded);
    await sendChat();
    await page.locator('.live-status').getByText('Message sent.').waitFor();
    await page.waitForFunction(() => document.querySelector('#live-chat-form input')?.value === '');
    assert.equal(calls.filter(([op, body]) => op === 'chat' && body.text === 'Please check the totals').length, 1,
      'the submitted message is trimmed for the API');
    assert.equal(await page.evaluate(() => LIVE_MEETINGS.drafts['live-1']?.chatText), '',
      'successful whitespace-padded send clears its saved draft');
    await chatSettled();

    // A detail response already in flight must not replace a draft while chat is submitted.
    world.detailHoldCount = 1;
    world.detailHeld = signal();
    await page.evaluate(() => { window.liveDetailRefresh = liveLoad(LIVE_MEETINGS); });
    await world.detailHeld.promise;
    assert.equal(world.detailWaiters.length, 1, 'a detail request is held in flight');
    const chatInput = page.locator('#live-chat-form input');
    const sent = 'First message while refresh is waiting';
    const submitted = `  ${sent}  `;
    await chatInput.fill(submitted);
    world.holdChat = true;
    world.chatHeld = signal();
    await sendChat();
    await world.chatHeld.promise;
    assert.equal(world.chatWaiters.length, 1, 'chat request is held before acknowledgement');
    const newerDraft = 'Keep this newer unsent draft';
    await chatInput.fill(newerDraft);
    // The detail GET started before submission is already in flight; return it while chat is busy.
    // liveLoad intentionally suppresses new refreshes while a send is in flight.
    world.detailWaiters.shift()();
    await page.evaluate(() => window.liveDetailRefresh);
    assert.equal(await chatInput.inputValue(), newerDraft, 'stale detail response cannot replace text while sending');
    await page.locator('#live-chat-form').evaluate(form => form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true})));
    assert.equal(calls.filter(([op, body]) => op === 'chat' && body.text === sent).length, 1,
      'duplicate submit is suppressed and whitespace is trimmed');
    world.holdChat = false;
    world.detailHoldCount = 1;
    world.detailHeld = signal();
    world.chatWaiters.shift()();
    await page.locator('.live-status').getByText('Message sent.').waitFor();
    await page.waitForFunction(value => document.querySelector('#live-chat-form input')?.value === value, newerDraft);
    await world.detailHeld.promise;
    assert.equal(world.detailWaiters.length, 1, 'post-send refresh is held until the newer draft is saved');
    world.detailWaiters.shift()();
    await page.waitForFunction(value => document.querySelector('#live-chat-form input')?.value === value, newerDraft);
    world.failChat = true;
    await chatInput.fill('Retain this rejected message');
    await sendChat();
    await page.locator('.live-status').getByText('Chat rejected').waitFor();
    assert.equal(await chatInput.inputValue(), 'Retain this rejected message', 'failed send keeps the draft');

    await page.locator('#live-bot-picker button').click();
    await page.locator('[data-live-bot-remove="finance"]').waitFor();
    assert(calls.some(([op, body]) => op === 'bots' && body.bots.includes('finance')));
    await page.locator('[data-live-bot-remove="finance"]').click();
    await page.locator('[data-live-bot-remove="finance"]').waitFor({state: 'detached'});
    assert(calls.some(([op]) => op === 'finance'), 'a joined person can revoke meeting-only bot access');
    await page.locator('[data-live-control=pause]').click();
    await page.locator('[data-live-control=resume]').waitFor();
    assert(calls.some(([op, body]) => op === 'control' && body.action === 'pause'));
    await page.locator('[data-live-control=resume]').click();
    // End shows while paused too: click it once the resumed meeting is painted, or the resume still in flight drops it.
    await page.locator('[data-live-control=pause]').waitFor();
    await page.locator('[data-live-control=end]').click();
    await page.locator('[data-live-finalize]').click();
    await page.locator('.live-finalized').waitFor();
    assert(calls.some(([op]) => op === 'finalize'));
    assert.doesNotMatch(await page.locator('.live-card-head').innerText(), /From Tico Recorder/);
    world.detail.client = 'tico-recorder';
    // The stable Open in Tico link selects even an ended meeting absent from the list.
    world.meetings = [];
    await page.goto('https://tico-ui.test/#/meetings?live=live-1');
    await page.locator('.live-card').waitFor();
    assert.match(await page.locator('.live-card-head').innerText(), /From Tico Recorder/);
    await page.locator('#live-back').click();
    await page.locator('#meet-live-now').waitFor();
    assert.deepEqual(errors, []);
    await context.close();
    await require('./support/recorder-pause.cjs')(browser);
  } finally { await browser.close(); }
}

main().catch(error => { console.error(error); process.exit(1); });
