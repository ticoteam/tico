// A sign-in return must not refill confirmed chat text from its saved restoration snapshot.
// All requests and sign-in pages are synthetic; the real UI, navigation and browser storage run unchanged.
const {chromium, webkit} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const engines = (process.env.TICO_UI_ENGINES || 'chromium,webkit').split(',');

async function scenario(browser, mode, edit, keeper = true, editedBefore = false) {
  const page = await browser.newPage({viewport: {width: 1200, height: 800}, serviceWorkers: 'block'});
  const errors = [], posts = [], delivered = [];
  const reads = new Set(), readWaiters = new Set();
  let navigating = false;
  const readsDrained = () => { if (!reads.size) for (const done of [...readWaiters]) done(); };
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (request.isNavigationRequest() && request.frame() === page.mainFrame()) navigating = true;
    if (request.method() === 'GET' && path !== '/api/v2/events' && !path.endsWith('/watch')) reads.add(request);
  });
  // A read still out when the page loads a new document is cancelled with the old one, and Chromium reports neither
  // requestfinished nor requestfailed for it: left counted, it held the next wait to its deadline ("Fixture reads did
  // not settle"). A new document in the main frame (not a hash change) ends every read made before it.
  page.on('framenavigated', frame => {
    if (frame !== page.mainFrame() || !navigating) return;
    navigating = false;
    reads.clear();
    readsDrained();
  });
  const readFinished = request => {
    reads.delete(request);
    readsDrained();
  };
  page.on('requestfinished', readFinished);
  page.on('requestfailed', readFinished);
  page.on('pageerror', e => errors.push(e.message));
  let expired = false, hold = null, release;
  // A real same-origin fixture keeps event streams open across sign-in. Fulfilling an intercepted
  // stream ends it immediately and creates reconnect/probe requests that WebKit can reject during navigation.
  const serverErrors = [];
  const server = http.createServer((request, response) => {
    void (async () => {
      const url = new URL(request.url, 'http://127.0.0.1'), p = url.pathname;
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const body = Buffer.concat(chunks).toString();
      const reply = (body, contentType, status = 200) => { response.writeHead(status, {'Content-Type': contentType}); response.end(body); };
      const json = (body, status = 200) => reply(JSON.stringify(body), 'application/json', status);
      const identity = () => json({error: {code: 'identity', detail: 'Sign in again', sign_in: '/auth/login'}}, 401);
      if (p === '/auth/login') return reply('<title>Fixture sign-in</title><a id="return" style="display:inline-block;padding:8px" href="' + url.searchParams.get('next') + '">Return after sign-in</a>', 'text/html');
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]))) return reply(fs.readFileSync(uiFile(ui[1]), 'utf8'), ui[1].endsWith('.css') ? 'text/css' : 'application/javascript');
      if (p.endsWith('.js')) return reply('', 'application/javascript');
      if (p === '/') return reply(html, 'text/html');
      if (p === '/api/v2/me' && expired) return identity();
      if (p === '/api/me' || p === '/api/v2/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
      if (p === '/api/employees') return json([{name: 'sales', display_name: 'Sales', host: keeper ? 'keeper' : 'local', status: 'active', can_chat: true, schedules: []}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/employees/sales/conversation') return json({runs: []});
      if (p === '/api/v2/conversations') return json({conversations: url.searchParams.get('chat_with')
        ? [{id: 'c1', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:sales']}] : []});
      if (p.endsWith('/snapshot')) return json({messages: delivered, execution: null});
      if (p === '/api/v2/events' || p.endsWith('/watch')) {
        response.writeHead(200, {'Content-Type': 'text/event-stream'});
        response.write('event: ready\ndata: {"seq":0}\n\n');
        return;
      }
      if (p === (keeper ? '/api/v2/chat/sales' : '/api/employees/sales/chat')) {
        const text = keeper ? JSON.parse(body).text : body.match(/name="text"\r\n\r\n([^]*?)\r\n--/)[1];
        posts.push({text, method: request.method});
        if (posts.length === 1) {
          if (mode === 'timeout') return; // the client must end the unanswered send itself
          expired = true;
          return identity();
        }
        if (hold) await hold;
        const message = {id: 'm' + posts.length, sender_id: 'human:ana', body: text, created_at: new Date().toISOString()};
        delivered.push(message);
        return json(keeper ? {conversation: {id: 'c1'}, message} : {run: 'fixture-run'});
      }
      return json(unrouted(p));
    })().catch(error => { serverErrors.push(error.message); if (!response.headersSent) response.writeHead(500); response.end(); });
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const origin = 'http://127.0.0.1:' + server.address().port;
  const box = page.locator('#chat-composer textarea'), send = page.locator('#chat-composer .p-send');
  const stored = () => page.evaluate(() => ({draft: chatDrafts().sales, otherDraft: chatDrafts().other,
    fields: JSON.parse(sessionStorage.getItem(SESSION_RETURN) || 'null')?.fields || []}));
  const frames = () => page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
  const readsSettled = async () => {
    const deadline = Date.now() + 30000;
    do {
      // A send schedules a snapshot read after a short burst; it can start after current reads finish.
      await page.waitForFunction(() => !LIVE_SOON.size);
      if (reads.size) await new Promise((resolve, reject) => {
        const done = () => { clearTimeout(timer); readWaiters.delete(done); resolve(); };
        const timer = setTimeout(() => { readWaiters.delete(done); reject(new Error('Fixture reads did not settle')); }, Math.max(1, deadline - Date.now()));
        readWaiters.add(done);
      });
      await frames();
      assert.ok(Date.now() < deadline, 'fixture reads settle before navigation');
    } while (reads.size || await page.evaluate(() => LIVE_SOON.size > 0));
  };
  // A loaded conversation can precede its stream subscription. Navigating again while WebKit is
  // still opening that connection reports a cancelled request as an access-control page error.
  const ready = () => page.waitForFunction(keeper => {
    if (!SESSION.seen || !(keeper ? typeof V2C !== 'undefined' && V2C?.loaded : typeof CONV !== 'undefined' && CONV?.slug === 'sales')
      || !liveConnected() || LIVE.es?.readyState !== EventSource.OPEN) return false;
    const subscribed = new URL(LIVE.es.url), expected = new URL(liveUrl(), location.href);
    // The ready event advances the cursor; only a change of subscription requires a new connection.
    subscribed.searchParams.delete('after'); expected.searchParams.delete('after');
    return subscribed.href === expected.href;
  }, keeper);
  const idle = () => page.waitForFunction(() => !BOT_PILL.sending && !document.querySelector('#chat-composer .p-send').disabled);
  const text = '  Keep this draft through sign-in  ';
  try {
    await page.goto(origin + '/#/bot/sales');
    await ready();
    await box.fill(text);
    await page.evaluate(() => localStorage.setItem(CHAT_DRAFTS, JSON.stringify({...chatDrafts(), other: 'Another bot draft'})));
    if (mode !== 'unsent') {
      if (mode === 'timeout') await page.evaluate(() => { CHAT_SEND.timeout = 250; });
      await send.click();
      await idle();
      if (mode === 'timeout') {
        await page.locator('.toast.err').waitFor();
        expired = true;
        await page.evaluate(() => get('/v2/me').catch(() => {}));
      } else if (!keeper) {
        // Legacy form errors do not check identity themselves. Confirm it directly, without relying
        // on the mock event stream closing and a reconnect eventually probing the session.
        await page.evaluate(() => get('/v2/me').catch(() => {}));
      }
    } else {
      expired = true;
      await page.evaluate(() => get('/v2/me').catch(() => {}));
    }
    await page.locator('#signed-out:not([hidden])').waitFor();
    assert.equal(await box.inputValue(), text, 'sign-out keeps the unsent text');
    await page.evaluate(text => {
      const kept = JSON.parse(sessionStorage.getItem(SESSION_RETURN));
      // A snapshot saved by an older page can still contain the chat draft. New pages keep only the
      // per-bot copy, but a confirmed send must consume the older saved copy and its live watcher too.
      const input = document.querySelector('#chat-composer textarea');
      kept.fields.push({scope: sessionScope(input), key: sessionKey(input), value: text});
      kept.fields.push({scope: 'task:other', key: 'Message', value: 'Another task draft'});
      sessionSave(kept);
    }, text);
    await readsSettled();
    await page.click('#signed-out button');
    await page.waitForURL('**/auth/login**');
    expired = false;
    await page.click('#return');
    await page.waitForURL(origin + '/#/bot/sales');
    await ready();
    await readsSettled();
    assert.equal(await box.inputValue(), text, 'return restores the draft');
    assert.ok((await stored()).fields.some(f => f.value === text), 'the return snapshot still holds a copy beside the restored local draft');

    if (mode !== 'unsent') {
      const sentText = editedBefore ? 'Edited after sign-in, before sending' : text.trim();
      if (editedBefore) await box.fill(sentText);
      if (edit) hold = new Promise(done => release = done);
      await send.click();
      await page.waitForFunction(() => BOT_PILL.sending || document.querySelector('#chat-composer .p-send').disabled || !document.querySelector('#chat-composer textarea').value);
      const next = 'Next draft typed while the resend is out\nA second line\nA third line';
      if (edit) {
        await box.fill(next);
        release(); hold = null;
      }
      await idle();
      await frames(); // let the sign-in return watcher respond to the send's DOM changes
      assert.equal(await box.inputValue(), edit ? next : '', 'confirmed text never comes back; later edits stay');
      if (edit) assert.ok(await box.evaluate(el => el.clientHeight >= el.scrollHeight - 2), 'later multiline edits keep the composer expanded');
      const saved = await stored();
      assert.equal(saved.draft, edit ? next : undefined, 'the persistent bot draft agrees with the composer');
      assert.ok(!saved.fields.some(f => f.value === text), 'the saved sign-in snapshot consumed the composer draft, including its pre-edit copy');
      assert.equal(delivered.length, 1, 'one successful delivery');
      assert.equal(delivered[0].body, sentText, 'the edited text is the text sent');
      if (!edit) {
        assert.equal(await send.evaluate(button => getComputedStyle(button).opacity), '0.45', 'the empty composer dims Send');
        await box.press('Enter');
        await send.evaluate(button => button.click());
        await frames();
      }
      assert.equal(posts.length, 2, 'the interrupted attempt and explicit resend are the only POSTs');
      await readsSettled();
      await page.reload();
      await ready();
      await frames();
      assert.equal(await box.inputValue(), edit ? next : '', 'a later reload cannot revive confirmed text');
      assert.equal(posts.length, 2, 'reload sends nothing');
    } else {
      await readsSettled();
      await page.reload();
      await ready();
      assert.equal(await box.inputValue(), text, 'an existing draft never sent survives return and another reload');
      assert.equal(posts.length, 0, 'an unsent draft makes no POST');
    }
    const final = await stored();
    assert.equal(final.otherDraft, 'Another bot draft', 'other bots keep their unsent drafts');
    assert.ok(final.fields.some(f => f.scope === 'task:other' && f.value === 'Another task draft'), 'other boxes keep their sign-in snapshots');
    assert.ok(posts.every(request => request.method === 'POST'), 'all send requests use POST');
    assert.deepEqual(errors, [], 'no browser errors');
    assert.deepEqual(serverErrors, [], 'no fixture server errors');
    console.log('ok -', keeper ? 'keeper' : 'local', mode, editedBefore ? 'edited restored draft' : edit ? 'preserves later edits' : 'sign-in return');
  } finally {
    release?.();
    try { await page.close(); } finally {
      server.closeAllConnections();
      await new Promise(resolve => server.close(resolve));
    }
  }
}

(async () => {
  for (const engine of engines) {
    const browser = await ({chromium, webkit}[engine]).launch({headless: true,
      ...(engine === 'chromium' ? {channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} : {})});
    try {
      await scenario(browser, '401', false, true, true);
      await scenario(browser, '401', false, false, true);
      await scenario(browser, '401', false);
      await scenario(browser, 'timeout', false);
      await scenario(browser, '401', true);
      await scenario(browser, 'unsent', false);
      await scenario(browser, '401', false, false);
      await scenario(browser, '401', true, false);
      console.log('ok - chat send sign-in:', engine);
    } finally { await browser.close(); }
  }
})().catch(error => { console.error(error); process.exit(1); });
