// Offline regression for company subscriptions given to computers (backend/model_subscriptions.py): a subscription is a
// company's saved plan login or API key; a computer holds at most one per AI tool; a bot never picks one.
//  - Settings has a Models tab between Computers and Repositories with Subscriptions, Tico Gateway, Defaults and
//    Limits; old links (AI providers, Subscriptions, Chat, the Models card on Computers) open it;
//  - Add subscription: a kind search box that starts on Claude plan, a required name, POST {kind, name, secret};
//    Rename, Reconnect and Remove from the row's menu, Reconnect and Remove asking first when runs would restart;
//  - Computers: one row per computer with a picker per AI tool; a switch that stops runs asks in the page first
//    (409 confirm_restart), then sends confirm; an older computer says Needs update; Gateway off reads None;
//  - a computer's pop-up still lists one connection per AI tool, its own login, and the queue line;
//  - the bot editor has Model and Computer and no subscription line; a phone has no sideways page scroll.
// SUBS_SHOTS=<dir> saves desktop and phone screenshots, dark and light.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile, harnesses} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const SHOTS = process.env.SUBS_SHOTS || '';

const KINDS = [
  {kind: 'claude_token', label: 'Claude plan', runtime: 'claude', secret_label: 'Token from claude setup-token', help: 'Run claude setup-token on a computer signed in to the plan, then paste the token.'},
  {kind: 'codex_login', label: 'ChatGPT plan', runtime: 'codex', secret_label: "Codex's auth.json", help: 'Paste ~/.codex/auth.json from a computer where codex login is signed in.'},
  {kind: 'anthropic_key', label: 'Anthropic API key', runtime: 'claude', secret_label: 'API key', help: ''},
  {kind: 'openai_key', label: 'OpenAI API key', runtime: 'codex', secret_label: 'API key', help: ''}];

function fixtures() {
  const now = new Date().toISOString();
  const ready = {installed: true, authenticated: 'ready'}, missing = {installed: true, authenticated: 'missing'};
  const machine = (id, label, operator, bots = [], runtimes = {codex: ready, claude: ready}, extra = {}) => ({id, label, owner_id: operator, last_seen_at: now,
    version: '0.5.14', platform: 'linux', bots, accepts_member_bots: true, readiness: {runtimes},
    turn_counts: {running: 0, starting: 0, waiting: 0, queued: 0, waits: []}, turn_summary: '0 running · 0 starting · 0 waiting', ...extra});
  return {
    machines: [
      machine('r1', 'Tico Team box', 'ana', ['builder', 'writer', 'ops'], {claude: ready, codex: ready, musecode: ready},
        {version: '0.5.4', release: '0.5.19',
          turn_counts: {running: 1, starting: 0, waiting: 0, queued: 55, waits: []}, turn_summary: '1 running · 0 starting · 55 queued · 0 waiting'}),
      machine('r2', 'Cloud · shared', 'ana', ['scout'], {claude: missing, codex: missing}),
      machine('r3', 'Cloud · Video Producer', 'ana', ['video'], {claude: missing, codex: missing}, {cloud_machine: {state: 'stopped'}}),
      machine('r4', "Sam's Mac", 'sam', [], {claude: ready, codex: missing}, {version: '0.4.2',
        turn_counts: {running: 0, starting: 0, waiting: 3, queued: 0, waits: []},
        update: {state: 'needs_update', label: 'Needs update', release: '0.4.2', commit: 'c0ffee'.padEnd(40, '0'), outdated: true, server_release: '0.5.14', error: ''}}),
    ],
    subs: [
      {id: 's1', name: 'Claude Max (Ana)', kind: 'claude_token', runtime: 'claude', label: 'Claude plan', status: 'working', status_detail: '',
        computers: ['r1', 'r2', 'r3'], usage: {window: '5h', used_pct: 62, cost_usd_month: null}, added_by: 'ana'},
      {id: 's2', name: 'ChatGPT Pro (Ana)', kind: 'codex_login', runtime: 'codex', label: 'ChatGPT plan', status: 'working', status_detail: '',
        computers: ['r1'], usage: {window: 'week', used_pct: 13, cost_usd_month: null}, added_by: 'ana'},
      {id: 's3', name: 'OpenAI key (company)', kind: 'openai_key', runtime: 'codex', label: 'OpenAI API key', status: 'working', status_detail: '',
        computers: [], usage: {window: null, used_pct: null, cost_usd_month: 41.2}, added_by: 'ana'},
      {id: 's4', name: 'Claude Max (Sam)', kind: 'claude_token', runtime: 'claude', label: 'Claude plan', status: 'needs_reconnect', status_detail: 'Token expired',
        computers: [], usage: null, added_by: 'sam'},
    ],
    assignments: [{runner_id: 'r1', runtime: 'claude', subscription_id: 's1'}, {runner_id: 'r1', runtime: 'codex', subscription_id: 's2'},
      {runner_id: 'r2', runtime: 'claude', subscription_id: 's1'}, {runner_id: 'r3', runtime: 'claude', subscription_id: 's1'}],
    capable: ['r1', 'r2', 'r3'],
    providers: {configured: true, enabled: ['anthropic', 'openai', 'tico'], default: {runtime: 'codex', model: 'gpt-6-luna'}, revision: 4,
      auto_fallback: true, check_models: [], check_order: [{runtime: 'grok'}, {runtime: 'codex'}], preset: 'subscriptions-gateway',
      presets: ['subscriptions-gateway', 'subscriptions', 'gateway', 'own-keys'], decide_level: 'low', decide_levels: ['low', 'medium', 'high'],
      meeting_model: '', providers: [{id: 'anthropic', label: 'Anthropic'}, {id: 'openai', label: 'OpenAI'}, {id: 'tico', label: 'Tico', gateway: true}]},
    gateway: {connected: true, state: 'connected', working: true, automatic: true, editable: true, preview: 'tgw…x9k', url: '', default_url: 'https://gateway.tico.dev', revision: 2, spent_usd: 188, cap_usd: 1000},
    models: [
      {id: 'gpt-6-luna', label: 'GPT 6 Luna', provider: 'openai', runtime: 'codex'},
      {id: 'claude-sonnet-5-5', label: 'Claude Sonnet 5.5', provider: 'anthropic', runtime: 'claude'},
      {id: 'meeting-1', label: 'Meeting 1.0', provider: 'tico', runtime: 'tico', meeting: true}],
    levels: {warning_per_person: 40, stop_per_person: 100, people: 3, warning: 120, stop: 300, in_use: 18},
  };
}

