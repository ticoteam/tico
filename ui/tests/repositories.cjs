// Offline regression for Settings > Repositories and a bot's Repositories (docs: PLAN sections 1 and 2). Fixtures only.
//  - the list shows ticked and unticked repositories, hides bot repos until asked, marks a setup command read from
//    tico.json or conductor.json and a repository GitHub no longer lists; a tick, a setup command, New bots get and
//    Refresh each send their request;
//  - the bot editor's Repositories: Own repo only, All ticked repos with Read or Write, Chosen repos with Read or Write
//    per repository, each change a PUT with the whole choice; Effective lists what the bot reaches;
//  - a member sees the same, read only; with GitHub not connected the page is one line.
// REPO_SHOTS=<dir> also saves the screenshots the owner approves.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const SHOTS = process.env.REPO_SHOTS || '';

const repo = (full_name, extra = {}) => ({full_name, enabled: false, bot_repo: false, default_branch: 'main',
  setup_command: '', setup_source: null, reachable: true, last_seen: '2026-10-01T09:00:00Z', ...extra});
function fixtures() {
  return {
    repositories: [
      repo('acme/web', {enabled: true, setup_command: 'pnpm install', setup_source: 'tico.json'}),
      repo('acme/api', {enabled: true, setup_command: 'uv sync', setup_source: 'conductor.json'}),
      repo('acme/mobile', {enabled: true, default_branch: 'develop'}),
      repo('acme/infra', {setup_command: 'make setup', setup_source: 'settings'}),
      repo('acme/design-system', {enabled: true, setup_command: 'npm ci', setup_source: 'settings'}),
      repo('acme/docs-site'),
      repo('acme/legacy-billing', {default_branch: 'master', reachable: false}),
      repo('acme/data-pipeline', {enabled: true, setup_command: 'make bootstrap', setup_source: 'tico.json'}),
      repo('acme/sdk-js', {enabled: true, setup_command: 'npm ci', setup_source: 'tico.json'}),
      repo('acme/sdk-python', {setup_command: 'uv sync', setup_source: 'tico.json'}),
      repo('acme/marketing-site', {setup_command: 'bun install', setup_source: 'conductor.json'}),
      repo('acme/bot-release-captain', {bot_repo: true}),
      repo('acme/bot-support', {bot_repo: true}),
    ],
    new_bot_default: 'own', github_connected: true,
    bot: {mode: 'chosen', all_access: 'write', chosen: [{full_name: 'acme/web', access: 'write'}, {full_name: 'acme/api', access: 'read'}]},
  };
}
const effective = (data) => {
  const own = [{full_name: 'acme/bot-release-captain', access: 'write'}];
  const b = data.bot;
  if (b.mode === 'all') return own.concat(data.repositories.filter(r => r.enabled && !r.bot_repo).map(r => ({full_name: r.full_name, access: b.all_access})));
  if (b.mode === 'chosen') return own.concat(b.chosen);
  return own;
};

