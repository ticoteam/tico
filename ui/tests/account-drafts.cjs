// Synthetic accounts and HTTP answers; real page, sign-out links, storage and sign-in navigation.
// A saved text must wait for /me and may return only to the same account in the same company.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const ana = {id: 'ana', role: 'owner', cloud: true, proxy_session: true, config: {environment_id: 'company-a'}};
const task = {id: 'draft-task', title: 'Synthetic draft task', owner: 'bot:ops', requester: 'human:ana',
  status: 'doing', version: 1, lane: 'company', created: new Date().toISOString(), conversation_id: 'draft-room'};
const taskBox = '#task-modal textarea[aria-label="Message"]';
const chatBox = '#chat-composer textarea';
const frames = page => page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));

async function scenario(browser, name, options = {}) {
  const context = await browser.newContext({serviceWorkers: 'block'});
  let page = await context.newPage();
  let me = ana, expired = false, delay = null, meRequested = false;
  const errors = [], posts = [];
  page.on('pageerror', e => errors.push(e.message));
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname;
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (request.method() === 'POST' && /\/(?:chat|comments?|messages|uploads)(?:\/|$)/.test(p)) posts.push(p);
    if (p === '/api/v2/logout' || p === '/auth/login') return route.fulfill({contentType: 'text/html',
      body: '<title>Fixture sign-in</title><a id="return" href="/">Sign in</a>'});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]))) return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/api/me') {
      meRequested = true;
      if (delay) await delay;
      return options.failedMe && me !== ana ? json({error: {detail: 'Unknown account'}}, 403) : json(me);
    }
    if (p === '/api/v2/me' && expired) return json({error: {code: 'identity', sign_in: '/auth/login'}}, 401);
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', host: 'keeper', state: 'active', status: 'active', can_chat: true, schedules: []}]);
    if (p === '/api/humans') return json({people: []});
    if (p === '/api/issues') return json([]);
    if (p === '/api/v2/events' || p.endsWith('/watch')) return; // held open; never reconnect during navigation
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/tasks/draft-task') return json({task, comments: [], events: [], children: [], can_comment: true});
    if (p.endsWith('/files')) return json({files: []});
    return json(unrouted(p));
  });
  const ready = async () => {
    await page.waitForFunction(() => typeof S !== 'undefined' && S.me && SESSION.seen);
    await page.locator(chatBox).waitFor();
  };
  const openTask = async () => {
    await page.evaluate(t => taskModalShow(t), task);
    await page.locator(taskBox).waitFor();
  };
  try {
    await page.goto('https://tico-ui.test/#/bot/ops');
    await ready();
    if (!options.taskOnly) await page.locator(chatBox).fill('Ana chat draft');
    await openTask();
    await page.locator(taskBox).fill('Ana task draft');
    await page.evaluate(() => document.querySelector('#task-modal').close());
    // Pending sends carry another saved text and replay key; they have the same ownership boundary.
    if (!options.taskOnly) await page.evaluate(() => {
      CHAT_PENDING.set('ops', {text: 'Ana chat draft', signature: 'fixture-signature', key: 'fixture-key', at: Date.now()});
      chatPendingSave();
    });
    if (options.expiry) {
      expired = true;
      await page.evaluate(() => get('/v2/me').catch(() => {}));
      await page.locator('#signed-out:not([hidden])').waitFor();
      await page.click('#signed-out button');
      await page.waitForURL('**/auth/login**');
    } else {
      await page.click('#account');
      await page.click('#sign-out');
      await page.waitForURL('**/api/v2/logout');
    }
    const record = await page.evaluate(() => JSON.parse(sessionStorage.getItem('tico.signin.return')));
    if (options.expiry) assert.ok(record?.fields.some(f => f.value === 'Ana task draft'), 'typed task text was actually saved before returning');
    if (options.legacy) await page.evaluate(() => {
      const kept = JSON.parse(sessionStorage.getItem('tico.signin.return')); delete kept.owner;
      sessionStorage.setItem('tico.signin.return', JSON.stringify(kept));
      localStorage.setItem('hub.chat.drafts', JSON.stringify({ops: 'Ana legacy chat'}));
      sessionStorage.setItem('hub.chat.pending', JSON.stringify([['ops', {text: 'Ana legacy pending', signature: 'old', key: 'old-key', at: Date.now()}]]));
    });
    if (options.checkMerge) await page.evaluate(() => {
      const kept = JSON.parse(sessionStorage.getItem('tico.signin.return')); delete kept.owner;
      kept.fields.push({scope: 'task:other', key: 'Message', value: 'Unknown earlier text'});
      sessionStorage.setItem('tico.signin.return', JSON.stringify(kept));
    });
    if (options.checkMerge) {
      // A fresh save must never relabel unknown older fields as the current account's.
      await page.goto('https://tico-ui.test/#/bot/ops'); await ready();
      await page.evaluate(() => {
        sessionStorage.setItem(SESSION_RETURN, JSON.stringify({at: Date.now(), fields: [{scope: 'task:other', key: 'Message', value: 'Unknown earlier text'}]}));
        sessionKeep();
      });
      assert.ok(!(await page.evaluate(() => sessionStorage.getItem(SESSION_RETURN))).includes('Unknown earlier text'));
      return;
    }
    me = options.next || {...ana};
    expired = false;
    if (options.newTab) {
      page = await context.newPage();
      page.on('pageerror', e => errors.push(e.message));
      await page.goto('https://tico-ui.test/#/bot/ops');
    } else if (options.delay) {
      let release;
      delay = new Promise(done => release = done); meRequested = false;
      await page.click('#return');
      await page.waitForURL(url => url.origin === 'https://tico-ui.test' && url.pathname === '/');
      await page.waitForFunction(() => typeof sessionKeep === 'function');
      assert.ok(meRequested, 'the authenticated identity read is pending');
      // Create a matching task field while /me is pending: even a late-rendered box must stay empty.
      await page.evaluate(() => {
        const container = document.createElement('div'); container.dataset.task = 'draft-task';
        container.innerHTML = '<textarea aria-label="Message"></textarea>'; document.body.appendChild(container);
      });
      await frames(page);
      assert.equal(await page.locator('[data-task="draft-task"] textarea').inputValue(), '', 'no text before identity verification');
      release(); delay = null;
    } else { await page.click('#return'); await page.waitForURL(url => url.origin === 'https://tico-ui.test' && url.pathname === '/'); }
    if (options.failedMe) {
      await page.getByText('Tico server not running', {exact: true}).waitFor();
    } else {
      // The sign-in provider returned without a hash; navigate to the chat after return handling.
      await page.waitForFunction(() => typeof S !== 'undefined' && S.me && SESSION.seen);
      await page.evaluate(() => { location.hash = '#/bot/ops'; });
      await page.locator(chatBox).waitFor();
      await frames(page);
    }
    const keep = !!options.keep;
    if (!options.failedMe) {
      assert.equal(await page.locator(chatBox).inputValue(), keep ? 'Ana chat draft' : '', 'chat text belongs to this account/company');
      assert.equal(await page.evaluate(() => BOT_PILL.unconfirmed?.text || ''), keep ? 'Ana chat draft' : '', 'pending text belongs to this account/company');
      await openTask(); await frames(page);
      assert.equal(await page.locator(taskBox).inputValue(), keep ? 'Ana task draft' : '', 'task text belongs to this account/company');
    }
    const saved = await page.evaluate(() => ({ret: sessionStorage.getItem('tico.signin.return'),
      chat: localStorage.getItem('hub.chat.drafts'), pending: sessionStorage.getItem('hub.chat.pending')}));
    if (!keep) {
      assert.equal(saved.ret, null, 'unknown or different account deletes the return record');
      assert.ok(!saved.chat?.includes('Ana'), 'no previous account chat text remains');
      assert.ok(!saved.pending?.includes('Ana'), 'no previous account pending text remains');
    } else assert.deepEqual(record.owner, {account: 'ana', company: 'company-a'}, 'saved identity includes account and company');
    assert.deepEqual(errors, []);
    assert.deepEqual(posts, [], 'restoration never sends a message');
  } finally { await context.close(); }
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL || undefined});
  const cases = [
    ['sign-out then different account', {next: {...ana, id: 'ben'}}],
    ['new tab in shared profile, different account', {newTab: true, next: {...ana, id: 'ben'}}],
    ['task-only expiry, different account', {taskOnly: true, expiry: true, next: {...ana, id: 'ben'}}],
    ['sign-out then same account', {keep: true}],
    ['expiry then same account', {expiry: true, keep: true}],
    ['expiry then different account', {expiry: true, next: {...ana, id: 'ben'}}],
    ['same account in different company', {next: {...ana, config: {environment_id: 'company-b'}}}],
    ['unknown account', {next: {...ana, id: ''}}],
    ['unknown company', {next: {...ana, config: {}}}],
    ['failed identity read', {next: {...ana, id: 'unknown'}, failedMe: true}],
    ['legacy records', {expiry: true, legacy: true}],
    ['delayed identity read', {expiry: true, delay: true, next: {...ana, id: 'ben'}}],
    ['unknown fields are never relabeled', {expiry: true, checkMerge: true}],
  ];
  const failures = [];
  try {
    for (const [name, options] of cases) {
      try { await scenario(browser, name, options); console.log('PASS:', name); }
      catch (error) { failures.push(name); console.error('FAIL:', name, error.stack); }
    }
  } finally { await browser.close(); }
  assert.deepEqual(failures, []);
})().catch(error => { console.error(error); process.exitCode = 1; });