async function open(browser, {viewport = {width: 1440, height: 900}, theme = 'dark', as = 'owner', tab = 'models', old = false, mutate} = {}) {
  const data = fixtures();
  mutate?.(data);
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors = [], writes = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(([t, saved]) => { try { localStorage.setItem('tico.theme', t); sessionStorage.setItem('tico.settings.tab', saved); } catch {} }, [theme, tab]);
  const me = {owner_id: {id: 'ana', role: 'owner', name: 'Ana'}, member: {id: 'sam', role: 'member', name: 'Sam'}}[as];
  Object.assign(me, {email: me.id + '@example.com', cloud: true, registered: true});
  const policy = {see: {everyone: true}, read: {everyone: true}, write: {everyone: true}};
  const bots = [
    {name: 'builder', display_name: 'Builder', status: 'active', state: 'active', owner_id: 'ana', repo: 'bot-builder', can_manage: true, revision: 3,
      users: [{id: 'ana', name: 'Ana'}], bot_owners: [], resolved_runtime: 'claude', resolved_model: 'claude-sonnet-5-5', model: 'claude-sonnet-5-5', harness: 'claude',
      machine: {runner_id: 'r1', label: 'Tico Team box', owner_id: 'ana', last_seen_at: new Date().toISOString()},
      my_access: {see: true, read: true, write: true}, access_policy: policy},
  ];
  // A refusal as the server sends it (backend/app.py Problem handler: the extra facts beside code and detail).
  const problem = (code, detail, extra) => ({error: {code, detail, retryable: false, ...extra}});
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
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'sam', name: 'Sam'}], teams: {}, org_groups: []});
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/operations') return json({machines: data.machines.map(m => ({...m, bot_connections: []})), services: [], agents: []});
    if (p === '/api/v2/models') return json({models: data.models, harnesses, enabled_providers: data.providers.enabled, default: data.providers.default});
    if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
    if (p === '/api/v2/providers' && method === 'GET') return json(data.providers);
    if (p === '/api/v2/providers' && method === 'PUT') {
      const body = req.postDataJSON(); writes.push({p, method, body});
      Object.assign(data.providers, body.preset ? {preset: body.preset} : {}, body.decide_level ? {decide_level: body.decide_level} : {}, {revision: data.providers.revision + 1});
      return json(data.providers);
    }
    if (p === '/api/v2/providers/gateway') return json(data.gateway);
    if (p === '/api/v2/settings/turn-levels' && method === 'GET') return json(data.levels);
    if (old && /^\/api\/v2\/(model|computer)-subscriptions/.test(p)) return json({detail: 'Not Found'}, 404);
    if (p === '/api/v2/model-subscriptions' && method === 'GET') return json({subscriptions: data.subs, kinds: KINDS});
    if (p === '/api/v2/model-subscriptions' && method === 'POST') {
      const body = req.postDataJSON(); writes.push({p, method, body});
      const kind = KINDS.find(k => k.kind === body.kind);
      const row = {id: 's9', name: body.name, kind: body.kind, runtime: kind.runtime, label: kind.label, status: 'new', status_detail: '', computers: [], usage: null, added_by: me.id};
      data.subs.push(row);
      return json(row);
    }
    const one = p.match(/^\/api\/v2\/model-subscriptions\/([^/]+)$/);
    if (one) {
      const sub = data.subs.find(s => s.id === one[1]);
      if (method === 'POST') {
        const body = req.postDataJSON(); writes.push({p, method, body});
        (data.keys ||= []).push(req.headers()['idempotency-key']);
        // Tico Team box is running a task: a new value there stops it, so the server asks first.
        if (body.secret && sub.computers.includes('r1') && !body.confirm)
          return json(problem('confirm_restart', 'Reconnecting restarts running tasks', {running: 1, sessions: 2, computers: sub.computers.length}), 409);
        if (body.name) sub.name = body.name;
        return json(sub);
      }
      if (method === 'DELETE') {
        writes.push({p, method, confirm: url.searchParams.get('confirm')});
        if (sub.computers.includes('r1') && url.searchParams.get('confirm') !== '1')
          return json(problem('confirm_restart', 'Removing it restarts running tasks', {running: 1, computers: sub.computers.length}), 409);
        data.subs = data.subs.filter(s => s !== sub);
        data.assignments = data.assignments.filter(a => a.subscription_id !== sub.id);
        return json({removed: sub.id});
      }
    }
    if (p === '/api/v2/computer-subscriptions' && method === 'GET') return json({assignments: data.assignments, capable: data.capable});
    if (p === '/api/v2/computer-subscriptions' && method === 'PUT') {
      const body = req.postDataJSON(); writes.push({p, method, body});
      // Tico Team box is running a task: a switch there stops it, so the server asks first.
      if (body.runner_id === 'r1' && !body.confirm) return json(problem('confirm_restart', 'Switching restarts running tasks', {running: 2, sessions: 5}), 409);
      data.assignments = data.assignments.filter(a => !(a.runner_id === body.runner_id && a.runtime === body.runtime));
      if (body.subscription) data.assignments.push({runner_id: body.runner_id, runtime: body.runtime, subscription_id: body.subscription});
      for (const s of data.subs) s.computers = data.assignments.filter(a => a.subscription_id === s.id).map(a => a.runner_id);
      return json({runner_id: body.runner_id, runtime: body.runtime, subscription_id: body.subscription, stopped: body.confirm ? 2 : 0, sessions_reset: body.confirm ? 5 : 0});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/settings');
  await page.locator(`#settings-${tab}`).waitFor();
  return {page, errors, writes, data};
}
async function shot(page, name, target = page) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, {recursive: true});
  await page.waitForTimeout(150);
  await target.screenshot({path: path.join(SHOTS, name + '.png'), ...(target === page ? {fullPage: true} : {})});
}
const modelsReady = async page => { await page.locator('#set-subs [data-subs-row]').first().waitFor(); await page.locator('#set-prov').waitFor(); };
// Waits for the fake server to have seen a write.
async function until(page, check, what) {
  for (let i = 0; i < 100; i++) { if (check()) return; await page.waitForTimeout(30); }
  throw new Error(`timed out waiting for ${what}`);
}