async function open(browser, {role = 'owner', connected = true, viewport = {width: 1440, height: 900}, theme = 'dark'} = {}) {
  const data = fixtures(); data.github_connected = connected;
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors = [], writes = [];
  await page.addInitScript(t => { try { localStorage.setItem('tico.theme', t); } catch {} }, theme);
  const me = {id: role === 'owner' ? 'ana' : 'sam', role, name: role === 'owner' ? 'Ana' : 'Sam', email: 'ana@example.com', cloud: true, registered: true};
  const bot = {name: 'release-captain', display_name: 'Release Captain', status: 'active', state: 'active', operator: 'ana',
    repo: 'bot-release-captain', can_manage: true, revision: 3, users: [{id: 'ana', name: 'Ana'}], bot_owners: [{id: 'sam', name: 'Sam'}],
    my_access: {see: true, read: true, write: true}, access_policy: {}};
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    // The icon font, so the screenshots show icons rather than their names.
    if (p.startsWith('/vendor/fonts/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile(p.slice(1)))});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json(me);
    if (p === '/api/employees') return json([bot]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'sam', name: 'Sam'}], teams: {}});
    if (p === '/api/issues') return json([]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/operations') return json({machines: [], services: [], agents: []});
    if (p === '/api/v2/models') return json({models: []});
    if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
    const list = () => ({repositories: data.repositories, new_bot_default: data.new_bot_default, github_connected: data.github_connected});
    if (p === '/api/v2/repositories') return json(list());
    if (p === '/api/v2/github/product-repos/preview' && method === 'GET') {
      const name = url.searchParams.get('name');
      return json({org: 'acme', name, repository: `acme/${name}`, visibility: 'private', auto_init: false,
        capability: 'available', capability_detail: 'Administration: write verified'});
    }
    if (p === '/api/v2/github/product-repos' && method === 'POST') {
      const body = req.postDataJSON(); writes.push({p, body});
      return json({repository: `${body.org}/${body.name}`, html_url: `https://github.com/${body.org}/${body.name}`,
        installation_access: 'available', note: 'No bot access was granted.'});
    }
    if (p === '/api/v2/repositories/refresh' && method === 'POST') { writes.push({p, body: {}}); return json(list()); }
    if (p === '/api/v2/repositories/settings' && method === 'PUT') {
      const body = req.postDataJSON(); writes.push({p, body}); data.new_bot_default = body.new_bot_default; return json({new_bot_default: body.new_bot_default});
    }
    let m;
    if ((m = p.match(/^\/api\/v2\/repositories\/([^/]+)\/([^/]+)$/)) && method === 'PUT') {
      const body = req.postDataJSON(), name = `${decodeURIComponent(m[1])}/${decodeURIComponent(m[2])}`;
      writes.push({p, body});
      const row = data.repositories.find(r => r.full_name === name);
      Object.assign(row, body, body.setup_command !== undefined ? {setup_source: body.setup_command ? 'settings' : null} : {});
      return json(row);
    }
    if (p === '/api/v2/bots/release-captain/repositories') {
      if (method === 'PUT') {
        const body = req.postDataJSON(); writes.push({p, body});
        if (data.refuse) return json({error: {code: 'forbidden', detail: data.refuse}}, 403);
        if (data.refuseOnce) { const why = data.refuseOnce; data.refuseOnce = ''; return json({error: {code: 'forbidden', detail: why}}, 403); }
        data.inflight = (data.inflight || 0) + 1; data.maxInflight = Math.max(data.maxInflight || 0, data.inflight);
        if (data.delay) { const wait = data.delay; data.delay = 0; await new Promise(done => setTimeout(done, wait)); }
        data.inflight -= 1;
        data.bot = {...data.bot, ...body};
      }
      if (method === 'GET' && data.slowGet) { const wait = data.slowGet; data.slowGet = 0; await new Promise(done => setTimeout(done, wait)); }
      return json({...data.bot, effective: effective(data)});         // GET and PUT both answer the whole access (backend access())
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json({});
  });
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('https://tico-ui.test/#/repositories');
  await page.waitForFunction(() => S.route === '#/settings' && SETTINGS_TAB === 'repos');
  return {page, errors, writes, data};
}
const shot = async (page, name) => { if (SHOTS) await page.screenshot({path: path.join(SHOTS, name), fullPage: false}); };
const lastWrite = (writes, p) => [...writes].reverse().find(w => w.p === p)?.body;

