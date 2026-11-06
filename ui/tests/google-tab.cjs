// Offline browser regression: the Connection card of Tools › Settings › Email (ui/google-connect.js) over the contract's
// GET /v2/google, with a fake hub (ui/tests/support/google-fake.cjs). An admin sees the two ways (Whole company, Each
// person signs in) as one toggle: neither picked while nothing is set up, the way set up picked otherwise, one way shown
// at a time. Each way's setup is a prompt for the person's own agent by default (the Workspace one makes its setup code
// only on Make prompt), or the steps by hand: the Whole company key in three steps, the check polling on a 20 s timer for
// 10 minutes (Playwright's fake clock), disconnect and replace confirming in the page. A member sees Connect Google for
// their own mailbox only (nothing when the company's key covers it), and a phone width has no sideways scroll.
// Leaving Tools › Settings and coming back keeps the card's state (ui/app/inboxes-email.js keeps one host, MB_GOOGLE_HOST,
// off the page while away). What Tico may do is ui/tests/inboxes.cjs.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {googleFake, row} = require('./support/google-fake.cjs');
const launch = () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});

const config = {version: '0.1.0', app_name: 'Tico', local: true};
const SHELL = {
  '/api/me': {id: 'ana', role: 'human', name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config},
  '/api/v2/config': config,
  '/api/employees': [],
  '/api/humans': {people: [{id: 'ana', name: 'Ana'}], teams: {}},
  '/api/people': {people: [], teams: {}},
  '/api/status': {cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []},
  '/api/v2/status': {bots: []},
  '/api/v2/needs-you': {items: []},
  '/api/v2/operations': {machines: [], services: []},
  '/api/v2/models': {models: [], harnesses: []},
  '/api/v2/me/tokens': {tokens: []},
};

// Opens Tools › Settings › Email on a fresh page. `clock` installs Playwright's fake timers before the page loads.
const READY = '#set-google [data-google-mode], #set-google .google-card';
const CARD = '#/tools/settings/email';
// Back to the card from another Settings tab, as its Settings link does.
const back = page => page.evaluate(card => { location.hash = card; }, CARD);
// Away to another page: the card's host is kept off the page, so what it shows is read from the host itself.
const away = page => page.evaluate(() => { location.hash = '#/settings/general'; });
const heldResult = page => page.evaluate(() => MB_GOOGLE_HOST?.querySelector('[data-ws-check-result]')?.textContent || '');
const heldShows = async (page, re) => { const end = Date.now() + 8000; while (!re.test(await heldResult(page))) { if (Date.now() > end) throw new Error('Timed out waiting for ' + re); await new Promise(r => setTimeout(r, 50)); } };
async function open(browser, fake, {width = 1440, clock = false, ready = READY} = {}) {
  const page = await browser.newPage({viewport: {width, height: 900}, serviceWorkers: 'block'});
  const seen = {errors: [], dialogs: 0, sent: []};
  page.on('pageerror', error => seen.errors.push(error.message));
  page.on('dialog', dialog => { seen.dialogs += 1; void dialog.accept(); });
  // Every write the page makes, to any route: a copied link must add none.
  page.on('request', request => { if (request.method() !== 'GET') seen.sent.push(`${request.method()} ${new URL(request.url()).pathname}`); });
  // The clipboard, kept in the page so a test can read what was copied.
  await page.addInitScript(() => {
    window.__copied = [];
    Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async text => { window.__copied.push(text); }}});
  });
  if (clock) await page.clock.install({time: new Date()});
  await page.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname;
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.hostname === 'accounts.google.com') return route.fulfill({contentType: 'text/html', body: '<h1>Google sign-in</h1>'});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p.startsWith('/api/v2/google') || p.startsWith('/api/v2/mail/google')) {
      const raw = request.postData();
      const answer = fake.answer(p, request.url(), request.method(), raw ? JSON.parse(raw) : null);
      if (answer !== undefined) return json(answer);
    }
    if (p in SHELL) return json(SHELL[p]);
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto(`https://tico-ui.test/${CARD}`);
  if (ready) await page.locator(ready).first().waitFor();
  else await page.waitForFunction(() => document.querySelector('#set-google')?._googleState);
  return {page, seen};
}

// The admin's pick of a way, then (optionally) the by-hand steps instead of the agent prompt.
async function pick(page, path, {self = false} = {}) {
  await page.locator(`[data-google-pick=${path}]`).click();
  await page.locator(`[data-google-card=${path === 'workspace' ? 'workspace' : 'account'}]`).waitFor();
  if (self) await bySelf(page, path);
}
async function bySelf(page, path) {
  await page.locator(`[data-google-how=self][data-google-path=${path}]`).click();
  await page.locator(`[data-google-how=self][data-google-path=${path}][aria-pressed=true]`).waitFor();
}
const cards = page => page.$$eval('#set-google [data-google-card]', els => els.map(el => el.dataset.googleCard));
const LINK = 'https://tico-ui.test/#/tools/settings/email';