async function navigation(browser) {
  const {page, errors} = await open(browser, {tab: 'general'});
  const tabs = await page.locator('#settings-tabs > [data-settings-tab]').evaluateAll(list => list.map(b => b.dataset.settingsTab));
  assert.deepEqual(tabs.slice(tabs.indexOf('devices'), tabs.indexOf('devices') + 3), ['devices', 'models', 'repos'], 'Models sits between Computers and Repositories');
  for (const alias of ['models', 'providers', 'chat', 'subscriptions', 'limits']) {
    await page.evaluate(() => settingsShow('general'));
    await page.evaluate(alias => settingsShow(alias), alias);
    assert.equal(await page.locator('#settings-models').isVisible(), true, `${alias} opens Models`);
    assert.equal(await page.evaluate(() => location.hash), '#/settings/models');
  }
  // The settings catalogue's old link to the Models card on Computers, and the old Subscriptions address.
  for (const hash of ['#/settings/devices/settings-models', '#/settings/subscriptions']) {
    await page.evaluate(() => settingsShow('general'));
    await page.evaluate(hash => { location.hash = hash; }, hash);
    await page.waitForFunction(() => location.hash === '#/settings/models' && SETTINGS_TAB === 'models');
  }
  await page.evaluate(() => { location.hash = '#/settings/computers'; });
  await page.waitForFunction(() => location.hash === '#/settings/devices' && SETTINGS_TAB === 'devices');
  for (const alias of ['tags', 'types']) {
    await page.evaluate(alias => settingsShow(alias), alias);
    assert.equal(await page.locator('#settings-tasks').isVisible(), true, `${alias} opens Tasks`);
  }
  for (const alias of ['credentials', 'cloud']) {
    assert.equal(await page.evaluate(alias => { settingsShow(alias); return location.hash; }, alias), '#/integrations');
  }
  assert.deepEqual(errors, []);
  await page.close();
  console.log('Models tab and old links: ok');
}

