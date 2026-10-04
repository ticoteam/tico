// Live Meetings surfaces only server-persisted transcript, chat and router events; tests use fixtures.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');

const makeDetail = id => ({id, title: 'Weekly sync', state: 'live', owner_actor: 'human:ana', seq: 2, event_id: 5,
  window_ms: 30000, cooldown_ms: 60000, reply_cap: 3, imported_meeting_id: null,
  humans: [{actor: 'human:ana', name: 'Ana'}], bots: [{bot: 'ops'}],
  chunks: [{seq: 1, revision: 1, speaker: 'Ana', text: 'We should ship the new plan.'},
    {seq: 2, revision: 2, speaker: 'Ben', text: 'Ops, can you review?', start_ms: 30000}],
  chat: [{id: 'c1', actor: 'human:ana', role: 'human', text: 'Welcome', transcript_seq: 1}],
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
    const world = {meetings: [], detail: null, holdDetail: false, detailWaiters: [],
      holdChat: false, chatWaiters: [], failChat: false};
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
        if (world.holdDetail) await new Promise(resolve => world.detailWaiters.push(resolve));
        return json(world.detail);
      }
      if (p.startsWith('/api/v2/live-meetings/live-1/')) {
        const operation = p.split('/').pop(), body = JSON.parse(route.request().postData() || '{}'); calls.push([operation, body]);
        if (method === 'DELETE' && p.includes('/bots/')) {
          world.detail.bots = world.detail.bots.filter(bot => bot.bot !== operation);
          return json({id: 'live-1', bot: operation, removed: true, cancelled_turns: [], event_id: 10});
        }
        if (operation === 'chat' && world.holdChat) await new Promise(resolve => world.chatWaiters.push(resolve));
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
      return json({});
    });
    await page.goto('https://tico-ui.test/#/meetings');
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
    assert.equal(await page.locator('.live-router').innerText().then(text => text.includes('Named mentions')), true);
    await page.locator('.live-transcript-link').click();
    assert.equal(await page.evaluate(() => document.activeElement.id), 'live-chunk-1', 'chat reply links to its transcript sequence');
    assert.equal(calls[0][0], 'connect');
    assert.ok(calls[0][1].client_id);
    assert.equal(calls[0][1].reply_cap, 2);
    assert.equal(calls[0][1].cooldown_seconds, 90);
    world.detail.owner_actor = 'human:ben';
    await page.locator('[data-live-id=live-1]').click();
    await page.locator('#live-bot-picker').waitFor();
    assert.equal(await page.locator('[data-live-control=pause]').count(), 0, 'joined participant can attach without controlling the meeting');
    world.detail.owner_actor = 'human:ana';
    await page.locator('[data-live-id=live-1]').click();
    await page.locator('#live-bot-picker select option[value=finance]').waitFor();
    await page.locator('#live-bot-picker select').selectOption('finance');
    const draft = 'Please review this plan';
    await page.locator('#live-chat-form input').fill(draft);
    await page.locator('#live-chat-form input').evaluate(input => input.setSelectionRange(8, 14));
    // Exercise the real five-second poll and an incoming meeting event while both controls hold drafts.
    await page.waitForTimeout(5200);
    const afterPoll = await page.locator('#live-chat-form input').evaluate(input => ({value: input.value,
      start: input.selectionStart, end: input.selectionEnd, focused: document.activeElement === input}));
    assert.deepEqual(afterPoll, {value: draft, start: 8, end: 14, focused: true});
    assert.deepEqual(await page.locator('#live-bot-picker select').evaluate(select =>
      [...select.selectedOptions].map(option => option.value)), ['finance']);
    await page.evaluate(() => LIVE_MEETINGS.sources.get('live-1').dispatchEvent(
      new MessageEvent('meeting.chat', {data: '{}', lastEventId: '8'})));
    await page.waitForTimeout(400);
    const afterEvent = await page.locator('#live-chat-form input').evaluate(input => ({value: input.value,
      start: input.selectionStart, end: input.selectionEnd, focused: document.activeElement === input}));
    assert.deepEqual(afterEvent, {value: draft, start: 8, end: 14, focused: true});
    assert.deepEqual(await page.locator('#live-bot-picker select').evaluate(select =>
      [...select.selectedOptions].map(option => option.value)), ['finance']);
    await page.locator('#live-chat-form button').click();
    const chatCalls = calls.filter(([op]) => op === 'chat');
    assert.equal(chatCalls.length, 1);
    assert.equal(chatCalls[0][1].text, draft);
    assert.deepEqual(await page.locator('#live-bot-picker select').evaluate(select =>
      [...select.selectedOptions].map(option => option.value)), ['finance']);

    // A detail response already in flight must not replace a draft while chat is submitted.
    world.holdDetail = true;
    await page.evaluate(() => { window.liveDetailRefresh = liveLoad(LIVE_MEETINGS); });
    await page.waitForFunction(() => true); // yield once so the mocked route can hold its detail response
    for (let tries = 0; !world.detailWaiters.length && tries < 30; tries++) await page.waitForTimeout(10);
    assert.equal(world.detailWaiters.length, 1, 'a detail request is held in flight');
    const chatInput = page.locator('#live-chat-form input');
    const sent = 'First message while refresh is waiting';
    await chatInput.fill(sent);
    world.holdChat = true;
    await page.locator('#live-chat-form button').click();
    for (let tries = 0; !world.chatWaiters.length && tries < 30; tries++) await page.waitForTimeout(10);
    assert.equal(world.chatWaiters.length, 1, 'chat request is held before acknowledgement');
    const newerDraft = 'Keep this newer unsent draft';
    await chatInput.fill(newerDraft);
    await page.evaluate(() => LIVE_MEETINGS.sources.get('live-1').dispatchEvent(
      new MessageEvent('meeting.chat', {data: '{}', lastEventId: '9'})));
    world.detailWaiters.shift()();
    await page.evaluate(() => window.liveDetailRefresh);
    assert.equal(await chatInput.inputValue(), newerDraft, 'stale detail response cannot replace text while sending');
    await page.locator('#live-chat-form').evaluate(form => form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true})));
    assert.equal(calls.filter(([op, body]) => op === 'chat' && body.text === sent).length, 1, 'duplicate submit is suppressed');
    world.holdChat = false;
    world.chatWaiters.shift()();
    await page.locator('.live-status').getByText('Message sent.').waitFor();
    await page.waitForFunction(value => document.querySelector('#live-chat-form input')?.value === value, newerDraft);
    for (let tries = 0; !world.detailWaiters.length && tries < 30; tries++) await page.waitForTimeout(10);
    assert.equal(world.detailWaiters.length, 1, 'post-send refresh is held until the newer draft is saved');
    world.detailWaiters.shift()();
    await page.waitForFunction(value => document.querySelector('#live-chat-form input')?.value === value, newerDraft);
    world.failChat = true;
    await chatInput.fill('Retain this rejected message');
    await page.locator('#live-chat-form button').click();
    await page.locator('.live-status').getByText('Chat rejected').waitFor();
    assert.equal(await chatInput.inputValue(), 'Retain this rejected message', 'failed send keeps the draft');
    world.holdDetail = false;

    await page.locator('#live-bot-picker button').click();
    assert(calls.some(([op, body]) => op === 'bots' && body.bots.includes('finance')));
    await page.locator('[data-live-bot-remove="finance"]').click();
    await page.locator('[data-live-bot-remove="finance"]').waitFor({state: 'detached'});
    assert(calls.some(([op]) => op === 'finance'), 'a joined person can revoke meeting-only bot access');
    await page.locator('[data-live-control=pause]').click();
    assert(calls.some(([op, body]) => op === 'control' && body.action === 'pause'));
    await page.locator('[data-live-control=resume]').click();
    await page.locator('[data-live-control=end]').click();
    await page.locator('[data-live-finalize]').click();
    assert(calls.some(([op]) => op === 'finalize'));
    assert.deepEqual(errors, []);
    await context.close();
  } finally { await browser.close(); }
}

main().catch(error => { console.error(error); process.exit(1); });
