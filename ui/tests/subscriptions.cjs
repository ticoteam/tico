// Offline regression for one login per AI tool per computer (backend/subscriptions.py, #1259): a computer is
// {runner_id, label} or null, signed_in is true, false or null (unknown) and the bot line may carry a `problem`.
//  - Computers holds Models and no Subscriptions section; old links (AI providers, Subscriptions) open Computers;
//  - a computer's pop-up lists one connection per AI tool, its own login, even where a runner still reports named
//    subscriptions from before; Add connection offers no second sign-in for a tool already signed in;
//  - the bot editor has no connection picker, only the line under Model "Runs on <tool> · Computer's login on
//    <computer>" from GET /v2/bots/{bot}/subscription;
//  - an older server (404) shows no line; a phone has no sideways scroll.
// And a person's own saved subscriptions (backend/model_subscriptions.py):
//  - Settings > Computers lists them with status and where each is picked; add (a ChatGPT login is pasted as
//    auth.json), reconnect and remove; no value is ever shown back;
//  - the bot editor's Subscription row is held until Save bot, and the line under Model names the one in effect;
//  - a task's and a team's pickers save when picked, offer only the caller's own, and show what is in effect and
//    what was passed over.
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
    // Ana's own subscriptions, and Lee's one picked for the Web team (GET /v2/me/subscriptions answers the caller's).
    mySubs: [
      {id: 's1', name: 'Claude Max', kind: 'claude_token', label: 'Claude', runtime: 'claude', status: 'working', owner: 'human:ana', preview: 'sk-ant-oat01-…wxyz'},
      {id: 's2', name: 'ChatGPT Pro', kind: 'codex_login', label: 'ChatGPT', runtime: 'codex', status: 'needs_reconnect', owner: 'human:ana', preview: 'ana@acme.example'}],
    others: [{id: 's9', name: 'Lee Claude', kind: 'claude_token', label: 'Claude', runtime: 'claude', status: 'working', owner: 'human:lee'}],
    picks: {'bot:builder:claude': 's1', 'team:g-web:claude': 's9', 'team:g-eng:codex': 's2'},
  };
}
const SUB_NAMES = {'human:ana': 'Ana', 'human:lee': 'Lee'};
// GET /v2/subscription-picks, as backend/model_subscriptions.py picks_view builds it: task -> bot -> team chain.
function picksView(data, scope, id) {
  const all = [...data.mySubs, ...data.others], sub = sid => all.find(x => x.id === sid);
  const brief = x => ({id: x.id, name: x.name, kind: x.kind, label: x.label, runtime: x.runtime, status: x.status, owner: x.owner});
  const bot = scope === 'bot' ? id : scope === 'task' ? 'builder' : null;
  const chain = team => { const out = []; for (let at = team; at; at = data.groups.find(g => g.id === at)?.parent) out.push(at); return out; };
  const levels = [...(scope === 'task' ? [['task', id]] : []), ...(bot ? [['bot', bot]] : []),
    ...chain(scope === 'team' ? id : 'g-web').map(g => ['team', g])];
  const name = (sc, sid) => sc === 'team' ? data.groups.find(g => g.id === sid).name : sc === 'bot' ? 'Builder' : 'Fix the build';
  const runtimes = (bot ? ['claude'] : ['claude', 'codex']).map(runtime => {
    const passed = [];
    let effect = null;
    for (const [sc, sid] of levels) {
      const s = sub(data.picks[`${sc}:${sid}:${runtime}`]); if (!s) continue;
      if (s.status === 'needs_reconnect') { passed.push({scope: sc, scope_id: sid, subscription: brief(s), why: 'needs_reconnect'}); continue; }
      effect = {source: sc, scope_id: sid, name: name(sc, sid), subscription: {...brief(s), owner_name: SUB_NAMES[s.owner]}};
      break;
    }
    effect ||= bot ? {source: 'computer', computer: "Sam's Mac"} : {source: 'none'};
    const own = sub(data.picks[`${scope}:${id}:${runtime}`]);
    return {runtime, label: runtime === 'claude' ? 'Claude Code' : 'Codex', effect: {...effect, passed},
      pick: own ? {...brief(own), set_by: own.owner, mine: own.owner === 'human:ana'} : null,
      options: data.mySubs.filter(x => x.runtime === runtime).map(brief)};
  });
  return {scope, id, bot, can_pick: true, runtimes};
}
function mySubsView(data) {
  return data.mySubs.map(x => ({...x, used_by: Object.entries(data.picks).filter(([, sid]) => sid === x.id).map(([key]) => {
    const [scope, id, runtime] = key.split(':');
    return {scope, id, runtime, name: scope === 'team' ? data.groups.find(g => g.id === id).name : scope === 'bot' ? 'Builder' : 'Fix the build'};
  })}));
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
    if (old && (p === '/api/v2/subscription-picks' || p.startsWith('/api/v2/me/subscriptions'))) return json({detail: 'Not Found'}, 404);
    if (p === '/api/v2/me/subscriptions' && method === 'GET') return json({subscriptions: mySubsView(data)});
    if (p.startsWith('/api/v2/me/subscriptions')) {
      const body = method === 'DELETE' ? null : req.postDataJSON(), sid = p.split('/')[5];
      writes.push({p, method, body});
      if (method === 'DELETE') { data.mySubs = data.mySubs.filter(x => x.id !== sid); return json({deleted: true, id: sid}); }
      if (sid) { const x = data.mySubs.find(y => y.id === sid); x.status = 'new'; return json(x); }
      const x = {id: 's3', name: body.name || 'ChatGPT', kind: body.kind, label: 'ChatGPT', runtime: 'codex', status: 'new', owner: 'human:ana', preview: ''};
      data.mySubs.push(x);
      return json(x);
    }
    if (p === '/api/v2/subscription-picks') {
      if (method === 'GET') return json(picksView(data, url.searchParams.get('scope'), url.searchParams.get('id')));
      const body = req.postDataJSON(); writes.push({p, method, body});
      const key = `${body.scope}:${body.id}:${body.runtime}`;
      if (body.subscription) data.picks[key] = body.subscription; else delete data.picks[key];
      return json(picksView(data, body.scope, body.id));
    }
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
  // A tab this person can't see opens General and says why; an unknown one opens General without the line.
  for (const [restricted, denied] of [['people', true], ['unknown-tab', false]]) {
    await page.evaluate(tab => settingsShow(tab), restricted);
    assert.equal(await page.locator('#settings-general').isVisible(), true, `${restricted} safely falls back`);
    assert.equal(await page.evaluate(() => SETTINGS_TAB), 'general');
    assert.equal(await page.locator('#settings-denied').isVisible(), denied, `${restricted}: the admins-only line`);
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
  await page.waitForFunction(() => location.hash === '#/settings/devices' && SETTINGS_TAB === 'devices');
  await page.locator('#set-machines .machine-name[data-computer-open=r1]').click();
  const detail = page.locator('#set-machines dialog.computer-detail[open]');
  // One connection per AI tool, the computer's own login: the named subscriptions it still reports are not shown.
  assert.deepEqual(await detail.locator('[data-connection]').evaluateAll(list => list.map(c => c.dataset.connection)), ['own:claude', 'own:codex']);
  assert.equal(await detail.locator('[data-connection="own:codex"] .computer-conn-head').innerText(), 'Codex');
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
  const {page, errors, writes, data} = await open(browser);
  data.picks = {};                                    // nobody picked a subscription: the computer's login
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

// Settings > Computers > Subscriptions: the list, add, reconnect and remove.
async function personSubs(browser) {
  const {page, errors, writes} = await open(browser);
  await page.evaluate(() => settingsShow('devices'));
  const card = page.locator('#set-subscriptions');
  await card.locator('[data-sub-row]').nth(1).waitFor();
  const row = id => card.locator(`[data-sub-row="${id}"]`);
  assert.equal(await row('s1').locator('.pill.ok').count(), 1, 'working');
  assert.equal(await row('s2').locator('.pill.fail').count(), 1, 'needs reconnect');
  assert.match(await row('s1').innerText(), /Builder/);
  assert.match(await row('s2').innerText(), /Engineering team/);
  // A ChatGPT login is the whole auth.json, pasted into a box; a token or key is a password field.
  const form = card.locator('#settings-subs-form');
  assert.equal(await form.locator('input[name=secret][type=password]').count(), 1);
  await form.locator('select[name=kind]').selectOption('codex_login');
  const login = '{"tokens":{"refresh_token":"acme-refresh"}}';
  await form.locator('textarea[name=secret]').fill(login);
  await form.locator('input[name=name]').fill('Acme ChatGPT');
  await form.locator('button[type=submit]').click();
  await card.locator('[data-sub-row="s3"]').waitFor();
  assert.deepEqual(writes.find(w => w.p === '/api/v2/me/subscriptions')?.body, {kind: 'codex_login', name: 'Acme ChatGPT', secret: login});
  assert.equal(await form.locator('input[name=secret][type=password]').count(), 1, 'the form starts over');
  // Reconnect: a new value in a dialog of its own.
  await row('s2').locator('[data-sub-reconnect]').click();
  const dialog = page.locator('dialog[open] [data-subs-reconnect]');
  await dialog.locator('textarea[name=secret]').fill('{"tokens":{"access_token":"acme-new"}}');
  await dialog.locator('button[type=submit]').click();
  await page.waitForFunction(() => !document.querySelector('dialog[open] [data-subs-reconnect]'));
  assert.deepEqual(writes.find(w => w.p === '/api/v2/me/subscriptions/s2')?.body, {secret: '{"tokens":{"access_token":"acme-new"}}'});
  // Remove asks first.
  await row('s1').locator('[data-sub-remove]').click();
  await page.locator('dialog.settings-confirm [data-confirm-ok]').click();
  await row('s1').waitFor({state: 'detached'});
  assert.ok(writes.some(w => w.p === '/api/v2/me/subscriptions/s1' && w.method === 'DELETE'));
  assert.doesNotMatch(await page.locator('body').innerText(), /acme-refresh|acme-new/, 'a value is never shown back');
  if (SHOTS) await card.screenshot({path: path.join(SHOTS, 'subscriptions-card-dark.png')});
  assert.deepEqual(errors, []);
  console.log('person subscriptions: ok');
  await page.close();
}

// The bot editor: the bot's own pick, held until Save bot; the line under Model names it.
async function botPick(browser) {
  const {page, errors, writes} = await open(browser);
  await page.evaluate(() => settingsEditBot('builder'));
  const rows = page.locator('#bot-editor [data-bot-people]');
  const select = rows.locator('[data-bot-subs] select[data-subs-select=claude]');
  await select.waitFor();
  assert.equal(await select.inputValue(), 's1');
  assert.equal(await rows.locator('[data-bot-sub-line] .sb-sub-text').innerText(), "Runs on Claude Code · Claude Max (Ana) on Sam's Mac");
  assert.deepEqual(await select.locator('option').evaluateAll(list => list.map(o => o.value)), ['', 's1'], 'only my own Claude subscriptions');
  await select.selectOption('');
  if (SHOTS) { await select.scrollIntoViewIfNeeded(); await page.screenshot({path: path.join(SHOTS, 'bot-subscription-dark.png')}); }
  assert.match(await page.locator('#bot-editor').innerText(), /Not saved yet: subscription/);
  assert.equal(writes.filter(w => w.p === '/api/v2/subscription-picks').length, 0, 'held until Save bot');
  await page.locator('#bot-editor [type=submit]').click();
  // With the bot's own pick off, the Web team's (Lee's) is the one in effect.
  await rows.locator('[data-bot-sub-line] .sb-sub-text', {hasText: 'Lee Claude'}).waitFor();
  assert.equal(await rows.locator('[data-bot-sub-line] .sb-sub-text').innerText(), "Runs on Claude Code · Lee Claude (Lee) on Sam's Mac");
  assert.deepEqual(writes.find(w => w.p === '/api/v2/subscription-picks')?.body, {scope: 'bot', id: 'builder', runtime: 'claude', subscription: null});
  assert.deepEqual(errors, []);
  console.log('bot pick: ok');
  await page.close();
}

// A task's and a team's pickers save when picked and show what is in effect, from where, and what was passed over.
async function taskAndTeam(browser) {
  const {page, errors, writes} = await open(browser);
  await page.evaluate(() => {
    const d = document.createElement('div'); d.id = 'subs-task-fixture';
    d.innerHTML = subsTaskHTML({id: 't1', owner: 'bot:builder', run_model: null});
    document.body.append(d); subsTaskBind(d, {id: 't1'});
  });
  const task = page.locator('#subs-task-fixture [data-task-subs]');
  await task.locator('select[data-subs-select=claude]').waitFor();
  assert.equal(await task.locator('[data-subs-effect=claude]').innerText(), "Claude Max (Ana) · the bot's");
  await task.locator('select[data-subs-select=claude]').selectOption('s1');
  await task.locator('[data-subs-effect=claude]', {hasText: /^Claude Max \(Ana\)$/}).waitFor();
  assert.deepEqual(writes.find(w => w.p === '/api/v2/subscription-picks')?.body, {scope: 'task', id: 't1', runtime: 'claude', subscription: 's1'});
  // The Web team: Lee's pick is shown as not mine; its Codex comes from Engineering, whose pick needs reconnecting.
  await page.evaluate(() => { location.hash = '#/team/g-web'; });
  await page.locator('#dash-root [data-subs-team]:not([hidden])').click();
  const team = page.locator('dialog[open] [data-subs-box]');
  await team.locator('select[data-subs-select=claude]').waitFor();
  assert.equal(await team.locator('select[data-subs-select=claude]').inputValue(), 's9');
  assert.match(await team.locator('select[data-subs-select=claude] option:checked').innerText(), /Lee Claude · not yours/);
  assert.equal(await team.locator('[data-subs-runtime=codex] .pill.fail').innerText(), 'ChatGPT Pro needs reconnect');
  assert.equal(await team.locator('[data-subs-effect=codex]').innerText(), "Each bot's own");
  await team.locator('select[data-subs-select=codex]').selectOption('s2');
  await page.waitForFunction(() => document.querySelector('dialog[open] [data-subs-effect=codex]') && document.querySelector('dialog[open] select[data-subs-select=codex]')?.value === 's2' && !document.querySelector('dialog[open] select[data-subs-select=codex]').disabled);
  assert.equal(await team.locator('[data-subs-runtime=codex] .pill.fail').count(), 1, 'one line per subscription passed over');
  if (SHOTS) await page.locator('dialog[open]').screenshot({path: path.join(SHOTS, 'team-subscription-dark.png')});
  assert.deepEqual(writes.filter(w => w.p === '/api/v2/subscription-picks').at(-1).body, {scope: 'team', id: 'g-web', runtime: 'codex', subscription: 's2'});
  assert.deepEqual(errors, []);
  console.log('task and team picks: ok');
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
  try { await navigation(browser); await computers(browser); await botEditor(browser); await personSubs(browser); await botPick(browser); await taskAndTeam(browser); await oldServer(browser); await phone(browser); }
  finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
