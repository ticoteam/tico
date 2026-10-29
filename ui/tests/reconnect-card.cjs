// Offline browser regression: a bot's task stopped on an account (backend/reconnects.py) shows one card in its thread.
// The person who can fix it gets the fix (Reconnect Google goes to Google's own page only); anyone else sees whom it
// waits on; a fixed or retired card has no button. A website's card (service `site`) offers Open screen only when the
// bot's computer screen is on, Enter login (its login card's request, else straight to the vault and a grant to the
// bot) and Done; the password goes only to the save route. Desktop in dark, phone in light, no sideways scroll.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const launch = () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
const SHOTS = process.env.TICO_UI_SHOTS || '';
const GOOGLE = 'https://accounts.google.com/o/oauth2/v2/auth?client_id=acme';
const PASSWORD = 'acme-portal-pass 42!', TOTP = 'JBSWY3DPEHPK3PXP';

const now = Date.now(), at = m => new Date(now - m * 60000).toISOString();
const quote = {id: 't-quote', title: 'Send the Acme renewal quote', owner: 'bot:botops', requester: 'human:ana', status: 'waiting',
  lane: 'company', version: 4, created: at(90), updated: at(3)};
const card = (id, over, minutes) => ({id, conversation_id: 'r-quote', from_actor: 'bot:botops', to_actor: over.person, kind: 'say',
  body: over.line, created: at(minutes), refs: {task: 't-quote', reconnect: {status: 'open', bot: 'botops', ...over}}});
const comments = [
  {id: 'q1', conversation_id: 'r-quote', from_actor: 'bot:botops', kind: 'say', body: 'Drafting the quote now.', created: at(20)},
  card('q2', {key: 'google:ana@acme.example', service: 'google', label: 'Google (ana@acme.example)', person: 'human:ana',
    fix: {kind: 'google', account: 'ana@acme.example'}, line: "BotOps can't reach Google (ana@acme.example)."}, 9),
  card('q3', {key: 'secret:botops:ACME_CRM_TOKEN', service: 'secret', label: 'the Acme CRM token', person: 'human:ben',
    fix: {kind: 'secret', request: 'cr9'}, line: 'BotOps needs the Acme CRM token to go on.'}, 6),
  card('q4', {key: 'login:r1:codex:_default', service: 'login', label: 'Codex on Acme Mac', person: 'human:ana', status: 'fixed',
    fix: {kind: 'login', runner: 'r1', runtime: 'codex', machine: 'Acme Mac'}, line: "BotOps can't sign in to Codex on Acme Mac."}, 3),
  card('q5', {key: 'google:cara@acme.example', service: 'google', label: 'Google (cara@acme.example)', person: 'human:ana', status: 'retired',
    fix: {kind: 'google', account: 'cara@acme.example'}, line: "BotOps can't reach Google (cara@acme.example)."}, 2),
  // Ana's own credential card, retired when the task moved on: Not needed, never a card she can still save.
  card('q6', {key: 'secret:botops:ACME_ERP_TOKEN', service: 'secret', label: 'the Acme ERP token', person: 'human:ana', status: 'retired',
    fix: {kind: 'secret', request: 'cr8'}, line: 'BotOps needs the Acme ERP token to go on.'}, 1),
];
comments[comments.length - 1].refs.credential_request = 'cr8';
const site = (id, over, minutes) => card(id, {service: 'site', bot: 'botops', person: 'human:ana', ...over,
  line: `BotOps needs you on ${over.fix.host}.`}, minutes);