async function owner(browser) {
  const {page, errors, writes} = await open(browser);
  const rows = page.locator('#set-repos .repo-row');
  await rows.first().waitFor();
  // Bot repos stay out of the list until asked for.
  assert.equal(await rows.count(), 11);
  assert.equal(await page.locator('#set-repos [data-repo="acme/bot-support"]').count(), 0);
  assert.equal(await page.locator('#set-repos [data-repos-filter]').isVisible(), true, 'the filter shows past ten repositories');
  const web = page.locator('#set-repos [data-repo="acme/web"]');
  assert.equal(await web.locator('[data-repo-tick]').isChecked(), true);
  assert.equal(await web.locator('.repo-branch').innerText(), 'main');
  assert.equal(await web.locator('[data-repo-setup]').inputValue(), 'pnpm install');
  assert.equal(await web.locator('.repo-src').innerText(), 'tico.json');
  assert.equal(await page.locator('#set-repos [data-repo="acme/infra"] .repo-src').count(), 0, 'no hint when the command was set here');
  assert.match(await page.locator('#set-repos [data-repo="acme/legacy-billing"]').innerText(), /Not reachable/);
  assert.equal(await page.locator('#set-repos [data-repo="acme/web"] .repo-warn').count(), 0);
  await shot(page, 'settings-repositories-desktop-dark.png');
  await page.locator('[data-theme-choice=light]').click();
  await page.waitForTimeout(150);
  await shot(page, 'settings-repositories-desktop-light.png');
  await page.locator('[data-theme-choice=dark]').click();

  await page.locator('#set-repos [data-repos-bots]').check();
  assert.equal(await rows.count(), 13);
  assert.match(await page.locator('#set-repos [data-repo="acme/bot-support"]').innerText(), /bot/);
  await page.locator('#set-repos [data-repos-bots]').uncheck();
  assert.equal(await rows.count(), 11);

  // A tick, a setup command (Enter saves), New bots get and Refresh each send their request.
  await page.locator('#set-repos [data-repo="acme/infra"] [data-repo-tick]').check();
  await page.waitForFunction(() => REPOS.repositories.find(r => r.full_name === 'acme/infra').enabled);
  assert.deepEqual(lastWrite(writes, '/api/v2/repositories/acme/infra'), {enabled: true});
  assert.equal(await page.locator('#set-repos [data-repo="acme/infra"] [data-repo-tick]').isChecked(), true);
  const webSetup = page.locator('#set-repos [data-repo="acme/web"] [data-repo-setup]');
  await webSetup.fill('pnpm install --frozen-lockfile'); await webSetup.press('Enter');
  await page.waitForFunction(() => REPOS.repositories.find(r => r.full_name === 'acme/web').setup_source === 'settings');
  assert.deepEqual(lastWrite(writes, '/api/v2/repositories/acme/web'), {setup_command: 'pnpm install --frozen-lockfile'});
  assert.equal(await page.locator('#set-repos [data-repo="acme/web"] .repo-src').count(), 0, 'set here now: the file hint goes');
  await page.locator('#set-repos label:has(input[name=repos_new_bot][value=all])').click();
  await page.waitForFunction(() => REPOS.new_bot_default === 'all');
  assert.deepEqual(lastWrite(writes, '/api/v2/repositories/settings'), {new_bot_default: 'all'});
  await page.locator('#set-repos [data-repos-refresh]').click();
  await page.waitForFunction(() => document.querySelector('#set-repos [data-repos-refresh]')?.textContent === 'Refresh');
  assert.ok(writes.some(w => w.p === '/api/v2/repositories/refresh'));
  assert.equal(await page.locator('#set-repos input[name=repos_new_bot][value=all]').isChecked(), true);
  await page.locator('#set-repos [data-repos-filter]').fill('sdk');
  assert.equal(await rows.count(), 2);
  await page.locator('#set-repos [data-repos-filter]').fill('');

  // The bot editor: Chosen repos lists the ticked repos (no bot repos), each with Read or Write.
  await page.evaluate(() => settingsEditBot('release-captain'));
  const box = page.locator('#bot-editor [data-bot-repos]');
  await box.locator('.brepo-row').first().waitFor();
  assert.equal(await box.locator('input[type=radio][value=chosen]').isChecked(), true);
  const picks = await box.locator('.brepo-row').evaluateAll(els => els.map(el => el.dataset.brepo));
  assert.deepEqual(picks, ['acme/api', 'acme/data-pipeline', 'acme/design-system', 'acme/infra', 'acme/mobile', 'acme/sdk-js', 'acme/web']);
  assert.equal(await box.locator('[data-brepo="acme/api"] input[value=read]').isChecked(), true);
  assert.equal(await box.locator('[data-brepo="acme/mobile"] input[value=write]').isDisabled(), true, 'Read or Write waits for the tick');
  assert.match(await box.locator('.brepo-eff').innerText(), /acme\/bot-release-captain write.*acme\/web write.*acme\/api read/s);
  await shot(page, 'bot-repositories-chosen-desktop-dark.png');
  await box.locator('[data-brepo="acme/mobile"] [data-brepo-pick]').check();
  await page.waitForFunction(() => document.querySelector('#bot-editor [data-brepo-status]')?.textContent === 'Saved');
  assert.deepEqual(lastWrite(writes, '/api/v2/bots/release-captain/repositories'), {mode: 'chosen', all_access: 'write', create_repositories: false,
    chosen: [{full_name: 'acme/web', access: 'write'}, {full_name: 'acme/api', access: 'read'}, {full_name: 'acme/mobile', access: 'write'}]});
  await box.locator('[data-brepo="acme/api"] label:has(input[value=write])').click();
  await page.waitForFunction(() => /acme\/api write/.test(document.querySelector('#bot-editor .brepo-eff')?.innerText || ''));
  assert.deepEqual(lastWrite(writes, '/api/v2/bots/release-captain/repositories').chosen.find(c => c.full_name === 'acme/api'), {full_name: 'acme/api', access: 'write'});
  await box.locator('[data-brepo="acme/web"] [data-brepo-pick]').uncheck();
  await page.waitForFunction(() => !/acme\/web/.test(document.querySelector('#bot-editor .brepo-eff')?.innerText || ''));
  assert.deepEqual(lastWrite(writes, '/api/v2/bots/release-captain/repositories').chosen.map(c => c.full_name), ['acme/api', 'acme/mobile']);
  // All ticked repos carries Read or Write; Own repo only leaves just the bot's own.
  await box.locator('label:has(input[type=radio][value=all])').first().click();
  await box.locator('.brepo-all').waitFor();
  assert.equal(await box.locator('.brepo-list').count(), 0);
  await page.waitForFunction(() => /acme\/sdk-js write/.test(document.querySelector('#bot-editor .brepo-eff')?.innerText || ''));
  assert.equal(lastWrite(writes, '/api/v2/bots/release-captain/repositories').mode, 'all');
  await box.locator('.brepo-all label:has(input[value=read])').click();
  await page.waitForFunction(() => /acme\/sdk-js read/.test(document.querySelector('#bot-editor .brepo-eff')?.innerText || ''));
  assert.deepEqual((({mode, all_access}) => ({mode, all_access}))(lastWrite(writes, '/api/v2/bots/release-captain/repositories')), {mode: 'all', all_access: 'read'});
  await box.locator('label:has(input[type=radio][value=own])').click();
  await page.waitForFunction(() => document.querySelector('#bot-editor .brepo-eff')?.innerText.trim() === 'Effective\nacme/bot-release-captain write'
    || /^Effective\s+acme\/bot-release-captain write$/.test(document.querySelector('#bot-editor .brepo-eff')?.innerText.trim() || ''));
  assert.equal(lastWrite(writes, '/api/v2/bots/release-captain/repositories').mode, 'own');
  // Keyboard: the mode is one radio group, so the arrow keys move it.
  await box.locator('input[type=radio][value=own]').focus();
  await page.keyboard.press('ArrowRight');
  await page.waitForFunction(() => document.querySelector('#bot-editor input[type=radio][value=all]')?.checked);
  assert.equal(lastWrite(writes, '/api/v2/bots/release-captain/repositories').mode, 'all');
  // Creation is a separate explicit grant; keyboard changes save and preserve focus.
  const creation = box.locator('[data-brepo-create]');
  assert.equal(await creation.isChecked(), false);
  await creation.focus();
  await page.keyboard.press('Space');
  await page.waitForFunction(() => document.querySelector('#bot-editor [data-brepo-status]')?.textContent === 'Saved');
  assert.equal(lastWrite(writes, '/api/v2/bots/release-captain/repositories').create_repositories, true);
  assert.equal(await creation.isChecked(), true);
  assert.equal(await creation.evaluate(el => el === document.activeElement), true);
  // The old free-text box is gone; Save bot no longer writes github-repos.
  assert.equal(await page.locator('#bot-editor textarea[name=extra_repos]').count(), 0);
  assert.deepEqual(errors, []);
  await page.close();
}

