// Offline browser regression: Settings > Google (ui/google-connect.js) over the contract's GET /v2/google, with a fake
// hub (ui/tests/support/google-fake.cjs). An admin sets up the Whole Workspace key in three steps, a member sees the
// account card only, the check polls on a 20 s timer for 10 minutes (Playwright's fake clock), disconnect and replace
// confirm in the page, and a phone width has no sideways scroll.
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

// Opens Settings > Google on a fresh page. `clock` installs Playwright's fake timers before the page loads.
async function open(browser, fake, {width = 1440, clock = false} = {}) {
  const page = await browser.newPage({viewport: {width, height: 900}, serviceWorkers: 'block'});
  const seen = {errors: [], dialogs: 0};
  page.on('pageerror', error => seen.errors.push(error.message));
  page.on('dialog', dialog => { seen.dialogs += 1; void dialog.accept(); });
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
  await page.goto('https://tico-ui.test/#/settings');
  await page.locator('[data-settings-tab=inbox]').click();
  await page.locator('#set-google [data-google-card]').first().waitFor();
  return {page, seen};
}

const until = async (check, what, ms = 8000) => {
  const end = Date.now() + ms;
  while (!check()) {
    if (Date.now() > end) throw new Error(`Timed out waiting for ${what}`);
    await new Promise(resolve => setTimeout(resolve, 50));
  }
};
const writesTo = (fake, path, method = 'POST') => fake.writes.filter(write => write.path === path && write.method === method);
const KEY = {type: 'service_account', private_key_id: 'KEYMARK-7731', client_email: 'tico-mail@tico-mail-a1b2c3.iam.gserviceaccount.com'};
const keyFile = {name: 'key.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(KEY))};

const scenarios = {
  'admin starts at step 1 under the Google tab': async browser => {
    const fake = googleFake();
    const {page, seen} = await open(browser, fake);
    assert.equal((await page.locator('[data-settings-tab=inbox]').innerText()).trim(), 'Google');
    assert.equal((await page.locator('#settings-inbox h2').innerText()).trim(), 'Google');
    assert.equal(await page.locator('[data-ws-step=create]').count(), 1);
    assert.equal(await page.locator('[data-ws-command]').count(), 1);
    assert.equal(await page.locator('[data-oauth-setup]').count(), 1, 'no OAuth client yet, so the guided panel shows');
    assert.deepEqual(seen.errors, []);
  },

  'New command makes one code, and re-opening the tab makes none': async browser => {
    const fake = googleFake();
    const {page, seen} = await open(browser, fake);
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
    await page.clock.runFor(20000);
    await until(() => checks().length === 2, 'the second check after 20 s');
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
    for (let tick = 1; tick <= 30; tick += 1) {
      await page.clock.runFor(20000);
      await until(() => writesTo(fake, '/api/v2/google/workspace/check').length === tick + 1, `check ${tick + 1}`);
    }
    await page.locator('[data-ws-check]', {hasText: 'Check again'}).waitFor();
    assert.match(await page.locator('[data-ws-check-result]').innerText(), /Still not accepted/);
    assert.deepEqual(seen.errors, []);
  },

  'a server that Cloud Shell cannot reach offers the upload only': async browser => {
    const fake = googleFake();
    fake.state.setup = {base: 'https://tico-ui.test', reachable: false};
    const {page, seen} = await open(browser, fake);
    assert.equal(await page.locator('[data-ws-command]').count(), 0);
    assert.match(await page.locator('[data-ws-step=create]').innerText(), /Cloud Shell can't reach this server/);
    assert.equal(await page.locator('[data-ws-file]').count(), 1);
    assert.deepEqual(seen.errors, []);
  },

  'a member sees only the account card, and is asked to ask an admin': async browser => {
    const fake = googleFake();
    fake.state.can_admin = false;
    const {page, seen} = await open(browser, fake);
    assert.equal(await page.locator('[data-google-card=workspace]').count(), 0);
    assert.match(await page.locator('[data-google-card=account]').innerText(), /Ask an admin to set up Google\./);
    assert.deepEqual(seen.errors, []);
  },

  'the OAuth client panel saves, and the console links need a valid project': async browser => {
    const fake = googleFake();
    const {page, seen} = await open(browser, fake);
    await page.locator('[data-oauth-project]').fill('Bad Project!');
    assert.equal(await page.locator('[data-oauth-link=overview]').getAttribute('href'), null);
    await page.locator('[data-oauth-project]').fill('tico-mail-a1b2c3');
    assert.equal(await page.locator('[data-oauth-link=overview]').getAttribute('href'),
      'https://console.cloud.google.com/auth/overview?project=tico-mail-a1b2c3');
    assert.equal(await page.locator('[data-oauth-link=clients]').getAttribute('href'),
      'https://console.cloud.google.com/auth/clients/create?project=tico-mail-a1b2c3');
    await page.locator('[name=client_id]').fill('acme-client-id');
    await page.locator('[name=client_secret]').fill('acme-secret');
    await page.locator('button[type=submit]').click();
    await until(() => writesTo(fake, '/api/v2/google/oauth-client', 'PUT').length === 1, 'the OAuth client save');
    assert.equal(writesTo(fake, '/api/v2/google/oauth-client', 'PUT')[0].body.client_id, 'acme-client-id');
    await page.locator('[data-oauth-form]').waitFor({state: 'detached'});
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
    await page.locator('[data-oauth-setup]').waitFor();
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
    assert.equal(await page.locator('[data-google-card=workspace]').count(), 1);
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