comments.push(
  // On the bot's computer, whose screen is on: the login goes through the card's own request.
  site('q7', {key: 'site:botops:portal.acme.example', label: 'portal.acme.example',
    fix: {kind: 'site', host: 'portal.acme.example', computer: 'r1', reason: 'Captcha on sign-up', request: 'cr-site'}}, 0.9),
  // A computer whose screen is off, no request: a credential administrator saves the login to the vault.
  site('q8', {key: 'site:botops:crm.acme.example', label: 'crm.acme.example', fix: {kind: 'site', host: 'crm.acme.example', computer: 'r2'}}, 0.8),
  site('q9', {key: 'site:botops:erp.acme.example', label: 'erp.acme.example', person: 'human:ben', fix: {kind: 'site', host: 'erp.acme.example', computer: 'r1'}}, 0.7),
  site('q11', {key: 'site:botops:docs.acme.example', label: 'docs.acme.example', fix: {kind: 'site', host: 'docs.acme.example', computer: 'r1'}}, 0.5),
  site('q10', {key: 'site:botops:old.acme.example', label: 'old.acme.example', status: 'fixed', fix: {kind: 'site', host: 'old.acme.example', computer: 'r1'}}, 0.6),
);
const loginRequest = {id: 'cr-site', status: 'pending', kind: 'login', env: '', bot: 'botops', bot_name: 'BotOps', label: '',
  title: 'BotOps needs a login for portal.acme.example', sites: ['portal.acme.example'], username: 'ops@acme.example',
  format: '', help_url: '', can_save: true, note: null, credential_id: null};

