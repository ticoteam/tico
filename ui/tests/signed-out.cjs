// Signed out mid-session (ui/app/session.js): every sign-in ends in one banner, and the live stream stops retrying.
//  - a proxy (Cloudflare Access) answers the API and the event stream with a 302 to its login on another origin: the
//    banner shows, the stream stops reconnecting, the chat draft and the page's place are kept through the sign-in;
//  - the same for a 401 carrying `WWW-Authenticate: Cloudflare-Access`;
//  - built-in sign-in's 401 naming its login page: the banner, and its button goes to that page and back here;
//  - Tico's own 401 `identity`, with no login page and no proxy header: the banner, the stream stopped;
//  - a task open in its dialog (on a computer and as the phone's sheet): the banner's button is inside the dialog, by
//    pointer and by keyboard, the comment stays; back from the login the task opens again with its comment;
//  - a comment in a closed task: another task opened first gets none of it, the task opened later gets all of it;
//  - the network down (nothing answers, the browser still online): no banner, the chat says Reconnecting, and the
//    stream comes back when the network does; the same when the sign-in check itself gets no answer.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const path = require('node:path');
const SHOTS = process.env.TICO_UI_SHOTS || '';
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();

// Another origin, one that never resolves: Playwright does not route a navigation's redirect target, so the visit stays
// on this computer (and ends in the browser's error page).
const ACCESS = 'https://access.example.test/cdn-cgi/access/login';
const now = new Date().toISOString();
const until = async (what, fn, ms = t(10000)) => {
  const deadline = Date.now() + ms;
  for (;;) {
    const value = await fn();
    if (value) return value;
    if (Date.now() > deadline) throw new Error('timed out: ' + what);
    await new Promise(r => setTimeout(r, 50));
  }
};

const task = id => ({id, title: id === 't-a' ? 'Renew the staging certificate' : 'Archive old builds', owner: 'bot:ops',
  requester: 'human:ana', status: 'doing', lane: 'company', version: 1, created_at: now, updated_at: now, conversation_id: 'r-' + id});

