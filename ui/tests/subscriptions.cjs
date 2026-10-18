// Offline regression for subscriptions (docs: PLAN section 7). Fixtures follow backend/subscriptions.py (r2-subs):
// names are lowercase-hyphen slugs, PUT answers the assignment it saved, a computer is {runner_id, label} or null,
// signed_in is true, false or null (unknown) and the bot line may carry a `problem`; members list only their own computers.
//  - Settings > AI providers > Subscriptions lists each computer's subscriptions with their sign-in per runtime (nothing for
//    unknown); Sign in starts the browser-code login for that computer and subscription; the one-line add turns a
//    typed name into a slug before signing in;
//  - each group picks one with PUT /v2/subscriptions {scope: 'group'}; a nested group says which group it inherits from;
//    a group whose bots sit on a computer where its subscription is known not to be signed in says so, an old runner
//    that never reported does not;
//  - the bot editor: Subscription "From group (<name>)" or its own (PUT {scope: 'bot'}), its computer's subscriptions
//    first; owners, admins and the bot computer's operator may change it, a co-owner may not; under Model the line from
//    GET /v2/bots/{bot}/subscription with the server's problem, or "Not signed in";
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
    machines: [machine('r1', 'Acme box', 'ana'), machine('r2', "Sam's Mac", 'sam', ['builder']), {...machine('r3', 'Old laptop', 'ana', ['scout']),
      update: {state: 'needs_update', label: 'Needs update', release: '0.2.0', commit: 'c0ffee'.padEnd(40, '0'), outdated: true, server_release: '0.3.0', error: ''}}],
    subs: {
      profiles_by_computer: [
        {runner_id: 'r1', label: 'Acme box', profiles: [
          {name: 'acme-eng', runtimes: {codex: {signed_in: true}, claude: {signed_in: false}}},
          {name: 'acme-ops', runtimes: {claude: {signed_in: true}, codex: {signed_in: null}}},
          {name: 'acme-new', runtimes: {codex: {signed_in: null}, claude: {signed_in: null}}}]},     // reported, state unknown
        {runner_id: 'r2', label: "Sam's Mac", profiles: [
          {name: 'acme-eng', runtimes: {claude: {signed_in: false}}},
          {name: 'acme-ops', runtimes: {claude: {signed_in: true}}}]},
        {runner_id: 'r3', label: 'Old laptop', profiles: []},          // a runner too old to report subscriptions
      ],
      assignments: [{scope: 'group', target: 'g-eng', profile: 'acme-eng', updated: now, updated_by: 'human:ana'}],
    },
    groups: [{id: 'g-eng', name: 'Engineering', parent: '', order: 1}, {id: 'g-web', name: 'Web', parent: 'g-eng', order: 1},
      {id: 'g-ops', name: 'Operations', parent: '', order: 2}],
  };
}
// GET /v2/bots/builder/subscription, as backend/subscriptions.py bot_subscription builds it.
function lineFor(data) {
  const own = data.subs.assignments.find(a => a.scope === 'bot' && a.target === 'builder');
  const computer = {runner_id: 'r2', label: "Sam's Mac"};
  if (own) {
    const ok = own.profile === 'acme-ops';
    return {profile: own.profile, source: 'bot', computer, signed_in: ok ? true : false,
      problem: ok ? '' : own.profile === 'acme-eng' ? "acme-eng is not signed in on Sam's Mac" : `profile ${own.profile} not on Sam's Mac`};
  }
  const group = data.subs.assignments.find(a => a.scope === 'group' && a.target === 'g-eng');
  if (!group) return {profile: null, source: 'computer', computer, signed_in: true, problem: ''};
  return {profile: group.profile, source: 'group:Engineering', computer, signed_in: false, problem: "acme-eng is not signed in on Sam's Mac"};
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
    if (p === '/api/v2/operations') return json({machines: as === 'owner' ? data.machines : data.machines.filter(m => m.operator === me.id), services: [], agents: []});
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
        return json({id: 'l1', state: 'signed_in', created: new Date().toISOString(), lines: []});
      }
      return json({id: 'l1', state: method === 'POST' ? 'cancelled' : 'waiting', url: 'https://claude.ai/oauth/example', code: 'ACME-1234', created: new Date().toISOString(), lines: []});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/settings');
  await page.locator('#settings-providers').waitFor();
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
  for (const restricted of ['team', 'people', 'history', 'privacy', 'unknown-tab']) {
    await page.evaluate(tab => settingsShow(tab), restricted);
    assert.equal(await page.locator('#settings-devices').isVisible(), true, `${restricted} safely falls back`);
    assert.equal(await page.evaluate(() => SETTINGS_TAB), 'devices');
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
  const card = page.locator('#settings-subs');
  await card.locator('.subs-row').first().waitFor();
  assert.equal(await card.locator('h2').innerText(), 'Subscriptions');
  assert.deepEqual(await card.locator('.subs-pc:not(.subs-groups) .subs-h').allInnerTexts(), ['ACME BOX', "SAM'S MAC"], 'a computer with none is not listed');
  const box = card.locator('.subs-pc').first();
  const eng = box.locator('[data-subs-profile="acme-eng"]');
  assert.equal(await eng.locator('.subs-name').innerText(), 'acme-eng');
  assert.deepEqual(await eng.locator('.subs-rt .pill').allInnerTexts(), ['Codex · signed in', 'Claude Code · not signed in']);
  assert.equal(await eng.locator('.subs-signin').count(), 1, 'Sign in only where it is not signed in');
  const ops = box.locator('[data-subs-profile="acme-ops"]');
  assert.equal(await ops.locator('.subs-name').innerText(), 'acme-ops');
  assert.deepEqual(await ops.locator('.subs-rt .pill').allInnerTexts(), ['Claude Code · signed in'], 'unknown sign-in adds no authentication pill');
  assert.deepEqual(await ops.locator('.subs-weekly > span:first-child').allInnerTexts(), ['Claude Code weekly: usage unknown', 'Codex weekly: usage unknown'], 'weekly allowance stays separate from sign-in state');
  // Unknown for every runtime: no state, but the owner can still sign it in.
  assert.deepEqual(await box.locator('[data-subs-profile="acme-new"] .subs-signin').allInnerTexts(), ['Sign in to Codex', 'Sign in to Claude Code']);
  assert.equal(await box.locator('[data-subs-profile="acme-new"] .pill').count(), 0);
  if (SHOTS) { fs.mkdirSync(SHOTS, {recursive: true}); await card.screenshot({path: path.join(SHOTS, 'settings-computers-subscriptions-dark.png')}); }
  // Sign in: the browser-code login for that computer and subscription.
  await eng.locator('.subs-signin').click();
  const dialog = page.locator('dialog.model-login');
  await dialog.locator('[data-code]').waitFor();
  assert.match(await dialog.locator('h2').innerText(), /Sign in to Claude Code · acme-eng on Acme box/);
  assert.deepEqual(last(writes, '/api/v2/computers/r1/logins'), {runtime: 'claude', profile: 'acme-eng'});
  await dialog.locator('[data-close]').first().click();
  await dialog.waitFor({state: 'detached'});
  // Groups: Engineering has one; Web inherits it and says so; Operations has none.
  const groups = card.locator('.subs-groups');
  assert.deepEqual(await groups.locator('.subs-name').allInnerTexts(), ['Engineering', 'Web', 'Operations']);
  assert.equal(await groups.locator('[data-subs-group="g-eng"]').inputValue(), 'acme-eng');
  assert.equal(await groups.locator('[data-subs-group="g-web"] option').first().innerText(), 'From Engineering (acme-eng)');
  assert.deepEqual(await groups.locator('[data-subs-group="g-ops"] option').allInnerTexts(), ['None', 'acme-eng', 'acme-new', 'acme-ops']);
  // Builder (in Web) runs on Sam's Mac where acme-eng is not signed in; Scout's old laptop never reported: no chip for it.
  assert.deepEqual(await groups.locator('.subs-row', {hasText: 'Engineering'}).locator('.subs-gap').allInnerTexts(), ["Not signed in on Sam's Mac"]);
  await groups.locator('[data-subs-group="g-ops"]').selectOption('acme-ops');
  await page.waitForFunction(() => document.querySelector('[data-subs-group="g-ops"]')?.value === 'acme-ops' && !document.querySelector('[data-subs-group="g-ops"]').disabled);
  assert.deepEqual(last(writes, '/api/v2/subscriptions'), {scope: 'group', target: 'g-ops', profile: 'acme-ops'});
  await groups.locator('[data-subs-group="g-eng"]').selectOption('');
  await page.waitForFunction(() => document.querySelector('[data-subs-group="g-eng"]')?.value === '' && !document.querySelector('[data-subs-group="g-eng"]').disabled);
  assert.deepEqual(last(writes, '/api/v2/subscriptions'), {scope: 'group', target: 'g-eng', profile: null});
  assert.equal(await card.locator('.subs-gap').count(), 0, 'no subscription, nothing to sign in');
  assert.equal(await groups.locator('[data-subs-group="g-web"] option').first().innerText(), 'None');
  // A new subscription: the typed name becomes a slug the server takes.
  const add = card.locator('[data-subs-add]');
  await add.locator('input[name=profile]').fill('Acme Research');
  assert.equal(await add.locator('#subs-add-msg').innerText(), 'Saved as acme-research');
  // A settings refresh while typing leaves the field alone.
  await page.evaluate(() => loadSettings());
  assert.equal(await add.locator('input[name=profile]').inputValue(), 'Acme Research');
  await add.locator('select[name=runner]').selectOption('r2');
  await add.locator('select[name=runtime]').selectOption('codex');
  await add.locator('button[type=submit]').click();
  await dialog.locator('[data-code]').waitFor();
  assert.deepEqual(last(writes, '/api/v2/computers/r2/logins'), {runtime: 'codex', profile: 'acme-research'});
  await dialog.locator('[data-close]').first().click();
  await dialog.waitFor({state: 'detached'});
  await add.locator('input[name=profile]').fill('---');
  await add.locator('button[type=submit]').click();
  assert.equal(await add.locator('#subs-add-msg').innerText(), 'Use letters or numbers');
  assert.equal(writes.filter(w => w.p.endsWith('/logins')).length, 2, 'nothing sent for an empty name');
  assert.deepEqual(errors, []);
  console.log('computers: ok');
  await page.close();
}