async function productCreate(browser) {
  const {page, errors, writes} = await open(browser);
  const section = page.locator('#set-repos [data-product-repo]');
  await section.waitFor();
  await section.locator('[data-product-repo-name]').fill('tico-recorder');
  await section.locator('[data-product-repo-preview]').click();
  await section.locator('[data-product-repo-create]').waitFor({state: 'visible'});
  assert.match(await section.locator('[data-product-repo-result]').innerText(), /acme\/tico-recorder · private · empty/);
  assert.deepEqual(writes.filter(w => w.p === '/api/v2/github/product-repos'), []);
  await section.locator('[data-product-repo-create]').click();
  await section.locator('[data-product-repo-result] a').waitFor();
  assert.equal(await section.locator('[data-product-repo-result] a').getAttribute('href'), 'https://github.com/acme/tico-recorder');
  assert.deepEqual(writes.filter(w => w.p === '/api/v2/github/product-repos').map(w => w.body), [
    {org: 'acme', name: 'tico-recorder', visibility: 'private', auto_init: false, confirmed: true},
  ]);
  assert.deepEqual(errors, []);
  await page.close();
}

// LOCAL-2: a refused save shows what the server holds, with the reason; LOCAL-4: saves go one at a time and the latest
// choice is the one that lands.
async function saves(browser) {
  const {page, errors, writes, data} = await open(browser);
  data.bot = {mode: 'all', all_access: 'write', chosen: []};
  await page.evaluate(() => settingsEditBot('release-captain'));
  const box = page.locator('#bot-editor [data-bot-repos]');
  await box.locator('.brepo-all').waitFor();
  const status = () => box.locator('[data-brepo-status]').innerText();
  // A refused mode change: back to All ticked repos, the reason kept.
  data.refuse = 'Only owners and admins change this';
  await box.locator('label:has(input[type=radio][value=own])').click();
  await page.waitForFunction(() => document.querySelector('#bot-editor input[type=radio][value=all]')?.checked);
  assert.equal(await box.locator('input[type=radio][value=own]').isChecked(), false);
  assert.equal(await status(), 'Only owners and admins change this');
  assert.equal(await box.locator('[data-brepo-status] .err').count(), 1);
  // A refused Read: Write stays selected.
  await box.locator('.brepo-all label:has(input[value=read])').click();
  await page.waitForFunction(() => document.querySelector('#bot-editor .brepo-all input[value=write]')?.checked);
  assert.equal(await status(), 'Only owners and admins change this');
  // A refused tick in Chosen repos.
  data.refuse = '';
  await box.locator('label:has(input[type=radio][value=chosen])').click();
  await page.waitForFunction(() => document.querySelector('#bot-editor [data-brepo-status]')?.textContent === 'Saved');
  await box.locator('[data-brepo="acme/web"] [data-brepo-pick]').click();
  await page.waitForFunction(() => document.querySelector('#bot-editor [data-brepo-status]')?.textContent === 'Saved');
  data.refuse = 'Repository access is locked';
  await box.locator('[data-brepo="acme/mobile"] [data-brepo-pick]').click();
  await page.waitForFunction(() => /locked/.test(document.querySelector('#bot-editor [data-brepo-status]')?.textContent || ''));
  assert.equal(await box.locator('[data-brepo="acme/mobile"] [data-brepo-pick]').isChecked(), false);
  assert.equal(await box.locator('[data-brepo="acme/web"] [data-brepo-pick]').isChecked(), true);
  // A slow save, then a newer choice: the newer one is sent after it, and it is what stays.
  data.refuse = ''; data.delay = 700; data.maxInflight = 0;
  const before = writes.length;
  await box.locator('label:has(input[type=radio][value=all])').first().click();
  await box.locator('.brepo-all').waitFor();
  await box.locator('label:has(input[type=radio][value=own])').click();
  await page.waitForFunction(() => document.querySelector('#bot-editor [data-brepo-status]')?.textContent === 'Saved', null, {timeout: 5000});
  assert.equal(data.maxInflight, 1, 'one save at a time');
  assert.deepEqual(writes.slice(before).map(w => w.body.mode), ['all', 'own']);
  assert.equal(data.bot.mode, 'own', 'the latest choice wins on the server');
  assert.equal(await box.locator('input[type=radio][value=own]').isChecked(), true);
  // Three quick changes while one is on its way: only the last is sent next.
  data.delay = 500;
  const mark = writes.length;
  await box.locator('label:has(input[type=radio][value=all])').first().click();
  await box.locator('.brepo-all').waitFor();
  await box.locator('.brepo-all label:has(input[value=read])').click();
  await box.locator('label:has(input[type=radio][value=chosen])').click();
  await page.waitForFunction(() => document.querySelector('#bot-editor [data-brepo-status]')?.textContent === 'Saved', null, {timeout: 5000});
  assert.deepEqual(writes.slice(mark).map(w => [w.body.mode, w.body.all_access]), [['all', 'write'], ['chosen', 'read']]);
  assert.equal(data.bot.mode, 'chosen');
  assert.equal(await box.locator('input[type=radio][value=chosen]').isChecked(), true);
  // A change made while a refused save reads the server's copy back is still sent, and wins.
  data.refuseOnce = 'Try again in a moment'; data.slowGet = 800;
  const from = writes.length;
  await box.locator('label:has(input[type=radio][value=own])').click();
  await page.waitForTimeout(150);                                   // the PUT was refused; the read-back is on its way
  assert.equal(writes.length, from + 1);
  await box.locator('label:has(input[type=radio][value=all])').first().click();
  await page.waitForFunction(() => document.querySelector('#bot-editor [data-brepo-status]')?.textContent === 'Saved', null, {timeout: 5000});
  assert.deepEqual(writes.slice(from).map(w => w.body.mode), ['own', 'all']);
  assert.equal(data.bot.mode, 'all', 'the newer choice is saved');
  assert.equal(await box.locator('input[type=radio][value=all]').isChecked(), true);
  assert.deepEqual(errors, []);
  console.log('saves: ok');
  await page.close();
}

