// Offline regression for subscriptions (docs: PLAN section 7). Fixtures follow backend/subscriptions.py (r2-subs):
// names are lowercase-hyphen slugs, PUT answers the assignment it saved, a computer is {runner_id, label} or null,
// signed_in is true, false or null (unknown) and the bot line may carry a `problem`; members list only their own computers.
//  - Computers holds Models and no Subscriptions section; old links (AI providers, Subscriptions) open Computers;
//  - a computer's pop-up lists its subscriptions as connections (only the tools each is signed in to) next to the
//    computer's own login; its one-line add turns a typed name into a slug and starts the browser-code login there;
//  - groups no longer choose one: the bot editor offers "Computer's login" or its own (PUT {scope: 'bot'}), its
//    computer's subscriptions first; owners, admins and the bot computer's operator may change it, a co-owner may not;
//    under Model the line "Runs on <tool> · <connection> on <computer>" from GET /v2/bots/{bot}/subscription, with the
//    server's problem;
//  - an older server (404) shows none of it; a phone has no sideways scroll.
// SUBS_SHOTS=<dir> saves the screenshots for the owner.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile, harnesses} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();   // load.cjs arrives with #254
const SHOTS = process.env.SUBS_SHOTS || '';

function fixtures() {
  const now = new Date().toISOString();
  const machine = (id, label, operator, bots = []) => ({id, label, operator, last_seen: now, version: '0.3.2', platform: 'linux', bots,
    accepts_member_bots: true, readiness: {runtimes: {codex: {installed: true, authenticated: 'ready'}, claude: {installed: true, authenticated: 'ready'}}}});
  return {
    // Two of Acme box's bots run Codex on acme-eng and take turns on it (backend/views.py shared_logins).
    machines: [{...machine('r1', 'Acme box', 'ana'), shared_logins: [{runtime: 'codex', profile: 'acme-eng', display_name: 'acme-eng', count: 2, bots: []}]}, machine('r2', "Sam's Mac", 'sam', ['builder']), {...machine('r3', 'Old laptop', 'ana', ['scout']),
      update: {state: 'needs_update', label: 'Needs update', release: '0.2.0', commit: 'c0ffee'.padEnd(40, '0'), outdated: true, server_release: '0.3.0', error: ''}}],
    subs: {
      profiles_by_computer: [
        {runner_id: 'r1', label: 'Acme box', profiles: [
          {name: 'acme-eng', runtimes: {codex: {signed_in: true}, claude: {signed_in: false}}},
          {id: 'p-ops', name: 'acme-ops', runtimes: {claude: {signed_in: true}, codex: {signed_in: null}}},
          {name: 'acme-new', runtimes: {codex: {signed_in: null}, claude: {signed_in: null}}}]},     // reported, state unknown
        {runner_id: 'r2', label: "Sam's Mac", profiles: [
          {name: 'acme-eng', runtimes: {claude: {signed_in: false}}},
          {name: 'acme-ops', runtimes: {claude: {signed_in: true}}}]},
        {runner_id: 'r3', label: 'Old laptop', profiles: []},          // a runner too old to report subscriptions
      ],
      assignments: [],
    },
    groups: [{id: 'g-eng', name: 'Engineering', parent: '', order: 1}, {id: 'g-web', name: 'Web', parent: 'g-eng', order: 1},
      {id: 'g-ops', name: 'Operations', parent: '', order: 2}],
  };
}
// GET /v2/bots/builder/subscription, as backend/subscriptions.py bot_subscription builds it.
function lineFor(data) {
  const own = data.subs.assignments.find(a => a.scope === 'bot' && a.target === 'builder');
  const computer = {runner_id: 'r2', label: "Sam's Mac"};
  const tool = {runtime: 'claude', harness: 'claude'};
  if (own) {
    const ok = own.profile === 'acme-ops';
    return {profile: own.profile, source: 'bot', computer, ...tool, signed_in: ok ? true : false,
      problem: ok ? '' : own.profile === 'acme-eng' ? "acme-eng is not signed in on Sam's Mac" : `profile ${own.profile} not on Sam's Mac`};
  }
  return {profile: null, source: 'computer', computer, ...tool, signed_in: true, problem: ''};
}