async function botEditor(browser) {
  const {page, errors, writes} = await open(browser);
  await page.evaluate(() => settingsEditBot('builder'));
  const rows = page.locator('#bot-editor [data-bot-people]');
  const sub = rows.locator('[data-bot-sub-row] select');
  await sub.waitFor();
  const line = rows.locator('[data-bot-sub-line]');
  await line.locator('.sb-sub-text').waitFor();
  // Its computer's subscriptions first, then the others.
  assert.deepEqual(await sub.locator('option').allInnerTexts(), ['From group (Engineering)', 'acme-eng', 'acme-ops', 'acme-new']);
  assert.deepEqual(await sub.locator('optgroup').evaluateAll(gs => gs.map(g => g.label)), ["Sam's Mac", 'Other computers']);
  assert.equal(await sub.inputValue(), '');
  assert.equal(await sub.isDisabled(), false, 'the owner may choose');
  assert.equal(await line.locator('.sb-sub-text').innerText(), "Subscription: acme-eng · from group Engineering · Sam's Mac");
  assert.equal(await line.locator('.sb-sub-warn').innerText(), "acme-eng is not signed in on Sam's Mac");
  assert.doesNotMatch(await line.innerText(), /object Object/);
  // The line sits right under the model picker.
  const model = await rows.locator('.sb-row', {hasText: 'Model'}).first().boundingBox(), under = await line.boundingBox();
  assert.ok(under.y >= model.y + model.height - 1 && under.y - (model.y + model.height) < 16, 'under the model picker');
  assert.equal(await sub.getAttribute('aria-label'), 'Subscription for Builder');
  if (SHOTS) { await rows.scrollIntoViewIfNeeded(); await rows.screenshot({path: path.join(SHOTS, 'bot-settings-subscription-dark.png')}); }
  // Its own choice.
  await sub.selectOption('acme-ops');
  await page.waitForFunction(() => /acme-ops · set for this bot/.test(document.querySelector('#bot-editor [data-bot-sub-line]')?.innerText || ''));
  assert.deepEqual(last(writes, '/api/v2/subscriptions'), {scope: 'bot', target: 'builder', profile: 'acme-ops'});
  assert.equal(await line.locator('.sb-sub-warn').count(), 0);
  assert.equal(await sub.inputValue(), 'acme-ops');
  // Back to the group's.
  await sub.selectOption('');
  await page.waitForFunction(() => /from group Engineering/.test(document.querySelector('#bot-editor [data-bot-sub-line]')?.innerText || ''));
  assert.deepEqual(last(writes, '/api/v2/subscriptions'), {scope: 'bot', target: 'builder', profile: null});
  assert.deepEqual(errors, []);
  console.log('bot editor: ok');
  await page.close();
}