const until = async (check, what, ms = 8000) => {
  const end = Date.now() + ms;
  while (!check()) {
    if (Date.now() > end) throw new Error(`Timed out waiting for ${what}`);
    await new Promise(resolve => setTimeout(resolve, 50));
  }
};
// The check's next 20 s timer, once the page has set one other than `previous`. The page sets it only after it has
// handled the answer before, so the clock may run only then: advanced earlier, it passes a timer not yet set.
const nextCheckTimer = async (page, previous, what) => {
  const end = Date.now() + 8000;
  for (;;) {
    const timer = await page.evaluate(() =>
      [...document.querySelectorAll('*')].find(el => el._googleWatch)?._googleWatch?.timer ?? null);
    if (timer !== null && timer !== previous) return timer;
    if (Date.now() > end) throw new Error(`Timed out waiting for ${what}`);
    await new Promise(resolve => setTimeout(resolve, 20));
  }
};
const writesTo = (fake, path, method = 'POST') => fake.writes.filter(write => write.path === path && write.method === method);
const KEY = {type: 'service_account', private_key_id: 'KEYMARK-7731', client_email: 'tico-mail@tico-mail-a1b2c3.iam.gserviceaccount.com'};
const keyFile = {name: 'key.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(KEY))};

const scenarios = {
  'an admin with nothing set up sees both ways with neither picked, and each pick shows only its way': async browser => {
    const fake = googleFake();
    const {page, seen} = await open(browser, fake);
    assert.equal((await page.locator('[data-settings-tab=connections]').innerText()).trim(), 'Connections');
    const modes = () => page.$$eval('[data-google-pick]', els => els.map(el => [el.innerText.trim(), el.getAttribute('aria-checked')]));
    assert.deepEqual(await modes(), [['Whole company', 'false'], ['Each person signs in', 'false']]);
    assert.deepEqual(await cards(page), [], 'no way shows before the pick');
    await pick(page, 'accounts');
    assert.deepEqual(await cards(page), ['account']);
    assert.equal(await page.locator('[data-google-pick=accounts]').getAttribute('aria-checked'), 'true');
    await pick(page, 'workspace');
    assert.deepEqual(await cards(page), ['workspace']);
    assert.equal(await page.locator('[data-ws-step=create]').count(), 1);
    assert.equal(await page.locator('#set-google h3').count(), 0, 'no heading over a way');
    await pick(page, 'accounts');
    assert.deepEqual(await cards(page), ['account']);
    assert.equal(writesTo(fake, '/api/v2/google/workspace/setup-code').length, 0, 'picking makes no code');
    assert.deepEqual(seen.errors, []);
  },

  'Each person signs in: the agent prompt is the default and names the redirect URI and this tab': async browser => {
    const fake = googleFake();
    const {page, seen} = await open(browser, fake);
    await pick(page, 'accounts');
    assert.equal(await page.locator('[data-google-how=agent][data-google-path=accounts]').getAttribute('aria-pressed'), 'true');
    assert.equal(await page.locator('[data-oauth-form]').count(), 0, 'the by-hand steps wait for Set it up myself');
    const prompt = await page.locator('[data-google-prompt=accounts] textarea').inputValue();
    assert.ok(prompt.includes('https://tico-ui.test/api/v2/mail/google/callback'), prompt);
    assert.ok(prompt.includes(LINK), prompt);
    assert.match(prompt, /acme\.example/);
    assert.ok(prompt.includes('gmail.googleapis.com') && prompt.includes('calendar-json.googleapis.com'), 'both APIs are turned on');
    assert.match(prompt, /ask me before you agree/);
    await page.locator('[data-google-prompt=accounts] [data-copy-prompt]').click();
    await page.waitForFunction(text => window.__copied.at(-1) === text, prompt);
    await bySelf(page, 'accounts');
    await page.locator('[data-oauth-form]').waitFor();
    assert.equal(await page.locator('[data-google-prompt]').count(), 0);
    assert.equal(fake.peopleReads, 0, 'no team list: everyone else is on the Tools list');
    assert.deepEqual(seen.errors, []);
  },

  'Whole Workspace: Make prompt is the default, and only its click makes a code, which the prompt holds': async browser => {
    const fake = googleFake();
    const {page, seen} = await open(browser, fake);
    await pick(page, 'workspace');
    assert.equal(await page.locator('[data-google-how=agent][data-google-path=workspace]').getAttribute('aria-pressed'), 'true');
    assert.equal(await page.locator('[data-ws-command]').count(), 0);
    await page.evaluate(() => window.mountGoogleConnect(document.querySelector('#set-google')));
    await page.waitForTimeout(300);
    assert.equal(writesTo(fake, '/api/v2/google/workspace/setup-code').length, 0, 'a render makes no code');
    await page.locator('[data-ws-prompt]').click();
    await page.locator('[data-google-prompt=workspace] textarea').waitFor();
    const prompt = await page.locator('[data-google-prompt=workspace] textarea').inputValue();
    assert.ok(prompt.includes('curl -fsSL https://tico-ui.test/api/v2/google/setup.sh | bash -s -- code1'), prompt);
    assert.match(prompt, /valid \d+ minutes, one use/);
    assert.match(prompt, /super admin of acme\.example/);
    assert.ok(prompt.includes(LINK), prompt);
    assert.equal(writesTo(fake, '/api/v2/google/workspace/setup-code')[0].body.domain, 'acme.example');
    await page.evaluate(() => window.mountGoogleConnect(document.querySelector('#set-google')));
    await page.waitForTimeout(300);
    assert.equal(writesTo(fake, '/api/v2/google/workspace/setup-code').length, 1, 'a re-render makes no second code');
    // The agent runs it; the key lands and the prompt, with its code, goes.
    fake.state.workspace = row('pending', {generation: 2});
    await page.locator('[data-ws-step=allow]').waitFor({timeout: 15000});
    assert.equal(await page.locator('[data-google-prompt]').count(), 0);
    assert.doesNotMatch(await page.locator('#set-google').innerText(), /code1/);
    assert.deepEqual(seen.errors, []);
  },

  'Whole Workspace: New prompt makes a fresh code; a Domain edit or another admin\'s newer code drops the prompt': async browser => {
    const fake = googleFake();
    const {page, seen} = await open(browser, fake);
    await pick(page, 'workspace');
    const codes = () => writesTo(fake, '/api/v2/google/workspace/setup-code');
    const prompt = () => page.locator('[data-google-prompt=workspace] textarea').inputValue();
    const holds = code => page.waitForFunction(code => document.querySelector('[data-google-prompt=workspace] textarea')
      ?.value.includes(`bash -s -- ${code}`), code);
    await page.locator('[data-ws-prompt]', {hasText: 'Make prompt'}).click();
    await holds('code1');
    await page.locator('[data-google-prompt=workspace] [data-ws-prompt]', {hasText: 'New prompt'}).click();
    await holds('code2');
    assert.equal(codes().length, 2, 'New prompt makes one fresh code');
    // A different domain makes the shown code wrong: it goes at once, and the cursor stays in the field.
    await page.locator('[data-ws-domain]').fill('acme2.example');
    await page.locator('[data-ws-prompt]', {hasText: 'Make prompt'}).waitFor();
    assert.equal(await page.locator('[data-google-prompt]').count(), 0);
    assert.equal(await page.evaluate(() => document.activeElement?.matches('[data-ws-domain]')), true);
    await page.evaluate(() => window.mountGoogleConnect(document.querySelector('#set-google')));
    await page.waitForTimeout(300);
    assert.equal(await page.locator('[data-google-prompt]').count(), 0, 'a render brings no code back');
    assert.equal(await page.locator('[data-ws-domain]').inputValue(), 'acme2.example');
    assert.equal(codes().length, 2, 'a Domain edit or a render makes no code');
    await page.locator('[data-ws-prompt]', {hasText: 'Make prompt'}).click();
    await holds('code3');
    assert.equal(codes()[2].body.domain, 'acme2.example');
    assert.match(await prompt(), /Workspace for acme2\.example/);
    // Another admin makes a newer code, so the server would refuse this one: the next read drops it.
    fake.gen += 1;
    await page.evaluate(() => window.mountGoogleConnect(document.querySelector('#set-google')));
    await page.locator('[data-ws-prompt]', {hasText: 'Make prompt'}).waitFor();
    assert.equal(await page.locator('[data-google-prompt]').count(), 0);
    assert.doesNotMatch(await page.locator('#set-google').innerText(), /code3/);
    assert.equal(codes().length, 3);
    assert.deepEqual(seen.errors, []);
  },

  'the setup prompt names what Tico may do, and the boxes are not in this card': async browser => {
    const fake = googleFake();
    fake.state.permissions = {organize_mail: true, send_mail: true, change_calendar: false};
    const {page, seen} = await open(browser, fake);
    await pick(page, 'accounts');
    assert.match(await page.locator('[data-google-prompt=accounts] textarea').inputValue(),
      /Tico will ask Google for: read mail and calendar, organize mail, send mail\./);
    assert.match(await page.locator('[data-google-prompt=accounts] textarea').inputValue(), /Each person signs in > Set it up myself/);
    assert.equal(await page.locator('#set-google [data-google-may], #set-google [data-google-may-card]').count(), 0);
    assert.deepEqual(seen.errors, []);
  },

  'the way already set up is picked; with both set up, the Workspace': async browser => {
    const fake = googleFake();
    fake.state.workspace = row('connected');
    let {page, seen} = await open(browser, fake);
    assert.equal(await page.locator('[data-google-pick=workspace]').getAttribute('aria-checked'), 'true');
    assert.deepEqual(await cards(page), ['workspace']);
    await pick(page, 'accounts');
    assert.deepEqual(await cards(page), ['account'], 'one way at a time');
    assert.deepEqual(seen.errors, []);
    await page.close();
    fake.state.workspace = null;
    fake.state.oauth = {...fake.state.oauth, configured: true, source: 'env', client_id: 'acme-client-id'};
    ({page, seen} = await open(browser, fake));
    assert.equal(await page.locator('[data-google-pick=accounts]').getAttribute('aria-checked'), 'true');
    assert.deepEqual(await cards(page), ['account']);
    assert.match(await page.locator('[data-acct-step=signin]').innerText(), /Done\. Set on the server\./);
    assert.equal(await page.locator('[data-acct-step=team], .google-team').count(), 0, 'no team list');
    await page.close();
    fake.state.workspace = row('connected');
    ({page, seen} = await open(browser, fake));
    assert.equal(await page.locator('[data-google-pick=workspace]').getAttribute('aria-checked'), 'true');
    assert.deepEqual(await cards(page), ['workspace']);
    assert.deepEqual(seen.errors, []);
  },

  'a connected key reads Connected · checked, and Check connection checks it': async browser => {
    const fake = googleFake();
    fake.state.workspace = row('connected', {checked_at: new Date(Date.now() - 120000).toISOString()});
    fake.checks = ['ok'];
    const {page, seen} = await open(browser, fake);
    assert.match((await page.locator('[data-ws-connected]').innerText()).replace(/\s+/g, ' '), /Connected · checked 2m ago/);
    assert.equal(await page.locator('.google-facts').count(), 0, 'no domain or client ID facts');
    await page.locator('[data-ws-recheck]').click();
    await until(() => writesTo(fake, '/api/v2/google/workspace/check').length === 1, 'the check');
    await page.locator('[data-ws-status]', {hasText: 'Connected · checked'}).waitFor();
    assert.deepEqual(seen.errors, []);
  },

  'New command makes one code, and re-opening the tab makes none': async browser => {
    const fake = googleFake();
    const {page, seen} = await open(browser, fake);
    await pick(page, 'workspace', {self: true});
    await page.locator('[data-ws-command]').click();
    await page.locator('.machine-command code', {hasText: 'bash -s -- code1'}).waitFor();
    // Settings calls the mount on every visit to the tab; it is the same call.
    await page.evaluate(() => window.mountGoogleConnect(document.querySelector('#set-google')));
    await page.locator('#set-google .machine-command code', {hasText: 'bash -s -- code1'}).waitFor();
    await page.waitForTimeout(300);
    assert.equal(writesTo(fake, '/api/v2/google/workspace/setup-code').length, 1, 'a tab switch issues no code');
    assert.deepEqual(seen.errors, []);
  },

  'uploading the key shows the delegation step, and the key never shows': async browser => {
    const fake = googleFake();
    const {page, seen} = await open(browser, fake);
    await pick(page, 'workspace', {self: true});
    await page.locator('[data-ws-file]').setInputFiles(keyFile);
    await page.locator('[data-ws-step=allow]').waitFor();
    const link = await page.locator('a.primary', {hasText: 'Open Google Admin'}).getAttribute('href');
    assert.match(link, /^https:\/\/admin\.google\.com\/ac\/owl\/domainwidedelegation\?clientIdToAdd=/);
    assert.ok(await page.locator('[data-copy]').count() >= 2, 'the client ID and the scopes each have a copy button');
    const sent = writesTo(fake, '/api/v2/google/workspace/key')[0];
    assert.equal(sent.body.key.type, 'service_account');
    assert.equal(sent.body.domain, 'acme.example');
    assert.doesNotMatch(await page.locator('#set-google').innerText(), /KEYMARK-7731/);
    assert.deepEqual(seen.errors, []);
  },

  'a New command waits for the key, then the steps follow without a reload': async browser => {
    const fake = googleFake();
    const {page, seen} = await open(browser, fake);
    await pick(page, 'workspace', {self: true});
    await page.locator('[data-ws-command]').click();
    await page.locator('.machine-command code', {hasText: 'bash -s -- code1'}).waitFor();
    // The script uploads the key on the computer, the way Cloud Shell does; the card polls until it lands.
    fake.state.workspace = row('pending');
    await page.locator('[data-ws-step=allow]').waitFor({timeout: 15000});
    assert.deepEqual(seen.errors, []);
  },

  'unauthorized_client lists the causes, rechecks every 20 s, and connects': async browser => {
    const fake = googleFake();
    fake.state.workspace = row('pending');
    fake.checks = ['unauthorized', 'unauthorized', 'ok'];
    const {page, seen} = await open(browser, fake, {clock: true});
    await page.locator('[data-ws-check]').click();
    await page.locator('.google-causes li').nth(3).waitFor();
    assert.equal(await page.locator('.google-causes li').count(), 4);
    assert.match(await page.locator('[data-ws-check-result]').innerText(), /Checking again every 20 seconds/);
    const checks = () => writesTo(fake, '/api/v2/google/workspace/check');
    await until(() => checks().length === 1, 'the first check');
    let timer = await nextCheckTimer(page, null, 'the timer after the first check');
    await page.clock.runFor(20000);
    await until(() => checks().length === 2, 'the second check after 20 s');
    await nextCheckTimer(page, timer, 'the timer after the second check');
    await page.clock.runFor(20000);
    await page.locator('[data-ws-status]', {hasText: 'Connected · checked ana@acme.example'}).waitFor();
    assert.equal(await page.locator('[data-ws-replace]').count(), 1, 'the key is connected now');
    assert.deepEqual(seen.errors, []);
  },

  'after ten minutes of unauthorized answers the button reads Check again': async browser => {
    const fake = googleFake();
    fake.state.workspace = row('pending');
    const {page, seen} = await open(browser, fake, {clock: true});
    await page.locator('[data-ws-check]').click();
    await until(() => writesTo(fake, '/api/v2/google/workspace/check').length === 1, 'the first check');
    let timer = null;
    for (let tick = 1; tick <= 30; tick += 1) {
      timer = await nextCheckTimer(page, timer, `the timer after check ${tick}`);
      await page.clock.runFor(20000);
      await until(() => writesTo(fake, '/api/v2/google/workspace/check').length === tick + 1, `check ${tick + 1}`);
    }
    await page.locator('[data-ws-check]', {hasText: 'Check again'}).waitFor();
    assert.match(await page.locator('[data-ws-check-result]').innerText(), /Still not accepted/);
    assert.deepEqual(seen.errors, []);
  },

  'a check paused on another page resumes, unless its ten minutes passed while away': async browser => {
    const fake = googleFake();
    fake.state.workspace = row('pending');
    const {page, seen} = await open(browser, fake, {clock: true});
    const checks = () => writesTo(fake, '/api/v2/google/workspace/check');
    await page.locator('[data-ws-check]').click();
    await until(() => checks().length === 1, 'the first check');
    let timer = await nextCheckTimer(page, null, 'the timer after the first check');
    await away(page);
    await page.clock.runFor(300000);
    await heldShows(page, /Still not accepted/);
    assert.equal(checks().length, 1, 'the hidden card does not check');
    assert.equal(await page.evaluate(() => MB_GOOGLE_HOST._googleWatch.timer), null);
    assert.doesNotMatch(await heldResult(page), /Checking again every 20 seconds/);
    await back(page);
    await until(() => checks().length === 2, 'the immediate check on returning');
    timer = await nextCheckTimer(page, timer, 'the timer after returning');
    assert.match(await page.locator('[data-ws-check-result]').innerText(), /Checking again every 20 seconds/);
    await page.clock.runFor(20000);
    await until(() => checks().length === 3, 'the next check after 20 s');
    await nextCheckTimer(page, timer, 'the timer after the next check');
    await away(page);
    // Another five minutes passes the original deadline, but not a new ten minutes from the resumed check.
    await page.clock.runFor(300000);
    await heldShows(page, /Still not accepted/);
    await back(page);
    await page.locator('[data-ws-check]', {hasText: 'Check again'}).waitFor();
    assert.match(await page.locator('[data-ws-check-result]').innerText(), /Still not accepted/);
    assert.doesNotMatch(await page.locator('[data-ws-check-result]').innerText(), /Checking again every 20 seconds/);
    assert.equal(await page.locator('#set-google').evaluate(host => host._googleWatch.timer), null);
    await page.clock.runFor(20000);
    assert.equal(checks().length, 3, 'returning after the original deadline does not restart checking');
    assert.deepEqual(seen.errors, []);
  },

  'a check paused on another page is dropped when another admin replaces the pending key': async browser => {
    const fake = googleFake();
    fake.state.workspace = row('connected');
    fake.state.pending = row('pending', {generation: 2});
    const {page, seen} = await open(browser, fake, {clock: true});
    const checks = () => writesTo(fake, '/api/v2/google/workspace/check');
    await page.locator('[data-ws-check]').click();
    await until(() => checks().length === 1, 'the first check');
    await nextCheckTimer(page, null, 'the timer after the first check');
    await away(page);
    await page.clock.runFor(300000);
    await heldShows(page, /Still not accepted/);
    assert.equal(checks().length, 1, 'the hidden card does not check');
    // Another admin uploads a newer pending key while this card is hidden.
    fake.state.pending = row('pending', {generation: 3});
    await back(page);
    await page.waitForFunction(() => document.querySelector('#set-google')?._googleState?.g.pending?.generation === 3);
    assert.equal(checks().length, 1, 'returning with a newer key does not resume the old watch');
    assert.doesNotMatch(await page.locator('[data-ws-check-result]').innerText(), /Checking again every 20 seconds/);
    assert.equal(await page.locator('#set-google').evaluate(host => host._googleWatch), null);
    await page.clock.runFor(20000);
    assert.equal(checks().length, 1, 'the old watch stays stopped after another 20 s');
    assert.deepEqual(seen.errors, []);
  },

  'a server that Cloud Shell cannot reach offers the upload only': async browser => {
    const fake = googleFake();
    fake.state.setup = {base: 'https://tico-ui.test', reachable: false};
    const {page, seen} = await open(browser, fake);
    await pick(page, 'workspace');
    assert.equal(await page.locator('[data-ws-command]').count(), 0);
    assert.equal(await page.locator('[data-google-how]').count(), 0, 'no command, so no agent prompt either');
    assert.match(await page.locator('[data-ws-step=create]').innerText(), /Cloud Shell can't reach this server/);
    assert.equal(await page.locator('[data-ws-file]').count(), 1);
    assert.deepEqual(seen.errors, []);
  },

  'a member sees only their own account, never the ways or the setup': async browser => {
    const fake = googleFake();
    fake.state.can_admin = false;
    let {page, seen} = await open(browser, fake);
    assert.equal(await page.locator('[data-google-mode]').count(), 0);
    assert.equal(await page.locator('[data-google-card=workspace]').count(), 0);
    assert.equal(await page.locator('[data-google-how], [data-oauth-setup]').count(), 0);
    assert.match(await page.locator('[data-google-card=account]').innerText(), /Ask an admin to connect Google in Tools › Settings\./);
    assert.deepEqual(seen.errors, []);
    await page.close();
    // With Google sign-in set up: their own Connect Google, no heading, and no team list.
    fake.state.oauth = {...fake.state.oauth, configured: true, source: 'settings', client_id: 'acme-client-id'};
    ({page, seen} = await open(browser, fake));
    assert.equal(await page.locator('#set-google h3').count(), 0);
    assert.equal(await page.locator('[data-google-disconnect]').count(), 1);
    assert.equal(await page.locator('.google-team, [data-oauth-remove], [data-google-mode]').count(), 0);
    assert.equal(fake.peopleReads, 0, 'a member never reads the team list');
    assert.deepEqual(seen.errors, []);
    await page.close();
    // Covered by the company's key: the card draws nothing (Your mailbox above it says it is connected by the company).
    fake.state.me = {mailbox: 'ana@acme.example', covered_by_workspace: true};
    ({page, seen} = await open(browser, fake, {ready: null}));
    assert.equal((await page.locator('#set-google').innerHTML()).trim(), '');
    assert.deepEqual(seen.errors, []);
  },

  'the OAuth client panel saves, and the console links need a valid project': async browser => {
    const fake = googleFake();
    const {page, seen} = await open(browser, fake);
    await pick(page, 'accounts', {self: true});
    await page.locator('[data-oauth-project]').fill('Bad Project!');
    assert.equal(await page.locator('[data-oauth-link=overview]').getAttribute('href'), null);
    await page.locator('[data-oauth-project]').fill('tico-mail-a1b2c3');
    assert.equal(await page.locator('[data-oauth-link=overview]').getAttribute('href'),
      'https://console.cloud.google.com/auth/overview?project=tico-mail-a1b2c3');
    assert.equal(await page.locator('[data-oauth-link=clients]').getAttribute('href'),
      'https://console.cloud.google.com/auth/clients/create?project=tico-mail-a1b2c3');
    assert.equal(await page.locator('[data-oauth-link=gmail]').getAttribute('href'),
      'https://console.cloud.google.com/apis/library/gmail.googleapis.com?project=tico-mail-a1b2c3');
    assert.equal(await page.locator('[data-oauth-link=calendar]').getAttribute('href'),
      'https://console.cloud.google.com/apis/library/calendar-json.googleapis.com?project=tico-mail-a1b2c3');
    await page.locator('[name=client_id]').fill('acme-client-id');
    await page.locator('[name=client_secret]').fill('acme-secret');
    await page.locator('button[type=submit]').click();
    await until(() => writesTo(fake, '/api/v2/google/oauth-client', 'PUT').length === 1, 'the OAuth client save');
    assert.equal(writesTo(fake, '/api/v2/google/oauth-client', 'PUT')[0].body.client_id, 'acme-client-id');
    await page.locator('[data-oauth-form]').waitFor({state: 'detached'});
    // Step 1 is done; step 2 fills in.
    await page.locator('[data-acct-step=signin] [data-oauth-remove]').waitFor();
    await page.locator('[data-acct-step=you] [data-google-disconnect]').waitFor();
    assert.deepEqual(seen.errors, []);
  },

  'replace key goes to step 1, and Cancel returns to the connected key': async browser => {
    const fake = googleFake();
    fake.state.workspace = row('connected');
    const {page, seen} = await open(browser, fake);
    await page.locator('[data-ws-replace]').click();
    await page.locator('[data-ws-step=create]').waitFor();
    await page.locator('[data-ws-replace-cancel]').click();
    await page.locator('[data-ws-disconnect]').waitFor();
    assert.equal(writesTo(fake, '/api/v2/google/workspace/setup-code').length, 0, 'replacing makes no code by itself');
    assert.deepEqual(seen.errors, []);
  },

  'disconnect confirms in the page, never in a browser dialog': async browser => {
    const fake = googleFake();
    fake.state.workspace = row('connected');
    const {page, seen} = await open(browser, fake);
    await page.locator('[data-ws-disconnect]').click();
    await page.locator('[data-ws-disconnect-confirm]').waitFor();
    assert.equal(seen.dialogs, 0);
    await page.locator('[data-ws-disconnect-yes]').click();
    await page.locator('[data-ws-step=create]').waitFor();
    assert.equal(writesTo(fake, '/api/v2/google/workspace', 'DELETE').length, 1);
    assert.equal(seen.dialogs, 0);
    assert.deepEqual(seen.errors, []);
  },

  'a replacement key shows its steps under the connected key': async browser => {
    const fake = googleFake();
    fake.state.workspace = row('connected');
    fake.state.pending = row('pending');
    const {page, seen} = await open(browser, fake);
    assert.equal(await page.locator('[data-ws-disconnect]').count(), 1);
    assert.equal(await page.locator('[data-ws-step=allow]').count(), 1);
    assert.equal(await page.locator('[data-ws-step=check]').count(), 1);
    assert.equal(await page.locator('[data-ws-step=create]').count(), 0);
    assert.deepEqual(seen.errors, []);
  },

  'a replacement ends when its new key arrives, and the Allow step and Check show': async browser => {
    const fake = googleFake();
    fake.state.workspace = row('connected');
    const {page, seen} = await open(browser, fake);
    await page.locator('[data-ws-replace]').click();
    await page.locator('[data-ws-step=create]').waitFor();
    // The script uploads the new key on the computer; the replacement keeps polling until it lands.
    fake.state.pending = row('pending', {generation: 2});
    await page.locator('[data-ws-step=allow]').waitFor({timeout: 15000});
    await page.locator('[data-ws-check]').waitFor();
    assert.equal(await page.locator('[data-ws-step=create]').count(), 0, 'the replacement is over');
    assert.equal(await page.locator('[data-ws-disconnect]').count(), 1, 'the connected key stays until the new one is checked');
    assert.deepEqual(seen.errors, []);
  },

  'disconnect clears the setup command, so the next setup shows no code': async browser => {
    const fake = googleFake();
    fake.state.workspace = row('connected');
    const {page, seen} = await open(browser, fake);
    await page.locator('[data-ws-replace]').click();
    await bySelf(page, 'workspace');
    await page.locator('[data-ws-command]').click();
    await page.locator('[data-ws-step=create] code', {hasText: 'bash -s -- code1'}).waitFor();
    await page.locator('[data-ws-replace-cancel]').click();
    await page.locator('[data-ws-disconnect]').click();
    await page.locator('[data-ws-disconnect-yes]').click();
    await page.locator('[data-ws-step=create]').waitFor();
    assert.equal(await page.locator('[data-ws-step=create] code').count(), 0, 'the old code is not shown again');
    assert.equal(writesTo(fake, '/api/v2/google/workspace', 'DELETE').length, 1);
    assert.deepEqual(seen.errors, []);
  },

  'a key that arrives for a command clears its code, so the next replacement shows none': async browser => {
    const fake = googleFake();
    fake.state.workspace = row('connected');
    const {page, seen} = await open(browser, fake);
    await page.locator('[data-ws-replace]').click();
    await bySelf(page, 'workspace');
    await page.locator('[data-ws-command]').click();
    await page.locator('[data-ws-step=create] code', {hasText: 'bash -s -- code1'}).waitFor();
    fake.state.pending = row('pending', {generation: 2});
    await page.locator('[data-ws-step=allow]').waitFor({timeout: 15000});
    await page.locator('[data-ws-replace]').click();
    await page.locator('[data-ws-step=create]').waitFor();
    assert.equal(await page.locator('[data-ws-step=create] code').count(), 0, 'the used code is gone');
    assert.doesNotMatch(await page.locator('[data-google-card=workspace]').innerText(), /code1/);
    assert.deepEqual(seen.errors, []);
  },

  'a first key that never connected can be removed in the page, with a confirm': async browser => {
    const fake = googleFake();
    fake.state.workspace = row('pending');
    const {page, seen} = await open(browser, fake);
    await page.locator('[data-ws-step=allow]').waitFor();
    await page.locator('[data-ws-disconnect]').click();
    await page.locator('[data-ws-disconnect-confirm]').waitFor();
    assert.match(await page.locator('[data-ws-disconnect-confirm]').innerText(), /Remove the uploaded key for acme\.example/);
    assert.equal(seen.dialogs, 0);
    await page.locator('[data-ws-disconnect-yes]').click();
    await page.locator('[data-ws-step=create]').waitFor();
    assert.equal(await page.locator('[data-ws-step=allow]').count(), 0);
    assert.equal(writesTo(fake, '/api/v2/google/workspace', 'DELETE').length, 1);
    assert.equal(fake.state.workspace, null);
    assert.deepEqual(seen.errors, []);
  },

  'a replacement made while a key is pending waits for a newer key, then its Allow step and Check show': async browser => {
    const fake = googleFake();
    fake.state.workspace = row('connected', {generation: 1});
    fake.state.pending = row('pending', {generation: 2});
    const {page, seen} = await open(browser, fake);
    await page.locator('[data-ws-replace]').click();
    await page.locator('[data-ws-step=create]').waitFor();
    // The waiting key is no newer than the click, so the replacement goes on; the script's newer key ends it.
    fake.state.pending = row('pending', {generation: 3});
    await page.locator('[data-ws-step=allow]').waitFor({timeout: 15000});
    await page.locator('[data-ws-check]').waitFor();
    assert.equal(await page.locator('[data-ws-step=create]').count(), 0, 'the replacement is over');
    assert.equal(await page.locator('[data-ws-disconnect]').count(), 1, 'the connected key stays until the new one is checked');
    assert.deepEqual(seen.errors, []);
  },

  'a command made while a key is pending is used up by its key, even when that lands while the tab is hidden': async browser => {
    const fake = googleFake();
    fake.state.workspace = row('connected', {generation: 1});
    fake.state.pending = row('pending', {generation: 2});
    const {page, seen} = await open(browser, fake);
    await page.locator('[data-ws-replace]').click();
    await bySelf(page, 'workspace');
    await page.locator('[data-ws-command]').click();
    await page.locator('[data-ws-step=create] code', {hasText: 'bash -s -- code1'}).waitFor();
    // Settings is left: nothing reads the state while the tab is hidden. The script's key uploads and is checked meanwhile.
    await page.evaluate(() => { document.querySelector('#set-google').hidden = true; });
    fake.state.pending = row('pending', {generation: 3});
    fake.state.workspace = row('connected', {generation: 3});
    fake.state.pending = null;
    // Settings comes back: its visit re-reads the state first, and that read uses up the command.
    await page.evaluate(() => { document.querySelector('#set-google').hidden = false; });
    await page.evaluate(() => window.mountGoogleConnect(document.querySelector('#set-google')));
    await page.locator('[data-ws-replace]').click();
    await page.locator('[data-ws-step=create]').waitFor();
    assert.equal(await page.locator('[data-ws-step=create] code').count(), 0, 'the used command shows no code');
    assert.doesNotMatch(await page.locator('[data-google-card=workspace]').innerText(), /code1/);
    assert.deepEqual(seen.errors, []);
  },

  'a failing key shows its error and a Check': async browser => {
    const fake = googleFake();
    fake.state.workspace = row('failing', {last_error: 'Google stopped accepting the key.'});
    const {page, seen} = await open(browser, fake);
    assert.match(await page.locator('[data-google-card=workspace] [role=alert]').first().innerText(), /Google stopped accepting the key\./);
    assert.equal(await page.locator('[data-ws-check]').count(), 1);
    assert.deepEqual(seen.errors, []);
  },

  'an admin removes the Google app in the page, with no browser dialog': async browser => {
    const fake = googleFake();
    fake.state.oauth = {configured: true, source: 'settings', redirect_uri: fake.state.oauth.redirect_uri, client_id: 'acme-client-id'};
    const {page, seen} = await open(browser, fake);
    await page.locator('[data-oauth-remove]').click();
    await page.locator('[data-oauth-remove-confirm]').waitFor();
    await page.locator('[data-oauth-remove-yes]').click();
    await page.locator('[data-acct-step=signin] [data-google-prompt=accounts]').waitFor();
    assert.equal(writesTo(fake, '/api/v2/google/oauth-client', 'DELETE').length, 1);
    assert.equal(seen.dialogs, 0);
    assert.deepEqual(seen.errors, []);
  },

  'me null renders without the personal actions': async browser => {
    const fake = googleFake();
    fake.state.me = null;
    fake.state.oauth = {configured: true, source: 'env', redirect_uri: fake.state.oauth.redirect_uri, client_id: null};
    const {page, seen} = await open(browser, fake);
    assert.match(await page.locator('[data-google-card=account]').innerText(), /Sign in as a person to connect your own mailbox\./);
    assert.equal(await page.locator('[data-google-connect]').count(), 0);
    assert.deepEqual(await cards(page), ['account'], 'Google sign-in is set up, so that path shows alone');
    assert.deepEqual(seen.errors, []);
  },

  'a mailbox covered by the Workspace says so, and keeps its own actions': async browser => {
    const fake = googleFake();
    fake.state.me = {mailbox: 'ana@acme.example', covered_by_workspace: true};
    fake.state.oauth = {configured: true, source: 'settings', redirect_uri: fake.state.oauth.redirect_uri, client_id: 'acme-client-id'};
    const {page, seen} = await open(browser, fake);
    assert.match(await page.locator('[data-google-card=account]').innerText(), /Covered by the Workspace connection\./);
    assert.equal(await page.locator('[data-google-disconnect]').count(), 1);
    assert.deepEqual(seen.errors, []);
  },

  'typed setup fields survive a switch to the agent prompt and back, and the secret goes once saved': async browser => {
    const fake = googleFake();
    const {page, seen} = await open(browser, fake);
    await pick(page, 'accounts', {self: true});
    await page.locator('[data-oauth-project]').fill('tico-mail-a1b2c3');
    await page.locator('[name=client_id]').fill('acme-client-id');
    await page.locator('[name=client_secret]').fill('acme-secret');
    await page.locator('[data-google-how=agent][data-google-path=accounts]').click();
    await page.locator('[data-google-prompt=accounts]').waitFor();
    assert.equal(await page.locator('[data-oauth-form]').count(), 0);
    await bySelf(page, 'accounts');
    assert.equal(await page.locator('[data-oauth-project]').inputValue(), 'tico-mail-a1b2c3');
    assert.equal(await page.locator('[name=client_id]').inputValue(), 'acme-client-id');
    assert.equal(await page.locator('[name=client_secret]').inputValue(), 'acme-secret');
    await page.locator('button[type=submit]').click();
    await until(() => writesTo(fake, '/api/v2/google/oauth-client', 'PUT').length === 1, 'the OAuth client save');
    await page.locator('[data-acct-step=signin] [data-oauth-remove]').waitFor();
    const kept = await page.locator('#set-google').evaluate(el => el._googleKeep || {});
    assert.ok(!kept['client-secret'], 'the secret is not kept after a save');
    assert.ok(!kept['client-id']);
    assert.deepEqual(seen.errors, []);
  },

  'a Workspace authorized before the read-only scopes redoes step 2 and Check on the same key': async browser => {
    const fake = googleFake();
    fake.state.workspace = row('connected', {narrowable: false});
    let {page, seen} = await open(browser, fake);
    assert.equal((await page.locator('[data-google-may-narrow]').innerText()).trim(), 'Redo step 2 to narrow Google access.');
    // Step 2 for the active key: its client ID and every scope, the link to Google Admin, then Check. No Replace.
    const allow = page.locator('[data-google-card=workspace] [data-ws-step=allow]');
    await allow.waitFor();
    assert.match(await allow.innerText(), /Authorize in Google Admin/);
    assert.equal(await allow.locator('a.primary').getAttribute('href'), fake.state.workspace.delegation_url);
    const copied = await allow.locator('[data-copy]').evaluateAll(els => els.map(el => el.dataset.copy));
    assert.deepEqual(copied, [fake.state.workspace.client_id, fake.state.workspace.scopes.join(',')]);
    assert.equal(await page.locator('[data-ws-step=create]').count(), 0, 'the key is kept');
    fake.checks.push('ok');
    await page.locator('[data-ws-check]').click();
    await page.locator('[data-google-may-narrow]').waitFor({state: 'detached'});
    assert.equal(await page.locator('[data-ws-step=allow]').count(), 0);
    assert.equal(await page.locator('[data-ws-step=check]').count(), 0);
    assert.equal(fake.writes.filter(w => w.path === '/api/v2/google/workspace/check').length, 1);
    assert.deepEqual(seen.errors, []);
    await page.close();
    fake.state.workspace = row('connected', {narrowable: true});
    ({page, seen} = await open(browser, fake));
    await page.locator('[data-ws-connected]').waitFor();
    assert.equal(await page.locator('[data-google-may-narrow]').count(), 0);
    assert.deepEqual(seen.errors, []);
  },

  'phone width: the command and the steps fit with no sideways scroll': async browser => {
    const fake = googleFake();
    fake.state.workspace = row('pending');
    const {page, seen} = await open(browser, fake, {width: 390});
    await page.locator('[data-ws-step=allow]').waitFor();
    await page.locator('[data-ws-check]').waitFor();
    const widths = await page.evaluate(() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth}));
    assert.ok(widths.scroll <= widths.client, `page scrolls sideways: ${widths.scroll} > ${widths.client}`);
    assert.deepEqual(seen.errors, []);
  },
};

(async () => {
  const browser = await launch();
  try {
    for (const [name, run] of Object.entries(scenarios)) {
      await run(browser);
      console.log(`ok  ${name}`);
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
