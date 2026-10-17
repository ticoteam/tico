// Signed out mid-session (ui/app/session.js): every sign-in ends in one banner, and the live stream stops retrying.
//  - a proxy (Cloudflare Access) answers the API and the event stream with a 302 to its login on another origin: the
//    banner shows, the stream stops reconnecting, the chat draft and the page's place are kept through the sign-in;
//  - the same for a 401 carrying `WWW-Authenticate: Cloudflare-Access`;
//  - built-in sign-in's 401 naming its login page: the banner, and its button goes to that page and back here;
//  - the network down (nothing answers, the browser still online): no banner, the chat says Reconnecting, and the
//    stream comes back when the network does.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
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

async function scenario(browser, name, down, check) {
  const page = await browser.newPage({viewport: {width: 1300, height: 820}, serviceWorkers: 'block'});
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
      messages: [{id: 'm1', from_actor: 'human:ana', body: 'Draft the release notes', created: now}],
      execution: {id: 'x1', state: 'running', bot: 'ops', conversation_id: 'c-ops', text: 'Working on it', started: now}});
    if (p === '/api/v2/tasks') return json({tasks: [], next_offset: null, cursor: '1.a'});
    if (p === '/api/v2/needs-you') return json({items: [], version: 'v0'});
    return json({});
  });
  try {
    await page.goto('https://tico-ui.test/#/bot/ops/chat');
    await until('the stream opens', () => seen.streams >= 1);
    await page.waitForSelector('.p-text');
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
        state.mode = 'redirect';
        // Sign in again: a top-level visit to this address, which the proxy sends to its login.
        await page.click('#signed-out button');
        await until('a top-level visit to this address', () => seen.top);
        assert.equal(seen.top, 'https://tico-ui.test/');
        state.mode = 'ok';
        // Back from the login, at the address without its # (the server never saw it).
        await page.goto('https://tico-ui.test/');
        await page.waitForFunction(() => location.hash === '#/bot/ops/chat');
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
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