// After a sign-in the list catches up by itself, even with the keyboard still inside the card (N1); only a name
// being typed holds a redraw back.
async function signInRefresh(browser) {
  const {page, errors, data} = await open(browser);
  const card = page.locator('#settings-subs');
  const eng = card.locator('.subs-pc').first().locator('[data-subs-profile="acme-eng"]');
  await eng.locator('.subs-signin').waitFor();
  // A redraw with focus on a group select still happens.
  await card.locator('[data-subs-group="g-ops"]').focus();
  data.subs.assignments.push({scope: 'group', target: 'g-ops', profile: 'acme-ops'});
  await page.evaluate(() => loadSettings());
  await page.waitForFunction(() => document.querySelector('[data-subs-group="g-ops"]')?.value === 'acme-ops');
  data.finishLogin = true;
  await eng.locator('.subs-signin').click();
  const dialog = page.locator('dialog.model-login');
  await dialog.locator('[data-status][data-state="signed_in"]').waitFor({timeout: t(8000)});
  await dialog.locator('[data-close]').first().click();
  await card.locator('.subs-pc').first().locator('[data-subs-profile="acme-eng"]', {hasText: 'Claude Code · signed in'}).waitFor({timeout: t(10000)});
  assert.deepEqual(errors, []);
  console.log('sign-in refresh: ok');
  await page.close();
}