async function modelsTab(browser) {
  const {page, errors, writes} = await open(browser);
  await modelsReady(page);
  const pane = page.locator('#settings-models');
  assert.deepEqual(await pane.locator(':scope > section.card > header h2').allInnerTexts(), ['Subscriptions', 'Tico Gateway', 'Defaults', 'Limits']);
  // No intro, no banner, no subtitles on the cards.
  assert.equal(await pane.locator('section.card > header .sub').count(), 0);
  // Rows: name, kind, a computer count, usage (share used, or the month's cost), Reconnect when the login stopped working.
  const rows = await pane.locator('[data-subs-row]').evaluateAll(list => list.map(r => [
    r.querySelector('.subs-name').textContent, r.querySelector('.subs-kind').textContent, r.querySelector('.subs-count .tnum').textContent,
    [...r.querySelector('.subs-use').querySelectorAll('.subs-usage-w, .tnum, .subs-usage:not(:has(.tnum)), .pill')].map(e => e.textContent).join(' ')]));
  assert.deepEqual(rows, [
    ['Claude Max (Ana)', 'Claude plan', '3', '5h 62%'],
    ['ChatGPT Pro (Ana)', 'ChatGPT plan', '1', 'week 13%'],
    ['OpenAI key (company)', 'OpenAI API key', '0', '$41.20 this month'],
    ['Claude Max (Sam)', 'Claude plan', '0', 'Reconnect']]);
  assert.equal(await pane.locator('[data-subs-row=s1] .subs-count').getAttribute('title'), 'Tico Team box, Cloud · shared, Cloud · Video Producer');
  assert.equal(await pane.locator('[data-subs-row=s1] .subs-count .nav-icon').count(), 1);
  // The Gateway is one toggle; the key, address and spend wait behind Advanced. No four-way choice, no provider boxes.
  assert.equal(await pane.locator('[data-provider-preset], [data-provider]').count(), 0);
  assert.equal(await pane.locator('[data-gateway-toggle]').isChecked(), true);
  assert.equal(await pane.locator('[data-gateway-key]').isVisible(), false);
  await pane.locator('[data-gateway-advanced] > summary').click();
  assert.equal(await pane.locator('[data-gateway-key]').isVisible(), true);
  assert.match(await pane.locator('[data-gateway-line]').innerText(), /Connected · \$188\.00 of \$1,000\.00 this month/);
  await pane.locator('[data-gateway-advanced] > summary').click();
  // Turning the Gateway off saves the subscriptions-only preset with the providers as they are.
  await pane.locator('[data-gateway-toggle]').click();
  await until(page, () => writes.some(w => w.p === '/api/v2/providers'), 'the Gateway save');
  assert.deepEqual(writes.find(w => w.p === '/api/v2/providers').body, {enabled: ['anthropic', 'openai', 'tico'], model: 'gpt-6-luna', expected_revision: 4, preset: 'subscriptions'});
  await page.waitForFunction(() => { const t = document.querySelector('[data-gateway-toggle]'); return t && !t.disabled && !t.checked; });
  // Defaults: the old controls, compact; a save leaves the preset and providers alone.
  assert.deepEqual(await pane.locator('#set-prov .models-row label').allInnerTexts(), ['Default model', 'Decisions', 'Meeting model', 'Fall back when a plan runs out', 'Check model']);
  assert.equal(await pane.locator('#set-prov-model').inputValue(), 'gpt-6-luna');
  await pane.locator('#set-prov-decide').selectOption('medium');
  const before = writes.length;
  await pane.locator('#set-prov-save').click();
  await until(page, () => writes.length > before, 'the Defaults save');
  const saved = writes.at(-1).body;
  assert.equal(saved.decide_level, 'medium');
  assert.deepEqual(saved.enabled, ['anthropic', 'openai', 'tico']);
  assert.equal('preset' in saved, false);
  // Limits: Warn at and Stop at, in runs per person.
  assert.deepEqual(await pane.locator('#turn-levels-form .models-row label').allInnerTexts(), ['Warn at', 'Stop at']);
  assert.equal(await pane.locator('#turn-levels-warn').inputValue(), '40');
  assert.deepEqual(errors, []);
  await page.close();
  console.log('Models tab cards: ok');
}

