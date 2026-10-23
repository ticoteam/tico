// Offline regression for one login per AI tool per computer (backend/subscriptions.py, #1259): a computer is
// {runner_id, label} or null, signed_in is true, false or null (unknown) and the bot line may carry a `problem`.
//  - Computers holds Models and no Subscriptions section; old links (AI providers, Subscriptions) open Computers;
//  - a computer's pop-up lists one connection per AI tool, its own login, even where a runner still reports named
//    subscriptions from before; Add connection offers no second sign-in for a tool already signed in;
//  - the bot editor has no connection picker, only the line under Model "Runs on <tool> · Computer's login on
//    <computer>" from GET /v2/bots/{bot}/subscription;
//  - an older server (404) shows no line; a phone has no sideways scroll.
// SUBS_SHOTS=<dir> saves the screenshots for the owner.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile, harnesses} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const SHOTS = process.env.SUBS_SHOTS || '';

function fixtures() {
  const now = new Date().toISOString();
  const machine = (id, label, operator, bots = []) => ({id, label, operator, last_seen: now, version: '0.3.2', platform: 'linux', bots,
    accepts_member_bots: true, readiness: {runtimes: {codex: {installed: true, authenticated: 'ready'}, claude: {installed: true, authenticated: 'ready'}}}});
  return {
    // Two of Acme box's bots run Codex on acme-eng and take turns on it (backend/views.py shared_logins).
    machines: [{...machine('r1', 'Acme box', 'ana'), shared_logins: [{runtime: 'codex', profile: '', count: 2, bots: []}]}, machine('r2', "Sam's Mac", 'sam', ['builder']), {...machine('r3', 'Old laptop', 'ana', ['scout']),
      update: {state: 'needs_update', label: 'Needs update', release: '0.2.0', commit: 'c0ffee'.padEnd(40, '0'), outdated: true, server_release: '0.3.0', error: ''}}],
    // Named subscriptions a runner still reports from before: the page shows none of them.
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
    },
    groups: [{id: 'g-eng', name: 'Engineering', parent: '', order: 1}, {id: 'g-web', name: 'Web', parent: 'g-eng', order: 1},
      {id: 'g-ops', name: 'Operations', parent: '', order: 2}],
  };
}
// GET /v2/bots/builder/subscription, as backend/subscriptions.py bot_subscription builds it.
function lineFor() {
  return {profile: null, source: 'computer', computer: {runner_id: 'r2', label: "Sam's Mac"}, runtime: 'claude', harness: 'claude', signed_in: true, problem: ''};
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
    const machines = data.machines.map(m => ({...m, profiles: structuredClone(data.subs.profiles_by_computer.find(c => c.runner_id === m.id)?.profiles || []),
      bot_connections: m.bots.map(bot => ({bot, ...(bot === 'builder' ? lineFor() : {profile: null, source: 'computer', runtime: 'codex', harness: 'codex', signed_in: null, problem: ''}), computer: undefined}))}));
    if (p === '/api/v2/operations') return json({machines: as === 'owner' ? machines : machines.filter(m => m.operator === me.id), services: [], agents: []});
    if (p === '/api/v2/models') return json({models: [], harnesses});
    if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
    if (p === '/api/v2/bots/builder/subscription') return old ? json({detail: 'Not Found'}, 404) : json(lineFor());
    if (p.startsWith('/api/v2/subscriptions')) { writes.push({p, method}); return json({detail: 'Method Not Allowed'}, 405); }
    const login = p.match(/^\/api\/v2\/computers\/([^/]+)\/logins$/);
    if (login && method === 'POST') {
      const body = req.postDataJSON(); writes.push({p, body});
      return json({id: 'l1', state: 'waiting', url: 'https://claude.ai/oauth/example', code: 'ACME-1234', created: new Date().toISOString(), lines: []});
    }
    if (p.match(/^\/api\/v2\/computers\/[^/]+\/logins\/l1(\/cancel)?$/)) {
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
  // One connection per AI tool, the computer's own login: the named subscriptions it still reports are not shown.
  assert.deepEqual(await detail.locator('[data-connection]').evaluateAll(list => list.map(c => c.dataset.connection)), ['own:claude', 'own:codex']);
  assert.match(await detail.locator('[data-connection="own:codex"]').innerText(), /Codex\s+Computer's login/);
  assert.doesNotMatch(await detail.innerText(), /acme-|Subscription ·|Rename/, 'no named subscription, nor its controls');
  // The bots on the computer's Codex login take turns on it.
  assert.equal(await detail.locator('[data-connection="own:codex"] [data-shared-login]').innerText(), '2 bots share this sign-in. They take turns: one Codex run at a time.');
  if (SHOTS) { fs.mkdirSync(SHOTS, {recursive: true}); await detail.screenshot({path: path.join(SHOTS, 'computer-detail-dark.png')}); }
  // Add connection: a tool already signed in here has no second sign-in; a different account means another computer.
  await detail.locator('[data-add-connection]').click();
  const add = page.locator('dialog.computer-add-dialog[open]');
  await add.locator('[data-add-tool=codex]').click();
  const subscription = add.locator('[data-add-own]');
  assert.equal(await subscription.isDisabled(), true);
  assert.match(await subscription.innerText(), /already signed in here; for a different account, add another computer/);
  assert.equal(await add.locator('input[name=profile]').count(), 0, 'no name for a second login');
  await page.keyboard.press('Escape');
  assert.equal(writes.length, 0, 'nothing sent');
  assert.deepEqual(errors, []);
  console.log('computers: ok');
  await page.close();
}

async function botEditor(browser) {
  const {page, errors, writes} = await open(browser);
  await page.evaluate(() => settingsEditBot('builder'));
  const rows = page.locator('#bot-editor [data-bot-people]');
  const line = rows.locator('[data-bot-sub-line]');
  await line.locator('.sb-sub-text').waitFor();
  // No connection to choose: the bot runs on its computer's login.
  assert.equal(await page.locator('#bot-editor [data-bot-sub-row], #bot-editor [data-bot-sub]').count(), 0);
  assert.equal(await line.locator('.sb-sub-text').innerText(), "Runs on Claude Code · Computer's login on Sam's Mac");
  assert.equal(await line.locator('.sb-sub-warn').count(), 0);
  // The line sits right under the model picker.
  const model = await rows.locator('.sb-row', {hasText: 'Model'}).first().boundingBox(), under = await line.boundingBox();
  assert.ok(under.y >= model.y + model.height - 1 && under.y - (model.y + model.height) < 16, 'under the model picker');
  if (SHOTS) { await rows.scrollIntoViewIfNeeded(); await rows.screenshot({path: path.join(SHOTS, 'bot-settings-connection-dark.png')}); }
  assert.deepEqual(writes, []);
  assert.deepEqual(errors, []);
  console.log('bot editor: ok');
  await page.close();
}

async function oldServer(browser) {
  const {page, errors} = await open(browser, {old: true});
  await page.evaluate(() => settingsEditBot('builder'));
  await page.locator('#bot-editor [data-bot-people] .sb-row').first().waitFor();
  // resolves once the 404 has been read and the rows painted
  await page.evaluate(() => subsBotMount(document.querySelector('#bot-editor [data-bot-people]'), 'builder'));
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
  try { await navigation(browser); await computers(browser); await botEditor(browser); await oldServer(browser); await phone(browser); }
  finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