async function open(browser, {viewport = {width: 1440, height: 900}, theme = 'dark', old = false, as = 'owner'} = {}) {
  const data = fixtures();
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors = [], writes = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(t => { try { localStorage.setItem('tico.theme', t); sessionStorage.setItem('tico.settings.tab', 'providers'); } catch {} }, theme);
  // owner: Ana; coowner: Lee, a member who co-owns the bot; operator: Sam, a member whose computer the bot runs on.
  const me = {owner: {id: 'ana', role: 'owner', name: 'Ana'}, coowner: {id: 'lee', role: 'member', name: 'Lee'},
    operator: {id: 'sam', role: 'member', name: 'Sam'}}[as];
  Object.assign(me, {email: me.id + '@example.com', cloud: true, registered: true});
  const policy = {see: {everyone: true}, read: {everyone: true}, write: {everyone: true}};
  const bots = [
    {name: 'builder', display_name: 'Builder', status: 'active', state: 'active', operator: 'ana', team: 'g-web',
      repo: 'bot-builder', can_manage: true, revision: 3, users: [{id: 'ana', name: 'Ana'}], bot_owners: [{id: 'lee', name: 'Lee'}],
      resolved_runtime: 'claude', resolved_model: 'claude-sonnet', harness: 'claude',
      machine: {runner_id: 'r2', label: "Sam's Mac", operator: 'sam', last_seen: new Date().toISOString()},
      my_access: {see: true, read: true, write: true}, access_policy: policy},
    {name: 'scout', display_name: 'Scout', status: 'active', state: 'active', operator: 'ana', team: 'g-eng', repo: 'bot-scout',
      can_manage: true, revision: 1, users: [], bot_owners: [], resolved_runtime: 'codex', harness: 'codex',
      machine: {runner_id: 'r3', label: 'Old laptop', operator: 'ana', last_seen: new Date().toISOString()},
      my_access: {see: true, read: true, write: true}, access_policy: policy},
  ];
  // A member lists only the computers they may see (backend computer_rows).
  const visible = () => as === 'owner' ? data.subs.profiles_by_computer : data.subs.profiles_by_computer.filter(c => c.runner_id === 'r2' && as === 'operator');
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.startsWith('/vendor/fonts/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile(p.slice(1)))});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json(me);
    if (p === '/api/employees') return json(bots);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'sam', name: 'Sam'}, {id: 'lee', name: 'Lee'}], teams: {}, org_groups: data.groups});
    if (p === '/api/issues') return json([]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    // Each computer with the subscriptions it reported and its bots' connections (backend/views.py operations).
    // `readiness_at`: the computer's last report, newer than a finished sign-in once its next heartbeat came (`beatAt`).
    const reported = data.beatAt && Date.now() >= data.beatAt ? new Date().toISOString() : undefined;
    const machines = data.machines.map(m => ({...m, readiness_at: reported || m.readiness_at, profiles: structuredClone(data.subs.profiles_by_computer.find(c => c.runner_id === m.id)?.profiles || []),
      bot_connections: m.bots.map(bot => ({bot, ...(bot === 'builder' ? lineFor(data) : {profile: null, source: 'computer', runtime: 'codex', harness: 'codex', signed_in: null, problem: ''}), computer: undefined}))}));
    if (p === '/api/v2/operations') return json({machines: as === 'owner' ? machines : machines.filter(m => m.operator === me.id), services: [], agents: []});
    if (p === '/api/v2/models') return json({models: [], harnesses});
    if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
    if ((p === '/api/v2/subscriptions' || p === '/api/v2/bots/builder/subscription') && old) return json({detail: 'Not Found'}, 404);
    if (p === '/api/v2/subscriptions' && method === 'GET') return json({profiles_by_computer: visible(), assignments: data.subs.assignments});
    if (p === '/api/v2/subscriptions' && method === 'PUT') {
      const body = req.postDataJSON(); writes.push({p, body});
      data.subs.assignments = data.subs.assignments.filter(a => !(a.scope === body.scope && a.target === body.target));
      if (body.profile) data.subs.assignments.push({scope: body.scope, target: body.target, profile: body.profile, updated: new Date().toISOString(), updated_by: 'human:' + me.id});
      return json(body);                                           // backend: body.model_dump()
    }
    if (p === '/api/v2/bots/builder/subscription') return json(lineFor(data));
    // A saved name, and a manual weekly reading (backend/subscriptions.py rename, save_weekly), in the next report.
    const saved = body => data.subs.profiles_by_computer.find(c => c.runner_id === body.runner_id).profiles.find(x => x.name === body.profile);
    if (p === '/api/v2/subscriptions/name' && method === 'PUT') {
      const body = req.postDataJSON(); writes.push({p, body});
      saved(body).display_name = body.display_name;
      return json({ok: true});
    }
    if (p === '/api/v2/subscriptions/weekly' && method === 'PUT') {
      const body = req.postDataJSON(); writes.push({p, body});
      saved(body).runtimes[body.runtime].weekly = body.used_percent === null ? undefined
        : {used_percent: body.used_percent, resets_at: body.resets_at, reported_at: new Date().toISOString(), source: 'manual'};
      return json({ok: true});
    }
    const login = p.match(/^\/api\/v2\/computers\/([^/]+)\/logins$/);
    if (login && method === 'POST') {
      const body = req.postDataJSON(); writes.push({p, body});
      if (body.profile && !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(body.profile)) return json({detail: [{loc: ['body', 'profile'], msg: 'String should match pattern'}]}, 422);
      return json({id: 'l1', state: 'waiting', url: 'https://claude.ai/oauth/example', code: 'ACME-1234', created: new Date().toISOString(), lines: []});
    }
    if (p.match(/^\/api\/v2\/computers\/[^/]+\/logins\/l1(\/cancel)?$/)) {
      // finishLogin: the sign-in completes, and the computer reports it on its next heartbeat.
      if (data.finishLogin && method === 'GET') {
        data.subs.profiles_by_computer[0].profiles[0].runtimes.claude.signed_in = true;
        data.beatAt = data.beatAt || Date.now() + 2000;
        return json({id: 'l1', state: 'signed_in', created: new Date().toISOString(), updated: new Date(data.beatAt - 2000).toISOString(), lines: []});
      }
      return json({id: 'l1', state: method === 'POST' ? 'cancelled' : 'waiting', url: 'https://claude.ai/oauth/example', code: 'ACME-1234', created: new Date().toISOString(), lines: []});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/settings');
  await page.locator('#settings-models').waitFor();   // the saved tab is the old AI providers one
  await page.waitForFunction(() => { const el = document.querySelector('#set-machines'); return el && !/Loading/.test(el.textContent); });
  return {page, errors, writes, data};
}
const last = (writes, p) => writes.filter(w => w.p === p).at(-1)?.body;

async function navigation(browser) {
  const {page, errors} = await open(browser);
  // A computer behind the server's release carries an Outdated badge and the page says whose it is to update.
  assert.equal(await page.locator('#set-machines [data-runner-outdated]').count(), 1);
  assert.match(await page.locator('#set-machines [data-runner-outdated-banner]').textContent(), /Tico 0\.3\.0.*Old laptop \(0\.2\.0\).*Yours/);
  for (const alias of ['tags', 'types']) {
    await page.evaluate(alias => settingsShow(alias), alias);
    assert.equal(await page.locator('#settings-tasks').isVisible(), true, `${alias} opens Tasks`);
    assert.equal(await page.evaluate(() => sessionStorage.getItem('tico.settings.tab')), 'tasks');
  }
  // Even if old navigation is still painted, losing a role cannot expose a restricted pane.
  await page.evaluate(() => { S.me.role = 'member'; S.me.bot_admin = false; });
  for (const restricted of ['people', 'unknown-tab']) {
    await page.evaluate(tab => settingsShow(tab), restricted);
    assert.equal(await page.locator('#settings-devices').isVisible(), true, `${restricted} safely falls back`);
    assert.equal(await page.evaluate(() => SETTINGS_TAB), 'devices');
  }
  // History keeps Runs for everyone; Changes is the owner's. The old Runs page lands there.
  await page.evaluate(() => { settingsShow('runs'); settingsHistoryShow('changes'); });
  assert.equal(await page.locator('#set-runs').isVisible(), true, 'runs opens History on Runs');
  assert.equal(await page.locator('#set-history').isVisible(), false, 'Changes stays hidden without the owner role');
  assert.equal(await page.locator('#settings-history-view').isVisible(), false);
  // Team, Assistant, Privacy and Storage are sections of General; the ones this person lost stay hidden there.
  for (const alias of ['team', 'assistant', 'privacy', 'storage']) {
    await page.evaluate(alias => settingsShow(alias), alias);
    assert.equal(await page.evaluate(() => SETTINGS_TAB), 'general', `${alias} opens General`);
    assert.equal(await page.locator('#settings-assistant-pane').isVisible(), true);
    for (const id of ['team', 'privacy', 'storage']) assert.equal(await page.locator('#settings-' + id).isVisible(), false, `${id} stays hidden`);
  }
  // AI providers is now Models on Computers; the Chat tab is gone.
  for (const alias of ['providers', 'models', 'chat']) {
    await page.evaluate(alias => settingsShow(alias), alias);
    assert.equal(await page.locator('#settings-models').isVisible(), true, `${alias} opens Computers`);
    assert.equal(await page.evaluate(() => sessionStorage.getItem('tico.settings.tab')), 'devices');
  }
  for (const alias of ['credentials', 'cloud']) {
    assert.equal(await page.evaluate(alias => { settingsShow(alias); return location.hash; }, alias), '#/integrations');
  }
  assert.deepEqual(errors, []);
  await page.close();
  console.log('settings aliases and current access: ok');
}

async function computers(browser) {
  const {page, errors, writes} = await open(browser);
  assert.equal(await page.locator('#settings-subs').count(), 0);
  // An old link to Subscriptions opens Computers.
  await page.evaluate(() => settingsShow('bots'));
  await page.evaluate(() => { location.hash = '#/settings/subscriptions'; });
  await page.waitForFunction(() => location.hash === '#/settings' && SETTINGS_TAB === 'devices');
  await page.locator('#set-machines .machine-name[data-computer-open=r1]').click();
  const detail = page.locator('#set-machines dialog.computer-detail[open]');
  // A subscription is a connection only for a tool it is signed in to; the computer's own logins stand beside them.
  assert.deepEqual(await detail.locator('[data-connection]').evaluateAll(list => list.map(c => c.dataset.connection)),
    ['own:claude', 'profile:acme-ops:claude', 'own:codex', 'profile:acme-eng:codex']);
  assert.match(await detail.locator('[data-connection="profile:acme-eng:codex"]').innerText(), /Codex\s+Subscription · acme-eng\s+Signed in/);
  // Two bots take turns on acme-eng's Codex sign-in; a sign-in one bot uses says nothing.
  assert.equal(await detail.locator('[data-connection="profile:acme-eng:codex"] [data-shared-login]').innerText(), '2 bots share this sign-in. They take turns: one Codex run at a time.');
  assert.equal(await detail.locator('[data-connection="profile:acme-ops:claude"] [data-shared-login]').count(), 0);
  assert.doesNotMatch(await detail.innerText(), /acme-new|not signed in/i, 'nothing for a slot that was never signed in');
  if (SHOTS) { fs.mkdirSync(SHOTS, {recursive: true}); await detail.screenshot({path: path.join(SHOTS, 'computer-detail-dark.png')}); }
  // A new subscription: the typed name becomes a slug the server takes, and the sign-in starts on this computer.
  let add = await addSubscription(page, 'codex');
  await add.locator('input[name=profile]').fill('Acme Research');
  assert.equal(await add.locator('[data-add-msg]').innerText(), 'Saved as acme-research');
  // A settings refresh while typing leaves the field alone.
  await page.evaluate(() => loadSettings());
  assert.equal(await add.locator('input[name=profile]').inputValue(), 'Acme Research');
  await add.locator('button[type=submit]').click();
  const dialog = page.locator('dialog.model-login');
  await dialog.locator('[data-code]').waitFor();
  assert.match(await dialog.locator('h2').innerText(), /Sign in to Codex \(ChatGPT\) · acme-research on Acme box/);
  assert.deepEqual(last(writes, '/api/v2/computers/r1/logins'), {runtime: 'codex', profile: 'acme-research'});
  await dialog.locator('[data-close]').first().click();
  await dialog.waitFor({state: 'detached'});
  add = await addSubscription(page, 'codex');
  await add.locator('input[name=profile]').fill('---');
  await add.locator('button[type=submit]').click();
  assert.equal(await add.locator('[data-add-msg]').innerText(), 'Use letters or numbers');
  await page.keyboard.press('Escape');
  assert.equal(writes.filter(w => w.p.endsWith('/logins')).length, 1, 'nothing sent for an empty name');
  assert.deepEqual(errors, []);
  console.log('computers: ok');
  await page.close();
}

// Add connection in the open computer pop-up: the tool, then Subscription; returns the pop-up at its name step.
async function addSubscription(page, runtime) {
  await page.locator('#set-machines dialog.computer-detail[open] [data-add-connection]').click();
  const add = page.locator('dialog.computer-add-dialog[open]');
  await add.locator(`[data-add-tool=${runtime}]`).click();
  await add.locator('[data-add-kind=subscription]').click();
  await add.locator('input[name=profile]').waitFor();
  return add;
}

async function botEditor(browser) {
  const {page, errors, writes} = await open(browser);
  await page.evaluate(() => settingsEditBot('builder'));
  const rows = page.locator('#bot-editor [data-bot-people]');
  const sub = rows.locator('[data-bot-sub-row] select');
  await sub.waitFor();
  const line = rows.locator('[data-bot-sub-line]');
  await line.locator('.sb-sub-text').waitFor();
  // The computer's own login by default; its computer's subscriptions first, then the others. No group default.
  assert.deepEqual(await sub.locator('option').allInnerTexts(), ["Computer's login", 'acme-eng', 'acme-ops', 'acme-new']);
  assert.deepEqual(await sub.locator('optgroup').evaluateAll(gs => gs.map(g => g.label)), ["Sam's Mac", 'Other computers']);
  assert.equal(await sub.inputValue(), '');
  assert.equal(await sub.isDisabled(), false, 'the owner may choose');
  assert.equal(await line.locator('.sb-sub-text').innerText(), "Runs on Claude Code · Computer's login on Sam's Mac");
  assert.equal(await line.locator('.sb-sub-warn').count(), 0);
  assert.doesNotMatch(await line.innerText(), /object Object|group/i);
  // The line sits right under the model picker.
  const model = await rows.locator('.sb-row', {hasText: 'Model'}).first().boundingBox(), under = await line.boundingBox();
  assert.ok(under.y >= model.y + model.height - 1 && under.y - (model.y + model.height) < 16, 'under the model picker');
  assert.equal(await sub.getAttribute('aria-label'), 'Connection for Builder');
  if (SHOTS) { await rows.scrollIntoViewIfNeeded(); await rows.screenshot({path: path.join(SHOTS, 'bot-settings-connection-dark.png')}); }
  // Its own choice, and the server's word when it cannot run there.
  await sub.selectOption('acme-ops');
  await page.waitForFunction(() => /Runs on Claude Code · acme-ops on Sam's Mac/.test(document.querySelector('#bot-editor [data-bot-sub-line]')?.innerText || ''));
  assert.deepEqual(last(writes, '/api/v2/subscriptions'), {scope: 'bot', target: 'builder', profile: 'acme-ops'});
  assert.equal(await line.locator('.sb-sub-warn').count(), 0);
  assert.equal(await sub.inputValue(), 'acme-ops');
  await sub.selectOption('acme-eng');
  await page.waitForFunction(() => /acme-eng is not signed in/.test(document.querySelector('#bot-editor [data-bot-sub-line] .sb-sub-warn')?.innerText || ''));
  // Back to the computer's login.
  await sub.selectOption('');
  await page.waitForFunction(() => /Computer's login on Sam's Mac/.test(document.querySelector('#bot-editor [data-bot-sub-line]')?.innerText || ''));
  assert.deepEqual(last(writes, '/api/v2/subscriptions'), {scope: 'bot', target: 'builder', profile: null});
  assert.deepEqual(errors, []);
  console.log('bot editor: ok');
  await page.close();
}

// After a sign-in the computer's row and pop-up catch up by themselves, saying "Waiting for <computer>" until the
// computer reports it; only a name being typed holds a redraw of the pop-up back.
async function signInRefresh(browser) {
  const {page, errors, data} = await open(browser);
  await page.evaluate(() => settingsShow('devices'));
  await page.locator('#set-machines .machine-name[data-computer-open=r1]').click();
  const detail = page.locator('#set-machines dialog.computer-detail[open]');
  // Signing acme-eng in to Claude Code as well: the add form with the existing name.
  data.finishLogin = true;
  const add = await addSubscription(page, 'claude');
  await add.locator('input[name=profile]').fill('acme-eng');
  await add.locator('button[type=submit]').click();
  const dialog = page.locator('dialog.model-login');
  await dialog.locator('[data-status][data-state="signed_in"]').waitFor({timeout: t(8000)});
  await dialog.locator('[data-close]').first().click();
  await page.locator('#set-machines tr[data-computer-row=r1] [data-machine-waiting]').waitFor({timeout: t(8000)});
  await detail.locator('[data-connection="profile:acme-eng:claude"]').waitFor({timeout: t(10000)});
  await page.locator('#set-machines tr[data-computer-row=r1] [data-machine-waiting]').waitFor({state: 'detached', timeout: t(10000)});
  assert.deepEqual(errors, []);
  console.log('sign-in refresh: ok');
  await page.close();
}

// A saved name or reading is no draft: the open pop-up shows it at once and takes later reports. (A name still being
// typed in Add connection stays as it is: computers, above.)
async function savedForms(browser) {
  const {page, errors, writes, data} = await open(browser);
  await page.evaluate(() => settingsShow('devices'));
  await page.locator('#set-machines .machine-name[data-computer-open=r1]').click();
  const detail = page.locator('#set-machines dialog.computer-detail[open]');
  const ops = detail.locator('[data-connection="profile:acme-ops:claude"]');
  await ops.locator('.computer-conn-rename summary').click();
  await ops.locator('[data-subs-rename] input[name=display_name]').fill('Acme Operations');
  await ops.locator('[data-subs-rename] button[type=submit]').click();
  await ops.locator('.computer-conn-head', {hasText: 'Subscription · Acme Operations'}).waitFor();
  assert.deepEqual(last(writes, '/api/v2/subscriptions/name'), {runner_id: 'r1', profile: 'acme-ops', display_name: 'Acme Operations'});
  assert.equal(await ops.locator('details.computer-conn-rename').evaluate(d => d.open), true, 'Rename stays open');
  await ops.locator('details.subs-controls summary').click();
  await ops.locator('[data-subs-weekly] input[name=percent]').fill('40');
  await ops.locator('[data-subs-weekly] button[type=submit]').click();
  await ops.locator('.computer-conn-usage', {hasText: 'Weekly 40% left'}).waitFor();
  assert.equal(last(writes, '/api/v2/subscriptions/weekly').used_percent, 60);
  assert.equal(await ops.locator('[data-subs-weekly] input[name=percent]').inputValue(), '40');
  // The computer's next report reaches the same pop-up, still open.
  data.subs.profiles_by_computer[0].profiles[1].runtimes.claude.weekly.used_percent = 90;
  await page.evaluate(() => loadSettings());
  await ops.locator('.computer-conn-usage', {hasText: 'Weekly 10% left'}).waitFor();
  assert.equal(await ops.locator('[data-subs-weekly] input[name=percent]').inputValue(), '10');
  assert.equal(await ops.locator('button:disabled').count(), 0, 'no button left waiting');
  assert.deepEqual(errors, []);
  console.log('saved forms refresh: ok');
  await page.close();
}

async function who(browser) {
  // A co-owner of the bot sees its connection but may not change it; the person whose computer it runs on may.
  for (const [as, enabled] of [['coowner', false], ['operator', true]]) {
    const {page, errors, writes} = await open(browser, {as});
    await page.evaluate(() => settingsEditBot('builder'));
    const sub = page.locator('#bot-editor [data-bot-sub-row] select');
    await sub.waitFor();
    await page.locator('#bot-editor [data-bot-sub-line] .sb-sub-text').waitFor();
    assert.equal(await sub.isDisabled(), !enabled, `${as}: ${enabled ? 'may' : 'may not'} choose`);
    if (as === 'operator') {
      // The computer's operator sees only their computer, and adds and signs in subscriptions on it.
      await page.goto('https://tico-ui.test/#/settings');
      await page.locator('[data-settings-tab=devices]').click();
      assert.deepEqual(await page.locator('#set-machines tr[data-computer-row]').evaluateAll(r => r.map(x => x.dataset.computerRow)), ['r2']);
      await page.locator('#set-machines .machine-name[data-computer-open=r2]').click();
      const add = await addSubscription(page, 'claude');
      await add.locator('input[name=profile]').fill('acme-eng');
      await add.locator('button[type=submit]').click();
      await page.locator('dialog.model-login [data-status][data-state="waiting"]').waitFor();
      assert.deepEqual(last(writes, '/api/v2/computers/r2/logins'), {runtime: 'claude', profile: 'acme-eng'});
    }
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log('who may choose: ok');
}

async function oldServer(browser) {
  const {page, errors} = await open(browser, {old: true});
  await page.evaluate(() => settingsEditBot('builder'));
  await page.locator('#bot-editor [data-bot-people] .sb-row').first().waitFor();
  // resolves once both 404s have been read and the rows painted
  await page.evaluate(() => subsBotMount(document.querySelector('#bot-editor [data-bot-people]'), 'builder'));
  assert.equal(await page.locator('#bot-editor [data-bot-sub-row]').isHidden(), true);
  assert.equal(await page.locator('#bot-editor [data-bot-sub-line]').isHidden(), true);
  assert.deepEqual(errors, []);
  console.log('older server: ok');
  await page.close();
}

async function phone(browser) {
  const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}});
  await page.evaluate(() => settingsShow('devices'));
  await page.locator('#set-machines .machine-name[data-computer-open=r1]').click();
  const detail = page.locator('#set-machines dialog.computer-detail[open]');
  await detail.locator('[data-connection]').first().waitFor();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 0, `no sideways scroll on a phone (${overflow}px)`);
  const inside = await detail.evaluate(d => d.querySelector('.computer-detail-body').scrollWidth - d.querySelector('.computer-detail-body').clientWidth);
  assert.ok(inside <= 0, `the pop-up fits a phone (${inside}px)`);
  if (SHOTS) await page.screenshot({path: path.join(SHOTS, 'computer-detail-phone-dark.png')});
  assert.deepEqual(errors, []);
  console.log('phone: ok');
  await page.close();
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try { await navigation(browser); await computers(browser); await botEditor(browser); await signInRefresh(browser); await savedForms(browser); await who(browser); await oldServer(browser); await phone(browser); }
  finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