async function member(browser) {
  const {page, errors, writes} = await open(browser, {role: 'member'});
  await page.locator('#set-repos .repo-row').first().waitFor();
  assert.equal(await page.locator('#set-repos [data-product-repo]').count(), 0, 'product creation is Owner-only');
  assert.equal(await page.locator('#set-repos [data-repo-tick]:not([disabled])').count(), 0);
  assert.equal(await page.locator('#set-repos [data-repo-setup]:not([readonly])').count(), 0);
  assert.equal(await page.locator('#set-repos input[name=repos_new_bot]:not([disabled])').count(), 0);
  assert.equal(await page.locator('#set-repos [data-repos-refresh]').isDisabled(), true);
  assert.equal(await page.locator('#set-repos [data-repos-bots]').isDisabled(), false, 'showing bot repos is only a view');
  // One of the bot's owners opens its settings: the same choice, read only.
  await page.evaluate(() => settingsEditBot('release-captain'));
  const box = page.locator('#bot-editor [data-bot-repos]');
  await box.locator('.brepo-row').first().waitFor();
  assert.equal(await box.locator('input:not([disabled])').count(), 0);
  await box.locator('label:has(input[type=radio][value=all])').first().click({force: true});
  await page.waitForTimeout(150);
  assert.equal(await box.locator('input[type=radio][value=chosen]').isChecked(), true);
  assert.deepEqual(writes, []);
  assert.deepEqual(errors, []);
  await page.close();
}

