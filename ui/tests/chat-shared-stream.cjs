// A chat on a computer that is not cloud follows the page's one live stream (GET /api/v2/events), as a cloud chat does:
// no page holds a stream of its own (the retired /api/v2/stream), a change to the conversation reads its snapshot once,
// and the 15-second poll stops while the stream carries it.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();   // load.cjs arrives with #254

const now = new Date().toISOString();

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({viewport: {width: 1300, height: 820}, serviceWorkers: 'block'});
  const errors = [], streams = [], legacy = [], reads = {snapshot: 0};
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
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: false, registered: true});
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', state: 'active', can_chat: true, schedules: []}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
    if (p === '/api/issues') return json([]);
    if (p === '/api/v2/stream') { legacy.push(url.search); return route.abort(); }
    if (p === '/api/v2/events') { streams.push({url, route}); return; }        // held open until the test sends
    if (p === '/api/v2/conversations') return json({conversations: url.searchParams.get('chat_with')
      ? [{id: 'c-ops', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:ops']}] : []});
    if (p === '/api/v2/conversations/c-ops/messages') return json({messages: snapshot.messages, next_before: null});
    if (p === '/api/v2/conversations/c-ops/snapshot') { reads.snapshot++; return json(snapshot); }
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
    for (const old of streams.splice(0)) await old.route.abort().catch(() => {});
    await stream.route.fulfill({contentType: 'text/event-stream', body: blocks.join('')});
    return stream.url;
  };
  const following = url => url.searchParams.get('conversation') === 'c-ops';
  const block = (seq, event, data) => `id: ${seq}\nevent: ${event}\ndata: ${JSON.stringify({seq, ...data})}\n\n`;

  await page.goto('https://tico-ui.test/#/bot/ops/chat');
  await page.waitForFunction(() => V2C?.rendered && V2C.liveOff);
  const opened = reads.snapshot;
  const subscribed = await send(following, block(10, 'ready', {}));
  assert.equal(subscribed.searchParams.get('conversation'), 'c-ops', 'a non-cloud chat follows its conversation on the shared stream');

  // A reply from the bot: one read of the snapshot for the change, and the reply shows.
  snapshot = {messages: [...snapshot.messages, {id: 'm2', from_actor: 'bot:ops', body: 'The notes are in the doc.', created: now}], execution: null};
  await send(following, block(11, 'messages', {id: 'm2', conversation_id: 'c-ops', message: snapshot.messages[1]}));
  await page.getByText('The notes are in the doc.').waitFor();
  await page.waitForFunction(() => !LIVE_SOON.has('chat:c-ops') && !V2C.reading && !V2C.readAgain, null, {timeout: t(5000)});
  assert.equal(reads.snapshot, opened + 1, 'one read for one change, and none on a timer');

  assert.deepEqual(legacy, [], 'no page asks for the retired /api/v2/stream');
  assert.deepEqual(errors, []);
  await browser.close();
  console.log('chat-shared-stream: ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