async function run(viewport, scheme, shot) {
  const browser = await launch();
  try {
    const page = await browser.newPage({viewport, serviceWorkers: 'block', colorScheme: scheme});
    await page.addInitScript(t => { try { localStorage.setItem('tico.theme', t); } catch {} }, scheme);
    const errors = [], connects = [], posts = [];
    const fixed = new Set();
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (url.origin === 'https://accounts.google.com') return route.fulfill({contentType: 'text/html', body: '<p>Google</p>'});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (req.method() === 'POST') posts.push({path: p, body: req.postData() || ''});
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true, credential_access: true});
      if (p === '/api/employees') return json([{name: 'botops', display_name: 'BotOps', host: 'keeper', status: 'active', can_chat: true, schedules: [], users: [{id: 'ana'}],
        online: true, machine: {runner_id: 'r1', label: 'Acme Linux', operator: 'ana', screen: 'control', screen_available: true}}]);
      if (p === '/api/v2/credential-requests/cr-site') return json(loginRequest);
      if (p === '/api/v2/credential-requests/cr-site/save') { fixed.add('q7'); return json({...loginRequest, status: 'saved', credential_id: 'c-site'}); }
      if (p === '/api/v2/credentials' && req.method() === 'POST') return json({id: 'c-crm', name: 'crm.acme.example'});
      if (p === '/api/v2/credentials/c-crm/grants') return json({id: 'g-crm'});
      const done = p.match(/^\/api\/v2\/reconnects\/([^/]+)\/done$/);
      if (done) { fixed.add(decodeURIComponent(done[1])); return json({ok: true}); }
      if (p === '/api/issues') return json([]);
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p === '/api/v2/mail/google/connect' && req.method() === 'POST') { connects.push(p); return json({auth_url: GOOGLE, mailbox: 'ana@acme.example'}); }
      if (p === '/api/v2/tasks/t-quote')
        return json({task: {...quote, conversation_id: 'r-quote'}, events: [], can_comment: true, children: [],
          comments: comments.map(m => fixed.has(m.id) ? {...m, refs: {...m.refs, reconnect: {...m.refs.reconnect, status: 'fixed'}}} : m)});
      if (p.startsWith('/api/v2/tasks/') && p.endsWith('/files')) return json({files: []});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/bot/botops');
    await page.waitForFunction(() => S.emps?.length);
    await page.evaluate(task => taskModalShow(task), quote);
    const thread = page.locator('#task-modal .task-comments');
    await thread.locator('.rc-card').first().waitFor();

    // Mine and open: the line and the one fix.
    const google = thread.locator('.rc-card[data-reconnect="google:ana@acme.example"]');
    assert.equal(await google.getAttribute('data-status'), 'open');
    assert.match(await google.innerText(), /BotOps can't reach Google \(ana@acme\.example\)\./);
    assert.equal(await google.locator('button.primary[data-reconnect-google]').count(), 1, 'the fix is one primary button');
    // Someone else's to fix: whom it waits on, no button and no credential card for a reader who cannot save it.
    const secret = thread.locator('.rc-card[data-reconnect="secret:botops:ACME_CRM_TOKEN"]');
    assert.match(await secret.innerText(), /Waiting on/);
    assert.equal(await secret.locator('button, a.button, [data-credential-host]').count(), 0);
    // Fixed: Reconnected, nothing to press.
    const login = thread.locator('.rc-card[data-reconnect="login:r1:codex:_default"]');
    assert.equal(await login.getAttribute('data-status'), 'fixed');
    assert.match(await login.innerText(), /Reconnected/);
    assert.equal(await login.locator('button, a.button').count(), 0);
    // Retired (its task ended or moved on): asks nothing.
    const retired = thread.locator('.rc-card[data-reconnect="google:cara@acme.example"]');
    assert.equal(await retired.getAttribute('data-status'), 'retired');
    assert.equal(await retired.locator('button, a.button').count(), 0);
    const unneeded = thread.locator('.rc-card[data-reconnect="secret:botops:ACME_ERP_TOKEN"]');
    assert.equal(await unneeded.getAttribute('data-status'), 'retired');
    assert.equal(await thread.locator('[data-credential-host="cr8"]').count(), 0, 'a retired credential card is not mounted');
    // Nothing runs off the side of the thread at phone width.
    const wide = await page.evaluate(() => [...document.querySelectorAll('#task-modal .rc-card')]
      .some(el => el.scrollWidth > el.clientWidth + 1 || el.getBoundingClientRect().right > innerWidth + 1));
    assert.equal(wide, false, 'cards fit the thread');
    if (shot) { await page.waitForTimeout(400); await google.evaluate(el => el.closest('.tcomment').scrollIntoView({block: 'center'})); await page.screenshot({path: shot}); }

    // A website: Log in to <host> and why; Open screen goes to the bot's computer screen.
    const portal = thread.locator('.rc-card[data-reconnect="site:botops:portal.acme.example"]');
    assert.match(await portal.innerText(), /Log in to portal\.acme\.example/);
    assert.match(await portal.innerText(), /Captcha on sign-up/);
    assert.equal(await portal.locator('a[data-site-screen]').getAttribute('href'), '#/computers/r1/screen');
    assert.deepEqual(await portal.locator('a, button').allInnerTexts(), ['Open screen', 'Enter login', 'Done']);
    // Screen off on its computer: no Open screen.
    const crm = thread.locator('.rc-card[data-reconnect="site:botops:crm.acme.example"]');
    assert.deepEqual(await crm.locator('a, button').allInnerTexts(), ['Enter login', 'Done']);
    // Asked of someone else: whom it waits on; Done only for someone who manages the bot (here the owner).
    const erp = thread.locator('.rc-card[data-reconnect="site:botops:erp.acme.example"]');
    assert.match(await erp.innerText(), /Waiting on/);
    assert.deepEqual(await erp.locator('a, button').allInnerTexts(), ['Done']);
    const old = thread.locator('.rc-card[data-reconnect="site:botops:old.acme.example"]');
    assert.match(await old.innerText(), /Done/);
    assert.equal(await old.locator('button, a').count(), 0);

    // Enter login opens the card's login form: username kept from the request, password and 2FA key typed here.
    await portal.locator('[data-site-login]').click();
    const form = portal.locator('[data-credential-host="cr-site"] form[data-kind="login"]');
    await form.waitFor();
    assert.equal(await form.locator('[name=username]').inputValue(), 'ops@acme.example');
    assert.deepEqual(await form.locator('input').evaluateAll(xs => xs.map(x => x.placeholder)), ['Username', 'Password', '2FA setup key (optional)']);
    if (shot) { await page.waitForTimeout(300); await portal.scrollIntoViewIfNeeded(); await page.screenshot({path: shot.replace('.png', '-site-v2.png')}); }
    await form.locator('[name=password]').fill(PASSWORD);
    await form.locator('[name=totp]').fill(TOTP);
    const wideSite = await page.evaluate(() => [...document.querySelectorAll('#task-modal .rc-site')]
      .some(el => el.scrollWidth > el.clientWidth + 1 || el.getBoundingClientRect().right > innerWidth + 1));
    assert.equal(wideSite, false, 'a site card and its form fit the thread');
    await form.locator('button[type=submit]').click();
    await thread.locator('.rc-card[data-reconnect="site:botops:portal.acme.example"][data-status="fixed"]').waitFor();
    const saves = posts.filter(r => r.path === '/api/v2/credential-requests/cr-site/save');
    assert.deepEqual(saves.map(r => JSON.parse(r.body)), [{username: 'ops@acme.example', password: PASSWORD, totp: TOTP}]);

    // No request behind it: the login goes to the vault for this host, is granted to the bot, and the card is Done.
    await crm.locator('[data-site-login]').click();
    const direct = crm.locator('[data-login-host] form[data-kind="login"]');
    await direct.waitFor();
    await direct.locator('[name=username]').fill('ops');
    await direct.locator('[name=password]').fill(PASSWORD);
    await direct.locator('button[type=submit]').click();
    await thread.locator('.rc-card[data-reconnect="site:botops:crm.acme.example"][data-status="fixed"]').waitFor();
    const made = posts.find(r => r.path === '/api/v2/credentials');
    assert.deepEqual(JSON.parse(made.body), {kind: 'login', name: 'crm.acme.example', sites: ['crm.acme.example'], source: 'Login card', username: 'ops', password: PASSWORD});
    assert.deepEqual(JSON.parse(posts.find(r => r.path === '/api/v2/credentials/c-crm/grants').body), {subject: 'bot:botops'});
    assert.ok(posts.some(r => r.path === '/api/v2/reconnects/q8/done'));
    // The password went to the save routes only, and is nowhere on the page.
    assert.ok(posts.filter(r => r.body.includes(PASSWORD)).every(r => ['/api/v2/credential-requests/cr-site/save', '/api/v2/credentials'].includes(r.path)));
    assert.ok(!(await page.content()).includes(PASSWORD), 'the password is not in the page');
    assert.ok(!(await page.content()).includes(TOTP), 'the 2FA key is not in the page');

    assert.equal(posts.filter(r => r.path.startsWith('/api/v2/reconnects/q7')).length, 0, 'saving through the card needs no Done');
    // Done closes a site card by hand.
    const docs = thread.locator('.rc-card[data-reconnect="site:botops:docs.acme.example"]');
    await docs.locator('[data-site-done]').click();
    await thread.locator('.rc-card[data-reconnect="site:botops:docs.acme.example"][data-status="fixed"]').waitFor();
    assert.deepEqual(JSON.parse(posts.find(r => r.path === '/api/v2/reconnects/q11/done').body), {task: 't-quote', key: 'site:botops:docs.acme.example'});

    // Reconnect Google starts the person's own Connect and goes to Google's page, nowhere else.
    await google.locator('[data-reconnect-google]').click();
    await page.waitForURL(u => String(u).startsWith('https://accounts.google.com/'));
    assert.deepEqual(connects, ['/api/v2/mail/google/connect']);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
}

(async () => {
  const shot = name => SHOTS ? path.join(SHOTS, name) : '';
  await run({width: 1200, height: 800}, 'dark', shot('reconnect-card-desktop.png'));
  await run({width: 390, height: 844}, 'light', shot('reconnect-card-phone.png'));
  console.log('ok');
})().catch(e => { console.error(e); process.exit(1); });