async function who(browser) {
  // A co-owner of the bot sees its subscription but may not change it; the person whose computer it runs on may.
  for (const [as, enabled] of [['coowner', false], ['operator', true]]) {
    const {page, errors, writes} = await open(browser, {as});
    await page.evaluate(() => settingsEditBot('builder'));
    const sub = page.locator('#bot-editor [data-bot-sub-row] select');
    await sub.waitFor();
    await page.locator('#bot-editor [data-bot-sub-line] .sb-sub-text').waitFor();
    assert.equal(await sub.isDisabled(), !enabled, `${as}: ${enabled ? 'may' : 'may not'} choose`);
    if (as === 'operator') {
      // The Computer operator can sign in existing profiles and add a profile on their Computer.
      const card = page.locator('#settings-subs');
      await card.locator('.subs-row').first().waitFor();
      assert.deepEqual(await card.locator('.subs-pc:not(.subs-groups) .subs-h').allInnerTexts(), ["SAM'S MAC"]);
      assert.ok(await card.locator('.subs-signin').count() > 0);
      assert.equal(await card.locator('[data-subs-add]').count(), 1);
      assert.deepEqual(await card.locator('[data-subs-add] [name=runner] option').allTextContents(), ["Sam's Mac"]);
      await page.evaluate(() => document.querySelector('#bot-editor').close());
      await card.locator('.subs-signin').first().click();
      await page.locator('dialog.model-login [data-status][data-state="waiting"]').waitFor();
      assert.deepEqual(last(writes, '/api/v2/computers/r2/logins'), {runtime: 'claude', profile: 'acme-eng'});
      assert.equal(await card.locator('[data-subs-group="g-eng"]').isDisabled(), true, 'groups are for owners and admins');
    }
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log('who may choose: ok');
}

async function oldServer(browser) {
  const {page, errors} = await open(browser, {old: true});
  await page.evaluate(() => renderSettingsSubs());          // resolves once the 404 has been read and the card drawn
  assert.equal(await page.locator('#settings-subs').isHidden(), true);
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
  await page.locator('#settings-subs .subs-row').first().waitFor();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 0, `no sideways scroll on a phone (${overflow}px)`);
  if (SHOTS) { await page.locator('#settings-subs').scrollIntoViewIfNeeded(); await page.screenshot({path: path.join(SHOTS, 'settings-subscriptions-phone-dark.png')}); }
  assert.deepEqual(errors, []);
  console.log('phone: ok');
  await page.close();
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try { await navigation(browser); await computers(browser); await botEditor(browser); await signInRefresh(browser); await who(browser); await oldServer(browser); await phone(browser); }
  finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
