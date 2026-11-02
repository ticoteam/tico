// Offline browser regression: a bot's task stopped on an account (backend/reconnects.py) shows one card in its thread.
// The person who can fix it gets the fix (Reconnect Google goes to Google's own page only); anyone else sees whom it
// waits on; a fixed or retired card has no button. Desktop in dark, phone in light, no sideways scroll.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const launch = () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
const SHOTS = process.env.TICO_UI_SHOTS || '';
const GOOGLE = 'https://accounts.google.com/o/oauth2/v2/auth?client_id=acme';

const now = Date.now(), at = m => new Date(now - m * 60000).toISOString();
const quote = {id: 't-quote', title: 'Send the Acme renewal quote', owner: 'bot:botops', requester: 'human:ana', status: 'waiting',
  lane: 'company', version: 4, created_at: at(90), updated_at: at(3)};
const card = (id, over, minutes) => ({id, conversation_id: 'r-quote', from_actor: 'bot:botops', to_actor: over.person, kind: 'say',
  body: over.line, created_at: at(minutes), refs: {task: 't-quote', reconnect: {status: 'open', bot: 'botops', ...over}}});
const comments = [
  {id: 'q1', conversation_id: 'r-quote', from_actor: 'bot:botops', kind: 'say', body: 'Drafting the quote now.', created_at: at(20)},
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

async function run(viewport, scheme, shot) {
  const browser = await launch();
  try {
    const page = await browser.newPage({viewport, serviceWorkers: 'block', colorScheme: scheme});
    await page.addInitScript(t => { try { localStorage.setItem('tico.theme', t); } catch {} }, scheme);
    const errors = [], connects = [];
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
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
      if (p === '/api/employees') return json([{name: 'botops', display_name: 'BotOps', host: 'keeper', status: 'active', can_chat: true, schedules: [], users: [{id: 'ana'}]}]);
      if (p === '/api/issues') return json([]);
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p === '/api/v2/mail/google/connect' && req.method() === 'POST') { connects.push(p); return json({auth_url: GOOGLE, mailbox: 'ana@acme.example'}); }
      if (p === '/api/v2/tasks/t-quote')
        return json({task: {...quote, conversation_id: 'r-quote'}, comments, events: [], can_comment: true, children: []});
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