async function addSubscription(browser) {
  const {page, errors, writes} = await open(browser);
  await modelsReady(page);
  await page.locator('#subs-add').click();
  const dialog = page.locator('dialog.subs-dialog[open]');
  const kind = dialog.locator('#subs-kind');
  assert.equal(await kind.inputValue(), 'Claude plan', 'starts on Claude plan');
  assert.equal(await dialog.locator('#subs-secret').getAttribute('type'), 'password');
  assert.match(await dialog.locator('[data-subs-secret] .k label').innerText(), /Token from claude setup-token/);
  // No computers to choose here, and no Sign in without the server's sign-in route.
  assert.equal(await dialog.locator('select, input[type=checkbox]').count(), 0);
  assert.deepEqual(await dialog.locator('.tmodal-foot button').allInnerTexts(), ['Cancel', 'Save']);
  // The name is required.
  await dialog.locator('#subs-secret').fill('sk-ant-oat01-abc');
  await dialog.locator('button[type=submit]').click();
  assert.equal(await dialog.locator('[data-field-err]').innerText(), 'Enter a name');
  assert.equal(writes.length, 0);
  // The kind is a search box: typing filters, Enter picks; ChatGPT plan takes Codex's auth.json.
  await kind.click();
  assert.deepEqual(await dialog.locator('[role=option]').allInnerTexts(), ['Claude plan', 'ChatGPT plan', 'Anthropic API key', 'OpenAI API key']);
  await kind.fill('chat');
  assert.deepEqual(await dialog.locator('[role=option]:not([hidden])').allInnerTexts(), ['ChatGPT plan']);
  await kind.press('Enter');
  assert.equal(await kind.inputValue(), 'ChatGPT plan');
  assert.equal(await dialog.locator('textarea#subs-secret').count(), 1);
  // Escape with the list open closes the list, not the dialog.
  await kind.press('ArrowDown');
  await kind.press('Escape');
  assert.equal(await dialog.isVisible(), true);
  await dialog.locator('input[name=name]').fill('ChatGPT Team');
  assert.equal(await dialog.locator('[data-field-err]').count(), 0, 'typing a name clears its error');
  await dialog.locator('#subs-secret').fill('{"auth_mode": "chatgpt", "tokens": {"id_token": "x"}}');
  await shot(page, 'add-subscription-desktop-dark', dialog);
  await dialog.locator('button[type=submit]').click();
  await dialog.waitFor({state: 'detached'});
  assert.deepEqual(writes.at(-1), {p: '/api/v2/model-subscriptions', method: 'POST',
    body: {kind: 'codex_login', name: 'ChatGPT Team', secret: '{"auth_mode": "chatgpt", "tokens": {"id_token": "x"}}'}});
  await page.locator('[data-subs-row=s9]').waitFor();
  assert.deepEqual(errors, []);
  await page.close();
  console.log('add subscription: ok');
}

