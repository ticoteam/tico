// Live events (ui/app/live.js, GET /api/v2/events), offline: the fixture holds each stream open until the test sends
// what changed, as the server does when a write commits.
//  - the chat follows its conversation on the page's one stream and reads its snapshot once per change, not on a timer;
//  - the Needs-you count beside a bot follows the `needs` topic: the event names what changed, and only that is read;
//  - the task board takes a task changed or created elsewhere from the event itself, without reading the list again;
//  - each reconnect resumes from the last change number;
//  - the 2-minute refresh asks only for the tasks changed since the list's cursor and applies them, and asks for the
//    labels with the tag it was given (a 304 then).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();   // load.cjs arrives with #254

const now = new Date().toISOString();
const task = over => ({id: 't1', title: 'Draft the release notes', body: 'Details.', owner: 'bot:ops', requester: 'human:ana',
  status: 'doing', lane: 'company', labels: [], tags: [], links: [], parts: {total: 0, done: 0}, version: 3, created: now, updated: now, ...over});
const block = (seq, event, data) => `id: ${seq}\nevent: ${event}\ndata: ${JSON.stringify({seq, ...data})}\n\n`;

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({viewport: {width: 1300, height: 820}, serviceWorkers: 'block'});
  const errors = [], streams = [], reads = {snapshot: 0, tasks: 0, active: 0, changed: [], labels: [], needs: []};
  page.on('pageerror', e => errors.push(e.message));
  let snapshot = {messages: [{id: 'm1', from_actor: 'human:ana', body: 'Draft the release notes', created: now}], execution: null};
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
    if (p === '/api/v2/events') { streams.push({url, route}); return; }        // held open until the test sends
    if (p === '/api/v2/conversations') return json({conversations: url.searchParams.get('chat_with')
      ? [{id: 'c-ops', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:ops']}] : []});
    if (p === '/api/v2/conversations/c-ops/snapshot') { reads.snapshot++; return json(snapshot); }
    if (p === '/api/v2/tasks' && url.searchParams.get('changed_after')) {
      reads.changed.push(url.searchParams.get('changed_after'));
      return json({tasks: [task({id: 't3', title: 'Send the release mail', status: 'open'})], gone: ['t2'], cursor: '21.a', next_offset: null});
    }
    if (p === '/api/v2/tasks') {
      reads.tasks++;
      if ((url.searchParams.get('status') || '').includes('open')) reads.active++;
      return json({tasks: [task()], next_offset: null, cursor: '12.a'});
    }
    if (p === '/api/v2/tasks/labels') {
      const tag = route.request().headers()['if-none-match'] || '';
      reads.labels.push(tag);
      if (tag) return route.fulfill({status: 304, headers: {ETag: tag}, body: ''});
      return route.fulfill({contentType: 'application/json', headers: {ETag: 'W/"l1"'}, body: JSON.stringify({labels: ['launch'], tags: []})});
    }
    if (p === '/api/v2/needs-you') {
      reads.needs.push(url.search);
      if (url.searchParams.get('ids') === 't9') return json({count: 1, version: 'v1', positions: {t9: 0}, items: [{id: 't9', kind: 'task',
        title: 'Approve the notes', owner: 'human:ana', requester: 'bot:ops', status: 'open', created: now}]});
      return json(url.searchParams.get('version') === 'v0' ? {count: 0, version: 'v0', unchanged: true} : {items: [], version: 'v0'});
    }
    return json(unrouted(p));
  });
  // The newest stream the page holds that `match` accepts, answered with `blocks` (it then ends, and the page reconnects).
  const send = async (match, ...blocks) => {
    const deadline = Date.now() + t(8000);
    let stream;
    while (!(stream = streams.filter(s => match(s.url)).at(-1))) {
      if (Date.now() > deadline) throw new Error('no stream: ' + streams.map(s => s.url.search).join(' | '));
      await new Promise(r => setTimeout(r, 50));
    }
    streams.splice(streams.indexOf(stream), 1);
    for (const old of streams.splice(0)) await old.route.abort().catch(() => {});   // superseded ones the page closed
    await stream.route.fulfill({contentType: 'text/event-stream', body: blocks.join('')});
    return stream.url;
  };
  const following = url => url.searchParams.get('conversation') === 'c-ops';

  await page.goto('https://tico-ui.test/#/bot/ops/chat');
  await page.waitForFunction(() => V2C?.rendered && V2C.liveOff);
  const opened = reads.snapshot;

  // The chat: one stream for the page, following this conversation; a change to it reads the snapshot once.
  snapshot = {messages: [...snapshot.messages, {id: 'm2', from_actor: 'bot:ops', body: 'The notes are in the doc.', created: now}], execution: null};
  const first = await send(following, block(10, 'ready', {}), block(11, 'messages', {id: 'm2', conversation_id: 'c-ops', message: snapshot.messages[1]}));
  assert.deepEqual(first.searchParams.get('topics').split(',').sort(), ['bots', 'messages', 'needs', 'runs', 'tasks']);
  await page.getByText('The notes are in the doc.').waitFor();
  // Settled: no snapshot read queued, running or asked for again.
  await page.waitForFunction(() => !LIVE_SOON.has('chat:c-ops') && !V2C.reading && !V2C.readAgain, null, {timeout: t(5000)});
  assert.equal(reads.snapshot, opened + 1, 'one read for one change, and none on a timer');

  // Needs you: the count beside the bot follows the topic, and the reconnect resumes after change 11.
  const fullReads = reads.needs.filter(q => !q.includes('ids=')).length;
  const second = await send(following, block(12, 'needs', {count: 1, version: 'v1', base: 'v0', changed: ['t9'], removed: []}));
  assert.equal(second.searchParams.get('after'), '11');
  assert.equal(first.searchParams.get('needs'), 'changes');
  await page.locator('.cnt.needs', {hasText: '1'}).first().waitFor();
  assert.equal(reads.needs.filter(q => !q.includes('ids=')).length, fullReads, 'only the changed item is read');

  // The task board: a task changed elsewhere is redrawn from the event; the list is not read again.
  await page.evaluate(() => { location.hash = '#/board'; });
  // Done's page lands after the list: wait for both.
  await page.waitForFunction(() => TASKS_ST && !TASKS_ST.loading && TASKS_ST.doneLoaded && !TASKS_ST.doneLoading
    && document.querySelector('#task-body .tl-row, #task-body .bcard'), null, {timeout: t(8000)})
    .catch(async e => { throw new Error(e.message + ' ' + JSON.stringify(errors) + (await page.evaluate(() => document.querySelector('#main')?.innerText.slice(0, 400)))); });
  const listed = reads.tasks;
  const third = await send(() => true, block(13, 'tasks', {id: 't1', actor: 'human:ben', task: task({title: 'Draft the final release notes', version: 4})}));
  assert.equal(third.searchParams.get('after'), '12');
  await page.locator('#task-body', {hasText: 'Draft the final release notes'}).waitFor();
  assert.equal(reads.tasks, listed, 'the change came with the event');
  // A new task arrives whole too: a busy team's bots create tasks every few seconds, and every open tab gets each one.
  const fourth = await send(() => true, block(14, 'tasks', {id: 't2', actor: 'bot:ops', task: task({id: 't2', title: 'Check the release links', status: 'open'})}));
  assert.equal(fourth.searchParams.get('after'), '13');
  await page.locator('#task-body', {hasText: 'Check the release links'}).waitFor();
  await page.waitForFunction(() => !LIVE_SOON.has('tasks') && !LIVE_THROTTLE.get('tasks-bulk')?.timer, null, {timeout: t(5000)});   // the batch is applied
  assert.equal(reads.tasks, listed, 'a new task does not read the list again');

  // The 2-minute refresh: only what changed since the list's cursor, applied in place; the labels come back as a 304.
  const active = reads.active;
  await page.evaluate(() => refresh(false));
  await page.locator('#task-body', {hasText: 'Send the release mail'}).waitFor();
  await page.waitForFunction(() => !document.querySelector('#task-body')?.innerText.includes('Check the release links'));
  assert.deepEqual(reads.changed, ['12.a']);
  assert.equal(reads.active, active, 'no full read of the open tasks');
  assert.equal(await page.evaluate(() => TASKS_ST.since), '21.a');
  assert.deepEqual(await page.evaluate(() => TASKS_ST.tasks.map(t => t.id).sort()), ['t1', 't3'], 'an unchanged task stays');
  assert.deepEqual(reads.labels.slice(-1), ['W/"l1"']);
  assert.deepEqual(await page.evaluate(() => TASKS_ST.labels), ['launch']);
  assert.deepEqual(errors, []);
  console.log('live events: chat, Needs you and the task board follow one stream; the refresh reads only what changed: ok');
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