async function notConnected(browser) {
  const {page, errors} = await open(browser, {connected: false});
  await page.locator('#set-repos .repos-line').waitFor();
  assert.equal((await page.locator('#set-repos').innerText()).replace(/\s+/g, ' '), "Repositories The GitHub App is not connected; bots use their computers' own GitHub sign-in. Connect GitHub");
  assert.equal(await page.locator('#set-repos a[data-repos-connect]').getAttribute('href'), '#/integrations');
  // The bot editor has no Repositories section then.
  await page.evaluate(() => settingsEditBot('release-captain'));
  await page.locator('#bot-editor form').waitFor();
  await page.waitForTimeout(200);
  assert.equal(await page.locator('#bot-editor [data-bot-repos]').isVisible(), false);
  assert.deepEqual(errors, []);
  await page.close();
}

async function phone(browser) {
  const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}});
  await page.locator('#set-repos .repo-row').first().waitFor();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 0, `no sideways scroll on a phone (${overflow}px)`);
  await page.locator('#settings-repos').scrollIntoViewIfNeeded();
  await page.evaluate(() => document.querySelector('#settings-tabs').scrollIntoView({block: 'start'}));
  await shot(page, 'settings-repositories-phone-dark.png');
  await page.evaluate(() => settingsEditBot('release-captain'));
  const box = page.locator('#bot-editor [data-bot-repos]');
  await box.locator('.brepo-row').first().waitFor();
  await box.evaluate(el => el.scrollIntoView({block: 'start'}));
  await shot(page, 'bot-repositories-chosen-phone-dark.png');
  assert.deepEqual(errors, []);
  await page.close();
}

(async () => {
  if (SHOTS) fs.mkdirSync(SHOTS, {recursive: true});
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    await owner(browser);
    await productCreate(browser);
    await saves(browser);
    await member(browser);
    await notConnected(browser);
    await phone(browser);
    console.log('ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