async function rowMenu(browser) {
  const {page, errors, writes, data} = await open(browser);
  await modelsReady(page);
  // Rename.
  await page.locator('[data-subs-more=s3]').click();
  assert.deepEqual(await page.locator('#subs-menu [data-subs-act]').allInnerTexts(), ['Rename', 'Reconnect', 'Remove']);
  await page.locator('#subs-menu [data-subs-act=rename]').click();
  const rename = page.locator('dialog.subs-dialog[open]');
  await rename.locator('input[name=name]').fill('OpenAI (ops)');
  await rename.locator('button[type=submit]').click();
  await rename.waitFor({state: 'detached'});
  assert.deepEqual(writes.at(-1), {p: '/api/v2/model-subscriptions/s3', method: 'POST', body: {name: 'OpenAI (ops)'}});
  // Reconnect from the row's pill: a new value only.
  await page.locator('[data-subs-row=s4] [data-subs-reconnect]').click();
  const again = page.locator('dialog.subs-dialog[open]');
  assert.equal(await again.locator('input[name=name]').count(), 0);
  await again.locator('#subs-secret').fill('sk-ant-oat01-new');
  await again.locator('button[type=submit]').click();
  await again.waitFor({state: 'detached'});
  assert.deepEqual(writes.at(-1), {p: '/api/v2/model-subscriptions/s4', method: 'POST', body: {secret: 'sk-ant-oat01-new'}});
  // Reconnect with a task running on a computer given it: the page asks first; Cancel keeps the dialog and sends nothing more.
  await page.locator('[data-subs-more=s2]').click();
  await page.locator('#subs-menu [data-subs-act=reconnect]').click();
  const live = page.locator('dialog.subs-dialog[open]');
  await live.locator('#subs-secret').fill('{"auth_mode": "chatgpt"}');
  await live.locator('button[type=submit]').click();
  const ask = page.locator('dialog.settings-confirm[open]');
  await ask.waitFor();
  assert.equal(await ask.locator('.tmodal-title').innerText(), 'Reconnect ChatGPT Pro (Ana)?');
  assert.equal(await ask.locator('p').innerText(), '1 running task stops and restarts, and their sessions start fresh.');
  assert.deepEqual(await ask.locator('button').allInnerTexts(), ['Cancel', 'Reconnect']);
  await ask.locator('[data-confirm-cancel]').click();
  await ask.waitFor({state: 'detached'});
  assert.equal(await live.count(), 1);
  assert.equal(writes.filter(w => w.body?.confirm).length, 0);
  // Reconnect sends confirm with a new Idempotency-Key.
  await live.locator('button[type=submit]').click();
  await page.locator('dialog.settings-confirm[open] [data-confirm-ok]').click();
  await live.waitFor({state: 'detached'});
  const s2 = writes.filter(w => w.p === '/api/v2/model-subscriptions/s2').map(w => w.body);
  assert.deepEqual(s2, [{secret: '{"auth_mode": "chatgpt"}'}, {secret: '{"auth_mode": "chatgpt"}'}, {secret: '{"auth_mode": "chatgpt"}', confirm: true}]);
  assert.notEqual(data.keys.at(-1), data.keys.at(-2));
  // Remove: asks; with runs going, the server says how many restart and the page asks once more.
  await page.locator('[data-subs-more=s1]').click();
  await page.locator('#subs-menu [data-subs-act=remove]').click();
  const confirm = page.locator('dialog.settings-confirm[open]');
  assert.equal(await confirm.locator('.tmodal-title').innerText(), 'Remove Claude Max (Ana)?');
  assert.equal(await confirm.locator('p').innerText(), '3 computers stop using it.');
  await confirm.locator('[data-confirm-ok]').click();
  await page.locator('dialog.settings-confirm[open] p', {hasText: 'restarts'}).waitFor();
  assert.equal(await confirm.locator('p').innerText(), '1 running task stops and restarts, and their sessions start fresh.');
  await confirm.locator('[data-confirm-ok]').click();
  await page.locator('[data-subs-row=s1]').waitFor({state: 'detached'});
  assert.deepEqual(writes.filter(w => w.method === 'DELETE').map(w => [w.p, w.confirm]), [['/api/v2/model-subscriptions/s1', null], ['/api/v2/model-subscriptions/s1', '1']]);
  assert.deepEqual(errors, []);
  await page.close();
  console.log('rename, reconnect, remove: ok');
}