async function scenario(browser, name, down, check, viewport = {width: 1300, height: 820}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors = [], seen = {streams: 0, me: 0, login: null, top: null};
  page.on('pageerror', e => errors.push(e.message));
  const state = {mode: 'ok'};
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname;
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/auth/login') { seen.login = url.search; return route.fulfill({contentType: 'text/html', body: '<title>login</title>'}); }
    if (p === '/' && state.mode === 'redirect') {
      if (route.request().isNavigationRequest()) seen.top = url.href;
      return route.fulfill({status: 302, headers: {location: ACCESS}, body: ''});
    }
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p.startsWith('/api/')) {
      if (p === '/api/v2/events') seen.streams++;
      if (p === '/api/v2/me') seen.me++;
      // `stream` mode: only the stream and the probe answer, so the stream alone has to find out.
      if (state.mode === 'stream' && p !== '/api/v2/events' && p !== '/api/v2/me') return;
      if (state.mode !== 'ok') return down(route, state.mode);
    }
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true, registered: true});
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', state: 'active', can_chat: true, schedules: []}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
    if (p === '/api/issues') return json([]);
    if (p === '/api/v2/events') return;          // held open, as the server does between changes
    if (p === '/api/v2/conversations') return json({conversations: url.searchParams.get('chat_with')
      ? [{id: 'c-ops', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:ops']}] : []});
    if (p === '/api/v2/conversations/c-ops/snapshot') return json({
      messages: [{id: 'm1', from_actor: 'human:ana', body: 'Draft the release notes', created_at: now}],
      execution: {id: 'x1', state: 'running', bot: 'ops', conversation_id: 'c-ops', text: 'Working on it', started_at: now}});
    if (p === '/api/v2/tasks') return json({tasks: [], next_offset: null, cursor: '1.a'});
    const one = p.match(/^\/api\/v2\/tasks\/(t-[ab])$/);
    if (one) return json({task: task(one[1]), comments: [], events: [], can_comment: true, children: []});
    if (/^\/api\/v2\/tasks\/t-[ab]\/files$/.test(p)) return json({files: []});
    if (p === '/api/v2/needs-you') return json({items: [], version: 'v0'});
    return json(unrouted(p));
  });
  try {
    await page.goto('https://tico-ui.test/#/bot/ops/chat');
    await until('the stream opens', () => seen.streams >= 1);
    await page.waitForSelector('.p-text:visible');
    await page.fill('.p-text', 'Half-written reply to Ops');
    await check({page, state, seen});
    assert.deepEqual(errors, [], name);
    console.log('ok -', name);
  } finally { await page.close(); }
}

// The banner is up and nothing reconnects for a few seconds more.
async function signedOutAndStill(page, seen) {
  await page.waitForSelector('#signed-out:not([hidden])', {timeout: t(10000)});
  assert.match(await page.textContent('#signed-out'), /You've been signed out\..*Sign in again/);
  const streams = seen.streams;
  await page.waitForTimeout(t(4000));
  assert.equal(seen.streams, streams, 'the stream stopped retrying');
  assert.equal(await page.locator('.toast.err').count(), 0, 'no error toasts under the banner');
}

// A task opened in full over the chat, as a chat card opens it, and a comment typed in it.
async function openTask(page, id, comment) {
  await page.evaluate(id => get('/v2/tasks/' + id).then(d => taskModalShow(d.task)), id);
  const box = `#task-modal[data-task="${id}"] textarea[aria-label="Message"]`;
  await page.waitForSelector(box);
  if (comment) await page.fill(box, comment);
  return box;
}
const closeTask = page => page.evaluate(() => document.getElementById('task-modal').close());
const expire = async (page, state) => { state.mode = 'stream'; await page.evaluate(() => LIVE.es.close() || LIVE.es.onerror()); };

// Back from the proxy's login, at the address without its # (the server never saw it).
async function signInAndReturn(page, state, seen, press) {
  state.mode = 'redirect';
  await press();
  await until('a top-level visit to this address', () => seen.top);
  assert.equal(seen.top, 'https://tico-ui.test/');
  // The visit is still on its way when the server sees it. Coming back before it lands (on the browser's error page
  // here) lets that page cut this navigation off.
  await until('the visit leaves for the login', () => !page.url().startsWith('https://tico-ui.test/'));
  state.mode = 'ok';
  await page.goto('https://tico-ui.test/');
  await page.waitForFunction(() => location.hash === '#/bot/ops/chat');
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const redirect = route => route.fulfill({status: 302, headers: {location: ACCESS}, body: ''});
    await scenario(browser, 'a 302 to the proxy login on another origin shows the banner and stops the stream', redirect,
      async ({page, state, seen}) => {
        // A box with no draft store of its own (a task's comment box is one), drawn by the page.
        const box = () => page.evaluate(() => document.body.insertAdjacentHTML('beforeend', '<textarea aria-label="Message"></textarea>'));
        await box();
        await page.fill('textarea[aria-label="Message"]', 'A comment on the task');
        state.mode = 'stream';
        await page.evaluate(() => LIVE.es.close() || LIVE.es.onerror());     // the five-minute stream ends
        await signedOutAndStill(page, seen);
        // Sign in again: a top-level visit to this address, which the proxy sends to its login, then back.
        await signInAndReturn(page, state, seen, () => page.click('#signed-out button'));
        await page.waitForFunction(() => document.querySelector('.p-text')?.value === 'Half-written reply to Ops');
        await box();
        await page.waitForFunction(() => document.querySelector('textarea[aria-label="Message"]').value === 'A comment on the task');
        assert.equal(await page.isHidden('#signed-out'), true);
      });

    const cloudflare401 = route => route.fulfill({status: 401, headers: {'WWW-Authenticate': 'Cloudflare-Access realm="tico-ui.test"'},
      contentType: 'text/html', body: 'Forbidden'});
    await scenario(browser, "a 401 with Cloudflare's WWW-Authenticate shows the banner and stops the stream", cloudflare401,
      async ({page, state, seen}) => {
        state.mode = '401';
        await page.evaluate(() => LIVE.es.close() || LIVE.es.onerror());
        await signedOutAndStill(page, seen);
      });

    const oidc401 = route => route.fulfill({status: 401, contentType: 'application/json',
      body: JSON.stringify({error: {code: 'identity', detail: 'Sign in to Tico', sign_in: '/auth/login'}})});
    await scenario(browser, "built-in sign-in's 401 shows the banner, and its button goes to the login page and back", oidc401,
      async ({page, state, seen}) => {
        state.mode = 'oidc';
        await page.evaluate(() => get('/v2/tasks').catch(() => {}));      // any read the page makes
        await signedOutAndStill(page, seen);
        await page.click('#signed-out button');
        await page.waitForURL('**/auth/login**');
        assert.equal(new URLSearchParams(seen.login).get('next'), '/#/bot/ops/chat');
      });

    const identity401 = route => route.fulfill({status: 401, contentType: 'application/json',
      body: JSON.stringify({error: {code: 'identity', detail: 'No identity from the proxy'}})});
    await scenario(browser, "Tico's own 401 identity, with no login page and no proxy header, shows the banner and stops the stream", identity401,
      async ({page, state, seen}) => {
        state.mode = 'identity';
        await page.evaluate(() => LIVE.es.close() || LIVE.es.onerror());
        await signedOutAndStill(page, seen);
      });

    for (const [where, viewport, press] of [
      ['on a computer, by pointer', undefined, page => page.click('#signed-out button')],
      ['as the phone sheet, by keyboard', {width: 390, height: 844}, async page => {
        for (let i = 0; i < 40 && !await page.evaluate(() => document.activeElement?.matches('#signed-out button')); i++) await page.keyboard.press('Tab');
        assert.equal(await page.evaluate(() => document.activeElement?.matches('#signed-out button')), true, 'Tab reaches Sign in again');
        await page.keyboard.press('Enter');
      }]]) {
      await scenario(browser, `a task open in its dialog ${where}: Sign in again works over it, and the task and its comment come back`, redirect,
        async ({page, state, seen}) => {
          const box = await openTask(page, 't-a', 'Cert renewed on staging, checking prod next');
          await expire(page, state);
          await signedOutAndStill(page, seen);
          assert.equal(await page.evaluate(() => document.querySelector('#task-modal:modal > #signed-out:first-child') !== null), true, 'the banner is in the dialog');
          assert.equal(await page.inputValue(box), 'Cert renewed on staging, checking prod next', 'the task stays open with its comment');
          if (SHOTS) await page.screenshot({path: path.join(SHOTS, `signed-out-task-${viewport ? 'phone' : 'desktop'}.png`)});
          await signInAndReturn(page, state, seen, () => press(page));
          await page.waitForFunction(b => document.querySelector(b)?.value === 'Cert renewed on staging, checking prod next', box, {timeout: t(10000)});
          assert.equal(await page.isHidden('#signed-out'), true);
        }, viewport);
    }

    await scenario(browser, "a closed task's comment goes back into that task only, however late it is opened", redirect,
      async ({page, state, seen}) => {
        await openTask(page, 't-a', 'Cert renewed on staging, checking prod next');
        await closeTask(page);
        await expire(page, state);
        await signedOutAndStill(page, seen);
        await signInAndReturn(page, state, seen, () => page.click('#signed-out button'));
        const other = await openTask(page, 't-b');
        await page.waitForTimeout(t(21000));                  // past the 20 s this used to wait
        assert.equal(await page.inputValue(other), '', "another task's box gets none of it");
        await closeTask(page);
        const box = await openTask(page, 't-a');
        await page.waitForFunction(b => document.querySelector(b)?.value === 'Cert renewed on staging, checking prod next', box);
      });

    await scenario(browser, 'the network down keeps reconnecting, with no banner', route => route.abort('internetdisconnected'),
      async ({page, state, seen}) => {
        // The held stream never says `ready`; mark it up, as a real one would be, so its drop is seen.
        await page.evaluate(() => { LIVE.connected = true; liveEmit('status', {connected: true}); });
        state.mode = 'down';
        await page.evaluate(() => LIVE.es.close() || LIVE.es.onerror());
        await until('the probe asked and found no answer', () => seen.me >= 1);
        await page.waitForFunction(() => /Reconnecting/.test(document.querySelector('#conv-state')?.textContent || ''), null, {timeout: t(10000)});
        const streams = seen.streams;
        await until('the stream keeps retrying', () => seen.streams >= streams + 1);
        assert.equal(await page.isHidden('#signed-out'), true);
        state.mode = 'ok';
        await page.waitForFunction(() => LIVE.connected || LIVE.es?.readyState === 0, null, {timeout: t(20000)});
        assert.equal(await page.isHidden('#signed-out'), true);
      });

    // A connection that stalls: the sign-in check gets no answer at all while the browser says it is online.
    await scenario(browser, 'a sign-in check with no answer leaves the stream reconnecting, and gives up after its deadline', (route, mode) => {
      if (new URL(route.request().url()).pathname === '/api/v2/me') return;              // held, never answered
      return route.abort('connectionfailed');
    }, async ({page, state, seen}) => {
      state.mode = 'held';
      await page.evaluate(() => LIVE.es.close() || LIVE.es.onerror());
      await until('the check is asked', () => seen.me >= 1);
      const streams = seen.streams;
      await until('the stream reconnects while the check waits', () => seen.streams > streams, t(4000));
      assert.equal(await page.evaluate(() => !!SESSION.probe), true, 'the check is still waiting');
      await page.waitForFunction(() => !SESSION.probe, null, {timeout: t(8000)});
      assert.equal(await page.isHidden('#signed-out'), true, 'no answer is not signed out');
      state.mode = 'ok';
      await page.waitForFunction(() => LIVE.es?.readyState === 0 || LIVE.connected, null, {timeout: t(40000)});
      await page.waitForTimeout(t(500));
      assert.equal(await page.isHidden('#signed-out'), true);
      // A late answer to a check that gave up leaves the newer check as it is.
      const late = await page.evaluate(async () => {
        const real = window.fetch, answers = [];
        window.fetch = (url, opts) => String(url).endsWith('/v2/me') ? new Promise(ok => answers.push(ok)) : real(url, opts);
        try {
          SESSION.probedAt = 0;
          const first = await sessionProbe();
          SESSION.probedAt = 0;
          const second = sessionProbe();
          answers[0](new Response('{}', {status: 200}));
          await new Promise(r => setTimeout(r, 50));
          const kept = SESSION.probe === second;
          answers[1](new Response('{}', {status: 200}));
          return {first, kept, out: SESSION.out};
        } finally { window.fetch = real; }
      });
      assert.deepEqual(late, {first: false, kept: true, out: false});
    });
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