async function computersTable(browser) {
  const {page, errors, writes} = await open(browser, {tab: 'devices'});
  await page.locator('#set-machines [data-subs-pick]').first().waitFor();
  const table = page.locator('#set-machines table.settings-machines');
  assert.deepEqual((await table.locator('thead th').allInnerTexts()).map(t => t.toLowerCase()), ['computer', 'bots', 'claude code', 'codex', 'muse code', 'now']);
  const row = id => table.locator(`[data-computer-row="${id}"]`);
  assert.equal(await row('r1').locator('.machine-bot-count').innerText(), '3');
  assert.equal(await row('r1').locator('[data-machine-now]').innerText(), '1 running · 55 queued');
  assert.equal(await row('r2').locator('[data-machine-now]').innerText(), 'idle');
  assert.equal(await row('r3').locator('[data-machine-now]').innerText(), 'asleep');
  assert.equal(await row('r4').locator('[data-machine-now]').innerText(), '3 waiting');
  assert.doesNotMatch(await table.innerText(), /0 starting/, 'the long queue line is on the computer page');
  // The picker offers the subscriptions for that tool; with none, the computer's own login, else the Gateway.
  const claude = row('r2').locator('[data-subs-pick=claude]');
  assert.deepEqual(await claude.locator('option').allInnerTexts(), ['Gateway', 'Claude Max (Ana)', 'Claude Max (Sam)']);
  assert.equal(await claude.inputValue(), 's1');
  assert.deepEqual(await row('r1').locator('[data-subs-pick=codex] option').allInnerTexts(), ['Signed in here', 'ChatGPT Pro (Ana)', 'OpenAI key (company)']);
  assert.equal(await row('r4').locator('[data-subs-needs-update]').count(), 2, 'an older computer says Needs update');
  assert.equal(await row('r1').locator('.machine-chip').count(), 1, 'Muse Code shows its login as before');
  // Under its own column a tool's chip says its state, not the tool's name again.
  assert.equal((await row('r1').locator('.machine-chip').innerText()).trim(), 'Signed in');
  // No running tasks: the switch saves at once.
  await row('r2').locator('[data-subs-pick=codex]').selectOption('s3');
  await page.waitForFunction(() => document.querySelector('[data-computer-row=r2] [data-subs-pick=codex]')?.dataset.current === 's3');
  assert.deepEqual(writes.at(-1).body, {runner_id: 'r2', runtime: 'codex', subscription: 's3', confirm: false});
  // Tico Team box is running a task: an in-page confirm with the counts; Cancel puts the picker back.
  await row('r1').locator('[data-subs-pick=claude]').selectOption('s4');
  const confirm = page.locator('dialog.settings-confirm[open]');
  assert.equal(await confirm.locator('.tmodal-title').innerText(), "Switch Tico Team box's Claude Code to Claude Max (Sam)?");
  assert.equal(await confirm.locator('p').innerText(), '2 running tasks stop and restart, and every Claude Code session on this computer starts fresh.');
  assert.deepEqual(await confirm.locator('button').allInnerTexts(), ['Cancel', 'Switch']);
  await shot(page, 'confirm-dialog-only-dark', confirm);
  await confirm.locator('[data-confirm-cancel]').click();
  await page.waitForFunction(() => document.querySelector('[data-computer-row=r1] [data-subs-pick=claude]')?.value === 's1');
  assert.equal(writes.filter(w => w.body?.confirm === true).length, 0);
  // Switch sends confirm and says what restarted.
  await row('r1').locator('[data-subs-pick=claude]').selectOption('s4');
  await page.locator('dialog.settings-confirm[open] [data-confirm-ok]').click();
  await page.waitForFunction(() => document.querySelector('[data-computer-row=r1] [data-subs-pick=claude]')?.dataset.current === 's4');
  assert.deepEqual(writes.at(-1).body, {runner_id: 'r1', runtime: 'claude', subscription: 's4', confirm: true});
  assert.match(await page.locator('#toast, .toast').last().innerText(), /Tico Team box's Claude Code: Claude Max \(Sam\) · 2 tasks restarted/);
  // A login that needs reconnecting says so beside the picker.
  assert.equal(await row('r1').locator('[data-subs-reconnect]').count(), 1);
  // A click on the row's name opens the computer's pop-up, with the queue line and one connection per AI tool.
  await row('r1').locator('.machine-name').click();
  const detail = page.locator('#set-machines dialog.computer-detail[open]');
  assert.match(await detail.locator('[data-turn-counts]').innerText(), /1 running · 0 starting · 55 queued/);
  // The release it runs, not the runner's own version constant.
  assert.match(await detail.locator('.computer-detail-facts').innerText(), /^Tico 0\.5\.19 · linux/);
  assert.deepEqual(await detail.locator('[data-connection]').evaluateAll(list => list.map(c => c.dataset.connection)), ['own:claude', 'own:codex', 'own:musecode']);
  await page.keyboard.press('Escape');
  // Add computer is a header button opening a small dialog with the enrollment fields.
  assert.equal(await page.locator('#machine-label').isVisible(), false);
  await page.locator('#machine-add-open').click();
  assert.equal(await page.locator('#machine-add-dialog #machine-label').isVisible(), true);
  await page.locator('#machine-add-dialog [data-machine-add-close]').click();
  assert.deepEqual(errors, []);
  await page.close();
  console.log('computers table and pickers: ok');
}

async function gatewayOffAndMember(browser) {
  const {page, errors} = await open(browser, {tab: 'devices', as: 'member', mutate: data => { data.providers.preset = 'subscriptions'; }});
  await page.locator('#set-machines [data-subs-pick]').first().waitFor();
  const codex = page.locator('[data-computer-row=r2] [data-subs-pick=codex]');
  assert.equal(await codex.locator('option').first().innerText(), 'None', 'Gateway off: no subscription reads None');
  assert.equal(await codex.isDisabled(), true, 'only owners and admins assign');
  await page.evaluate(() => settingsShow('models'));
  await page.locator('#set-subs [data-subs-row]').first().waitFor();
  assert.equal(await page.locator('#subs-add').isVisible(), false);
  assert.equal(await page.locator('[data-subs-more]').count(), 0);
  assert.equal(await page.locator('#settings-limits').count(), 0);
  await page.locator('[data-gateway-toggle]').waitFor();
  assert.equal(await page.locator('[data-gateway-toggle]').isDisabled(), true);
  assert.deepEqual(errors, []);
  await page.close();
  console.log('Gateway off, member view: ok');
}

async function oldServer(browser) {
  const {page, errors} = await open(browser, {tab: 'devices', old: true});
  await page.locator('#set-machines [data-computer-row=r1]').waitFor();
  // Without the routes, each AI tool shows the computer's login as before; Models says subscriptions are not kept.
  assert.equal(await page.locator('[data-subs-pick]').count(), 0);
  assert.ok(await page.locator('[data-computer-row=r1] .machine-chip').count() >= 2);
  await page.evaluate(() => settingsShow('models'));
  await page.locator('#set-subs p').waitFor();
  assert.equal(await page.locator('#subs-add').isVisible(), false);
  assert.deepEqual(errors, []);
  await page.close();
  console.log('older server: ok');
}

async function botEditor(browser) {
  const {page, errors, writes} = await open(browser, {tab: 'bots'});
  await page.evaluate(() => settingsEditBot('builder'));
  const rows = page.locator('#bot-editor [data-bot-people]');
  await rows.locator('.sb-row').first().waitFor();
  const labels = await rows.locator(':scope > .sb-row > span:first-child').allInnerTexts();
  assert.ok(labels.includes('Model') && labels.includes('Computer'), `Model and Computer are their own fields (${labels})`);
  assert.equal(await page.locator('#bot-editor [data-bot-sub-line], #bot-editor [data-bot-sub]').count(), 0);
  assert.doesNotMatch(await rows.innerText(), /Computer's login|Runs on .* ·/);
  assert.deepEqual(writes, []);
  assert.deepEqual(errors, []);
  await page.close();
  console.log('bot editor: ok');
}

async function screens(browser) {
  for (const theme of ['dark', 'light']) {
    for (const [kind, viewport] of [['desktop', {width: 1280, height: 860}], ['phone', {width: 390, height: 844}]]) {
      const {page, errors} = await open(browser, {viewport, theme});
      await modelsReady(page);
      await page.locator('#turn-levels-form').waitFor();
      const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      assert.ok(await overflow() <= 0, `${kind} Models: no sideways page scroll`);
      await shot(page, `models-${kind}-${theme}`);
      if (kind === 'desktop' && theme === 'light') await shot(page, 'limits-desktop-light', page.locator('#settings-limits'));
      await page.evaluate(() => settingsShow('devices'));
      await page.locator('#set-machines [data-subs-pick]').first().waitFor();
      assert.ok(await overflow() <= 0, `${kind} Computers: no sideways page scroll (the table scrolls in its card)`);
      await shot(page, `computers-${kind}-${theme}`);
      await page.locator('[data-computer-row=r1] [data-subs-pick=claude]').selectOption('s4');
      const confirm = page.locator('dialog.settings-confirm[open]');
      await confirm.waitFor();
      await shot(page, `confirm-${kind}-${theme}`);
      await confirm.locator('[data-confirm-cancel]').click();
      assert.deepEqual(errors, []);
      await page.close();
    }
  }
  console.log('phone fit and screenshots: ok');
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    await navigation(browser); await modelsTab(browser); await addSubscription(browser); await rowMenu(browser);
    await computersTable(browser); await gatewayOffAndMember(browser); await oldServer(browser); await botEditor(browser); await screens(browser);
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
